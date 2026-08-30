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

VERSION = "0.2.1"
ALLOWED = {
    "run_powershell",
    "run_shell",
    "get_hardware",
    "get_system_info",
    "install_package",
    "uninstall_package",
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
    "click",
    "type_text",
    "press_key",
    "scroll",
    "open_remote_assistance",
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
            stdin=subprocess.DEVNULL,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        return -1, e.stdout or "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _join_logs(chunks: list[str]) -> str:
    return "\n\n".join(c for c in chunks if c).strip()


def _winget_try(commands: list[list[str]]) -> tuple[int, str, str]:
    logs: list[str] = []
    last = (1, "", "winget failed")
    for cmd in commands:
        code, out, err = run(cmd, timeout=600)
        block = f"$ {' '.join(cmd)}\n{out}\n{err}".strip()
        logs.append(block)
        last = (code, _join_logs(logs), err)
        if code == 0:
            return 0, _join_logs(logs), err
    return last


def install_package(name: str, os_name: str) -> tuple[int, str, str]:
    name = (name or "").strip()
    if not name:
        return 1, "", "empty package name"
    if os_name in ("windows", "winpe"):
        if shutil.which("winget"):
            return _winget_try(
                [
                    ["winget", "install", "--id", name, "-e", "--source", "winget", "--disable-interactivity", "--accept-package-agreements", "--accept-source-agreements"],
                    ["winget", "install", "--name", name, "--source", "winget", "--disable-interactivity", "--accept-package-agreements", "--accept-source-agreements"],
                    ["winget", "install", name, "--source", "winget", "--disable-interactivity", "--accept-package-agreements", "--accept-source-agreements"],
                ]
            )
        if shutil.which("choco"):
            return run(["choco", "install", name, "-y"], timeout=600)
        return 1, "", "no package manager"
    if shutil.which("apt-get"):
        cmd = f"export DEBIAN_FRONTEND=noninteractive; apt-get update -y && apt-get install -y {name}"
        return run(cmd, shell=True, timeout=600)
    if shutil.which("dnf"):
        return run(["dnf", "install", "-y", name], timeout=600)
    return 1, "", "no package manager"


def uninstall_package(name: str, os_name: str) -> tuple[int, str, str]:
    name = (name or "").strip()
    if not name:
        return 1, "", "empty package name"
    if os_name in ("windows", "winpe"):
        if shutil.which("winget"):
            return _winget_try(
                [
                    ["winget", "uninstall", "--id", name, "-e", "--source", "winget", "--disable-interactivity"],
                    ["winget", "uninstall", "--name", name, "--source", "winget", "--disable-interactivity"],
                    ["winget", "uninstall", name, "--disable-interactivity"],
                ]
            )
        if shutil.which("choco"):
            return run(["choco", "uninstall", name, "-y"], timeout=600)
        return 1, "", "no package manager"
    if shutil.which("apt-get"):
        cmd = f"export DEBIAN_FRONTEND=noninteractive; apt-get remove -y {name}"
        return run(cmd, shell=True, timeout=600)
    if shutil.which("dnf"):
        return run(["dnf", "remove", "-y", name], timeout=600)
    return 1, "", "no package manager"


SCREEN_MAX_W = 1280
JPEG_QUALITY = 55
VK = {
    "return": 0x0D,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "f5": 0x74,
    "y": 0x59,
    "n": 0x4E,
}


def _win_dpi_aware():
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _screen_size() -> tuple[int, int]:
    if sys.platform == "win32":
        import ctypes

        _win_dpi_aware()
        u = ctypes.windll.user32
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    try:
        code, out, _err = run(["xdotool", "getdisplaygeometry"], timeout=5)
        if code == 0 and out.strip():
            parts = out.split()
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1920, 1080


def _jpeg_from_pil(img) -> tuple[bytes, int, int]:
    from io import BytesIO

    img = img.convert("RGB")
    w, h = img.size
    if w > SCREEN_MAX_W:
        h = max(1, int(h * SCREEN_MAX_W / w))
        w = SCREEN_MAX_W
        img = img.resize((w, h))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue(), w, h


def _screenshot_pil() -> tuple[bytes, int, int, int, int] | None:
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab()
        sw, sh = img.size
        data, iw, ih = _jpeg_from_pil(img)
        return data, iw, ih, sw, sh
    except Exception:
        return None


def _screenshot_windows() -> tuple[bytes, int, int, int, int] | None:
    dest = Path(os.environ.get("TEMP", ".")) / "ai-pc-screen.jpg"
    dest_ps = str(dest).replace("'", "")
    script = rf"""
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap ([int]$b.Width), ([int]$b.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen([int]$b.X, [int]$b.Y, 0, 0, $bmp.Size)
$maxW = {SCREEN_MAX_W}
$outW = $bmp.Width
$outH = $bmp.Height
if ($bmp.Width -gt $maxW) {{
  $outW = $maxW
  $outH = [int]($bmp.Height * $maxW / $bmp.Width)
  $thumb = New-Object System.Drawing.Bitmap $outW, $outH
  $tg = [System.Drawing.Graphics]::FromImage($thumb)
  $tg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $tg.DrawImage($bmp, 0, 0, $outW, $outH)
  $tg.Dispose()
  $bmp.Dispose()
  $bmp = $thumb
}}
$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object {{ $_.MimeType -eq 'image/jpeg' }}
$ep = New-Object System.Drawing.Imaging.EncoderParameters 1
$ep.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality, [long]{JPEG_QUALITY})
$bmp.Save('{dest_ps}', $codec, $ep)
Write-Output ("SIZE " + $b.Width + " " + $b.Height + " " + $outW + " " + $outH)
$g.Dispose(); $bmp.Dispose()
"""
    code, out, _err = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=25)
    if code != 0 or not dest.exists() or dest.stat().st_size < 100:
        return None
    data = dest.read_bytes()
    sw, sh = _screen_size()
    iw, ih = sw, sh
    for line in (out or "").splitlines():
        if line.startswith("SIZE "):
            parts = line.split()
            if len(parts) >= 5:
                sw, sh, iw, ih = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
    return data, iw, ih, sw, sh


