"""VOICE_NEAR_DOOR: near voices in, far voices out, by the lidar (owner 5 Nov 2026: "พยายาม
ให้หุ่นฟังเสียงคนใกล้ คนไกลออก"), and the voice lock it replaces staying gone.

The real `focus._decide`, the real `lidar_people.Tracker` on synthetic scans of a room,
the real `VadGate` on a scripted detector. Nothing reaches a robot.

What these hold down, each from a bug that already happened in another shape:
* off = no lidar read and no new row field; a typo = off (the sales room's pull);
* every failure opens: no fresh scan, warming up, a tracker that raises;
* moving, her name, a face in front, a call somebody pressed: the door is not asked;
* a still guest does not fall out of "near" when the tracker absorbs them;
* a "no" is silence and the pre-roll keeps the head; an open sentence is never cut;
* shadow changes no decision.
"""
from __future__ import annotations

import importlib.util
import inspect
import math
from pathlib import Path

import pytest

from app import event_walk, focus, lidar_people as lp, near_voice, robot_chassis, robot_face_link, room_screens
from app.config import settings
from app.vad_gate import VadGate
from tests.test_lidar_people import POSE, _legs, _room
from tests.test_vad_gate import LOUD_CHUNK, QUIET_CHUNK, ScriptedDetector

ROOT = Path(__file__).resolve().parents[1]
T = 1000.0
#: Past the warm-up, in scans 0.5 s apart.
WARM_S = 180.0
FAR_RMS = 0.03        # room talk
VOICE_RMS = 0.08      # over FOCUS_NEAR_RMS: "near" by today's level


@pytest.fixture
def rows(monkeypatch):
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def door(monkeypatch, rows):
    """A camera-opened call, no camera frames, today's waivers on, the door on, and a
    tracker that has watched an empty room long enough to have an opinion."""
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_require_face", True)
    monkeypatch.setattr(settings, "focus_near_rms", 0.06)
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    monkeypatch.setattr(settings, "focus_converse_hold_s", 20.0)
    monkeypatch.setattr(settings, "focus_screen_rms", 0.0)
    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "lidar_people", True)
    monkeypatch.setattr(settings, "voice_near_door", "on")
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda: "idle")
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    focus._forget_everything()
    focus.begin_session(summoned=True)
    near_voice.reset()
    lp.reset()
    yield Room()
    lp.reset()
    near_voice.reset()
    focus._forget_everything()


class Room:
    """A lidar scan every 0.5 s of a room the door reads through the real tracker."""

    def __init__(self):
        self.t = T - WARM_S - 1.0
        self.tracker = lp.STATE["tracker"]
        self.pose = POSE

    def scans(self, seconds, extra=(), look=True):
        """`extra` circles stand in the room for this long; the door's watch runs too."""
        end = self.t + seconds
        points = _room(extra)
        while self.t < end:
            self.t += 0.5
            self.tracker.update(points, self.pose, self.t)
            focus.observe_faces([], self.t)     # the camera is alive and sees nobody
            if look and int(self.t * 2) % 2 == 0:
                near_voice.look(self.t)
        return self

    def settled(self):
        return self.scans(WARM_S + 1.0)


def _open(t, rms=FAR_RMS):
    return focus.may_open(t, rms=rms)


# ---- the door itself ------------------------------------------------------------------

def test_nobody_near_refuses_a_voice_todays_waivers_would_let_in(door):
    door.settled()
    t = door.t
    assert _open(t, VOICE_RMS) == (False, focus.NOBODY_NEAR), "FOCUS_NEAR_RMS alone is not distance"
    focus.her_turn_ended(t)
    assert _open(t + 1, FAR_RMS) == (False, focus.NOBODY_NEAR), "nor is the reply window"


def test_somebody_at_the_robot_is_heard_with_no_face(door):
    """12 Oct: 983 of 983 'no face in front' refusals had no face at all, with people
    standing at the chest screen. Near is near: no camera needed, at any level."""
    door.settled().scans(1.0, _legs(20.0, 1.0))
    assert _open(door.t, FAR_RMS) == (True, focus.NEAR_PERSON)


