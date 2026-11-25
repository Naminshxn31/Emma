"""Motion on the event screen while "ตอบสด" is open (owner 23 Nov 2026: "ทำเลย ใช้เอเจนท์ตอบสดตัวเดิม").

The owner's idea: when the host goes off-script and his question matches the topic of a motion slide
the ceremony already has, that slide goes up on the event screen -- the screen is never without
motion. What decides it, and what it may never do:

* **The server decides, never the model.** The host's own words (the input transcription inside the
  "ตอบสด" window, app/heard.py's current caption) are matched against the small topic map below. The
  model is not asked and cannot name a slide.
* **Words, not substrings.** English as words; Thai cut by event_script's standard dictionary (not
  retrieval.tokenize, which learns the deck's words at load -- CLAUDE.md) and every Thai keyword cut the
  same way, matched as a run of whole words: สระ never matches inside an unrelated word. Loanword
  spellings are listed (แอป/แอพ, ยูนิต/ยูนิท, สมาร์ทโฮม/สมาร์ตโฮม ...).
* **No match is better than a weak one.** A topic needs a strong keyword (or two weak ones) and must
  beat every other topic outright: two topics tied = none. A price, quotation, lights, curtains or
  video word = none whatever else was said (those slides are excluded, below).
* **None = the show's own logo loop** (the idle clip the event screen plays between lines, ceremony-
  show.js): an existing, approved brand backdrop -- never the last line's slide under the host's new
  topic, never a wrong-topic slide.
* **Settled, not beat-synced.** A slide goes up in its finished state (the page's `late` path, the
  same one a screen joining mid-line uses): her live words are not the script's, so nothing waits for
  them; the slide's ambient motion stays.
* **Only what ceremony_slides would serve**: `for_cue(drafts=False)` -- while the slides file is a draft
  (today) nothing goes up but the logo; the preview passes drafts (scripts/preview_ceremony_motion.py).
* **The next press of the script replaces it** (`on_press`, from ceremony.script_press: as her picture,
  clear at press); the red stop and the call's end take it down as they take any slide. The robot face
  never gets these messages (face_mirror only) and keeps its own story behaviour.

Which slides, and why: TOPICS (each with the settled state it lands on, looked at on 23 Nov in
scripts/preview_ceremony_motion.py --shots -- a topic whose line settles on the logo has no slide), and
every other cue with its reason in EXCLUDED (`table()`).
Measured: scripts/eval_ceremony_motion.py.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time

from app import turnlog

logger = logging.getLogger("condo_voice.ceremony_motion")

#: topic -> the cue whose slide shows it, and why. Keywords: phrase -> weight (2 = strong, 1 = weak).
TOPICS: dict[str, dict] = {
    "location": {
        "cue": "s03_l03",
        "reason": "settles on the Pattaya street map: 'Getting around is easy · everyday life in Pattaya'",
        # (review 23 Nov: everyday nouns -- school, shopping, the beach, Pattaya -- are weak: alone they are
        # banter ("did you go to school?"), with a second cue ("schools nearby", "far from the beach") a topic)
        "en": {"located": 2, "jomtien": 2, "jomtian": 2, "jom tien": 2, "jontien": 2, "nearby": 2,
               "neighbourhood": 2, "neighborhood": 2, "getting around": 2,
               "location": 1, "beach": 1, "pattaya": 1, "hospital": 1, "school": 1, "shopping": 1, "mall": 1,
               "near": 1, "far": 1, "distance": 1, "where": 1, "sea": 1, "restaurant": 1, "area": 1},
        "th": {"ทำเล": 2, "ที่ตั้ง": 2, "ตั้งอยู่": 2, "จอมเทียน": 2, "การเดินทาง": 2,
               "หาด": 1, "ชายหาด": 1, "ทะเล": 1, "พัทยา": 1, "โรงพยาบาล": 1, "โรงเรียน": 1, "ห้าง": 1,
               "ห้างสรรพสินค้า": 1, "ใกล้": 1, "ห่าง": 1, "ไกล": 1, "ร้านอาหาร": 1, "แถว": 1},
    },
    "units": {
        "cue": "s04_l03",
        "reason": "settles inside a unit: the act's move after its line ends on a full-screen room render "
                  "(the '1,066 units' figure and the type cards are mid-line only -- she says the number)",
        "en": {"unit": 2, "studio": 2, "bedroom": 2, "room type": 2, "unit type": 2, "layout": 2,
               "floor plan": 2, "apartment": 2, "residence": 2, "one bed": 2, "two bed": 2,
               "kind of room": 2, "type of room": 2,
               "room": 1, "size": 1},                  # ("how many" out: "how many people are in the room?")
        "th": {"ยูนิต": 2, "ยูนิท": 2, "ห้องนอน": 2, "สตูดิโอ": 2, "แบบห้อง": 2, "ประเภทห้อง": 2,
               "ห้องชุด": 2, "แปลน": 2, "ห้อง": 1, "ขนาด": 1},
    },
    "facilities": {
        "cue": "s06_l03",
        "reason": "settles on 'around 2,500 square metres in total · Indoor facilities' over the gym, pools, "
                  "lounge and spa photos (no question, no number she has not been asked about beyond that line)",
        "en": {"facility": 2, "facilities": 2, "amenity": 2, "amenities": 2, "common area": 2, "pool": 2,
               "swimming pool": 2, "gym": 2, "fitness": 2, "sauna": 2, "spa": 2, "co working": 2,
               "coworking": 2, "lounge": 2, "playground": 2, "kids club": 2, "swim": 1, "swimming": 1,
               "exercise": 1, "relax": 1},
        "th": {"ส่วนกลาง": 2, "สิ่งอำนวยความสะดวก": 2, "สระ": 2, "สระว่ายน้ำ": 2, "ฟิตเนส": 2,
               "ฟิตเนต": 2, "ยิม": 2, "ซาวน่า": 2, "ซาวนา": 2, "สปา": 2, "โคเวิร์กกิ้ง": 2,
               "โคเวิร์คกิ้ง": 2, "เลานจ์": 2, "สนามเด็กเล่น": 2, "ว่ายน้ำ": 1, "ออกกำลังกาย": 1,
               "พักผ่อน": 1},
    },
    "technology": {
        "cue": None,
        "reason": "no slide: the line on technology (s02_l09) settles on the brand logo (its options' end), "
                  "the same as no match -- its smart-home phone is mid-line only. Kept as a topic so a "
                  "technology question never ties into, or falls to, another topic's slide",
        "en": {"smart home": 2, "smarthome": 2, "technology": 2, "tech": 2, "app": 2, "application": 2,
               "innovation": 2, "innovative": 2, "futuristic": 2, "design": 2, "concept": 2,
               "distinctive": 2, "special": 1, "what make": 1, "modern": 1, "smart": 1},
        "th": {"สมาร์ทโฮม": 2, "สมาร์ตโฮม": 2, "สมาร์ท โฮม": 2, "เทคโนโลยี": 2, "เทคโนโลยี่": 2,
               "แอป": 2, "แอพ": 2, "แอปพลิเคชัน": 2, "แอพพลิเคชั่น": 2, "นวัตกรรม": 2, "ดีไซน์": 2,
               "คอนเซปต์": 2, "ทันสมัย": 1, "พิเศษ": 1},
    },
}
#: A question with any of these gets no topic slide, whatever else it says: the quotation's slides are
#: excluded (a price must not seem answered on the screen), the lights/curtains and video slides too.
NEVER = {
    "en": ("price", "prices", "cost", "costs", "how much", "expensive", "cheap", "budget", "baht", "quotation",
           "quote", "discount", "promotion", "payment", "instalment", "installment", "down payment", "loan",
           "mortgage", "invest", "investment", "yield", "rent", "rental", "lights", "curtain", "curtains",
           "video", "film", "clip", "weather",
           # an event day's own (set A held-out, 23 Nov: "a betting pool going on who wins the lucky draw"
           # put the facilities slide up)
           "bet", "betting", "lucky draw", "raffle", "prize"),
    "th": ("ราคา", "บาท", "ใบเสนอราคา", "ส่วนลด", "โปรโมชั่น", "ผ่อน", "ดาวน์", "งบ", "ลงทุน", "ปล่อยเช่า",
           "ผลตอบแทน", "ไฟ", "ม่าน", "วิดีโอ", "คลิป", "อากาศ", "จับรางวัล", "ลุ้นรางวัล", "ของรางวัล", "พนัน"),
}
#: A question about EMMA herself is banter whatever it names ("Emma, did you go to school?", "are you a
#: robot unit?", "do you like shopping?" -- review 23 Nov): no topic. ("Can you tell us about the pool" and
#: "do you have a gym" are about the project: not here.)
ABOUT_HER = {
    "en": ("did you", "have you been", "have you ever", "are you", "were you", "you like", "you love",
           "you enjoy", "your favourite", "your favorite", "you feel", "you remember"),
    "th": ("เคย", "ชอบ", "รู้สึก"),
}
#: Every cue of the ceremony that is NOT in TOPICS, and why (the report and the docs read this).
EXCLUDED = {
    "s02_l03": "her self-introduction ('I'm Emma ...'): not an answer to a topic",
    "s02_l05": "what she can do, ends on preparing quotations",
    "s02_l09": "settles on the brand logo (the technology topic's line: see TOPICS)",
    "s04_l06": "settles on the script's question to the host ('Khun Miki · An area you'd like me to show you?')",
    "s04_l09": "the pool joke's set-up", "s04_l11": "the pool joke's punchline", "s04_l13": "the pool joke",
    "s04_l15": "the pool joke's close",
    "s05_l03": "the scripted view question that leads to the quotation",
    "s05_l06": "the scripted recommendation that leads to the quotation",
    "s05_l07": "the demo room's line: not written yet (waits for the room's number) -- one room, the quotation's",
    "s05_l08": "quotation", "s05_l11": "quotation", "s05_l13": "quotation", "s05_backup_quote": "quotation",
    "s06_l05": "the superfan joke's set-up", "s06_l07": "the superfan joke's punchline",
    "s07_l02": "the video's introduction ('please enjoy the video')",
    "s07_l07": "the water-quality dashboard ('this page'): only with that page on the screen",
    "s08_l06": "lights", "s08_l08": "lights", "s08_l10": "lights", "s08_l12": "lights",
    "s08_l14": "curtains", "s08_l16": "curtains",
    "s09_l02": "the film's introduction ('please enjoy the story')",
    "s10_l05": "the closing thanks",
}
#: A new utterance this long that matches nothing takes an earlier topic slide down (a new question nobody
#: recognised); a shorter one ("wow", "great, thank you") leaves it -- the answer is still on that topic.
SUBSTANTIVE_WORDS = 4
#: Decided once his words have stopped growing this long (review 23 Nov: decided on every partial caption,
#: "Which is better, the gym" put the facilities slide up before "...or the sea view from the units?" made
#: it a tie). Her answer begins 1-2 s after he stops; the input transcription comes before her first sound
#: (429/437, CLAUDE.md) -- the slide lands about as she starts. A pause of his longer than this mid-question
#: still decides on the half said; the rest of it decides again.
QUIET_S = 1.0
#: After a press of the script with a topic slide of ours up: the pressed line's own subtitle replaces it on
#: the screen; a press that says nothing (a walk, a tick) gets the logo once this long has gone by with no line.
PRESS_LINE_WAIT_S = 2.0
#: The preview on this PC shows draft slides (scripts/preview_ceremony_motion.py); the robot never does.
DRAFTS = False

#: `topic`: the decided topic whose screen is up (None = the logo, or nothing sent yet: `sent` False);
#: `from_utterance`: the caption (its start) whose words put it up; `slides`: topic -> slide, fetched once
#: per window (`ready`), so a decision does no file work on the voice loop (review 23 Nov: the signature
#: digest on every chunk); `pending`: the caption waiting out QUIET_S, `timer` its handle.
STATE: dict = {"topic": None, "sent": False, "from_utterance": None, "slides": {}, "ready": False,
               "pending": None, "timer": None, "cue_up": None, "cue_at": None}
_stock: dict = {}


def reset() -> None:
    _cancel_timer()
    STATE.update(topic=None, sent=False, from_utterance=None, slides={}, ready=False, pending=None, timer=None,
                 cue_up=None, cue_at=None)


def _cancel_timer() -> None:
    timer = STATE.get("timer")
    STATE["timer"] = None
    STATE["pending"] = None
    if timer is not None:
        try:
            timer.cancel()
        except Exception:                                # noqa: BLE001
            pass


# ---- words ----------------------------------------------------------------------------------------

_EN_WORD = re.compile(r"[a-z0-9]+")
_THAI = re.compile(r"[฀-๿]")


def _en_words(text: str) -> list[str]:
    out = []
    for w in _EN_WORD.findall(text.lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]                                   # units -> unit, schools -> school
        out.append(w)
    return out


def _th_words(text: str) -> list[str]:
    from app import event_script

    return [w for w in (t.strip() for t in event_script._segment(text)) if w and _THAI.search(w)]


def _phrase(words: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return n > 0 and any(tuple(words[k:k + n]) == phrase for k in range(len(words) - n + 1))


def _compiled() -> dict:
    """Every keyword cut the way the host's words are (cached: the Thai segmenter is not free)."""
    if "topics" not in _stock:
        topics = {}
        for name, t in TOPICS.items():
            topics[name] = {
                "en": [(tuple(_en_words(k)), w) for k, w in t["en"].items()],
                "th": [(tuple(_th_words(k)), w) for k, w in t["th"].items()],
            }
        _stock["topics"] = topics
        _stock["never_en"] = [tuple(_en_words(k)) for k in NEVER["en"]]
        _stock["never_th"] = [tuple(_th_words(k)) for k in NEVER["th"]]
        _stock["her_en"] = [tuple(_en_words(k)) for k in ABOUT_HER["en"]]
        _stock["her_th"] = [tuple(_th_words(k)) for k in ABOUT_HER["th"]]
    return _stock


