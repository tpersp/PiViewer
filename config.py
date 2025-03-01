#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# ------------------------------------------------------------
# Application Version & Paths
# ------------------------------------------------------------
# Bumped from 1.0.8 to 1.0.9
APP_VERSION = "1.0.9"

VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/PiViewer")
IMAGE_DIR   = os.environ.get("IMAGE_DIR", "/mnt/PiViewers")

CONFIG_PATH = os.path.join(VIEWER_HOME, "viewerconfig.json")
LOG_PATH    = os.path.join(VIEWER_HOME, "viewer.log")
WEB_BG      = os.path.join(VIEWER_HOME, "web_bg.jpg")
