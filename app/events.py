"""
The robot speaking when nobody asked it a question.

Almost everything here starts with a guest: they talk, the model answers, a
tool runs. This module is the other direction — something happened in the
room, and the robot has to bring it up itself. Today that is the tour pushing
itself along, the Canva window being moved by hand, and the robot finishing a
walk. Next it is a camera recognising a face, an appointment coming due, a
door opening.

There is exactly one way to do this: send text into the model's conversation,
which the provider delivers as if the guest had spoken. That single fact is
what makes this worth a module of its own, because it has three traps and
each of the four existing callers fell into a different one.

**Sending during playback destroys the answer you were waiting for.**
`turn_complete` means the model stopped *generating*; the guest may still
have twenty seconds of speech queued in the browser. Text arriving then is a
barge-in, and Gemini responds by cancelling its own generation and dropping
the audio it hadn't sent. Measured on the tour: of 81 slides, 21 delivered
under 60% of their script, one as little as 16%. The mechanism built to keep
the tour moving was the thing cutting it off. `wait_until_heard` came out of
that, and then only the tour got it — `robot_arrived` and `follow_canva` kept
firing straight into whatever was being said.

**What you waited for may not be true any more.** Waiting is not free: it can
take forty-five seconds, and the situation that justified the announcement
can dissolve while you sit in it. The tour learned this first — the model
usually calls `next_slide` by itself, so a nudge that was fair when scheduled
would skip a slide by the time it landed. Hence `still_relevant`, re-checked
*after* the wait rather than before it.

**Two of these at once is worse than either.** With one event source there was
nothing to collide with. With a robot, a camera and a clock there will be, and
two texts arriving together is the barge-in bug again with the robot as its
own interrupter. One at a time, in order.

And the session can be replaced while an announcement waits — a guest leaves,
someone opens a new tab, `handle_connection` hands the robot over. Telling the
*next* person that the robot has arrived somewhere on behalf of the last one
is a specific and quite bad way to fail, so the session is captured up front
and checked again at the end.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app import turnlog

logger = logging.getLogger("condo_voice.events")

#: One announcement at a time. Created at import, like `display._lock`, so
#: `tests/conftest.py::_fresh_async_state` has to reset it too — the suite
#: runs an event loop per test, and a lock abandoned by a closed loop stays
#: locked forever.
_lock = asyncio.Lock()

#: Long enough for a full slide script (~30s at the top end), short enough
#: that a stuck audio clock cannot park an announcement indefinitely.
DEFAULT_MAX_WAIT = 45.0

#: A beat after the last word, so the announcement doesn't tread on the tail
#: of the sentence it waited for.
DEFAULT_PAUSE = 0.4


#: How long a summoned browser gets to dial before the announcement gives
#: up this round. Covers mic acquisition + the Gemini connect (~2-3s
#: measured) with margin; the reminder watcher retries the next tick anyway.
SUMMON_WAIT_S = 12.0

#: How long the next announcement waits for the model to finish answering
#: the previous one. A greeting is a few seconds; a turn that never
#: completes (transport hiccup) must not park every later announcement.
TURN_WAIT_S = 15.0


async def announce(
    text: str,
    *,
    source: str,
    still_relevant: Callable[[], bool] | None = None,
    max_wait: float = DEFAULT_MAX_WAIT,
    then_pause: float = DEFAULT_PAUSE,
    summon: bool = False,
    arm_greeting: bool = False,
) -> bool:
    """Say something the guest didn't ask for, once they can hear it.

    `source` names the thing that happened ("robot_arrived", "tour_nudge").
    It goes in the turn log, because "why did the robot suddenly start
    talking" is a question someone will ask about a recording, and without
    this the log shows a guest turn that no guest took.

    `summon=True` is for events that must not die with the silence: in
    hybrid mode the line is usually *parked* when an alarm rings, so there
    is no session to speak into — but there is a standby browser listening
    for its name, and the server can say it: `wake.summon()` pushes the same
    "wake" the keyword would have, the browser dials, and the announcement
    is delivered on the fresh line. Events that only matter mid-conversation
    (a tour nudge) keep the default and drop.

    Returns whether the text actually reached the model.
    """
    import asyncio

    from app import display
    from app import session as session_module

    # Resolved before the wait, not after: this announcement belongs to the
    # conversation that was happening when the event fired. If that session
    # is handed over while we wait, the right move is to drop it, not to
    # deliver it to whoever is standing there now.
    session = session_module._active
    if (session is None or session.provider is None) and summon:
        from app import wake

        rang = await wake.summon(reason=source)
        if not rang:
            # Said out loud because the alternative is one flat line that
            # covers two completely different problems. "Nobody was on
            # standby" is a page that is closed, or in a call, or that gave
            # up its socket; "summoned 1, nobody dialled" is a page that
            # heard the bell and could not connect. The first real camera
            # greeting was lost to the first of those and the log could not
            # tell them apart.
            # Zero rings is not always zero answers: the greeter pre-rings
            # the moment a face is close enough, and a browser already
            # dialling has dropped its standby socket by the time this
            # announcement rings again — so the poll below runs either way,
            # and only the log line differs.
            logger.info("no standby browser rang for %s — either no page is "
                        "open at the sleep screen, or one is already "
                        "dialling from an earlier ring", source)
        else:
            logger.info("no session for %s — summoned %d standby browser(s)",
                        source, rang)
        deadline = SUMMON_WAIT_S
        while deadline > 0:
            await asyncio.sleep(0.25)
            deadline -= 0.25
            session = session_module._active
            if session is not None and session.provider is not None:
                break
    if session is None or session.provider is None:
        logger.info("no live session — dropping the %s announcement", source)
        turnlog.record("announce", source=source, sent=False, reason="no session")
        return False

    async with _lock:
        # First the model's turn, then the speakers. The audio queue is
        # empty between "text sent" and "first chunk back", so waiting on
        # it alone let the second announcement land while the model was
        # still generating the first — a barge-in by the robot's own hand
        # (on screen 2026-09-01: the greeting cut mid-word, ถูกพูดแทรก).
        idle = getattr(session, "turn_idle", None)
        if idle is not None and not idle.is_set():
            try:
                await asyncio.wait_for(idle.wait(), timeout=TURN_WAIT_S)
            except asyncio.TimeoutError:
                logger.info("previous announcement's turn never completed in %.0fs — "
                            "sending %s anyway", TURN_WAIT_S, source)
        waited = await display.wait_until_heard(
            max_wait=max_wait, then_pause=then_pause
        )

        if session_module._active is not session:
            logger.info(
                "session changed while waiting %.1fs — dropping the %s announcement",
                waited, source,
            )
            turnlog.record("announce", source=source, sent=False,
                           reason="session changed", waited=round(waited, 1))
            return False

        if still_relevant is not None and not still_relevant():
            logger.info(
                "%s sorted itself out after %.1fs — nothing to announce",
                source, waited,
            )
            turnlog.record("announce", source=source, sent=False,
                           reason="no longer relevant", waited=round(waited, 1))
            return False

        if idle is not None:
            idle.clear()                  # the model owes a turn for this text
        try:
            await session.provider.send_text(text)
        except Exception:
            if idle is not None:
                idle.set()
            logger.exception("could not deliver the %s announcement", source)
            turnlog.record("announce", source=source, sent=False,
                           reason="send failed", waited=round(waited, 1))
            return False

        # Start after the text is accepted, so a disconnected provider never
        # produces a silent gesture. Failure is deliberately non-fatal:
        # robot_arm.greet() preserves every hardware gate and the voice is
        # already on its way while the serial request is attempted.
        if arm_greeting:
            from app import robot_arm

            await robot_arm.greet()

    logger.info("announced %s after waiting %.1fs for audio", source, waited)
    turnlog.record("announce", source=source, sent=True, waited=round(waited, 1))
    return True
