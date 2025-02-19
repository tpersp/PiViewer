#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import json
import psutil
import subprocess
from datetime import datetime
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    send_from_directory, send_file, jsonify
)

# ------------------------------------------------------------------
# Read environment-based paths (fallbacks if not set)
# ------------------------------------------------------------------
VIEWER_HOME = os.environ.get("VIEWER_HOME", "/home/pi/viewer")
IMAGE_DIR   = os.environ.get("IMAGE_DIR", "/mnt/PiViewers")

CONFIG_PATH = os.path.join(VIEWER_HOME, "viewerconfig.json")
LOG_PATH    = os.path.join(VIEWER_HOME, "viewer.log")
WEB_BG      = os.path.join(VIEWER_HOME, "web_bg.jpg")

app = Flask(__name__, static_folder='static')

# ------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------

def init_config():
    """Initialize config file if missing."""
    if not os.path.exists(CONFIG_PATH):
        default_cfg = {
            "theme": "dark",
            "displays": {}
        }
        monitors = detect_monitors()
        for m, mdata in monitors.items():
            default_cfg["displays"][m] = {
                "mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": []
            }
        save_config(default_cfg)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        init_config()
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def detect_monitors():
    """
    Use xrandr --listmonitors to detect connected monitors.
    Returns a dictionary like:
      {
        "HDMI-1": {"resolution": "1920x1080", "name": "HDMI-1"},
        "HDMI-2": {"resolution": "800x600", "name": "HDMI-2"}
      }
    Fallback if none found.
    """
    try:
        out = subprocess.check_output(["xrandr", "--listmonitors"]).decode().strip()
        lines = out.split("\n")
        if len(lines) <= 1:
            return {"Display0": {"resolution": "unknown", "name": "Display0"}}

        monitors = {}
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            geometry_idx = None
            for i, p in enumerate(parts):
                if 'x' in p and '/' in p:
                    geometry_idx = i
                    break
            if geometry_idx is None:
                name_clean = parts[2].strip("+*")
                monitors[name_clean] = {"resolution": "unknown", "name": name_clean}
                continue

            geometry_part = parts[geometry_idx]
            actual_name = parts[-1]
            try:
                left, right = geometry_part.split("x")
                width = left.split("/")[0]
                right_split_plus = right.split("+")[0]
                height = right_split_plus.split("/")[0]
                resolution = f"{width}x{height}"
            except:
                resolution = "unknown"

            name_clean = actual_name.strip("+*")
            monitors[name_clean] = {"resolution": resolution, "name": name_clean}

        if not monitors:
            return {"Display0": {"resolution": "unknown", "name": "Display0"}}
        return monitors
    except:
        return {"Display0": {"resolution": "unknown", "name": "Display0"}}

def get_hostname():
    return subprocess.check_output(["hostname"]).decode().strip()

def get_ip_address():
    """Get first non-127.0.0.1 IP from `hostname -I`."""
    try:
        out = subprocess.check_output(["hostname", "-I"]).decode().strip()
        ips = out.split()
        for ip in ips:
            if not ip.startswith("127."):
                return ip
        return "Unknown"
    except:
        return "Unknown"

def get_pi_model():
    path = "/proc/device-tree/model"
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read().strip()
    return "Unknown Model"

def get_subfolders():
    """Return subfolders in IMAGE_DIR (one level)."""
    try:
        return [
            d for d in os.listdir(IMAGE_DIR)
            if os.path.isdir(os.path.join(IMAGE_DIR, d))
        ]
    except:
        return []

def get_system_stats():
    """
    Return (cpu_percent, mem_used_mb, load_1min, temp).
    """
    cpu = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    mem_used_mb = (mem.total - mem.available) / (1024 * 1024)
    loadavg = os.getloadavg()[0]
    try:
        temp = subprocess.check_output(["vcgencmd", "measure_temp"]).decode().strip()
    except:
        temp = "N/A"
    return (cpu, mem_used_mb, loadavg, temp)

def get_recent_logs():
    """
    Return the last 50 lines in reverse order (newest first).
    """
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, "r") as f:
        lines = f.readlines()
    lines = lines[-50:]
    lines.reverse()
    return lines

