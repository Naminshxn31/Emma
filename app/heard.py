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

import time
from contextvars import ContextVar
from dataclasses import dataclass

#: The most recent thing the provider reported hearing. Empty until the
#: guest says something, and reset per session by `forget`.
_LAST: str = ""

# ---- per-session captions, for the body/nav guards (design §2d step 4) ----
#
# `_LAST` is one global holding the latest *chunk*, from whichever session
# spoke last. Good enough for "did anybody say ปิดสไลด์", wrong for motion:
# a caption that lands *after* the tool call leaves the previous utterance in
# place — "ยกแขนได้ไหม" (a question) would have authorised the arm the model
# raised on the next sentence (safety review #7). So the motion guards read a
# store keyed by session, with the time each caption began and the time of
# the session's latest `activity_start`. The slide tools keep `_LAST`/`last()`
# exactly as they were.


@dataclass(frozen=True)
class Caption:
    """One utterance of one session, as the recogniser captioned it.

    `at` is the monotonic time its *first* chunk arrived: the moment the
    words began reaching us. Freshness and single use are both judged on it,
    so a long sentence cannot keep renewing itself chunk by chunk.
    """

    text: str
    at: float
    utterance: str | None = None
    raw: str = ""


#: id(session) -> (session, Caption). Keyed by id with the object kept for an
#: identity check, not a WeakKeyDictionary: test doubles and the eval harness
#: use bare `object()`, which cannot be weakly referenced. Entries are
#: dropped by `forget(session)` when a session ends.
_CAPTIONS: dict[int, tuple[object, Caption]] = {}
#: id(session) -> (session, monotonic time of the latest activity_start).
_ACTIVITY: dict[int, tuple[object, float]] = {}
#: id(session) -> (session, `at` of the newest caption already spent).
_CONSUMED: dict[int, tuple[object, float]] = {}

#: The session this task belongs to. `VoiceSession.run` binds it before the
#: pumps start, so the provider (which has no reference to its session) can
#: mark `activity_start` against the right conversation — the same per-task
#: mechanism as `registry.session_profile`.
_bound: ContextVar[object | None] = ContextVar("heard_session", default=None)


def bind_session(session: object | None) -> None:
    _bound.set(session)


def _resolve(session: object | None) -> object | None:
    return session if session is not None else _bound.get()


def _get(store: dict, session: object):
    entry = store.get(id(session))
    if entry is None or entry[0] is not session:
        return None
    return entry[1]


def record(text: str, session: object | None = None, *,
           utterance_id: str | None = None, now: float | None = None) -> None:
    """Remember a caption chunk.

    The global `_LAST` keeps today's meaning (the latest chunk, any session).
    With a session (explicit or bound), the chunk also joins that session's
    current caption — but only while it is the same utterance *and* no
    `activity_start` came after the caption began. A new segment of speech
    always starts a new caption, even when the provider keeps the same
    utterance id across it, so "ยกแขนได้ไหม … เอาเลย" cannot become one old
    caption that a later tool call reads as fresh.
    """
    _LAST = (text or "").strip()
    globals()["_LAST"] = _LAST
    owner = _resolve(session)
    if owner is None or not text:
        return
    at = time.monotonic() if now is None else now
    prev = _get(_CAPTIONS, owner)
    act = _get(_ACTIVITY, owner)
    same = (prev is not None and utterance_id is not None
            and prev.utterance == utterance_id
            and not (act is not None and act > prev.at))
    if same:
        raw = prev.raw + text
        caption = Caption(text=raw.strip(), at=prev.at, utterance=utterance_id, raw=raw)
    else:
        caption = Caption(text=text.strip(), at=at, utterance=utterance_id, raw=text)
    _CAPTIONS[id(owner)] = (owner, caption)


def mark_activity(session: object | None = None, *, now: float | None = None) -> None:
    """The provider sent `activity_start` (VAD local): new speech begins.

    Every caption recorded before this moment belongs to an earlier
    utterance and can no longer authorise motion.
    """
    owner = _resolve(session)
    if owner is None:
        return
    _ACTIVITY[id(owner)] = (owner, time.monotonic() if now is None else now)


def latest_caption(session: object | None = None) -> Caption | None:
    """The session's newest caption, fresh or not (for logs and tests)."""
    owner = _resolve(session)
    return None if owner is None else _get(_CAPTIONS, owner)


