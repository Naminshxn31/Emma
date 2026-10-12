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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Everything a room PC needs, and nothing else. Anything absent here is
#: absent on purpose; add to this list only with a reason.
FILES = ("run_room_agent.py",)
TREES = ("app",)
DATA = ("data/registry/source_registry.json",)

#: The Python that ships INSIDE the package, so the target PC never has to reach
#: the internet to get it. Bundling is the point: a sales-room PC is often on a
#: LAN with no direct route to python.org (or behind a proxy), and downloading
#: Python *there* stalls at 0 bytes -- observed on a real target 2026-09-25.
#: The management machine, which does have internet, fetches it once and carries
#: it. Keep this version in step with the one install.cmd names.
PY_VERSION = "3.12.7"
PY_INSTALLER = f"python-{PY_VERSION}-amd64.exe"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_INSTALLER}"
PY_CACHE = ROOT / ".tmp" / "python-cache" / PY_INSTALLER

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

REM Stop any agent already running first. An upgrade must REPLACE the old one:
REM otherwise the old process keeps port 8777, the new agent cannot bind and
REM exits, and the machine quietly keeps serving the old code with the old token
REM (seen on a real reinstall 2026-09-25 -- the new `screen` capability and the
REM new token never took effect). End the task, then stop only the agent's own
REM pythonw (matched by its command line, not every pythonw on the box).
schtasks /end /tn "EmmaRoomAgent" >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name='pythonw.exe'\\" | Where-Object { $_.CommandLine -like '*run_room_agent.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

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
REM Prefer the Python that shipped INSIDE this package -- no internet needed on
REM this PC. Downloading here is only the fallback, and it stalls at 0 bytes on a
REM LAN with no route to python.org (seen on a real target), so it is bounded
REM with timeouts and never the first choice.
set "PYSETUP=%SRC%python-%PYVER%-amd64.exe"
if exist "%PYSETUP%" goto run_python_setup
echo No bundled Python -- trying to download Python %PYVER% (about 25 MB)...
set "PYSETUP=%TEMP%\\python-%PYVER%-amd64.exe"
curl.exe -L --fail --connect-timeout 20 --max-time 600 -o "%PYSETUP%" "https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-amd64.exe"
if errorlevel 1 goto no_python
:run_python_setup
echo Installing Python %PYVER% into %PRIV% (quiet) ...
"%PYSETUP%" /quiet InstallAllUsers=1 PrependPath=0 Include_launcher=0 Shortcuts=0 Include_test=0 TargetDir="%PRIV%"
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

REM Let the logged-on (limited) user the task runs as write here -- its action
REM log, its screenshots and agent.log all live under this folder, and
REM ProgramData is not user-writable by default, so the headless auto-start would
REM silently fail to log or capture without this. S-1-5-32-545 = the Users group
REM (by SID, so it is right on a Thai-language Windows too).
icacls "%DEST%" /grant *S-1-5-32-545:(OI)(CI)M >nul

echo Installing Python packages...
REM Prefer wheels that shipped in the package (offline). The interpreter being
REM bundled but its packages not was the gap: pip would still reach pypi.org over
REM the same missing internet, fail, and -- unchecked -- leave a half-installed
REM agent that crashes on `import fastapi`. So: use the bundled wheelhouse if
REM present, else pip online, and FAIL LOUDLY either way.
if exist "%SRC%wheelhouse" (
  "%PY%" -m pip install --quiet --disable-pip-version-check --no-index --find-links "%SRC%wheelhouse" fastapi uvicorn python-dotenv pillow
) else (
  "%PY%" -m pip install --quiet --disable-pip-version-check fastapi uvicorn python-dotenv pillow
)
if errorlevel 1 goto pip_failed

REM Read the port from the .env that travelled with this package.
set "PORT=8777"
for /f "usebackq tokens=1,* delims==" %%A in ("%DEST%\\.env") do if /i "%%A"=="ROOM_AGENT_PORT" set "PORT=%%B"
netsh advfirewall firewall delete rule name="Emma Room Agent" >nul 2>&1
netsh advfirewall firewall add rule name="Emma Room Agent" dir=in action=allow protocol=TCP localport=%PORT% >nul
echo Opened inbound TCP port %PORT%.

