"""`say_script_line` -- the launch ceremony's approved lines, picked by the live model.

Owner 6 Nov 2026, with the ceremony script (คุณมิกิ asks, Emma answers, nine scenes):
"ตอนนี้มีสคริปต์แล้ว ยังส่งให้ Gemini ตอบ หรือให้ตอบจากสคริปต์ อันไหนดีกว่า วัดดู". This is
one of the ways that measurement compares (scripts/eval_live_qa.py --suite ceremony
--ceremony-mode tool): the host just talks, the model only recognises WHICH script
question was asked -- paraphrased, or without her name -- and names the cue. The words
are never the model's: the `show_unit(room)` rule, a pick from a list the server owns.

Visible (registry.allowed) only with EVENT_SCRIPT_TOOL on, `ceremony` named in
TOOL_GROUPS (opt-in), in the ceremony profile (the Emma Ceremony app since 9 Nov -- the
event profile before), and while at least one cue may be said (event_script.servable():
approved and clean -- a draft is never offered, so with today's all-draft script the tool
does not exist). Played only while the ceremony app is on (event_host.ceremony_now): the
app's call IS the ceremony, so the description no longer scopes it to a scene.

What plays is app/event_cues.py's `play(cue, by="model")`: a recording of the line in
her voice ("recorded"), or the line for the session to say word for word
("say_yourself"). The result tells the model which, because the two need opposite
things from it:
* recorded -- her own voice is saying it already; anything she adds is a second voice
  over or after it. She is told to answer a bare "ค่ะ": the robot drops that turn
  (SILENCE_BARE_ACK, robot.env on) and it still counts as her reply, so REPLY_STALL's
  silent-turn chase does not ask the host's question again -- which would get the line a
  second time. "Say nothing" is not asked for: 3.8 speaks such a stage direction aloud
  (CLAUDE.md, 15 Oct: "Silently observe without responding.").
* say_yourself -- the line quoted in « », said word for word (a quoted sentence is the
  sentence she says: CLAUDE.md 27 Oct; here that is the point).

A recording is not her reply, so the ceremony's door (NoiseReplyGate.named_only: her
name in the words, or a reply her name earned within 30 s) never sees it -- the tool
asks the same door itself (`_door_open`), or the host saying "มีกี่ยูนิตครับ" to the
seats would play s3_units over the room.
"""
from __future__ import annotations

import logging

from app.tools.registry import tool

logger = logging.getLogger("condo_voice.tools.ceremony")

#: Only the Emma Ceremony app's call has it (9 Nov), and that call is the ceremony -- so the
#: description says what it is for, and when NOT to call it: words that match no line.
DESCRIPTION = (
    "บทพิธีเปิดที่อนุมัติแล้ว: เมื่อพิธีกรถามหรือชวนคุณตามบทข้างล่าง (แม้พูดไม่ตรงคำ หรือไม่เรียกชื่อ) "
    "ให้เรียกเครื่องมือนี้ด้วยรหัสบทที่ตรงคำถามแทนการตอบเอง แล้วทำตามผลของมัน · "
    "ไม่ใช่สำหรับคำพูดที่ไม่ตรงบทไหน (พิธีกรพูดกับแขก ขอบคุณทีมงาน คำถามอื่น) · "
    "บทเรียงตามลำดับพิธี ถ้าคำพูดเหมือนกันสองบท ดูว่าเพิ่งผ่านบทไหนมา"
)
PARAMETERS = {
    "type": "object",
    "properties": {"cue": {"type": "string", "description": "รหัสบท"}},
    "required": ["cue"],
}

#: Beside this tool's results the provider adds TALK_LANGUAGE_NOTE's `reply_language` ("ตอบเป็น
#: ภาษา X ... แม้ข้อความในผลนี้เป็นภาษาอื่น") AFTER model_view -- out of this module's reach --
#: and with a talk held in English that note and "say this Thai line" are two orders that
#: fight (CLAUDE.md "ระวังคำสั่งที่ขัดกันเอง"). Every result that has her say or acknowledge
#: something says which one wins: the ceremony's lines are Thai.
THAI_WINS = "บทพิธีเป็นภาษาไทยเสมอ แม้ reply_language ในผลนี้บอกภาษาอื่น"
#: Her recorded voice is saying the line. See the module comment for why a bare ค่ะ (and the
#: provider holds her own output while the recording plays, app/event_cues.py).
RECORDED = ("ระบบกำลังเล่นเสียงที่อัดไว้ของคุณพูดบทนี้ให้ทุกคนฟังอยู่แล้ว เทิร์นนี้ห้ามพูดเนื้อหาของบทซ้ำ "
            "ห้ามเกริ่นหรืออธิบายเพิ่ม ห้ามเรียกเครื่องมืออีก ตอบแค่คำว่า 'ค่ะ' คำเดียวแล้วจบเทิร์น (" + THAI_WINS + ")")
