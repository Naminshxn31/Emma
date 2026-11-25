#!/usr/bin/env python3
"""The debut's sound (app/ceremony_reveal.py): a music bed's build, its break, its hit (and, for a countdown,
a voice counting over it) -- mixed here into ONE file, pinned in the ceremony's asset index with its times.

    python scripts/ceremony_reveal_sound.py mix --music <bed> --licence "<...>" [--credit "title|author|url" ...]
        [--build 5.0 | --start S] [--blackout 1.5] [--lufs -13.5] [--copy <file>] [--voice <count> --duck 10]
    python scripts/ceremony_reveal_sound.py video <file> --to 13.4 [--from 0] --checked "<what was looked at>"
        --shows "<what it shows>" --source-note "<whose file, when>"
    python scripts/ceremony_reveal_sound.py listened --by "<the owner's name>" [--note "..."]   # he listened to it
    python scripts/ceremony_reveal_sound.py show

Owner 23 Nov: "เอาเป็นดับไฟก่อน ปิดม่าน ... เมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มีซาวน์ แล้วหุ่นก็เปิดตัว", with a
countdown-trailer music he picked on Pixabay (its bed, a break to silence, its big hit) -- and after
listening to the counted version: "เอาเวลานับออก" (data/ceremony/reveal.json count_from 0: no --voice, no
numbers). The screen plays the build with the light pulsing on its beats, goes black at 0, and the light
burst and the logo come on the music's hit (the robot walks out of the box on it). Putting other sounds
in is this command, nothing in code. What it does:

  * the MUSIC: where its bed BREAKS (the first 10 ms frame 6 dB under the bed's median that stays under
    it for 0.3 s) is 0; its BEAT is measured (the autocorrelation of its high band's onsets, 75-120 a
    minute; --beat-period by ear) and its bars are taken to end on the break (a trailer's break is on a
    bar line); it is entered on the bar line about --build s before the break (4-6 s; --start to pick
    another point), a 15 ms fade only against a click; its HIT is the loudest transient after the break
    (the front of its rise); the silence between is cut to --blackout (a break shorter than that is
    refused). The build's beats (`beats`) are where the screen's light pulses;
  * a VOICE counting down (only when reveal.json counts): every spoken number found (10 ms energy 10 dB
    over the 50 ms before, then the word's own front), a high-pass and a compressor, each word cut out
    with its echo and placed on a --beat grid back from 0, the bed ducked under each (--duck dB);
  * the MIX: the whole to --lufs integrated (Emma's voice is -15.5 LUFS on the screen,
    scripts/voice_line.py: the debut a little over her) with its true peak at or under -1 dBTP (a
    limiter, only where a peak would pass it; measured again on the MP3 that is written);
  * writes data/ceremony/assets/<id>.mp3 (gitignored; robot_server deploy ships it because
    data/ceremony/reveal.json names it) and pins it in data/ceremony/assets/index.json under that id:
    sha256, `beats`, `numbers` ([number, seconds]; [] without a voice), `zero_s` (the screen goes
    black), `impact_s` (the hit, measured on the written file), `blackout_s`, `duration`, the loudness,
    the licence and credits, `measured` (what this script measured -- never `checked` nor `listened`:
    the owner's, by the `listened` command, pinned to the bytes), `hand`. --copy writes the same MP3
    somewhere to listen to.

`video` cuts the debut's video (owner 23 Nov: "แนวนอน.mp4 เพิ่มในตอนเปิดตัว") for the screen on Wi-Fi: the
part used, H.264 1080p, no sound (the mix is the sound), pinned under data/ceremony/reveal.json's `video`
id -- the screen plays it muted from the hit.

Pixabay's Content Licence: commercial use without attribution; no redistributing the files as they are
-- the mix plays inside the event and is never offered for download. Needs ffmpeg on PATH. The sound
files are someone else's downloads: they are only ever read by ffmpeg.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FOLDER = ROOT / "data" / "ceremony" / "assets"
INDEX = FOLDER / "index.json"
CONFIG = ROOT / "data" / "ceremony" / "reveal.json"
#: Emma's own voice on the screen (scripts/voice_line.py LUFS)
VOICE_LUFS = -15.5
TRUE_PEAK = -1.0
RATE = 48000
FRAME = 0.01


def _ffmpeg(*args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", "-hide_banner", *(["-nostdin"] if data is None else []), *args], input=data,
                          capture_output=True)


def loudness(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) by ffmpeg's ebur128."""
    out = _ffmpeg("-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-").stderr.decode("utf-8", "replace")
    summary = out[out.rfind("Summary:"):]
    lufs = peak = None
    for line in summary.splitlines():
        line = line.strip()
        if line.startswith("I:"):
            lufs = float(line.split()[1])
        elif line.startswith("Peak:"):
            peak = float(line.split()[1])
    if lufs is None or peak is None:
        raise SystemExit("ffmpeg could not measure %s:\n%s" % (path, out[-800:]))
    return lufs, peak