def test_somebody_beyond_the_reach_does_not_count(door):
    door.settled().scans(1.0, _legs(20.0, 2.0))
    assert _open(door.t, FAR_RMS) == (False, focus.NOBODY_NEAR)
    settings.voice_near_m = 2.5
    door.scans(1.0, _legs(20.0, 2.0))
    assert _open(door.t, FAR_RMS) == (True, focus.NEAR_PERSON)


def test_a_voice_too_loud_to_be_far_gets_in_when_the_lidar_sees_nobody(door):
    """A way out for whoever the lidar misses, and on a high bar."""
    door.settled()
    assert _open(door.t, 0.19) == (False, focus.NOBODY_NEAR)
    assert _open(door.t + 0.1, 0.21) == (True, focus.NEAR_VOICE)


def test_the_row_says_what_the_door_saw(door, rows):
    door.settled().scans(1.0, _legs(20.0, 1.0))
    _open(door.t, FAR_RMS)
    row = next(r for r in rows if r["event"] == "focus_open")
    assert row["reason"] == focus.NEAR_PERSON and row["near_state"] == "near"
    assert row["near_m"] == pytest.approx(1.0, abs=0.2) and row["near_why"] == "seen"
    near_voice.STATE["anchor"] = None
    door.scans(30.0)
    _open(door.t, FAR_RMS)
    row = next(r for r in reversed(rows) if r["event"] == "focus_deny")
    assert row["reason"] == focus.NOBODY_NEAR and row["near_state"] == "nobody" and row["near_m"] is None


# ---- when it must not be asked --------------------------------------------------------

def test_while_she_moves_every_voice_passes(door, monkeypatch):
    """Review 12 Oct: a guest's "หยุด" from beside a walking robot never reached the model.
    The door must never come back between a stop and the only thing that can stop her."""
    door.settled()
    for name, fake in (("nav", lambda: "active"),):
        monkeypatch.setattr(robot_face_link, "nav_activity", fake)
        assert _open(door.t, FAR_RMS) == (True, focus.ROBOT_MOVING), name
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda: "idle")
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    assert _open(door.t, FAR_RMS) == (True, focus.ROBOT_MOVING)


def test_her_name_gets_in_from_anywhere(door, monkeypatch):
    monkeypatch.setattr(settings, "focus_wake", True)
    monkeypatch.setattr(settings, "focus_wake_window_s", 10.0)
    door.settled()
    assert _open(door.t, FAR_RMS)[0] is False
    focus.heard_name(door.t)
    assert _open(door.t + 1, FAR_RMS)[0] is True


def _face(t, px=200):
    focus.observe_faces([focus.FaceObs(track=1, t=t, bearing_deg=0.0, width_px=px, yaw_deg=0.0, name=None)], t)


def test_a_face_in_front_is_todays_rule(door):
    door.settled()
    _face(door.t)
    assert _open(door.t, FAR_RMS) == (True, focus.OK)


def test_somebody_just_greeted_is_heard_from_where_they_stand(door):
    """31 Aug: greeted at the door, answered, never heard. 28 of 29 arrivals on this lens
    are lost within seconds, so the face going is not them going."""
    door.settled()
    _face(door.t)
    door.scans(focus.NEAR_FACE_HOLD_S - 2)
    assert _open(door.t, VOICE_RMS) == (True, focus.NEAR_VOICE), "the waiver, as today"
    door.scans(4.0)
    assert _open(door.t, VOICE_RMS) == (False, focus.NOBODY_NEAR)


def test_a_call_somebody_pressed_is_heard_as_it_was(door):
    """Owner 9 Oct: who presses or calls her is heard from outside the lens."""
    door.settled()
    focus.begin_session(summoned=False)
    assert _open(door.t, FAR_RMS) == (True, focus.OK)


