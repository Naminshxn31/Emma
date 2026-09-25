"""Build the folder to copy onto one sales-room PC.

    python scripts/make_room_package.py "ห้องขาย 1" --out D:\\room1

Why this is a script and not a paragraph in a document: the room PC needs a
precise and non-obvious subset of this repo, and the two ways of getting it
wrong both look fine at the time.

**Too little.** `app/tools/__init__.py` imports `app.config`, which resolves
the slide directory through `data/registry/source_registry.json` at IMPORT
time, and that reaches `data/projects/<id>/project.json`. The room agent
imports `app.tools` LATE, so a machine missing those two files starts
perfectly, answers /health, runs list_windows and screenshot, and fails the
first time somebody says "open the browser" — in front of a customer.
Measured on a bare copy, 2026-09-25; the agent now says so at boot too.

**Too much.** The obvious way to get a working folder is to copy the repo, and
the repo has `.env` in it: the Gemini key and the Supabase service-role key,
on a machine in a room the public walks through. This script copies a fixed
list and writes a fresh `.env` containing only the room's own settings, so the
real one cannot arrive by accident.

`PROJECT_NAME` is in that written `.env` because `Settings.__post_init__`
refuses to load when it does not match `PROJECT_ID` — it is the project's
display name, not a secret.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Everything a room PC needs, and nothing else. Anything absent here is
#: absent on purpose; add to this list only with a reason.
FILES = ("run_room_agent.py",)
TREES = ("app",)
DATA = ("data/registry/source_registry.json",)

START_CMD = """@echo off
REM Start Emma's room agent on this PC IN THE FOREGROUND. Leave this window open.
REM This is the visible way, for a first test. install.cmd is the real setup:
REM it runs the agent in the background and brings it back at every logon.
cd /d "%~dp0"
python run_room_agent.py
pause
"""

# The one-file setup. Owner asked for "a single file, everything runs in the
# background" -- so this installs to a normal program folder, starts the agent
# with no console window, and brings it back at every logon.
#
# "Background" here means "no window, runs as a service", NOT "concealed". It is
# deliberately the OPPOSITE of hidden: a fixed, standard location
# (C:\ProgramData\EmmaRoomAgent), a named Task Scheduler entry (EmmaRoomAgent),
# an ordinary pythonw.exe process in Task Manager, and an uninstall.cmd beside
# it. A service that can drive a PC has to be findable and stoppable by whoever
# owns that PC -- that is the line between an IT agent and a backdoor, and it is
# the same reason the token fails closed and the screenshots never leave the box.
#
# Console messages are ASCII on purpose: a UTF-8 BOM breaks batch, and the cmd
# console renders Thai poorly, so an installer that talked Thai would show boxes
# and read as broken. The chat stays Thai; this technical console does not.
#
# Runs as the LOGGED-IN user at limited privilege, not as admin or SYSTEM: the
# GUI capabilities (screenshot, focus a window, media keys) only work inside the
# interactive desktop session, which SYSTEM/session-0 does not have. The
# installer needs admin (firewall + ProgramData + task); the agent itself does
# not, and should not have it.
INSTALL_CMD = """@echo off
setlocal enableextensions
title Emma Room Agent installer