def caption_for(session: object | None = None, *, max_age_s: float | None = None,
                now: float | None = None) -> Caption | None:
    """The session's latest caption, **only if it may still authorise an action**.

    None unless it began after the session's latest `activity_start` and is
    at most `max_age_s` old (default ROBOT_HEARD_MAX_AGE_S). With server-side
    VAD there are no activity marks and only the age bound applies — the
    weaker case, named in tests/test_heard_fresh.py. Whether it was already
    spent is `consume`'s question, not this one's.
    """
    owner = _resolve(session)
    if owner is None:
        return None
    caption = _get(_CAPTIONS, owner)
    if caption is None:
        return None
    act = _get(_ACTIVITY, owner)
    if act is not None and act > caption.at:
        return None
    if max_age_s is None:
        from app.config import settings

        max_age_s = settings.robot_heard_max_age_s
    clock = time.monotonic() if now is None else now
    if clock - caption.at > max_age_s:
        return None
    return caption


def consume(session: object | None, caption: Caption) -> bool:
    """Spend a caption on one action. False if it (or a later one) was spent.

    One sentence authorises at most one motion: without this a model that
    retries — or calls `move_body` and then `go_to_place` — would move twice
    on one "ยกแขนซ้าย". Shared by body and nav on purpose, so the two guards
    cannot each spend the same caption once.
    """
    owner = _resolve(session)
    if owner is None or caption is None:
        return False
    spent = _get(_CONSUMED, owner)
    if spent is not None and caption.at <= spent:
        return False
    _CONSUMED[id(owner)] = (owner, caption.at)
    return True


def has_words(text: str, terms) -> bool:
    """Does `text` contain one of `terms` as whole words?

    Every term is tokenized the way the text is, and must appear as a run of
    whole tokens. Never a substring: `ยก` sits inside `ยกเลิก` and `ราคา`
    shares `าคา` with `อาคาร` (CLAUDE.md, twice). Latin terms go through the
    same path, so "stop" does not match "stopwatch".
    """
    from app.tools.retrieval import tokenize

    words = tokenize((text or "").lower())
    if not words:
        return False
    padded = " %s " % " ".join(words)
    for term in terms or ():
        parts = tokenize((term or "").lower())
        if parts and (" %s " % " ".join(parts)) in padded:
            return True
    return False


#: A place named in a sentence that refuses or asks about it is not a request
#: to go there (review #18): "อย่าไปห้องประชุมใหญ่", "ไม่ต้องไปห้องประชุม" and
#: "ห้องประชุมใหญ่อยู่ไหน" all named big_room and passed the place guard.
#: Whole words only, like every list in this module.
#: Review 2 #11 (real tokenizer + floor2.json, 9 Oct) added "not that one"
#: and "not back": "ไม่ใช่ห้องประชุมใหญ่" is ไม่ ใช่ (bare ไม่ is not a decline
#: word — "ไม่ไกล"), and "ยังไม่กลับแท่นชาร์จ" passed the dock branch because
#: only ไม่ไป was listed. ไม่ต้องการ is one token, so ไม่ต้อง never matched it.
_DECLINE_WORDS = ("อย่า", "ไม่ต้อง", "ไม่ต้องการ", "ไม่ไป", "ไม่กลับ", "ไม่เดิน",
                  "ไม่เอา", "ไม่อยาก", "ไม่ใช่", "ยังไม่", "ห้าม",
                  "don't", "dont", "do not", "not", "never", "didn't", "won't")
#: Asking the robot to stop, cancel or wait names the place as often as a go
#: does, and is the worst sentence to read as one: the guard is there to catch
#: the model that hears "หยุดไปห้องประชุมใหญ่" / "cancel the big meeting room"
#: and calls go_to_place (review 2 #11). Stop itself is never guarded — this
#: only stops the *walk* from being authorised by it.
_STOP_WORDS = ("หยุด", "ยกเลิก", "เลิก", "พอ", "พอแล้ว", "เดี๋ยวก่อน", "รอก่อน",
               "เดี๋ยวค่อย", "ไว้ก่อน", "ทีหลัง",
               "stop", "cancel", "never mind", "nevermind", "hold on", "wait")
