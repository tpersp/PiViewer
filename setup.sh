#!/usr/bin/env bash
#
# setup.sh - "It Just Works" for the new PySide6 + Flask PiViewer
#
#   1) Installs LightDM (with Xorg), python3, PySide6, etc. (but uses distro's PySide6, not pip)
#   2) Installs pip dependencies (with --break-system-packages) except PySide6
#   3) Disables screen blanking (via raspi-config)
#   4) Prompts for user & paths (unless in --auto-update mode)
#   5) Creates .env in VIEWER_HOME
#   6) (Optional) mounts a CIFS network share or fallback to local "Uploads"
#   7) Creates systemd services for piviewer.py + controller
#   8) Configure Openbox autologin & picom in openbox autostart
#   9) Reboots (unless in --auto-update mode)
#
# Usage:  sudo ./setup.sh  [--auto-update]
#   If you run with --auto-update, it will skip user prompts and final reboot.

# -------------------------------------------------------
# Must be run as root
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
  echo "  Simple 'It Just Works' Setup with LightDM + PiViewer (PySide6) "
  echo "================================================================="
  echo
  echo "This script will:"
  echo " 1) Install lightdm (Xorg), python3, etc. plus system python3-pyside6"
  echo " 2) pip-install your other dependencies (with --break-system-packages)"
  echo " 3) Disable screen blanking"
  echo " 4) Prompt for user & paths"
  echo " 5) Create .env in VIEWER_HOME"
  echo " 6) (Optional) mount a network share or fallback to local uploads folder"
  echo " 7) Create systemd services for piviewer.py + controller"
  echo " 8) Configure Openbox autologin & picom in openbox autostart"
  echo " 9) Reboot"
  echo
  read -p "Press [Enter] to continue or Ctrl+C to abort..."
fi

# -------------------------------------------------------
# 1) Install apt packages (including extras for LightDM)
# -------------------------------------------------------
echo
echo "== Step 1: Installing packages (lightdm, Xorg, python3, etc.) =="
apt-get update

apt-get install -y \
  lightdm \
  lightdm-gtk-greeter \
  accountsservice \
  dbus-x11 \
  policykit-1 \
  xorg \
  x11-xserver-utils \
  python3 \
  python3-pip \
  python3-pyside6 \
  cifs-utils \
  ffmpeg \
  raspi-config \
  openbox \
  picom \
  conky \
  python3-tk \
  git \
  libxcb-cursor0 \
  libxcb-randr0 \
  libxcb-shape0 \
  libxcb-xfixes0 \
  libxcb-xinerama0 \
  libxkbcommon-x11-0

if [ $? -ne 0 ]; then
  echo "Error installing packages via apt. Exiting."
  exit 1
fi

# Create /var/lib/lightdm/data so LightDM can store user-data:
mkdir -p /var/lib/lightdm/data
chown lightdm:lightdm /var/lib/lightdm/data

# Make sure accounts-daemon is enabled and running:
systemctl enable accounts-daemon
systemctl start accounts-daemon

# Let raspi-config handle auto-login in desktop:
# B4 => Auto login to Desktop
if command -v raspi-config &>/dev/null; then
  raspi-config nonint do_boot_behaviour B4
else
  echo "Warning: raspi-config not found; skipping auto-login configuration."
fi

# -------------------------------------------------------
# 2) pip install from dependencies.txt (but remove PySide6 from pip)
# -------------------------------------------------------
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
echo
echo "== Step 2: Handling Python dependencies =="
# In case user had PySide6 via pip, remove it to avoid conflict
pip3 uninstall -y PySide6

if [ -f "$SCRIPT_DIR/dependencies.txt" ]; then
  # Create a temporary filtered file that excludes "PySide6"
  grep -v -i "^PySide6" "$SCRIPT_DIR/dependencies.txt" > /tmp/deps_no_pyside6.txt
  echo "Installing from /tmp/deps_no_pyside6.txt..."
  pip3 install --break-system-packages -r /tmp/deps_no_pyside6.txt
  rm /tmp/deps_no_pyside6.txt
else
  echo "No dependencies.txt found. Installing minimal set by pip..."
  pip3 install --break-system-packages flask psutil requests spotipy
fi

# -------------------------------------------------------
# 3) Disable screen blanking and hide mouse cursor
# -------------------------------------------------------
echo
echo "== Step 3: Disabling screen blanking via raspi-config =="
if command -v raspi-config &>/dev/null; then
  raspi-config nonint do_blanking 1
  if [ $? -eq 0 ]; then
    echo "Screen blanking disabled."
  else
    echo "Warning: raspi-config do_blanking failed. You may need to disable blanking manually."
  fi
else
  echo "Warning: raspi-config not found; skipping screen-blanking changes."
fi

# Remove mouse cursor from X sessions
sed -i -- "s/#xserver-command=X/xserver-command=X -nocursor/" /etc/lightdm/lightdm.conf

# -------------------------------------------------------
# 3a) Update boot firmware configuration (optional)
# -------------------------------------------------------
echo
echo "== Step 3a: Updating boot firmware configuration in /boot/firmware/config.txt =="
if [ -f /boot/firmware/config.txt ]; then
  cp /boot/firmware/config.txt /boot/firmware/config.txt.backup

  # Insert dtoverlay if missing
  grep -q '^dtoverlay=vc4-kms-v3d' "/boot/firmware/config.txt" || \
    sed -i '/^# Enable DRM VC4 V3D driver/ a dtoverlay=vc4-kms-v3d' "/boot/firmware/config.txt"

  # Insert max_framebuffers if missing
  grep -q '^max_framebuffers=2' "/boot/firmware/config.txt" || \
    sed -i '/^dtoverlay=vc4-kms-v3d/ a max_framebuffers=2' "/boot/firmware/config.txt"

  # Insert hdmi_force_hotplug if missing
  grep -q '^hdmi_force_hotplug=1' "/boot/firmware/config.txt" || \
    sed -i '/^max_framebuffers=2/ a hdmi_force_hotplug=1' "/boot/firmware/config.txt"
