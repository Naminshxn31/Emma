"""Stories: the robot face's Interactive Presentation Engine (owner, 12 Oct 2026).

A guest asks; Emma answers by voice AND the portrait screen tells the same
story with motion, beat by beat in sync with her voice, then rests on an
overview the guest can tap (docs/emma-story-engine.md). This module is the
server half: it owns every word and every number of a story. The model picks
WHICH story -- `present_story(topic)`, an enum, the `show_unit(room)` rule --
and the page (client/assets/emma-story.js) only animates what this sends.

The catalog is `data/projects/<project>/stories/stories.json`, built by
`scripts/build_story_assets.py` from the designers' `design/*.json` and the
asset pins. It is a customer source like the facts and the project report,
so it is deny-by-default on every link:

* the six approval fields, evaluated by `knowledge_policy.evaluate_claim`,
  plus `approved_sha256` -- the digest of the stories and asset pins at the
  moment somebody approved them. Any edit after that (a line, a number, a
  picture swapped) is a different digest and the catalog is a draft again,
  until a person approves it again (`scripts/approve_stories.py --by NAME`);
* every story is validated on load: a known template, its lines, its line
  indexes in range, and no text that may never reach a guest -- money
  (prices are the one thing the owner keeps closed), the hedge the owner
  forbade ("แนวคิดเบื้องต้น / อาจเปลี่ยนแปลง"), "ไม่มีข้อมูล", the things he
  overruled (Rooftop, Thermal Galaxy, U-Tapao's 40-60 minutes);
* every picture is pinned by sha256 and re-hashed when served (the unit-plan
  rule: bytes that still hash, or nothing). A story whose picture is missing
  or changed is not offered at all -- half a story is a broken screen;
* a picture whose recorded source names a quotation sheet (prices) or the
  rooftop is refused whatever its hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from pathlib import Path

from app.config import ACTIVE_PROJECT_ID, settings

ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger("condo_voice.stories")

SOURCE_ID = "story_catalog"
CATALOG_NAME = "stories.json"
SCHEMA = 1

TEMPLATES = ("stack", "slabs", "drill", "spotlight", "assembly", "details",
             "distance", "metric", "features", "journey", "neighborhood")
FORMATS = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}
#: A picture pre-sized for a 1080-wide screen is ~100-250 KB; anything near
#: this is not one of ours.
MAX_ASSET_BYTES = 3 * 1024 * 1024

#: Tools a story tap may lead to (besides present_story). Screens and reads
#: only: a tap on a public screen must never move the robot, print, or reach
#: anything that is not a picture of the project.
TAP_TOOLS = frozenset({"show_room_type", "show_plan", "find_units", "compare_unit_types",
                       "search_condo_info"})

_STORY_ID = re.compile(r"[a-z][a-z0-9_]{1,47}\Z")
_TAP_ID = re.compile(r"story_[a-z0-9_]{1,80}\Z")
_ASSET_ID = re.compile(r"[a-z0-9][a-z0-9_\-]{0,79}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

#: A price on a guest's screen or in her mouth from a story: refused outright.
#: Currency, millions, or a price word followed by a figure -- the report's
#: money filter (app/project_report.py) plus dollars. "ราคา" alone is not a
#: price: the smart-home set MUST be called "not included in the room price".
_MONEY = re.compile(r"บาท|ล้าน|THB|฿|baht|million|ราคา\S*\s*\d|price\S*\s*\d|\$\s*\d|\busd\b",
                    re.IGNORECASE)
#: What the owner overruled (the project report's deny_terms, 12 Oct): the
#: rooftop, Thermal Galaxy, and U-Tapao's 40-60 minutes (the facts say ~30).
_DENIED = re.compile(r"roof\s*top|thermal\s*galaxy|40\s*[-–—]\s*60|40\s*ถึง\s*60", re.IGNORECASE)
#: Owner 10 Oct: "ห้ามพูดแบบนี้เป็นอันขาด" -- never the concept hedge.
_HEDGE = re.compile(r"แนวคิดเบื้องต้น|อาจ(มีการ)?เปลี่ยนแปลง|preliminary|may change|subject to change",
                    re.IGNORECASE)
#: Owner 12 Oct: never "ไม่มีข้อมูล" to a guest.
_NO_INFO = re.compile(r"ไม่มีข้อมูล|ไม่ทราบ|ไม่มีในระบบ|no information|don'?t know|do not know",
                      re.IGNORECASE)
#: Sources a picture may never come from, by name: quotation sheets carry
#: prices; the rooftop was overruled.
BANNED_SOURCE = re.compile(r"quotation|roof\s*top|thermal\s*galaxy|price", re.IGNORECASE)

#: What the page normalises before matching cue words (emma-story.js `norm`):
#: lower case, no whitespace or punctuation. The beat weights are counted the
#: same way so "how far into her answer" means the same on both sides.
_NORM = re.compile(r"[\s​‌‍\-‐-―.,!?'\"“”‘’()…:;"
                   r"·/、。，！？]+")

#: Keys whose numbers are geometry or timing, not claims (see `claim_numbers`).
_GEOMETRY_KEYS = frozenset({"at", "x", "y", "zoom", "scale", "ground_y", "roof_y", "frame_ms",
                            "line", "lines", "spot_line", "rise_line", "footnote_line", "meter_x", "dec",
                            "order_bottom_up", "crop", "w", "h",
                            # the drawn templates (journey, neighborhood): bar lengths
                            # mirror a value_en that says the figure in words
                            "x1", "y1", "x2", "y2", "to_x", "bar", "bar_max", "aspect",
                            "label_x", "label_y", "area_x", "area_y", "footnote_x"})

_cache: tuple[tuple, dict | None] | None = None
#: (path, mtime_ns, size) -> sha256, so a lookup does not hash every picture
#: every time, while a changed file is hashed again.
_digest_cache: dict[tuple[str, int, int], str] = {}
_warned: set[tuple[str, str]] = set()
#: For the catalog `load_catalog` cached: its content digest, and its valid
#: stories with the asset files' stats they were judged on. Without these,
#: every present_story call and every tap validated the whole catalog twice
#: on the session's event loop (~14 ms on the PC, review 12 Oct; more on
#: the robot's ARM) before her answer could start.
_memo: dict[str, tuple] = {}


# ---- where things live --------------------------------------------------------

def folder(project_id: str | None = None) -> Path:
    return Path(ROOT) / "data" / "projects" / (project_id or ACTIVE_PROJECT_ID) / "stories"


def catalog_path() -> Path:
    return folder() / CATALOG_NAME


def assets_dir() -> Path:
    return folder() / "assets"


def reset() -> None:
    global _cache
    _cache = None
    _digest_cache.clear()
    _warned.clear()
    _memo.clear()


def _cached(catalog: dict) -> tuple | None:
    """The load_catalog cache key when `catalog` is the very object it holds
    (a dict a caller built or edited in place is judged afresh)."""
    return _cache[0] if _cache is not None and _cache[1] is catalog else None


# ---- the catalog ------------------------------------------------------------------

def load_catalog(path: Path | None = None) -> dict | None:
    """The catalog as stored, or None (missing, unreadable, another project's)."""
    global _cache
    path = path or catalog_path()
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size, settings.project_id)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    data: dict | None = None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (isinstance(raw, dict) and raw.get("source_id") == SOURCE_ID
                and raw.get("project_id") == settings.project_id
                and isinstance(raw.get("stories"), list) and isinstance(raw.get("assets"), dict)):
            data = raw
        else:
            logger.warning("story catalog is not this project's catalog: %s", path)
    except (OSError, ValueError):
        logger.warning("story catalog unreadable: %s", path)
    _cache = (key, data)
    return data


