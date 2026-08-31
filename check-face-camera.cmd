@echo off
rem Double-click this when the camera is not behaving.
rem
rem It probes capture indices 0-7, reports which ones hand over a *moving*
rem picture, and puts one frame from each on screen with its number — a
rem virtual camera showing a still logo passes every other check there is,
rem which cost an afternoon on this machine.
chcp 65001 >nul
title Condo Voice - Which camera
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" scripts\watch_camera.py --list

echo.
echo Put the number in .env as FACE_CAMERA=... to pin it.
pause
