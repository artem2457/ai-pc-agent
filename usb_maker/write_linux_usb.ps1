#requires -RunAsAdministrator
param(
  [Parameter(Mandatory = $true)][int]$DiskNumber,
  [Parameter(Mandatory = $true)][string]$Token,
  [Parameter(Mandatory = $true)][string]$ServerUrl,
  [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$AlpineVersion = "3.20.3"
$AlpineIsoUrl = "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/x86_64/alpine-extended-$AlpineVersion-x86_64.iso"
$MinSizeMB = 512

$disk = Get-Disk -Number $DiskNumber
if ($disk.BusType -ne "USB") { throw "Disk $DiskNumber is not USB." }

$sizeMB = [int][math]::Floor($disk.Size / 1MB)
if ($sizeMB -lt $MinSizeMB) {
  throw "USB is ${sizeMB} MB. Use at least ${MinSizeMB} MB (Alpine Linux is ~400 MB)."
}

function Get-FreeLetter {
  $used = @((Get-Volume | Where-Object DriveLetter).DriveLetter)
  70..90 | ForEach-Object { [char]$_ } | Where-Object { $_ -notin $used } | Select-Object -First 1
}

Write-Host "Downloading Alpine Linux $AlpineVersion (open source, no Windows license)..."
$iso = Join-Path $env:TEMP "alpine-extended-$AlpineVersion.iso"
if (-not (Test-Path $iso) -or (Get-Item $iso).Length -lt 50MB) {
  Invoke-WebRequest -Uri $AlpineIsoUrl -OutFile $iso -UseBasicParsing
}

Write-Host "Preparing USB disk $DiskNumber ..."
$letter = Get-FreeLetter
if (-not $letter) { throw "No free drive letter for USB" }

@"
select disk $DiskNumber
clean
create partition primary
format fs=fat32 quick label=AIAGENT
assign letter=$letter
active
"@ | diskpart | Out-Host

Start-Sleep -Seconds 3
$usbRoot = "${letter}:\"
if (-not (Test-Path $usbRoot)) { throw "USB volume did not appear" }

Write-Host "Copying Alpine to USB (may take a few minutes)..."
$mount = Mount-DiskImage -ImagePath $iso -PassThru
Start-Sleep -Seconds 2
$isoVol = ($mount | Get-Volume).DriveLetter
if (-not $isoVol) { throw "Could not mount Alpine ISO" }
$isoRoot = "${isoVol}:\"
& robocopy.exe $isoRoot $usbRoot /E /R:2 /W:2 /NFL /NDL /NJH /NJS /nc /ns /np | Out-Host
Dismount-DiskImage -ImagePath $iso | Out-Null

Write-Host "Installing syslinux bootloader..."
$syslinux = Join-Path $usbRoot "boot\syslinux\syslinux.exe"
if (-not (Test-Path $syslinux)) { throw "syslinux.exe not found on Alpine ISO copy" }
& $syslinux -maf -d /boot/syslinux "${letter}:"
if ($LASTEXITCODE -ne 0) { throw "syslinux install failed (code $LASTEXITCODE)" }

$agentDir = Join-Path $usbRoot "AIAgent"
New-Item -ItemType Directory -Force -Path $agentDir | Out-Null

$localAgent = $null
if ($ProjectRoot) {
  $p = Join-Path $ProjectRoot "agent\agent.py"
  if (Test-Path $p) { $localAgent = $p }
}
if ($localAgent) {
  Copy-Item $localAgent (Join-Path $agentDir "agent.py") -Force
} else {
  Write-Host "Downloading agent from server..."
  Invoke-WebRequest -Uri ($ServerUrl.TrimEnd("/") + "/agent.py") -OutFile (Join-Path $agentDir "agent.py") -UseBasicParsing
}

@{ server_url = $ServerUrl.TrimEnd("/"); token = $Token } | ConvertTo-Json |
  Set-Content (Join-Path $agentDir "config.json") -Encoding UTF8

$bootSh = $null
foreach ($base in @(
    (Join-Path $PSScriptRoot "agent-boot.sh"),
    (Join-Path (Split-Path $PSScriptRoot -Parent) "linux_usb\agent-boot.sh")
  )) {
  if (Test-Path $base) { $bootSh = $base; break }
}
if (-not $bootSh) {
  $bootSh = Join-Path $env:TEMP "agent-boot.sh"
  Invoke-WebRequest -Uri ($ServerUrl.TrimEnd("/") + "/linux_usb/agent-boot.sh") -OutFile $bootSh -UseBasicParsing
}

$localD = Join-Path $usbRoot "etc\local.d"
New-Item -ItemType Directory -Force -Path $localD | Out-Null
Copy-Item $bootSh (Join-Path $localD "aiagent.start") -Force

Write-Host "Done. Alpine Linux USB ready (~400 MB, open source)."
Write-Host "Boot empty PC from USB, connect Ethernet/Wi-Fi, device appears on the website."