def decide(text: str | None) -> tuple[str | None, str]:
    """(topic, why) for the host's words: a topic of TOPICS, or None with why ("never", "about_her", "tie",
    "weak", "nothing")."""
    text = text or ""
    en, th = _en_words(text), _th_words(text)
    stock = _compiled()
    if any(_phrase(en, p) for p in stock["never_en"]) or any(_phrase(th, p) for p in stock["never_th"]):
        return None, "never"
    if any(_phrase(en, p) for p in stock["her_en"]) or any(_phrase(th, p) for p in stock["her_th"]):
        return None, "about_her"
    scores = {}
    for name, t in stock["topics"].items():
        score = sum(w for p, w in t["en"] if _phrase(en, p)) + sum(w for p, w in t["th"] if _phrase(th, p))
        if score:
            scores[name] = score
    if not scores:
        return None, "nothing"
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, score = ranked[0]
    if score < 2:
        return None, "weak"
    if len(ranked) > 1 and ranked[1][1] >= score:
        return None, "tie"
    if len(ranked) > 1 and ranked[1][1] >= 2:
        return None, "tie"                               # two topics each with a strong word: unsure
    return best, "match"


def _substantive(text: str) -> bool:
    return len(_en_words(text)) + len(_th_words(text)) >= SUBSTANTIVE_WORDS


