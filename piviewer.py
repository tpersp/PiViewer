#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piviewer.py
-----------
A single PySide6 GUI that replaces the old viewer.py + overlay.py logic.
Shows images in random/mixed/specific/spotify mode on each connected monitor,
and can display an overlay with clock, weather, etc.
"""

import sys
import os
import random
import time
import requests
import spotipy
import tempfile
import threading
import subprocess
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Slot, QMetaObject
from PySide6.QtGui import QPixmap, QMovie, QFont
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QWidget, QGraphicsBlurEffect

from spotipy.oauth2 import SpotifyOAuth

from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import load_config, save_config, log_message, get_subfolders, get_system_stats

def detect_monitors():
    """
    Use xrandr to detect connected monitors.
    Returns a dict where keys are monitor names and values are dicts with details.
    Example return:
      {
         "HDMI-1": { "screen_name": "HDMI-1: 1920x1080", "width": 1920, "height": 1080 },
         ...
      }
    """
    monitors = {}
    try:
        output = subprocess.check_output(["xrandr", "--query"]).decode("utf-8")
        for line in output.splitlines():
            if " connected " in line:
                parts = line.split()
                name = parts[0]
                # Look for a part like 1920x1080+0+0
                for part in parts:
                    if "x" in part and "+" in part:
                        res = part.split("+")[0]  # e.g., "1920x1080"
                        try:
                            width, height = res.split("x")
                            width = int(width)
                            height = int(height)
                        except Exception as e:
                            width, height = 0, 0
                        monitors[name] = {
                            "screen_name": f"{name}: {width}x{height}",
                            "width": width,
                            "height": height
                        }
                        break
    except Exception as e:
        log_message(f"Monitor detection error: {e}")
    return monitors

##################################
# DisplayWindow class using absolute positioning
##################################
class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True

        # Remove window frame and go fullscreen.
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Create central widget early so resizeEvent can access it.
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.central_widget.setStyleSheet("background-color: black;")

        # Background label (for blurred cover image)
        self.bg_label = QLabel(self.central_widget)
        self.bg_label.setScaledContents(True)
        self.bg_label.setStyleSheet("background-color: black;")

        # Foreground label (for main image or GIF)
        self.foreground_label = QLabel(self.central_widget)
        self.foreground_label.setScaledContents(True)
        self.foreground_label.setStyleSheet("background-color: black;")

        # Overlay labels for clock and weather.
        self.clock_label = QLabel(self.central_widget)
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        self.clock_label.setAttribute(Qt.WA_TranslucentBackground)
        self.weather_label = QLabel(self.central_widget)
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")
        self.weather_label.setAttribute(Qt.WA_TranslucentBackground)

        # Setup a blur effect for the background image.
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(0)
        self.bg_label.setGraphicsEffect(self.blur_effect)

        # Setup timers for slideshow and clock.
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        self.cfg = load_config()
        self.reload_settings()

        # Start the slideshow.
        self.next_image(force=True)

    def resizeEvent(self, event):
        if not hasattr(self, "central_widget"):
            return super().resizeEvent(event)
        rect = self.central_widget.rect()
        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)
        self.clock_label.adjustSize()
        self.clock_label.move(20, 20)
        self.weather_label.adjustSize()
        self.weather_label.move(20, self.clock_label.y() + self.clock_label.height() + 10)
        super().resizeEvent(event)

    def closeEvent(self, event):
        self.running = False
        super().closeEvent(event)

    @Slot()
    def reload_settings(self):
        """
        Reload configuration from disk and update display settings.
        (Note: The call to setWindowOpacity was removed to avoid plugin warnings.)
        """
        self.cfg = load_config()
        over = self.cfg.get("overlay", {})
        if "clock_font_size" in over:
            sz = over["clock_font_size"]
            self.clock_label.setStyleSheet(
                f"color: {over.get('font_color','#ffffff')}; font-size: {sz}px; background: transparent;"
            )
        if "weather_font_size" in over:
            sz2 = over["weather_font_size"]
            self.weather_label.setStyleSheet(
                f"color: {over.get('font_color','#ffffff')}; font-size: {sz2}px; background: transparent;"
            )
        # Set blur radius from config.
        user_blur = self.cfg.get("gui", {}).get("background_blur_radius", 0)
        self.blur_effect.setBlurRadius(user_blur)
        # Update slideshow interval.
        interval_ms = self.disp_cfg.get("image_interval", 60) * 1000
        self.slideshow_timer.setInterval(interval_ms)
        self.slideshow_timer.start()

        self.current_mode = self.disp_cfg.get("mode", "random_image")
        self.image_list = []
        self.index = 0
        if self.current_mode in ("random_image", "mixed", "specific_image"):
            self.build_local_image_list()

    def build_local_image_list(self):
        mode = self.current_mode
        if mode == "random_image":
            cat = self.disp_cfg.get("image_category", "")
            images = self.gather_images(cat)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(images)
            self.image_list = images
        elif mode == "mixed":
            folder_list = self.disp_cfg.get("mixed_folders", [])
            images = []
            for f in folder_list:
                images += self.gather_images(f)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(images)
            self.image_list = images
        elif mode == "specific_image":
            cat = self.disp_cfg.get("image_category", "")
            spec = self.disp_cfg.get("specific_image", "")
            path = os.path.join(IMAGE_DIR, cat, spec)
            if os.path.exists(path):
                self.image_list = [path]
            else:
                log_message(f"Specific image not found: {path}")
                self.image_list = []

    def gather_images(self, category):
        base = os.path.join(IMAGE_DIR, category) if category else IMAGE_DIR
        if not os.path.isdir(base):
            return []
        results = []
        for fname in os.listdir(base):
            lf = fname.lower()
            if lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
                results.append(os.path.join(base, fname))
        results.sort()
        return results

    def next_image(self, force=False):
        if not self.running:
            return
        if self.current_mode == "spotify":
            path = self.fetch_spotify_album_art()
            if path:
                self.load_image(path)
            return
        if not self.image_list:
            self.foreground_label.setText("No images found")
            self.foreground_label.setAlignment(Qt.AlignCenter)
            return
        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0
        path = self.image_list[self.index]
        self.load_image(path)

    def load_image(self, fullpath):
        if not os.path.exists(fullpath):
            return
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            movie = QMovie(fullpath)
            self.foreground_label.setMovie(movie)
            movie.start()
            self.bg_label.clear()
        else:
            pm = QPixmap(fullpath)
            self.foreground_label.setMovie(None)
            self.foreground_label.setPixmap(pm)
            scaled_bg = self.make_background_cover(pm)
            self.bg_label.setPixmap(scaled_bg)

    def make_background_cover(self, pixmap):
        rect = self.central_widget.rect()
        sw, sh = rect.width(), rect.height()
        if sw == 0 or sh == 0:
            return pixmap
        pw, ph = pixmap.width(), pixmap.height()
        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)
        if img_ratio > screen_ratio:
            new_h = sh
            new_w = int(img_ratio * new_h)
        else:
            new_w = sw
            new_h = int(new_w / img_ratio)
        scaled = pixmap.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        final = scaled.copy(xoff, yoff, sw, sh)
        return final

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def fetch_spotify_album_art(self):
        try:
            cfg = load_config()
            sp_cfg = cfg.get("spotify", {})
            c_id = sp_cfg.get("client_id", "")
            c_sec = sp_cfg.get("client_secret", "")
            r_uri = sp_cfg.get("redirect_uri", "")
            scope = sp_cfg.get("scope", "user-read-currently-playing user-read-playback-state")
            if not (c_id and c_sec and r_uri):
                return None
            auth = SpotifyOAuth(client_id=c_id, client_secret=c_sec,
                                redirect_uri=r_uri, scope=scope,
                                cache_path=".spotify_cache")
            token_info = auth.get_cached_token()
            if not token_info:
                return None
            if auth.is_token_expired(token_info):
                token_info = auth.refresh_access_token(token_info['refresh_token'])
            sp = spotipy.Spotify(auth=token_info['access_token'])
            current = sp.current_playback()
            if not current or not current.get("item"):
                return None
            album_imgs = current["item"]["album"]["images"]
            if not album_imgs:
                return None
            url = album_imgs[0]["url"]
            resp = requests.get(url, stream=True, timeout=5)
            if resp.status_code == 200:
                tmpf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                for chunk in resp.iter_content(1024):
                    tmpf.write(chunk)
                tmpf.close()
                return tmpf.name
        except Exception as e:
            log_message(f"Spotify error: {e}")
            return None
        return None

#########################################
# PiViewerGUI: Manages one window per display
#########################################
class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)
        # Use xrandr to detect monitors.
        monitors = detect_monitors()
        if monitors:
            self.cfg["displays"] = {}
            for name, info in monitors.items():
                display_info = {
                    "mode": "random_image",
                    "image_interval": 60,
                    "image_category": "",
                    "specific_image": "",
                    "shuffle_mode": False,
                    "mixed_folders": [],
                    "rotate": 0,
                    "screen_name": info["screen_name"]
                }
                self.cfg["displays"][name] = display_info
                log_message(f"Detected monitor: {info['screen_name']}")
        else:
            # Fallback: use QScreen detection.
            self.cfg["displays"] = {}
            for screen in self.app.screens():
                name = screen.name()
                geom = screen.geometry()
                display_info = {
                    "mode": "random_image",
                    "image_interval": 60,
                    "image_category": "",
                    "specific_image": "",
                    "shuffle_mode": False,
                    "mixed_folders": [],
                    "rotate": 0,
                    "screen_name": f"{name}: {geom.width()}x{geom.height()}"
                }
                self.cfg["displays"][name] = display_info
                log_message(f"Detected monitor (fallback): {display_info['screen_name']}")
        save_config(self.cfg)

        self.windows = []
        for dname, dcfg in self.cfg.get("displays", {}).items():
            w = DisplayWindow(dname, dcfg)
            if "screen_name" in dcfg:
                w.setWindowTitle(dcfg["screen_name"])
            else:
                w.setWindowTitle(dname)
            w.show()
            self.windows.append(w)
        self.reload_thread = threading.Thread(target=self.reload_loop, daemon=True)
        self.reload_thread.start()

    def reload_loop(self):
        while True:
            time.sleep(10)
            new_cfg = load_config()
            for dname, dcfg in new_cfg.get("displays", {}).items():
                for w in self.windows:
                    if w.disp_name == dname:
                        QMetaObject.invokeMethod(w, "reload_settings", Qt.QueuedConnection)

    def run(self):
        sys.exit(self.app.exec())

def main():
    try:
        log_message(f"Starting PiViewer GUI (v{APP_VERSION}).")
        gui = PiViewerGUI()
        gui.run()
    except Exception as e:
        log_message(f"Exception in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
