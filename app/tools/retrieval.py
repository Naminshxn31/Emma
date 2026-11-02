"""
Hybrid retrieval over the slide library: BM25 for words, embeddings for
meaning, fused by rank.

Why this replaced a hand-written scorer
---------------------------------------
The previous version compared overlapping character n-grams. Thai has no
spaces, so n-grams were a way to avoid needing a tokeniser — but they compare
*letters*, not words, and unrelated Thai words share letters constantly. The
failure that forced the rewrite: **ราคา** (price) and **อาคาร** (building)
both contain "าคา", so a guest asking the price got a picture of a building.
Patching that with rarity weighting, longer n-grams and a hand-written list
of banned words made it better, but each fix was a workaround for using the
wrong comparison in the first place. A real tokeniser splits ราคาโครงการ into
["ราคา", "โครงการ"] and the collision simply doesn't exist.

The n-gram path is still here, as the fallback when pythainlp isn't
installed. It is worse; it is not broken.

The half that was missing entirely
----------------------------------
Lexical search can only match words that are literally present. Two things
that mattered here and no amount of BM25 tuning would fix:

- **Synonyms.** สระว่ายน้ำ, สระลากูน and SKY POOL are the same facility. The
  old code only connected them because I typed the missing word into the
  slide's keywords by hand — which does not scale and silently rots.
- **Other languages.** A Chinese guest asking 游泳池 shares no characters
  with any slide. Lexical search returns nothing, forever, unless a human
  writes Chinese keywords for all 144 slides.

Embeddings handle both without anyone maintaining a vocabulary.

Degrading
---------
Every layer is optional and the system answers without it:

    pythainlp missing  -> character n-grams instead of words
    no API key         -> BM25 only, no semantic matching
    no network / error -> BM25 only, logged once, conversation unaffected

The sales gallery keeps working; it just gets less clever.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import math
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("condo_voice.retrieval")

# Customer-facing knowledge has a narrower policy than the source library.
# Floor numbers remain useful ("Sky Pool is on floor 3"), but dimensions of
# pools, lagoons and common areas are deliberately not spoken. Strip those
# measurements before either static facts or retrieval results reach a model;
# a value absent from its context cannot leak through a weaker instruction.
_COMMON_AREA = re.compile(
    r"สระ|ลากูน|ส่วนกลาง|ฟิตเนส|ซาวน่า|สปา|สนาม|สวน|"
    r"pool|lagoon|common\s*area|facilit(?:y|ies)|gym|sauna|spa|garden",
    re.IGNORECASE,
)
_DIMENSION = re.compile(
    r"(?:(?:ความยาว|ยาว|ขนาด|พื้นที่)\s*)?"
    # `-?` and the bare `m\b`: the English copy of the 2026-10-06 deck writes
    # the lagoon as "155m Water Lagoon" (intro-015 title_en) and "a 155-metre
    # water lagoon" (its summary_en). Both walked past this pattern while the
    # Thai "ลากูน 155 เมตร" beside them was stripped, so an English-speaking
    # guest would have heard the one number a Thai guest never does. `m\b`
    # needs the word to end there: "5 min" and "2 million" are not metres.
    # A width x length takes its first number with it: "ขนาด 16.125 × 5.85
    # เมตร" (Bungee Gym, the report) was cut to "ขนาด 16.125 ×" (28 Oct).
    r"(?:\d[\d,]*(?:\.\d+)?\s*[×xX]\s*)?"
    r"\d[\d,]*(?:\.\d+)?\s*-?\s*"
    r"(?:ตาราง\s*เมตร|ตร\.?\s*ม\.?|เมตร|ม\.|sqm|sq\.?\s*m\.?|m(?:etre|eter)s?|m\b)",
    re.IGNORECASE,
)


#: The caveat the owner banned on 2026-10-10: "อย่าบอกว่าเป็นเพียงแนวคิดอาจจะ
#: มีการเปลี่ยนแปลง ห้ามพูดแบบนี้เป็นอันขาด". The prompt and `state_instruction`
#: ban saying it, but the approved deck itself prints it (cover, theme page,
#: basement plan: "มุมขวาล่างระบุว่าเป็นแนวคิดเบื้องต้น เพื่อการนำเสนอเท่านั้น และ
#: อาจเปลี่ยนแปลงได้โดยไม่ต้องแจ้งล่วงหน้า") and search hands those summaries back
#: as `detail` — a ban in one place and the sentence as data in another is
#: two orders for one turn. So it is cut from project text before any model
#: reads it, the same door the dimensions go through. The approved files are
#: not edited; that is the owner's console. Price wording is left alone:
#: "ราคาเปลี่ยนตามห้องที่เหลือ" is a fact a buyer needs, not the banned caveat.
#: 21 Oct: the project report says it its own ways -- "ภาพเรนเดอร์ของชั้นนี้
#: เป็นภาพแนวคิด ผู้นำเสนอยอมรับว่าส่วน co-working ยังไม่ได้ออกแบบ", "ภาพเรนเดอร์ที่
#: อาจปรับเปลี่ยนได้", "Images are concept illustrations" -- and a tapped
#: picture of level 3 came back as "the co-working space is still not
#: designed". Narrow on purpose: "concept images" inside a fact's own
#: parenthesis ("shown as a double-height hall in the concept images") is not
#: the caveat, and an English sentence that matches goes whole.
#: 22 Oct (review): "The preliminary-concept disclaimer sits bottom right."
#: (intro-004.summary_en) slipped past "preliminary\s+concept" on its hyphen.
#: 26 Oct (robot review): 114 served report lines still carried the report's
#: own wording -- every picture caption "ภาพประกอบแนวคิด (Preliminary Concept)
#: ไม่ใช่แบบสุดท้าย — ..." and a row telling the reader to "แจ้งลูกค้าทุกครั้งที่
#: พูดถึงหน้าตา facility" -- the owner's rule the other way round.
#: 26 Oct (pre-commit review): the report says it in still more ways, and
#: the patterns above caught none of them -- built the way the robot serves
#: it (clean() over every approved chapter, the FAQ pairs, the English
#: aliases), 75 served report lines still carried it -- 15 of them FAQ
#: answers, which 15 English phrasings bring back too. Live, "How many
#: treadmills does Biogenesis have?" came back "...เป็นภาพเรนเดอร์ จึงควรบอก
#: ลูกค้าว่ารายละเอียดจริงอาจ
#: เปลี่ยนก่อนส่งมอบ" -- an approved answer telling Emma to say what the owner
#: banned. The forms: plain "อาจเปลี่ยน" (no แปลง) / "อาจต่าง" / "จะต่างจากภาพ"
#: after รายละเอียด/หน้าตา/ของ/จำนวน "จริง" (the real thing will differ),
#: "...อาจเปลี่ยนก่อนส่งมอบ", "การจัดวาง...อาจเปลี่ยน", the landscape's
#: "ชนิดไม้ วัสดุ และตำแหน่งอาจเปลี่ยน/อาจปรับ", "ยังพัฒนาต่อได้", "เป็นแบบแนวคิด",
#: "เป็นภาพ concept", "ไม่ใช่ภาพสุดท้าย", "ภาพยังไม่ final", "ไม่ใช่ภาพจริง", the
#: order to tell the customer every time ("บอก/แจ้งลูกค้า(ว่า)ทุกครั้ง/เสมอ",
#: "แจ้งทุกครั้งที่พูดถึงหน้าตา", "แจ้งลูกค้าว่าเป็นภาพประกอบ", "บอกว่าภาพคือแนวคิด
#: เสมอ") and the presenter's English inside Thai lines ("It will look a
#: little bit different", "All this will be changed, still not designed",
#: "This is not the real picture", the deck's whole "SUBJECT TO FURTHER
#: DEVELOPMENT AND CHANGE WITHOUT PRIOR NOTICE"). Scoped so the facts that
#: use the same verbs stay -- measured over every served line: "วิวสวน...
#: อาจเปลี่ยนหากที่ดินรอบโครงการถูกพัฒนา" (a view fact), "ตลาด ดอกเบี้ย และ
#: กฎหมายอาจเปลี่ยน" (investment caution), "ขนาดตามสัญญาอาจปรับตามรังวัดจริง"
#: (contract size), clinic hours that "อาจเปลี่ยน", "ราคายังไม่ final" (price).
#: Two lead-ins go with the caveat they introduce, never alone: "(ภาพ)เป็นภาพ
#: เรนเดอร์" right before "รายละเอียดจริง.../จำนวน...อาจ", and "...ว่า(ส่วน)
#: co-working" right before the presenter's not-designed quote -- left behind
#: they read "the presenter admitted that the co-working part", which a
#: model finishes itself.
CONCEPT_CAVEAT = re.compile(
    r"แนวคิดเบื้องต้น|เป็นเพียงแนวคิด|ยังเป็นแนวคิด|เพื่อการนำเสนอเท่านั้น|อาจ(?:จะ)?มีการเปลี่ยนแปลง"
    r"|ภาพประกอบแนวคิด|ไม่ใช่(?:ของ|แบบ|ภาพ)สุดท้าย"
    r"|อาจ(?:จะ)?เปลี่ยนแปลง|preliminary[\s-]+concept|for\s+illustrative\s+purposes(?:\s+only\.?)?"
    r"|subject\s+to\s+(?:further\s+development\s+and\s+)?change(?:\s+without\s+prior\s+notice\.?)?"
    r"|may\s+change"
    r"|เป็นภาพแนวคิด|ยังไม่ได้ออกแบบ|อาจ(?:จะ)?ปรับเปลี่ยน"
    r"|(?:images?|pictures?|renders?|renderings?)\s+(?:are|is)\s+(?:only\s+)?concept"
    r"|(?:still\s+)?not\s+(?:yet\s+)?designed"
    # the real thing will/may differ or change (26 Oct)
    r"|(?:รายละเอียด|หน้าตา|ของ|จำนวน)จริง(?:จะ|อาจ)(?:จะ)?(?:แตก)?ต่าง"
    r"|(?:รายละเอียด|หน้าตา|ของ|จำนวน)จริง(?:จะ|อาจ)(?:จะ)?(?:มีการ)?(?:ปรับ)?เปลี่ยน"
    r"|(?:จะ|อาจ)(?:จะ)?(?:แตก)?ต่างจากภาพ"
    # Narrowed after the fix round's own review (26 Oct): each of these was a
    # bare "word ... อาจเปลี่ยน" and would have cut facts the owner adds later
    # ("ตำแหน่งที่จอดรถอาจเปลี่ยนตามการจัดสรรของนิติบุคคล ห้องละ 1 คัน" lost its
    # piece, "ทีมขายจะแจ้งลูกค้าทุกครั้งเมื่อมีโปรโมชั่นใหม่" went whole). They now
    # name the report's own subjects: the pictured details, the planting plan.
    r"|(?:รายละเอียด|หน้าตา|จำนวน|ภาพ)(?:จริง)?อาจ(?:จะ)?(?:มีการ)?เปลี่ยน\S*?ก่อนส่งมอบ"
    r"|การจัดวาง\S*?อาจ(?:จะ)?(?:ปรับ|เปลี่ยน)"
    r"|(?:ชนิดไม้\s+วัสดุ\s+และตำแหน่ง|ตำแหน่งและชนิด|ชนิดและตำแหน่งเป็นไปตามแบบแนวคิดและ|ชนิดไม้"
    r"|ชนิดจริงและตำแหน่งปลูก)อาจ(?:จะ)?(?:ปรับ|เปลี่ยน)"
    r"|แนวคิดที่ยังพัฒนาต่อได้|(?<!\S)ยังพัฒนาต่อได้(?!\S)|เป็นแบบแนวคิด|(?:เป็นภาพ|ภาพ(?:\s+facility)?\s*เป็น)\s*concept|ไม่ใช่ภาพจริง"
    r"|concept,?\s+not\s+(?:yet\s+)?final"
    r"|ภาพ\S*?\s?ยังไม่\s*(?:final\b|ไฟนอล)"
    # "...and does not promise the building will match the picture" (03:211),
    # "...not a promise the delivered room has everything pictured" (14:375)
    r"|ไม่ยืนยันว่า\S*ตามภาพ|ไม่ใช่การรับประกันว่า\S*ตามภาพ\S*"
    # the order to say it to every customer (26 Oct) -- only where what is to
    # be said is the picture caveat: a table cell of its own, "...ว่า(เป็น)ภาพ",
    # "...ที่พูดถึงหน้าตา", or the 18.6.4 heading
    r"|(?:บอก|แจ้ง)ลูกค้า(?:ทุกครั้ง|เสมอ)(?=\s*(?:\||$|ว่า\s*[\"“]?(?:เป็น)?ภาพ|ที่(?:พูดถึง|บรรยาย)))"
    r"|ภาพ\s*concept\s*ต้องแจ้งลูกค้าเสมอ|(?:บอก|แจ้ง)ทุกครั้งที่(?:พูดถึง|บรรยาย)หน้าตา"
    r"|(?:บอก|แจ้ง)ลูกค้าว่าเป็นภาพ(?:ประกอบ|แนวคิด|เรนเดอร์)|บอกว่าภาพ\S*แนวคิด"
    r"|(?:it|this|that)\s+(?:will|may|might|would)\s+look\s+a\s+little\s+(?:bit\s+)?different"
    r"|all\s+this\s+will\s+be\s+changed"
    r"|not\s+the\s+real\s+picture"
    # the lead-ins, only right before the caveat they introduce (26 Oct)
    r"|(?:ภาพ\s+facility\s+\S*?)?เป็นภาพเรนเดอร์\s+(?=\S*?(?:รายละเอียดจริง|จำนวน\S*?อาจ))"
    r"|\(ภาพ(?:แนวคิด|เรนเดอร์)\s+(?=อาจ)"
    # (anchored at a piece start: a bare leading \S* is retried at every
    # character and made the whole report's cut six times slower)
    r"|(?<!\S)\S*?(?:ต้อง|ควร)(?:พูด|บอก)ว่า(?=\s+[\"“]?(?:ภาพประกอบแนวคิด|เป็นภาพแนวคิด"
    r"|\S*อาจ(?:จะ)?(?:มีการ)?(?:ปรับ)?เปลี่ยน))"
    r"|(?<!\S)\S*?ว่า\S*(?:\s+co-working)?(?=\s+[\"“(]?(?:ยังไม่ได้ออกแบบ|all\s+this|(?:still\s+)?not\s+designed"
    r"|it'?s\s+not\s+look|it\s+will\s+look))",
    re.IGNORECASE,
)
_PRICE_WORD = re.compile(r"ราคา|price|฿|baht", re.IGNORECASE)
#: An ENGLISH quotation inside a Thai line -- the deck's printed disclaimer,
#: the presenter's words ('"It will look a little bit different"', '"This is
#: not the real picture. Everybody aware."') -- is an English sentence, and an
#: English sentence that carries the caveat goes whole; cut by piece it left
#: '"It will', 'This is', 'FINAL DESIGNS AND DETAILS ARE' behind (26 Oct). A
#: THAI quotation is cut by piece like any Thai text: the report's suggested
#: answers quote facts and the caveat together ('"ตามภาพไฟนอลมีจักรยาน 3 คัน
#: รายละเอียดจริงอาจต่างได้)"', '"กลางโครงการมีสระหลักยาว 155 เมตร ... (ภาพแนวคิด
#: ยังไม่ final)"') -- measured: going whole, those took the bike count and the
#: lagoon's depth with them. The quote marks must sit like quote marks (an
#: opening one before a word, a closing one after a word): a stray one must
#: not pair with the next quote's opening and swallow the text between.
_QUOTED = re.compile(r'(?<![^\s(\[|:/])"(?=\S)[^"\n]{1,240}?(?<=\S)"(?![^\s)\]|,.;:/])|“[^”\n]{1,240}”')
_PAREN = re.compile(r"\s*\([^()\n]{1,120}\)")


def _sentences(value: str) -> list[str]:
    """Split at a full stop + space, never inside a quotation: the deck's
    "...FOR ILLUSTRATIVE PURPOSES ONLY. SUBJECT TO FURTHER DEVELOPMENT..."
    and "This is not the real picture. Everybody aware." are one quote each."""
    quotes = [m.span() for m in _QUOTED.finditer(value)]
    out, start = [], 0
    for m in re.finditer(r"(?<=[.!?])\s+", value):
        if any(a < m.start() < b for a, b in quotes):
            continue
        out.append(value[start:m.start()])
        start = m.end()
    out.append(value[start:])
    return out


def strip_concept_caveat(text: str | None) -> str:
    """Drop the clauses that carry the banned concept caveat.

    Thai separates clauses with spaces and English separates sentences
    with full stops, so an English sentence that carries it goes whole (an
    English parenthesis or quotation that carries it goes alone) and a Thai
    text loses only the space-delimited pieces that do. A piece about price
    stays (see CONCEPT_CAVEAT)."""
    value = str(text or "")
    if not value or not CONCEPT_CAVEAT.search(value):
        return value
    kept = []
    for sentence in _sentences(value):
        thai = bool(_THAI.search(sentence))
        # Thai has no full stops: a whole report line is one "sentence", and a
        # price word anywhere in it used to keep the caveat beside it (19_faq:
        # "เป็นภาพประกอบแนวคิด ... อาจเปลี่ยนแปลงได้ ..."). In Thai the price
        # PIECE stays, the caveat pieces still go.
        if not CONCEPT_CAVEAT.search(sentence) or (_PRICE_WORD.search(sentence) and not thai):
            kept.append(sentence)
            continue
        if not thai:
            # ...unless the caveat is only a parenthesis in it: "the third-floor
            # Sky Pool is designed with a transparent floor (concept, not
            # final)." keeps the floor and loses the parenthesis (26 Oct) --
            # gone whole, the gym's 6 m ceiling in the same sentence went too.
            trimmed = _PAREN.sub(lambda m: "" if CONCEPT_CAVEAT.search(m.group()) else m.group(), sentence)
            if trimmed != sentence and not CONCEPT_CAVEAT.search(trimmed):
                kept.append(trimmed.strip())
            continue
        # A piece goes when the caveat touches it -- matched across pieces too:
        # "(Preliminary Concept)" inside a Thai caption is two pieces, neither
        # of which matched alone, so it was served (robot review 26 Oct).
        spans = [m.span() for m in CONCEPT_CAVEAT.finditer(sentence)]
        # An English quotation that carries it goes whole (see _QUOTED) --
        # unless it is about price, which stays like an English price sentence.
        spans += [q.span() for q in _QUOTED.finditer(sentence)
                  if not _THAI.search(q.group()) and CONCEPT_CAVEAT.search(q.group())
                  and not _PRICE_WORD.search(q.group())]
        pieces, dropped = [], False
        for p in re.finditer(r"\S+", sentence):
            piece = p.group()
            if _PRICE_WORD.search(piece) or not any(a < p.end() and p.start() < b for a, b in spans):
                # "...เป็นภาพเรนเดอร์หรือภาพประกอบแนวคิด จึงไม่ใช่การรับประกัน และ..." (19_faq:1067):
                # the conclusion of a caveat just cut is the caveat too. On its
                # own ("ความเห็นของทีมขาย ไม่ใช่การรับประกัน") it is an
                # investment caution and stays.
                if dropped and _CAVEAT_CONCLUSION.fullmatch(piece):
                    continue
                pieces.append(piece)
                dropped = False
            else:
                dropped = True
        if pieces:
            kept.append(" ".join(pieces))
    return " ".join(kept).strip()


_CAVEAT_CONCLUSION = re.compile(r"(?:จึง|และ)?ไม่ใช่การรับประกัน[.,]?")


#: What the owner overruled (12 Oct 2026, the project report's deny_terms):
#: the rooftop garden & jacuzzi, and the old plan's Thermal Galaxy. The
#: approved slide catalog still describes the deck's "A WORLD OF INSPIRATION"
#: vignette as "ลานพักผ่อนบนดาดฟ้าตอนเย็น"; search handed that back as
#: `detail`, and with stories on (review 12 Oct) "บนดาดฟ้ามีอะไร" came back as
#: "บนดาดฟ้ามีสวนลอยฟ้า...". "ดาดฟ้า" alone is not overruled -- the roof
#: deck's height is an approved figure -- only a garden, terrace or jacuzzi
#: ON it.
#: 15 Oct (owner: "ลบ Glowing jellyfish-pattern floor ออก" -- the design
#: dropped it): four approved slide summaries still describe jellyfish on the
#: Journey to Mars / underground pool floor.
#: 22 Oct (owner: the Social Lounge has humanoid robots making and serving
#: drinks): the approved Social Club slide (intro-040) still notes "Robots
#: could be or could not be humanoid" / "ไม่เป็นรูปทรงมนุษย์" -- the report's
#: lines saying it go by deny_terms, this cuts the slide's clause.
OVERRULED = re.compile(
    r"roof\s*-?\s*top|รูฟ\s*ท็อป|(?:สวน|ลาน|จากุซซี่)\S*ดาดฟ้า|thermal\s*galaxy|jelly\s*-?\s*fish|แมงกะพรุน"
    r"|could\s+be\s+or\s+could\s+not\s+be\s+humanoid|ไม่เป็นรูปทรงมนุษย์",
    re.IGNORECASE,
)

#: The U-Tapao "40–60 minutes" map estimate the owner overruled (the approved
#: figure is the deck's). The report's deny list cuts it there; the approved
#: slide summary of intro-008 still says it -- cut the phrase, keep the rest.
_UTAPAO_RANGE = re.compile(
    r"(?:อู่ตะเภา|U-?Tapao)\S*?\s*(?:ประมาณ|about|approx\.?)?\s*40\s*[–-]\s*60\s*"
    r"(?:นาที|min(?:utes|s)?\b)\.?,?\s*",
    re.IGNORECASE,
)


def strip_overruled(text: str | None) -> str:
    """Drop the clauses that describe something the owner overruled: a whole
    English sentence, or the space-delimited Thai piece (with the "(CAPTION)"
    right after it, which would be left naming nothing)."""
    value = str(text or "")
    if not value or not OVERRULED.search(value):
        return value
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", value):
        if not OVERRULED.search(sentence):
            kept.append(sentence)
            continue
        if not _THAI.search(sentence):
            continue
        pieces, out, drop_caption = sentence.split(), [], False
        for piece in pieces:
            if OVERRULED.search(piece):
                drop_caption = True
                continue
            if drop_caption and piece.startswith("(") and piece.endswith(")"):
                drop_caption = False
                continue
            drop_caption = False
            out.append(piece)
        if out:
            kept.append(" ".join(out))
    return " ".join(kept).strip()


def sanitize_common_area_dimensions(text: str | None) -> str:
    """Hide prohibited common-area measurements while preserving locations.

    Also the one door every piece of project text goes through on its way to
    a model (facts, search results, slides), so the banned concept caveat is
    cut here too (`strip_concept_caveat`), and what the owner overruled
    (`strip_overruled`)."""
    value = strip_overruled(strip_concept_caveat(text))
    if value and _UTAPAO_RANGE.search(value):
        value = _UTAPAO_RANGE.sub("", value).strip()
    if not value or not _COMMON_AREA.search(value):
        return value
    value = _DIMENSION.sub("", value)
    value = re.sub(r"\s+([,.;:])", r"\1", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return value.strip(" ,;")

# ============================ tokenisation ============================

_THAI = re.compile(r"[฀-๿]")

#: Han and kana. Written without spaces, and there's no segmenter here for
#: them, so they get character bigrams — the standard fallback in CJK
#: retrieval and enough for BM25 to have an opinion. Hangul is deliberately
#: not in this set: Korean *does* put spaces between words.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def _chunks(text: str) -> list[str]:
    """Split into word-ish runs, in any writing system.

    Not a regex, because the obvious ones are both wrong. `[A-Za-z0-9]+|[฀-๿]+`
    silently dropped every other script on earth — "где бассейн" and 游泳池
    tokenised to nothing, so the lexical half of the search was dead for
    those guests. Replacing it with `\\w+` broke Thai and Hindi instead:
    Unicode combining marks (the vowels and tone marks in ส-ร-ะ-ว-่-า-ย) are
    category Mn, which `\\w` does not match, so it split inside words.

    Keeping letters, digits and marks, and treating everything else as a
    separator, is correct for all of them.
    """
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if unicodedata.category(ch)[0] in "LMN":
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out

_tokeniser: Any = None
_tokeniser_ready = False
#: +1 per (re)build: what a cache of tokenized text keys on (stories'
#: question index, audit 27 Oct) -- an id() can come back for a later object
_tokeniser_gen = 0


def _build_tokeniser(vocabulary: list[str]) -> Any:
    """pythainlp's newmm segmenter, taught the words in this deck.

    The stock dictionary is general Thai, so the project's own vocabulary —
    ซาวน่า, ลากูน, สกายพูล, ฟิตเนส — gets shredded into meaningless pieces
    ("ซาวน่า" -> ซาว + น่า). Those words are already written down in the
    slide keywords, so the deck teaches the tokeniser its own terms and stays
    correct as slides are added.
    """
    from pythainlp.corpus.common import thai_words
    from pythainlp.util import dict_trie

    words = set(thai_words())
    for phrase in vocabulary:
        phrase = phrase.strip()
        # Multi-word English keywords ("lagoon pool") are handled by the
        # Latin path; only Thai runs need teaching.
        if phrase and _THAI.search(phrase) and len(phrase) > 1:
            words.add(phrase)
    return dict_trie(dict_source=words)


def init_tokeniser(vocabulary: list[str] | None = None) -> bool:
    """Returns True if real word segmentation is available."""
    global _tokeniser, _tokeniser_ready, _tokeniser_gen
    _tokeniser_ready = True
    _tokeniser_gen += 1
    try:
        _tokeniser = _build_tokeniser(vocabulary or [])
        logger.info("Thai word segmentation enabled (pythainlp)")
        return True
    except Exception:
        _tokeniser = None
        logger.warning(
            "pythainlp unavailable — falling back to character n-grams, which "
            "confuse Thai words that share letters. `pip install pythainlp` "
            "to fix.", exc_info=True,
        )
        return False


def _ngrams(text: str) -> list[str]:
    """Fallback when there's no segmenter. Same shape as the old scorer."""
    out: list[str] = []
    for size in (3, 4, 5):
        if len(text) <= size:
            out.append(text)
        else:
            out.extend(text[i:i + size] for i in range(len(text) - size + 1))
    return out


