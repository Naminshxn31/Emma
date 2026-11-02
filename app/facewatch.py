"""
Deciding whether to say a name out loud — the layer between a camera frame
and the robot opening its mouth.

`app/faces.py` answers "who is nearest, and how near". That is a number.
This module turns numbers into one of three things, and refusing is the
common case:

    known      somebody enrolled, confidently, standing close enough
    stranger   a person is here, nobody we know
    nothing    no face worth acting on

Everything below exists because a greeting is irreversible. The robot
saying "สวัสดีคุณต้า" to somebody who is not ต้า cannot be taken back by a
later frame, and the person it happened to is standing right there. So the
defaults are all in the direction of staying quiet.

Why a decision needs more than one frame
----------------------------------------
A single frame is a bad witness: motion blur, a head turning, someone
walking behind the subject. `CONFIRM_FRAMES` makes the same answer arrive
several times in a row before it counts. This is the same shape as
`min_silence_duration` in the VAD — a detector that fires on one sample
fires on noise.

Why a decision has to expire
----------------------------
Somebody who stands at the desk for ten minutes is one arrival, not six
hundred. The cooldown is per person, and there is a separate one for
strangers, because otherwise a busy Saturday is a robot greeting the room
once a second.

Why distance is a threshold too
-------------------------------
`MIN_FACE_PX` is not about accuracy. A face 40 pixels wide is somebody
across the lobby who has not come to talk to us, and greeting them by name
from six metres away is worse than saying nothing — it is also the moment
the recognition is least reliable, so the two reasons point the same way.

What this module deliberately does not do
-----------------------------------------
It does not talk to the model, hold a socket, or know what a greeting says.
It returns a decision. `events.announce` is the one road to the model and
that stays true here (see its docstring — a camera was the next caller it
was written for).
"""
from __future__ import annotations

import itertools
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app import faces
from app.config import settings

logger = logging.getLogger("condo_voice.facewatch")

#: A frame counts as "the greeted person is still here" from this score up,
#: even though it takes FACE_THRESHOLD to *say* the name. Two different
#: errors with two different prices: greeting the wrong person is a voice
#: that cannot be taken back, so naming demands the full threshold and
#: three consecutive frames — but mistakenly believing somebody stayed only
#: keeps the robot quiet, which is the cheap direction.
#:
#: 0.42 for the auraface pack: derived (threshold 0.50 minus the dip the
#: doorway walk showed on the old pack), NOT yet measured — the buffalo
#: numbers (dips 0.40-0.43, impostors <=0.342, floor 0.38) died with the
#: pack switch. Worst case of it being wrong is a quieter robot; walk the
#: doorway with check-face-range.cmd and set it from the printed scores.
SOFT_PRESENCE = 0.42

#: The floor of the "probably somebody we know" band (FACE_PROBABLE_WAIT_S):
#: a consented person's best match at or above this, under the naming bar,
#: holds stranger frames instead of counting them. Measured, not derived:
#:
#: - colleagues walking in scored 0.33-0.38 on the way (doorway, 31 Aug —
#:   see the stranger->known dedupe in `_see`); real strangers 0.1-0.3 in
#:   the turn logs since 29 Sep.
#: - synthetic (scripts/eval_face_distance.py, 9 Oct: the scan crops of 7
#:   enrolled people in a 1024x768 q70 frame, 40-110 px): 97-100% of the
#:   genuine frames score >= 0.33, and 28-37% of leave-one-person-out
#:   impostors do (the same capture rig and a 38-vector gallery — an upper
#:   bound; studio strangers against 305 brokers: 63% >= 0.33, p50 0.349).
#:
#: An impostor in the band costs a delay (at most FACE_PROBABLE_WAIT_S) and
#: never a name — naming still needs FACE_THRESHOLD, the margin, one face
#: and consent — so the floor sits low, on the colleagues' side.
PROBABLE_LOW = 0.33
#: A probable arrival with no face in frame for this long has walked away.
PROBABLE_GAP_S = 2.0
#: A crop answer (FACE_ZOOM) counts for the frame's face only if the face it
#: holds, mapped back into the frame, overlaps the frame's box this much.
CROP_IOU = 0.3


@dataclass
class Sighting:
    """Somebody the robot has decided is worth speaking to."""

    kind: str  #: "known" or "stranger"
    name: str | None
    score: float
    meta: dict = field(default_factory=dict)
    #: With FACE_TRACK_WAIT_S on: seconds from the face's arrival to this
    #: decision, and how many frames were held back as unclear on the way.
    #: The turn log carries both, so the wait is tuned from the doorway
    #: and not from the synthetic blur it was first set on.
    track_s: float | None = None
    held: int = 0
    #: Usable faces (the company bar of `_why_not_name`) in the frame that
    #: decided. 0 = not counted, which the handshake reads as "not alone":
    #: a sighting nobody counted must not reach out to two people.
    faces_in_frame: int = 0
    #: With FACE_PROBABLE_WAIT_S on: how long a "probably known" hold ran
    #: before this decision (None = there was none).
    probable_s: float | None = None

    @property
    def group(self) -> str:
        return self.meta.get("group", "")


def _overlap(a, b) -> float:
    """Intersection over union of two (x1, y1, x2, y2) boxes; 0 when either is missing."""
    if not a or not b:
        return 0.0
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _head_now() -> tuple[float, float]:
    """Where the robot's head was last told to look, degrees (app/head_follow.py);
    (0, 0) with HEAD_FOLLOW off -- the frame never moved."""
    try:
        from app import head_follow

        return head_follow.offset_deg()
    except Exception:  # noqa: BLE001 -- a probe, never a reason to greet twice
        return 0.0, 0.0


def _in_head_frame(bbox, head_then, frame) -> tuple | None:
    """`bbox`, remembered with the head at `head_then`, moved to where that
    same spot is in a frame taken with the head where it is now.

    The same-spot dedupes compare a box from a greeting with a box seconds
    later. With the camera on a head that turns toward the person
    (HEAD_FOLLOW), the person ends up in the middle of the frame and their
    own box no longer overlaps the one they were greeted in -- the 2 Sep
    "blur greeted twice" bug, caused by the head instead of the blur. A
    turn of d degrees slides the scene by ~focal * tan(d) pixels (+pan =
    left: the scene moves right; +tilt = down: it moves up)."""
    if not bbox or head_then is None:
        return bbox
    now = _head_now()
    width = int(frame.shape[1]) if getattr(frame, "ndim", 0) >= 2 else 0
    if not width or tuple(now) == tuple(head_then):
        return bbox
    focal = (width / 2.0) / math.tan(math.radians(ROBOT_CAMERA_HFOV_DEG / 2.0))
    dx = focal * math.tan(math.radians(now[0] - head_then[0]))
    dy = -focal * math.tan(math.radians(now[1] - head_then[1]))
    x1, y1, x2, y2 = bbox
    return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)


