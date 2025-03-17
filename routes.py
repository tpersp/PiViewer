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
    get_hostname, get_ip_address, get_pi_model
)

main_bp = Blueprint("main", __name__, static_folder="static")


############################################################
# Extended Monitor Detection: get available modes, model name
############################################################
def detect_monitors_extended():
    """
    Calls xrandr --props to find connected monitors, their preferred/current resolution,
    plus a list of possible modes, plus a 'monitor name' from EDID if available.

    Returns a dict, e.g.:
      {
        "HDMI-1": {
          "model": "ASUS 14MUH2k5" (if found),
          "connected": True,
          "current_mode": "1920x1080",
          "modes": ["1920x1080", "1280x720", "640x480", ...]
        },
        ...
      }
    """
    result = {}
    try:
        xout = subprocess.check_output(["xrandr", "--props"], stderr=subprocess.STDOUT).decode("utf-8", "ignore")
    except Exception as e:
        log_message(f"Monitor detection error: {e}")
        return {}

    # We'll parse line by line
    current_monitor = None
    for line in xout.splitlines():
        line = line.strip()
        if " connected " in line:
            # e.g. "HDMI-1 connected primary 1920x1080+0+0 ..."
            parts = line.split()
            name = parts[0]
            if "connected" in line:
                current_monitor = name
                result[current_monitor] = {
                    "model": None,
                    "connected": True,
                    "current_mode": None,
                    "modes": []
                }
                # parse current mode if present
                for p in parts:
                    if "x" in p and "+" in p:  # e.g. "1920x1080+0+0"
                        mode_part = p.split("+")[0]  # "1920x1080"
                        result[current_monitor]["current_mode"] = mode_part
                        break
        elif current_monitor and line.startswith("EDID:"):
            # The next lines might contain the monitor name inside an EDID decode, or "Monitor name:"
            # We'll keep scanning lines. We won't parse the hex. We'll just watch for 'Monitor name:'
            pass
        elif current_monitor and "Monitor name:" in line:
            # e.g. "    Monitor name: ASUS 14MUH2k5"
            # extract after the colon
            idx = line.find("Monitor name:")
            name_str = line[idx + len("Monitor name:"):].strip()
            if name_str:
                result[current_monitor]["model"] = name_str
        elif current_monitor and (line.startswith("  ") or line.startswith("\t")):
            # Might be a mode line, e.g. "  1920x1080 60.00*+ 59.94 ..."
            # We'll parse the first token as the mode
            tokens = line.split()
            if tokens:
                mode_candidate = tokens[0]
                if "x" in mode_candidate and (mode_candidate[0].isdigit()):
                    # e.g. "1920x1080"
                    # skip duplicates
                    if mode_candidate not in result[current_monitor]["modes"]:
                        result[current_monitor]["modes"].append(mode_candidate)
        elif line and not line.startswith(" "):
            # We've reached a line about a different output, or something else
            current_monitor = None

    return result


#######################################
# For overlay preview, we just need res
#######################################
def get_local_monitors_from_config(cfg):
    """
    Return a dict that shows each display name + 'resolution' from config.
    But now we rely on 'dcfg["chosen_mode"]' or 'dcfg["screen_name"]' if present.
    If none, "?"
    """
    out = {}
    for dname, dcfg in cfg.get("displays", {}).items():
        # If we have a chosen_mode, let's use that as resolution
        chosen_res = dcfg.get("chosen_mode") or None
        if chosen_res:
            out[dname] = {"resolution": chosen_res}
        else:
            # fallback to old screen_name or "?"
            sn = dcfg.get("screen_name", "")
            if sn and ":" in sn:
                # e.g. "HDMI-2: 1024x600"
                part = sn.split(":")[-1].strip()
                out[dname] = {"resolution": part if "x" in part else "?"}
            else:
                out[dname] = {"resolution": "?"}
    return out


