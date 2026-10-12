"""The robot's mic array as the conversation's ears (ARRAY_AUDIO).

Owner, 9 Oct 2026: "คุยได้แบบไม่ติดขัดแม้จะมีคนเยอะ". What reception robots
put in front of their speech model is a voice front end on a mic array:
a beam toward the talker, chosen by who the camera sees talking. This robot
already had the pieces -- the 8-channel Bothlent array opened natively by
the app (ArrayListener.java), a direction model learned from faces
(app/doa.py), and an arbiter that knows who is engaged (app/focus.py) -- and
the conversation still came from the WebView's stereo getUserMedia.

Who does what:

* The app (ArrayBeam.java) runs a beam on channels 0-3 -- delay-and-sum by
  default, MVDR with ARRAY_AUDIO_BEAM=mvdr -- steered by the ARRIVAL TIMES
  this module sends down /ws/robot-doa (`array_steer`), and sends the mono
  16 kHz result up the same socket as binary frames (16 ms each).
* This module picks the target (the engaged face that last spoke, else the
  nearest engaged face, else none), turns its bearing into arrival times with
  the DOA calibration (`Calibration.arrivals`), keeps the incoming audio in a
  small buffer, and decides per page frame, for the robot face's session
  (`Tap.frame`), whether the array's audio goes to the model instead.

The rules the tap keeps (each one is a bug this project paid for once):

* **The page decides WHEN, the array decides WHAT.** The array's audio only
  ever goes up as the replacement of a page frame that arrived -- so the
  page's half-duplex, its 350 ms echo guard, the mute button and the
  summoned-greeting hold all apply to the array unchanged. Array audio that
  piles up while the page is silent is thrown away at the next frame.
* **The array has no echo canceller.** Independently of the page, nothing
  from the array is used while the server's own playback clock (reply bytes
  sent to the page + the page's audio lead) says Emma may be audible, plus
  ARRAY_AUDIO_ECHO_TAIL_S; those page frames go up as they are (the page's
  own gate and AEC already judged them), and the buffer is dropped. In
  half-duplex no frame arrives while she speaks, and the page resumes after
  ITS playback (which runs Android's output latency behind the server's
  clock) plus its 350 ms guard -- so the tail is counted again from that
  resume: page frames for ARRAY_AUDIO_ECHO_TAIL_S, then the array at the
  next boundary (`Tap.frame`, review 9 Oct).
* **Fallback is instant, coming back is not.** A stream older than
  ARRAY_AUDIO_STALE_MS (or digital zeros, or a link that is not capturing)
  gives the very next page frame back to the page: the hole is the stale
  time plus at most a page frame (~140 ms at the default 120 -- why 120 and
  not 60: config.py). Switching back to the array waits for a live run of
  RECOVER_S and then for a boundary -- a page gap (nothing was going up) or
  no speech segment open -- so two microphones are never spliced inside one
  utterance, and the same moment is never sent twice.
* One source per page frame, never both: no double audio by construction.

Off (the default), none of this exists: doa_ready is unchanged, no task, no
tap, the page's bytes go up as the same object (tests/test_array_audio.py).

Measured (scripts/eval_array_beam.py: the app's own ArrayBeam.java on
synthetic 4-mic rooms, 2 seeds; steering from a calibration taught by faces;
gain over microphone 0):

* DS, the default: +0.1..+0.4 dB SIR, +1.0..+1.4 dB SNR, the target within
  0.05 dB on every room, geometry and steering error. A ~6 cm array has no
  directivity to speak of at speech frequencies; DS is the beam that cannot
  hurt, not the one that separates talkers.
* MVDR (ARRAY_AUDIO_BEAM=mvdr): on the circle +5.7..+9.0 dB SIR and +2.3..
  +2.9 dB SNR, target +0.1..+0.2 dB, and it holds at 15 degrees of steering
  error (target -0.5..-0.7 dB). On the line +1.2..+1.8 dB SIR in reverberant
  rooms (+8.9 anechoic), but mis-steered by 8-15 degrees the target loses
  1.0-2.6 dB (LSD up to 3.9) -- the hops of a mis-steered target read as
  "another talker" and the guard cannot see them. Which array the robot has
  is unknown; hence DS by default and an A/B on the robot.
* The unaligned plain average ("none"): LSD 10-14 dB -- a comb filter on
  the voice. ARRAY_AUDIO_UNSTEERED=mic0 is the default for that reason.
* CPU (one core of the server PC, JIT warm, 30 s): DS 0.005x real time,
  MVDR 0.010x. An RK3588 big core is roughly 3x slower: ~1.5 % / ~3 % of
  one core; the little cores ~4 % / ~8 %.

On the robot (needs DOA_ENABLED; the APK with ArrayBeam):
0. BLOCKER, measured 10 Oct: the Bothlent array gives Android ONE capture
   stream. App capture first = the page's mic reads exact zeros for the
   whole call (so "fallback to the page" is a deaf call); page first = the
   app gets 2 of 8 channels. The plan below assumed both can capture at
   once -- they cannot. DOA_ENABLED is off in robot.env until the app owns
   the array alone.
1. Server .env: DOA_ENABLED=true ARRAY_AUDIO=true (ARRAY_AUDIO_BEAM=ds),
   restart. The operator screen's DOA line shows "เสียงคุยจากไมค์ 8 ช่อง
   (ds) ส่ง N" rising; the server log shows "call audio source: array (gap)"
   after Emma's first reply, and an `array_report` row every 5 s.
2. Talk to her from the front with two people chatting at the side. Read
   `array_report`: source=array, fallback ~0, lost/stale ~0, and
   array_vs_page_db -- set ARRAY_AUDIO_GAIN from it if the VAD misses quiet
   speech (the floor's scale changed with the source).
3. Same with ARRAY_AUDIO_BEAM=mvdr; compare `heard` accuracy and how often
   the side conversation opened a turn (focus_open/vad rows). guard_trips in
   array_report rising = MVDR fighting a mis-steered target: back to ds.
4. Unplug the array (or `adb shell am broadcast -a com.emma.robot.control.DOA
   --es do off`) mid-sentence: "call audio source: page (stale)" at once,
   the conversation carries on.
5. Channels 4-7: server .env ARRAY_CAPTURE8=true (+ the link as above),
   restart; on the robot, while someone talks 1 m in front:
   `adb shell am broadcast -a com.emma.robot.control.DOA --es do capture8 --ei seconds 8`
   -- talk ~3 s, make Emma speak ~3 s, then ~2 s of silence. Then on the
   server: `python scripts/array_channels_report.py --delete`. Turn
   ARRAY_CAPTURE8 off again; the file is deleted after
   ARRAY_CAPTURE8_KEEP_MIN (10) minutes regardless.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.array_audio")

# ---- the wire (ArrayBeam.java writes it) ----
MAGIC = 0xEA
KIND_BEAM = 1
KIND_RAW8 = 2
HEADER = 8
RATE = 16000
#: ArrayBeam.HOP: one beam chunk is 16 ms.
CHUNK_SAMPLES = 256
#: Biggest beam payload accepted (64 ms) and raw 8-channel block (2048 frames).
MAX_BEAM_PAYLOAD = 2048
MAX_RAW8_PAYLOAD = 2048 * 8 * 2
MAX_BINARY = HEADER + MAX_RAW8_PAYLOAD
#: Token buckets in bytes: the stream's own rate +25 %, half a second of burst.
BEAM_REFILL = RATE * 2 * 1.25
BEAM_BURST = RATE * 2 * 0.5
RAW8_REFILL = RATE * 16 * 1.25
RAW8_BURST = RATE * 16 * 0.5

# ---- the tap ----
#: A page frame this long after the previous one = the page stopped sending
#: in between (half-duplex, mute, a hold): a boundary.
PAGE_GAP_S = 0.12
#: The array must deliver this long without a stale gap before a session
#: switches (back) to it.
RECOVER_S = 0.5
#: Joining the array where the page did NOT pause (no speech segment open):
#: the two paths' latencies differ by tens of ms, so the array's newest audio
#: can be OLDER than the page's last frame -- the same moment twice. Nothing
#: goes up for this long first: a short hole in the VAD's pre-roll, never a
#: repeat (safe while the array path is under 80 ms slower than the page's).
SWITCH_SKEW_S = 0.1
#: Peak 0 for this long = digital silence: a muted capture, not a quiet room.
ZERO_LIMIT_S = 0.5
#: Most audio kept waiting for the next page frame (bytes, 1 s).
FIFO_MAX = RATE * 2
REPORT_S = 5.0

# ---- the steering ----
STEER_EVERY_S = 0.1
STEER_MIN_GAP_S = 0.25
STEER_REFRESH_S = 5.0
#: A bearing change smaller than this is not worth a message.
STEER_DEG = 2.0
#: "Last spoke": mouth activity at or above focus.MOUTH_MOVING within this.
SPOKE_HOLD_S = 8.0
#: "Talking now" (MVDR learns no "other talker" hop meanwhile): the target's
#: mouth moved within this (focus's own mouth window).
TALKING_S = 0.6

# ---- the channels 4-7 test ----
CAPTURE8_MAX_S = 10.0
#: A capture that has had no block for this long is over: the app was asked
#: for fewer seconds than CAPTURE8_MAX_S (the documented run is 8). Without
#: this it stayed open -- its tail unflushed, its .json never written, and an
#: open file is the one `sweep` must skip -- until the link closed.
CAPTURE8_IDLE_S = 2.0
#: How often the sweeper looks while any test file is on disk.
SWEEP_EVERY_S = 15.0
CAPTURE8_PREFIX = "ARRAY8-TEST-"
_CAPTURE_NAME = re.compile(r"^ARRAY8-TEST-(\d{8}-\d{6})\.(s16le|json)$")

_LOCK = threading.Lock()
_WARNED: set[str] = set()


def _once(key: str, message: str, *args) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        logger.warning(message, *args)


def _record(event: str, **fields) -> None:
    try:
        from app import turnlog

        turnlog.record(event, **fields)
    except Exception:
        logger.debug("turnlog failed for %s", event, exc_info=True)


def beam_mode() -> str:
    mode = settings.array_audio_beam
    if mode not in ("ds", "mvdr"):
        _once("mode", "ARRAY_AUDIO_BEAM=%r is not ds or mvdr — using ds", mode)
        return "ds"
    return mode


def unsteered_weights() -> list[float]:
    if settings.array_audio_unsteered == "average":
        return [0.25, 0.25, 0.25, 0.25]
    if settings.array_audio_unsteered != "mic0":
        _once("unsteered", "ARRAY_AUDIO_UNSTEERED=%r is not mic0 or average — using mic0",
              settings.array_audio_unsteered)
    return [1.0, 0.0, 0.0, 0.0]


def ready_fields() -> dict:
    """What doa_ready carries for the array (only the switches that are on)."""
    out: dict = {}
    if settings.array_audio:
        out["array"] = {"beam": beam_mode(), "chunk": CHUNK_SAMPLES, "rate": RATE}
    if settings.array_capture8:
        out["capture8"] = {"max_s": CAPTURE8_MAX_S}
    return out


# ==================== the stream (the link writes, a session reads) ====================


class _Bucket:
    def __init__(self, burst: float, per_s: float) -> None:
        self.burst, self.per_s = burst, per_s
        self.level, self.at = burst, None

    def take(self, now: float, amount: float) -> bool:
        if self.at is not None:
            self.level = min(self.burst, self.level + (now - self.at) * self.per_s)
        self.at = now
        if self.level >= amount:
            self.level -= amount
            return True
        return False


class _Stream:
    def __init__(self) -> None:
        self.link = None
        self.fifo = bytearray()
        self.last_arrival: float | None = None
        self.run_start: float | None = None
        self.zero_since: float | None = None
        self.next_seq: int | None = None
        self.beam_bucket = _Bucket(BEAM_BURST, BEAM_REFILL)
        self.raw8_bucket = _Bucket(RAW8_BURST, RAW8_REFILL)
        self.counts = {"chunks": 0, "bytes": 0, "lost": 0, "malformed": 0, "dropped_rate": 0,
                       "oversize": 0, "refused": 0, "raw8": 0, "stale": 0}
        # rms of what arrived, for the report (sum of squares, samples)
        self.arrived_ss = 0.0
        self.arrived_n = 0
        self.capture: _Capture | None = None
        self.steer: dict = {}
        #: Longest wait between two beam chunks since the last written
        #: report, as the server saw it (network + the event loop). The
        #: evidence ARRAY_AUDIO_STALE_MS is tuned from (array_report
        #: `max_gap_ms`), never from theory.
        self.max_gap = 0.0


_STREAM = _Stream()


def link_opened(link) -> None:
    with _LOCK:
        _STREAM.link = link
        _STREAM.fifo.clear()
        _STREAM.last_arrival = _STREAM.run_start = _STREAM.zero_since = None
        _STREAM.next_seq = None
    sweep()


def link_closed(link) -> None:
    with _LOCK:
        if _STREAM.link is link:
            _STREAM.link = None
            _STREAM.fifo.clear()
            _STREAM.last_arrival = _STREAM.run_start = None
        capture, _STREAM.capture = _STREAM.capture, None
    if capture is not None:
        capture.close()
    sweep()


def receive(link, data: bytes, now: float) -> bool:
    """One binary message from the app. True = it was a valid frame (the
    link's silence clock may reset). Never raises."""
    try:
        return _receive(link, bytes(data), now)
    except Exception:
        _once("receive", "array frame could not be handled")
        logger.debug("array frame failed", exc_info=True)
        return False


def _receive(link, data: bytes, now: float) -> bool:
    s = _STREAM
    if len(data) > MAX_BINARY:
        s.counts["oversize"] += 1
        _once("oversize", "array frame of %d bytes dropped (limit %d)", len(data), MAX_BINARY)
        return False
    if len(data) < HEADER + 2 or data[0] != MAGIC or (len(data) - HEADER) % 2:
        s.counts["malformed"] += 1
        return False
    kind = data[1]
    seq = data[2] | (data[3] << 8)
    payload = data[HEADER:]
    if kind == KIND_BEAM:
        if not settings.array_audio or len(payload) > MAX_BEAM_PAYLOAD:
            s.counts["refused" if not settings.array_audio else "oversize"] += 1
            return False
        if not s.beam_bucket.take(now, len(data)):
            s.counts["dropped_rate"] += 1
            return False
        _beam(link, seq, payload, now)
        return True
    if kind == KIND_RAW8:
        if not settings.array_capture8 or len(payload) % 16:
            s.counts["refused" if not settings.array_capture8 else "malformed"] += 1
            if not settings.array_capture8:
                _once("raw8_off", "raw 8-channel audio from the robot refused — ARRAY_CAPTURE8 is off")
            return False
        if not s.raw8_bucket.take(now, len(data)):
            s.counts["dropped_rate"] += 1
            return False
        _raw8(link, seq, payload)
        return True
    s.counts["malformed"] += 1
    return False


def _beam(link, seq: int, payload: bytes, now: float) -> None:
    s = _STREAM
    samples = np.frombuffer(payload, dtype="<i2")
    peak = int(np.abs(samples.astype(np.int32)).max()) if len(samples) else 0
    with _LOCK:
        if s.link is not link:
            return
        if s.next_seq is not None and seq != s.next_seq:
            s.counts["lost"] += (seq - s.next_seq) % 65536
        s.next_seq = (seq + 1) % 65536
        stale = settings.array_audio_stale_ms / 1000.0
        if s.last_arrival is not None:
            s.max_gap = max(s.max_gap, now - s.last_arrival)
        if s.last_arrival is None or now - s.last_arrival > stale:
            if s.last_arrival is not None:
                s.counts["stale"] += 1
            s.run_start = now
        s.last_arrival = now
        if peak == 0:
            s.zero_since = now if s.zero_since is None else s.zero_since
        else:
            s.zero_since = None
        s.fifo.extend(payload)
        if len(s.fifo) > FIFO_MAX:
            del s.fifo[:len(s.fifo) - FIFO_MAX]
        s.counts["chunks"] += 1
        s.counts["bytes"] += len(payload)
        x = samples.astype(np.float64) / 32768.0
        s.arrived_ss += float(np.dot(x, x))
        s.arrived_n += len(x)


def live(now: float) -> tuple[bool, str]:
    """Is the array delivering right now? (staying on it)"""
    s = _STREAM
    if not settings.array_audio:
        return False, "off"
    link = s.link
    if link is None:
        return False, "no_link"
    if getattr(link, "status", {}).get("state") != "capturing":
        return False, "not_capturing"
    if s.last_arrival is None or now - s.last_arrival > settings.array_audio_stale_ms / 1000.0:
        return False, "stale"
    if s.zero_since is not None and now - s.zero_since >= ZERO_LIMIT_S:
        return False, "zeros"
    return True, "live"


def recovered(now: float) -> bool:
    """Live for a whole RECOVER_S run (switching (back) to it)."""
    ok, _ = live(now)
    return ok and _STREAM.run_start is not None and now - _STREAM.run_start >= RECOVER_S


def _take(nbytes_align: int | None) -> bytes:
    """Everything waiting; with `nbytes_align`, only that many newest bytes
    (a boundary: what piled up before it belongs to a moment nobody sent)."""
    with _LOCK:
        fifo = _STREAM.fifo
        if nbytes_align is not None and len(fifo) > nbytes_align:
            del fifo[:len(fifo) - nbytes_align]
        out = bytes(fifo)
        fifo.clear()
    return out


def discard() -> None:
    with _LOCK:
        _STREAM.fifo.clear()


def _gain(pcm: bytes) -> bytes:
    g = settings.array_audio_gain
    if not pcm or g == 1.0 or not math.isfinite(g) or g <= 0:
        return pcm
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * min(g, 16.0)
    return np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes()


# ==================== the session's tap ====================


def _rms(pcm: bytes) -> tuple[float, int]:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64) / 32768.0
    return float(np.dot(x, x)), len(x)


