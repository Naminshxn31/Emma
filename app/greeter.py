"""
The camera as a second way in: somebody walks up, the robot opens its mouth.

`facewatch.Watcher` answers "is there somebody there, and is it anybody we
know". This module owns the parts that decision cannot: a camera handle, a
clock, and the one road to the model. It is deliberately the only place the
two meet — `facewatch` says in its own docstring that it does not talk to
the model or hold a socket, and that stays true.

Why this is not just "wake on a face"
-------------------------------------
There is already a way in: the wake word. It has a measured ceiling — about
half the calls that miss never reach the detector at all, too quiet or too
far, and no threshold fixes a sound that is not there. A camera fails in
completely different conditions to a microphone, which is the whole reason
to add one. It is a second door, not a better lock.

What it costs, and why it is off by default
-------------------------------------------
Every sighting that gets through opens a Gemini session. That is the exact
thing the wake word exists to prevent — "เปิดหน้าเว็บทิ้งไว้ทั้งวันไม่เผา
quota" — so a camera pointed at a corridor with `FACE_ENABLED=true` is a
bill. Three gates stand in front of it and all three are in `facewatch`:
a face has to be close enough (`FACE_MIN_PX`), the same answer has to
arrive several frames running (`FACE_CONFIRM_FRAMES`), and the same person
cannot do it twice inside `FACE_COOLDOWN_S` (ten minutes). The switch
itself is off, and the showroom default must stay that way.

Why the greeting is a summon and not a `send_text`
--------------------------------------------------
When somebody walks up there is usually no session at all — the line is
asleep waiting for a name. `events.announce(summon=True)` is exactly this
case: it rings the standby browser, waits for it to dial, and delivers the
text on the fresh line. Everything the four earlier event sources learned
the hard way (wait for the audio queue, re-check afterwards, one at a time,
never deliver to the next guest) comes with it for free. Writing a second
path here would be the four-exits bug with a camera attached.

Probably somebody we know (owner, 9 Oct 2026)
---------------------------------------------
"บางทีหุ่นอาจจะจดจำใบหน้าแต่เพราะอาจจะอยู่ไกลทำให้หุ่นไม่ชัวว่าเป็นคนรู้จัก
พยายามให้หุ่นขยับเพื่อเช็ค ถ้าเป็นคนรู้จักก็ทักทายด้วยชื่อพร้อมจับมือ". Four
switches, all off, each a separate decision:

- FACE_PROBABLE_WAIT_S — `facewatch` holds stranger frames while the best
  match is a consented colleague in the band under the bar (or far, blurred,
  turned, a close second), for at most that long. The name rules do not
  move: a hold never names anybody, it only stops the anonymous greeting
  from winning the race against the named one.
- FACE_ZOOM_ENABLED — this loop asks the face page for a q95 crop of the
  face the watcher wants a closer look at (`_ask_closer`), and hands the
  answer to the next `see()` once (`robot_camera.feed.take_crop`).
- FACE_TURN_TO_CHECK — the owner's "ขยับเพื่อเช็ค", narrowed to the one
  motion that cannot walk into anybody: a turn in place toward a probable
  far face, at most 30 degrees, through `robot_chassis.nudge` and refused
  on the dock, mid-walk, mid-conversation, with motion off, or without lidar
  room (`_turn_refusal`). Docked — the normal case — the first two do the
  work and the person walking up does the rest. Turns never add up: the
  heading stays within that angle of where she was put (`_within_home`).
- FACE_HANDSHAKE_NAMED_ONLY — the greeting gesture (group 6, the verified
  handshake) only with a name and one face in frame.

Two things the face-scan screen needs from a loop that runs all day
-------------------------------------------------------------------
The gallery is followed, not loaded once: a scan saves new vectors and the
person scanned expects to be recognised on the spot, not after somebody
restarts the server. And the loop can be paused: a scan in progress puts
a stranger at the lens on purpose, for a minute, turning their head —
exactly what the loop exists to pre-ring and greet.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from pathlib import Path

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.greeter")

#: Held so the loop is not garbage collected, and so shutdown can stop it.
_task: asyncio.Task | None = None

#: How long to wait after a camera read comes back empty before giving up on
#: the camera entirely. A USB camera unplugged mid-day should not spin a
#: thread reading None forever, and it should say so once.
MAX_EMPTY_READS = 30

#: How often, at most, the loop looks at the gallery file's timestamp. A
#: face scan saves new vectors and the person standing there expects to be
#: known now, not after a restart; one stat every two seconds is nothing
#: next to the 100-200ms of ONNX each frame with a face in it costs.
GALLERY_POLL_S = 2.0

#: The longest one `pause()` can hold the loop off. A scan that crashed
#: half way, or a tab closed mid-scan, must not leave the door camera deaf
#: for the rest of the day — the pause runs out on its own.
MAX_PAUSE_S = 900.0

#: Monotonic deadline of the current pause; 0.0 is "not paused".
_paused_until = 0.0

#: Named holds: holder -> monotonic deadline. Beside `_paused_until`, not
#: folded into it, because one extend-only deadline cannot be *given back*
#: by one holder without taking everybody else's with it. That was the face
#: scan's pause: a scan that ended could only `resume()` — zeroing the
#: deadline — which wiped the three-second grace another scan's save had
#: just set (the person just filed, greeted as a stranger before the new
#: gallery loaded), and a scan that saved while a second was still open
#: could release nothing at all.
_holds: dict[str, float] = {}

#: FACE_ZOOM: at most one crop request this often. The page answers in a
#: few hundred ms; asking faster only queues looks at the same moment.
ZOOM_INTERVAL_S = 0.5

#: FACE_TURN_TO_CHECK. The ceiling is in code, not only in .env: whatever
#: FACE_TURN_MAX_DEG says, a camera-triggered turn is never more than this.
TURN_MAX_DEG = 30.0
#: A face this close to the camera's axis is centred already; turning
#: would buy nothing and move a machine for it.
TURN_DEADBAND_DEG = 8.0
#: At most one turn this often, and one per probable arrival.
TURN_COOLDOWN_S = 30.0
#: The room a turn needs: the robot's rectangle (0.78 m across the arms)
#: swept through the angle plus the coast, kept this far from every lidar
#: return — drive_route's TURN_MARGIN_M / TURN_COAST_DEG and the walkable
#: fan's `_WALK_TURN_GAP_M` in main.py, the margins already in use.
TURN_MARGIN_M = 0.04
TURN_COAST_DEG = 3.0
#: drive_route will not judge a frame with fewer valid returns than this.
TURN_MIN_RETURNS = 200
#: Frames decide nothing for this long after a turn is sent (motion blur,
#: the box jumping), plus a second per TURN_DEG_PER_S of angle.
TURN_SETTLE_S = 1.5
TURN_DEG_PER_S = 20.0
#: Monotonic time of the last turn sent, and the hold it was for.
_last_turn_at = 0.0
_turned_for: float | None = None
#: Where the robot stood, and which way it faced, before its first turn to
#: check there. Nothing ever turns it back, so without this the turns added
#: up -- one 30 degree check per far face, a random walk of the heading over
#: a day, until the lens faced a wall and the door greeting was over. Every
#: turn keeps the heading within FACE_TURN_MAX_DEG of this; moved further
#: than TURN_HOME_RADIUS_M (walked, driven, docked), the new spot is home.
_turn_home: dict | None = None
TURN_HOME_RADIUS_M = 0.3


def _clamped(seconds) -> float:
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds):
        return 0.0
    return min(max(seconds, 0.0), MAX_PAUSE_S)


def hold(key: str, seconds: float) -> None:
    """Hold the loop off for `seconds` on behalf of `key`, *replacing* that
    holder's earlier deadline — shorter included — and touching nobody
    else's. Zero, negative or not a number releases it.

    Replace, not extend, because the holder is the one that knows the whole
    answer: `face_scan` recomputes its deadline from the scans still open
    every time one opens, ends or saves, and an extend-only hold would keep
    a scan's deadline alive after the scan itself was gone. Clamped to
    MAX_PAUSE_S like `pause()`.
    """
    seconds = _clamped(seconds)
    if seconds <= 0:
        _holds.pop(key, None)
        return
    _holds[key] = time.monotonic() + seconds


def release(key: str) -> None:
    """Give back `key`'s hold, now. Every other pause stays."""
    _holds.pop(key, None)


