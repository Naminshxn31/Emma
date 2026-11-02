"""The fixed lines Emma may say offline, and only once a person approved them.

`data/projects/<project>/offline/lines.json`:

    {"source_id": "offline_lines", "project_id": "embassy_world", "schema": 1,
     "lines": [{"id", "kind", "th", "en", "approved_by", "approved_at",
                "approved_sha256"}, ...]}

The same rules as every other customer-facing text in this repo, because
offline there is no model left to obey them -- the line IS the answer:

* **A draft is never spoken or shown.** All lines start with
  `approved_by: null`; `scripts/approve_offline_lines.py --by NAME` signs
  them (a person runs it -- nothing here fills `approved_by`, the 6 Oct rule
  for the facts). The signature covers the words: `approved_sha256` is the
  digest of th + en at approval, so an edit after approval is a draft again
  (the data console's "แก้ = ถอนการอนุมัติ"), without anybody remembering to
  clear a field.
* **A line that breaks a content rule is never served, approved or not**
  (`problems`): no digits (every number belongs to the facts or a tool --
  SALES_HOST_BLOCK's rule), no "ไม่มีข้อมูล / ไม่ทราบ / ไม่มีในระบบ" (owner
  12 Oct: "อย่าให้เหมือนว่าหุ่นไม่มีข้อมูลนี้"), no "อยู่เองหรือลงทุน" (owner
  15 Oct), no "แนวคิดเบื้องต้น / อาจเปลี่ยนแปลง" (owner 10 Oct), no promise of
  calling staff (no such tool -- prompts.SALES_HOST_BLOCK), Thai ends polite
  (ค่ะ/คะ), no markdown (it would be read aloud).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger("condo_voice.offline.lines")

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "offline_lines"
SCHEMA = 1
LANGS = ("th", "en")

#: Every id the offline provider asks for. The file may hold more (a later
#: round), never fewer: a missing id is a silent branch of the router.
#: `invite` (review 26 Oct): her one project question for a guest greeting
#: her back or asking for a language again -- the thank-you line answered both.
IDS = ("offline_greeting", "not_heard", "menu", "handoff_commercial", "handoff_other",
       "off_topic", "invite", "ack")

_DIGIT = re.compile(r"[0-9๐-๙]")
_NO_INFO = re.compile(r"ไม่มีข้อมูล|ไม่ทราบ|ไม่มีในระบบ|ไม่รู้|no information|don'?t know|do not know|"
                      r"not in (?:the|our) system", re.IGNORECASE)
_LIVE_OR_INVEST = re.compile(r"อยู่เอง|ลงทุน|live in (?:it )?yourself|invest", re.IGNORECASE)
_HEDGE = re.compile(r"แนวคิดเบื้องต้น|อาจ(?:มีการ)?เปลี่ยนแปลง|preliminary|may change|subject to change",
                    re.IGNORECASE)
#: There is no tool that calls a salesperson over (prompts.SALES_HOST_BLOCK):
#: "contact the sales team at the gallery", never "I'll call someone".
_CALLS_STAFF = re.compile(r"เรียน?เชิญพนักงาน|เรียกพนักงาน|ตามพนักงาน|I(?:'ll| will) call|"
                          r"let me call|calling (?:a|our) (?:staff|sales)", re.IGNORECASE)
_MARKDOWN = re.compile(r"\*\*|__|`|^#|\|", re.MULTILINE)
_POLITE_TH = re.compile(r"(?:ค่ะ|คะ)")


def path(project_id: str | None = None) -> Path:
    from app.config import settings

    return ROOT / "data" / "projects" / (project_id or settings.project_id) / "offline" / "lines.json"


def norm(text: object) -> str:
    """What a digest and a clip key are taken over: NFC, whitespace folded."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(text or ""))).strip()


def digest(line: dict) -> str:
    """What an approval signs: the words, both languages."""
    body = norm(line.get("th")) + "\n" + norm(line.get("en"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def problems(line: object) -> list[str]:
    """Content-rule breaches; [] = this line may be said once approved."""
    if not isinstance(line, dict):
        return ["not_a_line"]
    out: list[str] = []
    if not isinstance(line.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,47}", line["id"]):
        out.append("bad_id")
    for lang in LANGS:
        text = line.get(lang)
        if not isinstance(text, str) or not norm(text):
            out.append("empty_%s" % lang)
            continue
        for name, rx in (("digit", _DIGIT), ("no_info", _NO_INFO), ("live_or_invest", _LIVE_OR_INVEST),
                         ("hedge", _HEDGE), ("calls_staff", _CALLS_STAFF), ("markdown", _MARKDOWN)):
            if rx.search(text):
                out.append("%s_%s" % (name, lang))
    th = line.get("th")
    if isinstance(th, str) and norm(th) and not _POLITE_TH.search(th):
        out.append("not_polite_th")
    return out


def approved(line: dict) -> bool:
    """Signed by a person, for these exact words."""
    return (isinstance(line.get("approved_by"), str) and bool(line["approved_by"].strip())
            and isinstance(line.get("approved_at"), str) and bool(line["approved_at"].strip())
            and line.get("approved_sha256") == digest(line))


_cache: tuple | None = None


def reset() -> None:
    global _cache
    _cache = None


def load(file: Path | None = None) -> dict | None:
    """The file as stored, or None (missing, unreadable, another project's)."""
    global _cache
    from app.config import settings

    file = file or path()
    try:
        stat = file.stat()
    except OSError:
        return None
    key = (str(file), stat.st_mtime_ns, stat.st_size, settings.project_id)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    data = None
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
        if (isinstance(raw, dict) and raw.get("source_id") == SOURCE_ID
                and raw.get("project_id") == settings.project_id
                and isinstance(raw.get("lines"), list)):
            data = raw
        else:
            logger.warning("offline lines file is not this project's: %s", file)
    except (OSError, ValueError):
        logger.warning("offline lines unreadable: %s", file)
    _cache = (key, data)
    return data


def servable(data: dict | None = None) -> dict[str, dict]:
    """id -> line, for the lines that may be said right now: approved for
    these words and clean of every content rule. Drafts are simply absent."""
    data = load() if data is None else data
    out: dict[str, dict] = {}
    for line in (data or {}).get("lines") or []:
        if not isinstance(line, dict) or not approved(line):
            continue
        bad = problems(line)
        if bad:
            logger.warning("offline line %s is approved but breaks %s -- not said",
                           line.get("id"), ",".join(bad))
            continue
        out[line["id"]] = line
    return out


def text(line_id: str, lang: str, data: dict | None = None) -> str | None:
    """The words of an approved line in `lang` (th / en; anything else = en:
    the stories have no other language either), or None."""
    line = servable(data).get(line_id)
    if line is None:
        return None
    return norm(line.get(lang if lang in LANGS else "en")) or None


def status(data: dict | None = None) -> dict:
    """Counts for a boot line and the docs' checklist: approved / draft / bad."""
    data = load() if data is None else data
    lines = [ln for ln in (data or {}).get("lines") or [] if isinstance(ln, dict)]
    ok = servable(data)
    return {"total": len(lines), "approved": len(ok),
            "draft": sum(1 for ln in lines if not approved(ln)),
            "missing": [i for i in IDS if i not in {ln.get("id") for ln in lines}]}
