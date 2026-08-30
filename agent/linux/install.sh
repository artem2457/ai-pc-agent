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
  echo "Usage: curl -fsSL $URL/install.sh | bash -s -- --token TOKEN --url $URL"
  exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y python3 python3-pip
fi

mkdir -p /opt/ai-pc-agent /etc/ai-pc-agent
curl -fsSL "$URL/agent.py" -o /opt/ai-pc-agent/agent.py
if [[ ! -s /opt/ai-pc-agent/agent.py ]]; then
  echo "download agent.py from the zip or copy manually"
fi
python3 -m pip install --break-system-packages websockets psutil >/dev/null 2>&1 || python3 -m pip install websockets psutil

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
echo "Agent started. Check: systemctl status ai-pc-agent"
