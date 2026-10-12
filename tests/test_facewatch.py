"""
The rules that stand between a camera frame and the robot saying a name.

Every test here is about *not* speaking. That is the point of the module:
the greeting is irreversible and is delivered to the person it might be
wrong about, so refusing has to be the well-tested path.
"""
from __future__ import annotations

import numpy as np
import pytest

from app import faces, facewatch


FRAME = np.zeros((480, 640, 3), np.uint8)


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


ALICE = _unit(1)
BOB = _unit(0, 1)


def _gallery() -> faces.Gallery:
    return faces.Gallery(["ต้า", "mix"], np.stack([ALICE, BOB]),
                         [{"group": "staff"}, {"group": "staff"}])


def _watcher(**kw) -> facewatch.Watcher:
    kw.setdefault("gallery", _gallery())
    kw.setdefault("threshold", 0.45)
    kw.setdefault("confirm_frames", 3)
    kw.setdefault("cooldown_s", 600)
    kw.setdefault("min_face_px", 110)
    return facewatch.Watcher(**kw)


def _sees(monkeypatch, vec, width=200):
    """Make every frame contain one face of `width` px with this vector.

    Patched at `locate`, which is what the watcher calls: the embedding is
    the expensive half and it is skipped for an empty frame or a face too
    far away. Handing the vector over already filled in means `describe`
    never has to run, which is also what happens live once a face has been
    described.
    """
    face = faces.Face((0, 0, width, width), 0.9, vec)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [face])


def _sees_nothing(monkeypatch):
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [])


def test_an_empty_frame_says_nothing(monkeypatch):
    _sees_nothing(monkeypatch)
    assert _watcher().see(FRAME) is None


def test_one_good_frame_is_not_enough(monkeypatch):
    """A single frame is a bad witness: blur, a turning head, a passer-by."""
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=3)
    assert w.see(FRAME, now=0) is None
    assert w.see(FRAME, now=1) is None
    assert w.see(FRAME, now=2).name == "ต้า"


def test_a_face_that_changes_its_mind_never_confirms(monkeypatch):
    """Two people alternating in front of the lens must not add up to one.

    The streak counts agreement, not sightings — otherwise a busy doorway
    reaches any confirm count eventually and greets whoever happens to be in
    the frame at the moment it does.
    """
    w = _watcher(confirm_frames=3)
    for i, vec in enumerate([ALICE, BOB, ALICE, BOB]):
        _sees(monkeypatch, vec)
        assert w.see(FRAME, now=i) is None


def test_somebody_across_the_room_is_not_a_visitor(monkeypatch):
    """Small face = far away = also where recognition is least reliable."""
    _sees(monkeypatch, ALICE, width=40)
    w = _watcher(min_face_px=110)
    for i in range(6):
        assert w.see(FRAME, now=i) is None


def test_an_unenrolled_face_is_a_stranger_not_a_name(monkeypatch):
    """Nearest neighbour always exists. Being nearest is not being known.

    And a stranger holds the frame twice as long as a colleague before
    anything is said: the gallery can vouch for a colleague, a stranger's
    only witness is persistence. A garbage frame scoring 0.062 got greeted
    as a visitor on 2026-08-31; it would not have survived the doubled bar.
    """
    _sees(monkeypatch, _unit(0.2, 0.2, 1))
    w = _watcher(confirm_frames=1)
    assert w.see(FRAME, now=0) is None          # one frame: not yet
    seen = w.see(FRAME, now=1)                  # confirm_frames * 2
    assert seen is not None
    assert seen.kind == "stranger" and seen.name is None


def test_standing_at_the_desk_is_one_arrival_not_six_hundred(monkeypatch):
    """The cooldown is what keeps a greeting from becoming a metronome.

    And it counts from the last *sighting*, not the last greeting. This test
    used to assert the opposite — that a person still standing there gets
    greeted again once the clock runs out — and the first working run showed
    what that means: "หวัดดีค่ะ โชกุน" twice in one conversation, the second
    barging into the first. Staying is not arriving.
    """
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=1, cooldown_s=600)
    assert w.see(FRAME, now=0).name == "ต้า"
    # Still in frame every few minutes: each sighting pushes the deadline
    # out, so a two-hour conversation is one greeting — the times all sit
    # past the original cooldown and inside the refreshed one.
    for t in (1, 30, 599, 1100, 1600, 2100):
        assert w.see(FRAME, now=t) is None
    # Only somebody who was *away* for the whole cooldown is an arrival.
    assert w.see(FRAME, now=2100 + 601).name == "ต้า"


