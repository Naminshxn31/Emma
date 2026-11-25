"""The whole ceremony live: the server follows the host (owner 24 Nov 2026, the day before the event:
"เราจะทำเป็นการ์ดสอง หลังจากจับมือกับแขก แล้วหุ่นก็จะเดินกลับมา ... แล้วจากนั้นหุ่นจะคุยกับคุณมิกิ ช่วงนั้นเราอาจจะเปิดสด
เหมือนที่เราทำ แต่ให้ฟังเสียงคุณมิกิเท่านั้น" -- asked which form: "ทั้งพิธีคุยสด ไม่ใช้บทกด").

Measured on 7 Nov: Gemini speaking the script itself was ~76% faithful and choosing the cue itself ~68%. So the model
does not run the ceremony. Card 2 (the order's `follow` step, app/event_order.py) turns this mode on, and from then on:

* **The host's scripted line plays the next cue -- decided by the server, never the model.** The host's words (the input
  transcription: heard.latest_caption, the same feed as "ตอบสด"'s topic slides, app/ceremony_motion.py) are matched
  against the host line that comes BEFORE each of the next few unplayed cues of the running order
  (data/ceremony/floor1_ceremony.host.json, copied from the Actor Script's host column). A match presses that cue
  exactly as a staff press would: `ceremony.script_press` + `event_order.press` -- the approved recording, the
  subtitles, the slides, the chain jokes, every gate the press has.
* **Words, not substrings** (CLAUDE.md): Thai cut by event_script's standard dictionary (not retrieval.tokenize),
  tone marks / thanthakhat dropped on both sides (the ASR spells เอ็มม่า four ways), a token matches a token (or two
  neighbours joined, or a one-letter-off spelling of a longer word) -- never a piece of a word.
* **A weak or tied match = no cue** (she answers live instead): the cover of the line's weighted words must reach
  MIN_COVER, the line's ending must have been said (or nearly all of it), the host must not have said much more than
  the line, and the best cue must beat the next by MARGIN. Never backwards: only cues after the last one taken.
* **Gates stay**: a cue behind an unticked manual step, a venue device, a walk, a film or the handoff cannot fire
  (the scan stops at the first step not taken); the press itself refuses while she speaks, the robot moves, the
  opening runs. Voice never starts a walk. A cue with no host line (`manual`) blocks everything behind it: staff press
  it (the pointer follows a press). A cue that continues the one before it by script ("ต่อจากบทก่อน") is played by
  the server a moment after the last line ended (`continues`). Chained jokes are the order's own.
* **Anything else the host says, she answers live**: the "ตอบสด" window (app/ceremony.py `adhoc_open(follow=True)`):
  named-only (NoiseReplyGate: the host is the MC and talks to the guests -- she answers only when he says "เอ็มม่า",
  and the matching does NOT need her name), stories and topic slides as there, no 2-minute cap. The window shuts at
  every press (the existing rule) and this module re-opens it as soon as nothing is being said or moved.
* **Staff keep every control**: a press of any step moves the pointer (it is read from the order's marks); the red stop
  PAUSES follow until staff press "กลับมาโหมดสด"; "หยุดโหมดสด / กลับไปกดเอง" ends it; "กลับเข้าบท" of "ตอบสด" pauses it.

What "only Miki" means: the robot's ears are the host's clip-on wireless mic (the GCK receiver) plus the loudness /
focus gates that were already there (focus rule 2: the mic must reach rms 0.06 to open a turn) -- no voice-print
(ONE_VOICE is not recommended for 25 Nov, .tmp/miki-mic/README_TH.md). It is NOT a guarantee: a loud person near the
robot's own mic is heard and transcribed too. What protects the ceremony is the matcher (a stranger's words do not
cover a host line, so no cue fires) and the named-only rule (she answers only when called). docs/emma-ceremony.md.

No words of anybody's are logged or put on the staff page: the page shows the EXPECTED line from the script data, and
the turn log rows (`ceremony_follow`) carry the cue id, covers and counts only.

Nothing here has run on the robot yet.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import math
import re
import time
import unicodedata
from pathlib import Path

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.ceremony_follow")

#: The host stopped talking for this long (his caption stopped growing): decide. A pause inside a line shorter than
#: this does not cut it; Gemini starts to answer ~1 s after his turn ends, and the press closes her live window.
QUIET_S = 0.9
#: The weighted share of a line's words that must be heard, how many of them, and how far the best cue must lead.
MIN_COVER = 0.6
#: The very next cue (only), said loosely and finished: enough at this cover (see decide_text, rehearsal 24 Nov).
NEXT_COVER = 0.33
MIN_MATCHED = 3
MARGIN = 0.15
#: A much shorter version of a line (owner 24 Nov): its key words -- those no other host line has. At least KEY_MIN of
#: them and KEY_COVER of them, KEY_PRECISION of what he said between the first and the last of them.
KEY_MIN = 3
KEY_COVER = 0.6
KEY_PRECISION = 0.5
#: ...in at least this many separate places of the line (a compound word is cut into several words, all next to each
#: other: "สิ่งอำนวยความสะดวก" is one place, and an ad-lib about it is not his line about it).
KEY_GROUPS = 2
#: A line this short is a command or a reaction ("เปิดไฟล็อบบี้อีกครั้ง"): too generic for less than nearly all of it.
STRICT_TOKENS = 6
STRICT_COVER = 0.85
#: He stopped before the end of the line: wait this much longer before deciding that he is done (then the ending is not asked).
TAIL_GRACE_S = 1.3
#: He spoke and nothing matched: how long the page keeps the next step standing out.
NUDGE_S = 30.0
#: A line this short on content words needs fewer matched words (never fewer than 2).
SHORT_LINE = 4
#: The line's last two words: one of them must be heard, unless this much of the line was.
TAIL_FREE_COVER = 0.85
#: The matched words must be this share of what he said (he said much more than the line = he said something else).
MIN_PRECISION = 0.4
#: ... a question alone (ask_en) is a few words: they must come nearly together -- "how many bedrooms does the biggest unit
#: have" is not "how many units" (the eval's off-script line, 24 Nov: 3 of 5 words of the span)
ASK_PRECISION = 0.75
#: How far ahead of the pointer a match is looked for (never backwards).
LOOKAHEAD = 3
#: ... in script mode (read_past): 3 questions missed in a row stalled the ceremony for good (scenario review 24 Nov:
#: s02_l09, s03_l03, s04_l03 missed = 1/18 fired); at 5, up to 4 misses go on (15/18) with no new false match
SCRIPT_LOOKAHEAD = 5
#: Utterances kept for a match across a pause in a line, and how old the oldest may be.
BUFFER_UTTERANCES = 3
BUFFER_S = 25.0
#: A decided match waits this long for her voice / the robot / the press gate (a line of hers still ending), polling.
HOLD_S = 8.0
#: ... in script mode (CEREMONY_SCRIPT_SHOW_ONLY): her own held reply to the matched words streams in real time (3.8 sends
#: audio at about speaking pace -- s02_l05: 18.8 s of turn for 18.79 s of voice) and the read may not land on it;
#: rehearsal 24 Nov 15:21:58 a reply longer than 8 s had the press given up ("held") = no line, no slide.
SCRIPT_HOLD_S = 20.0
POLL_S = 0.4
#: The keeper: how often it looks, and how long after her last line a `continues` cue goes.
KEEP_S = 0.4
CONTINUE_DELAY_S = 1.2
#: ...only when the host has not said anything for this long (he may be about to speak over it).
CONTINUE_HOST_QUIET_S = 2.0
#: ...and only within this long of the cue before it having been taken (a skipped one is no reason to go on).
CONTINUE_WITHIN_S = 90.0

#: Why the window closed, for the keeper: staff took it down (pause), or the order was reset (follow ends).
PAUSE_CLOSES = ("staff", "staff_stopped", "stop", "cut")
END_CLOSES = ("reset",)

PAUSED_TH = {"stop": "กดหยุดแดง — พักโหมดสด (กด 'กลับมาโหมดสด' เมื่อพร้อม)",
             "staff": "ทีมงานกด 'กลับเข้าบท' — พักโหมดสด (กด 'กลับมาโหมดสด' เมื่อพร้อม)"}
GATE_TH = {"busy": "กำลังทำขั้นก่อนหน้าอยู่", "chain": "มุกกำลังพูดต่อเนื่อง", "opening": "การ์ดเปิดตัวกำลังทำอยู่",
           "moving": "หุ่นกำลังเดินหรือทำท่า", "speaking": "Emma กำลังพูดอยู่", "answering": "Emma กำลังตอบสดอยู่", "unknown": "ตรวจสถานะหุ่นไม่ได้"}
NO_HOST_TH = "ไม่มีบทคุณมิกิให้จับ — กดบทนี้เองเมื่อถึงจังหวะ"
STATE: dict = {"on": False, "paused": None, "since": None, "session": None, "utt": {}, "heard_mono": None,
               "spent": 0.0, "timer": None, "task": None, "keeper": None, "quiet_since": None,
               "cont_failed": None, "opened_at": None, "reopens": 0, "last_fire": None, "last_eval": None,
               "decision": 0, "closed_seen": 0.0, "auto_skipped": set(),
               # her name first: when each of his utterances was heard and whether it calls her
               "hist": {}}
#: 24 Nov 11:00 (owner: "ตอบได้แม้จะไม่ตรงกับบทมาก"): `pending` = the host's last words are not decided yet (her live reply
#: to them waits: `reply_hold`), `dropped` = they matched a cue (her live reply to them is dropped, the recording plays
#: alone), `lost_at` = since when the ceremony call has been gone (a re-dial is not the end), `redials` / `redialed_at`.
_EXTRA = {"pending": False, "dropped": False, "lost_at": None, "redials": 0, "redialed_at": None,
          # a matched cue waiting at the gates for its press (monotonic start) -- her reply to those words stays held
          "press_wait": None,
          # script-first slide picker (app/ceremony_pick.py, owner 24 Nov ~12:00): the context of the last host
          # utterance that was not a scripted line (his words' tokens stay as text in memory, never logged) and the
          # words of her answer to it so far
          "pick": None, "ans": "", "ans_n": 0,
          # the nudge (owner 24 Nov: he may end his lead-in without a question): when her first words of the answer
          # came (None = she has not started), and the (cue, utterance) pairs already nudged
          "ans_started": None, "nudged": None,
          # the jokes that play by themselves (CEREMONY_JOKES_AUTO): the one armed now, the set-ups already pressed by
          # follow, and when her last live turn ended
          "joke": None, "joke_done": None, "answer_done_at": None,
          # free talk (CEREMONY_FREE_TALK): the newest host utterance (`ex_stamp`), the motion the hall has for it
          # (`ex` = {"stamp", "motion": cue|story|topic}), whether the hall shows something of ours (`hall`) and since
          # when her answer has been over (`idle_since`, for the return to the logo)
          "ex_stamp": None, "ex": None, "hall": None, "idle_since": None, "hall_obj": None, "topic_cue": None,
          # her name first: she may answer his last words live / the utterance whose call is used up / since when the
          # decision waits for his voice to stop
          "may_answer": False, "may_at": 0.0, "call_spent": 0.0, "talk_since": None}
STATE.update(_EXTRA)
#: The ceremony app gone this long (not a re-dial of the network) ends follow.
APP_GONE_S = 30.0
#: A decision pending longer than this since the host's last caption is not waited for (her reply is released).
REPLY_HOLD_MAX_S = 6.0
_stock: dict = {}
_tasks: set = set()


def reset() -> None:
    timer = STATE.get("timer")
    keeper = STATE.get("keeper")
    for handle in (timer,):
        if handle is not None:
            try:
                handle.cancel()
            except Exception:                            # noqa: BLE001
                pass
    if keeper is not None:
        try:
            keeper.cancel()
        except Exception:                                # noqa: BLE001
            pass
    STATE.update(on=False, paused=None, since=None, session=None, utt={}, heard_mono=None, spent=0.0, timer=None,
                 task=None, keeper=None, quiet_since=None, cont_failed=None, opened_at=None, reopens=0,
                 last_fire=None, last_eval=None, decision=0, closed_seen=0.0, auto_skipped=set(), hist={})
    STATE.update(_EXTRA)
    _stock.pop("lines", None)
    _stock.pop("compiled", None)                         # (the words of the lines follow the file: never a stale set)
    _stock.pop("distinct", None)


def _spawn(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return None
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# ---- the host's lines ---------------------------------------------------------------------

def host_path() -> Path | None:
    """The host-lines file beside CEREMONY_FILE (floor1_ceremony.json -> floor1_ceremony.host.json)."""
    from app import event_script

    script = event_script.path()
    return None if script is None else script.with_suffix(".host.json")


def lines() -> dict:
    """cue id -> {"kind": host|continues|chain_follow|manual, "host_th"?, "host_en"?, "note_th"?}; {} = no file / unreadable.
    `host_en` (25 Nov: Khun Miki speaks English) is a TRANSLATION of the Thai line until his own English lines replace
    it -- a data-only change; the Thai line is matched too.
    Cached on the file's stat (a rehearsal edit is picked up)."""
    target = host_path()
    if target is None:
        return {}
    try:
        stat = target.stat()
    except OSError:
        return {}
    key = (str(target), stat.st_mtime_ns, stat.st_size)
    cached = _stock.get("lines")
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        rows = raw.get("lines") if isinstance(raw, dict) else None
        out = {}
        for cue_id, row in (rows or {}).items():
            if not isinstance(row, dict) or row.get("kind") not in ("host", "continues", "chain_follow", "manual"):
                continue
            if row["kind"] == "host" and not (isinstance(row.get("host_th"), str) and row["host_th"].strip()):
                continue                                 # a host line with no words is a manual cue
            asks = row.get("ask_en") if isinstance(row.get("ask_en"), list) else []
            says = row.get("ask_say") if isinstance(row.get("ask_say"), list) else []
            out[str(cue_id)] = {"kind": row["kind"], "host_th": " ".join(str(row.get("host_th") or "").split()) or None,
                                "host_en": " ".join(str(row.get("host_en") or "").split()) or None,
                                "ask_en": [" ".join(str(a).split()) for a in asks if isinstance(a, str) and a.strip()],
                                # (one of these must be in his words for the questions alone to count: the stop words
                                # tokens() drops -- "how can YOU help" is question 1, "how can I help you" is not)
                                "ask_say": [" ".join(str(a).lower().split()) for a in says if isinstance(a, str) and a.strip()],
                                "ask_word": [w for w in (row.get("ask_word") or []) if isinstance(w, str) and w.strip()],
                                # (what he means when his words are not the script's: patterns, app/ceremony_call.by_meaning)
                                "means": [m for m in (row.get("means") or []) if isinstance(m, str) and m.strip()],
                                "not": [m for m in (row.get("means_not") or []) if isinstance(m, str) and m.strip()],
                                "not_all": [m for m in (row.get("means_not_all") or []) if isinstance(m, str) and m.strip()],
                                "means_next": [m for m in (row.get("means_next") or []) if isinstance(m, str) and m.strip()],
                                "alike": row.get("alike") if isinstance(row.get("alike"), str) else None,
                                "tell": row.get("tell") is True,
                                "note_th": " ".join(str(row.get("note_th") or "").split()) or None}
    except (OSError, ValueError):
        logger.exception("ceremony follow: the host lines file")
        out = {}
    _stock["lines"] = (key, out)
    _stock.pop("compiled", None)
    return out