def pause_left() -> float:
    """Seconds until nothing holds the loop off; 0.0 when nothing does."""
    until = max([_paused_until, *_holds.values()])
    return max(0.0, until - time.monotonic())


def pause(seconds: float) -> None:
    """Hold recognition, the pre-ring and greetings off for `seconds`.

    Why at all: a face scan in progress puts a stranger at the lens on
    purpose — close, still, turning their head through five poses for a
    minute. That is a textbook arrival: left running, the loop pre-rings
    for them inside a second and greets them as a stranger mid-scan, the
    robot talking over the staff member walking them through it.

    Clamped to 0..MAX_PAUSE_S, and it only ever extends: a second request
    (a retry, a step that re-asks) must not cut a longer pause short.
    `resume()` is the one way to end it early.
    """
    global _paused_until
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = min(max(seconds, 0.0), MAX_PAUSE_S)
    # The running deadline goes first. Python's json reads "NaN" happily,
    # NaN compares false with everything, and max() then keeps its first
    # argument — this way round a NaN request changes nothing; the other
    # way round it would end the pause a scan is standing in.
    _paused_until = max(_paused_until, time.monotonic() + seconds)


def resume() -> None:
    """End every pause now, whatever is left of it — named holds included.

    The big switch, for a person or a test. `face_scan` does not call it:
    it gives back its own hold with `release`/`hold`, so ending one scan
    cannot end another's pause or the grace after a save.
    """
    global _paused_until
    _paused_until = 0.0
    _holds.clear()


def paused() -> bool:
    now = time.monotonic()
    return now < _paused_until or any(now < until for until in _holds.values())