def _yaw(kps) -> float | None:
    """How far the head is turned, from SCRFD's five points; None without them."""
    if kps is None:
        return None
    k = np.asarray(kps, dtype=np.float32)
    if k.shape[0] < 3:
        return None
    eyes = float(np.linalg.norm(k[1] - k[0]))
    if eyes <= 1e-6:
        return None
    return float((k[2][0] - (k[0][0] + k[1][0]) / 2) / eyes)


#: Horizontal field of view of the robot's camera, for face bearings
#: (listening focus). ASSUMED, not measured: the frames are 1024x768 (server
#: log "robot camera: first frame 1024x768") and nothing in this repo records
#: the lens — Android exposes only zoom 1..4x, FOV fixed (CHANGELOG). 70 deg
#: is a typical fixed-focus module. An error here scales every bearing by
#: the same factor, so a face at the image edge reads ~+/-35 deg when it may
#: be 30 or 40; the DOA side calibrates against these bearings, which
#: absorbs it. Measure: a person 2 m away stepping sideways until the box
#: centre touches the frame edge — the angle of that offset is half the FOV.
ROBOT_CAMERA_HFOV_DEG = 70.0
#: Depth of the nose tip in front of the eyes, in inter-eye distances — the
#: conversion from `_yaw`'s nose offset (weak perspective: offset/eye gap =
#: depth * tan(yaw)) to degrees. ~30 mm over ~63 mm; derived, not measured.
#: With it, Watcher.YAW_MAX 0.5 is 45 deg and FOCUS_YAW_MAX_DEG 35 is 0.35.
YAW_NOSE_DEPTH = 0.5


def bearing_deg(cx: float, frame_width: int, hfov_deg: float = ROBOT_CAMERA_HFOV_DEG) -> float:
    """Horizontal angle of image column `cx` from the camera axis, + = the
    robot's left (the camera looks where the robot looks, so image left is
    the robot's left)."""
    half = frame_width / 2.0
    focal = half / math.tan(math.radians(hfov_deg / 2.0))
    return math.degrees(math.atan2(half - cx, focal))


def elevation_deg(cy: float, frame_width: int, frame_height: int,
                  hfov_deg: float = ROBOT_CAMERA_HFOV_DEG) -> float:
    """Vertical angle of image row `cy` from the camera axis, + = above it
    (square pixels: the focal length comes from the width's HFOV). Assumed
    the way `bearing_deg` is -- an HFOV error scales both by one factor."""
    focal = (frame_width / 2.0) / math.tan(math.radians(hfov_deg / 2.0))
    return math.degrees(math.atan2(frame_height / 2.0 - cy, focal))


def yaw_deg(kps) -> float | None:
    """`_yaw`'s nose offset as degrees, + = head turned toward the robot's
    left; None without landmarks."""
    ratio = _yaw(kps)
    if ratio is None:
        return None
    return -math.degrees(math.atan2(ratio, YAW_NOSE_DEPTH))


def _sharpness(frame, bbox) -> float:
    """Focus of the face region (see Watcher.SHARP_MIN); inf when unmeasurable,
    so a frame the measure cannot read is never the one held back for it."""
    import cv2

    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return float("inf")
    crop = cv2.resize(frame[y1:y2, x1:x2], (64, 64), interpolation=cv2.INTER_AREA)
    if crop.ndim == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(crop, cv2.CV_64F).var())


