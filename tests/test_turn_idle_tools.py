"""turn_idle and tool calls (TURN_IDLE_TRACKS_TOOLS, design §5 P0 / §4).

gemini-3.8 sends `turn_complete` *before* its tool call in 13/15 turns
(docs/emma-live-qa-eval-2026-10-08.md). An announcement released by that
early turn_complete lands inside the tool's gap — the robot talking over its
own reply. With the switch on, a call in flight keeps `turn_idle` clear,
tracked by call id; every way a call can vanish (result, dropped, resumed,
reconnect, session end, ceiling) releases it, because a counter that sticks
above zero parks every announcement for good (safety review #8).

With the switch off — the default, the sales room — the timing is today's,
byte for byte: the condo runtime test at the bottom.
"""
from __future__ import annotations

import asyncio

import pytest

from app import events
from app import session as session_module
from app.config import settings
from app.providers.base import ProviderEvent


def _ev(kind, name="get_room_status", call_id=None, data=None):
    event = ProviderEvent(kind=kind, text=name, data=data)
    if call_id is not None:
        event.call_id = call_id
    return event


class WS:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        pass


class Provider:
    """Feeds the session pump one event at a time; `step` returns once the
    session has finished handling it."""

    output_sample_rate = 24000

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.texts: list[str] = []

    async def events(self):
        while True:
            event = await self.queue.get()
            if event is None:
                self.queue.task_done()
                return
            yield event
            self.queue.task_done()

    async def step(self, *evs):
        for event in evs:
            await self.queue.put(event)
            await self.queue.join()

    async def send_text(self, text):
        self.texts.append(text)


async def _start():
    sess = session_module.VoiceSession(WS())
    provider = Provider()
    sess.provider = provider
    pump = asyncio.create_task(sess._provider_to_browser())
    return sess, provider, pump


async def _stop(provider, pump):
    await provider.queue.put(None)
    await asyncio.wait_for(pump, 2)


@pytest.fixture
def tracking(monkeypatch):
    monkeypatch.setattr(settings, "turn_idle_tracks_tools", True)


def test_call_clears_idle_until_the_reply_turn_completes(tracking):
    async def scenario():
        sess, provider, pump = await _start()
        await provider.step(_ev("tool_call", call_id="c1"))
        cleared = not sess.turn_idle.is_set()
        await provider.step(_ev("tool_result", call_id="c1", data={"ok": True}))
        after_result = sess.turn_idle.is_set()
        await provider.step(_ev("turn_complete"))
        final = sess.turn_idle.is_set()
        await _stop(provider, pump)
        return cleared, after_result, final, sess._tools_in_flight

    cleared, after_result, final, flight = asyncio.run(scenario())
    assert cleared and not after_result and final and flight == {}


def test_turn_complete_during_a_call_does_not_set_idle(tracking):
    async def scenario():
        sess, provider, pump = await _start()
        await provider.step(_ev("tool_call", call_id="c1"), _ev("turn_complete"))
        held = sess.turn_idle.is_set()
        await provider.step(_ev("tool_result", call_id="c1", data={"ok": True}),
                            _ev("turn_complete"))
        released = sess.turn_idle.is_set()
        await _stop(provider, pump)
        return held, released

    assert asyncio.run(scenario()) == (False, True)


@pytest.mark.parametrize("why", ["dropped", "resumed", "reconnected", "ceiling"])
def test_every_way_out_releases_idle(tracking, why, monkeypatch):
    """A call whose result never comes must not hold idle forever."""
    async def scenario():
        sess, provider, pump = await _start()
        await provider.step(_ev("tool_call", call_id="c1"), _ev("turn_complete"))
        assert not sess.turn_idle.is_set()
        if why == "dropped":
            await provider.step(_ev("tool_dropped", call_id="c1", data={"call_id": "c1"}))
        elif why == "resumed":
            monkeypatch.setattr(sess, "_nudge_tour_if_stalled", _nothing)
            await provider.step(_ev("resumed"))
        elif why == "reconnected":
            await provider.step(_ev("reconnected"))
        elif why == "ceiling":
            sess.prune_tools_in_flight(now=10 ** 9)
        state = sess.turn_idle.is_set(), dict(sess._tools_in_flight)
        await _stop(provider, pump)
        return state

    assert asyncio.run(scenario()) == (True, {})


