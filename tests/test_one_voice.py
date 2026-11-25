"""The one-voice door (app/one_voice.py `Door`, app/vad_gate.py `voice_door`) and the page where a
person records their voice (/voice-lock, app/one_voice_page.py, client/voice-lock.html).

Owner 19 Nov: "ทำเป็นที่ให้พูดเพื่อบันทึกเสียงหน่อย เพราะจะให้ไมค์มันจับแค่เสียงคนนั้น ทำระบบแล้วเช็คด้วย".
The door's decisions run on a fake speaker model that reads the voice from the audio (every sample
of a test voice is `voice * 1000`), through the real VadGate with a scripted detector. The page's
socket runs through the real server app with a fake model and VAD. The last test drives the real
page in headless Chromium with the real models and a speech wav as the fake microphone, when this
machine has all of that, and says what goes unchecked when it does not.
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from app import one_voice as ov
from app.config import settings
from app.vad_gate import VadGate

ROOT = Path(__file__).resolve().parent.parent
DIM = 8
FRAME = 320                       # 20 ms at 16 kHz


def _vec(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i % DIM] = 1.0
    return v


def _voice_of(x) -> int:
    return int(round(float(np.abs(np.asarray(x)).mean()) * 32768 / 1000))


def fake_embed(x):
    """Voice 1 = the enrolled one; voice 5 sits at cosine 0.8 to it; everything else is far."""
    k = _voice_of(x)
    if k == 5:
        return np.array([0.8, 0.6] + [0.0] * (DIM - 2), dtype=np.float32)
    return _vec(0 if k == 1 else k)


def _pcm(voice: int, seconds: float, gain: float = 1.0) -> bytes:
    n = int(seconds * ov.RATE)
    return np.full(n, int(voice * 1000 * gain), dtype="<i2").tobytes()


def _frames(pcm: bytes):
    for i in range(0, len(pcm), FRAME * 2):
        yield pcm[i: i + FRAME * 2]


class Rows(list):
    def __call__(self, **row):
        self.append(row)


def _door(**kw) -> tuple:
    rows = Rows()
    args = dict(threshold=0.5, short_db=8.0, ref_db=ov.level_stats(ov.pcm_to_float(_pcm(1, 1)))["rms_db"],
                embed=fake_embed, inline=True, moving=lambda: False, record=rows)
    args.update(kw)
    return ov.Door(_vec(0), **args), rows


# ---- the door alone ----------------------------------------------------------------------------

def test_a_sentence_is_judged_by_voice_once_it_has_enough_speech():
    door, rows = _door()
    door.begin()
    assert door.look(_pcm(1, 1.0)) == ov.WAIT                         # under DECIDE_S: hold on
    assert door.look(_pcm(1, ov.DOOR_DECIDE_S + 0.1)) == ov.PASS
    door.begin()
    assert door.look(_pcm(3, ov.DOOR_DECIDE_S + 0.1)) == ov.BLOCK
    assert [r["decision"] for r in rows] == ["pass", "block"]
    assert rows[0]["why"] == "sentence" and rows[0]["score"] == 1.0 and rows[1]["score"] == 0.0


def test_a_close_voice_is_judged_against_the_bar_not_waved_through():
    door, _ = _door(threshold=0.85)
    door.begin()
    assert door.look(_pcm(5, 1.5)) == ov.BLOCK                        # 0.8 < 0.85
    door, _ = _door(threshold=0.75)
    door.begin()
    assert door.look(_pcm(5, 1.5)) == ov.PASS


def test_a_quiet_voice_that_sounds_like_the_owner_is_still_not_a_sentence_of_his():
    """A close-talk mic hears its wearer loud: the replay through the real gate let 3 of 20 of a
    bystander's sentences in (25 dB down) by voice alone. Score 1.0, 20 dB under: refused."""
    door, rows = _door()                                               # the wearer: -30.3 dBFS
    door.begin()
    assert door.look(_pcm(1, 1.5, gain=0.1)) == ov.BLOCK
    assert rows[-1]["score"] == 1.0 and rows[-1]["decision"] == "block"
    door.begin()
    assert door.look(_pcm(1, 1.5, gain=0.25)) == ov.PASS               # 12 dB under: his own, softer


def test_a_relook_must_clear_the_bar_by_a_margin():
    """Every relook is another draw: the closest other voice got 3 more sentences in that way."""
    door, rows = _door(threshold=0.75)                                 # voice 5 scores 0.8
    door.begin()
    assert door.relook(_pcm(5, 1.5), int(ov.DOOR_RELOOK_S * 2 * ov.RATE)) == ov.BLOCK
    door.begin()
    assert door.relook(_pcm(1, 1.5), int(ov.DOOR_RELOOK_S * 2 * ov.RATE)) == ov.PASS
    assert [r["why"] for r in rows] == ["relook"]


def test_a_short_word_passes_by_loudness_never_by_score():
    """5 Nov: eleven of the owner's short words were dropped. A short word is judged by level
    against the enrolled voice -- the quiet bystander is refused, the wearer is not."""
    door, rows = _door()                                               # the wearer: -30.3 dBFS
    door.begin()
    assert door.short(_pcm(1, 0.4)) == ov.PASS                        # loud as the wearer: in
    door.begin()
    assert door.short(_pcm(1, 0.4, gain=0.25)) == ov.BLOCK             # 12 dB under: out
    door.begin()
    assert door.short(_pcm(1, 0.4, gain=0.5)) == ov.PASS               # 6 dB under: in (8 dB rule)
    assert all(r["score"] is None and r["why"] == "short" for r in rows)


def test_the_short_word_level_follows_the_accepted_sentences_of_this_call():
    door, _ = _door(ref_db=-60.0)                                     # enrolled far too quiet
    door.begin()
    assert door.short(_pcm(1, 0.4, gain=0.25)) == ov.PASS              # over the old reference: in
    door.begin()
    assert door.look(_pcm(1, 1.5)) == ov.PASS                          # a sentence at the real level
    assert door.ref_db() == pytest.approx(ov.level_stats(ov.pcm_to_float(_pcm(1, 1)))["rms_db"])
    door.begin()
    assert door.short(_pcm(1, 0.4, gain=0.25)) == ov.BLOCK             # now 12 dB under the wearer


def test_while_the_robot_moves_every_voice_passes_at_once():
    door, rows = _door(moving=lambda: True)
    door.begin()
    assert door.look(_pcm(3, 0.1)) == ov.PASS
    door.begin()
    assert door.short(_pcm(3, 0.2, gain=0.1)) == ov.PASS
    assert {r["why"] for r in rows} == {"moving"}


