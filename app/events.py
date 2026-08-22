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


async def announce(
    text: str,
    *,
    source: str,
    still_relevant: Callable[[], bool] | None = None,
    max_wait: float = DEFAULT_MAX_WAIT,
    then_pause: float = DEFAULT_PAUSE,
) -> bool:
    """Say something the guest didn't ask for, once they can hear it.

    `source` names the thing that happened ("robot_arrived", "tour_nudge").
    It goes in the turn log, because "why did the robot suddenly start
    talking" is a question someone will ask about a recording, and without
    this the log shows a guest turn that no guest took.

    Returns whether the text actually reached the model.
    """
    from app import display
    from app import session as session_module

    # Resolved before the wait, not after: this announcement belongs to the
    # conversation that was happening when the event fired. If that session
    # is handed over while we wait, the right move is to drop it, not to
    # deliver it to whoever is standing there now.
    session = session_module._active
    if session is None or session.provider is None:
        logger.info("no live session — dropping the %s announcement", source)
        turnlog.record("announce", source=source, sent=False, reason="no session")
        return False

    async with _lock:
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

        try:
            await session.provider.send_text(text)
        except Exception:
            logger.exception("could not deliver the %s announcement", source)
            turnlog.record("announce", source=source, sent=False,
                           reason="send failed", waited=round(waited, 1))
            return False

    logger.info("announced %s after waiting %.1fs for audio", source, waited)
    turnlog.record("announce", source=source, sent=True, waited=round(waited, 1))
    return True
