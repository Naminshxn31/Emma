"""Direction of arrival from the robot's mic array -> app/focus.py.

docs/listening-focus-2026-10-09.md, input 4. Owner, 9 Oct 2026: the robot
answered two people chatting beside it. Focus decides whether a speech
segment may open; one thing it may weigh is *where the voice came from*, and
this module says that, in the camera's frame (+ = the robot's left).

Who does what:

* The robot's app (`ArrayListener.java`) opens the USB array "Bothlent UAC
  Dongle" natively -- 8 channels, 16 kHz; the WebView's getUserMedia only
  ever gets stereo -- and per ~64 ms frame above an energy gate computes the
  GCC-PHAT time lag of the six pairs of channels 0-3 (`DoaMath.java`). Only
  those numbers come here, on /ws/robot-doa. **No audio leaves the robot on
  this socket.**
* This module turns a lag vector into a bearing and calls
  `focus.observe_doa(t, bearing_deg | None, confidence)` for every frame.

**The mic geometry is unknown** -- nobody has measured the array, and the
array's position relative to the camera is what a bearing in the camera's
frame needs anyway. So the map is learned on the robot from pairs (lag
vector, bearing of the single engaged face whose mouth is moving), which is
exactly the situation the camera can vouch for:

* A far-field source at azimuth a gives lags L = c(elevation, cos a) + g sin a
  (each lag is a dot product of a mic-position difference with the source's
  unit vector). Faces stand in the camera's ~+-35 degrees and at roughly one
  elevation, so the cos a and elevation terms move little: a constant `c`
  plus a slope `g` in sin a is the far-field model linearised over the
  sector the faces can teach. Two 6-vectors, fitted by robust weighted least
  squares; nothing about the positions has to be known.
* Every lag vector is first made *consistent*: six pair lags of four mics
  carry three independent numbers (lag02 = lag01 + lag12 ...). The weighted
  projection onto that subspace halves the noise, and what it throws away
  (`closure`) says, without any geometry, whether one source explains the
  frame -- two people talking, or the room's echo, do not close.
* Limits that are physics, not tuning: a line array cannot tell front from
  back, and nothing learned from faces in front can either -- a voice from
  behind at 150 degrees reads like one at 30. A voice from the SIDE reads as
  the side (sin a saturates), which is what rule 4 needs: the faces are all
  in front, so "far from every face" is mostly "off to the side".

Before the calibration is ready (MIN_PAIRS pairs spanning MIN_SPREAD_DEG),
and for every frame one source does not explain or that overlaps the robot's
own voice, focus is told `observe_doa(t, None, 0.0)`: unknown. Unknown is
never "deny" -- every focus input fails open (the spec's rule).

The calibration lives in `data/doa_calibration.json` on the machine that runs
the server (on the robot: code/data/, the server's only writable place),
with the pairs it came from, fingerprinted by rate/channels/device: lags from
another channel map or device are another space, and mixing two spaces gives
numbers without an error (the embeddings lesson in CLAUDE.md).
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import os
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

#: The pair order every lag/strength vector uses, app and server alike.
#: lag[p] = arrival at mic j minus arrival at mic i, in samples at RATE.
PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))


def _pair_matrix() -> np.ndarray:
    m = np.zeros((len(PAIRS), 4))
    for row, (i, j) in enumerate(PAIRS):
        m[row, i] = -1.0
        m[row, j] = 1.0
    return m


_M = _pair_matrix()
#: Arrival times up to a constant: mic 0's is pinned to 0.
_M3 = _M[:, 1:]

# ---- what the app is told on connect (DoaMath.Params clamps each again) ----
RATE = 16000
#: 1024 samples = 64 ms per frame: long enough for a stable GCC peak, short
#: enough that a turn of the head between two words is two frames.
FRAME = 1024
#: CLAUDE.md "ไมโครโฟน ... ช่อง 0-3 ของจริง". The app's stats report all
#: eight channels' RMS so this can be checked on the robot, not assumed.
CHANNELS = (0, 1, 2, 3)
#: Lag search window, samples (+-16 = 34 cm of path difference: any dongle).
MAX_LAG = 16.0
#: Frames quieter than this (RMS of the four mics, full scale = 1) are not
#: analysed or sent. -48 dBFS: the room's hum stays home.
GATE_RMS = 0.004
MAX_FPS = 16
#: PHAT band. Below ~200 Hz is hum and handling noise; above ~4 kHz a few-cm
#: array starts to alias.
BAND_HZ = (200, 4000)
STATS_S = 2.0

# ---- the link's own limits ----
#: A doa frame is ~170 bytes and a status ~450; anything bigger is not ours.
MAX_TEXT_BYTES = 1024
#: Token buckets: frames at up to the app's rate (+25 %), statuses at 2/s.
FRAME_BURST = 2 * MAX_FPS
FRAME_REFILL_PER_S = MAX_FPS * 1.25
STATUS_BURST = 4
STATUS_REFILL_PER_S = 2.0
#: The server beats this often (the app's read timeout is 75 s), and closes a
#: link that has sent nothing for SILENT_S (the app sends a status every
#: STATS_S, capturing or not) -- a half-open socket must not hold the slot.
BEAT_S = 15.0
SILENT_S = 60.0
#: With ROBOT_FACE_TOKEN set, the app's doa_hello must arrive this fast.
HELLO_TIMEOUT_S = 5.0
STATES = frozenset({"starting", "capturing", "no_permission", "no_array", "capture_refused",
                    "conflict", "stopped", "idle"})
SOURCES = frozenset({"VOICE_RECOGNITION", "UNPROCESSED", "MIC"})

# ---- the estimator ----
#: Frames whose strength x closure quality falls under this say "unknown".
MIN_SOURCE_QUALITY = 0.15
#: Closure noise scale, samples. Measured on synthetic frames: one source
#: with 0.15-sample lag noise closes to p99 0.22; two sources whose pair
#: peaks split between them median 0.45. Retune from `mean_closure` in the
#: turn log's doa_summary rows, not from this.
CLOSURE_SCALE = 0.4
#: The robot's own voice: frames up to this long after its audio ends are
#: about the loudspeaker, not about anybody.
SPEAKING_TAIL_S = 0.3
#: Calibration pairs: only clean frames, at most PAIR_EVERY_S apart (16
#: near-identical frames a second would outvote everybody else).
PAIR_MIN_STRENGTH = 0.4
PAIR_MAX_CLOSURE = 0.35
PAIR_EVERY_S = 0.25
PAIR_LOG_EVERY_S = 1.0
#: Mouth activity that counts as speaking, and the window around a frame in
#: which faces/mouths are read.
MOUTH_MIN = 0.5
MOUTH_OTHER_MAX = 0.3
TRUTH_WINDOW_S = 0.5
#: focus.snapshot() is read at most this often for calibration truth.
TRUTH_TRY_EVERY_S = 0.1
SAVE_EVERY_S = 30.0
SUMMARY_EVERY_S = 30.0
STATS_LOG_EVERY_S = 10.0


def ready_frame() -> dict:
    """The first thing the app hears. It resets its backoff on THIS frame
    only -- never on onopen: the token gate accepts-then-closes too.

    `array` / `capture8` appear only with ARRAY_AUDIO / ARRAY_CAPTURE8 on
    (app/array_audio.py): with both off the frame is exactly what it was,
    and an app only ever beamforms or uploads raw audio when told to here."""
    frame = {"type": "doa_ready", "rate": RATE, "frame": FRAME, "channels": list(CHANNELS),
             "max_lag": MAX_LAG, "gate_rms": GATE_RMS, "max_fps": MAX_FPS,
             "band": list(BAND_HZ), "stats_s": STATS_S}
    if settings.array_audio or settings.array_capture8:
        from app import array_audio

        frame.update(array_audio.ready_fields())
    return frame


# ==================== geometry-free pieces ====================


def farfield_lags(positions_m, azimuth_deg: float, elevation_deg: float = 0.0,
                  rate: int = RATE, speed: float = 343.0) -> np.ndarray:
    """The six pair lags (samples) a far-field source makes on mics at
    `positions_m` (4 x 3, metres; x = the camera's axis, y = the robot's
    left, z = up). The model the calibration linearises -- and what the
    synthetic tests and `scripts/doa_calibrate.py --synthetic` feed it."""
    p = np.asarray(positions_m, float)
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    toward = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    arrival = -(p @ toward) / speed * rate     # nearer to the source = earlier
    return np.array([arrival[j] - arrival[i] for i, j in PAIRS])


def consistent(lags, strength) -> tuple[np.ndarray, float]:
    """(the closest lag vector four arrival times can make, closure RMS).

    Weighted by strength: a pair whose GCC peak was weak says less. The
    closure is what no single far-field source can explain -- two talkers,
    an echo, or a wrong peak -- in samples, needing no geometry."""
    L = np.asarray(lags, float)
    w = np.maximum(np.clip(np.asarray(strength, float), 0.0, 1.0), 0.05) ** 2
    sw = np.sqrt(w)
    t, *_ = np.linalg.lstsq(_M3 * sw[:, None], L * sw, rcond=None)
    fitted = _M3 @ t
    closure = float(math.sqrt(float(np.sum(w * (L - fitted) ** 2)) / float(np.sum(w))))
    return fitted, closure


class ClockMap:
    """The app's frame time (its own monotonic ms) -> this process's
    time.monotonic(), converted at arrival as the spec asks.

    offset = the smallest (arrival - t_app) of the last WINDOW_S: the least
    delayed frame bounds the clock offset plus the network's floor latency
    (milliseconds on adb reverse or the robot's own server). A converted
    time is never later than its own arrival. One per link: an app restart
    is a new clock."""

    WINDOW_S = 30.0

    def __init__(self) -> None:
        self._q: deque[tuple[float, float]] = deque()   # (arrival, value), values increasing

    def convert(self, t_app_ms: float, arrival: float) -> float:
        value = arrival - t_app_ms / 1000.0
        while self._q and self._q[-1][1] >= value:
            self._q.pop()
        self._q.append((arrival, value))
        while self._q[0][0] < arrival - self.WINDOW_S:
            self._q.popleft()
        return min(t_app_ms / 1000.0 + self._q[0][1], arrival)


# ==================== the learned map ====================


class Calibration:
    """lag vector -> sin(bearing), learned from (consistent lags, face
    bearing) pairs. See the module docstring for why `c + g sin a`."""

    MIN_PAIRS = 40
    FULL_PAIRS = 120
    MAX_PAIRS = 400
    #: Weighted spread of the pairs' bearings: one customer standing dead
    #: centre all day teaches a point, not a slope.
    MIN_SPREAD_DEG = 4.0
    #: |g| x spread / sigma: the slope must stand out of the noise.
    MIN_IDENT = 2.0
    SIGMA_FLOOR = 0.05
    REJECT_SIGMAS = 3.0
    #: Once ready, a pair this far off the model is someone else's voice
    #: while the face's lips moved: not learned. If most recent pairs are
    #: off, the setup changed (array moved, head turned) -> start over.
    DISAGREE_SIGMAS = 4.0
    DISAGREE_MIN = 0.5
    VERDICTS = 60
    RESET_SHARE = 0.7
    REFIT_EVERY = 8

    def __init__(self, fingerprint: dict | None = None) -> None:
        self.fingerprint = dict(fingerprint or {})
        self.pairs: deque[tuple[tuple[float, ...], float, float]] = deque(maxlen=self.MAX_PAIRS)
        self.c: np.ndarray | None = None
        self.g: np.ndarray | None = None
        self.sigma = 0.0
        self.spread_deg = 0.0
        self.ident = 0.0
        self.inliers = 0
        self.ready = False
        self.dirty = False
        self.updated = ""
        self._since_fit = 0
        self._verdicts: deque[bool] = deque(maxlen=self.VERDICTS)

    # -- learning --

    def add(self, fitted, bearing_deg: float, weight: float = 1.0) -> str:
        """'added' | 'disagrees' (not learned) | 'reset' (started over)."""
        L = np.asarray(fitted, float)
        if self.ready:
            r = float(np.linalg.norm(L - self.c - self.g * math.sin(math.radians(bearing_deg))))
            agrees = r <= max(self.DISAGREE_SIGMAS * self.sigma, self.DISAGREE_MIN)
            self._verdicts.append(agrees)
            if (len(self._verdicts) == self.VERDICTS
                    and self._verdicts.count(False) > self.RESET_SHARE * self.VERDICTS):
                self.reset()
                return "reset"
            if not agrees:
                return "disagrees"
        self.pairs.append((tuple(round(float(x), 4) for x in L), float(bearing_deg),
                           float(max(weight, 1e-3))))
        self.dirty = True
        self._since_fit += 1
        if not self.ready or self._since_fit >= self.REFIT_EVERY:
            self.fit()
        return "added"

    def reset(self) -> None:
        self.pairs.clear()
        self._verdicts.clear()
        self.c = self.g = None
        self.sigma = self.spread_deg = self.ident = 0.0
        self.ready = False
        self.dirty = True
        self._since_fit = 0

    def fit(self) -> bool:
        """Robust weighted least squares; True = ready to answer.

        Robust by REJECTION, not by down-weighting: a pair taken while
        somebody off camera talked over a face whose lips moved sits several
        samples off, and Huber weights still let ten percent of those double
        sigma (measured on synthetic arrays: 0.53-0.70 vs 0.27-0.45 samples),
        which made the calibration look unidentifiable. Sigma comes from the
        median residual (a 3-dof norm: RMS ~ 1.12 x median), pairs beyond
        REJECT_SIGMAS of it sit out the next pass, until the set settles.
        A cos(bearing) column was measured too and left out: it helped a
        horizontal circle, hurt a vertical one (elevation leaks into it)."""
        self._since_fit = 0
        n = len(self.pairs)
        if n < 3:
            self.ready = False
            return False
        L = np.array([p[0] for p in self.pairs])
        bearing = np.array([p[1] for p in self.pairs])
        w0 = np.array([p[2] for p in self.pairs])
        s = np.sin(np.radians(bearing))
        X = np.column_stack([np.ones(n), s])
        inlier = np.ones(n, bool)
        coef = np.zeros((2, L.shape[1]))
        r = np.zeros(n)
        for _ in range(6):
            w = w0 * inlier
            sw = np.sqrt(w)
            coef, *_ = np.linalg.lstsq(X * sw[:, None], L * sw[:, None], rcond=None)
            r = np.linalg.norm(L - X @ coef, axis=1)
            sigma = max(1.12 * float(np.median(r[inlier])), self.SIGMA_FLOOR)
            again = r <= self.REJECT_SIGMAS * sigma
            if again.sum() < 3 or (again == inlier).all():
                break
            inlier = again
        w = w0 * inlier
        sigma = max(float(math.sqrt(float(np.sum(w * r ** 2)) / float(np.sum(w)))), self.SIGMA_FLOOR)
        self.inliers = int(inlier.sum())
        self.c, self.g = coef[0], coef[1]
        mean = float(np.sum(w0 * bearing) / np.sum(w0))
        self.spread_deg = float(math.sqrt(float(np.sum(w0 * (bearing - mean) ** 2)) / float(np.sum(w0))))
        s_spread = float(math.sqrt(float(np.sum(w0 * (s - np.sum(w0 * s) / np.sum(w0)) ** 2))
                                   / float(np.sum(w0))))
        self.sigma = sigma
        self.ident = float(np.linalg.norm(self.g)) * s_spread / sigma
        self.ready = (n >= self.MIN_PAIRS and self.spread_deg >= self.MIN_SPREAD_DEG
                      and self.ident >= self.MIN_IDENT)
        self.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        return self.ready

    # -- answering --

    def arrivals(self, bearing_deg: float) -> list[float] | None:
        """The arrival times (samples, mic 0 = 0) of a voice at `bearing_deg`:
        the steering a beamformer needs (ARRAY_AUDIO), read off the learned
        map -- lags = c + g sin(bearing), made consistent, solved for the
        three arrival times the six lags are differences of. None until the
        map is ready: an unready map is a guess, and a beam steered by a
        guess is worse than no beam."""
        if not self.ready or self.c is None or self.g is None:
            return None
        lags = self.c + self.g * math.sin(math.radians(max(-90.0, min(90.0, bearing_deg))))
        t3, *_ = np.linalg.lstsq(_M3, lags, rcond=None)
        return [0.0] + [round(float(x), 4) for x in t3]

    def predict(self, fitted) -> tuple[float, float]:
        """(sin of the bearing, unclamped; residual in samples)."""
        d = np.asarray(fitted, float) - self.c
        gg = float(self.g @ self.g)
        s_hat = float(self.g @ d) / gg if gg > 0 else 0.0
        return s_hat, float(np.linalg.norm(d - self.g * s_hat))

    def confidence(self, s_hat: float, resid: float) -> tuple[float, dict]:
        """0..1 from the fit alone (the frame's own quality is multiplied in
        by the caller), with the parts for the log."""
        g_norm = float(np.linalg.norm(self.g))
        cos_a = math.sqrt(max(1.0 - min(s_hat * s_hat, 1.0), 0.05 ** 2))
        prec_deg = math.degrees(self.sigma / max(g_norm, 1e-9) / cos_a)
        parts = {
            "fit": math.exp(-(max(0.0, resid - self.sigma) / (2.0 * self.sigma + 0.1)) ** 2),
            "range": 1.0 if abs(s_hat) <= 1.0 else math.exp(-((abs(s_hat) - 1.0) / 0.15) ** 2),
            "precision": math.exp(-(prec_deg / 15.0) ** 2),
            "pairs": min(1.0, len(self.pairs) / self.FULL_PAIRS),
        }
        conf = 1.0
        for value in parts.values():
            conf *= value
        return conf, parts

    # -- the file --

    def to_json(self) -> dict:
        return {
            "version": 1,
            "fingerprint": self.fingerprint,
            "updated": self.updated,
            "pairs": [list(p[0]) + [round(p[1], 2), round(p[2], 4)] for p in self.pairs],
            "model": None if self.c is None else {
                "c": [round(float(x), 5) for x in self.c],
                "g": [round(float(x), 5) for x in self.g],
                "sigma": round(self.sigma, 5), "spread_deg": round(self.spread_deg, 2),
                "ident": round(self.ident, 3), "ready": self.ready, "n": len(self.pairs)},
        }

    @classmethod
    def from_json(cls, data: dict) -> "Calibration":
        """Rebuilt from its pairs (refit), never trusted from the stored
        model alone: a file edited by hand or by an older build is re-derived."""
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("not a version-1 DOA calibration")
        cal = cls(data.get("fingerprint") if isinstance(data.get("fingerprint"), dict) else {})
        for row in data.get("pairs") or []:
            if (isinstance(row, list) and len(row) == 8
                    and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                            and math.isfinite(x) for x in row)):
                cal.pairs.append((tuple(float(x) for x in row[:6]), float(row[6]), float(max(row[7], 1e-3))))
        cal.fit()
        cal.updated = str(data.get("updated") or "")
        cal.dirty = False
        return cal


def fingerprint_matches(stored: dict, wanted: dict) -> bool:
    """rate and channels must agree; the device too when both name one."""
    for key in ("rate", "channels"):
        if stored.get(key) != wanted.get(key):
            return False
    a, b = stored.get("device") or "", wanted.get("device") or ""
    return not (a and b and a != b)


def _calibration_path() -> Path:
    """Runtime data: on the robot, code/data/ is the server's writable dir.
    Tests point this at tmp_path."""
    return Path(__file__).resolve().parent.parent / "data" / "doa_calibration.json"


def _load(fingerprint: dict) -> Calibration:
    path = _calibration_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cal = Calibration.from_json(data)
    except FileNotFoundError:
        return Calibration(fingerprint)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        logger.warning("doa calibration at %s unreadable (%s) — starting a new one", path, exc)
        return Calibration(fingerprint)
    if not fingerprint_matches(cal.fingerprint, fingerprint):
        logger.warning("doa calibration at %s is for %s, not %s — starting a new one "
                       "(the file is replaced once the new one has pairs)",
                       path, cal.fingerprint, fingerprint)
        return Calibration(fingerprint)
    cal.fingerprint.update({k: v for k, v in fingerprint.items() if v})
    logger.info("doa calibration loaded: %d pairs, ready=%s, spread %.1f°",
                len(cal.pairs), cal.ready, cal.spread_deg)
    return cal


def _write(payload: dict) -> None:
    path = _calibration_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        logger.warning("doa calibration could not be saved to %s", path, exc_info=True)


# ==================== who is speaking on camera (calibration truth) ====================

#: Pushed by whoever knows it (focus, a test, a supervised session):
#: (t, bearing) of the one engaged face whose mouth moves.
_PUSHED: deque[tuple[float, float]] = deque(maxlen=64)


def note_speaker(t: float, bearing_deg: float) -> None:
    """The single engaged face whose mouth is moving is at `bearing_deg` at
    `t` (time.monotonic()). The push path for calibration truth; the pull
    path reads `focus.snapshot()` (`_truth_from_snapshot`). Never blocks."""
    if isinstance(t, (int, float)) and isinstance(bearing_deg, (int, float)) \
            and math.isfinite(t) and math.isfinite(bearing_deg) and abs(bearing_deg) <= 90:
        _PUSHED.append((float(t), float(bearing_deg)))


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _first(d: dict, *keys):
    for key in keys:
        if key in d:
            return d[key]
    return None


def _mouth_of(face: dict, mouths, t: float) -> float | None:
    """Latest mouth activity for a face near `t`, from whichever place the
    snapshot keeps it: on the face, or in a per-track table."""
    activity = _num(_first(face, "mouth", "mouth_activity", "activity"))
    at = _num(_first(face, "mouth_t", "activity_t"))
    if activity is not None:
        return activity if at is None or abs(at - t) <= TRUTH_WINDOW_S else None
    if isinstance(mouths, dict):
        track = face.get("track")
        entry = mouths.get(track, mouths.get(str(track)))
        if isinstance(entry, dict):
            activity, at = _num(_first(entry, "activity", "mouth", "value")), _num(entry.get("t"))
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            at, activity = _num(entry[0]), _num(entry[1])
        else:
            activity, at = _num(entry), None
        if activity is not None and (at is None or abs(at - t) <= TRUTH_WINDOW_S):
            return activity
    return None


def _truth_from_snapshot(snap, t: float) -> float | None:
    """The bearing of the ONE engaged face whose mouth moves at `t`, read from
    `focus.snapshot()`; None when that is not exactly the situation.

    app/focus.py's own view (read 9 Oct): snapshot["engaged"] = [{track,
    bearing, px, yaw, name}] -- already engaged by focus's rule, now -- and
    snapshot["mouth"] = {track: latest activity}. The latest value is fresh
    for an engaged face (active_speaker runs on every visible face at
    ASD_FPS); mouth entries of tracks NOT engaged are ignored here, because
    the table keeps a departed track's last value with no time on it.
    Also read, for a producer with timestamps: snapshot["faces"] = [{track,
    t, bearing_deg, width_px, yaw_deg, engaged?, mouth? / mouth_t?}] with
    the FOCUS_* engagement rule, and mouth as {t, activity} or [t, activity].
    Strict on purpose: a second engaged face, or another face's mouth
    moving, means the voice may be theirs."""
    if not isinstance(snap, dict):
        return None
    mouths = _first(snap, "mouth", "mouths")
    engaged_view = snap.get("engaged")
    if isinstance(engaged_view, list):
        faces = [dict(f, engaged=True) for f in engaged_view if isinstance(f, dict)]
    else:
        faces = snap.get("faces")
        if not isinstance(faces, list):
            return None
    engaged, others_talking = [], 0
    for face in faces:
        if dataclasses.is_dataclass(face) and not isinstance(face, type):
            face = dataclasses.asdict(face)
        if not isinstance(face, dict):
            continue
        seen = _num(_first(face, "t", "seen_t", "last_t"))
        if seen is not None and abs(seen - t) > max(settings.focus_hold_s, TRUTH_WINDOW_S):
            continue
        bearing = _num(_first(face, "bearing_deg", "bearing"))
        mouth = _mouth_of(face, mouths, t)
        flag = face.get("engaged")
        if isinstance(flag, bool):
            is_engaged = flag
        else:
            width = _num(_first(face, "width_px", "width"))
            yaw = _num(_first(face, "yaw_deg", "yaw"))
            is_engaged = (width is not None and width >= settings.focus_face_min_px
                          and (yaw is None or abs(yaw) <= settings.focus_yaw_max_deg))
        if is_engaged and bearing is not None:
            engaged.append((bearing, mouth))
        elif mouth is not None and mouth > MOUTH_OTHER_MAX:
            others_talking += 1
    if len(engaged) != 1 or others_talking:
        return None
    bearing, mouth = engaged[0]
    if mouth is None or mouth < MOUTH_MIN:
        return None
    return bearing


def _truth_at(t: float) -> float | None:
    for at, bearing in reversed(_PUSHED):
        if abs(at - t) <= TRUTH_WINDOW_S:
            return bearing
    focus = _focus()
    if focus is None:
        return None
    try:
        return _truth_from_snapshot(focus.snapshot(), t)
    except Exception:
        _once("snapshot", "focus.snapshot() failed — DOA calibration waits", exc_info=True)
        return None


# ==================== the module state ====================

_LOCK = threading.Lock()
_WARNED: set[str] = set()
_FOCUS = None
_FOCUS_TRIED = False


def _once(key: str, message: str, *args, exc_info: bool = False) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message, *args, exc_info=exc_info)


def _focus():
    """app.focus, or None while it does not exist (it is built in parallel;
    DOA must not be the thing that breaks a server without it)."""
    global _FOCUS, _FOCUS_TRIED
    if not _FOCUS_TRIED:
        _FOCUS_TRIED = True
        try:
            from app import focus as module
            _FOCUS = module
        except Exception:
            _FOCUS = None
            _once("focus", "app.focus is not importable — DOA is computed and logged but not used")
    return _FOCUS


def _record(event: str, **fields) -> None:
    try:
        from app import turnlog

        turnlog.record(event, **fields)
    except Exception:
        logger.debug("turnlog failed for %s", event, exc_info=True)


class _State:
    def __init__(self) -> None:
        self.link: _Link | None = None
        self.calibration: Calibration | None = None
        self.speaking_until = 0.0
        self.pair_last_t = -1e9
        self.truth_try_t = -1e9
        self.pair_log_last = -1e9
        self.save_last = 0.0
        self.last: dict = {}


_STATE = _State()


def _calibration_for(fingerprint: dict) -> Calibration:
    cal = _STATE.calibration
    if cal is None or not fingerprint_matches(cal.fingerprint, fingerprint):
        cal = _load(fingerprint)
        _STATE.calibration = cal
    elif fingerprint.get("device") and not cal.fingerprint.get("device"):
        cal.fingerprint["device"] = fingerprint["device"]
        cal.dirty = True
    return cal


def _robot_speaking(t: float, now: float) -> bool:
    try:
        from app import display

        remaining = display.remaining_lead()
    except Exception:
        remaining = 0.0
    if remaining > 0:
        _STATE.speaking_until = max(_STATE.speaking_until, now + remaining)
    return t <= _STATE.speaking_until + SPEAKING_TAIL_S


def estimate(cal: Calibration | None, t: float, lags, strength, *, speaking: bool = False,
             learn: bool = True) -> tuple[float | None, float, dict]:
    """One frame -> (bearing | None, confidence, why). Pure apart from the
    calibration it may teach. `speaking` = the robot's own voice overlaps."""
    fitted, closure = consistent(lags, strength)
    mean_strength = float(np.mean(np.clip(np.asarray(strength, float), 0.0, 1.0)))
    q_strength = min(1.0, max(0.0, (mean_strength - 0.2) / 0.4))
    q_closure = math.exp(-(closure / CLOSURE_SCALE) ** 2)
    info = {"closure": round(closure, 3), "strength": round(mean_strength, 3)}
    bearing, conf = None, 0.0
    if speaking:
        info["why"] = "robot_speaking"
    elif q_strength * q_closure < MIN_SOURCE_QUALITY:
        info["why"] = "no_single_source"
    elif cal is None or not cal.ready:
        info["why"] = "uncalibrated"
    else:
        s_hat, resid = cal.predict(fitted)
        fit_conf, parts = cal.confidence(s_hat, resid)
        conf = max(0.0, min(1.0, fit_conf * q_strength * q_closure))
        bearing = math.degrees(math.asin(max(-1.0, min(1.0, s_hat))))
        info.update(why="ok", resid=round(resid, 3), s=round(s_hat, 3),
                    **{k: round(v, 3) for k, v in parts.items()})
    if (learn and cal is not None and not speaking and mean_strength >= PAIR_MIN_STRENGTH
            and closure <= PAIR_MAX_CLOSURE and t - _STATE.pair_last_t >= PAIR_EVERY_S
            and t - _STATE.truth_try_t >= TRUTH_TRY_EVERY_S):
        _STATE.truth_try_t = t
        truth = _truth_at(t)
        if truth is not None:
            _STATE.pair_last_t = t
            was_ready = cal.ready
            verdict = cal.add(fitted, truth, weight=mean_strength ** 2)
            info["pair"] = verdict
            if t - _STATE.pair_log_last >= PAIR_LOG_EVERY_S and verdict == "added":
                _STATE.pair_log_last = t
                _record("doa_pair", lags=[round(float(x), 3) for x in fitted],
                        bearing=round(truth, 1), w=round(mean_strength ** 2, 3))
            if verdict == "reset":
                logger.warning("doa calibration: most recent pairs disagree with the faces — "
                               "starting over (array moved or head turned?)")
                _record("doa_calibration", change="reset")
            elif cal.ready != was_ready:
                logger.info("doa calibration %s: %d pairs, spread %.1f°, sigma %.3f samples, ident %.1f",
                            "READY" if cal.ready else "not ready", len(cal.pairs), cal.spread_deg,
                            cal.sigma, cal.ident)
                _record("doa_calibration", change="ready" if cal.ready else "unready", n=len(cal.pairs),
                        spread_deg=round(cal.spread_deg, 1), sigma=round(cal.sigma, 3),
                        ident=round(cal.ident, 2))
    return bearing, conf, info


