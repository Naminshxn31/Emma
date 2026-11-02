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
    # stage 3 (the fallback model) has its own tests below; here the two
    # stages the older tests describe, as on a machine with no fallback
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    # the tests end a turn right after the tool's result; the settle window
    # has its own test
    from app.providers import gemini

    monkeypatch.setattr(gemini, "AFTER_TOOL_SETTLE_S", 0.0)


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
        # a RESUME: the old socket is closed before the new one opens (live
        # 14 Oct: the server refuses a resume while it is still open). A fresh
        # session on the fallback model is dialed first (review 27 Oct).
        if provider._resume_handle:
            assert closed, "resumed while the old socket was still open"
            assert provider._session is None, "the mic must not write into the closed socket"
        provider._session = _Session()
        provider._cm = _Cm()
        rejoined.append(provider._session)

    async def dial(model, handle):
        # stage 3's dial: hands the session back, touches nothing of the call's
        session = _Session()
        rejoined.append(session)
        return _Cm(), session

    monkeypatch.setattr(provider, "_connect", connect)
    monkeypatch.setattr(provider, "_dial", dial)
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
    asked = _client_contents(rotated)
    assert asked and '"Is there a hospital nearby?"' in asked[0]["turns"].parts[0].text
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    # asked again on the rotated socket first, with no cut-over of ours before
    # it -- and then chased like any ask (review 27 Oct: it used to stop here)
    assert stages[0] == "rotated" and "nudge" in stages[1:]


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
    """Robot 20 Oct 15:53 (19 s) and 27 Oct 17:15 / 17:28 (the guest asked
    again at +5-6 s): the story came up and no voice. A bare turn_complete is
    not an answer here -- it is her turn ENDING silent: the question goes
    again SILENT_TURN_S later, not an empty nudge after REPLY_STALL_S (which
    the 20 Oct replay saw answered with a bare turn_complete, 18 times)."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 5.0)     # no rejoin inside this test
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _asked_then_tool(provider, monkeypatch, reply_after_tool="turn_complete", wait=0.15)
    asked = _client_contents(old)
    assert len(asked) == 1 and '"What is a social lounge?"' in asked[0]["turns"].parts[0].text
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["silent_turn"]


def test_the_silent_turn_is_asked_again_once_then_the_chain(monkeypatch, fast, rows):
    """The echo of that ask (a bare turn_complete again) must not start the
    chase over -- it would re-ask every SILENT_TURN_S forever. It goes on to
    the rejoin like any ask left unanswered."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("What is a social lounge?")
        await provider.send_audio(QUIET)
        provider._reply_seen("tool_call")
        provider._watch_after_tool()
        provider._reply_seen("turn_complete")        # her turn ends without a word
        await asyncio.sleep(0.04)
        provider._reply_seen("turn_complete")        # ...and again, after our ask
        await asyncio.sleep(0.3)

    asyncio.run(run())
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert stages[:2] == ["silent_turn", "reconnect"], stages
    assert len([c for c in _client_contents(old) if c.get("turns")]) == 1, "asked once on that socket"


class _Feed(_Session):
    """A socket whose receive() yields what the test puts in, as the server's
    messages, for the REAL receive loop."""
    def __init__(self):
        super().__init__()
        self.inbox = asyncio.Queue()

    async def receive(self):
        while True:
            msg = await self.inbox.get()
            if msg is None:
                raise ConnectionError("closed")
            yield msg


def _content(**kw):
    content = types.SimpleNamespace(interrupted=False, input_transcription=None, output_transcription=None,
                                    model_turn=None, turn_complete=False)
    for key, value in kw.items():
        setattr(content, key, value)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None, server_content=content)


def _said(text):
    return _content(output_transcription=types.SimpleNamespace(text=text))


def _audio(pcm):
    part = types.SimpleNamespace(inline_data=types.SimpleNamespace(data=pcm))
    return _content(model_turn=types.SimpleNamespace(parts=[part]))


