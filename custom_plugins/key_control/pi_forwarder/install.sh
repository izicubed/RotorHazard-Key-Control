#!/bin/bash
# Install the KEY CONTROL keyboard forwarder on this machine (Raspberry Pi).
# Usage: ./install.sh http://<rotorhazard-host>:5000
set -e

SERVER_URL="${1:?Usage: ./install.sh http://<rotorhazard-host>:5000}"
DEST=/home/pi/key_control
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== installing dependencies =="
sudo apt-get update -qq
sudo apt-get install -y python3-evdev python3-socketio python3-websocket 2>/dev/null || {
    echo "apt packages unavailable, falling back to pip"
    sudo apt-get install -y python3-pip
    pip3 install evdev "python-socketio[client]" --break-system-packages 2>/dev/null || \
        pip3 install evdev "python-socketio[client]"
}

echo "== installing forwarder to $DEST =="
mkdir -p "$DEST"
cp "$HERE/keyboard_forwarder.py" "$DEST/"
sudo usermod -aG input pi

echo "== installing systemd service =="
sed "s|http://ROTORHAZARD_HOST:5000|$SERVER_URL|" \
    "$HERE/key-control-forwarder.service" | \
    sudo tee /etc/systemd/system/key-control-forwarder.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now key-control-forwarder.service

echo "== done =="
systemctl --no-pager status key-control-forwarder.service || true
echo
echo "Check keyboard order:   python3 $DEST/keyboard_forwarder.py --list"
echo "Discover keycodes:      sudo systemctl stop key-control-forwarder && python3 $DEST/keyboard_forwarder.py --learn"
