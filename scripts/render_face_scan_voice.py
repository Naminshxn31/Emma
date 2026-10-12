"""
Render the face scan's spoken English prompts to static files, once.

Owner, 2026-09-29: "ตอนแสกนให้พูดบอกด้วยคนจะได้รู้ แล้วขอภาษาอังกฤษ" — the
person in front of the lens is looking at the lens, not the screen, so the
steps have to be heard. Static files rather than `speechSynthesis` because
the robot's Android WebView has no speech synthesis, and rather than the
station's `app/voice.py` cache because that follows TTS_PROVIDER (local =
a Thai-only VITS model) and renders on demand against a free tier of about
ten sentences a day. These eight lines never change, so they are rendered
here, in Emma's own voice (GEMINI_VOICE), and served from client/assets.

    python scripts/render_face_scan_voice.py          # only the missing ones
    python scripts/render_face_scan_voice.py --force  # all of them again

Keys match `enrollment.STEPS` keys plus three of the page's own moments; the
page (client/voice-preview.html, face scan script) plays <key>.wav.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "client" / "assets" / "face-scan" / "voice"

LINES = {
    # one per enrollment.STEPS key, in the same order
    "front": "Please look straight at the camera.",
    "side_a": "Now, slowly turn your head to your right.",
    "side_b": "Great. Now turn to the other side.",
    "far": "Please take one step back.",
    "front2": "Look straight at the camera again, and hold a smile.",
    # the page's own moments
    "noface": "I can't see your face yet. Please stand in front of the camera.",
    "done": "All done! Thank you.",
    "saved": "Thank you! I'll remember you now.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--force", action="store_true", help="render every line again")
    args = parser.parse_args(argv)

    from app import enrollment, voice
    from app.config import settings

    missing_steps = [s["key"] for s in enrollment.STEPS if s["key"] not in LINES]
    if missing_steps:
        print(f"no English line for step(s) {missing_steps} — add them to LINES")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    failed = 0
    for key, text in LINES.items():
        path = OUT / f"{key}.wav"
        if path.exists() and not args.force:
            print(f"  keep   {key}")
            continue
        for model in voice.MODELS:
            try:
                pcm, rate = voice._render(text, model)
            except Exception as exc:                      # quota, withdrawn model, network
                print(f"  {model}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            voice._write_wav(path, pcm, rate)
            print(f"  wrote  {key}  ({len(pcm) / 2 / rate:.1f}s, {model}, voice {settings.gemini_voice})")
            break
        else:
            failed += 1
            print(f"  FAILED {key}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