def coverage() -> dict:
    """Which cues of the running order have a host line, for the docs and the staff page's note."""
    from app import event_script

    rows = lines()
    ids = [st["cue"] for sc in event_script.order() or [] for st in sc["steps"] if st["do"] == "cue" and st.get("cue")]
    kinds: dict[str, list[str]] = {"host": [], "continues": [], "chain_follow": [], "manual": []}
    for cue_id in ids:
        kinds[(rows.get(cue_id) or {"kind": "manual"})["kind"]].append(cue_id)
    return {"cues": len(ids), **{k: v for k, v in kinds.items()}}


# ---- words --------------------------------------------------------------------------------

_MARKS = dict.fromkeys(map(ord, "็่้๊๋์ๆ"))        # mai taikhu, the tones, thanthakhat, mai yamok
_THAI = re.compile(r"[฀-๿]")
_ALNUM = re.compile(r"[0-9a-z]")
_STOP_RAW = ("ครับ ค่ะ คะ นะ ที่ ซึ่ง และ หรือ ก็ ว่า ให้ จะ นี้ นั้น อัน ของ เป็น แล้ว นะครับ ครับผม "
             "a an the and or of to in is are it that this for on at with we i you "
             # English function words (the host speaks English on 25 Nov): none of them is a word of a line
             "our us my me your be been am was were do does did have has had can could would will shall just so but "
             "if as by from there here its let please now then very really also about up out all some any one they "
             "them their he she his her yes yeah okay ok oh well")
#: Her name, however the transcriber spells it: it is not a word of the line (the matching never needs it).
_NAME_RAW = "เอ็มม่า เอมม่า เอ็มมา เอมมา เอ็มม้า เอมา เอ็มม่ะ อีมม่า เอ็มม่าา emma ema amma emmah"


#: Her name cut out of the raw text BEFORE the Thai dictionary sees it: glued to the word before it ("ที่ไหนครับเอ็มม่า")
#: it changes how that word is cut, and the line and what was heard would no longer be cut alike.
_NAME_RE = re.compile("(?:" + "|".join(sorted(
    (re.escape(n) for n in ("เอ็มม่า", "เอมม่า", "เอ็มมา", "เอมมา", "เอ็มม้า", "เอมม้า", "เอ็มหม่า", "เอมา", "เอ็มม่ะ",
                            "อีมม่า", "อิมม่า", "emma", "amma")), key=len, reverse=True)) + ")", re.I)


def _stem(token: str) -> str:
    """A light English stem so "units / unit", "located / location", "facilities / facility" are one word (both
    sides -- the line and what was heard -- go through it). Thai and mixed tokens are never touched."""
    if len(token) < 4 or not (token.isascii() and token.isalpha()):
        return token
    if token.endswith("ies") and len(token) > 4:
        token = token[:-3] + "y"
    elif token.endswith("s") and not token.endswith(("ss", "us")):
        token = token[:-1]
    if len(token) >= 7 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) >= 6 and token.endswith("ed"):
        token = token[:-2]
    elif len(token) >= 7 and token.endswith("ion"):
        token = token[:-3]
    if len(token) >= 5 and token.endswith("e"):
        token = token[:-1]
    return token


def _norm(token: str) -> str:
    token = unicodedata.normalize("NFKC", token).lower().translate(_MARKS)
    return _stem(token.strip(" \t\r\n.,!?;:\"'()[]{}…-–—_/\\|*+=<>~`^%$#@&"))


def _plain(words: str) -> frozenset:
    return frozenset(_norm(w) for w in words.split())


_STOP = _plain(_STOP_RAW)
_NAMES = _plain(_NAME_RAW)


def tokens(text: str | None) -> list[str]:
    """The content words of `text`, in order: Thai cut by event_script's standard dictionary, English as words;
    tone marks gone; her name, the polite endings and the small function words dropped."""
    from app import event_script

    out = []
    for piece in event_script._segment(_NAME_RE.sub(" ", unicodedata.normalize("NFKC", text or ""))):
        token = _norm(piece)
        if not token or token in _STOP or token in _NAMES:
            continue
        if not (_THAI.search(token) or _ALNUM.search(token)):
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        out.append(token)
    return out


def warm() -> None:
    """The Thai dictionary built (a second of CPU): in a thread when card 2 is pressed, never on the voice loop."""
    tokens("อุ่นเครื่อง Embassy World")
    _compiled()
    try:
        from app import ceremony_pick

        ceremony_pick.warm()                             # the picker's word tables (cached on the files' stat)
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: warming the picker")


EN = "|en"                                               # the key of a cue's English variant in `_compiled`
ASK = "|q"                                               # ... and of its question-only variants (cue + ASK + n + EN)


def _compiled() -> dict:
    """host line -> its tokens with weights; a word said in many lines counts less (1/sqrt(df)). The Thai line is
    keyed by the cue id, the English one (`host_en`) by cue id + EN, each weighed among the lines of its own language."""
    cached = _stock.get("compiled")
    if cached is not None:
        return cached
    out: dict = {}
    distinct: dict = {}
    for field, suffix in (("host_th", ""), ("host_en", EN)):
        rows = {cue + suffix: tokens(row[field]) for cue, row in lines().items()
                if row["kind"] == "host" and row.get(field)}
        df: dict[str, int] = {}
        for toks in rows.values():
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        out.update({key: [(t, 1.0 / (df[t] ** 0.5)) for t in toks] for key, toks in rows.items()})
        distinct.update({key: {t for t in set(toks) if df[t] == 1} for key, toks in rows.items()})
    # His QUESTION alone (owner 24 Nov ~13:45: "ปรับให้สคริป์ของเราเหลือคำถาม ถ้าหุ่นได้ยินประโยคที่คล้ายคำถามก็เปลี่ยนภาพจอแล้วพูด
    # ตามบท"): `ask_en` = the short questions of a host line ("where is our project located"), each a variant of its own,
    # keyed cue + ASK + n + EN -- English, so a short one must be said whole (decide_text's strict rule); weighed among the
    # questions. A question of under two content words is never a variant (one word is any question).
    rows = {}
    for cue, row in lines().items():
        if row["kind"] != "host":
            continue
        for n, ask in enumerate(row.get("ask_en") or ()):
            toks = tokens(ask)
            if len(toks) >= 2:
                rows[cue + ASK + str(n) + EN] = toks
    df = {}
    for toks in rows.values():
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    out.update({key: [(t, 1.0 / (df[t] ** 0.5)) for t in toks] for key, toks in rows.items()})
    distinct.update({key: {t for t in set(toks) if df[t] == 1} for key, toks in rows.items()})
    _stock["distinct"] = distinct
    _stock["compiled"] = out
    return out


def _find(word: str, heard: list[str], pos: dict):
    """Where in `heard` the word is: (first, last) index of the heard word, or the two neighbours joined that spell it,
    or one that is a letter off (long words only) -- None when it was not said."""
    if word in pos:
        return pos[word]
    if len(word) < 4:
        return None
    for k, h in enumerate(heard):
        if (len(h) >= 4 and abs(len(h) - len(word)) <= 2
                # ("your abilities" is not "facilities": the recogniser keeps a word's head)
                and not (settings.ceremony_name_first and h[0] != word[0] and h.isascii())
                and difflib.SequenceMatcher(None, word, h).ratio() >= 0.8):
            return (k, k)
    return None


def _distinct() -> dict:
    """host line -> the words only that line has (df 1): its key words. Cached with `_compiled`."""
    _compiled()
    return _stock["distinct"]


def _score(cue_id: str, heard: list[str], pos: dict) -> dict:
    """The cue's best variant (its Thai line or its English one): the higher cover, the Thai on a tie."""
    best = None
    # (CEREMONY_ENGLISH_ONLY, owner 24 Nov ~15:06 "ไม่ต้องฟังไทยด้วย": the Thai host lines are not matched -- Thai in the room
    # is the staff's)
    keys = (cue_id + EN,) if settings.ceremony_english_only and cue_id + EN in _compiled() else (cue_id, cue_id + EN)
    for key in keys:
        if key not in _compiled():
            continue
        one = _score_line(cue_id, key, heard, pos)
        if best is None or one["cover"] > best["cover"]:
            best = one
    return best or _score_line(cue_id, cue_id, heard, pos)


def _score_line(cue_id: str, line_key: str, heard: list[str], pos: dict) -> dict:
    toks = _compiled().get(line_key) or []
    key = _distinct().get(line_key) or set()
    total = sum(w for _t, w in toks) or 1.0
    got, at, hit = [], [], []
    for k, (t, w) in enumerate(toks):
        found = _find(t, heard, pos)
        if found is not None:
            got.append((t, w))
            at.extend(found)
            if t in key:
                hit.append(k)
    matched = {g for g, _w in got}
    missing = [t for t, _w in toks if t not in matched]
    span = (max(at) - min(at) + 1) if at else 0           # the heard words between his first and his last: banter outside is none of it
    return {"cue": cue_id, "lang": "en" if line_key.endswith(EN) else "th",
            "cover": sum(w for _t, w in got) / total, "matched": len(got), "tokens": len(toks),
            "tail": any(t in matched for t, _w in toks[-2:]), "span": span,
            "dmatched": len(hit), "dtotal": len([1 for t, _w in toks if t in key]),
            "dgroups": sum(1 for n, k in enumerate(hit) if n == 0 or k != hit[n - 1] + 1), "missing": missing}


def _score_ask(cue_id: str, heard: list[str], pos: dict) -> dict | None:
    """The cue's best question alone (ask_en): the higher cover; None = it has none."""
    best = None

    def tight(a: dict) -> float:
        return a["matched"] / a["span"] if a["span"] else 0.0

    for key in _compiled():
        if key.startswith(cue_id + ASK):
            one = _score_line(cue_id, key, heard, pos)
            # equal cover: the question said closest together (scenario review 24 Nov: "play the technology video"
            # inside "play the Embassy World technology video" kept the first ask, span 5 = 0.6 < ASK_PRECISION)
            if best is None or (one["cover"], tight(one)) > (best["cover"], tight(best)):
                best = one
    return best


#: Question words the transcriber drops at the start of his question (rehearsal 24 Nov 14:36: "What makes our project
#: different?" reached us as "Make project different."): one of them missing is still the question, if 3+ words remain.
_WH = frozenset(("what", "how", "wher", "where", "which", "why", "when"))


def _only_wh_missing(a: dict) -> bool:
    return bool(a.get("missing") and len(a["missing"]) == 1 and a["missing"][0] in _WH and a["matched"] >= 3)


def _positions(heard: list[str]) -> dict:
    pos: dict = {}
    for k, h in enumerate(heard):
        pos.setdefault(h, (k, k))
        if k + 1 < len(heard):
            pos.setdefault(h + heard[k + 1], (k, k + 1))
    return pos


_SENTENCE_END = re.compile(r"[.!?;]+")


def _last_sentence(text: str) -> str | None:
    """The last sentence of a transcript that has more than one (None when it is one sentence)."""
    parts = [p.strip() for p in _SENTENCE_END.split(text or "") if p.strip()]
    return parts[-1] if len(parts) > 1 else None


def _ask_said(cue_id: str, text: str, whole: str | None = None) -> bool:
    """`text`: the words his question is scored on; `whole`: all he said, when that is more than `text` (the last sentence
    alone, decide_text's second try) -- what rules a question out (`not_all`) is read in all of it. Scene check 25 Nov,
    a paraphrase from a set written blind to the patterns: "Okay, so pretend I'm a buyer with my wife and kids. How would
    you help me, Emma?" played question 1 -- the last sentence alone had no "buyer" left in it."""
    row = lines().get(cue_id) or {}
    need = row.get("ask_say") or []
    if name_first() and row.get("not_all"):
        from app import ceremony_call

        asked = ceremony_call._plain(ceremony_call.asked_part(whole if whole is not None else (text or "")))
        if any(re.search(p, asked) for p in row["not_all"]):
            return False                                 # ("pretend I am a customer ... how can you help" is question 8)
    low = " ".join(re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split())
    return not need or any(n in low for n in need)


#: A sentence this short (content words) with a cue's own word in it IS that cue's question, however the rest was heard
#: ("in the quotation", "is in the quotation", "Please pin the quotation", "Dashboard show." -- rehearsal 24 Nov). Only
#: for the next two questions: elsewhere a word is just a word.
ASK_WORD_MAX = 3


def _ask_word(heard: list[str], cue_ids: list[str], text: str = "") -> dict | None:
    from app import ceremony_call

    if not heard or len(heard) > ASK_WORD_MAX or ceremony_call.asks_how(text):
        return None                                      # ("how do I get a quotation" asks something else of it)
    hits = [c for c in cue_ids[:2] for w in ((lines().get(c) or {}).get("ask_word") or [])
            if set(tokens(w)) and set(tokens(w)) <= set(heard)]
    if len(set(hits)) != 1:
        return None
    return {"cue": hits[0], "cover": 1.0, "second": 0.0, "matched": 1, "tokens": 1, "span": 1}


def _ask_match(heard: list[str], pos: dict, cue_ids: list[str], text: str = "", whole: str | None = None) -> dict | None:
    """His QUESTION alone (owner 24 Nov ~13:45, "ถ้าหุ่นได้ยินประโยคที่คล้ายคำถามก็เปลี่ยนภาพจอแล้วพูดตามบท"): a second way in,
    asked only when the line itself did not match -- every word of one question said, close together (ASK_PRECISION),
    and no other cue's question nearly as well. The best score of that question, or None."""
    asked = sorted((a for a in (_score_ask(c, heard, pos) for c in cue_ids if _ask_said(c, text, whole)) if a is not None),
                   key=lambda a: (-a["cover"], -a["matched"]))
    if not asked:
        return None
    a = asked[0]
    # Two questions said whole: the one with more words is the more particular one (rehearsal 24 Nov 16:41: "looking for
    # a room for my family. How would you help?" -- question 8's three words and question 1's two "how ... help" tied, the
    # tie fell through to the last sentence alone, and question 1 was played). As many words = a tie, no match.
    rivals = [b for b in asked[1:] if b["matched"] >= a["matched"] or b["matched"] < b["tokens"]]
    second = rivals[0]["cover"] if rivals else 0.0
    if a["matched"] < a["tokens"] and not _only_wh_missing(a):
        return None
    if not a["span"] or a["matched"] / a["span"] < ASK_PRECISION:
        return None
    if second >= a["cover"] - MARGIN:
        return {"tie": True}
    return {**a, "second": second}


