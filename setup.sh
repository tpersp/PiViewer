#!/usr/bin/env bash
#
# setup.sh - Simple "It Just Works" viewer + controller installation
#
# NOTE: This setup.sh is designed for PiViewer version 1.1.1
#
#   1) Installs LightDM (with Xorg), mpv, python3, etc.
#   2) Installs pip dependencies (with --break-system-packages)
#   3) Disables screen blanking (via raspi-config)
#   4) Prompts for user + paths (unless in --auto-update mode)
#   5) Creates .env in VIEWER_HOME
#   6) (Optional) mounts a CIFS network share
#   7) Creates systemd services:
#        - viewer.service (runs viewer.py slideshow on X:0)
#        - controller.service (Flask web interface)
#        - overlay.service (optional clock+weather overlay)
#   8) Reboots (unless in --auto-update mode)
#
# After reboot, LightDM will auto-login to an X session on :0.
# viewer.service will run viewer.py (which dynamically assigns mpv
# to monitors based on xrandr).
# controller.service will run app.py on port 8080:  http://<Pi-IP>:8080
# overlay.service (if installed) will draw a transparent overlay window.
#
# Usage:  sudo ./setup.sh  [--auto-update]
#   If you run with --auto-update, we skip interactive prompts and the final reboot.

# -------------------------------------------------------
# Must be run as root (sudo):
# -------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  echo "Please run this script as root (e.g. sudo ./setup.sh)."
  exit 1
fi

# -------------------------------------------------------
# Check if we're in "auto-update" mode
# -------------------------------------------------------
AUTO_UPDATE="false"
if [[ "$1" == "--auto-update" ]]; then
  AUTO_UPDATE="true"
fi

if [[ "$AUTO_UPDATE" == "false" ]]; then
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
  echo " 7) Create systemd services for viewer.py, app.py, and overlay"
  echo " 8) Reboot"
  echo
  read -p "Press [Enter] to continue or Ctrl+C to abort..."
fi

# -------------------------------------------------------
# 1) Install apt packages
# -------------------------------------------------------
echo
echo "== Step 1: Installing packages (lightdm, Xorg, mpv, python3, etc.) =="
apt-get update
apt-get install -y lightdm xorg x11-xserver-utils mpv python3 python3-pip cifs-utils ffmpeg raspi-config \
                   openbox picom conky python3-tk

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
# 3) Disable screen blanking and hide mouse cursor
# -------------------------------------------------------
echo
echo "== Step 3: Disabling screen blanking via raspi-config =="
raspi-config nonint do_blanking 1
if [ $? -eq 0 ]; then
  echo "Screen blanking disabled."
else
  echo "Warning: raspi-config do_blanking failed. You may need to disable blanking manually."
fi

# Remove mouse cursor from X sessions:
sed -i -- "s/#xserver-command=X/xserver-command=X -nocursor/" /etc/lightdm/lightdm.conf

# -------------------------------------------------------
# 3a) Update boot firmware configuration
# -------------------------------------------------------
echo
echo "== Step 3a: Updating boot firmware configuration in /boot/firmware/config.txt =="
cp /boot/firmware/config.txt /boot/firmware/config.txt.backup

# Insert dtoverlay if missing, right after the comment line.
grep -q '^dtoverlay=vc4-kms-v3d' "/boot/firmware/config.txt" || \
  sed -i '/^# Enable DRM VC4 V3D driver/ a dtoverlay=vc4-kms-v3d' "/boot/firmware/config.txt"

# Insert max_framebuffers if missing
grep -q '^max_framebuffers=2' "/boot/firmware/config.txt" || \
  sed -i '/^dtoverlay=vc4-kms-v3d/ a max_framebuffers=2' "/boot/firmware/config.txt"

# Insert hdmi_force_hotplug if missing
grep -q '^hdmi_force_hotplug=1' "/boot/firmware/config.txt" || \
  sed -i '/^max_framebuffers=2/ a hdmi_force_hotplug=1' "/boot/firmware/config.txt"

# -------------------------------------------------------
# 4) Prompt user for config (skip if AUTO_UPDATE)
# -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "false" ]]; then
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

else
  # In --auto-update mode, set some defaults without prompting
  echo
  echo "== Auto-Update Mode: skipping interactive prompts. Using defaults. =="
  VIEWER_USER="pi"
  USER_ID="$(id -u "$VIEWER_USER")"
  if [ -z "$USER_ID" ]; then
    echo "User 'pi' not found. Exiting auto-update."
    exit 1
  fi

  VIEWER_HOME="/home/$VIEWER_USER/PiViewer"
  IMAGE_DIR="/mnt/PiViewers"
fi

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
# 6) (Optional) Configure CIFS/SMB share (skip if AUTO_UPDATE)
# -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "false" ]]; then
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
else
  echo
  echo "== Auto-Update Mode: skipping CIFS prompt. =="
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

# (C) overlay.service (optional transparent clock+weather overlay)
OVERLAY_SERVICE="/etc/systemd/system/overlay.service"
echo "Creating $OVERLAY_SERVICE ..."
cat <<EOF > "$OVERLAY_SERVICE"
[Unit]
Description=Clock & Weather Overlay
After=viewer.service
Wants=viewer.service

[Service]
User=$VIEWER_USER
Group=$VIEWER_USER
WorkingDirectory=$VIEWER_HOME
EnvironmentFile=$ENV_FILE

Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/$VIEWER_USER/.Xauthority"

ExecStart=/usr/bin/python3 overlay.py
Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=graphical.target
EOF

# (D) picom.service
PICOM_SERVICE="/etc/systemd/system/picom.service"
echo "Creating $PICOM_SERVICE ..."
cat <<EOF > "$PICOM_SERVICE"
[Unit]
Description=Picom Compositor

[Service]
Environment="DISPLAY=:0"
ExecStart=/usr/bin/picom -b
Restart=always

[Install]
WantedBy=default.target
EOF

echo "Reloading systemd..."
systemctl daemon-reload

echo "Enabling viewer.service & controller.service & overlay.service & picom.service..."
systemctl enable viewer.service
systemctl enable controller.service
systemctl enable overlay.service
systemctl enable picom.service

echo "Starting them now..."
systemctl start viewer.service
systemctl start controller.service
systemctl start overlay.service
systemctl start picom.service

# -------------------------------------------------------
# 8) Reboot (skip if AUTO_UPDATE)
# -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "false" ]]; then
  echo
  echo "========================================================"
  echo "Setup is complete. The Pi will now reboot."
  echo "Upon reboot:"
  echo " - LightDM auto-logs into X (DISPLAY=:0)."
  echo " - viewer.service starts viewer.py"
  echo " - controller.service starts app.py on port 8080"
  echo " - overlay.service starts the transparent overlay window"
  echo
  echo "You can configure slides at http://<Pi-IP>:8080"
  echo "Rebooting in 5 seconds..."
  sleep 5
  reboot
else
  echo
  echo "== Auto-Update Mode: skipping final reboot. =="
fi
