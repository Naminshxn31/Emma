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
    if payload.get("type") == "slide" and payload.get("slide") is not None:
        # Protect the transport itself, not only show()/_reveal(): callers
        # using broadcast directly must not bypass source resolution.
        from app.tools import slides

        safe = slides.display_payload(payload["slide"])
        if safe is None:
            return
        payload = {"type": "slide", "slide": safe}
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


def _machine_owned() -> bool:
    """Whether some session owns this machine's screens and audio clock.

    Everything in this module is state for *one* physical surface: one
    subtitle queue, one `_audio_until`. Under MULTI_SESSION there are N
    concurrent conversations and no robot — N sessions feeding one subtitle
    queue would interleave strangers' conversations on whatever /display
    happens to be open (the transcript-privacy rule, broken structurally),
    and N browsers overwriting one audio clock makes `remaining_lead()`
    garbage. So while nobody owns the machine, the writers accept nothing.
    Decided here, once, instead of at the eight call sites in session.py.
    """
    from app.config import settings

    return not settings.multi_session


def set_audio_lead(ms: float) -> None:
    """Told by the browser holding the audio queue."""
    global _audio_lead_ms, _audio_until
    if not _machine_owned():
        return
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

    if not _machine_owned():
        return
    _reveal_seq += 1
    if _audio_lead_ms < 200:
        await _reveal(slide)
        return

    seq = _reveal_seq
    _reveal_task = asyncio.create_task(_reveal_later(slide, seq, _audio_lead_ms / 1000))


def cancel_pending_reveal() -> None:
    """A queued picture belongs to the visitor whose voice queued it."""
    global _reveal_seq, _reveal_task
    _reveal_seq += 1
    if _reveal_task is not None and not _reveal_task.done():
        _reveal_task.cancel()
    _reveal_task = None


async def _reveal_later(slide: dict | None, seq: int, delay: float) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if seq == _reveal_seq:          # not already superseded
        await _reveal(slide)


async def _reveal(slide: dict | None) -> None:
    if not _machine_owned():
        return
    if slide is not None:
        # This is the final UI/Canva egress, including delayed reveals. Do
        # not trust a dict handed directly to display.show(), or a public
        # result cached before approval/project scope changed.
        from app.tools import slides

        slide = slides.display_payload(slide)
        if slide is None:
            logger.warning("blocked an unscoped or revoked slide at display egress")
            return
    await broadcast({"type": "slide", "slide": slide})
    # Opt-in mirror onto a real Canva window — no-op unless CANVA_URL is set.
    # Imported here (not at module load) so a machine without `playwright`
    # installed never has to know this exists.
    from app.tools import canva_display

    await canva_display.goto(slide.get("id") if slide else None)


# ==================== the robot's own screen ====================
#
# The chest screen is not a monitor. A laptop showing the transcript is a
# staff tool and may run as far ahead of the voice as it likes; this one is
# at chest height in front of the guest, so text arriving early means they
# read the answer before Emma says it — and read the end of a sentence she
# has not started.
#
# That is the same bug the rest of this module exists for, wearing text
# instead of pictures, so it uses the same clock. Nothing here consults
# `time` about anything except `_audio_until`.
#
# Only Emma's side is ever sent. What the guest said stays off this screen
# on purpose: transcription is wrong often enough to be embarrassing in
# public ("bit fire" for "ปิดไฟ" is in the README), and the prompt has her
# read phone numbers back to confirm them — which would put a stranger's
# number on a screen in a public room. The guest gets an indicator instead.

#: Chunks of Emma's speech waiting for their audio to be reached, as
#: (monotonic time to show it, text).
_sub_queue: list[tuple[float, str]] = []
#: What is on the screen right now.
_sub_shown: str = ""
#: `_audio_until` as it stood before the newest chunk arrived — which is when
#: the audio queued ahead of that chunk runs out, and therefore when that
#: chunk starts being spoken. Each chunk is released at the previous chunk's
#: finishing line, so the subtitle walks the voice instead of the generator.
_sub_prev_until: float = 0.0
_sub_task: asyncio.Task | None = None
#: Set at the end of a turn; the next chunk starts a fresh line rather than
#: growing one paragraph for the whole conversation.
_sub_restart: bool = True


async def say(text: str) -> None:
    """One chunk of Emma's speech arrived. Show it when she reaches it."""
    global _sub_prev_until, _sub_task

    if not text or not text.strip():
        return
    if not _machine_owned():
        return
    show_at = max(_sub_prev_until, time.monotonic())
    _sub_queue.append((show_at, text))
    _sub_prev_until = max(_audio_until, show_at)
    if _sub_task is None or _sub_task.done():
        _sub_task = asyncio.create_task(_drain_subtitle())


async def _drain_subtitle() -> None:
    """Release queued text as its audio comes due."""
    global _sub_shown, _sub_restart

    while _sub_queue:
        show_at, _ = _sub_queue[0]
        wait = show_at - time.monotonic()
        if wait > 0:
            try:
                await asyncio.sleep(min(wait, 0.2))
            except asyncio.CancelledError:
                return
            continue
        _, text = _sub_queue.pop(0)
        if _sub_restart:
            _sub_shown = ""
            _sub_restart = False
        _sub_shown = (_sub_shown + text)[-400:]
        await broadcast({"type": "subtitle", "text": _sub_shown})


def drop_unheard() -> int:
    """Barge-in: throw away everything not yet spoken. Returns chunks dropped.

    The queue *is* the unheard part — that is the whole definition of it —
    so a barge-in is exactly this one line. Without it the screen finishes
    reciting a sentence the guest cut off, which is the text version of the
    bug `STATE["unheard"]` was added for on the slide side.

    What is already on screen stays: it was said, and blanking it mid-answer
    would look like a crash rather than an interruption.
    """
    global _sub_prev_until, _sub_restart

    dropped = len(_sub_queue)
    _sub_queue.clear()
    _sub_prev_until = 0.0
    _sub_restart = True
    return dropped


def end_turn() -> None:
    """Emma stopped generating. Whatever comes next begins a new line."""
    global _sub_restart
    _sub_restart = True


async def clear_subtitle() -> None:
    """Wipe the screen — the call is over.

    Immediately, not on the page's keep-timer. The conversation screen keeps
    its transcript for a few minutes because one person is sitting at it;
    this screen is the most public surface in the building and nobody is
    watching it when a visitor walks away.
    """
    global _sub_shown, _sub_prev_until, _sub_restart

    drop_unheard()
    _sub_shown = ""
    _sub_prev_until = 0.0
    _sub_restart = True
    await broadcast({"type": "subtitle", "text": ""})


async def set_phase(phase: str) -> None:
    """Tell the screen whether Emma is listening, speaking, or idle."""
    if not _machine_owned():
        return
    await broadcast({"type": "phase", "phase": phase})


def subtitle_state() -> dict:
    """For a screen that connects mid-conversation, and for tests."""
    return {"shown": _sub_shown, "pending": len(_sub_queue)}


def reset_subtitle() -> None:
    """Drop every bit of subtitle state. Tests, and session handover."""
    global _sub_shown, _sub_prev_until, _sub_restart, _sub_task
    _sub_queue.clear()
    _sub_shown = ""
    _sub_prev_until = 0.0
    _sub_restart = True
    _sub_task = None
