"""Active speaker (app/active_speaker.py): which face in front of the robot talks.

Most of this runs on synthetic 2d106-layout landmark sequences -- a 3-D face
template in units of its own inter-ocular distance, rotated (nod / turn),
moved (walk), smiled and talked, projected, and jittered the way the real
model jitters (sd of gap/IOD ~0.009, measured on still photos under box
jitter + JPEG). Two tests go through the real 2d106det on insightface's own
sample photo when this machine has the model; their skip says what is then
not checked. And the robot page's camera sender is RUN in node (virtual
clock) to prove its frame rate only changes when the server asks.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app import active_speaker as asd
from app import robot_camera
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


@pytest.fixture(autouse=True)
def _asd_pins(monkeypatch):
    """Whatever this machine's .env says: off, local camera, and no worker
    left running after a test (it is a real thread)."""
    monkeypatch.setattr(settings, "asd_enabled", False)
    monkeypatch.setattr(settings, "asd_fps", 15.0)
    monkeypatch.setattr(settings, "face_camera_source", "local")
    monkeypatch.setattr(settings, "focus_face_min_px", 90)
    asd.stop()
    yield
    asd.stop()


# ---- synthetic faces --------------------------------------------------------


def face_points(open_=0.04, smile=0.0):
    """106 points (x, y, z) in IOD units, eye midpoint at the origin, y down,
    in 2d106det's layout (indices as in app/active_speaker.py)."""
    p = np.zeros((106, 3))
    jaw = 0.8 * (open_ - 0.04)
    for k, th in enumerate(np.linspace(-math.pi / 2, math.pi / 2, 33)):      # contour
        x, y = 0.95 * math.sin(th), -0.1 + 1.85 * math.cos(th)
        p[k] = (x, y + jaw * max(0.0, math.cos(th)) ** 2, -0.6 * abs(math.sin(th)))
    for base, cx in ((33, -0.5), (87, 0.5)):                                 # eyes
        for k in range(10):
            a = 2 * math.pi * k / 10
            p[base + k] = (cx + 0.18 * math.cos(a), 0.07 * math.sin(a), -0.05)
    for base, sgn in ((43, -1), (97, 1)):                                    # brows
        for k in range(9):
            p[base + k] = (sgn * (0.2 + 0.6 * k / 8), -0.28 - 0.05 * math.sin(math.pi * k / 8), 0.0)
    for k in range(7):                                                       # nose bridge
        p[72 + k] = (0.0, 0.12 + 0.07 * k, 0.2 + 0.06 * k)
    for k, x in enumerate((-0.18, -0.12, -0.06, 0.0, 0.06, 0.12, 0.18, 0.0)):
        p[79 + k] = (x, 0.66 if k < 7 else 0.6, 0.45 if k < 7 else 0.62)
    my, teeth = 1.03, 0.09 * smile
    up, down = 0.2 * (open_ + teeth), 0.8 * (open_ + teeth)
    cw, cy = 0.42 + 0.08 * smile, my - 0.06 * smile
    p[52], p[61] = (-cw, cy, 0.25), (cw, cy, 0.25)                            # corners
    for i, x in zip((64, 63, 71, 67, 68, 69), (-0.33, -0.2, -0.07, 0.07, 0.2, 0.33)):
        p[i] = (x * cw / 0.42, my - 0.08 - up - 0.03 * smile * abs(x), 0.35)
    for i, x in zip((55, 56, 53, 59, 58), (-0.28, -0.14, 0.0, 0.14, 0.28)):
        p[i] = (x * cw / 0.42, my + 0.1 + down, 0.33)
    for i, x in zip((65, 66, 62, 70), (-0.3, -0.15, 0.0, 0.15)):              # inner upper
        p[i] = (x * cw / 0.42, my - up, 0.32)
    for i, x in zip((54, 60, 57), (-0.15, 0.0, 0.15)):                        # inner lower
        p[i] = (x * cw / 0.42, my + down, 0.32)
    return p


def project(p3, scale=60.0, tx=320.0, ty=240.0, yaw=0.0, pitch=0.0, roll=0.0):
    c = np.array([0.0, 0.6, -0.5])                                           # head centre
    cyw, syw = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    cr, sr = math.cos(math.radians(roll)), math.sin(math.radians(roll))
    ry = np.array([[cyw, 0, syw], [0, 1, 0], [-syw, 0, cyw]])
    rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    rz = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]])
    q = (p3 - c) @ (rz @ ry @ rx).T + c
    return np.stack([tx + scale * q[:, 0], ty + scale * q[:, 1]], axis=1)


def syllables(t, seed, lo=0.06, hi=0.25, start=0.0):
    """Mouth opening for speech: half-sine bumps, ~4-5 a second."""
    rng = np.random.default_rng(seed)
    out, at = np.full(np.shape(t), 0.04), start
    while at < t.max() + 1:
        d = rng.uniform(0.12, 0.28)
        a = rng.uniform(lo, hi)
        m = (t >= at) & (t < at + d)
        out[m] = np.maximum(out[m], 0.04 + a * np.sin(np.pi * (t[m] - at) / d))
        at += d + rng.uniform(0.02, 0.2)
    return out


