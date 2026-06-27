#!/usr/bin/env bash
set -euo pipefail

# TeslaCam Plate & Object Detection Dashboard installer.
# Run from the cloned repository or extracted release folder:
#   sudo bash install.sh

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh" >&2
  exit 1
fi

APP_USER="${SUDO_USER:-pi}"
if ! id "$APP_USER" >/dev/null 2>&1; then
  APP_USER="pi"
fi
APP_GROUP="$(id -gn "$APP_USER")"
APP_DIR="/opt/teslacam-plate-dashboard"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv python3-pip python3-opencv tesseract-ocr ffmpeg rsync

mkdir -p "$APP_DIR"
if [[ "$SCRIPT_DIR" != "$APP_DIR" ]]; then
  rsync -a --delete \
    --exclude '.git' \
    --exclude 'config.json' \
    --exclude 'data' \
    --exclude 'static/detections' \
    --exclude 'static/objects' \
    --exclude 'venv' \
    --exclude '__pycache__' \
    "$SCRIPT_DIR/" "$APP_DIR/"
fi

if [[ ! -f "$APP_DIR/config.json" ]]; then
  cp "$APP_DIR/config.example.json" "$APP_DIR/config.json"
fi

mkdir -p "$APP_DIR/data" "$APP_DIR/static/detections" "$APP_DIR/static/objects"

# Pin local database/static paths to this installation, and prefer a TeslaUSB
# mount when one is present. The actual source footage is never copied.
python3 - "$APP_DIR" <<'PY'
import json
import sys
from pathlib import Path

app_dir = Path(sys.argv[1])
config_path = app_dir / "config.json"
data = json.loads(config_path.read_text(encoding="utf-8"))
data.setdefault("paths", {})
data["paths"]["db_path"] = str(app_dir / "data" / "plates.db")
data["paths"]["static_dir"] = str(app_dir / "static")

candidates = [
    "/mnt/gadget/part1-ro/TeslaCam",
    "/mnt/gadget/part1/TeslaCam",
    "/mnt/teslacam/TeslaCam",
    "/mnt/tesla/TeslaCam",
    "/media/pi/TeslaCam",
    "/media/pi/TeslaUSB/TeslaCam",
]
for candidate in candidates:
    if Path(candidate).is_dir():
        data["paths"]["teslacam_root"] = candidate
        break

config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
print(f"Configured {config_path}")
PY

python3 -m venv --system-site-packages "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel setuptools
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

cat > /etc/systemd/system/teslacam-plate-web.service <<EOF
[Unit]
Description=TeslaCam Plate Dashboard (web)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m app.webapp --config $APP_DIR/config.json --host 0.0.0.0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=teslacam-plate.target
EOF

cat > /etc/systemd/system/teslacam-plate-scanner.service <<EOF
[Unit]
Description=TeslaCam Plate Dashboard (scanner)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python -m scanner.run_scanner --config $APP_DIR/config.json
Restart=on-failure
RestartSec=5

[Install]
WantedBy=teslacam-plate.target
EOF

cat > /etc/systemd/system/teslacam-plate.target <<EOF
[Unit]
Description=TeslaCam Plate Dashboard
Wants=teslacam-plate-web.service teslacam-plate-scanner.service
After=network.target

[Install]
WantedBy=multi-user.target
EOF

chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
chmod -R u+rwX "$APP_DIR/data" "$APP_DIR/static"

systemctl daemon-reload
systemctl enable --now teslacam-plate.target

printf '\nInstalled to: %s\n' "$APP_DIR"
printf 'Dashboard:    http://HOSTNAME-OR-IP:5057\n'
printf 'Config:       %s/config.json\n' "$APP_DIR"
printf 'Status:       sudo systemctl status teslacam-plate-web.service teslacam-plate-scanner.service --no-pager -l\n'