def compute_overlay_preview(overlay_cfg, monitors_dict):
    selection = overlay_cfg.get("monitor_selection", "All")
    if selection == "All":
        maxw, maxh = 0, 0
        for dname, minfo in monitors_dict.items():
            try:
                w_str, h_str = minfo["resolution"].split("x")
                w, h = int(w_str), int(h_str)
                if w > maxw: maxw = w
                if h > maxh: maxh = h
            except:
                pass
        if maxw == 0 or maxh == 0:
            total_w, total_h = 1920, 1080
        else:
            total_w, total_h = maxw, maxh
    else:
        if selection in monitors_dict:
            try:
                part = monitors_dict[selection]["resolution"]
                w_str, h_str = part.split("x")
                total_w, total_h = int(w_str), int(h_str)
            except:
                total_w, total_h = 1920, 1080
        else:
            total_w, total_h = 1920, 1080

    max_preview_w = 400
    if total_w > 0:
        scale_factor = float(max_preview_w) / float(total_w)
    else:
        scale_factor = 0.2
    preview_width = int(total_w * scale_factor)
    preview_height = int(total_h * scale_factor)

    ow = overlay_cfg.get("overlay_width", 300)
    oh = overlay_cfg.get("overlay_height", 150)
    ox = overlay_cfg.get("offset_x", 20)
    oy = overlay_cfg.get("offset_y", 20)

    preview_overlay = {
        "width": int(ow * scale_factor),
        "height": int(oh * scale_factor),
        "left": int(ox * scale_factor),
        "top": int(oy * scale_factor),
    }

    return (preview_width, preview_height, preview_overlay)


##################
# XRandR resolution
##################
def set_monitor_resolution(output_name, resolution):
    """
    Attempt to set the given resolution on 'output_name' using xrandr.
    e.g. xrandr --output HDMI-1 --mode 1280x720
    """
    try:
        subprocess.check_call(["xrandr", "--output", output_name, "--mode", resolution])
        log_message(f"Set {output_name} to resolution {resolution} via xrandr.")
        return True
    except subprocess.CalledProcessError as e:
        log_message(f"Failed xrandr set {output_name} -> {resolution}: {e}")
        return False


def set_monitor_rotation(output_name, degrees):
    """
    If degrees is 0, 90, 180, or 270, we can do xrandr rotate.
    Otherwise, we skip. (We are also rotating in code, but the user asked
    if we can do xrandr-based. We'll do a best attempt.)
    """
    # xrandr rotate can only do normal, left, inverted, right
    # We'll map 0->normal, 90->left, 180->inverted, 270->right
    # everything else is "normal"
    angle_map = {
        0: "normal",
        90: "left",
        180: "inverted",
        270: "right"
    }
    if degrees in angle_map:
        rot_arg = angle_map[degrees]
        try:
            subprocess.check_call(["xrandr", "--output", output_name, "--rotate", rot_arg])
            log_message(f"XRandR rotate {output_name} -> {degrees} deg.")
        except subprocess.CalledProcessError as e:
            log_message(f"Failed xrandr rotate {output_name} to {degrees}: {e}")


############################################################
# Flask routes
############################################################
main_bp = Blueprint("main", __name__, static_folder="static")


@main_bp.route("/stats")
def stats_json():
    cpu, mem_mb, load1, temp = get_system_stats()
    return jsonify({
        "cpu_percent": cpu,
        "mem_used_mb": round(mem_mb, 1),
        "load_1min": round(load1, 2),
        "temp": temp
    })


@main_bp.route("/list_monitors")
def list_monitors():
    # older remote logic, unchanged
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


