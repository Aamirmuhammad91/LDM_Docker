#!/bin/bash

# Start JupyterHub in the background
jupyterhub &

# Wait for JupyterHub to be ready
sleep 3

# Execute the required command
python /jupyterhub_api/jupyterhub_api/App/flaskr.py

# Keep the container running (optional: needed if you want to keep the container running after the command)
tail -f /dev/null