# ---- the event screen ---------------------------------------------------------------------------

def _prepare() -> dict:
    """The window's slides (topic -> slide or None) and the keywords cut -- ceremony_slides' checks, the
    Thai dictionary: file and CPU work, done once per window off the voice loop (`on_open`)."""
    from app import ceremony_slides

    _compiled()
    slides = {}
    covered = _script_covered()
    STATE["script_first"] = bool(covered is not None)
    for name, t in TOPICS.items():
        try:
            if covered is not None and t["cue"] in covered:
                slides[name] = None                      # the script's own slide (app/ceremony_pick.py) answers this topic
                continue
            slides[name] = ceremony_slides.for_cue(t["cue"], lang="en", drafts=DRAFTS) if t["cue"] else None
        except Exception:                                # noqa: BLE001 -- that topic shows the logo
            logger.exception("ceremony motion: the slide of %s", name)
            slides[name] = None
    return slides


def prepare_cue(cue_id: str):
    """The slide of a cue (file work, cached by ceremony_slides): safe in a worker thread. None = nothing servable."""
    try:
        from app import ceremony_slides

        return ceremony_slides.for_cue(cue_id, lang="en", drafts=DRAFTS)
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony motion: preparing a cue's slide")
        return None


def send_cue(cue_id: str, slide, at=None) -> bool:
    """Send a prepared slide to the hall screen. MUST run on the event loop (face_mirror's queue and the timer
    handle are not thread-safe -- review 24 Nov). Never raises."""
    try:
        from app import face_mirror

        if at is not None:
            STATE["cue_at"] = at
        if slide is None:
            turnlog.record("ceremony_motion", shown="none", why="cue_no_slide", cue=cue_id)
            return False
        if face_mirror._state.get("subtitle") is not None:
            turnlog.record("ceremony_motion", shown="none", why="line_held", cue=cue_id)
            return False                                 # a pressed line's subtitle is on: its own slide, never torn down (review B)
        _cancel_timer()
        STATE.update(topic=None, sent=True, from_utterance=None, cue_up=cue_id)
        face_mirror.motion(slide, cue_id)
        turnlog.record("ceremony_motion", shown=cue_id, why="cue")
        return True
    except Exception:                                    # noqa: BLE001 -- the screen, never the call
        logger.exception("ceremony motion: showing a cue's slide")
        return False