def test_at_the_big_screen_a_clear_voice_is_enough_as_the_owner_ruled(door, monkeypatch):
    """29 Oct: people watch the TV there, from beside it, not from beside the robot."""
    monkeypatch.setattr(settings, "focus_screen_rms", 0.03)
    monkeypatch.setattr(room_screens, "at_screen", lambda now=None: "room")
    door.settled()
    assert _open(door.t, 0.04)[0] is True


# ---- every failure opens ---------------------------------------------------------------

def _as_today(allow_reason):
    assert allow_reason in ((False, focus.NO_FACE), (False, focus.NO_CAMERA)), "the rules with the door off"


def test_a_failure_of_any_kind_is_todays_rules(door, monkeypatch, rows):
    door.settled()
    assert _open(door.t, FAR_RMS)[1] == focus.NOBODY_NEAR     # the door really was deciding
    # the poller stopped: a board that does not answer, a walk
    _as_today(_open(door.t + lp.FRESH_S + 1, FAR_RMS))
    # HEAD_FOLLOW off: the tracker is not read at all
    door.settled()
    monkeypatch.setattr(settings, "head_follow", False)
    _as_today(_open(door.t, FAR_RMS))
    monkeypatch.setattr(settings, "head_follow", True)
    door.settled()
    # no chassis on this machine
    monkeypatch.setattr(robot_chassis, "configured", lambda: False)
    _as_today(_open(door.t, FAR_RMS))


def test_a_tracker_that_raises_never_reaches_the_audio_path(door, monkeypatch):
    door.settled()

    def boom(*a, **k):
        raise RuntimeError("no scan")

    monkeypatch.setattr(lp, "people", boom)
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "error")
    _as_today(_open(door.t, FAR_RMS))


def test_a_board_slow_to_answer_gives_no_opinion_and_forgets_nobody(door):
    """A guest the tracker has absorbed cannot be found again: dropping them on a hiccup would
    refuse them for the rest of the call."""
    door.settled()
    legs = _legs(30.0, 1.0)
    door.scans(130.0, legs)
    assert lp.people(door.t) == [] and near_voice.look(door.t)[0] == near_voice.NEAR
    door.t += 5.0
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "stale")
    door.scans(1.0, legs)
    assert near_voice.look(door.t)[0] == near_voice.NEAR


def test_a_walk_clears_the_tracker_and_with_it_the_door_s_opinion(door):
    door.settled().scans(1.0, _legs(30.0, 1.0))
    door.tracker.reset()
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "stale")
    assert near_voice.STATE["anchor"] is None and near_voice.STATE["still_since"] is None


# ---- the lidar losing people -----------------------------------------------------------

def test_a_still_guest_stays_near_after_the_tracker_absorbs_them(door):
    """96.5 s and a person who does not move is background: lidar_people.people() goes
    empty. On the robot p90 of a continuous sighting was 65-89 s, and a call lasts minutes."""
    door.settled()
    legs = _legs(30.0, 1.0)
    door.scans(1.0, legs)
    assert lp.people(door.t), "seen on arrival"
    door.scans(130.0, legs)
    assert lp.people(door.t) == [], "the tracker has let go of them, as measured"
    assert near_voice.look(door.t)[0] == near_voice.NEAR
    assert _open(door.t, FAR_RMS) == (True, focus.NEAR_PERSON)


def test_when_the_guest_leaves_the_door_closes_at_the_next_scan(door):
    door.settled()
    legs = _legs(30.0, 1.0)
    door.scans(130.0, legs)
    assert near_voice.look(door.t)[0] == near_voice.NEAR
    door.scans(1.0)
    assert near_voice.look(door.t)[0] == near_voice.NOBODY
    assert _open(door.t, FAR_RMS) == (False, focus.NOBODY_NEAR)


