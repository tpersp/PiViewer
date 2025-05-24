#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
import requests
from flask import (
    Blueprint, request, redirect, url_for, render_template,
    send_from_directory, send_file, jsonify
)
from config import APP_VERSION, WEB_BG, IMAGE_DIR, LOG_PATH, UPDATE_BRANCH, VIEWER_HOME
from utils import (
    load_config, save_config, init_config, log_message,
    get_system_stats, get_subfolders, count_files_in_folder,
    get_remote_config, get_remote_monitors,
    pull_displays_from_remote, push_displays_to_remote,
    get_hostname, get_ip_address, get_pi_model,
    CONFIG_PATH
)

# ─────────────────────────────────────────────────────────────────────────────
#  Monitor utilities
# ─────────────────────────────────────────────────────────────────────────────
def detect_monitors_extended():
    """
    Calls xrandr --props to find connected monitors, their preferred/current
    resolution plus a list of possible modes, plus a 'monitor name' from EDID
    if available.  We do NOT use these to change resolution.
    """
    result = {}
    try:
        xout = subprocess.check_output(
            ["xrandr", "--props"], stderr=subprocess.STDOUT
        ).decode("utf-8", "ignore")
    except Exception as e:
        log_message(f"Monitor detection error: {e}")
        return {}

    current_monitor = None
    for line in xout.splitlines():
        line = line.strip()
        if " connected " in line:
            parts = line.split()
            name = parts[0]
            if "connected" in line:
                current_monitor = name
                result[current_monitor] = {
                    "model": None,
                    "connected": True,
                    "current_mode": None,
                    "modes": [],
                }
                for p in parts:
                    if "x" in p and "+" in p:
                        result[current_monitor]["current_mode"] = p.split("+")[0]
                        break

        elif current_monitor and "Monitor name:" in line:
            idx = line.find("Monitor name:")
            name_str = line[idx + len("Monitor name:") :].strip()
            if name_str:
                result[current_monitor]["model"] = name_str

        elif current_monitor:
            tokens = line.split()
            if tokens:
                mode_candidate = tokens[0]
                if "x" in mode_candidate and mode_candidate[0].isdigit():
                    if mode_candidate not in result[current_monitor]["modes"]:
                        result[current_monitor]["modes"].append(mode_candidate)

    return result


def get_local_monitors_from_config(cfg):
    """
    Return a dict for referencing each monitor's resolution in overlays, etc.
    """
    out = {}
    for dname, dcfg in cfg.get("displays", {}).items():
        chosen = dcfg.get("chosen_mode")
        if chosen:
            out[dname] = {"resolution": chosen}
        else:
            sn = dcfg.get("screen_name", "")
            if sn and ":" in sn:
                part = sn.split(":")[-1].strip()
                out[dname] = {"resolution": part if "x" in part else "?"}
            else:
                out[dname] = {"resolution": "?"}
    return out


def compute_overlay_preview(overlay_cfg, monitors_dict):
    """
    Used for overlay preview only.  Ignores manual sizing settings (removed) and
    computes a preview overlay box automatically.
    """
    selection = overlay_cfg.get("monitor_selection", "All")
    if selection == "All":
        maxw, maxh = 0, 0
        for minfo in monitors_dict.values():
            try:
                w_str, h_str = minfo["resolution"].split("x")
                w, h = int(w_str), int(h_str)
                maxw = max(maxw, w)
                maxh = max(maxh, h)
            except Exception:
                pass
        total_w, total_h = (maxw or 1920), (maxh or 1080)
    else:
        if selection in monitors_dict:
            try:
                part = monitors_dict[selection]["resolution"]
                w_str, h_str = part.split("x")
                total_w, total_h = int(w_str), int(h_str)
            except Exception:
                total_w, total_h = 1920, 1080
        else:
            total_w, total_h = 1920, 1080

    max_preview_w = 400
    scale_factor = float(max_preview_w) / float(total_w) if total_w else 0.2
    preview_width = int(total_w * scale_factor)
    preview_height = int(total_h * scale_factor)

    # fixed-proportion overlay preview box
    preview_overlay = {
        "width": int(preview_width * 0.3),
        "height": int(preview_height * 0.2),
        "left": int(preview_width * 0.05),
        "top": int(preview_height * 0.05),
    }
    return preview_width, preview_height, preview_overlay


