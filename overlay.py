#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
overlay.py
----------
A simple always-on-top overlay that shows the current time
and (optionally) weather info. We now allow user adjustments
(via 'overlay' dict in viewerconfig.json) for:
 - overlay position (offset_x, offset_y)
 - background color
 - alpha transparency
 - lat/lon & API key for weather
Requires a compositing WM (e.g. picom) for real transparency.
"""

import os
import sys
import time
import json
import requests
import threading
import tkinter as tk
from datetime import datetime

from utils import load_config, log_message

REFRESH_INTERVAL_SEC = 300   # how often to refresh weather data
FONT_SIZE = 26


class OverlayApp:
    def __init__(self, root, overlay_cfg):
        self.root = root
        self.root.title("Clock & Weather Overlay")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        # Pull user settings
        self.bg_opacity = overlay_cfg.get("bg_opacity", 0.4)
        self.bg_color = overlay_cfg.get("bg_color", "#000000")
        offset_x = overlay_cfg.get("offset_x", 20)
        offset_y = overlay_cfg.get("offset_y", 20)

        # Set geometry for offset
        self.root.geometry(f"+{offset_x}+{offset_y}")

        # Set alpha transparency
        self.root.attributes("-alpha", self.bg_opacity)

        # Also set root window's background color, so no white border
        self.root.configure(bg=self.bg_color)

        # Create a frame (borderless)
        self.frame = tk.Frame(self.root, bg=self.bg_color, highlightthickness=0, bd=0)
        self.frame.pack(padx=10, pady=10)

        # Clock label
        self.time_label = tk.Label(
            self.frame,
            text="",
            fg="white",
            bg=self.bg_color,
            font=("Trebuchet MS", FONT_SIZE, "bold")
        )
        self.time_label.pack(anchor="e")

        # Weather label
        self.weather_label = tk.Label(
            self.frame,
            text="",
            fg="white",
            bg=self.bg_color,
            font=("Trebuchet MS", FONT_SIZE - 4)
        )
        self.weather_label.pack(anchor="e")

        # Always update clock
        self.update_time()

        # Check if weather is enabled
        self.weather_enabled = overlay_cfg.get("weather_enabled", False)
        if self.weather_enabled:
            self.api_key = overlay_cfg.get("api_key", "").strip()
            self.lat = overlay_cfg.get("lat")
            self.lon = overlay_cfg.get("lon")
            self.weather_info = None
            if self.api_key and self.lat is not None and self.lon is not None:
                threading.Thread(target=self.update_weather_loop, daemon=True).start()

    def update_time(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.time_label.config(text=now)
        self.root.after(1000, self.update_time)

    def update_weather_loop(self):
        while True:
            try:
                self.fetch_weather()
                self.update_weather_label()
            except Exception as e:
                log_message(f"Overlay Weather Error: {e}")
                self.weather_label.config(text="(Weather Error)")
            time.sleep(REFRESH_INTERVAL_SEC)

    def fetch_weather(self):
        # Use free OWM format with lat/lon
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={self.lat}&lon={self.lon}"
            f"&appid={self.api_key}&units=metric"
        )
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            self.weather_info = resp.json()
        else:
            self.weather_info = None

    def update_weather_label(self):
        if not self.weather_info:
            self.weather_label.config(text="(No data)")
            return
        main = self.weather_info.get("weather", [{}])[0].get("main", "??")
        temp = self.weather_info.get("main", {}).get("temp", "?")
        text = f"{main}, {temp}°C"
        self.weather_label.config(text=text)


def main():
    cfg = load_config()
    overlay_cfg = cfg.get("overlay", {})
    root = tk.Tk()
    app = OverlayApp(root, overlay_cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
