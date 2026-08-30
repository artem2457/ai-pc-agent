#requires -RunAsAdministrator
param(
  [string]$AdkPath = "C:\Program Files (x86)\Windows Kits\10\Assessment and Deployment Kit",
  [string]$Work = "$PSScriptRoot\work",
  [string]$OutIso = "$PSScriptRoot\ai-pc-agent-winpe.iso"
)

$oc = Join-Path $AdkPath "Windows Preinstallation Environment\amd64\WinPE_OCs"
$copype = Join-Path $AdkPath "Windows Preinstallation Environment\copype.cmd"
$makewinpemedia = Join-Path $AdkPath "Windows Preinstallation Environment\MakeWinPEMedia.cmd"

if (-not (Test-Path $copype)) {
  Write-Host "Install Windows ADK + WinPE add-on first:"
  Write-Host "https://learn.microsoft.com/windows-hardware/get-started/adk-install"
  exit 1
}

Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
& $copype amd64 $Work

$mount = Join-Path $Work "mount"
New-Item -ItemType Directory -Force -Path $mount | Out-Null
dism /Mount-Image /ImageFile:"$Work\media\sources\boot.wim" /Index:1 /MountDir:$mount

Copy-Item "$PSScriptRoot\startnet.cmd" "$mount\Windows\System32\startnet.cmd" -Force
New-Item -ItemType Directory -Force -Path "$mount\AIAgent" | Out-Null
Copy-Item "$PSScriptRoot\..\agent\agent.py" "$mount\AIAgent\agent.py" -Force
if (Test-Path "$PSScriptRoot\config.json") {
  Copy-Item "$PSScriptRoot\config.json" "$mount\AIAgent\config.json" -Force
}

dism /Unmount-Image /MountDir:$mount /Commit
& $makewinpemedia /ISO $Work $OutIso
Write-Host "ISO: $OutIso"
