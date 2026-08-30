@echo off
chcp 65001 >nul 2>&1
title AI PC Agent USB
cd /d "%~dp0"

net session >nul 2>&1
if not %errorLevel%==0 (
  echo Run as Administrator - confirm UAC.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "SERVER=http://localhost:8000"
set "TOKEN="
if not exist "%~dp0usb_maker.ps1" (
  echo Scripts not found locally. Downloading from %SERVER% ...
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing '%SERVER%/usb-maker.ps1' -OutFile '%~dp0usb_maker.ps1'; Invoke-WebRequest -UseBasicParsing '%SERVER%/usb-maker/write_linux_usb.ps1' -OutFile '%~dp0write_linux_usb.ps1' } catch { Write-Host $_; exit 1 }"
)

if not exist "%~dp0usb_maker.ps1" (
  echo.
  echo usb_maker.ps1 not found.
  echo Download usb-maker.bat from the website and run that file.
  pause
  exit /b 1
)

powershell -STA -NoProfile -ExecutionPolicy Bypass -File "%~dp0usb_maker.ps1" -ServerUrl "%SERVER%" -Token "%TOKEN%"
if errorlevel 1 (
  echo.
  echo GUI failed to start. See error above.
  pause
)
