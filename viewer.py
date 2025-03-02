#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import random
import time
import subprocess
import socket
import threading
from datetime import datetime

from config import APP_VERSION, VIEWER_HOME, IMAGE_DIR, CONFIG_PATH, LOG_PATH

def log_message(msg):
    print(msg)
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now()}: {msg}\n")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def mpv_command(sock_path, cmd_dict):
    """Send a JSON IPC command to MPV's UNIX socket."""
    if not os.path.exists(sock_path):
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(sock_path)
            s.sendall((json.dumps(cmd_dict) + "\n").encode("utf-8"))
    except Exception as e:
        log_message(f"[mpv_command] error: {e}")

def apply_loop_property(sock_path, fullpath):
    """For images, always set loop-file to inf."""
    mpv_command(sock_path, {"command": ["set_property", "loop-file", "inf"]})

def detect_monitors():
    """
    Use xrandr --listmonitors to detect connected monitors.
    Returns a list of monitor names, e.g. ["HDMI-1", "HDMI-2"], or ["Display0"] as fallback.
    """
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        if len(lines) <= 1:
            if os.path.exists("/dev/fb1"):
                return ["FB1"]
            else:
                return ["Display0"]
        monitors = []
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                raw_name = parts[-1]
                mname = raw_name.strip("+*")
                monitors.append(mname)
        if not monitors:
            if os.path.exists("/dev/fb1"):
                return ["FB1"]
            else:
                return ["Display0"]
        return monitors
    except:
        if os.path.exists("/dev/fb1"):
            return ["FB1"]
        else:
            return ["Display0"]

def build_full_path(relpath):
    return os.path.join(IMAGE_DIR, relpath)

def get_images_in_category(category):
    """
    Return list of image paths (relative to IMAGE_DIR) for the given category.
    Only accept GIF, JPG, JPEG, PNG.
    """
    if category:
        base = os.path.join(IMAGE_DIR, category)
        if not os.path.isdir(base):
            return []
        files = os.listdir(base)
        valid = []
        for f in files:
            lf = f.lower()
            if lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
                valid.append(os.path.join(category, f))
        valid.sort()
        return valid
    else:
        results = []
        for root, dirs, files in os.walk(IMAGE_DIR):
            for f in files:
                lf = f.lower()
                if lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
                    rel = os.path.relpath(os.path.join(root, f), IMAGE_DIR)
                    results.append(rel)
        results.sort()
        return results

def get_mixed_images(folder_list):
    """Combine images from multiple folders into one sorted list."""
    all_files = []
    for cat in folder_list:
        catlist = get_images_in_category(cat)
        all_files.extend(catlist)
    return sorted(all_files)

def get_screen_index(monitor_name):
    """
    Attempts to find index of monitor_name from xrandr --listmonitors output
    so mpv can use --screen=X.
    """
    all_mons = []
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2:
                raw_name = parts[-1]
                mname = raw_name.strip("+*")
                all_mons.append(mname)
    except:
        pass
    if monitor_name in all_mons:
        return all_mons.index(monitor_name)
    return 0

