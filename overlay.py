#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
overlay.py
----------
A simple always-on-top overlay that shows a clock and (optionally) weather info,
with user-settable toggles for overlay, clock, and background, plus layout, font,
and weather data preferences. Requires a compositing WM (like picom) for real alpha.
"""

import os
import sys
import time
import requests
import threading
import tkinter as tk
from datetime import datetime

from utils import load_config, log_message, detect_monitors

REFRESH_INTERVAL_SEC = 60

class OverlayApp:
    def __init__(self, root, overlay_cfg, weather_cfg):
        self.root = root
        self.root.title("Clock & Weather Overlay")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        # Pull user settings
        self.overlay_enabled    = overlay_cfg.get("overlay_enabled", True)
        self.clock_enabled      = overlay_cfg.get("clock_enabled", True)
        self.weather_enabled    = overlay_cfg.get("weather_enabled", False)
        self.bg_enabled         = overlay_cfg.get("background_enabled", True)

        # If overlay is disabled, we just exit immediately
        if not self.overlay_enabled:
            log_message("Overlay is disabled by config; exiting overlay process.")
            self.root.destroy()
            sys.exit(0)

        # Safe fallback for the user’s color choices
        raw_bg = overlay_cfg.get("bg_color", "#000000") or "#000000"
        raw_fg = overlay_cfg.get("font_color", "#FFFFFF") or "#FFFFFF"

        # If user turned off background box, pass None instead of an empty string
        if self.bg_enabled:
            self.bg_color = raw_bg
            # If user gave an invalid or empty color, default to "#000000"
            if not self.bg_color.strip():
                self.bg_color = "#000000"

            # Opacity only matters if bg_enabled
            try:
                self.bg_opacity = float(overlay_cfg.get("bg_opacity", 0.4))
            except:
                self.bg_opacity = 0.4
        else:
            self.bg_color    = None  # means "use system default" for labels/frame
            self.bg_opacity  = 0.0   # fully transparent
       
        self.font_color     = raw_fg.strip() if raw_fg.strip() else "#FFFFFF"
        self.clock_font     = overlay_cfg.get("clock_font_size", 26)
        self.weather_font   = overlay_cfg.get("weather_font_size", 22)
        self.layout_style   = overlay_cfg.get("layout_style", "stacked")
        self.pad_x          = overlay_cfg.get("padding_x", 8)
        self.pad_y          = overlay_cfg.get("padding_y", 6)

        # Which weather details to show
        self.show_desc       = overlay_cfg.get("show_desc", True)
        self.show_temp       = overlay_cfg.get("show_temp", True)
        self.show_feels_like = overlay_cfg.get("show_feels_like", False)
        self.show_humidity   = overlay_cfg.get("show_humidity", False)

        # geometry / position
        self.user_offset_x = overlay_cfg.get("offset_x", 20)
        self.user_offset_y = overlay_cfg.get("offset_y", 20)
        self.user_width    = overlay_cfg.get("overlay_width", 300)
        self.user_height   = overlay_cfg.get("overlay_height", 150)

        # Monitor selection
        self.monitor_sel  = overlay_cfg.get("monitor_selection", "All")

        # Weather config from separate dict
        self.api_key   = weather_cfg.get("api_key", "").strip()
        self.lat       = weather_cfg.get("lat", None)
        self.lon       = weather_cfg.get("lon", None)

        # Build minimal window geometry, set alpha if background is enabled
        self.root.geometry("1x1+0+0")
        self.root.attributes("-alpha", self.bg_opacity)

        # Container frame
        # (If bg_color is None, we rely on system default instead of passing "")
        self.frame = tk.Frame(
            self.root,
            bg=(self.bg_color if self.bg_color else None),
            highlightthickness=0,
            bd=0
        )
        self.frame.pack(fill=tk.BOTH, expand=True)

        # Create labels depending on layout
        self.inline_label = None
        self.clock_label  = None
        self.weather_label= None

        if self.layout_style == "inline":
            self.inline_label = tk.Label(
                self.frame,
                text="",
                fg=self.font_color,
                bg=(self.bg_color if self.bg_color else None),
                font=("Trebuchet MS", max(self.clock_font, self.weather_font))
            )
            self.inline_label.pack(anchor="nw", padx=self.pad_x, pady=self.pad_y)
        else:
            # stacked style
            if self.clock_enabled:
                self.clock_label = tk.Label(
                    self.frame,
                    text="",
                    fg=self.font_color,
                    bg=(self.bg_color if self.bg_color else None),
                    font=("Trebuchet MS", self.clock_font, "bold")
                )
                self.clock_label.pack(anchor="nw", padx=self.pad_x, pady=(self.pad_y, 0))

            if self.weather_enabled:
                self.weather_label = tk.Label(
                    self.frame,
                    text="",
                    fg=self.font_color,
                    bg=(self.bg_color if self.bg_color else None),
                    font=("Trebuchet MS", self.weather_font)
                )
                self.weather_label.pack(anchor="nw", padx=self.pad_x, pady=(0, self.pad_y))

        # Start updating clock if enabled
        if self.clock_enabled:
            self.update_time()

        # Start weather thread if enabled
        if self.weather_enabled and self.api_key and (self.lat is not None) and (self.lon is not None):
            self.weather_info = {}
            threading.Thread(target=self.update_weather_loop, daemon=True).start()

        # measure content
        self.root.update_idletasks()
        w_req = self.frame.winfo_reqwidth()
        h_req = self.frame.winfo_reqheight()

        # offset from chosen monitor
        mon_offset_x, mon_offset_y = 0, 0
        mon_dict = detect_monitors()
        if self.monitor_sel != "All" and self.monitor_sel in mon_dict:
            mon_offset_x = mon_dict[self.monitor_sel].get("offset_x", 0)
            mon_offset_y = mon_dict[self.monitor_sel].get("offset_y", 0)

        final_x = mon_offset_x + self.user_offset_x
        final_y = mon_offset_y + self.user_offset_y

        # If user_width or user_height <= 0, treat as "auto" from content
        final_w = w_req if (self.user_width <= 0) else max(w_req, self.user_width)
        final_h = h_req if (self.user_height <= 0) else max(h_req, self.user_height)

        self.root.geometry(f"{final_w}x{final_h}+{final_x}+{final_y}")

    def update_time(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        if self.layout_style == "inline" and self.inline_label is not None:
            clock_part = now_str if self.clock_enabled else ""
            weather_part = ""
            if self.weather_enabled and hasattr(self, "weather_info"):
                weather_part = self.build_weather_string()
            display_str = clock_part
            if clock_part and weather_part:
                display_str += "   " + weather_part
            elif weather_part:
                display_str = weather_part
            self.inline_label.config(text=display_str)
        else:
            if self.clock_label:
                self.clock_label.config(text=now_str)

        # update every second
        self.root.after(1000, self.update_time)

    def build_weather_string(self):
        """Construct the weather text based on user-chosen fields."""
        if not self.weather_info:
            return ""
        parts = []
        if self.show_desc:
            desc = self.weather_info.get("weather", [{}])[0].get("main", "?")
            parts.append(desc)
        if self.show_temp:
            t = self.weather_info.get("main", {}).get("temp", "?")
            parts.append(f"{t}°C")
        if self.show_feels_like:
            fl = self.weather_info.get("main", {}).get("feels_like", "?")
            parts.append(f"Feels:{fl}°C")
        if self.show_humidity:
            h = self.weather_info.get("main", {}).get("humidity", "?")
            parts.append(f"Hum:{h}%")

        return ", ".join(str(x) for x in parts if x)

    def update_weather_loop(self):
        while True:
            try:
                self.fetch_weather()
                if self.layout_style == "stacked" and self.weather_label:
                    self.weather_label.config(text=self.build_weather_string())
                # if inline layout, the next clock tick refreshes inline_label
            except Exception as ex:
                log_message(f"Overlay Weather Error: {ex}")
                if self.layout_style == "stacked" and self.weather_label:
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
            self.weather_info = {}

def main():
    cfg = load_config()
    overlay_cfg = cfg.get("overlay", {})
    weather_cfg = cfg.get("weather", {})
    root = tk.Tk()
    app = OverlayApp(root, overlay_cfg, weather_cfg)
    root.mainloop()

if __name__ == "__main__":
    main()
