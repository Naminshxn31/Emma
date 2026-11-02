"""Emma's pre-rendered voice for when there is no Gemini to speak with.

`data/offline/voice/` (gitignored): one WAV per sentence (24 kHz mono PCM16,
the Live API's own output format, so `ready.output_rate` stays 24000 and the
face plays a clip exactly like a live reply) and `index.json`:

    {"schema": 1, "model": "<tts model>", "voice": "<GEMINI_VOICE>", "rate": 24000,
     "clips": {"<key>": {"file": "<key>.wav", "sha256": "...", "lang": "th"}}}

`key = sha256(model | voice | lang | normalized text)` -- app/voice.py's rule
(`_key`): two models do not sound like each other, nor do two voices, and a
cache keyed on the text alone serves yesterday's speaker in today's voice
with no symptom but "something sounds off". The runtime takes the model
from the index (one model renders everything) and the voice from
GEMINI_VOICE: a voice changed since the render finds no clip and says
nothing, instead of a different woman answering.

Every clip is re-hashed before use (the sha256 in the index). A missing,
changed or wrong-format clip = no audio for that line: the words still go to
the bubble and the story still runs on its own clock (emma-story.js
noAudioS), which is what the face does today when audio stalls.

No local TTS model ships with this round. The one Thai female voice that
runs here (VIZINTZOR/MMS-TTS-THAI-FEMALEV2) is a fine-tune of Meta's MMS,
whose weights are CC-BY-NC -- not for a sales room -- and it is a different,
more synthetic woman than Emma (models research, 26 Oct). Clips rendered in
Emma's own voice while online cover every fixed and story line.
"""
from __future__ import annotations

import hashlib
import json
import logging
import wave
from pathlib import Path

from app.offline.lines import norm

logger = logging.getLogger("condo_voice.offline.voice")

ROOT = Path(__file__).resolve().parents[2]
RATE = 24000
SCHEMA = 1
INDEX = "index.json"


def folder() -> Path:
    from app.config import settings

    base = Path(settings.offline_voice_dir).expanduser()
    return base if base.is_absolute() else ROOT / base


def clip_key(model: str, voice: str, lang: str, text: str) -> str:
    stamp = "%s|%s|%s|%s" % (model, voice, lang, norm(text))
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()


_index_cache: tuple | None = None


def reset() -> None:
    global _index_cache
    _index_cache = None


def load_index(base: Path | None = None) -> dict | None:
    global _index_cache
    base = base or folder()
    file = base / INDEX
    try:
        stat = file.stat()
    except OSError:
        return None
    key = (str(file), stat.st_mtime_ns, stat.st_size)
    if _index_cache is not None and _index_cache[0] == key:
        return _index_cache[1]
    data = None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if (isinstance(raw, dict) and raw.get("schema") == SCHEMA and isinstance(raw.get("clips"), dict)
                and isinstance(raw.get("model"), str) and isinstance(raw.get("voice"), str)):
            data = raw
        else:
            logger.warning("offline voice index has the wrong shape: %s", file)
    except (OSError, ValueError):
        logger.warning("offline voice index unreadable: %s", file)
    _index_cache = (key, data)
    return data


def read_wav(path: Path) -> bytes | None:
    """PCM16 mono 24 kHz frames, or None for anything else."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != RATE:
                logger.warning("offline clip %s is not 24 kHz mono PCM16 -- not played", path.name)
                return None
            return w.readframes(w.getnframes())
    except (OSError, wave.Error, EOFError):
        return None


def write_wav(path: Path, pcm: bytes, rate: int = RATE) -> None:
    """Beside and rename (app/voice.py's rule): a render interrupted halfway
    never leaves a truncated file that a later lookup treats as a clip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    with wave.open(str(temp), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    temp.replace(path)


def pcm_for(text: str, lang: str, base: Path | None = None) -> bytes | None:
    """The clip's PCM for these words in this language, verified, or None."""
    from app.config import settings

    base = base or folder()
    index = load_index(base)
    if not index or not norm(text):
        return None
    if index.get("voice") != settings.gemini_voice:
        logger.info("offline clips are in voice %r, GEMINI_VOICE is %r -- not played",
                    index.get("voice"), settings.gemini_voice)
        return None
    entry = index["clips"].get(clip_key(index["model"], index["voice"], lang, text))
    if not isinstance(entry, dict):
        return None
    name = entry.get("file")
    if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith("."):
        return None
    file = base / name
    try:
        raw = file.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        logger.warning("offline clip %s does not match its pin -- not played", name)
        return None
    return read_wav(file)


def chunks(pcm: bytes, ms: int = 100) -> list[bytes]:
    """The clip in pieces the size of a live reply's chunks: the page
    schedules each as it arrives, and a barge-in can stop between them."""
    step = max(2, int(RATE * 2 * ms / 1000) // 2 * 2)
    return [pcm[i:i + step] for i in range(0, len(pcm), step)]


def coverage(wanted: list[tuple[str, str]], base: Path | None = None) -> dict:
    """How many of (lang, text) have a playable clip -- for the boot line."""
    have = sum(1 for lang, text in wanted if pcm_for(text, lang, base) is not None)
    return {"wanted": len(wanted), "have": have}
