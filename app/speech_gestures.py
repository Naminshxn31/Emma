"""Emma moves her arms while she talks: right, then left, then right...

Owner, 15 Oct: "เวลาพูดให้หุ่นทำท่าเหมือนอธิบาย ยกมือซ้าย/ขวา" → "ให้ขยับซ้ายขวา
สลับกันตอนคุย". The vendor's own speaking gesture (group 21) and its navigation
one (22) move only the head (owner, watching them 14 Oct), so the poses are
ours: `raise_right_arm` / `raise_left_arm` recorded in the app's pose studio
from the 15 Oct channel sweep (docs/robot-arm-command-map-2026-09-16.md).

What this module decides, and nothing else: WHEN to ask for a gesture and
WHICH side. The server still holds no pulse and no channel (the robot_body
rule): it names an id over the same `robot_face_link` request move_body uses,
and the app's gates still decide -- its "สั่งท่าทางด้วยเสียง" switch, the stop
latch, walking, busy, its cooldown and its 6-per-minute cap, the voice-channel
allow-list and the 1300-1700 clamps. An id the robot has no pose for
(`needs_mapping`) is never sent.

Why not through move_body and its heard guard: the guard proves a GUEST asked
for a motion (a misheard "ยกเลิก" must not lift an arm). Nobody asks for these;
the server times them to her own voice, from a vocabulary of two recorded
poses. The server's keys (`robot_body._server_gate`) and the /arm desk's
disarm (`robot_arm.armed`) still apply.

Timing: one gesture only while at least SPEECH_GESTURE_MIN_LEFT_S of her voice
is still to play (`session._play_until`, the robot face's only playback clock),
so a short reply gets none, and at most one start every SPEECH_GESTURE_GAP_S.
A barge-in stops further gestures; the one in the air finishes and rests on
its own -- a stop would latch the arm for 60 s (BodyGestures.STOP_LATCH_MS).

Two app builds (owner 15 Oct, after the first: "ท่ายังช้าแล้วดูไม่เป็นธรรมชาติ"):
the first plays every pose as a voice gesture -- group 99 after each, then a
3 s cooldown, so ~11 s apart. A build whose body_caps says `"speech": true`
takes `style: "speech"`: no rest between beats (one rest after the last),
no cooldown, its own cap -- paced by the BEAT knobs instead.

Off by default (SPEECH_GESTURES): the sales room PC and the desk page never
reach any of this.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.config import settings

logger = logging.getLogger("condo_voice.speech_gestures")

#: The order the sides alternate in. Robot's own right first: the
#: "raise_right_arm" id is the robot's right arm (ซ้าย/ขวาคือของตัวหุ่นเอง).
SIDES = ("raise_right_arm", "raise_left_arm")

#: App refusals that mean "the arm is still finishing / resting" (its 3 s
#: cooldown after a gesture's end, or a gesture still playing): ask again
#: after RETRY_AFTER_S instead of a whole SPEECH_GESTURE_GAP_S.
RETRY_SOON = ("cooldown", "busy")
RETRY_AFTER_S = 1.5

#: The latest gesture this module asked for and the robot accepted, for
#: `owns()` (focus must not read her own explaining hand as "the robot is
#: moving, listen to everybody").
LAST: dict = {"action": None, "at": None}


def reset_state() -> None:
    LAST.update(action=None, at=None)


#: A `started` this soon around our accepted request is ours; a guest's
#: "ยกแขนขวา" for the same id much later is not.
OWN_WINDOW_S = (-1.0, 10.0)


def owns(action: str | None, started_at: float | None) -> bool:
    """The body_status `started` (robot_body.STATE) is one of ours: same id,
    reported around the moment we asked. Never raises."""
    try:
        at = LAST.get("at")
        if action is None or action != LAST.get("action"):
            return False
        if not isinstance(at, (int, float)) or not isinstance(started_at, (int, float)):
            return False
        return OWN_WINDOW_S[0] <= started_at - at <= OWN_WINDOW_S[1]
    except Exception:
        return False


class _State:
    __slots__ = ("task", "side", "last", "silent_reason")

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.side = 0
        self.last = float("-inf")
        self.silent_reason: str | None = None


def _state(session) -> _State:
    st = getattr(session, "_speech_gestures", None)
    if st is None:
        st = _State()
        try:
            session._speech_gestures = st
        except Exception:
            pass
    return st


def _record(**fields) -> None:
    try:
        from app import turnlog

        turnlog.record("speech_gesture", **fields)
    except Exception:
        logger.debug("turnlog failed", exc_info=True)


def _left_s(session) -> float:
    return float(getattr(session, "_play_until", 0.0) or 0.0) - time.monotonic()


def _why_not(session, action: str) -> str | None:
    """Server-side reasons not to even ask. None = ask the robot."""
    from app import robot_arm, robot_body

    if not robot_body._server_gate(action):
        return "server_disabled"
    if not robot_arm.armed():
        return "latched"
    avail = robot_body.available(session)
    if avail is None:
        return "no_caps"
    if action not in avail:
        return "not_playable"
    return None


def on_audio(session) -> None:
    """A chunk of her reply went to the page. Cheap: starts the pacing task
    for this reply if none runs."""
    if not settings.speech_gestures:
        return
    try:
        from app import robot_face_link

        if not robot_face_link.is_robot_face(session):
            return
        st = _state(session)
        if st.task is not None and not st.task.done():
            return
        st.task = asyncio.get_running_loop().create_task(_run(session, st))
    except Exception:
        logger.debug("speech gesture start failed", exc_info=True)


def on_interrupted(session) -> None:
    """Barge-in: no further gestures for that reply."""
    st = getattr(session, "_speech_gestures", None)
    if st is not None and st.task is not None and not st.task.done():
        st.task.cancel()


on_closed = on_interrupted


def _beats(session) -> bool:
    """The robot plays `style: "speech"` (body_caps from this call)."""
    from app import robot_face_link

    caps = robot_face_link.caps(session, "body") or {}
    return caps.get("speech") is True


async def _run(session, st: _State) -> None:
    from app import robot_face_link

    await asyncio.sleep(max(0.0, settings.speech_gesture_first_s))
    while True:
        beats = _beats(session)
        gap = settings.speech_gesture_beat_gap_s if beats else settings.speech_gesture_gap_s
        min_left = (settings.speech_gesture_beat_min_left_s if beats
                    else settings.speech_gesture_min_left_s)
        if _left_s(session) < min_left:
            return
        wait = st.last + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(min(wait, 0.5))
            continue
        action = SIDES[st.side % len(SIDES)]
        why = _why_not(session, action)
        if why is not None:
            # Once per reason, not per reply: a switch that is off would
            # otherwise write a row for every answer of the day.
            if why != st.silent_reason:
                _record(action=action, sent=False, reason=why)
                st.silent_reason = why
            return
        st.silent_reason = None
        left = round(_left_s(session), 1)
        st.last = time.monotonic()
        payload = {"action": action, "style": "speech"} if beats else {"action": action}
        ack = await robot_face_link.request(session, "body", payload,
                                            settings.robot_body_ack_timeout_s)
        accepted = ack.get("accepted") is True
        reason = None if accepted else ack.get("reason")
        if accepted:
            LAST.update(action=action, at=time.time())
            st.side += 1
        elif reason in RETRY_SOON:
            # Robot 15 Oct: the answer's first gesture came 3 s after the
            # greeting's ended -- "cooldown" -- and the full gap then cost
            # ten seconds of the answer. The arm is free again in moments.
            st.last = time.monotonic() - gap + min(gap, RETRY_AFTER_S)
        _record(action=action, sent=True, accepted=accepted, reason=reason, left_s=left,
                beats=beats)
