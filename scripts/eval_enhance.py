"""Does speech enhancement (ENHANCE_ENABLED, GTCRN) help the VAD in a crowd,
and does it hurt the speech? Numbers for app/enhance.py's verdict.

    python scripts/eval_enhance.py                    # committed TTS (tests/data/wake)
    python scripts/eval_enhance.py --speech-dir DIR   # more/other 16 kHz mono wavs

A target talker (utterances with pauses) over other talkers (babble, 4
voices), stationary pink noise, or both, at several SNRs. The VAD is the
project's own Silero via sherpa-onnx with the call's settings
(threshold 0.5, VAD_SILENCE_MS, VAD_MIN_SPEECH_MS), fed 20 ms frames like
the page sends. Reference = the same VAD on the clean target.

Per condition, noisy vs enhanced:
  recall      fraction of the reference's speech frames the VAD calls speech
  false       fraction of the reference's SILENCE frames called speech
              (babble IS speech: here "false" = other people opening turns)
  end_ms      median delay from the target's turn end to the VAD closing
  si_sdr      of the stream vs the clean target, over the target's speech
Plus the damage on clean speech: SI-SDR and log-spectral distance of
GTCRN(clean) vs clean (a transparent enhancer reads > 20 dB and < 1 dB).

No customer audio: TTS only. Nothing is written.
"""
from __future__ import annotations

import argparse
import glob
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from tests import array_room as R  # noqa: E402

FS = 16000
FRAME = 320