def content_digest(catalog: dict) -> str:
    """What an approval signs: the stories and the asset pins, canonically."""
    key = _cached(catalog)
    if key is not None and _memo.get("digest", (None,))[0] == key:
        return _memo["digest"][1]
    body = {"stories": catalog.get("stories") or [], "assets": catalog.get("assets") or {}}
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if key is not None:
        _memo["digest"] = (key, digest)
    return digest


def approval_problem(catalog: dict | None) -> str | None:
    """None when a guest may see this catalog; otherwise why not."""
    if not catalog:
        return "no_catalog"
    from app.knowledge_policy import evaluate_claim

    decision = evaluate_claim(catalog, settings.project_id)
    if not decision.allowed:
        return decision.reason
    if catalog.get("approved_sha256") != content_digest(catalog):
        # Edited after the approval: a draft again (the data-console rule --
        # an edit withdraws the approval).
        return "changed_since_approval"
    return None


def approved(catalog: dict | None = None) -> bool:
    catalog = catalog if catalog is not None else load_catalog()
    problem = approval_problem(catalog)
    if problem is not None and catalog:
        logger.debug("story catalog not offered: %s", problem)
    return problem is None


# ---- one story -------------------------------------------------------------------

def norm_len(text: object) -> int:
    return len(_NORM.sub("", str(text or "").lower()))