class Watcher:
    """Frames in, sightings out. One per camera.

    Holds the gallery and the short-term memory that keeps a greeting from
    repeating. Deliberately not a module-level singleton: `MULTI_SESSION`
    exists in this project because module state shared across conversations
    turned into two guests' budgets in one spreadsheet, and "who did we just
    greet" is exactly that kind of state.
    """

    def __init__(self, gallery: faces.Gallery | None = None,
                 threshold: float | None = None,
                 confirm_frames: int | None = None,
                 cooldown_s: float | None = None,
                 min_face_px: int | None = None,
                 margin: float | None = None,
                 name_when_alone: bool | None = None,
                 track_wait_s: float | None = None,
                 probable_wait_s: float | None = None,
                 probable_min_px: int | None = None):
        self.gallery = gallery
        self.threshold = settings.face_threshold if threshold is None else threshold
        self.margin = settings.face_margin if margin is None else margin
        self.name_when_alone = (settings.face_name_when_alone
                                if name_when_alone is None else name_when_alone)
        #: Why the last confirmed face was greeted without its name, by
        #: name, so the log line is written once per person per reason and
        #: not thirty times a second.
        self._unnamed_logged: dict[str, str] = {}
        self.confirm_frames = (settings.face_confirm_frames
                               if confirm_frames is None else confirm_frames)
        self.cooldown_s = settings.face_cooldown_s if cooldown_s is None else cooldown_s
        self.min_face_px = settings.face_min_px if min_face_px is None else min_face_px
        self.track_wait_s = (settings.face_track_wait_s
                             if track_wait_s is None else track_wait_s)
        #: The arrival the current frames belong to: when it started, where
        #: its face was last, and how many frames it has held back as
        #: unclear. Only the wait reads it — the streak keeps its own rules.
        self._track: dict | None = None
        #: "Probably somebody we know" (FACE_PROBABLE_WAIT_S; 0 = off).
        self.probable_wait_s = (settings.face_probable_wait_s
                                if probable_wait_s is None else probable_wait_s)
        self.probable_min_px = (settings.face_probable_min_px
                                if probable_min_px is None else probable_min_px)
        #: The live hold: when it began, when a frame last supported it, and
        #: counts for the turn log. Holds no name — nothing about it is ever
        #: said or shown, and a hold is not a naming decision.
        self._probable: dict | None = None
        #: When the last hold ran out while somebody was still in frame. A
        #: new hold needs a frame with nobody in it first, or a person who
        #: never comes closer would be held for ever, one wait at a time.
        self._probable_spent_at: float | None = None
        #: Decisions for the turn log, drained by the greeter
        #: (`drain_events`). Numbers and reasons only, never a name.
        self.events: list[dict] = []
        #: The nearest face when a closer look would help (FACE_ZOOM_ENABLED,
        #: FACE_TURN_TO_CHECK): box, frame size, bearing, width, and whether
        #: it is a probable far face. Set per frame; None when nothing to do.
        self.look_closer: dict | None = None
        #: Frames before this (monotonic) are taken while the base turns
        #: toward a face: motion blur, and a box that jumps across the frame.
        #: They decide nothing (`hold_still`).
        self.still_until = 0.0

        self._streak_key: str | None = None
        self._streak = 0
        self._last_greeted: dict[str, float] = {}
        #: When somebody the robot might actually greet was last in frame —
        #: set before the confirmation completes, so the greeter can start
        #: dialling while the decision is still being made (confirming
        #: takes ~1.2s and the Gemini dial 2-3s; in sequence the robot
        #: greeted people's backs). "Might actually greet" is the important
        #: half: the first version fired on *any* face big enough, and the
        #: owner sitting at the desk — greeted an hour ago, deep in cooldown
        #: — opened a fresh Gemini session every fifteen seconds for nobody.
        #: The exact quota leak the wake word exists to prevent. So this is
        #: a candidate that is not suppressed, seen at least twice.
        self.someone_at: float | None = None
        #: The faces of strangers already greeted, so "a stranger" is not
        #: one person. With a single shared cooldown, greeting anybody
        #: unknown silenced every *other* unknown visitor for its whole
        #: length - measured 2026-08-31: a garbage frame scoring 0.062 was
        #: greeted as a stranger at 09:18, another at 09:31, and the real
        #: visitor who walked in minutes later got nothing at all. The
        #: vector is already in hand at that point; remembering it is what
        #: lets a *different* face through while the same face stays quiet.
        self._strangers: list[dict] = []
        #: Where and when the last *named* greeting fired. The mirror of
        #: the same-arrival rule below: a colleague greeted by name whose
        #: following frames blur (score 0.223 against the gallery,
        #: measured 2026-09-02 while โชกุน stood right at the lens) held a
        #: six-frame streak as "a stranger" and was greeted a second time,
        #: anonymously, seconds after hearing their own name.
        self._named: dict | None = None
        #: The last frame's verdict, in words the chest screen's temporary
        #: debug panel can show (owner, 2026-09-30: "อยากรู้ว่ามันจับคนได้ไหม").
        #: Diagnostic only — nothing reads it back into a decision. It exists
        #: for the same reason as WAKE_DEBUG's RMS lines: "ไกลเกิน", "คะแนน
        #: ต่ำ", "ติด cooldown" and "กำลังคุยอยู่" are indistinguishable from
        #: the doorway without a screen that says which one is happening.
        self.status: dict = {"verdict": "ยังไม่มีภาพจากกล้อง"}
        #: Listening focus: every face of the current frame and who the
        #: nearest one was confirmed to be (see `_publish`).
        self._located: list | None = None
        self._confirmed = _UNSET
        self._tracks = _Tracks()

    def _note(self, verdict: str, **extra) -> None:
        self.status = {"verdict": verdict, **extra}

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_settings(cls) -> "Watcher | None":
        """A watcher with the enrolled gallery, or None if there isn't one.

        None is a working state, not an error: a machine with no models and
        no gallery runs the rest of the showroom exactly as before. That is
        the same promise `FACE_ENABLED=false` makes, kept one layer down so
        a half-configured machine cannot half-work.
        """
        if not faces.available():
            logger.info("face models not installed — recognition off")
            return None
        path = Path(settings.face_gallery).expanduser()
        if not path.is_file():
            logger.info("no gallery at %s — recognition off", path)
            return None
        try:
            gallery = faces.Gallery.load(path)
        except ValueError as exc:
            # A gallery built by another model. Loud and off, never ranked.
            logger.warning("%s", exc)
            return None
        logger.info("face gallery: %d enrolled, threshold %.2f",
                    len(gallery), settings.face_threshold)
        return cls(gallery)

    # -- the decision ----------------------------------------------------

    def see(self, frame: np.ndarray, now: float | None = None,
            crop: dict | None = None) -> Sighting | None:
        """One camera frame in; a decision or None.

        Only the nearest face is considered. A queue at the desk is not a
        list of people to greet in turn — it is one person being spoken to
        and others waiting, and the robot addressing the second one is the
        barge-in bug wearing a face.

        With listening focus or active-speaker on, every face located in the
        frame is also published (`_publish`) — whatever the greeting decided,
        including "nobody". Off, the decision is all that happens.

        `crop` (FACE_ZOOM_ENABLED): the face page's closer look, from
        `robot_camera.feed.take_crop()` — used at most once, for this
        frame's nearest face, only if it shows the same face. It replaces
        the frame's *vector*, never its box: the distance rules read the
        frame's own pixels whatever the crop says.
        """
        now = time.monotonic() if now is None else now
        self._located = None
        self._confirmed = _UNSET
        self.look_closer = None
        try:
            return self._see(frame, now, crop)
        finally:
            if settings.focus_enabled or settings.asd_enabled:
                self._publish(frame, now)

    def _see(self, frame: np.ndarray, now: float, crop: dict | None = None) -> Sighting | None:
        # Locate, not detect: the embedding is the expensive half and both
        # tests below can rule a frame out without it. A doorway is empty
        # most of the time, and describing nobody costs the same as
        # describing somebody.
        found = faces.locate(frame)
        self._located = found
        self._probable_check(now, bool(found))
        if not found:
            self._streak_key, self._streak = None, 0
            self._track = None
            self._note("ไม่เห็นหน้า", faces=0)
            return None

        if now < self.still_until:
            # The base is turning toward this face (FACE_TURN_TO_CHECK).
            # Motion blur and a box sliding across the frame are no
            # evidence of anything; the hold is kept alive meanwhile — the
            # person did not leave, the robot moved.
            if self._probable is not None:
                self._probable["seen"] = now
            self._note("หุ่นกำลังหันมอง — ยังไม่ตัดสิน", faces=len(found))
            return None

        face = found[0]
        x1, _, x2, _ = face.bbox
        if (x2 - x1) < self.min_face_px:
            if self._probable_on() and (x2 - x1) >= self.probable_min_px:
                return self._far(frame, face, found, now, crop)
            # Somebody across the room. Not a visitor yet, and the frame
            # where they are smallest is the frame we trust least.
            self._streak_key, self._streak = None, 0
            self._track = None
            self._note("เห็นหน้าแต่ไกลเกิน (%dpx < %dpx)" % (x2 - x1, self.min_face_px),
                       faces=len(found), px=int(x2 - x1))
            return None
        self._follow(face, now)

        if self._describe(frame, face, crop) is None:
            self._note("เห็นหน้าแต่อ่านไม่ได้ (เบลอ/มืดเกิน)",
                       faces=len(found), px=int(x2 - x1))
            return None

        name, score, meta = None, -1.0, {}
        best = None
        unnamed = None
        if self.gallery is not None and len(self.gallery):
            i, score, runner_up = self.gallery.match2(face.vec)
            best = self.gallery.names[i]
            if score >= self.threshold:
                name, meta = best, self.gallery.meta[i]
                unnamed = self._why_not_name(name, score, runner_up, found)
                if unnamed:
                    # Recognised, but not to be named: greeted like a
                    # stranger, with the reason carried on the sighting for
                    # the turn log. The rest of this function treats the
                    # face as unknown — including remembering it, so the
                    # anonymous greeting is not repeated either.
                    if self._unnamed_logged.get(name) != unnamed:
                        logger.info("face: %s recognised (%.3f) but not named — %s",
                                    name, score, unnamed)
                        self._unnamed_logged[name] = unnamed
                    name, meta = None, {"unnamed": unnamed, "would_be": best}

        if name is None and (self._probable_on() or settings.face_zoom_enabled):
            # Under the bar (or a close second) but in the band: probably
            # somebody we know, at a bad moment. Arms or refreshes the hold;
            # what it holds is decided at the stranger gate below. A closer
            # look is worth asking for either way. Both off: not even asked
            # (consent reads people.json — no new read per frame on a pull).
            why = self._probable_evidence(best, score, unnamed)
            if why is not None:
                if self._probable_on():
                    self._probable_support(now, why, x2 - x1, score)
                self._want_closer(frame, face, now)

        # Strangers greeted longer ago than the cooldown are new arrivals
        # the next time they appear, so their memory expires with it.
        self._strangers = [e for e in self._strangers
                           if (now - e["at"]) < self.cooldown_s]

        # An unknown face is compared against the strangers already greeted
        # - the same bar that names a colleague decides "this is the person
        # I greeted five minutes ago". A match is identification, so it is
        # presence (one frame suffices) and it refreshes that stranger's
        # own clock, nobody else's.
        stranger_seen = None
        if name is None:
            for entry in self._strangers:
                if float(np.dot(face.vec, entry["vec"])) >= self.threshold:
                    stranger_seen = entry
                    entry["at"] = now
                    entry["bbox"] = tuple(face.bbox)
                    break

        key = (f"name:{name}" if name
               else f"stranger:{id(stranger_seen)}" if stranger_seen is not None
               else "stranger:new")

        # Could this frame end in a greeting? Decided before any clock is
        # refreshed below, because refreshing is what makes "already greeted"
        # look like "just now".
        if name:
            last = self._last_greeted.get(key)
            greetable = last is None or (now - last) >= self.cooldown_s
        else:
            greetable = stranger_seen is None

        # "Still here" is decided by a *single* frame, while greeting takes
        # several - the two mistakes cost differently. Greeting the wrong
        # person is a voice that cannot be taken back; wrongly believing
        # somebody stayed only keeps the robot quiet. Without this, a person
        # whose score flickers around the threshold at distance broke the
        # confirmation streak often enough that their clock was never
        # refreshed, and the robot re-greeted somebody who had been standing
        # there the whole time. A weak frame of the right face (>=
        # SOFT_PRESENCE, below the naming bar) counts as presence too.
        #
        # Only a clock still inside its cooldown is pushed along: presence
        # extends a suppression, it must never resurrect an expired one -
        # or the first frame of somebody coming back from lunch would reset
        # the very clock that says they were away. And a frame nobody can
        # identify - under SOFT_PRESENCE, matching no remembered stranger -
        # refreshes nothing: it is not evidence that anybody in particular
        # is still here, and treating it as such is how garbage frames kept
        # real visitors silenced.
        if name is None and best is not None and score >= SOFT_PRESENCE:
            stamp = self._last_greeted.get(f"name:{best}")
            if stamp is not None and (now - stamp) < self.cooldown_s:
                self._last_greeted[f"name:{best}"] = now
        elif name is not None:
            stamp = self._last_greeted.get(key)
            if stamp is not None and (now - stamp) < self.cooldown_s:
                self._last_greeted[key] = now

        # Unclear is not unknown. Early in an arrival a frame that would
        # count toward "a stranger" is held back — neither added to the
        # streak nor breaking one — when it is the kind of frame a
        # colleague also produces: blurred, turned, or scoring just under
        # the naming bar. Placed after the presence refresh (a held frame
        # of somebody in cooldown still says they are here) and before the
        # streak, which is the only thing it withholds.
        if key == "stranger:new" and unnamed is None:
            held = self._unclear(frame, face, best, score, now)
            if held:
                self._track["held"] += 1
                why = self._track["held_by"]
                why[held.split(" ")[0]] = why.get(held.split(" ")[0], 0) + 1
                left = self.track_wait_s - (now - self._track["start"])
                self._note("%s — รอภาพชัดอีก %.1f วิ" % (held, max(0.0, left)),
                           px=int(x2 - x1), score=round(float(score), 3), held=True)
                if self._probable_live(now):
                    self._probable["seen"] = now   # unclear, not gone
                self._want_closer(frame, face, now)
                return None
        if key == "stranger:new" and self._probable_live(now):
            # Probably somebody we know (FACE_PROBABLE_WAIT_S): a frame that
            # could be theirs at a bad moment does not count toward "a
            # stranger" while the hold lasts. A clear, close, frontal frame
            # scoring under the band still counts — that is a stranger
            # after all, and they are greeted as fast as before.
            held = self._probable_hold(frame, face, score, unnamed)
            if held:
                p = self._probable
                # A frame that could be theirs keeps the hold alive: blurred
                # is not gone. The wait still bounds it.
                p["seen"] = now
                p["held"] += 1
                p["why"][held] = p["why"].get(held, 0) + 1
                left = self.probable_wait_s - (now - p["start"])
                # No name, no "who": the chest screen shows this to the room.
                self._note("อาจเป็นคนที่รู้จัก (%s) — รอดูให้ชัดอีก %.1f วิ"
                           % (_HOLD_TH.get(held, held), max(0.0, left)),
                           px=int(x2 - x1), score=round(float(score), 3), held=True,
                           probable=True)
                self._want_closer(frame, face, now)
                return None

        if key == self._streak_key:
            self._streak += 1
        else:
            self._streak_key, self._streak = key, 1
        # A stranger has to hold the frame twice as long as a colleague.
        # The gallery can vouch for a colleague; for a stranger the only
        # witness is persistence - and the 0.062-scoring garbage frame that
        # was greeted this morning would not have survived six frames.
        needed = self.confirm_frames if name else self.confirm_frames * 2
        if self._streak >= needed:
            # Who the nearest face is, as far as naming goes — confirmed by
            # the same streak a greeting needs, and already through margin,
            # company and consent (`name` is None when any of them refused).
            # Listening focus carries it on the face's track.
            self._confirmed = name
        # The pre-ring fires one frame before confirmation, not at two:
        # for a colleague that is the same thing (needed 3), for a
        # stranger it is frame 5 of 6. Ringing strangers at two frames
        # dialled Gemini for a colleague in cooldown whose score dipped
        # under the bar for two frames (2026-09-01 15:xx) — a session
        # nobody greeted and nobody spoke to, held open for two minutes.
        if greetable and self._streak >= max(2, needed - 1):
            self.someone_at = now
        if self._streak < needed:
            who = name if name else "คนแปลกหน้า"
            shown = "%.2f" % score if score >= 0 else "-"
            self._note("เห็น%s (%s) — ยืนยัน %d/%d" % (who, shown, self._streak, needed),
                       px=int(x2 - x1), score=round(float(score), 3),
                       **({"name": name} if name else {}))
            return None

        if name:
            # The clock restarts on every confirmed sighting, not only on
            # the greeting. Counting from the greeting re-greeted somebody
            # who had simply stayed - measured on the first working run,
            # the same person twice in one conversation. A new arrival is
            # someone who was *away* for cooldown_s.
            last = self._last_greeted.get(key)
            self._last_greeted[key] = now
            if last is not None and (now - last) < self.cooldown_s:
                self._note("%s ทักไปแล้ว — ทักใหม่ได้ถ้าหายไปเกิน %d วิ" % (
                    name, int(self.cooldown_s)), name=name,
                    score=round(float(score), 3))
                return None
            # A colleague who was just greeted as a stranger has been
            # greeted. Walking in, the first confirmable thing about a
            # person is that they are a person — approach frames score
            # under the naming bar (measured: 0.33-0.38 on the way in,
            # 0.57 only at the desk) — so the anonymous greeting fires
            # first, and following it seconds later with a named one is
            # the robot interrupting its own conversation to add a name.
            # The remembered stranger face answers "was that already you".
            for entry in list(self._strangers):
                if float(np.dot(face.vec, entry["vec"])) >= self.threshold:
                    self._strangers.remove(entry)
                    self._note("%s คือคนที่เพิ่งทักแบบไม่มีชื่อ — ไม่ทักซ้ำ" % name,
                               name=name)
                    return None
            # The vector dedupe above cannot see the other half of this:
            # the approach frames of a colleague (blurred, 0.10-0.32
            # against the gallery) greeted anonymously, and the same
            # person named nine seconds later once they stood still
            # (2026-09-01 15:48). A garbage frame's vector matches
            # nobody, its owner included — but its *box* is where that
            # person was standing. A colleague confirmed soon after an
            # anonymous greeting, in the same spot, is that arrival;
            # somebody walking in behind them lands in a different box.
            for entry in list(self._strangers):
                recent = (now - entry.get("greeted_at", entry["at"])) < self.STRANGER_TO_NAME_S
                if recent and _overlap(face.bbox, _in_head_frame(entry.get("bbox"), entry.get("head"),
                                                                 frame)) >= self.SAME_SPOT_IOU:
                    logger.info("face: %s confirmed %.0fs after an anonymous greeting in "
                                "the same spot — same arrival, not greeting twice",
                                name, now - entry.get("greeted_at", entry["at"]))
                    self._strangers.remove(entry)
                    self._note("%s ยืนจุดที่เพิ่งทักแบบไม่มีชื่อ — ไม่ทักซ้ำ" % name,
                               name=name)
                    return None
            self._named = {"at": now, "bbox": tuple(face.bbox), "head": _head_now()}
            self._note("ทัก %s (%.2f)" % (name, score), name=name,
                       score=round(float(score), 3), greeting=True)
            return Sighting(kind="known", name=name, score=float(score),
                            meta=dict(meta), faces_in_frame=self._usable(found),
                            probable_s=self._probable_end(now, "named", x2 - x1, score),
                            **self._track_stats(now))

        if stranger_seen is not None:
            self._note("คนแปลกหน้าคนเดิม (ทักแล้ว) ยังอยู่ — ไม่ทักซ้ำ")
            return None                    # greeted already; clock refreshed

        # A stranger has to be closer than a colleague before the robot
        # speaks. Measured on the doorway setup (31 Aug): standing at the
        # door a face is 59-68px and a colleague scores 0.64-0.78; walking
        # in it is 41-51px, blurred, scoring 0.10-0.32 against the gallery
        # - and six of those frames passed as "a stranger" while the
        # colleague was still on their way to being recognised.
        if (x2 - x1) < self.min_face_px * self.STRANGER_PX_FACTOR:
            self._note("คนแปลกหน้าต้องใกล้กว่านี้ (%dpx < %dpx)" % (
                x2 - x1, int(self.min_face_px * self.STRANGER_PX_FACTOR)),
                px=int(x2 - x1))
            return None

        # The mirror of the stranger→known dedupe above: a colleague was
        # just greeted by name, and now an unmatchable face is confirmed
        # in the same spot. That is their own blur (0.223 at the lens,
        # 2026-09-02), not a new visitor — and it is remembered as a
        # greeted stranger so the swallow outlives the 20s window; without
        # that, the same blur re-confirms and greets anonymously the
        # moment the window closes, while the person is still standing
        # there. Somebody arriving behind them lands in a different box
        # and is still greeted.
        if (self._named is not None
                and (now - self._named["at"]) < self.STRANGER_TO_NAME_S
                and _overlap(face.bbox, _in_head_frame(self._named["bbox"], self._named.get("head"),
                                                       frame)) >= self.SAME_SPOT_IOU):
            logger.info("face: a stranger confirmed %.0fs after a named greeting in "
                        "the same spot — their own blur, not greeting twice",
                        now - self._named["at"])
            self._strangers.append({"vec": np.array(face.vec, copy=True), "at": now,
                                    "greeted_at": now, "bbox": tuple(face.bbox), "head": _head_now()})
            del self._strangers[:-16]
            self._note("ภาพเบลอของคนที่เพิ่งทักชื่อ — ไม่ทักซ้ำ")
            return None

        self._strangers.append({"vec": np.array(face.vec, copy=True), "at": now,
                                "greeted_at": now, "bbox": tuple(face.bbox), "head": _head_now()})
        # A remembered face costs 2KB; forgetting the oldest is cheaper
        # than comparing against a whole Saturday.
        del self._strangers[:-16]
        self._note("ทักคนแปลกหน้า" + (" (%.2f)" % score if score >= 0 else ""),
                   greeting=True)
        return Sighting(kind="stranger", name=None, score=float(score),
                        meta=dict(meta), faces_in_frame=self._usable(found),
                        probable_s=self._probable_end(now, "stranger", x2 - x1, score),
                        **self._track_stats(now))

    # -- the arrival wait (FACE_TRACK_WAIT_S) -----------------------------

    def _follow(self, face, now: float) -> None:
        """Keep `_track` on the arrival this face belongs to.

        The same arrival is the same box, moved a little, a moment later. A
        gap or a jump starts a new one — and with it a new wait, which is
        right: the wait belongs to somebody who has just walked up.
        """
        t = self._track
        if (t is None or (now - t["at"]) > self.TRACK_GAP_S
                or _overlap(face.bbox, t["bbox"]) < self.TRACK_IOU):
            self._track = {"start": now, "at": now, "bbox": tuple(face.bbox),
                           "held": 0, "held_by": {}}
        else:
            t["at"], t["bbox"] = now, tuple(face.bbox)

    def _unclear(self, frame, face, best, score: float, now: float) -> str | None:
        """Why this frame cannot count as a stranger yet, or None if it can.

        Only inside the wait: past it — or with the wait off — every frame
        counts, so the worst a wrong reading here costs is a stranger
        greeted `track_wait_s` later. That bound is what makes thresholds
        measured on synthetic blur safe to ship before the doorway has
        confirmed them.
        """
        t = self._track
        if self.track_wait_s <= 0 or t is None or (now - t["start"]) >= self.track_wait_s:
            return None
        if best is not None and score >= SOFT_PRESENCE:
            # Just under the bar is where a colleague at a bad angle lands;
            # a real stranger scores 0.1-0.3 (turn logs since 2026-09-29).
            # No name on this line: the chest screen shows it to the room.
            return "คล้ายคนที่รู้จัก (%.2f)" % score
        yaw = _yaw(face.kps)
        if yaw is not None and abs(yaw) > self.YAW_MAX:
            return "หน้าหันข้าง"
        if _sharpness(frame, face.bbox) < self.SHARP_MIN:
            return "ภาพเบลอ"
        return None

    def _track_stats(self, now: float) -> dict:
        if self.track_wait_s <= 0 or self._track is None:
            return {}
        t = self._track
        # Numbers and reasons only: this line is how SHARP_MIN/YAW_MAX get
        # re-set from the real doorway, and it needs no name to do that.
        logger.info("face: decided %.1fs into the arrival — held %d frame(s) %s",
                    now - t["start"], t["held"], t["held_by"] or "")
        return {"track_s": round(now - t["start"], 2), "held": t["held"]}

    # -- probably somebody we know (FACE_PROBABLE_WAIT_S) -------------------

    def _probable_on(self) -> bool:
        return self.probable_wait_s > 0 and self.gallery is not None and len(self.gallery) > 0

    def _probable_live(self, now: float) -> bool:
        p = self._probable
        return (p is not None and (now - p["start"]) < self.probable_wait_s
                and (now - p["seen"]) < PROBABLE_GAP_S)

    def _probable_check(self, now: float, anybody: bool) -> None:
        """End a hold that ran out (`expired`) or lost its person (`lost`).

        Runs first on every frame, before anything can count. An expiry
        while somebody is still in frame marks the arrival as spent: the
        next hold needs a frame with nobody in it, or a person who never
        comes closer would be held for ever, one wait at a time — the
        "a bound that resets itself is not a bound" lesson (ตัวกระตุ้นทัวร์).
        """
        if self._probable_spent_at is not None:
            if anybody:
                self._probable_spent_at = now
            elif (now - self._probable_spent_at) >= PROBABLE_GAP_S:
                self._probable_spent_at = None
        p = self._probable
        if p is None or self._probable_live(now):
            return
        expired = (now - p["start"]) >= self.probable_wait_s
        self._probable_end(now, "expired" if expired else "lost")
        if expired and anybody:
            self._probable_spent_at = now

    def _probable_evidence(self, best: str | None, score: float,
                           unnamed: str | None) -> str | None:
        """Why this frame says "probably somebody we know", or None.

        Only for a person the robot may name: waiting for a closer look at
        somebody it would never name (consent) is a stranger kept waiting
        for nothing, and "company" does not improve with distance. A close
        second (margin) does — the two names part as the face grows.
        """
        if best is None or score < PROBABLE_LOW:
            return None
        if unnamed is not None and not unnamed.startswith("margin"):
            return None
        from app import consent

        try:
            allowed = consent.may_name(best)[0]
        except Exception:                          # unreadable = not consented
            allowed = False
        if not allowed:
            return None
        if unnamed is not None:
            return "margin"
        return "band" if score < self.threshold else "far"

    def _probable_support(self, now: float, why: str, px: int, score: float) -> None:
        p = self._probable
        if p is None:
            if self._probable_spent_at is not None:
                return
            p = self._probable = {"start": now, "seen": now, "frames": 0, "held": 0,
                                  "why": {}, "px_first": int(px), "px_max": 0,
                                  "score_max": -1.0, "closer": 0}
            self.events.append({"event": "face_probable", "phase": "start", "why": why,
                                "px": int(px), "score": round(float(score), 3),
                                "band": [PROBABLE_LOW, self.threshold],
                                "wait_s": self.probable_wait_s})
            logger.info("face: probably somebody known (%s, %dpx, %.3f) — holding "
                        "stranger frames up to %.0fs", why, px, score, self.probable_wait_s)
        p["seen"] = now
        p["frames"] += 1
        p["px_max"] = max(p["px_max"], int(px))
        p["score_max"] = max(p["score_max"], float(score))

    def _probable_hold(self, frame, face, score: float, unnamed: str | None) -> str | None:
        """Why this stranger frame is held while a hold is live, or None.

        The frames a colleague also produces: a close second, a score in
        the band, a turned head, blur, or too small to greet a stranger at
        all. A clear, close, frontal, low-scoring frame is not one of them.
        """
        if unnamed is not None:
            return "margin" if unnamed.startswith("margin") else None
        x1, _, x2, _ = face.bbox
        if score >= PROBABLE_LOW:
            return "band"
        if (x2 - x1) < self.min_face_px * self.STRANGER_PX_FACTOR:
            return "far"
        yaw = _yaw(face.kps)
        if yaw is not None and abs(yaw) > self.YAW_MAX:
            return "turned"
        if _sharpness(frame, face.bbox) < self.SHARP_MIN:
            return "blurred"
        return None

    def _probable_end(self, now: float, phase: str, px: int | None = None,
                      score: float | None = None) -> float | None:
        """Close the hold with one turn-log row; its length, or None if none."""
        p = self._probable
        if p is None:
            return None
        self._probable = None
        waited = round(now - p["start"], 2)
        row = {"event": "face_probable", "phase": phase, "waited_s": waited,
               "frames": p["frames"], "held": p["held"], "held_by": dict(p["why"]),
               "px_first": p["px_first"], "px_max": p["px_max"],
               "score_max": round(p["score_max"], 3), "closer": p["closer"]}
        if px is not None:
            row["px"] = int(px)
        if score is not None:
            row["score"] = round(float(score), 3)
        self.events.append(row)
        logger.info("face: probable hold ended (%s) after %.1fs — %d frame(s) held %s",
                    phase, waited, p["held"], p["why"] or "")
        return waited

    def _far(self, frame, face, found, now: float, crop) -> None:
        """A face under FACE_MIN_PX, with the hold on: described only to keep
        a probable arrival alive. It never greets and never names — distance
        is still a threshold — and it confirms nothing (the streak resets,
        as it always did for a far face)."""
        x1, _, x2, _ = face.bbox
        px = int(x2 - x1)
        self._streak_key, self._streak = None, 0
        self._track = None
        if self._probable is None and self._probable_spent_at is not None:
            # This arrival's hold already ran out, and no new one starts
            # until the frame has been empty (`_probable_support`): the
            # embedding would decide nothing. A colleague at a desk across
            # the room is "spent" all day — 100-200 ms of ONNX per frame on
            # the robot, for nothing, where a far face used to cost a locate.
            self._note("เห็นหน้าแต่ไกลเกิน (%dpx < %dpx)" % (px, self.min_face_px),
                       faces=len(found), px=px)
            return None
        if self._describe(frame, face, crop) is None:
            self._note("เห็นหน้าแต่ไกลเกิน (%dpx < %dpx)" % (px, self.min_face_px),
                       faces=len(found), px=px)
            return None
        _i, score, _runner = self.gallery.match2(face.vec)
        best = self.gallery.names[_i]
        why = self._probable_evidence(best, score, None)
        if why is not None:
            self._probable_support(now, "far", px, score)
            self._want_closer(frame, face, now)
            # No name: the chest screen faces the room.
            self._note("อาจเป็นคนที่รู้จัก แต่ยังไกล (%dpx, %.2f) — รอให้เข้ามาใกล้" % (px, score),
                       faces=len(found), px=px, score=round(float(score), 3), probable=True)
        else:
            self._note("เห็นหน้าแต่ไกลเกิน (%dpx < %dpx)" % (px, self.min_face_px),
                       faces=len(found), px=px)
        return None

    def _describe(self, frame, face, crop) -> np.ndarray | None:
        """`face.vec`, from the page's closer look when it shows this face,
        else from the frame. None when neither can be read."""
        if crop is not None:
            vec = self._from_crop(face, crop)
            if vec is not None:
                face.vec = vec
                if self._probable is not None:
                    self._probable["closer"] += 1
                return vec
        if face.vec is None and faces.describe(frame, face) is None:
            return None
        return face.vec

    @staticmethod
    def _from_crop(face, crop: dict) -> np.ndarray | None:
        """The crop's vector for `face`, or None when the crop shows another
        face, an old place, or nothing readable. The crop's own boxes are
        mapped back into frame pixels first: `region` is where it was cut
        from, `scale` how many crop pixels per frame pixel."""
        try:
            image = crop["frame"]
            x0, y0 = float(crop["region"][0]), float(crop["region"][1])
            scale = float(crop.get("scale") or 1.0)
        except (KeyError, TypeError, ValueError, IndexError):
            return None
        if image is None or scale <= 0:
            return None
        best, best_iou = None, CROP_IOU
        for cand in faces.locate(image):
            bx = (cand.bbox[0] / scale + x0, cand.bbox[1] / scale + y0,
                  cand.bbox[2] / scale + x0, cand.bbox[3] / scale + y0)
            iou = _overlap(face.bbox, bx)
            if iou >= best_iou:
                best, best_iou = cand, iou
        if best is None:
            return None
        if best.vec is None and faces.describe(image, best) is None:
            return None
        return best.vec

    def _want_closer(self, frame, face, now: float) -> None:
        h, w = frame.shape[:2]
        x1, _, x2, _ = face.bbox
        live = self._probable_live(now)
        self.look_closer = {"bbox": tuple(int(v) for v in face.bbox), "w": int(w), "h": int(h),
                            "px": int(x2 - x1), "bearing": round(bearing_deg((x1 + x2) / 2.0, w), 1),
                            "far": (x2 - x1) < self.min_face_px, "probable": live,
                            # Which hold this is (its start), so the base
                            # turns at most once per probable arrival.
                            "hold": self._probable["start"] if live else None, "at": now}

    def hold_still(self, until: float) -> None:
        """Decide nothing on frames before `until` (the base is turning)."""
        self.still_until = max(self.still_until, float(until))

    def drain_events(self) -> list[dict]:
        """The turn-log rows gathered since the last call (greeter loop)."""
        out, self.events = self.events, []
        return out

    def _usable(self, found: list) -> int:
        """Faces of comparable size to the nearest: `_why_not_name`'s company
        bar (a poster or somebody across the room is not company)."""
        if not found:
            return 0
        primary = max(found, key=lambda f: f.bbox[2] - f.bbox[0])
        bar = max(self.min_face_px, 0.6 * (primary.bbox[2] - primary.bbox[0]))
        return sum(1 for f in found if (f.bbox[2] - f.bbox[0]) >= bar)

    #: Laplacian variance of the face, grey, resized to 64x64 (so the number
    #: is about focus, not face size). Measured 2026-10-08 on the scan
    #: crops pasted into a 1024x768 frame at 70/110 px, JPEG 70, with
    #: horizontal/vertical/diagonal motion and gaussian blur (420 frames):
    #: of the frames blurred under the 0.50 bar — the ones that read as a
    #: stranger — this holds 87% at 90 (79% at 75, 92% at 100). It also
    #: reads face contrast, not only focus: one colleague's *unblurred*
    #: front shot reads 95, so a higher cut starts holding clear frames.
    #: Holding a clear stranger costs at most the wait; set from the
    #: doorway's `held` counts once there are some.
    SHARP_MIN = 90.0
    #: Nose offset from the eyes' midpoint, in eye-distances (0 = facing
    #: the lens). The scan's side poses sit at 0.37-1.70; there the own
    #: score fell to 0.46-0.73 and the best *other* person rose to 0.50.
    YAW_MAX = 0.5
    #: Same arrival = boxes overlapping this much, no more than this apart.
    #: At 6 fps a person walking up moves a fraction of their face width.
    TRACK_IOU = 0.2
    TRACK_GAP_S = 1.0

    def _why_not_name(self, name: str, score: float, runner_up: float,
                      found: list) -> str | None:
        """None when the name may be spoken; otherwise the reason it may not.

        Three reasons, from the outside review of 2026-09-01, each of which
        turns a name into a plain greeting rather than into silence — a
        greeting without a name is never wrong, a wrong name is:

        - **margin**: the runner-up (a different person) is within
          `margin` of the best. Close second = the frame where the nearest
          is most likely the wrong one.
        - **company**: more than one usable face in the frame. The model
          is told one name and addresses everybody by it, and there is no
          speaker identification to say who answered.
        - **consent**: no record that this person agreed (only when
          FACE_REQUIRE_CONSENT is on), or a record that says they withdrew
          or that it expired (always). See app/consent.py.
        """
        if runner_up >= 0 and (score - runner_up) < self.margin:
            return "margin (runner-up %.3f within %.2f)" % (runner_up, self.margin)
        if self.name_when_alone:
            # Company is somebody standing *with* them: a second face of
            # comparable size. min_face_px alone (50px on the owner's
            # doorway setup) let a poster, a screen, or a person across
            # the room unname the one at the desk — measured 2026-09-01,
            # the owner alone in frame, "company (2 faces in frame)".
            usable = self._usable(found)
            if usable > 1:
                return "company (%d faces in frame)" % usable
        from app import consent

        allowed, state = consent.may_name(name)
        if not allowed:
            return "consent %s" % state
        return None

    #: A stranger must be this many times min_face_px wide before the
    #: anonymous greeting fires (see the stranger branch of `see`).
    STRANGER_PX_FACTOR = 1.3
    #: A colleague confirmed within this many seconds of an anonymous
    #: greeting, standing where it fired, is the same person arriving.
    STRANGER_TO_NAME_S = 20.0
    #: How much the two boxes must overlap to be "the same spot".
    SAME_SPOT_IOU = 0.3

    def forget(self) -> None:
        """Drop the cooldowns and the streak.

        For tests, and for the end of a day: "we already greeted them"
        should not survive into tomorrow morning.
        """
        self._streak_key, self._streak = None, 0
        self._last_greeted.clear()
        self._strangers.clear()
        self._named = None
        self._track = None
        self._tracks = _Tracks()
        self._probable = None
        self._probable_spent_at = None
        self.still_until = 0.0

    # -- listening focus: every face, every frame -------------------------

    def _publish(self, frame, now: float) -> None:
        """Hand this frame's faces to listening focus and active speaker.

        Every face the detector located, not only the nearest the greeting
        looks at: focus asks whether *anybody* engaged is in front, and the
        active speaker needs each mouth. Never raises — the greeting loop
        must outlive any consumer.
        """
        try:
            found = self._located or []
            width = int(frame.shape[1]) if getattr(frame, "ndim", 0) >= 2 else 0
            pairs = self._tracks.update(found, now, self._confirmed)
            people = None
            obs = []
            for track, face in pairs:
                name = track["name"]
                if name:
                    # A track keeps its name between confirmations, so the
                    # consent rule is asked again here, every frame, as the
                    # greeting does: a revocation takes effect on the next
                    # frame, not when the person next walks in.
                    from app import consent

                    if people is None:
                        try:
                            people = consent.load()
                        except consent.Unreadable:
                            people = False
                    if people is False or not consent.may_name(name, people=people)[0]:
                        name = None
                x1, _y1, x2, _y2 = face.bbox
                obs.append((track["id"], face, name,
                            bearing_deg((x1 + x2) / 2.0, width) if width else 0.0,
                            int(x2 - x1)))
            latest = [(tid, tuple(face.bbox), face.kps, now) for tid, face, *_ in obs]
            height = int(frame.shape[0]) if getattr(frame, "ndim", 0) >= 2 else 0
            with _latest_lock:
                _latest[:] = latest
                _latest_size[:] = [width, height]
            from app import focus

            if focus.enabled():          # off under MULTI_SESSION too
                focus.observe_faces([focus.FaceObs(track=tid, t=now, bearing_deg=b,
                                                   width_px=px, yaw_deg=yaw_deg(face.kps),
                                                   name=name)
                                     for tid, face, name, b, px in obs], now)
            if settings.asd_enabled:
                _active_speaker(frame, latest, now)
        except Exception:                          # pragma: no cover - belt
            logger.exception("face: could not publish the frame to listening focus")