class Tap:
    """Per session: which source each page frame becomes. See the module
    docstring for the rules; `frame` is the only entry."""

    def __init__(self) -> None:
        self.mode = "page"
        #: In array mode, Emma was audible a moment ago: page frames go up
        #: until the next boundary (see `_decide`).
        self.held = False
        #: Joining at a no-speech boundary: nothing goes up until then.
        self.skip_until: float | None = None
        self.last_page: float | None = None
        #: After her speech in a page gap: page frames until this (`frame`).
        self.echo_until: float | None = None
        self.counts = {"array": 0, "page": 0, "empty": 0, "switch_in": 0, "fallback": 0, "speaking": 0,
                       "speech": 0}
        #: What the last WRITTEN row saw (`_eventful`): None = no row yet.
        self._reported_mode: str | None = None
        self._reported_health: tuple | None = None
        self.page_ss = 0.0
        self.page_n = 0
        self.sent_ss = 0.0
        self.sent_n = 0
        self.report_at = time.monotonic()
        self.why = "start"

    def _to(self, mode: str, why: str, now: float) -> None:
        if mode == self.mode:
            return
        self.mode, self.why = mode, why
        self.counts["switch_in" if mode == "array" else "fallback"] += 1
        logger.info("call audio source: %s (%s)", mode, why)
        _record("array_source", source=mode, why=why)

    def frame(self, page: bytes, now: float, *, speaking: bool, in_speech: bool | None,
              audible_until: float | None = None) -> bytes:
        prev = self.last_page
        gap = prev is None or now - prev > PAGE_GAP_S
        self.last_page = now
        if gap and audible_until is not None and (prev is None or audible_until > prev):
            # She was audible while the page sent nothing: half-duplex, the
            # default. No frame arrived during her speech, so `speaking`
            # never held anything here, and by the time the page's own echo
            # guard (350 ms after ITS playback ended) lets frames through the
            # server's clock has run out -- that clock starts at send time and
            # knows nothing of Android's audio output latency (~0.1-0.3 s).
            # The page has an echo canceller behind its guard; the array has
            # nothing. So the tail is counted again from the page's resume,
            # page frames go up meanwhile, and the array rejoins only at the
            # next boundary after it -- which is what makes
            # ARRAY_AUDIO_ECHO_TAIL_S do anything in half-duplex at all.
            self.echo_until = now + max(0.0, float(settings.array_audio_echo_tail_s))
        if self.echo_until is not None:
            if now < self.echo_until:
                speaking = True
            else:
                self.echo_until = None
        ss, n = _rms(page)
        self.page_ss += ss
        self.page_n += n
        if in_speech:
            self.counts["speech"] += 1
        out = self._decide(page, now, gap, speaking, in_speech)
        if out is page:
            self.counts["page"] += 1
        elif out:
            self.counts["array"] += 1
            ss, n = _rms(out)
            self.sent_ss += ss
            self.sent_n += n
        else:
            self.counts["empty"] += 1
        if now - self.report_at >= REPORT_S:
            if self._eventful():
                self.report(now)
            else:
                self._quiet_window(now)
        return out

    @staticmethod
    def _health() -> tuple:
        c = _STREAM.counts
        return (c["lost"], c["stale"], c["dropped_rate"], c["malformed"])

    def _eventful(self) -> bool:
        """Is this window worth an `array_report` row?

        A standing line is open all day and the page sends frames all day:
        one row every REPORT_S of an empty room was ~17,000 rows a day of
        zeros (review 10 Oct). A row is written when somebody spoke (a VAD
        segment open), Emma was audible, the source switched, the stream's
        health counters moved, or it is the first window of the call."""
        c = self.counts
        return bool(c["speech"] or c["speaking"] or c["switch_in"] or c["fallback"]
                    or self.mode != self._reported_mode
                    or self._health() != self._reported_health)

    def _quiet_window(self, now: float) -> None:
        """Start the next window without writing a row. `max_gap` carries
        over to the next written row (it is the tuning evidence)."""
        for key in self.counts:
            self.counts[key] = 0
        self.page_ss = self.sent_ss = 0.0
        self.page_n = self.sent_n = 0
        with _LOCK:
            _STREAM.arrived_ss, _STREAM.arrived_n = 0.0, 0
        self.report_at = now

    def _decide(self, page: bytes, now: float, gap: bool, speaking: bool, in_speech) -> bytes:
        # A boundary: the page sent nothing for a while (half-duplex, mute, a
        # hold), or no speech segment is open (only the VAD's pre-roll would
        # see a splice). Two microphones are joined only there.
        boundary = gap or in_speech is False
        if self.skip_until is not None and not speaking:
            if now < self.skip_until and live(now)[0]:
                discard()
                return b""
            self.skip_until = None
            if live(now)[0]:
                return _gain(_take(None))
        if speaking:
            # The array has no AEC: what it heard while she may be audible is
            # her. The page frame goes up as it is (the page judged it).
            discard()
            self.counts["speaking"] += 1
            self.held = True
            self.skip_until = None
            return page
        ok, why = live(now)
        if not ok:
            if self.mode == "array":
                self._to("page", why, now)          # at once: the hole is the stale time
            self.held = False
            self.skip_until = None
            return page
        if self.mode != "array":
            if not recovered(now) or not boundary:
                return page
            self._to("array", "gap" if gap else "no_speech_open", now)
            self.held = False
            return self._join(page, now, gap)
        if self.held:
            if not boundary:
                return page
            self.held = False
            return self._join(page, now, gap)
        return _gain(_take(len(page) if gap else None))

    def _join(self, page: bytes, now: float, gap: bool) -> bytes:
        """Start forwarding the array at a boundary. After a page gap the
        array's newest frame-length is now (nothing was sent meanwhile);
        inside a continuous page stream, wait SWITCH_SKEW_S first."""
        if gap:
            return _gain(_take(len(page)))
        discard()
        self.skip_until = now + SWITCH_SKEW_S
        return b""

    def report(self, now: float | None = None) -> None:
        """Numbers only: which source, both levels over the same moments (the
        floor's scale changes with the source -- CLAUDE.md's vad floor rule),
        the stream's health and the steering."""
        now = time.monotonic() if now is None else now
        s = _STREAM
        page_rms = math.sqrt(self.page_ss / self.page_n) if self.page_n else None
        array_rms = math.sqrt(s.arrived_ss / s.arrived_n) if s.arrived_n else None
        sent_rms = math.sqrt(self.sent_ss / self.sent_n) if self.sent_n else None
        ratio = (round(20 * math.log10(array_rms / page_rms), 1)
                 if page_rms and array_rms and page_rms > 0 and array_rms > 0 else None)
        steer = dict(s.steer)
        row = {"source": self.mode, "array_frames": self.counts["array"], "page_frames": self.counts["page"],
               "empty": self.counts["empty"], "speaking": self.counts["speaking"],
               "switch_in": self.counts["switch_in"], "fallback": self.counts["fallback"],
               "page_rms": None if page_rms is None else round(page_rms, 4),
               "array_rms": None if array_rms is None else round(array_rms, 4),
               "sent_rms": None if sent_rms is None else round(sent_rms, 4),
               "array_vs_page_db": ratio, "lost": s.counts["lost"], "stale": s.counts["stale"],
               "dropped_rate": s.counts["dropped_rate"], "malformed": s.counts["malformed"],
               "beam": steer.get("mode"), "steer": steer.get("why"), "bearing": steer.get("bearing"),
               "gain": settings.array_audio_gain, "max_gap_ms": round(s.max_gap * 1000),
               "stale_ms": settings.array_audio_stale_ms}
        app_beam = (getattr(s.link, "status", None) or {}).get("beam") if s.link is not None else None
        if app_beam:
            # the app's own view: chunks it could not queue, MVDR hops, guard trips
            row.update(app_dropped=app_beam.get("dropped"), mvdr_hops=app_beam.get("mvdr_hops"),
                       guard_trips=app_beam.get("guard_trips"))
        logger.info("array audio: source=%s array=%d page=%d page_rms=%s array_rms=%s (%s dB) "
                    "lost=%d stale=%d steer=%s %s", row["source"], row["array_frames"], row["page_frames"],
                    row["page_rms"], row["array_rms"], ratio, row["lost"], row["stale"], row["steer"],
                    row["bearing"])
        _record("array_report", **row)
        for key in self.counts:
            self.counts[key] = 0
        self.page_ss = self.sent_ss = 0.0
        self.page_n = self.sent_n = 0
        with _LOCK:
            s.arrived_ss, s.arrived_n = 0.0, 0
            s.max_gap = 0.0
        self.report_at = now
        self._reported_mode = self.mode
        self._reported_health = self._health()


