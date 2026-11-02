"""The robot's real head turns toward the person she is talking with (HEAD_FOLLOW).

Owner, 31 Oct 2026: "เวลาคุยอยากให้หุ่นลองหมุนหัวเพื่อหันหน้าไปคุยกับคน เช่น
ได้ยินเสียงคนมาจากทางซ้ายใกล้ๆ ก็ให้หุ่นหันทักแล้วก้มหน้าหรือขึ้นลง เพื่อจับหน้าคน".

What it follows is what the face page's mascot already follows
(FOCUS_ATTENTION): `focus.attention()` -- the face whose mouth moves, else the
face the last segment was attributed to, else the nearest engaged face. The
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
lidar steers nothing: nobody has confirmed which way its angles run.

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
pictures look ahead; it refuses any other).

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
#: From the servo stopping to a frame of it reaching the server (the page's
#: ~6 fps JPEG stream).
CAMERA_LAG_S = 0.4
#: The app's own gap between two looks is 0.4 s (HeadLook.MIN_GAP_MS): even a
#: forced look waits this long after the last one, or it is refused too_soon.
FORCE_GAP_S = 0.5
#: Smaller changes (percent) are not sent: a servo hunting around a face that
#: breathes is noise on the board and in the picture.
MIN_STEP = 4
PAN_DEADBAND_DEG = 5.0
TILT_DEADBAND_DEG = 7.0
GAIN = 0.6
#: No target this long -> back to centre (the app also centres on its own).
IDLE_S = 6.0
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

# ---- the sweep (HEAD_CALIBRATE) ----
CAL_PAN = 30
CAL_TILT = 25
#: Downscaled width the pictures are compared at (1024 -> 320: ~3x faster and
#: still ~0.2 deg a pixel at a 70 deg HFOV).
CAL_WIDTH = 320
#: Phase correlation's peak below this = the two pictures share nothing.
CAL_MIN_RESPONSE = 0.05
#: Each side of the pan must slide at least this far (a head that did not
#: move, or a camera that does not turn with it).
CAL_MIN_DEG = 0.8
#: |degrees at 100 %| outside this is a measurement gone wrong, not a servo.
CAL_FULL_RANGE = (3.0, 150.0)
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

_REASON = re.compile(r"^[a-z_]{1,32}$")

#: Where the head was last told to look, in degrees (+pan = robot's left,
#: +tilt = down), for facewatch's same-spot dedupe: a box remembered with the
#: head turned is somewhere else in the frame once it turned back. One robot,
#: one head -- module state.
_HEAD = {"pan_deg": 0.0, "tilt_deg": 0.0, "moved_at": None}
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
#: Pairs for the check: one face in the picture, one person on the lidar
#: within this, nobody else.
LIDAR_PAIR_M = 2.5
LIDAR_SOLVE_EVERY = 5


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


def reset() -> None:
    _HEAD.update(pan_deg=0.0, tilt_deg=0.0, moved_at=None)
    _CAL.update(loaded=False, value=None, failed=None)
    _LIDAR.update(fit=None, saved=None)


def lidar_fit() -> dict | None:
    """The lidar <-> camera line when it is good enough to steer by: this
    process's latest solve, else the one saved with the sweep."""
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


def _set_head(pan: int, tilt: int) -> None:
    pan_full, tilt_full = full_deg()
    pan_deg, tilt_deg = pan / 100.0 * pan_full, tilt / 100.0 * tilt_full
    if (pan_deg, tilt_deg) != (_HEAD["pan_deg"], _HEAD["tilt_deg"]):
        _HEAD["moved_at"] = _clock()
    _HEAD["pan_deg"] = pan_deg
    _HEAD["tilt_deg"] = tilt_deg


def _walking(session) -> bool:
    try:
        from app import event_walk

        if event_walk.walking():
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from app import robot_face_link

        return robot_face_link.nav_activity(session) in ("active", "stale")
    except Exception:  # noqa: BLE001
        return False


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


def _fresh(track, after: float) -> bool:
    """The attention's face was seen in a frame taken after `after`."""
    if track is None:
        return False
    from app import facewatch

    return any(tid == track and t >= after for tid, _b, _k, t in facewatch.latest_tracks())


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


