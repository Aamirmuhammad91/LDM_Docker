#!/usr/bin/env python3
"""
JupyterHub Management API

Provides REST endpoints for managing JupyterHub guest users and operations.
"""


import logging

from flask import Flask, jsonify, request

import jupyterhub_api.api as hub_api

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

app = Flask(__name__)


def create_response(data=None, error=None, status_code=200):
    """
    Create a standardized JSON response.

    :param data: Response data
    :param error: Error message if applicable
    :param status_code: HTTP status code
    :return: Flask response object
    """
    response = {}

    if error:
        response['success'] = False
        response['error'] = error
    else:
        response['success'] = True
        if data is not None:
            response['data'] = data

    return jsonify(response), status_code


@app.route('/get_user', methods=['GET'])
def get_user():
    """
    Get an available guest user.

    Returns:
        200: Available username
        503: No users available
        500: Server error
    """
    try:
        username = hub_api.get_free_user()

        if username:
            log.info(f"Assigned free user: {username}")
            return create_response({'username': username})
        else:
            log.warning("No free users available")
            return create_response(
                error="No available guest users",
                status_code=503
            )
    except Exception as e:
        log.error(f"Error in get_user: {e}")
        return create_response(error=str(e), status_code=500)


@app.route('/running_user', methods=['GET'])
def running_users():
    """
    Get list of users with running servers.

    Returns:
        200: List of running usernames
    """
    try:
        users = hub_api.get_running_users()
        return create_response({'users': users, 'count': len(users)})
    except Exception as e:
        log.error(f"Error in running_users: {e}")
        return create_response(error=str(e), status_code=500)


@app.route('/restart_jupyterhub', methods=['GET'])
def restart_jupyterhub():
    """
    Restart the JupyterHub service.

    Returns:
        200: Restart successful
        500: Restart failed
    """
    try:
        result = hub_api.restart_jupyterhub()

        if result:
            log.info("JupyterHub restart successful")
            return create_response({'restarted': True})
        else:
            log.error("JupyterHub restart failed")
            return create_response(
                error="Failed to restart JupyterHub",
                status_code=500
            )
    except Exception as e:
        log.error(f"Error in restart_jupyterhub: {e}")


@app.route('/update_env', methods=['POST'])
def update_env():
    """
    Update environment variables.

    Expects JSON body with key-value pairs.

    Returns:
        200: Update successful
        400: Invalid input
        500: Update failed
    """
    try:
        if not request.is_json:
            return create_response(
                error="Content-Type must be application/json",
                status_code=400
            )

        updates = request.get_json()

        if not isinstance(updates, dict):
            return create_response(
                error="Request body must be a JSON object",
                status_code=400
            )

        result = hub_api.update_env_variable(updates)

        if result:
            log.info(f"Environment variables updated: {list(updates.keys())}")
            return create_response({'updated': list(updates.keys())})
        else:
            return create_response(
                error="Failed to update environment variables",
                status_code=500
            )
    except Exception as e:
        log.error(f"Error in update_env: {e}")
        return create_response(error=str(e), status_code=500)


@app.route('/copy_notebook', methods=['POST'])
def copy_notebook():
    """
    Copy a notebook to a user's container.

    Query parameters or JSON body:
        - username: Target username
        - notebook_name: Name of the notebook file

    Returns:
        200: Copy successful
        400: Invalid input
        404: Notebook not found
        500: Copy failed
    """
    try:
        # Accept both query params and JSON body
        if request.is_json:
            data = request.get_json()
            username = data.get('username')
            notebook_name = data.get('notebook_name')
        else:
            username = request.args.get('username')
            notebook_name = request.args.get('notebook_name')

        # Validate input
        if not username or not notebook_name:
            return create_response(
                error="Both 'username' and 'notebook_name' are required",
                status_code=400
            )

        # Security validation
        if '..' in notebook_name or '/' in notebook_name or '\\' in notebook_name:
            return create_response(
                error="Invalid notebook name",
                status_code=400
            )

        result = hub_api.copy_notebook_to_container(username, notebook_name)

        if result:
            log.info(f"Notebook {notebook_name} copied to {username}")
            return create_response({
                'username': username,
                'notebook': notebook_name,
                'copied': True
            })
        else:
            return create_response(
                error="Failed to copy notebook",
                status_code=500
            )

    except Exception as e:
        log.error(f"Error in copy_notebook: {e}")
        return create_response(error=str(e), status_code=500)


@app.route('/cleanup_volumes', methods=['POST'])
def cleanup_volumes():
    """
    Clean up unused Docker volumes for guest users.

    Returns:
        200: Cleanup successful with count
        500: Cleanup failed
    """
    try:
        removed_count = hub_api.cleanup_unused_volumes()

        if removed_count >= 0:
            log.info(f"Cleanup completed: {removed_count} volumes removed")
            return create_response({'removed_count': removed_count})
        else:
            return create_response(
                error="Volume cleanup failed",
                status_code=500
            )
    except Exception as e:
        log.error(f"Error in cleanup_volumes: {e}")
        return create_response(error=str(e), status_code=500)


@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return create_response(error="Endpoint not found", status_code=404)


@app.errorhandler(401)
def unauthorized(error):
    """Handle 401 errors."""
    return create_response(error=error.description or "Unauthorized", status_code=401)


@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    log.error(f"Internal server error: {error}")
    return create_response(error="Internal server error", status_code=500)


if __name__ == '__main__':
    app.run(host='jupyterhub', port=6000)
