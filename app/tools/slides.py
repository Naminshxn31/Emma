"""
Slide presentation — the assistant shows things, not just describes them.

Two modes, because a sales gallery needs both:

- **Answer with a slide.** A guest asks "ขอดูผังห้อง 2 ห้องนอน" and the
  matching slide appears while the assistant talks about it.
- **Narrated tour.** `start_presentation` walks an ordered deck; the model
  narrates each slide and calls `next_slide` itself when it's done, so a
  guest can interrupt at any point and the conversation just carries on —
  no separate "presentation mode" the robot has to be pulled out of.

Slides live in `data/slides/` with an index carrying Thai *and* English
titles, summaries and keywords (ported from `emma`, which had already
catalogued 85 of them). Search matches against all of it, so either language
finds the same slide.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.slides")

#: Ordered tours. Keys are what a guest might ask for; values are slide types
#: drawn from the index, in the order they should be presented.
TOURS: dict[str, list[str]] = {
    # The official sales deck, in its authored order — this is what a sales
    # person would actually walk a guest through.
    "deck": ["deck"],
    "overview": ["cover", "branding", "location-map", "facility", "unit-plan"],
    "facilities": ["facility"],
    "floorplans": ["floor-plan", "unit-plan"],
    "location": ["location-map"],
}

#: Tour used when the guest just says "แนะนำโครงการหน่อย". Falls back to the
#: assembled overview if no official deck has been imported yet.
DEFAULT_TOUR = "deck"

#: Repeated on every advance, because a rule sitting near the top of the
#: system prompt loses to whatever the model feels like doing sixty slides
#: later. It kept asking "shall we go on?" after each page — and again after
#: returning from a question — even with the prompt forbidding it. An
#: instruction attached to the tool result arrives at the moment of the
#: decision, which is the only place it reliably lands.
KEEP_GOING = (
    # The boundary first, and stated as a boundary rather than a preference.
    #
    # "ห้ามเกริ่นล่วงหน้า" was already in here — at the very end, after four
    # other clauses — and every single slide still finished with a hand-off
    # the script doesn't contain: "...building better tomorrows ค่ะ เดี๋ยวไป
    # ดูภาพรวมโครงการกันต่อนะคะ". The scripts themselves end cleanly at ค่ะ,
    # so this is the model padding the seam, and a rule it reads last loses
    # to the urge to be a smooth presenter.
    #
    # Quoting the exact padding it produces is what makes a rule like this
    # land; "don't preview" is abstract, "don't say เดี๋ยวไป...กันต่อนะคะ" is
    # a string it can match against what it was about to emit.
    "พูดเฉพาะเนื้อหาในฟิลด์ script เท่านั้น ห้ามเติมประโยคใดๆ ก่อนหรือหลังบท "
    "ห้ามลงท้ายด้วยการบอกว่าจะไปดูอะไรต่อ เช่น 'เดี๋ยวไปดู...กันต่อนะคะ' "
    "'ต่อไปจะเป็น...' 'สไลด์ถัดไป...' — จบที่ประโยคสุดท้ายของบทแล้วหยุดพูด "
    "ห้ามขึ้นต้นด้วยการประกาศชื่อหัวข้อสไลด์ "
    # Now the rest: fidelity to the script, then the mechanics.
    # The scripts are Thai only, and the assistant answers in whatever
    # language the guest speaks. Without this the two rules contradict each
    # other — "speak only what's in the script" against "reply in their
    # language" — and a Chinese guest gets either Thai read at them or
    # something improvised. Translation is allowed; changing the content
    # under cover of translating is not.
    "ถ้าลูกค้าพูดภาษาอื่น ให้แปลบทเป็นภาษานั้นโดยคงเนื้อหาให้ครบถ้วน "
    "ห้ามเพิ่มหรือตัดเนื้อหาระหว่างแปล "
    "ปรับถ้อยคำให้ลื่นสำหรับการพูดได้ แต่ห้ามแต่งเนื้อหาใหม่ ห้ามข้ามเนื้อหา "
    "ห้ามเพิ่มตัวเลขหรือข้อมูลนอกบท "
    "ถ้าสไลด์นี้ไม่มี script ให้บรรยายสั้นๆ 2-3 ประโยคจากข้อมูลสไลด์ "
    "พูดจบแล้วเรียก next_slide ทันทีโดยไม่ต้องพูดอะไรคั่น "
    "ห้ามถามว่าจะไปสไลด์ถัดไปไหม ห้ามรอคำตอบ ลูกค้าจะพูดแทรกเองถ้าต้องการ"
)

_slides: list[dict] | None = None

#: Current presentation position. `deck` is the list of slide ids being shown.
#:
#: `detour` means the screen is showing something the guest asked for
#: mid-presentation, so `current` is not `deck[index]`. The deck and the
#: position survive it. They used to be wiped: answering "ขอดูฟิตเนส" during
#: a tour threw the running presentation away, and "บรรยายต่อจากเมื่อกี้"
#: then hit "no presentation in progress" — the assistant carried on talking
#: from memory while the picture sat frozen on the gym.
#:
#: `awaiting` is the slide id whose question is hanging in the air, with
#: `asked_at` the moment it was asked. Everything that pushes the tour
#: forward has to consult it, or the robot asks "เคยมาเที่ยวจอมเทียนไหมคะ"
#: and moves to the next page before the guest has drawn breath.
def _blank_state() -> dict[str, Any]:
    return {
        "deck": [], "index": -1, "tour": None, "current": None, "detour": False,
        "awaiting": None, "asked_at": 0.0, "unheard": False, "manual": False,
        # Where a stopped tour is kept so it can be picked back up. See
        # `stop_presentation` — "หยุด" and "เลิกดูแล้ว" are different requests
        # and used to be the same code path.
        "parked": None,
    }


STATE: dict[str, Any] = _blank_state()


def reset_state() -> None:
    """Back to no presentation running.

    Exists so tests reset from the same definition the module uses. The test
    fixture used to list the keys by hand and had already drifted — it never
    cleared `detour`, so whether a test saw a paused tour depended on which
    test ran before it. Adding `awaiting` to that list would have been the
    second key nobody remembered.
    """
    STATE.clear()
    STATE.update(_blank_state())


#: Sent instead of KEEP_GOING when the slide carries a question.
#:
#: The one thing it has to overturn is the habit the rest of the tour drills
#: in: KEEP_GOING says call next_slide the moment you stop talking, and it
#: says so on every single advance. A question asked under that instruction
#: is not a question, it's a rhetorical flourish on the way past.
ASK_AND_WAIT = (
    "พูดตามฟิลด์ script ก่อน แล้วถามคำถามในฟิลด์ ask ต่อท้ายด้วยน้ำเสียงเป็นกันเอง "
    "ปรับถ้อยคำคำถามให้เป็นธรรมชาติได้ แต่ต้องถามเรื่องเดียวกับที่เขียนไว้ "
    "ถ้าลูกค้าพูดภาษาอื่น ให้แปลทั้งบทและคำถามเป็นภาษานั้นโดยคงเนื้อหาครบถ้วน "
    # The whole point, stated as the boundary it is.
    "ถามแล้วหยุด ห้ามเรียก next_slide ห้ามพูดต่อ ห้ามตอบคำถามตัวเอง รอให้ลูกค้าตอบ "
    "พอลูกค้าตอบ ให้รับคำตอบสั้นๆ อย่างเป็นธรรมชาติก่อน 1 ประโยค "
    "เชื่อมโยงกับโครงการถ้าเชื่อมได้ แล้วค่อยเรียก next_slide ไปต่อ "
    "ถ้าลูกค้าเงียบ ระบบจะบอกให้ไปต่อเอง ไม่ต้องถามซ้ำ ไม่ต้องทวง"
)


def _slides_dir() -> Path:
    return Path(settings.slides_dir).expanduser()


def load_slides() -> list[dict]:
    global _slides
    if _slides is not None:
        return _slides
    path = _slides_dir() / "index.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _slides = raw["images"] if isinstance(raw, dict) else raw
    except Exception:
        logger.warning("no slide index at %s — presentation tools will be empty", path)
        _slides = []
    return _slides


def search_slides(query: str, limit: int = 8):
    """Rank the library against a question.

    BM25 over word-tokenised Thai, plus embeddings for meaning, fused by
    rank — see app/tools/slide_search.py and app/tools/retrieval.py.
    """
    from app.tools import slide_search

    return slide_search.get_index(load_slides()).search(query, limit=limit)


def reload_slides() -> None:
    """Drop the cache so a re-indexed deck is picked up without a restart."""
    global _slides
    from app.tools import slide_search

    _slides = None
    slide_search.reset()   # the index is built from the deck, so it goes too



def confident_enough_to_show(hits, query: str = "") -> bool:
    """Is the top hit good enough to put on the screen?

    Answering in words and changing the picture are separate decisions and
    deserve separate bars. A marginal text hit is cheap — the model reads it
    and can decline to use it. A marginal *slide* is expensive: the guest is
    looking at the screen, and a wrong picture silently contradicts whatever
    is being said. Asking ราคาโครงการ put a photo of the executives up while
    the reply was "we don't have pricing yet".

    Wrong in the safe direction: it will sometimes decline to show a slide
    that would have been fine. If the model really wants a picture it can
    call `show_slide`, where the guest asked for one explicitly.
    """
    return bool(hits) and hits[0].found and hits[0].confident



def _public(slide: dict, position: dict | None = None) -> dict:
    """What the model and the display window get. Deliberately excludes the
    raw index entry so a schema change here doesn't leak into prompts."""
    out = {
        "id": slide["id"],
        "type": slide.get("type"),
        "title_th": slide.get("title_th"),
        "title_en": slide.get("title_en"),
        "summary_th": slide.get("summary_th"),
        "summary_en": slide.get("summary_en"),
        "url": f"/slides/{slide['file']}",
    }
    # Narration for this slide, with its provenance attached.
    #
    # `script_is_approved_copy` used to be hard-coded True for anything that
    # had a script at all. That was written on the assumption that scripts
    # only ever arrive from the sales team. They don't: the deck's 59 pages
    # were drafted from the slide images, and flagging those as approved
    # turned the safety mechanism inside out — the field exists to tell the
    # model "a human signed off on these exact words, say them", and it was
    # saying that about words no human had read.
    #
    # A draft is still worth speaking: it keeps the robot on the deck's own
    # material instead of improvising. It just must not be presented as
    # something it isn't, and a person must be able to see what still needs
    # review. `scripts/approve_narration.py` flips the flag after sign-off.
    script = slide.get("script_th") or slide.get("script_en")
    if script:
        out["script"] = script
        if slide.get("script_approved"):
            out["script_is_approved_copy"] = True
            out["script_approved_by"] = slide.get("script_approved_by", "")
        else:
            out["script_is_draft"] = True
    # An invitation for the guest to say something back, written per slide.
    #
    # Not generated: a robot inventing its own small talk in a sales gallery
    # is a liability, and the good questions are the ones that only make
    # sense on a particular page — "เคยมาเที่ยวจอมเทียนไหมคะ" belongs to the
    # location map and nowhere else. The sales team writes them; this just
    # carries them through.
    ask = slide.get("ask_th") or slide.get("ask_en")
    if ask:
        out["ask"] = ask
    if position:
        out.update(position)
    return out


