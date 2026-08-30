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

function Get-FreeLetter {
  $used = @((Get-Volume | Where-Object DriveLetter).DriveLetter)
  70..90 | ForEach-Object { [char]$_ } | Where-Object { $_ -notin $used } | Select-Object -First 2
}

function Find-WinREWim {
  $local = Join-Path $env:SystemRoot "System32\Recovery\Winre.wim"
  if (Test-Path $local) { return $local }
  try { & reagentc.exe /enable | Out-Null } catch {}
  if (Test-Path $local) { return $local }
  foreach ($n in 67..90) {
    $p = "{0}:\Recovery\WindowsRE\Winre.wim" -f [char]$n
    if (Test-Path $p) { return $p }
  }
  throw "WinRE не найден. На этом ПК выполни в админ-консоли: reagentc /enable"
}

$letters = @(Get-FreeLetter)
if ($letters.Count -lt 2) { throw "Нет свободных букв дисков" }
$efi = $letters[0]
$win = $letters[1]

Write-Host "Ищу WinRE на этом компьютере (ISO не нужен)..."
$wim = Find-WinREWim
Write-Host "WinRE: $wim"

Write-Host "Стираю USB диск $DiskNumber ..."
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
if (-not (Test-Path $root)) { throw "NTFS-раздел не появился" }

Write-Host "Распаковываю среду загрузки на флешку..."
New-Item -ItemType Directory -Force -Path $root | Out-Null
& dism.exe /Apply-Image /ImageFile:$wim /Index:1 /ApplyDir:$root
if ($LASTEXITCODE -ne 0) { throw "DISM Apply-Image не удался (код $LASTEXITCODE)" }

Write-Host "Делаю EFI-загрузку..."
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
  Write-Host "Скачиваю агент с сервера..."
  Invoke-WebRequest -Uri ($ServerUrl.TrimEnd("/") + "/agent.py") -OutFile (Join-Path $agentDir "agent.py") -UseBasicParsing
}

Write-Host "Скачиваю портативный Python (на ПК Python не нужен)..."
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

Write-Host "Готово. ISO не копировали — Windows скачает Grok после появления ПК на сайте."
Write-Host "Вставь флешку в пустой ПК → Boot from USB → интернет."
