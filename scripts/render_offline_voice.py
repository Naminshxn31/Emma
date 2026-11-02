#!/usr/bin/env python3
"""Render Emma's offline voice: every approved fixed line and every approved
story line, once, in her own voice, while the internet works.

    python scripts/render_offline_voice.py --dry-run            # counts + cost, no API call
    python scripts/render_offline_voice.py --dry-run --only lines
    python scripts/render_offline_voice.py                      # renders what is missing (PAID key)

Through app/voice.py's Gemini TTS path (`_render`): GEMINI_VOICE (the live
session's voice), ONE model for every clip (OFFLINE_VOICE_MODEL, default
gemini-3.8-flash-tts -- Thai supported), keyed on model + voice + language +
text (app/offline/voice.py `clip_key`). One model for all on purpose: the
repo already heard two TTS models both called "Despina" sound different
(CHANGELOG, the face-scan clips) -- a cache in two voices is the bug, so a
render into an index made by another model or voice is refused (start a
fresh folder with --fresh).

Only approved text is ever rendered: drafts are not in `lines.servable()`
and an unapproved story catalog offers no stories. Text that changed after
a render simply has no clip (the key is over the words), so the page shows
the words without the voice until this runs again.

The free tier is no use here: the repo measured ~10 TTS renders a day per
model (app/voice.py). Run it on a paid key, outside opening hours -- the key
is shared with the live sales room. Output 24 kHz mono PCM16 WAV, the Live
API's own format (the face plays it like a live reply).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.offline import lines, voice  # noqa: E402

#: Speaking rates the face measured (emma-story.js voiceRate): Thai 12.5,
#: English 15 characters a second -- for the duration estimate only.
CHARS_PER_S = {"th": 12.5, "en": 15.0}
#: Gemini TTS Standard tier through 31 Dec 2026 (models research, 26 Oct):
#: $9.00 per 1M output audio tokens at 25 tokens a second, $0.50 per 1M input
#: text tokens (~4 characters a token). An ESTIMATE, printed as one.
AUDIO_USD_PER_S = 9.00 / 1_000_000 * 25
TEXT_USD_PER_CHAR = 0.50 / 1_000_000 / 4


def wanted(only: str | None = None) -> list[tuple[str, str, str]]:
    """(source, lang, text), unique by language + text, in a stable order."""
    out: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, lang: str, text: str) -> None:
        text = lines.norm(text)
        if text and (lang, text) not in seen:
            seen.add((lang, text))
            out.append((source, lang, text))

    if only in (None, "lines"):
        for line_id, line in sorted(lines.servable().items()):
            for lang in lines.LANGS:
                add("line:" + line_id, lang, line.get(lang) or "")
    if only in (None, "stories"):
        from app import stories

        for sid, story in sorted(stories.available().items()):
            for line in story.get("lines") or []:
                for lang in lines.LANGS:
                    add("story:" + sid, lang, line.get(lang) or "")
    return out


def plan(items: list[tuple[str, str, str]], model: str, base: Path) -> dict:
    index = voice.load_index(base)
    have = 0
    todo = []
    for source, lang, text in items:
        key = voice.clip_key(model, settings.gemini_voice, lang, text)
        entry = (index or {}).get("clips", {}).get(key) if index and index.get("model") == model else None
        if entry and (base / entry["file"]).is_file():
            have += 1
        else:
            todo.append((source, lang, text, key))
    chars = {lang: sum(len(t) for _, l_, t, _ in todo if l_ == lang) for lang in lines.LANGS}
    seconds = sum(chars[lang] / CHARS_PER_S[lang] for lang in lines.LANGS)
    cost = seconds * AUDIO_USD_PER_S + sum(chars.values()) * TEXT_USD_PER_CHAR
    return {"total": len(items), "have": have, "todo": todo, "chars": chars, "seconds": seconds,
            "usd": cost, "index_model": (index or {}).get("model"), "index_voice": (index or {}).get("voice")}


def _write_index(base: Path, index: dict) -> None:
    tmp = base / (voice.INDEX + ".part")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, base / voice.INDEX)


def render(todo, model: str, base: Path, fresh: bool = False) -> int:
    from app import voice as tts

    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set -- nothing rendered")
    index = voice.load_index(base)
    if index and not fresh and (index.get("model") != model or index.get("voice") != settings.gemini_voice):
        raise SystemExit("the clips in %s are model %r voice %r -- one voice for everything: "
                         "render with that model, or start over with --fresh"
                         % (base, index.get("model"), index.get("voice")))
    if fresh or not index:
        index = {"schema": voice.SCHEMA, "model": model, "voice": settings.gemini_voice,
                 "rate": voice.RATE, "clips": {}}
    base.mkdir(parents=True, exist_ok=True)
    done = 0
    for n, (source, lang, text, key) in enumerate(todo, 1):
        for attempt in range(6):
            try:
                pcm, rate = tts._render(text, model)
                break
            except Exception as exc:
                message = str(exc).lower()
                if any(t in message for t in tts._QUOTA_DAY):
                    print("today's quota is used up after %d clips -- run again tomorrow" % done)
                    return done
                if any(t in message for t in tts._RATE_LIMIT) and attempt < 5:
                    time.sleep(min(60.0, 5.0 * 2 ** attempt))
                    continue
                raise SystemExit("render failed on %s (%s): %s -- %d clips kept" % (source, lang, exc, done))
        if rate != voice.RATE:
            raise SystemExit("%s returned %d Hz, not %d -- not stored" % (model, rate, voice.RATE))
        name = key + ".wav"
        voice.write_wav(base / name, pcm)
        index["clips"][key] = {"file": name, "lang": lang,
                               "sha256": hashlib.sha256((base / name).read_bytes()).hexdigest()}
        _write_index(base, index)          # after every clip: an interrupted run keeps what it paid for
        done += 1
        print("[%d/%d] %s %s" % (n, len(todo), lang, source), flush=True)
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="counts, characters and cost; no API call")
    ap.add_argument("--only", choices=["lines", "stories"])
    ap.add_argument("--model", default=settings.offline_voice_model)
    ap.add_argument("--fresh", action="store_true", help="start a new index (another model/voice)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    base = voice.folder()
    items = wanted(args.only)
    p = plan(items, args.model, base)
    status = lines.status()
    print("approved fixed lines: %d of %d (drafts are never rendered)" % (status["approved"], status["total"]))
    print("clips wanted: %d (%s) | already rendered: %d | to render: %d"
          % (p["total"], args.only or "lines + stories", p["have"], len(p["todo"])))
    print("characters to render: th %d, en %d | ~%.0f s of speech (~%.1f min)"
          % (p["chars"]["th"], p["chars"]["en"], p["seconds"], p["seconds"] / 60))
    print("model %s, voice %s | ESTIMATED cost ~$%.2f (Standard tier, Gemini TTS pricing as of Oct 2026)"
          % (args.model, settings.gemini_voice, p["usd"]))
    if p["index_model"] and (p["index_model"] != args.model or p["index_voice"] != settings.gemini_voice):
        print("NOTE: %s already holds clips of model %r voice %r -- rendering needs --fresh"
              % (base, p["index_model"], p["index_voice"]))
    if args.dry_run or not p["todo"]:
        return 0
    done = render(p["todo"], args.model, base, fresh=args.fresh)
    print("rendered %d clip(s) into %s" % (done, base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
