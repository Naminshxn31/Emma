"""
One enrolled voice: the voice print, the score, the verdict -- one copy for the PC bench
(scripts/voice_lock_try.py), the page where a person records their voice (/voice-lock,
app/one_voice_page.py) and the door in the live audio path (`Door` below, ONE_VOICE_PROFILES).

Owner 19 Nov 2026: "ทำเป็นที่ให้พูดเพื่อบันทึกเสียงหน่อย เพราะจะให้ไมค์มันจับแค่เสียงคนนั้น". The 5 Nov
voice lock (removed, CLAUDE.md "ฟังเสียงคนเดียวเอาออกแล้ว") listened through the robot's far-field
array; this one is meant for a close-talk wireless mic (the GCK). Measurements behind every number
here are in scripts/voice_lock_try.py `bench` and CHANGELOG 19 Nov.

**Personal data (PDPA).** A voice print identifies a person the way a face does, and saved
utterances are recordings of people. Everything lives under data/voice-lock/ (gitignored, in no
deploy bundle); nothing here prints, logs or sends a vector or a sample -- callers get lengths,
levels, scores and decisions. Prints load with allow_pickle=False and carry the model pin: a print
from other weights is another space, refused.

Heavy libraries (sherpa-onnx, soundfile, scipy) load on first use only: importing this module costs
numpy, so a server with the door off pays nothing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

logger = logging.getLogger("condo_voice.one_voice")

ROOT = Path(__file__).resolve().parent.parent
RATE = 16000
FOLDER = ROOT / "data" / "voice-lock"
PRINTS = FOLDER / "prints"
UTTERANCES = FOLDER / "utterances"
AUDIO_EXT = (".wav", ".flac", ".ogg", ".mp3")
#: The GCK wireless receiver's name on this PC: "Microphone (GCK)". 19 Nov this was guessed as
#: "LCS_USB_AUDIO" (USB 0a67:8524, then the only USB input) -- wrong: 20 Nov the owner spoke into the
#: GCK and "Microphone (GCK)" read peak 0.43-0.80 while LCS_USB_AUDIO stayed exact zero on every API.
#: The "transmitter is off" verdicts of 19 Nov were measuring the wrong device.
GCK_NAMES = ("GCK",)
#: Labels `eval` counts as the enrolled person. Every other subfolder is somebody else.
GENUINE_LABELS = ("target", "self", "me")
#: Somebody else talking NEAR the mic, not into it: a loudness question, reported on its own and
#: never used to set the bar (which is against people speaking into the same mic).
NEARBY_LABELS = ("nearby",)

# ---- the decision ---------------------------------------------------------------------------

#: Default ACCEPT bar (cosine to the enrolled print). From `bench` on Mini LibriSpeech (26 English
#: readers, 30 s enrolment, 19 Nov), enrolled refused / others let in on AVERAGE / the ONE closest
#: other voice let in:
#:     window   bar 0.40          bar 0.50          bar 0.60
#:     1.2 s    0.7 / 1.2 / 55%   5.9 / 0.1 / 18%   19.9 / 0.0 / 4%
#:     2 s      0.0 / 1.1 / 62%   1.0 / 0.0 / 14%    6.2 / 0.0 / 0%
#:     0.5 s   28.4 / 0.5 / 36%  53.6 / 0.1 / 12%   83.8 / 0.0 / 0%
#: The average hides the case that matters: a lock meets one particular other person, and the
#: closest pairs of readers score 0.40-0.47 against each other's prints -- at 0.40 that person
#: gets in more often than not. 0.50 is the start; `eval` on the owner's own recordings through
#: the GCK is what should set it (each corpus reader also had their own mic, which flatters the
#: model: two people on ONE mic can only score closer).
DEFAULT_THRESHOLD = 0.50
#: Utterances shorter than this (speech after trimming) are "short": reported apart, never used
#: to choose the threshold. "ค่ะ", "ใช่", "หยุด", "yes" are 0.3-0.7 s.
SHORT_S = 1.0
#: Below this there is nothing to embed (the extractor needs a few frames).
MIN_EMBED_S = 0.25
#: The verdict `eval` prints: on utterances >= SHORT_S, a threshold exists where the other person
#: passes at most MAX_FAR and the enrolled person is refused at most MAX_FRR.
MAX_FAR = 0.05
MAX_FRR = 0.10
#: The grid `eval` prints (the recommendation itself is searched on a 0.01 grid).
TABLE = tuple(round(0.20 + 0.05 * i, 2) for i in range(11))
#: Fewer sentences than this on either side and `eval` says UNKNOWN: one line of the other person
#: "not getting in" proves nothing (review 19 Nov).
MIN_N = 10
#: An utterance too short or too quiet to embed scores this in `eval`: live it is never accepted.
NO_SCORE = -1.0

# ---- enrolment ----------------------------------------------------------------------------

#: The print is the mean of these windows of speech (the length of the removed designated-voice
#: prints of 5 Nov: 3 s clean enrolment vs a 2 s test window, 94% target / 5% other at 0.35, TTS).
ENROLL_WINDOW_S = 3.0
#: Less speech than this and the print is refused (it needs several windows to be a mean at all).
ENROLL_MIN_SPEECH_S = 10.0
#: An enrolment split in two whose halves are closer than this is one voice; further apart is two
#: people (somebody else talked during the recording). Measured 5 Nov on Mini LibriSpeech 3 s
#: windows (app/voice_designated.py, removed; backup in .tmp/removed-voice-lock-2026-11-05/):
#: one voice refused 2/312, a 50/50 blend taken 0/78. Not measured on Thai.
ONE_VOICE = 0.60
#: The smaller group up to this share is a remark from somebody else: left out of the print.
REMARK_SHARE = 0.25

# ---- signal ---------------------------------------------------------------------------------

#: |sample| at or above this counts as clipped (the GCK clipped 5-7% of voice seconds on the robot).
CLIP_AT = 0.99
#: A recording whose peak never reaches this is not speech, whatever the VAD says (dither).
SILENT_PEAK = 0.002


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    if not n > 0:
        raise ValueError("empty vector")
    return v / n


def score(vec, voice) -> float:
    """Cosine between an utterance's embedding and the enrolled print."""
    return float(unit(vec) @ unit(voice))


def decide(s: float | None, speech_s: float, threshold: float) -> tuple[str, bool]:
    """(ACCEPT | REJECT | NONE, is_short). NONE = too short to embed: no score at all."""
    short = speech_s < SHORT_S
    if s is None:
        return "NONE", short
    return ("ACCEPT" if s >= threshold else "REJECT"), short


