@echo off
rem Double-click to check the scan booth machine and its link to the robot,
rem without opening the camera (docs\emma-scan-booth-rehearsal.md step 4).
rem Every line [ ok ] = ready to rehearse.
chcp 65001 >nul
title Emma - Check scan booth
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" scripts\booth_app.py --check
echo.
echo ------------------------------------------------------------------
echo  This only CHECKS. The booth itself is not running yet.
echo  To use it: double-click start-scan-booth.cmd and leave that window open.
echo  (owner's first try, 23 Nov: ran this one, and the check-in page showed no button)
echo ------------------------------------------------------------------
pause
