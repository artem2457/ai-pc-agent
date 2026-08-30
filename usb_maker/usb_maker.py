"""Deprecated: USB maker is PowerShell, no Python required."""
import subprocess
import sys
from pathlib import Path

ps1 = Path(__file__).with_name("usb_maker.ps1")
sys.exit(
    subprocess.call(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
    )
)
