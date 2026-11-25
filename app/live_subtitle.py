"""Thai subtitle of Emma's LIVE answers on the ceremony hall screen (CEREMONY_LIVE_SUBTITLE_TH).

Owner 24 Nov: "ถ้าเอาซับไทยที่หุ่นตอบพอ ไม่มีซับคนพูด จะไวไหม" -- only HER words, in Thai.
In live first the cues' own subtitles (event_cues -> face_mirror.subtitle) do not exist, and her
transcript on the hall's face page is hidden in show mode, so the hall showed no words at all.

What this does, per answer ("turn"):
  * her output transcript (session `assistant_transcript`, never the host's `user_transcript`)
    is cut into sentences (`split_pieces`);
  * each sentence is translated to Thai by a fast Gemini TEXT model through the project's
    google-genai client, as its own asyncio task -- never awaited by the audio path, 2.5 s at most;
    a failure or timeout = no subtitle for that sentence (her audio is never touched);
  * Thai goes to the hall in order (`face_mirror.live_subtitle`); where her voice has got is the
    page's own audio clock: a `mirror_live_mark` is sent in step with her audio frames at the start
    of each sentence and the page keeps its clock there (voice-preview.html mirrorLiveMark).
  * turn end = the subtitle ends after the last sentence is out; interrupted / stop = down now,
    translations still running are dropped.

Never English, never the host: a result without a Thai letter is dropped. Cheap when off: `feed`
returns on the first test. Nothing here raises into the call.
"""
from __future__ import annotations

import asyncio
import logging
import re

from app import face_mirror
from app.config import settings

logger = logging.getLogger(__name__)

#: A clause longer than this (characters, still without a full stop) is cut at its last comma.
LONG_CLAUSE = 110
#: ...but never leaving less than this on the first piece.
MIN_PIECE = 30
#: A piece shorter than this waits for the next one (`split_pieces`).
MERGE_BELOW = 24
#: English speech in characters a second (150 wpm of ~5.5): the page's guess before it knows better.
CHARS_PER_S = 14.0

_THAI = re.compile(r"[฀-๿]")
_BOUNDARY = re.compile(r"([.!?]+)(?=\s)")                       # a stop FOLLOWED by a space: "3.5" and "U.S.A" stay whole
_CLAUSE = re.compile(r"[,;:](?=\s)")

INSTRUCTION = (
    "Translate the English sentence into natural spoken Thai, as a subtitle for a real-estate "
    "presentation. Keep every number, unit and name exactly as written (Embassy World, Emma, "
    "place names). Add nothing, explain nothing, leave nothing out. Output only the Thai "
    "sentence, one line, no quotes. The speaker is a woman: polite endings are ค่ะ / คะ, never ครับ."
)


def split_pieces(buf: str, *, final: bool = False) -> tuple[list[str], str]:
    """(complete pieces, the rest). A piece ends at . ! ? followed by a space; a rest longer than
    LONG_CLAUSE is cut at its last ", " / "; " / ": ". `final` = the turn is over: the rest is a
    piece too. Pieces are stripped, empty ones dropped."""
    pieces: list[str] = []
    pos = 0
    for m in _BOUNDARY.finditer(buf):
        piece = buf[pos:m.end()].strip()
        if piece:
            pieces.append(piece)
        pos = m.end()
    rest = buf[pos:]
    while len(rest) > LONG_CLAUSE:
        cut = None
        for m in _CLAUSE.finditer(rest[:LONG_CLAUSE]):
            if m.end() >= MIN_PIECE:
                cut = m.end()
        if cut is None:
            break
        pieces.append(rest[:cut].strip())
        rest = rest[cut:]
    if final and rest.strip():
        pieces.append(rest.strip())
        rest = ""
    elif final:
        rest = ""
    # a piece too short to stand alone goes on with the next ("Yes." "Thank you."): one request, one band, and the
    # free tier's 15 requests a minute are not spent on two words (measured 24 Nov: 429 after 15 sentences)
    merged: list[str] = []
    acc = ""
    for piece in pieces:
        acc = (acc + " " + piece).strip()
        if len(acc) >= MERGE_BELOW:
            merged.append(acc)
            acc = ""
    if acc:
        if final:
            merged.append(acc)
        else:
            rest = (acc + " " + rest.lstrip()) if rest.strip() else acc
    return merged, rest


# ---- the translation ----------------------------------------------------------------------

_client = None


