#!/usr/bin/env python3
"""
Try "listen to ONE enrolled voice" on this PC, with the wireless mic that will go on the robot.

Owner, 19 Nov 2026: "แยกเอเจนท์มาทำเรื่องเสียงในคอมก่อนให้จำเสียงคนคนเดียว โดยเราจะลองก่อนว่าได้ไหม
ตอนนี้ที่คอมติดไมค์กับที่จะใช้กับหุ่นแล้ว". A bench, not a feature: nothing here touches the live Emma
session, robot.env or the robot (the voice lock that was wired into the session was removed on 5 Nov
-- CLAUDE.md "ฟังเสียงคนเดียวเอาออกแล้ว"). The new reason to look again is the microphone: that
attempt listened through the robot's far-field array, where one person's prints across sessions
scored 0.43-0.72 and short words were dropped; this one listens through a close-talk wireless mic.

    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py devices [--test 10]
    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py enroll --name owner [--seconds 30]
    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py listen --name owner [--save target]
    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py eval data\\voice-lock\\utterances --name owner
    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py bench <speaker corpus>   # the model alone
    .venv\\Scripts\\python.exe -X utf8 scripts\\voice_lock_try.py forget --name owner [--audio]

**Personal data (PDPA).** A voice print identifies a person the way a face does, and the saved
utterances are recordings of people. Everything lives under data/voice-lock/ (gitignored); nothing
here prints, logs or sends a vector or a sample -- the output is lengths, levels and scores. `forget`
deletes a print, `forget --audio` the recordings too. Ask the people you record; delete when done.

**What decides (and why short words are separate).** Each utterance the VAD closes is trimmed of its
quiet head and tail, embedded with the speaker model chosen on 9 Oct (app/voiceprint.py: 3D-Speaker
CAM++ zh+en, the pinned file), and its cosine to the enrolled print is the score: >= threshold =
ACCEPT. Under ~1 s the embedding is mostly guesswork (9 Oct: EER 9.9% at 0.8 s against 4.1% at
1.2 s) -- on 5 Nov the owner's short words were what got dropped -- so `eval` reports utterances
under SHORT_S on their own and never lets them set the threshold.

The mic: the GCK receiver shows up on Windows as "Microphone (GCK)" (20 Nov, measured with the owner
speaking; "Microphone (LCS_USB_AUDIO)", USB 0a67:8524, is another input that sends exact zeros -- the
19 Nov guess that it was the GCK made every check say "transmitter off"). Its noise gate sends EXACT zero when nobody speaks (on the robot, 11 Nov:
46% of quiet windows), so silence alone cannot tell "gate closed" from "transmitter off": `devices
--test` must be run while somebody speaks into the mic.
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.one_voice import (  # noqa: E402  the shared core: one copy for CLI, page and gate
    AUDIO_EXT, CLIP_AT, DEFAULT_THRESHOLD, ENROLL_MIN_SPEECH_S, ENROLL_WINDOW_S, GCK_NAMES,
    GENUINE_LABELS, MAX_FAR, MAX_FRR, PRINTS, RATE, UTTERANCES, Segmenter, build_print, eer,
    far_frr, judge_utterance, level_stats, liveness, eval_rows, forget, load_embedder, load_print,
    read_audio, recommend, report, safe_name, save_print, save_utterance, speech_windows, trim,
)


class Decimator:
    """Integer-ratio down-sampler with state across blocks (48 kHz -> 16 kHz is 3:1): the same
    output whatever the block sizes, so the live path hears what an offline read of the file
    would. Linear-phase FIR low-pass at 0.9 x the new Nyquist."""

    def __init__(self, rate_in: int, rate_out: int = RATE, taps_per_step: int = 32) -> None:
        if rate_in % rate_out:
            raise ValueError(f"{rate_in} Hz is not a multiple of {rate_out} Hz")
        self.q = rate_in // rate_out
        self.phase = 0
        if self.q == 1:
            self.h = None
            return
        from scipy.signal import firwin

        self.h = firwin(self.q * taps_per_step + 1, 0.45 * rate_out, fs=rate_in)
        self.zi = np.zeros(len(self.h) - 1)

    def __call__(self, x) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).ravel()
        if self.h is None:
            return x.astype(np.float32)
        from scipy.signal import lfilter

        y, self.zi = lfilter(self.h, 1.0, x, zi=self.zi)
        out = y[self.phase::self.q]
        self.phase = (self.phase - len(x)) % self.q
        return out.astype(np.float32)


# ---- the microphone ---------------------------------------------------------------------------

def input_devices() -> list:
    import sounddevice as sd

    apis = sd.query_hostapis()
    default_in = sd.default.device[0]
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        out.append({"index": i, "name": d["name"].replace("\r", " ").replace("\n", " "),
                    "api": apis[d["hostapi"]]["name"], "channels": d["max_input_channels"],
                    "rate": int(d["default_samplerate"]), "gck": any(g in d["name"] for g in GCK_NAMES),
                    "default": i == default_in})
    return out


def pick_device(spec: str | None) -> dict:
    devs = input_devices()
    if not devs:
        raise SystemExit("no input devices")
    if spec is None:
        for want in (lambda d: d["gck"] and d["api"] == "Windows WASAPI", lambda d: d["gck"], lambda d: d["default"]):
            hit = [d for d in devs if want(d)]
            if hit:
                return hit[0]
        return devs[0]
    if spec.isdigit():
        hit = [d for d in devs if d["index"] == int(spec)]
    else:
        hit = sorted([d for d in devs if spec.lower() in d["name"].lower()], key=lambda d: d["api"] != "Windows WASAPI")
    if not hit:
        raise SystemExit(f"no input device matches {spec!r} -- see: voice_lock_try.py devices")
    return hit[0]


class Mic:
    """One input device as 16 kHz mono float32 blocks."""

    def __init__(self, dev: dict, block_s: float = 0.1) -> None:
        import sounddevice as sd

        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.dropped = 0
        rate = dev["rate"]
        extra = None
        if rate % RATE:
            # Not an integer multiple of 16 kHz: let Windows convert (WASAPI) or ask for 16 kHz.
            rate = RATE
            if dev["api"] == "Windows WASAPI":
                extra = sd.WasapiSettings(auto_convert=True)
        self.dec = Decimator(rate)
        self.rate = rate

        def callback(indata, frames, t, status):      # audio thread: copy and go
            if status.input_overflow:
                self.dropped += 1
            try:
                self.q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                self.dropped += 1

        self.stream = sd.InputStream(device=dev["index"], channels=1, samplerate=rate, dtype="float32",
                                     blocksize=int(rate * block_s), callback=callback, extra_settings=extra)

    def __enter__(self):
        self.stream.start()
        return self

    def __exit__(self, *exc):
        self.stream.stop()
        self.stream.close()

    def read(self, timeout: float = 1.0) -> tuple[np.ndarray, np.ndarray] | None:
        """(raw block at the device rate, the same block at 16 kHz), or None on a timeout."""
        try:
            raw = self.q.get(timeout=timeout)
        except queue.Empty:
            return None
        return raw, self.dec(raw)


def _bar(rms_db: float | None, width: int = 30) -> str:
    if rms_db is None:
        return ""
    n = int(max(0, min(width, (rms_db + 60) / 60 * width)))
    return "#" * n


# ---- commands ---------------------------------------------------------------------------------

def cmd_devices(args) -> int:
    devs = input_devices()
    print(f"{'#':>3}  {'host API':<20} {'ch':>2} {'rate':>6}  name")
    for d in devs:
        mark = "  <- GCK receiver" if d["gck"] else ""
        mark += "  [Windows default]" if d["default"] else ""
        print(f"{d['index']:>3}  {d['api']:<20} {d['channels']:>2} {d['rate']:>6}  {d['name']}{mark}")
    dev = pick_device(args.device)
    print(f"\nlisten/enroll will use #{dev['index']} {dev['name']} ({dev['api']}, {dev['rate']} Hz)"
          + ("" if dev["gck"] else "   ** not the GCK **"))
    if not args.test:
        print("Check that it is live: add --test 10 and SPEAK INTO THE MIC while it runs.")
        return 0
    print(f"\nListening {args.test:g} s -- speak into the mic now ...")
    peaks = []
    with Mic(dev) as mic:
        start = time.monotonic()
        sec: list = []
        while time.monotonic() - start < args.test:
            got = mic.read()
            if got is None:
                print("  no frames from the device in 1 s (USB state? replug the receiver)")
                continue
            sec.append(got[0])
            if sum(len(s) for s in sec) >= mic.rate:
                x = np.concatenate(sec)
                sec = []
                st = level_stats(x)
                peaks.append(st["peak"])
                db = "  -inf" if st["rms_db"] is None else f"{st['rms_db']:6.1f}"
                print(f"  {len(peaks):>3} s  peak {st['peak']:.4f}  rms {db} dBFS  zero {st['zero'] * 100:5.1f}%"
                      f"  clip {st['clip'] * 100:4.1f}%  {_bar(st['rms_db'])}")
    v = liveness(peaks)
    print(f"\n{v}")
    if v == "NO SOUND":
        print("Every sample was exactly 0. A real mic never reads exact zero -- the receiver is sending silence:\n"
              "  the transmitter is off / not paired / muted, or its noise gate stayed closed because nobody spoke.\n"
              "  Switch the transmitter on, speak into it and run --test again. Nothing else is worth measuring until then.")
        return 2
    if v == "DITHER ONLY":
        print("Only dither (peak < 0.002): the receiver is on but hears nothing -- transmitter muted or out of range.")
        return 2
    if max(peaks) >= CLIP_AT:
        print("Clipping seen: lower the transmitter/receiver gain (or Windows input level) before enrolling.")
    return 0


ENROLL_TEXT = """Read this aloud, at your normal voice, mic where it will be worn (Thai and English both count):

  สวัสดีค่ะ วันนี้อยากทราบเรื่องโครงการนี้หน่อย ว่ามีสิ่งอำนวยความสะดวกอะไรบ้าง
  ห้องแบบสองห้องนอนมีขนาดเท่าไหร่ แล้วมีระเบียงด้วยไหม
  ที่นี่ห่างจากหาดกี่กิโลเมตร ถ้าเดินไปจะใช้เวลาประมาณเท่าไหร่
  สระว่ายน้ำอยู่ชั้นไหน แล้วมีฟิตเนสกับห้องซาวน่าให้ใช้ด้วยหรือเปล่า
  Hello Emma, I would like to know more about this project and what facilities it has.
  How far is the beach from here, and how long would it take to walk there?
  Tell me about the idea behind the building and why it was designed this way.
  ขอบคุณมากนะ เดี๋ยวขอคุยกับทีมขายต่ออีกสักครู่แล้วกัน
