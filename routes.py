#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess  # for restart_viewer
import requests
from flask import (
    Blueprint, request, redirect, url_for, render_template,
    send_from_directory, send_file, jsonify
)

from config import APP_VERSION, WEB_BG, IMAGE_DIR, LOG_PATH
from utils import (
    load_config, save_config, init_config, log_message,
    get_system_stats, get_subfolders, count_files_in_folder,
    detect_monitors, get_remote_config, get_remote_monitors,
    pull_displays_from_remote, push_displays_to_remote,   # <--- new partial sync calls
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
    # Return the actual monitor info (including resolution) as detected.
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
    return f"{prefix}{(max_num + 1):03d}{desired_ext}"


@main_bp.route("/restart_viewer", methods=["POST"])
def restart_viewer():
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
            cfg["main_ip"] = ""

        save_config(cfg)
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

    # Sync local config with physically connected monitors
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

    # Remove stale displays
    remove_list = []
    for d in list(cfg["displays"].keys()):
        if d not in monitors:
            remove_list.append(d)
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

                # parse interval
                try:
                    new_interval = int(new_interval_s)
                except:
                    new_interval = dcfg["image_interval"]
                # parse rotate
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

    # Info for local displays
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

    # If sub device
    sub_info_line = ""
    if cfg.get("role") == "sub":
        sub_info_line = "This device is SUB"
        if cfg["main_ip"]:
            sub_info_line += f" - Main IP: {cfg['main_ip']}"

    # If main, gather remote info (just so we can show on index page)
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

            # Build a simplified table of remote "displays"
            table_of_displays = []
            for rdname, rdcfg in rem_cfg.get("displays", {}).items():
                resolution = "unknown"
                # Now remote_mons is a dict like {"HDMI-1": {"resolution": "?"}, ...}
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

    # Pull remote config (full), just for display
    remote_cfg = get_remote_config(dev_ip)
    if not remote_cfg:
        return f"Could not fetch remote config from {dev_ip}", 500

    # Also remote monitors & subfolders
    remote_mons = get_remote_monitors(dev_ip)
    remote_folders = get_remote_subfolders(dev_ip)

    # If we POST, we want to partially update the remote device's "displays"
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update_remote":
            # build a partial "displays" from the posted form
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

            # Push new_disp to remote
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
    Another device can POST partial config. We simply merge it into local.
    If it includes "displays", we overwrite local "displays".
    If it includes "theme", we overwrite local "theme".
    We do NOT overwrite role, main_ip, or devices here.
    """
    incoming = request.get_json()
    if not incoming:
        return "No JSON received", 400

    cfg = load_config()
    # Merge only keys that we allow
    if "displays" in incoming:
        cfg["displays"] = incoming["displays"]
    if "theme" in incoming:
        cfg["theme"] = incoming["theme"]
    # (Add more if you want to allow them from remote)

    save_config(cfg)
    log_message("Local config partially updated via /update_config")
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
            if dev_ip == local_ip:
                log_message(f"Skipping adding device {dev_name} - IP is ourself.")
            else:
                if "devices" not in cfg:
                    cfg["devices"] = []
                # Add new device with empty "displays"
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
            # Pushing local known "devices[idx].displays" to the sub
            idx_str = action.replace("push_", "")
            try:
                idx = int(idx_str)
                dev_info = cfg["devices"][idx]
                dev_ip = dev_info.get("ip")
                if dev_ip:
                    # we push only dev_info["displays"] to that sub device
                    push_displays_to_remote(dev_ip, dev_info.get("displays", {}))
            except Exception as e:
                log_message(f"Push error: {e}")

        elif action.startswith("pull_"):
            # Pull the remote's displays and store them in devices[idx].displays
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