def test_a_stranger_and_a_colleague_have_separate_cooldowns(monkeypatch):
    """Greeting a stranger must not silence the person behind them."""
    w = _watcher(confirm_frames=1, cooldown_s=600)
    _sees(monkeypatch, _unit(0.2, 0.2, 1))
    w.see(FRAME, now=0)
    assert w.see(FRAME, now=1).kind == "stranger"
    # Behind them, so in a different place — the same spot a second later
    # would be the stranger's own face resolving into a name (see
    # test_consent's same-arrival tests).
    behind = faces.Face((400, 0, 600, 200), 0.9, ALICE)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [behind])
    assert w.see(FRAME, now=2).name == "ต้า"


def test_a_different_stranger_is_not_the_same_stranger(monkeypatch):
    """"A stranger" is not one person.

    With a single shared cooldown, greeting anybody unknown silenced every
    other unknown visitor for five minutes — measured 2026-08-31: garbage
    frames were greeted at 09:18 and 09:31, and the real visitor who walked
    in minutes later got nothing at all. The greeted face is remembered and
    compared, so the same face stays quiet while a different one is a
    visitor.
    """
    w = _watcher(confirm_frames=1, cooldown_s=600)

    first = _unit(0.2, 0.2, 1)
    _sees(monkeypatch, first)
    w.see(FRAME, now=0)
    assert w.see(FRAME, now=1).kind == "stranger"
    # Same face again, well inside the cooldown: quiet.
    assert w.see(FRAME, now=10) is None
    assert w.see(FRAME, now=11) is None

    # A different unknown face, still inside the first one's cooldown.
    second = _unit(0.1, 0.1, 0.1, 1)
    _sees(monkeypatch, second)
    w.see(FRAME, now=20)
    assert w.see(FRAME, now=21).kind == "stranger"

    # And the first stranger, back after their own cooldown: a visitor again.
    _sees(monkeypatch, first)
    seen = w.see(FRAME, now=700) or w.see(FRAME, now=701)
    assert seen.kind == "stranger"


def test_a_stranger_who_stays_is_one_visit(monkeypatch):
    """Their remembered face is refreshed by single frames, same as a
    colleague's clock — staying is not arriving, whoever you are."""
    w = _watcher(confirm_frames=1, cooldown_s=60)
    _sees(monkeypatch, _unit(0.2, 0.2, 1))
    w.see(FRAME, now=0)
    assert w.see(FRAME, now=1).kind == "stranger"
    # Still there, one frame at a time, each one pushing the clock along.
    for t in (30, 59, 100, 150, 200):
        assert w.see(FRAME, now=t) is None
    # Away past the cooldown, then back: greeted again.
    assert w.see(FRAME, now=300) is None     # first frame back
    assert w.see(FRAME, now=301).kind == "stranger"


def test_the_group_travels_with_the_sighting(monkeypatch):
    """A colleague arriving and an agent bringing a client want different
    sentences, so the distinction has to survive as far as whatever writes
    one."""
    _sees(monkeypatch, ALICE)
    seen = _watcher(confirm_frames=1).see(FRAME, now=0)
    assert seen.group == "staff"


def test_forget_clears_the_cooldowns(monkeypatch):
    """"We already greeted them" must not survive into tomorrow morning."""
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=1, cooldown_s=600)
    assert w.see(FRAME, now=0) is not None
    assert w.see(FRAME, now=1) is None
    w.forget()
    assert w.see(FRAME, now=2) is not None


def test_no_gallery_means_no_watcher_not_a_crash(monkeypatch, tmp_path):
    """A half-configured machine must not half-work.

    The showroom without models or a gallery has to run exactly as it did
    before this module existed — the same promise `FACE_ENABLED=false`
    makes, kept one layer further down.
    """
    from app.config import settings

    monkeypatch.setattr(faces, "available", lambda: True)
    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "nothing.npz"))
    assert facewatch.Watcher.from_settings() is None