def log_message(msg):
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now()}: {msg}\n")

def get_folder_prefix(folder_name):
    if not folder_name.strip():
        return "misc"
    words = folder_name.split()
    letters = [w[0].lower() for w in words if w]
    return "".join(letters)

def count_files_in_folder(folder_path):
    """Return how many valid image files are in a folder."""
    if not os.path.isdir(folder_path):
        return 0
    cnt = 0
    for f in os.listdir(folder_path):
        lf = f.lower()
        if lf.endswith((".png", ".jpg", ".jpeg", ".gif")):
            cnt += 1
    return cnt

# ------------------------------------------------------------------
# Additional Endpoints
# ------------------------------------------------------------------

@app.route("/stats")
def stats_json():
    """
    Return real-time system stats as JSON (polled every 10s).
    """
    cpu, mem_mb, load1, temp = get_system_stats()
    return jsonify({
        "cpu_percent": cpu,
        "mem_used_mb": round(mem_mb, 1),
        "load_1min": round(load1, 2),
        "temp": temp
    })

# ------------------------------------------------------------------
# Serve Images, Logs, Etc.
# ------------------------------------------------------------------

@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(IMAGE_DIR, filename)

@app.route("/bg_image")
def bg_image():
    if os.path.exists(WEB_BG):
        return send_file(WEB_BG)
    return "", 404

@app.route("/download_log")
def download_log():
    """
    Download the raw log file (in normal chronological order).
    """
    if os.path.exists(LOG_PATH):
        return send_file(LOG_PATH, as_attachment=True)
    return "No log file found", 404

# ------------------------------------------------------------------
# Upload Handlers
# ------------------------------------------------------------------

@app.route("/upload_bg", methods=["POST"])
def upload_bg():
    """
    Upload a background image (custom theme).
    """
    f = request.files.get("bg_image")
    if f:
        f.save(WEB_BG)
    return redirect(url_for("settings"))

@app.route("/upload_media", methods=["GET", "POST"])
def upload_media():
    """
    Upload new GIFs/images (multiple files).
    Rename using prefix + zero-padded numbering.
    """
    cfg = load_config()
    subfolders = get_subfolders()

    if request.method == "GET":
        HTML = """
        <!DOCTYPE html>
        <html>
        <head>
          <title>Upload Media</title>
          <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
        </head>
        <body class="{{ theme }}">
        <div class="container">
          <h1>Upload New Media (GIF/PNG/JPG)</h1>
          <form method="POST" enctype="multipart/form-data">
            <label>Select Subfolder:</label><br>
            <select name="subfolder">
              {% for sf in subfolders %}
                <option value="{{ sf }}">{{ sf }}</option>
              {% endfor %}
            </select>
            <br><br>
            (Optional) Create new subfolder:
            <input type="text" name="new_subfolder" placeholder="New Folder Name">
            <br><br>
            <input type="file" name="mediafiles" accept=".gif,.png,.jpg,.jpeg" multiple>
            <br><br>
            <button type="submit">Upload</button>
          </form>
          <br>
          <a href="{{ url_for('index') }}">
            <button>Return to Main</button>
          </a>
        </div>
        </body>
        </html>
        """
        return render_template_string(HTML, theme=cfg.get("theme", "dark"), subfolders=subfolders)

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
        final_path = os.path.join(target_dir, new_filename)
        file.save(final_path)
        log_message(f"Uploaded file saved to: {final_path}")

    return redirect(url_for("index"))

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

# ------------------------------------------------------------------
# Restart Viewer
# ------------------------------------------------------------------

@app.route("/restart_viewer", methods=["POST"])
def restart_viewer():
    try:
        subprocess.check_output(["sudo", "systemctl", "restart", "viewer.service"])
        return redirect(url_for("index"))
    except subprocess.CalledProcessError as e:
        return f"Failed to restart viewer.service: {e}", 500

