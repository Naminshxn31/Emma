"""Synthetic rooms for the robot's 4-mic array (ARRAY_AUDIO): a target talker,
other talkers, reverberation and a diffuse noise field, rendered to four
microphones with exact fractional delays -- each component kept separate so
a beamformer's SIR/SNR gain can be measured (run every component through the
filter the mixture got; tests/test_array_audio.py and
scripts/eval_array_beam.py both use this).

Speech is the committed TTS in tests/data/wake (no customer audio, any
machine). The geometries are the ones tests/test_doa.py teaches the DOA
calibration with: the real array's is unknown, which is the point of
learning the steering from faces.
"""
from __future__ import annotations

import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np

from app import doa

FS = 16000
C = 343.0
WAKE = Path(__file__).resolve().parent / "data" / "wake"
TARGET_CLIPS = ("emma_sentence.wav", "emma_thai_f.wav", "other_imm_trap.wav")
OTHER_CLIPS = ("other.wav", "emma_thai_m.wav", "emma.wav")


def circle(rot: float = 0.4, r: float = 0.032) -> np.ndarray:
    return np.array([[r * math.cos(rot + k * math.pi / 2), r * math.sin(rot + k * math.pi / 2), 0.0]
                     for k in range(4)])


def line(ang_deg: float = 12.0, d: float = 0.035) -> np.ndarray:
    a = math.radians(ang_deg)
    return np.array([[(k - 1.5) * d * math.sin(a), (k - 1.5) * d * math.cos(a), 0.0] for k in range(4)])


GEOMETRIES = {"circle": circle(), "line": line()}


def unit(az_deg: float, el_deg: float) -> np.ndarray:
    a, e = math.radians(az_deg), math.radians(el_deg)
    return np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])


def arrivals(pos, az_deg: float, el_deg: float = 10.0) -> np.ndarray:
    """Far-field arrival time at each mic, samples, mic 0 = 0."""
    t = -(np.asarray(pos) @ unit(az_deg, el_deg)) / C * FS
    return t - t[0]


def load(name: str | Path) -> np.ndarray:
    path = Path(name)
    if not path.is_absolute():
        path = WAKE / path
    with wave.open(str(path)) as w:
        assert w.getframerate() == FS and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32768.0


def speech(clips, n: int, rng, level: float = 0.05) -> np.ndarray:
    """Clips end to end with short pauses, scaled to `level` RMS over speech."""
    parts, total = [], 0
    while total < n:
        x = load(clips[int(rng.integers(len(clips)))])
        gap = np.zeros(int(rng.uniform(0.1, 0.4) * FS))
        parts += [x, gap]
        total += len(x) + len(gap)
    x = np.concatenate(parts)[:n]
    active = np.abs(x) > 1e-3
    return x / (math.sqrt(float(np.mean(x[active] ** 2))) + 1e-12) * level


def _kernel(frac: float, taps: int = 32) -> np.ndarray:
    n = np.arange(taps) - taps // 2 + 1
    return np.sinc(n - frac) * np.hanning(taps + 2)[1:-1]


def rir(pos, az_deg: float, el_deg: float, rng, rt60: float = 0.5, drr_db: float = 3.0,
        reflections: int = 300, length_s: float = 0.45) -> np.ndarray:
    """(4, L) impulse responses: the direct path at (az, el), then
    `reflections` plane waves from random directions decaying with rt60 --
    a reverberant field with the right spatial coherence, no geometry needed."""
    L = int(length_s * FS) + 64
    pos = np.asarray(pos)
    h = np.zeros((len(pos), L))
    base = 40.0

    def add(direction, delay, gain):
        arrival = -(pos @ direction) / C * FS
        for m in range(len(pos)):
            d = base + delay + arrival[m]
            start = int(math.floor(d)) - 15
            if 0 <= start and start + 32 <= L:
                h[m, start:start + 32] += gain * _kernel(d - math.floor(d))

    add(unit(az_deg, el_deg), 0.0, 1.0)
    if rt60 > 0:
        t = np.sort(rng.uniform(0.002, length_s - 0.02, reflections))
        g = np.exp(-6.9 * t / rt60) * rng.choice([-1.0, 1.0], reflections) * rng.uniform(0.5, 1.0, reflections)
        g *= math.sqrt(10 ** (-drr_db / 10) / float(np.sum(g ** 2)))
        for tr, gr in zip(t, g):
            v = rng.normal(size=3)
            v[2] *= 0.6
            add(v / np.linalg.norm(v), tr * FS, gr)
    return h


