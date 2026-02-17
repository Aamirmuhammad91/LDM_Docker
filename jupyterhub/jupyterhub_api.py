import requests
import json
import os
import logging
import subprocess
import sys

logging.basicConfig(level=logging.INFO)

log = logging.getLogger(__name__)

# Set the URL of your JupyterHub instance
url_nb = os.getenv('CKAN_JUPYTERNOTEBOOK_URL')
hub_url = url_nb + 'hub/api/users'
# Set the API token for authentication
api_token = os.getenv('JUPYTERHUB_API_TOKEN')

def get_guest_list(n):
    """
    Generate the lis of users with the maximum number of concurrent users allowed.

    :param n: maximal amount of guest users
    :return: list of all the users
    """
    return ["guest" + str(i) for i in range(0, int(n))]

def get_running_users():
    # Construct the request headers with the API token
    headers = {
        'Authorization': 'token ' + api_token # f'Bearer {api_token}'
    }
    # Make a GET request to the JupyterHub API endpoint with the authentication token
    response = requests.get(hub_url, headers=headers, verify=False)
    # log.info(f"requests in get_running_users: {requests}")
    # log.info(response.text)
    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        try:
            # Parse the JSON response
            users_info = json.loads(response.text)
            # Extract the list of usernames whose servers are running
            running_users = [user['name'] for user in users_info if user.get('server', {})]
            log.info(running_users)
        except json.JSONDecodeError as e:
            log.info("Failed to parse JSON response:", e)
    else:
        log.info("Failed to retrieve user information. Status code: %s", response.status_code)
        return []
    return running_users


def get_free_user():
    guest_list = get_guest_list(os.getenv('CKAN_JUPYTERHUB_USER'))
    running_list = get_running_users()
    set_a = set(guest_list)
    set_b = set(running_list)
    # Retrieve elements from set A that are not in set B
    result = set_a - set_b
    log.info(f"get_free_user {result}")
    if len(result) > 0:
        return result.pop()
    return None


def restart_jupyterhub():
    try:
        # Kill the JupyterHub process
        result = os.system('pkill -f "/usr/local/bin/jupyterhub"')
        if result == 15:
            log.info("JupyterHub successfully killed")

            # Clean up proxy PID file if it exists
            pid_file = "/srv/jupyterhub/jupyterhub-proxy.pid"
            if os.path.exists(pid_file):
                log.info(f"Removing stale proxy PID file: {pid_file}")
                os.remove(pid_file)

            # Start JupyterHub
            result = os.system('jupyterhub &')
            log.info(result)
            if result == 0:
                log.info("JupyterHub successfully restarted")
                return True
            log.error("Error restarting JupyterHub")
            return False
        else:
            log.error("Error killing JupyterHub")
            return False
    except Exception as e:
        log.error(f"Error restarting JupyterHub: {str(e)}")
        return False

def update_env_variable(updates):
    """Update environment variable"""
    for key, value in updates.items():
        try:
            os.environ[key] = str(value)
        except Exception as e:
            log.error(f"Error updating environment variable {key}: {str(e)}")
            return False
    return True


def copy_notebook_to_container(username, notebook_name):
    """Copy a specific notebook to an existing user's volume using tar archive"""
    try:
        import docker
        import io
        import tarfile
        
        client = docker.from_env()
        client.images.pull("busybox:1.36")

        # Define volume and paths
        volume_name = f"jupyterhub-{username}"
        notebook_dir = '/home/jovyan/work'
        src_file_in_manager = os.path.join(os.getenv('CKAN_STORAGE_PATH', '/var/lib/ckan'), 'notebook', notebook_name)

        # Create temporary container with the volume mounted
        init = client.containers.create(
            image="busybox:1.36",
            command="sleep 120",
            mounts=[docker.types.Mount(target=notebook_dir, source=volume_name, type="volume")],
        )
        init.start()
        
        try:
            # Create directory structure
            init.exec_run(f"mkdir -p {notebook_dir}")

            # Get filename
            filename = os.path.basename(src_file_in_manager)

            # Create tar archive in memory
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                with open(src_file_in_manager, "rb") as f:
                    data = f.read()
                info = tarfile.TarInfo(name=filename)
                info.size = len(data)
                info.mode = 0o644
                info.mtime = os.path.getmtime(src_file_in_manager)
                tar.addfile(info, io.BytesIO(data))
            buf.seek(0)

            # Copy file to container
            init.put_archive(notebook_dir, buf.getvalue())
            
            log.info(f"Notebook {notebook_name} copied successfully to {username}'s container")
            return True
            
        finally:
            init.remove(force=True)

    except Exception as e:
        log.error(f"Error during notebook copy: {str(e)}")
        return False


def cleanup_unused_volumes():
    """Clean up unused jupyterhub guest volumes"""
    try:
        import docker
        client = docker.from_env()

        # Get list of all volumes
        volumes = client.volumes.list()

        # Get list of guest users
        guest_list = get_guest_list(os.getenv('CKAN_JUPYTERHUB_USER'))

        # Get list of running containers
        running_containers = client.containers.list()

        # Extract volume names that are currently in use
        used_volumes = set()
        for container in running_containers:
            for mount in container.attrs['Mounts']:
                if mount['Type'] == 'volume':
                    used_volumes.add(mount['Name'])

        # Count of removed volumes
        removed_count = 0

        # Check each volume
        for volume in volumes:
            volume_name = volume.name
            # Only process jupyterhub guest volumes
            if volume_name.startswith('jupyterhub-guest'):
                # Extract the username from the volume name
                username = volume_name.replace('jupyterhub-', '')

                # If the volume is not in use and belongs to a guest user, remove it
                if volume_name not in used_volumes and username in guest_list:
                    log.info(f"Removing unused volume: {volume_name}")
                    volume.remove(force=True)
                    removed_count += 1

        log.info(f"Cleanup complete. Removed {removed_count} unused volumes.")
        return removed_count

    except Exception as e:
        log.error(f"Error cleaning up volumes: {str(e)}")
        return -1
