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
    """Initialize config file if missing, including default displays & multi-device placeholders, plus weather/overlay defaults."""
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "theme": "dark",
            "displays": {},   # local device's displays
            "role": "main",   # 'main' or 'sub'
            "main_ip": "",
            "devices": [],
            # Default overlay config
            "overlay": {
                "overlay_enabled": False,
                "clock_enabled": True,
                "weather_enabled": False,
                "background_enabled": True,
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
                "show_desc": True,
                "show_temp": True,
                "show_feels_like": False,
                "show_humidity": False,
                "monitor_selection": "All",
            },
            # Default weather config
            "weather": {
                "api_key": "",
                "zip_code": "",
                "country_code": "",
                "lat": None,
                "lon": None
            }
        }

        # Auto-create local displays from actual monitors
        monitors = detect_monitors()
        for m in monitors:
            default_cfg["displays"][m] = {
                "mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": [],
                "rotate": 0
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

def detect_monitors():
    """
    Use xrandr --listmonitors to detect connected monitors.
    Returns a dict like:
      {
        "HDMI-1": {
          "resolution": "1920x1080",
          "name": "HDMI-1",
          "offset_x": 0,
          "offset_y": 0
        },
        ...
      }
    """
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        if len(lines) <= 1:
            if os.path.exists("/dev/fb1"):
                return {
                    "FB1": {
                        "resolution": "480x320",
                        "name": "FB1",
                        "offset_x": 0,
                        "offset_y": 0
                    }
                }
            else:
                return {
                    "Display0": {
                        "resolution": "unknown",
                        "name": "Display0",
                        "offset_x": 0,
                        "offset_y": 0
                    }
                }
        monitors = {}
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            geometry_part = None
            name_clean = parts[-1].strip("+*")

            # look for something like "1920/444x1080/249+0+0"
            for p in parts:
                if "x" in p and "+" in p:
                    geometry_part = p
                    break
            if not geometry_part:
                monitors[name_clean] = {
                    "resolution": "unknown",
                    "name": name_clean,
                    "offset_x": 0,
                    "offset_y": 0
                }
                continue

            try:
                left, right = geometry_part.split("x", 1)
                w_str = left.split("/")[0]
                plus_index = right.find("+")
                height_part = right[:plus_index]
                offsets_part = right[plus_index:]
                h_str = height_part.split("/")[0]
                offsetbits = offsets_part.lstrip("+").split("+")
                if len(offsetbits) == 2:
                    ox_str, oy_str = offsetbits
                else:
                    ox_str, oy_str = ("0","0")

                width_val = int(w_str)
                height_val = int(h_str)
                offset_x_val = int(ox_str)
                offset_y_val = int(oy_str)

                monitors[name_clean] = {
                    "resolution": f"{width_val}x{height_val}",
                    "name": name_clean,
                    "offset_x": offset_x_val,
                    "offset_y": offset_y_val
                }
            except:
                monitors[name_clean] = {
                    "resolution": "unknown",
                    "name": name_clean,
                    "offset_x": 0,
                    "offset_y": 0
                }

        if not monitors:
            if os.path.exists("/dev/fb1"):
                return {
                    "FB1": {
                        "resolution": "480x320",
                        "name": "FB1",
                        "offset_x": 0,
                        "offset_y": 0
                    }
                }
            else:
                return {
                    "Display0": {
                        "resolution": "unknown",
                        "name": "Display0",
                        "offset_x": 0,
                        "offset_y": 0
                    }
                }
        return monitors
    except:
        if os.path.exists("/dev/fb1"):
            return {
                "FB1": {
                    "resolution": "480x320",
                    "name": "FB1",
                    "offset_x": 0,
                    "offset_y": 0
                }
            }
        else:
            return {
                "Display0": {
                    "resolution": "unknown",
                    "name": "Display0",
                    "offset_x": 0,
                    "offset_y": 0
                }
            }

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

def get_system_stats():
    cpu = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    mem_used_mb = (mem.total - mem.available) / (1024 * 1024)
    loadavg = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0
    try:
        temp = subprocess.check_output(["vcgencmd", "measure_temp"]).decode().strip()
    except:
        temp = "N/A"
    return (cpu, mem_used_mb, loadavg, temp)

def get_folder_prefix(folder_name):
    if not folder_name.strip():
        return "misc"
    words = folder_name.split()
    letters = [w[0].lower() for w in words if w]
    return "".join(letters)

def count_files_in_folder(folder_path):
    if not os.path.isdir(folder_path):
        return 0
    cnt = 0
    valid_ext = (".png", ".jpg", ".jpeg", ".gif")
    for f in os.listdir(folder_path):
        if f.lower().endswith(valid_ext):
            cnt += 1
    return cnt

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
