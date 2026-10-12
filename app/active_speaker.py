"""Active speaker: which face in front of the robot is talking.

Owner, 9 Oct 2026: the robot answered two people chatting nearby, and asked
for it to talk only with whom it sees, and to know who is talking ("รู้ด้วย
ว่าคุยกับใครอยู่ … พูดชื่อคนที่คุยกับหุ่นด้วย"). `app/focus.py` arbitrates
(spec: docs/listening-focus-2026-10-09.md); this module is one of its four
inputs: a mouth-activity score 0..1 per face track, handed to
`focus.observe_mouth(track, t, activity)` at up to ASD_FPS.

Off unless ASD_ENABLED. Off means: no thread, no model loaded, the robot
camera page keeps its frame rate, and `process_detections` returns at once.

The method (landmarks, not pixels)
----------------------------------
Per face crop, insightface's 2d106det (106 2-D points, 192x192 input)
gives the inner lips and the eyes. For each sample:

    gap = mean distance of three inner-lip pairs (66-54, 62-60, 70-57)
          divided by the inter-ocular distance (IOD)

Dividing by the IOD makes it a property of the face, not of the distance
to the lens: a face 60 px wide and one 200 px wide give the same number.
Distances, not coordinates, so a head that moves or tilts in the image
plane does not change it. The eyes are rigid while speaking; the jaw (and
the face contour with it) is not, which is why the contour or the face box
is NOT the normaliser.

Talking is not "the mouth is open" -- a smile shows teeth and holds them
there -- it is the gap *changing*. So the signal is built from the
frame-to-frame steps of `gap`, each cleaned two ways before it counts:

- **Wobble subtraction.** The eyes and nose (35 points) cannot change
  shape while somebody talks. Their shape change between two frames (the
  Procrustes residual after removing translation, scale and roll) is how
  unsteady the landmark model was on that step -- jitter, blur, or a head
  rotating in depth (a nod, a turn). That amount, times WOBBLE_K, plus a
  small DEADBAND, is subtracted from the lip step (soft threshold). A
  nodding head with a closed mouth therefore contributes nothing, and the
  noise floor follows each face and each frame instead of being one
  number tuned on one person.
- **Motion gate.** A step where the face translated faster than MOVE_GATE
  IOD/s or changed size faster than SCALE_GATE (walking, a fast turn) is
  not counted at all. If most of the window is gated, the face reports
  nothing (unknown), never 0 -- focus then decides from its other inputs
  (spec rule: "If none is available -> do not use this rule").

Two numbers come out of the cleaned steps:

- **movement** over the last SHORT_WINDOW_S: summed |step| per second. Fast
  attack: the first syllable registers within a frame or two, which
  matters because the VAD opens a segment ~0.2 s into speech and the face
  pipeline lags the audio by ~0.1 s. Capped at ONSET_CAP, because a single
  movement is also what a smile looks like while it forms.
- **oscillation** over the last LONG_WINDOW_S: (summed |step| - |net
  change|) / 2 per second -- the movement that came back. A smile that
  forms and holds is one step out and none back: total = net, oscillation
  0. Speech is open-close-open: oscillation ~ total. Only this reaches 1.0.

    activity = max(min(ONSET_CAP, norm(movement)), norm(oscillation))

so: a closed or still-smiling mouth -> ~0; a smile forming -> at most
ONSET_CAP for about half a second; talking -> 0.3 within ~0.1 s of the
first syllable (synthetic median 0.10 s, worst 0.23 s) and up to 1.0 once
the mouth has come back.
`norm(x) = clip((x - floor) / span, 0, 1)` where the floor is per track:
the FLOOR_PCTL percentile of that face's own recent oscillation (people
pause), clamped to [FLOOR_MIN, FLOOR_MAX] so that somebody who talks
non-stop cannot normalise themselves into silence. Every constant below is
a first guess from synthetic sequences and the jitter measured on still
photos (sd of gap/IOD ~0.009 under +-3% box jitter + JPEG); tune them from
the robot's own logs (the periodic "asd:" line and focus's mouth max),
not from theory.

Measured (9 Oct 2026). Synthetic faces at 15 fps, gap jitter 0.008:
talking mean 0.76, closed / held smile / nodding 15 deg / turning 30 deg
0.00 (peaks <= 0.06 even with lip-only jitter twice the rigid jitter), at
40 and 120 px/IOD identically; walking 4 IOD/s across: unknown. Real
2d106det on insightface's sample photo (IOD ~48 px), JPEG q65-75 round
trips: the still face 0.00 over 4 s; the same face with a 0-8 px jaw
opening drawn in 4x a second: mean 0.74 (a face already smiling open
with teeth: 0.41). tests/test_active_speaker.py runs both.

What it cannot tell: chewing, silent laughing, mouthing words. Those move a
mouth exactly like speech; telling them apart needs the audio (see
Light-ASD below).

Tracking between detections
---------------------------
Detection (SCRFD, in the greeter's thread at FACE_FPS ~6) is the expensive
half; lip motion needs ~15 samples a second. The landmarks themselves are
the tracker: each face's next crop is centred on its current landmarks
(box = landmark extent x BOX_RATIO, shifted by BOX_OFFSET -- measured
against SCRFD boxes on the insightface sample faces: 1.30 +- 0.07,
offset (-0.02, -0.16) x extent). A detection only creates tracks, keeps
them alive (TRACK_HOLD_S), and re-anchors a track whose landmark box has
drifted off its face (IoU < REANCHOR_IOU) or that went implausible three
frames in a row. Implausible = eyes/mouth geometry no face has (mouth not
below the eyes, eyes or mouth smeared out, face extent or IOD out of
proportion with the crop) -- what the model returns for a crop of a wall.

CPU
---
One onnxruntime thread, one face per run, JPEG decoded
once per frame and shared with the greeter (robot_camera.feed.decoded_for).
Faces narrower than 0.8 x FOCUS_FACE_MIN_PX are skipped (focus cannot
count them as engaged anyway), at most MAX_TRACKS faces, and nothing at
all -- not even a decode -- while there are no tracks. Measured on the
dev PC (i5-14400F, 1 thread, 1080p JPEG q70): decode 6.2 ms + 3.8-4.5 ms
per face (crop + 2d106det); 2 faces at 15 fps = 14.4 ms/frame = 0.22 core.
The robot's A76 cores are ~3-4x slower at this: ~0.65-0.85 core for two
faces at 15 fps (estimate, not measured there -- the "asd:" log line
says the real number).
`CpuGovernor` times every processed frame; if ASD_FPS would cost more than
CPU_BUDGET_CORES it lowers the rate (not below MIN_FPS) and says so in the
log, and climbs back when there is headroom.

Light-ASD (CVPR 2023) was evaluated as an upgrade and not wired: see the
note at LIGHT_ASD below.

Licence note: 2d106det.onnx in fal/AuraFace-v1 is byte-identical to the
one in insightface's buffalo_l pack (sha256 below), as is the SCRFD the
greeter already runs; the HF repo is tagged Apache-2.0 but the files'
origin is insightface's model zoo ("non-commercial research purposes").
Same standing as the detector already in use -- the owner's call, not
this module's: owner approved 9 Oct 2026 (2d106det and the SCRFD
detector, both from insightface's buffalo_l; see _PACKS in app/faces.py).
"""
from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.active_speaker")