def test_a_gallery_from_another_model_is_refused_not_used(monkeypatch, tmp_path):
    """The `EMBED_PROVIDER` rule again, at the last place it can be caught.

    Vectors from a different model still produce cosines. They just rank
    strangers above the right person, and nothing downstream would look
    wrong until somebody was greeted by the wrong name.
    """
    from app.config import settings

    stale = tmp_path / "stale.npz"
    faces.Gallery(["ต้า"], np.stack([ALICE]), model_tag="other/model").save(stale)
    monkeypatch.setattr(faces, "available", lambda: True)
    monkeypatch.setattr(settings, "face_gallery", str(stale))

    assert facewatch.Watcher.from_settings() is None


def test_no_models_means_no_watcher(monkeypatch):
    monkeypatch.setattr(faces, "available", lambda: False)
    assert facewatch.Watcher.from_settings() is None


@pytest.mark.parametrize("score_vec,expect", [(ALICE, "known"), (_unit(0.3, 0.3, 1), "stranger")])
def test_the_threshold_is_the_only_thing_that_turns_a_number_into_a_name(
        monkeypatch, score_vec, expect):
    _sees(monkeypatch, score_vec)
    w = _watcher(confirm_frames=1)
    seen = w.see(FRAME, now=0) or w.see(FRAME, now=1)   # strangers take 2x
    assert seen.kind == expect


def test_a_flickering_score_does_not_make_the_robot_forget_you(monkeypatch):
    """Measured at the doorway: the right person dips to 0.40-0.43 between
    0.52s. Each dip broke the confirmation streak, the streak was the only
    thing refreshing the clock, and the robot re-greeted somebody who had
    been standing there the whole time. Presence takes one frame; only the
    greeting takes three.
    """
    w = _watcher(confirm_frames=3, cooldown_s=60)
    _sees(monkeypatch, ALICE)
    for t in (0, 1, 2):
        got = w.see(FRAME, now=t)
    assert got is not None and got.name == "ต้า"

    # Weak frames of the same face — score above SOFT_PRESENCE but under the
    # naming bar — arriving alone, never three in a row.
    weak = ALICE * 0.40 + BOB * np.sqrt(1 - 0.40 ** 2)
    for t in range(10, 130, 10):
        _sees(monkeypatch, ALICE if t % 20 else weak)
        w.see(FRAME, now=t)

    # 120s of flicker later (two cooldowns), a clean streak: still no
    # re-greeting, because they never left.
    _sees(monkeypatch, ALICE)
    for t in (130, 131, 132):
        assert w.see(FRAME, now=t) is None


def test_presence_extends_a_suppression_but_never_resurrects_one(monkeypatch):
    """The first frame of somebody back from lunch must not reset the very
    clock that proves they were away."""
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=1, cooldown_s=60)
    assert w.see(FRAME, now=0).name == "ต้า"
    # Away past the whole cooldown; their entry is stale.
    # The first frame back is both the presence frame and the greeting frame.
    assert w.see(FRAME, now=100).name == "ต้า"


# -- who is worth dialling for ------------------------------------------------

def test_a_colleague_in_cooldown_does_not_ring_the_browser(monkeypatch):
    """The first pre-ring fired on any face big enough — and the owner at
    the desk, greeted an hour ago, opened a fresh Gemini session every
    fifteen seconds for nobody. Only a candidate that could end in a
    greeting is worth dialling for."""
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=1, cooldown_s=600)
    assert w.see(FRAME, now=0).name == "ต้า"
    w.someone_at = None
    for t in (1, 2, 3):
        w.see(FRAME, now=t)
    assert w.someone_at is None


def test_a_new_arrival_rings_before_being_confirmed(monkeypatch):
    """Seen twice, not confirmed: that is when the dial starts."""
    _sees(monkeypatch, ALICE)
    w = _watcher(confirm_frames=3, cooldown_s=600)
    assert w.see(FRAME, now=0) is None
    assert w.someone_at is None            # one frame is a bad witness
    assert w.see(FRAME, now=1) is None
    assert w.someone_at == 1               # two frames: start dialling
    assert w.see(FRAME, now=2).name == "ต้า"


def test_a_remembered_stranger_does_not_ring(monkeypatch):
    _sees(monkeypatch, _unit(0.2, 0.2, 1))
    w = _watcher(confirm_frames=1, cooldown_s=600)
    w.see(FRAME, now=0)
    assert w.see(FRAME, now=1).kind == "stranger"
    w.someone_at = None
    for t in (2, 3, 4):
        w.see(FRAME, now=t)
    assert w.someone_at is None