def _gallery_signature(path: Path) -> tuple[int, int] | None:
    """(st_mtime_ns, st_size) of the gallery file, or None if it is not there.

    Size as well as the timestamp: two saves inside one tick of a coarse
    filesystem clock share a timestamp, and a save that added somebody's
    shots is bigger.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _GalleryFollower:
    """Reload the gallery into a running Watcher when the file changes.

    In place, into the watcher that is already running, rather than
    `stop()` then `start()`: a restart builds a new Watcher, which forgets
    who was just greeted (the cooldowns, the remembered strangers, the
    same-arrival memory) — so everybody standing in front of the robot at
    that moment is greeted again — and it releases and reopens the camera,
    the slow liveness-probing open, to change a file the loop only needs to
    read.
    """

    def __init__(self, path: Path, baseline: tuple[int, int] | None):
        self.path = path
        #: The version the running gallery came from. Taken by the caller
        #: *before* the first load: a save landing between that stat and the
        #: load then reads as a change and is loaded again — harmless —
        #: where the other order would miss it until the next save.
        self.loaded = baseline
        #: The version last warned about, so a broken file is one line.
        self.warned: tuple[int, int] | None = None
        self.next_check = time.monotonic() + GALLERY_POLL_S

    async def poll(self, watcher) -> None:
        from app import faces

        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + GALLERY_POLL_S
        stamp = _gallery_signature(self.path)
        if stamp is None or stamp == self.loaded:
            # Gone is not "empty": a rebuild may be mid-write, and the
            # vectors in memory are still the best answer the robot has.
            return
        try:
            gallery = await asyncio.to_thread(faces.Gallery.load, self.path)
        except Exception as exc:
            # Keep what works. A version that failed is tried again on the
            # next poll — `Gallery.save` is atomic, but a file copied in by
            # hand can be caught half way, and a handle held for a moment
            # by something else lets go — yet it is *said* once per
            # version, so a file broken for good is one line in the log,
            # not one every two seconds.
            if self.warned != stamp:
                self.warned = stamp
                logger.warning("face gallery changed but did not load — keeping "
                               "the one in memory: %s", exc)
            return
        self.loaded = stamp
        # Assigned here, in the loop's own coroutine, between two awaited
        # `see()` calls — never from the loading thread. `see()` runs in a
        # worker thread and reads `gallery` more than once per frame
        # (match2 for an index, then names[i] and meta[i]); a swap landing
        # between those reads names the wrong person, or indexes past the
        # end of a smaller gallery.
        watcher.gallery = gallery
        logger.info("face gallery reloaded: %d vectors", len(gallery))


def greeting_for(sighting) -> str:
    """What to send to the model when somebody is standing there.

    Instructions, not words to repeat: the model chooses its own sentence,
    the same as every other announcement. Two things are named explicitly
    because the model reaches for both on its own and both are wrong in a
    showroom — narrating the camera ("ระบบจดจำใบหน้าตรวจพบ..."), which is
    unsettling to hear from a robot you have just walked up to, and telling
    a stranger that it does not know them, which is an accusation rather
    than a greeting.
    """
    if sighting.kind == "known":
        who = sighting.name
        role = sighting.group
        from app import focus

        if focus.labels_deliverable():
            # With speaker labels there IS a way to know who is speaking,
            # and the default sentence below ("ไม่มีระบบจำเสียง", never use
            # the name again) would contradict the label line in the system
            # instruction — two orders that cancel each other, the CLAUDE.md
            # "คำสั่งที่ขัดกันเอง" trap. The name is allowed back exactly
            # where a label carries it, and nowhere else.
            return (
                "%s เพิ่งเดินเข้ามาหน้าหุ่น%s — ทักทายด้วยชื่อสั้นๆ แล้วถามว่า"
                "ให้ช่วยอะไรไหม ห้ามพูดถึงกล้อง ห้ามพูดว่า "
                "'ระบบจดจำใบหน้าตรวจพบ' หรือ 'สแกนเจอ' "
                "หลังจากนี้เรียกชื่อได้เฉพาะเทิร์นที่มีบันทึก [ผู้พูด: คุณ%s] "
                "เทิร์นอื่นอาจเป็นคนอื่นที่ยืนอยู่ด้วย"
                % (who, (" (%s)" % role) if role else "", who)
            )
        # The name is for this one sentence. There is no speaker
        # identification in this system: after the greeting the model has
        # no way to know whether the voice it hears is this person or
        # somebody standing beside them, and left alone it assumes — and
        # keeps using the name, and answers a stranger's question as if it
        # were theirs. Quoted rather than described, because that is what
        # has worked on this model every time.
        return (
            "%s เพิ่งเดินเข้ามาหน้าหุ่น%s — ทักทายด้วยชื่อสั้นๆ แล้วถามว่า"
            "ให้ช่วยอะไรไหม ห้ามพูดถึงกล้อง ห้ามพูดว่า "
            "'ระบบจดจำใบหน้าตรวจพบ' หรือ 'สแกนเจอ' "
            "ใช้ชื่อเฉพาะในประโยคทักนี้เท่านั้น หลังจากนี้ห้ามเรียกชื่ออีก "
            "และห้ามถือว่าคนที่พูดคือ%s — ไม่มีระบบจำเสียง อาจเป็นคนอื่นที่ยืนอยู่ด้วย"
            % (who, (" (%s)" % role) if role else "", who)
        )
    if settings.face_camera_source == "robot" and settings.assistant_profile == "condo":
        # The robot in project mode (owner 10 Oct) greets a stranger with the
        # opening the owner settled on 14 Sep -- English welcome + ONE light
        # question about the project -- not the generic line below: the robot
        # log of 9-10 Oct shows "สวัสดีค่ะ มีอะไรให้ช่วย..." every time, the
        # very "มีอะไรให้ช่วยไหม" GREETING replaced. The sales room's camera
        # (another FACE_CAMERA_SOURCE) keeps its line unchanged.
        from app.prompts import GREETING

        return (
            "มีคนเดินเข้ามายืนหน้าหุ่น ยังไม่รู้ว่าเป็นใคร — ห้ามพูดถึงกล้อง และห้ามบอกว่าจำไม่ได้"
            "หรือไม่รู้จัก ห้ามเปิดด้วย \"มีอะไรให้ช่วยไหม\" — " + GREETING
        )
    return (
        "มีคนเดินเข้ามายืนหน้าหุ่น ยังไม่รู้ว่าเป็นใคร — ทักทายสั้นๆ "
        "แล้วถามว่าให้ช่วยอะไรไหม ห้ามพูดถึงกล้อง และห้ามบอกว่าจำไม่ได้"
        "หรือไม่รู้จัก"
    )


def _say_who_is_missing(watcher) -> None:
    """Name anybody enrolled from the camera who is not in the gallery yet.

    Enrolling and rebuilding are deliberately two steps — the gallery is what
    the robot says names out of, and it should not change because somebody
    was practising at the station. But the gap between them is invisible from
    the doorway: standing in front of the camera and not being recognised
    looks exactly like a threshold that is too high, and the answer is often
    "nobody ran build_face_gallery.py". A name in the boot log costs nothing
    and answers it before it is asked.
    """
    from app import enrollment

    try:
        known = set(watcher.gallery.names) if watcher.gallery is not None else set()
        missing = [name for name, _ in enrollment.enrolled() if name not in known]
    except Exception:                              # pragma: no cover - belt
        return
    if missing:
        logger.warning(
            "enrolled but not in the gallery: %s — run "
            "build-face-gallery.cmd (or python scripts/build_face_gallery.py) "
            "or they will be greeted as strangers", ", ".join(missing))


def _track_fields(sighting) -> dict:
    """The arrival wait's numbers for the turn log (FACE_TRACK_WAIT_S on),
    and how long a "probably known" hold ran (FACE_PROBABLE_WAIT_S on)."""
    out = {}
    track_s = getattr(sighting, "track_s", None)
    if track_s is not None:
        out = {"track_s": track_s, "held": getattr(sighting, "held", 0)}
    probable_s = getattr(sighting, "probable_s", None)
    if probable_s is not None:
        out["probable_s"] = probable_s
    return out


def _handshake(sighting) -> tuple[bool, str]:
    """(offer the greeting gesture, why) — FACE_HANDSHAKE_NAMED_ONLY.

    A handshake is offered to somebody the robot is naming: known, past
    margin and consent (that is what `kind == "known"` means — `facewatch`
    hands back a stranger for every refusal), and alone in frame. Two
    people walking in together get a greeting without a hand — the same
    reason they get one without a name. `faces_in_frame` 0 (not counted)
    reads as "not alone".
    """
    if not settings.robot_greeting_gesture_enabled:
        return False, "gesture_off"
    if sighting.kind != "known" or not sighting.name:
        return False, "stranger"
    if getattr(sighting, "faces_in_frame", 0) != 1:
        return False, "company"
    return True, "named_alone"


async def _greet(sighting) -> None:
    extra = {}
    if sighting.meta.get("unnamed"):
        # Recognised but greeted without the name; the reason (margin,
        # company, consent) is the whole story of that decision.
        extra = {"unnamed": sighting.meta["unnamed"]}
    extra.update(_track_fields(sighting))
    arm = True
    if settings.face_handshake_named_only:
        # Off, the gesture rides every camera greeting exactly as before
        # (ROBOT_GREETING_GESTURE_ENABLED and the arm's own gates decide).
        arm, why = _handshake(sighting)
        extra.update(handshake=arm, handshake_why=why)
    turnlog.record("face_seen", kind=sighting.kind, name=sighting.name,
                   score=round(sighting.score, 3), group=sighting.group, **extra)
    from app import events

    await events.announce(
        greeting_for(sighting),
        source="face_known" if sighting.kind == "known" else "face_stranger",
        summon=True,
        arm_greeting=arm,
        # Confirmed before a scan began, delivered after: the summon wait
        # and the audio queue can hold a greeting for seconds, and one
        # landing mid-scan is the interruption `pause()` exists to stop.
        still_relevant=lambda: not paused(),
    )


def _face_debug(status: dict) -> None:
    """The camera loop's verdict, for the chest screen's temporary debug
    panel (owner 2026-09-30: "อยากรู้ว่ามันจับคนได้ไหม" — the WAKE_DEBUG
    lesson wearing a face: from the doorway, "ไกลเกิน", "คะแนนต่ำ", "ติด
    cooldown" and "กำลังคุยอยู่" are indistinguishable without a screen that
    says which). Best-effort by design: the panel is diagnostic and a failure
    here must never touch the greeting loop."""
    try:
        from app import robot_camera

        robot_camera.feed.set_face_status(status)
    except Exception:                          # pragma: no cover - belt
        pass


def _log_watch_events(watcher) -> None:
    """The watcher's decisions since the last frame, as turn-log rows.

    `facewatch` keeps no file handle (its docstring: it returns decisions);
    it queues them and this loop writes them. getattr: a watcher double in a
    test, or an older one, has no queue.
    """
    drain = getattr(watcher, "drain_events", None)
    if drain is None:
        return
    try:
        rows = drain()
    except Exception:                              # pragma: no cover - belt
        return
    for row in rows:
        row = dict(row)
        turnlog.record(row.pop("event", "face_probable"), **row)


def _zoom_on() -> bool:
    """A closer look needs the robot's own page: a lens on this machine has
    no page to ask (FACE_CAMERA_SOURCE=local)."""
    return settings.face_zoom_enabled and settings.face_camera_source == "robot"


def _take_crop():
    from app import robot_camera

    try:
        return robot_camera.feed.take_crop()
    except Exception:                              # pragma: no cover - belt
        logger.debug("face: crop unreadable", exc_info=True)
        return None


def _see(watcher, frame, crop_on: bool):
    """`watcher.see` with the page's crop, both in the worker thread: taking a
    crop decodes a q95 JPEG of up to 1024 px, and the event loop this would
    otherwise run on is also carrying the call's audio."""
    crop = _take_crop() if crop_on else None
    if crop is not None:
        return watcher.see(frame, None, crop)
    return watcher.see(frame)


