#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piviewer.py
Shows images in random/mixed/specific/spotify mode on each connected monitor,
and can display an overlay with clock, weather, and Spotify track info.

2025-05-25  •  GIF-safe cache fix
--------------------------------
RuntimeError: “Internal C++ object (PySide6.QtGui.QMovie) already deleted”
was caused by storing a QMovie instance in the image-cache and re-using it
after the underlying C++ object had been freed.

Fix:
  • The cache now holds only the GIF path + first frame (never the QMovie).
  • A *new* QMovie is constructed every time we display a GIF.
  • All .stop() / .deleteLater() calls are wrapped in try/except RuntimeError.
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
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, Slot, QSize, QRect, QRectF
from PySide6.QtGui import (
    QPixmap,
    QMovie,
    QPainter,
    QImage,
    QImageReader,
    QTransform,
    QFont,
)
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QProgressBar,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsBlurEffect,
    QSizePolicy,
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH, VIEWER_HOME
from utils import load_config, save_config, log_message


# ─────────────────────────────────────────────────────────────────────────────
#  Custom QLabel that can render text in “difference” mode
# ─────────────────────────────────────────────────────────────────────────────
class NegativeTextLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.useDifference = False

    def paintEvent(self, event):
        if self.useDifference:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Difference)
            painter.setPen(Qt.white)
            painter.setFont(self.font())
            flags = self.alignment() | Qt.TextWordWrap
            painter.drawText(self.rect(), flags, self.text())
        else:
            super().paintEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  Minimal xrandr parser (fallback) – keeps GUI running even if Flask part
#  hasn’t updated the persistent monitor list yet.
# ─────────────────────────────────────────────────────────────────────────────
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
                            w, h = map(int, res.split("x"))
                        except Exception:
                            w, h = 0, 0
                        monitors[name] = {
                            "screen_name": f"{name}: {w}x{h}",
                            "width": w,
                            "height": h,
                        }
                        break
    except Exception as e:
        log_message(f"Monitor detection error (fallback): {e}")
    return monitors


