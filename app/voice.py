"""Emma's voice for the pages this project serves itself.

Why this exists at all
----------------------
The enrolment station has to say "turn your head" to somebody who is looking
at a lens, not at a screen. The browser can do that for free — except that
`speechSynthesis` can only read Thai if the machine has a Thai voice, and the
showroom machine has none. Measured, not assumed:

    SAPI5 tokens        David, Zira                     both en-US
    OneCore tokens      DavidM, MarkM, ZiraM            all  en-US
    Settings -> Manage voices -> Add voices             nothing Thai
    Edge (its own voice list)                           the same three

A Thai sentence read by an English voice is worse than silence, so that path
ends in silence, which is what the owner heard. Sending them off to install a
language pack was advice that could not be taken.

So the sentence is rendered by the same model family that gives the robot its
voice, in the voice the robot already uses (`GEMINI_VOICE`), and written to
disk. A phrase is paid for once, ever: the five instructions are rendered
when the station starts and never again, on this machine or any other with
the same cache. Only a new person's name is ever a new phrase.

This is not the live audio path — nothing here goes near a session. It is a
file cache with a renderer behind it, and every failure returns None, because
the caller is a web request from a page standing between a colleague and a
camera. Silence with a beep is a bad outcome; a traceback is a worse one.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import wave
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.voice")

CACHE = Path(__file__).resolve().parent.parent / "data" / "faces" / "voice"

#: Tried in order, and only on the errors that mean "this model is not
#: available" — the same rule `GEMINI_MODEL_FALLBACK` follows for the live
#: session. Falling back on any error at all turns a five-second network
#: hiccup into a permanent, silent downgrade nobody goes back to check.
MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts")

#: The in-machine renderer (TTS_PROVIDER=local): MMS-VITS finetuned for
#: Thai, ~150MB, renders a sentence in 1-2s on this CPU with no quota and
#: no network. It exists because the free tier renders ten sentences a day
#: — which the very first warm-up spent — and because a name is a sentence
#: nobody has rendered before, so with Gemini the station could not speak
#: names at all. The trade is the voice: this is not Kore. One flow, one
#: voice still holds — switching provider means an empty cache, and
#: `state()` says so out loud if two voices ever share the directory.
LOCAL_MODEL = "VIZINTZOR/MMS-TTS-THAI-FEMALEV2"
LOCAL_TAG = "local/" + LOCAL_MODEL

_local = None  # (tokenizer, model), loaded once behind _lock

_MISSING_MODEL = ("not found", "404", "not supported", "unavailable")

#: A rate limit is not a withdrawn model, and treating it as one produced a
#: cache in *two voices*: warming seven lines back to back spends the free
#: tier's three requests a minute, the next three fell through to the older
#: model, and the station then read four instructions in one voice and three
#: in another. The same mistake as mixing two embedding spaces, in audio.
#: Per-minute limits are waited out — this project already learned that
#: building embeddings — so the fix is to sleep and ask the same model again.
_RATE_LIMIT = ("429", "resource_exhausted", "quota", "rate limit")

#: And a per-*day* quota is not a per-minute one. Measured: the free tier
#: allows ten TTS renders a day per model, and the API says which limit was
#: hit inside the same 429. Sleeping through a daily cap is 135 seconds spent
#: to be refused again — waiting is only the right answer when the thing
#: being waited for arrives.
_QUOTA_DAY = ("perday", "per day")

#: One at a time. Two colleagues cannot be enrolled at once — there is one
#: camera and one station — and rendering is the only thing here that costs
#: anything.
_lock = threading.Lock()

#: Models whose daily allowance is gone, for the life of this process.
#:
#: Without it every sentence that is not cached spends a doomed round trip
#: at the moment somebody is standing at the camera — five of them appeared
#: in the owner's very first log. The daily quota is the one failure that is
#: knowably permanent for the rest of the run, which is what makes a latch
#: honest here and dishonest for anything else. Restarting the station
#: clears it, and so does tomorrow. Same shape as `_embedding_failed`, down
#: to needing a reset between tests.
_spent: set[str] = set()

TAKEN = "เก็บแล้ว"
SAVED = "บันทึกเรียบร้อย คนต่อไปได้เลย"


def opening_lines() -> list[str]:
    """Said once and then never again.

    Everything the station can say: the five instructions, the corrections,
    and the two acknowledgements. All of it, because a sentence that has not
    been rendered arrives as silence at the moment somebody needed telling —
    and the free tier allows three renders a minute, so the moment somebody
    needs telling is the worst possible time to ask for one.
    """
    from app import enrollment

    return ([s["prompt"] for s in enrollment.STEPS]
            + list(enrollment.SPOKEN.values()) + [TAKEN, SAVED])


def _key(text: str, model: str) -> str:
    """Cache name. The model and the voice are part of it on purpose.

    Two models do not sound like each other, and neither do two voices. The
    project already learned this with embeddings: a cache keyed on the text
    alone will happily serve yesterday's speaker in today's voice, and the
    only symptom is that something sounds slightly wrong to somebody who
    cannot say why.
    """
    stamp = f"{model}|{settings.gemini_voice}|{text}"
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16] + ".wav"


def _order() -> list[str]:
    """Models to try, the one already in the cache first.

    Whatever rendered the clips that exist is the voice this station has
    been speaking with, so it is the voice the rest of the sentences have to
    be in. Without this, a day when the newest model is withdrawn and comes
    back leaves a directory in two voices — and the only symptom is that
    something sounds slightly wrong to somebody who cannot say why.
    """
    for model in MODELS:
        if CACHE.is_dir() and any((CACHE / _key(line, model)).exists()
                                  for line in opening_lines()):
            return [model] + [m for m in MODELS if m != model]
    return list(MODELS)


def _tags() -> tuple[str, ...]:
    """Cache keys the active provider may have written, preferred first."""
    if settings.tts_provider == "local":
        return (LOCAL_TAG,)
    return MODELS


def cached(text: str) -> Path | None:
    """The clip if it is already on disk, without asking anybody."""
    for model in _tags():
        path = CACHE / _key(text, model)
        if path.exists():
            return path
    return None


def _write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside and rename, so a render interrupted halfway cannot leave a
    # truncated file that every later lookup treats as a cache hit.
    temp = path.with_suffix(".part")
    with wave.open(str(temp), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    temp.replace(path)


def _render_local(text: str) -> tuple[bytes, int]:
    """One sentence through the on-disk VITS model. Raises; callers decide."""
    global _local
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer, VitsModel

    if _local is None:
        # local_files_only: this renderer exists to work when the network
        # does not, so it must never be the thing that goes looking for one.
        path = snapshot_download(LOCAL_MODEL, local_files_only=True)
        _local = (AutoTokenizer.from_pretrained(path),
                  VitsModel.from_pretrained(path))
    tok, model = _local
    inputs = tok(text, return_tensors="pt")
    with torch.no_grad():
        wav = model(**inputs).waveform[0].numpy()
    pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return pcm, int(model.config.sampling_rate)


def _render(text: str, model: str) -> tuple[bytes, int]:
    """PCM for one sentence. Raises; the caller decides what that means."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    reply = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=settings.gemini_voice))),
        ),
    )
    blob = reply.candidates[0].content.parts[0].inline_data
    found = re.search(r"rate=(\d+)", blob.mime_type or "")
    return blob.data, int(found.group(1)) if found else 24000