def key_words_ok(best: dict, second: dict | None) -> bool:
    """He said a much shorter version: only the line's key words (the words no other host line has). Enough of them,
    nearly all the words he said between the first and the last of them, and no other line sharing any."""
    if best["dtotal"] < KEY_MIN or best["dmatched"] < max(KEY_MIN, math.ceil(KEY_COVER * best["dtotal"])):
        return False
    if best["dgroups"] < KEY_GROUPS:
        return False
    if best["span"] and best["matched"] / best["span"] < KEY_PRECISION:
        return False
    return second is None or second["dmatched"] <= best["dmatched"] - 2 or second["dmatched"] == 0


def decide_text(text: str, cue_ids: list[str], *, final: bool = False) -> dict:
    """The pure rule: the host's words `text` against the host lines of `cue_ids` (the next unplayed cues, in
    order) -> {"verdict", "cue", "cover", "second", ...}. verdict: "match", or why not: "nothing", "weak", "tie",
    "no_tail", "diluted". `final`: he has been quiet for the tail's grace -- the line's ending need not have been said
    (a shorter version of it). scripts/eval_ceremony_follow.py and the tests call this same function.

    Two ways to match (owner 24 Nov: "อาจจะสั้นกว่าหรือยาวกว่าก็ได้"): the line's weighted words (any order, banter
    before or after it does not count against him: only the span between his first and last word of it is judged), or
    -- a much shorter version -- its key words (`key_words_ok`)."""
    heard = tokens(text)
    out = {"verdict": "nothing", "cue": None, "cover": 0.0, "second": 0.0, "matched": 0, "tokens": 0, "heard": len(heard)}
    if not heard or not cue_ids:
        return out
    pos: dict = {}
    for k, h in enumerate(heard):
        pos.setdefault(h, (k, k))
        if k + 1 < len(heard):
            pos.setdefault(h + heard[k + 1], (k, k + 1))
    scored = sorted((_score(c, heard, pos) for c in cue_ids if c in _compiled() or c + EN in _compiled()),
                    key=lambda s: -s["cover"])
    if not scored:
        return out
    best = scored[0]
    nxt = scored[1] if len(scored) > 1 else None
    second = nxt["cover"] if nxt else 0.0
    out.update(cue=best["cue"], cover=round(best["cover"], 3), second=round(second, 3), matched=best["matched"],
               tokens=best["tokens"], by="cover", lang=best["lang"])
    need = max(2, min(MIN_MATCHED, best["tokens"]))
    bar = STRICT_COVER if best["tokens"] <= STRICT_TOKENS else MIN_COVER
    if best["lang"] == "en" and best["tokens"] <= STRICT_TOKENS:
        need = best["tokens"]                    # a short English line is every word of it ("Sea view rooms are popular" is not "I would like a room with a sea view")
    if best["cover"] < bar or best["matched"] < need:
        out["verdict"] = "weak" if best["cover"] >= 0.3 else "nothing"
        if best["tokens"] > STRICT_TOKENS and key_words_ok(best, nxt):
            out.update(verdict="match", by="key")
        elif (final and best["cue"] == cue_ids[0] and best["cover"] >= NEXT_COVER and best["matched"] >= 2
              and second <= best["cover"] - MARGIN and best["cue"] not in generic_cues("any")):
            # The very next cue, said loosely (rehearsal 24 Nov 11:29: Khun Miki's real opening line reached
            # cover 0.348 against our translated line -- "weak", nothing played, no film). Only the NEXT cue, only
            # once he has finished (final), never a generic line: on the 1,008 off-script lines x cues of the eval
            # the best non-generic cover was 0.295 (generic ones reach 0.87 -- excluded).
            out.update(verdict="match", by="next")
    elif second >= 0.4 and second > best["cover"] - MARGIN:
        out["verdict"] = "tie"
    elif best["tokens"] >= SHORT_LINE and not best["tail"] and best["cover"] < TAIL_FREE_COVER and not final:
        out["verdict"] = "no_tail"
    elif best["span"] and best["matched"] / best["span"] < MIN_PRECISION:
        out["verdict"] = "diluted"
    else:
        out["verdict"] = "match"
    if out["verdict"] != "match":
        # (every decision is taken once his words have stopped for QUIET_S -- _decide; the first version asked for `final`
        # too, which only the tail-grace path ever passes: rehearsal 24 Nov 14:44, "What makes our project different?"
        # was judged "nothing" on the line alone and the question was never tried)
        a = _ask_match(heard, pos, cue_ids, text)
        if a is None:
            # his question at the END of a long talk to the guests (owner 24 Nov ~14:58): the words of the question
            # often come earlier in the talk too ("our project ... where is our project located?") and the span from
            # their first mention dilutes it -- the last sentence alone is asked as well
            last = _last_sentence(text)
            if last:
                tail = tokens(last)
                a = _ask_match(tail, _positions(tail), cue_ids, last, whole=text)
        if a is None:
            a = _ask_word(heard, cue_ids, text)
        if a is not None and not a.get("tie") and settings.ceremony_name_first:
            from app import ceremony_call

            said = ceremony_call._plain(ceremony_call.last_words(text))
            if any(re.search(p, said) for p in (lines().get(a["cue"]) or {}).get("not") or ()):
                a = None
        if a is not None and not a.get("tie"):
            out.update(verdict="match", by="ask", cue=a["cue"], cover=round(a["cover"], 3), second=round(a["second"], 3),
                       matched=a["matched"], tokens=a["tokens"], lang="en")
    return out


def decide_windows(utterances: list[str], cue_ids: list[str], *, final: bool = False) -> dict:
    """`utterances` oldest first: the line may be said across a pause, so the last 1..BUFFER_UTTERANCES of them are
    tried as one text (newest window first); the first window that matches wins, else the best cover seen."""
    best = None
    for k in range(1, min(BUFFER_UTTERANCES, len(utterances)) + 1):
        res = decide_text(" ".join(utterances[-k:]), cue_ids, final=final)
        res["window"] = k
        if res["verdict"] == "match":
            return res
        if best is None or res["cover"] > best["cover"]:
            best = res
    return best or decide_text("", cue_ids)


def generic_cues(lang: str = "th") -> list[str]:
    """The host lines too generic to match on key words (fewer than KEY_MIN words no other line has): they are matched
    only when he says most of the line; said shorter, the staff page shows them as "กดเอง". `lang`: "th" (the Thai
    lines, the default), "en" (the English ones), "any" (generic in either: what the staff page marks)."""
    key = _distinct()
    out = []
    for cue, toks in _compiled().items():
        if ASK in cue:
            continue                                     # (a question variant is short by design: its line decides)
        is_en = cue.endswith(EN)
        if lang != "any" and is_en != (lang == "en"):
            continue
        if len(toks) <= STRICT_TOKENS or len(key.get(cue) or ()) < KEY_MIN:
            name = cue[:-len(EN)] if is_en else cue
            if name not in out:
                out.append(name)
    return out


# ---- where the order is: the pointer ------------------------------------------------------

def live_first() -> bool:
    """CEREMONY_FOLLOW_CUES off (owner 24 Nov ~11:40, "อยากให้มันตอบสดเลย ไม่ต้องมากดตามบท"): the matcher never presses
    a cue, never holds or drops her reply -- it shows the matched cue's slide and moves the pointer on."""
    return not settings.ceremony_follow_cues


def free_talk() -> bool:
    """CEREMONY_FREE_TALK (owner 24 Nov ~12:40, "สุดท้ายก็คุยด้นสด ... เราแค่ปรับโมชั่นให้มันดีขึ้น"), on top of live first and a
    running, unpaused follow: she answers freely, nothing nudges her, no joke plays by itself, the picker marks no step."""
    try:
        return bool(live_first() and settings.ceremony_free_talk and settings.ceremony_follow_host
                    and STATE["on"] and not STATE["paused"])
    except Exception:                                    # noqa: BLE001
        return False


#: What may take the hall in an exchange when something of ours is up already: a cue slide replaces a topic slide or
#: another cue (a refinement); a story replaces only a topic slide; a topic slide only an empty hall. "The first clear
#: motion of an exchange wins" -- a story never replaces the cue slide of the same exchange.
_MAY_REPLACE = {"cue": (None, "topic", "cue"), "story": (None, "topic"), "topic": (None,)}


def claim_hall(kind: str, stamp=None) -> bool:
    """Free talk: may `kind` ("cue" / "story" / "topic") put motion on the hall screen for the current exchange (the
    newest host utterance)? True = yes, and it is recorded (the logo timer restarts). Outside free talk always True."""
    if not free_talk():
        return True
    if stamp is not None:
        STATE["ex_stamp"] = stamp
    ex = STATE["ex"]
    if ex is None or ex["stamp"] != STATE["ex_stamp"]:
        ex = STATE["ex"] = {"stamp": STATE["ex_stamp"], "motion": None}
    if ex["motion"] not in _MAY_REPLACE.get(kind, ()):
        turnlog.record("ceremony_follow", action="hall", kind=kind, ok=False, held_by=ex["motion"])
        return False
    ex["motion"] = kind
    STATE["hall"], STATE["idle_since"], STATE["hall_obj"] = True, None, None
    turnlog.record("ceremony_follow", action="hall", kind=kind, ok=True)
    return True


def story_wanted(topic) -> bool:
    """face_mirror.view: a story of hers on the hall screen (free talk). True = show it. Not when the exchange has a cue
    slide up already; the story otherwise stops being "the script's" (it is shown even if a cue's `show`). Never raises."""
    try:
        return claim_hall("story")
    except Exception:                                    # noqa: BLE001 -- the story goes out
        return True


def hall_released() -> None:
    """Staff pressed the script (ceremony_motion.on_press): the settled-slide rules of a press take over -- the logo
    timer is not ours any more."""
    STATE["hall"], STATE["idle_since"], STATE["hall_obj"] = None, None, None
    STATE["pick"] = None                                 # (review B 24 Nov: the pressed line's own words must not refine a pick)


def _pressed_line() -> bool:
    """A staff-pressed line is on (its words flow through the same transcript hook as her live answers)."""
    try:
        from app import ceremony, event_cues, event_order

        return bool(event_cues.line_audible() or ceremony.STATE.get("script_presses")
                    or event_order.STATE.get("pressing") is not None)
    except Exception:                                    # noqa: BLE001
        return False


def _answer_busy() -> bool:
    """Her answer is not over, or the host is talking again: a press gate is up (streaming / a line audible / a walk),
    his words are being decided or he is speaking, or her voice is still playing on the page by the server's clock."""
    if _gate_why() is not None or STATE["timer"] is not None or _host_speaking():
        return True
    return _voice_playing()


def _voice_playing() -> bool:
    """Her voice is still playing on the page by the server's clock (a turn_complete can come ~20 s before it ends)."""
    try:
        session = STATE.get("session")
        until = getattr(session, "_array_audible_until", None)
        return bool(callable(until) and until(time.monotonic()) > time.monotonic())
    except Exception:                                    # noqa: BLE001
        return False


async def _free_tick(now_m: float) -> None:
    """Free talk: the hall shows something of ours and her answer has been over for CEREMONY_LOGO_AFTER_S with no new
    motion -> the show's logo loop (`face_mirror.motion(None, None)`: the same message a window's opening sends). A
    new motion (claim_hall) or her next answer restarts the wait. Never raises."""
    try:
        if not free_talk():
            STATE["hall"], STATE["idle_since"] = None, None
            return
        if not STATE["hall"]:
            return
        if _answer_busy():
            STATE["idle_since"] = None
            return
        from app import face_mirror

        kind = (STATE["ex"] or {}).get("motion")
        if kind != "story":
            now_obj = face_mirror.motion_now()           # revocable: our slide still the one held? (a press's subtitle took it)
            if STATE["hall_obj"] is None:
                STATE["hall_obj"] = now_obj
            elif now_obj is not STATE["hall_obj"]:
                hall_released()
                return
        if STATE["idle_since"] is None:
            STATE["idle_since"] = now_m
            return
        if now_m - STATE["idle_since"] < settings.ceremony_logo_after_s:
            return
        _to_logo("free_talk")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: back to the logo")


def _to_logo(why: str) -> None:
    """The show's logo loop on the hall screen: the slide down (`motion(None, None)`, what a window's opening sends) and a
    story of hers down too (review B 24 Nov: `motion` leaves `body.mirror-story` up) -- the same `clear` tool result
    the robot's own clear sends. Never raises."""
    try:
        from app import ceremony_motion, face_mirror

        was_story = (STATE["ex"] or {}).get("motion") == "story"
        STATE["hall"], STATE["idle_since"], STATE["hall_obj"] = None, None, None
        ceremony_motion.STATE.update(topic=None, sent=True, cue_up=None)
        if was_story:
            face_mirror.publish_json({"type": "tool_result", "result": {"screen": "clear"}})
        face_mirror.motion(None, None)
        turnlog.record("ceremony_motion", shown="logo", why=why)
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the logo")


def _all_host_cues() -> list:
    """Every cue of the order he asks for, in order: [{n, i, cue, state, behind}] (`behind` = the order is past it).
    Never raises."""
    try:
        from app import event_order

        scenes = event_order._scenes() or []
        rows, out = lines(), []
        flat = event_order._flat(scenes)
        last = event_order.STATE["last"]
        at = flat.index(tuple(last)) if last is not None and tuple(last) in flat else -1
        for k, (n, i) in enumerate(flat):
            st = event_order._find(scenes, n, i) or {}
            cue = st.get("cue")
            if st.get("do") != "cue" or not cue or (rows.get(cue) or {}).get("kind") != "host":
                continue
            state = (event_order.STATE["marks"].get(event_order._key(n, i)) or {}).get("state")
            out.append({"n": n, "i": i, "cue": cue, "state": state, "behind": k <= at})
        return out
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the cues he asks for")
        return []


def _is_request(text: str) -> bool:
    """He is asking for something, not telling the guests about it (app/ceremony_call.py)."""
    from app import ceremony_call

    return ceremony_call.is_request(text)


def _elsewhere_match(texts: list, near: list, addressed: bool = False):
    """(cand, res): a question of the script that is NOT among the next few -- asked out of order, come back to, or asked
    again (owner 24 Nov ~16:50 "เราต้องเผื่อกรณีที่เขาถามข้ามไปมานะ เพราะบางอาจจะถามอันนี้ก่อนแล้วกลับมาถามอันนี้ก็ได้") -- or None.
    Weighed against every question of the script, the next few included (a tie is no match). One he went past and that
    was never said plays like the next ones; one already said, or far ahead, only when his words ASK for it
    (`_is_request`), and a joke already told only by her name. `addressed` (her name first): he called her -- only one
    already said must still be asked for. The order never goes back. Never raises."""
    try:
        every = _all_host_cues()
        if not every or not texts:
            return None
        last = str(texts[-1])
        res = decide_windows([last], [d["cue"] for d in every])
        if res["verdict"] != "match" or res["cue"] in near:
            return None
        cand = next(d for d in every if d["cue"] == res["cue"])
        return (cand, res) if _may_play_again(cand, last, addressed) else None
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: a question out of order")
        return None


