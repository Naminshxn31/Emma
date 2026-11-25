"""The ceremony's motion slides on the event screen (owner 17 Nov 2026: "เราจะทำตัวพรีเซ้นต์
โมชั่น โดยที่เราจะต้องลิ้งระหว่างสไลด์กับเสียงหุ่น ... แล้วก็มีซับภาษาไทยด้านล่าง ... อันนี้
สำหรับขึ้นจออีเว้นท์ ขนาด 16:9").

Every line Emma says in the ceremony (app/event_script.py, CEREMONY_FILE) may have one
slide: a template, its words, and beats that fire when her voice has reached a point of
the line. The slide travels with the line's subtitle (app/face_mirror.py `subtitle`) to
the room screen, whose engine (client/assets/ceremony-show.js) draws it; the robot's own
face never sees it.

The slides are OUR drawing of the owner's approved words, so:

  * the file (`<ceremony file stem>.slides.json`) is a draft until a person signs it
    (scripts/approve_ceremony_slides.py): `approved_by` / `approved_at` /
    `approved_sha256` over every slide entry. Unsigned, or changed since = the robot's
    server sends no slide at all (the plain backdrop and the subtitle, as before).
    Nothing here writes those fields.
  * every slide is checked against ITS cue's line each time it is read: on-screen words
    are English and each must be a word of that cue's English line (or the project's
    name); every number on it must be a number that line says; a number the owner fixed
    with a qualifier keeps it ("about 600 ... straight line"); an icon must be backed by
    a word of the line too (a pool on the joke's set-up would give the punchline away).
    Never a price, a promotion, a usable roof, a "concept / subject to change" caveat,
    the live-or-invest question -- and there is no field a picture could go in.
  * a slide that breaks a rule is not served (the backdrop instead) and the reason is
    logged once; it never stops the cue.

`for_cue()` gives what the page gets: the template's own fields rebuilt one by one and
the beats as fractions of the line (0..1), resolved from `at_en` / `at_th` -- a piece of
the line as it is written -- for the language she says it in.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from app import event_script
from app.offline.lines import norm

logger = logging.getLogger("condo_voice.ceremony_slides")

FORMAT = "emma-ceremony-slides-v1"

#: The icons the engine can draw (client/assets/ceremony-show.js ICONS -- a test compares
#: the two lists) and the words of the cue's English line that back each one: at least one
#: must be in the line. An empty tuple = decoration that says nothing (always allowed).
ICONS: dict[str, tuple[str, ...]] = {
    "spark": (),
    "orbit": ("futuristic", "innovation", "world"),
    "info": ("information",),
    "home": ("unit", "units", "home", "living", "residents"),
    "plan": ("plans", "plan"),
    "doc": ("quotation", "details"),
    "relax": ("relaxing", "relax"),
    "chip": ("technology", "smart", "system"),
    "pin": ("area", "road", "located"),
    "sea": ("sea", "beach"),
    "pool": ("pool", "swimming"),
    "tree": ("outdoors", "common"),
    "building": ("building", "floor", "storeys"),
    "layers": ("basement", "ground", "floor", "level"),
    "bulb": ("lights",),
    "curtain": ("curtains",),
    "play": ("video", "story"),
    "trophy": ("first", "place"),
    "drop": ("water", "waterproof"),
    "gauge": ("dashboard", "monitor"),
    "screen": ("screen", "dashboard"),
    "shop": ("shopping",),
    "hospital": ("hospitals",),
    "school": ("schools",),
    "star": ("attractions", "highlight"),
    "people": ("everyone", "team", "family", "customers", "agents", "people"),
    "chat": ("talk", "ask", "hello"),
    "heart": ("pleasure", "thank", "glad", "delighted"),
    "ruler": ("metres", "deep"),
    "book": ("studied",),
}

# ---- what a template carries ---------------------------------------------------------
# kind: "text" (a string), "texts" (a list of strings), "icon", "asset" (a picture's id in
# the stories catalogue), "bool", "int", ("enum", (...)), and rows of small objects:
# _rows(fields, optional names, fewest, most) / ("row", fields, optional names).
_TEXT_MAX = 90
_ROWS_MAX = 5


def _rows(fields: dict, optional: tuple = (), lo: int = 2, hi: int = _ROWS_MAX) -> tuple:
    return ("rows", fields, optional, lo, hi)


#: A figure laid over or beside a picture: the `number` template's own fields.
_FIGURE = ("row", {"lead": "text", "value": "number", "unit": "text", "label": "text"}, ("lead", "label"))
#: How a picture sits in its frame: filling it (cropped) or whole (a plan, a map).
_FIT = ("enum", ("cover", "contain"))

TEMPLATES: dict[str, dict] = {
    # a greeting, an invitation, the thank-you: big words in the middle
    "hero": {"need": ("title",),
             "fields": {"kicker": "text", "title": "text", "sub": "text", "chips": "texts", "icon": "icon",
                        "mood": ("enum", ("plain", "closing"))}},
    # one big figure
    "number": {"need": ("value", "unit"),
               "fields": {"kicker": "text", "title": "text", "lead": "text", "value": "number", "unit": "text",
                          "label": "text",
                          "chips": "texts", "graphic": ("enum", ("span", "lane")),
                          "from_icon": "icon", "to_icon": "icon"}},
    # a title and things revealed one by one
    "list": {"need": ("title", "items"),
             "fields": {"kicker": "text", "title": "text",
                        "items": _rows({"text": "text", "icon": "icon"}, ("icon",)),
                        "highlight": ("row", {"text": "text", "icon": "icon"}, ("icon",)), "foot": "text"}},
    # two or three choices; `pick` = the one that lights up at the "pick" beat
    "choice": {"need": ("title", "options"),
               "fields": {"kicker": "text", "title": "text",
                          "options": _rows({"text": "text", "icon": "icon"}, ("icon",)), "pick": "int"}},
    # "working ... " and "done" (the lights, the curtain, the quotation)
    "status": {"need": ("state", "title", "device"),
               "fields": {"kicker": "text", "state": ("enum", ("working", "done")),
                          "device": ("enum", ("lights_off", "lights_on", "curtain", "document")),
                          "title": "text", "detail": "text"}},
    # a joke: the set-up (with its trailing dots) and/or the punchline
    "joke": {"need": (),
             "fields": {"kicker": "text", "title": "text", "tail": "bool", "punch": "text", "icon": "icon"}},
    # several figures side by side
    "stats": {"need": ("stats",),
              "fields": {"kicker": "text", "title": "text",
                         "stats": _rows({"lead": "text", "value": "number", "unit": "text", "label": "text"},
                                        ("lead",))}},
    # ---- the picture templates (owner 17 Nov: "โชว์รูปต่างๆให้คนเห็นภาพ") ----
    # a small film: full-bleed pictures one after another, each on its beat, drifting
    # (a shot may carry its own figure, up while that picture is: a line with several
    # numbers gives each one its picture; `end` = a large centred title for the last frame)
    "film": {"need": ("shots",),
             "fields": {"kicker": "text", "title": "text",
                        "shots": _rows({"asset": "asset", "caption": "text", "fit": _FIT, "figure": _FIGURE},
                                       ("caption", "fit", "figure"), 1, 6),
                        "figure": _FIGURE, "chips": "texts", "end": "text"}},
    # one large picture on one side (it changes on beats), the words on the other
    "spotlight": {"need": ("title", "pictures"),
                  "fields": {"kicker": "text", "title": "text", "sub": "text",
                             "side": ("enum", ("left", "right")),
                             "pictures": _rows({"asset": "asset", "caption": "text", "fit": _FIT},
                                               ("caption", "fit"), 1, 5),
                             "figure": _FIGURE, "chips": "texts"}},
    # three to six picture tiles arriving one by one, each with its label
    "mosaic": {"need": ("tiles",),
               "fields": {"kicker": "text", "title": "text",
                          "tiles": _rows({"asset": "asset", "label": "text", "fit": _FIT}, ("label", "fit"), 3, 6),
                          "foot": "text"}},
}
#: Any of the first seven may sit on a dimmed picture.
for _name in ("hero", "number", "list", "choice", "status", "joke", "stats"):
    TEMPLATES[_name]["fields"]["backdrop"] = "asset"

#: What the status template's devices must be backed by, as an icon is.
_DEVICES = {"lights_off": ("lights",), "lights_on": ("lights",), "curtain": ("curtains",),
            "document": ("quotation",)}

_THAI = re.compile(r"[฀-๿]")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z]+")
#: Never on the event screen, whatever the line says (CLAUDE.md: prices, promotions, the
#: roof as a place to be, the concept caveat, the live-or-invest question).
BANNED = re.compile(
    r"price|pricing|baht|thb|฿|\$|discount|promotion|promo\b|"
    r"roof\s*-?\s*top|roof\s*-?\s*(?:deck|terrace|garden|bar|pool)|sky\s*-?\s*(?:deck|terrace|garden|bar)|"
    r"concept|subject\s+to\s+change|preliminary|not\s+final|"
    r"invest|live\s+in\s+it|"
    r"https?:|www\.|\.(?:png|jpe?g|webp|svg|gif)\b|<|>",
    re.I)
#: Words a slide may use without the line saying them: the project's and her name, and the
#: small words that join a headline.
ALLOWED_WORDS = frozenset(
    "embassy world emma a an the and of to in on for with at is are it this that".split())
#: The owner's fixed figures and the words that must stay beside them on the same slide
#: (each inner tuple: any one of its phrases).
QUALIFIED: dict[str, tuple[tuple[str, ...], ...]] = {
    "600": (("about",), ("straight line",)),
    "2500": (("around", "about"), ("estimate",)),
    "23": (("about",),),
}
_BEAT_FIELDS = frozenset({"show", "at", "at_en", "at_th"})
#: The template of a slide an act draws (the generic content model below, "acts").
ACT = "act"
_ID = re.compile(r"[a-z][a-z0-9_]{1,47}")


def path() -> Path | None:
    """`<ceremony file stem>.slides.json` beside CEREMONY_FILE; None with no ceremony file."""
    script = event_script.path()
    if script is None:
        return None
    return script.with_name(script.stem + ".slides.json")


def load_base(file: Path | None = None) -> dict | None:
    """The base slides file as written, or None (no file, unreadable, another format).
    The approval's fields live here; the approval script writes only this."""
    target = file if file is not None else path()
    if target is None:
        return None
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.exception("ceremony slides %s could not be read", target)
        return None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or not isinstance(doc.get("slides"), dict):
        logger.warning("ceremony slides %s is not %s -- no slides", target, FORMAT)
        return None
    return doc


