#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from config import APP_VERSION
from utils import init_config, log_message
from routes import main_bp

def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    init_config()
    app.register_blueprint(main_bp)
    return app

if __name__=="__main__":
    app = create_app()
    log_message(f"Starting PiViewer Flask app version {APP_VERSION}.")
    app.run(host="0.0.0.0", port=8080, debug=False)