def _may_play_again(cand: dict, last: str, addressed: bool) -> bool:
    if cand["state"] == "running":
        return False
    joke = cand["cue"] in {cue for _prev, _setup, cue in _joke_pairs()}
    if addressed:
        from app import ceremony_call

        # (a line not said yet still needs his words to ASK for it -- or be its own told kind, the praise before a joke.
        # Long-talk test 24 Nov night: "Emma, before I ask you, let me tell everyone something." held the call for a minute
        # and every told sentence after it that sounded like a line played it: the lights went off on "You will see the
        # lobby lights turn off later today.")
        tell = bool((lines().get(cand["cue"]) or {}).get("tell"))
        return (cand["state"] != "done" and tell) or joke or _is_request(last) or ceremony_call.short(last)
    if cand["state"] == "done" or not cand["behind"]:
        from app.providers.transcript_filter import HER_NAME_ONLY

        return bool(HER_NAME_ONLY.search(last)) if joke else _is_request(last)
    return True


# ---- her name first (CEREMONY_NAME_FIRST, owner 24 Nov ~17:40; app/ceremony_call.py) ----------------------

#: He called her and goes on talking: the call holds while he pauses no longer than this between two sentences...
CALL_GAP_S = 12.0
#: Her name alone this soon after his question is the call for it.
CALL_AFTER_S = 8.0
#: ...and for this long in all, from the sentence with her name.
CALL_MAX_S = 60.0
#: A short answer to a question SHE just asked him ("what kind of view do you like?") would need no name for this long after
#: her line. 0 = her name, always (owner 24 Nov ~18:10: "ให้พูด emma ทุกครั้งแม้ว่าจะสั่งเปิดปิดไฟ ม่าน ด้วย").
REPLY_TO_HER_S = 0.0
#: Off the script: this much more silence before her live answer is let out (he may only be taking a breath).
OFF_SCRIPT_WAIT_S = 1.0
#: Her leave to answer his last words live runs out after this.
MAY_ANSWER_S = 20.0
#: A decision does not wait for a voice segment that never closes (room noise) longer than this.
HOST_TALK_MAX_S = 3.0


def name_first() -> bool:
    return bool(settings.ceremony_name_first and settings.ceremony_script_show_only and not live_first())


def may_answer(text: str) -> bool:
    """NoiseReplyGate / the silence watchdog: may she answer these words live? Her name first: only when the decision
    about his words said so (he called her, and asked for something no line answers). Otherwise: her name is in them."""
    if name_first():
        # (it runs out: a voice with no caption long after her answer is not answered on it -- review 24 Nov)
        return bool(STATE["may_answer"]) and time.monotonic() - STATE["may_at"] <= MAY_ANSWER_S
    from app.providers.transcript_filter import HER_NAME_ONLY

    return bool(HER_NAME_ONLY.search(text or ""))


def _note_call(at: float, text: str, now: float) -> None:
    from app import ceremony_call

    hist = STATE["hist"]
    hist[at] = {"t": now, "called": ceremony_call.called(text)}
    STATE["may_answer"] = False
    for key in [k for k in hist if at - k > CALL_MAX_S + BUFFER_S]:
        hist.pop(key, None)


def addressed(stamp: float) -> float | None:
    """The utterance he called her in (its time), None = he did not: this one, or one before it he has gone on from
    (CALL_GAP_S / CALL_MAX_S) with no line played and no answer given since."""
    hist, spent = STATE["hist"], max(STATE["spent"], STATE["call_spent"])
    after = None
    for key in sorted((k for k in hist if spent < k <= stamp), reverse=True):
        if (after is not None and after - hist[key]["t"] > CALL_GAP_S) or stamp - key > CALL_MAX_S:
            return None
        if hist[key]["called"]:
            return key
        after = key
    return None


def _since_call(key: float) -> list:
    """His words from the call on -- what he told the guests before her name is not his question to her. Her name alone
    right after a question ("Where is our project located? ... Emma?") is: that sentence counts."""
    keys = sorted(STATE["utt"])
    texts = [STATE["utt"][k] for k in keys if k >= key]
    before = [k for k in keys if k < key]
    from app import ceremony_call

    if before and len(texts) == 1 and (ceremony_call.only_her_name(texts[0]) or ceremony_call.points_back(texts[0])):
        prev = before[-1]
        if key - (STATE["hist"].get(prev) or {"t": prev})["t"] <= CALL_AFTER_S:
            return [" ".join([str(STATE["utt"][prev]), str(texts[0])])]
    return texts


def _reply_cue() -> str | None:
    """The host cue his words may answer without her name: the next one after the line she just said, when that line
    ended by asking him something. None = none. Never raises."""
    try:
        from app import event_cues, event_order, event_script

        log = event_cues.STATE.get("said_log") or []
        if not log or time.monotonic() - float(log[-1]["at"]) > REPLY_TO_HER_S:
            return None
        said = log[-1]["id"]
        line = ((event_script.servable() or {}).get(said) or {}).get("en") or ""
        if not line.strip().endswith("?"):
            return None
        scenes, rows, past = event_order._scenes() or [], lines(), False
        for n, i in event_order._flat(scenes):
            st = event_order._find(scenes, n, i) or {}
            if past and st.get("do") == "cue" and (rows.get(st.get("cue")) or {}).get("kind") == "host":
                return st["cue"]
            past = past or st.get("cue") == said
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: an answer to her question")
    return None


def _meaning_rules(cues: list, nxt: str | None) -> dict:
    rows = lines()
    out = {}
    for cue in cues:
        row = rows.get(cue) or {}
        means = list(row.get("means") or ())
        out[cue] = {"means": means, "not": row.get("not") or [], "not_all": row.get("not_all") or [],
                    "next": list(row.get("means_next") or ()) if cue == nxt else [], "alike": row.get("alike"),
                    "tell": bool(row.get("tell"))}
    return out


def _meaning_match(texts: list, near: list, only: str | None = None):
    """(cand, res): the cue his words MEAN when they are not the script's (host.json `means`; `means_next` counts for the
    very next cue only), or None. His last utterance; a short one ("... our project?") with the one before it."""
    try:
        from app import ceremony_call

        every = [d for d in _all_host_cues() if only is None or d["cue"] == only]
        if not every or not texts:
            return None
        rules = _meaning_rules([d["cue"] for d in every], only or (near[0] if near else None))
        if only is None:
            # what the script does not answer, as lines of its own (app/ceremony_extra.py): weighed beside the script's
            from app import ceremony_extra

            rules.update(ceremony_extra.rules())
        last = str(texts[-1])
        tries = [last]
        # (a whole question of its own is the question: the told captions before it are not -- long-talk test 24 Nov night,
        # "What is the weather like in Pattaya in December?" after a told "the quotation is printed in one minute" printed it)
        whole_question = "?" in last and len(last.split()) >= 4
        if len(texts) > 1 and len(last.split()) <= 10 and not whole_question:
            tries.append(" ".join(str(t) for t in texts[-2:]))
        if len(texts) > 2 and len(last.split()) <= 10 and not whole_question and sum(len(str(t).split()) for t in texts) <= 40:
            tries.append(" ".join(str(t) for t in texts))    # ("Let us look at the rooms Emma / how many / in total")
        for said in tries:
            res = ceremony_call.by_meaning(said, rules)
            if res is None:
                continue
            cand = next((d for d in every if d["cue"] == res["cue"]), None)
            if cand is None:
                return {"n": None, "i": None, "cue": res["cue"], "state": None, "behind": True, "extra": True}, res
            if only is not None or _may_play_again(cand, said, True):
                return cand, res
            return None
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: what he means")
    return None


def _alike(cand: dict, near: list) -> dict:
    """Two lines of his that sound alike (the praise before each joke): the one that comes next is the one he says."""
    rows = lines()
    group = (rows.get(cand["cue"]) or {}).get("alike")
    if not group or cand["cue"] in near[:2]:
        return cand
    for cue in near[:2]:
        if (rows.get(cue) or {}).get("alike") == group:
            return next((d for d in _all_host_cues() if d["cue"] == cue), cand)
    return cand


def _host_talking() -> bool:
    """His voice is in the mic now: the VAD's segment is open (not session._guest_speaking, which counts any voice in the
    room for a while: a noisy hall would hold every decision). Never raises; anything unknown = False."""
    try:
        gate = getattr(getattr(STATE.get("session"), "provider", None), "_vad_gate", None)
        return getattr(gate, "in_speech", False) is True
    except Exception:                                    # noqa: BLE001
        return False


def _off_script_logo(texts: list) -> None:
    """The script shown (CEREMONY_SCRIPT_SHOW_ONLY): his words matched no line -- she answers live, and the last line's
    settled slide must not stand under that answer (rehearsal 24 Nov 15:08: "Prepare a quotation for this unit?" stayed
    up while she answered the facilities and the location again; owner: "อะไรที่ไม่มีในบท ... ไม่ต้องโชว์อะไร"). The logo,
    unless a line is still being said / a joke's punchline is coming, or the words were Thai in the English-only
    ceremony (the staff, not him). Never raises."""
    try:
        from app import event_cues
        from app.providers.transcript_filter import _thai_only

        last = str(texts[-1] if texts else "")
        if not last.strip() or (settings.ceremony_english_only and _thai_only(last)):
            return
        if event_cues.line_audible() or _gate_why() in ("busy", "chain", "speaking"):
            return
        _to_logo("off_script")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the logo after an off-script answer")


def _exchange_unclaimed() -> None:
    """Free talk, his words decided and no motion picked for this exchange: the last exchange's slide / story does not
    stay under the new question (review B 24 Nov) -- the logo, until a motion of this exchange comes."""
    if free_talk() and STATE["hall"]:
        ex = STATE["ex"]
        if ex is None or ex["stamp"] != STATE["ex_stamp"]:
            _to_logo("new_exchange")


def pointer_of(scenes: list[dict], marks: dict, last, host: dict, chain_waiting: bool = False,
               live: bool = False, read_past: bool = False) -> dict:
    """Pure. The steps after `last` that are not taken, up to the first that stops the scan:
    {"kind": "host"|"continues"|"manual"|"gate"|"running"|"chain"|"end", "cands": [{n, i, cue, host_th}, ...] (the
    host-line cues in front, at most LOOKAHEAD), "at": (n, i) of the step that stopped it, "label": its words,
    "why_th"}. A step is taken when its mark is done / skipped; `running` blocks; `failed` is not taken."""
    from app import event_order

    flat = event_order._flat(scenes)
    start = event_order._after(scenes, last)
    cands: list[dict] = []
    cont = None                                          # (read_past: the continuation in front, still to play)
    if start is None:
        return {"kind": "end", "cands": [], "at": None, "label": None, "why_th": "จบลำดับพิธีแล้ว"}
    for (n, i) in flat[flat.index(start):]:
        step = event_order._find(scenes, n, i)
        mark = marks.get(event_order._key(n, i))
        state = (mark or {}).get("state")
        if state in ("done", "skipped"):
            continue
        label = step.get("label_th") or step.get("cue") or step.get("do")
        if read_past and step.get("do") == "manual" and state != "running":
            # The script shown (owner 24 Nov ~15:12: "ของจริงต้องไม่ค้างนะ ไม่งั้นจะทำไรไม่ได้"): a staff tick nobody ticked
            # ("ใบเสนอราคาพร้อม", "ไฟดับแล้ว") held every later question of his (rehearsal 15:02-15:06). His next question
            # goes past it; the "...done" line after the tick is a staff cue and is never said unconfirmed.
            continue
        if cands and (state == "running" or step.get("do") != "cue" or event_order._is_live(step)):
            break                                        # the cues in front of the stopper may go; nothing behind it
        if state == "running":
            return {"kind": "running", "cands": [], "at": (n, i), "label": label,
                    "why_th": "กำลังทำ: %s" % label}
        if step.get("do") != "cue" or event_order._is_live(step):
            return {"kind": "gate", "cands": [], "at": (n, i), "label": label,
                    "why_th": "รอขั้น: %s (ทีมงานทำ/กดเอง)" % label}
        row = host.get(step.get("cue")) or {"kind": "manual"}
        if row["kind"] == "host":
            cands.append({"n": n, "i": i, "cue": step["cue"], "host_th": row["host_th"], "host_en": row.get("host_en")})
            if len(cands) >= (SCRIPT_LOOKAHEAD if read_past else LOOKAHEAD):
                break
            continue
        if live and row["kind"] != "host":
            continue                                     # live first: nothing plays a continuation / needs a press; the pointer reads past it
        if read_past and (cands or row["kind"] == "manual"
                          or (row["kind"] == "chain_follow" and not chain_waiting)):
            # (+ a punchline whose set-up's chain is gone -- a restart mid-joke, rehearsal 14:55: the pointer sat on
            # s04_l11 for good. A punchline that IS coming is `chain_waiting`, and the gates hold a press meanwhile.)
            # The script shown (CEREMONY_SCRIPT_SHOW_ONLY, rehearsal 24 Nov 14:36): a missed first question left the
            # pointer on s02_l03 with s02_l05 (a continuation) behind it -- every later question of his was matched
            # against s02_l03 alone, and none ever played. His next questions are looked at past a continuation / a
            # staff line; a continuation that IS the next step still plays by itself (returned below when first).
            continue
        if cands:
            break                                        # the cue has no host line: nothing behind it is looked at
        if row["kind"] == "continues" and read_past and cont is None:
            # (scenario review 24 Nov: a continuation that never plays -- staff skipped the empty step before it, its
            # line before it is 90 s old, its press was refused -- held every later question for good; and a question
            # asked while it waited to play was lost. It is still the pointer (it plays by itself), with his next
            # questions behind it: one of them asked = he went on, and it is left behind.)
            cont = {"kind": "continues", "at": (n, i), "label": label, "why_th": "Emma พูดต่อเองหลังบทก่อนจบ",
                    "cue": step["cue"]}
            continue
        if row["kind"] == "continues" and read_past and cont is not None:
            # (two in a row -- s05_l07, empty, and s05_l08: the SECOND was returned as the pointer, with no questions
            # behind it; the first was never skipped, so the second had no line before it and never came: rehearsal
            # 24 Nov 17:33, "would you like me to prepare a quotation" not said and "blocked" at 17:35)
            continue
        if row["kind"] == "continues":
            return {"kind": "continues", "cands": [], "at": (n, i), "label": label,
                    "why_th": "Emma พูดต่อเองหลังบทก่อนจบ", "cue": step["cue"]}
        if row["kind"] == "chain_follow" and chain_waiting:
            return {"kind": "chain", "cands": [], "at": (n, i), "label": label,
                    "why_th": "เฉลยมุกตามมาเองหลังมุกเกริ่น"}
        return {"kind": "manual", "cands": [], "at": (n, i), "label": label, "cue": step["cue"],
                "why_th": (row.get("note_th") or NO_HOST_TH) + " — กดบท: %s" % label}
    if cont is not None:
        return {**cont, "cands": cands}
    if cands:
        return {"kind": "host", "cands": cands, "at": (cands[0]["n"], cands[0]["i"]), "label": None, "why_th": None}
    return {"kind": "end", "cands": [], "at": None, "label": None, "why_th": "จบลำดับพิธีแล้ว"}


