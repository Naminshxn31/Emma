"""Emma as the host of an event -- the scenes staff switch from a phone.

Owner, 30 Oct 2026: "ทำโหมดใหม่ แยกเป็นอีกแอบ อันนี้จะใช้สำหรับให้ emma เป็น host
งานคอยรับแขกในวันงานอีเว้น ... หลักๆ ก็มีเชิญลูกค้า แล้วพาไปยังจุดต่างๆ และคอยไป
ทักทายคนต่างๆที่โต๊ะ ... อาจจะทำเป็นซีนๆ" -- then: office floor 1, "ให้ทีมงานกด
เปลี่ยนซีน", 25-26 Nov.

The pieces:
* data/events/floor1_event.json (EVENT_FILE): the event's facts, its spots and
  its scenes. A scene is where Emma stands (`spot`, or a list of `stops` such
  as the tables) and what she says when it starts and when she gets there.
  Draft text until the owner gives the real one.
* <same name>.spots.json beside it, written on the robot by "บันทึกตำแหน่ง
  ตอนนี้" on the staff page: the measured poses, and how many clean arrivals
  each spot has had. gitignored; deploy never deletes or overwrites it (the
  server wrote it -- scripts/robot_server.py robot_edited/changed).
* The `event` profile (app/prompts.py `event_block`): the sales host (condo)
  with this block on top. The robot app "Emma Event" opens the face with
  event=1.
* What the model is told about the scene goes three ways, because only the
  last two are known to reach it (CLAUDE.md "คำสั่งที่ติดไปกับ tool result ถึง
  โมเดลจริง"): the block in the system prompt (the event, how scenes work), an
  announcement when staff switch scenes or a walk arrives (events.announce),
  and `scene_note()` beside every tool result (app/tools/registry.model_view).

Staff move the robot; a voice never does -- the event profile does not see the
walking tools at all (registry), and app/event_walk.py has its own stops.
The model picks nothing here: staff pick the scene, the file says the words'
gist, and the facts she may state about the event are the file's lines.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from app import event_walk, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_host")

ROOT = Path(__file__).resolve().parent.parent
#: board_walk's max moving speed for a spot reached by one MoveTo, when the
#: spot names none: the reception tour's 0.35 less a little -- people stand
#: around a host, and a guest walks beside her.
DEFAULT_SPEED = 0.3

STATE: dict = {"scene": None, "stop": 0, "since": None, "seq": 0}
_cache: dict = {"key": None, "doc": None}
#: Announcements and walks started from HTTP handlers run on as tasks; held so
#: they are not garbage-collected mid-way.
_tasks: set[asyncio.Task] = set()


def reset() -> None:
    STATE.update(scene=None, stop=0, since=None, seq=0)
    _cache.update(key=None, doc=None)
    event_walk.reset()


# ---- the files ---------------------------------------------------------------------

def _path() -> Path | None:
    name = (settings.event_file or "").strip()
    if not name:
        return None
    path = Path(name)
    return path if path.is_absolute() else ROOT / path


def spots_path() -> Path | None:
    path = _path()
    return None if path is None else path.with_name(path.stem + ".spots.json")


def _stat(path: Path | None):
    try:
        st = path.stat() if path is not None else None
    except OSError:
        return None
    return None if st is None else (st.st_mtime_ns, st.st_size)


def _read_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("event file %s could not be read", path)
        return None
    return doc if isinstance(doc, dict) else None


def load() -> dict | None:
    """The event, with the robot's recorded spots merged in. None = no event
    file (every machine but the event robot): the event profile is then the
    sales host and the staff page says there is nothing to run."""
    path, saved_path = _path(), spots_path()
    key = (str(path), _stat(path), _stat(saved_path))
    if _cache["key"] == key:
        return _cache["doc"]
    doc = _read_json(path)
    if doc is not None:
        doc = _normalise(doc, _read_json(saved_path) or {})
    _cache.update(key=key, doc=doc)
    return doc


def _pose(value) -> list | None:
    """[x, y, heading|None] with numbers, or None."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    heading = value[2] if len(value) > 2 else None
    try:
        heading = None if heading is None else float(heading)
    except (TypeError, ValueError):
        heading = None
    return [x, y, heading]