def sequence(kind, fps=15.0, dur=4.0, scale=60.0, jitter=0.008, seed=1, onset=None):
    """(times, landmark frames) for one person doing `kind`."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, dur, 1.0 / fps)
    open_, smile = np.full_like(t, 0.04), np.zeros_like(t)
    pitch = 2.0 * np.sin(2 * np.pi * 0.6 * t + rng.uniform(0, 6))          # natural sway
    yaw = 2.0 * np.sin(2 * np.pi * 0.4 * t + rng.uniform(0, 6))
    tx, blur = np.full_like(t, 320.0), np.ones_like(t)
    if kind in ("talk", "talk_nod"):
        open_ = syllables(t, seed, start=0.0 if onset is None else onset)
    if kind == "smile":
        smile = np.clip((t - 1.0) / 0.3, 0, 1)                             # forms at 1 s, held
    if kind == "gasp":
        open_ = 0.04 + 0.26 * np.clip((t - 1.0) / 0.15, 0, 1)              # drops open, stays
    if kind in ("nod", "talk_nod"):
        pitch = 15.0 * np.sin(2 * np.pi * 1.8 * t)
        blur = 1.0 + 1.5 * np.abs(np.cos(2 * np.pi * 1.8 * t))              # motion blur
    if kind == "turn":
        yaw = 30.0 * np.sin(2 * np.pi * 0.5 * t)
        blur = 1.0 + np.abs(np.cos(2 * np.pi * 0.5 * t))
    if kind == "walk":
        tx, blur = 100 + 4.0 * scale * t, np.full_like(t, 2.0)              # 4 IOD/s across
    frames = []
    for i in range(len(t)):
        pts = project(face_points(open_[i], smile[i]), scale, tx[i], 240.0, yaw[i], pitch[i])
        frames.append(pts + rng.normal(0, jitter * scale * blur[i], pts.shape))
    return t, frames


def activities(kind, **kw):
    t, frames = sequence(kind, **kw)
    sig, out = asd.MouthSignal(), []
    for ti, lm in zip(t, frames):
        g = asd.mouth_geometry(lm)
        a = None if g is None else sig.add(float(ti), g)
        out.append(np.nan if a is None else a)
    return t, np.array(out)


def _settled(kind, seeds=range(4), after=2.0, **kw):
    """Mean and max activity after `after` s, over several people (seeds)."""
    means, peaks = [], []
    for s in seeds:
        t, a = activities(kind, seed=s, **kw)
        sel = a[t > after]
        means.append(np.nanmean(sel) if np.any(~np.isnan(sel)) else np.nan)
        peaks.append(np.nanmax(sel) if np.any(~np.isnan(sel)) else np.nan)
    return float(np.nanmean(means)), float(np.nanmax(peaks))


# ---- the signal ---------------------------------------------------------------


def test_talking_outranks_closed_smiling_nodding_turning():
    """The ordering the whole feature rests on: speech is the open-close
    rhythm, not an open mouth, not a moving head. Silent faces stay well
    under the 0.3 a 'moves' rule would use; talking sits far above it."""
    talk, _ = _settled("talk")
    assert talk >= 0.5, talk
    for kind in ("closed", "smile", "nod", "turn"):
        mean, peak = _settled(kind)
        assert mean <= 0.05 and peak <= 0.3, (kind, mean, peak)
        assert talk > mean + 0.4, (kind, talk, mean)


def test_a_held_smile_is_silent_and_its_onset_stays_below_talking():
    """Smiling-but-silent: one step out, none back. The onset may register
    as a movement (it is one) but never above ONSET_CAP, and the held smile
    reads as a closed mouth even though it shows teeth (gap 0.13 vs 0.04)."""
    for seed in range(6):
        t, a = activities("smile", seed=seed)
        assert np.nanmax(a[t > 0.9]) <= asd.ONSET_CAP + 1e-9
        assert np.nanmean(a[t > 2.0]) <= 0.02, seed


def test_a_mouth_that_opens_once_and_stays_open_is_not_talking():
    """A gasp, a yawn held, listening open-mouthed: one big step (0.26 IOD)
    and no way back. Once the step has left the short (movement) window it
    must count for nothing -- the long window still holds it, and only
    the "came back" part of the movement may score there."""
    for seed in range(4):
        t, a = activities("gasp", seed=seed)
        late = a[(t > 1.15 + asd.SHORT_WINDOW_S + 0.1) & (t < 1.0 + asd.LONG_WINDOW_S)]
        assert np.nanmax(late) <= 0.05, (seed, late)


def test_nodding_with_a_closed_mouth_is_not_talking_even_with_noisy_lips():
    """A nod rotates the face in depth; the landmark model wobbles on every
    point including the lips. The eyes+nose shape change is the measure of
    that wobble and is subtracted from the lip step (WOBBLE_K)."""
    mean, peak = _settled("nod", jitter=0.012)
    assert mean <= 0.05 and peak <= 0.3, (mean, peak)


def test_talking_while_nodding_still_reads_as_talking():
    mean, _ = _settled("talk_nod")
    assert mean >= 0.25, mean


def test_the_score_is_a_property_of_the_face_not_of_its_distance():
    """gap / IOD: the same person at 40 px/IOD (doorway) and 120 px/IOD
    (at the desk) gives the same activity, sample for sample."""
    for kind in ("talk", "closed"):
        _, near = activities(kind, scale=120.0, seed=3)
        _, far = activities(kind, scale=40.0, seed=3)
        ok = ~np.isnan(near) & ~np.isnan(far)
        assert ok.sum() > 30
        assert np.max(np.abs(near[ok] - far[ok])) < 0.02, kind


def test_the_first_syllable_registers_fast():
    """The VAD opens a segment ~0.2 s into speech, and focus asks about the
    last ~0.6 s at that moment. A score that waited for open-close cycles
    would call the speaker silent exactly when it is asked."""
    lat = []
    for seed in range(8):
        t, a = activities("talk", onset=1.5, seed=seed)
        assert np.nanmax(a[t < 1.5]) <= 0.05, "silence before the onset"
        hit = t[(t >= 1.5) & (np.nan_to_num(a) >= 0.3)]
        lat.append(hit[0] - 1.5 if len(hit) else 9.0)
    assert np.median(lat) <= 0.2 and max(lat) <= 0.35, lat


def test_still_working_at_a_degraded_frame_rate():
    """CpuGovernor may drop to MIN_FPS; the score must still separate."""
    talk, _ = _settled("talk", fps=7.5)
    closed, peak = _settled("closed", fps=7.5)
    assert talk >= 0.4 and closed <= 0.05 and peak <= 0.3, (talk, closed, peak)


def test_a_head_moving_too_fast_reports_unknown_never_silent():
    """Walking across the lens at 4 IOD/s: no lip step can be trusted. The
    face must report nothing (focus then skips the mouth rule), not 0 --
    a 0 would let focus deny the person who is actually speaking."""
    t, a = activities("walk")
    assert np.all(np.isnan(a)), a[~np.isnan(a)][:5]


def test_landmarks_no_face_could_have_are_rejected():
    rng = np.random.default_rng(0)
    for k in range(200):                                                    # crops of a wall
        assert asd.mouth_geometry(rng.uniform(0, 300, (106, 2)), box=(70, 70, 230, 230)) is None, k
    good = project(face_points(), 60, 320, 240)
    mirrored = good * (1, -1)                                               # mouth above the eyes
    assert asd.mouth_geometry(mirrored) is None
    melted = good.copy()
    melted[list(asd.EYE_A)] += rng.normal(0, 40, (10, 2))                   # an eye smeared out
    assert asd.mouth_geometry(melted) is None
    assert asd.mouth_geometry(good) is not None
    assert asd.mouth_geometry(good[:100]) is None
    bad = good.copy()
    bad[5, 0] = np.nan
    assert asd.mouth_geometry(bad) is None
    # And a face far too small for the crop it came from.
    assert asd.mouth_geometry(good, box=(0, 0, 2000, 2000)) is None


def test_shape_change_ignores_translation_scale_and_roll():
    z = np.array([complex(x, y) for x, y in project(face_points(), 1, 0, 0)[list(asd.RIGID)]])
    z = z - z.mean()
    moved = z * 1.7 * np.exp(1j * 0.4)
    assert asd.shape_change(moved - moved.mean(), z) < 1e-9
    nod = np.array([complex(x, y) for x, y in project(face_points(), 1, 0, 0, pitch=12)[list(asd.RIGID)]])
    assert asd.shape_change(nod - nod.mean(), z) > 0.01


# ---- tracking between detections -----------------------------------------------


class MovingFace:
    """A fake landmarker: one face at a known spot that moves each frame. It
    answers with that face's landmarks only when the crop it is given is
    centred on the face (as a real model needs) -- otherwise with points no
    face has. So a track only survives if its box FOLLOWS the face."""

    def __init__(self, scale=60.0):
        self.scale = scale
        self.pos = np.array([320.0, 240.0])
        self.open = 0.04
        self.calls = 0

    def truth(self):
        return project(face_points(self.open), self.scale, *self.pos)

    def __call__(self, frame, boxes):
        self.calls += 1
        out = []
        lm = self.truth()
        cen = (lm.min(0) + lm.max(0)) / 2
        for x1, y1, x2, y2 in boxes:
            side = max(x2 - x1, y2 - y1)
            if math.hypot((x1 + x2) / 2 - cen[0], (y1 + y2) / 2 - cen[1] + 0.164 * side / 1.3) < 0.15 * side:
                out.append(lm)
            else:
                out.append(np.random.default_rng(self.calls).uniform(0, 640, (106, 2)))
        return out


def _det_box(lm):
    return asd.box_from_landmarks(lm)


def test_tracks_follow_the_face_between_detections():
    """One detection, then the face walks 90 px across ~0.9 s at 15 fps with
    no further detection. The landmark-derived box must keep up: every frame
    scores, and the track's box ends on the face."""
    fake = MovingFace()
    got = []
    eng = asd.Engine(landmarker=fake, sink=lambda tr, t, a: got.append((tr, t)), target_fps=15, min_px=0)
    eng.on_detections(None, [(7, _det_box(fake.truth()), None, 0.0)], 0.0)
    for k in range(1, 14):
        fake.pos += (7.0, 0.0)                     # 7 px/frame = 1.75 IOD/s
        fake.open = 0.04 + 0.1 * (k % 3 == 1)
        eng.process_frame(np.zeros((480, 640, 3), np.uint8), k / 15)
    assert not eng._tracks[7].frozen
    assert len(got) >= 9 and got[-1][0] == 7, got
    box = eng._tracks[7].box
    assert asd.iou(box, _det_box(fake.truth())) > 0.8


