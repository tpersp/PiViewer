#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# ------------------------------------------------------------
# Application Version & Paths
# ------------------------------------------------------------

APP_VERSION = "1.2.1"

VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/PiViewer")
# Default IMAGE_DIR is set to a local 'uploads' folder within VIEWER_HOME if not provided by the environment.
IMAGE_DIR   = os.environ.get("IMAGE_DIR", os.path.join(VIEWER_HOME, "uploads"))

CONFIG_PATH = os.path.join(VIEWER_HOME, "viewerconfig.json")
LOG_PATH    = os.path.join(VIEWER_HOME, "viewer.log")
WEB_BG      = os.path.join(VIEWER_HOME, "web_bg.jpg")

# ------------------------------------------------------------
# Git Update Branch
# ------------------------------------------------------------
# You can change this in your environment or directly in this file.
# When the user clicks the Update button in Settings, all local 
# files will be overwritten with the updated ones.
# main = stable
# dev = experimental features
#
UPDATE_BRANCH = os.environ.get("UPDATE_BRANCH", "dev")
