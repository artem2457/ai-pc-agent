#!/usr/bin/env bash
set -euo pipefail

TOKEN=""
URL="http://localhost:8000"
DEVICE_ID="$(hostname | tr '[:lower:]' '[:upper:]')"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --device-id) DEVICE_ID="$2"; shift 2 ;;
    *) echo "unknown $1"; exit 1 ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  echo "Download install-agent.sh from the website and run: bash install-agent.sh"
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root: sudo bash install-agent.sh"
  exit 1
fi

install_python() {
  if command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y python3 python3-pip curl
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip curl
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip curl
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip curl
  else
    echo "Install python3 and pip, then run this script again."
    exit 1
  fi
}

install_python

mkdir -p /opt/ai-pc-agent /etc/ai-pc-agent
curl -fsSL "$URL/agent.py" -o /opt/ai-pc-agent/agent.py
python3 -m pip install --break-system-packages websockets psutil >/dev/null 2>&1 \
  || python3 -m pip install websockets psutil

cat > /etc/ai-pc-agent/config.json <<EOF
{"server_url": "$URL", "token": "$TOKEN"}
EOF

cat > /etc/systemd/system/ai-pc-agent.service <<EOF
[Unit]
Description=AI PC Agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/ai-pc-agent/agent.py --config /etc/ai-pc-agent/config.json --device-id $DEVICE_ID
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ai-pc-agent
CHAT="${URL%/}/pc-chat?token=${TOKEN}&device=${DEVICE_ID}"
echo "Agent started. Device: $DEVICE_ID"
echo "Chat: $CHAT"
if command -v xdg-open >/dev/null 2>&1 && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
  xdg-open "$CHAT" >/dev/null 2>&1 || true
fi