REM Needs admin: a firewall rule, C:\\ProgramData, and a scheduled task.
net session >nul 2>&1
if errorlevel 1 (
  echo Approve the prompt to run this as administrator...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "SRC=%~dp0"
set "DEST=C:\\ProgramData\\EmmaRoomAgent"
echo Installing Emma Room Agent to %DEST%

REM --- Python: use the system copy if present, else install a PRIVATE one into
REM the agent's own folder and use it by full path. A private copy means the
REM agent never fights a system Python's version and needs nothing on PATH; any
REM user can run it because it is executed by its full path. Falls back to a
REM manual message only if the download itself is blocked.
set "PRIV=C:\\ProgramData\\EmmaRoomAgent\\python"
set "PYW="
set "PY="
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
for /f "delims=" %%P in ('where python 2^>nul')  do if not defined PY  set "PY=%%P"
if defined PYW goto have_python
if exist "%PRIV%\\pythonw.exe" ( set "PYW=%PRIV%\\pythonw.exe" & set "PY=%PRIV%\\python.exe" & goto have_python )

set "PYVER=3.12.7"
set "PYEXE=%TEMP%\\python-%PYVER%-amd64.exe"
echo Python not found -- downloading Python %PYVER% (about 25 MB)...
curl.exe -L -o "%PYEXE%" "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
if errorlevel 1 goto no_python
echo Installing Python quietly into %PRIV% ...
"%PYEXE%" /quiet InstallAllUsers=1 PrependPath=0 Include_launcher=0 Shortcuts=0 Include_test=0 TargetDir="%PRIV%"
del "%PYEXE%" >nul 2>&1
if not exist "%PRIV%\\pythonw.exe" goto no_python
set "PYW=%PRIV%\\pythonw.exe"
set "PY=%PRIV%\\python.exe"
goto have_python

:no_python
echo.
echo Could not get Python automatically (no internet, or the download was blocked).
echo Install it by hand, then run this file again:
echo   https://www.python.org/downloads/   (tick "Add python.exe to PATH")
echo.
if not defined ROOM_AGENT_SILENT pause
exit /b 1

:have_python
if not defined PY set "PY=%PYW:pythonw.exe=python.exe%"
echo Using Python at %PYW%

echo Copying files...
xcopy "%SRC%app" "%DEST%\\app" /e /i /y /q >nul
xcopy "%SRC%data" "%DEST%\\data" /e /i /y /q >nul
copy /y "%SRC%run_room_agent.py" "%DEST%\\" >nul
copy /y "%SRC%.env" "%DEST%\\" >nul
copy /y "%SRC%uninstall.cmd" "%DEST%\\" >nul

echo Installing Python packages...
"%PY%" -m pip install --quiet --disable-pip-version-check fastapi uvicorn python-dotenv

REM Read the port from the .env that travelled with this package.
set "PORT=8777"
for /f "usebackq tokens=1,* delims==" %%A in ("%DEST%\\.env") do if /i "%%A"=="ROOM_AGENT_PORT" set "PORT=%%B"
netsh advfirewall firewall delete rule name="Emma Room Agent" >nul 2>&1
netsh advfirewall firewall add rule name="Emma Room Agent" dir=in action=allow protocol=TCP localport=%PORT% >nul
echo Opened inbound TCP port %PORT%.

REM Auto-start at logon, no window, as the ordinary (non-admin) user. %PYW% was
REM resolved above (system or the private copy), so no PATH lookup is needed.
powershell -NoProfile -Command "Register-ScheduledTask -TaskName 'EmmaRoomAgent' -Force -RunLevel Limited -Action (New-ScheduledTaskAction -Execute '%PYW%' -Argument '\\"%DEST%\\run_room_agent.py\\"' -WorkingDirectory '%DEST%') -Trigger (New-ScheduledTaskTrigger -AtLogOn)" >nul
echo Registered auto-start (Task Scheduler entry: EmmaRoomAgent).

REM Start it now, in the background, no window.
start "" /d "%DEST%" "%PYW%" run_room_agent.py
echo Started in the background.

echo.
echo Done. This PC's address(es) on the LAN:
powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -like '192.168.*'}).IPAddress"
echo.
echo It now starts at every logon with no window.
echo Installed at %DEST% -- shown in Task Scheduler as EmmaRoomAgent.
echo To stop and remove it: run uninstall.cmd
echo.
REM ROOM_AGENT_SILENT is set by the remote deploy (deploy_room_agents.ps1): a
REM `pause` under WinRM has no console to read a key and would hang the deploy.
if not defined ROOM_AGENT_SILENT pause
"""

UNINSTALL_CMD = """@echo off
setlocal
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
set "DEST=C:\\ProgramData\\EmmaRoomAgent"
echo Stopping Emma Room Agent...
schtasks /end /tn "EmmaRoomAgent" >nul 2>&1
schtasks /delete /f /tn "EmmaRoomAgent" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name='pythonw.exe'\\" | Where-Object { $_.CommandLine -like '*run_room_agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
netsh advfirewall firewall delete rule name="Emma Room Agent" >nul 2>&1
echo Removed the scheduled task and the firewall rule.
echo Files remain at %DEST% -- delete that folder to remove them completely.
if not defined ROOM_AGENT_SILENT pause
"""


def project_identity() -> tuple[str, str, Path]:
    registry = json.loads((ROOT / "data" / "registry" / "source_registry.json").read_text(encoding="utf-8"))
    for source in registry["sources"]:
        if source.get("id") == "project_identity":
            path = ROOT / source["location"]
            identity = json.loads(path.read_text(encoding="utf-8"))
            return identity["project_id"], identity["display_name"], path
    raise SystemExit("no project_identity in the source registry")


def build(room: str, out: Path, host: str, port: int, allow: str,
          programs: str, token: str | None) -> dict:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty — pick an empty folder or delete it first")
    out.mkdir(parents=True, exist_ok=True)

    for tree in TREES:
        shutil.copytree(ROOT / tree, out / tree,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in FILES:
        shutil.copy(ROOT / name, out / name)
    for rel in DATA:
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, out / rel)

    project_id, display_name, identity_path = project_identity()
    rel_identity = identity_path.relative_to(ROOT)
    (out / rel_identity).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(identity_path, out / rel_identity)

    token = token or secrets.token_urlsafe(24)
    (out / ".env").write_text(
        "# This machine's room agent. No API key belongs in this file.\n"
        f"PROJECT_ID={project_id}\n"
        f"PROJECT_NAME={display_name}\n"
        f"ROOM_AGENT_NAME={room}\n"
        f"ROOM_AGENT_TOKEN={token}\n"
        f"ROOM_AGENT_HOST={host}\n"
        f"ROOM_AGENT_PORT={port}\n"
        f"ROOM_AGENT_ALLOW={allow}\n"
        f"ROOM_AGENT_PROGRAMS={programs}\n"
        "# Keyboard and mouse stay off unless this says true, even when ALLOW=all.\n"
        "ROOM_AGENT_INPUT=false\n",
        encoding="utf-8")
    # .cmd files: ASCII + CRLF, and never a BOM (batch chokes on a BOM on the
    # first line). encode("ascii") makes a stray non-ASCII character fail here,
    # in the build, rather than as a mojibake line on the room PC.
    for name, body in (("start-room-agent.cmd", START_CMD),
                       ("install.cmd", INSTALL_CMD),
                       ("uninstall.cmd", UNINSTALL_CMD)):
        (out / name).write_bytes(body.replace("\n", "\r\n").encode("ascii"))

    assert not (out / ".env.example").exists()
    leaked = [p for p in out.rglob("*") if p.is_file() and p.name == ".env" and p.parent != out]
    assert not leaked, leaked
    return {"room": room, "token": token, "port": port, "out": str(out)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("room", help='the room name, exactly as the server will say it')
    parser.add_argument("--out", required=True, type=Path, help="an empty folder to build into")
    parser.add_argument("--host", default="0.0.0.0",
                        help="0.0.0.0 so the server can reach it (default); 127.0.0.1 to test alone")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--allow", default="all",
                        help='capabilities this room turns on; "all" is the whole catalogue')
    parser.add_argument("--programs", default="",
                        help='programs this room may open; "all" lets the Start Menu decide')
    parser.add_argument("--token", default=None, help="reuse an existing token instead of a new one")
    args = parser.parse_args(argv)

    built = build(args.room, args.out, args.host, args.port, args.allow, args.programs, args.token)

    print(f"built {built['out']}")
    print("\nON THE ROOM PC")
    print("1. copy that whole folder over (USB, or drag it in through AnyDesk/RDP)")
    print("2. run install.cmd  (one file: installs Python packages, opens the port,")
    print("   auto-starts at every logon, and starts it now in the background)")
    print("   -- Python must already be installed; install.cmd says so if it is not")
    print("3. note the LAN address it prints at the end")
    print("\nON THIS MACHINE, add the room to data/rooms.json:")
    print(json.dumps({built["room"]: {"url": f"http://<the room PC's IP>:{built['port']}",
                                      "token": built["token"]}}, ensure_ascii=False, indent=1))
    print(f'\nthen check it:  python scripts/room_control.py health "{built["room"]}"')
    print("\nto stop and remove it on the room PC later: uninstall.cmd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