def test_without_detections_a_track_expires():
    fake = MovingFace()
    eng = asd.Engine(landmarker=fake, sink=lambda *a: None, target_fps=15, min_px=0)
    eng.on_detections(None, [(1, _det_box(fake.truth()), None, 0.0)], 0.0)
    eng.process_frame(np.zeros((480, 640, 3), np.uint8), 0.5)
    assert 1 in eng._tracks
    eng.process_frame(np.zeros((480, 640, 3), np.uint8), 0.5 + asd.TRACK_HOLD_S + 0.6)
    assert 1 not in eng._tracks, "a face nobody has detected for TRACK_HOLD_S is gone"


def test_a_lost_track_is_reanchored_by_the_next_detection():
    fake = MovingFace()
    eng = asd.Engine(landmarker=fake, sink=lambda *a: None, target_fps=15, min_px=0)
    eng.on_detections(None, [(1, _det_box(fake.truth()), None, 0.0)], 0.0)
    fake.pos += (200.0, 0.0)                        # teleport: the crop now misses
    for k in range(asd.MAX_INVALID):
        eng.process_frame(np.zeros((480, 640, 3), np.uint8), (k + 1) / 15)
    assert eng._tracks[1].frozen, "three implausible frames in a row freeze the track"
    eng.on_detections(None, [(1, _det_box(fake.truth()), None, 0.3)], 0.3)
    assert not eng._tracks[1].frozen
    assert eng.process_frame(np.zeros((480, 640, 3), np.uint8), 0.35) is not None
    assert asd.iou(eng._tracks[1].box, _det_box(fake.truth())) > 0.8


