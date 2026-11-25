"""The robot's real head turns toward the person she is talking with (HEAD_FOLLOW).

Owner, 31 Oct 2026: "เวลาคุยอยากให้หุ่นลองหมุนหัวเพื่อหันหน้าไปคุยกับคน เช่น
ได้ยินเสียงคนมาจากทางซ้ายใกล้ๆ ก็ให้หุ่นหันทักแล้วก้มหน้าหรือขึ้นลง เพื่อจับหน้าคน".

What it follows starts from what the face page's mascot already follows
(FOCUS_ATTENTION): `focus.attention()` -- the face whose mouth moves, else the
face the last segment was attributed to, else the nearest engaged face. With
HEAD_FOLLOW_FIRST (owner 10 Nov, `_first_person`) that is only who it turns to
first: the head then stays with that person -- by track, else by where they
stand in the room -- and turns to nobody else until they are gone, then comes
back to centre; the mascot's eyes keep following the cue. The
head camera turns with the head, so a face's bearing IS the error left to
turn: a closed loop, a step of GAIN of it at a time, re-measured on a frame
taken after the move had time to finish -- the app says how long the move
takes in its answer (`ack`), or SETTLE_S from a page that does not answer.

"A voice from the left" needs the mic array's direction, and that is not
available: the Bothlent array gives Android ONE capture stream, and the
call's WebView holds it (measured 10 Oct -- DOA_ENABLED=false, robot.env).
A DOA bearing is NOT used here even when it comes: the array does not turn
with the head (nobody has said where it sits), so its bearing is no "error
left to turn", and doa.py's face-taught calibration would need the head's
offset too. Until then the camera leads, and one thing stands in for hearing
a direction: a voice AT the robot (rms >= HEAD_SEARCH_RMS) with no face in
view tilts the head down (HEAD_SEARCH_TILT) -- 12-13 Oct, all 983 "no face in
front" refusals had no face at all while people stood at the chest screen
under the camera.

**ARRAY_MIC (2 Nov, docs/emma-array-mic-2026-11-02.md)** makes the app the
array's only owner, so a DOA bearing can come. app/doa.py now keeps its
frames straight -- it learns in the robot's frame (face + head pan, no pair
while the head turns: `moved_at`) and hands focus the bearing minus the pan
-- so focus's DOA bearing IS the head camera's error. It still never drives
the face loop (no face to re-measure); with HEAD_FOLLOW_DOA (off) it aims
the one-shot "voice at the robot, no face" search instead of only looking
down (`_doa_look`), before the lidar's look.

**Measured before it follows anybody (HEAD_CALIBRATE, 31 Oct, round 2).** Neither the
direction ch7 turns for "+" nor whether the camera's picture is mirrored was
ever checked on the robot, and degrees per percent is a guess (15 Oct's
photos cut the head off). A wrong sign is not a slow loop, it is a head that
turns AWAY from every guest until it hits its limit. So the first call with
no data/robot/head_calibration.json sweeps the head centre -> left -> right
-> down -> centre and reads how far the head camera's own picture slid
(phase correlation, `measure`): degrees per 100 %, signed, in the camera's
own units -- the units every bearing here is in, so an HFOV guess cancels.
It also proves the camera turns with the head (no slide = no loop) and that
the app moved at all. Saved, it is used from then on; deleting the file
measures again. A failed sweep turns following off for that call (and a
sweep that moved the head is not repeated until the server restarts), and
`head_calibration` rows say why. A loop that still turns the wrong way
(camera re-mounted after the sweep) stops itself after DIVERGE_STRIKES
steps that pushed the face further out.

**The lidar checks the camera (LIDAR_PEOPLE, 31 Oct, round 3 -- owner "เช็ค
เรดาห์ เลเซอร์ และภาพประกอบกัน").** A face in the picture and the one person
the lidar sees at the same moment are a pair (`_pair`); enough pairs give
the line `lidar = a * camera + b` (app/lidar_people.py): the camera's real
field of view, where it points, and a check of the sweep's head sign in a
second sensor. Once it holds it is saved with the sweep, and a voice at the
robot with no face in view turns the head toward the nearest person on the
lidar (`_lidar_look`) instead of only looking down. Until it holds, the
lidar steers nothing -- unless the robot's lens is measured (9 Nov,
`_measured_pan`: the check expects a lens near the 70 deg guess and the
robot's is 19, so it never held there). With the lens measured the check is
a record only and never aims (`lidar_fit` None, lidar_people.solve never
trusts): the measured facts aim the head, map_check and the walk-up alike.

The rule of robot_body holds: the server holds no pulse and no channel. It
sends a percent of the app's own span (`robot_face_link.look`), the app
maps it to ch7/ch8 inside its own hard clamps (HeadLook.java) and refuses
it behind its own gates: the "สั่งท่าทางด้วยเสียง" switch, a stop latch, a
walk, the operator screen, a vendor group on the arm. Its answer comes back
through the page (`head_look_ack`): a refused look is NOT where the head is
(the dedupe's offset and the next step start from the last accepted one).
The app also brings the head back to centre by itself when no look came for
10 s, so a call that ends -- or a server that dies -- does not leave it
turned; that is why a head held on a face is re-sent every HOLD_RESEND_S
(and the re-send also puts it back after a vendor group moved it).

Walking: the head goes to centre once and nothing more is sent until the
walk ends (the app takes a centre look even while it navigates -- the walk
pictures look ahead; it refuses any other). With HEAD_WALK_SCAN (owner 4 Nov)
it sweeps WALK_SCAN instead (both sides and ahead, eye level / desk / floor),
inside the app's walk box, and comes back to centre when the walk ends.

Off by default, and on its own switch: FOCUS_ATTENTION (the mascot) is on
the robot since 10 Oct and must not start moving a servo with a pull.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import re
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.head_follow")

ROOT = Path(__file__).resolve().parent.parent
#: Written by the robot's server (gitignored with the rest of data/robot/;
#: robot_server deploy never ships or deletes it).
CAL_PATH = ROOT / "data" / "robot" / "head_calibration.json"

#: One look per this long at most, and a face is used again only from a frame
#: taken after the move ended -- or the same error is corrected twice and the
#: head overshoots.
SEND_GAP_S = 1.0
#: A page that does not answer (from before 31 Oct, round 2): the move's time is unknown.
SETTLE_S = 0.9
#: With a pan trim (HEAD_PAN_TRIM_PCT) the ceremony's still head is re-sent this often: the app takes the head to its own
#: centre after 10 s with no look.
TRIM_RESEND_S = 4.0
#: From the servo stopping to a frame of it reaching the server (the page's
#: ~6 fps JPEG stream).
CAMERA_LAG_S = 0.4
#: The app's own gap between two looks is 0.4 s (HeadLook.MIN_GAP_MS): even a
#: forced look waits this long after the last one, or it is refused too_soon.
FORCE_GAP_S = 0.5
#: Smaller changes (percent) are not sent: a servo hunting around a face that
#: breathes is noise on the board and in the picture.
MIN_STEP = 4
#: 10, not 5 (owner 10 Nov: "ไม่ต้องหันไปมาตลอดเวลา"). In the camera's 70 deg
#: units the robot's 19 deg lens shows +-35, so 10 is a face still well inside
#: the middle of the picture (~2.7 true deg). At 5, one guest standing and
#: talking was "turned to" every 10-25 s, the head hunting back and forth
#: across them by 4 % (10 Nov log, 15:57: bearings +6.2, -6.7, -6.4, +6.8,
#: -5.7, -9.9, +6.1 -- every one a look).
PAN_DEADBAND_DEG = 10.0
TILT_DEADBAND_DEG = 7.0
GAIN = 0.6
#: No target this long -> back to centre (the app also centres on its own).
IDLE_S = 6.0

# ---- the first person (HEAD_FOLLOW_FIRST, owner 10 Nov) ----
#: Owner, 10 Nov 2026: "เวลาหุ่นเห็นหน้าใครคนแรก อยากให้กล้องแทร็กตามคนนั้นเวลาเขา
#: ยืนคุยหรืออยู่รอบๆ แต่พอเขาไปหุ่นก็กลับมาตรงเหมือนเดิม ไม่ต้องหันไปมาตลอดเวลา".
#: The robot's log that day: with 2-6 faces in view the cue the head followed
#: (`focus.attention`: the nearest face, FOCUS_GAZE_VISIT_S's round, whoever's
#: mouth moved) jumped from person to person -- bearings +24.6 then -29.6,
#: +33.1 then -33.7 ("visit") -- and the head with it, to its 60 % limit.
#: Now the head locks on the first face it turns to and follows only that
#: one; the mascot's eyes still follow `attention` (they are not a servo).
#: Not the track id alone: the camera's track ids are not a person -- a head
#: move slides the box past the tracker's overlap (one guest's call: tracks
#: 13, 14, 17, 20, 42, 51 in three minutes). So the lock also remembers where
#: the person stands in the ROOM (camera bearing + the head's pan, the units
#: `focus._visit` uses) and takes again the face nearest that spot, from a
#: frame after the head settled, within LOCK_NEAR_DEG -- ~6.8 true deg with
#: the robot's 19 deg lens: ~12 cm at a metre, where the next guest's face
#: is out of the picture altogether.
LOCK_NEAR_DEG = 25.0
#: Not seen this long (from the last frame they were in) = gone: the lock
#: ends and the idle branch centres the head in the same tick. Until then the
#: head holds where it is and turns to nobody else. A new lock waits for the
#: head to be back at centre (or aimed by a search) -- "พอเขาไปหุ่นก็กลับมาตรง".
LOCK_LOST_S = IDLE_S
#: HEAD_LOCK_BY=speaker (owner 12 Nov: "อยากให้หันตามคนที่พูดคนแรกหรืออยู่ด้านหน้า"): the
#: attention sources that mean "this face's voice opened her ears" -- a mouth seen moving,
#: the face the last forwarded segment was attributed to, the main person (counted from
#: those segments). "face" and "visit" are only somebody in view.
SPEAKER_SOURCES = ("mouth", "label", "main")
#: A "label" is the lone face in view while a forwarded segment played ("only_face"): a face
#: crossing the lens while somebody off camera talks gets it too (review 12 Nov). It must
#: have been in the picture this long first -- two of the robot's ~1 s frames.
SPEAKER_MIN_STAND_S = 0.8
#: A face's stand survives a gap this long between its frames: the robot sends a frame with a
#: face about once a second and 2.5 s in its worst minute (6 Nov face_perf); FOCUS_HOLD_S (2.0)
#: restarted somebody standing there in a slow minute, and "front" never came (review 12 Nov).
STAND_GAP_S = 3.0
#: Looks that aim the head for a reason of their own. One that turns it (a
#: pan) ends the lock: whoever it finds there is the next first person. Any of
#: them, sent, is the head back from the last person.
_AIMED = ("idle", "search", "lidar", "doa", "approach", "recentre", "walk", "walk_scan", "calibrate")
#: A head held off centre is re-sent this often: the app centres after 10 s
#: with no look (HeadLook.IDLE_CENTRE_MS), and a guest standing still on the
#: axis gets no new step -- without this the head swung back mid-sentence.
HOLD_RESEND_S = 4.0
#: A loud voice and no face for this long -> the look-down search; it holds
#: SEARCH_HOLD_S and is not repeated within SEARCH_EVERY_S (room chatter must
#: not make the head bob down and up all day).
SEARCH_AFTER_S = 1.0
SEARCH_VOICE_AGE_S = 2.0
SEARCH_HOLD_S = 6.0
SEARCH_EVERY_S = 20.0
LOG_EVERY_S = 5.0
#: The app's answer comes back through the page within milliseconds.
ACK_WAIT_S = 1.0
#: HeadLook.UNKNOWN_MS: a move from where nobody knows the head is.
UNKNOWN_MOVE_S = 3.0
#: Steps after which the face was further out, on the side the step should
#: have brought it in from, by DIVERGE_SHARE of the step or more, in a row ->
#: the loop turns the wrong way: stop. Two, not three: with GAIN 0.6 a wrong
#: sign reaches HEAD_PAN_MAX in two steps, and a head pinned at its limit
#: sends no step to judge. A guest who walks on is not one (they would have
#: to outrun 1.75 steps in each of two moves -- tested).
DIVERGE_STRIKES = 2
DIVERGE_SHARE = 0.75
DIVERGE_MIN_DEG = 2.0

# ---- the walk scan (HEAD_WALK_SCAN, owner 4 Nov) ----
#: Where the head looks on a walk while the BASE STANDS STILL (percent; + =
#: down): one nod, never a pan. Inside the app's walk box
#: (HeadLook: no pan, WALK_TILT_DOWN_US 36 = 18 %, the call's own
#: HEAD_TILT_DOWN_MAX) -- the app refuses anything past it. Eye level comes
#: free (the head is ahead when the base stops), then ~11 deg down (desk tops)
#: -- one move down and one back per stop. The head turns ~0.75 deg a percent
#: of tilt past ~1 % of gear slack (4 Nov dock ladder), and STOPS at ~22-23 %.
#: 4 Nov afternoon, the owner twice: "ตอนเดินไม่เห็นก้มมองดูวัดพื้น หรือโต๊ะเลย"
#: (the first scan, ((35, 0), (0, 12), (-35, 0), (0, 0)) every 2 s, was ~8 deg
#: either side and a ~6 deg nod) and then "อย่าหันหัวไวเกินและถี่เกิน เดี๋ยว
#: มอเตอร์ที่หัวจะไหม้" (12 Sep: ch8 smoked, stuck). So: the base turns for the
#: sides (a survey's look stops, every 20 deg), the head only nods while the
#: base stands, each move >= 1.5 s (the app's WALK_MIN_MS), one per dwell, and
#: on the move it is ahead and still.
#: 4 Nov evening, the dock tilt ladder: the head stops at ~22-23 % down -- this
#: nod's 40 % (and the rejected 80 %) pushed ch8 on a head that had stopped.
#: 18 = HEAD_TILT_DOWN_MAX, and the send clamps to the cap as well (~11 deg: the
#: floor shows only from ~4 m; near things are the lidar's).
WALK_SCAN = ((0, 18),)
#: Each pose this long (the app's own floor between walk looks is 2 s).
WALK_SCAN_DWELL_S = 2.5
#: The base must have stood this long first (walk_frames.still_s): a spot
#: turn's last wobble is not a stop.
WALK_SCAN_STILL_S = 1.0
#: The app saying no to a scan look (an APK from before the walk box, or a
#: walk it cannot see): this walk looks ahead, asked once.
WALK_SCAN_REFUSALS = ("navigating", "nav_unknown")

# ---- the sweep (HEAD_CALIBRATE) ----
#: Small on purpose (4 Nov, the robot): the first sweeps used 30/25 and read
#: `no_match` twice -- the saved pictures showed pan 30 % sliding the head
#: camera's picture 166 of 320 px (past half the width, where phase
#: correlation wraps: the trace climbed 13 -> 29 deg, then read -34) and tilt
#: 25 % sliding it 84 of 240 px. 10/12 slide it ~17 % of each side.
CAL_PAN = 10
CAL_TILT = 12
#: Downscaled width the pictures are compared at (1024 -> 320: ~3x faster and
#: still ~0.2 deg a pixel at a 70 deg HFOV).
CAL_WIDTH = 320
#: Phase correlation's peak below this = the two pictures share nothing.
CAL_MIN_RESPONSE = 0.05
#: Each side of the pan must slide at least this far (a head that did not
#: move, or a camera that does not turn with it).
CAL_MIN_DEG = 0.8
#: |degrees at 100 %| outside this is a measurement gone wrong, not a servo.
#: The robot's head is ~120 (pan) / ~80 (tilt) in the camera's degrees
#: (4 Nov pictures, at the assumed HFOV) -- 150 was too near.
CAL_FULL_RANGE = (3.0, 250.0)
#: Left and right must be mirror images within this factor.
CAL_SYMMETRY = 2.0
#: Back at centre the picture must be where it started, within this.
CAL_RETURN_MAX_DEG = 2.0
#: A blank wall slides without changing: below this much detail the sweep
#: cannot see a move, so it says so instead of reading "no motion".
CAL_MIN_TEXTURE = 2.0
CAL_FRAME_WAIT_S = 3.0
#: On top of the move and CAMERA_LAG_S: a frame's time is when it ARRIVED,
#: and a servo can ring after its timed move -- the sweep can afford to wait.
CAL_SETTLE_EXTRA_S = 0.5
#: A sweep look refused for a moment (a gesture or vendor group holds the
#: head, the app's own gap) is asked again for this long before giving up.
CAL_RETRY_S = 8.0
CAL_RETRY_REASONS = ("busy", "too_soon")
#: The sweep starts when she is quiet (her greeting's arm gestures can hold
#: the head), at most this long after the call wanted it.
CAL_QUIET_WAIT_S = 20.0
#: Refusals that will not change within the call: the switch is off, an APK
#: that does not know the look.
STOPPING_REFUSALS = ("disabled", "bad_request")
#: The last sweep's five pictures + every frame's slide over time, for the
#: bench (4 Nov: the first sweep on the robot read `no_match` with the head
#: sweeping fully and nobody in front -- even "back" was 26 deg off "centre";
#: the turnlog's numbers alone cannot say whether the frames were mid-move,
#: late, or of another camera). On the robot only, replaced by the next sweep.
SWEEP_DIR = ROOT / "data" / "robot" / "head_sweep"
#: After the frame the sweep measures, keep watching this long: a picture
#: still sliding then was taken mid-move.
SWEEP_TRACE_AFTER_S = 1.5

_REASON = re.compile(r"^[a-z_]{1,32}$")

#: Where the head was last told to look, in degrees (+pan = robot's left,
#: +tilt = down), for facewatch's same-spot dedupe: a box remembered with the
#: head turned is somewhere else in the frame once it turned back. One robot,
#: one head -- module state.
_HEAD = {"pan_deg": 0.0, "tilt_deg": 0.0, "moved_at": None, "pan_pct": 0, "tilt_pct": 0,
         "settle_at": -math.inf, "known": True}
#: The measured calibration (loaded once), and whether a sweep that moved the
#: head already failed in this process (not repeated: each try is a head
#: sweeping in front of a guest).
_CAL: dict = {"loaded": False, "value": None, "failed": None}
#: The lidar <-> camera check (app/lidar_people.py): its latest solve, and
#: the last one written into the calibration file.
_LIDAR: dict = {"fit": None, "saved": None}

#: A voice at the robot with no face in view and a person on the lidar this
#: near: turn toward them (the lidar says where "the left" is); this near,
#: also look down (they stand under the camera, at the chest screen).
LIDAR_TURN_M = 1.6
LIDAR_CLOSE_M = 1.0
#: The head, real degrees a percent of pan (dock ladder 4 Nov, docs/robot-servo-limits-2026-10-31.md) --
#: face_approach.PAN_DEG_PER_PCT, the same number (a test holds them equal). The sweep's 10 % step reads
#: ~0.23 by counting the gears' slack, so the sweep's number does not aim at a lidar bearing: with it
#: the head would turn 1.6x past the person.
PAN_DEG_PER_PCT = 0.37
#: A person past the pan cap by this much is still turned to (face_approach.LOOK_PAST_DEG): inside the
#: measured lens's half width (9.5), not at its edge where a face is half out of the picture.
LOOK_PAST_DEG = 6.0
#: Pairs for the check: one face in the picture, one person on the lidar
#: within this, nobody else.
LIDAR_PAIR_M = 2.5
LIDAR_SOLVE_EVERY = 5
#: One look down asked for from outside a call's loop (app/face_approach.py, standing
#: in front of somebody with no face in view yet), and how long it stays worth doing.
_LOOK: dict = {"tilt": None, "pan": None, "at": -math.inf, "pose": None}
#: A look face_approach asked for, not yet answered by the app: counted as the app's slowest move
#: (HeadLook UNKNOWN_MS, from a head nobody knows -- after a group, a rest, a stop or at start). Its
#: frames from before then are of the way there (review 6 Nov: a fixed 1.3 s let a bystander on the
#: sweep path confirm a chair). The answer's own `ms` replaces it (`ack`).
UNACKED_MOVE_S = 3.0
#: An outside look keeps the head this long past its settle (face_approach's LOOK_TILT_S of frames and a
#: little): the idle branch's centring cut a 3 s move's look at SEARCH_HOLD_S (review 6 Nov).
LOOK_HOLD_S = 4.0
#: The app centres the head by itself this long after its last look (HeadLook.IDLE_CENTRE_MS).
APP_IDLE_CENTRE_S = 10.0
#: A call dialled for the greeting takes 1-3 s to tick (`voice_ready` rows).
LOOK_HINT_S = 4.0


def _clock() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def offset_deg() -> tuple[float, float]:
    """(pan, tilt) the head was last told, degrees; (0, 0) with HEAD_FOLLOW off."""
    if not settings.head_follow:
        return 0.0, 0.0
    return _HEAD["pan_deg"], _HEAD["tilt_deg"]


def moved_at() -> float | None:
    """When the head was last told a DIFFERENT pose (time.monotonic()), or
    None. app/doa.py learns no pair from a face seen while the head turns:
    that face's bearing is in a frame nobody knows."""
    if not settings.head_follow:
        return None
    return _HEAD["moved_at"]


