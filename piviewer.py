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
from PySide6.QtGui import QPixmap, QMovie, QPainter, QImage, QImageReader, QTransform, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect, QSizePolicy
)

from spotipy.oauth2 import SpotifyOAuth
from config import APP_VERSION, IMAGE_DIR, LOG_PATH, VIEWER_HOME
from utils import load_config, save_config, log_message

# NEW: import our expanded weather icon map and fallback:
from weathericonmap import OWM_ICON_MAP, FALLBACK_ICON, ALL_WEATHER_ICONS


class NegativeTextLabel(QLabel):
    """
    Custom label that optionally uses a negative (difference) composition mode
    to invert text over bright backgrounds.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.useDifference = False

    def paintEvent(self, event):
        if self.useDifference:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode_Difference)
            # Force the pen to white
            painter.setPen(Qt.white)
            painter.setFont(self.font())
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

        # For caching images/gifs
        self.image_cache = OrderedDict()
        self.cache_capacity = 15

        self.last_displayed_path = None
        self.current_pixmap = None
        self.current_movie = None
        self.handling_gif_frames = False
        self.last_scaled_foreground_image = None
        self.current_drawn_image = None
        self.foreground_drawn_rect = None

        if self.assigned_screen:
            self.setGeometry(self.assigned_screen.geometry())
        else:
            screen = self.screen()
            if screen:
                self.setGeometry(screen.geometry())
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Main widget
        self.main_widget = QWidget(self)
        self.setCentralWidget(self.main_widget)
        self.main_widget.setStyleSheet("background-color: black;")

        # Background label
        self.bg_label = QLabel(self.main_widget)
        self.bg_label.setScaledContents(False)
        self.bg_label.setStyleSheet("background-color: black;")

        # Foreground label for displaying images/gifs
        self.foreground_label = QLabel(self.main_widget)
        self.foreground_label.setScaledContents(False)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.foreground_label.setStyleSheet("background-color: transparent;")

        # Overlays
        self.clock_label = NegativeTextLabel(self.main_widget)
        self.clock_label.setText("00:00:00")
        self.clock_label.setAlignment(Qt.AlignCenter)
        self.clock_label.setStyleSheet("background: transparent;")

        # For debugging the weather label visibility, we give it a red border and translucent background.
        self.weather_label = NegativeTextLabel(self.main_widget)
        self.weather_label.setAlignment(Qt.AlignCenter)
        self.weather_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 150); "
            "border: 1px solid red; "
            "color: #FFFFFF;"
        )
        # Force the label to interpret its text as rich text (HTML)
        self.weather_label.setTextFormat(Qt.RichText)

        # Spotify
        self.spotify_info = None
        self.spotify_info_label = NegativeTextLabel(self.main_widget)
        self.spotify_info_label.setAlignment(Qt.AlignCenter)
        self.spotify_info_label.setStyleSheet("background: transparent;")
        self.spotify_info_label.hide()

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

        # Load config, start
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
            scr = self.screen()
            if scr:
                self.setGeometry(scr.geometry())

        rect = self.main_widget.rect()
        margin = 10

        self.bg_label.setGeometry(rect)
        self.foreground_label.setGeometry(rect)
        self.bg_label.lower()

        # Place Spotify label
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

        def place_overlay_label(lbl, position, container_rect, y_offset=0):
            width_avail = container_rect.width() - 2 * margin
            lbl.setFixedWidth(width_avail)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            if "left" in position:
                lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            elif "right" in position:
                lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
            lbl.adjustSize()
            h = lbl.sizeHint().height()
            lbl.setFixedHeight(h)
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

        self.clock_label.raise_()
        self.weather_label.raise_()
        self.spotify_info_label.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setup_layout()

    @Slot()
    def reload_settings(self):
        self.cfg = load_config()
        over = self.disp_cfg.get("overlay") or self.cfg.get("overlay", {})

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
            self.weather_label.setStyleSheet(
                "background: transparent; border: 1px solid red; color: #FFFFFF;"
            )
            f1 = QFont(self.clock_label.font())
            f1.setPixelSize(cfsize)
            self.clock_label.setFont(f1)
            f2 = QFont(self.weather_label.font())
            f2.setPixelSize(wfsize)
            self.weather_label.setFont(f2)
        else:
            self.clock_label.useDifference = False
            self.weather_label.useDifference = False
            fcolor = over.get("font_color", "#FFFFFF")
            self.clock_label.setStyleSheet(
                f"color: {fcolor}; font-size: {cfsize}px; background: transparent;"
            )
            self.weather_label.setStyleSheet(
                f"border: 1px solid red; font-size: {wfsize}px; color: {fcolor}; background-color: rgba(0,0,0,150);"
            )

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
            coll = []
            for fd in folder_list:
                coll += self.gather_images(fd)
            if self.disp_cfg.get("shuffle_mode", False):
                random.shuffle(coll)
            self.image_list = coll
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
        found = []
        for fn in os.listdir(base):
            lf = fn.lower()
            if lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
                found.append(os.path.join(base, fn))
        found.sort()
        return found

    def load_and_cache_image(self, fullpath):
        ext = os.path.splitext(fullpath)[1].lower()
        if ext == ".gif":
            mv = QMovie(fullpath)
            r = QImageReader(fullpath)
            r.setAutoDetectImageFormat(True)
            ff = r.read()
            return {"type": "gif", "movie": mv, "first_frame": ff}
        else:
            px = QPixmap(fullpath)
            return {"type": "static", "pixmap": px}

    def get_cached_image(self, fullpath):
        if fullpath in self.image_cache:
            self.image_cache.move_to_end(fullpath)
            return self.image_cache[fullpath]
        info = self.load_and_cache_image(fullpath)
        self.image_cache[fullpath] = info
        if len(self.image_cache) > self.cache_capacity:
            self.image_cache.popitem(last=False)
        return info

    def preload_next_images(self):
        if not self.image_list:
            return
        for i in range(1, 4):
            idx = (self.index + i) % len(self.image_list)
            pth = self.image_list[idx]
            if pth not in self.image_cache:
                self.get_cached_image(pth)

    def next_image(self, force=False):
        if not self.running:
            return

        if self.current_mode == "spotify":
            path = self.fetch_spotify_album_art()
            if path:
                self.show_foreground_image(path, is_spotify=True)
                self.spotify_info_label.show()

                sp_song = self.spotify_info.get("song") if self.spotify_info else ""
                sp_art = self.spotify_info.get("artist") if self.spotify_info else ""
                sp_alb = self.spotify_info.get("album") if self.spotify_info else ""

                info_parts = []
                if self.disp_cfg.get("spotify_show_song", True) and sp_song:
                    info_parts.append(sp_song)
                if self.disp_cfg.get("spotify_show_artist", True) and sp_art:
                    info_parts.append(sp_art)
                if self.disp_cfg.get("spotify_show_album", True) and sp_alb:
                    info_parts.append(sp_alb)

                pos = self.disp_cfg.get("spotify_info_position", "bottom-center")
                if "left" in pos or "right" in pos:
                    textval = "\n".join(info_parts)
                else:
                    textval = " | ".join(info_parts)

                self.spotify_info_label.setText(textval)
                fsize = self.disp_cfg.get("spotify_font_size", 18)
                if self.disp_cfg.get("spotify_negative_font", True):
                    self.spotify_info_label.useDifference = True
                    self.spotify_info_label.setStyleSheet("background: transparent;")
                    fnt = QFont(self.spotify_info_label.font())
                    fnt.setPixelSize(fsize)
                    self.spotify_info_label.setFont(fnt)
                else:
                    self.spotify_info_label.useDifference = False
                    self.spotify_info_label.setStyleSheet(
                        f"color: #FFFFFF; font-size: {fsize}px; background: transparent;"
                    )
                self.spotify_info_label.raise_()
                self.setup_layout()

            else:
                fallback_mode = self.disp_cfg.get("fallback_mode", "random_image")
                if fallback_mode in ("random_image", "mixed", "specific_image"):
                    original_list = self.image_list
                    original_mode = self.current_mode
                    self.current_mode = fallback_mode
                    self.build_local_image_list()
                    if not self.image_list:
                        self.clear_foreground_label("No fallback images found")
                    else:
                        self.index = (self.index + 1) % len(self.image_list)
                        newp = self.image_list[self.index]
                        self.last_displayed_path = newp
                        self.show_foreground_image(newp)
                    self.current_mode = original_mode
                    self.image_list = original_list
                    self.spotify_info_label.setText("")
                    self.spotify_info_label.hide()
                else:
                    self.clear_foreground_label("No Spotify track info")
                    self.spotify_info_label.setText("")
                    self.spotify_info_label.hide()
            return

        if not self.image_list:
            self.clear_foreground_label("No images found")
            return

        if self.last_displayed_path and self.last_displayed_path in self.image_cache:
            del self.image_cache[self.last_displayed_path]

        self.index += 1
        if self.index >= len(self.image_list):
            self.index = 0
        newp = self.image_list[self.index]
        self.last_displayed_path = newp

        self.show_foreground_image(newp)
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
        frm = self.current_movie.currentImage()
        if frm.isNull():
            return
        src_pm = QPixmap.fromImage(frm)
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
        img_aspect = float(iw) / float(ih)
        scr_aspect = float(fw) / float(fh)
        if img_aspect > scr_aspect:
            bounding_w = fw
            bounding_h = int(bounding_w / img_aspect)
        else:
            bounding_h = fh
            bounding_w = int(bounding_h * img_aspect)
        if bounding_w < 1:
            bounding_w = 1
        if bounding_h < 1:
            bounding_h = 1
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
            return (fw, fh)
        img_aspect = float(iw) / float(ih)
        scr_aspect = float(fw) / float(fh)
        if img_aspect > scr_aspect:
            new_w = fw
            new_h = int(new_w / img_aspect)
        else:
            new_h = fh
            new_w = int(new_h * img_aspect)
        if new_w < 1:
            new_w = 1
        if new_h < 1:
            new_h = 1
        return (new_w, new_h)

    def degrade_foreground(self, src_pm, bounding):
        bw, bh = bounding
        if bw < 1 or bh < 1:
            return src_pm
        scaled = src_pm.scaled(bw, bh, Qt.KeepAspectRatio, Qt.FastTransformation)
        if self.fg_scale_percent >= 100:
            return scaled
        sf = float(self.fg_scale_percent) / 100.0
        dw = int(bw * sf)
        dh = int(bh * sf)
        if dw < 1 or dh < 1:
            return scaled
        smaller = scaled.scaled(dw, dh, Qt.IgnoreAspectRatio, Qt.FastTransformation)
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
        scr_aspect = float(sw) / float(sh)
        img_aspect = float(pw) / float(ph)
        tmode = Qt.FastTransformation
        if img_aspect > scr_aspect:
            new_h = sh
            new_w = int(new_h * img_aspect)
        else:
            new_w = sw
            new_h = int(new_w / img_aspect)
        scaled = pixmap.scaled(new_w, new_h, Qt.KeepAspectRatio, tmode)
        xoff = (scaled.width() - sw) // 2
        yoff = (scaled.height() - sh) // 2
        final_cover = scaled.copy(xoff, yoff, sw, sh)
        if self.bg_scale_percent < 100:
            sf = float(self.bg_scale_percent) / 100.0
            dw = int(sw * sf)
            dh = int(sh * sf)
            if dw > 0 and dh > 0:
                temp_down = final_cover.scaled(dw, dh, Qt.IgnoreAspectRatio, tmode)
                blurred = self.blur_pixmap_once(temp_down, self.bg_blur_radius)
                if blurred:
                    return blurred.scaled(sw, sh, Qt.IgnoreAspectRatio, tmode)
                else:
                    return temp_down.scaled(sw, sh, Qt.IgnoreAspectRatio, tmode)
            else:
                return self.blur_pixmap_once(final_cover, self.bg_blur_radius)
        else:
            return self.blur_pixmap_once(final_cover, self.bg_blur_radius)

    def blur_pixmap_once(self, pm, radius):
        if radius <= 0:
            return pm
        sc = QGraphicsScene()
        it = QGraphicsPixmapItem(pm)
        bl = QGraphicsBlurEffect()
        bl.setBlurRadius(radius)
        bl.setBlurHints(QGraphicsBlurEffect.PerformanceHint)
        it.setGraphicsEffect(bl)
        sc.addItem(it)
        out = QImage(pm.width(), pm.height(), QImage.Format_ARGB32)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        sc.render(painter, QRectF(0, 0, pm.width(), pm.height()),
                  QRectF(0, 0, pm.width(), pm.height()))
        painter.end()
        return QPixmap.fromImage(out)

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def update_weather(self):
        threading.Thread(target=self.fetch_and_update_weather, daemon=True).start()

    def fetch_and_update_weather(self):
        cfg = load_config()
        over = self.disp_cfg.get("overlay") or cfg.get("overlay", {})
        if not over.get("weather_enabled", False):
            return

        wcfg = cfg.get("weather", {})
        api_key = wcfg.get("api_key", "")
        zip_code = wcfg.get("zip_code", "")
        country_code = wcfg.get("country_code", "")
        if not (api_key and zip_code and country_code):
            def update_missing():
                self.weather_label.show()
                self.weather_label.setText("Weather: config missing")
                self.weather_label.raise_()
                self.setup_layout()
            QTimer.singleShot(0, update_missing)
            return

        try:
            url = f"https://api.openweathermap.org/data/2.5/weather?zip={zip_code},{country_code}&units=metric&appid={api_key}"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()

                api_icon = data["weather"][0].get("icon", "01d")
                ICON_MAP = {
                    "01d": ALL_WEATHER_ICONS.get("wi-day-sunny", FALLBACK_ICON),
                    "01n": ALL_WEATHER_ICONS.get("wi-night-clear", FALLBACK_ICON),
                    "02d": ALL_WEATHER_ICONS.get("wi-day-cloudy", FALLBACK_ICON),
                    "02n": ALL_WEATHER_ICONS.get("wi-night-cloudy", FALLBACK_ICON),
                    "03d": ALL_WEATHER_ICONS.get("wi-cloud", FALLBACK_ICON),
                    "03n": ALL_WEATHER_ICONS.get("wi-cloud", FALLBACK_ICON),
                    "04d": ALL_WEATHER_ICONS.get("wi-cloudy", FALLBACK_ICON),
                    "04n": ALL_WEATHER_ICONS.get("wi-cloudy", FALLBACK_ICON),
                    "09d": ALL_WEATHER_ICONS.get("wi-day-rain", FALLBACK_ICON),
                    "09n": ALL_WEATHER_ICONS.get("wi-night-rain", FALLBACK_ICON),
                    "10d": ALL_WEATHER_ICONS.get("wi-day-rain", FALLBACK_ICON),
                    "10n": ALL_WEATHER_ICONS.get("wi-night-rain", FALLBACK_ICON),
                    "11d": ALL_WEATHER_ICONS.get("wi-day-thunderstorm", FALLBACK_ICON),
                    "11n": ALL_WEATHER_ICONS.get("wi-night-thunderstorm", FALLBACK_ICON),
                    "13d": ALL_WEATHER_ICONS.get("wi-day-snow", FALLBACK_ICON),
                    "13n": ALL_WEATHER_ICONS.get("wi-night-snow", FALLBACK_ICON),
                    "50d": ALL_WEATHER_ICONS.get("wi-day-fog", FALLBACK_ICON),
                    "50n": ALL_WEATHER_ICONS.get("wi-night-fog", FALLBACK_ICON)
                }
                icon_char = ICON_MAP.get(api_icon, FALLBACK_ICON)

                text_parts = []
                if over.get("show_desc", True):
                    text_parts.append(data["weather"][0]["description"].title())
                if over.get("show_temp", True):
                    text_parts.append(f"{ALL_WEATHER_ICONS.get('wi-thermometer', '')} {data['main']['temp']}\u00B0C")
                if over.get("show_feels_like", False):
                    text_parts.append(f"{ALL_WEATHER_ICONS.get('wi-thermometer-exterior', '')} {data['main']['feels_like']}\u00B0C")
                if over.get("show_humidity", False):
                    text_parts.append(f"{ALL_WEATHER_ICONS.get('wi-humidity', '')} {data['main']['humidity']}%")
                if over.get("show_windspeed", False) and "wind" in data and "speed" in data["wind"]:
                    text_parts.append(f"{ALL_WEATHER_ICONS.get('wi-wind-default', '')} {data['wind']['speed']} m/s")

                layout_mode = over.get("weather_layout", "inline")
                if layout_mode == "stacked":
                    text_str = "\n".join(text_parts)
                else:
                    text_str = " | ".join(text_parts)

                display_mode = over.get("weather_display_mode", "text_only")
                wfsize = over.get("weather_font_size", 18)
                if display_mode == "icon_only":
                    final_str = f"<span style=\"font-family: 'Weather Icons'; font-size: {wfsize}px;\">{icon_char}</span>"
                elif display_mode == "text_only":
                    final_str = text_str if text_str else ""
                elif display_mode == "icon_and_text":
                    if text_str.strip():
                        final_str = f"<span style=\"font-family: 'Weather Icons'; font-size: {wfsize}px;\">{icon_char}</span>  {text_str}"
                    else:
                        final_str = f"<span style=\"font-family: 'Weather Icons'; font-size: {wfsize}px;\">{icon_char}</span>"
                else:
                    final_str = text_str

                log_message(f"Weather label text: '{final_str}'")

                def update_label():
                    self.weather_label.show()
                    self.weather_label.raise_()
                    self.weather_label.setWordWrap(True)
                    # Set the text as rich text so the HTML styling takes effect.
                    self.weather_label.setText(final_str if final_str.strip() else "(No weather data)")
                    self.setup_layout()
                QTimer.singleShot(0, update_label)
            else:
                def update_error():
                    self.weather_label.show()
                    self.weather_label.raise_()
                    self.weather_label.setText("Weather: error")
                    self.setup_layout()
                QTimer.singleShot(0, update_error)
        except Exception as exc:
            def update_exc():
                self.weather_label.show()
                self.weather_label.raise_()
                self.weather_label.setText("Weather: error")
                self.setup_layout()
            QTimer.singleShot(0, update_exc)
            log_message(f"Error updating weather: {exc}")

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
            auth = SpotifyOAuth(
                client_id=cid,
                client_secret=csec,
                redirect_uri=ruri,
                scope=scope,
                cache_path=".spotify_cache"
            )
            token_info = auth.get_cached_token()
            if not token_info:
                self.spotify_info = None
                return None
            if auth.is_token_expired(token_info):
                token_info = auth.refresh_access_token(token_info["refresh_token"])
            sp = spotipy.Spotify(auth=token_info["access_token"])
            curr = sp.current_playback()
            if not curr or not curr.get("item") or not curr.get("is_playing", False):
                self.spotify_info = None
                return None

            itm = curr["item"]
            track_name = itm.get("name", "")
            artists = ", ".join(a.get("name", "") for a in itm.get("artists", []))
            album_name = itm.get("album", {}).get("name", "")
            self.spotify_info = {
                "song": track_name,
                "artist": artists,
                "album": album_name
            }
            imgs = itm["album"]["images"]
            if not imgs:
                return None
            url = imgs[0]["url"]
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


class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)

        # Load Weather Icons TTF
        font_path = os.path.join(VIEWER_HOME, "static", "weather-icons", "font", "weathericons-regular-webfont.ttf")
        if os.path.exists(font_path):
            fid = QFontDatabase.addApplicationFont(font_path)
            if fid < 0:
                log_message("Failed to load Weather Icons TTF font.")
            else:
                fams = QFontDatabase.applicationFontFamilies(fid)
                log_message(f"Loaded Weather Icons font family: {fams}")
        else:
            log_message(f"Weather Icons TTF not found at: {font_path}")

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
