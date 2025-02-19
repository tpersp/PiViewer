# RasPi Viewer LightOS

RasPi Viewer LightOS is a lightweight, easy-to-configure slideshow viewer and controller designed for Raspberry Pi OS. It leverages [mpv](https://mpv.io/) for fullscreen image display and uses a Flask web interface to manage settings and media—now with automatic multi-monitor support.

> **Note:** This project was developed and tested on a Raspberry Pi 4 running Raspberry Pi OS with a desktop environment.

## Features

- **Multi-Monitor Support:**  
  Automatically detects connected monitors using `xrandr` and launches an mpv instance for each screen. The viewer dynamically assigns mpv’s `--screen` parameter based on the order reported by xrandr. This means that if you have three, four, five, or more monitors, each one will be handled automatically.

- **Web Interface:**  
  Manage your slideshow settings (display mode, image intervals, folder selection, and theme) via a simple web interface running on port 8080.

- **Systemd Integration:**  
  The installation script creates systemd services for both the viewer (slideshow) and the controller (web interface), so your application starts automatically at boot.

- **Custom Themes:**  
  Choose between dark, light, or custom themes via the settings page. For custom themes, you can upload your own background image.

- **Easy Installation:**  
  A single `setup.sh` script installs all necessary packages, Python dependencies, performs basic system configuration (such as disabling screen blanking), and sets up systemd services.

## Installation

### Prerequisites

- **Raspberry Pi OS with Desktop:**  
  Ensure that you have Raspberry Pi OS (Desktop version) installed.
- **Network (Optional):**  
  If you want to mount a network share for images, make sure CIFS is supported.

### Manual Configuration: Disable Wayland / Use X11

If the automated setup does not fully disable Wayland, or you prefer to do it manually, follow these steps:

1. **Edit `/boot/firmware/config.txt`:**  
   - **Do not include:**
     ```
     dtoverlay=vc4-fkms-v3d
     ```
   - **Ensure you have the following:**
     ```
     dtoverlay=vc4-kms-v3d
     max_framebuffers=2
     hdmi_force_hotplug=1
     ```

2. **Disable Wayland in your display manager:**  
   For Raspberry Pi OS, run:
   ```
   sudo raspi-config
   ```
Navigate to Advanced → X11 and follow the prompts to disable Wayland.

## Reboot

Reboot the system to ensure that X11 starts properly.

## Automated Setup

### Clone the Repository

```
git clone https://github.com/yourusername/your-repo-name.git
```
cd your-repo-name

## Run the Setup Script:

Make the setup script executable and run it:

```
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

```
http://<Your-Raspberry-Pi-IP>:8080
```
Use this interface to configure display settings, choose themes, and manage media.


## Project Structure

```plaintext
├── app.py              # Flask web interface for managing slides and settings
├── viewer.py           # Main slideshow viewer using mpv, with multi-monitor support
├── static/             # Static assets (CSS, images, etc.)
├── setup.sh            # Installation script for system configuration and service setup
├── dependencies.txt    # Python dependencies (if applicable)
├── README.md           # This file
└── (other project files...)
```


## Customization

### Display Settings
Through the web interface you can choose:
- The mode for each display (random, specific, or mixed)
- The time interval between image changes
- The image category (subfolder) or specific image selection
- Whether to shuffle images

### Theme Settings
In the Settings page, you can select a theme (dark, light, or custom). If you choose custom, you can upload a background image.

### Multi-Monitor Mapping
The viewer automatically detects monitors using `xrandr` and maps each mpv instance to a screen index based on its order. This automatic mapping supports systems with more than two monitors.


### Troubleshooting
## Display Issues:
If an mpv instance isn’t appearing on the correct monitor, verify that the systemd service’s environment variables (DISPLAY, XAUTHORITY, and XDG_RUNTIME_DIR) are set correctly. 
You can test this by running:
```
sudo -u <username> DISPLAY=:0 XAUTHORITY=/home/<username>/.Xauthority xrandr --listmonitors
```

## Monitor Detection

If the web interface does not list all connected monitors, ensure that your X session is correctly configured and that the manual steps for disabling Wayland have been followed.

### Service Logs

To inspect logs and troubleshoot service issues, run:

```
sudo systemctl status viewer.service
sudo journalctl -u viewer.service
```