def _ask_closer(want: dict, last_ask: float, now: float) -> float:
    """Ask the face page for a crop of `want` at most every ZOOM_INTERVAL_S.
    Returns the time of the last request."""
    if now - last_ask < ZOOM_INTERVAL_S:
        return last_ask
    from app import robot_camera

    try:
        robot_camera.feed.request_crop(want["bbox"], want["w"], want["h"],
                                       photo=settings.face_zoom_photo)
    except Exception:                              # pragma: no cover - belt
        logger.debug("face: crop request failed", exc_info=True)
    return now


#: How long a desk STOP holds the greeter's own turn -- the app's latch length.
TURN_DESK_STOP_HOLD_S = 60.0


def _app_latched(link) -> bool:
    """A robot face whose latest nav_status says the app's 60 s stop latch is
    on (`stop_latch_s` > 0). Every stop path on the robot -- the red bar, B,
    a desk STOP, the operator screen -- sets that latch and holds the app's
    own walks and gestures for it; this turn is the server's own motion and
    used to see none of it (review, 10 Oct). A face that never sends the
    field (an older APK) reads as not latched -- the rest of the checks are
    unchanged for it."""
    for face in link.live_faces():
        status = link.nav_status(face) or {}
        left = status.get("stop_latch_s")
        if isinstance(left, (int, float)) and not isinstance(left, bool) and left > 0:
            return True
    return False