async def _frame_after(t: float):
    """The head camera's first picture taken at or after `t` (decoded), or
    None after CAL_FRAME_WAIT_S more."""
    from app import robot_camera

    deadline = t + CAL_FRAME_WAIT_S
    while True:
        seq, jpeg, at = robot_camera.feed.latest()
        if jpeg and at and at >= t:
            frame = robot_camera.feed.decoded_for(seq, jpeg)
            if frame is not None:
                return frame
        if _clock() > deadline:
            return None
        await _sleep(0.1)


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
            return
        # refused: the head is where the last accepted look left it
        self.pan, self.tilt = self.confirmed
        _set_head(self.pan, self.tilt)
        self.settle_at = pending["at"]
        self.last_step = None
        if pending["why"] == "walk":
            self.walk_centred = False
        now = _clock()
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

        if _walking(self.session):
            if not self.walk_centred and await self._send(0, 0, now, "walk", force=True):
                self.walk_centred = True
            return
        self.walk_centred = False
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
        cue = focus.attention(now)
        doa_cue = None
        if cue.get("source") == "doa":
            # Not a face to centre: the loop below corrects a face's error in
            # the camera, and a DOA bearing is no such error to re-measure.
            # With HEAD_FOLLOW_DOA it may aim the one-shot search instead.
            doa_cue = cue if settings.head_follow_doa else None
            cue = {"bearing": None, "source": None, "track": None}
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
        if self._search_due(now):
            _pan, down, _up = _limits()
            want, why = self._doa_look(doa_cue), "doa"
            if want is None:
                want, why = self._lidar_look(now), "lidar"
            if want is None:
                want, why = (self.pan, max(self.tilt, min(down, int(settings.head_search_tilt)))), "search"
            if want == (self.pan, self.tilt) or await self._send(*want, now, why, force=True):
                self.searched_at = now
                self.hold_until = now + SEARCH_HOLD_S
            return
        if (self.pan, self.tilt) != (0, 0) and now > max(self.target_at + IDLE_S, self.hold_until):
            await self._send(0, 0, now, "idle", force=True)
            return
        await self._hold(now)

    async def _hold(self, now: float) -> None:
        """Re-send a pose held off centre before the app's 10 s centring."""
        if (self.pan, self.tilt) != (0, 0) and now - self.sent_at >= HOLD_RESEND_S:
            await self._send(self.pan, self.tilt, now, "hold", force=True)

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
        person on the lidar -- only once the lidar's check against the
        camera holds (a lidar never compared with the camera could have its
        left and right the other way round)."""
        if not settings.lidar_people:
            return None
        fit = lidar_fit()
        if fit is None:
            return None
        from app import lidar_people

        near = [p for p in lidar_people.people(now) if p.distance <= LIDAR_TURN_M]
        if not near:
            return None
        person = near[0]
        cam = lidar_people.camera_from_lidar(fit, person.bearing)
        if cam is None:
            return None
        pan_max, down, _up = _limits()
        pan_full, _tilt_full = full_deg()
        pan = _clamp(cam / _per_pct(pan_full), -pan_max, pan_max)
        tilt = self.tilt
        if person.distance <= LIDAR_CLOSE_M:
            tilt = max(self.tilt, min(down, int(settings.head_search_tilt)))
        return pan, tilt

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

    def _search_due(self, now: float) -> bool:
        if int(settings.head_search_tilt) <= 0 or now - self.searched_at < SEARCH_EVERY_S:
            return False
        if now - self.target_at < SEARCH_AFTER_S:
            return False
        from app import focus

        at, rms = focus.last_voice()
        return (at is not None and 0 <= now - at <= SEARCH_VOICE_AGE_S
                and rms is not None and rms >= float(settings.head_search_rms))

    async def _send(self, pan: int, tilt: int, now: float, why: str, *, force: bool = False,
                    bearing: float | None = None, elev: float | None = None, track=None) -> bool:
        if (pan, tilt) == (self.pan, self.tilt) and why not in ("walk", "hold", "calibrate"):
            return False
        if not force and max(abs(pan - self.pan), abs(tilt - self.tilt)) < MIN_STEP:
            return False
        if now - self.sent_at < (FORCE_GAP_S if force else SEND_GAP_S):
            return False
        from app import robot_face_link

        self.seq += 1
        if not await robot_face_link.look(self.session, pan, tilt, seq=self.seq):
            return False
        pan_full, _tilt_full = full_deg()
        if bearing is not None and why not in ("hold",):
            self.last_step = {"track": track, "bearing": float(bearing),
                              "turn_deg": (pan - self.pan) / 100.0 * pan_full}
        self.pending = {"seq": self.seq, "pan": pan, "tilt": tilt, "at": now, "why": why}
        moved = (pan, tilt) != (self.pan, self.tilt) or why != "hold"
        self.pan, self.tilt, self.sent_at = pan, tilt, now
        if moved:
            self.settle_at = now + SETTLE_S
        _set_head(pan, tilt)
        if why != "hold" and (why != self.logged_why or now - self.logged_at >= LOG_EVERY_S):
            self.logged_at, self.logged_why = now, why
            _record(why=why, pan=pan, tilt=tilt,
                    bearing=None if bearing is None else round(float(bearing), 1),
                    elev=None if elev is None else round(float(elev), 1))
        return True

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
        moved = False
        logger.info("head calibration: sweeping (no %s yet)", CAL_PATH.name)
        for name, pan, tilt in plan:
            ok, reason, due = await self._look_and_wait(pan, tilt)
            if not ok:
                self._calibration_failed(reason or "refused", measured=False)
                return
            moved = moved or (pan, tilt) != (0, 0)
            frame = await _frame_after(due + CAL_SETTLE_EXTRA_S)
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


def _record(**fields) -> None:
    """`head_look` rows: what the bench reads the loop from (the bearing
    before a look against the percent sent, refusals and why). No picture,
    no words."""
    try:
        from app import turnlog

        turnlog.record("head_look", **fields)
    except Exception:  # noqa: BLE001
        logger.debug("head_look row not written", exc_info=True)
