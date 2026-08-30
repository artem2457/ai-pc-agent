param(
  [string]$Token = $env:AGENT_TOKEN,
  [string]$Url = "http://localhost:8000",
  [string]$DeviceId = $env:COMPUTERNAME
)

if (-not $Token) {
  Write-Host "Usage: irm http://SERVER/install.ps1 | iex"
  Write-Host "Or: .\install.ps1 -Token TOKEN -Url http://SERVER"
  exit 1
}

$root = Join-Path $env:ProgramData "AIAgent"
New-Item -ItemType Directory -Force -Path $root | Out-Null
$agentSrc = Join-Path $PSScriptRoot "..\agent.py"
if (Test-Path $agentSrc) {
  Copy-Item $agentSrc (Join-Path $root "agent.py") -Force
} else {
  Invoke-WebRequest -Uri "$Url/agent.py" -OutFile (Join-Path $root "agent.py")
}

python -m pip install websockets psutil --quiet

@{ server_url = $Url; token = $Token } | ConvertTo-Json | Set-Content (Join-Path $root "config.json") -Encoding UTF8

$action = New-ScheduledTaskAction -Execute "python" -Argument "`"$(Join-Path $root 'agent.py')`" --config `"$(Join-Path $root 'config.json')`" --device-id $DeviceId"
$trigger = New-ScheduledTaskTrigger -AtStartup
try {
  Unregister-ScheduledTask -TaskName "AI-PC-Agent" -Confirm:$false -ErrorAction SilentlyContinue
} catch {}
Register-ScheduledTask -TaskName "AI-PC-Agent" -Action $action -Trigger $trigger -RunLevel Highest -Force | Out-Null
Start-Process python -ArgumentList "`"$(Join-Path $root 'agent.py')`" --config `"$(Join-Path $root 'config.json')`" --device-id $DeviceId"
Write-Host "Agent started. Device: $DeviceId"
