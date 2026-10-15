"""SILENCE_BARE_ACK: a reply that is only "ค่ะ" never reaches the speaker.

Owner, 15 Oct: talk not meant for Emma (staff among themselves) gets silence,
not "ค่ะ" -- 24 of 142 robot turns 12-14 Oct were exactly that. The prompt
cannot ask for silence (3.8 spoke "Silently observe without responding."
aloud), so it keeps "ตอบแค่ ค่ะ" and the provider drops that turn.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.config import settings
from app.providers.base import ProviderEvent
from app.providers.transcript_filter import ACK_MAX_HELD_BYTES, BareAckGate


def _t(text):
    return ProviderEvent(kind="assistant_transcript", text=text)


def _a(n=4800):
    return ProviderEvent(kind="audio", audio=b"\x01\x00" * (n // 2))


def test_a_bare_ack_is_held_and_dropped():
    gate = BareAckGate(True)
    assert gate.push(_t("ค่ะ")) == [] and gate.push(_a()) == []
    assert gate.close() == ([], True)


def test_an_answer_goes_out_at_its_first_words():
    gate = BareAckGate(True)
    first = _t("ที่จอดรถของเรา")
    assert gate.push(first) == [first], "not delayed: the first piece already is not 'ค่ะ'"
    audio = _a()
    assert gate.push(audio) == [audio]
    assert gate.close() == ([], False)


def test_an_answer_opening_with_ka_waits_for_its_next_piece_then_keeps_order():
    gate = BareAckGate(True)
    a, b, c = _t("ค่ะ "), _a(), _t("ที่นี่มีสระว่ายน้ำ")
    assert gate.push(a) == [] and gate.push(b) == []
    assert gate.push(c) == [a, b, c]


def test_long_audio_is_released_whatever_its_transcript():
    gate = BareAckGate(True)
    big = _a(ACK_MAX_HELD_BYTES + 2)
    assert gate.push(big) == [big], "half a second is a 'ค่ะ'; more is a reply"


def test_audio_with_no_transcript_is_released_at_turn_complete():
    gate = BareAckGate(True)
    audio = _a()
    assert gate.push(audio) == []
    assert gate.close() == ([audio], False), "unknown content is never thrown away"


def test_off_is_a_pass_through():
    gate = BareAckGate(False)
    ev = _t("ค่ะ")
    assert gate.push(ev) == [ev] and gate.close() == ([], False)


# ---- through the provider's receive loop ----------------------------------------

def _message(text=None, audio=None, turn_complete=False):
    parts = [types.SimpleNamespace(inline_data=types.SimpleNamespace(data=audio))] if audio else []
    content = types.SimpleNamespace(
        interrupted=False, input_transcription=None,
        output_transcription=types.SimpleNamespace(text=text) if text else None,
        model_turn=types.SimpleNamespace(parts=parts) if parts else None,
        turn_complete=turn_complete)
    return types.SimpleNamespace(session_resumption_update=None, go_away=None, usage_metadata=None,
                                 tool_call_cancellation=None, tool_call=None, server_content=content)


def _run(monkeypatch, messages, on=True):
    from app import turnlog
    from app.providers.gemini import GeminiProvider

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append(event))
    monkeypatch.setattr(settings, "silence_bare_ack", on)
    provider = GeminiProvider("Kore", "x")
    provider.model = "gemini-3.8-live"

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


def test_the_robot_plays_nothing_for_a_bare_ka(monkeypatch):
    kinds, rows = _run(monkeypatch, [_message(text="ค่ะ", audio=b"\x01\x00" * 6000),
                                     _message(turn_complete=True)])
    assert "audio" not in kinds and "assistant_transcript" not in kinds
    assert "turn_complete" in kinds, "the turn still ends: the watchdog and the page see it"
    assert rows.count("bare_ack_dropped") == 1


def test_an_answer_plays_as_before(monkeypatch):
    kinds, rows = _run(monkeypatch, [_message(text="ที่จอดรถของเราอยู่ชั้นใต้ดิน", audio=b"\x01\x00" * 6000),
                                     _message(turn_complete=True)])
    assert "audio" in kinds and "assistant_transcript" in kinds and "turn_complete" in kinds
    assert "bare_ack_dropped" not in rows


@pytest.mark.parametrize("on", [False])
def test_off_by_default_the_bare_ka_plays(monkeypatch, on):
    assert settings.silence_bare_ack is False, "the sales room's PC must not change on a pull"
    kinds, rows = _run(monkeypatch, [_message(text="ค่ะ", audio=b"\x01\x00" * 6000),
                                     _message(turn_complete=True)], on=on)
    assert "audio" in kinds and "bare_ack_dropped" not in rows