def _screenshot_linux() -> tuple[bytes, int, int, int, int] | None:
    dest = Path("/tmp/ai-pc-screen.jpg")
    cmds = [
        ["import", "-window", "root", "-quality", str(JPEG_QUALITY), str(dest)],
        ["scrot", "-z", str(dest)],
        ["gnome-screenshot", "-f", str(dest)],
    ]
    for cmd in cmds:
        if not shutil.which(cmd[0]):
            continue
        code, _out, _err = run(cmd, timeout=15)
        if code == 0 and dest.exists() and dest.stat().st_size > 100:
            data = dest.read_bytes()
            sw, sh = _screen_size()
            return data, sw, sh, sw, sh
    return None


def capture_screen() -> dict:
    got = _screenshot_pil()
    if not got and sys.platform == "win32":
        got = _screenshot_windows()
    if not got:
        got = _screenshot_linux()
    if not got:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "screenshot failed: no capture backend (PIL / PowerShell / import)",
            "data": {"available": False},
        }
    data, iw, ih, sw, sh = got
    b64 = base64.b64encode(data).decode("ascii")
    return {
        "exit_code": 0,
        "stdout": f"screenshot {iw}x{ih} (screen {sw}x{sh}) jpeg {len(data)} bytes",
        "stderr": "",
        "data": {
            "available": True,
            "mime": "image/jpeg",
            "image_base64": b64,
            "image_width": iw,
            "image_height": ih,
            "screen_width": sw,
            "screen_height": sh,
        },
    }


def _map_point(params: dict) -> tuple[int, int]:
    x = float(params.get("x") or 0)
    y = float(params.get("y") or 0)
    iw = float(params.get("image_width") or 0)
    ih = float(params.get("image_height") or 0)
    sw, sh = _screen_size()
    if iw > 0 and ih > 0:
        x = x * sw / iw
        y = y * sh / ih
    return max(0, min(sw - 1, int(round(x)))), max(0, min(sh - 1, int(round(y))))


def _win_user32():
    import ctypes

    _win_dpi_aware()
    return ctypes.windll.user32