"""


def cmd_enroll(args) -> int:
    name = safe_name(args.name)
    embed = load_embedder()
    if args.wav:
        chunks = [trim(read_audio(p)) for p in args.wav]
        source = "wav"
        clip = max((level_stats(c)["clip"] for c in chunks), default=0.0)
    else:
        dev = pick_device(args.device)
        print(f"Recording {args.seconds:g} s from #{dev['index']} {dev['name']}")
        print(ENROLL_TEXT)
        try:
            input("Press Enter, then start reading ... ")
        except EOFError:                      # not a console (a test run): start at once
            print()
        seg = Segmenter()
        chunks = []
        raw_peak, clip_n, total = 0.0, 0, 0
        with Mic(dev) as mic:
            start = time.monotonic()
            last_print = -1
            sec: list = []
            while time.monotonic() - start < args.seconds:
                got = mic.read()
                if got is None:
                    continue
                raw, x16 = got
                raw_peak = max(raw_peak, float(np.abs(raw).max()))
                clip_n += int((np.abs(raw) >= CLIP_AT).sum())
                total += len(raw)
                sec.append(x16)
                chunks += [trim(s) for _, s in seg.feed(x16)]
                el = int(time.monotonic() - start)
                if el != last_print:
                    last_print = el
                    st = level_stats(np.concatenate(sec)) if sec else level_stats([])
                    sec = []
                    print(f"\r  {el:>3}/{args.seconds:g} s  {_bar(st['rms_db']):<30}", end="", flush=True)
        print()
        chunks += [trim(s) for _, s in seg.flush()]
        if raw_peak == 0.0:
            print("NO SOUND: every sample was exactly 0 -- transmitter off / unpaired / muted. Nothing enrolled.")
            return 2
        source = f"mic:{dev['name']}"
        clip = clip_n / max(total, 1)
    speech = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    speech_s = len(speech) / RATE
    if speech_s < ENROLL_MIN_SPEECH_S:
        print(f"Only {speech_s:.1f} s of speech (need {ENROLL_MIN_SPEECH_S:g}): nothing enrolled. "
              "Read for longer (--seconds 40) or closer to the mic.")
        return 1
    wins = speech_windows(speech)
    built = build_print([embed(w) for w in wins])
    if not built["ok"]:
        if built["why"] == "mixed_voices":
            print(f"Two voices in the recording (halves {built['halves']}, smaller share {built['smaller']}): "
                  "nothing enrolled. Record again with only the one person talking.")
        else:
            print(f"Not enrolled: {built['why']}")
        return 1
    lv = level_stats(speech)
    meta = {"speech_s": round(speech_s, 1), "level_db": lv["rms_db"], "clip": round(clip, 4), "source": source,
            "created": datetime.now().isoformat(timespec="seconds")}
    path = save_print(name, built, meta)
    print(f"Enrolled '{name}': {speech_s:.1f} s of speech, {built['kept']}/{built['windows']} windows of "
          f"{ENROLL_WINDOW_S:g} s, each window vs the print: worst {built['worst']}, mean {built['self_mean']}"
          f" (one-voice check: halves {built['halves']})")
    print(f"  level {lv['rms_db']} dBFS, clipped {clip * 100:.2f}% of samples  -> {path.relative_to(ROOT)}")
    if clip > 0.001:
        print("  ** clipping: lower the gain and enrol again -- a clipped print scores lower later **")
    if built["worst"] < 0.5:
        print("  ** a window scored under 0.50 against its own print: noisy room or a second voice? **")
    return 0


def cmd_listen(args) -> int:
    name = safe_name(args.name)
    voice, meta = load_print(name)
    embed = load_embedder()
    dev = pick_device(args.device)
    save_dir = None
    session = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.save:
        save_dir = UTTERANCES / safe_name(args.save)       # target / other / nearby
    print(f"Listening on #{dev['index']} {dev['name']} for '{name}' (enrolled {meta.get('speech_s')} s, "
          f"{meta.get('created')}), bar {args.threshold:.2f}"
          + (f", saving to {save_dir.relative_to(ROOT)}" if save_dir else "") + ". Ctrl+C to stop.")
    print(f"{'#':>4} {'at':>6} {'speech':>8} {'level':>9} {'peak':>6} {'clip':>6} {'score':>7}  decision")
    seg = Segmenter()
    n = 0
    counts = {"ACCEPT": 0, "REJECT": 0, "NONE": 0}
    deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
    mic = None
    try:
        with Mic(dev) as mic:
            while deadline is None or time.monotonic() < deadline:
                got = mic.read()
                if got is None:
                    continue
                for start_s, x in seg.feed(got[1]):
                    n += 1
                    row = judge_utterance(x, embed, voice, args.threshold)
                    counts[row["decision"]] += 1
                    _print_row(n, start_s, row)
                    if save_dir is not None:
                        save_utterance(args.save, x, session, n, start_s, row["speech_s"])
                    if args.count and n >= args.count:
                        raise KeyboardInterrupt
    except KeyboardInterrupt:
        pass
    if not (args.count and n >= args.count):
        # The utterance under way when the clock or Ctrl+C stopped us: judged, not lost.
        for start_s, x in seg.flush():
            n += 1
            row = judge_utterance(x, embed, voice, args.threshold)
            counts[row["decision"]] += 1
            _print_row(n, start_s, row)
            if save_dir is not None:
                save_utterance(args.save, x, session, n, start_s, row["speech_s"])
    print(f"\n{n} utterances: ACCEPT {counts['ACCEPT']}, REJECT {counts['REJECT']}, too short to score {counts['NONE']}"
          + (f"  (device dropped {mic.dropped} blocks)" if mic is not None and mic.dropped else ""))
    if save_dir is not None:
        print(f"saved under {save_dir.relative_to(ROOT)} -- personal recordings: delete with `forget --name {name} --audio`")
    return 0


def _print_row(n: int, start_s: float, row: dict) -> None:
    db = "     -" if row["rms_db"] is None else f"{row['rms_db']:6.1f}"
    sc = "      -" if row["score"] is None else f"{row['score']:7.3f}"
    mins, secs = divmod(int(start_s), 60)
    tail = "  short<1s: unreliable" if row["short"] else ""
    print(f"{n:>4} {mins:>3}:{secs:02d} {row['speech_s']:>6.2f} s {db} dB {row['peak']:>6.3f} "
          f"{row['clip'] * 100:>5.1f}% {sc}  {row['decision']}{tail}", flush=True)


def cmd_eval(args) -> int:
    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"{folder} is not a folder (want <folder>/target, <folder>/other, <folder>/nearby)")
    name = safe_name(args.name)
    voice, meta = load_print(name)
    embed = load_embedder()
    genuine = set(GENUINE_LABELS) | {name}
    rows = eval_rows(folder, embed, voice, args.threshold, name)
    if not rows:
        raise SystemExit(f"no audio under {folder}/<label>/")
    print(f"eval of {len(rows)} utterances in {folder} against '{name}' (enrolled {meta.get('speech_s')} s "
          f"from {meta.get('source')}); genuine labels: {', '.join(sorted(genuine & {r['label'] for r in rows})) or 'none!'}")
    lines, numbers = report(rows, args.threshold, max_far=args.max_far, max_frr=args.max_frr)
    print("\n".join(lines))
    if args.json:
        Path(args.json).write_text(json.dumps(numbers, indent=1, default=float), encoding="utf-8")
    return 0


def cmd_bench(args) -> int:
    """Model separation on a speaker-labelled corpus (one folder per speaker, e.g. LibriSpeech):
    enrol each speaker from their first files, score the rest (whole utterances and windows cut
    to fixed lengths) against every speaker's print. Measures the model and this procedure, NOT
    the GCK or a room: each corpus speaker also has their own recording channel, which flatters
    the separation -- one shared mic is harder."""
    root = Path(args.corpus)
    embed = load_embedder()
    speakers = sorted(p for p in root.iterdir() if p.is_dir())
    rng = np.random.default_rng(7)
    prints, items = {}, []          # items: (speaker, length label, embedding)
    t0 = time.monotonic()
    for sp in speakers:
        files = sorted(p for p in sp.rglob("*") if p.suffix.lower() in AUDIO_EXT)
        enrol, i = [], 0
        while i < len(files) and sum(len(e) for e in enrol) / RATE < args.enroll_s:
            enrol.append(trim(read_audio(files[i])))
            i += 1
        built = build_print([embed(w) for w in speech_windows(np.concatenate(enrol))]) if enrol else {"ok": False}
        if not built.get("ok"):
            print(f"  {sp.name}: not enrolled ({built.get('why')})")
            continue
        prints[sp.name] = built["vector"]
        for f in files[i: i + args.per_speaker]:
            x = trim(read_audio(f))
            items.append((sp.name, "whole", embed(x)))
            for L in args.lengths:
                n = int(L * RATE)
                if len(x) > n:
                    s = int(rng.integers(0, len(x) - n))
                    items.append((sp.name, f"{L:g}s", embed(x[s: s + n])))
    names = sorted(prints)
    if len(names) < 2:
        raise SystemExit(f"bench needs >= 2 enrolled speakers, got {len(names)}")
    P = np.array([prints[n] for n in names])
    print(f"{len(names)} speakers enrolled from {args.enroll_s:g} s each, {len(items)} test items, "
          f"{time.monotonic() - t0:.0f} s")
    print(f"{'length':<7}{'EER':>7}{'at':>6}{'bar FAR<=5%':>13}{'FRR there':>11}{'worst':>9}{'>50%':>7}"
          + "".join(f"{'@' + format(t, '.2f'):>18}" for t in args.bars))
    for L in ["whole"] + [f"{x:g}s" for x in args.lengths]:
        sel = [(s, e) for s, lab, e in items if lab == L]
        if not sel:
            continue
        S = np.array([e for _, e in sel]) @ P.T
        own = np.array([names.index(s) for s, _ in sel])
        gen = S[np.arange(len(sel)), own]
        mask = np.ones_like(S, dtype=bool)
        mask[np.arange(len(sel)), own] = False
        imp = S[mask]
        e = eer(gen, imp)
        rec = recommend(gen, imp)
        if e is None or rec is None:
            continue
        worst = pair_far(S, own, rec["threshold"])
        cells = ""
        for t in args.bars:
            far, frr = far_frr(gen, imp, t)
            pf = pair_far(S, own, t)
            cells += f"{frr * 100:>7.1f}/{far * 100:.1f}/{np.nanmax(pf) * 100:<5.0f}"
        print(f"{L:<7}{e[0] * 100:>6.1f}%{e[1]:>6.2f}{rec['threshold']:>13.2f}{rec['frr'] * 100:>10.1f}%"
              f"{np.nanmax(worst) * 100:>8.0f}% {majority_share(worst) * 100:>5.1f}%{cells}")
    print("FRR = the enrolled speaker refused, FAR = another speaker let in (all pairs); 'worst pair' = the one\n"
          "other speaker let in most at the FAR<=5% bar, 'pairs >50%' = share of (print, other speaker) pairs\n"
          "where the other speaker gets in more often than not. Cells: FRR / FAR / worst pair, in %.")
    if args.pairs:
        sel = [(s, e) for s, lab, e in items if lab == "whole"]
        S = np.array([e for _, e in sel]) @ P.T
        own = np.array([names.index(s) for s, _ in sel])
        m = pair_mean(S, own)
        np.fill_diagonal(m, np.nan)
        order = np.dstack(np.unravel_index(np.argsort(-np.nan_to_num(m, nan=-9).ravel()), m.shape))[0][: args.pairs]
        print("closest pairs (mean score of speaker k's whole utterances against speaker j's print):")
        for j, k in order:
            print(f"  print {names[j]:>6}  speaker {names[k]:>6}  mean {m[j, k]:.3f}")
    return 0


def pair_far(S, own, t: float) -> np.ndarray:
    """FAR per (print j, other speaker k): the share of speaker k's items scoring >= t against
    print j. S is items x prints, own[i] the print index of item i's own speaker. Diagonal NaN."""
    S = np.asarray(S, dtype=float)
    own = np.asarray(own)
    n = S.shape[1]
    out = np.full((n, n), np.nan)
    for k in range(n):
        rows = own == k
        if rows.any():
            out[:, k] = (S[rows] >= t).mean(axis=0)
    np.fill_diagonal(out, np.nan)
    return out


