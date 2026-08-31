@echo off
rem Double-click this to open the face enrolment station.
rem A browser opens at http://127.0.0.1:8770 — pick the camera, type a name,
rem follow the five prompts. Nothing reaches the robot until you also run
rem build-face-gallery.cmd afterwards; that is deliberately a second step.
rem
rem chcp 65001: the names are Thai and the station prints them. Same reason
rem condo-inventory's start-lan.cmd does it.
chcp 65001 >nul
title Condo Voice - Enrol faces
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

echo Close Elgato Camera Hub and any browser tab using the camera first —
echo the Facecam can only be held by one program at a time.
echo.

call ".venv\Scripts\python.exe" scripts\enroll_app.py

echo.
echo Station stopped.
echo When everyone is enrolled, run build-face-gallery.cmd
pause
