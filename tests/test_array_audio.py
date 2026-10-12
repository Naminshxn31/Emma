"""The robot's mic array as the conversation's ears (ARRAY_AUDIO): the app's
beam (ArrayBeam.java, COMPILED and RUN on synthetic multi-talker rooms), the
binary frames and steering on /ws/robot-doa (app/doa.py + app/array_audio.py),
the session's source switch (`Tap`), the channels 4-7 test, and the promise
that with every switch off nothing changed.

The SIR/SNR numbers are measured, not asserted into existence: each
component of a room (target, other talkers, diffuse noise) goes through the
exact filter the mixture got (`ArrayBeam.processWith`), so the gain of each
beam is a ratio of real outputs. The robot's array geometry is unknown --
the circle and the line are the ones tests/test_doa.py teaches with.
"""
from __future__ import annotations

import asyncio
import json
import math
import struct
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from app import array_audio, doa
from app.config import settings
from tests import array_room as R
from tests.array_room import BEAM_HARNESS, LATENCY, ORDER, beam, gains, lsd  # noqa: F401
from tests.array_room import run_java as _run
from tests.test_emma_robot_body_nav_app import SRC, _javac, code_only, method, read

BEAM = SRC / "ArrayBeam.java"
MATH = SRC / "DoaMath.java"
LINK = SRC / "DoaLink.java"
LISTENER = SRC / "ArrayListener.java"
ROBOT_UA = "Mozilla/5.0 (Linux; Android 15) EmmaRobot/0.1 EmmaArray/1"
TOKEN = "array-test-token"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    doa.reset_state()
    array_audio.reset_state()
    monkeypatch.setattr(doa, "_calibration_path", lambda: tmp_path / "doa_calibration.json")
    monkeypatch.setattr(array_audio, "_capture_dir", lambda: tmp_path / "array_capture8")
    yield
    array_audio.reset_state()
    doa.reset_state()


# ==================== the beam, compiled and run ====================

STEER_HARNESS = """package com.emma.robot.control;

public final class SteerHarness {
    static String show(ArrayBeam.Steer s) {
        StringBuilder b = new StringBuilder();
        b.append(s.tau == null ? "tau=null" : "tau=" + java.util.Arrays.toString(s.tau));
        b.append(" lags=").append(s.lags == null ? "null" : java.util.Arrays.toString(s.lags));
        b.append(" w=").append(java.util.Arrays.toString(s.unsteered));
        b.append(" mode=").append(s.mode).append(" talking=").append(s.talking).append(" sigma=").append(s.sigma);
        return b.toString();
    }

    public static void main(String[] a) {
        System.out.println(show(ArrayBeam.Steer.parse(new double[]{0.5, 1.5, -1.0, 2.5}, 0.2,
                new double[]{1, 0, 0, 0}, "mvdr", 3, 16, true)));
        System.out.println(show(ArrayBeam.Steer.parse(new double[]{0, Double.NaN, 0, 0}, 0.2, null, "ds", 4, 16, true)));
        System.out.println(show(ArrayBeam.Steer.parse(new double[]{0, 99, 0, 0}, 0.2, null, "ds", 5, 16, false)));
        System.out.println(show(ArrayBeam.Steer.parse(new double[]{0, 1}, 0.2, new double[]{2, 2, 2, 2}, "x", 6, 16, false)));
        System.out.println(show(ArrayBeam.Steer.parse(null, Double.NaN, new double[]{0.25, 0.25, 0.25, 0.25}, "ds", 7, 16, true)));
        byte[] f = ArrayBeam.pack(ArrayBeam.KIND_BEAM, 0x1234, 0x1_0000_0005L, new short[]{1, -2, 32767}, 3);
        StringBuilder hex = new StringBuilder();
        for (byte b : f) hex.append(String.format("%02x", b & 0xff));
        System.out.println("pack=" + hex);
        double[] lags = {1.0, 2.0, 0.5, 1.0, -0.5, -1.5};
        double[] fit = new double[6];
        double closure = ArrayBeam.consistentInto(lags, new double[]{0.9, 0.8, 0.7, 0.9, 0.6, 0.5}, fit);
        System.out.println("fit=" + java.util.Arrays.toString(fit) + " closure=" + closure);
    }
}
"""

LINK_HARNESS = """package com.emma.robot.control;

public final class ArrayLinkHarness {
    public static void main(String[] a) throws Exception {
        DoaLink link = DoaLink.open(a[0], "EmmaRobot/0.1 EmmaArray/1", 5000, null);
        int frames = Integer.parseInt(a[1]);
        link.sendText("{\\"type\\":\\"doa_hello\\",\\"token\\":\\"\\"}");
        System.out.println("first=" + link.readText());
        link.sendText(DoaMath.statusJson("capturing", "VOICE_RECOGNITION", "Bothlent UAC Dongle", null,
                new double[8], 1, Boolean.FALSE, Boolean.FALSE, 0, 0, 0, 16000, 8,
                new long[]{0, 1, frames, 0, frames, 0, 0, 0, 0, 0, 0, 0}));
        short[] pcm = new short[ArrayBeam.HOP];
        for (int f = 0; f < frames; f++) {
            for (int k = 0; k < pcm.length; k++) pcm[k] = (short) (1000 * Math.sin(0.05 * (f * pcm.length + k)));
            link.sendBinary(ArrayBeam.pack(ArrayBeam.KIND_BEAM, f, 1000L + 16L * f, pcm, pcm.length));
            Thread.sleep(16);
        }
        link.setReadTimeout(5000);
        for (int k = 0; k < 20; k++) {
            String text = link.readText();
            if (text == null) break;
            if (text.contains("array_steer")) {
                System.out.println("steer=" + text);
                ArrayBeam.Steer s = ArrayBeam.Steer.parse(new double[]{0, 0, 0, 0}, 0.2, null, "ds", 0, 16, false);
                System.out.println("parsed_ok=" + (s != null));
                break;
            }
        }
        link.close();
        System.out.println("closed");
    }
}
"""