def load(path: Path, channels: int, af: str | None = None) -> np.ndarray:
    """The file as float samples at RATE: (n,) for mono, (n, 2) for stereo."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path)]
    if af:
        args += ["-af", af]
    raw = subprocess.run(args + ["-ac", str(channels), "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    return x if channels == 1 else x.reshape(-1, 2)


def write_wav(x: np.ndarray, path: Path) -> None:
    if x.ndim == 1:
        x = np.stack([x, x], axis=1)
    done = _ffmpeg("-y", "-f", "f32le", "-ar", str(RATE), "-ac", "2", "-i", "-", "-c:a", "pcm_f32le", str(path),
                   data=np.ascontiguousarray(x, dtype=np.float32).tobytes())
    if done.returncode != 0:
        raise SystemExit("ffmpeg could not write %s" % path)


def mono(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 1 else x.mean(axis=1)


def levels(x: np.ndarray, frame: float = FRAME) -> np.ndarray:
    """Energy per frame, in dB."""
    x = mono(x)
    n = int(frame * RATE)
    k = len(x) // n
    return 10 * np.log10(np.mean(x[:k * n].reshape(k, n) ** 2, axis=1) + 1e-12)


def hit(x: np.ndarray, start: float = 0.0, within: float | None = None) -> float:
    """Seconds to the hit after `start`: the loudest 50 ms (within `within` s, if given), then the front
    of its rise -- the sharpest rise of 10 ms energy over the 50 ms before, in the 0.4 s before it."""
    x = mono(x)
    a = int(start * RATE)
    b = len(x) if within is None else min(len(x), a + int(within * RATE))
    hop = int(0.05 * RATE)
    if b - a < 4 * hop:
        raise SystemExit("nothing after %.2f s to find a hit in" % start)
    rms = np.sqrt(np.convolve(x[a:b] ** 2, np.ones(hop) / hop, mode="valid"))
    loudest = start + int(np.argmax(rms)) / RATE
    level = levels(x)
    best, at = -1e9, loudest
    for k in range(max(5, int((loudest - 0.4) / FRAME)), min(len(level), int((loudest + 0.05) / FRAME) + 1)):
        rise = level[k] - level[k - 5:k].mean()
        if rise > best:
            best, at = rise, k * FRAME
    return round(at, 3)


def words(voice: np.ndarray) -> list[float]:
    """Seconds to each spoken number's front."""
    coarse = levels(voice)
    floor = coarse.max() - 35
    found: list[float] = []
    for k in range(5, len(coarse)):
        if coarse[k] > floor and coarse[k] - coarse[k - 5:k].mean() > 10:
            if not found or k * FRAME - found[-1] > 0.3:
                found.append(k * FRAME)
    fine = levels(voice, 0.005)
    out = []
    for t in found:
        a, b = int(max(0.0, t - 0.1) / 0.005), int((t + 0.4) / 0.005)
        peak = fine[a:b].max()
        front = next(i for i in range(a, b) if fine[i] >= peak - 20)
        out.append(round(front * 0.005, 3))
    return out