def _script_covered():
    """Live first with follow running (owner 24 Nov: no motion that duplicates the script): the cues the script-first
    picker serves -- those topics keep no slide of their own. None = the old behaviour (switch off, cues on, follow off)."""
    try:
        from app import ceremony_follow, ceremony_pick

        if not ceremony_follow.script_first():
            return None
        return set((ceremony_pick.table().get("cues") or {}))
    except Exception:                                    # noqa: BLE001
        return None


def show_cue(cue_id: str, at=None) -> bool:
    """CEREMONY_FOLLOW_CUES off (owner 24 Nov ~11:40, app/ceremony_follow.py): the host said this cue's scripted line
    and she answers live -- the cue's own slide goes up in its settled state (the same `late` path as a topic slide:
    face_mirror.motion, never the robot face). `at` = the caption (its start) that matched: the topic matcher leaves
    that utterance alone. False = no slide servable. Call on the loop (or use prepare_cue + send_cue). Never raises."""
    return send_cue(cue_id, prepare_cue(cue_id), at)


def _show(topic: str | None, why: str) -> None:
    """`topic` = a decided topic, or None for the logo loop. Nothing is sent when that is up already."""
    from app import face_mirror

    if STATE["sent"] and topic == STATE["topic"]:
        return
    slide = STATE["slides"].get(topic) if topic is not None else None
    target = TOPICS[topic]["cue"] if slide is not None else "logo"
    if topic is not None and slide is None:
        why = "no_slide" if TOPICS[topic]["cue"] else "topic_without_slide"     # (a draft file; technology)
    logo_already = STATE["sent"] and STATE["slides"].get(STATE["topic"]) is None and not STATE.get("cue_up")
    STATE.update(topic=topic, sent=True, cue_up=None)
    if slide is None and logo_already:
        if topic is not None:                            # (logo to logo: nothing to send -- the row says why)
            turnlog.record("ceremony_motion", shown="logo", why=why)
        return
    face_mirror.motion(slide, target if slide is not None else None)
    turnlog.record("ceremony_motion", shown=target, why=why)


