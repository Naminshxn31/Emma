"""REPLY_STALL_S: a heard question the server never answers.

Robot, 14 Oct 13:19 (owner's own call): three questions were transcribed and
the server sent nothing back for 146 s -- no reply, no usage, no error. The
provider now completes the turn again after REPLY_STALL_S, then rejoins the
conversation on a new socket and asks the question again as text.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.config import settings
from app.vad_gate import VadGate


class Scripted:
    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


QUIET = b"\x30\x00" * 160
LOUD = (int(0.1 * 32767)).to_bytes(2, "little", signed=True) * 160


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(settings, "reply_stall_s", 0.05)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 0.05)


class _Session:
    def __init__(self):
        self.calls = []

    async def send_realtime_input(self, **kw):
        self.calls.append(next(k for k, v in kw.items() if v is not None))

    async def send_client_content(self, **kw):
        self.calls.append(("client_content", kw))


def _provider(monkeypatch, answers):
    from app import vad_gate
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(vad_gate, "for_session",
                        lambda **_: VadGate(Scripted(answers), prefix_padding_ms=100))
    provider = GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"
    provider._session = _Session()
    provider._resume_handle = "handle"
    closed = []

    class _Cm:
        async def __aexit__(self, *exc):
            closed.append(self)

    provider._cm = _Cm()
    rejoined = []

    async def connect():
        # the old socket is closed before the new one opens (live 14 Oct:
        # the server refuses a resume while it is still open)
        assert closed, "resumed while the old socket was still open"
        assert provider._session is None, "the mic must not write into the closed socket"
        provider._session = _Session()
        provider._cm = _Cm()
        rejoined.append(provider._session)

    monkeypatch.setattr(provider, "_connect", connect)
    return provider, rejoined


def _client_contents(session):
    return [c[1] for c in session.calls if isinstance(c, tuple)]


def _question(provider, *pieces, wait=0.3, reply=False):
    """Each piece: one segment of speech whose transcript is `piece`."""
    async def run():
        for piece in pieces:
            await provider.send_audio(LOUD)
            if piece:
                provider._input_event(piece)
            await provider.send_audio(QUIET)
        if reply:
            provider._reply_seen()
        await asyncio.sleep(wait)

    asyncio.run(run())


def test_a_question_left_unanswered_is_nudged_then_asked_again_on_a_new_socket(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _question(provider, "Is there a hospital nearby?")
    assert _client_contents(old) == [{"turn_complete": True}], "the nudge: one more 'your turn'"
    assert len(rejoined) == 1
    asked = _client_contents(rejoined[0])
    assert len(asked) == 1 and asked[0]["turn_complete"] is True
    text = asked[0]["turns"].parts[0].text
    assert '"Is there a hospital nearby?"' in text and "ภาษาเดียวกับที่ลูกค้าพูด" in text
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["nudge", "reconnect"]
    provider._reply_seen()                                  # the answer, at last
    answered = [r for r in rows if r["event"] == "reply_stall"][-1]
    assert answered["stage"] == "answered" and answered["after"] == "reconnect"


def test_a_reply_in_time_is_left_alone(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _question(provider, "Where is the project?", reply=True)
    assert _client_contents(old) == [] and rejoined == []
    assert not [r for r in rows if r["event"] == "reply_stall"]


def test_noise_with_no_words_is_never_chased(monkeypatch, fast, rows):
    """Silence is the right answer to room noise; making her answer it is the
    "ค่ะ"-to-chatter bug."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _question(provider, "")
    assert _client_contents(old) == [] and rejoined == []


def test_off_by_default(monkeypatch, rows):
    assert settings.reply_stall_s == 0, "conftest/.env must not switch it on for the suite"
    provider, rejoined = _provider(monkeypatch, [True, False])
    _question(provider, "Is there parking?", wait=0.05)
    assert provider._stall_guard is None and rejoined == []


def test_the_whole_unanswered_question_is_asked_again(monkeypatch, fast, rows):
    """14 Oct: "Is there a hospital near" / " by?" were two segments. Words of
    a question still unanswered carry over into the next segment."""
    provider, rejoined = _provider(monkeypatch, [True, False, True, False])
    _question(provider, "Is there a hospital near", " by?")
    text = _client_contents(rejoined[0])[0]["turns"].parts[0].text
    assert '"Is there a hospital near by?"' in text


def test_an_answered_question_is_not_asked_again(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False, True, False])

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("How tall is the building?")
        await provider.send_audio(QUIET)
        provider._reply_seen()                   # answered
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        await asyncio.sleep(0.3)

    asyncio.run(run())
    text = _client_contents(rejoined[0])[0]["turns"].parts[0].text
    assert '"Is there a hospital nearby?"' in text and "tall" not in text


