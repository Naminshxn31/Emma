"""Audit 27 Oct: a tap, a rotation and a quiet line must not end or garble a call.

Each test was run red with its fix reverted (CLAUDE.md: a test that passes
with the code reverted checks a side effect, not the condition). The repros
the audit wrote are in .tmp/audit-2026-10-27/connection/; the roads here are
theirs, driven through the code the guest actually reaches (session._quick_ask
for a tap, the real receive loop for a drop).
"""
from __future__ import annotations

import asyncio
import json
import time
import types

import pytest
from websockets.exceptions import ConnectionClosedError

from app.config import settings
from tests.test_reply_stall import (  # noqa: F401  (fixtures by import)
    FALLBACK,
    LOUD,
    QUIET,
    QUOTA,
    _client_contents,
    _dial_started,
    _LoopCm,
    _LoopSession,
    _provider,
    _Session,
    fast,
    rows,
)

UNITS_EN = "What unit types are there, and how big are they?"


class _Closed(_LoopSession):
    """A socket like websockets': once closed (by the server's `fail`, or by
    us), every send raises ConnectionClosedError."""

    def _check(self):
        if self.closed.is_set():
            raise ConnectionClosedError(None, None)

    async def send_client_content(self, **kw):
        self._check()
        await super().send_client_content(**kw)

    async def send_realtime_input(self, **kw):
        self._check()
        await super().send_realtime_input(**kw)


def _closing_loop_provider(monkeypatch, *, dial_s):
    provider, _ = _provider(monkeypatch, [True, False])
    sessions = []

    async def connect():
        s = _Closed()
        provider._session, provider._cm = s, _LoopCm(s)
        sessions.append((provider.model, s))

    async def dial(model, handle):
        sessions.append(("dialing", model))
        await asyncio.sleep(dial_s)
        s = _Closed()
        sessions.append((model, s))
        return _LoopCm(s), s

    monkeypatch.setattr(provider, "_connect", connect)
    monkeypatch.setattr(provider, "_dial", dial)
    return provider, sessions


async def _call_on(provider, question):
    first = _Closed()
    provider._session, provider._cm = first, _LoopCm(first)
    ended = []

    async def consume():
        async for _ in provider._receive_events():
            pass
        ended.append(True)

    loop = asyncio.create_task(consume())
    await provider.send_audio(LOUD)
    provider._input_event(question)
    await provider.send_audio(QUIET)
    return loop, ended


class _Page:
    """The robot face's socket: frames are fed from the test."""

    def __init__(self, frames=()):
        self.q: asyncio.Queue = asyncio.Queue()
        for frame in frames:
            self.q.put_nowait(frame)
        self.sent: list = []

    async def receive(self):
        return await self.q.get()

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        pass


def _tap_frame(ask_id="units", lang="en"):
    return {"type": "websocket.receive", "text": json.dumps({"type": "quick_ask", "id": ask_id, "lang": lang})}


def _face_session(provider, ws=None):
    from app import session as session_module

    sess = session_module.VoiceSession(ws or _Page(), provider_name="gemini")
    sess.provider = provider
    sess.profile = "condo"
    sess._robot_face_claim = True
    return sess


# ---- 1. a tap on a socket the server already closed --------------------------------

def test_a_send_on_a_socket_the_server_closed_is_noted_not_raised(monkeypatch, rows):
    """`send_text` read `_session` and sent on it bare: a socket closed before
    the receive loop noticed raised ConnectionClosed into whoever sent -- a
    tap's session._quick_ask, and from there the mic pump. Now `_send_input`'s
    shape: one `upstream_closed` row, no exception. A wrong send still raises."""
    provider, _ = _provider(monkeypatch, [True, False])
    dead = _Closed()
    dead.fail(None)
    provider._session = dead
    asyncio.run(provider.send_text("ลูกค้าแตะหัวข้อ สระว่ายน้ำ"))
    assert {"event": "upstream_closed", "code": 1006, "stage": "text"} in rows

    class Broken(_Session):
        async def send_client_content(self, **kw):
            raise ValueError("not a closed socket")

    provider._session = Broken()
    with pytest.raises(ValueError):
        asyncio.run(provider.send_text("x"))


def test_a_tap_that_cannot_be_sent_never_ends_the_call(monkeypatch, rows):
    """`_quick_ask` awaited the provider bare inside `_browser_to_provider`,
    whose `except Exception` returns -- and FIRST_COMPLETED ends the call
    (session_end mic_pump_failed). No tap may end a call, whatever the send
    raises: the pump runs on until the page itself hangs up."""
    class Refusing:
        async def send_text(self, text):
            raise ValueError("whatever the provider raised")

    page = _Page([_tap_frame(), {"type": "websocket.disconnect"}])
    sess = _face_session(Refusing(), page)
    asyncio.run(asyncio.wait_for(sess._browser_to_provider(), 5))
    assert sess._end_reason == "browser_closed", "not mic_pump_failed"