def test_markup_and_silent_audio_are_not_her_answer(monkeypatch, fast, rows):
    """Robot 27 Oct 17:21, on the real receive loop: after a story she sent
    "<no " / "speech>{pause}" over a moment of silent audio and ended the
    turn. That counted as her answer -- the watch was cleared, nothing chased
    the silence, the guest swore and asked again. Now: no caption, no answer,
    and the question goes again."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    monkeypatch.setattr(settings, "reply_stall_s", 5.0)          # the plain nudge is not what fires
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 5.0)
    provider, _ = _provider(monkeypatch, [True, False])
    sock = _Feed()
    provider._session = sock
    seen = []

    async def run():
        async def consume():
            async for ev in provider._receive_events():
                seen.append((ev.kind, ev.text))

        loop = asyncio.create_task(consume())
        await provider.send_audio(LOUD)
        provider._input_event("ขอดูผังโครงการ")
        await provider.send_audio(QUIET)
        provider._reply_seen("tool_call")
        provider._watch_after_tool()                 # present_story's result went back
        for msg in (_said("<no "), _audio(b"\x00\x00" * 480), _said("speech>{pause}"),
                    _content(turn_complete=True)):
            await sock.inbox.put(msg)
        await asyncio.sleep(0.2)
        loop.cancel()

    asyncio.run(run())
    assert not [t for k, t in seen if k == "assistant_transcript" and t], "markup reached the screen"
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"] == ["silent_turn"]
    asked = [c for c in _client_contents(sock) if c.get("turns")]
    assert asked and '"ขอดูผังโครงการ"' in asked[0]["turns"].parts[0].text


def test_her_voice_is_her_answer(monkeypatch, fast, rows):
    """The same turn with her voice in it is an answer: nothing is chased."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    provider, _ = _provider(monkeypatch, [True, False])
    sock = _Feed()
    provider._session = sock
    voice = (int(0.2 * 32767)).to_bytes(2, "little", signed=True) * 480

    async def run():
        async def consume():
            async for _ in provider._receive_events():
                pass

        loop = asyncio.create_task(consume())
        await provider.send_audio(LOUD)
        provider._input_event("ขอดูผังโครงการ")
        await provider.send_audio(QUIET)
        provider._reply_seen("tool_call")
        provider._watch_after_tool()
        for msg in (_said("นี่คือผังโครงการ"), _audio(voice), _content(turn_complete=True)):
            await sock.inbox.put(msg)
        await asyncio.sleep(0.2)
        loop.cancel()

    asyncio.run(run())
    assert not [r for r in rows if r["event"] == "reply_stall" and r["stage"] != "answered"]
    assert provider._stall_wait is None


def test_voice_and_words_are_told_from_silence_and_markup():
    from app.providers import gemini
    from app.providers.transcript_filter import TranscriptFilter

    assert not gemini._voiced(b"\x00\x00" * 480) and not gemini._voiced(b"\x10\x00" * 480)
    assert gemini._voiced((3000).to_bytes(2, "little", signed=True) * 480)
    assert gemini._voiced((-3000).to_bytes(2, "little", signed=True) * 480)
    assert not gemini._voiced(b"") and not gemini._voiced(b"\x01")
    f = TranscriptFilter()
    assert f.feed("<no ") + f.feed("speech>{pause}") + f.flush() == ""
    assert f.feed("ห้องนี้</em> สวยค่ะ") == "ห้องนี้ สวยค่ะ"
    assert f.feed("<no speech") + f.flush() == "", "a tag that never closed"
    assert f.feed("The lagoon is 155 metres.") == "The lagoon is 155 metres."
    assert not gemini._has_words(" ,. ") and gemini._has_words("ค่ะ")


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


# ---- a tapped turn (27 Oct) ---------------------------------------------------

def _tapped_then_tool(provider, *, after):
    """A topic tapped on the robot face: the model calls a tool, the result
    goes back, then `after` -- Emma's previous answer already played."""
    async def run():
        provider._replied_since_end = True          # the turn before was answered aloud
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        provider._reply_seen("tool_call")
        provider._watch_after_tool()
        provider._reply_seen(after)
        await asyncio.sleep(0.3)

    asyncio.run(run())


def test_a_tapped_turn_left_silent_after_its_tool_is_chased(monkeypatch, fast, rows):
    """Replay of the 20 Oct robot log (27 Oct): after a story or a room-type
    card the model ended the turn without a word. A spoken question is chased;
    a tap never was (`_stall_heard` comes from the input transcript only, and
    the last answer's audio had set `_replied_since_end`). Asked again as the
    tap's own text -- nobody said it, so not STALL_ASK's "ลูกค้าพูดว่า". Her
    turn ENDED silent, so first on the same socket (27 Oct, `_chase_silent_turn`),
    then the rejoin."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _tapped_then_tool(provider, after="turn_complete")
    assert [c["turns"].parts[0].text for c in _client_contents(old) if c.get("turns")] == ["ลูกค้าแตะหัวข้อ สระว่ายน้ำ"]
    assert len(rejoined) == 1
    asked = _client_contents(rejoined[0])
    assert asked[0]["turns"].parts[0].text == "ลูกค้าแตะหัวข้อ สระว่ายน้ำ"
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"][:2] == ["silent_turn", "reconnect"]


def test_a_tapped_turn_answered_in_words_is_left_alone(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    _tapped_then_tool(provider, after="reply")
    assert _client_contents(old) == [] and rejoined == []
    assert provider._stall_text_turn is False


def test_a_tap_is_not_watched_with_the_switch_off(monkeypatch, rows):
    monkeypatch.setattr(settings, "reply_stall_s", 0)
    provider, _ = _provider(monkeypatch, [True, False])
    provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
    assert provider._stall_wait is None and provider._stall_text_turn is False


def test_speech_after_a_tap_is_chased_as_speech(monkeypatch, fast, rows):
    """The guest taps, then says something: the spoken words are what get asked
    again, wrapped as heard (STALL_ASK), not the tap's text."""
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def tap():
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")

    asyncio.run(tap())
    _question(provider, "Is there a hospital nearby?")
    text = _client_contents(rejoined[0])[0]["turns"].parts[0].text
    assert '"Is there a hospital nearby?"' in text and "สระว่ายน้ำ" not in text