def _observe(t: float, bearing: float | None, conf: float) -> None:
    focus = _focus()
    if focus is None:
        return
    try:
        focus.observe_doa(t, bearing, conf)
    except Exception:
        _once("observe", "focus.observe_doa failed — DOA keeps running", exc_info=True)


def _save_soon(force: bool = False) -> None:
    cal = _STATE.calibration
    if cal is None or not cal.dirty:
        return
    now = time.monotonic()
    if not force and now - _STATE.save_last < SAVE_EVERY_S:
        return
    _STATE.save_last = now
    payload = cal.to_json()          # built here, on the loop; written off it
    cal.dirty = False
    try:
        asyncio.get_running_loop().run_in_executor(None, _write, payload)
    except RuntimeError:
        _write(payload)


# ==================== the link ====================


class _Bucket:
    def __init__(self, burst: float, per_s: float) -> None:
        self.burst, self.per_s = burst, per_s
        self.level, self.at = burst, None

    def take(self, now: float) -> bool:
        if self.at is not None:
            self.level = min(self.burst, self.level + (now - self.at) * self.per_s)
        self.at = now
        if self.level >= 1.0:
            self.level -= 1.0
            return True
        return False


def _finite_list(value, n: int, bound: float) -> list[float] | None:
    if not isinstance(value, list) or len(value) != n:
        return None
    out = []
    for x in value:
        x = _num(x)
        if x is None or abs(x) > bound:
            return None
        out.append(x)
    return out