def cue_dir(file: Path | None = None) -> Path | None:
    """`slides/` beside the base file: one `<cue id>.json` per cue."""
    target = file if file is not None else path()
    return None if target is None else target.parent / "slides"


def load(file: Path | None = None) -> dict | None:
    """The slides as they are used: the base file with every `slides/<cue id>.json` laid
    over it (owner 17 Nov: "ให้เอเจนท์แยกทำแต่ละหน้าเลย" -- one file per cue, so authors
    never touch the same file). A per-cue file is one slide object ({"template": ...}) and
    replaces the base file's slide of that cue. One that cannot be read takes down ITS
    slide only -- never back to the older one in the base file, never the rest."""
    doc = load_base(file)
    if doc is None:
        return None
    merged = dict(doc["slides"])
    folder = cue_dir(file)
    try:
        files = sorted(folder.glob("*.json")) if folder is not None and folder.is_dir() else []
    except OSError:
        files = []
    for one in files:
        cue_id = one.stem
        if not _ID.fullmatch(cue_id):
            logger.warning("ceremony slides: %s is not a cue id -- ignored", one.name)
            continue
        try:
            slide = json.loads(one.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("ceremony slides: %s could not be read (%s) -- that cue has no slide", one.name, exc)
            slide = {"template": BROKEN}
        merged[cue_id] = slide if isinstance(slide, dict) else {"template": BROKEN}
    return {**doc, "slides": merged}


#: The template of a per-cue file that could not be read: no such template, so not served.
BROKEN = "(unreadable file)"


def _used_assets(value, out: set[str]) -> set[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("asset", "backdrop") and isinstance(item, str):
                out.add(item)
            else:
                _used_assets(item, out)
    elif isinstance(value, list):
        for item in value:
            _used_assets(item, out)
    return out


def digest(doc: dict) -> str:
    """What an approval signs: every slide entry as written (the per-cue files laid over
    the base file), and for every picture they name its pinned bytes and what the
    pictures' list says of it -- a picture swapped or un-excluded after the signature is
    a new thing to look at."""
    from app import stories

    slides = doc.get("slides") or {}
    body = {"slides": slides}
    used = sorted(_used_assets(slides, set()))
    if used:                                  # (none = the digest of a file signed before pictures)
        pins = (stories.load_catalog() or {}).get("assets") or {}
        listed = asset_registry()
        body["pictures"] = {a: {"sha256": (pins.get(a) or {}).get("sha256"), "listed": listed.get(a)} for a in used}
        # ... and every file of the ceremony's own index a slide can bring to the screen: the
        # full-size picture, its depth map, a candidate, a clip -- as the bytes are NOW
        files = {a: facts for a in used for facts in [_index_facts(a)] if facts}
        if files:
            body["files"] = files
    # ... and the act that draws it (its code decides when and how the words appear)
    named = sorted({s["act"] for s in slides.values()
                    if isinstance(s, dict) and s.get("template") == ACT and isinstance(s.get("act"), str)})
    if named:
        have = acts()
        body["acts"] = {a: ({"js": have[a]["js_sha256"], "css": have[a]["css_sha256"]} if a in have else None)
                        for a in named}
    if len(body) == 1:
        body = slides
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approved(doc: dict | None) -> bool:
    """Signed by a person, for exactly these slides."""
    return (isinstance(doc, dict)
            and isinstance(doc.get("approved_by"), str) and bool(doc["approved_by"].strip())
            and isinstance(doc.get("approved_at"), str) and bool(doc["approved_at"].strip())
            and doc.get("approved_sha256") == digest(doc))


# ---- one slide against its cue ---------------------------------------------------------

def _number_key(text: str) -> str:
    return text.replace(",", "").rstrip(".")


def _words(text: str | None) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def _in_line(word: str, line: set[str]) -> bool:
    """The word as the line has it, or its plural/singular."""
    return (word in line or word + "s" in line or (word.endswith("s") and word[:-1] in line))


# ---- a figure stays with its own words -------------------------------------------------
# Review 17 Nov: every word and number of "8 metres deep" or "23 storeys high" is in the
# line, and so is "about" somewhere on a slide whose 600 stands alone. A figure is a claim
# only together with what it counts, so each one is bound to the line's own phrase:
#   * a figure with a unit (`number`, a `stats` tile): "<lead> <value>" and "<value> <unit>"
#     must each be a run of the line's words, in order, with nothing between;
#   * a figure inside a sentence ("Floor 3", "155 metres long"): the number and the word
#     after it (the word before, when it ends the text) must be such a run; a bare figure
#     with no word beside it is refused;
#   * the owner's qualifiers (QUALIFIED) must be in the SAME group -- the figure's own
#     lead/unit/label, or the same text -- not anywhere on the slide;
#   * a `stats` tile's label must be made of words of the clause the line says the figure
#     in (the text between two of the line's punctuation marks).
# Not enforced (a person looks at these): that a `number` slide's caption, a list item or
# a chip sits beside the right figure when it has no number in it; word order inside a
# text; that an icon matches the item it is drawn on.

_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+")
#: On-screen text: letters, digits, spaces and these marks. No "+", "%", currency, no
#: letter outside A-Z (the headline font and the checks above are for English).
_PLAIN = re.compile(r"[A-Za-z0-9 ·,.\-'!?:]*")
#: "roof" only as the height the line gives -- never beside a pool, a facility, a view.
_ROOF = re.compile(r"\broof", re.I)
_ROOF_AS_HEIGHT = frozenset({"roof level", "the roof level"})


def _toks(text: str | None) -> list[str]:
    """Words and numbers in order; a number as written, without the comma or stop after it."""
    return [t.rstrip(",.") if t[0].isdigit() else t for t in _TOKEN.findall((text or "").lower())]


def _same(a: str, b: str) -> bool:
    if a[0].isdigit() or b[0].isdigit():
        return a == b
    return a == b or a + "s" == b or a == b + "s"


def _run_at(needle: list[str], hay: list[str]) -> int:
    """Where `needle` is a contiguous run of `hay` (-1 = nowhere)."""
    if not needle:
        return -1
    for i in range(len(hay) - len(needle) + 1):
        if all(_same(n, hay[i + j]) for j, n in enumerate(needle)):
            return i
    return -1


def _qualified(number: str, group: str, bad: list[str]) -> None:
    needs = QUALIFIED.get(_number_key(number))
    for any_of in needs or ():
        if not any(phrase in group for phrase in any_of):
            bad.append("number_lost_its_qualifier:%s:%s" % (_number_key(number), any_of[0]))


def _figures_of(slide: dict) -> list[tuple[dict, object]]:
    """A template slide's figures: (row, is a stats tile)."""
    figures: list[tuple[dict, object]] = []
    if slide.get("template") == "number":
        figures.append((slide, False))
    elif isinstance(slide.get("figure"), dict):
        figures.append((slide["figure"], False))          # a film's or a spotlight's figure
    if slide.get("template") == "film" and isinstance(slide.get("shots"), list):
        figures += [(row["figure"], False) for row in slide["shots"]      # ... and each shot's own
                    if isinstance(row, dict) and isinstance(row.get("figure"), dict)]
    elif slide.get("template") == "stats" and isinstance(slide.get("stats"), list):
        figures += [(row, True) for row in slide["stats"] if isinstance(row, dict)]
    return figures


def _bound(figures: list, strings: list[str], said: list[str], line: str | None) -> list[str]:
    """`figures`: (row, how its label is held) -- False = not held, True = to the CLAUSE the
    figure is said in (a stats tile), "sentence" = to its sentence (an act's figure)."""
    bad: list[str] = []
    plain = list(strings)
    for row, tile in figures:
        value = row.get("value")
        if not isinstance(value, str) or not _toks(value):
            continue
        if value in plain:
            plain.remove(value)                    # checked here, with its lead and unit
        number = _toks(value)[:1]
        lead, unit = _toks(row.get("lead") if isinstance(row.get("lead"), str) else ""), \
            _toks(row.get("unit") if isinstance(row.get("unit"), str) else "")
        if lead and _run_at(lead + number, said) < 0:
            bad.append("number_not_with_its_words:%s" % value)
        at = _run_at(number + unit, said)
        if at < 0:
            bad.append("number_not_with_its_words:%s" % value)
        anchor = lead + number if lead and not unit else number + unit
        group = " ".join(str(row.get(k) or "") for k in ("lead", "value", "unit", "label")).lower()
        _qualified(value, norm(group), bad)
        if tile and at >= 0 and isinstance(row.get("label"), str):
            # the clause of the line this figure is said in
            clause: set[str] = set()
            for piece in re.split(r"(?<=[.,;:!?])\s+" if tile is True else r"(?<=[.!?])\s+", norm(line).lower()):
                words = _toks(piece)
                if _run_at(anchor, words) >= 0:
                    clause = set(words)
                    break
            for word in _WORD.findall(row["label"].lower()):
                if word not in ALLOWED_WORDS and not _in_line(word, clause):
                    bad.append("label_not_with_its_number:%s:%s" % (value, word))
    for text in plain:
        words = _toks(text)
        for i, tok in enumerate(words):
            if not tok[0].isdigit():
                continue
            pair = [tok, words[i + 1]] if i + 1 < len(words) else ([words[i - 1], tok] if i else [])
            if not pair or _run_at(pair, said) < 0:
                bad.append("number_not_with_its_words:%s" % tok)
            _qualified(tok, norm(text).lower(), bad)
    return bad


def keys_of(slide: dict) -> list[str]:
    """The parts of a slide a beat may reveal (the engine marks the same ones `data-k`)."""
    template = slide.get("template")
    if template == ACT:
        return [key for group in _act_maps(slide).values() for key in group]
    out: list[str] = []

    def rows(name: str, key: str) -> None:
        value = slide.get(name)
        if isinstance(value, list):
            out.extend("%s:%d" % (key, i) for i in range(len(value)))

    if template == "hero":
        out += ["title"] + (["sub"] if slide.get("sub") else []) + (["icon"] if slide.get("icon") else [])
        rows("chips", "chip")
    elif template == "number":
        out += ((["title"] if slide.get("title") else []) + ["value"] + (["label"] if slide.get("label") else [])
                + (["graphic"] if slide.get("graphic") else []))
        rows("chips", "chip")
    elif template == "list":
        out += ["title"] + (["highlight"] if slide.get("highlight") else []) + (["foot"] if slide.get("foot") else [])
        rows("items", "item")
    elif template == "choice":
        out += ["title"] + (["pick"] if isinstance(slide.get("pick"), int) else [])
        rows("options", "option")
    elif template == "status":
        out += ["title"] + (["detail"] if slide.get("detail") else [])
    elif template == "joke":
        out += [k for k in ("title", "punch", "icon") if slide.get(k)]
    elif template == "stats":
        out += ["title"] if slide.get("title") else []
        rows("stats", "stat")
    elif template == "film":
        out += ((["title"] if slide.get("title") else []) + (["figure"] if slide.get("figure") else [])
                + (["end"] if slide.get("end") else []))
        rows("shots", "shot")
        rows("chips", "chip")
    elif template == "spotlight":
        out += ["title"] + (["sub"] if slide.get("sub") else []) + (["figure"] if slide.get("figure") else [])
        rows("pictures", "picture")
        rows("chips", "chip")
    elif template == "mosaic":
        out += (["title"] if slide.get("title") else []) + (["foot"] if slide.get("foot") else [])
        rows("tiles", "tile")
    return out


def _check_value(kind, value, where: str, strings: list[str], icons: list[str], bad: list[str],
                 assets: list[str] | None = None) -> None:
    if kind in ("text", "number"):
        if not isinstance(value, str) or not value.strip() or len(value) > _TEXT_MAX:
            bad.append("bad_text:%s" % where)
            return
        if kind == "number" and not _NUMBER.fullmatch(value.strip()):
            bad.append("not_a_number:%s" % where)
        strings.append(value)
    elif kind == "texts":
        if not isinstance(value, list) or not 1 <= len(value) <= _ROWS_MAX:
            bad.append("bad_list:%s" % where)
            return
        for i, item in enumerate(value):
            _check_value("text", item, "%s[%d]" % (where, i), strings, icons, bad, assets)
    elif kind == "icon":
        if not isinstance(value, str) or value not in ICONS:
            bad.append("unknown_icon:%s" % where)
        else:
            icons.append(value)
    elif kind == "asset":
        if not isinstance(value, str) or not value:
            bad.append("bad_asset:%s" % where)
        elif assets is not None:
            assets.append(value)
    elif kind == "bool":
        if not isinstance(value, bool):
            bad.append("bad_flag:%s" % where)
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            bad.append("bad_index:%s" % where)
    elif isinstance(kind, tuple) and kind[0] == "enum":
        if value not in kind[1]:
            bad.append("bad_choice:%s" % where)
    elif isinstance(kind, tuple) and kind[0] in ("rows", "row"):
        rows = value if kind[0] == "rows" else [value]
        if kind[0] == "rows" and (not isinstance(value, list) or not kind[3] <= len(value) <= kind[4]):
            bad.append("bad_list:%s" % where)
            return
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                bad.append("bad_row:%s[%d]" % (where, i))
                continue
            for name in row:
                if name not in kind[1]:
                    bad.append("unknown_field:%s.%s" % (where, name))
            for name, sub in kind[1].items():
                if name in row:
                    _check_value(sub, row[name], "%s[%d].%s" % (where, i, name), strings, icons, bad, assets)
                elif name not in kind[2]:
                    bad.append("missing:%s[%d].%s" % (where, i, name))


def _find(line: str, piece: object) -> float | None:
    """Where a piece of the line starts, as a fraction of the line (0..1)."""
    if not isinstance(piece, str) or not norm(piece) or not line:
        return None
    # (case does not matter: "a real highlight" is the line's "A real highlight" -- 17 Nov
    # evening, a builder's beat refused for a capital. Same length either way for this text.)
    low, want = line.lower(), norm(piece).lower()
    at = (low if len(low) == len(line) else line).find(want if len(low) == len(line) else norm(piece))
    return None if at < 0 else at / len(line)


def _beat_place(beat: dict, name: str, lines: dict, last: dict, bad: list[str], order: tuple) -> float | None:
    """Where a beat is in the line (0..1), from `at_en` / `at_th` / `at`; None = nowhere
    (said in `bad`). `last` keeps the line's order between calls."""
    found: dict[str, float] = {}
    for code in ("en", "th"):
        if "at_" + code in beat:
            where = _find(lines[code], beat["at_" + code])
            if where is None:
                bad.append("beat_not_in_line:%s:%s" % (code, name))
                continue
            if where < last[code]:
                bad.append("beats_out_of_order:%s:%s" % (code, name))
            last[code] = where
            found[code] = where
    if "at" in beat:
        at = beat["at"]
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 <= at <= 1:
            bad.append("bad_beat_at:%s" % name)
            return None
        if at < last["at"]:
            bad.append("beats_out_of_order:at:%s" % name)
        last["at"] = float(at)
        found.setdefault("at", float(at))
    if not found:
        bad.append("beat_has_no_place:%s" % name)
        return None
    return found.get(order[0], found.get(order[1], found.get("at")))


def beats_of(slide: dict, cue: dict, lang: str = "en") -> tuple[list[dict], list[str]]:
    """The slide's beats as [{at: 0..1, show: key}] in voice order, and what is wrong with
    them. `lang` = the language she says the line in: its piece (`at_en`/`at_th`) places
    the beat, the other language's is the fallback; a bare `at` is a fraction as written."""
    if slide.get("template") == ACT:
        return _act_beats(slide, cue, lang)
    raw = slide.get("beats", [])
    bad: list[str] = []
    if not isinstance(raw, list):
        return [], ["bad_beats"]
    keys = set(keys_of(slide))
    lines = {"en": norm(cue.get("en")), "th": norm(cue.get("th"))}
    first, second = ("en", "th") if lang == "en" else ("th", "en")
    out: list[dict] = []
    last = {"en": -1.0, "th": -1.0, "at": -1.0}
    seen: set[str] = set()
    for i, beat in enumerate(raw):
        if not isinstance(beat, dict) or set(beat) - _BEAT_FIELDS:
            bad.append("bad_beat:%d" % i)
            continue
        key = beat.get("show")
        if key not in keys:
            bad.append("beat_shows_nothing:%s" % key)
            continue
        if key in seen:
            bad.append("beat_twice:%s" % key)
            continue
        seen.add(key)
        at = _beat_place(beat, key, lines, last, bad, (first, second))
        if at is None:
            continue
        out.append({"at": round(float(at), 4), "show": key})
    out.sort(key=lambda b: b["at"])          # stable: equal places keep the file's order
    return out, bad


def _strings_problems(strings: list[str], cue: dict) -> list[str]:
    """Every on-screen string against its cue's English line: the characters, the words that
    are never shown, and that each word and number is one the line says."""
    bad: list[str] = []
    line = _words(cue.get("en"))
    numbers = {t for t in _toks(cue.get("en")) if t[0].isdigit()}
    for text in strings:
        if _THAI.search(text):
            bad.append("thai_on_slide")
        if not _PLAIN.fullmatch(_THAI.sub("", text)):
            bad.append("symbol_on_slide")
        hit = BANNED.search(text)
        if hit:
            bad.append("banned:%s" % hit.group().strip().lower())
        if _ROOF.search(text) and norm(text).lower() not in _ROOF_AS_HEIGHT:
            bad.append("banned:roof")
        for m in _NUMBER.finditer(text):
            # as the line writes it, to the comma: "10,66" is not the line's 1,066
            if m.group().rstrip(",.") not in numbers:
                bad.append("number_not_in_line:%s" % m.group())
        for word in _WORD.findall(text.lower()):
            if word not in ALLOWED_WORDS and not _in_line(word, line):
                bad.append("word_not_in_line:%s" % word)
    return bad


def problems(slide: object, cue: dict | None) -> list[str]:
    """Why this slide may not go on the screen ([] = it may)."""
    if not isinstance(slide, dict):
        return ["not_a_slide"]
    if cue is None:
        return ["unknown_cue"]
    if not cue.get("en") or not cue.get("th"):
        return ["cue_has_no_words"]
    if slide.get("template") == ACT:
        seen_act: set[str] = set()
        return [b for b in _act_problems(slide, cue) if not (b in seen_act or seen_act.add(b))]
    spec = TEMPLATES.get(slide.get("template"))
    if spec is None:
        return ["unknown_template"]
    bad: list[str] = []
    strings: list[str] = []
    icons: list[str] = []
    pictures: list[str] = []
    for name in slide:
        if name not in spec["fields"] and name not in ("template", "beats"):
            bad.append("unknown_field:%s" % name)
    for name in spec["need"]:
        if name not in slide:
            bad.append("missing:%s" % name)
    for name, kind in spec["fields"].items():
        if name in slide:
            _check_value(kind, slide[name], name, strings, icons, bad, pictures)
    if slide.get("template") == "joke" and not (slide.get("title") or slide.get("punch")):
        bad.append("missing:title")
    if slide.get("template") == "choice" and isinstance(slide.get("pick"), int) and not isinstance(slide.get("pick"), bool):
        if not 0 <= slide["pick"] < len(slide.get("options") or []):
            bad.append("bad_index:pick")
    if not strings and not pictures:
        bad.append("no_words")

    line = _words(cue.get("en"))
    said = _toks(cue.get("en"))
    bad += _strings_problems(strings, cue)
    bad += _bound(_figures_of(slide), strings, said, cue.get("en"))
    for icon in icons:
        if ICONS[icon] and not any(w in line for w in ICONS[icon]):
            bad.append("icon_not_in_line:%s" % icon)
    device = slide.get("device")
    if device in _DEVICES and not any(w in line for w in _DEVICES[device]):
        bad.append("icon_not_in_line:%s" % device)
    bad += _pictures(slide, cue)
    # a film's first picture is its establishing shot, a spotlight's frame is never empty:
    # picture 0 is up before her voice, so no beat may name it
    first = {"film": "shot:0", "spotlight": "picture:0"}.get(slide.get("template"))
    if first and isinstance(slide.get("beats"), list) and any(
            isinstance(b, dict) and b.get("show") == first for b in slide["beats"]):
        bad.append("first_picture_on_a_beat:%s" % first)
    bad += beats_of(slide, cue)[1]
    seen: set[str] = set()
    return [b for b in bad if not (b in seen or seen.add(b))]


def _rebuilt(kind, value):
    if kind in ("text", "number"):
        return norm(value)
    if kind == "texts":
        return [norm(v) for v in value]
    if kind == "asset":
        return _picture(value)
    if isinstance(kind, tuple) and kind[0] == "rows":
        return [{k: _rebuilt(sub, row[k]) for k, sub in kind[1].items() if k in row} for row in value]
    if isinstance(kind, tuple) and kind[0] == "row":
        return {k: _rebuilt(sub, value[k]) for k, sub in kind[1].items() if k in value}
    return value


def _picture(asset_id: str) -> dict:
    """A picture as the page gets it: its address on the room screen's own listener
    (/story-asset/<sha256>, app/mirror_server.py -- the bytes are re-hashed there on every
    read) and its shape. Never a path, never the id's file name."""
    from app import stories

    pin = (stories.load_catalog() or {})["assets"][asset_id]
    return {"src": "/story-asset/%s" % pin["sha256"], "w": int(pin["w"]), "h": int(pin["h"]),
            "alpha": bool(pin.get("alpha"))}


def page_payload(slide: dict, cue: dict, lang: str = "en") -> dict:
    """A clean slide as the page gets it: the template's own fields, rebuilt one by one,
    and the beats as fractions of the line."""
    if slide["template"] == ACT:
        return _act_payload(slide, cue, lang)
    spec = TEMPLATES[slide["template"]]
    out = {"template": slide["template"]}
    for name, kind in spec["fields"].items():
        if name in slide:
            out[name] = _rebuilt(kind, slide[name])
    out["beats"] = beats_of(slide, cue, lang)[0]
    return out


# ---- what the server sends -------------------------------------------------------------

_cache: dict = {"key": None, "doc": None, "told": set()}


def reset() -> None:
    _cache.update(key=None, doc=None, told=set())


def _stat(target: Path | None):
    try:
        st = target.stat()
        return (str(target), st.st_mtime_ns, st.st_size)
    except (OSError, AttributeError):
        return None


def _doc() -> dict | None:
    folder = cue_dir()
    try:
        per_cue = tuple(_stat(f) for f in sorted(folder.glob("*.json"))) if folder is not None and folder.is_dir() else ()
    except OSError:
        per_cue = ()
    key = (_stat(path()), _stat(event_script.path()), per_cue, _stat(assets_path()))
    if _cache["key"] != key:
        _cache.update(key=key, doc=load() if key[0] else None, told=set())
    return _cache["doc"]


def _tell(what: str, *args) -> None:
    """Once per reason while the files stay as they are: a cue is pressed many times."""
    line = what % args
    if line not in _cache["told"]:
        _cache["told"].add(line)
        logger.warning("ceremony slides: %s", line)


def for_cue(cue_id: object, *, lang: str = "en", drafts: bool = False) -> dict | None:
    """The slide to send with this cue's line, or None (no file, no slide for it, the file
    not signed, a rule broken). `drafts` = the preview on this PC only
    (scripts/preview_ceremony_show.py): unsigned slides too, the content rules unchanged.
    Never raises: a slide must not be able to stop a line."""
    try:
        doc = _doc()
        if doc is None or not isinstance(cue_id, str):
            return None
        slide = doc["slides"].get(cue_id)
        if slide is None:
            return None
        if not drafts and not approved(doc):
            _tell("the slides file is %s -- no slide is sent (scripts/approve_ceremony_slides.py)",
                  "changed since its approval" if doc.get("approved_by") else "a draft")
            return None
        cue = event_script.cue(cue_id)
        bad = problems(slide, cue)
        if bad:
            _tell("slide %s is not served: %s", cue_id, ", ".join(bad))
            return None
        if _used_assets(slide, set()):
            from app.config import settings

            if not settings.stories_enabled:      # /story-asset answers 404 then: no picture would load
                _tell("slide %s has pictures and STORIES_ENABLED is off on this machine -- not served", cue_id)
                return None
        return page_payload(slide, cue, lang)
    except Exception:
        logger.exception("ceremony slides: slide for %r", cue_id)
        return None


#: The one film a line outside the script may borrow (card 2's opener, owner 25 Nov): the act and scene whose
#: greeting heading `opener_slide` knows how to retitle.
OPENER_FILM = ("emma_console", "intro")


def opener_slide(base_id: object, en: object, th: object, *, lang: str = "en", drafts: bool = False) -> dict | None:
    """Card 2's opener on the hall screen (owner 25 Nov, "อย่าลืมโมชั่นด้วยนะ"): the film of the line it stands in for
    (`instead_of`, s02_l03 -- the opener is that line with its greeting swapped for "Allow me to introduce myself once
    again"), retitled. Built from the SIGNED slide, never a file of its own: the greeting heading (`hello`) says the
    opener's first sentence, the second greeting ("Hello, everyone": `every`) is gone and the beat that showed it goes
    when her name comes. Nothing else changes -- the same pictures, the same beats at the same words. Then the whole
    slide is checked against the opener's own words, as every slide is against its cue (`problems`): a word on the
    screen she does not say = None. None too when the base slide is not served itself (unsigned file, a broken rule)
    or is not the console's intro. Never raises: a slide must not be able to stop a line."""
    try:
        import copy

        doc = _doc()
        if doc is None or not isinstance(base_id, str) or not isinstance(en, str) or not isinstance(th, str):
            return None
        base = doc["slides"].get(base_id)
        if not isinstance(base, dict) or base.get("template") != ACT or (
                base.get("act"), (base.get("options") or {}).get("mode")) != OPENER_FILM:
            return None
        if not drafts and not approved(doc):
            _tell("the slides file is %s -- no slide for card 2's opener", "changed since its approval"
                  if doc.get("approved_by") else "a draft")
            return None
        if problems(base, event_script.cue(base_id)):
            return None                           # (for_cue says why, the line itself has no slide either)
        line = norm(en)
        first = re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0].rstrip(".!?")
        slide = copy.deepcopy(base)
        texts = slide.get("texts") or {}
        if "hello" not in texts or not first or first == line:
            return None
        texts["hello"] = first
        texts.pop("every", None)
        beats = slide.get("beats") or []
        for k, beat in enumerate(beats):
            if isinstance(beat, dict) and beat.get("key") == "every":
                nxt = beats[k + 1] if k + 1 < len(beats) and isinstance(beats[k + 1], dict) else {}
                if not nxt.get("at_en"):
                    return None
                beats[k] = {"at_en": nxt["at_en"], "do": beat.get("do")}
        cue = {"id": "card2_open", "en": line, "th": norm(th)}
        bad = problems(slide, cue)
        if bad:
            _tell("card 2's opener has no slide: %s", ", ".join(bad))
            return None
        if _used_assets(slide, set()):
            from app.config import settings

            if not settings.stories_enabled:
                return None
        return page_payload(slide, cue, lang)
    except Exception:
        logger.exception("ceremony slides: card 2's opener from %r", base_id)
        return None


def status(doc: dict | None = None) -> list[dict]:
    """Every slide of the file with what is wrong with it, for the approval script."""
    doc = doc if doc is not None else load()
    rows = []
    for cue_id, slide in ((doc or {}).get("slides") or {}).items():
        cue = event_script.cue(cue_id) if isinstance(cue_id, str) and _ID.fullmatch(cue_id) else None
        rows.append({"id": cue_id, "template": slide.get("template") if isinstance(slide, dict) else None,
                     "act": slide.get("act") if isinstance(slide, dict) and slide.get("template") == ACT else None,
                     "problems": problems(slide, cue)})
    return rows


# ---- pictures (owner 17 Nov: "โชว์รูปต่างๆให้คนเห็นภาพไม่ใช่แค่มีแค่โมชั่นหนังสือ") ----------------
# A slide names a picture by its id in the stories catalogue (app/stories.py: pinned by
# sha256, cropped and masked by that pipeline, approved with it). Which of those a
# ceremony slide may show is `slide_assets.json` beside the ceremony file -- deny by
# default: a picture not listed there as usable is not shown. Each usable one says what
# it shows and the words of a line that back it (`about`: one must be in the cue's
# English line -- a picture is a claim, as an icon is).

ASSETS_FORMAT = "emma-ceremony-slide-assets-v1"
ASSET_KINDS = ("render", "plan", "map", "drawn", "model", "other")
_assets_cache: dict = {"key": None, "doc": {}}


def assets_path() -> Path | None:
    script = event_script.path()
    return None if script is None else script.with_name("slide_assets.json")


def asset_registry() -> dict[str, dict]:
    """id -> {"ok", "why", "kind", "shows", "about", "text"} (empty with no file)."""
    target = assets_path()
    key = _stat(target)
    if _assets_cache["key"] != key or key is None:
        doc: dict = {}
        if key is not None:
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and raw.get("format") == ASSETS_FORMAT and isinstance(raw.get("assets"), dict):
                    doc = {k: v for k, v in raw["assets"].items() if isinstance(k, str) and isinstance(v, dict)}
                else:
                    logger.warning("ceremony slides: %s is not %s -- no pictures", target, ASSETS_FORMAT)
            except (OSError, ValueError):
                logger.exception("ceremony slides: %s could not be read -- no pictures", target)
        _assets_cache.update(key=key, doc=doc)
    return _assets_cache["doc"]


def asset_problem(asset_id: object, *, catalog: dict | None = None) -> str | None:
    """Why this picture may not be on any slide (None = it may): not the catalogue's pinned
    bytes, not listed as usable. Whether it belongs on a given slide is `_pictures`."""
    from app import stories

    if not isinstance(asset_id, str):
        return "bad_asset"
    catalog = catalog if catalog is not None else stories.load_catalog()
    pin = ((catalog or {}).get("assets") or {}).get(asset_id)
    if pin is None:
        return "unknown_asset"
    if stories.asset_problem(asset_id, pin) is not None:
        return "asset_changed"                       # not the bytes the catalogue pinned
    entry = asset_registry().get(asset_id)
    if entry is None:
        return "asset_not_listed"
    if entry.get("ok") is not True:
        return "asset_excluded"
    if not stories.approved(catalog):
        return "catalogue_not_approved"               # its pictures are not served at all
    if entry.get("kind") not in ASSET_KINDS or not isinstance(entry.get("about"), list) or not entry["about"]:
        return "asset_not_described"
    return None


# ---- a picture belongs where its own words are said ---------------------------------------
# Picture review 17 Nov: "about" used to be matched against the whole line with words like
# project / view / living / floor, so a map stood for "a view of the sea", a living room for
# "Smart Home", and the level-3 pool could be captioned as anything the line said. Now:
#   * `about` holds specific nouns only; a phrase is backed when ALL its words are there
#     (numbers exactly: "floor 3" is not "ground floor");
#   * a picture that comes in on a beat is backed by the SENTENCE of the line its beat is
#     in -- the words she is saying when it appears, not anything said elsewhere;
#   * a picture in the opening (no beat; a backdrop) is backed by the line, or is one of the
#     few listed `establishing` (the entrance, the project's view, the site hologram);
#   * a caption or label on a picture must contain one of THAT picture's `about` phrases.

#: A sentence that names the project itself. An `establishing` picture shows the project as a
#: whole (the buildings from outside, the lagoon from above, the campus model), so where she
#: names the project -- "Thank you all for being part of Embassy World", "everything about
#: Embassy World", "the story of Embassy World" -- it is backed, on a beat as in the opening
#: (integration 17 Nov evening: those lines could only reuse three or four pictures).
_PROJECT = (["embassy", "world"], ["project"])


def _names_project(tokens: list[str]) -> bool:
    return any(_run_at(name, tokens) >= 0 for name in _PROJECT)


def _backed_at(entry: dict, tokens: list[str]) -> bool:
    """Backed by these words: by its own `about`, or an establishing picture by a sentence that
    names the project."""
    return _backed(entry, tokens) or (entry.get("establishing") is True and _names_project(tokens))


def _backed(entry: dict, tokens: list[str]) -> bool:
    for phrase in entry.get("about") or []:
        need = _toks(phrase) if isinstance(phrase, str) else []
        if need and all(any(_same(n, t) for t in tokens) for n in need):
            return True
    return False


def _sentence_at(line: str, at: float) -> str:
    """The sentence of the line that the place `at` (0..1) is in."""
    index = min(len(line) - 1, max(0, int(round(at * len(line)))))
    start = 0
    for m in re.finditer(r"(?<=[.!?])\s+", line):
        if m.end() > index:
            return line[start:m.start()]
        start = m.end()
    return line[start:]


def _pictures(slide: dict, cue: dict) -> list[str]:
    bad: list[str] = []
    line = norm(cue.get("en"))
    rows: list[tuple[str | None, object, object]] = []            # (part key or None, asset, caption)
    if "backdrop" in slide:
        rows.append((None, slide["backdrop"], None))
    for name, key, text in (("shots", "shot", "caption"), ("pictures", "picture", "caption"), ("tiles", "tile", "label")):
        if name in TEMPLATES.get(slide.get("template"), {}).get("fields", {}) and isinstance(slide.get(name), list):
            rows += [("%s:%d" % (key, i), row.get("asset"), row.get(text))
                     for i, row in enumerate(slide[name]) if isinstance(row, dict) and "asset" in row]
    places = {b["show"]: b["at"] for b in beats_of(slide, cue)[0]}
    listed = asset_registry()
    for key, asset, caption in rows:
        if not isinstance(asset, str) or not asset:
            continue                                               # (said already: bad_asset)
        why = asset_problem(asset)
        if why:
            bad.append("%s:%s" % (why, asset))
            continue
        entry = listed[asset]
        if key in places:
            if not _backed_at(entry, _toks(_sentence_at(line, places[key]))):
                bad.append("asset_not_near_its_beat:%s" % asset)
        elif entry.get("establishing") is not True and not _backed(entry, _toks(line)):
            bad.append("asset_not_in_line:%s" % asset)
        if isinstance(caption, str) and caption.strip() and not _backed(entry, _toks(caption)):
            bad.append("caption_not_of_its_picture:%s" % asset)
    return bad


# ---- acts (owner 17 Nov evening: "แยกเอเจนท์สำหรับออกแบบแต่ละส่วน แล้วก็ให้ทำ") ---------------------
# The film is built from ACTS: one JavaScript file per part of the show
# (client/assets/ceremony-show/acts/<name>.js, + an optional .css), each drawing and moving
# its own piece on the shared core. What an act may put on the screen is still decided HERE,
# with no per-act code: a slide {"template": "act", "act": <name>} carries its whole content
# in one generic shape --
#     texts    {key: "words"}
#     figures  {key: {lead?, value, unit?, label?}}
#     pictures {key: {asset, caption?, fit?}}
#     videos   {key: {asset}}
#     beats    [{at_en | at_th | at, do: <the act's verb>, key?: <key> | [keys], ...numbers}]
#     options  numbers, booleans, short ids, arrays/objects of those (coordinates live here)
# -- and the engine hands the act nothing else to draw words or pictures with (its factories
# read these maps; a test reads every act file for another way to put words on the screen).
# Every rule of the templates holds for it, plus the ones a free-form act needs:
#   * a beat's `key` means "this comes on the screen here": the engine keeps what it made for
#     that key hidden until then, so the checks below are about what the hall really sees;
#   * a text or figure may not use a word or number she has not reached: its words must be in
#     the line up to the end of the sentence its (first) beat is in -- with no beat, the
#     FIRST sentence. A joke's punchline cannot be on the screen during its set-up;
#   * a figure needs a lead or a unit (never a bare number), holds the owner's qualifiers
#     inside itself, and its label is made of words of the sentence it is said in;
#   * a picture or clip is backed where it appears, as on the templates;
#   * `options` and a beat's own fields can hold no sentence: a string there is a short
#     lower-case id.
# Not enforced here (a person looks, and the source scan helps): what an act DOES at a beat.

_ACT_NAME = re.compile(r"[a-z][a-z0-9_]{1,31}")
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ACT_FIELDS = ("template", "act", "texts", "figures", "pictures", "videos", "beats", "options", "continues")
_ACT_MAPS = {"texts": 24, "figures": 12, "pictures": 16, "videos": 4}
_ACT_BEATS = 40
_ACT_BEAT_KEYS = 8
_OPTIONS_CHARS = 6000
_OPTIONS_DEPTH = 5
_ACT_FIGURE = ("row", {"lead": "text", "value": "number", "unit": "text", "label": "text"}, ("lead", "unit", "label"))
_ACT_PICTURE = ("row", {"asset": "asset", "caption": "text", "fit": _FIT}, ("caption", "fit"))
_ACT_VIDEO = ("row", {"asset": "asset"}, ())
_acts_cache: dict = {"key": None, "doc": {}}


def acts_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "client" / "assets" / "ceremony-show" / "acts"


def acts() -> dict[str, dict]:
    """Every act in the folder: name -> {"v": a tag of its bytes, "css": has a stylesheet,
    "js_sha256", "css_sha256"}. The folder IS the list: nothing to register by hand."""
    folder = acts_dir()
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix in (".js", ".css")) if folder.is_dir() else []
    except OSError:
        files = []
    key = tuple(_stat(f) for f in files)
    if _acts_cache["key"] != key:
        out: dict[str, dict] = {}
        for file in files:
            if file.suffix != ".js" or not _ACT_NAME.fullmatch(file.stem):
                continue
            try:
                js = file.read_bytes()
                sheet = file.with_suffix(".css")
                css = sheet.read_bytes() if sheet.is_file() else None
            except OSError:
                continue
            out[file.stem] = {
                "v": hashlib.sha256(js + b"\0" + (css or b"")).hexdigest()[:12],
                "css": css is not None,
                "js_sha256": hashlib.sha256(js).hexdigest(),
                "css_sha256": hashlib.sha256(css).hexdigest() if css is not None else None,
            }
        _acts_cache.update(key=key, doc=out)
    return _acts_cache["doc"]


