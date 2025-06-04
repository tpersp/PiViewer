#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
piviewer.py
Shows images in random/mixed/specific/spotify mode on each connected monitor,
and can display an overlay with clock, weather, and Spotify track info.
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

from PySide6.QtCore import Qt, QTimer, Slot, QSize, QRect, QRectF
from PySide6.QtGui import QPixmap, QMovie, QPainter, QImage, QImageReader, QTransform, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QProgressBar,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect, QSizePolicy
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH, VIEWER_HOME
from utils import load_config, save_config, log_message


# --- Custom label for negative (difference) text drawing ---
class NegativeTextLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # When useDifference is True, the text is drawn using difference mode.
        self.useDifference = False

    def paintEvent(self, event):
        if self.useDifference:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Difference)
            painter.setPen(Qt.white)
            painter.setFont(self.font())
            # Combine current alignment with TextWordWrap flag.
            flags = self.alignment() | Qt.TextWordWrap
            painter.drawText(self.rect(), flags, self.text())
        else:
            super().paintEvent(event)


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
        log_message(f"Monitor detection error (fallback): {e}")
    return monitors


class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg, assigned_screen=None):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.assigned_screen = assigned_screen
        self.running = True

        # Caching
        self.image_cache = OrderedDict()
        self.cache_capacity = 15

        self.last_displayed_path = None
        self.current_pixmap = None
        self.current_movie = None
        self.handling_gif_frames = False
        self.last_scaled_foreground_image = None

        # Variables for auto-negative sampling (no longer used for difference mode)
        self.current_drawn_image = None
        self.foreground_drawn_rect = None

        # Set window geometry
        if self.assigned_screen:
            self.setGeometry(self.assigned_screen.geometry())
        else:
            screen = self.screen()
            if screen:
                self.setGeometry(screen.geometry())
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Central widget and child labels
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setScaledContents(False)
        self.bg_label.setStyleSheet("background-color: black;")

        # Foreground label for images
        self.foreground_label = QLabel(self.main_widget)
        self.foreground_label.setScaledContents(False)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("background-color: transparent;")

        # Overlay labels for clock and weather
        self.clock_label = NegativeTextLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setAlignment(Qt.AlignCenter)
        self.clock_label.setStyleSheet("background: transparent;")
        self.weather_label = NegativeTextLabel(self.main_widget)
        self.weather_label.setAlignment(Qt.AlignCenter)
        self.weather_label.setStyleSheet("background: transparent;")

        # Spotify info (track details) label
        self.spotify_info = None
        self.spotify_info_label = NegativeTextLabel(self.main_widget)
        self.spotify_info_label.setAlignment(Qt.AlignCenter)
        self.spotify_info_label.setStyleSheet("background: transparent;")
        self.spotify_info_label.hide()  # Hidden unless in spotify mode

        # New: Spotify progress bar and timer
        self.spotify_progress_bar = QProgressBar(self.main_widget)
        self.spotify_progress_bar.hide()
        self.spotify_progress_bar.setTextVisible(False)
        self.spotify_progress_bar.setMinimum(0)
        self.spotify_progress_bar.setMaximum(100)
        self.spotify_progress_timer = QTimer(self)
        self.spotify_progress_timer.timeout.connect(self.update_spotify_progress)
        self.spotify_fetch_thread = None

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
        QTimer.singleShot(1000, self.setup_layout)

    def setup_layout(self):
        if not self.isVisible():
            return
        if self.assigned_screen:
            self.setGeometry(self.assigned_screen.geometry())
        else:
            screen = self.screen()
            if screen:
                self.setGeometry(screen.geometry())
        rect = self.main_widget.rect()
        margin = 10

        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)
        self.bg_label.lower()

        # Position Spotify info label – its text box spans nearly the full screen width.
        pos = self.disp_cfg.get("spotify_info_position", "bottom-center")
        self.spotify_info_label.setWordWrap(True)
        self.spotify_info_label.setFixedWidth(rect.width() - 2 * margin)
        self.spotify_info_label.adjustSize()
        if "left" in pos:
            self.spotify_info_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        elif "right" in pos:
            self.spotify_info_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        else:
            self.spotify_info_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        if "top" in pos:
            y = margin
        elif "bottom" in pos:
            y = rect.height() - self.spotify_info_label.height() - margin
        else:
            y = (rect.height() - self.spotify_info_label.height()) // 2
        self.spotify_info_label.move(margin, y)
        self.spotify_info_label.raise_()

        # Position Spotify progress bar using its own position setting.
        if self.spotify_progress_bar.isVisible():
            ppos = self.disp_cfg.get("spotify_progress_position", "bottom-center")
            pb_height = 10  # Bar thinner now
            if ppos == "above_info":
                x = self.spotify_info_label.x()
                y = self.spotify_info_label.y() - pb_height - 5
                width = self.spotify_info_label.width()
            elif ppos == "below_info":
                x = self.spotify_info_label.x()
                y = self.spotify_info_label.y() + self.spotify_info_label.height() + 5
                width = self.spotify_info_label.width()
            elif ppos == "top-center":
                width = rect.width() - 2 * margin
                x = margin
                y = margin
            elif ppos == "bottom-center":
                width = rect.width() - 2 * margin
                x = margin
                y = rect.height() - pb_height - margin
            else:
                x = self.spotify_info_label.x()
                y = self.spotify_info_label.y() + self.spotify_info_label.height() + 5
                width = self.spotify_info_label.width()
            self.spotify_progress_bar.setGeometry(x, y, width, pb_height)
            self.spotify_progress_bar.raise_()

        # Helper function for dynamic overlay labels (clock and weather)
        def place_overlay_label(lbl, position, container_rect, y_offset=0):
            full_width = container_rect.width() - 2 * margin
            lbl.setFixedWidth(full_width)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            if "left" in position:
                lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            elif "right" in position:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            lbl.adjustSize()
            # Force re-wrap by using sizeHint() for height.
            required_height = lbl.sizeHint().height()
            lbl.setFixedHeight(required_height)
            h = required_height
            if "top" in position:
                y = margin + y_offset
            elif "bottom" in position:
                y = container_rect.height() - h - margin - y_offset
            else:
                y = (container_rect.height() - h) // 2
            lbl.move(margin, y)
            return (y + h + margin)

        if self.clock_label.isVisible() or self.weather_label.isVisible():
            clock_pos = self.overlay_config.get("clock_position", "bottom-center")
            weather_pos = self.overlay_config.get("weather_position", "bottom-center")
            offset_after_clock = 0
            if self.clock_label.isVisible():
                offset_after_clock = place_overlay_label(self.clock_label, clock_pos, rect, 0)
            if self.weather_label.isVisible():
                if weather_pos == clock_pos and self.clock_label.isVisible():
                    place_overlay_label(self.weather_label, weather_pos, rect, offset_after_clock)
                else:
                    place_overlay_label(self.weather_label, weather_pos, rect, 0)

        if self.current_pixmap and not self.handling_gif_frames:
            self.updateForegroundScaled()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setup_layout()

    @Slot()
    def reload_settings(self):
        self.cfg = load_config()
        if "overlay" in self.disp_cfg:
            over = self.disp_cfg["overlay"]
        else:
            over = self.cfg.get("overlay", {})

        if over.get("clock_enabled", False):
            self.clock_label.show()
        else:
            self.clock_label.hide()

        if over.get("weather_enabled", False):
            self.weather_label.show()
        else:
            self.weather_label.hide()

        cfsize = over.get("clock_font_size", 24)
        wfsize = over.get("weather_font_size", 18)
        if over.get("auto_negative_font", False):
            self.clock_label.useDifference = True
            self.weather_label.useDifference = True
            self.clock_label.setStyleSheet("background: transparent;")
            self.weather_label.setStyleSheet("background: transparent;")
            font_clock = QFont(self.clock_label.font())
            font_clock.setPixelSize(cfsize)
            self.clock_label.setFont(font_clock)
            font_weather = QFont(self.weather_label.font())
            font_weather.setPixelSize(wfsize)
            self.weather_label.setFont(font_weather)
        else:
            self.clock_label.useDifference = False
            self.weather_label.useDifference = False
            fcolor = over.get("font_color", "#FFFFFF")
            self.clock_label.setStyleSheet(f"color: {fcolor}; font-size: {cfsize}px; background: transparent;")
            self.weather_label.setStyleSheet(f"color: {fcolor}; font-size: {wfsize}px; background: transparent;")
        self.overlay_config = over

        gui_cfg = self.cfg.get("gui", {})
        try:
            self.bg_blur_radius = int(gui_cfg.get("background_blur_radius", 0))
        except:
            self.bg_blur_radius = 0
        try:
            self.bg_scale_percent = int(gui_cfg.get("background_scale_percent", 100))
        except:
            self.bg_scale_percent = 100
        try:
            self.fg_scale_percent = int(gui_cfg.get("foreground_scale_percent", 100))
        except:
            self.fg_scale_percent = 100

        interval_s = self.disp_cfg.get("image_interval", 60)
        self.current_mode = self.disp_cfg.get("mode", "random_image")
        if self.current_mode == "spotify":
            interval_s = 5
            if self.disp_cfg.get("spotify_show_progress", False):
                self.spotify_progress_bar.show()
                upd_int = self.disp_cfg.get("spotify_progress_update_interval", 200)
                self.spotify_progress_timer.setInterval(upd_int)
                self.spotify_progress_timer.start()
                theme = self.disp_cfg.get("spotify_progress_theme", "dark")
                if theme == "light":
                    self.spotify_progress_bar.setStyleSheet(
                        "QProgressBar { border: 1px solid #ccc; border-radius: 5px; background-color: #f0f0f0; }"
                        "QProgressBar::chunk { background-color: #a0a0a0; }"
                    )
                elif theme == "dark":
                    self.spotify_progress_bar.setStyleSheet(
                        "QProgressBar { border: 1px solid #444; border-radius: 5px; background-color: #333; }"
                        "QProgressBar::chunk { background-color: #888; }"
                    )
                elif theme == "spotify":
                    self.spotify_progress_bar.setStyleSheet(
                        "QProgressBar { border: 1px solid #1DB954; border-radius: 5px; background-color: #121212; }"
                        "QProgressBar::chunk { background-color: #1DB954; }"
                    )
                elif theme == "coffee":
                    self.spotify_progress_bar.setStyleSheet(
                        "QProgressBar { border: 1px solid #8B4513; border-radius: 5px; background-color: #423828; }"
                        "QProgressBar::chunk { background-color: #8B4513; }"
                    )
                else:
                    self.spotify_progress_bar.setStyleSheet("")
            else:
                self.spotify_progress_bar.hide()
                self.spotify_progress_timer.stop()
        self.slideshow_timer.setInterval(interval_s * 1000)
        self.slideshow_timer.start()

        self.image_list = []
        self.index = 0
        if self.current_mode in ("random_image", "mixed", "specific_image"):
            self.build_local_image_list()

        if self.current_mode == "spotify":
            self.next_image(force=True)

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
            allimg = []
            for folder in folder_list:
                allimg += self.gather_images(folder)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(allimg)
            self.image_list = allimg
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
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            movie = QMovie(fullpath)
            tmp_reader = QImageReader(fullpath)
            tmp_reader.setAutoDetectImageFormat(True)
            first_frame = tmp_reader.read()
            return {"type": "gif", "movie": movie, "first_frame": first_frame}
        else:
            pixmap = QPixmap(fullpath)
            return {"type": "static", "pixmap": pixmap}

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
        for i in range(1, 4):
            idx = (self.index + i) % len(self.image_list)
            path = self.image_list[idx]
            if path not in self.image_cache:
                self.get_cached_image(path)

    def start_spotify_fetch(self):
        if self.spotify_fetch_thread and self.spotify_fetch_thread.is_alive():
            return
        def worker():
            result = self.fetch_spotify_album_art()
            QTimer.singleShot(0, lambda r=result: self.handle_spotify_result(r))
        self.spotify_fetch_thread = threading.Thread(target=worker, daemon=True)
        self.spotify_fetch_thread.start()

    def handle_spotify_result(self, path):
        self.spotify_fetch_thread = None
        if path:
            self.show_foreground_image(path, is_spotify=True)
            self.spotify_info_label.show()
            if self.disp_cfg.get("spotify_show_progress", False):
                self.spotify_progress_bar.show()
                upd_int = self.disp_cfg.get("spotify_progress_update_interval", 200)
                self.spotify_progress_timer.setInterval(upd_int)
                if not self.spotify_progress_timer.isActive():
                    self.spotify_progress_timer.start()
            info_parts = []
            if self.disp_cfg.get("spotify_show_song", True) and self.spotify_info and self.spotify_info.get("song"):
                info_parts.append(self.spotify_info["song"])
            if self.disp_cfg.get("spotify_show_artist", True) and self.spotify_info and self.spotify_info.get("artist"):
                info_parts.append(self.spotify_info["artist"])
            if self.disp_cfg.get("spotify_show_album", True) and self.spotify_info and self.spotify_info.get("album"):
                info_parts.append(self.spotify_info["album"])
            pos = self.disp_cfg.get("spotify_info_position", "bottom-center")
            if "left" in pos or "right" in pos:
                text = "\n".join(info_parts)
            else:
                text = " | ".join(info_parts)
            self.spotify_info_label.setText(text)
            font_size = self.disp_cfg.get("spotify_font_size", 18)
            if self.disp_cfg.get("spotify_negative_font", True):
                self.spotify_info_label.useDifference = True
                self.spotify_info_label.setStyleSheet("background: transparent;")
                font = QFont(self.spotify_info_label.font())
                font.setPixelSize(font_size)
                self.spotify_info_label.setFont(font)
            else:
                self.spotify_info_label.useDifference = False
                self.spotify_info_label.setStyleSheet(f"color: #FFFFFF; font-size: {font_size}px; background: transparent;")
            self.spotify_info_label.raise_()
            self.setup_layout()
        else:
            self.spotify_progress_bar.hide()
            self.spotify_progress_timer.stop()
            fallback_mode = self.disp_cfg.get("fallback_mode", "random_image")
            if fallback_mode in ("random_image", "mixed", "specific_image"):
                image_list_backup = self.image_list
                mode_backup = self.current_mode
                self.current_mode = fallback_mode
                self.build_local_image_list()
                if not self.image_list:
                    self.clear_foreground_label("No fallback images found")
                else:
                    self.index = (self.index + 1) % len(self.image_list)
                    new_path = self.image_list[self.index]
                    self.last_displayed_path = new_path
                    self.show_foreground_image(new_path)
                self.current_mode = mode_backup
                self.image_list = image_list_backup
                self.spotify_info_label.setText("")
                self.spotify_info_label.hide()
            else:
                self.clear_foreground_label("No Spotify track info")
                self.spotify_info_label.setText("")
                self.spotify_info_label.hide()

    def next_image(self, force=False):
        if not self.running:
            return

        if self.current_mode == "spotify":
            if self.spotify_fetch_thread and self.spotify_fetch_thread.is_alive():
                return
            self.start_spotify_fetch()
            return

        if not self.image_list:
            self.clear_foreground_label("No images found")
            return

        if self.last_displayed_path and self.last_displayed_path in self.image_cache:
            del self.image_cache[self.last_displayed_path]

        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0
        new_path = self.image_list[self.index]
        self.last_displayed_path = new_path

        self.show_foreground_image(new_path)
        self.preload_next_images()
        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.update()
            self.weather_label.update()

    def clear_foreground_label(self, message):
        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False
        self.foreground_label.setMovie(None)
        self.foreground_label.setText(message)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("color: white; background-color: transparent;")
        self.spotify_progress_bar.hide()
        self.spotify_progress_timer.stop()

    def show_foreground_image(self, fullpath, is_spotify=False):
        if not os.path.exists(fullpath):
            self.clear_foreground_label("Missing file")
            return

        if self.current_movie:
            self.current_movie.stop()
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False

        data = self.get_cached_image(fullpath)
        if data["type"] == "gif" and not is_spotify:
            if self.fg_scale_percent == 100:
                self.current_movie = data["movie"]
                ff = data["first_frame"]
                bw, bh = self.calc_bounding_for_window(ff)
                if bw > 0 and bh > 0:
                    self.current_movie.setScaledSize(QSize(bw, bh))
                self.foreground_label.setMovie(self.current_movie)
                self.current_movie.start()
                self.handling_gif_frames = False
                if not ff.isNull():
                    pm = QPixmap.fromImage(ff)
                    blurred = self.make_background_cover(pm)
                    self.bg_label.setPixmap(blurred if blurred else QPixmap())
            else:
                self.current_movie = data["movie"]
                self.handling_gif_frames = True
                ff = data["first_frame"]
                if ff.isNull():
                    self.clear_foreground_label("GIF error")
                    return
                pm = QPixmap.fromImage(ff)
                blurred = self.make_background_cover(pm)
                self.bg_label.setPixmap(blurred if blurred else QPixmap())
                bw, bh = self.calc_bounding_for_window(ff)
                self.gif_bounds = (bw, bh)
                self.current_movie.frameChanged.connect(self.on_gif_frame_changed)
                self.current_movie.start()
        else:
            if data["type"] == "static":
                self.current_pixmap = data["pixmap"]
            else:
                self.current_pixmap = QPixmap(fullpath)
            self.handling_gif_frames = False
            self.updateForegroundScaled()
            blurred = self.make_background_cover(self.current_pixmap)
            self.bg_label.setPixmap(blurred if blurred else QPixmap())
        self.spotify_info_label.raise_()

    def on_gif_frame_changed(self, frame_index):
        if not self.current_movie or not self.handling_gif_frames:
            return
        frm_img = self.current_movie.currentImage()
        if frm_img.isNull():
            return
        src_pm = QPixmap.fromImage(frm_img)
        degraded = self.degrade_foreground(src_pm, self.gif_bounds)
        rotated = self.apply_rotation_if_any(degraded)
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        bw, bh = self.gif_bounds
        final_img = QImage(fw, fh, QImage.Format_ARGB32)
        final_img.fill(Qt.transparent)
        painter = QPainter(final_img)
        xoff = (fw - bw) // 2
        yoff = (fh - bh) // 2
        painter.drawPixmap(xoff, yoff, rotated)
        painter.end()
        self.foreground_label.setPixmap(QPixmap.fromImage(final_img))
        self.last_scaled_foreground_image = final_img
        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.update()
            self.weather_label.update()
        self.spotify_info_label.raise_()

    def calc_bounding_for_window(self, first_frame):
        fw = self.foreground_label.width()
        fh = self.foreground_label.height()
        if fw < 1 or fh < 1:
            return (fw, fh)
        iw = first_frame.width()
        ih = first_frame.height()
        if iw < 1 or ih < 1:
            return (fw, fh)
        image_aspect = float(iw) / float(ih)
        screen_aspect = float(fw) / float(fh)
        if image_aspect > screen_aspect:
            bounding_w = fw
            bounding_h = int(bounding_w / image_aspect)
        else:
            bounding_h = fh
            bounding_w = int(bounding_h * image_aspect)
        if bounding_w < 1: bounding_w = 1
        if bounding_h < 1: bounding_h = 1
        return (bounding_w, bounding_h)

    def updateForegroundScaled(self):
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
        bw, bh = self.calc_fill_size(iw, ih, fw, fh)
        degraded = self.degrade_foreground(self.current_pixmap, (bw, bh))
        rotated = self.apply_rotation_if_any(degraded)
        self.current_drawn_image = rotated.toImage()
        final_img = QImage(fw, fh, QImage.Format_ARGB32)
        final_img.fill(Qt.transparent)
        painter = QPainter(final_img)
        rw = rotated.width()
        rh = rotated.height()
        xoff = (fw - rw) // 2
        yoff = (fh - rh) // 2
        painter.drawPixmap(xoff, yoff, rotated)
        painter.end()
        self.foreground_drawn_rect = QRect(xoff, yoff, rw, rh)
        self.foreground_label.setPixmap(QPixmap.fromImage(final_img))
        self.last_scaled_foreground_image = final_img
        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.update()
            self.weather_label.update()

    def calc_fill_size(self, iw, ih, fw, fh):
        if iw <= 0 or ih <= 0 or fw <= 0 or fh <= 0:
            return (fw, fh)
        image_aspect = float(iw) / float(ih)
        screen_aspect = float(fw) / float(fh)
        if image_aspect > screen_aspect:
            new_w = fw
            new_h = int(new_w / image_aspect)
        else:
            new_h = fh
            new_w = int(new_h * image_aspect)
        if new_w < 1: new_w = 1
        if new_h < 1: new_h = 1
        return (new_w, new_h)

    def degrade_foreground(self, src_pm, bounding):
        bw, bh = bounding
        if bw < 1 or bh < 1:
            return src_pm
        scaled = src_pm.scaled(bw, bh, Qt.KeepAspectRatio, Qt.FastTransformation)
        if self.fg_scale_percent >= 100:
            return scaled
        sf = float(self.fg_scale_percent) / 100.0
        down_w = int(bw * sf)
        down_h = int(bh * sf)
        if down_w < 1 or down_h < 1:
            return scaled
        smaller = scaled.scaled(down_w, down_h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        final_pm = smaller.scaled(bw, bh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        return final_pm

    def apply_rotation_if_any(self, pixmap):
        deg = self.disp_cfg.get("rotate", 0)
        if deg == 0:
            return pixmap
        transform = QTransform()
        transform.rotate(deg)
        return pixmap.transformed(transform, Qt.SmoothTransformation)

    def make_background_cover(self, pixmap):
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        pw, ph = pixmap.width(), pixmap.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return None
        screen_ratio = float(sw) / float(sh)
        img_ratio = float(pw) / float(ph)
        tmode = Qt.FastTransformation
        if img_ratio > screen_ratio:
            new_h = sh
            new_w = int(new_h * img_ratio)
        else:
            new_w = sw
            new_h = int(new_w / img_ratio)
        scaled = pixmap.scaled(new_w, new_h, Qt.KeepAspectRatio, tmode)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        final_cover = scaled.copy(xoff, yoff, sw, sh)
        if self.bg_scale_percent < 100:
            sf = float(self.bg_scale_percent) / 100.0
            down_w = int(sw * sf)
            down_h = int(sh * sf)
            if down_w > 0 and down_h > 0:
                temp_down = final_cover.scaled(down_w, down_h, Qt.IgnoreAspectRatio, tmode)
                blurred = self.blur_pixmap_once(temp_down, self.bg_blur_radius)
                if blurred:
                    final_bg = blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, tmode)
                else:
                    final_bg = temp_down.scaled(sw, sh, Qt.IgnoreAspectRatio, tmode)
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

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def update_weather(self):
        cfg = load_config()
        if "overlay" in self.disp_cfg:
            over = self.disp_cfg["overlay"]
        else:
            over = cfg.get("overlay", {})
        if not over.get("weather_enabled", False):
            return
        wcfg = cfg.get("weather", {})
        api_key = wcfg.get("api_key", "")
        zip_code = wcfg.get("zip_code", "")
        country_code = wcfg.get("country_code", "")
        if not (api_key and zip_code and country_code):
            self.weather_label.setText("Weather: config missing")
            if self.weather_label.isVisible():
                self.setup_layout()
            return
        try:
            url = f"https://api.openweathermap.org/data/2.5/weather?zip={zip_code},{country_code}&units=metric&appid={api_key}"
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
                layout_mode = over.get("weather_layout", "inline")
                if layout_mode == "stacked":
                    weather_text = "\n".join(parts)
                else:
                    weather_text = " | ".join(parts)
                self.weather_label.setWordWrap(True)
                self.weather_label.setText(weather_text)
            else:
                self.weather_label.setText("Weather: error")
        except Exception as e:
            self.weather_label.setText("Weather: error")
            log_message(f"Error updating weather: {e}")
        if self.weather_label.isVisible():
            self.setup_layout()

    def fetch_spotify_album_art(self):
        try:
            cfg = load_config()
            sp_cfg = cfg.get("spotify", {})
            cid = sp_cfg.get("client_id", "")
            csec = sp_cfg.get("client_secret", "")
            ruri = sp_cfg.get("redirect_uri", "")
            scope = sp_cfg.get("scope", "user-read-currently-playing user-read-playback-state")
            if not (cid and csec and ruri):
                self.spotify_info = None
                return None
            auth = SpotifyOAuth(client_id=cid, client_secret=csec,
                                redirect_uri=ruri, scope=scope,
                                cache_path=".spotify_cache")
            token_info = auth.get_cached_token()
            if not token_info:
                self.spotify_info = None
                return None
            if auth.is_token_expired(token_info):
                token_info = auth.refresh_access_token(token_info["refresh_token"])
            sp = spotipy.Spotify(auth=token_info["access_token"])
            current = sp.current_playback()
            if not current or not current.get("item") or not current.get("is_playing", False):
                self.spotify_info = None
                return None
            item = current["item"]
            track_name = item.get("name", "")
            artists = ", ".join([a.get("name", "") for a in item.get("artists", [])])
            album_name = item.get("album", {}).get("name", "")
            self.spotify_info = {
                "song": track_name,
                "artist": artists,
                "album": album_name
            }
            # Capture playback progress info for progress bar updates
            progress_ms = current.get("progress_ms", 0)
            duration_ms = item.get("duration_ms", 0)
            self.spotify_info["progress_ms"] = progress_ms
            self.spotify_info["duration_ms"] = duration_ms
            self.spotify_info["fetched_time"] = time.time()
            album_imgs = item["album"]["images"]
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
            self.spotify_info = None
            return None
        return None

    def update_spotify_progress(self):
        if not self.spotify_info or "progress_ms" not in self.spotify_info or "duration_ms" not in self.spotify_info or "fetched_time" not in self.spotify_info:
            self.spotify_progress_bar.setValue(0)
            return
        progress_ms = self.spotify_info["progress_ms"]
        duration_ms = self.spotify_info["duration_ms"]
        fetched_time = self.spotify_info["fetched_time"]
        elapsed = (time.time() - fetched_time) * 1000  # in ms
        current_progress = progress_ms + elapsed
        if duration_ms > 0:
            percent = min(100, (current_progress / duration_ms) * 100)
        else:
            percent = 0
        self.spotify_progress_bar.setValue(int(percent))

    def pull_displays_from_remote(self, ip):
        pass  # Placeholder if needed

class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)

        fallback_mons = detect_monitors()
        if fallback_mons:
            if "displays" not in self.cfg:
                self.cfg["displays"] = {}
            if "Display0" in self.cfg["displays"]:
                del self.cfg["displays"]["Display0"]
            for mon_name, mon_info in fallback_mons.items():
                if mon_name not in self.cfg["displays"]:
                    self.cfg["displays"][mon_name] = {
                        "mode": "random_image",
                        "fallback_mode": "random_image",
                        "image_interval": 60,
                        "image_category": "",
                        "specific_image": "",
                        "shuffle_mode": False,
                        "mixed_folders": [],
                        "rotate": 0,
                        "screen_name": mon_info["screen_name"]
                    }
                    log_message(f"Added fallback monitor to config: {mon_info['screen_name']}")
            save_config(self.cfg)

        self.windows = []
        screens = self.app.screens()
        i = 0
        for dname, dcfg in self.cfg.get("displays", {}).items():
            assigned_screen = screens[i] if i < len(screens) else None
            w = DisplayWindow(dname, dcfg, assigned_screen)
            if "monitor_model" in dcfg and dcfg["monitor_model"]:
                t = f"{dname} ({dcfg['monitor_model']})"
            else:
                t = dcfg.get("screen_name", dname)
            w.setWindowTitle(t)
            w.show()
            self.windows.append(w)
            i += 1

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