def bucket(speech_s: float) -> str:
    if speech_s < SHORT_S:
        return "<1s"
    return "1-2s" if speech_s < 2.0 else ">=2s"


def summary(values) -> dict | None:
    v = np.asarray([x for x in values if x is not None], dtype=float)
    if not len(v):
        return None
    p10, p50, p90 = np.percentile(v, [10, 50, 90])
    return {"n": int(len(v)), "mean": float(v.mean()), "p10": float(p10), "p50": float(p50),
            "p90": float(p90), "min": float(v.min()), "max": float(v.max())}


def far_frr(genuine, impostor, t: float) -> tuple[float | None, float | None]:
    """FAR = share of other people's utterances AT OR ABOVE t (let in); FRR = share of the
    enrolled person's BELOW t (refused). None where that side has no utterances."""
    g = np.asarray(genuine, dtype=float)
    i = np.asarray(impostor, dtype=float)
    far = float((i >= t).mean()) if len(i) else None
    frr = float((g < t).mean()) if len(g) else None
    return far, frr


def eer(genuine, impostor) -> tuple[float, float] | None:
    """(equal error rate, the threshold where FAR and FRR meet). Both sides needed."""
    g = np.asarray(genuine, dtype=float)
    i = np.asarray(impostor, dtype=float)
    if not len(g) or not len(i):
        return None
    best = None
    for t in np.unique(np.concatenate([g, i, [1.01]])):
        far, frr = far_frr(g, i, float(t))
        gap = abs(far - frr)
        if best is None or gap < best[0] - 1e-12:
            best = (gap, (far + frr) / 2, float(t))
    return best[1], best[2]


def recommend(genuine, impostor, max_far: float = MAX_FAR, step: float = 0.01) -> dict | None:
    """The LOWEST threshold (fewest of the enrolled person's words refused) at which other people
    pass at most `max_far`. Lower bars let more people in, so this is the most forgiving bar that
    still keeps them out. None when there is nobody else to keep out."""
    i = np.asarray(impostor, dtype=float)
    if not len(i):
        return None
    for k in range(0, int(round(1.0 / step)) + 2):
        t = round(k * step, 4)
        far, frr = far_frr(genuine, i, t)
        if far <= max_far:
            return {"threshold": t, "far": far, "frr": frr}
    return None


