#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
piviewer.py
Shows images in random/mixed/specific/spotify mode on each connected monitor,
and can display an overlay with clock, weather, etc.
'''

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

from PySide6.QtCore import Qt, QTimer, Slot, QSize
from PySide6.QtGui import QPixmap, QMovie, QPainter, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QGraphicsBlurEffect
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import load_config, save_config, log_message

def detect_monitors():
    '''
    Use xrandr to detect connected monitors.
    Returns a dict of:
      {
        "HDMI-1": {
          "screen_name": "HDMI-1: 1920x1080",
          "width": 1920,
          "height": 1080
        },
        ...
      }
    '''
    monitors = {}
    try:
        output = subprocess.check_output(["xrandr", "--query"]).decode("utf-8")
        for line in output.splitlines():
            if " connected " in line:
                parts = line.split()
                name = parts[0]
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

        # Store the last static image as QPixmap (foreground):
        self.current_pixmap = None
        # Track the current QMovie to properly stop it:
        self.current_movie = None

        # Force our window to the full screen area of whichever monitor it's on
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Central widget
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setScaledContents(False)
        self.bg_label.setStyleSheet("background-color: black;")

        # Foreground label
        self.foreground_label = QLabel(self.main_widget)
        self.foreground_label.setScaledContents(False)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("background-color: black; color: white;")

        # Overlay: clock and weather
        self.clock_label = QLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")

        self.weather_label = QLabel(self.main_widget)
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")

        # Blur effect for background
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(0)
        self.bg_label.setGraphicsEffect(self.blur_effect)

        # Slideshow and clock timers
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        # Load config, then start
        self.cfg = load_config()
        self.reload_settings()
        self.next_image(force=True)

        # Defer geometry-based positioning
        QTimer.singleShot(1000, self.setup_layout)

    def setup_layout(self):
        if not self.isVisible():
            return

        # Ensure we occupy the whole monitor
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())

        rect = self.main_widget.rect()
        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)

        # Make sure background is behind:
        self.bg_label.lower()
        # Foreground above background:
        self.foreground_label.raise_()
        # Overlay on top:
        self.clock_label.raise_()
        self.weather_label.raise_()

        # Position overlay items
        self.clock_label.adjustSize()
        self.clock_label.move(20, 20)

        self.weather_label.adjustSize()
        self.weather_label.move(
            20,
            self.clock_label.y() + self.clock_label.height() + 10
        )

        # Update any existing image with the new layout
        if self.current_pixmap:
            self.updateForegroundScaled()

    def resizeEvent(self, event):
        QMainWindow.resizeEvent(self, event)
        self.setup_layout()
        if self.current_pixmap:
            self.updateForegroundScaled()

    @Slot()
    def reload_settings(self):
        self.cfg = load_config()

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

            # Font sizes/colors
            clock_sz = over.get("clock_font_size", 24)
            weath_sz = over.get("weather_font_size", 18)
            fcolor = over.get("font_color", "#ffffff")
            self.clock_label.setStyleSheet(
                f"color: {fcolor}; font-size: {clock_sz}px; background: transparent;"
            )
            self.weather_label.setStyleSheet(
                f"color: {fcolor}; font-size: {weath_sz}px; background: transparent;"
            )

            # Blur
            user_blur = self.cfg.get("gui", {}).get("background_blur_radius", 0)
            self.blur_effect.setBlurRadius(user_blur)

        # Slideshow interval
        interval_s = self.disp_cfg.get("image_interval", 60)
        self.slideshow_timer.setInterval(interval_s * 1000)
        self.slideshow_timer.start()

        # Mode
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
                if self.current_movie:
                    self.current_movie.stop()
                    self.current_movie.deleteLater()
                    self.current_movie = None
                self.foreground_label.setMovie(None)
                self.foreground_label.setText("No Spotify track info")
                self.foreground_label.setAlignment(Qt.AlignCenter)
            return

        if not self.image_list:
            if self.current_movie:
                self.current_movie.stop()
                self.current_movie.deleteLater()
                self.current_movie = None
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
        if not os.path.exists(fullpath):
            return

        # Stop any existing movie
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None

        ext = os.path.splitext(fullpath)[1].lower()

        # ---------------------------------------------------------------------
        # If it's an animated GIF, we must manually scale QMovie to fill space.
        # ---------------------------------------------------------------------
        if ext == ".gif":
            movie = QMovie(fullpath)
            self.current_movie = movie

            # Figure out original GIF size from the first frame
            temp_reader = QImageReader(fullpath)
            temp_reader.setAutoDetectImageFormat(True)
            if temp_reader.canRead():
                original_size = temp_reader.size()
                ow = original_size.width()
                oh = original_size.height()
            else:
                # fallback if metadata can't be read
                ow, oh = 1, 1

            fw = self.foreground_label.width()
            fh = self.foreground_label.height()

            if fw < 1 or fh < 1 or ow < 1 or oh < 1:
                # fallback: just show the movie at its original size
                self.foreground_label.setMovie(movie)
                movie.start()
                return

            image_aspect = ow / float(oh)
            screen_aspect = fw / float(fh)

            if image_aspect > screen_aspect:
                new_w = fw
                new_h = int(new_w / image_aspect)
            else:
                new_h = fh
                new_w = int(new_h * image_aspect)

            # Scale the animated GIF frames
            movie.setScaledSize(QSize(new_w, new_h))
            self.foreground_label.setMovie(movie)
            movie.start()

            # For the blurred background, grab the first frame via QImageReader
            temp_reader.setCurrentImage(0)
            first_frame = temp_reader.read()
            if not first_frame.isNull():
                pm = QPixmap.fromImage(first_frame)
                blurred = self.make_background_cover(pm)
                if blurred:
                    self.bg_label.setPixmap(blurred)
                else:
                    self.bg_label.clear()
            else:
                self.bg_label.clear()

        else:
            # Still image: scale with our existing approach
            pm = QPixmap(fullpath)
            self.foreground_label.setMovie(None)
            self.current_pixmap = pm

            self.updateForegroundScaled()

            # Update blurred background
            blurred = self.make_background_cover(pm)
            if blurred:
                self.bg_label.setPixmap(blurred)
            else:
                self.bg_label.clear()

    def updateForegroundScaled(self):
        """
        Scale the (still) image up/down so it touches at least one screen edge,
        without clipping, and preserving aspect ratio.
        """
        if not self.current_pixmap:
            return
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        if fw < 1 or fh < 1:
            return

        # Original image size
        iw = self.current_pixmap.width()
        ih = self.current_pixmap.height()
        if iw < 1 or ih < 1:
            return

        # Determine new dimensions (keep entire image visible)
        image_aspect = iw / float(ih)
        screen_aspect = fw / float(fh)

        if image_aspect > screen_aspect:
            # Scale to full width so left and right touch
            new_w = fw
            new_h = int(new_w / image_aspect)
        else:
            # Scale to full height so top and bottom touch
            new_h = fh
            new_w = int(new_h * image_aspect)

        # Scale the image
        scaled_pm = self.current_pixmap.scaled(
            new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        # Draw it on a black background so it is centered
        final_pm = QPixmap(fw, fh)
        final_pm.fill(Qt.black)
        painter = QPainter(final_pm)
        xoff = (fw - new_w) // 2
        yoff = (fh - new_h) // 2
        painter.drawPixmap(xoff, yoff, scaled_pm)
        painter.end()

        self.foreground_label.setPixmap(final_pm)

    def make_background_cover(self, pixmap):
        """
        The blurred background: we do a 'cover' fill that may crop edges.
        """
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        pw, ph = pixmap.width(), pixmap.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return None

        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)

        # We'll fill the entire screen, cropping edges
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

        self.windows = []
        for dname, dcfg in self.cfg.get("displays", {}).items():
            w = DisplayWindow(dname, dcfg)
            title = dcfg.get("screen_name", dname)
            w.setWindowTitle(title)
            w.show()
            self.windows.append(w)

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