def _the_client():
    global _client
    if _client is None:
        from google import genai

        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def translate(text: str) -> str | None:
    """English sentence -> Thai, or None (no key, a failure, the timeout, an answer with no Thai in it).
    Runs as its own task: the audio path never waits on it."""
    if not settings.gemini_api_key or not text.strip():
        return None
    try:
        from google.genai import types

        async def _call():
            reply = await _the_client().aio.models.generate_content(
                model=settings.ceremony_live_subtitle_model,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=INSTRUCTION, temperature=0.0, max_output_tokens=400),
            )
            return reply.text

        out = await asyncio.wait_for(_call(), timeout=settings.ceremony_live_subtitle_timeout_s)
    except Exception as exc:                       # noqa: BLE001 -- no subtitle for this sentence, nothing else
        logger.info("live subtitle: translation skipped (%s)", type(exc).__name__)
        return None
    out = " ".join(str(out or "").replace("“", "").replace("”", "").replace('"', "").split())
    if not out or not _THAI.search(out):
        return None
    return out[:400]


# ---- the turn --------------------------------------------------------------------------------

class _Turn:
    def __init__(self, number: int) -> None:
        self.number = number
        self.buf = ""
        self.sent = 0                              # pieces cut so far (= marks sent after the first)
        self.marked = False
        self.ended = False
        self.dead = False
        self.last: asyncio.Task | None = None      # the newest delivery: the next waits for it (order)


_turn: _Turn | None = None
_counter = 0
_tasks: set = set()


def active() -> bool:
    """Switch on, live first, and a hall screen to show it on (no screen = nothing to translate for)."""
    try:
        if not settings.ceremony_live_subtitle_th:
            return False
        from app import ceremony

        return bool(ceremony.live_first() and face_mirror.live_subtitle_ok())
    except Exception:                              # noqa: BLE001
        return False


def _spawn(coro) -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def _deliver(turn: _Turn, n: int, text: str, prior: asyncio.Task | None) -> None:
    thai = await translate(text)                   # (starts at once: sentences translate side by side)
    if prior is not None:
        try:
            await prior                            # ...and go out in the order she said them
        except BaseException:                      # noqa: BLE001 -- the one before failing is not this one's
            pass
    if turn.dead or not thai:
        return
    face_mirror.live_subtitle(turn.number, n, thai, len(text) / CHARS_PER_S)


async def _finish(turn: _Turn, prior: asyncio.Task | None) -> None:
    if prior is not None:
        try:
            await prior
        except BaseException:                      # noqa: BLE001
            pass
    if not turn.dead:
        face_mirror.live_subtitle_end(turn.number)


def _emit(turn: _Turn, pieces: list[str]) -> None:
    for piece in pieces:
        n = turn.sent
        turn.sent += 1
        face_mirror.live_mark(turn.number, turn.sent)         # the end of this piece = the start of the next
        turn.last = _spawn(_deliver(turn, n, piece, turn.last))


def feed(text: str) -> None:
    """A chunk of her answer's words (session `assistant_transcript`). Never raises."""
    global _turn, _counter
    try:
        if not text or not active():
            return
        if _turn is None or _turn.ended or _turn.dead:
            _counter += 1
            _turn = _Turn(_counter)
        turn = _turn
        if not turn.marked and text.strip():
            turn.marked = True
            face_mirror.live_mark(turn.number, 0)             # where her first sentence begins
        turn.buf += text
        pieces, turn.buf = split_pieces(turn.buf)
        _emit(turn, pieces)
    except Exception:                              # noqa: BLE001 -- her words go on to the screen and the log
        logger.exception("live subtitle: feed")


def end_turn() -> None:
    """Her answer is complete (turn_complete): the last sentence goes out, then the subtitle ends."""
    global _turn
    try:
        turn = _turn
        if turn is None or turn.ended or turn.dead:
            return
        turn.ended = True
        pieces, turn.buf = split_pieces(turn.buf, final=True)
        _emit(turn, pieces)
        _spawn(_finish(turn, turn.last))
    except Exception:                              # noqa: BLE001
        logger.exception("live subtitle: end")


def cut() -> None:
    """Interrupted / stopped: down now, translations still out are dropped."""
    global _turn
    try:
        turn, _turn = _turn, None
        if turn is None or turn.dead:
            return
        turn.dead = True
        face_mirror.live_subtitle_end(turn.number, cut=True)
    except Exception:                              # noqa: BLE001
        logger.exception("live subtitle: cut")