REM Auto-start at logon, no window, as WHOEVER LOGS IN (the interactive user),
REM not as the account that ran this installer. With no principal the task binds
REM to the installer's identity, so over a WinRM deploy -- run as an IT admin who
REM is never interactively logged on at the room PC -- the agent would come back
REM after a reboot in a session that does not exist, and its screen/keyboard
REM capabilities need the interactive session. A Users-group principal runs it in
REM the logged-on user's own session. %PYW% was resolved above, so no PATH lookup.
powershell -NoProfile -Command "Register-ScheduledTask -TaskName 'EmmaRoomAgent' -Force -Action (New-ScheduledTaskAction -Execute '%PYW%' -Argument '\\"%DEST%\\run_room_agent.py\\"' -WorkingDirectory '%DEST%') -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Principal (New-ScheduledTaskPrincipal -GroupId 'BUILTIN\\Users' -RunLevel Limited)" >nul
echo Registered auto-start (Task Scheduler entry: EmmaRoomAgent).

REM Start it now via the TASK, not a bare `start`. Two reasons: it then runs
REM under the same Limited Users principal it will use after every reboot (a bare
REM `start` would run it with the installer's elevated token until the next
REM logon), and there is exactly ONE way the agent is ever launched -- so a
REM self-update, which restarts the agent, can never race a second copy for the
REM port. If /run is refused for any reason the AtLogOn trigger still starts it.
schtasks /run /tn "EmmaRoomAgent" >nul
echo Started in the background (via the scheduled task).

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
exit /b 0

:pip_failed
echo.
echo FAILED to install the Python packages (fastapi/uvicorn/python-dotenv).
echo The agent cannot run without them. This PC has no route to pypi.org and the
echo package shipped no offline wheels. Rebuild the package on a machine with
echo internet (it will bundle the wheels), or give this PC internet, then re-run.
echo Nothing was left auto-starting.
if not defined ROOM_AGENT_SILENT pause
exit /b 1
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


def ensure_bundled_python(out: Path) -> str | None:
    """Put the Python installer into the package, fetching it once (on the
    machine that has internet) and caching it. Returns the filename it wrote, or
    None if it could not be obtained -- in which case the package still builds and
    install.cmd falls back to downloading on the target, then to a manual message.
    A failure to bundle must never fail the build."""
    if not PY_CACHE.exists():
        try:
            PY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            print(f"fetching {PY_INSTALLER} once (cached at {PY_CACHE.relative_to(ROOT)}) ...")
            tmp = PY_CACHE.with_suffix(".part")
            urllib.request.urlretrieve(PY_URL, tmp)
            tmp.replace(PY_CACHE)
        except Exception as exc:                 # no internet here, or blocked
            print(f"  could not fetch Python to bundle ({exc}); the package will "
                  f"download it on the target instead.")
            return None
    shutil.copy(PY_CACHE, out / PY_INSTALLER)
    return PY_INSTALLER


def project_identity() -> tuple[str, str, Path]:
    registry = json.loads((ROOT / "data" / "registry" / "source_registry.json").read_text(encoding="utf-8"))
    for source in registry["sources"]:
        if source.get("id") == "project_identity":
            path = ROOT / source["location"]
            identity = json.loads(path.read_text(encoding="utf-8"))
            return identity["project_id"], identity["display_name"], path
    raise SystemExit("no project_identity in the source registry")


def _env_value(raw: str) -> str:
    """Validate a value going into the room PC's .env (unquoted KEY=value format).

    A newline would inject a whole extra line into the file; a control character,
    a leading/trailing space, or a '#' would be truncated or mis-parsed by
    python-dotenv (so ROOM_AGENT_NAME would then NOT match rooms.json, and the
    command would land nowhere). These values come from the operator building the
    package, so a bad one is a mistake worth failing the build for -- loudly,
    here -- rather than silently shipping a corrupt .env to a room PC."""
    s = str(raw or "")
    if any(ord(c) < 32 for c in s):
        raise SystemExit("a room setting has a newline/control character -- "
                         "refusing to write a corrupt .env")
    if s != s.strip() or "#" in s:
        raise SystemExit("a room setting has a leading/trailing space or '#' "
                         f"({s!r}) -- rename it so the .env is not mangled")
    return s