def _valid_frame(event: dict) -> tuple[float, list, list, float] | None:
    t_app = _num(event.get("t_app_ms"))
    lags = _finite_list(event.get("lags"), len(PAIRS), MAX_LAG * 4)
    strength = _finite_list(event.get("strength"), len(PAIRS), 1.01)
    rms = _num(event.get("rms"))
    if t_app is None or t_app < 0 or lags is None or strength is None or rms is None or not 0 <= rms <= 2:
        return None
    return t_app, lags, strength, rms


def _clean_text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return "".join(ch for ch in value if ch.isprintable())[:limit]


def _clean_status(event: dict) -> dict | None:
    state = event.get("state")
    if state not in STATES:
        return None
    out = {"state": state}
    source = event.get("source")
    out["source"] = source if source in SOURCES else None
    out["device"] = _clean_text(event.get("device"), 80)
    out["detail"] = _clean_text(event.get("detail"), 160)
    rms8 = _finite_list(event.get("rms8"), 8, 2.0)
    out["rms8"] = None if rms8 is None else [round(x, 5) for x in rms8]
    for key in ("others", "zero_ms", "sent", "gated", "rate", "channels"):
        value = event.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    for key in ("others_silenced", "self_silenced"):
        value = event.get(key)
        out[key] = value if isinstance(value, bool) else None
    beam = event.get("beam")
    if isinstance(beam, dict):
        # ARRAY_AUDIO's counters (DoaMath.BEAM_FIELDS): integers only.
        out["beam"] = {k: v for k, v in beam.items()
                       if k in BEAM_FIELDS and isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 53}
    return out


