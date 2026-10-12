"""ENHANCE_ENABLED (app/enhance.py): off by default, loud when it cannot run,
transparent on clean speech, and placed after the source switch and before
the VAD in the session. The verdict numbers come from scripts/eval_enhance.py.
"""
from __future__ import annotations

import asyncio
import math
from pathlib import Path

import numpy as np
import pytest

from app import enhance
from app.config import settings
from tests import array_room as R

MODEL = Path(__file__).resolve().parent.parent / "data" / "enhance" / "gtcrn_simple.onnx"


@pytest.fixture(autouse=True)
def _fresh():
    enhance.reset_warnings()
    yield
    enhance.reset_warnings()


def test_off_by_default_means_no_enhancer():
    assert settings.enhance_enabled is False
    assert enhance.for_session(16000) is None


def test_a_missing_model_is_said_out_loud(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(settings, "enhance_enabled", True)
    monkeypatch.setattr(settings, "enhance_model", str(tmp_path / "nope.onnx"))
    assert enhance.for_session(16000) is None
    assert "fetch_enhance_model.py" in caplog.text and "NOT enhanced" in caplog.text
    caplog.clear()
    assert enhance.for_session(24000) is None          # OpenAI's 24 kHz: GTCRN is 16 kHz
    assert "24000" in caplog.text


@pytest.fixture
def denoiser():
    if not MODEL.is_file():
        pytest.skip("no GTCRN model at data/enhance/ (python scripts/fetch_enhance_model.py): the real "
                    "denoiser's transparency on clean speech was NOT checked")
    d = enhance.make_denoiser(MODEL)
    assert d is not None
    return d


def test_the_real_denoiser_keeps_clean_speech_and_the_stream_length(denoiser):
    clean = R.speech(R.TARGET_CLIPS, 2 * R.FS, np.random.default_rng(1))
    pcm = np.clip(np.round(clean * 32768), -32768, 32767).astype("<i2")
    e = enhance.Enhancer(denoiser)
    outs = [e.process(pcm[k:k + 320].tobytes()) for k in range(0, len(pcm), 320)]
    assert any(o == b"" for o in outs)                  # whole 256-sample shifts: some frames come back empty
    y = np.frombuffer(b"".join(outs), "<i2").astype(float) / 32768
    assert len(pcm) - 256 <= len(y) <= len(pcm)          # nothing lost but the last partial shift
    x = clean[: len(y)]
    alpha = float(np.dot(y, x) / np.dot(x, x))
    si_sdr = 10 * math.log10(np.sum((alpha * x) ** 2) / np.sum((y - alpha * x) ** 2))
    assert si_sdr > 25.0, si_sdr


def test_the_session_enhances_after_the_source_and_before_the_vad(monkeypatch):
    """Placement: the provider (and so the VAD) gets the enhancer's bytes;
    an empty return sends nothing that frame."""
    from app.session import VoiceSession
    from tests.test_array_audio import _session

    class Fake:
        def __init__(self):
            self.n = 0

        def process(self, pcm):
            self.n += 1
            return b"" if self.n % 2 else pcm[::-1]

    fake = Fake()
    monkeypatch.setattr(settings, "enhance_enabled", True)
    monkeypatch.setattr(enhance, "for_session", lambda rate: fake)
    frames = [bytes([k, 0]) * 160 for k in range(4)]
    s = _session(frames)
    s._open_array_and_enhancer(s.provider, False, False)
    assert s._enhancer is fake and s._array_tap is None
    asyncio.run(VoiceSession._browser_to_provider(s))
    assert s.provider.sent == [frames[1][::-1], frames[3][::-1]]