def test_every_failure_opens_the_door():
    def broken(x):
        raise RuntimeError("model gone")

    door, rows = _door(embed=broken)
    door.begin()
    assert door.look(_pcm(3, 1.5)) == ov.PASS
    assert rows[-1]["why"] == "fail_open:model"


def test_a_verdict_that_never_comes_opens_after_the_hold_limit():
    from concurrent.futures import Future

    door, rows = _door(inline=False)
    door._submit = lambda v: Future()                                 # never done
    door.begin()
    assert door.look(_pcm(1, 1.5)) == ov.WAIT
    assert door.look(_pcm(1, ov.DOOR_HOLD_MAX_S - 0.1)) == ov.WAIT
    assert door.look(_pcm(1, ov.DOOR_HOLD_MAX_S + 0.1)) == ov.PASS
    assert rows[-1]["why"] == "fail_open:slow"


def test_the_pre_roll_does_not_count_toward_the_hold_limit():
    """Review 19 Nov: VAD_HEAD_HOLD_MS lets the pre-roll be 1.8 s on the robot. Counted, another
    person's 1.18 s at the wearer's loudness crossed 3 s and passed as a murmur, by level."""
    door, rows = _door()
    head = _pcm(1, 1.85, gain=0.01)                                   # quiet room, 1.85 s
    door.begin(len(head))
    assert door.look(head + _pcm(3, 1.18)) == ov.WAIT                 # 3.03 s held: not "a murmur" yet
    assert door.look(head + _pcm(3, 1.25)) == ov.BLOCK                # judged by voice
    assert rows[-1]["why"] == "sentence"


def test_a_late_ask_gets_its_own_wait_before_the_door_opens():
    from concurrent.futures import Future

    door, rows = _door()
    door._submit = lambda v: Future()                                 # never done
    head = _pcm(1, 1.8, gain=0.01)
    door.begin(len(head))
    assert door.look(head + _pcm(1, 1.25)) == ov.WAIT                 # asked at 3.05 s held
    assert door.look(head + _pcm(1, 1.3)) == ov.WAIT                  # not "slow" one frame later
    assert door.look(head + _pcm(1, 1.25 + ov.DOOR_VERDICT_WAIT_S + 0.05)) == ov.PASS
    assert rows[-1]["why"] == "fail_open:slow"


def test_a_model_error_on_a_relook_opens_too():
    def broken(x):
        raise RuntimeError("model gone")

    door, rows = _door(embed=broken)
    door.begin()
    assert door.relook(_pcm(1, 1.5), int(ov.DOOR_RELOOK_S * 2 * ov.RATE)) == ov.PASS
    assert rows[-1]["why"] == "fail_open:model"


def test_the_opening_doors_are_asked_once_per_run_not_every_held_frame(monkeypatch):
    """Review 19 Nov: a refused 4 s run asked focus.may_open 200 times -- each a focus_open row
    and a renewal of focus's converse hold, for a bystander the door had refused."""
    from app import focus

    calls = []
    monkeypatch.setattr(focus, "enabled", lambda: True)
    monkeypatch.setattr(focus, "may_open", lambda now, rms=0.0: calls.append(now) or (True, "ok"))
    frames, answers = _script((3, 4.0, True, 1), (3, 0.6, False, 0.01), (1, 2.0, True, 1), (1, 0.6, False, 0.01))
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, rows = _door()
    acts = _run(gate, frames)
    assert len(calls) == 2                                            # one per run
    assert _kinds(acts).count("start") == 1


def test_the_log_carries_numbers_only():
    door, rows = _door()
    door.begin()
    door.look(_pcm(1, 1.5))
    door.begin()
    door.short(_pcm(3, 0.4))
    allowed = {"decision", "why", "score", "speech_s", "rms_db", "ref_db", "threshold"}
    for r in rows:
        assert set(r) == allowed
        assert all(v is None or isinstance(v, (int, float, str)) for v in r.values())


# ---- the door in the live path ------------------------------------------------------------------

class Detector:
    """Says whatever the test tells it to, one answer per feed (tests/test_vad_gate.py)."""

    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


def _run(gate, pcm_frames):
    out = []
    for f in pcm_frames:
        out.append(gate.feed(f))
    return out


def _script(*parts):
    """[(voice, seconds, speaking, gain)] -> (frames, detector answers)."""
    frames, answers = [], []
    for voice, seconds, speaking, gain in parts:
        for f in _frames(_pcm(voice, seconds, gain)):
            frames.append(f)
            answers.append(speaking)
    return frames, answers


def _forwarded(acts):
    return b"".join(d for a in acts for k, d in a if k == "audio")


def _kinds(acts):
    return [k for a in acts for k, _ in a]


def test_no_door_is_the_gate_of_before_byte_for_byte():
    frames, answers = _script((1, 0.4, False, 0.01), (3, 2.0, True, 1), (1, 0.6, False, 0.01))
    plain = _run(VadGate(Detector(answers), prefix_padding_ms=300), frames)
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    assert gate.voice_door is None
    assert _run(gate, frames) == plain


def test_an_enrolled_sentence_goes_up_whole_and_in_order_once_the_door_says_yes():
    frames, answers = _script((1, 0.4, False, 0.01), (1, 2.0, True, 1), (1, 0.6, False, 0.01))
    plain = _run(VadGate(Detector(answers), prefix_padding_ms=300), frames)
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, _ = _door()
    acts = _run(gate, frames)
    first = next(i for i, a in enumerate(acts) if a)
    plain_first = next(i for i, a in enumerate(plain) if a)
    assert first - plain_first == pytest.approx(ov.DOOR_DECIDE_S / 0.02, abs=20)   # held ~1.2 s
    assert acts[first][0] == ("start", b"")
    assert _forwarded(acts) == _forwarded(plain)                      # nothing lost, nothing reordered
    assert _kinds(acts).count("start") == 1 and _kinds(acts).count("end") == 1


def test_another_voice_sends_nothing_at_all_and_the_next_run_still_opens():
    frames, answers = _script((3, 2.0, True, 1), (3, 0.6, False, 0.01), (1, 2.0, True, 1), (1, 0.6, False, 0.01))
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, rows = _door()
    acts = _run(gate, frames)
    run1 = acts[:100]
    assert all(a == [] for a in run1)                                 # not a byte, not a signal
    assert _kinds(acts).count("start") == 1
    assert _voice_of(ov.pcm_to_float(_forwarded(acts))) == 1
    assert [r["decision"] for r in rows] == ["block", "pass"]


