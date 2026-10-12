"""What are channels 4-7 of the Bothlent array? Reads an ARRAY_CAPTURE8 test file.

    python scripts/array_channels_report.py                 # newest data/array_capture8/ARRAY8-TEST-*.s16le
    python scripts/array_channels_report.py FILE.s16le      # a given one (8 ch, 16 kHz, s16le interleaved)
    python scripts/array_channels_report.py --delete        # ... and delete it (and its .json) after reading

The capture is room audio for one test; the server deletes it after
ARRAY_CAPTURE8_KEEP_MIN anyway. Record it as the steps in app/array_audio.py
say: ~3 s of a person talking 1 m in front, ~3 s of Emma (the robot's own
speaker) talking, ~2 s of silence -- each kind of channel gives itself away
in a different part.

Per channel: level, noise floor (10th percentile of 32 ms frame RMS),
dynamic range, exact-zero share, DC. For each of 4-7 against 0-3:
  * best normalised cross-correlation within +-2 ms, and its lag
  * coherence (MSC) in three bands while somebody talks
  * how much of it a short FIR of channels 0-3 explains (R^2), over the
    loud frames AND over the QUIET ones -- the decisive part: a channel
    computed from 0-3 (a fixed beam, a sum) is explained in the quiet frames
    too, because it carries 0-3's own sensor noise; a separate microphone's
    self-noise is its own, so its quiet-frame R^2 is low however coherent
    the room makes the loud frames.
and a hedged verdict: silent / duplicate / derived from 0-3 (linear) /
processed (correlated, not linear: NS, AGC, AEC output?) / another mic /
playback reference (a digital floor that wakes up when the speaker plays,
with the mics hearing it a few ms LATER -- the reference an echo canceller
needs, and the most valuable answer this test can give).

Numbers only on stdout; nothing is written.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FS = 16000
CH = 8
FRAME = 512


def newest() -> Path | None:
    folder = ROOT / "data" / "array_capture8"
    files = sorted(folder.glob("ARRAY8-TEST-*.s16le"))
    return files[-1] if files else None


def load(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<i2")
    n = len(raw) // CH
    return raw[: n * CH].reshape(n, CH).T.astype(np.float64) / 32768.0


def db(x: float) -> float:
    return 20 * math.log10(max(x, 1e-10))


def frame_rms(x: np.ndarray) -> np.ndarray:
    n = len(x) // FRAME
    return np.sqrt(np.mean(x[: n * FRAME].reshape(n, FRAME) ** 2, axis=1))


def xcorr(a: np.ndarray, b: np.ndarray, max_lag: int = 32) -> tuple[float, int]:
    """(best normalised correlation, lag in samples; + = b later than a)."""
    a = a - a.mean()
    b = b - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0, 0
    n = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    c = np.fft.irfft(np.conj(np.fft.rfft(a, n)) * np.fft.rfft(b, n), n)
    lags = np.r_[0:max_lag + 1, -max_lag:0]
    vals = np.r_[c[: max_lag + 1], c[-max_lag:]] / (na * nb)
    k = int(np.argmax(np.abs(vals)))
    return float(vals[k]), int(lags[k])


def coherence(a: np.ndarray, b: np.ndarray, bands=((300, 1000), (1000, 3000), (3000, 6000))) -> list[float]:
    from scipy.signal import coherence as msc

    with np.errstate(invalid="ignore", divide="ignore"):
        f, c = msc(a, b, fs=FS, nperseg=512)
    c = np.nan_to_num(c)                    # a silent channel: no coherence, not NaN
    return [float(np.mean(c[(f >= lo) & (f < hi)])) for lo, hi in bands]


def fir_r2(target: np.ndarray, sources: np.ndarray, mask: np.ndarray, taps: int = 16) -> float:
    """R^2 of target from a +-taps FIR of the sources, on the frames in mask."""
    n = len(target) // FRAME
    idx = np.nonzero(mask[:n])[0]
    if len(idx) < 4:
        return float("nan")
    rows_t, rows_x = [], []
    for k in idx:
        lo, hi = k * FRAME + taps, (k + 1) * FRAME - taps
        if lo < taps or hi > len(target) - taps:
            continue
        cols = [sources[m][lo - s:hi - s] for m in range(len(sources)) for s in range(-taps, taps + 1)]
        rows_x.append(np.stack(cols, axis=1))
        rows_t.append(target[lo:hi])
    if not rows_t:
        return float("nan")
    X = np.concatenate(rows_x)
    y = np.concatenate(rows_t)
    if np.dot(y, y) == 0:
        return float("nan")
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    return float(1 - np.dot(resid, resid) / np.dot(y - y.mean(), y - y.mean()))


def analyse(x: np.ndarray) -> dict:
    mics = x[:4]
    level = np.mean([frame_rms(m) for m in mics], axis=0)
    loud = level >= np.percentile(level, 70)
    quiet = level <= np.percentile(level, 20)
    out = {"seconds": round(x.shape[1] / FS, 2), "channels": []}
    for c in range(CH):
        r = frame_rms(x[c])
        out["channels"].append({
            "ch": c, "rms_dbfs": round(db(float(np.sqrt(np.mean(x[c] ** 2)))), 1),
            "floor_dbfs": round(db(float(np.percentile(r, 10))), 1),
            "range_db": round(db(float(np.percentile(r, 90))) - db(float(np.percentile(r, 10))), 1),
            "zero_share": round(float(np.mean(x[c] == 0)), 3), "dc": round(float(x[c].mean()), 5)})
    mic_floor = float(np.median([ch["floor_dbfs"] for ch in out["channels"][:4]]))
    pair_coh = [coherence(mics[i], mics[j]) for i in range(4) for j in range(i + 1, 4)]
    out["mic_pair_coherence"] = [round(float(np.mean([p[b] for p in pair_coh])), 2) for b in range(3)]
    out["verdicts"] = {}
    for c in range(4, CH):
        info = out["channels"][c]
        best = [xcorr(mics[m], x[c]) for m in range(4)]
        corr, lag = max(best, key=lambda v: abs(v[0]))
        info.update(best_corr=round(corr, 3), lag=lag,
                    coherence=[round(v, 2) for v in coherence(mics[0], x[c])],
                    r2_loud=round(fir_r2(x[c], mics, loud), 3), r2_quiet=round(fir_r2(x[c], mics, quiet), 3))
        dup = [j for j in range(CH) if j != c and np.array_equal(x[j], x[c])]
        active = frame_rms(x[c]) > 10 ** (-80 / 20)
        # A playback reference leads the mics by the speaker's path and the
        # output buffering: search 0..50 ms, only where the channel is awake.
        awake = np.repeat(active, FRAME)[: x.shape[1]]
        ref_corr, ref_lag = 0.0, 0
        if awake.any():
            sel = np.nonzero(awake)[0]
            ref_corr, ref_lag = xcorr(x[c][sel[0]:sel[-1] + 1], mics[0][sel[0]:sel[-1] + 1], max_lag=800)
            # xcorr(a=this channel, b=mic 0): + lag = the mics hear it later
        info.update(ref_corr=round(ref_corr, 3), ref_lag_ms=round(ref_lag / FS * 1000, 1))
        if info["rms_dbfs"] < -90 or info["zero_share"] > 0.99:
            verdict = "silent (exact zeros / nothing)"
        elif dup:
            verdict = "duplicate of ch %d" % dup[0]
        elif info["floor_dbfs"] < -85 and 0.05 < active.mean() < 0.95 and ref_lag > 0 and abs(ref_corr) > 0.2:
            verdict = ("playback reference? a digital floor that wakes up with sound, and the mics hear it "
                       "%.1f ms LATER (what an echo canceller needs)" % (ref_lag / FS * 1000))
        elif info["r2_quiet"] >= 0.9:
            verdict = "derived from 0-3 (a linear mix even of their self-noise: a fixed beam or sum)"
        elif max(info["coherence"]) >= 0.6 and info["floor_dbfs"] < mic_floor - 6:
            verdict = ("processed output (follows 0-3 while people talk, floor %.0f dB under the mics: "
                       "noise suppression / AEC?)" % (mic_floor - info["floor_dbfs"]))
        elif max(info["coherence"]) >= 0.4 and abs(info["floor_dbfs"] - mic_floor) <= 6:
            verdict = "another microphone (its own self-noise, coherent with 0-3 like the mics are)"
        else:
            verdict = "unclear -- read the numbers"
        out["verdicts"][c] = verdict
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--delete", action="store_true", help="delete the capture (and its .json) after reading")
    ap.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = ap.parse_args()
    path = args.file or newest()
    if path is None or not path.is_file():
        print("no capture: run the ARRAY_CAPTURE8 steps first (data/array_capture8/ is empty)")
        return 1
    result = analyse(load(path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(f"{path.name}: {result['seconds']} s, 8 channels")
        print(f"mic pairs 0-3 coherence (300-1k / 1-3k / 3-6k Hz): {result['mic_pair_coherence']}")
        print(f"{'ch':>2} {'rms':>7} {'floor':>7} {'range':>6} {'zeros':>6} {'corr':>6} {'lag':>4} "
              f"{'coherence':>18} {'R2loud':>7} {'R2quiet':>7}")
        for ch in result["channels"]:
            extra = ""
            if ch["ch"] >= 4:
                extra = (f" {ch['best_corr']:6.3f} {ch['lag']:4d} {str(ch['coherence']):>18} "
                         f"{ch['r2_loud']:7.3f} {ch['r2_quiet']:7.3f}")
            print(f"{ch['ch']:>2} {ch['rms_dbfs']:7.1f} {ch['floor_dbfs']:7.1f} {ch['range_db']:6.1f} "
                  f"{ch['zero_share']:6.3f}{extra}")
        for c, verdict in result["verdicts"].items():
            print(f"ch {c}: {verdict}")
    if args.delete:
        for victim in (path, path.with_suffix(".json")):
            victim.unlink(missing_ok=True)
        print(f"deleted {path.name} (+ .json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