def pointer() -> dict:
    from app import event_order

    scenes = event_order._scenes()
    if scenes is None:
        return {"kind": "end", "cands": [], "at": None, "label": None, "why_th": "ไม่มีลำดับพิธี"}
    try:
        event_order._settle(scenes)                      # a line that was said is done now, not at the page's next poll
    except Exception:                                    # noqa: BLE001 -- a stale mark only delays a continuation
        logger.exception("ceremony follow: settling the order")
    chain = event_order.STATE.get("chain") or {}
    waiting = chain.get("state") == "waiting"
    ptr = pointer_of(scenes, event_order.STATE["marks"], event_order.STATE["last"], lines(), waiting,
                     live=live_first(), read_past=bool(settings.ceremony_script_show_only and not live_first()))
    if ptr["kind"] == "host" and waiting:
        ptr = {**ptr, "kind": "chain", "why_th": "มุกกำลังพูดต่อเนื่อง"}
    if ptr["kind"] == "continues" and STATE["cont_failed"] == tuple(ptr["at"]):
        # the server tried to go on by itself and was refused (a line with no words yet, a gate): staff's now
        why = (STATE["last_fire"] or {}).get("error_th") or "ยังเล่นเองไม่ได้"
        ptr = {**ptr, "kind": "manual", "why_th": "เล่นต่อเองไม่ได้: %s — กดบทนี้เอง หรือกด 'ข้ามขั้นนี้' ในลำดับพิธี" % why}
    return ptr


# ---- the mode -----------------------------------------------------------------------------

def start_refusal() -> str | None:
    """Why card 2 cannot be pressed now (Thai), None = it can. The ceremony app being on is the order's own gate."""
    from app import ceremony

    if not settings.ceremony_follow_host:
        return "โหมดคุยสดปิดอยู่ (CEREMONY_FOLLOW_HOST)"
    if ceremony.STATE.get("replies") not in (None, "cues"):
        return "พิธีนี้ตั้งให้ Emma ตอบเมื่อถูกเรียกชื่ออยู่แล้ว — ไม่ต้องใช้โหมดนี้"
    if not lines():
        return "ไม่พบไฟล์บทคุณมิกิ (floor1_ceremony.host.json)"
    return None


def active() -> bool:
    """Follow mode is on and the call that started it is still the ceremony's."""
    if not (STATE["on"] and settings.ceremony_follow_host):
        return False
    from app import ceremony

    now_session = ceremony.active_session()
    if now_session is None:
        return False
    if now_session is STATE["session"]:
        STATE["lost_at"] = None
        return True
    return _adopt(now_session)


def _adopt(session) -> bool:
    """The ceremony call was re-dialled (the network): a NEW call of the same app (ceremony.active_session() is only
    ever the ceremony app's) takes follow over -- pointer (the order's marks), pause and the host's file unchanged,
    the window re-opened by the keeper. Never raises."""
    try:
        STATE.update(session=session, lost_at=None, utt={}, pending=False, dropped=False, timer=None,
                     redials=STATE["redials"] + 1, redialed_at=time.time(), closed_seen=time.time())
        turnlog.record("ceremony_follow", action="redial", n=STATE["redials"], paused=bool(STATE["paused"]))
        return True
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: adopting the re-dialled call")
        return False


def answers_all() -> bool:
    """CEREMONY_FOLLOW_ANSWER_ALL: follow is on, not paused, and its live window is open -- her live answer to the host
    needs no name now (providers/gemini.py `_ceremony_named_only`). Never raises."""
    try:
        if not (settings.ceremony_follow_answer_all and settings.ceremony_follow_host and STATE["on"]
                and not STATE["paused"]):
            return False
        from app import ceremony

        return active() and ceremony._adhoc_live(time.time()) is not None
    except Exception:                                    # noqa: BLE001
        return False


def reply_hold() -> str | None:
    """What NoiseReplyGate asks at her live reply's first event (answers_all mode): "drop" = the host's words matched
    a cue, the recording plays alone; "wait" = follow has not decided them yet (the reply is held, bounded); None =
    no match, the reply is hers. Never raises."""
    try:
        if live_first():
            return None                                  # live first: her reply is never held or dropped (owner 24 Nov)
        if not (settings.ceremony_follow_answer_all and settings.ceremony_follow_host and STATE["on"]
                and not STATE["paused"] and active()):
            return None
        if STATE["dropped"]:
            return "drop"                                # (the press closed the window: still this reply's to drop)
        from app import ceremony

        if ceremony._adhoc_live(time.time()) is None:
            return None
        if (STATE["pending"] and STATE["heard_mono"] is not None
                and time.monotonic() - STATE["heard_mono"] < REPLY_HOLD_MAX_S):
            return "wait"
        pw = STATE.get("press_wait")
        if pw is not None and time.monotonic() - pw < SCRIPT_HOLD_S + 2.0:
            # a cue is matched and waits for her held reply's turn to end before it is read (rehearsal 24 Nov 15:21:
            # the 6 s hold ran out first, her reply came out of the speaker, and the press then waited on THAT) --
            # held until the press is decided, bounded
            return "wait"
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the reply hold")
    return None


def wants_words(session) -> bool:
    """session._note_ceremony_words asks FIRST, on every caption of every call: True only on the ceremony call
    while follow is on, not paused, and the live window is open (the mic is up). Never raises."""
    try:
        if STATE["on"] and session is not None and session is not STATE["session"]:
            active()                                     # a re-dialled ceremony call is adopted here too
        if not STATE["on"] or STATE["paused"] or session is None or session is not STATE["session"]:
            return False
        if not settings.ceremony_follow_host:
            return False
        from app import ceremony

        return session is ceremony.active_session() and ceremony._adhoc_live(time.time()) is not None
    except Exception:                                    # noqa: BLE001 -- the call, never the matcher
        return False


async def start(*, by: str = "order") -> dict:
    """Card 2 pressed (event_order `follow` step), or the page's "เปิดโหมดสด". Already on and paused = resume."""
    from app import ceremony

    why = start_refusal()
    if why:
        return {"ok": False, "error": "follow_refused", "error_th": why}
    session = ceremony.active_session()
    if session is None:
        return ceremony.refusal()
    if STATE["on"] and STATE["session"] is session:
        if STATE["paused"]:
            resume(by)
            return {"ok": True, "follow": "resumed"}
        return {"ok": True, "follow": "already"}
    reset()
    STATE.update(on=True, since=time.time(), session=session)
    try:
        asyncio.get_running_loop().run_in_executor(None, warm)
    except RuntimeError:
        warm()
    STATE["keeper"] = _spawn(_keep())
    turnlog.record("ceremony_follow", action="start", by=by, cues=len(lines()))
    return {"ok": True, "follow": "on"}


def stop(why: str = "staff") -> bool:
    """"หยุดโหมดสด / กลับไปกดเอง", the app closing, an order reset. The window closes; nothing re-opens it."""
    if not STATE["on"]:
        return False
    was = STATE["since"]
    keeper, timer = STATE["keeper"], STATE["timer"]
    STATE.update(on=False, paused=None, timer=None, keeper=None, utt={}, session=None)
    for handle in (keeper, timer):
        if handle is not None:
            try:
                handle.cancel()
            except Exception:                            # noqa: BLE001
                pass
    try:
        from app import ceremony

        ceremony.adhoc_close("staff")
    except Exception:                                    # noqa: BLE001 -- the mode is off either way
        logger.exception("ceremony follow: closing the window")
    turnlog.record("ceremony_follow", action="stop", why=str(why)[:24],
                   on_s=round(max(0.0, time.time() - was), 1) if was else None)
    return True


def pause(why: str = "stop") -> bool:
    """The red stop, or "กลับเข้าบท": follow waits for staff to resume. Synchronous: the stop's route calls it before
    anything it awaits."""
    if not STATE["on"] or STATE["paused"]:
        return False
    STATE["paused"] = str(why)[:24]
    STATE["decision"] += 1                               # a decision waiting for her voice is void
    joke = STATE["joke"]
    if joke is not None:                                 # a joke waiting for its moment is off (staff press it, or not)
        STATE["joke_done"] = (STATE["joke_done"] or set()) | {joke["setup"]}
        _joke_cancel("paused")
    STATE["utt"] = {}
    STATE["pending"] = STATE["dropped"] = False
    timer = STATE["timer"]
    STATE["timer"] = None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:                                # noqa: BLE001
            pass
    turnlog.record("ceremony_follow", action="pause", why=STATE["paused"])
    return True


def resume(by: str = "staff") -> bool:
    if not STATE["on"] or not STATE["paused"]:
        return False
    STATE.update(paused=None, utt={}, cont_failed=None, closed_seen=time.time())   # (the stop's closure is read: not again)
    turnlog.record("ceremony_follow", action="resume", by=by)
    return True


# ---- the host's words ---------------------------------------------------------------------

def on_host_words(session, caption) -> None:
    """The host's words so far in this utterance (session's receive loop, after heard.record). Decided once they
    stop growing for QUIET_S. Only text is kept, in memory, never logged. Never raises."""
    try:
        if not wants_words(session) or caption is None:
            return
        at = getattr(caption, "at", None)
        text = getattr(caption, "text", "") or ""
        if at is None or not text.strip() or at <= STATE["spent"]:
            return                                       # (empty, or the utterance a cue was already played for)
        if settings.ceremony_english_only:
            from app.providers.transcript_filter import _thai_only

            if _thai_only(text):
                # (review 24 Nov: the staff's Thai in the room filled the 3-utterance buffer and pushed his question out,
                # cancelled a decision waiting at the gates, and held continuations/jokes for "the host is talking")
                return
        now = time.monotonic()
        STATE["utt"][at] = text
        if name_first():
            _note_call(at, text, now)
            STATE["talk_since"] = None
        STATE["topic_cue"] = None                        # (the cue his scripted line named: for the joke by topic)
        STATE["ex_stamp"] = at                           # (free talk: a new utterance = a new exchange for the hall)
        STATE["heard_mono"] = now
        STATE["pending"], STATE["dropped"] = True, False  # a new utterance: her reply to it waits for the decision
        STATE["pick"], STATE["ans"], STATE["ans_n"] = None, "", 0   # (the picker's context is the last utterance's)
        STATE["ans_started"] = None
        for key in sorted(STATE["utt"]):                 # (`at` is monotonic: the same clock as `now`)
            if key <= STATE["spent"] or now - key > BUFFER_S:
                STATE["utt"].pop(key, None)
        while len(STATE["utt"]) > BUFFER_UTTERANCES:
            STATE["utt"].pop(min(STATE["utt"]))
        old = STATE["timer"]
        if old is not None:
            old.cancel()
        try:
            STATE["timer"] = asyncio.get_running_loop().call_later(QUIET_S, _quiet)
        except RuntimeError:
            STATE["timer"] = None
            _decide_sync()                               # (no loop: a script / the plain test path)
    except Exception:                                    # noqa: BLE001 -- the call, never the matcher
        logger.exception("ceremony follow: the host's words")


def _quiet() -> None:
    STATE["timer"] = None
    if name_first() and _host_talking():
        # (owner 24 Nov ~17:40 "ในกรณีที่เขาพูดชื่อ emma แล้วยังพูดก็ยังไม่ตอบรอจนกว่าเขาจะพูดเสร็จ"): his voice went on after the
        # last caption -- nothing is decided until it stops; a segment that never closes is not waited for
        now = time.monotonic()
        STATE["talk_since"] = STATE["talk_since"] or now
        if now - STATE["talk_since"] < HOST_TALK_MAX_S:
            try:
                STATE["timer"] = asyncio.get_running_loop().call_later(POLL_S, _quiet)
                return
            except RuntimeError:
                pass
    STATE["talk_since"] = None
    STATE["task"] = _spawn(_decide())


def _decide_sync() -> None:
    asyncio.run(_decide())


async def _decide() -> None:
    try:
        await _decide_inner()
    finally:
        if STATE["timer"] is None:                       # (a newer caption re-armed the timer: that one decides)
            STATE["pending"] = False
            _exchange_unclaimed()


async def _decide_inner() -> None:
    try:
        if not (STATE["on"] and not STATE["paused"] and settings.ceremony_follow_host):
            return
        texts = [STATE["utt"][k] for k in sorted(STATE["utt"])]
        if not texts:
            return
        ptr = pointer()
        stamp = max(STATE["utt"])
        script_mode = bool(settings.ceremony_script_show_only and not live_first())
        named = script_mode and name_first()
        answer_to_her = None
        called_at = addressed(stamp) if named else None
        if called_at is not None:
            texts = _since_call(called_at)
            from app import ceremony_call

            if ceremony_call.dangling(str(texts[-1])):
                # (he stopped where no question ends -- "what facilities are": the rest decides, with these words)
                STATE["last_eval"] = {"at": time.time(), "verdict": "listening", "why_th": "รอคุณมิกิพูดต่อให้จบคำถาม",
                                      "cue": None, "cover": None, "second": None, "spoke": True}
                turnlog.record("ceremony_follow", action="eval", verdict="listening")
                return
        if named and called_at is None:
            answer_to_her = _reply_cue()
            if answer_to_her is None or ptr["kind"] in ("running", "chain"):
                _not_called(texts)
                return
            found = _elsewhere_match(texts, [], True)
            if found is None or found[0]["cue"] != answer_to_her:
                found = _meaning_match(texts, [], only=answer_to_her)
            if found is None:
                _not_called(texts)
                return
            await _press_found(found, stamp, [], "reply")
            return
        if not ptr["cands"] or (ptr["kind"] not in ("host",)
                                and not (script_mode and ptr["kind"] in ("continues", "manual"))):
            STATE["last_eval"] = {"at": time.time(), "verdict": "blocked", "why_th": ptr["why_th"], "cue": None,
                                  "cover": None, "spoke": True}
            turnlog.record("ceremony_follow", action="eval", verdict="blocked", at=_at(ptr["at"]), kind=ptr["kind"])
            if live_first():
                await _pick_early(texts, ptr["cands"], stamp)    # blocked pointer: she still answers live, the slide follows the script
            if not script_mode or ptr["kind"] in ("running", "chain"):
                if script_mode:
                    _off_script_logo(texts)      # (review 24 Nov: a blocked pointer left the last slide under her answer)
                return
            # (rehearsal 24 Nov 16:46: after scene 9 the order stood at the walk to the globe -- a staff step -- and every
            # question asked again was refused unheard: "พอกลับมาถามซ้ำโมชั่นก็ไม่เปลี่ยน")
            res = {"verdict": "blocked"}
        else:
            res = decide_windows(texts, [c["cue"] for c in ptr["cands"]])
        picked = False
        if res["verdict"] == "no_tail":
            if live_first():
                # owner 24 Nov rehearsal: the slide must be up by her first audio. The tail grace below waits 1.3 s for
                # a shorter version of the line -- the picker needs none of it: on his words, now (once per utterance)
                picked = True
                await _pick_early(texts, ptr["cands"], stamp)
            # he may have said a shorter version of the line (owner 24 Nov): wait a moment more, and if he is still
            # quiet the ending is no longer asked for
            await asyncio.sleep(TAIL_GRACE_S)
            if not (STATE["on"] and not STATE["paused"]) or (STATE["utt"] and max(STATE["utt"]) != stamp):
                return
            res = decide_windows(texts, [c["cue"] for c in ptr["cands"]], final=True)
            res["grace"] = True
        if res["verdict"] != "blocked":
            STATE["last_eval"] = {"at": time.time(), "verdict": res["verdict"], "cue": res["cue"], "cover": res["cover"],
                                  "second": res["second"], "why_th": None, "spoke": True}
            turnlog.record("ceremony_follow", action="eval", verdict=res["verdict"], cue=res["cue"], cover=res["cover"],
                           second=res["second"], matched=res["matched"], tokens=res["tokens"], heard=res["heard"],
                           window=res.get("window"), by=res.get("by"), grace=res.get("grace"))
        found = None
        if script_mode and res["verdict"] == "match":
            # a longer question of elsewhere said whole is the one he asked: at question 1, "I am a customer looking for a
            # room for my family. How would you help?" is question 8 (its "how ... you help" is all of question 1)
            found = _elsewhere_match(texts, [res["cue"]], named)
            if found is not None and found[1]["matched"] <= res["matched"]:
                found = None
        if res["verdict"] != "match" or found is not None:
            if live_first() and not picked:
                await _pick_early(texts, ptr["cands"], stamp)    # script-first: a partial / loose question about a scripted topic
                return
            if not settings.ceremony_script_show_only:
                return
            near = [c["cue"] for c in ptr["cands"]]
            if found is None:
                found = _elsewhere_match(texts, near, named)
            if found is None and named:
                found = _meaning_match(texts, near)
            if found is None and named:
                await _off_script(texts, stamp)
                return
            if found is None:
                _off_script_logo(texts)
                return
            if named:
                found = (_alike(found[0], near), found[1])
            # a question out of order, or one already said asked again (rehearsal 24 Nov 15:26-16:47): its line is played
            # whole -- voice, motion from the start, subtitle -- the staff's own press (the order never goes back, and
            # nothing in between is marked skipped: what was jumped over can still be asked)
            cand, res = found
            skipped = []
            turnlog.record("ceremony_follow", action="extra" if cand.get("extra") else "again" if cand["state"] == "done"
                           else "jump", cue=cand["cue"], by=res.get("by"))
        else:
            cand = next(c for c in ptr["cands"] if c["cue"] == res["cue"])
            if live_first():
                await _show_cue(cand, res, ptr["cands"][:ptr["cands"].index(cand)], stamp)
                return
            # (robot log 24 Nov 11:36: `dropped` was set HERE, before the gate: the press was refused with her own reply
            # streaming = her reply dropped AND no cue = silence. It is set only after the press went ok, below.)
            skipped = [c for c in ptr["cands"][:ptr["cands"].index(cand)]]
        await _press(cand, res, stamp, skipped)
    except Exception:                                    # noqa: BLE001 -- the ceremony goes on by hand
        logger.exception("ceremony follow: deciding")