@main_bp.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if "weather" not in cfg:
        cfg["weather"] = {}

    if request.method == "POST":
        new_theme = request.form.get("theme", "dark")
        new_role = request.form.get("role", "main")
        cfg["theme"] = new_theme
        cfg["role"] = new_role
        if new_role == "sub":
            cfg["main_ip"] = request.form.get("main_ip", "").strip()
        else:
            cfg["main_ip"] = ""

        if new_theme == "custom":
            if "bg_image" in request.files:
                f = request.files["bg_image"]
                if f and f.filename:
                    f.save(WEB_BG)

        w_api = request.form.get("weather_api_key", "").strip()
        w_zip = request.form.get("weather_zip_code", "").strip()
        w_cc = request.form.get("weather_country_code", "").strip()
        if w_api and w_zip and w_cc:
            try:
                weather_url = f"http://api.openweathermap.org/data/2.5/weather?zip={w_zip},{w_cc}&appid={w_api}"
                r = requests.get(weather_url, timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    coord = data.get("coord", {})
                    w_lat = str(coord.get("lat", ""))
                    w_lon = str(coord.get("lon", ""))
                else:
                    w_lat = request.form.get("weather_lat", "").strip()
                    w_lon = request.form.get("weather_lon", "").strip()
            except Exception as e:
                w_lat = request.form.get("weather_lat", "").strip()
                w_lon = request.form.get("weather_lon", "").strip()
        else:
            w_lat = request.form.get("weather_lat", "").strip()
            w_lon = request.form.get("weather_lon", "").strip()

        cfg["weather"]["api_key"] = w_api
        cfg["weather"]["zip_code"] = w_zip
        cfg["weather"]["country_code"] = w_cc
        try:
            cfg["weather"]["lat"] = float(w_lat)
        except:
            cfg["weather"]["lat"] = None
        try:
            cfg["weather"]["lon"] = float(w_lon)
        except:
            cfg["weather"]["lon"] = None

        # --- GUI settings ---
        if "gui" not in cfg:
            cfg["gui"] = {}
        try:
            cfg["gui"]["background_blur_radius"] = int(request.form.get("background_blur_radius", "20"))
        except:
            cfg["gui"]["background_blur_radius"] = 20

        try:
            cfg["gui"]["background_scale_percent"] = int(request.form.get("background_scale_percent", "100"))
        except:
            cfg["gui"]["background_scale_percent"] = 100

        try:
            cfg["gui"]["foreground_scale_percent"] = int(request.form.get("foreground_scale_percent", "100"))
        except:
            cfg["gui"]["foreground_scale_percent"] = 100

        save_config(cfg)
        return redirect(url_for("main.settings"))
    else:
        return render_template(
            "settings.html",
            theme=cfg.get("theme", "dark"),
            cfg=cfg,
            update_branch=UPDATE_BRANCH
        )


@main_bp.route("/configure_spotify", methods=["GET", "POST"])
def configure_spotify():
    cfg = load_config()
    if "spotify" not in cfg:
        cfg["spotify"] = {}
    if request.method == "POST":
        cid = request.form.get("client_id", "").strip()
        csec = request.form.get("client_secret", "").strip()
        ruri = request.form.get("redirect_uri", "").strip()
        scope = request.form.get("scope", "user-read-currently-playing user-read-playback-state").strip()
        cfg["spotify"] = {
            "client_id": cid,
            "client_secret": csec,
            "redirect_uri": ruri,
            "scope": scope
        }
        save_config(cfg)
        return redirect(url_for("main.configure_spotify"))
    else:
        return render_template(
            "configure_spotify.html",
            spotify=cfg["spotify"],
            theme=cfg.get("theme", "dark")
        )


@main_bp.route("/spotify_auth")
def spotify_auth():
    from spotipy.oauth2 import SpotifyOAuth
    cfg = load_config()
    sp_cfg = cfg.get("spotify", {})
    cid = sp_cfg.get("client_id", "")
    csec = sp_cfg.get("client_secret", "")
    ruri = sp_cfg.get("redirect_uri", "")
    scope = sp_cfg.get("scope", "user-read-currently-playing user-read-playback-state")
    if not (cid and csec and ruri):
        return "Spotify config incomplete", 400
    sp_oauth = SpotifyOAuth(
        client_id=cid,
        client_secret=csec,
        redirect_uri=ruri,
        scope=scope,
        cache_path=".spotify_cache"
    )
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)


