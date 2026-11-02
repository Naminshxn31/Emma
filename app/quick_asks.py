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
#: The language menu (owner 29 Oct: "ไทย อังกฤษ จีน จะเพิ่มรัสเซีย"): LANGS
#: are the languages the topics and hints are written in; a language on the
#: menu needs no topic text of its own -- picking it pins the conversation
#: (app/talk_lang.py `pin`) and every later tap says it by name.
PICK_LANGS = ("th", "en", "zh", "ru")

_LANG_NAMES_TH = {"th": "ไทย", "en": "อังกฤษ", "zh": "จีน", "ru": "รัสเซีย"}

# ---- the conversation's language, for a tap (owner, 15 Oct 2026) -----------
# Talking English, a tap on a story button got a Thai answer: the tap's user
# turn was Thai text around a Thai label, and "answer in the language the
# guest spoke last" lost to the language of the turn itself. When the server
# knows what language the talk is in (her latest reply, else the guest's
# words), the tap now says it outright -- and in that language.

_TAP_PREAMBLE = {
    "th": "[ลูกค้าแตะปุ่ม \"%s\" บนจอ ไม่ได้พูด — ตอนนี้คุยกันเป็นภาษาไทย ตอบเป็นภาษาไทย ห้ามเปลี่ยนภาษา] ",
    "en": "[The guest tapped \"%s\" on the screen and did not speak. This conversation is in English: "
          "answer in English, do not switch language.] ",
    "zh": "[The guest tapped \"%s\" on the screen and did not speak. This conversation is in Chinese: "
          "answer in Chinese, do not switch language.] ",
}
_TAP_CALL = {"th": "เรียก %s แล้วเล่าตามผลลัพธ์", "en": "Call %s and tell it from the result.",
             "zh": "Call %s and tell it from the result."}

# ---- any other language the server can tell (audit 27 Oct) -------------------
# The tap's language came from `script_lang`, which reads every Latin script
# as English and kanji as Chinese: in a German talk a tap said "This
# conversation is in English: answer in English, do not switch language", in
# a Japanese one "in Chinese" -- the tap itself switched her. session._quick_ask
# now asks the provider's TalkLanguage first (app/talk_lang.py, from what the
# GUEST said), and a language with no wording of its own here gets this one
# preamble naming it -- a bracketed stage direction like the others, never a
# sentence in that language for her to repeat.

#: English names for the codes app/talk_lang.py tells (its NAMES are Thai).
#: Every one of them (review 29 Oct, tests/test_talk_lang_vietnamese.py): talk_lang
#: learnt "vi" on 28 Oct and this table did not, so a tap in a Vietnamese talk
#: fell back to the Thai "answer in the language spoken last" turn -- the one
#: a Thai tap text beats (owner 15 Oct, the reason `talk` exists).
_LANG_NAMES_EN = {"th": "Thai", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
                  "ru": "Russian", "he": "Hebrew", "ar": "Arabic", "hi": "Hindi", "de": "German",
                  "fr": "French", "es": "Spanish", "it": "Italian", "vi": "Vietnamese"}
_TAP_PREAMBLE_NAMED = ("[The guest tapped \"%s\" on the screen and did not speak. This conversation is in %s: "
                       "answer in %s, do not switch language.] ")


def tap_talk(code) -> str | None:
    """`code` when a tap can say the conversation is in it (th/en/zh, or any
    other language app/talk_lang.py tells), else None -- the caller then uses
    the old "answer in the language the guest spoke last" wording."""
    return code if isinstance(code, str) and code in _LANG_NAMES_EN else None


def talk_name_en(code) -> str | None:
    """The language's English name for a bracketed instruction ("German"),
    or None for a code `tap_talk` does not take."""
    return _LANG_NAMES_EN.get(code) if tap_talk(code) else None


def script_lang(text) -> str | None:
    """th / en / zh by the letters of `text`, or None when it says too little.
    Thai wins on a handful of Thai letters: a Thai reply is full of English
    names (Water Lagoon, Sky Pool), while an English reply carries at most a
    closing "ค่ะ". Kana is Japanese, which this cannot name: None, not the
    "zh" its kanji used to read as (audit 27 Oct)."""
    if not isinstance(text, str):
        return None
    thai = sum(1 for c in text if "฀" <= c <= "๿")
    kana = sum(1 for c in text if "぀" <= c <= "ヿ")
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    if thai >= 8:
        return "th"
    if kana:
        return None
    if cjk >= 4:
        return "zh"
    if latin >= 8:
        return "en"
    return None


