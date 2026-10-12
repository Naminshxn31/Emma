"""The standing line (STANDING_LINE): a summoned session held open so a face
greeting costs a turn, not a 2-3s dial.

The property worth defending on every side is the OFF side: the sales-room
default must not grow a loop that dials Gemini on its own, and a line that
has been used must still die — one visitor's words must not sit in the
model's context when the next visitor walks up.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app import standing
from app.config import settings


def test_off_by_default_and_refused_under_multi_session(monkeypatch):
    standing.stop()
    assert settings.standing_line is False, "conftest pins the .env value off"
    standing.start()
    assert standing._task is None

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "multi_session", True)
    standing.start()
    assert standing._task is None, "no single line exists to hold under MULTI_SESSION"


def test_the_loop_rings_only_when_no_session_is_live(monkeypatch):
    from app import session as session_module
    from app import wake

    monkeypatch.setattr(session_module, "_active", None)
    rings = []

    async def fake_summon(reason=""):
        rings.append(reason)
        return 1

    monkeypatch.setattr(wake, "summon", fake_summon)
    # Two ticks with a live session (no ring), then the line is free.
    actives = [object(), object(), None, None]
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 6:
            raise asyncio.CancelledError
        if actives:
            session_module._active = actives.pop(0)

    monkeypatch.setattr(standing.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(standing.run())
    assert rings == ["standing_line", "standing_line"]


def test_a_virgin_standing_line_is_not_abandoned(monkeypatch):
    """Ungreeted and nobody ever heard: this IS the held-open line, and the
    30s abandon / idle timeout must both leave it alone — an hour of silence
    included."""
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > 8:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sess._close_when_nobody_is_there())
    assert sent == [], "a virgin standing line must never be hung up on"


def test_a_standing_line_that_was_used_still_dies(monkeypatch):
    """The moment anybody was heard, the ordinary rules end the quiet line —
    that is what flushes one visitor's context before the next."""
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._ever_heard = True
    sess._last_heard_at = time.monotonic() - 3600
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send

    async def fake_sleep(_s):
        pass

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    asyncio.run(sess._close_when_nobody_is_there())
    assert sent and sent[-1]["type"] == "idle_timeout"


def test_a_line_already_spoken_on_counts_as_a_conversation():
    """ประโยคทักทายไม่ให้พูดแทรก covers the standing line too: somebody who
    started talking on the held-open line must not be greeted over."""
    from app import greeter

    class Line:
        summoned = True
        _greeting_turn_seen = False
        _ever_heard = False

    assert greeter._in_conversation(Line()) is False
    Line._ever_heard = True
    assert greeter._in_conversation(Line()) is True


# -- the greeting on a line held open for hours (review 10 Oct) ----------------

def _ticks(monkeypatch, sess, n=3):
    from app import session as session_module
    from app.tools import slides

    sent = []

    async def send(msg):
        sent.append(msg)

    async def shut():
        pass

    sess._send_json = send
    monkeypatch.setattr(slides, "shutdown_display", shut)
    calls = {"n": 0}

    async def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] > n:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    try:
        asyncio.run(sess._close_when_nobody_is_there())
    except asyncio.CancelledError:
        pass
    return [m["type"] for m in sent]


@pytest.mark.parametrize("frames_streamed", [True, False])
def test_a_greeted_standing_line_is_not_hung_up_on_before_the_guest_can_answer(
        monkeypatch, frames_streamed):
    """Dialled an hour ago, greeted just now, the guest has not spoken yet.

    Probe 10 Oct, both ways the robot gets there: with the page's mic
    streaming (the real robot) the 10 s hold ended at the first frame and the
    greeting was never registered — the greeted line stayed "virgin" and,
    once the guest spoke, died by the 30 s ABANDON rule; with no frame since
    the dial the greeting registered but `_last_heard_at` still held the
    dial time, and the first idle tick hung up on the greeted guest."""
    from app import display
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sess._ears_closed_until = None if frames_streamed else time.monotonic() - 3590
    sess._greeting_turn_done()
    assert sess._greeting_turn_seen, "the greeting on the held line is the greeting"
    assert _ticks(monkeypatch, sess) == [], "hung up on the greeting it just gave"

    # The guest answers, then listens 35 s to a long reply: IDLE_TIMEOUT_S
    # (120) governs a greeted call, not the 30 s abandon of an ungreeted one.
    sess._ever_heard = True
    sess._last_heard_at = time.monotonic() - 35
    assert _ticks(monkeypatch, sess) == []


def test_a_greeted_standing_line_still_dies_when_nobody_answers(monkeypatch):
    """The idle clock starts at the greeting — and still runs out."""
    from app import display
    from app import session as session_module

    monkeypatch.setattr(settings, "standing_line", True)
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 3600
    sess._ears_closed_until = None
    sess._greeting_turn_done()
    sess._idle_from -= 121
    assert _ticks(monkeypatch, sess) == ["idle_timeout"]


def test_an_answer_on_a_line_somebody_spoke_on_is_not_its_greeting(monkeypatch):
    """Ears already open and a guest already heard: that turn answered them.
    It must not restart the idle clock (only guest speech does)."""
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._ears_closed_until = None
    sess._ever_heard = True
    sess._greeting_turn_done()
    assert sess._greeting_turn_seen is False
    assert sess._idle_from == 0.0
    plain = session_module.VoiceSession(None, provider_name="gemini")
    plain._greeting_turn_done()
    assert plain._greeting_turn_seen is False and plain._idle_from == 0.0


def test_the_idle_start_is_set_only_by_the_greeting():
    """The `_last_heard_at` rule's twin (test_only_guest_speech_resets_the_
    idle_clock): any other writer — assistant audio, a tool, a tour nudge —
    would keep the empty-room session alive forever."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "app" / "session.py").read_text(
        encoding="utf-8")
    writes = [line.strip() for line in source.splitlines() if "_idle_from = " in line]
    assert len(writes) == 2, writes
    body = source.split("def _greeting_turn_done", 1)[1].split("\n    def ", 1)[0]
    assert "self._idle_from = " in body