def acts_manifest() -> dict:
    """What the room screen asks for when it opens (/ceremony-acts.json): the acts to load."""
    out: dict = {"acts": {name: {"v": a["v"], "css": a["css"]} for name, a in acts().items()}}
    # the clip the screen plays between lines -- only when it may be shown at all
    try:
        if act_asset_problem(IDLE_CLIP, video=True) is None:
            out["idle"] = _act_video(IDLE_CLIP)
    except Exception:
        logger.exception("ceremony slides: the idle clip")
    # the debut reveal's sound: the screen asks for it now, so the press starts it at once
    try:
        from app import ceremony_reveal

        spec = ceremony_reveal.info()
        if spec is not None:
            out["reveal"] = spec
    except Exception:
        logger.exception("ceremony slides: the reveal's sound")
    return out


# ---- the ceremony's own files: full-size pictures, depth maps, candidates, clips -----------------
# `assets/index.json` beside the ceremony file (scripts/build_ceremony_hires.py writes it):
# a list of {id, kind, file, sha256, w, h, from_pin | candidate, checked, ok, depth, about,
# class, shows, ...}. Two sorts of entry:
#   * `from_pin` = a catalogue picture's id: the same picture at its source size (and its
#     depth map). The catalogue's pin and the pictures' list stay the authority on whether
#     it may be shown and what it claims; this only makes it sharper.
#   * a candidate (`candidate: true`, or no `from_pin`): a file the stories pipeline never
#     saw. Usable only when the index says a person checked it (`checked`) and it is ok and
#     described -- deny by default, as everywhere.
# Every file is pinned by sha256 and re-hashed when it changes on disk; the screen gets it
# at /ceremony-asset/<sha256> (app/mirror_server.py) and at no other address.