# ------------------------------------------------------------------
# Settings Page
# ------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    cfg = load_config()
    if request.method == "POST":
        new_theme = request.form.get("theme", "dark")
        cfg["theme"] = new_theme
        save_config(cfg)
        f = request.files.get("bg_image")
        if f:
            f.save(WEB_BG)
        return redirect(url_for("settings"))

    HTML = """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Settings</title>
      <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
    </head>
    <body class="{{ theme }}">
    <div class="container">
      <h1>Settings</h1>
      <form method="POST" enctype="multipart/form-data">
        <label>Theme:</label><br>
        <select name="theme">
          <option value="dark" {% if cfg.theme=="dark" %}selected{% endif %}>Dark</option>
          <option value="light" {% if cfg.theme=="light" %}selected{% endif %}>Light</option>
          <option value="custom" {% if cfg.theme=="custom" %}selected{% endif %}>Custom</option>
        </select>
        <br><br>
        {% if cfg.theme=="custom" %}
        <label>Upload Custom BG:</label><br>
        <input type="file" name="bg_image" accept="image/*"><br><br>
        {% endif %}
        <button type="submit">Save Settings</button>
      </form>
      <br>
      <a href="{{ url_for('download_log') }}"><button>Download Log</button></a>
      <br><br>
      <a href="{{ url_for('index') }}"><button>Back to Main</button></a>
    </div>
    </body>
    </html>
    """
    return render_template_string(HTML, theme=cfg.get("theme", "dark"), cfg=cfg)