def test_two_tracks_on_one_face_do_not_both_score():
    fake = MovingFace()
    eng = asd.Engine(landmarker=fake, sink=lambda *a: None, target_fps=15, min_px=0)
    box = _det_box(fake.truth())
    eng.on_detections(None, [(1, box, None, 0.0)], 0.0)
    eng.on_detections(None, [(1, box, None, 0.1), (2, box, None, 0.2)], 0.2)
    eng.process_frame(np.zeros((480, 640, 3), np.uint8), 0.25)
    frozen = {tid for tid, tr in eng._tracks.items() if tr.frozen}
    assert frozen == {1}, "the track with the older detection yields"


def test_small_and_extra_faces_cost_nothing():
    """Faces focus cannot count as engaged (< 0.8 x FOCUS_FACE_MIN_PX) are not
    cropped, and at most MAX_TRACKS faces are, largest first."""
    seen = []

    def lmk(frame, boxes):
        seen.append([round(b[2] - b[0]) for b in boxes])
        return [np.zeros((106, 2)) for _ in boxes]

    eng = asd.Engine(landmarker=lmk, sink=lambda *a: None, target_fps=15)   # min 72 px
    dets = [(i, (10.0 * i, 0.0, 10.0 * i + w, w), None, 0.0)
            for i, w in enumerate((50, 80, 100, 120, 140))]
    eng.on_detections(None, dets, 0.0)
    eng.process_frame(np.zeros((480, 640, 3), np.uint8), 0.1)
    assert seen == [[140, 120, 100]]


def test_entries_in_other_shapes_are_accepted_and_garbage_skipped():
    class Obs:
        track, bbox, t = 4, (0, 0, 100, 100), 1.0

    eng = asd.Engine(landmarker=None, sink=lambda *a: None, target_fps=15, min_px=0)
    eng.on_detections(None, [Obs(), ("x",), (5, (10, 10, 5, 5), None, 1.0), None,
                             [6, [0, 0, 50, 60]]], 1.0)
    assert set(eng._tracks) == {4, 6}


# ---- CPU budget ------------------------------------------------------------


