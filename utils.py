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
    """Initialize config file if missing, including default displays and multi-device placeholders."""
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "role": "main",  # 'main' or 'sub'
            "main_ip": "",   # Only relevant if sub
            "devices": [],   # If main, store sub-devices
            "theme": "dark",
            "displays": {}
        }
        monitors = detect_monitors()
        for m, mdata in monitors.items():
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
    Returns dict like:
      {
        "HDMI-1": {"resolution": "1920x1080", "name": "HDMI-1"},
        ...
      }
    Fallback to {"Display0": {...}} if not available.
    """
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        if len(lines) <= 1:
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
            return {"Display0": {"resolution": "unknown", "name": "Display0"}}
        return monitors
    except:
        return {"Display0": {"resolution": "unknown", "name": "Display0"}}

def get_hostname():
    try:
        return subprocess.check_output(["hostname"]).decode().strip()
    except:
        return "UnknownHost"

def get_ip_address():
    """Return first non-127.0.0.1 IP from `hostname -I`."""
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
    """Pull config from remote device. Returns dict or None."""
    url = f"http://{ip}:8080/sync_config"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote config from {ip}: {e}")
    return None

def get_remote_monitors(ip):
    """Fetch monitor info from remote device as dict or empty on fail."""
    url = f"http://{ip}:8080/list_monitors"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote monitors from {ip}: {e}")
    return {}

def push_config_to_subdevice(ip, local_cfg):
    """Push local config to a sub device."""
    url = f"http://{ip}:8080/update_config"
    try:
        r = requests.post(url, json=local_cfg, timeout=5)
        if r.status_code == 200:
            log_message(f"Pushed config to {ip} successfully.")
        else:
            log_message(f"Pushing config to {ip} failed with code {r.status_code}.")
    except Exception as e:
        log_message(f"Error pushing config to {ip}: {e}")

def maybe_push_to_subdevices(cfg):
    """
    If role == 'main', push to sub-devices.
    If role == 'sub', push to main if main_ip is set.
    """
    if cfg.get("role") != "main":
        main_ip = cfg.get("main_ip", "")
        if main_ip:
            log_message("Sub device pushing config to main device.")
            push_config_to_subdevice(main_ip, cfg)
        return
    # if main
    for dev in cfg.get("devices", []):
        ip = dev.get("ip")
        if ip:
            push_config_to_subdevice(ip, cfg)
