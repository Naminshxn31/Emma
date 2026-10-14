"""
Listening focus — talk to the person in front of the robot, know who spoke.

Owner, 9 Oct 2026: the robot answered two people chatting beside it. The
spec is docs/listening-focus-2026-10-09.md; app/focus.py is the arbiter.

The producers (active speaker, voice prints, DOA) are other modules written
in parallel, so every one of them is a stub here: what is tested is the
arbiter's rules, the doors it sits behind (VadGate, the Gemini provider, the
session) and that every switch off leaves today's behaviour exactly as it
was.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

import numpy as np
import pytest

from app import focus
from app.config import settings
from app.vad_gate import VadGate

T = 1000.0


@pytest.fixture
def rows(monkeypatch):
    """Turn-log rows focus writes, captured instead of written."""
    from app import turnlog

    out: list[dict] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **f: out.append({"event": event, **f}))
    return out


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "vad_silence_ms", 500)


def face(track=1, t=T, bearing=0.0, px=150, yaw=0.0, name=None):
    return focus.FaceObs(track=track, t=t, bearing_deg=bearing, width_px=px,
                         yaw_deg=yaw, name=name)


# ---- rule 1: off is today ----------------------------------------------

def test_off_allows_everything_and_writes_nothing(rows):
    """FOCUS_ENABLED=false (the default): no rule runs, no row is written —
    the sales room has no robot camera and must behave byte-identically."""
    assert focus.may_open(T) == (True, focus.OFF)
    assert focus.segment_closed(1, T, T + 2) is None
    assert rows == []


# ---- rule 2: a face in front -------------------------------------------

def test_a_walking_or_gesturing_robot_hears_every_voice(on, rows, monkeypatch):
    """Review 12 Oct: rule 2 dropped "หยุด" said from beside or behind a robot
    on a walk -- out of the forward camera -- so the model never heard it and
    never called stop. While the robot moves, every voice goes through; a
    gesture's "started" stops counting after BODY_MOVING_MAX_S."""
    import time as _time

    from app import robot_body, robot_face_link

    focus.observe_faces([], T)                      # the camera works; nobody in front
    assert focus.may_open(T + 0.1) == (False, focus.NO_FACE)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "active")
    assert focus.may_open(T + 0.2) == (True, focus.ROBOT_MOVING)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "idle")
    monkeypatch.setitem(robot_body.STATE, "state", "started")
    monkeypatch.setitem(robot_body.STATE, "at", _time.time())
    assert focus.may_open(T + 0.3) == (True, focus.ROBOT_MOVING)
    monkeypatch.setitem(robot_body.STATE, "at", _time.time() - focus.BODY_MOVING_MAX_S - 1)
    assert focus.may_open(T + 0.4) == (False, focus.NO_FACE)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: 1 / 0)
    assert focus.may_open(T + 0.5) == (False, focus.NO_FACE)   # never raises


def test_no_face_in_front_denies(on, rows):
    focus.observe_faces([], T)                      # the camera works; nobody
    assert focus.may_open(T + 0.1) == (False, focus.NO_FACE)
    focus.observe_faces([face(t=T + 0.2)], T + 0.2)
    assert focus.may_open(T + 0.3) == (True, focus.OK)


def test_a_conversation_under_way_is_heard_when_the_camera_loses_the_face(on, rows, monkeypatch):
    """13 Oct, robot logs: 1,065 segments denied "no face in front" to 86
    opened -- a guest at the chest screen is under the head camera, or so
    close the face is found for one frame and lost. After a segment the rules
    let through, FOCUS_CONVERSE_HOLD_S waives the face; only such a segment
    renews it (chained on its own opens, one word kept the room open)."""
    monkeypatch.setattr(settings, "focus_converse_hold_s", 20.0)
    focus.observe_faces([face(t=T)], T)
    assert focus.may_open(T + 0.1) == (True, focus.OK)
    focus.observe_faces([], T + 5.0)                 # the face is lost
    assert focus.may_open(T + 5.1) == (True, focus.CONVERSING)
    focus.observe_faces([], T + 19.0)                # the camera still runs
    assert focus.may_open(T + 19.0) == (True, focus.CONVERSING)
    # its own opens do not renew it: 20 s after the face-verified one, over
    focus.observe_faces([], T + 20.4)
    assert focus.may_open(T + 20.5) == (False, focus.NO_FACE)
    # and a new session starts without one
    focus.observe_faces([face(t=T + 30)], T + 30)
    assert focus.may_open(T + 30.1)[0]
    focus.reset()
    focus.observe_faces([], T + 33)                  # past FOCUS_HOLD_S of that face
    assert focus.may_open(T + 33.1) == (False, focus.NO_FACE)
    assert any(r.get("reason") == focus.CONVERSING and r.get("conversing") for r in rows)


def test_a_voice_at_the_robot_is_heard_without_a_face(on, monkeypatch):
    """FOCUS_NEAR_RMS: room chatter measured 0.02-0.04, opened segments p50
    0.057 -- a voice this loud is somebody at the robot. It never starts a
    conversation hold of its own."""
    monkeypatch.setattr(settings, "focus_near_rms", 0.06)
    monkeypatch.setattr(settings, "focus_converse_hold_s", 20.0)
    focus.observe_faces([], T)
    assert focus.may_open(T + 0.1, rms=0.03) == (False, focus.NO_FACE)
    assert focus.may_open(T + 0.2, rms=0.09) == (True, focus.NEAR_VOICE)
    assert focus.may_open(T + 2.0, rms=0.03) == (False, focus.NO_FACE)


def test_the_guest_answering_her_is_heard_without_a_face(on, rows, monkeypatch):
    """13 Oct, robot logs: every one of 983 "no face in front" denials had
    no face at all -- the converse hold, renewed only by a face-verified
    open, rarely started. FOCUS_REPLY_WINDOW_S: a voice that starts within
    the window after her voice ends (greeting or answer) is the reply. Her
    next turn renews it; a quiet guest past the window needs a face again."""
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    focus.observe_faces([], T)
    assert focus.may_open(T + 0.1) == (False, focus.NO_FACE)
    focus.her_turn_ended(T + 3.0)                    # the greeting plays out at +3 s
    assert focus.may_open(T + 2.0) == (True, focus.REPLY)
    focus.observe_faces([], T + 12.5)
    assert focus.may_open(T + 12.9) == (True, focus.REPLY)
    focus.observe_faces([], T + 13.2)
    assert focus.may_open(T + 13.2) == (False, focus.NO_FACE)
    focus.her_turn_ended(T + 20.0)                   # she answered again
    focus.observe_faces([], T + 25.0)
    assert focus.may_open(T + 25.0) == (True, focus.REPLY)
    assert any(r.get("reason") == focus.REPLY and r.get("reply") for r in rows)
    focus.reset()                                    # a new call starts without one
    focus.observe_faces([], T + 26.0)
    assert focus.may_open(T + 26.0) == (False, focus.NO_FACE)


def test_the_reply_window_is_off_by_default(on):
    assert settings.focus_reply_window_s == 0
    focus.observe_faces([], T)
    focus.her_turn_ended(T)
    assert focus.may_open(T + 0.5) == (False, focus.NO_FACE)


def test_both_waivers_are_off_by_default(on):
    assert settings.focus_converse_hold_s == 0 and settings.focus_near_rms == 0
    focus.observe_faces([face(t=T)], T)
    assert focus.may_open(T + 0.1)[0]
    focus.observe_faces([], T + 5.0)
    assert focus.may_open(T + 5.1, rms=0.5) == (False, focus.NO_FACE)


