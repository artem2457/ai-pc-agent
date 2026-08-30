#!/usr/bin/env python3
"""AI PC Agent — connects out to the cloud server and runs commands."""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

try:
    import websockets
except ImportError:
    websockets = None

VERSION = "0.1.0"
ALLOWED = {
    "run_powershell",
    "run_shell",
    "get_hardware",
    "get_system_info",
    "install_package",
    "download_file",
    "reboot",
    "shutdown",
    "manage_service",
    "install_windows",
    "partition_disk",
    "read_file",
    "write_file",
    "upload_file",
    "get_processes",
    "get_services",
    "get_screen",
}


def ok(msg: str):
    print(f"[OK] {msg}", flush=True)


class MiniWS:
    """RFC6455 client using only the standard library (WinPE embeddable Python)."""

    def __init__(self, url: str):
        from urllib.parse import urlparse

        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        self.path = u.path or "/"
        if u.query:
            self.path += "?" + u.query
        if (u.scheme == "wss" and self.port == 443) or (u.scheme == "ws" and self.port == 80):
            self._host_header = self.host
        else:
            self._host_header = f"{self.host}:{self.port}"
        raw = socket.create_connection((self.host, self.port), timeout=60)
        if u.scheme == "wss":
            try:
                ctx = ssl.create_default_context()
                self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
            except ssl.SSLError:
                raw.close()
                raw = socket.create_connection((self.host, self.port), timeout=60)
                ctx = ssl._create_unverified_context()
                self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        self.sock.settimeout(120)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self._host_header}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake")
            buf += chunk
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            raise ConnectionError(buf[:180].decode("latin1", "replace"))

    def send(self, text: str):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        hdr = bytes([0x81])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(hdr + mask + masked)

    def recv(self) -> str:
        while True:
            opcode, payload = self._read_frame()
            if opcode == 0x8:
                raise ConnectionError("close")
            if opcode == 0x9:
                self._send_raw(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            return payload.decode("utf-8", "replace")

    def _send_raw(self, opcode: int, payload: bytes):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            hdr = bytes([0x80 | opcode, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", n)
        else:
            hdr = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(hdr + mask + masked)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("eof")
            buf += chunk
        return buf

    def _read_frame(self):
        data = self._read_exact(2)
        b1, b2 = data[0], data[1]
        opcode = b1 & 0x0F
        masked = b2 >> 7
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(n)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def close(self):
        try:
            self._send_raw(0x8, b"")
            self.sock.close()
        except Exception:
            pass


def detect_os() -> str:
    if os.environ.get("WINPE") == "1" or Path("X:\\Windows").exists():
        return "winpe"
    if platform.system() == "Windows":
        return "windows"
    return "linux"


def hardware() -> dict:
    info = {
        "cpu": platform.processor() or platform.machine(),
        "os": platform.platform(),
        "hostname": socket.gethostname(),
        "ram_gb": None,
        "disk": None,
    }
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / (1024**3), 1)
        disks = []
        for p in psutil.disk_partitions():
            try:
                u = psutil.disk_usage(p.mountpoint)
                disks.append({"mount": p.mountpoint, "total_gb": round(u.total / (1024**3), 1)})
            except Exception:
                pass
        info["disk"] = disks
    except Exception:
        pass
    return info


def default_download_path(os_name: str) -> str:
    if os_name in ("windows", "winpe"):
        for letter in "DEFGWH":
            p = Path(f"{letter}:/")
            if p.exists():
                return str(p / "windows.iso")
        return r"X:\windows.iso"
    return "/tmp/download.bin"


def download_url(url: str, dest: str) -> None:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "AI-PC-Agent"})
    with urllib.request.urlopen(req, timeout=7200) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def run(cmd: list[str] | str, shell=False, timeout=170) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        return -1, e.stdout or "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def install_package(name: str, os_name: str) -> tuple[int, str, str]:
    if os_name in ("windows", "winpe"):
        if shutil.which("winget"):
            return run(["winget", "install", "-e", "--id", name, "--accept-package-agreements", "--accept-source-agreements"])
        return run(["choco", "install", name, "-y"])
    if shutil.which("apt-get"):
        cmd = f"export DEBIAN_FRONTEND=noninteractive; apt-get update -y && apt-get install -y {name}"
        return run(cmd, shell=True)
    if shutil.which("dnf"):
        return run(["dnf", "install", "-y", name])
    return 1, "", "no package manager"


