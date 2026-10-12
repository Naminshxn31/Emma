"""Drive another machine's room agent from this one.

This is the CALLER side. Nothing here is wired into Emma (owner: "อย่าเพิ่งเอาไป
ใส่ที่ emma นะ ทำทดสอบไปก่อน"), and it lives in scripts/ rather than app/ so that
`test_the_agent_is_not_wired_into_emma_yet` keeps meaning what it says.

It exists because the robot is not part of this link. The robot is a microphone
and a screen; the SERVER is what talks to a room's PC. So "this computer can
drive that computer" is the whole feature, and it can be proved with two
computers and no robot in the room at all.

    python scripts/room_control.py rooms
    python scripts/room_control.py health "ห้องขาย 1"
    python scripts/room_control.py do "ห้องขาย 1" list_windows
    python scripts/room_control.py do "ห้องขาย 1" open_program name=calculator

The routing table is data/rooms.json, which is gitignored: it holds one token
per machine, and a token in the history is a token that has to be changed
everywhere it was ever used.

    {
      "ห้องขาย 1": {"url": "http://192.168.1.51:8777", "token": "..."},
      "ห้องขาย 2": {"url": "http://192.168.1.52:8777", "token": "..."}
    }

The rule this file is built around, and the reason `ask_first` exists: **an
unknown room is a question, never a guess.** Picking "the only room in the
table" or "the room we talked to last" is how a command meant for room 1 lands
on a screen in room 2, in front of a different customer. There is no fallback
here on purpose.
"""

from __future__ import annotations

import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "data" / "rooms.json"
TIMEOUT = 8.0


class RoomError(Exception):
    """Something the caller has to be told, not something to work around."""


def _is_android(entry: dict) -> bool:
    """An Android panel room is driven over adb, not the HTTP room agent. It is
    marked either by `kind: "android"` or simply by having an `adb` address."""
    return entry.get("kind") == "android" or bool(entry.get("adb"))


def _input_enabled(entry: dict) -> bool:
    """Real taps/keystrokes on an Android panel are OFF unless the room's entry
    says exactly `"input": true` -- fail closed like the Windows agent's
    ROOM_AGENT_INPUT (default off). A missing flag, or a non-bool such as the
    string "false", must never hand a networked caller real input: the panel is
    reachable from a browser (the console binds localhost, but a page in that
    browser can still POST), so this flag is a real barrier, not a formality.
    A malformed value is a mistake in a hand-edited file -- say so, don't guess."""
    val = entry.get("input", False)
    if val is True:
        return True
    if val is False or val is None:
        return False
    raise RoomError('a room\'s "input" must be true or false in rooms.json '
                    f"(got {val!r})")


_android = None


