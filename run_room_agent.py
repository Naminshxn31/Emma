"""Start the room agent on THIS machine.

Separate from `run_server.py` on purpose: this is the only thing a room PC
runs. It holds no API key, no project data and no conversation — it takes a
named action from the server and performs it here.

    set ROOM_AGENT_NAME=ห้องขาย 1
    set ROOM_AGENT_TOKEN=<a long random string, different per machine>
    set ROOM_AGENT_HOST=0.0.0.0        (only when the server is another machine)
    python run_room_agent.py

Nothing is wired into Emma yet — this runs and is tested on its own first
(owner, 2026-09-24: "อย่าเพิ่งเอาไปใส่ที่ emma นะ ทำทดสอบไปก่อน").
"""

import os
import sys
from pathlib import Path

# Same lesson as run_server.py: paths in this repo are written relative to the
# repo, so a shortcut started from anywhere else must not change what they
# mean. The agent writes its log under data/room-agent/.
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def _redirect_output_when_headless() -> None:
    """Send stdout/stderr to a logfile when there is no console.

    The agent runs in the background under pythonw.exe (the `start` in
    install.cmd and the scheduled task). A pythonw process launched detached has
    NO console, so sys.stdout/sys.stderr are None, and uvicorn's own logging
    writes there -- which is why the foreground python.exe came up fine on a
    real target while the background pythonw did not (2026-09-25). Pointing both
    at a file fixes the headless start and, just as important, leaves the first
    trace this agent has ever had for diagnosing a start that fails silently.

    ProgramData is not writable by a standard user by default; install.cmd grants
    the Users group Modify on the install dir so this open() succeeds for the
    logged-on user the task runs as. TEMP is the fallback if it still cannot.
    """
    headless = (sys.stdout is None or sys.stderr is None
                or os.path.basename(sys.executable).lower().startswith("pythonw"))
    if not headless:
        return
    for target in (ROOT / "agent.log", Path(os.environ.get("TEMP", ".")) / "emma-room-agent.log"):
        try:
            stream = open(target, "a", buffering=1, encoding="utf-8", errors="replace")
        except OSError:
            continue
        sys.stdout = stream
        sys.stderr = stream
        return


if __name__ == "__main__":
    _redirect_output_when_headless()
    from app.room_agent import main

    main()
