import json
import logging
import os

import docker
import requests

from .utils import (
    get_guest_list,
    get_storage_path,
    copy_file_to_volume,
    get_volume_name,
    NOTEBOOK_DIR
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Configuration
url_nb = os.getenv('CKAN_JUPYTERNOTEBOOK_URL')
hub_url = url_nb + 'hub/api/users'
api_token = os.getenv('JUPYTERHUB_API_TOKEN')


def get_running_users():
    """
    Get list of users with currently running servers.

    :return: List of usernames with active servers
    """
    headers = {'Authorization': f'token {api_token}'}

    try:
        response = requests.get(hub_url, headers=headers, verify=False, timeout=10)
        response.raise_for_status()

        users_info = response.json()
        running_users = [user['name'] for user in users_info if user.get('server')]

        log.info(f"Running users: {running_users}")
        return running_users

    except requests.exceptions.RequestException as e:
        log.error(f"Failed to retrieve user information: {e}")
        return []
    except (json.JSONDecodeError, KeyError) as e:
        log.error(f"Failed to parse API response: {e}")
        return []


def get_free_user():
    """
    Find an available guest user (one without a running server).

    :return: Username of free guest user, or None if all are busy
    """
    max_users = os.getenv('CKAN_JUPYTERHUB_USER')
    if not max_users:
        log.error("CKAN_JUPYTERHUB_USER environment variable not set")
        return None

    guest_list = get_guest_list(max_users)
    running_list = get_running_users()

    free_users = set(guest_list) - set(running_list)

    log.info(f"Free users: {free_users}")

    return free_users.pop() if free_users else None


def restart_jupyterhub():
    """
    Restart the JupyterHub service.

    :return: True if successful, False otherwise
    """
    try:
        # Kill JupyterHub process
        log.info("Attempting to kill JupyterHub process")
        kill_result = os.system('pkill -f "/usr/local/bin/jupyterhub"')

        # pkill returns 0 if at least one process was killed
        if kill_result != 0:
            log.warning(f"pkill returned {kill_result} - process may not have been running")
        else:
            log.info("JupyterHub process killed successfully")

        # Clean up stale proxy PID file
        pid_file = "/srv/jupyterhub/jupyterhub-proxy.pid"
        if os.path.exists(pid_file):
            log.info(f"Removing stale proxy PID file: {pid_file}")
            os.remove(pid_file)

        # Start JupyterHub
        log.info("Starting JupyterHub")
        start_result = os.system('jupyterhub &')

        if start_result == 0:
            log.info("JupyterHub started successfully")
            return True
        else:
            log.error(f"Failed to start JupyterHub, exit code: {start_result}")
            return False

    except Exception as e:
        log.error(f"Error restarting JupyterHub: {e}")
        return False


def update_env_variable(updates):
    """
    Update environment variables.

    :param updates: Dictionary of key-value pairs to update
    :return: True if all updates successful, False otherwise
    """
    if not isinstance(updates, dict):
        log.error("Updates must be a dictionary")
        return False

    success = True
    for key, value in updates.items():
        try:
            os.environ[key] = str(value)
            log.info(f"Updated environment variable: {key}")
        except Exception as e:
            log.error(f"Error updating environment variable {key}: {e}")
            success = False

    return success


def copy_notebook_to_container(username, notebook_name):
    """
    Copy a specific notebook to a user's volume.

    :param username: Target username
    :param notebook_name: Name of the notebook file
    :return: True if successful, False otherwise
    """
    # Input validation
    if not username or not notebook_name:
        log.error("Username and notebook_name are required")
        return False

    # Security: prevent path traversal
    if '..' in notebook_name or '/' in notebook_name or '\\' in notebook_name:
        log.error(f"Invalid notebook name: {notebook_name}")
        return False

    try:
        client = docker.from_env()

        volume_name = get_volume_name(username)
        src_file_path = get_storage_path(notebook_name)

        # Check if source file exists
        if not os.path.exists(src_file_path):
            log.error(f"Source file not found: {src_file_path}")
            return False

        # Perform the copy
        copy_file_to_volume(client, volume_name, src_file_path, NOTEBOOK_DIR)
        log.info(f"Notebook {notebook_name} copied to {username}'s container")
        return True

    except docker.errors.NotFound:
        log.error(f"Volume {volume_name} not found")
        return False
    except Exception as e:
        log.error(f"Error copying notebook: {e}")
        return False


def cleanup_unused_volumes():
    """
    Clean up Docker volumes for guest users that don't have running containers.

    :return: Number of volumes removed, or -1 on error
    """
    max_users = os.getenv('CKAN_JUPYTERHUB_USER')
    if not max_users:
        log.error("CKAN_JUPYTERHUB_USER environment variable not set")
        return -1

    try:
        client = docker.from_env()

        # Get all volumes
        volumes = client.volumes.list()

        # Get guest list
        guest_list = get_guest_list(max_users)

        # Get currently used volumes
        running_containers = client.containers.list()
        used_volumes = set()

        for container in running_containers:
            for mount in container.attrs.get('Mounts', []):
                if mount.get('Type') == 'volume':
                    used_volumes.add(mount.get('Name'))

        # Clean up unused volumes
        removed_count = 0

        for volume in volumes:
            volume_name = volume.name

            # Only process jupyterhub guest volumes
            if not volume_name.startswith('jupyterhub-guest'):
                continue

            # Extract username
            username = volume_name.replace('jupyterhub-', '')

            # Remove if unused and belongs to a guest user
            if volume_name not in used_volumes and username in guest_list:
                try:
                    log.info(f"Removing unused volume: {volume_name}")
                    volume.remove(force=True)
                    removed_count += 1
                except Exception as e:
                    log.error(f"Failed to remove volume {volume_name}: {e}")

        log.info(f"Cleanup complete. Removed {removed_count} unused volumes.")
        return removed_count

    except Exception as e:
        log.error(f"Error during volume cleanup: {e}")
        return -1