def test_a_short_word_opens_and_closes_in_one_go_when_it_is_loud_enough():
    frames, answers = _script((1, 2.0, True, 1), (1, 0.6, False, 0.01),      # a sentence: the level
                              (3, 0.4, True, 1), (3, 0.6, False, 0.01))       # "ค่ะ" at that level
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, rows = _door()
    acts = _run(gate, frames)
    assert _kinds(acts).count("start") == 2 and _kinds(acts).count("end") == 2
    burst = next(a for a in acts[130:] if a)
    assert [k for k, _ in burst] == ["start", "audio", "end"]
    assert rows[-1]["why"] == "short" and rows[-1]["decision"] == "pass"
    assert gate.take_closed() is not None and not gate.in_speech


def test_a_quiet_short_word_from_the_room_is_dropped():
    frames, answers = _script((3, 0.4, True, 0.1), (3, 0.6, False, 0.01))     # -40.8 vs -30.3 dBFS
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, rows = _door()
    acts = _run(gate, frames)
    assert _kinds(acts) == []
    assert rows[-1]["why"] == "short" and rows[-1]["decision"] == "block"


def test_the_enrolled_voice_right_after_another_one_gets_in_by_the_relook():
    """One detector run, no pause: somebody else, then the owner. Refused at first, let in once
    the latest audio is his -- with the relook window as the head."""
    frames, answers = _script((3, 1.6, True, 1), (1, 3.0, True, 1), (1, 0.6, False, 0.01))
    gate = VadGate(Detector(answers), prefix_padding_ms=300)
    gate.voice_door, rows = _door()
    acts = _run(gate, frames)
    assert _kinds(acts).count("start") == 1
    head = next(a for a in acts if a)
    assert head[0] == ("start", b"")
    assert _voice_of(ov.pcm_to_float(head[1][1])) in (1, 2)   # mostly the owner
    assert [r["why"] for r in rows][-1] == "relook"
    assert len(head[1][1]) <= ov.DOOR_RELOOK_WINDOW_S * 2 * ov.RATE


# ---- switched on only where listed ---------------------------------------------------------------

def _store(tmp_path, monkeypatch):
    monkeypatch.setattr(ov, "PRINTS", tmp_path / "prints")
    monkeypatch.setattr(ov, "UTTERANCES", tmp_path / "utterances")
    monkeypatch.setattr(ov, "_model_pin", lambda: "pin")
    monkeypatch.setattr(ov, "model_ready", lambda: (True, ""))
    ov._warned.clear()


def _enrol(name="owner"):
    built = {"ok": True, "vector": _vec(0), "windows": 4, "kept": 4, "worst": 1.0, "self_mean": 1.0,
             "halves": 1.0, "smaller": 0.0}
    ov.save_print(name, built, {"speech_s": 12.0, "level_db": -30.3})


