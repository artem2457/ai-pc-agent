#!/bin/sh
# Runs on Alpine Linux live USB at boot (OpenRC local.d).
set -e

CFG="/AIAgent/config.json"
AGENT="/AIAgent/agent.py"

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! command -v python3 >/dev/null 2>&1; then
  apk add --no-cache python3 py3-pip >/dev/null 2>&1 || true
fi
python3 -m pip install --break-system-packages websockets psutil >/dev/null 2>&1 \
  || python3 -m pip install websockets psutil >/dev/null 2>&1 \
  || true

if [ ! -f "$CFG" ] || [ ! -f "$AGENT" ]; then
  echo "AIAgent files missing on USB"
  exit 0
fi

while true; do
  python3 "$AGENT" --config "$CFG" --device-id "$(hostname -s 2>/dev/null || echo ALPINE-USB)"
  sleep 10
done
