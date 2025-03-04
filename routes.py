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
    detect_monitors, get_remote_config, get_remote_monitors,
    pull_displays_from_remote, push_displays_to_remote,
    get_hostname, get_ip_address, get_pi_model, get_folder_prefix
)

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
    return jsonify(detect_monitors())


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


@main_bp.route("/upload_bg", methods=["POST"])
def upload_bg():
    f = request.files.get("bg_image")
    if f:
        f.save(WEB_BG)
    return redirect(url_for("main.settings"))


@main_bp.route("/upload_media", methods=["GET", "POST"])
def upload_media():
    cfg = load_config()
    subfolders = get_subfolders()
    if request.method == "GET":
        return render_template(
            "upload_media.html",
            theme=cfg.get("theme", "dark"),
            subfolders=subfolders
        )

    files = request.files.getlist("mediafiles")
    if not files:
        return "No file(s) selected", 400

    subfolder = request.form.get("subfolder") or ""
    new_subfolder = request.form.get("new_subfolder", "").strip()

    if new_subfolder:
        subfolder = new_subfolder
        target_dir = os.path.join(IMAGE_DIR, subfolder)
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
    else:
        target_dir = os.path.join(IMAGE_DIR, subfolder)
        if not os.path.exists(target_dir):
            return "Subfolder does not exist and no new folder was specified", 400

    for file in files:
        if not file.filename:
            continue
        original_name = file.filename
        ext = os.path.splitext(original_name.lower())[1]
        # Only allow GIF, JPG, JPEG, PNG
        if ext not in [".gif", ".jpg", ".jpeg", ".png"]:
            log_message(f"Skipped file (unsupported): {original_name}")
            continue

        new_filename = get_folder_prefix(subfolder)  # partial prefix
        # We use a numeric approach in the original code:
        new_filename = get_next_filename(subfolder, target_dir, ext)
        final_path = os.path.join(IMAGE_DIR, subfolder, new_filename)
        file.save(final_path)
        log_message(f"Uploaded file saved to: {final_path}")

    return redirect(url_for("main.index"))


def get_next_filename(subfolder_name, folder_path, desired_ext):
    prefix = get_folder_prefix(subfolder_name)
    existing = os.listdir(folder_path)
    max_num = 0
    for fname in existing:
        if fname.lower().startswith(prefix) and fname.lower().endswith(desired_ext):
            plen = len(prefix)
            num_str = fname[plen:-len(desired_ext)]
            try:
                num = int(num_str)
                if num > max_num:
                    max_num = num
            except:
                pass
    return f"{prefix}{(max_num + 1):03d}{desired_ext}"


