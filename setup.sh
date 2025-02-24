#!/usr/bin/env bash
#
# setup.sh - Simple "It Just Works" viewer + controller installation
#   1) Installs LightDM (with Xorg), mpv, python3, etc.
#   2) Installs pip dependencies (with --break-system-packages)
#   3) Disables screen blanking (via raspi-config)
#   4) Prompts for user + paths
#   5) Creates .env in VIEWER_HOME
#   6) (Optional) mounts a CIFS network share
#   7) Creates systemd services:
#        - viewer.service (runs viewer.py slideshow on X:0)
#        - controller.service (Flask web interface)
#   8) Reboots
#
# After reboot, LightDM will auto-login to an X session on :0.
# viewer.service will run viewer.py (which now dynamically assigns mpv
# instances to monitors based on xrandr output).
# controller.service will run app.py on port 8080:  http://<Pi-IP>:8080
#
# Example usage:  sudo ./setup.sh

# -------------------------------------------------------
# Must be run as root (sudo):
# -------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Please run this script as root (e.g. sudo ./setup.sh)."
  exit 1
fi

echo "================================================================="
echo "  Simple 'It Just Works' Setup with LightDM + viewer + controller"
echo "================================================================="
echo
echo "This script will:"
echo " 1) Install lightdm (Xorg), mpv, python3, etc."
echo " 2) pip-install your Python dependencies (with --break-system-packages)"
echo " 3) Disable screen blanking"
echo " 4) Prompt for user & paths"
echo " 5) Create .env in VIEWER_HOME"
echo " 6) (Optional) mount a network share"
echo " 7) Create systemd services for viewer.py and app.py"
echo " 8) Reboot"
echo
read -p "Press [Enter] to continue or Ctrl+C to abort..."

# -------------------------------------------------------
# 1) Install apt packages
# -------------------------------------------------------
echo
echo "== Step 1: Installing packages (lightdm, Xorg, mpv, python3, etc.) =="
apt-get update
apt-get install -y lightdm xorg x11-xserver-utils mpv python3 python3-pip cifs-utils ffmpeg raspi-config

if [ $? -ne 0 ]; then
  echo "Error installing packages via apt. Exiting."
  exit 1
fi

# Let raspi-config handle auto-login in desktop:
raspi-config nonint do_boot_behaviour B4
# B4 => Auto login to Desktop (on RPi OS).

# -------------------------------------------------------
# 2) pip install from dependencies.txt
# -------------------------------------------------------
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "$SCRIPT_DIR/dependencies.txt" ]; then
  echo
  echo "== Step 2: Installing Python dependencies (with --break-system-packages) =="
  pip3 install --break-system-packages -r "$SCRIPT_DIR/dependencies.txt"
  if [ $? -ne 0 ]; then
    echo "Error installing pip packages. Exiting."
    exit 1
  fi
else
  echo "== Step 2: No dependencies.txt found, skipping pip install."
fi

# -------------------------------------------------------
# 3) Disable screen blanking and mouse cursor
# -------------------------------------------------------
echo
echo "== Step 3: Disabling screen blanking via raspi-config =="
raspi-config nonint do_blanking 1
if [ $? -eq 0 ]; then
  echo "Screen blanking disabled."
else
  echo "Warning: raspi-config do_blanking failed. You may need to disable blanking manually."
fi
sudo sed -i -- "s/#xserver-command=X/xserver-command=X -nocursor/" /etc/lightdm/lightdm.conf


# -------------------------------------------------------
# 4) Prompt user for config
# -------------------------------------------------------
echo
echo "== Step 4: Configuration =="
read -p "Enter the Linux username to run the viewer & controller (default: pi): " VIEWER_USER
VIEWER_USER=${VIEWER_USER:-pi}

USER_ID="$(id -u "$VIEWER_USER" 2>/dev/null)"
if [ -z "$USER_ID" ]; then
  echo "User '$VIEWER_USER' not found. Create user? (y/n)"
  read create_user
  if [[ "$create_user" =~ ^[Yy]$ ]]; then
    adduser --gecos "" --disabled-password "$VIEWER_USER"
    USER_ID="$(id -u "$VIEWER_USER")"
    echo "User '$VIEWER_USER' created with no password (you can set one later if desired)."
  else
    echo "Cannot proceed without a valid user. Exiting."
    exit 1
  fi
fi

read -p "Enter the path for VIEWER_HOME (default: /home/$VIEWER_USER/PiViewer): " input_home
if [ -z "$input_home" ]; then
  VIEWER_HOME="/home/$VIEWER_USER/PiViewer"
else
  VIEWER_HOME="$input_home"
fi

read -p "Enter the path for IMAGE_DIR (default: /mnt/PiViewers): " input_dir
IMAGE_DIR=${input_dir:-/mnt/PiViewers}

echo
echo "Creating $VIEWER_HOME if it doesn't exist..."
mkdir -p "$VIEWER_HOME"
chown "$VIEWER_USER":"$VIEWER_USER" "$VIEWER_HOME"

