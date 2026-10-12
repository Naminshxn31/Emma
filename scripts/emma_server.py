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


if __name__ == "__main__":
    prepare()
    sys.argv = [str(CODE / "run_server.py")]
    runpy.run_path(str(CODE / "run_server.py"), run_name="__main__")