# ---- review 27 Oct: a tap survives what is not an answer ------------------------

def test_a_wordless_sound_after_a_tap_keeps_the_tap_owed(monkeypatch, fast, rows):
    """A cough or an "อืม" near the robot opens a segment with no words: the
    tap stays the thing to chase (it used to be cleared, and the guard then
    found nothing to ask)."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        await provider.send_audio(LOUD)          # a sound, no transcript
        await provider.send_audio(QUIET)
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert _client_contents(old) == [{"turn_complete": True}], "the nudge"
    assert _client_contents(rejoined[0])[0]["turns"].parts[0].text == "ลูกค้าแตะหัวข้อ สระว่ายน้ำ"


def test_the_tap_cutting_the_previous_answer_keeps_its_watch(monkeypatch, fast, rows):
    """The tap lands while the last answer is still generating: the
    `interrupted` that ends THAT answer is not the tapped turn's reply."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        provider._replied_since_end = True
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        provider._reply_seen("interrupted")      # the old generation, cut
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert _client_contents(old) == [{"turn_complete": True}]


def test_a_late_transcript_replaces_the_tap_not_glued_onto_it(monkeypatch, fast, rows):
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def run():
        provider.watch_text_turn("TAPTEXT")
        provider._input_event("Is there a gym?")   # the transcript of speech before the tap
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert provider._stall_text_turn is False
    asked = [c for c in _client_contents(rejoined[0])] if rejoined else []
    for c in asked:
        assert "TAPTEXT" not in c["turns"].parts[0].text


def test_a_bare_turn_complete_answering_the_nudge_does_not_stop_the_rejoin(monkeypatch, fast, rows):
    """Replay of 20 Oct on the final code (27 Oct): the server answered every
    nudge (itself a turn_complete) with a bare turn_complete, that counted as
    "a reply seen", and the rejoin-and-ask-again never ran -- 18 nudges, 0
    rejoins, the guest's questions unanswered."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session
    real = old.send_client_content

    async def echoing(**kw):
        await real(**kw)
        if kw.get("turn_complete") and not kw.get("turns"):
            provider._reply_seen("turn_complete")     # the server's bare echo

    old.send_client_content = echoing
    _question(provider, "Is there a hospital nearby?")
    assert _client_contents(old) == [{"turn_complete": True}], "the nudge"
    assert len(rejoined) == 1, "the echo of the nudge stopped the rejoin"
    assert [r["stage"] for r in rows if r["event"] == "reply_stall"][:2] == ["nudge", "reconnect"]


# ---- 27 Oct: the key at its tokens-per-minute ceiling ---------------------------

FALLBACK = "gemini-2.5-flash-native-audio-preview-12-2025"


def test_a_stall_the_rejoin_did_not_cure_goes_to_the_fallback_model(monkeypatch, fast, rows):
    """Instrumented replay of the 20 Oct call (27 Oct): nudge, rejoin, the
    question asked again -- and 9 times in 13 still nothing, with the key at
    its 65K tokens/minute for Gemini 3.8 Live. Stage 3: a fresh session on the
    fallback model (its own quota), asked again with the recap."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])
    _question(provider, "Is there a hospital nearby?", wait=0.5)
    assert provider.model == FALLBACK
    assert len(rejoined) == 2, "rejoin on 3.8, then the fallback session"
    asked = _client_contents(rejoined[1])
    assert asked and '"Is there a hospital nearby?"' in asked[0]["turns"].parts[0].text
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert stages[:3] == ["nudge", "reconnect", "fallback"]
    fallback = [r for r in rows if r["event"] == "model_fallback"]
    assert fallback and fallback[0]["road"] == "stall" and fallback[0]["using"] == FALLBACK
    assert provider._resume_handle is None, "a handle belongs to the other model's session"


def test_an_answer_after_the_rejoin_keeps_the_configured_model(monkeypatch, fast, rows):
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while len(rejoined) < 1:
            await asyncio.sleep(0.01)
        provider._reply_seen()                   # the answer, after the re-ask
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert provider.model == "gemini-3.8-live" and len(rejoined) == 1
    assert not [r for r in rows if r["event"] == "model_fallback"]