#: Words that carry no topic. Needed because guests speak in questions:
#: "มีห้องฟิตเนสไหมคะ" is one topic word wrapped in five particles, and a
#: slide deck contains no particles at all — so every one of them counted as
#: "something the guest asked for that isn't here", and a perfectly good
#: match scored as a miss. Only ever applied when *judging* a match; the
#: index itself keeps every word.
_EXTRA_STOPWORDS = {
    # Thai question frames and politeness, beyond pythainlp's list.
    "มี", "ไหม", "มั้ย", "หรือ", "คะ", "ค่ะ", "ครับ", "อยาก", "ขอ", "ดู",
    "อยู่", "ที่ไหน", "ไหน", "อะไร", "ยัง", "เห็น", "ช่วย", "หน่อย", "บ้าง",
    "ได้", "เป็น", "ใคร", "ทำไม", "เท่าไหร่", "กี่", "นะ", "หรอ", "เหรอ",
    # English equivalents.
    "do", "does", "did", "you", "have", "has", "is", "are", "the", "a", "an",
    "any", "what", "where", "which", "who", "how", "there", "i", "we", "can",
    "could", "would", "please", "show", "me", "tell", "about", "of", "to",
    "and", "or", "in", "on", "at", "for", "with", "your", "it", "this",
}

