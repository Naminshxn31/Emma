"""Staff press a ceremony cue and Emma says its approved line (app/event_cues.py).

What these hold down:
* only an approved, clean cue is ever said; anything else is refused with nothing sent;
* no live call of the robot face = an error, and nobody claims she spoke;
* recorded (only a take a person listened to): the words interleaved with her audio,
  turn_complete last, the model's own output held off the speaker while the line plays
  and let through after it, only a voice the local VAD let in stopping the rest of the
  line, and every reply gate treating the line as a server text turn;
* the line is her turn in the exchange: the ceremony's "addressed" window runs from the
  end of its audio;
* read: exactly `event_script.read_text` (no held-language note), through announce,
  scored with event_voice's scorer, "no_transcript" when there are no words;
* the cue tool (by="model") never announces; its hold stays up while it waits;
* a newer press supersedes an older one still waiting; a double tap is one line;
* every failure road ends the press honestly and gives the announcement lock and
  turn_idle back;
* the staff endpoint is behind the page's key.
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

import pytest

from app import event_clips, event_cues, event_host, event_script, events, robot_face_link, turnlog
from app.config import settings
from app.providers import gemini
from app.providers.base import ProviderEvent
from app.providers.transcript_filter import NAMED_WINDOW_S, NOISE_HOLD_BYTES, BareAckGate
from tests.ceremony_app import ceremony_on
from tests.test_slides import _as_the_live_session

ROOT = Path(__file__).resolve().parent.parent
STAFF_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
#: The running order and the cue list live on the Emma Ceremony app's own page (owner 9 Nov 2026:
#: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน"); its page tests are tests/test_event_order_page.py.
CEREMONY_PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")

GREET = "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่า มีอะไรให้ฉันช่วยหรือไม่คะ"
THANKS = "ขอขอบคุณทุกคนที่เข้ามาในงานวันนี้นะคะ"
SECOND = 24000 * 2                     # bytes of one second of 24 kHz PCM16


def _signed(raw: dict) -> dict:
    """A cue as a person's approval leaves it (scripts/approve_event_script.py
    --listened: the words signed, and the take it was heard in pinned)."""
    raw = dict(raw)
    raw.update(approved_by="tester", approved_at="2026-11-06T12:00:00+07:00",
               approved_sha256=event_script.digest(event_script._normalise(raw)))
    if raw.get("th"):
        raw.setdefault("approved_voice_sha256", "sha-" + raw["id"])
    return raw


@pytest.fixture
def script(tmp_path, monkeypatch):
    """A script file of our own -- never the tracked one (tests never write real data,
    and the real event's voice folder holds rendered clips)."""
    doc = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "cues": [
        _signed({"id": "s1_greet", "scene": 1, "scene_th": "Grand Introduction",
                 "host_th": "(Emma หันหน้าหาคุณมิกิ)", "th": GREET, "numbers": []}),
        {"id": "s1_draft", "scene": 1, "th": "ฉันทำอะไรได้หลายอย่างเลยล่ะค่ะ", "numbers": [],
         "approved_by": None, "approved_at": None, "approved_sha256": None},
        _signed({"id": "s2_bad", "scene": 2, "th": "ไม่มีข้อมูลค่ะ", "numbers": []}),
        _signed({"id": "s4_live", "scene": 4, "th": None, "live_th": "แนะนำห้องวิวทะเล", "numbers": []}),
        _signed({"id": "s8_story", "scene": 8, "th": "ได้เลยค่ะ เชิญทุกท่านรับชมวิดีโอค่ะ",
                 "numbers": [], "clip": "opening"}),
        _signed({"id": "s9_thanks", "scene": 9, "scene_th": "The Grand Premiere", "th": THANKS, "numbers": []}),
        _signed({"id": "s3_unheard", "scene": 3, "th": "ขอบคุณสำหรับคำชมค่ะคุณมิกิ", "numbers": [],
                 "approved_voice_sha256": None}),
    ]}
    path = tmp_path / "floor1_ceremony.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(event_script, "path", lambda: path)
    # the film the s8 cue rolls is "on disk" (event_script.clip_problems)
    monkeypatch.setattr(event_clips, "table",          # as event_clips.table builds a row
                        lambda: {"opening": {"id": "opening", "title_th": "opening", "has_file": True}})
    event_script.reset()
    event_cues.reset()
    yield path
    event_cues.reset()
    event_script.reset()


@pytest.fixture
def voice(monkeypatch):
    """Recordings by cue id (the real app/event_voice.py has its own tests)."""
    recs: dict = {}
    shas: dict = {}

    class Voice:
        @staticmethod
        def clip_sha(c, lang="th"):
            return shas.get(c["id"], "sha-" + c["id"]) if c["id"] in recs else None

        @staticmethod
        def pcm(c, lang="th", base=None, *, sha=None):
            if sha is not None and Voice.clip_sha(c) != sha:
                return None
            return recs.get(c["id"])

        @staticmethod
        def status(c):
            pcm = recs.get(c["id"])
            return {"recorded": pcm is not None, "fidelity": 1.0 if pcm else None,
                    "seconds": None if pcm is None else len(pcm) / SECOND, "why": None if pcm else "no_clip"}

    Voice.shas = shas                       # a take re-rendered since the owner listened
    monkeypatch.setattr(event_cues, "_voice", lambda: Voice)
    return recs


@pytest.fixture
def rows(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def face(monkeypatch):
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)


class FakeProvider:
    """A provider with no recorded-line support: read mode only."""
    plays_recorded = False
    output_sample_rate = 24000

    def __init__(self):
        self.sent: list[str] = []
        self.taps: list = []
        self.unquote: list[bool] = []
        self.named: list[float] = []

    def unquote_next_reply(self, on=True):
        self.unquote.append(on)

    def mark_named(self, at):
        self.named.append(at)

    async def send_text(self, text):
        self.sent.append(text)

    def tap_output(self, fn):
        self.taps.append(fn)
        return lambda: self.taps.remove(fn)

    def feed(self, event):
        for tap in list(self.taps):
            tap(event)


class _Socket:
    """The Live socket under a real GeminiProvider: records what was sent."""
    def __init__(self, script=None):
        self.sent: list[dict] = []
        self.passes = 0
        self.script = script

    async def send_client_content(self, **kw):
        self.sent.append(kw)

    async def send_realtime_input(self, **kw):
        self.sent.append({"realtime": sorted(k for k, v in kw.items() if v is not None)})

    async def receive(self):
        self.passes += 1
        if self.passes == 1 and self.script is not None:
            async for message in self.script():
                yield message


def _msg(text=None, audio=None, turn_complete=False, interrupted=False, tool=None):
    """One Live API message: her words/audio, or a tool call."""
    if tool is not None:
        call = types.SimpleNamespace(function_calls=[types.SimpleNamespace(id=tool, name="present_story", args={})])
        return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                     tool_call_cancellation=None, tool_call=call, server_content=None)
    parts = [types.SimpleNamespace(inline_data=types.SimpleNamespace(data=audio))] if audio else []
    content = types.SimpleNamespace(
        interrupted=interrupted, input_transcription=None,
        output_transcription=types.SimpleNamespace(text=text) if text else None,
        model_turn=types.SimpleNamespace(parts=parts) if parts else None,
        turn_complete=turn_complete)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None, server_content=content)


def _session(provider):
    from app.session import VoiceSession

    sess = VoiceSession(ws=None)
    sess.provider = provider
    return _as_the_live_session(sess)


