"""Drive the robot's chassis directly, over SLAMTEC's own RESTful API.

The vendor AAR was never the only way in, and on 2026-09-10 the chassis said
so itself. `GET /api/core/system/v1/robot/info` answers **Slamware SDP** by
Slamtec, firmware `5.1.1-deb-for-aobo-hermes`. The `quickConnect()` in
`aoborobotsdk-v2.0.aar` that `robot_link` was written to wait for is a wrapper
around this service, and SLAMTEC publishes the protocol.

So movement no longer needs the Android app at all: the server talks to the
chassis, and the app on the chest screen goes back to being what it already
is — a voice client. `robot_link.send()` still decides *what*; this module is
one more way of doing it, chosen ahead of the WebSocket when it is configured.

**What this costs.** Going around the vendor app means going around whatever
the vendor app was doing on top of navigation. The chassis avoids obstacles by
itself (that is what a SLAM chassis is), but *avoiding* is not *stopping*, and
the safety rules in `docs/ต่อกับหุ่นยนต์ Astronaut.md` do not disappear when
the layer that was supposed to hold them does — they move here:

* a walk nobody is watching must end by itself (`ROBOT_ARRIVAL_TIMEOUT_S`,
  already armed by `robot.go_to_place`),
* losing the link mid-walk must not read as "still on its way",
* and the physical stop button is still the only stop that cannot fail.

**Arrival is polled, not reported.** REST has no callback. `_watch()` holds
the only clock: it asks the chassis what it is doing, and when the walk ends
it calls `robot_link.arrived()` — the same road the Android app's
`robot_arrived` message would have taken, so the model hears about it as its
own turn instead of as a return value nobody was waiting for.

**Nothing here decides that a walk succeeded from an HTTP 200.** Sending is
not arriving; that is the `hardware: "mock"` lesson from `smarthome.py` with
a heavier object attached. Success is a terminal action status, or — when the
action simply disappears, which the vendor documentation says is normal — the
robot's own reported pose being within `ROBOT_CHASSIS_ARRIVAL_TOLERANCE_M` of
where it was sent. Anything else is reported as "could not confirm".
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct
from typing import Any

logger = logging.getLogger("condo_voice.robot")

#: The laser-explored occupancy grid, straight from the board. Binary, not
#: JSON — the layout below is copied from this robot's own `/js/spec.js`
#: (`getExploreMap`), not from a general SLAMTEC manual:
#:   0-3   float32  x of the map origin (metres)
#:   4-7   float32  y of the map origin
#:   8-11  uint32   cells along x
#:   12-15 uint32   cells along y
#:   16-19 float32  resolution — side of one cell, metres
#:   20-31 reserved
#:   32-35 uint32   byte count of what follows; must equal cells_x * cells_y
#:   36-    bytes    one byte per cell, row-major from the origin
#: Little-endian throughout ("低位字节在前").
MAP_EXPLORE = "/api/core/slam/v1/maps/explore"
_MAP_HEADER = struct.Struct("<ffIIf")     # bytes 0-19
_MAP_SIZE_AT = 32
_MAP_DATA_AT = 36

#: Action factories the chassis advertised on 2026-09-10, of the twenty-one it
#: listed. Named constants rather than inline strings so a firmware that
#: renames one fails in a single place.
MOVE_TO = "slamtec.agent.actions.MoveToAction"
GO_HOME = "slamtec.agent.actions.GoHomeAction"
#: "遥控移动, 需要定时调用以达到连续运动效果" — remote-control move, to be
#: re-sent on a timer for continuous motion (this robot's /js/spec.js). The
#: joystick primitive: no target, no distance, a direction that lasts as
#: long as somebody keeps saying it. Speed is the board's own
#: `base.max_moving_speed` / `base.max_angular_speed`, not a per-call value.
MOVE_BY = "slamtec.agent.actions.MoveByAction"

#: `ActionDirection` from the same spec: 0 forward, 1 back, 2 right, 3 left.
DRIVE_DIRECTIONS = {"forward": 0, "back": 1, "right": 2, "left": 3}

#: Relocalization, from the spec: without `area` it is global; the movement
#: type decides whether the robot may turn in place to do it.
RECOVER_LOCALIZATION = "slamtec.agent.actions.RecoverLocalizationAction"
LOCALIZATION_ENABLE = "/api/core/slam/v1/localization/:enable"
LOCALIZATION_QUALITY = "/api/core/slam/v1/localization/quality"
LOCALIZATION_POSE = "/api/core/slam/v1/localization/pose"
HOMEDOCKS = "/api/core/slam/v1/homedocks"
#: Where a saved spot goes. The spec: caller generates the UUID,
#: `metadata.display_name` is what people see, `metadata.type` tells kinds
#: of POI apart. This is the single-map artifact API — the same list
#: `refresh_places()` falls back to, so a saved spot is a destination the
#: voice path can offer on the next poll.
POIS = "/api/core/artifact/v1/pois"
#: Persist the running map to the board's file system so POIs survive a
#: reboot. The spec forbids this in a multi-floor deployment ("会丢失其他
#: 楼层的地图" — other floors' maps are lost), so `save_map()` checks the
#: floor list first. Owner authorised on 2026-09-12 after a backup.
MAP_SAVE = "/api/multi-floor/map/v1/stcm/:save"
FLOORS = "/api/multi-floor/map/v1/floors"
MAP_STCM = "/api/core/slam/v1/maps/stcm"

#: Lidar arcs, radians from the robot's front, for "is there something in
#: the way of *this* direction". Front and back are ±35°; a 0.3 m stop line
#: needs the whole width of the robot covered, not one beam. Sides are for
#: the page's own display; turning in place is never blocked on them.
_ARC_FRONT = math.radians(35)
_ARC_BACK = math.pi - _ARC_FRONT
_ARC_SIDE = (math.radians(55), math.radians(125))


class ObstacleError(RuntimeError):
    """Refused to drive toward something closer than the clearance."""

    def __init__(self, direction: str, distance: float, limit: float):
        super().__init__("ใกล้สิ่งกีดขวาง %.2f ม. ทาง %s — หยุด" % (distance, direction))
        self.direction, self.distance, self.limit = direction, distance, limit

#: REST ActionState from this robot's /js/spec.js, confirmed by a live
#: GoHomeAction returning 0 then 1. These are NOT the C++ SDK bit flags.
#: Done carries a separate result: 0 success, -1 failed, -2 aborted.
STATUS_WAITING = 0
STATUS_RUNNING = 1
STATUS_FINISHED = 4
STATUS_PAUSED = 3
_TERMINAL = {STATUS_FINISHED}

#: Cached answers from the chassis. Read synchronously by `robot_link`, which
#: is called from tool handlers that must not block; written only by `_watch`.
#: Same shape of contract as `robot_link.STATE` — last measurement, not truth
#: at this instant.
STATE: dict[str, Any] = {
    "connected": False,
    "moving": None,
    "battery": None,
    "charging": None,
    "docked": None,
    "pose": None,
    "action_name": None,
    "action_status": None,
    "model": None,
    "error": None,
}

#: POI names from the chassis map, in map order. Empty is a real answer: the
#: chassis reported `[]` on 2026-09-10 because nobody has walked the robot
#: around the gallery and saved any points yet.
PLACES: list[str] = []

#: name -> {"x": float, "y": float, "yaw": float}
_POSES: dict[str, dict[str, float]] = {}

#: The walk we are waiting on: action id, the place name it was ordered for,
#: and where that place is, so a disappearing action can still be judged.
_pending: dict[str, Any] | None = None

_client: Any = None          # httpx.AsyncClient, created lazily
_task: asyncio.Task | None = None
_poi_countdown = 0

#: Extent of the last map read: origin_x, origin_y, width, height, resolution.
#: Kept so a clicked destination can be checked against the map without
#: pulling the whole grid again for every click. None until a map has been
#: read at all — and then `goto_xy` reads one first rather than guessing.
_map_bounds: dict[str, float] | None = None
_map_cells: bytes = b""
_map_read_at: float = 0.0

#: A map older than this is re-read before a click is judged against it —
#: the robot keeps exploring while somebody looks at the page.
MAP_MAX_AGE_S = 10.0

#: The hold-to-drive deadman: the task that will cancel the chassis action
#: unless `drive()` is called again first. One per process, because there is
#: one robot and the second joystick to say "forward" is not a second robot.
_drive_task: asyncio.Task | None = None
_drive_direction: str | None = None

#: The lidar frame the obstacle check reads while a drive burst is on. Kept
#: fresh by `_scan_watch` in the background instead of fetched inline on
#: every heartbeat: a heartbeat used to cost two round trips through the
#: `nc` relay (scan, then move), and when the pair ran long the board's
#: own MoveBy lifetime lapsed before the next one arrived — a stutter the
#: owner felt as "jerky" on 2026-09-12. One round trip per heartbeat now.
_scan_cache: dict[str, Any] | None = None
_scan_at: float = 0.0
_scan_task: asyncio.Task | None = None
SCAN_FRESH_S = 0.5
SCAN_WATCH_S = 0.2

#: What a map byte means. **Measured, not documented** — the spec says only
#: "one byte per cell". On 2026-09-12 (`scripts/probe_map_semantics.py`) the
#: live lidar frame was projected onto the live map: the robot's own cell
#: read 127; cells along the rays (free by definition) were 20..127 in
#: 4,668 of 4,928 samples; cells the returns ended in (obstacles by
#: definition) were 129..196 in 226 of 335, the rest 0. So, read as a
#: signed int8: positive = free, with 127 the most certain; negative =
#: occupied; 0 = never observed. The intuitive reading (higher = more
#: occupied) is backwards, and that is why this is measured.
CELL_UNKNOWN = "unknown"
CELL_FREE = "free"
CELL_OCCUPIED = "occupied"


def cell_kind(value: int) -> str:
    if value == 0:
        return CELL_UNKNOWN
    return CELL_FREE if value < 128 else CELL_OCCUPIED

#: The last action document `_tick` saw, so transitions can be logged
#: without a line every poll for the rest of the day.
_last_action_seen: Any = object()


def configured() -> bool:
    """True when this machine has been pointed at a chassis at all.

    Empty URL is the default and means "use the Android app path", which is
    what every machine did before this module existed.
    """
    from app.config import settings

    return bool(settings.robot_enabled and settings.robot_chassis_url.strip())


def connected() -> bool:
    """True only when the chassis answered us recently.

    Not "the URL is set". A tunnel that has fallen over answers nothing, and
    a robot we cannot reach is a robot we must not claim to be driving.
    """
    return bool(STATE["connected"])


def places() -> list[str]:
    return list(PLACES)


def status() -> dict:
    """For `/health` and the boot banner."""
    from app.config import settings

    return {
        "configured": configured(),
        "connected": connected(),
        "motion_enabled": settings.robot_chassis_motion_enabled,
        "url": settings.robot_chassis_url if configured() else "",
        "places_known": len(PLACES),
        "battery": STATE["battery"],
        "charging": STATE["charging"],
        "model": STATE["model"],
        "error": STATE["error"],
    }


def reset_state() -> None:
    """One definition, so tests and code cannot drift apart —
    `robot_link.reset_state` exists for exactly this reason."""
    global _pending, _poi_countdown, _map_bounds, _map_cells, _map_read_at
    global _drive_task, _drive_direction
    task, _drive_task = _drive_task, None
    _drive_direction = None
    if task is not None and not task.done():
        try:
            task.cancel()
        except RuntimeError:       # its loop is already gone (tests)
            pass
    _stop_scan_watch()
    STATE.update({
        "connected": False, "moving": None, "battery": None, "charging": None,
        "docked": None, "pose": None, "action_name": None, "action_status": None,
        "model": None, "error": None,
    })
    PLACES.clear()
    _POSES.clear()
    _pending = None
    _poi_countdown = 0
    _map_bounds = None
    _map_cells = b""
    _map_read_at = 0.0


# --------------------------------------------------------------------------
# HTTP


async def _http() -> Any:
    global _client
    if _client is None:
        import httpx

        from app.config import settings

        _client = httpx.AsyncClient(
            base_url=settings.robot_chassis_url.rstrip("/"),
            timeout=settings.robot_chassis_timeout_s,
        )
    return _client


async def request(method: str, path: str, payload: Any = None) -> Any:
    """One request. Returns parsed JSON, or None for 404.

    404 is not an error here: `/actions/:current` answers it whenever the
    robot is standing still, which is most of the time.
    """
    client = await _http()
    response = await client.request(method, path, json=payload)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


async def request_raw(method: str, path: str) -> bytes | None:
    """`request` for the endpoints that answer bytes, not JSON.

    Separate on purpose: `request()` is what every test stands in for, and a
    stand-in that has to answer JSON for one path and a byte stream for
    another is a stand-in that gets one of them wrong quietly. 404 is None
    here too — a robot that has never been driven around has no map.
    """
    client = await _http()
    response = await client.request(method, path)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content or None


# --------------------------------------------------------------------------
# Reading


async def power() -> dict:
    return await request("GET", "/api/core/system/v1/power/status") or {}


async def pose() -> dict:
    return await request("GET", "/api/core/slam/v1/localization/pose") or {}


async def info() -> dict:
    return await request("GET", "/api/core/system/v1/robot/info") or {}


#: One lidar frame. Shape from this robot's `/js/spec.js` (`LaserScan`) and
#: confirmed live on 2026-09-10: `{"laser_points": [{"angle", "distance",
#: "valid"}, ...], "pose": {...}}`. `angle` is radians from the robot's own
#: front, `distance` metres; the frame on that day had 1,586 points.
LASERSCAN = "/api/core/system/v1/laserscan"


async def laserscan(max_points: int = 0) -> dict[str, Any]:
    """The current lidar frame, tidied for a page that draws it as a radar.

    Points the board marks invalid are kept (with `valid: False`) so the
    radar can show where the lidar *looked* and saw nothing — a wall of
    missing returns is information. Anything that is not a numeric
    angle/distance pair is dropped rather than drawn at (0, 0).

    `max_points` thins the frame by taking every n-th point, which keeps the
    angular coverage while making a 1,586-point frame small enough to fetch
    a few times a second over the `nc` relay. 0 means everything.
    """
    raw = await request("GET", LASERSCAN)
    source = raw.get("laser_points") if isinstance(raw, dict) else None
    points: list[dict[str, Any]] = []
    if isinstance(source, list):
        for item in source:
            if not isinstance(item, dict):
                continue
            angle, distance = item.get("angle"), item.get("distance")
            if isinstance(angle, bool) or isinstance(distance, bool):
                continue
            if not isinstance(angle, (int, float)) or not isinstance(distance, (int, float)):
                continue
            if not (math.isfinite(angle) and math.isfinite(distance)):
                continue
            points.append({"angle": float(angle), "distance": float(distance),
                           "valid": bool(item.get("valid"))})
    total = len(points)
    valid = sum(1 for p in points if p["valid"])
    if max_points and total > max_points:
        step = math.ceil(total / max_points)
        points = points[::step]
    where = raw.get("pose") if isinstance(raw, dict) and isinstance(raw.get("pose"), dict) else None
    return {
        "pose": None if where is None else {k: where.get(k) for k in ("x", "y", "yaw")},
        "points": points,
        "total": total,
        "valid": valid,
        "clearance": clearance(points),
    }


def clearance(points: list[dict[str, Any]]) -> dict[str, float | None]:
    """Nearest valid lidar return in each of four arcs, metres; None = no
    return in that arc (nothing seen — which is not the same as clear)."""
    nearest: dict[str, float | None] = {"front": None, "back": None, "left": None, "right": None}

    def keep(key: str, d: float) -> None:
        if nearest[key] is None or d < nearest[key]:
            nearest[key] = d

    for p in points:
        if not p.get("valid") or not p.get("distance", 0) > 0:
            continue
        a = math.atan2(math.sin(p["angle"]), math.cos(p["angle"]))   # wrap to (-π, π]
        d = float(p["distance"])
        if abs(a) <= _ARC_FRONT:
            keep("front", d)
        elif abs(a) >= _ARC_BACK:
            keep("back", d)
        elif _ARC_SIDE[0] <= a <= _ARC_SIDE[1]:
            keep("left", d)
        elif -_ARC_SIDE[1] <= a <= -_ARC_SIDE[0]:
            keep("right", d)
    return nearest


async def localization_quality() -> int | None:
    """0..100 per the spec; None when the board did not answer a number.
    0 with a pose that drifts is odometry, not a fix."""
    value = await request("GET", LOCALIZATION_QUALITY)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def localization_enabled() -> bool | None:
    """False is "pure odometry mode" in the spec's own words — the state in
    which quality stays 0 no matter what the lidar sees."""
    value = await request("GET", LOCALIZATION_ENABLE)
    return value if isinstance(value, bool) else None


async def home_dock() -> dict[str, float] | None:
    """Where the charging dock is on the map, or None if none is registered.
    Same shape as a POI; the 2026-09-10 capture in the tests is the proof."""
    raw = await request("GET", HOMEDOCKS)
    if not isinstance(raw, list):
        return None
    for entry in raw:
        if isinstance(entry, dict):
            position = _poi_pose(entry)
            if position is not None:
                return position
    return None


def parse_explore_map(blob: bytes) -> dict[str, Any]:
    """Unpack the board's binary grid into numbers plus the raw cells.

    Strict about the size fields because a truncated proxy response — the
    tunnel to the chassis is a `nc` relay on the robot — would otherwise
    parse into a map that is half the room, and the click-to-go page would
    then offer destinations that are really off the edge of what was read.

    The cells are returned as bytes, untouched. What a byte *means* (free,
    occupied, unknown) is not in the spec and has not been measured on this
    robot yet; the page renders it as a greyscale until it has been.
    """
    if len(blob) < _MAP_DATA_AT:
        raise ValueError("explore map shorter than its own header: %d bytes" % len(blob))
    origin_x, origin_y, width, height, resolution = _MAP_HEADER.unpack_from(blob, 0)
    (size,) = struct.unpack_from("<I", blob, _MAP_SIZE_AT)
    if size != width * height:
        raise ValueError("explore map size field %d != %d x %d" % (size, width, height))
    cells = blob[_MAP_DATA_AT:_MAP_DATA_AT + size]
    if len(cells) != size:
        raise ValueError("explore map truncated: %d of %d cells" % (len(cells), size))
    if not (resolution > 0) or not math.isfinite(resolution):
        raise ValueError("explore map resolution %r is not usable" % (resolution,))
    return {
        "origin_x": float(origin_x),
        "origin_y": float(origin_y),
        "width": int(width),
        "height": int(height),
        "resolution": float(resolution),
        "cells": bytes(cells),
    }


def _bounds_of(grid: dict[str, Any]) -> dict[str, float]:
    return {k: float(grid[k]) for k in ("origin_x", "origin_y", "width", "height", "resolution")}


async def explore_map() -> dict[str, Any] | None:
    """The occupancy grid the robot is navigating in, or None when there is none.

    Also remembers the extent, which is what `goto_xy` checks a click against.
    """
    global _map_bounds, _map_cells, _map_read_at
    blob = await request_raw("GET", MAP_EXPLORE)
    if blob is None:
        return None
    grid = parse_explore_map(blob)
    _map_bounds = _bounds_of(grid)
    _map_cells = grid["cells"]
    _map_read_at = asyncio.get_running_loop().time()
    return grid


def inside_map(x: float, y: float, bounds: dict[str, float] | None = None) -> bool | None:
    """Is a world coordinate on the map at all? None when no map has been read."""
    bounds = _map_bounds if bounds is None else bounds
    if bounds is None:
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return False
    max_x = bounds["origin_x"] + bounds["width"] * bounds["resolution"]
    max_y = bounds["origin_y"] + bounds["height"] * bounds["resolution"]
    return bounds["origin_x"] <= x < max_x and bounds["origin_y"] <= y < max_y


def cell_at(x: float, y: float) -> int | None:
    """The map byte under a world coordinate, from the last map read.
    None when off the map or when no map has been read."""
    if _map_bounds is None or not inside_map(x, y):
        return None
    b = _map_bounds
    col = int(math.floor((x - b["origin_x"]) / b["resolution"]))
    row = int(math.floor((y - b["origin_y"]) / b["resolution"]))
    index = row * int(b["width"]) + col
    if index < 0 or index >= len(_map_cells):
        return None
    return _map_cells[index]


def _poi_name(poi: dict) -> str:
    """A POI's human name, wherever this firmware decided to put it.

    Tried in order rather than assumed, because the gallery's map has no
    points yet — the shape below is read from SLAMTEC's documentation, not
    from a populated map on this robot, and the first real POI is the test
    that settles it. Falling back to the id keeps an unparsable point
    *reachable* rather than silently absent from `places()`.
    """
    metadata = poi.get("metadata") if isinstance(poi.get("metadata"), dict) else {}
    for candidate in (metadata.get("display_name"), metadata.get("name"),
                      poi.get("display_name"), poi.get("name"), poi.get("id")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _poi_pose(poi: dict) -> dict[str, float] | None:
    raw = poi.get("pose")
    if not isinstance(raw, dict):
        metadata = poi.get("metadata")
        raw = metadata.get("pose") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return None
    try:
        return {"x": float(raw["x"]), "y": float(raw["y"]),
                "yaw": float(raw.get("yaw") or 0.0)}
    except (KeyError, TypeError, ValueError):
        return None


async def refresh_places() -> list[str]:
    """Re-read the chassis map's points of interest.

    The chassis is the authority, exactly as the robot app was: POIs are made
    by driving the robot somewhere and saving the pose. This is a cache of
    that, and an empty list is a true answer, not a failed read.
    """
    raw = await request("GET", "/api/multi-floor/map/v1/pois")
    if not raw:
        # `if not raw`, not `if raw is None`, and this robot is why. The
        # multi-floor API answers `[]` with a 200 when nothing is filed under
        # a floor, and on 2026-09-10 `/api/multi-floor/map/v1/floors` was
        # empty too — the gallery's robot is running a single unsaved map.
        # Treating that empty list as the answer would mean never asking the
        # single-map artifact API at all, and reporting "no destinations" on
        # a robot that has some. Found by running this against the real
        # board, not by reading the documentation.
        raw = await request("GET", "/api/core/artifact/v1/pois")
    if not isinstance(raw, list):
        return list(PLACES)

    names: list[str] = []
    poses: dict[str, dict[str, float]] = {}
    for poi in raw:
        if not isinstance(poi, dict):
            continue
        name = _poi_name(poi)
        position = _poi_pose(poi)
        if not name or position is None:
            logger.warning("chassis POI skipped — no usable name or pose: %r", poi)
            continue
        if name not in poses:
            names.append(name)
        poses[name] = position

    PLACES[:] = names
    _POSES.clear()
    _POSES.update(poses)
    return list(PLACES)


# --------------------------------------------------------------------------
# Commanding


async def current_action() -> dict | None:
    action = await request("GET", "/api/core/motion/v1/actions/:current")
    return action if isinstance(action, dict) else None


def _action_id(action: dict) -> Any:
    for key in ("action_id", "id", "actionId"):
        if key in action:
            return action[key]
    return None


def _finished(action: dict | None) -> tuple[bool, bool | None]:
    """(is it over, did it succeed) for one action document.

    `None` for the second element means *we do not know* — reported to the
    guest as a failure to confirm, never as an arrival. The caller may then
    fall back to comparing the robot's pose against where it was sent.
    """
    if action is None:
        return True, None
    state = action.get("state") if isinstance(action.get("state"), dict) else action
    status = state.get("status")
    if type(status) is not int or status not in _TERMINAL:
        return False, None
    result = state.get("result")
    if type(result) is not int:
        return True, None
    return True, result == 0


async def move_to(x: float, y: float, yaw: float = 0.0) -> Any:
    """Order a walk. Returns the action id; raises on transport failure.

    `mode: 0` is navigation with obstacle avoidance rather than track
    following — the only mode that makes sense in a room with customers in it.
    """
    action = await request("POST", "/api/core/motion/v1/actions", {
        "action_name": MOVE_TO,
        "options": {
            "target": {"x": float(x), "y": float(y), "z": 0},
            "move_options": {"mode": 0, "flags": [], "yaw": float(yaw),
                             "acceptable_precision": 0, "fail_retry_count": 0},
        },
    })
    # Logged whole, because the interesting failure is not "the POST was
    # refused" — the board answers 200 and the action then vanishes within a
    # second, taking its reason with it. On 2026-09-10 twenty-two commands
    # reached the chassis, every one accepted, and the robot never moved; the
    # only place an explanation could have been was in this document.
    logger.info("chassis action created: %s", action)
    return _action_id(action) if isinstance(action, dict) else None


async def go_home() -> Any:
    """Back onto the charging dock — `GoHomeAction` with the spec's default
    `flags: dock`, which is *docking*, not "stand near the dock".

    Refused while the robot does not know where it is: a go-home from an
    odometry pose is a walk to where the dock would be if the robot were
    where it thinks it is, which on 2026-09-12 was three metres off.
    """
    from app.config import settings

    quality = await localization_quality()
    if quality is None or quality < settings.robot_poi_min_quality:
        raise ValueError("หุ่นยังไม่รู้ตำแหน่งตัวเองพอที่จะหาแท่น (localization_quality=%s ต้อง ≥ %d) — กดปรับตำแหน่งก่อน"
                         % (quality, settings.robot_poi_min_quality))
    action = await request("POST", "/api/core/motion/v1/actions",
                           {"action_name": GO_HOME, "options": {}})
    logger.info("chassis go_home created: %s", action)
    return _action_id(action) if isinstance(action, dict) else None


async def save_map() -> dict[str, Any]:
    """Write the running map to the board's storage so it survives a reboot.

    Guarded by the floor list because the spec is explicit that in a
    multi-floor deployment this loses every other floor. This robot has
    none (`floors: []` on 2026-09-10 and 2026-09-12). Returns the size of
    the map that was current at the time, so the log has a number to
    compare with the backup.
    """
    floors = await request("GET", FLOORS)
    if isinstance(floors, list) and len(floors) > 1:
        raise ValueError("หุ่นมีหลายชั้น (%d) — การเซฟแบบนี้จะลบแมพชั้นอื่น ต้องทำผ่าน RoboStudio" % len(floors))
    current = await request_raw("GET", MAP_STCM)
    await request("POST", MAP_SAVE)
    size = len(current) if current else 0
    logger.info("chassis map saved to the board (%d bytes in memory at the time)", size)
    return {"saved": True, "map_bytes": size}


async def cancel() -> None:
    await request("DELETE", "/api/core/motion/v1/actions/:current")


async def nudge(forward_m: float = 0.0, turn_rad: float = 0.0) -> Any:
    """One small, finite step from wherever the robot is standing.

    Built on `MoveToAction` rather than the chassis's own `MoveByAction` and
    `RotateAction`, which it also advertises and which would be the natural
    fit. Their option shapes are in no documentation we have, and the first
    thing an unverified payload would do here is move a robot in a room with
    people in it. `MoveToAction` is the one body this chassis has actually
    accepted, so a jog is arithmetic on top of it instead of a guess.

    **Bounded by construction.** The target is computed once, from the pose at
    the moment of the press, so losing the link mid-step leaves a walk of at
    most one step — not a robot still driving with nobody able to call it back.
    """
    from app.config import settings

    if not settings.robot_chassis_motion_enabled:
        logger.warning("chassis motion is locked pending hardware acceptance")
        raise PermissionError("motion locked")
    where = await pose()
    x = float(where.get("x") or 0.0)
    y = float(where.get("y") or 0.0)
    yaw = float(where.get("yaw") or 0.0)
    return await move_to(x + forward_m * math.cos(yaw),
                         y + forward_m * math.sin(yaw), yaw + turn_rad)


async def drive(direction: str) -> bool:
    """Keep the robot moving in a direction for as long as this keeps being
    called. Returns True when this call *started* a burst, False when it only
    kept one going — so the caller can log the press, not every heartbeat.

    The joystick feel the nudge could not give: `MoveToAction` per press is
    accelerate–decelerate–stop every 0.3 m. `MoveByAction` is the board's own
    remote-control primitive and runs until it is not re-sent.

    **What bounds it.** Not distance any more — time. A server-side deadman
    (`ROBOT_DRIVE_TIMEOUT_MS`) cancels the action itself when the page goes
    quiet: closed tab, dropped Wi-Fi, a finger that slid off the button. The
    board is documented to stop on its own too, but nobody has measured after
    how long, so this does not rely on it. Raises ValueError for a direction
    the board has no code for; PermissionError while motion is locked.
    """
    global _drive_task, _drive_direction
    from app.config import settings

    if direction not in DRIVE_DIRECTIONS:
        raise ValueError("ไม่รู้จักทิศ %r" % (direction,))
    if not settings.robot_chassis_motion_enabled:
        logger.warning("chassis motion is locked pending hardware acceptance")
        raise PermissionError("motion locked")

    started = _drive_task is None or _drive_task.done()
    await _refuse_if_blocked(direction, running=not started)
    # The spec is explicit that MoveBy must be re-sent on a timer to keep
    # moving ("需要定时调用以达到连续运动效果"), so every heartbeat still
    # posts one — what changed on 2026-09-12 is that it posts *only* that:
    # the lidar check reads the watcher's frame instead of fetching its own.
    t0 = asyncio.get_running_loop().time()
    await request("POST", "/api/core/motion/v1/actions", {
        "action_name": MOVE_BY,
        "options": {"direction": DRIVE_DIRECTIONS[direction]},
    })
    took_ms = (asyncio.get_running_loop().time() - t0) * 1000.0
    if took_ms > 100:
        # Worth a line: a slow relay is the one thing that makes a smooth
        # burst stutter, and nobody can see it from the page.
        logger.info("drive: MoveBy POST took %.0f ms", took_ms)
    STATE["moving"] = True
    _drive_direction = direction
    _start_scan_watch()
    # Re-arm the deadman: the previous one would have fired for a press that
    # is still happening.
    old, _drive_task = _drive_task, asyncio.create_task(_drive_deadman())
    if old is not None and not old.done():
        old.cancel()
    if started:
        logger.info("drive: %s (deadman %d ms)", direction, settings.robot_drive_timeout_ms)
    return started


async def _refuse_if_blocked(direction: str, running: bool) -> None:
    """The 0.3 m stop line, checked on every heartbeat with a fresh frame.

    `MoveByAction` is remote control: unlike `MoveToAction` nothing on the
    board is planning around obstacles, so this is the only thing between
    the joystick and the wall. Only the arc in the direction of travel
    counts — a robot 0.7 m from a wall must still be allowed to turn and to
    back away, or "stuck" gets called "safe". If a burst is already running
    it is stopped here, before the refusal is raised, so the page seeing the
    error is not a page whose robot is still rolling.
    """
    from app.config import settings

    arc = {"forward": "front", "back": "back"}.get(direction)
    if arc is None:
        return
    limit = settings.robot_drive_min_clearance_m
    if limit <= 0:
        return
    nearest = (await _fresh_scan())["clearance"][arc]
    if nearest is not None and nearest < limit:
        if running:
            await drive_stop(reason="obstacle")
        raise ObstacleError(direction, nearest, limit)


async def _fresh_scan() -> dict[str, Any]:
    """The latest lidar frame, from the background watcher when it is
    recent enough, fetched inline only when it is not (first heartbeat of
    a burst, or the watcher fell behind). Never a stale frame: a wall that
    appeared half a second ago is a wall."""
    global _scan_cache, _scan_at
    now = asyncio.get_running_loop().time()
    if _scan_cache is not None and now - _scan_at <= SCAN_FRESH_S:
        return _scan_cache
    _scan_cache = await laserscan()
    _scan_at = asyncio.get_running_loop().time()
    return _scan_cache


async def _scan_watch() -> None:
    global _scan_cache, _scan_at
    while True:
        try:
            frame = await laserscan()
            _scan_cache, _scan_at = frame, asyncio.get_running_loop().time()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Leave the cache to go stale; the next heartbeat then fetches
            # inline and, if that fails too, the drive refuses — a scan we
            # cannot read is not a clear road.
            logger.warning("drive: lidar read failed — %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(SCAN_WATCH_S)


def _start_scan_watch() -> None:
    global _scan_task
    if _scan_task is None or _scan_task.done():
        _scan_task = asyncio.create_task(_scan_watch())


def _stop_scan_watch() -> None:
    global _scan_task, _scan_cache
    task, _scan_task = _scan_task, None
    _scan_cache = None
    if task is not None and not task.done() and task is not asyncio.current_task():
        try:
            task.cancel()
        except RuntimeError:
            pass


async def _drive_deadman() -> None:
    from app.config import settings

    await asyncio.sleep(settings.robot_drive_timeout_ms / 1000.0)
    logger.warning("drive: page went quiet while holding %r — stopping the chassis",
                   _drive_direction)
    await drive_stop(reason="deadman")


async def drive_stop(reason: str = "release") -> None:
    """End a drive burst now: disarm the deadman, cancel the board's action.

    Safe to call when nothing is being driven — it is what the stop button
    does, and a stop button that has to check first is not a stop button.
    """
    global _drive_task, _drive_direction
    task, _drive_task = _drive_task, None
    was = _drive_direction
    _drive_direction = None
    if task is not None and not task.done() and task is not asyncio.current_task():
        task.cancel()
    _stop_scan_watch()
    STATE["moving"] = None
    await cancel()
    if was is not None:
        logger.info("drive: stopped (%s) after %s", reason, was)


async def relocalize(mode: str = "dock") -> dict[str, Any]:
    """Get the robot to know where it is on its map again.

    On 2026-09-12 the robot sat on its dock with `localization_quality` 0
    and a pose of (0, 0) — odometry, not a fix — so every clicked
    destination was measured from a point that was not where the robot was.
    Three ways up, in order of how little they move the robot:

    * `dock`   — it is on the charging dock, and the dock is on the map, so
                 tell SLAM that is where it is (`PUT localization/pose`) and
                 ask it to confirm without moving. Refused when the power
                 status says it is not docked: a pose set from a wrong
                 assumption is worse than no pose.
    * `static` — confirm from where it stands, no movement.
    * `rotate` — let it turn in place to find itself. This moves the
                 robot, so it is behind the motion lock like everything
                 that does.

    All three first switch localization back on if the board reports it
    paused ("pure odometry mode" in the spec's words), because in that
    state quality stays 0 whatever the lidar sees. Returns what was found
    and done; the page should watch `localization_quality` afterwards —
    the recovery is an action and finishes on its own time.
    """
    from app.config import settings

    if mode not in {"dock", "static", "rotate"}:
        raise ValueError("ไม่รู้จักโหมด relocalize %r" % (mode,))
    if mode == "rotate" and not settings.robot_chassis_motion_enabled:
        raise PermissionError("motion locked")

    report: dict[str, Any] = {"mode": mode}
    report["quality_before"] = await localization_quality()
    enabled = await localization_enabled()
    report["localization_was_paused"] = enabled is False
    if enabled is False:
        await request("PUT", LOCALIZATION_ENABLE, {"enable": True})

    if mode == "dock":
        docking = (await power()).get("dockingStatus")
        if docking != "on_dock":
            raise ValueError("หุ่นไม่ได้อยู่บนแท่นชาร์จ (dockingStatus=%s) — ใช้โหมด static หรือ rotate แทน" % docking)
        dock = await home_dock()
        if dock is None:
            raise ValueError("แมพนี้ไม่มีแท่นชาร์จลงทะเบียน — ใช้โหมด static หรือ rotate แทน")
        await request("PUT", LOCALIZATION_POSE, {
            "x": dock["x"], "y": dock["y"], "z": 0,
            "yaw": dock["yaw"], "pitch": 0, "roll": 0,
        })
        report["pose_set_to_dock"] = dock

    action = await request("POST", "/api/core/motion/v1/actions", {
        "action_name": RECOVER_LOCALIZATION,
        "options": {"relocalization_options": {
            "max_recover_time": 15000,
            "recover_movement_type": "RotateOnly" if mode == "rotate" else "NoMove",
        }},
    })
    logger.info("relocalize %s: %s", mode, action)
    report["action_id"] = _action_id(action) if isinstance(action, dict) else None
    return report


#: How far the registered dock may sit from a robot that is *on* it before
#: the registration is called stale. Found on 2026-09-12: the dock was
#: registered at (-0.25, 0.01) while the docked, localized robot read
#: (3.05, -4.05) — 5.2 metres — because the board's
#: `docking.docked_register_strategy` is `when_not_exists`, so a dock
#: registered in an earlier map frame is never corrected on its own. A
#: go-home from off the dock would have navigated to the old point.
DOCK_STALE_M = 1.0


async def dock_check() -> dict[str, Any]:
    """Is the registered dock where the robot is standing while docked?

    Only meaningful on the dock with a localization fix; otherwise the
    answer is None rather than a number that means nothing.
    """
    docked = (await power()).get("dockingStatus") == "on_dock"
    quality = await localization_quality()
    dock = await home_dock()
    where = await pose()
    out: dict[str, Any] = {"docked": docked, "quality": quality, "dock": dock, "stale": None, "offset_m": None}
    if not docked or dock is None or quality is None:
        return out
    from app.config import settings

    if quality < settings.robot_poi_min_quality:
        return out
    try:
        offset = math.hypot(float(where["x"]) - dock["x"], float(where["y"]) - dock["y"])
    except (KeyError, TypeError, ValueError):
        return out
    out["offset_m"] = round(offset, 3)
    out["stale"] = offset > DOCK_STALE_M
    return out


async def register_dock() -> dict[str, Any]:
    """Move the dock registration to where the robot is standing, docked.

    Edits the existing dock in place (`PUT homedocks/{id}`) rather than
    adding a second one: two docks and `go_home` picks, and the one it
    picks may be the stale one. Refused off the dock or without a fix, for
    the same reason `save_poi` is — a dock registered from odometry is a
    dock in the wrong place, which is the fault being fixed. Persists the
    map afterwards so the correction survives a reboot.
    """
    from app.config import settings

    reading = await power()
    if reading.get("dockingStatus") != "on_dock":
        raise ValueError("ต้องอยู่บนแท่นชาร์จก่อน (dockingStatus=%s)" % reading.get("dockingStatus"))
    quality = await localization_quality()
    if quality is None or quality < settings.robot_poi_min_quality:
        raise ValueError("หุ่นยังไม่รู้ตำแหน่งตัวเองพอ (localization_quality=%s ต้อง ≥ %d) — กดปรับตำแหน่งก่อน"
                         % (quality, settings.robot_poi_min_quality))
    where = await pose()
    here = {"x": float(where["x"]), "y": float(where["y"]), "yaw": float(where.get("yaw") or 0.0)}
    raw = await request("GET", HOMEDOCKS)
    existing = [d for d in raw if isinstance(d, dict) and d.get("id")] if isinstance(raw, list) else []
    before = _poi_pose(existing[0]) if existing else None
    if existing:
        await request("PUT", "%s/%s" % (HOMEDOCKS, existing[0]["id"]), {"pose": here})
    else:
        await request("POST", "%s/:register" % HOMEDOCKS, {"metadata": {"display_name": "home_dock"}})
    after = await home_dock()
    if after is None or math.hypot(after["x"] - here["x"], after["y"] - here["y"]) > 0.05:
        raise RuntimeError("บอร์ดรับคำสั่งแต่ตำแหน่งแท่นไม่เปลี่ยน (%s)" % (after,))
    logger.info("dock re-registered at (%.2f, %.2f, yaw %.2f); was %s", here["x"], here["y"], here["yaw"], before)
    try:
        persisted = (await save_map())["saved"]
    except Exception as exc:
        logger.warning("dock re-registered but the map could not be persisted: %s", exc)
        persisted = False
    return {"dock": after, "was": before, "quality": quality, "persisted": persisted}


async def save_poi(name: str) -> dict[str, Any]:
    """Mark where the robot is standing as a named destination on its map.

    The way POIs were always meant to be made — drive the robot somewhere
    and save the pose — just done from our page instead of the vendor's.
    Two refusals, both about the pose being real:

    * `localization_quality` below `ROBOT_POI_MIN_QUALITY`: the pose is
      odometry, and a spot saved from odometry is a lie every future
      `go_to_place` will repeat.
    * a name already on the map: two "ที่ชาร์จ" is a coin toss for the guest
      who asks for it; pick another name or delete the old one on the board.

    Not persisted across a reboot by this call: the POI lives in the map the
    board is running, and this robot runs an unsaved map (2026-09-10).
    Saving the map is a separate, bigger decision.
    """
    from app.config import settings

    name = (name or "").strip()
    if not name:
        raise ValueError("ต้องตั้งชื่อจุดก่อน")
    quality = await localization_quality()
    if quality is None or quality < settings.robot_poi_min_quality:
        raise ValueError("หุ่นยังไม่รู้ตำแหน่งตัวเองพอ (localization_quality=%s ต้อง ≥ %d) — กดปรับตำแหน่งก่อน"
                         % (quality, settings.robot_poi_min_quality))
    await refresh_places()
    if name in PLACES:
        raise ValueError("มีจุดชื่อ %r อยู่แล้ว — ตั้งชื่ออื่น" % name)

    import uuid

    poi_id = str(uuid.uuid4())
    # No `pose` on purpose — measured on 2026-09-12 against this board: a
    # POI *with* a pose is refused, 403 "operation fail", both on POST and
    # on PUT by id; the same body without a pose is accepted and the board
    # files it at its own current position (the spec's own recommendation,
    # "建议不包含Pose，此时会用机器人当前位置创建POI"). Which is also the
    # honest version of "save where I am": the board's idea of where it is,
    # not ours from a moment earlier.
    await request("POST", POIS, {
        "id": poi_id,
        "metadata": {"display_name": name, "type": "point"},
    })
    places_now = await refresh_places()
    here = _POSES.get(name)
    if name not in places_now or here is None:
        # The board said 200 and then does not list it: the 2026-09-10 shape
        # of failure. Say so rather than report a spot nobody can go to.
        raise RuntimeError("บอร์ดรับจุดแล้วแต่ไม่แสดงในรายการ — ลองอ่านซ้ำหรือดูที่ RoboStudio")
    logger.info("saved POI %r at (%.2f, %.2f, yaw %.2f) quality=%s", name, here["x"], here["y"], here["yaw"], quality)
    # A point that does not survive a reboot is a point the owner will save
    # again next week. Persisting is owner-authorised (2026-09-12); a failure
    # here is reported, not hidden — the point exists until the next reboot.
    try:
        persisted = (await save_map())["saved"]
    except Exception as exc:
        logger.warning("POI %r saved in memory but the map could not be persisted: %s", name, exc)
        persisted = False
    return {"name": name, "id": poi_id, "pose": dict(here), "quality": quality,
            "places": places_now, "persisted": persisted}


#: How far `undock()` walks off the dock: enough to clear the dock's IR
#: field for the docking test the owner asked for, short enough to be one
#: bounded step.
UNDOCK_M = 0.6


async def undock() -> dict[str, Any]:
    """Leave the dock: one short walk straight ahead, as a `MoveToAction`.

    There is no undock action in this firmware's spec, and on 2026-09-12
    `MoveByAction` (remote control) on the dock produced actions that
    finished without the robot moving at all. `MoveToAction` is what the
    vendor app uses to leave — the board handles leaving the dock as part
    of navigating — so this is the same target arithmetic as `goto_xy`,
    with the target the robot's own heading `UNDOCK_M` ahead (the robot
    docks backwards, so ahead is away). Refused off the dock, without a
    fix, or with anything closer in front than the step needs.
    """
    from app.config import settings

    reading = await power()
    if reading.get("dockingStatus") != "on_dock":
        raise ValueError("หุ่นไม่ได้อยู่บนแท่น (dockingStatus=%s) — ใช้จอยหรือ goto ได้เลย" % reading.get("dockingStatus"))
    front = (await laserscan())["clearance"]["front"]
    need = UNDOCK_M + settings.robot_drive_min_clearance_m
    if front is not None and front < need:
        raise ObstacleError("forward", front, need)
    where = await pose()
    x, y, yaw = float(where["x"]), float(where["y"]), float(where.get("yaw") or 0.0)
    target = (x + UNDOCK_M * math.cos(yaw), y + UNDOCK_M * math.sin(yaw))
    sent = await goto_xy(*target)
    return {"sent": sent, "target": {"x": round(target[0], 3), "y": round(target[1], 3)}, "from": {"x": x, "y": y, "yaw": yaw}}


async def goto_xy(x: float, y: float) -> str:
    """A walk to a point somebody clicked on the map, for the control page.

    The one manual command whose destination the *client* chooses, which is
    exactly what `_NUDGE_METRES` in main.py exists to prevent — so it is held
    to something a nudge is not: the point has to be on the map the robot is
    navigating in. Off-map here is refused before anything is sent, not left
    for the planner to fail silently on (the 2026-09-10 failure mode: the
    board answers 200 and the action just sits there).

    And the clicked cell has to be one the robot has *seen to be free* —
    see `cell_kind`, which is a measurement from 2026-09-12, not a reading of
    the documentation. A wall is refused outright; a cell the lidar has never
    observed is refused too, because on a freshly booted robot that is 80% of
    the map and the planner's answer to it is the 2026-09-10 silence. The
    obstacle avoidance of `mode: 0` still applies on the way there.
    Raises ValueError for a point that cannot be sent; returns the
    `send()` verdict otherwise.
    """
    x, y = float(x), float(y)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError("พิกัดไม่ใช่ตัวเลข")
    if _map_bounds is None or \
            asyncio.get_running_loop().time() - _map_read_at > MAP_MAX_AGE_S:
        await explore_map()
    on_map = inside_map(x, y)
    if on_map is None:
        raise ValueError("หุ่นยังไม่มีแมพ — ส่งพิกัดไม่ได้")
    if not on_map:
        raise ValueError("จุดนี้อยู่นอกแมพ")
    value = cell_at(x, y)
    kind = CELL_UNKNOWN if value is None else cell_kind(value)
    if kind == CELL_OCCUPIED:
        raise ValueError("จุดนี้เป็นสิ่งกีดขวางบนแมพ")
    if kind == CELL_UNKNOWN:
        raise ValueError("จุดนี้หุ่นยังไม่เคยเห็น (ยังไม่ได้สำรวจ) — เลือกจุดในพื้นที่ที่แมพว่างแล้ว")
    return await send("move_to_xy", x=x, y=y)


async def live_state() -> dict:
    """Everything the manual-control page needs, read fresh.

    Separate from `STATE` on purpose: that cache is refreshed on the poll
    interval and is right for a tool result, but somebody standing next to a
    moving robot with a stop button needs the current answer, not the one
    from up to two seconds ago.
    """
    reading = await power()
    where = await pose()
    action = await current_action()
    try:
        quality = await request("GET", "/api/core/slam/v1/localization/quality")
    except Exception:
        quality = None
    from app.config import settings

    return {
        "connected": True,
        "motion_enabled": settings.robot_chassis_motion_enabled,
        "battery": reading.get("batteryPercentage"),
        "charging": reading.get("isCharging"),
        "docking": reading.get("dockingStatus"),
        "power_stage": reading.get("powerStage"),
        "pose": {k: where.get(k) for k in ("x", "y", "yaw")},
        "localization_quality": quality,
        "action": None if action is None else {
            "id": _action_id(action),
            "name": action.get("action_name"),
            "status": (action.get("state") or {}).get("status")
            if isinstance(action.get("state"), dict) else None,
        },
        "places": list(PLACES),
        "model": STATE.get("model"),
    }


async def send(action: str, **args: Any) -> str:
    """The `robot_link.send` contract: "ok" or "failed", never a promise.

    Deliberately does not wait for the walk. See `robot_link.send` — a
    synchronous tool call held open for half a minute is half a minute of a
    robot standing mute beside the person it just offered to guide.
    """
    global _pending
    from app.config import settings

    if action in {"move_to_point", "move_to_xy", "go_home"} \
            and not settings.robot_chassis_motion_enabled:
        logger.warning("chassis motion is locked pending hardware acceptance")
        return "failed"
    try:
        if action == "move_to_xy":
            # Same road as a named point, minus the name: the arrival is still
            # judged by `_tick` against the coordinate, and the label is what
            # `robot_link.arrived` will be told if anything is waiting.
            target = {"x": float(args["x"]), "y": float(args["y"]),
                      "yaw": float((await pose()).get("yaw") or 0.0)}
            action_id = await move_to(target["x"], target["y"], target["yaw"])
            _pending = {"id": action_id,
                        "place": "พิกัด (%.2f, %.2f)" % (target["x"], target["y"]),
                        "target": target}
            STATE["moving"] = True
            return "ok"
        if action == "move_to_point":
            place = args.get("place")
            target = _POSES.get(place or "")
            if target is None:
                logger.warning("chassis has no pose for %r — refusing to guess", place)
                return "failed"
            action_id = await move_to(target["x"], target["y"], target["yaw"])
            _pending = {"id": action_id, "place": place, "target": target}
            STATE["moving"] = True
            return "ok"
        if action == "go_home":
            action_id = await go_home()
            _pending = {"id": action_id, "place": "base", "target": None}
            STATE["moving"] = True
            return "ok"
        if action == "cancel_navigation":
            STATE["moving"] = None
            await cancel()
            _pending = None
            return "ok"
    except Exception:
        logger.exception("chassis command %r failed", action)
        STATE["connected"] = False
        STATE["moving"] = None
        STATE["error"] = "command failed"
        return "failed"
    logger.warning("chassis does not implement %r", action)
    return "failed"


# --------------------------------------------------------------------------
# The clock


def _reached(target: dict[str, float] | None, where: dict) -> bool | None:
    """Did the robot end up where it was sent? None when we cannot tell."""
    if target is None:
        return None
    try:
        dx = float(where["x"]) - target["x"]
        dy = float(where["y"]) - target["y"]
    except (KeyError, TypeError, ValueError):
        return None
    from app.config import settings

    return math.hypot(dx, dy) <= settings.robot_chassis_arrival_tolerance_m


async def _tick() -> None:
    global _pending, _poi_countdown

    reading = await power()
    # RoboStudio and the vendor app can start actions too. Read the board
    # even when this process has no pending command; the old cached False
    # otherwise described a robot navigating under another controller.
    action = await current_action()
    where = await pose()
    STATE["pose"] = where or None
    # The board's own account of the action, kept whole and logged whenever it
    # changes. Not per poll — that would be a line every two seconds forever —
    # but every transition, because the transition is the diagnosis: an action
    # that goes straight from created to gone is a refusal the chassis never
    # spelled out anywhere else.
    global _last_action_seen
    if action != _last_action_seen:
        logger.info("chassis action now: %s", action)
        _last_action_seen = action
    if action is None:
        STATE["action_name"] = None
        STATE["action_status"] = None
        STATE["moving"] = False  # No navigation action; not wheel-speed feedback.
    else:
        action_state = action.get("state")
        action_state = action_state if isinstance(action_state, dict) else action
        code = action_state.get("status")
        STATE["action_name"] = action.get("action_name")
        STATE["action_status"] = code
        if type(code) is int and code in _TERMINAL:
            STATE["moving"] = False
        elif (type(code) is int and code in {STATUS_WAITING, STATUS_RUNNING, STATUS_PAUSED}
              and action.get("action_name") in {MOVE_TO, GO_HOME}):
            # Active navigation, not measured wheel speed. Other factories
            # include HealthSupervisoryAction; "running" alone is not motion.
            STATE["moving"] = True
        else:
            STATE["moving"] = None
    STATE["connected"] = True
    STATE["error"] = None
    if isinstance(reading.get("batteryPercentage"), (int, float)):
        STATE["battery"] = reading["batteryPercentage"]
    STATE["charging"] = reading.get("isCharging")
    STATE["docked"] = reading.get("dockingStatus") == "on_dock"

    if _poi_countdown <= 0:
        await refresh_places()
        if STATE["model"] is None:
            STATE["model"] = (await info()).get("modelName")
        _poi_countdown = 15
    _poi_countdown -= 1

    if _pending is None:
        return

    mine = action is not None and (
        _pending["id"] is None or _action_id(action) == _pending["id"])
    over, ok = _finished(action if mine else None)
    if not over:
        return

    place, target = _pending["place"], _pending["target"]
    _pending = None
    if ok is None:
        # The action is simply gone, which the vendor documentation says is
        # the normal end of a walk. Ask the robot where it is instead of
        # assuming either answer.
        if place == "base":
            ok = bool(STATE["docked"])
        else:
            ok = _reached(target, where) or False

    from app.tools import robot_link

    robot_link.STATE["status_source"] = "chassis"
    if not await robot_link.arrived(place, bool(ok)):
        logger.info("chassis: walk to %r ended (ok=%s) with nothing waiting", place, ok)


async def _watch() -> None:
    from app.config import settings

    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if STATE["connected"]:
                logger.warning("chassis unreachable — %s: %s", type(exc).__name__, exc)
            STATE["connected"] = False
            STATE["error"] = f"{type(exc).__name__}: {exc}"
            # A walk we can no longer see is not a walk we can call finished.
            # `robot.go_to_place` armed ROBOT_ARRIVAL_TIMEOUT_S for exactly
            # this, and it announces the failure out loud when it fires.
            STATE["moving"] = None
        await asyncio.sleep(settings.robot_chassis_poll_s)


def start() -> None:
    """Begin polling, if this machine is pointed at a chassis.

    Checks the switch itself so `main.py` does not have to be kept in step
    with it — the same shape as `greeter.start()`.
    """
    global _task
    if _task is not None and not _task.done():
        return
    if not configured():
        return
    from app.config import settings

    logger.info("robot chassis: polling %s every %.1fs",
                settings.robot_chassis_url, settings.robot_chassis_poll_s)
    _task = asyncio.create_task(_watch())


async def stop() -> None:
    """Let go of the poller and the socket.

    Does not cancel a walk in progress: the server going down is not a reason
    to move the robot, and a chassis left walking is exactly what the
    physical stop button is for.
    """
    global _task, _client
    task, _task = _task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if _drive_task is not None and not _drive_task.done():
        # A joystick press in flight while the server shuts down: the
        # deadman will not get to fire, so fire it now. The one exception to
        # "the server going down is not a reason to move the robot" — this
        # is a reason to *stop* it, which is the direction that is always
        # allowed.
        try:
            await drive_stop(reason="shutdown")
        except Exception:
            logger.exception("drive: could not stop the chassis on shutdown")
    client, _client = _client, None
    if client is not None:
        await client.aclose()
    reset_state()