# ---- the model and its point layout ---------------------------------------

_ROOT = Path(__file__).resolve().parent.parent
#: Byte-identical in the auraface and buffalo_l packs (both are insightface's
#: own 2d106det). The pin is not about tampering: the point indices below are
#: a property of THIS model, and another 106-point model with another layout
#: would make "inner lips" mean some other part of the face, silently.
LANDMARK_MODEL_SHA256 = "f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf"
LANDMARK_MODEL_PATHS = (
    _ROOT / "data" / "faces" / "models" / "auraface" / "2d106det.onnx",
    _ROOT / "data" / "faces" / "models" / "buffalo_l" / "2d106det.onnx",
)
INPUT_SIZE = 192

#: 2d106det layout, checked by drawing the indices on insightface's sample
#: photo (t1.jpg) with closed, smiling and teeth-showing mouths: 33-42 and
#: 87-96 the two eyes, 72-86 the nose, 52/61 the mouth corners, 65/66/62/70
#: the inner upper lip and 54/60/57 the inner lower lip.
EYE_A = tuple(range(33, 43))
EYE_B = tuple(range(87, 97))
NOSE = tuple(range(72, 87))
MOUTH = tuple(range(52, 72))
INNER_PAIRS = ((66, 54), (62, 60), (70, 57))
RIGID = EYE_A + EYE_B + NOSE

# ---- the signal (see the module docstring; tune from robot logs) ----------

