from dockerspawner import DockerSpawner
import os
import sys
from tornado import web, gen
from jupyterhub.auth import Authenticator
from urllib.parse import urlparse, parse_qs
import requests
import re
import asyncio
from traitlets import Unicode
import docker
import io
import tarfile

api_token = os.getenv('JUPYTERHUB_API_TOKEN')

def get_guest_list(n):
    """
    Generate the lis of users with the maximum number of concurrent users allowed.

    :param n: maximal amount of guest users
    :return: list of all the users
    """
    return ["guest" + str(i) for i in range(0, int(n))]


c.Authenticator.auto_login = True
c.JupyterHub.allow_named_servers = True

c.JupyterHub.bind_url = 'http://localhost:8000'
c.JupyterHub.base_url = os.getenv('CKAN_JUPYTERHUB_BASE_URL')


class DummyAuthenticator(Authenticator):
    password = Unicode(
        None,
        allow_none=True,
        config=True,
        help="""
        Set a global password for all users wanting to log in.

        This allows users with any username to log in with the same static password.
        """
    )

    @gen.coroutine
    def authenticate(self, handler, data):
        # Get the request URI
        uri = handler.request.uri

        # Parse the URI to extract the query string
        parsed_uri = urlparse(uri)
        # example parsed_uri: ParseResult(scheme='', netloc='', path='/hub/login', params='', query='next=%2Fhub%2Fuser%2Fguest1%2Fnotebooks%2F31614c67-8577-4cef-bd55-a6a18d58d02c_2022-12-27t112011140519.ipynb', fragment='')
        query_params = parse_qs(parsed_uri.query)

        # Extract the 'next' parameter from the query string
        next_param = query_params.get('next', [None])[0]

        if next_param:
            # Use regex to find '/user/<username>/' pattern
            match = re.search(r'/user/([^/]+)/', next_param)
            if match:
                username = match.group(1)
                return username
            else:
                return None
        else:
            return None


c.JupyterHub.authenticator_class = DummyAuthenticator

c.GenericOAuthenticator.enable_auth_state = True
c.Spawner.http_timeout = 300
c.JupyterHub.log_level = 'ERROR' # 'WARN'  'DEBUG'  'ERROR'
c.JupyterHub.hub_ip = '0.0.0.0'

c.DockerSpawner.network_name = os.getenv('CKAN_NETWORK')

c.DockerSpawner.remove = True
c.DockerSpawner.stop = True
# c.DockerSpawner.remove_containers = True


class GuestDockerSpawner(DockerSpawner):

    user_list = get_guest_list(os.getenv('CKAN_JUPYTERHUB_USER'))

    async def _seed_volume(self, volume_name, notebook_name):
        client = docker.from_env()
        client.images.pull("busybox:1.36")

        src_file_in_manager = os.path.join(os.getenv('CKAN_STORAGE_PATH', '/var/lib/ckan'), 'notebook', notebook_name)
        # src_file_in_manager = f"/ckan_storage/notebook/{notebook_name}"
        init = client.containers.create(
            image="busybox:1.36",
            command="sleep 120",
            mounts=[docker.types.Mount(target=self.notebook_dir, source=volume_name, type="volume")],
        )
        init.start()
        try:
            init.exec_run(f"mkdir -p {self.notebook_dir}")

            filename = os.path.basename(src_file_in_manager)

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

            init.put_archive(self.notebook_dir, buf.getvalue())
        finally:
            init.remove(force=True)

    async def start(self):
        if self.user.name in self.user_list:
            self.log.info(f"Creating docker for {self.user.name}")

            # Define user volume
            volume_name = f"jupyterhub-{self.user.name}"
            # self.volume_name = volume_name

            # Set up the volume
            self.volumes[volume_name] = {
                'bind': self.notebook_dir,
                'mode': 'rw',
            }

            # Set resource limits
            self.extra_host_config = {
                "mem_limit": os.getenv('CKAN_JUPYTERHUB_MEMORY_LIMIT'),
                "cpu_period": 100000,
                "cpu_quota": int(os.getenv('CKAN_JUPYTERHUB_PERCENTAGE_CPU')) * 1000
            }

            # Extract notebook name from URL
            notebook_name = None
            if hasattr(self, 'handler') and hasattr(self.handler, 'request'):
                next_param = self.handler.request.query_arguments.get('next', [None])[0]
                if next_param:
                    next_path = next_param.decode('utf-8')
                    match = re.search(r'notebooks/([^/]+\.ipynb)', next_path)
                    if match:
                        notebook_name = match.group(1)
                        self.log.info(f"Found notebook name: {notebook_name}")

            # Copy notebooks from source volume
            if notebook_name is not None:
                await self._seed_volume(volume_name, notebook_name)
                self.log.error(f'I copied the notebook for {self.user.name}')

            # Start the user container
            container = await super().start()
            return container
        else:
            # Default behavior for non-guest users
            self.volumes['jupyterhub-user-{username}'] = {
                'bind': self.notebook_dir,
                'mode': 'ro',
            }
            return await super().start()


c.JupyterHub.spawner_class = GuestDockerSpawner
# c.NativeAuthenticator.create_system_users = True

# Make sure DockerSpawner logs are visible
c.DockerSpawner.debug = False


c.Spawner.args = ['--NotebookApp.tornado_settings={"headers":{"Content-Security-Policy": "frame-ancestors *;"}}']
c.JupyterHub.tornado_settings = {'headers': {'Content-Security-Policy': "frame-ancestors *;"}}


notebook_dir = '/home/jovyan/work'
c.DockerSpawner.notebook_dir = notebook_dir  # 'LDM_examples_files/RESOURCES/jupyternotebooks/notebook'


c.DockerSpawner.image = "jupyter/datascience-notebook:latest"
c.Spawner.mem_limit = os.getenv('CKAN_JUPYTERHUB_MEMORY_LIMIT')
# Persistence
c.JupyterHub.db_url = "sqlite:///data/jupyterhub.sqlite"


# c.Authenticator.admin_users = {'myadmin'}
# c.NativeAuthenticator.open_signup = True

# Enable user registration
#c.Authenticator.allowed_users = set(get_guest_list() + ['myadmin'])
c.Authenticator.allowed_users = set(get_guest_list(os.getenv('CKAN_JUPYTERHUB_USER')))


c.JupyterHub.services = [
    {
        'name': 'idle-culler',
        'api_token': api_token,
        'admin': True,
        'oauth_no_confirm': True,
        'command': [
            sys.executable,
            '-m', 'jupyterhub_idle_culler',
            '--timeout=' + os.getenv('CKAN_JUPYTERHUB_TIMEOUT'),
            '--cull-users', # Cull users
#           '--remove-users' # Remove users
        ],
    },
]

c.JupyterHub.load_roles = [
    {
        "name": "list-and-cull", # name the role
        "services": [
            "idle-culler", # assign the service to this role
        ],
        "scopes": [
            # declare what permissions the service should have
            "list:users", # list users
        ],
    }
]

# Add a volume cleanup service
c.JupyterHub.services.append({
    'name': 'volume-cleaner',
    'command': [
        'sh', '-c',
        'while true; do sleep 60; curl -s ' +os.getenv('CKAN_API_JUPYTERHUB')+'/cleanup_volumes > /dev/null; done'
    ],
})

# Shutdown user servers on logout
c.JupyterHub.shutdown_on_logout = True


# http://localhost:8000/hub/authorize
# http://localhost:8000/user/myadmin/lab
# http://194.95.158.86:8000/hub/login
