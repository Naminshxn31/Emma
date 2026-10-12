"""
Near or far, per mode (owner, 2026-09-29: "แต่ละโหมดฟังเสียงต่างกัน บางโหมด
จะต้องฟังเสียงคนด้านหน้า บางโหมดสามารถฟังได้ไกล" — EmmaJoke and the
interpreter far, the sales host near).

Two halves, one decision: the server opens each call's VAD gate with the
mode's loudness floor, and /health tells the page which modes are far so it
can set the browser's AGC — the one knob the server cannot reach. Blank
LISTEN_FAR_PROFILES must leave every mode exactly as before.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import vad_gate
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


def _split(monkeypatch, far=("joke", "translator"), near_floor=0.04, far_floor=0.0):
    monkeypatch.setattr(settings, "listen_far_profiles", frozenset(far))
    monkeypatch.setattr(settings, "vad_min_rms", near_floor)
    monkeypatch.setattr(settings, "vad_min_rms_far", far_floor)


def test_blank_means_every_mode_keeps_todays_floor(monkeypatch):
    monkeypatch.setattr(settings, "vad_min_rms", 0.04)
    for profile in ("condo", "joke", "translator", None):
        assert settings.listens_far(profile) is False
        assert settings.call_vad_floor(profile) == 0.04


def test_far_modes_open_with_their_own_floor_and_near_ones_keep_the_near_floor(monkeypatch):
    _split(monkeypatch, far_floor=0.005)
    assert settings.call_vad_floor("joke") == 0.005
    assert settings.call_vad_floor("translator") == 0.005
    assert settings.call_vad_floor("condo") == 0.04
    assert settings.call_vad_floor(" JOKE ") == 0.005          # the session lower-cases too


def test_a_blank_profile_is_the_machines_default_one(monkeypatch):
    _split(monkeypatch)
    monkeypatch.setattr(settings, "assistant_profile", "joke")
    assert settings.listens_far(None) and settings.listens_far("")
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    assert not settings.listens_far(None)


def test_the_gate_takes_the_floor_it_is_given(monkeypatch):
    class _Detector:
        def accept_waveform(self, samples):
            pass

        def is_speech_detected(self):
            return False

    monkeypatch.setattr(settings, "vad_mode", "local")
    monkeypatch.setattr(settings, "vad_min_rms", 0.04)
    monkeypatch.setattr(vad_gate, "_make_detector", lambda: _Detector())
    assert vad_gate.for_session()._min_rms == 0.04               # no argument = as before
    assert vad_gate.for_session(min_rms=0.0)._min_rms == 0.0
    assert vad_gate.for_session(min_rms=0.012)._min_rms == 0.012


def test_the_session_hands_its_modes_floor_to_the_provider(monkeypatch):
    """The wiring, not just the helper: session.py passes
    settings.call_vad_floor(self.profile) into get_provider."""
    src = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    assert "vad_floor=settings.call_vad_floor(self.profile)," in src
    gemini = (ROOT / "app" / "providers" / "gemini.py").read_text(encoding="utf-8")
    assert "vad_gate.for_session(min_rms=vad_floor)" in gemini
    init = (ROOT / "app" / "providers" / "__init__.py").read_text(encoding="utf-8")
    assert "use_tools=use_tools, vad_floor=vad_floor)" in init


def test_health_tells_the_page_which_modes_listen_far(monkeypatch):
    from app.main import app

    client = TestClient(app)
    listen = client.get("/health").json()["mic"]["listen"]
    assert listen == {"split": False, "far_profiles": [], "default_far": False}

    _split(monkeypatch)
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    listen = client.get("/health").json()["mic"]["listen"]
    assert listen == {"split": True, "far_profiles": ["joke", "translator"], "default_far": False}


def _page_code() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_page_sets_agc_from_the_mode_when_the_mic_opens():
    html = _page_code()
    # The call's mic asks for the mode's AGC, not the machine-wide one.
    assert "autoGainControl: callAGC() }" in html
    assert "autoGainControl: micAGC }" not in html
    agc = html.split("function callAGC() {", 1)[1].split("\n    }\n", 1)[0]
    # No split = exactly the old behaviour; an explicit ?agc on the URL wins.
    assert "if (agcFromUrl || !listenSplit) return micAGC;" in agc
    assert "return listensFar();" in agc
    far = html.split("function listensFar() {", 1)[1].split("\n    }\n", 1)[0]
    assert "modeId ? listenFarProfiles.indexOf(modeId) >= 0 : listenDefaultFar" in far
    # Read from /health, and the ?agc override is remembered as an override.
    assert "listenSplit = !!listen.split;" in html
    assert re.search(r"micAGC = q\.get\('agc'\) === '1'; agcFromUrl = true;", html)
    # A mode change redials (cycleMode), which is what re-opens the mic with
    # the new mode's AGC — and the status line says which way it listens.
    cycle = html.split("function cycleMode() {", 1)[1].split("\n    }\n", 1)[0]
    assert "setTimeout(startCall, 220);" in cycle
    assert "' · ฟังไกล' : ' · ฟังคนตรงหน้า'" in cycle
