"""
Reaching the robot over adb from the server — the three things that broke
the arm's greeting wave on 2026-09-29, all at once, in this order:

1. **The event loop could not run a subprocess at all.** uvicorn on Windows
   picks `SelectorEventLoop` whenever it runs the app in a subprocess (reload
   or workers — `uvicorn/loops/asyncio.py`), and that loop raises
   `NotImplementedError` from `asyncio.create_subprocess_exec`. Its message is
   empty, which is exactly the log line the owner's wave produced:
   "arm: could not resolve the CH340 port: " — followed by nothing. `run()`
   below uses a worker thread and plain `subprocess.run`, which works on
   every loop.
2. **The configured adb was a relative path** (`tools/android/platform-tools/
   adb.exe`, forward slashes). Windows' process creation does not find that
   the way a shell would: FileNotFoundError even with the right working
   directory. Resolved against the repo root here, as run_server.py already
   does for the certificate files.
3. **The robot was addressed by the wrong name.** `ROBOT_ARM_ADB_SERIAL` is
   the robot's hardware serial (`ro.serialno`), but adb only knows a Wi-Fi
   device by `IP:port` — and the robot's IP moves (it did that same day). So
   `adb -s <serial>` answered "device not found" while the robot sat
   connected under another name. `find()` asks every connected device for its
   serial and remembers which name answers to it.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("condo_voice.adb")

ROOT = Path(__file__).resolve().parent.parent

#: configured serial -> the adb device name that currently reaches it.
_names: dict[str, str] = {}


def executable(configured: str) -> str:
    """The adb to run: a path with a directory in it is taken from the repo
    root (as the certs are); a bare name is left for PATH to find."""
    value = (configured or "adb").strip()
    if "/" not in value and "\\" not in value:
        return value
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return str(path)


async def run(args: list[str], timeout: float) -> tuple[int, str]:
    """Run one adb command off the event loop. (returncode, combined output).

    Raises asyncio.TimeoutError on timeout, like the asyncio version did, so
    callers keep one failure shape. Bytes decoded here as UTF-8 with
    replacement: a device shell answers in whatever encoding it likes, and a
    cp874 decode error would read as "the arm is broken".
    """
    def call() -> tuple[int, str]:
        done = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout)
        return done.returncode, (done.stdout or b"").decode("utf-8", "replace").strip()

    try:
        return await asyncio.to_thread(call)
    except subprocess.TimeoutExpired:
        raise asyncio.TimeoutError(f"adb did not answer within {timeout:g}s") from None


def current(serial: str) -> str:
    """The adb name to use for this serial right now (the serial itself until
    `find` has learned better)."""
    return _names.get(serial, serial)


def looks_missing(output: str) -> bool:
    """adb's answers for a missing or stale Wi-Fi connection."""
    text = (output or "").lower()
    return ("not found" in text and "device" in text) or "device offline" in text


async def find(adb: str, serial: str, timeout: float) -> bool:
    """Learn which connected device answers to `serial`. True if found.

    Only called after adb said "not found", so the normal path costs nothing
    extra. Verify ro.serialno even for a matching mDNS service: the sales room
    has a panel and a signage box of the same model as the robot.
    """
    try:
        code, listing = await run([adb, "devices"], timeout)
    except Exception as exc:
        logger.info("adb: could not list devices: %s", exc or type(exc).__name__)
        return False
    names = []
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            names.append(parts[0])
    for name in names:
        if name == serial:
            _names[serial] = name
            return True
        try:
            code, out = await run([adb, "-s", name, "shell", "getprop ro.serialno"], timeout)
        except Exception:
            continue
        if code == 0 and out.strip() == serial:
            if _names.get(serial) != name:
                logger.info("adb: serial %s is connected as %s", serial, name)
            _names[serial] = name
            return True
    # Wi-Fi adb may have dropped its connection while Android still advertises
    # the robot. `adb devices` cannot list it in that state. Only reconnect the
    # mDNS service whose advertised serial is the configured hardware serial,
    # and still check ro.serialno after connecting before using it.
    try:
        code, services = await run([adb, "mdns", "services"], timeout)
    except Exception as exc:
        logger.info("adb: could not discover mDNS services: %s", exc or type(exc).__name__)
        code, services = 1, ""
    if code == 0:
        for line in services.splitlines():
            parts = line.split()
            if len(parts) != 3 or parts[:2] != [f"adb-{serial}", "_adb._tcp"]:
                continue
            address = parts[2]
            if not address.rsplit(":", 1)[-1].isdigit():
                continue
            try:
                connected, _ = await run([adb, "connect", address], timeout)
                if connected:
                    continue
                verified, actual = await run(
                    [adb, "-s", address, "shell", "getprop ro.serialno"], timeout)
            except Exception as exc:
                logger.info("adb: could not verify discovered robot: %s", exc or type(exc).__name__)
                continue
            if verified == 0 and actual.strip() == serial:
                logger.info("adb: serial %s reconnected as %s", serial, address)
                _names[serial] = address
                return True
    _names.pop(serial, None)
    logger.warning("adb: no connected device has serial %s (connected: %s)",
                   serial, ", ".join(names) or "none")
    return False


def reset() -> None:
    """Tests: forget learned names."""
    _names.clear()
