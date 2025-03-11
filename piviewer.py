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
from datetime import datetime

from PySide6.QtCore import (
    Qt, QTimer, QRect, QSize, QThread, Signal
)
from PySide6.QtGui import (
    QPixmap, QMovie, QFont
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget,
    QVBoxLayout, QGraphicsBlurEffect
)

from spotipy.oauth2 import SpotifyOAuth

from config import APP_VERSION, IMAGE_DIR, LOG_PATH
from utils import (
    load_config, save_config, log_message, get_subfolders,
    get_system_stats
)

##################################
# A separate "display window" class
##################################
class DisplayWindow(QMainWindow):
    def __init__(self, disp_name, disp_cfg):
        super().__init__()
        self.disp_name = disp_name
        self.disp_cfg = disp_cfg
        self.running = True

        # Fullscreen on whichever monitor we like; for now,
        # we just do normal fullscreen on the primary screen. 
        # If you want truly separate monitors, you'd position the window with geometry
        # or use QScreen logic. We'll keep it simple for demonstration.
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.showFullScreen()

        # Container
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0,0,0,0)

        # A background label to show a blurred "filled" version of the image
        self.bg_label = QLabel()
        self.bg_label.setScaledContents(True)
        self.bg_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.bg_label, stretch=1)

        # Optional blur effect
        self.blur_effect = QGraphicsBlurEffect()
        self.blur_effect.setBlurRadius(0)  # user can set
        self.bg_label.setGraphicsEffect(self.blur_effect)

        # A foreground label for the unblurred image
        self.foreground_label = QLabel()
        self.foreground_label.setScaledContents(True)
        self.foreground_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.foreground_label, stretch=1)

        # Overlay text labels (clock, weather)
        self.clock_label = QLabel()
        self.clock_label.setStyleSheet("color: white; font-size: 24px;")
        self.layout.addWidget(self.clock_label, 0, Qt.AlignTop)

        self.weather_label = QLabel()
        self.weather_label.setStyleSheet("color: white; font-size: 18px;")
        self.layout.addWidget(self.weather_label, 0, Qt.AlignTop)

        # Setup timers
        self.slideshow_timer = QTimer()
        self.slideshow_timer.timeout.connect(self.next_image)
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        self.cfg = load_config()
        self.reload_settings()

        # Start slideshow
        self.next_image(force=True)

    def closeEvent(self, event):
        self.running = False
        super().closeEvent(event)

    def reload_settings(self):
        """
        Re-read config from disk, adjust intervals, gather image list, etc.
        """
        self.cfg = load_config()
        # apply overlay config
        over = self.cfg.get("overlay", {})
        blur_radius = 0
        if "bg_opacity" in over:
            # partial alpha for the entire window 
            # only works if you run under a WM that respects it (like picom)
            try:
                alpha_val = float(over["bg_opacity"])
                if alpha_val < 0: alpha_val = 0
                if alpha_val > 1: alpha_val = 1
                self.setWindowOpacity(alpha_val)
            except:
                pass
        if "clock_font_size" in over:
            sz = over["clock_font_size"]
            self.clock_label.setStyleSheet(f"color: {over.get('font_color','#ffffff')}; font-size: {sz}px;")
        if "weather_font_size" in over:
            sz2 = over["weather_font_size"]
            self.weather_label.setStyleSheet(f"color: {over.get('font_color','#ffffff')}; font-size: {sz2}px;")
        if "layout_style" in over:
            # If you wanted stacked vs. inline, you'd rearrange these labels.
            pass
        if "show_desc" in over:
            # We'll handle the actual text in the weather background thread if you want
            pass
        # if user wants a blur background
        # we can define some new config key, e.g. "background_blur_radius"
        # or just reuse the 'rotate' or something. We'll do a separate:
        user_blur = self.cfg.get("gui", {}).get("background_blur_radius", 0)
        self.blur_effect.setBlurRadius(user_blur)

        # re-check the display config for interval
        interval_ms = self.disp_cfg.get("image_interval", 60) * 1000
        self.slideshow_timer.setInterval(interval_ms)
        self.slideshow_timer.start()

        # random/specific/mixed/spotify
        self.current_mode = self.disp_cfg.get("mode","random_image")
        self.image_list = []
        self.index = 0
        if self.current_mode in ("random_image","mixed","specific_image"):
            self.build_local_image_list()

    def build_local_image_list(self):
        mode = self.current_mode
        if mode=="random_image":
            cat = self.disp_cfg.get("image_category","")
            images = self.gather_images(cat)
            if self.disp_cfg.get("shuffle_mode",False):
                random.shuffle(images)
            self.image_list = images
        elif mode=="mixed":
            folder_list = self.disp_cfg.get("mixed_folders",[])
            images = []
            for f in folder_list:
                images += self.gather_images(f)
            if self.disp_cfg.get("shuffle_mode",False):
                random.shuffle(images)
            self.image_list = images
        elif mode=="specific_image":
            cat = self.disp_cfg.get("image_category","")
            spec = self.disp_cfg.get("specific_image","")
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
            if lf.endswith((".jpg",".jpeg",".png",".gif")):
                results.append(os.path.join(base,fname))
        results.sort()
        return results

    def next_image(self, force=False):
        if not self.running:
            return
        mode = self.current_mode
        if mode=="spotify":
            path = self.fetch_spotify_album_art()
            if path:
                self.load_image(path)
            return
        # local images
        if not self.image_list:
            return
        self.index += 1
        if self.index>=len(self.image_list):
            self.index=0
        path = self.image_list[self.index]
        self.load_image(path)

    def load_image(self, fullpath):
        if not os.path.exists(fullpath):
            return
        ext = os.path.splitext(fullpath)[1].lower()
        if ext==".gif":
            movie = QMovie(fullpath)
            self.foreground_label.setMovie(movie)
            movie.start()
            self.bg_label.clear()
        else:
            # load QPixmap
            pm = QPixmap(fullpath)
            self.foreground_label.setMovie(None)
            self.foreground_label.setPixmap(pm)
            # do a 'cover' scale for background
            scaled_bg = self.make_background_cover(pm)
            self.bg_label.setPixmap(scaled_bg)

    def make_background_cover(self, pixmap):
        screen_size = QApplication.primaryScreen().size()
        sw, sh = screen_size.width(), screen_size.height()
        pw, ph = pixmap.width(), pixmap.height()
        screen_ratio = sw / float(sh)
        img_ratio = pw / float(ph)
        if img_ratio > screen_ratio:
            # scale by height
            new_h = sh
            new_w = int(img_ratio * new_h)
        else:
            new_w = sw
            new_h = int(new_w / img_ratio)
        scaled = pixmap.scaled(new_w,new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        xoff = (scaled.width()-sw)//2
        yoff = (scaled.height()-sh)//2
        final = scaled.copy(xoff,yoff, sw, sh)
        return final

    def update_clock(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        self.clock_label.setText(now_str)

    def fetch_spotify_album_art(self):
        try:
            cfg = load_config()
            sp_cfg = cfg.get("spotify",{})
            c_id = sp_cfg.get("client_id","")
            c_sec = sp_cfg.get("client_secret","")
            r_uri = sp_cfg.get("redirect_uri","")
            scope = sp_cfg.get("scope","user-read-currently-playing user-read-playback-state")
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
            if resp.status_code==200:
                tmpf = tempfile.NamedTemporaryFile(delete=False,suffix=".jpg")
                for chunk in resp.iter_content(1024):
                    tmpf.write(chunk)
                tmpf.close()
                return tmpf.name
        except Exception as e:
            log_message(f"Spotify error: {e}")
            return None
        return None


#########################################
# The main manager that spawns a window per monitor
#########################################

class PiViewerGUI:
    def __init__(self):
        self.cfg = load_config()
        self.app = QApplication(sys.argv)
        self.windows = []
        # For each display in cfg["displays"], create a window
        for dname,dcfg in self.cfg.get("displays",{}).items():
            w = DisplayWindow(dname, dcfg)
            w.show()
            self.windows.append(w)

        # Start background thread to periodically reload config 
        # so we can respond to new settings from the web UI
        self.reload_thread = threading.Thread(target=self.reload_loop, daemon=True)
        self.reload_thread.start()

    def reload_loop(self):
        while True:
            time.sleep(10)  # check every 10s
            new_cfg = load_config()
            # if anything changed in new_cfg, apply it
            # for each window, re-assign the appropriate display config
            for dname,dcfg in new_cfg.get("displays",{}).items():
                for w in self.windows:
                    if w.disp_name==dname:
                        w.disp_cfg=dcfg
                        w.reload_settings()

    def run(self):
        sys.exit(self.app.exec())


def main():
    log_message(f"Starting PiViewer GUI (v{APP_VERSION}).")
    gui = PiViewerGUI()
    gui.run()


if __name__=="__main__":
    main()