def bed_break(music: np.ndarray, after: float) -> float:
    """Where the bed breaks: the first frame 6 dB under the bed's median (from `after` on) that stays under
    it for 0.3 s, with the bed playing before it."""
    level = levels(music)
    a = int(after / FRAME)
    bed = float(np.median(level[a:a + int(1.0 / FRAME)]))
    hold = int(0.3 / FRAME)
    for k in range(a + 1, len(level) - hold):
        if level[k] < bed - 6 and level[k:k + hold].max() < bed - 6:
            return round(k * FRAME, 3)
    raise SystemExit("the music has no break after %.2f s (a bed falling to silence before its hit)" % after)


def _smooth_db(curve: np.ndarray, attack: float, release: float) -> np.ndarray:
    """A gain curve in dB through a one-pole follower (down fast, back up slow), at 1 ms steps."""
    out = np.empty_like(curve)
    g = curve[0]
    ka, kr = np.exp(-0.001 / attack), np.exp(-0.001 / release)
    for i, c in enumerate(curve):
        k = ka if c < g else kr
        g = k * g + (1 - k) * c
        out[i] = g
    return out


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit("cannot read %s: %s" % (CONFIG, exc))


def _rows() -> list:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.is_file() else []


def _write_rows(rows: list) -> None:
    # (the index as the builder writes it: one-space indent, CRLF, not ASCII-escaped)
    text = json.dumps(rows, indent=1, ensure_ascii=False).replace("\n", "\r\n") + "\r\n"
    INDEX.write_bytes(text.encode("utf-8"))


def beat_period(music: np.ndarray, a: float, b: float, lo: float = 0.5, hi: float = 0.85) -> float:
    """The bed's beat, in seconds: the autocorrelation of its high band's onsets (hi-hats, a clock's tick)
    between `a` and `b`, its strongest peak between `lo` and `hi` s (75-120 a minute)."""
    x = mono(music)[int(a * RATE):int(b * RATE)]
    hop, win = 480, 2048
    n = (len(x) - win) // hop
    if n < 200:
        raise SystemExit("too little of the bed (%.1f s) to hear its beat in" % (b - a))
    frames = np.stack([x[i * hop:i * hop + win] * np.hanning(win) for i in range(n)])
    spec = np.log1p(10 * np.abs(np.fft.rfft(frames, axis=1)))
    band = spec[:, np.fft.rfftfreq(win, 1 / RATE) > 2000]
    flux = np.maximum(0, np.diff(band, axis=0)).sum(axis=1)
    flux = flux - flux.mean()
    ac = np.correlate(flux, flux, "full")[len(flux) - 1:]
    lags = np.arange(len(ac)) * hop / RATE
    window = (lags >= lo) & (lags <= hi)
    k = int(np.argmax(np.where(window, ac, -np.inf)))
    # (refined between its neighbours: a parabola through the three)
    if 0 < k < len(ac) - 1:
        d = (ac[k - 1] - ac[k + 1]) / (2 * (ac[k - 1] - 2 * ac[k] + ac[k + 1]) or 1)
        return float((k + d) * hop / RATE)
    return float(lags[k])