def build(room: str, out: Path, host: str, port: int, allow: str,
          programs: str, token: str | None, bundle_python: bool = True,
          input_enabled: bool = False, update_url: str = "") -> dict:
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
    q = _env_value
    (out / ".env").write_text(
        "# This machine's room agent. No API key belongs in this file.\n"
        f"PROJECT_ID={q(project_id)}\n"
        f"PROJECT_NAME={q(display_name)}\n"
        f"ROOM_AGENT_NAME={q(room)}\n"
        f"ROOM_AGENT_TOKEN={q(token)}\n"
        f"ROOM_AGENT_HOST={q(host)}\n"
        f"ROOM_AGENT_PORT={port}\n"
        f"ROOM_AGENT_ALLOW={q(allow)}\n"
        f"ROOM_AGENT_PROGRAMS={q(programs)}\n"
        "# Keyboard and mouse stay off unless this says true, even when ALLOW=all.\n"
        f"ROOM_AGENT_INPUT={'true' if input_enabled else 'false'}\n"
        "# One-click self-update pulls agent code from THIS fixed url only (management PC).\n"
        f"ROOM_AGENT_UPDATE_URL={q(update_url)}\n",
        encoding="utf-8")
    # .cmd files: ASCII + CRLF, and never a BOM (batch chokes on a BOM on the
    # first line). encode("ascii") makes a stray non-ASCII character fail here,
    # in the build, rather than as a mojibake line on the room PC.
    for name, body in (("start-room-agent.cmd", START_CMD),
                       ("install.cmd", INSTALL_CMD),
                       ("uninstall.cmd", UNINSTALL_CMD)):
        (out / name).write_bytes(body.replace("\n", "\r\n").encode("ascii"))

    bundled = ensure_bundled_python(out) if bundle_python else None

    assert not (out / ".env.example").exists()
    leaked = [p for p in out.rglob("*") if p.is_file() and p.name == ".env" and p.parent != out]
    assert not leaked, leaked
    return {"room": room, "token": token, "port": port, "out": str(out), "python": bundled}


def main(argv: list[str] | None = None) -> int:
    # The summary prints a Thai room name; a Windows console defaults to cp1252
    # and would crash on it AFTER the package is already written, which reads as
    # a build failure when it is not. Speak UTF-8 to our own stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
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
    parser.add_argument("--input", action="store_true",
                        help="allow keyboard and mouse control from the network "
                             "(off by default; this machine will accept typing and clicking)")
    parser.add_argument("--update-url", default="",
                        help="URL of the agent update server on the management PC "
                             "(e.g. http://192.168.0.2:8799/agent-bundle) for one-click self-update")
    parser.add_argument("--no-bundle-python", action="store_true",
                        help="do not ship Python inside the package (smaller; the target must "
                             "already have Python or be able to download it)")
    args = parser.parse_args(argv)

    built = build(args.room, args.out, args.host, args.port, args.allow, args.programs,
                  args.token, bundle_python=not args.no_bundle_python, input_enabled=args.input,
                  update_url=args.update_url)

    print(f"built {built['out']}")
    if built.get("python"):
        print(f"  Python {PY_VERSION} is bundled -- the target needs no internet for it")
    elif not args.no_bundle_python:
        print("  NOTE: could not bundle Python here (no internet?); the target will try to "
              "download it, which fails on a LAN with no route to python.org")
    print("\nON THE ROOM PC")
    print("1. copy that whole folder over (USB, or drag it in through AnyDesk/RDP)")
    print("2. run install.cmd  (one file: installs Python if missing, the packages, opens")
    print("   the port, auto-starts at every logon, and starts it now in the background)")
    print("3. note the LAN address it prints at the end")
    print("\nON THIS MACHINE, add the room to data/rooms.json:")
    print(json.dumps({built["room"]: {"url": f"http://<the room PC's IP>:{built['port']}",
                                      "token": built["token"]}}, ensure_ascii=False, indent=1))
    print(f'\nthen check it:  python scripts/room_control.py health "{built["room"]}"')
    print("\nto stop and remove it on the room PC later: uninstall.cmd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