_MEDIA = {".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
          ".avif": "image/avif", ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg"}
_VIDEO_SUFFIXES = (".mp4", ".webm")
#: A sound (23 Nov, the debut's): never a slide's -- served only as the one reveal.json names.
_AUDIO_SUFFIXES = (".mp3",)
_SHA = re.compile(r"[0-9a-f]{64}")
#: `checked` is a person's note of what was looked for ("no price / no people"); these
#: words alone are not one.
_NOT_CHECKED = frozenset({"no", "not", "none", "false", "not yet", "not checked", "unchecked", "pending", "todo",
                          "tbd", "later"})
_index_cache: dict = {"key": None, "doc": {}}
#: A picture's credit as the index may carry it (only a licence's own line: "© <holder>",
#: "· <service>"); anything else is not drawn.
_CREDIT = re.compile(r"© [A-Za-z][A-Za-z .]{1,40}(?: · [A-Za-z][A-Za-z .]{1,30})?")
#: The project's own logo clip (asset_candidates.json): it OPENS the ceremony on the room
#: screen, once (owner 18 Nov night: "เอาเปิดอันแรก ส่วนตอนรอระหว่างบทไม่ต้องมีเลย" -- the
#: owner's new 6.7 s clip, not the 10 s `logo_chrome`; never an interstitial between lines).
IDLE_CLIP = "c_logo_reveal"
#: The debut on card 1 (app/ceremony_reveal.py; owner 23 Nov): its settings beside the ceremony file --
#: the ids of its sound (the one sound of the index the screen may fetch) and its video, the countdown's
#: times for a silent run. scripts/ceremony_reveal_sound.py puts the sound and the video in.
REVEAL_FILE = "reveal.json"
#: (the ids when the file does not name them)
REVEAL_SOUND = "reveal_debut"
REVEAL_VIDEO = "debut_wave"
_reveal_cache: dict = {"key": None, "doc": {}}
_sha_cache: dict = {}


def index_dir() -> Path | None:
    script = event_script.path()
    return None if script is None else script.parent / "assets"


def _file_sha(target: Path) -> str | None:
    """The file's sha256 as it is on disk now (hashed again only when it changed)."""
    key = _stat(target)
    if key is None:
        return None
    hit = _sha_cache.get(key[0])
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        # (read whole: this module never holds a file handle -- a test keeps it from ever
        # writing one -- and the largest file here is a short clip)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return None
    _sha_cache[key[0]] = (key, digest)
    return digest


def _inside(folder: Path, name: object) -> Path | None:
    """A file of the assets folder by its relative name; never a path out of it."""
    if not isinstance(name, str) or not name.strip() or "\\" in name or name.startswith("/") or ":" in name:
        return None
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return folder.joinpath(*parts)


def _size(row: dict) -> tuple[int | None, int | None]:
    w, h = row.get("w"), row.get("h")
    size = row.get("size")
    if isinstance(size, (list, tuple)) and len(size) == 2:
        w, h = w or size[0], h or size[1]
    elif isinstance(size, dict):
        w, h = w or size.get("w"), h or size.get("h")
    ok = lambda n: isinstance(n, int) and not isinstance(n, bool) and 0 < n < 100000        # noqa: E731
    return (w if ok(w) else None), (h if ok(h) else None)


def _index_entry(row: object, folder: Path) -> dict | None:
    if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not _ID.fullmatch(row["id"]):
        return None
    file = _inside(folder, row.get("file"))
    if file is None or file.suffix.lower() not in _MEDIA:
        return None
    sha = row.get("sha256")
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        return None
    depth = row.get("depth")
    # (the index gives a depth map twice: as measured, and `soft` -- smaller and smoothed,
    # the one a picture is displaced by: a hard edge in the depth tears the picture)
    soft = depth.get("soft") if isinstance(depth, dict) and isinstance(depth.get("soft"), dict) else None
    if isinstance(soft, dict) and _inside(folder, soft.get("file")) is not None:
        depth_file, depth_sha = _inside(folder, soft.get("file")), soft.get("sha256")
    elif isinstance(depth, dict):
        depth_file, depth_sha = _inside(folder, depth.get("file")), depth.get("sha256")
    else:
        depth_file, depth_sha = _inside(folder, depth), None
    if depth_file is not None and depth_file.suffix.lower() not in (".png", ".webp", ".jpg", ".jpeg"):
        depth_file = None
    w, h = _size(row)
    from_pin = row.get("from_pin") if isinstance(row.get("from_pin"), str) and row["from_pin"] else None
    checked = row.get("checked")
    return {
        "id": row["id"], "file": file, "sha256": sha, "w": w, "h": h,
        "kind": ("video" if file.suffix.lower() in _VIDEO_SUFFIXES
                 else "audio" if file.suffix.lower() in _AUDIO_SUFFIXES else "picture"),
        "from_pin": from_pin,
        "candidate": row.get("candidate") is True or from_pin is None,
        "checked": checked is True or (isinstance(checked, str) and bool(checked.strip())
                                       and checked.strip().lower() not in _NOT_CHECKED),
        # (a keep-out that cannot be read is no keep-out: the file is not used at all)
        "ok": row.get("ok") is True and ("keep_out" not in row or _boxes(row["keep_out"]) is not None),
        "depth": None if depth_file is None else {
            "file": depth_file, "sha256": depth_sha if isinstance(depth_sha, str) and _SHA.fullmatch(depth_sha) else None,
            "near": "dark" if isinstance(depth, dict) and depth.get("near") in ("black", "dark") else "bright"},
        "about": [a for a in row.get("about") or [] if isinstance(a, str)] if isinstance(row.get("about"), list) else [],
        "class": row.get("class") if isinstance(row.get("class"), str) else None,
        "shows": row.get("shows") if isinstance(row.get("shows"), str) else "",
        "establishing": row.get("establishing") is True,
        # a map rendered from OpenStreetMap: its credit, drawn by the screen's core (never an act)
        "credit": row.get("credit") if isinstance(row.get("credit"), str) and _CREDIT.fullmatch(row["credit"]) else None,
        "alpha": row.get("alpha") is True,
        # where a real person is in it (18 Nov review: a seated cut-out in the egg pavilion was
        # mid-frame on s06_l03): None = nobody wrote it down -- a candidate is then refused
        "people": _boxes(row.get("people")),
        # the same for a catalogue picture shown at full size; both are keep-outs no slide shows
        "keep_out": _boxes(row.get("keep_out")) or [],
    }


def _boxes(raw: object) -> list[list[float]] | None:
    """A list of keep-out boxes [left, top, right, bottom] (fractions of the file); None when
    it is not one (a box that is not four ordered fractions makes the whole list None)."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        box = item.get("box") if isinstance(item, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                and 0 <= v <= 1 for v in box)
                and box[0] < box[2] and box[1] < box[3]):
            return None
        out.append([float(v) for v in box])
    return out


def asset_index() -> dict[str, dict]:
    """id -> entry of `assets/index.json` (empty with no file)."""
    folder = index_dir()
    target = None if folder is None else folder / "index.json"
    key = _stat(target)
    if _index_cache["key"] != key or key is None:
        doc: dict[str, dict] = {}
        if key is not None:
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.exception("ceremony slides: %s could not be read -- no full-size pictures, no clips", target)
                raw = None
            rows = raw.get("assets") if isinstance(raw, dict) else raw
            if isinstance(rows, dict):
                rows = [{**v, "id": v.get("id", k)} for k, v in rows.items() if isinstance(v, dict)]
            for row in rows if isinstance(rows, list) else []:
                entry = _index_entry(row, folder)
                if entry is not None:
                    doc.setdefault(entry["id"], entry)
        _index_cache.update(key=key, doc=doc)
    return _index_cache["doc"]


def _file_problem(entry: dict) -> str | None:
    """The entry's file is there and is the bytes the index pinned."""
    now = _file_sha(entry["file"])
    if now is None:
        return "asset_file_missing"
    return None if now == entry["sha256"] else "asset_changed"


def _depth_sha(entry: dict) -> str | None:
    """The depth map's hash when it may be used: the file is there and, when the index pins
    it, is those bytes."""
    depth = entry.get("depth")
    if not depth:
        return None
    now = _file_sha(depth["file"])
    if now is None or (depth["sha256"] is not None and depth["sha256"] != now):
        return None
    return now


def _hires(asset_id: str) -> dict | None:
    """The full-size file of a catalogue picture, when the index has one that may be used."""
    for entry in asset_index().values():
        if (entry["from_pin"] == asset_id and not entry["candidate"] and entry["kind"] == "picture"
                and entry["ok"] and _file_problem(entry) is None):
            return entry
    return None


def _described(asset_id: str) -> dict:
    """What a picture claims (`about`, `establishing`): the pictures' list first, the index
    entry's own description for a candidate the list does not have."""
    listed = asset_registry().get(asset_id)
    if listed is not None:
        return listed
    entry = asset_index().get(asset_id) or {}
    return {"about": entry.get("about") or [], "establishing": entry.get("establishing") is True}


def act_asset_problem(asset_id: object, *, video: bool = False) -> str | None:
    """Why an act slide may not show this picture / clip (None = it may)."""
    from app import stories

    if not isinstance(asset_id, str):
        return "bad_asset"
    pins = (stories.load_catalog() or {}).get("assets") or {}
    if asset_id in pins:
        return "not_a_video" if video else asset_problem(asset_id)
    entry = asset_index().get(asset_id)
    if entry is None or not entry["candidate"]:
        return "unknown_asset"
    if entry["kind"] == "audio":
        return "not_a_video" if video else "not_a_picture"          # (a sound is the reveal's, never a slide's)
    if (entry["kind"] == "video") != video:
        return "not_a_video" if video else "not_a_picture"
    listed = asset_registry().get(asset_id)
    if not entry["ok"] or (listed is not None and listed.get("ok") is not True):
        return "asset_excluded"
    if not entry["checked"]:
        return "asset_not_checked"
    if entry["people"] is None:
        return "people_not_checked"                # nobody wrote down whether a real person is in it
    why = _file_problem(entry)
    if why:
        return why
    if not _described(asset_id).get("about") or not ((listed or {}).get("shows") or entry["shows"]):
        return "asset_not_described"
    return None


def reveal_config() -> dict:
    """data/ceremony/reveal.json as it is now ({} when there is none or it is not an object)."""
    folder = index_dir()
    target = None if folder is None else folder.parent / REVEAL_FILE
    key = None if target is None else _stat(target)
    if _reveal_cache["key"] != key or key is None:
        doc: dict = {}
        if key is not None:
            try:
                raw = json.loads(target.read_text(encoding="utf-8"))
                doc = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                logger.warning("ceremony slides: %s is not JSON -- the debut plays silent", target)
        _reveal_cache.update(key=key, doc=doc)
    return _reveal_cache["doc"]


def _reveal_id(field: str, default: str) -> str:
    value = reveal_config().get(field, default)
    return value if isinstance(value, str) and _KEY.fullmatch(value) else default


def reveal_sound_id() -> str:
    return _reveal_id("sound", REVEAL_SOUND)


def reveal_video_id() -> str:
    return _reveal_id("video", REVEAL_VIDEO)


def reveal_sound_problem() -> str | None:
    """Why the debut's sound may not go to the screen (None = it may): the one sound reveal.json names, in
    the index as a sound, ok, its file the bytes the index pinned. Not gated on a person's `checked`
    (review 23 Nov: the mix script wrote that note itself -- now it records what it MEASURED, and the
    owner's `listened`, pinned to the bytes, is a warning on the card when missing:
    app/ceremony_reveal.warnings): a debut gone silent on the day because nobody ran a command is worse."""
    entry = asset_index().get(reveal_sound_id())
    if entry is None:
        return "no_reveal_sound"
    if entry["kind"] != "audio":
        return "not_a_sound"
    if not entry["ok"]:
        return "asset_excluded"
    return _file_problem(entry)


def _servable(entry: dict) -> bool:
    if not entry["ok"]:
        return False
    if entry["kind"] == "audio":
        return entry["id"] == reveal_sound_id() and reveal_sound_problem() is None
    if entry["candidate"]:
        return act_asset_problem(entry["id"], video=entry["kind"] == "video") is None
    return asset_problem(entry["from_pin"]) is None and _file_problem(entry) is None


def asset_file(sha256: str) -> tuple[Path, str] | None:
    """The file the screen may fetch at /ceremony-asset/<sha256>: a usable entry's own file
    or its depth map, as the bytes are now -- nothing else in that folder."""
    try:
        for entry in asset_index().values():
            if entry["sha256"] == sha256:
                if _servable(entry):
                    return entry["file"], _MEDIA[entry["file"].suffix.lower()]
            elif entry.get("depth") and _depth_sha(entry) == sha256 and _servable(entry):
                return entry["depth"]["file"], _MEDIA.get(entry["depth"]["file"].suffix.lower(), "image/png")
    except Exception:
        logger.exception("ceremony slides: asset %s", sha256)
    return None


def _index_facts(asset_id: str) -> list[dict]:
    """What a signature holds about the index's files behind one id (as they are now)."""
    out = []
    for entry in asset_index().values():
        if entry["id"] != asset_id and entry["from_pin"] != asset_id:
            continue
        out.append({"id": entry["id"], "pinned": entry["sha256"], "sha256": _file_sha(entry["file"]),
                    "depth": _depth_sha(entry), "candidate": entry["candidate"], "checked": entry["checked"],
                    "ok": entry["ok"], "about": entry["about"], "class": entry["class"],
                    "establishing": entry["establishing"], "credit": entry.get("credit"),
                    "keep_out": (entry.get("people") or []) + (entry.get("keep_out") or [])})
    return sorted(out, key=lambda row: row["id"])


def _act_picture(asset_id: str) -> dict:
    """A picture as an act's page gets it: the catalogue's copy (`src`), the full-size file
    when the index has one (`hires`), its depth map (`depth`) -- addresses on the screen's
    own listener, never a path."""
    from app import stories

    pin = ((stories.load_catalog() or {}).get("assets") or {}).get(asset_id)
    if pin is not None:
        out = {"src": "/story-asset/%s" % pin["sha256"], "w": int(pin["w"]), "h": int(pin["h"]),
               "alpha": bool(pin.get("alpha"))}
        full = _hires(asset_id)
        if full is not None:
            out.update(hires="/ceremony-asset/%s" % full["sha256"], hw=full["w"], hh=full["h"])
    else:
        full = asset_index()[asset_id]
        out = {"src": "/ceremony-asset/%s" % full["sha256"], "w": full["w"], "h": full["h"], "alpha": full["alpha"]}
    depth = _depth_sha(full) if full is not None else None
    if depth:
        out["depth"] = "/ceremony-asset/%s" % depth
        if full["depth"]["near"] == "dark":
            out["depth_near"] = "dark"
    credit = _credit(asset_id)
    if credit:
        out["credit"] = credit
    keep = keep_out(asset_id)
    if keep:
        out["keep_out"] = keep
    return out


def keep_out(asset_id: str) -> list[list[float]]:
    """Where a real person is in a picture (boxes, fractions of it): never on the screen.
    The same picture's catalogue copy and full-size file share one frame, so the boxes hold
    for whichever the screen shows."""
    out: list[list[float]] = []
    for entry in asset_index().values():
        if entry["id"] == asset_id or entry["from_pin"] == asset_id:
            out += (entry.get("people") or []) + (entry.get("keep_out") or [])
    return out


def _credit(asset_id: str) -> str | None:
    """The credit any index entry of this picture carries (its own, or its full-size file's)."""
    for entry in asset_index().values():
        if (entry["id"] == asset_id or entry["from_pin"] == asset_id) and entry.get("credit"):
            return entry["credit"]
    return None


def _act_video(asset_id: str) -> dict:
    entry = asset_index()[asset_id]
    return {"src": "/ceremony-asset/%s" % entry["sha256"], "w": entry["w"], "h": entry["h"],
            "type": _MEDIA[entry["file"].suffix.lower()]}


# ---- an act slide against its cue --------------------------------------------------------------

def _plain_option(value: object, depth: int) -> bool:
    """A number, a boolean, a short id, or an array/object of those: nothing that reads as
    words."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return value == value and abs(value) < 1e9                 # (not NaN, not infinite)
    if isinstance(value, str):
        return bool(_KEY.fullmatch(value))
    if depth <= 0:
        return False
    if isinstance(value, list):
        return len(value) <= 400 and all(_plain_option(v, depth - 1) for v in value)
    if isinstance(value, dict):
        return (len(value) <= 64 and all(isinstance(k, str) and _KEY.fullmatch(k) for k in value)
                and all(_plain_option(v, depth - 1) for v in value.values()))
    return False


def _options_problems(options: object) -> list[str]:
    if options is None:
        return []
    if not isinstance(options, dict):
        return ["bad_options"]
    bad = ["bad_options:%s" % key for key, value in options.items()
           if not (isinstance(key, str) and _KEY.fullmatch(key) and _plain_option(value, _OPTIONS_DEPTH - 1))]
    try:
        if len(json.dumps(options)) > _OPTIONS_CHARS:
            bad.append("options_too_big")
    except (TypeError, ValueError):
        bad.append("bad_options")
    return bad


def _act_maps(slide: dict, bad: list[str] | None = None) -> dict[str, dict]:
    """The four content maps with only their well-formed keys (the problems said in `bad`)."""
    seen: set[str] = set()
    maps: dict[str, dict] = {}
    for field, most in _ACT_MAPS.items():
        value = slide.get(field, {})
        maps[field] = {}
        if not isinstance(value, dict) or len(value) > most:
            if bad is not None:
                bad.append("bad_map:%s" % field)
            continue
        for key, item in value.items():
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                if bad is not None:
                    bad.append("bad_key:%s.%s" % (field, key))
            elif key in seen:
                if bad is not None:
                    bad.append("key_twice:%s" % key)
            else:
                seen.add(key)
                maps[field][key] = item
    return maps


def _beat_keys(beat: dict) -> list[str] | None:
    named = beat.get("key", [])
    named = [named] if isinstance(named, str) else named
    if not isinstance(named, list) or len(named) > _ACT_BEAT_KEYS or not all(isinstance(k, str) for k in named):
        return None
    return named


def _act_beats(slide: dict, cue: dict, lang: str = "en") -> tuple[list[dict], list[str]]:
    """An act slide's beats as the page gets them -- [{at: 0..1, do, key?, ...the act's own
    numbers}] in voice order -- and what is wrong with them."""
    raw = slide.get("beats", [])
    if not isinstance(raw, list):
        return [], ["bad_beats"]
    bad: list[str] = []
    if len(raw) > _ACT_BEATS:
        bad.append("too_many_beats")
    keys = {key for group in _act_maps(slide).values() for key in group}
    lines = {"en": norm(cue.get("en")), "th": norm(cue.get("th"))}
    order = ("en", "th") if lang == "en" else ("th", "en")
    last = {"en": -1.0, "th": -1.0, "at": -1.0}
    out: list[dict] = []
    for i, beat in enumerate(raw[:_ACT_BEATS]):
        if not isinstance(beat, dict):
            bad.append("bad_beat:%d" % i)
            continue
        do = beat.get("do")
        if not isinstance(do, str) or not _KEY.fullmatch(do):
            bad.append("bad_beat_do:%d" % i)
            continue
        named = _beat_keys(beat)
        if named is None:
            bad.append("bad_beat_key:%d" % i)
            continue
        if any(k not in keys for k in named):
            bad.extend("beat_shows_nothing:%s" % k for k in named if k not in keys)
            continue
        own = {name: value for name, value in beat.items() if name not in ("at", "at_en", "at_th", "do", "key")}
        wrong = [name for name, value in own.items()
                 if not (isinstance(name, str) and _KEY.fullmatch(name) and _plain_option(value, 2))]
        if wrong:
            bad.extend("bad_beat_field:%d.%s" % (i, name) for name in wrong)
            continue
        at = _beat_place(beat, "%d:%s" % (i, do), lines, last, bad, order)
        if at is None:
            continue
        out.append({**own, "at": round(float(at), 4), "do": do, **({"key": beat["key"]} if "key" in beat else {})})
    out.sort(key=lambda b: b["at"])          # stable: equal places keep the file's order
    return out, bad


def _sentence_end(line: str, at: float) -> int:
    """Where the sentence that the place `at` (0..1) is in ends."""
    index = min(len(line) - 1, max(0, int(round(at * len(line)))))
    for m in re.finditer(r"(?<=[.!?])\s+", line):
        if m.end() > index:
            return m.start()
    return len(line)


#: How many lines back a shot may be carried (s04_l11 -> s04_l13 -> s04_l15 is two).
_CONTINUES_MAX = 3


def _continued_lines(slide: dict, cue: dict, bad: list[str]) -> list[str]:
    """The English lines this slide's shot is CARRIED OVER from, nearest first ([] = none).

    Owner 18 Nov 2026, on s04_l13 (an abstract dot on dark water after Emma's dive into the
    pool): "ทำให้มันต่อเนื่องกันกับอันก่อนหน้าก็ได้ โดยให้หุ่นว่ายไปมายังดีกว่าอีก". The picture rule
    (a picture is backed by the words of ITS line) refused the pool there: "The day I'm
    completely waterproof, I'd love to go and show off a few laps" and "With pleasure, Khun
    Miki!" never say "pool" -- although both are the same exchange, in the same shot, as
    "Swimming in the 155-metre pool myself!". So a slide may say `"continues": "<cue id>"`:
    its pictures and clips may then be backed by the WHOLE line of the cue it continues
    (already said when this one starts), as well as by its own. Kept narrow on purpose --
    this is the owner choosing to hold a shot, not a way round the rule:
      * the cue named is the step DIRECTLY before this cue in its scene of the running order
        (no line, walk, clip or anything else between them);
      * that cue has a slide of the SAME act (a held shot is one act's);
      * it may itself continue the line before it, at most %d lines back in all;
      * nothing else is loosened: a caption is still of its picture, words are still not
        shown before she says them, `establishing` is as it was, and the slides file's
        signature covers the field like every other.
    Never raises; what is wrong with the claim is added to `bad`."""
    lines: list[str] = []
    seen = {cue.get("id")}
    here, claim = cue.get("id"), slide.get("continues")
    act = slide.get("act")
    try:
        doc = _doc() or {}
        slides = doc.get("slides") or {}
        order = event_script.order() or []
    except Exception:                                 # a broken file is said elsewhere
        slides, order = {}, []
    while claim is not None:
        if not isinstance(claim, str) or not _ID.fullmatch(claim) or claim in seen or len(lines) >= _CONTINUES_MAX:
            bad.append("bad_continues")
            break
        before = None
        for scene in order:
            steps = scene.get("steps") or []
            at = next((i for i, st in enumerate(steps) if st.get("do") == "cue" and st.get("cue") == here), None)
            if at is not None:
                prev = steps[at - 1] if at > 0 else None
                before = prev.get("cue") if prev and prev.get("do") == "cue" else None
                break
        if before != claim:
            bad.append("continues_not_the_line_before:%s" % claim)
            break
        earlier = slides.get(claim)
        if not isinstance(earlier, dict) or earlier.get("template") != ACT or earlier.get("act") != act:
            bad.append("continues_another_act:%s" % claim)
            break
        earlier_cue = event_script.cue(claim)
        if earlier_cue is None or not earlier_cue.get("en"):
            bad.append("continues_not_the_line_before:%s" % claim)
            break
        lines.append(norm(earlier_cue["en"]))
        seen.add(claim)
        here, claim = claim, earlier.get("continues")
    return lines


_continued_lines.__doc__ = _continued_lines.__doc__ % _CONTINUES_MAX


def _act_problems(slide: dict, cue: dict) -> list[str]:
    bad: list[str] = ["unknown_field:%s" % name for name in slide if name not in _ACT_FIELDS]
    name = slide.get("act")
    if not isinstance(name, str) or not _ACT_NAME.fullmatch(name):
        bad.append("bad_act")
    elif name not in acts():
        bad.append("unknown_act:%s" % name)
    maps = _act_maps(slide, bad)
    strings: list[str] = []
    icons: list[str] = []
    assets: list[str] = []
    shown: dict[str, list[str]] = {}                 # key -> the strings that come with it
    for field, kind in (("texts", "text"), ("figures", _ACT_FIGURE), ("pictures", _ACT_PICTURE), ("videos", _ACT_VIDEO)):
        for key, item in maps[field].items():
            before = len(strings)
            _check_value(kind, item, "%s.%s" % (field, key), strings, icons, bad, assets)
            shown[key] = strings[before:]
            if field == "figures" and isinstance(item, dict) and not (item.get("lead") or item.get("unit")):
                bad.append("bare_figure:%s" % key)
    bad += _strings_problems(strings, cue)
    said = _toks(cue.get("en"))
    bad += _bound([(row, "sentence") for row in maps["figures"].values() if isinstance(row, dict)],
                  strings, said, cue.get("en"))

    beats, beat_bad = _act_beats(slide, cue)
    appears: dict[str, float] = {}
    for beat in beats:                               # in voice order: the first is the earliest
        for key in _beat_keys(beat) or []:
            appears.setdefault(key, beat["at"])
    line = norm(cue.get("en"))
    whole = _words(line)
    numbers = {t for t in said if t[0].isdigit()}
    for key, texts in shown.items():
        reached = _toks(line[:_sentence_end(line, appears.get(key, 0.0))])
        words = {t for t in reached if not t[0].isdigit()}
        for text in texts:
            for tok in _toks(text):
                if tok[0].isdigit():
                    early = tok in numbers and tok not in reached
                else:
                    early = tok not in ALLOWED_WORDS and _in_line(tok, whole) and not _in_line(tok, words)
                if early:
                    bad.append("words_before_she_says_them:%s:%s" % (key, tok))

    carried = _continued_lines(slide, cue, bad) if "continues" in slide else []
    for field in ("pictures", "videos"):
        for key, item in maps[field].items():
            asset = item.get("asset") if isinstance(item, dict) else None
            if not isinstance(asset, str) or not asset:
                continue                                           # (said already: bad_asset)
            why = act_asset_problem(asset, video=field == "videos")
            if why:
                bad.append("%s:%s" % (why, asset))
                continue
            entry = _described(asset)
            # (a shot carried over from the line before -- `continues` -- is backed by that line too)
            held = any(_backed(entry, _toks(earlier)) for earlier in carried)
            if key in appears:
                if not held and not _backed_at(entry, _toks(_sentence_at(line, appears[key]))):
                    bad.append("asset_not_near_its_beat:%s" % asset)
            elif not held and entry.get("establishing") is not True and not _backed(entry, _toks(line)):
                bad.append("asset_not_in_line:%s" % asset)
            caption = item.get("caption")
            if isinstance(caption, str) and caption.strip() and not _backed(entry, _toks(caption)):
                bad.append("caption_not_of_its_picture:%s" % asset)
    bad += _options_problems(slide.get("options"))
    return bad + beat_bad


def _act_payload(slide: dict, cue: dict, lang: str = "en") -> dict:
    """A clean act slide as the page gets it: every map rebuilt field by field."""
    maps = _act_maps(slide)
    act = acts()[slide["act"]]
    out: dict = {"template": ACT, "act": slide["act"], "v": act["v"], "css": act["css"],
                 "texts": {k: norm(v) for k, v in maps["texts"].items()},
                 "figures": {k: {f: norm(row[f]) for f in ("lead", "value", "unit", "label") if f in row}
                             for k, row in maps["figures"].items()},
                 "pictures": {}, "videos": {}}
    for key, row in maps["pictures"].items():
        picture = _act_picture(row["asset"])
        if "caption" in row:
            picture["caption"] = norm(row["caption"])
        if "fit" in row:
            picture["fit"] = row["fit"]
        out["pictures"][key] = picture
    for key, row in maps["videos"].items():
        out["videos"][key] = _act_video(row["asset"])
    out["beats"] = _act_beats(slide, cue, lang)[0]
    out["said"] = _said_spans(out["texts"], cue, lang)
    out["options"] = json.loads(json.dumps(slide.get("options") or {}))
    return out


def _said_spans(texts: dict, cue: dict, lang: str = "en") -> dict:
    """Where each text is in the line she says, as [from, to] fractions of it -- for the
    core's typed text, which never shows a letter her voice has not reached (review 18 Nov:
    s06_l07's punchline was typed on a wall clock from "I'm sure", ahead of "first place").
    A text that is not a piece word for word ("Prepare a quotation for this unit?" of "...prepare
    a quotation for this unit as well?") spans from its first word to its last, in order; one
    whose words are not all in the line has no span (it types on its own time)."""
    line = norm(cue.get("en") if lang == "en" else cue.get("th"))
    out: dict[str, list[float]] = {}
    if not line:
        return out
    low = line.lower() if len(line.lower()) == len(line) else line
    for key, text in texts.items():
        want = norm(text)
        if not want:
            continue
        at = low.find(want.lower() if low is not line else want)
        if at >= 0:
            out[key] = [round(at / len(line), 4), round((at + len(want)) / len(line), 4)]
            continue
        start = end = None
        for word in re.findall(r"[^\W_]+(?:['\u2019][^\W_]+)*", want.lower() if low is not line else want):
            m = re.search(r"(?<![^\W_])%s(?![^\W_])" % re.escape(word), low[end or 0:])
            if not m:
                start = None
                break
            start = (end or 0) + m.start() if start is None else start
            end = (end or 0) + m.end()
        if start is not None and end is not None:
            out[key] = [round(start / len(line), 4), round(end / len(line), 4)]
    return out