def _normalise(doc: dict, saved: dict) -> dict:
    spots: dict[str, dict] = {}
    recorded = saved.get("spots") if isinstance(saved.get("spots"), dict) else {}
    for sid, raw in (doc.get("spots") or {}).items():
        if not isinstance(raw, dict):
            continue
        spot = dict(raw)
        spot["id"] = sid
        spot["name_th"] = str(raw.get("name_th") or sid)
        spot["pose"] = _pose(raw.get("pose"))
        mine = recorded.get(sid) if isinstance(recorded.get(sid), dict) else {}
        if _pose(mine.get("pose")) is not None:
            # measured at the robot wins over the file's: that is what staff saw
            spot["pose"] = _pose(mine.get("pose"))
            spot["steps"] = None
            spot["recorded_at"] = mine.get("recorded_at")
        spot["arrivals"] = int(mine.get("arrivals") or 0)
        spots[sid] = spot
    scenes = []
    seen = set()
    for raw in doc.get("scenes") or []:
        if not isinstance(raw, dict) or not raw.get("id") or raw["id"] in seen:
            continue
        seen.add(raw["id"])
        scene = dict(raw)
        scene["title_th"] = str(raw.get("title_th") or raw["id"])
        stops = [s for s in (raw.get("stops") or []) if s in spots]
        scene["stops"] = stops
        scene["spot"] = raw.get("spot") if raw.get("spot") in spots else None
        scenes.append(scene)
    return {"event": doc.get("event") if isinstance(doc.get("event"), dict) else {},
            "route_file": doc.get("route_file"), "spots": spots, "scenes": scenes}


def _save_recorded(update) -> None:
    """Read-modify-write the robot's spots file (one writer: this server)."""
    path = spots_path()
    if path is None:
        raise RuntimeError("no EVENT_FILE")
    saved = _read_json(path) or {}
    spots = saved.get("spots") if isinstance(saved.get("spots"), dict) else {}
    update(spots)
    saved = {"about": "written by the event staff page on the robot (app/event_host.py) -- "
                      "measured spots and their clean arrivals", "spots": spots}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    _cache.update(key=None)


def dock_doc(doc: dict) -> dict | None:
    """floor1.json's dock (pose + approach), which board_walk's dock needs."""
    name = doc.get("route_file")
    if not name:
        return None
    path = Path(name)
    route = _read_json(path if path.is_absolute() else ROOT / path)
    dock = (route or {}).get("dock")
    return dock if isinstance(dock, dict) else None


def spot_steps(spot: dict | None) -> list[dict] | None:
    """board_walk steps from wherever the robot is to this spot, or None
    when the spot cannot be walked to (not recorded yet). The undock is a
    no-op off the dock (board_walk.Walker.undock)."""
    if not spot:
        return None
    if spot.get("dock"):
        return [{"do": "dock"}]
    if spot.get("steps"):
        return [{"do": "undock"}] + [dict(s) for s in spot["steps"]]
    pose = spot.get("pose")
    if pose is None:
        return None
    step = {"to": [pose[0], pose[1]], "speed": float(spot.get("speed") or DEFAULT_SPEED),
            "label": spot.get("name_th")}
    if pose[2] is not None:
        step["face"] = pose[2]
    return [{"do": "undock"}, step]


# ---- the scene ---------------------------------------------------------------------

def scene(scene_id) -> dict | None:
    doc = load()
    return next((s for s in (doc or {}).get("scenes", []) if s["id"] == scene_id), None)


def current() -> dict | None:
    return scene(STATE["scene"]) if STATE["scene"] else None


def _here(sc: dict | None) -> str | None:
    """The spot the scene is about right now: its own, or its current stop."""
    if sc is None:
        return None
    if sc.get("stops"):
        i = min(STATE["stop"], len(sc["stops"]) - 1)
        return sc["stops"][i]
    return sc.get("spot")


def _spot(spot_id) -> dict | None:
    return ((load() or {}).get("spots") or {}).get(spot_id)


def scene_note() -> str | None:
    """Beside every tool result in an event session: the scene staff chose."""
    sc = current()
    if sc is None:
        return None
    note = "ฉากตอนนี้ (ทีมงานเลือก): %s — %s" % (sc["title_th"], sc.get("brief_th") or "")
    if sc.get("stops"):
        spot = _spot(_here(sc))
        if spot is not None:
            note += " · ตอนนี้อยู่ที่ %s" % spot["name_th"]
    return note.strip()