def _win_click(x: int, y: int, button: str):
    u = _win_user32()
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    down_up = {
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }.get(button, (0x0002, 0x0004))
    times = 2 if button == "double" else 1
    if button == "double":
        down_up = (0x0002, 0x0004)
    for _ in range(times):
        u.mouse_event(down_up[0], 0, 0, 0, 0)
        time.sleep(0.05)
        u.mouse_event(down_up[1], 0, 0, 0, 0)
        time.sleep(0.06)


def _win_vk(name: str):
    vk = VK.get((name or "").strip().lower())
    if vk is None and len(name) == 1:
        vk = ord(name.upper())
    if vk is None:
        raise ValueError(f"unknown key {name}")
    u = _win_user32()
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    u.keybd_event(vk, 0, 2, 0)


def _win_list_windows() -> list[tuple[int, str]]:
    import ctypes
    from ctypes import wintypes

    u = _win_user32()
    found: list[tuple[int, str]] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lp):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n < 1:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title:
            found.append((int(hwnd), title))
        return True

    cb = WNDENUMPROC(_cb)
    u.EnumWindows(cb, 0)
    return found


_SKIP_TITLES = (
    "ai pc agent",
    "ai pc chat",
    "pc-chat",
    "holderchat",
    "windows powershell",
    "командная строка",
    "command prompt",
)


def _win_find_hwnd(hint: str) -> tuple[int, str] | None:
    hint_l = (hint or "").strip().lower()
    needles = []
    if hint_l:
        needles.append(hint_l)
        if hint_l in ("notepad", "блокнот"):
            needles.extend(["notepad", "блокнот", "безымянный"])
    rows = _win_list_windows()
    scored = []
    for hwnd, title in rows:
        low = title.lower()
        if any(s in low for s in _SKIP_TITLES):
            continue
        if needles:
            if any(n in low for n in needles):
                scored.append((hwnd, title))
        elif "notepad" in low or "блокнот" in low:
            scored.append((hwnd, title))
    return scored[0] if scored else None


def _win_force_foreground(hwnd: int) -> bool:
    import ctypes

    u = _win_user32()
    k = ctypes.windll.kernel32
    fg = u.GetForegroundWindow()
    cur = k.GetCurrentThreadId()
    pid = ctypes.c_ulong(0)
    fg_tid = u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    if fg_tid:
        u.AttachThreadInput(cur, fg_tid, True)
    u.ShowWindow(hwnd, 9)
    u.BringWindowToTop(hwnd)
    u.keybd_event(0x12, 0, 0, 0)
    u.keybd_event(0x12, 0, 2, 0)
    ok = bool(u.SetForegroundWindow(hwnd))
    if fg_tid:
        u.AttachThreadInput(cur, fg_tid, False)
    time.sleep(0.2)
    return ok or u.GetForegroundWindow() == hwnd


