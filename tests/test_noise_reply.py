"""NOISE_REPLY_NEAR_RMS: her reply to a quiet sound nobody could make out is
never played (owner 29 Oct: "อะไรที่ฟังไม่ชัดก็ไม่ต้องตอบ ให้คิดว่าเป็น noise แต่ถ้า
เสียงดังอยู่ใกล้ แต่ฟังไม่ทันก็บอกหรือถามใหม่ ... ส่วนเสียงที่ไกลหรือฟังไม่ชัดก็ข้ามไป").

Replay of the robot logs 9-27 Oct (610 segments let through, 958 replies):
the replies the rule drops were "โอเค"/"ครับ"/"อื้อ"/"de" answered with an
offer or "ได้ยินไม่ชัด"; the replies with a tool call or from a voice at the
robot (>= 0.06) -- a show_unit, a story, "by?" -> hospitals -- all kept.
"""
from __future__ import annotations

import asyncio
import types

from app.config import settings
from app.providers.base import ProviderEvent
from app.providers.transcript_filter import NOISE_HOLD_BYTES, NoiseReplyGate


def _t(text):
    return ProviderEvent(kind="assistant_transcript", text=text)


def _a(n=4800):
    return ProviderEvent(kind="audio", audio=b"\x01\x00" * (n // 2))


def _gate(rms, heard="", near=0.06):
    g = NoiseReplyGate(near)
    g.segment(rms)
    g.heard(heard)
    return g


# ---- the rule -------------------------------------------------------------------

def test_a_quiet_sound_made_nothing_of_gets_no_reply():
    for heard in ("", "de", "โอเค", "อื้อ", "1", "对的。"):
        g = _gate(0.03, heard)
        assert g.push(_t("ขอโทษค่ะ เอ็มม่าได้ยินไม่ชัด")) == [] and g.push(_a(NOISE_HOLD_BYTES)) == []
        owed, dropped = g.close()
        assert owed == [] and dropped is not None, heard


def test_a_loud_voice_not_caught_still_gets_asked_again():
    """near and loud but not caught: prompt rule 3's "please say it again" plays"""
    g = _gate(0.08, "")
    first = _t("ขอโทษค่ะ เอ็มม่าได้ยินไม่ชัด")
    assert g.push(first) == [first]
    assert g.close() == ([], None)


def test_words_her_name_a_what_or_a_tool_always_get_their_reply():
    for heard in ("ที่จอดรถมีกี่คัน", "Emma", "เอ็มม่า", "What?", "อะไร", "pardon", "ห้ะ"):
        g = _gate(0.02, heard)
        first = _t("x")
        assert g.push(first) == [first], heard
    g = _gate(0.02, "by?")
    g.tool_call()                                   # "by?" -> hospitals, 14 Oct
    first = _t("Hospitals are about 6 to 13 kilometres away")
    assert g.push(first) == [first]


def test_a_reply_to_a_server_text_or_a_tap_is_never_judged():
    """Replay 15 Oct: a tapped story's first words came just after a 0.023 sound."""
    g = NoiseReplyGate(0.06)
    g.text_turn()                                   # the tap (send_text)
    g.tool_call()                                   # present_story
    g.segment(0.023)                                # a sound before her first word
    first = _t("Our facilities are spread")
    assert g.push(first) == [first]
    g2 = NoiseReplyGate(0.06)
    g2.text_turn()                                  # a greeting / an announcement
    g2.segment(0.02)
    hello = _t("Hello, I'm Emma.")
    assert g2.push(hello) == [hello]


def test_a_question_in_two_segments_is_one_question():
    g = _gate(0.03, "ที่จอดรถ")
    g.segment(0.025)                                # a second short segment
    g.heard("อืม")
    first = _t("ที่จอดรถของเรา")
    assert g.push(first) == [first]


def test_a_sound_over_her_reply_starts_a_new_heard():
    g = _gate(0.08, "ที่จอดรถมีกี่คันคะ")
    answer = _t("ที่จอดรถของเรา")
    assert g.push(answer) == [answer]
    g.segment(0.02)                                 # a cough over her answer
    g.reset()                                       # ...interrupted her
    assert g.push(_t("ขอโทษค่ะ ได้ยินไม่ชัด")) == []
    assert g.close()[1] is not None


def test_the_reply_waits_for_a_late_caption_and_is_released_when_it_comes():
    """2% of captions land after her first audio: she waits, the caption arrives."""
    g = _gate(0.03, "")
    audio = _a()
    assert g.push(audio) == []
    g.heard("ที่จอดรถมีกี่คันคะ")
    text = _t("ที่จอดรถของเรา")
    assert g.push(text) == [audio, text]


def test_an_empty_turn_keeps_what_was_heard_for_the_reply_after_it():
    """gemini-3.8 completes a turn before its tool call, then replies."""
    g = _gate(0.03, "")
    assert g.close() == ([], None)                  # nothing said: not judged, not used up
    g.tool_call()
    first = _t("x")
    assert g.push(first) == [first]


def test_off_is_a_pass_through():
    g = _gate(0.02, "", near=0.0)
    ev = _t("ขอโทษค่ะ")
    assert g.push(ev) == [ev] and g.close() == ([], None)
    assert settings.noise_reply_near_rms == 0.0, "the sales room's PC must not change on a pull"


# ---- through the provider ---------------------------------------------------------

def _message(text=None, audio=None, turn_complete=False, heard=None):
    parts = [types.SimpleNamespace(inline_data=types.SimpleNamespace(data=audio))] if audio else []
    content = types.SimpleNamespace(
        interrupted=False,
        input_transcription=types.SimpleNamespace(text=heard, finished=None) if heard else None,
        output_transcription=types.SimpleNamespace(text=text) if text else None,
        model_turn=types.SimpleNamespace(parts=parts) if parts else None,
        turn_complete=turn_complete)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None, server_content=content)


def _provider(monkeypatch, near=0.06):
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(settings, "noise_reply_near_rms", near)
    provider = GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"
    return provider


def _run(monkeypatch, provider, messages):
    from app import turnlog

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))

    class Session:
        def __init__(self):
            self.passes = 0

        async def receive(self):
            self.passes += 1
            if self.passes == 1:
                for m in messages:
                    yield m

    provider._session = Session()

    async def reconnect():
        return False

    monkeypatch.setattr(provider, "_reconnect", reconnect)
    kinds = []

    async def go():
        async for event in provider._receive_events():
            kinds.append(event.kind)

    asyncio.run(go())
    return kinds, rows