def test_a_tap_while_stage3_waits_on_a_quota_closed_socket_keeps_the_call(monkeypatch, fast, rows):
    """The audit's road (A2), whole: stage 3 dials the fallback, the stalled
    socket is closed for quota, the receive loop waits for the dial with
    `_session` still on the closed socket -- and a tap arrives."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, sessions = _closing_loop_provider(monkeypatch, dial_s=0.6)
    page = _Page()
    sess = _face_session(provider, page)

    async def run():
        loop, ended = await _call_on(provider, "Is there a hospital nearby?")
        pump = asyncio.create_task(sess._browser_to_provider())
        await _dial_started(sessions)
        provider._session.fail(RuntimeError(QUOTA))
        await asyncio.sleep(0.05)
        await page.q.put(_tap_frame())
        await asyncio.sleep(1.0)
        pump_done = pump.done()
        loop.cancel()
        pump.cancel()
        return pump_done, ended

    pump_done, ended = asyncio.run(run())
    assert not pump_done, "the mic pump returned: FIRST_COMPLETED ends the call (%s)" % sess._end_reason
    assert ended == [], "the receive loop gave up"
    assert provider.model == FALLBACK and provider._session is not None


# ---- 2. a tap during a rejoin is ONE user turn ---------------------------------------

def test_a_tap_during_a_stage2_rejoin_is_the_only_turn_on_the_new_socket(monkeypatch, fast, rows):
    """B1: the tap waited in send_text while `_cut_over` dialed; its watch ran
    only after the send, so the stalled guard -- resuming first -- still saw
    its own watch and asked the OLD question, and the tap went out right
    behind it. Watched before it is sent, the guard sees a newer turn."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    plain_connect = provider._connect

    async def slow_connect():
        await asyncio.sleep(0.3)
        await plain_connect()

    monkeypatch.setattr(provider, "_connect", slow_connect)
    sess = _face_session(provider)

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while not provider._reconnecting:          # nudge at 0.05 s, the rejoin from 0.10 s
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.05)
        await sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"})
        await asyncio.sleep(0.01)

    asyncio.run(run())
    asked = [c["turns"].parts[0].text for c in _client_contents(rejoined[0]) if c.get("turns")]
    assert len(asked) == 1, "two user turns back to back: %r" % [a[:50] for a in asked]
    assert asked[0].endswith(UNITS_EN)


def _fresh_road(monkeypatch, old_close_s=0.0):
    """B2: the line dropped, the resume is refused, the loop's `_reconnect`
    opens a fresh session. `old_close_s`: how long closing the dead socket's
    context takes (`_reconnect` awaits it between the new socket opening and
    `_ask_again_fresh`)."""
    monkeypatch.setattr(settings, "reply_stall_s", 8.0)
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    provider, _ = _provider(monkeypatch, [True, False])
    opened = []

    async def connect():
        await asyncio.sleep(0.2)
        if provider._resume_handle:
            raise RuntimeError("1008 BidiGenerateContent session not found or expired")
        s = _Session()
        provider._session = s
        opened.append(s)

    class SlowCm:
        async def __aexit__(self, *exc):
            await asyncio.sleep(old_close_s)

    monkeypatch.setattr(provider, "_connect", connect)
    provider._cm = SlowCm()
    sess = _face_session(provider)

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        provider._retire(provider._session)        # the line dropped
        rec = asyncio.create_task(provider._reconnect())
        while not provider._reconnecting:
            await asyncio.sleep(0.005)
        await sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"})
        await rec
        for guard in list(provider._stall_guards):
            guard.cancel()

    asyncio.run(run())
    return [c[1]["turns"].parts[0].text for c in opened[0].calls if c[1].get("turns")]


@pytest.mark.parametrize("old_close_s", [0.0, 0.25])
def test_a_tap_during_a_fresh_rejoin_goes_once_carrying_the_recap(monkeypatch, rows, old_close_s):
    """B2: `_ask_again_fresh` asked the pending turn with the recap, and the
    tap that had waited for the socket went out again on top of it. The tap
    now carries the recap itself and the rejoin does not ask it -- whether the
    tap is still waiting when the rejoin asks (0.0), or already went out while
    the dead socket's close was awaited (0.25: in production that close is
    real I/O)."""
    from app.providers.gemini import FRESH_RECAP

    asked = _fresh_road(monkeypatch, old_close_s)
    assert len(asked) == 1, "two user turns back to back: %r" % [a[:50] for a in asked]
    assert asked[0].startswith(FRESH_RECAP.split("%s")[0]), "the fresh session gets the recap"
    assert asked[0].endswith(UNITS_EN)