@pytest.fixture(scope="module")
def jvm(tmp_path_factory):
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: ArrayBeam (the beam, MVDR, the wire packing) and DoaLink.sendBinary "
                    "were NOT compiled or run -- every dB number in this file is unchecked")
    javac, java = tools
    work = tmp_path_factory.mktemp("array_java")
    src = work / "src"
    src.mkdir()
    for name, text in (("BeamHarness.java", BEAM_HARNESS), ("SteerHarness.java", STEER_HARNESS),
                       ("ArrayLinkHarness.java", LINK_HARNESS)):
        (src / name).write_text(text, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(MATH), str(LINK), str(BEAM)]
                   + [str(p) for p in src.glob("*.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=180)
    return {"java": java, "cp": str(out), "work": work}


@pytest.fixture(scope="module")
def rooms():
    """Two seeds per geometry: a target at 10 deg, talkers at 70 and -130 deg
    at the target's level, diffuse noise 10 dB under, RT60 0.5 s."""
    out = {}
    for name, pos in R.GEOMETRIES.items():
        cal = R.taught_calibration(pos, np.random.default_rng(5))
        for seed in (3, 4):
            out[(name, seed)] = (R.room(pos, np.random.default_rng(seed), target_az=10.0, rt60=0.5), cal)
    return out


def test_mic0_is_the_raw_mic_and_an_unaligned_average_colours_the_voice(jvm, rooms):
    """No target: ARRAY_AUDIO_UNSTEERED. mic0 is microphone 0, sample for
    sample (the beam's 16 ms late); the plain average of four UNALIGNED mics
    comb-filters the voice -- the reason mic0 is the default."""
    comps, cal = rooms[("circle", 3)]
    out, _ = beam(jvm, comps, "ds", None, unsteered="1,0,0,0", eq=False)
    raw = comps["mix"][0]
    err = out["mix"][LATENCY + 1024:] - raw[1024:len(raw) - LATENCY]
    assert np.max(np.abs(err)) < 1e-4 * max(1e-9, np.max(np.abs(raw)))
    off_axis = R.room(R.circle(), np.random.default_rng(6), target_az=25.0, rt60=0.0)
    average, _ = beam(jvm, off_axis, "ds", None, unsteered="0.25,0.25,0.25,0.25")
    steered, _ = beam(jvm, off_axis, "ds", cal.arrivals(25.0))
    g_avg, g_ds = gains(off_axis, average), gains(off_axis, steered)
    print(f"\nunsteered average LSD {g_avg['lsd']:.1f} dB vs steered DS {g_ds['lsd']:.2f} dB")
    assert g_avg["lsd"] > 5.0 and g_ds["lsd"] < 1.0


@pytest.mark.parametrize("geometry", sorted(R.GEOMETRIES))
def test_delay_and_sum_steered_by_the_learned_model(jvm, rooms, geometry):
    """DS toward the face's bearing, with arrival times read off the DOA
    calibration taught from faces (no geometry given). It must never hurt
    the target (gain within 0.3 dB, LSD < 1 dB) and it buys what a ~6 cm
    array can: about +1 dB of SNR against diffuse noise, little SIR."""
    rows = []
    for seed in (3, 4):
        comps, cal = rooms[(geometry, seed)]
        tau = cal.arrivals(10.0)
        truth = R.arrivals(R.GEOMETRIES[geometry], 10.0)
        assert max(abs(a - b) for a, b in zip(tau, truth)) < 0.35, (tau, truth)   # the model's steering
        out, _ = beam(jvm, comps, "ds", tau)
        g = gains(comps, out)
        rows.append(g)
        assert abs(g["target"]) < 0.3 and g["lsd"] < 1.0, g
        assert g["snr"] > 0.8 and g["sir"] > -0.5, g
    print(f"\n{geometry} DS: " + ", ".join(f"dSIR {g['sir']:+.2f} dSNR {g['snr']:+.2f} tgt {g['target']:+.2f} "
                                         f"LSD {g['lsd']:.2f}" for g in rows))


@pytest.mark.parametrize("geometry,margin", [("circle", 3.0), ("line", 0.8)])
def test_mvdr_beats_delay_and_sum_without_hurting_the_target(jvm, rooms, geometry, margin):
    """MVDR, noise covariance learned on quiet and other-talker hops only:
    several dB of SIR over DS on the circle, about one on the line, the
    target within half a dB and never coloured."""
    for seed in (3, 4):
        comps, cal = rooms[(geometry, seed)]
        tau = cal.arrivals(10.0)
        ds = gains(comps, beam(jvm, comps, "ds", tau, sigma=cal.sigma)[0])
        out, info = beam(jvm, comps, "mvdr", tau, sigma=cal.sigma)
        mv = gains(comps, out)
        print(f"\n{geometry} s{seed}: DS dSIR {ds['sir']:+.2f} dSNR {ds['snr']:+.2f} | MVDR dSIR {mv['sir']:+.2f} "
              f"dSNR {mv['snr']:+.2f} tgt {mv['target']:+.2f} LSD {mv['lsd']:.2f} | {info}")
        assert mv["sir"] >= ds["sir"] + margin, (ds, mv)
        assert mv["snr"] >= ds["snr"], (ds, mv)
        assert abs(mv["target"]) < 0.5 and mv["lsd"] < 1.5, mv
        assert info["mvdr"] > 0.8 * info["hops"] and info["guard"] == 0
        assert info["quiet"] > 0 and info["other"] > 0 and info["target"] > 0


def test_mvdr_needs_equalised_mics_and_the_guard_catches_it_when_not(jvm, rooms):
    """Mics 3 dB apart: with the per-mic gain equalisation MVDR keeps its SIR;
    without it the steering vector is wrong and R eats the target -- the
    self-cancellation guard sees the target's own hops lose power, forgets R
    and falls back to DS (trips > 0), instead of shipping a muffled voice."""
    comps, cal = rooms[("circle", 3)]
    g = 10 ** (np.random.default_rng(9).normal(0, 3.0, 4) / 20)
    skewed = {k: v * g[:, None] for k, v in comps.items()}
    tau = cal.arrivals(10.0)
    fixed, info_eq = beam(jvm, skewed, "mvdr", tau, sigma=cal.sigma, eq=True)
    raw, info_raw = beam(jvm, skewed, "mvdr", tau, sigma=cal.sigma, eq=False)
    with_eq, without = gains(skewed, fixed), gains(skewed, raw)
    print(f"\nmismatched mics: EQ dSIR {with_eq['sir']:+.2f} trips {info_eq['guard']} | "
          f"no EQ dSIR {without['sir']:+.2f} trips {info_raw['guard']}")
    assert info_eq["guard"] == 0 and with_eq["sir"] > 4.0
    assert info_raw["guard"] > 0


def test_the_talking_flag_keeps_a_mis_steered_target_out_of_the_noise_estimate(jvm):
    """The failure the guard cannot see: steered 15 deg wrong on a line array,
    none of the target's hops match its lags, so they are learned as
    'another talker' and MVDR cancels the target. While the camera says the
    target's mouth moves (focus, delayed 300 ms like a real camera), MVDR
    learns no 'other' hop: the loss shrinks. DS is the default for this."""
    pos = R.line()
    comps = R.room(pos, np.random.default_rng(3), target_az=10.0, rt60=0.5)
    cal = R.taught_calibration(pos, np.random.default_rng(5))
    tau = cal.arrivals(25.0)                         # 15 degrees off
    x = comps["target_direct"][0]
    e = np.array([np.mean(x[k:k + 256] ** 2) for k in range(0, len(x) - 255, 256)])
    act = np.nonzero(e > 0.02 * np.percentile(e, 95))[0]
    talk = np.zeros(len(e) + 64, bool)
    for k in act:
        talk[k + 19:k + 19 + 38] = True              # 0.3 s late, held 0.6 s
    blind = gains(comps, beam(jvm, comps, "mvdr", tau, sigma=cal.sigma)[0])
    out, info = beam(jvm, comps, "mvdr", tau, sigma=cal.sigma, talk=talk[:len(e)])
    seeing = gains(comps, out)
    ds = gains(comps, beam(jvm, comps, "ds", tau)[0])
    print(f"\nmis-steered line: MVDR tgt {blind['target']:+.2f} | with talking {seeing['target']:+.2f} "
          f"(held {info['held']}) | DS tgt {ds['target']:+.2f}")
    assert blind["target"] < -1.0                    # the failure is real
    assert seeing["target"] > blind["target"] + 0.3 and info["held"] > 0
    assert abs(ds["target"]) < 0.2                   # DS cannot do this


def test_steer_parsing_and_the_wire_format(jvm):
    lines = _run(jvm, "SteerHarness")
    assert lines[0] == ("tau=[0.0, 1.0, -1.5, 2.0] lags=[1.0, -1.5, 2.0, -2.5, 1.0, 3.5] w=[1.0, 0.0, 0.0, 0.0] "
                        "mode=1 talking=true sigma=0.2")
    # a NaN or an out-of-range arrival time: no target (mic 0), never a wild filter
    for line in lines[1:3]:
        assert line.startswith("tau=null lags=null w=[1.0, 0.0, 0.0, 0.0] mode=0 talking=false")
    # wrong length + invalid weights + unknown mode: mic 0, DS
    assert lines[3].startswith("tau=null lags=null w=[1.0, 0.0, 0.0, 0.0] mode=0")
    # no target but valid unsteered weights: kept; talking needs a target
    assert lines[4] == "tau=null lags=null w=[0.25, 0.25, 0.25, 0.25] mode=0 talking=false sigma=0.3"
    raw = bytes.fromhex(lines[5].split("=", 1)[1])
    assert raw[:2] == bytes([0xEA, 1]) and struct.unpack("<H", raw[2:4])[0] == 0x1234
    assert struct.unpack("<I", raw[4:8])[0] == 5                        # t_app_ms modulo 2^32
    assert np.frombuffer(raw[8:], "<i2").tolist() == [1, -2, 32767]
    # the Java consistency fit is app/doa.py's
    fit = [float(v) for v in lines[6].split("fit=[", 1)[1].split("]", 1)[0].split(",")]
    closure = float(lines[6].rsplit("closure=", 1)[1])
    py_fit, py_closure = doa.consistent([1.0, 2.0, 0.5, 1.0, -0.5, -1.5], [0.9, 0.8, 0.7, 0.9, 0.6, 0.5])
    assert np.allclose(fit, py_fit, atol=1e-9) and abs(closure - py_closure) < 1e-9


def test_the_beam_is_cheap(jvm, rooms):
    """CPU: the leader alone (one beam, what the robot runs), MVDR included."""
    comps, cal = rooms[("circle", 3)]
    _, info = beam(jvm, {"mix": comps["mix"]}, "mvdr", cal.arrivals(10.0), sigma=cal.sigma, keys=("mix",))
    seconds = comps["mix"].shape[1] / R.FS
    rtf = info["lead_ns"] / 1e9 / seconds
    print(f"\nMVDR beam: {info['lead_ns'] / 1e6:.0f} ms for {seconds:.0f} s of audio = {rtf:.3f} x real time "
          f"(one core of this PC, JIT warming included)")
    assert rtf < 0.25


# ==================== the link: ready, binary frames, steering ====================


def _client(monkeypatch, *, array=True, capture8=False):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "doa_enabled", True)
    monkeypatch.setattr(settings, "array_audio", array)
    monkeypatch.setattr(settings, "array_capture8", capture8)
    return TestClient(app)


STATUS = {"type": "doa_status", "state": "capturing", "source": "VOICE_RECOGNITION",
          "device": "Bothlent UAC Dongle", "detail": None, "rms8": [0.01] * 8, "others": 1,
          "others_silenced": False, "self_silenced": False, "zero_ms": 0, "sent": 0, "gated": 0,
          "rate": 16000, "channels": 8}


def pack(kind: int, seq: int, pcm: np.ndarray, t_ms: int = 0) -> bytes:
    return bytes([0xEA, kind]) + struct.pack("<HI", seq & 0xFFFF, t_ms & 0xFFFFFFFF) + \
        np.asarray(pcm, "<i2").tobytes()


def _wait_for(predicate, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_switches_off_doa_ready_is_byte_identical():
    assert not settings.array_audio and not settings.array_capture8
    assert json.dumps(doa.ready_frame()) == json.dumps(
        {"type": "doa_ready", "rate": 16000, "frame": 1024, "channels": [0, 1, 2, 3], "max_lag": 16.0,
         "gate_rms": 0.004, "max_fps": 16, "band": [200, 4000], "stats_s": 2.0})


def test_ready_carries_the_array_and_capture8_only_when_on(monkeypatch):
    monkeypatch.setattr(settings, "array_audio", True)
    assert doa.ready_frame()["array"] == {"beam": "ds", "chunk": 256, "rate": 16000}
    assert "capture8" not in doa.ready_frame()
    monkeypatch.setattr(settings, "array_audio_beam", "mvdr")
    assert doa.ready_frame()["array"]["beam"] == "mvdr"
    monkeypatch.setattr(settings, "array_audio_beam", "superbeam")      # a typo is DS, said once
    assert doa.ready_frame()["array"]["beam"] == "ds"
    monkeypatch.setattr(settings, "array_audio", False)
    monkeypatch.setattr(settings, "array_capture8", True)
    ready = doa.ready_frame()
    assert "array" not in ready and ready["capture8"] == {"max_s": 10.0}


def test_binary_means_nothing_with_the_array_off(monkeypatch):
    client = _client(monkeypatch, array=False)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        assert "array" not in ws.receive_json()
        ws.send_text(json.dumps(STATUS))
        ws.send_bytes(pack(1, 0, np.ones(256)))
        ws.send_text(json.dumps({"type": "doa_status", **{k: v for k, v in STATUS.items() if k != "type"}}))
        time.sleep(0.2)
    # not even looked at: the bytes never reach array_audio (it would refuse them too)
    assert array_audio._STREAM.counts == array_audio._Stream().counts
    assert len(array_audio._STREAM.fifo) == 0


def test_beam_frames_arrive_checked_and_rate_limited(monkeypatch):
    client = _client(monkeypatch)
    pcm = (np.sin(np.arange(256) * 0.1) * 3000).astype("<i2")
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        ready = ws.receive_json()
        assert ready["array"]["chunk"] == 256
        ws.send_text(json.dumps(dict(STATUS, beam={"mode": 0, "gen": 1, "sent": 3, "dropped": 0, "hops": 3,
                                                   "mvdr_hops": 0, "quiet": 0, "other": 0, "target": 0, "held": 0,
                                                   "guard_trips": 0, "raw8_sent": 0, "bogus": "x"})))
        ws.send_bytes(pack(1, 0, pcm))
        ws.send_bytes(pack(1, 1, pcm))
        ws.send_bytes(pack(1, 3, pcm))                       # seq 2 lost
        ws.send_bytes(b"\x00" + pack(1, 4, pcm)[1:])         # bad magic
        ws.send_bytes(pack(1, 5, pcm)[:-1])                  # odd length
        ws.send_bytes(pack(9, 6, pcm))                       # unknown kind
        ws.send_bytes(pack(1, 7, np.zeros(4096)))            # over the beam's size
        ws.send_bytes(pack(2, 0, np.zeros(64)))              # raw 8-ch with capture8 off
        assert _wait_for(lambda: array_audio._STREAM.counts["chunks"] == 3
                         and array_audio._STREAM.counts["refused"] == 1)
        link = array_audio._STREAM.link
        assert link is not None and link.status["beam"]["sent"] == 3 and "bogus" not in link.status["beam"]
        s = array_audio._STREAM.counts
        assert s["lost"] == 1 and s["malformed"] == 3 and s["oversize"] == 1 and s["refused"] == 1
        assert bytes(array_audio._STREAM.fifo) == pcm.tobytes() * 3
        # far beyond the stream's own rate: the bucket drops the excess
        for k in range(200):
            ws.send_bytes(pack(1, 8 + k, pcm))
        assert _wait_for(lambda: s["chunks"] + s["dropped_rate"] >= 203)
        assert s["dropped_rate"] > 100
    assert array_audio._STREAM.link is None and not array_audio._STREAM.fifo


class _FakeFocus:
    MOUTH_MOVING = 0.35

    def __init__(self):
        self.snap: dict = {}

    def snapshot(self):
        return self.snap

    def observe_doa(self, *a):
        pass


def _ready_calibration(pos=None):
    pos = R.circle() if pos is None else pos
    cal = R.taught_calibration(pos, np.random.default_rng(21))
    cal.fingerprint = {"rate": 16000, "channels": [0, 1, 2, 3], "device": "Bothlent UAC Dongle"}
    assert cal.ready
    doa._STATE.calibration = cal
    return cal


def test_the_target_is_the_face_that_last_spoke_else_the_nearest(monkeypatch):
    fake = _FakeFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    now = time.monotonic()
    # nobody: unsteered, mic 0
    msg = array_audio.steer_message(now)
    assert msg["tau"] is None and msg["why"] == "none" and msg["unsteered"] == [1.0, 0.0, 0.0, 0.0]
    fake.snap = {"engaged": [{"track": 1, "bearing": 12.0, "px": 120}, {"track": 2, "bearing": -20.0, "px": 160}],
                 "mouth": {}}
    # faces but no calibration yet: still unsteered, and it says why
    assert array_audio.steer_message(now)["why"] == "uncalibrated"
    cal = _ready_calibration()
    msg = array_audio.steer_message(now)
    assert msg["why"] == "nearest" and msg["bearing"] == -20.0 and msg["tau"] == cal.arrivals(-20.0)
    assert msg["talking"] is False and msg["sigma"] == round(cal.sigma, 4)
    # the smaller face speaks: it is the target, talking now
    fake.snap["mouth"] = {1: {"t": now, "activity": 0.8}}
    msg = array_audio.steer_message(now)
    assert (msg["why"], msg["bearing"], msg["talking"]) == ("spoke", 12.0, True)
    # it stops: still the one who last spoke (8 s), no longer talking
    fake.snap["mouth"] = {1: {"t": now + 1.0, "activity": 0.1}}
    msg = array_audio.steer_message(now + 2.0)
    assert (msg["why"], msg["bearing"], msg["talking"]) == ("spoke", 12.0, False)
    assert array_audio.steer_message(now + 9.0)["why"] == "nearest"
    monkeypatch.setattr(settings, "array_audio_unsteered", "average")
    fake.snap = {}
    assert array_audio.steer_message(now)["unsteered"] == [0.25] * 4


def test_steer_messages_follow_the_target_down_the_link(monkeypatch):
    fake = _FakeFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    monkeypatch.setattr(array_audio, "STEER_EVERY_S", 0.02)
    monkeypatch.setattr(array_audio, "STEER_MIN_GAP_S", 0.05)
    cal = _ready_calibration()
    fake.snap = {"engaged": [{"track": 1, "bearing": 15.0, "px": 150}], "mouth": {}}
    client = _client(monkeypatch)
    with client.websocket_connect("/ws/robot-doa?token=" + TOKEN, headers={"user-agent": ROBOT_UA}) as ws:
        assert ws.receive_json()["type"] == "doa_ready"
        first = ws.receive_json()
        assert first["type"] == "array_steer" and first["gen"] == 1 and first["tau"] == cal.arrivals(15.0)
        fake.snap = {"engaged": [{"track": 1, "bearing": -25.0, "px": 150}], "mouth": {}}
        second = ws.receive_json()
        assert second["gen"] == 2 and second["bearing"] == -25.0
        fake.snap = {}
        third = ws.receive_json()
        assert third["tau"] is None and third["why"] == "none"


@pytest.fixture
def live_server(monkeypatch):
    import uvicorn
    from fastapi import FastAPI

    from app import main

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "doa_enabled", True)
    monkeypatch.setattr(settings, "array_audio", True)
    mini = FastAPI()
    mini.add_api_websocket_route("/ws/robot-doa", main.ws_robot_doa)
    server = uvicorn.Server(uvicorn.Config(mini, host="127.0.0.1", port=0, log_level="warning", lifespan="off",
                                           timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert _wait_for(lambda: server.started, 10.0)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield "ws://127.0.0.1:%d/ws/robot-doa" % port
    server.should_exit = True
    thread.join(10)


def test_the_java_client_sends_beam_frames_and_reads_its_steering(jvm, live_server, monkeypatch):
    fake = _FakeFocus()
    monkeypatch.setattr(doa, "_focus", lambda: fake)
    cal = _ready_calibration()
    fake.snap = {"engaged": [{"track": 3, "bearing": 8.0, "px": 150}], "mouth": {}}
    lines = _run(jvm, "ArrayLinkHarness", live_server + "?token=" + TOKEN, "12", timeout=60)
    ready = json.loads(lines[0][6:])
    assert ready["type"] == "doa_ready" and ready["array"]["beam"] == "ds"
    steer = json.loads(next(line for line in lines if line.startswith("steer="))[6:])
    assert steer["tau"] == cal.arrivals(8.0) and steer["why"] == "nearest"
    assert lines[-1] == "closed"
    assert _wait_for(lambda: array_audio._STREAM.counts["chunks"] == 12, 5.0)
    assert array_audio._STREAM.counts["lost"] == 0


# ==================== the session's tap ====================


class _Link:
    def __init__(self):
        self.status = {"state": "capturing"}
        self.device = "Bothlent UAC Dongle"


WRAP = 1 << 15


def _code(idx0: int, n: int, array: bool) -> bytes:
    """Content timecode: the global sample index mod 2^15 (2 s), the page's
    as itself, the array's as -(index) - 1 -- the sign says the source."""
    v = np.arange(idx0, idx0 + n) % WRAP
    return (-v - 1 if array else v).astype("<i2").tobytes()


def _decode(chunk: bytes) -> tuple[np.ndarray, np.ndarray]:
    v = np.frombuffer(chunk, "<i2").astype(np.int64)
    src = v < 0
    return np.where(src, -v - 1, v), src


def simulate(*, seconds=6.0, page_lat=0.035, array_lat=0.045, array_down=(), page_gaps=(), in_speech=lambda t: True,
             speaking=lambda t: False, audible_until=lambda t: None, jitter=0.003, seed=0,
             stall=None, report_from=None):
    """Page frames every 20 ms and array chunks every 16 ms over one timeline,
    each carrying the room's content at its own latency, through one Tap.
    -> list of (t, page_bytes, out_bytes).

    `stall=(a, b)`: the server's event loop is blocked from a to b -- nothing
    arriving in between is processed until b, and then the page socket's
    queue happens to be read before the array's (the order the review's
    case needs: the page frame sees a beam clock that stopped with the loop).
    `report_from`: the tap's report clock starts there (by default it starts
    at the real monotonic time, so a simulated timeline never reports)."""
    rng = np.random.default_rng(seed)
    link = _Link()
    array_audio.link_opened(link)
    tap = array_audio.Tap()
    if report_from is not None:
        tap.report_at = 100.0 + report_from
    events = []
    t = 0.0
    while t < seconds:
        if not any(a <= t < b for a, b in page_gaps):
            events.append((t + rng.uniform(0, jitter), "page", t))
        t += 0.02
    t, seq = 0.0, 0
    while t < seconds:
        if not any(a <= t < b for a, b in array_down):
            events.append((t + rng.uniform(0, jitter), "array", t))
        t += 0.016
    if stall is not None:
        a, b = stall
        events = [((b, 0 if kind == "page" else 1, at) if a <= at < b else (at, 0, 0.0), kind, t)
                  for at, kind, t in events]
        events.sort(key=lambda e: e[0])
        events = [(key[0], kind, t) for key, kind, t in events]
    events.sort(key=lambda e: e[0])
    out = []
    base = 100.0
    for at, kind, t in events:
        now = base + at
        if kind == "array":
            end = int(round((t - array_lat) * R.FS))
            array_audio._beam(link, seq, _code(end - 256, 256, True), now)
            seq += 1
        else:
            end = int(round((t - page_lat) * R.FS))
            page = _code(end - 320, 320, False)
            until = audible_until(t)
            got = tap.frame(page, now, speaking=speaking(t), in_speech=in_speech(t),
                            audible_until=None if until is None else base + until)
            out.append((t, page, got))
    return out, tap


def _continuity(out):
    """Walk the forwarded stream: (largest content hole in ms at a source
    change, any repeated moment, the source changes)."""
    prev, prev_src, holes, repeats, changes = None, None, [], 0, []
    for t, page, got in out:
        if not got:
            continue
        idx, src = _decode(got)
        assert src.all() or not src.any(), "one page frame carried both sources"
        if got is page:
            assert not src.any()
        if prev is not None:
            step = int(idx[0] - prev) % WRAP
            if step == 0 or step > WRAP // 2:
                repeats += 1
            elif src[0] != prev_src:
                holes.append((step - 1) / R.FS * 1000)
                changes.append((t, "array" if src[0] else "page"))
        inner = np.diff(idx) % WRAP
        assert (inner == 1).all(), "a chunk is not continuous inside"
        prev, prev_src = int(idx[-1]), bool(src[-1])
    return holes, repeats, changes


@pytest.fixture
def array_on(monkeypatch):
    monkeypatch.setattr(settings, "array_audio", True)
    monkeypatch.setattr(settings, "doa_enabled", True)


def test_fallback_is_instant_with_no_hole_over_100ms_and_no_double_audio(array_on):
    """The array joins at Emma's turn (a page gap), dies mid-speech at 2.5 s
    (fallback: the next page frame after the stale time), comes back at 3 s
    and rejoins at the next page gap (4.0-4.6 s). Every forwarded frame is
    one source; no moment is sent twice; the fallback hole is the stale time
    plus at most one page frame (under 100 ms at the first 60; the default is
    120 since review 10 Oct -- config.py says why)."""
    stale = settings.array_audio_stale_ms / 1000.0
    out, tap = simulate(array_down=[(2.5, 3.0)], page_gaps=[(1.0, 1.6), (4.0, 4.6)])
    holes, repeats, changes = _continuity(out)
    assert repeats == 0
    kinds = [c[1] for c in changes]
    assert kinds == ["array", "page", "array"], changes
    assert 1.6 <= changes[0][0] < 1.7 and 2.5 < changes[1][0] < 2.5 + stale + 0.05
    assert 4.6 <= changes[2][0] < 4.7
    fallback_hole = holes[1]
    print(f"\nholes at source changes (ms): {[round(h, 1) for h in holes]}")
    assert fallback_hole <= settings.array_audio_stale_ms + 40.0
    assert tap.counts["fallback"] == 1 and tap.counts["switch_in"] == 2


def test_the_array_never_joins_inside_an_utterance(array_on):
    """Live again mid-speech with no page gap: the page keeps the turn until
    no segment is open, then the array joins after SWITCH_SKEW_S of nothing
    -- a short hole in the pre-roll, never the same moment twice."""
    talking_until = 3.5
    out, _ = simulate(array_down=[(0.0, 1.0)], in_speech=lambda t: t < talking_until, seconds=5.0)
    holes, repeats, changes = _continuity(out)
    assert repeats == 0
    assert len(changes) == 1 and changes[0][1] == "array" and changes[0][0] >= talking_until
    assert 0 < holes[0] <= 100.0


def test_half_duplex_gate_on_the_array(array_on):
    """Emma audible (the server's playback clock, 2-3 s) on a page that keeps
    sending (no half-duplex of its own): the array's audio is thrown away
    and the page frame goes up as it is -- the array has no AEC. After her
    turn the array comes back only at a boundary (no segment open, 3.4 s),
    and nothing it heard while she was audible is ever forwarded."""
    speaking = lambda t: 2.0 <= t < 3.0     # noqa: E731
    out, tap = simulate(page_gaps=[(1.0, 1.6)], in_speech=lambda t: t < 3.4, speaking=speaking, seconds=4.5)
    holes, repeats, changes = _continuity(out)
    during = [(page, got) for t, page, got in out if 2.0 <= t < 3.0]
    assert during and all(got is page for page, got in during)
    assert tap.counts["speaking"] == len(during)
    assert repeats == 0
    held = [(t, got) for t, page, got in out if 3.0 <= t < 3.4]
    assert held and all(got is page for t, page, got in out if 3.0 <= t < 3.4)
    assert any(not (got is page) and got for t, page, got in out if t >= 3.5)
    # the array's content from while she was audible (2.0-3.0 s at the room)
    for t, page, got in out:
        if got and got is not page:
            idx, src = _decode(got)
            moment = idx / R.FS                        # modulo 2.048 s: unwrap near t
            moment = moment + np.round((t - 0.05 - moment) / (WRAP / R.FS)) * (WRAP / R.FS)
            assert src.all() and not ((moment >= 2.0) & (moment < 3.0)).any(), t


def test_half_duplex_the_echo_tail_counts_from_the_pages_resume(monkeypatch, array_on):
    """Half-duplex (the default): the page sends NOTHING while Emma speaks, so
    the tap never sees `speaking`, and the page resumes after its own
    playback + 350 ms -- by then the server's clock (which knows nothing of
    Android's output latency) has run out. Her speech ended (server clock)
    at 1.45 s inside the page gap 1.0-1.6 s: the array, which has no AEC,
    must not join at the resume; page frames carry ARRAY_AUDIO_ECHO_TAIL_S
    first, and the array joins at the next boundary after that."""
    monkeypatch.setattr(settings, "array_audio_echo_tail_s", 0.25)
    out, tap = simulate(page_gaps=[(1.0, 1.6)], in_speech=lambda t: False, seconds=2.5,
                        audible_until=lambda t: 1.45 if t >= 1.0 else None)
    holes, repeats, changes = _continuity(out)
    assert repeats == 0
    window = [(t, page, got) for t, page, got in out if 1.6 <= t < 1.84]
    assert window and all(got is page for t, page, got in window), "page frames for the tail"
    after = [c for c in changes if c[0] >= 1.0]          # (it joined at 0.64 s, before her turn)
    assert [k for _, k in after] == ["page", "array"], changes
    assert after[0][0] < 1.62 and after[1][0] >= 1.85, changes
    # after her turn, nothing the array heard before the resume + tail is forwarded
    for t, page, got in out:
        if t >= 1.0 and got and got is not page:
            idx, src = _decode(got)
            moment = idx / R.FS
            moment = moment + np.round((t - 0.05 - moment) / (WRAP / R.FS)) * (WRAP / R.FS)
            assert src.all() and (moment >= 1.6 + 0.25 - 0.06).all(), t
    # and with the tail at 0 the knob really is the knob: back to joining at the resume
    monkeypatch.setattr(settings, "array_audio_echo_tail_s", 0.0)
    out0, _ = simulate(page_gaps=[(1.0, 1.6)], in_speech=lambda t: False, seconds=2.5,
                       audible_until=lambda t: 1.45 if t >= 1.0 else None)
    assert [c for c in _continuity(out0)[2] if c[0] >= 1.0] == [], "array straight on at the resume"


def test_the_session_closes_the_array_while_emma_may_be_heard(monkeypatch, array_on):
    """Session level: reply bytes sent to the page start the playback clock;
    until it runs out (+ the echo tail) no array audio replaces a page frame,
    and the array audio that piled up meanwhile is dropped, not replayed."""
    from app.session import VoiceSession

    session = VoiceSession.__new__(VoiceSession)
    session._array_tap = array_audio.Tap()
    session._play_until = 0.0
    monkeypatch.setattr(settings, "array_audio_echo_tail_s", 0.25)
    now = time.monotonic()
    session._note_playback(1.0)              # one second of reply, sent now
    assert session._array_speaking(now + 0.5)
    assert session._array_speaking(now + 1.2)            # the tail
    assert not session._array_speaking(now + 1.3)
    session._play_until = min(session._play_until, now + 0.1)   # "interrupted" at 0.1 s
    assert not session._array_speaking(now + 0.4)


def test_the_session_tells_the_tap_when_she_stops_being_audible(monkeypatch, array_on):
    """The half-duplex echo window needs the clock: `_array_frame` passes the
    server's playback end (None before she has said anything)."""
    from app import robot_face_link
    from app.session import VoiceSession

    seen = []

    class _Tap:
        def frame(self, page, now, **kw):
            seen.append(kw)
            return page

    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    session = VoiceSession.__new__(VoiceSession)
    session._array_tap = _Tap()
    session._play_until = 0.0
    session.provider = None
    session._array_frame(bytes(640))
    assert seen[-1]["audible_until"] is None
    session._note_playback(1.0)
    session._array_frame(bytes(640))
    assert seen[-1]["audible_until"] == pytest.approx(session._play_until)


def test_the_tap_is_only_for_the_robot_face_in_plain_half_duplex(monkeypatch, caplog):
    class WS:
        def __init__(self, agent):
            self.headers = {"user-agent": agent}

    class S:
        def __init__(self, agent):
            self.ws = WS(agent)

    robot, desk = S(ROBOT_UA), S("Mozilla/5.0 Chrome/130")
    kw = dict(input_rate=16000, barge_in=False, hold_speech=False)
    assert array_audio.tap_for(robot, **kw) is None                    # ARRAY_AUDIO off
    monkeypatch.setattr(settings, "array_audio", True)
    assert array_audio.tap_for(robot, **kw) is None                    # DOA_ENABLED off: said why
    assert "DOA_ENABLED" in caplog.text
    monkeypatch.setattr(settings, "doa_enabled", True)
    assert isinstance(array_audio.tap_for(robot, **kw), array_audio.Tap)
    assert array_audio.tap_for(desk, **kw) is None
    assert array_audio.tap_for(robot, input_rate=24000, barge_in=False, hold_speech=False) is None
    assert array_audio.tap_for(robot, input_rate=16000, barge_in=True, hold_speech=False) is None
    assert array_audio.tap_for(robot, input_rate=16000, barge_in=False, hold_speech=True) is None
    monkeypatch.setattr(settings, "multi_session", True)
    assert array_audio.tap_for(robot, **kw) is None


class _Provider:
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self):
        self.sent: list = []

    async def send_audio(self, data):
        self.sent.append(data)


class _WS:
    def __init__(self, frames, agent="Mozilla/5.0 Chrome/130"):
        self.headers = {"user-agent": agent}
        self._frames = list(frames)

    async def receive(self):
        if self._frames:
            return {"type": "websocket.receive", "bytes": self._frames.pop(0)}
        return {"type": "websocket.disconnect"}

    async def send_text(self, text):
        pass


def _session(frames, agent="Mozilla/5.0 Chrome/130"):
    from app.session import VoiceSession

    s = VoiceSession.__new__(VoiceSession)
    s.ws = _WS(frames, agent)
    s.provider = _Provider()
    s._ears_closed_until = None
    s._greeting_turn_seen = False
    return s


@pytest.mark.parametrize("robot", [False, True])
def test_switches_off_the_page_bytes_go_up_untouched(monkeypatch, robot):
    """The sales room (and the robot with DOA_ENABLED but ARRAY_AUDIO off):
    nothing new runs, and the very same bytes objects reach the provider in
    the same order."""
    from app.session import VoiceSession

    monkeypatch.setattr(settings, "doa_enabled", robot)
    frames = [_code(k * 320, 320, False) for k in range(5)]
    s = _session(frames, agent=ROBOT_UA if robot else "Mozilla/5.0 Chrome/130")
    s._open_array_and_enhancer(s.provider, False, False)
    assert s._array_tap is None and s._enhancer is None
    rows = []
    monkeypatch.setattr("app.turnlog.record", lambda event, **f: rows.append(event))
    asyncio.run(VoiceSession._browser_to_provider(s))
    assert len(s.provider.sent) == 5 and all(a is b for a, b in zip(s.provider.sent, frames))
    assert not any(r.startswith("array") for r in rows)
    assert array_audio._STREAM.counts == array_audio._Stream().counts


def test_the_session_sends_the_array_instead_of_the_page(monkeypatch, array_on):
    """End to end through the session pump: the robot face's frames after a
    gap are replaced by the array's audio (with ARRAY_AUDIO_GAIN), never
    both; with the array silent the page's own bytes go up."""
    from app import robot_face_link
    from app.session import VoiceSession

    link = _Link()
    array_audio.link_opened(link)
    monkeypatch.setattr(array_audio, "RECOVER_S", 0.0)
    monkeypatch.setattr(settings, "array_audio_stale_ms", 5000)
    monkeypatch.setattr(robot_face_link, "is_robot_face", lambda session: True)
    monkeypatch.setattr(settings, "array_audio_gain", 2.0)
    now = time.monotonic()
    array_audio._beam(link, 0, np.full(320, 1000, "<i2").tobytes(), now)
    page = np.full(320, 7, "<i2").tobytes()
    s = _session([page], agent=ROBOT_UA)
    s._open_array_and_enhancer(s.provider, False, False)
    assert isinstance(s._array_tap, array_audio.Tap)
    asyncio.run(VoiceSession._browser_to_provider(s))
    assert len(s.provider.sent) == 1
    assert np.frombuffer(s.provider.sent[0], "<i2").tolist() == [2000] * 320
    # the stream goes stale: the next page frame is the page's own
    s2 = _session([page], agent=ROBOT_UA)
    s2._open_array_and_enhancer(s2.provider, False, False)
    monkeypatch.setattr(array_audio, "live", lambda now: (False, "stale"))
    asyncio.run(VoiceSession._browser_to_provider(s2))
    assert s2.provider.sent == [page] and s2.provider.sent[0] is page


def test_the_report_puts_both_levels_side_by_side(monkeypatch, array_on):
    """The floor's scale depends on the chain in front of it (CLAUDE.md): the
    array_report row carries the page's and the array's RMS over the same
    moments, so ARRAY_AUDIO_GAIN / VAD floors are tuned from numbers."""
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    out, tap = simulate(page_gaps=[(1.0, 1.6)], seconds=3.0)
    tap.report()
    event, row = [r for r in rows if r[0] == "array_report"][-1]
    assert row["page_rms"] and row["array_rms"] and row["array_vs_page_db"] is not None
    assert set(row) >= {"source", "array_frames", "page_frames", "lost", "stale", "fallback", "switch_in"}
    assert all(not isinstance(v, str) or len(v) < 20 for v in row.values())     # numbers, short labels


# ==================== the channels 4-7 test ====================


def test_capture8_is_refused_unless_the_server_asked(monkeypatch):
    monkeypatch.setattr(settings, "array_audio", True)
    assert not array_audio.receive(_Link(), pack(2, 0, np.zeros(64)), time.monotonic())
    assert not array_audio._capture_dir().exists()


def test_capture8_writes_an_obvious_file_and_deletes_it_on_time(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "array_capture8", True)
    monkeypatch.setattr(settings, "array_capture8_keep_min", 10.0)
    link = _Link()
    array_audio.link_opened(link)
    blocks = [np.arange(k * 8192, (k + 1) * 8192) % 30000 for k in range(3)]
    for seq, block in enumerate(blocks):
        assert array_audio.receive(link, pack(2, seq, block), time.monotonic())
    array_audio.link_closed(link)
    folder = array_audio._capture_dir()
    files = sorted(p.name for p in folder.iterdir())
    assert len(files) == 2 and all(f.startswith("ARRAY8-TEST-") for f in files)
    raw = folder / files[1]
    assert raw.suffix == ".s16le" and np.fromfile(raw, "<i2").tolist() == np.concatenate(blocks).tolist()
    meta = json.loads((folder / files[0]).read_text(encoding="utf-8"))
    assert meta["frames"] == 3 * 1024 and meta["channels"] == 8 and meta["missing_chunks"] == 0
    stamp = time.mktime(time.strptime(files[0][len("ARRAY8-TEST-"):-5], "%Y%m%d-%H%M%S"))
    assert array_audio.sweep(stamp + 9 * 60) == 0
    # the age is the NAME's: a fresh mtime (a copy, a restore) does not keep it
    import os

    os.utime(raw, (time.time() + 3600, time.time() + 3600))
    assert array_audio.sweep(stamp + 10 * 60 + 1) == 2 and not any(folder.iterdir())


def test_a_short_capture8_run_is_closed_and_kept_to_its_deadline(monkeypatch):
    """The documented run asks the app for 8 s (< CAPTURE8_MAX_S): the server
    never saw the file fill, so it stayed open -- tail unflushed, no .json,
    and an open file is the one the sweep skips -- until the link closed.
    Idle for CAPTURE8_IDLE_S = over: closed, flushed, described."""
    monkeypatch.setattr(settings, "array_capture8", True)
    link = _Link()
    array_audio.link_opened(link)
    blocks = [np.arange(k * 8192, (k + 1) * 8192) % 30000 for k in range(2)]
    for seq, block in enumerate(blocks):
        assert array_audio.receive(link, pack(2, seq, block), time.monotonic())
    folder = array_audio._capture_dir()
    assert array_audio.maintain(time.monotonic()) is True              # still running: kept
    assert not list(folder.glob("*.json"))
    assert array_audio.maintain(time.monotonic() + array_audio.CAPTURE8_IDLE_S + 0.1) is True
    raw, = folder.glob("*.s16le")
    meta = json.loads(raw.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["frames"] == 2 * 1024
    assert np.fromfile(raw, "<i2").tolist() == np.concatenate(blocks).tolist(), "the tail is on disk"


def test_the_server_sweeps_captures_with_every_switch_off(monkeypatch):
    """The test's last step is "turn ARRAY_CAPTURE8 off and restart" -- after
    which no link or call ever reached array_audio here, and the room audio
    stayed on disk for good. The startup hook sweeps whatever the switches;
    a fresh file keeps a sweeper running until it, too, is gone."""
    from app import main

    assert not settings.array_audio and not settings.array_capture8
    folder = array_audio._capture_dir()
    folder.mkdir(parents=True)
    old = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() - 3600))
    for suffix in (".s16le", ".json"):
        (folder / f"ARRAY8-TEST-{old}{suffix}").write_bytes(b"room")
    (folder / "notes.txt").write_text("not ours", encoding="utf-8")
    asyncio.run(main._forget_array_captures())
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]

    fresh = time.strftime("%Y%m%d-%H%M%S")
    (folder / f"ARRAY8-TEST-{fresh}.s16le").write_bytes(b"room")
    monkeypatch.setattr(array_audio, "SWEEP_EVERY_S", 0.01)

    async def run():
        await main._forget_array_captures()
        task = array_audio._SWEEPER["task"]
        assert task is not None and not task.done(), "a fresh file keeps the sweeper going"
        monkeypatch.setattr(settings, "array_capture8_keep_min", 0.0)     # ...until it expires
        await asyncio.wait_for(task, 2.0)

    asyncio.run(run())
    assert sorted(p.name for p in folder.iterdir()) == ["notes.txt"]