def test_a_face_too_small_or_turned_away_is_not_engaged(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_face_min_px", 90)
    monkeypatch.setattr(settings, "focus_yaw_max_deg", 35.0)
    focus.observe_faces([face(px=60)], T)
    assert focus.may_open(T) == (False, focus.NO_FACE), "somebody across the room"
    focus.observe_faces([face(px=150, yaw=60.0)], T + 0.1)
    assert focus.may_open(T + 0.1) == (False, focus.NO_FACE), "talking to the person beside them"
    focus.observe_faces([face(px=150, yaw=-30.0)], T + 0.2)
    assert focus.may_open(T + 0.2)[0]


def test_the_facing_rule_is_off_at_zero(on, monkeypatch):
    """Owner, 9 Oct 2026: "การหันหน้าเข้ากล้องเอาออกดีกว่า" -- a customer
    reading the chest screen or turned to a companion is still in front."""
    monkeypatch.setattr(settings, "focus_yaw_max_deg", 0.0)
    focus.observe_faces([face(px=150, yaw=75.0)], T)
    assert focus.may_open(T) == (True, focus.OK)
    focus.observe_faces([face(px=20, yaw=0.0, t=T + 3)], T + 3)
    assert focus.may_open(T + 3)[0] is False, "size still decides who is in front"


def test_a_call_a_person_started_is_heard_from_outside_the_lens(on, monkeypatch):
    """The button / wake word: no face needed (rule 2 is for camera-opened
    calls only); a camera-opened (summoned) call still needs one."""
    monkeypatch.setattr(settings, "focus_require_face", True)
    focus.observe_faces([], T)
    focus.begin_session(summoned=False)
    assert focus.may_open(T + 0.1) == (True, focus.OK)
    focus.begin_session(summoned=True)
    assert focus.may_open(T + 0.2) == (False, focus.NO_FACE)


def test_a_person_started_call_still_obeys_the_mouth_rule(on, monkeypatch):
    """Out of the lens is fine; a face IN the lens whose mouth is proven and
    silent still means someone else spoke."""
    focus.begin_session(summoned=False)
    focus.observe_faces([face(t=T)], T)
    focus.observe_mouth(1, T, 0.9)                     # proven: seen moving once
    for i in range(1, 8):
        focus.observe_faces([face(t=T + i * 0.1)], T + i * 0.1)
        focus.observe_mouth(1, T + i * 0.1, 0.0)
    assert focus.may_open(T + 0.75) == (False, focus.SILENT_FACES)


def test_a_face_counts_for_focus_hold_s_after_it_was_seen(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_hold_s", 1.5)
    focus.observe_faces([face(t=T)], T)
    focus.observe_faces([], T + 1.0)                # a missed detection
    assert focus.may_open(T + 1.0)[0], "a blink of the detector is not nobody"
    focus.observe_faces([], T + 2.0)
    assert focus.may_open(T + 2.0) == (False, focus.NO_FACE)


def test_camera_dead_with_require_face_denies_and_says_no_camera(on, rows):
    """Fail closed per switch: the robot that silently fell back to hearing
    the whole room is the failure this exists to end — and the reason must
    say *camera*, or "she stopped listening" is undiagnosable."""
    assert focus.may_open(T) == (False, focus.NO_CAMERA), "never a frame"
    focus.observe_faces([face(t=T)], T)
    assert focus.may_open(T + 0.5)[0]
    stale = T + focus.CAMERA_STALE_S + 0.5
    assert focus.may_open(stale) == (False, focus.NO_CAMERA), "frames stopped"
    deny = [r for r in rows if r["event"] == "focus_deny"]
    assert deny and deny[-1]["reason"] == focus.NO_CAMERA and deny[-1]["camera"] is False


def test_without_require_face_nobody_in_view_is_allowed(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_require_face", False)
    assert focus.may_open(T)[0]
    focus.observe_mouth(1, T, 0.0)
    focus.observe_doa(T, 80.0, 0.9)
    assert focus.may_open(T + 0.1)[0], "rules 3-4 compare against faces in front; none here"


# ---- rule 3: a mouth in front moves -------------------------------------

def test_silent_faces_in_front_deny_when_mouth_activity_exists(on):
    focus.observe_faces([face(t=T)], T)
    focus.observe_mouth(1, T - 5.0, 0.8)            # seen talking earlier: a mouth it can see
    focus.observe_mouth(1, T - 0.2, 0.05)
    focus.observe_mouth(1, T - 0.1, 0.10)
    assert focus.may_open(T) == (False, focus.SILENT_FACES), "someone off-camera spoke"
    focus.observe_mouth(1, T + 0.05, 0.8)
    assert focus.may_open(T + 0.1) == (True, focus.OK)


def test_no_mouth_data_skips_rule_three(on):
    """Fail open per input: the active speaker off or crashed is not "the
    faces are silent"."""
    focus.observe_faces([face(t=T)], T)
    focus.observe_mouth(1, T - 5.0, 0.0)            # stale — outside the window
    assert focus.may_open(T)[0]


def test_a_mouth_never_seen_moving_is_never_called_silent(on):
    """A face mask: 2d106det is happy with the face and its lips never move
    (measured on t1.jpg with a painted mask over a moving jaw: activity
    0.00, max 0.08). Denying "faces in front are silent" there made the
    masked customer deaf for the whole visit. Until the active speaker has
    seen THIS mouth move, its zeros are a missing input, not silence."""
    masked, other = face(track=1, bearing=0.0), face(track=2, bearing=30.0)
    focus.observe_faces([masked], T)
    for k in range(10):
        focus.observe_mouth(1, T - 0.5 + k * 0.05, 0.02)
    assert focus.may_open(T) == (True, focus.OK), "the masked customer is heard"
    label = focus.segment_closed(1, T - 0.5, T + 1.0)
    assert label is not None and label.why == "only_face", "not 'off_camera'"

    # A mouth that has moved once is a mouth it can see: then silence counts.
    focus.observe_faces([other], T + 2)
    focus.observe_mouth(2, T + 2.0, 0.9)
    for k in range(10):
        focus.observe_mouth(2, T + 3.0 + k * 0.05, 0.02)
    focus.observe_faces([other], T + 3.5)
    assert focus.may_open(T + 3.5) == (False, focus.SILENT_FACES)


def test_a_glance_away_inside_the_hold_still_counts(on, monkeypatch):
    """Spec rule 2: a face engaged WITHIN FOCUS_HOLD_S. The newest frame
    alone used to decide, so one glance at a companion (or one noisy
    keypoint) denied the customer who faced the robot a moment before."""
    monkeypatch.setattr(settings, "focus_hold_s", 1.5)
    focus.observe_faces([face(t=T, yaw=0.0)], T)
    focus.observe_faces([face(t=T + 0.3, yaw=60.0)], T + 0.3)    # turned for a frame
    assert focus.may_open(T + 0.4) == (True, focus.OK)
    focus.observe_faces([face(t=T + 1.9, yaw=60.0)], T + 1.9)
    assert focus.may_open(T + 2.0) == (False, focus.NO_FACE), "turned away past the hold"


def test_a_face_the_greeter_greets_is_never_too_small_to_hear(on, monkeypatch):
    """The greeter greets from FACE_MIN_PX (strangers 1.3x); the owner's room
    runs 50. With FOCUS_FACE_MIN_PX=90 everyone greeted at 50-89 px was
    spoken to, then denied "no face in front" for answering."""
    from app import active_speaker

    monkeypatch.setattr(settings, "face_min_px", 50)
    monkeypatch.setattr(settings, "focus_face_min_px", 90)
    focus.observe_faces([face(t=T, px=65)], T)                  # a greeted stranger (>= 1.3 x 50)
    assert focus.may_open(T) == (True, focus.OK)
    assert focus.face_min_px() == 50
    assert active_speaker.Engine(landmarker=lambda f, b: []).min_px == pytest.approx(40.0), \
        "the lips are measured on every face focus can count"
    monkeypatch.setattr(settings, "face_min_px", 110)           # the default: focus's own bar
    assert focus.face_min_px() == 90


# ---- rule 4: the voice comes from where a face is -----------------------

def test_doa_far_from_every_face_denies(on, rows):
    focus.observe_faces([face(t=T, bearing=10.0)], T)
    focus.observe_doa(T - 0.1, -40.0, 0.9)
    assert focus.may_open(T) == (False, focus.ELSEWHERE)
    assert [r for r in rows if r["event"] == "focus_deny"][-1]["doa_off"] == 50.0


def test_doa_near_a_face_or_unsure_or_none_is_allowed(on):
    focus.observe_faces([face(t=T, bearing=10.0)], T)
    focus.observe_doa(T - 0.1, 20.0, 0.9)
    assert focus.may_open(T)[0], "within the tolerance"
    focus._forget_everything()
    focus.observe_faces([face(t=T, bearing=10.0)], T)
    focus.observe_doa(T - 0.1, -60.0, 0.2)
    assert focus.may_open(T)[0], "an unsure bearing is not evidence"
    focus._forget_everything()
    focus.observe_faces([face(t=T, bearing=10.0)], T)
    focus.observe_doa(T - 0.1, None, 0.9)
    assert focus.may_open(T)[0], "None = no single source, not 'elsewhere'"


# ---- rule 5: another voice ----------------------------------------------

def _main_speaker_then_another(t0=T):
    """Two segments the face in front was seen speaking (cluster 1), then a
    third from cluster 2 that nobody in view said."""
    focus.observe_faces([face(t=t0)], t0)
    for seg, start in ((1, t0), (2, t0 + 3)):
        focus.observe_faces([face(t=start)], start)
        focus.observe_mouth(1, start + 0.5, 0.9)
        focus.segment_closed(seg, start, start + 2.0)
        focus.observe_voice(seg, start, start + 2.0, cluster=1, confidence=0.9)
    focus.observe_faces([face(t=t0 + 6)], t0 + 6)
    focus.segment_closed(3, t0 + 6, t0 + 8)
    focus.observe_voice(3, t0 + 6, t0 + 8, cluster=2, confidence=0.9)


def test_after_another_voice_only_a_moving_mouth_reopens(on):
    _main_speaker_then_another()
    t = T + 8.5
    focus.observe_faces([face(t=t)], t)
    assert focus.may_open(t) == (False, focus.ANOTHER_VOICE)
    focus.observe_mouth(1, t + 0.1, 0.9)
    assert focus.may_open(t + 0.2)[0], "a customer in front speaking opens it"


def test_another_voice_rule_expires_so_the_robot_is_never_deaf(on):
    """Once denied, nothing new reaches the voice prints — a rule waiting
    for the other voice to stop would never see it stop."""
    _main_speaker_then_another()
    t = T + 8 + focus.ANOTHER_VOICE_HOLD_S + 0.5
    focus.observe_faces([face(t=t)], t)
    assert focus.may_open(t)[0]


# ---- the turn log -------------------------------------------------------

def test_a_denied_utterance_is_one_row_not_one_per_frame(on, rows):
    """Silero says "speech" on every 20 ms frame of a denied utterance; the
    log gets the burst, with numbers only."""
    focus.observe_faces([face(px=40, t=T)], T)
    for i in range(50):
        focus.may_open(T + i * 0.02)
    deny = [r for r in rows if r["event"] == "focus_deny"]
    assert len(deny) == 1
    assert deny[0]["reason"] == focus.NO_FACE and deny[0]["px"] == 40
    focus.may_open(T + 3.0)                         # a second utterance later
    assert len([r for r in rows if r["event"] == "focus_deny"]) == 2


def test_rows_carry_numbers_never_names_or_text(on, rows):
    focus.observe_faces([face(t=T, name="แก้ว")], T)
    focus.observe_mouth(1, T + 0.3, 0.9)
    focus.may_open(T + 0.4, rms=0.0312)
    focus.segment_closed(1, T, T + 2.0)
    opened = [r for r in rows if r["event"] == "focus_open"][0]
    assert opened["rms"] == 0.0312 and opened["faces"] == 1
    for row in rows:
        assert "แก้ว" not in repr(row), row
        assert "text" not in row and "name" not in row
    label = [r for r in rows if r["event"] == "focus_label"][0]
    assert label["named"] is True


# ---- who spoke ----------------------------------------------------------

def test_mouth_picks_the_speaker_and_ordinals_follow_first_appearance(on):
    a, b = face(track=11, bearing=20.0), face(track=12, bearing=-20.0)
    focus.observe_faces([a, b], T)
    focus.observe_mouth(11, T + 0.5, 0.05)
    focus.observe_mouth(12, T + 0.5, 0.9)
    first = focus.segment_closed(1, T, T + 2.0)
    assert (first.ordinal, first.track, first.why) == (1, 12, "mouth")

    focus.observe_faces([a, b], T + 3)
    focus.observe_mouth(11, T + 3.5, 0.9)
    focus.observe_mouth(12, T + 3.5, 0.0)
    second = focus.segment_closed(2, T + 3, T + 5.0)
    assert (second.ordinal, second.track) == (2, 11)

    focus.observe_faces([a, b], T + 6)
    focus.observe_mouth(12, T + 6.5, 0.9)
    focus.observe_mouth(11, T + 6.5, 0.0)
    assert focus.segment_closed(3, T + 6, T + 8.0).ordinal == 1


def test_doa_breaks_a_mouth_tie(on):
    focus.observe_faces([face(track=1, bearing=30.0), face(track=2, bearing=-30.0)], T)
    focus.observe_mouth(1, T + 0.5, 0.8)
    focus.observe_mouth(2, T + 0.5, 0.75)
    focus.observe_doa(T + 0.6, -28.0, 0.9)
    label = focus.segment_closed(1, T, T + 2.0)
    assert (label.track, label.why) == (2, "mouth+doa")


def test_the_hangover_does_not_vote(on, monkeypatch):
    """t1 includes VAD_SILENCE_MS of quiet after the sentence; a mouth that
    moved in it (the next person starting to answer) is not the speaker."""
    monkeypatch.setattr(settings, "vad_silence_ms", 900)
    focus.observe_faces([face(track=1, bearing=20.0), face(track=2, bearing=-20.0)], T)
    focus.observe_mouth(1, T + 0.8, 0.6)            # during the sentence
    focus.observe_mouth(2, T + 0.8, 0.0)
    for at in (2.4, 2.6, 2.8):                      # inside the hangover
        focus.observe_mouth(2, T + at, 1.0)
    assert focus.segment_closed(1, T, T + 3.0).track == 1


def test_a_lone_face_without_mouth_or_doa_is_a_weak_label(on):
    focus.observe_faces([face(track=4)], T)
    label = focus.segment_closed(1, T, T + 2.0)
    assert (label.track, label.why, label.confidence) == (4, "only_face", 0.5)


def test_nobody_in_view_gives_no_label(on):
    focus.observe_faces([], T)
    assert focus.segment_closed(1, T, T + 2.0) is None


def test_the_voice_carries_the_person_when_the_face_is_gone(on, rows):
    focus.observe_faces([face(track=5)], T)
    focus.observe_mouth(5, T + 0.5, 0.9)
    assert focus.segment_closed(1, T, T + 2.0).ordinal == 1
    focus.observe_voice(1, T, T + 2.0, cluster=7, confidence=0.9)

    later = T + 10
    focus.observe_faces([], later)                  # looked away
    assert focus.segment_closed(2, later, later + 2.0) is None
    focus.observe_voice(2, later, later + 2.0, cluster=7, confidence=0.9)
    voice = [r for r in rows if r["event"] == "focus_voice"][-1]
    assert voice["ordinal"] == 1 and voice["late"] is True

    # A print that is already in when the segment closes labels it directly.
    focus.observe_voice(3, later + 3, later + 5, cluster=7, confidence=0.9)
    label = focus.segment_closed(3, later + 3, later + 5)
    assert (label.ordinal, label.why) == (1, "voice")


def test_a_returning_face_is_merged_back_by_its_voice(on):
    focus.observe_faces([face(track=5)], T)
    focus.observe_mouth(5, T + 0.5, 0.9)
    focus.segment_closed(1, T, T + 2)
    focus.observe_voice(1, T, T + 2, cluster=7, confidence=0.9)
    focus.observe_faces([face(track=9, t=T + 10)], T + 10)   # new track, same person
    focus.observe_mouth(9, T + 10.5, 0.9)
    assert focus.segment_closed(2, T + 10, T + 12).ordinal == 2
    focus.observe_voice(2, T + 10, T + 12, cluster=7, confidence=0.9)
    focus.observe_mouth(9, T + 20.5, 0.9)
    focus.observe_faces([face(track=9, t=T + 20)], T + 20)
    assert focus.segment_closed(3, T + 20, T + 22).ordinal == 1, "merged into the first"
    assert [p["ordinal"] for p in focus.snapshot()["people"]] == [1]


def test_a_print_from_an_ended_session_is_dropped(on, rows):
    focus.reset()                                    # a new conversation began now
    focus.observe_voice(999, 1.0, 2.0, cluster=3, confidence=0.9)
    assert not any(r["event"] == "focus_voice" for r in rows)
    assert 999 not in focus._voice


def test_a_label_names_only_the_face_the_camera_named_in_this_segment(on):
    """FaceObs.name is consent-checked by the publisher on every frame; a
    label uses only the name of the face seen speaking in its own window —
    so a revocation (the publisher sending None) drops it from the next
    label, and a label carried by the voice alone never has one."""
    focus.observe_faces([face(track=3, name="แก้ว")], T)
    focus.observe_mouth(3, T + 0.5, 0.9)
    label = focus.segment_closed(1, T, T + 2)
    assert label.name == "แก้ว"
    assert focus.context_note(label) == "[ผู้พูด: คุณแก้ว]"
    focus.observe_voice(1, T, T + 2, cluster=5, confidence=0.9)

    focus.observe_faces([face(track=3, t=T + 3, name=None)], T + 3)   # revoked
    focus.observe_mouth(3, T + 3.5, 0.9)
    revoked = focus.segment_closed(2, T + 3, T + 5)
    assert (revoked.ordinal, revoked.name) == (1, None)
    assert focus.context_note(revoked) == "[ผู้พูด: คนที่ 1]"

    focus.observe_faces([], T + 10)                                    # out of view
    focus.observe_voice(3, T + 10, T + 12, cluster=5, confidence=0.9)
    by_voice = focus.segment_closed(3, T + 10, T + 12)
    assert (by_voice.ordinal, by_voice.why, by_voice.name) == (1, "voice", None)


def test_a_weak_label_is_not_sent():
    weak = focus.SpeakerLabel(ordinal=1, track=None, name=None, confidence=0.3, why="x")
    assert focus.context_note(weak) is None
    assert focus.context_note(None) is None


def test_reset_forgets_the_conversation_not_the_room(on):
    now = time.monotonic()
    focus.observe_faces([face(track=1, t=now - 1.0)], now - 1.0)
    focus.observe_mouth(1, now - 0.9, 0.9)
    focus.segment_closed(1, now - 1.5, now)
    focus.observe_faces([face(track=1, t=now)], now)
    assert focus.snapshot()["people"]
    focus.reset()
    snap = focus.snapshot()
    assert snap["people"] == [] and snap["opens"] == 0
    assert snap["engaged"], "the person standing there is still standing there"


def test_observers_are_bounded(on):
    for i in range(10_000):
        focus.observe_faces([face(track=i, t=T + i * 0.001)], T + i * 0.001)
        focus.observe_mouth(i, T + i * 0.001, 0.5)
        focus.observe_doa(T + i * 0.001, 0.0, 0.9)
    assert len(focus._frames) <= focus.MAX_FRAMES
    assert len(focus._mouth) <= focus.MAX_TRACKS
    assert len(focus._doa) <= focus.MAX_DOA


def test_observers_are_safe_from_many_threads(on):
    def worker(k):
        for i in range(500):
            focus.observe_faces([face(track=k, t=T + i)], T + i)
            focus.observe_mouth(k, T + i, 0.5)
            focus.may_open(T + i)
    threads = [threading.Thread(target=worker, args=(k,)) for k in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not any(th.is_alive() for th in threads)
    focus.snapshot()


# ---- the door: VadGate --------------------------------------------------

class Scripted:
    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


QUIET = b"\x30\x00" * 160
LOUD = (int(0.1 * 32767)).to_bytes(2, "little", signed=True) * 160


def test_a_deny_behaves_exactly_like_the_floor_and_keeps_the_preroll(on, rows):
    """Nothing goes upstream, and the pre-roll keeps rolling — so the face
    turning toward the robot mid-sentence opens the segment with the
    denied words still in its head."""
    gate = VadGate(Scripted([False, True, True]), prefix_padding_ms=100)
    focus.observe_faces([], time.monotonic())       # camera on, nobody in front
    assert gate.feed(QUIET) == []
    assert gate.feed(LOUD) == [], "denied: no actions, not quieter ones"
    assert not gate.in_speech and gate.segments == 0
    assert bytes(gate._preroll).endswith(LOUD), "the denied frame stays in the pre-roll"

    focus.observe_faces([face(t=time.monotonic())], time.monotonic())
    actions = gate.feed(LOUD)
    assert [k for k, _ in actions] == ["start", "audio", "audio"]
    assert actions[1][1] == QUIET + LOUD, "head intact"
    opened = [r for r in rows if r["event"] == "focus_open"]
    assert opened and opened[0]["rms"] == pytest.approx(0.1, abs=0.001)


def test_focus_is_asked_only_after_silence_and_the_floor(on, monkeypatch):
    """The third test at the same door: a muted mic or a far voice never
    reaches the arbiter (and never logs as a focus decision)."""
    asked = []
    monkeypatch.setattr(focus, "may_open", lambda t, rms=None: asked.append(rms) or (True, "ok"))
    gate = VadGate(Scripted([True]), prefix_padding_ms=100)
    gate.feed(b"\x00\x00" * 160)                     # digital silence
    floored = VadGate(Scripted([True]), prefix_padding_ms=100, min_rms=0.05, floor_grace_s=0)
    floored.feed(QUIET)                              # under the floor
    assert asked == []


def test_off_never_asks_and_the_gate_is_the_old_gate(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("asked while FOCUS_ENABLED is off")

    monkeypatch.setattr(focus, "may_open", boom)
    gate = VadGate(Scripted([False, True, True, False]), prefix_padding_ms=100)
    out = [gate.feed(c) for c in (QUIET, LOUD, LOUD, QUIET)]
    assert [[k for k, _ in a] for a in out] == [[], ["start", "audio", "audio"],
                                                ["audio"], ["audio", "end"]]


def test_the_gate_reports_the_closed_segment(monkeypatch):
    gate = VadGate(Scripted([False, True, True, False]), prefix_padding_ms=100)
    gate.keep_audio = True
    before = time.monotonic()
    for c in (QUIET, LOUD, LOUD, QUIET):
        gate.feed(c)
    seg, t0, t1, pcm = gate.take_closed()
    assert seg == 1 and t0 <= before + 0.01 and t1 >= t0
    assert pcm == QUIET + LOUD + LOUD + QUIET, "exactly what went upstream"
    assert gate.take_closed() is None, "once"

    plain = VadGate(Scripted([True, False]), prefix_padding_ms=100)
    plain.feed(LOUD)
    plain.feed(QUIET)
    assert plain.take_closed()[3] == b"", "no audio kept unless voice prints asked"


# ---- the provider: a note that is not a turn ----------------------------

def _provider(monkeypatch, answers, model="gemini-3.8-live"):
    from app import vad_gate
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(vad_gate, "for_session",
                        lambda **_: VadGate(Scripted(answers), prefix_padding_ms=100))
    provider = GeminiProvider("Kore", "x")
    provider.model = model
    calls = []

    class FakeSession:
        async def send_realtime_input(self, **kw):
            calls.append(next(k for k, v in kw.items() if v is not None))

        async def send_client_content(self, **kw):
            calls.append(("client_content", kw))

    provider._session = FakeSession()
    return provider, calls


def test_the_note_rides_inside_the_activity_before_its_end(monkeypatch):
    """Before activity_end, never after: once the activity ends the model is
    generating, and client content then interrupts the reply."""
    provider, calls = _provider(monkeypatch, [False, True, True, False])
    seen = []

    def hook(seg, t0, t1, pcm):
        seen.append((seg, t1 >= t0, pcm))
        return "[ผู้พูด: คนที่ 1]"

    assert provider.watch_segments(hook, keep_audio=True)

    async def run():
        for c in (QUIET, LOUD, LOUD, QUIET):
            await provider.send_audio(c)

    asyncio.run(run())
    kinds = [c if isinstance(c, str) else c[0] for c in calls]
    assert kinds == ["activity_start", "audio", "audio", "audio", "audio",
                     "client_content", "activity_end"], kinds
    note = calls[5][1]
    assert note["turn_complete"] is False, "a note, never a turn"
    assert note["turns"].parts[0].text == "[ผู้พูด: คนที่ 1]"
    assert seen == [(1, True, QUIET + LOUD + LOUD + QUIET)]


def test_no_note_no_client_content(monkeypatch):
    provider, calls = _provider(monkeypatch, [True, False])
    provider.watch_segments(lambda *a: None)

    async def run():
        await provider.send_audio(LOUD)
        await provider.send_audio(QUIET)

    asyncio.run(run())
    assert not any(isinstance(c, tuple) for c in calls)
    assert provider._note_guard is None, "labels off: no stall guard either"


def _labelled_turn(monkeypatch, reply=None):
    """One labelled segment through the provider, then NOTE_STALL_S of
    either nothing or `reply` (a fake server message) from the server."""
    from app.providers import gemini

    monkeypatch.setattr(gemini, "NOTE_STALL_S", 0.05)
    monkeypatch.setitem(gemini._NOTE_STATE, "stalled", False)
    provider, calls = _provider(monkeypatch, [True, False])
    provider.watch_segments(lambda *a: "[ผู้พูด: คนที่ 1]")
    messages = [] if reply is None else [reply]

    async def receive():
        while messages:
            yield messages.pop(0)

    provider._session.receive = receive

    async def run():
        await provider.send_audio(LOUD)
        await provider.send_audio(QUIET)
        if reply is not None:
            async for _ in provider._receive_events():
                pass
        await asyncio.sleep(0.2)

    asyncio.run(run())
    return provider, calls


def test_a_note_that_holds_the_turn_open_is_released_once_then_notes_stop(monkeypatch, rows):
    """google-genai: with turn_complete=False the model "will not return
    until you send turn_complete=True". Unmeasured, so bounded: nothing back
    within NOTE_STALL_S -> complete the turn, and no more notes this process
    — one late reply, never a robot that hears and never answers."""
    from app.providers import gemini

    provider, calls = _labelled_turn(monkeypatch)
    kinds = [c if isinstance(c, str) else c[0] for c in calls]
    assert kinds == ["activity_start", "audio", "audio", "client_content", "activity_end",
                     "client_content"], kinds
    assert calls[-1][1] == {"turn_complete": True}, "an empty turn_complete releases the turn"
    assert gemini._NOTE_STATE["stalled"] is True
    assert any(r["event"] == "focus_note_stalled" for r in rows)
    assert asyncio.run(provider.send_context_note("[ผู้พูด: คนที่ 1]")) is False
    assert len(calls) == 6, "latched: the next customer is not labelled into a stall"


def test_a_reply_that_starts_disarms_the_stall_guard(monkeypatch):
    from app.providers import gemini

    content = types.SimpleNamespace(interrupted=False, input_transcription=None,
                                    output_transcription=types.SimpleNamespace(text="ค่ะ"),
                                    model_turn=None, turn_complete=False)
    message = types.SimpleNamespace(session_resumption_update=None, go_away=None,
                                    usage_metadata=None, tool_call_cancellation=None,
                                    tool_call=None, server_content=content)
    provider, calls = _labelled_turn(monkeypatch, reply=message)
    assert [c[1] for c in calls if isinstance(c, tuple)] == [
        {"turns": calls[3][1]["turns"], "turn_complete": False}], "only the note itself"
    assert gemini._NOTE_STATE["stalled"] is False


def test_an_input_transcript_alone_is_not_a_reply(monkeypatch):
    """A held turn still gets its user audio transcribed — that must not
    count as the model answering."""
    content = types.SimpleNamespace(interrupted=False,
                                    input_transcription=types.SimpleNamespace(text="สวัสดี",
                                                                              finished=None),
                                    output_transcription=None, model_turn=None,
                                    turn_complete=False)
    message = types.SimpleNamespace(session_resumption_update=None, go_away=None,
                                    usage_metadata=None, tool_call_cancellation=None,
                                    tool_call=None, server_content=content)
    _provider_, calls = _labelled_turn(monkeypatch, reply=message)
    assert calls[-1] == ("client_content", {"turn_complete": True})


def test_gemini_31_has_no_non_turn_road_so_nothing_is_sent(monkeypatch):
    """On 3.1 realtime text IS a turn and client content only seeds the
    start — a label must not become a question Emma answers."""
    provider, calls = _provider(monkeypatch, [], model="gemini-3.1-flash-live-preview")
    assert asyncio.run(provider.send_context_note("[ผู้พูด: คนที่ 1]")) is False
    assert calls == []


def test_a_hook_that_crashes_never_costs_the_turn(monkeypatch):
    provider, calls = _provider(monkeypatch, [True, False])

    def hook(*a):
        raise RuntimeError("producer bug")

    provider.watch_segments(hook)

    async def run():
        await provider.send_audio(LOUD)
        await provider.send_audio(QUIET)

    asyncio.run(run())
    assert calls[-1] == "activity_end"


def test_no_local_gate_no_segments():
    from app.providers.gemini import GeminiProvider

    provider = GeminiProvider("Kore", "x")          # conftest pins VAD_MODE=gemini
    assert provider.watch_segments(lambda *a: None) is False


# ---- the session --------------------------------------------------------

@pytest.fixture
def stub_voiceprint(monkeypatch):
    """app/voiceprint.py is another agent's module; this is its contract:
    segment(segment, t0, t1, pcm16) -> calls focus.observe_voice; reset()."""
    import app
    from app import session as session_module

    calls = {"segment": [], "reset": [], "threads": set()}
    done = threading.Event()

    def segment(seg, t0, t1, pcm):
        calls["segment"].append((seg, t0, t1, len(pcm)))
        calls["threads"].add(threading.current_thread().name)
        focus.observe_voice(seg, t0, t1, cluster=4, confidence=0.9)
        done.set()

    def reset():
        calls["reset"].append(threading.current_thread().name)
        done.set()

    stub = types.ModuleType("app.voiceprint")
    stub.segment, stub.reset = segment, reset
    monkeypatch.setitem(sys.modules, "app.voiceprint", stub)
    monkeypatch.setattr(app, "voiceprint", stub, raising=False)
    monkeypatch.setattr(session_module, "_voiceprints", session_module._VoiceprintWorker())
    calls["done"] = done
    return calls


def _session(profile="joke"):
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", profile=profile)
    return sess


def test_session_hands_the_segment_to_focus_then_the_voice_print_thread(
        on, monkeypatch, rows, stub_voiceprint):
    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    now = time.monotonic()
    focus.observe_faces([face(track=2, t=now - 1.0)], now - 1.0)
    focus.observe_mouth(2, now - 1.0, 0.9)
    note = _session()._segment_closed(1, now - 2.0, now, b"\x01\x00" * 3200)
    assert note is None, "labels off: nothing for the model"
    assert stub_voiceprint["done"].wait(5)
    (seg, t0, t1, n), = stub_voiceprint["segment"]
    assert n == 6400 and stub_voiceprint["threads"] == {"voiceprint"}, \
        "off the audio path, on its own thread"
    label = [r for r in rows if r["event"] == "focus_label"][0]
    assert label["segment"] == seg, "focus knew the segment before its voice print"
    deadline = time.monotonic() + 5
    while not any(r["event"] == "focus_voice" for r in rows) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [r for r in rows if r["event"] == "focus_voice"][0]["ordinal"] == 1


def test_a_label_reaches_the_model_only_with_the_switch_and_never_for_the_interpreter(
        on, monkeypatch):
    now = time.monotonic()
    focus.observe_faces([face(track=2, t=now - 1.0)], now - 1.0)
    focus.observe_mouth(2, now - 1.0, 0.9)
    assert _session()._segment_closed(1, now - 2, now, b"") is None
    monkeypatch.setattr(settings, "focus_speaker_labels", True)
    assert _session()._segment_closed(2, now - 2, now, b"") == "[ผู้พูด: คนที่ 1]"
    assert _session("translator")._segment_closed(3, now - 2, now, b"") is None
    monkeypatch.setattr(settings, "focus_enabled", False)
    assert _session()._segment_closed(4, now - 2, now, b"") is None, "focus off = no labels"


def test_a_missing_voiceprint_module_is_tolerated(on, monkeypatch, caplog):
    import app
    from app import session as session_module

    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setitem(sys.modules, "app.voiceprint", None)     # import -> ImportError
    monkeypatch.delattr(app, "voiceprint", raising=False)
    worker = session_module._VoiceprintWorker()
    monkeypatch.setattr(session_module, "_voiceprints", worker)
    _session()._segment_closed(1, 0.0, 1.0, b"\x01\x00" * 10)
    deadline = time.monotonic() + 5
    while not worker._missing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker._missing


def test_a_full_voiceprint_queue_drops_instead_of_waiting(monkeypatch, rows):
    from app import session as session_module

    worker = session_module._VoiceprintWorker()
    monkeypatch.setattr(worker, "_start", lambda: None)          # nobody consumes
    for i in range(worker.MAX_PENDING + 3):
        worker.submit(i, 0.0, 1.0, b"\x00\x00")
    assert len([r for r in rows if r["event"] == "voiceprint_dropped"]) == 3


def test_session_boundary_resets_focus_and_the_voice_prints(on, monkeypatch, stub_voiceprint):
    from app import session as session_module

    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    now = time.monotonic()
    focus.observe_faces([face(track=2, t=now - 1.0)], now - 1.0)
    _session()._segment_closed(1, now - 2, now, b"\x01\x00" * 100)
    assert stub_voiceprint["done"].wait(5)
    stub_voiceprint["done"].clear()
    assert focus.snapshot()["people"]
    session_module._reset_conversation_state()
    assert focus.snapshot()["people"] == []
    assert stub_voiceprint["done"].wait(5)
    assert stub_voiceprint["reset"] == ["voiceprint"], \
        "reset on the voice-print thread, after any print in flight"


def test_watch_segments_is_not_even_asked_with_everything_off():
    class Provider:
        def watch_segments(self, *a, **k):
            raise AssertionError("asked with focus and voice prints off")

    _session()._watch_segments(Provider())


def test_focus_without_local_vad_says_so(on, rows, caplog):
    class Provider:
        def watch_segments(self, *a, **k):
            return False

    _session()._watch_segments(Provider())
    assert rows and rows[0]["event"] == "focus_unavailable"
    assert "VAD_MODE=local" in caplog.text


def _idle_run(monkeypatch, sess, iterations=4):
    from app import session as session_module
    from app.tools import slides

    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    n = {"i": 0}

    async def fake_sleep(_s):
        n["i"] += 1
        if n["i"] > iterations:
            raise asyncio.CancelledError

    async def shut():
        pass

    monkeypatch.setattr(session_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(slides, "shutdown_display", shut)
    try:
        asyncio.run(sess._close_when_nobody_is_there())
    except asyncio.CancelledError:
        pass
    return sent


def test_nobody_in_front_ends_the_conversation_sooner(on, monkeypatch, rows):
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = _session()
    sess.summoned = True                              # the camera opened this call
    sess._greeting_turn_seen = True                   # ...and greeted: a conversation
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)
    focus.observe_faces([], time.monotonic())        # camera working, nobody
    sent = _idle_run(monkeypatch, sess)
    assert sent and sent[-1]["type"] == "idle_timeout"
    assert [r for r in rows if r["event"] == "idle_timeout"][0]["why"] == "no_face"


def test_her_long_answer_is_not_an_empty_room(on, monkeypatch, rows):
    """13 Oct, live on the robot: a tapped facilities question, her 31 s
    answer and its story -- hung up "no_face" at 30 s, mid-sentence, because
    the clock ran from the guest's tap. Her voice holds it; the quiet counts
    from when she stops. Not for ever: past ANSWER_HOLDS_IDLE_S after the
    guest's last words, narration to nobody still ends."""
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = _session()
    sess.summoned = True
    sess._greeting_turn_seen = True
    now = time.monotonic()
    sess._last_heard_at = now - (sess.FOCUS_AWAY_S + 1)
    sess._play_until = now + 4.0                      # she is still talking
    focus.observe_faces([], now)                      # camera working, nobody
    assert _idle_run(monkeypatch, sess) == []
    sess._play_until = now - (sess.FOCUS_AWAY_S + 1)  # she stopped long ago
    sent = _idle_run(monkeypatch, sess)
    assert sent and sent[-1]["type"] == "idle_timeout"
    sess2 = _session()
    sess2.summoned = True
    sess2._greeting_turn_seen = True
    sess2._last_heard_at = now - (sess2.ANSWER_HOLDS_IDLE_S + sess2.FOCUS_AWAY_S + 1)
    sess2._play_until = now + 30.0                    # a narration to nobody
    focus.observe_faces([], time.monotonic())
    sent = _idle_run(monkeypatch, sess2)
    assert sent and sent[-1]["type"] == "idle_timeout"


def test_a_call_a_person_started_is_not_ended_for_being_out_of_the_lens(on, monkeypatch, rows):
    """Rule 2's line, kept by the idle clock too: a button call is heard from
    outside the lens, so 30 s after its last words (mid-answer, when Emma's
    reply runs long) is not the end of it — IDLE_TIMEOUT_S (120 s) is."""
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = _session()
    assert sess.summoned is False
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)
    focus.observe_faces([], time.monotonic())        # camera working, nobody
    assert _idle_run(monkeypatch, sess) == []
    assert not [r for r in rows if r["event"] == "idle_timeout"]


def test_a_dead_camera_is_not_an_empty_room(on, monkeypatch):
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = _session()
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)
    assert _idle_run(monkeypatch, sess) == [], "120 s idle still rules"


def test_someone_in_front_keeps_the_conversation(on, monkeypatch):
    monkeypatch.setattr(settings, "idle_timeout_s", 120)
    sess = _session()
    sess._last_heard_at = time.monotonic() - (sess.FOCUS_AWAY_S + 1)
    focus.observe_faces([face(t=time.monotonic())], time.monotonic())
    assert _idle_run(monkeypatch, sess) == []


# ---- the camera: every face, every frame ---------------------------------

FRAME = np.zeros((480, 640, 3), np.uint8)
KPS = np.array([[0, 0], [10, 0], [5, 5], [2, 8], [8, 8]], np.float32)


def _two_faces(monkeypatch, vec=None):
    from app import faces

    left = faces.Face((40, 100, 200, 260), 0.9, vec, KPS + [80, 150])    # 160 px, image left
    right = faces.Face((450, 120, 570, 240), 0.9, None, KPS + [480, 150])
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [left, right])


def _watcher():
    from app import faces, facewatch

    v = np.zeros(512, np.float32)
    v[0] = 1.0
    gallery = faces.Gallery(["แก้ว"], v[None, :], [{"group": "staff"}])
    return facewatch.Watcher(gallery=gallery, threshold=0.45, confirm_frames=2,
                             cooldown_s=600, min_face_px=50, margin=0.0,
                             name_when_alone=False), v


def test_every_located_face_is_published_with_bearing_and_track(on, monkeypatch, tmp_path):
    from app import enrollment, facewatch

    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")   # no consent file
    monkeypatch.setattr(settings, "face_require_consent", False)
    got = []
    monkeypatch.setattr(focus, "observe_faces", lambda faces, t: got.append((t, faces)))
    watcher, vec = _watcher()
    _two_faces(monkeypatch, vec)
    watcher.see(FRAME, now=10.0)
    watcher.see(FRAME, now=10.2)
    (t1, first), (t2, second) = got
    assert t2 == 10.2 and len(second) == 2
    left, right = second
    assert left.bearing_deg > 0 > right.bearing_deg, "+ = the robot's left (image left)"
    assert (left.width_px, right.width_px) == (160, 120)
    assert [f.track for f in first] == [f.track for f in second], "stable while in view"
    assert left.name == "แก้ว" and right.name is None, \
        "a name only on the face the greeting confirmed, through consent"
    tracks = facewatch.latest_tracks()
    assert [(tr, box) for tr, box, _kps, _t in tracks] == \
        [(left.track, (40, 100, 200, 260)), (right.track, (450, 120, 570, 240))]


def test_a_name_revoked_after_confirmation_is_not_published(on, monkeypatch):
    """The track keeps the name the greeting confirmed, so consent is asked
    again at every publish: revoked mid-visit = gone from the next frame,
    not when the person next walks in."""
    from app import consent

    got = []
    monkeypatch.setattr(focus, "observe_faces", lambda faces, t: got.append(faces))
    monkeypatch.setattr(consent, "load", lambda: {})
    monkeypatch.setattr(consent, "may_name", lambda name, **_: (True, "OK"))
    watcher, vec = _watcher()
    _two_faces(monkeypatch, vec)
    watcher.see(FRAME, now=10.0)
    watcher.see(FRAME, now=10.2)
    assert got[-1][0].name == "แก้ว"
    monkeypatch.setattr(consent, "may_name", lambda name, **_: (False, "revoked"))
    watcher.see(FRAME, now=10.4)
    assert all(f.name is None for f in got[-1])


def test_nobody_in_frame_is_published_as_nobody(on, monkeypatch):
    from app import faces

    got = []
    monkeypatch.setattr(focus, "observe_faces", lambda faces_, t: got.append(faces_))
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [])
    _watcher()[0].see(FRAME, now=1.0)
    assert got == [[]], "[] = the camera works and nobody is there"


def test_the_active_speaker_gets_every_frame_when_on(monkeypatch):
    import app
    from app import facewatch

    monkeypatch.setattr(settings, "asd_enabled", True)
    seen = []
    stub = types.ModuleType("app.active_speaker")
    stub.process_detections = lambda frame, tracks, t: seen.append((frame.shape, tracks, t))
    monkeypatch.setitem(sys.modules, "app.active_speaker", stub)
    monkeypatch.setattr(app, "active_speaker", stub, raising=False)
    monkeypatch.setitem(facewatch._asd_state, "missing", False)
    watcher, _ = _watcher()
    _two_faces(monkeypatch)
    watcher.see(FRAME, now=5.0)
    (shape, tracks, t), = seen
    assert shape == FRAME.shape and t == 5.0 and len(tracks) == 2
    assert all(len(entry) == 4 for entry in tracks), "(track, bbox, kps, t)"


def test_a_missing_active_speaker_module_is_tolerated(monkeypatch):
    import app
    from app import facewatch

    monkeypatch.setattr(settings, "asd_enabled", True)
    monkeypatch.setitem(sys.modules, "app.active_speaker", None)
    monkeypatch.delattr(app, "active_speaker", raising=False)
    monkeypatch.setitem(facewatch._asd_state, "missing", False)
    watcher, _ = _watcher()
    _two_faces(monkeypatch)
    watcher.see(FRAME, now=5.0)                     # must not raise
    assert facewatch._asd_state["missing"] is True


def test_with_focus_and_asd_off_nothing_is_published(monkeypatch):
    from app import facewatch

    def boom(*a, **k):
        raise AssertionError("published while both switches are off")

    monkeypatch.setattr(focus, "observe_faces", boom)
    monkeypatch.setattr(facewatch.Watcher, "_publish", boom)
    watcher, _ = _watcher()
    _two_faces(monkeypatch)
    watcher.see(FRAME, now=5.0)


# ---- the prompt and the greeting -----------------------------------------

@pytest.fixture
def note_road(monkeypatch, tmp_path):
    """Everything a speaker note needs to be SENT (focus.labels_deliverable):
    Gemini, local VAD with its model on disk, a model that has a non-turn
    road for context, no stall latch. Pinned, not read from this machine."""
    from app.providers import gemini

    model = tmp_path / "silero_vad.onnx"
    model.write_bytes(b"x")
    monkeypatch.setattr(settings, "provider", "gemini")
    monkeypatch.setattr(settings, "vad_mode", "local")
    monkeypatch.setattr(settings, "vad_model", str(model))
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-live")
    monkeypatch.setitem(gemini._NOTE_STATE, "stalled", False)
    return gemini


def test_the_prompt_line_appears_only_with_labels_on(monkeypatch, note_road):
    from app.prompts import SPEAKER_LABEL_LINE, build_instructions

    for profile in ("condo", "emma", "joke", "translator"):
        assert SPEAKER_LABEL_LINE not in build_instructions("Embassy World", profile=profile)
    monkeypatch.setattr(settings, "focus_speaker_labels", True)
    assert SPEAKER_LABEL_LINE not in build_instructions("Embassy World", profile="joke"), \
        "labels without focus are never sent, so never explained"
    monkeypatch.setattr(settings, "focus_enabled", True)
    for profile in ("condo", "joke"):
        assert build_instructions("Embassy World", profile=profile).endswith(SPEAKER_LABEL_LINE)
    assert SPEAKER_LABEL_LINE not in build_instructions("Embassy World", profile="translator")


def test_the_named_greeting_stops_contradicting_the_labels(monkeypatch, note_road):
    from app import greeter

    sighting = types.SimpleNamespace(kind="known", name="แก้ว", group="staff")
    default = greeter.greeting_for(sighting)
    assert "ไม่มีระบบจำเสียง" in default, "default text unchanged"
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_speaker_labels", True)
    labelled = greeter.greeting_for(sighting)
    assert "[ผู้พูด: คุณแก้ว]" in labelled and "ไม่มีระบบจำเสียง" not in labelled


def test_the_focus_idle_watcher_never_hangs_up_a_fresh_call(on, monkeypatch):
    """The watcher races under FIRST_COMPLETED: one that returns at once
    ends the session at once (the bug test_voice guards by source). With
    IDLE_TIMEOUT_S off and only focus asking for it, a call that just
    opened — nobody in view yet — must stay up."""
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    sess = _session()                                # _last_heard_at = now
    focus.observe_faces([], time.monotonic())
    assert _idle_run(monkeypatch, sess, iterations=6) == []


# ---- review 10 Oct: labels only where a note can travel -------------------

@pytest.mark.parametrize("breaks", ["model_3_1", "gemini_vad", "no_vad_model", "stalled", "openai"])
def test_no_label_promise_where_no_note_can_be_sent(monkeypatch, note_road, breaks):
    """The prompt line and the named greeting promise "[ผู้พูด: …]" notes.
    Gemini 3.1 has no non-turn road (send_context_note refuses), Gemini-side
    VAD has no segments to label, VAD_MODE=local without its model falls back
    to it, the stall latch stops notes for the process, OpenAI has none —
    and the line used to ride along anyway, the greeting dropping "never use
    the name again" for a speaker check that would never come."""
    from app import greeter
    from app.prompts import SPEAKER_LABEL_LINE, build_instructions

    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_speaker_labels", True)
    assert focus.labels_deliverable(), "the road is open before it is broken"
    if breaks == "model_3_1":
        monkeypatch.setattr(settings, "gemini_model", "gemini-3.1-flash-live-preview")
    elif breaks == "gemini_vad":
        monkeypatch.setattr(settings, "vad_mode", "gemini")
    elif breaks == "no_vad_model":
        monkeypatch.setattr(settings, "vad_model", settings.vad_model + ".missing")
    elif breaks == "stalled":
        monkeypatch.setitem(note_road._NOTE_STATE, "stalled", True)
    elif breaks == "openai":
        monkeypatch.setattr(settings, "provider", "openai")
    assert focus.labels_deliverable() is False
    for profile in ("condo", "joke"):
        assert SPEAKER_LABEL_LINE not in build_instructions("Embassy World", profile=profile)
    sighting = types.SimpleNamespace(kind="known", name="แก้ว", group="staff")
    assert "ไม่มีระบบจำเสียง" in greeter.greeting_for(sighting)


def test_focus_is_off_under_multi_session(on, monkeypatch, rows):
    """MULTI_SESSION: no `_active`, so `focus.reset()` never ran — `_people`
    grew across every tester, and FOCUS_REQUIRE_FACE denied every tester
    whose browser had no robot camera in front of it."""
    monkeypatch.setattr(settings, "multi_session", True)
    monkeypatch.setattr(settings, "focus_require_face", True)
    monkeypatch.setattr(focus, "_multi_warned", False)
    assert focus.enabled() is False
    assert focus.may_open(T) == (True, focus.OFF), "a tester is heard"
    focus.observe_faces([face(t=T)], T)
    focus.observe_mouth(1, T, 0.9)
    assert focus.segment_closed(1, T, T + 2) is None
    assert focus._people == [] and rows == []
    monkeypatch.setattr(settings, "focus_speaker_labels", True)
    assert focus.labels_deliverable() is False


def test_a_superseded_sessions_last_segment_is_not_attributed_to_the_next(
        on, monkeypatch, rows):
    """A takeover resets focus for the new session while the old one is
    still unwinding; a segment the OLD gate closed after that landed in the
    new conversation's attribution (review 10 Oct)."""
    now = time.monotonic()
    old_epoch = focus.epoch()
    focus.reset()                                   # the new session's reset
    new_epoch = focus.epoch()
    assert new_epoch != old_epoch
    focus.observe_faces([face(track=2, t=now - 1.0)], now - 1.0)
    focus.observe_mouth(2, now - 1.0, 0.9)
    assert focus.segment_closed(1, now - 2.0, now, epoch=old_epoch) is None
    assert focus._people == [] and not [r for r in rows if r["event"] == "focus_label"]
    # the session-level door: a superseded session hands nothing on at all
    sess = _session()
    sess._focus_epoch = old_epoch
    submitted = []
    from app import session as session_module

    monkeypatch.setattr(settings, "voiceprint_enabled", True)
    monkeypatch.setattr(session_module._voiceprints, "submit",
                        lambda *a: submitted.append(a))
    assert sess._segment_closed(2, now - 2.0, now, b"\x01\x00" * 3200) is None
    assert submitted == [] and focus._people == []
    # ...and the current conversation is attributed as before
    sess._focus_epoch = new_epoch
    sess._segment_closed(3, now - 2.0, now, b"\x01\x00" * 3200)
    assert len(submitted) == 1
    assert [r for r in rows if r["event"] == "focus_label"]


def test_the_reply_window_does_not_chain_on_chatter_for_ever(on, rows, monkeypatch):
    """Review 13 Oct, replayed on 12 Oct's denials: every answer renewed the
    window, so chatter within 10 s of her voice chained up to 5 answers and
    the call never idled out. After MAX_REPLY_CHAIN turns that answered only
    window-admitted voices it stops renewing; a face (or a near voice, or her
    name) starts the count again."""
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    t = T
    focus.her_turn_ended(t)                           # the greeting
    assert focus.MAX_REPLY_CHAIN == 3
    for _ in range(3):
        focus.observe_faces([], t + 1)
        assert focus.may_open(t + 1) == (True, focus.REPLY)
        t += 5
        focus.her_turn_ended(t)                       # she answered the chatter
    focus.observe_faces([], t + 1)
    assert focus.may_open(t + 1) == (False, focus.NO_FACE), "the chain stops"
    # a face in front is a guest: the window serves them again
    focus.observe_faces([face(t=t + 2)], t + 2)
    assert focus.may_open(t + 2) == (True, focus.OK)
    focus.her_turn_ended(t + 5)
    focus.observe_faces([], t + 20)                   # the face has gone
    assert focus.may_open(t + 6) == (True, focus.REPLY)


def test_a_superseded_calls_turn_does_not_open_the_new_calls_window(on, monkeypatch):
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    old = focus.epoch()
    focus.reset()                                     # a new call took the robot
    focus.her_turn_ended(T, epoch=old)                # the old call's late turn
    focus.observe_faces([], T)
    assert focus.may_open(T + 1) == (False, focus.NO_FACE)
