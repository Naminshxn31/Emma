"""
Fan-out to the slide display(s).

The display is a separate fullscreen page — on the real robot it's the
18.5" chest screen, in development it's a second browser window. It's
deliberately its own WebSocket rather than part of the conversation socket:

- more than one screen can mirror the same presentation,
- opening or closing a display never disturbs an in-progress conversation,
- the display reconnects on its own without touching the voice session.

Displays are passive. They receive the current slide and render it; nothing
is ever sent back. That keeps a screen in the lobby from being able to drive
the robot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import WebSocket

logger = logging.getLogger("condo_voice.display")

_clients: set[WebSocket] = set()
_lock = asyncio.Lock()


async def register(ws: WebSocket) -> None:
    async with _lock:
        _clients.add(ws)
    logger.info("display connected (%d total)", len(_clients))


async def unregister(ws: WebSocket) -> None:
    async with _lock:
        _clients.discard(ws)
    logger.info("display disconnected (%d left)", len(_clients))


def client_count() -> int:
    return len(_clients)


async def broadcast(payload: dict) -> None:
    """Push a state change to every display, dropping any that have gone."""
    if not _clients:
        return
    message = json.dumps(payload, ensure_ascii=False)
    async with _lock:
        targets = list(_clients)

    dead = []
    for ws in targets:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    if dead:
        async with _lock:
            for ws in dead:
                _clients.discard(ws)


#: How far the conversation's audio is queued ahead of what the guest has
#: actually heard, in milliseconds, as last reported by the browser playing
#: it. Zero when nobody is in a call, which is also what the tests see.
#:
#: This exists because the model produces a whole narration turn several
#: times faster than it takes to say, and the browser queues that audio
#: back-to-back. By the time `next_slide` runs, the model has finished
#: *generating* the sentences about the slide it is leaving — but the guest
#: is still near the start of hearing them, five to ten seconds behind.
#: Switching the screens at that moment puts them a paragraph ahead of the
#: conversation: the robot describes the airports while the map of Pattaya
#: is already up. Nothing is late; the picture is early.
_audio_lead_ms: float = 0.0

#: Latest-wins: during a tour the model can advance again while a previous
#: change is still waiting, and only the newest one is worth showing.
_reveal_seq = 0
_reveal_task: asyncio.Task | None = None


#: Monotonic time by which the queued speech will have finished playing.
_audio_until: float = 0.0


def set_audio_lead(ms: float) -> None:
    """Told by the browser holding the audio queue."""
    global _audio_lead_ms, _audio_until
    _audio_lead_ms = max(0.0, min(float(ms or 0.0), 30_000.0))
    _audio_until = time.monotonic() + _audio_lead_ms / 1000


def remaining_lead() -> float:
    """Seconds of speech still sitting in the browser's queue, unheard.

    Anything that changes what the guest is *looking at* has to consult this
    first. The model finishes generating a narration several times faster
    than it takes to say, so at the moment a tool runs, "now" on the server
    is some seconds ahead of "now" at the guest's ear.
    """
    return max(0.0, _audio_until - time.monotonic())


async def wait_until_heard(max_wait: float = 25.0, then_pause: float = 0.0) -> float:
    """Block until the guest has heard everything queued. Returns seconds waited.

    Deliberately blocking, and the one place in this codebase where that is
    correct. The model produces a narration turn far faster than it takes to
    say; without this it chains narrate -> next_slide -> narrate through all
    59 pages in about a minute, queueing eight minutes of speech that nobody
    can interrupt out of. Waiting here idles the model while the guest
    listens, which is exactly what a person presenting does.

    Re-checks as it goes: a barge-in empties the queue and sets the deadline
    back to now, so an interrupted tour stops waiting immediately.
    """
    started = time.monotonic()
    while True:
        remaining = _audio_until - time.monotonic()
        if remaining <= 0.05:
            break
        if time.monotonic() - started > max_wait:
            logger.info("stopped waiting for audio after %.0fs", max_wait)
            break
        await asyncio.sleep(min(remaining, 0.25))
    if then_pause > 0:
        await asyncio.sleep(then_pause)
    return time.monotonic() - started


async def show(slide: dict | None) -> None:
    """A tool changed the slide. Put it up when the guest can hear it."""
    global _reveal_seq, _reveal_task

    _reveal_seq += 1
    if _audio_lead_ms < 200:
        await _reveal(slide)
        return

    seq = _reveal_seq
    _reveal_task = asyncio.create_task(_reveal_later(slide, seq, _audio_lead_ms / 1000))


async def _reveal_later(slide: dict | None, seq: int, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if seq == _reveal_seq:          # not already superseded
        await _reveal(slide)


async def _reveal(slide: dict | None) -> None:
    await broadcast({"type": "slide", "slide": slide})
    # Opt-in mirror onto a real Canva window — no-op unless CANVA_URL is set.
    # Imported here (not at module load) so a machine without `playwright`
    # installed never has to know this exists.
    from app.tools import canva_display

    await canva_display.goto(slide.get("id") if slide else None)
