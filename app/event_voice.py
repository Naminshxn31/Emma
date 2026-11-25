"""Emma's recorded voice for the ceremony's cue lines (app/event_script.py).

Staff press a cue and the room must hear the approved line word for word. A live
session asked to read it (`event_script.READ_RULE`) can still drop or add a word;
a recording made beforehand and checked against the line cannot. So the cue
player (app/event_cues.py) plays the recording when there is one, as if it were
her live reply, and asks the live session to read the line only when there is not.

It has to be HER LIVE VOICE. This repo already heard two TTS models both called
"Despina" sound like two different women (CHANGELOG, the face-scan clips;
app/offline/voice.py), so these clips are not TTS: scripts/render_event_script.py
sends the cue's read turn (`event_script.read_text`) to a Gemini Live session on
the SAME model and voice the robot talks with (GEMINI_MODEL, GEMINI_VOICE) and
keeps the audio it answers with, plus that session's own output transcription.

`<ceremony file's folder>/voice/` -- data/ceremony/voice/ for the real ceremony since 9 Nov
(it was data/events/voice/ beside the event file until the ceremony became an app of its own;
gitignored: rendered audio, rebuilt rather than history). CEREMONY_FILE empty = no folder, so
a test never meets the clips this PC rendered (conftest empties CEREMONY_FILE):

    {"schema": 1, "model": "<live model>", "voice": "<GEMINI_VOICE>", "rate": 24000,
     "clips": {"<key>": {"file": "<key>.wav", "sha256": "...", "lang": "th",
                         "cue": "<cue id>", "fidelity": 0.97, "min_fidelity": 0.92,
                         "transcript": "<what the session said it said>",
                         "numbers": {"missing": [], "extra": []}, "seconds": 7.4,
                         "takes": 3, "rendered_at": "2026-11-06T22:41:00+0700"}}}

The rules are the offline clips' plus the transcript's:

* `key = sha256(model | voice | lang | normalised LINE)` (app/offline/voice.py
  `clip_key`) -- over the line's words, not the read turn around them: an edited
  line simply has no clip (and is a draft again, `event_script.digest`).
* Played only while the index's model is GEMINI_MODEL and its voice GEMINI_VOICE:
  a model or voice changed since the render = no clip (the line is read live),
  never another woman saying it.
* Every clip is re-hashed before it is played, and must be 24 kHz mono PCM16 --
  the Live API's own output, so the face plays it exactly like a live reply.
* The transcript decides, again at playback: fidelity of the stored transcript to
  the CURRENT line (app/speech_match.py) must reach the bar the take was accepted
  at, and that bar may not be under FIDELITY_FLOOR; a number she said that the
  line does not have (`number_check` extra), or one of the line's that the
  transcript has neither as digits nor in Thai words (`unspoken_numbers` -- a
  dropped "155" costs ~2% fidelity, under any bar) = not played. Recomputed, not
  read from the index, so a hand-edited "fidelity" cannot let a take through.
* NOT the approval. This module answers "is there a good recording of these
  words"; the cue player answers "may this cue be said" (event_script.servable(),
  drafts never). A preview render of a draft (`--drafts`) therefore has a clip
  that status() lists and the player never plays until a person signs the cue --
  and signing the same words keeps the clip, because the key is the words.
"""
from __future__ import annotations

import hashlib
import json
import logging
from array import array
from pathlib import Path

from app.offline.lines import norm
from app.offline.voice import clip_key, read_wav, write_wav  # noqa: F401  (one key rule, one WAV format)

logger = logging.getLogger("condo_voice.event_voice")

RATE = 24000
SCHEMA = 1
INDEX = "index.json"
#: The bar a take must reach to be kept (scripts/render_event_script.py
#: --min-fidelity). Measured on the preview render of 6 Nov 2026 (see
#: render_event_script.py's docstring for the numbers).
DEFAULT_MIN_FIDELITY = 0.92
#: Never played below this, whatever bar the index says the take was accepted
#: at -- the renderer refuses a lower --min-fidelity too.
FIDELITY_FLOOR = 0.90
#: A sample at or over this is her voice (~-40 dBFS): the provider's VOICED_PEAK
#: (app/providers/gemini.py), used here to trim the silence at both ends.
VOICED_PEAK = 328


