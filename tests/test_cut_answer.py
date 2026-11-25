"""The answer a dropped line cut off (robot 6 Nov 09:13:57: a 1011 2.7 s into the fitness story,
the resume refused, a fresh session with nothing pending, and the guest's question never answered).
Next to tests/test_reply_stall.py's own cases, two with the robot's reply gates on (review 6 Nov):
a reply the noise gate held was never heard and is not asked again as a question; a quiet question
that went through a tool and whose answer was playing is."""
from __future__ import annotations

import asyncio

from app.config import settings
from tests.test_reply_stall import (QUIET, _Feed, _audio, _client_contents,  # noqa: F401
                                    _provider, _said, rows)

SOFT_NOISE = (int(0.03 * 32767)).to_bytes(2, "little", signed=True) * 160
SOFT_ASK = (int(0.02 * 32767)).to_bytes(2, "little", signed=True) * 160
VOICE = (int(0.2 * 32767)).to_bytes(2, "little", signed=True) * 480
Q = "ในฟิตเนสมีอุปกรณ์อะไรบ้าง"


def test_a_reply_the_noise_gate_held_is_not_asked_again_after_a_drop(monkeypatch, rows):
    monkeypatch.setattr(settings, "reply_stall_s", 30.0)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 30.0)
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    monkeypatch.setattr(settings, "noise_reply_near_rms", 0.06)
    from app.providers.transcript_filter import NoiseReplyGate
    provider, _ = _provider(monkeypatch, [True, False])
    provider._noise_gate = NoiseReplyGate(0.06)
    first = _Feed()
    provider._session = first
    opened = []

    async def connect():
        if provider._resume_handle:
            raise RuntimeError("1008 None. BidiGenerateContent session not found or expired")
        provider._session = _Feed()
        opened.append(provider._session)

    monkeypatch.setattr(provider, "_connect", connect)
    played = []

    async def run():
        async def consume():
            async for ev in provider._receive_events():
                if ev.kind in ("audio", "assistant_transcript"):
                    played.append(ev.kind)

        loop = asyncio.create_task(consume())
        await provider.send_audio(SOFT_NOISE)
        provider._input_event("อืม")
        await provider.send_audio(QUIET)
        for msg in (_said("ขอโทษค่ะ เอ็มม่าได้ยินไม่ชัด"), _audio(VOICE)):
            await first.inbox.put(msg)
        await asyncio.sleep(0.05)
        await first.inbox.put(None)
        await asyncio.sleep(0.2)
        loop.cancel()

    asyncio.run(run())
    asked = [c for c in _client_contents(opened[0]) if c.get("turns")] if opened else []
    assert played == [], "the gate held her reply to the noise"
    assert asked == [], "the noise was asked again as a question on the fresh session"


def test_a_quiet_question_answered_through_a_tool_and_cut_is_asked_again(monkeypatch, rows):
    monkeypatch.setattr(settings, "reply_stall_s", 30.0)
    monkeypatch.setattr(settings, "reply_stall_reconnect_s", 30.0)
    monkeypatch.setattr(settings, "gemini_model_fallback", "")
    from app.providers.transcript_filter import NoiseReplyGate
    provider, _ = _provider(monkeypatch, [True, False])
    provider._noise_gate = NoiseReplyGate(0.06)
    first = _Feed()
    provider._session = first
    opened = []

    async def connect():
        if provider._resume_handle:
            raise RuntimeError("1008 None. BidiGenerateContent session not found or expired")
        provider._session = _Feed()
        opened.append(provider._session)

    monkeypatch.setattr(provider, "_connect", connect)

    async def run():
        async def consume():
            async for _ in provider._receive_events():
                pass

        loop = asyncio.create_task(consume())
        await provider.send_audio(SOFT_ASK)
        provider._input_event(Q)
        await provider.send_audio(QUIET)
        provider._reply_seen("tool_call")
        provider._noise_gate.tool_call()
        provider._watch_after_tool()
        for msg in (_said("ฟิตเนส Biogenesis อยู่ชั้น G"), _audio(VOICE)):
            await first.inbox.put(msg)
        await asyncio.sleep(0.05)
        await first.inbox.put(None)
        await asyncio.sleep(0.2)
        loop.cancel()

    asyncio.run(run())
    asked = [c for c in _client_contents(opened[0]) if c.get("turns")]
    assert len(asked) == 1 and Q in asked[0]["turns"].parts[0].text
