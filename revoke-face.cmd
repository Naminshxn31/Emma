@echo off
rem Double-click to withdraw somebody's face from the greeter, or to see
rem everybody's consent status.
rem
rem Leave the name empty to list. With a name, the robot stops saying it
rem from the next camera frame; run build-face-gallery.cmd afterwards to
rem drop their vectors from the gallery as well. The camera crops in
rem data\faces\enrolled\<name>\ stay unless you answer y below.
chcp 65001 >nul
title Condo Voice - Revoke a face
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Cannot find .venv\Scripts\python.exe
  echo Run this from the condo-voice folder, with the virtualenv set up.
  echo.
  pause
  exit /b 1
)

set "NAME="
set /p NAME=Name to revoke (empty = list everybody):
if "%NAME%"=="" (
  call ".venv\Scripts\python.exe" scripts\revoke_face.py --list
  echo.
  pause
  exit /b 0
)

set "DEL="
set /p DEL=Also delete their camera shots? [y/N]:
if /i "%DEL%"=="y" (
  call ".venv\Scripts\python.exe" scripts\revoke_face.py "%NAME%" --delete-shots
) else (
  call ".venv\Scripts\python.exe" scripts\revoke_face.py "%NAME%"
)

echo.
pause
