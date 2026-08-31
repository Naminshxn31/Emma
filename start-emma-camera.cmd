@echo off
rem Double-click this to run Emma with the entrance camera watching.
rem
rem It is the ordinary server (run_server.py, the same HOST/PORT/WS_TOKEN from
rem .env) with one thing turned on: FACE_ENABLED. That switch is off in .env
rem on purpose - the showroom machine must behave identically after a git pull,
rem and every face the camera accepts opens a Gemini session. Double-clicking
rem this file is the deliberate act that turns it on, for this run only.
rem Nothing in .env is edited.
chcp 65001 >nul
title Condo Voice - Emma with camera
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

rem Set before python starts: load_dotenv() does not override what is already
rem in the environment, so this wins over .env without touching the file.
set FACE_ENABLED=true

echo Emma is starting with the camera on.
echo.
echo   - close the enrolment station first; the camera can only be held by
echo     one program at a time
echo   - the browser page opens by itself and must stay open: the camera
echo     rings that page, and with no page there is nothing to ring
echo   - the same person is greeted once per FACE_COOLDOWN_S (10 minutes by
echo     default) - put FACE_COOLDOWN_S=30 in .env while testing
echo   - anybody enrolled since the last build-face-gallery.cmd is not in
echo     the gallery yet; the startup lines below say who
echo.

call ".venv\Scripts\python.exe" run_server.py --open

echo.
echo Emma stopped.
pause