async def _turn_refusal(angle: float) -> tuple[str | None, dict]:
    """Why the base must not turn by `angle` degrees now, or None — plus the
    numbers the turn log keeps (the lidar gap). Every check that can be
    answered here is, before the next one asks the board anything; the
    first refusal wins and the board is not touched after it.

    Strict on purpose. The robot turns *on its own*, toward somebody who
    did not ask, in a room with people in it: unknown is no.
    """
    from app import robot_chassis, robot_face_link
    from app import session as session_module

    info: dict = {}
    if not settings.robot_chassis_motion_enabled:
        return "motion_disabled", info
    if not robot_chassis.configured():
        return "no_chassis", info
    if abs(angle) < TURN_DEADBAND_DEG:
        return "centred", info
    if time.monotonic() - _last_turn_at < TURN_COOLDOWN_S:
        return "cooldown", info
    if _in_conversation(session_module._active):
        # Somebody is talking to Emma: turning away from them toward a far
        # face is the barge-in bug with wheels.
        return "live_session", info
    nav = robot_face_link.nav_activity()
    if nav in ("active", "stale"):
        return "walking", info
    if nav != "idle":
        # No face page vouching for "not walking" is not "not walking".
        return "nav_unknown", info
    if _app_latched(robot_face_link):
        # Somebody pressed a stop at the robot within the last minute.
        return "app_latched", info
    if robot_chassis.desk_stopped_within(TURN_DESK_STOP_HOLD_S):
        # ...or at the desk: on the "board" plan that STOP never reaches the
        # app, so its latch above cannot see it (review 12 Oct).
        return "desk_stopped", info
    try:
        robot_chassis.run_fence()
    except robot_chassis.Fenced as exc:
        return exc.code, info
    if robot_chassis.STATE.get("moving"):
        return "driving", info               # somebody is on the joystick
    try:
        docking = (await robot_chassis.power()).get("dockingStatus")
    except Exception:
        docking = None
    info["docking"] = docking
    if docking == "on_dock":
        return "docked", info
    if docking != "not_on_dock":
        return "dock_unknown", info
    try:
        over, _ok = robot_chassis._finished(await robot_chassis.current_action())
    except Exception:
        return "board_unreadable", info
    if not over:
        return "board_busy", info            # an action of its own is running
    from app import robot_lanes as rl

    try:
        scan = await robot_chassis.laserscan()
    except Exception:
        return "no_scan", info
    pts = rl.scan_points({"laser_points": scan.get("points") or []})
    info["returns"] = int(len(pts))
    if len(pts) < TURN_MIN_RETURNS:
        return "no_scan", info
    sweep = math.radians(angle) + math.copysign(math.radians(TURN_COAST_DEG), angle)
    gap = rl.turn_sweep_gap(pts, sweep, width=rl.ROBOT_WIDTH)
    info["gap_m"] = round(float(gap), 3)
    if gap < TURN_MARGIN_M:
        return "no_room", info
    return None, info


