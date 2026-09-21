"""Small, allow-listed Android controls for the robot test console.

This module intentionally is not an ADB shell exposed over HTTP.  The robot is
reachable over ADB, but a browser only gets the few controls whose effects we
have identified: the two display backlights. Rotation remains readable state;
the real panel ignored every attempted Android rotation command.
Every path and every accepted value is chosen here on the server.
"""
from __future__ import annotations

import asyncio
import re

from app.config import settings


BACKLIGHTS = {
    "a": "/sys/class/backlight/backlight",
    "b": "/sys/class/backlight/backlight1",
}
BRIGHTNESS_PRESETS = frozenset({0, 25, 50, 80, 100})


async def _run(args: list[str], timeout: float | None = None) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or settings.robot_arm_timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace").strip()


def _adb(*args: str) -> list[str]:
    command = [settings.robot_arm_adb]
    if settings.robot_arm_adb_serial:
        command += ["-s", settings.robot_arm_adb_serial]
    return command + list(args)


async def _shell(command: str) -> tuple[int, str]:
    return await _run(_adb("shell", command))


async def state() -> dict:
    """Read the Android controls and device inventory visible right now."""
    try:
        code, connection = await _run(_adb("get-state"))
    except Exception as exc:
        return {"connected": False, "error": f"{type(exc).__name__}: {exc}"}
    if code or connection.strip() != "device":
        return {"connected": False, "error": connection or "ADB ไม่ตอบ"}

    backlight_command = "; ".join(
        f"c=$(cat {path}/brightness 2>/dev/null); "
        f"m=$(cat {path}/max_brightness 2>/dev/null); echo BL:{key}:$c:$m"
        for key, path in BACKLIGHTS.items()
    )
    results = await asyncio.gather(
        _shell(backlight_command),
        _shell("echo ROT:$(settings get system user_rotation):$(settings get system accelerometer_rotation)"),
        _shell("dumpsys media.camera"),
        _shell("cat /proc/asound/cards"),
        return_exceptions=True,
    )

    backlights: dict[str, dict] = {}
    if not isinstance(results[0], Exception):
        _code, output = results[0]
        for key, current, maximum in re.findall(r"BL:([ab]):(\d*):(\d*)", output):
            if current and maximum and int(maximum) > 0:
                backlights[key] = {
                    "current": int(current),
                    "maximum": int(maximum),
                    "percent": round(int(current) * 100 / int(maximum)),
                }

    rotation = None
    auto_rotate = None
    if not isinstance(results[1], Exception):
        _code, output = results[1]
        match = re.search(r"ROT:([0-3]):([01])", output)
        if match:
            rotation = int(match.group(1))
            auto_rotate = match.group(2) == "1"

    camera_count = None
    if not isinstance(results[2], Exception):
        _code, output = results[2]
        match = re.search(r"Number of camera devices:\s*(\d+)", output)
        if match:
            camera_count = int(match.group(1))

    audio_cards = []
    if not isinstance(results[3], Exception):
        _code, output = results[3]
        audio_cards = [line.strip() for line in output.splitlines()
                       if re.match(r"^\s*\d+\s+\[", line)]

    return {
        "connected": True,
        "backlights": backlights,
        "rotation": rotation,
        "auto_rotate": auto_rotate,
        "camera_count": camera_count,
        "audio_cards": audio_cards,
        "bothlent_present": any("bothlent" in line.lower() for line in audio_cards),
    }


async def set_brightness(display: str, percent: int) -> dict:
    """Set one known backlight to one of five deliberate presets."""
    if display not in BACKLIGHTS:
        raise ValueError("จอไม่อยู่ในรายการที่อนุญาต")
    if percent not in BRIGHTNESS_PRESETS:
        raise ValueError("ความสว่างต้องเป็น 0, 25, 50, 80 หรือ 100%")
    path = BACKLIGHTS[display]
    code, raw_max = await _shell(f"cat {path}/max_brightness")
    try:
        maximum = int(raw_max.strip())
    except (TypeError, ValueError):
        raise RuntimeError(raw_max or "อ่านค่าความสว่างสูงสุดไม่ได้")
    if code or maximum <= 0:
        raise RuntimeError(raw_max or "ไม่พบช่องไฟหน้าจอ")
    target = round(maximum * percent / 100)
    code, output = await _shell(
        f"su 0 sh -c 'echo {target} > {path}/brightness' && cat {path}/brightness")
    if code:
        raise RuntimeError(output or "เขียนค่าความสว่างไม่สำเร็จ")
    try:
        actual = int(output.splitlines()[-1].strip())
    except (IndexError, ValueError):
        raise RuntimeError("เขียนแล้วแต่อ่านค่ากลับไม่ได้")
    return {"display": display, "percent": round(actual * 100 / maximum),
            "current": actual, "maximum": maximum}
