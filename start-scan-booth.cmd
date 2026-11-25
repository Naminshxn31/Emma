@echo off
rem Double-click to open the guests' face-scan booth (docs\emma-scan-booth-rehearsal.md).
rem A browser opens at http://127.0.0.1:8771 -- press F11 for full screen.
rem With BOOTH_RETURN_URL set (check-in and booth on one PC) no tab opens: the
rem check-in page opens the booth when a guest presses "ให้ Emma จำหน้า". Leave this window open.
rem It checks this machine and the robot first; a line marked [ !! ] is what
rem to fix before guests scan (the scan still opens, saves wait for the robot).
rem
rem chcp 65001: the notice and the names are Thai.
chcp 65001 >nul
title Emma - Scan booth
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

echo Close Camera Hub and any browser tab using the camera first --
echo a camera can only be held by one program at a time.
echo.
call ".venv\Scripts\python.exe" scripts\booth_app.py --check
echo.

call ".venv\Scripts\python.exe" scripts\booth_app.py

echo.
echo Booth stopped.
pause