def _strings(value, skip=("notes_th", "sources")):
    """Every string a story carries, except the owner's notes and citations."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in skip:
                continue
            yield from _strings(item, skip)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, skip)


def banned_text(story: dict) -> list[str]:
    """The guest-facing strings of a story that may never reach a guest."""
    found = []
    for text in _strings(story):
        for name, pattern in (("money", _MONEY), ("overruled", _DENIED),
                              ("hedge", _HEDGE), ("no_info", _NO_INFO)):
            if pattern.search(text):
                found.append(f"{name}: {text[:60]}")
    return found


def asset_refs(params) -> list[str]:
    """Asset ids a story's params name: any `asset` / `*_asset` string, and
    the `frames` / `mood_assets` lists."""
    out: list[str] = []

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            if key in ("frames", "mood_assets"):
                out.extend(v for v in value if isinstance(v, str))
            else:
                for v in value:
                    walk(v, key)
        elif isinstance(value, str) and (key == "asset" or key.endswith("_asset")):
            out.append(value)

    walk(params)
    return out


def _line_refs(template: str, params: dict) -> list:
    """Every `line` index a template's params point at."""
    refs: list = []
    for key in ("items", "floors", "rows", "counters", "chips", "parts", "places", "roads", "measures"):
        for item in params.get(key) or []:
            if isinstance(item, dict) and "line" in item:
                refs.append(item["line"])
    for key in ("spot_line", "line", "rise_line", "footnote_line"):
        if key in params:
            refs.append(params[key])
    for key in ("photo", "route_to", "beach"):
        if isinstance(params.get(key), dict) and "line" in params[key]:
            refs.append(params[key]["line"])
    if template == "details":
        refs.extend(params.get("lines") or [])
    return refs


