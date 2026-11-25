"""Rehearsal 24 Nov: the host's wireless mic is heard while the ceremony's live window is open, even with a
silent face in the robot's lens (focus rule 3). Closed window / no ceremony: the rules as they were.

And the window stands down rules 3-5 ONLY. The first cut asked the window above rule 2, so on the
summoned call the robot face dials every voice over the far floor opened a turn while it was open --
guests talking near the host's mic (7-8 turns a minute with one talker in that night's simulation).
Rule 2 and each of its waivers are asked exactly as on any other call; the host's own mic gets in
there as a near voice (FOCUS_NEAR_RMS), which is why its level is a thing to check at rehearsal:
`focus_open reason="near voice"` rows carry his `rms`, `focus_deny reason="no face in front"` rows
the level of what was refused.
"""
from __future__ import annotations

import pytest

from app import ceremony, focus
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on

T = 1000.0
#: The robot's own bar for "a voice at the robot" (data/robot-server/robot.env FOCUS_NEAR_RMS).
NEAR = 0.06
OPEN = {"cue": "adhoc"}


@pytest.fixture
def silent_face(monkeypatch):
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda *a, **k: None)
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_hold_s", 2.0)
    monkeypatch.setattr(settings, "focus_face_min_px", 60)
    focus.reset()
    focus.begin_session(False)
    focus.observe_mouth(1, T - 5, 0.9)                    # this mouth has moved once: "proven"
    focus.observe_faces([focus.FaceObs(track=1, t=T, bearing_deg=0.0, width_px=140, yaw_deg=0.0, name=None)], T)
    focus.observe_mouth(1, T, 0.0)
    yield
    focus.reset()


def test_silent_face_refuses_without_a_window(silent_face, monkeypatch):
    monkeypatch.setattr(ceremony, "live_state", lambda: None)
    assert focus.may_open(T, rms=0.08) == (False, focus.SILENT_FACES)


def test_open_live_window_hears_the_hosts_mic(silent_face, monkeypatch):
    monkeypatch.setattr(ceremony, "live_state", lambda: {"cue": "adhoc"})
    assert focus.may_open(T, rms=0.08) == (True, focus.CEREMONY_LIVE)


