"""REPLY_STALL_S in the Emma Ceremony app: one empty "your turn", never more (24 Nov 2026).

Decided the day before the opening ceremony. A heard utterance Gemini does not answer is chased in
three steps (app/providers/gemini.py `_reply_stall_guard`): the nudge (an empty turn_complete on the
same socket), a rejoin that asks the transcribed words again AS TEXT, and the whole call moved to
GEMINI_MODEL_FALLBACK. Until tonight a cue-only ceremony stopped at the nudge only while its live
window ("ตอบสด", or the order's own live step) was SHUT; with the window open it got all three:

* inside the window anything within 30 s of her last word may be answered, so guests' chatter that
  Gemini had rightly left alone was asked back to her as a question -- a server text turn, which the
  cue-only gate plays whatever the window has done since;
* the ceremony's call could end on gemini-2.5 for the rest of the ceremony (never measured reading
  the ceremony's lines; it closes the socket on an empty turn and does not compress context).

What these hold down: in a cue-only ceremony, window open or shut, nothing heard is ever asked back
as text -- not after the nudge, not as the silent-turn ask, not as the fallback model's ask-form
nudge, not after the socket rotated, not on a fresh session -- no rejoin, no model fallback. If she
stays silent staff press "กลับเข้าบท" or the next cue. Outside it (the sales room, the Emma Event
app, a ceremony that answers by name) the chase is what it was, step for step.

The fakes are tests/test_reply_stall.py's (its provider, its scripted VAD, its sockets) and the
ceremony is claimed the robot's way (tests/ceremony_app.py).
"""
from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, event_host
from app.config import settings
from tests.ceremony_app import ceremony_on
from tests.test_reply_stall import (FALLBACK, LOUD, QUIET, _client_contents, _provider,  # noqa: F401 -- fixtures
                                    _Session, fast, rows)

ROOT = Path(__file__).resolve().parents[1]
MODEL = "gemini-3.8-live"
HOST = "Emma, how deep is the main pool?"