def _set_current(slide: dict | None, position: dict | None = None) -> dict | None:
    STATE["current"] = _public(slide, position) if slide else None
    return STATE["current"]


def current_slide() -> dict | None:
    return STATE["current"]


def show_current(slide: dict) -> dict:
    """Put a slide on screen from outside the slide tools.

    Used by `search_condo_info` so answering a question also updates the
    picture — the screen contradicting the narration is the worst outcome.
    A running presentation is *paused*, not thrown away: the guest asked a
    question, they didn't cancel the tour.
    """
    STATE["detour"] = True
    return _set_current(slide)


async def _point_canva_at(slide_id: str, *, hold: bool = False) -> None:
    """Send the Canva window to this slide and wait until it is actually there.

    An earlier version returned immediately (`goto`) so a slow browser could
    never stall the conversation. That over-corrected: on the first slide,
    where Chromium is cold-starting for several seconds, the model got its
    tool result back and began narrating over a window still loading the
    cover — the exact "พูดก่อน Canva เปิด" seen in testing.

    So wait — but with a leash. `arrive` is bounded by its own timeout, and
    `registry.dispatch` caps the whole tool call at `TOOL_TIMEOUT_S`, so this
    can hold narration until the picture is up without ever reviving the old
    "never returns" hang. Once the window is warm, arrow-key navigation lands
    in about a tenth of a second, so later slides pay almost nothing; the
    cold first slide is the one that needed the wait.

    Only while the guest is caught up, though. Two things drive the Canva
    window and they had different clocks:

      - `display.show()`, which runs after every slide tool, holds the reveal
        back by however much speech is still queued in the browser
      - this, which moved the window the instant the tool ran

    So on any slide whose narration outlasts SLIDE_TOOL_BUDGET_S, `next_slide`
    stopped waiting with audio still unheard, and Canva jumped to the next
    page while the guest was still hearing the previous one — the further
    ahead, the longer the script. The web display, obeying the lead, stayed
    put; the screenshot showed the two windows on different pages. The p90
    deck script takes ~30s to say against a 6s budget, so the worst slides
    ran the picture almost half a minute early, and the error compounded.

    When audio is still queued there is nothing to do here: `display.show()`
    has already scheduled the reveal, and `_reveal` moves Canva itself. Doing
    it now would only mean doing it early.

    `hold=True` opts out, and the first slide of a tour needs it. "Early"
    only means anything relative to a page the guest is currently being told
    about, and at the start of a tour there isn't one — the window is blank
    and Chromium may still be launching. Deferring there doesn't keep the
    picture honest, it just leaves the robot narrating a cover nobody can
    see yet. Note the lead is normally *not* zero at that moment: the model
    has just said "ได้ค่ะ เดี๋ยวพาชมนะคะ" and that audio is still queued, so
    the lead check alone would skip precisely the case it was written for.
    """
    from app import display
    from app.tools import canva_display

    if not hold and display.remaining_lead() >= 0.2:
        logger.debug(
            "not moving canva to %s yet — %.1fs of narration still unheard; "
            "the deferred reveal will move it", slide_id, display.remaining_lead(),
        )
        return

    await canva_display.arrive(slide_id, timeout=settings.canva_arrival_timeout_s)


