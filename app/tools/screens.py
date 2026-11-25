"""`room_screen` -- the robot's face on a room's big screen, or off it.

Owner 28 Oct 2026: "สั่งให้เปิดจอก็ให้หุ่นเชื่อมจอเลย" (app/room_screens.py has
the rest, including the proximity half that needs no tool). Group `screens`:
opt-in (`_OPT_IN`), and visible only on the robot face's own session with
ROOM_SCREENS_ENABLED, the mirror listener and at least one screen configured
(`registry.allowed`). Never in MULTI_SESSION_SAFE.

The model picks open/close and, when there are several, which screen from
the configured names -- never an address or a URL (the `show_unit(room)`
rule): the server builds the page address and the PC's own agent opens only
that page.

Opening needs the guest's own words (the heard guard, app/heard.py): a word
for a screen in what was just heard, or a yes to Emma's question about it.
A misheard "open" is not a wrong answer, it is her face arriving on top of
whatever another room is showing its own customer. Closing needs nothing --
it is the way back, like stop.
"""
from __future__ import annotations

import time

from app.tools.registry import tool

#: What the guest says when they mean the room's screen. Whole words (has_words).
SCREEN_WORDS = ("จอ", "จอใหญ่", "หน้าจอ", "ทีวี", "โทรทัศน์", "มอนิเตอร์",
                "screen", "big screen", "tv", "television", "monitor", "display")
#: A yes to "ให้ขึ้นจอใหญ่ไหมคะ" -- only within ASK_WINDOW_S of that question.
YES_WORDS = ("ใช่", "ได้", "เอา", "เอาเลย", "เปิด", "เปิดเลย", "ขึ้น", "โอเค", "ครับ", "ค่ะ", "คะ",
             "จ้า", "ok", "okay", "yes", "yeah", "sure", "please", "go")
#: Never an open, whatever else is in the sentence: "ไม่ต้องเปิดจอ", "ปิดจอ".
NO_WORDS = ("ไม่", "อย่า", "ไม่ต้อง", "ไม่เอา", "ปิด", "ลง", "เอาลง", "ยกเลิก", "หยุด",
            "no", "not", "don't", "dont", "close", "off", "stop", "cancel")
ASK_WINDOW_S = 60.0
_asked: dict[str, float | None] = {"at": None}

DESCRIPTION = (
    "เอาหน้า Emma (หน้าจอหุ่นที่ลูกค้าเห็นอยู่) ขึ้นจอใหญ่ของห้อง (จอคอมหรือทีวีในห้องนั้น) หรือเอาลง: "
    "เรียกเมื่อคนตรงหน้าขอ 'เปิดจอ' 'ขึ้นจอใหญ่' 'โชว์บนทีวี' 'show it on the big screen'→open "
    "'ปิดจอ' 'เอาลงจากจอ'→close · จอเปิดเองเมื่อหุ่นเข้าใกล้ ไม่ต้องเรียกเพื่อเรื่องนั้น · "
    "ไม่ใช่การเปิดเว็บ สไลด์ หรือโปรแกรม · ผลไม่สำเร็จห้ามเรียกซ้ำเอง"
)
PARAMETERS = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["open", "close"]}},
    "required": ["action"],
}


def _declare():
    """With several screens the model also names which, from the table."""
    from app import room_screens

    names = room_screens.names()
    if len(names) < 2:
        return DESCRIPTION, PARAMETERS
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["open", "close"]},
            "screen": {"type": "string", "enum": names,
                       "description": "จอห้องไหน (เว้นไว้ = จอที่หุ่นอยู่ใกล้)"},
        },
        "required": ["action"],
    }
    return DESCRIPTION + " · จอที่มี: " + ", ".join(names), parameters


#: What the ceremony's host says when they mean the film. Whole words (has_words).
CLIP_WORDS = ("คลิป", "วิดีโอ", "วีดีโอ", "วีดิโอ", "วิดิโอ", "หนัง", "clip", "video", "film", "movie", "vtr")