SHORT_WINDOW_S = 0.30
LONG_WINDOW_S = 0.80
MIN_SPAN_S = 0.25          # less history than this: no report yet
MAX_STEP_GAP_S = 0.35      # a hole longer than this restarts the windows
MIN_VALID = 0.6            # share of the long window not motion-gated
WOBBLE_K = 1.0
DEADBAND = 0.003           # IOD per step
MOVE_GATE = 3.0            # IOD per second of face translation
SCALE_GATE = 1.5           # |d ln(IOD)| per second
MOVE_FLOOR = 0.10          # IOD/s of cleaned movement that is still noise
MOVE_SPAN = 0.50
ONSET_CAP = 0.6
FLOOR_DEFAULT = 0.04       # IOD/s of oscillation, until a face has history
FLOOR_MIN = 0.02
FLOOR_MAX = 0.20
FLOOR_PCTL = 20
FLOOR_WINDOW_S = 8.0
FLOOR_WARM_S = 2.0
OSC_SPAN = 0.30

# ---- tracking ------------------------------------------------------------

BOX_RATIO = 1.30
BOX_OFFSET = (-0.015, -0.164)
TRACK_HOLD_S = 1.0         # no detection for this long: the track is gone
REANCHOR_IOU = 0.30
COLLAPSE_IOU = 0.60        # two tracks on one face: freeze the older one
MAX_INVALID = 3
MAX_TRACKS = 3
MIN_PX_SHARE = 0.8         # of FOCUS_FACE_MIN_PX

# ---- CPU -----------------------------------------------------------------

#: The robot's whole ASD (decode + crops + landmarks) at ASD_FPS with two
#: faces is meant to fit in one core; over that, the rate drops.
CPU_BUDGET_CORES = 1.0
MIN_FPS = 5.0
STALE_FRAME_S = 0.5        # a feed frame older than this is not "now"
REPORT_EVERY_S = 10.0

#: Light-ASD (Liao et al., CVPR 2023, github.com/Junhua-Liao/Light-ASD),
#: evaluated 9 Oct 2026 and NOT wired. Code and weights are MIT (1.02 M
#: params, 4.1 MB); it exports to ONNX (opset 17) after patching the audio
#: encoder's two MaxPool3d nodes (torch applies them to a 4-D tensor),
#: matching torch to 2e-6. It wants 25 fps grayscale 112x112 face crops
#: plus 13-dim MFCC at 100 Hz, aligned 4:1, and its backend is a forward +
#: backward GRU -- a score needs the whole window (>= 1 s latency, or
#: overlapping re-runs). Measured on the dev PC, onnxruntime 1 thread:
#: 111 ms per face per 1 s window (65 ms for 15 frames); the robot's A76 is
#: ~3-4x slower, i.e. ~0.35-0.45 core per face before decode, crops at 25
#: fps and MFCC -- two faces alone exceed the budget above. Its weights are
#: trained on AVA (films) and fine-tuned on TalkSet (VoxCeleb2 + LRS3,
#: datasets whose terms are research-oriented). What it would add is
#: audio-visual synchrony (rejects chewing and off-camera speech that
#: coincides with lip motion); revisit if the robot's logs show the
#: landmark score failing on exactly that.
LIGHT_ASD = False


def enabled() -> bool:
    return bool(settings.asd_enabled)


# ---- geometry ------------------------------------------------------------


@dataclass(frozen=True)
class MouthGeom:
    """What one face's landmarks say, in units of its own IOD."""

    gap: float                 # inner-lip opening / IOD
    iod: float                 # inter-ocular distance, px
    center: tuple              # eye midpoint, px
    rigid: tuple               # eyes+nose as complex numbers, centred, / IOD