def test_the_door_is_off_unless_the_profile_is_listed(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    _enrol()
    assert settings.one_voice_profiles == frozenset()                 # the pinned default
    assert ov.door_for("condo") is None
    monkeypatch.setattr(settings, "one_voice_profiles", frozenset({"event"}))
    assert ov.door_for("condo") is None
    door = ov.door_for("event")
    assert isinstance(door, ov.Door) and door.ref_db() == -30.3
    assert door.threshold == settings.one_voice_threshold


def test_listed_but_unusable_means_every_voice_passes(tmp_path, monkeypatch, caplog):
    _store(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "one_voice_profiles", frozenset({"condo"}))
    assert ov.door_for("condo") is None                               # no print on file
    _enrol("a")
    _enrol("b")
    ov._warned.clear()
    assert ov.door_for("condo") is None                               # two, and no ONE_VOICE_NAME
    monkeypatch.setattr(settings, "one_voice_name", "b")
    assert isinstance(ov.door_for("condo"), ov.Door)
    monkeypatch.setattr(ov, "model_ready", lambda: (False, "no_model"))
    assert ov.door_for("condo") is None
    assert "every voice passes" in caplog.text


def test_a_listed_profile_with_no_local_gate_says_so_instead_of_passing_everyone_silently(
        monkeypatch, caplog):
    """Review 20 Nov: VAD_MODE defaults to "gemini" -- no VadGate, so the door never attached and
    nothing said so. Now a warning (once) and a turn-log row (every such call)."""
    rows = Rows()
    monkeypatch.setattr(ov, "_turnlog_off", rows)
    ov._warned.clear()
    monkeypatch.setattr(settings, "vad_mode", "gemini")
    ov.no_gate("condo", provider="GeminiProvider")
    assert rows == []                                                  # not listed: not our business
    monkeypatch.setattr(settings, "one_voice_profiles", frozenset({"condo"}))
    ov.no_gate("condo", provider="GeminiProvider")
    ov.no_gate("condo", provider="GeminiProvider")
    assert rows == [{"why": "vad_mode_not_local", "provider": "GeminiProvider"}] * 2
    assert caplog.text.count("every voice passes; set VAD_MODE=local") == 1


def test_whether_the_door_can_attach_is_read_from_the_vad_settings(tmp_path, monkeypatch):
    model = tmp_path / "silero.onnx"
    monkeypatch.setattr(settings, "vad_model", str(model))
    monkeypatch.setattr(settings, "vad_mode", "gemini")
    assert ov.gate_attachable() == (False, "vad_mode_not_local")
    monkeypatch.setattr(settings, "vad_mode", "local")
    assert ov.gate_attachable() == (False, "no_vad_model")
    model.write_bytes(b"x")
    assert ov.gate_attachable() == (True, "")


def test_the_session_attaches_the_door_only_when_a_profile_is_listed():
    src = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    i = src.index("gate.voice_door = await asyncio.to_thread(one_voice.door_for, self.profile)")
    before = src[max(0, i - 600): i]
    assert "if settings.one_voice_profiles:" in before
    assert "from app import one_voice" in before                      # imported only then
    after = src[i: i + 600]
    assert "else:" in after and "one_voice.no_gate(self.profile" in after   # no gate: said, not silent


# ---- the page's socket ----------------------------------------------------------------------------

class FakeSegmenter:
    """Cuts at every all-zero frame (the page's test stream marks the gaps that way)."""

    def __init__(self):
        self.buf = []

    def feed(self, x):
        if np.abs(x).max() == 0:
            return self.flush()
        self.buf.append(np.asarray(x, dtype=np.float32))
        return []

    def flush(self):
        if not self.buf:
            return []
        out, self.buf = [(0.0, np.concatenate(self.buf))], []
        return out


@pytest.fixture
def page(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import one_voice_page
    from app.main import app

    _store(tmp_path, monkeypatch)
    monkeypatch.setattr(one_voice_page, "_embedder", lambda: fake_embed)
    monkeypatch.setattr(one_voice_page, "_segmenter", FakeSegmenter)
    monkeypatch.setattr(one_voice_page, "_vad_ready", lambda: True)
    # These tests hand seconds of audio over in milliseconds, standing in for a page that sends in
    # real time; the faster-than-time guard is tested on its own (it is switched back on there).
    monkeypatch.setattr(one_voice_page, "FAST_RATIO", float("inf"))
    return TestClient(app), tmp_path


def _say(ws, voice, seconds, gain=1.0):
    for f in _frames(_pcm(voice, seconds, gain)):
        ws.send_bytes(f)
    ws.send_bytes(bytes(FRAME * 2))                                  # the gap that ends it


def _until(ws, kind):
    while True:
        m = json.loads(ws.receive_text())
        if m["type"] == kind:
            return m
        if m["type"] == "error":
            raise AssertionError(m)


def test_the_page_is_served():
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/voice-lock")
    assert r.status_code == 200 and "บันทึกเสียง" in r.text and "/ws/voice-lock" in r.text


def test_the_whole_test_runs_through_the_socket(page):
    client, tmp = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        s = _until(ws, "state")
        assert s["prints"] == [] and s["model"] and s["threshold"] == ov.DEFAULT_THRESHOLD
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": False}))
        assert "ยินยอม" in json.loads(ws.receive_text())["message"]
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True, "mic": "LCS_USB_AUDIO"}))
        _until(ws, "enrolling")
        for _ in range(3):
            _say(ws, 1, 4.0)
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        e = _until(ws, "enrolled")
        assert e["ok"] and e["speech_s"] == 12.0 and "vector" not in e
        s = _until(ws, "state")
        assert [p["name"] for p in s["prints"]] == ["owner"]
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "target", "threshold": 0.5}))
        _until(ws, "listening")
        _say(ws, 1, 2.0)
        u = _until(ws, "utterance")
        assert u["decision"] == "ACCEPT" and u["score"] == 1.0 and u["saved"] and not u["short"]
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "other"}))
        _until(ws, "listening")
        _say(ws, 3, 2.0)
        u = _until(ws, "utterance")
        assert u["decision"] == "REJECT" and u["label"] == "other"
        ws.send_text(json.dumps({"type": "listen_stop"}))
        _until(ws, "stopped")
        s = _until(ws, "state")
        assert s["saved"] == {"other": 1, "target": 1}
        ws.send_text(json.dumps({"type": "eval", "name": "owner"}))
        ev = _until(ws, "eval")
        assert ev["verdict"] == "UNKNOWN" and ev["counts"] == {"other": 1, "target": 1}
        assert any("VERDICT" in line for line in ev["lines"])
        ws.send_text(json.dumps({"type": "forget", "name": "owner", "audio": True}))
        f = _until(ws, "forgotten")
        assert f == {"type": "forgotten", "print": True, "files": 4, "left": 0}   # 2 wavs + 2 index.csv
        s = _until(ws, "state")
        assert s["prints"] == [] and s["saved"] == {}
    assert not any(p.is_file() for p in tmp.rglob("*"))


def test_two_voices_in_one_recording_are_not_enrolled(page):
    client, _ = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True}))
        _until(ws, "enrolling")
        _say(ws, 1, 6.0)
        _say(ws, 3, 6.0)
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        e = _until(ws, "enrolled")
        assert not e["ok"] and e["why"] == "mixed_voices"


def test_digital_silence_and_too_little_speech_are_told_apart(page):
    client, _ = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True}))
        _until(ws, "enrolling")
        for _ in range(100):
            ws.send_bytes(bytes(FRAME * 2))                          # the GCK with its transmitter off
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        assert _until(ws, "enrolled")["why"] == "no_sound"
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True}))
        _until(ws, "enrolling")
        _say(ws, 1, 4.0)
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        e = _until(ws, "enrolled")
        assert e["why"] == "too_short" and e["speech_s"] == 4.0


def test_a_near_silent_recording_says_wrong_mic_not_read_more(page):
    """20 Nov: the page recorded LCS_USB_AUDIO (dither ~3e-5, not the GCK) and told the owner to read
    more, closer to the mic. A recording whose peak never reaches a voice is a mic problem."""
    client, _ = page
    rng = np.random.default_rng(3)
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True, "mic": "Microphone (LCS_USB_AUDIO)"}))
        _until(ws, "enrolling")
        dither = (rng.integers(-1, 2, size=16000 * 6) ).astype(np.int16)         # +-1 LSB = peak 3e-5
        for i in range(0, len(dither), 320):
            ws.send_bytes(dither[i:i + 320].tobytes())
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        e = _until(ws, "enrolled")
        assert not e["ok"] and e["why"] == "too_quiet" and e["peak"] < 0.001
        assert e["mic"] == "Microphone (LCS_USB_AUDIO)"


