#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piviewer.py
-----------
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

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QPixmap, QMovie
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QLabel, QGraphicsBlurEffect

from spotipy.oauth2 import SpotifyOAuth

from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import load_config, save_config, log_message


def detect_monitors():
    """
    Use xrandr to detect connected monitors.
    Returns a dict { monitor_name: { "screen_name": "...", "width": W, "height": H }, ... }
    Example:
      {
        "HDMI-1": {
          "screen_name": "HDMI-1: 1920x1080",
          "width": 1920,
          "height": 1080
        },
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
                # e.g., "1920x1080+0+0"
                for part in parts:
                    if "x" in part and "+" in part:
                        res = part.split("+")[0]
                        try:
                            w, h = res.split("x")
                            w = int(w)
                            h = int(h)
                        except:
                            w, h = 0, 0
                        monitors[name] = {
                            "screen_name": f"{name}: {w}x{h}",
                            "width": w,
                            "height": h
                        }
                        break
    except Exception as e:
        log_message(f"Monitor detection error: {e}")
    return monitors


class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True

        # Remove window frame and go fullscreen.
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Main widget as central widget
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setStyleSheet("background-color: black;")
        self.bg_label.setScaledContents(True)

        # Foreground label
        self.foreground_label = QLabel(self.main_widget)
        # We do NOT setScaledContents(true), so it won't distort the aspect ratio:
        self.foreground_label.setScaledContents(False)
        self.foreground_label.setStyleSheet("color: white; background-color: black;")
        self.foreground_label.setAlignment(Qt.AlignCenter)

        # Overlay: clock + weather
        self.clock_label = QLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")

        self.weather_label = QLabel(self.main_widget)
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")

        # Blur effect for background
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(0)
        self.bg_label.setGraphicsEffect(self.blur_effect)

        # Timers
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        # Load config and start
        self.cfg = load_config()
        self.reload_settings()

        # Immediately load the first image
        self.next_image(force=True)

        # Position labels after the window is up
        self.setup_label_positions()

    def setup_label_positions(self):
        rect = self.main_widget.rect()
        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)

        self.clock_label.adjustSize()
        self.clock_label.move(20, 20)

        self.weather_label.adjustSize()
        self.weather_label.move(
            20,
            self.clock_label.y() + self.clock_label.height() + 10
        )

    def closeEvent(self, event):
        self.running = False
        super().closeEvent(event)

    @Slot()
    def reload_settings(self):
        """
        Load the display config from disk and set up intervals, etc.
        (We do not forcibly rebuild the image list unless the mode/folder changed.)
        """
        self.cfg = load_config()

        # Overlay
        over = self.cfg.get("overlay", {})
        if not over.get("overlay_enabled", False):
            self.clock_label.hide()
            self.weather_label.hide()
        else:
            # Clock
            if over.get("clock_enabled", True):
                self.clock_label.show()
            else:
                self.clock_label.hide()
            # Weather
            if over.get("weather_enabled", False):
                self.weather_label.show()
            else:
                self.weather_label.hide()

            # fonts/colors
            clock_sz = over.get("clock_font_size", 24)
            weath_sz = over.get("weather_font_size", 18)
            fcolor = over.get("font_color", "#ffffff")

            clock_css = f"color: {fcolor}; font-size: {clock_sz}px; background: transparent;"
            weather_css = f"color: {fcolor}; font-size: {weath_sz}px; background: transparent;"
            self.clock_label.setStyleSheet(clock_css)
            self.weather_label.setStyleSheet(weather_css)

            # blur
            user_blur = self.cfg.get("gui", {}).get("background_blur_radius", 0)
            self.blur_effect.setBlurRadius(user_blur)

        # Slideshow interval
        new_interval = self.disp_cfg.get("image_interval", 60)
        self.slideshow_timer.setInterval(new_interval * 1000)
        self.slideshow_timer.start()

        # Current mode
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
            for folder in folder_list:
                images += self.gather_images(folder)
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
                self.show_foreground_image(path)
            else:
                # No track info
                self.foreground_label.setMovie(None)
                self.foreground_label.setText("No Spotify track info")
                self.foreground_label.setAlignment(Qt.AlignCenter)
            return

        # Local images
        if not self.image_list:
            self.foreground_label.setMovie(None)
            self.foreground_label.setText("No images found")
            self.foreground_label.setAlignment(Qt.AlignCenter)
            return

        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0

        path = self.image_list[self.index]
        self.show_foreground_image(path)

    def show_foreground_image(self, fullpath):
        """Load an image or GIF into self.foreground_label, letterboxing to keep aspect ratio."""
        if not os.path.exists(fullpath):
            return
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            movie = QMovie(fullpath)
            self.foreground_label.setMovie(movie)
            movie.start()
            self.bg_label.clear()  # no blur behind GIF
        else:
            pm = QPixmap(fullpath)
            self.foreground_label.setMovie(None)
            # Scale to fit the label's size, but keep aspect ratio
            fw = self.foreground_label.width()
            fh = self.foreground_label.height()
            if fw <= 0 or fh <= 0:
                self.foreground_label.setPixmap(pm)
            else:
                scaled = pm.scaled(fw, fh, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.foreground_label.setPixmap(scaled)
            # Also set blurred background "cover"
            scaled_bg = self.make_background_cover(pm)
            self.bg_label.setPixmap(scaled_bg)

    def make_background_cover(self, pixmap):
        """
        We fill the entire screen behind the image with a blurred "cover" approach:
        cropping the scaled image so no black bars.
        """
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        if sw <= 0 or sh <= 0:
            return pixmap

        pw, ph = pixmap.width(), pixmap.height()
        if pw == 0 or ph == 0:
            return pixmap

        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)

        # expand to fill entire background, then crop
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
            cid = sp_cfg.get("client_id", "")
            csec = sp_cfg.get("client_secret", "")
            ruri = sp_cfg.get("redirect_uri", "")
            scope = sp_cfg.get("scope", "user-read-currently-playing user-read-playback-state")

            if not (cid and csec and ruri):
                return None

            auth = SpotifyOAuth(
                client_id=cid,
                client_secret=csec,
                redirect_uri=ruri,
                scope=scope,
                cache_path=".spotify_cache"
            )
            token_info = auth.get_cached_token()
            if not token_info:
                return None
            if auth.is_token_expired(token_info):
                token_info = auth.refresh_access_token(token_info["refresh_token"])

            import spotipy
            sp = spotipy.Spotify(auth=token_info["access_token"])
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


class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)

        # Detect monitors
        detected = detect_monitors()
        if detected:
            # Merge newly detected monitors into cfg["displays"] if missing
            if "displays" not in self.cfg:
                self.cfg["displays"] = {}

            for mon_name, mon_info in detected.items():
                if mon_name not in self.cfg["displays"]:
                    self.cfg["displays"][mon_name] = {
                        "mode": "random_image",
                        "image_interval": 60,
                        "image_category": "",
                        "specific_image": "",
                        "shuffle_mode": False,
                        "mixed_folders": [],
                        "rotate": 0,
                        "screen_name": mon_info["screen_name"]
                    }
                    log_message(f"Added new monitor to config: {mon_info['screen_name']}")

            save_config(self.cfg)
        else:
            log_message("No monitors detected via xrandr. Using existing config...")

        # Create windows
        self.windows = []
        for dname, dcfg in self.cfg.get("displays", {}).items():
            w = DisplayWindow(dname, dcfg)
            title = dcfg.get("screen_name", dname)
            w.setWindowTitle(title)
            w.show()
            self.windows.append(w)

        # No periodic reload loop: changes are applied if you do "Restart Viewer"
        # or the user calls reload_settings manually.

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