def tap_for(session, *, input_rate: int, barge_in: bool, hold_speech: bool) -> Tap | None:
    """The tap for a session, or None (today's path, untouched)."""
    if not settings.array_audio:
        return None
    sweep()
    if not settings.doa_enabled:
        _once("doa", "ARRAY_AUDIO is on but DOA_ENABLED is off — the array audio rides the DOA "
                     "link and steers with its calibration: the page's mic stays the source")
        return None
    if settings.multi_session:
        return None
    agent = getattr(getattr(session, "ws", None), "headers", {}).get("user-agent", "")
    if "EmmaRobot/" not in agent:
        return None                     # only the robot's own face hears with the robot's array
    why = None
    if input_rate != RATE:
        why = "provider input rate %s Hz (the array sends 16 kHz)" % input_rate
    elif barge_in:
        why = "barge-in (ECHO_BARGE) sends audio while Emma speaks; the array has no AEC"
    elif hold_speech:
        why = "hold-speech replays audio from while Emma spoke; the array has no AEC"
    if why:
        logger.info("array audio not used for this call: %s", why)
        _record("array_source", source="page", why=why.split(" ")[0])
        return None
    return Tap()


# ==================== the steering (server -> app) ====================

_SPOKE: dict = {}


def _target(now: float) -> tuple[str, float | None, bool]:
    """(why, bearing, talking): the engaged face that last spoke, else the
    nearest engaged face, else none; talking = its mouth moves right now."""
    from app import doa

    focus = doa._focus()
    if focus is None:
        return "no_focus", None, False
    try:
        snap = focus.snapshot()
    except Exception:
        _once("snapshot", "focus.snapshot() failed — the beam stays unsteered")
        return "no_focus", None, False
    moving = getattr(focus, "MOUTH_MOVING", 0.35)
    for track, entry in (snap.get("mouth") or {}).items():
        if isinstance(entry, dict):
            t, act = entry.get("t"), entry.get("activity")
            if (isinstance(t, (int, float)) and isinstance(act, (int, float))
                    and act >= moving and now - t <= 1.0):
                _SPOKE[track] = max(_SPOKE.get(track, -1e9), float(t))
    for track in [k for k, t in _SPOKE.items() if now - t > 60.0]:
        del _SPOKE[track]
    faces = []
    for face in snap.get("engaged") or []:
        if not isinstance(face, dict):
            continue
        bearing, px = face.get("bearing"), face.get("px")
        if isinstance(bearing, (int, float)) and math.isfinite(bearing) and abs(bearing) <= 90:
            faces.append((face.get("track"), float(bearing), px if isinstance(px, (int, float)) else 0))
    spoke = [(_SPOKE[tr], b) for tr, b, _ in faces if tr in _SPOKE and now - _SPOKE[tr] <= SPOKE_HOLD_S]
    if spoke:
        last, bearing = max(spoke)
        return "spoke", bearing, now - last <= TALKING_S
    if faces:
        return "nearest", max(faces, key=lambda f: f[2])[1], False
    return "none", None, False