def test_somebody_passing_does_not_hold_the_door(door):
    door.settled()
    door.scans(1.0, _legs(30.0, 1.0))
    assert near_voice.look(door.t)[0] == near_voice.NEAR
    door.scans(1.0, _legs(80.0, 2.3))
    assert near_voice.look(door.t)[0] == near_voice.NOBODY


def test_a_coat_on_a_chair_does_not_hold_the_door_all_day(door):
    door.settled()
    coat = _legs(30.0, 1.0)
    door.scans(120.0, coat)                 # the tracker has absorbed it...
    assert near_voice.look(door.t)[0] == near_voice.NEAR
    door.scans(near_voice.ANCHOR_MAX_S, coat)   # ...and the spot alone holds only so long
    assert near_voice.look(door.t)[0] == near_voice.NOBODY


def test_after_she_moves_the_door_has_no_opinion_while_it_warms_up(door):
    """Whoever stands there when she stops is background from the first scan: the
    face-approach walk ends one metre from a guest. Refusing them would be the worst
    moment to be deaf."""
    door.settled()
    legs = _legs(0.0, 1.0)
    door.scans(1.0)
    door.pose = {"x": 5.0, "y": 2.0, "yaw": 0.3}
    door.scans(2.0, legs)
    assert lp.people(door.t) == []
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "warming")
    _as_today(_open(door.t, FAR_RMS))
    door.scans(WARM_S - 10, legs)
    assert near_voice.look(door.t)[0] == near_voice.UNKNOWN
    door.scans(15.0, legs)
    assert near_voice.look(door.t)[0] == near_voice.NOBODY, "the known limit: never seen"
    # the moment a leg moves they are found, and kept
    door.scans(1.0, _legs(8.0, 1.0))
    assert near_voice.look(door.t)[0] == near_voice.NEAR


def test_a_robot_that_keeps_moving_never_warms_up(door):
    """Every scan's pose differs: the tracker resets each time and must never read as settled."""
    door.settled()
    for i in range(int(WARM_S * 2 + 20)):
        door.t += 0.5
        door.pose = {"x": i * 0.1, "y": 2.0, "yaw": 0.3}
        door.tracker.update(_room(), door.pose, door.t)
        near_voice.look(door.t)
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "warming")


def test_moving_forgets_who_was_near(door):
    """Something at the same bearing and distance after a move is not the same person: the
    spot alone must not carry an anchor to a new place."""
    legs = _legs(30.0, 1.0)
    door.settled().scans(1.0, legs)
    assert near_voice.STATE["anchor"] is not None
    door.pose = {"x": 9.0, "y": 2.0, "yaw": 0.3}
    door.scans(1.0, legs)
    assert near_voice.STATE["anchor"] is None
    assert near_voice.look(door.t) == (near_voice.UNKNOWN, None, "warming")


# ---- shadow and off ---------------------------------------------------------------------

def test_shadow_changes_no_decision_and_says_what_it_would_do(door, monkeypatch, rows):
    monkeypatch.setattr(settings, "voice_near_door", "shadow")
    door.settled()
    assert _open(door.t, VOICE_RMS) == (True, focus.NEAR_VOICE), "as today"
    row = next(r for r in rows if r["event"] == "focus_open")
    assert row["near_state"] == "nobody" and row["near_would"] == "refuse"
    rows.clear()
    assert _open(door.t + 1, FAR_RMS) == (False, focus.NO_FACE), "as today"
    row = next(r for r in rows if r["event"] == "focus_deny")
    assert row["near_would"] == "refuse"
    rows.clear()
    door.scans(3.0, _legs(20.0, 1.0))        # past the refused burst's one row
    assert _open(door.t, FAR_RMS) == (False, focus.NO_FACE), "as today, though somebody is there"
    row = next(r for r in rows if r["event"] == "focus_deny")
    assert row["near_would"] == "open" and row["near_state"] == "near"
    assert focus.person_near(door.t) is False, "the session's vetoes do not move either"