def _gemini(monkeypatch, *, lead=1000.0):
    monkeypatch.setattr(gemini, "RECORDED_LEAD_S", lead)       # 1000 = no pacing: fast tests
    monkeypatch.setitem(gemini._NOTE_STATE, "stalled", False)
    provider = gemini.GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"
    provider._session = _Socket()
    return provider


def _no_reconnect(provider, monkeypatch):
    async def reconnect():
        return False

    monkeypatch.setattr(provider, "_reconnect", reconnect)


def _drain(provider) -> list:
    out = []
    while not provider._event_queue.empty():
        out.append(provider._event_queue.get_nowait())
    return out


async def _settle(cond, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


def _cue_rows(rows):
    return [r for r in rows if r["event"] == "event_cue"]


def _pcm(seconds=1.0, value=b"\x10\x27"):
    return value * int(24000 * seconds)


def _turns_started(socket) -> list:
    return [s for s in socket.sent if s.get("turn_complete") is True]


async def _read_all(provider, into):
    async for event in provider._receive_events():
        into.append(event)


# ---- what may be said ------------------------------------------------------------------

@pytest.mark.parametrize("cue_id, why", [("s1_draft", "not_approved"), ("s2_bad", "problems"),
                                         ("s4_live", "live_only"), ("nope", "no_cue"), ("", "no_cue")])
def test_only_an_approved_clean_line_is_ever_said(script, voice, rows, face, cue_id, why, monkeypatch):
    """With a live call registered: "nothing sent" here is the gate, not the missing call."""
    voice["s1_draft"] = voice["s2_bad"] = _pcm()
    provider = FakeProvider()
    _session(provider)
    announced = []

    async def announce(*a, **k):
        announced.append(a)
        return True

    monkeypatch.setattr(events, "announce", announce)
    result = asyncio.run(event_cues.play(cue_id))
    assert result["ok"] is False and result["error"] == why and result["error_th"]
    assert provider.sent == [] and announced == []
    assert _cue_rows(rows)[-1]["error"] == why
    assert not event_cues.STATE["runs"]


def test_no_call_is_said_to_staff_and_she_is_not_claimed_to_have_spoken(script, voice, rows, monkeypatch):
    voice["s1_greet"] = _pcm()
    result = asyncio.run(event_cues.play("s1_greet"))
    assert result == {"ok": False, "error": "no_call", "error_th": event_cues.ERRORS_TH["no_call"]}
    # a page that is not the robot face's is no call either
    provider = FakeProvider()
    _session(provider)
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: False)
    assert asyncio.run(event_cues.play("s1_greet"))["error"] == "no_call"
    assert provider.sent == []
    assert [r["error"] for r in _cue_rows(rows)] == ["no_call", "no_call"]


