#!/usr/bin/env python3
"""Khun Miki's questions, voiced, for the ceremony PREVIEW only.

    python scripts/preview_host_voice.py            # renders what is missing or changed
    python scripts/preview_host_voice.py --force    # every line again
    python scripts/preview_host_voice.py --voice-id ID

Owner 18 Nov 2026 (watching the film preview): "อันนี้ขอทำเป็นได้ยินคุณมิกิถามด้วย ขอซับไทย
แต่ของอีเว้นไม่ต้องนะ อันนี้ใช้ตรวจ", then "มิกิพูดภาษาอังกฤษ" -- the preview plays the host's
line in ENGLISH (scripts/preview_host_en.json: translations made here of each cue's `host_th`,
the owner's script having his lines in Thai only) under his Thai words as the subtitle, before
Emma answers, so the watcher hears the exchange
as the hall will. On the day Khun Miki speaks for himself: nothing here is ever served by
the robot or the room screen.

The takes go to .tmp/ceremony-show/host-voice/ (gitignored): <cue>.wav (24 kHz mono 16-bit,
the rate the page plays) + index.json pinning each take to the words it says -- a changed
host line is rendered again, an unchanged one is not (the plan's characters are counted).
A host line in brackets is a stage direction ("(พักรับเสียงหัวเราะ)"), not words: skipped.
The voice is an ElevenLabs voice (ELEVENLABS_API_KEY in this PC's .env, as
scripts/voice_line.py); a stand-in for checking, not Khun Miki.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / ".tmp" / "ceremony-show" / "host-voice"
EN = ROOT / "scripts" / "preview_host_en.json"   # his English (owner 18 Nov: "มิกิพูดภาษาอังกฤษ")
VOICE = "cjVigY5qzO86Huf0OWal"          # "Eric - Smooth, Trustworthy" (premade, male, English)
MODEL = "eleven_v3"
# (first take, 18 Nov: his THAI words in eleven_multilingual_v2, which has no Thai -- "ภาษาอะไรฟัง
# ไม่รู้เรื่อง"; then Thai in v3 (heard back word for word) -- "มิกิพูดภาษาอังกฤษ")


def _voice_line():
    spec = importlib.util.spec_from_file_location("voice_line", ROOT / "scripts" / "voice_line.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def host_lines() -> dict[str, tuple[str, str]]:
    """cue id -> (his Thai words from the script = the subtitle, his English = what is voiced).
    Stage directions in brackets are left out; a line whose English was translated from
    other Thai than the script has now is left out too (named by main), never voiced stale."""
    from app import event_script

    try:
        rows = json.loads(EN.read_text(encoding="utf-8")).get("lines") or {}
    except (OSError, ValueError):
        rows = {}
    out: dict[str, tuple[str, str]] = {}
    for cue in (event_script.load() or {}).get("cues") or []:
        th = str(cue.get("host_th") or "").strip()
        row = rows.get(str(cue["id"])) or {}
        if th and not th.startswith("(") and row.get("th") == th and str(row.get("en") or "").strip():
            out[str(cue["id"])] = (th, str(row["en"]).strip())
    return out


def stale() -> list[str]:
    """Spoken host lines with no English for the Thai as it is now."""
    from app import event_script

    have = host_lines()
    return [str(c["id"]) for c in (event_script.load() or {}).get("cues") or []
            if str(c.get("host_th") or "").strip() and not str(c["host_th"]).strip().startswith("(")
            and str(c["id"]) not in have]


def _key(text: str, voice: str, model: str) -> str:
    return hashlib.sha256(("%s\n%s\n%s" % (voice, model, text)).encode("utf-8")).hexdigest()


def load_index(base: Path = OUT) -> dict:
    try:
        doc = json.loads((base / "index.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def takes(base: Path = OUT) -> dict[str, dict]:
    """cue id -> {"text", "pcm"}: only takes whose words are the host line as it is now."""
    from app.offline.voice import read_wav

    now = host_lines()
    out: dict[str, dict] = {}
    for cid, row in (load_index(base).get("clips") or {}).items():
        if not isinstance(row, dict) or now.get(cid) != (row.get("text"), row.get("en")):
            continue
        pcm = read_wav(base / str(row.get("file")))
        if pcm:
            out[cid] = {"text": row["text"], "pcm": pcm}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--voice-id", default=VOICE)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    import httpx

    vl = _voice_line()
    ffmpeg = vl.find_ffmpeg()
    OUT.mkdir(parents=True, exist_ok=True)
    index = load_index()
    clips = index.setdefault("clips", {})
    todo = [(cid, th, en) for cid, (th, en) in host_lines().items()
            if args.force or (clips.get(cid) or {}).get("key") != _key(en, args.voice_id, args.model)
            or not (OUT / ("%s.wav" % cid)).exists()]
    for cid in stale():
        print("  %s: no English for its Thai as the script has it now -- translate it in %s" % (cid, EN.name))
    print("%d host line(s) to render, %d characters" % (len(todo), sum(len(t) for _, _, t in todo)))
    if not todo:
        return 0
    key = vl.api_key()
    with httpx.Client(base_url=vl.API, headers={"xi-api-key": key}, timeout=120) as client:
        for cid, th, text in todo:
            r = client.post("/v1/text-to-speech/%s" % args.voice_id, params={"output_format": "mp3_44100_128"},
                            json={"text": text, "model_id": args.model})
            if r.status_code != 200:
                print("  %s: HTTP %d %s" % (cid, r.status_code, r.text[:200]))
                continue
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "take.mp3"
                src.write_bytes(r.content)
                vl.prepare(src, OUT / ("%s.wav" % cid), ffmpeg)
            clips[cid] = {"file": "%s.wav" % cid, "text": th, "en": text, "voice": args.voice_id, "model": args.model,
                          "key": _key(text, args.voice_id, args.model)}
            (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
            print("  %s ok" % cid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