def mix(args) -> int:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH")
    cfg = config()
    asset_id, count, beat = cfg.get("sound"), cfg.get("count_from"), float(args.beat)
    if not isinstance(asset_id, str) or not asset_id.replace("_", "").isalnum():
        raise SystemExit("%s names no sound" % CONFIG)
    if not isinstance(count, int) or not 0 <= count <= 20:
        raise SystemExit("%s: count_from must be 0..20" % CONFIG)
    if bool(args.voice) != bool(count):
        raise SystemExit("%s counts %d: %s" % (CONFIG, count, "give the --voice that counts" if count else
                                                "no --voice (the debut has no countdown; owner 23 Nov: เอาเวลานับออก)"))
    music = load(Path(args.music), 2)
    spoken: list = []
    voice = None
    if count:
        spoken = words(load(Path(args.voice), 1))
        if len(spoken) < count:
            raise SystemExit("the voice says %d numbers (%s), the screen counts %d" % (len(spoken), spoken, count))
        spoken = spoken[:count]
        # (its intelligibility on a hall's PA: no rumble, the words even)
        voice = load(Path(args.voice), 1, "highpass=f=120,acompressor=threshold=-30dB:ratio=4:attack=5:release=120")

    zero_m = bed_break(music, after=(args.start + count * beat - 1.5) if args.start is not None else 2.0)
    hit_m = hit(music, start=zero_m + 0.05)
    # the bed's beat, its bars ending on the break (a trailer's break is on a bar line): where it is
    # entered when --start is not given -- the bar line `--build` s before the break or nearest it, 4-6 s
    period = args.beat_period or beat_period(music, max(0.5, zero_m - 8.0), zero_m)
    bar = 4 * period
    start = args.start
    if start is None:
        lines = [zero_m - k * bar for k in range(1, 8) if zero_m - k * bar >= 0.3]
        fits = [b for b in lines if 4.0 <= zero_m - b <= 6.0] or lines
        start = round(min(fits, key=lambda b: abs((zero_m - b) - args.build)), 3)
    if zero_m - count * beat < start + 0.2:
        raise SystemExit("the bed breaks at %.2f s: %d beats before it start before --start %.2f" % (zero_m, count, start))
    # the music, edited: from `start` (a bar line: a 15 ms fade only against a click), to the break and a
    # little of its fall, the silence cut to --blackout, then the hit and all that rings after it
    pre = min(0.3, args.blackout / 2)
    keep = args.blackout - pre
    head = fade_out(fade_in(music[int(start * RATE):int((zero_m + keep) * RATE)], 0.015 if not count else 0.12), 0.02)
    if hit_m - pre < zero_m + keep:
        raise SystemExit("the music's break (%.2f s) is shorter than --blackout %.2f s" % (hit_m - zero_m, args.blackout))
    bed = np.concatenate([head, fade_in(music[int((hit_m - pre) * RATE):], 0.01)])
    zero = zero_m - start
    impact_at = zero + args.blackout
    numbers = [[count - k, round(zero - (count - k) * beat, 3)] for k in range(count)]
    # the beats of the build (the light pulses on them): back from the break, one a period
    beats = sorted(round(zero - k * period, 3) for k in range(1, 200) if zero - k * period >= -0.02)
    beats = [max(0.0, t) for t in beats]

    out = bed
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        if count:
            # the voice: each word cut out with its echo, placed on the grid
            vox = np.zeros(len(bed))
            for k, (_, at) in enumerate(numbers):
                a = spoken[k] - 0.06
                b = spoken[k + 1] - 0.03 if k + 1 < len(spoken) else len(voice) / RATE
                b = min(b, spoken[k] + 1.1)
                seg = fade_out(fade_in(voice[int(a * RATE):int(b * RATE)], 0.01), 0.06)
                at0 = int((at - 0.06) * RATE)
                vox[at0:at0 + len(seg)] += seg[:len(vox) - at0]
            # its level: --voice-over dB over the bed's own loudness through the count (before the duck)
            write_wav(vox, tmp / "vox.wav")
            write_wav(bed[int(numbers[0][1] * RATE):int(zero * RATE)], tmp / "bed.wav")
            v_lufs, _ = loudness(tmp / "vox.wav")
            b_lufs, _ = loudness(tmp / "bed.wav")
            vox *= 10 ** ((b_lufs + args.voice_over - v_lufs) / 20)
            # the duck: 4 dB under the whole count, --duck under each word (1 ms steps)
            curve = np.zeros(len(bed) // 48 + 1)
            curve[int((numbers[0][1] - 0.3) * 1000):int(zero * 1000)] = -4.0
            for _, at in numbers:
                curve[int((at - 0.03) * 1000):int((at + 0.55) * 1000)] = -float(args.duck)
            duck = np.repeat(10 ** (_smooth_db(curve, 0.03, 0.25) / 20), 48)[:len(bed)]
            out = bed * duck[:, None] + vox[:, None]
        write_wav(out, tmp / "mix.wav")
        lufs, _ = loudness(tmp / "mix.wav")
        out = out * 10 ** ((args.lufs - lufs) / 20)
        # the true peak: limited only where it would pass -1 dBTP, measured on the MP3 (an encode moves it)
        name = asset_id + ".mp3"
        mp3 = tmp / name
        write_wav(out, tmp / "pre.wav")
        ceiling = -1.6
        for _ in range(5):
            done = _ffmpeg("-y", "-i", str(tmp / "pre.wav"), "-af",
                           "alimiter=limit=%.4f:attack=3:release=60:level=disabled" % 10 ** (ceiling / 20),
                           "-map_metadata", "-1", "-ar", str(RATE), "-ac", "2", "-c:a", "libmp3lame", "-b:a", "256k", str(mp3))
            if done.returncode != 0:
                raise SystemExit("ffmpeg could not write the mix:\n" + done.stderr.decode("utf-8", "replace")[-800:])
            got_lufs, got_peak = loudness(mp3)
            if got_peak <= TRUE_PEAK:
                break
            ceiling -= got_peak - TRUE_PEAK + 0.2
        else:
            raise SystemExit("the mix's true peak stays over %.1f dBTP" % TRUE_PEAK)
        final = load(mp3, 2)
        impact = hit(final, start=impact_at - 0.4, within=1.0)
        # (an encoder's delay moves everything by the same few ms: the numbers, the beats and 0 by the hit's)
        shift = round(impact - impact_at, 3)
        data = mp3.read_bytes()
    numbers = [[n, round(t + shift, 3)] for n, t in numbers]
    beats = [round(max(0.0, t + shift), 3) for t in beats]
    zero = round(zero + shift, 3)
    length = round(len(final) / RATE, 2)
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER / name).write_bytes(data)
    if args.copy:
        Path(args.copy).parent.mkdir(parents=True, exist_ok=True)
        Path(args.copy).write_bytes(data)
    credits = []
    for c in args.credit or []:
        parts = [p.strip() for p in c.split("|")]
        if len(parts) != 3:
            raise SystemExit("--credit is \"title|author|url\"")
        credits.append({"title": parts[0], "author": parts[1], "url": parts[2]})
    today = datetime.date.today().isoformat()
    measured = {"on": today, "by": "scripts/ceremony_reveal_sound.py mix", "lufs": round(got_lufs, 1),
                "true_peak": round(got_peak, 1), "voice_lufs_ref": VOICE_LUFS, "music_start_s": start,
                "music_break_s": zero_m, "music_hit_s": hit_m, "beat_s": round(period, 4),
                "tempo_bpm": round(60 / period, 1)}
    if count:
        measured.update(grid_s=beat, duck_db=args.duck, voice_words_s=spoken)
    row = {
        "id": asset_id, "kind": "audio", "file": name, "sha256": hashlib.sha256(data).hexdigest(), "candidate": True,
        "ok": True, "people": [], "class": "other", "about": ["debut"],
        "shows": (("the debut's sound: a voice counting %d..1 over a music bed, " % count) if count else
                  "the debut's sound: the music's build (no countdown), ") +
                 "a break at 0 (the screen goes black), the music's hit (the light burst and the logo; the robot walks out on it)",
        # (what THIS script measured -- never `checked`, a person's note, nor `listened`: the owner's, by the
        # `listened` command, pinned to these bytes; review 23 Nov)
        "measured": measured,
        "numbers": numbers, "beats": beats, "zero_s": zero, "impact_s": round(impact, 3),
        "blackout_s": round(impact - zero, 3), "duration": length, "lufs": round(got_lufs, 1),
        "true_peak": round(got_peak, 1), "licence": {"note": args.licence, "credits": credits},
        "source": " + ".join(Path(p).name for p in (args.music, args.voice) if p), "bytes": len(data),
        "hand": "scripts/ceremony_reveal_sound.py mix: the bed from %.2f s of the music (a bar line), its break at %.2f s, "
                "its hit at %.2f s, its beat %.3f s%s" % (start, zero_m, hit_m, period,
                                                        "; the voice's words at %s s of its file" % spoken if count else ""),
    }
    rows = [r for r in _rows() if not (isinstance(r, dict) and r.get("id") == asset_id)] + [row]
    _write_rows(rows)
    print("debut sound: %s  %.1f LUFS, true peak %.1f dBTP, %.2f s" % ((FOLDER / name).relative_to(ROOT), got_lufs,
                                                                     got_peak, length))
    print("  the music: bed from %.2f s (a bar line), breaks at %.2f s, hits at %.2f s; beat %.4f s (%.1f a minute)"
          % (start, zero_m, hit_m, period, 60 / period))
    if count:
        print("  the voice's words (s of its file): %s" % spoken)
        print("  numbers: %s" % "  ".join("%d@%.3f" % (n, t) for n, t in numbers))
    print("  beats: %s" % " ".join("%.2f" % t for t in beats))
    print("  0 (black) at %.3f s, the hit at %.3f s (black %.2f s)" % (zero, impact, impact - zero))
    if args.copy:
        print("  a copy to listen to: %s" % Path(args.copy).resolve())
    return 0


