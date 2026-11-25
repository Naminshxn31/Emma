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

Two modes of the day (owner 5 Nov 2026: "ช่วงเปิดพิธีจะฟังเสียงคนเดียว และก็เปิดคลิป
เปิดทีวี พูดออกมา อาจจะมีซีนก่อน ส่วนตอนปกติค่อยปรับโหมดตอนพิธีเปิดจบ ส่วนการทักทายหลัง
จากเปิดอยากให้เวลาหุ่นเจอหน้าคนแล้วจำได้ หุ่นก็จะเดินเข้าไปหาแล้วทักทายเลย"). Since 9 Nov
the opening ceremony is an APP of its own (owner: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน"):
"Emma Ceremony" on the robot = the ceremony mode (app/ceremony.py, its script and running
order in CEREMONY_FILE), and this file's scenes are the Emma Event app's only -- none of them
is a ceremony any more (a `ceremony`/`replies` a file still carries is ignored, logged).
`ceremony_now()` and the probes beside it stay here because every caller asks them here:
whoever speaks in the ceremony speaks to the room, not to her (a reply needs her name, or
with "replies": "cues" nothing but the lines staff press is said at all); the camera greets
nobody (greeter), she asks nothing (session), the call stays up through a clip. While the
ceremony app is on, nothing of the event host runs: no scene announcement or arrival line
(`_say`), no roam, no walking up to guests, and the Event page's presses that drive her are
refused (`CEREMONY_ERROR`). (Listening to one enrolled voice there was built and taken out on
5 Nov: it refused 0 strangers on the robot and dropped 11 short phrases of the person on file.)
`tv` and `clip` put the screen and a film up (app/event_clips.py); a scene marked `approach`
walks up to guests the camera knows (app/face_approach.py, which still has all its own gates).

Staff move the robot; a voice never does -- the event profile does not see the
walking tools at all (registry), and app/event_walk.py has its own stops.
The model picks nothing here: staff pick the scene, the file says the words'
gist, and the facts she may state about the event are the file's lines.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path

from app import ceremony, event_pageant, event_walk, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.event_host")

ROOT = Path(__file__).resolve().parent.parent
#: board_walk's max moving speed for a spot reached by one MoveTo, when the
#: spot names none: the reception tour's 0.35 less a little -- people stand
#: around a host, and a guest walks beside her.
DEFAULT_SPEED = 0.3
#: `forward_m` in a scene (owner 10 Nov, the debut pressed on floor 2: "กดแล้วหุ่นไม่เห็นเดินไป
#: ด้านหน้าเลย"): while its spot is not recorded, the walk is this far straight ahead from where she
#: stands (board_walk `forward`: no map, the lidar strip ahead). The walk's own spot id and name --
#: it is not an arrival at the scene's spot (nothing counted, nothing to resume: event_walk).
FORWARD_SPOT = "forward"
FORWARD_NAME_TH = "ด้านหน้า"
FORWARD_MAX_M = 2.0
FORWARD_SPEED = 0.15
#: A pageant scene's walk ahead also stands still for things on the robot's right (board_walk
#: `right_clear_m`): the arm is out to that side (10 Nov's side pose). An estimate past the base's
#: edge -- the elbow and hand reach ~0.5-0.6 m from the centre at shoulder height (not measured).
PAGEANT_RIGHT_CLEAR_M = 0.30

#: Asking a crowd for room once a walk is enough; a person who steps aside and
#: another who steps in comes round again no sooner than this.
HELD_SAY_EVERY_S = 20.0
#: The way round a floor hazard stands this far outside its reach.
WAY_ROUND_MARGIN_M = 0.2
#: Stops of a walk that are not a surprise to anyone standing there: staff chose it,
#: she was told to stop, or another walk took over. Any other stop leaves a robot
#: that said it was going and is not -- she says something.
QUIET_STOPS = ("replaced", "staff", "voice", "desk", "shutdown")

#: Beside every word she is told during a ceremony. It outranks the sales rules that
#: end a turn on a question: into somebody's speech that is an interruption.
CEREMONY_RULE = ("ช่วงพิธีเปิด พูดเฉพาะเท่าที่ถูกขอ สั้นๆ ไม่ถามกลับ ไม่ชวนคุยต่อ "
                 "คนที่ดำเนินพิธีกำลังพูดกับแขกในงาน ไม่ได้พูดกับคุณ เว้นแต่เรียกชื่อคุณ")

#: The live window's note and context, kept where they were read (app/ceremony.py has them).
LIVE_NOTE = ceremony.LIVE_NOTE
LIVE_CONTEXT = ceremony.LIVE_CONTEXT

#: What a press of the Event page that drives her answers while the ceremony app is on the
#: robot (owner 9 Nov: "ไม่งั้นจะปนกัน"). The red stop is never refused; saving a spot is not
#: driving her.
CEREMONY_ERROR = "ceremony_app"
CEREMONY_ERROR_TH = "หุ่นเปิดแอป Emma Ceremony อยู่ — ฉากงานกดได้เมื่อเปิดแอป Emma Event"

#: A scene's `gesture` (owner 9 Nov: "เดินเปิดตัว หุ่นโบกมือทักทายพร้อมพูดประโยค"): what the
#: staff page calls it. Not robot_contracts' label: the owner's word for group 3 is โบกมือ.
GESTURE_TH = {"greet_arm": "โบกมือทักทาย", "wave": "โบกมือ",
              # a scene's `pageant` (owner 9 Nov, app/event_pageant.py), not a body id
              "pageant": "ท่านางงาม (โบกมือ หันซ้าย-ขวา)"}
#: The red stop was pressed while she waited to say the line: no gesture after it.
STOPPED_GESTURE_TH = "กดหยุดไปแล้ว จึงไม่ส่งท่าทาง"
#: Her new reply never began to play: no arm without her voice.
NO_VOICE_TH = "Emma ยังไม่เริ่มพูด จึงไม่ส่งท่าทาง"
#: The card's "เอามือลง" took this press's arm back (MANUAL down_seq): no hand, no fallback gesture.
STAFF_DOWN_TH = "ทีมงานกดเอามือลง จึงไม่ส่งท่าทาง"
#: A `walk_out_of_box` scene not out of the box (owner 23 Nov): its line, no arm.
IN_BOX_TH = "ยังไม่ได้เดินออกจากกล่อง จึงไม่ยกมือ (แขนกางข้างจะโดนกล่อง)"
NOT_STILL_TH = "เดินออกมาแล้วแต่ไม่เห็นว่าล้อหยุดนิ่ง จึงไม่ยกมือ (แขนยังล็อก — ดูหุ่นแล้วกดปลดล็อกเองได้)"
#: A walk out that ended short of its forward_m (something ahead near its end): not out -- no arm, no line.
SHORT_TH = "เดินออกจากกล่องไม่ครบ (%.2f จาก %.2f ม.) — ไม่โบก ไม่พูดประโยค แขนยังล็อก"
#: A walk out pressed over one that stopped short: it would walk another whole forward_m.
UNKNOWN_OUT_TH = "เดินออกจากกล่องไม่ครบ (ไม่รู้ระยะ: ตำแหน่งกระโดด/หยุดกลางทาง) — ไม่โบก ไม่พูดประโยค แขนยังล็อก"
#: The line waits at most this long for the base to be still: WALK_QUIET holds for
#: walk_frames.MOTION_STILL_S (15 s) after the last move.
STILL_WAIT_S = 17.0
#: The gesture waits at most this long for her new reply to be what plays (her last answer
#: may still have this much to go: announce does not wait for it on the robot).
VOICE_WAIT_S = 30.0
#: The arm rises this much before her first word is due: the group takes a moment to move.
VOICE_LEAD_S = 0.3
#: `busy`/`cooldown` from the arm: asked again every ARM_RETRY_EVERY_S for ARM_RETRY_S.
ARM_RETRY_S = 6.0
ARM_RETRY_EVERY_S = 0.5

#: `tv`: what the last screen press asked and how it went (a scene's `tv`, the ceremony
#: order's screen step), for the phones. `perform`: the scene's line (and its gesture) as
#: it went, for the phones -- "did she say it, did the arm go" was nowhere on the page.
STATE: dict = {"scene": None, "stop": 0, "since": None, "seq": 0, "held_said": 0.0, "tv": None, "standing": False,
               "perform": None, "walk_line": None}
_cache: dict = {"key": None, "doc": None}
#: Announcements and walks started from HTTP handlers run on as tasks; held so
#: they are not garbage-collected mid-way.
_tasks: set[asyncio.Task] = set()


def reset() -> None:
    STATE.update(scene=None, stop=0, since=None, seq=0, held_said=0.0, tv=None, perform=None, standing=False,
                 walk_line=None)
    MANUAL.update(saying=False, arm=False, pageant_gen=0, down_seq=None)
    RELEARN.update(phase=None, msg_th=None, at=None, first=None)
    RELEARN_RUN.update(since=None)
    _cache.update(key=None, doc=None)
    event_walk.reset()
    event_pageant.reset()
    from app import event_clips, event_tour

    event_tour.reset()
    event_clips.reset()
    from app import event_roam

    event_roam.reset()


def _scene_path() -> Path:
    return event_walk.RUN_DIR / "scene.json"


def _note_scene() -> None:
    """The scene staff chose survives the server: after Android's reset the page
    used to come back with no scene at all and Emma's tool results lost it."""
    try:
        event_walk.RUN_DIR.mkdir(parents=True, exist_ok=True)
        _scene_path().write_text(json.dumps({k: STATE[k] for k in ("scene", "stop", "since")}), encoding="utf-8")
    except OSError:
        logger.exception("could not note the event scene")


async def recover_after_restart() -> None:
    """At the staff page's start: the scene and the walk a restart cut off."""
    try:
        doc = json.loads(_scene_path().read_text(encoding="utf-8"))
        sc = scene(doc.get("scene"))
        if sc is not None:
            # The scene comes back; walking up to guests does not (a machine must not start
            # walking by itself: app/face_approach.py set_enabled). The ceremony is not a
            # scene since 9 Nov: it comes back with the ceremony app's call, not from here.
            STATE.update(scene=doc["scene"], stop=int(doc.get("stop") or 0), since=doc.get("since"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        logger.exception("could not read the noted event scene")
    from app import event_tour

    event_tour.recover()                                 # card 1's tour a restart cut: said, never carried on
    await event_walk.recover_after_restart()


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


def file_heading(raw_spot) -> float | None:
    """A spot's heading as the event FILE gives it (its pose's, or its last step's `face`) -- before anything
    recorded at the robot."""
    if not isinstance(raw_spot, dict):
        return None
    pose = _pose(raw_spot.get("pose"))
    if pose is not None:
        return pose[2]
    faces = [s.get("face") for s in raw_spot.get("steps") or [] if isinstance(s, dict) and s.get("face") is not None]
    return float(faces[-1]) if faces else None


def follow_spot(steps: list[dict], base_pose, base_file_heading: float | None) -> list[dict]:
    """The steps of a spot that walks on FROM another (`from_spot`), with that spot measured at the robot at
    `base_pose` [x, y, heading]: the first point there, and every turn by the angle the base's heading moved from
    the file's (second review 22 Nov: card 3's absolute 180 deg after a counter recorded at 15-20 deg turned her only
    160-165 deg -- at 1.9 m the guest was 0.5-0.65 m to the side of the strip, she walked past). No heading on
    either side = the turns as they were. Pure: tests/test_event_host.py, scripts/floor_sim.py's --spots."""
    out = [dict(s) for s in steps]
    first = next((i for i, s in enumerate(out) if s.get("to")), None)
    if first is not None:
        out[first]["to"] = [float(base_pose[0]), float(base_pose[1])]
    heading = base_pose[2] if len(base_pose) > 2 else None
    if heading is not None and base_file_heading is not None:
        moved = float(heading) - float(base_file_heading)
        for s in out:
            if s.get("do") == "turn" and s.get("face") is not None:
                s["face"] = round((float(s["face"]) + moved + 180.0) % 360.0 - 180.0, 1)
    return out


def _follows(spot) -> str | None:
    """The spot a spot walks on from (`from_spot`), when it names one by its id."""
    f = spot.get("from_spot") if isinstance(spot, dict) else None
    return f if isinstance(f, str) else None


def _relearnt(raw) -> dict | None:
    """The dock pose the staff page re-learnt at the robot (`relearn_dock`), from the spots file:
    {"pose": [x, y, deg], "recorded_at": ...} -- or None (nothing re-learnt, or not readable)."""
    if not isinstance(raw, dict):
        return None
    pose = _pose(raw.get("pose"))
    if pose is None or pose[2] is None:
        return None
    return {"pose": pose, "recorded_at": raw.get("recorded_at"), "was": raw.get("was"),
            "shift_cm": raw.get("shift_cm"), "fit": raw.get("fit")}


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
        if _pose(mine.get("pose")) is not None and not raw.get("from_spot"):
            # measured at the robot wins over the file's: that is what staff saw
            spot["pose"] = _pose(mine.get("pose"))
            spot["steps"] = None
            spot["recorded_at"] = mine.get("recorded_at")
        spot["arrivals"] = int(mine.get("arrivals") or 0)
        spots[sid] = spot
    # A spot that walks on FROM another (`from_spot`; card 3's guest_front since 22 Nov: from the counter, turned
    # round, straight up to the guest): its first point is that spot's, and the one measured at the robot wins
    # (review 22 Nov: a copy in the file stayed where it was while staff recorded beside_host at the rehearsal --
    # card 3 would have driven back to the old point and walked out from there). Its own steps are never swapped
    # for a recorded pose (save_spot refuses one): the turn and the walk up to the guest ARE the spot. A from_spot
    # that is not a spot to stand at = not walkable (said in the log), never a guess.
    raw_spots = doc.get("spots") or {}
    for sid, spot in spots.items():
        if "from_spot" not in spot:
            continue
        base = spots.get(spot["from_spot"]) if isinstance(spot["from_spot"], str) else None
        steps = [dict(s) for s in spot.get("steps") or []]
        first = next((i for i, s in enumerate(steps) if s.get("to")), None)
        if base is None or base.get("dock") or spot["from_spot"] == sid or first is None:
            logger.warning("spot %s: from_spot %r is not a spot it can start from -- not walked", sid, spot["from_spot"])
            spot["steps"], spot["pose"] = None, None
            continue
        if base.get("recorded_at") and base.get("pose") is not None:
            steps = follow_spot(steps, base["pose"], file_heading(raw_spots.get(spot["from_spot"])))
        spot["steps"] = steps
        spot["pose"] = None
    clips = {cid: {"title_th": str(raw.get("title_th") or cid), "file": raw.get("file"),
                   "seconds": raw.get("seconds"), "after_th": raw.get("after_th")}
             for cid, raw in (doc.get("clips") or {}).items() if isinstance(raw, dict)}
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
        if raw.get("ceremony") or raw.get("replies"):
            # The ceremony is the Emma Ceremony app since 9 Nov (app/ceremony.py, owner "ไม่งั้นจะ
            # ปนกัน"): a scene that says it is one is an ordinary scene of the Event app.
            logger.warning("scene %s is marked as a ceremony: the ceremony is the Emma Ceremony app "
                           "now (CEREMONY_FILE) -- ignored here", raw["id"])
            scene.pop("ceremony", None)
            scene.pop("replies", None)
        scene["approach"] = raw.get("approach") is True
        scene["tv"] = raw.get("tv") if raw.get("tv") in ("open", "close") else None
        scene["clip"] = raw.get("clip") if raw.get("clip") in clips else None
        scene["roam"] = raw.get("roam") if isinstance(raw.get("roam"), dict) else None
        # A gesture staff chose for the scene's line: a body id, never a stop (the page's red
        # button and the E-stop are the stops -- robot_body.staff_request refuses one anyway).
        gesture = raw.get("gesture")
        scene["gesture"] = gesture if _gesture_ok(gesture) else None
        if gesture and scene["gesture"] is None:
            logger.warning("scene %s names a gesture the robot does not have: %r", raw["id"], gesture)
        scene["quick"] = raw.get("quick") is True
        # ท่านางงาม (owner 9 Nov: "เดินแล้วหันหน้าโบกมือไปมา"): the hand waves and the head turns
        # through the scene's walk and its line (app/event_pageant.py). `gesture` stays the
        # fallback: the greeting wave at the spot when the robot refuses the pageant.
        scene["pageant"] = raw.get("pageant") is True
        # A line recorded ahead in a voice the owner picked by ear (owner 10 Nov: the debut's English
        # pageant line, "Jessica v3 รอบแรก (C) ชอบเสียงนี้"): played instead of the model saying it
        # (`_say_line`, EVENT_RECORDED_LINES). Checked again at the press (`recorded_pcm`).
        scene["recorded"] = _recorded_entry(raw)
        # Its line said as the wheels turn, not at the spot (owner 10 Nov, the debut: "รวมทั้งหมด
        # เป็นปุ่มเปิดตัว โดยให้หุ่นเดินไปด้านหน้าพร้อมโบกมือและพูด"): `_on_walk`; the arrival then
        # says nothing again and only brings the walk's wave down after the line (`_callbacks`).
        scene["say_on_walk"] = raw.get("say_on_walk") is True
        # She starts INSIDE a box (owner 23 Nov, the ceremony's opening: "หุ่นอยู่ในกล่องอาจจะต้องให้หุ่นเดินออกมาก่อน จึงค่อย
        # ยกมือนะ"): the scene's walk goes with the arms DOWN (rested before the wheels, event_walk._arm_rested; no
        # pageant on the way, no `right_clear_m` -- the box's side is beside her), and only once it ARRIVED and the
        # wheels stand still (`_wheels_still`) the hand comes up -- the standing pageant, its line after it -- then
        # down. Not out (refused, walking off, cut, still moving): no arm of any kind (`_no_arm`): the pageant's arm
        # swings out sideways into the box. Wins over `say_on_walk` (the 10 Nov walk-and-wave, before the box).
        scene["walk_out_of_box"] = raw.get("walk_out_of_box") is True
        # Card 1's whole opening in one press (owner 23 Nov: the debut, then the counter's handshake, the guests' and
        # back to the counter): what follows the scene's own walk, app/event_tour.py (EVENT_TOUR). Parsed here, planned
        # from what is recorded at each press.
        from app import event_tour

        scene["tour"] = event_tour.parse(raw.get("tour"), spots)
        forward = raw.get("forward_m")
        scene["forward_m"] = (float(forward) if isinstance(forward, (int, float)) and not isinstance(forward, bool)
                              and 0.0 < forward <= FORWARD_MAX_M else None)
        if forward is not None and scene["forward_m"] is None:
            logger.warning("scene %s: forward_m %r is not a distance in (0, %.1f] m -- no walk ahead",
                           raw["id"], forward, FORWARD_MAX_M)
        if raw.get("clip") and scene["clip"] is None:
            logger.warning("scene %s names a clip the event file does not have: %s", raw["id"], raw.get("clip"))
        scenes.append(scene)
    hazards = []
    for raw in doc.get("floor_hazards") or []:
        try:
            hazards.append({"id": str(raw["id"]), "name_th": str(raw.get("name_th") or raw["id"]),
                            "x": float(raw["x"]), "y": float(raw["y"]), "radius_m": float(raw["radius_m"])})
        except (KeyError, TypeError, ValueError):
            logger.warning("a floor hazard in the event file cannot be read: %r", raw)
    no_go = doc.get("no_walk_up_below_y")
    return {"event": doc.get("event") if isinstance(doc.get("event"), dict) else {},
            "route_file": doc.get("route_file"), "spots": spots, "scenes": scenes,
            "dock_relearnt": _relearnt(saved.get("dock")),
            "clips": clips, "screen": doc.get("screen") if isinstance(doc.get("screen"), str) else None,
            "hazards": hazards,
            "no_walk_up_below_y": float(no_go) if isinstance(no_go, (int, float)) and not isinstance(no_go, bool) else None}


#: A say_on_walk scene's arrival waits at most this long for its line, already on its way, to have
#: started before the walk's wave comes down: the line's own waits (her current reply, then the
#: announcement lock -- event_cues VOICE_WAIT_S + LOCK_WAIT_S; review 10 Nov: 20 s was less).
WALK_LINE_WAIT_S = 15.0 + 45.0 + 5.0
#: Where a scene's recorded line lives: the event's own gitignored folder, shipped by deploy for
#: the files the event file names (scripts/robot_server.py event_clip_files).
RECORDED_DIR = "data/events/clips/"
#: The Live API's output rate: what the face plays her voice at (providers' output_sample_rate).
RECORDED_RATE = 24000
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _recorded_entry(raw: dict) -> dict | None:
    """The scene's `recorded`: {"file": data/events/clips/<name>.wav, "sha256": the take the
    owner heard, "text": its words (the caption, the log), "lang": en|th}. Anything else is
    ignored, logged -- she says the line herself."""
    rec = raw.get("recorded")
    if rec is None:
        return None
    file = rec.get("file") if isinstance(rec, dict) else None
    text = rec.get("text") if isinstance(rec, dict) else None
    if not (isinstance(file, str) and file.startswith(RECORDED_DIR) and ".." not in file
            and "/" not in file[len(RECORDED_DIR):] and file.endswith(".wav")
            and isinstance(rec.get("sha256"), str) and _SHA256.fullmatch(rec["sha256"])
            and isinstance(text, str) and text.strip()):
        logger.warning("scene %s: its recorded line is not {file under %s, .wav; sha256; text} -- "
                       "she says the line herself", raw.get("id"), RECORDED_DIR)
        return None
    return {"file": file, "sha256": rec["sha256"], "text": " ".join(text.split()),
            "lang": rec.get("lang") if rec.get("lang") in ("en", "th") else "en"}


def recorded_pcm(sc: dict) -> tuple[bytes | None, str | None]:
    """The scene's recorded line as the PCM the call plays, or (None, why): `off`
    (EVENT_RECORDED_LINES), `missing` (not on this machine -- not deployed), `not_the_take`
    (its bytes are not the pinned ones: a re-render is a take nobody heard, never played --
    pin it after listening), `format` (not 16-bit mono at RECORDED_RATE: it would play at the
    wrong speed). (None, None) = the scene has none. Read at the press: deploy may bring it
    after the server started."""
    import hashlib
    import io
    import wave

    rec = sc.get("recorded")
    if not rec:
        return None, None
    if not settings.event_recorded_lines:
        return None, "off"
    try:
        data = (ROOT / rec["file"]).read_bytes()
    except OSError:
        return None, "missing"
    if hashlib.sha256(data).hexdigest() != rec["sha256"]:
        return None, "not_the_take"
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RECORDED_RATE:
                return None, "format"
            pcm = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return None, "format"
    return (pcm, None) if pcm else (None, "format")


async def _say_recorded(sc: dict, still) -> bool | None:
    """The scene's recorded line through event_cues.play_line (the recorded-cue road: her reply
    first, the lock, the red stop). True = her first word is out; False = it is not to be said
    now (stopped, superseded, the ceremony app, already playing, half out); None = say the line
    the usual way (no recording to play, or the call cannot play one -- the words still go out,
    in her own voice)."""
    from app import event_cues

    pcm, why = await asyncio.to_thread(recorded_pcm, sc)
    if pcm is None:
        if why is not None:
            turnlog.record("event_recorded", scene=sc["id"], ok=False, error=why)
            if why != "off":
                logger.warning("scene %s: its recorded line is not played (%s) -- she says it herself",
                               sc["id"], why)
        return None
    if ceremony_now() or not still():
        return False
    rec = sc["recorded"]
    result = await event_cues.play_line("scene_" + sc["id"], rec["text"], pcm, lang=rec["lang"],
                                        scene_th=sc["title_th"], still=still)
    if result.get("ok"):
        return True
    error = result.get("error")
    turnlog.record("event_recorded", scene=sc["id"], ok=False, error=error)
    if error in ("no_call", "quiet", "no_player", "not_started"):
        return None
    return False


#: The phone's word for a hand that came down (the app's own end, a failure) before her line went out.
HAND_DOWN_TH = "หุ่นเอามือลงก่อนเริ่มพูด"
#: Before the debut's hand where she stands: her answer still playing is waited out this long at most.
HAND_QUIET_WAIT_S = 20.0


async def _her_voice_ends(limit: float, still) -> None:
    """Until her voice now playing (an answer to a guest) has ended -- the session's playback clock
    (`_play_until`, as `_her_voice_begins`) -- `limit` at most, or the press is gone."""
    from app import session as session_module

    deadline = time.monotonic() + limit
    while still() and time.monotonic() < deadline:
        session = session_module._active
        until = float(getattr(session, "_play_until", 0.0) or 0.0) if session is not None else 0.0
        if until <= time.monotonic():
            return
        await asyncio.sleep(0.1)


def _line_playing(sc: dict) -> bool:
    """The scene's recorded line is playing now (event_cues' run of it: a press before's)."""
    from app import event_cues

    return event_cues._running("scene_" + sc["id"])


def _gesture_ok(value) -> bool:
    from app import robot_contracts as rc

    return isinstance(value, str) and value in rc.BODY_ACTION_IDS and value != "stop"


def keep_out_on_line(x0: float, y0: float, x1: float, y1: float) -> bool:
    """Would a walk up to somebody (app/face_approach.py) go past the line the file draws: the
    multimedia room's side of the floor, whatever a person standing in its doorway says. Owner
    5 Nov: "เปิดค้าง แต่เราไม่เข้าทำเป็นปิดเลย". A straight line's lowest point is one of its ends."""
    line = (load() or {}).get("no_walk_up_below_y")
    return line is not None and min(y0, y1) < line


def hazard_on_line(x0: float, y0: float, x1: float, y1: float) -> dict | None:
    """A floor hazard the robot's centre would come within reach of, driving this straight
    line (a point when both ends are one). Things under the laser's plane: neither the lidar
    nor the map knows them (owner 5 Nov, a photo: "โดนตรงนี้ทำให้มีสะดุ้ง"), so the file does."""
    dx, dy = x1 - x0, y1 - y0
    length2 = dx * dx + dy * dy
    for hazard in (load() or {}).get("hazards") or []:
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((hazard["x"] - x0) * dx + (hazard["y"] - y0) * dy) / length2))
        if math.hypot(x0 + t * dx - hazard["x"], y0 + t * dy - hazard["y"]) < hazard["radius_m"]:
            return hazard
    return None


def _way_round(hazard: dict, a: tuple, b: tuple) -> tuple | None:
    """A point to go through so that a -> point -> b keeps off it. The board plans straight when
    nothing is in the way, and for the lidar and its map nothing is. None = no side is clear."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length
    reach = hazard["radius_m"] + WAY_ROUND_MARGIN_M
    best = None
    for side in (1.0, -1.0):
        px, py = hazard["x"] + side * reach * nx, hazard["y"] + side * reach * ny
        if hazard_on_line(ax, ay, px, py) or hazard_on_line(px, py, bx, by):
            continue
        cost = math.hypot(px - ax, py - ay) + math.hypot(bx - px, by - py)
        if best is None or cost < best[0]:
            best = (cost, (round(px, 2), round(py, 2)))
    return None if best is None else best[1]


#: scripts/board_walk.py's own numbers for where its dock step sends the board (PREDOCK_M,
#: BACK_FROM_MAX_M, AXIS_OFFSET_MAX_DEG, APPROACH_SAME_M). That file is a script the walk runs,
#: not a module of the server's: tests/test_event_roam.py holds the two together.
_DOCK_PREDOCK_M, _DOCK_BACK_MAX_M, _DOCK_AXIS_MAX_DEG, _DOCK_APPROACH_SAME_M = 0.45, 2.5, 5.0, 0.6


def dock_way_in(dock: dict | None) -> tuple[float, float] | None:
    """Where board_walk's dock step sends the board FIRST: the dock's approach point -- or, for
    a dock that is come home to facing out (`back_from_m`, 16 Nov), the spot that far out on
    its line when the approach point is that same place (board_walk skips it) or there is
    none. None = nothing to check (no approach point and the old way home), as it was."""
    if not dock:
        return None
    approach = dock.get("approach")
    at = (float(approach[0]), float(approach[1])) if approach else None
    try:
        far = float(dock.get("back_from_m") or 0.0)
        off = float(dock.get("axis_offset_deg") or 0.0)
        x, y, deg = (float(v) for v in dock["pose"])
    except (KeyError, TypeError, ValueError):
        return at
    if not _DOCK_PREDOCK_M < far <= _DOCK_BACK_MAX_M:
        return at
    line = math.radians(deg + (off if 0.0 < abs(off) <= _DOCK_AXIS_MAX_DEG else 0.0))
    spot = (x + far * math.cos(line), y + far * math.sin(line))
    if at is None or math.hypot(at[0] - spot[0], at[1] - spot[1]) < _DOCK_APPROACH_SAME_M:
        return spot
    return at


def keep_off_hazards(steps: list[dict], start: tuple, dock: dict | None = None) -> list[dict]:
    """board_walk steps from `start` with a way round every floor hazard a straight leg would
    cross. A start already inside its reach goes as it was: it is leaving, and any way out crosses."""
    at = (float(start[0]), float(start[1]))
    out: list[dict] = []
    for step in steps:
        target = None
        if step.get("to"):
            target = (float(step["to"][0]), float(step["to"][1]))
        elif step.get("do") == "dock":
            target = dock_way_in(dock)                   # the approach point, as ever, unless the dock says otherwise
        if target is not None:
            hazard = hazard_on_line(*at, *target)
            if hazard is not None and hazard_on_line(*at, *at) is None:
                point = _way_round(hazard, at, target)
                if point is None:
                    logger.warning("no way round %s from (%.2f, %.2f)", hazard["id"], *at)
                else:
                    out.append({"to": list(point), "speed": float(step.get("speed") or DEFAULT_SPEED),
                                "label": "อ้อม" + hazard["name_th"]})
            at = target
        out.append(step)
    return out


#: "At the dock" for the way out: in the dock's own lane -- this far to a side of its line at
#: the most, from on it out to this far short of the approach point. Not a radius round the
#: dock pose (review 17 Nov): stopped 0.7-1.0 m out in the nook (a red stop in the 1.9 m
#: reverse, a way home that failed) the next press went straight, the arms 0.15 m from the
#: corner at 0.8 m out and 0.29 at 1.0 in the simulator, against 0.19 by the approach point.
_DOCK_LANE_HALF_M = 0.7
_DOCK_LANE_SHORT_M = 0.3


def at_the_dock(start: tuple, dock: dict) -> bool:
    """Is `start` in the dock's lane, nearer the dock than its approach point?"""
    try:
        dx, dy, deg = (float(v) for v in dock["pose"])
        ax, ay = float(dock["approach"][0]), float(dock["approach"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    ux, uy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    out = (float(start[0]) - dx) * ux + (float(start[1]) - dy) * uy
    aside = (float(start[0]) - dx) * -uy + (float(start[1]) - dy) * ux
    reach = (ax - dx) * ux + (ay - dy) * uy
    return abs(aside) <= _DOCK_LANE_HALF_M and -0.3 <= out < reach - _DOCK_LANE_SHORT_M


def leave_the_dock(steps: list[dict], start: tuple, dock: dict | None) -> list[dict]:
    """A walk that sets out from the dock goes out through the dock's approach point first,
    when the route file's dock says so (`leave_by_approach`; absent = the steps as they were).

    The way IN has always come along that corridor (28 Oct: sent home from the middle of the
    reception, the board cut past the table at (-7.5, -4.2) and an arm hit it -- board_walk's
    dock()). The way OUT did not: from the pre-dock spot the board plans straight for the
    spot asked. What does not depend on how the board plans (the furniture of 16 Nov,
    scripts/floor_sim.py): the straight centre line from the pre-dock spot to the entrance
    side passes 0.22 m from the corner of that same table (cells x -7.62..-7.52, y -4.82..
    -4.62, not on the board's map) -- inside the base's own 0.30 m half-width; to lobby_east
    0.41 m (0.39 is the arms' half-width); by the approach point the centre line is 1.0 m or
    more from anything after it. How near the arms then come is the simulator's assumed
    planner margin, not a measurement (0.04 m at the least with 0.36 m, at about (-8.0, -4.8)
    -- where her centre is, not the table). Not walked on the robot either way."""
    if not dock or not dock.get("leave_by_approach") or not dock.get("approach"):
        return steps
    try:
        ax, ay = float(dock["approach"][0]), float(dock["approach"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return steps
    if not at_the_dock(start, dock):
        return steps
    first = next((i for i, s in enumerate(steps) if s.get("to")), None)
    if first is None:
        return steps
    tx, ty = float(steps[first]["to"][0]), float(steps[first]["to"][1])
    if math.hypot(tx - ax, ty - ay) < _DOCK_APPROACH_SAME_M:
        return steps                                     # it goes there first already
    out = {"to": [ax, ay], "speed": float(steps[first].get("speed") or DEFAULT_SPEED), "label": "ออกจากแท่นทางเดียวกับที่เข้า"}
    return steps[:first] + [out] + steps[first:]


def route_from(steps: list[dict], start: tuple, dock: dict | None) -> list[dict]:
    """The steps as they are walked from `start`: out of the dock by its corridor, round every
    floor hazard. Pure (the event file's hazards aside): scripts/floor_sim.py walks these."""
    return keep_off_hazards(leave_the_dock(steps, start, dock), start, dock)


async def steps_from_pose(steps: list[dict], dock: dict | None) -> list[dict]:
    """`route_from` where the board says she stands. No pose = the steps as they were."""
    try:
        from app import robot_chassis

        if not robot_chassis.configured():
            return steps
        pose = await asyncio.wait_for(robot_chassis.pose(), 3.0)
        return route_from(steps, (float(pose["x"]), float(pose["y"])), dock)
    except Exception:                                    # noqa: BLE001
        logger.exception("the event walk could not read where it starts from")
        return steps


async def steps_from_here(steps: list[dict] | None, doc: dict) -> list[dict] | None:
    """The same steps, from where she stands now. No pose = as they were: the walker's own gate
    refuses a walk from a pose nobody can read."""
    if not steps:
        return steps
    dock = dock_doc(doc)
    if not doc.get("hazards") and not (dock or {}).get("leave_by_approach"):
        return steps
    return await steps_from_pose(steps, dock)


async def walk_steps(spot: dict | None, doc: dict, *, head_on: bool = False) -> list[dict] | None:
    """The steps of a walk to `spot` from where she stands now -- every walk to a spot (a scene's, the card's
    "ไป<จุด>", the ceremony order's, the roam's, card 1's tour): its route (`steps_from_here`) and, with the board's
    pose read, every turn of more than event_tour.TURN_FIRST_DEG made slowly and watched instead of by the board
    (review 23 Nov: "ไปแขก 1" from the counter spun her round at the board's speed, blind behind). No pose = the
    route as before (the walker's own gate refuses a walk from a pose nobody can read). `head_on`: the walk ends in
    the handshake there -- straight in from the spot's head-on point (event_tour.head_on_steps, owner 23 Nov)."""
    from app import event_tour

    steps = spot_steps(spot)
    if not steps:
        return steps
    here = await event_tour._where()
    if here is None:
        return await steps_from_here(event_tour.head_on_steps(spot, steps) if head_on else steps, doc)
    return event_tour.leg_steps(spot, here, dock_doc(doc), head_on=head_on)


#: The robot's spots file is there but does not parse (a power cut mid-write): nothing is written over
#: it -- the next arrival's count used to rewrite it as empty, every recorded spot and the re-learnt
#: dock gone (review 20 Nov). Said on the phone (save_spot, the re-learn, the morning card).
SPOTS_UNREADABLE_TH = ("ไฟล์จุดที่บันทึกบนหุ่นอ่านไม่ได้ (เสียหาย) — ไม่บันทึกทับ เพื่อไม่ให้จุดและแท่นที่วัดไว้หาย "
                       "· โทรหาผู้ดูแล")


class SpotsUnreadable(RuntimeError):
    pass


def spots_file_broken() -> bool:
    path = spots_path()
    return path is not None and path.exists() and _read_json(path) is None


def _save_recorded(update, *, dock: dict | None = None) -> None:
    """Read-modify-write the robot's spots file (one writer: this server). `dock`: the dock pose
    re-learnt at the robot (relearn_dock), kept beside the spots -- a spot save keeps it.
    A file that is there but cannot be read raises SpotsUnreadable (never rewritten as empty);
    the write is atomic: a temp file, fsync'd, then put in its place."""
    path = spots_path()
    if path is None:
        raise RuntimeError("no EVENT_FILE")
    saved = _read_json(path) if path.exists() else {}
    if saved is None:
        raise SpotsUnreadable(str(path))
    spots = saved.get("spots") if isinstance(saved.get("spots"), dict) else {}
    update(spots)
    kept_dock = dock if dock is not None else saved.get("dock")
    saved = {"about": "written by the event staff page on the robot (app/event_host.py) -- "
                      "measured spots and their clean arrivals", "spots": spots}
    if isinstance(kept_dock, dict):
        saved["dock"] = kept_dock
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(saved, ensure_ascii=False, indent=1).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    with contextlib.suppress(OSError, AttributeError):   # the directory entry too (not on Windows)
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    _cache.update(key=None)


#: The route file's dock, kept until the file changes: every press asks for it now (the way
#: out of the dock) and the file is 60 kB of paths read on the server's loop (review 17 Nov).
_dock_cache: dict = {"key": None, "dock": None}


def dock_doc(doc: dict) -> dict | None:
    """floor1.json's dock (pose + approach), which board_walk's dock needs."""
    name = doc.get("route_file")
    if not name:
        return None
    path = Path(name)
    path = path if path.is_absolute() else ROOT / path
    key = (str(path), _stat(path))
    if _dock_cache["key"] != key:
        dock = (_read_json(path) or {}).get("dock")
        _dock_cache.update(key=key, dock=dock if isinstance(dock, dict) else None)
    dock = _dock_cache["dock"]
    if dock is None:
        return None
    dock = dict(dock)
    learnt = doc.get("dock_relearnt")
    if learnt:
        # re-learnt at the robot this setup (owner 19 Nov: the dock is put down again every time):
        # its pose over the route file's -- the approach, the way home and the line's offset stay
        dock["pose"] = list(learnt["pose"])
    return dock


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
    # `via` in the event file: the aisle to come along. Between two far points the board
    # plans its own way and cuts corners into table edges, which the arms reach and the
    # lidar's plane does not (28 Oct: an arm hit the table at (-7.5, -4.2)).
    via = [{"to": [float(p[0]), float(p[1])], "speed": step["speed"], "label": "ทางเดิน"}
           for p in (spot.get("via") or [])]
    return [{"do": "undock"}, *via, step]


# ---- the scene ---------------------------------------------------------------------

def scene(scene_id) -> dict | None:
    doc = load()
    return next((s for s in (doc or {}).get("scenes", []) if s["id"] == scene_id), None)


def current() -> dict | None:
    return scene(STATE["scene"]) if STATE["scene"] else None


def ceremony_now() -> bool:
    """Is the opening ceremony running -- the Emma Ceremony app's call the active one
    (app/ceremony.py, owner 9 Nov)? Asked on every audio frame, every camera frame and every
    reply: reads no file. No clock of its own any more: the app being open is the mode
    (EVENT_CEREMONY_MAX_S was the clock of a ceremony SCENE, which outlived the event in
    scene.json -- an app does not)."""
    return ceremony.on()


# ---- a cue-only ceremony, and its one live window ------------------------------------
# Kept here as the interface every caller (providers, registry, session, the order) asks, and
# tests patch; the state is app/ceremony.py's since 9 Nov. Review of 7 Nov: with her name the
# only key she answered live whenever the host said "เอ็มม่า" -- nearly every line of his --
# AND then said the line staff pressed; her live answer ran tools; applause cut her line. With
# "replies": "cues" (CEREMONY_FILE):
# * the mic goes up as SILENCE (providers/gemini.py and offline.py `send_audio`): nothing to
#   answer, no tool, no barge-in on her line, no reply watchdog;
# * except while the robot moves -- a spoken "หยุด" must reach the stop tools: the audio goes
#   up, and then every reply that is not a server turn is dropped (NoiseReplyGate, why
#   "cue_only") and every tool but the stops refused (registry, error "cue_only");
# * the one live step (s4_recommend) is a window staff open (`live_open`), shut by "ถัดไป",
#   the red stop, the app closing, or its own end (EVENT_LIVE_MAX_S).
# Asked on every audio frame and every reply: reads no file.

def cue_only() -> bool:
    """A cue-only ceremony is running: she speaks only the lines staff press."""
    return ceremony.cue_only()


def cue_only_closed() -> bool:
    """cue_only() with no live window open: every reply that is not a server turn is
    dropped, every tool but a stop refused -- and, the robot still, the mic is deaf."""
    return ceremony.cue_only_closed()


def robot_moving() -> bool:
    """A walk or a gesture under way: when a spoken stop has to be heard. focus's own
    test (the face's walk, the event walk, a gesture) and a base moving with nobody
    reporting it (robot_face_link.server_walking). Never raises: a probe that fails is
    "still" -- and the mic of a cue-only ceremony deaf, as it is meant to be."""
    try:
        from app import focus, robot_face_link

        return bool(focus._robot_moving() or robot_face_link.server_walking())
    except Exception:                                    # noqa: BLE001
        return False


def mic_deaf() -> bool:
    """What the providers' `send_audio` asks per frame: the mic goes up as silence."""
    return cue_only_closed() and not robot_moving()


def live_open(*, cue_id: str, brief_th: str | None, max_s: float | None = None) -> bool:
    """The ceremony's live window (app/ceremony.py live_open). False = the ceremony app is
    not on (nothing opened)."""
    return ceremony.live_open(cue_id=cue_id, brief_th=brief_th, max_s=max_s)


def live_close(why: str) -> None:
    ceremony.live_close(why)


def live_state() -> dict | None:
    return ceremony.live_state()


def live_note() -> str | None:
    """Beside tool results while the live window is open (registry `event_live`)."""
    return ceremony.live_note()


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
    """Beside every tool result in an Emma Event session: the scene staff chose. Nothing of the
    ceremony since 9 Nov (its lines just said, its rule): that is the ceremony app's
    (`ceremony_note`) -- owner "ไม่งั้นจะปนกัน"."""
    sc = current()
    if sc is None:
        return None
    note = "ฉากตอนนี้ (ทีมงานเลือก): %s — %s" % (sc["title_th"], sc.get("brief_th") or "")
    if STATE.get("standing"):
        # acted where she stands (walking off, the spot not recorded, the roam refused): not at its
        # spot, not on the way (review 10 Nov: "ตอนนี้อยู่ที่ โต๊ะ 1" rode on every tool result with
        # walking off, and the rest scene's "กลับไปชาร์จ" brought back "I need to recharge")
        if "on_stand_th" in sc and not sc.get("on_stand_th"):
            return None                                  # nothing to act where she stands
        return (note.strip() + STANDING)
    if sc.get("stops"):
        spot = _spot(_here(sc))
        if spot is not None:
            note += " · ตอนนี้อยู่ที่ %s" % spot["name_th"]
    return note.strip()


def ceremony_note() -> str | None:
    """Beside every tool result in an Emma Ceremony session while the ceremony app is on: the
    ceremony's rule, and a recorded line just said or a picture just shown (app/event_cues.py
    `last_said_note`: ids, never the line) -- how the model learns of them."""
    if not ceremony_now():
        return None
    from app import event_cues

    said = event_cues.last_said_note()
    note = (said + " · " + CEREMONY_RULE) if said else CEREMONY_RULE
    # while her live window is open (24 Nov): what she just said, which source wins, the common area (app/ceremony.py)
    extras = ceremony.live_extras()
    return (note + " · " + extras) if extras else note


def _basics() -> tuple[str, str, str, list[str]]:
    """The event's name, venue, dates and facts as the event file has them -- the event's own
    data, which the ceremony shares (owner 9 Nov: "สามารถใช้ข้อมูลอันเก่าได้")."""
    ev = (load() or {}).get("event") or {}
    name = ("งาน " + ev["name_th"]) if ev.get("name_th") else "งานอีเวนต์ของบริษัท"
    venue = ev.get("venue_th") or ""
    dates = ", ".join(str(d) for d in (ev.get("dates") or []) if d)
    facts = [str(f).strip() for f in (ev.get("facts_th") or []) if str(f).strip()]
    return name, venue, dates, facts


#: The staff-walk rule, in both blocks: a guest's "พาไป..." never reaches the wheels.
STAFF_WALKS = ("การเดินของหุ่นทีมงานเป็นคนสั่งเท่านั้น ถ้าแขกขอให้พาไปที่ไหน ให้บอกว่าจะให้ทีมงานช่วยพาไป หรือชี้ทางให้ "
               "ห้ามบอกว่ากำลังเดินไปเอง")
#: ...and what is not in the file's facts.
NO_EVENT_FACT = ("เรื่องของงานที่ไม่อยู่ในข้อมูลข้างบน (กำหนดการ เวลา ของที่ระลึก ที่จอดรถของงาน) ห้ามแต่ง "
                 "ให้บอกอย่างเป็นธรรมชาติว่าทีมงานที่จุดลงทะเบียนช่วยดูให้ได้")


def event_block() -> str:
    """The event profile's block in the system prompt. Facts about the event
    are the file's own lines; none = hand over to the staff, never invent.
    Nothing of the ceremony since 9 Nov (its script is `ceremony_block`'s)."""
    name, venue, dates, facts = _basics()
    lines = [
        "[งานอีเวนต์ — โหมดเจ้าบ้านของงาน]",
        "วันนี้คุณเป็นเจ้าบ้านของ%s%s%s ต้อนรับแขกอย่างอบอุ่นและเป็นกันเอง ยังตอบเรื่องโครงการได้ตามกฎข้างบนทุกข้อ "
        "และเรื่องของงานนี้ (การต้อนรับ ทักทาย ขอบคุณที่มาร่วมงาน) อยู่ในขอบเขตด้วย ไม่ใช่เรื่องอื่น"
        % (name, (" ที่" + venue) if venue else "", (" (" + dates + ")") if dates else ""),
        "ทีมงานเป็นคนเลือกฉาก (เช่น รับแขก พาแขกไปจุดต่างๆ ทักทายตามโต๊ะ) ข้อความที่ขึ้นต้นด้วย [ทีมงาน...] หรือ "
        "[หุ่นเดินถึง...] มาจากระบบ ไม่ใช่คำพูดของแขก ให้ทำตามทันทีโดยไม่ต้องทวนคำสั่ง และพูดเป็นภาษาของบทสนทนา",
        "ฉากที่เลือกอยู่จะติดมากับผลของเครื่องมือทุกครั้ง (event_scene) ให้ตอบให้เข้ากับฉากนั้น",
        STAFF_WALKS,
    ]
    if facts:
        lines.append("ข้อมูลงานที่พูดได้ (พูดได้เฉพาะเท่านี้):")
        lines += ["- " + f for f in facts]
    lines.append(NO_EVENT_FACT)
    return "\n".join(lines)


def ceremony_block() -> str:
    """The ceremony profile's block (the Emma Ceremony app, app/ceremony.py): the event's own
    name, venue, dates and facts (the event file's, shared), how the ceremony runs
    (CEREMONY_RULE), the staff-walks line, and the script when a switch says so. No scene: the
    scenes are the Emma Event app's (owner 9 Nov: "ไม่งั้นจะปนกัน")."""
    name, venue, dates, facts = _basics()
    lines = [
        "[พิธีเปิดงาน — แอป Emma Ceremony]",
        "ตอนนี้คุณอยู่ในพิธีเปิดของ%s%s%s คนดำเนินพิธีพูดกับแขกในงาน เรื่องโครงการยังตอบได้ตามกฎข้างบนทุกข้อเมื่อถูกขอ"
        % (name, (" ที่" + venue) if venue else "", (" (" + dates + ")") if dates else ""),
        CEREMONY_RULE,
        ceremony.ADHOC_PROMPT_TH,              # her live answers: the ceremony page's "ตอบสด" (owner 23 Nov)
        *ceremony.prompt_rules(),              # which source wins, the common area (owner 24 Nov)
        *([ceremony.follow_prompt_th()] if settings.ceremony_follow_host else []),   # ทั้งพิธีคุยสด (24 Nov)
        "ทีมงานกดลำดับพิธีทีละขั้นจากหน้าคุม ข้อความที่ขึ้นต้นด้วย [คิวบทพิธี...] หรือ [ทีมงาน...] หรือที่บอกว่าเป็นข้อความระบบ "
        "มาจากระบบ ไม่ใช่คำพูดของแขก ให้ทำตามทันทีโดยไม่ต้องทวนคำสั่ง",
        "สิ่งที่เพิ่งเกิดในพิธี (บทที่เพิ่งพูด ช่วงตอบสดที่ทีมงานเปิด) จะติดมากับผลของเครื่องมือ (event_scene, event_live) "
        "ให้ตอบให้เข้ากับช่วงนั้น",
        STAFF_WALKS,
    ]
    if settings.ceremony_english_only:
        # (owner 24 Nov rehearsal "ทำไมยังพูดไทยเอาออกเลย")
        lines.append("Speak English only for the whole ceremony: every answer and every line, even when the host or "
                     "a guest speaks Thai. The Thai subtitle on the screen is added by the system; never speak Thai.")
    if facts:
        lines.append("ข้อมูลงานที่พูดได้ (พูดได้เฉพาะเท่านี้):")
        lines += ["- " + f for f in facts]
    lines.append(NO_EVENT_FACT)
    script = script_block()
    if script:
        lines.append(script)
    return "\n".join(lines)


# ---- the ceremony's script, for the live session -------------------------------------
# Owner 6 Nov 2026, with the launch ceremony's script (CEREMONY_FILE since 9 Nov,
# app/event_script.py): "ส่งให้ Gemini ตอบ หรือให้ตอบจากสคริปต์ อันไหนดีกว่า วัดดู". Staff
# pressing a cue is app/event_cues.py; these are the two ways the LIVE session answers the
# host's script questions when nobody presses anything, each behind its own switch and
# measured against the press by scripts/eval_live_qa.py --suite ceremony:
# * EVENT_SCRIPT_PROMPT (free): the script in the system prompt -- `script_block`.
# * EVENT_SCRIPT_TOOL (tool): `say_script_line(cue)` (app/tools/ceremony.py) -- the model names
#   the question, the words are not its own; the prompt only points at the tool.
# Both off = ceremony_block() without it (tests/test_ceremony_live.py).
#
# In the ceremony block only since 9 Nov: the Emma Ceremony app's call IS the ceremony, so the
# block no longer says which scene it is scoped to, nor does a scene announcement have to say
# when the ceremony began or ended -- and the Event app's calls never see the script at all.

#: The free mode's block. Her lines are quoted in « » on purpose: a sentence quoted in an
#: instruction is the sentence she says word for word (CLAUDE.md, 27 Oct) -- for an approved
#: line that is the point. The lines outrank the sales rules they break by design (the owner's
#: own "มีอะไรให้ฉันช่วยหรือไม่คะ", a question to end s3_facility, CEREMONY_RULE's "ไม่ถามกลับ"):
#: said so, or two instructions fight (CLAUDE.md "ระวังคำสั่งที่ขัดกันเอง"). The host's words
#: are in ' ' so that « » stays hers alone.
SCRIPT_HEAD = "[บทพิธีเปิด]"
SCRIPT_RULE = ("ระหว่างพิธี เมื่อ%(host)s (พิธีกร) พูดตรงหรือใกล้เคียงกับบทไหน (ไม่ต้องตรงคำ) ให้ตอบด้วยประโยคของบทนั้น"
               "ตามตัวอักษรเป็นภาษาไทย ไม่เติม ไม่ตัด ไม่ต้องค้นข้อมูลเพิ่ม ประโยคของบท รวมคำถามและคำเชิญที่อยู่ในบท "
               "ใช้แทนกฎเรื่องความยาวและคำถามท้าย ตัวเลขพูดได้เฉพาะที่อยู่ในบทนี้หรือในข้อมูลโครงการ บทเรียงตามลำดับพิธี")
SCRIPT_LINE = "- %(ask)s → «%(th)s»"
#: The same rule for a file SPOKEN in English (`"speak": "en"`, owner 17 Nov). In English, and
#: it says which way a clash goes: the Thai rule's "เป็นภาษาไทย" round English lines had her
#: translate an approved line into Thai nobody approved (review 17 Nov).
SCRIPT_RULE_EN = ("During the ceremony, when %(host)s (the host) says a line that matches or is close to one "
                  "below (it need not be word for word), answer with that line's sentence in « », in English, "
                  "exactly as written: do not add, cut or translate anything, and do not look anything up. "
                  "The line, with any question or invitation inside it, replaces the rules about length and "
                  "closing questions. The ceremony's lines are English even if the host speaks Thai. Say only "
                  "numbers that are in these lines or in the project data. The lines are in ceremony order.")
SCRIPT_TAIL_EN = ("Anything else %(host)s says that matches no line (talking to guests, thanking the team) is "
                  "not a question to you: do not say a script line in reply.")
#: Follow mode (CEREMONY_FOLLOW_SCRIPT_PROMPT, owner 24 Nov): added after the rule above. The server plays the
#: recorded line itself when it recognises the host's line; her live answer is for what it did not. No sentence of hers
#: quoted (CLAUDE.md 27 Oct).
SCRIPT_FOLLOW_NOTE = ("ระบบเล่นบทที่ตรงกับคำพูดของ%(host)sให้เองเมื่อจับได้ ถ้าคำพูดของเขาไม่ชัดเจนว่าเป็นการนำเข้าสู่บทนั้น "
                      "หรือไม่แน่ใจ ให้ตอบสั้นๆ ด้วยคำของคุณเองแทนการพูดประโยคของบท")
SCRIPT_FOLLOW_NOTE_EN = ("The system itself plays the recorded line when it recognises what %(host)s says. If what he says "
                         "is not clearly the lead-in to one of these lines, or you are not sure, answer briefly in "
                         "your own words instead of saying a script line.")
#: CEREMONY_FOLLOW_CUES=false (owner 24 Nov ~11:40): nothing plays a line for her -- the note says so. No sentence of hers quoted.
SCRIPT_FOLLOW_LIVE_NOTE = ("ข้อนี้ใช้แทนข้อที่ให้พูดตามบทตามตัวอักษรข้างบน: ไม่มีระบบเล่นบทให้ คุณตอบเองทุกครั้งที่%(host)sพูดกับคุณ "
                           "เป็นภาษาอังกฤษ ด้วยคำของคุณเองแบบย่อของบทที่ตรงกับสิ่งที่เขานำเข้ามา "
                           "หนึ่งถึงสามประโยคสั้นๆ ราว 20-35 คำ ประเด็นหลักและตัวเลขของบทนั้นเท่านั้น ไม่ต้องเล่าครบทุกท่อน "
                           "ใช้เฉพาะตัวเลขของบทนั้นหรือข้อมูลโครงการ ห้ามเงียบเมื่อเขาถามหรือส่งมือให้คุณ "
                           "แต่ถ้าเขายังพูดกับแขกหรือเล่าเรื่องยาวอยู่และไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด รอจนเขาถามคุณ")
SCRIPT_FOLLOW_LIVE_NOTE_EN = ("This replaces the rule above about saying a line exactly as written: nothing plays a line for you. "
                              "You answer %(host)s yourself, every time he asks you or hands over to you, in English, in your own "
                              "words: a SHORT form of the script line that matches what he led in with, 1-3 short sentences "
                              "(about 20-35 words), its key facts and figures only, using only that line's figures or the "
                              "project data. Never stay silent when he asks you. While he is speaking to the guests (a speech, "
                              "a joke, a story) and has not asked you, say nothing or the barest acknowledgement and wait; "
                              "if he talks at length and then asks about a script topic, answer only that final question.")
#: Free talk with the script (owner 24 Nov ~13:40, after a rehearsal answer of "what can you do?" came back as "I can move
#: my head and arms..." -- the script was not in her context at all: "ให้มันตรงกับโครงการหรือบทที่เรามีด้วยสิ" / "เราก็สามารถ
#: เอาของสคริปมาเป็นคำถามได้หนิ"). The tail above ("not a question to you") is wrong here: off the script she still answers.
#: ... and it leads him back to the script (owner 24 Nov ~13:50: "พยายามให้เขามาตามบทของเรา"): one short invitation to the
#: next topic, as a question he may ask -- never its answer before he asks (that would be the script line said early).
SCRIPT_FREE_TAIL = ("ถ้า%(host)sถามคุณเรื่องที่ไม่ตรงกับบทไหน ให้ตอบสั้นๆ ด้วยคำของคุณเองจากข้อมูลโครงการ ไม่ต้องยกประโยคของบทมาตอบ "
                    "แล้วชวนเขากลับเข้าพิธีด้วยประโยคสั้นๆ หนึ่งประโยค โดยเสนอหัวข้อหนึ่งในบทที่ยังไม่ได้คุยกันเป็นคำถามให้เขาถาม "
                    "ห้ามตอบเนื้อหาของหัวข้อนั้นเองจนกว่าเขาจะถาม "
                    "ส่วนคำพูดที่เขาพูดกับแขกหรือขอบคุณทีมงาน ไม่ใช่คำถามถึงคุณ")
SCRIPT_FREE_TAIL_EN = ("When %(host)s asks you something that matches no line, answer briefly in your own words from the "
                       "project data; do not recite a script line for it. Then, in one short sentence, invite him back to "
                       "the ceremony: offer one topic of the script not yet talked about, as a question he could ask "
                       "you -- do not give that topic's content until he asks. What he says to the guests or to thank the team is "
                       "not a question to you.")
#: The script shown + the server plays a matched line (CEREMONY_SCRIPT_SHOW_ONLY with CEREMONY_FOLLOW_CUES on, rehearsal
#: 24 Nov ~14:55, owner: "ทำไมพอถามไปมันเหมือนตอบช้าจัง"): the cue is read by the live model (no listened-to recording),
#: and a reading may not land on a turn still in flight (event_cues `_model_busy`, review 7 Nov) -- the press waited for
#: her own held reply to finish generating: the whole line, 6-9 s. Her reply short = it is done in ~2 s; a line the
#: matcher missed still gets the script's facts, briefly. No sentence of hers quoted (CLAUDE.md 27 Oct).
SCRIPT_SHOW_NOTE = ("ข้อนี้ใช้แทนข้อที่ให้พูดตามบทตามตัวอักษรข้างบน: เมื่อคำพูดของ%(host)sตรงกับบทไหน ระบบเล่นบทนั้นให้เองพร้อมภาพ "
                    "คำตอบของคุณเองต่อคำเดียวกันจะไม่มีใครได้ยิน ดังนั้นทุกครั้งให้ตอบสั้น หนึ่งถึงสองประโยคสั้นๆ ราว 15-25 คำ "
                    "เป็นภาษาอังกฤษ ด้วยประเด็นหลักและตัวเลขของบทที่ตรงกับคำถาม ห้ามพูดบททั้งบท ห้ามเงียบเมื่อเขาถามคุณ")
SCRIPT_SHOW_NOTE_EN = ("This replaces the rule above about saying a line exactly as written: when what %(host)s says "
                       "matches a line, the system plays that line for you with its pictures, and your own reply to the "
                       "same words is not heard. So every time, answer SHORT: 1-2 short sentences (about 15-25 words) in "
                       "English with the key facts and figures of the matching line -- never recite a whole line. Never "
                       "stay silent when he asks you.")
SCRIPT_LIVE = "- %(ask)s → ไม่มีประโยคตายตัว ตอบเองตามแนวนี้: %(live)s"
SCRIPT_TAIL = ("คำพูดอื่นของ%(host)sที่ไม่ตรงกับบทไหน (พูดกับแขก ขอบคุณทีมงาน) ไม่ใช่คำถามถึงคุณ "
               "ห้ามพูดประโยคของบทแทน")
#: The tool mode's one line: the cues are in the tool's own description.
SCRIPT_TOOL_LINE = ("เมื่อ%(host)s (พิธีกร) ถามคุณตามบทพิธี (แม้พูดไม่ตรงคำ) ให้เรียก say_script_line "
                    "ด้วยรหัสบทที่ตรงคำถามแทนการตอบเอง แล้วทำตามผลของมัน")


def script_cues() -> dict[str, dict]:
    """id -> cue the live session may be handed, in script order: approved and clean
    (event_script.servable). The ceremony eval serves drafts in its own process only."""
    from app import event_script

    return event_script.servable()


def script_host() -> str:
    """The host's name as the script writes it ("คุณมิกิ")."""
    from app import event_script

    return str((event_script.load() or {}).get("host_th") or "พิธีกร").strip() or "พิธีกร"


def script_ask(c: dict, host: str, cap: int | None = None) -> str:
    """How a cue's question is named to the model: its scene, and the host's words --
    or, for a cue the host says nothing to (s1_greet), the moment as the script writes it.
    `cap`: the host's words cut to this many characters (the tool's description)."""
    scene = ("ฉาก %d · " % c["scene"]) if isinstance(c.get("scene"), int) else ""
    ask = (c.get("host_th") or "").strip()
    if not ask:
        return scene + "(%sไม่ได้พูด)" % host
    if cap is not None and len(ask) > cap:
        ask = ask[:cap].rstrip() + "…"
    if ask.startswith("("):
        return scene + "จังหวะ " + ask                   # a stage direction, not words
    return scene + "%s: '%s'" % (host, ask)


def script_block() -> str | None:
    """What ceremony_block adds for the script; None = nothing (both switches off, or no cue
    may be said). Built when the ceremony app's call opens, for the whole call."""
    # free talk (owner 24 Nov ~12:40) first had no script line in her prompt; the rehearsal at ~13:35 answered the
    # script's own first question generically -> the script is hers again (owner: "เอาของสคริปมาเป็นคำถามได้หนิ"), under
    # the same switch as live first (CEREMONY_FOLLOW_SCRIPT_PROMPT)
    follow_script = bool(settings.ceremony_follow_host and settings.ceremony_follow_script_prompt)
    free = follow_script and ceremony.free_talk()
    if not (settings.event_script_prompt or settings.event_script_tool or follow_script):
        return None
    from app.prompts import _tool_visible

    if settings.event_script_tool and _tool_visible("say_script_line"):
        # the tool carries the cues; the lines a second time here would be the model's to say
        return SCRIPT_TOOL_LINE % {"host": script_host()}
    if not (settings.event_script_prompt or follow_script):
        return None
    every = cues = script_cues()
    own = {}
    then: dict[str, list[str]] = {}                       # free talk: a host cue's id -> the continuations said after it
    if follow_script and not settings.event_script_prompt:
        # follow mode (owner 24 Nov): the cues the server plays by itself after the one before it -- a chained joke's
        # punchline, a continuation -- are not hers to say early (7 Nov free mode answered jokes with a thanks)
        from app import ceremony_follow

        own = ceremony_follow.lines()
        if free:
            # free talk: nothing plays a continuation (CEREMONY_FOLLOW_CUES off) -- its words are part of the answer to the
            # host line before it (S02-L05/L06 after "what can you do?"). Out: a staff line said after a tick (`manual`:
            # "the lights are off" said live would claim what nobody saw done), a joke's punchline, and a joke's set-up
            # the server plays by itself after her topic answer (CEREMONY_JOKES_AUTO)
            setups = set()
            if settings.ceremony_jokes_auto:
                setups = {cue for _, cue in ceremony_follow._joke_topics().values()}
            kept, last = {}, None
            for k, c in cues.items():
                kind = (own.get(k) or {}).get("kind")
                if kind == "continues":
                    if last is not None and c.get("th"):
                        then.setdefault(last, []).append(k)
                    continue
                if kind in ("chain_follow", "manual") or k in setups:
                    last = None
                    continue
                kept[k] = c
                last = k
            cues = kept
        else:
            cues = {k: c for k, c in cues.items() if (own.get(k) or {}).get("kind") not in ("chain_follow", "continues")}
    if not cues:
        return None
    host = script_host()
    from app import event_script

    english = event_script.speak() == "en"
    lines = [SCRIPT_HEAD, (SCRIPT_RULE_EN if english else SCRIPT_RULE) % {"host": host}]
    if follow_script:
        if not settings.ceremony_follow_cues:
            lines.append((SCRIPT_FOLLOW_LIVE_NOTE_EN if english else SCRIPT_FOLLOW_LIVE_NOTE) % {"host": host})
        elif settings.ceremony_script_show_only:
            lines.append((SCRIPT_SHOW_NOTE_EN if english else SCRIPT_SHOW_NOTE) % {"host": host})
            from app import ceremony_stories

            if ceremony_stories.offered():
                lines.append(ceremony_stories.NOTE_EN)
        else:
            lines.append((SCRIPT_FOLLOW_NOTE_EN if english else SCRIPT_FOLLOW_NOTE) % {"host": host})
    for k, c in cues.items():
        ask = script_ask(c, host)
        en_ask = str((own.get(k) or {}).get("host_en") or "").strip() if free else ""
        if en_ask:
            # the host speaks English on the day (host_en, floor1_ceremony.host.json): his words as he will say them
            scene = ("ฉาก %d · " % c["scene"]) if isinstance(c.get("scene"), int) else ""
            ask = scene + "%s: '%s'" % (host, en_ask)
        if c.get("th"):
            # the line she SAYS: the English in an English-spoken file (event_script.spoken)
            said = event_script.spoken(c)["th"]
            for nxt in then.get(k, ()):
                said = said.rstrip() + " " + event_script.spoken(every[nxt])["th"].strip()
            lines.append(SCRIPT_LINE % {"ask": ask, "th": said})
        elif c.get("live_th"):
            lines.append(SCRIPT_LIVE % {"ask": ask, "live": c["live_th"]})
    if free or (follow_script and settings.ceremony_follow_answer_all):
        # (she answers him off the script too -- "not a question to you" would contradict the window's brief)
        lines.append((SCRIPT_FREE_TAIL_EN if english else SCRIPT_FREE_TAIL) % {"host": host})
    else:
        lines.append((SCRIPT_TAIL_EN if english else SCRIPT_TAIL) % {"host": host})
    return "\n".join(lines)


# ---- what staff do -----------------------------------------------------------------

def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


#: What the event host may still say while the Emma Ceremony app is on: the end of a film --
#: the ceremony's own order rolls the event's films (they are shared). Every other line here
#: (a scene, an arrival, a held walk, the roam's) is the Event app's.
CEREMONY_SAYS = ("event_clip_end",)


async def _say(text: str, source: str, still, *, held_language: bool = True) -> bool:
    """events.announce for the event host. True = the text reached the model.
    `held_language=False`: the text fixes its own language (a quoted sentence said word for
    word) -- the 🌐 menu's "answer only in X" after it is two orders in one turn (the ceremony
    cues' rule, events.announce)."""
    from app import events

    if source not in CEREMONY_SAYS:
        # Owner 9 Nov, "ไม่งั้นจะปนกัน": nothing of the event host is said into the ceremony --
        # a scene's walk arriving, the roam going home. Asked again after announce's wait
        # (her voice first): the ceremony app may have opened meanwhile.
        if ceremony_now():
            turnlog.record("event_say_dropped", source=source, why="ceremony_app")
            return False
        inner = still
        still = lambda: not ceremony_now() and bool(inner())   # noqa: E731
    try:
        # the keyword only when it differs: every other line goes out exactly as it always did
        fixed = {} if held_language else {"held_language": False}
        return bool(await events.announce(text, source=source, still_relevant=still, summon=True, **fixed))
    except Exception:                                    # noqa: BLE001
        logger.exception("event announcement %s failed", source)
        return False


def _perform(sc: dict, seq: int, phase: str, **more) -> None:
    """The scene's line as it goes, for the phones (`STATE["perform"]`): walking to say it,
    saying it, done (said or not, the gesture's answer), or cut (the walk never got there).
    Only the current press writes it: a scene staff left must not report over the next one."""
    if STATE["seq"] != seq:
        return
    now = STATE["perform"] if (STATE["perform"] or {}).get("seq") == seq else {}
    STATE["perform"] = {**now, "seq": seq, "scene": sc["id"], "title_th": sc["title_th"],
                        "gesture": sc.get("gesture"), "phase": phase, "at": time.time(), **more}


def _robot_settling() -> bool:
    """The base moved within walk_frames.MOTION_STILL_S (pushed, a joystick, board_walk over adb):
    WALK_QUIET drops her whole reply then (NoiseReplyGate checks it before the server-turn
    exemption) and the arm is refused `navigating` (robot_face_link.server_walking). The event
    host's own walk is not it -- this is asked only where none is on."""
    try:
        from app import robot_face_link, walk_frames

        if walk_frames.base_moving():
            return True
        return bool(settings.walk_quiet and robot_face_link.walk_quiet())
    except Exception:                                    # noqa: BLE001 -- a wait, not a gate
        return False


async def _her_voice_begins(limit: float) -> bool:
    """After a line's text reached the model: wait until her NEW reply is what plays now. True
    then; False when it did not within `limit` (no voice = no arm).

    Review 9 Nov: announce does not wait for her last answer to finish on the robot (the face
    page sends no audio lead -- display.wait_until_heard has nothing to wait on), so the arm
    sent at once rose over the end of her previous answer, or was refused `busy` by its speech
    gestures. The session's playback clock is read instead (`_play_until`, session._note_playback):
    every chunk of the new reply is queued behind what was already queued, so the clock passing
    where the queue ended at the start (`p0`) = the new reply has come, and it plays from
    max(p0, when it was first seen). Only an interruption ever moves the clock BACK (the page
    stopped her voice, a barge-in): then her next audio is an answer to whoever cut in, not this
    line -- False. (Not `_sent_audio_ms`: second review, it is reset at every turn_complete and
    every interruption, so the reply's own end hid it and the wave never came.)"""
    from app import session as session_module

    session = session_module._active
    if session is None:
        return False

    def clock() -> float:
        return float(getattr(session, "_play_until", 0.0) or 0.0)

    p0 = max(clock(), time.monotonic())
    last = clock()
    starts = None
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if session_module._active is not session:
            return False
        now, until = time.monotonic(), clock()
        if until < last - 0.05:
            return False                                 # interrupted: not this line's audio
        last = until
        if starts is None and until > p0 + 0.01:
            starts = max(p0, now)
        if starts is not None and now >= starts - VOICE_LEAD_S:
            return True
        await asyncio.sleep(0.1)
    return False


async def _gesture(action: str, *, retry: bool = True) -> dict:
    """robot_body.staff_request, asked again while the arm answers `busy` or `cooldown` (her speech
    gestures' beat, the rest before a walk that was then refused: free again in moments --
    speech_gestures.RETRY_SOON), or `request_pending` (a beat's request still in flight on the
    link). Never a stop (staff_request refuses one).

    `retry=False` (the "สั่งเอง" card, review 14 Nov round 2): asked once. A card press is a person
    at the phone -- a re-press while her last gesture still plays was retried until that one ended
    and played as a second gesture (a handshake twice), over the app's own cooldown, and a
    "เอามือลง" in between did not stop the retry. "Busy" is said to them instead; they press again."""
    from app import robot_body

    deadline = time.monotonic() + ARM_RETRY_S
    while True:
        try:
            result = await robot_body.staff_request(action)
        except Exception:                                # noqa: BLE001 -- the phone says so
            logger.exception("the scene's gesture %s", action)
            return {"ok": False, "error": "error", "error_th": "ส่งท่าทางไม่ได้"}
        if not retry or result.get("ok") or result.get("error") not in ("busy", "cooldown", "request_pending") \
                or time.monotonic() + ARM_RETRY_EVERY_S > deadline:
            return result
        await asyncio.sleep(ARM_RETRY_EVERY_S)


async def _say_line(text: str, source: str, sc: dict, seq: int, stops_seen: int | None = None, *,
                    on_walk: bool = False) -> None:
    """The scene's own line -- at its start where she stands, on arrival, at a stop -- and the
    scene's `gesture` with it (owner 9 Nov: "เดินเปิดตัว หุ่นโบกมือทักทายพร้อมพูดประโยค").

    In order, each step watched for a person's red stop since the press (`stops_seen`, the
    press's own reading -- review 9 Nov: read here it came after the stop it was for):
    1. the robot standing still (`_robot_settling`, at most STILL_WAIT_S): pushed onto the stage a
       moment ago, her reply would be dropped whole and the phone would say it was sent;
    2. the line (announce). A stop meanwhile drops it (still_relevant);
    3. the arm, once her new reply is what plays (`_her_voice_begins`): with her first words, not
       over the end of her last answer; a line that never went out lifts no arm -- announce's own
       `arm_greeting` rule for the camera's handshake. Never on a walk's start (event_walk rests
       the arm before the wheels; robot_face_link refuses a gesture during a walk). Staff's press:
       robot_body.staff_request, every gate but the heard guard (no caption to check -- the press
       IS the person asking), never a stop; `busy`/`cooldown` asked again for ARM_RETRY_S.
       EXCEPT the pageant (owner 11 Nov, "เหมือนมือจะขึ้นช้ากว่าเสียงที่พูด"): its hand comes up BEFORE
       the line (before step 2) and the line waits for it (event_pageant.hand_up) -- a line that then
       does not go out brings the hand straight down; a start refused before the line is never
       asked again after it (the scene's `gesture` is the fallback).

    `on_walk` (a `say_on_walk` scene's line, said as the wheels turn -- `_on_walk`): no waiting for
    the robot to stand still (step 1: it is meant to be moving), and no arm at all (step 3) -- the
    wave is the walk's own pageant, and it comes down after the walk and the line (`_callbacks`)."""
    if stops_seen is None:
        stops_seen = event_walk.stops()
    current = _still(seq)

    def still() -> bool:
        return current() and event_walk.stops() == stops_seen

    gesture = sc.get("gesture")
    pageant = bool(sc.get("pageant"))

    def pageant_off(why: str) -> None:
        # the line will not carry the pageant: her talking arm may have the arm again, and a hand the
        # walk raised comes down (review 9 Nov: two of these returns left it waving until the 45 s cap)
        if pageant:
            event_pageant.unwant()
            if event_pageant.active(seq):
                _spawn(event_pageant.end(why))

    if not on_walk and _robot_settling():
        _perform(sc, seq, "settling", said=None, gesture_ok=None, gesture_error_th=None)
        deadline = time.monotonic() + STILL_WAIT_S
        while _robot_settling() and still() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
    if not still():
        pageant_off("stopped")
        if current():
            # that button stops what moves AND what was about to be said
            _perform(sc, seq, "stopped", said=False)
            turnlog.record("event_say_dropped", source=source, why="staff_stopped")
        return
    # ท่านางงาม: the hand leads her line (owner 11 Nov, the debut pressed standing: "เหมือนมือจะขึ้นช้ากว่า
    # เสียงที่พูด" -- its start went with her first word, and the raise is 6.9 s of her 7.4 s line).
    # Standing, the start is asked for now, BEFORE the line, and one refused here is not asked again
    # once she speaks (11 Nov 09:36: a re-press found the arm still coming down, `busy` for 6 s, and
    # the hand rose after her line was over and waved 19 s alone) -- the scene's `gesture` after
    # her first words is the fallback. Up (here, on the walk, or kept from the press before), the
    # line waits for it (event_pageant.hand_up).
    # Review 11 Nov: before the start, (a) the call -- the start goes over it, and with the mic held
    # and the call hung up it used to be her line that rang it (event_walk.call_for, the walk's
    # bell); (b) her answer to a guest still playing -- the hand would rise over its end (the 9 Nov
    # case `_her_voice_begins` was written for). On the walk the phone keeps "walking" while the
    # line waits (the arrival waits for a line that has set out while it says so).
    hand = None
    if pageant and not on_walk and not event_pageant.active(seq) and _arm_free(seq):
        _perform(sc, seq, "raising", said=None, gesture="pageant", gesture_ok=None, gesture_error_th=None)
        if event_pageant._session() is None:
            await event_walk.call_for(still, reason="event_scene")
        await _her_voice_ends(HAND_QUIET_WAIT_S, still)
        # the card's "เอามือลง" while the bell rang or her answer played: not raised (MANUAL down_seq)
        hand = await event_pageant.start(seq, still=lambda: still() and _arm_free(seq))
    if pageant and event_pageant.active(seq):
        if not on_walk:
            _perform(sc, seq, "raising", said=None, gesture="pageant", gesture_ok=None, gesture_error_th=None)
        await event_pageant.hand_up(seq, still)
    if not still():
        # stopped (or staff moved on) while the hand came up: no line, and the hand comes down
        pageant_off("stopped")
        if current():
            _perform(sc, seq, "stopped", said=False)
            turnlog.record("event_say_dropped", source=source, why="staff_stopped")
        return
    up_at_line = pageant and event_pageant.active(seq) and not event_pageant.ending()
    hand_kw = {"gesture": "pageant"} if up_at_line else {}
    _perform(sc, seq, "saying", said=None, gesture_ok=None, gesture_error_th=None, **hand_kw)
    if pageant and on_walk and not up_at_line:
        event_pageant.want()                             # the walk's start refused: her talking arm keeps off
    # the line recorded ahead, when the scene has one (owner 10 Nov): its first word is out when
    # this answers True -- no waiting for her voice below
    recorded = await _say_recorded(sc, still) if sc.get("recorded") else None
    if recorded is None:
        fixed = _QUOTED.search(text) is not None         # a quoted sentence: its language is its own
        said = await (_say(text, source, still, held_language=False) if fixed else _say(text, source, still))
    else:
        said = recorded
        if recorded:
            _perform(sc, seq, "saying", said=None, recorded=True, **hand_kw)    # the phones: the file is playing
    if event_walk.stops() != stops_seen:
        pageant_off("stopped")
        # the red stop while she waited to speak: it stopped the arm, and an arm that rises after
        # it is the one thing that button is for (the app's 60 s latch would refuse it -- this
        # does not lean on that). The line itself was dropped unless it had gone out already.
        if gesture:
            turnlog.record("event_gesture", scene=sc["id"], action=gesture, ok=False, error="staff_stopped")
        _perform(sc, seq, "stopped" if not said else "done", said=bool(said),
                 **({"gesture_ok": False, "gesture_error_th": STOPPED_GESTURE_TH} if said and gesture else {}))
        return
    if (recorded is False and up_at_line and current() and not on_walk
            and _line_playing(sc) and event_pageant.active(seq)):
        # pressed again while the press before's line still plays (event_cues.play_line: once it
        # speaks it plays out, `already`): that line is this press's too, and the hand kept for it
        # stays up through it
        turnlog.record("event_gesture", scene=sc["id"], action="pageant", ok=True, error=None)
        _perform(sc, seq, "done", said=True, gesture="pageant", gesture_ok=True, gesture_error_th=None)
        _spawn(event_pageant.end_after_line(seq, standing=not event_pageant.on_a_walk()))
        return
    if not said or not (gesture or pageant) or not current():
        pageant_off("not_said")                          # the walk's wave, with no line to end it
        _perform(sc, seq, "done", said=bool(said))
        return
    if not recorded and not await _her_voice_begins(VOICE_WAIT_S):
        pageant_off("no_voice")
        turnlog.record("event_gesture", scene=sc["id"], action="pageant" if pageant else gesture,
                       ok=False, error="no_voice")
        _perform(sc, seq, "done", said=True, gesture_ok=False, gesture_error_th=NO_VOICE_TH)
        return
    if not still():
        pageant_off("stopped")
        _perform(sc, seq, "done", said=True,
                 **({"gesture_ok": False, "gesture_error_th": STOPPED_GESTURE_TH} if current() else {}))
        return
    if on_walk:
        # the wave is the walk's: up with the wheels (`_on_walk`), down after the walk and the line
        # (`_callbacks`). No arm here -- robot_face_link refuses one while the wheels turn, and a
        # standing pageant start would be one.
        up = pageant and event_pageant.active(seq)
        if pageant and not up:
            event_pageant.unwant()
        _perform(sc, seq, "done", said=True, **({"gesture": "pageant", "gesture_ok": up} if pageant else {}))
        return
    if not _arm_free(seq):
        # staff pressed the card's "เอามือลง" for this press (review 14 Nov, round 2: a hand that came
        # down that way read as one that came down by itself, and the fallback gesture rose after it)
        event_pageant.unwant()
        turnlog.record("event_gesture", scene=sc["id"], action="pageant" if pageant else gesture,
                       ok=False, error="staff_down")
        _perform(sc, seq, "done", said=True, gesture="pageant" if pageant else gesture, gesture_ok=False,
                 gesture_error_th=STAFF_DOWN_TH)
        return
    if pageant:
        # ท่านางงาม: the hand up when her line went out (raised before it, on the walk, or kept from
        # the press before) waves on through it, and comes down after it -- standing not before one
        # turn each way (MIN_STANDING_S). None up then: it is not asked for now that she speaks
        # (the late hand of 11 Nov), the scene's `gesture` is the fallback.
        if up_at_line:
            turnlog.record("event_gesture", scene=sc["id"], action="pageant", ok=True, error=None)
            _perform(sc, seq, "done", said=True, gesture="pageant", gesture_ok=True, gesture_error_th=None)
            _spawn(event_pageant.end_after_line(seq, standing=not event_pageant.on_a_walk()))
            return
        started = hand if hand and not hand.get("ok") else {"ok": False, "error": "ended", "error_th": HAND_DOWN_TH}
        _perform(sc, seq, "saying", said=True, pageant_ok=False, pageant_error_th=started.get("error_th"))
        if not gesture:
            _perform(sc, seq, "done", said=True, gesture="pageant", gesture_ok=False,
                     gesture_error_th=started.get("error_th"))
            return
    result = await _gesture(gesture)
    ok = bool(result.get("ok"))
    turnlog.record("event_gesture", scene=sc["id"], action=gesture, ok=ok, error=result.get("error"))
    _perform(sc, seq, "done", said=True, gesture_ok=ok, gesture_error_th=None if ok else result.get("error_th"))


def _system(tag: str, body: str) -> str:
    return "[%s — ข้อความระบบ ไม่ใช่คำพูดแขก] %s" % (tag, body)


def _still(seq: int):
    return lambda: STATE["seq"] == seq


#: Said when the scene is acted where she stands (no spot to go to that can
#: be walked to, or walking off): the go_to_place rule ("ห้ามบอกว่ากำลังเดินไป").
STANDING = " (หุ่นยืนอยู่กับที่ ห้ามบอกว่ากำลังเดินไป)"
#: Beside a walk's start line: the arrival is a line of its own.
NOT_ARRIVED = "ห้ามบอกว่าถึงแล้ว"


def scene_text(sc: dict, *, walking: bool, spot_id: str | None) -> str | None:
    """What Emma is told when staff switch to `sc` (scripts/eval_live_qa.py
    sends the same text, `server:scene:<id>`)."""
    if walking:
        text = sc.get("on_start_th")
        if text and NOT_ARRIVED not in text:
            # once (10 Nov: a start line that says it already got it twice in a row)
            text += " " + NOT_ARRIVED
    else:
        # nowhere to go, or the walk refused (off, not recorded, ...): the
        # scene is acted where she stands -- and she must not claim to go.
        # `on_stand_th` when the scene has one: its arrival line is not for where she
        # stands (10 Nov, every scene pressed with walking off: "พาแขกไปห้องมัลติมีเดีย" had her
        # say she had reached the room from the far side of the lobby; "กลับแท่นชาร์จ", that
        # she was off to charge). "" = nothing to say there.
        if spot_id is not None and "on_stand_th" in sc:
            text = sc.get("on_stand_th") or None
        else:
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


def walk_line_text(sc: dict) -> str | None:
    """A `say_on_walk` scene's line as the wheels turn: the line she would say at the spot, told it
    is on the way (when its recorded line cannot play, she says it herself)."""
    text = sc.get("on_arrive_th") or sc.get("on_start_th")
    if not text:
        return None
    return _system("หุ่นกำลังเดินออกไปข้างหน้า", text + " " + NOT_ARRIVED)


def clip_end_text(title: str, line: str) -> str:
    """The film ended and the room is looking at her (`after_th` in the file). The ceremony's
    rule rides along only in the ceremony (since 9 Nov the Event app's films are not one)."""
    return _system("คลิป '%s' จบแล้ว" % title, line + (" (" + CEREMONY_RULE + ")" if ceremony_now() else ""))


def held_text() -> str:
    """Something stands in her way and the walk is waiting. No sentence to read out:
    a quoted one is what she says word for word (CLAUDE.md), and she speaks whatever
    language the talk is in."""
    return _system("มีคนหรือของขวางทางหุ่นอยู่", "ขอทางคนที่อยู่ตรงหน้าอย่างสุภาพหนึ่งประโยคสั้นๆ เป็นภาษาของบทสนทนา "
                   "หุ่นจะเดินต่อเองเมื่อทางว่าง ห้ามบอกว่าถึงแล้ว")


def trouble_text(spot_name: str) -> str:
    """The walk she announced ended short of where she said she was going."""
    return _system("หุ่นเดินไม่ถึง%s" % spot_name, "บอกแขกที่อยู่ใกล้ๆ สั้นๆ หนึ่งประโยคว่าขอหยุดตรงนี้สักครู่ "
                   "ทีมงานกำลังดูให้ เป็นภาษาของบทสนทนา ห้ามบอกว่าถึงแล้ว (หุ่นยืนอยู่กับที่ ห้ามบอกว่ากำลังเดินไป)")


def stop_text(sc: dict, spot_name: str) -> str | None:
    """A stop of the scene (the next table) acted where she stands -- walking off, or that stop not
    recorded: she is NOT at it (review 10 Nov: "ทักทายแขกที่โต๊ะนี้" went to her from across the room).
    The scene's `on_stand_th` when it has one, as scene_text."""
    text = (sc.get("on_stand_th") or None) if "on_stand_th" in sc else sc.get("on_arrive_th")
    return _system("ทีมงานเลือก%s แต่หุ่นยืนอยู่ที่เดิม" % spot_name, text + STANDING) if text else None


#: A `walk_out_of_box` scene's hand comes up once the base stands still after its walk arrived: WHEELS_STILL_WINDOWS
#: windows in a row (board poses WHEELS_POSE_GAP_S apart) within WHEELS_STILL_M / WHEELS_STILL_DEG of the window's
#: first pose, waited for WHEELS_STILL_WAIT_S at most. Not confirmed: no arm (the pageant swings the arm out sideways).
#: Measured 23 Nov on the robot (the board's REST pose read ~3.3 times a second through five walks out of the box and
#: 24 other stops; the owner, watching the opening: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม"): the pose FREEZES the
#: moment the wheels stop -- 0.5 mm / 0.03 deg at most over the next 10 s, no jitter from a pose just out of a box the
#: map does not have (review 23 Nov's worry) -- while no two reads 0.3 s apart in motion were within 1 mm / 0.05 deg,
#: and a walk out ends on a creep of 10-14 mm a read (~40 mm/s) and stops from it. Until then: 0.4 s, 3 windows,
#: 2 cm / 1 deg -- still 1.8-2.2 s after the stop (robot 22:42:03 arrived, 22:42:05 out). Now 0.25 s, 2 windows,
#: 5 mm / 0.3 deg: replayed on those logs, never "still" with motion ahead (0 of 1,441 starts while moving; 1 cm /
#: 0.5 deg was, 31 times, at the slow end of a turn), still 0.6-1.0 s after the stop -- and tighter than before:
#: it lets through under 10 mm/s and 0.6 deg/s over its 0.5 s, where the old one let through 16.7 mm/s and 0.83 deg/s.
WHEELS_POSE_GAP_S = 0.25
WHEELS_STILL_WINDOWS = 2
WHEELS_STILL_M = 0.005
WHEELS_STILL_DEG = 0.3
WHEELS_STILL_WAIT_S = 6.0
#: A walk out of the box is out when it walked its whole forward_m but this: board_walk's FORWARD_NEAR_DONE_M (its step
#: ends as done that near when what it asked outruns the pose -- review 25 Nov 02:3x: at 0.03 a walk out 3-5 cm short
#: was done to the walker and "debut_short" to the card). tests/test_debut_forward.py keeps the two together.
WALKED_OUT_TOL_M = 0.05


def _no_arm(sc: dict) -> dict:
    """The scene as said with no arm at all: no pageant, no `gesture` (a `walk_out_of_box` scene not out of the box --
    the pageant's arm swings out sideways, the greeting wave's too)."""
    return dict(sc, pageant=False, gesture=None)


async def _line_no_arm(text: str, source: str, sc: dict, seq: int, stops_seen: int | None, why: str,
                       why_th: str) -> None:
    """A `walk_out_of_box` scene's line with no arm (`_no_arm`), the phone told why the hand did not come up."""
    turnlog.record("event_gesture", scene=sc["id"], action="pageant", ok=False, error=why)
    await _say_line(text, source, _no_arm(sc), seq, stops_seen)
    if STATE["seq"] == seq and (STATE["perform"] or {}).get("phase") == "done":
        _perform(sc, seq, "done", gesture="pageant", gesture_ok=False, gesture_error_th=why_th)


async def _wheels_still(still) -> bool | None:
    """After a walk arrived: True once the board's pose stayed within WHEELS_STILL_M / WHEELS_STILL_DEG of the first
    pose of a run of WHEELS_STILL_WINDOWS windows WHEELS_POSE_GAP_S apart; False = not seen still within
    WHEELS_STILL_WAIT_S, the pose unreadable, or `still()` no longer true; None = no board here (robot_chassis not
    configured: a PC, the tests) -- the walker's arrival is all there is."""
    from app import robot_chassis

    try:
        if not robot_chassis.configured():
            return None
    except Exception:                                    # noqa: BLE001
        return None
    deadline = time.monotonic() + WHEELS_STILL_WAIT_S
    anchor, windows = None, 0
    while still() and time.monotonic() < deadline:
        try:
            pose = await asyncio.wait_for(robot_chassis.pose(), 2.0)
            here = (float(pose["x"]), float(pose["y"]), math.degrees(float(pose["yaw"])))
        except Exception:                                # noqa: BLE001 -- not knowing is not still
            logger.warning("the walk out of the box: the board's pose could not be read -- no wave")
            return False
        if (anchor is not None and math.hypot(here[0] - anchor[0], here[1] - anchor[1]) < WHEELS_STILL_M
                and abs((here[2] - anchor[2] + 180.0) % 360.0 - 180.0) < WHEELS_STILL_DEG):
            windows += 1
            if windows >= WHEELS_STILL_WINDOWS:
                return True
        else:
            anchor, windows = here, 0                    # moved (or the first read): count again from here
        await asyncio.sleep(WHEELS_POSE_GAP_S)
    return False


def _callbacks(sc: dict, spot: dict | None, seq: int):
    """What a scene's walk says on the way: (on_end, on_event)."""
    name = spot["name_th"] if spot else ""

    async def ended(run) -> None:
        try:
            await _ended(run)
        finally:
            if sc.get("tour"):
                # card 1's tour (app/event_tour.py, owner 23 Nov): after this walk -- and, arrived, its line and its
                # wave -- it goes on to the counter (its own task), or ends here
                from app import event_tour

                event_tour.debut_ended(seq, run)

    async def _ended(run) -> None:
        if sc.get("walk_out_of_box"):
            await _out_of_box(run)
            return
        if run.state == "arrived":
            if run.spot != FORWARD_SPOT:                 # a metre ahead is no spot's arrival
                _count_arrival(run.spot)
            if STATE["seq"] == seq:
                if sc.get("say_on_walk") and STATE.get("walk_line") != seq:
                    # its line never set out on the way (review 10 Nov: the pageant's start outran
                    # a short walk -- 1 m is ~7 s, a start may retry 6 s; a resumed walk): the
                    # spot's road, as every other scene -- the line, the standing wave, the greeting
                    text = arrival_text(sc, name)
                    if text:
                        await _say_line(text, "event_arrived", sc, seq)
                    return
                if sc.get("say_on_walk"):
                    # its line set out on the way: the wave comes down once that line is over
                    # (not before it started: a line still waiting its turn is not over)
                    deadline = time.monotonic() + WALK_LINE_WAIT_S
                    while (STATE["seq"] == seq and (STATE["perform"] or {}).get("phase") in ("walking", "saying")
                           and time.monotonic() < deadline):
                        await asyncio.sleep(0.1)
                    if event_pageant.active(seq):
                        await event_pageant.end_after_line(seq, standing=False)
                    return
                text = arrival_text(sc, name)
                if text:
                    await _say_line(text, "event_arrived", sc, seq)
            return
        if event_pageant.active(seq):
            await event_pageant.end("walk_" + run.state)    # the walk is over: so is the wave on it
        if (STATE["perform"] or {}).get("phase") == "walking":
            _perform(sc, seq, "cut", said=False)         # the line (and its wave) waited for here
        # a walker refused at its own gate never left: start() said so and the
        # scene is being acted where she stands
        if not run.saw_started or run.reason in QUIET_STOPS or STATE["seq"] != seq:
            return
        await _say(trouble_text(name), "event_walk_trouble", _still(seq))

    async def _out_of_box(run) -> None:
        """The walk out of the box ended (owner 23 Nov). Out = its whole forward_m walked (`walked_out`, from the
        board's poses) and then the wheels still: the in-the-box latch lets go and the hand comes up -- the standing
        pageant, its line after it. Not seen still: the line, no arm, the latch kept. Not out -- short (board_walk's
        "near enough"), stopped, refused: no arm, no line, the latch kept, how far she went kept (a walk out that set
        out is neither in nor out: app/event_box.py); a walk she set out on says it did not get there, as every one."""
        from app import event_box

        arrived = run.state == "arrived"
        # where it left her, from the walker's own lines (app/event_box.py: no walker started = the arm back; set out =
        # used, how far kept or "not known") -- whoever pressed since: the box is the robot's, not the press's
        kind = event_box.walk_ended(run, sc.get("forward_m"), tol_m=WALKED_OUT_TOL_M)
        out, walked = kind == "full", event_walk.walked_m(run)
        run.out_of_box = out                             # card 1's tour reads it (event_tour.debut_ended)
        mine = STATE["seq"] == seq
        if not out:
            if event_pageant.active(seq):
                await event_pageant.end("walk_" + run.state)
            if not mine:
                return
            short_th = (SHORT_TH % (walked, float(sc.get("forward_m") or 0.0)) if walked is not None
                        else UNKNOWN_OUT_TH)
            if arrived or (STATE["perform"] or {}).get("phase") == "walking":
                _perform(sc, seq, "cut", said=False, gesture="pageant", gesture_ok=False,
                         gesture_error_th=short_th if arrived else IN_BOX_TH)
            turnlog.record("event_box", action="not_out", walked_m=walked, arrived=arrived)
            if not run.saw_started or run.reason in QUIET_STOPS:
                return
            await _say(trouble_text(name), "event_walk_trouble", _still(seq))
            return
        if not mine:
            return                                       # another press since: its own latch, its own walk
        text = arrival_text(sc, name)
        seen = event_walk.stops()                        # a red stop while the wheels are watched counts
        settled = await _wheels_still(lambda: STATE["seq"] == seq and event_walk.stops() == seen)
        if STATE["seq"] != seq:
            return
        # card 1's tour reads it too (event_tour.debut_ended): not seen still = the arm stays locked, no handshakes
        run.box_still = settled is not False and event_walk.stops() == seen
        if run.box_still:
            event_box.outcome("out")                     # out, and still: the arm is hers again
            if text:
                await _say_line(text, "event_arrived", sc, seq, seen)
        elif text:
            await _line_no_arm(text, "event_arrived", sc, seq, seen, "not_still", NOT_STILL_TH)

    async def told(run, what: str) -> None:
        if what != "held" or STATE["seq"] != seq:
            return
        if sc.get("walk_out_of_box"):
            # held on the way out of the box (its door not open all the way): nobody there to ask for the way --
            # the walk ends on its patience (45 s), no wave, no tour; the phone says so
            return
        now = time.monotonic()
        if now - STATE["held_said"] < HELD_SAY_EVERY_S:
            return
        STATE["held_said"] = now
        # the guest may have moved by the time she can speak (announce waits for her
        # own audio): the walk has gone on, and asking then is talking to nobody
        await _say(held_text(), "event_held",
                   lambda: STATE["seq"] == seq and event_walk.current() is run and bool(run.held_at))

    return ended, told


async def _walk_to(sc: dict, spot_id: str | None, seq: int, stops_seen: int | None = None) -> dict | None:
    """Start the walk for a scene's spot. None when the scene has nowhere to go.
    `stops_seen`: event_walk.stops() when the press began -- a person's stop since = no walk
    (review 7 Nov: the red stop in the awaits before `start` used to find nothing to stop)."""
    if spot_id is None:
        return None
    doc = load() or {}
    spot = (doc.get("spots") or {}).get(spot_id)
    if sc.get("walk_out_of_box"):
        # out of the box: ALWAYS its straight walk ahead, never a recorded spot (review 23 Nov: a recorded stage made
        # it a board MoveTo from inside the box); none = nowhere to go
        return await _walk_ahead(sc, seq, stops_seen) if sc.get("forward_m") else None
    if spot_steps(spot) is None and sc.get("forward_m"):
        return await _walk_ahead(sc, seq, stops_seen)
    # a scene that gives the hand there (card 2: Khun Miki's) comes in head-on where the spot asks for it (owner 23 Nov:
    # "เดินให้ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามาจับมือกับคุณมิกิ" -- card 1's counter leg, event_tour.head_on_steps)
    steps = await walk_steps(spot, doc, head_on=sc.get("gesture") == "handshake")
    on_end, on_event = _callbacks(sc, spot, seq)
    return await event_walk.start(spot_id, spot["name_th"] if spot else spot_id, steps,
                                  dock=dock_doc(doc), on_end=on_end, on_event=on_event,
                                  stops_seen=stops_seen)


async def _walk_ahead(sc: dict, seq: int, stops_seen: int | None) -> dict:
    """The scene's `forward_m`: straight ahead from where she stands, its spot not recorded (`ahead_steps`)."""
    doc = load() or {}
    steps = await ahead_steps(sc, doc)
    if isinstance(steps, dict):
        return steps                                     # refused: a floor hazard on the line, no pose
    on_end, on_event = _callbacks(sc, {"name_th": FORWARD_NAME_TH}, seq)
    return await event_walk.start(FORWARD_SPOT, FORWARD_NAME_TH, steps, dock=dock_doc(doc),
                                  on_end=on_end, on_event=on_event, stops_seen=stops_seen)


async def ahead_steps(sc: dict, doc: dict) -> list[dict] | dict:
    """The steps of a scene's `forward_m`, or its refusal ({"ok": False, ...}). A floor hazard on that
    line (a pose read, floor 1's file) = not walked: the lidar does not see it, and the walker has no
    gate of its own behind this one (no map) -- so a pose that cannot be read is no walk either (review
    10 Nov). Walking off = event_walk's own refusal, no pose read first (that was up to 3 s before the
    line on every press). Known and accepted: on another floor the board's pose is that floor's numbers,
    and floor 1's hazard can refuse a walk there."""
    metres = float(sc["forward_m"])

    def refuse(key: str, text: str) -> dict:
        turnlog.record("event_walk", phase="refused", spot=FORWARD_SPOT, reason=key)
        return {"ok": False, "error": key, "error_th": text}

    if settings.event_walk_enabled and doc.get("hazards"):
        hazard = None
        try:
            from app import robot_chassis

            if not robot_chassis.configured():
                return refuse("no_pose", "อ่านตำแหน่งหุ่นไม่ได้ เช็คของบนพื้นข้างหน้าไม่ได้ — ไม่เดิน")
            pose = await asyncio.wait_for(robot_chassis.pose(), 3.0)
            x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
            hazard = hazard_on_line(x, y, x + metres * math.cos(yaw), y + metres * math.sin(yaw))
        except Exception:                                # noqa: BLE001
            logger.exception("the walk ahead could not read where it starts from")
            return refuse("no_pose", "อ่านตำแหน่งหุ่นไม่ได้ เช็คของบนพื้นข้างหน้าไม่ได้ — ไม่เดิน")
        if hazard is not None:
            return refuse("hazard", "ข้างหน้ามี%s ที่เลเซอร์มองไม่เห็น — ไม่เดิน" % hazard["name_th"])
    step = {"forward": metres, "speed": FORWARD_SPEED, "label": "เดินออกไปข้างหน้า"}
    if sc.get("pageant") and not sc.get("walk_out_of_box"):
        # the pageant's arm out to the right on the way; out of the box it is down (owner 23 Nov) -- and the box's side
        # right beside her would hold the walk for good
        step["right_clear_m"] = PAGEANT_RIGHT_CLEAR_M
    if sc.get("walk_out_of_box"):
        step["short_ok"] = False                         # all of it, or not out (board_walk's "near enough" is not)
    return [step]


async def debut_walk(sc: dict) -> dict:
    """The walk of a scene out of the box, for the ceremony's opening (app/event_tour.py `begin_ceremony`):
    {"spot", "name_th", "steps", "dock"} -- always its `forward_m` straight ahead, never a recorded spot -- or
    {"ok": False, "error", "error_th"}. The same walk card 1 of the Event page walks (`_walk_to`)."""
    doc = load() or {}
    # out of the box: ALWAYS the straight walk ahead, never a recorded spot (review 23 Nov)
    if not sc.get("forward_m"):
        return {"ok": False, "error": "no_steps", "error_th": event_walk.REASONS_TH["no_steps"]}
    steps = await ahead_steps(sc, doc)
    if isinstance(steps, dict):
        return steps
    return {"spot": FORWARD_SPOT, "name_th": FORWARD_NAME_TH, "steps": steps, "dock": dock_doc(doc)}


def ceremony_refusal() -> dict:
    """What a press of the Event page that drives her answers while the Emma Ceremony app is on
    the robot (owner 9 Nov: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน")."""
    return {"ok": False, "error": CEREMONY_ERROR, "error_th": CEREMONY_ERROR_TH}


async def resume() -> dict:
    """เดินต่อ: the stopped walk from the step it reached, nothing announced again.
    On arrival she says the scene's line if it is still that scene's spot. Not while the
    ceremony app is on: the Event page's button (the ceremony's walks are its own steps)."""
    if ceremony_now():
        return ceremony_refusal()
    run = event_walk.current()
    sc = current()
    on_end, on_event = _count_only, None
    if run is not None and sc is not None and _here(sc) == run.spot:
        on_end, on_event = _callbacks(sc, _spot(run.spot), STATE["seq"])
    walk = await event_walk.resume_last(on_end=on_end, on_event=on_event)
    if walk.get("ok") and on_end is not _count_only and (STATE["perform"] or {}).get("phase") in ("cut", "stopped"):
        # the walk that was cut goes on: its line (and wave) waits for the arrival again -- the red
        # "never got there" left up read as a fault, and a press of start would stop this walk
        _perform(sc, STATE["seq"], "walking", said=None, gesture_ok=None, gesture_error_th=None)
    return {"ok": bool(walk.get("ok")), "walk": walk,
            **({} if walk.get("ok") else {"error": walk.get("error"), "error_th": walk.get("error_th")})}


async def dock_pose() -> dict:
    """ตั้งตำแหน่งที่แท่น: the recovery from a stale pose after a boot. The dock's pose the lidar
    agrees with, or -- when it does not -- a look round it (board_walk --find-dock-pose, nothing
    written): a dock put down somewhere else this setup (owner 19 Nov: every time) is said as
    such, with the button that re-learns it, not as "maybe not on the dock"."""
    dock = dock_doc(load() or {})
    out = await event_walk.set_dock_pose(dock)
    if out.get("ok") or out.get("error") != "fit" or not dock:
        return out
    look = await event_walk.find_dock_pose(dock, set_it=False)
    word = look.get("found") or {}
    if word.get("ok") and word.get("on_dock") and float(word.get("shift_m") or 0) >= RELEARN_SAME_M:
        return dict(out, relearn=True, error="dock_moved", error_th=DOCK_MOVED_TH % _moved_words(word))
    return out


# ---- re-learning the dock at setup (owner 19 Nov: "ต้องมาตั้งแท่นใหม่ทุกครั้งที่ทำ") ----------------
#
# The dock is put down again at every setup -- that night 11 cm deeper and 9 cm aside of the 16 Nov
# pose, which fit 0.14 -- and finding it took a terminal: a lidar search round the file's pose,
# `--set-dock-pose` there, 0.8 m straight out and `back_to` in, the pose read again on the contacts,
# the file edited by hand. Here it is one button: the same steps, the motion board_walk's own
# `forward` and `back_to` (the debut's walk ahead and the walk card's "กลับแท่น"), two taps like every
# walk. The pose written = halfway between the two lidar fits on the dock (staff's placing, her own
# docking), heading the first: that night's rule (floor1.json's note), and its three docks after
# sat within 3.6 cm of it.

RELEARN_SPOT = "dock_relearn"
RELEARN_NAME_TH = "วัดแท่นใหม่ (ออก 0.8 ม. แล้วถอยกลับ)"
RELEARN_OUT_M = 0.8
#: The two fits on the dock further apart than this are not the same dock: nothing written.
RELEARN_AGREE_M = 0.10
#: Under this from the file's pose the dock has not moved (a fit's noise): "ตรงกับที่บันทึกไว้".
RELEARN_SAME_M = 0.03
#: After the contacts: the base settles and charging shows before the second look.
RELEARN_SETTLE_S = 2.0
DOCK_MOVED_TH = "แท่นไม่ได้อยู่ตรงที่บันทึกไว้ (%s) — กด 'วัดแท่นใหม่' (หุ่นออกไปข้างหน้า 0.8 ม. แล้วถอยกลับเข้าแท่นเอง)"
RELEARN_TH = {
    "not_on_dock": "หุ่นต้องอยู่บนแท่นและชาร์จอยู่ก่อน — เข็นหุ่นเข้าแท่นให้ขั้วแตะ (ไฟชาร์จติด) แล้วกดใหม่",
    "no_board": "อ่านบอร์ดเดินไม่ได้ — รอสักครู่แล้วกดใหม่",
    "not_found": "หาตำแหน่งแท่นด้วยเลเซอร์ไม่เจอ (ตรง %s) — ดูว่าหุ่นอยู่บนแท่นจริง ไม่มีคนยืนล้อมหน้าหุ่น แล้วกดใหม่",
    "edge": "แท่นอยู่ไกลจากที่บันทึกไว้เกิน 30 ซม. — วางแท่นใกล้ที่เดิม หรือโทรหาผู้ดูแล",
    "hazard": "ข้างหน้าแท่น 1 ม. มี%s — ย้ายของออกก่อน",
    "not_docked": "หุ่นถอยกลับแล้วแต่ไม่ได้ชาร์จ — ไม่ได้บันทึกแท่นใหม่ · ดูข้างหลังหุ่น เข็นเข้าแท่นแล้วกด 'วัดแท่นใหม่' อีกครั้ง",
    "disagree": "สองครั้งที่วัดบนแท่นห่างกัน %.0f ซม. — ไม่ได้บันทึก · ดูว่าแท่นถูกชนจนขยับหรือเปล่า แล้วกดใหม่",
    "walk": "การเดินวัดแท่นไม่สำเร็จ — ไม่ได้บันทึกแท่นใหม่",
}
#: The last re-learn, for the phone (phase: checking | walking | measuring | done | failed).
RELEARN: dict = {"phase": None, "msg_th": None, "at": None, "first": None}
#: A re-learn under way (monotonic start), from its press to the end of its look on the contacts.
#: Review 20 Nov, proved: two presses at once both searched with --set, one walked, and the other's
#: "busy" overwrote the phone's line (and RELEARN["first"]) while the robot was out of the dock.
RELEARN_RUN: dict = {"since": None}
#: Past this a re-learn that never ended (a lost callback) no longer blocks the button.
RELEARN_MAX_S = 600.0
RELEARN_BUSY_TH = "กำลังวัดแท่นอยู่แล้ว — รอให้เสร็จ (ดูบรรทัด 'วัดแท่น' บนการ์ดเช้า)"


def _moved_words(word: dict) -> str:
    """board_walk's search, in the words staff read: deeper/out, left/right, turned."""
    along, aside = float(word.get("along_m") or 0.0), float(word.get("aside_m") or 0.0)
    turn = float(word.get("turn_deg") or 0.0)
    bits = ["%s %.0f ซม." % ("ลึกเข้าไป" if along < 0 else "ยื่นออกมา", abs(along) * 100) if abs(along) >= 0.01 else None,
            "เยื้อง%s %.0f ซม." % ("ซ้าย" if aside > 0 else "ขวา", abs(aside) * 100) if abs(aside) >= 0.01 else None,
            "หัน %.1f°" % turn if abs(turn) >= 0.5 else None]
    return " ".join(b for b in bits if b) or "ห่าง %.0f ซม." % (float(word.get("shift_m") or 0.0) * 100)


def _relearn_note(phase: str, msg_th: str | None) -> None:
    RELEARN.update(phase=phase, msg_th=msg_th, at=time.time())
    turnlog.record("event_dock_relearn", phase=phase)


def relearn_view() -> dict:
    """The phone's line for the dock: the last re-learn's phase and words, and whether the dock
    was re-learnt TODAY (a morning check: the dock is put down again at every setup)."""
    learnt = (load() or {}).get("dock_relearnt")
    today = time.strftime("%Y-%m-%d")
    return {"phase": RELEARN["phase"], "msg_th": RELEARN["msg_th"], "at": RELEARN["at"],
            "running": _relearn_running(), "recorded_at": (learnt or {}).get("recorded_at"),
            "today": bool(learnt and str(learnt.get("recorded_at") or "").startswith(today))}


async def _docking_status() -> str | None:
    """The board's dockingStatus ("on_dock", ...), None with no chassis here."""
    from app import robot_chassis

    if not robot_chassis.configured():
        return None
    power = await asyncio.wait_for(robot_chassis.power(), 3.0)
    return power.get("dockingStatus")


def _relearn_running() -> bool:
    since = RELEARN_RUN["since"]
    return since is not None and time.monotonic() - since < RELEARN_MAX_S


async def relearn_dock(*, stops_seen: int | None = None) -> dict:
    """One re-learn at a time (RELEARN_RUN, taken before the first await): a second press is
    refused without a word on the first one's line. Held until the re-learn's own end
    (`_relearn_end`), or given back here when it never walked."""
    if _relearn_running():
        return {"ok": False, "error": "relearn_busy", "error_th": RELEARN_BUSY_TH}
    if ceremony_now():
        return ceremony_refusal()
    if event_walk.walking():
        return {"ok": False, "error": "walking", "error_th": event_walk.REASONS_TH["walking"]}
    if event_walk.pose_busy():
        return {"ok": False, "error": "pose_busy", "error_th": event_walk.REASONS_TH["pose_busy"]}
    RELEARN_RUN["since"] = mine = time.monotonic()
    handed = False
    try:
        out = await _relearn_dock(stops_seen)
        handed = bool(out.get("ok"))
        return out
    finally:
        if not handed and RELEARN_RUN["since"] == mine:
            RELEARN_RUN["since"] = None


async def _relearn_dock(stops_seen: int | None) -> dict:
    """วัดแท่นใหม่: on the dock -> the lidar's best pose near the file's, the board's pose set there
    (A) -> straight out RELEARN_OUT_M and back in along that line (one board_walk walk: `forward`,
    `back_to` A) -> on the contacts the lidar again (B) -> halfway A..B written as the dock
    (`_save_recorded(dock=)`, dock_doc lays it over the route file) and the board's pose set to it.
    The walk's answer is the press's; the rest is said on the phone as it happens (`relearn_view`)."""
    if ceremony_now():
        return ceremony_refusal()
    if event_walk.walking():
        return {"ok": False, "error": "walking", "error_th": event_walk.REASONS_TH["walking"]}
    doc = load() or {}
    dock = dock_doc(doc)
    if not dock:
        return {"ok": False, "error": "no_dock", "error_th": event_walk.REASONS_TH["no_dock"]}

    def refuse(key: str, *args) -> dict:
        msg = RELEARN_TH[key] % args if args else RELEARN_TH[key]
        _relearn_note("failed", msg)
        return {"ok": False, "error": key, "error_th": msg}

    _relearn_note("checking", "กำลังหาแท่นด้วยเลเซอร์")
    try:
        status = await _docking_status()
    except Exception:                                    # noqa: BLE001
        logger.exception("relearn dock: the board's docking status")
        return refuse("no_board")
    if status != "on_dock":
        return refuse("no_board" if status is None else "not_on_dock")
    look = await event_walk.find_dock_pose(dock, set_it=True)
    word = look.get("found") or {}
    if not look.get("ok"):
        _relearn_note("failed", look.get("error_th"))
        return look
    if word.get("edge"):
        return refuse("edge")
    if word.get("ok") and word.get("not_set") == "not_on_dock":
        return refuse("not_on_dock")                     # board_walk read it again before writing
    if word.get("ok") and word.get("not_set"):
        _relearn_note("failed", event_walk.REASONS_TH["busy_board"])
        return {"ok": False, "error": "busy_board", "error_th": event_walk.REASONS_TH["busy_board"]}
    if not word.get("ok") or not word.get("set"):
        return refuse("not_found", "%.2f" % float(word.get("fit") or 0.0))
    ax, ay, adeg = (float(v) for v in word["pose"])
    yaw = math.radians(adeg)
    hazard = hazard_on_line(ax, ay, ax + (RELEARN_OUT_M + 0.2) * math.cos(yaw), ay + (RELEARN_OUT_M + 0.2) * math.sin(yaw))
    if hazard is not None:
        return refuse("hazard", hazard["name_th"])
    RELEARN["first"] = {"pose": [ax, ay, adeg], "fit": word.get("fit"), "file": list(dock["pose"])}
    from app import event_roam

    await event_roam.stop("staff")
    await event_pageant.end("spot")
    STATE["seq"] += 1
    steps = [{"forward": RELEARN_OUT_M, "speed": FORWARD_SPEED, "label": "ออกจากแท่นตรงไปข้างหน้า"},
             {"back_to": [round(ax, 4), round(ay, 4), round(adeg, 2)], "label": "ถอยกลับเข้าแท่น"}]
    moved = (" · " + DOCK_MOVED_TH.split(" — ")[0] % _moved_words(word)) if float(word.get("shift_m") or 0) >= RELEARN_SAME_M else ""
    if ceremony_now():
        # asked again: the checks above awaited the lidar search (review 20 Nov) -- the ceremony
        # app may have come up since, and this walks
        _relearn_note("failed", CEREMONY_ERROR_TH)
        return ceremony_refusal()
    # said before the walk: its end (`_relearn_end`, the walk's own callback) may come first
    _relearn_note("walking", "ตั้งตำแหน่งบนแท่นแล้ว (เลเซอร์ตรง %.2f)%s — กำลังออก 0.8 ม. แล้วถอยกลับ"
                  % (float(word.get("fit") or 0.0), moved))
    walk = await event_walk.start(RELEARN_SPOT, RELEARN_NAME_TH, steps, dock=None, on_end=_relearn_end,
                                  stops_seen=stops_seen)
    if not walk.get("ok"):
        _relearn_note("failed", "%s: %s" % (RELEARN_TH["walk"], walk.get("error_th") or walk.get("error")))
        return {"ok": False, "walk": walk, "error": walk.get("error"), "error_th": walk.get("error_th")}
    return {"ok": True, "walk": walk, "found": word}


async def _relearn_end(run) -> None:
    """The re-learn's walk ended: on the dock, measure again and write -- or say why not. The
    board's pose is held from here (no walk starts while she is measured: review 20 Nov, the
    buttons came back as the wheels stopped) and the re-learn is over at the end, whatever."""
    try:
        with event_walk.pose_hold("relearn"):
            await _relearn_measure(run)
    finally:
        RELEARN_RUN["since"] = None


async def _relearn_measure(run) -> None:
    first = RELEARN.get("first") or {}
    if run.state != "arrived" or not first:
        why = run.view().get("reason_th") or run.reason or run.state
        _relearn_note("failed", "%s (%s)" % (RELEARN_TH["walk"], why))
        return
    _relearn_note("measuring", "ถอยเข้าแท่นแล้ว — กำลังวัดตำแหน่งบนแท่นอีกครั้ง")
    await asyncio.sleep(RELEARN_SETTLE_S)
    try:
        status = await _docking_status()
    except Exception:                                    # noqa: BLE001
        status = None
    if status != "on_dock":
        _relearn_note("failed", RELEARN_TH["not_docked"])
        return
    dock = dock_doc(load() or {})
    if not dock:
        _relearn_note("failed", event_walk.REASONS_TH["no_dock"])
        return
    # the board's pose goes where she sits now (B), not to the halfway written below: B is where she is
    look = await event_walk.find_dock_pose(dock, set_it=True, hold="relearn")
    word = look.get("found") or {}
    if not (look.get("ok") and word.get("ok")):
        _relearn_note("failed", RELEARN_TH["not_found"] % ("%.2f" % float(word.get("fit") or 0.0)))
        return
    (ax, ay, adeg), (bx, by, _) = first["pose"], word["pose"]
    apart = math.hypot(bx - ax, by - ay)
    if apart > RELEARN_AGREE_M:
        _relearn_note("failed", RELEARN_TH["disagree"] % (apart * 100))
        return
    pose = [round((ax + bx) / 2, 3), round((ay + by) / 2, 3), round(adeg, 1)]
    was = first.get("file") or list(dock.get("pose") or [pose[0], pose[1]])
    entry = {"pose": pose, "recorded_at": time.strftime("%Y-%m-%d %H:%M"), "was": was,
             "fit": [first.get("fit"), word.get("fit")],
             "shift_cm": round(100 * math.hypot(pose[0] - float(was[0]), pose[1] - float(was[1])), 1)}
    try:
        _save_recorded(lambda spots: None, dock=entry)
    except SpotsUnreadable:
        _relearn_note("failed", SPOTS_UNREADABLE_TH)
        return
    except Exception:                                    # noqa: BLE001
        logger.exception("relearn dock: could not write the dock pose")
        _relearn_note("failed", "บันทึกตำแหน่งแท่นไม่ได้ (เขียนไฟล์ไม่ได้)")
        return
    turnlog.record("event_dock_relearnt", shift_cm=entry["shift_cm"], fit_a=first.get("fit"), fit_b=word.get("fit"))
    moved = ("แท่นห่างจากค่าเดิม %.0f ซม." % entry["shift_cm"]) if entry["shift_cm"] >= RELEARN_SAME_M * 100 \
        else "ตรงกับที่บันทึกไว้"
    if word.get("set"):
        _relearn_note("done", "วัดแท่นใหม่เสร็จ %s — %s · ตั้งตำแหน่งแล้ว พร้อมเดิน" % (entry["recorded_at"][-5:], moved))
    else:
        _relearn_note("failed", "บันทึกแท่นใหม่แล้ว (%s) แต่ตั้งตำแหน่งบอร์ดไม่ได้ — กด 'ตั้งตำแหน่งที่แท่น'" % moved)


def _count_arrival(spot_id: str) -> None:
    def bump(spots):
        mine = spots.setdefault(spot_id, {})
        mine["arrivals"] = int(mine.get("arrivals") or 0) + 1
    try:
        _save_recorded(bump)
    except Exception:                                    # noqa: BLE001 -- a counter is not worth a failure
        logger.exception("could not count the arrival at %s", spot_id)


def _apply_mode(sc: dict) -> dict | None:
    """The scene's walking up to guests, a switch of its own, which a scene presses for
    staff. It answers for itself (no area marked, walking off, ...): the phone shows why.
    (No ceremony to set since 9 Nov: that is the Emma Ceremony app, app/ceremony.py.)"""
    if not settings.face_approach:
        return None
    from app import face_approach

    if sc["approach"]:
        return face_approach.set_enabled(True)
    if face_approach.armed():
        face_approach.set_enabled(False)
    return None


async def big_screen(want: str, *, by: str = "scene") -> dict:
    """The robot's face on the room's big screen ("open"), or off it ("close") -- one way for a
    scene's `tv` and the ceremony order's screen step (owner 9 Nov: the ceremony uses the old
    big screen). {"ok", "error"?}; also kept for the phones (STATE["tv"]): "เปิดจอแล้ว" said by
    a robot looking at a dark TV is the mock rule."""
    from app import event_clips, room_screens

    if want not in ("open", "close"):
        return {"ok": False, "error": "bad_screen"}
    name = (load() or {}).get("screen")
    try:
        if want == "close":
            result = await room_screens.close_screen(name, by=by)
        elif room_screens.state()["open"]:
            result = {"ok": True}                        # a reopen would drop the fetched clip
        else:
            result = await room_screens.open_screen(name, by=by)
    except Exception:                                    # noqa: BLE001 -- staff page says so
        logger.exception("the big screen")
        result = {"ok": False, "error": "error"}
    ok = bool(result.get("ok"))
    STATE["tv"] = {"want": want, "ok": ok, "error": None if ok else result.get("error")}
    if ok and want == "open":
        await event_clips.prepare()
    return {"ok": ok, **({} if ok else {"error": result.get("error") or "error"})}


async def _scene_screen(sc: dict) -> None:
    """The scene's `tv`. On a task: the room PC takes seconds, and the staff page waits
    for nothing. Its result is on that page."""
    await big_screen(sc["tv"], by="scene")


def _clip_ended(seq: int):
    async def ended(clip: dict, phase: str) -> None:
        line = clip.get("after_th")
        if phase == "ended" and line and STATE["seq"] == seq:
            await _say(clip_end_text(clip["title_th"], line), "event_clip_end", _still(seq))
    return ended


async def play_clip(clip_id: str, *, by: str = "staff", lead_in: bool = False) -> dict:
    """Staff's button, the host's words and a scene's `clip` all start one here, so all
    end the same way. Not in the normal mode by voice: event_clip checks that itself."""
    from app import event_clips

    return await event_clips.play(clip_id, by=by, lead_in=lead_in, on_end=_clip_ended(STATE["seq"]))


async def start_scene(scene_id: str, *, stop_index: int = 0, stops_seen: int | None = None) -> dict:
    """Staff picked a scene (or a stop of it). Walks if the scene has a spot
    and walking is on; Emma says the scene's opening line either way."""
    from app import event_clips, event_roam

    if ceremony_now():
        # Before anything is stopped: a scene pressed on the Event page must not take down the
        # ceremony's film or its walk either (owner 9 Nov, "ไม่งั้นจะปนกัน")
        return ceremony_refusal()
    sc = scene(scene_id)
    if sc is None:
        return {"ok": False, "error": "no_scene", "error_th": "ไม่พบฉากนี้"}
    from app import event_tour

    if sc.get("tour") and event_tour.running(scene_id):
        # card 1 again over its own tour (review 23 Nov): a fresh metre ahead from wherever she stands -- not before
        # a person stopped this one. Nothing is touched: the tour goes on.
        turnlog.record("event_scene", scene=scene_id, refused="tour_running")
        return {"ok": False, "error": "tour_running", "error_th": event_tour.RUNNING_TH}
    if sc.get("walk_out_of_box"):
        from app import event_box

        if not event_box.press("event_card", sc.get("forward_m")):
            # single use (second review 23 Nov): only staff's "หุ่นอยู่ในกล่องแล้ว" arms the walk out of the box -- out,
            # partly out or not known, a press would walk another whole forward_m from wherever she stands
            turnlog.record("event_scene", scene=scene_id, refused="not_armed")
            return {"ok": False, "error": "not_armed", "error_th": event_box.NOT_ARMED_TH}
    try:
        # the press's own reading of people's stops (the staff page passes the one it took first)
        seen = stops_seen if stops_seen is not None else event_walk.stops()
        # the last scene's wave comes down -- except the same scene pressed again while its standing
        # hand is up (owner 11 Nov 09:36, the debut twice in 9 s: down, `busy` while lowering, up again
        # after her line): it stays up for the new line (event_pageant.can_keep). A walk rests it anyway.
        # Out of the box (owner 23 Nov): never kept -- the arm is down before she walks out
        kept = bool(sc.get("pageant")) and not sc.get("walk_out_of_box") and event_pageant.can_keep(STATE["scene"], scene_id)
        if not kept:
            await event_pageant.end("scene")
        await event_roam.stop("scene")                       # before the walk: it must not start another
        await event_walk.stop("replaced")                    # one scene, one walk
        await event_clips.stop("scene")                      # ...and no film from the last one
        STATE["seq"] += 1
        seq = STATE["seq"]
        if kept and event_pageant.active() and not event_pageant.ending():
            event_pageant.keep(seq)                          # no await since the seq: the last press's line lets go of it
        STATE.update(scene=scene_id, since=time.time(), perform=None,
                     stop=max(0, min(stop_index, len(sc["stops"]) - 1)) if sc["stops"] else 0)
        _note_scene()
        approach = _apply_mode(sc)
        spot_id = _here(sc)
        from app import event_tour

        # card 1's tour (owner 23 Nov), planned BEFORE its walk is asked for -- a walker that arrives at once must find
        # it; any other scene ends a tour under way
        event_tour.pressed(sc, seq, seen)
        walk = await _walk_to(sc, spot_id, seq, stops_seen)
    except BaseException:
        if sc.get("walk_out_of_box") and not event_walk.walking():
            # raised (or cancelled) between the press and its walk (third review, 23 Nov): no walker set out -- the arm
            # back, not a press "pending" for good. A walker that did start ends through its own `_out_of_box`.
            from app import event_box

            event_box.never_moved()
        raise
    walking = bool(walk and walk.get("ok"))
    if sc.get("walk_out_of_box") and not walking:
        from app import event_box

        event_box.never_moved()                          # no walker set out (off, refused): the arm comes back
    if sc.get("tour"):
        event_tour.walk_answer(seq, walk)                # no walk = acted where she stands, no tour
    # The roam before the line (review 10 Nov: its "จะเดินทักทายแขกทั่วงาน" went out, then the roam
    # refused -- walking off on the robot). Refused = the scene acted where she stands, like a spot.
    roam = event_roam.start(sc) if sc["roam"] else None
    roaming = bool(roam and roam.get("ok"))
    where = spot_id if spot_id is not None or not sc["roam"] or roaming else ""
    STATE["standing"] = not walking and not roaming and where is not None
    turnlog.record("event_scene", scene=scene_id, stop=STATE["stop"], spot=spot_id,
                   walking=walking, refused=None if walk is None or walking else walk.get("error"),
                   tv=sc["tv"], clip=sc["clip"],
                   approach=None if approach is None else bool(approach.get("ok")))
    text = scene_text(sc, walking=walking, spot_id=where)
    if walking:
        # on the way: only the start's words, no gesture; the line (and its wave) is the arrival's
        if sc.get("on_arrive_th"):
            _perform(sc, seq, "walking", said=None, gesture_ok=None, gesture_error_th=None,
                     **({"gesture": "pageant"} if sc["pageant"] else {}))
        if text:
            # and the red stop: the walk it announces is over (review 10 Nov)
            _spawn(_say(text, "event_scene", lambda: STATE["seq"] == seq and event_walk.stops() == seen))
        if (sc["pageant"] or sc["say_on_walk"]) and not sc.get("walk_out_of_box"):
            # ...except the pageant (owner 9 Nov: "โบกไปด้วยระหว่างเดิน"): once the wheels turn --
            # and a say_on_walk scene's line with it (owner 10 Nov: "เดินไปด้านหน้าพร้อมโบกมือและพูด").
            # Out of the box (owner 23 Nov): neither -- arms down on the way, the wave and the line at the end.
            _spawn(_on_walk(sc, seq, event_walk.current(), seen))
    elif text:
        # `seen` = the press's own reading: a red stop while the arm was rested before the walk
        # answered the walk "stopped", and the scene was then acted where she stood -- the greeting
        # and its wave (review 9 Nov, probed). _say_line drops both on that stop. Not out of the box (no
        # walk: off, refused): the line where she stands, no arm (owner 23 Nov -- the arm would hit the box).
        if sc.get("walk_out_of_box"):
            _spawn(_line_no_arm(text, "event_scene", sc, seq, seen, "in_box", IN_BOX_TH))
        else:
            _spawn(_say_line(text, "event_scene", sc, seq, seen))
    if sc["tv"]:
        _spawn(_scene_screen(sc))
    clip = await play_clip(sc["clip"], by="scene", lead_in=bool(text)) if sc["clip"] else None
    return {"ok": True, "scene": scene_id, "stop": STATE["stop"], "walk": walk,
            "approach": approach, "clip": clip, "roam": roam, "speaks": bool(text)}


async def _on_walk(sc: dict, seq: int, run, stops_seen: int) -> None:
    """The debut's walk: the hand comes up once the walker is out (event_pageant.on_walk). The phone
    hears how it went; a refusal leaves the walk as it was (arms down) and the spot's line tries the
    pageant again standing, then the scene's `gesture`. A `say_on_walk` scene says its line right
    after (owner 10 Nov: "เดินไปด้านหน้าพร้อมโบกมือและพูด") -- once the walker is out, the hand up or
    refused; on the walk the wave is never tried again standing (`_say_line(on_walk=True)`)."""
    if run is None:
        return

    def still() -> bool:
        return STATE["seq"] == seq and event_walk.stops() == stops_seen

    if sc["pageant"]:
        # the card's "เอามือลง" before the wheels were out: not raised on this walk (MANUAL down_seq)
        result = await event_pageant.on_walk(seq, run, lambda: still() and _arm_free(seq))
        if STATE["seq"] == seq and (STATE["perform"] or {}).get("phase") == "walking":
            _perform(sc, seq, "walking", gesture="pageant", pageant_ok=bool(result.get("ok")),
                     pageant_error_th=None if result.get("ok") else result.get("error_th"))
    if not sc["say_on_walk"]:
        return
    deadline = time.monotonic() + event_pageant.START_WAIT_S
    while not (run.saw_started and run.state == "walking"):
        if run.state not in ("starting", "walking") or not still() or time.monotonic() > deadline:
            return                                       # never got going: `_callbacks` says so
        await asyncio.sleep(0.1)
    text = walk_line_text(sc)
    if text and still() and event_walk.current() is run:
        # set with no await since the walker's state was read: the arrival (`_callbacks`) then knows
        # the line is this walk's, and says it itself only when it never set out
        STATE["walk_line"] = seq
        await _say_line(text, "event_walk_line", sc, seq, stops_seen, on_walk=True)


async def next_stop(*, stops_seen: int | None = None) -> dict:
    if ceremony_now():
        return ceremony_refusal()
    sc = current()
    if sc is None or not sc.get("stops"):
        return {"ok": False, "error": "no_stops", "error_th": "ฉากนี้ไม่มีจุดถัดไป"}
    nxt = STATE["stop"] + 1
    if nxt >= len(sc["stops"]):
        return {"ok": False, "error": "last_stop", "error_th": "นี่คือจุดสุดท้ายของฉากนี้แล้ว"}
    return await go_stop(nxt, stops_seen=stops_seen)


async def go_stop(index: int, *, stops_seen: int | None = None) -> dict:
    if ceremony_now():
        return ceremony_refusal()
    sc = current()
    if sc is None or not sc.get("stops") or not 0 <= index < len(sc["stops"]):
        return {"ok": False, "error": "no_stops", "error_th": "ฉากนี้ไม่มีจุดนี้"}
    seen = stops_seen if stops_seen is not None else event_walk.stops()
    await event_pageant.end("scene")
    await event_walk.stop("replaced")
    STATE["seq"] += 1
    seq = STATE["seq"]
    STATE.update(stop=index, perform=None)
    _note_scene()
    spot_id = sc["stops"][index]
    walk = await _walk_to(sc, spot_id, seq, stops_seen)
    turnlog.record("event_scene", scene=sc["id"], stop=index, spot=spot_id,
                   walking=bool(walk and walk.get("ok")))
    STATE["standing"] = not (walk and walk.get("ok"))
    text = None
    if walk and walk.get("ok"):
        if sc.get("on_arrive_th"):
            _perform(sc, seq, "walking", said=None, gesture_ok=None, gesture_error_th=None)
    else:
        spot = _spot(spot_id)
        text = stop_text(sc, spot["name_th"] if spot else spot_id)
        if text:
            _spawn(_say_line(text, "event_scene", sc, seq, seen))
    return {"ok": True, "scene": sc["id"], "stop": index, "walk": walk,
            "speaks": bool(text) or bool(walk and walk.get("ok"))}


async def go_spot(spot_id: str, *, stops_seen: int | None = None, head_on: bool = False) -> dict:
    """Walk only (the staff page's spot list): no scene, nothing said. `head_on`: card 1's walk to a handshake done by
    hand (event_tour.event_hand, event_order.opening_hand) -- the way in the card would have come (walk_steps)."""
    doc = load() or {}
    spot = (doc.get("spots") or {}).get(spot_id)
    if spot is None:
        return {"ok": False, "error": "no_spot", "error_th": "ไม่พบจุดนี้"}
    from app import event_roam

    await event_roam.stop("staff")                       # her walk now, not the list's
    await event_pageant.end("spot")
    await event_walk.stop("replaced")
    STATE["seq"] += 1
    seen = event_walk.stops() if stops_seen is None else stops_seen
    show = spot.get("show") if isinstance(spot.get("show"), dict) else {}
    if show:
        # card 3 (owner 25 Nov ~04:40: "ตอนไปการ์ด 3 จอก็ไม่ควรขึ้นของการ์ด 2 ไหม"): card 2's last slide down, the hall's
        # stage back to its held logo (face_mirror.motion(None, None) -- condo-voice-75's tested way down; a no-op when
        # nothing is up); her music and pictures come over it after the tease
        try:
            from app import face_mirror

            face_mirror.motion(None, None)
        except Exception:                                # noqa: BLE001 -- the screen never stops a walk
            logger.exception("spot %s: card 2's slide down", spot_id)
    walk = await event_walk.start(spot_id, spot["name_th"], await walk_steps(spot, doc, head_on=head_on),
                                  dock=dock_doc(doc), on_end=_arrived_show(spot_id, show, seen) if show else _count_only,
                                  stops_seen=stops_seen)
    if walk.get("ok") and show.get("first"):
        # owner 25 Nov 01:57 (card 3): "หุ่นกับมิกิว่ามีอะไรจะเซอร์ไพรอีกอย่าง ถ้าอยากรู้ ... emma พาไปดูเอง" -- and after
        # rehearsal 5: "การ์ด 3 ตอนพูดก็เดินไปด้วยเลยไหม". Over the walk, once it has set out (its arm rested first: her
        # talking arm cannot take the rest away -- rehearsal 5's arm_not_rest; while the wheels turn the app refuses
        # her gestures). Not said (no take, a stop) = the walk as ever.
        _spawn(_walk_line(spot_id, show["first"], seen))
    return {"ok": bool(walk.get("ok")), "walk": walk, **({} if walk.get("ok") else
                                                          {"error": walk.get("error"),
                                                           "error_th": walk.get("error_th")})}


#: A spot's `show` line: her voice over within this (s) before what comes next.
SHOW_LINE_MAX_S = 20.0


async def _spot_line(spot_id: str, rec: dict, seen: int, what: str) -> bool:
    """A spot's recorded `show` line (pinned like a scene's `recorded`), through event_cues.play_line as the debut's and
    Khun Miki's are, and waited out. Not played (not this take, no call, a stop) = nothing said, logged."""
    from app import event_cues, event_tour

    entry = _recorded_entry({"id": "spot %s %s" % (spot_id, what), "recorded": rec})
    said, error = False, "not_pinned"
    if entry is not None:
        try:
            pcm, error = await asyncio.to_thread(recorded_pcm, {"recorded": entry})
            if pcm is not None and event_walk.stops() == seen:
                result = await event_cues.play_line("spot_%s_%s" % (spot_id, what), entry["text"], pcm,
                                                    lang=entry.get("lang") or "en", scene_th=spot_id,
                                                    still=lambda: event_walk.stops() == seen)
                said, error = bool(result.get("ok")), result.get("error")
                deadline = time.monotonic() + SHOW_LINE_MAX_S
                while said and event_tour._her_voice_on() and time.monotonic() < deadline \
                        and event_walk.stops() == seen:
                    await asyncio.sleep(0.2)
        except Exception:                                # noqa: BLE001 -- a line never ends a walk
            logger.exception("spot %s: the %s line", spot_id, what)
            error = "error"
    turnlog.record("event_walk", phase="show_line", spot=spot_id, what=what, said=said, error=None if said else error)
    return said


def _music(op: str) -> None:
    """The hall's catwalk music, as card 1 sends it (event_tour.music: its switch CATWALK_MUSIC, errors swallowed)."""
    from app import event_tour

    event_tour.music(op)


async def _walk_line(spot_id: str, rec: dict, seen: int) -> None:
    """A spot's `first` line said over its walk, the hall's music started after it (the walk's own start leaves it to
    this: event_walk._catwalk) -- cut with the walk when that ends without arriving."""
    run = event_walk.current()
    line = asyncio.ensure_future(_spot_line(spot_id, rec, seen, "first"))
    try:
        while not line.done():
            if run is not None and (event_walk.current() is not run or run.state not in ("starting", "walking", "arrived")):
                # the walk ended without arriving (B, the page quiet, the face lost, the board, another walk pressed --
                # only a person's stop moves `stops()`): "Emma will take you there" stops with it (review 25 Nov 03:3x)
                from app import event_cues

                turnlog.record("event_walk", phase="show_line_cut", spot=spot_id, state=getattr(run, "state", None))
                await event_cues.cut("walk_ended")
                break
            await asyncio.wait({line}, timeout=0.2)
        with contextlib.suppress(Exception):
            await line
    finally:
        # the music ONCE, after her line (owner 25 Nov ~04:25: "รอการ์ด 3 พูดแล้วเปิดเพลงรอบเดียว"), if the walk still goes
        if event_walk.current() is run and run is not None and event_walk.walking():
            from app import event_tour

            event_tour.music("walk", track="walk")


def _arrived_show(spot_id: str, show: dict, seen: int):
    """go_spot's end for a spot with a `show`: arrived (and nobody stopped her) = its `gesture` and its `arrive` line,
    together (owner 25 Nov: at the globe, the right hand up, "this is it -- surprise!")."""
    async def ended(run) -> None:
        await _count_only(run)
        if run.state != "arrived" or event_walk.stops() != seen:
            return
        if show.get("gesture"):
            result = await _gesture(str(show["gesture"]))
            turnlog.record("event_walk", phase="show_gesture", spot=spot_id, ok=bool(result.get("ok")),
                           error=None if result.get("ok") else result.get("error"))
        if show.get("arrive"):
            _music("duck")                               # the arrival's hit under her reveal: silence for her voice
            await _spot_line(spot_id, show["arrive"], seen, "arrive")
    return ended


def box_clear() -> dict:
    """Staff's two taps, "หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน" (either page, POST /event/box-clear): somebody moved her out of
    the box by hand, or she never was in one -- her arm is hers again (app/event_box.py). Not while a walk out of
    the box is still going: its own end decides."""
    from app import event_box, event_tour

    if event_walk.walking() or event_tour.active():
        return {"ok": False, "error": "walking", "error_th": BOX_BUSY_TH}
    was = event_box.clear("staff")
    return {"ok": True, "was_in": was}


BOX_BUSY_TH = "หุ่นกำลังเดิน/ทำต่อเนื่องอยู่ — รอให้จบหรือกดหยุดก่อน"


def box_arm(by: str = "event_page") -> dict:
    """Staff's two taps, "หุ่นอยู่ในกล่องแล้ว" (the Event page's box panel; the ceremony order's in-box tick): she is in
    the box -- the one walk out is armed, and the arm latch holds from now (app/event_box.py). Not while she walks or
    a card runs: that walk's own end says where she is."""
    from app import event_box

    # The Event page's two taps while the ceremony app is on (cross-review 24 Nov): after the opening has walked her
    # out, a stray arm latched the arm and made every later ceremony walk (scene 10's, to the globe) refuse until
    # someone cleared it. The ceremony's own in-box tick (`by="ceremony_tick"`) is how the ceremony arms.
    if by == "event_page" and ceremony_now():
        return {"ok": False, "error": CEREMONY_ERROR, "error_th": CEREMONY_ERROR_TH}
    why = box_arm_refusal()
    if why is not None:
        return why
    event_box.arm(by)
    return {"ok": True, "box": event_box.view()}


def box_arm_refusal() -> dict | None:
    """Why staff's "she is in the box" is refused now (`box_arm`, and the ceremony tick's own step) -- or None. While
    she walks, a card runs or a press of the walk out is under way: that walk's own end says where she is. Partly out
    or not known how far (third review, 23 Nov): arming blanked the distance walked, and the next press walked another
    whole forward_m from half out -- out first (`box_finish`, or by hand and `box_clear`), then in again."""
    from app import event_box, event_tour

    if event_walk.walking() or event_tour.active() or event_box.pending():
        return {"ok": False, "error": "walking", "error_th": BOX_BUSY_TH}
    if event_box.partial():
        return {"ok": False, "error": "partial", "error_th": event_box.ARM_PARTIAL_TH}
    return None


#: The rest of a walk out that stopped short, as the phone names it.
FINISH_NAME_TH = "ออกจากกล่องให้ครบ"
FINISH_UNKNOWN_TH = ("ไม่รู้ว่าหุ่นเดินออกมาแล้วเท่าไหร่ (ล้อหมุนแต่ไม่มีระยะ) — เดินต่อเองไม่ได้ · ดูหุ่นจริง พาออกเองแล้วกด "
                     "'หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน'")
FINISH_NOTHING_TH = "เดินครบระยะแล้วแต่ไม่เห็นล้อนิ่ง — ดูหุ่นจริงแล้วกด 'หุ่นออกจากกล่องแล้ว — ปลดล็อกแขน'"


async def box_finish(*, stops_seen: int | None = None) -> dict:
    """"เดินออกให้ครบ" (either page, two taps, POST /event/box-finish): a walk out that set out and stopped short walks
    only what is left of it -- straight ahead, all of it or not out (`short_ok` False), the arm rested before the
    wheels -- and lets the in-the-box latch go once that is walked and the wheels are still. The wave and the line
    after it are staff's to press (the Event page's "สั่งเอง" card, the ceremony's opening step's own button)."""
    from app import event_box, event_tour

    how = event_box.view().get("left") if event_box.held() else None
    if how == "unknown":
        return {"ok": False, "error": "unknown", "error_th": FINISH_UNKNOWN_TH}
    if how == "not_still":
        return {"ok": False, "error": "nothing_left", "error_th": FINISH_NOTHING_TH}
    left = event_box.left_m()
    if left is None:
        return {"ok": False, "error": "not_partial", "error_th": "ไม่มีการเดินออกจากกล่องที่ค้างครึ่งทาง"}
    if event_tour.active():
        return {"ok": False, "error": "tour_running", "error_th": event_tour.RUNNING_TH}
    if left < event_box.MOVED_M:
        # all of it walked, the wheels not seen still: nothing to walk -- clearing is staff's look and their own button
        return {"ok": False, "error": "nothing_left", "error_th": FINISH_NOTHING_TH}
    doc = load() or {}
    steps = await ahead_steps({"forward_m": left, "walk_out_of_box": True}, doc)
    if isinstance(steps, dict):
        return steps
    steps[0]["label"] = FINISH_NAME_TH
    seen = stops_seen if stops_seen is not None else event_walk.stops()

    async def ended(run) -> None:
        # all of it -> "not still" until the wheels say so; short -> what it walked added; no line -> not known
        if event_box.walk_ended(run, left, tol_m=WALKED_OUT_TOL_M) == "full":
            settled = await _wheels_still(lambda: event_walk.stops() == seen)
            if settled is not False and event_walk.stops() == seen:
                event_box.outcome("out")

    walk = await event_walk.start(FORWARD_SPOT, FINISH_NAME_TH, steps, dock=dock_doc(doc), on_end=ended,
                                  stops_seen=seen)
    return {"ok": bool(walk.get("ok")), "walk": walk,
            **({} if walk.get("ok") else {"error": walk.get("error"), "error_th": walk.get("error_th")})}


async def _count_only(run) -> None:
    if run.state == "arrived":
        _count_arrival(run.spot)


# ---- one thing at a time: the Event page's "สั่งเอง" card (owner 14 Nov) ----------------------------
# Owner 14 Nov 2026: "ทำหน้าปุ่มกดยัง ทำแบบ manual". A scene's press does everything at once (walk,
# line, gesture, the big screen); on the card staff press ONE: a scene's line where she stands, a
# gesture, a walk to a spot (`go_spot`, already one thing). Ids only -- a scene's id, a gesture's id
# from MANUAL_GESTURES -- never words (the `show_unit(room)` rule). None of these is a scene press:
# STATE["seq"] is not moved, so a line or a wave pressed here never cancels the scene's own (her
# arrival line, its wave) -- it is said beside it.

#: The arm gestures the card offers. No head (ch8 burned 12 Sep -- the servo is the robot's to move
#: gently; HeadLook), no stop (the red button is the stop), no vendor groups beyond these.
MANUAL_GESTURES = ("wave", "greet_arm", "handshake", "raise_right_arm", "raise_left_arm", "rest")
#: The card's refusals in the phone's words.
MANUAL_ERRORS_TH = {
    "no_scene": "ไม่พบฉากนี้",
    "no_line": "ฉากนี้ไม่มีประโยคให้พูดตรงที่หุ่นยืน",
    "busy": "ปุ่มที่กดก่อนหน้ายังส่งอยู่ — รอสักครู่แล้วกดใหม่",
    "stopped": "กดหยุดแล้ว — ยกเลิกประโยค",
    "not_said": "Emma ไม่ได้พูด — หน้าหุ่นไม่รับสาย ดูว่าแอป Emma Event เปิดอยู่บนหุ่น แล้วกดใหม่",
    "bad_request": "ไม่รู้จักท่านี้ — รีเฟรชหน้าคุมแล้วกดใหม่",
    "walking": "หุ่นกำลังเดินอยู่ — รอให้ถึงก่อน หรือกดหยุดแล้วค่อยกดพูด",
    "moved": "หุ่นออกเดินหรือที่ยืนเปลี่ยนก่อนถึงคิวพูด — ยกเลิกประโยค ดูที่หุ่นแล้วกดใหม่",
    # every play_line failure _say_recorded does not hand to her own voice (review 14 Nov round 2:
    # it named one cause of several) -- the take playing already, cut short, her voice still on
    "already": "เสียงอัดของฉากนี้เล่นไม่ได้ตอนนี้ (กำลังเล่นอยู่ ถูกตัด หรือ Emma ยังพูดค้าง) — ดูที่หุ่นก่อนกดซ้ำ",
    "stopped_arm": "กดหยุดระหว่างเรียกสาย — ไม่ส่งท่า",
    "arm_walking": "หุ่นกำลังเดินอยู่ — ขยับแขนไม่ได้ รอให้ถึงก่อน",
    "app_latched": "เพิ่งกดหยุด — หุ่นล็อกแขนไว้ราว 60 วินาที",
    # card 1's tour (app/event_tour.py) has the arm between its walks: its own handshakes, waited out
    "tour": "การ์ด 1 กำลังทำต่อเนื่องอยู่ (เดินไปจับมือทีละจุด) — กดหยุดสีแดงก่อน แล้วค่อยสั่งเอง",
}
#: One press of each kind in flight: a second tap while her line or the arm's answer is on its way
#: is said, never sent twice (two of her lines queued; two gestures = the second refused `busy`).
#: `pageant_gen`: moved by every "เอามือลง" -- a ท่านางงาม start still on its way when it was pressed
#: belongs to nobody, and a late accept is brought straight down (event_pageant.start's `still`).
#: `down_seq`: the scene press (STATE["seq"]) whose arm "เอามือลง" took back (review 14 Nov, round 2):
#: everything that press still had queued for the arm -- the debut's hand before its line, the hand
#: on its walk and again at the spot, the fallback gesture after a hand that came down -- is not
#: asked for (`_arm_free`). Pressing the scene again is a new press, its arm its own.
MANUAL: dict = {"saying": False, "arm": False, "pageant_gen": 0, "down_seq": None}


def _arm_free(seq: int) -> bool:
    """The scene press `seq` may still move the arm: staff's "เอามือลง" did not take it back."""
    return MANUAL.get("down_seq") != seq


def _manual_refusal(code: str) -> dict:
    return {"ok": False, "error": code, "error_th": MANUAL_ERRORS_TH[code]}


def _manual_there(sc: dict) -> str | None:
    """The scene's spot, when the last walk ARRIVED there (she stands at it): its arrival line is
    hers to say. Anything else -- another walk since, a walk that stopped short, none -- is "where
    she stands" (10 Nov: the multimedia room's arrival said from across the lobby). A scene of
    stops (the tables): whichever of them the last walk arrived at -- the card's own walk to table 3
    included, which the scene's `STATE["stop"]` knows nothing of (review 14 Nov)."""
    run = event_walk.current()
    if run is None or run.state != "arrived":
        return None
    spots = list(sc.get("stops") or []) or ([sc["spot"]] if sc.get("spot") else [])
    if run.spot not in spots or _moved_since(run):
        return None
    return run.spot


#: Seen moving this long after a walk ended = moved off its spot (walk_frames.Motion needs two polls
#: a second apart past 3 cm: the arrival's own last creep is inside it).
MOVED_SLACK_S = 2.0


def _moved_since(run) -> bool:
    """The base was seen moving on its own after `run` ended (review 14 Nov, round 2: pushed back,
    driven by a joystick, docked by hand -- the card still offered "ถึงห้องมัลติมีเดียแล้ว", the
    10 Nov bug in a new shape). Not seen (WALK_FRAMES off, the board silent) = not moved: the
    walk's own word is all there is. Never raises."""
    try:
        from app import walk_frames

        moved = walk_frames.moved_at()
    except Exception:                                    # noqa: BLE001
        return False
    ended = getattr(run, "ended", None)
    return moved is not None and ended is not None and moved > float(ended) + MOVED_SLACK_S


def _manual_choice(sc: dict) -> dict | None:
    """The one choice behind the card's "พูด" of `sc` (manual_line, manual_kind and the phone's
    preview read it -- three copies drifted is a button saying one line and her saying another):
    {"kind": "arrive", "text", "at": the spot's name} at its spot (`_manual_there`), else
    {"kind": "stand", "text", "standing": bool} -- scene_text's choice (`on_stand_th` for a scene
    that goes somewhere, "" = nothing; never "I have arrived", never "on my way"). None = nothing."""
    there = _manual_there(sc)
    if there is not None and sc.get("on_arrive_th"):
        spot = _spot(there)
        return {"kind": "arrive", "text": sc["on_arrive_th"], "at": spot["name_th"] if spot else there}
    spot_id = _here(sc)
    where = spot_id if spot_id is not None else ("" if sc.get("roam") else None)
    if where is not None and "on_stand_th" in sc:
        text = sc.get("on_stand_th") or None
    else:
        text = sc.get("on_arrive_th") or sc.get("on_start_th")
    if not text:
        return None
    return {"kind": "stand", "text": text, "standing": where is not None}


def manual_line(sc: dict) -> str | None:
    """What the card's "พูด" of `sc` tells her (`_manual_choice`). None = nothing to say."""
    choice = _manual_choice(sc)
    if choice is None:
        return None
    if choice["kind"] == "arrive":
        return _system("ทีมงานกดให้พูด หุ่นอยู่ที่%sแล้ว" % choice["at"], choice["text"])
    text = choice["text"] + (STANDING if choice["standing"] else "")
    return _system("ทีมงานกดให้พูด (%s)" % sc["title_th"], text)


def manual_kind(sc: dict) -> str | None:
    """For the card's button: "arrive" (she is at the scene's spot), "stand", or None (nothing)."""
    choice = _manual_choice(sc)
    return choice["kind"] if choice else None


def manual_preview(sc: dict) -> dict | None:
    """What the card shows under a "พูด" button (review 14 Nov, round 2: a scene's title alone did
    not say WHICH of its lines a press says -- from the lobby "พาแขกไปห้องมัลติมีเดีย" is the
    invitation, not the arrival): {"quote": True, "text"} = the sentence she says as it is (says_th's
    rule: «...»), {"quote": False, "text"} = what the line asks of her, in the event file's words
    (staff read it; an instruction is not shown as her words). None = nothing to say."""
    choice = _manual_choice(sc)
    if choice is None:
        return None
    found = _QUOTED.search(choice["text"])
    if found:
        return {"quote": True, "text": found.group(1).strip()}
    return {"quote": False, "text": choice["text"].strip()}


async def manual_say(scene_id: str) -> dict:
    """The card's "พูด": the scene's line now, where she stands -- no walk, no gesture, the scene
    staff chose stays the scene. Its recorded take when it has one (the debut's), else her own voice.
    Waits for the robot to stand still first (pushed a moment ago, WALK_QUIET would drop her reply
    whole), and a red stop meanwhile drops it -- _say_line's steps 1-2, without its arm.
    {"ok", "scene", "kind", "recorded"} | {"ok": False, "error", "error_th"}."""
    if ceremony_now():
        return ceremony_refusal()
    sc = scene(scene_id)
    if sc is None:
        return _manual_refusal("no_scene")
    text = manual_line(sc)
    if text is None:
        return _manual_refusal("no_line")
    if event_walk.walking():
        # a line for "where she stands" said on the way (review 14 Nov): she would be told
        # "หุ่นยืนอยู่กับที่" with the wheels turning
        return _manual_refusal("walking")
    if MANUAL["saying"]:
        return _manual_refusal("busy")
    kind = manual_kind(sc)
    MANUAL["saying"] = True
    seen = event_walk.stops()

    def placed() -> bool:
        # chosen at the press, said up to a minute later (her voice, the lock): still her place --
        # no walk set out since, the arrival line not gone stale (review 14 Nov)
        return not event_walk.walking() and manual_line(sc) == text

    def still() -> bool:
        return event_walk.stops() == seen and placed()

    def why_not(recorded) -> dict:
        if ceremony_now():
            return ceremony_refusal()
        if event_walk.stops() != seen:
            return _manual_refusal("stopped")
        if not placed():
            return _manual_refusal("moved")
        return _manual_refusal("already" if recorded is False else "not_said")

    recorded = None
    try:
        if not event_walk.moving() and _robot_settling():
            deadline = time.monotonic() + STILL_WAIT_S
            while _robot_settling() and still() and time.monotonic() < deadline:
                await asyncio.sleep(0.25)
        if not still():
            out = why_not(None)
            turnlog.record("event_manual", what="say", scene=scene_id, ok=False, error=out["error"])
            return out
        if sc.get("recorded"):
            # the take plays over a call only: rung first, MUTED under a held mic (STAFF_RING) --
            # with none, _say_recorded hands the words to her own voice and her summon rings it
            if event_pageant._session() is None:
                await event_walk.call_for(still, reason="event_manual")
            recorded = await _say_recorded(sc, still)
        if recorded is None:
            fixed = _QUOTED.search(text) is not None     # a quoted sentence: its language is its own
            said = await (_say(text, "event_manual", still, held_language=False) if fixed
                          else _say(text, "event_manual", still))
        else:
            said = recorded
    finally:
        MANUAL["saying"] = False
    if not said:
        out = why_not(recorded)
        turnlog.record("event_manual", what="say", scene=scene_id, kind=kind, ok=False, error=out["error"])
        return out
    turnlog.record("event_manual", what="say", scene=scene_id, kind=kind, ok=True, recorded=bool(recorded))
    return {"ok": True, "scene": scene_id, "kind": kind, "recorded": bool(recorded)}


async def manual_gesture(action: str) -> dict:
    """The card's gesture buttons. A MANUAL_GESTURES id: robot_body.staff_request (every gate but
    the heard guard -- the press IS the person asking), asked ONCE (`_gesture(retry=False)`: a
    re-press while the last one plays is said busy, never queued behind it). Never during a walk
    (refused here, `navigating`, before anything is touched -- robot_face_link would refuse it too,
    but a pageant start on its way had already taken the debut walk's own start, review 14 Nov round
    2). With no call up, the camera's bell first (`event_walk.call_for`: the call opens MUTED under
    a held mic -- the arm needs no mic). "pageant": the debut's ท่านางงาม where she stands, its own 45 s
    cap brings it down (the app, Pageant.MAX_MS) or "pageant_end" / the next scene / a walk; a hand
    already up (and not on its way down) is said so, not asked again. "pageant_end" is never
    refused -- a hand coming down is what every gate wants -- and it takes back what the scene press
    still had queued for the arm (MANUAL down_seq). {"ok", "action", "error", "error_th"} (+ "note"
    for the phone): `ok` = the robot accepted it, never "it waved" (the mock rule)."""
    if action == "pageant_end":
        # A start still on its way (its ack, its retries up to RETRY_S) must not outlive this press
        # (review 14 Nov: the end found nothing accepted yet, said "down", and the start was accepted
        # after it -- 45 s of waving): it is nobody's now (`pageant_gen`, its `still`), and the end
        # waits for it and asks the app until nothing is up (`wait`). And the scene press's own arm,
        # still to come (round 2: the debut's hand before its line, its fallback wave after it, the
        # hand at the walk's end) is taken back with it (`down_seq`, `_arm_free`).
        MANUAL["pageant_gen"] += 1
        MANUAL["down_seq"] = STATE["seq"]
        # card 1's tour: its later handshakes are this press's arm too -- it ends here, no handshake and no walk after
        # (review 23 Nov), and its walk under way stops where she is (the run reads the end at its next tick)
        from app import event_tour

        tour_ended = event_tour.stop("hand_down")
        was_up = event_pageant._maybe_up() or bool(event_pageant.STATE.get("asking"))
        ok = await event_pageant.end("staff", wait=True)
        turnlog.record("event_manual", what="gesture", action=action, ok=bool(ok), was_up=was_up)
        # nothing was up = nothing was sent: said so (round 2 -- "สั่งเอามือลงแล้ว" over a held-out
        # handshake, the button read as a general "arm down")
        return {"ok": bool(ok), "action": action, "note": None if was_up else "nothing_up", "tour_ended": tour_ended,
                "error": None if ok else "not_down",
                "error_th": None if ok else "ส่งคำสั่งเอามือลงไม่ถึงหุ่น — ถ้าแขนยังยก กดปุ่มหยุดสีแดง"}
    if action not in MANUAL_GESTURES and action != "pageant":
        return dict(_manual_refusal("bad_request"), action=action)
    if ceremony_now():
        return dict(ceremony_refusal(), action=action)
    from app import event_tour

    if event_tour.holds_arm():
        # its handshakes are waited out between its walks: a second gesture there is refused by the robot `busy`,
        # or lands between them and holds the arm out over the next walk's rest
        return dict(_manual_refusal("tour"), action=action)
    if event_walk.walking():
        return dict(_manual_refusal("arm_walking"), action=action)
    if MANUAL["arm"]:
        return dict(_manual_refusal("busy"), action=action)
    if action == "pageant" and event_pageant.active() and not event_pageant.ending():
        # up already (the scene's, or this card's a moment ago): one hand, said so -- asking again
        # would answer "accepted" for nothing new
        turnlog.record("event_manual", what="gesture", action=action, ok=True, note="already_up")
        return {"ok": True, "action": action, "note": "already_up", "error": None, "error_th": None}
    seen, seq, gen = event_walk.stops(), STATE["seq"], MANUAL["pageant_gen"]
    MANUAL["arm"] = True
    try:
        if event_pageant._session() is None:
            await event_walk.call_for(lambda: event_walk.stops() == seen, reason="event_manual")
        # asked again after the bell (review 14 Nov): a red stop while it rang may have reached no
        # app to latch (no call, no link) -- this must not lean on the latch (_say_line's rule); the
        # ceremony app may have opened meanwhile, and its arm is its order's; a walk may have set out
        if event_walk.stops() != seen:
            result = {"ok": False, "error": "stopped", "error_th": MANUAL_ERRORS_TH["stopped_arm"]}
        elif ceremony_now():
            result = ceremony_refusal()
        elif event_walk.walking():
            result = _manual_refusal("arm_walking")
        elif action == "pageant":
            # the press now is its owner (`seq`): the next scene or walk brings it down like the
            # debut's; a red stop or "เอามือลง" before the app answered brings a late accept down
            result = await event_pageant.start(seq, still=lambda: (STATE["seq"] == seq and event_walk.stops() == seen
                                                                   and MANUAL["pageant_gen"] == gen))
        else:
            result = await _gesture(action, retry=False)
    finally:
        MANUAL["arm"] = False
    ok = bool(result.get("ok"))
    turnlog.record("event_manual", what="gesture", action=action, ok=ok, error=None if ok else result.get("error"))
    return {"ok": ok, "action": action, "note": None, "error": None if ok else result.get("error"),
            "error_th": None if ok else result.get("error_th")}


def manual_snapshot(doc: dict) -> dict | None:
    """The card, for the phone: every gesture of MANUAL_GESTURES, in that order, with `why_th` = why
    it would not be sent now (the gates staff_request passes -- one the robot has no recording of
    included). Never dropped from the list: that is known only while a call is up (its caps), and
    the buttons moved under a finger as calls came and went (review 14 Nov). Whether the event has
    a pageant to offer, and the presses in flight. None = it could not be read (the page hides the
    card): round 2 -- a caps row of the wrong shape raised here, and /event/state answered 500, the
    whole page said "ติดต่อหุ่นไม่ได้" and went stale; the card is not worth the page."""
    try:
        return _manual_snapshot(doc)
    except Exception:                                    # noqa: BLE001 -- the scenes and the stop come first
        logger.exception("event staff page: the สั่งเอง card")
        return None


def _manual_snapshot(doc: dict) -> dict:
    from app import robot_body

    # the app's own 60 s latch after a stop (the red button, B, the operator screen): every arm
    # press is refused by the robot meanwhile, and the buttons looked ready (round 2)
    latched = MANUAL_ERRORS_TH["app_latched"] if event_walk._app_latched() else None
    from app import event_tour

    if latched is None and event_tour.holds_arm():
        latched = MANUAL_ERRORS_TH["tour"]               # the press would be refused (manual_gesture): greyed, said why
    gestures = []
    for action in MANUAL_GESTURES:
        code = robot_body.staff_ready(action)
        if code == "no_call":
            code = None                                   # the press rings the call itself
        gestures.append({"id": action, "name_th": gesture_th(action),
                         "why_th": latched or (robot_body.staff_reason_th(code) if code else None)})
    pageant = any(sc.get("pageant") for sc in doc.get("scenes") or [])
    return {"gestures": gestures,
            "pageant": pageant,
            "pageant_why_th": (latched or _manual_pageant_why()) if pageant else None,
            "pageant_up": event_pageant.active(),
            "saying": MANUAL["saying"], "arm": MANUAL["arm"]}


def _manual_fields(sc: dict) -> dict:
    """A scene's "พูด" on the card (manual_kind, manual_preview) -- nothing, rather than the page,
    when it cannot be read (manual_snapshot's rule)."""
    try:
        return {"manual_line": manual_kind(sc), "manual_says": manual_preview(sc)}
    except Exception:                                    # noqa: BLE001
        logger.exception("event staff page: the line of %s", sc.get("id"))
        return {"manual_line": None, "manual_says": None}


def _manual_pageant_why() -> str | None:
    """Why the card's ท่านางงาม would be refused now, read from the gates its press meets (review
    14 Nov, round 2: the button stood pressable with the arm switched off at the server, every
    other gesture greyed beside it, and every press came back refused). event_pageant.start's
    own gate (the server's keys, the desk's /arm STOP); a walk (robot_face_link refuses it
    `navigating` -- the debut's walk is the one exception, and that is a scene's, not this
    card's); a call up on an app that has no pageant (`not_event_app`). No call = None: the
    press rings it. What the robot decides at the moment is its to answer on the press."""
    from app import robot_body, robot_face_link

    code = event_pageant._gate()
    if code is None and robot_face_link.server_walking():
        code = "navigating"
    if code is None:
        session = event_pageant._session()
        if session is not None and (robot_face_link.caps(session, "body") or {}).get("pageant") is not True:
            code = "not_event_app"
    return robot_body.staff_reason_th(code) if code else None


#: The way back along a walk ahead (event_walk.back_line): its run on the phone.
BACK_SPOT = "back_to_dock"
BACK_NAME_TH = "แท่นชาร์จ (ถอยตามทางที่เดินออกมา)"
#: board_walk's BACK_TO_LINE_M / BACK_TO_YAW_DEG / BACK_TO_MAX_M: read here first so the phone hears
#: why at once, in Thai -- the walker checks again from the pose it starts from.
BACK_LINE_M, BACK_LINE_DEG, BACK_MAX_M = 0.05, 3.0, 2.5


async def _off_the_back_line(line: dict) -> str | None:
    """None = the robot stands on the way back (or its pose cannot be read here: the walker
    checks), else the phone's word for why it will not back along it."""
    try:
        from app import robot_chassis

        if not robot_chassis.configured():
            return None
        pose = await asyncio.wait_for(robot_chassis.pose(), 3.0)
        x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose["yaw"])
    except Exception:                                    # noqa: BLE001 -- board_walk reads it again
        return None
    dx, dy, dyaw = line["dock"][0], line["dock"][1], math.radians(line["dock"][2])
    ux, uy = math.cos(dyaw), math.sin(dyaw)
    along = (x - dx) * ux + (y - dy) * uy
    aside = (x - dx) * -uy + (y - dy) * ux
    turned = abs(math.degrees(math.atan2(math.sin(yaw - dyaw), math.cos(yaw - dyaw))))
    if abs(aside) <= BACK_LINE_M and turned <= BACK_LINE_DEG and 0.05 <= along <= BACK_MAX_M:
        return None
    if along < 0.05 and abs(aside) <= BACK_LINE_M and turned <= BACK_LINE_DEG:
        return "หุ่นอยู่ชิดแท่นแล้วแต่ไม่ได้ชาร์จ — ดันหุ่นเข้าแท่นเบาๆ"
    where = ("ห่างแท่น %.1f ม. เกินที่ถอยได้" % along if abs(aside) <= BACK_LINE_M and turned <= BACK_LINE_DEG
             else "ห่างแนว %.0f ซม. หัน %.0f°" % (abs(aside) * 100, turned))
    return ("หุ่นไม่ได้อยู่บนทางที่เดินออกมาแล้ว (%s) — ไม่ถอย เพราะเลเซอร์มองไม่เห็นข้างหลัง · เข็นหุ่นกลับแท่นเอง "
            "(ชั้น 1: กดกลับแท่นอีกครั้ง = เดินเข้าแท่นตามแผนที่)" % where)


#: The way the button said when staff tapped it twice is not the server's now (review 11 Nov).
WAY_CHANGED_TH = {
    "back": "ทางถอยกลับหายไปแล้ว (เริ่มเซิร์ฟเวอร์ใหม่ / เกิน 30 นาที / หุ่นถูกขยับ) — ไม่ได้เดินตามแผนที่แทน "
            "ดูปุ่มอีกครั้ง: ถ้าขึ้น 'ตามแผนที่ ชั้น 1' ใช้ได้เฉพาะชั้น 1",
    "map": "ตอนนี้มีทางถอยตามทางที่เดินออกมาแล้ว — ดูปุ่มอีกครั้งแล้วกดใหม่",
}


async def go_home(*, stops_seen: int | None = None, way: str | None = None) -> dict:
    """The walk card's "กลับแท่น" (owner 11 Nov: "ขอปุ่มกลับแท่น", after the debut's metre off the
    floor-2 dock with the board on floor 1's map). After a walk ahead off the dock: back along the
    line it came out on (event_walk.back_line -> board_walk `back_to`: velocity only, no map --
    either floor). With no such line: the map's dock walk (the spot list's "dock" -- floor 1's;
    the phone says which it will be). Nothing is said."""
    if ceremony_now():
        return ceremony_refusal()
    line = event_walk.back_line()
    now = "map" if line is None else "back"
    if way is not None and way != now:
        # the phone confirmed one way and the server would take the other: neither, and say so --
        # a floor-1 dock walk on the map is the wrong floor's on floor 2
        turnlog.record("event_walk", phase="refused", spot=BACK_SPOT, reason="way_changed", asked=way, now=now)
        return {"ok": False, "error": "way_changed", "error_th": WAY_CHANGED_TH.get(way, WAY_CHANGED_TH["back"]),
                "way": now}
    if line is None:
        out = await go_spot("dock", stops_seen=stops_seen)
        return dict(out, way="map")
    off = await _off_the_back_line(line)
    if off is not None:
        # one rule (review 11 Nov): the line is the way home only while she stands on it -- off it
        # it is forgotten, and the next press is the map's walk (the button says so; on the wrong
        # floor's map its fit gate refuses)
        event_walk.forget_back_line("off_the_line")
        turnlog.record("event_walk", phase="refused", spot=BACK_SPOT, reason="off_the_line")
        return {"ok": False, "error": "off_the_line", "error_th": off, "way": "back"}
    from app import event_roam

    await event_roam.stop("staff")
    await event_pageant.end("spot")
    await event_walk.stop("replaced")
    STATE["seq"] += 1
    walk = await event_walk.start(BACK_SPOT, BACK_NAME_TH, [{"back_to": line["dock"], "label": "ถอยกลับแท่น"}],
                                  dock=None, stops_seen=stops_seen)
    return {"ok": bool(walk.get("ok")), "walk": walk, "way": "back",
            **({} if walk.get("ok") else {"error": walk.get("error"), "error_th": walk.get("error_th")})}


async def stop() -> dict:
    from app import event_roam, face_approach

    # First, and whether or not anything is walking: pressed while she is still checking
    # (planning, or between arrival and a turn) it was {"stopped": False} and she set out.
    face_approach._turn_off("staff")
    live_close("stop")                                   # the red stop shuts the live window too
    await event_roam.stop("staff")
    stopped = await event_walk.stop("staff")
    from app import event_tour

    # card 1's tour: over, nothing more of it (after the walk's stop: its leg keeps the person's reason)
    toured = event_tour.stop("staff")
    turnlog.record("event_stop", walking=stopped, tour=toured)
    return {"ok": True, "stopped": stopped, "tour_stopped": toured}


async def save_spot(spot_id: str) -> dict:
    """The robot's pose now becomes this spot (staff drove it there)."""
    doc = load()
    if doc is None or spot_id not in doc["spots"]:
        return {"ok": False, "error": "no_spot", "error_th": "ไม่พบจุดนี้"}
    if doc["spots"][spot_id].get("dock"):
        return {"ok": False, "error": "dock", "error_th": "แท่นชาร์จใช้ตำแหน่งของแท่นเอง บันทึกทับไม่ได้"}
    follows = _follows(doc["spots"][spot_id]) or ("?" if "from_spot" in doc["spots"][spot_id] else None)
    if follows:
        # its walk is "from there, turned round, up to whoever stands there" (card 3, 22 Nov): a pose recorded
        # here would replace that walk with a drive at the place a guest stands
        name = (doc["spots"].get(follows) or {}).get("name_th") or follows
        return {"ok": False, "error": "follows",
                "error_th": "จุดนี้เดินต่อจาก '%s' (หมุนตัวแล้วเดินไปหาแขก) ไม่ต้องบันทึก — จะย้ายให้บันทึกจุด '%s' แทน"
                            % (name, name)}
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
    hazard = hazard_on_line(x, y, x, y)
    if hazard is not None:
        # a table's spot on top of it would be driven to all day
        return {"ok": False, "error": "floor_hazard",
                "error_th": "ตรงนี้ใกล้จุดสะดุดบนพื้น (%s) ให้ยืนห่างออกไปอีกนิดแล้วบันทึกใหม่" % hazard["name_th"]}
    entry = {"pose": [round(x, 3), round(y, 3), round(math.degrees(yaw), 1)],
             "recorded_at": time.strftime("%Y-%m-%d %H:%M"), "quality": quality, "arrivals": 0}

    def put(spots):
        spots[spot_id] = entry
    try:
        _save_recorded(put)
    except SpotsUnreadable:
        return {"ok": False, "error": "spots_unreadable", "error_th": SPOTS_UNREADABLE_TH}
    turnlog.record("event_spot_saved", spot=spot_id, quality=quality)
    return {"ok": True, "spot": spot_id, "pose": entry["pose"]}


# ---- the staff page ----------------------------------------------------------------

def _screen_view() -> dict:
    """The room's big screen for the phones: offered on this machine, and whether the face is up."""
    try:
        from app import room_screens

        up = room_screens.state()
        return {"offered": bool(room_screens.offered()), "open": bool(up.get("open")), "name": up.get("screen")}
    except Exception:                                    # noqa: BLE001 -- a poll, not a gate
        logger.exception("the big screen's state")
        return {"offered": False, "open": False, "name": None}


def _net_view() -> dict | None:
    """The robot's internet as its prober sees it (NET_STATUS, app/net_health.py), for both
    phones' "เน็ต" lamp -- None when nothing probes. Never in the poll's way."""
    from app.config import settings as cfg

    if not (cfg.offline_fallback or cfg.net_status):
        return None
    try:
        from app import net_health

        return net_health.staff_view()
    except Exception:                                    # noqa: BLE001 -- a lamp, not the walk's poll
        logger.exception("staff page: the network lamp")
        return None


def shared_snapshot() -> dict:
    """What both phones show (the Event page's /event/state and the Ceremony page's
    /ceremony/state -- owner 9 Nov, the ceremony "สามารถใช้ข้อมูลอันเก่าได้"): the walk, the
    face, the modes, the films, the big screen, the event's spots. Never a guest's words."""
    doc = load()
    from app import event_clips

    from app import event_box

    out = {"ok": True, "event": None, "walk": event_walk.snapshot(), "face": _face_state(),
           "mode": mode_snapshot(), "clips": event_clips.snapshot(), "screen": _screen_view(), "spots": [],
           "net": _net_view(), "relearn": relearn_view(),
           # in the box (app/event_box.py, owner 23 Nov): every arm held -- both pages, with staff's two-tap clear
           "box": event_box.view()}
    if doc is None:
        return out
    for sid, spot in doc["spots"].items():
        out["spots"].append({"id": sid, "name_th": spot["name_th"], "dock": bool(spot.get("dock")),
                             "walkable": spot_steps(spot) is not None, "pose": spot.get("pose"),
                             "follows_th": ((doc["spots"].get(_follows(spot)) or {}).get("name_th") or _follows(spot)
                                            if _follows(spot) else None),
                             "recorded_at": spot.get("recorded_at"), "arrivals": spot.get("arrivals", 0)})
    ev = doc.get("event") or {}
    out["event"] = {"name_th": ev.get("name_th"), "dates": ev.get("dates") or []}
    return out


def snapshot() -> dict:
    """Everything the Event phone page shows: the shared pieces and the scenes. Never a guest's
    words. No event file = {"event": None, ...} (the page says there is nothing to run)."""
    doc = load()
    out = shared_snapshot()
    from app import event_tour

    out["tour"] = event_tour.view()                      # card 1's tour as it went: its steps, where it ended
    if doc is None:
        return out
    scenes = []
    for sc in doc["scenes"]:
        # what card 1's one press does after its own walk, from what is recorded NOW (the same plan the press runs:
        # event_tour.pressed) -- None when the scene has none or EVENT_TOUR is off
        tour = event_tour.planned(sc.get("tour"), doc["spots"]) if settings.event_tour else None
        scenes.append({"id": sc["id"], "title_th": sc["title_th"], "brief_th": sc.get("brief_th") or "",
                       "tour": tour,
                       # a line for the people pressing the card (card 3, 22 Nov: look behind her first), never
                       # sent to the model
                       "staff_th": sc.get("staff_th") if isinstance(sc.get("staff_th"), str) else "",
                       "spot": sc.get("spot"), "stops": sc.get("stops") or [],
                       "approach": sc["approach"], "tv": sc["tv"], "clip": sc["clip"],
                       "roam": bool(sc["roam"]), "quick": sc["quick"], "gesture": sc["gesture"],
                       "gesture_th": gesture_th("pageant" if sc["pageant"] else sc["gesture"]),
                       "pageant": sc["pageant"], "says_th": says_th(sc), "forward_m": sc["forward_m"],
                       # the page's numbered boxes say what she does (owner 10 Nov "ช่องๆ แยกแต่ละซีน
                       # เป็นลำดับ"), as start_scene does it: a walk's line as it sets off (`on_start_th`)
                       # and at the spot (`on_arrive_th` -- rest has none: silent at the dock), the line
                       # as the wheels turn, the roam's stops (event_roam.start wants 2 recorded), and a
                       # scene with nothing to say where she stands said as such, not as "พูด" -- only a
                       # scene that goes somewhere reads `on_stand_th` (scene_text's `spot_id`)
                       "say_on_walk": sc["say_on_walk"],
                       # out of the box first, arms down, then the wave and the line (owner 23 Nov)
                       "walk_out_of_box": bool(sc.get("walk_out_of_box")),
                       "has_start": bool(sc.get("on_start_th")), "has_arrive": bool(sc.get("on_arrive_th")),
                       "roam_stops": list((sc["roam"] or {}).get("stops") or []) if sc["roam"] else [],
                       "quiet_standing": bool(sc.get("spot") or sc.get("stops") or sc["roam"])
                                         and "on_stand_th" in sc and not sc.get("on_stand_th"),
                       # the "สั่งเอง" card's "พูด" (owner 14 Nov): "arrive" | "stand" | None, and
                       # what that line is (manual_preview: her sentence, or what it asks of her)
                       **_manual_fields(sc)})
    out.update(scenes=scenes, perform=perform_view(), manual=manual_snapshot(doc),
               current={"scene": STATE["scene"], "stop": STATE["stop"], "since": STATE["since"]})
    return out


def gesture_th(action: str | None) -> str | None:
    if not action:
        return None
    from app import robot_contracts as rc

    return GESTURE_TH.get(action) or rc.BODY_LABELS.get(action) or action


_QUOTED = re.compile(r"«([^»]+)»")


def says_th(sc: dict) -> str | None:
    """The sentence the scene's own line quotes, for the staff page ("Emma จะพูด: ..."). Only a
    quoted one: the rest of a line is an instruction to her, not words anyone will hear."""
    line = (sc.get("on_arrive_th") or sc.get("on_start_th")) if _here(sc) else \
        (sc.get("on_start_th") or sc.get("on_arrive_th"))
    found = _QUOTED.search(line or "")
    return found.group(1).strip() if found else None


def perform_view() -> dict | None:
    """STATE["perform"] for the phones, with its age."""
    p = STATE["perform"]
    if not p:
        return None
    view = {k: p.get(k) for k in ("scene", "title_th", "phase", "said", "gesture", "gesture_ok",
                                   "gesture_error_th", "pageant_ok", "pageant_error_th", "recorded")}
    view["gesture_th"] = gesture_th(p.get("gesture"))
    view["ago_s"] = max(0, int(time.time() - float(p.get("at") or time.time())))
    return view


def _mic_closed() -> bool:
    """With a call up, what its page said of its mic (a camera's greeting opens a held call's
    mic: the hold alone made the phone say "closed, tap it", and the tap closed it -- review
    6 Nov). With no call, the hold: the next call opens muted."""
    try:
        from app import session as session_module
        from app import wake

        if session_module._active is not None:
            return bool(getattr(session_module._active, "_page_muted", False))
        return bool(wake.mic_held())
    except Exception:                                    # noqa: BLE001 -- a warning, not a gate
        return False


def mode_snapshot() -> dict:
    """The two modes, as both phones show them: `ceremony_app` = the Emma Ceremony app is on
    the robot (app/ceremony.py; `ceremony` says the same, for the readers it had before 9 Nov).
    Says so when a promise is not being kept: a mic muted this morning means she hears nobody,
    and nobody could tell from here."""
    on = ceremony_now()
    warnings = []
    if _mic_closed():
        # Somebody tapped "mute" at the robot this morning: she would not hear the
        # host, and nothing on this phone would say why.
        warnings.append("ไมค์ปิดอยู่ที่หน้าหุ่น — แตะไมค์ที่หน้าจอหุ่นก่อน Emma จึงจะได้ยิน")
    tv = STATE["tv"]
    if tv and not tv["ok"]:
        warnings.append("เปิดจอใหญ่ไม่สำเร็จ" if tv["want"] == "open" else "ปิดจอใหญ่ไม่สำเร็จ")
    from app import event_roam

    cues = cue_only()
    live = live_state()
    # cue-only: she answers nobody, named or not -- except inside the live window
    return {"ceremony": on, "ceremony_app": on, "needs_name": on and (not cues or live is not None),
            "cue_only": cues, "live": live,
            "tv": tv, "warnings": warnings, "roam": event_roam.snapshot()}


def _face_state() -> dict:
    """The robot face, and which app's call is up (`event_call` = Emma Event's, `ceremony_call`
    = Emma Ceremony's)."""
    out = {"connected": False, "event_call": False, "ceremony_call": False}
    try:
        from app import robot_face_link
        from app import session as session_module

        out["connected"] = bool(robot_face_link.live_faces())
        active = session_module._active
        out["event_call"] = bool(active is not None and getattr(active, "profile", None) == "event")
        out["ceremony_call"] = bool(active is not None and getattr(active, "profile", None) == "ceremony")
    except Exception:                                    # noqa: BLE001
        pass
    return out