def upper95(k: int, n: int) -> float:
    """One-sided 95% upper bound (Clopper-Pearson) on a rate seen k times in n: 0 of 15 -> 18%."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    if k == 0:
        return float(1 - 0.05 ** (1 / n))
    from scipy.stats import beta

    return float(beta.ppf(0.95, k + 1, n - k))


def judge(rec: dict | None, max_frr: float = MAX_FRR) -> str:
    """WORKS / DOES NOT WORK / UNKNOWN for one recommendation."""
    if rec is None or rec.get("frr") is None:
        return "UNKNOWN"
    return "WORKS" if rec["frr"] <= max_frr else "DOES NOT WORK"


def _halves(vecs) -> tuple:
    """Two means from the two windows furthest apart: (their cosine, the smaller group's share,
    the larger group as a mask). One group: cosine 1, share 0. (From app/voice_designated.py of
    5 Nov, removed; the measurement is in its ONE_VOICE comment.)"""
    sims = vecs @ vecs.T
    i, j = np.unravel_index(int(np.argmin(sims)), sims.shape)
    a, b = vecs[i], vecs[j]
    near_a = np.ones(len(vecs), dtype=bool)
    for _ in range(6):
        near_a = (vecs @ a) >= (vecs @ b)
        if near_a.all() or not near_a.any():
            return 1.0, 0.0, np.ones(len(vecs), dtype=bool)
        a, b = unit(vecs[near_a].mean(axis=0)), unit(vecs[~near_a].mean(axis=0))
    larger = near_a if near_a.sum() >= (~near_a).sum() else ~near_a
    return float(a @ b), float((~larger).mean()), larger


def build_print(window_vecs) -> dict:
    """The print from enrolment windows, or {"ok": False, "why": ...}. Never returns a vector
    for printing: callers save `vector` and show the numbers."""
    vecs = np.asarray([unit(v) for v in window_vecs], dtype=np.float32)
    if len(vecs) < 2:
        return {"ok": False, "why": "too_few_windows", "windows": int(len(vecs))}
    first = None
    kept = vecs
    for _ in range(3):                        # a remark from a third person too
        cosine, share, larger = _halves(kept)
        first = first or {"halves": round(cosine, 3), "smaller": round(share, 3)}
        if cosine >= ONE_VOICE:
            break
        if share > REMARK_SHARE:
            return {"ok": False, "why": "mixed_voices", "windows": int(len(vecs)), **first}
        kept = kept[larger]
    mean = unit(kept.mean(axis=0))
    self_scores = kept @ mean
    return {"ok": True, "vector": mean, "windows": int(len(vecs)), "kept": int(len(kept)),
            "worst": round(float(self_scores.min()), 3), "self_mean": round(float(self_scores.mean()), 3),
            **first}


def level_stats(x) -> dict:
    """rms (dBFS) over the voiced frames, peak, clipped and exact-zero shares."""
    x = np.asarray(x, dtype=np.float32)
    if not len(x):
        return {"rms_db": None, "peak": 0.0, "clip": 0.0, "zero": 1.0}
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return {"rms_db": round(20 * np.log10(rms), 1) if rms > 0 else None,
            "peak": round(float(np.abs(x).max()), 4),
            "clip": round(float((np.abs(x) >= CLIP_AT).mean()), 4),
            "zero": round(float((x == 0).mean()), 4)}


def liveness(peaks) -> str:
    """Verdict over per-second peaks of a live test."""
    p = np.asarray(peaks, dtype=float)
    if not len(p) or float(p.max()) == 0.0:
        return "NO SOUND"
    if float(p.max()) < SILENT_PEAK:
        return "DITHER ONLY"
    return "ALIVE"


def speech_windows(x, win_s: float = ENROLL_WINDOW_S, rate: int = RATE) -> list:
    n = int(win_s * rate)
    return [x[i: i + n] for i in range(0, len(x) - n + 1, n)]


def short_policy(rows: list, threshold: float, margin: float = 0.10, level_db: float = 6.0) -> dict | None:
    """An offline estimate for short-word rules that are NOT wired anywhere. Both pass a short
    utterance only inside N s after an accepted sentence; then

      "level"        its level is within `level_db` of the enrolled person's accepted sentences
                     (a close-talk mic hears its wearer loud and a bystander quiet), or
      "level+score"  that AND it scores >= threshold - margin.

    The time window cannot be measured from separate test sessions, so every short word is assumed
    to land inside one: the best case for the enrolled person and the WORST case for everybody
    else (5 Nov: the 6 s `reply_burst` window alone let every voice in). Per rule and label:
    (passed, n). Short words too short to embed count as failing "level+score"."""
    ref = [r["rms_db"] for r in rows if r.get("genuine") and r["speech_s"] >= SHORT_S
           and r.get("score") is not None and r["score"] >= threshold and r.get("rms_db") is not None]
    if not ref:
        return None
    ref_db = float(np.median(ref))
    out: dict = {"ref_db": round(ref_db, 1), "level": {}, "level+score": {}}
    for r in rows:
        if r["speech_s"] >= SHORT_S:
            continue
        loud = r.get("rms_db") is not None and abs(r["rms_db"] - ref_db) <= level_db
        scored = loud and r.get("score") is not None and r["score"] >= threshold - margin
        for rule, ok in (("level", loud), ("level+score", scored)):
            passed, n = out[rule].get(r["label"], (0, 0))
            out[rule][r["label"]] = (passed + int(ok), n + 1)
    return out


def report(rows: list, threshold: float, *, max_far: float = MAX_FAR, max_frr: float = MAX_FRR) -> tuple[list, dict]:
    """The eval report from scored rows: (text lines, numbers). Pure: rows are dicts with label,
    genuine, speech_s, rms_db, clip, score (None = too short to embed)."""
    lines: list = []
    labels = sorted({r["label"] for r in rows}, key=lambda s: (s not in GENUINE_LABELS, s))
    lines.append(f"{'label':<10}{'length':<7}{'n':>4}{'p10':>7}{'p50':>7}{'p90':>7}{'min':>7}{'max':>7}"
                 f"{'level p50':>11}{'clipped':>9}")
    for lab in labels:
        for b in ("<1s", "1-2s", ">=2s"):
            rs = [r for r in rows if r["label"] == lab and bucket(r["speech_s"]) == b]
            if not rs:
                continue
            s = summary(r["score"] for r in rs)
            lv = summary(r["rms_db"] for r in rs)
            clipped = sum(1 for r in rs if r.get("clip", 0) > 0.001)
            sc = (f"{s['p10']:>7.2f}{s['p50']:>7.2f}{s['p90']:>7.2f}{s['min']:>7.2f}{s['max']:>7.2f}"
                  if s else f"{'(too short to score)':>35}")
            lv_txt = f"{lv['p50']:>7.1f} dB" if lv else f"{'-':>10}"
            lines.append(f"{lab:<10}{b:<7}{len(rs):>4}{sc}{lv_txt:>11}{clipped:>9}")

    def scores(pick, long_):
        # Too short / too quiet to embed = never accepted live: it counts, as a refusal (review
        # 19 Nov: dropping it made the enrolled person's short words look better than they are).
        return [NO_SCORE if r["score"] is None else r["score"] for r in rows if pick(r)
                and ((r["speech_s"] >= SHORT_S) == long_)]

    genuine = lambda r: r["genuine"]                                    # noqa: E731
    others = [lab for lab in labels if lab not in GENUINE_LABELS and not any(
        r["genuine"] for r in rows if r["label"] == lab)]
    # The bar is set against people speaking INTO the mic -- every label but the bystanders, whom a
    # loudness bar answers (review 19 Nov: only a folder named exactly 'other' used to count).
    same_mic = [lab for lab in others if lab not in NEARBY_LABELS]
    hard_pick = lambda r: r["label"] in same_mic                        # noqa: E731
    hard_name = "+".join(same_mic) or "another person on the mic"
    numbers: dict = {"threshold": threshold, "against": same_mic}
    for long_, title in ((True, f"utterances >= {SHORT_S:g} s"), (False, f"short words < {SHORT_S:g} s")):
        g = scores(genuine, long_)
        lines.append("")
        lines.append(f"FRR / FAR per threshold -- {title}   (enrolled n={len(g)}; "
                     + ", ".join(f"{lab} n={len(scores(lambda r, L=lab: r['label'] == L, long_))}" for lab in others) + ")")
        lines.append(f"{'bar':>6}{'FRR enrolled':>14}" + "".join(f"{'FAR ' + lab:>14}" for lab in others))
        for t in TABLE:
            _, frr = far_frr(g, [], t)
            row = f"{t:>6.2f}{_pct(frr):>14}"
            for lab in others:
                far, _ = far_frr([], scores(lambda r, L=lab: r["label"] == L, long_), t)
                row += f"{_pct(far):>14}"
            lines.append(row + ("   <- current" if abs(t - threshold) < 1e-9 else ""))
        imp = scores(hard_pick, long_)
        e = eer(g, imp)
        rec = recommend(g, imp, max_far)
        key = "long" if long_ else "short"
        numbers[key] = {"eer": e, "recommend": rec, "n_genuine": len(g), "n_hard": len(imp)}
        if e:
            lines.append(f"  EER vs {hard_name}: {e[0] * 100:.1f}% at {e[1]:.2f}")
        if rec:
            lines.append(f"  lowest bar with FAR({hard_name}) <= {max_far * 100:.0f}%: {rec['threshold']:.2f}"
                         f" -> enrolled refused {_pct(rec['frr'])}")
        far, frr = far_frr(g, imp, threshold)
        lines.append(f"  at the current bar {threshold:.2f}: enrolled refused {_pct(frr)}, {hard_name} let in {_pct(far)}")
    long_rec = numbers["long"]["recommend"]
    n_g, n_h = numbers["long"]["n_genuine"], numbers["long"]["n_hard"]
    verdict = judge(long_rec, max_frr) if min(n_g, n_h) >= MIN_N else "UNKNOWN"
    numbers["verdict"] = verdict
    lines.append("")
    if verdict == "UNKNOWN":
        lines.append(f"VERDICT: UNKNOWN -- need >= {MIN_N} utterances >= {SHORT_S:g} s from the enrolled person AND "
                     f"from another person speaking INTO the mic (have {n_g} and {n_h}"
                     + ("" if same_mic else "; no such folder -- 'nearby' does not count") + ").")
    else:
        # The bar was fitted on these very utterances, and "0 of 15 got in" is not "never": the
        # one-sided 95% upper bound of the other person's real pass rate goes with the verdict.
        let_in = sum(1 for s in scores(hard_pick, True) if s >= long_rec["threshold"])
        up = upper95(let_in, n_h)
        numbers["far_upper95"] = up
        numbers["provisional"] = up > max_far
        lines.append(f"VERDICT for sentences >= {SHORT_S:g} s: {verdict} -- at {long_rec['threshold']:.2f} the enrolled "
                     f"person is refused {_pct(long_rec['frr'])} of {n_g} (bar: <= {max_frr * 100:.0f}%) while {hard_name} "
                     f"passes {let_in} of {n_h} (bar: <= {max_far * 100:.0f}%).")
        if up > max_far:
            lines.append(f"  PROVISIONAL: with {let_in} of {n_h} let in, the true rate could still be up to "
                         f"{up * 100:.0f}% (95% bound); the bar was also fitted on these same utterances. "
                         "More lines (and a second session) make it firmer.")
        lines.append("  That is ONE other person. Among 26 LibriSpeech readers the closest other voice averaged "
                     "0.40-0.47 against a print (at 0.40 it got in 55-100% of the time): test with the most "
                     "similar-sounding person you have, and keep a margin over this bar.")
    srec = numbers["short"]["recommend"]
    if srec and srec.get("frr") is not None:
        lines.append(f"Short words: at the bar that keeps {hard_name} to <= {max_far * 100:.0f}% ({srec['threshold']:.2f}) "
                     f"the enrolled person's short words are refused {_pct(srec['frr'])} -- "
                     + ("usable" if srec["frr"] <= 0.30 else "NOT usable: short words cannot tell who is speaking"))
    # Level: a close-talk mic hears its wearer loud and a bystander quiet -- a different question
    # from WHO, and the one a 'nearby' recording answers.
    near = [r["rms_db"] for r in rows if r["label"] == "nearby" and r.get("rms_db") is not None]
    mine = [r["rms_db"] for r in rows if r["genuine"] and r.get("rms_db") is not None]
    if near and mine:
        bar = (np.percentile(mine, 5) + np.percentile(near, 95)) / 2
        kept = float((np.asarray(mine) >= bar).mean())
        let = float((np.asarray(near) >= bar).mean())
        numbers["level"] = {"bar_db": round(float(bar), 1), "enrolled_kept": kept, "nearby_let_in": let}
        lines.append(f"Level alone: a loudness bar at {bar:.1f} dBFS keeps {_pct(kept)} of the enrolled "
                     f"person's utterances and lets in {_pct(let)} of 'nearby' (talking without the mic).")
    pol = short_policy(rows, long_rec["threshold"] if long_rec else threshold)
    if pol and (pol["level"] or pol["level+score"]):
        numbers["short_policy"] = pol
        lines.append("Short-word rules (IDEAS, not wired anywhere): a short word right after an accepted sentence "
                     f"passes if its level is within 6 dB of the wearer ({pol['ref_db']:.1f} dBFS) [level], and also "
                     "scores >= bar-0.10 [level+score]. Worst case for others (every short word assumed in the window):")
        for rule in ("level", "level+score"):
            parts = ", ".join(f"{lab} {p}/{n}" for lab, (p, n) in sorted(pol[rule].items()))
            lines.append(f"  {rule:<12} {parts}")
    return lines, numbers


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


# ---- files ------------------------------------------------------------------------------------

class PrintError(SystemExit):
    """Why a print, a name or a model is not usable. A SystemExit so the CLI exits with the
    message; the server catches it by name and tells the page."""


def safe_name(name: str) -> str:
    """A label for the print's file name: letters, digits, _ and -. A label, not necessarily the
    person's real name."""
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise PrintError(f"--name must be 1-40 of A-Z a-z 0-9 _ - (a label such as 'owner'), got {name!r}")
    if re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])", name):
        raise PrintError(f"{name!r} is a reserved device name on Windows -- pick another label")
    return name


