"""Drive a room's Android panel from the operator console, over adb.

This is the Android sibling of the Windows room agent. Some sales rooms use an
Android interactive panel instead of a Windows PC; the Windows agent (Win32 /
ctypes) cannot run on it, so the panel is driven from the management PC over
**adb** instead of an HTTP agent. The panel is a company-authorized device that
a staff member enabled Wireless debugging on and paired with this PC; adb is the
gate the same way the room token is the gate for a Windows room.

It exposes the SAME capability names the Windows agent does (`screen`,
`list_windows`, `mouse_click`, `type_text`, ...), and `screen` returns the same
`{jpeg_base64, size}` shape, so `client/room-console.html` drives an Android
room with no change: the live view, the click-to-tap mapping and the buttons all
work as-is.

Two rules carried over from the rest of this project:
- **mock/unreachable is never reported as ok.** If adb cannot reach the panel,
  the result says "could not reach", never that the action happened -- the same
  reason the robot's mock results are not "ok".
- **input is its own thing.** `mouse_click`/`type_text` (real taps/keystrokes)
  are gated by the room's `input` flag, mirroring `ROOM_AGENT_INPUT`.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_ADB = ROOT / "tools" / "android" / "platform-tools" / "adb.exe"
_SCRCPY = ROOT / "tools" / "android" / "scrcpy" / "scrcpy.exe"
TIMEOUT = 12.0
MAX_TEXT = 200
# How long to watch scrcpy after launch before calling it "opened" -- a window
# that dies at once (device gone, adb clash) must not be reported as success.
_SCRCPY_SETTLE = 1.2
# scrcpy's default audio buffer is 50ms, which stutters over a jittery Wi-Fi adb
# link (the audio underruns whenever the network hiccups). A larger buffer
# absorbs the jitter -- a bit more latency, much smoother. Tunable here.
#   playback: kept moderate so audio stays roughly in sync with the video.
#   mic:      larger -- it is audio ONLY (nothing to sync to) and monitoring a
#             room does not need low latency, so it can buffer more and be
#             smoother still (the mic stutter reported 2026-09-28).
_AUDIO_BUFFER_MS = "200"
_MIC_BUFFER_MS = "400"
# No console window flashing on the operator's desktop for every adb call.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0)

# risk: read (only observes), act (changes the panel, reversible), input (real
# taps/keystrokes -- gated behind the room's `input` flag).
CATALOGUE: dict[str, dict[str, str]] = {
    "screen": {"risk": "read", "what": "ภาพหน้าจอสดของจอ Android ส่งกลับมาดูที่แผงคุม"},
    "screenshot": {"risk": "read", "what": "เก็บภาพหน้าจอไว้ในจอนั้น (/sdcard/Pictures)"},
    "list_windows": {"risk": "read", "what": "แอปที่เปิดอยู่บนจอ"},
    "monitor": {"risk": "act", "what": "ปิด/เปิดจอ (sleep/wake)"},
    "reboot": {"risk": "act", "what": "รีบูตจอ (ปิด-เปิดกลับมาเอง)"},
    "shutdown": {"risk": "act", "what": "ปิดจอสนิท (เปิดกลับจากที่นี่ไม่ได้)"},
    "lock_screen": {"risk": "act", "what": "พักหน้าจอ"},
    "media_control": {"risk": "act", "what": "ปุ่มมีเดีย เล่น/หยุด/เสียง"},
    "press_key": {"risk": "act", "what": "กดปุ่ม เช่น เลื่อนสไลด์/กลับ/โฮม"},
    "open_in_browser": {"risk": "act", "what": "เปิดหน้าเว็บบนจอ (http/https)"},
    "open_program": {"risk": "act", "what": "เปิดแอปบนจอ (ตามชื่อ package)"},
    "mouse_click": {"risk": "input", "what": "แตะจอตามพิกัด (คุมจริง)"},
    "mouse_drag": {"risk": "input", "what": "ลาก/เลื่อนบนจอ (คุมจริง)"},
    "type_text": {"risk": "input", "what": "พิมพ์ข้อความบนจอ (คุมจริง)"},
}

_KEYEVENTS = {
    "previous_slide": "KEYCODE_PAGE_UP",
    "next_slide": "KEYCODE_PAGE_DOWN",
    "back": "KEYCODE_BACK",
    "home": "KEYCODE_HOME",
    "enter": "KEYCODE_ENTER",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
}
_MEDIA = {
    "playpause": "KEYCODE_MEDIA_PLAY_PAUSE",
    "play": "KEYCODE_MEDIA_PLAY",
    "pause": "KEYCODE_MEDIA_PAUSE",
    "next": "KEYCODE_MEDIA_NEXT",
    "previous": "KEYCODE_MEDIA_PREVIOUS",
    "volumeup": "KEYCODE_VOLUME_UP",
    "volumedown": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE",
}


class AdbError(Exception):
    """adb could not reach or drive the panel -- report it, do not pretend."""


def adb_path() -> str:
    if _BUNDLED_ADB.is_file():
        return str(_BUNDLED_ADB)
    found = shutil.which("adb")
    if found:
        return found
    raise AdbError("adb not found (expected tools/android/platform-tools/adb.exe)")


def _run(serial: str, args: list[str], *, binary: bool = False,
         timeout: float = TIMEOUT) -> bytes | str:
    """Run `adb -s <serial> <args>`. Isolated so tests can stub it."""
    cmd = [adb_path(), "-s", serial, *args]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except FileNotFoundError as exc:
        raise AdbError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb timed out: {' '.join(args)}") from exc
    if out.returncode != 0:
        err = (out.stderr or b"").decode("utf-8", "replace").strip()
        raise AdbError(err or f"adb failed: {' '.join(args)}")
    return out.stdout if binary else out.stdout.decode("utf-8", "replace")


def _connect(addr: str) -> None:
    """Make sure the panel is connected. Idempotent: `adb connect` to an already
    connected device just says so. Pairing is a one-time human step on the panel
    (Wireless debugging); this only re-attaches the transport."""
    try:
        listed = _run(addr, ["get-state"], timeout=6.0).strip()
        if listed == "device":
            return
    except AdbError:
        pass
    # get-state fails when not attached; try to (re)connect via the plain adb.
    try:
        res = subprocess.run([adb_path(), "connect", addr], capture_output=True,
                             timeout=8.0, check=False,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise AdbError(f"could not reach the panel at {addr}") from exc
    text = (res.stdout or b"").decode("utf-8", "replace").lower()
    if "connected" not in text and "already" not in text:
        raise AdbError(f"could not reach the panel at {addr}: "
                       + text.strip().replace("\n", " "))


def _plain(args: list[str], *, timeout: float = TIMEOUT) -> str:
    """Run adb with no -s target (e.g. `adb devices -l`).

    A missing adb or a hung server raises AdbError like the rest of this file,
    so callers (discover, _target's reconnect nudge) turn it into "could not
    reach", never a bare 500 or a silent empty list."""
    try:
        out = subprocess.run([adb_path(), *args], capture_output=True, timeout=timeout,
                             check=False, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    except FileNotFoundError as exc:
        raise AdbError(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"adb timed out: {' '.join(args)}") from exc
    if out.returncode != 0:
        raise AdbError((out.stderr or b"").decode("utf-8", "replace").strip() or "adb failed")
    return out.stdout.decode("utf-8", "replace")


def _looks_like_addr(value: str) -> bool:
    """`192.168.1.99:5555` is a literal address; `an40010992a86711205` is a
    device serial. The mDNS name has underscores, a serial has neither."""
    return ":" in value and "_" not in value and "/" not in value


def _find_by_serial(serial: str) -> str | None:
    """Find the transport that currently embeds this hardware serial. adb's mDNS
    auto-connect re-attaches a paired panel after a reboot at a NEW port, but the
    transport name is always `adb-<serial>-...`, so the serial is the stable key."""
    try:
        out = _plain(["devices", "-l"])
    except AdbError:
        return None
    for line in out.splitlines():
        parts = line.split()
        # Match the serial EXACTLY, not as a substring: a bare serial "99" must
        # not match the transport "192.168.1.99:33099", and a rebooting panel's
        # short serial must not latch onto a different online device that merely
        # contains those characters. The mDNS transport is always adb-<serial>-…
        if len(parts) >= 2 and parts[1] == "device" and (
                parts[0] == serial or parts[0].startswith(f"adb-{serial}-")):
            return parts[0]
    return None


def _target(adb_value: str) -> str:
    """Resolve a room's `adb` value to the -s target to use right now.

    A literal `ip:port` is connected and used as-is. A device SERIAL is resolved
    to whatever transport currently carries it -- so rooms.json can hold the
    stable serial once and never need the reboot-changed port updated."""
    if not isinstance(adb_value, str):
        raise AdbError("adb address must be text")
    v = adb_value.strip()
    if not v:
        # A blank/whitespace adb value must NOT fall through to "the first device
        # adb lists" -- that is the guess this project forbids.
        raise AdbError("this room has no adb address")
    if _looks_like_addr(v):
        _connect(v)
        return v
    found = _find_by_serial(v)
    if found:
        return found
    # A freshly-rebooted panel may not be re-attached yet; nudge discovery once.
    try:
        _plain(["reconnect", "offline"], timeout=6.0)
    except AdbError:
        pass
    found = _find_by_serial(v)
    if not found:
        raise AdbError(f"panel {v!r} not found -- is it paired and on the same LAN "
                       "(Wireless debugging on)?")
    return found


# --- capabilities -----------------------------------------------------------

def _screen(addr: str) -> dict:
    """The panel's screen as a downscaled JPEG -- same shape as the Windows
    agent's `screen`, so the console shows it and maps clicks to real pixels."""
    try:
        from PIL import Image
    except Exception:
        return {"ok": False, "error": "screen needs Pillow on the console PC"}
    png = _run(addr, ["exec-out", "screencap", "-p"], binary=True, timeout=20.0)
    if not png[:8] == b"\x89PNG\r\n\x1a\n":
        return {"ok": False, "error": "the panel did not return an image"}
    image = Image.open(io.BytesIO(png))
    real = list(image.size)             # tap coordinates are in THIS space
    max_w = 1366
    if image.width > max_w:
        image = image.resize((max_w, round(image.height * max_w / image.width)))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=55)
    return {"ok": True, "jpeg_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
            "size": real}


def _screenshot(addr: str) -> dict:
    """Save a full-res grab ON the panel (mirrors the Windows `screenshot`:
    saved there, path returned, not sent -- a grab kept is a record someone
    should have chosen to keep)."""
    path = "/sdcard/Pictures/emma-panel.png"
    _run(addr, ["shell", "screencap", "-p", path])
    return {"ok": True, "saved": path,
            "instruction": "เก็บภาพไว้ในจอนั้นแล้ว (ไม่ได้ส่งภาพออกมา)"}


def _list_windows(addr: str) -> dict:
    """The foreground app and recent tasks -- the Android answer to
    'list_windows'."""
    out = _run(addr, ["shell", "dumpsys", "activity", "activities"])
    wins: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if "topResumedActivity=" in line or "ResumedActivity=" in line:
            frag = line.split("ActivityRecord{", 1)[-1]
            comp = next((p for p in frag.split() if "/" in p), frag).rstrip("}")[:80]
            if comp and comp not in wins:
                wins.append(comp)
    return {"ok": True, "windows": wins or ["(อ่านแอปที่เปิดอยู่ไม่ได้)"]}


def _keyevent(addr: str, keycode: str) -> None:
    _run(addr, ["shell", "input", "keyevent", keycode])


def _monitor(addr: str, state: str) -> dict:
    want = (state or "").strip().lower()
    if want not in {"off", "on"}:
        return {"ok": False, "error": "unknown state", "known": ["off", "on"]}
    _keyevent(addr, "KEYCODE_SLEEP" if want == "off" else "KEYCODE_WAKEUP")
    return {"ok": True, "monitor": want}


def _media(addr: str, action: str) -> dict:
    keycode = _MEDIA.get((action or "").strip().lower())
    if not keycode:
        return {"ok": False, "error": "unknown media action", "known": sorted(_MEDIA)}
    _keyevent(addr, keycode)
    return {"ok": True, "media": action}


def _press_key(addr: str, key: str) -> dict:
    name = (key or "").strip()
    keycode = _KEYEVENTS.get(name.lower())
    if keycode is None:
        # allow a raw KEYCODE_* passthrough, nothing else (no arbitrary shell)
        if name.upper().startswith("KEYCODE_") and name[8:].replace("_", "").isalnum():
            keycode = name.upper()
        else:
            return {"ok": False, "error": "unknown key", "known": sorted(_KEYEVENTS)}
    _keyevent(addr, keycode)
    return {"ok": True, "pressed": name}


def _open_in_browser(addr: str, url: str) -> dict:
    u = (url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return {"ok": False, "error": "url must be http/https"}
    if len(u) > 2000 or any(ord(c) < 32 for c in u):
        return {"ok": False, "error": "url is not valid"}
    # adb shell reassembles argv into ONE remote command string, so a bare
    # `-d http://x;reboot` would run `reboot`. Single-quote the url for the
    # panel's shell (like _type_text) so ; & | $ ` inside it stay literal while
    # legitimate query strings (?a=1&b=2) still work.
    safe = u.replace("'", "'\\''")
    out = _run(addr, ["shell", f"am start -a android.intent.action.VIEW -d '{safe}'"])
    # `am start` can exit 0 and still print "Error: Activity not started" / an
    # exception -- report that instead of a false success.
    if "Error:" in out or "Exception" in out:
        return {"ok": False, "error": "จอเปิดหน้าเว็บไม่สำเร็จ", "detail": out.strip()[:200],
                "instruction": "จอนั้นเปิดหน้าเว็บไม่สำเร็จ ให้บอกตรงๆ ห้ามบอกว่าเปิดแล้ว"}
    return {"ok": True, "opened": u}


def _open_program(addr: str, name: str) -> dict:
    """Launch an app by package name. Android has no Start-Menu list, so `name`
    is the package (e.g. com.google.android.youtube); an unknown package fails
    loudly rather than opening something else."""
    pkg = (name or "").strip()
    if not pkg or not all(c.isalnum() or c in "._" for c in pkg):
        return {"ok": False, "error": "open_program needs a package name (เช่น com.google.android.youtube)"}
    out = _run(addr, ["shell", "monkey", "-p", pkg, "-c",
                      "android.intent.category.LAUNCHER", "1"])
    if "No activities found" in out or "Error" in out:
        return {"ok": False, "error": f"no app with package {pkg}"}
    return {"ok": True, "opened": pkg}


def _tap(addr: str, x: Any, y: Any) -> dict:
    try:
        px, py = int(x), int(y)
    except (TypeError, ValueError, OverflowError):     # OverflowError: int(inf)
        return {"ok": False, "error": "mouse_click needs integer x,y"}
    _run(addr, ["shell", "input", "tap", str(px), str(py)])
    return {"ok": True, "clicked": [px, py]}


def _drag(addr: str, x1: Any, y1: Any, x2: Any, y2: Any, ms: Any) -> dict:
    """A press-move-release (adb `input swipe`): scroll, drag, flick on the
    panel. Coordinates are in the panel's real pixel space, same as a tap."""
    try:
        a, b, c, d = int(x1), int(y1), int(x2), int(y2)
    except (TypeError, ValueError, OverflowError):     # OverflowError: int(inf)
        return {"ok": False, "error": "mouse_drag needs integer coordinates"}
    try:
        dur = int(ms)
    except (TypeError, ValueError, OverflowError):
        dur = 200
    dur = max(20, min(3000, dur))
    _run(addr, ["shell", "input", "swipe", str(a), str(b), str(c), str(d), str(dur)])
    return {"ok": True, "dragged": [a, b, c, d]}


def _remote_text(text: str) -> str:
    """Build a safe `input text '...'` command for the panel's shell. Spaces
    become %s (what `input text` wants), single quotes are escaped, so arbitrary
    text is passed literally with no shell injection."""
    body = str(text or "")[:MAX_TEXT].replace(" ", "%s")
    body = body.replace("'", "'\\''")
    return f"input text '{body}'"


def _type_text(addr: str, text: str) -> dict:
    body = str(text or "")
    if not body:
        return {"ok": False, "error": "nothing to type"}
    if len(body) > MAX_TEXT:
        return {"ok": False, "error": "too long", "limit": MAX_TEXT}
    # `adb shell input text` can only inject characters the panel's keymap knows:
    # a Thai letter or emoji throws NullPointerException on the device, which
    # comes back here as a non-zero exit and would be reported as "could not
    # reach the panel" -- a lie. Say the true thing and point at scrcpy, which
    # types anything. Control characters would also break the one remote command
    # string (a NUL raises before it is even sent).
    if any(ord(c) < 32 or ord(c) > 126 for c in body):
        return {"ok": False, "error": "พิมพ์ได้เฉพาะอักษร ASCII ผ่าน adb",
                "instruction": "จอ Android รับพิมพ์ผ่าน adb ได้เฉพาะอังกฤษ/ตัวเลข/สัญลักษณ์พื้นฐาน "
                               "อักษรไทยหรืออีโมจิให้พิมพ์ผ่านหน้าต่างเรียลไทม์ (scrcpy) แทน"}
    _run(addr, ["shell", _remote_text(body)])
    return {"ok": True, "typed_len": len(body)}


def _reboot(addr: str) -> dict:
    _run(addr, ["reboot"])
    return {"ok": True, "power": "restart",
            "instruction": "สั่งรีบูตจอแล้ว จะปิด-เปิดกลับมาเอง แล้ว adb หาเจอผ่าน serial อัตโนมัติ"}


def _shutdown(addr: str) -> dict:
    _run(addr, ["reboot", "-p"])
    return {"ok": True, "power": "shutdown",
            "instruction": "สั่งปิดจอแล้ว — Android ปลุกจากดับสนิทไม่ได้ ต้องกดที่เครื่อง/ปลั๊ก Wi-Fi"}


_ACTIONS = {
    "screen": lambda a, args: _screen(a),
    "screenshot": lambda a, args: _screenshot(a),
    "list_windows": lambda a, args: _list_windows(a),
    "monitor": lambda a, args: _monitor(a, args.get("state", "")),
    "reboot": lambda a, args: _reboot(a),
    "shutdown": lambda a, args: _shutdown(a),
    "lock_screen": lambda a, args: (_keyevent(a, "KEYCODE_SLEEP"), {"ok": True})[1],
    "media_control": lambda a, args: _media(a, args.get("action", "")),
    "press_key": lambda a, args: _press_key(a, args.get("key", "")),
    "open_in_browser": lambda a, args: _open_in_browser(a, args.get("url", "")),
    "open_program": lambda a, args: _open_program(a, args.get("name", "")),
    "mouse_click": lambda a, args: _tap(a, args.get("x"), args.get("y")),
    "mouse_drag": lambda a, args: _drag(a, args.get("x1"), args.get("y1"),
                                        args.get("x2"), args.get("y2"), args.get("ms", 200)),
    "type_text": lambda a, args: _type_text(a, args.get("text", "")),
}
_INPUT_ACTIONS = {"mouse_click", "mouse_drag", "type_text"}


def health(addr: str, *, name: str = "", input_enabled: bool = True) -> dict:
    """Same shape the console reads from a Windows room's /health."""
    try:
        serial = _target(addr)
        model = _run(serial, ["shell", "getprop", "ro.product.model"], timeout=6.0).strip()
    except AdbError as exc:
        return {"ok": False, "error": "cannot reach the panel", "detail": str(exc),
                "instruction": "ต่อจอ Android ไม่ได้ (เช็ก Wireless debugging/pair) ให้บอกตรงๆ"}
    enabled = [n for n in CATALOGUE if input_enabled or CATALOGUE[n]["risk"] != "input"]
    return {
        "ok": True,
        "room": name or model or addr,
        "kind": "android",
        "armed": True,
        "enabled": sorted(enabled),
        "everything_enabled": input_enabled,
        "any_program": True,
        "catalogue": CATALOGUE,
        "programs": ["all"],
        "input_control": bool(input_enabled),
        "model": model,
    }


def do(addr: str, action: str, args: dict[str, Any] | None = None, *,
       input_enabled: bool = True) -> dict:
    """Run one action on the panel. Unreachable -> 'could not reach', never ok.
    Input actions refused unless the room enabled input."""
    name = (action or "").strip()
    handler = _ACTIONS.get(name)
    if handler is None:
        return {"ok": False, "error": "unknown action", "known": sorted(_ACTIONS)}
    if name in _INPUT_ACTIONS and not input_enabled:
        return {"ok": False, "error": "input control is off",
                "instruction": "จอนี้ปิดการแตะ/พิมพ์จากเครือข่ายไว้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}
    try:
        serial = _target(addr)
        return handler(serial, args or {})
    except AdbError as exc:
        return {"ok": False, "error": "could not reach the panel",
                "detail": str(exc),
                "instruction": "ต่อจอ Android ไม่ได้/สั่งไม่สำเร็จ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}


def discover() -> list[dict]:
    """List the Android devices adb can currently see, each with the STABLE
    serial to paste into data/rooms.json -- so setting a new panel up is 'pair
    it, run discover, copy the serial', not a manual getprop hunt. Deduped by
    serial (a panel shows up twice: as ip:port and as its mDNS name)."""
    found: dict[str, dict] = {}
    # Let an adb failure (adb missing, server hung) RAISE, so /discover shows the
    # real reason instead of an empty list that reads as "no panels, pair one".
    lines = _plain(["devices", "-l"]).splitlines()
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        transport = parts[0]
        model = next((p.split(":", 1)[1] for p in parts if p.startswith("model:")), "")
        try:
            serial = _run(transport, ["shell", "getprop", "ro.serialno"], timeout=6.0).strip()
        except AdbError:
            serial = ""
        # The user-set "Device name" (Settings > About) is how identical models
        # are told apart -- set each panel's to the room and discovery labels it.
        name = ""
        try:
            name = _run(transport, ["shell", "settings", "get", "global", "device_name"], timeout=6.0).strip()
            if name.lower() in ("null", "none", ""):
                name = ""
        except AdbError:
            pass
        if serial and serial not in found:
            found[serial] = {"serial": serial, "model": model or "Android",
                             "name": name, "transport": transport}
    return list(found.values())


def _scrcpy_args(serial: str, title: str, audio: str) -> list[str]:
    """The scrcpy command for one of three modes. LIVE only -- no `--record` in
    any of them, so nothing is ever written to disk:
      off      -- video only (the real-time control window), no sound.
      playback -- video + the panel's OUTPUT audio (what is playing on it).
      mic      -- audio ONLY, the panel's microphone (listening to the room);
                  no video and `--no-control` so it cannot touch the panel.

    The mic mode does NOTHING to suppress Android's mic-in-use indicator (the
    green dot). That is deliberate (owner, 2026-09-28: "คนที่จอเห็นได้ว่ากำลังใช้ไมค์
    ไม่ต้องเอาซ่อนไว้เลย"): the people in the room should see the mic is live. We
    only read the mic; we never hide that we are.
    """
    base = [str(_SCRCPY), "-s", serial]
    # A larger audio buffer than scrcpy's 50ms default, so the sound does not
    # stutter over the Wi-Fi adb link (seen 2026-09-28).
    if audio == "mic":
        return base + ["--no-video", "--no-control", "--audio-source=mic",
                       "--audio-buffer=" + _MIC_BUFFER_MS,
                       "--window-title", title or "Android (mic)"]
    common = ["-m", "1280", "-b", "4M", "--max-fps", "30", "--window-title", title or "Android"]
    if audio == "playback":
        return base + ["--audio-buffer=" + _AUDIO_BUFFER_MS] + common   # forwards output audio
    return base + ["--no-audio"] + common          # "off": video only


def launch_scrcpy(addr: str, *, title: str = "Android", audio: str = "off") -> dict:
    """Open a real-time scrcpy window for this panel on the operator's desktop.

    The screencap-poll live view sends a whole 4K PNG per frame -- too heavy for
    smooth playback over wi-fi adb. scrcpy instead streams H.264 (smooth, low
    latency) and carries mouse/keyboard back to the panel. It is a local GUI
    process the console merely launches, and only for a known room. scrcpy
    reuses OUR adb through the ADB env var so it does not start a second,
    version-clashing adb server. Detached so it outlives a console restart.

    `audio` picks the mode (see _scrcpy_args): "off" (video only), "playback"
    (video + the panel's output audio), or "mic" (audio only, the room). It is
    always LIVE -- never recorded."""
    if not _SCRCPY.is_file():
        return {"ok": False, "error": "scrcpy is not installed (tools/android/scrcpy)"}
    mode = (audio or "off").strip().lower()
    if mode not in {"off", "playback", "mic"}:
        return {"ok": False, "error": "unknown audio mode", "known": ["off", "playback", "mic"]}
    try:
        serial = _target(addr)
    except AdbError as exc:
        return {"ok": False, "error": "could not reach the panel", "detail": str(exc)}
    env = dict(os.environ)
    env["ADB"] = adb_path()
    try:
        proc = subprocess.Popen(
            _scrcpy_args(serial, title, mode),
            env=env, creationflags=_NO_WINDOW | _DETACHED,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return {"ok": False, "error": "could not start scrcpy", "detail": str(exc)}
    # scrcpy can die at once (device gone, adb-server clash). Watch it briefly so
    # the console does not log "real-time window opened" over a window that isn't
    # there.
    time.sleep(_SCRCPY_SETTLE)
    if proc.poll() is not None:
        return {"ok": False, "error": "scrcpy exited immediately",
                "instruction": "เปิดหน้าต่างเรียลไทม์ไม่สำเร็จ (จอหลุด/adb ชนกัน?) ให้บอกตรงๆ"}
    return {"ok": True, "launched": addr,
            "instruction": "เปิดหน้าต่างเรียลไทม์บนเครื่อง operator แล้ว"}
