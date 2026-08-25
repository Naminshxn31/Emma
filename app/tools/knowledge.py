"""
Look things up about the project — retrieval, without showing a picture.

The slide library is already a body of project knowledge: 144 entries with
Thai and English titles, summaries and (once written) narration scripts. But
until now it was only reachable through `show_slide`, which puts an image on
the screen. A guest who just asks "มีฟิตเนสไหม" doesn't want a picture, and
the model had nothing to answer from — the whole library was invisible to it
unless it decided to display something.

This is the retrieval half of RAG over data that already exists. It is
deliberately *not* stuffed into the system instructions: the Live API
re-processes and re-bills those on every single turn, so 144 summaries in
the prompt would make every reply slower and more expensive, including the
ones that never mention the project.

What it is not: a source of prices or promotions. Those live in
the approved facts file (data/condo_facts.json). Slide summaries are
descriptive, and the prompt forbids quoting numbers that aren't in
that file.
"""
from __future__ import annotations

import logging

from app.tools.registry import tool
from app import turnlog
from app.tools.slides import confident_enough_to_show, load_slides

logger = logging.getLogger("condo_voice.knowledge")

MAX_RESULTS = 4

#: Commercial terms this tool must never try to answer. The rule that slide
#: summaries are not a pricing source was written down at the top of this
#: file and then left to fuzzy matching to enforce, which it can't: asking
#: "ราคาโครงการ" scored highly against every slide containing โครงการ — a
#: word in a third of the deck — and put a photo of the executives on screen
#: next to a reply that correctly said there was no pricing yet.
#:
#: No amount of scoring tuning fixes that, because the match is real; it's
#: the *question* that has no answer here. Cheaper and far more predictable
#: to state which questions those are. Prices, promotions and payment terms
#: live in data/condo_facts.json, where a human signs them off.
#: A gallery in Thailand gets Chinese, Russian, Japanese and Korean buyers,
#: and the first thing any of them asks is the price. Listing only Thai and
#: English words meant 价格是多少 and "цена" walked straight past the
#: guardrail — the reported bug, in a language nobody had tested.
# The commercial-question guard lives in `app.tools.retrieval` now — it has
# to protect *every* retrieval surface, and `search_my_documents` could not
# import it from here: importing this module registers the knowledge tools
# as a side effect, so a machine whose TOOL_GROUPS excludes `knowledge`
# would have sprouted `search_condo_info` just by using the web library.
# retrieval is the one shared module with no @tool in it.
from app.tools.retrieval import is_commercial as _is_commercial  # noqa: E402


def _entry(slide: dict) -> dict:
    """One search hit, trimmed to what's useful to speak from."""
    out = {
        "title": slide.get("title_th") or slide.get("title_en"),
        "detail": slide.get("summary_th") or slide.get("summary_en") or "",
        "topic": slide.get("type"),
        # Two extra fields with different standing, kept apart on purpose:
        #
        #   slide_text — every word printed on the slide. The developer's own
        #     copy, as approved as the picture the guest is looking at.
        #   description — written by a model looking at the image. Good for
        #     describing a room out loud, never a source for a number.
        #
        # Collapsing them would let a generated sentence be quoted with the
        # authority of the deck.
        # So the model can offer to show it: "อยากดูภาพไหมคะ"
        "slide_query": slide.get("title_th") or slide.get("title_en"),
    }
    slide_text = slide.get("transcript_th") or slide.get("transcript_en")
    if slide_text:
        out["slide_text"] = slide_text[:400]
    description = slide.get("detail_th") or slide.get("detail_en")
    if description:
        out["description"] = description[:600]
    script = slide.get("script_th") or slide.get("script_en")
    if script:
        out["approved_script"] = script
    return out