def _channels_report():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "array_channels_report.py"
    spec = importlib.util.spec_from_file_location("array_channels_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_channels_report_tells_the_kinds_apart():
    """Synthetic 8-channel captures with the four answers the robot might
    give for channels 4-7: a fixed sum of 0-3, another microphone, a
    noise-suppressed copy, and a playback reference (the AEC's input)."""
    A = _channels_report()
    rng = np.random.default_rng(1)
    n = 8 * R.FS
    pos = np.vstack([R.circle(), [[0.0, 0.05, 0.0]]])
    talk = np.zeros(n)
    talk[: 3 * R.FS] = R.speech(R.TARGET_CLIPS, 3 * R.FS, rng)
    play = np.zeros(n)
    play[3 * R.FS: 6 * R.FS] = R.speech(R.OTHER_CLIPS, 3 * R.FS, rng, level=0.1)
    spk = np.roll(R.render(play, R.rir(pos, 180, -20, rng, rt60=0.4)), 48, axis=1)
    mics = R.render(talk, R.rir(pos, 0, 10, rng, rt60=0.4)) + spk + R.diffuse(pos, n, rng) * 0.002
    mics += rng.normal(0, 10 ** (-66 / 20), mics.shape)
    x = np.zeros((8, n))
    x[:4] = mics[:4]
    x[4] = mics[:4].mean(0)
    x[5] = mics[4]
    gate = np.repeat(A.frame_rms(mics[0]) > np.percentile(A.frame_rms(mics[0]), 40), A.FRAME)
    x[6, :len(gate)] = mics[0, :len(gate)] * gate * 0.9
    x[7] = play * 0.5
    verdicts = A.analyse(np.clip(np.round(x * 32768), -32768, 32767) / 32768)["verdicts"]
    assert verdicts[4].startswith("derived from 0-3")
    assert verdicts[5].startswith("another microphone")
    assert verdicts[6].startswith("processed output")
    assert verdicts[7].startswith("playback reference")
    silent = np.zeros((8, R.FS))
    silent[:4] = rng.normal(0, 0.01, (4, R.FS))
    silent[5] = silent[1]
    v = A.analyse(silent)["verdicts"]
    assert v[4].startswith("silent") and v[5] == "duplicate of ch 1"


# ==================== the app's source contracts ====================


def test_the_app_beamforms_only_when_doa_ready_asks():
    src = code_only(read(LISTENER))
    ready = method(src, "private void readArray(JSONObject m)")
    assert 'm.optJSONObject("array")' in ready and "beamMode = array == null ? null" in ready
    loop = method(src, "private void captureLoop(Run owner)")
    assert "ArrayBeam beam = beamMode != null ? new ArrayBeam(RATE, p.maxLag) : null;" in loop
    # without the beam the reads are the DOA frame, exactly as before
    assert "int readShorts = (beam != null ? ArrayBeam.HOP : p.frame) * DoaMath.CAPTURE_CHANNELS;" in loop
    assert "int want = Math.min(buf.length - at, readShorts - at % readShorts);" in loop
    assert loop.count("ArrayBeam.KIND_BEAM") == 1 and "while (beam != null &&" in loop
    serve = method(src, "private long serve(Run r, DoaLink opened)")
    assert serve.index("params = readParams(message);") < serve.index("readArray(message);")
    assert '"array_steer".equals(message.optString("type"))' in serve
    steer = method(src, "static ArrayBeam.Steer readSteer(JSONObject m, double maxLag)")
    assert "ArrayBeam.Steer.parse(" in steer
    send = method(src, "private void sendLoop(Run r)")
    assert "current.sendBinary((byte[]) next)" in send and "current.sendText((String) next)" in send


def test_the_raw_upload_is_debug_only_and_needs_the_server():
    src = code_only(read(LISTENER))
    start = method(src, "private void startCapture8(int seconds)")
    assert start.index("if (!capture8Allowed)") < start.index("capture8Left =")
    assert '!"capturing".equals(state)' in start
    # reachable only from the debug receiver (registered for debuggable builds)
    assert src.count("startCapture8(") == 2
    assert "startCapture8(" in method(src, "public void onReceive(Context context, Intent intent)")
    loop = method(src, "private void captureLoop(Run owner)")
    assert "if (capture8Left > 0 && capture8Allowed)" in loop and loop.count("ArrayBeam.KIND_RAW8") == 1
    assert "capture8Left = 0;" in method(src, "private void stopAll(String why)")


# ==================== review 10 Oct: report volume, loop stalls ====================


def test_an_idle_line_writes_array_report_only_when_something_happens(monkeypatch, array_on):
    """A standing line is open all day and the page sends frames all day: a
    row every REPORT_S of an empty room was ~17,000 rows a day of zeros. Now
    the call's first window (the switch to the array) and the window where
    somebody spoke -- not the quiet ones in between."""
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    out, tap = simulate(seconds=31.0, in_speech=lambda t: 21.0 <= t < 23.0, report_from=0.0)
    reports = [f for e, f in rows if e == "array_report"]
    assert len(reports) == 2, [(r["source"], r["array_frames"]) for r in reports]
    assert reports[0]["switch_in"] == 1 and reports[0]["source"] == "array"


def test_a_stream_health_change_is_reported_on_an_otherwise_idle_line(monkeypatch, array_on):
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    out, tap = simulate(seconds=16.0, in_speech=lambda t: False, report_from=0.0,
                        array_down=[(12.0, 12.4)])
    reports = [f for e, f in rows if e == "array_report"]
    assert len(reports) == 2 and reports[1]["stale"] >= 1, reports


@pytest.mark.parametrize("stale_ms, falls_back", [(120, False), (60, True)])
def test_an_event_loop_stall_is_not_a_dead_array(monkeypatch, array_on, stale_ms, falls_back):
    """The loop is blocked 100 ms (asyncio's own "slow callback" bar is 100):
    the page frame queued during the stall is read first and sees a beam
    clock that stopped with the loop. At the first default, 60 ms, that was
    a dead array -- a mid-utterance splice to the page mic and a `fallback`
    in the A/B's report; at 120 (= PAGE_GAP_S) it is not. The array's audio
    from the stall is delayed, never lost or repeated."""
    monkeypatch.setattr(settings, "array_audio_stale_ms", stale_ms)
    rows = []
    monkeypatch.setattr(array_audio, "_record", lambda event, **f: rows.append((event, f)))
    out, tap = simulate(seconds=4.0, page_gaps=[(1.0, 1.6)], stall=(2.5, 2.6),
                        in_speech=lambda t: True)
    holes, repeats, changes = _continuity(out)
    assert repeats == 0
    kinds = [c[1] for c in changes]
    assert kinds == (["array", "page"] if falls_back else ["array"]), changes
    tap.report()
    row = [f for e, f in rows if e == "array_report"][-1]
    assert row["max_gap_ms"] >= 90 and row["stale_ms"] == stale_ms


def test_the_shipped_stale_default_is_120():
    """Asserted on the source (dataclass defaults are baked at import from
    this machine's .env): the default the robot gets without a setting."""
    import inspect
    import re

    from app import config

    assert re.search(r'os\.getenv\("ARRAY_AUDIO_STALE_MS",\s*"120"\)', inspect.getsource(config))
