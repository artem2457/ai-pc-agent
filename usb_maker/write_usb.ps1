#requires -RunAsAdministrator
param(
  [Parameter(Mandatory = $true)][int]$DiskNumber,
  [Parameter(Mandatory = $true)][string]$Token,
  [Parameter(Mandatory = $true)][string]$ServerUrl,
  [string]$ProjectRoot = ""
)

$ErrorActionPreference = "Stop"
$disk = Get-Disk -Number $DiskNumber
if ($disk.BusType -ne "USB") { throw "Disk $DiskNumber is not USB." }

$sizeGB = [int][math]::Floor($disk.Size / 1GB)
if ($sizeGB -lt 8) {
  throw "USB is ${sizeGB} GB. Use an 8 GB or larger flash drive."
}

function Get-FreeLetter {
  $used = @((Get-Volume | Where-Object DriveLetter).DriveLetter)
  70..90 | ForEach-Object { [char]$_ } | Where-Object { $_ -notin $used }
}

function Test-Wim($path) {
  if (-not $path) { return $false }
  $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
  return [bool]($item -and $item.Length -gt 1MB)
}

function Copy-WimToTemp($src) {
  $dest = Join-Path $env:TEMP "ai-winre.wim"
  Write-Host "Copying WinRE to $dest"
  Copy-Item -LiteralPath $src -Destination $dest -Force
  return $dest
}

function Get-TempDriveLetter {
  $letter = @(Get-FreeLetter) | Select-Object -First 1
  if (-not $letter) { throw "No free drive letter to mount Recovery partition" }
  return $letter
}

function Mount-PartitionTemp($diskN, $partN) {
  $letter = Get-TempDriveLetter
  $p = Get-Partition -DiskNumber $diskN -PartitionNumber $partN -ErrorAction Stop
  if ($p.DriveLetter) { return @{ Letter = $p.DriveLetter; Mounted = $false; Disk = $diskN; Part = $partN } }
  Set-Partition -DiskNumber $diskN -PartitionNumber $partN -NewDriveLetter $letter
  return @{ Letter = $letter; Mounted = $true; Disk = $diskN; Part = $partN }
}

function Dismount-PartitionTemp($info) {
  if ($info -and $info.Mounted) {
    try {
      Remove-PartitionAccessPath -DiskNumber $info.Disk -PartitionNumber $info.Part -AccessPath ("{0}:" -f $info.Letter)
    } catch {}
  }
}

function Find-WimOnLetter($letter) {
  foreach ($rel in @("Recovery\WindowsRE\Winre.wim", "WindowsRE\Winre.wim", "Winre.wim")) {
    $wim = "{0}:\{1}" -f $letter, $rel
    if (Test-Wim $wim) { return $wim }
  }
  return $null
}