_stopwords: set[str] | None = None


def stopwords() -> set[str]:
    global _stopwords
    if _stopwords is None:
        words = set(_EXTRA_STOPWORDS)
        try:
            from pythainlp.corpus import thai_stopwords

            words |= set(thai_stopwords())
        except Exception:
            pass
        _stopwords = words
    return _stopwords


def content_tokens(tokens: list[str]) -> list[str]:
    """Drop the words that don't name a topic.

    Falls back to the original list if *everything* was a stopword, so a
    query like "ที่ไหน" still ranks against something rather than nothing.
    """
    kept = [t for t in tokens if t not in stopwords() and len(t) > 1]
    return kept or tokens


def robust_tokens(text: str) -> list[str]:
    """Words, plus character n-grams of the Thai runs as a safety net.

    Thai segmentation is ambiguous and newmm sometimes picks wrong:
    "ทำเลอยู่ตรงไหน" comes out as ทำ|เลอ|ยู่|ตรงไหน, because เลอ really is a
    word — and the topic (ทำเล) vanishes. n-grams don't care where the
    boundaries are, so they catch what the segmenter drops.

    Used for **ranking only**. Judging whether a match is real still goes by
    words alone, because n-grams are exactly what confused ราคา with อาคาร;
    here they can only ever add a candidate, never certify one.
    """
    tokens = tokenize(text)
    for chunk in _chunks(unicodedata.normalize("NFKC", text or "").lower()):
        if _THAI.search(chunk):
            tokens.extend(_ngrams(chunk))
    return tokens


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).lower()
    if not _tokeniser_ready:
        init_tokeniser()

    tokens: list[str] = []
    for chunk in _chunks(text):
        if _THAI.search(chunk):
            if _tokeniser is not None:
                from pythainlp.tokenize import word_tokenize

                tokens.extend(
                    w for w in word_tokenize(chunk, custom_dict=_tokeniser, engine="newmm")
                    if w.strip()
                )
            else:
                tokens.extend(_ngrams(chunk))
        elif _CJK.search(chunk):
            # No spaces and no segmenter: bigrams, plus the single characters,
            # since a lot of Chinese content words are one character.
            tokens.extend(chunk)
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            # Latin, Cyrillic, Greek, Arabic, Devanagari, digits — all of
            # these are already space-delimited by the time _CHUNK ran.
            tokens.append(chunk)
    return tokens


