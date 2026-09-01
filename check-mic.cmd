@echo off
rem Double-click this when Emma "cannot hear" - it asks the microphone
rem itself, below the browser, whether any sound is coming out of it.
rem
rem One line per input device:
rem   alive      - room noise reaches the OS; the fault (if any) is above:
rem                which mic the browser picked, the page, the VAD
rem   NO FRAMES  - the device sends nothing to any program: a mute button
rem                on the mic, a USB device in a bad state (replug), or
rem                Windows audio needing a reboot. No setting fixes this.
rem   SILENT     - frames arrive but every sample is exactly zero: same
rem                causes, one layer up
rem
rem Nobody needs to talk. Emma can stay running; the mic is shared.
chcp 65001 >nul
title Condo Voice - Microphone check
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" scripts\check_mic.py

echo.
pause
