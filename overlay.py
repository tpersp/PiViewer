#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
overlay.py
----------
A simple always-on-top overlay that shows the current time
and (optionally) weather info. Designed to run under a 
compositing window manager (e.g., picom + openbox).
"""

import os
import sys
import time
import json
import requests
import threading
import tkinter as tk
from datetime import datetime

# Basic config approach:
# If you want to store a weather API key & city, you can store them in a small JSON
# or read them from environment variables. For demonstration, we’ll do them inline:
WEATHER_API_KEY = ""    # Insert your OpenWeatherMap API key here (if desired)
WEATHER_CITY_ID = ""    # e.g., "2643743" for London, or an empty string to skip

REFRESH_INTERVAL_SEC = 300  # how often to refresh weather data
FONT_SIZE = 26

# For partial transparency, picom must be running. We can specify a color to treat as
# "transparent" or rely on alpha. We'll use a semi-opaque approach for readability.
BG_COLOR = "#000000"  # black background
BG_OPACITY = 0.4      # 40% opaque if compositing is enabled

class OverlayApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clock & Weather Overlay")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", BG_OPACITY)
        self.root.overrideredirect(True)

        # Position in the top-right corner by default
        # (You can adjust geometry if you prefer a different location)
        self.root.geometry("+1600+20")  

        # A frame to hold time label and weather label
        self.frame = tk.Frame(self.root, bg=BG_COLOR)
        self.frame.pack(padx=10, pady=10)

        self.time_label = tk.Label(self.frame, text="", fg="white", bg=BG_COLOR,
                                   font=("Trebuchet MS", FONT_SIZE, "bold"))
        self.time_label.pack(anchor="e")

        self.weather_label = tk.Label(self.frame, text="", fg="white", bg=BG_COLOR,
                                      font=("Trebuchet MS", FONT_SIZE - 4))
        self.weather_label.pack(anchor="e")

        self.update_time()
        if WEATHER_API_KEY and WEATHER_CITY_ID:
            self.weather_info = None
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
                self.weather_label.config(text="(Weather Error)")
            time.sleep(REFRESH_INTERVAL_SEC)

    def fetch_weather(self):
        # Example using OpenWeatherMap "Current Weather Data" with city ID & API key
        url = f"http://api.openweathermap.org/data/2.5/weather?id={WEATHER_CITY_ID}&appid={WEATHER_API_KEY}&units=metric"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            self.weather_info = resp.json()
        else:
            self.weather_info = None

    def update_weather_label(self):
        if not self.weather_info:
            self.weather_label.config(text="(No data)")
            return
        # Basic example: show temp and condition
        main = self.weather_info.get("weather", [{}])[0].get("main", "??")
        temp = self.weather_info.get("main", {}).get("temp", "?")
        text = f"{main}, {temp}°C"
        self.weather_label.config(text=text)

if __name__ == "__main__":
    root = tk.Tk()
    app = OverlayApp(root)
    root.mainloop()
