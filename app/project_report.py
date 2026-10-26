"""The project report as an approved customer source (owner, 12 Oct 2026).

"ข้อมูลพวกนี้อนุมัติให้หุ่นตอบได้ จะมีแค่เรื่องราคาห้องเท่านั้นที่ห้ามพูด" -- the
report built from the owner's `data embassyworld.zip` (6 Oct) may answer guests:
parking, sizes, heights, materials, the things the approved facts never
carried. Live, the same day, four such questions ("ที่จอดรถกี่คัน", "สระยาว
เท่าไหร่", "ฟิตเนสใหญ่แค่ไหน", "ตึกสูงเท่าไหร่") were all handed to the sales
team while the report had every answer -- because the whole document library
is closed to the sales profile (mydocs has no per-document approval).

So this is not "open the library". It is one folder, opened by an approval
manifest the knowledge policy evaluates like any other source
(`data/projects/<project>/sales/project_report_approval.json`):

* the six approval fields, checked by `evaluate_claim` -- deny by default;
* an allow-list of chapter files, each pinned by sha256: a report edited
  after the approval is not approved, so a changed file drops out until
  somebody approves it again (the unit-plan rule: bytes that still hash);
* chapters that are not customer facts stay out (method notes, the pricing
  and sales-status chapters, the source-conflict notes, the unit appendix);
* lines carrying money never leave the server -- room prices are the one
  thing the owner kept closed, and a chunk about room types can sit next to
  a price table. Cut at the source, like UNITS_SHOW_PRICE: a field never
  sent cannot be read out;
* lines naming what the owner overruled the same morning (Thermal Galaxy,
  Rooftop, Utapao's 40-60 minutes -- the facts carry 30) are cut too, in
  English AND Thai ("สวนบนดาดฟ้า"...), and a heading that names one takes
  its whole section with it (`clean`).

Built once, in the background warm-up (`search_condo_info("warm up")` at
startup): tokenising ~2 MB of Thai inside a tool call would blow its budget.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
from pathlib import Path

from app.config import ACTIVE_PROJECT_ID, settings

ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("condo_voice.project_report")

#: Results per question, and the judging floor (mydocs' numbers).
MAX_RESULTS = 2
MIN_COVERAGE = 0.5
#: How many BM25 neighbours coverage gets to judge.
POOL = 40

#: A line with money in it: room prices are the one thing that stays closed.
_MONEY = re.compile(r"บาท|ล้าน|THB|฿|baht|million|ราคา\S*\s*\d|price", re.IGNORECASE)

_lock = threading.Lock()
_index: dict | None = None


def manifest_path(project_id: str | None = None) -> Path:
    return Path(ROOT) / "data" / "projects" / (project_id or ACTIVE_PROJECT_ID) / "sales" / \
        "project_report_approval.json"


def load_manifest(path: Path | None = None) -> dict | None:
    try:
        data = json.loads((path or manifest_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def approved(manifest: dict | None) -> bool:
    if not manifest:
        return False
    from app.knowledge_policy import evaluate_claim

    decision = evaluate_claim(manifest, settings.project_id)
    if not decision.allowed:
        logger.info("project report not usable: %s", decision.trace())
    return decision.allowed


_HEADING = re.compile(r"\s{0,3}(#{1,6})\s")


def clean(text: str, deny_terms: list[str], state: dict | None = None) -> str:
    """Drop every line that carries money or a term the owner overruled --
    and, when a HEADING names an overruled thing, its whole section (up to
    the next heading of the same or a higher level).

    Line by line alone kept the wrong half (review 12 Oct): "## 12.12
    สวนบนดาดฟ้า และ Eco System Quality Dashboard" survived while the line
    under it saying the Rooftop Garden is in no landscape drawing was cut,
    and Emma turned the heading into "บนดาดฟ้ามีสวนลอยฟ้า...". Under "### 5.5.1
    สนามบินอู่ตะเภา" the bullets "≈ 41 กม. (≈ 55 นาที)" and "ให้ตอบ 40–60
    นาที" never name the airport, so they passed -- the very figure the owner
    overruled. `state` carries an open section across the chunks of one file
    (a section can span chunks)."""
    state = state if state is not None else {}
    keep = []
    for line in text.splitlines():
        heading = _HEADING.match(line)
        level = len(heading.group(1)) if heading else 0
        denied = any(term and term.lower() in line.lower() for term in deny_terms)
        skip = state.get("skip_level")
        if skip and level and level <= skip:
            state["skip_level"] = skip = None      # the next section of that rank: open again
        if skip:
            continue
        if denied:
            if level:
                state["skip_level"] = level
            continue
        if _MONEY.search(line):
            continue
        keep.append(line)
    # Owner 12 Oct: never "ไม่มีข้อมูล" to a guest. The report says it about
    # what is not settled yet ("ยังไม่มีข้อมูลความสูงเพดานสำเร็จรูป") and the
    # model repeats the report's words -- same meaning, the guest-facing word.
    # And the concept caveat the owner banned (10 Oct) is cut here as it is
    # from facts, slides and search results: report passages never went
    # through that door, and 22 of its 299 FAQ answers carry it (21 Oct --
    # "the co-working space is still not designed" on a tapped picture).
    from app.tools.retrieval import strip_concept_caveat

    text = "\n".join(strip_concept_caveat(line) for line in keep)
    return text.strip().replace("ยังไม่มีข้อมูล", "ยังไม่สรุป")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(manifest: dict) -> dict:
    from app.tools.mydocs import _chunks
    from app.tools.retrieval import BM25, content_tokens, robust_tokens, tokenize

    folder = Path(ROOT) / str(manifest.get("folder") or "")
    deny = [str(t) for t in manifest.get("deny_terms") or []]
    chunks: list[dict] = []
    faq: list[dict] = []
    skipped = []
    for name, pin in sorted((manifest.get("files") or {}).items()):
        path = folder / name
        try:
            if not path.is_file() or _sha256(path) != pin:
                skipped.append(name)
                continue
        except OSError:
            skipped.append(name)
            continue
        state: dict = {}                        # an overruled section may span chunks
        for chunk in _chunks(path, name):
            text = clean(chunk["text"], deny, state)
            if len(text) >= 40:
                chunks.append({"file": name, "text": text})
        faq.extend(_faq_pairs(path.read_text(encoding="utf-8"), name, deny))
    faq.extend(_faq_aliases(manifest, faq, deny))
    if skipped:
        logger.warning("project report: %d chapter(s) not served (missing or changed since "
                       "the approval): %s", len(skipped), ", ".join(skipped))
    bm25 = BM25([content_tokens(robust_tokens(c["text"])) for c in chunks])
    words = [set(content_tokens(tokenize(c["text"]))) for c in chunks]
    faq_words = [set(content_tokens(tokenize(_focus(f["q"])))) for f in faq]
    # Word rarity over the report's OWN questions: the English (FAQ_ALIASES)
    # are more questions to match, not a new calibration -- counted in, they
    # made "developer" common and "Who is the developer?", the FAQ's own
    # question word for word, fell under FAQ_MIN_INFORMATION (measured 20 Oct).
    own = [ws for ws, f in zip(faq_words, faq) if not f.get("alias")]
    df: dict[str, int] = {}
    for ws in own:
        for w in ws:
            df[w] = df.get(w, 0) + 1
    faq_idf = {w: math.log((len(own) + 1) / (n + 0.5)) for w, n in df.items()}
    logger.info("project report indexed: %d chunk(s) from %d chapter(s), %d FAQ question(s)",
                len(chunks), len({c["file"] for c in chunks}), len(faq))
    return {"key": _index_key(manifest),
            "chunks": chunks, "bm25": bm25, "words": words,
            "faq": faq, "faq_words": faq_words, "faq_idf": faq_idf, "faq_own": len(own),
            "faq_semantic": _faq_semantic(manifest, faq)}


#: The report answers its own questions (19_faq.md: **ถาม:** / **ตอบ:**, and
#: **Q:** / **A:** in its English part). Measured 12 Oct, offline over all 316
#: of them: the chunk search handed Emma the answer for 234; "เพดานห้องสูง
#: เท่าไร" found nothing because the report says ความสูงเพดาน, "1-Bedroom
#: ขนาดเท่าไร" because it says 1 ห้องนอน, and live "สองห้องนอนมีกี่ห้องน้ำ" went
#: to the sales team while chapter 14 says two. The guest's question is now
#: matched to the report's question -- by meaning (the same local model the
#: slide search uses) and by its words -- and the report's own answer comes
#: back, cleaned like every other passage.
#: English for the FAQ's Thai questions (robot 20 Oct): an English guest
#: question shares no WORD with a Thai FAQ question, so its coverage was 0
#: and only meaning >= FAQ_SURE_SIMILARITY could match -- "how high is the
#: ceiling" found "เพดานห้องสูงเท่าไร" at 0.845 and was dropped, while wrong
#: pairs sat right beside it ("start construction" -> "จะสร้างเสร็จเมื่อไร"
#: 0.839), so the bar could not come down. Each English question is tied to
#: its Thai question by exact text and brings back THAT question's approved
#: answer -- the file holds no answer text. A Thai question edited in the
#: report no longer matches, and its English drops out (logged).
FAQ_ALIASES = "project_report_faq_en.json"


def _alias_path(manifest: dict) -> Path:
    return manifest_path(manifest.get("project_id")).with_name(FAQ_ALIASES)


def _index_key(manifest: dict) -> str:
    """The manifest and the English questions: a change to either rebuilds."""
    path = _alias_path(manifest)
    try:
        aliases = _sha256(path) if path.is_file() else ""
    except OSError:
        aliases = ""
    return json.dumps(manifest, sort_keys=True, ensure_ascii=False) + "|" + aliases


def _faq_aliases(manifest: dict, faq: list[dict], deny: list[str]) -> list[dict]:
    path = _alias_path(manifest)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("project report FAQ: %s unreadable -- Thai questions only", path.name)
        return []
    pairs = {(f["file"], f["q"]): f for f in faq}
    chapter = data.get("chapter")
    out, stale = [], 0
    for item in data.get("aliases") or []:
        pair = pairs.get((chapter, item.get("th"))) if isinstance(item, dict) else None
        if pair is None:
            stale += 1
            continue
        for en in item.get("en") or []:
            if (isinstance(en, str) and en.strip()
                    and not any(t and t.lower() in en.lower() for t in deny)):
                out.append({"file": pair["file"], "q": en.strip(), "a": pair["a"], "alias": True})
    if stale:
        logger.warning("project report FAQ: %d English question(s) in %s name a Thai question the "
                       "served report no longer has -- left out", stale, path.name)
    return out


_FAQ_Q = re.compile(r"^\*\*(?:ถาม|Q):\*\*\s*(.+?)\s*$")
_FAQ_A = re.compile(r"^\*\*(?:ตอบ|A):\*\*\s*(.+?)\s*$")


def _faq_pairs(text: str, name: str, deny: list[str]) -> list[dict]:
    pairs, question, state = [], None, {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if _HEADING.match(line):
            clean(line, deny, state)             # an overruled heading closes its section's answers
            question = None
            continue
        m = _FAQ_Q.match(line)
        if m:
            question = m.group(1)
            continue
        m = _FAQ_A.match(line)
        if m and question:
            answer = clean(m.group(1), deny, dict(state))
            if state.get("skip_level") is None and len(answer) >= 20 \
                    and not any(t and t.lower() in question.lower() for t in deny):
                pairs.append({"file": name, "q": question, "a": answer})
            question = None
    return pairs


def _faq_semantic(manifest: dict, faq: list[dict]):
    """The FAQ questions' embeddings, cached beside the manifest; None when no
    embedding model is available (the word match still works)."""
    if not faq:
        return None
    try:
        from app.tools.retrieval import SemanticIndex, choose_provider, fingerprint_of

        if choose_provider() == "off":
            return None
        index = SemanticIndex(manifest_path(manifest.get("project_id")).with_name("faq_embeddings.npz"))
        fingerprint = fingerprint_of([f["q"] for f in faq])
        if not index.load(fingerprint):
            index.build([f["q"] for f in faq], fingerprint)
        return index
    except Exception:
        logger.warning("project report FAQ: no embeddings, matching by words only", exc_info=True)
        return None


def _get_index(manifest: dict) -> dict:
    global _index
    key = _index_key(manifest)
    with _lock:
        if _index is None or _index["key"] != key:
            _index = _build(manifest)
        return _index


#: Every page of this report is about this project, so its name decides
#: nothing -- and it decided everything: live 12 Oct, "โครงการ Embassy World
#: ห่างจาก Terminal 21 กี่กิโลเมตร" ranked the address FAQ (dense in
#: "Embassy World ... ห่าง ... กิโลเมตร") over the travel table holding
#: "Terminal 21 Pattaya | 7.2 | 11.2 | 12". Dropped before ranking and judging.
_PROJECT_NAME = re.compile(r"(?i)\bembassy\s*world\b|\bembassy\b|เอ็มบาสซี่?\s*เวิลด์|เอ็มบาสซี่?|โครงการนี้|โครงการ")
#: The guest's word -> the report's word (the report says พันธุ์ไม้; "โครงการ
#: มีพืชชนิดอะไรบ้าง" found nothing with 34 names in the appendix).
_SYNONYMS = {"พืช": "พันธุ์ไม้ ต้นไม้", "ต้นไม้": "พันธุ์ไม้", "plants": "พันธุ์ไม้", "trees": "พันธุ์ไม้",
             # the travel table names places in English ("Central Pattaya | 5.5 | 9.9")
             "เซ็นทรัล": "Central", "เทอร์มินอล": "Terminal", "วอล์คกิ้งสตรีท": "Walking Street",
             "วอล์กกิ้งสตรีท": "Walking Street", "สุวรรณภูมิ": "Suvarnabhumi", "ดอนเมือง": "Don Mueang",
             "โรงพยาบาล": "Hospital", "ห้าง": "Central Terminal",
             # the report names the types "Studio / 1-Bedroom / 2-Bedroom"; live 12 Oct
             # "สองห้องนอนมีกี่ห้องน้ำ" got the 1-Bedroom and Studio FAQ instead
             "สองห้องนอน": "2-Bedroom 2 ห้องนอน", "2 ห้องนอน": "2-Bedroom",
             "หนึ่งห้องนอน": "1-Bedroom 1 ห้องนอน", "1 ห้องนอน": "1-Bedroom", "ห้องนอนเดียว": "1-Bedroom",
             "สตูดิโอ": "Studio", "two bedroom": "2-Bedroom", "one bedroom": "1-Bedroom",
             # the travel chapters head their tables in Thai (4.9.7 ร้านอาหารและคาเฟ่,
             # หมวด ท่องเที่ยว); robot 20 Oct "nearest restaurant" got the convenience
             # stores and the beach, "tourist attraction" the banks -- both to the sales team
             "restaurant": "ร้านอาหาร", "cafe": "คาเฟ่", "tourist": "ท่องเที่ยว",
             "attraction": "ท่องเที่ยว", "sightseeing": "ท่องเที่ยว"}


def _focus(query: str) -> str:
    q = _PROJECT_NAME.sub(" ", query or "")
    extra = [v for k, v in _SYNONYMS.items() if k in q.lower()]
    q = " ".join([q] + extra).strip()
    return q or (query or "")


def _variants(query: str) -> list[tuple[list[str], tuple[str, ...]]]:
    """The question's words to judge coverage on: the guest's own, and --
    when a synonym applies -- the same question in the report's words, which
    counts only where the report's own English name is in the passage.

    A synonym is an ALTERNATIVE, not one more word to find. Added to the
    question (`_focus`, still what BM25 ranks on) it made the bar stricter:
    "สองห้องนอนมีกี่ห้องน้ำ" needed สอง AND 2-Bedroom in one passage, the
    table that answers it ("มีกี่ห้องน้ำ / แอร์ | 2 ห้องน้ำ") says only
    2-Bedroom, and the 1-Bedroom FAQ won (live 12 Oct). And the digit is not a
    content word, so "bedroom" alone would have let every 1-Bedroom passage
    through: the name ("2-bedroom", Latin -- matched as text, never Thai) must
    be there."""
    from app.tools.retrieval import content_tokens, tokenize

    q = _PROJECT_NAME.sub(" ", query or "")
    low = q.lower()
    out = [(content_tokens(tokenize(q)), ())]
    found = [(k, v) for k, v in sorted(_SYNONYMS.items(), key=lambda kv: -len(kv[0])) if k in low]
    if found:
        names = []
        for k, v in found:
            if k not in low:
                continue                  # inside a longer key already replaced
            low = low.replace(k, " " + v + " ")
            head = v.split()[0].lower()
            if head.isascii():
                names.append(head)
        out.append((content_tokens(tokenize(low)), tuple(names)))
    return out


def search(query: str) -> list[dict]:
    """Approved report passages for `query`, best first; [] when the report
    is not approved, missing, or has nothing close enough."""
    manifest = load_manifest()
    if not approved(manifest):
        return []
    from app.tools.mydocs import _coverage
    from app.tools.retrieval import content_tokens, robust_tokens, tokenize

    index = _get_index(manifest)
    if not index["chunks"] and not index.get("faq"):
        return []
    hits = _faq_hits(index, query, manifest)
    if not index["chunks"]:
        for h in hits:
            h.pop("a_text", None)
        return hits
    variants = _variants(query)
    query = _focus(query)
    scores = index["bm25"].scores(content_tokens(robust_tokens(query)))
    # BM25 over n-grams finds the neighbourhood; whole-word coverage decides
    # inside it. Live 12 Oct: the address FAQ scored 120 on "ห่าง ... กิโลเมตร"
    # n-grams at coverage 0.5, the travel table holding the Terminal 21 row
    # 20 at coverage 0.75 -- ranked by score alone the wrong page won.
    pool = [i for i in sorted(range(len(scores)), key=lambda i: -scores[i])[:POOL] if scores[i] > 0]

    def coverage(i: int) -> float:
        text = index["chunks"][i]["text"].lower()
        return max((_coverage(words, index["words"][i]) for words, names in variants
                    if all(n in text for n in names)), default=0.0)

    judged = [(round(coverage(i), 2), scores[i], i) for i in pool]
    passages = []
    for coverage, _score, i in sorted(judged, key=lambda t: (-t[0], -t[1])):
        if coverage < MIN_COVERAGE:
            break
        text = index["chunks"][i]["text"]
        if any(h["a_text"] in text for h in hits if h.get("a_text")):
            continue                              # the passage the FAQ answer already came from
        passages.append({"source_id": manifest.get("source_id"), "file": index["chunks"][i]["file"],
                         "text": text})
        if len(passages) >= MAX_RESULTS:
            break
    for h in hits:
        h.pop("a_text", None)
    return hits + passages


#: Calibrated 12 Oct on the local MiniLM against the 230 guest-style questions
#: of data/eval/qa_live_scenarios.json, eyeballing every match above 0.72, plus
#: off-topic questions (the highest, "ไปเชียงใหม่ยังไง" -> "ไปกรุงเทพฯ ใช้เวลา
#: เท่าไร", scored 0.71). Meaning alone is not precise for short Thai room
#: questions: "มีแบบห้องอะไรบ้าง" -> "ห้องมีเฟอร์นิเจอร์ให้ไหม" at 0.84, "ขอดูรูป
#: ห้องหน่อย" -> "ดูห้องตัวอย่างได้เมื่อไร" at 0.89. So the match also needs the
#: guest's DISTINCTIVE words (idf-weighted over the FAQ questions, as a share
#: of the rarest word's weight) in the FAQ question, and enough of them: "ห้อง"
#: alone (0.26) or "ยูนิต" alone (0.75) carries no question to match -- the
#: facts in the prompt answer those. With
#: these numbers: 34 accepted, 0 of the 20 known-wrong pairs. Cost, measured:
#: "สูงกี่เมตร", "โครงการอยู่ตรงไหน", "Where is the sauna?" no longer match by
#: FAQ (the passage search and the facts still answer them).
FAQ_MIN_SIMILARITY = 0.78
FAQ_MIN_WEIGHTED_COVERAGE = 0.5
FAQ_SURE_SIMILARITY = 0.92
FAQ_MIN_INFORMATION = 0.78
FAQ_MIN_QUESTIONS_FOR_RARITY = 30
#: Without an embedding model (words only): a stricter word bar.
FAQ_WORDS_ONLY_COVERAGE = 0.75
FAQ_MAX = 1
_THAI_CHAR = re.compile(r"[\u0e00-\u0e7f]")
#: Spoken fillers carry no question but counted as rare words the FAQ
#: lacks: "Okay, how high is the ceiling..." lost a fifth of its word cover
#: to "okay" (robot 20 Oct).
_FAQ_FILLERS = frozenset({"okay", "ok", "so", "well", "um", "uh", "hmm", "let", "let's", "lets",
                          "actually", "just"})


def _faq_hits(index: dict, query: str, manifest: dict) -> list[dict]:
    """The report's own answer when the guest asks one of its questions."""
    faq = index.get("faq") or []
    if not faq:
        return []
    from app.tools.retrieval import content_tokens, tokenize

    idf = index.get("faq_idf") or {}
    words = set(content_tokens(tokenize(_focus(query)))) - _FAQ_FILLERS
    # A word the FAQ never uses still counts as information the FAQ lacks.
    # As a share of the rarest word's weight, so the bar does not move with
    # the number of questions (calibrated on 301).
    own = index.get("faq_own", len(faq))
    rarest = math.log((own + 1) / 0.5)
    weight = {w: idf.get(w, math.log(own + 1)) / rarest for w in words}
    information = sum(weight.values())
    # Word rarity means something only over many questions: with a handful,
    # every word is "rare" or "common" by accident (a 1-question FAQ weighed
    # "เพดาน" like "ห้อง"). Below that the meaning + coverage bars decide alone.
    if own >= FAQ_MIN_QUESTIONS_FOR_RARITY and information < FAQ_MIN_INFORMATION:
        return []
    covs = [sum(weight[w] for w in words if w in target) / information for target in index["faq_words"]]
    sims = None
    semantic = index.get("faq_semantic")
    if semantic is not None:
        try:
            sims = semantic.similarities(query)
        except Exception:
            logger.warning("project report FAQ: query embedding failed", exc_info=True)
    # One question in several phrasings (FAQ_ALIASES) is judged as one: its
    # best meaning and its best word cover, each from whichever phrasing has
    # it. Judged phrasing by phrasing, the top one decided alone -- "Why is
    # it called Biogenesis?" ranked the Thai original first (0.919, words
    # 0.26) and failed, while its English phrasing (words 1.0) was never
    # looked at. A question with no English is judged exactly as before.
    # The English phrasings are for guests who do not ask in Thai: a Thai
    # question is judged on the Thai ones alone, exactly as before -- with
    # the English meaning in the mix "ภาพ facility ใต้ดินเป็นของจริงไหม" took
    # the ground-floor answer (measured 20 Oct over 876 questions).
    thai = bool(_THAI_CHAR.search(query or ""))
    groups: dict[str, list[int]] = {}
    for i, f in enumerate(faq):
        if thai and f.get("alias"):
            continue
        groups.setdefault(f["a"], []).append(i)
    scored = []
    for members in groups.values():
        if sims is not None:
            top = max(members, key=lambda i: float(sims[i]))
            sim, cov = float(sims[top]), max(covs[i] for i in members)
            ok = (sim >= FAQ_MIN_SIMILARITY and cov >= FAQ_MIN_WEIGHTED_COVERAGE) or sim >= FAQ_SURE_SIMILARITY
            scored.append((sim, top, ok))
        else:
            top = max(members, key=lambda i: covs[i])
            scored.append((covs[top], top, covs[top] >= FAQ_WORDS_ONLY_COVERAGE))
    scored.sort(key=lambda t: -t[0])
    hits = []
    for _rank, i, ok in scored[:1]:
        if not ok:
            continue
        hits.append({"source_id": manifest.get("source_id"), "file": faq[i]["file"],
                     "text": "ถาม: %s\nตอบ: %s" % (faq[i]["q"], faq[i]["a"]), "a_text": faq[i]["a"][:120]})
        if len(hits) >= FAQ_MAX:
            break
    return hits


def faq(query: str) -> list[dict]:
    """Only the report's own answer to `query` -- the strict FAQ matcher, no
    passages (the guest's words beside the model's query, knowledge.py)."""
    manifest = load_manifest()
    if not approved(manifest):
        return []
    index = _get_index(manifest)
    hits = _faq_hits(index, query, manifest)
    for h in hits:
        h.pop("a_text", None)
    return hits


def reset() -> None:
    global _index
    with _lock:
        _index = None