#: The app's beam counters in a status (DoaMath.BEAM_FIELDS).
BEAM_FIELDS = ("mode", "gen", "sent", "dropped", "hops", "mvdr_hops", "quiet", "other", "target", "held",
               "guard_trips", "raw8_sent")


class _Link:
    """One app connection's state. Everything here runs on the event loop."""

    def __init__(self, agent: str) -> None:
        self.agent = agent[:120]
        self.clock = ClockMap()
        self.frames = _Bucket(FRAME_BURST, FRAME_REFILL_PER_S)
        self.statuses = _Bucket(STATUS_BURST, STATUS_REFILL_PER_S)
        self.last_heard = time.monotonic()
        self.status: dict = {}
        self.device: str | None = None
        self.counts = {"frames": 0, "unknown": 0, "dropped_rate": 0, "malformed": 0, "early": 0,
                       "oversize": 0}
        self.conf_sum = 0.0
        self.closure_sum = 0.0
        self.summary_at = time.monotonic()
        self.stats_log_at = 0.0
        self.drop_log_at = -1e9

    def handle(self, event: dict) -> None:
        """One parsed message from the app. Never raises."""
        try:
            mtype = event.get("type")
            now = time.monotonic()
            if mtype == "doa":
                self._frame(event, now)
            elif mtype == "doa_status":
                if self.statuses.take(now):
                    self._status(event, now)
        except Exception:
            _once("handle", "doa frame could not be handled", exc_info=True)

    def _frame(self, event: dict, now: float) -> None:
        if not self.frames.take(now):
            self.counts["dropped_rate"] += 1
            if now - self.drop_log_at >= 60.0:
                self.drop_log_at = now
                logger.warning("doa frames over the rate limit dropped (%d so far)", self.counts["dropped_rate"])
            return
        frame = _valid_frame(event)
        if frame is None:
            self.counts["malformed"] += 1
            return
        if self.device is None:
            # Frames before the app said which device it captures from: the
            # calibration is per device (another array = another lag space).
            self.counts["early"] += 1
            return
        t_app, lags, strength, rms = frame
        t = self.clock.convert(t_app, now)
        cal = _calibration_for({"rate": RATE, "channels": list(CHANNELS), "device": self.device})
        bearing, conf, info = estimate(cal, t, lags, strength, speaking=_robot_speaking(t, now))
        _observe(t, bearing, conf)
        self.counts["frames"] += 1
        if bearing is None:
            self.counts["unknown"] += 1
        self.conf_sum += conf
        self.closure_sum += info.get("closure", 0.0)
        _STATE.last = {"t": round(t, 3), "bearing": None if bearing is None else round(bearing, 1),
                       "conf": round(conf, 3), "rms": round(rms, 5), **info}
        _save_soon()
        if now - self.summary_at >= SUMMARY_EVERY_S:
            self._summary(now)

    def _summary(self, now: float) -> None:
        n = self.counts["frames"]
        if n:
            _record("doa_summary", frames=n, unknown=self.counts["unknown"],
                    mean_conf=round(self.conf_sum / n, 3), mean_closure=round(self.closure_sum / n, 3),
                    dropped=self.counts["dropped_rate"], malformed=self.counts["malformed"],
                    calibrated=bool(_STATE.calibration and _STATE.calibration.ready))
        self.summary_at = now
        for key in ("frames", "unknown"):
            self.counts[key] = 0
        self.conf_sum = self.closure_sum = 0.0

    def _status(self, event: dict, now: float) -> None:
        status = _clean_status(event)
        if status is None:
            self.counts["malformed"] += 1
            return
        changed = status["state"] != self.status.get("state") or any(
            status.get(k) != self.status.get(k)
            for k in ("source", "device", "detail", "others", "others_silenced", "self_silenced"))
        self.status = status
        if status["state"] == "capturing" and status.get("device") is not None:
            self.device = status["device"] or "unknown"
        elif status["state"] != "capturing":
            self.device = None
        if changed:
            logger.info("doa app: state=%s source=%s device=%s rate=%s ch=%s others=%s "
                        "others_silenced=%s self_silenced=%s detail=%s",
                        status["state"], status["source"], status["device"], status["rate"],
                        status["channels"], status["others"], status["others_silenced"],
                        status["self_silenced"], status["detail"])
            _record("doa_link", state="app_" + status["state"], source=status["source"],
                    device=status["device"], detail=status["detail"], others=status["others"],
                    others_silenced=status["others_silenced"], self_silenced=status["self_silenced"],
                    rate=status["rate"], channels=status["channels"])
        if now - self.stats_log_at >= STATS_LOG_EVERY_S:
            self.stats_log_at = now
            logger.info("doa stats: rms8=%s zero_ms=%s sent=%s gated=%s calibrated=%s pairs=%d",
                        status["rms8"], status["zero_ms"], status["sent"], status["gated"],
                        bool(_STATE.calibration and _STATE.calibration.ready),
                        len(_STATE.calibration.pairs) if _STATE.calibration else 0)