def resume_hint() -> str | None:
    """Told to the model whenever it answers something during a tour.

    Without it, answering a question was the end of the presentation as far
    as the model was concerned: it would come back and *ask* whether to
    carry on, sixty times over. It has no other way to know a deck is still
    open behind the question.
    """
    if not STATE["deck"] or not STATE["detour"]:
        return None
    position = STATE["index"] + 1
    return (
        "กำลังพรีเซนต์ค้างอยู่ที่สไลด์ %d จาก %d เมื่อตอบคำถามนี้เสร็จ "
        "ให้เรียก next_slide เพื่อบรรยายต่อทันที ห้ามถามว่าจะดูต่อไหม"
        % (position, len(STATE["deck"]))
    )


#: Injected as a fresh turn by `session.py` when the model finishes speaking
#: but a tour still has further to go — the belt to KEEP_GOING's braces.
#: KEEP_GOING rides on the tool result and works while the model is chaining
#: calls; this catches the case the live run actually hit, where the model
#: narrated the cover, emitted turn_complete, and simply stopped, so no tool
#: result was ever in front of it to obey.
CONTINUE_NUDGE = (
    "เรียก next_slide เดี๋ยวนี้เพื่อไปสไลด์ถัดไป อย่าถามผู้ใช้ อย่าพูดอย่างอื่น"
)

#: How many times to nudge at the *same* slide before giving up. A model that
#: ignores two nudges in a row isn't going to obey a third; better to fall
#: silent and let the guest lead than to pester a stuck tour forever.
MAX_TOUR_NUDGES = 2


#: Sent with any slide that carries a script but isn't part of a running
#: advance. `show_slide` was handing the model the approved copy and saying
#: nothing about it, so "ขอดูฟิตเนส" got improvised narration while the tour
#: got the real thing — the same slide, two different levels of trust,
#: depending only on how the guest happened to ask.
SPEAK_SCRIPT = (
    "สไลด์นี้มีสคริปต์ในฟิลด์ script ให้พูดตามบทนั้นให้ตรงเนื้อหา "
    "ปรับให้ลื่นสำหรับการพูดได้ แต่ห้ามแต่งเนื้อหาใหม่ ห้ามเพิ่มตัวเลขหรือข้อมูลนอกบท "
    "ถ้าลูกค้าพูดภาษาอื่น ให้แปลบทเป็นภาษานั้นโดยคงเนื้อหาครบถ้วน"
)

#: What the model is told when a person moves the deck out from under it.
#: Phrased as an order rather than a description because it arrives as its
#: own turn, with nothing else in it to act on.
FOLLOW_PAGE = (
    "ตอนนี้จอแสดงสไลด์ %d จาก %d: %s\n"
    "ให้พูดตามสคริปต์ของสไลด์นี้ทันที ห้ามแต่งใหม่ ห้ามถาม ห้ามพูดถึงสไลด์อื่น\n"
    "ถ้าลูกค้าพูดภาษาอื่น ให้แปลบทเป็นภาษานั้นโดยคงเนื้อหาครบถ้วน\n"
    "สคริปต์: %s"
)