def _not_called(texts: list) -> None:
    """Her name first: he is not talking to her -- nothing plays, nothing is answered, the screen stays as it is."""
    STATE["may_answer"] = False
    STATE["last_eval"] = {"at": time.time(), "verdict": "not_called", "why_th": "ยังไม่ได้เรียก Emma", "cue": None, "cover": None,
                          "second": None, "spoke": True}
    turnlog.record("ceremony_follow", action="eval", verdict="not_called", heard=len(tokens(str(texts[-1]))))


async def _off_script(texts: list, stamp: float) -> None:
    """He called her and no line answers his words. He asks for something: she answers live, short, nothing on the
    screen, once he has really stopped. He is only telling something: the call holds and she goes on listening."""
    from app import ceremony_call

    last = str(texts[-1])
    asked = _is_request(last) and not ceremony_call.only_attention(last) and not ceremony_call.unfinished(last)
    if asked:
        await asyncio.sleep(OFF_SCRIPT_WAIT_S)
        if not STATE["on"] or STATE["paused"] or (STATE["utt"] and max(STATE["utt"]) != stamp) or _host_talking():
            return                                       # he went on: that is decided with his next words
    STATE["may_answer"], STATE["may_at"] = asked, time.monotonic()
    turnlog.record("ceremony_follow", action="off_script" if asked else "listening")
    if asked:
        STATE["call_spent"] = stamp                      # (the next question is asked with her name again)
        _off_script_logo(texts)
        try:
            from app import ceremony

            await ceremony._clear_adhoc_picture()        # (the story of the answer before is not this one's)
        except Exception:                                # noqa: BLE001 -- the answer, never its screen
            logger.exception("ceremony follow: the last story's picture")


async def _press_found(found, stamp: float, skipped: list, why: str) -> None:
    cand, res = found
    turnlog.record("ceremony_follow", action=why, cue=cand["cue"], by=res.get("by"))
    await _press(cand, res, stamp, skipped)


async def _press(cand: dict, res: dict, stamp: float, skipped: list) -> None:
    """The decided line, through the gates, then the staff's own press (retried once when her turn got in first)."""
    STATE["decision"] += 1
    token = STATE["decision"]
    waited = 0.0
    script_mode = bool(settings.ceremony_script_show_only)
    hold_s = SCRIPT_HOLD_S if script_mode else HOLD_S
    recorded = bool(script_mode and await asyncio.to_thread(_recorded, cand.get("cue")))
    if script_mode:
        STATE["press_wait"] = time.monotonic()
    try:
        while True:
            gate = _gate_why(recorded)
            if gate is None and name_first() and _host_talking() and waited < HOST_TALK_MAX_S:
                gate = "host_talks"
            if gate is None:
                break
            if waited >= hold_s or STATE["decision"] != token or STATE["paused"] or not STATE["on"]:
                _note_fire(cand, res, "host", ok=False, error="held",
                           error_th="ไม่เล่นบท: %s" % GATE_TH.get(gate, gate))
                return
            await asyncio.sleep(POLL_S)
            waited += POLL_S
    finally:
        if script_mode and STATE["decision"] == token:
            STATE["press_wait"] = None
    if STATE["decision"] != token or STATE["paused"] or not STATE["on"] or (STATE["utt"] and max(STATE["utt"]) != stamp):
        return                                       # the host said more meanwhile / a stop: a later decision
    if name_first() and STATE["utt"] and max(STATE["utt"]) != stamp:
        return
    result = await _play(cand, res, "host", skipped)
    if (script_mode and isinstance(result, dict) and not result.get("ok")
            and result.get("error") == "still_speaking" and waited < hold_s):
        # (her turn began between the gate's look and the read's own: wait for it once more, then press again)
        _reopen_window()
        STATE["press_wait"] = time.monotonic()
        try:
            while _gate_why() is not None and waited < hold_s and STATE["decision"] == token and STATE["on"]:
                await asyncio.sleep(POLL_S)
                waited += POLL_S
        finally:
            if STATE["decision"] == token:
                STATE["press_wait"] = None
        if STATE["decision"] == token and STATE["on"] and not STATE["paused"] and _gate_why() is None:
            result = await _play(cand, res, "host", skipped)
    if isinstance(result, dict) and result.get("ok"):
        STATE["dropped"] = True                      # her live reply to these words never plays: the recording does
    else:
        _reopen_window()                             # a refused press closed the window (script_press): reopen it at once


def _reopen_window() -> None:
    """A press that did not go closed the live window for nothing (ceremony.script_press closes it first): without it
    the mic is deaf (`cue_only_closed`) and her in-flight reply is dropped as `cue_only`. Never raises."""
    try:
        from app import ceremony

        if STATE["on"] and not STATE["paused"] and ceremony._adhoc_live(time.time()) is None:
            result = ceremony.adhoc_open(follow=True, quiet=True)
            if result.get("ok"):
                STATE["opened_at"] = time.time()
                STATE["reopens"] += 1
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: reopening the window")


def _at(pair) -> str | None:
    return "%s.%s" % tuple(pair) if pair else None


def _recorded(cue_id: str | None) -> bool:
    """The cue plays a listened-to recording (event_cues: `approved_voice_sha256` = the clip's sha), not a live read.
    Never raises; anything unknown = False (the read's waits stay)."""
    if not cue_id:
        return False
    try:
        from app import event_cues, event_script

        c = (event_script.servable() or {}).get(cue_id)
        if c is None and str(cue_id).startswith("x_"):
            from app import ceremony_extra

            return cue_id in ceremony_extra.servable()   # (offered only with its recording signed)
        return bool(c) and bool(event_cues._voice_status(c).get("approved"))
    except Exception:                                    # noqa: BLE001
        return False


def _gate_why(recorded: bool = False) -> str | None:
    """Why no cue may go now (a GATE_TH key), None = clear. The press has the same gates and refuses on its own; this
    is what lets a decided match wait for her voice instead of being refused."""
    try:
        from app import ceremony, event_cues, event_host, event_order, event_tour, event_walk

        if ceremony.STATE.get("script_presses") or event_order.STATE.get("pressing") is not None:
            return "busy"
        chain = event_order.STATE.get("chain") or {}
        if chain.get("state") == "waiting":
            return "chain"
        if event_tour.ceremony_running():
            return "opening"
        if event_walk.walking() or event_host.robot_moving():
            return "moving"
        if event_cues.line_audible():
            return "speaking"
        session = STATE.get("session")
        if recorded and settings.ceremony_script_show_only and reply_hold() in ("wait", "drop"):
            # A listened-to recording (owner 24 Nov ~15:55 "ที่ตอบช้าแก้แล้วใช่ไหม"): play_recorded purges the model's
            # queued output and keeps a turn of hers that began under the line off the speaker to its end -- her held
            # reply is no reason to wait (the read's 6-9 s: her turn streams at speaking pace). Only while that reply is
            # held/dropped: one already audible is still waited for.
            return None
        if session is not None and event_cues._asks(getattr(session, "provider", None), "model_streaming"):
            return "answering"                           # her own live reply is streaming: a press would be refused over it
        if session is not None and settings.ceremony_script_show_only:
            # the read's own test (event_cues `_read_line.still`, review 7 Nov): a turn of hers in flight with nothing
            # out yet -- the ~1.2 s before her first sound -- refuses the press "still_speaking" too (rehearsal 24 Nov
            # 15:30:08: the press went 1 s after the match and was refused = no line, no slide)
            busy = event_cues._model_busy(session, getattr(session, "provider", None), time.monotonic())
            if busy is not None and busy != "reply_owed":
                return "answering"
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the gates")
        return "unknown"
    return None


def _note_fire(cand: dict, res: dict | None, by: str, *, ok: bool, error: str | None = None,
               error_th: str | None = None) -> None:
    STATE["last_fire"] = {"at": time.time(), "cue": cand["cue"], "n": cand["n"], "i": cand["i"], "by": by, "ok": ok,
                          "cover": (res or {}).get("cover"), "second": (res or {}).get("second"),
                          "error": error, "error_th": error_th}
    turnlog.record("ceremony_follow", action="fire", cue=cand["cue"], by=by, ok=ok, error=error,
                   cover=(res or {}).get("cover"), second=(res or {}).get("second"))


async def _show_cue(cand: dict, res: dict | None, skipped: list, stamp) -> None:
    """Live first (CEREMONY_FOLLOW_CUES off): the host's scripted line was recognised. Nothing is pressed or played and
    her live reply is untouched -- the step is marked done first (the ones he went past, skipped); only when that went
    ok does the cue's slide go up on the hall screen in its settled state (review 24 Nov: the slide used to go first and
    a refused mark left a slide for a step still standing). The slide is sent on the loop (face_mirror's queue is not
    thread-safe); only the file work runs in a thread. The cue's `show` story (a picture on the robot face) is NOT
    shown. No gate: nothing here makes a sound."""
    from app import ceremony_motion, event_order

    if STATE["paused"] or not STATE["on"] or (STATE["utt"] and max(STATE["utt"]) != stamp):
        return                                           # a stop / he said more meanwhile: a later decision
    STATE["spent"] = max(STATE["utt"]) if STATE["utt"] else STATE["spent"]
    STATE["utt"] = {}
    before = STATE["pick"]
    STATE["pick"] = None                                 # (a scripted line: no picker context for her answer)
    heard_at = STATE["heard_mono"]
    up_already = bool(before and before.get("shown") == cand["cue"])   # (the picker put this very slide up already: not again)
    STATE["decision"] += 1
    ceremony_motion.STATE["cue_at"] = stamp              # (the topic matcher leaves this utterance alone from now on)
    slide = await asyncio.to_thread(ceremony_motion.prepare_cue, cand["cue"])
    if free_talk():
        # free talk: no pointer side effects (no mark, no skip, no nudge) -- the slide only, if the hall may take it
        shown = bool(up_already or (slide is not None and claim_hall("cue", stamp)
                                    and ceremony_motion.send_cue(cand["cue"], slide, stamp)))
        STATE["topic_cue"] = cand["cue"]                 # (her answer to his scripted line is about this cue: a joke may follow it)
        _note_fire(cand, res, "live", ok=True)
        turnlog.record("ceremony_follow", action="shown", cue=cand["cue"], slide=bool(shown), ok=True, free=True)
        return
    result = await event_order.mark_shown(cand["n"], cand["i"])
    ok = bool(isinstance(result, dict) and result.get("ok"))
    shown = False
    if ok:
        for sk in skipped:
            await event_order.skip(sk["n"], sk["i"])
        shown = True if up_already else ceremony_motion.send_cue(cand["cue"], slide, stamp)
        _spawn(_nudge(cand["cue"], stamp, heard_at))     # his lead-in matched: if she does not answer, it is her turn
    _note_fire(cand, res, "live", ok=ok, error=None if ok else (result or {}).get("error"),
               error_th=None if ok else (result or {}).get("error_th"))
    turnlog.record("ceremony_follow", action="shown", cue=cand["cue"], slide=bool(shown), ok=ok)


# ---- the script-first slide picker (app/ceremony_pick.py) ------------------------------------------

def script_first() -> bool:
    """Live first AND follow is running on the ceremony call: the script decides the hall screen's slide
    (owner 24 Nov: the old topic motion and a story the script already covers do not also go up -- no clash)."""
    try:
        return bool(live_first() and settings.ceremony_follow_host and STATE["on"] and not STATE["paused"])
    except Exception:                                    # noqa: BLE001
        return False


def script_story_ids() -> set:
    """The stories that are a cue's `show` in the running order (location_beach, unit_count, ...): the script covers
    them, so the hall shows the cue's slide for them instead. Only a cue the picker can actually put up counts
    (review 24 Nov: a cue with no servable slide used to suppress its story and leave the hall blank). Never raises."""
    try:
        from app import ceremony_pick, event_script

        servable = set((ceremony_pick.table() or {}).get("cues") or {})
        return {str(st["show"]) for sc in event_script.order() or [] for st in sc["steps"]
                if st.get("do") == "cue" and st.get("show") and st.get("cue") in servable}
    except Exception:                                    # noqa: BLE001
        return set()


def hall_story_covered(topic) -> bool:
    """face_mirror.view asks for a story's tool result: True = do not put it on the hall (the cue's slide does)."""
    return bool(isinstance(topic, str) and script_first() and topic in script_story_ids())