def look_hint(tilt_pct: int, pan_pct: int | None = None) -> None:
    """Look down this far, and that way (`pan_pct`, + = her left; None = wherever the head is),
    once, on the next tick with no face to follow and the base still (a walk's own branch comes
    first). Slow and rare by the caller's word: the owner's rule after ch8 smoked on 12 Sep;
    `_limits` and the app clamp it to the measured stops."""
    _LOOK.update(tilt=int(tilt_pct), pan=None if pan_pct is None else int(pan_pct), at=_clock(), pose=None)


def look_taken() -> bool:
    """Has the last `look_hint` reached the head? A face being followed comes first and holds it there,
    and a hint nobody took is not a look: frames from then are of wherever the head already was."""
    return not settings.head_follow or _LOOK["tilt"] is None


def settled_at() -> float:
    """When the head's last move is over and the camera's frames are of where it went: the app's own
    `ms` once it answered (`HeadFollow.ack`), the slowest move until then for a look asked from outside
    (`look_hint`). -inf with HEAD_FOLLOW off: the frame never moved."""
    if not settings.head_follow:
        return -math.inf
    if not _HEAD.get("known", True):
        return math.inf                  # where it points is nobody's guess until a call centres it
    return float(_HEAD.get("settle_at", -math.inf))


def at_look_pose() -> bool:
    """The head is still where the last `look_hint` sent it: no refusal put it back (`ack`), no face
    being followed took it elsewhere (review 6 Nov: a face at the lens edge was centred by the loop and
    then counted as the look's answer). True with HEAD_FOLLOW off: there is no head to move."""
    if not settings.head_follow:
        return True
    pose = _LOOK.get("pose")
    return pose is not None and (_HEAD.get("pan_pct"), _HEAD.get("tilt_pct")) == tuple(pose)