#: No recording: the live session says it. The question inside a line is part of the line
#: (s3_facility ends on one; CEREMONY_RULE beside every tool result says "ไม่ถามกลับ").
SAY = ("นี่คือบทที่อนุมัติแล้วสำหรับคำถามนี้ พูดประโยคในเครื่องหมาย « » ออกเสียงตามตัวอักษรทุกคำเป็นภาษาไทย "
       "(" + THAI_WINS + " ห้ามแปล) รวมคำถามที่อยู่ในบทด้วย ห้ามเติม ห้ามตัด ห้ามพูดอย่างอื่นก่อนหรือหลัง "
       "ห้ามเรียกเครื่องมือนี้อีก: «%s»")
#: The same for a file spoken in English (`"speak": "en"`): an English order round an English
#: line, and it says the line wins over `reply_language` -- never THAI_WINS, which with an
#: English line was "say this in Thai, never translate" (review 17 Nov).
SAY_EN = ("This is the approved line for this question. Say the sentence inside « » aloud in English, "
          "exactly as written, every word (the ceremony's lines are English even if reply_language in this "
          "result names another language; do not translate), with any question that is in the line. Do not "
          "add or cut anything, say nothing before or after it, and do not call this tool again: «%s»")
#: A cue with no fixed words (s4_recommend: free rooms change daily).
LIVE = ("บทนี้ไม่มีประโยคตายตัว ตอบเองตามแนวนี้: %s · ตัวเลขทุกตัวต้องมาจากข้อมูลโครงการหรือผลของเครื่องมือเท่านั้น "
        "ห้ามเรียกเครื่องมือนี้ซ้ำ")
#: Not in the ceremony (a call of the ceremony profile that is not the robot's active one):
#: the question is answered the usual way (searching if it needs to), in the talk's language --
#: not the script's line.
NOT_CEREMONY = ("ตอนนี้ไม่ใช่ช่วงพิธีเปิด นี่ไม่ใช่คำถามตามบทพิธี ตอบคำถามเองทันทีตามปกติ "
                "(ค้นข้อมูลโครงการได้ตามปกติ ตอบเป็นภาษาของบทสนทนา) ห้ามพูดประโยคของบทพิธี "
                "ห้ามเรียกเครื่องมือนี้อีก")
NOT_NAMED = "พิธีกรกำลังพูดกับแขกในงาน ไม่ได้ถามคุณ ไม่ต้องพูดบทนี้ ฟังต่อเงียบๆ"
UNKNOWN = "ไม่มีบทนี้ ห้ามเดารหัสบทใหม่ ตอบคำถามเองสั้นๆ จากข้อมูลโครงการเท่านั้น"
FAILED = ("เล่นบทนี้ไม่ได้ตอนนี้ ตอบคำถามของพิธีกรเองสั้นๆ จากข้อมูลโครงการเท่านั้น ห้ามเรียกเครื่องมือนี้ซ้ำ "
          "ห้ามบอกว่าระบบมีปัญหา")
#: event_cues answers "already" (this line is being said) or "busy" (another one is): her own
#: answer now would be a second voice over it -- the recorded case again, not the failure one.
SAYING = ("เสียงของคุณกำลังพูดบทอยู่ตอนนี้ เทิร์นนี้ห้ามพูดเนื้อหาของบท ห้ามเกริ่นหรืออธิบายเพิ่ม "
          "ห้ามเรียกเครื่องมืออีก ตอบแค่คำว่า 'ค่ะ' คำเดียวแล้วจบเทิร์น (" + THAI_WINS + ")")
SAYING_ERRORS = ("already", "busy")
#: How long a host's line may run in the description (the prompt's block has them whole).
ASK_MAX_CHARS = 120


def _cues() -> dict[str, dict]:
    from app import event_host

    return event_host.script_cues()


def offered() -> bool:
    """At least one cue the model may be handed (registry.allowed, after the switch)."""
    try:
        return bool(_cues())
    except Exception:                                    # noqa: BLE001 -- a bad file is no tool
        logger.exception("the ceremony script could not be read")
        return False


def _ask(c: dict, host: str) -> str:
    from app import event_host

    return event_host.script_ask(c, host, cap=ASK_MAX_CHARS) + ("" if c.get("th") else " (ไม่มีประโยคตายตัว)")


