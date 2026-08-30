wpeinit
wpeutil WaitForNetwork
wpeutil DisableFirewall
setlocal EnableDelayedExpansion
set AGENT=
for %%D in (C D E F G H W X) do (
  if exist %%D:\AIAgent\python\python.exe set AGENT=%%D:\AIAgent
)
if not defined AGENT (
  if exist X:\AIAgent\agent.py (
    python X:\AIAgent\agent.py --config X:\AIAgent\config.json
    goto :eof
  )
  echo AIAgent not found
  pause
  exit /b 1
)
cd /d !AGENT!
:retry
"!AGENT!\python\python.exe" agent.py --config "!AGENT!\config.json"
ping -n 6 127.0.0.1 >nul
goto retry