def mouth_geometry(lm, box=None) -> MouthGeom | None:
    """Mouth opening and the rigid reference from 106 points; None if no face
    could have produced them (the model always answers, even for a wall)."""
    pts = np.asarray(lm, dtype=np.float64).reshape(-1, 2)
    if pts.shape[0] != 106 or not np.all(np.isfinite(pts)):
        return None
    ea, eb = pts[list(EYE_A)].mean(0), pts[list(EYE_B)].mean(0)
    iod = float(np.linalg.norm(eb - ea))
    if iod < 4.0:
        return None
    u = (eb - ea) / iod
    n = np.array([-u[1], u[0]])            # points down the face for an upright head
    mid = (ea + eb) / 2
    mouth = pts[list(MOUTH)]
    v = mouth.mean(0) - mid
    down, across = float(v @ n) / iod, float(v @ u) / iod
    if not (0.5 <= down <= 2.2) or abs(across) > 0.9:
        return None
    # Parts must be compact and the face the right size for its eyes: a crop
    # of a wall gives points scattered over the whole crop (eye rms ~2 IOD
    # measured on random points, vs ~0.13 on a face).
    spread = lambda p: float(np.sqrt(np.mean(np.sum((p - p.mean(0)) ** 2, axis=1)))) / iod  # noqa: E731
    if (spread(pts[list(EYE_A)]) > 0.4 or spread(pts[list(EYE_B)]) > 0.4
            or spread(mouth) > 0.7):
        return None
    extent = float(max(pts.max(0) - pts.min(0))) / iod
    if not (1.3 <= extent <= 4.5):
        return None
    if box is not None:
        side = max(box[2] - box[0], box[3] - box[1])
        if side <= 0 or not (0.12 <= iod / side <= 0.8):
            return None
        # The box the next crop would use must stay near this one: a jump
        # in size is the model losing the face, not the face changing size
        # in 1/15 s.
        if not (0.5 <= extent * iod * BOX_RATIO / side <= 2.0):
            return None
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        c = pts.mean(0)
        if math.hypot(c[0] - cx, c[1] - cy) > 0.45 * side:
            return None
    gap = float(np.mean([np.linalg.norm(pts[a] - pts[b]) for a, b in INNER_PAIRS])) / iod
    r = pts[list(RIGID)]
    z = (r[:, 0] + 1j * r[:, 1])
    z = (z - z.mean()) / iod
    return MouthGeom(gap, iod, (float(mid[0]), float(mid[1])), tuple(z.tolist()))


def shape_change(a: Iterable[complex], b: Iterable[complex]) -> float:
    """RMS shape difference of two centred point sets after the best
    similarity fit (translation, scale, roll removed). 0 = same shape."""
    za, zb = np.asarray(tuple(a)), np.asarray(tuple(b))
    den = float(np.sum(np.abs(zb) ** 2))
    if den <= 1e-12:
        return 0.0
    c = np.sum(np.conj(zb) * za) / den
    return float(np.sqrt(np.mean(np.abs(za - c * zb) ** 2)))


def box_from_landmarks(lm) -> tuple[float, float, float, float]:
    """The next crop for this face, in SCRFD-box terms (see BOX_RATIO)."""
    pts = np.asarray(lm, dtype=np.float64).reshape(-1, 2)
    lo, hi = pts.min(0), pts.max(0)
    ext = float(max(hi - lo))
    cx = (lo[0] + hi[0]) / 2 + BOX_OFFSET[0] * ext
    cy = (lo[1] + hi[1]) / 2 + BOX_OFFSET[1] * ext
    half = ext * BOX_RATIO / 2
    return (cx - half, cy - half, cx + half, cy + half)


def iou(a, b) -> float:
    if not a or not b:
        return 0.0
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---- the per-face signal --------------------------------------------------


def _norm(x: float, floor: float, span: float) -> float:
    return min(1.0, max(0.0, (x - floor) / span))


class MouthSignal:
    """One face's samples in, its activity out (see the module docstring)."""

    def __init__(self) -> None:
        self._prev: tuple[float, MouthGeom] | None = None
        #: (t, cleaned step, counted, dt) -- counted=False = motion-gated
        self._steps: deque = deque()
        self._osc: deque = deque()          # (t, oscillation) for the floor
        self._first: float | None = None
        self.last: float | None = None
        self.floor = FLOOR_DEFAULT
        self.parts: dict = {}

    def add(self, t: float, geom: MouthGeom) -> float | None:
        prev, self._prev = self._prev, (t, geom)
        if self._first is None:
            self._first = t
        if prev is None or not (0 < t - prev[0] <= MAX_STEP_GAP_S):
            self._steps.clear()
            return None
        pt, pg = prev
        dt = t - pt
        mean_iod = (geom.iod + pg.iod) / 2
        speed = math.hypot(geom.center[0] - pg.center[0],
                           geom.center[1] - pg.center[1]) / mean_iod / dt
        scale_rate = abs(math.log(geom.iod / pg.iod)) / dt
        counted = speed <= MOVE_GATE and scale_rate <= SCALE_GATE
        step = 0.0
        if counted:
            dg = geom.gap - pg.gap
            noise = WOBBLE_K * shape_change(geom.rigid, pg.rigid) + DEADBAND
            step = math.copysign(max(0.0, abs(dg) - noise), dg)
        self._steps.append((t, step, counted, dt))
        while self._steps and t - self._steps[0][0] >= LONG_WINDOW_S:
            self._steps.popleft()
        return self._score(t)

    def _score(self, t: float) -> float | None:
        steps = self._steps
        span = sum(s[3] for s in steps)
        if span < MIN_SPAN_S:
            return None
        valid = sum(s[3] for s in steps if s[2])
        if valid < MIN_VALID * span:
            self.last = None
            return None                      # the head moved too much to tell
        total = sum(abs(s[1]) for s in steps)
        net = abs(sum(s[1] for s in steps))
        osc = (total - net) / 2 / valid
        short = [s for s in steps if t - s[0] < SHORT_WINDOW_S and s[2]]
        short_span = sum(s[3] for s in short)
        move = sum(abs(s[1]) for s in short) / short_span if short_span > 0 else 0.0

        self._osc.append((t, osc))
        while self._osc and t - self._osc[0][0] > FLOOR_WINDOW_S:
            self._osc.popleft()
        if t - (self._first or t) >= FLOOR_WARM_S and len(self._osc) >= 5:
            pct = float(np.percentile([o for _, o in self._osc], FLOOR_PCTL))
            self.floor = min(FLOOR_MAX, max(FLOOR_MIN, pct))
        a_osc = _norm(osc, self.floor, OSC_SPAN)
        a_move = min(ONSET_CAP, _norm(move, MOVE_FLOOR, MOVE_SPAN))
        self.last = max(a_osc, a_move)
        self.parts = {"osc": round(osc, 3), "move": round(move, 3), "floor": round(self.floor, 3)}
        return self.last


