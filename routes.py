#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import (
    Blueprint, request, redirect, url_for, render_template,
    send_from_directory, send_file, jsonify
)
import os

# Import our config constants and utils
from config import APP_VERSION, WEB_BG, IMAGE_DIR, LOG_PATH
from utils import (
    load_config, save_config, init_config, log_message,
    get_system_stats, get_subfolders, count_files_in_folder,
    detect_monitors, maybe_push_to_subdevices, get_remote_config,
    get_remote_monitors, get_remote_subfolders, push_config_to_subdevice,
    get_hostname, get_ip_address, get_pi_model, get_folder_prefix
)

main_bp = Blueprint("main", __name__, static_folder="static")

@main_bp.route("/stats")
def stats_json():
    """Return real-time system stats as JSON (polled every 10s)."""
    cpu, mem_mb, load1, temp = get_system_stats()
    return jsonify({
        "cpu_percent": cpu,
        "mem_used_mb": round(mem_mb, 1),
        "load_1min": round(load1, 2),
        "temp": temp
    })

@main_bp.route("/list_monitors", methods=["GET"])
def list_monitors():
    """Return the list of detected monitors for this device as JSON."""
    return jsonify(detect_monitors())

@main_bp.route("/list_folders", methods=["GET"])
def list_folders():
    """Return subfolders on this device as JSON."""
    return jsonify(get_subfolders())

@main_bp.route("/images/<path:filename>")
def serve_image(filename):
    """Serve an image from the IMAGE_DIR."""
    return send_from_directory(IMAGE_DIR, filename)

@main_bp.route("/bg_image")
def bg_image():
    """Serve custom background image if it exists."""
    if os.path.exists(WEB_BG):
        return send_file(WEB_BG)
    return "", 404

@main_bp.route("/download_log")
def download_log():
    """Download the raw log file."""
    if os.path.exists(LOG_PATH):
        return send_file(LOG_PATH, as_attachment=True)
    return "No log file found", 404

@main_bp.route("/upload_bg", methods=["POST"])
def upload_bg():
    """Upload a background image (custom theme)."""
    f = request.files.get("bg_image")
    if f:
        f.save(WEB_BG)
    return redirect(url_for("main.settings"))

@main_bp.route("/upload_media", methods=["GET", "POST"])
def upload_media():
    """
    Upload new GIFs/images (multiple files).
    Rename using prefix + zero-padded numbering.
    """
    cfg = load_config()
    subfolders = get_subfolders()
    if request.method == "GET":
        # Just render the upload form
        return render_template(
            "upload_media.html",
            theme=cfg.get("theme", "dark"),
            subfolders=subfolders
        )

    files = request.files.getlist("mediafiles")
    if not files or len(files) == 0:
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
        if ext not in [".gif", ".jpg", ".jpeg", ".png"]:
            log_message(f"Skipped file (unsupported): {original_name}")
            continue
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
    next_num = max_num + 1
    return f"{prefix}{next_num:03d}{desired_ext}"