def test_record_everyone_now_and_say_whose_voice_later(page):
    """Owner 20 Nov: "อัดรวมก่อน แยกทีหลัง". The mixed recording stays out of the eval folders (every
    folder there but the enrolled one is read as another person on the mic) until a person sorts it."""
    client, tmp = page
    _enrol()
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "unsorted"}))
        _until(ws, "listening")
        for voice in (1, 3, 1):
            _say(ws, voice, 2.0)
            assert _until(ws, "utterance")["saved"]
        ws.send_text(json.dumps({"type": "listen_stop"}))
        _until(ws, "stopped")
        s = _until(ws, "state")
        assert s["unsorted"] == 3 and s["saved"] == {}                # nothing in the eval folders yet
        assert not (tmp / "utterances").exists()
        ws.send_text(json.dumps({"type": "unsorted_list"}))
        items = _until(ws, "unsorted")["items"]
        assert [round(i["speech_s"]) for i in items] == [2, 2, 2]
        files = [i["file"] for i in items]
        ws.send_text(json.dumps({"type": "clip", "file": files[1]}))
        clip = _until(ws, "clip")
        pcm = np.frombuffer(base64.b64decode(clip["pcm"]), dtype="<i2")
        assert clip["rate"] == ov.RATE and len(pcm) == 2 * ov.RATE and _voice_of(pcm / 32768) == 3
        for bad in ({"file": "../prints/owner.npz"}, {"file": "nope-001.wav"}, {"file": None}):
            ws.send_text(json.dumps({"type": "clip", **bad}))
            err = json.loads(ws.receive_text())
            assert err["type"] == "error" and "ไม่มีเสียงนี้" in err["message"]     # a listed name, never a path
        ws.send_text(json.dumps({"type": "sort", "file": files[0], "label": "evil"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        for f, label in zip(files, ("target", "other", "discard")):
            ws.send_text(json.dumps({"type": "sort", "file": f, "label": label}))
            done = _until(ws, "sorted")
            assert done["label"] == label
        assert done["left"] == 0
        s = _until(ws, "state")
        assert s["unsorted"] == 0 and s["saved"] == {"other": 1, "target": 1}
        ws.send_text(json.dumps({"type": "sort", "file": files[0], "label": "target"}))   # already sorted
        assert json.loads(ws.receive_text())["type"] == "error"
        ws.send_text(json.dumps({"type": "eval", "name": "owner"}))
        assert _until(ws, "eval")["counts"] == {"other": 1, "target": 1}
        for label in ("target", "other"):                             # the index follows the file
            rows = (tmp / "utterances" / label / "index.csv").read_text(encoding="utf-8").splitlines()
            assert len(rows) == 2 and rows[0] == "file,session,at_s,speech_s"
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "unsorted"}))
        _until(ws, "listening")
        _say(ws, 3, 2.0)
        _until(ws, "utterance")
        ws.send_text(json.dumps({"type": "forget", "name": "owner", "audio": True}))
        f = _until(ws, "forgotten")
        assert f["files"] == 6                                        # 2 sorted wavs + 2 indexes + 1 unsorted + its index
    assert not any(p.is_file() for p in tmp.rglob("*"))


def test_a_recording_page_and_a_sorting_page_at_once_lose_nothing(tmp_path, monkeypatch):
    """Review 20 Nov: a row appended between a sort's read and its rewrite of the unsorted index was
    dropped -- the wav stayed on disk, never listed again. The sort here stops right before its
    rewrite while a second page saves; the save must wait, not slip in."""
    _store(tmp_path, monkeypatch)
    x = np.full(ov.RATE, 0.03, dtype=np.float32)
    first = ov.save_unsorted(x, "20261120-120000-000000", 1, 0.0, 1.0)
    real_write = ov._write_index
    paused, go = threading.Event(), threading.Event()

    def write(index, rows):
        if threading.current_thread().name == "sorter" and index.parent == ov.unsorted_dir():
            paused.set()
            go.wait(5)
        real_write(index, rows)

    monkeypatch.setattr(ov, "_write_index", write)
    sorter = threading.Thread(target=ov.sort_unsorted, args=(first, "target"), name="sorter")
    sorter.start()
    assert paused.wait(5)
    saver = threading.Thread(target=ov.save_unsorted, args=(x, "20261120-120001-000000", 1, 0.0, 1.0))
    saver.start()
    saver.join(0.4)                                                   # free to append now, without the lock
    go.set()
    sorter.join(5)
    saver.join(5)
    assert [i["file"] for i in ov.unsorted_list()] == ["20261120-120001-000000-001.wav"]
    assert [f.name for f in (tmp_path / "utterances" / "target").glob("*.wav")] == [first]


def test_sorting_counts_what_is_left_to_sort_and_forget_leaves_nothing(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    x = np.full(ov.RATE, 0.03, dtype=np.float32)
    a, b, c = (ov.save_unsorted(x, "20261120-120000-000000", n, 0.0, 1.0) for n in (1, 2, 1000))
    assert c.endswith("-1000.wav") and c in [i["file"] for i in ov.unsorted_list()]   # {n:03d} grew a digit
    (ov.unsorted_dir() / b).unlink()                                  # a wav gone behind the index's back
    assert ov.sort_unsorted(a, "other")["left"] == 1                  # c; not the dead row of b
    for d in (ov.unsorted_dir(), tmp_path / "utterances" / "other"):
        (d / "index.tmp").write_text("a rewrite cut short", encoding="utf-8")
    out = ov.forget("owner", audio=True)
    assert out["left"] == 0 and not any(p.is_file() for p in tmp_path.rglob("*"))


def test_a_broken_recording_is_an_error_not_a_dead_socket(page):
    client, tmp = page
    _enrol()
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "unsorted"}))
        _until(ws, "listening")
        _say(ws, 1, 2.0)
        _until(ws, "utterance")
        ws.send_text(json.dumps({"type": "listen_stop"}))
        _until(ws, "stopped")
        ws.send_text(json.dumps({"type": "unsorted_list"}))
        f = _until(ws, "unsorted")["items"][0]["file"]
        (tmp / "unsorted" / f).write_bytes(b"not a wav at all")
        ws.send_text(json.dumps({"type": "clip", "file": f}))
        err = json.loads(ws.receive_text())
        assert err["type"] == "error" and "อ่านเสียงนี้ไม่ได้" in err["message"]
        ws.send_text(json.dumps({"type": "hello"}))
        assert _until(ws, "state")["unsorted"] == 1                   # the socket lives on
        ws.send_text(json.dumps({"type": "sort", "file": f, "label": "discard"}))
        assert _until(ws, "sorted")["left"] == 0


def test_audio_faster_than_time_is_the_page_not_the_reader():
    from app import one_voice_page as p

    assert not p.too_fast(40.0, 40.0) and not p.too_fast(50.0, 40.0)       # real time, and lag
    assert not p.too_fast(1.0, 0.0)                                        # the first lump
    assert p.too_fast(90.0, 40.0)                                          # 20 Nov: 3 senders, capped at 90 s


def test_a_page_sending_its_mic_twice_is_refused_not_told_to_read_more(page, monkeypatch):
    """20 Nov: every mic switch on the page left the old worklet posting too -- 151-251 frames/s for
    50/s of time -- and the owner's reading came back as "0 s of speech, read more, closer"."""
    from app import one_voice_page

    monkeypatch.setattr(one_voice_page, "FAST_RATIO", 1.25)
    client, tmp = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True, "mic": "Microphone (GCK)"}))
        _until(ws, "enrolling")
        for _ in range(3):
            _say(ws, 1, 4.0)                                         # 12 s of speech in ~no time
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        e = _until(ws, "enrolled")
        assert not e["ok"] and e["why"] == "too_fast" and e["seconds"] >= 12.0 and e["wall_s"] < 5
        assert e["mic"] == "Microphone (GCK)"
    assert not (tmp / "prints").exists() or not any((tmp / "prints").rglob("*.npz"))