# ---- 3. go_away while a tool runs ------------------------------------------------------

class _QSession:
    """A socket whose messages the test feeds; receive() ends a pass at
    turn_complete like the SDK's."""

    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()
        self.calls: list = []

    async def receive(self):
        while True:
            m = await self.q.get()
            if m is None:
                return
            yield m
            sc = getattr(m, "server_content", None)
            if sc is not None and getattr(sc, "turn_complete", False):
                return

    async def send_realtime_input(self, **kw):
        self.calls.append(next(k for k, v in kw.items() if v is not None))

    async def send_client_content(self, **kw):
        self.calls.append(("client_content", kw))

    async def send_tool_response(self, **kw):
        self.calls.append(("tool_response", kw))


def _msg(**kw):
    base = dict(session_resumption_update=None, go_away=None, usage_metadata=None,
                tool_call_cancellation=None, tool_call=None, server_content=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_go_away_while_a_tool_runs_shows_the_result_and_asks_again(monkeypatch, rows):
    """C1: guest asks -> the model calls a tool -> go_away arrives while it
    runs -> `_reconnect` resumes on a new socket. The result was dropped as
    "from a retired connection": no story on the page, and nothing asked the
    question again on the resumed model, which is idle. Now: shown on the
    page, sent to neither socket, and asked again on the new one."""
    from app import tools as tools_pkg

    monkeypatch.setattr(settings, "reply_stall_s", 0.3)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 0.0)
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    provider, _ = _provider(monkeypatch, [True, False])
    old, new = _QSession(), _QSession()
    provider._session = old

    class Cm:
        async def __aexit__(self, *exc):
            pass

    provider._cm = Cm()

    async def resume():                      # go_away's road: the new one opens first
        provider._session, provider._cm = new, Cm()

    monkeypatch.setattr(provider, "_connect", resume)
    story = {"ok": True, "screen": "story", "topic": "pools_all"}

    async def slow_dispatch(calls):
        await asyncio.sleep(0.2)
        return [(cid, name, dict(story)) for cid, name, _a in calls]

    monkeypatch.setattr(tools_pkg, "dispatch_all", slow_dispatch)
    monkeypatch.setattr(tools_pkg, "model_view", lambda name, result: result)
    events: list = []

    async def run():
        async def consume():
            async for ev in provider.events():
                events.append((ev.kind, ev.data))

        task = asyncio.create_task(consume())
        await provider.send_audio(LOUD)
        provider._input_event("Is there a swimming pool?")
        await provider.send_audio(QUIET)
        fc = types.SimpleNamespace(id="c1", name="present_story", args={"topic": "pools_all"})
        await old.q.put(_msg(tool_call=types.SimpleNamespace(function_calls=[fc])))
        await asyncio.sleep(0.05)            # the tool is running
        await old.q.put(_msg(go_away=types.SimpleNamespace(time_left="5s")))
        await asyncio.sleep(0.9)             # > the tool + REPLY_STALL_S
        task.cancel()

    asyncio.run(run())
    assert ("tool_result", story) in events, "the page never got what the tool did"
    assert not [c for c in old.calls + new.calls if isinstance(c, tuple) and c[0] == "tool_response"], \
        "a result from a retired connection must not enter its replacement"
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert stages and stages[0] == "rotated", stages
    asked = [c[1]["turns"].parts[0].text for c in new.calls if isinstance(c, tuple) and c[1].get("turns")]
    assert asked and "Is there a swimming pool?" in asked[0], "asked again on the resumed socket"


# ---- 4. a drop on an idle resumed line --------------------------------------------------

class _DropAfter:
    """A socket whose stream raises (or just ends) after `after_s`, with no
    message before it -- a resumed model is idle and says nothing. `skew`:
    seconds the provider's clock jumps first (the drop comes that much
    later, without the test waiting for it)."""

    def __init__(self, after_s, clock=None, skew=0.0, ends=False):
        self.after_s, self.clock, self.skew, self.ends = after_s, clock, skew, ends
        self.calls: list = []

    async def receive(self):
        await asyncio.sleep(self.after_s)
        if self.clock is not None:
            self.clock[0] += self.skew
        if self.ends:
            return
        raise ConnectionClosedError(None, None)
        yield  # pragma: no cover - an async generator

    async def send_realtime_input(self, **kw):
        self.calls.append("rt")

    async def send_client_content(self, **kw):
        self.calls.append("cc")