def _step_of(cue_id: str):
    """(n, i) of the cue's step in the running order, or None."""
    from app import event_order

    for sc in event_order._scenes() or []:
        for st in sc["steps"]:
            if st.get("do") == "cue" and st.get("cue") == cue_id:
                return sc["n"], st["i"]
    return None


async def _pick_early(texts: list, cands: list, stamp) -> None:
    """The host spoke and no scripted line matched (or the pointer is blocked): pick the slide from the script on his
    words alone. Once per utterance end. The context is kept for her answer (`_refine`)."""
    from app import ceremony_pick

    if not live_first() or STATE["paused"] or not STATE["on"]:
        return
    text = " ".join(texts)
    near = [c["cue"] for c in cands]
    res = await asyncio.to_thread(ceremony_pick.pick, text, None, set(near))
    if STATE["paused"] or not STATE["on"] or (STATE["utt"] and max(STATE["utt"]) != stamp):
        return                                           # a stop / he said more meanwhile
    ctx = {"stamp": stamp, "text": text, "near": near, "cands": list(cands), "shown": None, "r8": False, "rfin": False}
    STATE["pick"], STATE["ans"], STATE["ans_n"] = ctx, "", 0
    turnlog.record("ceremony_follow", action="pick", stage="early", verdict=res["verdict"], cue=res["cue"],
                   score=res["score"], second=res["second"], why=res.get("why"))
    if res["verdict"] == "pick":
        await _show_pick(ctx, res)


async def _show_pick(ctx: dict, res: dict) -> None:
    """Put the picked cue's slide up. A STRONG pick that is one of the next cues also marks its step shown (the ones
    in front of it skipped, as a scripted line does); a topic-level / far pick only shows the slide -- it never moves
    the pointer, so the steps in between stay for staff. Marking first; a refused mark = no slide (a crossed slide)."""
    from app import ceremony_motion, event_order

    cue = res["cue"]
    if ctx["shown"] == cue:
        return
    ceremony_motion.STATE["cue_at"] = ctx["stamp"]
    heard_at = STATE["heard_mono"]                       # (the utterance this pick answers: the nudge is only for it)
    slide = await asyncio.to_thread(ceremony_motion.prepare_cue, cue)
    if STATE["pick"] is not ctx or STATE["paused"] or not STATE["on"]:
        return                                           # a newer utterance / a stop meanwhile
    free = free_talk()
    mark_ok = True
    if res["strong"] and not free:
        order = [c["cue"] for c in ctx["cands"]]
        if cue in order:
            cand = ctx["cands"][order.index(cue)]
            result = await event_order.mark_shown(cand["n"], cand["i"])
            mark_ok = bool(isinstance(result, dict) and result.get("ok"))
            if mark_ok:
                for sk in ctx["cands"][:order.index(cue)]:
                    await event_order.skip(sk["n"], sk["i"])
    if not mark_ok:
        return
    if STATE["pick"] is not ctx or STATE["paused"] or not STATE["on"]:
        return                                           # (he spoke / a stop during the mark's awaits: no stale slide)
    if free and (slide is None or not claim_hall("cue", ctx["stamp"])):
        return                                           # (free talk: nothing servable, or the exchange's motion is a cue's already)
    if ceremony_motion.send_cue(cue, slide, ctx["stamp"]):
        ctx["shown"] = cue
        if res["strong"] and not free:
            _spawn(_nudge(cue, ctx["stamp"], heard_at))
        turnlog.record("ceremony_follow", action="pick_shown", cue=cue, stage=res["stage"], strong=res["strong"])


#: His lead-in matched a cue and he has finished: she gets this long to start the answer by herself, then ONE server
#: text turn tells her it is her turn (owner 24 Nov, "บางทีเขาอาจจะอธิบายแล้วจบโดยไม่มีคำถาม"). Fixed words + the cue id
#: only -- never anything he said. English, short, from the script (the script notes of app/ceremony.py say how).
NUDGE_AFTER_S = 1.5
NUDGE_TEXT = ("[คิวบทพิธี — ระบบ ไม่ใช่คำพูดของแขก] The host has finished his lead-in to script cue %s and has not asked a "
              "question. It is your turn: give that cue's line from your script, in English, short (one to three "
              "sentences). Say nothing else and call no tool.")


def _she_started() -> bool:
    if STATE["ans_started"] is not None:
        return True
    return _gate_why() in ("answering", "speaking")


def _host_speaking() -> bool:
    """The host is talking right now: the ceremony call's VAD gate has a segment open (a bool read, no per-frame cost).
    The caption of a finished utterance lands ~CEREMONY_LIVE_END_SILENCE_MS after he stops, so `heard_mono` alone
    cannot see a second utterance that began inside the nudge delay (review 24 Nov). Never raises; no gate = False."""
    try:
        session = STATE.get("session")
        gate = getattr(getattr(session, "provider", None), "_vad_gate", None)
        return bool(gate is not None and getattr(gate, "in_speech", False))
    except Exception:                                    # noqa: BLE001
        return False


def _window_open() -> bool:
    try:
        from app import ceremony

        return ceremony._adhoc_live(time.time()) is not None
    except Exception:                                    # noqa: BLE001
        return False


def _heard_unchanged(heard_at) -> bool:
    return STATE["heard_mono"] == heard_at


def _gate_clear() -> bool:
    return _gate_why() is None


def _nudge_relevant(heard_at) -> bool:
    """Everything that must still hold for 'it is her turn' to go out: follow running and not paused, the live window
    open, she has not started, no new caption since, the host is not speaking, and none of the press gates is up
    (busy / chain / opening / moving / speaking / answering)."""
    return bool(script_first() and _window_open() and not _she_started() and _heard_unchanged(heard_at)
                and not _host_speaking() and _gate_clear())


async def _nudge(cue: str, stamp, heard_at) -> None:
    """One server text turn for this match, only when she has not begun to answer within NUDGE_AFTER_S and he has not
    spoken again. Never raises; at most one per (cue, utterance)."""
    try:
        if not script_first() or free_talk():            # (free talk: nobody nudges her -- she answers when he asks)
            return
        done = STATE["nudged"]
        if done is None:
            done = STATE["nudged"] = set()
        if (cue, stamp) in done:
            return
        done.add((cue, stamp))
        await asyncio.sleep(NUDGE_AFTER_S)

        def relevant() -> bool:
            return _nudge_relevant(heard_at)

        if not relevant():
            return
        from app import events

        sent = await events.announce(NUDGE_TEXT % cue, source="follow_nudge", still_relevant=relevant,
                                     held_language=False)
        turnlog.record("ceremony_follow", action="nudge", cue=cue, sent=bool(sent))
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the nudge")


def on_answer_words(session, text) -> None:
    """Her live answer's output transcription, chunk by chunk (session's `assistant_transcript`): kept in memory for the
    picker only. After REFINE_WORDS content words the pick is refined once. Cheap; never raises."""
    try:
        if _pressed_line():
            return                                       # (a staff-pressed line Gemini reads is not an answer to the host: review B)
        if (text and text.strip() and STATE["on"] and STATE["ans_started"] is None and live_first()
                and session is STATE["session"]):
            STATE["ans_started"] = time.monotonic()      # she has started to answer: no nudge for this one
        ctx = STATE["pick"]
        if ctx is None or not text or not live_first() or STATE["paused"] or session is not STATE["session"]:
            return
        STATE["ans"] = (STATE["ans"] + text)[-1500:]
        STATE["ans_n"] += len(re.findall(r"[A-Za-z]{3,}|[\u0e00-\u0e7f]{2,}", text))
        from app import ceremony_pick

        if STATE["ans_n"] >= ceremony_pick.REFINE_WORDS and not ctx["r8"]:
            ctx["r8"] = True
            _spawn(_refine(ctx))
    except Exception:                                    # noqa: BLE001 -- the call, never the picker
        logger.exception("ceremony follow: her answer's words")


def on_answer_done(session) -> None:
    """Her turn ended (turn_complete): the last refinement, with the whole answer. Never raises."""
    try:
        if _pressed_line():
            return
        if session is STATE["session"]:
            STATE["answer_done_at"] = time.monotonic()   # (the jokes wait for this after an answer she began)
        ctx = STATE["pick"]
        if (ctx is None and STATE["topic_cue"] and STATE["ans_started"] is not None and free_talk()
                and session is STATE["session"]):
            cue, STATE["topic_cue"] = STATE["topic_cue"], None
            _arm_topic_joke(cue)                         # (his scripted line matched: her answer was about that cue)
            return
        if ctx is None or ctx["rfin"] or not live_first() or STATE["paused"] or session is not STATE["session"]:
            return
        if STATE["ans_n"] < 3:
            return                                       # (nothing said: an empty turn, a tool call's gap)
        ctx["rfin"] = True
        _spawn(_refine(ctx, final=True))
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: her answer's end")


async def _refine(ctx: dict, final: bool = False) -> None:
    """Score the host's words + her answer so far; switch to a different cue only when it is clearly ahead."""
    try:
        from app import ceremony_pick

        answer = STATE["ans"]
        res = await asyncio.to_thread(ceremony_pick.pick, ctx["text"], answer, set(ctx["near"]))
        if STATE["pick"] is not ctx or STATE["paused"] or not STATE["on"]:
            return
        turnlog.record("ceremony_follow", action="pick", stage="final" if final else "refine", verdict=res["verdict"],
                       cue=res["cue"], score=res["score"], second=res["second"], why=res.get("why"),
                       words=STATE["ans_n"], changed=bool(res["verdict"] == "pick" and res["cue"] != ctx["shown"]))
        if res["verdict"] == "pick" and res["cue"] != ctx["shown"]:
            await _show_pick(ctx, res)
        if final:
            _arm_topic_joke(res["cue"] if res["verdict"] == "pick" else ctx["shown"])
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: refining the pick")


async def _play(cand: dict, res: dict | None, by: str, skipped: list | None = None) -> dict:
    """The press: the staff's own path (the window closes first, her picture comes down, the screen's topic slide
    too), then the order's press -- every gate of it."""
    from app import ceremony, event_order

    if STATE["utt"]:
        STATE["spent"] = max(STATE["utt"])
    STATE["utt"] = {}
    if by == "host":
        _preempt_live_reply()
    try:
        async with ceremony.script_press("script"):
            if cand.get("extra"):
                from app import ceremony_extra, event_cues

                result = await event_cues.play(cand["cue"], by="staff", show=ceremony_extra.topic_of(cand["cue"]))
            else:
                result = await event_order.press(cand["n"], cand["i"])
    finally:
        _preempt_live_reply(False)
    ok = bool(isinstance(result, dict) and result.get("ok"))
    _note_fire(cand, res, by, ok=ok, error=None if ok else (result or {}).get("error"),
               error_th=None if ok else (result or {}).get("error_th"))
    if ok:
        for sk in skipped or []:
            await event_order.skip(sk["n"], sk["i"])     # the host went past them: staff would skip them too
    return result


def _preempt_live_reply(on: bool = True) -> None:
    """The host's scripted line was matched: a live reply of hers that is already out (it began before the decision,
    e.g. a late caption) is cut by the press instead of waited for (event_cues `_until_quiet`, was up to 15 s)."""
    try:
        from app import event_cues

        event_cues.STATE["preempt_live_until"] = time.monotonic() + 10.0 if on else 0.0
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: preempting a live reply")


# ---- the jokes that play by themselves (CEREMONY_JOKES_AUTO, owner 24 Nov) -----------------------------

#: After the cue before the joke was taken with no answer of hers to wait for (a staff press, or she has not begun),
#: this long before the set-up (the nudge gets its chance first).
JOKE_SETTLE_S = 3.0
#: The host must have been quiet this long (the VAD's segment closed and no caption since).
JOKE_HOST_QUIET_S = 1.2
#: Ready but the host keeps talking: after this long staff are told they can press it.
JOKE_GIVE_UP_S = 20.0
JOKE_STAFF_TH = "มุกรอจังหวะ — กดเองได้"


def answer_in_two(follower_cue) -> bool:
    """The line chained on behind `follower_cue`'s lead is the rest of her own answer (question 1, question 9: owner
    24 Nov "จะอยู่ในคำถามที่ 9 ต่อเลย"), not a joke's punchline: the host file says it `continues` the line before it."""
    return (lines().get(follower_cue) or {}).get("kind") == "continues"


def _joke_pairs() -> list:
    """[(prev (n, i), set-up (n, i), cue id)]: the step just before each `chain` set-up, when that is a cue too."""
    from app import event_order

    out = []
    for sc in event_order._scenes() or []:
        steps = sc["steps"]
        for k, st in enumerate(steps):
            if (st.get("chain") and st.get("do") == "cue" and k > 0 and steps[k - 1].get("do") == "cue"
                    and not steps[k - 1].get("chain")
                    and not (k + 1 < len(steps) and answer_in_two(steps[k + 1].get("cue")))):
                out.append(((sc["n"], steps[k - 1]["i"]), (sc["n"], st["i"]), st["cue"]))
    return out


def _joke_topics() -> dict:
    """{cue id of the step before a chained joke's set-up: ((n, i) of the set-up, the set-up's cue id)}."""
    from app import event_order

    out = {}
    for sc in event_order._scenes() or []:
        steps = sc["steps"]
        for k, st in enumerate(steps):
            if (st.get("chain") and st.get("do") == "cue" and k > 0 and steps[k - 1].get("do") == "cue"
                    and not steps[k - 1].get("chain")
                    and not (k + 1 < len(steps) and answer_in_two(steps[k + 1].get("cue")))):
                out[steps[k - 1]["cue"]] = ((sc["n"], st["i"]), st["cue"])
    return out


def _arm_topic_joke(topic_cue) -> None:
    """Free talk, owner 24 Nov ~12:50 ("ตอบตอนคุณมิกิพูดถึงเรื่องบทนั้น แล้วตอนหุ่นตอบก็ให้หุ่นพูดมุขต่อเลย"): her answer's final
    pick is the cue just before a joke -> that joke is armed; `_joke_tick` presses its set-up once her voice is out and
    the host is quiet (the staff-press path: the chain plays the punchline, the slide comes with the press). Once per joke
    per ceremony run. Never raises."""
    try:
        if not (free_talk() and settings.ceremony_jokes_auto and topic_cue):
            return
        from app import event_order

        hit = _joke_topics().get(topic_cue)
        if hit is None:
            return
        setup, cue = hit
        done = STATE["joke_done"]
        if done is None:
            done = STATE["joke_done"] = set()
        j = STATE["joke"]
        if setup in done or event_order.STATE["marks"].get(event_order._key(*setup)) is not None:
            return
        if j is not None and j["state"] != "staff":
            return                                       # (one at a time; a given-up one does not hold the next)
        STATE["joke"] = {"setup": setup, "cue": cue, "since": time.monotonic(), "last": event_order.STATE["last"],
                         "ready_since": None, "state": "wait"}
        turnlog.record("ceremony_follow", action="joke", stage="armed", cue=cue, by="topic")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: arming a joke by topic")