async def _turn_to_check(watcher, want: dict) -> bool:
    """Turn in place toward a probable far face (FACE_TURN_TO_CHECK).

    Never a step toward anybody: `nudge(forward_m=0.0, ...)` aims at the
    spot the robot stands on, with a new heading, bounded by construction
    (the target is computed once) and behind its own fences (motion lock,
    run fence, routes owning the base, localization) on top of
    `_turn_refusal`. One turn log row either way. Returns whether it turned.
    """
    global _last_turn_at
    from app import robot_chassis

    bearing = float(want.get("bearing") or 0.0)
    limit = max(0.0, min(TURN_MAX_DEG, float(settings.face_turn_max_deg)))
    angle = max(-limit, min(limit, bearing))
    row = {"bearing": round(bearing, 1), "angle": round(angle, 1), "px": want.get("px")}
    reason, info = await _turn_refusal(angle)
    row.update(info)
    if reason is None:
        angle, reason, more = await _within_home(angle, limit)
        row.update(more, angle=round(angle, 1))
    if reason is None:
        try:
            await robot_chassis.nudge(forward_m=0.0, turn_rad=math.radians(angle))
        except robot_chassis.Fenced as exc:
            reason = exc.code
        except PermissionError:
            reason = "motion_disabled"
        except Exception as exc:
            logger.warning("face: turn to check failed — %s: %s", type(exc).__name__, exc)
            reason = "chassis_error"
    if reason is not None:
        logger.info("face: not turning toward the far face (%.0f deg) — %s", angle, reason)
        turnlog.record("face_turn", turned=False, why=reason, **row)
        return False
    _last_turn_at = time.monotonic()
    hold = getattr(watcher, "hold_still", None)
    if hold is not None:
        hold(time.monotonic() + TURN_SETTLE_S + abs(angle) / TURN_DEG_PER_S)
    logger.info("face: turned %.0f deg toward a probable far face", angle)
    turnlog.record("face_turn", turned=True, **row)
    return True


async def _within_home(angle: float, limit: float) -> tuple[float, str | None, dict]:
    """(`angle` cut so the heading stays within `limit` degrees of
    `_turn_home`, why not, numbers for the turn log). Never more than the
    angle asked for, never the other way: the lidar sweep `_turn_refusal`
    checked covers the cut turn. An unreadable pose is a refusal -- unknown
    is no, as everywhere in this check."""
    global _turn_home
    from app import robot_chassis

    try:
        where = await robot_chassis.pose()
        x, y, yaw = float(where["x"]), float(where["y"]), float(where["yaw"])
    except Exception:
        return angle, "pose_unreadable", {}
    if not all(math.isfinite(v) for v in (x, y, yaw)):
        return angle, "pose_unreadable", {}
    home = _turn_home
    if home is None or math.hypot(x - home["x"], y - home["y"]) > TURN_HOME_RADIUS_M:
        home = _turn_home = {"x": x, "y": y, "yaw": yaw}
    off = math.degrees(math.atan2(math.sin(yaw - home["yaw"]), math.cos(yaw - home["yaw"])))
    cut = max(-limit, min(limit, off + angle)) - off
    cut = max(min(angle, 0.0), min(max(angle, 0.0), cut))
    info = {"offset": round(off, 1)}
    if abs(cut) < TURN_DEADBAND_DEG:
        return cut, "at_limit", info
    return cut, None, info


def _wants_turn(want: dict | None) -> bool:
    """A probable far face, not already tried for in this hold."""
    return bool(settings.face_turn_to_check and want and want.get("probable")
                and want.get("far") and want.get("hold") != _turned_for)


def _start_turn(watcher, want: dict) -> asyncio.Task:
    """One attempt per probable hold, turned or refused: the refusal reads
    the board (dock, lidar), and asking it every frame of a hold is a load
    on the link the joystick and the lanes share, for the same answer."""
    global _turned_for
    _turned_for = want.get("hold")
    return asyncio.create_task(_turn_to_check(watcher, want))