def handle(action: str, params: dict, os_name: str) -> dict:
    if action not in ALLOWED:
        return {"exit_code": 2, "stdout": "", "stderr": f"unknown action {action}", "data": {}}
    if action == "get_hardware" or action == "get_system_info":
        data = hardware()
        return {"exit_code": 0, "stdout": json.dumps(data, ensure_ascii=False, indent=2), "stderr": "", "data": data}
    if action == "run_powershell":
        script = params.get("script") or "Write-Output 'ok'"
        code, out, err = run(["powershell", "-NoProfile", "-Command", script])
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "run_shell":
        script = params.get("script") or "uname -a"
        code, out, err = run(script, shell=True)
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "install_package":
        code, out, err = install_package(params.get("name") or "", os_name)
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "download_file":
        url = params.get("url")
        dest = params.get("path") or default_download_path(os_name)
        try:
            download_url(url, dest)
            return {"exit_code": 0, "stdout": dest, "stderr": "", "data": {"path": dest}}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}
    if action == "reboot":
        if os_name in ("windows", "winpe"):
            subprocess.Popen(["shutdown", "/r", "/t", "5"])
        else:
            subprocess.Popen(["reboot"])
        return {"exit_code": 0, "stdout": "reboot scheduled", "stderr": "", "data": {}}
    if action == "shutdown":
        if os_name in ("windows", "winpe"):
            subprocess.Popen(["shutdown", "/s", "/t", "5"])
        else:
            subprocess.Popen(["shutdown", "-h", "now"])
        return {"exit_code": 0, "stdout": "shutdown scheduled", "stderr": "", "data": {}}
    if action == "read_file":
        path = Path(params.get("path") or "")
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
            return {"exit_code": 0, "stdout": data[-80000:], "stderr": "", "data": {"path": str(path)}}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}
    if action == "write_file":
        path = Path(params.get("path") or "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.get("content") or "", encoding="utf-8")
            return {"exit_code": 0, "stdout": str(path), "stderr": "", "data": {"path": str(path)}}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}
    if action == "upload_file":
        path = Path(params.get("path") or "")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            b64 = params.get("content_base64") or ""
            if b64:
                import base64

                path.write_bytes(base64.b64decode(b64))
            else:
                path.write_text(params.get("content") or "", encoding="utf-8")
            return {"exit_code": 0, "stdout": str(path), "stderr": "", "data": {"path": str(path)}}
        except Exception as e:
            return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}
    if action == "get_processes":
        try:
            import psutil

            lines = [f"{p.pid}\t{p.name()}" for p in psutil.process_iter(["pid", "name"])]
            return {"exit_code": 0, "stdout": "\n".join(lines[-400:]), "stderr": "", "data": {}}
        except Exception:
            if os_name in ("windows", "winpe"):
                code, out, err = run(["tasklist"])
            else:
                code, out, err = run(["ps", "aux"])
            return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "get_services":
        if os_name == "linux":
            code, out, err = run(["systemctl", "list-units", "--type=service", "--no-pager"])
        else:
            code, out, err = run(["sc", "query", "state=", "all"])
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "get_screen":
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "screenshot skipped; use execute_command for console output",
            "data": {"available": False},
        }
    if action == "manage_service":
        name = params.get("name")
        op = params.get("op") or "status"
        if os_name == "linux":
            code, out, err = run(["systemctl", op, name])
        else:
            code, out, err = run(["sc", op, name])
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "partition_disk":
        if os_name not in ("windows", "winpe"):
            return {"exit_code": 1, "stdout": "", "stderr": "only windows/winpe", "data": {}}
        script = r"""
$disk = Get-Disk | Where-Object { $_.PartitionStyle -eq 'RAW' -or $_.Number -eq 0 } | Select-Object -First 1
Get-Disk | Format-Table Number, FriendlyName, Size, PartitionStyle
Write-Output 'partition_disk: inspect only unless confirmed; use Autounattend for real install'
"""
        code, out, err = run(["powershell", "-NoProfile", "-Command", script])
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "install_windows":
        key = (params.get("product_key") or "").replace("'", "").replace('"', "")
        image = (params.get("image") or "").replace("'", "")
        script = r"""
$ErrorActionPreference = 'Continue'
$key = 'PRODUCTKEY'
$image = 'IMAGEPATH'
function Find-Setup($root) {
  if (-not $root) { return $null }
  Get-ChildItem -Path $root -Filter setup.exe -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
}
if ($image -like '*.iso' -and (Test-Path $image)) {
  $mounted = Mount-DiskImage -ImagePath $image -PassThru
  Start-Sleep -Seconds 3
  $letter = ($mounted | Get-Volume).DriveLetter
  if ($letter) { $image = "$letter`:\" }
}
$roots = @()
if ($image) { $roots += $image }
$roots += @('X:\','C:\','D:\','E:\','F:\','G:\','W:\')
$iso = Get-ChildItem -Path X:\,C:\,D:\,E:\,F:\,G:\,W:\ -Filter *.iso -ErrorAction SilentlyContinue | Select-Object -First 1
if ($iso) {
  $mounted = Mount-DiskImage -ImagePath $iso.FullName -PassThru -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 3
  $letter = ($mounted | Get-Volume).DriveLetter
  if ($letter) { $roots = @("$letter`:\") + $roots }
}
$setup = $null
foreach ($r in $roots) { $setup = Find-Setup $r; if ($setup) { break } }
if (-not $setup) {
  Write-Output 'setup.exe not found. Grok should download_file the official Windows ISO first, then call install_os.'
  exit 1
}
Write-Output ("setup=" + $setup.FullName)
$arg = '/auto upgrade /noreboot'
if ($key) { $arg = "/pkey $key $arg" }
Start-Process -FilePath $setup.FullName -ArgumentList $arg -Wait -ErrorAction SilentlyContinue
Write-Output 'setup finished or launched'
"""
        script = script.replace("PRODUCTKEY", key).replace("IMAGEPATH", image)
        code, out, err = run(["powershell", "-NoProfile", "-Command", script], timeout=7200)
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    return {"exit_code": 2, "stdout": "", "stderr": "unhandled", "data": {}}


