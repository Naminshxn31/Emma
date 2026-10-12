"""
Look things up about the project — retrieval, without showing a picture.

The slide catalog is a search index for presentation assets, not an approved
fact database. Only records with customer disclosure metadata may contribute
text to a response. An unreviewed image can still be displayed separately.

This is the retrieval half of RAG over data that already exists. It is
deliberately *not* stuffed into the system instructions: the Live API
re-processes and re-bills those on every single turn, so 144 summaries in
the prompt would make every reply slower and more expensive, including the
ones that never mention the project.

It is never a source of prices or promotions. Those need a separately
approved source; a description inferred from an image is not approval.
"""
from __future__ import annotations

import logging

from app.tools.registry import tool
from app import turnlog
from app.tools.slides import confident_enough_to_show, load_slides
from app.config import settings
from app.knowledge_policy import evaluate_claim, state_instruction
from app.presentation_policy import customer_slide_trace

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
#: live in the project facts source, where a human signs them off.
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
from app.tools.retrieval import (  # noqa: E402
    is_commercial as _is_commercial,
    sanitize_common_area_dimensions,
)


_BROAD_QUERY_TERMS = (
    "อะไรบ้าง", "มีอะไร", "ทั้งหมด", "ภาพรวม", "สิ่งอำนวยความสะดวก",
    "overview", "all facilities", "what facilities",
)


def _result_limit(query: str) -> int:
    """Specific questions get one answer; explicit overviews may get several."""
    lowered = (query or "").strip().lower()
    return MAX_RESULTS if any(term in lowered for term in _BROAD_QUERY_TERMS) else 1


def _entry(slide: dict) -> dict:
    """One search hit, trimmed to what's useful to speak from."""
    decision = evaluate_claim(slide, settings.project_id)
    if not decision.allowed or slide.get("source_id") != "slide_catalog":
        return {"policy_trace": customer_slide_trace(
            decision, settings.project_id,
            valid_source=slide.get("source_id") == "slide_catalog")}
    out = {
        "source_id": slide["source_id"],
        "project_id": slide["project_id"],
        "policy_trace": customer_slide_trace(decision, settings.project_id),
        "content_state": decision.content_state,
        "title": sanitize_common_area_dimensions(
            slide.get("title_th") or slide.get("title_en")
        ),
        "detail": sanitize_common_area_dimensions(
            slide.get("summary_th") or slide.get("summary_en") or ""
        ),
        "topic": slide.get("type"),
        # Two extra fields with different standing, kept apart on purpose:
        #
        #   slide_text — words printed on a slide whose source metadata
        #     explicitly passed customer disclosure above.
        #   description — written by a model looking at the image. Good for
        #     describing a room out loud, never a source for a number.
        #
        # Collapsing them would let a generated sentence be quoted with the
        # authority of the deck.
        # So the model can offer to show it: "อยากดูภาพไหมคะ"
        "slide_query": sanitize_common_area_dimensions(
            slide.get("title_th") or slide.get("title_en")
        ),
    }
    slide_text = slide.get("transcript_th") or slide.get("transcript_en")
    if slide_text:
        out["slide_text"] = sanitize_common_area_dimensions(slide_text[:400])
    description = slide.get("detail_th") or slide.get("detail_en")
    if description:
        out["description"] = sanitize_common_area_dimensions(description[:600])
    script = slide.get("script_th") or slide.get("script_en")
    if script and slide.get("script_approved") and slide.get("script_approved_by"):
        out["approved_script"] = sanitize_common_area_dimensions(script)
    elif script:
        out["script_is_draft"] = True
    caution = state_instruction(decision.content_state)
    if caution:
        out["content_state_instruction"] = caution
    return out


def _sanitize_now_showing(value: dict | None) -> dict | None:
    if not value:
        return value
    out = dict(value)
    for key in ("title_th", "title_en", "summary_th", "summary_en", "script"):
        if out.get(key):
            out[key] = sanitize_common_area_dimensions(out[key])
    return out


def _no_picture(result: dict) -> dict:
    """Say so when a machine that shows answer pictures did not show one.

    2026-10-11 eval: "มีรูปไหม" searched "facilities", the match was too
    broad to change the screen, and Emma still said "มีภาพสิ่งอำนวยความ
    สะดวกและผังโครงการให้ชมบนจอค่ะ" to a guest looking at nothing. The
    prompt allows that sentence only after "แสดงภาพ...บนจอแล้ว"; silence in
    the result was not enough. Only with ANSWER_IMAGES, so the sales room's
    results are unchanged.
    """
    if settings.answer_images:
        result["note"] = "ไม่ได้ขึ้นภาพบนจอ ห้ามพูดว่ามีภาพให้ชม " + result["note"]
    return result


