"""What the guest was last heard to say, and whether to believe it.

The robot's own voice comes back through its speakers into its own
microphone. The Live API's VAD hears that as somebody starting to talk, cuts
the narration off, and transcribes whatever it caught. In one recorded run
that produced ten sentences chopped mid-word — and this:

    ผู้ช่วย: ...Embassy World พร้อมเป็นส่วนหนึ่งของความฝันนั้นค่ะ
    ลูกค้า: bit like
    ผู้ช่วย: ได้ค่ะ ปิดสไลด์เรียบร้อยแล้วค่ะ

Nobody said "bit like". It is a fragment of Emma's own voice, and it closed
the presentation in front of a guest.

`HALF_DUPLEX=true` cures the cause and costs the ability to talk over the
robot, which the gallery decided it did not want to give up yet. This is the
other half: even with echo present, an *action* should need a word that asks
for it. Answering a question wrongly is a conversation; closing the deck
wrongly ends the demo.

So the destructive slide tools consult this module, and when the words that
would justify them are absent they ask instead of acting. That is a guard on
the consequence, not a guess about the audio — we cannot tell echo from
speech, but we can tell "ปิดสไลด์" from "bit like".
"""
from __future__ import annotations

#: The most recent thing the provider reported hearing. Empty until the
#: guest says something, and reset per session by `forget`.
_LAST: str = ""


def record(text: str) -> None:
    _LAST = (text or "").strip()
    globals()["_LAST"] = _LAST


def last() -> str:
    return _LAST


def forget() -> None:
    globals()["_LAST"] = ""


#: Words that ask for the screen to stop. Kept short and literal: this is a
#: list of things a guest actually says, not a thesaurus. A word missing here
#: costs one confirmation question; a word wrongly here costs a closed deck.
_STOP_TH = (
    "ปิด", "พอ", "พอแล้ว", "หยุด", "เลิก", "จบ", "ออก", "ไม่ดู", "ไม่เอา",
    "พัก", "ไม่ต้อง",
)
_STOP_OTHER = (
    "stop", "close", "enough", "exit", "quit", "end", "done", "finish",
    "关闭", "停", "结束", "그만", "стоп", "закрой", "хватит",
    "やめて", "終わり",
)


#: Things in the room that get switched off, which is not the same request.
#:
#: From the real logs: `ไฟ` 27x, `เปิด` 19x, `ปิด` 15x — switching the lights
#: is the commonest thing anybody says to this robot, by a wide margin.
#:
#: `ปิดไฟ` happens to survive on its own because the tokenizer keeps it as
#: one word, so `ปิด` never appears standalone. `ปิดแอร์` does not: it splits
#: into `ปิด` + `แอร์`, and without this the guard would have read "turn the
#: air conditioning off" as permission to close the presentation. Relying on
#: which compounds a dictionary happens to contain is not a design.
_ROOM_THINGS = ("ไฟ", "แอร์", "เครื่องปรับอากาศ", "ดวงไฟ", "ไฟห้อง",
                "light", "lights", "aircon", "air conditioner", "ac")

#: Words that make it about the screen rather than the room.
_SCREEN_THINGS = ("สไลด์", "พรีเซนต์", "จอ", "นำเสนอ", "หน้าต่าง",
                  "slide", "presentation", "screen", "deck")


def _is_thai(word: str) -> bool:
    return any("฀" <= ch <= "๿" for ch in word)


def asks_to_stop(text: str | None = None) -> bool:
    """Does what we heard contain a word that asks for this?

    Thai is matched as *words*, not substrings. `text in haystack` finds a
    word inside an unrelated one constantly — `ปิด` sits inside `เปิด`, which
    is the opposite instruction, and `พอ` inside `พอดี` and `เพราะ`. The same
    bug already shipped once in `knowledge._is_commercial`, where `งบ` inside
    `ยังไงบ้าง` made the robot refuse ordinary questions. Once is enough.
    """
    haystack = (text if text is not None else _LAST).lower().strip()
    if not haystack:
        return False

    from app.tools.retrieval import tokenize

    words = tokenize(haystack)
    padded = " %s " % " ".join(words) if words else " "

    def _has(terms) -> bool:
        for term in terms:
            if not _is_thai(term):
                if term in haystack:
                    return True
            elif (" %s " % " ".join(tokenize(term))) in padded:
                return True
        return False

    # "ปิดแอร์" is an instruction about the room, not about the screen, and
    # it contains the same verb. Only defer to that reading when nothing in
    # the sentence is about the presentation — "ปิดไฟกับปิดสไลด์ด้วย" asks
    # for both.
    if _has(_ROOM_THINGS) and not _has(_SCREEN_THINGS):
        return False

    return _has(_STOP_OTHER) or _has(_STOP_TH)


