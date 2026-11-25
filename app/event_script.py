"""The launch ceremony's script: Emma's lines, and only once a person approved them.

The owner sent the ceremony script on 6 Nov 2026 (คุณมิกิ asks, Emma answers, nine
scenes) and asked for both ways of saying it: word for word when staff press a cue,
and live when the host just talks to her. This module is the data under both -- the
words, their approval, and the rules a line must pass. Playing a cue, the live
prompt block and the cue tool build on it; nothing here speaks.

CEREMONY_FILE (data/ceremony/floor1_ceremony.json, tracked -- its own file since 9 Nov, when
the ceremony became an app of its own, "Emma Ceremony", app/ceremony.py; it was
data/events/floor1_event.script.json beside the event file before, and the format, the cues
and their approvals moved unchanged):

    {"format": "emma-event-script-v1", "host_th": "คุณมิกิ", "replies": "cues" | "named",
     "speak": "th" | "en",
     "cues": [{"id", "scene", "scene_th", "host_th", "th", "en", "live_th",
               "numbers": [{"say", "source", "as", "factor"?}], "clip"?, "note_th",
               "approved_by", "approved_at", "approved_sha256"}, ...]}

The rules are the offline lines' (app/offline/lines.py), because a cue line is said
verbatim with no model left to obey anything -- the line IS the answer:

* **A draft is never said.** `approved_by` starts null; `scripts/approve_event_script.py
  --by NAME` signs (a person runs it, nothing here fills `approved_by` -- the 6 Oct
  rule). The signature is over the words AND their number sources (`digest`), so an
  edit afterwards is a draft again without anybody remembering to clear a field.
* **Every number has an owner.** The offline lines carry no digits at all; a ceremony
  line is mostly numbers (1,066 units, 155 m, 1.3 m deep). So each number in `th`
  must sit inside a `numbers[].say`, and that row names an approved source and the
  exact text there (`as`) whose numbers it repeats -- `factor` for a unit change
  ("600 เมตร" from "0.6 กม.", factor 1000). The source text is the SERVED text:
  the approved facts as the prompt renders them, a report chapter only while its
  pin still matches (cleaned of money lines and deny_terms, as project_report
  serves it), an approved story's lines. The script's own "500 เมตร" has no such
  row, and that is the point.
* **`speak` = the language the cues are SAID in** (owner 17 Nov: "Emma เป็นอังกฤษพร้อมซับไทยจะเอา
  ขึ้นจอ เสียง eng"). Absent = "th": every file written before it behaves as it did. "en": the
  cue's `en` is what she says (read verbatim, or its own recorded clip) and `th` is the subtitle
  the show team puts on the screen. The approval always covered both texts (`digest`), so an
  English line added or edited after a signature is a draft again; a cue with no `en` is not
  servable under "en". The number rule covers the English too: it must say exactly the numbers
  the Thai line says, in digits or in English words -- no more, no fewer (`en_number_problems`).
* **A line that breaks a content rule is never served, approved or not** (`problems`):
  "ไม่มีข้อมูล", "อยู่เองหรือลงทุน", "แนวคิดเบื้องต้น", promises to call staff,
  markdown, an overruled term (retrieval.OVERRULED), Thai without a polite ending.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

from app.offline.lines import (_CALLS_STAFF, _HEDGE, _LIVE_OR_INVEST, _MARKDOWN,  # noqa: F401
                               _NO_INFO, norm)

logger = logging.getLogger("condo_voice.event_script")

ROOT = Path(__file__).resolve().parent.parent
FORMAT = "emma-event-script-v1"
LANGS = ("th", "en")

#: A number as it is written in a line: 1,066 / 1.3 / 155 / the 3 of "3rd".
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: A number written in Thai words. Review 6 Nov: "สระยาวห้าร้อยเมตร และมีหนึ่งหมื่นยูนิต"
#: passed a digits-only rule clean. Matched per WORD (the tokenizer's), never as a
#: substring -- "สามารถ", "แฟนพันธุ์แท้", "ห้างสรรพสินค้า" carry สาม/พัน/ห้า inside other
#: words (the ราคา-in-อาคาร bug, CLAUDE.md). A token that is only part numeral
#: ("สายสอง", "ที่หนึ่ง") is a name or an ordinal and is not read as a figure.
_TH_DIGITS = {"ศูนย์": 0, "หนึ่ง": 1, "เอ็ด": 1, "สอง": 2, "ยี่": 2, "สาม": 3, "สี่": 4, "ห้า": 5,
              "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9}
_TH_TENS = {"สิบ": 10, "ร้อย": 100, "พัน": 1000, "หมื่น": 10000, "แสน": 100000, "ล้าน": 1000000}
_TH_NUMERAL = re.compile("(?:%s)+" % "|".join(sorted(list(_TH_DIGITS) + list(_TH_TENS), key=len, reverse=True)))
#: A number written in English words ("six hundred", "one point three", "twenty-three"). An
#: ordinal (first, second, third) is not a figure: "Jomtien Second Road", "first place".
_EN_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_EN_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_EN_SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
_EN_WORD = re.compile(r"[A-Za-z]+")
#: A spelled ordinal is a figure too ("on the eighth floor" is floor 8): review 17 Nov, the
#: first reader skipped them and "the fifteenth floor" passed with no number behind it. An
#: idiom that is no figure ("first place", "Jomtien Second Road") is listed in the cue's
#: `words_ok`, as the Thai side lists "ร้อยเปอร์เซ็นต์".
_EN_ORDINALS = {w: i + 1 for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth "
    "fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split())}
_EN_ORDINALS.update({"thirtieth": 30, "fortieth": 40, "fiftieth": 50, "sixtieth": 60, "seventieth": 70,
                     "eightieth": 80, "ninetieth": 90, "hundredth": 100, "thousandth": 1000})
#: Words that say an amount without a figure. Nobody sourced "hundreds of units" or "twice
#: the size": flagged unless the cue lists the phrase in `words_ok`.
_EN_QUANTITY = re.compile(r"\b(?:hundreds|thousands|millions|billions|dozens?|half|halves|twice|thrice|double[ds]?|"
                          r"triple[ds]?|quarters?|couple|pair)\b", re.I)
#: English for what the owner cut from the project (retrieval.OVERRULED has "rooftop"): a
#: usable roof by another name. Checked on a cue's English only -- the shared pattern cleans
#: approved project text and is not widened from here.
_EN_OVERRULED = re.compile(r"roof\s*-?\s*(?:deck|terrace|garden|bar|pool)|sky\s*-?\s*(?:deck|terrace|garden)", re.I)
#: A unit change a number row may make, and nothing else (review: factor 600/23
#: turned "23 เมตร" into "600 ตารางเมตร"): metres <-> kilometres, centimetres.
FACTORS = (1, 100, 1000, 0.01, 0.001)
#: Thai polite ending, allowing the host's name after it ("...เลยค่ะคุณมิกิ").
_POLITE_TH = re.compile(r"(?:ค่ะ|คะ)(?:\s*คุณ\S{0,16})?\s*[!?.…]*\s*$")
_ID = re.compile(r"[a-z][a-z0-9_]{1,47}")


def path() -> Path | None:
    """CEREMONY_FILE (relative to the repo); None when it is empty (conftest, a machine with no
    ceremony). Not "<event stem>.script.json" any more (9 Nov): the ceremony's own file, which
    names the event file's spots and films by id but is not one of its parts."""
    from app.config import settings

    name = (settings.ceremony_file or "").strip()
    if not name:
        return None
    target = Path(name)
    return target if target.is_absolute() else ROOT / target