# -------------------------------------------------------
# 5) Create .env
# -------------------------------------------------------
ENV_FILE="$VIEWER_HOME/.env"
echo "Creating $ENV_FILE with VIEWER_HOME + IMAGE_DIR..."
cat <<EOF > "$ENV_FILE"
VIEWER_HOME=$VIEWER_HOME
IMAGE_DIR=$IMAGE_DIR
EOF
chown "$VIEWER_USER":"$VIEWER_USER" "$ENV_FILE"

echo
echo "Contents of $ENV_FILE:"
cat "$ENV_FILE"
echo

# -------------------------------------------------------
# 6) (Optional) Configure CIFS/SMB share
# -------------------------------------------------------
echo
echo "== Step 6: (Optional) Network Share at $IMAGE_DIR =="
read -p "Mount a network share at $IMAGE_DIR via CIFS? (y/n): " mount_answer
if [[ "$mount_answer" =~ ^[Yy]$ ]]; then
  read -p "Enter server share path (e.g. //192.168.1.100/MyShare): " SERVER_SHARE
  if [ -z "$SERVER_SHARE" ]; then
    echo "No share path entered. Skipping."
  else
    read -p "Mount options (e.g. guest,uid=$USER_ID,gid=$USER_ID,vers=3.0) [ENTER for default]: " MOUNT_OPTS
    if [ -z "$MOUNT_OPTS" ]; then
      MOUNT_OPTS="guest,uid=$USER_ID,gid=$USER_ID,vers=3.0"
    fi

    echo "Creating mount dir: $IMAGE_DIR"
    mkdir -p "$IMAGE_DIR"

    FSTAB_LINE="$SERVER_SHARE  $IMAGE_DIR  cifs  $MOUNT_OPTS  0  0"
    if grep -qs "$SERVER_SHARE" /etc/fstab; then
      echo "Share already in /etc/fstab; skipping append."
    else
      echo "Appending to /etc/fstab: $FSTAB_LINE"
      echo "$FSTAB_LINE" >> /etc/fstab
    fi

    echo "Mounting all..."
    mount -a
    if [ $? -ne 0 ]; then
      echo "WARNING: mount -a failed. Check credentials/options."
    else
      echo "Share mounted at $IMAGE_DIR."
    fi
  fi
fi

# -------------------------------------------------------
# 7) Create systemd services
# -------------------------------------------------------
echo
echo "== Step 7: Creating systemd service files =="

# (A) viewer.service
VIEWER_SERVICE="/etc/systemd/system/viewer.service"
echo "Creating $VIEWER_SERVICE ..."
cat <<EOF > "$VIEWER_SERVICE"
[Unit]
Description=Slideshow Viewer (mpv on X:0)
After=lightdm.service
Wants=lightdm.service

[Service]
User=$VIEWER_USER
Group=$VIEWER_USER
ExecStartPre=/bin/sleep 10
WorkingDirectory=$VIEWER_HOME
EnvironmentFile=$ENV_FILE

# Provide environment for X
Environment="DISPLAY=:0"
Environment="XDG_RUNTIME_DIR=/run/user/$USER_ID"
Environment="XAUTHORITY=/home/$VIEWER_USER/.Xauthority"

ExecStartPre=/bin/bash -c 'if [ ! -d /run/user/$USER_ID ]; then mkdir -p /run/user/$USER_ID && chown $VIEWER_USER:$VIEWER_USER /run/user/$USER_ID; fi'

ExecStart=/usr/bin/python3 viewer.py

Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=graphical.target
EOF

# (B) controller.service
CONTROLLER_SERVICE="/etc/systemd/system/controller.service"
echo "Creating $CONTROLLER_SERVICE ..."
cat <<EOF > "$CONTROLLER_SERVICE"
[Unit]
Description=Viewer Controller (Flask web interface)
After=network-online.target
Wants=network-online.target

[Service]
User=$VIEWER_USER
Group=$VIEWER_USER
WorkingDirectory=$VIEWER_HOME
EnvironmentFile=$ENV_FILE

# Provide environment for X (if needed)
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/$VIEWER_USER/.Xauthority"

ExecStart=/usr/bin/python3 app.py
Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd..."
systemctl daemon-reload

echo "Enabling viewer.service & controller.service..."
systemctl enable viewer.service
systemctl enable controller.service

echo "Starting them now..."
systemctl start viewer.service
systemctl start controller.service

# -------------------------------------------------------
# 8) Reboot
# -------------------------------------------------------
echo
echo "========================================================"
echo "Setup is complete. The Pi will now reboot."
echo "Upon reboot:"
echo " - LightDM auto-logs into X (DISPLAY=:0)."
echo " - viewer.service starts viewer.py, which now supports multiple monitors."
echo " - controller.service starts app.py on port 8080."
echo
echo "You can configure slides at http://<Pi-IP>:8080"
echo "Rebooting in 5 seconds..."
sleep 5
reboot