def problems(story: object, assets: dict) -> list[str]:
    """Why this story cannot be offered (empty = it can)."""
    if not isinstance(story, dict):
        return ["not an object"]
    out = []
    sid = story.get("id")
    if not isinstance(sid, str) or not _STORY_ID.fullmatch(sid):
        out.append("bad id")
    template = story.get("template")
    if template not in TEMPLATES:
        out.append("unknown template %r" % (template,))
    lines = story.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 12:
        out.append("lines: 1..12 required")
        lines = []
    for k, line in enumerate(lines):
        if not isinstance(line, dict) or not all(isinstance(line.get(f), str) and line.get(f).strip()
                                                 for f in ("th", "en")):
            out.append("line %d needs th and en" % k)
            continue
        for f in ("cue_th", "cue_en", "cue_zh"):
            if f in line and not (isinstance(line[f], list)
                                  and all(isinstance(c, str) and c.strip() for c in line[f])):
                out.append("line %d %s must be a list of words" % (k, f))
    params = story.get("params")
    if not isinstance(params, dict):
        out.append("params missing")
        params = {}
    for ref in _line_refs(str(template), params):
        if isinstance(ref, bool) or not isinstance(ref, int) or not 0 <= ref < max(1, len(lines)):
            out.append("line index %r out of range" % (ref,))
    if template == "slabs":
        floors = params.get("floors") or []
        order = params.get("order_bottom_up")
        if order is not None and sorted(order) != list(range(len(floors))):
            out.append("order_bottom_up must be a permutation of the floors")
    for ref in asset_refs(params):
        if ref not in assets:
            out.append("asset %r not pinned" % ref)
    if not story.get("sources"):
        out.append("no sources")
    seen = set()
    for tap in story.get("taps") or []:
        tid = tap.get("id") if isinstance(tap, dict) else None
        if not isinstance(tid, str) or not _TAP_ID.fullmatch(tid) or tid in seen:
            out.append("bad or duplicate tap id %r" % (tid,))
            continue
        seen.add(tid)
        if not isinstance(tap.get("label_en"), str) or not isinstance(tap.get("label_th"), str):
            out.append("tap %s needs label_en and label_th" % tid)
        nxt, call = tap.get("next_story"), tap.get("tool")
        if bool(nxt) == bool(call):
            out.append("tap %s needs exactly one of next_story / tool" % tid)
        if call is not None:
            if (not isinstance(call, dict) or call.get("name") not in TAP_TOOLS
                    or not isinstance(call.get("args", {}), dict)
                    or not all(isinstance(v, (str, int, float)) and not isinstance(v, bool)
                               for v in call.get("args", {}).values())):
                out.append("tap %s: tool must be one of %s with plain args" % (tid, sorted(TAP_TOOLS)))
        if nxt is not None and (not isinstance(nxt, str) or not _STORY_ID.fullmatch(nxt)):
            out.append("tap %s: bad next_story" % tid)
    out.extend(banned_text(story))
    return out


# ---- pictures ---------------------------------------------------------------------