def look_here() -> None:
    """A look with the head where it is: this call's follow is off (a failed sweep) and no look will be
    sent -- what the lens sees now is the look (review 6 Nov: every look waited for a hint no tick would
    take, and ended "head_busy")."""
    _LOOK.update(tilt=None, pan=None, at=_clock(), pose=(_HEAD["pan_pct"], _HEAD["tilt_pct"]))


def _heading_home(unknown: bool = True) -> None:
    """Nobody will send this head anything more (its call ended, or the call's follow turned off). The
    app centres it by itself APP_IDLE_CENTRE_S after its last look -- but not when it is latched, on the
    operator screen, in the background or switched off, and not until a handshake ends (review 6 Nov).
    So where it points is not known: `settled_at` says never, until the next call's HeadFollow has
    centred it (`tick`, "recentre"). Before, a box seen between calls was read at the last call's pan."""
    if not settings.head_follow or (_HEAD["pan_pct"], _HEAD["tilt_pct"]) == (0, 0):
        return
    if unknown:
        _HEAD["known"] = False
    else:
        # this call's follow off: it ticks no more, so nothing would centre it -- the app's own 10 s
        _HEAD["settle_at"] = max(float(_HEAD.get("settle_at", -math.inf)),
                                 _clock() + APP_IDLE_CENTRE_S + UNACKED_MOVE_S + CAMERA_LAG_S)
    _set_head(0, 0)


#: The app's gestures that end in the arm board's own groups (6 handshake, 3 greet, 99 rest after every arm gesture):
#: those groups drive the head channel too, to a value nobody measured, and nothing told the head back -- owner 25 Nov
#: ~01:55: "ทำไมกลับมาไม่ตรงเหมือนตอนรี" (straight after a power cycle, turned after use). Their end = the head's place is
#: unknown: the next tick centres it slowly (the ceremony's "ceremony_centre", elsewhere the "recentre" look).
_GROUP_ENDED = frozenset(("done", "failed", "stopped"))


def gesture_ended(action: str | None, state: str | None) -> None:
    """robot_body, on a body status: an arm gesture is over (any of them ends in group 99)."""
    if not settings.head_follow or state not in _GROUP_ENDED or not action or action in ("stop", "look"):
        return
    _HEAD["known"] = False


def call_ended() -> None:
    """session.py, when a call that had a head follow ends."""
    _heading_home()


def _hinted_tilt(now: float) -> int | None:
    if _LOOK["tilt"] is None or now - _LOOK["at"] > LOOK_HINT_S:
        return None
    return _LOOK["tilt"]


def reset() -> None:
    _LOOK.update(tilt=None, pan=None, at=-math.inf, pose=None)
    _HEAD.update(pan_deg=0.0, tilt_deg=0.0, moved_at=None, pan_pct=0, tilt_pct=0, settle_at=-math.inf,
                 known=True)
    _CAL.update(loaded=False, value=None, failed=None)
    _LIDAR.update(fit=None, saved=None)


def lidar_fit() -> dict | None:
    """The lidar <-> camera line when it is good enough to steer by: this
    process's latest solve, else the one saved with the sweep. Never with the
    lens measured on the robot: the measured facts aim instead, everywhere
    (this, map_check.lens and face_approach read this) -- review 9 Nov: a fit
    of the wrong sign (6 Nov 09:14:59, a +0.31, b -16) would have held, been
    saved, and turned every look away from the person."""
    if float(settings.robot_camera_hfov_measured or 0.0) > 0:
        return None
    fit = _LIDAR["fit"]
    if fit and fit.get("trusted"):
        return fit
    cal = _calibration() if settings.head_calibrate else None
    saved = cal.get("lidar") if isinstance(cal, dict) else None
    return saved if isinstance(saved, dict) and saved.get("trusted") else None


def camera_mapping() -> dict | None:
    """For the walk pictures' lidar strip (walk_frames' run.json): the
    camera's assumed field of view and, once checked, the lidar line."""
    from app import facewatch

    fit = lidar_fit()
    return {"hfov_assumed": facewatch.ROBOT_CAMERA_HFOV_DEG,
            # what the robot measured (ROBOT_CAMERA_HFOV_MEASURED / _MIRRORED,
            # 4 Nov): the pictures' lens until the lidar check holds
            "hfov_measured": settings.robot_camera_hfov_measured or None,
            "mirrored": bool(settings.robot_camera_mirrored),
            "lidar_a": None if fit is None else fit.get("a"),
            "lidar_b": None if fit is None else fit.get("b"),
            "hfov_from_lidar": None if fit is None else fit.get("hfov_deg")}