def _in_conversation(session) -> bool:
    """Is this a conversation a stranger's greeting would interrupt?

    Not merely "is there a session". The pre-ring opens the session
    *before* the face is confirmed, so by the time a stranger is
    confirmed there is always a live session — the one rung for them —
    and the first version of the no-interrupt rule threw away every
    stranger's greeting on that basis (2026-09-01 14:5x: "greeting never
    came (hold expired)", nobody greeted at all). A summoned session that
    has not yet had its greeting is a line held open for exactly this
    announcement; a session that has been greeted, or that somebody
    opened by name or button, is a conversation.
    """
    if session is None:
        return False
    if getattr(session, "summoned", False) and not getattr(session, "_greeting_turn_seen", False):
        # ...unless somebody already started talking on that line. On a
        # standing line (2026-09-30) the held-open session can be spoken to
        # before any camera greeting; a greeting landing then is the robot
        # cutting into their sentence — ประโยคทักทายไม่ให้พูดแทรก covers
        # this line too.
        return bool(getattr(session, "_ever_heard", False))
    return True


async def _open_camera_patiently(camera):
    """Open the camera, retrying on FACE_CAMERA_RETRY_S until it works.

    Returns None only when retrying is switched off. Says something on the
    first failure and then about once a minute — a line every ten seconds
    for a camera that is unplugged all weekend is noise, silence is the bug
    this replaces.
    """
    attempt = 0
    while True:
        cap = await asyncio.to_thread(camera.open_camera)
        if cap is not None:
            if attempt:
                logger.info("camera is back after %d attempt(s)", attempt)
            return cap
        if settings.face_camera_retry_s <= 0:
            logger.warning("camera greeting off — no camera opened "
                           "(try: python scripts/watch_camera.py --list)")
            return None
        if attempt % 6 == 0:
            logger.warning("no camera opened — retrying every %.0fs "
                           "(try: python scripts/watch_camera.py --list)",
                           settings.face_camera_retry_s)
        attempt += 1
        await asyncio.sleep(settings.face_camera_retry_s)


