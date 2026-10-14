"""present_story: the robot face tells a story with pictures while Emma talks.

The model picks WHICH story -- one enum value -- and nothing else: every
number, word and picture comes from the approved catalog (app/stories.py),
the `show_unit(room)` rule. Visible only on the robot face's own session,
the condo profile, with STORIES_ENABLED on, `stories` named in TOOL_GROUPS
(opt-in, app/tools/__init__.py) and an approved catalog with at least one
complete story (app/tools/registry.py `allowed`). Anywhere else the sales
room's tool list is byte-identical.
"""
from __future__ import annotations

from app.tools.registry import tool

#: The boundary with the screens that already answer room questions is the
#: owner's 12 Oct order: a chosen room TYPE ("สนใจสตูดิโอ", a tap, "ขอดูผัง
#: ห้องสองห้องนอน") is a real available unit through show_room_type; a story
#: is "what is it like / how big / where". Without the sentence the model
#: had two right-looking answers for "ขอดูห้องสองห้องนอน".
#: "ก่อนตอบ แม้จะรู้คำตอบอยู่แล้ว": live eval 13 Oct, 28 cases -- every miss
#: (11) called no tool at all and answered from the project facts in the
#: prompt (door lock, hospitals, developer), and one of them made up a view
#: recommendation. The model knowing the answer is not the question; the
#: guest seeing the pictures is.
DESCRIPTION = (
    "เล่าเรื่องด้วยภาพเคลื่อนไหวบนจอหน้าหุ่นไปพร้อมเสียงพูด: เรียกก่อนตอบทุกครั้งที่คำถามของลูกค้าเข้าหัวข้อใน topic "
    "(ถามด้วยคำอื่นหรือภาษาอื่นก็นับ) แม้จะรู้คำตอบจากข้อมูลโครงการอยู่แล้ว เพราะลูกค้าได้เห็นภาพประกอบ "
    "แล้วพูดตามบรรทัด say ในผลลัพธ์ทีละบรรทัด ภาพและตัวเลขบนจอมาจากข้อมูลที่อนุมัติ "
    "เลือกได้แค่หัวข้อ ไม่ต้องเรียก search_condo_info ซ้ำสำหรับคำถามเดียวกัน "
    "ลูกค้าเลือกแบบห้องหรือขอดูผังห้อง/ห้องที่ยังว่างของแบบใดแบบหนึ่ง ใช้ show_room_type "
    "ห้องเจาะจงหรือผังชั้นสถานะจริงใช้ show_unit / show_plan ตามเดิม"
)

#: The shape when no catalog is offered (the tool is then invisible anyway).
#: `topic` is the only argument there will ever be: a story is chosen, never
#: composed (a test holds this).
PARAMETERS = {
    "type": "object",
    "properties": {"topic": {"type": "string", "description": "หัวข้อเรื่องที่จะเล่าบนจอ"}},
    "required": ["topic"],
}


def _declare():
    """This machine's catalog as the enum, each topic with the questions it
    answers (the description is what tells the model when to call it)."""
    from app import stories

    offered = stories.available()
    if not offered:
        return DESCRIPTION, PARAMETERS
    topics = []
    for sid in sorted(offered):
        story = offered[sid]
        # Every Thai question the designer listed (up to six) and the first
        # English one. The first live run listed two Thai questions per topic,
        # and the narrow ones it did not list -- "ห่างทะเลไหม", "ไปสนามบินกี่นาที",
        # "สระลากูนยาวเท่าไหร่" -- were answered from the facts with no story.
        asks = [q for q in (story.get("questions_th") or [])[:6] + (story.get("questions_en") or [])[:1]
                if isinstance(q, str)]
        topics.append("%s = %s" % (sid, " / ".join(asks)) if asks else sid)
    description = DESCRIPTION + " หัวข้อ: " + "; ".join(topics)
    parameters = {
        "type": "object",
        "properties": {"topic": {"type": "string", "enum": sorted(offered),
                                 "description": "หัวข้อเรื่องที่จะเล่าบนจอ"}},
        "required": ["topic"],
    }
    return description, parameters


#: `story` (the page payload) goes to the screen only: the model is handed
#: ok / topic / say / instruction -- the lines to speak and the rules.
@tool(name="present_story", description=DESCRIPTION, parameters=PARAMETERS, declare=_declare,
      screen_only=("story",))
def present_story(topic: str) -> dict:
    from app import stories

    return stories.tool_result(topic)