def test_the_robot_plays_nothing_for_a_quiet_unclear_sound(monkeypatch):
    provider = _provider(monkeypatch)
    provider._noise_gate.segment(0.03)
    kinds, rows = _run(monkeypatch, provider, [
        _message(heard="de"),
        _message(text="ขอโทษค่ะ เอ็มม่าได้ยินไม่ชัด", audio=b"\x01\x00" * 6000),
        _message(turn_complete=True)])
    assert "audio" not in kinds and "assistant_transcript" not in kinds
    assert "turn_complete" in kinds, "the turn still ends: the watchdog and the page see it"
    assert ("noise_reply_dropped", {"rms": 0.03, "letters": 2}) in rows


def test_an_answer_to_words_plays_as_before(monkeypatch):
    provider = _provider(monkeypatch)
    provider._noise_gate.segment(0.03)
    kinds, rows = _run(monkeypatch, provider, [
        _message(heard="ที่จอดรถมีกี่คันคะ"),
        _message(text="ที่จอดรถของเราอยู่ชั้นใต้ดิน", audio=b"\x01\x00" * 6000),
        _message(turn_complete=True)])
    assert "audio" in kinds and "assistant_transcript" in kinds
    assert not [r for r in rows if r[0] == "noise_reply_dropped"]


def test_a_vad_start_tells_the_gate_how_loud_it_was(monkeypatch):
    provider = _provider(monkeypatch)

    class Vad:
        open_rms = 0.041

        def feed(self, pcm):
            return [("start", b"")]

    provider._vad_gate = Vad()
    provider._session = object()

    async def quiet(**kw):
        return None

    monkeypatch.setattr(provider, "_send_input", quiet)
    asyncio.run(provider.send_audio(b"\x00\x00" * 320))
    assert provider._noise_gate._heard["rms"] == 0.041


def test_send_text_marks_the_next_reply_as_the_servers(monkeypatch):
    provider = _provider(monkeypatch)
    provider._noise_gate.segment(0.02)
    provider._session = None                      # dropped on the floor, still marked
    asyncio.run(provider.send_text("ทักลูกค้า"))
    assert provider._noise_gate._heard["server"] is True


def test_the_vad_gate_keeps_the_opening_rms():
    import inspect

    from app.vad_gate import VadGate

    assert "self.open_rms: float | None = None" in inspect.getsource(VadGate.__init__)
    feed = inspect.getsource(VadGate.feed)
    opened = feed[feed.index("self.segments += 1"):]
    assert "self.open_rms = rms" in opened[:opened.index('actions.append(("start"')]