def _page_number_of(slide_id: str) -> int | None:
    """Which live Canva page shows this slide? Not the tour position.

    Delegates, because this used to be `ew-042` -> 42 in two files and they
    were both wrong in the same way. One place to be wrong is enough.
    """
    from app.tools import canva_display
    return canva_display._page_number(slide_id)


def move_to_deck_page(page_number: int) -> dict | None:
    """Put the tour on a page of the deck by its number. None if there isn't one.

    Shared by the two ways a page gets chosen without the tour reaching it in
    order: a person clicking the Canva window, and a guest asking for it out
    loud. Both mean the same thing — the deck is now here — so both move the
    position rather than treating it as a detour to come back from.
    """
    from app.tools import canva_display

    # Which image is on that page is a measured fact about the live deck,
    # not a sum done on our own filenames. Falls back to the old arithmetic
    # only where nothing has been measured, which is also the only case
    # where the two numbers agree.
    slide_id = (canva_display.slide_on_page(page_number)
                or "%s-%03d" % (settings.canva_deck_prefix, page_number))
    slide = _by_id(slide_id)
    if slide is None:
        return None

    deck = STATE["deck"] or _build_deck(DEFAULT_TOUR)
    if slide_id not in deck:
        return None

    STATE.update({
        "deck": deck,
        "index": deck.index(slide_id),
        "tour": STATE["tour"] or DEFAULT_TOUR,
        "detour": False,
    })
    return _set_current(slide, {"position": STATE["index"] + 1, "total": len(deck)})


def follow_external_page(page_number: int) -> dict | None:
    """A person moved the deck. Move the tour to match, and say what to read.

    Returns the narration order for the model, or None if that page isn't
    part of the deck being presented — a stray click on a different design
    shouldn't drag the conversation somewhere.

    Deliberately also works when no tour is running: the salesperson opening
    the deck and clicking through *is* the presentation, and the robot's job
    is to narrate it.
    """
    # Already there. Re-narrating the slide the tour is on is how a stale
    # reading turns into the deck playing itself over again — the transcript
    # that ran 1..59 and then started at "ยินดีต้อนรับสู่ Embassy World" a
    # second time.
    deck = STATE["deck"]
    if deck and 0 <= STATE["index"] < len(deck):
        here = _page_number_of(deck[STATE["index"]])
        if here == page_number:
            return None

    shown = move_to_deck_page(page_number)
    if shown is None:
        return None
    slide = _by_id(shown["id"])
    script = slide.get("script_th") or slide.get("script_en") or ""
    if not script:
        return None
    return {
        "slide": shown,
        "text": FOLLOW_PAGE % (
            shown["position"], shown["total"],
            slide.get("title_th") or slide.get("title_en") or "",
            script,
        ),
    }


def should_continue_tour() -> bool:
    """Is there a slide the tour still owes the guest?

    Called on every turn_complete, so it has to be cheap and pure — no I/O,
    just the state. True in two cases: the deck has slides after the current
    one, or a question pulled the screen onto a detour that next_slide still
    needs to resume from. False once the last slide has been narrated with no
    detour outstanding, which is where the tour is meant to end and the nudge
    must stop.

    Also false while a slide's question is still hanging in the air. The nudge
    fires on turn_complete, and asking a question *is* completing a turn — so
    without this the robot would ask "เคยมาเที่ยวจอมเทียนไหมคะ" and be pushed
    onto the next slide before the guest could open their mouth, which is a
    worse impression than never asking. The silence after a question is the
    guest's, not a stall.

    It expires, though. A gallery is full of people who won't answer a robot,
    and the tour cannot sit there indefinitely waiting on one of them — after
    `tour_ask_grace_s` the question is treated as declined and the nudge takes
    over, which is what the guest asked for in the first place: pause a beat,
    then carry on by yourself.
    """
    deck = STATE["deck"]
    if not deck:
        return False
    if awaiting_answer():
        return False
    # The guest took the wheel. `go_to_page` is a request about one page, not
    # a place to restart from: they asked "อธิบายสไลด์หน้าที่ 2", heard about
    # page 2, and then the deck set off from page 50 on its own because the
    # advance order rode along with the answer.
    if STATE["manual"]:
        return False
    if STATE["detour"]:
        return True
    return STATE["index"] < len(deck) - 1


def awaiting_answer() -> bool:
    """Is a slide's question still open, and still inside its grace period?"""
    if not STATE["awaiting"]:
        return False
    if time.monotonic() - STATE["asked_at"] >= settings.tour_ask_grace_s:
        logger.info(
            "no answer to the question on %s after %.0fs — carrying on",
            STATE["awaiting"], settings.tour_ask_grace_s,
        )
        STATE["awaiting"] = None
        return False
    return True


def answer_received() -> bool:
    """The guest said something while a question was open.

    Called from the session the moment speech is detected, not when the
    transcript arrives: the point is to stop the grace timer from expiring
    underneath someone who is halfway through answering.
    """
    if not STATE["awaiting"]:
        return False
    logger.info("guest answered the question on %s", STATE["awaiting"])
    STATE["awaiting"] = None
    return True


def _arm_question(slide: dict | None) -> None:
    """Open the waiting period if this slide asks the guest something."""
    if slide and (slide.get("ask_th") or slide.get("ask_en")):
        STATE["awaiting"] = slide["id"]
        STATE["asked_at"] = time.monotonic()
    else:
        STATE["awaiting"] = None