async def _nothing():
    return None


def test_session_end_releases_idle_through_run(tracking, monkeypatch):
    """Finding #43: the session-end way out, through the real
    `VoiceSession.run` and its `finally`. The "end" case above used to call
    `_clear_tools_in_flight` itself, so deleting the call in run() changed
    no result. The call must be seen in flight first, or this proves
    nothing."""
    from tests.test_session_face_identity import DESK_UA, FakeProvider, run_session

    def in_flight(sess):
        return {"in_flight": dict(sess._tools_in_flight), "idle": sess.turn_idle.is_set()}

    provider = FakeProvider(script=[_ev("tool_call", call_id="c1"), in_flight,
                                    _ev("turn_complete")])
    sess = run_session(monkeypatch, DESK_UA, provider)
    assert "c1" in provider.seen["in_flight"] and provider.seen["idle"] is False
    assert sess._tools_in_flight == {}
    assert sess.turn_idle.is_set()


def test_the_ceiling_follows_the_tools_own_timeout(tracking):
    """A paced tool (next_slide) legitimately outlives TOOL_TIMEOUT_S; its
    entry must not be discarded at the ordinary ceiling."""
    from app.tools import registry, slides  # noqa: F401  (registers next_slide)

    async def scenario():
        sess, provider, pump = await _start()
        await provider.step(_ev("tool_call", name="next_slide", call_id="p1"),
                            _ev("tool_call", name="get_room_status", call_id="q1"))
        started = sess._tools_in_flight["p1"][0]
        sess.prune_tools_in_flight(now=started + registry.TOOL_TIMEOUT_S + 3)
        left = set(sess._tools_in_flight)
        sess.prune_tools_in_flight(now=started + registry.SLOW_TOOL_TIMEOUT_S + 3)
        await _stop(provider, pump)
        return left, set(sess._tools_in_flight)

    left, finally_left = asyncio.run(scenario())
    assert left == {"p1"} and finally_left == set()


@pytest.mark.parametrize("kind", ["resumed", "reconnected"])
def test_announcement_releases_are_not_the_trackers_to_end(tracking, kind, monkeypatch):
    """An announcement clears idle for its own turn. A resume or reconnect
    with nothing of ours in flight must not set it again under the
    announcement — that would let the next one barge in."""
    async def scenario():
        sess, provider, pump = await _start()
        monkeypatch.setattr(sess, "_nudge_tour_if_stalled", _nothing)
        sess.turn_idle.clear()                       # an announcement's turn
        await provider.step(_ev(kind))
        state = sess.turn_idle.is_set()
        await _stop(provider, pump)
        return state

    assert asyncio.run(scenario()) is False


def test_a_call_without_an_id_is_not_tracked(tracking):
    """OpenAI emits no call ids: no tracking beats a guess."""
    async def scenario():
        sess, provider, pump = await _start()
        await provider.step(_ev("tool_call"))
        state = sess.turn_idle.is_set()
        await _stop(provider, pump)
        return state

    assert asyncio.run(scenario()) is True


# ---- orderings A/B/C, and announce against the gap ----


ORDERS = {
    # gemini-3.8, 13/15: turn_complete, then the call, then its reply turn
    "A_before": ["turn_complete", "tool_call", "tool_result", "turn_complete"],
    "B_during": ["tool_call", "turn_complete", "tool_result", "turn_complete"],
    "C_after": ["tool_call", "tool_result", "turn_complete"],
}