@main_bp.route("/restart_viewer", methods=["POST"])
def restart_viewer():
    """
    Restarts both viewer.service and overlay.service so that
    any display or overlay changes take effect.
    """
    try:
        subprocess.check_output(["sudo", "systemctl", "restart", "viewer.service"])
        subprocess.check_output(["sudo", "systemctl", "restart", "overlay.service"])
        return redirect(url_for("main.index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart services: {e}", 500


@main_bp.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        new_theme = request.form.get("theme", "dark")
        new_role = request.form.get("role", "main")
        cfg["theme"] = new_theme
        cfg["role"] = new_role

        if new_role == "sub":
            cfg["main_ip"] = request.form.get("main_ip", "").strip()
        else:
            cfg["main_ip"] = ""

        save_config(cfg)
        return redirect(url_for("main.settings"))

    return render_template(
        "settings.html",
        theme=cfg.get("theme", "dark"),
        cfg=cfg,
        update_branch=UPDATE_BRANCH
    )


@main_bp.route("/overlay_config", methods=["GET", "POST"])
def overlay_config():
    """
    Manage settings for the clock & weather overlay in cfg["overlay"].
    We add new fields:
      - monitor_selection
      - overlay_width, overlay_height
    Also, we create a scaled preview for the chosen monitor or all monitors.
    """
    cfg = load_config()
    if "overlay" not in cfg:
        cfg["overlay"] = {
            "weather_enabled": False,
            "api_key": "",
            "zip_code": "",
            "country_code": "",
            "lat": None,
            "lon": None,
            "bg_color": "#000000",
            "bg_opacity": 0.4,
            "offset_x": 20,
            "offset_y": 20,
            "monitor_selection": "All",
            "overlay_width": 300,
            "overlay_height": 150
        }

    over = cfg["overlay"]
    monitors = detect_monitors()  # e.g. {"HDMI-1": {...}, "HDMI-2": {...}}

    # By default let's pick "All" as a combined bounding resolution if there's more than one monitor.
    # We'll parse the resolution to compute a scaled size for the preview.
    # For simplicity, if "All" is chosen, we assume all monitors are side-by-side horizontally.
    total_width = 0
    total_height = 0

    if len(monitors) == 0:
        # No actual monitors detected, fallback
        monitors = {"Display0": {"resolution": "1920x1080", "name": "Display0"}}

    if over["monitor_selection"] == "All":
        # sum widths and take max height
        for mname, minfo in monitors.items():
            res = minfo.get("resolution", "1920x1080")
            w, h = parse_resolution(res)
            total_width += w
            if h > total_height:
                total_height = h
    else:
        # Single monitor
        chosen = over["monitor_selection"]
        if chosen in monitors:
            res = monitors[chosen].get("resolution", "1920x1080")
            w, h = parse_resolution(res)
            total_width = w
            total_height = h
        else:
            # fallback
            total_width, total_height = (1920, 1080)

    # Decide on a scale factor so the preview is not too large
    max_preview_w = 500.0  # or 600
    scaleFactor = 1.0
    if total_width > 0:
        scaleFactor = max_preview_w / float(total_width)
    if scaleFactor > 1.0:
        scaleFactor = 1.0
    previewW = int(total_width * scaleFactor)
    previewH = int(total_height * scaleFactor)

    # We'll position the draggable overlay box in the preview:
    # scale the offset + width/height
    boxLeft = int(over.get("offset_x", 20) * scaleFactor)
    boxTop  = int(over.get("offset_y", 20) * scaleFactor)
    boxW    = int(over.get("overlay_width", 300) * scaleFactor)
    boxH    = int(over.get("overlay_height", 150) * scaleFactor)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "select_monitor":
            # user changed the monitor in the dropdown
            over["monitor_selection"] = request.form.get("monitor_selection", "All")
            save_config(cfg)
            return redirect(url_for("main.overlay_config"))

        elif action == "save_overlay":
            over["weather_enabled"] = (request.form.get("weather_enabled") == "on")
            over["api_key"] = request.form.get("api_key", "").strip()
            over["zip_code"] = request.form.get("zip_code", "").strip()
            over["country_code"] = request.form.get("country_code", "").strip()

            lat_str = request.form.get("lat", "").strip()
            lon_str = request.form.get("lon", "").strip()
            try:
                over["lat"] = float(lat_str)
            except:
                over["lat"] = None
            try:
                over["lon"] = float(lon_str)
            except:
                over["lon"] = None

            over["bg_color"] = request.form.get("bg_color", "#000000").strip()
            try:
                over["bg_opacity"] = float(request.form.get("bg_opacity", "0.4"))
            except:
                over["bg_opacity"] = 0.4

            try:
                over["offset_x"] = int(request.form.get("offset_x", "20"))
            except:
                over["offset_x"] = 20

            try:
                over["offset_y"] = int(request.form.get("offset_y", "20"))
            except:
                over["offset_y"] = 20

            # new fields
            monitor_sel = over.get("monitor_selection", "All")  # keep existing if not changed
            # we do NOT forcibly update monitor_selection here because it is changed by 'select_monitor' action
            try:
                over["overlay_width"] = int(request.form.get("overlay_width", "300"))
            except:
                over["overlay_width"] = 300
            try:
                over["overlay_height"] = int(request.form.get("overlay_height", "150"))
            except:
                over["overlay_height"] = 150

            # auto-lookup lat/lon if possible:
            if over["api_key"] and over["zip_code"] and over["country_code"]:
                if (over["lat"] is None) or (over["lon"] is None):
                    _auto_lookup_latlon(over)

            save_config(cfg)

            # restart overlay service
            try:
                subprocess.check_call(["sudo", "systemctl", "restart", "overlay.service"])
            except subprocess.CalledProcessError as e:
                log_message(f"Failed to restart overlay.service: {e}")

            return redirect(url_for("main.overlay_config"))

    # Render
    preview_data = {
        "width": previewW,
        "height": previewH
    }
    preview_overlay = {
        "left": boxLeft,
        "top": boxTop,
        "width": boxW,
        "height": boxH
    }

    return render_template(
        "overlay.html",
        theme=cfg.get("theme", "dark"),
        overlay=over,
        monitors=monitors,
        preview_size=preview_data,
        preview_overlay=preview_overlay
    )


def _auto_lookup_latlon(over):
    """
    Call the OWM Geo endpoint automatically if we have
    apikey, zip, and country but lat/lon are not set.
    """
    apikey = over["api_key"]
    zip_c = over["zip_code"]
    ctry = over["country_code"]
    url = f"http://api.openweathermap.org/geo/1.0/zip?zip={zip_c},{ctry}&appid={apikey}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            lat = data.get("lat")
            lon = data.get("lon")
            if lat is not None and lon is not None:
                over["lat"] = lat
                over["lon"] = lon
                log_message(f"Overlay lat/lon auto-updated: {lat}, {lon}")
        else:
            log_message(f"Geo lookup failed. Status code: {r.status_code}")
    except Exception as e:
        log_message(f"Geo lookup error: {e}")


def parse_resolution(res_str):
    """
    Given a string like "1920x1080", return (1920,1080).
    If parse fails, return (1920,1080).
    """
    try:
        w, h = res_str.lower().split("x")
        return (int(w), int(h))
    except:
        return (1920, 1080)


@main_bp.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    monitors = detect_monitors()

    for m in monitors:
        if m not in cfg["displays"]:
            cfg["displays"][m] = {
                "mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": [],
                "rotate": 0
            }

    remove_list = [d for d in list(cfg["displays"].keys()) if d not in monitors]
    for r in remove_list:
        del cfg["displays"][r]

    save_config(cfg)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_displays":
            for disp_name in cfg["displays"]:
                pre = disp_name + "_"
                dcfg = cfg["displays"][disp_name]
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
            return redirect(url_for("main.index"))

    folder_counts = {}
    for sf in get_subfolders():
        folder_counts[sf] = count_files_in_folder(os.path.join(IMAGE_DIR, sf))

    display_images = {}
    for dname, dcfg in cfg["displays"].items():
        if dcfg["mode"] == "specific_image":
            cat = dcfg.get("image_category", "")
            path = os.path.join(IMAGE_DIR, cat)
            if cat and os.path.exists(path):
                fs = [f for f in os.listdir(path)
                      if f.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))]
                fs.sort()
                display_images[dname] = [os.path.join(cat, f) for f in fs]
            else:
                display_images[dname] = []
        else:
            display_images[dname] = []

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

    remote_displays = []
    if cfg.get("role") == "main":
        for dev in cfg.get("devices", []):
            dev_ip = dev.get("ip")
            dev_name = dev.get("name", "Unknown")
            if not dev_ip:
                continue
            rem_cfg = get_remote_config(dev_ip)
            if not rem_cfg:
                continue
            remote_mons = get_remote_monitors(dev_ip)
            table_of_displays = []
            for rdname, rdcfg in rem_cfg.get("displays", {}).items():
                resolution = "unknown"
                if remote_mons and rdname in remote_mons:
                    resolution = remote_mons[rdname].get("resolution", "unknown")
                mode = rdcfg.get("mode", "?")
                if mode == "mixed":
                    folder_str = ", ".join(rdcfg.get("mixed_folders", [])) or "None"
                elif mode == "specific_image":
                    folder_str = rdcfg.get("specific_image") or "No selection"
                else:
                    cat = rdcfg.get("image_category", "")
                    folder_str = cat if cat else "All"
                shuffle_str = "Yes" if rdcfg.get("shuffle_mode") else "No"
                table_of_displays.append({
                    "dname": rdname,
                    "resolution": resolution,
                    "mode": mode,
                    "folders": folder_str,
                    "shuffle": shuffle_str
                })
            remote_displays.append({
                "name": dev_name,
                "ip": dev_ip,
                "displays": table_of_displays,
                "index": cfg["devices"].index(dev)
            })

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
        monitors=monitors,
        version=APP_VERSION,
        sub_info_line=sub_info_line,
        remote_displays=remote_displays
    )