def _model_pin() -> str:
    from app import voiceprint

    return voiceprint.MODEL_SHA256[:16]


def save_print(name: str, built: dict, meta: dict, folder: Path | None = None) -> Path:
    folder = PRINTS if folder is None else folder
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_name(name)}.npz"
    tmp = folder / f".{path.stem}.tmp.npz"
    info = {k: v for k, v in built.items() if k not in ("vector", "ok")}
    info.update(meta)
    np.savez(tmp, vector=np.asarray(built["vector"], dtype=np.float32),
             model=np.array(_model_pin(), dtype="U"), meta=np.array(json.dumps(info, ensure_ascii=False), dtype="U"))
    os.replace(tmp, path)
    return path


def load_print(name: str, folder: Path | None = None) -> tuple[np.ndarray, dict]:
    folder = PRINTS if folder is None else folder
    path = folder / f"{safe_name(name)}.npz"
    if not path.is_file():
        have = sorted(p.stem for p in folder.glob("*.npz")) if folder.is_dir() else []
        raise PrintError(f"no print named {name!r} in {folder} (have: {', '.join(have) or 'none'}) -- run enroll first")
    with np.load(path, allow_pickle=False) as z:        # 'U' arrays: never pickle
        vector, model, meta = z["vector"].astype(np.float32), str(z["model"]), json.loads(str(z["meta"]))
    if model != _model_pin():
        raise PrintError(f"{path.name} came from other model weights -- enrol again")
    return vector, meta


def read_audio(path: str | Path) -> np.ndarray:
    """Any file soundfile reads, as mono float32 at 16 kHz."""
    import soundfile as sf

    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    x = x.mean(axis=1)
    if sr != RATE:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(sr), RATE)
        x = resample_poly(x, RATE // g, int(sr) // g).astype(np.float32)
    return x


def write_wav(path: Path, x) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(np.asarray(x, dtype=np.float32), -1, 1), RATE, subtype="PCM_16")


# ---- models -----------------------------------------------------------------------------------

def load_embedder(threads: int = 2):
    """The pinned speaker model of app/voiceprint.py, or exit with what to run."""
    from app import voiceprint as vp

    path = vp.DEFAULT_MODEL
    if not path.is_file():
        raise PrintError(f"speaker model missing at {path} -- run: .venv\\Scripts\\python.exe scripts\\fetch_voiceprint_model.py")
    if vp._sha256(path) != vp.MODEL_SHA256:
        raise PrintError(f"{path.name} does not match its pin -- re-run scripts\\fetch_voiceprint_model.py --force")
    import sherpa_onnx

    ext = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(path), num_threads=threads, provider="cpu"))

    def embed(x) -> np.ndarray:
        stream = ext.create_stream()
        stream.accept_waveform(sample_rate=RATE, waveform=np.asarray(x, dtype=np.float32))
        stream.input_finished()
        return unit(ext.compute(stream))

    return embed


def trim(x) -> np.ndarray:
    """The quiet head and tail off (app/voiceprint.voiced: the one the server uses)."""
    from app import voiceprint as vp

    return vp.voiced(np.asarray(x, dtype=np.float32))