def test_listening_to_a_page_sending_twice_stops_and_says_why(page, monkeypatch):
    from app import one_voice_page

    client, _ = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "enroll_start", "name": "owner", "consent": True}))
        _until(ws, "enrolling")
        for _ in range(3):
            _say(ws, 1, 4.0)
        ws.send_text(json.dumps({"type": "enroll_stop"}))
        assert _until(ws, "enrolled")["ok"]
        monkeypatch.setattr(one_voice_page, "FAST_RATIO", 1.25)
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": None}))
        _until(ws, "listening")
        for f in _frames(_pcm(1, 4.0, 1.0)):
            ws.send_bytes(f)
        ws.send_text(json.dumps({"type": "listen_stop"}))
        seen = []
        while not seen or seen[-1]["type"] != "stopped":            # ends either way: no hang on a revert
            seen.append(json.loads(ws.receive_text()))
        errors = [m for m in seen if m["type"] == "error"]
        assert errors and errors[0]["code"] == "too_fast" and "F5" in errors[0]["message"]
        assert not any(m["type"] == "utterance" for m in seen)        # nothing judged from that stream


def test_bad_names_and_labels_are_refused_not_written(page):
    client, tmp = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        for cmd in ({"type": "enroll_start", "name": "../x", "consent": True},
                    {"type": "listen_start", "name": "owner", "label": "target"},       # no print yet
                    {"type": "nonsense"}):
            ws.send_text(json.dumps(cmd))
            assert json.loads(ws.receive_text())["type"] == "error"
        assert not (tmp / "prints").exists()
        _enrol()
        ws.send_text(json.dumps({"type": "listen_start", "name": "owner", "label": "../../etc"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        _say(ws, 1, 1.0)                                              # not listening: nothing kept
        ws.send_text(json.dumps({"type": "hello"}))
        assert _until(ws, "state")["saved"] == {}
    assert not (tmp / "utterances").exists()


def test_a_name_that_is_not_text_is_an_error_not_a_dead_socket(page):
    client, _ = page
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "listen_start", "name": 12345, "label": "target"}))
        assert json.loads(ws.receive_text())["type"] == "error"
        ws.send_text(json.dumps({"type": "hello"}))
        assert _until(ws, "state")["type"] == "state"                # still alive


def test_the_page_is_told_when_a_listed_profile_cannot_carry_the_door(page, monkeypatch):
    client, _ = page
    monkeypatch.setattr(settings, "one_voice_profiles", frozenset({"condo"}))
    monkeypatch.setattr(settings, "vad_mode", "gemini")
    with client.websocket_connect("/ws/voice-lock") as ws:
        ws.send_text(json.dumps({"type": "hello"}))
        g = _until(ws, "state")["gate"]
    assert g["profiles"] == ["condo"] and g["attachable"] is False and g["why"] == "vad_mode_not_local"


def test_the_socket_is_behind_the_token_like_every_other(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", "sekrit")
    with TestClient(app).websocket_connect("/ws/voice-lock") as ws:
        m = json.loads(ws.receive_text())
    assert m["code"] == "unauthorized"


def test_the_page_never_shows_or_sends_a_print():
    src = (ROOT / "client" / "voice-lock.html").read_text(encoding="utf-8")
    assert "vector" not in src
    page_py = (ROOT / "app" / "one_voice_page.py").read_text(encoding="utf-8")
    assert '"vector"' in page_py and 'k != "vector"' in page_py          # stripped before sending


# ---- the real page in Chromium, the real models ------------------------------------------------

def _speech_wav(path: Path) -> Path:
    """A fake microphone: one committed TTS voice, repeated with pauses (Chromium loops it)."""
    src = ROOT / "tests" / "data" / "wake" / "other.wav"
    with wave.open(str(src)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        rate = w.getframerate()
    gap = np.zeros(int(0.7 * rate), dtype="<i2")
    y = np.concatenate([np.concatenate([x, gap]) for _ in range(6)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(y.tobytes())
    return path


def serve_page(port: int):
    """The page and its socket alone (no Emma, no camera, no wake word) on 127.0.0.1:port."""
    import uvicorn
    from fastapi import FastAPI

    from app import one_voice_page

    async def no_gate(websocket, token):
        return False

    app = FastAPI()
    one_voice_page.register(app, no_gate, {"Cache-Control": "no-cache"})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    return server, t


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.timeout(180)          # a 20 s recording, then live listening, in a real browser
def test_the_real_page_records_and_listens_with_the_real_models(tmp_path, monkeypatch):
    from app import one_voice_page

    if not ov.model_ready()[0] or not (ROOT / "data" / "wake" / "silero_vad.onnx").is_file():
        pytest.skip("speaker model or Silero VAD missing: UNCHECKED -- that the real page, in a real "
                    "browser, records a voice, builds its print with CAM++ and judges what it hears")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: the real page in a browser was NOT checked")
    monkeypatch.setattr(ov, "PRINTS", tmp_path / "prints")
    monkeypatch.setattr(ov, "UTTERANCES", tmp_path / "utterances")
    monkeypatch.setattr(one_voice_page, "ENROLL_S", 20.0)
    wav = _speech_wav(tmp_path / "mic.wav")
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): the real page was NOT checked"
                        % str(exc).splitlines()[0])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/voice-lock")
        page.click("#micOpen")
        page.wait_for_selector("#micStatus.ok", timeout=15000)       # the fake mic is heard
        assert "บันทึกเสียง (20 วิ)" in page.inner_text("#enrollBtn")
        page.check("#consent")
        page.click("#enrollBtn")
        page.wait_for_function("document.querySelector('#enrollResult').textContent.startsWith('✓')",
                               timeout=45000)
        page.click("button[data-label=target]")
        page.wait_for_selector("button[data-label=target].on", timeout=10000)
        page.wait_for_function("document.querySelectorAll('#results li.yes').length >= 2", timeout=30000)
        assert page.eval_on_selector("#results", "u => u.getBoundingClientRect().top") <             page.eval_on_selector("#linesBox", "b => b.getBoundingClientRect().top")   # ✓/✗ above the lines
        page.click("#listenStop")
        page.wait_for_function("document.querySelector('#savedCounts').textContent.includes('ฉัน')",
                               timeout=10000)
        page.click("#evalBtn")
        page.wait_for_function("!document.querySelector('#verdict').textContent.includes('กำลังสรุป')",
                               timeout=30000)
        assert "ยังบอกไม่ได้" in page.inner_text("#verdict")         # nobody else spoke
        page.on("dialog", lambda d: d.accept())
        page.click("#forgetBtn")
        page.wait_for_function("document.querySelector('#printInfo').textContent.includes('ยังไม่มี')",
                               timeout=10000)
        assert errors == []
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)
    assert not any(p.is_file() for p in (tmp_path / "prints").rglob("*"))


@pytest.mark.timeout(90)
def test_switching_the_mic_on_the_page_leaves_one_sender(tmp_path, monkeypatch):
    """20 Nov: each mic switch added a worklet that kept posting (50 -> 151 -> 251 frames/s) and the
    owner's GCK reading reached the server interleaved with a dead mic's frames: 0 s of speech."""
    from app import one_voice_page

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: that a mic switch leaves one sender was NOT checked")
    monkeypatch.setattr(ov, "PRINTS", tmp_path / "prints")
    monkeypatch.setattr(ov, "UTTERANCES", tmp_path / "utterances")
    monkeypatch.setattr(ov, "model_ready", lambda: (True, ""))      # the button needs both; no model runs
    monkeypatch.setattr(one_voice_page, "_vad_ready", lambda: True)
    wav = _speech_wav(tmp_path / "mic.wav")
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): NOT checked" % str(exc).splitlines()[0])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        sent = []
        page.on("websocket", lambda ws: ws.on(
            "framesent", lambda f: sent.append(time.monotonic()) if isinstance(f, (bytes, bytearray)) else None))
        page.goto(f"http://127.0.0.1:{port}/voice-lock")
        page.click("#micOpen")
        page.wait_for_selector("#micStatus.ok", timeout=15000)
        for _ in range(2):                                           # the owner picking the mic again
            page.eval_on_selector("#micSelect", "s => s.dispatchEvent(new Event('change'))")
            page.wait_for_timeout(700)
        page.wait_for_selector("#micStatus.ok", timeout=15000)
        page.check("#consent")
        page.wait_for_function("!document.getElementById('enrollBtn').disabled", timeout=10000)
        page.click("#enrollBtn")
        page.wait_for_timeout(1000)                                  # 'enrolling' back, sending on
        t0 = time.monotonic()
        page.wait_for_timeout(3000)
        rate = sum(1 for t in sent if t >= t0) / (time.monotonic() - t0)
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)
    assert 35 <= rate <= 65, f"{rate:.0f} frames/s (one 20 ms sender = 50/s)"
    assert not any(p.is_file() for p in (tmp_path / "prints").rglob("*"))