def tap_preamble(label: dict, talk: str) -> str:
    """The marked-as-a-tap opening in the conversation's own language (any
    code `tap_talk` takes; one outside LANGS gets the English label)."""
    shown = label.get(talk) or label.get("en") or label.get("th") or ""
    if talk in _TAP_PREAMBLE:
        return _TAP_PREAMBLE[talk] % shown
    name = _LANG_NAMES_EN[talk]
    return _TAP_PREAMBLE_NAMED % (shown, name, name)


def tap_call(call: str, talk: str) -> str:
    return _TAP_CALL.get(talk, _TAP_CALL["en"]) % call

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
#: The event host is the sales host at an event (app/event_host.py): the same
#: taps, the same language menu.
TOPICS["event"] = TOPICS["condo"]

#: The language menu. Picking one is the guest saying which language -- and,
#: since 29 Oct, the only language she answers in for the rest of the call
#: (app/talk_lang.py `pin`). `label` in the language itself, `sub` its
#: English name for whoever reads neither script.
LANGUAGE_BUTTONS = (
    {"id": "th", "label": "ไทย", "sub": "Thai"},
    {"id": "en", "label": "English", "sub": ""},
    {"id": "zh", "label": "中文", "sub": "Chinese"},
    {"id": "ru", "label": "Русский", "sub": "Russian"},
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


def picked_language(ask_id) -> str | None:
    """The language a `lang_xx` id picks from the menu, or None."""
    if isinstance(ask_id, str) and ask_id.startswith("lang_") and ask_id[5:] in PICK_LANGS:
        return ask_id[5:]
    return None


def text_for(profile: str, ask_id, lang=None, talk=None, cut: bool = False) -> str | None:
    """The user turn for a tap, or None for an id this profile does not have.
    `lang` is the page's language (its buttons, else the script of the
    guest's last utterance); the model still answers in the language the
    guest is speaking, if any. `talk` is the conversation's language as the
    server knows it (the provider's TalkLanguage, else `script_lang`): when
    given, the tap says it outright -- a language with no ask of its own here
    gets the English one under a preamble that names it."""
    if not isinstance(ask_id, str):
        return None
    code = _lang(lang)
    talk = tap_talk(talk)
    if ask_id.startswith("lang_"):
        target = picked_language(ask_id)
        if target is None or not TOPICS.get(profile):
            return None
        # `cut`: the pick stopped her mid-answer (the page says so -- review 29
        # Oct: the answer was lost, and "one short sentence, then stop" never
        # gave it back). Somebody who switched language mid-answer did not
        # follow it: it is said again, whole, in the new language.
        if target == "th":
            now = ("ลูกค้าเลือกขณะที่คุณกำลังตอบ จึงยังไม่ได้ฟังคำตอบล่าสุดจนจบ ตอนนี้ตอบคำตอบล่าสุดนั้นใหม่ทั้งหมดเป็นภาษาไทย"
                   if cut else "ตอนนี้ตอบรับสั้นๆ ประโยคเดียวเป็นภาษาไทย แล้วหยุด ห้ามถามต่อ")
            return ("[ลูกค้าแตะปุ่มเลือกภาษาบนจอ ไม่ได้พูด] ลูกค้าเลือกภาษาไทย — ตั้งแต่นี้ตอบเป็นภาษาไทยเท่านั้น"
                    " แม้ได้ยินภาษาอื่น จนกว่าลูกค้าจะเลือกใหม่หรือขอเปลี่ยนภาษาตรงๆ " + now)
        # Any other pick in English, naming the language: a Thai turn is what
        # a tap's answer comes back in (owner 15 Oct), and this one turn sets
        # the language of every answer after it. A stage direction, never a
        # sentence in that language for her to repeat.
        name = _LANG_NAMES_EN[target]
        now = ("They chose it while you were answering and did not hear the rest: now give your last "
               "answer again, whole, in %s." % name if cut else
               "Now say one short sentence in %s that you will speak %s, then stop; do not ask anything."
               % (name, name))
        return ("[The guest chose %s on the screen's language menu and did not speak.] From now on answer "
                "only in %s, even when you hear another language, until the guest chooses again or plainly "
                "asks for another language. " % (name, name)) + now
    for topic in TOPICS.get(profile) or ():
        if topic["id"] == ask_id and talk:
            return tap_preamble(topic["label"], talk) + topic["ask"].get(talk, topic["ask"]["en"])
        if topic["id"] == ask_id:
            return ("[ลูกค้าแตะปุ่ม \"%s\" บนจอ ไม่ได้พูด — ตอบเป็นภาษาเดียวกับที่ลูกค้าพูดล่าสุด "
                    "ถ้ายังไม่ได้พูด ตอบเป็นภาษา%s] %s"
                    % (topic["label"]["th"], _LANG_NAMES_TH[code], topic["ask"][code]))
    item = _choice(ask_id)
    if item is not None and TOPICS.get(profile) and talk:
        return tap_preamble(item["label"], talk) + (item["ask"] if talk == "th" else item["ask_en"])
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
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์",
             "ask_en": "The guest chose the studio -- call show_room_type(type=\"studio\") "
                       "to show a sample room plan on the screen, then tell it from the result."},
            {"id": "type_1br", "label": {"th": "1 ห้องนอน", "en": "1 Bedroom", "zh": "一室"},
             "ask": "ลูกค้าเลือกห้องแบบ 1 ห้องนอน — เรียก show_room_type(type=\"1br\") "
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์",
             "ask_en": "The guest chose the one-bedroom -- call show_room_type(type=\"1br\") "
                       "to show a sample room plan on the screen, then tell it from the result."},
            {"id": "type_2br", "label": {"th": "2 ห้องนอน", "en": "2 Bedrooms", "zh": "两室"},
             "ask": "ลูกค้าเลือกห้องแบบ 2 ห้องนอน — เรียก show_room_type(type=\"2br\") "
                    "เพื่อขึ้นผังห้องตัวอย่างบนจอ แล้วเล่าตามผลลัพธ์",
             "ask_en": "The guest chose the two-bedroom -- call show_room_type(type=\"2br\") "
                       "to show a sample room plan on the screen, then tell it from the result."},
        ),
    },
}

