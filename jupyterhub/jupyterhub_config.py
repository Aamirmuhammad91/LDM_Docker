import os
import re
import sys
from urllib.parse import urlparse, parse_qs

import docker
from dockerspawner import DockerSpawner
from jupyterhub.auth import Authenticator
from tornado import gen
from traitlets import Unicode

from jupyterhub_api.utils import (
    get_guest_list,
    get_storage_path,
    copy_file_to_volume,
    get_volume_name,
    NOTEBOOK_DIR
)

# Configuration
api_token = os.getenv('JUPYTERHUB_API_TOKEN')

# Basic JupyterHub settings
c.Authenticator.auto_login = True
c.JupyterHub.allow_named_servers = True
c.JupyterHub.bind_url = 'http://localhost:8000'
c.JupyterHub.base_url = os.getenv('CKAN_JUPYTERHUB_BASE_URL', '/')
c.JupyterHub.log_level = os.getenv('JUPYTERHUB_LOG_LEVEL', 'ERROR')
c.JupyterHub.hub_ip = '0.0.0.0'

# Database
c.JupyterHub.db_url = os.getenv('JUPYTERHUB_DB_URL', 'sqlite:///data/jupyterhub.sqlite')

# Docker settings
c.DockerSpawner.network_name = os.getenv('CKAN_NETWORK')
c.DockerSpawner.remove = True
c.DockerSpawner.stop = True
c.DockerSpawner.debug = False
c.DockerSpawner.image = os.getenv('JUPYTERHUB_DOCKER_IMAGE', 'jupyter/datascience-notebook:latest')
c.DockerSpawner.notebook_dir = NOTEBOOK_DIR

# Resource limits
c.Spawner.mem_limit = os.getenv('CKAN_JUPYTERHUB_MEMORY_LIMIT')
c.Spawner.http_timeout = 300

# Security headers for iframe embedding
c.Spawner.args = [
    '--NotebookApp.tornado_settings={"headers":{"Content-Security-Policy": "frame-ancestors *;"}}'
]
c.JupyterHub.tornado_settings = {
    'headers': {'Content-Security-Policy': "frame-ancestors *;"}
}

# Shutdown on logout
c.JupyterHub.shutdown_on_logout = True


class DummyAuthenticator(Authenticator):
    """
    Custom authenticator that extracts username from URL parameters.

    This allows automatic login by passing the username in the 'next' parameter.
    """

    password = Unicode(
        None,
        allow_none=True,
        config=True,
        help="Set a global password for all users (not used in this implementation)"
    )

    @gen.coroutine
    def authenticate(self, handler, data):
        """
        Extract username from the URL's 'next' parameter.

        Expected format: /hub/login?next=/hub/user/<username>/...
        """
        uri = handler.request.uri
        parsed_uri = urlparse(uri)
        query_params = parse_qs(parsed_uri.query)

        next_param = query_params.get('next', [None])[0]

        if not next_param:
            self.log.warning("No 'next' parameter found in URL")
            return None

        # Extract username from path
        match = re.search(r'/user/([^/]+)/', next_param)
        if match:
            username = match.group(1)
            self.log.info(f"Authenticated user: {username}")
            return username
        else:
            self.log.warning(f"Could not extract username from: {next_param}")
            return None