def load_config(path: Path | None) -> dict:
    cfg = {}
    for candidate in [
        path,
        Path("config.json"),
        Path("/etc/ai-pc-agent/config.json"),
        Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "AIAgent" / "config.json",
    ]:
        if candidate and Path(candidate).exists():
            cfg = json.loads(Path(candidate).read_text(encoding="utf-8-sig"))
            break
    return cfg


def register_payload(token: str, device_id: str, os_name: str, hw: dict) -> dict:
    return {
        "type": "register",
        "device_id": device_id,
        "token": token,
        "os": os_name,
        "agent_version": VERSION,
        "hostname": socket.gethostname(),
        "hardware": hw,
    }


_chat_opened = False


def http_base_from_ws(ws_url: str) -> str:
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[len("wss://") :].split("/ws/", 1)[0]
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[len("ws://") :].split("/ws/", 1)[0]
    return ws_url.rstrip("/")


def open_pc_chat(http_url: str, token: str, device_id: str):
    global _chat_opened
    if _chat_opened or not http_url or not token:
        return
    _chat_opened = True
    q = urllib.parse.urlencode({"token": token, "device": device_id})
    chat_url = f"{http_url.rstrip('/')}/pc-chat?{q}"
    print(f"[OK] Chat: {chat_url}", flush=True)
    try:
        if webbrowser.open(chat_url, new=1):
            return
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            os.startfile(chat_url)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", chat_url])
        elif os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            subprocess.Popen(["xdg-open", chat_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def loop_websockets(url: str, token: str, device_id: str, os_name: str, hw: dict):
    import asyncio

    async def run_once(ws):
        await ws.send(json.dumps(register_payload(token, device_id, os_name, hw)))
        ack = json.loads(await ws.recv())
        ok(f"Server connected ({ack.get('device_id', device_id)})")
        open_pc_chat(http_base_from_ws(url), token, ack.get("device_id", device_id))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("type") != "command":
                continue
            print(f"SERVER → {msg.get('action')}", flush=True)
            result = handle(msg.get("action"), msg.get("params") or {}, os_name)
            await ws.send(
                json.dumps(
                    {
                        "type": "result",
                        "command_id": msg.get("id"),
                        "exit_code": result["exit_code"],
                        "stdout": result["stdout"],
                        "stderr": result["stderr"],
                        "data": result.get("data") or {},
                    }
                )
            )

    async def main_loop():
        while True:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=30) as ws:
                    await run_once(ws)
            except Exception as e:
                print(f"[WAIT] reconnect in 5s: {e}", flush=True)
                await asyncio.sleep(5)

    asyncio.run(main_loop())


def loop_miniws(url: str, token: str, device_id: str, os_name: str, hw: dict):
    while True:
        ws = None
        try:
            ws = MiniWS(url)
            ws.send(json.dumps(register_payload(token, device_id, os_name, hw)))
            ack = json.loads(ws.recv())
            ok(f"Server connected ({ack.get('device_id', device_id)})")
            open_pc_chat(http_base_from_ws(url), token, ack.get("device_id", device_id))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("type") != "command":
                    continue
                print(f"SERVER → {msg.get('action')}", flush=True)
                result = handle(msg.get("action"), msg.get("params") or {}, os_name)
                ws.send(
                    json.dumps(
                        {
                            "type": "result",
                            "command_id": msg.get("id"),
                            "exit_code": result["exit_code"],
                            "stdout": result["stdout"],
                            "stderr": result["stderr"],
                            "data": result.get("data") or {},
                        }
                    )
                )
        except Exception as e:
            print(f"[WAIT] reconnect in 5s: {e}", flush=True)
            time.sleep(5)
        finally:
            if ws:
                ws.close()


def loop_sync(url: str, token: str, device_id: str):
    os_name = detect_os()
    hw = hardware()
    print(f"AI PC Agent v{VERSION}")
    ok("Network connected")
    ok(f"Device ID: {device_id}")
    ok(f"OS: {os_name}")
    print("Waiting for commands...", flush=True)
    if websockets is not None:
        loop_websockets(url, token, device_id, os_name, hw)
    else:
        loop_miniws(url, token, device_id, os_name, hw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("AGENT_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""))
    parser.add_argument("--device-id", default=os.environ.get("DEVICE_ID", socket.gethostname().upper()[:16] or "PC"))
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    cfg = load_config(Path(args.config) if args.config else None)
    token = args.token or cfg.get("token") or ""
    url = args.url or cfg.get("server_url") or "http://localhost:8000"
    if not token:
        print("Need --token (download install-agent.bat from the website)")
        sys.exit(1)
    base = url.rstrip("/")
    if base.startswith("http://"):
        ws = "ws://" + base[len("http://") :] + "/ws/agent"
    elif base.startswith("https://"):
        ws = "wss://" + base[len("https://") :] + "/ws/agent"
    else:
        ws = base
    loop_sync(ws, token, args.device_id)


if __name__ == "__main__":
    main()