def steer_message(now: float | None = None) -> dict:
    """The `array_steer` frame for this moment (without its gen)."""
    from app import doa

    now = time.monotonic() if now is None else now
    why, bearing, talking = _target(now)
    cal = doa._STATE.calibration
    tau = None
    if bearing is not None:
        tau = cal.arrivals(bearing) if cal is not None else None
        if tau is None:
            why = "uncalibrated"
    return {"type": "array_steer", "mode": beam_mode(), "unsteered": unsteered_weights(),
            "sigma": round(float(cal.sigma), 4) if cal is not None and cal.ready else 0.3,
            "tau": tau, "bearing": None if tau is None else round(bearing, 1), "why": why,
            "talking": bool(talking and tau is not None)}


def _differs(a: dict | None, b: dict) -> bool:
    if a is None:
        return True
    if (a["tau"] is None) != (b["tau"] is None) or a["mode"] != b["mode"] or a["talking"] != b["talking"]:
        return True
    if a["tau"] is None:
        return False
    return abs((a["bearing"] or 0.0) - (b["bearing"] or 0.0)) >= STEER_DEG


async def steer_loop(send_text) -> None:
    """The beam's target, down the DOA link, for as long as the link lives:
    on change (STEER_DEG, presence, mode), at most every STEER_MIN_GAP_S,
    and again every STEER_REFRESH_S (an app that restarted its capture)."""
    gen, last, sent_at = 0, None, -1e9
    while True:
        now = time.monotonic()
        msg = steer_message(now)
        if (_differs(last, msg) and now - sent_at >= STEER_MIN_GAP_S) or now - sent_at >= STEER_REFRESH_S:
            gen += 1
            await send_text(json.dumps(dict(msg, gen=gen)))
            if _differs(last, msg):
                _record("array_steer", why=msg["why"], bearing=msg["bearing"], mode=msg["mode"])
            last, sent_at = msg, now
            with _LOCK:
                _STREAM.steer = {"why": msg["why"], "bearing": msg["bearing"], "mode": msg["mode"], "gen": gen}
        await asyncio.sleep(STEER_EVERY_S)