# ---- CPU budget -----------------------------------------------------------


class CpuGovernor:
    """Keeps ASD at ASD_FPS while that fits CPU_BUDGET_CORES, lower when not.

    Fed the measured cost of every processed frame. Load = smoothed cost per
    frame x frames per second, in cores. Over budget: drop to what fits
    (x0.9 headroom), never below MIN_FPS. Under 60% of budget for HOLD_UP_S:
    step back up toward the target. Changes are rare by construction and each
    one is logged -- "the robot quietly samples lips at 5 fps" must be a line
    in the log, not a discovery.
    """

    HOLD_DOWN_S = 2.0
    HOLD_UP_S = 5.0

    def __init__(self, target_fps: float, budget: float = CPU_BUDGET_CORES,
                 min_fps: float = MIN_FPS, alpha: float = 0.2) -> None:
        self.target = max(1.0, float(target_fps))
        self.budget = budget
        self.min_fps = min(min_fps, self.target)
        self.alpha = alpha
        self.fps = self.target
        self.cost: float | None = None       # seconds per processed frame (EMA)
        self._changed_at = -1e9
        self._roomy_since: float | None = None

    @property
    def load(self) -> float:
        return 0.0 if self.cost is None else self.cost * self.fps

    def update(self, cost_s: float, now: float) -> float | None:
        """One frame's cost in; the new rate if it changed, else None."""
        self.cost = cost_s if self.cost is None else (
            self.alpha * cost_s + (1 - self.alpha) * self.cost)
        if now - self._changed_at < self.HOLD_DOWN_S:
            return None
        if self.load > self.budget and self.fps > self.min_fps:
            new = max(self.min_fps, min(self.fps * 0.8, 0.9 * self.budget / self.cost))
            return self._set(new, now)
        if self.fps < self.target and self.load < 0.6 * self.budget:
            if self._roomy_since is None:
                self._roomy_since = now
            elif now - self._roomy_since >= self.HOLD_UP_S:
                fits = 0.9 * self.budget / max(self.cost, 1e-6)
                return self._set(min(self.target, self.fps * 1.25, fits), now)
        else:
            self._roomy_since = None
        return None

    def _set(self, fps: float, now: float) -> float | None:
        fps = round(fps, 1)
        if abs(fps - self.fps) < 0.05:
            return None
        self.fps, self._changed_at, self._roomy_since = fps, now, None
        return fps


# ---- the landmark model ----------------------------------------------------


