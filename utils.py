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
    """Initialize config file if missing, including default displays & multi-device placeholders."""
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "theme": "dark",
            "displays": {},   # local device's displays
            "role": "main",   # 'main' or 'sub'
            "main_ip": "",
            "devices": []     # each device is {name, ip, displays: {...}}
        }
        # Auto-create local displays
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
    Returns something like:
      {
        "HDMI-1": {"resolution": "1920x1080", "name": "HDMI-1"},
        ...
      }
    If xrandr returns none, we check if /dev/fb1 exists and fallback to a single "FB1" monitor.
    Otherwise final fallback is "Display0".
    """
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        if len(lines) <= 1:
            # xrandr sees no monitors
            if os.path.exists("/dev/fb1"):
                return {"FB1": {"resolution": "480x320", "name": "FB1"}}
            else:
                return {"Display0": {"resolution": "unknown", "name": "Display0"}}
        monitors = {}
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            geometry_idx = None
            for i, p in enumerate(parts):
                if 'x' in p and '/' in p:
                    geometry_idx = i
                    break
            if geometry_idx is None:
                name_clean = parts[2].strip("+*")
                monitors[name_clean] = {"resolution": "unknown", "name": name_clean}
                continue
            geometry_part = parts[geometry_idx]
            actual_name = parts[-1]
            try:
                left, right = geometry_part.split("x")
                width = left.split("/")[0]
                right_split_plus = right.split("+")[0]
                height = right_split_plus.split("/")[0]
                resolution = f"{width}x{height}"
            except:
                resolution = "unknown"
            name_clean = actual_name.strip("+*")
            monitors[name_clean] = {"resolution": resolution, "name": name_clean}
        if not monitors:
            # No recognized monitors from xrandr
            if os.path.exists("/dev/fb1"):
                return {"FB1": {"resolution": "480x320", "name": "FB1"}}
            else:
                return {"Display0": {"resolution": "unknown", "name": "Display0"}}
        return monitors
    except:
        # if xrandr fails entirely:
        if os.path.exists("/dev/fb1"):
            return {"FB1": {"resolution": "480x320", "name": "FB1"}}
        else:
            return {"Display0": {"resolution": "unknown", "name": "Display0"}}

def get_hostname():
    try:
        return subprocess.check_output(["hostname"]).decode().strip()
    except:
        return "UnknownHost"

def get_ip_address():
    """Return the first non-127.* IP from `hostname -I`."""
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
    """Return subfolders in IMAGE_DIR (one level)."""
    try:
        return [d for d in os.listdir(IMAGE_DIR) if os.path.isdir(os.path.join(IMAGE_DIR, d))]
    except:
        return []

def get_system_stats():
    """Return (cpu_percent, mem_used_mb, load_1min, temp)."""
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
    """Return how many valid image files are in a folder."""
    if not os.path.isdir(folder_path):
        return 0
    cnt = 0
    for f in os.listdir(folder_path):
        lf = f.lower()
        if lf.endswith((".png", ".jpg", ".jpeg", ".gif")):
            cnt += 1
    return cnt

def get_remote_config(ip):
    """
    Pull FULL config from remote device (includes displays, role, etc.).
    """
    url = f"http://{ip}:8080/sync_config"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote config from {ip}: {e}")
    return None

def get_remote_monitors(ip):
    """Fetch monitor info from remote device as a dict or empty on fail."""
    url = f"http://{ip}:8080/list_monitors"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote monitors from {ip}: {e}")
    return {}

def push_displays_to_remote(ip, displays_obj):
    """
    Push ONLY the "displays" portion to a remote device, ignoring role/devices/main_ip.
    We'll do a partial update:
      { "displays": { ... } }
    Then the remote overwrites only its local 'displays'.
    """
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
    """
    Pull FULL config from remote, but only return the "displays" portion.
    Return None if fail.
    """
    remote_cfg = get_remote_config(ip)
    if not remote_cfg:
        return None
    # We only care about "displays".
    return remote_cfg.get("displays", {})
