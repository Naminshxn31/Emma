"""`move_body` — one named gesture of the robot's own head or arms (design §2d).

Group `body`: opt-in (`_OPT_IN`), loaded only when TOOL_GROUPS names it, and
visible only on the robot face's own session with ROBOT_BODY_ENABLED on
(`registry.allowed`). Never in MULTI_SESSION_SAFE. While it is visible,
`request_handshake` is hidden, so the handshake has one path.

The parameter is an enum and nothing else: the model chooses *which* named
gesture, the Android app owns every number that moves a joint. A free-text or
numeric argument here would hand a pulse to a machine that mishears "อะไรนะ
50" (CLAUDE.md, show_unit's rule).

The description quotes real phrasings and the one wrong trigger that matters
— a question about the arm is not a command to move it — because a rule the
model can match against what it is about to do is the rule it follows
(CLAUDE.md: "อ้างคำที่โมเดลพูดผิดจริงๆ ลงไปในคำสั่ง").
"""
from __future__ import annotations

from app import robot_body
from app.robot_contracts import BODY_ACTION_IDS
from app.tools.registry import tool


@tool(
    name="move_body",
    description=(
        "ขยับหัว แขน หรือมือของตัวหุ่น Emma หนึ่งท่า เมื่อคนตรงหน้าสั่งชัดเจน เรียกทันทีไม่ต้องถามซ้ำ "
        "เช่น 'ก้มหน้าหน่อย'→head_down 'เงยหน้า'→head_up 'พยักหน้า'→nod 'ส่ายหน้า'→shake_head "
        "'หันซ้าย'→head_left 'หันขวา'→head_right 'มองตรง'→head_center 'ยกแขนซ้าย'→raise_left_arm "
        "'ยกแขนขวา'→raise_right_arm 'โบกมือ'→wave 'ยกมือทักทาย'→greet_arm 'จับมือกันไหม'→handshake "
        "'ขยับมือไปทางซ้าย/ขวา'→hands_left/hands_right 'เอาแขนลง' 'ท่าพัก'→rest "
        "'หยุด' ระหว่างหุ่นกำลังทำท่า→stop · ซ้าย/ขวาคือของตัวหุ่นเอง · "
        "อย่าเรียกเพียงเพราะมีคนถามว่าหุ่นทำท่าอะไรได้บ้างหรือมีแขนไหม · ผลไม่สำเร็จห้ามเรียกซ้ำเอง"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(BODY_ACTION_IDS),
                "description": "ชื่อท่าจากรายการเท่านั้น",
            }
        },
        "required": ["action"],
    },
    tags=["robot", "body"],
    exclusive=True,
    # A stop must never come back "busy" while a gesture's ack is pending
    # (safety review #1): registry.preempt skips the in-flight check for it.
    preempt=lambda args: args.get("action") == "stop",
)
async def move_body(action: str) -> dict:
    return await robot_body.request(action)
