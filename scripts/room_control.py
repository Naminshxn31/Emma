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
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "data" / "rooms.json"
TIMEOUT = 8.0


class RoomError(Exception):
    """Something the caller has to be told, not something to work around."""


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
    if not entry.get("url"):
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


def health(room: str) -> dict:
    entry = resolve(room)
    return _call(entry["url"].rstrip("/") + "/health", None)


def do(room: str, action: str, args: dict[str, Any] | None = None) -> dict:
    entry = resolve(room)
    return _call(entry["url"].rstrip("/") + "/do", entry.get("token"),
                 {"action": action, "args": args or {}})


def _main(argv: list[str]) -> int:
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
                print(f"{name}\t{entry.get('url', '(no url)')}\t"
                      f"token {'set' if entry.get('token') else 'MISSING'}")
            return 0
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
        print(f"unknown command {cmd!r}. try: rooms | health | do")
        return 2
    except RoomError as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