CLIP_DESCRIPTION = (
    "เล่นคลิปของงานบนจอใหญ่ หรือหยุดคลิปที่กำลังเล่น: เรียกเมื่อคนดำเนินพิธีเรียกชื่อคุณแล้วขอ "
    "'Emma เปิดคลิปหน่อย' 'Emma play the video'→play 'หยุดคลิป'→stop · ระบบจะบอกว่าเล่นได้ไหม "
    "ผลไม่สำเร็จห้ามเรียกซ้ำเอง"
)


def _declare_clip():
    from app import event_clips

    ids = [cid for cid, clip in event_clips.table().items() if clip["has_file"]]
    parameters = {"type": "object",
                  "properties": {"action": {"type": "string", "enum": ["play", "stop"]}},
                  "required": ["action"]}
    if len(ids) > 1:
        parameters["properties"]["clip"] = {"type": "string", "enum": ids}
    titles = [event_clips.table()[cid]["title_th"] for cid in ids]
    return CLIP_DESCRIPTION + (" · คลิปที่มี: " + ", ".join(titles) if titles else ""), parameters


def _named_just_now(text: str) -> bool:
    """Her name in these words, or a reply her name earned a moment ago: "เดี๋ยวเราจะชม
    คลิปกันครับ" to the seats is not a cue."""
    from app import session as session_module
    from app.providers.transcript_filter import HER_NAME_ONLY

    if HER_NAME_ONLY.search(text):
        return True
    session = session_module._active
    provider = getattr(session, "provider", None)
    gate = getattr(provider, "_noise_gate", None)
    return bool(gate is not None and gate.named_recently())


@tool(name="event_clip", description=CLIP_DESCRIPTION,
      parameters={"type": "object", "properties": {"action": {"type": "string", "enum": ["play", "stop"]}},
                  "required": ["action"]},
      declare=_declare_clip, tags=["robot", "screen"], exclusive=True)
async def event_clip(action: str, clip: str = "") -> dict:
    """The model picks play or stop and which id; a file is not its to name. Only inside a
    ceremony, where the door lets one voice in: at the party any guest's "เปิดคลิปหน่อย"
    would otherwise take the room's screen. Stopping needs nothing, like every stop."""
    from app import event_clips, event_host, heard

    action = (action or "").strip().lower()
    if action == "stop":
        stopped = await event_clips.stop("voice")
        return {"ok": True, "action": "stop", "stopped": stopped,
                "instruction": "หยุดคลิปแล้ว ตอบรับสั้นๆ ประโยคเดียว" if stopped
                               else "ไม่มีคลิปกำลังเล่น บอกสั้นๆ ประโยคเดียว"}
    if action != "play":
        return {"ok": False, "error": "unknown action", "known": ["play", "stop"]}
    if not event_host.ceremony_now():
        return {"ok": False, "action": "play", "reason": "not_ceremony",
                "instruction": "ตอนนี้ไม่ใช่ช่วงพิธีเปิด เปิดคลิปเองไม่ได้ บอกสั้นๆ ว่าทีมงานดูแลเรื่องนี้ "
                               "ห้ามบอกว่าเปิดแล้ว"}
    caption = heard.caption_for()
    if (caption is None or heard.has_words(caption.text, NO_WORDS)
            or not heard.has_words(caption.text, CLIP_WORDS) or not _named_just_now(caption.text)):
        return {"ok": False, "action": "play", "reason": "not_heard",
                "instruction": "ยังไม่มีใครเรียกชื่อคุณแล้วขอคลิป อย่าเปิด ห้ามบอกว่าเปิดแล้ว ฟังต่อเงียบๆ"}
    ids = [cid for cid, entry in event_clips.table().items() if entry["has_file"]]
    chosen = clip if clip in ids else (ids[0] if len(ids) == 1 else None)
    if chosen is None:
        return {"ok": False, "action": "play", "error": "which_clip", "clips": ids,
                "instruction": "มีหลายคลิป ถามสั้นๆ ว่าคลิปไหน ห้ามเดาเอง ห้ามบอกว่าเปิดแล้ว"}
    result = await event_host.play_clip(chosen, by="voice", lead_in=True)
    if result.get("ok"):
        return {"ok": True, "action": "play", "clip": chosen, "hardware": "requested",
                "instruction": "ส่งคำสั่งเล่นคลิปแล้ว ภาพจะขึ้นบนจอหลังคุณพูดจบ ตอบรับสั้นมากหนึ่งประโยคตามภาษาของบทสนทนา "
                               "ห้ามบอกว่าเห็นคลิปเล่นแล้ว แล้วเงียบจนกว่าคลิปจบ"}
    return {"ok": False, "action": "play", "error": result.get("error"),
            "instruction": "เล่นคลิปไม่สำเร็จ บอกตรงๆ สั้นๆ ว่าตอนนี้ขึ้นคลิปไม่ได้ ทีมงานกำลังดูให้ ห้ามบอกว่าเปิดแล้ว"}


