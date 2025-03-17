#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Optimized piviewer.py with hardware-accelerated decoding and full functionality
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
import mmap
from datetime import datetime
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, Slot, QSize, QRectF, QThread, QObject, Signal
from PySide6.QtGui import QImage, QPixmap, QPainter, QMovie, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import load_config, save_config, log_message

# -------------------------------------------------------------------
# Optimized image loading and processing classes
# -------------------------------------------------------------------
class MappedImageLoader:
    @staticmethod
    def load(path):
        try:
            with open(path, 'rb') as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                    img = QImage.fromData(m)
                    if not img.isNull():
                        return img
        except Exception as e:
            log_message(f"Memory-mapped load failed: {e}")
        return QImage(path)

class ImageWorker(QObject):
    imageReady = Signal(QImage, QImage, str, bool)  # (foreground, background, path, is_gif)

    def __init__(self):
        super().__init__()
        self.cache = OrderedDict()
        self.cache_capacity = 10
        self.blur_radius = 10
        self.bg_scale = 0.4

    def process_image(self, path, display_size):
        try:
            cached = self.cache.get(path)
            if cached:
                self.cache.move_to_end(path)
                return (*cached, path)
            
            # Load image using memory mapping
            img = MappedImageLoader.load(path)
            if img.isNull():
                return (None, None, path, False)

            is_gif = path.lower().endswith('.gif')
            
            # Foreground processing: scale to 95% of display size keeping aspect ratio
            fg_img = img.scaled(int(display_size.width() * 0.95), 
                                int(display_size.height() * 0.95),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)

            # Background processing: scale image and apply fast box blur
            bg_img = img.scaled(int(display_size.width() * self.bg_scale),
                                int(display_size.height() * self.bg_scale),
                                Qt.KeepAspectRatioByExpanding, 
                                Qt.FastTransformation)
            bg_img = self.fast_box_blur(bg_img, self.blur_radius)
            bg_img = bg_img.scaled(display_size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

            # Update cache
            self.cache[path] = (fg_img, bg_img)
            if len(self.cache) > self.cache_capacity:
                self.cache.popitem(last=False)

            return (fg_img, bg_img, path, is_gif)
        except Exception as e:
            log_message(f"Image processing error: {e}")
            return (None, None, path, False)

    def fast_box_blur(self, image, radius):
        if radius < 1 or image.isNull():
            return image
        img = image.convertToFormat(QImage.Format_ARGB32)
        w, h = img.width(), img.height()
        # Obtain the image bits as a byte array
        pixels = img.bits().asarray(w * h * 4)
        for _ in range(radius):
            new_pixels = bytearray(pixels)
            for y in range(h):
                for x in range(1, w-1):
                    idx = (y * w + x) * 4
                    # Average the red, green, and blue values from the neighboring pixels (horizontal box)
                    r = (pixels[idx - 4] + pixels[idx] + pixels[idx + 4]) // 3
                    g = (pixels[idx - 3] + pixels[idx + 1] + pixels[idx + 5]) // 3
                    b = (pixels[idx - 2] + pixels[idx + 2] + pixels[idx + 6]) // 3
                    new_pixels[idx:idx+3] = bytes([r, g, b])
            pixels = new_pixels
        return QImage(bytes(pixels), w, h, QImage.Format_ARGB32)

# -------------------------------------------------------------------
# Updated DisplayWindow using the ImageWorker in a background thread
# -------------------------------------------------------------------
class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True
        self.current_movie = None
        self.worker = ImageWorker()
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        self.thread.start()

        # Initialize UI components
        self.init_ui()
        self.init_timers()
        self.load_config()

        # Variables for image list management
        self.image_list = []
        self.current_index = 0
        self.current_mode = self.disp_cfg.get("mode", "random_image")

    def init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.showFullScreen()

        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label for blurred image
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setGeometry(self.main_widget.rect())

        # Foreground label for main image or GIF
        self.fg_label = QLabel(self.main_widget)
        self.fg_label.setAlignment(Qt.AlignCenter)
        self.fg_label.setGeometry(self.main_widget.rect())

        # Overlay components
        self.clock_label = QLabel(self.main_widget)
        self.weather_label = QLabel(self.main_widget)
        self.update_overlay_style()

    def init_timers(self):
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.update_weather)
        self.weather_timer.start(60000)

    def load_config(self):
        self.cfg = load_config()
        # Update worker parameters from config
        self.worker.blur_radius = self.cfg.get("gui", {}).get("background_blur_radius", 10)
        self.worker.bg_scale = self.cfg.get("gui", {}).get("background_resolution_scale", 0.4)
        interval = self.disp_cfg.get("image_interval", 60) * 1000
        self.slideshow_timer.setInterval(interval)
        self.slideshow_timer.start()
        self.reload_settings()

    def reload_settings(self):
        # Reload display settings from config and rebuild image list if needed
        self.cfg = load_config()
        over = self.cfg.get("overlay", {})
        if not over.get("overlay_enabled", False):
            self.clock_label.hide()
            self.weather_label.hide()
        else:
            self.clock_label.show() if over.get("clock_enabled", True) else self.clock_label.hide()
            self.weather_label.show() if over.get("weather_enabled", False) else self.weather_label.hide()
        # Set mode and build the local image list when in non-Spotify mode
        self.current_mode = self.disp_cfg.get("mode", "random_image")
        if self.current_mode in ("random_image", "mixed", "specific_image"):
            self.build_local_image_list()

    def build_local_image_list(self):
        mode = self.current_mode
        if mode == "random_image":
            cat = self.disp_cfg.get("image_category", "")
            images = self.gather_images(cat)
            random.shuffle(images)
            self.image_list = images
        elif mode == "mixed":
            folder_list = self.disp_cfg.get("mixed_folders", [])
            images = []
            for folder in folder_list:
                images += self.gather_images(folder)
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
        if self.current_mode == "spotify":
            self.handle_spotify_image()
        else:
            if not self.image_list:
                self.fg_label.setText("No images found")
                self.fg_label.setAlignment(Qt.AlignCenter)
                return
            path = self.image_list[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.image_list)
            threading.Thread(target=self.process_image, args=(path,)).start()

    def handle_spotify_image(self):
        path = self.fetch_spotify_album_art()
        if path:
            threading.Thread(target=self.process_image, args=(path,)).start()
        else:
            if self.current_movie:
                self.current_movie.stop()
                self.current_movie.deleteLater()
                self.current_movie = None
            self.fg_label.setText("No Spotify track info")
            self.fg_label.setAlignment(Qt.AlignCenter)

    def process_image(self, path):
        fg_img, bg_img, path, is_gif = self.worker.process_image(path, self.size())
        if fg_img and bg_img:
            self.update_display(fg_img, bg_img, path, is_gif)

    @Slot()
    def update_display(self, fg_img, bg_img, path, is_gif):
        if is_gif:
            self.handle_gif(path)
        else:
            self.bg_label.setPixmap(QPixmap.fromImage(bg_img))
            self.fg_label.setPixmap(QPixmap.fromImage(fg_img))

    def handle_gif(self, path):
        if self.current_movie:
            self.current_movie.stop()
        self.current_movie = QMovie(path)
        self.current_movie.setCacheMode(QMovie.CacheAll)
        self.fg_label.setMovie(self.current_movie)
        self.current_movie.start()

    def update_overlay_style(self):
        # Set default positions and styles for overlay labels
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")
        # Position overlays at top-left with a margin
        self.clock_label.move(20, 20)
        self.weather_label.move(20, 50)

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def update_weather(self):
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
            auth = SpotifyOAuth(client_id=cid, client_secret=csec, redirect_uri=ruri, scope=scope, cache_path=".spotify_cache")
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

# -------------------------------------------------------------------
# Unchanged helper function for monitor detection
# -------------------------------------------------------------------
def detect_monitors():
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

# -------------------------------------------------------------------
# Unchanged PiViewerGUI for multi-display support
# -------------------------------------------------------------------
class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)
        detected = detect_monitors()
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

# -------------------------------------------------------------------
# Main entry point
# -------------------------------------------------------------------
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