@main_bp.route("/callback")
def callback():
    from spotipy.oauth2 import SpotifyOAuth
    cfg = load_config()
    sp_cfg = cfg.get("spotify", {})
    cid = sp_cfg.get("client_id", "")
    csec = sp_cfg.get("client_secret", "")
    ruri = sp_cfg.get("redirect_uri", "")
    scope = sp_cfg.get("scope", "user-read-currently-playing user-read-playback-state")
    sp_oauth = SpotifyOAuth(
        client_id=cid,
        client_secret=csec,
        redirect_uri=ruri,
        scope=scope,
        cache_path=".spotify_cache"
    )
    code = request.args.get("code")
    if not code:
        return "Authorization failed: no code provided", 400
    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        log_message(f"Spotify callback error: {e}")
        return "Spotify callback error", 500
    return redirect(url_for("main.configure_spotify"))


@main_bp.route("/overlay_config", methods=["GET", "POST"])
def overlay_config():
    cfg = load_config()
    if "overlay" not in cfg:
        cfg["overlay"] = {}
    over = cfg["overlay"]

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "select_monitor":
            over["monitor_selection"] = request.form.get("monitor_selection", "All")
            save_config(cfg)
            return redirect(url_for("main.overlay_config"))

        elif action == "save_overlay":
            over["overlay_enabled"] = ("overlay_enabled" in request.form)
            over["clock_enabled"] = ("clock_enabled" in request.form)
            over["weather_enabled"] = ("weather_enabled" in request.form)
            over["background_enabled"] = ("background_enabled" in request.form)
            try:
                over["clock_font_size"] = int(request.form.get("clock_font_size", "26"))
            except:
                over["clock_font_size"] = 26
            try:
                over["weather_font_size"] = int(request.form.get("weather_font_size", "22"))
            except:
                over["weather_font_size"] = 22
            over["font_color"] = request.form.get("font_color", "#FFFFFF")
            over["layout_style"] = request.form.get("layout_style", "stacked")
            try:
                over["padding_x"] = int(request.form.get("padding_x", "8"))
            except:
                over["padding_x"] = 8
            try:
                over["padding_y"] = int(request.form.get("padding_y", "6"))
            except:
                over["padding_y"] = 6

            over["show_desc"] = ("show_desc" in request.form)
            over["show_temp"] = ("show_temp" in request.form)
            over["show_feels_like"] = ("show_feels_like" in request.form)
            over["show_humidity"] = ("show_humidity" in request.form)

            # X/Y
            try:
                over["offset_x"] = int(request.form.get("offset_x", "20"))
            except:
                over["offset_x"] = 20
            try:
                over["offset_y"] = int(request.form.get("offset_y", "20"))
            except:
                over["offset_y"] = 20

            # W/H
            try:
                wval = int(request.form.get("overlay_width", "300"))
                over["overlay_width"] = wval
            except:
                over["overlay_width"] = 300
            try:
                hval = int(request.form.get("overlay_height", "150"))
                over["overlay_height"] = hval
            except:
                over["overlay_height"] = 150

            over["bg_color"] = request.form.get("bg_color", "#000000")
            try:
                over["bg_opacity"] = float(request.form.get("bg_opacity", "0.4"))
            except:
                over["bg_opacity"] = 0.4

            save_config(cfg)
            try:
                subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
            except subprocess.CalledProcessError as e:
                log_message(f"Failed to restart piviewer.service: {e}")

            return redirect(url_for("main.overlay_config"))

    # We re-check the monitors from config
    monitors_dict = get_local_monitors_from_config(cfg)
    pw, ph, preview_overlay = compute_overlay_preview(cfg["overlay"], monitors_dict)

    return render_template(
        "overlay.html",
        theme=cfg.get("theme", "dark"),
        overlay=cfg["overlay"],
        monitors=monitors_dict,
        preview_size={"width": pw, "height": ph},
        preview_overlay=preview_overlay
    )