def folder() -> Path | None:
    """`voice/` beside the ceremony file (its lines are the ceremony's), or None when no
    ceremony is configured."""
    from app import event_script

    script = event_script.path()
    return None if script is None else script.parent / "voice"


_index_cache: tuple | None = None
#: file -> (mtime_ns, size, sha256): status() polls without re-reading every clip.
#: pcm() never uses it -- what is played is hashed as it is read.
_hash_cache: dict[str, tuple[int, int, str]] = {}
#: file -> (mtime_ns, size, is 24 kHz mono PCM16): the staff page polls status()
#: every 10 s, and on the robot that re-read every WAV each time (review 6 Nov).
_format_cache: dict[str, tuple[int, int, bool]] = {}


def reset() -> None:
    global _index_cache
    _index_cache = None
    _hash_cache.clear()
    _format_cache.clear()


def load_index(base: Path | None = None) -> dict | None:
    """The index, cached on its stat; None = no folder, no file, or not ours."""
    global _index_cache
    base = base if base is not None else folder()
    if base is None:
        return None
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
                and isinstance(raw.get("model"), str) and isinstance(raw.get("voice"), str)
                and raw.get("rate") == RATE):
            data = raw
        else:
            logger.warning("event voice index has the wrong shape: %s", file)
    except (OSError, ValueError):
        logger.warning("event voice index unreadable: %s", file)
    _index_cache = (key, data)
    return data


def _model(name: str | None) -> str:
    return (name or "").removeprefix("models/")


# ---- a number said in words ------------------------------------------------------
#
# The preview render of 6 Nov: one s3_joke take's transcript wrote "สระหนึ่งร้อยห้าสิบห้า
# เมตร" where the others write "155", so `number_check` called 155 missing. And a take
# that DROPPED "155" loses 3 of ~180 characters -- fidelity ~0.98, over any bar. So a
# number missing from the transcript's digits counts as said only when it is there
# spelled in Thai; otherwise the take is not kept (render) and not played (here).

