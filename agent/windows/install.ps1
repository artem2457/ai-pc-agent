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

function Invoke-Py([string]$PyExe, [string[]]$PyArgs) {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "SilentlyContinue"
  try {
    $output = & $PyExe @PyArgs 2>&1
    return @{ ExitCode = $LASTEXITCODE; Output = $output }
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Test-PipWorks([string]$PyExe) {
  if (-not (Test-Path $PyExe)) { return $false }
  return (Invoke-Py $PyExe @("-m", "pip", "--version")).ExitCode -eq 0
}

function Install-Pip([string]$PyExe) {
  $getPip = Join-Path $env:TEMP "get-pip.py"
  if (-not (Test-Path $getPip)) {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip -UseBasicParsing
  }
  $r = Invoke-Py $PyExe @($getPip, "--no-warn-script-location")
  if ($r.ExitCode -ne 0) {
    throw "get-pip failed: $($r.Output | Out-String)"
  }
}

function Enable-EmbedSitePackages([string]$PyDir) {
  $pth = Get-ChildItem $PyDir -Filter "python*._pth" -ErrorAction SilentlyContinue | Select-Object -First 1
  $zipName = (Get-ChildItem $PyDir -Filter "python*.zip" -ErrorAction SilentlyContinue | Select-Object -First 1).Name
  if ($pth -and $zipName) {
    @"
$zipName
.
import site
"@ | Set-Content $pth.FullName -Encoding ASCII
  }
}

function Install-AgentDeps([string]$PyExe) {
  if (-not (Test-PipWorks $PyExe)) {
    Write-Host "Installing pip..."
    Install-Pip $PyExe
  }
  if (-not (Test-PipWorks $PyExe)) {
    throw "pip is still unavailable after get-pip"
  }

  Write-Host "Installing agent packages..."
  $r = Invoke-Py $PyExe @("-m", "pip", "install", "websockets", "psutil")
  if ($r.ExitCode -ne 0) {
    throw "pip install failed: $($r.Output | Out-String)"
  }

  $check = Invoke-Py $PyExe @("-c", "import websockets, psutil")
  if ($check.ExitCode -ne 0) {
    throw "package import failed: $($check.Output | Out-String)"
  }
}

function Install-PortablePython([string]$Root) {
  $pyDir = Join-Path $Root "python"
  $pyExe = Join-Path $pyDir "python.exe"

  if (-not (Test-Path $pyExe)) {
    Write-Host "Downloading portable Python..."
    if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
    $pyZip = Join-Path $env:TEMP "ai-pc-python-embed.zip"
    $pyUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
    Invoke-WebRequest -Uri $pyUrl -OutFile $pyZip -UseBasicParsing
    Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
    Get-ChildItem "$env:SystemRoot\System32\vcruntime140*.dll", "$env:SystemRoot\System32\msvcp140.dll" -ErrorAction SilentlyContinue |
      ForEach-Object { Copy-Item $_.FullName $pyDir -Force }
  } else {
    Write-Host "Using portable Python in $pyDir ..."
  }

  Enable-EmbedSitePackages $pyDir
  Install-AgentDeps $pyExe
  return $pyExe
}

function Ensure-Python([string]$Root) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $pyExe = $cmd.Source
    try {
      Install-AgentDeps $pyExe
      return $pyExe
    } catch {
      Write-Host "System Python failed: $($_.Exception.Message)"
      Write-Host "Falling back to portable Python in $Root ..."
    }
  }
  return Install-PortablePython $Root
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

$configPath = Join-Path $root "config.json"
$configJson = (@{ server_url = $Url.TrimEnd("/"); token = $Token } | ConvertTo-Json -Compress)
[System.IO.File]::WriteAllText($configPath, $configJson, (New-Object System.Text.UTF8Encoding $false))

$config = $configPath
$agent = Join-Path $root "agent.py"
$taskArgs = "`"$agent`" --config `"$config`" --device-id $DeviceId"

$action = New-ScheduledTaskAction -Execute $python -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn
$boot = New-ScheduledTaskTrigger -AtStartup
try {
  Unregister-ScheduledTask -TaskName "AI-PC-Agent" -Confirm:$false -ErrorAction SilentlyContinue
} catch {}
Register-ScheduledTask -TaskName "AI-PC-Agent" -Action $action -Trigger @($trigger, $boot) -RunLevel Highest -Force | Out-Null
Start-Process -FilePath $python -ArgumentList $agent, "--config", $config, "--device-id", $DeviceId
Write-Host "Agent installed and started. Device: $DeviceId"
Write-Host "Check the website - PC should appear online in a few seconds."
Write-Host "Press any key to close..."
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