def _digest(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > MAX_ASSET_BYTES:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    digest = _digest_cache.get(key)
    if digest is None:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        _digest_cache[key] = digest
    return digest


def asset_problem(asset_id: str, entry: object, base: Path | None = None) -> str | None:
    """None when the pinned picture is on disk and still hashes to its pin
    (in `base`, the catalog's assets/ folder unless a build names another)."""
    if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
        return "bad asset id"
    if not isinstance(entry, dict):
        return "no pin"
    fmt, digest = entry.get("format"), entry.get("sha256")
    if fmt not in FORMATS or entry.get("file") != f"{asset_id}.{fmt}":
        return "file name must be <id>.<format>"
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return "bad pin"
    if BANNED_SOURCE.search(str(entry.get("source") or "")) or BANNED_SOURCE.search(asset_id):
        return "banned source"
    if _digest((base or assets_dir()) / entry["file"]) != digest:
        return "missing or changed"
    return None


def _asset_stats(catalog: dict) -> tuple:
    out = []
    for entry in (catalog.get("assets") or {}).values():
        name = entry.get("file") if isinstance(entry, dict) else None
        try:
            stat = (assets_dir() / str(name)).stat()
            out.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            out.append((name, None, None))
    return tuple(out)


def _valid_stories(catalog: dict) -> dict[str, dict]:
    key = _cached(catalog)
    if key is not None:
        # the same catalog, the same picture files on disk: the same answer
        # (a file swapped or touched changes its stat and is judged again)
        stats = _asset_stats(catalog)
        hit = _memo.get("valid")
        if hit is not None and hit[0] == key and hit[1] == stats:
            return dict(hit[2])
        out = _judge(catalog)
        _memo["valid"] = (key, stats, out)
        return dict(out)
    return _judge(catalog)


def _judge(catalog: dict) -> dict[str, dict]:
    assets = catalog.get("assets") or {}
    out: dict[str, dict] = {}
    for story in catalog.get("stories") or []:
        issues = problems(story, assets)
        if not issues:
            for ref in asset_refs(story["params"]):
                why = asset_problem(ref, assets.get(ref))
                if why:
                    issues.append("asset %s: %s" % (ref, why))
        if issues:
            # once per story and reason: this runs every time a tool list is built
            key = (str(story.get("id") if isinstance(story, dict) else None), "; ".join(issues[:4]))
            if key not in _warned:
                _warned.add(key)
                logger.warning("story %r not offered: %s", key[0], key[1])
            continue
        if story["id"] in out:
            logger.warning("story %r defined twice; the first is kept", story["id"])
            continue
        out[story["id"]] = story
    return out


def available(catalog: dict | None = None) -> dict[str, dict]:
    """id -> story, for the stories a guest may be shown right now."""
    catalog = catalog if catalog is not None else load_catalog()
    if not catalog or not approved(catalog):
        return {}
    return _valid_stories(catalog)


def offered() -> bool:
    """The switch is on and at least one approved story is complete."""
    return bool(settings.stories_enabled and available())


# ---- what the page and the model get --------------------------------------------

def _resolve(value, ref, key=""):
    if isinstance(value, dict):
        return {k: _resolve(v, ref, k) for k, v in value.items()}
    if isinstance(value, list):
        if key in ("frames", "mood_assets"):
            return [ref(v) for v in value]
        return [_resolve(v, ref, key) for v in value]
    if isinstance(value, str) and (key == "asset" or key.endswith("_asset")):
        return ref(value)
    return value


def _cues(line: dict) -> list[str]:
    seen, out = set(), []
    for field in ("cue_th", "cue_en", "cue_zh"):
        for cue in line.get(field) or []:
            key = cue.strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def page_payload(story: dict, catalog: dict, *, tap_ok=None) -> dict:
    """What emma-story.js animates: template, beats (cue words and how long
    each line is, per language), params with pictures resolved to pinned
    URLs, and the taps this session can follow. No sources, notes or guest
    questions -- the page never needs them."""
    assets = catalog.get("assets") or {}

    def ref(asset_id: str) -> dict:
        entry = assets[asset_id]
        out = {"src": "/story-asset/" + entry["sha256"], "w": int(entry.get("w") or 0),
               "h": int(entry.get("h") or 0)}
        if entry.get("alpha"):
            out["alpha"] = True          # drawn on the dark stage, not on white paper
        return out

    params = _resolve(story.get("params") or {}, ref)
    every = [tap for tap in story.get("taps") or [] if isinstance(tap, dict)]
    taps = [tap for tap in every if tap_ok is None or tap_ok(tap)]
    tap_ids = {tap["id"] for tap in taps}
    # Bind taps to the template's own elements (a docked row, a floor slab, a
    # drill card whose place has a story of its own -- review 12 Oct: the
    # Sky Pool card on the level 3 plan led nowhere): an item may name its
    # tap; otherwise slabs match on next_story, and a
    # stack with exactly one tap per item binds by position -- decided on the
    # catalog's list, so a tap this session cannot follow leaves a plain row
    # rather than shifting every row onto its neighbour's tap.
    template = story.get("template")
    for key in ("items", "floors"):
        elements = params.get(key)
        if not isinstance(elements, list):
            continue
        # only the stack's rows, the slabs' floors and the drill's cards carry
        # a tap themselves; anywhere else a tap is a chip on the overview
        binds = (template, key) in (("stack", "items"), ("slabs", "floors"), ("drill", "items"))
        for k, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            chosen = element.get("tap") if binds else None
            if binds and not chosen and key == "floors" and element.get("next_story"):
                chosen = next((t["id"] for t in every if t.get("next_story") == element["next_story"]), None)
            if binds and not chosen and template == "stack" and len(every) == len(elements):
                chosen = every[k]["id"]
            element.pop("tap", None)
            element.pop("next_story", None)
            if chosen in tap_ids:
                element["tap"] = chosen
    beats = [{"cues": _cues(line), "w": {"th": norm_len(line["th"]), "en": norm_len(line["en"])}}
             for line in story["lines"]]
    return {
        "id": story["id"],
        "template": story["template"],
        "beats": beats,
        "params": params,
        "taps": [{"id": t["id"], "label_en": t["label_en"], "label_th": t["label_th"]} for t in taps],
    }


#: Read with every story (tool results reach the model; the system prompt's
#: tail does not -- CLAUDE.md). Each clause is a failure this engine invites:
#: a paraphrase the page cannot follow, a number nobody approved, a price,
#: "ไม่มีข้อมูล", narrating the screen, a second call in one answer.
INSTRUCTION = (
    "จอหน้าหุ่นกำลังเล่าเรื่องนี้เป็นภาพตามเสียงของคุณ: พูดบรรทัดใน say ตามลำดับ ครบทุกบรรทัด "
    "เป็นภาษาเดียวกับที่ลูกค้าพูด (ไทยใช้ th อังกฤษใช้ en ภาษาอื่นแปลจาก en) "
    "พูดชื่อเฉพาะและคำสำคัญให้ตรงตามบรรทัด เพราะภาพขยับตามคำเหล่านั้น "
    "ห้ามเพิ่มตัวเลขที่ไม่อยู่ในบรรทัด ห้ามพูดราคา ห้ามพูดว่า \"ไม่มีข้อมูล\" "
    "ห้ามบรรยายจอ เช่น \"ดูบนจอนะคะ\" นอกจากที่บรรทัดพูดเอง "
    "บรรทัดเหล่านี้คือคำตอบครบแล้ว ไม่ต้องเรียก search_condo_info สำหรับคำถามเดียวกัน "
    "พูดจบแล้วหยุดรอลูกค้า ห้ามเรียก present_story ซ้ำในคำตอบนี้"
)

#: The next question is a new question (robot log 14 Oct, owner's own run):
#: after the overview story asked "อยากเห็นชั้นไหน", "ขอเริ่มต้นจากชั้น
#: basement" was answered from memory with no picture -- the overview's
#: list was still in her context and "ห้ามเรียก present_story ซ้ำ" read
#: as a rule for the rest of the call. The story's own taps are the follow-
#: ups the designer expected; naming them, and quoting the reply she gave
#: instead, is the shape that reaches this model (CLAUDE.md "อ้างคำที่
#: โมเดลพูดผิดจริงๆ").
FOLLOW_UP = (
    "คำถามถัดไปของลูกค้าเป็นคำถามใหม่: ถ้าลูกค้าเลือกหรือถามต่อเรื่องใดข้างล่างนี้ (เช่น ตอบว่าอยากดูชั้นไหน) "
    "ให้เรียก present_story ของเรื่องนั้นก่อนพูดทุกครั้ง ห้ามเล่าเองจากที่เพิ่งพูดไป "
    "เช่น 'ชั้นใต้ดินของเรามีสิ่งอำนวยความสะดวกครบครันเลยค่ะ ทั้ง...' โดยไม่เรียก present_story: %s"
)


def follow_ups(story: dict, stories: dict) -> list[str]:
    """`label -> topic` for each tap of this story that leads to another
    offered story (the same rule as the buttons: what is not offered is not
    named)."""
    out = []
    for tap in story.get("taps") or []:
        nxt = tap.get("next_story")
        if isinstance(nxt, str) and nxt in stories and nxt != story.get("id"):
            out.append("%s -> %s" % (tap.get("label_th") or tap.get("label_en") or nxt, nxt))
    return out


#: Which story a search query asks for (robot log 14 Oct, owner's own run:
#: "How many parking space are there?" and "Is there a hospital nearby?" went
#: to search_condo_info -- whose description named "ที่จอดรถกี่คัน" and
#: "ห่างห้าง/โรงพยาบาลกี่กิโล" -- the screen showed a slide of the basement and
#: spa, and the repeats got no picture at all). search_condo_info hands such a
#: query back to present_story (app/tools/knowledge.py). A query matches a
#: story by the words of the story's own listed questions, each weighted by
#: how few stories share it; a query word no story uses counts against the
#: match, so "parking fee" is not the parking story. Asking words carry no
#: topic: "แนะนำโครงการหน่อย" matched the basement story by its "แนะนำสปา
#: หน่อย" until they were dropped. Tuned on the real catalog (51 stories)
#: against the 303 distinct queries the model sent in the logs of 12-14 Oct
#: and the guests' questions of every story case in the live eval: no match
#: named the wrong story.
QUERY_COVER = 0.75
QUERY_MARGIN = 0.2
_ASKING_WORDS = ("หน่อย", "ช่วย", "ขอ", "แนะนำ", "บอก", "เล่า", "อยาก", "รู้", "ดู", "ค่ะ", "คะ", "ครับ",
                 "นะ", "please", "tell", "me", "about", "show", "can", "could", "you", "i", "want",
                 "know", "to")
_QUERY_INDEX: dict = {"key": None}


def _query_words(text: str, story_side: bool = False) -> set[str]:
    from app.tools.retrieval import tokenize

    if _QUERY_INDEX.get("asking") is None:
        _QUERY_INDEX["asking"] = frozenset(t for w in _ASKING_WORDS for t in tokenize(w))
    out = set()
    for word in tokenize(text):
        if word in _QUERY_INDEX["asking"]:
            continue
        out.add(word)
        # the stories ask "Are there schools nearby?"; the model searches
        # "school nearby" -- the singular on the story's side only, so a
        # query's "units" still names the story that says "units"
        if (story_side and word.isascii() and word.isalpha() and len(word) > 3
                and word.endswith("s") and not word.endswith("ss")):
            out.add(word[:-1])
    return out


def story_for_query(query: object, offered: dict | None = None) -> str | None:
    """The one story `query` asks for, or None when no story clearly does."""
    if not isinstance(query, str) or not query.strip():
        return None
    offered = available() if offered is None else offered
    if len(offered) < 2:
        return None
    asks = tuple((sid, tuple((s.get("questions_th") or []) + (s.get("questions_en") or [])))
                 for sid, s in sorted(offered.items()))
    if _QUERY_INDEX["key"] != asks:
        docs = {sid: _query_words(" ".join(q for q in qs if isinstance(q, str)), story_side=True)
                for sid, qs in asks}
        _QUERY_INDEX.update(key=asks, docs=docs, df=Counter(w for ws in docs.values() for w in ws))
    docs, df = _QUERY_INDEX["docs"], _QUERY_INDEX["df"]
    words = _query_words(query)
    if not words:
        return None
    idf = {w: math.log((len(docs) + 1) / (df.get(w, 0) + 0.5)) for w in words}
    total = sum(idf.values())
    scored = sorted(((sum(idf[w] for w in words if w in ws) / total, sid) for sid, ws in docs.items()),
                    reverse=True)
    (best, sid), (second, _) = scored[0], scored[1]
    return sid if best >= QUERY_COVER and best - second >= QUERY_MARGIN else None


def tool_result(topic: object) -> dict:
    """The tool's answer: the page payload, the lines to say, the rules."""
    catalog = load_catalog()
    stories = available(catalog) if settings.stories_enabled else {}
    story = stories.get(topic) if isinstance(topic, str) else None
    if story is None:
        return {"ok": False, "error": "unknown_story",
                "instruction": "เรื่องนี้ขึ้นจอไม่ได้ ให้ตอบด้วยคำพูดตามข้อมูลโครงการแทน "
                               "ห้ามพูดว่าไม่มีข้อมูล ห้ามเรียก present_story ซ้ำ"}
    payload = page_payload(story, catalog, tap_ok=lambda tap: _tap_allowed(tap, stories))
    from app.knowledge_policy import state_instruction

    extra = state_instruction(str(catalog.get("content_state") or ""))
    from app import turnlog

    turnlog.record("story_shown", topic=story["id"], taps=len(payload["taps"]))
    nexts = follow_ups(story, stories)
    return {
        "ok": True,
        "screen": "story",
        "topic": story["id"],
        "story": payload,
        "say": [{"th": line["th"], "en": line["en"]} for line in story["lines"]],
        "instruction": INSTRUCTION + (" " + FOLLOW_UP % "; ".join(nexts) if nexts else "")
                       + (" " + extra if extra else ""),
    }


def _tap_allowed(tap: dict, stories: dict) -> bool:
    """A tap is drawn only if what it leads to exists for THIS session: the
    next story is offered, or the tool is one the model can see right now (a
    button that asks for a tool she cannot see is the refusing-model bug)."""
    if tap.get("next_story"):
        return tap["next_story"] in stories
    call = tap.get("tool") or {}
    from app.prompts import _tool_visible

    return call.get("name") in TAP_TOOLS and _tool_visible(call["name"])


def find_tap(topic: object, tap_id: object) -> dict | None:
    """The catalog's own tap behind an id, while its story is still offered."""
    if not isinstance(topic, str) or not isinstance(tap_id, str) or not settings.stories_enabled:
        return None
    stories = available()
    story = stories.get(topic)
    if story is None:
        return None
    for tap in story.get("taps") or []:
        if tap.get("id") == tap_id and _tap_allowed(tap, stories):
            return tap
    return None


def tap_text(tap: dict, lang: object = None) -> str:
    """The user turn a story tap becomes. The page sent an id; every word
    here is the catalog's (quick_asks' rule: a tap is marked as a tap, and
    the reply stays in the language the guest speaks)."""
    from app.quick_asks import _LANG_NAMES_TH, _lang

    if tap.get("next_story"):
        action = 'เรียก present_story(topic="%s") แล้วเล่าตามผลลัพธ์' % tap["next_story"]
    else:
        call = tap["tool"]
        args = ", ".join("%s=%s" % (k, json.dumps(v, ensure_ascii=False))
                         for k, v in sorted((call.get("args") or {}).items()))
        action = "เรียก %s(%s) แล้วเล่าตามผลลัพธ์" % (call["name"], args)
    return ("[ลูกค้าแตะปุ่ม \"%s\" บนจอ ไม่ได้พูด — ตอบเป็นภาษาเดียวกับที่ลูกค้าพูดล่าสุด "
            "ถ้ายังไม่ได้พูด ตอบเป็นภาษา%s] %s" % (tap["label_th"], _LANG_NAMES_TH[_lang(lang)], action))


def tap_ids(result: object) -> set[str]:
    """The tap ids a present_story result put on the page."""
    if not isinstance(result, dict):
        return set()
    story = result.get("story")
    taps = story.get("taps") if isinstance(story, dict) else None
    return {t["id"] for t in taps or [] if isinstance(t, dict) and isinstance(t.get("id"), str)}


def asset_body(sha256: str, catalog: dict | None = None, *,
               require_approval: bool = True, base: Path | None = None) -> tuple[bytes, str] | None:
    """(bytes, media type) behind /story-asset/<sha256>, re-hashed on every
    read: these are the bytes that leave the server. Only for a pinned
    picture of an approved catalog with the switch on (`require_approval`
    is for the dev harness, which never serves a guest)."""
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        return None
    if require_approval and not settings.stories_enabled:
        return None
    catalog = catalog if catalog is not None else load_catalog()
    if not catalog or (require_approval and not approved(catalog)):
        return None
    for asset_id, entry in (catalog.get("assets") or {}).items():
        if isinstance(entry, dict) and entry.get("sha256") == sha256:
            if asset_problem(asset_id, entry, base) is not None:
                return None
            try:
                body = ((base or assets_dir()) / entry["file"]).read_bytes()
            except OSError:
                return None
            if hashlib.sha256(body).hexdigest() != sha256:
                return None
            return body, FORMATS[entry["format"]]
    return None


# ---- review aid (build script) ------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def claim_numbers(story: dict) -> set[str]:
    """Every number a story would put on screen or in her mouth -- for the
    build script's "is each number in a cited source" report. Her lines, the
    labels and values in params, and the tap labels; never geometry or timing
    (positions, line indexes, frame times), ids or the owner's notes."""
    found: set[str] = set()

    def walk(value, key=""):
        if key in _GEOMETRY_KEYS or key in ("id", "asset", "frames", "mood_assets", "tap",
                                              "next_story", "tool"):
            return
        if key.endswith("_asset"):
            return
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            found.add(_plain(value))
        elif isinstance(value, str):
            for match in _NUMBER.findall(value):
                found.add(_plain(match.replace(",", "")))

    for line in story.get("lines") or []:
        if isinstance(line, dict):
            walk(line.get("th"))
            walk(line.get("en"))
    walk(story.get("params") or {})
    for tap in story.get("taps") or []:
        if isinstance(tap, dict):
            walk(tap.get("label_en"))
            walk(tap.get("label_th"))
    return found


def _plain(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else ("%g" % number)