async def _announce_through(order, settle):
    """Run one ordering with an announcement waiting from the first event.
    Returns whether the text had been sent after each step, and at the end."""
    sess, provider, pump = await _start()
    session_module._active = sess
    settings.announce_settle_s = settle
    sess.turn_idle.clear()                      # a turn is under way
    announce = None
    sent_after = []
    for i, kind in enumerate(ORDERS[order]):
        await provider.step(_ev(kind, call_id="c1",
                                data={"ok": True} if kind == "tool_result" else None))
        if i == 0:
            announce = asyncio.create_task(events.announce(
                "ระบบแจ้ง", source="test", max_wait=0.1, then_pause=0))
        await asyncio.sleep(0.02)
        sent_after.append(bool(provider.texts))
    await asyncio.wait_for(announce, 2)
    await _stop(provider, pump)
    return sent_after, bool(provider.texts)


@pytest.mark.parametrize("order", sorted(ORDERS))
def test_announce_does_not_fire_inside_the_gap(tracking, order, monkeypatch):
    """With tracking and a settle (P6 measures the real gap; 0.1 s here is a
    stand-in longer than the 20 ms between events), the announcement goes
    out only after the tool's own reply turn has completed — in every
    ordering 3.8 produces."""
    monkeypatch.setattr(settings, "announce_settle_s", 0.1)
    sent_after, sent = asyncio.run(_announce_through(order, 0.1))
    assert sent_after[:-1] == [False] * (len(sent_after) - 1)
    assert sent is True


def test_tracking_alone_does_not_cover_turn_complete_before_the_call(tracking, monkeypatch):
    """Ordering A with no settle: the announcement is released by the early
    turn_complete before the call can clear idle. Recorded, not hidden — it
    is why ANNOUNCE_SETTLE_S exists and why P6 measures the gap before the
    owner sets it."""
    monkeypatch.setattr(settings, "announce_settle_s", 0.0)
    sent_after, _sent = asyncio.run(_announce_through("A_before", 0.0))
    assert sent_after[0] is True


def test_condo_runtime_timing_is_todays_with_the_defaults():
    """Safety review #8: the default must not change sales-room timing. The
    3.8 ordering (turn_complete, tool_call, tool_result) leaves turn_idle set
    the whole way, exactly as before P0, and an announcement goes out at
    once — no settle, no tool tracking."""
    assert settings.turn_idle_tracks_tools is False
    assert settings.announce_settle_s == 0.0

    async def scenario():
        sess, provider, pump = await _start()
        session_module._active = sess
        idle_after = []
        for kind in ("turn_complete", "tool_call", "tool_result"):
            await provider.step(_ev(kind, call_id="c1",
                                    data={"ok": True} if kind == "tool_result" else None))
            idle_after.append(sess.turn_idle.is_set())
        loop = asyncio.get_running_loop()
        started = loop.time()
        sent = await events.announce("ระบบแจ้ง", source="test", max_wait=0.1, then_pause=0)
        took = loop.time() - started
        await _stop(provider, pump)
        return idle_after, sent, took, getattr(sess, "_tools_in_flight", None)

    idle_after, sent, took, flight = asyncio.run(scenario())
    assert idle_after == [True, True, True]
    assert sent is True and took < 0.05
    assert not flight


# ---- ANNOUNCE_SETTLE_S ----


def test_settle_waits_again_when_idle_is_cleared_inside_it(monkeypatch):
    monkeypatch.setattr(settings, "announce_settle_s", 0.15)

    async def scenario():
        idle = asyncio.Event()
        idle.set()
        loop = asyncio.get_running_loop()

        async def late_call():
            await asyncio.sleep(0.05)
            idle.clear()                        # the tool call after turn_complete
            await asyncio.sleep(0.2)
            idle.set()                          # its reply completes

        task = asyncio.create_task(late_call())
        started = loop.time()
        await events._wait_for_idle(idle, "test")
        took = loop.time() - started
        await task
        return took

    took = asyncio.run(scenario())
    assert took >= 0.25 + 0.14                  # waited the gap, then settled again