#: The hold's reasons as the chest screen's debug panel says them.
_HOLD_TH = {"band": "คะแนนใกล้เกณฑ์", "margin": "คล้ายสองคน", "far": "ยังไกล",
            "turned": "หน้าหันข้าง", "blurred": "ภาพเบลอ"}

#: "Nothing was confirmed about the nearest face this frame" — distinct from
#: None, which is "confirmed, and not to be named".
_UNSET = object()
#: Track ids, per process: stable per person while in view.
_TRACK_IDS = itertools.count(1)


class _Tracks:
    """Every face in view, followed frame to frame by box overlap.

    The greeting's own `_track` follows only the nearest face; listening
    focus needs everybody — "is anybody in front talking" and "which mouth
    moved". Same overlap rule as the arrival wait (TRACK_IOU), with a gap
    as long as FOCUS_HOLD_S's default so a blink of a missed detection does
    not make a new person.
    """

    IOU = Watcher.TRACK_IOU
    GAP_S = 1.5
    MAX = 16

    def __init__(self) -> None:
        self._live: list[dict] = []

    def update(self, found: list, now: float, confirmed=_UNSET) -> list[tuple[dict, object]]:
        self._live = [t for t in self._live if 0.0 <= now - t["at"] <= self.GAP_S]
        free = list(self._live)
        out = []
        for i, face in enumerate(found):
            best, best_iou = None, self.IOU
            for track in free:
                iou = _overlap(face.bbox, track["bbox"])
                if iou >= best_iou:
                    best, best_iou = track, iou
            if best is None:
                best = {"id": next(_TRACK_IDS), "bbox": tuple(face.bbox), "at": now,
                        "name": None}
                self._live.append(best)
            else:
                free.remove(best)
            best["bbox"], best["at"] = tuple(face.bbox), now
            if i == 0 and confirmed is not _UNSET:
                # found[0] is the face the greeting judged. A confirmed
                # stranger, or a refused name, clears what the track held.
                best["name"] = confirmed
            out.append((best, face))
        del self._live[:-self.MAX]
        return out


