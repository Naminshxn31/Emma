"""Supervise the room agent on THIS machine: start it, keep it up, apply updates.

This is the only thing a room PC runs, and it is deliberately TINY and stable.
The agent itself (app/) can be replaced by a one-click self-update from the
office, but the RUNNING supervisor stays in memory until the next logon, so a
bad update to the agent is caught and rolled back HERE -- nobody has to walk to
the room PC. That is the whole reason the agent runs as a child of this loop
rather than in this process.

    set ROOM_AGENT_NAME=ห้องขาย 1
    set ROOM_AGENT_TOKEN=<a long random string, different per machine>
    set ROOM_AGENT_HOST=0.0.0.0        (only when the server is another machine)
    python run_room_agent.py

When the agent child exits:
  - exit 97  -> a self-update just staged new code; relaunch into it at once.
  - crash within seconds AFTER an update -> the new code will not even boot;
    restore app.prev and relaunch the old code (the self-heal that makes
    "update from the office, never walk to the room PC" safe).
  - any other exit -> wait a moment and relaunch; a room agent should stay up.

Nothing is wired into Emma yet (owner, 2026-09-24: "อย่าเพิ่งเอาไปใส่ที่ emma นะ
ทำทดสอบไปก่อน").
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Same lesson as run_server.py: paths in this repo are written relative to the
# repo, so a shortcut started from anywhere else must not change what they mean.
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

# Kept in step with app.room_agent.UPDATE_EXIT_CODE: the child exits with this to
# ask for an immediate relaunch into freshly-applied code.
UPDATE_EXIT_CODE = 97
_APP = ROOT / "app"
_PREV = ROOT / "app.prev"


def _log_stream():
    """One logfile for the supervisor AND the agent it runs.

    Under pythonw (the background start and the scheduled task) a detached
    process has NO console -- sys.stdout/sys.stderr are None and uvicorn's
    logging writes there, which is why a foreground python came up on a real
    target while the background pythonw did not (2026-09-25). ProgramData is not
    user-writable by default; install.cmd grants the Users group Modify so this
    open() succeeds, and TEMP is the fallback. Returns None when there is a real
    console (a developer running it in the foreground), so output stays on screen.
    """
    headless = (sys.stdout is None or sys.stderr is None
                or os.path.basename(sys.executable).lower().startswith("pythonw"))
    if not headless:
        return None
    for target in (ROOT / "agent.log", Path(os.environ.get("TEMP", ".")) / "emma-room-agent.log"):
        try:
            return open(target, "a", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _run_agent(stream) -> int:
    """Run the agent as a child process and return its exit code. Isolated so the
    supervisor loop can be unit-tested without starting anything real."""
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "app.room_agent"], cwd=str(ROOT),
        stdout=stream or None, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return proc.wait()


def _roll_back(say) -> bool:
    """Restore the previous agent code a self-update kept in app.prev, keeping the
    broken code as app.bad for diagnosis. Returns True if it rolled back."""
    if not _PREV.is_dir():
        return False
    bad = ROOT / "app.bad"
    shutil.rmtree(bad, ignore_errors=True)
    try:
        if _APP.exists():
            os.replace(_APP, bad)
        os.replace(_PREV, _APP)
    except OSError as exc:
        say("[supervisor] rollback FAILED: %s" % exc)
        return False
    say("[supervisor] rolled back to the previous agent code (app.prev)")
    return True


def main(run_agent=_run_agent) -> None:
    stream = _log_stream()

    def say(msg: str) -> None:
        (stream or sys.stderr).write(msg + "\n")

    say("[supervisor] starting; python=%s" % sys.executable)
    pending_update = False
    while True:
        started = time.monotonic()
        code = run_agent(stream)
        ran = time.monotonic() - started
        if ran > 20:
            pending_update = False                    # the running code proved itself
        if code == UPDATE_EXIT_CODE:
            say("[supervisor] agent asked to restart for a self-update (up %.0fs)" % ran)
            pending_update = True
            continue
        if pending_update and code != 0 and ran < 15:
            # The just-applied update will not even boot. Restore app.prev and
            # relaunch the old code -- the self-heal that keeps a bad update from
            # stranding the agent until someone logs on at the room PC.
            say("[supervisor] agent crashed %.1fs after an update (exit %s)" % (ran, code))
            pending_update = False
            if _roll_back(say):
                continue
        say("[supervisor] agent exited (%s); restarting in 3s" % code)
        time.sleep(3)


if __name__ == "__main__":
    main()