# ============================== BM25 ==============================

#: Okapi BM25 defaults. k1 controls how fast repeated terms stop helping;
#: b how hard long documents are penalised.
BM25_K1 = 1.5
BM25_B = 0.75


class BM25:
    """Okapi BM25 over pre-tokenised documents.

    Implemented here rather than pulled in as a dependency: it is thirty
    lines, this deployment runs on a robot with no package manager attached,
    and the formula is fixed.
    """

    def __init__(self, documents: list[list[str]]) -> None:
        self.docs = documents
        self.n = len(documents)
        self.lengths = [len(d) for d in documents]
        self.avg_length = (sum(self.lengths) / self.n) if self.n else 0.0

        self.freqs: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc in documents:
            counts: dict[str, int] = {}
            for token in doc:
                counts[token] = counts.get(token, 0) + 1
            self.freqs.append(counts)
            for token in counts:
                df[token] = df.get(token, 0) + 1

        # Lucene's variant: always positive, so a term in every document
        # contributes ~0 instead of going negative and actively penalising.
        self.idf = {
            t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = [0.0] * self.n
        if not self.n:
            return out
        for token in set(query_tokens):
            idf = self.idf.get(token)
            if idf is None:
                continue          # not in the corpus: nothing to score against
            for i, counts in enumerate(self.freqs):
                f = counts.get(token, 0)
                if not f:
                    continue
                norm = 1 - BM25_B + BM25_B * (self.lengths[i] / (self.avg_length or 1))
                out[i] += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * norm)
        return out

    def coverage(self, query_tokens: list[str], index: int) -> float:
        """Share of the query's IDF mass this document actually contains.

        Ranking says which document is closest; this says whether "closest"
        means anything. Used to decide no-match, where BM25's own score is
        unusable because its scale moves with query length.
        """
        wanted = set(content_tokens(query_tokens))
        if not wanted:
            return 0.0
        # An unseen term is maximally informative: the guest asked for
        # something this deck never mentions.
        max_idf = math.log(1 + (self.n + 0.5) / 0.5) if self.n else 1.0
        total = sum(self.idf.get(t, max_idf) for t in wanted)
        if total <= 0:
            return 0.0
        counts = self.freqs[index]
        found = sum(self.idf[t] for t in wanted if t in counts and t in self.idf)
        return found / total


# ========================= semantic index =========================

EMBED_MODEL = "gemini-embedding-001"
#: Matryoshka truncation. 144 slides do not need 3072 dimensions, and the
#: smaller vector is faster to compare and cheaper to cache. Anything below
#: 3072 comes back un-normalised and MUST be normalised before use.
EMBED_DIM = 768
_EMBED_BATCH = 32


_numpy_checked = False
_numpy_ok = False