def _win_click_client_center(hwnd: int):
    import ctypes
    from ctypes import wintypes

    u = _win_user32()
    rect = wintypes.RECT()
    u.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT((rect.right - rect.left) // 2, max(40, (rect.bottom - rect.top) // 2))
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    _win_click(pt.x, pt.y, "left")
    time.sleep(0.2)


def _escape_sendkeys(text: str) -> str:
    out = []
    for ch in text:
        if ch in "+^%~(){}[]":
            out.append("{" + ch + "}")
        elif ch == "\n":
            out.append("{ENTER}")
        elif ch == "\t":
            out.append("{TAB}")
        else:
            out.append(ch)
    return "".join(out)


def _win_sendkeys(text: str) -> tuple[int, str]:
    escaped = _escape_sendkeys(text)
    env_name = "AI_PC_SENDKEYS"
    os.environ[env_name] = escaped
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.SendKeys]::SendWait([Environment]::GetEnvironmentVariable('{env_name}'))"
    )
    try:
        return run(
            ["powershell", "-STA", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=30,
        )[:2]
    finally:
        os.environ.pop(env_name, None)


def _win_sendinput_unicode(text: str) -> int:
    import ctypes

    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", ctypes.c_ushort),
            ("wScan", ctypes.c_ushort),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short), ("wParamH", ctypes.c_short)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    pad64 = ctypes.sizeof(ctypes.c_void_p) == 8

    class INPUT(ctypes.Structure):
        _fields_ = (
            [("type", ctypes.c_ulong), ("pad", ctypes.c_ulong), ("union", INPUTUNION)]
            if pad64
            else [("type", ctypes.c_ulong), ("union", INPUTUNION)]
        )

    def _make(ch_code: int, flags: int) -> INPUT:
        inp = INPUT()
        inp.type = 1
        inp.union.ki = KEYBDINPUT(0, ch_code, flags, 0, 0)
        return inp

    chunks: list = []
    for ch in text:
        if ch == "\n":
            if chunks:
                arr = (INPUT * len(chunks))(*chunks)
                ctypes.windll.user32.SendInput(len(chunks), arr, ctypes.sizeof(INPUT))
                chunks = []
            _win_vk("return")
            continue
        code = ord(ch)
        chunks.append(_make(code, KEYEVENTF_UNICODE))
        chunks.append(_make(code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    if not chunks:
        return 1
    arr = (INPUT * len(chunks))(*chunks)
    sent = ctypes.windll.user32.SendInput(len(chunks), arr, ctypes.sizeof(INPUT))
    return int(sent)


def _win_type_into_app(text: str, hint: str, click_xy: tuple[int, int] | None) -> tuple[int, str]:
    notes = []
    found = _win_find_hwnd(hint)
    if found:
        hwnd, title = found
        fg = _win_force_foreground(hwnd)
        notes.append(f"focus '{title}' ok={fg}")
        if click_xy:
            _win_click(click_xy[0], click_xy[1], "left")
            notes.append(f"click {click_xy[0]},{click_xy[1]}")
        else:
            _win_click_client_center(hwnd)
            notes.append("click client center")
    elif click_xy:
        _win_click(click_xy[0], click_xy[1], "left")
        notes.append(f"click {click_xy[0]},{click_xy[1]} (no window match)")
        time.sleep(0.25)
    else:
        notes.append("no window to focus — typing to foreground")

    time.sleep(0.15)
    code, out = _win_sendkeys(text)
    notes.append(f"SendKeys exit={code} {out[:80]}")
    if code != 0:
        sent = _win_sendinput_unicode(text)
        notes.append(f"SendInput fallback={sent}")
    return 0, "; ".join(notes)


def _win_scroll(dy: int):
    _win_user32().mouse_event(0x0800, 0, 0, int(dy) * 120, 0)


def do_click(params: dict) -> dict:
    x, y = _map_point(params)
    button = str(params.get("button") or "left").lower()
    try:
        if sys.platform == "win32":
            _win_click(x, y, button)
        elif shutil.which("xdotool"):
            btn = {"right": "3", "middle": "2"}.get(button, "1")
            run(["xdotool", "mousemove", str(x), str(y), "click", btn], timeout=10)
            if button == "double":
                run(["xdotool", "click", "1"], timeout=5)
        else:
            return {"exit_code": 1, "stdout": "", "stderr": "no click backend", "data": {}}
        return {"exit_code": 0, "stdout": f"click {button} at {x},{y}", "stderr": "", "data": {"x": x, "y": y}}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}


def do_type(params: dict) -> dict:
    text = str(params.get("text") or "")
    if not text:
        return {"exit_code": 1, "stdout": "", "stderr": "empty text", "data": {}}
    text = text[:4000]
    hint = str(params.get("window") or params.get("hint") or "")
    click_xy = None
    if params.get("x") is not None and params.get("y") is not None:
        click_xy = _map_point(params)
    try:
        if sys.platform == "win32":
            code, log = _win_type_into_app(text, hint, click_xy)
            return {"exit_code": code, "stdout": f"typed {len(text)} chars. {log}", "stderr": "", "data": {}}
        if shutil.which("xdotool"):
            if hint:
                run(["xdotool", "search", "--name", hint, "windowactivate"], timeout=10)
            run(["xdotool", "type", "--delay", "12", "--", text], timeout=30)
            return {"exit_code": 0, "stdout": f"typed {len(text)} chars", "stderr": "", "data": {}}
        return {"exit_code": 1, "stdout": "", "stderr": "no type backend", "data": {}}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}


