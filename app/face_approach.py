"""
The robot sees a guest who scanned at the booth, walks up, greets them by name
and offers a hand (owner, 4 Nov 2026: "เวลาที่หุ่นเห็นหน้าคนนี้ก็เดินเข้าไปทักเลย
พร้อมเชคแฮน"; launch event 25-26 Nov, floor 1). Design and bench plan:
docs/emma-face-approach.md. Never run on the robot yet; off (FACE_APPROACH).

A robot walking toward somebody who did not ask is the one thing here that can
hurt a person, so this is built as a list of reasons not to:

* A person is watching. Only while the event staff page is alive AND staff
  pressed "on" on it (`armed`). The page going quiet turns it OFF, not just
  paused: reopening a phone must not silently start a machine walking. Every
  human stop (voice, desk, staff, the page or the robot face lost) turns it off
  the same way, until somebody presses on again.
* It walks with app/event_walk.py and nothing else: board_walk with one MoveTo,
  so every stop path, the dead man's switches, the wheel fence and the arm gate
  of a server-run walk apply without a line here. Voice can't start it: the
  camera does, and only inside staff's "on".
* Who: only a track's own confirmation (`observe`), not the nearest face.
* Where: `plan`, from pose + the face's bearing through the measured lens + the
  lidar's range. The face says which way and roughly how far; the lidar must
  find somebody standing there. Its own straight corridor must be clear NOW,
  because the board's floor-1 map lacks the furniture (a planner goal can route
  through a table, 28 Oct): the goal has to be reachable in a straight line, and
  a walk that leaves that line is stopped (OFF_LINE_M) -- a detour means the
  planner knows something the map got wrong. The goal and the line must lie
  inside the area staff marked with the robot itself (`add_corner`).
* Never chasing: the person must stand still before it leaves (`_preflight`) and
  a person who moves away while it walks ends the walk. One attempt per person
  per FACE_APPROACH_PERSON_COOLDOWN_S, counted when it starts, whatever came of it.
* Afterwards it stays where it stopped. A walk back would be one more autonomous
  walk through a crowd, with nobody to walk to; staff's next scene moves it, and
  a scene wins at once (start_scene's event_walk.stop("replaced")).

**Anybody she sees (FACE_APPROACH_ANYONE, owner 5 Nov: "เวลาเห็นคนก็ให้หุ่นเดินไปทัก ...
ถ้ามีในข้อมูลเราก็ทัก"; docs/emma-face-approach.md section 9).** The same road and every gate
above, with one thing different: who starts it. Not a face, which the 19 deg lens
sees in 5% of the circle, but the lidar: somebody who walked in on their own feet
and has stood for a moment (lidar_people.Follower). She has no name on the way, so
she decides nothing on the way: at the stop she gives the frames back to the door
camera (`_look_then_greet`), and its rules (the bars, margin, alone in frame,
consent) say whether there is a name. No face at all in FACE_APPROACH_CHECK_S and
somebody still there: a plain greeting. "ทักไม่มีชื่อไม่มีวันผิด ชื่อผิดมี".
A face too far off to greet (`door_min_px`) is the owner's "เห็นไม่ชัด": the same walk,
started by the camera instead (`_note_far`). The lidar must find them there too.

**The camera sees a person (PERSON_VISION, owner 5 Nov: "ให้กล้องช่วยตรวจดูไม่ได้หรอ แบบเห็นเป็นคน ก็เข้าไปทัก";
of a chair somebody carried in: "ใช้กล้องช่วย"; docs/emma-face-approach.md section 11).** Lidar and camera each
know what the other cannot: where a thing stands, and what it is. So:
* A person's shape in the lens for PERSON_VISION_LOOKS looks in about the same place, no face needed
  (`_note_body`): somebody to walk up to, if the lidar finds something person-sized there.
* What only the lidar found gets looked at before she takes a step (`_look_first`): the head turns
  that way, slowly and once, and on past what the head reaches the base turns. A person's shape
  or a face: on. Nothing in two looks, level and lower: not a person, nothing said, not
  looked at again for a while (`_not_person`). This is what stops the chair.
* Somebody who stood still all along is on nobody's list: background to the Tracker, and out of a 19 deg lens.
  So what the lidar has in a place the map says is free floor, and that she could walk to, gets a
  glance too, at most one per FACE_APPROACH_LOOK_EVERY_S (`_scan_candidate`).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
import time
from pathlib import Path

from app import event_walk, turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.face_approach")

#: The event_walk spot of these walks: event_walk.Run.can_resume refuses it.
SPOT = event_walk.NO_RESUME_SPOT

#: The robot, front to centre (0.70 x 0.60 m, measured; the lidar is treated
#: as its centre -- where exactly on the body it sits has not been measured).
HALF_LENGTH_M = 0.35
#: The person is told by nothing: a stop of less than this is not "in front of
#: them" but "in their face". The setting can only ask for more.
STOP_FLOOR_M = 1.0
#: Hard ceilings whatever .env says.
MAX_TRAVEL_M = 4.0
MAX_SPEED = 0.3
#: Closer than this, no walk: she greets from where she is.
MIN_TRAVEL_M = 0.30
#: Somebody further round than this: the base turns on the spot first (board_walk's own
#: arms-room check), and then walks. The MoveTo would turn too, with no such check.
TURN_FIRST_DEG = 15.0
#: At the stop, further off the line than this and the lens (+-9.5 deg) may not hold them.
REAIM_DEG = 7.0
#: Walking: when the plan's goal would now leave her under the floor from somebody who came
#: closer, she stops this far short of the floor (0.25 m/s for one WATCH_EVERY_S and the
#: board's stop). A person who stayed put leaves exactly the floor at the goal: not that.
MEET_MARGIN_M = 0.2
MEET_TOLERANCE_M = 0.05
#: How wide a corridor must be clear: the arms' 0.78 m and 15 cm a side.
CORRIDOR_HALF_M = 0.39 + 0.15
#: The board refuses a goal < ~0.55 m from an obstacle (28 Oct); a refused
#: goal is a stop in front of a guest with nothing said.
GOAL_CLEAR_M = 0.60
#: The robot's half width from the edge of the marked area, all along the line.
AREA_MARGIN_M = 0.40
#: A spot turn sweeps the corners: half the diagonal of 0.70 x 0.78 + 3 cm. The lidar's knee-height
#: plane does not see a table top, and an arm hit one on 28 Oct: staff's area is what says there is none.
TURN_EDGE_M = 0.55
#: The strip ahead while she walks. The floor protected only the person she walks to: somebody else
#: 0.2 m in front did not stop the walk, nor did the target closing past TRACK_RADIUS_M (review 5 Nov).
STRIP_NEAR_M = 0.25
STRIP_FAR_M = HALF_LENGTH_M + STOP_FLOOR_M - 0.05
#: A return in the strip this close to the person she walks to is them.
STRIP_TARGET_M = 0.35
#: Which lidar returns can be the person. The face is a rough range (a child, a
#: big head) and the lidar-to-camera angle is unmeasured until LIDAR_PEOPLE's
#: check holds; legs half a metre off the face's line at 4 m are 7 deg.
CONE_DEG = 7.0
RANGE_RATIO = (0.6, 1.6)
#: A face this wide, metres: range from pixels only as a cross-check.
FACE_M = 0.16
#: A person's shape this wide, metres (shoulders and arms, front on), and how far off the lidar's range
#: the box may say they are: sideways is narrower, sitting is wider, a box at the frame's edge is cut.
#: Only to tell somebody far behind a chair from the chair (review 6 Nov: 15 m behind = 92 px against
#: the chair's 2.2 m; a person at 2.2 m is ~630 px).
BODY_M = 0.45
BODY_RANGE_RATIO = (0.4, 2.0)
#: Before leaving: this many looks this far apart, all within MOVING_M.
PREFLIGHT_LOOKS = 3
PREFLIGHT_GAP_S = 0.5
MOVING_M = 0.25
#: While walking, how often it looks; the person gone for LOST_S or moved
#: MOVED_M from where they stood ends the walk.
WATCH_EVERY_S = 0.25
LOST_S = 1.5
MOVED_M = 0.60
TRACK_RADIUS_M = 0.90
#: Left the straight line by this much = the planner went round something.
OFF_LINE_M = 0.30
#: Consecutive failed board reads before a walk is stopped for them.
READ_FAILS = 3
#: Arrived: two poses this far apart, this close together = stopped.
SETTLE_GAP_S = 0.5
SETTLE_M = 0.01
SETTLE_DEG = 1.0
SETTLE_MAX_S = 5.0
#: The camera's frame after the head moved is the head's old direction
#: (head_follow CAMERA_LAG_S + SETTLE_S).
HEAD_SETTLE_S = 1.3
#: A candidate older than this is somebody who was there.
FRESH_S = 1.5
#: How often the stop looks at what the door camera decided.
CHECK_POLL_S = 0.1
#: Frames that disagree or drop out for this long end a track's confirmation.
TRACK_GAP_S = 2.0
#: One refusal per reason this often in the turn log, not one every tick.
LOG_EVERY_S = 30.0
#: After a refusal, this long before asking again: the same answer read from the
#: board 2.5 times a second is load on the link the joystick and walks share.
RETRY_AFTER_S = 2.0
#: In a dry run the same person may be measured again after this long.
DRY_RUN_REPEAT_S = 60.0
#: The handshake waits for the arm's own "not walking" at most this long.
ARM_WAIT_S = 20.0
#: Anybody-she-sees road. Somebody she has walked up to or greeted stands within this of
#: where they stood: the same person, for FACE_APPROACH_PERSON_COOLDOWN_S. The lidar has no
#: other way to know them; somebody who walks further and stands again is the face check's.
SAME_SPOT_M = 1.5
#: A track she could not go to (blocked, too far) is asked about again this often: the
#: answer comes from the board, and the person standing there has not changed.
TRACK_RETRY_S = 10.0
#: From the lidar's own range: the person's two legs, not a table leg beside them.
PERSON_NEAR_M = 0.6
#: A face under the door's bar and at least this wide (~6 m through the 19 deg lens; her
#: longest walk's reach is ~4.7 m) for this many looks: somebody to walk up to.
#: Also the least that counts as "a face" at the stop. The detector's smallest answers are not
#: faces: of 425 it found in the walk pictures (640 input), 184 are under 15 px, clustered at the
#: bottom of the frame, and the robot's refusals carry a constant 9-11 px "seen" (560 rows,
#: 10 Oct - 5 Nov). Such a thing in view must never read as a person there.
FAR_MIN_PX = 80
FAR_LOOKS = 3
#: The head, real degrees a percent of pan, + = her left (dock ladder 4 Nov,
#: docs/robot-servo-limits-2026-10-31.md; the 10 % sweep reads 0.23 by counting the gears' slack).
#: Too high aims short, and the lens's +-9.5 deg still holds them; too low would overshoot.
PAN_DEG_PER_PCT = 0.37
#: A look past what the head reaches, by this much, is a turn of the base: inside the lens's half width.
LOOK_PAST_DEG = 6.0
#: One tilt of a look: time to get there (HEAD_SETTLE_S, in `observe`) and for its frames, counted
#: from when the head took it. A head that has not in this long is held by something else.
LOOK_TILT_S = 3.0
LOOK_WAIT_S = 4.0
#: What answers a look is near the middle of the picture, this far either side of where she aimed: the
#: lens is 19 deg, and a face at its edge is somebody else (review 5 Nov: a chair at 10 deg was
#: confirmed by a face in the lens the head never left).
LOOK_MIDDLE_DEG = 5.0
LENS_DEG = 19.0
#: However the glances are set, at most one look this often (ch8 smoked on 12 Sep). Out, a nod, back.
LOOK_MIN_GAP_S = 10.0
#: This often while looking, and this many frames with a person's shape: one box is not a person.
LOOK_FRAME_S = 0.4
LOOK_FRAMES = 2
#: The same place in the picture, look to look, as a share of its width. And looks further apart than
#: this are not "running": a walk or a look in between, and one box would finish an old count.
BODY_SAME_PLACE = 0.15
BODY_GAP_S = 3.0
#: The cone for a box: its half width (the lens is 19 deg) and this.
BODY_CONE_EXTRA_DEG = 1.5
#: A thing the lidar has, this close to what the map has, is what the map has.
MAP_CLEAR_M = 0.2
#: Looked at, and nobody there, within this of the spot: for this long, or longer for what she walked
#: up to and never found a face on (a draped thing the model calls a person; the walk is the expensive part).
NOT_PERSON_M = 0.5
NOT_PERSON_S = 300.0
NO_FACE_KEEP_S = 3600.0
#: A glance she never made, because she could not have walked there anyway.
UNREACHED_KEEP_S = 120.0
#: After turning the base to look, this long before she does it again: no spinning in the middle of a crowd.
TURN_LOOK_GAP_S = 45.0
#: How often the lidar's list is gone through for something to glance at.
SCAN_ASK_S = 1.0
#: The board's map is read for the line of sight this often (0.4 MB; it does not change
#: during a walk-free morning).
MAP_TTL_S = 300.0
#: Where faces sit in the head camera (114 real faces, 4 Nov, tilt 0): 0.15 m under its
#: axis at every distance, so a level camera (the fit's slope is nothing). The tilt
#: that centres one: atan(0.15 / range). And the head turns ~0.75 deg a percent of tilt
#: (dock ladder 4 Nov, docs/robot-servo-limits-2026-10-31.md).
FACE_DROP_M = 0.15
TILT_DEG_PER_PCT = 0.75
#: No face for this long at the first tilt: one more look, lower (somebody short or sitting).
SECOND_LOOK_S = 2.5
#: A face in view but no decision yet: the door's own frames get this much longer.
FACE_MORE_S = 3.0
#: Still there at the end of the look: within this of where they stood.
STILL_THERE_M = 0.5
#: How often the look reads where the lidar has them.
POSITION_EVERY_S = 1.0
#: Stop reasons that are a person (or a dead man's switch) saying stop: the
#: feature turns off until staff press on again. "replaced" is staff moving her
#: on, and leaves it on.
#: "app_stop": B or the operator screen on the robot (the app's own 60 s latch; event_walk reads it).
HUMAN_STOPS = {"staff", "voice", "desk", "control_lost", "face_gone", "shutdown", "app_stop"}
#: How long a line rung for a look or a walk may take to come up (the dial is 1-3 s: voice_ready rows).
LINE_WAIT_S = 8.0

REASONS_TH = {
    "off": "ปิดอยู่ (FACE_APPROACH)", "walk_off": "การเดินของงานปิดอยู่ (EVENT_WALK_ENABLED)",
    "ceremony": "อยู่ในพิธีเปิด", "floor_hazard": "ทางตรงผ่านจุดสะดุดบนพื้น",
    "not_armed": "ทีมงานยังไม่ได้เปิด หรือหน้าคุมหลุด", "no_area": "ยังไม่ได้กำหนดพื้นที่เดิน",
    "not_calibrated": "ยังไม่รู้มุมกล้อง", "consent": "ไม่มีความยินยอมของคนนี้",
    "person_cooldown": "เพิ่งเข้าหาคนนี้ไปแล้ว", "gap": "เพิ่งเดินเข้าหาคนอื่นไป",
    "scene_recent": "ทีมงานเพิ่งเปลี่ยนฉาก", "walking": "หุ่นกำลังเดินอยู่",
    "nav_unknown": "อ่านสถานะการเดินไม่ได้", "stop_latched": "เพิ่งมีการกดหยุด",
    "desk_stopped": "เพิ่งกดหยุดจากหน้าคุมหุ่น", "arm_locked": "แขนถูกล็อกอยู่",
    "conversation": "Emma กำลังคุยอยู่", "no_chassis": "ไม่ได้ต่อบอร์ดนำทาง",
    "docked": "หุ่นอยู่บนแท่น", "dock_unknown": "อ่านสถานะแท่นไม่ได้", "board_busy": "บอร์ดกำลังทำงานอื่น",
    "board_unreadable": "อ่านบอร์ดไม่ได้", "driving": "มีคนขับหุ่นอยู่", "fenced": "มีการเดินอื่นถือล้ออยู่",
    "low_quality": "หุ่นไม่มั่นใจตำแหน่งตัวเอง", "no_scan": "ไม่มีข้อมูลไลดาร์",
    "no_lidar_target": "ไลดาร์ไม่เจอคนตรงที่กล้องเห็น", "too_far": "ไกลเกินไป",
    "path_blocked": "ทางตรงมีของหรือคนขวาง", "goal_tight": "จุดหยุดแคบเกินไป", "no_turn_room": "ที่หมุนตัวไม่พอ",
    "outside_area": "นอกพื้นที่ที่กำหนด", "moving_target": "คนนั้นกำลังเดินอยู่", "gone": "คนนั้นไปแล้ว",
    "lost_target": "ไลดาร์หาคนนั้นไม่เจอแล้ว", "moved": "คนนั้นขยับออกไป", "off_line": "หุ่นออกนอกทางตรง",
    "staff_off": "ทีมงานปิดแล้ว", "not_settled": "หุ่นยังไม่หยุดนิ่ง", "lost_on_arrival": "ถึงแล้วแต่คนนั้นไม่อยู่",
    "walk_refused": "เริ่มเดินไม่ได้", "scene_changed": "ทีมงานเปลี่ยนฉาก",
    "no_lidar": "ไลดาร์ปิดอยู่ (LIDAR_PEOPLE)", "no_map": "อ่านแผนที่บอร์ดไม่ได้",
    "behind_wall": "คนนั้นอยู่หลังกระจกหรือผนังในแผนที่", "keep_out": "ทางตรงเข้าเขตที่ห้ามเดิน",
    "door_greeted": "ทักจากตรงนี้ไปแล้ว ไม่ต้องเดิน", "already_greeted": "คนนี้ทักไปแล้ว",
    "met": "คนนั้นเดินเข้ามาหา", "not_a_person": "กล้องดูแล้วไม่ใช่คน", "no_vision": "โมเดลดูคนใช้ไม่ได้",
    "no_line": "เปิดสายไม่ติด (หัวหันไม่ได้ และไม่มีใครได้ยินคำว่าหยุด)", "turned_recently": "เพิ่งหันตัวไปดู",
    "mic_held": "ปิดไมค์อยู่ ไม่เดินเข้าหาใคร", "app_stop": "กดหยุดที่ตัวหุ่น",
    "no_frames": "หันไปแล้วแต่ไม่ได้ภาพมาดู", "on_the_map": "ตรงนั้นเป็นของที่มีในแผนที่ (ผนัง/เฟอร์นิเจอร์)",
    "scanning": "กำลังสแกนหน้าใครอยู่", "head_unknown": "ไม่รู้ว่าหัวหันไปทางไหน (สายเพิ่งจบ)", "box_too_far": "กล่องคนในภาพเล็กเกินระยะที่ไลดาร์เจอ (คนไกลข้างหลัง)",
    "looked_recently": "เพิ่งหันหัวไปดู", "head_busy": "หัวกำลังตามคนอื่นอยู่",
}
for _key, _text in REASONS_TH.items():
    event_walk.REASONS_TH.setdefault("approach_" + _key, "หยุดเข้าหาแขก: " + _text)

_eyes_lock = threading.Lock()
STATE: dict = {
    "staff_on": False, "phase": "idle",  # idle | planning | walking | greeting | checking
    "by_name": {}, "last_any": -math.inf, "task": None, "loop": None,
    "last": None, "logged": {}, "armed_at": -math.inf, "stop_seen": None, "retry_at": -math.inf,
    "kind": None, "spots": [], "door_at": None, "map": None, "map_at": -math.inf,
    "not_people": [], "looked_at": -math.inf, "turned_at": -math.inf, "scan_at": -math.inf, "head_at": -math.inf,
    "face_lost_at": None,
}
_eyes: dict = {"at": -math.inf, "tracks": None, "candidate": None, "face_at": None, "suppressed_at": None,
               "far": None, "far_tracks": None, "far_tried": -math.inf,
               "body": None, "body_run": [], "body_at": -math.inf, "body_tried": -math.inf, "body_seen": -math.inf,
               "seen_n": 0, "counting": False, "look_tol": LOOK_MIDDLE_DEG,
               "look_range": None, "check_range": None, "looked_n": 0}


def reset() -> None:
    task = STATE.get("loop")
    if task is not None and not task.done():
        task.cancel()
    STATE.update(staff_on=False, phase="idle", by_name={}, last_any=-math.inf, task=None, loop=None,
                 last=None, logged={}, armed_at=-math.inf, stop_seen=None, retry_at=-math.inf,
                 kind=None, spots=[], door_at=None, map=None, map_at=-math.inf,
                 not_people=[], looked_at=-math.inf, turned_at=-math.inf, scan_at=-math.inf, head_at=-math.inf,
                 face_lost_at=None)
    _eyes.update(at=-math.inf, tracks=None, candidate=None, face_at=None, suppressed_at=None,
                 far=None, far_tracks=None, far_tried=-math.inf,
                 body=None, body_run=[], body_at=-math.inf, body_tried=-math.inf, body_seen=-math.inf, seen_n=0,
                 counting=False, look_tol=LOOK_MIDDLE_DEG, look_range=None, check_range=None, looked_n=0)


# ---- the area staff marked with the robot ----------------------------------------------

def _area_file() -> Path | None:
    from app import event_host

    event = event_host._path()
    return None if event is None else event.with_name(event.stem + ".approach.spots.json")


def area() -> list[tuple[float, float]]:
    """The corners, in the order they were marked. [] = nowhere may be walked to.
    Written next to the event's spots (gitignored *.spots.json: deploy leaves it)."""
    import json

    file = _area_file()
    if file is None:
        return []
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
        return [(float(x), float(y)) for x, y in doc.get("corners") or []]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def _save_area(corners: list) -> None:
    import json

    file = _area_file()
    if file is None:
        raise RuntimeError("no EVENT_FILE")
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"about": "where the robot may walk up to a guest: corners staff marked "
                                        "with the robot itself (app/face_approach.py)",
                               "corners": corners}, indent=1), encoding="utf-8")
    tmp.replace(file)


