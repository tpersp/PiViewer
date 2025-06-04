# PiViewer

PiViewer is a modern, easy-to-configure slideshow + overlay viewer written in **Python/PySide6** along with a companion **Flask**-based web interface. It seamlessly supports multiple monitors on a Raspberry Pi and can optionally display a live overlay (e.g. clock, weather) on top of your images or GIFs.

## Key Features

- **Multiple Monitors**: Launches a PySide6 window per detected monitor, each with its own display mode (Random, Mixed, Spotify, etc.).
- **Web Controller**: A Flask web interface (on port **8080**) lets you manage sub-devices, change the slideshow folder, set intervals, shuffle, or pick a single image.
- **Systemd Integration**: The `setup.sh` script creates two systemd services:
  - `piviewer.service` - runs the PySide6 slideshow windows
  - `controller.service` - runs the Flask app
- **Overlay**: Optionally display time, weather, or custom text overlay in a semi-transparent box.
- **Remote Device Management**: If you set the Pi’s role to **main**, it can push or pull display configs from sub-devices.
- **Spotify Integration**: Show currently playing track’s album art on a display.

## Installation

These instructions assume you have a clean Raspberry Pi OS image (Lite or Desktop) with **X11**.

1. **Clone the Repository**:

```bash
sudo apt update
sudo apt install -y git
cd ~
git clone https://github.com/tpersp/PiViewer.git
cd PiViewer
```

2. **Run Setup**:

```bash
chmod +x setup.sh
sudo ./setup.sh
```

During the setup:

- **Apt packages** are installed (LightDM, Xorg, Python3, etc.)
- **pip packages** from `dependencies.txt` are installed
- **Screen blanking** is disabled
- You’ll be prompted for the user that will auto-login into X, the path for `VIEWER_HOME` and `IMAGE_DIR`.
- **Optionally** mount a CIFS share at `IMAGE_DIR`, or skip to use a local uploads folder.
- Systemd services are created and enabled.
- The system is **rebooted** (unless you run `--auto-update`).

3. **Post-Reboot**:
   - LightDM auto-logs into the specified user’s X session.
   - `piviewer.service` runs, launching a PySide6 slideshow window on each detected screen.
   - `controller.service` hosts the web UI on **port 8080**.

## Usage

Once the Pi is up and running:

### Web Interface

Browse to `http://<PI-IP>:8080` to access the interface. You’ll see:

- **Main Screen** (`index.html`)
  - Displays system stats (CPU, memory, temp)
  - Lets you configure each local display’s mode (Random, Specific, Mixed, or Spotify)
  - For Specific mode, choose exactly one image. For Mixed, drag-drop multiple folders.
  - **Manage** how often images rotate, shuffle, etc.

- **Settings** Page
  - Choose your Pi’s role (main or sub)
  - If sub, specify the IP of your main Pi
  - Set the web theme (Dark, Light, or Custom) and optionally upload a background image
  - Configure weather (API key, location) used by the overlay

- **Device Manager** (only if role=main)
  - Add sub-devices by name + IP
  - Push or pull display configs from each sub device
  - Remotely configure them

- **Overlay Settings**
  - Enable or disable the overlay box
  - Position, size, and color of the overlay
  - Font sizes, weather toggles, clock toggles, etc.

### Spotify Integration

In `Configure Spotify`, provide your **Client ID**, **Client Secret**, and **Redirect URI** from the Spotify Developer Dashboard. Then click **Authorize Spotify** to store the OAuth token. You can set one or more displays to `spotify` mode.

### Media Upload

Use the **Upload Media** page to add images/GIFs. You can place them in existing subfolders or create a new one. If you have a CIFS share, it will appear under your `IMAGE_DIR`.

## Multi-Device Setup

- **Main Device**: in the Settings page, set Role to `main`. It can manage sub-devices.
- **Sub Device**: set role to `sub` and specify the main Pi’s IP address.

Then, from the main Pi’s **Device Manager**, add the sub’s IP and name. You can push/pull config or go into remote configure for that sub.

## Directory Structure

Below is a simplified layout:

```
PiViewer/
├── app.py               # Flask entry point
├── config.py            # Paths, version info
├── piviewer.py          # PySide6 main script creating slideshow windows
├── routes.py            # All Flask routes
├── utils.py             # Shared functions (config I/O, logging, etc.)
├── setup.sh             # Automated setup script
├── dependencies.txt     # Required pip packages
├── static/
│   ├── style.css
│   ├── favicon.png
│   └── icon.png
├── templates/
│   ├── index.html
│   ├── settings.html
│   ├── overlay.html
│   ├── device_manager.html
│   ├── remote_configure.html
│   ├── configure_spotify.html
│   ├── upload_media.html
│   ...
└── README.md            # This README
```

## Systemd Services

Two services are created:

- **piviewer.service**
  - Runs `piviewer.py` at boot, so the slideshows start automatically on every connected screen.
- **controller.service**
  - Runs `app.py`, the Flask server on port 8080.

You can check their status or logs:

```bash
sudo systemctl status piviewer.service
sudo systemctl status controller.service

sudo journalctl -u piviewer.service
sudo journalctl -u controller.service
```

## Troubleshooting

- **No images?** Ensure images exist in the `IMAGE_DIR` (or subfolders). By default, check `/mnt/PiViewers` or wherever you mounted.
- **Wrong screen**? Confirm you have multiple monitors recognized by X. PiViewer uses PySide6’s screen geometry, so make sure your environment is not on Wayland.
- **Spotify issues**? Check `.spotify_cache` for the saved token. Re-authorize if needed.
- **Overlay not transparent?** You need a compositor (like **picom**) running for real transparency.
- **Check logs**: Look at `piviewer.log` (in your `VIEWER_HOME`) or `journalctl -u piviewer.service`.
## Running Tests

Run `pytest` from the project root to run the unit tests.

## Contributing

Feel free to open pull requests or issues. Any improvements to multi-monitor detection, new overlay features, or theming are welcome.

**Enjoy PiViewer!**