def _idle_line(monkeypatch, *, skew, ends):
    """The first socket drops; the rejoin's socket stays silent and drops too,
    `skew` seconds later on the provider's clock. Returns (events, resumes)."""
    from app.providers import gemini

    clock = [0.0]

    class _Clock:
        """Only the provider's clock moves: asyncio keeps the real one."""

        def __getattr__(self, name):
            return getattr(time, name)

        @staticmethod
        def monotonic():
            return time.monotonic() + clock[0]

    monkeypatch.setattr(gemini, "time", _Clock())
    provider, _ = _provider(monkeypatch, [])
    provider._session = _DropAfter(0.01, ends=ends)
    opened = []

    class Cm:
        async def __aexit__(self, *exc):
            pass

    provider._cm = Cm()

    async def resume():
        s = _DropAfter(0.01, clock, skew, ends=ends) if not opened else _DropAfter(30.0)
        provider._session, provider._cm = s, Cm()
        opened.append(s)

    monkeypatch.setattr(provider, "_connect", resume)
    kinds: list = []

    async def run():
        async def consume():
            async for ev in provider._receive_events():
                kinds.append(ev.kind)

        task = asyncio.create_task(consume())
        for _ in range(100):
            if task.done() or len(opened) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    return kinds, opened


@pytest.mark.parametrize("ends", [False, True], ids=["dropped", "ended"])
def test_a_drop_long_after_a_resume_on_an_idle_line_is_rejoined_again(monkeypatch, rows, ends):
    """D1: `just_reconnected` -- no reconnect on top of a reconnect that
    yielded nothing -- was cleared only by a received message. A resumed
    model is idle and the standing line is idle by design, so the next drop,
    however much later, found it still set and ended the call."""
    kinds, opened = _idle_line(monkeypatch, skew=10.0, ends=ends)
    assert "error" not in kinds, "an independent second drop ended the call"
    assert len(opened) == 2, "the second drop gets its own rejoin"


@pytest.mark.parametrize("ends", [False, True], ids=["dropped", "ended"])
def test_a_rejoin_that_yields_nothing_right_back_is_not_rejoined_on_top(monkeypatch, rows, ends):
    """...while the guard itself stays: a rejoined socket that drops at once,
    nothing received, is the rejoin failing -- no tight reconnect loop."""
    kinds, opened = _idle_line(monkeypatch, skew=0.0, ends=ends)
    assert len(opened) == 1
    if not ends:
        assert kinds[-1] == "error", "the call ends as before"


# ---- 6. a turn that said nothing keeps the story on screen -----------------------------

class _Events:
    output_sample_rate = 24000

    def __init__(self, evs):
        self.evs = evs

    async def events(self):
        for e in self.evs:
            yield e


def test_a_turn_with_no_reply_keeps_the_storys_taps(monkeypatch):
    """A bare "ค่ะ" dropped by SILENCE_BARE_ACK (the gate lets only the
    turn_complete through -- tests/test_bare_ack.py), or 3.8's empty turn
    before its tool call, ended a turn the page reads as an answer with no
    picture: the story the guest was looking at went, and its taps with it.
    A turn that said nothing is no answer; one that spoke (voice or words)
    still owns the screen."""
    from app import session as session_module
    from app.providers.base import ProviderEvent

    async def turn(sess, *evs):
        sess._guest_started_turn()                # talk near the robot opened a turn
        sess.provider = _Events([*evs, ProviderEvent(kind="turn_complete")])
        await sess._provider_to_browser()

    async def scenario():
        sess = session_module.VoiceSession(_Page())
        sess._robot_face_claim = True
        sess._story_taps = {"story_x_level3"}
        await turn(sess)                                             # nothing at all
        kept = set(sess._story_taps)
        await turn(sess, ProviderEvent(kind="audio", audio=b"\x01\x00" * 2400))
        after_voice = set(sess._story_taps)
        sess._story_taps = {"story_x_level3"}
        await turn(sess, ProviderEvent(kind="assistant_transcript", text="ได้ค่ะ"))
        return kept, after_voice, sess._story_taps

    kept, after_voice, after_words = asyncio.run(scenario())
    assert kept == {"story_x_level3"}, "a turn with no reply wiped the story's taps"
    assert after_voice == set() and after_words == set(), "an answer still owns the screen"