def _slides_on_screen() -> bool:
    """May answering put the matching picture on screen? Yes where this
    machine presents slides at all (TOOL_GROUPS blank = every group, the
    gallery default, or listing `slides` — the gate the prompt uses for its
    slide rules), and yes where ANSWER_IMAGES asks for the pictures without
    the presentation (owner 2026-10-11, the robot)."""
    groups = settings.enabled_tool_groups()
    return groups is None or "slides" in groups or settings.answer_images


@tool(
    name="search_condo_info",
    description=(
        "ค้นหาข้อมูลเกี่ยวกับโครงการเพื่อใช้ตอบคำถามลูกค้า โดยไม่ต้องเปิดภาพ "
        "ใช้เมื่อลูกค้าถามว่าโครงการมีอะไรบ้าง มีสิ่งอำนวยความสะดวกอะไร อยู่ชั้นไหน "
        "หรือถามรายละเอียดที่คุณยังไม่รู้ ให้เรียกเครื่องมือนี้ก่อนตอบเสมอ "
        "ส่ง intent ของคำถามให้ครบ โดยเฉพาะคำว่าอยู่ตรงไหนหรืออยู่ชั้นไหน "
        "ห้ามย่อเหลือเพียงชื่อสถานที่จน intent ตำแหน่งหาย "
        "ห้ามเดาเอง ถ้าไม่พบข้อมูลห้ามพูดว่าไม่มีข้อมูลหรือไม่ทราบ ให้ส่งต่อทีมขายอย่างเป็นธรรมชาติ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "คำถามที่คง intent ของลูกค้าครบ เช่น 'ฟิตเนส', "
                    "'สระว่ายน้ำอยู่ชั้นไหน', 'facilities'; ถ้าถามตำแหน่ง "
                    "ต้องคงคำว่าอยู่ตรงไหน/อยู่ชั้นไหน ห้ามส่งเพียงชื่อสถานที่"
                ),
            }
        },
        "required": ["query"],
    },
    # Tagged as a slide tool too: it changes what's on screen, so the
    # registry must push the update out to the displays.
    tags=["knowledge", "slides"],
    blocking=True,
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
            # Points at the approved facts block instead of "say there is no
            # data": that wording won over the facts every time — live eval
            # 2026-10-07, 0 of 30 price questions answered while condo_facts
            # carried approved starting prices, and "ไม่มีข้อมูลทำเล" with the
            # location in the prompt. The slides are still never the source.
            "instruction": (
                "คลังภาพไม่ใช่แหล่งราคา ค่าใช้จ่าย หรือเงื่อนไข ห้ามเดาหรืออ้างอิงจากคำบรรยายภาพ "
                "ถ้า 'ข้อมูลโครงการ' ใน system prompt มีคำตอบ (ราคาเริ่มต้น ค่าใช้จ่าย โปรโมชั่น "
                "เวลาทำการ ทำเล) ให้ตอบเฉพาะที่เขียนไว้ตรงๆ ตามกฎราคา ห้ามสรุปนโยบายหรือเงื่อนไขที่ไม่ได้เขียน "
                "(เช่น การันตีค่าเช่า ราคาตามวิวหรือชั้น) ถ้าไม่มีห้ามพูดว่าไม่มีข้อมูลหรือไม่ทราบ ให้ส่งต่อทีมขายอย่างเป็นธรรมชาติ"
            ),
        }

    from app.tools import slides as slides_mod

    # An undisclosed slide must not influence BM25, embeddings or the optional
    # reranker for a customer answer. Image-only search has a separate index.
    ranked = slides_mod.search_disclosed_slide_text(query)
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
        hits = [h.slide for h in ranked if h.found][:_result_limit(query)]

    rejected = [customer_slide_trace(evaluate_claim(slide, settings.project_id),
                                     settings.project_id,
                                     valid_source=slide.get("source_id") == "slide_catalog")
                for slide in slides if slide.get("source_id") != "slide_catalog"
                or not evaluate_claim(slide, settings.project_id).allowed][:1]
    hits = [slide for slide in hits if slide.get("source_id") == "slide_catalog"
            and evaluate_claim(slide, settings.project_id).allowed]
    for trace in rejected:
        logger.info("knowledge policy %s", trace)

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
            "policy_trace": rejected,
            # Spelled out so the model doesn't fill the silence with a guess.
            # "Not in the slides" is not "nobody knows": live eval 2026-10-07
            # answered "ชั้น 3 มีอะไรบ้าง" and "โครงการอยู่ตรงไหน" with "no data"
            # while the facts block in the prompt listed both.
            # "...ตอบจากนั้น" alone was read as licence to extend: the re-test
            # turned the facts' "Smart Security" (in-unit, concept) into
            # "ระบบรักษาความปลอดภัยแบบอัจฉริยะ ครอบคลุมทั้งโครงการ".
            "instruction": ("ไม่พบในคลังภาพ ถ้า 'ข้อมูลโครงการ' ใน system prompt ตอบได้ "
                            "ให้ตอบเฉพาะที่เขียนไว้ตรงๆ ห้ามขยายความ "
                            "ถ้าไม่มีห้ามพูดว่าไม่มีข้อมูลหรือไม่ทราบ ให้ส่งต่อทีมขายอย่างเป็นธรรมชาติ ห้ามเดา"),
        }

    logger.info("knowledge lookup %r -> %d hit(s)", query, len(hits))

    result = {
        "ok": True,
        "found": True,
        "results": [_entry(s) for s in hits],
        "note": "ข้อมูลนี้เป็นคำบรรยายภาพ ห้ามใช้อ้างอิงราคาหรือโปรโมชั่น",
        "instruction": (
            "ตอบเฉพาะข้อมูลที่ตรงคำถามจากผลลัพธ์นี้ ห้ามนำข้อมูลข้างเคียงมาต่อเติม"
        ),
    }
    # Once per distinct instruction: four hits of one concept deck used to
    # repeat the same sentence four times.
    for caution in dict.fromkeys(item["content_state_instruction"] for item in result["results"]
                                 if item.get("content_state_instruction")):
        result["instruction"] += " " + caution
    if len(hits) > 1:
        # Owner 2026-10-10: a broad "what is there" gets the sections, then a
        # question, never the whole list. Said here as well as in the prompt
        # because an order riding on a tool result is the one the model
        # follows (CLAUDE.md) — "ตอบเฉพาะข้อมูลที่ตรงคำถาม" above won the first
        # run and S13 recited the ground floor.
        result["instruction"] += (
            " ถ้าลูกค้าถามกว้างว่ามีสิ่งอำนวยความสะดวกอะไรบ้าง (ไม่ได้ถามสิ่งหรือชั้นเฉพาะ) ห้ามไล่ผลทั้งหมด "
            "ให้ตอบประโยคเดียวที่ชวนเลือกชั้นตามข้อ 'สิ่งอำนวยความสะดวก' แล้วรอลูกค้าเลือก")

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
        return _no_picture(result)

    # No slides on the screen while the machine doesn't load the slides
    # group. The owner took the presentation off the gallery twice (11 Sep
    # "เอาพรีเซ้นออกก่อน", 8 Oct "เอาสไลด์ออกก่อน"), and the prompt already
    # says so (without_slide_rules) — but this tool is in the knowledge
    # group and kept putting slide images up as a side effect of answering.
    # Nobody noticed because from 6 Oct to 7 Oct the asset manifest was stale
    # and every image was refused; re-pinning it would have brought the
    # slides back onto the screen with the switch still off. The answer
    # text is unaffected: it comes from the approved catalogue either way.
    # ANSWER_IMAGES (2026-10-11) is the owner asking for the pictures again
    # without the presentation: "ทุกเรื่องที่มีภาพตรง".
    if not _slides_on_screen():
        turnlog.record(
            "lookup", query=query, showed=None, best=hits[0].get("id"),
            best_title=hits[0].get("title_th"), reason="slides group not loaded, ANSWER_IMAGES off",
        )
        return result

    turnlog.record(
        "lookup", query=query, showed=hits[0].get("id"),
        title=hits[0].get("title_th"),
        runners_up=[h.get("id") for h in hits[1:4]],
    )
    if settings.multi_session:
        return _no_picture(result)
    from app.tool_io import on_loop

    shown = _sanitize_now_showing(on_loop(slides_mod.show_current, hits[0]))
    if not shown or not shown.get("url"):
        return _no_picture(result)
    result["now_showing"] = shown
    resume = on_loop(slides_mod.resume_hint)
    if resume:
        result["presentation"] = resume
    result["note"] = "แสดงภาพที่ตรงกับคำตอบบนจอแล้ว " + result["note"]
    return result