@pytest.mark.timeout(90)
def test_a_failed_or_overlapping_mic_switch_records_the_mic_the_page_shows(tmp_path, monkeypatch):
    """Review 20 Nov: (1) a switch whose getUserMedia failed left the old node on a stopped stream --
    buttons on, meter "ศูนย์ล้วน", the transmitter blamed; (2) two quick switches recorded whichever
    device answered last, under the other one's name, with a capture leaked."""
    from app import one_voice_page

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: failed/overlapping mic switches were NOT checked")
    monkeypatch.setattr(ov, "model_ready", lambda: (True, ""))
    monkeypatch.setattr(one_voice_page, "_vad_ready", lambda: True)
    wav = _speech_wav(tmp_path / "mic.wav")
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): NOT checked" % str(exc).splitlines()[0])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/voice-lock")
        page.click("#micOpen")
        page.wait_for_selector("#micStatus.ok", timeout=15000)
        page.wait_for_function("!document.getElementById('enrollBtn').disabled", timeout=10000)
        # (1) the browser refuses the next mic
        page.evaluate("""() => { window.__gum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('busy', 'NotReadableError')); }""")
        page.eval_on_selector("#micSelect", "s => s.dispatchEvent(new Event('change'))")
        page.wait_for_function("document.querySelector('#micStatus').textContent.startsWith('เปิดไมค์ตัวนี้ไม่ได้')",
                               timeout=5000)
        assert page.evaluate("node === null && stream === null")
        assert page.is_disabled("#enrollBtn")
        assert "bad" in page.get_attribute("#micStatus", "class")
        # (2) two switches in a row; the FIRST device answers last
        labels = page.eval_on_selector_all("#micSelect option", "os => os.map(o => o.value)")
        assert len(labels) >= 2, labels
        page.evaluate("""() => { let n = 0; window.__opened = [];
            navigator.mediaDevices.getUserMedia = (c) => { const i = ++n;
              return window.__gum(c).then((s) => { window.__opened.push(s);
                return new Promise((r) => setTimeout(() => r(s), i === 1 ? 900 : 0)); }); }; }""")
        page.evaluate("""([a, b]) => { const s = document.getElementById('micSelect');
            s.value = a; s.dispatchEvent(new Event('change'));
            s.value = b; s.dispatchEvent(new Event('change')); }""", [labels[-2], labels[-1]])
        page.wait_for_timeout(1800)
        shown = page.evaluate("() => { const s = document.getElementById('micSelect'); return s.options[s.selectedIndex].textContent; }")
        live = page.evaluate("() => window.__opened.filter((s) => s.getAudioTracks()[0].readyState === 'live').map((s) => s.getAudioTracks()[0].label)")
        recording = page.evaluate("stream && stream.getAudioTracks()[0].label")
        assert live == [recording], (live, recording)                 # one capture, the page's own
        assert shown.startswith(recording), (shown, recording)        # the mic named is the mic heard
        page.wait_for_selector("#micStatus.ok", timeout=10000)
        assert not page.is_disabled("#enrollBtn")
        assert errors == []
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)