def _android_mod():
    """Load the adb backend lazily (and only when an Android room is used), so
    room_control still imports on a machine with no adb / no Pillow."""
    global _android
    if _android is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "android_control.py"
        spec = importlib.util.spec_from_file_location("android_control", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _android = mod
    return _android


def rooms() -> dict[str, dict]:
    if not TABLE.is_file():
        return {}
    try:
        # utf-8-sig, because this file is edited by hand on Windows and Notepad
        # writes a BOM. Plain utf-8 chokes on it with "Expecting value: line 1
        # column 1", which reads like the JSON is wrong rather than invisible.
        # The room names are Thai, so the encoding here is not a detail: a name
        # that does not survive the round trip matches nothing, and matching
        # nothing is the good failure -- it asks. Write this file with a tool
        # that keeps UTF-8 (PowerShell's ConvertTo-Json > file does not).
        data = json.loads(TABLE.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RoomError(f"{TABLE} is not valid JSON: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RoomError(f"{TABLE} is not UTF-8: {exc}") from exc
    if not isinstance(data, dict):
        raise RoomError(f"{TABLE} must be an object of room -> {{url, token}}")
    return data


def resolve(room: str) -> dict:
    """Find one room, or say which rooms exist. Never pick one."""
    table = rooms()
    if not table:
        raise RoomError(f"no rooms configured — create {TABLE} (see this file's docstring)")
    name = (room or "").strip()
    if not name:
        raise RoomError("which room? known: " + ", ".join(sorted(table)))
    entry = table.get(name)
    if entry is None:
        # A near-match is still a different room. Offer the list; do not choose.
        raise RoomError(f"no room called {name!r}. known: " + ", ".join(sorted(table)))
    if _is_android(entry):
        if not entry.get("adb"):
            raise RoomError(f"room {name!r} (android) has no adb address in {TABLE}")
    elif not entry.get("url"):
        raise RoomError(f"room {name!r} has no url in {TABLE}")
    return entry


def _call(url: str, token: str | None, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method="POST" if body is not None else "GET")
    data = None
    if token:
        req.add_header("x-room-token", token)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:          # the agent's own refusals
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        # A room PC that is off, asleep or on another subnet lands here. It has
        # to read as "could not reach", never as a failure of the action, or the
        # answer becomes "I turned the screen off" about a machine nobody spoke
        # to. Same rule as the robot's mock-is-not-ok.
        return {"ok": False, "error": "cannot reach the room agent",
                "detail": str(getattr(exc, "reason", exc)),
                "instruction": "ติดต่อเครื่องในห้องนั้นไม่ได้ ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}
    except TimeoutError as exc:
        # A read timeout (the agent accepted the connection but a slow/hung
        # action never answered) is raised bare, NOT wrapped in URLError, so it
        # would otherwise escape as a 500. It reads as "did not respond", never
        # as the action having happened.
        return {"ok": False, "error": "the room agent did not respond in time",
                "detail": str(exc),
                "instruction": "เครื่องในห้องนั้นค้างหรือช้าเกินไป ให้บอกตรงๆ ห้ามบอกว่าทำแล้ว"}


def health(room: str) -> dict:
    entry = resolve(room)
    if _is_android(entry):
        return _android_mod().health(entry["adb"], name=room,
                                     input_enabled=_input_enabled(entry))
    return _call(entry["url"].rstrip("/") + "/health", None)


def do(room: str, action: str, args: dict[str, Any] | None = None) -> dict:
    entry = resolve(room)
    if _is_android(entry):
        return _android_mod().do(entry["adb"], action, args or {},
                                 input_enabled=_input_enabled(entry))
    return _call(entry["url"].rstrip("/") + "/do", entry.get("token"),
                 {"action": action, "args": args or {}})


def discover_android() -> list[dict]:
    """Which Android panels adb can see right now, each with the stable serial to
    paste into rooms.json. For first-time setup, so nobody hunts for a number."""
    return _android_mod().discover()


def launch_scrcpy(room: str, audio: str = "off") -> dict:
    """Open a real-time scrcpy window for an Android room. Windows rooms have no
    scrcpy equivalent (the room agent serves them), so this refuses them rather
    than guessing. `audio`: "off" (video only), "playback" (video + the panel's
    output audio), or "mic" (audio only, the room). Always live, never recorded."""
    entry = resolve(room)
    if not _is_android(entry):
        raise RoomError(f"scrcpy is for Android rooms; {room!r} is a PC room")
    return _android_mod().launch_scrcpy(entry["adb"], title=room, audio=audio)


def _parse_mac(mac: str) -> bytes:
    hexs = "".join(c for c in str(mac or "") if c not in ":-.").strip()
    if len(hexs) != 12 or any(c not in "0123456789abcdefABCDEF" for c in hexs):
        raise RoomError(f"{mac!r} is not a MAC address (expected 6 bytes like AA:BB:CC:DD:EE:FF)")
    return bytes.fromhex(hexs)


def _broadcast_targets(url: str) -> list[str]:
    """Where to send the wake packet. The subnet-directed broadcast derived from
    the PC's own IP (assume /24) is first, because it routes out the interface on
    THAT subnet -- important on this management PC, which has two (wired
    192.168.0.x and Wi-Fi). The limited broadcast is a fallback."""
    targets: list[str] = []
    try:
        host = urllib.parse.urlparse(url).hostname or ""
        octets = host.split(".")
        if len(octets) == 4 and all(o.isdigit() for o in octets):
            targets.append(".".join(octets[:3] + ["255"]))
    except Exception:
        pass
    targets.append("255.255.255.255")
    return targets


def wake(room: str) -> dict:
    """Wake a powered-off Windows room PC with a Wake-on-LAN magic packet.

    The PC is OFF, so this cannot go through the room agent (there is nothing
    running there) -- the management PC sends the packet itself. Android panels
    have no WoL, so this refuses them (a smart plug is the answer there). It is
    fire-and-forget by nature: UDP, no reply, so it reports 'signal sent', never
    'the PC is on' -- check /health after, same honesty rule as the rest."""
    entry = resolve(room)
    if _is_android(entry):
        raise RoomError(f"Wake-on-LAN is for Windows rooms; {room!r} is an Android panel "
                        "(no WoL on Android -- use a Wi-Fi smart plug)")
    mac = entry.get("mac", "")
    if not mac:
        raise RoomError(f'room {room!r} has no "mac" in {TABLE} -- add the PC\'s LAN MAC '
                        '(its /health shows it as mac_hint while it is on) to use Wake-on-LAN')
    packet = b"\xff" * 6 + _parse_mac(mac) * 16
    sent: list[str] = []
    for addr in _broadcast_targets(entry.get("url", "")):
        for port in (9, 7):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(packet, (addr, port))
                sock.close()
                sent.append(f"{addr}:{port}")
            except OSError:
                pass
    if not sent:
        return {"ok": False, "error": "could not send the wake packet",
                "instruction": "ส่งสัญญาณปลุกไม่ได้ (เครือข่าย?) ให้บอกตรงๆ ห้ามบอกว่าเปิดแล้ว"}
    return {"ok": True, "woke": mac, "sent_to": sent,
            "instruction": "ส่งสัญญาณปลุกแล้ว — เครื่องจะเปิดเองถ้าตั้ง Wake-on-LAN ไว้ (BIOS + ไดรเวอร์แลน "
                           "+ ปิด Fast Startup) รอสักครู่แล้วเช็ก health · ถ้าไม่ขึ้น = ยังไม่ได้ตั้ง WoL ที่เครื่องนั้น"}


def _main(argv: list[str]) -> int:
    # Room names are Thai; a Windows console defaults to cp1252 and would crash
    # on printing them. Speak UTF-8 to our own stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "rooms":
            table = rooms()
            if not table:
                print(f"no rooms configured — create {TABLE}")
                return 1
            for name, entry in sorted(table.items()):
                if _is_android(entry):
                    print(f"{name}\t{entry.get('adb', '(no adb)')}\tandroid")
                else:
                    print(f"{name}\t{entry.get('url', '(no url)')}\t"
                          f"token {'set' if entry.get('token') else 'MISSING'}")
            return 0
        if cmd == "discover":
            try:
                devs = discover_android()
            except Exception as exc:                    # adb missing / server hung
                print(f"could not list Android panels: {exc}")
                return 1
            if not devs:
                print("no Android panels visible (pair one via Wireless debugging first)")
                return 1
            print("Android panels adb can see -- paste a line into data/rooms.json:")
            for d in devs:
                label = d.get("name") or d["model"]
                # json.dumps so a panel's user-set device name cannot inject extra
                # JSON keys into the suggested line (it is attacker-controlled: set
                # on the panel by anyone with physical access).
                print("  " + json.dumps({label: {"kind": "android", "adb": d["serial"]}},
                                         ensure_ascii=False))
            return 0
        if cmd == "wake":
            if not rest:
                print("usage: wake <room>")
                return 2
            result = wake(rest[0])
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result.get("ok") else 1
        if cmd == "health":
            print(json.dumps(health(rest[0] if rest else ""), ensure_ascii=False, indent=1))
            return 0
        if cmd == "do":
            if len(rest) < 2:
                print("usage: do <room> <action> [key=value ...]")
                return 2
            room, action = rest[0], rest[1]
            args = dict(pair.split("=", 1) for pair in rest[2:] if "=" in pair)
            result = do(room, action, args)
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 0 if result.get("ok") else 1
        print(f"unknown command {cmd!r}. try: rooms | discover | health | do | wake")
        return 2
    except RoomError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