def numpy_available() -> bool:
    """Checked once, up front, rather than discovered mid-request.

    Semantic search is meant to be optional — every other missing piece
    degrades quietly. numpy didn't, because one `import numpy` sat outside
    the try block that was supposed to catch exactly this. The exception
    escaped through the search all the way into the tool result, so the
    robot told a guest "No module named 'numpy'" instead of showing a slide.
    """
    global _numpy_checked, _numpy_ok
    if not _numpy_checked:
        _numpy_checked = True
        try:
            import numpy  # noqa: F401

            _numpy_ok = True
        except Exception:
            _numpy_ok = False
            logger.warning(
                "numpy is not installed — semantic search is off, so synonyms "
                "and cross-language questions won't match. Lexical search "
                "still works. Fix with: pip install numpy"
            )
    return _numpy_ok


def _l2_normalise(vectors):
    import numpy as np

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


#: Local, offline embeddings. Small enough to ship on the gallery PC and
#: multilingual, which is the whole reason semantic search exists here — a
#: Chinese or Russian question has to find a Thai slide.
#:
#: Configurable (EMBED_LOCAL_MODEL) because the genuine/garbage gap this
#: index produces is measured, and the honest way to try a stronger model
#: (BGE-M3) is to change one line, rebuild, and run eval_search.py again.
#: The model name is baked into `embedding_signature`, so a change forces a
#: rebuild instead of quietly mixing spaces.
LOCAL_MODEL = os.getenv(
    "EMBED_LOCAL_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
#: Which engine runs LOCAL_MODEL: `auto` (sentence-transformers where it is
#: installed -- the PC -- else the ONNX export, app/embed_onnx.py -- the
#: robot), `sentence-transformers`, or `onnx`. Not a different space: the two
#: are measured equal (CLAUDE.md), which is why the signature does not name the
#: engine. `onnx` on the PC is how that measurement is re-run
#: (`EMBED_LOCAL_BACKEND=onnx python scripts/eval_search.py`). A misspelling
#: is `auto`, never an error.
LOCAL_BACKEND = os.getenv("EMBED_LOCAL_BACKEND", "auto")
_local = None
#: Latched once no engine could load, so the reason is logged once and every
#: later query fails fast to lexical -- what slide_search's own latch expects.
_local_unavailable = False
_local_lock = threading.Lock()


def _onnx_serves_local_model() -> bool:
    """The ONNX files ARE one model. Serving them for another EMBED_LOCAL_MODEL
    would put MiniLM vectors under that model's signature."""
    from app import embed_onnx

    return LOCAL_MODEL == embed_onnx.MODEL


def local_embeddings_installed() -> bool:
    """Is the offline backend *available*, without paying to find out.

    `find_spec` locates the package without executing it. That distinction is
    the whole point: importing `sentence_transformers` pulls in `transformers`,
    which walks its own `models/` directory opening every file to build an
    import map, and on Windows that took long enough to blow a twenty-second
    test timeout. The stack looked like a hang. It was a library loading.

    `choose_provider()` only ever wanted to know whether the option exists —
    and it asks that question at *startup*, to print one line in the banner. On
    a machine with the package installed and no API key, answering it by
    loading a whole transformer model is a cost paid by everyone, forever, for
    a log message.

    Actually loading the model stays in `_local_encoder`, where it is paid by
    whoever genuinely embeds something.

    The ONNX engine (the robot's) counts when its packages and files are
    there -- still `find_spec` and `is_file`, nothing opened or hashed.
    """
    import importlib.util

    if importlib.util.find_spec("sentence_transformers") is not None:
        return True
    from app import embed_onnx

    return (_onnx_serves_local_model() and embed_onnx.files_present()
            and importlib.util.find_spec("onnxruntime") is not None
            and importlib.util.find_spec("tokenizers") is not None)


def _st_encoder():
    """sentence-transformers (torch), or None if it is not installed."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    try:
        logger.info("loading local embedding model %s (first run downloads it)", LOCAL_MODEL)
        return SentenceTransformer(LOCAL_MODEL)
    except Exception:
        logger.warning("could not load the local embedding model", exc_info=True)
        return None


def _onnx_encoder():
    """The ONNX export of LOCAL_MODEL (app/embed_onnx.py), or None."""
    from app import embed_onnx

    if not _onnx_serves_local_model():
        logger.info("the ONNX embedding files are %s, not EMBED_LOCAL_MODEL=%s -- not used",
                    embed_onnx.MODEL, LOCAL_MODEL)
        return None
    return embed_onnx.load()


def _local_encoder():
    """Load the offline model once, or None if no engine can.

    Once, under a lock: the boot warm-up (main._warm_search) and a guest's
    first question both arrive here in worker threads, and two concurrent
    loads of a 470 MB model is twice the memory and twice the wait for
    nothing. Never on the event loop -- every caller is a blocking tool or
    the warm-up's `to_thread`.

    `auto` tries sentence-transformers first: where it is installed it is
    what built the index, and the PC's behaviour must not change with this.
    Where it is not (the robot), the ONNX export of the same model -- the
    same vectors, measured (app/embed_onnx.py). Neither = None, logged once;
    `_embed_local` raises and search carries on lexical, exactly as before
    the ONNX path existed.
    """
    global _local, _local_unavailable
    if _local is not None or _local_unavailable:
        return _local
    with _local_lock:
        if _local is not None or _local_unavailable:
            return _local
        backend = (LOCAL_BACKEND or "auto").strip().lower()
        if backend in {"st", "sentence-transformers", "sentence_transformers", "torch"}:
            engines = (_st_encoder,)
        elif backend == "onnx":
            engines = (_onnx_encoder,)
        else:
            if backend != "auto":
                logger.warning("EMBED_LOCAL_BACKEND=%r is not auto/onnx/sentence-transformers "
                               "-- using auto", LOCAL_BACKEND)
            engines = (_st_encoder, _onnx_encoder)
        for engine in engines:
            model = engine()
            if model is not None:
                _local = model
                return model
        _local_unavailable = True
        logger.info(
            "local embeddings unavailable — no sentence-transformers and no ONNX "
            "model (python scripts/fetch_embed_model.py); search stays lexical"
        )
        return None


#: tests/conftest.py replaces `_local_encoder` for every test (the back door
#: in CLAUDE.md) and never puts it back; tests of the loader itself call it
#: through this name.
_local_encoder_impl = _local_encoder


def embedding_signature(provider: str) -> str:
    """Which embedding space a set of vectors lives in.

    Folded into the cache fingerprint, and that is not bookkeeping — it is the
    one thing that stops this feature producing confident nonsense.

    Vectors from two different models are not comparable. Cosine similarity
    between a Gemini document vector and a MiniLM query vector does not error;
    it returns a number, and that number ranks slides at random. Build the
    index on the API, lose the API, fall back to local for queries, and search
    quietly starts answering "ฟิตเนส" with the executive team photo — with no
    warning anywhere, because nothing failed.

    So the provider is part of the identity of the cache. Change it and the
    index is stale by definition.

    The local ENGINE is deliberately not in it: sentence-transformers (PC)
    and the ONNX export (robot, app/embed_onnx.py) were measured on 180
    texts -- the 48 indexed slides, the 126 eval questions, Thai/English/
    Chinese probes -- at min cosine 0.9999998 (2026-10-09); the ONNX slide
    vectors against the PC-built embeddings.npz the same, and eval_search
    gave identical outcomes on all 126 questions. That is one space, so an
    index either machine builds is valid on both. The int8 export measured
    min 0.971 and is NOT used; an export that ever is needs its own space --
    and note that embeddings.npz records only the provider ("local"), not
    this string, so a new signature alone would not make an old index stale.
    """
    if provider == "local":
        return "local:%s" % LOCAL_MODEL
    return "gemini:%s:%d" % (EMBED_MODEL, EMBED_DIM)


def _looks_like_a_rate_limit(exc: Exception) -> bool:
    """A quota refusal, as opposed to a bad key or a broken request.

    Matched on text because the SDK raises a generic ClientError for both,
    and retrying a 401 forever would be worse than failing fast.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("429", "resource_exhausted", "rate limit", "quota", "exceeded")
    )


def _embed_local(texts: list[str]):
    """Offline embeddings, L2-normalised to match the API path."""
    import numpy as np

    model = _local_encoder()
    if model is None:
        raise RuntimeError("local embedding model is not available")
    vectors = model.encode(list(texts), convert_to_numpy=True, show_progress_bar=False)
    return _l2_normalise(np.asarray(vectors, dtype="float32"))