async def add_corner(clear: bool = False) -> dict:
    """The robot's pose now is the next corner of the area. Staff drive it round
    the walkway: the map cannot be asked where the tables are (it lacks them)."""
    from app import robot_chassis

    if clear:
        _save_area([])
        return {"ok": True, "corners": 0}
    if event_walk.walking():
        return {"ok": False, "error": "walking", "error_th": REASONS_TH["walking"]}
    try:
        pose = await asyncio.wait_for(robot_chassis.pose(), 5.0)
        quality = await asyncio.wait_for(robot_chassis.localization_quality(), 5.0)
        x, y = float(pose["x"]), float(pose["y"])
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "no_pose", "error_th": "อ่านตำแหน่งหุ่นไม่ได้"}
    if quality is None or quality < settings.face_approach_min_quality:
        return {"ok": False, "error": "low_quality", "error_th": REASONS_TH["low_quality"]}
    corners = [list(c) for c in area()] + [[round(x, 3), round(y, 3)]]
    _save_area(corners)
    return {"ok": True, "corners": len(corners)}


def inside(poly, x: float, y: float) -> bool:
    if len(poly) < 3:
        return False
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def edge_distance(poly, x: float, y: float) -> float:
    best = math.inf
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        dx, dy = x2 - x1, y2 - y1
        share = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1.0)))
        best = min(best, math.hypot(x - x1 - share * dx, y - y1 - share * dy))
    return best


# ---- the lens -------------------------------------------------------------------------

def lidar_bearing(cx: float, frame_w: int, head_pan: float, mapping: dict | None) -> float:
    """A picture column as a lidar angle: the head camera's picture is mirrored
    and 19 deg wide (4 Nov), the head may be turned, and the head's own angle is
    recorded in the assumed lens's units. `map_check.lens` is that conversion
    (what the walk pictures use), so there is one of it."""
    from app import facewatch, map_check

    lens = map_check.lens(mapping)
    picture = facewatch.bearing_deg(cx, frame_w, lens["hfov"])
    head = _head_real_deg(head_pan)
    if head is None:
        return lens["a"] * (picture + lens["head"](head_pan)) + lens["b"]
    return lens["a"] * picture + lens["b"] + head


def _head_real_deg(head_pan: float) -> float | None:
    """The head's recorded angle (head_follow's units: percent x the sweep's degrees per 100) in real
    degrees, + = her left, at PAN_DEG_PER_PCT -- the one `_pan_for` aims with (review 6 Nov: reading
    through the sweep's 0.23 deg/% while aiming at 0.37, a face centred at pan 60 read 13.5 deg against
    22.2 and the box road walked to another thing in the cone). None without a sweep's scale."""
    from app import head_follow

    full = head_follow.full_deg()[0]
    if not full:
        return None
    return float(head_pan) * 100.0 / float(full) * PAN_DEG_PER_PCT


def _focal_px(frame_w: float, hfov_deg: float) -> float:
    return (float(frame_w) / 2.0) / math.tan(math.radians(hfov_deg / 2.0))