def test_shadow_records_the_faces_too_that_is_the_lidars_coverage(door, monkeypatch, rows):
    monkeypatch.setattr(settings, "voice_near_door", "shadow")
    door.settled().scans(1.0, _legs(20.0, 1.0))
    _face(door.t)
    assert _open(door.t, FAR_RMS) == (True, focus.OK)
    row = next(r for r in rows if r["event"] == "focus_open")
    assert row["near_state"] == "near" and "near_would" not in row


@pytest.mark.parametrize("value", ["off", "", "OFF ", "onn", "true", None])
def test_off_or_misspelt_reads_nothing_and_adds_nothing(door, monkeypatch, rows, value):
    monkeypatch.setattr(settings, "voice_near_door", value)
    door.settled()
    monkeypatch.setattr(near_voice, "look", lambda *a: pytest.fail("off must not read the lidar"))
    assert _open(door.t, VOICE_RMS) == (True, focus.NEAR_VOICE)
    assert _open(door.t + 1, FAR_RMS) == (False, focus.NO_FACE)
    assert all(not any(k.startswith("near_") for k in r) for r in rows)
    assert focus.person_near(door.t) is False


def test_the_code_default_is_off_and_the_conftest_pins_it():
    source = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert 'os.getenv("VOICE_NEAR_DOOR", "off")' in source
    assert settings.voice_near_door == "off"


def test_no_task_runs_with_the_switch_off():
    near_voice.start()
    assert near_voice.STATE["task"] is None


def test_the_watch_sees_a_silent_person_nobody_at_the_door_asks_about(door, monkeypatch):
    """Somebody stands reading the screen for two minutes, then speaks: with no voice at the
    door nothing would have looked, and the tracker has absorbed them by then."""
    import asyncio

    seen = []
    monkeypatch.setattr(near_voice, "WATCH_EVERY_S", 0.0)
    monkeypatch.setattr(near_voice, "look", lambda now: seen.append(now))

    async def main():
        near_voice.start()
        assert near_voice.STATE["task"] is not None
        for _ in range(1000):
            if seen:
                break
            await asyncio.sleep(0)
        assert seen, "the watch never looked"
        await near_voice.stop()
        assert near_voice.STATE["task"] is None

    asyncio.run(main())


def test_the_servers_startup_starts_it():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "near_voice.start()" in source and "await near_voice.stop()" in source


def test_analyze_log_reads_the_rows(capsys):
    from scripts import analyze_log

    analyze_log.focus([
        {"event": "focus_open", "t": "10:00:00", "reason": "ok", "rms": 0.05, "near_state": "near"},
        {"event": "focus_open", "t": "10:00:01", "reason": "ok", "rms": 0.05, "near_state": "nobody"},
        {"event": "focus_open", "t": "10:00:02", "reason": "near voice", "rms": 0.07, "near_state": "nobody", "near_would": "refuse"},
        {"event": "focus_deny", "t": "10:00:03", "reason": "no face in front", "rms": 0.03, "near_state": "near", "near_would": "open"}])
    out = capsys.readouterr().out
    assert "ประตูคนใกล้" in out
    assert "เสียงที่มีหน้าในกล้อง 2: ไลดาร์เห็นคนใกล้ 1 · ไม่เห็น 1" in out
    assert "วันนี้เปิด→ปฏิเสธ 1 · วันนี้ไม่ฟัง→เปิด 1" in out


def test_robot_env_never_turns_the_door_on_without_a_bench(monkeypatch):
    """`on` waits for docs/near-voice-bench.md. Shadow changes nothing she hears."""
    text = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("VOICE_NEAR_DOOR")]
    assert all(ln == "VOICE_NEAR_DOOR=shadow" for ln in lines), lines


def test_the_prompts_know_nothing_of_it():
    """Byte-identical prompts: it decides what she hears, never what she is told."""
    source = (ROOT / "app" / "prompts.py").read_text(encoding="utf-8")
    assert "near_voice" not in source and "VOICE_NEAR" not in source