def test_a_clip_on_the_big_screen_keeps_the_line_in(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm()
    event_clips.STATE["phase"] = event_clips.PLAYING
    try:
        result = asyncio.run(event_cues.play("s1_greet"))
    finally:
        event_clips.STATE["phase"] = event_clips.IDLE
    assert result["error"] == "quiet"
    assert _drain(provider) == [] and provider._recorded is None


def test_a_quiet_probe_that_fails_is_not_quiet_and_the_line_plays(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s9_thanks"] = _pcm(0.3)

    def broken():
        raise RuntimeError("robot_face_link unreadable")

    monkeypatch.setattr(gemini, "_walk_quiet", broken)

    async def go():
        assert (await event_cues.play("s9_thanks"))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert b"".join(e.audio for e in _drain(provider) if e.kind == "audio") == _pcm(0.3)


# ---- whose voice ---------------------------------------------------------------------------

def test_a_take_nobody_listened_to_is_read_live_never_played(script, voice, rows, face, monkeypatch):
    """Approval signs the words, not the audio: a --fresh render after it would swap
    the stage audio unheard. No `approved_voice_sha256` for this take = read live --
    on a provider that COULD play it."""
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s3_unheard"] = _pcm()
    sent = []

    async def announce(text, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(events, "announce", announce)
    monkeypatch.setattr(event_cues, "READ_TURN_S", 0.05)
    result = asyncio.run(event_cues.play("s3_unheard"))
    assert result == {"ok": True, "mode": "read"}
    assert sent == [event_script.read_text(event_script.cue("s3_unheard"))]
    assert not [e for e in _drain(provider) if e.kind == "audio"] and provider._recorded is None
    assert _cue_rows(rows)[-1]["voice_why"] == "voice_not_approved"


def test_a_take_other_than_the_one_listened_to_is_not_played(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s9_thanks"] = _pcm(0.3)
    event_cues._voice().shas["s9_thanks"] = "sha-a-fresh-render"   # re-rendered since the owner listened
    sent = []

    async def announce(text, **kw):
        sent.append(text)
        return True

    monkeypatch.setattr(events, "announce", announce)
    monkeypatch.setattr(event_cues, "READ_TURN_S", 0.05)
    result = asyncio.run(event_cues.play("s9_thanks"))
    assert result["mode"] == "read" and sent == [event_script.read_text(event_script.cue("s9_thanks"))]
    assert not [e for e in _drain(provider) if e.kind == "audio"]
    assert _cue_rows(rows)[-1]["voice_why"] == "voice_not_approved"
    # ...and the cue tool says it itself rather than play it
    event_cues.reset()
    assert asyncio.run(event_cues.play("s9_thanks", by="model"))["mode"] == "say_yourself"
    assert provider._recorded is None


# ---- read mode -------------------------------------------------------------------------

def test_without_a_recording_the_model_reads_exactly_the_line_and_is_scored(script, voice, rows, face):
    provider = FakeProvider()
    _session(provider)
    c = event_script.cue("s1_greet")

    async def go():
        async def her_first_words():          # the press answers on her first sound (review 7 Nov)
            assert await _settle(lambda: provider.sent and provider.taps)
            provider.feed(ProviderEvent(kind="assistant_transcript", text="สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่า "))
            provider.feed(ProviderEvent(kind="audio", audio=_pcm(0.5)))

        opening = asyncio.create_task(her_first_words())
        result = await event_cues.play("s1_greet")
        await opening
        assert result == {"ok": True, "mode": "read"}
        assert provider.sent == [event_script.read_text(c)]
        assert event_cues.STATE["runs"], "her turn for the line is still being listened to"
        provider.feed(ProviderEvent(kind="assistant_transcript", text="มีอะไรให้ฉันช่วยหรือไม่คะ"))
        provider.feed(ProviderEvent(kind="turn_complete"))
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    row = _cue_rows(rows)[-1]
    assert row["mode"] == "read" and row["ok"] is True and row["fidelity"] == 1.0
    assert row["seconds"] == 0.5 and isinstance(row["ms"], int)
    assert row["numbers_missing"] == row["numbers_extra"] == 0 and "why" not in row
    # the guest's words are never in the row; ours are not either (numbers only)
    assert GREET not in json.dumps(row, ensure_ascii=False)
    assert provider.taps == [], "the tap is removed once her turn ended"
    assert provider.unquote == [True, False], "the « » are kept out of her caption for that turn only"
    assert provider.named, "her reading was her turn in the exchange"


def test_a_read_cue_carries_no_held_language_note(script, voice, rows, face, monkeypatch):
    """A language pinned from the menu makes every server line say "answer only in
    English" -- after READ_RULE's "Thai, never translate" that is two orders in one turn."""
    from app import talk_lang

    provider = FakeProvider()
    provider._talk = talk_lang.TalkLanguage()
    assert provider._talk.pin("en")
    assert provider._talk.held_note(), "the menu's pin is held (else this test checks nothing)"
    _session(provider)
    monkeypatch.setattr(event_cues, "READ_TURN_S", 0.05)
    asyncio.run(event_cues.play("s9_thanks"))
    assert provider.sent == [event_script.read_text(event_script.cue("s9_thanks"))]


def test_a_reading_with_no_transcript_is_not_measured_not_zero(script, voice, rows, face):
    """3.8 leaves the output transcript out of ~40% of turns (lead, 6 Nov): audio and
    no words is "no_transcript" with fidelity null -- never 0 for a line maybe said right."""
    provider = FakeProvider()
    _session(provider)

    async def go():
        async def her():                      # the press answers on her first sound
            assert await _settle(lambda: provider.sent and provider.taps)
            provider.feed(ProviderEvent(kind="audio", audio=_pcm(0.5)))
            provider.feed(ProviderEvent(kind="turn_complete"))

        reading = asyncio.create_task(her())
        assert (await event_cues.play("s1_greet"))["mode"] == "read"
        await reading
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    row = _cue_rows(rows)[-1]
    assert row["ok"] is True and row["why"] == "no_transcript"
    assert "fidelity" in row and row["fidelity"] is None
    assert "numbers_missing" not in row


def test_a_reading_is_scored_by_the_voice_files_own_scorer(script, voice, rows, face, monkeypatch):
    """event_voice.line_fidelity (numbers said in Thai words count as said): the bar
    the recordings were kept by is the bar the live reading is measured by."""
    provider = FakeProvider()
    _session(provider)
    asked = []

    class Voice:
        @staticmethod
        def clip_sha(c, lang="th"):
            return None

        @staticmethod
        def status(c):
            return {"recorded": False}

        @staticmethod
        def line_fidelity(heard, line):
            asked.append((heard, line))
            return 0.42

        @staticmethod
        def unspoken_numbers(heard, line):
            return [155.0]

    monkeypatch.setattr(event_cues, "_voice", lambda: Voice)

    async def go():
        async def her():                      # the press answers on her first sound
            assert await _settle(lambda: provider.sent and provider.taps)
            provider.feed(ProviderEvent(kind="assistant_transcript", text="ขอขอบคุณทุกคนค่ะ"))
            provider.feed(ProviderEvent(kind="audio", audio=_pcm(0.2)))
            provider.feed(ProviderEvent(kind="turn_complete"))

        reading = asyncio.create_task(her())
        await event_cues.play("s9_thanks")
        await reading
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert asked == [("ขอขอบคุณทุกคนค่ะ", THANKS)]
    row = _cue_rows(rows)[-1]
    assert row["fidelity"] == 0.42 and row["numbers_missing"] == 1


def test_a_read_turn_that_never_went_out_disarms_the_quote_strip(script, voice, rows, face, monkeypatch):
    provider = FakeProvider()
    _session(provider)
    real = events.announce

    async def failing(text, *, still_relevant=None, **kw):
        assert still_relevant()                      # armed, as announce does right before sending
        return False                                 # ...and the send failed

    monkeypatch.setattr(events, "announce", failing)
    assert asyncio.run(event_cues.play("s9_thanks"))["error"] == "not_sent"
    assert provider.unquote == [True, False] and provider.taps == []
    monkeypatch.setattr(events, "announce", real)


def test_read_mode_goes_through_announce_as_an_event_cue(script, voice, rows, face, monkeypatch):
    provider = FakeProvider()
    _session(provider)
    seen = []
    real = events.announce

    async def spy(text, **kw):
        seen.append((text, kw["source"], kw["held_language"]))
        return await real(text, **kw)

    monkeypatch.setattr(events, "announce", spy)
    monkeypatch.setattr(event_cues, "READ_TURN_S", 0.05)
    asyncio.run(event_cues.play("s9_thanks"))
    assert seen == [(event_script.read_text(event_script.cue("s9_thanks")), "event_cue", False)]


def test_offline_with_no_recording_is_refused_not_read(script, voice, rows, face):
    provider = FakeProvider()
    provider.model = "offline"
    _session(provider)
    assert asyncio.run(event_cues.play("s1_greet"))["error"] == "offline"
    assert provider.sent == []


def test_the_quote_marks_of_a_read_cue_stay_out_of_her_caption_for_that_turn_only(rows, monkeypatch):
    provider = _gemini(monkeypatch)

    async def model():
        yield _msg(text="«สวัสดีค่ะคุณมิกิ", audio=b"\x01\x00" * 2400)
        yield _msg(text=" ฉันชื่อเอ็มม่า»", turn_complete=True)
        yield _msg(text="ราคา «เริ่มต้น»", audio=b"\x01\x00" * 2400)    # the turn after
        yield _msg(turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    turns, current = [], []

    async def go():
        provider.unquote_next_reply()
        async for event in provider._receive_events():
            if event.kind == "assistant_transcript":
                current.append(event.text)
            elif event.kind == "turn_complete":
                turns.append("".join(current))
                current.clear()

    asyncio.run(go())
    assert turns[0] == "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่า"
    assert "«" in turns[1], "only the reading turn: nothing else of hers is rewritten"


def test_a_reading_cut_off_before_a_word_ends_the_strip(rows, monkeypatch):
    provider = _gemini(monkeypatch)

    async def model():
        yield _msg(interrupted=True)                 # the reading turn, cut before its first word
        yield _msg(text="«ต่อไป»", audio=b"\x01\x00" * 2400, turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    said = []

    async def go():
        provider.unquote_next_reply()
        async for event in provider._receive_events():
            if event.kind == "assistant_transcript":
                said.append(event.text)

    asyncio.run(go())
    assert "«" in "".join(said)


# ---- the cue tool ------------------------------------------------------------------------

def test_the_cue_tool_never_announces_and_without_a_recording_hands_the_line_back(
        script, voice, rows, face, monkeypatch):
    provider = FakeProvider()
    _session(provider)

    async def announce(*a, **k):
        raise AssertionError("the model is mid-turn: a cue from its tool must never announce")

    monkeypatch.setattr(events, "announce", announce)
    result = asyncio.run(event_cues.play("s1_greet", by="model"))
    assert result == {"ok": True, "mode": "say_yourself", "line": GREET}
    assert provider.sent == [] and not event_cues.STATE["runs"]
    assert _cue_rows(rows)[-1]["mode"] == "say_yourself"


def test_the_cue_tools_recorded_line_holds_the_model_at_once_and_follows_the_tool_result(
        script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm()

    async def announce(*a, **k):
        raise AssertionError("never announce from inside a tool call")

    monkeypatch.setattr(events, "announce", announce)

    async def go():
        result = await event_cues.play("s1_greet", by="model")
        assert result == {"ok": True, "mode": "recorded"}
        assert provider._recorded is not None, "the hold is up before the tool's result goes back"
        await asyncio.sleep(0.2)
        assert _drain(provider) == [], "nothing before the tool batch has finished"
        provider._tool_rounds += 1                  # run_tools' finally: the result went back
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    assert b"".join(e.audio for e in out if e.kind == "audio") == voice["s1_greet"]
    assert out[-1].kind == "turn_complete"
    assert not _turns_started(provider._session), "no turn was started"
    assert _cue_rows(rows)[-1]["mode"] == "recorded" and _cue_rows(rows)[-1]["by"] == "model"


def test_the_cue_tool_does_not_cut_a_line_being_said(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    event_cues.STATE["runs"][99] = {"id": "s9_thanks", "phase": "speaking"}
    voice["s1_greet"] = _pcm()
    assert asyncio.run(event_cues.play("s1_greet", by="model"))["error"] == "busy"
    assert provider._recorded is None


def test_a_superseded_cue_tool_line_lowers_its_hold(script, voice, rows, face, monkeypatch):
    """Waiting for the lock, a newer press came: the model run ends superseded and its
    hold goes with it -- or the model's answers stayed off the speaker for its bound."""
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm(0.3)

    async def go():
        await events._lock.acquire()                 # a scene's line is being said
        assert (await event_cues.play("s1_greet", by="model"))["mode"] == "recorded"
        assert provider._recorded is not None
        await asyncio.sleep(0.2)
        event_cues.STATE["seq"] += 1                 # a newer press (staff)
        events._lock.release()
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert provider._recorded is None, "the superseded run's hold is still up"
    assert _cue_rows(rows)[-1]["error"] == "superseded"
    assert not events._lock.locked()


def test_the_cue_tools_hold_is_kept_up_while_it_waits(script, voice, rows, face, monkeypatch):
    """Her voice (≤15 s) and the lock (≤45 s) can outlast the hold's own bound: the
    waiting press keeps it up, and the line still plays held."""
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm(0.2)
    monkeypatch.setattr(gemini, "RECORDED_HOLD_SLACK_S", 0.3)
    monkeypatch.setattr(gemini, "RECORDED_TOOL_WAIT_S", 0.0)
    real_sleep = asyncio.sleep

    async def keep(provider_, report):
        while True:
            await real_sleep(0.05)
            if not provider_.refresh_recorded(report):
                return

    monkeypatch.setattr(event_cues, "_keep_hold", keep)

    async def go():
        await events._lock.acquire()
        await event_cues.play("s1_greet", by="model")
        await asyncio.sleep(1.0)                      # longer than the hold's own bound
        assert provider._recording_now() is not None, "the hold lapsed while the line waited"
        events._lock.release()
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert _cue_rows(rows)[-1]["ok"] is True


def test_a_hold_that_lapsed_before_the_line_is_an_honest_not_started(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm(0.2)
    monkeypatch.setattr(gemini, "RECORDED_HOLD_SLACK_S", 0.1)
    monkeypatch.setattr(gemini, "RECORDED_TOOL_WAIT_S", 0.0)

    async def dead(provider_, report):                # the keeper died
        return None

    monkeypatch.setattr(event_cues, "_keep_hold", dead)

    async def go():
        await events._lock.acquire()
        await event_cues.play("s1_greet", by="model")
        await asyncio.sleep(0.6)
        events._lock.release()
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    row = _cue_rows(rows)[-1]
    assert row["error"] == "not_started" and row["why"] == "hold_lapsed", row
    assert not [e for e in _drain(provider) if e.kind == "audio"]


def test_the_lock_wait_is_bounded_and_says_so(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(0.2)
    monkeypatch.setattr(event_cues, "LOCK_WAIT_S", 0.2)

    async def go():
        await events._lock.acquire()                 # never released in time
        result = await event_cues.play("s9_thanks")
        events._lock.release()
        return result

    result = asyncio.run(go())
    assert result["error"] == "not_started"
    assert _cue_rows(rows)[-1]["why"] == "lock_timeout"
    assert sess.turn_idle.is_set()


# ---- recorded mode ---------------------------------------------------------------------

def test_a_recorded_line_is_her_reply_words_interleaved_turn_complete_last(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s1_greet"] = _pcm(1.0)

    async def go():
        result = await event_cues.play("s1_greet")
        assert result == {"ok": True, "mode": "recorded"}
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    kinds = [e.kind for e in out]
    assert kinds[0] == "assistant_transcript" and kinds[-1] == "turn_complete"
    assert kinds.count("turn_complete") == 1 and "interrupted" not in kinds
    said = "".join(e.text for e in out if e.kind == "assistant_transcript")
    assert said == GREET
    assert b"".join(e.audio for e in out if e.kind == "audio") == voice["s1_greet"]
    # interleaved: words keep arriving between the audio pieces, not all up front
    first_audio = kinds.index("audio")
    last_words = max(i for i, k in enumerate(kinds) if k == "assistant_transcript")
    assert last_words > first_audio
    # by default nothing goes to the model after it (EVENT_CUE_CONTEXT_NOTE off): the
    # fact rides beside tool results -- the id, never the line
    assert provider._session.sent == []
    assert sess.turn_idle.is_set() and not events._lock.locked()
    note = event_cues.last_said_note()
    assert "s1_greet" in note and GREET not in note
    # ...and kept for her live window (24 Nov, app/ceremony.py `recent_cues_th`): the id and the time, by a whole line only
    assert [e["id"] for e in event_cues.STATE["said_log"]] == ["s1_greet"]
    row = _cue_rows(rows)[-1]
    assert row["mode"] == "recorded" and row["ok"] and row["seconds"] == 1.0 and "noted" not in row
    assert isinstance(row["ms"], int) and "fidelity" not in row


def test_the_line_just_said_rides_beside_tool_results(script, voice, rows, face, monkeypatch):
    """Beside the ceremony app's tool results (event_host.ceremony_note), by id; never beside
    the Event app's (its scene note) -- owner 9 Nov, "ไม่งั้นจะปนกัน"."""
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(0.3)
    assert event_host.scene_note() is None and event_host.ceremony_note() is None

    async def go():
        await event_cues.play("s9_thanks")
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert event_host.ceremony_note() is None            # not the ceremony app's call: nothing
    ceremony_on(replies="named", session=sess)
    note = event_host.ceremony_note()
    assert "s9_thanks" in note and THANKS not in note and event_host.CEREMONY_RULE in note
    monkeypatch.setattr(event_host, "current", lambda: {"title_th": "รับแขก", "brief_th": "", "stops": []})
    assert "s9_thanks" not in event_host.scene_note() and "รับแขก" in event_host.scene_note()
    monkeypatch.setattr(event_cues, "SAID_NOTE_S", 0.0)
    assert "s9_thanks" not in event_host.ceremony_note()


def test_the_context_note_is_behind_its_switch(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s1_greet"] = _pcm(0.3)
    monkeypatch.setattr(settings, "event_cue_context_note", True)

    async def go():
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    note = provider._session.sent[-1]
    assert note["turn_complete"] is False
    assert note["turns"].parts[0].text == event_cues.NOTE_SAID % GREET
    assert _cue_rows(rows)[-1]["noted"] is True


def test_the_models_own_turn_is_held_during_the_line_and_let_through_after(
        script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch, lead=0.0)            # paced: the line takes ~0.5 s
    _session(provider)
    voice["s1_greet"] = _pcm(0.5)

    async def model():
        assert await _settle(lambda: provider._recorded is not None and provider._recorded.get("pushing"))
        yield _msg(text="ได้เลยค่ะ ", audio=b"\x01\x00" * 2400)   # her own answer, mid-line
        yield _msg(turn_complete=True)
        assert await _settle(lambda: provider._recorded is None)
        yield _msg(text="ต่อไปค่ะ", audio=b"\x02\x00" * 2400)     # a turn after the line
        yield _msg(turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    received = []

    async def go():
        reader = asyncio.create_task(_read_all(provider, received))
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.wait_for(reader, 5)

    asyncio.run(go())
    model_audio = b"".join(e.audio for e in received if e.kind == "audio")
    assert b"\x01\x00" not in model_audio, "the model's answer under the line reached the speaker"
    assert model_audio == b"\x02\x00" * 2400, "a turn after the line plays as ever"
    assert "".join(e.text for e in received if e.kind == "assistant_transcript") == "ต่อไปค่ะ"
    assert [e.kind for e in received].count("turn_complete") == 1, "the held turn ends nothing on the page"
    line = _drain(provider)
    assert b"".join(e.audio for e in line if e.kind == "audio") == voice["s1_greet"]
    held = [r for r in rows if r["event"] == "event_cue_model_held"]
    assert held and held[0]["audio_ms"] == 100 and "text" not in held[0]


def test_a_held_turn_that_ends_after_the_line_ends_with_an_empty_turn_end(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch, lead=0.0)
    _session(provider)
    voice["s1_greet"] = _pcm(0.4)

    async def model():
        assert await _settle(lambda: provider._recorded is not None and provider._recorded.get("pushing"))
        yield _msg(text="ค่ะ", audio=b"\x01\x00" * 2400)          # begun under the line
        assert await _settle(lambda: provider._recorded is None)
        yield _msg(text=" ได้เลย", audio=b"\x01\x00" * 2400)      # the same turn, after it
        yield _msg(turn_complete=True)
        yield _msg(text="ใหม่", audio=b"\x02\x00" * 2400, turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    received = []

    async def go():
        reader = asyncio.create_task(_read_all(provider, received))
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.wait_for(reader, 5)

    asyncio.run(go())
    kinds = [e.kind for e in received]
    assert b"".join(e.audio for e in received if e.kind == "audio") == b"\x02\x00" * 2400
    assert kinds.count("turn_complete") == 2, "the held turn's end passes (empty), then the new turn's"


def test_the_models_own_interruption_does_not_cut_the_line(script, voice, rows, face, monkeypatch):
    """Only a voice the local VAD let in cuts the line: the model being interrupted
    is not the room speaking (review 6 Nov)."""
    provider = _gemini(monkeypatch, lead=0.0)
    _session(provider)
    voice["s1_greet"] = _pcm(0.5)

    async def model():
        assert await _settle(lambda: provider._recorded is not None and provider._recorded.get("pushing"))
        yield _msg(text="ได้", audio=b"\x01\x00" * 2400)
        yield _msg(interrupted=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    received = []

    async def go():
        reader = asyncio.create_task(_read_all(provider, received))
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.wait_for(reader, 5)

    asyncio.run(go())
    assert "interrupted" not in [e.kind for e in received], "it would stop the line on the page"
    line = _drain(provider)
    assert b"".join(e.audio for e in line if e.kind == "audio") == voice["s1_greet"]
    assert "interrupted" not in [e.kind for e in line]
    assert not _cue_rows(rows)[-1].get("cut")


def test_a_tool_called_under_the_line_still_runs(script, voice, rows, face, monkeypatch):
    """The hold is on her voice and words only: a stop the host asked for over the line
    must still reach its tool."""
    provider = _gemini(monkeypatch, lead=0.0)
    _session(provider)
    voice["s1_greet"] = _pcm(0.4)

    async def model():
        assert await _settle(lambda: provider._recorded is not None and provider._recorded.get("pushing"))
        yield _msg(text="หยุด", audio=b"\x01\x00" * 2400)
        yield _msg(tool="call-1")

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)

    async def go():
        reader = asyncio.create_task(_read_all(provider, []))
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.wait_for(reader, 5)

    asyncio.run(go())
    assert provider._tool_batches.qsize() == 1 and "call-1" in provider._pending_tool_ids


def test_a_guest_voice_stops_the_rest_of_the_line(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch, lead=0.0)
    _session(provider)
    voice["s1_greet"] = _pcm(1.0)

    class Vad:
        open_rms = 0.2
        in_speech = False

        def feed(self, pcm):
            return [("start", b"")]

    provider._vad_gate = Vad()

    async def quiet(**kw):
        return True

    monkeypatch.setattr(provider, "_send_input", quiet)

    async def go():
        assert (await event_cues.play("s1_greet"))["ok"]
        await provider.send_audio(b"\x00\x00" * 320)      # somebody speaks over her
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    kinds = [e.kind for e in out]
    sent = b"".join(e.audio for e in out if e.kind == "audio")
    assert 0 < len(sent) < len(voice["s1_greet"]), "the rest of the line kept coming after the barge-in"
    last_audio = max(i for i, k in enumerate(kinds) if k == "audio")
    assert "interrupted" in kinds[last_audio:] and kinds[-1] == "turn_complete"
    row = _cue_rows(rows)[-1]
    assert row["cut"] is True and row["ok"] is True


def test_her_old_answer_still_playing_is_stopped_first_and_its_queued_tail_dropped(
        script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(0.3)
    monkeypatch.setattr(event_cues, "VOICE_WAIT_S", 0.1)
    provider._event_queue.put_nowait(ProviderEvent(kind="audio", audio=b"\x07\x00" * 2400))   # not yet sent
    provider._event_queue.put_nowait(ProviderEvent(kind="assistant_transcript", text="ห้องว่าง"))
    provider._event_queue.put_nowait(ProviderEvent(kind="user_transcript", text="x"))

    async def go():
        sess._play_until = time.monotonic() + 30.0     # a long answer still on the page
        await event_cues.play("s9_thanks")
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    assert b"\x07\x00" not in b"".join(e.audio for e in out if e.kind == "audio")
    assert "ห้องว่าง" not in "".join(e.text or "" for e in out if e.kind == "assistant_transcript")
    assert [e.kind for e in out][:2] == ["user_transcript", "interrupted"], "the guest's words keep their place"
    assert _cue_rows(rows)[-1]["purged_ms"] == 100


def test_a_line_the_page_stopped_taking_is_partial_not_ok(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(1.0)
    monkeypatch.setattr(gemini, "RECORDED_PUT_WAIT_S", 0.1)
    for _ in range(provider._event_queue.maxsize - 3):      # nobody is reading: 3 places left
        provider._event_queue.put_nowait(ProviderEvent(kind="usage", data={}))

    async def go():
        result = await event_cues.play("s9_thanks")
        assert await _settle(lambda: not event_cues.STATE["runs"])
        return result

    asyncio.run(go())
    row = _cue_rows(rows)[-1]
    assert row["ok"] is False and row["error"] == "not_sent" and row["partial"] is True
    assert row["why"] == "put_timeout"
    assert sess.turn_idle.is_set() and not events._lock.locked()
    assert event_cues.last_said_note() is None, "a fragment is not 'said'"
    assert event_cues.STATE["said_log"] == [], "...nor kept for her live window"


def test_a_press_cancelled_mid_line_closes_her_turn_and_gives_everything_back(
        script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch, lead=0.0)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(1.0)

    async def go():
        await event_cues.play("s9_thanks")
        await asyncio.sleep(0.15)
        for task in list(event_cues._tasks):
            task.cancel()
        assert await _settle(lambda: not event_cues.STATE["runs"])
        await asyncio.sleep(0.05)

    asyncio.run(go())
    kinds = [e.kind for e in _drain(provider)]
    assert kinds[-2:] == ["interrupted", "turn_complete"], kinds
    assert sess.turn_idle.is_set() and not events._lock.locked()
    assert provider._recorded is None


def test_a_hold_past_its_bound_holds_nothing(rows, monkeypatch):
    """A hold nobody lowered must not leave a mute robot."""
    provider = _gemini(monkeypatch)
    provider._recorded = {"until": time.monotonic() - 1.0, "seconds": 1.0, "cut": False, "pushing": False,
                          "tools_at": 0, "report": {}}

    async def model():
        yield _msg(text="ค่ะ ได้เลย", audio=b"\x03\x00" * 2400, turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    received = []
    asyncio.run(_read_all(provider, received))
    assert b"".join(e.audio for e in received if e.kind == "audio") == b"\x03\x00" * 2400
    assert provider._recorded is None


def test_a_held_turn_whose_end_never_came_does_not_hold_her_for_good(script, rows, monkeypatch):
    """The socket dropped under a held turn: no turn_complete ever came for it. The
    next reply, seconds later, is hers to say -- not held as part of that turn."""
    provider = _gemini(monkeypatch)
    provider._model_held_at = time.monotonic() - gemini.RECORDED_HELD_GAP_S - 1.0
    provider._model_out_at = provider._model_held_at

    async def model():
        yield _msg(text="ค่ะ ที่จอดรถอยู่ชั้นใต้ดิน", audio=b"\x03\x00" * 2400)
        yield _msg(turn_complete=True)

    provider._session = _Socket(model)
    _no_reconnect(provider, monkeypatch)
    received = []
    asyncio.run(_read_all(provider, received))
    assert b"".join(e.audio for e in received if e.kind == "audio") == b"\x03\x00" * 2400
    assert not provider.model_streaming()


def test_every_reply_gate_treats_the_line_as_a_server_text_turn(script, voice, rows, face, monkeypatch):
    """A ceremony (only her name earns a reply), a quiet unclear sound just heard
    (NOISE_REPLY_NEAR_RMS), SILENCE_BARE_ACK on, a question being watched
    (REPLY_STALL_S), a tainted transcript budget, a follow-up pending: the line
    goes out whole."""
    monkeypatch.setattr(settings, "noise_reply_near_rms", 0.06)
    provider = _gemini(monkeypatch)
    provider._ack_gate = BareAckGate(True)
    sess = _session(provider)
    voice["s1_greet"] = _pcm(1.0)
    ceremony_on(replies="named", session=sess)           # the ceremony app's call
    assert event_host.ceremony_now()
    provider._noise_gate.segment(0.02)
    provider._noise_gate.heard("อ่ะ")                 # a quiet sound nobody made out, not her name
    provider._stall_wait = time.monotonic()          # the watchdog is waiting for an answer
    provider._stall_heard = "อ่ะ"
    provider._out_budget.allow("**planning**")       # the budget is tainted for the model's turn
    sess._answer_pending = True                      # her answer's follow-up would be owed

    async def go():
        assert (await event_cues.play("s1_greet"))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    assert "".join(e.text for e in out if e.kind == "assistant_transcript") == GREET
    assert b"".join(e.audio for e in out if e.kind == "audio") == voice["s1_greet"]
    assert not [r for r in rows if r["event"] in ("noise_reply_dropped", "bare_ack_dropped")]
    assert provider._stall_wait is None, "the watchdog would chase the host past the line"
    assert sess._answer_pending is False, "the follow-up timer would ask after a ceremony line"
    assert provider._replied_since_end is True


def test_a_cue_line_opens_the_exchange_from_the_end_of_its_audio(script, voice, rows, face, monkeypatch):
    """Review 6 Nov (reviewer 2): after any recorded cue the host's next unnamed line
    ("ว้าว คุณฉลาดมาก...") was refused -- the line went out as a server turn and
    nothing refreshed "she was addressed". It is her turn in the exchange: the window
    runs from when its audio ENDS."""
    monkeypatch.setattr(settings, "noise_reply_near_rms", 0.06)
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s1_greet"] = _pcm(3.0)
    ceremony_on(replies="named", session=sess)           # the named-only ceremony app's call

    async def go():
        await event_cues.play("s1_greet")
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    gate = provider._noise_gate
    ends = gate._named_at
    assert ends >= time.monotonic() + 1.5, "measured from when it was generated, not when it ends"
    gate.segment(0.2)
    gate.heard("ว้าว คุณฉลาดมากเลยนะเนี่ย")
    first = gemini.ProviderEvent(kind="assistant_transcript", text="ขอบคุณค่ะ")
    assert gate.push(first) == [first], "the host's next line in the exchange was refused"
    gate.close()
    # ...and the window closes as before, NAMED_WINDOW_S after her audio ended
    monkeypatch.setattr("app.providers.transcript_filter.time.monotonic", lambda: ends + NAMED_WINDOW_S + 1)
    gate.segment(0.2)
    gate.heard("ขอบคุณแขกทุกท่านครับ")
    assert gate.push(gemini.ProviderEvent(kind="assistant_transcript", text="ค่ะ")) == []
    assert gate.push(gemini.ProviderEvent(kind="audio", audio=b"\x01\x00" * (NOISE_HOLD_BYTES // 2))) == []


def test_a_cue_with_a_clip_rolls_it_after_the_line(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s8_story"] = _pcm(0.5)
    calls = []

    async def play_clip(clip_id, *, by="staff", lead_in=False):
        calls.append((clip_id, by, lead_in, provider._recorded is None, events._lock.locked()))
        return {"ok": True}

    monkeypatch.setattr(event_host, "play_clip", play_clip)

    async def go():
        assert (await event_cues.play("s8_story"))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert calls == [("opening", "staff", True, True, False)], "the film after her line, her last words first"
    assert _cue_rows(rows)[-1]["clip"] == "opening"


def test_a_film_that_did_not_start_is_on_the_cues_answer_not_only_in_its_row(script, voice, rows, face,
                                                                             monkeypatch):
    """Review 7 Nov (B4): the cue ended "ok" (her line was said) and the film's failure
    was in the row alone -- the running order, reading the cue's last answer, could not
    tell staff the video never came up."""
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s8_story"] = _pcm(0.3)
    outcome = {"ok": False, "error": "no_screen", "error_th": event_clips.ERRORS_TH["no_screen"]}

    async def play_clip(clip_id, *, by="staff", lead_in=False):
        return dict(outcome)

    monkeypatch.setattr(event_host, "play_clip", play_clip)

    async def go():
        assert (await event_cues.play("s8_story"))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    last = event_cues.STATE["last"]["s8_story"]
    assert last["ok"] is True and last["error"] is None, "her line was said"
    assert last["clip"] == {"id": "opening", "ok": False, "error": "no_screen",
                            "error_th": event_clips.ERRORS_TH["no_screen"]}
    row = _cue_rows(rows)[-1]
    assert row["clip"] == "opening" and row["clip_ok"] is False and row["clip_error"] == "no_screen"
    # ...and one that started says so the same way
    outcome.clear()
    outcome["ok"] = True
    asyncio.run(go())
    assert event_cues.STATE["last"]["s8_story"]["clip"] == {"id": "opening", "ok": True, "error": None,
                                                            "error_th": None}
    assert _cue_rows(rows)[-1]["clip_ok"] is True


# ---- one at a time, and what waits ---------------------------------------------------

def test_a_newer_press_supersedes_one_still_waiting_for_her_voice(script, voice, rows, face, monkeypatch):
    provider = FakeProvider()
    sess = _session(provider)

    async def her():                                 # her reading of the line that went out
        assert await _settle(lambda: provider.sent and provider.taps)
        provider.feed(ProviderEvent(kind="audio", audio=_pcm(0.2)))
        provider.feed(ProviderEvent(kind="turn_complete"))

    async def go():
        sess.turn_idle.clear()                       # she is answering something
        first = asyncio.create_task(event_cues.play("s1_greet"))
        # wait for the first press to be registered, not a fixed 0.15 s: a cold
        # `_pick` took longer than that on its own, the "double tap" registered first
        # and the test waited on itself (agent A, 7 Nov)
        assert await _settle(lambda: any(r["id"] == "s1_greet" for r in event_cues.STATE["runs"].values()))
        again = await event_cues.play("s1_greet")
        assert again["error"] == "already", "a double tap is one line, not two"
        second = asyncio.create_task(event_cues.play("s9_thanks"))
        result = await asyncio.wait_for(first, 2)
        assert result["error"] == "superseded"
        sess.turn_idle.set()                         # her answer is over
        reading = asyncio.create_task(her())
        assert (await asyncio.wait_for(second, 3))["ok"]
        await reading
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert provider.sent == [event_script.read_text(event_script.cue("s9_thanks"))]
    assert [r.get("error") for r in _cue_rows(rows)] == ["already", "superseded", None]


def test_a_double_tap_while_the_recording_is_read_is_one_line(script, voice, rows, face, monkeypatch):
    """The second tap lands while the first is still reading its recording from the
    robot's disk: it must find the first press, not start a second line."""
    import threading

    provider = _gemini(monkeypatch)
    _session(provider)
    real = event_cues._voice()
    entered, release = threading.Event(), threading.Event()

    class Slow(real):
        @staticmethod
        def clip_sha(c, lang="th"):
            entered.set()
            release.wait(3)                            # the first press is inside its read
            return "sha-" + c["id"]

        @staticmethod
        def pcm(c, lang="th", base=None, *, sha=None):
            return _pcm(0.3)

    monkeypatch.setattr(event_cues, "_voice", lambda: Slow)

    async def go():
        first = asyncio.create_task(event_cues.play("s9_thanks"))
        assert await _settle(entered.is_set)
        try:
            second = await asyncio.wait_for(event_cues.play("s9_thanks"), 1.0)
        finally:
            release.set()
        assert second["error"] == "already"
        assert (await asyncio.wait_for(first, 3))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    out = _drain(provider)
    assert [e.kind for e in out].count("turn_complete") == 1
    assert b"".join(e.audio for e in out if e.kind == "audio") == _pcm(0.3)


def test_a_press_waits_for_her_voice_to_play_out(script, voice, rows, face, monkeypatch):
    provider = _gemini(monkeypatch)
    sess = _session(provider)
    voice["s9_thanks"] = _pcm(0.5)

    async def go():
        sess._play_until = time.monotonic() + 0.4      # her last answer is still playing
        t0 = time.monotonic()
        assert (await event_cues.play("s9_thanks"))["ok"]
        assert time.monotonic() - t0 >= 0.35
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())
    assert "interrupted" not in [e.kind for e in _drain(provider)], "she finished: nothing to cut"


@pytest.mark.parametrize("busy", ["streaming", "tool"])
def test_a_press_waits_for_a_model_turn_or_a_tool_in_flight(script, voice, rows, face, monkeypatch, busy):
    """A turn still streaming would be cut into the line; a tool still running would
    show its result on the face mid-line."""
    provider = _gemini(monkeypatch)
    _session(provider)
    voice["s9_thanks"] = _pcm(0.3)

    async def go():
        if busy == "streaming":
            provider._model_out_at = time.monotonic()
        else:
            provider._pending_tool_ids.add("call-9")
        press = asyncio.create_task(event_cues.play("s9_thanks"))
        await asyncio.sleep(0.4)
        assert not press.done() and not _drain(provider), "the line started over the model"
        provider._model_out_at = None
        provider._pending_tool_ids.clear()
        assert (await asyncio.wait_for(press, 3))["ok"]
        assert await _settle(lambda: not event_cues.STATE["runs"])

    asyncio.run(go())


def test_a_tap_during_the_line_waits_for_it_and_is_then_answered(script, voice, rows, face, monkeypatch):
    """Sent at once, the model's answer to a tap would begin under the line and be held
    to its end: the guest would get nothing, and the watchdog would count the held
    output as the answer. It goes when the line is over -- and is watched from then."""
    monkeypatch.setattr(settings, "reply_stall_s", 30.0)
    provider = _gemini(monkeypatch, lead=0.0)
    _session(provider)
    voice["s1_greet"] = _pcm(0.6)

    async def go():
        await event_cues.play("s1_greet")
        provider.watch_text_turn("แตะ: ห้องตัวอย่าง")          # session._quick_ask watches first
        t0 = time.monotonic()
        await provider.send_text("แตะ: ห้องตัวอย่าง")
        assert time.monotonic() - t0 < 0.1, "a tap arrives on the mic pump: it must not wait there"
        assert not _turns_started(provider._session), "sent under the line"
        assert provider._stall_wait is None, "its guard would nudge the model mid-line"
        assert await _settle(lambda: _turns_started(provider._session), 3)
        assert provider._recorded is None, "sent before the line was over"
        assert provider._stall_wait is not None, "watched again from when it really went out"
        assert await _settle(lambda: not event_cues.STATE["runs"])
        provider._stall_wait = None
        for guard in list(provider._stall_guards):
            guard.cancel()

    asyncio.run(go())
    sent = _turns_started(provider._session)
    assert len(sent) == 1 and sent[0]["turns"].parts[0].text == "แตะ: ห้องตัวอย่าง"
    assert [r["event"] for r in rows if r["event"].startswith("event_cue_text")] == [
        "event_cue_text_deferred", "event_cue_text_sent"]


# ---- the offline provider ---------------------------------------------------------------

def test_offline_plays_a_recording_the_same_way(monkeypatch):
    from app.providers import offline

    monkeypatch.setattr(offline, "_own_gate", lambda vad_floor: None)
    provider = offline.OfflineProvider("Despina", "x")
    report = {}
    assert asyncio.run(provider.play_recorded(GREET, _pcm(1.0), lang="th", report=report)) is True
    out = []
    while not provider._events.empty():
        out.append(provider._events.get_nowait())
    kinds = [e.kind for e in out]
    assert kinds[0] == "assistant_transcript" and kinds[-1] == "turn_complete"
    assert "".join(e.text for e in out if e.kind == "assistant_transcript") == GREET
    assert b"".join(e.audio for e in out if e.kind == "audio") == _pcm(1.0)
    assert report["seconds"] == 1.0 and "first_at" in report and report["closed"]
    assert provider._asked_until is not None, "her line ends on คะ: an answer is expected"


def test_a_provider_that_cannot_play_a_recording_says_so():
    from app.providers.base import VoiceProvider

    assert VoiceProvider.plays_recorded is False
    assert gemini.GeminiProvider.plays_recorded is True


# ---- app/event_voice.py: what the cues ask of it -------------------------------------------

def test_the_voice_files_format_check_is_cached_on_the_file(tmp_path, monkeypatch):
    """The staff page polls status() every 10 s: on the robot that re-read every WAV."""
    from app import event_voice

    event_voice.reset()
    wav = tmp_path / "clip.wav"
    event_voice.write_wav(wav, _pcm(0.2))
    reads = []
    real = event_voice.read_wav
    monkeypatch.setattr(event_voice, "read_wav", lambda f: reads.append(f) or real(f))
    assert event_voice._plays(wav) and event_voice._plays(wav)
    assert len(reads) == 1
    event_voice.write_wav(wav, _pcm(0.3))              # a new take: its size changed
    assert event_voice._plays(wav) and len(reads) == 2
    event_voice.reset()


def test_clip_sha_is_the_take_pcm_would_play(monkeypatch):
    from app import event_voice

    entry = {"sha256": "abc", "seconds": 1.0}
    monkeypatch.setattr(event_voice, "_judge", lambda c, lang, base: (None, entry, Path("x.wav"), 0.99))
    monkeypatch.setattr(event_voice, "_pinned", lambda f, sha: sha == "abc")
    monkeypatch.setattr(event_voice, "_plays", lambda f: True)
    assert event_voice.clip_sha({"id": "a"}) == "abc"
    monkeypatch.setattr(event_voice, "_pinned", lambda f, sha: False)       # bytes not the pinned ones
    assert event_voice.clip_sha({"id": "a"}) is None
    monkeypatch.setattr(event_voice, "_judge", lambda c, lang, base: ("below_bar", entry, None, 0.5))
    assert event_voice.clip_sha({"id": "a"}) is None
    # pcm(sha=...) plays only that take
    monkeypatch.setattr(event_voice, "_judge", lambda c, lang, base: (None, entry, Path("x.wav"), 0.99))
    assert event_voice.pcm({"id": "a"}, sha="another") is None


# ---- the staff page ---------------------------------------------------------------------

def test_the_staff_endpoint_is_behind_the_key_and_routes_an_id(script, voice, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    calls = []

    async def play(cue_id, *, by="staff"):
        calls.append((cue_id, by))
        return {"ok": False, "error": "no_call"}

    monkeypatch.setattr(event_cues, "play", play)
    voice["s1_greet"] = voice["s3_unheard"] = _pcm()
    from tests.ceremony_app import ceremony_on

    ceremony_on()            # the Emma Ceremony app on the robot (owner 9 Nov: its own app and page)
    with TestClient(event_control_server.app) as client:
        assert client.post("/ceremony/cue", json={"id": "s1_greet"}).status_code == 403
        assert calls == []
        client.get("/ceremony-control?key=staff-secret")
        assert client.post("/ceremony/cue", json={"id": "s1_greet"}).json()["error"] == "no_call"
        assert calls == [("s1_greet", "staff")]
        assert client.post("/event/cue", json={"id": "s1_greet"}).status_code == 404   # not the event's any more
        assert "cues" not in client.get("/event/state").json()
        state = client.get("/ceremony/state").json()
    cues = {c["id"]: c for c in state["cues"]["cues"]}
    assert set(cues) == {"s1_greet", "s1_draft", "s2_bad", "s4_live", "s8_story", "s9_thanks", "s3_unheard"}
    assert cues["s1_greet"]["ready"] and cues["s1_greet"]["recorded"] and cues["s1_greet"]["voice_approved"]
    assert cues["s3_unheard"]["recorded"] and not cues["s3_unheard"]["voice_approved"]
    assert not cues["s1_draft"]["ready"] and cues["s1_draft"]["recorded"] is False
    assert cues["s2_bad"]["problems"] and cues["s4_live"]["kind"] == "live"
    assert state["cues"]["call"] is False


# ---- the staff page: the running order (ลำดับพิธี) --------------------------------------

def _staff_js(start: str, end: str = "\n  }\n") -> str:
    """One piece of the staff page's script, from `start` to its closing brace."""
    body = STAFF_PAGE[STAFF_PAGE.index(start):]
    return body[:body.index(end) + len(end)]


def _ceremony_js(start: str, end: str = "\n  }\n") -> str:
    body = CEREMONY_PAGE[CEREMONY_PAGE.index(start):]
    return body[:body.index(end) + len(end)]


def _order_section() -> str:
    return CEREMONY_PAGE[CEREMONY_PAGE.index("// ---- the running order"):CEREMONY_PAGE.index("// ---- the poll")]


def test_the_staff_page_reads_the_servers_real_answer_paths(tmp_path, monkeypatch, rows):
    """Review 7 Nov: the page read r.mode / r.walk at the top while app/event_order.py answers
    {ok, n, i, do, result}. Here the REAL event_order (over test_event_order's own script) and
    the REAL event_cues._with_show build the answers, and the page must read exactly those
    paths -- a renamed field goes red here, not at the ceremony."""
    from app import event_order, event_walk
    from tests import test_event_order as order_tests        # its script and event file, not its fixtures

    from tests.ceremony_app import ceremony_on

    target = tmp_path / "floor1_event.json"
    target.write_text(order_tests.EVENT.read_text(encoding="utf-8"), encoding="utf-8")
    script = tmp_path / "floor1_ceremony.json"
    script.write_text(json.dumps(order_tests.SCRIPT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(script))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(settings, "stories_enabled", False)       # the picture is off: a warning, not a block
    monkeypatch.setattr(event_cues, "_live_call", lambda: object())

    async def play(cue_id, *, by="staff", show=None):
        # the line started; the picture missed it -- in event_cues' own words
        return event_cues._with_show({"show": show, "show_error": "not_said"}, {"ok": True, "mode": "read"})

    monkeypatch.setattr(event_cues, "play", play)

    def step(snap, n, i):
        sc = next(s for s in snap["scenes"] if s["n"] == n)
        return next(st for st in sc["steps"] if st["i"] == i)

    for mod in (event_host, event_script, event_cues, event_order):
        mod.reset()
    ceremony_on()                                      # the ceremony app on the robot
    try:
        before = step(asyncio.run(event_order.snapshot()), 2, 0)
        assert before["ready"] is True and before["why_th"].startswith("ภาพไม่ขึ้น")
        out = asyncio.run(event_order.press(2, 0))
        assert out["ok"] is True and (out["n"], out["i"], out["do"]) == (2, 0, "cue")
        assert out["result"]["mode"] == "read"
        assert out["result"]["show"]["ok"] is False and out["result"]["show"]["error_th"]
        mark = step(asyncio.run(event_order.snapshot()), 2, 0)["mark"]
        assert mark["state"] != "failed" and mark["error_th"].startswith("ภาพไม่ขึ้น")
        stale = asyncio.run(event_order.next((1, 0)))
        assert stale["ok"] is False and stale["error"] == "stale" and stale["error_th"]
    finally:
        for mod in (event_host, event_script, event_cues, event_order):
            mod.reset()
    outcome = _ceremony_js("function orderOutcome(")
    assert "var res = r.result || {};" in outcome and "CUE_MODE[res.mode]" in outcome
    assert "walkMessage(res)" in outcome and "pictureNote(res)" in outcome
    assert "r.error === 'stale'" in outcome and "r.error_th" in outcome
    picture = _ceremony_js("function pictureNote(")
    assert "res.show" in picture and "show.ok === false" in picture and "show.error_th" in picture
    assert "walkMessage(r)" not in _order_section()                  # never the top level again
    row = _ceremony_js("function stepRow(")
    assert "if (st.why_th && st.ready) {" in row and "var warn = markWarn(mark);" in row


def test_the_stop_answer_has_the_fields_the_staff_page_reads(rows, monkeypatch):
    """POST /event/stop also stops the arm and cuts her ceremony line (app/event_control_server.py):
    {ok, stopped, body: {sent, error_th, note_th}, line_cut} -- the fields stopMessage reads."""
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    with TestClient(event_control_server.app) as client:
        client.get("/event-control?key=staff-secret")
        out = client.post("/event/stop").json()
    assert out["ok"] is True and isinstance(out["stopped"], bool) and isinstance(out["line_cut"], bool)
    assert {"sent", "error_th", "note_th"} <= set(out["body"])
    assert out["body"]["sent"] is False and out["body"]["error_th"]     # no robot face here: said why


def test_the_stop_toast_says_what_stop_reached():
    """The walk, the arm, her line: each said by its own field, none assumed -- and a
    refused or unreachable stop always ends at the E-stop."""
    msg = _staff_js("function stopMessage(")
    for path in ("r.stopped", "var body = r && r.body;", "body.sent", "body.note_th", "body.error_th",
                 "r.line_cut", "'E-stop'"):
        assert path in msg, path
    # the arm's part is said even when the walk's stop failed: not an early return
    assert "return" not in msg[:msg.index("var body = r && r.body;")].replace("function stopMessage(", "")
    assert "toast(stopMessage(r), 8000);" in _staff_js("$('stopBtn').onclick", "};\n")
