#!/usr/bin/env python3
"""Build the robot face's story pictures and catalog (app/stories.py).

    python scripts/build_story_assets.py                # pictures + pins + stories.json
    python scripts/build_story_assets.py --merge-only   # stories.json from design/ + pins
    python scripts/build_story_assets.py --check        # report only, write nothing
    python scripts/build_story_assets.py --root DIR --sources DIR

Three inputs under data/projects/<project>/stories/ (all tracked, all small):

  recipes/*.json   [{"id", "source", "crop", "max_w", "format", "why"}, ...]
                   -- how each picture is cut from the owner's material in
                   .tmp/story-sources (gitignored: raw files never enter git).
                   A PDF source takes "page" (1-based) and "dpi".
  design/*.json    the stories, as the designers wrote them
                   (docs/emma-story-engine.md has the format)

Two outputs:

  assets/<id>.<format>   the pictures, cropped and pre-sized for a 1080x1920
                         screen (gitignored; `robot_server.py deploy` ships
                         them when STORIES_ENABLED is on in robot.env)
  asset_pins.json        sha256 / size / dimensions / source of each picture
  stories.json           the catalog the server reads: the stories, the pins
                         they use, and the approval fields -- as a DRAFT.
                         Only `scripts/approve_stories.py --by NAME` (a person)
                         approves it; an edit after that is a draft again.

Deterministic: the same inputs give the same bytes (no timestamps, fixed
encoder settings, sorted output). Refuses, by name, any source that is a
quotation sheet (prices), the rooftop or Thermal Galaxy (owner overruled).

The report under each story is the review: structure problems (the story is
not offered), and numbers that do not appear in any source the story cites
(each number on screen or in her mouth must come from the approved facts,
the approved report chapters or the approved slides).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import stories  # noqa: E402
from app.config import ACTIVE_PROJECT_ID  # noqa: E402

DEFAULT_SOURCES = ROOT / ".tmp" / "story-sources"
#: A pre-sized picture should load and decode fast on the robot.
TARGET_BYTES = 250 * 1024
MAX_SIDE = 2400

DRAFT = {
    "approval_status": "draft",
    "approved_by": "",
    "approved_at": "",
    "effective_at": "",
    "disclosure_scope": "customer",
    # The facts' own state for the facilities; its instruction is the owner's
    # "never say it is a preliminary concept" (knowledge_policy.state_instruction).
    "content_state": "preliminary_concept",
    "approved_sha256": "",
}


class RecipeError(ValueError):
    pass


# ---- recipes -> pictures ------------------------------------------------------------

def load_recipes(root: Path) -> list[dict]:
    recipes: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted((root / "recipes").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("assets", []) if isinstance(data, dict) else []
        for item in items:
            if not isinstance(item, dict):
                raise RecipeError("%s: every recipe must be an object" % path.name)
            rid = item.get("id")
            if rid in seen:
                raise RecipeError("asset id %r is in both %s and %s" % (rid, seen[rid], path.name))
            seen[str(rid)] = path.name
            recipes.append(dict(item, _file=path.name))
    return recipes


def check_recipe(recipe: dict, sources: Path) -> Path:
    """The source file this recipe may use; raises on anything not allowed."""
    rid = recipe.get("id")
    if not isinstance(rid, str) or not stories._ASSET_ID.fullmatch(rid):
        raise RecipeError("bad asset id %r" % (rid,))
    src = recipe.get("source")
    if not isinstance(src, str) or not src or Path(src).is_absolute() or "\\" in src:
        raise RecipeError("%s: source must be a relative path from the sources folder" % rid)
    if stories.BANNED_SOURCE.search(src) or stories.BANNED_SOURCE.search(rid):
        # Quotation sheets carry prices; the rooftop and Thermal Galaxy were
        # overruled by the owner. Refused by name, before any pixel is read.
        raise RecipeError("%s: source %r is not allowed on a guest's screen" % (rid, src))
    path = (sources / src).resolve()
    # The owner's material (.tmp/story-sources), or this project's own data
    # in the repo (the approved slides, the sha-pinned unit plans) -- written
    # "../../data/projects/<project>/..." from the sources folder.
    project = (ROOT / "data" / "projects" / ACTIVE_PROJECT_ID).resolve()
    if not (path.is_relative_to(sources.resolve()) or path.is_relative_to(project)):
        raise RecipeError("%s: source must be in the sources folder or this project's data" % rid)
    if not path.is_file():
        raise RecipeError("%s: source not found: %s" % (rid, src))
    pin = recipe.get("source_sha256")
    if pin is not None and hashlib.sha256(path.read_bytes()).hexdigest() != pin:
        raise RecipeError("%s: source changed since the recipe pinned it (source_sha256)" % rid)
    fmt = recipe.get("format", "webp")
    if fmt not in stories.FORMATS:
        raise RecipeError("%s: format must be one of %s" % (rid, sorted(stories.FORMATS)))
    return path


#: The architect's dimension layers in the unit drawings (260615_EMB3_UNIT.pdf).
DIMENSION_LAYERS = ("DIMC", "DIMENSION")


def _vector_points(drawing: dict) -> list:
    import fitz

    out = []
    for item in drawing["items"]:
        if item[0] == "re":
            r = item[1]
            out += [fitz.Point(r.x0, r.y0), fitz.Point(r.x1, r.y0), fitz.Point(r.x0, r.y1), fitz.Point(r.x1, r.y1)]
        elif item[0] == "qu":
            q = item[1]
            out += [q.ul, q.ur, q.ll, q.lr]
        else:
            out += [p for p in item[1:] if isinstance(p, fitz.Point)]
    return out


def _without_dimensions(page, recipe: dict):
    """A unit plan with no dimensions on it (owner 27 Oct, on the robot's room
    types page: "รูปห้องไม่ต้องแสดง dimension ... ภาพเล็ก ไม่ต้องแสดงไดเมนชั่น").

    The plan is drawn again from the page's own vector paths into a new page:
    the dimension layers left out, and what the architect drew on layer "0"
    for them -- a thin line ending on a dimension tick (its dimension or
    extension line) -- and anything mostly outside the walls (a slanted
    wall's dimension lies inside the walls' box, so the box alone does not
    catch it; the CAD wipeouts that cover the walls lie outside it). Text is
    not drawn at all: the figures, and the title the row already says.
    Returns (document, page) -- keep the document open while rendering."""
    import fitz

    s = 72.0 / int(recipe.get("dpi", 120))
    area = fitz.Rect(*[v * s for v in recipe["crop"]])
    # (padded: a straight line's rect has no area, and an empty Rect
    # "intersects" nothing in PyMuPDF -- review 27 Oct: every horizontal or
    # vertical one-segment path was gone, window glass and fixtures included)
    def box(d):
        return fitz.Rect(d["rect"]) + (-0.01, -0.01, 0.01, 0.01)

    drawings = [d for d in page.get_drawings() if box(d).intersects(area)]
    walls = [fitz.Rect(d["rect"]) for d in drawings if d.get("layer") == "WALL"]
    if not walls:
        raise RecipeError("%s: strip_dimensions found no WALL layer inside the crop" % recipe["id"])
    wall = walls[0]
    for w in walls[1:]:
        wall |= w
    zone = wall + (-4, -4, 4, 4)
    ticks = [fitz.Rect(d["rect"]) + (-1.5, -1.5, 1.5, 1.5) for d in drawings
             if d.get("layer") in DIMENSION_LAYERS]
    def in_zone(d) -> bool:
        rect = fitz.Rect(d["rect"])
        if d.get("layer") in DIMENSION_LAYERS or not box(d).intersects(zone):
            return False
        if rect.get_area() > 0:
            return (rect & zone).get_area() >= 0.6 * rect.get_area()
        return zone.contains(rect.tl) and zone.contains(rect.br)

    # The walls as segments (thick strokes: the cut walls, their outlines and
    # the columns), for "is this point inside the room": a slanted wall's own
    # reference lines run beside it, inside the walls' box (STUDIO B107).
    segments = []
    for d in drawings:
        # (in the zone only: the page's frame is a thick stroke too, and every
        # ray from anywhere meets it)
        if (d.get("width") or 0) < 0.4 or not in_zone(d):
            continue
        for item in d["items"]:
            if item[0] == "l":
                segments.append((item[1], item[2]))
            elif item[0] == "re":
                r = item[1]
                corners = [fitz.Point(r.x0, r.y0), fitz.Point(r.x1, r.y0), fitz.Point(r.x1, r.y1), fitz.Point(r.x0, r.y1)]
                segments += [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
            elif item[0] == "qu":
                q = item[1]
                corners = [q.ul, q.ur, q.lr, q.ll]
                segments += [(corners[i], corners[(i + 1) % 4]) for i in range(4)]

    def enclosed(p) -> bool:
        """Every axis ray from p meets a wall before leaving the plan."""
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            hit = False
            for a, b in segments:
                if dx:      # horizontal ray: does segment a-b cross y = p.y on that side?
                    if (a.y - p.y) * (b.y - p.y) > 0 or a.y == b.y:
                        continue
                    x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
                    if (x - p.x) * dx > 0:
                        hit = True
                        break
                else:
                    if (a.x - p.x) * (b.x - p.x) > 0 or a.x == b.x:
                        continue
                    y = a.y + (p.x - a.x) * (b.y - a.y) / (b.x - a.x)
                    if (y - p.y) * dy > 0:
                        hit = True
                        break
            if not hit:
                return False
        return True
    doc = fitz.open()
    new = doc.new_page(width=page.rect.width, height=page.rect.height)
    shape = new.new_shape()
    for d in drawings:
        if not in_zone(d):
            continue
        if ((d.get("width") or 0) < 0.4 and d.get("layer") == "0"
                and any(t.contains(p) for p in _vector_points(d) for t in ticks)):
            continue
        if ((d.get("width") or 0) < 0.4 and d.get("layer") == "0" and len(d["items"]) == 1
                and d["items"][0][0] == "l"):
            a, b = d["items"][0][1], d["items"][0][2]
            diagonal = min(abs(b.x - a.x), abs(b.y - a.y)) > 3 and abs(b - a) > 20
            if diagonal and not enclosed(fitz.Point((a.x + b.x) / 2, (a.y + b.y) / 2)):
                continue
        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                shape.draw_line(item[1], item[2])
            elif kind == "c":
                shape.draw_bezier(item[1], item[2], item[3], item[4])
            elif kind == "re":
                shape.draw_rect(item[1])
            elif kind == "qu":
                shape.draw_quad(item[1])
        cap = d.get("lineCap")
        shape.finish(fill=d.get("fill"), color=d.get("color"), width=d.get("width") or 0,
                     lineCap=max(cap) if isinstance(cap, (list, tuple)) else (cap or 0),
                     lineJoin=d.get("lineJoin") or 0, closePath=d.get("closePath", False),
                     even_odd=d.get("even_odd", False), fill_opacity=d.get("fill_opacity") or 1,
                     stroke_opacity=d.get("stroke_opacity") or 1, dashes=d.get("dashes"))
    shape.commit()
    return doc, new


def _open(path: Path, recipe: dict):
    from PIL import Image

    if path.suffix.lower() == ".pdf":
        import fitz

        page_no = int(recipe.get("page", 1))
        dpi = int(recipe.get("dpi", 120))
        with fitz.open(path) as doc:
            if not 1 <= page_no <= doc.page_count:
                raise RecipeError("%s: page %d not in %s" % (recipe["id"], page_no, path.name))
            page = doc[page_no - 1]
            if recipe.get("strip_dimensions"):
                if not recipe.get("crop"):
                    raise RecipeError("%s: strip_dimensions needs a crop (which plan of the page)" % recipe["id"])
                drawn, page = _without_dimensions(page, recipe)
                with drawn:
                    pix = page.get_pixmap(dpi=dpi, alpha=False)
            else:
                pix = page.get_pixmap(dpi=dpi, alpha=False)
            return Image.open(io.BytesIO(pix.tobytes("png")))
    image = Image.open(path)
    image.load()
    return image


def render(recipe: dict, sources: Path) -> tuple[bytes, int, int]:
    """(encoded bytes, width, height) of one recipe. Same input, same bytes.
    Whether it kept transparency: `has_alpha(data)`."""
    from PIL import Image

    path = check_recipe(recipe, sources)
    image = _open(path, recipe)
    icc = image.info.get("icc_profile")
    crop = recipe.get("crop")
    if crop is not None:
        if (not isinstance(crop, list) or len(crop) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in crop)):
            raise RecipeError("%s: crop must be [x0, y0, x1, y1] in source pixels" % recipe["id"])
        x0, y0, x1, y1 = crop
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise RecipeError("%s: crop %s outside the %dx%d source" % (recipe["id"], crop,
                                                                       image.width, image.height))
        image = image.crop((x0, y0, x1, y1))
    fmt = recipe.get("format", "webp")
    keep_alpha = fmt in ("png", "webp") and (image.mode in ("RGBA", "LA")
                                             or (image.mode == "P" and "transparency" in image.info))
    image = image.convert("RGBA" if keep_alpha else "RGB")
    for op in recipe.get("post") or []:
        image = _post(image, op, recipe["id"], fmt)
    max_w = int(recipe.get("max_w", 1080))
    max_h = int(recipe.get("max_h", 1920))
    if not (64 <= max_w <= MAX_SIDE and 64 <= max_h <= MAX_SIDE):
        raise RecipeError("%s: max_w / max_h must be 64..%d" % (recipe["id"], MAX_SIDE))
    scale = min(1.0, max_w / image.width, max_h / image.height)
    if scale < 1.0:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(size, Image.LANCZOS)
    budget = int(recipe.get("max_kb", TARGET_BYTES // 1024)) * 1024
    quality = int(recipe.get("quality", 82))
    while True:
        out = io.BytesIO()
        extra = {"icc_profile": icc} if icc else {}
        if fmt == "webp":
            image.save(out, "WEBP", quality=quality, method=6, **extra)
        elif fmt == "jpg":
            image.save(out, "JPEG", quality=quality, optimize=True, progressive=True, **extra)
        else:
            image.save(out, "PNG", optimize=True, **extra)
        data = out.getvalue()
        if fmt == "png" or len(data) <= budget or quality <= 50:
            return data, image.width, image.height
        quality -= 6


_LINES_TO_ALPHA = re.compile(r"lines_to_alpha:#([0-9a-fA-F]{6})\Z")
_INPAINT = re.compile(r"inpaint:(\d+),(\d+),(\d+),(\d+)\Z")
_FILL = re.compile(r"fill:(\d+),(\d+),(\d+),(\d+)@(\d+),(\d+)\Z")
_INSERT = re.compile(r"insert:(\d+),(\d+),(\d+),(\d+)@(\d+):(\d+),(\d+)\Z")


def _post(image, op: object, rid: str, fmt: str):
    """The few picture operations a recipe may ask for.

    lines_to_alpha:#RRGGBB -- a black-on-white drawing (a unit plan) becomes
    lines of that colour on transparent, alpha = how dark the line was: the
    plans sit on the dark stage the way the prototype drew them.

    inpaint:x0,y0,x1,y1 -- (in the CROPPED picture's pixels) each row of the
    box is filled by a straight blend between the pixels just left and just
    right of it: a thin baked-in mark -- a callout's leader line, its dot --
    goes, the plan around it stays (review 12 Oct: a website pointer ran from
    the top edge into the Aqua Cinema pool on the basement plan). Only for
    thin marks; a wide box smears.

    fill:x0,y0,x1,y1@sx,sy -- the box takes the colour of the pixel (sx, sy):
    where a mark crosses printed text, blending from the letters beside it
    smears them, and the plain ground the text sits on is the honest fill.

    insert:x0,y0,x1,y1@at:sx0,sx1 -- a letter missing from a printed label:
    columns sx0..sx1 of the band (the same letter elsewhere in that line, so
    the same font, size and colour) are copied in before column `at`, then
    the band is squeezed back to its own width so the label keeps its box
    (owner 13 Oct: the basement plan's "CRYTAL STONE SAUNA" -> CRYSTAL, the
    picture itself kept as printed). Everything inside the band; at most 15%
    wider before the squeeze, or the letters visibly thin."""
    from PIL import Image

    insert = _INSERT.fullmatch(op) if isinstance(op, str) else None
    if insert is not None:
        x0, y0, x1, y1, at, sx0, sx1 = (int(v) for v in insert.groups())
        width = x1 - x0 + 1
        if not (0 <= x0 < at <= x1 < image.width and 0 <= y0 <= y1 < image.height
                and x0 <= sx0 <= sx1 <= x1 and y1 - y0 < 80
                and (sx1 - sx0 + 1) * 100 <= width * 15):
            raise RecipeError("%s: insert %s outside the picture, outside its band, "
                              "or more than 15%% of the band" % (rid, op))
        band = image.crop((x0, y0, x1 + 1, y1 + 1))
        letter = image.crop((sx0, y0, sx1 + 1, y1 + 1))
        cut = at - x0
        grown = Image.new(band.mode, (width + letter.width, band.height))
        grown.paste(band.crop((0, 0, cut, band.height)), (0, 0))
        grown.paste(letter, (cut, 0))
        grown.paste(band.crop((cut, 0, width, band.height)), (cut + letter.width, 0))
        image.paste(grown.resize((width, band.height), Image.LANCZOS), (x0, y0))
        return image
    fill = _FILL.fullmatch(op) if isinstance(op, str) else None
    if fill is not None:
        x0, y0, x1, y1, sx, sy = (int(v) for v in fill.groups())
        if not (0 <= x0 <= x1 < image.width and 0 <= y0 <= y1 < image.height
                and 0 <= sx < image.width and 0 <= sy < image.height) or (x1 - x0) * (y1 - y0) > 2000:
            raise RecipeError("%s: fill box %s outside the picture or larger than 2000 px" % (rid, op))
        px = image.load()
        colour = px[sx, sy]
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                px[x, y] = colour
        return image
    paint = _INPAINT.fullmatch(op) if isinstance(op, str) else None
    if paint is not None:
        x0, y0, x1, y1 = (int(v) for v in paint.groups())
        if not (1 <= x0 <= x1 < image.width - 1 and 0 <= y0 <= y1 < image.height) or x1 - x0 > 40:
            raise RecipeError("%s: inpaint box %s outside the picture or wider than 40 px" % (rid, op))
        px = image.load()
        span = x1 - x0 + 2
        for y in range(y0, y1 + 1):
            left, right = px[x0 - 1, y], px[x1 + 1, y]
            for x in range(x0, x1 + 1):
                t = (x - x0 + 1) / span
                px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(left, right))
        return image
    match = _LINES_TO_ALPHA.fullmatch(op) if isinstance(op, str) else None
    if match is None:
        raise RecipeError("%s: unknown post step %r" % (rid, op))
    if fmt not in ("webp", "png"):
        raise RecipeError("%s: lines_to_alpha needs webp or png (transparency)" % rid)
    rgb = tuple(int(match.group(1)[k:k + 2], 16) for k in (0, 2, 4))
    gray = image.convert("L")
    alpha = gray.point(lambda v: 255 - v)
    out = Image.new("RGBA", image.size, rgb + (0,))
    out.putalpha(alpha)
    return out


def has_alpha(data: bytes) -> bool:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        return image.mode in ("RGBA", "LA") or "transparency" in image.info


def build_assets(root: Path, sources: Path, *, write: bool = True) -> tuple[dict, list[str]]:
    """Every recipe rendered; returns (pins, problems). Writes assets/ and
    asset_pins.json unless `write` is false."""
    pins: dict[str, dict] = {}
    problems: list[str] = []
    out_dir = root / "assets"
    for recipe in load_recipes(root):
        try:
            data, width, height = render(recipe, sources)
            alpha = has_alpha(data)
        except (RecipeError, OSError, ValueError) as exc:
            problems.append("%s (%s): %s" % (recipe.get("id"), recipe["_file"], exc))
            continue
        fmt = recipe.get("format", "webp")
        name = "%s.%s" % (recipe["id"], fmt)
        pins[recipe["id"]] = {
            "file": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "w": width,
            "h": height,
            "format": fmt,
            "source": recipe["source"],
            "crop": recipe.get("crop"),
            # transparent (lines_to_alpha plans): the page draws it on dark, not white
            "alpha": alpha,
        }
        if len(data) > TARGET_BYTES:
            problems.append("%s: %d KB (> %d KB budget even at the lowest quality)"
                            % (recipe["id"], len(data) // 1024, TARGET_BYTES // 1024))
        if write:
            out_dir.mkdir(parents=True, exist_ok=True)
            target = out_dir / name
            if not target.is_file() or target.read_bytes() != data:
                target.write_bytes(data)
    pins = dict(sorted(pins.items()))
    if write:
        (root / "asset_pins.json").write_text(json.dumps(pins, ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")
    return pins, problems


# ---- design + pins -> catalog --------------------------------------------------------

def load_designs(root: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted((root / "design").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("stories"), list):
            data = data["stories"]
        out.extend(data if isinstance(data, list) else [data])
    return out


def inline_maps(designs: list[dict], root: Path) -> dict[str, list[str]]:
    """The real maps (template "map", owner 27 Oct): each story's overlay --
    where the project, its places and their routes lie on the picture, as
    scripts/render_story_maps.py projected them -- written into its params.
    The overlay must have been computed on the very picture the recipe pins
    (its PNG's sha256): a map re-rendered without its overlay, or the other
    way round, puts every pin on the wrong street, silently. Returns the
    problems per story id; a story with one is not offered."""
    problems: dict[str, list[str]] = {}
    try:
        recipes = {r["id"]: r for r in load_recipes(root)}
    except (RecipeError, OSError, ValueError) as exc:
        recipes = {}
        problems["*"] = ["recipes unreadable: %s" % exc]
    for story in designs:
        if not isinstance(story, dict) or story.get("template") != "map":
            continue
        sid = str(story.get("id"))
        params = story.get("params") if isinstance(story.get("params"), dict) else {}
        mine = problems.setdefault(sid, [])
        name = params.get("overlay") or sid
        try:
            over = json.loads((root / "maps" / ("%s.json" % name)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mine.append("map overlay maps/%s.json missing -- run scripts/render_story_maps.py" % name)
            continue
        recipe = recipes.get(str(params.get("map_asset")))
        if not recipe or recipe.get("source_sha256") != over.get("image_sha256"):
            mine.append("map overlay %s was computed on another picture than %s pins -- render both again"
                        % (name, params.get("map_asset")))
            continue
        params["aspect"] = round(over["w"] / over["h"], 4)
        params["project"] = over["project"]
        params["attribution_en"] = over["attribution_en"]
        for row in params.get("rows") or []:
            if not isinstance(row, dict) or not row.get("place"):
                continue
            for key in ("pin", "route", "walk_route"):
                row.pop(key, None)
            place = (over.get("places") or {}).get(row["place"])
            if not place:
                continue                     # stories.problems names the row
            row["pin"] = {"x": place["x"], "y": place["y"]}
            for key in ("route", "walk_route"):
                if place.get(key):
                    row[key] = place[key]
    return problems


def _numbers(text: str) -> set[str]:
    return {stories._plain(m.replace(",", "")) for m in stories._NUMBER.findall(text or "")}


def _source_texts(project_id: str) -> dict:
    """What a citation may point at: the approved facts by label, the
    approved report chapters by file, the approved slides by id."""
    base = ROOT / "data" / "projects" / project_id
    facts: dict[str, str] = {}
    try:
        data = json.loads((base / "facts" / "condo_facts.json").read_text(encoding="utf-8"))
        for item in data.get("facts") or []:
            if isinstance(item, dict) and item.get("label"):
                facts[str(item["label"])] = str(item.get("value") or "")
    except (OSError, ValueError):
        pass
    report: dict[str, str] = {}
    try:
        manifest = json.loads((base / "sales" / "project_report_approval.json").read_text(encoding="utf-8"))
        folder = ROOT / str(manifest.get("folder") or "")
        for name in manifest.get("files") or {}:
            try:
                report[name] = (folder / name).read_text(encoding="utf-8")
            except OSError:
                report[name] = ""
    except (OSError, ValueError):
        pass
    slides: dict[str, str] = {}
    try:
        index = json.loads((base / "presentations" / "slides" / "index.json").read_text(encoding="utf-8"))
        for slide in index.get("images") or index.get("slides") or []:
            if isinstance(slide, dict) and slide.get("id"):
                slides[str(slide["id"])] = json.dumps(slide, ensure_ascii=False)
    except (OSError, ValueError):
        pass
    sales: dict[str, str] = {}
    for rel in ("qa_script.json", "unit-plans/index.json"):
        try:
            text = (base / "sales" / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        sales[rel] = text
        sales["sales/" + rel] = text
    return {"facts": facts, "report": report, "slides": slides, "sales": sales}


def citation_report(story: dict, texts: dict) -> tuple[list[str], set[str]]:
    """(citations that point at nothing approved, numbers no citation carries)."""
    bad: list[str] = []
    carried: set[str] = set()
    for source in story.get("sources") or []:
        kind, _, rest = str(source).partition(":")
        # "facts:<label> (what was taken from it)" -- a label is the text up to
        # "#" or " (" -- and "report:<file>#<section>" / "report:<file> line 7 (...)"
        # / "slides:intro-041 (...)": a file or id is the first word.
        rest = rest.strip()
        key = (re.split(r"#|\s\(", rest, maxsplit=1)[0] if kind == "facts"
               else re.split(r"[#\s(]", rest, maxsplit=1)[0]).strip()
        if kind == "facts":
            if key not in texts["facts"]:
                bad.append(source)
            carried |= _numbers(texts["facts"].get(key, ""))
        elif kind == "report":
            if key not in texts["report"]:
                bad.append(source + " (not an approved chapter)")
            carried |= _numbers(texts["report"].get(key, ""))
        elif kind == "slides":
            if key not in texts["slides"]:
                bad.append(source)
            carried |= _numbers(texts["slides"].get(key, ""))
        elif kind in ("sales", "plans"):
            # the curated Q&A script / the sha-pinned unit-plan index
            name = key
            if name not in texts["sales"]:
                bad.append(source)
            carried |= _numbers(texts["sales"].get(name, ""))
        elif kind in ("web", "ue", "asset", "assets"):
            continue                      # picture provenance, carries no claim
        else:
            bad.append(source + " (unknown kind)")
    return bad, stories.claim_numbers(story) - carried


def merge(root: Path, *, write: bool = True, project_id: str = ACTIVE_PROJECT_ID,
          pins: dict | None = None) -> tuple[dict, dict]:
    """(catalog, report) from design/*.json + asset_pins.json (or `pins`, the
    ones just rendered in memory). The catalog is a draft unless it is
    byte-for-byte the content that was approved."""
    in_memory = pins is not None
    if pins is None:
        try:
            pins = json.loads((root / "asset_pins.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pins = {}
    designs = load_designs(root)
    map_problems = inline_maps(designs, root)
    used: set[str] = set()
    for story in designs:
        if isinstance(story, dict) and isinstance(story.get("params"), dict):
            used.update(stories.asset_refs(story["params"]))
    assets = {k: pins[k] for k in sorted(used) if k in pins}
    catalog = {"source_id": stories.SOURCE_ID, "project_id": project_id, "schema": stories.SCHEMA}
    catalog.update(DRAFT)
    catalog["stories"] = designs
    catalog["assets"] = assets
    previous = None
    try:
        previous = json.loads((root / stories.CATALOG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    digest = stories.content_digest(catalog)
    if (isinstance(previous, dict) and previous.get("approval_status") == "approved"
            and previous.get("approved_sha256") == digest):
        # Nothing changed since a person approved it: re-merging keeps it.
        for key in ("approval_status", "approved_by", "approved_at", "effective_at",
                    "disclosure_scope", "content_state", "approved_sha256", "expires_at",
                    "approval_note"):
            if key in previous:
                catalog[key] = previous[key]
    texts = _source_texts(project_id)
    report: dict[str, dict] = {}
    ids = [s.get("id") for s in designs if isinstance(s, dict)]
    for story in designs:
        sid = story.get("id") if isinstance(story, dict) else None
        entry = {"problems": stories.problems(story, assets) + map_problems.get(str(sid), []),
                 "unsourced": [], "bad_sources": []}
        if isinstance(story, dict):
            for ref in stories.asset_refs(story.get("params") or {}):
                # pins rendered in memory (--check) have no file on disk to re-hash
                if ref in assets and not in_memory:
                    why = stories.asset_problem(ref, assets[ref], root / "assets")
                    if why:
                        entry["problems"].append("asset %s: %s" % (ref, why))
            for tap in story.get("taps") or []:
                nxt = tap.get("next_story") if isinstance(tap, dict) else None
                if nxt and nxt not in ids:
                    entry["problems"].append("tap %s leads to unknown story %r" % (tap.get("id"), nxt))
            bad, unsourced = citation_report(story, texts)
            entry["bad_sources"] = bad
            entry["unsourced"] = sorted(unsourced, key=lambda x: (len(x), x))
        if ids.count(sid) > 1:
            entry["problems"].append("id defined %d times" % ids.count(sid))
        report[str(sid)] = entry
    if write:
        (root / stories.CATALOG_NAME).write_text(
            json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return catalog, report


def print_report(report: dict, asset_problems: list[str]) -> int:
    hard = 0
    for line in asset_problems:
        print("ASSET  " + line)
        hard += 1
    for sid, entry in report.items():
        state = "OK" if not entry["problems"] else "NOT OFFERED"
        print("%-28s %s" % (sid, state))
        for line in entry["problems"]:
            print("    problem: " + line)
            hard += 1
        for line in entry["bad_sources"]:
            print("    source:  " + line)
        if entry["unsourced"]:
            print("    WARN numbers not found in any cited source: " + ", ".join(entry["unsourced"]))
    return hard


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=stories.folder())
    ap.add_argument("--sources", type=Path, default=DEFAULT_SOURCES)
    ap.add_argument("--merge-only", action="store_true", help="skip the pictures, rebuild stories.json")
    ap.add_argument("--check", action="store_true", help="report only, write nothing")
    ap.add_argument("--strict", action="store_true", help="fail on unsourced numbers too")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")      # Thai in the report on Windows
    except (AttributeError, ValueError):
        pass
    root = args.root.resolve()
    if not (root / "design").is_dir() and not (root / "recipes").is_dir():
        print("nothing to build: %s has no design/ or recipes/" % root)
        return 1
    asset_problems: list[str] = []
    pins = None
    if not args.merge_only:
        try:
            pins, asset_problems = build_assets(root, args.sources.resolve(), write=not args.check)
        except RecipeError as exc:
            print("recipes: %s" % exc)
            return 2
    # --check writes no asset_pins.json: judge the stories against the pins
    # just rendered (it used to read the file from disk -- on a fresh checkout,
    # every story "NOT OFFERED: asset not pinned")
    catalog, report = merge(root, write=not args.check, pins=pins if args.check else None)
    hard = print_report(report, asset_problems)
    unsourced = sum(1 for e in report.values() if e["unsourced"])
    print("\n%d stories, %d pictures pinned, approval: %s%s"
          % (len(catalog["stories"]), len(catalog["assets"]), catalog["approval_status"],
             "" if args.check else " (written)"))
    if hard:
        return 1
    if args.strict and unsourced:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