class DisplayThread(threading.Thread):
    def __init__(self, disp_name):
        super().__init__()
        self.disp_name = disp_name
        self.sock_path = f"/tmp/mpv_{disp_name}.sock"
        self.stop_flag = False
        self.mpv_proc = None

    def run(self):
        mpv_cmd = [
            "mpv",
            "--idle",
            "--fullscreen",
            "--no-terminal",
            "--quiet",
            "--force-window=yes",
            "--keep-open=yes",
            "--vo=gpu",
            "--loop-file=inf",
            f"--input-ipc-server={self.sock_path}"
        ]
        screen_index = get_screen_index(self.disp_name)
        mpv_cmd.append(f"--screen={screen_index}")
        log_message(f"[{self.disp_name}] Starting MPV (version {APP_VERSION}): {' '.join(mpv_cmd)}")
        self.mpv_proc = subprocess.Popen(mpv_cmd)

        while not self.stop_flag:
            cfg = load_config()
            disp_cfg = cfg.get("displays", {}).get(self.disp_name, {})
            mode = disp_cfg.get("mode", "random_image")

            if mode == "random_image":
                self.random_slideshow(disp_cfg)
            elif mode == "specific_image":
                self.load_specific(disp_cfg)
            elif mode == "mixed":
                self.load_mixed(disp_cfg)
            else:
                log_message(f"[{self.disp_name}] Unknown mode '{mode}', sleeping 5s.")
                time.sleep(5)

        log_message(f"[{self.disp_name}] Stopping MPV.")
        self.mpv_proc.terminate()
        self.mpv_proc.wait()

    def apply_rotation(self, disp_cfg):
        rotate = disp_cfg.get("rotate", 0)
        mpv_command(self.sock_path, {"command": ["set_property", "video-rotate", rotate]})

    def random_slideshow(self, disp_cfg):
        cat = disp_cfg.get("image_category", "")
        shuffle = disp_cfg.get("shuffle_mode", False)
        interval = disp_cfg.get("image_interval", 60)
        images = get_images_in_category(cat)
        if not images:
            log_message(f"[{self.disp_name}] No images in category='{cat}', wait 10s.")
            time.sleep(10)
            return

        if shuffle:
            random.shuffle(images)
        idx = 0

        while True:
            # Check if the mode changed mid-loop:
            c2 = load_config().get("displays", {}).get(self.disp_name, {})
            if c2.get("mode") != "random_image":
                break
            if c2.get("image_category", "") != cat:
                break

            fullpath = build_full_path(images[idx])
            # (Previously we logged every loadfile, now we skip it to reduce spam)
            mpv_command(self.sock_path, {"command": ["loadfile", fullpath, "replace"]})
            apply_loop_property(self.sock_path, fullpath)
            self.apply_rotation(disp_cfg)

            idx += 1
            if idx >= len(images):
                idx = 0
                if shuffle:
                    random.shuffle(images)

            # Wait interval seconds, checking stop_flag or mode changes
            for _ in range(interval):
                if self.stop_flag:
                    return
                m = load_config().get("displays", {}).get(self.disp_name, {}).get("mode")
                if m != "random_image":
                    return
                time.sleep(1)

    def load_specific(self, disp_cfg):
        cat = disp_cfg.get("image_category", "")
        spec = disp_cfg.get("specific_image", "")
        if not spec:
            log_message(f"[{self.disp_name}] No specific_image set, sleeping 10s.")
            time.sleep(10)
            return

        fullpath = os.path.join(IMAGE_DIR, cat, spec)
        if not os.path.exists(fullpath):
            log_message(f"[{self.disp_name}] specific_image '{fullpath}' not found, sleeping 10s.")
            time.sleep(10)
            return

        # (We skip logging "loadfile" to reduce spam)
        mpv_command(self.sock_path, {"command": ["loadfile", fullpath, "replace"]})
        apply_loop_property(self.sock_path, fullpath)
        self.apply_rotation(disp_cfg)

        while not self.stop_flag:
            c2 = load_config().get("displays", {}).get(self.disp_name, {})
            if c2.get("mode") != "specific_image":
                break
            if c2.get("specific_image", "") != spec:
                break
            time.sleep(1)

    def load_mixed(self, disp_cfg):
        folder_list = disp_cfg.get("mixed_folders", [])
        if not folder_list:
            log_message(f"[{self.disp_name}] No folders in 'mixed_folders', sleeping 10s.")
            time.sleep(10)
            return

        images = get_mixed_images(folder_list)
        if not images:
            log_message(f"[{self.disp_name}] 'mixed' mode but no images found in {folder_list}, sleeping 10s.")
            time.sleep(10)
            return

        shuffle = disp_cfg.get("shuffle_mode", False)
        interval = disp_cfg.get("image_interval", 60)
        if shuffle:
            random.shuffle(images)
        idx = 0

        while True:
            c2 = load_config().get("displays", {}).get(self.disp_name, {})
            if c2.get("mode") != "mixed":
                break
            if c2.get("mixed_folders", []) != folder_list:
                break

            fullpath = build_full_path(images[idx])
            mpv_command(self.sock_path, {"command": ["loadfile", fullpath, "replace"]})
            apply_loop_property(self.sock_path, fullpath)
            self.apply_rotation(disp_cfg)

            idx += 1
            if idx >= len(images):
                idx = 0
                if shuffle:
                    random.shuffle(images)

            for _ in range(interval):
                if self.stop_flag:
                    return
                m = load_config().get("displays", {}).get(self.disp_name, {}).get("mode")
                if m != "mixed":
                    return
                time.sleep(1)

    def stop(self):
        self.stop_flag = True

if __name__ == "__main__":
    log_message(f"Starting viewer (version {APP_VERSION}).")
    monitors = detect_monitors()
    if not monitors:
        log_message("No monitors detected. Exiting.")
        exit(0)

    threads = []
    for m in monitors:
        t = DisplayThread(m)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        log_message("KeyboardInterrupt => shutting down viewer.")
    finally:
        for t in threads:
            t.stop()
        for t in threads:
            t.join()
        log_message("Viewer exit complete.")
