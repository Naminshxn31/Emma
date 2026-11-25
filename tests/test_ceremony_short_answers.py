"""Live first: short answers + waiting out a long speech (owner 24 Nov ~12:15,
"ปรับสคริปให้มีบทสั้นด้วย แล้วเวลามิกิพูดบางทีอาจจะพูดยาวแล้วค่อยมาถามเกี่ยวกับบทด้วย").

* the live notes ask for the SHORT form of a scripted line (1-3 sentences, ~20-35 words), only in live-first mode;
* the end-of-turn silence is longer for the ceremony call only, with CEREMONY_FOLLOW_HOST on and CEREMONY_FOLLOW_CUES off;
* every other call is the call it was (get_provider is handed no new argument).
"""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app import ceremony, event_host
from app.config import settings
from app.vad_gate import VadGate


def test_the_live_notes_ask_for_the_short_form_and_to_wait():
    for text in (ceremony.FOLLOW_BRIEF_LIVE_TH, ceremony.FOLLOW_PROMPT_LIVE_TH):
        assert "20-35" in text and "แบบย่อ" in text and "ยาวเท่าบทได้" not in text
        assert "รอ" in text                              # waits while he talks to the guests
    assert "20-35" in event_host.SCRIPT_FOLLOW_LIVE_NOTE
    assert "SHORT" in event_host.SCRIPT_FOLLOW_LIVE_NOTE_EN and "20-35" in event_host.SCRIPT_FOLLOW_LIVE_NOTE_EN


def test_the_other_modes_do_not_carry_the_short_form():
    for text in (ceremony.FOLLOW_BRIEF_TH, ceremony.FOLLOW_PROMPT_TH, ceremony.FOLLOW_BRIEF_ALL_TH,
                 ceremony.FOLLOW_PROMPT_ALL_TH, event_host.SCRIPT_FOLLOW_NOTE, event_host.SCRIPT_FOLLOW_NOTE_EN):
        assert "20-35" not in text


def test_the_notes_quote_no_sentence_and_no_project_number():
    import re

    for text in (ceremony.FOLLOW_BRIEF_LIVE_TH, ceremony.FOLLOW_PROMPT_LIVE_TH, event_host.SCRIPT_FOLLOW_LIVE_NOTE):
        assert "'" not in text and '"' not in text and "“" not in text
        assert set(re.findall(r"\d+", text)) <= {"1", "3", "20", "35"}


def test_the_end_silence_is_the_live_first_ceremony_calls_only(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_live_end_silence_ms", 1500)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    assert settings.call_vad_silence_ms("ceremony") == 1500
    for other in ("condo", "event", "emma", "", None):
        assert settings.call_vad_silence_ms(other) is None
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)          # the pressed ceremony
    assert settings.call_vad_silence_ms("ceremony") is None
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_follow_host", False)         # follow off
    assert settings.call_vad_silence_ms("ceremony") is None
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_live_end_silence_ms", 0)     # the default
    assert settings.call_vad_silence_ms("ceremony") is None


def _dial_kwargs(monkeypatch, profile):
    """`VoiceSession.run` end to end with a fake provider: the keyword arguments get_provider was handed."""
    from app import session as session_module
    from tests.test_session_face_identity import DESK_UA, FakeProvider, FakeWS

    seen: list = []
    provider = FakeProvider()
    for key, value in (("gemini_api_key", "test-key"), ("openai_api_key", "test-key"), ("idle_timeout_s", 0),
                       ("canva_url", ""), ("turn_log", False)):
        monkeypatch.setattr(settings, key, value)

    def fake_get_provider(*a, **k):
        seen.append(k)
        return provider

    monkeypatch.setattr(session_module, "get_provider", fake_get_provider)
    sess = session_module.VoiceSession(FakeWS(DESK_UA), profile=profile)
    provider.session = sess

    async def go():
        import asyncio

        await asyncio.wait_for(sess.run(), 10)

    import asyncio

    asyncio.run(go())
    assert len(seen) == 1
    return seen[0]


def test_the_session_dials_the_live_first_ceremony_with_the_end_silence_and_nobody_else(monkeypatch):
    """session.py hands `vad_silence_ms` to get_provider (review 24 Nov: nothing asserted that hand-off -- dropping it
    left the whole suite green and the host's long speech cut at VAD_SILENCE_MS)."""
    monkeypatch.setattr(settings, "ceremony_live_end_silence_ms", 1500)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    assert _dial_kwargs(monkeypatch, "ceremony")["vad_silence_ms"] == 1500
    assert "vad_silence_ms" not in _dial_kwargs(monkeypatch, "condo")           # a sales session: as it was
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)                 # the pressed ceremony: as it was
    assert "vad_silence_ms" not in _dial_kwargs(monkeypatch, "ceremony")


def test_get_provider_is_handed_nothing_new_unless_asked(monkeypatch):
    from app import providers
    from app.providers import gemini

    seen = []

    class Fake:
        def __init__(self, *a, **k):
            seen.append(k)

    monkeypatch.setattr(gemini, "GeminiProvider", Fake)
    providers.get_provider("gemini", "Kore", "i", vad_floor=0.04)
    providers.get_provider("gemini", "Kore", "i", vad_floor=0.04, vad_silence_ms=1500)
    assert "vad_silence_ms" not in seen[0] and seen[1]["vad_silence_ms"] == 1500


def test_the_provider_keeps_its_silence(monkeypatch):
    from app.providers import get_provider

    assert get_provider("gemini", "Kore", "i")._vad_silence_ms is None
    assert get_provider("gemini", "Kore", "i", vad_silence_ms=1500)._vad_silence_ms == 1500


def _clip(name):
    import numpy as np

    with wave.open(str(Path(__file__).parent / "data" / "wake" / name)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2")
    loud = np.where(np.abs(x.astype("float32")) / 32768 > 0.02)[0]
    return x[loud[0]:loud[-1]]


def _turns(silence_ms, pause_s):
    import numpy as np

    from app import vad_gate

    speech = np.concatenate([_clip("emma_sentence.wav"), _clip("other.wav")])
    question = _clip("emma_thai_f.wav")
    stream = np.concatenate([speech, np.zeros(int(pause_s * 16000), "<i2"), question,
                             np.zeros(48000, "<i2")]).tobytes()
    gate = VadGate(vad_gate._make_detector(silence_ms), prefix_padding_ms=300, floor_grace_s=0)
    ends = 0
    for i in range(0, len(stream), 640):
        ends += sum(1 for k, _ in gate.feed(stream[i:i + 640]) if k == "end")
    return ends


def test_a_longer_end_silence_keeps_a_speech_with_pauses_one_turn(monkeypatch):
    if not Path(settings.vad_model).is_file():
        pytest.skip("no silero_vad.onnx -- the end-of-turn silence is UNTESTED with the real detector here")
    monkeypatch.setattr(settings, "vad_min_speech_ms", 250)
    monkeypatch.setattr(settings, "vad_silence_ms", 900)
    assert _turns(900, 1.0) == 2          # today: a 1 s pause ends the turn, her answer would start there
    assert _turns(1500, 1.0) == 1         # live first: the pause stays inside the turn
    assert _turns(1500, 0.5) == 1
    assert _turns(1500, 2.5) == 2         # a real stop is still a stop