@tool(
    name="search_condo_info",
    description=(
        "ค้นหาข้อมูลเกี่ยวกับโครงการเพื่อใช้ตอบคำถามลูกค้า โดยไม่ต้องเปิดภาพ "
        "ใช้เมื่อลูกค้าถามว่าโครงการมีอะไรบ้าง มีสิ่งอำนวยความสะดวกอะไร อยู่ชั้นไหน "
        "หรือถามรายละเอียดที่คุณยังไม่รู้ ให้เรียกเครื่องมือนี้ก่อนตอบเสมอ "
        "ห้ามเดาเอง ถ้าไม่พบข้อมูลให้บอกลูกค้าตรงๆ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "สิ่งที่ต้องการค้นหา เช่น 'ฟิตเนส', 'สระว่ายน้ำอยู่ชั้นไหน', 'facilities'",
            }
        },
        "required": ["query"],
    },
    # Tagged as a slide tool too: it changes what's on screen, so the
    # registry must push the update out to the displays.
    tags=["knowledge", "slides"],
)
def search_condo_info(query: str) -> dict:
    slides = load_slides()
    if not slides:
        return {"ok": False, "error": "no project information available"}

    if _is_commercial(query):
        logger.info("lookup %r: commercial question, not answerable from slides", query)
        # Worth its own event. These are the questions customers most want
        # answered and the ones the robot is least able to answer, so how
        # often they come up is a business fact, not just a debugging one —
        # it is the evidence for filling in the blanks in the facts file.
        turnlog.record("commercial_question", query=query)
        return {
            "ok": True,
            "found": False,
            "results": [],
            "instruction": (
                "ข้อมูลราคาและโปรโมชั่นไม่มีในคลังภาพ ห้ามเดาหรืออ้างอิงจากคำบรรยายภาพ "
                "ให้บอกลูกค้าตรงๆ ว่ายังไม่มีข้อมูลส่วนนี้ แล้วแนะนำให้ติดต่อฝ่ายขาย"
            ),
        }

    from app.tools import slides as slides_mod

    ranked = slides_mod.search_slides(query)
    # **The top hit decides whether anything is answered at all.**
    #
    # The line above used to read `[h.slide for h in ranked if h.found]`, which
    # is not what the comment beside it claimed. Ranking is Reciprocal Rank
    # Fusion of BM25 and cosine, so the first hit is the best *compromise*, not
    # the most similar — an eighth-placed slide can carry a higher cosine than
    # the first. Scanning the whole list therefore let a slide nobody ranked
    # highly answer the question on its own.
    #
    # That is exactly the gap between `scripts/eval_search.py`, which judges
    # `hits[0]`, and this tool, which judged all of them: the eval reported
    # every nonsense question rejected while two tests asserting the same thing
    # still failed. The measurement was right about the statistic it measured.
    #
    # So make the code match the comment. If the closest slide isn't close
    # enough, there is no answer here — and the rest of the list, which by
    # definition ranked worse, does not get a second vote.
    if not ranked or not ranked[0].found:
        hits = []
    else:
        hits = [h.slide for h in ranked if h.found][:MAX_RESULTS]

    if not hits:
        # The single most useful line in the log.
        #
        # Every other event says what the robot did; this one says what it
        # *couldn't* do, in the guest's own words. The one improvement that
        # would actually make search better is keywords on the slides, and
        # the words worth adding are the ones real guests used and missed
        # with — not ones invented at a desk. Without this they are lost the
        # moment the sentence ends.
        #
        # `best` is kept even though nothing was shown: "asked about the gym,
        # closest match was the yoga slide, still not close enough" is a
        # different fix from "asked about a golf course we don't have".
        turnlog.record(
            "lookup", query=query, showed=None, found=False,
            best=(ranked[0].slide.get("id") if ranked else None),
            best_title=(ranked[0].slide.get("title_th") if ranked else None),
            reason="nothing matched",
        )
        return {
            "ok": True,
            "found": False,
            "results": [],
            # Spelled out so the model doesn't fill the silence with a guess.
            "instruction": "ไม่พบข้อมูลนี้ ให้บอกลูกค้าตรงๆ ว่าไม่มีข้อมูล และแนะนำให้ติดต่อเจ้าหน้าที่ฝ่ายขาย ห้ามเดา",
        }

    logger.info("knowledge lookup %r -> %d hit(s)", query, len(hits))

    result = {
        "ok": True,
        "found": True,
        "results": [_entry(s) for s in hits],
        "note": "ข้อมูลนี้เป็นคำบรรยายภาพ ห้ามใช้อ้างอิงราคาหรือโปรโมชั่น",
    }

    # Put the best match on screen as part of answering — but only when it's
    # clearly the right picture. Leaving the display to a separate show_slide
    # call meant the assistant described a sauna while a lounge stayed up;
    # changing it on *every* lookup was the opposite failure, and worse:
    # asking about pricing swapped the screen to an unrelated slide, so the
    # picture contradicted an answer that was itself correct ("no pricing
    # yet"). When the match is doubtful, leave the screen alone.
    if not confident_enough_to_show(ranked, query):
        top = hits[0].get("title_th") or hits[0].get("title_en")
        logger.info("lookup %r: match too weak to change the screen (%s)", query, top)
        # Logged as a decision, not a non-event. "ทำไมไม่ขึ้นสไลด์" and
        # "ทำไมขึ้นสไลด์ผิด" are the same investigation, and it needs the
        # rejected candidate to be answerable.
        turnlog.record(
            "lookup", query=query, showed=None, best=hits[0].get("id"),
            best_title=top, reason="below the show threshold",
        )
        return result

    turnlog.record(
        "lookup", query=query, showed=hits[0].get("id"),
        title=hits[0].get("title_th"),
        runners_up=[h.get("id") for h in hits[1:4]],
    )
    result["now_showing"] = slides_mod.show_current(hits[0])
    resume = slides_mod.resume_hint()
    if resume:
        result["presentation"] = resume
    result["note"] = "แสดงภาพที่ตรงกับคำตอบบนจอแล้ว " + result["note"]
    return result
