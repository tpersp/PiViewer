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


class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True

        # Increase cache size to reduce repeated loading on Pi Zero
        self.image_cache = OrderedDict()
        self.cache_capacity = 15

        self.last_displayed_path = None

        # For static images:
        self.current_pixmap = None

        # For animated GIFs:
        # - If scale=100, we let QMovie scale frames internally (fast).
        # - If scale<100, we degrade frames ourselves.
        self.current_movie = None
        self.handling_gif_frames = False

        # Attempt to occupy full screen on this monitor
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

        # Foreground label for final composited frames
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

        # Load config and start
        self.cfg = load_config()
        self.reload_settings()
        self.next_image(force=True)
        # Delay layout until after show
        QTimer.singleShot(1000, self.setup_layout)

    def setup_layout(self):
        if not self.isVisible():
            return
        screen = self.screen()
        if screen:
            self.setGeometry(screen.geometry())
        rect = self.main_widget.rect()

        # Make background & foreground fill entire window
        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)

        # Ensure the background is behind
        self.bg_label.lower()
        self.foreground_label.raise_()
        self.clock_label.raise_()
        self.weather_label.raise_()

        # Position clock & weather near top-left
        self.clock_label.adjustSize()
        self.clock_label.move(20, 20)

        self.weather_label.adjustSize()
        self.weather_label.move(20, self.clock_label.y() + self.clock_label.height() + 10)

        # If we have a static image loaded (not a manually handled GIF),
        # re-scale it to fill the new geometry
        if self.current_pixmap and not self.handling_gif_frames:
            self.updateForegroundScaled()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setup_layout()

    @Slot()
    def reload_settings(self):
        self.cfg = load_config()
        over = self.cfg.get("overlay", {})

        # Show/hide overlay elements
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

            # Overlay style
            csize = over.get("clock_font_size", 24)
            wsize = over.get("weather_font_size", 18)
            fcolor = over.get("font_color", "#ffffff")
            self.clock_label.setStyleSheet(
                f"color: {fcolor}; font-size: {csize}px; background: transparent;"
            )
            self.weather_label.setStyleSheet(
                f"color: {fcolor}; font-size: {wsize}px; background: transparent;"
            )

        # Read background / foreground scale
        gui_cfg = self.cfg.get("gui", {})
        try:
            self.bg_blur_radius = int(gui_cfg.get("background_blur_radius", 0))
        except:
            self.bg_blur_radius = 0

        # background scale -> degrade the background resolution
        try:
            self.bg_scale_percent = int(gui_cfg.get("background_scale_percent", 100))
        except:
            self.bg_scale_percent = 100

        # foreground scale -> degrade the foreground resolution
        try:
            self.fg_scale_percent = int(gui_cfg.get("foreground_scale_percent", 100))
        except:
            self.fg_scale_percent = 100

        # Update slideshow interval
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
            all_img = []
            for folder in folder_list:
                all_img += self.gather_images(folder)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(all_img)
            self.image_list = all_img
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

    # -----------------------------------------------------------------------
    # Caching
    # -----------------------------------------------------------------------
    def load_and_cache_image(self, fullpath):
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            movie = QMovie(fullpath)
            # We'll read first frame to see its size
            tmp_reader = QImageReader(fullpath)
            tmp_reader.setAutoDetectImageFormat(True)
            first_frame = tmp_reader.read()
            data = {"type": "gif", "movie": movie, "first_frame": first_frame}
        else:
            pixmap = QPixmap(fullpath)
            data = {"type": "static", "pixmap": pixmap}
        return data

    def get_cached_image(self, fullpath):
        if fullpath in self.image_cache:
            self.image_cache.move_to_end(fullpath)
            return self.image_cache[fullpath]
        data = self.load_and_cache_image(fullpath)
        self.image_cache[fullpath] = data
        if len(self.image_cache) > self.cache_capacity:
            self.image_cache.popitem(last=False)
        return data

    def preload_next_images(self):
        if not self.image_list:
            return
        preload_count = 3
        for i in range(1, preload_count + 1):
            idx = (self.index + i) % len(self.image_list)
            path = self.image_list[idx]
            if path not in self.image_cache:
                self.get_cached_image(path)

    # -----------------------------------------------------------------------
    # Slideshow logic
    # -----------------------------------------------------------------------
    def next_image(self, force=False):
        if not self.running:
            return

        # If we're in Spotify mode
        if self.current_mode == "spotify":
            path = self.fetch_spotify_album_art()
            if path:
                self.show_foreground_image(path, is_spotify=True)
            else:
                self.clear_foreground_label("No Spotify track info")
            return

        # Otherwise, local images
        if not self.image_list:
            self.clear_foreground_label("No images found")
            return

        # Purge old item from cache
        if self.last_displayed_path and self.last_displayed_path in self.image_cache:
            del self.image_cache[self.last_displayed_path]

        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0
        new_path = self.image_list[self.index]
        self.last_displayed_path = new_path

        self.show_foreground_image(new_path)
        self.preload_next_images()

    def clear_foreground_label(self, message):
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False
        self.foreground_label.setMovie(None)
        self.foreground_label.setText(message)
        self.foreground_label.setAlignment(Qt.AlignCenter)

    def show_foreground_image(self, fullpath, is_spotify=False):
        """
        Show an image/GIF, scaling it so it fills the window dimension
        in at least one dimension (width or height).
        If scale=100 & it's a GIF => we let QMovie handle scaling (faster).
        If scale<100 => we degrade frames ourselves (downscale->upscale).
        """
        if not os.path.exists(fullpath):
            self.clear_foreground_label("Missing file")
            return

        # Stop old QMovie if any
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False

        data = self.get_cached_image(fullpath)

        if data["type"] == "gif" and not is_spotify:
            # If scale=100, we use QMovie.setScaledSize
            if self.fg_scale_percent == 100:
                self.current_movie = data["movie"]
                # Use setScaledSize so it always fills the bounding dimension
                bounding_w, bounding_h = self.calc_bounding_for_window(data["first_frame"])
                if bounding_w > 0 and bounding_h > 0:
                    self.current_movie.setScaledSize(QSize(bounding_w, bounding_h))

                self.foreground_label.setMovie(self.current_movie)
                self.current_movie.start()
                self.handling_gif_frames = False

                # Blur background from first frame
                if not data["first_frame"].isNull():
                    pm = QPixmap.fromImage(data["first_frame"])
                    blurred = self.make_background_cover(pm)
                    if blurred:
                        self.bg_label.setPixmap(blurred)
                    else:
                        self.bg_label.clear()
                else:
                    self.bg_label.clear()

            else:
                # degrade frames ourselves
                self.current_movie = data["movie"]
                self.handling_gif_frames = True
                ff = data["first_frame"]
                if ff.isNull():
                    self.clear_foreground_label("GIF error")
                    return

                # blur background from first frame
                pm = QPixmap.fromImage(ff)
                blurred = self.make_background_cover(pm)
                if blurred:
                    self.bg_label.setPixmap(blurred)
                else:
                    self.bg_label.clear()

                # bounding dimension for the window
                bounding_w, bounding_h = self.calc_bounding_for_window(ff)
                self.gif_bounds = (bounding_w, bounding_h)
                # connect signals
                self.current_movie.frameChanged.connect(self.on_gif_frame_changed)
                self.current_movie.start()

        else:
            # static or Spotify
            if data["type"] == "static":
                self.current_pixmap = data["pixmap"]
            else:
                # treat Spotify as static
                self.current_pixmap = QPixmap(fullpath)

            self.handling_gif_frames = False
            self.updateForegroundScaled()

            # blur background
            blurred = self.make_background_cover(self.current_pixmap)
            if blurred:
                self.bg_label.setPixmap(blurred)
            else:
                self.bg_label.clear()

    def on_gif_frame_changed(self, frame_index):
        """
        For scale<100, degrade each GIF frame manually.
        """
        if not self.current_movie or not self.handling_gif_frames:
            return
        frame_img = self.current_movie.currentImage()
        if frame_img.isNull():
            return

        src_pm = QPixmap.fromImage(frame_img)
        final_pm = self.degrade_foreground(src_pm, self.gif_bounds)
        # Center it in the label
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        bounding_w, bounding_h = self.gif_bounds
        final_img = QImage(fw, fh, QImage.Format_ARGB32)
        final_img.fill(Qt.transparent)

        painter = QPainter(final_img)
        xoff = (fw - bounding_w) // 2
        yoff = (fh - bounding_h) // 2
        painter.drawPixmap(xoff, yoff, final_pm)
        painter.end()

        self.foreground_label.setPixmap(QPixmap.fromImage(final_img))

    def calc_bounding_for_window(self, first_frame):
        """
        Compute bounding size so the image/gif touches side or top/bottom.
        Input: a QImage (the first frame) to get original aspect,
               plus the window size from foreground_label.
        Output: (bounding_w, bounding_h)
        """
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        if fw < 1 or fh < 1:
            return (fw, fh)
        iw = first_frame.width()
        ih = first_frame.height()
        if iw < 1 or ih < 1:
            return (fw, fh)

        # same approach used for fill
        image_aspect = iw / float(ih)
        screen_aspect = fw / float(fh)
        if image_aspect > screen_aspect:
            # fill width
            bounding_w = fw
            bounding_h = int(bounding_w / image_aspect)
        else:
            # fill height
            bounding_h = fh
            bounding_w = int(bounding_h * image_aspect)
        if bounding_w < 1: bounding_w = 1
        if bounding_h < 1: bounding_h = 1
        return (bounding_w, bounding_h)

    def updateForegroundScaled(self):
        """
        For static images: always fill the window dimension in at least
        one direction, then degrade if <100.
        """
        if not self.current_pixmap:
            return

        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        if fw < 1 or fh < 1:
            return

        iw = self.current_pixmap.width()
        ih = self.current_pixmap.height()
        if iw < 1 or ih < 1:
            return

        # bounding dimension that touches side or top/bottom
        bounding_w, bounding_h = self.calc_fill_size(iw, ih, fw, fh)

        # degrade if needed
        final_pm = self.degrade_foreground(self.current_pixmap, (bounding_w, bounding_h))

        # center it in the label
        final_img = QImage(fw, fh, QImage.Format_ARGB32)
        final_img.fill(Qt.transparent)
        painter = QPainter(final_img)
        xoff = (fw - bounding_w) // 2
        yoff = (fh - bounding_h) // 2
        painter.drawPixmap(xoff, yoff, final_pm)
        painter.end()

        self.foreground_label.setPixmap(QPixmap.fromImage(final_img))

    def calc_fill_size(self, iw, ih, fw, fh):
        """
        Return (bounding_w, bounding_h) so that (iw x ih) is scaled
        to fill the (fw x fh) region in at least one dimension.
        This is the same approach as 'cover' or 'fit inside' to
        ensure side-to-side or top-to-bottom is fully spanned.
        """
        if iw <= 0 or ih <= 0 or fw <= 0 or fh <= 0:
            return (fw, fh)

        image_aspect = iw / float(ih)
        screen_aspect = fw / float(fh)
        if image_aspect > screen_aspect:
            # fill width
            new_w = fw
            new_h = int(new_w / image_aspect)
        else:
            # fill height
            new_h = fh
            new_w = int(new_h * image_aspect)
        if new_w < 1: new_w = 1
        if new_h < 1: new_h = 1
        return (new_w, new_h)

    def degrade_foreground(self, src_pm, bounding):
        """
        Scale src_pm up or down to bounding (covering the screen dimension),
        then if fg_scale_percent < 100, do an internal down+up degrade.
        """
        (bw, bh) = bounding
        if bw < 1 or bh < 1:
            return src_pm

        # 1) scale to bounding
        scaled = src_pm.scaled(bw, bh, Qt.KeepAspectRatio, Qt.FastTransformation)
        # 2) degrade if <100
        if self.fg_scale_percent >= 100:
            return scaled

        scale_factor = float(self.fg_scale_percent) / 100.0
        down_w = int(bw * scale_factor)
        down_h = int(bh * scale_factor)
        if down_w < 1 or down_h < 1:
            return scaled

        small = scaled.scaled(down_w, down_h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        final_pm = small.scaled(bw, bh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        return final_pm

    # -----------------------------------------------------------------------
    # Background logic (blur with optional resolution downscale)
    # -----------------------------------------------------------------------
    def make_background_cover(self, pixmap):
        """
        Scale `pixmap` so it covers entire window dimension (with possible cropping),
        optionally degrade resolution, then blur once. Return final QPixmap
        sized exactly to the window geometry.
        """
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        pw, ph = pixmap.width(), pixmap.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return None

        screen_ratio = float(sw) / float(sh)
        img_ratio = float(pw) / float(ph)
        trans_mode = Qt.FastTransformation

        # Step 1: scale so it's big enough to cover
        if img_ratio > screen_ratio:
            new_h = sh
            new_w = int(new_h * img_ratio)
        else:
            new_w = sw
            new_h = int(new_w / img_ratio)

        scaled = pixmap.scaled(new_w, new_h, Qt.KeepAspectRatio, trans_mode)
        # Crop center to exactly (sw x sh)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        final_cover = scaled.copy(xoff, yoff, sw, sh)

        # Step 2: degrade if needed
        if self.bg_scale_percent < 100:
            sf = float(self.bg_scale_percent) / 100.0
            down_w = int(sw * sf)
            down_h = int(sh * sf)
            if down_w > 0 and down_h > 0:
                temp_down = final_cover.scaled(down_w, down_h, Qt.IgnoreAspectRatio, trans_mode)
                # blur that
                temp_blurred = self.blur_pixmap_once(temp_down, self.bg_blur_radius)
                if temp_blurred:
                    final_bg = temp_blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, trans_mode)
                else:
                    final_bg = temp_down.scaled(sw, sh, Qt.IgnoreAspectRatio, trans_mode)
            else:
                final_bg = self.blur_pixmap_once(final_cover, self.bg_blur_radius)
        else:
            final_bg = self.blur_pixmap_once(final_cover, self.bg_blur_radius)

        return final_bg

    def blur_pixmap_once(self, pm, radius):
        if radius <= 0:
            return pm
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pm)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(radius)
        blur.setBlurHints(QGraphicsBlurEffect.PerformanceHint)
        item.setGraphicsEffect(blur)
        scene.addItem(item)

        result = QImage(pm.width(), pm.height(), QImage.Format_ARGB32)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        scene.render(painter, QRectF(0, 0, pm.width(), pm.height()),
                     QRectF(0, 0, pm.width(), pm.height()))
        painter.end()
        return QPixmap.fromImage(result)

    # -----------------------------------------------------------------------
    # Clock & Weather
    # -----------------------------------------------------------------------
    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def update_weather(self):
        cfg = load_config()
        over = cfg.get("overlay", {})
        if not over.get("weather_enabled", False):
            return

        wcfg = cfg.get("weather", {})
        api_key = wcfg.get("api_key", "")
        lat = wcfg.get("lat")
        lon = wcfg.get("lon")
        if not (api_key and lat is not None and lon is not None):
            self.weather_label.setText("Weather: config missing")
            return

        try:
            url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&units=metric&appid={api_key}"
            r = requests.get(url, timeout=5)
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

    # -----------------------------------------------------------------------
    # Spotify
    # -----------------------------------------------------------------------
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

            auth = SpotifyOAuth(client_id=cid, client_secret=csec,
                                redirect_uri=ruri, scope=scope,
                                cache_path=".spotify_cache")
            token_info = auth.get_cached_token()
            if not token_info:
                return None
            if auth.is_token_expired(token_info):
                token_info = auth.refresh_access_token(token_info["refresh_token"])

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
        if detected and len(detected) > 0:
            if "displays" not in self.cfg:
                self.cfg["displays"] = {}
            # Remove any old "Display0"
            if "Display0" in self.cfg["displays"]:
                del self.cfg["displays"]["Display0"]

            # Add newly found monitors if missing
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

        # Create a window per display in config
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