def event_block() -> str:
    """The event profile's block in the system prompt. Facts about the event
    are the file's own lines; none = hand over to the staff, never invent."""
    doc = load() or {}
    ev = doc.get("event") or {}
    name = ("งาน " + ev["name_th"]) if ev.get("name_th") else "งานอีเวนต์ของบริษัท"
    venue = ev.get("venue_th") or ""
    dates = ", ".join(str(d) for d in (ev.get("dates") or []) if d)
    facts = [str(f).strip() for f in (ev.get("facts_th") or []) if str(f).strip()]
    lines = [
        "[งานอีเวนต์ — โหมดเจ้าบ้านของงาน]",
        "วันนี้คุณเป็นเจ้าบ้านของ%s%s%s ต้อนรับแขกอย่างอบอุ่นและเป็นกันเอง ยังตอบเรื่องโครงการได้ตามกฎข้างบนทุกข้อ "
        "และเรื่องของงานนี้ (การต้อนรับ ทักทาย ขอบคุณที่มาร่วมงาน) อยู่ในขอบเขตด้วย ไม่ใช่เรื่องอื่น"
        % (name, (" ที่" + venue) if venue else "", (" (" + dates + ")") if dates else ""),
        "ทีมงานเป็นคนเลือกฉาก (เช่น รับแขก พาแขกไปจุดต่างๆ ทักทายตามโต๊ะ) ข้อความที่ขึ้นต้นด้วย [ทีมงาน...] หรือ "
        "[หุ่นเดินถึง...] มาจากระบบ ไม่ใช่คำพูดของแขก ให้ทำตามทันทีโดยไม่ต้องทวนคำสั่ง และพูดเป็นภาษาของบทสนทนา",
        "ฉากที่เลือกอยู่จะติดมากับผลของเครื่องมือทุกครั้ง (event_scene) ให้ตอบให้เข้ากับฉากนั้น",
        "การเดินของหุ่นทีมงานเป็นคนสั่งเท่านั้น ถ้าแขกขอให้พาไปที่ไหน ให้บอกว่าจะให้ทีมงานช่วยพาไป หรือชี้ทางให้ "
        "ห้ามบอกว่ากำลังเดินไปเอง",
    ]
    if facts:
        lines.append("ข้อมูลงานที่พูดได้ (พูดได้เฉพาะเท่านี้):")
        lines += ["- " + f for f in facts]
    lines.append("เรื่องของงานที่ไม่อยู่ในข้อมูลข้างบน (กำหนดการ เวลา ของที่ระลึก ที่จอดรถของงาน) ห้ามแต่ง "
                 "ให้บอกอย่างเป็นธรรมชาติว่าทีมงานที่จุดลงทะเบียนช่วยดูให้ได้")
    return "\n".join(lines)


# ---- what staff do -----------------------------------------------------------------

def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _say(text: str, source: str, still) -> None:
    from app import events

    try:
        await events.announce(text, source=source, still_relevant=still, summon=True)
    except Exception:                                    # noqa: BLE001
        logger.exception("event announcement %s failed", source)


def _system(tag: str, body: str) -> str:
    return "[%s — ข้อความระบบ ไม่ใช่คำพูดแขก] %s" % (tag, body)


def _still(seq: int):
    return lambda: STATE["seq"] == seq


#: Said when the scene is acted where she stands (no spot to go to that can
#: be walked to, or walking off): the go_to_place rule ("ห้ามบอกว่ากำลังเดินไป").
STANDING = " (หุ่นยืนอยู่กับที่ ห้ามบอกว่ากำลังเดินไป)"


def scene_text(sc: dict, *, walking: bool, spot_id: str | None) -> str | None:
    """What Emma is told when staff switch to `sc` (scripts/eval_live_qa.py
    sends the same text, `server:scene:<id>`)."""
    if walking:
        text = sc.get("on_start_th")
        if text:
            text += " ห้ามบอกว่าถึงแล้ว"
    else:
        # nowhere to go, or the walk refused (off, not recorded, ...): the
        # scene is acted where she stands -- and she must not claim to go
        text = sc.get("on_arrive_th") or sc.get("on_start_th")
        if text and spot_id is not None:
            text += STANDING
    if not text:
        return None
    return _system("ทีมงานเปลี่ยนฉากเป็น '%s'" % sc["title_th"], (sc.get("brief_th") or "") + " ตอนนี้: " + text)


def arrival_text(sc: dict, spot_name: str) -> str | None:
    """What Emma is told when a scene's walk arrives (`server:arrive:<id>`)."""
    text = sc.get("on_arrive_th")
    return _system("หุ่นเดินถึง%sแล้ว" % spot_name, text) if text else None