def fade_in(seg: np.ndarray, seconds: float) -> np.ndarray:
    seg = seg.copy()
    n = min(len(seg), int(seconds * RATE))
    ramp = np.linspace(0.0, 1.0, n)
    seg[:n] *= ramp[:, None] if seg.ndim == 2 else ramp
    return seg


def fade_out(seg: np.ndarray, seconds: float) -> np.ndarray:
    seg = seg.copy()
    n = min(len(seg), int(seconds * RATE))
    ramp = np.linspace(1.0, 0.0, n)
    if n:
        seg[-n:] *= ramp[:, None] if seg.ndim == 2 else ramp
    return seg


def video(args) -> int:
    """The debut's video (data/ceremony/reveal.json `video`): a cut of a file, for the screen on Wi-Fi."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not on PATH")
    asset_id = config().get("video")
    if not isinstance(asset_id, str) or not asset_id.replace("_", "").isalnum():
        raise SystemExit("%s names no video" % CONFIG)
    name = asset_id + ".mp4"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / name
        # (H.264 1080p, a key frame every 2 s, the index at the front: it starts at once from a LAN; no sound
        # -- the debut's sound is the mix)
        done = _ffmpeg("-y", "-ss", "%.3f" % args.cut_from, "-i", str(args.source), "-t", "%.3f" % (args.to - args.cut_from),
                       "-an", "-map_metadata", "-1", "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
                       "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
                       "-movflags", "+faststart", "-g", "60", "-vf", "scale=1920:1080", str(out))
        if done.returncode != 0:
            raise SystemExit("ffmpeg could not write the video:\n" + done.stderr.decode("utf-8", "replace")[-800:])
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                                "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(out)],
                               capture_output=True, check=True).stdout
        info = json.loads(probe)["streams"][0]
        num, den = (int(v) for v in info["r_frame_rate"].split("/"))
        data = out.read_bytes()
    FOLDER.mkdir(parents=True, exist_ok=True)
    (FOLDER / name).write_bytes(data)
    frames = int(info["nb_read_frames"])
    row = {
        "id": asset_id, "kind": "video", "file": name, "sha256": hashlib.sha256(data).hexdigest(),
        "w": int(info["width"]), "h": int(info["height"]), "from_pin": None, "candidate": True,
        "checked": args.checked, "ok": True, "depth": None, "people": [], "about": ["debut"], "class": "other",
        "shows": args.shows, "source": args.source_note, "alpha": False, "bytes": len(data),
        "duration": round(frames * den / num, 3), "fps": round(num / den, 3), "frames": frames,
        "hand": "scripts/ceremony_reveal_sound.py video: %.2f-%.2f s of the file, H.264 1080p CRF %d, no sound"
                % (args.cut_from, args.to, args.crf),
    }
    rows = [r for r in _rows() if not (isinstance(r, dict) and r.get("id") == asset_id)] + [row]
    _write_rows(rows)
    print("debut video: %s  %d frames, %.2f s, %.1f MB" % ((FOLDER / name).relative_to(ROOT), frames, row["duration"],
                                                          len(data) / 1e6))
    return 0


def listened(args) -> int:
    """The owner listened to the debut's sound as it is now: `listened` in its entry, pinned to its bytes
    (a new mix has other bytes: the note no longer counts, the card warns again). Only the person who
    listened runs this, with their own name."""
    sound = config().get("sound")
    rows = _rows()
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == sound), None)
    if row is None:
        raise SystemExit("no %s in %s" % (sound, INDEX))
    data = (FOLDER / row["file"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise SystemExit("%s is not the bytes the index pinned -- mix it again first" % row["file"])
    row["listened"] = {"by": args.by, "on": datetime.date.today().isoformat(), "sha256": row["sha256"],
                       **({"note": args.note} if args.note else {})}
    _write_rows(rows)
    print("%s: listened by %s (pinned to %s...)" % (sound, args.by, row["sha256"][:12]))
    return 0


def show() -> int:
    cfg = config()
    rows = {r.get("id"): r for r in _rows() if isinstance(r, dict)}
    print(json.dumps({"config": cfg, "sound": rows.get(cfg.get("sound")), "video": rows.get(cfg.get("video"))},
                     indent=1, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("mix", help="mix a countdown voice over a music bed into the debut's sound")
    p.add_argument("--music", required=True, help="the music: a bed, a break to silence, a hit")
    p.add_argument("--voice", help="a voice counting down (count_from numbers; none when reveal.json counts 0)")
    p.add_argument("--licence", required=True, help="the licence, who downloaded them and when (kept in the index)")
    p.add_argument("--credit", action="append", help="\"title|author|url\" of each file (repeat)")
    p.add_argument("--start", type=float, help="seconds of the music to start the bed at (else: a bar line, --build)")
    p.add_argument("--build", type=float, default=5.0, help="seconds of music before the break, about (a bar line, 4-6 s)")
    p.add_argument("--beat-period", type=float, help="the bed's beat in seconds, by ear (else measured)")
    p.add_argument("--beat", type=float, default=1.0, help="seconds between the numbers")
    p.add_argument("--blackout", type=float, default=1.5, help="seconds of black and silence between 0 and the hit")
    p.add_argument("--duck", type=float, default=10.0, help="dB the bed goes under each number")
    p.add_argument("--voice-over", type=float, default=0.0, help="dB the voice is over the bed's own loudness")
    p.add_argument("--lufs", type=float, default=-13.5, help="the mix's integrated loudness (Emma: -15.5)")
    p.add_argument("--copy", help="also write the MP3 here (to listen to)")
    p = sub.add_parser("video", help="cut a video for the debut (plays muted from the hit, the logo over it)")
    p.add_argument("source")
    p.add_argument("--from", dest="cut_from", type=float, default=0.0, help="seconds of the file to start at")
    p.add_argument("--to", type=float, required=True, help="seconds of the file to end at")
    p.add_argument("--crf", type=int, default=23)
    p.add_argument("--checked", required=True, help="a person's note: what was looked at (no people, no text, ...)")
    p.add_argument("--shows", required=True, help="what it shows")
    p.add_argument("--source-note", required=True, help="whose file it is and when it came")
    p = sub.add_parser("listened", help="the owner listened to the debut's sound as it is now (his own name)")
    p.add_argument("--by", required=True)
    p.add_argument("--note")
    sub.add_parser("show", help="print the debut's settings, its sound's and its video's entries")
    args = ap.parse_args(argv)
    # The owner runs this in Windows PowerShell (cp1252/cp874 console): `show` prints the index's Thai
    # notes and crashed with UnicodeEncodeError (integration pass 24 Nov). Same as approve_event_script.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    return {"show": lambda: show(), "video": lambda: video(args), "listened": lambda: listened(args)}.get(
        args.cmd, lambda: mix(args))()


if __name__ == "__main__":
    sys.exit(main())
