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

from PySide6.QtCore import Qt, QTimer, Slot, QSize, QRectF, QThread, Signal
from PySide6.QtGui import QPixmap, QMovie, QPainter, QImage, QImageReader
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import load_config, save_config, log_message

# ----------------------------------------------------------------
# Worker thread class to offload heavy image processing.
# It scales the foreground image and computes a blurred background.
# ----------------------------------------------------------------
class ImageProcessingThread(QThread):
    # Emits two QImage objects: one for the processed foreground, one for the processed background.
    processed_images = Signal(object, object)

    def __init__(self, original_image, fg_target_size, bg_target_size, fg_scale_percent, bg_blur_radius, bg_resolution_scale):
        super().__init__()
        self.original_image = original_image  # QImage
        self.fg_target_size = fg_target_size  # QSize for foreground label
        self.bg_target_size = bg_target_size  # QSize for main widget/background
        self.fg_scale_percent = fg_scale_percent
        self.bg_blur_radius = bg_blur_radius
        self.bg_resolution_scale = bg_resolution_scale

    def run(self):
        fg = self.process_foreground(self.original_image, self.fg_target_size, self.fg_scale_percent)
        bg = self.process_background(self.original_image, self.bg_target_size, self.bg_resolution_scale, self.bg_blur_radius)
        self.processed_images.emit(fg, bg)

    def process_foreground(self, image, target_size, fg_scale_percent):
        fw, fh = target_size.width(), target_size.height()
        iw, ih = image.width(), image.height()
        if iw < 1 or ih < 1 or fw < 1 or fh < 1:
            return QImage()
        image_aspect = iw / float(ih)
        screen_aspect = fw / float(fh)
        if image_aspect > screen_aspect:
            new_w = fw
            new_h = int(new_w / image_aspect)
        else:
            new_h = fh
            new_w = int(new_h * image_aspect)
        new_w = int(new_w * fg_scale_percent)
        new_h = int(new_h * fg_scale_percent)
        scaled = image.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        final = QImage(fw, fh, QImage.Format_ARGB32)
        final.fill(Qt.transparent)
        painter = QPainter(final)
        xoff = (fw - new_w) // 2
        yoff = (fh - new_h) // 2
        painter.drawImage(xoff, yoff, scaled)
        painter.end()
        return final

    def process_background(self, image, target_size, bg_resolution_scale, bg_blur_radius):
        sw, sh = target_size.width(), target_size.height()
        pw, ph = image.width(), image.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return QImage()
        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)
        if img_ratio > screen_ratio:
            new_h = sh
            new_w = int(img_ratio * new_h)
        else:
            new_w = sw
            new_h = int(new_w / img_ratio)
        scaled = image.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        final = scaled.copy(xoff, yoff, sw, sh)
        if bg_resolution_scale < 1.0:
            reduced_width = int(final.width() * bg_resolution_scale)
            reduced_height = int(final.height() * bg_resolution_scale)
            final = final.scaled(reduced_width, reduced_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        blurred = self.blur_image(final, bg_blur_radius)
        if bg_resolution_scale < 1.0:
            blurred = blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return blurred

    def blur_image(self, image, radius):
        if radius <= 0:
            return image
        scene = QGraphicsScene()
        pix = QPixmap.fromImage(image)
        item = QGraphicsPixmapItem(pix)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(radius)
        item.setGraphicsEffect(blur)
        scene.addItem(item)
        result = QImage(pix.width(), pix.height(), QImage.Format_ARGB32)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        scene.render(painter, QRectF(0, 0, pix.width(), pix.height()), QRectF(0, 0, pix.width(), pix.height()))
        painter.end()
        return result


# ----------------------------------------------------------------
# Main display window class.
# ----------------------------------------------------------------
class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True

        # Caching: use an OrderedDict as a simple LRU cache.
        self.image_cache = OrderedDict()
        self.cache_capacity = 5  # Adjust as needed.
        self.last_displayed_path = None  # For cache removal

        # For static images:
        self.current_pixmap = None
        # For animated GIFs:
        self.current_movie = None

        # Processed image caches (from worker thread)
        self.cached_fg = None
        self.cached_bg = None
        self.image_thread = None

        # Force full-screen on the monitor
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Central widget
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label (blurred background)
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setScaledContents(False)
        self.bg_label.setStyleSheet("background-color: black;")

        # Foreground label (for the image/GIF)
        self.foreground_label = QLabel(self.main_widget)
        self.foreground_label.setScaledContents(False)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("background-color: transparent; color: white;")

        # Overlay: clock and weather
        self.clock_label = QLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setStyleSheet("color: white; font-size: 24px; background: transparent;")
        self.weather_label = QLabel(self.main_widget)
        self.weather_label.setStyleSheet("color: white; font-size: 18px; background: transparent;")

        # Timers
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.update_weather)
        self.weather_timer.start(60000)
        self.update_weather()

        # Load configuration and start slideshow
        self.cfg = load_config()
        self.reload_settings()
        self.next_image(force=True)
        QTimer.singleShot(1000, self.setup_layout)

    def setup_layout(self):
        if not self.isVisible():
            return
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())
        rect = self.main_widget.rect()
        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)
        self.bg_label.lower()
        self.foreground_label.raise_()
        self.clock_label.raise_()
        self.weather_label.raise_()
        self.clock_label.adjustSize()
        self.clock_label.move(20, 20)
        self.weather_label.adjustSize()
        self.weather_label.move(20, self.clock_label.y() + self.clock_label.height() + 10)
        # If a processed image exists, reapply it on layout change
        if self.cached_fg:
            self.foreground_label.setPixmap(self.cached_fg)
        if self.cached_bg:
            self.bg_label.setPixmap(self.cached_bg)

    def resizeEvent(self, event):
        QMainWindow.resizeEvent(self, event)
        self.setup_layout()
        if self.current_pixmap:
            # Delay reprocessing slightly to avoid rapid-fire processing during resize.
            QTimer.singleShot(100, self.start_image_processing)

    @Slot()
    def reload_settings(self):
        self.cfg = load_config()
        over = self.cfg.get("overlay", {})
        if not over.get("overlay_enabled", False):
            self.clock_label.hide()
            self.weather_label.hide()
        else:
            if over.get("clock_enabled", True):
                self.clock_label.show()
            else:
                self.clock_label.hide()
            if over.get("weather_enabled", False):
                self.weather_label.show()
            else:
                self.weather_label.hide()
            clock_sz = over.get("clock_font_size", 24)
            weath_sz = over.get("weather_font_size", 18)
            fcolor = over.get("font_color", "#ffffff")
            self.clock_label.setStyleSheet(f"color: {fcolor}; font-size: {clock_sz}px; background: transparent;")
            self.weather_label.setStyleSheet(f"color: {fcolor}; font-size: {weath_sz}px; background: transparent;")
        try:
            self.bg_blur_radius = int(self.cfg.get("gui", {}).get("background_blur_radius", 0))
        except:
            self.bg_blur_radius = 0
        try:
            self.bg_resolution_scale = float(self.cfg.get("gui", {}).get("background_resolution_scale", 1.0))
        except:
            self.bg_resolution_scale = 1.0
        try:
            self.fg_scale_percent = float(self.cfg.get("gui", {}).get("foreground_scale_percent", 100)) / 100.0
        except:
            self.fg_scale_percent = 1.0
        interval_s = self.disp_cfg.get("image_interval", 60)
        self.slideshow_timer.setInterval(interval_s * 1000)
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

    # --- Caching functions ---
    def load_and_cache_image(self, fullpath):
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
        # Remove previously displayed image from cache to free memory
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

        ext = os.path.splitext(fullpath)[1].lower()
        data = self.get_cached_image(fullpath)

        if data["type"] == "gif":
            # Handle animated GIFs using the original scaling logic (keep full image visible)
            movie = data["movie"]
            self.current_movie = movie
            temp_reader = QImageReader(fullpath)
            temp_reader.setAutoDetectImageFormat(True)
            first_frame = temp_reader.read()
            if not first_frame.isNull():
                ow = first_frame.width()
                oh = first_frame.height()
            else:
                ow, oh = 1, 1
            fw = self.foreground_label.width()
            fh = self.foreground_label.height()
            if fw < 1 or fh < 1 or ow < 1 or oh < 1:
                self.foreground_label.setMovie(movie)
                movie.start()
            else:
                image_aspect = ow / float(oh)
                screen_aspect = fw / float(fh)
                if image_aspect > screen_aspect:
                    new_w = fw
                    new_h = int(new_w / image_aspect)
                else:
                    new_h = fh
                    new_w = int(new_h * image_aspect)
                movie.setScaledSize(QSize(new_w, new_h))
                self.foreground_label.setMovie(movie)
                movie.start()
            # Update blurred background using first frame
            if not first_frame.isNull():
                pm = QPixmap.fromImage(first_frame)
                # For GIFs we do not offload processing
                blurred = pm.scaled(self.bg_label.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                self.bg_label.setPixmap(blurred)
            else:
                self.bg_label.clear()
        else:
            # Still image: use worker thread for scaling and blurring
            pixmap = data["pixmap"]
            self.current_pixmap = pixmap
            self.start_image_processing()

    def start_image_processing(self):
        if self.current_pixmap is None:
            return
        fg_target_size = self.foreground_label.size()
        bg_target_size = self.main_widget.size()
        original_image = self.current_pixmap.toImage()
        if self.image_thread is not None:
            self.image_thread.terminate()
            self.image_thread.wait()
        self.image_thread = ImageProcessingThread(original_image, fg_target_size, bg_target_size,
                                                  self.fg_scale_percent, self.bg_blur_radius, self.bg_resolution_scale)
        self.image_thread.processed_images.connect(self.on_processing_done)
        self.image_thread.start()

    @Slot(object, object)
    def on_processing_done(self, fg_image, bg_image):
        self.cached_fg = QPixmap.fromImage(fg_image)
        self.cached_bg = QPixmap.fromImage(bg_image)
        self.foreground_label.setPixmap(self.cached_fg)
        self.bg_label.setPixmap(self.cached_bg)

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
        return None


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


def detect_monitors():
    """
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
    """
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
