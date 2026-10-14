"""The standing line: a Gemini session held open so a greeting costs a turn,
not a dial.

Owner, 2026-09-30 ("เราสามารถต่อสายกับ Gemini ตลอดเลยได้ไหม แบบค้างไว้
เวลาทักจะได้ไว" → "ทำเลย"). Before this, the fastest greeting still paid a
2-3s Gemini dial after the face confirmed; the pre-ring hid part of it and
never all of it. Now, whenever no session is live and a standby browser is
listening for its bell, this loop rings it — the same bell the wake word and
the greeter's pre-ring use — and the page dials a *summoned* session that
then just sits there:

- summoned = no opening line spoken into an empty room (`greeting=None`),
  and the greeter still counts it as a line held open for a greeting.
- `_close_when_nobody_is_there` holds a VIRGIN standing line (ungreeted,
  nobody ever heard) open indefinitely instead of abandoning it at 30s; the
  provider's own go_away/resume machinery survives Gemini's rotations.
- The moment the line is used — greeted, or somebody talks — it stops being
  virgin and dies by the ordinary idle rules, ON PURPOSE: one visitor's
  words must not sit in the model's context when the next visitor walks up
  (the TRANSCRIPT_KEEP_MIN concern, one layer down). This loop then rings a
  fresh line within seconds.

An idle standing line costs ~nothing only because local VAD never streams
silence upstream; the config comment says so where STANDING_LINE is set.
MULTI_SESSION has no single line to hold (`_active` is never set there), so
the switch is refused loudly rather than half-working.
"""
from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger("condo_voice.standing")

#: How often the loop looks for a missing line. The bell itself is cheap;
#: the pause after a ring gives the page time to dial before we conclude
#: nobody answered.
CHECK_S = 5.0
DIAL_GRACE_S = 10.0

_task: asyncio.Task | None = None


def start() -> None:
    """Begin holding the line, if this machine asked to.

    Called from the server's startup hook; checks its own switches, the same
    shape as `greeter.start()`.
    """
    global _task
    if not settings.standing_line:
        return
    if settings.multi_session:
        logger.warning("STANDING_LINE ignored — MULTI_SESSION has no single "
                       "line to hold")
        return
    _task = asyncio.create_task(run())
    logger.info("standing line on — the standby browser is rung whenever no "
                "session is live")


def stop() -> None:
    """Tests: cancel the loop and forget it."""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


async def run() -> None:
    """Ring until cancelled. Never raises into the server that started it."""
    from app import session as session_module
    from app import wake

    said_nobody = False
    try:
        while True:
            await asyncio.sleep(CHECK_S)
            if session_module._active is not None:
                said_nobody = False
                continue
            rang = await wake.summon(reason="standing_line")
            if rang:
                said_nobody = False
                logger.info("standing line: rang %d standby browser(s)", rang)
                # Let the dial land before the next look — ringing again
                # mid-dial is how a page ends up superseding itself.
                await asyncio.sleep(DIAL_GRACE_S)
            elif not said_nobody:
                # Once, not every five seconds: a robot page that is closed
                # all evening would otherwise write this line forever.
                said_nobody = True
                if wake.held_only():
                    logger.info("standing line: the standby page is muted — "
                                "not ringing it open (the camera still can)")
                else:
                    logger.info("standing line: nobody on standby to ring — "
                                "retrying quietly")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("standing line stopped")