def test_a_failing_probe_is_the_ordinary_rules(silent_face, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(ceremony, "live_state", boom)
    assert focus.may_open(T, rms=0.08) == (False, focus.SILENT_FACES)


# ---- the window never skips rule 2 ------------------------------------------------------------

@pytest.fixture
def robot(monkeypatch):
    """The robot's listening settings (robot.env: a face required on a summoned call, near voice
    0.06, reply window 10 s, converse hold 20 s), a robot standing still, a camera with nobody in
    it yet. Yields the turn-log rows focus wrote."""
    from app import turnlog

    rows: list[tuple[str, dict]] = []
    monkeypatch.setattr(turnlog, "record", lambda event, **fields: rows.append((event, fields)))
    monkeypatch.setattr(settings, "focus_enabled", True)
    monkeypatch.setattr(settings, "focus_require_face", True)
    monkeypatch.setattr(settings, "focus_hold_s", 2.0)
    monkeypatch.setattr(settings, "focus_face_min_px", 60)
    monkeypatch.setattr(settings, "focus_near_rms", NEAR)
    monkeypatch.setattr(settings, "focus_reply_window_s", 10.0)
    monkeypatch.setattr(settings, "focus_converse_hold_s", 20.0)
    monkeypatch.setattr(settings, "focus_wake_window_s", 10.0)
    monkeypatch.setattr(settings, "focus_engage_mode", "full")
    # a walking robot hears every voice before any of this (ROBOT_MOVING): here she stands
    monkeypatch.setattr(focus, "_robot_moving", lambda: False)
    focus._forget_everything()
    yield rows
    focus._forget_everything()


def _window(monkeypatch, state):
    """The ceremony's probe answers `state` (None = no window / no ceremony); returns the list of
    times it was asked."""
    asked: list[float] = []

    def live_state():
        asked.append(1.0)
        return state

    monkeypatch.setattr(ceremony, "live_state", live_state)
    return asked


def _nobody(t: float = T) -> None:
    """The camera is alive and sees nobody."""
    focus.observe_faces([], t)


def _face(t: float = T, *, mouth: float | None = 0.0, track: int = 1) -> None:
    """A face in the lens. `mouth`: its activity now, after the active speaker has seen it move
    once (so it can be judged silent); None = no mouth data for it at all."""
    if mouth is not None:
        focus.observe_mouth(track, t - 5, 0.9)
    focus.observe_faces([focus.FaceObs(track=track, t=t, bearing_deg=0.0, width_px=140,
                                       yaw_deg=0.0, name=None)], t)
    if mouth is not None:
        focus.observe_mouth(track, t, mouth)


@pytest.mark.parametrize("summoned", [True, False])
def test_the_hosts_loud_mic_passes_a_silent_face_in_the_window(robot, monkeypatch, summoned):
    """(1) The bug of the rehearsal stays fixed, on the summoned call the robot really makes too."""
    _window(monkeypatch, OPEN)
    focus.begin_session(summoned)
    _face(mouth=0.0)
    assert focus.may_open(T, rms=0.08) == (True, focus.CEREMONY_LIVE)
    assert [f["reason"] for e, f in robot if e == "focus_open"] == [focus.CEREMONY_LIVE]


def test_a_quiet_voice_with_no_face_is_refused_in_the_window(robot, monkeypatch):
    """(2) Summoned call, nobody in the lens, room-chatter level: rule 2 refuses it, window or not."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody()
    assert focus.may_open(T, rms=0.03) == (False, focus.NO_FACE)
    event, row = robot[-1]
    assert (event, row["reason"], row["rms"], row["origin"]) == ("focus_deny", focus.NO_FACE, 0.03, "camera")


def test_a_quiet_voice_with_no_camera_is_refused_in_the_window(robot, monkeypatch):
    _window(monkeypatch, OPEN)
    focus.begin_session(True)                             # no frame ever came: "no camera"
    assert focus.may_open(T, rms=0.03) == (False, focus.NO_CAMERA)


def test_rule_2_refuses_before_the_window_is_even_asked(robot, monkeypatch):
    """The order itself: what rule 2 refuses never reaches the window's probe."""
    asked = _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody()
    assert focus.may_open(T, rms=0.03) == (False, focus.NO_FACE)
    assert asked == []
    _face(mouth=0.0)
    assert focus.may_open(T, rms=0.03) == (True, focus.CEREMONY_LIVE)
    assert len(asked) == 1


def test_a_loud_voice_with_no_face_is_heard_in_the_window(robot, monkeypatch):
    """(3) The host's wireless mic: no face of his in the lens, loud -- rule 2's near voice."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody()
    assert focus.may_open(T, rms=0.08) == (True, focus.NEAR_VOICE)
    event, row = robot[-1]
    assert (event, row["reason"], row["rms"], row.get("near")) == ("focus_open", focus.NEAR_VOICE, 0.08, True)


@pytest.mark.parametrize("rms, heard", [(0.0599, False), (0.06, True), (0.0601, True)])
def test_the_bar_for_the_hosts_mic_is_the_near_level(robot, monkeypatch, rms, heard):
    """What the rehearsal must check: outside the reply window his sentence needs one frame at
    FOCUS_NEAR_RMS (0.06 on the robot) -- a mic set lower is refused like the room."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody()
    assert focus.may_open(T, rms=rms) == ((True, focus.NEAR_VOICE) if heard else (False, focus.NO_FACE))


def test_the_reply_window_still_answers_a_quiet_host_in_the_window(robot, monkeypatch):
    """Rule 2's waivers are all still there: within 10 s of her voice a quiet answer is hers to hear,
    and after it the room level is refused again."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody(T)
    focus.her_turn_ended(T - 1.0)
    assert focus.may_open(T, rms=0.03) == (True, focus.REPLY)
    _nobody(T + 12.0)
    assert focus.may_open(T + 12.0, rms=0.03) == (False, focus.NO_FACE)


def test_room_chatter_cannot_chain_on_her_answers_in_the_window(robot, monkeypatch):
    """MAX_REPLY_CHAIN counts inside the window too: three of her turns that answered only voices
    the reply window let in, and it stops renewing (asked above rule 2, no reason was ever
    "reply to her" and the chain never counted)."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    t = T
    focus.her_turn_ended(t)
    for _ in range(focus.MAX_REPLY_CHAIN):
        t += 5.0
        _nobody(t)
        assert focus.may_open(t, rms=0.03) == (True, focus.REPLY)
        focus.her_turn_ended(t + 1.0)
    t += 5.0
    _nobody(t)
    assert focus.may_open(t, rms=0.03) == (False, focus.NO_FACE)


def test_her_name_still_opens_the_window_to_a_quiet_voice(robot, monkeypatch):
    """"Emma, ..." from the host at any level: the name waives rule 2 (FOCUS_WAKE), and the window
    then keeps rules 3-5 away from his sentence."""
    _window(monkeypatch, OPEN)
    focus.begin_session(True)
    _nobody()
    focus.heard_name(T - 1.0)
    assert focus.may_open(T, rms=0.03) == (True, focus.CEREMONY_LIVE)


@pytest.mark.parametrize("state, with_the_face, after_it_left", [
    (None, (True, focus.OK), (True, focus.CONVERSING)),
    (OPEN, (True, focus.CEREMONY_LIVE), (False, focus.NO_FACE)),
])
def test_an_open_under_the_window_starts_no_converse_hold(robot, monkeypatch, state, with_the_face,
                                                           after_it_left):
    """Rules 3-5 were not asked, so the open is no proof the voice was the face's: unlike an OK open
    it does not keep the room open for FOCUS_CONVERSE_HOLD_S once the face has left the lens."""
    _window(monkeypatch, state)
    focus.begin_session(True)
    _face(T, mouth=0.9)
    assert focus.may_open(T, rms=0.03) == with_the_face
    _nobody(T + 3.0)
    assert focus.may_open(T + 3.0, rms=0.03) == after_it_left


#: (summoned, who is in the lens, rms) -> the decision with no window, and with the window open.
#: The left column is the rules as they were before the ceremony had a window; the right one may
#: differ from it ONLY where rule 2 had nothing against the voice.
_NO_FACE, _NO_CAMERA = (False, focus.NO_FACE), (False, focus.NO_CAMERA)
_NEAR, _OK, _SILENT, _LIVE = (True, focus.NEAR_VOICE), (True, focus.OK), (False, focus.SILENT_FACES), (
    True, focus.CEREMONY_LIVE)
TABLE = [
    # summoned (the robot face's call)   lens       rms    closed       open
    (True,  "nobody",    0.03, _NO_FACE,   _NO_FACE),
    (True,  "nobody",    0.08, _NEAR,      _NEAR),
    (True,  "no_camera", 0.03, _NO_CAMERA, _NO_CAMERA),
    (True,  "no_camera", 0.08, _NEAR,      _NEAR),
    (True,  "silent",    0.03, _SILENT,    _LIVE),
    (True,  "silent",    0.08, _SILENT,    _LIVE),
    (True,  "talking",   0.03, _OK,        _LIVE),
    (True,  "unjudged",  0.03, _OK,        _LIVE),
    # a call a person started (the button): rule 2 does not judge it
    (False, "nobody",    0.03, _OK,        _LIVE),
    (False, "silent",    0.08, _SILENT,    _LIVE),
    (False, "talking",   0.03, _OK,        _LIVE),
]


def _scene(summoned: bool, lens: str) -> None:
    focus.begin_session(summoned)
    if lens == "nobody":
        _nobody()
    elif lens == "silent":
        _face(mouth=0.0)
    elif lens == "talking":
        _face(mouth=0.9)
    elif lens == "unjudged":
        _face(mouth=None)
    else:
        assert lens == "no_camera"


@pytest.mark.parametrize("summoned, lens, rms, closed, opened", TABLE)
def test_no_window_is_the_old_decision(robot, monkeypatch, summoned, lens, rms, closed, opened):
    """(4) Window closed (the sales room, the Event app, the ceremony between live steps): the
    decision of the rules as they were, reason and all."""
    _window(monkeypatch, None)
    _scene(summoned, lens)
    assert focus.may_open(T, rms=rms) == closed


@pytest.mark.parametrize("summoned, lens, rms, closed, opened", TABLE)
def test_an_open_window_changes_only_what_rule_2_let_through(robot, monkeypatch, summoned, lens, rms,
                                                              closed, opened):
    _window(monkeypatch, OPEN)
    _scene(summoned, lens)
    assert focus.may_open(T, rms=rms) == opened
    if closed[1] in (focus.NO_FACE, focus.NO_CAMERA, focus.NEAR_VOICE):
        assert opened == closed                           # rule 2's own answer: the window is not asked


def test_no_ceremony_on_the_robot_never_asks_for_more(robot):
    """The real module, nothing patched: no ceremony app (the sales room's condo, Emma Event) has no
    window to open, so a silent face refuses as it always did and room chatter stays out."""
    assert ceremony.live_open(cue_id="adhoc", brief_th=None) is False
    assert ceremony.live_state() is None
    focus.begin_session(True)
    _nobody()
    assert focus.may_open(T, rms=0.03) == (False, focus.NO_FACE)
    _face(mouth=0.0)
    assert focus.may_open(T, rms=0.08) == (False, focus.SILENT_FACES)


def test_the_real_window_opens_and_shuts_the_bypass(robot):
    """The real window (app/ceremony.py), opened and shut as staff do: only while it is open does a
    silent face stop refusing the loud voice, and at no time does it let the room in."""
    ceremony_on()
    try:
        focus.begin_session(True)
        _face(T, mouth=0.0)
        assert focus.may_open(T, rms=0.08) == (False, focus.SILENT_FACES)      # cue-only, no window
        assert ceremony.live_open(cue_id="adhoc", brief_th=None) is True
        assert focus.may_open(T, rms=0.08) == (True, focus.CEREMONY_LIVE)
        _nobody(T + 5.0)                                                       # the guest stepped away
        assert focus.may_open(T + 5.0, rms=0.03) == (False, focus.NO_FACE)
        assert focus.may_open(T + 5.0, rms=0.08) == (True, focus.NEAR_VOICE)
        ceremony.live_close("staff")
        _face(T + 10.0, mouth=0.0)
        assert focus.may_open(T + 10.0, rms=0.08) == (False, focus.SILENT_FACES)
    finally:
        ceremony_off()