def _fits_range(width_m: float, px: float, frame_w: float, range_m, ratio) -> bool:
    """Does something `px` wide, `width_m` across, stand about `range_m` away? True when there is no
    range to hold it to."""
    if range_m is None or range_m <= 0:
        return True
    hfov = true_hfov(_mapping()) or LENS_DEG
    implied = width_m * _focal_px(frame_w, hfov) / max(float(px), 1.0)
    return ratio[0] * float(range_m) <= implied <= ratio[1] * float(range_m)


def true_hfov(mapping: dict | None) -> float | None:
    """The lens's real width, or None: measured, or from the lidar's check.
    The assumed 70 is not an answer to "how far away is that face"."""
    mapping = mapping or {}
    return mapping.get("hfov_measured") or mapping.get("hfov_from_lidar") or None


def face_range_m(px: float, frame_w: int, hfov_deg: float) -> float:
    focal = (frame_w / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    return FACE_M * focal / max(px, 1.0)


# ---- the plan: no I/O -----------------------------------------------------------------

def _wrap(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def sight_blocked(grid, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Does the board's map have a wall on the straight line from her to that person?
    11-14% of the lidar's returns in the lobby come from beyond one (4 Nov): the lidar
    sees through the glass, and a person out there, or a reflection, stands on the other
    side of it. The corridor test cannot say: the glass is what it sees through. So the
    map, which has the glass and keeps the multimedia room's door shut. A wall the map has
    wrong costs one walk not taken, the cheap direction."""
    import numpy as np

    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0.6:
        return False                     # her own cell and theirs say nothing
    n = max(2, int(length / max(grid.res * 0.8, 0.02)))
    t = np.linspace(0.3 / length, 1.0 - 0.3 / length, n)
    cols = ((x0 + (x1 - x0) * t - grid.ox) / grid.res).astype(int)
    rows = ((y0 + (y1 - y0) * t - grid.oy) / grid.res).astype(int)
    occ = grid.occ()
    keep = (rows >= 0) & (cols >= 0) & (rows < occ.shape[0]) & (cols < occ.shape[1])
    return bool(occ[rows[keep], cols[keep]].any())


def find_person(points, bearing_deg: float, near_m: float, far_m: float,
                cone_deg: float = CONE_DEG, nearest: bool = False):
    """The person at that bearing and range in a robot-frame scan, or None: a
    leg- or body-sized cluster (lidar_people's own test), closest to the line.
    `nearest`: the closest of them instead. A box in the lens has no range: what the lidar has
    behind them is not them, and what stands in front is what she must stop for anyway."""
    import numpy as np

    from app import lidar_people

    if not len(points):
        return None
    rng = np.hypot(points[:, 0], points[:, 1])
    ang = np.arctan2(points[:, 1], points[:, 0])
    # The band's clusters first, the cone after (review 6 Nov): cut to the cone first, a wall 3 m
    # ahead came out 0.73 m wide -- person-sized -- and the box road walked at it.
    keep = (rng >= near_m) & (rng <= far_m)
    if not keep.any():
        return None
    order = np.argsort(ang[keep])
    seq = [(float(a), float(d)) for a, d in zip(ang[keep][order], rng[keep][order])]
    # A person counts when any of them is in the cone (their half width at their range), so somebody at
    # the cone's edge is still found -- "moved" at once, not "lost" a second and a half later.
    found = [p for p in lidar_people._people(lidar_people._clusters(seq), 0.0)
             if abs(_wrap(p.bearing - bearing_deg))
             <= cone_deg + math.degrees(math.atan2(p.width / 2.0, max(p.distance, 0.1)))]
    if not found:
        return None
    if nearest:
        return min(found, key=lambda p: p.distance)
    return min(found, key=lambda p: abs(_wrap(p.bearing - bearing_deg)))


def plan(points, pose: dict, bearing_deg: float, face_m: float, corners,
         stop_m: float | None = None, max_m: float | None = None,
         speed: float | None = None, lidar_range: bool = False, grid=None, spot_turn: bool = False,
         band: tuple[float, float] | None = None, cone_deg: float = CONE_DEG) -> dict:
    """Where to go, or why not. {"why": key|None, ...}. `points` = robot-frame
    lidar returns (robot_lanes.scan_points), `pose` = the board's (radians).

    `lidar_range`: `face_m` is the lidar's own range to somebody the lidar found, not
    a guess from a face's size: a tighter band. `grid`: the board's map, for the line of
    sight to them (`sight_blocked`). `spot_turn`: somebody within reach gets a turn, not nothing.
    `band`, `cone_deg`: a person's shape in the lens says which way and nothing of how far: the
    nearest person-sized thing in that cone, `face_m` unused.

    `close`: the person is near enough to greet without a step."""
    import numpy as np

    from app import event_host, robot_lanes as rl

    stop = max(STOP_FLOOR_M, float(settings.face_approach_stop_m if stop_m is None else stop_m))
    limit = min(MAX_TRAVEL_M, float(settings.face_approach_max_m if max_m is None else max_m))
    speed = min(MAX_SPEED, float(settings.face_approach_speed if speed is None else speed))
    if len(points) < 50:
        return {"why": "no_scan"}
    if band is not None:
        near, far = band
    elif lidar_range:
        near, far = face_m - PERSON_NEAR_M, face_m + PERSON_NEAR_M
    else:
        near, far = face_m * RANGE_RATIO[0], face_m * RANGE_RATIO[1]
    face_m = None if band is not None else round(face_m, 2)
    person = find_person(points, bearing_deg, near, far, cone_deg=cone_deg, nearest=band is not None)
    if person is None:
        return {"why": "no_lidar_target", "face_m": face_m}
    r, phi = person.distance, math.radians(person.bearing)
    x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0)
    heading = yaw + phi
    travel = r - stop - HALF_LENGTH_M
    out = {"why": None, "range_m": r, "bearing_deg": person.bearing, "face_m": face_m,
           "person": (x + r * math.cos(heading), y + r * math.sin(heading)),
           "travel_m": round(max(travel, 0.0), 2), "start": (x, y)}
    if grid is not None and sight_blocked(grid, x, y, *out["person"]):
        return {**out, "why": "behind_wall"}
    poly = list(corners)
    # She will turn where she stands (a walk's first move, or all she does for somebody within
    # reach): the lidar cannot see a table top, so the marked area has to say there is room.
    turns_here = abs(person.bearing) >= TURN_FIRST_DEG
    if travel < MIN_TRAVEL_M:
        out["close"] = True
        out["face_deg"] = round(math.degrees(heading), 1)
        if spot_turn and turns_here and not _room_to_turn(poly, x, y):
            return {**out, "why": "outside_area"}
        return out
    if travel > limit:
        return {**out, "why": "too_far"}
    # The straight corridor, now. Not asked of the map: it lacks the tables.
    u = points[:, 0] * math.cos(phi) + points[:, 1] * math.sin(phi)
    v = -points[:, 0] * math.sin(phi) + points[:, 1] * math.cos(phi)
    rng = np.hypot(points[:, 0], points[:, 1])
    in_way = (rng > 0.25) & (u > 0.0) & (u < travel + HALF_LENGTH_M + 0.15) & (np.abs(v) < CORRIDOR_HALF_M)
    if in_way.any():
        return {**out, "why": "path_blocked", "blocked_at_m": round(float(u[in_way].min()), 2)}
    # ...and on past the goal as far as the strip ahead will look on arrival (`_watch`), or she sets
    # out for a walk she will break off at the end. Not the person's own legs: staggered, the
    # front one is nearer than their middle.
    near_end = (u >= travel + HALF_LENGTH_M + 0.15) & (u < travel + STRIP_FAR_M) & (np.abs(v) < CORRIDOR_HALF_M)
    near_end &= np.hypot(points[:, 0] - r * math.cos(phi), points[:, 1] - r * math.sin(phi)) > STRIP_TARGET_M + 0.1
    if near_end.any():
        return {**out, "why": "path_blocked", "blocked_at_m": round(float(u[near_end].min()), 2)}
    gx, gy = x + travel * math.cos(heading), y + travel * math.sin(heading)
    gu, gv = travel * math.cos(phi), travel * math.sin(phi)
    if float(np.hypot(points[:, 0] - gu, points[:, 1] - gv).min()) < GOAL_CLEAR_M:
        return {**out, "why": "goal_tight"}
    if abs(person.bearing) >= 3.0:
        sweep = phi + math.copysign(math.radians(3.0), phi)
        if rl.turn_sweep_gap(points, sweep, width=rl.ROBOT_WIDTH) < 0.04:
            return {**out, "why": "no_turn_room"}
    if len(poly) < 3:
        return {**out, "why": "no_area"}
    steps = max(2, int(travel / 0.1))
    line = [(x + travel * i / steps * math.cos(heading), y + travel * i / steps * math.sin(heading))
            for i in range(steps + 1)]
    if not all(inside(poly, px, py) and edge_distance(poly, px, py) >= AREA_MARGIN_M for px, py in line):
        return {**out, "why": "outside_area"}
    if turns_here and not _room_to_turn(poly, x, y):
        return {**out, "why": "outside_area"}
    # Under the laser, so the corridor above found nothing there; only the file knows.
    if event_host.hazard_on_line(x, y, gx, gy) is not None:
        return {**out, "why": "floor_hazard"}
    # The multimedia room: hers to stay out of, whatever a person standing in its doorway says.
    if event_host.keep_out_on_line(x, y, gx, gy):
        return {**out, "why": "keep_out"}
    out.update(goal=(gx, gy), face_deg=round(math.degrees(heading), 1), speed=speed)
    return out


def _room_to_turn(poly, x: float, y: float) -> bool:
    return len(poly) >= 3 and inside(poly, x, y) and edge_distance(poly, x, y) >= TURN_EDGE_M


def strip_ahead(points, pose: dict, p: dict):
    """The returns in the strip ahead along her line, robot frame, or an empty array: within
    the floor of her front, as wide as the plan's corridor. Against the line, not her heading:
    it holds through the spot turn that starts a walk."""
    import numpy as np

    yaw = float(pose.get("yaw") or 0.0)
    phi = math.atan2(p["goal"][1] - p["start"][1], p["goal"][0] - p["start"][0]) - yaw
    u = points[:, 0] * math.cos(phi) + points[:, 1] * math.sin(phi)
    v = -points[:, 0] * math.sin(phi) + points[:, 1] * math.cos(phi)
    rng = np.hypot(points[:, 0], points[:, 1])
    return points[(rng > STRIP_NEAR_M) & (u > 0.0) & (u < STRIP_FAR_M) & (np.abs(v) < CORRIDOR_HALF_M)]


def steps_for(p: dict) -> list[dict]:
    """board_walk's one MoveTo: `face` makes it arrive looking at them. On the anybody road
    (`turn_first`) a spot turn before it, and only that for somebody already within reach."""
    steps: list[dict] = []
    if p.get("turn_first") and abs(p.get("bearing_deg") or 0.0) >= TURN_FIRST_DEG:
        steps.append({"do": "turn", "face": p["face_deg"], "label": "หันหาแขก"})
    if p.get("goal") is not None:
        steps.append({"to": [round(p["goal"][0], 3), round(p["goal"][1], 3)], "face": p["face_deg"],
                      "speed": p["speed"], "label": "เข้าหาแขก"})
    return steps


def off_line(pose: dict, start, goal) -> float:
    """How far `pose` is from the straight line start -> goal, metres."""
    ax, ay = start
    bx, by = goal
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    return abs((float(pose["x"]) - ax) * dy - (float(pose["y"]) - ay) * dx) / length


# ---- who: every usable face's own track ------------------------------------------------

def observe(watcher, frame, found: list, now: float) -> None:
    """From `Watcher.see` in its worker thread, with the frame's located faces.

    Why not the greeter's own decision: it judges only the nearest face and
    names only somebody alone (FACE_NAME_WHEN_ALONE), and at this event there
    is always somebody else in frame. Measured answer in docs/emma-face-approach.md:
    a name here needs a stricter bar than the door's (FACE_APPROACH_THRESHOLD)
    held for several frames of that face's own track, and then another face in
    the picture does not take it away: the name was earned by that face, not
    assumed of the biggest one, and the robot walks up to that face and says it
    once. Cost: at most FACE_APPROACH_MAX_FACES describes, FACE_APPROACH_EVERY_S
    apart, and only while staff have it on.
    """
    # Keeps looking through "planning": "they are still in the picture" right
    # before the wheels turn is this very candidate, refreshed.
    if not armed() or STATE["phase"] in ("walking", "greeting"):
        return
    if STATE["phase"] == "checking":
        # At the stop on the anybody road: the door camera is deciding. This only notes
        # what it is looking at, for `_look_then_greet`, and describes nothing more.
        # At the range the lidar has them (review 6 Nov: a 106 px face, ~4.6 m behind, anywhere in the
        # picture was "seen" and the hand went out to the chair she stood at).
        width = float(frame.shape[1])
        if any((f.bbox[2] - f.bbox[0]) >= FAR_MIN_PX
               and _fits_range(FACE_M, f.bbox[2] - f.bbox[0], width, _eyes.get("check_range"), RANGE_RATIO)
               for f in found):
            _eyes["face_at"] = now
        _eyes["suppressed_at"] = getattr(watcher, "suppressed_at", None)
        return
    with _eyes_lock:
        if now - _eyes["at"] < settings.face_approach_every_s:
            return
        _eyes["at"] = now
    from app import faces, facewatch, guests, head_follow, walk_frames

    moved = head_follow.moved_at()
    if ((moved is not None and time.monotonic() - moved < HEAD_SETTLE_S)
            or time.monotonic() < head_follow.settled_at() or walk_frames.base_moving()):
        return                      # the picture's direction is not the head's now
    if STATE["phase"] == "looking":
        _note_look(frame, found, now)    # one question this look: is that a person
        return
    if settings.face_approach_anyone:
        _note_far(frame, found, now)
        _note_body(frame, found, now)
    gallery = getattr(watcher, "gallery", None)
    if gallery is None or not len(gallery):
        return
    big = [f for f in found if (f.bbox[2] - f.bbox[0]) >= settings.face_approach_min_px]
    big = big[:max(1, int(settings.face_approach_max_faces))]
    if _eyes["tracks"] is None:
        _eyes["tracks"] = facewatch._Tracks()
    width = int(frame.shape[1])
    for track, face in _eyes["tracks"].update(big, now):
        seen = track.setdefault("approach", {"name": None, "n": 0, "at": now})
        if face.vec is None and faces.describe(frame, face) is None:
            continue
        index, score, runner = gallery.match2(face.vec)
        name, meta = gallery.names[index], gallery.meta[index]
        yaw = facewatch._yaw(face.kps)
        good = (meta.get("group") == guests.GROUP and score >= settings.face_approach_threshold
                and (runner < 0 or score - runner >= settings.face_approach_margin)
                and (yaw is None or abs(yaw) <= facewatch.Watcher.YAW_MAX)
                and facewatch._sharpness(frame, face.bbox) >= facewatch.Watcher.SHARP_MIN)
        if good and seen["name"] not in (None, name):
            seen.update(name=None, n=0)        # two names on one track: nobody
        if good:
            seen.update(name=name, n=seen["n"] + 1, at=now)
        elif now - seen["at"] > TRACK_GAP_S:
            seen.update(name=None, n=0, at=now)
        if seen["n"] >= settings.face_approach_frames and good:
            x1, _y1, x2, _y2 = face.bbox
            best = _eyes["candidate"]
            if best is None or now - best["at"] > 0.2 or (x2 - x1) > best["px"]:
                _eyes["candidate"] = {
                    "name": name, "meta": dict(meta), "score": float(score), "px": int(x2 - x1),
                    "cx": (x1 + x2) / 2.0, "w": width, "head": facewatch._head_now()[0], "at": now,
                    "faces": len(found)}


def _note_far(frame, found: list, now: float) -> None:
    """A face she can see and could not yet greet: the owner's "เห็นไม่ชัด". Located only,
    never described: who it is, is the door camera's question at the stop. Largest first."""
    from app import facewatch

    door = int(settings.face_approach_door_px)
    far = [f for f in found if FAR_MIN_PX <= (f.bbox[2] - f.bbox[0]) < door]
    if _eyes["far_tracks"] is None:
        _eyes["far_tracks"] = facewatch._Tracks()
    width = int(frame.shape[1])
    for track, face in _eyes["far_tracks"].update(far, now):
        track["far_n"] = track.get("far_n", 0) + 1
        if track["far_n"] >= FAR_LOOKS:
            x1, _y1, x2, _y2 = face.bbox
            _eyes["far"] = {"kind": "person", "via": "camera", "name": None, "px": int(x2 - x1), "cx": (x1 + x2) / 2.0,
                            "w": width, "head": facewatch._head_now()[0], "at": now, "faces": len(found)}
            return


def _note_body(frame, found: list, now: float) -> None:
    """A person's shape in the lens, in about the same place for PERSON_VISION_LOOKS looks: somebody
    who stays, face or no face. A face the door can greet is the door's: it is about to, from here.
    On the walks the boxes that were not people came while the base moved (`observe` does not ask then)
    and from one draped thing (docs/emma-face-approach.md section 11), which the lidar and the stop answer."""
    from app import facewatch, person_vision

    if not person_vision.enabled() or now - _eyes["body_at"] < float(settings.person_vision_every_s):
        return
    _eyes["body_at"] = now
    door = int(settings.face_approach_door_px)
    boxes = [] if any((f.bbox[2] - f.bbox[0]) >= door for f in found) else person_vision.people(frame)
    if not boxes:
        _eyes["body_run"] = []
        return
    box, width = boxes[0], int(frame.shape[1])
    run = _eyes["body_run"]
    if run and (abs(box.cx - run[-1]) > BODY_SAME_PLACE * width or now - _eyes["body_seen"] > BODY_GAP_S):
        run = []
    _eyes["body_seen"] = now
    run = (run + [box.cx])[-max(1, int(settings.person_vision_looks)):]
    _eyes["body_run"] = run
    if len(run) >= int(settings.person_vision_looks):
        _eyes["body"] = {"kind": "person", "via": "body", "name": None, "px": box.width, "cx": box.cx, "w": width,
                         "head": facewatch._head_now()[0], "at": now, "faces": len(found),
                         "score": round(box.score, 3)}


def _note_look(frame, found: list, now: float) -> None:
    """`_look_first`'s frames, once the head has been told (`counting`; `observe` already waited for it
    to arrive): a face, or a person's shape in LOOK_FRAMES of them, near the middle of the picture."""
    from app import person_vision

    from app import head_follow

    from app import facewatch, map_check

    if not _eyes["counting"] or not head_follow.at_look_pose():
        return                           # a frame from wherever else the head went is not this look
    width = float(frame.shape[1])
    hfov = map_check.lens(_mapping())["hfov"]
    tol = float(_eyes["look_tol"])
    expect = float(_eyes.get("look_expect") or 0.0)
    near = _eyes.get("look_range")

    def in_the_middle(x1: float, x2: float) -> bool:
        # Around where the target should be in the picture (review 6 Nov: widened round the picture's
        # middle instead, a look at the head's reach took the whole frame, and a person at its far edge
        # confirmed a chair at 27 deg). The name is the 5 Nov rule's: what answers is where it was aimed.
        a1, a2 = facewatch.bearing_deg(x1, int(width), hfov), facewatch.bearing_deg(x2, int(width), hfov)
        return min(a1, a2) <= expect + tol and max(a1, a2) >= expect - tol

    # ...and about as far away as what the lidar found (review 6 Nov: a person 15-30 m behind a chair,
    # or a face of 80 px -- 6 m off -- confirmed the chair at 2.2 m)
    if any((f.bbox[2] - f.bbox[0]) >= FAR_MIN_PX and in_the_middle(f.bbox[0], f.bbox[2])
           and _fits_range(FACE_M, f.bbox[2] - f.bbox[0], width, near, RANGE_RATIO) for f in found):
        _eyes["looked_n"] += 1
        _eyes["seen_n"] = LOOK_FRAMES
        return
    if now - _eyes["body_at"] < LOOK_FRAME_S:
        return
    _eyes["body_at"] = now
    _eyes["looked_n"] += 1
    if any(in_the_middle(b.x1, b.x2) and _fits_range(BODY_M, b.width, width, near, BODY_RANGE_RATIO)
           for b in person_vision.people(frame)):
        _eyes["seen_n"] += 1


def door_min_px(base: int) -> int:
    """The door camera's FACE_MIN_PX right now: `base`, or higher while she may walk up to
    whoever she sees. Off, armed or not, it is `base`."""
    if settings.face_approach_anyone and armed():
        return max(int(base), int(settings.face_approach_door_px))
    return int(base)


def _candidate(now: float, name: str | None = None) -> dict | None:
    c = _eyes["candidate"]
    if c is None or now - c["at"] > FRESH_S or (name is not None and c["name"] != name):
        return None
    return c


def holds_greeting(sighting) -> bool:
    """Should the door camera's own greeting wait? While she is on her way to
    somebody, and for a stranger's greeting while a scanned guest is being
    confirmed in the same frames: confirming takes 1-2 s and a stranger 1 s, and
    the first version of that race would be "Hello everyone" to the room, a
    conversation, and no walk (the rule against interrupting one)."""
    if not armed():
        return False
    if STATE["phase"] == "checking":
        return False                     # the stop's whole point: the door decides now
    if STATE["phase"] in ("planning", "looking") and STATE["kind"] == "person":
        return False                     # a face is here already: she greets it, and does not walk
    if STATE["phase"] != "idle":
        return True
    return sighting.kind == "stranger" and _candidate(time.monotonic()) is not None


def note_greeted(name: str | None) -> None:
    """The door camera greeted somebody where they stood (a guest by name, or anybody): no
    need to walk up. The lidar's nearest person in front of the lens is who that was."""
    now = time.monotonic()
    STATE["door_at"] = now
    if name:
        STATE["by_name"][name] = now
    if not settings.face_approach_anyone:
        return
    from app import lidar_people

    for person in lidar_people.people(now, max_age=2.0):
        if abs(person.bearing) <= CONE_DEG * 2:
            rad = math.radians(person.bearing)
            world = lidar_people.world_of({"x": person.distance * math.cos(rad),
                                           "y": person.distance * math.sin(rad)})
            if world is not None:
                _remember_spot(world, now)
            break


# ---- the anybody road: who ---------------------------------------------------------------

def _remember_spot(world, now: float) -> None:
    STATE["spots"] = [s for s in STATE["spots"]
                      if now - s[2] < settings.face_approach_person_cooldown_s][-32:] + [(world[0], world[1], now)]


def _greeted_here(world, now: float) -> bool:
    return any(now - at < settings.face_approach_person_cooldown_s and math.hypot(world[0] - x, world[1] - y) < SAME_SPOT_M
               for x, y, at in STATE["spots"])


def _person_stop() -> float:
    return max(STOP_FLOOR_M, float(settings.face_approach_person_stop_m))


def _person_candidate(now: float) -> dict | None:
    """The nearest person worth a walk, or None. The lidar's word (lidar_people.standing),
    minus who she dealt with and who is out of reach: asking the board about them
    again changes nothing."""
    if not settings.face_approach_anyone:
        return None
    far = _eyes["far"]
    if far is not None and now - far["at"] <= FRESH_S and now - _eyes["far_tried"] >= TRACK_RETRY_S:
        _eyes.update(far=None, far_tried=now)
        return far
    body = _eyes["body"]
    if body is not None and now - body["at"] <= FRESH_S and now - _eyes["body_tried"] >= TRACK_RETRY_S:
        _eyes.update(body=None, body_tried=now)
        return body
    from app import lidar_people

    reach = _reach()
    for person in lidar_people.standing(now):
        if person["distance"] > reach or now - person["tried_at"] < TRACK_RETRY_S:
            continue
        world = lidar_people.world_of(person)
        if world is None or _greeted_here(world, now):
            continue
        return {"kind": "person", "name": None, "track": person["id"], "world": world, "range_m": person["distance"],
                "bearing_deg": person["bearing"], "at": now, "faces": 0}
    return None


def _reach() -> float:
    return min(MAX_TRAVEL_M, float(settings.face_approach_max_m)) + _person_stop() + HALF_LENGTH_M


def _not_person(world, keep_s: float, now: float | None = None) -> None:
    now = time.monotonic() if now is None else now
    STATE["not_people"] = [s for s in STATE["not_people"] if s[2] > now][-64:] + [(world[0], world[1], now + keep_s)]


def _not_person_here(world, now: float) -> bool:
    return any(until > now and math.hypot(world[0] - x, world[1] - y) < NOT_PERSON_M
               for x, y, until in STATE["not_people"])


def _on_the_map(grid, x: float, y: float) -> bool:
    """Is there something on the board's map within MAP_CLEAR_M of here? A wall, a sofa: known."""
    occ = grid.occ()
    cells = int(MAP_CLEAR_M / grid.res)
    col, row = int((x - grid.ox) / grid.res), int((y - grid.oy) / grid.res)
    window = occ[max(0, row - cells):row + cells + 1, max(0, col - cells):col + cells + 1]
    return bool(window.any())


async def _scan_candidate(now: float) -> dict | None:
    """Somebody who stood still all along: the lidar has them, the Tracker calls them background, the
    lens is pointing elsewhere. Something person-sized on floor the map says is free, that she could
    walk up to, not looked at lately: one glance. At most one per FACE_APPROACH_LOOK_EVERY_S, because
    a head is a servo. `_look_first` says what it is; here is only what is worth asking about."""
    from app import lidar_people, person_vision

    every = float(settings.face_approach_look_every_s)
    if (not settings.face_approach_anyone or not person_vision.enabled() or every <= 0
            or now - STATE["looked_at"] < every or now - STATE["scan_at"] < SCAN_ASK_S):
        return None
    STATE["scan_at"] = now
    pose = lidar_people.STATE["tracker"].pose
    things = lidar_people.things(now)
    corners = area() if things else []
    if pose is None or len(corners) < 3:
        return None
    grid = await _map_grid()
    if grid is None:
        return None
    reach, stop = _reach(), _person_stop()
    for thing in things:                 # nearest first
        if thing.distance > reach:
            break
        rad = math.radians(thing.bearing)
        world = lidar_people.world_of({"x": thing.distance * math.cos(rad), "y": thing.distance * math.sin(rad)})
        if world is None or _greeted_here(world, now) or _not_person_here(world, now):
            continue
        if _on_the_map(grid, *world):
            continue
        if _pan_for(thing.bearing) is None:
            # Past what the head reaches. Not worth a turn of the base: floor 1's map lacks the event's
            # tables, and each one would turn her, every 45 s, with no evidence yet of anybody (review 5 Nov).
            continue
        # Where she would stop for them: outside staff's area, no board reads and no head for it.
        travel = thing.distance - stop - HALF_LENGTH_M
        heading = pose[2] + rad
        if travel >= MIN_TRAVEL_M and not inside(corners, pose[0] + travel * math.cos(heading),
                                                 pose[1] + travel * math.sin(heading)):
            continue
        STATE["looked_at"] = now
        return {"kind": "person", "via": "scan", "name": None, "world": world, "range_m": thing.distance,
                "bearing_deg": thing.bearing, "at": now, "faces": 0}
    return None


async def _map_grid():
    """The board's map, for the line of sight. None = unreadable, which is a no."""
    from app import map_check, robot_chassis

    now = time.monotonic()
    if STATE["map"] is not None and now - STATE["map_at"] < MAP_TTL_S:
        return STATE["map"]
    try:
        blob = await asyncio.wait_for(robot_chassis.request_raw("GET", robot_chassis.MAP_EXPLORE), 5.0)
        grid = map_check.parse_explore(blob)
    except Exception:  # noqa: BLE001 -- no map, no walk on the lidar's word alone
        logger.info("approach: no map for the line of sight", exc_info=True)
        return None
    STATE.update(map=grid, map_at=now)
    return grid


# ---- staff's switch --------------------------------------------------------------------

def page_alive() -> bool:
    beat = event_walk.STATE.get("heartbeat")
    return beat is not None and time.monotonic() - beat <= settings.event_walk_heartbeat_s


def armed() -> bool:
    return bool(settings.face_approach and STATE["staff_on"] and page_alive())


def set_enabled(on: bool) -> dict:
    if not on:
        STATE["staff_on"] = False
        turnlog.record("face_approach", phase="staff", on=False)
        return {"ok": True, "on": False}
    from app import event_host

    for why, bad in (("off", not settings.face_approach), ("ceremony", event_host.ceremony_now()),
                     ("walk_off", _walk_is_off()), ("no_area", len(area()) < 3),
                     ("no_lidar", settings.face_approach_anyone and not settings.lidar_people),
                     ("no_vision", _vision_is_broken()), ("stop_latched", _app_latched())):
        if bad:
            return {"ok": False, "error": why, "error_th": REASONS_TH[why]}
    STATE["staff_on"] = True
    STATE["armed_at"] = time.monotonic()
    turnlog.record("face_approach", phase="staff", on=True)
    return {"ok": True, "on": True}


def _app_latched() -> bool:
    from app import greeter, robot_face_link

    return greeter._app_latched(robot_face_link)


def _face_alive() -> bool:
    """The robot face has been on the link within event_walk.FACE_GRACE_S."""
    from app import robot_face_link

    if robot_face_link.live_faces():
        STATE["face_lost_at"] = None
        return True
    lost = STATE["face_lost_at"] = STATE.get("face_lost_at") or time.monotonic()
    return time.monotonic() - lost <= event_walk.FACE_GRACE_S


def _vision_is_broken() -> bool:
    """Asked for and not there: staff should see it when they press, not after a day of no walks."""
    from app import person_vision

    return bool(settings.face_approach_anyone and person_vision.enabled() and not person_vision.available())


def _walk_is_off() -> bool:
    """A dry run moves nothing, so it needs no walking switch: the bench's first
    stage runs with EVENT_WALK_ENABLED still off."""
    return not settings.event_walk_enabled and not settings.face_approach_dry_run


def _turn_off(why: str) -> None:
    if STATE["staff_on"]:
        STATE["staff_on"] = False
        logger.warning("walking up to guests turned off: %s", why)
        turnlog.record("face_approach", phase="staff", on=False, why=why)


def snapshot() -> dict:
    """For the phone page. No names: a phone in a crowd is read over a shoulder."""
    from app import guests

    last = STATE["last"]
    return {"available": bool(settings.face_approach), "on": armed(), "phase": STATE["phase"],
            "dry_run": bool(settings.face_approach_dry_run), "anyone": bool(settings.face_approach_anyone),
            "corners": len(area()), "guests": guests.count(),
            "last": None if last is None else {
                "what": last["what"], "why_th": REASONS_TH.get(last.get("why") or "", last.get("why")),
                "travel_m": last.get("travel_m"),
                "ago_s": round(time.monotonic() - last["at"], 0)}}


def _note(what: str, why: str | None = None, **fields) -> None:
    STATE["last"] = {"what": what, "why": why, "at": time.monotonic(), "travel_m": fields.get("travel_m")}
    key = (what, why)
    now = time.monotonic()
    if what == "refused" and now - STATE["logged"].get(key, -math.inf) < LOG_EVERY_S:
        return
    STATE["logged"][key] = now
    turnlog.record("face_approach", phase=what, why=why, **fields)
    logger.info("approach: %s%s", what, " (%s)" % why if why else "")


# ---- the gates -------------------------------------------------------------------------

def _still_allowed(c: dict) -> str | None:
    """Everything that needs no question to the board. In order, first one wins."""
    from app import consent, event_host, greeter, robot_arm, robot_chassis, robot_face_link, wake
    from app import session as session_module

    if not settings.face_approach:
        return "off"
    if event_host.ceremony_now():
        return "ceremony"
    if _walk_is_off():
        return "walk_off"
    if not armed():
        return "not_armed"
    if greeter.paused_except("face-approach"):
        # A face scan is being taken (greeter.hold): the head stays on the person being scanned.
        return "scanning"
    if wake.mic_held():
        # The owner's mute: the page's mic is shut, a "หยุด" would not be heard, and a hold
        # means not now -- she walks up to nobody (owner 6 Nov; review: she used to walk deaf).
        return "mic_held"
    if len(area()) < 3:
        return "no_area"
    now = time.monotonic()
    if c.get("kind") == "person" and c.get("world") is not None:
        # No name yet: consent is the door camera's question at the stop, if one comes. The
        # lidar says where they are, so the lens's calibration is not asked for either.
        if _greeted_here(c["world"], now):
            return "person_cooldown"
    elif c.get("kind") == "person":
        if true_hfov(_mapping()) is None:
            return "not_calibrated"      # a far face: "where" is the lens's answer
    else:
        mapping = _mapping()
        if true_hfov(mapping) is None:
            return "not_calibrated"
        # OK only: MISSING is tolerated for a colleague's name at the door, but
        # nobody is walked up to on a record that does not exist.
        if consent.status(c["name"]) != consent.OK:
            return "consent"
        if now - STATE["by_name"].get(c["name"], -math.inf) < settings.face_approach_person_cooldown_s:
            return "person_cooldown"
    if now - STATE["last_any"] < settings.face_approach_gap_s:
        return "gap"
    since = event_host.STATE.get("since")
    if since is not None and time.time() - since < settings.face_approach_after_scene_s:
        return "scene_recent"
    if event_walk.walking() or robot_face_link.server_walking():
        return "walking"
    nav = robot_face_link.nav_activity()
    if nav in ("active", "stale"):
        return "walking"
    if nav != "idle":
        return "nav_unknown"
    if greeter._app_latched(robot_face_link):
        return "stop_latched"
    if robot_chassis.desk_stopped_within(greeter.TURN_DESK_STOP_HOLD_S):
        return "desk_stopped"
    if not robot_arm.armed() or not settings.robot_arm_motion_enabled:
        return "arm_locked"
    # "exchange" only (final review 6 Nov): with FACE_GREET_LAPSE_S, "pending" -- a voice near her --
    # refused every walk in a crowd, after the head had already looked. Her answer, if one comes, is an
    # exchange and `_watch` stops the walk.
    if greeter._conversation_state(session_module._active) == "exchange":
        return "conversation"
    if not robot_chassis.configured():
        return "no_chassis"
    try:
        robot_chassis.run_fence()
    except robot_chassis.Fenced:
        return "fenced"
    if robot_chassis.STATE.get("moving"):
        return "driving"
    return None


def _mapping() -> dict | None:
    from app import head_follow

    return head_follow.camera_mapping()


async def _board_refusal() -> str | None:
    """The board's half: on the dock, busy, or lost. Unknown is no."""
    from app import robot_chassis

    try:
        docking = (await robot_chassis.power()).get("dockingStatus")
    except Exception:  # noqa: BLE001
        return "dock_unknown"
    if docking == "on_dock":
        return "docked"
    if docking != "not_on_dock":
        return "dock_unknown"
    try:
        over, _ok = robot_chassis._finished(await robot_chassis.current_action())
        quality = await robot_chassis.localization_quality()
    except Exception:  # noqa: BLE001
        return "board_unreadable"
    if not over:
        return "board_busy"
    if quality is None or quality < settings.face_approach_min_quality:
        return "low_quality"
    return None


async def _scan() -> tuple:
    """(robot-frame points, pose). Raises what the board raises."""
    from app import robot_chassis, robot_lanes as rl

    scan = await asyncio.wait_for(robot_chassis.laserscan(), 2.0)
    pose = scan.get("pose") or await asyncio.wait_for(robot_chassis.pose(), 2.0)
    return rl.scan_points({"laser_points": scan.get("points") or []}), pose


def _range_bearing(pose: dict, world) -> tuple[float, float]:
    """(metres, degrees off her front) to a place on the map."""
    x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0)
    dx, dy = world[0] - x, world[1] - y
    return math.hypot(dx, dy), _wrap(math.degrees(math.atan2(dy, dx) - yaw))


def _plan_now(c: dict, points, pose, grid=None) -> dict:
    if c.get("kind") == "person":
        if c.get("world") is not None:
            r, bearing = _range_bearing(pose, c["world"])
            p = plan(points, pose, bearing, r, area(), stop_m=_person_stop(), lidar_range=True, grid=grid,
                     spot_turn=True)
        elif c.get("via") == "body":
            mapping = _mapping()
            bearing = lidar_bearing(c["cx"], c["w"], c["head"], mapping)
            half = c["px"] / c["w"] * true_hfov(mapping) / 2.0
            p = plan(points, pose, bearing, 0.0, area(), stop_m=_person_stop(), grid=grid, spot_turn=True,
                     band=(PERSON_NEAR_M, _reach()), cone_deg=max(CONE_DEG, half + BODY_CONE_EXTRA_DEG))
        else:
            mapping = _mapping()
            bearing = lidar_bearing(c["cx"], c["w"], c["head"], mapping)
            p = plan(points, pose, bearing, face_range_m(c["px"], c["w"], true_hfov(mapping)), area(),
                     stop_m=_person_stop(), grid=grid, spot_turn=True)
        p["turn_first"] = True
        return p
    mapping = _mapping()
    bearing = lidar_bearing(c["cx"], c["w"], c["head"], mapping)
    return plan(points, pose, bearing, face_range_m(c["px"], c["w"], true_hfov(mapping)), area())


async def _preflight(c: dict, grid=None) -> dict:
    """Is somebody standing there, and standing still? PREFLIGHT_LOOKS plans, all
    of which must agree; never leaves for a person on their way past."""
    first = None
    result: dict = {"why": "no_scan"}
    for look in range(PREFLIGHT_LOOKS):
        if look:
            await asyncio.sleep(PREFLIGHT_GAP_S)
        try:
            points, pose = await _scan()
        except Exception:  # noqa: BLE001
            return {"why": "no_scan"}
        result = _plan_now(c, points, pose, grid)
        if result.get("why"):
            return result
        if first is None:
            first = result["person"]
        elif math.dist(first, result["person"]) > MOVING_M:
            return {**result, "why": "moving_target"}
    return result


# ---- one approach ----------------------------------------------------------------------

def _who(c: dict) -> dict:
    """What a row may say of who this is: numbers. Never a name."""
    if c.get("kind") == "person":
        return {"kind": "person", "seen_by": c.get("via") or "lidar", "px": c.get("px"),
                "confirmed": bool(c.get("confirmed"))}
    return {"px": c["px"], "score": round(c["score"], 3), "faces": c["faces"]}


async def _approach(c: dict) -> None:
    from app import event_host, greeter, lidar_people
    from app import session as session_module

    person = c.get("kind") == "person"
    STATE.update(phase="planning", kind="person" if person else "guest", door_at=None)
    try:
        if person and c.get("track") is not None:
            track = lidar_people.track(c["track"])
            if track is not None:
                track["tried_at"] = c["at"]
        why = _still_allowed(c) or await _board_refusal()
        if why:
            _note("refused", why)
            return
        seq = event_host.STATE.get("seq")
        # The line first, before the plan (review 6 Nov): the dial takes up to LINE_WAIT_S, and a
        # plan, a "standing still" or a gate from before it is that old. Everything below -- the
        # preflight, the look, the last gates before the wheels -- is asked with the line up.
        if (person and c.get("via") not in ("body", "camera") and _looked_recently(time.monotonic())):
            # The look this road needs would be refused anyway: no line spent on it (review 6 Nov).
            _note("refused", "looked_recently")
            return
        why = await _line_up()
        if why:
            _note("refused", why)
            return
        grid = None
        if person:
            grid = await _map_grid()
            if grid is None:
                _note("refused", "no_map")
                return
        p = await (_preflight(c, grid) if person else _preflight(c))
        if p.get("why"):
            _note("refused", p["why"], travel_m=p.get("travel_m"), range_m=p.get("range_m"))
            if c.get("via") == "scan" and p["why"] != "no_scan":
                _not_person(c["world"], UNREACHED_KEEP_S)
            return
        if (c.get("via") == "body" and p.get("range_m")
                and not _fits_range(BODY_M, c.get("px") or 0, c.get("w") or 1024, p["range_m"], BODY_RANGE_RATIO)):
            # The box road takes the nearest person-sized thing in the cone and has no look first: the box
            # must be about that far away too (review 6 Nov: a 40 px box -- somebody far behind -- walked
            # her to the chair 2.2 m off).
            _note("refused", "box_too_far", range_m=p.get("range_m"), **_who(c))
            return
        if (c.get("via") in ("body", "camera") and grid is not None and p.get("person") is not None
                and _on_the_map(grid, *p["person"])):
            # The scan road's own rule (`_scan_candidate`), asked of these too (review 6 Nov): a table
            # leg or a wall the map has, in the cone of a box or a far face, is not them.
            _note("refused", "on_the_map", range_m=p.get("range_m"), **_who(c))
            return
        if c.get("via") == "body" and _not_person_here(p["person"], time.monotonic()):
            # The camera road has no list of its own: what it keeps calling a person, and no walk
            # ever found a face on, ends here.
            _note("refused", "not_a_person", range_m=p.get("range_m"), **_who(c))
            return
        if person and c.get("via") not in ("body", "camera"):
            p = await _look_first(c, p, grid, seq)
            if p is None:
                return
        # A second is a long time in a crowd: asked again, and they must still
        # be in the picture, right before the wheels turn.
        why = _still_allowed(c) or await _board_refusal()
        if why is None and not _line_ready(session_module._active):
            why = "no_line"              # it dropped since it came up
        if why:
            _note("refused", why)
            return
        if person:
            # A face came into the lens meanwhile and the door greeted it: she is talking
            # to somebody now, and they are in front of her. No walk for that.
            if STATE["door_at"] is not None:
                _note("refused", "door_greeted")
                return
            if c.get("track") is not None and lidar_people.track(c["track"]) is None:
                _note("refused", "gone")
                return
            if _greeted_here(p["person"], time.monotonic()):
                _note("refused", "person_cooldown")
                return
        elif _candidate(time.monotonic(), c["name"]) is None:
            _note("refused", "gone")
            return
        if settings.face_approach_dry_run:
            # The bench's first stage (docs/emma-face-approach.md): everything
            # worked out, nothing moved, nothing said. The numbers are for a
            # tape measure: are left and right right, is the range right.
            again = time.monotonic() - settings.face_approach_person_cooldown_s + DRY_RUN_REPEAT_S
            if person:
                STATE["spots"] = STATE["spots"][-32:] + [(p["person"][0], p["person"][1], again)]
            else:
                STATE["by_name"][c["name"]] = again
            camera_deg = None
            if c.get("cx") is not None and c.get("w"):
                # what the picture said, beside the lidar's own: a mirror or an offset hides in the cone
                camera_deg = round(lidar_bearing(c["cx"], c["w"], c.get("head") or 0.0, _mapping()), 1)
            _note("would_walk", None, travel_m=p.get("travel_m"), range_m=p.get("range_m"),
                  bearing_deg=p.get("bearing_deg"), camera_deg=camera_deg, face_m=p.get("face_m"),
                  close=bool(p.get("close")),
                  turn=bool(person and abs(p.get("bearing_deg") or 0.0) >= TURN_FIRST_DEG), **_who(c))
            return
        # Counted now, whatever comes of it: an attempt that fails is not a
        # reason to try that person again in ten seconds. Never chase.
        now = STATE["last_any"] = time.monotonic()
        if person:
            _remember_spot(p["person"], now)
        else:
            STATE["by_name"][c["name"]] = now
        walked = None
        if p.get("close") and not steps_for(p):
            _note("close", None, range_m=p["range_m"], **_who(c))
        else:
            if not armed():              # the awaits above: a "หยุด" with nothing yet walking
                _note("refused", "not_armed")
                return
            _open_ears()
            walked = await _walk(c, p, seq)
            if walked is None:
                return
        if person:
            await _look_then_greet(c, p, walked, seq)
        else:
            await _greet(c, p, walked, seq)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 -- the camera loop must outlive this
        logger.exception("approach failed")
        _note("failed")
    finally:
        STATE.update(phase="idle", kind=None)
        greeter.release("face-approach")
        _eyes["candidate"] = None
        _eyes["tracks"] = None
        STATE["retry_at"] = time.monotonic() + RETRY_AFTER_S


def _line_ready(session) -> bool:
    """A live call that can act on "หยุด": its provider is up, and it is not the offline voice
    (OFFLINE_FALLBACK answers from a fixed list and calls no tool)."""
    from app import robot_face_link

    return (session is not None and getattr(session, "provider", None) is not None
            and not getattr(session, "_offline", False)
            # The robot's own mic: a desk tab that superseded the robot face hears nobody at the robot.
            and robot_face_link.is_robot_face(session))


async def _line_up() -> str | None:
    """A call open before the head turns or a wheel does (owner 6 Nov: "ถ้าเห็นคนหุ่นจะเดินเข้าไป แล้ว
    ตอนเดินก็เปิดสายเลย แต่ถ้าไม่มีใครคุยกับหุ่นหรือหุ่นไม่เห็นก็พักสายไว้"). None when there is one, else
    why not. Never under a mic hold (`_still_allowed` refuses that first; asked again here because the
    awaits before can span a tap). A line rung here that nobody uses ends by the ordinary rules: a
    summoned line with no greeting after SUMMONED_ABANDON_S, a used one when it goes idle -- the
    owner's "พักสายไว้"."""
    from app import wake
    from app import session as session_module

    if wake.mic_held():
        return "mic_held"
    if _line_ready(session_module._active):
        return None
    await wake.summon(reason="face_approach")
    deadline = time.monotonic() + LINE_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        if wake.mic_held():
            return "mic_held"            # tapped during the dial (review 6 Nov)
        if _line_ready(session_module._active):
            return None
    return "no_line"


def _line_gone() -> str | None:
    """While she looks or walks: "mic_held" or "no_line" when nobody can stop her by voice now."""
    from app import wake
    from app import session as session_module

    if wake.mic_held():
        return "mic_held"
    if not _line_ready(session_module._active):
        return "no_line"
    return None


def _keep_line() -> None:
    """The line is the approach's while it runs: a summoned line nobody greeted ends after
    SUMMONED_ABANDON_S (30 s), and dial + look + walk can be longer (review 6 Nov). Each call holds
    its idle clock from now; when the approach stops calling, the ordinary rules park it."""
    from app import session as session_module

    keep = getattr(session_module._active, "keep_line", None)
    if callable(keep):
        keep()


def _open_ears() -> None:
    """She is about to walk on this line: what is said to her is heard from now. A summoned call is
    deaf until its greeting (SUMMONED_HOLD_MAX_S), and here the greeting comes at the stop -- the
    "หยุด" before it. Somebody who talks to her on the way is an exchange, and `_watch` stops the walk."""
    from app import session as session_module

    opener = getattr(session_module._active, "open_ears", None)
    if callable(opener):
        opener("face_approach")


def _looked_recently(now: float) -> bool:
    """One look at most this often, however they are asked for (ch8 smoked on 12 Sep)."""
    return now - STATE["head_at"] < max(LOOK_MIN_GAP_S, float(settings.face_approach_look_every_s))


def _picture_deg(bearing_deg: float, pan_pct: int) -> float:
    """Where a lidar bearing falls in the head camera's picture (facewatch.bearing_deg's degrees) with the
    head at `pan_pct`: `lidar_bearing` the other way round."""
    from app import map_check

    lens = map_check.lens(_mapping())
    return (float(bearing_deg) - lens["b"] - float(pan_pct) * PAN_DEG_PER_PCT) / (lens["a"] or 1.0)


def _pan_for(bearing_deg: float) -> int | None:
    """Percent of pan that puts a lidar bearing in the lens, or None past what the head reaches."""
    # HEAD_FOLLOW off: the head stays where it is, and only the lens's own width is left.
    limit = max(0, min(100, int(settings.head_pan_max))) * PAN_DEG_PER_PCT if settings.head_follow else 0.0
    if abs(bearing_deg) > limit + LOOK_PAST_DEG:
        return None
    return int(round(max(-limit, min(limit, bearing_deg)) / PAN_DEG_PER_PCT))


def _set_aside(c: dict, keep_s: float, world) -> None:
    """Not to be asked about again for `keep_s`: the lidar's track by its id (its `tried_at` from the
    future, TRACK_RETRY_S counting from there), and the place, for the glances."""
    from app import lidar_people

    now = time.monotonic()
    _not_person(world, keep_s, now)
    if c.get("track") is not None:
        track = lidar_people.track(c["track"])
        if track is not None:
            track["tried_at"] = now + keep_s


async def _look_first(c: dict, p: dict, grid, seq) -> dict | None:
    """The lidar found something person-sized. What is it? The head camera, before a single step. Returns
    the plan to go on with (a base turn changes it), or None and it has said why.

    Two tilts: level for somebody standing (a 19 deg lens at 3 m sees 0.75 m of them, around the
    head), then lower for somebody sitting, or short. No person in either: nothing is said, and
    that place is set aside. A chair somebody carried in ends here, and so does a poster's stand."""
    from app import event_host, greeter, head_follow, person_vision, robot_face_link
    from app import session as session_module

    if not person_vision.enabled():
        return p                         # the lidar's word, and no hand without a face
    if not person_vision.available():
        _note("refused", "no_vision")
        return None
    now = time.monotonic()
    if _looked_recently(now):
        # A track comes back in TRACK_RETRY_S; the glances have their own limit, but it is one head.
        _note("refused", "looked_recently")
        return None
    # The head turns inside a call's loop (head_follow). Seeing somebody opens the line (owner 6 Nov:
    # "ถ้าเห็นคนหุ่นจะเดินเข้าไป แล้วตอนเดินก็เปิดสายเลย"); it used to be refused here when no standing
    # line was up. A fresh call's head may not be ready yet: that look ends "head_busy" below, nothing
    # concluded, and the next tick asks again.
    why = await _line_up()
    if why:
        _note("refused", why)
        return None
    now = time.monotonic()
    pan = _pan_for(p["bearing_deg"])
    head_off = bool(getattr(getattr(session_module._active, "_head_follow", None), "off", None))
    if head_off:
        # This call's head is off (a failed sweep, a head turning the wrong way): only the lens is left.
        pan = 0 if abs(p["bearing_deg"]) <= LOOK_PAST_DEG else None
    if pan is None:
        if now - STATE["turned_at"] < TURN_LOOK_GAP_S:
            _note("refused", "turned_recently")
            _set_aside(c, UNREACHED_KEEP_S, p["person"])
            return None
        if settings.face_approach_dry_run:
            # A dry run turns no wheel, and this one takes a wheel to look. Said, set aside.
            _note("would_turn", None, bearing_deg=p.get("bearing_deg"), range_m=p.get("range_m"), **_who(c))
            _set_aside(c, UNREACHED_KEEP_S, p["person"])
            return None
        try:
            _points, pose = await _scan()
        except Exception:  # noqa: BLE001
            _note("refused", "no_scan")
            return None
        # A wheel is about to turn, and every gate was last asked before the map and the preflight:
        # seconds, in which B, the arm lock, a joystick, a scene and somebody talking all still turned
        # her (review 5 Nov). The board first: nothing awaited between the last answer and the turn.
        why = await _board_refusal() or _still_allowed(c)
        if why is None and event_host.STATE.get("seq") != seq:
            why = "scene_changed"
        if why is None and STATE["door_at"] is not None:
            why = "door_greeted"
        if why:
            _note("refused", why)
            return None
        STATE["turned_at"] = now
        where = p["person"]
        if not await _reaim(c, pose, where, seq) or not await _settled():
            return None
        # The base moved: the Tracker threw its frame away, and the track with it. The place is what counts now.
        c["track"] = None
        STATE["phase"] = "planning"
        # A turn counts as moving (walk_frames) for 15 s past its end, and the frames are not to be
        # read until then: `observe` and every gate ahead say so.
        deadline = time.monotonic() + ARM_WAIT_S
        while robot_face_link.server_walking() and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if not armed():
                _note("refused", "not_armed")
                return None
        p = await _replan(c, grid, where)
        if p is None:
            return None
        pan = _pan_for(p["bearing_deg"]) or 0
    if not armed():                      # the awaits above: no head for a "หยุด"
        _note("refused", "not_armed")
        return None
    STATE.update(phase="looking", head_at=time.monotonic())
    # Past the head's reach by up to LOOK_PAST_DEG, they are that far off the middle of the picture.
    _eyes.update(seen_n=0, body_at=-math.inf, counting=False, looked_n=0, look_range=p.get("range_m"),
                 look_expect=_picture_deg(p["bearing_deg"], pan), look_tol=LOOK_MIDDLE_DEG)
    # With this call's follow off no tick takes a hint: one look, the lens where the head is.
    for tilt in ((None,) if head_off else (_tilt_for(p["range_m"]), int(settings.head_search_tilt))):
        if tilt is None:
            head_follow.look_here()
        else:
            head_follow.look_hint(tilt, pan)
        _eyes["counting"] = False
        asked = time.monotonic()
        until = asked + LOOK_WAIT_S
        while time.monotonic() < until:
            await asyncio.sleep(CHECK_POLL_S)
            if not _eyes["counting"]:
                if head_follow.look_taken():
                    if not head_follow.at_look_pose():
                        # The app refused it (busy, too soon) and the head is where it was: frames from
                        # there are not of this place (review 6 Nov). Nothing concluded.
                        _note("refused", "head_busy", range_m=p.get("range_m"))
                        return None
                    settle = head_follow.settled_at()
                    if settle == math.inf:
                        # Where the head points is nobody's guess (its call ended): nothing to read.
                        _note("refused", "head_unknown", range_m=p.get("range_m"))
                        return None
                    if time.monotonic() >= settle:
                        _eyes["counting"] = True
                        until = time.monotonic() + LOOK_TILT_S
                    else:
                        until = max(until, settle + LOOK_TILT_S)     # still on its way there
                elif time.monotonic() - asked >= LOOK_WAIT_S - CHECK_POLL_S:
                    # A face in view holds the head (its loop comes first). She has not looked at this:
                    # nothing is known about it, so nothing is remembered about it.
                    _note("refused", "head_busy", range_m=p.get("range_m"))
                    return None
            elif not head_follow.at_look_pose():
                # Taken elsewhere mid-look (a face being followed): what it saw is not that place.
                _note("refused", "head_busy", range_m=p.get("range_m"))
                return None
            if STATE["door_at"] is not None:
                _note("refused", "door_greeted")
                return None
            if not armed():
                _note("refused", "not_armed")
                return None
            if event_host.STATE.get("seq") != seq:
                _note("refused", "scene_changed")
                return None
            # Her talking with somebody ends the look. Somebody near her mid-sentence does not
            # (FACE_GREET_LAPSE_S's "pending"; critic 6 Nov): in a crowd that is always.
            if greeter._conversation_state(session_module._active) == "exchange":
                _note("refused", "conversation")
                return None
            if robot_face_link.server_walking():
                _note("refused", "walking")
                return None
            why = _line_gone()
            if why:
                _note("refused", why)
                return None
            if _eyes["seen_n"] >= LOOK_FRAMES:
                c["confirmed"] = True
                STATE["phase"] = "planning"
                _note("looked", None, seen=True, pan=pan, tilt=tilt, range_m=p.get("range_m"), **_who(c))
                # Seconds went by: the corridor is asked again, for now and not for then.
                return await _replan(c, grid, p["person"])
    if not _eyes["looked_n"]:
        # Not a frame examined in either tilt (the door camera's frames held, its loop busy): she has
        # not seen that place, so nothing is remembered about it (review 6 Nov).
        _note("refused", "no_frames", pan=pan, range_m=p.get("range_m"), **_who(c))
        return None
    _note("refused", "not_a_person", pan=pan, range_m=p.get("range_m"), **_who(c))
    _set_aside(c, NOT_PERSON_S, p["person"])
    return None


async def _replan(c: dict, grid, where) -> dict | None:
    p = await _preflight(c, grid)
    if p.get("why"):
        _note("refused", p["why"], travel_m=p.get("travel_m"), range_m=p.get("range_m"))
        if p["why"] != "no_scan":
            _set_aside(c, UNREACHED_KEEP_S, where)
        return None
    return p


async def _walk(c: dict, p: dict, seq) -> object | None:
    """Start it, watch it. The run when it arrived, None otherwise."""
    from app import greeter

    STATE["phase"] = "walking"
    # The door camera's greetings (and its frames' cost) are not for now.
    greeter.hold("face-approach", p["travel_m"] / max(p.get("speed") or settings.face_approach_speed, 0.05) + 30.0)
    result = await event_walk.start(SPOT, "เข้าหาแขก", steps_for(p), dock=None, autonomous=True)
    if not result.get("ok"):
        _note("refused", "walk_refused", error=result.get("error"))
        return None
    run = event_walk.current()
    _note("start", None, travel_m=p["travel_m"], range_m=p["range_m"], speed=p.get("speed"), **_who(c))
    why = await _watch(run, p, seq)
    if why is not None:
        await event_walk.stop("approach_" + why)
    for _ in range(100):
        if run.state not in ("starting", "walking"):
            break
        await asyncio.sleep(0.05)
    if why == "met":
        # They came to her. She stops short of them and it is a greeting, not a failure.
        _note("met", None, range_m=p["range_m"])
        return run
    if why is not None:
        _note("aborted", why)
        return None
    if run.state != "arrived":
        reason = run.reason or run.state
        _note("aborted", reason)
        if run.stop_requested in HUMAN_STOPS:
            _turn_off(str(run.stop_requested))
        return None
    return run


async def _watch(run, p: dict, seq) -> str | None:
    """While it walks: is the reason for walking still true? The reason to stop,
    or None when the run ended on its own. Stopping is event_walk's job, and the
    board's own obstacle stops and every other stop path run regardless."""
    from app import event_host, greeter, robot_chassis
    from app import session as session_module

    lost_since = None
    fails = 0
    while event_walk.current() is run and event_walk.walking():
        await asyncio.sleep(WATCH_EVERY_S)
        if not (event_walk.current() is run and event_walk.walking()):
            return None
        if not armed():
            return "staff_off"
        if getattr(run, "held_at", None) is not None:
            # board_walk: something stands in the way. Waiting it out is how a
            # robot ends up pressing on a stranger; this walk was optional.
            return "path_blocked"
        if event_host.STATE.get("seq") != seq:
            return "scene_changed"
        # somebody spoke to her on the way (and she answers). Not "pending": with
        # FACE_GREET_LAPSE_S any voice near her mid-walk stopped it and marked the person done.
        if greeter._conversation_state(session_module._active) == "exchange":
            return "conversation"
        # Nobody to say "หยุด" to any more (review 6 Nov): the line dropped, or the mic was muted.
        why = _line_gone()
        if why:
            return why
        _keep_line()
        try:
            points, pose = await _scan()
        except Exception:  # noqa: BLE001
            fails += 1
            if fails >= READ_FAILS:
                return "board_unreadable"
            continue
        if p.get("goal") is not None:
            ahead = strip_ahead(points, pose, p)
            if len(ahead):
                # The board's own stop may come too; this one does not wait for it, or for the
                # quality read below. Them, closing in: met. Anybody or anything else: a stop.
                target = _person_at(points, pose, p["person"])
                if target is not None:
                    tx, ty = target[0] - float(pose["x"]), target[1] - float(pose["y"])
                    yaw = float(pose.get("yaw") or 0.0)
                    bx, by = tx * math.cos(yaw) + ty * math.sin(yaw), -tx * math.sin(yaw) + ty * math.cos(yaw)
                    if min(math.hypot(q[0] - bx, q[1] - by) for q in ahead) <= STRIP_TARGET_M:
                        return "met"
                return "path_blocked"
        try:
            quality = await asyncio.wait_for(robot_chassis.localization_quality(), 2.0)
            fails = 0
        except Exception:  # noqa: BLE001
            fails += 1
            if fails >= READ_FAILS:
                return "board_unreadable"
            continue
        if quality is None or quality < settings.face_approach_min_quality:
            return "low_quality"
        if p.get("goal") is not None and off_line(pose, p["start"], p["goal"]) > OFF_LINE_M:
            return "off_line"
        where = _person_at(points, pose, p["person"])
        now = time.monotonic()
        if where is None:
            lost_since = lost_since or now
            if now - lost_since > LOST_S:
                return "lost_target"
            continue
        lost_since = None
        # Before "moved": somebody walking up to meet her is what this was for. And the plan's
        # metre is from where they stood: without this a person who stepped 0.5 m toward her
        # was under MOVED_M and she would have stopped 0.5 m from them.
        here = (float(pose["x"]), float(pose["y"]))
        if (p.get("goal") is not None and math.dist(where, p["goal"]) - HALF_LENGTH_M < STOP_FLOOR_M - MEET_TOLERANCE_M
                and math.dist(where, here) - HALF_LENGTH_M < STOP_FLOOR_M + MEET_MARGIN_M):
            return "met"
        if math.dist(where, p["person"]) > MOVED_M:
            return "moved"
    return None


def _person_at(points, pose, world, radius: float = TRACK_RADIUS_M) -> tuple | None:
    """Where the person near `world` (a place on the map) is now, or None."""
    x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0)
    dx, dy = world[0] - x, world[1] - y
    r = math.hypot(dx, dy)
    bearing = math.degrees(math.atan2(dy, dx) - yaw)
    found = find_person(points, _wrap(bearing), max(0.3, r - radius), r + radius,
                        cone_deg=max(CONE_DEG, math.degrees(math.atan2(radius, max(r, 0.5)))))
    if found is None:
        return None
    heading = yaw + math.radians(found.bearing)
    return (x + found.distance * math.cos(heading), y + found.distance * math.sin(heading))


async def _settled() -> bool:
    """Fully stopped: two board poses that agree, and no action on the board."""
    from app import robot_chassis

    deadline = time.monotonic() + SETTLE_MAX_S
    before = None
    while time.monotonic() < deadline:
        try:
            pose = await asyncio.wait_for(robot_chassis.pose(), 2.0)
            here = (float(pose["x"]), float(pose["y"]), math.degrees(float(pose.get("yaw") or 0.0)))
            over, _ok = robot_chassis._finished(await robot_chassis.current_action())
        except Exception:  # noqa: BLE001
            return False
        if (before is not None and over and math.dist(here[:2], before[:2]) <= SETTLE_M
                and abs(_wrap(here[2] - before[2])) <= SETTLE_DEG):
            return True
        before = here
        await asyncio.sleep(SETTLE_GAP_S)
    return False


def _still_greetable(seq) -> bool:
    """Right before her greeting goes out (events.announce asks after its wait), what may have changed
    since she arrived (review 6 Nov): staff switched her off, a scene, a mute tapped at the stop (the
    greeting would have reopened the mic -- greeting_mic), the door camera greeting them first, or
    somebody already talking to her."""
    from app import event_host, greeter, wake
    from app import session as session_module

    return (event_host.STATE.get("seq") == seq and not event_walk.walking() and armed()
            and not wake.mic_held() and STATE["door_at"] is None
            and greeter._conversation_state(session_module._active) != "exchange")


async def _greet(c: dict, p: dict, walked, seq) -> None:
    """Fully stopped, the person still there and still consented: the name, then
    the hand. Nothing said to the air: a name with nobody there is a guest's name
    said to the room."""
    from app import consent, event_host, events, facewatch, greeter
    from app import session as session_module

    STATE["phase"] = "greeting"
    if not await _settled():
        _note("aborted", "not_settled")
        return
    try:
        points, pose = await _scan()
    except Exception:  # noqa: BLE001
        _note("aborted", "no_scan")
        return
    if _person_at(points, pose, p["person"]) is None:
        _note("aborted", "lost_on_arrival")
        return
    if consent.status(c["name"]) != consent.OK:
        _note("aborted", "consent")
        return
    if event_host.STATE.get("seq") != seq:
        _note("aborted", "scene_changed")
        return
    # faces_in_frame=1: this greeting is to the one person she walked up to --
    # never FACE_GROUP_GREETING's "everyone" in a crowd she can always see. The
    # true count went in the "start" row.
    sighting = facewatch.Sighting(kind="known", name=c["name"], score=c["score"], meta=c["meta"],
                                  faces_in_frame=1)
    sent = await events.announce(
        greeter.greeting_for(sighting), source="face_approach", summon=True, said_private=True,
        still_relevant=lambda: _still_greetable(seq) and consent.status(c["name"]) == consent.OK,
    )
    if not sent:
        _note("aborted", "not_said")
        return
    greeter.mark_greeted(session_module._active)
    shook = await _shake(seq)
    _note("greeted", None, handshake=shook, walked=walked is not None)


def _tilt_for(range_m: float) -> int:
    """How far down to look to centre a face at this range, percent of the head's span
    (FACE_DROP_M). Never past the call's own cap, which the app clamps again."""
    want = math.degrees(math.atan2(FACE_DROP_M, max(range_m, 0.5))) / TILT_DEG_PER_PCT
    return int(round(min(want, float(settings.head_tilt_down_max))))


async def _look_then_greet(c: dict, p: dict, walked, seq) -> None:
    """Stopped in front of somebody: now the camera. The door's own rules decide, because
    it has them all (the bars, margin, alone in frame, consent, who it greeted already):
    its frames come back on, and a greeting from it is the greeting. Nothing goes to the
    model before this: the name, if any, comes out of a face she has just looked at.
    The head moves at most twice, slowly, with the base standing still."""
    from app import event_host, events, facewatch, greeter, head_follow, wake
    from app import session as session_module

    STATE["phase"] = "greeting"
    if walked is not None and not await _settled():
        _note("aborted", "not_settled")
        return
    try:
        points, pose = await _scan()
    except Exception:  # noqa: BLE001
        _note("aborted", "no_scan")
        return
    where = _person_at(points, pose, p["person"])
    if where is None:
        _note("aborted", "lost_on_arrival")
        return
    if event_host.STATE.get("seq") != seq:
        _note("aborted", "scene_changed")
        return
    range_m, bearing = _range_bearing(pose, where)
    if abs(bearing) > REAIM_DEG:
        why = await _line_up()           # a wheel turns: the line first, here too (review 6 Nov)
        if why:
            _note("aborted", why)
            return
        if not _room_to_turn(area(), float(pose["x"]), float(pose["y"])):
            # No turn at the edge of staff's area. She looks from where she stands.
            _note("no_reaim", "outside_area")
        elif not await _reaim(c, pose, where, seq) or not await _settled():
            return
    STATE.update(phase="checking", door_at=None)
    _eyes.update(face_at=None, suppressed_at=None, check_range=range_m)
    started = time.monotonic()
    greeter.release("face-approach")
    if session_module._active is None and not wake.mic_held():
        # The line first (it dropped on the way): the dial is 1-3 s (voice_ready rows), and the greeting's wait.
        await wake.summon(reason="face_approach")
    # The stop's look is one look (down to the range, maybe lower, back: <= 3 moves). Right after another
    # one -- the track road looked before it walked -- it is the first tilt alone (review 6 Nov: 6-7 moves
    # in 20 s against "no look more often than every 20 s"); the face needs that one.
    lower_allowed = not _looked_recently(time.monotonic())
    head_follow.look_hint(_tilt_for(range_m))
    STATE["head_at"] = time.monotonic()
    second_look = False
    positions: list = []
    next_read = started
    while True:
        await asyncio.sleep(CHECK_POLL_S)
        now = time.monotonic()
        if now >= next_read:
            # The evidence for the bar nobody can set yet (`spread_m` below).
            next_read = now + POSITION_EVERY_S
            with contextlib.suppress(Exception):
                pts, at_pose = await _scan()
                seen_at = _person_at(pts, at_pose, where, STILL_THERE_M)
                if seen_at is not None:
                    positions.append(seen_at)
        _keep_line()
        if STATE["door_at"] is not None:
            _remember_spot(where, now)
            _note("greeted", None, via="door", walked=walked is not None, **_who(c))
            return
        if not armed():
            _note("aborted", "staff_off")
            return
        if event_host.STATE.get("seq") != seq:
            _note("aborted", "scene_changed")
            return
        # Her talking with somebody, not somebody near her mid-sentence (FACE_GREET_LAPSE_S's
        # "pending"): the door's greeting waits that out on its own.
        if greeter._conversation_state(session_module._active) == "exchange":
            _note("greeted", None, via="talk", walked=walked is not None, **_who(c))
            return                       # somebody is talking to her: that is the greeting
        face_at, suppressed_at = _eyes["face_at"], _eyes["suppressed_at"]
        seen = face_at is not None and face_at >= started
        if suppressed_at is not None and suppressed_at >= started:
            # The door knows this face and has greeted it already. Not twice.
            _remember_spot(where, now)
            _note("aborted", "already_greeted", walked=walked is not None)
            return
        if not seen and not second_look and now - started >= SECOND_LOOK_S:
            second_look = True
            if lower_allowed:
                head_follow.look_hint(int(settings.head_search_tilt))
        if now - started < float(settings.face_approach_check_s) + (FACE_MORE_S if seen else 0.0):
            continue
        break
    # Nobody in the lens, or a face the door could not read in all that time. Are they still
    # there? The lidar says, close by where they stood: no words to somebody who left, and a
    # chair leg a metre off is not them.
    try:
        points, pose = await _scan()
    except Exception:  # noqa: BLE001
        _note("aborted", "no_scan")
        return
    if _person_at(points, pose, where, STILL_THERE_M) is None:
        _note("aborted", "lost_on_arrival")
        return
    _remember_spot(where, time.monotonic())
    # faces_in_frame=1: this is to the one person she walked up to, never "Hello everyone".
    sighting = facewatch.Sighting(kind="stranger", name=None, score=-1.0, meta={}, faces_in_frame=1)
    sent = await events.announce(
        greeter.greeting_for(sighting), source="face_approach", summon=True,
        # greeter.paused(): a face scan opened at the stop. Its person is not to be talked over.
        still_relevant=lambda: _still_greetable(seq) and not greeter.paused(),
    )
    if not sent:
        _note("aborted", "not_said")
        return
    greeter.mark_greeted(session_module._active)
    # A hand only to a face she saw: what she walked up to may be a chair somebody carried in (the
    # lidar cannot tell: docs/emma-face-approach.md section 9). Words to a chair are forgiven.
    shook = False
    if seen and settings.robot_greeting_gesture_enabled and not settings.face_handshake_named_only:
        shook = await _shake(seq)
    # No bar on this: 2 people verified in the pictures ranged 0.22-0.24 m over the 2.4-3 s they stood,
    # just after walking in, and what never walked ranged 0.00-0.05 m -- but tracks that look like a
    # person standing at the robot do that too, and nobody was seen for 6 s. Measure at the bench:
    # `spread_m` on a person with their back turned, and on a chair.
    spread = max((math.dist(a, b) for a in positions for b in positions), default=None)
    if not seen and c.get("via") == "body":
        # The camera called it a person from afar and she has stood in front of it for all of
        # FACE_APPROACH_CHECK_S with no face. Somebody with their back to her, or a draped thing (on
        # the walks the standing boxes with no face were one thing under black cloth). Not again.
        _not_person(where, NO_FACE_KEEP_S)
    _note("greeted", None, via="plain", face=seen, handshake=shook, walked=walked is not None,
          spread_m=None if spread is None else round(spread, 3), **_who(c))


async def _reaim(c: dict, pose: dict, where, seq) -> bool:
    """They shifted on the way, past what the lens holds: one spot turn toward them, by the
    same walker and every stop that has. False = it did not happen, and that is the end."""
    from app import greeter

    from app import session as session_module

    if not armed():                      # a "หยุด" between arriving and this turn
        _note("aborted", "staff_off")
        return False
    # somebody talking to her, a face being scanned: not the moment to turn (review 6 Nov)
    if greeter._conversation_state(session_module._active) == "exchange" or greeter.paused_except("face-approach"):
        _note("aborted", "conversation")
        return False
    x, y, yaw = float(pose["x"]), float(pose["y"]), float(pose.get("yaw") or 0.0)
    heading = math.degrees(math.atan2(where[1] - y, where[0] - x))
    q = {"turn_first": True, "bearing_deg": _wrap(heading - math.degrees(yaw)), "face_deg": round(heading, 1),
         "travel_m": 0.0, "range_m": math.dist(where, (x, y)), "person": tuple(where)}
    if abs(q["bearing_deg"]) < TURN_FIRST_DEG:
        q["bearing_deg"] = math.copysign(TURN_FIRST_DEG, q["bearing_deg"])  # steps_for's bar is for the plan
    _open_ears()                         # a wheel turns: hearing "หยุด" from now
    walked = await _walk(c, q, seq)
    greeter.release("face-approach")
    return walked is not None


async def _shake(seq) -> bool:
    """The hand, after the voice. `robot_arm.greet` keeps every arm gate (it
    refuses while `server_walking`, which can trail a walk: walk_frames' motion
    check holds 15 s past the last move). That wait is seconds: staff STOP (no app
    latch), a new scene or the ceremony during it is asked again after it (review 6 Nov)."""
    from app import event_host, greeter, robot_arm, robot_face_link
    from app import session as session_module

    deadline = time.monotonic() + ARM_WAIT_S
    while robot_face_link.server_walking() and time.monotonic() < deadline:
        await asyncio.sleep(0.25)
    if robot_face_link.server_walking() or greeter._app_latched(robot_face_link):
        return False
    if not armed() or event_host.STATE.get("seq") != seq or event_host.ceremony_now():
        return False
    session = session_module._active
    return bool(session is not None and await robot_arm.greet(session=session))


# ---- the loop --------------------------------------------------------------------------

async def tick() -> None:
    """One look at whether somebody should be walked up to."""
    if STATE["staff_on"] and not page_alive():
        _turn_off("control_lost")
    if STATE["staff_on"] and _app_latched():
        # B, the operator screen, a stop on the robot: the app's 60 s latch. Without this she came back
        # by herself when it ran out (review 5 Nov), and a person saying stop means until somebody presses on.
        _turn_off("app_stop")
    if STATE["staff_on"] and not _face_alive():
        # The robot face gone with no walk under way (event_walk's own switch covers a walk): when the
        # app came back she walked up to people with nobody pressing on (review 6 Nov).
        _turn_off("face_gone")
    if not armed() or STATE["phase"] != "idle":
        return
    run = event_walk.current()
    if (run is not None and run.stop_requested in HUMAN_STOPS and STATE["stop_seen"] is not run
            and run.ended is not None):
        # A human stop while it was armed (a scene's walk, voice, the desk):
        # armed again only by a person pressing on again. Seen once it has ended: a stop waits
        # for its walker, and a tick that saw it before then never looked again (review 6 Nov).
        STATE["stop_seen"] = run
        if run.ended > STATE["armed_at"]:
            _turn_off(str(run.stop_requested))
            return
    now = time.monotonic()
    if now < STATE["retry_at"] or (STATE["task"] is not None and not STATE["task"].done()):
        return
    c = _candidate(now) or _person_candidate(now) or await _scan_candidate(now)
    if c is None:
        return
    STATE["task"] = asyncio.create_task(_approach(c))


async def _loop() -> None:
    while True:
        await asyncio.sleep(0.2)
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("approach tick failed")


def start() -> None:
    """From greeter.start(): nothing runs with the switch off."""
    if not settings.face_approach:
        return
    if settings.face_approach_anyone and settings.person_vision:
        from app import person_vision

        threading.Thread(target=person_vision.available, name="person-vision", daemon=True).start()
    task = STATE.get("loop")
    if task is None or task.done():
        STATE["loop"] = asyncio.create_task(_loop())


async def stop() -> None:
    for key in ("loop", "task"):
        task = STATE.get(key)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
    STATE.update(loop=None, task=None)