@pytest.fixture
def chase(monkeypatch, fast):  # noqa: F811 -- the fixture of tests/test_reply_stall.py
    """Every step of the chase live, and quick: REPLY_STALL_S, the rejoin, and a fallback model set
    (the robot's .env has one) -- so "never" below is the watchdog's own doing, not a step that was
    switched off."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)


def _stages(rows) -> list:  # noqa: F811
    return [r["stage"] for r in rows if r["event"] == "reply_stall"]


def _spied(monkeypatch, provider) -> dict:
    """The later steps and every text the provider sends, counted and called through: the controls
    below see each of them move, so a zero inside the ceremony is not a spy that sees nothing."""
    seen = {"rejoin": 0, "stage3": 0, "fallback_dial": 0, "texts": []}
    cut_over, stall_fallback = provider._cut_over, provider._stall_fallback
    to_fallback, send_text = provider._cut_over_to_fallback, provider.send_text

    async def spy_cut_over():
        seen["rejoin"] += 1
        return await cut_over()

    async def spy_stall_fallback(since, heard):
        seen["stage3"] += 1
        return await stall_fallback(since, heard)

    async def spy_to_fallback(since=None):
        seen["fallback_dial"] += 1
        return await to_fallback(since)

    async def spy_send_text(text, **kw):
        seen["texts"].append(text)
        return await send_text(text, **kw)

    monkeypatch.setattr(provider, "_cut_over", spy_cut_over)
    monkeypatch.setattr(provider, "_stall_fallback", spy_stall_fallback)
    monkeypatch.setattr(provider, "_cut_over_to_fallback", spy_to_fallback)
    monkeypatch.setattr(provider, "send_text", spy_send_text)
    return seen


NOTHING_MORE = {"rejoin": 0, "stage3": 0, "fallback_dial": 0, "texts": []}


def _ask(provider, words) -> None:
    """One utterance through the mic as tests/test_reply_stall.py `_question` sends it -- speech,
    its transcript, the silence that ends it -- then the watchdog it armed run to its OWN end
    (every later step is awaited inside that one task): no sleep to outlast on a loaded machine."""
    async def run():
        await provider.send_audio(LOUD)
        provider._input_event(words)
        await provider.send_audio(QUIET)
        guard = provider._stall_guard
        assert guard is not None, "the utterance armed no watch at all"
        await asyncio.wait_for(asyncio.shield(guard), 15)

    asyncio.run(run())


def _guard(provider, words, *, session=None, silent=False) -> None:
    """The guard itself on a pending utterance (tests/test_ceremony_adhoc.py's way): for the cases
    the mic cannot stage -- a deaf mic, a silent turn after a tool, a socket that rotated."""
    async def run():
        since = time.monotonic()
        provider._stall_heard = words
        provider._stall_wait = provider._stall_t0 = since
        await asyncio.wait_for(provider._reply_stall_guard(
            provider._session if session is None else session, since, 0.01 if silent else None, silent), 15)

    asyncio.run(run())


def _open(kind: str) -> None:
    """The cue-only ceremony's live window: the page's "ตอบสด", or the running order's own step."""
    if kind == "adhoc":
        assert ceremony.adhoc_open()["ok"]
    else:
        assert event_host.live_open(cue_id="s4_recommend", brief_th="ตอบคำถามของพิธีกร")
    assert event_host.cue_only() and not event_host.cue_only_closed() and not event_host.mic_deaf()


# ---- the window open: the nudge, and nothing after it ---------------------------------------------

@pytest.mark.parametrize("window", ["adhoc", "order"])
def test_an_open_window_gets_the_one_nudge_and_nothing_after_it(monkeypatch, chase, rows, window):  # noqa: F811
    provider, rejoined = _provider(monkeypatch, [True, False])
    ceremony_on(replies="cues")
    _open(window)
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _ask(provider, HOST)
    assert _client_contents(old) == [{"turn_complete": True}], "step 1: one empty 'your turn', same socket"
    assert seen == NOTHING_MORE, "his words asked back as text, a rejoin or the fallback model"
    assert rejoined == [] and provider._session is old, "no new socket of any kind"
    assert provider.model == MODEL and provider._resume_handle == "handle"
    assert _stages(rows) == ["nudge"]
    assert not [r for r in rows if r["event"] in ("model_fallback", "reconnect", "session_fresh")]
    assert ceremony.live_state() is not None, "the window is staff's to close, not the watchdog's"
    provider._reply_seen()                                  # she answers after all: said so in the log
    answered = [r for r in rows if r["event"] == "reply_stall"][-1]
    assert answered["stage"] == "answered" and answered["after"] == "nudge"


@pytest.mark.parametrize("who", ["sales_room", "event_app", "named_ceremony"])
def test_outside_a_cue_only_ceremony_the_chase_is_what_it_was(monkeypatch, chase, rows, who):  # noqa: F811
    """The control, with the very spies and the very utterance: nudge, the rejoin that asks again,
    the fallback model -- tests/test_reply_stall.py's own chain, step for step."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    if who == "event_app":
        from app import session as session_module

        session_module._active = SimpleNamespace(profile="event", provider=None)
    elif who == "named_ceremony":
        ceremony_on(replies="named")                        # answers by name: not cue-only
    assert not event_host.cue_only()
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _ask(provider, HOST)
    assert _client_contents(old) == [{"turn_complete": True}]
    assert _stages(rows)[:3] == ["nudge", "reconnect", "fallback"]
    assert seen["rejoin"] == 1 and seen["stage3"] == 1 and seen["fallback_dial"] == 1
    assert len(seen["texts"]) == 2 and all(HOST in text for text in seen["texts"])
    assert len(rejoined) == 2 and provider._session is rejoined[1], "the rejoin, then the fallback's session"
    assert provider.model == FALLBACK and provider._resume_handle is None
    assert [r["road"] for r in rows if r["event"] == "model_fallback"] == ["stall"]


# ---- no text ask of any form in the window ---------------------------------------------------------

@pytest.mark.parametrize("cue_only", [True, False])
def test_a_silent_turn_after_a_tool_is_not_asked_again_as_text(monkeypatch, chase, rows, cue_only):  # noqa: F811
    """`_chase_silent_turn`: its first step IS the question asked again as text."""
    provider, rejoined = _provider(monkeypatch, [])
    if cue_only:
        ceremony_on(replies="cues")
        _open("adhoc")
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _guard(provider, HOST, silent=True)
    if cue_only:
        assert seen == NOTHING_MORE and old.calls == [] and rejoined == []
        assert _stages(rows) == [] and provider._stall_wait is None and provider.model == MODEL
    else:
        assert _stages(rows)[0] == "silent_turn" and HOST in seen["texts"][0]


@pytest.mark.parametrize("cue_only", [True, False])
def test_on_the_fallback_model_the_window_gets_no_ask_form_nudge(monkeypatch, chase, rows, cue_only):  # noqa: F811
    """A call DIALED on 2.5 (the configured model refused at connect): its nudge is the question asked
    again as text, because an empty turn closes its socket. In the ceremony: neither."""
    provider, rejoined = _provider(monkeypatch, [])
    provider.model = FALLBACK
    if cue_only:
        ceremony_on(replies="cues")
        _open("order")
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _guard(provider, HOST)
    if cue_only:
        assert seen == NOTHING_MORE and old.calls == [] and rejoined == []
        assert _stages(rows) == [] and provider._stall_wait is None
    else:
        assert _stages(rows)[0] == "nudge" and HOST in seen["texts"][0]


@pytest.mark.parametrize("state", ["open", "shut_moving", "none"])
def test_a_socket_that_rotated_asks_nothing_back(monkeypatch, chase, rows, state):  # noqa: F811
    """`_stall_rotated`: a go_away or a drop replaced the socket under the pending utterance, and the
    guard's first step is to ask it again on the new one -- as text."""
    provider, _ = _provider(monkeypatch, [])
    if state != "none":
        ceremony_on(replies="cues")
    if state == "open":
        _open("adhoc")
    elif state == "shut_moving":
        monkeypatch.setattr(event_host, "robot_moving", lambda: True)
        assert event_host.cue_only_closed()
    seen = _spied(monkeypatch, provider)
    asked_on, new = provider._session, _Session()
    provider._session = new                                 # the loop's own reconnect put it there
    _guard(provider, HOST, session=asked_on)
    if state == "none":
        assert _stages(rows)[0] == "rotated" and HOST in seen["texts"][0]
        assert len(_client_contents(new)) >= 1
    else:
        assert seen == NOTHING_MORE and new.calls == [] and asked_on.calls == []
        assert _stages(rows) == [] and provider._stall_wait is None


@pytest.mark.parametrize("state", ["open", "shut", "none"])
@pytest.mark.parametrize("pending", ["unanswered", "answer_cut_off"])
def test_a_fresh_session_asks_nothing_back(monkeypatch, chase, rows, state, pending):  # noqa: F811
    """`_ask_again_fresh`: the resume was refused, the new session knows nothing, and the pending
    question (or the answer the drop cut off) is asked at once with the recap -- as text. In a ceremony
    that is a question from before "กลับเข้าบท", answered live over the script."""
    provider, _ = _provider(monkeypatch, [])
    if state != "none":
        ceremony_on(replies="cues")
    if state == "open":
        _open("order")
    seen = _spied(monkeypatch, provider)
    session = provider._session

    async def run():
        if pending == "unanswered":
            provider._stall_heard = HOST
            provider._stall_wait = provider._stall_t0 = time.monotonic()
        else:
            provider._answer_open = (HOST, False)
        provider._fresh_pending = True
        await provider._ask_again_fresh()

    asyncio.run(run())
    assert provider._answer_open is None and provider._answer_owed is None
    if state == "none":
        assert _stages(rows) == ["fresh"] and len(seen["texts"]) == 1 and HOST in seen["texts"][0]
    else:
        assert seen == NOTHING_MORE and session.calls == []
        assert _stages(rows) == [] and provider._stall_wait is None


# ---- the window shut: as it was --------------------------------------------------------------------

def test_a_shut_window_with_the_robot_still_is_never_chased(monkeypatch, chase, rows):  # noqa: F811
    provider, rejoined = _provider(monkeypatch, [])
    ceremony_on(replies="cues")
    monkeypatch.setattr(event_host, "robot_moving", lambda: False)
    assert event_host.cue_only_closed() and event_host.mic_deaf(), "and nothing of the room goes up at all"
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _guard(provider, HOST)
    assert seen == NOTHING_MORE and old.calls == [] and rejoined == []
    assert _stages(rows) == [] and provider._stall_wait is None


def test_a_shut_window_with_the_robot_moving_gets_the_one_nudge(monkeypatch, chase, rows):  # noqa: F811
    """A spoken "หยุด" may be owed (review 7 Nov): one empty "your turn", as before tonight."""
    provider, rejoined = _provider(monkeypatch, [])
    ceremony_on(replies="cues")
    monkeypatch.setattr(event_host, "robot_moving", lambda: True)
    assert event_host.cue_only_closed() and not event_host.mic_deaf()
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _guard(provider, "Emma, stop!")
    assert _client_contents(old) == [{"turn_complete": True}]
    assert seen == NOTHING_MORE and rejoined == [] and _stages(rows) == ["nudge"]
    assert provider.model == MODEL


# ---- the belt behind step 1 ------------------------------------------------------------------------

@pytest.mark.parametrize("window", ["open", "shut"])
def test_a_later_step_reached_all_the_same_stops_whatever_the_window(monkeypatch, chase, rows, window):  # noqa: F811
    """`_stall_window_shut`, asked before every later step: step 1's probe said "no ceremony" (it
    failed, or the ceremony began while the chase slept) -- the rejoin must still not run, window
    open ("cue_only" in the row) or shut ("window_closed", as since 23 Nov)."""
    from app.providers import gemini

    provider, rejoined = _provider(monkeypatch, [])
    ceremony_on(replies="cues")
    if window == "open":
        _open("adhoc")
    else:
        monkeypatch.setattr(event_host, "robot_moving", lambda: True)   # a shut window is chased only then
    real, calls = gemini._cue_only, []

    def probe():
        calls.append(1)
        return False if len(calls) == 1 else real()

    monkeypatch.setattr(gemini, "_cue_only", probe)
    seen = _spied(monkeypatch, provider)
    old = provider._session
    _guard(provider, HOST)
    assert len(calls) == 2, "step 1's probe, then the belt before the rejoin"
    assert _client_contents(old) == [{"turn_complete": True}]
    assert seen == NOTHING_MORE and rejoined == [] and provider.model == MODEL
    assert _stages(rows) == ["nudge", "cue_only" if window == "open" else "window_closed"]
    assert provider._stall_wait is None


def test_the_probe_that_fails_is_no_ceremony(monkeypatch):
    from app.providers import gemini

    def boom():
        raise RuntimeError("the ceremony's state")

    ceremony_on(replies="cues")
    assert gemini._cue_only() is True and gemini._cue_only_closed() is True
    monkeypatch.setattr(event_host, "cue_only", boom)
    monkeypatch.setattr(event_host, "cue_only_closed", boom)
    assert gemini._cue_only() is False and gemini._cue_only_closed() is False


def test_each_ceremony_probe_of_the_provider_is_defined_once():
    """`_cue_only_closed` stood twice in the file until 24 Nov: the second silently replaced the
    first, and an edit to the first would have changed nothing."""
    tree = ast.parse((ROOT / "app" / "providers" / "gemini.py").read_text(encoding="utf-8"))
    names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    twice = sorted({name for name in names if names.count(name) > 1})
    assert twice == [], twice
    assert "_cue_only" in names and "_cue_only_closed" in names