def test_zero_settle_is_no_sleep():
    async def scenario():
        idle = asyncio.Event()
        idle.set()
        loop = asyncio.get_running_loop()
        started = loop.time()
        await events._wait_for_idle(idle, "test")
        return loop.time() - started

    assert asyncio.run(scenario()) < 0.01


def test_settle_stays_inside_turn_wait(monkeypatch):
    """A turn_idle that is clear again after every settle (a stream of late
    calls) cannot hold an announcement past the one TURN_WAIT_S bound."""
    monkeypatch.setattr(settings, "announce_settle_s", 0.05)
    monkeypatch.setattr(events, "TURN_WAIT_S", 0.3)

    class AlwaysBusyAgain:
        """Set the instant anyone waits, clear whenever anyone looks."""

        def is_set(self):
            return False

        async def wait(self):
            await asyncio.sleep(0)
            return True

    async def scenario():
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.wait_for(events._wait_for_idle(AlwaysBusyAgain(), "test"), 2)
        return loop.time() - started

    took = asyncio.run(scenario())
    assert 0.25 <= took < 0.6


# ---- gemini.py's side of the plumbing ----


class _FC:
    def __init__(self, name, cid):
        self.name, self.id, self.args = name, cid, {}


class _Live:
    def __init__(self):
        self.responses = []

    async def send_tool_response(self, function_responses):
        self.responses.append(function_responses)


def _provider():
    from app.providers.gemini import GeminiProvider

    provider = GeminiProvider("voice", "test")
    provider._session = _Live()
    return provider


def test_gemini_tags_calls_and_results_with_the_call_id(monkeypatch):
    from app import tools

    async def dispatch(calls):
        return [(cid, name, {"ok": True}) for cid, name, _ in calls]

    monkeypatch.setattr(tools, "dispatch_all", dispatch)

    async def scenario():
        provider = _provider()
        return [e async for e in provider._run_tool_calls([_FC("a", "id-1"), _FC("b", None)])]

    evs = asyncio.run(scenario())
    assert [(e.kind, e.call_id) for e in evs] == [
        ("tool_call", "id-1"), ("tool_call", "local-1"),
        ("tool_result", "id-1"), ("tool_result", "local-1")]
    assert evs[0].data is None and evs[2].data == {"ok": True}


@pytest.mark.parametrize("switch", [True, False])
def test_gemini_reports_a_retired_connection_as_dropped_only_when_tracking(monkeypatch, switch):
    from app import tools

    monkeypatch.setattr(settings, "turn_idle_tracks_tools", switch)

    async def scenario():
        provider = _provider()
        replacement = _Live()

        async def dispatch(calls):
            provider._session = replacement
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        return [e async for e in provider._run_tool_calls([_FC("a", "id-1")])]

    kinds = [(e.kind, getattr(e, "call_id", None)) for e in asyncio.run(scenario())]
    if switch:
        assert kinds == [("tool_call", "id-1"), ("tool_dropped", "id-1")]
    else:
        assert kinds == [("tool_call", "id-1")]


def test_gemini_reports_a_cancelled_call_as_dropped(monkeypatch, tracking):
    from app import tools

    async def scenario():
        provider = _provider()

        async def dispatch(calls):
            provider._cancelled_tool_ids.add("id-1")       # cancelled mid-run
            return [(cid, name, {"ok": True}) for cid, name, _ in calls]

        monkeypatch.setattr(tools, "dispatch_all", dispatch)
        return [e async for e in provider._run_tool_calls([_FC("a", "id-1"), _FC("b", "id-2")])]

    kinds = [(e.kind, e.call_id) for e in asyncio.run(scenario())]
    assert ("tool_dropped", "id-1") in kinds and ("tool_result", "id-2") in kinds
    assert ("tool_result", "id-1") not in kinds