def on_open() -> None:
    """"ตอบสด" opened: the window's slides are fetched (in a thread when there is a loop: the staff's press
    must not stall her voice); once in, the logo loop -- the last line's settled slide is not the host's new
    topic -- and then his topics. NO SLIDE SERVABLE (today: the slides file is a draft, unsigned) = this
    feature is inert: nothing sent to the event screen, nothing decided, one turnlog row says so."""
    reset()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _ready(_prepare())
        return
    stamp = object()
    STATE["window"] = stamp

    def done(fut) -> None:
        if STATE.get("window") is not stamp or fut.cancelled():
            return                                       # (a press since, or a newer window: not this one's)
        try:
            slides = fut.result()
        except Exception:                                # noqa: BLE001 -- no slides: inert
            logger.exception("ceremony motion: preparing the window")
            slides = {}
        _ready(slides)

    loop.run_in_executor(None, _prepare).add_done_callback(lambda fut: loop.call_soon_threadsafe(done, fut))


def on_open_quiet() -> None:
    """The follow window re-opened after a line the server played (app/ceremony_follow.py): the slides ready
    for the host's topics, but NO logo -- the line's settled slide stays up until the next press or topic."""
    reset()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _ready(_prepare(), quiet=True)
        return
    stamp = object()
    STATE["window"] = stamp

    def done(fut) -> None:
        if STATE.get("window") is not stamp or fut.cancelled():
            return
        try:
            slides = fut.result()
        except Exception:                                # noqa: BLE001 -- no slides: inert
            logger.exception("ceremony motion: preparing the window")
            slides = {}
        _ready(slides, quiet=True)

    loop.run_in_executor(None, _prepare).add_done_callback(lambda fut: loop.call_soon_threadsafe(done, fut))


