"""
Shared utilities for JupyterHub operations
"""
import io
import logging
import os
import tarfile

import docker

log = logging.getLogger(__name__)

# Configuration constants
BUSYBOX_IMAGE = "busybox:1.36"
NOTEBOOK_DIR = '/home/jovyan/work'
CONTAINER_SLEEP_TIME = 120


def get_guest_list(n):
    """
    Generate the list of users with the maximum number of concurrent users allowed.

    :param n: maximal amount of guest users (string or int)
    :return: list of all guest usernames
    """
    return [f"guest{i}" for i in range(0, int(n))]


def get_storage_path(notebook_name):
    """
    Get the full path to a notebook in storage.
    
    :param notebook_name: Name of the notebook file
    :return: Full path to the notebook
    """
    storage_path = os.getenv('CKAN_STORAGE_PATH', '/var/lib/ckan')
    return os.path.join(storage_path, 'notebook', notebook_name)


def create_tar_archive(src_file_path):
    """
    Create an in-memory tar archive of a file with preserved metadata.
    
    :param src_file_path: Path to the source file
    :return: BytesIO buffer containing the tar archive
    :raises FileNotFoundError: If source file doesn't exist
    """
    if not os.path.exists(src_file_path):
        raise FileNotFoundError(f"Source file not found: {src_file_path}")
    
    filename = os.path.basename(src_file_path)
    
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        with open(src_file_path, "rb") as f:
            data = f.read()
        
        info = tarfile.TarInfo(name=filename)
        info.size = len(data)
        info.mode = 0o644
        info.mtime = os.path.getmtime(src_file_path)
        tar.addfile(info, io.BytesIO(data))
    
    buf.seek(0)
    return buf


def copy_file_to_volume(client, volume_name, src_file_path, target_dir=NOTEBOOK_DIR):
    """
    Copy a file to a Docker volume using a temporary container.
    
    :param client: Docker client instance
    :param volume_name: Name of the target volume
    :param src_file_path: Path to the source file on host
    :param target_dir: Target directory in the volume
    :return: True if successful
    :raises Exception: On any error during copy
    """
    # Ensure busybox image is available
    try:
        client.images.get(BUSYBOX_IMAGE)
    except docker.errors.ImageNotFound:
        log.info(f"Pulling {BUSYBOX_IMAGE} image")
        client.images.pull(BUSYBOX_IMAGE)
    
    # Create temporary container
    init = client.containers.create(
        image=BUSYBOX_IMAGE,
        command=f"sleep {CONTAINER_SLEEP_TIME}",
        mounts=[docker.types.Mount(target=target_dir, source=volume_name, type="volume")],
    )
    
    init.start()
    
    try:
        # Create directory structure
        init.exec_run(f"mkdir -p {target_dir}")
        
        # Create and copy tar archive
        tar_buffer = create_tar_archive(src_file_path)
        init.put_archive(target_dir, tar_buffer.getvalue())
        
        filename = os.path.basename(src_file_path)
        log.info(f"Successfully copied {filename} to volume {volume_name}")
        return True
        
    finally:
        init.remove(force=True)


def get_volume_name(username):
    """
    Get the standard volume name for a user.
    
    :param username: Username
    :return: Volume name in format jupyterhub-{username}
    """
    return f"jupyterhub-{username}"