def test_governor_drops_the_rate_when_over_budget_and_climbs_back():
    g = asd.CpuGovernor(target_fps=15, budget=1.0, min_fps=5)
    assert g.update(0.1, now=0.0) is not None       # 100 ms x 15 fps = 1.5 cores
    assert g.fps <= 9.0 and g.load <= 1.0 + 1e-9, (g.fps, g.load)
    for k in range(40):                              # stays slow: never below the floor
        g.update(0.5, now=2.5 + k * 2.5)
    assert g.fps == 5.0
    cheap = [g.update(0.01, now=200.0 + k * 0.5) for k in range(200)]
    assert g.fps == 15.0, g.fps
    assert sum(c is not None for c in cheap) >= 2, "it climbs back in steps"


def test_governor_holds_still_inside_the_budget():
    g = asd.CpuGovernor(target_fps=15, budget=1.0)
    assert all(g.update(0.04, now=k * 0.07) is None for k in range(300))   # 0.6 core
    assert g.fps == 15.0


class _FakeFeed:
    """robot_camera.feed's two read calls, with a new frame every read."""

    def __init__(self):
        self.seq = 0
        self.frame = np.zeros((480, 640, 3), np.uint8)

    def latest(self):
        self.seq += 1
        return self.seq, b"jpeg", time.monotonic()

    def decoded_for(self, seq, jpeg):
        return self.frame


def test_the_worker_degrades_and_says_so_when_frames_cost_too_much(caplog):
    """The full loop: frames from the feed, a landmarker that takes 50 ms
    per face, two faces -> 100 ms a frame -> 15 fps would be 1.5 cores. The
    rate drops and the log says by how much."""
    fake = MovingFace()

    def slow(frame, boxes):
        time.sleep(0.05 * len(boxes))
        return fake(frame, boxes)

    eng = asd.Engine(landmarker=slow, sink=lambda *a: None, feed=_FakeFeed(),
                     target_fps=15, min_px=0)
    box = _det_box(fake.truth())
    far = tuple(np.add(box, (300, 0, 300, 0)))
    caplog.set_level("INFO", logger="condo_voice.active_speaker")
    keep = threading.Event()

    def detections():
        while not keep.is_set():
            eng.on_detections(None, [(1, box, None, time.monotonic()),
                                     (2, far, None, time.monotonic())], time.monotonic())
            keep.wait(0.1)

    feeder = threading.Thread(target=detections, daemon=True)
    feeder.start()
    eng.start()
    try:
        deadline = time.monotonic() + 5
        while eng.governor.fps >= 15 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        keep.set()
        eng.stop()
        feeder.join(1)
    assert eng.governor.fps < 15
    assert any("running at" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


# ---- off means off; on means the robot page is asked to send faster -------------


def _threads():
    return [t for t in threading.enumerate() if t.name == "active-speaker"]


def test_off_nothing_runs_and_nothing_changes(monkeypatch):
    monkeypatch.setattr(settings, "face_camera_source", "robot")
    called = []
    monkeypatch.setattr(asd.Engine, "__init__", lambda *a, **k: called.append(1))
    asd.process_detections(np.zeros((4, 4, 3), np.uint8), [(1, (0, 0, 2, 2), None, 0.0)], 0.0)
    assert called == [] and asd.engine() is None and not _threads()
    assert asd.snapshot() == {"enabled": False, "running": False}
    assert robot_camera.sender_fps() is None
    assert robot_camera.RobotCameraFeed().control() == (0, {}), (
        "ASD off: /ws/camera has nothing new to send the page")
    feed = robot_camera.RobotCameraFeed()
    feed.set_control(zoom=2)
    assert feed.control()[1] == {"zoom": 2}


def test_on_asks_the_robot_page_for_asd_fps_only_with_the_robot_camera(monkeypatch):
    monkeypatch.setattr(settings, "asd_enabled", True)
    assert robot_camera.sender_fps() is None, "a local lens: the robot page's rate is moot"
    monkeypatch.setattr(settings, "face_camera_source", "robot")
    assert robot_camera.sender_fps() == 15.0
    feed = robot_camera.RobotCameraFeed()
    assert feed.control() == (0, {"fps": 15.0})
    feed.set_control(zoom=1.5)
    assert feed.control()[1] == {"zoom": 1.5, "fps": 15.0}
    monkeypatch.setattr(settings, "asd_fps", 90.0)
    assert robot_camera.sender_fps() == robot_camera.MAX_SENDER_FPS


def test_the_camera_socket_tells_the_page_the_rate_on_connect(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", "")
    monkeypatch.setattr(settings, "asd_enabled", True)
    monkeypatch.setattr(settings, "face_camera_source", "robot")
    monkeypatch.setattr(robot_camera, "feed", robot_camera.RobotCameraFeed())
    with TestClient(app).websocket_connect("/ws/camera") as ws:
        msg = ws.receive_json()
    assert msg == {"type": "camctl", "fps": 15.0}


def test_on_starts_one_worker_and_stop_ends_it(monkeypatch):
    monkeypatch.setattr(settings, "asd_enabled", True)
    monkeypatch.setattr(settings, "face_camera_source", "robot")
    monkeypatch.setattr(asd.Landmarker, "find", classmethod(lambda cls: MovingFace()))
    monkeypatch.setattr(robot_camera, "feed", robot_camera.RobotCameraFeed())
    asd.process_detections(None, [(1, (0, 0, 100, 100), None, 0.0)], 0.0)
    asd.process_detections(None, [(1, (0, 0, 100, 100), None, 0.1)], 0.1)
    eng = asd.engine()
    assert eng is not None and eng._feed is robot_camera.feed
    assert len(_threads()) == 1
    assert asd.snapshot()["running"] is True
    asd.stop()
    assert asd.engine() is None and not _threads()


def test_a_missing_model_turns_asd_off_quietly_once(monkeypatch, caplog):
    monkeypatch.setattr(settings, "asd_enabled", True)
    monkeypatch.setattr(asd, "LANDMARK_MODEL_PATHS", (Path("no/such/2d106det.onnx"),))
    caplog.set_level("WARNING", logger="condo_voice.active_speaker")
    asd.process_detections(None, [(1, (0, 0, 100, 100), None, 0.0)], 0.0)
    eng = asd.engine()
    eng._thread.join(5)
    assert eng.dead
    asd.process_detections(None, [(1, (0, 0, 100, 100), None, 0.1)], 0.1)   # no-op now
    assert sum("2d106det" in r.getMessage() for r in caplog.records) == 1


def test_a_model_with_another_layout_is_refused(monkeypatch, tmp_path):
    other = tmp_path / "2d106det.onnx"
    other.write_bytes(b"some other 106-point model")
    monkeypatch.setattr(asd, "LANDMARK_MODEL_PATHS", (other,))
    assert asd.Landmarker.find() is None


def test_focus_receives_the_scores_and_its_absence_is_survivable(monkeypatch):
    import sys
    import types

    got = []
    fake_focus = types.SimpleNamespace(observe_mouth=lambda tr, t, a: got.append((tr, t, a)))
    monkeypatch.setitem(sys.modules, "app.focus", fake_focus)
    import app as app_pkg
    monkeypatch.setattr(app_pkg, "focus", fake_focus, raising=False)
    asd._focus_sink(3, 1.5, 0.75)
    assert got == [(3, 1.5, 0.75)]
    monkeypatch.setitem(sys.modules, "app.focus", None)     # import fails
    monkeypatch.delattr(app_pkg, "focus", raising=False)
    asd._focus_sink(3, 1.6, 0.5)                             # no exception


# ---- the shared decode -----------------------------------------------------------


def test_one_decode_per_frame_for_greeter_and_asd(monkeypatch):
    import cv2

    calls = []
    real = robot_camera._decode
    monkeypatch.setattr(robot_camera, "_decode", lambda b: calls.append(1) or real(b))
    feed = robot_camera.RobotCameraFeed()
    cap = robot_camera.RobotCapture(feed, wait_s=0.2)
    ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), 90, np.uint8))
    feed.push(buf.tobytes())
    seq, jpeg, _ = feed.latest()
    mine = feed.decoded_for(seq, jpeg)
    ok, frame = cap.read()
    assert ok and frame is mine and len(calls) == 1
    feed.push(buf.tobytes())
    seq2, jpeg2, _ = feed.latest()
    assert feed.decoded_for(seq2, jpeg2) is not mine and len(calls) == 2