@main_bp.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()

    # [1] Redetect extended monitors whenever we load the index
    # If the user has changed cables, we want to update config automatically.
    from .routes import detect_monitors_extended
    ext_mons = detect_monitors_extended()
    # We'll unify them with the config. For each connected monitor, update or add
    if "displays" not in cfg:
        cfg["displays"] = {}

    # remove any old "Display0" or old monitors that no longer exist
    # but let's do that carefully only if not found in ext_mons
    to_remove = []
    for dname in cfg["displays"]:
        if dname not in ext_mons and dname.startswith("Display"):
            # old fallback
            to_remove.append(dname)
        elif dname not in ext_mons and dname.startswith("HDMI"):
            # might be an old display
            to_remove.append(dname)
    for dr in to_remove:
        del cfg["displays"][dr]

    for mon_name, minfo in ext_mons.items():
        # example: minfo["model"], minfo["current_mode"], minfo["modes"]
        if mon_name not in cfg["displays"]:
            cfg["displays"][mon_name] = {
                "mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": [],
                "rotate": 0,
                # store the model if found
                "screen_name": f"{mon_name}: {minfo['current_mode']}",
                "chosen_mode": minfo["current_mode"]
            }
            log_message(f"Detected new monitor {mon_name} with current mode {minfo['current_mode']}")
        else:
            # update existing
            dcfg = cfg["displays"][mon_name]
            dcfg["screen_name"] = f"{mon_name}: {minfo['current_mode']}"
            # If there's no chosen_mode or it differs from the actual current mode, update it
            if "chosen_mode" not in dcfg or dcfg["chosen_mode"] != minfo["current_mode"]:
                dcfg["chosen_mode"] = minfo["current_mode"]

            # optionally store the 'model' for info
            if minfo["model"]:
                dcfg["monitor_model"] = minfo["model"]

    save_config(cfg)

    # [2] If user posted form
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_displays":
            for dname in cfg["displays"]:
                pre = dname + "_"
                dcfg = cfg["displays"][dname]
                new_mode = request.form.get(pre + "mode", dcfg["mode"])
                new_interval_s = request.form.get(pre + "image_interval", str(dcfg["image_interval"]))
                new_cat = request.form.get(pre + "image_category", dcfg["image_category"])
                shuffle_val = request.form.get(pre + "shuffle_mode", "no")
                new_spec = request.form.get(pre + "specific_image", dcfg["specific_image"])
                rotate_str = request.form.get(pre + "rotate", "0")
                mixed_str = request.form.get(pre + "mixed_order", "")
                mixed_list = [x for x in mixed_str.split(",") if x]

                try:
                    new_interval = int(new_interval_s)
                except:
                    new_interval = dcfg["image_interval"]
                try:
                    new_rotate = int(rotate_str)
                except:
                    new_rotate = 0

                dcfg["mode"] = new_mode
                dcfg["image_interval"] = new_interval
                dcfg["image_category"] = new_cat
                dcfg["shuffle_mode"] = (shuffle_val == "yes")
                dcfg["specific_image"] = new_spec
                dcfg["rotate"] = new_rotate

                if new_mode == "mixed":
                    dcfg["mixed_folders"] = mixed_list
                else:
                    dcfg["mixed_folders"] = []

            save_config(cfg)
            # Attempt to restart viewer
            try:
                subprocess.check_call(["sudo", "systemctl", "restart", "piviewer.service"])
            except:
                pass
            return redirect(url_for("main.index"))

        # [3] If user is changing resolution or something
        # let's see if there's a param like <monitor>_chosen_res
        for dname in cfg["displays"]:
            param_name = dname + "_chosen_res"
            if param_name in request.form:
                chosen_res = request.form.get(param_name)
                # attempt xrandr
                ok = set_monitor_resolution(dname, chosen_res)
                if ok:
                    cfg["displays"][dname]["chosen_mode"] = chosen_res
                    # also re-apply rotation if non-zero
                    rot = cfg["displays"][dname].get("rotate", 0)
                    set_monitor_rotation(dname, rot)
        save_config(cfg)
        return redirect(url_for("main.index"))

    # [4] Prepare data for the main page
    folder_counts = {}
    for sf in get_subfolders():
        folder_counts[sf] = count_files_in_folder(os.path.join(IMAGE_DIR, sf))

    display_images = {}
    for dname, dcfg in cfg["displays"].items():
        cat = dcfg.get("image_category", "")
        base_dir = os.path.join(IMAGE_DIR, cat) if cat else IMAGE_DIR
        image_list = []
        if os.path.isdir(base_dir):
            for fname in os.listdir(base_dir):
                lf = fname.lower()
                if lf.endswith((".jpg", ".jpeg", ".png", ".gif")):
                    rel_path = fname
                    image_list.append(os.path.join(cat, rel_path) if cat else rel_path)
        image_list.sort()
        display_images[dname] = image_list

    cpu, mem_mb, load1, temp = get_system_stats()
    host = get_hostname()
    ipaddr = get_ip_address()
    model = get_pi_model()
    theme = cfg.get("theme", "dark")

    sub_info_line = ""
    if cfg.get("role") == "sub":
        sub_info_line = "This device is SUB"
        if cfg["main_ip"]:
            sub_info_line += f" - Main IP: {cfg['main_ip']}"

    # We'll build a dictionary of monitors for the UI
    # Now we want to show a resolution dropdown for each monitor,
    # and possibly show model name too if we have it.
    final_monitors = {}
    # We'll call detect_monitors_extended again (already have ext_mons from above)
    for mon_name, minfo in ext_mons.items():
        # e.g. minfo["model"], minfo["modes"], minfo["current_mode"]
        # Use config's "chosen_mode" if any
        chosen = cfg["displays"][mon_name].get("chosen_mode", minfo["current_mode"])
        model_name = minfo["model"] or "?"
        # We'll list out the modes in the <select>
        final_monitors[mon_name] = {
            "resolution": chosen,
            "available_modes": minfo["modes"],
            "model_name": model_name
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
        monitors=final_monitors  # pass the new final_monitors data
    )