# -- a named greeting is not followed by an anonymous one for the same face ----

def test_a_colleagues_blur_after_their_named_greeting_is_not_a_new_stranger(monkeypatch):
    """The mirror of the same-arrival rule: โชกุน was greeted by name
    (0.546), then his frames blurred at the lens (0.223 against the
    gallery), held a streak as "a stranger", and he was greeted a second
    time — anonymously, seconds after hearing his own name (2026-09-02)."""
    w = _watcher(confirm_frames=1, cooldown_s=600)
    _sees(monkeypatch, ALICE)
    assert w.see(FRAME, now=0).name == "ต้า"
    garbage = _unit(0, 0, 0, 1)          # matches nobody, its owner included
    _sees(monkeypatch, garbage)
    for i in range(30):                   # well past the 20s same-spot window
        assert w.see(FRAME, now=1.0 + i) is None, "greeted twice"


def test_a_real_stranger_behind_a_greeted_colleague_is_still_greeted(monkeypatch):
    """The other half: a different box is a different person."""
    w = _watcher(confirm_frames=1, cooldown_s=600)
    _sees(monkeypatch, ALICE)
    assert w.see(FRAME, now=0).name == "ต้า"
    other = faces.Face((400, 0, 600, 200), 0.9, _unit(0, 0, 0, 1))
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [other])
    w.see(FRAME, now=1)
    assert w.see(FRAME, now=2).kind == "stranger"


def test_every_refusal_leaves_a_verdict_the_debug_panel_can_show(monkeypatch):
    """Owner 2026-09-30: "อยากรู้ว่ามันจับคนได้ไหม" — from the doorway,
    "ไกลเกิน", "คะแนนต่ำ", "ติด cooldown" all look identical (the WAKE_DEBUG
    lesson wearing a face), so every exit of see() says which one happened.
    Diagnostic only: nothing may read `status` back into a decision."""
    w = _watcher(confirm_frames=3)
    assert "ยังไม่มีภาพ" in w.status["verdict"]

    _sees_nothing(monkeypatch)
    assert w.see(FRAME, now=0) is None
    assert w.status["verdict"] == "ไม่เห็นหน้า"

    _sees(monkeypatch, ALICE, width=40)
    assert w.see(FRAME, now=1) is None
    assert "ไกลเกิน" in w.status["verdict"] and "40px" in w.status["verdict"]

    _sees(monkeypatch, ALICE)
    assert w.see(FRAME, now=2) is None
    assert "ยืนยัน 1/3" in w.status["verdict"]
    assert w.status["name"] == "ต้า"

    assert w.see(FRAME, now=3) is None
    assert w.see(FRAME, now=4) is not None
    assert w.status["verdict"].startswith("ทัก ต้า")
    assert w.status["greeting"] is True

    # Still standing there: the refusal names the cooldown, not silence.
    assert w.see(FRAME, now=5) is None
    assert "ทักไปแล้ว" in w.status["verdict"]


# -- the arrival wait (FACE_TRACK_WAIT_S) ---------------------------------

#: A face region with texture (sharp) and one without (as blurred as it
#: gets). The face box is (0, 0, 200, 200), so only that corner matters.
SHARP = np.random.default_rng(7).integers(0, 255, (480, 640, 3), dtype=np.uint8)
BLURRED = np.full((480, 640, 3), 120, np.uint8)
UNKNOWN = _unit(0.2, 0.2, 1)              # ~0.19 against ต้า: a real stranger
LOOKALIKE = _unit(0.43, 0, 0.903)         # 0.43: just under the 0.45 bar
#: SCRFD's five points with the nose well off the eyes' midpoint (yaw 0.75).
TURNED = np.array([[60, 80], [140, 80], [160, 120], [70, 160], [130, 160]], np.float32)


def _sees_face(monkeypatch, vec, kps=None, width=200):
    face = faces.Face((0, 0, width, width), 0.9, vec, kps)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [face])