def test_the_arrival_rate_is_measured():
    feed = robot_camera.RobotCameraFeed()
    assert feed.arrival_fps() == 0.0
    for _ in range(6):
        feed.push(b"x")
        time.sleep(0.05)
    assert 8.0 <= feed.arrival_fps() <= 25.0


# ---- the real model, when this machine has it ---------------------------------------


def _sample_photo():
    try:
        import insightface
    except Exception:
        return None
    path = Path(insightface.__file__).parent / "data" / "images" / "t1.jpg"
    return path if path.is_file() else None


#: The closed-mouth face in insightface's sample t1.jpg (SCRFD box, measured).
_T1_FACE = (58.8, 258.7, 167.5, 396.2)


def _open_mouth(frame, lm, d):
    """Drop everything below this face's lip line by d px, with a dark mouth
    interior in the gap: a jaw opening, drawn into the photo."""
    import cv2

    h, w = frame.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ylip = float(np.mean([lm[i][1] for i in (66, 62, 70, 54, 60, 57)]))
    xc, mw, chin = float(lm[52:72, 0].mean()), abs(lm[61, 0] - lm[52, 0]), float(lm[0][1])
    wy = 1 / (1 + np.exp(-np.clip(yy - ylip, -40, 40))) * np.clip((chin + 0.5 * mw - yy) / (0.5 * mw), 0, 1)
    dy = (d * wy * np.exp(-((xx - xc) / (0.9 * mw)) ** 2)).astype(np.float32)
    out = cv2.remap(frame, xx, yy - dy, cv2.INTER_LINEAR)
    if d > 0.5:
        mask = np.zeros((h, w), np.float32)
        cv2.ellipse(mask, (int(xc), int(ylip + d / 2)), (int(0.38 * mw), max(1, int(d / 2))),
                    0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (5, 5), 0)[..., None]
        out = (out * (1 - mask) + np.array([35, 25, 70], np.float32) * mask).astype(np.uint8)
    return out