class Segmenter:
    """Silero (the same model as VAD_MODE=local) cutting a 16 kHz stream into utterances."""

    WINDOW = 512

    def __init__(self, min_silence_s: float = 0.5, min_speech_s: float = 0.15) -> None:
        import sherpa_onnx

        model = ROOT / "data" / "wake" / "silero_vad.onnx"
        if not model.is_file():
            raise PrintError(f"VAD model missing at {model} -- run: .venv\\Scripts\\python.exe scripts\\fetch_vad_model.py")
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(model)
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = min_silence_s
        cfg.silero_vad.min_speech_duration = min_speech_s
        cfg.silero_vad.max_speech_duration = 30.0
        cfg.sample_rate = RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)
        self.buf = np.zeros(0, dtype=np.float32)

    def feed(self, x) -> list:
        """[(start_s, samples)] of every utterance that closed."""
        self.buf = np.concatenate([self.buf, np.asarray(x, dtype=np.float32)])
        while len(self.buf) >= self.WINDOW:
            self.vad.accept_waveform(self.buf[: self.WINDOW])
            self.buf = self.buf[self.WINDOW:]
        return self._drain()

    def flush(self) -> list:
        self.vad.flush()
        return self._drain()

    def _drain(self) -> list:
        out = []
        while not self.vad.empty():
            seg = self.vad.front
            out.append((seg.start / RATE, np.asarray(seg.samples, dtype=np.float32)))
            self.vad.pop()
        return out


def judge_utterance(x, embed, voice, threshold: float) -> dict:
    """Everything `listen` and `eval` say about one utterance."""
    v = trim(x)
    speech_s = len(v) / RATE
    # Raw, not rounded: `decide` and `eval` must put 0.995 s on the same side of SHORT_S (review
    # 19 Nov: a rounded 1.00 was "short" live and a sentence in eval). Round when printing.
    row = {"speech_s": speech_s, **level_stats(v), "score": None}
    if speech_s >= MIN_EMBED_S and row["peak"] >= SILENT_PEAK:
        row["score"] = round(score(embed(v), voice), 4)
    row["decision"], row["short"] = decide(row["score"], speech_s, threshold)
    return row


