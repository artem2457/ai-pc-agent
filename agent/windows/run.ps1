param(
  [string]$Config = "$env:ProgramData\AIAgent\config.json"
)
python "$PSScriptRoot\..\agent.py" --config $Config