def _narration_instruction(slide: dict | None) -> str:
    """Which set of orders goes out with this slide, and arm the wait.

    Kept in one place on purpose: four call sites hand a slide to the model,
    and a slide whose question is honoured on three of them is worse than one
    that is never asked at all — the guest learns the robot sometimes waits
    and sometimes talks over them.
    """
    _arm_question(slide)
    if slide and (slide.get("ask_th") or slide.get("ask_en")):
        return ASK_AND_WAIT
    return KEEP_GOING


def pause_for_barge_in() -> bool:
    """A guest talked over the narration. Mark the tour paused so the next
    advance re-narrates the current slide instead of walking past it.

    The deck position was never lost — it lives in `STATE["index"]` — but a
    plain barge-in (not a "show me X" request) wasn't being treated as a
    pause, so the server's advance nudge, firing after the reply, stepped the
    deck forward over a slide the guest had not actually heard. Reusing the
    detour flag makes `next_slide` resume the same slide, the way returning
    from a question already does. No-op outside a running tour.

    `unheard` is what keeps resuming from turning into repeating. It is set
    *only* here, because being talked over is the only case where the slide
    still owes the guest something. Someone who waits for the narration to
    finish and *then* asks a question also leaves the tour on a detour — but
    they heard the page, and replaying it at them isn't courtesy, it's the
    robot not having noticed.
    """
    if not STATE["deck"]:
        return False
    STATE["detour"] = True
    STATE["unheard"] = True
    return True


def _by_id(slide_id: str) -> dict | None:
    return next((s for s in load_slides() if s["id"] == slide_id), None)


def _build_deck(tour: str) -> list[str]:
    """The slides of a tour, in the order they will be presented.

    For the sales deck that order comes from Canva, because Canva is what
    the sales team edits and what the guest is looking at. Filename order is
    a fact about a folder; page order is a fact about the presentation.
    Pages the measurement couldn't place are left out rather than guessed
    at — see `scripts/canva_pages.py`.

    Every other tour is still assembled from slide types here: those are
    ours, they have no Canva pages, and they are answers to questions rather
    than a document.
    """
    if tour == "deck":
        from app.tools import canva_display
        known = set(s["id"] for s in load_slides())
        ordered = [sid for sid in canva_display.deck_order() if sid in known]
        if ordered:
            return ordered
        # Never measured. Present the export in its own order, as before.
    wanted = TOURS.get(tour, [])
    deck: list[str] = []
    for slide_type in wanted:
        deck += [s["id"] for s in load_slides() if s.get("type") == slide_type]
    return deck


# ---------------------------------------------------------------- tools


@tool(
    name="show_slide",
    description=(
        "แสดงภาพหรือสไลด์บนจอให้ลูกค้าดู ใช้เมื่อลูกค้าขอดูอะไรก็ตาม เช่น ผังห้อง แปลนชั้น "
        "สิ่งอำนวยความสะดวก สระว่ายน้ำ ฟิตเนส ทำเลที่ตั้ง หรือเมื่อคุณกำลังอธิบายสิ่งที่ควรเห็นภาพประกอบ "
        "ระบุสิ่งที่ต้องการแสดงเป็นคำค้นภาษาไทยหรืออังกฤษ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "สิ่งที่ต้องการแสดง เช่น 'ผังห้อง 2 ห้องนอน', 'สระว่ายน้ำ', 'swimming pool'",
            }
        },
        "required": ["query"],
    },
    tags=["slides"],
)
def show_slide(query: str) -> dict:
    slides = load_slides()
    if not slides:
        return {"ok": False, "error": "no slides available"}

    hits = search_slides(query)
    if not hits or not hits[0].found:
        # Say so plainly instead of showing something unrelated — a wrong
        # floor plan on screen is worse than none. Judged on how much of the
        # question was actually found, not on rank: rank only says "this is
        # the closest slide there is", and for a query with nothing to match
        # ("ราคาหุ้นวันนี้") something is always closest.
        return {
            "ok": False,
            "error": "no matching slide",
            "hint": "ไม่พบภาพที่ตรงกับคำค้นนี้",
        }

    STATE["detour"] = True
    shown = _set_current(hits[0].slide)
    alternatives = [h.slide["id"] for h in hits[1:4] if h.found]
    out = {"ok": True, "slide": shown, "alternatives": alternatives}
    if shown.get("script"):
        out["instruction"] = SPEAK_SCRIPT
    resume = resume_hint()
    if resume:
        out["presentation"] = resume
    return out


@tool(
    name="start_presentation",
    description=(
        "เริ่มพรีเซนต์สไลด์เป็นชุดตามลำดับ ใช้เมื่อลูกค้าขอให้แนะนำโครงการ พาชม หรือขอดูภาพรวม "
        "หลังเรียกแล้วให้บรรยายสไลด์แรกสั้นๆ 2-3 ประโยค แล้วเรียก next_slide เพื่อไปสไลด์ถัดไปเอง "
        "ทำแบบนี้ต่อเนื่องจนจบ ถ้าลูกค้าพูดแทรกให้หยุดแล้วตอบคำถามก่อน"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tour": {
                "type": "string",
                "enum": list(TOURS.keys()),
                "description": (
                    "ชุดสไลด์ deck=ชุดพรีเซนต์ทางการของโครงการ (ใช้ตัวนี้เป็นหลัก) "
                    "overview=ภาพรวม facilities=สิ่งอำนวยความสะดวก floorplans=ผังห้อง location=ทำเล"
                ),
            }
        },
    },
    tags=["slides"],
)
async def start_presentation(tour: str = DEFAULT_TOUR) -> dict:
    chosen = tour if tour in TOURS else DEFAULT_TOUR
    deck = _build_deck(chosen)
    if not deck and chosen != "overview":
        # No official deck imported yet — fall back rather than refusing.
        chosen = "overview"
        deck = _build_deck(chosen)
    if not deck:
        return {"ok": False, "error": f"no slides for tour '{chosen}'"}

    # `parked` goes too: starting from the cover is an explicit decision to
    # abandon wherever the last tour stopped, and leaving it behind would let
    # a later `resume_presentation` yank the guest back to a slide from a
    # presentation that is no longer running.
    STATE.update({"deck": deck, "index": 0, "tour": chosen, "detour": False,
                  "manual": False, "parked": None})
    slide = _by_id(deck[0])
    shown = _set_current(slide, {"position": 1, "total": len(deck)})
    # Wait for the cover to be on the Canva window before handing back — the
    # instant this returns the model starts narrating, and slide one is the
    # cold-start case where the window is slowest. Bounded, see
    # `_point_canva_at`. Advancing past the cover is not this call's job: the
    # model does it via next_slide, backed by the server-side nudge in
    # session.py for when it narrates and then stalls.
    # hold: the cold-start case. Blank window, Chromium possibly still
    # launching, and no page the guest is mid-way through hearing about.
    await _point_canva_at(deck[0], hold=True)
    return {"ok": True, "tour": chosen, "total": len(deck), "slide": shown,
            "instruction": _narration_instruction(slide)}


