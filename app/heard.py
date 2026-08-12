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
    if any(word in haystack for word in _STOP_OTHER):
        return True

    from app.tools.retrieval import tokenize

    words = tokenize(haystack)
    if not words:
        return False
    padded = " %s " % " ".join(words)
    for term in _STOP_TH:
        if not _is_thai(term):
            continue
        if (" %s " % " ".join(tokenize(term))) in padded:
            return True
    return False


#: What to tell the model when it tried to close the deck without being asked.
CONFIRM_FIRST = (
    "ยังไม่ได้ยินลูกค้าสั่งให้ปิด ห้ามปิด ให้ถามสั้นๆ ก่อนว่าต้องการให้ปิดสไลด์ไหม "
    "ถ้าลูกค้ายืนยันค่อยเรียกเครื่องมือนี้อีกครั้ง "
    "ถ้ากำลังพรีเซนต์อยู่ให้พรีเซนต์ต่อตามปกติ"
)