#: The room types as she says them. Thai is matched as whole phrases built on
#: ห้องนอน / สตูดิโอ, never a short word that can sit inside another one (the
#: ราคา-inside-อาคาร trap); "Pilates Studio" is a facility, not a room type.
#: Russian since the language menu offered it (29 Oct): her Russian question
#: about the types drew no buttons (review 29 Oct).
_ROOM_TYPE_WORDS = (
    re.compile(r"สตูดิโอ|(?<!pilates )\bstudios?\b|开间|单间|студи[яюиейё]", re.IGNORECASE),
    re.compile(r"(หนึ่ง|1)\s*ห้องนอน|ห้องนอนเดียว|\b(one|1)[\s-]*bed(room)?s?\b|一室|一房|一居"
               r"|(одн(ой|а|у|им)|1)[\s-]*спальн|однокомнатн", re.IGNORECASE),
    re.compile(r"(สอง|2)\s*ห้องนอน|\b(two|2)[\s-]*bed(room)?s?\b|两室|两房|两居|二室|二房"
               r"|(дв(умя|е|ух)|2)[\s-]*спальн|двухкомнатн", re.IGNORECASE),
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
_WHICH_TYPE = re.compile(r"แบบไหน|ห้องไหน|\bwhich\s+(one|type|unit|room)s?\b|哪种|哪一种|哪个户型"
                         r"|как(ой|ая|ую|ие)\s+(тип|вариант|квартир)", re.IGNORECASE)


def _types_named(text: str) -> int:
    return sum(1 for rx in _ROOM_TYPE_WORDS if rx.search(text or ""))


def choices_for_page(name: str) -> dict:
    """What the page draws for a set: ids and labels, never the words."""
    spec = CHOICES[name]
    return {"type": "choices", "set": name, "hint": dict(spec["hint"]),
            "items": [{"id": item["id"], "label": dict(item["label"])} for item in spec["items"]]}
