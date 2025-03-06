#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

# ------------------------------------------------------------
# Application Version & Paths
# ------------------------------------------------------------
# Bumped from 1.1.1 to 1.1.2
APP_VERSION = "1.1.2"

VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/PiViewer")
IMAGE_DIR   = os.environ.get("IMAGE_DIR", "/mnt/PiViewers")

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
UPDATE_BRANCH = os.environ.get("UPDATE_BRANCH", "main")