async def run() -> None:
    """Watch until cancelled. Never raises into the server that started it."""
    from app import camera, facewatch, wake
    from app import session as session_module

    gallery_path = Path(settings.face_gallery).expanduser()
    baseline = _gallery_signature(gallery_path)     # before the load; see _GalleryFollower
    watcher = facewatch.Watcher.from_settings()
    if watcher is None:
        # from_settings has already said which piece is missing. No camera is
        # opened at all: a lens held open to feed a decision nobody can make
        # is a light on the front of the machine for no reason.
        logger.info("camera greeting off — nothing to recognise with")
        return

    _say_who_is_missing(watcher)

    cap = await _open_camera_patiently(camera)
    if cap is None:
        return

    interval = 1.0 / max(settings.face_fps, 0.5)
    empty = 0
    last_ring = 0.0
    last_ask = 0.0
    turn_task: asyncio.Task | None = None
    follow = _GalleryFollower(gallery_path, baseline)
    was_held = False
    logger.info("camera greeting on — %.1f fps, threshold %.2f, cooldown "
                "%.0fs. The enrolment station cannot open the same camera "
                "while this is running.", settings.face_fps, watcher.threshold,
                watcher.cooldown_s)
    try:
        while True:
            # Between frames: no `see()` is running while this coroutine is
            # here, which is what makes swapping the gallery safe. Followed
            # during a pause too — that is when a scan saves.
            await follow.poll(watcher)
            held = paused()
            if held != was_held:
                was_held = held
                if held:
                    logger.info("camera greeting paused (face scan) — frames "
                                "still read, nobody recognised, rung or greeted")
                else:
                    logger.info("camera greeting resumed after the pause")

            ok, frame = await asyncio.to_thread(cap.read)
            if not ok or frame is None:
                empty += 1
                if empty >= MAX_EMPTY_READS:
                    # Not the end. A camera that stops delivering is a
                    # USB hiccup or somebody else holding the device, and
                    # both end on their own — the loop used to end with
                    # them, silently, until the next restart. Release the
                    # dead handle and try again on a timer.
                    with contextlib.suppress(Exception):
                        cap.release()
                    if settings.face_camera_retry_s <= 0:
                        logger.warning("camera stopped returning frames — "
                                       "greeting off until the server restarts "
                                       "(FACE_CAMERA_RETRY_S=0)")
                        return
                    logger.warning("camera stopped returning frames — "
                                   "reopening every %.0fs", settings.face_camera_retry_s)
                    cap = await _open_camera_patiently(camera)
                    if cap is None:
                        return
                    empty = 0
                    continue
                await asyncio.sleep(interval)
                continue
            empty = 0

            if held:
                # The frame is read and dropped rather than the camera
                # closed: reopening is the slow liveness-probing open, and
                # reading is what keeps the dead-camera watch above honest.
                # Skipped here, before `see()`, so the pre-ring below is
                # skipped with it — `someone_at` from a frame just before
                # the pause would otherwise still ring for the person
                # being scanned.
                _face_debug({"verdict": "พักการทัก (กำลังสแกนหน้า)"})
                await asyncio.sleep(interval)
                continue

            # Recognition off the event loop: it is 100-200ms of ONNX per
            # frame with somebody in front of the lens, and this process is
            # also carrying a live audio session. The measured cost at
            # FACE_THREADS=2 is 18% of the machine with a face in frame,
            # 8% without.
            sighting = await asyncio.to_thread(_see, watcher, frame, _zoom_on())
            _log_watch_events(watcher)
            if paused():
                # The pause began while `see()` was in its thread. Its
                # verdict is about somebody who is now being scanned.
                _face_debug({"verdict": "พักการทัก (กำลังสแกนหน้า)"})
                await asyncio.sleep(interval)
                continue
            # A closer look and a turn are about the face just judged — not
            # during a scan (above), and the turn not into a conversation.
            want = getattr(watcher, "look_closer", None)
            if want and _zoom_on():
                last_ask = _ask_closer(want, last_ask, time.monotonic())
            if (_wants_turn(want) and (turn_task is None or turn_task.done())
                    and not _in_conversation(session_module._active)):
                turn_task = _start_turn(watcher, want)
            # getattr: the panel is diagnostic, and a watcher double without
            # the field (tests) must not take the greeting loop down.
            debug_status = dict(getattr(watcher, "status", None) or {})

            # Ring the standby browser the moment somebody is close enough,
            # not after they are confirmed: confirming takes ~1.2s and the
            # Gemini dial 2-3s, and run in sequence the robot greeted
            # people's backs. A ring with a session already up costs
            # nothing (no summon fires), and a face that leaves before
            # confirming costs one aborted dial — cheaper than a greeting
            # nobody stayed to hear.
            now_mono = time.monotonic()
            if (watcher.someone_at is not None
                    and now_mono - watcher.someone_at < 1.0
                    and session_module._active is None
                    and now_mono - last_ring > 15.0):
                last_ring = now_mono
                rang = await wake.summon(reason="face_approach")
                if rang:
                    logger.info("somebody close — pre-ringing %d standby "
                                "browser(s) while confirming", rang)

            if sighting is not None and sighting.kind == "stranger" \
                    and not settings.face_greet_strangers:
                # Read after `see`, not before: the cooldown it just set is
                # what stops the next thirty frames asking the same
                # question, and skipping the call would also skip that.
                logger.info("face: stranger (%.3f) — not greeting strangers",
                            sighting.score)
                debug_status["verdict"] = "เห็นคนแปลกหน้า — ตั้งค่าไว้ไม่ทักคนแปลกหน้า"
                sighting = None
            if sighting is not None and _in_conversation(session_module._active):
                # A face wakes the line up; a face does not cut into a
                # conversation. Strangers first (2026-09-01, one session):
                # three "strangers" at 0.355 / 0.469 / 0.478 — every one of
                # them one of the two people already talking to Emma, caught
                # turning their head (the threshold is 0.50). Each landed
                # as a user turn mid-sentence and Emma broke off to say
                # "สวัสดีค่ะ มีอะไรให้ช่วยไหมคะ" to nobody. Known faces kept
                # greeting mid-conversation for a month on the theory that a
                # name is worth hearing — until 2026-09-30, when the log
                # showed "พีท" re-greeted 12s into his own conversation
                # (11:07:15 → 11:07:27) and the owner ruled: ประโยคทักทาย
                # ไม่ให้พูดแทรก. A greeting is for the start of a
                # conversation; mid-sentence it is an interruption whoever
                # it names. The cooldown `see()` just set still stands, so
                # the same person is not re-asked about for FACE_COOLDOWN_S.
                logger.info("face: %s %s (%.3f) during a live session — "
                            "not interrupting", sighting.kind,
                            sighting.name or "-", sighting.score)
                turnlog.record("face_seen", kind=sighting.kind,
                               name=sighting.name,
                               score=round(sighting.score, 3),
                               group=sighting.group or "",
                               greeted=False, why="live_session",
                               **_track_fields(sighting))
                debug_status["verdict"] = ("เห็น%s แต่กำลังคุยอยู่ — ไม่พูดแทรก"
                                           % (sighting.name or "คนแปลกหน้า"))
                sighting = None
            _face_debug(debug_status)
            if sighting is not None:
                logger.info("face: %s %s (%.3f)", sighting.kind,
                            sighting.name or "-", sighting.score)
                await _greet(sighting)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        # A camera loop that dies must not take the showroom with it, and
        # must not die quietly either.
        logger.exception("camera greeting stopped")
    finally:
        if turn_task is not None and not turn_task.done():
            turn_task.cancel()
        with contextlib.suppress(Exception):
            cap.release()


def start() -> None:
    """Begin watching, if this machine is meant to.

    Called from the server's startup hook. Not from `Watcher.__init__` and
    not on the first frame: starting hardware from inside something that
    looks like a constructor is how the deck warm-up ended up inside the
    path a waiting conversation ran through.
    """
    global _task
    if not settings.face_enabled or _task is not None:
        return
    _task = asyncio.create_task(run())


async def stop() -> None:
    global _task
    if _task is None:
        return
    task, _task = _task, None
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