def _real_landmarker():
    photo = _sample_photo()
    model = next((p for p in asd.LANDMARK_MODEL_PATHS if p.is_file()), None)
    if photo is None or model is None:
        pytest.skip("NOT CHECKED: the real 2d106det's point layout and the end-to-end score "
                    "on a real face -- needs data/faces/models/*/2d106det.onnx (gitignored) "
                    "and insightface's sample photo t1.jpg")
    lmk = asd.Landmarker.find()
    assert lmk is not None, "the model on this machine is not the pinned one"
    import cv2

    return lmk, cv2.imread(str(photo))


def test_real_model_layout_inner_lips_part_when_the_jaw_drops():
    """Pins the indices: on the real model, INNER_PAIRS widen with the jaw
    (measured 0.048 -> 0.09 -> 0.136 -> 0.16 for 0/3/6/9 px)."""
    lmk, img = _real_landmarker()
    base = lmk(img, [_T1_FACE])[0]
    gaps = []
    for d in (0, 4, 8):
        g = asd.mouth_geometry(lmk(_open_mouth(img, base, d), [_T1_FACE])[0], _T1_FACE)
        assert g is not None
        gaps.append(g.gap)
    assert gaps[0] < 0.07 and gaps[1] > gaps[0] + 0.03 and gaps[2] > gaps[1] + 0.02, gaps


def test_real_model_end_to_end_still_face_is_silent_talking_face_is_not():
    """Engine + real 2d106det + JPEG round trips at 15 fps: the same photo
    still (camera noise, sub-pixel drift) vs with a jaw opening 4 times a
    second. Measured: still 0.00, talking mean ~0.7."""
    import cv2

    lmk, img = _real_landmarker()
    base = lmk(img, [_T1_FACE])[0]
    result = {}
    for mode in ("still", "talk"):
        out = []
        eng = asd.Engine(landmarker=lmk, sink=lambda tr, t, a: out.append(a), target_fps=15, min_px=0)
        eng.on_detections(None, [(1, _T1_FACE, None, 0.0)], 0.0)
        rng = np.random.default_rng(3)
        for k in range(45):
            t = k / 15
            d = max(0.0, 8 * math.sin(2 * math.pi * 4.2 * t)) * rng.uniform(0.5, 1.0) if mode == "talk" else 0.0
            frame = _open_mouth(img, base, d) if d > 0 else img
            enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(65, 75))])[1]
            eng.process_frame(cv2.imdecode(enc, cv2.IMREAD_COLOR), t)
            if k % 5 == 0:
                eng.on_detections(None, [(1, _T1_FACE, None, t)], t)
        result[mode] = np.array(out)
    assert len(result["still"]) > 30 and result["still"].max() <= 0.1, result["still"].max()
    assert result["talk"][5:].mean() >= 0.4, result["talk"][5:].mean()


# ---- the robot page's sender: its rate changes only when the server asks -----------

