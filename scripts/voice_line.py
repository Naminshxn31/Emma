"""A scene's line recorded ahead in a voice the owner picked by ear (app/event_host.py `recorded`).

Owner 10 Nov 2026, after a blind listening test (Despina, the robot's voice, beside ElevenLabs
voices): "พอเป็นเสียงภาษาไทยก็อันเดิมดี แต่พอเป็นอังกฤษ อีกตัวดีกว่า" -> "Jessica v3 รอบแรก (C)
ชอบเสียงนี้" -> "เราใช้โชว์แค่วันงาน". The debut's English pageant line is played from a file in
that voice; every other word she says is still the live model's (Despina).

    python scripts/voice_line.py render --voice-id ID --model eleven_v3 --text "..." \\
        --out data/events/clips/NAME.wav [--takes 3]
    python scripts/voice_line.py prepare SRC.mp3 --out data/events/clips/NAME.wav

Every WAV comes out 16-bit mono at 24 kHz -- the Live API's output rate, what the face plays
(app/event_host.recorded_pcm refuses anything else) -- at LUFS -15.5: Despina's own debut line
measured -14.7 and her answers -15.8 / -18.0 (10 Nov, the same lines rendered through
gemini-3.8-live), so the clip is not louder or softer than her. The sha256 printed is what the
scene's `recorded` pins: a re-render is another take, unheard, and is not played until pinned.

The key is ELEVENLABS_API_KEY in this PC's .env (scripts/robot_server.py ENV_DROP keeps it off
the robot: the robot only plays the file). A clip rendered on the FREE plan has no commercial
licence (ElevenLabs' price page, Free: "Commercial license" struck through) -- the event is
commercial: render the take that plays on the day on a paid plan.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.elevenlabs.io"
RATE = 24000
LUFS = -15.5
TRUE_PEAK = -1.5


def find_ffmpeg(given: str | None = None) -> str:
    if given:
        return given
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for exe in sorted(Path(local, "Microsoft", "WinGet", "Packages").glob("Gyan.FFmpeg*/**/ffmpeg.exe")):
            return str(exe)
    raise SystemExit("ffmpeg not found: pass --ffmpeg PATH")


def ffmpeg_args(ffmpeg: str, src: Path, out: Path) -> list[str]:
    """Mono, 24 kHz, 16-bit, loudness-matched to her live voice."""
    return [ffmpeg, "-y", "-loglevel", "error", "-i", str(src),
            "-af", "loudnorm=I=%s:TP=%s:LRA=11" % (LUFS, TRUE_PEAK),
            "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(out)]


def describe(path: Path) -> str:
    data = path.read_bytes()
    with wave.open(str(path)) as w:
        seconds = w.getnframes() / float(w.getframerate())
        shape = "%d ch, %d bit, %d Hz" % (w.getnchannels(), 8 * w.getsampwidth(), w.getframerate())
    return "%s  %.2f s  %s  sha256 %s" % (path.relative_to(ROOT) if path.is_relative_to(ROOT) else path,
                                          seconds, shape, hashlib.sha256(data).hexdigest())


def prepare(src: Path, out: Path, ffmpeg: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(ffmpeg_args(ffmpeg, src, out), check=True)
    print(describe(out))


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key and (ROOT / ".env").is_file():
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is not set (this PC's .env)")
    return key


def render(args, ffmpeg: str) -> None:
    import httpx

    body = {"text": args.text, "model_id": args.model}
    if args.stability is not None:
        body["voice_settings"] = {"stability": args.stability}
    with httpx.Client(base_url=API, headers={"xi-api-key": api_key()}, timeout=180) as client:
        for n in range(1, args.takes + 1):
            r = client.post("/v1/text-to-speech/%s" % args.voice_id, params={"output_format": "mp3_44100_128"},
                            json=body)
            if r.status_code != 200:
                raise SystemExit("ElevenLabs %d: %s" % (r.status_code, r.text[:300]))
            out = args.out if args.takes == 1 else args.out.with_name("%s_take%d%s" % (args.out.stem, n, args.out.suffix))
            with tempfile.TemporaryDirectory() as tmp:
                mp3 = Path(tmp) / "take.mp3"
                mp3.write_bytes(r.content)
                prepare(mp3, out, ffmpeg)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ffmpeg", help="ffmpeg.exe (default: PATH, then winget's Gyan.FFmpeg)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="an audio file -> the WAV the robot plays")
    p.add_argument("src", type=Path)
    p.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("render", help="ElevenLabs text-to-speech -> the WAV the robot plays")
    r.add_argument("--voice-id", required=True)
    r.add_argument("--model", default="eleven_v3")
    r.add_argument("--text", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--takes", type=int, default=1)
    r.add_argument("--stability", type=float, help="v3: 0.0 creative, 0.5 natural, 1.0 robust (default: the voice's)")
    args = ap.parse_args(argv)
    ffmpeg = find_ffmpeg(args.ffmpeg)
    if args.cmd == "prepare":
        prepare(args.src, args.out, ffmpeg)
    else:
        render(args, ffmpeg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