# ─────────────────────────────────────────────────────────────────────────────
#  Blueprint
# ─────────────────────────────────────────────────────────────────────────────
main_bp = Blueprint("main", __name__, static_folder="static")

# ─────────────────────────────────────────────────────────────────────────────
#  Simple utility routes (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/stats")
def stats_json():
    cpu, mem_mb, load1, temp = get_system_stats()
    return jsonify(
        {
            "cpu_percent": cpu,
            "mem_used_mb": round(mem_mb, 1),
            "load_1min": round(load1, 2),
            "temp": temp,
        }
    )


@main_bp.route("/list_monitors")
def list_monitors():
    # basic dummy for JS polling
    return jsonify({"Display0": {"resolution": "1920x1080", "offset_x": 0, "offset_y": 0}})


@main_bp.route("/list_folders")
def list_folders():
    return jsonify(get_subfolders())


@main_bp.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGE_DIR, filename)


@main_bp.route("/bg_image")
def bg_image():
    if os.path.exists(WEB_BG):
        return send_file(WEB_BG)
    return "", 404


@main_bp.route("/download_log")
def download_log():
    if os.path.exists(LOG_PATH):
        return send_file(LOG_PATH, as_attachment=True)
    return "No log file found", 404


# ─────────────────────────────────────────────────────────────────────────────
#  Upload & control helpers (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/upload_media", methods=["GET", "POST"])
def upload_media():
    cfg = load_config()
    theme = cfg.get("theme", "dark")
    subfolders = get_subfolders()
    if request.method == "GET":
        return render_template("upload_media.html", theme=theme, subfolders=subfolders)

    files = request.files.getlist("mediafiles")
    if not files:
        return "No files selected", 400

    subfolder = request.form.get("subfolder", "")
    new_subfolder = request.form.get("new_subfolder", "").strip()
    if new_subfolder:
        subfolder = new_subfolder

    target_dir = os.path.join(IMAGE_DIR, subfolder)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    for f in files:
        if not f.filename:
            continue
        lf = f.filename.lower()
        if not lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
            log_message(f"Unsupported file type: {f.filename}")
            continue
        final_path = os.path.join(target_dir, f.filename)
        f.save(final_path)
        log_message(f"Uploaded file: {final_path}")

    return redirect(url_for("main.index"))