def test_the_guest_speaking_again_cancels_the_rejoin(monkeypatch, rows):
    monkeypatch.setattr(settings, "reply_stall_s", 0.05)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 0.3)
    provider, rejoined = _provider(monkeypatch, [True, False, True])

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Hello?")
        await provider.send_audio(QUIET)
        await asyncio.sleep(0.12)                # nudged
        await provider.send_audio(LOUD)          # the guest starts again
        await asyncio.sleep(0.4)

    asyncio.run(run())
    assert rejoined == []
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["nudge", "guest_spoke"]


def _message(**content):
    base = dict(interrupted=False, input_transcription=None, output_transcription=None,
                model_turn=None, turn_complete=False)
    base.update(content)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None,
                                 server_content=types.SimpleNamespace(**base))


def test_the_receive_loop_reads_the_new_socket_after_a_cut_over(monkeypatch):
    """The cut-over happens outside the loop; the old stream then dies as its
    socket closes. That death is "read the new one", not a drop to rejoin
    from a second time."""
    provider, _ = _provider(monkeypatch, [])
    reconnects = []

    async def reconnect():
        reconnects.append(1)
        return False

    monkeypatch.setattr(provider, "_reconnect", reconnect)

    class New:
        def __init__(self):
            self.passes = 0

        async def receive(self):
            self.passes += 1
            if self.passes == 1:
                yield _message(turn_complete=True)

    new = New()

    class Old:
        async def receive(self):
            yield _message(input_transcription=types.SimpleNamespace(text="hello", finished=None))
            provider._cut_over_from = provider._session          # what _cut_over does
            provider._session = new
            raise ConnectionError("socket closed by the cut-over")

    provider._session = Old()
    kinds = []

    async def run():
        async for event in provider._receive_events():
            kinds.append(event.kind)

    asyncio.run(run())
    assert "user_transcript" in kinds and "turn_complete" in kinds
    assert reconnects == [1], "only the new session's own end asks to rejoin"


def test_no_handle_means_no_rejoin_and_a_logged_reason(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])
    provider._resume_handle = None
    old = provider._session
    _question(provider, "Is there parking?")
    assert rejoined == [] and provider._session is old, "the stalled socket is left as it was"
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["nudge", "no_handle"]