@main_bp.route("/remote_configure/<int:dev_index>", methods=["GET", "POST"])
def remote_configure(dev_index):
    """Main device can configure a sub-device's display settings (like a remote editor)."""
    cfg = load_config()
    if cfg.get("role") != "main":
        return "This device is not 'main'.", 403

    if dev_index < 0 or dev_index >= len(cfg.get("devices", [])):
        return "Invalid device index", 404

    dev_info = cfg["devices"][dev_index]
    dev_ip = dev_info.get("ip")
    dev_name = dev_info.get("name")

    remote_cfg = get_remote_config(dev_ip)
    if not remote_cfg:
        return f"Could not fetch remote config from {dev_ip}", 500

    remote_mons = get_remote_monitors(dev_ip)
    remote_folders = get_remote_subfolders(dev_ip)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_remote":
            new_disp = {}
            for dname, dcfg in remote_cfg.get("displays", {}).items():
                pre = dname + "_"
                new_mode = request.form.get(pre + "mode", dcfg["mode"])
                new_interval_s = request.form.get(pre + "image_interval", str(dcfg["image_interval"]))
                new_cat = request.form.get(pre + "image_category", dcfg.get("image_category", ""))
                new_shuffle = request.form.get(pre + "shuffle_mode", "no")
                new_spec = request.form.get(pre + "specific_image", dcfg.get("specific_image", ""))
                new_rotate_s = request.form.get(pre + "rotate", str(dcfg.get("rotate", 0)))
                mixed_str = request.form.get(pre + "mixed_order", "")
                mixed_list = [x for x in mixed_str.split(",") if x]

                try:
                    new_interval = int(new_interval_s)
                except:
                    new_interval = dcfg["image_interval"]
                try:
                    new_rotate = int(new_rotate_s)
                except:
                    new_rotate = 0

                sub_dict = {
                    "mode": new_mode,
                    "image_interval": new_interval,
                    "image_category": new_cat,
                    "specific_image": new_spec,
                    "shuffle_mode": (new_shuffle == "yes"),
                    "mixed_folders": mixed_list if new_mode == "mixed" else [],
                    "rotate": new_rotate
                }
                new_disp[dname] = sub_dict

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


