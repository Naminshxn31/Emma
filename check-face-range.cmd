@echo off
rem Double-click this to measure how far the camera can recognise a face.
rem
rem A live window opens with a box around your face; the console prints one
rem line per sighting:   seen  <name>  <score>  <width>px  <verdict>
rem
rem Walk slowly from the camera toward the door and watch two numbers:
rem   width  - the robot only acts at FACE_MIN_PX (110px) or wider
rem   score  - the robot only says a name at FACE_THRESHOLD (0.45) or higher
rem The distance where either one drops below its line is the real range.
rem
rem Close the Emma window first - the camera can be held by one program.
chcp 65001 >nul
title Condo Voice - Face range check
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

echo Walk from the camera toward the door and read width + score.
echo Press q in the video window to stop.
echo.

call ".venv\Scripts\python.exe" scripts\watch_camera.py

echo.
pause