# ------------------------------------------------------------------
# Main Page
# ------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    cfg = load_config()
    monitors = detect_monitors()

    # Sync config with actual monitors
    for m in monitors:
        if m not in cfg["displays"]:
            cfg["displays"][m] = {
                "mode": "random_image",
                "image_interval": 60,
                "image_category": "",
                "specific_image": "",
                "shuffle_mode": False,
                "mixed_folders": []
            }
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
                mode = request.form.get(pre + "mode", cfg["displays"][disp_name]["mode"])
                interval_str = request.form.get(pre + "image_interval", str(cfg["displays"][disp_name]["image_interval"]))
                cat = request.form.get(pre + "image_category", cfg["displays"][disp_name]["image_category"])
                shuffle_str = request.form.get(pre + "shuffle_mode", "off")
                spec_img = request.form.get(pre + "specific_image", cfg["displays"][disp_name]["specific_image"])
                mixed_order_str = request.form.get(pre + "mixed_order", "")
                mixed_order_list = [x for x in mixed_order_str.split(",") if x]
                try:
                    interval = int(interval_str)
                except:
                    interval = cfg["displays"][disp_name]["image_interval"]
                shuffle_b = (shuffle_str == "on")
                disp_cfg = cfg["displays"][disp_name]
                disp_cfg["mode"] = mode
                disp_cfg["image_interval"] = interval
                disp_cfg["image_category"] = cat
                disp_cfg["shuffle_mode"] = shuffle_b
                disp_cfg["specific_image"] = spec_img
                if mode == "mixed":
                    disp_cfg["mixed_folders"] = mixed_order_list
                else:
                    disp_cfg["mixed_folders"] = []
            save_config(cfg)
            return redirect(url_for("index"))

    folder_counts = {}
    for sf in get_subfolders():
        folder_path = os.path.join(IMAGE_DIR, sf)
        folder_counts[sf] = count_files_in_folder(folder_path)

    display_images = {}
    for dname, dcfg in cfg["displays"].items():
        if dcfg["mode"] == "specific_image":
            cat = dcfg.get("image_category", "")
            path = os.path.join(IMAGE_DIR, cat)
            if cat and os.path.exists(path):
                fs = [f for f in os.listdir(path) if f.lower().endswith((".jpg",".jpeg",".png",".gif"))]
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

    HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Viewer Controller</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
  <style>
    .help-icon {
      position: absolute;
      top: 6px;
      right: 10px;
      font-size: 18px;
      cursor: pointer;
      padding: 4px;
      color: #F2F2F2;
      background: rgba(0,0,0,0.3);
      border-radius: 50%;
    }
    .help-box {
      display: none;
      position: absolute;
      top: 35px;
      right: 10px;
      width: 200px;
      background: #333;
      color: #FFF;
      border: 1px solid #555;
      border-radius: 6px;
      padding: 10px;
      z-index: 999;
    }
    .help-box p {
      margin: 0;
      font-size: 14px;
    }
    .display-card {
      position: relative;
    }
    .dnd-column {
      width: 250px;
      min-height: 150px;
      background: rgba(255,255,255,0.1);
      border: 2px solid #666;
      border-radius: 6px;
      margin: 5px;
      padding: 10px;
    }
    .dnd-column h4 {
      margin-top: 0;
      margin-bottom: 10px;
    }
    .lazy-thumb {
      width: 60px;
      height: 60px;
      object-fit: cover;
      border: 2px solid #555;
      border-radius: 4px;
      margin: 5px;
    }
    .thumb-img {
      width: 60px;
      height: 60px;
      object-fit: cover;
    }
  </style>
  <script>
    function fetchStats() {
      fetch("/stats")
      .then(r => r.json())
      .then(data => {
        document.getElementById("stat_cpu").textContent = data.cpu_percent + "%";
        document.getElementById("stat_mem").textContent = data.mem_used_mb + "MB";
        document.getElementById("stat_load").textContent = data.load_1min;
        document.getElementById("stat_temp").textContent = data.temp;
      }).catch(e => console.log(e));
    }
    setInterval(fetchStats, 10000);
    window.addEventListener("load", fetchStats);

    function toggleHelpBox(boxID){
      let box = document.getElementById(boxID);
      box.style.display = (box.style.display === "block") ? "none" : "block";
    }

    function initMixedUI(dispName){
      let searchBox = document.getElementById(dispName + "_search");
      let availList = document.getElementById(dispName + "_availList");
      let selList = document.getElementById(dispName + "_selList");
      let hiddenOrder = document.getElementById(dispName + "_mixed_order");
      function sortAvailable(){
        let items = Array.from(availList.querySelectorAll("li"));
        items.sort((a,b)=>{
          let fa = a.getAttribute("data-folder").toLowerCase();
          let fb = b.getAttribute("data-folder").toLowerCase();
          return fa.localeCompare(fb);
        });
        items.forEach(li => availList.appendChild(li));
      }
      searchBox.addEventListener("input", ()=>{
        let txt = searchBox.value.toLowerCase();
        let items = availList.querySelectorAll("li");
        items.forEach(li => {
          if(li.getAttribute("data-folder").toLowerCase().includes(txt)){
            li.style.display="";
          } else {
            li.style.display="none";
          }
        });
      });
      let dragSrcEl=null;
      function handleDragStart(e){
        dragSrcEl = this;
        e.dataTransfer.effectAllowed="move";
        e.dataTransfer.setData("text/html", this.innerHTML);
      }
      function handleDragOver(e){
        if(e.preventDefault) e.preventDefault();
        return false;
      }
      function handleDragEnter(e){ this.classList.add("selected"); }
      function handleDragLeave(e){ this.classList.remove("selected"); }
      function handleDrop(e){
        if(e.stopPropagation) e.stopPropagation();
        if(dragSrcEl!=this){
          let oldHTML = dragSrcEl.innerHTML;
          dragSrcEl.innerHTML = this.innerHTML;
          this.innerHTML = e.dataTransfer.getData("text/html");
        }
        return false;
      }
      function handleDragEnd(e){
        let items = selList.querySelectorAll("li");
        items.forEach(li => li.classList.remove("selected"));
        updateHiddenOrder();
      }
      function addDnDHandlers(item){
        item.addEventListener("dragstart", handleDragStart);
        item.addEventListener("dragenter", handleDragEnter);
        item.addEventListener("dragover", handleDragOver);
        item.addEventListener("dragleave", handleDragLeave);
        item.addEventListener("drop", handleDrop);
        item.addEventListener("dragend", handleDragEnd);
      }
      function moveItem(li, sourceUL, targetUL){
        targetUL.appendChild(li);
        if(targetUL === selList){
          addDnDHandlers(li);
        } else {
          sortAvailable();
        }
        updateHiddenOrder();
      }
      availList.addEventListener("click", e=>{
        if(e.target.tagName==="LI"){
          moveItem(e.target, availList, selList);
        }
      });
      selList.addEventListener("click", e=>{
        if(e.target.tagName==="LI"){
          moveItem(e.target, selList, availList);
        }
      });
      function updateHiddenOrder(){
        let items = selList.querySelectorAll("li");
        let arr=[];
        items.forEach(li=> arr.push(li.getAttribute("data-folder")));
        hiddenOrder.value = arr.join(",");
      }
      let selItems = selList.querySelectorAll("li");
      selItems.forEach(li => addDnDHandlers(li));
      sortAvailable();
    }

    function loadSpecificThumbnails(dispName){
      let container = document.getElementById(dispName + "_lazyContainer");
      let allThumbs = JSON.parse(container.getAttribute("data-files"));
      let shownCount = container.querySelectorAll("label.thumb-label").length;
      let nextLimit = shownCount + 100;
      let slice = allThumbs.slice(shownCount, nextLimit);
      slice.forEach(filePath => {
        let bn = filePath.split("/").pop();
        let lbl = document.createElement("label");
        lbl.className = "thumb-label";
        let img = document.createElement("img");
        img.src = "/images/" + filePath;
        img.className = "lazy-thumb";
        let radio = document.createElement("input");
        radio.type = "radio";
        radio.name = dispName + "_specific_image";
        radio.value = bn;
        lbl.appendChild(img);
        lbl.appendChild(document.createElement("br"));
        lbl.appendChild(radio);
        lbl.appendChild(document.createTextNode(" " + bn));
        container.insertBefore(lbl, container.lastElementChild);
      });
      if(nextLimit >= allThumbs.length){
        container.lastElementChild.style.display = "none";
      }
    }
  </script>