def do_press(params: dict) -> dict:
    key = str(params.get("key") or "").strip()
    if not key:
        return {"exit_code": 1, "stdout": "", "stderr": "empty key", "data": {}}
    try:
        if sys.platform == "win32":
            _win_vk(key)
        elif shutil.which("xdotool"):
            run(["xdotool", "key", key], timeout=10)
        else:
            return {"exit_code": 1, "stdout": "", "stderr": "no key backend", "data": {}}
        return {"exit_code": 0, "stdout": f"key {key}", "stderr": "", "data": {}}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}


def do_scroll(params: dict) -> dict:
    dy = int(params.get("dy") or params.get("amount") or 0)
    if dy == 0:
        return {"exit_code": 1, "stdout": "", "stderr": "dy required", "data": {}}
    try:
        if sys.platform == "win32":
            _win_scroll(dy)
        elif shutil.which("xdotool"):
            run(["xdotool", "click", "--repeat", str(abs(dy)), "4" if dy > 0 else "5"], timeout=10)
        else:
            return {"exit_code": 1, "stdout": "", "stderr": "no scroll backend", "data": {}}
        return {"exit_code": 0, "stdout": f"scroll {dy}", "stderr": "", "data": {}}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e), "data": {}}


def handle(action: str, params: dict, os_name: str) -> dict:
    if action not in ALLOWED:
        return {"exit_code": 2, "stdout": "", "stderr": f"unknown action {action}", "data": {}}
    if action == "get_hardware" or action == "get_system_info":
        data = hardware()
        return {"exit_code": 0, "stdout": json.dumps(data, ensure_ascii=False, indent=2), "stderr": "", "data": data}
    if action == "run_powershell":
        script = params.get("script") or "Write-Output 'ok'"
        wrapped = (
            "$ConfirmPreference='None'; $ProgressPreference='SilentlyContinue'; "
            + script
        )
        slow = any(w in script.lower() for w in ("winget", "choco", "msiexec", "setup.exe"))
        code, out, err = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
            timeout=600 if slow else 170,
        )
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "run_shell":
        script = params.get("script") or "uname -a"
        slow = any(w in script.lower() for w in ("apt-get", "dnf", "yum", "pacman", "winget"))
        code, out, err = run(script, shell=True, timeout=600 if slow else 170)
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "install_package":
        code, out, err = install_package(params.get("name") or "", os_name)
        return {"exit_code": code, "stdout": out, "stderr": err, "data": {}}
    if action == "uninstall_package":
        code, out, err = uninstall_package(params.get("name") or "", os_name)
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
        return capture_screen()
    if action == "click":
        return do_click(params)
    if action == "type_text":
        return do_type(params)
    if action == "press_key":
        return do_press(params)
    if action == "scroll":
        return do_scroll(params)
    if action == "open_remote_assistance":
        if os_name not in ("windows", "winpe"):
            return {"exit_code": 1, "stdout": "", "stderr": "only windows", "data": {}}
        attempts = [
            ["powershell", "-NoProfile", "-Command", "Start-Process 'ms-quick-assist:'"],
            ["cmd", "/c", "start", "", "ms-quick-assist:"],
            ["msra.exe"],
        ]
        logs = []
        for cmd in attempts:
            code, out, err = run(cmd, timeout=30)
            logs.append(f"$ {' '.join(cmd)}\n{out}\n{err}".strip())
            if code == 0:
                return {
                    "exit_code": 0,
                    "stdout": "Быстрая помощь Windows открыта.\n" + "\n".join(logs),
                    "stderr": "",
                    "data": {"tool": "quick_assist"},
                }
        return {
            "exit_code": 0,
            "stdout": "Попытка открыть удалённую помощь.\n" + "\n".join(logs),
            "stderr": "",
            "data": {"tool": "quick_assist"},
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
    parser.add_argument("--url", default=os.environ.get("AGENT_URL") or "")
    parser.add_argument("--token", default=os.environ.get("AGENT_TOKEN") or "")
    parser.add_argument("--device-id", default=os.environ.get("DEVICE_ID") or socket.gethostname().upper()[:16] or "PC")
    parser.add_argument("--config", default="")
    args = parser.parse_args()
    cfg = load_config(Path(args.config) if args.config else None)
    token = (args.token or cfg.get("token") or "").strip()
    url = (args.url or cfg.get("server_url") or "http://localhost:8000").strip()
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