def test_a_blurred_arrival_is_not_a_stranger_until_the_wait_runs_out(monkeypatch):
    """Blur that drops a colleague's 0.73 to 0.46 (measured 2026-10-08) is
    a stranger to the per-frame rule. Inside the wait it counts for nothing;
    past it, it counts again — the delay to a real stranger is bounded."""
    _sees_face(monkeypatch, UNKNOWN)
    w = _watcher(confirm_frames=1, track_wait_s=3.0)
    assert w.see(BLURRED, now=0.0) is None
    assert w.see(BLURRED, now=1.0) is None
    assert "ภาพเบลอ" in w.status["verdict"] and w.status["held"] is True
    assert w.see(BLURRED, now=2.0) is None
    assert w.see(BLURRED, now=3.0) is None          # wait over: frame 1 of 2
    seen = w.see(BLURRED, now=3.5)
    assert seen is not None and seen.kind == "stranger"
    assert seen.track_s == 3.5 and seen.held == 3


def test_the_wait_off_is_the_old_per_frame_rule(monkeypatch):
    _sees_face(monkeypatch, UNKNOWN)
    w = _watcher(confirm_frames=1, track_wait_s=0)
    assert w.see(BLURRED, now=0.0) is None
    seen = w.see(BLURRED, now=0.2)
    assert seen is not None and seen.kind == "stranger"
    assert seen.track_s is None and seen.held == 0


def test_a_clear_stranger_is_greeted_as_fast_as_before(monkeypatch):
    """The owner asked for passers-by to be greeted faster (2026-09-30).
    A sharp, frontal, low-scoring face is evidence, wait or no wait."""
    _sees_face(monkeypatch, UNKNOWN)
    w = _watcher(confirm_frames=1, track_wait_s=3.0)
    assert w.see(SHARP, now=0.0) is None
    seen = w.see(SHARP, now=0.2)
    assert seen is not None and seen.kind == "stranger" and seen.held == 0


def test_a_score_just_under_the_bar_waits_for_a_clearer_frame(monkeypatch):
    """0.40-0.49 "strangers" in the logs are where a colleague at a bad
    angle lands. Given the wait, the clear frame that follows names them."""
    w = _watcher(confirm_frames=2, track_wait_s=3.0)
    _sees_face(monkeypatch, LOOKALIKE)
    for t in (0.0, 0.2, 0.4, 0.6):
        assert w.see(SHARP, now=t) is None
    assert "คล้ายคนที่รู้จัก" in w.status["verdict"]
    assert "ต้า" not in w.status["verdict"]       # the chest screen faces the room
    _sees_face(monkeypatch, ALICE)
    assert w.see(SHARP, now=0.8) is None
    seen = w.see(SHARP, now=1.0)
    assert seen is not None and seen.name == "ต้า" and seen.held == 4


def test_a_turned_head_is_held_back(monkeypatch):
    _sees_face(monkeypatch, UNKNOWN, kps=TURNED)
    w = _watcher(confirm_frames=1, track_wait_s=3.0)
    assert w.see(SHARP, now=0.0) is None
    assert w.see(SHARP, now=0.2) is None
    assert "หันข้าง" in w.status["verdict"]


def test_a_held_frame_does_not_break_a_colleagues_streak(monkeypatch):
    """Named, blurred, named: the blur used to reset the streak to a
    stranger's, and the colleague started counting again from one."""
    w = _watcher(confirm_frames=2, track_wait_s=3.0)
    _sees_face(monkeypatch, ALICE)
    assert w.see(SHARP, now=0.0) is None
    _sees_face(monkeypatch, UNKNOWN)
    assert w.see(BLURRED, now=0.2) is None
    _sees_face(monkeypatch, ALICE)
    seen = w.see(SHARP, now=0.4)
    assert seen is not None and seen.name == "ต้า"


def test_a_new_arrival_gets_a_new_wait(monkeypatch):
    """The wait belongs to somebody who has just walked up: an empty frame
    in between ends the arrival, and the next face starts its own clock."""
    _sees_face(monkeypatch, UNKNOWN)
    w = _watcher(confirm_frames=1, track_wait_s=3.0)
    for t in (0.0, 1.0, 2.0, 3.0):
        w.see(BLURRED, now=t)
    _sees_nothing(monkeypatch)
    w.see(BLURRED, now=3.2)
    _sees_face(monkeypatch, UNKNOWN)
    assert w.see(BLURRED, now=3.4) is None
    assert w.status.get("held") is True
