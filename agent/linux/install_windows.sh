#!/bin/sh
# Prepare Windows install with agent autostart after first boot (Alpine / Linux live USB).
set -eu

URL=""
TOKEN=""
DEVICE_ID="$(hostname -s 2>/dev/null || echo ALPINE-USB)"
ISO=""
CLEAN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --device-id) DEVICE_ID="$2"; shift 2 ;;
    --iso) ISO="$2"; shift 2 ;;
    --clean) CLEAN=1; shift ;;
    *) echo "unknown arg: $1"; exit 1 ;;
  esac
done

if [ -z "$URL" ] || [ -z "$TOKEN" ]; then
  echo "need --url and --token"
  exit 1
fi

BASE="${URL%/}"
STAGE="/tmp/aiagent-stage"
AGENT_DIR="$STAGE/AIAgent"
mkdir -p "$AGENT_DIR"

apk add --no-cache curl ntfs-3g >/dev/null 2>&1 || true

curl -fsSL "$BASE/agent.py" -o "$AGENT_DIR/agent.py"
curl -fsSL "$BASE/install.ps1" -o "$AGENT_DIR/install.ps1"
curl -fsSL "$BASE/winpe/Autounattend-oobe.xml" -o "$AGENT_DIR/Autounattend.xml"
printf '{"server_url":"%s","token":"%s","device_id":"%s"}\n' "$BASE" "$TOKEN" "$DEVICE_ID" > "$AGENT_DIR/config.json"

copy_tree() {
  dest="$1"
  mkdir -p "$dest/AIAgent"
  cp -a "$AGENT_DIR/." "$dest/AIAgent/"
  cp -a "$AGENT_DIR/Autounattend.xml" "$dest/Autounattend.xml" 2>/dev/null || true
  echo "staged -> $dest"
}

# Alpine USB layout
[ -d /AIAgent ] && copy_tree "$(dirname /AIAgent)"

# Removable / data partitions
for part in /dev/sd?? /dev/nvme??n?p?; do
  [ -b "$part" ] || continue
  mp="/mnt/stage-$(basename "$part")"
  mkdir -p "$mp"
  if mount -t ntfs-3g "$part" "$mp" 2>/dev/null || mount "$part" "$mp" 2>/dev/null; then
    copy_tree "$mp"
    umount "$mp" 2>/dev/null || true
  fi
  rmdir "$mp" 2>/dev/null || true
done

ISO="${ISO:-/tmp/windows.iso}"
if [ ! -f "$ISO" ]; then
  for candidate in /tmp/windows.iso /mnt/*/windows.iso /AIAgent/windows.iso; do
    [ -f "$candidate" ] && ISO="$candidate" && break
  done
fi

if [ ! -f "$ISO" ]; then
  echo "windows.iso not found; download first (download_file). Staged AIAgent on available drives."
  exit 0
fi

IM="/mnt/winiso"
mkdir -p "$IM"
if mount -o loop,ro "$ISO" "$IM" 2>/dev/null; then
  copy_tree "$IM"
  if [ "$CLEAN" -eq 1 ] && command -v wimlib-imagex >/dev/null 2>&1; then
    TARGET=""
    for part in /dev/sd?? /dev/nvme??n?p?; do
      [ -b "$part" ] || continue
      mp="/mnt/target"
      mkdir -p "$mp"
      if mount -t ntfs-3g "$part" "$mp" 2>/dev/null; then
        if [ -d "$mp/Windows" ] || [ -f "$mp/bootmgr" ]; then
          umount "$mp" 2>/dev/null || true
          continue
        fi
        echo "Applying install.wim to $part ..."
        wimlib-imagex apply "$IM/sources/install.wim" 1 "$mp" || true
        mkdir -p "$mp/ProgramData/AIAgent" "$mp/Windows/Panther"
        cp -a "$AGENT_DIR/." "$mp/ProgramData/AIAgent/"
        cp -a "$AGENT_DIR/Autounattend.xml" "$mp/Windows/Panther/unattend.xml" 2>/dev/null || true
        TARGET="$part"
        umount "$mp" 2>/dev/null || true
        break
      fi
      umount "$mp" 2>/dev/null || true
    done
    [ -n "$TARGET" ] && echo "Windows image applied to $TARGET; reboot into new Windows"
  else
    echo "ISO mounted at $IM; Autounattend + AIAgent copied to media. Boot from ISO or apply wim manually."
  fi
  umount "$IM" 2>/dev/null || true
else
  echo "Could not mount ISO; AIAgent staged on partitions only."
fi

echo "done"