def heard_open(now: float | None = None) -> bool:
    """The guest asked for a screen in their own words, just now."""
    from app import heard

    caption = heard.caption_for()
    if caption is None or heard.has_words(caption.text, NO_WORDS):
        return False
    if heard.has_words(caption.text, SCREEN_WORDS):
        return True
    asked = _asked["at"]
    clock = time.monotonic() if now is None else now
    return (asked is not None and clock - asked <= ASK_WINDOW_S and caption.at > asked
            and heard.has_words(caption.text, YES_WORDS))


@tool(name="room_screen", description=DESCRIPTION, parameters=PARAMETERS, declare=_declare,
      tags=["robot", "screen"], exclusive=True)
async def room_screen(action: str, screen: str = "") -> dict:
    from app import room_screens

    action = (action or "").strip().lower()
    if action not in ("open", "close"):
        return {"ok": False, "error": "unknown action", "known": ["open", "close"]}
    if action == "close":
        result = await room_screens.close_screen(screen or None)
        if result["ok"]:
            return {"ok": True, "action": "close",
                    "instruction": "เอาหน้า Emma ลงจากจอใหญ่แล้ว (เครื่องนั้นรับคำสั่งแล้ว) "
                                   "ตอบรับสั้นๆ ประโยคเดียว"}
        return {"ok": False, "action": "close", "error": result.get("error"),
                "instruction": "สั่งเอาลงจากจอไม่สำเร็จ บอกตรงๆ สั้นๆ ว่าตอนนี้สั่งจอนั้นไม่ได้ "
                               "ให้พนักงานช่วยปิดที่เครื่อง ห้ามบอกว่าปิดแล้ว"}
    if not heard_open():
        _asked["at"] = time.monotonic()
        return {"ok": False, "action": "open", "reason": "not_heard",
                "instruction": "ยังไม่ได้ยินลูกค้าขอเรื่องจอเอง ถามสั้นๆ หนึ่งประโยคว่าจะให้ขึ้นหน้า Emma "
                               "บนจอใหญ่ของห้องไหม แล้วรอคำตอบ ห้ามบอกว่าเปิดแล้ว"}
    _asked["at"] = None
    result = await room_screens.open_screen(screen or None)
    if result["ok"]:
        return {"ok": True, "action": "open", "screen": result["screen"],
                "instruction": "ส่งหน้า Emma ขึ้นจอใหญ่ของห้องแล้ว ภาพจะขึ้นในไม่กี่วินาที ตอบรับสั้นๆ ประโยคเดียวว่า"
                               "ขึ้นจอใหญ่ให้แล้ว (ไม่ใช่ 'แชร์หน้าจอ') ห้ามบอกว่าเห็นภาพบนจอแล้ว และคุยต่อตามปกติ"}
    if result.get("error") in ("which_screen", "unknown_screen"):
        return {"ok": False, "action": "open", "error": result["error"], "screens": result.get("screens"),
                "instruction": "มีจอมากกว่าหนึ่งจอ ถามสั้นๆ ว่าจะให้ขึ้นจอห้องไหน แล้วเรียกใหม่พร้อม screen "
                               "ห้ามเดาเอง ห้ามบอกว่าเปิดแล้ว"}
    return {"ok": False, "action": "open", "error": result.get("error"),
            "instruction": "ขึ้นจอใหญ่ไม่สำเร็จ (เครื่องในห้องนั้นไม่ตอบ) บอกตรงๆ สั้นๆ ว่าตอนนี้ขึ้นจอไม่ได้ "
                           "ให้พนักงานช่วยดูเครื่อง ห้ามบอกว่าเปิดแล้ว แล้วคุยต่อตามปกติ"}