function Find-WinREWim {
  $candidates = @(
    (Join-Path $env:SystemRoot "System32\Recovery\Winre.wim"),
    (Join-Path $env:SystemRoot "Recovery\WindowsRE\Winre.wim")
  )
  foreach ($c in $candidates) {
    if (Test-Wim $c) { return (Copy-WimToTemp $c) }
  }

  Write-Host "Enabling Windows Recovery on this PC..."
  try { & reagentc.exe /enable | Out-Host } catch { Write-Host $_ }

  foreach ($c in $candidates) {
    if (Test-Wim $c) { return (Copy-WimToTemp $c) }
  }

  $info = & reagentc.exe /info 2>&1 | Out-String
  Write-Host $info

  $loc = $null
  foreach ($line in ($info -split "`r?`n")) {
    if ($line -match '(?i)(location|Speicherort|emplacement|расположен)\s*:\s*(.+)\s*$') {
      $loc = $Matches[2].Trim()
    }
  }
  if ($loc) {
    $wim = $loc.TrimEnd("\") + "\Winre.wim"
    if (Test-Wim $wim) { return (Copy-WimToTemp $wim) }
  }

  if ($info -match '(?i)harddisk(\d+)\\partition(\d+)') {
    $diskN = [int]$Matches[1]
    $partN = [int]$Matches[2]
    $mounted = $null
    try {
      $mounted = Mount-PartitionTemp $diskN $partN
      $wim = Find-WimOnLetter $mounted.Letter
      if ($wim) { return (Copy-WimToTemp $wim) }
    } catch {
      Write-Host $_
    } finally {
      Dismount-PartitionTemp $mounted
    }
  }

  $gptRecovery = "{de94bba4-06d1-4d40-a16a-bfd50179d6ac}"
  foreach ($p in Get-Partition -ErrorAction SilentlyContinue) {
    $isRecovery = ($p.GptType -eq $gptRecovery) -or ("$($p.Type)" -match "Recovery")
    if (-not $isRecovery) { continue }
    $mounted = $null
    try {
      $mounted = Mount-PartitionTemp $p.DiskNumber $p.PartitionNumber
      $wim = Find-WimOnLetter $mounted.Letter
      if ($wim) { return (Copy-WimToTemp $wim) }
    } catch {
      Write-Host $_
    } finally {
      Dismount-PartitionTemp $mounted
    }
  }

  throw @"
WinRE not found on THIS PC (the computer running usb-maker).
The empty target PC is not used yet.

On THIS PC, open Command Prompt as Administrator and run:
  reagentc /enable
  reagentc /info

Then run usb-maker again. Use an 8 GB or larger USB drive.
"@
}

Write-Host "Looking for WinRE on this PC (boot files for the USB)..."
$wim = Find-WinREWim
Write-Host "WinRE: $wim"

$letters = @(Get-FreeLetter | Select-Object -First 2)
if ($letters.Count -lt 2) { throw "Not enough free drive letters" }
$efi = $letters[0]
$win = $letters[1]

Write-Host "Erasing USB disk $DiskNumber ..."
@"
select disk $DiskNumber
clean
convert gpt
create partition efi size=300
format fs=fat32 quick label=BOOT
assign letter=$efi
create partition primary
format fs=ntfs quick label=AIAGENT
assign letter=$win
"@ | diskpart | Out-Host

Start-Sleep -Seconds 2
$root = "${win}:\"
if (-not (Test-Path $root)) { throw "NTFS partition did not appear" }

Write-Host "Applying boot environment to USB..."
New-Item -ItemType Directory -Force -Path $root | Out-Null
& dism.exe /Apply-Image /ImageFile:$wim /Index:1 /ApplyDir:$root
if ($LASTEXITCODE -ne 0) { throw "DISM Apply-Image failed (code $LASTEXITCODE)" }

Write-Host "Creating EFI boot entry..."
bcdboot.exe "${win}:\Windows" /s "${efi}:" /f UEFI | Out-Host

$agentDir = Join-Path $root "AIAgent"
New-Item -ItemType Directory -Force -Path $agentDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $agentDir "python") | Out-Null

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

Write-Host "Downloading portable Python (no install on target PC)..."
$pyZip = Join-Path $env:TEMP "ai-pc-python-embed.zip"
$pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing
Expand-Archive -Path $pyZip -DestinationPath (Join-Path $agentDir "python") -Force
Get-ChildItem "$env:SystemRoot\System32\vcruntime140*.dll","$env:SystemRoot\System32\msvcp140.dll" -ErrorAction SilentlyContinue |
  ForEach-Object { Copy-Item $_.FullName (Join-Path $agentDir "python") -Force }

$pth = Get-ChildItem (Join-Path $agentDir "python") -Filter "python*._pth" | Select-Object -First 1
$zipName = (Get-ChildItem (Join-Path $agentDir "python") -Filter "python*.zip" | Select-Object -First 1).Name
if ($pth -and $zipName) {
  @"
$zipName
.
import site
"@ | Set-Content $pth.FullName -Encoding ASCII
}

@{ server_url = $ServerUrl.TrimEnd("/"); token = $Token } | ConvertTo-Json |
  Set-Content (Join-Path $agentDir "config.json") -Encoding UTF8

$runCmd = @"
@echo off
wpeinit
wpeutil WaitForNetwork
wpeutil DisableFirewall
setlocal EnableDelayedExpansion
set AGENT=
for %%D in (C D E F G H W X) do (
  if exist %%D:\AIAgent\python\python.exe set AGENT=%%D:\AIAgent
)
if not defined AGENT (
  echo AIAgent not found
  pause
  exit /b 1
)
cd /d !AGENT!
:retry
"!AGENT!\python\python.exe" agent.py --config "!AGENT!\config.json"
ping -n 6 127.0.0.1 >nul
goto retry
"@
Set-Content -Path (Join-Path $agentDir "run.cmd") -Value $runCmd -Encoding ASCII
Copy-Item (Join-Path $agentDir "run.cmd") "${win}:\Windows\System32\startnet.cmd" -Force

Write-Host "Done. No ISO copied - Grok will download Windows after the PC appears on the site."
Write-Host "Boot empty PC from USB and connect to the internet."
