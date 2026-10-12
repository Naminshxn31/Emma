"""Tap-to-ask topics on the robot face (owner, 10 Oct 2026: "ปรับ ... uxui ของ
แอพให้ดูดีที่สุด ใช้งานได้จริง").

Reception robots put a handful of topic buttons under the face because the
person in front of one rarely knows what it can answer, and a showroom is
loud: a tap always gets through where a voice may not. The same rule as
`show_unit(room)` and the document catalogue: **the page picks an id, the
server owns every word.** The page never sends text to the model -- a
free-text field on a public screen is a way to put anything in Emma's
mouth -- and nothing here carries a number: prices, sizes and places all
come back from the facts and the tools, as for a spoken question.

The words go to the model as a user turn that says it was a TAP, not
speech: prompt rule #2 sets the reply language from what the guest SAID,
and a Thai topic label tapped by an English speaker must not switch her to
Thai. So the language is "the one you are already speaking in", falling
back to the page's language (its language buttons, or the script of the
guest's last utterance, read on the page).

Robot face only (`session.run` sends the list on a robot-face `ready`): the
sales-room desk page never sees it and its `ready` frame is unchanged.
"""
from __future__ import annotations

LANGS = ("th", "en", "zh")

_LANG_NAMES_TH = {"th": "ไทย", "en": "อังกฤษ", "zh": "จีน"}

#: The topics, per profile. `ask` is the guest's question in each language;
#: the label is what the button says. Order = on-screen order.
TOPICS: dict[str, tuple[dict, ...]] = {
    "condo": (
        {"id": "about",
         "label": {"th": "รู้จักโครงการ", "en": "About the project", "zh": "项目介绍"},
         "ask": {"th": "ช่วยเล่าเกี่ยวกับโครงการนี้สั้นๆ หน่อยค่ะ",
                 "en": "Could you tell me briefly about this project?",
                 "zh": "可以简单介绍一下这个项目吗？"}},
        {"id": "price",
         "label": {"th": "ราคาเริ่มต้น", "en": "Starting prices", "zh": "起价"},
         "ask": {"th": "ราคาเริ่มต้นของแต่ละแบบห้องเท่าไหร่คะ",
                 "en": "What are the starting prices for each unit type?",
                 "zh": "每种户型的起价是多少？"}},
        {"id": "units",
         "label": {"th": "แบบห้อง", "en": "Unit types", "zh": "户型"},
         "ask": {"th": "มีแบบห้องอะไรบ้าง ขนาดเท่าไหร่คะ",
                 "en": "What unit types are there, and how big are they?",
                 "zh": "有哪些户型？面积多大？"}},
        {"id": "facilities",
         "label": {"th": "ส่วนกลาง", "en": "Facilities", "zh": "公共设施"},
         "ask": {"th": "โครงการมีส่วนกลางอะไรบ้างคะ",
                 "en": "What facilities does the project have?",
                 "zh": "项目有哪些公共设施？"}},
        {"id": "location",
         "label": {"th": "ทำเลที่ตั้ง", "en": "Location", "zh": "位置"},
         "ask": {"th": "โครงการอยู่ตรงไหน ใกล้อะไรบ้างคะ",
                 "en": "Where is the project, and what is nearby?",
                 "zh": "项目在哪里？附近有什么？"}},
        {"id": "available",
         "label": {"th": "ห้องที่ยังว่าง", "en": "Available units", "zh": "可售房源"},
         "ask": {"th": "ตอนนี้ยังมีห้องว่างแบบไหนบ้างคะ",
                 "en": "Which units are still available?",
                 "zh": "现在还有哪些房源可售？"}},
    ),
}

#: The language buttons. Tapping one is the guest saying which language,
#: which rule #2 already obeys at once ("ถ้าระบุภาษาที่ต้องการให้ใช้ทันที").
LANGUAGE_BUTTONS = (
    {"id": "th", "label": "ไทย"},
    {"id": "en", "label": "English"},
    {"id": "zh", "label": "中文"},
)

HINT = {
    "th": "ถามได้เลย หรือแตะหัวข้อด้านล่าง",
    "en": "Ask me anything, or tap a topic",
    "zh": "请直接提问，或点击下方话题",
}


def _lang(code) -> str:
    code = (code or "").strip().lower() if isinstance(code, str) else ""
    return code if code in LANGS else "th"


def for_page(profile: str, brand: str = "") -> dict | None:
    """What the robot face renders: labels in every language, no ask text
    (the page never needs it). None = this profile has no topics."""
    topics = TOPICS.get(profile)
    if not topics:
        return None
    return {
        "brand": brand,
        "langs": [dict(b) for b in LANGUAGE_BUTTONS],
        "hint": dict(HINT),
        "items": [{"id": t["id"], "label": dict(t["label"])} for t in topics],
    }


def text_for(profile: str, ask_id, lang=None) -> str | None:
    """The user turn for a tap, or None for an id this profile does not have.
    `lang` is the page's language (its buttons, else the script of the
    guest's last utterance); the model still answers in the language the
    guest is speaking, if any."""
    if not isinstance(ask_id, str):
        return None
    code = _lang(lang)
    if ask_id.startswith("lang_"):
        target = ask_id[5:]
        if target not in LANGS or not TOPICS.get(profile):
            return None
        return ("[ลูกค้าแตะปุ่มเลือกภาษาบนจอ ไม่ได้พูด] ลูกค้าเลือกภาษา%s — ตั้งแต่นี้ตอบเป็นภาษา%s "
                "ตอนนี้ตอบรับสั้นๆ ประโยคเดียวเป็นภาษา%s แล้วหยุด ห้ามถามต่อ"
                % (_LANG_NAMES_TH[target], _LANG_NAMES_TH[target], _LANG_NAMES_TH[target]))
    for topic in TOPICS.get(profile) or ():
        if topic["id"] == ask_id:
            return ("[ลูกค้าแตะปุ่ม \"%s\" บนจอ ไม่ได้พูด — ตอบเป็นภาษาเดียวกับที่ลูกค้าพูดล่าสุด "
                    "ถ้ายังไม่ได้พูด ตอบเป็นภาษา%s] %s"
                    % (topic["label"]["th"], _LANG_NAMES_TH[code], topic["ask"][code]))
    return None