def clips(folder: Path) -> tuple[list[Path], list[Path]]:
    files = sorted(Path(p) for p in glob.glob(str(folder / "*.wav")))
    if not files:
        raise SystemExit(f"no wavs in {folder}")
    target = [f for f in files if "zira" in f.name] or files[: max(1, len(files) // 2)]
    others = [f for f in files if f not in target] or files
    return target, others


def utterances(files, n, rng, level=0.05):
    """Utterances with 1.0-2.5 s pauses: turns to start AND end."""
    out = np.zeros(n)
    at = int(0.5 * FS)
    while at < n:
        x = R.load(files[int(rng.integers(len(files)))])
        end = min(n, at + len(x))
        out[at:end] = x[: end - at]
        at = end + int(rng.uniform(1.0, 2.5) * FS)
    active = np.abs(out) > 1e-3
    return out / (math.sqrt(float(np.mean(out[active] ** 2))) + 1e-12) * level


def babble(files, n, rng, voices=4):
    out = np.zeros(n)
    for _ in range(voices):
        parts, total = [], 0
        while total < n + FS:
            x = R.load(files[int(rng.integers(len(files)))])
            parts += [x, np.zeros(int(rng.uniform(0.05, 0.3) * FS))]
            total += len(parts[-2]) + len(parts[-1])
        x = np.concatenate(parts)
        shift = int(rng.integers(FS))
        out += x[shift:shift + n]
    return out


def pink(n, rng):
    f = np.fft.rfftfreq(n, 1 / FS)
    spec = np.fft.rfft(rng.normal(size=n)) / np.sqrt(np.maximum(f, 50.0))
    return np.fft.irfft(spec, n)


def scale_to(noise, target, snr_db):
    t = target[np.abs(target) > 1e-3]
    return noise * math.sqrt(float(np.mean(t ** 2)) / float(np.mean(noise ** 2)) * 10 ** (-snr_db / 10))


def vad_frames(x, gate: bool = False) -> np.ndarray:
    """Per 20 ms frame: is a speech segment open. gate=False: the bare
    detector; gate=True: the call's own VadGate around it (speaker focus at
    VAD_FOCUS_RATIO, no loudness floor -- the robot's far profile)."""
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(ROOT / settings.vad_model) if not Path(settings.vad_model).is_absolute() \
        else settings.vad_model
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = settings.vad_silence_ms / 1000
    cfg.silero_vad.min_speech_duration = max(0, settings.vad_min_speech_ms) / 1000
    cfg.silero_vad.max_speech_duration = 60.0
    cfg.sample_rate = FS
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=70)
    out = np.zeros(len(x) // FRAME, bool)
    if gate:
        from app.vad_gate import VadGate

        g = VadGate(vad, prefix_padding_ms=settings.vad_prefix_padding_ms, min_rms=0.0,
                    floor_grace_s=0.0, focus_ratio=settings.vad_focus_ratio)
        pcm = np.clip(np.round(x * 32768), -32768, 32767).astype("<i2")
        for k in range(len(out)):
            g.feed(pcm[k * FRAME:(k + 1) * FRAME].tobytes())
            out[k] = g.in_speech
        return out
    for k in range(len(out)):
        vad.accept_waveform(x[k * FRAME:(k + 1) * FRAME].astype(np.float32))
        out[k] = vad.is_speech_detected()
        while not vad.empty():
            vad.pop()
    return out


def enhance(x) -> np.ndarray:
    from app import enhance as E

    d = E.make_denoiser(ROOT / settings.enhance_model if not Path(settings.enhance_model).is_absolute()
                        else settings.enhance_model)
    if d is None:
        raise SystemExit("no GTCRN model: python scripts/fetch_enhance_model.py")
    parts = []
    for k in range(0, len(x), FRAME):
        parts.append(np.asarray(d(x[k:k + FRAME].astype(np.float32), FS).samples))
    parts.append(np.asarray(d.flush().samples))
    y = np.concatenate(parts)[: len(x)]
    return np.pad(y, (0, len(x) - len(y)))


def si_sdr(est, ref, mask=None):
    if mask is not None:
        est, ref = est[mask], ref[mask]
    alpha = float(np.dot(est, ref) / (np.dot(ref, ref) + 1e-12))
    target = alpha * ref
    return 10 * math.log10(float(np.dot(target, target)) / (float(np.sum((est - target) ** 2)) + 1e-12))


def lsd(est, ref):
    from scipy.signal import stft

    _, _, A = stft(ref, FS, nperseg=512)
    _, _, B = stft(est, FS, nperseg=512)
    pa, pb = np.abs(A) ** 2, np.abs(B) ** 2
    act = pa.sum(0) > 1e-3 * pa.sum(0).max()
    d = 10 * np.log10((pb[:, act] + 1e-10) / (pa[:, act] + 1e-10))
    return float(np.sqrt(np.mean(d ** 2)))


def turn_ends(ref: np.ndarray) -> list[int]:
    return [k for k in range(1, len(ref)) if ref[k - 1] and not ref[k]]


def score(vad: np.ndarray, ref: np.ndarray) -> dict:
    sp, si = ref, ~ref
    delays = []
    for k in turn_ends(ref):
        j = k
        while j < len(vad) and vad[j]:
            j += 1
        delays.append((j - k) * FRAME / FS * 1000)
    return {"recall": float(vad[sp].mean()) if sp.any() else float("nan"),
            "false": float(vad[si].mean()) if si.any() else float("nan"),
            "end_ms": float(np.median(delays)) if delays else float("nan")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speech-dir", type=Path, default=R.WAKE)
    ap.add_argument("--seconds", type=float, default=40.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--snr", type=float, nargs="+", default=[0.0, 5.0, 10.0, 20.0])
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    target_files, other_files = clips(args.speech_dir)
    n = int(args.seconds * FS)
    clean = utterances(target_files, n, rng)
    ref = vad_frames(clean)
    t0 = time.perf_counter()
    clean_enh = enhance(clean)
    rtf = (time.perf_counter() - t0) / args.seconds
    speech = np.repeat(ref, FRAME)[:n]
    speech = np.pad(speech, (0, n - len(speech)))
    print(f"material: {args.speech_dir} target={len(target_files)} other={len(other_files)} "
          f"{args.seconds:.0f}s, reference speech {ref.mean():.0%} of frames, GTCRN RTF {rtf:.3f}")
    print(f"clean speech through GTCRN: SI-SDR {si_sdr(clean_enh, clean, speech):.1f} dB, "
          f"LSD {lsd(clean_enh, clean):.2f} dB, VAD on it {score(vad_frames(clean_enh), ref)}")
    bab = babble(other_files, n, rng)
    pk = pink(n, rng)
    print(f"{'condition':22s} {'':4s} {'recall':>7s} {'false':>7s} {'end_ms':>7s} {'si_sdr':>7s}   "
          f"| VadGate (focus {settings.vad_focus_ratio}): {'recall':>6s} {'false':>6s} {'end_ms':>6s}")
    for snr in args.snr:
        for name, noise in (("babble", scale_to(bab, clean, snr)), ("pink", scale_to(pk, clean, snr)),
                            ("babble+pink", scale_to(bab, clean, snr + 3) + scale_to(pk, clean, snr + 3))):
            noisy = clean + noise
            enh = enhance(noisy)
            for label, x in (("in", noisy), ("enh", enh)):
                s = score(vad_frames(x), ref)
                g = score(vad_frames(x, gate=True), ref)
                print(f"{name + ' @' + format(snr, 'g') + 'dB':22s} {label:4s} {s['recall']:7.2f} {s['false']:7.2f} "
                      f"{s['end_ms']:7.0f} {si_sdr(x, clean, speech):7.1f}   |{'':33s}"
                      f"{g['recall']:6.2f} {g['false']:6.2f} {g['end_ms']:6.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
