#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import subprocess
import requests
import random
import psutil
from datetime import datetime

from config import (
    APP_VERSION,
    VIEWER_HOME,
    IMAGE_DIR,
    CONFIG_PATH,
    LOG_PATH,
    WEB_BG
)

def init_config():
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "theme": "dark",
            "role": "main",
            "main_ip": "",
            "devices": [],
            # displays dictionary for multi-display logic
            "displays": {
                "Display0": {
                    "mode": "random_image",
                    "fallback_mode": "random_image",   # <-- New fallback mode default
                    "image_interval": 60,
                    "image_category": "",
                    "specific_image": "",
                    "shuffle_mode": False,
                    "mixed_folders": [],
                    "rotate": 0,
                    "spotify_info_position": "bottom-center"
                }
            },
            "overlay": {
                "overlay_enabled": True,       # Changed from False so overlay is always on
                "clock_enabled": False,        # Off by default
                "weather_enabled": False,      # Off by default
                "background_enabled": False,   # Off by default
                "font_color": "#FFFFFF",
                "bg_color": "#000000",
                "bg_opacity": 0.4,
                "offset_x": 20,
                "offset_y": 20,
                "overlay_width": 300,
                "overlay_height": 150,
                "clock_font_size": 26,
                "weather_font_size": 22,
                "layout_style": "stacked",
                "padding_x": 8,
                "padding_y": 6,
                "show_desc": False,            # Off by default
                "show_temp": False,            # Off by default
                "show_feels_like": False,
                "show_humidity": False,
                "monitor_selection": "All"
            },
            "gui": {
                "background_blur_radius": 20,
                "background_scale_percent": 100,
                "foreground_scale_percent": 100
            },
            "weather": {
                "api_key": "",
                "zip_code": "",
                "country_code": "",
                "lat": None,
                "lon": None
            },
            "spotify": {
                "client_id": "",
                "client_secret": "",
                "redirect_uri": "",
                "scope": "user-read-currently-playing user-read-playback-state"
            }
        }
        save_config(default_cfg)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        init_config()
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def log_message(msg):
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now()}: {msg}\n")
    print(msg)

def get_system_stats():
    cpu = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    mem_used_mb = (mem.total - mem.available) / (1024 * 1024)
    load1 = 0
    try:
        load1 = os.getloadavg()[0]
    except:
        pass
    temp = "N/A"
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"]).decode().strip()
        temp = out
    except:
        pass
    return (cpu, mem_used_mb, load1, temp)

def get_hostname():
    try:
        return subprocess.check_output(["hostname"]).decode().strip()
    except:
        return "UnknownHost"

def get_ip_address():
    try:
        out = subprocess.check_output(["hostname", "-I"]).decode().strip()
        ips = out.split()
        for ip in ips:
            if not ip.startswith("127."):
                return ip
        return "Unknown"
    except:
        return "Unknown"

def get_pi_model():
    path = "/proc/device-tree/model"
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return "Unknown Model"

def get_subfolders():
    try:
        return [d for d in os.listdir(IMAGE_DIR) if os.path.isdir(os.path.join(IMAGE_DIR, d))]
    except:
        return []

def count_files_in_folder(folder_path):
    if not os.path.isdir(folder_path):
        return 0
    cnt = 0
    valid_ext = (".png", ".jpg", ".jpeg", ".gif")
    for f in os.listdir(folder_path):
        if f.lower().endswith(valid_ext):
            cnt += 1
    return cnt

################################
# Remote device push/pull logic
################################

def get_remote_config(ip):
    url = f"http://{ip}:8080/sync_config"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote config from {ip}: {e}")
    return None

def get_remote_monitors(ip):
    url = f"http://{ip}:8080/list_monitors"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote monitors from {ip}: {e}")
    return {}

def push_displays_to_remote(ip, displays_obj):
    url = f"http://{ip}:8080/update_config"
    partial = {"displays": displays_obj}
    try:
        r = requests.post(url, json=partial, timeout=5)
        if r.status_code == 200:
            log_message(f"Pushed partial displays to {ip} successfully.")
        else:
            log_message(f"Push to {ip} failed with code {r.status_code}.")
    except Exception as e:
        log_message(f"Error pushing partial displays to {ip}: {e}")

def pull_displays_from_remote(ip):
    remote_cfg = get_remote_config(ip)
    if not remote_cfg:
        return None
    return remote_cfg.get("displays", {})