#: Query vectors already paid for, keyed by text. Off unless a caller turns it
#: on — see `use_query_cache`.
#:
#: Written for `scripts/eval_search.py`. Embedding 127 questions back to back
#: exceeded the free tier's 100 requests per minute *for the embedding model*,
#: and the run continued with semantic search silently switched off: every
#: question after the 429 was scored lexically, reported a similarity of
#: -1.00, and was quietly dropped from the calibration. The suggested
#: threshold that came out the other side was computed on a fraction of the
#: questions, and looked exactly as authoritative as a complete one.
#:
#: A cache fixes it properly rather than by sleeping: the same question asked
#: twice costs one request ever, so re-running the eval after a change is both
#: free and instant. Never used by the live robot, where each guest question is
#: genuinely new and staleness would be a bug.
_QUERY_CACHE: dict[str, Any] = {}
_query_cache_path: Path | None = None


def use_query_cache(path: Path | None) -> None:
    """Turn the query-embedding cache on, backed by `path`. None turns it off."""
    global _query_cache_path, _QUERY_CACHE

    _query_cache_path = path
    _QUERY_CACHE = {}
    if path is None or not path.exists():
        return
    try:
        import numpy as np

        with np.load(path, allow_pickle=False) as data:
            _QUERY_CACHE = {k: data[k] for k in data.files}
        logger.info("query cache: %d questions already embedded", len(_QUERY_CACHE))
    except Exception:
        logger.warning("could not read the query cache at %s — starting empty", path)


def save_query_cache() -> None:
    if _query_cache_path is None or not _QUERY_CACHE:
        return
    try:
        import numpy as np

        _query_cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(_query_cache_path, **_QUERY_CACHE)
    except Exception:
        logger.warning("could not save the query cache", exc_info=True)


def _cache_space(provider: str) -> str:
    """The query-cache key prefix. "local" stays what sentence-transformers
    wrote (106 eval questions cached since August); the ONNX engine gets its
    own, or `EMBED_LOCAL_BACKEND=onnx eval_search.py` would read the PC
    engine's cached vectors for 106 of 126 questions and report that it had
    measured ONNX. Equal spaces do not make a measurement of one engine a
    measurement of the other."""
    if provider != "local":
        return provider
    from app import embed_onnx

    return "local+onnx" if isinstance(_local_encoder(), embed_onnx.OnnxEncoder) else "local"


def embed(texts: list[str], task_type: str, provider: str, *, attempts: int = 1):
    """Embed with a named provider. Never mixes — see `embedding_signature`."""
    # One query, cache on: answer from disk if this exact question has been
    # embedded before. Only ever a single query — batches are index builds,
    # which have their own cache and must not be short-circuited per item.
    if _query_cache_path is not None and len(texts) == 1:
        import numpy as np

        key = "%s\x00%s\x00%s" % (_cache_space(provider), task_type, texts[0])
        hit = _QUERY_CACHE.get(key)
        if hit is not None:
            return np.asarray([hit])

    if provider == "local":
        vectors = _embed_local(texts)
    else:
        # attempts>1 when the cache is on: the eval asks 127 questions in a
        # row and the free tier allows 100 embeddings a minute, so without
        # backoff the run trips a 429 partway and finishes with semantic
        # search silently switched off — which is how a calibration got
        # computed on a fraction of its own question list and still printed a
        # confident number.
        vectors = _embed(texts, task_type,
                         attempts=max(attempts, 5 if _query_cache_path else 1))

    if _query_cache_path is not None and len(texts) == 1:
        key = "%s\x00%s\x00%s" % (_cache_space(provider), task_type, texts[0])
        _QUERY_CACHE[key] = vectors[0]
    return vectors


def choose_provider() -> str:
    """Which embedding backend to build with, from EMBED_PROVIDER.

    `auto` prefers the API — it is better, and this deck is small enough that
    building costs a handful of requests — but falls back to local rather than
    leaving search switched off. The decision is made once, for a whole index,
    and recorded in the cache.
    """
    from app.config import settings

    choice = (settings.embed_provider or "auto").strip().lower()
    if choice in {"gemini", "api"}:
        return "gemini"
    if choice == "local":
        return "local"
    if choice in {"off", "none", "false"}:
        return "off"
    # auto
    if settings.gemini_api_key:
        return "gemini"
    # `local_embeddings_installed`, not `_local_encoder`: this runs in the
    # startup banner, and loading the model here would make every boot pay for
    # a question that only needed to know whether the package exists.
    if local_embeddings_installed():
        logger.info("no GEMINI_API_KEY — using local embeddings")
        return "local"
    return "off"


def _embed(texts: list[str], task_type: str, *, attempts: int = 1):
    """One or more embeddings from the Gemini API, L2-normalised.

    `attempts` is the whole reason this has retry logic, and why the default
    is 1 rather than something generous.

    The free tier caps this model at 100 requests a minute. Building the
    index is 144 slides in batches of 32 — five requests — but every server
    start with no cached `embeddings.npz` does it again, and a day of
    restarts walked the peak to 96/100. Past that the call raises, the caller
    logs "continuing with lexical search only", **the file is never written**,
    and the next start begins from nothing. A loop that cannot finish while
    it is being throttled, which is why semantic search had never once run.
    (RPD was only 160/1000 — it is the per-minute limit that binds, and a
    per-minute limit is exactly what waiting fixes.)

    So the build path asks for patience. The *query* path must not: it runs
    inside a tool call with a 15-second ceiling, and a guest asking "มีฟิตเนส
    ไหม" would get silence and then a timeout error. Failing there costs one
    lexical-only answer, which is a far better trade.
    """
    import time

    import numpy as np
    from google import genai
    from google.genai import types

    from app.config import settings

    client = genai.Client(api_key=settings.gemini_api_key)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        batch = texts[start:start + _EMBED_BATCH]
        for attempt in range(1, attempts + 1):
            try:
                response = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type, output_dimensionality=EMBED_DIM
                    ),
                )
                break
            except Exception as exc:
                if attempt >= attempts or not _looks_like_a_rate_limit(exc):
                    raise
                # The cap is per *minute*, so the wait has to be able to reach
                # a minute. 5s, 10s, 20s, 40s, 60s, 60s...
                pause = min(5 * 2 ** (attempt - 1), 60)
                logger.info(
                    "embedding rate-limited (batch %d, attempt %d/%d) — waiting %ds",
                    start // _EMBED_BATCH + 1, attempt, attempts, pause,
                )
                time.sleep(pause)
        vectors.extend(e.values for e in response.embeddings)
    return _l2_normalise(np.asarray(vectors, dtype="float32"))