_cache: dict = {"key": None, "doc": None}


def reset() -> None:
    _cache.update(key=None, doc=None)


def load(file: Path | None = None) -> dict | None:
    """The raw script file, cached on its stat; None = no file or unreadable."""
    target = file if file is not None else path()
    if target is None:
        return None
    try:
        st = target.stat()
        key = (str(target), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if file is None and _cache["key"] == key:
        return _cache["doc"]
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("event script %s could not be read", target)
        doc = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        if doc is not None:
            logger.warning("event script %s is not %s -- no cues", target, FORMAT)
        doc = None
    if file is None:
        _cache.update(key=key, doc=doc)
    return doc


def _text(value) -> str | None:
    value = norm(value) if isinstance(value, str) else ""
    return value or None


def _normalise(raw: dict) -> dict:
    numbers = []
    for row in raw.get("numbers") or []:
        if isinstance(row, dict):
            factor = row.get("factor", 1)
            numbers.append({"say": _text(row.get("say")), "source": _text(row.get("source")),
                            "as": _text(row.get("as")),
                            "factor": factor if isinstance(factor, (int, float)) and not isinstance(factor, bool) else None})
    scene = raw.get("scene")
    words_ok = [w for w in (_text(x) for x in raw.get("words_ok") or [] if isinstance(x, str)) if w]
    return {"id": raw.get("id"), "scene": scene if isinstance(scene, int) and not isinstance(scene, bool) else None,
            "scene_th": _text(raw.get("scene_th")), "host_th": _text(raw.get("host_th")),
            "th": _text(raw.get("th")), "en": _text(raw.get("en")), "live_th": _text(raw.get("live_th")),
            "numbers": numbers, "words_ok": words_ok, "clip": _text(raw.get("clip")),
            "note_th": _text(raw.get("note_th")),
            "approved_by": raw.get("approved_by"), "approved_at": raw.get("approved_at"),
            "approved_sha256": raw.get("approved_sha256"),
            # The clip the approver listened to (review 6 Nov: an approval over the words
            # alone let a later --fresh render swap the stage audio unheard). Not in the
            # digest: re-rendering must not turn the WORDS back into a draft -- it turns
            # the recording back into "not listened to", and the cue is read live instead.
            "approved_voice_sha256": raw.get("approved_voice_sha256")}


def cues(doc: dict | None = None) -> list[dict]:
    """Every cue in file order, normalised; ids unique (a repeat is dropped, logged)."""
    doc = doc if doc is not None else load()
    out, seen = [], set()
    for raw in (doc or {}).get("cues") or []:
        if not isinstance(raw, dict):
            continue
        cue = _normalise(raw)
        if cue["id"] in seen:
            logger.warning("event script: cue id %r appears twice -- the second is dropped", cue["id"])
            continue
        seen.add(cue["id"])
        out.append(cue)
    return out


def cue(cue_id, doc: dict | None = None) -> dict | None:
    for c in cues(doc):
        if c["id"] == cue_id:
            return c
    return None


def digest(c: dict) -> str:
    """What an approval signs: the words in both languages, the live brief, the
    number rows and the clip -- every part that decides what the room hears."""
    body = {"th": norm(c.get("th")), "en": norm(c.get("en")), "live_th": norm(c.get("live_th")),
            "numbers": [{k: row.get(k) for k in ("say", "source", "as", "factor")}
                        for row in c.get("numbers") or [] if isinstance(row, dict)],
            "clip": c.get("clip") or None}
    if c.get("words_ok"):           # absent = the digest of a cue written before this field
        body["words_ok"] = [norm(w) for w in c["words_ok"]]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def approved(c: dict) -> bool:
    """Signed by a person, for this exact cue."""
    return (isinstance(c.get("approved_by"), str) and bool(c["approved_by"].strip())
            and isinstance(c.get("approved_at"), str) and bool(c["approved_at"].strip())
            and c.get("approved_sha256") == digest(c))


# ---- where a number may come from ----------------------------------------------------

#: source_text answers for SOURCE_CACHE_S. Rehearsal 24 Nov 11:00: once the cues were signed, every servable() --
#: and the staff page's every poll reaches it -- rendered the whole facts file (a "knowledge policy" log line per
#: fact) and re-hashed report chapters for each number row of each of 29 cues: the robot's server log was 99% those
#: lines and the event loop stopped answering (/ceremony/state > 30 s). A source changes by a person's approval,
#: not between two polls; a short cache keeps that true and the loop free.
SOURCE_CACHE_S = 300.0   # (review C 24 Nov: 30 s met ceremony._served's refresh every other time; files change by deploy)
_source_cache: dict[str, tuple[float, str | None]] = {}


def source_text(name: str | None) -> str | None:
    """The served text of one approved source, or None (unknown, unapproved, changed)."""
    if not name:
        return None
    hit = _source_cache.get(name)
    now = time.monotonic()
    if hit is not None and now - hit[0] < SOURCE_CACHE_S:
        return hit[1]
    text = _source_text(name)
    _source_cache[name] = (now, text)
    return text


def _source_text(name: str) -> str | None:
    if name == "facts":
        from app import prompts

        text = prompts.load_facts()
        return None if text == prompts.FALLBACK_FACTS else text
    if name.startswith("report:"):
        return _report_chapter(name.split(":", 1)[1])
    if name.startswith("story:"):
        from app import stories

        story = stories.available().get(name.split(":", 1)[1])
        if not isinstance(story, dict):
            return None
        return "\n".join(str(line.get(lang) or "") for line in story.get("lines") or []
                         if isinstance(line, dict) for lang in LANGS)
    return None


def _report_chapter(file_name: str) -> str | None:
    """One chapter the way project_report serves it: only while its sha256 still
    matches the approval's pin, cleaned of money lines and deny_terms."""
    from app import project_report

    manifest = project_report.load_manifest()
    if not project_report.approved(manifest) or "/" in file_name or "\\" in file_name or ".." in file_name:
        return None
    pin = (manifest.get("files") or {}).get(file_name)
    folder = ROOT / str(manifest.get("folder") or "")
    target = folder / file_name
    try:
        if not pin or not target.is_file() or project_report._sha256(target) != pin:
            return None
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    deny = [str(t) for t in manifest.get("deny_terms") or []]
    return project_report.clean(text, deny, {})


def _thai_value(word: str) -> float | None:
    """หนึ่งร้อยห้าสิบห้า -> 155; None when the run is not one readable number."""
    total, cur = 0, None
    for part in re.findall("|".join(sorted(list(_TH_DIGITS) + list(_TH_TENS), key=len, reverse=True)), word):
        if part in _TH_DIGITS:
            if cur is not None:
                return None                     # two digits in a row: not one number
            cur = _TH_DIGITS[part]
        else:
            total += (1 if cur is None else cur) * _TH_TENS[part]
            cur = None
    return float(total + (cur or 0))


_stock: dict = {}


def _segment(text: str) -> list[str]:
    """Thai words with the STOCK dictionary, every piece kept (they concatenate back
    to `text`, so offsets are exact).

    Not retrieval.tokenize: that one is taught the deck's and the stories' words when
    their indexes build (`init_tokeniser(vocabulary)`), and the eval of 7 Nov 00:12
    caught the consequence -- "ชั้นสาม" was ชั้น|สาม at start-up and one word once
    an index had loaded, so s3_facility's number row stopped matching mid-run and
    the cue dropped out of servable(). Whether a line may be said must not depend
    on which index happened to load first."""
    if "trie" not in _stock:
        try:
            from pythainlp.corpus.common import thai_words
            from pythainlp.util import dict_trie

            _stock["trie"] = dict_trie(dict_source=set(thai_words()))
        except Exception:
            logger.warning("pythainlp unavailable: numbers in Thai words are read by pattern only")
            _stock["trie"] = None
    if _stock["trie"] is None:
        return re.split(r"(%s)" % _TH_NUMERAL.pattern, text)
    from pythainlp.tokenize import word_tokenize

    return word_tokenize(text, custom_dict=_stock["trie"], engine="newmm", keep_whitespace=True)


def thai_numerals(text: str | None) -> list[tuple[int, int, str]]:
    """(start, end, word) for every word that is a number in Thai words."""
    text = text or ""
    out, pos = [], 0
    for tok in _segment(text):
        at, pos = pos, pos + len(tok)
        if not tok or not _TH_NUMERAL.fullmatch(tok):
            continue
        if out and out[-1][1] == at:            # หนึ่ง|ร้อยห้าสิบ|ห้า is one number, 155
            start, _end, word = out.pop()
            out.append((start, pos, word + tok))
        else:
            out.append((at, pos, tok))
    return out


def _values(text: str | None) -> list[float]:
    out = []
    for m in _NUMBER.finditer(text or ""):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    for _s, _e, word in thai_numerals(text):
        value = _thai_value(word)
        if value is not None:
            out.append(value)
    return out


def _english_value(words: list[str]) -> float | None:
    """["one", "thousand", "sixty", "six"] -> 1066; ["one", "point", "three"] -> 1.3;
    None when the run is not one readable number."""
    if "point" in words:
        at = words.index("point")
        whole, frac = _english_value(words[:at]) if at else 0.0, words[at + 1:]
        if whole is None or not frac or any(w not in _EN_UNITS or _EN_UNITS[w] > 9 for w in frac):
            return None
        return float("%d.%s" % (whole, "".join(str(_EN_UNITS[w]) for w in frac)))
    total, cur, seen = 0, 0, False
    for w in words:
        if w in _EN_UNITS:
            cur += _EN_UNITS[w]
        elif w in _EN_TENS:
            cur += _EN_TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in _EN_SCALES:
            total += (cur or 1) * _EN_SCALES[w]
            cur = 0
        else:
            return None
        seen = True
    return float(total + cur) if seen else None


def english_numerals(text: str | None) -> list[tuple[int, int, float]]:
    """(start, end, value) for every number written in English words. Words run together
    over spaces and hyphens ("twenty-three", "one thousand sixty-six"); an "and" inside a
    number ("one hundred and fifty-five") and a "point" before digits-in-words belong to it."""
    text = text or ""
    words = [(m.start(), m.end(), m.group(0).lower()) for m in _EN_WORD.finditer(text)]
    number = lambda w: w in _EN_UNITS or w in _EN_TENS or w in _EN_SCALES      # noqa: E731
    out, i = [], 0
    while i < len(words):
        if words[i][2] in _EN_ORDINALS:                  # "eighth" alone
            out.append((words[i][0], words[i][1], float(_EN_ORDINALS[words[i][2]])))
            i += 1
            continue
        if not number(words[i][2]):
            i += 1
            continue
        run, j = [words[i]], i + 1
        while j < len(words):
            gap = text[run[-1][1]:words[j][0]]
            if gap.strip(" -") != "":
                break
            w = words[j][2]
            if number(w):
                run.append(words[j])
            elif w in ("and", "point") and j + 1 < len(words) and number(words[j + 1][2]) \
                    and text[words[j][1]:words[j + 1][0]].strip(" -") == "":
                if w == "point":
                    run.append(words[j])
                j += 1
                run.append(words[j])
            else:
                break
            j += 1
        value = _english_value([w for _s, _e, w in run])
        # "twenty-first": the cardinal run and the ordinal that closes it are one figure
        if value is not None and j < len(words) and words[j][2] in _EN_ORDINALS \
                and text[run[-1][1]:words[j][0]].strip(" -") == "" and _EN_ORDINALS[words[j][2]] < 10 \
                and run[-1][2] in _EN_TENS:
            value += _EN_ORDINALS[words[j][2]]
            run.append(words[j])
            j += 1
        if value is not None:
            out.append((run[0][0], run[-1][1], value))
        i = j
    return out


def _masked(text: str | None, phrases) -> str:
    """`text` with every `words_ok` phrase blanked: those are not figures in either language."""
    text = text or ""
    for phrase in phrases or []:
        text = re.sub(re.escape(phrase), lambda m: " " * len(m.group(0)), text, flags=re.I)
    return text


def en_number_problems(c: dict) -> list[str]:
    """[] = the English line says exactly the numbers the Thai line says (in digits, in
    English words or as a spelled ordinal): the rows that own the Thai numbers own these too.
    A number only in the English is a figure nobody sourced; one only in the Thai is a
    subtitle saying more than she does; a quantity word ("hundreds of", "twice", "half") is
    an amount with no figure and no source. A phrase that only looks like a number ("first
    place") is the approver's to list in `words_ok`.

    What this does NOT judge: what a number is attached to. "600 feet" for "600 เมตร", or the
    right figure on the wrong thing, passes here -- that is the person approving the line."""
    en = _masked(c.get("en"), c.get("words_ok"))
    if not en.strip():
        return []
    said = [float(m.group(0).replace(",", "")) for m in _NUMBER.finditer(en)
            if m.group(0).replace(",", "").replace(".", "").isdigit()]
    said += [v for _s, _e, v in english_numerals(en)]
    want = _values(_masked(c.get("th"), c.get("words_ok")))
    out, left = [], list(want)
    for v in said:
        hit = next((w for w in left if abs(w - v) < 1e-6), None)
        if hit is None:
            out.append("en_number_extra:%g" % v)
        else:
            left.remove(hit)
    out.extend("en_number_missing:%g" % w for w in left)
    out.extend("en_quantity_word:%s" % m.group(0).lower() for m in _EN_QUANTITY.finditer(en))
    return out


def _flat(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _bounded(hay: str, needle: str) -> list[int]:
    """Where `needle` occurs in `hay` without a digit run going on either side:
    "8 ชั้น" is not in "18 ชั้น", "1.3 เมตร" not in "11.3 เมตร" (review 6 Nov --
    a bare substring let both through)."""
    out = []
    for m in re.finditer(re.escape(needle), hay):
        before = hay[:m.start()]
        after = hay[m.end():]
        if needle[:1].isdigit() and re.search(r"\d[.,]?$", before):
            continue
        if needle[-1:].isdigit() and re.match(r"[.,]?\d", after):
            continue
        out.append(m.start())
    return out


def number_problems(c: dict, *, lookup=source_text) -> list[str]:
    """[] = every number in `th`, in digits or in Thai words, sits inside a declared
    row whose source says it, or inside a phrase the approver listed in `words_ok`
    as not a figure ("ร้อยเปอร์เซ็นต์")."""
    th = c.get("th") or ""
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    for i, row in enumerate(c.get("numbers") or []):
        say, src, as_text, factor = row.get("say"), row.get("source"), row.get("as"), row.get("factor")
        if not say or not src or not as_text or factor is None:
            out.append("number_row_%d_incomplete" % i)
            continue
        if not any(abs(factor - f) < 1e-12 for f in FACTORS):
            out.append("number_row_%d_bad_factor" % i)
            continue
        found = _bounded(th, say)
        if not found:
            out.append("number_row_%d_not_in_line" % i)
        spans.extend((s, s + len(say)) for s in found)
        text = lookup(src)
        if text is None:
            out.append("number_row_%d_source_unavailable" % i)
        elif not _bounded(_flat(text), _flat(as_text)):
            out.append("number_row_%d_not_in_source" % i)
        said, quoted = sorted(_values(say)), sorted(v * factor for v in _values(as_text))
        if not said or len(said) != len(quoted) or any(abs(a - b) > 1e-6 for a, b in zip(said, quoted)):
            out.append("number_row_%d_mismatch" % i)
    for phrase in c.get("words_ok") or []:
        spans.extend((s, s + len(phrase)) for s in (m.start() for m in re.finditer(re.escape(phrase), th)))
    for m in _NUMBER.finditer(th):
        if not any(s <= m.start() and m.end() <= e for s, e in spans):
            out.append("unsourced_number:%s" % m.group(0))
    for start, end, word in thai_numerals(th):
        if not any(s <= start and end <= e for s, e in spans):
            out.append("unsourced_number:%s" % word)
    return out


def clip_problems(c: dict) -> list[str]:
    """A cue that rolls a film needs the film (review 6 Nov: s8 invited the room to a
    video that did not exist -- the line played, then clip_error)."""
    if not c.get("clip"):
        return []
    from app import event_clips

    entry = event_clips.table().get(c["clip"])
    if entry is None:
        return ["clip_unknown"]
    return [] if entry.get("has_file") else ["clip_missing"]


def speak(doc: dict | None = None) -> str:
    """The language this file's cues are said in: "th" unless the file says "en"."""
    doc = doc if doc is not None else load()
    lang = (doc or {}).get("speak")
    return lang if lang in LANGS else "th"


def en_problems(c: dict) -> list[str]:
    """What is wrong with the cue's English as a line to SAY: its numbers against the Thai,
    and the English names of what the owner cut. [] when there is no English."""
    en = c.get("en") or ""
    if not en:
        return []
    out = en_number_problems(c)
    if _EN_OVERRULED.search(_masked(en, c.get("words_ok"))):
        out.append("overruled_en")
    return out


def problems(c: object, *, lookup=source_text, speak: str = "th") -> list[str]:
    """Content-rule breaches; [] = this cue may be used once approved. `speak` = the file's
    spoken language: under "en" a line with no English has nothing to say."""
    if not isinstance(c, dict):
        return ["not_a_cue"]
    out: list[str] = []
    if not isinstance(c.get("id"), str) or not _ID.fullmatch(c["id"]):
        out.append("bad_id")
    if not c.get("th") and not c.get("live_th"):
        out.append("empty")
    for field in ("th", "en", "live_th"):
        text = c.get(field)
        if not text:
            continue
        for name, rx in (("no_info", _NO_INFO), ("live_or_invest", _LIVE_OR_INVEST), ("hedge", _HEDGE),
                         ("calls_staff", _CALLS_STAFF), ("markdown", _MARKDOWN)):
            if rx.search(text):
                out.append("%s_%s" % (name, field))
        from app.tools.retrieval import OVERRULED

        if OVERRULED.search(text):
            out.append("overruled_%s" % field)
    if c.get("th") and not _POLITE_TH.search(c["th"]):
        out.append("not_polite_th")
    # The English is judged as a line only where it is SAID. In a Thai-spoken file an `en`
    # nobody will hear must not cost the room its Thai line (review 17 Nov) -- `en_problems`
    # still names what is wrong with it for the status listing.
    if speak == "en":
        out.extend(en_problems(c))
        if c.get("th") and not c.get("en"):
            out.append("no_en_line")
    if c.get("th"):
        out.extend(number_problems(c, lookup=lookup))
    elif c.get("numbers"):
        out.append("numbers_without_line")
    out.extend(clip_problems(c))
    return out


def _problems_in(c: dict, lang: str) -> list[str]:
    """`problems` for a file spoken in `lang`. Called the old way for "th": every file written
    before `speak` (and every test that stands in for `problems`) sees the call it always saw."""
    return problems(c) if lang == "th" else problems(c, speak=lang)


def servable(doc: dict | None = None, *, drafts: bool = False) -> dict[str, dict]:
    """id -> cue, for the cues that may be used right now: approved and clean.

    `drafts=True` drops the approval (never the content rules) and exists for
    the measuring scripts only -- an eval of a line the owner has not signed yet,
    a preview render of its voice. Nothing under app/ passes it (a test reads
    the source): on the stage a draft is never said."""
    doc = doc if doc is not None else load()
    lang = speak(doc)
    return {c["id"]: c for c in cues(doc) if (drafts or approved(c)) and not _problems_in(c, lang)}


def status(doc: dict | None = None) -> list[dict]:
    """One row per cue for the staff page and the CLI: what it is and why not."""
    rows = []
    doc = doc if doc is not None else load()
    lang = speak(doc)
    for c in cues(doc):
        probs = _problems_in(c, lang)
        rows.append({"id": c["id"], "scene": c["scene"], "scene_th": c["scene_th"],
                     "host_th": c["host_th"], "th": c["th"], "live_th": c["live_th"],
                     **({"en": c["en"], "speak": "en"} if lang == "en" else {}),
                     # an English nobody says (a Thai-spoken file): reported, never blocking
                     **({"en_problems": en_problems(c)} if lang != "en" and c.get("en") and en_problems(c) else {}),
                     "kind": "line" if c["th"] else ("live" if c["live_th"] else "empty"),
                     "clip": c["clip"], "approved": approved(c), "problems": probs,
                     "ready": approved(c) and not probs})
    return rows


#: What the live session gets when staff press a cue that has no recorded voice:
#: the words, quoted, as the whole turn. Quoting is the point here -- this repo
#: measured that a sentence quoted in an instruction is a sentence she says word
#: for word (CLAUDE.md, 27 Oct); for a cue that is exactly what is wanted.
READ_RULE = ("[คิวบทพิธี — ทีมงานกดให้พูดบทนี้ ไม่ใช่คำพูดของแขก] พูดประโยคในเครื่องหมาย « » "
             "ออกเสียงตามตัวอักษรทุกคำเป็นภาษาไทย ห้ามเติม ห้ามตัด ห้ามแปล ห้ามพูดอย่างอื่นก่อนหรือหลัง "
             "ห้ามเรียกเครื่องมือ: «%s»")


#: The same turn for a line said in English (`speak`: "en"). Written IN English: a Thai
#: instruction round an English sentence is two languages in one turn, and the rule that
#: measured best here is the one that looks like what she is about to say.
READ_RULE_EN = ("[Ceremony cue: staff pressed this line. It is not something a guest said.] Say the sentence "
                "inside « » aloud in English, exactly as written, every word. Do not add anything, do not "
                "leave anything out, do not translate it, say nothing before or after it, and call no "
                "tool: «%s»")


def read_text(c: dict, lang: str = "th") -> str | None:
    """The turn that asks the live session to say this cue's line, or None."""
    line = c.get(lang) if lang in LANGS else None
    if not line:
        return None
    return (READ_RULE_EN if lang == "en" else READ_RULE) % line


def spoken(c: dict, doc: dict | None = None) -> dict:
    """The cue as the player says it: `th` is the line that goes out and `lang` its language
    (event_cues' own convention, `play_line`). Under "en" that is the English, with the Thai
    kept as `sub_th`; the cue's own fields (`en`, the approval, the clip pins) stay."""
    if speak(doc) != "en" or not c.get("en"):
        return c
    return {**c, "sub_th": c.get("th"), "th": c["en"], "lang": "en"}


# ---- the running order (owner 7 Nov: "ทำเป็นแบบกดสั่งด้วยนะ พวกซีนต่างๆ") -------------------
#
# The script's nine scenes as steps staff press one by one on the phone, following the
# host (app/event_order.py runs them). `order` is a list of {"n", "title_th", "steps"};
# a step is {"do": <kind>, <its one id>, "label_th"?, "note_th"?}. Every step names an
# id -- a cue, a spot, a clip, a gesture, open/close, an app -- never words: the
# `show_unit(room)` rule again, now for the press. The order is not part of a cue's approval
# (`digest`): reordering never turns a line back into a draft, and a draft is still never said.
#
# No `scene` step since 9 Nov (owner: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน"): the scenes are
# the Emma Event app's, and opening the ceremony app is what starts the ceremony. In their
# place, `screen` (the big screen, the same code as a scene's `tv`) and `handoff` (the robot
# face opens the Emma Event app for the after-part). A file that still has one is flagged.

#: kind -> the fields it carries besides do/label_th/note_th. `show` and `chain` are the cue's
#: optional ones: an approved story drawn on the face while she says the line, and `"chain": true`
#: -- the next step of the same scene (a worded line too) is pressed by itself the moment this
#: line has been said (owner 23 Nov, the two jokes: "พูดต่อเนื่องเลย ไม่ต้องเว้นช่วง"; event_order).
ORDER_KINDS = {"screen": ("screen",), "walk": ("spot",), "body": ("action",),
               "cue": ("cue", "show", "chain"), "clip": ("clip",), "handoff": ("to",),
               # a staff tick; `"in_box": true` on the one that says she is in the box -- it arms the opening's one
               # walk out and sets the arm latch (app/event_box.py, second review 23 Nov), before the opening
               "manual": ("in_box",),
               # the venue's lights / curtains (app/venue_devices.py, owner 20 Nov): placed BEFORE the
               # staff tick that confirms the room changed -- sent is not done. Not in the order at
               # all while this machine does not drive that device (event_order hides it).
               "device": ("device", "action"),
               # the ceremony's opening (owner 23 Nov: "ช่วงพิธีอยู่แอปพิธีทั้งหมด"): card 1 of the Event page
               # -- the screen's countdown, the walk out of the box on its hit, the wave and the line, then
               # the counter and the guests (app/event_tour.py `begin_ceremony`) -- as ONE step. `scene`
               # names the Event file's scene it is (its walk, its recorded line, its tour).
               "opening": ("scene",),
               # card 2 (owner 24 Nov, "ทั้งพิธีคุยสด ไม่ใช้บทกด"; app/ceremony_follow.py): turns the whole-ceremony-live
               # mode on for the rest of the order. Not in the order at all while CEREMONY_FOLLOW_HOST is off
               # (event_order hides it, as it hides a device step) -- and no cue's digest is touched by it.
               "follow": ()}
#: What a `screen` step may ask, and the apps a `handoff` may open (app/ceremony.py APPS).
SCREEN_WANTS = ("open", "close")
HANDOFF_APPS = ("event",)
_ORDER_TEXT = ("label_th", "note_th")
_ORDER_IDS = ("screen", "spot", "action", "cue", "show", "clip", "to", "device")


def order(doc: dict | None = None) -> list[dict] | None:
    """The running order, normalised: every step of the file kept at its own index `i`
    (a press names (n, i), and a step the file got wrong must not shift the ones after
    it), its ids as written. None = the file has no order."""
    doc = doc if doc is not None else load()
    raw = (doc or {}).get("order")
    if not isinstance(raw, list):
        return None
    out = []
    for scene in raw:
        if not isinstance(scene, dict):
            continue
        n = scene.get("n")
        steps = []
        for i, step in enumerate(scene.get("steps") if isinstance(scene.get("steps"), list) else []):
            step = step if isinstance(step, dict) else {}
            do = step.get("do")
            row = {"i": i, "do": do if do in ORDER_KINDS else None}
            for key in _ORDER_TEXT:
                row[key] = _text(step.get(key))
            for key in _ORDER_IDS:
                value = step.get(key)
                row[key] = value.strip() if isinstance(value, str) and value.strip() else None
            if do == "manual" and step.get("in_box") is True:
                row["in_box"] = True                     # only where written (the order's digest, its snapshot)
            if do == "cue" and step.get("chain") is True:
                # only where it is written: an order with no chain normalises as before (its digest,
                # the kept progress of event_order, the 20 Nov snapshot of the order)
                row["chain"] = True
            if do == "opening":
                # the same: only on its own step (`_ORDER_IDS` would add a `scene` to every row)
                scene_id = step.get("scene")
                row["scene"] = scene_id.strip() if isinstance(scene_id, str) and scene_id.strip() else None
            steps.append(row)
        out.append({"n": n if isinstance(n, int) and not isinstance(n, bool) else None,
                    "title_th": _text(scene.get("title_th")), "steps": steps})
    return out


def _story_ids() -> set[str] | None:
    """The stories a `show` may name, or None when they are off on this machine (the
    step's readiness says so instead -- a PC without STORIES_ENABLED must still be able
    to check the file)."""
    from app.config import settings

    if not settings.stories_enabled:
        return None
    from app import stories

    return set(stories.available())


def order_problems(doc: dict | None = None, event: dict | None = None) -> list[str]:
    """[] = every step of the order names something that exists, in the right kind.
    `event` = the event file as app/event_host.load() gives it (its spots and clips, which
    the ceremony shares); the script's own cues are `doc`'s."""
    from app import robot_contracts as rc

    doc = doc if doc is not None else load()
    raw = (doc or {}).get("order")
    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["order_not_a_list"]
    if event is None:
        from app import event_host

        event = event_host.load() or {}
    spots = event.get("spots") or {}
    clips = event.get("clips") or {}
    scenes = {s.get("id"): s for s in event.get("scenes") or [] if isinstance(s, dict)}
    by_id = {c["id"]: c for c in cues(doc)}
    cue_ids = set(by_id)
    stories = _story_ids()
    out: list[str] = []
    seen: set[int] = set()
    openings = 0
    in_box_ticks = 0
    follows = 0
    for k, scene in enumerate(raw):
        if not isinstance(scene, dict):
            out.append("scene_%d_not_an_object" % k)
            continue
        n = scene.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            out.append("scene_%d_bad_n" % k)
            where = "scene_%d" % k
        else:
            if n in seen:
                out.append("duplicate_n:%d" % n)
            seen.add(n)
            where = "n%d" % n
        if not _text(scene.get("title_th")):
            out.append("%s_no_title" % where)
        steps = scene.get("steps")
        if not isinstance(steps, list) or not steps:
            out.append("%s_no_steps" % where)
            continue
        for i, step in enumerate(steps):
            at = "%s.%d" % (where, i)
            if not isinstance(step, dict):
                out.append("%s_not_an_object" % at)
                continue
            do = step.get("do")
            if do == "scene":
                # the Event app's (owner 9 Nov, "ไม่งั้นจะปนกัน"): a ceremony step must not press one
                out.append("%s_scene_is_the_event_apps" % at)
                continue
            if do not in ORDER_KINDS:
                out.append("%s_bad_kind:%s" % (at, str(do)[:24]))
                continue
            allowed = set(ORDER_KINDS[do]) | {"do"} | set(_ORDER_TEXT)
            for key in step:
                if key == "show" and do != "cue":
                    out.append("%s_show_not_on_cue" % at)
                elif key not in allowed:
                    out.append("%s_unexpected_field:%s" % (at, str(key)[:24]))
            for key in _ORDER_TEXT:
                if key in step and not isinstance(step[key], str):
                    out.append("%s_%s_not_text" % (at, key))
            if do == "manual" and not _text(step.get("label_th")):
                out.append("%s_manual_without_label" % at)   # a button with nothing on it
            if do == "manual" and "in_box" in step:
                if step["in_box"] is not True:
                    out.append("%s_bad_in_box" % at)
                else:
                    in_box_ticks += 1
                    if in_box_ticks > 1:
                        out.append("%s_second_in_box_tick" % at)
            value = step.get(ORDER_KINDS[do][0]) if ORDER_KINDS[do] else None
            if do == "screen" and value not in SCREEN_WANTS:
                out.append("%s_bad_screen:%s" % (at, str(value)[:24]))
            elif do == "handoff" and value not in HANDOFF_APPS:
                out.append("%s_bad_app:%s" % (at, str(value)[:24]))
            elif do == "walk" and value not in spots:
                out.append("%s_unknown_spot:%s" % (at, str(value)[:48]))
            elif do == "clip" and value not in clips:
                out.append("%s_unknown_clip:%s" % (at, str(value)[:48]))
            elif do == "cue" and value not in cue_ids:
                out.append("%s_unknown_cue:%s" % (at, str(value)[:48]))
            elif do == "opening":
                # card 1 of the Event file: a scene that walks out of the box (its `walk_out_of_box`), once
                openings += 1
                if not in_box_ticks:
                    # nothing would ever arm its walk out (the Event page's button is the Event app's)
                    out.append("%s_opening_without_in_box_tick" % at)
                sc = scenes.get(value)
                if sc is None:
                    out.append("%s_unknown_scene:%s" % (at, str(value)[:48]))
                elif not sc.get("walk_out_of_box"):
                    out.append("%s_scene_not_out_of_the_box:%s" % (at, str(value)[:48]))
                if openings > 1:
                    out.append("%s_second_opening" % at)
            elif do == "follow":
                # card 2: after the opening (card 1), once -- it lets the server follow the host from there
                follows += 1
                if not openings:
                    out.append("%s_follow_before_the_opening" % at)
                if follows > 1:
                    out.append("%s_second_follow" % at)
            elif do == "device":
                # named in the venue devices file, its action one the device's kind has -- checked only
                # where this machine drives devices (review 20 Nov): with VENUE_DEVICES off the step is
                # not in the order at all (event_order._visible), and a half-edited venue file must not
                # turn the morning card's "ลำดับพิธี" row red. The PC's preflight checks the ids against
                # the ROBOT's config (scripts/event_preflight.py check_venue).
                from app import venue_devices

                if venue_devices.Config.from_settings().enabled:
                    bad = venue_devices.step_problem(value, step.get("action"))
                    if bad:
                        out.append("%s_%s" % (at, bad))
            elif do == "body":
                if value == "stop":
                    # the page's red STOP button (and the E-stop) stops; a stop that is
                    # a step in a list is one somebody has to scroll to
                    out.append("%s_stop_is_not_a_step" % at)
                elif value not in rc.BODY_ACTION_IDS:
                    out.append("%s_unknown_body:%s" % (at, str(value)[:48]))
            if do == "cue" and "show" in step and stories is not None and step["show"] not in stories:
                out.append("%s_unknown_story:%s" % (at, str(step["show"])[:48]))
            if do == "cue" and "chain" in step:
                out += _chain_problems(at, step, steps[i + 1] if i + 1 < len(steps) else None, by_id)
    return out


def _chain_problems(at: str, step: dict, after, by_id: dict) -> list[str]:
    """A cue step's `chain` (owner 23 Nov, the jokes said straight through): the next step of the
    SAME scene is pressed by itself once this line has been said (app/event_order.py). Both must be
    worded lines: a live brief has no end the server knows, and a line with a film after it would
    have the next line start over the film. `false` = no chain."""
    value = step.get("chain")
    if not isinstance(value, bool):
        return ["%s_chain_not_a_bool" % at]
    if value is False:
        return []
    if not isinstance(after, dict):
        return ["%s_chain_to_nothing" % at]          # the scene's last step: a chain never crosses a scene
    if after.get("do") != "cue":
        return ["%s_chain_to_a_%s" % (at, str(after.get("do"))[:16])]
    out = []
    lead, follow = by_id.get(step.get("cue")), by_id.get(after.get("cue"))
    if lead is not None and not lead.get("th"):
        out.append("%s_chain_from_a_line_without_words" % at)
    if lead is not None and lead.get("clip"):
        out.append("%s_chain_after_a_film" % at)
    if follow is not None and not follow.get("th"):
        out.append("%s_chain_to_a_line_without_words" % at)
    return out
