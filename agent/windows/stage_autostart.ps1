param(
  [Parameter(Mandatory = $true)][string]$Url,
  [Parameter(Mandatory = $true)][string]$Token,
  [string]$DeviceId = $env:COMPUTERNAME,
  [string]$Image = "",
  [switch]$CleanInstall
)

$ErrorActionPreference = "Stop"
$base = $Url.TrimEnd("/")
$agentDir = "C:\ProgramData\AIAgent"
New-Item -ItemType Directory -Force -Path $agentDir | Out-Null

Write-Host "Downloading agent from $base ..."
Invoke-WebRequest -Uri "$base/agent.py" -OutFile (Join-Path $agentDir "agent.py") -UseBasicParsing
Invoke-WebRequest -Uri "$base/install.ps1" -OutFile (Join-Path $agentDir "install.ps1") -UseBasicParsing
Invoke-WebRequest -Uri "$base/winpe/Autounattend-oobe.xml" -OutFile (Join-Path $agentDir "Autounattend.xml") -UseBasicParsing

$config = @{ server_url = $base; token = $Token; device_id = $DeviceId }
($config | ConvertTo-Json -Compress) | Set-Content (Join-Path $agentDir "config.json") -Encoding UTF8

function Copy-Stage([string]$targetRoot) {
  if (-not $targetRoot) { return }
  $dest = Join-Path $targetRoot "AIAgent"
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Copy-Item (Join-Path $agentDir "*") $dest -Recurse -Force
  Copy-Item (Join-Path $agentDir "Autounattend.xml") (Join-Path $targetRoot "Autounattend.xml") -Force -ErrorAction SilentlyContinue
  Write-Host "Staged copy: $dest"
}

# Secondary drives survive clean install to C:
foreach ($letter in @("D", "E", "F", "G", "H", "W", "X")) {
  $root = "${letter}:\"
  if (Test-Path $root) { Copy-Stage $root.TrimEnd("\") }
}

# USB / live environments (Alpine, WinPE)
if (Test-Path "/AIAgent") {
  Copy-Item (Join-Path $agentDir "*") "/AIAgent" -Recurse -Force -ErrorAction SilentlyContinue
}

# Install media root (Autounattend.xml is picked up by setup)
if ($Image -like "*.iso" -and (Test-Path $Image)) {
  $mount = Mount-DiskImage -ImagePath $Image -PassThru | Get-Volume
  Start-Sleep -Seconds 2
  if ($mount.DriveLetter) {
    $isoRoot = "$($mount.DriveLetter):\"
    Copy-Stage $isoRoot.TrimEnd("\")
  }
} elseif ($Image -and (Test-Path $Image) -and -not ($Image -like "*.iso")) {
  Copy-Stage $Image.TrimEnd("\")
}

Write-Output "AIAgent autostart staged in $agentDir"
Write-Output "Autounattend: $(Join-Path $agentDir 'Autounattend.xml')"