@main_bp.route("/remote_configure/<int:dev_index>", methods=["GET", "POST"])
def remote_configure(dev_index):
    cfg = load_config()
    if cfg.get("role") != "main":
        return "This device is not 'main'.", 403

    if dev_index < 0 or dev_index >= len(cfg.get("devices", [])):
        return "Invalid device index", 404

    dev_info = cfg["devices"][dev_index]
    dev_ip = dev_info.get("ip")
    dev_name = dev_info.get("name")

    remote_cfg = get_remote_config(dev_ip) or {"displays": {}}
    remote_mons = get_remote_monitors(dev_ip)
    remote_folders = []
    try:
        r = requests.get(f"http://{dev_ip}:8080/list_folders", timeout=5)
        if r.status_code == 200:
            remote_folders = r.json()
    except:
        pass

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_remote":
            new_disp = {}
            for dname, dc in remote_cfg.get("displays", {}).items():
                pre = dname + "_"
                new_mode = request.form.get(pre + "mode", dc.get("mode", "random_image"))
                new_int_s = request.form.get(pre + "image_interval", str(dc.get("image_interval", 60)))
                new_cat = request.form.get(pre + "image_category", dc.get("image_category", ""))
                new_shuffle = request.form.get(pre + "shuffle_mode", "no")
                new_spec = request.form.get(pre + "specific_image", dc.get("specific_image", ""))
                new_rot_s = request.form.get(pre + "rotate", str(dc.get("rotate", 0)))
                mixed_str = request.form.get(pre + "mixed_order", "")
                mixed_list = [x for x in mixed_str.split(",") if x]

                try:
                    ni = int(new_int_s)
                except:
                    ni = dc.get("image_interval", 60)
                try:
                    nr = int(new_rot_s)
                except:
                    nr = 0

                subdict = {
                    "mode": new_mode,
                    "image_interval": ni,
                    "image_category": new_cat,
                    "specific_image": new_spec,
                    "shuffle_mode": (new_shuffle == "yes"),
                    "mixed_folders": mixed_list if new_mode == "mixed" else [],
                    "rotate": nr
                }
                new_disp[dname] = subdict

            push_displays_to_remote(dev_ip, new_disp)
            return redirect(url_for("main.remote_configure", dev_index=dev_index))

    return render_template(
        "remote_configure.html",
        dev_name=dev_name,
        dev_ip=dev_ip,
        remote_cfg=remote_cfg,
        remote_mons=remote_mons,
        remote_folders=remote_folders
    )


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
    return "Config updated", 200


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
        if action == "add_device" and dev_name and dev_ip:
            if dev_ip == local_ip:
                log_message(f"Skipping adding device {dev_name} - same IP as local.")
            else:
                if "devices" not in cfg:
                    cfg["devices"] = []
                cfg["devices"].append({
                    "name": dev_name,
                    "ip": dev_ip,
                    "displays": {}
                })
                save_config(cfg)
                log_message(f"Added sub device: {dev_name} ({dev_ip})")
        elif action.startswith("remove_"):
            idx_str = action.replace("remove_", "")
            try:
                idx = int(idx_str)
                if 0 <= idx < len(cfg["devices"]):
                    removed = cfg["devices"].pop(idx)
                    save_config(cfg)
                    log_message(f"Removed sub device: {removed}")
            except:
                pass
        elif action.startswith("push_"):
            idx_str = action.replace("push_", "")
            try:
                idx = int(idx_str)
                dev_info = cfg["devices"][idx]
                dev_ip = dev_info.get("ip")
                if dev_ip:
                    push_displays_to_remote(dev_ip, dev_info.get("displays", {}))
            except Exception as e:
                log_message(f"Push error: {e}")
        elif action.startswith("pull_"):
            idx_str = action.replace("pull_", "")
            try:
                idx = int(idx_str)
                dev_info = cfg["devices"][idx]
                dev_ip = dev_info.get("ip")
                if dev_ip:
                    rd = pull_displays_from_remote(dev_ip)
                    if rd is not None:
                        dev_info["displays"] = rd
                        save_config(cfg)
                        log_message(f"Pulled remote displays from {dev_ip} => devices[{idx}]")
            except Exception as e:
                log_message(f"Pull error: {e}")
        return redirect(url_for("main.device_manager"))

    return render_template(
        "device_manager.html",
        cfg=cfg,
        theme=cfg.get("theme", "dark")
    )


