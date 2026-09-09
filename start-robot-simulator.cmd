@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -Command "try { $simHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8010/health' -TimeoutSec 3; if ($simHealth.ok -eq $true -and $simHealth.robot_simulator -eq $true) { exit 0 }; exit 1 } catch { exit 1 }"
if not errorlevel 1 (
  echo Robot simulator is already running. Opening Emma World...
  start "" "http://127.0.0.1:8010"
  exit /b 0
)
set "SIM_PYTHON=.venv\Scripts\python.exe"
if exist ".venv-smoke\Scripts\python.exe" set "SIM_PYTHON=.venv-smoke\Scripts\python.exe"
if not exist "%SIM_PYTHON%" (
  echo Python environment missing. See docs\robot-simulator.md for setup.
  pause
  exit /b 1
)
echo Robot simulator: http://127.0.0.1:8010
echo Robot simulation only. Emma voice is optional and uses your configured API.
echo Ctrl+C to stop.
"%SIM_PYTHON%" -X utf8 -m app.robot_simulator
if errorlevel 1 pause