def test_a_bare_turn_complete_answering_the_re_ask_is_no_answer(monkeypatch, fast, rows):
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while len(rejoined) < 1:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        provider._reply_seen("turn_complete")   # the echo of the re-ask
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert provider.model == FALLBACK, "the echo of the re-ask stopped stage 3"


def test_a_quota_refusal_on_resume_opens_the_fresh_session_on_the_fallback(monkeypatch, rows):
    """27 Oct, the same key: "1011 You exceeded your current quota" on the
    resume -- the same model refuses the fresh session too."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, _ = _provider(monkeypatch, [True, False])
    models = []

    async def connect():
        models.append(provider.model)
        if provider.model != FALLBACK:
            raise RuntimeError("1011 None. You exceeded your current quota, please check your plan")
        provider._session = _Session()

    monkeypatch.setattr(provider, "_connect", connect)
    fresh = asyncio.run(provider._connect_resumed_or_fresh("reconnect"))
    assert fresh is True and models == ["gemini-3.8-live", FALLBACK]
    assert [r for r in rows if r["event"] == "model_fallback"][0]["road"] == "reconnect"


def test_a_refused_resume_for_another_reason_stays_on_the_model(monkeypatch, rows):
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, opened, handles = _refusing_provider(monkeypatch)
    fresh = asyncio.run(provider._connect_resumed_or_fresh("reconnect"))
    assert fresh is True and provider.model == "gemini-3.8-live" and len(opened) == 1
    assert not [r for r in rows if r["event"] == "model_fallback"]


def test_the_context_bound_reaches_the_session_config(monkeypatch):
    """LIVE_CONTEXT_COMPRESS_AT: unset = the API default exactly as before
    (80% of the window: 85,000 tokens a turn on the robot, 20 Oct); set = the
    trigger and what is kept, target defaulting to half."""
    from google.genai import types

    from app.providers import gemini

    monkeypatch.setattr(settings, "live_context_compress_at", 0)
    cfg = gemini._compression_config(types)
    assert cfg.trigger_tokens is None and cfg.sliding_window.target_tokens is None
    monkeypatch.setattr(settings, "live_context_compress_at", 24000)
    monkeypatch.setattr(settings, "live_context_keep", 15000)
    cfg = gemini._compression_config(types)
    assert cfg.trigger_tokens == 24000 and cfg.sliding_window.target_tokens == 15000
    monkeypatch.setattr(settings, "live_context_keep", 30000)       # not below the trigger
    assert gemini._compression_config(types).sliding_window.target_tokens == 12000
    monkeypatch.setattr(settings, "live_context_keep", 15000)
    provider, _ = _provider(monkeypatch, [True, False])
    built = provider._build_config()["context_window_compression"]
    assert built.trigger_tokens == 24000


def test_a_failed_fallback_dial_keeps_the_stalled_session(monkeypatch, fast, rows):
    """Review 27 Oct: closing first meant a failed fallback dial left no
    session and no handle, and the call ended -- the stalled 3.8 session had
    still answered 4 times in 13 on the replay. Dialed first; on failure the
    call stays where it was."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def dial(model, handle):
        raise RuntimeError("timed out during opening handshake")

    monkeypatch.setattr(provider, "_dial", dial)
    _question(provider, "Is there a hospital nearby?", wait=0.5)
    assert provider.model == "gemini-3.8-live" and provider._session is rejoined[0]
    assert provider._resume_handle == "handle", "the handle of the live session is kept"
    assert "fallback_failed" in [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert not [r for r in rows if r["event"] == "model_fallback"]


def test_a_tap_during_a_cut_over_waits_for_the_new_socket(monkeypatch, fast, rows):
    """Review 27 Oct: a tap while a cut-over dialed was dropped by send_text
    (no socket) and its watch cancelled the guard mid-connect. It now waits
    for the new socket, and the dial is not cancelled."""
    provider, rejoined = _provider(monkeypatch, [True, False])

    async def run():
        provider._session = None
        provider._reconnecting = True

        async def finish():
            await asyncio.sleep(0.2)
            provider._session = _Session()
            provider._reconnecting = False
            return provider._session

        done = asyncio.create_task(finish())
        await provider.send_text("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        return await done

    new = asyncio.run(run())
    assert _client_contents(new)[0]["turns"].parts[0].text == "ลูกค้าแตะหัวข้อ สระว่ายน้ำ"


def test_a_quota_close_mid_session_reopens_fresh_on_the_fallback(monkeypatch, rows):
    """Review 27 Oct: a socket closed for quota mid-session was resumed on the
    same model -- at the same quota."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, _ = _provider(monkeypatch, [True, False])
    models = []

    async def connect():
        models.append((provider.model, provider._resume_handle))
        provider._session = _Session()

    monkeypatch.setattr(provider, "_connect", connect)
    exc = RuntimeError("1011 None. You exceeded your current quota, please check your plan")
    assert provider._fall_back("drop", str(exc)) is True
    provider._fresh_after_fallback = True
    ok = asyncio.run(provider._reconnect())
    assert ok is True and models == [(FALLBACK, None)]
    assert provider._fresh_pending is True, "nothing was pending: the recap waits for the next ask"
    assert [r for r in rows if r["event"] == "reconnect"][-1]["outcome"] == "fresh"


def test_the_context_floor_is_said_once(monkeypatch, rows):
    monkeypatch.setattr(settings, "live_context_compress_at", 32000)
    monkeypatch.setattr(settings, "live_context_keep", 13000)
    provider, _ = _provider(monkeypatch, [True, False])
    provider._check_context_floor(12300)
    provider._check_context_floor(12300)
    floor = [r for r in rows if r["event"] == "context_floor"]
    assert len(floor) == 1 and floor[0]["keep"] == 13000
    other, _ = _provider(monkeypatch, [True, False])
    monkeypatch.setattr(settings, "live_context_keep", 20000)
    other._check_context_floor(12300)
    assert len([r for r in rows if r["event"] == "context_floor"]) == 1, "20,000 leaves room"


def test_an_answer_during_the_fallback_dial_keeps_the_stalled_session(monkeypatch, fast, rows):
    """Review 27 Oct, 2nd pass: the fallback is dialed first, and the stalled
    socket is still read meanwhile -- its answer can start during the dial.
    Swapping then would cut it off: the new session is closed unused. And the
    dial touches nothing of the call's: a handle the stalled socket sends
    meanwhile is the call's handle afterwards (it used to be reverted)."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])
    dialing = []

    async def dial(model, handle):
        closed = []

        class _NewCm:
            async def __aexit__(self, *exc):
                closed.append(True)

        session = _Session()
        dialing.append((model, handle, session, closed))
        await asyncio.sleep(0.15)                   # the dial takes a moment
        return _NewCm(), session

    monkeypatch.setattr(provider, "_dial", dial)
    seen = {}

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while not dialing:                       # the fallback dial is under way
            await asyncio.sleep(0.005)
        seen["during"] = (provider.model, provider._resume_handle, provider._cm)
        provider._resume_handle = "newer"        # the stalled socket's next handle
        provider._reply_seen()                   # ...and its answer
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert seen["during"][:2] == ("gemini-3.8-live", "handle"), "the dial touched the call's state"
    assert provider.model == "gemini-3.8-live" and provider._session is rejoined[0]
    assert provider._resume_handle == "newer"
    (model, handle, unused, closed), = dialing
    assert (model, handle) == (FALLBACK, None)
    assert closed and _client_contents(unused) == [], "the dialed session closed, nothing asked on it"
    assert "fallback_moot" in [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert not [r for r in rows if r["event"] == "model_fallback"]


class _LoopSession(_Session):
    """A socket the REAL receive loop reads: receive() ends when it is closed
    (by us, or by the server with `fail`)."""
    def __init__(self):
        super().__init__()
        self.closed = asyncio.Event()
        self.error = None

    def fail(self, exc):
        self.error = exc
        self.closed.set()

    async def receive(self):
        await self.closed.wait()
        raise self.error or ConnectionError("closed by us")
        yield  # pragma: no cover - an async generator


class _LoopCm:
    def __init__(self, s):
        self.s = s

    async def __aexit__(self, *exc):
        self.s.closed.set()


QUOTA = "1011 None. You exceeded your current quota, please check your plan"


def _loop_provider(monkeypatch, *, dial_s=0.2):
    """`_provider`, with sockets the real receive loop can read. `sessions`
    records every one opened, as (model, session); a dial that started is
    ("dialing", model) first."""
    provider, _ = _provider(monkeypatch, [True, False])
    sessions = []

    async def connect():
        s = _LoopSession()
        provider._session, provider._cm = s, _LoopCm(s)
        sessions.append((provider.model, s))

    async def dial(model, handle):
        sessions.append(("dialing", model))
        await asyncio.sleep(dial_s)
        s = _LoopSession()
        sessions.append((model, s))
        return _LoopCm(s), s

    monkeypatch.setattr(provider, "_connect", connect)
    monkeypatch.setattr(provider, "_dial", dial)
    return provider, sessions


async def _call(provider, question):
    """The call's first socket, the real receive loop on it, and a question.
    Returns (loop task, ended list)."""
    first = _LoopSession()
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


async def _dial_started(sessions):
    while not any(m == "dialing" for m, _ in sessions):
        await asyncio.sleep(0.005)


def test_a_tap_during_the_fallback_dial_never_ends_the_call(monkeypatch, fast, rows):
    """Review 27 Oct (reproduced on the real receive loop): a tap during the
    stage-3 dial cancelled the guard mid-connect -- no session, no handle, the
    loop gave up, the call was over. The dial now touches nothing of the
    call's: the tap goes out on the socket that is there, the dial it made
    moot is dropped, and the tap's own watch chases it to the fallback."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, sessions = _loop_provider(monkeypatch)
    tap = "ลูกค้าแตะหัวข้อ สระว่ายน้ำ"

    async def run():
        loop, ended = await _call(provider, "Is there a hospital nearby?")
        await _dial_started(sessions)
        await asyncio.sleep(0.05)
        await provider.send_text(tap)            # session._quick_ask, in this order
        provider.watch_text_turn(tap)
        await asyncio.sleep(1.5)
        loop.cancel()
        return ended

    ended = asyncio.run(run())
    assert ended == [], "the receive loop gave up: the call was over"
    assert provider._session is not None and provider.model == FALLBACK
    on_fallback = [s for m, s in sessions if m == FALLBACK and s is provider._session]
    asked = [c for c in _client_contents(on_fallback[0]) if c.get("turns")]
    # one ask, the tap's: the stalled question was the OLDER turn (the
    # 2nd pass: the swap used to ask it on top of the tap)
    assert len(asked) == 1 and asked[0]["turns"].parts[0].text.endswith(tap)


@pytest.mark.parametrize("close", [QUOTA, "1011 None. Internal error encountered."])
def test_the_stalled_socket_closing_during_the_dial_lands_on_the_fallback(
        monkeypatch, fast, rows, close):
    """Review 27 Oct, 2nd pass (real receive loop): the stalled socket closed
    -- for quota, the very case stage 3 is for -- while the fallback dialed.
    The loop found the handle cleared for the dial and ended the call (or
    reconnected on top of the dial). It now waits for the dial and reads the
    session it opened: one fallback session, the question asked once. (Any
    other close: resuming 3.8 instead made the dial moot and dropped the
    question -- nothing was left to ask it again.)"""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, sessions = _loop_provider(monkeypatch, dial_s=0.3)

    async def run():
        loop, ended = await _call(provider, "Is there a hospital nearby?")
        await _dial_started(sessions)
        provider._session.fail(RuntimeError(close))   # the stalled 3.8 rejoin, mid-dial
        await asyncio.sleep(0.8)
        loop.cancel()
        return ended

    ended = asyncio.run(run())
    assert ended == [], "the call ended"
    opened = [s for m, s in sessions if m == FALLBACK]
    assert len(opened) == 1 and provider._session is opened[0], "one fallback session, read"
    asked = [c for c in _client_contents(opened[0]) if c.get("turns")]
    assert len(asked) == 1 and "hospital" in asked[0]["turns"].parts[0].text


def test_the_call_ending_during_the_dial_closes_its_socket_and_opens_none(monkeypatch, fast, rows):
    """Review 27 Oct, 2nd pass: `__aexit__` during the dial closed the
    half-open fallback socket and leaked the stalled one (still open on the
    server, holding one of the key's Live sessions)."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, sessions = _loop_provider(monkeypatch, dial_s=0.3)

    async def run():
        loop, _ = await _call(provider, "Is there a hospital nearby?")
        await _dial_started(sessions)
        stalled = provider._session
        await provider.__aexit__(None, None, None)
        await asyncio.sleep(0.5)
        loop.cancel()
        return stalled

    stalled = asyncio.run(run())
    assert stalled.closed.is_set(), "the stalled socket was left open"
    assert all(s.closed.is_set() for m, s in sessions if m == FALLBACK), \
        "a session opened after the call ended, and stayed open"


def test_closing_stops_every_stall_guard_not_only_the_newest(monkeypatch, fast, rows):
    """Review 27 Oct, 2nd pass: a re-arm while a rejoin dials leaves the
    dialing guard running (`_arm_stall_guard`); `__aexit__` cancelled only the
    newest, and the orphan could open a session after the call ended."""
    provider, _ = _provider(monkeypatch, [True, False])
    monkeypatch.setattr(settings, "reply_stall_s", 5.0)   # neither finishes on its own

    async def run():
        provider._stall_heard = "Is there a hospital nearby?"
        provider._reconnecting = True           # a rejoin is dialing: re-arms do not cancel
        provider._arm_stall_guard()
        first = provider._stall_guard
        provider._arm_stall_guard()
        provider._reconnecting = False
        await provider.__aexit__(None, None, None)
        await asyncio.sleep(0.02)
        # read inside the loop: asyncio.run cancels whatever is left on exit
        return first is not provider._stall_guard, first.done(), provider._stall_guard.done()

    two, first_done, second_done = asyncio.run(run())
    assert two and second_done
    assert first_done, "the guard a re-arm left running outlived the call"


def test_a_quota_close_of_the_rejoin_still_reaches_the_fallback(monkeypatch, fast, rows):
    """Review 27 Oct, 2nd pass: the stage-2 rejoin closed for quota before it
    sent a thing. `just_reconnected` (no reconnect on top of a reconnect that
    yielded nothing) skipped the fallback the quota branch had just chosen,
    and the call ended. A model switch is a new road -- once a call."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, sessions = _loop_provider(monkeypatch)
    plain_connect = provider._connect

    async def connect():
        await plain_connect()
        if provider.model != FALLBACK:          # the 3.8 rejoin, closed before a word
            provider._session.fail(RuntimeError(QUOTA))

    monkeypatch.setattr(provider, "_connect", connect)

    async def run():
        loop, ended = await _call(provider, "Is there a hospital nearby?")
        await asyncio.sleep(0.6)
        loop.cancel()
        return ended

    ended = asyncio.run(run())
    assert ended == [], "the call ended with the fallback unused"
    assert provider.model == FALLBACK and provider._session is not None
    assert [r["road"] for r in rows if r["event"] == "model_fallback"] == ["drop"]
    asked = [c for c in _client_contents(provider._session) if c.get("turns")]
    assert asked and "hospital" in asked[0]["turns"].parts[0].text


def test_a_watch_armed_during_the_rejoin_does_not_cancel_it(monkeypatch, fast, rows):
    """Review 27 Oct: the rejoin closes the old socket first, then dials; a
    watch armed then (a tap whose send gave up waiting for the new socket --
    speech cannot: the mic is dropped while there is no socket) cancelled the
    dialing guard, and the dial with it: no socket, and the call ended. A
    re-arm while `_reconnecting` leaves the dialing guard alone."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    plain_connect = provider._connect

    async def connect():
        await asyncio.sleep(0.15)                   # the rejoin takes a moment
        await plain_connect()

    monkeypatch.setattr(provider, "_connect", connect)

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while not provider._reconnecting:
            await asyncio.sleep(0.005)
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        await asyncio.sleep(0.3)

    asyncio.run(run())
    stall = [r["outcome"] for r in rows if r["event"] == "reconnect" and r.get("road") == "stall"]
    assert stall[:1] == ["resumed"], "the rejoin was cancelled mid-dial"
    assert provider._session is rejoined[0]


def test_a_reask_answered_with_a_tool_and_silent_again_is_not_asked_a_third_time(monkeypatch, fast, rows):
    """Review 27 Oct: the silent-turn ask answered with a tool call (the story
    again) clears the watch, the result re-arms it, and the second silence
    began the chase over -- every 3-4 s until someone spoke, each ask a whole
    prompt. Once per question; the second silence goes the plain way."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    monkeypatch.setattr(settings, "reply_stall_s", 0.15)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 5.0)
    provider, _ = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("ขอดูผังโครงการ")
        await provider.send_audio(QUIET)
        for _ in range(3):
            provider._reply_seen("tool_call")        # present_story (again)
            provider._watch_after_tool()
            provider._reply_seen("turn_complete")    # ...and not a word
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)

    asyncio.run(run())
    stages = [r["stage"] for r in rows if r["event"] == "reply_stall"]
    assert stages.count("silent_turn") == 1, stages
    assert "nudge" in stages, "the plain chain takes over"
    assert len([c for c in _client_contents(old) if c.get("turns")]) == 1


def test_a_turn_complete_right_after_the_result_is_the_tool_turns_own(monkeypatch, fast, rows):
    """3.8 sends turn_complete with the tool call; read after a fast tool's
    result went back it looked like her reply ending silent, and the ask
    would land on a reply about to start."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "AFTER_TOOL_SETTLE_S", 0.3)
    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    monkeypatch.setattr(settings, "reply_stall_s", 5.0)
    provider, _ = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("ขอดูผังโครงการ")
        await provider.send_audio(QUIET)
        provider._reply_seen("tool_call")
        provider._watch_after_tool()
        provider._reply_seen("turn_complete")        # the tool turn's own, read late
        await asyncio.sleep(0.15)

    asyncio.run(run())
    assert not [c for c in _client_contents(old) if c.get("turns")]
    assert not [r for r in rows if r["event"] == "reply_stall"]


def test_a_stray_bracket_holds_nothing_back():
    """Review 27 Oct: "The lobby <" then "is on the ground floor" held the
    rest of the sentence as a possible tag, and dropped it at the turn's end."""
    from app.providers.transcript_filter import TranscriptFilter

    f = TranscriptFilter()
    out = f.feed("The lobby <") + f.feed("is on the ground floor") + f.flush()
    assert "is on the ground floor" in out
    f = TranscriptFilter()
    assert f.feed("We have a {") + f.feed("sky pool and a fitness centre") + f.flush() \
        == "We have a {sky pool and a fitness centre"


def test_a_socket_replaced_during_the_fallback_dial_keeps_the_replacement(monkeypatch, fast, rows):
    """Stage 3's fallback dial is moot when ANOTHER road replaced the socket
    while it dialed (go_away's `_reconnect` finished meanwhile: `_reconnecting`
    False again, `_stall_wait` unchanged). The docstring of
    `_cut_over_to_fallback` lists the case; no test held it -- removing the
    guard left the suite green (audit 27 Oct)."""
    monkeypatch.setattr(settings, "gemini_model_fallback", FALLBACK)
    provider, rejoined = _provider(monkeypatch, [True, False])
    dialing = []

    async def dial(model, handle):
        closed = []

        class _NewCm:
            async def __aexit__(self, *exc):
                closed.append(True)

        session = _Session()
        dialing.append((session, closed))
        await asyncio.sleep(0.15)
        return _NewCm(), session

    monkeypatch.setattr(provider, "_dial", dial)
    replaced = {}

    class _ReplCm:
        async def __aexit__(self, *exc):
            replaced["closed"] = True

    async def run():
        await provider.send_audio(LOUD)
        provider._input_event("Is there a hospital nearby?")
        await provider.send_audio(QUIET)
        while not dialing:
            await asyncio.sleep(0.005)
        # go_away on the stalled socket: the loop's own `_reconnect` resumed
        # the conversation on a new socket and finished, all during the dial
        replaced["session"] = _Session()
        provider._cm, provider._session = _ReplCm(), replaced["session"]
        await asyncio.sleep(0.3)

    asyncio.run(run())
    (unused, closed), = dialing
    assert provider._session is replaced["session"], "the resumed conversation was swapped away"
    assert provider.model == "gemini-3.8-live"
    assert closed and _client_contents(unused) == []
    assert "closed" not in replaced


def test_a_cough_between_the_tapped_tool_call_and_its_result_keeps_the_tap(monkeypatch, fast, rows):
    """Review finding 2 -- an owed tap survives a wordless segment -- in the
    one state the earlier test could not tell: the tap already answered with a
    tool call (which clears `_stall_wait`), a cough, the tool's result, then a
    turn with no word. Reverting the fix left that test green (audit 27 Oct)."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "SILENT_TURN_S", 0.02)
    provider, rejoined = _provider(monkeypatch, [True, False])
    old = provider._session

    async def run():
        provider._replied_since_end = True               # the previous answer played
        provider.watch_text_turn("ลูกค้าแตะหัวข้อ สระว่ายน้ำ")
        provider._reply_seen("tool_call")                # the model calls present_story
        await provider.send_audio(LOUD)                  # a cough near the robot: no words
        await provider.send_audio(QUIET)
        provider._watch_after_tool()                     # the story's result goes back
        provider._reply_seen("turn_complete")            # ...and she ends the turn silent
        await asyncio.sleep(0.3)

    asyncio.run(run())
    texts = [c["turns"].parts[0].text for c in _client_contents(old) if c.get("turns")]
    assert texts == ["ลูกค้าแตะหัวข้อ สระว่ายน้ำ"], (texts, [r.get("stage") for r in rows if r["event"] == "reply_stall"])


def test_on_the_fallback_model_the_nudge_is_the_question_asked_again(monkeypatch, fast, rows):
    """Live 28 Oct: an empty turn_complete=True on a 2.5 native-audio session
    is "1007 Request contains an invalid argument" -- the socket closes. On the
    replay of 20 Oct a stall after the switch to 2.5 dropped the socket that
    way. There stage 1 asks the question again as text; 3.8 keeps its nudge."""
    provider, rejoined = _provider(monkeypatch, [True, False])
    provider.model = FALLBACK
    old = provider._session
    _question(provider, "Is there a hospital nearby?")
    sent = _client_contents(old)
    assert sent and sent[0] != {"turn_complete": True}, "no empty turn on a model that closes on it"
    assert '"Is there a hospital nearby?"' in sent[0]["turns"].parts[0].text
    nudge = [r for r in rows if r["event"] == "reply_stall"][0]
    assert nudge["stage"] == "nudge" and nudge["form"] == "ask"
    from app.providers.gemini import _takes_empty_turn
    assert _takes_empty_turn("gemini-3.8-live") and _takes_empty_turn("models/gemini-3.8-live")
    assert not _takes_empty_turn(FALLBACK) and not _takes_empty_turn("models/" + FALLBACK)