def _save_lidar(fit: dict) -> None:
    """Write a trusted check next to the sweep (the next process steers by it
    from its first call). Only when it changed enough to matter."""
    cal = _CAL["value"]
    if not isinstance(cal, dict) or not fit.get("trusted"):
        return
    before = _LIDAR["saved"] or cal.get("lidar") or {}
    if (before.get("trusted") and abs((before.get("a") or 0) - fit["a"]) < 0.03
            and abs((before.get("b") or 0) - fit["b"]) < 1.0):
        return
    keep = {k: fit.get(k) for k in ("n", "a", "b", "mad_deg", "hfov_deg", "head_ratio", "trusted")}
    keep["measured_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    doc = dict(cal, lidar=keep)
    try:
        CAL_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        logger.warning("lidar check not saved", exc_info=True)
        return
    _CAL["value"] = doc
    _LIDAR["saved"] = keep


def _clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def _limits() -> tuple[int, int, int]:
    pan = max(0, min(100, int(settings.head_pan_max)))
    down = max(0, min(100, int(settings.head_tilt_down_max)))
    up = max(0, min(100, int(settings.head_tilt_up_max)))
    return pan, down, up


def _valid_full(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and CAL_FULL_RANGE[0] <= abs(value) <= CAL_FULL_RANGE[1])


def load_calibration(path: Path | None = None) -> dict | None:
    """The saved sweep, or None (none yet, or a file that does not hold two
    usable numbers -- that one is measured again rather than trusted)."""
    path = CAL_PATH if path is None else path
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("head calibration %s unreadable -- measuring again", path.name)
        return None
    if not isinstance(doc, dict) or not (_valid_full(doc.get("pan_deg_per_100"))
                                         and _valid_full(doc.get("tilt_deg_per_100"))):
        logger.warning("head calibration %s holds no usable numbers -- measuring again", path.name)
        return None
    return doc


def _calibration() -> dict | None:
    if not _CAL["loaded"]:
        _CAL["loaded"] = True
        _CAL["value"] = load_calibration()
    return _CAL["value"]


def full_deg() -> tuple[float, float]:
    """Degrees the head turns / tilts at +100 %, SIGNED (a negative pan = "+"
    turns the camera to the robot's right on this robot): the sweep's when
    there is one, else HEAD_PAN_FULL_DEG / HEAD_TILT_FULL_DEG."""
    cal = _calibration() if settings.head_calibrate else None
    if cal is not None:
        return float(cal["pan_deg_per_100"]), float(cal["tilt_deg_per_100"])
    return float(settings.head_pan_full_deg), float(settings.head_tilt_full_deg)


def _per_pct(full: float) -> float:
    """Degrees per percent, never zero (a zero would divide)."""
    sign = -1.0 if full < 0 else 1.0
    return sign * max(1.0, abs(full)) / 100.0


def _reach_deg() -> float:
    """How far off ahead (true degrees) a turn still brings somebody into the picture: the pan cap
    plus LOOK_PAST_DEG, inside the lens's half width (+-60 % = ~22 deg, + 6) -- face_approach's
    `_pan_for` rule, so the head and the walk-up never disagree about who is out of reach."""
    return _limits()[0] * PAN_DEG_PER_PCT + min(LOOK_PAST_DEG, float(settings.robot_camera_hfov_measured or 0.0) / 2.0)


def _walk_pending() -> bool:
    """An event walk on its way to the wheels (the arm's rest first): `_walking` waits for the wheels,
    and a pan now would be undone by the walk's centring as they start (review 9 Nov)."""
    try:
        from app import event_walk

        return bool(event_walk.walking())
    except Exception:  # noqa: BLE001
        return False


def _approach_looked_recently() -> bool:
    """The walk-up turned the head to look within its gap: this pan waits as well."""
    try:
        from app import face_approach

        return bool(face_approach._looked_recently(time.monotonic()))
    except Exception:  # noqa: BLE001
        return False


def _note_head_turned() -> None:
    """A lidar pan counts toward the walk-up's own gap between looks (face_approach `head_at`, the
    servo's 20 s): the two otherwise could look one after the other (review 9 Nov)."""
    try:
        from app import face_approach

        face_approach.STATE["head_at"] = time.monotonic()
    except Exception:  # noqa: BLE001
        pass


def _lens_measured() -> bool:
    """The head camera's lens measured on the robot (ROBOT_CAMERA_HFOV_MEASURED) and a sweep to read
    the head's sign from: enough to aim at a lidar bearing without the lidar check (`_measured_pan`)."""
    return float(settings.robot_camera_hfov_measured or 0.0) > 0 and (
        settings.head_calibrate and _calibration() is not None)


def _measured_pan(bearing: float) -> int | None:
    """Percent of pan that points the head camera at a lidar bearing (+ = the robot's left, true
    degrees), from what was measured on the robot instead of the lidar check -- None past what the
    head and the lens reach together (the camera would not see them from the limit either).

    9 Nov, the robot's log: the check never held and never could -- it expects a lens near the 70
    deg guess and the robot's is 19, and its pairs (a face and the one lidar person nearby) are
    mostly not the same person (6 and 9 Nov: head_sign/scale/disagree every time) -- so a voice
    with no face in view only ever looked down, while the person stood beside the robot. Its four
    facts are all measured: the lidar counts to the left (map_check lays its rays on the map), the
    picture is mirrored (a box label in the sweep's pictures reads right only flipped), 0.37 deg a
    percent (the dock ladder), and "+" turns left when the sweep's sign and the mirroring disagree
    -- a sweep reads the picture, so a mirrored picture flips what its sign says (on the robot:
    -94 and mirrored; face_approach assumes "+" = left outright -- a test holds the two equal on
    the robot's numbers)."""
    cal = _calibration() if settings.head_calibrate else None
    hfov = float(settings.robot_camera_hfov_measured or 0.0)
    if cal is None or hfov <= 0 or not math.isfinite(float(bearing)):
        return None
    pan_max, _down, _up = _limits()
    if abs(bearing) > _reach_deg():
        return None
    left_is_plus = (float(cal["pan_deg_per_100"]) > 0) != bool(settings.robot_camera_mirrored)
    pct = float(bearing) / PAN_DEG_PER_PCT
    return _clamp(pct if left_is_plus else -pct, -pan_max, pan_max)


def _set_head(pan: int, tilt: int) -> None:
    pan_full, tilt_full = full_deg()
    pan_deg, tilt_deg = pan / 100.0 * pan_full, tilt / 100.0 * tilt_full
    if (pan_deg, tilt_deg) != (_HEAD["pan_deg"], _HEAD["tilt_deg"]):
        _HEAD["moved_at"] = _clock()
    _HEAD["pan_deg"] = pan_deg
    _HEAD["tilt_deg"] = tilt_deg
    _HEAD["pan_pct"], _HEAD["tilt_pct"] = int(pan), int(tilt)


def _approach_walking() -> bool:
    """Her own walk up to somebody (face_approach, event_walk's NO_RESUME_SPOT): a few metres, and the head
    has already looked; the walk scan's nod would be one more move in its budget (review 6 Nov)."""
    try:
        from app import event_walk

        run = event_walk.current()
        return bool(run is not None and event_walk.walking() and run.spot == event_walk.NO_RESUME_SPOT)
    except Exception:  # noqa: BLE001
        return False


def _pageant() -> bool:
    """The Emma Event debut's pageant owns the head (app/event_pageant.py). Never raises."""
    try:
        from app import event_pageant

        return event_pageant.active()
    except Exception:  # noqa: BLE001
        return False


def _tour() -> bool:
    """Card 1's tour holds the head still from its press to its end (owner 23 Nov: "ไม่ต้องให้หุ่นขยับหัวนะ";
    app/event_tour.py). Never raises."""
    try:
        from app import event_tour

        return event_tour.holds_head()
    except Exception:  # noqa: BLE001
        return False


def _ceremony_still(session) -> bool:
    """CEREMONY_HEAD_STILL: this is a ceremony call (profile `ceremony`, the Emma Ceremony app) and the head
    is to stay where it is (owner 24 Nov: "ไม่ต้องให้หุ่นหันหัวไปมาตอนพูดแล้วให้ขยับมือเหมือนที่ทำตอนแรกพอ").
    Never raises."""
    try:
        return bool(settings.ceremony_head_still) and getattr(session, "profile", None) == "ceremony"
    except Exception:  # noqa: BLE001
        return False


def _walking(session) -> bool:
    try:
        from app import event_walk

        if event_walk.moving():                    # the wheels, not the arm's rest before them
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from app import robot_face_link

        if robot_face_link.nav_activity(session) in ("active", "stale"):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from app import walk_frames

        # board_walk over adb / a joystick: the app sees no walk (4 Nov) --
        # without this the head would follow faces through one
        return bool(walk_frames.base_moving())
    except Exception:  # noqa: BLE001
        return False


def _base_still_s() -> float | None:
    try:
        from app import walk_frames

        return walk_frames.still_s()
    except Exception:  # noqa: BLE001 -- unknown = moving: no nod
        return None


def _elevation(track, after: float) -> float | None:
    """Degrees above the camera axis of `track`'s box, from a frame taken
    after `after`; None when there is no such box."""
    if track is None:
        return None
    from app import facewatch

    width, height = facewatch.latest_frame_size()
    if not width or not height:
        return None
    for tid, bbox, _kps, t in facewatch.latest_tracks():
        if tid == track:
            if t < after:
                return None
            _x1, y1, _x2, y2 = bbox
            return facewatch.elevation_deg((y1 + y2) / 2.0, width, height)
    return None


def _lock_by_speaker() -> bool:
    """HEAD_LOCK_BY=speaker (owner 12 Nov); anything else is the 10 Nov "first". Only with
    HEAD_FOLLOW_FIRST: without the lock the head follows attention's cue as before."""
    return bool(settings.head_follow_first) and str(settings.head_lock_by or "").strip().lower() == "speaker"


def _fresh(track, after: float) -> bool:
    """The attention's face was seen in a frame taken after `after`."""
    if track is None:
        return False
    from app import facewatch

    return any(tid == track and t >= after for tid, _b, _k, t in facewatch.latest_tracks())


def _faces_in_view(after: float, now: float) -> list[tuple[int, float, bool]]:
    """(track, bearing, fresh) of each face in the camera's last frame wide
    enough to be engaged (focus's own bar) -- fresh = from a frame taken after
    `after`, the head's last move over. Bearings in facewatch's units, the
    ones `focus.attention` and the sweep use.

    A frame older than FOCUS_HOLD_S is nobody (`focus._engaged_faces`'s own
    window): facewatch keeps its last frame when the camera stalls or a face
    scan pauses the greeter, and a lock fed from it held the head turned
    for ever (review 10 Nov)."""
    from app import facewatch, focus

    width, _height = facewatch.latest_frame_size()
    if not width:
        return []
    bar = focus.face_min_px()
    oldest = now - max(0.0, float(settings.focus_hold_s)) - 0.5
    out = []
    for tid, bbox, _kps, t in facewatch.latest_tracks():
        x1, _y1, x2, _y2 = bbox
        if x2 - x1 < bar or t < oldest:
            continue
        out.append((tid, facewatch.bearing_deg((x1 + x2) / 2.0, width), t >= after))
    return out


# ---- the sweep's arithmetic ----------------------------------------------------

def _gray(frame):
    import cv2
    import numpy as np

    img = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    scale = CAL_WIDTH / float(img.shape[1])
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return small.astype(np.float32), scale


def _texture(gray) -> float:
    import cv2
    import numpy as np

    return float(np.std(cv2.Laplacian(gray, cv2.CV_32F)))


def shift_deg(a, b) -> tuple[float, float, float]:
    """How the scene moved from picture `a` to picture `b`, as (bearing,
    elevation, response): degrees in the camera's own units (+bearing = the
    scene moved to the robot's left, +elevation = up -- facewatch's signs),
    and phase correlation's peak (how sure)."""
    import cv2

    from app import facewatch

    ga, scale = _gray(a)
    gb, _ = _gray(b)
    window = cv2.createHanningWindow((ga.shape[1], ga.shape[0]), cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(ga, gb, window)
    width = float(a.shape[1])
    focal = (width / 2.0) / math.tan(math.radians(facewatch.ROBOT_CAMERA_HFOV_DEG / 2.0))
    # content moving right (dx > 0) is moving toward the robot's right
    bearing = -math.degrees(math.atan2(dx / scale, focal))
    elevation = -math.degrees(math.atan2(dy / scale, focal))
    return bearing, elevation, float(response)


def measure(frames: dict, pan_pct: int = CAL_PAN, tilt_pct: int = CAL_TILT) -> dict:
    """Degrees per 100 % from the sweep's five pictures (centre, left, right,
    down, back). Turning the head to its left by d slides the scene d to the
    robot's right (bearing -d); tilting down by d slides it d up."""
    out: dict = {"ok": False, "reason": None, "pan_deg_per_100": None, "tilt_deg_per_100": None,
                 "pan_pct": pan_pct, "tilt_pct": tilt_pct}
    texture = min(_texture(_gray(frames[k])[0]) for k in ("centre", "left", "right", "down"))
    out["texture"] = round(texture, 2)
    if texture < CAL_MIN_TEXTURE:
        out["reason"] = "no_texture"
        return out
    slid = {k: shift_deg(frames["centre"], frames[k]) for k in ("left", "right", "down", "back")}
    out["shifts"] = {k: [round(v[0], 2), round(v[1], 2), round(v[2], 3)] for k, v in slid.items()}
    left, right, down, back = (slid[k] for k in ("left", "right", "down", "back"))
    if min(v[2] for v in (left, right, down)) < CAL_MIN_RESPONSE:
        out["reason"] = "no_match"
        return out
    if abs(left[0]) < CAL_MIN_DEG or abs(right[0]) < CAL_MIN_DEG:
        # the app said it moved: the camera is not on the head, or the head is stuck
        out["reason"] = "no_motion"
        return out
    if left[0] * right[0] >= 0 or not (1 / CAL_SYMMETRY <= abs(left[0]) / abs(right[0]) <= CAL_SYMMETRY):
        out["reason"] = "asymmetric"
        return out
    if abs(left[1]) > max(1.5, abs(left[0])) or abs(down[0]) > max(1.5, abs(down[1])):
        # a pan that slides the picture up/down: the camera sits rotated
        out["reason"] = "axes_crossed"
        return out
    if abs(down[1]) < CAL_MIN_DEG:
        out["reason"] = "no_tilt"
        return out
    if math.hypot(back[0], back[1]) > CAL_RETURN_MAX_DEG:
        out["reason"] = "no_return"
        return out
    pan_full = -(left[0] - right[0]) / (2.0 * pan_pct) * 100.0
    tilt_full = down[1] / float(tilt_pct) * 100.0
    out["pan_deg_per_100"] = round(pan_full, 2)
    out["tilt_deg_per_100"] = round(tilt_full, 2)
    if not (_valid_full(pan_full) and _valid_full(tilt_full)):
        out["reason"] = "out_of_range"
        return out
    out["ok"] = True
    return out


async def _frame_after_traced(t: float, since: float, ref) -> tuple:
    """The head camera's first picture taken at or after `t` (decoded; None
    after CAL_FRAME_WAIT_S more), its time after `since` (the look's send),
    and how every frame arriving after `since` sat against `ref` (the
    previous pose's picture; None = the first frame seen): [s after send,
    bearing, elevation, response] -- watched SWEEP_TRACE_AFTER_S past the
    chosen one, for the bench (SWEEP_DIR)."""
    from app import robot_camera

    deadline = t + CAL_FRAME_WAIT_S
    chosen = chosen_at = None
    trace: list = []
    last_seq = None
    while True:
        seq, jpeg, at = robot_camera.feed.latest()
        if jpeg and at and at >= since and seq != last_seq:
            last_seq = seq
            frame = robot_camera.feed.decoded_for(seq, jpeg)
            if frame is not None:
                if ref is None:
                    ref = frame
                try:
                    b, e, r = shift_deg(ref, frame)
                    trace.append([round(at - since, 2), round(b, 2), round(e, 2), round(r, 3)])
                except Exception:  # noqa: BLE001 -- the trace is for the bench, never the sweep's undoing
                    pass
                if chosen is None and at >= t:
                    chosen, chosen_at = frame, at
        now = _clock()
        if chosen is not None and now >= chosen_at + SWEEP_TRACE_AFTER_S:
            return chosen, trace, round(chosen_at - since, 2)
        if chosen is None and now > deadline:
            return None, trace, None
        await _sleep(0.1)


def _save_sweep(frames: dict, steps: list, result: dict) -> None:
    """The bench's copy of a sweep (SWEEP_DIR): one JPEG per pose + sweep.json.
    Never raises -- the sweep's outcome does not depend on a disk."""
    try:
        import cv2

        SWEEP_DIR.mkdir(parents=True, exist_ok=True)
        for old in list(SWEEP_DIR.glob("*.jpg")) + list(SWEEP_DIR.glob("sweep.json")):
            old.unlink()
        for i, (name, frame) in enumerate(frames.items()):
            scale = 640.0 / frame.shape[1]
            small = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                (SWEEP_DIR / ("%d_%s.jpg" % (i, name))).write_bytes(buf.tobytes())
        doc = {"measured_at": _dt.datetime.now().isoformat(timespec="seconds"),
               "result": {k: result.get(k) for k in ("ok", "reason", "pan_deg_per_100", "tilt_deg_per_100",
                                                     "texture", "shifts")},
               "settle": {"camera_lag_s": CAMERA_LAG_S, "extra_s": CAL_SETTLE_EXTRA_S},
               "steps": steps}
        (SWEEP_DIR / "sweep.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("head calibration: sweep pictures not saved", exc_info=True)


class HeadFollow:
    """One per robot-face call (session.py's engagement watcher ticks it)."""

    def __init__(self, session) -> None:
        self.session = session
        self.pan = 0                       # where the head was last told (believed)
        self.tilt = 0
        self.confirmed = (0, 0)            # ...and the last look the app accepted
        self.seq = 0
        self.pending: dict | None = None   # the look whose answer has not come
        self.answers: dict[int, tuple[bool, str | None, int | None]] = {}
        self.sent_at = -math.inf
        self.settle_at = -math.inf         # frames from before this are of the old pose
        self.target_at = -math.inf
        self.hold_until = -math.inf
        self.searched_at = -math.inf
        self.walk_centred = False
        self.logged_at = -math.inf
        self.logged_why = None
        self.refused_at = -math.inf
        self.refused_why: str | None = None
        self.cal_wanted_at: float | None = None
        self.last_step: dict | None = None
        self.strikes = 0
        self.off: str | None = None        # why this call does not follow (sweep failed, diverging)
        # the walk scan (HEAD_WALK_SCAN): the next pose, when the last was sent,
        # whether the app refused it this walk
        self.walk_scan_i = 0
        self.walk_scan_at = -math.inf
        self.walk_scan_off = False
        # the first person (HEAD_FOLLOW_FIRST): who the head follows, and
        # whether the head must come back to centre before the next one
        self.lock: dict | None = None
        self.relock_blocked = False
        # HEAD_LOCK_BY=speaker: since when each face track has been in the picture (frame
        # times: [first, last]), when any face last was, and until when a face found is the
        # one a voice-aimed look went to
        self.in_view: dict = {}
        self.voice_aim_until = -math.inf
        # HEAD_SEARCH_NEEDS_LIDAR: when a skipped look down was last written down
        self.skipped_at = -math.inf
        # CEREMONY_HEAD_STILL: the one centring look of this call has been sent
        self.ceremony_centred = False
        # a refused centring look is asked again no sooner than this (the app's 60 s stop latch answers every ask)
        self.centre_again_at = -math.inf

    # ---- the app's answer, relayed by the page --------------------------------

    def ack(self, event: dict) -> None:
        """`head_look_ack` {seq, accepted, reason, ms}: whitelisted, matched to
        the look it answers; anything else is dropped."""
        seq = event.get("seq") if isinstance(event, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            return
        accepted = event.get("accepted") is True
        reason = event.get("reason")
        reason = reason if (not accepted and isinstance(reason, str) and _REASON.match(reason)) else None
        ms = event.get("ms")
        ms = ms if isinstance(ms, int) and not isinstance(ms, bool) and 0 <= ms <= 10000 else None
        self.answers[seq] = (accepted, reason, ms)
        while len(self.answers) > 8:
            self.answers.pop(next(iter(self.answers)))
        pending = self.pending
        if pending is None or pending["seq"] != seq:
            return
        self.pending = None
        if accepted:
            self.confirmed = (pending["pan"], pending["tilt"])
            if ms is not None and pending["why"] != "hold":
                self.settle_at = pending["at"] + ms / 1000.0 + CAMERA_LAG_S
                _HEAD["settle_at"] = self.settle_at
                if pending["why"] == "approach":
                    self.hold_until = max(self.hold_until, self.settle_at + LOOK_HOLD_S)
            return
        # refused: the head is where the last accepted look left it
        self.pan, self.tilt = self.confirmed
        _set_head(self.pan, self.tilt)
        # The refused move never happened; one before it, accepted, may still be on its way (review 6 Nov).
        self.settle_at = max(float(pending.get("prev_settle", -math.inf)), pending["at"])
        _HEAD["settle_at"] = self.settle_at
        self.last_step = None
        if pending.get("blocked"):
            # the centring after the first person left never happened: still back to centre first
            # (review 10 Nov: a refused idle let the bystander be locked from the turned head)
            self.relock_blocked = True
        if pending["why"] == "walk":
            self.walk_centred = False
        if pending["why"] == "walk_scan" and reason in WALK_SCAN_REFUSALS:
            # an APK without the walk box, or a walk the app cannot see: ahead
            # for the rest of this walk (centred by the walk branch), asked once
            self.walk_scan_off = True
            self.walk_centred = False
        now = _clock()
        if pending["why"] in ("ceremony_centre", "recentre"):
            # A centring that was refused (a red stop's 60 s latch, a group still on the arm) never happened: the head
            # is still where nobody knows -- asked again every TRIM_RESEND_S, not forgotten (review 25 Nov: after a red
            # stop mid-handshake it stayed where the stop froze it until the next gesture's end).
            _HEAD["known"] = False
            if pending["why"] == "ceremony_centre":
                self.ceremony_centred = False
            self.centre_again_at = now + TRIM_RESEND_S
        if reason != self.refused_why or now - self.refused_at >= LOG_EVERY_S:
            self.refused_at, self.refused_why = now, reason
            _record(why="refused", reason=reason or "unknown", pan=pending["pan"], tilt=pending["tilt"])
        if reason in STOPPING_REFUSALS and pending["why"] != "calibrate":
            # the switch is off (or an APK that cannot look): asking again
            # every second only fills the log; the next call asks again
            self._stop("app_" + reason)

    # ---- the loop ---------------------------------------------------------------

    async def tick(self, now: float | None = None) -> None:
        if not settings.head_follow or self.off:
            return
        now = _clock() if now is None else now
        from app import focus

        if _pageant():
            # the Emma Event debut's pageant turns the head itself (Pageant.java, owner 9 Nov): no
            # look -- the app would refuse it `busy` -- and afterwards nobody knows where the head
            # is (its end is group 99), so the next tick centres it slowly ("recentre")
            _HEAD["known"] = False
            self._unlock(now, "pageant")
            self.voice_aim_until = -math.inf      # a voice look from before is not about now
            return
        if _tour():
            # card 1's tour (owner 23 Nov: "ไม่ต้องให้หุ่นขยับหัวนะ"): no look at all through its walks and its
            # handshakes -- not the walk's centring or scan, not a face, not a look down, not the lidar's, not a
            # face approach's hint, not a recentre. Group 6 (the handshake) moves the head itself: after the tour
            # nobody knows where it is, and the next tick centres it slowly ("recentre"), as after the pageant.
            _HEAD["known"] = False
            self._unlock(now, "tour")
            self.voice_aim_until = -math.inf
            return
        if _ceremony_still(self.session):
            # CEREMONY_HEAD_STILL (owner 24 Nov): in the ceremony the head is the ONE thing that does not move -- the
            # arms speak. No face lock, no lidar or voice turn, no look down, no face approach's hint, no calibration
            # sweep, no walk centring or scan: nothing but ONE look to centre, the first tick of the call (where the
            # head was left is nobody's guess: after a pageant, or a call that ended off centre). A staff/body command
            # (move_body, the staff pages) goes round this loop and is not touched.
            self._unlock(now, "ceremony")
            self.voice_aim_until = -math.inf
            # ... and once more after card 1's pageant / tour in this same call (the branches above set "known" False:
            # group 99 / the handshake's group 6 leave the head where nobody knows -- review 24 Nov: without this it
            # stayed there for the rest of the ceremony). The app moves a head it does not know at its slowest.
            # HEAD_PAN_TRIM_PCT: a straight head is not the app's own centre, and the app takes the head there after 10 s
            # with no look -- re-sent while there is a trim (owner 25 Nov: the head looked left of the body)
            trim = bool(getattr(settings, "head_pan_trim_pct", 0))
            if ((not self.ceremony_centred or not _HEAD.get("known", True)
                 or (trim and now - self.sent_at >= TRIM_RESEND_S))
                    and now >= self.centre_again_at
                    and await self._send(0, 0, now, "ceremony_centre", force=True)):
                self.ceremony_centred = True
                _HEAD["known"] = True
                self.settle_at = max(self.settle_at, now + UNACKED_MOVE_S + CAMERA_LAG_S)
                _HEAD["settle_at"] = self.settle_at
            return
        if _walking(self.session):
            self._unlock(now, "walk")            # the base turns: the spot in the room is gone
            self.voice_aim_until = -math.inf
            if settings.head_walk_scan and not self.walk_scan_off and not _approach_walking():
                await self._walk_scan(now)
                return
            if not self.walk_centred and await self._send(0, 0, now, "walk", force=True):
                self.walk_centred = True
            return
        # a walk that ended with the head off ahead needs nothing here: the loop
        # below starts from where it was told (self.pan/tilt) and its no-target
        # path centres it (tested: test_a_walk_with_the_scan_sweeps_round...)
        self.walk_centred = False
        self.walk_scan_off = False
        self.walk_scan_i = 0
        if settings.head_calibrate and _calibration() is None:
            if _CAL["failed"]:
                self._stop("uncalibrated:" + _CAL["failed"])
                return
            from app import robot_face_link

            caps = robot_face_link.caps(self.session, "body")
            if caps is None:
                return                 # the page has not said what the app can do yet
            if caps.get("look") is not True:
                self._stop("app_cannot_look")      # an APK from before 31 Oct
                return
            if self.cal_wanted_at is None:
                self.cal_wanted_at = now
            playing = float(getattr(self.session, "_play_until", 0.0) or 0.0) > _clock()
            if playing and now - self.cal_wanted_at < CAL_QUIET_WAIT_S:
                return
            await self._calibrate()
            return
        if not _HEAD.get("known", True):
            # Left where nobody knows (`_heading_home`): one slow centre before anything reads where it
            # points. The app moves a head it does not know at its slowest (UNKNOWN_MS).
            if now >= self.centre_again_at and await self._send(0, 0, now, "recentre", force=True):
                _HEAD["known"] = True
                self.settle_at = max(self.settle_at, now + UNACKED_MOVE_S + CAMERA_LAG_S)
                _HEAD["settle_at"] = self.settle_at
            return
        self._note_faces(now)
        cue = focus.attention(now)
        doa_cue = None
        if cue.get("source") == "doa":
            # Not a face to centre: the loop below corrects a face's error in
            # the camera, and a DOA bearing is no such error to re-measure.
            # With HEAD_FOLLOW_DOA it may aim the one-shot search instead.
            doa_cue = cue if settings.head_follow_doa else None
            cue = {"bearing": None, "source": None, "track": None}
        if settings.head_follow_first:
            cue = self._first_person(cue, now)
        if cue.get("bearing") is not None:
            self.target_at = now
            track = cue.get("track")
            if _fresh(track, self.settle_at):
                self._pair(float(cue["bearing"]), now)
                self._judge_step(track, float(cue["bearing"]))
                if self.off:
                    return
                if now - self.sent_at >= SEND_GAP_S:
                    want, elev = self._toward(cue)
                    if await self._send(*want, now, cue.get("source") or "face",
                                        bearing=cue["bearing"], elev=elev, track=track):
                        return
            await self._hold(now)
            return
        hinted = _hinted_tilt(now)
        if hinted is not None:
            pan_max, down, up = _limits()
            tilt = _clamp(hinted, -up, down)
            pan = self.pan if _LOOK["pan"] is None else _clamp(_LOOK["pan"], -pan_max, pan_max)
            if (pan, tilt) == (self.pan, self.tilt) or await self._send(pan, tilt, now, "approach", force=True):
                _LOOK["tilt"] = None
                _LOOK["pose"] = (pan, tilt)
                self.searched_at = now
                self.hold_until = max(now + SEARCH_HOLD_S, self.settle_at + LOOK_HOLD_S)
            return
        want = None
        if self._search_due(now):
            _pan, down, _up = _limits()
            want, why = self._doa_look(doa_cue), "doa"
            if want is None:
                want, why = self._lidar_look(now), "lidar"
            if want is None and self._worth_looking_down(now):
                want, why = (self.pan, max(self.tilt, min(down, int(settings.head_search_tilt)))), "search"
            # nobody in the head's reach (HEAD_SEARCH_NEEDS_LIDAR): no look -- the idle centring below
        if want is not None:
            panned = want[0] != self.pan
            if want == (self.pan, self.tilt) or await self._send(*want, now, why, force=True):
                self.searched_at = now
                self.hold_until = now + SEARCH_HOLD_S
                # the face found there next is the voice's (HEAD_LOCK_BY=speaker), from a frame
                # after the move: until the look's hold is over
                self.voice_aim_until = max(self.settle_at, now) + SEARCH_HOLD_S
                if panned:
                    _note_head_turned()
            return
        if (self.pan, self.tilt) != (0, 0) and now > max(self.target_at + IDLE_S, self.hold_until):
            await self._send(0, 0, now, "idle", force=True)
            return
        await self._hold(now)

    async def _hold(self, now: float) -> None:
        """Re-send a pose held off centre before the app's 10 s centring."""
        if (self.pan, self.tilt) != (0, 0) and now - self.sent_at >= HOLD_RESEND_S:
            await self._send(self.pan, self.tilt, now, "hold", force=True)

    def _first_person(self, cue: dict, now: float) -> dict:
        """HEAD_FOLLOW_FIRST: the cue the head follows -- the first person it
        turned to, until they are gone (LOCK_LOST_S), not `attention`'s pick
        of the moment. `cue` is attention's (its DOA already taken out).

        No lock yet: attention's face becomes the lock once it is in a frame
        taken after the head settled (where it stands in the room is known
        then); before that the cue goes through as it is -- a stale frame
        moves nothing either way (the loop's `_fresh`). Locked: that track,
        else the face nearest its spot (LOCK_NEAR_DEG) that was never in a
        frame together with them; through a detector's
        blink (FOCUS_HOLD_S) still the target with no step; then nobody --
        the head holds and turns to no one else -- until LOCK_LOST_S."""
        none = {"bearing": None, "source": None, "track": None}
        head = offset_deg()[0]
        faces = _faces_in_view(self.settle_at, now)
        lock = self.lock
        if lock is None:
            if self.relock_blocked:
                return none                       # back to centre first (the idle branch)
            if _lock_by_speaker():
                pick = self._who_to_lock(cue, now, faces, head)
                if pick is None:
                    return none                   # somebody passing, or nobody yet: no turn
                track, bearing, source = pick
            else:
                track = cue.get("track")
                if cue.get("bearing") is None or track is None:
                    return cue
                if not any(tid == track and fresh for tid, _b, fresh in faces):
                    return cue
                bearing, source = float(cue["bearing"]), cue.get("source")
            self.lock = {"track": track, "room": bearing + head, "bearing": bearing, "seen": now,
                         "since": now, "relocks": 0, "elsewhere": 0, "other": None,
                         "beside": {tid for tid, _b, _f in faces if tid != track}}
            self.voice_aim_until = -math.inf      # that look found its face; the next voice aims anew
            _record_lock(state="on", source=source, bearing=round(bearing, 1),
                         pan=self.pan, faces=len(faces))
            return {"bearing": bearing, "source": "first", "track": track}
        other = cue.get("track")
        if other is not None and other != lock["track"] and other != lock["other"]:
            lock["elsewhere"] += 1                # attention went to somebody else; the head did not
        lock["other"] = other if other != lock["track"] else None
        found = next(((tid, b, fresh) for tid, b, fresh in faces if tid == lock["track"]), None)
        if found is not None:
            # Seen in one frame with them = somebody else, never them under a new id (review 10 Nov:
            # one frame of the detector missing them handed the head to the neighbour 20 away).
            lock["beside"].update(tid for tid, _b, _f in faces if tid != lock["track"])
        else:
            near = sorted((abs(b + head - lock["room"]), tid, b) for tid, b, fresh in faces
                          if fresh and tid not in lock["beside"])
            if near and near[0][0] <= LOCK_NEAR_DEG:
                _d, tid, b = near[0]
                found = (tid, b, True)
                lock["relocks"] += 1
        if found is not None:
            tid, bearing, fresh = found
            if cue.get("track") == tid and cue.get("bearing") is not None:
                bearing = float(cue["bearing"])   # the arbiter's own number for this face
            lock.update(track=tid, bearing=bearing, seen=now)
            if fresh:
                lock["room"] = bearing + head
            return {"bearing": bearing, "source": "first", "track": tid}
        if now - lock["seen"] <= max(0.0, float(settings.focus_hold_s)):
            # a frame or two without them: still the target (no search, no centring), and no step --
            # track None: a box of theirs too small to count must not be stepped to at the old bearing
            return {"bearing": lock["bearing"], "source": "first", "track": None}
        if now - lock["seen"] <= LOCK_LOST_S:     # the idle branch's own `>`: they end in one tick
            return none                           # hold: nobody else is turned to while they may be back
        # LOCK_LOST_S from when they were last SEEN (the blink's cue kept target_at going): the idle
        # branch counts from there too, so it centres the head in this same tick (review: it was 8 s)
        self.target_at = min(self.target_at, lock["seen"])
        self._unlock(now, "gone")
        return none

    def _who_to_lock(self, cue: dict, now: float, faces: list, head: float) -> tuple | None:
        """HEAD_LOCK_BY=speaker: (track, bearing, why) of the face the lock may take, or None.

        In this order (owner 12 Nov: "คนที่พูดคนแรก หรืออยู่ด้านหน้า"): the face attention
        says spoke ("speaker"); a face in a frame after a look aimed at a loud voice with no
        face in view ("voice_look" -- the look down, the lidar's turn: whoever is there is the
        voice); a face that has been in the picture HEAD_FRONT_STAND_S, the one most straight
        ahead of the robot first ("front"). Only fresh faces (a frame after the head settled):
        where they stand in the room must be known for the lock. Anybody else -- a face
        crossing the lens -- is no lock and no turn: on 12 Nov 35 of 37 locks were gone
        within 10 s."""
        fresh = {tid: b for tid, b, is_fresh in faces if is_fresh}
        if not fresh:
            return None
        track = cue.get("track")
        source = cue.get("source")
        if cue.get("bearing") is not None and track in fresh and source in SPEAKER_SOURCES:
            if source != "label" or self._stood(track) >= SPEAKER_MIN_STAND_S:
                return track, float(cue["bearing"]), "speaker"
        if now <= self.voice_aim_until:
            # aimed at the voice: the face nearest the middle of the picture
            tid = min(fresh, key=lambda k: (abs(fresh[k]), k))
            return tid, fresh[tid], "voice_look"
        stand = max(0.0, float(settings.head_front_stand_s))
        standing = [tid for tid in fresh if self._stood(tid) >= stand]
        if not standing:
            return None
        tid = min(standing, key=lambda k: (abs(fresh[k] + head), k))   # straight ahead of the ROBOT
        return tid, fresh[tid], "front"

    def _note_faces(self, now: float) -> None:
        """Keep, per face track, the first and last frame it was in (frame times). A track
        missing longer than STAND_GAP_S starts again: a detector blink or a slow minute of the
        robot's camera must not reset somebody standing there; a person who walked off and
        back is new."""
        from app import facewatch, focus

        width, _height = facewatch.latest_frame_size()
        if not width:
            return
        hold = max(0.0, float(settings.focus_hold_s))
        bar = focus.face_min_px()
        for tid, bbox, _kps, t in facewatch.latest_tracks():
            x1, _y1, x2, _y2 = bbox
            if x2 - x1 < bar or t < now - hold - 0.5:
                continue
            seen = self.in_view.get(tid)
            if seen is None or t - seen[1] > STAND_GAP_S:
                self.in_view[tid] = [t, t]
            else:
                seen[1] = max(seen[1], t)
        for tid in [k for k, (_first, last) in self.in_view.items() if now - last > STAND_GAP_S + hold + 0.5]:
            del self.in_view[tid]

    def _stood(self, track) -> float:
        """How long this track has been in the picture, from its frames."""
        seen = self.in_view.get(track)
        return 0.0 if seen is None else seen[1] - seen[0]

    def _unlock(self, now: float, why: str) -> None:
        lock, self.lock = self.lock, None
        if lock is None:
            return
        if why == "gone" and (self.pan, self.tilt) != (0, 0):
            self.relock_blocked = True            # cleared by the next aimed look (the idle centring)
        _record_lock(state="off", why=why, followed_s=round(max(0.0, lock["seen"] - lock["since"]), 1),
                     relocks=lock["relocks"], elsewhere=lock["elsewhere"], pan=self.pan)

    def _toward(self, cue: dict) -> tuple[tuple[int, int], float | None]:
        pan_max, down, up = _limits()
        pan_full, tilt_full = full_deg()
        bearing = float(cue["bearing"])
        pan = float(self.pan)
        if abs(bearing) >= PAN_DEADBAND_DEG:
            pan += GAIN * bearing / _per_pct(pan_full)
        tilt = float(self.tilt)
        elev = _elevation(cue.get("track"), self.settle_at)
        if elev is not None and abs(elev) >= TILT_DEADBAND_DEG:
            # a face above the axis = look up = tilt toward minus
            tilt -= GAIN * elev / _per_pct(tilt_full)
        return (_clamp(pan, -pan_max, pan_max), _clamp(tilt, -up, down)), elev

    def _judge_step(self, track, bearing: float) -> None:
        """The face after the last step, against where the step should have
        put it: further out on the same side, by DIVERGE_SHARE of the step or
        more, is the loop turning the wrong way."""
        step, self.last_step = self.last_step, None
        if step is None or step["track"] != track:
            return
        turned = step["turn_deg"]                  # + = the head turned to its left
        if abs(turned) < DIVERGE_MIN_DEG:
            return
        moved = bearing - step["bearing"]         # turning left should make it smaller
        if moved * turned > 0 and abs(moved) >= max(DIVERGE_MIN_DEG, DIVERGE_SHARE * abs(turned)):
            self.strikes += 1
        else:
            self.strikes = 0
        if self.strikes >= DIVERGE_STRIKES:
            self._stop("diverging")

    def _doa_look(self, cue: dict | None) -> tuple[int, int] | None:
        """Where to look for a voice at the robot with no face in view, from
        the mic array (HEAD_FOLLOW_DOA; the app owns the array, ARRAY_MIC).
        focus's DOA bearing is already in the head camera's frame (app/doa.py
        subtracts the head's pan: the array does not turn with the head, the
        camera does), so it is the turn left to make -- taken whole, once,
        like the lidar's look: the face loop takes over when the camera finds
        the face. Down too: a voice this loud is at the robot."""
        if cue is None or cue.get("bearing") is None:
            return None
        bearing = float(cue["bearing"])
        if not math.isfinite(bearing):
            return None
        pan_max, down, _up = _limits()
        pan_full, _tilt_full = full_deg()
        pan = _clamp(self.pan + bearing / _per_pct(pan_full), -pan_max, pan_max)
        tilt = max(self.tilt, min(down, int(settings.head_search_tilt)))
        return pan, tilt

    def _lidar_look(self, now: float) -> tuple[int, int] | None:
        """Where to look for a voice with no face in view, from the nearest
        person on the lidar -- once the lidar's check against the camera
        holds, or else from the lens measured on the robot (`_measured_pan`).
        Neither = none: a lidar never compared with anything could have its
        left and right the other way round."""
        if not settings.lidar_people:
            return None
        fit = lidar_fit()
        if fit is None and not _lens_measured():
            return None
        from app import lidar_people

        near = [p for p in lidar_people.people(now) if p.distance <= LIDAR_TURN_M]
        if not near:
            return None
        pan_max, down, _up = _limits()
        if fit is not None:
            person = near[0]
            cam = lidar_people.camera_from_lidar(fit, person.bearing)
            if cam is None:
                return None
            pan_full, _tilt_full = full_deg()
            pan = _clamp(cam / _per_pct(pan_full), -pan_max, pan_max)
        else:
            if _walk_pending() or _approach_looked_recently():
                return None
            reach = _reach_deg()
            # Somebody right beside the robot past what the head reaches is the likeliest voice: no
            # turn to anyone else then, only the look down (HEAD_SEARCH_NEEDS_LIDAR: not even that). Further out the lidar's "people" beside
            # it are mostly things (9 Nov: -85..-91 deg at 1.5-1.6 m all morning) -- they block nothing.
            if any(abs(p.bearing) > reach and p.distance <= LIDAR_CLOSE_M for p in near):
                return None
            inside = [p for p in near if abs(p.bearing) <= reach]
            if not inside:
                return None
            person = inside[0]
            pan = _measured_pan(person.bearing)
            if pan is None:
                return None
        tilt = self.tilt
        if person.distance <= LIDAR_CLOSE_M:
            tilt = max(self.tilt, min(down, int(settings.head_search_tilt)))
        return pan, tilt

    def _worth_looking_down(self, now: float) -> bool:
        """HEAD_SEARCH_NEEDS_LIDAR: a look down for a loud voice only with somebody on the lidar within
        the head's reach (true degrees, `_reach_deg`) and LIDAR_TURN_M -- the one the look can find.
        12 Nov with the GCK mic: 39 looks down and back in 37 min, a face in 45 of 5,971 frames, the
        nearest person mostly 60-100 deg off ahead. Nobody in reach: no look, and a `search_skipped`
        row (once per SEARCH_EVERY_S) says where the nearest one was -- the numbers a base turn toward
        them would start from. No fresh scan or one still gathering its background, a lock held, or
        the switch or LIDAR_PEOPLE off: the 10 Nov search.

        Known gap (review 12 Nov): somebody who stood still in reach since before the tracker's last
        reset is its background from the start, and so "nobody" until they move."""
        if not (settings.head_search_needs_lidar and settings.lidar_people):
            return True
        if self.lock is not None:
            # The person the head follows went under the camera (they stepped up to the chest screen):
            # the 10 Nov look down finds them again. The lidar may not show them -- a person who stands
            # still becomes its background in ~80-96 s (review 12 Nov, simulated on its Tracker).
            return True
        from app import lidar_people

        if not lidar_people.background_ready(now):
            return True                           # no scan, or a tracker warming up: not "nobody"
        reach = _reach_deg()
        near = [p for p in lidar_people.people(now) if p.distance <= LIDAR_TURN_M]
        if any(abs(p.bearing) <= reach for p in near):
            return True
        if now - self.skipped_at >= SEARCH_EVERY_S:
            self.skipped_at = now
            beside = near[0] if near else None
            _record(why="search_skipped", reason="beside" if beside else "nobody", pan=self.pan, tilt=self.tilt,
                    beside_deg=None if beside is None else round(float(beside.bearing), 1),
                    beside_m=None if beside is None else round(float(beside.distance), 2),
                    reach_deg=round(reach, 1))
        return False

    def _pair(self, bearing: float, now: float) -> None:
        """One face in the picture and one person on the lidar, at the same
        moment, after the head settled: a pair for the lidar's check."""
        if not settings.lidar_people:
            return
        from app import facewatch, lidar_people

        if len(facewatch.latest_tracks()) != 1:
            return
        near = [p for p in lidar_people.people(now, max_age=1.0) if p.distance <= LIDAR_PAIR_M]
        if len(near) != 1:
            return
        head = offset_deg()[0]
        check = lidar_people.check()
        if not check.add(bearing + head, near[0].bearing, head, now):
            return
        n = len(check.pairs)
        if n < lidar_people.MIN_PAIRS or n % LIDAR_SOLVE_EVERY:
            return
        fit = check.solve()
        was = (_LIDAR["fit"] or {}).get("trusted")
        _LIDAR["fit"] = fit
        _record_lidar(fit)
        if fit.get("trusted"):
            _save_lidar(fit)
            if not was:
                logger.info("lidar check holds: lidar = %.2f x camera %+.1f deg (camera ~%.0f deg wide)",
                            fit["a"], fit["b"], fit.get("hfov_deg") or 0)

    def _stop(self, why: str) -> None:
        self.off = why
        logger.warning("head follow off for this call: %s", why)
        _record(why="off", reason=why, pan=self.pan, tilt=self.tilt)
        # the head goes back to centre: the app's own 10 s centring does it
        # with nobody sending, and a look now could be the wrong way again
        _heading_home(unknown=False)

    def _search_due(self, now: float) -> bool:
        if int(settings.head_search_tilt) <= 0 or now - self.searched_at < SEARCH_EVERY_S:
            return False
        if now - self.target_at < SEARCH_AFTER_S:
            return False
        if _lock_by_speaker() and self.lock is None and _faces_in_view(-math.inf, now):
            # a face in view that is no lock yet: the segment's close says whether the voice
            # was theirs ("only_face" -> attention's label) -- not a look away from them. With a
            # lock the search stays (their face gone under the camera: the look down finds them
            # again). The window is the frames focus still counts (FOCUS_HOLD_S): the robot's
            # frame times are ~1 s behind `now` (review 12 Nov: frame time + 1 s never held)
            return False
        from app import focus

        at, rms = focus.last_voice()
        return (at is not None and 0 <= now - at <= SEARCH_VOICE_AGE_S
                and rms is not None and rms >= float(settings.head_search_rms))

    async def _send(self, pan: int, tilt: int, now: float, why: str, *, force: bool = False,
                    bearing: float | None = None, elev: float | None = None, track=None) -> bool:
        if (pan, tilt) == (self.pan, self.tilt) and why not in ("walk", "walk_scan", "hold", "calibrate", "recentre",
                                                  "ceremony_centre"):
            return False
        if not force and max(abs(pan - self.pan), abs(tilt - self.tilt)) < MIN_STEP:
            return False
        if now - self.sent_at < (FORCE_GAP_S if force else SEND_GAP_S):
            return False
        from app import robot_face_link

        self.seq += 1
        if not await robot_face_link.look(self.session, pan, tilt, seq=self.seq):
            return False
        blocked = self.relock_blocked and why in _AIMED
        if why in _AIMED:
            if pan != self.pan:
                self._unlock(now, why)            # aimed elsewhere: whoever is there is the next
            self.relock_blocked = False
        pan_full, _tilt_full = full_deg()
        if bearing is not None and why not in ("hold",):
            self.last_step = {"track": track, "bearing": float(bearing),
                              "turn_deg": (pan - self.pan) / 100.0 * pan_full}
        self.pending = {"seq": self.seq, "pan": pan, "tilt": tilt, "at": now, "why": why,
                        "prev_settle": self.settle_at, "blocked": blocked}
        moved = (pan, tilt) != (self.pan, self.tilt) or why != "hold"
        self.pan, self.tilt, self.sent_at = pan, tilt, now
        if moved:
            # A look asked from outside (face_approach) waits for the app's answer, or its slowest move.
            self.settle_at = now + (UNACKED_MOVE_S + CAMERA_LAG_S if why == "approach" else SETTLE_S)
            _HEAD["settle_at"] = self.settle_at
        _set_head(pan, tilt)
        # (every ceremony centre is written down: 25 Nov the centres after group 99 went out 2-3 s after the last row and
        # the 5 s throttle hid them -- the log read as "no centre after the rest" while the head was off for another reason)
        if why != "hold" and (why != self.logged_why or now - self.logged_at >= LOG_EVERY_S or why == "ceremony_centre"):
            self.logged_at, self.logged_why = now, why
            _record(why=why, pan=pan, tilt=tilt,
                    bearing=None if bearing is None else round(float(bearing), 1),
                    elev=None if elev is None else round(float(elev), 1))
        return True

    async def _walk_scan(self, now: float) -> None:
        """While the base stands still on a walk: WALK_SCAN, one pose per
        WALK_SCAN_DWELL_S, once per stop; while it moves (or nobody knows):
        ahead, sent once. The walk pictures carry where the head was told to
        look (`_set_head`), so a nodded picture is read as one (map_check)."""
        still = _base_still_s()
        if still is None or still < WALK_SCAN_STILL_S:
            self.walk_scan_i = 0                      # the next stop nods afresh
            if (self.pan, self.tilt) != (0, 0) or not self.walk_centred:
                if now - self.walk_scan_at < WALK_SCAN_DWELL_S:
                    return                            # the last nod has its time first
                if await self._send(0, 0, now, "walk", force=True):
                    self.walk_centred = True
                    self.walk_scan_at = now
            return
        if self.walk_scan_i >= len(WALK_SCAN) or now - self.walk_scan_at < WALK_SCAN_DWELL_S:
            return                                    # this stop's nod is done: hold it
        pan, tilt = WALK_SCAN[self.walk_scan_i]
        _pan_max, down, _up = _limits()
        pan, tilt = 0, max(0, min(tilt, down))       # a nod only, never past the call's own cap
        if await self._send(pan, tilt, now, "walk_scan", force=True):
            self.walk_scan_i += 1
            self.walk_scan_at = now
            self.walk_centred = False

    # ---- the sweep --------------------------------------------------------------

    async def _look_and_wait(self, pan: int, tilt: int) -> tuple[bool, str | None, float]:
        """One sweep look: sent, its answer awaited (ACK_WAIT_S; a page that
        never answers is taken at its word), asked again while the app says
        busy/too_soon (CAL_RETRY_S), and the time its frame is due."""
        give_up = _clock() + CAL_RETRY_S
        while True:
            now = _clock()
            gap = FORCE_GAP_S - (now - self.sent_at)
            if gap > 0:
                await _sleep(gap)
                now = _clock()
            if not await self._send(pan, tilt, now, "calibrate", force=True):
                return False, "not_sent", now
            seq = self.seq
            deadline = now + ACK_WAIT_S
            while seq not in self.answers and _clock() < deadline:
                await _sleep(0.05)
            accepted, reason, ms = self.answers.get(seq, (True, None, None))
            if accepted:
                move_s = UNKNOWN_MOVE_S if ms is None else ms / 1000.0
                return True, None, now + move_s + CAMERA_LAG_S
            if reason not in CAL_RETRY_REASONS or _clock() >= give_up:
                return False, reason or "refused", now
            await _sleep(0.5)

    async def _calibrate(self) -> None:
        plan = (("centre", 0, 0), ("left", CAL_PAN, 0), ("right", -CAL_PAN, 0),
                ("down", 0, CAL_TILT), ("back", 0, 0))
        frames: dict = {}
        steps: list = []
        moved = False
        logger.info("head calibration: sweeping (no %s yet)", CAL_PATH.name)
        for name, pan, tilt in plan:
            ok, reason, due = await self._look_and_wait(pan, tilt)
            if not ok:
                self._calibration_failed(reason or "refused", measured=False)
                return
            moved = moved or (pan, tilt) != (0, 0)
            sent = self.sent_at
            answer = self.answers.get(self.seq)
            ref = frames[next(reversed(frames))] if frames else None
            frame, trace, chosen_s = await _frame_after_traced(due + CAL_SETTLE_EXTRA_S, sent, ref)
            steps.append({"name": name, "pan": pan, "tilt": tilt,
                          "ack_ms": None if answer is None else answer[2],
                          "due_s": round(due - sent, 2), "chosen_s": chosen_s, "trace": trace})
            if frame is None:
                if moved:
                    await self._look_and_wait(0, 0)
                _record_calibration({"ok": False, "reason": "no_camera"}, None, None)
                self._calibration_failed("no_camera", measured=moved)
                return
            frames[name] = frame
        try:
            result = measure(frames)
        except Exception:  # noqa: BLE001 -- a sweep that cannot be read is a failed sweep
            logger.exception("head calibration: measuring failed")
            result = {"ok": False, "reason": "error"}
        height, width = frames["centre"].shape[:2]
        _record_calibration(result, width, height)
        _save_sweep(frames, steps, result)
        if not result.get("ok"):
            self._calibration_failed(result.get("reason") or "error", measured=True)
            return
        from app import facewatch

        doc = {k: result[k] for k in ("pan_deg_per_100", "tilt_deg_per_100", "pan_pct", "tilt_pct",
                                      "shifts", "texture")}
        doc.update(measured_at=_dt.datetime.now().isoformat(timespec="seconds"),
                   frame=[width, height], hfov_assumed=facewatch.ROBOT_CAMERA_HFOV_DEG)
        try:
            CAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            CAL_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            logger.warning("head calibration not saved -- used for this process only", exc_info=True)
        _CAL.update(loaded=True, value=doc, failed=None)
        logger.info("head calibration: pan %+.1f deg / tilt %+.1f deg at 100%%",
                    doc["pan_deg_per_100"], doc["tilt_deg_per_100"])
        self.pan, self.tilt = self.confirmed = (0, 0)
        _set_head(0, 0)

    def _calibration_failed(self, reason: str, *, measured: bool) -> None:
        """`measured`: the head swept and the pictures said no (no_motion,
        no_texture, ...) -- not repeated until the server restarts, because
        each try is the head sweeping in front of somebody. A refusal (the
        switch, a stop, a walk that began, a gesture that never let go) is a
        circumstance: this call stops, the next one tries again."""
        if measured:
            _CAL["failed"] = reason
        else:
            _record(why="calibrate", reason=reason, pan=self.pan, tilt=self.tilt)
        self._stop("calibration:" + reason)


def _record_calibration(result: dict, width, height) -> None:
    fields = {k: result.get(k) for k in ("ok", "reason", "pan_deg_per_100", "tilt_deg_per_100",
                                         "texture", "shifts")}
    fields["frame"] = None if width is None else [width, height]
    try:
        from app import turnlog

        turnlog.record("head_calibration", **fields)
    except Exception:  # noqa: BLE001
        logger.debug("head_calibration row not written", exc_info=True)


def _record_lidar(fit: dict) -> None:
    try:
        from app import turnlog

        turnlog.record("head_lidar_check", **{k: fit.get(k) for k in (
            "n", "kept", "a", "b", "mad_deg", "spread_deg", "hfov_deg", "head_ratio", "trusted", "why")})
    except Exception:  # noqa: BLE001
        logger.debug("head_lidar_check row not written", exc_info=True)


def _record_lock(**fields) -> None:
    """`head_lock` rows (HEAD_FOLLOW_FIRST): a lock taken (state on: attention's
    source, bearing, faces in the frame) or ended (state off: why, seconds
    followed, faces taken again by their spot, times attention pointed at
    somebody else meanwhile). Numbers only -- no name, no picture."""
    try:
        from app import turnlog

        turnlog.record("head_lock", **fields)
    except Exception:  # noqa: BLE001
        logger.debug("head_lock row not written", exc_info=True)


def _record(**fields) -> None:
    """`head_look` rows: what the bench reads the loop from (the bearing
    before a look against the percent sent, refusals and why). No picture,
    no words."""
    try:
        from app import turnlog

        turnlog.record("head_look", **fields)
    except Exception:  # noqa: BLE001
        logger.debug("head_look row not written", exc_info=True)
