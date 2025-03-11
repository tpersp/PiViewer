#!/usr/bin/env bash
#
# setup.sh - "It Just Works" for the new PySide6 + Flask PiViewer
#

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo)."
  exit 1
fi

echo "=== Installing apt packages ==="
apt-get update
apt-get install -y python3 python3-pip python3-requests python3-tk git \
                   openbox picom conky lightdm xorg x11-xserver-utils cifs-utils \
                   ffmpeg raspi-config

echo "== Setting LightDM to auto-login =="
raspi-config nonint do_boot_behaviour B4

echo "== Disabling screen blanking =="
raspi-config nonint do_blanking 1

# Hide mouse cursor from X sessions
sed -i -- "s/#xserver-command=X/xserver-command=X -nocursor/" /etc/lightdm/lightdm.conf

echo "== Installing Python deps =="
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "$SCRIPT_DIR/dependencies.txt" ]; then
  pip3 install --break-system-packages -r "$SCRIPT_DIR/dependencies.txt"
else
  pip3 install --break-system-packages flask psutil requests spotipy PySide6
fi

VIEWER_USER="pi"
read -p "Which user should run PiViewer? [default: pi]: " user_inp
if [ ! -z "$user_inp" ]; then
  VIEWER_USER="$user_inp"
fi

if ! id "$VIEWER_USER" &>/dev/null; then
  echo "User $VIEWER_USER not found. Creating..."
  adduser --gecos "" --disabled-password "$VIEWER_USER"
fi

VIEWER_HOME="/home/$VIEWER_USER/PiViewer"
IMAGE_DIR="/mnt/PiViewers"

echo "VIEWER_HOME=$VIEWER_HOME"
echo "IMAGE_DIR=$IMAGE_DIR"

mkdir -p "$VIEWER_HOME"
chown "$VIEWER_USER:$VIEWER_USER" "$VIEWER_HOME"

ENV_FILE="$VIEWER_HOME/.env"
cat <<EOF > "$ENV_FILE"
VIEWER_HOME=$VIEWER_HOME
IMAGE_DIR=$IMAGE_DIR
EOF
chown "$VIEWER_USER:$VIEWER_USER" "$ENV_FILE"

# (Optional) mount network share at $IMAGE_DIR or so, if you want.

echo "=== Creating systemd service for PySide6 GUI (piviewer.service) ==="
PIVIEWER_SERVICE="/etc/systemd/system/piviewer.service"
cat <<EOF > "$PIVIEWER_SERVICE"
[Unit]
Description=PiViewer PySide6 Slideshow + Overlay
After=lightdm.service
Wants=lightdm.service

[Service]
User=$VIEWER_USER
Group=$VIEWER_USER
WorkingDirectory=$VIEWER_HOME
EnvironmentFile=$ENV_FILE
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/$VIEWER_USER/.Xauthority"
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 $VIEWER_HOME/piviewer.py

Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=graphical.target
EOF

echo "=== Creating systemd service for Flask web controller (controller.service) ==="
CONTROLLER_SERVICE="/etc/systemd/system/controller.service"
cat <<EOF > "$CONTROLLER_SERVICE"
[Unit]
Description=PiViewer Flask Web Controller
After=network-online.target
Wants=network-online.target

[Service]
User=$VIEWER_USER
Group=$VIEWER_USER
WorkingDirectory=$VIEWER_HOME
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $VIEWER_HOME/app.py
Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd..."
systemctl daemon-reload
systemctl enable piviewer.service
systemctl enable controller.service
systemctl start piviewer.service
systemctl start controller.service

echo "Setup complete. You may want to reboot now."
