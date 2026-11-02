"""What Emma says offline: rules over what she heard, never a model.

`route(text, lang, state)` -> a Route: a story (its approved lines), one of
the approved fixed lines, or silence. In order:

0. **A stop** ("หยุด", "stop") -> `stop_route`: the stop tool, never a line.
1. **Commercial** (`retrieval.is_commercial`, the server's own gate) and
   **one specific unit / what is still available** -> `handoff_commercial`.
   Prices and availability live in the facts and the live inventory; the
   fixed lines carry no numbers by rule, so offline they go to the sales team.
   That line names no price (review 26 Oct): is_commercial also catches
   opening hours, completion, ownership, and "จอง" catches booking a meeting
   room -- a line saying "for prices..." to "what time does the gym open?"
   would be Emma raising price herself, which the owner forbids (8 Oct).
2. **A story** (`stories.story_for_query`, the router search_condo_info
   already hands story questions to) behind a CONTENT GATE. Measured on the
   real guest table (understand.json, answerable): the matcher answered 69
   of its 71 project hits right -- but online the model ignores the rest of
   what it fires on, and offline nothing does: a bare "ไม่", "ไม่มีนะ" or
   "ไม่มีอะไร" mapped to nearby_hospitals, "มัน" and "7 7" to nearby_7eleven.
   The gate: the match must stand on a word that names something (not a
   particle, pronoun, number or asking word) AND that word must be one of
   the matched story's own question words. Numbers in docs/emma-offline-mode.md.
3. **Greeting** ("hello", "สวัสดี", "what's your name") -> the offline
   greeting once per conversation, then her one project question (`invite`).
   **Thanks / goodbye** -> the acknowledgement.
4. **Off topic** (weather, recipes, songs, translation, general knowledge)
   -> the redirect (owner 15 Oct: "ห้ามตอบเนื้อหาแม้คำเดียว").
5. **A question about something else** -> the menu of what she can tell,
   once per language per conversation and only where stories are offered,
   then the hand-off to the sales team (which promises nothing: it is said
   exactly when a question did not match a story).
6. **Anything else is silence.** 63% of what passed the robot's listening
   gate in the logs was background talk, her own echo or misheard fragments
   (answerable: 636 of 1,004). Online the model shrugs those off; a router
   that answered them would talk to the room all day.

**Not spoken offline, on purpose** -- measured on the same table:
* report FAQ answers: only 10 of the 55 FAQ-matched questions got an answer
  speakable as-is; 31 came back in the other language (an English alias
  returns the Thai answer), others carry notes to the bot, source citations
  and dimension strings. They are reference text for a model, not lines.
* report passages and slide captions: wrong 53% / 44% of the time, and they
  fire on 48% / 25% of background talk. With no model to filter, a wrong
  answer would be spoken with full confidence.

Measured on the real table (scratchpad, 26 Oct; 988 heard utterances, the
16 taps go through the tap path): background talk / echo / fragments answered
17 of 636 (2.7% -- 222 before the "addressed" gate below); project questions
(202): a story 69 (judged 67 right, the rest partly, none wrong), the menu
112, silence 20, the sales hand-off 1; price questions (19): hand-off 18;
greetings (71): the greeting 7, the acknowledgement 3, silence 61. Story
fires on noise/greetings: 12 raw -> 5 behind the content gate -> 0 with the
one-word rule. The bigger lever is story recall, not the router: 78 of the 99
project questions without a full answer are on a topic that HAS a story.

Nothing here ever writes a word: every Route names an approved line or an
approved story, and the provider says only those.
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger("condo_voice.offline.router")


@dataclass
class Route:
    #: "story" | "line" | "silent" | "stop"
    action: str
    #: the fixed line's id (action "line")
    line: str | None = None
    #: the story's id (action "story")
    topic: str | None = None
    #: why, as a category for the turn log (never the guest's words)
    why: str = ""


@dataclass
class RouterState:
    """One conversation's memory: what was already said, so the menu is not
    recited twice and the greeting is not repeated to the same guest."""
    greeted: bool = False
    menu_langs: set = field(default_factory=set)
    last_line: str | None = None
    last_line_at: float = -1e9


#: The same fixed line twice inside this many seconds is not said the second
#: time: a hand-off repeated to whatever the room says next is nagging.
REPEAT_GAP_S = 30.0

# ---- words ---------------------------------------------------------------------

#: Words that carry no topic, on top of retrieval.stopwords() (pythainlp's
#: list + the search's own question frames). Each one is a false fire of the
#: story matcher on the real table, or the kind of word background talk is
#: made of. Thai goes through the same tokenizer as the query (NFKC splits
#: ำ, so a raw string would never match the token).
_STOP_EXTRA = (
    "ไม่", "มัน", "นะ", "อะ", "อ่ะ", "เออ", "อือ", "อืม", "เอ่อ", "โอเค", "ok", "okay", "ครับผม", "จ้ะ", "จ้า",
    "ใช่", "เหรอ", "หรอ", "แหละ", "ล่ะ", "เลย", "แล้ว", "ก็", "จะ", "ว่า", "ที่", "นี้", "นั้น", "นี่", "โน่น",
    "เขา", "เรา", "ผม", "ฉัน", "ดิฉัน", "หนู", "คุณ", "เธอ", "มึง", "กู", "แก", "พี่", "น้อง",
    "หน่อย", "ช่วย", "ขอ", "แนะนำ", "บอก", "เล่า", "อยาก", "รู้", "ดู", "ค่ะ", "คะ", "ครับ", "จ๊ะ",
    "yes", "no", "yeah", "yep", "nope", "so", "um", "uh", "oh", "ah", "hmm", "well", "like", "just",
    "know", "want", "see", "let", "get", "go", "it's", "that", "that's", "be", "was", "were", "not",
    "my", "our", "they", "he", "she", "him", "her", "them", "us", "too", "also", "very", "really",
    "please", "tell", "me", "about", "show", "can", "could", "you", "i", "to",
)
#: Matched as substrings on purpose: neither sits inside another Thai word
#: (สวัสดิการ is spelled with ิ, not ี).
_GREETING_TH = ("สวัสดี", "หวัดดี")
_GREETING_EN = re.compile(r"^\W*(hello|hi|hey|good (morning|afternoon|evening)|greetings)\b", re.I)
_NAME_Q = re.compile(r"what(?:'s| is) your name|who are you|ชื่ออะไร|คุณคือใคร|เธอคือใคร|ชื่อเล่น", re.I)
_THANKS = re.compile(r"ขอบคุณ|ขอบใจ|thank|thanks|thx|cheers|ลาก่อน|บ๊ายบาย|บายบาย|good ?bye|bye\b|"
                     r"see you|แล้วเจอกัน", re.I)
#: Off topic: things a guest asks a talking robot that are not the project.
#: Matched in the words, Thai as tokens (CLAUDE.md: never a substring of Thai).
_OFF_EN = re.compile(
    r"\b(weather|rain(ing)?|recipe|cook(ing)?|song|sing|dance|joke|news|football|soccer|movie|"
    r"translate|translation|what does .{1,30} mean|how do you say|spell|"
    r"president|prime minister|stock market|bitcoin|crypto|math|calculate|"
    r"how old are you|are you (a )?(robot|human|real|ai)|do you have feelings|girlfriend|boyfriend|"
    r"married|lottery)\b", re.I)
_OFF_TH = ("อากาศ", "ฝน", "สูตร", "ทำอาหาร", "เพลง", "ร้องเพลง", "เต้น", "มุก", "ตลก", "ข่าว", "ฟุตบอล",
           "หนัง", "แปลว่า", "แปล", "ภาษาอังกฤษว่า", "สะกด", "นายก", "หุ้น", "หวย", "ลอตเตอรี่",
           "แฟน", "แต่งงาน", "อายุเท่าไหร่", "หุ่นยนต์")
#: Asking: a question or a request, the sign that words were addressed to
#: her. Thai question words / request verbs as tokens; English by shape.
_ASK_TH = ("ไหม", "มั้ย", "หรือเปล่า", "รึเปล่า", "อะไร", "ยังไง", "อย่างไร", "เท่าไหร่", "เท่าไร", "กี่",
           "ที่ไหน", "ตรงไหน", "ไหน", "เมื่อไหร่", "เมื่อไร", "ทำไม", "ใคร", "บ้าง", "หรือยัง",
           "ขอ", "อยาก", "ช่วย", "แนะนำ", "บอก", "เล่า", "คะ", "หรอ", "เหรอ", "มีไหม")
_ASK_EN = re.compile(
    r"\?|^\W*(what|where|when|why|which|who|how|is|are|do|does|did|can|could|will|would|should|"
    r"may|have|has|tell|show|give|explain|describe)\b|\b(i want|i'd like|i would like|"
    r"tell me|show me|can i|could i|i'm looking|i am looking|recommend|"
    r"let'?s (talk|speak) about|what about|how about)\b", re.I)
#: Spoken TO her in English ("can you...", "what can you tell me"): enough to
#: answer with the menu even when no project word was recognised -- a guest's
#: "Can you show me more pictures?" deserves the menu, not silence. Thai has
#: no such marker that the room's own talk lacks.
_TO_HER_EN = re.compile(r"\b(can|could|would|will|do) you\b|\bwhat can you\b|\btell me\b|"
                        r"\bshow me\b|\bemma\b", re.I)
#: One specific unit, or what is still on sale: live inventory (network).
_UNIT_EN = re.compile(
    r"\b[a-f]\s*-?\s*\d{3,4}\b|\b(unit|room)\s*(no\.?|number)?\s*\d{3,4}\b|\bavailab|\bstill (free|open|left)\b|"
    r"\b(sold|left|remaining|vacant)\b|\bbook(ing)?\b|\breserv", re.I)
#: ...and the weaker kind, asked only when no story answered: "which room has
#: the best view" is the views story's (judged right on the real table) before
#: it is a unit search.
_UNIT_ONE_EN = re.compile(r"\bthis (unit|apartment)\b|\bfloor plan of (this|the|that|my) (unit|room|apartment)\b|"
                          r"\bwhich (unit|room|apartment)\b|\brecommend (me )?(a|an|the) (unit|room|apartment)\b",
                          re.I)
_UNIT_TH_WORDS = ("ว่าง", "ห้องว่าง", "เหลือ", "ขายแล้ว", "จอง", "ขายหมด")
#: Asks for ONE unit by itself (no "ว่าง" needed): which room, recommend a
#: room, a corner room -- live inventory online (find_units / show_unit).
_UNIT_TH_PHRASES = ("ห้องไหน", "ยูนิตไหน", "แนะนำห้อง", "แนะนำยูนิต", "ห้องมุม")
#: Money the server's commercial gate does not list (the real table: it
#: missed "cheapest one bedroom" twice and a free washing machine).
_MONEY_EN = re.compile(r"\b(cheapest|most expensive|for free|free of charge|discount)\b", re.I)
_MONEY_TH = ("ล้าน", "บาท", "ถูกที่สุด", "ราคาถูก", "แพง", "ฟรี", "ส่วนลด")
_ROOM_NO = re.compile(r"(?<![0-9])(?:[a-fA-F]\s*-?\s*)?[0-9]{3,4}(?![0-9])")
#: People talking to each other, not to her: the slang, the vocatives and
#: the third person that fill the background talk the robot's mic hears
#: (the real table: 204 of 636 noise utterances carry one, 3 of 213 project
#: questions). Not "หนู" -- a guest may call the robot that.
_HUMAN_TALK = ("วะ", "แม่ง", "กู", "มึง", "เฮ้ย", "ไอ้", "ป่ะ", "เนาะ", "อ่ะ", "เงี้ย", "เนี่ย", "เนี้ย", "พี่",
               "เจ๊", "แก", "เขา", "อ๋อ", "เออ", "นาย", "น้อง", "งั้น", "ดิ", "สิ", "อะ", "แหละ", "เว้ย",
               "ว่ะ", "โว้ย", "ตังค์", "มั้ง",
               # people pointing at something together: "...ใช่ไหม" (37 noise,
               # 0 project questions), "อันนี้" (23 / 0), "รู้สึก" (7 / 0)
               "ใช่ไหม", "ใช่มั้ย", "ใช่ป่ะ", "อันนี้", "รู้สึก")
#: Project words beyond the stories' own question words, so the gate still
#: knows a project question while the story catalog is off or not approved.
_PROJECT_WORDS = ("ห้อง", "ห้องนอน", "แบบห้อง", "ห้องน้ำ", "ระเบียง", "สตูดิโอ", "เฟอร์นิเจอร์",
                  "โครงการ", "คอนโด", "ตึก", "อาคาร", "ชั้น", "สระ", "ฟิตเนส", "ราคา", "ทำเล",
                  "ส่วนกลาง", "ที่จอดรถ", "วิว", "ทะเล", "หาด", "สนามบิน", "ผู้พัฒนา", "บริษัท",
                  "room", "rooms", "unit", "units", "project", "condo", "building", "buildings", "floor",
                  "floors", "pool", "gym", "price", "location", "beach", "parking", "view", "facilities",
                  "developer", "bedroom", "studio", "embassy")
#: The longest utterance (letters, no spaces) answered with a fixed line.
#: Real project questions: 173 of 213 are within 80; the background talk
#: that slips past every other test is mostly long monologue.
MAX_LETTERS = 80


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


_words_lock = threading.Lock()
_STOP: frozenset | None = None
_TH_SETS: dict | None = None


def _tokenize(text: str) -> list[str]:
    from app.tools.retrieval import tokenize

    return tokenize(text)


def _token_set(words) -> frozenset:
    return frozenset(t for w in words for t in _tokenize(w))


def _phrases(words) -> tuple:
    """Each term as the token sequence the tokenizer makes of it. A term is
    matched as that whole sequence, never token by token: split, "อายุ
    เท่าไหร่" made every "...เท่าไหร่" off topic, "ห้องว่าง" made every "ห้อง"
    a unit question and "มีไหม" made every "มี" a question (all measured on
    the real table on the first run). The is_commercial rule, one level up."""
    return tuple(p for p in (tuple(_tokenize(w)) for w in words) if p)


def _contains(tokens: list[str], phrases: tuple) -> bool:
    hay = " %s " % " ".join(tokens)
    return any((" %s " % " ".join(p)) in hay for p in phrases)


def _sets() -> tuple[frozenset, dict]:
    """The word lists, through the tokenizer the query goes through. Built
    lazily (after the slide index taught the tokenizer the deck's words)."""
    global _STOP, _TH_SETS
    with _words_lock:
        if _STOP is None:
            from app.tools.retrieval import stopwords

            _STOP = frozenset(stopwords()) | _token_set(_STOP_EXTRA) | frozenset(_STOP_EXTRA)
            _TH_SETS = {"off": _phrases(_OFF_TH), "ask": _phrases(_ASK_TH),
                        "unit": _phrases(_UNIT_TH_WORDS),
                        "human": _phrases(_HUMAN_TALK), "project": _phrases(_PROJECT_WORDS),
                        "room": _phrases(("ห้อง", "ยูนิต", "unit", "room")),
                        "room_no": _phrases(("ห้อง", "ยูนิต", "เบอร์")),
                        "unit_one": _phrases(_UNIT_TH_PHRASES), "money": _phrases(_MONEY_TH)}
        return _STOP, _TH_SETS


def reset() -> None:
    """Tests: rebuild the word sets after the tokenizer changes."""
    global _STOP, _TH_SETS
    _STOP = None
    _TH_SETS = None


def content_words(text: str) -> list[str]:
    """The words that name something: no particles, pronouns, asking words,
    numbers or single letters. Order kept."""
    stop, _ = _sets()
    out = []
    for word in _tokenize(text):
        w = word.strip().lower()
        if not w or w in stop or len(w) < 2 or not any(ch.isalpha() for ch in w):
            continue
        out.append(w)
    return out


# ---- the slide index first, as the server does ---------------------------------

_prepared = False


def prepare() -> None:
    """Teach the tokenizer the deck's words before the first query, the way
    the server's startup warm-up does (stories.story_for_query was tuned
    measured that way -- 15 Oct: "the tokenizer gets the deck's words then
    splits Thai differently; the first measurement was the wrong layer").
    Blocking; call it off the event loop. Never raises."""
    global _prepared
    if _prepared:
        return
    try:
        from app.tools import slide_search
        from app.tools.slides import load_slides

        slide_search.get_index(load_slides())
    except Exception:
        logger.warning("offline router: slide index not built -- Thai words may split "
                       "differently from the server's story matcher", exc_info=True)
    reset()
    _prepared = True


# ---- the gate -------------------------------------------------------------------

def story_for(text: str, stories_on: bool = True) -> tuple[str | None, str]:
    """(story id, "") or (None, why): the matcher behind the content gate.
    `stories_on` = present_story is offered on this session (the provider
    asks the registry); off, offline tells no story either."""
    from app import stories
    from app.config import settings

    if not stories_on or not settings.stories_enabled:
        return None, "stories_off"
    try:
        offered = stories.available()
    except Exception:
        logger.debug("stories unavailable offline", exc_info=True)
        return None, "no_stories"
    if not offered:
        return None, "no_stories"
    topic = stories.story_for_query(text, offered)
    if topic is None:
        return None, "no_match"
    words = content_words(text)
    if not words:
        return None, "gate_no_content"
    docs = (stories._QUERY_INDEX.get("docs") or {}).get(topic) or set()
    if not any(w in docs or (w.endswith("s") and w[:-1] in docs) for w in words):
        return None, "gate_off_story"
    return topic, ""


def _has_thai(text: str) -> bool:
    return any("฀" <= ch <= "๿" for ch in text)


#: The offline Thai recognizer hears the rising question particle "ไหม" as
#: "ใหม่" (new) at the end of a question: 4 of the 16 synthetic Thai
#: questions that lost their route on this PC (zipformer-thai, 26 Oct).
#: Gemini's own transcripts end on "ใหม่" twice in 1,004 real utterances,
#: both background talk that the other gates still stop.
_MAI_AT_END = re.compile(r"ใหม่\s*(?:คะ|ค่ะ|ครับ|ฮะ)?\s*$")


def asks(text: str) -> bool:
    """Is this addressed to her as a question or a request?"""
    if _ASK_EN.search(text) or _MAI_AT_END.search(text):
        return True
    _, sets = _sets()
    return _contains(_tokenize(text), sets["ask"])


def _unit_question(text: str) -> bool:
    """What is still for sale, or a room by its number: live inventory."""
    if _UNIT_EN.search(text):
        return True
    _, sets = _sets()
    tokens = _tokenize(text)
    if _contains(tokens, sets["unit"]) and (_contains(tokens, sets["room"]) or asks(text)):
        return True
    # "ห้อง 801" / "A801": a room number said with a room word
    return bool(_ROOM_NO.search(text) and _contains(tokens, sets["room_no"]))


def _one_unit(text: str) -> bool:
    """Which room / recommend a room: asked after the stories had their turn."""
    if _UNIT_ONE_EN.search(text):
        return True
    _, sets = _sets()
    return _contains(_tokenize(text), sets["unit_one"])


def _money(text: str) -> bool:
    if _MONEY_EN.search(text):
        return True
    _, sets = _sets()
    return _contains(_tokenize(text), sets["money"])


def _human_talk(text: str) -> bool:
    _, sets = _sets()
    return _contains(_tokenize(text), sets["human"])


def _anchored(text: str, words: list[str]) -> bool:
    """A word of the project: one of the stories' own question words, or the
    fixed project list. What tells "ห้องนอนมีกี่แบบคะ" from "ทำไมชอบร้องเพลง"."""
    from app import stories

    _, sets = _sets()
    docs = (stories._QUERY_INDEX.get("docs") or {}).values()
    if any(w in ws for w in words for ws in docs):
        return True
    return _contains(_tokenize(text), sets["project"])


def _greeting(text: str) -> bool:
    if _GREETING_EN.search(text) or _NAME_Q.search(text):
        return True
    return any(g in text for g in _GREETING_TH)


def _off_topic(text: str) -> bool:
    if _OFF_EN.search(text):
        return True
    _, sets = _sets()
    return _contains(_tokenize(text), sets["off"])


def route(text: str, lang: str, state: RouterState | None = None, *, now: float | None = None,
          expecting: bool = False, stories_on: bool = True) -> Route:
    """The action for one heard utterance (see the module doc). `lang` = the
    language she answers in (th / en); `state` remembers the conversation;
    `expecting` = she has just asked the guest something (her line ended on
    a question a few seconds ago), so a one-word answer ("ทำเล", "the gym")
    is an answer and not a stray word from the room."""
    import time

    state = state if state is not None else RouterState()
    now = time.monotonic() if now is None else now
    text = _norm(text)
    if not text:
        return Route("silent", why="empty")
    words = content_words(text)
    asked = asks(text)
    human = _human_talk(text)
    # Every fixed line needs the same three things: words that name something,
    # the shape of a question or request, and no sign that two people are
    # talking to each other. Measured on the real table: 31 of 636 noise
    # utterances pass all three (of 246 that merely "ask"), against 178 of 213
    # project questions. A story has its own, stronger gate (`story_for`).
    addressed = bool(words) and (asked or expecting) and not human
    from app.tools.retrieval import is_commercial

    if addressed and (is_commercial(text) or _money(text)):
        return line_route(state, "handoff_commercial", "commercial", now)
    if addressed and _unit_question(text):
        return line_route(state, "handoff_commercial", "unit", now)
    topic, _gate = story_for(text, stories_on)
    if topic is not None and (asked or expecting or len(words) >= 2):
        # one bare word from the room ("โรงเรียน", "Bangkok") is not a
        # question unless she has just asked one
        state.last_line = None
        return Route("story", topic=topic, why="story")
    if addressed and _one_unit(text):
        return line_route(state, "handoff_commercial", "unit", now)
    if not human and len(words) <= 3 and _greeting(text):
        if not state.greeted:
            state.greeted = True
            return line_route(state, "offline_greeting", "greeting", now)
        # Her greeting already played (usually the camera's): the guest
        # greeting her back gets the owner's one project question, not the
        # thank-you line -- "My pleasure" to a "Hello" was the review's
        # non-sequitur, and loose words get a question (owner 12 Oct:
        # "ไม่ใช่ตอบแค่ ค่ะ").
        return line_route(state, "invite", "greeting_again", now)
    if not human and len(words) <= 3 and _THANKS.search(text):
        return line_route(state, "ack", "thanks", now)
    short = len(re.sub(r"\s+", "", text)) <= MAX_LETTERS
    if addressed and short and _off_topic(text):
        return line_route(state, "off_topic", "off_topic", now)
    if addressed and short and (_anchored(text, words) or _TO_HER_EN.search(text)):
        # The menu names what she can show (the stories' topics, the
        # pictures): only where present_story is offered -- without it every
        # one of those topics would be a promise she cannot keep (review 26
        # Oct: menu -> hand-off -> hand-off, each offering facilities).
        if stories_on and lang not in state.menu_langs:
            state.menu_langs.add(lang)
            return line_route(state, "menu", "menu", now)
        return line_route(state, "handoff_other", "handoff", now)
    return Route("silent", why="human_talk" if human else ("noise" if words else "filler"))


def route_language_request(lang: str, state: RouterState, *, now: float | None = None,
                           stories_on: bool = True) -> Route:
    """The guest asked for a language ("speak English", a language button):
    the menu in it, once (where stories are offered -- see route()); her
    one project question after (a second "speak English" got "My pleasure"
    before the review)."""
    import time

    now = time.monotonic() if now is None else now
    if stories_on and lang not in state.menu_langs:
        state.menu_langs.add(lang)
        return line_route(state, "menu", "language", now)
    return line_route(state, "invite", "language_again", now)


#: Her name as a recognizer writes it: "Emma, stop" is a stop and nothing else.
_HER_NAME = ("emma", "เอ็มม่า", "เอมม่า", "เอ็มมา")


def stop_route(text: str, moving) -> Route | None:
    """A stop, before anything else. Online the model calls stop_moving /
    move_body(stop) on "หยุด" / "stop"; offline the review (26 Oct) found
    them routed to silence -- and "Emma stop" right after her question to
    the 15-second menu, with the robot still moving.

    * The robot moving (focus's own test, passed in): any utterance with a
      stop word is a stop -- erring toward stopping, the way focus lets every
      sound through while the robot moves.
    * Standing still: only a stop word on its own (her name and particles
      aside) -- "wait, what about the pool?" is the pool question.

    The words are heard._STOP_WORDS, the motion guard's own list, matched as
    whole words. A stop is never answered with a fixed line. `moving` is a
    bool or a callable asked only once a stop word was heard."""
    from app import heard

    text = _norm(text)
    if not text or not heard.has_words(text, heard._STOP_WORDS):
        return None
    if moving() if callable(moving) else moving:
        return Route("stop", why="stop_moving")
    # her name as the tokenizer splits it (เอ็มม่า is two Thai tokens)
    name = _token_set(_HER_NAME) | frozenset(_HER_NAME)
    rest = [w for w in content_words(text)
            if w not in name and not heard.has_words(w, heard._STOP_WORDS)]
    return None if rest else Route("stop", why="stop")


def line_route(state: RouterState, line_id: str, why: str, now: float) -> Route:
    """A fixed line, unless the same one was just said (REPEAT_GAP_S)."""
    if state.last_line == line_id and now - state.last_line_at < REPEAT_GAP_S:
        return Route("silent", why="repeat_" + why)
    state.last_line = line_id
    state.last_line_at = now
    return Route("line", line=line_id, why=why)


# ---- what the session sends with send_text --------------------------------------
#
# send_text carries instructions written for a model (events.announce, the
# taps, the follow-up). Offline there is no model to read them, so the
# provider asks what they ARE -- by the markers the server's own builders put
# in them -- and acts on the few it can honour with approved words:
#
#   a camera greeting (greeter.greeting_for)     -> the offline greeting
#   a story tap (stories.tap_text)               -> that story
#   a room-type button under her question        -> that type's story, if offered
#   a tap that calls an inventory tool           -> the commercial hand-off
#   a tapped picture (stories.tap_more_text)     -> the enlarged picture is the
#                                                   answer; the hand-off once
#   a topic tap (quick_asks.text_for)            -> its question, routed
#   a language button                            -> the menu in that language
#   anything else (follow-up, goodbye, nav, ...) -> ignored, with a debug line

_PRESENT = re.compile(r'present_story\(topic="([a-z][a-z0-9_]{1,47})"\)')
#: The room-type buttons under her question (quick_asks.CHOICES): offline the
#: type's own story when it is offered, else the sales team.
_CHOICE = re.compile(r'(?:ลูกค้าเลือกห้องแบบ|The guest chose the).*show_room_type\(type="(studio|1br|2br)"\)',
                     re.DOTALL)
CHOICE_STORY = {"studio": "unit_studio", "1br": "unit_one_bedroom", "2br": "unit_two_bedroom"}
_TOOL_TAP = re.compile(r"\b(show_room_type|show_plan|find_units|compare_unit_types|show_unit)\(")
_MORE_TAP = re.compile(r"^\[(?:ลูกค้าแตะภาพ|The guest tapped the picture)")
#: A pick on the language menu (quick_asks.text_for): Thai in Thai, any other
#: language in English since 29 Oct (Russian joined the menu that day).
_LANG_TAP = re.compile(r"^\[(?:ลูกค้าแตะปุ่มเลือกภาษาบนจอ ไม่ได้พูด\] ลูกค้าเลือกภาษา(ไทย|อังกฤษ|จีน|รัสเซีย)"
                       r"|The guest chose (Thai|English|Chinese|Russian) on the screen's language menu)")
_LANG_TAP_CODE = {"ไทย": "th", "อังกฤษ": "en", "จีน": "zh", "รัสเซีย": "ru",
                  "Thai": "th", "English": "en", "Chinese": "zh", "Russian": "ru"}
_GREETING_MARKS = ("เพิ่งเดินเข้ามาหน้าหุ่น", "มีคนเดินเข้ามายืนหน้าหุ่น")
_TAP_PREFIX = re.compile(r"^\[(?:ลูกค้าแตะปุ่ม|The guest tapped)[^\]]*\]\s*")


@dataclass
class Injected:
    #: "greeting" | "story" | "choice" | "tool_tap" | "more_tap" | "topic" | "language" | "ignore"
    kind: str
    topic: str | None = None
    text: str | None = None
    lang: str | None = None


def classify_injected(text: str) -> Injected:
    text = text or ""
    if any(mark in text for mark in _GREETING_MARKS):
        return Injected("greeting")
    picked = _LANG_TAP.match(text)
    if picked:
        return Injected("language", lang=_LANG_TAP_CODE[picked.group(1) or picked.group(2)])
    if _MORE_TAP.match(text):
        return Injected("more_tap")
    present = _PRESENT.search(text)
    if present and _TAP_PREFIX.match(text):
        return Injected("story", topic=present.group(1))
    choice = _CHOICE.search(text)
    if choice and _TAP_PREFIX.match(text):
        return Injected("choice", topic=CHOICE_STORY[choice.group(1)])
    if _TOOL_TAP.search(text) and _TAP_PREFIX.match(text):
        return Injected("tool_tap")
    if _TAP_PREFIX.match(text):
        # a topic button: the catalog's own question after the tap marker
        from app import quick_asks

        for topic in quick_asks.TOPICS.get("condo", ()):
            for lang, ask in topic["ask"].items():
                if text.endswith(ask):
                    return Injected("topic", text=ask, lang=lang if lang in ("th", "en") else "en")
    return Injected("ignore")