_latest_lock = threading.Lock()
#: The last published frame's faces: [(track, bbox, kps, t)].
_latest: list[tuple] = []
#: That frame's [width, height] -- where a box sits vertically, for the head's
#: tilt (app/head_follow.py). [0, 0] until a frame has been published.
_latest_size: list[int] = [0, 0]
_asd_state = {"missing": False, "failed_at": 0.0}


def latest_tracks() -> list[tuple]:
    """[(track, bbox, kps, t)] of the last processed frame — for the active
    speaker (app/active_speaker.py) to crop mouths between detections. Empty
    until a frame has been published (FOCUS_ENABLED or ASD_ENABLED)."""
    with _latest_lock:
        return list(_latest)


def latest_frame_size() -> tuple[int, int]:
    """(width, height) of the frame `latest_tracks` came from; (0, 0) = none yet."""
    with _latest_lock:
        return int(_latest_size[0]), int(_latest_size[1])


def _active_speaker(frame, tracks: list[tuple], now: float) -> None:
    """`active_speaker.process_detections(frame, tracks, t)`, if that module
    exists. Runs on the greeting loop's worker thread at FACE_FPS: the
    consumer hands heavy work to its own thread."""
    if _asd_state["missing"]:
        return
    try:
        from app import active_speaker
    except ImportError:
        _asd_state["missing"] = True
        logger.warning("ASD_ENABLED but app/active_speaker.py is not importable — "
                       "mouth activity off until restart")
        return
    try:
        active_speaker.process_detections(frame, tracks, now)
    except Exception:
        if now - _asd_state["failed_at"] > 60.0:
            _asd_state["failed_at"] = now
            logger.exception("active_speaker.process_detections failed")
