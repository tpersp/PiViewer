#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
overlay.py
----------
A simple always-on-top overlay that shows the current time
and (optionally) weather info. Now with:
 - monitor-based offsets (if user selects 1 monitor)
 - auto-sizing (to remove extra blank space around text)
 - partial alpha transparency
Requires a compositing WM (like picom) for real transparency.
"""

import os
import sys
import time
import json
import requests
import threading
import tkinter as tk
from datetime import datetime

from utils import load_config, log_message, detect_monitors

REFRESH_INTERVAL_SEC = 300   # how often to refresh weather data
FONT_SIZE = 26


class OverlayApp:
    def __init__(self, root, overlay_cfg):
        self.root = root
        self.root.title("Clock & Weather Overlay")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        # Basic user settings
        self.bg_opacity = overlay_cfg.get("bg_opacity", 0.4)
        self.bg_color = overlay_cfg.get("bg_color", "#000000")
        self.user_offset_x = overlay_cfg.get("offset_x", 20)
        self.user_offset_y = overlay_cfg.get("offset_y", 20)
        self.monitor_selection = overlay_cfg.get("monitor_selection", "All")
        self.weather_enabled = overlay_cfg.get("weather_enabled", False)
        self.api_key = overlay_cfg.get("api_key", "").strip()
        self.lat = overlay_cfg.get("lat")
        self.lon = overlay_cfg.get("lon")

        # We'll do auto-sizing to remove extra blank space. So initially, we set small geometry.
        # We'll place it properly after measuring content.
        self.root.geometry("1x1+0+0")

        # alpha transparency (needs picom or similar)
        self.root.attributes("-alpha", self.bg_opacity)

        # Make sure no border color is shown
        self.root.configure(bg=self.bg_color)

        # Build the main frame
        self.frame = tk.Frame(self.root, bg=self.bg_color, highlightthickness=0, bd=0)
        self.frame.pack(fill=tk.BOTH, expand=True)

        # Clock label
        self.time_label = tk.Label(
            self.frame,
            text="",
            fg="white",
            bg=self.bg_color,
            font=("Trebuchet MS", FONT_SIZE, "bold")
        )
        # Anchor to NW so there's minimal left margin
        self.time_label.pack(anchor="nw", padx=8, pady=(6, 0))

        # Weather label
        self.weather_label = tk.Label(
            self.frame,
            text="",
            fg="white",
            bg=self.bg_color,
            font=("Trebuchet MS", FONT_SIZE - 4)
        )
        # Pack below time label
        self.weather_label.pack(anchor="nw", padx=8, pady=(0, 4))

        # Start updating clock
        self.update_time()

        # If weather is enabled, start the fetch loop
        if self.weather_enabled and self.api_key and (self.lat is not None) and (self.lon is not None):
            threading.Thread(target=self.update_weather_loop, daemon=True).start()

        # After building, do an update to measure
        self.root.update_idletasks()
        # Now we know how big the content is:
        w = self.frame.winfo_reqwidth()
        h = self.frame.winfo_reqheight()

        # If user picked a single monitor, retrieve offset from detect_monitors
        # else 0,0 for "All"
        mon_offset_x = 0
        mon_offset_y = 0
        if self.monitor_selection != "All":
            # see if it exists in the dict
            monitors = detect_monitors()
            if self.monitor_selection in monitors:
                mon_offset_x = monitors[self.monitor_selection].get("offset_x", 0)
                mon_offset_y = monitors[self.monitor_selection].get("offset_y", 0)

        # Final position
        final_x = mon_offset_x + self.user_offset_x
        final_y = mon_offset_y + self.user_offset_y

        # apply geometry
        self.root.geometry(f"{w}x{h}+{final_x}+{final_y}")

    def update_time(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.time_label.config(text=now)
        self.root.after(1000, self.update_time)

    def update_weather_loop(self):
        while True:
            try:
                self.fetch_weather()
                self.refresh_weather_label()
            except Exception as e:
                log_message(f"Overlay Weather Error: {e}")
                self.weather_label.config(text="(Weather Error)")
            time.sleep(REFRESH_INTERVAL_SEC)

    def fetch_weather(self):
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

    def refresh_weather_label(self):
        if not self.weather_info:
            self.weather_label.config(text="(No data)")
            return
        main = self.weather_info.get("weather", [{}])[0].get("main", "??")
        temp = self.weather_info.get("main", {}).get("temp", "?")
        self.weather_label.config(text=f"{main}, {temp}°C")


def main():
    cfg = load_config()
    over = cfg.get("overlay", {})
    root = tk.Tk()
    app = OverlayApp(root, over)
    root.mainloop()

if __name__ == "__main__":
    main()