def stop_text(sc: dict, spot_name: str) -> str | None:
    """A stop of the scene (the next table) acted where she stands."""
    text = sc.get("on_arrive_th")
    return _system("ทีมงานให้ทักทายที่%s" % spot_name, text + STANDING) if text else None


async def _walk_to(sc: dict, spot_id: str | None, seq: int) -> dict | None:
    """Start the walk for a scene's spot. None when the scene has nowhere to go."""
    if spot_id is None:
        return None
    doc = load() or {}
    spot = (doc.get("spots") or {}).get(spot_id)
    steps = spot_steps(spot)

    async def arrived(run) -> None:
        if run.state != "arrived":
            return
        _count_arrival(run.spot)
        if STATE["seq"] != seq:
            return
        text = arrival_text(sc, spot["name_th"])
        if text:
            await _say(text, "event_arrived", _still(seq))

    return await event_walk.start(spot_id, spot["name_th"] if spot else spot_id, steps,
                                  dock=dock_doc(doc), on_end=arrived)


def _count_arrival(spot_id: str) -> None:
    def bump(spots):
        mine = spots.setdefault(spot_id, {})
        mine["arrivals"] = int(mine.get("arrivals") or 0) + 1
    try:
        _save_recorded(bump)
    except Exception:                                    # noqa: BLE001 -- a counter is not worth a failure
        logger.exception("could not count the arrival at %s", spot_id)


async def start_scene(scene_id: str, *, stop_index: int = 0) -> dict:
    """Staff picked a scene (or a stop of it). Walks if the scene has a spot
    and walking is on; Emma says the scene's opening line either way."""
    sc = scene(scene_id)
    if sc is None:
        return {"ok": False, "error": "no_scene", "error_th": "ไม่พบฉากนี้"}
    await event_walk.stop("replaced")                    # one scene, one walk
    STATE["seq"] += 1
    seq = STATE["seq"]
    STATE.update(scene=scene_id, since=time.time(),
                 stop=max(0, min(stop_index, len(sc["stops"]) - 1)) if sc["stops"] else 0)
    spot_id = _here(sc)
    walk = await _walk_to(sc, spot_id, seq)
    walking = bool(walk and walk.get("ok"))
    turnlog.record("event_scene", scene=scene_id, stop=STATE["stop"], spot=spot_id,
                   walking=walking, refused=None if walk is None or walking else walk.get("error"))
    text = scene_text(sc, walking=walking, spot_id=spot_id)
    if text:
        _spawn(_say(text, "event_scene", _still(seq)))
    return {"ok": True, "scene": scene_id, "stop": STATE["stop"], "walk": walk}


async def next_stop() -> dict:
    sc = current()
    if sc is None or not sc.get("stops"):
        return {"ok": False, "error": "no_stops", "error_th": "ฉากนี้ไม่มีจุดถัดไป"}
    nxt = STATE["stop"] + 1
    if nxt >= len(sc["stops"]):
        return {"ok": False, "error": "last_stop", "error_th": "นี่คือจุดสุดท้ายของฉากนี้แล้ว"}
    return await go_stop(nxt)


async def go_stop(index: int) -> dict:
    sc = current()
    if sc is None or not sc.get("stops") or not 0 <= index < len(sc["stops"]):
        return {"ok": False, "error": "no_stops", "error_th": "ฉากนี้ไม่มีจุดนี้"}
    await event_walk.stop("replaced")
    STATE["seq"] += 1
    seq = STATE["seq"]
    STATE["stop"] = index
    spot_id = sc["stops"][index]
    walk = await _walk_to(sc, spot_id, seq)
    turnlog.record("event_scene", scene=sc["id"], stop=index, spot=spot_id,
                   walking=bool(walk and walk.get("ok")))
    if not (walk and walk.get("ok")):
        spot = _spot(spot_id)
        text = stop_text(sc, spot["name_th"] if spot else spot_id)
        if text:
            _spawn(_say(text, "event_scene", _still(seq)))
    return {"ok": True, "scene": sc["id"], "stop": index, "walk": walk}