</head>
<body class="{{ theme }}">
<div class="container">
  <h1 style="text-align:center;">Viewer Controller</h1>
  <p style="text-align:center;">
    <strong>Hostname:</strong> {{host}} |
    <strong>IP:</strong> {{ipaddr}} |
    <strong>Model:</strong> {{model}}<br>
    <strong>CPU:</strong> <span id="stat_cpu">{{cpu}}%</span> |
    <strong>Mem:</strong> <span id="stat_mem">{{mem_mb}}MB</span> |
    <strong>Load(1m):</strong> <span id="stat_load">{{load1}}</span> |
    <strong>Temp:</strong> <span id="stat_temp">{{temp}}</span>
  </p>
  <div style="text-align:center; margin-bottom:20px;">
    <a href="{{ url_for('settings') }}">
      <button style="font-size:14px;">Settings</button>
    </a>
  </div>
  <section>
    <h2 style="text-align:center;">Display Settings</h2>
    <form method="POST">
      <input type="hidden" name="action" value="update_displays">
      <div class="display-grid">
        {% for dname, dcfg in cfg.displays.items() %}
        {% set resolution = monitors[dname].resolution if dname in monitors else "?" %}
        <div class="display-card">
          <div class="help-icon" onclick="toggleHelpBox('help_{{dname}}')">?</div>
          <div class="help-box" id="help_{{dname}}">
            <p><strong>Display Info</strong></p>
            <p>- Mode: random, specific, or mixed</p>
            <p>- Interval: how often new images load</p>
            <p>- Shuffle: randomizes order</p>
            <p>- Mixed: pick multiple folders, drag to reorder</p>
            <p>- Specific: pick exactly 1 file</p>
          </div>
          <h3>{{ dname }} ({{ resolution }})</h3>
          <div class="field-block">
            <label>Mode:</label><br>
            <select name="{{ dname }}_mode">
              <option value="random_image"   {% if dcfg.mode=="random_image" %}selected{% endif %}>Random Image/GIF</option>
              <option value="specific_image" {% if dcfg.mode=="specific_image" %}selected{% endif %}>Specific Image/GIF</option>
              <option value="mixed"          {% if dcfg.mode=="mixed" %}selected{% endif %}>Mixed (Multiple Folders)</option>
            </select>
          </div>
          <div class="field-block">
            <label>Interval (seconds):</label><br>
            <input type="number" name="{{ dname }}_image_interval" value="{{ dcfg.image_interval }}">
          </div>
          {% if dcfg.mode != "mixed" %}
            <div class="field-block">
              <label>Category (subfolder):</label><br>
              <select name="{{ dname }}_image_category">
                <option value="" {% if not dcfg.image_category %}selected{% endif %}>All</option>
                {% for sf in subfolders %}
                  {% set count = folder_counts[sf] %}
                  <option value="{{ sf }}" {% if dcfg.image_category==sf %}selected{% endif %}>{{ sf }} ({{count}} files)</option>
                {% endfor %}
              </select>
            </div>
          {% endif %}
          {% if dcfg.mode == "mixed" %}
            <div class="field-block">
              <label>Multiple Folders (drag to reorder):</label><br>
              <div style="display:flex; flex-direction:row; gap:20px; justify-content:center; margin-top:10px;">
                <div class="dnd-column">
                  <h4>Available</h4>
                  <input type="text" placeholder="Search..." id="{{ dname }}_search" class="search-box" style="width:90%;">
                  <ul class="mixed-list" id="{{ dname }}_availList" style="list-style:none; margin:0; padding:0;">
                    {% for sf in subfolders if sf not in dcfg.mixed_folders %}
                    <li draggable="true" data-folder="{{ sf }}">{{ sf }} ({{ folder_counts[sf] }} files)</li>
                    {% endfor %}
                  </ul>
                </div>
                <div class="dnd-column">
                  <h4>Selected</h4>
                  <ul class="mixed-list" id="{{ dname }}_selList" style="list-style:none; margin:0; padding:0;">
                    {% for sf in dcfg.mixed_folders %}
                    <li draggable="true" data-folder="{{ sf }}">{{ sf }} ({{ folder_counts[sf]|default(0) }} files)</li>
                    {% endfor %}
                  </ul>
                </div>
              </div>
              <input type="hidden" name="{{ dname }}_mixed_order" id="{{ dname }}_mixed_order" value="{{ ','.join(dcfg.mixed_folders) }}">
              <script>
                document.addEventListener("DOMContentLoaded", function(){
                  initMixedUI("{{ dname }}");
                });
              </script>
            </div>
          {% endif %}
          <div class="field-block">
            <label>Shuffle?</label><br>
            <input type="checkbox" name="{{ dname }}_shuffle_mode" {% if dcfg.shuffle_mode %}checked{% endif %}>
          </div>
          {% if dcfg.mode == "specific_image" %}
            <div class="field-block">
              <h4>Select Image/GIF</h4>
              {% set fileList = display_images[dname] %}
              {% if fileList and fileList|length > 100 %}
                <div id="{{ dname }}_lazyContainer" data-files='{{ fileList|tojson }}' class="lazy-thumbs-container">
                  <button type="button" onclick="loadSpecificThumbnails('{{ dname }}')" style="margin:10px;">
                    Show Thumbnails
                  </button>
                </div>
              {% else %}
                <div class="image-gallery">
                  {% for imgpath in fileList %}
                    {% set bn = imgpath.split('/')[-1] %}
                    <label class="thumb-label">
                      <img src="/images/{{ imgpath }}" class="thumb-img"><br>
                      <input type="radio" name="{{ dname }}_specific_image" value="{{ bn }}"
                        {% if bn == dcfg.specific_image %}checked{% endif %}>
                      {{ bn }}
                    </label>
                  {% else %}
                    <p>No images found or category is empty.</p>
                  {% endfor %}
                </div>
              {% endif %}
            </div>
          {% endif %}
        </div>
        {% endfor %}
      </div>
      <div class="centered-btn">
        <button type="submit">Save Display Settings</button>
      </div>
    </form>
  </section>
  <section style="text-align:center;">
    <h2>Media Management</h2>
    <p>Upload multiple GIFs/images to subfolders, or create new subfolders. (GIF/JPG/PNG)</p>
    <a href="{{ url_for('upload_media') }}">
      <button>Go to Upload Page</button>
    </a>
  </section>
</div>
</body>
</html>
"""
    return render_template_string(
        HTML,
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
        monitors=monitors
    )

if __name__ == "__main__":
    init_config()
    app.run(host="0.0.0.0", port=8080, debug=False)