def _joke_cancel(why: str) -> None:
    j = STATE["joke"]
    if j is not None:
        turnlog.record("ceremony_follow", action="joke", stage="cancel", cue=j["cue"], why=why)
    STATE["joke"] = None


async def _joke_tick(now_m: float) -> None:
    """Live first only: when the step before a joke's set-up has been taken (the picker's strong pick, or a staff
    press), her live answer to it is over and the host has been quiet, press the set-up once through the staff press
    path (`ceremony.script_press` + `event_order.press`): the order's chain rule plays the punchline after it. Never
    raises. The set-up is never pressed over the host: after JOKE_GIVE_UP_S staff are told, and it is theirs."""
    try:
        if not (live_first() and settings.ceremony_jokes_auto and STATE["on"] and not STATE["paused"]):
            if STATE["joke"] is not None:
                _joke_cancel("off" if STATE["on"] and not STATE["paused"] else "stopped")
            return
        from app import ceremony, event_order

        marks = event_order.STATE["marks"]
        done = STATE["joke_done"]
        if done is None:
            done = STATE["joke_done"] = set()
        j = STATE["joke"]
        if j is not None:
            if marks.get(event_order._key(*j["setup"])) is not None:
                return _joke_cancel("taken")             # (staff pressed / skipped it: it is theirs)
            if event_order.STATE["last"] != j["last"]:
                return _joke_cancel("order_moved")       # (staff pressed anything else, or the order moved past)
        elif free_talk():
            return                                       # free talk: a joke is armed by her answer's topic (`_arm_topic_joke`), not by the pointer
        else:
            last = tuple(event_order.STATE["last"] or ())
            for prev, setup, cue in _joke_pairs():
                if setup in done or last != prev or marks.get(event_order._key(*setup)) is not None:
                    continue
                if (marks.get(event_order._key(*prev)) or {}).get("state") != "done":
                    continue
                STATE["joke"] = j = {"setup": setup, "cue": cue, "since": now_m, "last": event_order.STATE["last"],
                                     "ready_since": None, "state": "wait"}
                turnlog.record("ceremony_follow", action="joke", stage="armed", cue=cue)
                break
            if j is None:
                return
        if j["state"] != "wait":
            return
        started = STATE["ans_started"]
        if started is not None:
            answered = STATE["answer_done_at"] is not None and STATE["answer_done_at"] >= started
        else:
            answered = now_m - j["since"] >= JOKE_SETTLE_S
        if not (answered and _gate_why() is None and not _voice_playing()):
            j["ready_since"] = None                      # (her answer / a gate: the host's clock starts when they clear)
            return
        if j["ready_since"] is None:
            j["ready_since"] = now_m
        quiet = (not _host_speaking()
                 and (STATE["heard_mono"] is None or now_m - STATE["heard_mono"] >= JOKE_HOST_QUIET_S)
                 and STATE["timer"] is None)
        if not quiet:
            if now_m - j["ready_since"] >= JOKE_GIVE_UP_S:
                j["state"] = "staff"                     # shown on the staff page; nothing more from us
                turnlog.record("ceremony_follow", action="joke", stage="staff", cue=j["cue"])
            return
        j["state"] = "pressing"
        done.add(j["setup"])
        cand = {"n": j["setup"][0], "i": j["setup"][1], "cue": j["cue"]}
        async with ceremony.script_press("script"):
            result = await event_order.press(cand["n"], cand["i"])
        ok = bool(isinstance(result, dict) and result.get("ok"))
        _note_fire(cand, None, "joke", ok=ok, error=None if ok else (result or {}).get("error"),
                   error_th=None if ok else (result or {}).get("error_th"))
        if ok:
            STATE["joke"] = None                         # (its punchline follows by the order's chain rule)
        else:
            j["state"] = "staff"                         # refused: staff press it when it is time
    except asyncio.CancelledError:
        raise
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony follow: the joke")


# ---- the keeper: the window, the continuations ----------------------------------------------

async def _keep() -> None:
    # One failing tick must not end the keeper (review 24 Nov: the try wrapped the whole loop, so one exception
    # left STATE["on"] True, the page saying "on", and the window never reopened -- the mic deaf after the next
    # press). Each tick is caught on its own; only a cancel or follow ending stops the loop.
    while STATE["on"]:
        await asyncio.sleep(KEEP_S)
        if not STATE["on"]:
            return
        try:
            from app import ceremony as _cer

            if _cer.STATE.get("handoff") is not None:
                stop("handoff")                          # the order's last step opens the Event app: follow ends
                return
            if not active():
                # the call may be re-dialling (the network): wait for it, as long as APP_GONE_S
                if STATE["lost_at"] is None:
                    STATE["lost_at"] = time.monotonic()
                if time.monotonic() - STATE["lost_at"] > APP_GONE_S and not (live_first() and settings.ceremony_free_talk):
                    stop("app_closed")                   # (free talk: the next ceremony call is adopted -- `active()` -- and the keeper reopens its window)
                    return
                continue
            await _tick()
        except asyncio.CancelledError:
            raise
        except Exception:                                # noqa: BLE001
            logger.exception("ceremony follow: one keeper tick")


async def _tick() -> None:
    from app import ceremony, event_cues

    now_m = time.monotonic()
    try:
        audible = bool(event_cues.line_audible())
    except Exception:                                    # noqa: BLE001
        audible = True
    if audible:
        STATE["quiet_since"] = None
    elif STATE["quiet_since"] is None:
        STATE["quiet_since"] = now_m
    if STATE["paused"]:
        return
    live = ceremony._adhoc_live(time.time()) is not None
    if not live:
        closed = ceremony.STATE.get("adhoc_closed")
        if (closed is not None and STATE["opened_at"] is not None and closed["at"] >= STATE["opened_at"] - 0.5
                and closed["at"] > STATE["closed_seen"]):
            STATE["closed_seen"] = closed["at"]          # (a closure is read once: resume must not be paused by it again)
            if closed["why"] in END_CLOSES:
                stop("reset")
                return
            if closed["why"] in PAUSE_CLOSES:
                pause("stop" if closed["why"] != "staff" else "staff")
                return
    # A `continues` cue is looked at while the window is open too (review 24 Nov: the open window returned first,
    # the keeper reopened it on the first quiet tick, and from then on the continuation was never looked at --
    # s02_l05 / s05_l08 never played). The press closes the window itself (ceremony.script_press).
    if await _maybe_continue(now_m):
        return
    await _joke_tick(now_m)
    await _free_tick(now_m)
    if ceremony.STATE.get("script_presses"):
        return                                           # (the joke's press is on its way: it closed the window itself)
    if live:
        return
    from app import event_order

    if (event_order.STATE.get("chain") or {}).get("state") == "waiting":
        # Two lines said as one (the jokes, question 1's answer): no window to talk in between them (owner 24 Nov ~15:45
        # "พยายามให้มันเป็นบทเดียวกันเลย ไม่งั้นมันจะเปิดให้พูด"). The chain's own watcher ends that state, bounded.
        return
    result = ceremony.adhoc_open(follow=True, quiet=STATE["reopens"] > 0)
    if result.get("ok"):
        STATE["opened_at"] = time.time()
        STATE["reopens"] += 1


async def _maybe_continue(now_m: float) -> bool:
    """A cue that continues the one before it by script is played once her last line has been over for a moment and
    the host is silent. A refusal is not retried until the pointer moves (staff press it)."""
    from app import event_order

    if live_first():
        return False                                     # live first: nothing is played for her, a continuation included
    ptr = pointer()
    if ptr["kind"] != "continues":
        return False
    key = tuple(ptr["at"])
    if STATE["cont_failed"] == key:
        return False
    qs = STATE["quiet_since"]
    if qs is None or now_m - qs < CONTINUE_DELAY_S:
        return False
    if STATE["heard_mono"] is not None and now_m - STATE["heard_mono"] < CONTINUE_HOST_QUIET_S:
        return False
    scenes = event_order._scenes() or []
    flat = event_order._flat(scenes)
    k = flat.index(key) if key in flat else 0
    # the line before it, looking past only the empty cues the server itself skipped just below (a step STAFF
    # skipped is still no reason to go on)
    prev, j = None, k - 1
    while j >= 0:
        m = event_order.STATE["marks"].get(event_order._key(*flat[j]))
        if m is not None and m.get("state") == "skipped" and tuple(flat[j]) in STATE["auto_skipped"]:
            j -= 1
            continue
        prev = m
        break
    if not prev or prev.get("state") != "done" or time.time() - float(prev.get("at") or 0) > CONTINUE_WITHIN_S:
        return False
    # ...and it IS the line just said: another one played since (an earlier question asked again, 24 Nov) = the moment is
    # gone, "would you like a quotation for this unit" does not follow the answer about the location (review 24 Nov)
    for at in flat:
        m = event_order.STATE["marks"].get(event_order._key(*at))
        if (m is not None and m is not prev and m.get("state") in ("done", "running")
                and (event_order._find(scenes, *at) or {}).get("do") == "cue"
                and float(m.get("at") or 0) > float(prev.get("at") or 0)):
            return False
    if _gate_why() is not None:
        return False
    # A continuing cue that cannot be said (review 24 Nov: s05_l07, the demo room, is an empty unsigned draft): the
    # server skips it the way staff would ("ข้ามขั้นนี้"), so the line after it is not held behind it. Signed later
    # (the sales team's room number) = it is servable and plays like the others.
    try:
        from app import event_script
        sayable = ptr["cue"] in event_script.servable()
    except Exception:                                    # noqa: BLE001 -- unknown: try the press, it has every gate
        sayable = True
    if not sayable:
        res = await event_order.skip(*key)
        logger.info("ceremony follow: skipped %s (not servable) ok=%s", ptr["cue"], (res or {}).get("ok"))
        if isinstance(res, dict) and res.get("ok"):
            STATE["auto_skipped"].add(key)
        else:
            STATE["cont_failed"] = key
        return True
    cand = {"n": key[0], "i": key[1], "cue": ptr["cue"]}
    result = await _play(cand, None, "continue")
    if not (isinstance(result, dict) and result.get("ok")):
        STATE["cont_failed"] = key
    return True


def _card2_taken() -> bool:
    """Card 2 was pressed in this order (its mark is done): the page's own "เปิดโหมดสด" is for a restart after that."""
    from app import event_order

    for sc in event_order._scenes() or []:
        for st in sc["steps"]:
            if st.get("do") == "follow":
                return (event_order.STATE["marks"].get(event_order._key(sc["n"], st["i"])) or {}).get("state") == "done"
    return False


# ---- the staff page -----------------------------------------------------------------------

def snapshot() -> dict:
    """For GET /ceremony/state `follow`. The EXPECTED host line comes from the script data; what the host really said
    never appears here -- only counts and the cue ids."""
    from app import ceremony

    out = {"enabled": bool(settings.ceremony_follow_host)}
    if not out["enabled"]:
        return out
    on = active()
    out.update(on=on, paused=STATE["paused"] if on else None,
               paused_th=PAUSED_TH.get(STATE["paused"] or "", "พักโหมดสด") if on and STATE["paused"] else None,
               since=STATE["since"] if on else None, server_time=time.time())
    live = None
    try:
        live = ceremony._adhoc_live(time.time())
    except Exception:                                    # noqa: BLE001
        pass
    out["window_open"] = live is not None
    out["answer_all"] = bool(settings.ceremony_follow_answer_all)
    out["live_first"] = live_first()
    out["live_first_th"] = "ตอบสดเป็นหลัก — บทกดเองเป็นทางสำรอง" if live_first() else None
    out["free_talk"] = bool(live_first() and settings.ceremony_free_talk)
    out["free_talk_th"] = ("คุยสด — ภาพตามเรื่องที่คุย · บท/มุกกดเองเป็นทางสำรอง"
                           if out["free_talk"] else None)
    if out["free_talk"]:
        out["live_first_th"] = out["free_talk_th"]         # (the staff page prints this one: ceremony-control.html)
    out["redials"] = STATE["redials"]
    out["redialed_at"] = STATE["redialed_at"] if on else None
    out["redial_th"] = ("สายพิธีต่อใหม่แล้ว (%d ครั้ง) — โหมดสดยังทำงานต่อ ตำแหน่งบทเดิม" % STATE["redials"]
                        if on and STATE["redials"] and STATE["redialed_at"]
                        and time.time() - STATE["redialed_at"] < 60 else None)
    out["gone_th"] = ("สายพิธีหาย — รอต่อใหม่ (โหมดสดจะหยุดเองถ้าหายเกิน %d วินาที)" % APP_GONE_S
                      if STATE["on"] and not on and STATE["lost_at"] is not None else None)
    try:
        from app import event_host

        out["mic_muted"] = bool(event_host._mic_closed())
    except Exception:                                    # noqa: BLE001
        out["mic_muted"] = None
    out["mic_th"] = ("ไมค์ปิดอยู่ที่หน้าหุ่น — Emma ไม่ได้ยินคุณมิกิ (แตะไมค์ที่จอหุ่นแล้วเปิดไมค์)"
                     if out["mic_muted"] else None)
    out["can_start"] = (not on) and ceremony.on() and start_refusal() is None and _card2_taken()
    cov = coverage()
    out["manual_cues"] = cov["manual"]
    out["hostless"] = len(cov["manual"]) + len(cov["continues"]) + len(cov["chain_follow"])
    out["with_host"] = len(cov["host"])
    out["cues"] = cov["cues"]
    generic = set(generic_cues("any"))
    out["generic_cues"] = sorted(generic)
    if on:
        ptr = pointer()
        out["next"] = {"kind": ptr["kind"], "at": list(ptr["at"]) if ptr["at"] else None, "label": ptr["label"],
                       "why_th": ptr["why_th"],
                       "expect": [{"n": c["n"], "i": c["i"], "cue": c["cue"], "host_th": c["host_th"],
                                   "generic": c["cue"] in generic} for c in ptr["cands"]]}
        out["gate_th"] = GATE_TH.get(_gate_why() or "")
        joke = STATE["joke"]
        out["joke_th"] = JOKE_STAFF_TH if joke is not None and joke["state"] == "staff" else None
        # He spoke and nothing matched (owner 24 Nov): the step staff would press next stands out on the page, so a
        # hand can fire it at once. Not after a cue fired since, not for long, not while paused.
        spoke, shot = STATE["last_eval"], STATE["last_fire"]
        if (not STATE["paused"] and spoke and spoke.get("spoke") and spoke["verdict"] != "match" and ptr["at"]
                and ptr["kind"] in ("host", "manual", "continues", "gate")
                and time.time() - spoke["at"] <= NUDGE_S and not (shot and shot["at"] >= spoke["at"])):
            out["nudge"] = {"n": ptr["at"][0], "i": ptr["at"][1], "at": spoke["at"]}
    fire, evaluation = STATE["last_fire"], STATE["last_eval"]
    out["last_fire"] = None if fire is None else {k: fire[k] for k in ("at", "cue", "n", "i", "by", "ok", "cover", "second",
                                                                       "error_th")}
    out["last_eval"] = None if evaluation is None else {k: evaluation.get(k) for k in ("at", "verdict", "cue", "cover",
                                                                                       "why_th")}
    return out
