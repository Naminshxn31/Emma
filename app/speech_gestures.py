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
    """How long she still speaks. A recorded line (the whole ceremony is recorded lines) is pushed only RECORDED_LEAD_S
    ahead of her voice, so the playback clock never read more than ~2 s left and no gesture ever passed MIN_LEFT --
    rehearsal 25 Nov 01:14, s10_l05 18.7 s standing, not one arm (owner: "การ์ดสองตอนคุยต้องขยับมือด้วยนะ"). The
    line's own length from its first word says the rest."""
    now = time.monotonic()
    left = float(getattr(session, "_play_until", 0.0) or 0.0) - now
    try:
        rec = getattr(getattr(session, "provider", None), "_recorded", None)
        if isinstance(rec, dict) and rec.get("pushing") and not rec.get("cut"):
            first = (rec.get("report") or {}).get("first_at")
            if isinstance(first, (int, float)):
                left = max(left, float(first) + float(rec.get("seconds") or 0.0) - now)
    except Exception:  # noqa: BLE001 -- the playback clock alone, as before
        pass
    return left


def _why_not(session, action: str) -> str | None:
    """Server-side reasons not to even ask. None = ask the robot."""
    from app import robot_arm, robot_body

    if not robot_body._server_gate(action):
        return "server_disabled"
    try:
        from app import event_pageant

        if event_pageant.claims_arm():
            # the debut's pageant has the arm, or is about to: a beat held out when it takes the arm
            # would stay out under it until group 99 (review 9 Nov)
            return "pageant"
    except Exception:  # noqa: BLE001
        pass
    try:
        from app import event_tour

        if event_tour.holds_arm():
            # card 1's tour (owner 23 Nov): its handshakes have the arm between its walks, each waited out to
            # the app's own `done` -- a beat in between is refused busy there, or holds the arm out over the
            # next walk's rest
            return "tour"
    except Exception:  # noqa: BLE001
        pass
    try:
        from app import event_box

        if event_box.holds(session):
            # in the box (owner 23 Nov: "เดินออกมาก่อน จึงค่อยยกมือ"): a beat of a line said in the box -- the recorded
            # one too, ~0.8 s into it -- would swing an arm into its wall (review 23 Nov)
            return "in_box"
    except Exception:  # noqa: BLE001 -- not knowing: keep the arm down
        return "in_box"
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