async def go_spot(spot_id: str) -> dict:
    """Walk only (the staff page's spot list): no scene, nothing said."""
    doc = load() or {}
    spot = (doc.get("spots") or {}).get(spot_id)
    if spot is None:
        return {"ok": False, "error": "no_spot", "error_th": "ไม่พบจุดนี้"}
    await event_walk.stop("replaced")
    STATE["seq"] += 1
    walk = await event_walk.start(spot_id, spot["name_th"], spot_steps(spot), dock=dock_doc(doc),
                                  on_end=_count_only)
    return {"ok": bool(walk.get("ok")), "walk": walk, **({} if walk.get("ok") else
                                                          {"error": walk.get("error"),
                                                           "error_th": walk.get("error_th")})}


async def _count_only(run) -> None:
    if run.state == "arrived":
        _count_arrival(run.spot)


async def stop() -> dict:
    stopped = await event_walk.stop("staff")
    turnlog.record("event_stop", walking=stopped)
    return {"ok": True, "stopped": stopped}


async def save_spot(spot_id: str) -> dict:
    """The robot's pose now becomes this spot (staff drove it there)."""
    doc = load()
    if doc is None or spot_id not in doc["spots"]:
        return {"ok": False, "error": "no_spot", "error_th": "ไม่พบจุดนี้"}
    if doc["spots"][spot_id].get("dock"):
        return {"ok": False, "error": "dock", "error_th": "แท่นชาร์จใช้ตำแหน่งของแท่นเอง บันทึกทับไม่ได้"}
    if event_walk.walking():
        return {"ok": False, "error": "walking", "error_th": "บันทึกระหว่างหุ่นเดินไม่ได้"}
    try:
        from app import robot_chassis

        if not robot_chassis.configured():
            raise RuntimeError("no chassis")
        pose = await asyncio.wait_for(robot_chassis.pose(), 5.0)
        quality = await asyncio.wait_for(robot_chassis.localization_quality(), 5.0)
        x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
    except Exception:                                    # noqa: BLE001
        logger.exception("event spot %s: could not read the robot's pose", spot_id)
        return {"ok": False, "error": "no_pose", "error_th": "อ่านตำแหน่งหุ่นไม่ได้"}
    if quality is not None and quality < 50:
        # board_walk refuses to start below this (MIN_QUALITY): a spot saved
        # from a lost fix would be a place nobody can walk back to
        return {"ok": False, "error": "lost", "error_th": "หุ่นยังหาตำแหน่งตัวเองไม่ได้ (Q%d)" % quality}
    import math

    entry = {"pose": [round(x, 3), round(y, 3), round(math.degrees(yaw), 1)],
             "recorded_at": time.strftime("%Y-%m-%d %H:%M"), "quality": quality, "arrivals": 0}

    def put(spots):
        spots[spot_id] = entry
    _save_recorded(put)
    turnlog.record("event_spot_saved", spot=spot_id, quality=quality)
    return {"ok": True, "spot": spot_id, "pose": entry["pose"]}


# ---- the staff page ----------------------------------------------------------------

def snapshot() -> dict:
    """Everything the phone page shows. Never a guest's words."""
    doc = load()
    walk = event_walk.snapshot()
    face = _face_state()
    if doc is None:
        return {"ok": True, "event": None, "walk": walk, "face": face}
    spots = []
    for sid, spot in doc["spots"].items():
        spots.append({"id": sid, "name_th": spot["name_th"], "dock": bool(spot.get("dock")),
                      "walkable": spot_steps(spot) is not None, "pose": spot.get("pose"),
                      "recorded_at": spot.get("recorded_at"), "arrivals": spot.get("arrivals", 0)})
    scenes = []
    for sc in doc["scenes"]:
        scenes.append({"id": sc["id"], "title_th": sc["title_th"], "brief_th": sc.get("brief_th") or "",
                       "spot": sc.get("spot"), "stops": sc.get("stops") or []})
    ev = doc.get("event") or {}
    return {"ok": True, "event": {"name_th": ev.get("name_th"), "dates": ev.get("dates") or []},
            "scenes": scenes, "spots": spots,
            "current": {"scene": STATE["scene"], "stop": STATE["stop"], "since": STATE["since"]},
            "walk": walk, "face": face}


def _face_state() -> dict:
    out = {"connected": False, "event_call": False}
    try:
        from app import robot_face_link
        from app import session as session_module

        out["connected"] = bool(robot_face_link.live_faces())
        active = session_module._active
        out["event_call"] = bool(active is not None and getattr(active, "profile", None) == "event")
    except Exception:                                    # noqa: BLE001
        pass
    return out