def _declare():
    """The cue ids as an enum and each one's question, from the script on this machine."""
    from app import event_host

    cues = _cues()
    if not cues:
        return DESCRIPTION, PARAMETERS
    host = event_host.script_host()
    listing = " · ".join("%s = %s" % (cid, _ask(c, host)) for cid, c in cues.items())
    parameters = {"type": "object",
                  "properties": {"cue": {"type": "string", "enum": list(cues), "description": "รหัสบท"}},
                  "required": ["cue"]}
    return DESCRIPTION + " · บท: " + listing, parameters


def _door_open() -> bool:
    """The ceremony's door, asked for a recording it never sees: her name in what the host
    said since her last reply -- the words the reply door itself judges by
    (NoiseReplyGate._heard; the latest caption alone goes stale at the next activity_start,
    which one burst of applause between the question and the call would be) -- or a reply
    her name earned a moment ago (named_recently). No session to ask = closed. (The ceremony
    eval opens it in its own process: it measures the model's pick, and its text turns skip
    the door for every mode alike.)"""
    from app import heard
    from app import session as session_module
    from app.providers.transcript_filter import HER_NAME_ONLY

    caption = heard.caption_for()
    if caption is not None and HER_NAME_ONLY.search(caption.text or ""):
        return True
    provider = getattr(session_module._active, "provider", None)
    gate = getattr(provider, "_noise_gate", None)
    if gate is None:
        return False
    since_reply = getattr(gate, "_heard", None)
    if isinstance(since_reply, dict) and HER_NAME_ONLY.search(str(since_reply.get("text") or "")):
        return True
    return bool(gate.named_recently())


async def _play(cue_id: str, c: dict) -> dict:
    """app/event_cues.py's answer. Built beside this module; until it is in the tree the
    line goes to the live session to say -- what a press with no recording does."""
    try:
        from app import event_cues
    except ImportError:
        logger.warning("app.event_cues is not in this tree -- %s goes to the live session to say", cue_id)
        from app import event_script

        return {"ok": True, "mode": "say_yourself", "line": event_script.spoken(c)["th"]}
    try:
        result = await event_cues.play(cue_id, by="model")
    except Exception:                                    # noqa: BLE001 -- she answers instead
        logger.exception("event_cues.play(%s) failed", cue_id)
        return {"ok": False, "error": "play_failed"}
    return result if isinstance(result, dict) else {"ok": False, "error": "play_failed"}


@tool(name="say_script_line", description=DESCRIPTION, parameters=PARAMETERS, declare=_declare,
      tags=["event"], exclusive=True)
async def say_script_line(cue: str) -> dict:
    """The model names the script question it heard; the line is the server's."""
    from app import event_host, turnlog

    cue_id = (cue or "").strip() if isinstance(cue, str) else ""
    cues = _cues()
    c = cues.get(cue_id)
    if c is None:
        return {"ok": False, "error": "unknown_cue", "known": list(cues), "instruction": UNKNOWN}
    if not event_host.ceremony_now():
        return {"ok": False, "cue": cue_id, "reason": "not_ceremony", "instruction": NOT_CEREMONY}
    if not _door_open():
        turnlog.record("ceremony_cue", cue=cue_id, by="model", mode=None, why="not_named")
        return {"ok": False, "cue": cue_id, "reason": "not_named", "instruction": NOT_NAMED}
    if not c.get("th"):
        turnlog.record("ceremony_cue", cue=cue_id, by="model", mode="live")
        return {"ok": True, "cue": cue_id, "mode": "live", "instruction": LIVE % c["live_th"]}
    result = await _play(cue_id, c)
    mode = result.get("mode") if result.get("ok") else None
    turnlog.record("ceremony_cue", cue=cue_id, by="model", mode=mode,
                   why=None if mode else str(result.get("error") or "not_played"))
    if mode == "recorded":
        return {"ok": True, "cue": cue_id, "mode": "recorded", "instruction": RECORDED}
    if mode == "say_yourself":
        from app import event_script

        said = event_script.spoken(c)                     # the English in an English-spoken file
        line = result.get("line")
        line = line.strip() if isinstance(line, str) and line.strip() else said["th"]
        rule = SAY_EN if said.get("lang") == "en" else SAY
        return {"ok": True, "cue": cue_id, "mode": "say_yourself", "instruction": rule % line}
    error = str(result.get("error") or "not_played")
    return {"ok": False, "cue": cue_id, "error": error,
            "instruction": SAYING if error in SAYING_ERRORS else FAILED}
