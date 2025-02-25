#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from config import APP_VERSION
from utils import init_config, log_message
from routes import main_bp

def create_app():
    # Initialize Flask with a specified templates folder (default is 'templates')
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Ensure config file is created
    init_config()

    # Register our blueprint which has all routes
    app.register_blueprint(main_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    log_message(f"Starting PiViewer Flask app version {APP_VERSION}.")
    # Run on 0.0.0.0 so it is accessible on the LAN
    app.run(host="0.0.0.0", port=8080, debug=False)
