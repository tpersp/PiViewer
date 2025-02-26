# PiViewer

PiViewer is a lightweight, easy-to-configure slideshow viewer and controller designed for Raspberry Pi OS. It leverages [mpv](https://mpv.io/) for fullscreen image display and uses a Flask web interface to manage settings and media—now with automatic multi-monitor support.

> **Note:** This project was developed and tested on Pi 4 and Pi Zero2W running Raspberry Pi OS Lite (both 32-bit and 64-bit).

---

## Features

- **Multi-Monitor Support**  
  Automatically detects connected monitors using `xrandr` and launches an mpv instance per screen. The viewer dynamically assigns mpv’s `--screen` parameter based on the order reported by xrandr. Whether you have two, three, or more monitors, each will be handled automatically.

- **Web Interface**  
  Manage your slideshow settings (display mode, image intervals, folder selection, theming) via a simple web interface on **port 8080**.

- **Systemd Integration**  
  The included setup script creates systemd services for both the viewer (slideshow) and the controller (web interface), so PiViewer starts automatically at boot.

- **Custom Themes**  
  Choose between dark, light, or custom themes via the settings page. For a custom theme, you can upload your own background image.

- **Easy Installation**  
  A single `setup.sh` script installs all necessary packages and Python dependencies, performs basic system configuration (e.g., disabling screen blanking), and sets up systemd services.

---

## Installation

### Prerequisites

- **Raspberry Pi OS**  
  Ensure you have Raspberry Pi OS (Lite or Desktop) installed.
- **X11 instead of Wayland**  
  PiViewer currently uses `xrandr` and X11; it does *not* support Wayland.  
  (You may need to disable Wayland manually in your Pi’s configuration.)

### Manual X11 Configuration (If Needed)

1. **`/boot/config.txt`** or `/boot/firmware/config.txt` (depending on your Pi OS version):  
   - You *should not* have `dtoverlay=vc4-fkms-v3d`.  
   - Instead, ensure something like:
     ```ini
     dtoverlay=vc4-kms-v3d
     max_framebuffers=2
     hdmi_force_hotplug=1
     ```
2. **Disable Wayland**  
   In Raspberry Pi OS, run:
sudo raspi-config

Then navigate to advanced/X11 options, and ensure Wayland is disabled.

3. **Reboot** to apply changes.

---

## Automated Setup

### Clone the Repository

```bash
sudo apt update
sudo apt install git -y
git clone https://github.com/tpersp/PiViewer.git
```
Navigate to the project folder:
```
cd PiViewer
```

Run the Setup Script
```bash
chmod +x setup.sh
sudo ./setup.sh
```
The script will:

- **Update apt** and install necessary packages (LightDM, Xorg, mpv, Python3, etc.)
- **Install Python dependencies** from `dependencies.txt`
- **Disable screen blanking** via `raspi-config`
- **Prompt you for the Linux username** to use and the paths for `VIEWER_HOME` and `IMAGE_DIR`
- **Create a `.env` file** containing those paths
- **Optionally configure a CIFS network share**
- **Create systemd services** for `viewer.py` and `app.py` (the slideshow and the web controller)
- **Reboot the system** when finished

## After Reboot

Once the Pi reboots:
- **LightDM** will auto-login into an X session on `:0`.
- **viewer.service** will launch `viewer.py`, which automatically detects all connected monitors and assigns an mpv instance to each.
- **controller.service** will launch `app.py`, which runs the Flask web interface on port `8080`.

### Access the Web Interface

Open a browser and navigate to:

```lua
http://<Your-Raspberry-Pi-IP>:8080
```
Use this interface to configure display settings, choose themes, and manage media.

### Project Structure
A simplified layout:

```php
PiViewer/
├── app.py                # Main Flask entry point
├── config.py             # Holds app version & path constants
├── utils.py              # Shared utility functions (config load/save, logging, etc.)
├── routes.py             # Flask routes (blueprint)
├── viewer.py             # Slideshow: spawns mpv per detected monitor
├── static/
│    └── style.css        # Consolidated CSS
├── templates/
│    ├── index.html
│    ├── settings.html
│    ├── device_manager.html
│    ├── remote_configure.html
│    └── upload_media.html
├── setup.sh              # Installation script
├── dependencies.txt      # Python dependencies
└── README.md             # This file
```

## Usage & Customization

### Local Displays
Using the web interface (on port 8080), you can select:

- Mode: Random, Specific, or Mixed
- Interval: How many seconds between image changes
- Rotate: How many degrees to rotate each image
- Shuffle: Whether images should display in random order
- For specific_image mode, you can pick the exact file.
- For mixed mode, you can select multiple folders, drag to reorder them, etc.

### Remote (Sub) Devices
If you configure one Pi as the main, you can add sub devices in the Device Manager page. The main device can push/pull configurations to/from each sub device and even remotely configure their displays.

### Themes
In Settings, choose between dark, light, or custom.
For a custom theme, upload a background image; the rest of the UI overlays on top.

## Troubleshooting
#### No Image or Wrong Monitor
Check that the environment variables (DISPLAY, XAUTHORITY, XDG_RUNTIME_DIR) are being set correctly in viewer.service.
Confirm with 
```bash
xrandr --listmonitors that X11 sees all monitors.
```
#### Logs

View systemd logs:
```bash
sudo systemctl status viewer.service
sudo journalctl -u viewer.service
```
Check the PiViewer log (written to viewer.log by default).
#### Wayland Conflicts

If you see warnings about xrandr or DISPLAY, ensure you’re not on Wayland.