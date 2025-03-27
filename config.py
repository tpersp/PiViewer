#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# ------------------------------------------------------------
# Load environment variables from .env file in VIEWER_HOME if it exists.
# ------------------------------------------------------------
def load_env():
    # Use the default if VIEWER_HOME isn’t already set.
    default_home = "/home/pi/PiViewer"
    home = os.environ.get("VIEWER_HOME", default_home)
    env_path = os.path.join(home, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val)

load_env()

# ------------------------------------------------------------
# Application Version & Paths
# ------------------------------------------------------------

APP_VERSION = "2.4.4" #new weather icons

VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/PiViewer")
IMAGE_DIR   = os.environ.get("IMAGE_DIR", "/mnt/PiViewers")

CONFIG_PATH = os.path.join(VIEWER_HOME, "viewerconfig.json")
LOG_PATH    = os.path.join(VIEWER_HOME, "viewer.log")
WEB_BG      = os.path.join(VIEWER_HOME, "web_bg.jpg")

# ------------------------------------------------------------
# Git Update Branch
# ------------------------------------------------------------
UPDATE_BRANCH = os.environ.get("UPDATE_BRANCH", "dev")
