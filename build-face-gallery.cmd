@echo off
rem Double-click this after enrolling people, to make the robot use them.
rem
rem Two files rather than one button, on purpose: capturing somebody's face
rem and changing what the robot believes are different decisions, and the
rem second one should be taken deliberately. Same reason `approve_narration`
rem is a separate command from writing the scripts.
chcp 65001 >nul
title Condo Voice - Build face gallery
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

call ".venv\Scripts\python.exe" scripts\build_face_gallery.py

echo.
pause