def get_remote_subfolders(ip):
    """List subfolders from remote or [] if fail."""
    url = f"http://{ip}:8080/list_folders"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log_message(f"Error fetching remote folders from {ip}: {e}")
    return []


@main_bp.route("/sync_config", methods=["GET"])
def sync_config():
    """Return entire config as JSON for remote GET."""
    return load_config()


@main_bp.route("/update_config", methods=["POST"])
def update_config():
    """
    Another device can POST partial config. We merge allowed keys.
    """
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
    """
    If role == 'main', manage sub devices.
    """
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
                log_message(f"Skipping adding device {dev_name} - IP is ourself.")
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
                    remote_displays = pull_displays_from_remote(dev_ip)
                    if remote_displays is not None:
                        dev_info["displays"] = remote_displays
                        save_config(cfg)
                        log_message(f"Pulled remote displays from {dev_ip} into devices[{idx}].displays")
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
    """
    Pulls latest code from GitHub, using the UPDATE_BRANCH from config.py.
    Forces local code to match remote (discarding local changes),
    then if setup.sh changed, re-runs it in 'no-prompt' mode.
    Finally, we'll show a confirmation page.
    """
    cfg = load_config()

    # 1) Save old commit hash for setup.sh
    old_hash = ""
    try:
        old_hash = subprocess.check_output(
            ["git", "rev-parse", f"HEAD:setup.sh"],
            cwd=VIEWER_HOME
        ).decode().strip()
    except Exception as e:
        log_message(f"update_app: Could not get old setup.sh hash: {e}")

    # 2) Perform forced update
    try:
        log_message(f"Starting update: forced reset to origin/{UPDATE_BRANCH}")
        subprocess.check_call(["git", "fetch"], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "checkout", UPDATE_BRANCH], cwd=VIEWER_HOME)
        subprocess.check_call(["git", "reset", "--hard", f"origin/{UPDATE_BRANCH}"], cwd=VIEWER_HOME)
    except subprocess.CalledProcessError as e:
        log_message(f"Git update failed: {e}")
        return "Git update failed. Check logs.", 500

    # 3) Compare new commit hash for setup.sh
    new_hash = ""
    try:
        new_hash = subprocess.check_output(
            ["git", "rev-parse", f"HEAD:setup.sh"],
            cwd=VIEWER_HOME
        ).decode().strip()
    except Exception as e:
        log_message(f"update_app: Could not get new setup.sh hash: {e}")

    # 4) If changed, run the updated setup.sh with --auto-update
    if old_hash and new_hash and old_hash != new_hash:
        log_message("setup.sh changed. Re-running setup.sh in --auto-update mode...")
        try:
            subprocess.check_call(["sudo", "bash", "setup.sh", "--auto-update"], cwd=VIEWER_HOME)
        except subprocess.CalledProcessError as e:
            log_message(f"Re-running setup.sh failed: {e}")

    # 5) done
    log_message("Update completed successfully.")
    return render_template("update_complete.html")


@main_bp.route("/restart_services", methods=["POST", "GET"])
def restart_services():
    """
    Restarts viewer, overlay, and controller services.
    """
    try:
        subprocess.check_call(["sudo", "systemctl", "restart", "viewer.service"])
        subprocess.check_call(["sudo", "systemctl", "restart", "overlay.service"])
        subprocess.check_call(["sudo", "systemctl", "restart", "controller.service"])
        log_message("Services restarted successfully.")
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart services: {e}")
        return "Failed to restart services. Check logs.", 500

    return "Services are restarting now..."