fi

# -------------------------------------------------------
# 4) Prompt user for config (skip if AUTO_UPDATE)
# -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "false" ]]; then
  echo
  echo "== Step 4: Configuration =="
  read -p "Enter the Linux username to run PiViewer (default: pi): " VIEWER_USER
  VIEWER_USER=${VIEWER_USER:-pi}

  USER_ID="$(id -u "$VIEWER_USER" 2>/dev/null)"
  if [ -z "$USER_ID" ]; then
    echo "User '$VIEWER_USER' not found. Create user? (y/n)"
    read create_user
    if [[ "$create_user" =~ ^[Yy]$ ]]; then
      adduser --gecos "" --disabled-password "$VIEWER_USER"
      USER_ID="$(id -u "$VIEWER_USER")"
      echo "User '$VIEWER_USER' created with no password."
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
  echo
  echo "== Auto-Update Mode: skipping interactive user/path prompts. Using defaults =="
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
chown "$VIEWER_USER:$VIEWER_USER" "$VIEWER_HOME"

# -------------------------------------------------------
# 5) Create .env
# -------------------------------------------------------
ENV_FILE="$VIEWER_HOME/.env"
echo "Creating $ENV_FILE with VIEWER_HOME + IMAGE_DIR..."
cat <<EOF > "$ENV_FILE"
VIEWER_HOME=$VIEWER_HOME
IMAGE_DIR=$IMAGE_DIR
EOF
chown "$VIEWER_USER:$VIEWER_USER" "$ENV_FILE"

echo
echo "Contents of $ENV_FILE:"
cat "$ENV_FILE"
echo

# -------------------------------------------------------
# 6) (Optional) Configure CIFS/SMB share
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
      USER_ID="$(id -u "$VIEWER_USER")"
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
  else
    echo "No network share chosen. Setting up local uploads folder."
    IMAGE_DIR="$VIEWER_HOME/Uploads"
    mkdir -p "$IMAGE_DIR"
    chown $VIEWER_USER:$VIEWER_USER "$IMAGE_DIR"
    echo "Local uploads folder created at $IMAGE_DIR."
    echo "Updating .env file with new IMAGE_DIR..."
    cat <<EOF > "$ENV_FILE"
VIEWER_HOME=$VIEWER_HOME
IMAGE_DIR=$IMAGE_DIR
EOF
    chown "$VIEWER_USER:$VIEWER_USER" "$ENV_FILE"
    echo "Updated .env file:"
    cat "$ENV_FILE"
  fi
else
  echo
  echo "== Auto-Update Mode: skipping CIFS prompt. =="
fi

# -------------------------------------------------------
# 7) Create systemd services for piviewer + controller
# -------------------------------------------------------
echo
echo "== Step 7: Creating systemd service files =="

PIVIEWER_SERVICE="/etc/systemd/system/piviewer.service"
echo "Creating $PIVIEWER_SERVICE ..."
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
Environment="QT_QPA_PLATFORM_PLUGIN_PATH=/usr/local/lib/python3.11/dist-packages/PySide6/Qt/plugins/platforms"
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 $VIEWER_HOME/piviewer.py

Restart=always
RestartSec=5
Type=simple

[Install]
WantedBy=graphical.target
EOF

CONTROLLER_SERVICE="/etc/systemd/system/controller.service"
echo "Creating $CONTROLLER_SERVICE ..."
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

# -------------------------------------------------------
# 8) Configure Openbox autologin & picom in openbox autostart
# -------------------------------------------------------
echo
echo "== Step 8: Configure Openbox autologin and autostart (with picom) =="

mkdir -p /etc/lightdm/lightdm.conf.d
cat <<EOF >/etc/lightdm/lightdm.conf.d/99-openbox-autologin.conf
[Seat:*]
greeter-session=lightdm-gtk-greeter
user-session=openbox
autologin-user=$VIEWER_USER
autologin-user-timeout=0
autologin-session=openbox
EOF

OPENBOX_CONF_DIR="/home/$VIEWER_USER/.config/openbox"
mkdir -p "$OPENBOX_CONF_DIR"
AUTOSTART_FILE="$OPENBOX_CONF_DIR/autostart"

cat <<EOF > "$AUTOSTART_FILE"
#!/usr/bin/env bash
# Minimal openbox autostart
# Start picom after X is ready

# Set a black root just in case
xsetroot -solid black

# Start picom in background
picom &
EOF

chown -R "$VIEWER_USER:$VIEWER_USER" "/home/$VIEWER_USER/.config/openbox"
chmod +x "$AUTOSTART_FILE"

echo "Done configuring Openbox autostart."

# -------------------------------------------------------
# 9) Reboot (skip if AUTO_UPDATE)
# -------------------------------------------------------
if [[ "$AUTO_UPDATE" == "false" ]]; then
  echo
  echo "========================================================"
  echo "Setup is complete. The Pi will now reboot."
  echo "Upon reboot:"
  echo " - LightDM auto-logs into X/Openbox (DISPLAY=:0)"
  echo " - openbox/autostart runs picom"
  echo " - piviewer.service runs piviewer.py"
  echo " - controller.service runs Flask at http://<Pi-IP>:8080"
  echo
  echo "Rebooting in 5 seconds..."
  sleep 5
  reboot
else
  echo
  echo "== Auto-Update Mode: skipping final reboot. =="
fi
