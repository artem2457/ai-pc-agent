param(
  [string]$Token = $env:AGENT_TOKEN,
  [string]$Url = "http://localhost:8000",
  [string]$DeviceId = $env:COMPUTERNAME
)

$ErrorActionPreference = "Stop"

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = New-Object Security.Principal.WindowsPrincipal($id)
  return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-PipWorks($pyExe) {
  if (-not (Test-Path $pyExe)) { return $false }
  & $pyExe -m pip --version *>$null
  return $LASTEXITCODE -eq 0
}

function Install-Pip($pyExe) {
  $getPip = Join-Path $env:TEMP "get-pip.py"
  Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
  & $pyExe $getPip --no-warn-script-location
  if ($LASTEXITCODE -ne 0) { throw "get-pip failed for $pyExe" }
}

function Install-AgentDeps($pyExe) {
  if (-not (Test-PipWorks $pyExe)) {
    Write-Host "Installing pip..."
    Install-Pip $pyExe
  }
  & $pyExe -m pip install websockets psutil --quiet
  if ($LASTEXITCODE -ne 0) {
    & $pyExe -m pip install websockets psutil
  }
  if ($LASTEXITCODE -ne 0) { throw "Could not install websockets/psutil" }
}

function Install-PortablePython($root) {
  $pyDir = Join-Path $root "python"
  $pyExe = Join-Path $pyDir "python.exe"
  if (Test-Path $pyExe) {
    Install-AgentDeps $pyExe
    return $pyExe
  }

  Write-Host "Downloading portable Python..."
  New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
  $pyZip = Join-Path $env:TEMP "ai-pc-python-embed.zip"
  $pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
  Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing
  Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
  Get-ChildItem "$env:SystemRoot\System32\vcruntime140*.dll", "$env:SystemRoot\System32\msvcp140.dll" -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName $pyDir -Force }

  $pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
  $zipName = (Get-ChildItem $pyDir -Filter "python*.zip" | Select-Object -First 1).Name
  if ($pth -and $zipName) {
    @"
$zipName
.
import site
"@ | Set-Content $pth.FullName -Encoding ASCII
  }

  Install-AgentDeps $pyExe
  return $pyExe
}

function Ensure-Python($root) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $pyExe = $cmd.Source
    try {
      Install-AgentDeps $pyExe
      return $pyExe
    } catch {
      Write-Host "System Python is missing pip or packages: $($_.Exception.Message)"
      Write-Host "Falling back to portable Python in $root ..."
    }
  }
  return Install-PortablePython $root
}

if (-not $Token) {
  Write-Host "Download install-agent.bat from the website and run as Administrator."
  exit 1
}

if (-not (Test-Admin)) {
  $script = $MyInvocation.MyCommand.Path
  $args = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Token `"$Token`" -Url `"$Url`""
  if ($DeviceId) { $args += " -DeviceId `"$DeviceId`"" }
  Start-Process powershell.exe -Verb RunAs -ArgumentList $args
  exit
}

$root = Join-Path $env:ProgramData "AIAgent"
New-Item -ItemType Directory -Force -Path $root | Out-Null

Write-Host "Downloading agent from $Url ..."
Invoke-WebRequest -Uri "$Url/agent.py" -OutFile (Join-Path $root "agent.py") -UseBasicParsing

$python = Ensure-Python $root

@{ server_url = $Url.TrimEnd("/"); token = $Token } | ConvertTo-Json |
  Set-Content (Join-Path $root "config.json") -Encoding UTF8

$config = Join-Path $root "config.json"
$agent = Join-Path $root "agent.py"
$taskArgs = "`"$agent`" --config `"$config`" --device-id $DeviceId"

$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn
$boot = New-ScheduledTaskTrigger -AtStartup
try {
  Unregister-ScheduledTask -TaskName "AI-PC-Agent" -Confirm:$false -ErrorAction SilentlyContinue
} catch {}
Register-ScheduledTask -TaskName "AI-PC-Agent" -Action $action -Trigger @($trigger, $boot) -RunLevel Highest -Force | Out-Null
Start-Process -FilePath $python -ArgumentList $taskArgs
Write-Host "Agent installed and started. Device: $DeviceId"
Write-Host "Check the website - PC should appear online in a few seconds."
