"""A motion for what the ceremony's script does not answer (CEREMONY_OFFSCRIPT_STORIES, owner 24 Nov 2026 ~18:10).

"บางคำถามที่ในบทเราไม่มีโมชั่น ก้ไปเอาโมชั่นมาได้ไหม เราจะได้เพิ่มขอบเขตการถามด้วย ... ให้เอเจนท์เช็คด้วยอันไหนตรงกับบท ก็ไม่ต้องเอามา"

The approved motion stories (app/stories.py) whose questions the script does NOT answer are offered to the ceremony's call:
Khun Miki calls her, asks one of them, she calls present_story and says its approved English lines while the hall and her
face tell it in pictures, with the Thai lines as the hall's subtitle. The list is
`data/ceremony/offscript_stories.json` -- every story the script covers (or that talks price, or is too long for a stage)
is named there with the reason, and is refused.

The order of things is the script's: a question the script answers plays its cue, and the story the model may have asked
for in the same breath never reaches a screen -- its picture waits with her held reply (NoiseReplyGate) and goes out only
with it. Off (the default) = no story in the ceremony, as before.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.ceremony_stories")

FILE = Path(__file__).resolve().parents[1] / "data" / "ceremony" / "offscript_stories.json"
FORMAT = "emma-ceremony-offscript-stories-v1"
#: The hall's subtitle of a told story is up this long at the most (its page takes it down with her voice).
SUB_MAX_S = 90.0

#: What the model is told of the tool in the ceremony's call (English only: the ceremony is).
DESCRIPTION = (
    "Tells one approved topic of the project with motion pictures on the screens while you speak. Call it BEFORE you answer "
    "whenever the host asks you, by name, about one of the topics below and it is not a line of the ceremony script. Then say "
    "the lines in `say`, in English, word for word, nothing before them and nothing after. Never call it for a line of the "
    "script, for a greeting, or for small talk. Topics: ")
INSTRUCTION = ("Say the lines in `say` one after another, in English, word for word. Nothing before them, nothing after: no "
               "question, no invitation, no word about the screen. Figures only as the lines give them.")
#: The line added to the ceremony's script rules.
NOTE_EN = ("When his question is not a line of the script but is a topic of present_story, call present_story first and say its "
           "lines; that answer may be as long as its lines. Every other question off the script: 1-2 short sentences, no tool.")
_STOCK: dict = {}


def in_ceremony() -> bool:
    """This call is the ceremony app's. Never raises."""
    try:
        from app.tools.registry import session_profile

        return (session_profile.get() or settings.assistant_profile) == "ceremony"
    except Exception:                                    # noqa: BLE001
        return False


def listed() -> frozenset:
    """The topics the file offers; empty = no file / unreadable."""
    try:
        stat = FILE.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        if _STOCK.get("key") != key:
            doc = json.loads(FILE.read_text(encoding="utf-8"))
            topics = doc.get("topics") if isinstance(doc, dict) and doc.get("format") == FORMAT else None
            _STOCK.update(key=key, topics=frozenset(t for t in topics or () if isinstance(t, str)))
        return _STOCK["topics"]
    except (OSError, ValueError, KeyError):
        logger.warning("the off-script stories file is unreadable", exc_info=True)
        return frozenset()


def topics() -> dict:
    """id -> story, for the ceremony's call: listed here AND approved and complete (app/stories.available)."""
    from app import stories

    if not settings.stories_enabled:
        return {}
    keep = listed()
    return {sid: story for sid, story in stories.available().items() if sid in keep}


def on() -> bool:
    if settings.ceremony_extra_lines:
        # (the same stories as lines the server plays: the tool is only the fallback while none of them is signed)
        from app import ceremony_extra

        if ceremony_extra.on() and ceremony_extra.servable():
            return False
    return bool(settings.ceremony_offscript_stories and settings.ceremony_script_show_only
                and settings.ceremony_name_first and settings.stories_enabled)


def offered() -> bool:
    try:
        return on() and bool(topics())
    except Exception:                                    # noqa: BLE001 -- a broken catalog offers nothing
        logger.exception("the off-script stories")
        return False


def declare():
    """(description, parameters) of present_story in the ceremony's call."""
    have = topics()
    rows = []
    for sid in sorted(have):
        asks = [q for q in (have[sid].get("questions_en") or [])[:4] if isinstance(q, str)]
        rows.append("%s = %s" % (sid, " / ".join(asks)) if asks else sid)
    return DESCRIPTION + "; ".join(rows), {
        "type": "object",
        "properties": {"topic": {"type": "string", "enum": sorted(have), "description": "the topic to tell"}},
        "required": ["topic"]}


def tool_result(topic: object) -> dict:
    """present_story in the ceremony: the lines to say, in English. The picture is not in it -- it goes out with her
    reply (`picture_event`), or not at all."""
    have = topics() if on() else {}
    story = have.get(topic) if isinstance(topic, str) else None
    if story is None:
        return {"ok": False, "error": "unknown_story",
                "instruction": "This topic has no pictures here. Answer in 1-2 short English sentences from what you know; "
                               "do not call present_story again."}
    return {"ok": True, "topic": story["id"], "say": [line["en"] for line in story["lines"]], "instruction": INSTRUCTION}


def picture_event(topic: object) -> dict | None:
    """The `story_picture` data of a told story: the topic, and the hall's Thai line of it."""
    story = topics().get(topic) if isinstance(topic, str) else None
    if story is None:
        return None
    th = " ".join(" ".join((line.get("th") or "").split()) for line in story["lines"]).strip()
    en = " ".join(" ".join((line.get("en") or "").split()) for line in story["lines"]).strip()
    from app import event_cues

    seconds = min(SUB_MAX_S, max(3.0, len(en) / event_cues.EN_CHARS_PER_S))
    return {"topic": story["id"], "cue": None, "by": "offscript",
            "sub": {"id": ("story-" + story["id"])[:64], "text": th, "seconds": seconds}}


def reset() -> None:
    _STOCK.clear()
