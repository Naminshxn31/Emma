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

import re

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
    item = _choice(ask_id)
    if item is not None and TOPICS.get(profile):
        return ("[ลูกค้าแตะปุ่ม \"%s\" ใต้คำถามของคุณบนจอ ไม่ได้พูด — ตอบเป็นภาษาเดียวกับที่ลูกค้าพูดล่าสุด "
                "ถ้ายังไม่ได้พูด ตอบเป็นภาษา%s] %s"
                % (item["label"]["th"], _LANG_NAMES_TH[code], item["ask"]))
    return None


# ---- choices under her own question (owner, 12 Oct 2026) ----------------------
#
# "ลูกค้ากำลังมองหาห้องแบบสตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ คำพวกนี้อยากให้ขึ้น
# เป็นตัวเลือกบนจอด้วยให้ลูกค้ากดเข้าไปดูได้ ... พอกดไปเราก็อาจจะขึ้นผังหรือรูปห้องนั้น"
#
# The topics' rule again: the page gets ids and labels, a tap comes back as an
# id, the words the model reads are these. What a tap *does* is a tool the
# model can also reach by voice ("สตูดิโอค่ะ" after the same question), so a
# spoken answer and a tapped one end on the same screen. The buttons follow
# what she ASKED -- read from her own transcript by session.py -- never what
# the guest said (their words stay off this screen).

#: Each set: the tool a tap leads to (no tool on this machine = no buttons --
#: a button that asks for a tool she cannot see is the refusing-model bug),
#: the line above the buttons, and the buttons in on-screen order.
CHOICES: dict[str, dict] = {
    "room_types": {
        "tool": "show_room_type",
        "hint": {"th": "แตะเพื่อดูผังห้องจริง", "en": "Tap to see a real room plan",
                 "zh": "点击查看真实户型图"},
        "items": (
            {"id": "type_studio", "label": {"th": "สตูดิโอ", "en": "Studio", "zh": "开间"},
             "ask": "ลูกค้าเลือกห้องแบบสตูดิโอ — เรียก show_room_type(type=\"studio\") "
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์"},
            {"id": "type_1br", "label": {"th": "1 ห้องนอน", "en": "1 Bedroom", "zh": "一室"},
             "ask": "ลูกค้าเลือกห้องแบบ 1 ห้องนอน — เรียก show_room_type(type=\"1br\") "
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์"},
            {"id": "type_2br", "label": {"th": "2 ห้องนอน", "en": "2 Bedrooms", "zh": "两室"},
             "ask": "ลูกค้าเลือกห้องแบบ 2 ห้องนอน — เรียก show_room_type(type=\"2br\") "
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์"},
        ),
    },
}

#: The room types as she says them. Thai is matched as whole phrases built on
#: ห้องนอน / สตูดิโอ, never a short word that can sit inside another one (the
#: ราคา-inside-อาคาร trap); "Pilates Studio" is a facility, not a room type.
_ROOM_TYPE_WORDS = (
    re.compile(r"สตูดิโอ|(?<!pilates )\bstudios?\b|开间|单间", re.IGNORECASE),
    re.compile(r"(หนึ่ง|1)\s*ห้องนอน|ห้องนอนเดียว|\b(one|1)[\s-]*bed(room)?s?\b|一室|一房|一居",
               re.IGNORECASE),
    re.compile(r"(สอง|2)\s*ห้องนอน|\b(two|2)[\s-]*bed(room)?s?\b|两室|两房|两居|二室|二房",
               re.IGNORECASE),
)


def _choice(ask_id) -> dict | None:
    for spec in CHOICES.values():
        for item in spec["items"]:
            if item["id"] == ask_id:
                return item
    return None


def choice_set_of(ask_id) -> str | None:
    """Which set a choice id belongs to, or None (a topic, a language, junk)."""
    for name, spec in CHOICES.items():
        if any(item["id"] == ask_id for item in spec["items"]):
            return name
    return None


#: Where her sentences end: ?/! (Thai and CJK too), ". " before a capital,
#: and the Thai particles ค่ะ/คะ before a space. Not a bare "." -- "ตร.ม." and
#: "51.5" sit inside Thai sentences.
_SENTENCE_END = re.compile(r"(?<=[!?。！？])\s*|\.\s+(?=[A-Z])|(?<=ค่ะ)\s+|(?<=คะ)\s+")


def _last_sentence(text: str) -> str:
    parts = [part for part in _SENTENCE_END.split((text or "").strip()) if part and part.strip()]
    return parts[-1] if parts else ""


def choices_asked(said: str) -> str | None:
    """The set her line asks the guest to pick from, or None. The caller has
    already checked that the line ends on a question; only that last sentence
    is read, so an answer that listed the room types and then asked about
    something else ("...Which floor interests you?") gets no type buttons."""
    question = _last_sentence(said)
    if _types_named(question) >= 2:
        return "room_types"
    # "...สองห้องนอน 52 ตร.ม. ค่ะ ลูกค้าสนใจห้องแบบไหนคะ": the types listed just
    # before, and a question that asks for one of them by kind -- not "which
    # floor", which is a different choice.
    if _WHICH_TYPE.search(question) and _types_named((said or "").strip()[-200:]) >= 2:
        return "room_types"
    return None


#: A question that asks for one of the room types just listed.
_WHICH_TYPE = re.compile(r"แบบไหน|ห้องไหน|\bwhich\s+(one|type|unit|room)s?\b|哪种|哪一种|哪个户型",
                         re.IGNORECASE)


def _types_named(text: str) -> int:
    return sum(1 for rx in _ROOM_TYPE_WORDS if rx.search(text or ""))


def choices_for_page(name: str) -> dict:
    """What the page draws for a set: ids and labels, never the words."""
    spec = CHOICES[name]
    return {"type": "choices", "set": name, "hint": dict(spec["hint"]),
            "items": [{"id": item["id"], "label": dict(item["label"])} for item in spec["items"]]}