# ---- the session's face vetoes --------------------------------------------------------

def test_a_guest_near_under_the_lens_is_not_an_empty_room(door):
    door.settled()
    assert focus.person_near(door.t) is False
    door.scans(1.0, _legs(20.0, 1.0))
    assert focus.person_near(door.t) is True


def test_the_sessions_vetoes_ask_it_where_they_ask_the_big_screen():
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    assert source.count("focus.at_big_screen() is None and not focus.person_near()") == 2


def test_a_near_person_starts_the_reply_chain_again(door):
    """MAX_REPLY_CHAIN counts turns that only the window let in; a person at the robot is
    the same evidence as a face."""
    door.settled().scans(1.0, _legs(20.0, 1.0))
    focus._reply["chain"] = 2
    assert _open(door.t, FAR_RMS)[1] == focus.NEAR_PERSON
    focus.her_turn_ended(door.t)
    assert focus._reply["chain"] == 0


# ---- the gate: same door, same rules --------------------------------------------------

FRAME = len(QUIET_CHUNK)


def _gate(detector_says, **kw):
    return VadGate(ScriptedDetector(detector_says), prefix_padding_ms=300, floor_grace_s=0, **kw)


def test_a_no_is_silence_and_the_head_of_the_sentence_is_kept(door, monkeypatch):
    """Somebody walks up mid-sentence: nothing went up while the door said no, and the
    sentence arrives from its first word (VAD_HEAD_HOLD_MS, the 28 Oct bug)."""
    door.settled()
    now = {"t": door.t}
    monkeypatch.setattr("time.monotonic", lambda: now["t"])
    gate = _gate([True] * 200, head_hold_ms=1500)
    for _ in range(80):
        now["t"] += 0.01
        assert gate.feed(LOUD_CHUNK) == [], "no one near: silence"
    assert gate.in_speech is False
    door.scans(1.0, _legs(20.0, 1.0))
    now["t"] = door.t
    actions = gate.feed(LOUD_CHUNK)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert len(actions[1][1]) == 80 * FRAME, "0.8 s of the refused head comes along"


def test_an_open_sentence_is_never_cut_by_the_door_going_no(door, monkeypatch):
    """It decides at the opening: no second hangover clock."""
    door.settled().scans(1.0, _legs(20.0, 1.0))
    now = {"t": door.t}
    monkeypatch.setattr("time.monotonic", lambda: now["t"])
    gate = _gate([True] * 60 + [False], head_hold_ms=0)
    assert gate.feed(LOUD_CHUNK)[0] == ("start", b"")
    door.scans(1.0)                        # she walked away while still talking
    assert near_voice.look(door.t)[0] == near_voice.NOBODY
    now["t"] = door.t
    for _ in range(59):
        assert [k for k, _ in gate.feed(LOUD_CHUNK)] == ["audio"]
    assert [k for k, _ in gate.feed(LOUD_CHUNK)] == ["audio", "end"]