# ─────────────────────────────────────────────────────────────────────────────
#  Full-screen window per display
# ─────────────────────────────────────────────────────────────────────────────
class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg, assigned_screen=None):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.assigned_screen = assigned_screen
        self.running = True

        # LRU cache – capacity 15
        self.image_cache = OrderedDict()
        self.cache_capacity = 15

        # State holders
        self.last_displayed_path = None
        self.current_pixmap = None
        self.current_movie = None
        self.handling_gif_frames = False
        self.last_scaled_foreground_image = None
        self.current_drawn_image = None
        self.foreground_drawn_rect = None

        # Spotify error tracking
        self.spotify_fail_count = 0
        self.spotify_fail_limit = 12   # 12 × 5 s ≈ 1 min

        # --------------------------- Window geometry
        if self.assigned_screen:
            self.setGeometry(self.assigned_screen.geometry())
        else:
            sc = self.screen()
            if sc:
                self.setGeometry(sc.geometry())
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # --------------------------- child widgets
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color:black;")

        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setScaledContents(False)
        self.bg_label.setStyleSheet("background-color:black;")

        self.foreground_label = QLabel(self.main_widget)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("background-color:transparent;")

        self.clock_label = NegativeTextLabel(self.main_widget)
        self.weather_label = NegativeTextLabel(self.main_widget)

        self.spotify_info_label = NegativeTextLabel(self.main_widget)
        self.spotify_info_label.hide()

        self.spotify_progress_bar = QProgressBar(self.main_widget)
        self.spotify_progress_bar.hide()
        self.spotify_progress_bar.setTextVisible(False)
        self.spotify_progress_bar.setMinimum(0)
        self.spotify_progress_bar.setMaximum(100)

        # --------------------------- timers
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self.next_image)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        self.weather_timer = QTimer(self)
        self.weather_timer.timeout.connect(self.update_weather)
        self.weather_timer.start(60000)

        self.spotify_progress_timer = QTimer(self)
        self.spotify_progress_timer.timeout.connect(self.update_spotify_progress)

        # --------------------------- start-up
        self.cfg = load_config()
        self.reload_settings()
        self.next_image(force=True)
        QTimer.singleShot(1000, self.setup_layout)

    # ─────────────────────────────────────────────────────────────────────────
    #  Geometry / layout helpers  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    def setup_layout(self):
        if not self.isVisible():
            return
        if self.assigned_screen:
            self.setGeometry(self.assigned_screen.geometry())
        else:
            sc = self.screen()
            if sc:
                self.setGeometry(sc.geometry())

        rect = self.main_widget.rect()
        margin = 10

        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)
        self.bg_label.lower()

        # --- Spotify info label
        pos = self.disp_cfg.get("spotify_info_position", "bottom-center")
        self.spotify_info_label.setWordWrap(True)
        self.spotify_info_label.setFixedWidth(rect.width() - margin * 2)
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

        # --- Spotify progress bar
        if self.spotify_progress_bar.isVisible():
            ppos = self.disp_cfg.get("spotify_progress_position", "bottom-center")
            bar_h = 10
            if ppos == "above_info":
                x = self.spotify_info_label.x()
                y = self.spotify_info_label.y() - bar_h - 5
                w = self.spotify_info_label.width()
            elif ppos == "below_info":
                x = self.spotify_info_label.x()
                y = self.spotify_info_label.y() + self.spotify_info_label.height() + 5
                w = self.spotify_info_label.width()
            elif ppos == "top-center":
                x, y = margin, margin
                w = rect.width() - margin * 2
            else:  # bottom-center
                x = margin
                y = rect.height() - bar_h - margin
                w = rect.width() - margin * 2
            self.spotify_progress_bar.setGeometry(x, y, w, bar_h)
            self.spotify_progress_bar.raise_()

        # --- helper to place clock / weather
        def place_overlay(lbl, setting_key, container_rect, extra=0):
            pos_key = self.overlay_config.get(setting_key, "bottom-center")
            full_w = container_rect.width() - margin * 2
            lbl.setFixedWidth(full_w)
            lbl.setWordWrap(True)
            if "left" in pos_key:
                lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            elif "right" in pos_key:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            lbl.adjustSize()
            h = lbl.sizeHint().height()
            if "top" in pos_key:
                y_ = margin + extra
            elif "bottom" in pos_key:
                y_ = container_rect.height() - h - margin - extra
            else:
                y_ = (container_rect.height() - h) // 2
            lbl.move(margin, y_)
            return y_ + h + margin

        next_offset = 0
        if self.clock_label.isVisible():
            next_offset = place_overlay(self.clock_label, "clock_position", rect, 0)
        if self.weather_label.isVisible():
            wsp = self.overlay_config.get("weather_position", "bottom-center")
            if self.clock_label.isVisible() and wsp == self.overlay_config.get(
                "clock_position", "bottom-center"
            ):
                place_overlay(self.weather_label, "weather_position", rect, next_offset)
            else:
                place_overlay(self.weather_label, "weather_position", rect, 0)

        # refresh scaled foreground if needed
        if self.current_pixmap and not self.handling_gif_frames:
            self.updateForegroundScaled()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setup_layout()

    # ─────────────────────────────────────────────────────────────────────────
    #  Settings reload  (unchanged except progress-bar handling)
    # ─────────────────────────────────────────────────────────────────────────
    @Slot()
    def reload_settings(self):
        self.cfg = load_config()
        self.overlay_config = self.disp_cfg.get("overlay", self.cfg.get("overlay", {}))

        # overlay visibility / font
        if self.overlay_config.get("clock_enabled", False):
            self.clock_label.show()
        else:
            self.clock_label.hide()

        if self.overlay_config.get("weather_enabled", False):
            self.weather_label.show()
        else:
            self.weather_label.hide()

        cfsize = self.overlay_config.get("clock_font_size", 24)
        wfsize = self.overlay_config.get("weather_font_size", 18)
        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.useDifference = True
            self.weather_label.useDifference = True
            self.clock_label.setStyleSheet("background:transparent;")
            self.weather_label.setStyleSheet("background:transparent;")
            f1, f2 = QFont(self.clock_label.font()), QFont(self.weather_label.font())
            f1.setPixelSize(cfsize)
            f2.setPixelSize(wfsize)
            self.clock_label.setFont(f1)
            self.weather_label.setFont(f2)
        else:
            self.clock_label.useDifference = False
            self.weather_label.useDifference = False
            fcol = self.overlay_config.get("font_color", "#FFFFFF")
            self.clock_label.setStyleSheet(
                f"color:{fcol}; font-size:{cfsize}px; background:transparent;"
            )
            self.weather_label.setStyleSheet(
                f"color:{fcol}; font-size:{wfsize}px; background:transparent;"
            )

        # GUI-scaling
        gui_cfg = self.cfg.get("gui", {})
        self.bg_blur_radius = int(gui_cfg.get("background_blur_radius", 0))
        self.bg_scale_percent = int(gui_cfg.get("background_scale_percent", 100))
        self.fg_scale_percent = int(gui_cfg.get("foreground_scale_percent", 100))

        # slideshow interval
        self.current_mode = self.disp_cfg.get("mode", "random_image")
        interval_s = 5 if self.current_mode == "spotify" else self.disp_cfg.get(
            "image_interval", 60
        )
        self.slideshow_timer.setInterval(interval_s * 1000)
        self.slideshow_timer.start()

        # progress-bar enable
        if self.current_mode == "spotify" and self.disp_cfg.get("spotify_show_progress", False):
            self.spotify_progress_bar.show()
            upd_int = self.disp_cfg.get("spotify_progress_update_interval", 200)
            self.spotify_progress_timer.setInterval(upd_int)
            if not self.spotify_progress_timer.isActive():
                self.spotify_progress_timer.start()
            theme = self.disp_cfg.get("spotify_progress_theme", "dark")
            if theme == "light":
                self.spotify_progress_bar.setStyleSheet(
                    "QProgressBar{border:1px solid #ccc;border-radius:5px;background:#f0f0f0}"
                    "QProgressBar::chunk{background:#a0a0a0}"
                )
            elif theme == "dark":
                self.spotify_progress_bar.setStyleSheet(
                    "QProgressBar{border:1px solid #444;border-radius:5px;background:#333}"
                    "QProgressBar::chunk{background:#888}"
                )
            elif theme == "spotify":
                self.spotify_progress_bar.setStyleSheet(
                    "QProgressBar{border:1px solid #1DB954;border-radius:5px;background:#121212}"
                    "QProgressBar::chunk{background:#1DB954}"
                )
            elif theme == "coffee":
                self.spotify_progress_bar.setStyleSheet(
                    "QProgressBar{border:1px solid #8B4513;border-radius:5px;background:#423828}"
                    "QProgressBar::chunk{background:#8B4513}"
                )
            else:
                self.spotify_progress_bar.setStyleSheet("")
        else:
            self.spotify_progress_bar.hide()
            self.spotify_progress_timer.stop()

        # (re)build image list if not spotify
        self.image_list = []
        self.index = 0
        if self.current_mode in ("random_image", "mixed", "specific_image"):
            self.build_local_image_list()

        self.next_image(force=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  Helpers to build local image lists  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    def build_local_image_list(self):
        mode = self.current_mode
        if mode == "random_image":
            cat = self.disp_cfg.get("image_category", "")
            imgs = self.gather_images(cat)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(imgs)
            self.image_list = imgs
        elif mode == "mixed":
            folders = self.disp_cfg.get("mixed_folders", [])
            imgs = []
            for fld in folders:
                imgs.extend(self.gather_images(fld))
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(imgs)
            self.image_list = imgs
        elif mode == "specific_image":
            cat = self.disp_cfg.get("image_category", "")
            spec = self.disp_cfg.get("specific_image", "")
            path = os.path.join(IMAGE_DIR, cat, spec)
            self.image_list = [path] if os.path.exists(path) else []

    def gather_images(self, category):
        base = os.path.join(IMAGE_DIR, category) if category else IMAGE_DIR
        if not os.path.isdir(base):
            return []
        res = [
            os.path.join(base, f)
            for f in os.listdir(base)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
        ]
        res.sort()
        return res

    # ─────────────────────────────────────────────────────────────────────────
    #  -------- GIF-safe cache fix BLOCK  -----------------------------------
    # ─────────────────────────────────────────────────────────────────────────
    def load_and_cache_image(self, fullpath):
        """
        Store ONLY path + first frame for GIFs.  Never cache a QMovie.
        """
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            rdr = QImageReader(fullpath)
            rdr.setAutoDetectImageFormat(True)
            first_frame = rdr.read()
            data = {"type": "gif", "path": fullpath, "first_frame": first_frame}
        else:
            data = {"type": "static", "pixmap": QPixmap(fullpath)}
        self.image_cache[fullpath] = data
        if len(self.image_cache) > self.cache_capacity:
            self.image_cache.popitem(last=False)
        return data

    def get_cached_image(self, fullpath):
        if fullpath in self.image_cache:
            self.image_cache.move_to_end(fullpath)
            return self.image_cache[fullpath]
        return self.load_and_cache_image(fullpath)

    # (preload_next_images unchanged)
    def preload_next_images(self):
        if not self.image_list:
            return
        for i in range(1, 4):
            idx = (self.index + i) % len(self.image_list)
            path = self.image_list[idx]
            if path not in self.image_cache:
                self.get_cached_image(path)

    # ─────────────────────────────────────────────────────────────────────────
    #  Slideshow driver  (unchanged except GIF-fix logic calls)
    # ─────────────────────────────────────────────────────────────────────────
    def next_image(self, force=False):
        if not self.running:
            return

        # ---------------------- Spotify mode
        if self.current_mode == "spotify":
            img_path = self.fetch_spotify_album_art()
            if img_path:
                self.spotify_fail_count = 0
                self.show_foreground_image(img_path, is_spotify=True)
                self.spotify_info_label.show()
                if self.disp_cfg.get("spotify_show_progress", False):
                    self.spotify_progress_bar.show()
                self.setup_layout()
                return

            # retry / fallback handling
            self.spotify_fail_count += 1
            self.spotify_progress_bar.hide()
            self.spotify_progress_timer.stop()

            if self.spotify_fail_count >= self.spotify_fail_limit:
                fb_mode = self.disp_cfg.get("fallback_mode", "random_image")
                log_message(
                    f"{self.disp_name}: Spotify unreachable – switching permanently to '{fb_mode}'."
                )
                self.current_mode = fb_mode
                if fb_mode in ("random_image", "mixed", "specific_image"):
                    self.build_local_image_list()
                    self.slideshow_timer.setInterval(
                        self.disp_cfg.get("image_interval", 60) * 1000
                    )
                    self.next_image(force=True)
                else:
                    self.clear_foreground_label("No Spotify track info")
                return

            # temporary fallback for this tick
            fb_mode = self.disp_cfg.get("fallback_mode", "random_image")
            if fb_mode in ("random_image", "mixed", "specific_image"):
                backup_list = self.image_list[:]
                backup_mode = self.current_mode
                self.current_mode = fb_mode
                self.build_local_image_list()
                if self.image_list:
                    self.index = (self.index + 1) % len(self.image_list)
                    self.show_foreground_image(self.image_list[self.index])
                else:
                    self.clear_foreground_label("No fallback images")
                self.current_mode = backup_mode
                self.image_list = backup_list
            else:
                self.clear_foreground_label("No Spotify track info")
            return

        # ---------------------- local image modes
        if not self.image_list:
            self.clear_foreground_label("No images found")
            return

        if self.last_displayed_path and self.last_displayed_path in self.image_cache:
            del self.image_cache[self.last_displayed_path]

        self.index = (self.index + 1) % len(self.image_list)
        new_path = self.image_list[self.index]
        self.last_displayed_path = new_path
        self.show_foreground_image(new_path)
        self.preload_next_images()

        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.update()
            self.weather_label.update()

    # ─────────────────────────────────────────────────────────────────────────
    #  Clear / show helpers  (only GIF-logic changed)
    # ─────────────────────────────────────────────────────────────────────────
    def clear_foreground_label(self, message):
        if self.current_movie:
            try:
                self.current_movie.stop()
            except RuntimeError:
                pass
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False
        self.foreground_label.setMovie(None)
        self.foreground_label.setText(message)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("color:white; background:transparent;")
        self.spotify_progress_bar.hide()
        self.spotify_progress_timer.stop()

    def show_foreground_image(self, fullpath, *, is_spotify=False):
        if not os.path.exists(fullpath):
            self.clear_foreground_label("Missing file")
            return

        # dispose previous movie safely
        if self.current_movie:
            try:
                self.current_movie.stop()
            except RuntimeError:
                pass
            self.current_movie.deleteLater()
            self.current_movie = None
            self.handling_gif_frames = False

        data = self.get_cached_image(fullpath)

        # ---------- GIF handling (new QMovie each time)
        if data["type"] == "gif" and not is_spotify:
            ff = data["first_frame"]
            self.current_movie = QMovie(data["path"])

            if self.fg_scale_percent == 100:
                bw, bh = self.calc_bounding_for_window(ff)
                if bw and bh:
                    self.current_movie.setScaledSize(QSize(bw, bh))
                self.foreground_label.setMovie(self.current_movie)
                self.current_movie.start()
                self.bg_label.setPixmap(
                    self.make_background_cover(QPixmap.fromImage(ff)) or QPixmap()
                )
                self.handling_gif_frames = False
            else:
                self.handling_gif_frames = True
                bw, bh = self.calc_bounding_for_window(ff)
                self.gif_bounds = (bw, bh)
                self.current_movie.frameChanged.connect(self.on_gif_frame_changed)
                self.current_movie.start()
                self.bg_label.setPixmap(
                    self.make_background_cover(QPixmap.fromImage(ff)) or QPixmap()
                )
            return

        # ---------- static image / album art
        if data["type"] == "static":
            self.current_pixmap = data["pixmap"]
        else:  # album art file just downloaded
            self.current_pixmap = QPixmap(fullpath)

        self.updateForegroundScaled()
        self.bg_label.setPixmap(self.make_background_cover(self.current_pixmap) or QPixmap())
        self.spotify_info_label.raise_()

    def on_gif_frame_changed(self, _idx):
        if not self.current_movie or not self.handling_gif_frames:
            return
        frm = self.current_movie.currentImage()
        if frm.isNull():
            return
        src_pm = QPixmap.fromImage(frm)
        degraded = self.degrade_foreground(src_pm, self.gif_bounds)
        rotated = self.apply_rotation_if_any(degraded)

        fw, fh = self.foreground_label.width(), self.foreground_label.height()
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

    # ─────────────────────────────────────────────────────────────────────────
    #  Scaling / rotation / background helpers  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    def calc_bounding_for_window(self, first_frame: QImage):
        fw, fh = self.foreground_label.width(), self.foreground_label.height()
        iw, ih = first_frame.width(), first_frame.height()
        if fw < 1 or fh < 1 or iw < 1 or ih < 1:
            return (fw, fh)
        img_ar = iw / ih
        scr_ar = fw / fh
        if img_ar > scr_ar:
            bw = fw
            bh = int(bw / img_ar)
        else:
            bh = fh
            bw = int(bh * img_ar)
        return max(bw, 1), max(bh, 1)

    def updateForegroundScaled(self):
        if not self.current_pixmap:
            return
        fw, fh = self.foreground_label.width(), self.foreground_label.height()
        if fw < 1 or fh < 1:
            return
        iw, ih = self.current_pixmap.width(), self.current_pixmap.height()
        bw, bh = self.calc_fill_size(iw, ih, fw, fh)
        degraded = self.degrade_foreground(self.current_pixmap, (bw, bh))
        rotated = self.apply_rotation_if_any(degraded)
        self.current_drawn_image = rotated.toImage()

        final_img = QImage(fw, fh, QImage.Format_ARGB32)
        final_img.fill(Qt.transparent)
        painter = QPainter(final_img)
        xoff = (fw - rotated.width()) // 2
        yoff = (fh - rotated.height()) // 2
        painter.drawPixmap(xoff, yoff, rotated)
        painter.end()

        self.foreground_drawn_rect = QRect(xoff, yoff, rotated.width(), rotated.height())
        self.foreground_label.setPixmap(QPixmap.fromImage(final_img))
        self.last_scaled_foreground_image = final_img
        if self.overlay_config.get("auto_negative_font", False):
            self.clock_label.update()
            self.weather_label.update()

    def calc_fill_size(self, iw, ih, fw, fh):
        if iw <= 0 or ih <= 0 or fw <= 0 or fh <= 0:
            return fw, fh
        img_ar = iw / ih
        scr_ar = fw / fh
        if img_ar > scr_ar:
            nw = fw
            nh = int(nw / img_ar)
        else:
            nh = fh
            nw = int(nh * img_ar)
        return max(nw, 1), max(nh, 1)

    def degrade_foreground(self, pm, bounding):
        bw, bh = bounding
        scaled = pm.scaled(bw, bh, Qt.KeepAspectRatio, Qt.FastTransformation)
        if self.fg_scale_percent >= 100:
            return scaled
        sf = self.fg_scale_percent / 100.0
        dw, dh = max(int(bw * sf), 1), max(int(bh * sf), 1)
        tmp = scaled.scaled(dw, dh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
        return tmp.scaled(bw, bh, Qt.IgnoreAspectRatio, Qt.FastTransformation)

    def apply_rotation_if_any(self, pm):
        deg = self.disp_cfg.get("rotate", 0)
        if deg == 0:
            return pm
        t = QTransform()
        t.rotate(deg)
        return pm.transformed(t, Qt.SmoothTransformation)

    def make_background_cover(self, pm):
        rect = self.main_widget.rect()
        sw, sh = rect.width(), rect.height()
        pw, ph = pm.width(), pm.height()
        if sw < 1 or sh < 1 or pw < 1 or ph < 1:
            return None
        scr_ar = sw / sh
        img_ar = pw / ph
        tmode = Qt.FastTransformation
        if img_ar > scr_ar:
            nh = sh
            nw = int(nh * img_ar)
        else:
            nw = sw
            nh = int(nw / img_ar)
        scaled = pm.scaled(nw, nh, Qt.KeepAspectRatio, tmode)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        cropped = scaled.copy(xoff, yoff, sw, sh)

        if self.bg_scale_percent < 100:
            sf = self.bg_scale_percent / 100.0
            dw, dh = max(int(sw * sf), 1), max(int(sh * sf), 1)
            tmp_dn = cropped.scaled(dw, dh, Qt.IgnoreAspectRatio, tmode)
            blurred = self.blur_pixmap_once(tmp_dn, self.bg_blur_radius)
            return blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, tmode)
        else:
            return self.blur_pixmap_once(cropped, self.bg_blur_radius)

    def blur_pixmap_once(self, pm, radius):
        if radius <= 0:
            return pm
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pm)
        blur = QGraphicsBlurEffect()
        blur.setBlurRadius(radius)
        item.setGraphicsEffect(blur)
        scene.addItem(item)
        result = QImage(pm.width(), pm.height(), QImage.Format_ARGB32)
        result.fill(Qt.transparent)
        painter = QPainter(result)
        scene.render(
            painter,
            QRectF(0, 0, pm.width(), pm.height()),
            QRectF(0, 0, pm.width(), pm.height()),
        )
        painter.end()
        return QPixmap.fromImage(result)

    # ─────────────────────────────────────────────────────────────────────────
    #  Clock / weather helpers  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    def update_clock(self):
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    def update_weather(self):
        cfg = load_config()
        over = self.overlay_config
        if not over.get("weather_enabled", False):
            return
        wcfg = cfg.get("weather", {})
        api_key = wcfg.get("api_key", "")
        zip_code = wcfg.get("zip_code", "")
        cc = wcfg.get("country_code", "")
        if not (api_key and zip_code and cc):
            self.weather_label.setText("Weather: config missing")
            if self.weather_label.isVisible():
                self.setup_layout()
            return
        try:
            url = (
                f"https://api.openweathermap.org/data/2.5/weather?"
                f"zip={zip_code},{cc}&units=metric&appid={api_key}"
            )
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                parts = []
                if over.get("show_desc", True):
                    parts.append(data["weather"][0]["description"].title())
                if over.get("show_temp", True):
                    parts.append(f"{data['main']['temp']:.0f}°C")
                if over.get("show_feels_like", False):
                    parts.append(f"Feels:{data['main']['feels_like']:.0f}°C")
                if over.get("show_humidity", False):
                    parts.append(f"Humidity:{data['main']['humidity']}%")
                sep = "\n" if over.get("weather_layout", "inline") == "stacked" else " | "
                self.weather_label.setText(sep.join(parts))
            else:
                self.weather_label.setText("Weather: error")
        except Exception as e:
            self.weather_label.setText("Weather: error")
            log_message(f"Error updating weather: {e}")
        if self.weather_label.isVisible():
            self.setup_layout()

    # ─────────────────────────────────────────────────────────────────────────
    #  Spotify helpers  (unchanged)
    # ─────────────────────────────────────────────────────────────────────────
    def fetch_spotify_album_art(self):
        try:
            cfg = load_config()
            sp_cfg = cfg.get("spotify", {})
            cid = sp_cfg.get("client_id", "")
            csec = sp_cfg.get("client_secret", "")
            ruri = sp_cfg.get("redirect_uri", "")
            scope = sp_cfg.get(
                "scope", "user-read-currently-playing user-read-playback-state"
            )
            if not (cid and csec and ruri):
                self.spotify_info = None
                return None
            auth = SpotifyOAuth(
                client_id=cid,
                client_secret=csec,
                redirect_uri=ruri,
                scope=scope,
                cache_path=".spotify_cache",
            )
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
            artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
            album_name = item.get("album", {}).get("name", "")
            self.spotify_info = {
                "song": track_name,
                "artist": artists,
                "album": album_name,
                "progress_ms": current.get("progress_ms", 0),
                "duration_ms": item.get("duration_ms", 0),
                "fetched_time": time.time(),
            }
            album_imgs = item["album"]["images"]
            if not album_imgs:
                return None
            url = album_imgs[0]["url"]
            resp = requests.get(url, stream=True, timeout=5)
            if resp.status_code == 200:
                tmpf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                for chunk in resp.iter_content(4096):
                    tmpf.write(chunk)
                tmpf.close()

                # update label text & style
                info_parts = []
                if self.disp_cfg.get("spotify_show_song", True):
                    info_parts.append(self.spotify_info["song"])
                if self.disp_cfg.get("spotify_show_artist", True):
                    info_parts.append(self.spotify_info["artist"])
                if self.disp_cfg.get("spotify_show_album", True):
                    info_parts.append(self.spotify_info["album"])
                pos = self.disp_cfg.get("spotify_info_position", "bottom-center")
                sep = "\n" if ("left" in pos or "right" in pos) else " | "
                self.spotify_info_label.setText(sep.join(info_parts))
                fsize = self.disp_cfg.get("spotify_font_size", 18)
                if self.disp_cfg.get("spotify_negative_font", True):
                    self.spotify_info_label.useDifference = True
                    self.spotify_info_label.setStyleSheet("background:transparent;")
                    f = QFont(self.spotify_info_label.font())
                    f.setPixelSize(fsize)
                    self.spotify_info_label.setFont(f)
                else:
                    self.spotify_info_label.useDifference = False
                    self.spotify_info_label.setStyleSheet(
                        f"color:#FFFFFF; font-size:{fsize}px; background:transparent;"
                    )
                return tmpf.name
        except Exception as e:
            log_message(f"Spotify error: {e}")
            self.spotify_info = None
        return None

    def update_spotify_progress(self):
        if not self.spotify_info:
            self.spotify_progress_bar.setValue(0)
            return
        prog = self.spotify_info.get("progress_ms", 0)
        dur = self.spotify_info.get("duration_ms", 0)
        elapsed = (time.time() - self.spotify_info.get("fetched_time", 0)) * 1000
        pct = (prog + elapsed) / dur * 100 if dur > 0 else 0
        self.spotify_progress_bar.setValue(int(min(100, pct)))

    def pull_displays_from_remote(self, ip):
        pass  # placeholder (real function lives in Flask routes)


# ─────────────────────────────────────────────────────────────────────────────
#  Application bootstrap  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)

        fallback_mons = detect_monitors()
        if fallback_mons:
            self.cfg.setdefault("displays", {})
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
                        "screen_name": mon_info["screen_name"],
                    }
                    log_message(f"Added fallback monitor: {mon_info['screen_name']}")
            save_config(self.cfg)

        self.windows = []
        screens = self.app.screens()
        for i, (dname, dcfg) in enumerate(self.cfg.get("displays", {}).items()):
            sc = screens[i] if i < len(screens) else None
            w = DisplayWindow(dname, dcfg, sc)
            title = (
                f"{dname} ({dcfg.get('monitor_model')})"
                if dcfg.get("monitor_model")
                else dcfg.get("screen_name", dname)
            )
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
            log_message(f"Fatal exception in main: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()