def majority_share(pf) -> float:
    """Share of (print, other speaker) pairs where the other speaker gets in more often than not --
    over the off-diagonal pairs only (review 19 Nov: `nanmean(pf > 0.5)` counted the NaN diagonal
    as "no" and divided by n^2)."""
    pf = np.asarray(pf, dtype=float)
    ok = ~np.isnan(pf)
    return float((pf[ok] > 0.5).mean()) if ok.any() else 0.0


def pair_mean(S, own) -> np.ndarray:
    """Mean score per (print j, speaker k) -- the diagonal is each speaker against their own print."""
    S = np.asarray(S, dtype=float)
    own = np.asarray(own)
    n = S.shape[1]
    out = np.full((n, n), np.nan)
    for k in range(n):
        rows = own == k
        if rows.any():
            out[:, k] = S[rows].mean(axis=0)
    return out


def cmd_forget(args) -> int:
    name = safe_name(args.name)
    out = forget(name, audio=args.audio)
    print(f"deleted print {name!r}" if out["print"] else f"no print {name!r}")
    if args.audio:
        print(f"deleted {out['files']} files under {UTTERANCES.relative_to(ROOT)}")
    return 0


def cmd_list(args) -> int:
    for p in sorted(PRINTS.glob("*.npz")) if PRINTS.is_dir() else []:
        with np.load(p, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
        print(f"{p.stem}: {meta.get('speech_s')} s speech, {meta.get('kept')}/{meta.get('windows')} windows, "
              f"from {meta.get('source')}, {meta.get('created')}")
    if UTTERANCES.is_dir():
        for d in sorted(x for x in UTTERANCES.iterdir() if x.is_dir()):
            print(f"utterances/{d.name}: {sum(1 for f in d.iterdir() if f.suffix.lower() in AUDIO_EXT)} files")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("devices", help="list inputs, mark the GCK; --test N listens N s")
    d.add_argument("--device")
    d.add_argument("--test", type=float, default=0)
    e = sub.add_parser("enroll", help="record a person reading and store their print")
    e.add_argument("--name", required=True)
    e.add_argument("--seconds", type=float, default=30)
    e.add_argument("--device")
    e.add_argument("--wav", nargs="+", help="enrol from files instead of the mic")
    li = sub.add_parser("listen", help="live: score every utterance against the print")
    li.add_argument("--name", required=True)
    li.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    li.add_argument("--device")
    li.add_argument("--save", help="label to save each utterance under (target / other / nearby)")
    li.add_argument("--minutes", type=float, default=0)
    li.add_argument("--count", type=int, default=0)
    ev = sub.add_parser("eval", help="labelled folders of wavs -> score distributions, FAR/FRR, a threshold")
    ev.add_argument("folder")
    ev.add_argument("--name", required=True)
    ev.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ev.add_argument("--max-far", type=float, default=MAX_FAR)
    ev.add_argument("--max-frr", type=float, default=MAX_FRR)
    ev.add_argument("--json", help="also write the numbers (no audio, no vectors) to this file")
    b = sub.add_parser("bench", help="model separation on a speaker-labelled corpus (LibriSpeech layout)")
    b.add_argument("corpus")
    b.add_argument("--enroll-s", type=float, default=30)
    b.add_argument("--per-speaker", type=int, default=25)
    b.add_argument("--lengths", type=float, nargs="+", default=[0.5, 0.8, 1.2, 2.0])
    b.add_argument("--bars", type=float, nargs="+", default=[0.30, 0.40, 0.50])
    b.add_argument("--pairs", type=int, default=0, help="also list the N closest (print, other speaker) pairs")
    f = sub.add_parser("forget", help="delete a print; --audio also deletes EVERY saved utterance (all labels)")
    f.add_argument("--name", required=True)
    f.add_argument("--audio", action="store_true")
    sub.add_parser("list", help="prints and saved utterances on file")
    args = ap.parse_args(argv)
    return {"devices": cmd_devices, "enroll": cmd_enroll, "listen": cmd_listen, "eval": cmd_eval,
            "bench": cmd_bench, "forget": cmd_forget, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