class Landmarker:
    """2d106det over onnxruntime, one thread, one face per run.

    Not batched: the graph declares its output as [1, 212], and a batch of
    two works but makes onnxruntime print a shape warning on every run (15
    a second into the robot's log). Batching measured no faster anyway
    (2 faces: 9.07 ms batched vs 2 x 4.55 ms).
    """

    def __init__(self, path: Path) -> None:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self._input = self._sess.get_inputs()[0].name

    @classmethod
    def find(cls) -> "Landmarker | None":
        for path in LANDMARK_MODEL_PATHS:
            if not path.is_file():
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != LANDMARK_MODEL_SHA256:
                logger.warning("asd: %s is not the pinned 2d106det (sha256 %s…) -- its "
                               "point layout is unknown, mouth activity off", path, digest[:12])
                return None
            return cls(path)
        logger.warning("asd: no 2d106det.onnx under data/faces/models/ -- mouth activity "
                       "off (it ships with the auraface pack; the robot deploy must carry it)")
        return None

    def __call__(self, frame: np.ndarray, boxes: list) -> list[np.ndarray]:
        import cv2

        blobs, inverse = [], []
        for x1, y1, x2, y2 in boxes:
            side = max(x2 - x1, y2 - y1) * 1.5          # insightface's own crop rule
            s = INPUT_SIZE / max(side, 1.0)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            m = np.array([[s, 0.0, INPUT_SIZE / 2 - cx * s],
                          [0.0, s, INPUT_SIZE / 2 - cy * s]], dtype=np.float32)
            crop = cv2.warpAffine(frame, m, (INPUT_SIZE, INPUT_SIZE), borderValue=0.0)
            # BGR -> RGB, HWC -> CHW; the graph normalises (Sub/Mul up front).
            blobs.append(crop[:, :, ::-1].transpose(2, 0, 1).astype(np.float32))
            inverse.append(cv2.invertAffineTransform(m))
        if not blobs:
            return []
        result = []
        for blob, im in zip(blobs, inverse):
            pred = self._sess.run(None, {self._input: blob[None]})[0][0]
            p = (pred.reshape(-1, 2).astype(np.float64) + 1.0) * (INPUT_SIZE // 2)
            result.append(p @ im[:, :2].T.astype(np.float64) + im[:, 2])
        return result


# ---- tracks and the engine --------------------------------------------------


class _Track:
    __slots__ = ("id", "box", "det_box", "det_t", "invalid", "frozen", "anchor", "mouth")

    def __init__(self, tid: int, box, t: float) -> None:
        self.id = tid
        self.box = tuple(float(v) for v in box)
        self.det_box = self.box
        self.det_t = t
        self.invalid = 0
        self.frozen = False
        self.anchor = 0          # bumped on every re-anchor: stale landmark boxes lose
        self.mouth = MouthSignal()


def _unpack(entry) -> tuple[int, tuple, float | None] | None:
    """(track_id, bbox, kps, t) as facewatch.latest_tracks() gives them, or
    any object with track/track_id/id + bbox (+ t). Bad entries are skipped."""
    try:
        if isinstance(entry, (tuple, list)):
            tid, bbox = entry[0], entry[1]
            t = entry[3] if len(entry) > 3 else None
        else:
            tid = next(getattr(entry, k) for k in ("track", "track_id", "id") if hasattr(entry, k))
            bbox, t = entry.bbox, getattr(entry, "t", None)
        box = tuple(float(v) for v in tuple(bbox)[:4])
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        return int(tid), box, (None if t is None else float(t))
    except (StopIteration, TypeError, ValueError, IndexError, AttributeError):
        return None


def _focus_sink(track: int, t: float, activity: float) -> None:
    """focus.observe_mouth, if focus exists on this machine yet."""
    global _focus_missing_logged
    try:
        from app import focus
    except ImportError:
        if not _focus_missing_logged:
            _focus_missing_logged = True
            logger.warning("asd: app.focus is not importable -- mouth activity measured "
                           "but has nowhere to go")
        return
    focus.observe_mouth(track, t, activity)


_focus_missing_logged = False


def _engaged_px() -> float:
    """Focus's engagement width bar (`focus.face_min_px`: FOCUS_FACE_MIN_PX
    capped at the greeter's FACE_MIN_PX). Read from focus, not from the
    setting, so the faces skipped here are exactly the ones focus cannot
    count — with the raw setting a greeted 60 px face would be engaged
    there and never have a mouth measured here."""
    try:
        from app import focus

        return float(focus.face_min_px())
    except ImportError:
        return float(settings.focus_face_min_px)


class Engine:
    """Tracks + landmarks + per-face signals. One per process (the robot is one
    robot); constructible with fakes for tests (`landmarker=`, `sink=`)."""

    def __init__(self, landmarker: Callable | None = None,
                 sink: Callable[[int, float, float], None] = _focus_sink,
                 feed=None, target_fps: float | None = None,
                 min_px: float | None = None) -> None:
        self._landmarker = landmarker
        self._sink = sink
        self._feed = feed
        self._lock = threading.Lock()
        self._tracks: dict[int, _Track] = {}
        self.governor = CpuGovernor(settings.asd_fps if target_fps is None else target_fps)
        self.min_px = (MIN_PX_SHARE * _engaged_px()) if min_px is None else min_px
        # Detection frames for the worker when there is no feed to read
        # (a local USB camera: the greeter owns the capture handle).
        self._slot: tuple[np.ndarray, float] | None = None
        self._slot_ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.dead = False
        self.frames = 0
        self.timing = {"decode_ms": 0.0, "landmark_ms": 0.0, "faces": 0}
        self._last_report = 0.0

    # -- detections (greeter thread: must stay cheap) ----------------------

    def on_detections(self, frame, tracks, t: float) -> None:
        seen: set[int] = set()
        with self._lock:
            for entry in tracks or ():
                got = _unpack(entry)
                if got is None:
                    continue
                tid, box, tt = got
                tt = t if tt is None else tt
                seen.add(tid)
                tr = self._tracks.get(tid)
                if tr is None:
                    self._tracks[tid] = _Track(tid, box, tt)
                    continue
                tr.det_box, tr.det_t = box, tt
                if tr.frozen or iou(tr.box, box) < REANCHOR_IOU:
                    tr.box, tr.frozen, tr.invalid = box, False, 0
                    tr.anchor += 1
            for tid in [k for k, tr in self._tracks.items()
                        if k not in seen and t - tr.det_t > TRACK_HOLD_S]:
                del self._tracks[tid]
        if self._feed is None and frame is not None:
            self._slot = (frame, t)
            self._slot_ready.set()

    # -- frames (worker thread, or a test calling it directly) -------------

    def process_frame(self, frame: np.ndarray, t: float) -> dict[int, float]:
        """Landmarks for every live track on this frame; emits and returns
        {track: activity} for the faces that produced a score."""
        if self._landmarker is None or frame is None:
            return {}
        with self._lock:
            for tid in [k for k, tr in self._tracks.items() if t - tr.det_t > TRACK_HOLD_S]:
                del self._tracks[tid]
            live = [tr for tr in self._tracks.values()
                    if not tr.frozen and (tr.det_box[2] - tr.det_box[0]) >= self.min_px]
            live.sort(key=lambda tr: tr.det_box[2] - tr.det_box[0], reverse=True)
            live = live[:MAX_TRACKS]
            jobs = [(tr, tr.box, tr.anchor) for tr in live]
        if not jobs:
            return {}
        t0 = time.perf_counter()
        marks = self._landmarker(frame, [box for _, box, _ in jobs])
        self.timing["landmark_ms"] = (time.perf_counter() - t0) * 1000 / len(jobs)
        self.timing["faces"] = len(jobs)
        out: dict[int, float] = {}
        with self._lock:
            for (tr, box, anchor), lm in zip(jobs, marks):
                if self._tracks.get(tr.id) is not tr or tr.anchor != anchor:
                    continue                 # re-anchored or dropped meanwhile
                geom = mouth_geometry(lm, box)
                if geom is None:
                    tr.invalid += 1
                    if tr.invalid >= MAX_INVALID:
                        tr.frozen = True     # wait for the next detection
                    continue
                tr.invalid = 0
                tr.box = box_from_landmarks(lm)
                act = tr.mouth.add(t, geom)
                if act is not None:
                    out[tr.id] = act
            self._collapse()
        for tid, act in out.items():
            try:
                self._sink(tid, t, act)
            except Exception:  # noqa: BLE001 - a consumer bug must not stop the lips
                logger.exception("asd: observe_mouth failed")
        self.frames += 1
        return out

    def _collapse(self) -> None:
        """Two landmark-tracked boxes that slid onto one face: the one whose
        detection is older is frozen until a detection re-anchors it."""
        trs = [tr for tr in self._tracks.values() if not tr.frozen]
        for i, a in enumerate(trs):
            for b in trs[i + 1:]:
                if not a.frozen and not b.frozen and iou(a.box, b.box) > COLLAPSE_IOU:
                    (a if a.det_t < b.det_t else b).frozen = True

    def has_tracks(self) -> bool:
        """Anything worth decoding a frame for (same filter as process_frame)."""
        with self._lock:
            return any(not tr.frozen and (tr.det_box[2] - tr.det_box[0]) >= self.min_px
                       for tr in self._tracks.values())

    def snapshot(self) -> dict:
        with self._lock:
            tracks = {tid: {"activity": None if tr.mouth.last is None else round(tr.mouth.last, 3),
                            "frozen": tr.frozen, "width_px": int(tr.det_box[2] - tr.det_box[0]),
                            **tr.mouth.parts}
                      for tid, tr in self._tracks.items()}
        g = self.governor
        return {"enabled": True, "fps": g.fps, "target_fps": g.target,
                "load_cores": round(g.load, 3), "budget_cores": g.budget,
                "frames": self.frames, "dead": self.dead,
                "decode_ms": round(self.timing["decode_ms"], 2),
                "landmark_ms_per_face": round(self.timing["landmark_ms"], 2),
                "tracks": tracks}

    # -- the worker ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="active-speaker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._slot_ready.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _run(self) -> None:
        if self._landmarker is None:
            try:
                self._landmarker = Landmarker.find()
            except Exception:  # noqa: BLE001 - onnxruntime missing, corrupt file
                logger.exception("asd: landmark model failed to load")
                self._landmarker = None
            if self._landmarker is None:
                self.dead = True
                return
        logger.info("asd: on -- %s, target %.0f fps, budget %.1f core",
                    "robot camera frames" if self._feed is not None
                    else "detection frames only (local camera: the greeter owns it)",
                    self.governor.target, self.governor.budget)
        last_seq = None
        next_at = time.monotonic()
        while not self._stop.is_set():
            if self._feed is None:
                self._slot_ready.wait(1.0)
                self._slot_ready.clear()
                item, self._slot = self._slot, None
                if item is None:
                    continue
                frame, t = item
                cost0 = time.perf_counter()
                self.timing["decode_ms"] = 0.0
            else:
                now = time.monotonic()
                if now < next_at:
                    self._stop.wait(next_at - now)
                    continue
                if not self.has_tracks():
                    next_at = now + 0.1      # nobody tracked: not even a decode
                    continue
                seq, jpeg, at = self._feed.latest()
                if seq == last_seq or not jpeg or now - at > STALE_FRAME_S:
                    # Due but nothing new yet: look again shortly instead of
                    # skipping a whole period -- page and worker tick at the
                    # same rate, and a phase slip would halve the samples.
                    self._stop.wait(0.01)
                    continue
                last_seq = seq
                next_at = now + 1.0 / self.governor.fps
                cost0 = time.perf_counter()
                frame = self._feed.decoded_for(seq, jpeg)
                self.timing["decode_ms"] = (time.perf_counter() - cost0) * 1000
                t = at
            try:
                self.process_frame(frame, t)
            except Exception:  # noqa: BLE001
                logger.exception("asd: frame failed")
                continue
            now = time.monotonic()
            # The governor sets the feed's sampling rate. Without a feed the
            # rate is the greeter's (FACE_FPS), which it cannot change: a
            # "dropping to N fps" line there would be a lie.
            changed = (self.governor.update(time.perf_counter() - cost0, now)
                       if self._feed is not None else None)
            if changed is not None:
                g = self.governor
                (logger.warning if changed < g.target else logger.info)(
                    "asd: %.0f ms per frame (%d faces) -- running at %.1f fps "
                    "(target %.0f, budget %.1f core, load now %.2f)",
                    g.cost * 1000, self.timing["faces"], changed, g.target, g.budget, g.load)
            if now - self._last_report >= REPORT_EVERY_S:
                self._last_report = now
                self._report()

    def _report(self) -> None:
        snap = self.snapshot()
        mouths = " ".join("t%d:%s" % (tid, "-" if v["activity"] is None else "%.2f" % v["activity"])
                          for tid, v in snap["tracks"].items())
        logger.info("asd: %.1f fps, %.2f core (decode %.1f ms, landmarks %.1f ms/face x %d), "
                    "mouth %s", snap["fps"], snap["load_cores"], snap["decode_ms"],
                    snap["landmark_ms_per_face"], self.timing["faces"], mouths or "-")


# ---- module API (what the backbone calls) ----------------------------------

_engine: Engine | None = None
_engine_lock = threading.Lock()


def engine() -> Engine | None:
    return _engine


def _ensure() -> Engine | None:
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            feed = None
            if settings.face_camera_source == "robot":
                from app import robot_camera
                feed = robot_camera.feed
            _engine = Engine(feed=feed)
            _engine.start()
    return _engine


def process_detections(frame, tracks, t: float) -> None:
    """Called by the greeter side after each detection with
    facewatch.latest_tracks() -- (track_id, bbox, kps, t) per face. Cheap:
    updates the track table (and, without a robot feed, hands the frame to
    the worker). Starts the worker on first use. ASD off: returns at once."""
    if not enabled():
        return
    eng = _ensure()
    if eng is None or eng.dead:
        return
    eng.on_detections(frame, tracks, t)


def snapshot() -> dict:
    eng = _engine
    if eng is None:
        return {"enabled": enabled(), "running": False}
    return {**eng.snapshot(), "running": True}


def stop() -> None:
    """Stop the worker and forget every track (tests, shutdown)."""
    global _engine
    with _engine_lock:
        eng, _engine = _engine, None
    if eng is not None:
        eng.stop()