@main_bp.route("/restart_viewer", methods=["POST"])
def restart_viewer():
    try:
        subprocess.check_output(["sudo", "systemctl", "restart", "piviewer.service"])
        return redirect(url_for("main.index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart service: {e}", 500


@main_bp.route("/restart_device", methods=["POST"])
def restart_device():
    try:
        subprocess.check_output(["sudo", "reboot"])
        return redirect(url_for("main.index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart device: {e}", 500


@main_bp.route("/power_off", methods=["POST"])
def power_off():
    cfg = load_config()
    theme = cfg.get("theme", "dark")
    if theme == "dark":
        page_bg, text_color, button_bg, button_color, link_hover_bg = (
            "#121212",
            "#ECECEC",
            "#444",
            "#FFF",
            "#666",
        )
    else:
        page_bg, text_color, button_bg, button_color, link_hover_bg = (
            "#FFFFFF",
            "#222",
            "#ddd",
            "#111",
            "#bbb",
        )
    try:
        subprocess.check_output(["sudo", "poweroff"])
    except subprocess.CalledProcessError as e:
        return f"Failed to power off: {e}", 500
    return f"""
    <html>
      <head><meta charset="utf-8"/><title>Device Power Off</title>
      <style>
        body {{
          background-color:{page_bg};color:{text_color};font-family:Arial,sans-serif;
          text-align:center;margin-top:50px;
        }}
        a.button {{
          display:inline-block;margin-top:20px;padding:10px 20px;
          background-color:{button_bg};color:{button_color};
          border:none;border-radius:6px;text-decoration:none;cursor:pointer;
        }}
        a.button:hover{{background-color:{link_hover_bg};}}
      </style></head>
      <body><h2>Device is powering off...</h2><p>Please wait for the device to shut down.</p></body>
    </html>
    """


# ─────────────────────────────────────────────────────────────────────────────
#  Settings & overlay pages (unchanged)
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if "weather" not in cfg:
        cfg["weather"] = {}
    if request.method == "POST":
        new_theme = request.form.get("theme", "dark")
        new_role = request.form.get("role", "main")
        cfg["theme"], cfg["role"] = new_theme, new_role

        cfg["main_ip"] = request.form.get("main_ip", "").strip() if new_role == "sub" else ""

        if new_theme == "custom" and "bg_image" in request.files:
            f = request.files["bg_image"]
            if f and f.filename:
                f.save(WEB_BG)

        # weather settings
        w_api = request.form.get("weather_api_key", "").strip()
        w_zip = request.form.get("weather_zip_code", "").strip()
        w_cc = request.form.get("weather_country_code", "").strip()
        if w_api and w_zip and w_cc:
            try:
                requests.get(
                    f"http://api.openweathermap.org/data/2.5/weather?zip={w_zip},{w_cc}&appid={w_api}&units=metric",
                    timeout=5,
                )
            except Exception:
                pass
        cfg["weather"].update({"api_key": w_api, "zip_code": w_zip, "country_code": w_cc})

        if "gui" not in cfg:
            cfg["gui"] = {}
        for gkey in (
            ("background_blur_radius", 20),
            ("background_scale_percent", 100),
            ("foreground_scale_percent", 100),
        ):
            try:
                cfg["gui"][gkey[0]] = int(request.form.get(gkey[0], str(gkey[1])))
            except Exception:
                cfg["gui"][gkey[0]] = gkey[1]

        save_config(cfg)
        return redirect(url_for("main.settings"))

    # GET
    theme = cfg.get("theme", "dark")
    weather_info = None
    w_api = cfg.get("weather", {}).get("api_key", "").strip()
    w_zip = cfg.get("weather", {}).get("zip_code", "").strip()
    w_cc = cfg.get("weather", {}).get("country_code", "").strip()
    if w_api and w_zip and w_cc:
        try:
            r = requests.get(
                f"http://api.openweathermap.org/data/2.5/weather?zip={w_zip},{w_cc}&appid={w_api}&units=metric",
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                weather_info = {
                    "name": data.get("name", "Unknown"),
                    "timezone": data.get("timezone", "Unknown"),
                    "country": data.get("sys", {}).get("country", "Unknown"),
                }
        except Exception:
            weather_info = None

    return render_template(
        "settings.html",
        theme=theme,
        cfg=cfg,
        update_branch=UPDATE_BRANCH,
        weather_info=weather_info,
        version=APP_VERSION,
    )


@main_bp.route("/clear_config", methods=["POST"])
def clear_config():
    """Delete viewerconfig.json, re-init defaults, restart service."""
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
        log_message("viewerconfig.json deleted – re-initialising.")
    init_config()
    try:
        subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart after clear_config: {e}")
    return redirect(url_for("main.settings"))


# configure_spotify, spotify_auth, callback – unchanged
@main_bp.route("/configure_spotify", methods=["GET", "POST"])
def configure_spotify():
    cfg = load_config()
    if "spotify" not in cfg:
        cfg["spotify"] = {}
    if request.method == "POST":
        cfg["spotify"] = {
            "client_id": request.form.get("client_id", "").strip(),
            "client_secret": request.form.get("client_secret", "").strip(),
            "redirect_uri": request.form.get("redirect_uri", "").strip(),
            "scope": request.form.get(
                "scope", "user-read-currently-playing user-read-playback-state"
            ).strip(),
        }
        save_config(cfg)
        return redirect(url_for("main.configure_spotify"))
    return render_template(
        "configure_spotify.html",
        spotify=cfg["spotify"],
        theme=cfg.get("theme", "dark"),
    )


@main_bp.route("/spotify_auth")
def spotify_auth():
    from spotipy.oauth2 import SpotifyOAuth

    cfg = load_config()
    sp_cfg = cfg.get("spotify", {})
    cid, csec, ruri = (
        sp_cfg.get("client_id", ""),
        sp_cfg.get("client_secret", ""),
        sp_cfg.get("redirect_uri", ""),
    )
    scope = sp_cfg.get(
        "scope", "user-read-currently-playing user-read-playback-state"
    )
    if not (cid and csec and ruri):
        return "Spotify config incomplete", 400
    sp_oauth = SpotifyOAuth(
        client_id=cid,
        client_secret=csec,
        redirect_uri=ruri,
        scope=scope,
        cache_path=".spotify_cache",
    )
    return redirect(sp_oauth.get_authorize_url())


@main_bp.route("/callback")
def callback():
    from spotipy.oauth2 import SpotifyOAuth

    cfg = load_config()
    sp_cfg = cfg.get("spotify", {})
    cid, csec, ruri = (
        sp_cfg.get("client_id", ""),
        sp_cfg.get("client_secret", ""),
        sp_cfg.get("redirect_uri", ""),
    )
    scope = sp_cfg.get(
        "scope", "user-read-currently-playing user-read-playback-state"
    )
    sp_oauth = SpotifyOAuth(
        client_id=cid,
        client_secret=csec,
        redirect_uri=ruri,
        scope=scope,
        cache_path=".spotify_cache",
    )
    code = request.args.get("code")
    if not code:
        return "Authorization failed: no code provided", 400
    try:
        sp_oauth.get_access_token(code)
    except Exception as e:
        log_message(f"Spotify callback error: {e}")
        return "Spotify callback error", 500

    # Auto redirect if the callback is coming via localhost
    if "localhost" in request.host or "127.0.0.1" in request.host:
        redirect_url = f"http://{get_ip_address()}:8080/configure_spotify"
        return f"""
        <html><head><meta charset="utf-8">
        <meta http-equiv="refresh" content="0; url={redirect_url}">
        <title>Spotify Authorization Complete</title></head>
        <body>
          <h2>Spotify Authorization Complete</h2>
          <p>If you are not redirected automatically,
             <a href="{redirect_url}">click here</a>.</p>
        </body></html>
        """
    return redirect(url_for("main.configure_spotify"))


# overlay_config route unchanged (no shuffle / spotify interaction)
@main_bp.route("/overlay_config", methods=["GET", "POST"])
def overlay_config():
    cfg = load_config()
    if request.method == "POST":
        for monitor in cfg.get("displays", {}):
            new_overlay = {
                "clock_enabled": f"{monitor}_clock_enabled" in request.form,
                "weather_enabled": f"{monitor}_weather_enabled" in request.form,
                "clock_font_size": int(
                    request.form.get(f"{monitor}_clock_font_size", "26")
                ),
                "weather_font_size": int(
                    request.form.get(f"{monitor}_weather_font_size", "22")
                ),
                "font_color": request.form.get(f"{monitor}_font_color", "#FFFFFF"),
                "auto_negative_font": f"{monitor}_auto_negative_font" in request.form,
                "clock_position": request.form.get(
                    f"{monitor}_clock_position", "bottom-center"
                ),
                "weather_position": request.form.get(
                    f"{monitor}_weather_position", "bottom-center"
                ),
                "weather_layout": request.form.get(
                    f"{monitor}_weather_layout", "inline"
                ),
                "show_desc": f"{monitor}_show_desc" in request.form,
                "show_temp": f"{monitor}_show_temp" in request.form,
                "show_feels_like": f"{monitor}_show_feels_like" in request.form,
                "show_humidity": f"{monitor}_show_humidity" in request.form,
            }
            if "displays" in cfg and monitor in cfg["displays"]:
                cfg["displays"][monitor]["overlay"] = new_overlay
        save_config(cfg)
        try:
            subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
        except subprocess.CalledProcessError as e:
            log_message(f"Failed to restart after overlay_config: {e}")
        return redirect(url_for("main.overlay_config"))

    return render_template(
        "overlay.html",
        theme=cfg.get("theme", "dark"),
        monitors=cfg.get("displays", {}),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN INDEX  – ***core persistence bug-fixes applied here***
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()

    # Extended monitor detection
    ext_mons = detect_monitors_extended()
    cfg.setdefault("displays", {})

    # Remove stale display entries
    to_remove = [
        dname
        for dname in cfg["displays"]
        if dname not in ext_mons
        and (dname.startswith("Display") or dname.startswith("HDMI"))
    ]
    for dr in to_remove:
        del cfg["displays"][dr]

    # Add / update each connected monitor
    for mon_name, minfo in ext_mons.items():
        if mon_name not in cfg["displays"]:
            cfg["displays"][mon_name] = {
                "mode": "random_image",
                "fallback_mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": [],
                "rotate": 0,
                "screen_name": f"{mon_name}: {minfo['current_mode']}",
                "chosen_mode": minfo["current_mode"],
                "spotify_info_position": "bottom-center",
            }
            log_message(
                f"Detected new monitor {mon_name} with current mode {minfo['current_mode']}"
            )
        else:
            dcfg = cfg["displays"][mon_name]
            dcfg["screen_name"] = f"{mon_name}: {minfo['current_mode']}"
            if minfo.get("model"):
                dcfg["monitor_model"] = minfo["model"]

    save_config(cfg)

    flash_msg = (
        "If you experience lower performance or framerate than expected, "
        "please consider using a physically lower resolution monitor."
    )

    # ───────────────────────────────────────────
    # POST  →  update_displays
    # ───────────────────────────────────────────
    if request.method == "POST":
        if request.form.get("action") == "update_displays":
            for dname, dcfg in cfg["displays"].items():
                pre = f"{dname}_"

                new_mode = request.form.get(pre + "mode", dcfg.get("mode", "random_image"))
                dcfg["mode"] = new_mode

                # interval present for all modes
                if pre + "image_interval" in request.form:
                    try:
                        dcfg["image_interval"] = int(request.form.get(pre + "image_interval"))
                    except Exception:
                        pass

                # image_category / specific_image only if field visible
                if pre + "image_category" in request.form:
                    dcfg["image_category"] = request.form.get(pre + "image_category", dcfg.get("image_category", ""))
                if pre + "specific_image" in request.form:
                    dcfg["specific_image"] = request.form.get(pre + "specific_image", dcfg.get("specific_image", ""))

                # shuffle checkbox: only touch if present
                if pre + "shuffle_mode" in request.form:
                    dcfg["shuffle_mode"] = True
                elif pre + "shuffle_mode" in request.form.keys():
                    # explicit radio buttons case
                    dcfg["shuffle_mode"] = request.form.get(pre + "shuffle_mode") == "yes"

                # rotation
                if pre + "rotate" in request.form:
                    try:
                        dcfg["rotate"] = int(request.form.get(pre + "rotate"))
                    except Exception:
                        pass

                # mixed folders list
                if pre + "mixed_order" in request.form:
                    mixed_str = request.form.get(pre + "mixed_order", "")
                    dcfg["mixed_folders"] = [x for x in mixed_str.split(",") if x]

                # ── Spotify-specific fields (only when shown) ──
                if pre + "fallback_mode" in request.form:
                    dcfg["fallback_mode"] = request.form.get(pre + "fallback_mode", dcfg.get("fallback_mode", "random_image"))

                if pre + "spotify_show_song" in request.form or pre + "spotify_show_artist" in request.form or pre + "spotify_show_album" in request.form:
                    dcfg["spotify_show_song"] = pre + "spotify_show_song" in request.form
                    dcfg["spotify_show_artist"] = pre + "spotify_show_artist" in request.form
                    dcfg["spotify_show_album"] = pre + "spotify_show_album" in request.form

                if pre + "spotify_font_size" in request.form:
                    try:
                        dcfg["spotify_font_size"] = int(request.form.get(pre + "spotify_font_size", "18"))
                    except Exception:
                        pass

                if pre + "spotify_negative_font" in request.form or pre + "spotify_negative_font" in request.form.keys():
                    dcfg["spotify_negative_font"] = pre + "spotify_negative_font" in request.form

                if pre + "spotify_info_position" in request.form:
                    dcfg["spotify_info_position"] = request.form.get(pre + "spotify_info_position", dcfg.get("spotify_info_position", "bottom-center"))

                if pre + "spotify_show_progress" in request.form or pre + "spotify_show_progress" in request.form.keys():
                    dcfg["spotify_show_progress"] = pre + "spotify_show_progress" in request.form

                if pre + "spotify_progress_position" in request.form:
                    dcfg["spotify_progress_position"] = request.form.get(pre + "spotify_progress_position", dcfg.get("spotify_progress_position", "below_info"))

                if pre + "spotify_progress_theme" in request.form:
                    dcfg["spotify_progress_theme"] = request.form.get(pre + "spotify_progress_theme", dcfg.get("spotify_progress_theme", "dark"))

                if pre + "spotify_progress_update_interval" in request.form:
                    try:
                        dcfg["spotify_progress_update_interval"] = int(
                            request.form.get(pre + "spotify_progress_update_interval", dcfg.get("spotify_progress_update_interval", 200))
                        )
                    except Exception:
                        pass

            save_config(cfg)
            try:
                subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
            except Exception as e:
                log_message(f"Viewer restart after update_displays failed: {e}")
            return redirect(url_for("main.index"))

    # ───────────────────────────────────────────
    # GET – build template context
    # ───────────────────────────────────────────
    folder_counts = {sf: count_files_in_folder(os.path.join(IMAGE_DIR, sf)) for sf in get_subfolders()}

    display_images = {}
    for dname, dcfg in cfg["displays"].items():
        cat = dcfg.get("image_category", "")
        base_dir = os.path.join(IMAGE_DIR, cat) if cat else IMAGE_DIR
        img_list = [
            (os.path.join(cat, f) if cat else f)
            for f in os.listdir(base_dir)
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))
        ] if os.path.isdir(base_dir) else []
        img_list.sort()
        display_images[dname] = img_list

    cpu, mem_mb, load1, temp = get_system_stats()
    host, ipaddr, model = get_hostname(), get_ip_address(), get_pi_model()
    theme = cfg.get("theme", "dark")

    sub_info_line = ""
    if cfg.get("role") == "sub":
        sub_info_line = "This device is SUB"
        if cfg.get("main_ip"):
            sub_info_line += f" - Main IP: {cfg['main_ip']}"

    if cfg.get("role") == "main":
        sp_cfg = cfg.get("spotify", {})
        spotify_cache_path = os.path.join(VIEWER_HOME, ".spotify_cache")
        spotify_status = (
            "✅"
            if sp_cfg.get("client_id") and sp_cfg.get("client_secret") and sp_cfg.get("redirect_uri") and os.path.exists(spotify_cache_path)
            else ("⚠️" if sp_cfg.get("client_id") else "❌")
        )

        w_cfg = cfg.get("weather", {})
        weather_status = "✅" if w_cfg.get("api_key") and w_cfg.get("zip_code") and w_cfg.get("country_code") else "❌"

        devices = cfg.get("devices", [])
        subdevices_status = (
            "✅ " + ", ".join(d.get("name", d.get("ip", "Unknown")) for d in devices)
            if devices
            else "❌"
        )
    else:
        spotify_status = weather_status = ""
        subdevices_status = sub_info_line

    final_monitors = {
        mon_name: {
            "resolution": cfg["displays"][mon_name].get("chosen_mode", minfo["current_mode"]),
            "available_modes": minfo["modes"],
            "model_name": minfo.get("model") or "?",
        }
        for mon_name, minfo in ext_mons.items()
    }

    return render_template(
        "index.html",
        cfg=cfg,
        subfolders=get_subfolders(),
        folder_counts=folder_counts,
        display_images=display_images,
        cpu=cpu,
        mem_mb=round(mem_mb, 1),
        load1=round(load1, 2),
        temp=temp,
        host=host,
        ipaddr=ipaddr,
        model=model,
        theme=theme,
        version=APP_VERSION,
        sub_info_line=sub_info_line,
        monitors=final_monitors,
        flash_msg=flash_msg,
        spotify_status=spotify_status,
        weather_status=weather_status,
        subdevices_status=subdevices_status,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  REMOTE CONFIGURE  – identical persistence tweaks
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/remote_configure/<int:dev_index>", methods=["GET", "POST"])
def remote_configure(dev_index):
    cfg = load_config()
    if cfg.get("role") != "main":
        return "This device is not 'main'.", 403

    if dev_index < 0 or dev_index >= len(cfg.get("devices", [])):
        return "Invalid device index", 404

    dev_info = cfg["devices"][dev_index]
    dev_ip, dev_name = dev_info.get("ip"), dev_info.get("name")

    remote_cfg = get_remote_config(dev_ip) or {"displays": {}}
    remote_mons = get_remote_monitors(dev_ip)
    remote_folders = []
    try:
        r = requests.get(f"http://{dev_ip}:8080/list_folders", timeout=5)
        if r.status_code == 200:
            remote_folders = r.json()
    except Exception:
        pass

    if request.method == "POST" and request.form.get("action") == "update_remote":
        new_disp = {}
        for dname, dc in remote_cfg.get("displays", {}).items():
            pre = f"{dname}_"
            subdict = dc.copy()

            if pre + "mode" in request.form:
                subdict["mode"] = request.form.get(pre + "mode")

            if pre + "image_interval" in request.form:
                try:
                    subdict["image_interval"] = int(request.form.get(pre + "image_interval"))
                except Exception:
                    pass

            if pre + "image_category" in request.form:
                subdict["image_category"] = request.form.get(pre + "image_category", dc.get("image_category", ""))

            if pre + "specific_image" in request.form:
                subdict["specific_image"] = request.form.get(pre + "specific_image", dc.get("specific_image", ""))

            if pre + "shuffle_mode" in request.form:
                subdict["shuffle_mode"] = True

            if pre + "rotate" in request.form:
                try:
                    subdict["rotate"] = int(request.form.get(pre + "rotate"))
                except Exception:
                    pass

            if pre + "mixed_order" in request.form:
                mixed_str = request.form.get(pre + "mixed_order", "")
                subdict["mixed_folders"] = [x for x in mixed_str.split(",") if x]

            new_disp[dname] = subdict

        push_displays_to_remote(dev_ip, new_disp)
        return redirect(url_for("main.remote_configure", dev_index=dev_index))

    return render_template(
        "remote_configure.html",
        dev_name=dev_name,
        dev_ip=dev_ip,
        remote_cfg=remote_cfg,
        remote_mons=remote_mons,
        remote_folders=remote_folders,
        theme=cfg.get("theme", "dark"),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Sync / update_config remain unchanged
# ─────────────────────────────────────────────────────────────────────────────
@main_bp.route("/sync_config", methods=["GET"])
def sync_config():
    return jsonify(load_config())


@main_bp.route("/update_config", methods=["POST"])
def update_config():
    incoming = request.get_json()
    if not incoming:
        return "No JSON received", 400
    cfg = load_config()
    if "displays" in incoming:
        cfg["displays"] = incoming["displays"]
    if "theme" in incoming:
        cfg["theme"] = incoming["theme"]
    save_config(cfg)
    log_message("Local config partially updated via /update_config")
    try:
        subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart piviewer after config update: {e}")
    return "Config updated", 200


# remaining device_manager, update_app, restart_services – unchanged
@main_bp.route("/device_manager", methods=["GET", "POST"])
def device_manager():
    cfg = load_config()
    if cfg.get("role") != "main":
        return "This device is not 'main'.", 403

    local_ip = get_ip_address()
    if request.method == "POST":
        action = request.form.get("action", "")
        dev_name = request.form.get("dev_name", "").strip()
        dev_ip = request.form.get("dev_ip", "").strip()

        if action == "add_device" and dev_name and dev_ip and dev_ip != local_ip:
            cfg.setdefault("devices", []).append({"name": dev_name, "ip": dev_ip, "displays": {}})
            save_config(cfg)
            log_message(f"Added sub device: {dev_name} ({dev_ip})")

        elif action.startswith("remove_"):
            try:
                idx = int(action.replace("remove_", ""))
                if 0 <= idx < len(cfg.get("devices", [])):
                    removed = cfg["devices"].pop(idx)
                    save_config(cfg)
                    log_message(f"Removed sub device: {removed}")
            except Exception:
                pass

        elif action.startswith("push_"):
            try:
                idx = int(action.replace("push_", ""))
                dev_info = cfg["devices"][idx]
                push_displays_to_remote(dev_info["ip"], dev_info.get("displays", {}))
            except Exception as e:
                log_message(f"Push error: {e}")

        elif action.startswith("pull_"):
            try:
                idx = int(action.replace("pull_", ""))
                dev_info = cfg["devices"][idx]
                rd = pull_displays_from_remote(dev_info["ip"])
                if rd is not None:
                    dev_info["displays"] = rd
                    save_config(cfg)
                    log_message(f"Pulled remote displays from {dev_info['ip']} into idx {idx}")
            except Exception as e:
                log_message(f"Pull error: {e}")

        return redirect(url_for("main.device_manager"))

    return render_template(
        "device_manager.html", cfg=cfg, theme=cfg.get("theme", "dark")
    )


@main_bp.route("/update_app", methods=["POST"])
def update_app():
    cfg = load_config()
    log_message(f"Starting update: forced reset to origin/{UPDATE_BRANCH}")

    try:
        subprocess.check_call(["git", "fetch"], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "checkout", UPDATE_BRANCH], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "reset", "--hard", f"origin/{UPDATE_BRANCH}"], cwd=VIEWER_HOME)
    except subprocess.CalledProcessError as e:
        log_message(f"Git update failed: {e}")
        return "Git update failed. Check logs.", 500

    log_message("Update completed successfully.")
    subprocess.Popen(["sudo", "reboot"])

    theme = cfg.get("theme", "dark")
    if theme == "dark":
        page_bg, text_color, button_bg, button_color, link_hover_bg = (
            "#121212",
            "#ECECEC",
            "#444",
            "#FFF",
            "#666",
        )
    else:
        page_bg, text_color, button_bg, button_color, link_hover_bg = (
            "#FFFFFF",
            "#222",
            "#ddd",
            "#111",
            "#bbb",
        )

    return f"""
    <html><head><meta charset="utf-8"/><title>PiViewer Update</title>
    <style>
      body{{background-color:{page_bg};color:{text_color};font-family:Arial,sans-serif;
           text-align:center;margin-top:50px;}}
      a.button{{display:inline-block;margin-top:20px;padding:10px 20px;
                background-color:{button_bg};color:{button_color};
                border:none;border-radius:6px;text-decoration:none;cursor:pointer;}}
      a.button:hover{{background-color:{link_hover_bg};}}
    </style></head>
    <body>
      <h2>Update is complete. The system is now rebooting...</h2>
      <p>Please wait for the device to come back online.</p>
      <p>If the device does not redirect automatically,<br>
         <a class="button" href="/">Return to Home Page</a></p>
      <script>setTimeout(()=>{{window.location.href="/";}},10000);</script>
    </body></html>
    """


@main_bp.route("/restart_services", methods=["POST", "GET"])
def restart_services():
    try:
        subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
        subprocess.check_call(["sudo", "systemctl", "restart", "controller.service"])
        log_message("Services restarted.")
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart services: {e}")
        return "Failed to restart services. Check logs.", 500
    return "Services are restarting now..."