@main_bp.route("/update_app", methods=["POST"])
def update_app():
    cfg = load_config()
    log_message(f"Starting update: forced reset to origin/{UPDATE_BRANCH}")

    old_hash = ""
    try:
        old_hash = subprocess.check_output(["git", "rev-parse", "HEAD:setup.sh"], cwd=VIEWER_HOME).decode().strip()
    except Exception as e:
        log_message(f"Could not get old setup.sh hash: {e}")

    try:
        subprocess.check_call(["git", "fetch"], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "checkout", UPDATE_BRANCH], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "reset", "--hard", f"origin/{UPDATE_BRANCH}"], cwd=VIEWER_HOME)
    except subprocess.CalledProcessError as e:
        log_message(f"Git update failed: {e}")
        return "Git update failed. Check logs.", 500

    new_hash = ""
    try:
        new_hash = subprocess.check_output(["git", "rev-parse", "HEAD:setup.sh"], cwd=VIEWER_HOME).decode().strip()
    except Exception as e:
        log_message(f"Could not get new setup.sh hash: {e}")

    if old_hash and new_hash and (old_hash != new_hash):
        log_message("setup.sh changed. Re-running it in --auto-update mode...")
        try:
            subprocess.check_call(["sudo", "bash", "setup.sh", "--auto-update"], cwd=VIEWER_HOME)
        except subprocess.CalledProcessError as e:
            log_message(f"Re-running setup.sh failed: {e}")

    log_message("Update completed successfully.")
    return render_template("update_complete.html")


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