class GuestDockerSpawner(DockerSpawner):
    """
    Custom Docker spawner for guest users with volume management and resource limits.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        max_users = os.getenv('CKAN_JUPYTERHUB_USER')
        self.user_list = get_guest_list(max_users) if max_users else []

    async def _seed_volume(self, volume_name, notebook_name):
        """
        Copy a notebook file to a user's volume.

        :param volume_name: Name of the Docker volume
        :param notebook_name: Name of the notebook file to copy
        """
        try:
            client = docker.from_env()
            src_file_path = get_storage_path(notebook_name)

            # Use shared utility function
            copy_file_to_volume(client, volume_name, src_file_path, self.notebook_dir)
            self.log.info(f"Seeded volume {volume_name} with {notebook_name}")

        except Exception as e:
            self.log.error(f"Failed to seed volume: {e}")
            raise

    async def start(self):
        """
        Start the user's Jupyter server container.

        For guest users: creates volume, applies resource limits, and copies notebook if specified.
        For other users: uses default read-only volume.
        """
        if self.user.name in self.user_list:
            self.log.info(f"Starting container for guest user: {self.user.name}")

            # Setup volume
            volume_name = get_volume_name(self.user.name)
            self.volumes[volume_name] = {
                'bind': self.notebook_dir,
                'mode': 'rw',
            }

            # Apply resource limits
            memory_limit = os.getenv('CKAN_JUPYTERHUB_MEMORY_LIMIT')
            cpu_percentage = os.getenv('CKAN_JUPYTERHUB_PERCENTAGE_CPU', '100')

            self.extra_host_config = {
                "mem_limit": memory_limit,
                "cpu_period": 100000,
                "cpu_quota": int(cpu_percentage) * 1000
            }

            # Extract notebook name from request URL if present
            notebook_name = None
            if hasattr(self, 'handler') and hasattr(self.handler, 'request'):
                next_param = self.handler.request.query_arguments.get('next', [None])[0]
                if next_param:
                    next_path = next_param.decode('utf-8')
                    match = re.search(r'notebooks/([^/]+\.ipynb)', next_path)
                    if match:
                        notebook_name = match.group(1)
                        self.log.info(f"Detected notebook request: {notebook_name}")

            # Copy notebook if specified
            if notebook_name:
                try:
                    await self._seed_volume(volume_name, notebook_name)
                    self.log.info(f"Successfully copied notebook for {self.user.name}")
                except Exception as e:
                    self.log.error(f"Failed to copy notebook: {e}")
                    # Continue anyway - volume exists even if copy failed

            # Start container
            container = await super().start()
            return container

        else:
            # Non-guest user with read-only default volume
            self.log.info(f"Starting container for non-guest user: {self.user.name}")
            self.volumes[f'jupyterhub-user-{self.user.name}'] = {
                'bind': self.notebook_dir,
                'mode': 'ro',
            }
            return await super().start()


# Configure authenticator and spawner
c.JupyterHub.authenticator_class = DummyAuthenticator
c.JupyterHub.spawner_class = GuestDockerSpawner

# Allowed users
max_users = os.getenv('CKAN_JUPYTERHUB_USER')
if max_users:
    c.Authenticator.allowed_users = set(get_guest_list(max_users))
else:
    import logging
    logging.warning("CKAN_JUPYTERHUB_USER not set - no guest users allowed")
    c.Authenticator.allowed_users = set()

# Configure services
c.JupyterHub.services = [
    {
        'name': 'idle-culler',
        'api_token': api_token,
        'admin': True,
        'oauth_no_confirm': True,
        'command': [
            sys.executable,
            '-m', 'jupyterhub_idle_culler',
            '--timeout=' + os.getenv('CKAN_JUPYTERHUB_TIMEOUT', '3600'),
            '--cull-users',
        ],
    },
]

# Configure roles
c.JupyterHub.load_roles = [
    {
        "name": "list-and-cull",
        "services": ["idle-culler"],
        "scopes": ["list:users"],
    }
]

# Add volume cleanup service if API URL is configured
api_url = os.getenv('CKAN_API_JUPYTERHUB')
if api_url:
    c.JupyterHub.services.append({
        'name': 'volume-cleaner',
        'command': [
            'sh', '-c',
            f'while true; do sleep 60; curl -X POST -s {api_url}/cleanup_volumes > /dev/null; done'
        ],
    })
else:
    import logging
    logging.warning("CKAN_API_JUPYTERHUB not set - volume cleanup service disabled")

# http://localhost:8000/hub/authorize
# http://localhost:8000/user/myadmin/lab
# http://194.95.158.86:8000/hub/login