async def _refuse(websocket, code: str) -> None:
    await websocket.send_text(json.dumps({"type": "error", "code": code}))
    await websocket.close()


async def _await_hello(websocket) -> bool:
    """With ROBOT_FACE_TOKEN set: the app's first message must carry it."""
    import secrets

    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=HELLO_TIMEOUT_S)
    except asyncio.TimeoutError:
        return False
    text = message.get("text")
    if not isinstance(text, str) or len(text) > MAX_TEXT_BYTES:
        return False
    try:
        event = json.loads(text)
    except ValueError:
        return False
    token = event.get("token") if isinstance(event, dict) and event.get("type") == "doa_hello" else None
    return isinstance(token, str) and secrets.compare_digest(
        token.encode("utf-8"), settings.robot_face_token.encode("utf-8"))


async def serve_link(websocket) -> None:
    """/ws/robot-doa, called by main.py AFTER `_reject_unauthorized` (the
    WS_TOKEN gate). The same rules as `robot_face_link.serve_status_link`:

    * Only a user agent with `EmmaRobot/`; anything else gets
      `error/not_robot` and the app stops for good on it (as on unauthorized).
    * DOA_ENABLED off: `doa_off`, then close -- the app does not open the
      array and asks again much later. On: `doa_ready` with the capture
      parameters; the app resets its backoff on that frame only.
    * One array at a time: a second robot app gets `error/busy` (there are
      two ZC-3588A with this APK; two lag streams interleaved are noise).
    * In: text only, size-checked before parsing, token-bucket rate limits;
      `_Link.handle` validates every field. Out: ready, then beats.
    """
    from starlette.websockets import WebSocketDisconnect

    await websocket.accept()
    agent = websocket.headers.get("user-agent", "")
    if "EmmaRobot/" not in agent:
        await _refuse(websocket, "not_robot")
        return
    if not settings.doa_enabled:
        await websocket.send_text(json.dumps({"type": "doa_off"}))
        await websocket.close()
        return
    link = _Link(agent)
    with _LOCK:
        busy = _STATE.link is not None
        if not busy:
            _STATE.link = link
    if busy:
        # the app backs off 1, 2, 4 ... 60 s: usually its own previous link
        # had not closed yet; a second robot asks once a minute -- say so once
        _once("busy", "second robot-doa link refused (one array at a time): %s", agent[:120])
        await _refuse(websocket, "busy")
        return
    array_on = False
    try:
        if settings.robot_face_token and not await _await_hello(websocket):
            logger.warning("robot-doa link without the right face token — refused")
            await _refuse(websocket, "bad_face_token")
            return
        ready = ready_frame()
        array_on = "array" in ready or "capture8" in ready
        await websocket.send_text(json.dumps(ready))
        logger.info("robot-doa link open (%s)", link.agent)
        _record("doa_link", state="open")
        if array_on:
            from app import array_audio

            array_audio.link_opened(link)

        async def receive_loop() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                text = message.get("text")
                if not isinstance(text, str):
                    data = message.get("bytes")
                    # Binary is the array's (beam audio, the raw 8-channel
                    # test) and only when this server asked for it in
                    # doa_ready; otherwise it means nothing here, as before.
                    if isinstance(data, (bytes, bytearray)) and array_on:
                        from app import array_audio

                        if array_audio.receive(link, data, time.monotonic()):
                            link.last_heard = time.monotonic()
                    continue
                size = len(text)
                if size <= MAX_TEXT_BYTES:
                    size = len(text.encode("utf-8"))
                if size > MAX_TEXT_BYTES:
                    link.counts["oversize"] += 1
                    _once("oversize", "robot-doa message of %d+ bytes dropped (limit %d)",
                          size, MAX_TEXT_BYTES)
                    continue
                link.last_heard = time.monotonic()
                try:
                    event = json.loads(text)
                except ValueError:
                    link.counts["malformed"] += 1
                    continue
                if isinstance(event, dict):
                    link.handle(event)

        async def beat_loop() -> None:
            beat = json.dumps({"type": "doa_beat"})
            while True:
                await asyncio.sleep(BEAT_S)
                if time.monotonic() - link.last_heard > SILENT_S:
                    logger.warning("robot-doa link silent for %.0f s — closed", SILENT_S)
                    await websocket.close()
                    return
                await websocket.send_text(beat)

        tasks = [asyncio.ensure_future(receive_loop()), asyncio.ensure_future(beat_loop())]
        if "array" in ready:
            from app import array_audio

            # The beam's target, down the same socket: the focus target's
            # arrival times from this calibration (array_audio.steer_loop).
            tasks.append(asyncio.ensure_future(array_audio.steer_loop(websocket.send_text)))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is not None \
                        and not isinstance(task.exception(), (WebSocketDisconnect, RuntimeError)):
                    logger.warning("robot-doa link ended: %r", task.exception())
        finally:
            for task in tasks:
                task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        return
    finally:
        with _LOCK:
            if _STATE.link is link:
                _STATE.link = None
        if settings.array_audio or settings.array_capture8:
            from app import array_audio

            array_audio.link_closed(link)
        link._summary(time.monotonic())
        _save_soon(force=True)
        logger.info("robot-doa link closed")
        _record("doa_link", state="closed", malformed=link.counts["malformed"] or None,
                dropped=link.counts["dropped_rate"] or None, oversize=link.counts["oversize"] or None)


def snapshot() -> dict:
    """Debug view: numbers only."""
    cal = _STATE.calibration
    link = _STATE.link
    return {
        "enabled": settings.doa_enabled,
        "link": None if link is None else {"status": link.status, "counts": dict(link.counts)},
        "calibration": None if cal is None else {
            "ready": cal.ready, "pairs": len(cal.pairs), "spread_deg": round(cal.spread_deg, 1),
            "sigma": round(cal.sigma, 3), "ident": round(cal.ident, 2), "fingerprint": cal.fingerprint},
        "last": dict(_STATE.last),
    }


def reset_state() -> None:
    """Test isolation."""
    global _STATE, _FOCUS, _FOCUS_TRIED
    _STATE = _State()
    _PUSHED.clear()
    _WARNED.clear()
    _FOCUS, _FOCUS_TRIED = None, False