class SemanticIndex:
    """Slide embeddings, built once and cached next to the slide index."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.vectors = None
        self.fingerprint = ""
        #: Which embedding space `self.vectors` live in. Queries must use the
        #: same one; mixing two returns plausible numbers and wrong slides.
        self.provider = ""

    @property
    def _name(self) -> str:
        """For log lines: which index (slides' embeddings.npz, the report's
        faq_embeddings.npz). Tests build one without a path."""
        return getattr(self.path, "name", None) or "embeddings"

    # -- persistence --

    def load(self, fingerprint: str) -> bool:
        try:
            import numpy as np

            with np.load(self.path, allow_pickle=False) as data:
                if str(data["fingerprint"]) != fingerprint:
                    # The file name, not "slide": the report FAQ uses this
                    # class too, and on 15 Oct "slide embeddings are stale"
                    # sent the robot's reset hunt after the slide index.
                    logger.info("%s is stale — its texts changed", self._name)
                    return False
                cached = str(data["provider"]) if "provider" in data else ""
                wanted = choose_provider()
                if cached and wanted != "off" and cached != wanted:
                    # Not a preference — a correctness requirement. Querying
                    # a Gemini-built index with a local encoder ranks slides
                    # at random and reports no error at all.
                    logger.info(
                        "slide embeddings were built with %r but %r is active — rebuilding",
                        cached, wanted,
                    )
                    return False
                self.vectors = data["vectors"]
                self.fingerprint = fingerprint
                self.provider = cached or "gemini"
                return True
        except Exception:
            return False

    def save(self) -> None:
        import numpy as np

        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            self.path, vectors=self.vectors, fingerprint=self.fingerprint,
            provider=self.provider or "gemini",
        )

    # -- use --

    def build(self, texts: list[str], fingerprint: str) -> None:
        from app.config import settings

        if not settings.embed_build:
            # Both callers catch this and search by words (EMBED_BUILD).
            raise RuntimeError("%s is stale and EMBED_BUILD is off on this machine -- "
                               "build it on the PC and deploy" % self._name)
        provider = choose_provider()
        if provider == "off":
            raise RuntimeError("no embedding provider available")
        logger.info("embedding %d texts for %s with %s", len(texts), self._name,
                    embedding_signature(provider))
        try:
            # Patient on purpose — this runs once and the result is cached to
            # disk. Waiting out a per-minute cap here is what stops every
            # restart from starting over. See `_embed`.
            self.vectors = embed(texts, "RETRIEVAL_DOCUMENT", provider, attempts=6)
        except Exception:
            # The fallback is whole-index, never per-batch. Half a deck in one
            # space and half in another would be worse than no index at all:
            # the halves are not comparable, and nothing would say so.
            if provider != "gemini" or _local_encoder() is None:
                raise
            logger.warning(
                "the embedding API failed — rebuilding the whole index locally",
                exc_info=True,
            )
            provider = "local"
            self.vectors = embed(texts, "RETRIEVAL_DOCUMENT", provider)
        self.provider = provider
        self.fingerprint = fingerprint
        self.save()

    def similarities(self, query: str):
        """Cosine similarity to every slide. Both sides are unit vectors, so
        the dot product is the cosine."""
        if self.vectors is None:
            return None
        # Same space as the vectors were built in, always. A query embedded
        # by a different model would be compared against these by cosine
        # similarity, which returns a number rather than an error.
        return self.vectors @ embed(
            [query], "RETRIEVAL_QUERY", self.provider or "gemini"
        )[0]


# ============================== fusion ==============================

#: Reciprocal rank fusion. Combines rankings without needing the two scores
#: to share a scale — BM25 is unbounded and cosine similarity is 0–1, so
#: adding them directly would be meaningless. k=60 is the value from the
#: original RRF paper and is not sensitive.
RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[int]], weights: list[float]) -> dict[int, float]:
    """`rankings` are lists of document indices, best first."""
    fused: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, index in enumerate(ranking):
            fused[index] = fused.get(index, 0.0) + weight / (RRF_K + rank + 1)
    return fused


def fingerprint_of(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ==================== the commercial-question guard ====================
#
# Moved here from knowledge.py (2026-08-25) so search_my_documents could use
# it without importing knowledge's @tool registrations. The web library is
# full of marketing articles saying things like "yields 7-10%", and a money
# question routed there would have quoted an unsigned number to a customer —
# the exact thing the whole approval system exists to prevent.

COMMERCIAL_TERMS = (
    # Thai
    "ราคา", "โปรโมชั่น", "โปรโมชัน", "ส่วนลด", "ผ่อน", "ดาวน์", "กี่บาท",
    "เท่าไหร่", "เท่าไร", "งบ", "ค่าส่วนกลาง", "ดอกเบี้ย", "สินเชื่อ",
    # English
    "price", "pricing", "cost", "promotion", "discount", "installment",
    "down payment", "how much", "afford", "budget", "mortgage", "payment",
    # Chinese (simplified and traditional)
    "价格", "價格", "多少钱", "多少錢", "费用", "費用", "优惠", "優惠",
    "折扣", "首付", "分期", "贷款", "貸款", "预算", "預算",
    # Japanese
    "価格", "値段", "いくら", "費用", "割引", "ローン", "予算",
    # Korean
    "가격", "얼마", "비용", "할인", "분양가", "예산", "대출",
    # Russian
    "цена", "цены", "стоимость", "сколько стоит", "скидка", "рассрочка",
    # Other common buyer languages
    "prix", "preis", "precio", "prezzo", "سعر", "السعر", "كم الثمن",
    "कीमत", "मूल्य", "giá", "harga", "berapa",

    # --- Not about money, but answerable only from an approved fact ---
    #
    # Found by measurement, not by thinking of them: `scripts/eval_search.py`
    # showed these reaching the slide search and coming back with a picture.
    # "ห้องขายเปิดกี่โมง" got a rendering of Common Sphere; "โอนได้เมื่อไหร่"
    # got an aspirational branding slide.
    #
    # They belong here for the same reason the prices do. Opening hours is one
    # of the four fields still blank in condo_facts.json, and ownership and
    # handover are commitments a developer makes — none of them is a thing to
    # infer from a caption written by a model looking at a photograph.
    "เปิดกี่โมง", "เวลาทำการ", "เปิดปิดกี่โมง", "opening hours", "what time",
    "ต่างชาติ", "ชาวต่างชาติ", "foreigner", "foreign buyer", "freehold",
    "leasehold", "กรรมสิทธิ์", "โอน", "handover", "transfer", "完工", "过户",
    "เสร็จเมื่อไหร่", "สร้างเสร็จ", "completion",

    # --- Yield / return / rent, added 2026-09-12 after measuring mydocs ---
    #
    # The guard listed ราคา/งบ/ดอกเบี้ย/กี่บาท but had no word for *yield* or
    # *return*, so "ผลตอบแทนการลงทุนกี่เปอร์เซ็นต์" walked straight past it and
    # search_my_documents answered found=True from the marketing web dump —
    # which is the file this comment (and the one at the top of this block)
    # names as full of "yields 7-10%". A return question is the money question
    # that library answers with the most confident unsigned numbers. Measured
    # against the innocent phrases the docstring protects (ผ่อนคลาย, น่าลงทุน,
    # ยังไงบ้าง): none of these matches them. "ลงทุน" alone is left out on
    # purpose — "ทำไมพัทยาน่าลงทุน" is the market-strategy doc's own story.
    "ผลตอบแทน", "เปอร์เซ็นต์", "ค่าเช่า", "ปล่อยเช่า",
    "roi", "yield", "rental", "return on", "percent",
)


def _term_is_thai(term: str) -> bool:
    return any("\u0e00" <= ch <= "\u0e7f" for ch in term)


#: Split once, because the two halves need different matching. See
#: `_is_commercial`.
_THAI_TERMS = tuple(t for t in COMMERCIAL_TERMS if _term_is_thai(t))
_NON_THAI_TERMS = tuple(t for t in COMMERCIAL_TERMS if not _term_is_thai(t))

#: "เท่าไหร่" is Thai for "how much" — and for how far, how long, how big.
#: As a bare guard word it refused every measurement question as a price
#: question: measured live 2026-10-07 (scripts/eval_live_qa.py), the guard
#: fired 15 times, including "ห่างจากหาดจอมเทียนเท่าไหร่" (answered
#: "ข้อมูลราคาและโปรโมชั่นไม่มีในคลังภาพ") and "สระยาวเท่าไหร่", whose answer
#: (the 155 m lagoon) is on an approved slide. So the bare word counts only
#: when nothing in the question measures something. A real money word
#: (ราคา, ค่าส่วนกลาง, งบ, ผ่อน...) still catches the question on its own —
#: "ห้องใหญ่ราคาเท่าไหร่" is commercial because of ราคา, not เท่าไหร่.
_BARE_AMOUNT = frozenset({"เท่าไหร่", "เท่าไร"})
#: Matched INSIDE tokens: the segmenter is taught the deck's keywords, so
#: "ขนาดห้อง" can arrive as one token (it did, in the test suite's order).
#: Loosening the guard on a false hit only means the question reaches the
#: slide search — any real money word still refuses it first.
_MEASURE_PARTS = ("ห่าง", "ไกล", "ระยะ", "ยาว", "กว้าง", "สูง", "ลึก", "ใหญ่",
                  "ขนาด", "พื้นที่", "เมตร", "กิโล", "ใช้เวลา", "นาที", "ชั่วโมง",
                  "ความจุ",
                  # 12 Oct, the report's FAQ walked through this gate: "มีซาวน่า
                  # กี่ห้อง ร้อนเท่าไร" went to the sales team as a price question
                  "ร้อน", "อุณหภูมิ", "องศา")
#: Whole tokens only: จุ (holds) sits inside จุด (point) and much else.
_MEASURE_EXACT = frozenset({"จุ"})


def _measures_something(words: list[str]) -> bool:
    return any(w in _MEASURE_EXACT or any(p in w for p in _MEASURE_PARTS) for w in words)


#: Review 12 Oct: matched inside tokens, the exemption also let real price
#: questions about a unit through -- "ห้องวิวทะเลชั้นสูงเท่าไหร่" (ชั้นสูง),
#: "ห้องขนาด 30 ตารางเมตรเท่าไหร่", "ตารางเมตรละเท่าไหร่", "ห้องใหญ่สุดเท่าไหร่".
#: The bare word is still "how much" when the question is priced per
#: something (ละ), or is about a unit and either states its measure already
#: (a number: the size is given, so the size is not what is asked) or
#: measures it only with the words that pick WHICH unit (high floor, biggest).
#: "ห้องขนาดเท่าไหร่", "ห้องห่างจากลิฟต์เท่าไหร่", "ตึกสูงเท่าไหร่" stay measures.
_UNIT_WORDS = frozenset({"ห้อง", "ยูนิต", "คอนโด", "ห้องชุด", "ห้องนอน"})
_PICKING_PARTS = ("ใหญ่", "สูง")
#: 12 Oct, measured over the 316 questions the approved report answers in its
#: own FAQ: the rule above sent 11 non-money ones to the sales team as price
#: questions, because a "ห้อง" there is a ROOM OF THE PROJECT, not a unit for
#: sale ("ห้อง Spinning Class ใหญ่เท่าไร", "ห้องน้ำชั้น 3 ใหญ่เท่าไร", "Common
#: Sphere ... มีห้องประชุมกี่ห้อง"), or a part of the unit is measured ("เพดาน
#: ห้องสูงเท่าไร"). A question naming one of these measures that thing.
#: Matched in the JOINED tokens, normalised like `tokenize` (NFKC splits ำ, and
#: newmm splits "ห้องน้ำ" into ห้อง+น้ำ and "ซาวน่า" into ซาว+น่า). No word that
#: sits inside a common one: ครัว is in ครอบครัว, บ่อ in บ่อย -- hence บ่อบอล.
_MEASURED_THINGS = tuple(unicodedata.normalize("NFKC", t) for t in (
    "เพดาน", "ฝ้า", "ห้องน้ำ", "ระเบียง", "หน้าต่าง", "ประตู", "เตียง", "ห้องครัว",
    "สระ", "ซาวน่า", "ฟิตเนส", "ห้องประชุม", "จักรยาน", "บ่อบอล", "ลิฟต์",
    "spinning", "cycling", "pilates", "bungee", "cinema", "sphere", "lounge",
    "junior", "biogenesis", "office", "sauna", "gym", "pool"))
#: "the size is given, so the size is not what is asked" -- a number WITH a
#: size unit. A bare digit is not that: it is in the type's name ("1-Bedroom
#: ขนาดเท่าไร" was refused as a price question).
_SIZE_GIVEN = re.compile(r"\d+(?:\.\d+)?\s*(?:ตาราง|ตร\.?\s*ม|sq|เมตร|m2|m²)", re.IGNORECASE)


def _asks_a_price_anyway(words: list[str], query: str = "") -> bool:
    # "ตารางเมตรละเท่าไหร่" is priced per something; "อย่างละกี่ห้อง" is "of each".
    if any(w == "ละ" and (i == 0 or words[i - 1] != "อย่าง") for i, w in enumerate(words)):
        return True
    if not any(w in _UNIT_WORDS for w in words):
        return False
    joined = "".join(words)
    if any(t in joined for t in _MEASURED_THINGS):
        return False
    if _SIZE_GIVEN.search(query or "") if query else any(ch.isdigit() for w in words for ch in w):
        return True
    measuring = [w for w in words if w in _MEASURE_EXACT or any(p in w for p in _MEASURE_PARTS)]
    return all(any(a in w for a in _PICKING_PARTS) for w in measuring)


#: How a number from a result is SAID (owner 21 Oct: "การพูดตัวเลขให้เป็น
#: ทศนิยม 1 ตน. แล้วก็พยายามพูดให้มันเลขไม่เยอะ ... ห่างจากทะเล 600 เมตร ก็อาจจะ
#: เปลี่ยนเป็นใช้เวลา ... นาที"). Robot 20 Oct: "The room is 6.884 by 2.90
#: metres", "7.616 by 2.90 metres". Rides on every search and report result
#: (and the tapped-picture answers in app/stories.py); here because this is
#: the shared module with no @tool in it. Time only when the result has it --
#: the beach is 15-24 minutes on foot by the report, never a computed 5.
SPOKEN_NUMBERS = (
    "ตัวเลขที่พูด: ทศนิยมไม่เกินหนึ่งตำแหน่งพร้อมคำว่า 'ประมาณ' (7.616 เป็น 'ประมาณ 7.6') "
    "ห้ามอ่านกว้างคูณยาว เช่น 'The room is 6.884 by 2.90 metres' ถ้าลูกค้าไม่ได้ถามขนาดด้านกว้างยาว "
    "ระยะทางที่ผลมีเวลาเดินทาง ให้พูดเวลาแทนระยะ เช่น 'ขับรถประมาณ 4 นาที' "
    "ใช้ตัวเลขให้น้อยที่สุดที่ตอบคำถามได้ ห้ามคำนวณตัวเลขใหม่")


def is_commercial(query: str) -> bool:
    """Never answerable from slide captions, in any language.

    **Thai terms are matched as words, not as substrings**, and that is not a
    refinement — the substring version was refusing ordinary questions:

        "ที่ออกกำลังกายเป็นยังไงบ้าง"  ->  refused, because ยังไ*งบ*้าง
                                          contains งบ (budget)
        "ซาวน่าช่วยผ่อนคลายไหม"        ->  refused, because *ผ่อน*คลาย
                                          contains ผ่อน (instalment)

    Thai is written without spaces, so `term in text` finds a word inside an
    unrelated one constantly. This is the same failure that forced the whole
    retrieval rewrite — ราคา (price) matching inside อาคาร (building) — and it
    had quietly reappeared in this function, where the cost is higher: a
    refused question is a guest being told "ask the sales team" when they asked
    what the gym looks like. "เป็นยังไงบ้าง" is about as common as Thai
    phrasing gets.

    Found only after `scripts/eval_search.py` was made to walk this gate the
    way the tool does. It had been running past it for as long as it existed.

    Non-Thai terms keep substring matching: Latin scripts have spaces, and
    there is no segmenter here for Han or kana.
    """
    return _commercial_by(query, _THAI_TERMS, _NON_THAI_TERMS)


def _commercial_by(query: str, thai_terms: tuple, non_thai_terms: tuple) -> bool:
    q = (query or "").lower()
    if any(term in q for term in non_thai_terms):
        return True

    from app.tools.retrieval import tokenize

    words = tokenize(q)
    if not words:
        return False
    haystack = " %s " % " ".join(words)
    measures = _measures_something(words) and not _asks_a_price_anyway(words, q)
    for term in thai_terms:
        needle = " %s " % " ".join(tokenize(term))
        if needle in haystack:
            if term in _BARE_AMOUNT and measures:
                continue   # "how far/long/big", not "how much" — see _BARE_AMOUNT
            return True
    return False


#: When the project is finished. In the gate since before the report existed
#: (a completion date is a developer's commitment, never a caption's), but the
#: owner approved the report for answering everything but unit prices (12 Oct)
#: and its FAQ carries the date with its own caveat ("ตามระบบขาย ... ยืนยันใน
#: สัญญา"). Replay of the 20 Oct log (27 Oct): "expected completion date" was
#: refused at this gate and handed to the sales team while the approved
#: answer sat in the FAQ. A question that is commercial ONLY because of these
#: words may take the report's FAQ answer (knowledge.search_condo_info) --
#: still never a slide; any other COMMERCIAL_TERMS word keeps it a price
#: question, and only the FAQ's completion-date answers are taken
#: (knowledge._DATE_QUESTION -- COMMERCIAL_TERMS has no word for every
#: payment term, review 27 Oct).
_DATE_TERMS = ("เสร็จเมื่อไหร่", "สร้างเสร็จ", "completion", "handover", "完工")


def asks_only_a_date(query: str) -> bool:
    """`is_commercial`, and only because it asks when the project is done."""
    if not is_commercial(query):
        return False
    return not _commercial_by(query, tuple(t for t in _THAI_TERMS if t not in _DATE_TERMS),
                              tuple(t for t in _NON_THAI_TERMS if t not in _DATE_TERMS))

