#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# ------------------------------------------------------------
# Application Version & Paths
# ------------------------------------------------------------
APP_VERSION = "2.0.0"

VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/PiViewer")
IMAGE_DIR   = os.environ.get("IMAGE_DIR", "/mnt/PiViewers")

CONFIG_PATH = os.path.join(VIEWER_HOME, "viewerconfig.json")
LOG_PATH    = os.path.join(VIEWER_HOME, "viewer.log")
WEB_BG      = os.path.join(VIEWER_HOME, "web_bg.jpg")

# ------------------------------------------------------------
# Git Update Branch
# ------------------------------------------------------------
UPDATE_BRANCH = os.environ.get("UPDATE_BRANCH", "qtdev")
