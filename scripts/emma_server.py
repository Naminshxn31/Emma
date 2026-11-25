"""Start the whole Emma server ON THE ROBOT (owner, 9 Oct 2026: "ทำให้ทุกอย่าง
รันบนหุ่น" -- the face must not depend on a PC and an adb tunnel).

Runs under the robot's glibc Python (scripts/robot_server.py installs it in
/data/local/emma-server/runtime), as the unprivileged uid `nobody` with the
`inet` group, from /data/local/emma-server/code. It only prepares what an
Android box lacks and then runs run_server.py unchanged, so the PC and the
robot keep one server, not two that drift:

* names: glibc has no /etc/resolv.conf here -> scripts/robot_dns.py;
* TLS roots: OpenSSL's default paths (/etc/ssl) do not exist either, and the
  Live API goes through `websockets`, which uses the default context, not
  certifi as httpx does -> SSL_CERT_FILE points at certifi's bundle;
* time: no /usr/share/zoneinfo -> the launcher exports TZ=ICT-7.

Never started by hand on the robot: `robot_server.py start` and the face
app's ServerLauncher both go through code/scripts/emma_server.sh.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]


def prepare() -> None:
    sys.path.insert(0, str(CODE / "scripts"))
    import robot_dns

    robot_dns.install()
    try:
        import certifi
    except ImportError:          # the runtime pins it; say so instead of a TLS error later
        print("emma_server: certifi missing from the runtime -- TLS to Google will fail", flush=True)
    else:
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.chdir(CODE)
    sys.path.insert(0, str(CODE))


def already_serving(host: str, port: int, timeout: float = 1.0) -> bool:
    """Something answers on the server's port: another Emma server holds it. Checked BEFORE
    this one boots (11 Nov: a second server started every ~30 s for an hour, lost the port and
    exited -- but its startup ran first: event_walk.recover_after_restart cleared a walk in
    progress and cancelled the board's action, and loading the models over and over drained the
    battery). A TCP connect: the port answers whatever its TLS."""
    import socket

    try:
        with socket.create_connection(("127.0.0.1" if host in ("", "0.0.0.0") else host, port), timeout=timeout):
            return True
    except OSError:
        return False


#: Held for the server's whole life (`hold_server_lock`): kept here so it is never closed.
_LOCK: list = []


def hold_server_lock(path: Path) -> bool:
    """One Emma server process at a time, booting or serving: an exclusive flock on `path` for this
    process's life. The port check alone only sees a server that has bound its port -- uvicorn runs
    every startup hook first (review 11 Nov), so a second one started during the first's boot ran
    its startup too. False = another server process holds it. No fcntl (not the robot): True."""
    try:
        import fcntl
    except ImportError:
        return True
    f = open(path, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    _LOCK.append(f)
    return True


if __name__ == "__main__":
    prepare()
    if not hold_server_lock(CODE / "data" / ".server.lock"):
        print("emma_server: another Emma server process is running (booting or serving) -- not starting a second",
              flush=True)
        sys.exit(0)
    from app.config import settings

    if already_serving(settings.host, settings.port):
        print("emma_server: another Emma server already answers on %s:%d -- not starting a second"
              % (settings.host, settings.port), flush=True)
        sys.exit(0)
    sys.argv = [str(CODE / "run_server.py")]
    runpy.run_path(str(CODE / "run_server.py"), run_name="__main__")
