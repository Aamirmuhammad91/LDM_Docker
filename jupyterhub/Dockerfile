FROM jupyterhub/jupyterhub:4.1.6

RUN pip install --no-cache \
    oauthenticator==17.3.0 \
    dockerspawner==14.0.0 \
    jupyterhub-nativeauthenticator==1.3.0 \
    jupyterhub-idle-culler==1.4.0 \
    flask==3.1.2 \
    docker==7.1.0

COPY jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
COPY ./api /jupyterhub_api

# Make the startup script executable
RUN chmod +x /jupyterhub_api/Scripts/start_api.sh && \
    pip install -e /jupyterhub_api

# Expose the necessary ports (informational)
EXPOSE 8000 6000

# Set the startup script as the entrypoint
ENTRYPOINT ["/jupyterhub_api/Scripts/start_api.sh"]