def test_a_rotation_under_a_pending_question_asks_it_again(monkeypatch, fast, rows):
    """Review 14 Oct: go_away (or a dropped stream) rotating the socket while a
    question waited used to stop the guard silently -- the resumed model sits
    idle, the very silence this exists for. The question is asked again on
    the new socket, without a cut-over of our own."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    rotated = _Session()

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        await asyncio.sleep(0.02)
        provider._session = rotated               # the receive loop's own _reconnect
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert rejoined == [], "no second socket of ours"
    asked = _client_contents(rotated)
    assert len(asked) == 1 and '"Is there a hospital nearby?"' in asked[0]["turns"].parts[0].text
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["rotated"]


def test_the_loops_reconnect_is_marked_so_a_cut_over_never_overlaps_it(monkeypatch):
    provider, _ = _provider(monkeypatch, [])
    seen = []

    class _Cm:
        async def __aexit__(self, *exc):
            pass

    provider._cm = _Cm()

    async def connect():
        seen.append(provider._reconnecting)
        assert await provider._cut_over() is False, "a cut-over while the loop reconnects"
        provider._session = _Session()
        provider._cm = _Cm()

    monkeypatch.setattr(provider, "_connect", connect)
    assert asyncio.run(provider._reconnect()) is True
    assert seen == [True] and provider._reconnecting is False


# ---- a resume the server refuses: a fresh session in its place (robot 20 Oct)

def _refusing_provider(monkeypatch, refuse_fresh=False):
    """The loop's own road (`_reconnect`): the first connect -- with the
    handle -- is refused the way the robot saw it on 20 Oct; the second, with
    no handle, opens (unless `refuse_fresh`)."""
    provider, _ = _provider(monkeypatch, [True, False])
    opened, handles = [], []

    async def connect():
        handles.append(provider._resume_handle)
        if provider._resume_handle or refuse_fresh:
            raise RuntimeError("1008 None. BidiGenerateContent session not found or expired")
        provider._session = _Session()
        opened.append(provider._session)

    monkeypatch.setattr(provider, "_connect", connect)
    return provider, opened, handles


def _pending(provider, monkeypatch, *exchanges):
    """A conversation, then a question nobody has answered (the guard's own
    timer far away, so only the fresh session asks)."""
    monkeypatch.setattr(settings, "reply_stall_s", 30.0)

    async def run():
        for guest, emma in exchanges:
            provider._input_event(guest)
            provider._remember("emma", emma)
        await provider.send_audio(LOUD)
        provider._input_event("What is Journey to the Future?")
        await provider.send_audio(QUIET)
        ok = await provider._reconnect()
        guard = provider._stall_guard
        if guard is not None:
            guard.cancel()
        return ok

    return asyncio.run(run())


def test_a_refused_resume_opens_a_fresh_session_and_asks_with_a_recap(monkeypatch, rows):
    provider, opened, handles = _refusing_provider(monkeypatch)
    ok = _pending(provider, monkeypatch, ("Let's speak English from now on.", "Certainly!"),
                  ("Why biogenesis?", "Our fitness centre, Biogenesis, is on the ground floor."))
    assert ok is True, "the call goes on"
    assert handles == ["handle", None], "the fresh one is opened without the dead handle"
    assert provider._session is opened[0]
    asked = _client_contents(opened[0])
    assert len(asked) == 1 and asked[0]["turn_complete"] is True, "asked at once, not after the guard's timer"
    text = asked[0]["turns"].parts[0].text
    assert '"What is Journey to the Future?"' in text
    assert 'Emma: "Our fitness centre, Biogenesis' in text, "the new model is told the conversation"
    assert "ห้ามทักทาย" in text, "to the guest it is the same conversation"
    fresh = [r for r in rows if r["event"] == "session_fresh"]
    assert fresh and fresh[0]["pending"] is True and "not found or expired" in fresh[0]["reason"]
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["fresh"]


def test_the_recap_rides_once(monkeypatch, rows):
    provider, opened, _ = _refusing_provider(monkeypatch)
    _pending(provider, monkeypatch, ("Hello", "Hi"))
    assert "ห้ามทักทาย" not in provider._ask_text("again"), "consumed by the first ask"


def test_a_fresh_session_with_nothing_pending_says_nothing(monkeypatch, rows):
    provider, opened, _ = _refusing_provider(monkeypatch)
    assert asyncio.run(provider._reconnect()) is True
    assert _client_contents(opened[0]) == [], "no question waiting: the guest speaks next"
    assert [r for r in rows if r["event"] == "session_fresh"][0]["pending"] is False


def test_refused_twice_ends_the_call_as_before(monkeypatch, rows):
    provider, opened, handles = _refusing_provider(monkeypatch, refuse_fresh=True)
    assert asyncio.run(provider._reconnect()) is False
    assert handles == ["handle", None] and opened == []


# ---- a tool's result answered with silence (robot 20 Oct) ------------------

def _asked_then_tool(provider, monkeypatch, *, reply_after_tool=None, wait=0.3):
    """A question asked aloud, the model calls a tool (which counted as the
    reply starting), the result goes back, then `reply_after_tool`."""
    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("What is a social lounge?")
        await provider.send_audio(QUIET)
        provider._reply_seen()                       # the tool_call arrives
        provider._watch_after_tool()                 # its result went back
        if reply_after_tool:
            provider._reply_seen(reply_after_tool)
        await asyncio.sleep(wait)

    asyncio.run(run())


def test_silence_after_a_tool_result_is_chased(monkeypatch, fast, rows):
    """Robot 20 Oct 15:53: the story came up and no voice for 19 s, the guest
    asked again. A bare turn_complete is not an answer here."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _asked_then_tool(provider, monkeypatch, reply_after_tool="turn_complete")
    assert _client_contents(old) == [{"turn_complete": True}], "the nudge"
    nudge = [r for r in rows if r["event"] == "reply_stall"][0]
    assert nudge["stage"] == "nudge" and nudge["after_tool"] is True


def test_words_after_a_tool_result_end_the_watch(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _asked_then_tool(provider, monkeypatch, reply_after_tool="reply")
    assert _client_contents(old) == [] and rejoined == []
    assert not [r for r in rows if r["event"] == "reply_stall"]


def test_a_tool_after_emma_already_spoke_is_not_watched(monkeypatch, fast, rows):
    """A tap or one of our own follow-ups: no guest question waiting on it."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    provider._replied_since_end = True
    provider._stall_heard = "an old question"
    provider._watch_after_tool()
    asyncio.run(asyncio.sleep(0.2))
    assert _client_contents(old) == [] and provider._stall_after_tool is False