def test_silence_rms_still_comes_first(door, monkeypatch):
    """1 Sep: a muted mic's dither opened segments and she answered the silence."""
    door.settled().scans(1.0, _legs(20.0, 1.0))
    now = {"t": door.t}
    monkeypatch.setattr("time.monotonic", lambda: now["t"])
    monkeypatch.setattr(near_voice, "look", lambda *a: pytest.fail("not a voice"))
    gate = _gate([True] * 5)
    for _ in range(5):
        assert gate.feed(b"\x01\x00" * (FRAME // 2)) == []
    assert gate.in_speech is False


# ---- the replay ------------------------------------------------------------------------

def test_the_replay_prints_numbers_only(tmp_path, capsys, monkeypatch):
    """data/logs is customers' words: nothing of a transcript may reach its output."""
    import json

    from scripts import replay_near_voice as rp

    words = "ราคาห้องเท่าไหร่ครับ"
    day = tmp_path / "2026-11-04.jsonl"
    rows = [{"t": "10:00:00", "event": "lidar_people", "n": 1, "nearest_m": 1.0, "nearest_deg": 5.0},
            {"t": "10:00:01", "event": "focus_open", "session_id": "a", "reason": "reply to her", "rms": 0.03},
            {"t": "10:00:03", "event": "heard", "session_id": "a", "text": words},
            {"t": "10:00:05", "event": "mic_report", "session_id": "a", "peak": 0.05, "avg": 0.01},
            {"t": "10:05:01", "event": "focus_open", "session_id": "a", "reason": "near voice", "rms": 0.07},
            {"t": "10:05:03", "event": "heard", "session_id": "a", "text": words},
            {"t": "10:05:05", "event": "mic_report", "session_id": "a", "peak": 0.07, "avg": 0.01},
            {"t": "10:06:01", "event": "focus_open", "session_id": "b", "reason": "ok", "rms": 0.07,
             "near_state": "near", "near_m": 1.0},
            {"t": "10:06:02", "event": "focus_deny", "session_id": "b", "reason": "no face in front", "rms": 0.03,
             "near_state": "nobody", "near_would": "refuse"}]
    day.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["replay", "--logs", str(tmp_path)])
    rp.main()
    out = capsys.readouterr().out
    assert words not in out and "voices 2" in out
    assert "lidar <= 1.5 m or >= 0.20" in out and "voices with a face in front 1: lidar near 1" in out


# ---- the voice lock stays gone --------------------------------------------------------

@pytest.mark.parametrize("module", ["app.voice_lock", "app.voice_enroll", "app.voice_designated"])
def test_the_voice_lock_is_gone(module):
    assert importlib.util.find_spec(module) is None


def test_nothing_can_turn_a_voice_print_door_on():
    assert "voice_door" not in inspect.signature(VadGate.__init__).parameters
    for name in ("voice_lock_mode", "voice_enroll_enabled", "voice_switch_file", "voice_lock_scope"):
        assert not hasattr(settings, name), name
    for rel in ("app/vad_gate.py", "app/focus.py", "app/session.py", "app/main.py", "app/event_host.py",
                "app/event_control_server.py", "app/providers/gemini.py", "client/voice-preview.html",
                "client/event-control.html", ".env.example", "data/robot-server/robot.env"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for word in ("voice_lock", "VOICE_LOCK", "voice_enroll", "VOICE_ENROLL", "voice-enroll",
                     "voice-switch", "voice-release", "EmmaVoiceEnroll", "enrollMic"):
            assert word not in text, (rel, word)


def test_voice_clusters_for_focus_stay_and_the_enrolment_helper_left():
    from app import voiceprint

    assert hasattr(voiceprint, "voiced") and not hasattr(voiceprint, "speech_only")
    assert settings.voiceprint_enabled is False


def test_no_route_is_left_behind():
    from app.event_control_server import app as event_app
    from app.main import app as main_app

    paths = {getattr(r, "path", "") for r in main_app.routes} | {getattr(r, "path", "") for r in event_app.routes}
    assert not [p for p in paths if "voice-enroll" in p or "voice-switch" in p or "voice-release" in p]
    assert "/face-scan/start" in paths, "the face scan was not taken with it"


def test_the_hold_menu_keeps_its_order_and_the_slot_is_an_honest_empty_one():
    html = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    slots = html.split("var ACTIONS = [", 1)[1].split("];", 1)[0]
    labels = [part.split("label: '", 1)[1].split("'", 1)[0] for part in slots.split("{") if "label:" in part]
    assert labels == ["สแกนใบหน้า", "นำทาง", "จอใหญ่"]
    assert slots.rstrip().endswith("{}, {}, {}")


def test_the_face_scan_keeps_its_own_hang_up():
    html = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    assert "stopForFaceScan: function () { if (inCall || ws) endCall(); }" in html
    assert "if (window.EmmaFaceScan && window.EmmaFaceScan.isOpen()) return;" in html
