"""VAD_HEAD_HOLD_MS: a sentence a door lets in mid-way arrives whole.

Robot log 28 Oct (staff testing in the floor-1 media room, the camera seeing
no face all evening): listening focus denied the first frames of a tester's
sentence ("no face in front", rms 0.031) and let it in a moment later on a
louder syllable ("near voice", rms 0.069) -- with only the 0.3 s pre-roll, so
the model heard only the sentence's last word or two. 73 of the day's 124 opens came within
2 s of a denial or a floor drop; on 20 Oct English guests reached the model
starting mid-sentence (a leading comma, a dangling "is ..."). The floor's and the focus's own comments promised "the segment
opens then, head intact"; the pre-roll was trimmed to prefix_padding_ms the
whole time.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.vad_gate import VadGate

from tests.test_vad_gate import LOUD_CHUNK, QUIET_CHUNK, ScriptedDetector

FRAME = len(QUIET_CHUNK)            # 10 ms at 16 kHz (160 samples)


def _refused_then_loud(head_hold_ms, refused=40, before=10):
    """`before` silent frames, then `refused` speech frames under the floor,
    then one loud frame: returns the bytes forwarded with the start."""
    answers = [False] * before + [True] * (refused + 1)
    gate = VadGate(ScriptedDetector(answers), prefix_padding_ms=300,
                   min_rms=0.03, floor_grace_s=0, head_hold_ms=head_hold_ms)
    for _ in range(before):
        assert gate.feed(b"\x00\x00" * (FRAME // 2)) == []
    for _ in range(refused):
        assert gate.feed(QUIET_CHUNK) == [], "the floor still refuses these"
    actions = gate.feed(LOUD_CHUNK)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    return actions[1][1]


def test_without_the_hold_only_the_pre_roll_comes_along():
    """The old behaviour, kept exactly at 0 (the sales-room PC)."""
    head = _refused_then_loud(0)
    assert len(head) == 300 * 32, "300 ms of the refused sentence, the rest lost"


def test_the_hold_keeps_the_refused_head_of_the_sentence():
    """0.8 s refused, then let in: all 0.8 s goes up, plus the pre-roll's
    silence before it. Reverting the cap in VadGate.feed turns this red."""
    head = _refused_then_loud(1500, refused=80, before=10)
    assert len(head) == (10 + 80) * FRAME     # 0.1 s silence + 0.8 s refused speech
    assert head.endswith(QUIET_CHUNK * 80), "every refused frame, in order"


def test_the_hold_is_capped():
    """A long refused run (room chatter) brings at most prefix + hold."""
    head = _refused_then_loud(500, refused=200, before=0)
    assert len(head) == (300 + 500) * 32


def test_a_refused_run_that_ends_is_not_carried_into_the_next_sentence():
    """The detector's own "no" ends the run: the next sentence opens with the
    plain pre-roll, not the chatter that was refused before it."""
    answers = [True] * 60 + [False] * 40 + [True]
    gate = VadGate(ScriptedDetector(answers), prefix_padding_ms=300,
                   min_rms=0.03, floor_grace_s=0, head_hold_ms=1500)
    for _ in range(60):
        assert gate.feed(QUIET_CHUNK) == []          # 0.6 s of refused chatter
    silence = b"\x00\x00" * (FRAME // 2)
    for _ in range(40):
        assert gate.feed(silence) == []              # the run ended
    actions = gate.feed(LOUD_CHUNK)
    assert actions[1][1] == silence * 30, "only the 300 ms pre-roll of silence"


def test_focus_letting_a_sentence_in_late_keeps_its_head(monkeypatch):
    """The 28 Oct path itself: focus says no ("no face in front") for the
    first frames, then yes on a near voice -- the gate re-asks every frame."""
    from app import focus

    verdicts = iter([(False, "no face in front")] * 60 + [(True, "near voice")])
    monkeypatch.setattr(focus, "enabled", lambda: True)
    monkeypatch.setattr(focus, "may_open", lambda t, rms=None: next(verdicts))
    gate = VadGate(ScriptedDetector([True] * 61), prefix_padding_ms=300,
                   floor_grace_s=0, head_hold_ms=1500)
    for _ in range(60):
        assert gate.feed(QUIET_CHUNK) == []
    actions = gate.feed(LOUD_CHUNK)
    assert [k for k, _ in actions][:2] == ["start", "audio"]
    assert actions[1][1] == QUIET_CHUNK * 60, "the 0.6 s focus refused"


def test_for_session_carries_the_hold(monkeypatch):
    from app import vad_gate

    monkeypatch.setattr(settings, "vad_mode", "local")
    monkeypatch.setattr(settings, "vad_head_hold_ms", 1500)
    monkeypatch.setattr(vad_gate, "_make_detector", lambda: ScriptedDetector([]))
    assert vad_gate.for_session()._head_max == 1500 * 32


def test_the_robot_sets_the_hold_and_the_pc_default_is_off():
    """Off by default: a pull must not change what the sales-room PC sends."""
    from pathlib import Path

    from app import config

    assert "VAD_HEAD_HOLD_MS\", \"0\"" in Path(config.__file__).read_text(encoding="utf-8")
    env = (Path(__file__).resolve().parent.parent / "data" / "robot-server" / "robot.env").read_text(
        encoding="utf-8")
    assert "\nVAD_HEAD_HOLD_MS=1500\n" in env


def test_the_real_detector_sentence_let_in_late_arrives_whole(monkeypatch):
    """Real Silero over the committed TTS turn: its first 1.0 s turned down
    to the level the 28 Oct denials had (frames <= 0.047 rms -- Silero still
    hears speech there) and a door at 0.06 (FOCUS_NEAR_RMS on the robot)
    refusing it, the rest at its own level. Without the hold the model gets
    the sentence from 0.3 s before the open; with it, from before the
    sentence began."""
    import numpy as np

    from tests.test_vad_gate import _committed_speech, _real_detector_or_skip

    speech = np.frombuffer(_committed_speech(repeats=1), "<i2").astype("float32")
    soft = speech.copy()
    soft[:16000] *= 0.2                                 # the first second, under the door
    lead = np.zeros(8000, dtype="float32")               # 0.5 s of room before it
    stream = np.concatenate([lead, soft])
    pcm = np.clip(stream, -32768, 32767).astype("<i2").tobytes()

    def forwarded_before_open(hold):
        gate = VadGate(_real_detector_or_skip(monkeypatch, "the head hold on real speech"),
                       prefix_padding_ms=300, min_rms=0.06, floor_grace_s=0, head_hold_ms=hold)
        t = 0.0
        for i in range(0, len(pcm), 640):
            actions = gate.feed(pcm[i:i + 640])
            if actions and actions[0][0] == "start":
                return t, len(actions[1][1]) / 32000.0 if len(actions) > 2 else 0.0
            t += 0.02
        return None, 0.0

    opened, plain = forwarded_before_open(0)
    opened2, held = forwarded_before_open(1500)
    assert opened is not None and opened == opened2
    assert opened > 0.5 + 0.8, "the floor really did refuse the soft start"
    assert plain == pytest.approx(0.3, abs=0.03)
    # the speech started at 0.5 s: with the hold, the head reaches back to it
    assert opened - held <= 0.5 + 0.05, (opened, held)