#: A trip that already happened (or is asked about) is news, not a request:
#: "ผมเพิ่งไปห้องประชุมใหญ่มา", "เคยไปห้องประชุมใหญ่ไหม", "เมื่อกี้ไป
#: ห้องประชุมมา", "I just came from the big meeting room". Refused even with
#: a take-word: "เคยพาลูกค้าไปห้องประชุมใหญ่ไหม" asks about the past too.
#: เมื่อกี๊ splits into เมื่อ กี๊; a term is tokenized like the text, so the
#: run matches.
_PAST_WORDS = ("เคย", "เพิ่ง", "เมื่อกี้", "เมื่อกี๊", "ตะกี้", "เมื่อวาน", "เมื่อเช้า",
               "came", "been", "went", "visited")
#: ...and so is a sentence that *ends* in มา / มาแล้ว ("ไปห้องประชุมใหญ่มา" =
#: "went there and came back"), once trailing particles are skipped. Only
#: the last word: "กลับมา" is its own token, so "ไปห้องประชุมใหญ่แล้วกลับมา"
#: (go and come back) still walks.
_PAST_TAILS = (("มา",), ("มา", "แล้ว"))
#: A where-question asks for directions. Walking the guest there is a bigger
#: answer than the one asked for, so it needs a word that asks to be taken.
#: The integrator's check of #18 added the questions *about* the place that
#: still named big_room: "ห้องประชุมใหญ่ไปยังไง", "...ไกลไหม", "...มีกี่ที่นั่ง",
#: "Is the big meeting room far?". Same rule: without a take-word they ask,
#: they do not request ("พาไปห้องประชุมใหญ่ ไม่ไกลใช่ไหม" still walks).
_WHERE_WORDS = ("ไหน", "ที่ไหน", "อยู่ไหน", "ทางไหน", "where",
                "ยังไง", "อย่างไร", "ไกล", "กี่", "how", "far")
#: Deliberately not ไป: "ห้องประชุมใหญ่ไปทางไหน" is the question, not the
#: request. พา / take / bring are what a guest says when he wants company.
_TAKE_WORDS = ("พา", "พาไป", "take", "bring", "lead")


def _ends_with(text: str, tails) -> bool:
    """Does the caption end in one of `tails` (word tuples), past particles?"""
    from app import robot_contracts as rc
    from app.tools.retrieval import tokenize

    core = rc.strip_particles(tokenize((text or "").lower()))
    return any(len(core) >= len(t) and tuple(core[-len(t):]) == t for t in tails)


def declines_or_asks_where(text: str | None) -> bool:
    """Does this caption refuse, stop or recall the walk, or only ask about
    the place?

    For the place-name heard guard (`robot_nav._heard_place`): a caption
    for which this is True must not authorise a walk, whatever place it
    names. Errs toward True — a refused walk costs one question, a wrong
    one walks a guest who said "don't" across the building.

    Two tiers, on purpose. A refusal, a stop/cancel/wait or the past wins
    over everything, a take-word included ("หยุด ไม่ต้องพาไปห้องประชุมใหญ่").
    A where-question, a yes/no question (`robot_contracts.asks_yes_no`:
    "ห้องประชุมใหญ่สวยไหม", "ต้องกลับแท่นไหม", "Is the big meeting room
    nice?") only asks — unless the guest also asked to be taken there.
    "...ได้ไหม" (please may I) and "...กันไหม" (shall we) are requests.
    """
    if not text or not text.strip():
        return False
    if (has_words(text, _DECLINE_WORDS) or has_words(text, _STOP_WORDS)
            or has_words(text, _PAST_WORDS) or _ends_with(text, _PAST_TAILS)):
        return True
    if has_words(text, _TAKE_WORDS):
        return False
    if has_words(text, _WHERE_WORDS):
        return True
    from app import robot_contracts as rc
    from app.tools.retrieval import tokenize

    return rc.asks_yes_no(tokenize(text.lower()))


def last() -> str:
    return _LAST


def forget(session: object | None = None) -> None:
    """No argument: forget everything (a new conversation owns the room).
    With a session: drop only that session's captions and marks."""
    if session is None:
        globals()["_LAST"] = ""
        _CAPTIONS.clear()
        _ACTIVITY.clear()
        _CONSUMED.clear()
        return
    for store in (_CAPTIONS, _ACTIVITY, _CONSUMED):
        entry = store.get(id(session))
        if entry is not None and entry[0] is session:
            del store[id(session)]


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
