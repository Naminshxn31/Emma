"""The robot puts its own face on a room's big screen (owner 28 Oct 2026).

"จอห้องนั้นต้องเปิดเว็บทิ้งไว้หรอ ไม่เอาๆ เอาแบบหุ่นไปอยู่ใกล้จอห้องนั้น หรือสั่งให้เปิดจอ
ก็ให้หุ่นเชื่อมจอเลย" -- the room screen keeps nothing open. Two ways the face
goes up there, both from the robot's side:

* asked: the `room_screen` tool (app/tools/screens.py) -- "เปิดจอ" / "ปิดจอ";
* near: the chassis pose (`robot_chassis.STATE["pose"]`, polled every
  ROBOT_CHASSIS_POLL_S) inside a screen's `near.radius` for READINGS polls
  running opens it; past radius + LEAVE_MARGIN_M for READINGS polls closes it.

Putting it up = asking that PC's own agent (the small HTTP service each room
PC runs, POST /do with its key) for `emma_screen_open` with this robot's
mirror URL (app/mirror_server.py) and a one-time ticket in place of the
MIRROR_TOKEN; taking it down = `emma_screen_close`. Those two are the only
actions this server ever asks of a room PC (a test holds it): the general
room control stays off Emma, as the owner said of it on 24 Sep.

Rules that are each a way this goes wrong:

* **Manual wins over the pose.** "ปิดจอ" next to the screen must not be
  undone by the next poll: a screen closed by hand is not reopened until the
  robot has left its area. A screen opened by hand is not swapped for a
  nearer one; it closes on leaving only once the pose had been inside it
  (asked from across the floor, with no map, it stays until "ปิดจอ").
* **Auto-open only when nothing is up**, and a failed open waits
  RETRY_AFTER_S -- a PC that is off must not be asked every two seconds.
* **Boot closes every screen** (best effort): a robot server that restarted
  has forgotten what it opened, and a face left up in a room the robot has
  left is the one state nothing else would ever fix.
* The pose is the board's. On a floor with no map it is odometry, so "near"
  is only as right as the map (docs: floor 1 needs its map first); the log
  line of every auto open says the distance it was decided on.

Config, ROOM_SCREENS_FILE (gitignored -- it holds each PC's key; the robot's
copy is `room-screens.json` beside its .env, pushed by `robot_server.py
secrets`):

    {"screens": [{"name": "ห้องขาย 2", "agent": "http://192.168.1.52:8777",
                  "token": "...", "near": {"x": 1.2, "y": -3.4, "radius": 2.0}}]}

`near` is optional (no `near` = only on request). `python
scripts/room_screens.py here "ห้องขาย 2"` writes it from where the robot
stands. The file is re-read when it changes, no restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.room_screens")

ROOT = Path(__file__).resolve().parent.parent

#: The only two things this server may ask of a room PC.
OPEN, CLOSE = "emma_screen_open", "emma_screen_close"
#: Polls in a row inside (or beyond the margin) before anything changes: one
#: odd pose reading must not flash a screen on and off.
READINGS = 2
#: Leaving is radius + this: standing on the edge must not toggle the screen.
LEAVE_MARGIN_M = 1.0
#: A screen whose PC did not answer is not asked again sooner than this.
RETRY_AFTER_S = 60.0
#: The agent takes a few seconds (it closes an old window politely first).
REQUEST_TIMEOUT_S = 10.0
BOOT_TIMEOUT_S = 4.0

STATE: dict[str, Any] = {"open": None, "by": None, "seen_inside": False}
#: name -> [polls inside in a row, polls beyond the margin in a row]
_runs: dict[str, list[int]] = {}
#: Closed by hand while inside: no auto-open until the robot has left.
_suppressed: set[str] = set()
_failed_at: dict[str, float] = {}
_cache: dict[str, Any] = {"key": None, "screens": []}
#: The screen whose area the robot is in now (`at_screen`), and when a pose
#: last said so. Owner 29 Oct, after the media-room test (the camera saw a
#: usable face in 1 of 297 listening decisions while people watched the TV):
#: "ผ่อน 'ต้องเห็นหน้า' ตอนหุ่นอยู่หน้าจอใหญ่ ... ฟังจากเสียงที่ได้ยินชัดเอา".
_present: dict[str, Any] = {"name": None, "at": None}
#: A presence no pose has confirmed for this long is over: the chassis link
#: down is not "still at the screen" (a robot pushed away keeps no pose).
PRESENCE_STALE_S = 15.0
_lock: dict[str, Any] = {"loop": None, "lock": None}
_task: dict[str, Any] = {"task": None}


def reset() -> None:
    """Tests: one definition of 'nothing is up'."""
    STATE.update({"open": None, "by": None, "seen_inside": False})
    _runs.clear()
    _suppressed.clear()
    _failed_at.clear()
    _cache.update({"key": None, "screens": []})
    _lock.update({"loop": None, "lock": None})
    _present.update({"name": None, "at": None})


def state() -> dict:
    """Whether a screen is up now, and which."""
    return {"open": STATE["open"] is not None, "screen": STATE["open"]}


def at_screen(now: float | None = None) -> str | None:
    """The room screen the robot stands at, or None.

    At = the pose (polled by the watcher, `decide`) inside that screen's
    `near` area: in after READINGS polls within the radius -- the rule that
    opens the screen by itself -- or while its face is up on that screen
    (opened by staff or asked) and the pose is within radius +
    LEAVE_MARGIN_M; out after READINGS polls beyond radius + LEAVE_MARGIN_M,
    the rule that closes it. A presence no pose has confirmed for
    PRESENCE_STALE_S is over. Enter/leave are turn-log rows (`at_screen`).
    Robot log 28 Oct: the media room's screen opened by itself at 0.47-2.22 m
    and by staff at 3.88-4.13 m (radius 3.0)."""
    clock = time.monotonic() if now is None else now
    name, at = _present["name"], _present["at"]
    if name is None or at is None or clock - at > PRESENCE_STALE_S:
        return None
    return name


def _presence(dists: dict[str, tuple[float, float]], clock: float) -> None:
    """One pose poll's say on `at_screen` (called by `decide`)."""
    present = _present["name"]
    if present is not None and clock - (_present["at"] or clock) > PRESENCE_STALE_S:
        present = None                     # no pose for a while: unknown is not "at"
    if present is not None:
        run = _runs.get(present)
        if present not in dists or (run is not None and run[1] >= READINGS):
            present = None
    if present is None:
        candidates = [(d, name) for name, (d, r) in dists.items()
                      if _runs.get(name, [0, 0])[0] >= READINGS
                      or (STATE["open"] == name and d <= r + LEAVE_MARGIN_M)]
        if candidates:
            present = min(candidates)[1]
    before = _present["name"] if _present["at"] is not None and clock - _present["at"] <= PRESENCE_STALE_S else None
    if present != before:
        if before is not None:
            logger.info("at the big screen: left '%s'", before)
            turnlog.record("at_screen", screen=before, state="leave")
        if present is not None:
            logger.info("at the big screen: '%s' (%.2f m)", present, dists[present][0])
            turnlog.record("at_screen", screen=present, state="enter",
                           distance_m=round(dists[present][0], 2))
    _present.update({"name": present, "at": clock if present is not None else None})