_SENDER_HARNESS = r"""'use strict';
// Runs the page's REAL ?cam=1 camera sender, cut out verbatim, against a
// fake DOM/camera/socket and a virtual clock. usage: node h.cjs <page> <scenario>
const fs = require('fs');
const vm = require('vm');
const [, , PAGE, SCENARIO] = process.argv;
const html = fs.readFileSync(PAGE, 'utf8');
const marker = "// ---- the robot's own camera, sent to the server (`?cam=1`) ----";
const at = html.indexOf(marker);
if (at < 0) throw new Error('marker not found');
const code = html.slice(at, html.indexOf('</script>', at));

let now = 1e6, nextId = 1;
const timers = new Map();
const clock = {
  setTimeout(fn, ms) { const id = nextId++; timers.set(id, { at: now + Math.max(0, +ms || 0), fn, every: 0 }); return id; },
  setInterval(fn, ms) { const id = nextId++; const e = Math.max(1, +ms || 0); timers.set(id, { at: now + e, fn, every: e }); return id; },
  clearTimeout(id) { timers.delete(id); }, clearInterval(id) { timers.delete(id); },
};
async function flush() { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); }
async function advance(ms) {
  const end = now + ms;
  await flush();
  for (;;) {
    let best = null, bestId = 0;
    for (const [id, t] of timers) if (t.at <= end && (!best || t.at < best.at)) { best = t; bestId = id; }
    if (!best) break;
    now = best.at;
    if (best.every) best.at += best.every; else timers.delete(bestId);
    best.fn();
    await flush();
  }
  now = end;
  await flush();
}
function el(tag) {
  return { tagName: tag, style: {}, children: [], appendChild(c) { this.children.push(c); return c; },
    remove() {}, addEventListener() {}, getContext() { return { drawImage() {} }; },
    toBlob(cb) { cb({ size: 10 }); }, width: 0, height: 0 };
}
function video() {
  const v = el('video');
  v.play = () => Promise.resolve();
  Object.defineProperty(v, 'videoWidth', { get() { return this.srcObject ? 1920 : 0; } });
  Object.defineProperty(v, 'videoHeight', { get() { return this.srcObject ? 1080 : 0; } });
  Object.defineProperty(v, 'currentTime', { get() { return now / 1000; } });
  return v;
}
const sockets = [];
class FakeWS {
  constructor(url) {
    this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = [];
    sockets.push(this);
    clock.setTimeout(() => { this.readyState = 1; if (this.onopen) this.onopen(); if (this.onReady) this.onReady(this); }, 5);
  }
  send() { this.sent.push(now); }
  close() { if (this.readyState === 3) return; this.readyState = 3; clock.setTimeout(() => this.onclose && this.onclose(), 0); }
  serverSays(obj) { this.onmessage({ data: JSON.stringify(obj) }); }
  serverDrops() { this.readyState = 3; this.onclose && this.onclose(); }
}
FakeWS.OPEN = 1;
const track = { stop() {}, getCapabilities() { return {}; }, applyConstraints() { return Promise.resolve(); } };
const stream = { getVideoTracks: () => [track], getTracks: () => [track] };
const win = {};
function sandbox(search) {
  const sb = {
    URLSearchParams, WebSocket: FakeWS, Promise, JSON, Math, Number, Object,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    Date: { now: () => now }, console: { log() {}, warn() {}, error() {} },
    location: { search, protocol: 'https:', host: 'robot.test' },
    document: { hidden: false, readyState: 'complete', body: el('body'), addEventListener() {},
      createElement: t => (t === 'video' ? video() : el(t)) },
    navigator: { mediaDevices: { getUserMedia: () => Promise.resolve(stream),
      enumerateDevices: () => Promise.resolve([{ kind: 'videoinput', deviceId: 'c0' }]) } },
    addEventListener(t, fn) { (win[t] = win[t] || []).push(fn); },
  };
  sb.window = sb;
  return sb;
}
function rate(ws, from, to) { return ws.sent.filter(t => t > from && t <= to).length / ((to - from) / 1000); }

(async () => {
  const search = { off: '?cam=1&camdebug=0', on: '?cam=1&camdebug=0', url_wins: '?cam=1&camdebug=0&camfps=4',
                   reconnect: '?cam=1&camdebug=0' }[SCENARIO];
  const sb = sandbox(search);
  vm.createContext(sb);
  vm.runInContext(code, sb);
  await advance(3000);                              // load + getUserMedia + connect
  const ws = sockets[0];
  if (!ws) throw new Error('no socket');
  if (SCENARIO !== 'off') ws.serverSays({ type: 'camctl', fps: 15 });
  let t0 = now; await advance(4000);
  const out = { first: rate(ws, t0 + 500, now) };
  if (SCENARIO === 'reconnect') {
    ws.serverDrops();                                // server restarted with ASD off
    await advance(3000);
    const ws2 = sockets[sockets.length - 1];
    t0 = now; await advance(4000);
    out.after = rate(ws2, t0 + 500, now);
    out.sockets = sockets.length;
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _sender(tmp_path, scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("NOT CHECKED: the robot page's camera rate (node is not installed here)")
    harness = tmp_path / "sender.cjs"
    harness.write_text(_SENDER_HARNESS, encoding="utf-8")
    res = subprocess.run([node, str(harness), str(PAGE), scenario], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr[-1500:]
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_page_script_parses(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("NOT CHECKED: the camera sender's syntax (node is not installed here)")
    html = PAGE.read_text(encoding="utf-8")
    marker = "// ---- the robot's own camera, sent to the server (`?cam=1`) ----"
    at = html.index(marker)
    src = tmp_path / "cam.js"
    src.write_text(html[at:html.index("</script>", at)], encoding="utf-8")
    res = subprocess.run([node, "--check", str(src)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr


def test_page_rate_is_its_own_until_the_server_asks(tmp_path):
    off = _sender(tmp_path, "off")
    assert 5.5 <= off["first"] <= 6.5, off          # the page's default 6 fps, untouched
    on = _sender(tmp_path, "on")
    assert 14.0 <= on["first"] <= 16.0, on


def test_page_url_rate_beats_the_server(tmp_path):
    got = _sender(tmp_path, "url_wins")
    assert 3.5 <= got["first"] <= 4.5, got


def test_page_drops_back_to_its_own_rate_on_a_new_socket(tmp_path):
    """A server restarted with ASD off sends no rate; the page must not keep
    the old one's 15 fps for the rest of the day."""
    got = _sender(tmp_path, "reconnect")
    assert 14.0 <= got["first"] <= 16.0 and got["sockets"] == 2, got
    assert 5.5 <= got["after"] <= 6.5, got