@main_bp.route("/restart_viewer", methods=["POST"])
def restart_viewer():
    """Restart the viewer.service via systemctl."""
    try:
        subprocess.check_output(["sudo", "systemctl", "restart", "viewer.service"])
        return redirect(url_for("main.index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart viewer.service: {e}", 500

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
            # clear main_ip if we are main
            cfg["main_ip"] = ""

        save_config(cfg)
        maybe_push_to_subdevices(cfg)

        # If user uploaded a custom BG:
        f = request.files.get("bg_image")
        if f:
            f.save(WEB_BG)

        return redirect(url_for("main.settings"))

    return render_template(
        "settings.html",
        theme=cfg.get("theme", "dark"),
        cfg=cfg
    )

@main_bp.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    monitors = detect_monitors()

    # Sync local config with connected monitors
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
        else:
            if "rotate" not in cfg["displays"][m]:
                cfg["displays"][m]["rotate"] = 0

    remove_list = []
    for existing_disp in list(cfg["displays"].keys()):
        if existing_disp not in monitors:
            remove_list.append(existing_disp)
    for r in remove_list:
        del cfg["displays"][r]

    save_config(cfg)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_displays":
            for disp_name in cfg["displays"]:
                pre = disp_name + "_"
                disp_cfg = cfg["displays"][disp_name]
                mode = request.form.get(pre + "mode", disp_cfg["mode"])
                interval_str = request.form.get(pre + "image_interval", str(disp_cfg["image_interval"]))
                cat = request.form.get(pre + "image_category", disp_cfg["image_category"])
                shuffle_val = request.form.get(pre + "shuffle_mode", "no")
                spec_img = request.form.get(pre + "specific_image", disp_cfg["specific_image"])
                rotate_str = request.form.get(pre + "rotate", "0")
                mixed_order_str = request.form.get(pre + "mixed_order", "")
                mixed_order_list = [x for x in mixed_order_str.split(",") if x]

                try:
                    interval = int(interval_str)
                except:
                    interval = disp_cfg["image_interval"]
                try:
                    rotate_val = int(rotate_str)
                except:
                    rotate_val = 0

                shuffle_b = (shuffle_val == "yes")

                disp_cfg["mode"] = mode
                disp_cfg["image_interval"] = interval
                disp_cfg["image_category"] = cat
                disp_cfg["shuffle_mode"] = shuffle_b
                disp_cfg["specific_image"] = spec_img
                disp_cfg["rotate"] = rotate_val

                if mode == "mixed":
                    disp_cfg["mixed_folders"] = mixed_order_list
                else:
                    disp_cfg["mixed_folders"] = []

            save_config(cfg)
            maybe_push_to_subdevices(cfg)
            return redirect(url_for("main.index"))

    # Gather info for local displays
    folder_counts = {}
    for sf in get_subfolders():
        folder_path = os.path.join(IMAGE_DIR, sf)
        folder_counts[sf] = count_files_in_folder(folder_path)

    display_images = {}
    for dname, dcfg in cfg["displays"].items():
        # If 'specific_image' mode, gather the sorted file list in the chosen category
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

    # If sub device, show note about main device
    sub_info_line = ""
    if cfg.get("role") == "sub":
        sub_info_line = "This device is SUB"
        if cfg.get("main_ip"):
            sub_info_line += f" - Main IP: {cfg['main_ip']}"

    # If main, gather remote info
    remote_displays = []
    if cfg.get("role") == "main":
        for dev in cfg.get("devices", []):
            dev_ip = dev.get("ip")
            dev_name = dev.get("name")
            if not dev_ip:
                continue
            remote_cfg = get_remote_config(dev_ip)
            if not remote_cfg:
                continue
            remote_mons = get_remote_monitors(dev_ip)

            rdisplays = []
            for rdname, rdcfg in remote_cfg.get("displays", {}).items():
                resolution = "unknown"
                if remote_mons and rdname in remote_mons:
                    resolution = remote_mons[rdname].get("resolution", "unknown")

                if rdcfg["mode"] == "mixed":
                    folder_str = ", ".join(rdcfg.get("mixed_folders", [])) or "None"
                elif rdcfg["mode"] == "specific_image":
                    folder_str = rdcfg.get("specific_image") or "No selection"
                else:
                    cat = rdcfg.get("image_category", "")
                    folder_str = cat if cat else "All"

                shuffle_str = "Yes" if rdcfg.get("shuffle_mode") else "No"

                rdisplays.append({
                    "dname": rdname,
                    "resolution": resolution,
                    "mode": rdcfg.get("mode", "?"),
                    "folders": folder_str,
                    "shuffle": shuffle_str,
                })

            remote_displays.append({
                "name": dev_name,
                "ip": dev_ip,
                "displays": rdisplays,
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
    """Allows the main device to configure the sub-device's display settings."""
    cfg = load_config()
    if cfg.get("role") != "main":
        return "This device is not 'main', cannot configure remote devices.", 403

    if dev_index < 0 or dev_index >= len(cfg.get("devices", [])):
        return "Invalid device index", 404

    dev_info = cfg["devices"][dev_index]
    dev_ip = dev_info.get("ip")
    dev_name = dev_info.get("name")

    remote_cfg = get_remote_config(dev_ip)
    if not remote_cfg:
        return f"Could not fetch remote config from {dev_ip}", 500

    remote_mons = get_remote_monitors(dev_ip) or {}
    remote_folders = get_remote_subfolders(dev_ip)

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_remote":
            for disp_name, disp_cfg in remote_cfg.get("displays", {}).items():
                pre = disp_name + "_"
                mode = request.form.get(pre + "mode", disp_cfg["mode"])
                interval_str = request.form.get(pre + "image_interval", str(disp_cfg["image_interval"]))
                cat = request.form.get(pre + "image_category", disp_cfg["image_category"])
                shuffle_val = request.form.get(pre + "shuffle_mode", "no")
                spec_img = request.form.get(pre + "specific_image", disp_cfg["specific_image"])
                rotate_str = request.form.get(pre + "rotate", str(disp_cfg.get("rotate", 0)))
                mixed_order_str = request.form.get(pre + "mixed_order", "")
                mixed_order_list = [x for x in mixed_order_str.split(",") if x]

                try:
                    interval = int(interval_str)
                except:
                    interval = disp_cfg["image_interval"]
                try:
                    rotate_val = int(rotate_str)
                except:
                    rotate_val = 0
                shuffle_b = (shuffle_val == "yes")

                disp_cfg["mode"] = mode
                disp_cfg["image_interval"] = interval
                disp_cfg["image_category"] = cat
                disp_cfg["shuffle_mode"] = shuffle_b
                disp_cfg["specific_image"] = spec_img
                disp_cfg["rotate"] = rotate_val

                if mode == "mixed":
                    disp_cfg["mixed_folders"] = mixed_order_list
                else:
                    disp_cfg["mixed_folders"] = []

            # push updated config to sub
            push_config_to_subdevice(dev_ip, remote_cfg)
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
    """Fetch subfolders from a remote device, or [] on fail."""
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
    """Return this device's local config as JSON."""
    cfg = load_config()
    return jsonify(cfg)

@main_bp.route("/update_config", methods=["POST"])
def update_config():
    """
    Another device can POST here with JSON to overwrite local config.
    Then we save and push changes if needed.
    """
    new_cfg = request.get_json()
    if not new_cfg:
        return "No JSON received", 400

    save_config(new_cfg)
    log_message("Local config updated via /update_config")
    cfg = load_config()
    maybe_push_to_subdevices(cfg)
    return "Config updated", 200

@main_bp.route("/device_manager", methods=["GET", "POST"])
def device_manager():
    """
    If role == 'main', manage sub devices: add, remove, push/pull, configure.
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
            # If user tries to add ourselves, skip
            if dev_ip == local_ip:
                log_message(f"Skipping adding device {dev_name} because IP is local.")
            else:
                if "devices" not in cfg:
                    cfg["devices"] = []
                # Add new device
                cfg["devices"].append({"name": dev_name, "ip": dev_ip})
                save_config(cfg)
                log_message(f"Added sub device: {dev_name} ({dev_ip})")

        elif action.startswith("remove_"):
            idx_str = action.replace("remove_", "")
            try:
                idx = int(idx_str)
                if 0 <= idx < len(cfg.get("devices", [])):
                    removed_dev = cfg["devices"].pop(idx)
                    save_config(cfg)
                    log_message(f"Removed sub device: {removed_dev}")
            except:
                pass

        elif action.startswith("push_"):
            idx_str = action.replace("push_", "")
            try:
                idx = int(idx_str)
                dev_info = cfg["devices"][idx]
                push_config_to_subdevice(dev_info["ip"], cfg)
            except:
                pass

        elif action.startswith("pull_"):
            idx_str = action.replace("pull_", "")
            try:
                idx = int(idx_str)
                dev_info = cfg["devices"][idx]
                remote_cfg = get_remote_config(dev_info["ip"])
                if remote_cfg:
                    save_config(remote_cfg)
                    log_message(f"Pulled config from {dev_info['ip']} -> local overwrite.")
                    new_local = load_config()
                    maybe_push_to_subdevices(new_local)
            except:
                pass

        return redirect(url_for("main.device_manager"))

    return render_template(
        "device_manager.html",
        cfg=cfg,
        theme=cfg.get("theme", "dark")
    )
