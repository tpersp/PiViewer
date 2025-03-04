#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
overlay.py
----------
A simple always-on-top overlay that shows the current time
and (optionally) weather info, with user-settable:
 - offsets + monitor selection
 - background color & transparency
 - clock & weather font sizes
 - layout: stacked or inline
 - padding
Requires a compositing WM (like picom) for real alpha transparency.
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

REFRESH_INTERVAL_SEC = 300

class OverlayApp:
    def __init__(self, root, overlay_cfg):
        self.root = root
        self.root.title("Clock & Weather Overlay")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)

        # Pull user settings
        self.bg_opacity   = overlay_cfg.get("bg_opacity", 0.4)
        self.bg_color     = overlay_cfg.get("bg_color", "#000000")
        self.user_offset_x= overlay_cfg.get("offset_x", 20)
        self.user_offset_y= overlay_cfg.get("offset_y", 20)
        self.win_w        = overlay_cfg.get("overlay_width", 300)
        self.win_h        = overlay_cfg.get("overlay_height", 150)
        self.monitor_sel  = overlay_cfg.get("monitor_selection", "All")
        self.weather_en   = overlay_cfg.get("weather_enabled", False)
        self.api_key      = overlay_cfg.get("api_key", "").strip()
        self.lat          = overlay_cfg.get("lat")
        self.lon          = overlay_cfg.get("lon")
        # new advanced text settings
        self.clock_font   = overlay_cfg.get("clock_font_size", 26)
        self.weather_font = overlay_cfg.get("weather_font_size", 22)
        self.layout_style = overlay_cfg.get("layout_style", "stacked")  # "inline" or "stacked"
        self.pad_x        = overlay_cfg.get("padding_x", 8)
        self.pad_y        = overlay_cfg.get("padding_y", 6)

        # Start with a minimal geometry
        self.root.geometry("1x1+0+0")
        self.root.attributes("-alpha", self.bg_opacity)
        self.root.configure(bg=self.bg_color)

        # Build frame
        self.frame = tk.Frame(self.root, bg=self.bg_color, highlightthickness=0, bd=0)
        self.frame.pack(fill=tk.BOTH, expand=True)

        # If layout_style == "inline", we combine clock & weather in a single label
        if self.layout_style == "inline":
            self.inline_label = tk.Label(
                self.frame,
                text="",
                fg="white",
                bg=self.bg_color,
                font=("Trebuchet MS", self.clock_font)  # we'll unify font
            )
            self.inline_label.pack(anchor="nw", padx=self.pad_x, pady=self.pad_y)
        else:
            # stacked
            self.clock_label = tk.Label(
                self.frame,
                text="",
                fg="white",
                bg=self.bg_color,
                font=("Trebuchet MS", self.clock_font, "bold")
            )
            self.clock_label.pack(anchor="nw", padx=self.pad_x, pady=(self.pad_y, 0))

            self.weather_label = tk.Label(
                self.frame,
                text="",
                fg="white",
                bg=self.bg_color,
                font=("Trebuchet MS", self.weather_font)
            )
            self.weather_label.pack(anchor="nw", padx=self.pad_x, pady=(0, self.pad_y))

        self.update_time()

        if self.weather_en and self.api_key and (self.lat is not None) and (self.lon is not None):
            self.weather_info = None
            threading.Thread(target=self.update_weather_loop, daemon=True).start()

        # measure content
        self.root.update_idletasks()
        w_req = self.frame.winfo_reqwidth()
        h_req = self.frame.winfo_reqheight()

        # If user picked a single monitor, shift by that monitor's offset
        mon_offset_x = 0
        mon_offset_y = 0
        if self.monitor_sel != "All":
            mon_dict = detect_monitors()
            if self.monitor_sel in mon_dict:
                mon_offset_x = mon_dict[self.monitor_sel].get("offset_x", 0)
                mon_offset_y = mon_dict[self.monitor_sel].get("offset_y", 0)

        final_x = mon_offset_x + self.user_offset_x
        final_y = mon_offset_y + self.user_offset_y

        # If the user specified overlay_width/height, respect them only if they're bigger than content
        # or else we can clamp. We'll let the user explicitly override if they want a larger box
        # even if the text doesn't fill it.
        final_w = max(w_req, self.win_w)
        final_h = max(h_req, self.win_h)

        self.root.geometry(f"{final_w}x{final_h}+{final_x}+{final_y}")

    def update_time(self):
        now_str = datetime.now().strftime("%H:%M:%S")
        if self.layout_style == "inline":
            # If inline, also show weather in same line
            # if weather_info is available
            if hasattr(self, "weather_info") and self.weather_info:
                main_desc = self.weather_info.get("weather", [{}])[0].get("main", "??")
                temp_val  = self.weather_info.get("main", {}).get("temp", "?")
                weather_str = f"{main_desc}, {temp_val}°C"
                display_str = f"{now_str}   {weather_str}"
            else:
                # no weather yet
                display_str = now_str
            self.inline_label.config(text=display_str)
        else:
            # stacked
            self.clock_label.config(text=now_str)
        self.root.after(1000, self.update_time)

    def update_weather_loop(self):
        import time
        while True:
            try:
                self.fetch_weather()
                if self.layout_style == "stacked":
                    self.update_stacked_weather()
                # if inline, the inline label is updated next time clock ticks
            except Exception as ex:
                log_message(f"Overlay Weather Error: {ex}")
                if self.layout_style == "stacked":
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

    def update_stacked_weather(self):
        if not self.weather_info:
            self.weather_label.config(text="(No data)")
            return
        main_desc = self.weather_info.get("weather", [{}])[0].get("main", "??")
        temp_val  = self.weather_info.get("main", {}).get("temp", "?")
        self.weather_label.config(text=f"{main_desc}, {temp_val}°C")


def main():
    cfg = load_config()
    overlay = cfg.get("overlay", {})
    root = tk.Tk()
    app = OverlayApp(root, overlay)
    root.mainloop()

if __name__ == "__main__":
    main()