def _ready(slides: dict, quiet: bool = False) -> None:
    from app import face_mirror

    STATE.update(slides=slides, ready=True)
    if not any(slides.values()) and not STATE.get("script_first"):
        turnlog.record("ceremony_motion", shown="none", why="no_slides")
        return
    if quiet:
        return                                           # (nothing sent: `sent` stays False)
    STATE["sent"] = True
    face_mirror.motion(None, None)
    turnlog.record("ceremony_motion", shown="logo", why="open")


def wants_words(session) -> bool:
    """session.py asks this FIRST, on every caption of every robot-face call (the sales robot's too): True
    only on the ceremony app's call while its "ตอบสด" window is open and some topic has a slide to show.
    False = nothing else runs -- the caption is not even read. Never raises."""
    try:
        ceremony = sys.modules.get("app.ceremony")      # (never imported = no ceremony call can exist)
        if ceremony is None or session is None or session is not ceremony.active_session():
            return False
        if ceremony._adhoc_live(time.time()) is None:
            return False
        return not (STATE.get("ready") and not any(STATE["slides"].values()))
    except Exception:                                    # noqa: BLE001 -- the call, never the screen
        return False


def on_press() -> None:
    """A press of the script (ceremony.script_press, a line or a reset): his words drive nothing from here.
    A slide of ours up: the pressed line's subtitle replaces it on the screen (no logo in between -- owner
    18 Nov "ไม่อยากให้มีคั่น"); no line within PRESS_LINE_WAIT_S = the logo."""
    from app import face_mirror

    try:
        from app import ceremony_follow

        ceremony_follow.hall_released()                  # (free talk: a staff press's slide settles by these rules, not by the logo timer)
    except Exception:                                    # noqa: BLE001
        pass
    up = STATE["sent"] and (STATE["slides"].get(STATE["topic"]) is not None or bool(STATE.get("cue_up")))
    sent = face_mirror.motion_now() if up else None
    reset()
    STATE["window"] = None
    if sent is None:
        return

    def logo() -> None:
        if face_mirror.motion_now() is sent:             # still ours: no line, no other window replaced it
            face_mirror.motion(None, None)
            turnlog.record("ceremony_motion", shown="logo", why="press")

    try:
        asyncio.get_running_loop().call_later(PRESS_LINE_WAIT_S, logo)
    except RuntimeError:
        logo()                                           # (no loop: no line can come)