def clip(text: str, attempts: int = 1) -> Path | None:
    """The audio for this sentence, rendering it if this is the first time.

    None means "say it with a beep instead" — no key, no network, a model
    that has been withdrawn. None of those are reasons to break enrolment.

    `attempts` is how many times a rate limit may be waited out, and it is 1
    everywhere except the warm-up. Exactly the split `build_embeddings.py`
    settled on: waiting is right while nobody is standing there and wrong
    inside a request, where the wait *is* the failure.
    """
    text = (text or "").strip()
    if not text:
        return None
    hit = cached(text)
    if hit:
        return hit

    if settings.tts_provider == "local":
        with _lock:
            hit = cached(text)
            if hit:
                return hit
            try:
                pcm, rate = _render_local(text)
            except Exception as exc:
                logger.warning("tts: local render failed: %s", exc)
                return None
            path = CACHE / _key(text, LOCAL_TAG)
            _write_wav(path, pcm, rate)
            return path

    if not settings.gemini_api_key:
        return None

    with _lock:
        hit = cached(text)          # somebody may have rendered it while we waited
        if hit:
            return hit
        for model in _order():
            if model in _spent:
                # Not "try the next one": the models are in voice order, so
                # anything after this is a different voice.
                return None
            for attempt in range(max(1, attempts)):
                try:
                    pcm, rate = _render(text, model)
                except Exception as exc:
                    message = str(exc).lower()
                    if any(term in message for term in _QUOTA_DAY):
                        logger.warning(
                            "tts: today's free renders for %s are used up", model)
                        _spent.add(model)
                        # And *not* on to the next model. A quota is a
                        # tomorrow problem; asking a different model would
                        # buy today's remaining lines in a second voice, and
                        # a flow that changes voice halfway through is worse
                        # than one that is missing its last two sentences.
                        # Withdrawal is the only reason to change model.
                        return None
                    if any(term in message for term in _RATE_LIMIT):
                        if attempt + 1 >= max(1, attempts):
                            logger.warning("tts: rate limited on %s", model)
                            return None
                        time.sleep(min(60.0, 5.0 * 2 ** attempt))
                        continue
                    if any(term in message for term in _MISSING_MODEL):
                        logger.warning("tts: %s unavailable (%s)", model, exc)
                        break
                    logger.warning("tts: %s failed: %s", model, exc)
                    return None
                path = CACHE / _key(text, model)
                _write_wav(path, pcm, rate)
                return path
    return None


def warm() -> None:
    """Render the fixed instructions ahead of anybody needing them.

    Called from the station's startup on a background thread, never from a
    request. This project has the scar in the other direction: warming the
    Canva deck was wired into the path a waiting conversation ran through,
    and the mechanism written to stop the robot talking over a blank screen
    timed out every single time it was switched on.
    """
    lines = opening_lines()
    for line in lines:
        try:
            clip(line, attempts=6)
        except Exception as exc:                    # pragma: no cover - belt
            logger.warning("tts warm failed: %s", exc)
            return
    logger.warning("voice: %s", state(lines))


def state(lines: list[str] | None = None) -> str:
    """One line saying what the station can and cannot say, and why.

    The free tier renders ten sentences a day per model, and there are more
    sentences than that, so a first day ends with some lines spoken and some
    silent. Without this the operator meets that as "Emma says three things
    and then stops" — the same undiagnosable shape as a microphone nobody was
    holding. It also names a cache split across two models, which is two
    voices in one flow and wants the directory deleted and warmed again.
    """
    lines = opening_lines() if lines is None else lines
    have = [line for line in lines if cached(line)]
    voices = {model for line in lines for model in MODELS + (LOCAL_TAG,)
              if (CACHE / _key(line, model)).exists()}
    note = f"{len(have)}/{len(lines)} clips ready"
    if len(have) < len(lines):
        note += (" — the rest render themselves next time"
                 + ("" if settings.tts_provider == "local"
                    else " (free tier: 10/day)"))
    if len(voices) > 1:
        note += (f" — WARNING: {len(voices)} different models in the cache, "
                 f"delete {CACHE} and start once more to get one voice")
    return note