def pcm_to_float(pcm16: bytes) -> np.ndarray:
    """PCM16 little-endian mono -> float32 in [-1, 1)."""
    return np.frombuffer(bytes(pcm16)[: len(pcm16) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


# ---- the store, shared by the CLI and the page ------------------------------------------------

def list_prints(folder: Path | None = None) -> list:
    """Who is on file -- numbers only, never a vector."""
    folder = PRINTS if folder is None else folder
    out = []
    for p in (sorted(folder.glob("*.npz")) if folder.is_dir() else []):
        if p.name.startswith("."):
            continue
        try:
            with np.load(p, allow_pickle=False) as z:
                meta, model = json.loads(str(z["meta"])), str(z["model"])
        except Exception:
            out.append({"name": p.stem, "unreadable": True})
            continue
        out.append({"name": p.stem, "speech_s": meta.get("speech_s"), "kept": meta.get("kept"),
                    "windows": meta.get("windows"), "worst": meta.get("worst"), "level_db": meta.get("level_db"),
                    "created": meta.get("created"), "source": meta.get("source"),
                    "current_model": model == _model_pin()})
    return out


def saved_counts(folder: Path | None = None) -> dict:
    folder = UTTERANCES if folder is None else folder
    if not folder.is_dir():
        return {}
    return {d.name: sum(1 for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT)
            for d in sorted(folder.iterdir()) if d.is_dir()}


def save_utterance(label: str, x, session: str, n: int, start_s: float, speech_s: float,
                   folder: Path | None = None) -> str:
    """One utterance as a wav under <folder>/<label>/ plus a line of its timing (no scores, no
    words) in that folder's index.csv. Returns the file name."""
    import csv

    folder = UTTERANCES if folder is None else folder
    d = folder / safe_name(label)
    name = f"{session}-{n:03d}.wav"
    write_wav(d / name, x)
    index = d / "index.csv"
    row = {"file": name, "session": session, "at_s": round(float(start_s), 2),
           "speech_s": round(float(speech_s), 3)}
    new = not index.exists()
    with open(index, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    return name


# ---- the mixed recording: record everyone now, say whose each one is later -------------------------
# Owner 20 Nov ("บันทึกตอนนี้เลย แล้วค่อยไปแยกเสียง" -> "อัดรวมก่อน แยกทีหลัง"): one recording while
# anybody talks into the mic, then each utterance played back and put under target / other / nearby
# by a person who heard it. Kept BESIDE UTTERANCES, never in it: eval_rows reads every folder under
# UTTERANCES that is not the enrolled person as "another person on the mic", so an unsorted pile
# there would be scored as other people -- the owner's own voice among them.
SORT_LABELS = ("target", "other", "nearby")
# {n:03d} grows a digit at the 1000th utterance of a run: 3 or more (review 20 Nov).
_SORT_NAME = re.compile(r"^[0-9-]+-[0-9]{3,}\.wav$")
# The unsorted index IS the list. Every writer of it (a recording page appending, a sorting page
# rewriting, forget) holds this: two pages at once used to lose a row appended between a sort's read
# and its rewrite, or fail the rewrite on Windows while the other page had the file open (review 20 Nov).
_sort_lock = threading.Lock()


def unsorted_dir(utterances: Path | None = None) -> Path:
    """Derived from UTTERANCES at call time: whatever moves the labelled folders moves this one."""
    return (UTTERANCES if utterances is None else utterances).parent / "unsorted"


def save_unsorted(x, session: str, n: int, start_s: float, speech_s: float) -> str:
    d = unsorted_dir()
    with _sort_lock:
        return save_utterance(d.name, x, session, n, start_s, speech_s, folder=d.parent)


def _index_rows(index: Path) -> list:
    import csv

    if not index.is_file():
        return []
    with open(index, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_index(index: Path, rows: list) -> None:
    import csv

    if not rows:
        index.unlink(missing_ok=True)
        return
    tmp = index.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["file", "session", "at_s", "speech_s"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in ("file", "session", "at_s", "speech_s")})
    tmp.replace(index)


def unsorted_list() -> list:
    """The mixed recording's utterances still waiting, oldest first: file name, length, when."""
    d = unsorted_dir()
    out = []
    for r in _index_rows(d / "index.csv"):
        f = str(r.get("file") or "")
        if _SORT_NAME.match(f) and (d / f).is_file():
            out.append({"file": f, "session": r.get("session", ""), "at_s": float(r.get("at_s") or 0),
                        "speech_s": float(r.get("speech_s") or 0)})
    return out


def _unsorted_file(file: str) -> Path:
    """Only a name that the unsorted index lists -- never a path from the page."""
    if not isinstance(file, str) or not _SORT_NAME.match(file) or file not in {r["file"] for r in unsorted_list()}:
        raise PrintError("ไม่มีเสียงนี้ในรายการที่ยังไม่แยก")
    return unsorted_dir() / file


def unsorted_audio(file: str) -> np.ndarray:
    path = _unsorted_file(file)
    try:
        return read_audio(path)
    except Exception as exc:          # soundfile's own errors are not OSError: they would end the socket
        raise PrintError(f"อ่านเสียงนี้ไม่ได้ ({type(exc).__name__})") from exc


def sort_unsorted(file: str, label: str) -> dict:
    """Move one utterance from the mixed recording under a label (or delete it: "discard")."""
    if label not in SORT_LABELS + ("discard",):
        raise PrintError(f"ป้ายต้องเป็น {', '.join(SORT_LABELS)} หรือ discard")
    with _sort_lock:
        src = _unsorted_file(file)            # inside the lock: a second page sorting it too gets "not listed"
        d = unsorted_dir()
        rows = _index_rows(d / "index.csv")
        row = next((r for r in rows if r.get("file") == file), None)
        if row is None:
            raise PrintError("ไม่มีเสียงนี้ในรายการที่ยังไม่แยก")
        if label == "discard":
            src.unlink()
        else:
            dest_dir = UTTERANCES / safe_name(label)
            dest = dest_dir / file
            if dest.exists():
                raise PrintError("มีเสียงชื่อนี้ในกลุ่มนั้นแล้ว")
            dest_dir.mkdir(parents=True, exist_ok=True)
            src.replace(dest)
            _write_index(dest_dir / "index.csv", _index_rows(dest_dir / "index.csv") + [row])
        # Rows whose wav is gone go too: the count the page shows is what it can still sort.
        left = [r for r in rows if r.get("file") != file and (d / str(r.get("file") or "")).is_file()]
        _write_index(d / "index.csv", left)
        return {"file": file, "label": label, "left": len(unsorted_list())}


def forget(name: str, *, audio: bool = False, prints: Path | None = None, utterances: Path | None = None) -> dict:
    """Delete a print; with `audio`, every saved utterance of every label too (and the mixed recording)."""
    prints = PRINTS if prints is None else prints
    utterances = UTTERANCES if utterances is None else utterances
    p = prints / f"{safe_name(name)}.npz"
    had = p.exists()
    if had:
        p.unlink()
    n = left = 0
    with _sort_lock:
        n, left = _forget_audio(utterances, audio)
    return {"print": had, "files": n, "left": left}


def _forget_audio(utterances: Path, audio: bool) -> tuple:
    n = left = 0
    for root in ((utterances, unsorted_dir(utterances)) if audio else ()):
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            # index.tmp too: a rewrite cut short leaves one, and "left: 0" must mean nothing is left.
            if f.is_file() and (f.suffix.lower() in AUDIO_EXT or f.name in ("index.csv", "index.tmp")):
                try:
                    f.unlink()
                    n += 1
                except OSError:           # Windows: a file another page is writing right now
                    left += 1
        for d in sorted((x for x in root.rglob("*") if x.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        if root != utterances:
            try:
                root.rmdir()
            except OSError:
                pass
    return n, left


def eval_rows(folder: Path, embed, voice, threshold: float, name: str) -> list:
    """Every utterance under <folder>/<label>/ judged against the print: rows for `report`."""
    genuine = set(GENUINE_LABELS) | {name}
    rows = []
    for sub in sorted(p for p in Path(folder).iterdir() if p.is_dir()):
        for f in sorted(p for p in sub.rglob("*") if p.suffix.lower() in AUDIO_EXT):
            r = judge_utterance(read_audio(f), embed, voice, threshold)
            r.update(label=sub.name, genuine=sub.name in genuine)
            rows.append(r)
    return rows


_load_lock = threading.Lock()
_call_lock = threading.Lock()
_shared_embed = None
_model_check: tuple | None = None


def model_ready() -> tuple:
    """(usable, why not) for the pinned speaker model; the hash is cached by (mtime, size)."""
    global _model_check
    from app import voiceprint as vp

    path = vp.DEFAULT_MODEL
    if not path.is_file():
        return False, "no_model"
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    if _model_check is None or _model_check[0] != key:
        _model_check = (key, vp._sha256(path) == vp.MODEL_SHA256)
    return (True, "") if _model_check[1] else (False, "model_pin")


def shared_embedder():
    """One loaded model for the server (the page and every door), calls serialised: the page
    and a live call may both embed at once, and one extractor is all the CPU should hold."""
    global _shared_embed
    with _load_lock:
        if _shared_embed is None:
            raw = load_embedder()

            def embed(x):
                with _call_lock:
                    return raw(x)

            _shared_embed = embed
    return _shared_embed


# ---- the door in the live audio path (ONE_VOICE_PROFILES) ---------------------------------------
#
# Owner 19 Nov: "จะให้ไมค์มันจับแค่เสียงคนนั้น". The 5 Nov door (removed) failed two ways the owner
# felt: eleven of his short words dropped in four minutes ("เหมือนจะไม่ฟัง"), and the 6 s
# `reply_burst` meant to save them let EVERY voice through. What the measurements allow:
#
#   * A sentence (>= DOOR_DECIDE_S of voiced speech) is judged by voice. bench 19 Nov, 1.2 s
#     windows at 0.50: the enrolled reader refused 5.9%, others on average 0.1%, the ONE closest
#     other voice 18% -- set the bar from the owner's own `eval` on the GCK, not from this.
#   * A short word cannot be: 0.5 s windows refuse the owner 54% at 0.50 (EER 8.2% on average,
#     29% in the closest-pair run). So a short word is judged by LOUDNESS: it passes when it is
#     at most ONE_VOICE_SHORT_DB under the enrolled voice's recent accepted sentences in this
#     call (the enrolment level until there is one). A close-talk mic hears its wearer loud and
#     the room quiet. THE RISK: anybody speaking into the same mic at the same distance passes
#     short words (the closest-pair run: 2 of 3), and AGC (LISTEN_FAR_PROFILES on the robot)
#     shrinks the gap this rides on. No time window: a window is what let everyone in on 5 Nov.
#   * While the robot walks or moves every voice passes at once (focus ROBOT_MOVING): "หยุด" from
#     anyone must reach stop_moving.
#   * Every failure opens: a verdict not back DOOR_VERDICT_WAIT_S after the ask, a model error,
#     a missing model or print, no local VAD gate to hang the door on (door_for / no_gate return
#     None and say so). A deaf robot is the worse failure. NOT a failure: a run that stays under
#     DOOR_DECIDE_S of loud speech for DOOR_HOLD_MAX_S (a murmur) -- judged by loudness like a
#     short word, so it can be refused.
#
# The cost: a sentence's first word goes upstream ~DOOR_DECIDE_S later (Gemini answers at
# activity_end, so the reply to a longer sentence is not later), and "speech_started" (barge-in)
# waits for the verdict.

#: Voiced speech held before a sentence is judged by voice (bench: 1.2 s at 0.50 = 5.9% / 18%;
#: 0.8 s = 18.8% / 9% -- shorter refuses the owner more without keeping others out better).
DOOR_DECIDE_S = 1.2
#: Audio held since the run reached the door (the pre-roll NOT counted: VAD_HEAD_HOLD_MS lets it be
#: 1.8 s on the robot -- review 19 Nov) past which a run never loud for DOOR_DECIDE_S is judged
#: like a short word (by loudness).
DOOR_HOLD_MAX_S = 3.0
#: Audio fed while a verdict is out past which the door opens (fail open: a model this slow is a
#: broken model). Counted from the ask, not from the run's start.
DOOR_VERDICT_WAIT_S = 1.5
#: While a run is refused, look again this often at its last DOOR_RELOOK_WINDOW_S: the enrolled
#: voice starting straight after somebody else's (one detector run, no 0.9 s pause) still gets in.
DOOR_RELOOK_S = 1.0
DOOR_RELOOK_WINDOW_S = 1.5
#: ... and a relook must clear the bar by this much. Every look is another draw: replayed through
#: the real gate (Silero + CAM++, LibriSpeech laid out like the owner's test, 19 Nov), the one
#: closest other voice got in 4 of 20 sentences at 0.50 -- 1 on the first look, 3 on relooks.
DOOR_RELOOK_MARGIN = 0.10
#: A sentence must also be no more than this under the wearer's level. A close-talk mic hears a
#: bystander far quieter (the same replay, that voice 25 dB down: 3 of 20 sentences got in by
#: voice alone); the wearer's own sentences vary a few dB. 15 dB is a guess with room on both
#: sides -- the owner's `nearby` test on the GCK is what measures the real gap. AGC flattens it.
DOOR_SENTENCE_DB = 15.0
#: Accepted sentences whose level the short-word rule follows (median).
DOOR_LEVEL_MEMORY = 5
PASS, BLOCK, WAIT = "pass", "block", "wait"

_door_pool = None
_warned: set = set()


def _pool() -> ThreadPoolExecutor:
    global _door_pool
    if _door_pool is None:
        _door_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="one-voice")
    return _door_pool


def _robot_moving() -> bool:
    try:
        from app import focus

        return bool(focus._robot_moving())
    except Exception:
        return False


def _turnlog(**row) -> None:
    from app import turnlog

    turnlog.record("one_voice", **row)


class Door:
    """The voice door VadGate asks before a segment opens (app/vad_gate.py `voice_door`).

    The gate holds the head of a run (pre-roll + frames) and asks `look(held)` every frame:
    PASS opens the segment with the held audio first, BLOCK drops it and refuses the run until
    the detector says it ended (`relook` may still let the enrolled voice in), WAIT keeps holding.
    A run that ends while held asks `short(held)`. One run at a time: `begin()`. Rows go to the
    turn log as numbers only (no audio, no names)."""

    def __init__(self, voice, *, threshold: float, short_db: float, ref_db,
                 embed=None, inline: bool = False, moving=_robot_moving, record=_turnlog) -> None:
        self.voice = unit(voice)
        self.threshold = float(threshold)
        self.short_db = float(short_db)
        self._enrol_db = ref_db
        self._embed = embed               # None = the shared model, loaded in the worker thread
        self._inline = inline
        self._moving = moving
        self._record = record
        self._levels: deque = deque(maxlen=DOOR_LEVEL_MEMORY)
        self._pending = None
        self._verdict = None
        self._relook_bytes = 0
        self._head = 0
        self._asked_at = 0
        self.counts = {"sentence_pass": 0, "sentence_block": 0, "short_pass": 0, "short_block": 0,
                       "moving": 0, "fail_open": 0, "relook_pass": 0}

    def ref_db(self):
        return float(np.median(self._levels)) if self._levels else self._enrol_db

    def begin(self, head_bytes: int = 0) -> None:
        """A new run reached the door; `head_bytes` of what it will hold is pre-roll."""
        self._pending = None
        self._verdict = None
        self._relook_bytes = 0
        self._head = int(head_bytes)
        self._asked_at = 0

    # -- the three questions ---------------------------------------------------------------------

    def look(self, held: bytes) -> str:
        if self._moving():
            return self._open("moving", None, None)
        if self._verdict is not None:
            return self._verdict
        if self._pending is not None:
            if self._pending.done():
                return self._finish(*self._pending.result(), why="sentence")
            if len(held) - self._asked_at > DOOR_VERDICT_WAIT_S * 2 * RATE:
                return self._open("fail_open", None, None, detail="slow")
            return WAIT
        v = trim(pcm_to_float(held))
        if len(v) / RATE >= DOOR_DECIDE_S:
            self._pending = self._submit(v)
            self._asked_at = len(held)
            if self._pending.done():
                return self._finish(*self._pending.result(), why="sentence")
            return WAIT
        if len(held) - self._head > DOOR_HOLD_MAX_S * 2 * RATE:
            # Three seconds the detector calls speech, never 1.2 s of it loud: a murmur. Judged
            # like a short word (by loudness), not waved through.
            return self._by_level(v, why="long_murmur")
        return WAIT

    def short(self, held: bytes) -> str:
        """The run ended before a verdict: a short word, or a sentence whose verdict is late."""
        if self._moving():
            return self._open("moving", None, None)
        if self._verdict is not None:
            return self._verdict
        if self._pending is not None and self._pending.done():
            return self._finish(*self._pending.result(), why="sentence")
        return self._by_level(trim(pcm_to_float(held)), why="short")

    def relook(self, window: bytes, new_bytes: int) -> str:
        """While a run is refused: PASS if its latest audio has become the enrolled voice."""
        if self._moving():
            return self._open("moving", None, None)
        if self._pending is not None:
            if not self._pending.done():
                return BLOCK
            s, speech_s, lv = self._pending.result()
            self._pending = None
            if not isinstance(s, float):
                return self._finish(s, speech_s, lv, why="relook")      # a model error opens, as anywhere
            if s >= self.threshold + DOOR_RELOOK_MARGIN and self._loud_enough(lv):
                self.counts["relook_pass"] += 1
                if lv is not None:
                    self._levels.append(lv)
                self._log(PASS, "relook", s, speech_s, lv)
                self._verdict = PASS
                return PASS
            return BLOCK
        self._relook_bytes += new_bytes
        if self._relook_bytes < DOOR_RELOOK_S * 2 * RATE:
            return BLOCK
        self._relook_bytes = 0
        v = trim(pcm_to_float(window[-int(DOOR_RELOOK_WINDOW_S * 2 * RATE):]))
        if len(v) / RATE >= DOOR_DECIDE_S:
            self._pending = self._submit(v)
            if self._pending.done():
                return self.relook(window, 0)
        return BLOCK

    # -- inside ------------------------------------------------------------------------------------

    def _submit(self, v):
        lv = level_stats(v)["rms_db"]
        speech_s = len(v) / RATE

        def job():
            try:
                embed = self._embed or shared_embedder()
                return score(embed(v), self.voice), speech_s, lv
            except BaseException as exc:          # PrintError is a SystemExit: caught here too
                return exc, speech_s, lv

        if self._inline:
            from concurrent.futures import Future

            f = Future()
            f.set_result(job())
            return f
        return _pool().submit(job)

    def _finish(self, s, speech_s, lv, *, why: str) -> str:
        self._pending = None
        if not isinstance(s, float):
            if "embed" not in _warned:
                _warned.add("embed")
                logger.warning("one voice door: the speaker model failed (%s) -- the door opens",
                               type(s).__name__)
            return self._open("fail_open", speech_s, lv, detail="model")
        if s >= self.threshold and self._loud_enough(lv):
            if lv is not None:
                self._levels.append(lv)
            self.counts["sentence_pass"] += 1
            self._verdict = PASS
        else:
            self.counts["sentence_block"] += 1
            self._verdict = BLOCK
        self._log(self._verdict, why, s, speech_s, lv)
        return self._verdict

    def _loud_enough(self, lv) -> bool:
        """A sentence within DOOR_SENTENCE_DB of the wearer (no reference = no level test)."""
        ref = self.ref_db()
        return ref is None or lv is None or lv >= ref - DOOR_SENTENCE_DB

    def _by_level(self, v, *, why: str) -> str:
        lv = level_stats(v)["rms_db"] if len(v) else None
        ref = self.ref_db()
        ok = lv is not None and ref is not None and lv >= ref - self.short_db
        self.counts["short_pass" if ok else "short_block"] += 1
        self._verdict = PASS if ok else BLOCK
        self._log(self._verdict, why, None, len(v) / RATE, lv)
        return self._verdict

    def _open(self, why: str, speech_s, lv, detail=None) -> str:
        self.counts["moving" if why == "moving" else "fail_open"] += 1
        self._verdict = PASS
        self._pending = None
        self._log(PASS, why if detail is None else f"{why}:{detail}", None, speech_s, lv)
        return PASS

    def _log(self, decision, why, s, speech_s, lv) -> None:
        try:
            ref = self.ref_db()
            self._record(decision=decision, why=why,
                         score=None if s is None else round(float(s), 3),
                         speech_s=None if speech_s is None else round(float(speech_s), 2),
                         rms_db=lv, ref_db=None if ref is None else round(float(ref), 1),
                         threshold=self.threshold)
        except Exception:
            pass


def gate_attachable() -> tuple:
    """(can the door attach to a call on this machine, why not): it hangs on the local VAD gate,
    which exists only with VAD_MODE=local and the Silero model on disk (app/vad_gate.py
    for_session). VAD_MODE's default is "gemini" -- listed profiles then get no door at all."""
    from app.config import settings

    if (getattr(settings, "vad_mode", "") or "").strip().lower() != "local":
        return False, "vad_mode_not_local"
    model = Path(getattr(settings, "vad_model", "") or "").expanduser()
    if not model.is_absolute():
        model = ROOT / model
    if not model.is_file():
        return False, "no_vad_model"
    return True, ""


def no_gate(profile, provider: str = "") -> None:
    """A listed profile's call has no local VAD gate: every voice passes. Said once in the log
    and in the turn log of every such call (review 20 Nov: this used to be silent)."""
    from app.config import settings

    profiles = getattr(settings, "one_voice_profiles", frozenset()) or frozenset()
    prof = (profile or settings.assistant_profile or "").strip().lower()
    if prof not in profiles:
        return
    _, why = gate_attachable()
    why = why or "no_local_vad"
    _warn_once("nogate:" + why, "ONE_VOICE_PROFILES lists %s but this call has no local VAD gate (%s, %s) -- "
               "the one-voice door cannot attach and every voice passes; set VAD_MODE=local", prof, why,
               provider or "?")
    try:
        _turnlog_off(why=why, provider=provider or None)
    except Exception:
        pass


def _turnlog_off(**row) -> None:
    from app import turnlog

    turnlog.record("one_voice_off", **row)


def door_for(profile):
    """The door for a call on this profile, or None (off -- the default -- or not usable)."""
    from app.config import settings

    profiles = getattr(settings, "one_voice_profiles", frozenset()) or frozenset()
    prof = (profile or settings.assistant_profile or "").strip().lower()
    if prof not in profiles:
        return None
    ok, why = model_ready()
    if not ok:
        _warn_once("model", "ONE_VOICE_PROFILES lists %s but the speaker model is not usable (%s) -- "
                   "every voice passes; run scripts/fetch_voiceprint_model.py", prof, why)
        return None
    name = (getattr(settings, "one_voice_name", "") or "").strip()
    if not name:
        names = [p["name"] for p in list_prints() if not p.get("unreadable")]
        if len(names) != 1:
            _warn_once("name", "ONE_VOICE_PROFILES lists %s but %d voices are on file and ONE_VOICE_NAME "
                       "is empty -- every voice passes", prof, len(names))
            return None
        name = names[0]
    try:
        voice, meta = load_print(name)
    except PrintError as exc:
        _warn_once("print", "ONE_VOICE_PROFILES lists %s but the voice print is not usable (%s) -- "
                   "every voice passes", prof, exc)
        return None
    logger.info("one voice door on for this %s call (threshold %.2f, short words within %.0f dB)",
                prof, settings.one_voice_threshold, settings.one_voice_short_db)
    return Door(voice, threshold=settings.one_voice_threshold, short_db=settings.one_voice_short_db,
                ref_db=meta.get("level_db"))


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)