_TH_DIGIT = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
_TH_PLACE = ("", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน", "ล้าน")


def _thai_int(n: int) -> set[str]:
    """Ways a whole number (< 10,000,000) is said: 801 = แปดร้อยเอ็ด or แปดร้อยหนึ่ง,
    155 = หนึ่งร้อยห้าสิบห้า or ร้อยห้าสิบห้า."""
    if n == 0:
        return {_TH_DIGIT[0]}
    if n >= 10_000_000:
        return set()
    digits = [int(d) for d in str(n)]
    words = []
    for i, d in enumerate(digits):
        place = len(digits) - 1 - i
        if d == 0:
            continue
        if place == 0:
            words.append("เอ็ด" if d == 1 and n >= 10 else _TH_DIGIT[d])
        elif place == 1:
            words.append("สิบ" if d == 1 else "ยี่สิบ" if d == 2 else _TH_DIGIT[d] + "สิบ")
        else:
            words.append(_TH_DIGIT[d] + _TH_PLACE[place])
    text = "".join(words)
    out = {text}
    if text.endswith("เอ็ด"):
        out.add(text[: -len("เอ็ด")] + "หนึ่ง")
    out |= {w[len("หนึ่ง"):] for w in list(out) if w.startswith("หนึ่ง") and len(digits) >= 3}
    return out


def thai_number_words(value: float) -> set[str]:
    """Thai spellings of a number as a line writes it (1066, 1.3, 2500)."""
    text = ("%f" % value).rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return set()
    out = _thai_int(int(whole))
    if frac:
        out = {w + "จุด" + "".join(_TH_DIGIT[int(d)] for d in frac) for w in out}
    return out


def unspoken_numbers(heard: str | None, line: str | None) -> list[float]:
    """Numbers of the line that the transcript has neither as digits, nor in Thai words, nor
    in English words ("six hundred", "one point three", "eighth": an English line -- 17 Nov,
    the ceremony spoken in English; with Thai words only, every English clip that read a
    number aloud in words counted as having dropped it, and one that dropped it was not told
    apart)."""
    from app import event_script, speech_match

    folded = speech_match.fold(heard)
    english = [v for _s, _e, v in event_script.english_numerals(heard)]
    return [v for v in speech_match.number_check(heard, line)["missing"]
            if not any(speech_match.fold(w) in folded for w in thai_number_words(v))
            and not any(abs(v - e) < 1e-6 for e in english)]


def line_fidelity(heard: str | None, line: str | None) -> float:
    """speech_match.fidelity, with each number the transcript spelled in Thai words
    spelled the same way in the line first: "155" heard as "ร้อยห้าสิบห้า" is the
    line said right, and on a short line those twelve letters alone fell under
    the bar (0.87 for a 55-character line)."""
    from app import speech_match

    score = speech_match.fidelity(heard, line)
    folded = speech_match.fold(heard)
    missing = speech_match.number_check(heard, line)["missing"]
    if not missing or not line:
        return score
    spelled = line
    for m in reversed(list(speech_match._NUMBER.finditer(line))):
        try:
            value = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        if not any(abs(value - v) < 1e-6 for v in missing):
            continue
        said = [w for w in sorted(thai_number_words(value), key=len, reverse=True)
                if speech_match.fold(w) in folded]
        if said:
            spelled = spelled[:m.start()] + said[0] + spelled[m.end():]
    return max(score, speech_match.fidelity(heard, spelled))


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _judge(c: dict, lang: str, base: Path | None) -> tuple[str | None, dict | None, Path | None, float | None]:
    """(why not, entry, file, fidelity now) for the clip of this cue's line in
    `lang`; why = None when the clip may be played (its bytes still to check)."""
    from app import speech_match
    from app.config import settings

    line = norm(c.get(lang)) if isinstance(c, dict) and isinstance(c.get(lang), str) else ""
    if not line:
        return "no_line", None, None, None
    base = base if base is not None else folder()
    if base is None:
        return "no_folder", None, None, None
    index = load_index(base)
    if not index:
        return "no_index", None, None, None
    if _model(index.get("model")) != _model(settings.gemini_model):
        return "model_changed", None, None, None
    if index.get("voice") != settings.gemini_voice:
        return "voice_changed", None, None, None
    entry = index["clips"].get(clip_key(index["model"], index["voice"], lang, line))
    if not isinstance(entry, dict) or entry.get("lang") != lang:
        return "no_clip", None, None, None
    name = entry.get("file")
    if not isinstance(name, str) or "/" in name or "\\" in name or name.startswith(".") or not name.endswith(".wav"):
        return "bad_entry", entry, None, None
    bar = _number(entry.get("min_fidelity"))
    if bar is None or bar < FIDELITY_FLOOR:
        return "bar_too_low", entry, None, None
    transcript = entry.get("transcript")
    score = line_fidelity(transcript if isinstance(transcript, str) else "", line)
    if score < bar:
        return "below_bar", entry, None, score
    if speech_match.number_check(transcript, line)["extra"]:
        return "extra_number", entry, None, score
    if unspoken_numbers(transcript, line):
        return "number_not_said", entry, None, score
    return None, entry, base / name, score


def pcm(c: dict, lang: str = "th", base: Path | None = None, *, sha: str | None = None) -> bytes | None:
    """The recorded PCM (24 kHz mono 16-bit) of this cue's CURRENT line, or None.

    None whenever anything is off -- no folder/index/clip, another model or voice,
    a take under its bar, a file that is not the pinned bytes or not the format.
    The approval is the caller's (event_script.servable()): see the module doc.
    `sha`: only that clip (the take a person listened to and approved,
    `approved_voice_sha256`) -- checked against the bytes read HERE, not an earlier
    look, so a `--fresh` render in between is not played under the old approval."""
    why, entry, file, _score = _judge(c, lang, base)
    if why is not None or entry is None or file is None:
        return None
    if sha is not None and entry.get("sha256") != sha:
        return None
    try:
        raw = file.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
        logger.warning("event clip %s does not match its pin -- not played", file.name)
        return None
    return read_wav(file)


def _pinned(file: Path, sha: object) -> bool:
    try:
        st = file.stat()
    except OSError:
        return False
    seen = _hash_cache.get(str(file))
    if seen is None or seen[:2] != (st.st_mtime_ns, st.st_size):
        try:
            seen = (st.st_mtime_ns, st.st_size, hashlib.sha256(file.read_bytes()).hexdigest())
        except OSError:
            return False
        _hash_cache[str(file)] = seen
    return seen[2] == sha


def _plays(file: Path) -> bool:
    """read_wav() would take it -- cached on (mtime, size): status() is polled."""
    try:
        st = file.stat()
    except OSError:
        return False
    seen = _format_cache.get(str(file))
    if seen is None or seen[:2] != (st.st_mtime_ns, st.st_size):
        seen = (st.st_mtime_ns, st.st_size, read_wav(file) is not None)
        _format_cache[str(file)] = seen
    return seen[2]


def _checked(c: dict, lang: str, base: Path | None) -> tuple[str | None, dict | None, float | None]:
    why, entry, file, score = _judge(c, lang, base)
    if why is None and file is not None and entry is not None:
        if not _pinned(file, entry.get("sha256")):
            why = "not_pinned"
        elif not _plays(file):
            why = "bad_format"
    return why, entry, score


def clip_sha(c: dict, lang: str = "th", base: Path | None = None) -> str | None:
    """The sha256 of the clip pcm() would return for this cue now (same checks), or
    None. What `approved_voice_sha256` is compared with: approval signs the words,
    and the owner's listening signs this take (review 6 Nov)."""
    why, entry, _score = _checked(c, lang, base)
    sha = entry.get("sha256") if why is None and entry is not None else None
    return sha if isinstance(sha, str) else None


def status(c: dict, lang: str = "th", base: Path | None = None) -> dict:
    """{"recorded", "fidelity", "seconds", "why"} for the staff page and the CLI.

    recorded = pcm() would return a clip (the approval aside); fidelity/seconds
    are that clip's, None when there is none; why names what is missing."""
    why, entry, score = _checked(c, lang, base)
    if why is not None:
        return {"recorded": False, "fidelity": None, "seconds": None, "why": why}
    seconds = _number(entry.get("seconds"))
    return {"recorded": True, "fidelity": round(score, 3) if score is not None else None,
            "seconds": round(seconds, 2) if seconds is not None else None, "why": None}


def seconds_of(pcm16: bytes, rate: int = RATE) -> float:
    return len(pcm16) / 2 / rate


def trim_silence(pcm16: bytes, *, rate: int = RATE, peak: int = VOICED_PEAK,
                 lead_ms: int = 60, tail_ms: int = 250) -> bytes:
    """The take without the silence before her first sound and after her last.

    A little room is kept at each end (`lead_ms` before the first voiced sample,
    `tail_ms` after the last: a final syllable decays under the peak before it
    ends). All silence = b"" -- nothing to keep."""
    n = len(pcm16) // 2
    if n == 0:
        return b""
    samples = array("h")
    samples.frombytes(pcm16[: n * 2])
    first = next((i for i in range(n) if samples[i] >= peak or samples[i] <= -peak), None)
    if first is None:
        return b""
    last = next(i for i in range(n - 1, -1, -1) if samples[i] >= peak or samples[i] <= -peak)
    start = max(0, first - rate * lead_ms // 1000)
    end = min(n, last + 1 + rate * tail_ms // 1000)
    return pcm16[start * 2:end * 2]