# --------------------------------------------------------------------------
# the table


def _path() -> Path | None:
    raw = (settings.room_screens_file or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _clean(entry: Any) -> dict | None:
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    agent = str(entry.get("agent") or "").strip().rstrip("/")
    key = str(entry.get("token") or "").strip()
    parsed = urlparse(agent)
    if not name or not key or parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    out = {"name": name, "agent": agent, "token": key}
    # Which of that PC's screens is the big one ("DISPLAY2", "secondary"; owner
    # 28 Oct: the multimedia room's PC drives two). Blank = its primary.
    display = str(entry.get("display") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9]{1,16}", display):
        out["display"] = display
    near = entry.get("near")
    if isinstance(near, dict):
        try:
            x, y, r = float(near["x"]), float(near["y"]), float(near.get("radius", 2.0))
        except (KeyError, TypeError, ValueError):
            x = y = r = math.nan
        if all(math.isfinite(v) for v in (x, y, r)) and r > 0:
            out["near"] = {"x": x, "y": y, "radius": r}
    return out


def screens() -> list[dict]:
    """The configured screens, re-read when the file changes."""
    path = _path()
    try:
        st = path.stat() if path else None
    except OSError:
        st = None
    key = None if st is None else (str(path), st.st_mtime_ns, st.st_size)
    if key == _cache["key"]:
        return _cache["screens"]
    table: list[dict] = []
    if key is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            raw = data.get("screens") if isinstance(data, dict) else None
            for entry in raw if isinstance(raw, list) else []:
                clean = _clean(entry)
                if clean is None:
                    logger.warning("room screens: an entry in %s is missing name/agent/token -- skipped",
                                   path.name)
                elif any(s["name"] == clean["name"] for s in table):
                    logger.warning("room screens: %r is listed twice -- the second is skipped",
                                   clean["name"])
                else:
                    table.append(clean)
        except (OSError, ValueError) as exc:
            logger.error("room screens: %s could not be read (%s)", path, exc)
    _cache.update({"key": key, "screens": table})
    return table


def offered() -> bool:
    """The tool is shown and the watcher acts only with all of it in place."""
    return bool(settings.room_screens_enabled and settings.mirror_port
                and settings.mirror_token and screens())


def names() -> list[str]:
    return [s["name"] for s in screens()]


# --------------------------------------------------------------------------
# where the robot is


def _xy(pose: Any) -> tuple[float, float] | None:
    if not isinstance(pose, dict):
        return None
    try:
        x, y = float(pose["x"]), float(pose["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def _distance(screen: dict, xy: tuple[float, float]) -> float:
    near = screen["near"]
    return math.hypot(xy[0] - near["x"], xy[1] - near["y"])


def current_pose() -> dict | None:
    from app import robot_chassis

    return robot_chassis.STATE.get("pose") if robot_chassis.connected() else None


def pick(name: str | None, pose: Any = None) -> dict | None:
    """Which screen. Named = that one or none. Unnamed = the one whose area
    the robot is in, else the only one there is, else None (ask -- a guess
    puts the face in front of the wrong room)."""
    table = screens()
    wanted = (name or "").strip().casefold()
    if wanted:
        return next((s for s in table if s["name"].casefold() == wanted), None)
    xy = _xy(pose)
    if xy is not None:
        inside = [(_distance(s, xy), s) for s in table
                  if "near" in s and _distance(s, xy) <= s["near"]["radius"] + LEAVE_MARGIN_M]
        if inside:
            return min(inside, key=lambda pair: pair[0])[1]
    return table[0] if len(table) == 1 else None


def decide(pose: Any, now: float | None = None) -> tuple[str, str] | None:
    """One poll of the pose -> ("open"|"close", name) or None. Pure apart from
    the run counters; the watcher does the asking."""
    clock = time.monotonic() if now is None else now
    xy = _xy(pose)
    if xy is None:
        _runs.clear()          # no pose = no runs; nothing is decided blind
        return None
    best = None
    dists: dict[str, tuple[float, float]] = {}
    for screen in screens():
        if "near" not in screen:
            continue
        name, d, r = screen["name"], _distance(screen, xy), screen["near"]["radius"]
        dists[name] = (d, r)
        run = _runs.setdefault(name, [0, 0])
        if d <= r:
            run[0], run[1] = run[0] + 1, 0
        elif d > r + LEAVE_MARGIN_M:
            run[0], run[1] = 0, run[1] + 1
            if run[1] >= READINGS:
                _suppressed.discard(name)        # it left: the hand-close is spent
        if run[0] >= READINGS and (best is None or d < best[1]):
            best = (name, d)
        if name == STATE["open"] and run[0] >= 1:
            STATE["seen_inside"] = True
    _presence(dists, clock)
    current = STATE["open"]
    if current is not None:
        run = _runs.get(current)
        if (run is not None and run[1] >= READINGS
                and (STATE["by"] == "near" or STATE["seen_inside"])):
            return ("close", current)
        return None
    if best is not None and best[0] not in _suppressed:
        failed = _failed_at.get(best[0])
        if failed is None or clock - failed >= RETRY_AFTER_S:
            return ("open", best[0])
    return None


# --------------------------------------------------------------------------
# asking the room PC


#: Where a probe goes when the room PC's own address says nothing about the
#: LAN (below). Nothing is sent: a UDP connect only asks for a route.
_ROUTE_PROBE = "1.1.1.1"


def _robot_ip_toward(host: str) -> str:
    """This robot's address on the network the room PC is on: the address the
    kernel would send from (a UDP connect sends nothing).

    A room PC reached on loopback is a tunnel (adb reverse), and loopback is
    no address for its browser to fetch the page from: through `adb forward`
    the room screen ran up to 2.6 s behind the robot's voice where the LAN
    copy kept time (measured on the robot 28 Oct). So the page goes over the
    LAN even when the command came through a tunnel."""
    bound = (settings.mirror_host or "").strip()
    if bound and bound not in ("0.0.0.0", "::"):
        return bound
    loopback = host in ("localhost", "::1") or host.startswith("127.")
    for target in ((_ROUTE_PROBE, host) if loopback else (host,)):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((target, 9))
            return probe.getsockname()[0]
        except OSError:
            continue                      # no route out: the tunnel's own end
        finally:
            probe.close()
    raise OSError("no route toward %s" % host)


def mirror_url(screen: dict) -> str:
    """The face page for that PC, with a one-time ticket (never the token)."""
    from app import mirror_server

    host = urlparse(screen["agent"]).hostname or ""
    query = {"ticket": mirror_server.issue_ticket()}
    if settings.room_screen_sound:
        query["sound"] = "1"
    return "http://%s:%d/mirror?%s" % (_robot_ip_toward(host), settings.mirror_port, urlencode(query))


async def _ask(screen: dict, action: str, args: dict, timeout: float = REQUEST_TIMEOUT_S) -> dict:
    if action not in (OPEN, CLOSE):          # the whole surface, said twice
        raise ValueError(action)
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(screen["agent"] + "/do", json={"action": action, "args": args},
                                         headers={"x-room-token": screen["token"]})
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if response.status_code != 200:
            return {"ok": False, "error": body.get("error") or "http %d" % response.status_code}
        return body
    except Exception as exc:                 # off, asleep, other subnet, firewall
        return {"ok": False, "error": "unreachable", "detail": type(exc).__name__}


def _get_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _lock["loop"] is not loop:
        _lock.update({"loop": loop, "lock": asyncio.Lock()})
    return _lock["lock"]


async def _close_one(screen: dict, by: str, timeout: float = REQUEST_TIMEOUT_S) -> dict:
    result = await _ask(screen, CLOSE, {}, timeout)
    ok = bool(result.get("ok"))
    if STATE["open"] == screen["name"] and (ok or result.get("error") == "unreachable"):
        # An unreachable PC is treated as down: the robot will not reach it to
        # undo anything later either, and a STATE stuck on it blocks every
        # other screen from opening by itself.
        STATE.update({"open": None, "by": None, "seen_inside": False})
    turnlog.record("room_screen", action="close", screen=screen["name"], by=by, ok=ok,
                   error=None if ok else result.get("error"))
    return result


async def open_screen(name: str | None = None, *, by: str = "asked") -> dict:
    """Put the face up on one screen. {"ok", "screen"} or {"ok": False, "error", ...}."""
    pose = current_pose()
    screen = pick(name, pose)
    if screen is None:
        return {"ok": False, "error": "which_screen" if not name else "unknown_screen", "screens": names()}
    async with _get_lock():
        if STATE["open"] and STATE["open"] != screen["name"]:
            other = next((s for s in screens() if s["name"] == STATE["open"]), None)
            if other is not None:
                await _close_one(other, by)
        try:
            url = mirror_url(screen)
        except OSError as exc:
            result = {"ok": False, "error": "no_route", "detail": type(exc).__name__}
        else:
            args = {"url": url}
            if screen.get("display"):
                args["display"] = screen["display"]
            result = await _ask(screen, OPEN, args)
        ok = bool(result.get("ok"))
        if ok:
            xy = _xy(pose)
            inside = (xy is not None and "near" in screen
                      and _distance(screen, xy) <= screen["near"]["radius"])
            STATE.update({"open": screen["name"], "by": by, "seen_inside": inside})
            _failed_at.pop(screen["name"], None)
            _suppressed.discard(screen["name"])
        else:
            _failed_at[screen["name"]] = time.monotonic()
        xy = _xy(pose)
        distance = (round(_distance(screen, xy), 2) if xy is not None and "near" in screen else None)
        turnlog.record("room_screen", action="open", screen=screen["name"], by=by, ok=ok,
                       distance_m=distance, error=None if ok else result.get("error"))
        if by == "near":
            logger.info("room screen %r: %s at %s m from it (%s)", screen["name"],
                        "opened" if ok else "could not open", distance, result.get("error") or "ok")
    return {"ok": ok, "screen": screen["name"], "error": None if ok else result.get("error")}


async def close_screen(name: str | None = None, *, by: str = "asked") -> dict:
    """Take the face down: the named screen, else the one up, else every
    screen (the robot may have restarted since it opened one)."""
    table = screens()
    if name:
        targets = [s for s in table if s["name"].casefold() == name.strip().casefold()]
        if not targets:
            return {"ok": False, "error": "unknown_screen", "screens": names()}
    elif STATE["open"]:
        targets = [s for s in table if s["name"] == STATE["open"]]
    else:
        targets = list(table)
    async with _get_lock():
        results = await asyncio.gather(*(_close_one(s, by) for s in targets))
    if by in ("asked", "staff", "tap"):
        # Closed by hand while standing there: the pose must not reopen it.
        # Only a screen with a position: one closed before it had any (28 Oct
        # on the robot -- "ปิดจอ" in a voice test, then `here` while standing
        # at it) stayed shut until the robot walked away and back, which reads
        # as "the position did not take".
        xy = _xy(current_pose())
        for screen in targets:
            if "near" in screen and (xy is None or _distance(screen, xy) <= (
                    screen["near"]["radius"] + LEAVE_MARGIN_M)):
                _suppressed.add(screen["name"])
    ok = all(r.get("ok") for r in results) if results else True
    return {"ok": ok, "screens": [s["name"] for s in targets],
            "error": None if ok else next((r.get("error") for r in results if not r.get("ok")), None)}


# --------------------------------------------------------------------------
# the watcher


async def _watch() -> None:
    if offered():
        await asyncio.gather(*(_close_one(s, "boot", BOOT_TIMEOUT_S) for s in screens()))
    while True:
        await asyncio.sleep(max(0.5, settings.robot_chassis_poll_s))
        try:
            if not offered():
                continue
            step = decide(current_pose())
            if step is None:
                continue
            action, name = step
            if action == "open":
                await open_screen(name, by="near")
            else:
                await close_screen(name, by="near")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("room screens: the watcher hit an error (it keeps going)")


def start() -> None:
    """From main's startup hook. Off unless ROOM_SCREENS_ENABLED."""
    if not settings.room_screens_enabled:
        return
    task = _task["task"]
    if task is not None and not task.done():
        return
    if not offered():
        logger.warning("ROOM_SCREENS_ENABLED is on but no screen can be used -- needs MIRROR_PORT, "
                       "MIRROR_TOKEN and at least one screen in %s", settings.room_screens_file)
    logger.info("room screens: %s (%d with a position)", ", ".join(names()) or "none",
                sum(1 for s in screens() if "near" in s))
    _task["task"] = asyncio.create_task(_watch())


async def stop() -> None:
    task, _task["task"] = _task["task"], None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def toggle(*, by: str = "tap") -> dict:
    """The robot face's hold-menu slot: down if one is up, else up (the
    server's state decides, not the page's -- the pose or a voice may have
    changed it since the menu was drawn)."""
    if STATE["open"]:
        result = await close_screen(by=by)
    else:
        result = await open_screen(by=by)
    return dict(result, **state())