@pytest.mark.timeout(120)
def test_the_page_records_everyone_and_sorts_by_ear(tmp_path, monkeypatch):
    """Owner 20 Nov "อัดรวมก่อน แยกทีหลัง": the real page records a mixed run (no ✓/✗ on screen),
    plays each sentence back and files it where the person who heard it says."""
    from app import one_voice_page

    if not (ROOT / "data" / "wake" / "silero_vad.onnx").is_file():
        pytest.skip("Silero VAD missing: the page's mixed recording was NOT checked")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: the page's mixed recording was NOT checked")
    _store(tmp_path, monkeypatch)
    _enrol()
    monkeypatch.setattr(one_voice_page, "_embedder", lambda: (lambda x: _vec(0)))
    wav = _speech_wav(tmp_path / "mic.wav")
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): NOT checked" % str(exc).splitlines()[0])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/voice-lock")
        page.click("#micOpen")
        page.wait_for_selector("#micStatus.ok", timeout=15000)
        page.click("button[data-label=unsorted]")
        page.wait_for_selector("button[data-label=unsorted].on", timeout=10000)
        page.wait_for_function("document.querySelectorAll('#results li').length >= 4", timeout=60000)
        assert not page.query_selector("#results li.yes, #results li.no")   # no verdict while recording everyone
        page.click("#listenStop")
        page.wait_for_function("document.querySelector('#savedCounts').textContent.includes('ยังไม่แยก')",
                               timeout=10000)
        page.click("#sortLoad")
        page.wait_for_function("document.querySelectorAll('#sortList li').length >= 4", timeout=10000)
        n = page.eval_on_selector_all("#sortList li", "ls => ls.length")
        assert "คะแนน" not in page.inner_text("#sortList")                # the ear decides, not the score
        page.click("#sortList li:first-child button:has-text('▶')")
        page.wait_for_function("window.__lastClipSamples > 8000", timeout=10000)
        page.click("#sortList li:first-child button[data-sort=target]")
        page.wait_for_function(f"document.querySelectorAll('#sortList li').length === {n - 1}", timeout=10000)
        page.click("#sortList li:first-child button[data-sort=other]")
        page.wait_for_function(f"document.querySelectorAll('#sortList li').length === {n - 2}", timeout=10000)
        page.wait_for_function("document.querySelector('#savedCounts').textContent.includes('ฉัน 1')", timeout=10000)
        assert "อีกคน 1" in page.inner_text("#savedCounts")
        assert len(list((tmp_path / "utterances" / "target").glob("*.wav"))) == 1
        assert len(list((tmp_path / "utterances" / "other").glob("*.wav"))) == 1
        assert len(list((tmp_path / "unsorted").glob("*.wav"))) == n - 2
        # A clip still playing stops when a listening run starts (it would go into the mic). Same JS
        # turn: a clip that simply ended would pass otherwise.
        page.evaluate("window.__lastClipSamples = 0")
        page.click("#sortList li:first-child button:has-text('▶')")
        page.wait_for_function("window.__lastClipSamples > 8000", timeout=10000)
        was, now = page.evaluate("""() => { const was = playing !== null;
            document.querySelector('button[data-label=unsorted]').click(); return [was, playing === null]; }""")
        assert was and now
        page.wait_for_selector("button[data-label=unsorted].on", timeout=10000)
        page.click("#listenStop")
        # A sort that fails gives its buttons back (review 20 Nov: dead until a reload).
        gone = page.eval_on_selector("#sortList li:first-child", "li => li.dataset.file")
        (tmp_path / "unsorted" / gone).unlink()
        page.click("#sortList li:first-child button[data-sort=nearby]")
        page.wait_for_function("document.querySelector('#banner').textContent.includes('ไม่มีเสียงนี้')", timeout=10000)
        assert page.is_enabled("#sortList li:first-child button[data-sort=nearby]")
        # Deleting everything clears the list it deleted.
        page.on("dialog", lambda d: d.accept())
        page.click("#forgetBtn")
        page.wait_for_function("document.querySelectorAll('#sortList li').length === 0", timeout=10000)
        assert errors == []
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)
    assert not any(f.suffix == ".wav" for f in tmp_path.rglob("*") if f.name != "mic.wav")


@pytest.mark.timeout(60)
def test_agc_0_in_the_url_records_with_the_browsers_gain_off(tmp_path):
    """20 Nov: with the browser's AGC on, people a metre from the GCK's wearer reached it as loud as he
    did (loudness let 91% of them in). ?agc=0 -- the robot kiosk's own switch -- measures near/far."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: ?agc=0 on the page was NOT checked")
    wav = _speech_wav(tmp_path / "mic.wav")
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): NOT checked" % str(exc).splitlines()[0])
        seen = {}
        for query in ("", "?agc=0", "?agc=1"):
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/voice-lock{query}")
            page.click("#micOpen")
            page.wait_for_selector("#micStatus.ok", timeout=15000)
            seen[query] = (page.evaluate("micFlags.autoGainControl"),
                           page.evaluate("stream.getAudioTracks()[0].getSettings().autoGainControl"),
                           "ปรับระดับอัตโนมัติ ปิด" in page.inner_text("#micInfo"))
            page.close()
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)
    assert seen[""] == (True, True, False)                           # the call page's setting (no /health here)
    assert seen["?agc=0"] == (False, False, True)                    # asked of the browser, and said on the page
    assert seen["?agc=1"] == (True, True, False)


@pytest.mark.timeout(60)
def test_the_page_says_a_transmitter_sending_exact_zeros_is_not_a_quiet_room(tmp_path, monkeypatch):
    """19 Nov on this PC the GCK receiver sent exact zeros: the meter must say so in words."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright missing: the page's digital-silence message was NOT checked")
    wav = tmp_path / "zeros.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(16000 * 2 * 3))
    port = free_port()
    server, thread = serve_page(port)
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s): NOT checked" % str(exc).splitlines()[0])
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(f"http://127.0.0.1:{port}/voice-lock")
        page.click("#micOpen")
        page.wait_for_function("document.querySelector('#micStatus').textContent.startsWith('ไม่มีเสียงเลย')",
                               timeout=15000)
        assert "bad" in page.get_attribute("#micStatus", "class")
        assert "ตัวส่ง" in page.inner_text("#micStatus")
        assert "hidden" in page.get_attribute("#gateNote", "class")      # the default: nothing armed
        # A listed profile on a machine whose calls have no local VAD gate: said in red.
        monkeypatch.setattr(settings, "one_voice_profiles", frozenset({"condo"}))
        monkeypatch.setattr(settings, "vad_mode", "gemini")
        page.reload()
        page.wait_for_selector("#gateNote.bad", timeout=10000)
        assert "VAD_MODE" in page.inner_text("#gateNote") and "ทุกเสียงผ่าน" in page.inner_text("#gateNote")
        browser.close()
    finally:
        pw.stop()
        server.should_exit = True
        thread.join(5)