# ==================== the channels 4-7 test (ARRAY_CAPTURE8) ====================


def _capture_dir() -> Path:
    """The server's data dir (tests point this at tmp_path)."""
    return Path(__file__).resolve().parent.parent / "data" / "array_capture8"


class _Capture:
    def __init__(self, link) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        folder = _capture_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{CAPTURE8_PREFIX}{stamp}.s16le"
        self.meta = self.path.with_suffix(".json")
        self.frames = 0
        self.missing = 0
        self.next_seq = 0
        self.device = getattr(link, "device", None)
        self.source = (getattr(link, "status", {}) or {}).get("source")
        self.file = open(self.path, "wb")
        self.full = False
        self.last_at = time.monotonic()
        logger.warning("ARRAY_CAPTURE8: writing the raw 8-channel test to %s — deleted after %.0f min",
                       self.path, settings.array_capture8_keep_min)
        _record("array_capture8", state="start")
        ensure_sweeper()

    def add(self, seq: int, payload: bytes) -> None:
        if self.full:
            return
        if seq != self.next_seq:
            self.missing += (seq - self.next_seq) % 65536
        self.next_seq = (seq + 1) % 65536
        self.file.write(payload)
        self.frames += len(payload) // 16
        self.last_at = time.monotonic()
        if self.frames >= CAPTURE8_MAX_S * RATE:
            self.full = True
            self.close()

    def close(self) -> None:
        if self.file.closed:
            return
        self.file.close()
        self.meta.write_text(json.dumps({
            "rate": RATE, "channels": 8, "format": "s16le interleaved", "frames": self.frames,
            "seconds": round(self.frames / RATE, 2), "missing_chunks": self.missing,
            "device": self.device, "source": self.source,
            "keep_min": settings.array_capture8_keep_min,
            "note": "room audio for the channels 4-7 test (ARRAY_CAPTURE8); deleted automatically",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("ARRAY_CAPTURE8: %s closed (%.1f s, %d chunks missing)", self.path.name,
                    self.frames / RATE, self.missing)
        _record("array_capture8", state="done", seconds=round(self.frames / RATE, 1), missing=self.missing)


def _raw8(link, seq: int, payload: bytes) -> None:
    """A run starts at seq 0 (one adb request on the robot = one file)."""
    s = _STREAM
    if seq == 0:
        old, s.capture = s.capture, None
        if old is not None:
            old.close()
        sweep()
        s.capture = _Capture(link)
    capture = s.capture
    if capture is None or capture.full:
        return                          # the middle of a run this server did not start
    s.counts["raw8"] += 1
    capture.add(seq, payload)


def maintain(now: float | None = None) -> bool:
    """Close a capture whose run ended short (CAPTURE8_IDLE_S without a
    block), then delete what has expired. True while there is still
    something to watch: an open capture or a test file on disk."""
    now = time.monotonic() if now is None else now
    capture = _STREAM.capture
    if capture is not None and not capture.file.closed and now - capture.last_at >= CAPTURE8_IDLE_S:
        capture.close()
    sweep()
    capture = _STREAM.capture
    if capture is not None and not capture.file.closed:
        return True
    folder = _capture_dir()
    return folder.is_dir() and any(_CAPTURE_NAME.match(p.name) for p in folder.iterdir())


_SWEEPER: dict = {"task": None}


def ensure_sweeper() -> None:
    """Keep `maintain` running while there is room audio on disk.

    "Deleted after ARRAY_CAPTURE8_KEEP_MIN" used to depend on something else
    happening -- a link opening or closing, a call starting with ARRAY_AUDIO
    on, the next capture. The documented test ends with ARRAY_CAPTURE8 turned
    off and a restart, after which none of those run here at all, and the
    raw 8 channels of the room stayed on the robot for good. Started by every
    capture and by the server's startup hook (main.py); it ends itself once
    the folder holds nothing. No running loop (a test calling in directly):
    nothing to start."""
    task = _SWEEPER["task"]
    if task is not None and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _SWEEPER["task"] = loop.create_task(_sweeper())


async def _sweeper() -> None:
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            if not maintain():
                return
        except Exception:
            logger.warning("ARRAY_CAPTURE8: the sweep failed", exc_info=True)


def sweep(now_wall: float | None = None) -> int:
    """Delete test captures older than ARRAY_CAPTURE8_KEEP_MIN. The age comes
    from the NAME (CLAUDE.md: a copy, a backup restore or a sync rewrites
    mtime; none of them restarts the clock on room audio)."""
    folder = _capture_dir()
    if not folder.is_dir():
        return 0
    now_wall = time.time() if now_wall is None else now_wall
    keep_s = max(0.0, settings.array_capture8_keep_min) * 60.0
    removed = 0
    for path in folder.iterdir():
        match = _CAPTURE_NAME.match(path.name)
        if not match:
            continue
        try:
            made = time.mktime(time.strptime(match.group(1), "%Y%m%d-%H%M%S"))
        except ValueError:
            continue
        if now_wall - made >= keep_s:
            capture = _STREAM.capture
            if capture is not None and path in (capture.path, capture.meta) and not capture.file.closed:
                continue                # still being written
            try:
                path.unlink()
                removed += 1
            except OSError:
                logger.warning("ARRAY_CAPTURE8: could not delete %s", path)
    if removed:
        logger.info("ARRAY_CAPTURE8: deleted %d expired test file(s)", removed)
    return removed


# ==================== debug / tests ====================


def snapshot() -> dict:
    s = _STREAM
    now = time.monotonic()
    ok, why = live(now)
    return {"enabled": settings.array_audio, "live": ok, "why": why, "recovered": recovered(now),
            "fifo_bytes": len(s.fifo), "counts": dict(s.counts), "steer": dict(s.steer)}


def reset_state() -> None:
    """Test isolation."""
    global _STREAM
    capture = _STREAM.capture
    if capture is not None:
        capture.close()
    _STREAM = _Stream()
    _SPOKE.clear()
    task = _SWEEPER["task"]
    if task is not None and not task.done():
        task.cancel()
    _SWEEPER["task"] = None
    _WARNED.clear()