#: Words that end a conversation. Same shape and same discipline as the stop
#: list: things people actually say when they leave, not a thesaurus.
#:
#: Deliberately does **not** include the Chinese for "let's go". On
#: 2026-08-24 the recogniser emitted `我们走吧。` — one canned sentence,
#: byte-identical, at 15:24, 17:20, 17:22, 17:31 and 17:48, once only two
#: seconds after a session opened. Nobody said it: it is what a recogniser
#: given a zh-CN hint produces from silence. Four of the day's seven hangups
#: came from it. A phrase that arrives out of nothing must not be able to end
#: a conversation, so the list stays in the languages this assistant is
#: actually spoken to in — and the guard below is what makes the list matter.
_BYE_TH = (
    "บาย", "บ๊ายบาย", "ลาก่อน", "แค่นี้", "พอแล้ว", "ไปแล้ว", "ไปละ",
    "ไว้เจอกัน", "แล้วเจอกัน", "ขอบคุณมาก", "จบแล้ว", "เลิกคุย", "วางสาย",
)
_BYE_OTHER = (
    "bye", "goodbye", "see you", "see ya", "that's all", "thats all",
    "thank you bye", "hang up", "good night", "goodnight",
    # Real goodbyes in the languages guests actually bring to the gallery.
    # Note what is present and what is absent: 再见/拜拜 are how a Chinese
    # speaker ends a conversation; 我们走吧 ("let's go") is not — it is the
    # exact sentence the recogniser hallucinates from silence, and keeping
    # it out is what lets a real 再见 hang up while the phantom cannot.
    "再见", "拜拜", "先走了",
    "さようなら", "またね", "バイバイ",
    "안녕히 계세요", "잘 있어",
    "до свидания", "пока",
    "au revoir", "auf wiedersehen", "adiós", "adios",
)


def asks_to_end(text: str | None = None) -> bool:
    """Does what we heard actually sound like a goodbye?

    Hanging up is the most expensive thing this assistant can do on a
    mishearing: the guest is mid-sentence and the line simply goes. Unlike a
    wrong answer, there is nothing to correct — the conversation is over and
    the person has to start it again.

    Same word-not-substring rule as `asks_to_stop`, for the same reason.
    """
    haystack = (text if text is not None else _LAST).lower().strip()
    if not haystack:
        return False

    from app.tools.retrieval import tokenize

    words = tokenize(haystack)
    padded = " %s " % " ".join(words) if words else " "
    for term in _BYE_OTHER:
        if term in haystack:
            return True
    for term in _BYE_TH:
        if (" %s " % " ".join(tokenize(term))) in padded:
            return True
    return False


#: What to tell the model when it tried to hang up without being asked.
ASK_BEFORE_ENDING = (
    "ยังไม่ได้ยินลูกค้าบอกลา ห้ามวางสาย ให้ถามสั้นๆ ว่าจะให้จบการสนทนาเลยไหม "
    "ถ้าลูกค้ายืนยันค่อยเรียกเครื่องมือนี้อีกครั้ง ถ้าไม่ใช่ให้คุยต่อตามปกติ"
)


#: What to tell the model when it tried to close the deck without being asked.
CONFIRM_FIRST = (
    "ยังไม่ได้ยินลูกค้าสั่งให้ปิด ห้ามปิด ให้ถามสั้นๆ ก่อนว่าต้องการให้ปิดสไลด์ไหม "
    "ถ้าลูกค้ายืนยันค่อยเรียกเครื่องมือนี้อีกครั้ง "
    "ถ้ากำลังพรีเซนต์อยู่ให้พรีเซนต์ต่อตามปกติ"
)
