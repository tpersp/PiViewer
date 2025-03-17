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
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, Slot, QSize, QRectF
from PySide6.QtGui import QPixmap, QMovie, QPainter, QImage, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect
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

        # Cache for loaded images/gifs.
        # Uses an OrderedDict as a simple LRU cache with a fixed capacity.
        self.image_cache = OrderedDict()
        self.cache_capacity = 3  # Adjust cache capacity as needed.
        self.last_displayed_path = None  # To remove displayed image from cache

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
        # Foreground area is transparent (so we see the blurred background behind it)
        self.foreground_label.setStyleSheet("background-color: transparent; color: white;")

        # Overlay: clock and weather
        self.clock_label = QLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")

        self.weather_label = QLabel(self.main_widget)
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")

        # === We do not attach QGraphicsBlurEffect to the live label, only a one-time pass below. ===

        # Slideshow and clock timers
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        # Weather update timer (update every 60 seconds)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.update_weather)
        self.weather_timer.start(60000)  # 60000 ms = 60 seconds
        self.update_weather()  # Initial weather update

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

        # Background behind everything
        self.bg_label.lower()
        # Foreground above background
        self.foreground_label.raise_()
        # Overlay on top
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

        # We'll do a one-time blur pass using QGraphicsBlurEffect offscreen
        self.bg_blur_radius = self.cfg.get("gui", {}).get("background_blur_radius", 0)
        if not isinstance(self.bg_blur_radius, int):
            self.bg_blur_radius = 0

        # Background resolution scale factor (default 1.0)
        try:
            self.bg_resolution_scale = float(self.cfg.get("gui", {}).get("background_resolution_scale", 1.0))
        except:
            self.bg_resolution_scale = 1.0

        # NEW: Foreground scale factor from user (percentage, 100 means no scaling)
        try:
            self.fg_scale_percent = float(self.cfg.get("gui", {}).get("foreground_scale_percent", 100)) / 100.0
        except:
            self.fg_scale_percent = 1.0

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

    def load_and_cache_image(self, fullpath):
        """
        Load an image or gif from disk.
        For GIFs, also read the first frame (for the blurred background).
        Returns a dict with type and the loaded data.
        """
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            movie = QMovie(fullpath)
            temp_reader = QImageReader(fullpath)
            temp_reader.setAutoDetectImageFormat(True)
            first_frame = temp_reader.read()
            data = {"type": "gif", "movie": movie, "first_frame": first_frame}
        else:
            pixmap = QPixmap(fullpath)
            data = {"type": "static", "pixmap": pixmap}
        return data

    def get_cached_image(self, fullpath):
        """
        Retrieve a cached image/gif if available; otherwise load it and cache it.
        Implements a simple LRU cache with fixed capacity.
        """
        if fullpath in self.image_cache:
            self.image_cache.move_to_end(fullpath)
            return self.image_cache[fullpath]
        else:
            data = self.load_and_cache_image(fullpath)
            self.image_cache[fullpath] = data
            if len(self.image_cache) > self.cache_capacity:
                self.image_cache.popitem(last=False)
            return data

    def preload_next_images(self):
        """
        Preload a few images (or gifs) ahead of time to improve performance.
        """
        if not self.image_list:
            return
        preload_count = 3  # number of images to preload
        for i in range(1, preload_count + 1):
            next_index = (self.index + i) % len(self.image_list)
            next_path = self.image_list[next_index]
            if next_path not in self.image_cache:
                self.get_cached_image(next_path)

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

        # Remove previously displayed image from cache (to free memory)
        if self.last_displayed_path and self.last_displayed_path in self.image_cache:
            del self.image_cache[self.last_displayed_path]

        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0

        path = self.image_list[self.index]
        self.last_displayed_path = path

        self.show_foreground_image(path)
        self.preload_next_images()

    def show_foreground_image(self, fullpath):
        if not os.path.exists(fullpath):
            return

        # Stop any existing movie
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None

        data = self.get_cached_image(fullpath)
        ext = os.path.splitext(fullpath)[1].lower()

        if data["type"] == "gif":
            movie = data["movie"]
            self.current_movie = movie

            first_frame = data["first_frame"]
            fw = self.foreground_label.width()
            fh = self.foreground_label.height()

            # If dimensions are invalid or first frame is unavailable, fallback
            if fw < 1 or fh < 1 or first_frame.isNull():
                self.foreground_label.setMovie(movie)
                movie.start()
            else:
                ow = first_frame.width()
                oh = first_frame.height()
                # Compute scale factor for aspect fill: use the maximum ratio
                factor = max(fw / ow, fh / oh)
                new_w = int(ow * factor)
                new_h = int(oh * factor)
                movie.setScaledSize(QSize(new_w, new_h))
                self.foreground_label.setMovie(movie)
                movie.start()

            # Update blurred background using the first frame
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
            # Still image
            pixmap = data["pixmap"]
            self.foreground_label.setMovie(None)
            self.current_pixmap = pixmap

            self.updateForegroundScaled()

            # Update blurred background
            blurred = self.make_background_cover(pixmap)
            if blurred:
                self.bg_label.setPixmap(blurred)
            else:
                self.bg_label.clear()

    def updateForegroundScaled(self):
        """
        Scale the (still) image so it fills the entire foreground area
        (using an aspect-fill approach) and then center-crop it to the widget's size.
        """
        if not self.current_pixmap:
            return
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        if fw < 1 or fh < 1:
            return

        # Scale with KeepAspectRatioByExpanding to ensure full coverage.
        scaled = self.current_pixmap.scaled(fw, fh, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        # Apply user-specified foreground scale factor if not 100%
        if self.fg_scale_percent != 1.0:
            new_width = int(scaled.width() * self.fg_scale_percent)
            new_height = int(scaled.height() * self.fg_scale_percent)
            scaled = scaled.scaled(new_width, new_height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

        # Crop the center of the scaled image to exactly fill the foreground label.
        sw = scaled.width()
        sh = scaled.height()
        xoff = (sw - fw) // 2
        yoff = (sh - fh) // 2
        cropped = scaled.copy(xoff, yoff, fw, fh)
        self.foreground_label.setPixmap(cropped)

    def make_background_cover(self, pixmap):
        """
        Do a 'cover' fill that may crop edges, then do a one-time blur with QGraphicsBlurEffect offscreen.
        """
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        pw, ph = pixmap.width(), pixmap.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return None

        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)

        # Fill the entire screen, cropping edges if needed.
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
        # If background resolution is reduced for performance, downscale now.
        if self.bg_resolution_scale < 1.0:
            reduced_width = int(final.width() * self.bg_resolution_scale)
            reduced_height = int(final.height() * self.bg_resolution_scale)
            final = final.scaled(reduced_width, reduced_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        blurred = self.blur_pixmap_once(final, self.bg_blur_radius)
        # Scale the blurred image back up if it was reduced.
        if self.bg_resolution_scale < 1.0:
            blurred = blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return blurred

    def blur_pixmap_once(self, pm, radius):
        """
        Renders a QPixmap with QGraphicsBlurEffect exactly once, offscreen,
        to achieve a soft, spill-like blur without real-time overhead.
        """
        if radius <= 0:
            return pm

        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pm)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(radius)
        item.setGraphicsEffect(blur)
        scene.addItem(item)

        # Render the result to an ARGB image.
        result = QImage(pm.width(), pm.height(), QImage.Format_ARGB32)
        result.fill(Qt.transparent)

        painter = QPainter(result)
        scene.render(
            painter,
            QRectF(0, 0, pm.width(), pm.height()),
            QRectF(0, 0, pm.width(), pm.height())
        )
        painter.end()

        return QPixmap.fromImage(result)

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def update_weather(self):
        """
        Fetch the current weather using the OpenWeatherMap API and update the weather label.
        Only updates if the weather overlay is enabled.
        """
        cfg = load_config()
        weather_cfg = cfg.get("weather", {})
        over = cfg.get("overlay", {})
        if not over.get("weather_enabled", False):
            return
        api_key = weather_cfg.get("api_key", "")
        lat = weather_cfg.get("lat")
        lon = weather_cfg.get("lon")
        if not (api_key and lat is not None and lon is not None):
            self.weather_label.setText("Weather: config missing")
            return
        try:
            weather_url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={api_key}"
            r = requests.get(weather_url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                parts = []
                if over.get("show_desc", True):
                    parts.append(data["weather"][0]["description"].title())
                if over.get("show_temp", True):
                    parts.append(f"{data['main']['temp']}\u00B0C")
                if over.get("show_feels_like", False):
                    parts.append(f"Feels: {data['main']['feels_like']}\u00B0C")
                if over.get("show_humidity", False):
                    parts.append(f"Humidity: {data['main']['humidity']}%")
                weather_text = " | ".join(parts)
                self.weather_label.setText(weather_text)
            else:
                self.weather_label.setText("Weather: error")
        except Exception as e:
            self.weather_label.setText("Weather: error")
            log_message(f"Error updating weather: {e}")

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

        # If we detect real monitors, remove the default "Display0"
        if detected and len(detected) > 0:
            if "displays" not in self.cfg:
                self.cfg["displays"] = {}
            if "Display0" in self.cfg["displays"]:
                del self.cfg["displays"]["Display0"]

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