@tool(
    name="next_slide",
    description=(
        "ไปสไลด์ถัดไปในชุดที่กำลังพรีเซนต์ เรียกหลังบรรยายสไลด์ปัจจุบันจบแล้ว "
        "ถ้าผลลัพธ์บอกว่า finished แปลว่าจบชุดแล้ว ให้สรุปสั้นๆ และถามลูกค้าว่าสนใจเรื่องใดเพิ่มเติม"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
    # Holds the call open until the guest has heard the slide being left.
    # That is the point of it, not a performance bug — see wait_until_heard.
    paced=True,
)
async def next_slide() -> dict:
    deck = STATE["deck"]
    if not deck:
        return {"ok": False, "error": "no presentation in progress"}

    # Hold here until the guest has actually heard the narration for the
    # slide we're leaving, then leave a beat before moving. Without it the
    # model advances at the speed it can generate — through the whole deck in
    # about a minute, with minutes of speech queued behind it and no way for
    # anyone to interrupt out. See app/display.py:wait_until_heard.
    #
    # This holds the *function call* open for as long as the narration takes
    # to speak, which on a realtime session is a long time to make the model
    # wait. `SLIDE_TOOL_BUDGET_S` caps it: better a slightly early slide than
    # a tour that stops dead because the call was held too long.
    import time as _time

    from app import display

    started = _time.monotonic()
    budget = settings.slide_tool_budget_s
    heard = await display.wait_until_heard(
        max_wait=budget, then_pause=settings.slide_pause_s
    )

    # Coming back from a detour, "next" means the slide the tour was on —
    # the guest hasn't seen it narrated yet, they went off to look at the gym
    # before it got there.
    # Coming back from a detour: resume only what was actually cut off.
    if STATE["detour"]:
        STATE["detour"] = False
        was_cut_off = STATE["unheard"]
        STATE["unheard"] = False
        if was_cut_off and 0 <= STATE["index"] < len(deck):
            slide = _by_id(deck[STATE["index"]])
            shown = _set_current(
                slide, {"position": STATE["index"] + 1, "total": len(deck)}
            )
            await _point_canva_at(deck[STATE["index"]])
            STATE["awaiting"] = None
            return {"ok": True, "finished": False, "resumed": True,
                    "slide": shown, "instruction": KEEP_GOING}

    if STATE["index"] + 1 >= len(deck):
        # A finished deck stops being a deck.
        #
        # It used to be left in STATE, and that turned every later request
        # into a relapse: the guest says "ขอดูฟิตเนส", `show_slide` sets
        # `detour`, `should_continue_tour()` sees the detour and says yes, the
        # nudge fires, and `next_slide` narrates slide 59 over the top of the
        # thing they actually asked to see. From the guest's side the robot
        # answered them and then started presenting again unprompted.
        #
        # Keeping the position is only worth anything while there is a slide
        # still owed. At the end there isn't one, so holding on to it buys
        # nothing and costs that.
        total = len(deck)
        logger.info("next_slide: deck finished after %.1fs", _time.monotonic() - started)
        keep = STATE["current"]
        reset_state()
        STATE["current"] = keep          # the last slide stays on screen
        return {
            "ok": True, "finished": True, "total": total,
            "instruction": (
                "จบชุดพรีเซนต์แล้ว ไม่มีสไลด์เหลือ ห้ามเรียก next_slide อีก "
                "ให้สรุปสั้นๆ แล้วถามลูกค้าว่าสนใจเรื่องใดเพิ่มเติม"
            ),
        }

    STATE["manual"] = False      # an explicit advance means the tour is live
    STATE["index"] += 1
    slide = _by_id(deck[STATE["index"]])
    shown = _set_current(slide, {"position": STATE["index"] + 1, "total": len(deck)})
    await _point_canva_at(deck[STATE["index"]])
    logger.info(
        "next_slide -> %s (%d/%d): waited %.1fs for audio, %.1fs total",
        deck[STATE["index"]], STATE["index"] + 1, len(deck),
        heard, _time.monotonic() - started,
    )
    return {"ok": True, "finished": False, "slide": shown,
            "instruction": _narration_instruction(slide)}


@tool(
    name="go_to_page",
    description=(
        "ไปยังหน้าสไลด์ตามหมายเลขหน้าที่ลูกค้าระบุ ใช้เมื่อลูกค้าพูดถึงเลขหน้าโดยตรง "
        "เช่น 'ไปหน้า 24' 'ขอดูหน้าที่ 5' 'ย้อนกลับไปหน้า 3' "
        "ถ้าลูกค้าบอกชื่อเรื่องแทนเลขหน้า ให้ใช้ show_slide"
    ),
    parameters={
        "type": "object",
        "properties": {
            "page": {
                "type": "integer",
                "description": "หมายเลขหน้าในชุดพรีเซนต์ เริ่มที่ 1",
            }
        },
        "required": ["page"],
    },
    tags=["slides"],
)
async def go_to_page(page: int) -> dict:
    """Jump the deck to a page the guest named out loud.

    `show_slide` matches text, and a number is terrible text: "หน้า 24",
    "24" and "ไปหน้าที่ 24" each landed on a different slide and none of
    them was page 24. A page number isn't a topic to search for, it's a
    coordinate, so it gets its own way in.

    Moves the tour rather than treating it as a detour — asking for a page
    by number *is* saying where the presentation should be now.
    """
    try:
        number = int(page)
    except (TypeError, ValueError):
        return {"ok": False, "error": "page must be a number"}

    shown = move_to_deck_page(number)
    if shown is None:
        total = len(STATE["deck"] or [s["id"] for s in load_slides()
                                      if s.get("type") == "deck"])
        return {
            "ok": False,
            "error": "no such page",
            "instruction": "ไม่มีหน้านี้ในชุดพรีเซนต์ (มี %d หน้า) ให้บอกลูกค้าตรงๆ" % total,
        }

    await _point_canva_at(shown["id"])
    out = {"ok": True, "slide": shown}
    # SPEAK_SCRIPT, never KEEP_GOING. KEEP_GOING ends with "พูดจบแล้วเรียก
    # next_slide ทันที" — the order that makes a *tour* advance itself. Sent
    # here it turned "อธิบายสไลด์หน้าที่ 50" into a presentation starting at
    # slide 50: the model answered the question and then kept going, page
    # after page, because the answer carried the instruction to.
    STATE["manual"] = True
    out["instruction"] = SPEAK_SCRIPT if shown.get("script") else SPEAK_SCRIPT
    ask = shown.get("ask")
    if ask:
        out["instruction"] += " ถามคำถามในฟิลด์ ask ต่อท้ายด้วย แล้วรอคำตอบ"
    return out


@tool(
    name="previous_slide",
    description="ย้อนกลับไปสไลด์ก่อนหน้า ใช้เมื่อลูกค้าขอให้กลับไปดูภาพที่แล้ว",
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
)
def previous_slide() -> dict:
    deck = STATE["deck"]
    if not deck:
        return {"ok": False, "error": "no presentation in progress"}
    if STATE["index"] <= 0:
        return {"ok": False, "error": "already at the first slide"}

    STATE["index"] -= 1
    slide = _by_id(deck[STATE["index"]])
    shown = _set_current(slide, {"position": STATE["index"] + 1, "total": len(deck)})
    return {"ok": True, "slide": shown}


@tool(
    name="hide_slide",
    description="ปิดภาพบนจอ กลับไปหน้าจอเปล่า ใช้เมื่อลูกค้าดูเสร็จแล้วหรือเปลี่ยนไปคุยเรื่องอื่น",
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
)
def hide_slide() -> dict:
    # reset_state() rather than a hand-written update: this used to list the
    # keys it cleared and had already fallen behind, leaving `detour`,
    # `awaiting` and `unheard` set on a deck that no longer existed.
    reset_state()
    _set_current(None)
    return {"ok": True, "cleared": True}


@tool(
    name="close_presentation",
    description=(
        "ปิดหน้าต่างพรีเซนต์ทั้งหมด ใช้เมื่อลูกค้าบอกว่า ปิดพรีเซนต์ ปิดสไลด์ ปิดจอ "
        "ปิดหน้าต่าง เลิกดูแล้ว close ทั้งจอเต็มและหน้าต่าง Canva จะถูกปิด"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
    # Closing a browser window is slower than any other slide tool and there
    # is nothing to be gained by racing it: the guest asked for the screen to
    # go away, so the reply should come after it has.
    paced=True,
)
async def close_presentation() -> dict:
    """Stop narrating, blank the display, and close the Canva window.

    Distinct from `stop_presentation`, which leaves the picture up. The reason
    to separate them is the fullscreen window: once Canva fills a wall-mounted
    screen there has to be a way to say "that's enough" out loud, or the only
    exit is a keyboard nobody standing in a sales gallery has.

    Distinct from `hide_slide` too, which blanks the display but leaves the
    browser running and parked — the right thing between two guests, the wrong
    thing at the end of the day.
    """
    from app.tools import canva_display

    reset_state()
    _set_current(None)
    try:
        await canva_display.shutdown()
    except Exception:
        # The screen is already logically off; a browser that won't close is
        # a problem for the next launch, which handles a dead handle anyway.
        logger.exception("could not close the canva window")
    return {
        "ok": True, "cleared": True, "closed": True,
        "instruction": "ปิดหน้าต่างพรีเซนต์แล้ว ตอบรับสั้นๆ ห้ามเรียกเครื่องมือสไลด์อีกจนกว่าลูกค้าจะขอ",
    }


@tool(
    name="stop_presentation",
    description=(
        "หยุดการพรีเซนต์ชั่วคราว ใช้เฉพาะเมื่อลูกค้าสั่งให้หยุดตรงๆ เท่านั้น เช่น "
        "'หยุด' 'พอแล้ว' 'พอก่อน' 'ไม่ต้องพรีเซนต์แล้ว' 'stop' 'พักก่อน' — "
        "ภาพบนจอยังอยู่เหมือนเดิม และเรียก resume_presentation กลับมาดูต่อได้ "
        # Every clause after this is a live mistake, quoted. The model called
        # this tool when a guest asked "Can you speak Chinese?" — it heard a
        # change of subject as a change of mind, killed the deck at slide 2,
        # and there was then no way back. A guest who asks a question has not
        # asked for the presentation to end; answering *is* the interruption
        # handling, and the tour resumes by itself afterwards.
        "ห้ามใช้เมื่อลูกค้าแค่ถามคำถาม ขอเปลี่ยนภาษา ขอให้พูดช้าลง หรือพูดแทรก — "
        "กรณีเหล่านั้นให้ตอบคำถามไปตามปกติแล้วพรีเซนต์ต่อ ไม่ต้องเรียกเครื่องมือนี้"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
)
def stop_presentation() -> dict:
    """End the tour but leave the picture up.

    Added because a live session showed there was no way to do it. A guest
    said "หยุด พรีเซนต์", the model answered "ได้ค่ะ หยุดพรีเซนต์เรียบร้อย
    แล้วค่ะ" — and then narrated the next slide anyway, because saying so was
    all it could do. `STATE["deck"]` was untouched, `should_continue_tour()`
    stayed true, and the server's own nudge pushed the deck onward. The guest
    asked twice and was overridden twice.

    `hide_slide` did clear the deck, but its description is about blanking the
    screen, and the model never once called it in a full 59-slide session.
    Stopping the narration and blanking the screen are different requests:
    somebody who says "พอแล้ว" while looking at the gym usually wants to keep
    looking at the gym.
    """
    was_running = bool(STATE["deck"])
    position = STATE["index"] + 1
    total = len(STATE["deck"])
    keep = STATE["current"]
    # Park it before clearing. `unheard` decides where a resume starts: a
    # guest talked over mid-narration has not heard this slide, so resuming
    # means saying it again; one who let it finish and then said "พอแล้ว" has,
    # so resuming means the next one.
    parked = None
    if was_running:
        parked = {
            "tour": STATE["tour"],
            "index": STATE["index"],
            "unheard": bool(STATE["unheard"] or STATE["detour"]),
            "total": total,
        }
    reset_state()
    STATE["current"] = keep          # the screen does not change
    STATE["parked"] = parked
    if not was_running:
        return {"ok": True, "stopped": False,
                "instruction": "ไม่มีการพรีเซนต์ค้างอยู่ ให้ตอบสั้นๆ แล้วถามว่าต้องการอะไรต่อ"}
    logger.info("presentation stopped by the guest at slide %d/%d — parked", position, total)
    return {
        "ok": True, "stopped": True, "stopped_at": position, "total": total,
        "resumable": True,
        "instruction": (
            "หยุดพรีเซนต์แล้ว ห้ามบรรยายสไลด์ต่ออีก ห้ามเรียก next_slide "
            "ตอบรับสั้นๆ แล้วบอกลูกค้าว่าถ้าอยากดูต่อบอกได้ตลอด "
            "แล้วถามว่าอยากดูอะไรหรือถามอะไรเพิ่ม"
        ),
    }


@tool(
    name="resume_presentation",
    description=(
        "กลับมาพรีเซนต์ต่อจากจุดที่หยุดไว้ ใช้เมื่อลูกค้าบอกว่า ไปต่อ ดูต่อ พรีเซนต์ต่อ "
        "เมื่อกี้ถึงไหนแล้ว continue go on — ไม่เริ่มใหม่จากสไลด์แรก "
        "ถ้าไม่มีอะไรค้างไว้ผลลัพธ์จะบอก แล้วค่อยใช้ start_presentation แทน"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["slides"],
    paced=True,
)
async def resume_presentation() -> dict:
    """Pick a stopped tour back up where it was left.

    Written because a live session had no way to do this. A guest asked "Can
    you speak Chinese?" two slides into the deck, the model called
    `stop_presentation`, and `stop_presentation` called `reset_state()` — deck,
    position and tour all gone. Emma answered the question in Chinese, and
    that was the end of the presentation: the only route back was
    `start_presentation`, which starts at slide one, so the guest was offered
    the cover again after having already seen it.

    Stopping and finishing are not the same event, and only one of them should
    cost the deck. The position was already sitting in `STATE["index"]`; all
    that was missing was somewhere to keep it and a way to ask for it back.
    """
    parked = STATE["parked"]
    if not parked:
        return {"ok": False, "resumed": False, "error": "nothing parked",
                "instruction": ("ไม่มีการพรีเซนต์ค้างอยู่ ถ้าลูกค้าอยากดู "
                                "ให้เรียก start_presentation เริ่มใหม่")}

    tour = parked.get("tour") or DEFAULT_TOUR
    deck = _build_deck(tour)
    if not deck:
        STATE["parked"] = None
        return {"ok": False, "resumed": False, "error": f"no slides for tour '{tour}'"}

    # The deck is rebuilt from the tour name rather than stored, so it cannot
    # go stale against `data/slides/` — but that also means the parked index
    # has to be re-checked against the length it lands in.
    index = min(max(int(parked.get("index", 0)), 0), len(deck) - 1)
    if not parked.get("unheard"):
        # They heard this one to the end before stopping. Carry on past it.
        if index + 1 >= len(deck):
            STATE["parked"] = None
            return {"ok": True, "resumed": False, "finished": True,
                    "instruction": ("พรีเซนต์ถึงสไลด์สุดท้ายไปแล้ว ให้สรุปสั้นๆ "
                                    "แล้วถามว่าสนใจเรื่องใดเพิ่มเติม")}
        index += 1

    STATE.update({"deck": deck, "index": index, "tour": tour, "detour": False,
                  "unheard": False, "manual": False, "parked": None})
    slide = _by_id(deck[index])
    shown = _set_current(slide, {"position": index + 1, "total": len(deck)})
    await _point_canva_at(deck[index], hold=True)
    logger.info("presentation resumed at slide %d/%d", index + 1, len(deck))
    return {"ok": True, "resumed": True, "slide": shown,
            "position": index + 1, "total": len(deck),
            "instruction": _narration_instruction(slide)}
