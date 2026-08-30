#requires -RunAsAdministrator
param(
  [string]$Mount = "X:\",
  [string]$Config = "$PSScriptRoot\config.json"
)
$dest = Join-Path $Mount "AIAgent"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "$PSScriptRoot\..\agent\agent.py" "$dest\agent.py" -Force
if (Test-Path $Config) { Copy-Item $Config "$dest\config.json" -Force }
Copy-Item "$PSScriptRoot\startnet.cmd" (Join-Path $Mount "Windows\System32\startnet.cmd") -Force -ErrorAction SilentlyContinue
Write-Host "Agent injected into $dest"