def render(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    from scipy.signal import fftconvolve

    return np.array([fftconvolve(x, hm)[:len(x)] for hm in h])


def diffuse(pos, n: int, rng, sources: int = 48) -> np.ndarray:
    """Pinkish noise from `sources` random directions: an isotropic field."""
    pos = np.asarray(pos)
    out = np.zeros((len(pos), n))
    f = np.fft.rfftfreq(n + 512, 1 / FS)
    for _ in range(sources):
        spec = np.fft.rfft(rng.normal(size=n + 512)) / np.sqrt(np.maximum(f, 100.0))
        arrival = -(pos @ unit(rng.uniform(-180, 180), rng.uniform(-40, 40))) / C * FS
        for m in range(len(pos)):
            out[m] += np.fft.irfft(spec * np.exp(-2j * np.pi * f * (arrival[m] + 64) / FS), n + 512)[:n]
    return out


def power(x) -> float:
    return float(np.mean(np.asarray(x) ** 2))


def db(x: float) -> float:
    return 10 * math.log10(max(x, 1e-20))


def room(pos, rng, *, target_az: float = 10.0, others=((70.0, 0.0), (-130.0, 0.0)), snr_db: float = 10.0,
         rt60: float = 0.5, seconds: float = 8.0) -> dict[str, np.ndarray]:
    """Components at the four mics: target (reverberant), target_direct (the
    same talker, direct path only -- the reference for damage), interf (the
    other talkers, each at its own SIR in `others` = ((az, sir_db), ...)),
    noise (diffuse, at `snr_db` under the target), and `mix`."""
    n = int(seconds * FS)
    talker = speech(TARGET_CLIPS, n, rng)
    out = {"target": render(talker, rir(pos, target_az, 10, rng, rt60=rt60, drr_db=4)),
           "target_direct": render(talker, rir(pos, target_az, 10, rng, rt60=0))}
    ref = power(out["target"][0])
    interf = np.zeros_like(out["target"])
    for az, sir in others:
        x = render(speech(OTHER_CLIPS, n, rng), rir(pos, az, 5, rng, rt60=rt60, drr_db=0))
        interf += x * math.sqrt(ref / power(x[0]) * 10 ** (-sir / 10))
    out["interf"] = interf
    noise = diffuse(pos, n, rng)
    out["noise"] = noise * math.sqrt(ref / power(noise[0]) * 10 ** (-snr_db / 10))
    out["mix"] = out["target"] + out["interf"] + out["noise"]
    return out


def taught_calibration(pos, rng, n: int = 150, noise: float = 0.15) -> doa.Calibration:
    """Taught the way the robot teaches it (faces -30..30, mouths 5..25 deg up)."""
    cal = doa.Calibration({})
    for _ in range(n):
        face = rng.uniform(-30, 30)
        lags = doa.farfield_lags(pos, face, rng.uniform(5, 25)) + rng.normal(0, noise, 6)
        cal.add(doa.consistent(lags, [0.7] * 6)[0], face, 0.49)
    cal.fit()
    return cal


# ==================== running the app's beam (ArrayBeam.java) ====================

ORDER = ("mix", "target", "target_direct", "interf", "noise")
#: ArrayBeam's output is its input N - HOP samples ago.
LATENCY = 256

BEAM_HARNESS = """package com.emma.robot.control;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Paths;

public final class BeamHarness {
    static double[] csv(String s) {
        if ("none".equals(s)) return null;
        String[] p = s.split(",");
        double[] v = new double[p.length];
        for (int k = 0; k < p.length; k++) v[k] = Double.parseDouble(p[k]);
        return v;
    }

    /** in.f32 [comp][mic][n], comps, n, out.f32, mode, tau|none, sigma, unsteered, eq 1/0 [, talk.bin] */
    public static void main(String[] a) throws Exception {
        FloatBuffer fb = ByteBuffer.wrap(Files.readAllBytes(Paths.get(a[0]))).order(ByteOrder.LITTLE_ENDIAN)
                .asFloatBuffer();
        int comps = Integer.parseInt(a[1]);
        int n = Integer.parseInt(a[2]);
        float[][][] x = new float[comps][4][n];
        for (int c = 0; c < comps; c++) for (int m = 0; m < 4; m++) fb.get(x[c][m]);
        double[] tau = csv(a[5]);
        double sigma = Double.parseDouble(a[6]);
        double[] unsteered = csv(a[7]);
        byte[] talk = a.length > 9 ? Files.readAllBytes(Paths.get(a[9])) : null;
        ArrayBeam.Steer quiet = ArrayBeam.Steer.parse(tau, sigma, unsteered, a[4], 1, 16, false);
        ArrayBeam.Steer talking = ArrayBeam.Steer.parse(tau, sigma, unsteered, a[4], 2, 16, true);
        ArrayBeam lead = new ArrayBeam(16000, 16);
        lead.equalise("1".equals(a[8]));
        lead.steer(quiet);
        ArrayBeam[] follow = new ArrayBeam[comps];
        for (int c = 1; c < comps; c++) follow[c] = new ArrayBeam(16000, 16);
        float[][] out = new float[comps][n];
        float[][] hop = new float[4][ArrayBeam.HOP];
        long leadNs = 0;
        for (int at = 0; at + ArrayBeam.HOP <= n; at += ArrayBeam.HOP) {
            if (talk != null) lead.steer(talk[at / ArrayBeam.HOP] != 0 ? talking : quiet);
            for (int c = 0; c < comps; c++) {
                for (int m = 0; m < 4; m++) System.arraycopy(x[c][m], at, hop[m], 0, ArrayBeam.HOP);
                long t0 = System.nanoTime();
                float[] y = c == 0 ? lead.process(hop) : follow[c].processWith(hop, lead);
                if (c == 0) leadNs += System.nanoTime() - t0;
                System.arraycopy(y, 0, out[c], at, ArrayBeam.HOP);
            }
        }
        ByteBuffer ob = ByteBuffer.allocate(4 * comps * n).order(ByteOrder.LITTLE_ENDIAN);
        for (int c = 0; c < comps; c++) for (int k = 0; k < n; k++) ob.putFloat(out[c][k]);
        Files.write(Paths.get(a[3]), ob.array());
        System.out.println("{\\"hops\\":" + lead.hops + ",\\"quiet\\":" + lead.quietHops + ",\\"other\\":"
                + lead.otherHops + ",\\"target\\":" + lead.targetHops + ",\\"mvdr\\":" + lead.mvdrHops
                + ",\\"held\\":" + lead.heldHops + ",\\"guard\\":" + lead.guardTrips + ",\\"lead_ns\\":" + leadNs + "}");
    }
}
"""


def run_java(jvm, main: str, *args: str, timeout: float = 180) -> list[str]:
    done = subprocess.run([jvm["java"], "-cp", jvm["cp"], "com.emma.robot.control." + main, *args],
                          check=True, capture_output=True, encoding="utf-8", timeout=timeout)
    return done.stdout.splitlines()


def beam(jvm, comps: dict, mode: str, tau, *, sigma: float = 0.2, unsteered="1,0,0,0", eq: bool = True,
         talk=None, keys=ORDER) -> tuple[dict, dict]:
    """Run ArrayBeam on a room: the first key is the mixture (the leader),
    every other key follows with the leader's filter. -> (outputs, counters)."""
    n = comps[keys[0]].shape[1]
    work = jvm["work"]
    np.stack([comps[k] for k in keys]).astype("<f4").tofile(work / "in.f32")
    args = [str(work / "in.f32"), str(len(keys)), str(n), str(work / "out.f32"), mode,
            "none" if tau is None else ",".join("%.6f" % v for v in tau), str(sigma), unsteered,
            "1" if eq else "0"]
    if talk is not None:
        np.asarray(talk, np.uint8).tofile(work / "talk.bin")
        args.append(str(work / "talk.bin"))
    info = json.loads(run_java(jvm, "BeamHarness", *args)[-1])
    out = np.fromfile(work / "out.f32", "<f4").reshape(len(keys), n).astype(float)
    return dict(zip(keys, out)), info


def gains(comps: dict, out: dict, skip_s: float = 1.0) -> dict:
    """dB improvements of the beam over mic 0 (the raw microphone), the
    target's own gain, and the log-spectral distance of the direct target
    (damage: a beam that colours the voice reads > 1 dB)."""
    skip = int(skip_s * FS)
    o = {k: v[skip + LATENCY:] for k, v in out.items()}
    i = {k: v[0, skip:v.shape[1] - LATENCY] for k, v in comps.items()}
    res = {"sir": db(power(o["target"]) / power(o["interf"])) - db(power(i["target"]) / power(i["interf"])),
           "snr": db(power(o["target"]) / power(o["noise"])) - db(power(i["target"]) / power(i["noise"])),
           "target": db(power(o["target_direct"]) / power(i["target_direct"]))}
    res["lsd"] = lsd(o["target_direct"], i["target_direct"])
    return res


def lsd(est, ref) -> float:
    from scipy.signal import stft

    _, _, A = stft(ref, FS, nperseg=512)
    _, _, B = stft(est, FS, nperseg=512)
    pa, pb = np.abs(A) ** 2, np.abs(B) ** 2
    act = pa.sum(0) > 1e-3 * pa.sum(0).max()
    f = np.fft.rfftfreq(512, 1 / FS)
    band = (f > 100) & (f < 7000)
    g = pb[:, act].sum() / pa[:, act].sum()
    d = 10 * np.log10((pb[band][:, act] + 1e-12) / (g * pa[band][:, act] + 1e-12))
    return float(np.sqrt(np.mean(d ** 2)))
