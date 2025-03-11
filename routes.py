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
    # For compatibility, we can just return a fake dictionary
    return jsonify({"Display0": {"resolution":"1920x1080","offset_x":0,"offset_y":0}})

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

@main_bp.route("/upload_media", methods=["GET","POST"])
def upload_media():
    cfg = load_config()
    theme = cfg.get("theme","dark")
    subfolders = get_subfolders()
    if request.method=="GET":
        return render_template("upload_media.html", theme=theme, subfolders=subfolders)

    files = request.files.getlist("mediafiles")
    if not files:
        return "No files selected",400
    subfolder = request.form.get("subfolder","")
    new_subfolder = request.form.get("new_subfolder","").strip()
    if new_subfolder:
        subfolder = new_subfolder
    target_dir = os.path.join(IMAGE_DIR, subfolder)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)

    for f in files:
        if not f.filename:
            continue
        lf = f.filename.lower()
        if not lf.endswith((".jpg",".jpeg",".png",".gif")):
            log_message(f"Unsupported file type: {f.filename}")
            continue
        final_path = os.path.join(target_dir, f.filename)
        f.save(final_path)
        log_message(f"Uploaded file: {final_path}")

    return redirect(url_for("main.index"))

@main_bp.route("/restart_viewer", methods=["POST"])
def restart_viewer():
    try:
        # Now we have a single service "piviewer.service"
        subprocess.check_output(["sudo","systemctl","restart","piviewer.service"])
        return redirect(url_for("main.index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart service: {e}",500

@main_bp.route("/settings", methods=["GET","POST"])
def settings():
    cfg = load_config()
    if "weather" not in cfg:
        cfg["weather"] = {}

    if request.method=="POST":
        new_theme = request.form.get("theme","dark")
        new_role = request.form.get("role","main")
        cfg["theme"] = new_theme
        cfg["role"] = new_role
        if new_role=="sub":
            cfg["main_ip"] = request.form.get("main_ip","").strip()
        else:
            cfg["main_ip"]=""

        if new_theme=="custom":
            if "bg_image" in request.files:
                f = request.files["bg_image"]
                if f and f.filename:
                    f.save(WEB_BG)

        w_api = request.form.get("weather_api_key","").strip()
        w_zip = request.form.get("weather_zip_code","").strip()
        w_cc = request.form.get("weather_country_code","").strip()
        w_lat = request.form.get("weather_lat","").strip()
        w_lon = request.form.get("weather_lon","").strip()

        if "weather" not in cfg:
            cfg["weather"]={}
        cfg["weather"]["api_key"] = w_api
        cfg["weather"]["zip_code"] = w_zip
        cfg["weather"]["country_code"] = w_cc

        try:
            cfg["weather"]["lat"] = float(w_lat)
        except:
            cfg["weather"]["lat"]=None
        try:
            cfg["weather"]["lon"] = float(w_lon)
        except:
            cfg["weather"]["lon"]=None

        save_config(cfg)
        return redirect(url_for("main.settings"))
    else:
        return render_template("settings.html",
            theme=cfg.get("theme","dark"),
            cfg=cfg,
            update_branch=UPDATE_BRANCH
        )

@main_bp.route("/configure_spotify", methods=["GET","POST"])
def configure_spotify():
    cfg = load_config()
    if "spotify" not in cfg:
        cfg["spotify"]={}
    if request.method=="POST":
        cid = request.form.get("client_id","").strip()
        csec = request.form.get("client_secret","").strip()
        ruri = request.form.get("redirect_uri","").strip()
        scope = request.form.get("scope","user-read-currently-playing user-read-playback-state").strip()
        cfg["spotify"] = {
            "client_id": cid,
            "client_secret": csec,
            "redirect_uri": ruri,
            "scope": scope
        }
        save_config(cfg)
        return redirect(url_for("main.configure_spotify"))
    else:
        return render_template("configure_spotify.html", 
            spotify=cfg["spotify"],
            theme=cfg.get("theme","dark")
        )

@main_bp.route("/spotify_auth")
def spotify_auth():
    from spotipy.oauth2 import SpotifyOAuth
    cfg = load_config()
    sp_cfg = cfg.get("spotify",{})
    cid = sp_cfg.get("client_id","")
    csec = sp_cfg.get("client_secret","")
    ruri = sp_cfg.get("redirect_uri","")
    scope = sp_cfg.get("scope","user-read-currently-playing user-read-playback-state")
    if not (cid and csec and ruri):
        return "Spotify config incomplete",400
    sp_oauth = SpotifyOAuth(client_id=cid, client_secret=csec,
                            redirect_uri=ruri, scope=scope,
                            cache_path=".spotify_cache")
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@main_bp.route("/callback")
def callback():
    from spotipy.oauth2 import SpotifyOAuth
    cfg = load_config()
    sp_cfg = cfg.get("spotify",{})
    cid = sp_cfg.get("client_id","")
    csec = sp_cfg.get("client_secret","")
    ruri = sp_cfg.get("redirect_uri","")
    scope = sp_cfg.get("scope","user-read-currently-playing user-read-playback-state")
    sp_oauth = SpotifyOAuth(client_id=cid, client_secret=csec,
                            redirect_uri=ruri, scope=scope,
                            cache_path=".spotify_cache")
    code = request.args.get("code")
    if not code:
        return "Authorization failed: no code provided",400
    try:
        token_info = sp_oauth.get_access_token(code)
    except Exception as e:
        log_message(f"Spotify callback error: {e}")
        return "Spotify callback error",500
    return redirect(url_for("main.configure_spotify"))

@main_bp.route("/overlay_config", methods=["GET","POST"])
def overlay_config():
    cfg = load_config()
    if "overlay" not in cfg:
        cfg["overlay"]={}
    over = cfg["overlay"]
    if request.method=="POST":
        action = request.form.get("action","")
        if action=="select_monitor":
            over["monitor_selection"] = request.form.get("monitor_selection","All")
            save_config(cfg)
            return redirect(url_for("main.overlay_config"))
        elif action=="save_overlay":
            over["overlay_enabled"] = ("overlay_enabled" in request.form)
            over["clock_enabled"] = ("clock_enabled" in request.form)
            over["weather_enabled"] = ("weather_enabled" in request.form)
            over["background_enabled"] = ("background_enabled" in request.form)
            try:
                over["clock_font_size"] = int(request.form.get("clock_font_size","26"))
            except:
                over["clock_font_size"]=26
            try:
                over["weather_font_size"] = int(request.form.get("weather_font_size","22"))
            except:
                over["weather_font_size"]=22
            over["font_color"] = request.form.get("font_color","#FFFFFF")
            over["layout_style"] = request.form.get("layout_style","stacked")
            try:
                over["padding_x"]=int(request.form.get("padding_x","8"))
            except:
                over["padding_x"]=8
            try:
                over["padding_y"]=int(request.form.get("padding_y","6"))
            except:
                over["padding_y"]=6
            over["show_desc"] = ("show_desc" in request.form)
            over["show_temp"] = ("show_temp" in request.form)
            over["show_feels_like"] = ("show_feels_like" in request.form)
            over["show_humidity"] = ("show_humidity" in request.form)
            try:
                over["offset_x"]=int(request.form.get("offset_x","20"))
            except:
                over["offset_x"]=20
            try:
                over["offset_y"]=int(request.form.get("offset_y","20"))
            except:
                over["offset_y"]=20
            try:
                wval = int(request.form.get("overlay_width","300"))
                over["overlay_width"]=wval
            except:
                over["overlay_width"]=300
            try:
                hval = int(request.form.get("overlay_height","150"))
                over["overlay_height"]=hval
            except:
                over["overlay_height"]=150
            over["bg_color"] = request.form.get("bg_color","#000000")
            try:
                over["bg_opacity"] = float(request.form.get("bg_opacity","0.4"))
            except:
                over["bg_opacity"]=0.4
            save_config(cfg)
            # previously we restarted overlay.service, now we just restart piviewer
            try:
                subprocess.check_call(["sudo","systemctl","restart","piviewer.service"])
            except subprocess.CalledProcessError as e:
                log_message(f"Failed to restart piviewer.service: {e}")
            return redirect(url_for("main.overlay_config"))
    return render_template("overlay.html",
        theme=cfg.get("theme","dark"),
        overlay=over
    )

@main_bp.route("/", methods=["GET","POST"])
def index():
    cfg = load_config()
    # we no longer detect monitors with xrandr; just assume "Display0" etc.
    if "displays" not in cfg:
        cfg["displays"]={}
    # ensure at least 1 display
    if not cfg["displays"]:
        cfg["displays"]["Display0"]={
            "mode":"random_image",
            "image_interval":60,
            "image_category":"",
            "specific_image":"",
            "shuffle_mode":False,
            "mixed_folders":[],
            "rotate":0
        }
        save_config(cfg)

    if request.method=="POST":
        action = request.form.get("action","")
        if action=="update_displays":
            for dname in cfg["displays"]:
                pre = dname+"_"
                dcfg = cfg["displays"][dname]
                new_mode = request.form.get(pre+"mode", dcfg["mode"])
                new_interval_s = request.form.get(pre+"image_interval", str(dcfg["image_interval"]))
                new_cat = request.form.get(pre+"image_category", dcfg["image_category"])
                shuffle_val = request.form.get(pre+"shuffle_mode","no")
                new_spec = request.form.get(pre+"specific_image", dcfg["specific_image"])
                rotate_str = request.form.get(pre+"rotate","0")
                mixed_str = request.form.get(pre+"mixed_order","")
                mixed_list = [x for x in mixed_str.split(",") if x]

                try:
                    new_interval=int(new_interval_s)
                except:
                    new_interval=dcfg["image_interval"]
                try:
                    new_rotate=int(rotate_str)
                except:
                    new_rotate=0

                dcfg["mode"]=new_mode
                dcfg["image_interval"]=new_interval
                dcfg["image_category"]=new_cat
                dcfg["shuffle_mode"]=(shuffle_val=="yes")
                dcfg["specific_image"]=new_spec
                dcfg["rotate"]=new_rotate

                if new_mode=="mixed":
                    dcfg["mixed_folders"]=mixed_list
                else:
                    dcfg["mixed_folders"]=[]

            save_config(cfg)
            # we can optionally restart piviewer
            try:
                subprocess.check_call(["sudo","systemctl","restart","piviewer.service"])
            except:
                pass
            return redirect(url_for("main.index"))

    folder_counts={}
    for sf in get_subfolders():
        folder_counts[sf]=count_files_in_folder(os.path.join(IMAGE_DIR,sf))

    # we won't gather display_images like old approach, because the new GUI loads them itself
    # but let's keep the code for partial backward compatibility:
    display_images = {}
    for dname,dcfg in cfg["displays"].items():
        display_images[dname]=[]

    cpu,mem_mb,load1,temp = get_system_stats()
    host = get_hostname()
    ipaddr = get_ip_address()
    model = get_pi_model()
    theme = cfg.get("theme","dark")

    sub_info_line=""
    if cfg.get("role")=="sub":
        sub_info_line="This device is SUB"
        if cfg["main_ip"]:
            sub_info_line+=f" - Main IP: {cfg['main_ip']}"

    return render_template("index.html",
        cfg=cfg,
        subfolders=get_subfolders(),
        folder_counts=folder_counts,
        display_images=display_images,
        cpu=cpu,
        mem_mb=round(mem_mb,1),
        load1=round(load1,2),
        temp=temp,
        host=host,
        ipaddr=ipaddr,
        model=model,
        theme=theme,
        version=APP_VERSION,
        sub_info_line=sub_info_line
    )

@main_bp.route("/remote_configure/<int:dev_index>", methods=["GET","POST"])
def remote_configure(dev_index):
    cfg = load_config()
    if cfg.get("role")!="main":
        return "This device is not 'main'.",403

    if dev_index<0 or dev_index>=len(cfg.get("devices",[])):
        return "Invalid device index",404

    dev_info = cfg["devices"][dev_index]
    dev_ip = dev_info.get("ip")
    dev_name = dev_info.get("name")

    # in the old approach, we tried to fetch remote config
    remote_cfg = get_remote_config(dev_ip) or {"displays":{}}
    remote_mons = get_remote_monitors(dev_ip)
    remote_folders=[]
    try:
        r = requests.get(f"http://{dev_ip}:8080/list_folders", timeout=5)
        if r.status_code==200:
            remote_folders=r.json()
    except:
        pass

    if request.method=="POST":
        action = request.form.get("action","")
        if action=="update_remote":
            new_disp={}
            for dname,dc in remote_cfg.get("displays",{}).items():
                pre=dname+"_"
                new_mode = request.form.get(pre+"mode", dc.get("mode","random_image"))
                new_int_s = request.form.get(pre+"image_interval", str(dc.get("image_interval",60)))
                new_cat = request.form.get(pre+"image_category", dc.get("image_category",""))
                new_shuffle = request.form.get(pre+"shuffle_mode","no")
                new_spec = request.form.get(pre+"specific_image", dc.get("specific_image",""))
                new_rot_s = request.form.get(pre+"rotate",str(dc.get("rotate",0)))
                mixed_str = request.form.get(pre+"mixed_order","")
                mixed_list = [x for x in mixed_str.split(",") if x]

                try:
                    ni = int(new_int_s)
                except:
                    ni=dc.get("image_interval",60)
                try:
                    nr = int(new_rot_s)
                except:
                    nr=0
                subdict={
                    "mode":new_mode,
                    "image_interval":ni,
                    "image_category":new_cat,
                    "specific_image":new_spec,
                    "shuffle_mode":(new_shuffle=="yes"),
                    "mixed_folders":mixed_list if new_mode=="mixed" else [],
                    "rotate":nr
                }
                new_disp[dname]=subdict
            push_displays_to_remote(dev_ip, new_disp)
            return redirect(url_for("main.remote_configure", dev_index=dev_index))

    return render_template("remote_configure.html",
        dev_name=dev_name,
        dev_ip=dev_ip,
        remote_cfg=remote_cfg,
        remote_mons=remote_mons,
        remote_folders=remote_folders
    )


@main_bp.route("/sync_config", methods=["GET"])
def sync_config():
    return load_config()

@main_bp.route("/update_config", methods=["POST"])
def update_config():
    incoming = request.get_json()
    if not incoming:
        return "No JSON received",400
    cfg = load_config()
    if "displays" in incoming:
        cfg["displays"]=incoming["displays"]
    if "theme" in incoming:
        cfg["theme"]=incoming["theme"]
    save_config(cfg)
    log_message("Local config partially updated via /update_config")
    return "Config updated",200

@main_bp.route("/device_manager", methods=["GET","POST"])
def device_manager():
    cfg = load_config()
    if cfg.get("role")!="main":
        return "This device is not 'main'.",403

    local_ip = get_ip_address()
    if request.method=="POST":
        action = request.form.get("action","")
        dev_name = request.form.get("dev_name","").strip()
        dev_ip = request.form.get("dev_ip","").strip()
        if action=="add_device" and dev_name and dev_ip:
            if dev_ip==local_ip:
                log_message(f"Skipping adding device {dev_name} - same IP as local.")
            else:
                if "devices" not in cfg:
                    cfg["devices"]=[]
                cfg["devices"].append({
                    "name": dev_name,
                    "ip": dev_ip,
                    "displays": {}
                })
                save_config(cfg)
                log_message(f"Added sub device: {dev_name} ({dev_ip})")
        elif action.startswith("remove_"):
            idx_str = action.replace("remove_","")
            try:
                idx = int(idx_str)
                if 0<=idx<len(cfg["devices"]):
                    removed = cfg["devices"].pop(idx)
                    save_config(cfg)
                    log_message(f"Removed sub device: {removed}")
            except:
                pass
        elif action.startswith("push_"):
            idx_str = action.replace("push_","")
            try:
                idx=int(idx_str)
                dev_info=cfg["devices"][idx]
                dev_ip=dev_info.get("ip")
                if dev_ip:
                    push_displays_to_remote(dev_ip, dev_info.get("displays",{}))
            except Exception as e:
                log_message(f"Push error: {e}")
        elif action.startswith("pull_"):
            idx_str=action.replace("pull_","")
            try:
                idx=int(idx_str)
                dev_info=cfg["devices"][idx]
                dev_ip=dev_info.get("ip")
                if dev_ip:
                    rd = pull_displays_from_remote(dev_ip)
                    if rd is not None:
                        dev_info["displays"]=rd
                        save_config(cfg)
                        log_message(f"Pulled remote displays from {dev_ip} => devices[{idx}]")
            except Exception as e:
                log_message(f"Pull error: {e}")
        return redirect(url_for("main.device_manager"))

    return render_template("device_manager.html",
        cfg=cfg,
        theme=cfg.get("theme","dark")
    )

@main_bp.route("/update_app", methods=["POST"])
def update_app():
    cfg = load_config()
    log_message(f"Starting update: forced reset to origin/{UPDATE_BRANCH}")
    try:
        subprocess.check_call(["git","fetch"], cwd=VIEWER_HOME)
        subprocess.check_call(["git","checkout", UPDATE_BRANCH], cwd=VIEWER_HOME)
        subprocess.check_call(["git","reset","--hard",f"origin/{UPDATE_BRANCH}"],cwd=VIEWER_HOME)
    except subprocess.CalledProcessError as e:
        log_message(f"Git update failed: {e}")
        return "Git update failed. Check logs.",500
    log_message("Update completed successfully.")
    return render_template("update_complete.html")

@main_bp.route("/restart_services", methods=["POST","GET"])
def restart_services():
    try:
        subprocess.check_call(["sudo","systemctl","restart","piviewer.service"])
        subprocess.check_call(["sudo","systemctl","restart","controller.service"])
        log_message("Services restarted.")
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart services: {e}")
        return "Failed to restart services. Check logs.",500
    return "Services are restarting now..."