def on_host_words(session, caption) -> None:
    """The host's words so far in this utterance (session's receive loop, after heard.record): only on the
    ceremony call, only while "ตอบสด" is open; decided once they stop growing for QUIET_S. Never raises."""
    try:
        from app import ceremony

        if session is None or session is not ceremony.active_session() or caption is None:
            return
        if ceremony._adhoc_live(time.time()) is None:
            return
        from app.config import settings

        if settings.ceremony_script_show_only:
            return                                       # only the script's own slides (owner 24 Nov ~13:50: "ไม่ต้องโชว์อะไร")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _decide_now(session, caption)                # (no loop: a script, the tests' plain path)
            return
        _cancel_timer()
        STATE["pending"] = (session, caption)
        STATE["timer"] = loop.call_later(QUIET_S, _fire)
    except Exception:                                    # noqa: BLE001 -- the screen, never the call
        logger.exception("ceremony motion: the host's words")


def _fire() -> None:
    pending = STATE.get("pending")
    STATE.update(pending=None, timer=None)
    if not pending:
        return
    if not STATE.get("ready"):                           # the window's slides not in yet: a moment more
        try:
            STATE["pending"] = pending
            STATE["timer"] = asyncio.get_running_loop().call_later(0.2, _fire)
        except RuntimeError:
            pass
        return
    _decide_now(*pending)


def _decide_now(session, caption) -> None:
    try:
        from app import ceremony

        if session is not ceremony.active_session() or ceremony._adhoc_live(time.time()) is None:
            return                                       # (a press, the window's end, a new call meanwhile)
        if not STATE.get("ready"):
            STATE.update(slides=_prepare(), ready=True)
        if not any(STATE["slides"].values()):
            return                                       # no slide servable (a draft file): inert
        text = getattr(caption, "text", "") or ""
        at = getattr(caption, "at", None)
        if at is not None and STATE.get("cue_at") is not None and at <= STATE["cue_at"]:
            return                                       # this utterance put a cue's own slide up (show_cue): not replaced
        change, topic, why = step(STATE["topic"], STATE["from_utterance"], text, at)
        if not change:
            return
        if topic is not None and not _free_claim(at):
            return                                       # free talk: a cue slide / story holds this exchange
        if topic is not None and not (STATE["sent"] and topic == STATE["topic"]):
            STATE["from_utterance"] = at
        _show(topic, why)
    except Exception:                                    # noqa: BLE001 -- the screen, never the call
        logger.exception("ceremony motion: deciding")


def _free_claim(at) -> bool:
    """Free talk: a topic slide may go up only on an exchange nothing else has taken (cue > story > topic). True outside
    free talk. Never raises."""
    try:
        from app import ceremony_follow

        return ceremony_follow.claim_hall("topic", at)
    except Exception:                                    # noqa: BLE001
        return True


def step(up: str | None, from_utterance, text: str, at) -> tuple[bool, str | None, str]:
    """The rule, pure: with topic `up` on the screen (None = the logo), put up by the utterance
    `from_utterance`, the host's words `text` of utterance `at` -> (change?, the topic to show or None for
    the logo, why). scripts/eval_ceremony_motion.py replays partial captions through this same rule."""
    topic, why = decide(text)
    if topic is not None:
        return topic != up, topic, why
    if up is None:
        return False, None, why                          # the logo already
    if why in ("never", "tie", "about_her"):
        return True, None, why                           # a price, two topics, banter: never under a slide
    # nothing / weak: the same utterance that put it up (the rest of a half-said word unmade it: "สระ|บุรี")
    # or a new question = the logo; a short reaction ("wow") of a new utterance keeps it
    if from_utterance != at and not _substantive(text):
        return False, up, why
    return True, None, why


def table() -> list[dict]:
    """Every cue: shown for which topic, or excluded and why (docs, the report, tests)."""
    rows = [{"cue": t["cue"], "topic": name, "reason": t["reason"]} for name, t in TOPICS.items()]
    rows += [{"cue": cue, "topic": None, "reason": why} for cue, why in EXCLUDED.items()]
    return rows
