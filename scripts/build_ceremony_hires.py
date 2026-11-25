#!/usr/bin/env python3
"""The ceremony film's pictures at the size an event screen needs.

    python scripts/build_ceremony_hires.py              # every usable picture + every candidate
    python scripts/build_ceremony_hires.py ID [ID ...]  # only these
    python scripts/build_ceremony_hires.py --check      # verify what is on disk, write nothing
    python scripts/build_ceremony_hires.py --survey     # only the candidates' survey sheets

The stories catalogue's pictures (data/projects/<project>/stories/assets, pinned by sha256,
approved) were cut ~1080 px wide for the robot's own screen. The ceremony film plays on a
16:9 event screen and moves a camera inside them, so it needs the pixels the owner's
material really has. For every picture a ceremony slide may use (data/ceremony/
slide_assets.json, read through app/ceremony_slides.py) this runs the SAME recipe
(stories/recipes/*.json, through scripts/build_story_assets.py's own `render`) on the same
source, asking for the source's own resolution instead of the robot's (long side capped at
3840) -- a PDF plan is rendered at a whole multiple of the recipe's dpi with the crop
multiplied by the same number, so it is the same cut of the same page.

Nothing is trusted because the recipe is the same. First the recipe is run as the stories
run it and must give the pin's bytes exactly (so the recipe, with every mask, is the one
that was approved); then the large result is brought back down to its pin's size and
compared with the pin, and each pixel-box mask is proven on it one by one. A picture that
is not the pin's picture -- a mask that was not applied, another crop -- is NOT written,
and its entry in the index says why. Where the source has no more pixels than the pin, the entry says so
(`larger: false`); the file is then the same cut at the pin's size, encoded less hard.

Candidates (the owner's material that is not in the stories catalogue) come from
data/ceremony/asset_candidates.json: each was LOOKED at (its `checked` says at what), is
pinned to the bytes that were looked at (`source_sha256`), and goes through the same
`render` (crop / post steps). A candidate whose source changed since, that is marked not ok
or that has no `checked` / `about` is not written. Clips (kind "video") are re-wrapped or
assembled with ffmpeg and probed afterwards; a glyph ("format": "svg") is taken as it is
after its text is checked to be shapes only, with its licence text copied beside it; a
"derive" candidate is MADE from its source by a named derivation that carries its own
checks (scripts/build_ceremony_site_clean.py: the site plan with every building block --
unit numbers, areas, labels -- turned into a flat shape, and the lagoon's outline measured).

Outputs:
  data/ceremony/assets/<id>.<ext>      the pictures and clips (not tracked: rebuilt from
                                       .tmp/story-sources; see .gitignore)
  data/ceremony/assets/index.json      the ONE file the film's engine reads: a list of
      {id, kind, file, sha256, w, h, from_pin, candidate, checked, ok, why_not?, depth, about, ...}
      (depth maps are added by scripts/build_ceremony_depth.py and kept here while they
      still belong to the picture's bytes)
  .tmp/ceremony-show/assets/hires.md, index_sheet_*.png, candidates.md, cand_sheet_*.png

Same inputs, same bytes (fixed encoder settings, no timestamps). Nothing here touches the
stories catalogue, its builder, the validator's registry, the robot or the network.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import build_story_assets as bsa  # noqa: E402  (the stories' recipe runner: reused, never changed)
from app import ceremony_slides, stories  # noqa: E402

OUT = ROOT / "data" / "ceremony" / "assets"
INDEX_NAME = "index.json"
CANDIDATES = ROOT / "data" / "ceremony" / "asset_candidates.json"
CANDIDATES_FORMAT = "emma-ceremony-asset-candidates-v1"
REPORT = ROOT / ".tmp" / "ceremony-show" / "assets"

#: The event screen is 16:9 and the film pushes into a picture up to about 2x.
LONG_SIDE = 3840
QUALITY = 92
#: build_story_assets caps a recipe at 2400 px "so it loads and decodes fast on the robot".
#: These pictures never go to the robot's screen; the cap is lifted for this process only
#: (the module on disk is not touched) so the builder's own `render` can be the one that runs.
bsa.MAX_SIDE = max(bsa.MAX_SIDE, LONG_SIDE)

#: A source this much larger than its pin is worth calling high-resolution.
LARGER_FROM = 1.2

# ---- "is it the same picture as its pin" -------------------------------------------------
# Measured 17 Nov on the 80 usable pictures (see hires.md): the true pairs and the two
# deliberate wrongs (the crop moved by 1% of the pin's width; a recipe's post steps left out)
# are far apart on both numbers. A photo's pin is a hard-compressed WebP, so "the same" is
# not byte equality: SSIM on luma for the whole picture, and the worst 16x16 tile's mean
# absolute difference so one small unmasked mark cannot hide in a good average.
TILE = 16
SSIM_MIN = 0.93          # the 80 true pairs: 0.954 .. 1.000; a crop moved 1.4 pin pixels: 0.34 .. 0.88
WORST_TILE_MAX = 14.0    # the 80 true pairs: 0.0 .. 9.4; dimensions left on a plan: 62.9; a leader line: 40.2
#: A line drawing on transparent (lines_to_alpha): thin strokes rendered at 5x and brought
#: down are not pixel-equal to strokes rendered small, so both sides are softened first
#: (sigma in pin pixels) -- a line that is there on one side only still shows.
LINE_BLUR = 1.2
STAGE = (3, 5, 13)                     # the film's stage colour: see-through plans are judged on it


class Refused(Exception):
    """This picture is not written; the message is its `why_not`."""


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _image(data: bytes):
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _on_stage(image):
    """RGB as the film shows it: a see-through picture laid on the stage colour."""
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        under = Image.new("RGBA", image.size, STAGE + (255,))
        under.alpha_composite(image)
        return under.convert("RGB")
    return image.convert("RGB")


def compare(hi, pin) -> dict:
    """How far `hi`, brought down to the pin's size, is from the pin."""
    import numpy as np
    from PIL import Image, ImageFilter
    from skimage.metrics import structural_similarity

    see_through = pin.mode in ("RGBA", "LA")
    small = hi if hi.size == pin.size else hi.resize(pin.size, Image.LANCZOS)
    a, b = _on_stage(pin), _on_stage(small)
    if see_through:
        blur = ImageFilter.GaussianBlur(LINE_BLUR)
        a, b = a.filter(blur), b.filter(blur)
    fa = np.asarray(a, dtype=np.float32)
    fb = np.asarray(b, dtype=np.float32)
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    ssim = float(structural_similarity(fa @ weights, fb @ weights, data_range=255.0,
                                       gaussian_weights=True, sigma=1.5, use_sample_covariance=False))
    diff = np.abs(fa - fb).mean(axis=2)
    h, w = diff.shape
    th, tw = max(1, h // TILE), max(1, w // TILE)
    tiles = diff[:th * TILE, :tw * TILE].reshape(th, TILE, tw, TILE).mean(axis=(1, 3)) if h >= TILE and w >= TILE else diff
    return {"ssim": round(ssim, 4), "mad": round(float(diff.mean()), 2),
            "worst_tile": round(float(tiles.max()), 2)}


def same_picture(score: dict) -> str | None:
    """None when the scores say "the pin's picture", else what is wrong."""
    if score["ssim"] < SSIM_MIN:
        return "not the pin's picture: SSIM %.3f < %.2f at the pin's size" % (score["ssim"], SSIM_MIN)
    if score["worst_tile"] > WORST_TILE_MAX:
        return ("differs from the pin in one place: worst %dx%d tile %.1f/255 > %.0f (a mask or insert "
                "not applied?)" % (TILE, TILE, score["worst_tile"], WORST_TILE_MAX))
    return None


def masks_proven(recipe: dict, sources: Path, hi, pin) -> tuple[list[dict], str | None]:
    """Each pixel-box post step (inpaint / fill / insert: a callout's leader line painted out,
    a missing letter set in) proven on the result, one by one: the picture is rendered once
    more WITHOUT that step, and inside the step's box the real one must (a) differ from that
    control -- the step did something there -- and (b) be nearer the pin than the control is.
    The whole-picture numbers cannot see the smallest of them (fac_plan_b's 4x17 px fill moves
    the worst tile from 7.7 to 8.0); this can. Returns (evidence per step, problem or None)."""
    import numpy as np
    from PIL import Image

    post = list(recipe.get("post") or [])
    boxes = []
    for i, op in enumerate(post):
        m = (bsa._INPAINT.fullmatch(op) or bsa._FILL.fullmatch(op) or bsa._INSERT.fullmatch(op)) if isinstance(op, str) else None
        if m is not None:
            boxes.append((i, op, [int(v) for v in m.groups()[:4]]))
    if not boxes:
        return [], None
    crop = recipe.get("crop")
    if not crop:
        return [], "post steps in pixels on a recipe without a crop"
    up = hi.width / (crop[2] - crop[0])                 # crop pixels -> this picture's pixels (1.0 unless capped)
    down = pin.width / hi.width

    def stage(image):
        return np.asarray(_on_stage(image), dtype=np.float32)

    full, small, pinned = stage(hi), stage(hi.resize(pin.size, Image.LANCZOS)), stage(pin)
    evidence = []
    for i, op, (x0, y0, x1, y1) in boxes:
        without = dict(recipe, post=post[:i] + post[i + 1:])
        data, _w, _h, _t = render_hires(without, sources)
        control = _image(data)
        cfull, csmall = stage(control), stage(control.resize(pin.size, Image.LANCZOS))
        hx0, hy0, hx1, hy1 = int(x0 * up), int(y0 * up), int((x1 + 1) * up) + 1, int((y1 + 1) * up) + 1
        px0, py0 = max(0, int(hx0 * down) - 1), max(0, int(hy0 * down) - 1)
        px1, py1 = int(hx1 * down) + 2, int(hy1 * down) + 2
        row = {"step": op,
               "changed": round(float(np.abs(full[hy0:hy1, hx0:hx1] - cfull[hy0:hy1, hx0:hx1]).mean()), 2),
               "to_pin": round(float(np.abs(pinned[py0:py1, px0:px1] - small[py0:py1, px0:px1]).mean()), 2),
               "to_pin_without": round(float(np.abs(pinned[py0:py1, px0:px1] - csmall[py0:py1, px0:px1]).mean()), 2)}
        evidence.append(row)
        if row["changed"] < 1.0:
            return evidence, "post step %s changed nothing in its box at this size" % op
        if not row["to_pin"] < row["to_pin_without"]:
            return evidence, ("post step %s: inside its box the picture is no nearer the pin with the step "
                              "(%.2f) than without it (%.2f)" % (op, row["to_pin"], row["to_pin_without"]))
    return evidence, None


# ---- the stories' recipes, asked for at the source's own size -----------------------------

def hires_recipe(recipe: dict, source: Path) -> tuple[dict, int]:
    """(the same recipe at full resolution, the whole number a PDF was multiplied by)."""
    out = {k: v for k, v in recipe.items() if k not in ("max_w", "max_h", "max_kb", "quality")}
    out.update(max_w=LONG_SIDE, max_h=LONG_SIDE, max_kb=256 * 1024, quality=QUALITY)
    times = 1
    if source.suffix.lower() == ".pdf":
        crop = recipe.get("crop")
        if not crop:
            raise Refused("a PDF recipe without a crop cannot be scaled")
        times = max(1, LONG_SIDE // max(crop[2] - crop[0], crop[3] - crop[1]))
        if times > 1:
            for op in recipe.get("post") or []:
                # inpaint / fill / insert name pixels of the cropped picture: at another dpi
                # they would land beside the mark they were measured on.
                if not (isinstance(op, str) and bsa._LINES_TO_ALPHA.fullmatch(op)):
                    raise Refused("post step %r is in pixels of the recipe's dpi; not scaled blindly" % (op,))
            out["dpi"] = int(recipe.get("dpi", 120)) * times
            out["crop"] = [v * times for v in crop]
    return out, times


def render_hires(recipe: dict, sources: Path) -> tuple[bytes, int, int, int]:
    from PIL import Image

    source = bsa.check_recipe(recipe, sources)
    asked, times = hires_recipe(recipe, source)
    limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None          # an A3 page at 1000 dpi is the owner's own drawing, not a bomb
    try:
        data, w, h = bsa.render(asked, sources)
    finally:
        Image.MAX_IMAGE_PIXELS = limit
    return data, w, h, times


def usable_pins() -> dict[str, dict]:
    """catalogue id -> its pin, for the pictures a ceremony slide may show today."""
    catalog = stories.load_catalog() or {}
    return {aid: pin for aid, pin in sorted((catalog.get("assets") or {}).items())
            if ceremony_slides.asset_problem(aid, catalog=catalog) is None}


def map_credit(pin: dict) -> str | None:
    """The attribution of a story map this pin was made from (stories/maps/<name>.json)."""
    source = str(pin.get("source") or "")
    if not source.startswith("maps/"):
        return None
    try:
        doc = json.loads((stories.assets_dir().parent / "maps" / (Path(source).stem + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    credit = doc.get("attribution_en")
    return credit if isinstance(credit, str) and credit.strip() else None


def build_pin(aid: str, pin: dict, recipe: dict | None, sources: Path, listed: dict) -> tuple[dict, bytes | None]:
    """(index entry, bytes to write or None). The entry is complete either way."""
    entry = {"id": aid, "kind": "picture", "file": None, "sha256": None, "w": None, "h": None,
             "from_pin": aid, "candidate": False, "checked": "", "ok": False, "depth": None,
             "about": list((listed.get(aid) or {}).get("about") or []),
             "class": (listed.get(aid) or {}).get("kind"), "shows": (listed.get(aid) or {}).get("shows") or "",
             "text": (listed.get(aid) or {}).get("text") or "",
             "pin": {"file": pin.get("file"), "sha256": pin.get("sha256"), "w": pin.get("w"), "h": pin.get("h")},
             "alpha": bool(pin.get("alpha"))}
    # a map rendered from OpenStreetMap carries its credit (ODbL): the screen draws it whenever
    # the picture shows (app/ceremony_slides.py credit; CLAUDE.md "เครดิต OSM บนภาพต้องอยู่")
    credit = map_credit(pin)
    if credit:
        entry["credit"] = credit
    try:
        if recipe is None:
            raise Refused("no recipe for this pin in stories/recipes")
        if stories.asset_problem(aid, pin) is not None:
            raise Refused("the pinned picture is not on disk as pinned: %s" % stories.asset_problem(aid, pin))
        pinned = (stories.assets_dir() / str(pin["file"])).read_bytes()
        try:
            # First, exactly: the recipe and the source on disk still make the pin byte for byte
            # (the builder is deterministic; all 80 did on 17 Nov). A recipe that lost a mask
            # or moved its crop since the pin was approved stops here, however small the mask
            # -- fac_plan_b's 4x17 px fill is invisible to every picture score below.
            again, _w, _h = bsa.render(recipe, sources)
            if sha256_of(again) != pin.get("sha256"):
                raise Refused("the recipe and source no longer make the pinned picture (sha256 differs): "
                              "the recipe, the source or the encoder changed since it was approved")
            data, w, h, times = render_hires(recipe, sources)
        except (bsa.RecipeError, OSError, ValueError) as exc:
            raise Refused("recipe: %s" % exc) from exc
        score = compare(_image(data), _image(pinned))
        entry["score"] = score
        entry["scale"] = round(max(w, h) / max(pin["w"], pin["h"]), 2)
        entry["larger"] = entry["scale"] >= LARGER_FROM
        entry["source"] = recipe["source"]
        if times > 1:
            entry["pdf_times"] = times
        why = same_picture(score)
        if why:
            raise Refused(why)
        masks, why = masks_proven(recipe, sources, _image(data), _image(pinned))
        if masks:
            entry["masks"] = masks
        if why:
            raise Refused(why)
        if bsa.has_alpha(data) != bool(pin.get("alpha")):
            raise Refused("see-through differs from the pin (alpha %s vs %s)" % (bsa.has_alpha(data), pin.get("alpha")))
        if abs(w / h - pin["w"] / pin["h"]) > 0.01:
            raise Refused("shape %dx%d is not the pin's %dx%d" % (w, h, pin["w"], pin["h"]))
    except Refused as exc:
        entry["why_not"] = str(exc)
        return entry, None
    entry.update(file="%s.%s" % (aid, recipe.get("format", "webp")), sha256=sha256_of(data), w=w, h=h, ok=True,
                 bytes=len(data),
                 checked="the recipe still makes the pin byte for byte; same recipe at source size, brought down "
                         "to the pin %dx%d and compared: SSIM %.3f, worst %dpx tile %.1f/255%s" % (
                             pin["w"], pin["h"], score["ssim"], TILE, score["worst_tile"],
                                                        "; %d masks proven one by one" % len(masks) if masks else ""))
    return entry, data


# ---- candidates ---------------------------------------------------------------------------

#: Never a candidate, whatever a file says about it: the other project's sales kit, the
#: unit inventory (prices, availability), and what build_story_assets refuses by name.
BANNED_CANDIDATE = ("saleskit/", "/UnitInventory/")


def load_candidates() -> dict:
    try:
        raw = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    except OSError:
        return {"candidates": [], "excluded": []}
    if not isinstance(raw, dict) or raw.get("format") != CANDIDATES_FORMAT:
        raise SystemExit("%s is not %s" % (CANDIDATES, CANDIDATES_FORMAT))
    return raw


def _candidate_base(item: dict) -> dict:
    return {"id": item.get("id"), "kind": item.get("kind", "picture"), "file": None, "sha256": None,
            "w": None, "h": None, "from_pin": None, "candidate": True, "checked": item.get("checked") or "",
            "ok": False, "depth": None, "about": list(item.get("about") or []),
            "class": item.get("class"), "shows": item.get("shows") or "", "text": item.get("text") or "",
            "source": item.get("source") or item.get("frames"), "alpha": False,
            # (a person's decision in asset_candidates.json, carried as it is)
            **({"establishing": True} if item.get("establishing") is True else {}),
            # where a real person is in it ([] = looked at, nobody); absent = refused (_candidate_gate)
            **({"people": item["people"]} if isinstance(item.get("people"), list) else {})}


def _candidate_gate(item: dict) -> None:
    cid = item.get("id")
    if not isinstance(cid, str) or not (cid.startswith("c_") or cid == "logo_chrome") or not stories._ASSET_ID.fullmatch(cid):
        raise Refused("a candidate's id starts with c_ (lower case, digits, _)")
    if item.get("ok") is not True:
        raise Refused(item.get("why_not") or "marked not usable")
    if not item.get("checked"):
        raise Refused("nobody wrote what was looked at (`checked`)")
    if not item.get("about"):
        raise Refused("no `about`: the specific nouns it shows")
    if not isinstance(item.get("people"), list):
        raise Refused("nobody wrote whether a real person is in it (`people`: boxes, or [] for nobody)")
    names = [item.get("source") or ""] + list(item.get("frames") or [])
    for name in names:
        if any(bad in "/" + name for bad in BANNED_CANDIDATE):
            raise Refused("source %r is never shown at the ceremony" % name)


#: A glyph is taken as it is (a vector has no "full resolution"), so what it may contain is
#: checked instead: shapes only -- no words, no pictures, nothing fetched, nothing that runs.
_SVG_SHAPES = frozenset({"svg", "g", "path", "circle", "ellipse", "line", "rect", "polyline", "polygon"})
_SVG_MAX_BYTES = 8 * 1024
_SVG_NOT_A_SHAPE = ("<!", "<?", "href", "url(", "style", "javascript", " on")


def _svg_glyph(data: bytes) -> tuple[int, int]:
    import xml.etree.ElementTree as ET

    if len(data) > _SVG_MAX_BYTES:
        raise Refused("an SVG glyph over %d bytes is not a glyph" % _SVG_MAX_BYTES)
    text = data.decode("utf-8")
    lowered = text.lower()
    for mark in _SVG_NOT_A_SHAPE:
        if mark in lowered:
            raise Refused("the SVG has more than shapes in it (%r: links, styles, entities or handlers)" % mark.strip())
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise Refused("the SVG does not parse: %s" % exc) from exc
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in _SVG_SHAPES or (node.text or "").strip() or (node.tail or "").strip():
            raise Refused("the SVG holds <%s> or loose text: only shapes are taken" % tag)
    box = (root.get("viewBox") or "").split()
    if root.tag.rsplit("}", 1)[-1] != "svg" or len(box) != 4:
        raise Refused("the SVG has no viewBox")
    return round(float(box[2])), round(float(box[3]))


def build_candidate_svg(item: dict, sources: Path, out: Path) -> tuple[dict, bytes | None]:
    entry = _candidate_base(item)
    try:
        _candidate_gate(item)
        if not item.get("source_sha256"):
            raise Refused("not pinned to the bytes that were looked at (source_sha256)")
        try:
            source = bsa.check_recipe({"id": item["id"], "source": item["source"],
                                       "source_sha256": item["source_sha256"], "format": "webp"}, sources)
        except bsa.RecipeError as exc:
            raise Refused("recipe: %s" % exc) from exc
        data = source.read_bytes()
        w, h = _svg_glyph(data)
        licence = item.get("licence") if isinstance(item.get("licence"), dict) else None
        if not licence or not licence.get("files"):
            raise Refused("a glyph from somebody's icon set needs its licence named and its text beside it")
        notices = []
        for name in licence["files"]:
            path = (sources / name).resolve()
            if not path.is_relative_to(sources.resolve()) or not path.is_file():
                raise Refused("licence text not found: %s" % name)
            notices.append(path)
    except (Refused, UnicodeDecodeError, ValueError) as exc:
        entry["why_not"] = str(exc)
        return entry, None
    (out / "licenses").mkdir(parents=True, exist_ok=True)
    for path in notices:                        # the notice travels with the copy (ISC / Apache-2.0 ask for it)
        target = out / "licenses" / path.name
        if not target.is_file() or target.read_bytes() != path.read_bytes():
            target.write_bytes(path.read_bytes())
    entry.update(file="%s.svg" % item["id"], sha256=sha256_of(data), w=w, h=h, ok=True, bytes=len(data), alpha=True,
                 vector=True, licence={"name": licence.get("name"), "files": ["licenses/" + p.name for p in notices]})
    return entry, data


def build_candidate_derived(item: dict, sources: Path) -> tuple[dict, bytes | None]:
    """A picture MADE from the owner's file by a named derivation that carries its own checks
    (today one: `site_lagoon_clean`, scripts/build_ceremony_site_clean.py -- the site plan with
    every building block, and so every unit number, area and label, turned into a flat shape).
    The derivation's refusal is this candidate's refusal."""
    entry = _candidate_base(item)
    try:
        _candidate_gate(item)
        if not item.get("source_sha256"):
            raise Refused("not pinned to the bytes that were looked at (source_sha256)")
        if item.get("derive") != "site_lagoon_clean":
            raise Refused("unknown derivation %r" % (item.get("derive"),))
        try:
            source = bsa.check_recipe({"id": item["id"], "source": item["source"],
                                       "source_sha256": item["source_sha256"], "format": "png"}, sources)
        except bsa.RecipeError as exc:
            raise Refused("recipe: %s" % exc) from exc
        import build_ceremony_site_clean as clean

        try:
            data, geometry, facts = clean.derive(source.read_bytes())
        except clean.DeriveError as exc:
            raise Refused("derivation: %s" % exc) from exc
        picture = _image(data)
    except Refused as exc:
        entry["why_not"] = str(exc)
        return entry, None
    entry.update(file="%s.png" % item["id"], sha256=sha256_of(data), w=picture.width, h=picture.height, ok=True,
                 bytes=len(data), alpha=True, geometry=geometry, derived={"by": item["derive"], **facts})
    return entry, data


def build_candidate_picture(item: dict, sources: Path) -> tuple[dict, bytes | None]:
    entry = _candidate_base(item)
    try:
        _candidate_gate(item)
        if not item.get("source_sha256"):
            raise Refused("not pinned to the bytes that were looked at (source_sha256)")
        recipe = {k: item[k] for k in ("id", "source", "source_sha256", "crop", "post", "format", "page", "dpi")
                  if item.get(k) is not None}
        try:
            data, w, h, _times = render_hires(recipe, sources)   # check_recipe: banned names, sha pin
        except (bsa.RecipeError, OSError, ValueError) as exc:
            raise Refused("recipe: %s" % exc) from exc
    except Refused as exc:
        entry["why_not"] = str(exc)
        return entry, None
    entry.update(file="%s.%s" % (item["id"], item.get("format", "webp")), sha256=sha256_of(data), w=w, h=h,
                 ok=True, bytes=len(data), alpha=bsa.has_alpha(data))
    if item.get("crop"):
        entry["crop"] = item["crop"]
    return entry, data


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames:format=duration",
         "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = str(stream.get("r_frame_rate") or "0/1").partition("/")
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
    return {"codec": stream.get("codec_name"), "pix_fmt": stream.get("pix_fmt"), "w": stream.get("width"),
            "h": stream.get("height"), "fps": round(float(num) / float(den or 1), 3),
            "frames": int(stream.get("nb_read_frames") or 0),
            "duration": round(float((data.get("format") or {}).get("duration") or 0), 3), "audio": bool(audio)}


def build_candidate_video(item: dict, sources: Path, work: Path) -> tuple[dict, bytes | None]:
    """A clip a browser plays: H.264 yuv420p, index at the front, no sound track (the hall's
    sound is Emma's voice; a film with its own track is also refused autoplay by Chromium).
    `source` = the owner's clip (re-wrapped without re-encoding when it already is H.264
    yuv420p); `frames` = stills in order, each pinned by sha256 in `frames_sha256`, at `fps`."""
    entry = _candidate_base(item)
    target = work / ("%s.mp4" % item.get("id"))
    try:
        _candidate_gate(item)
        common = ["-an", "-map_metadata", "-1", "-movflags", "+faststart", "-fflags", "+bitexact",
                  "-flags:v", "+bitexact"]
        # the owner's own clip keeps its container tags: one of them is its AI-generated-content
        # label, which is not ours to drop
        kept = ["-an", "-map_metadata", "0", "-movflags", "+faststart+use_metadata_tags"]
        if item.get("source"):
            recipe = {"id": item["id"], "source": item["source"], "source_sha256": item.get("source_sha256"),
                      "format": "webp"}
            if not item.get("source_sha256"):
                raise Refused("not pinned to the bytes that were looked at (source_sha256)")
            try:
                source = bsa.check_recipe(recipe, sources)
            except bsa.RecipeError as exc:
                raise Refused("recipe: %s" % exc) from exc
            before = _probe(source)
            if before["codec"] == "h264" and before["pix_fmt"] == "yuv420p":
                codec = ["-c:v", "copy"]
            else:
                codec = ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p"]
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0"] + codec + kept + [str(target)]
            expect_frames, expect_size = before["frames"], (before["w"], before["h"])
        else:
            frames = list(item.get("frames") or [])
            pins = item.get("frames_sha256") or {}
            if len(frames) < 2:
                raise Refused("a clip needs a source or at least two frames")
            listing = work / ("%s.frames.txt" % item["id"])
            lines = []
            hold = 1.0 / float(item.get("fps") or 12)
            for name in frames:
                path = (sources / name).resolve()
                if not path.is_relative_to(sources.resolve()) or not path.is_file():
                    raise Refused("frame not found in the sources folder: %s" % name)
                if sha256_of(path.read_bytes()) != pins.get(name):
                    raise Refused("frame changed since it was looked at: %s" % name)
                lines += ["file '%s'" % str(path).replace("\\", "/").replace("'", "'\\''"), "duration %.6f" % hold]
            lines.append(lines[-2])                      # ffmpeg's concat drops the last duration without it
            listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
            vf = []
            if item.get("crop"):
                x0, y0, x1, y1 = item["crop"]
                vf.append("crop=%d:%d:%d:%d" % (x1 - x0, y1 - y0, x0, y0))
            if item.get("scale_w"):
                vf.append("scale=%d:-2:flags=lanczos" % int(item["scale_w"]))
            if item.get("blend"):
                # between two stills of a slow move, in-between frames are mixed from both:
                # softer than a jump, and no picture that was not in the owner's frames
                vf.append("framerate=fps=%d:interp_start=0:interp_end=255:scene=100" % int(item["blend"]))
            vf.append("format=yuv420p")
            cmd = (["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-vf", ",".join(vf), "-fps_mode", "cfr", "-r", str(int(item.get("blend") or item.get("fps") or 12)),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "17"] + common + [str(target)])
            expect_frames, expect_size = None, None
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if done.returncode != 0 or not target.is_file():
            raise Refused("ffmpeg: %s" % (done.stderr.strip()[-300:] or "failed"))
        after = _probe(target)
        if after["codec"] != "h264" or after["pix_fmt"] != "yuv420p" or after["audio"]:
            raise Refused("not a browser's clip: %s" % after)
        if expect_frames is not None and (after["frames"] != expect_frames or (after["w"], after["h"]) != expect_size):
            raise Refused("re-wrapped clip is not the source's: %s" % after)
        if after["frames"] < 2 or after["w"] % 2 or after["h"] % 2:
            raise Refused("clip has %d frames at %sx%s" % (after["frames"], after["w"], after["h"]))
        whole = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target), "-f", "null", "-"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        if whole.returncode != 0 or whole.stderr.strip():
            raise Refused("the clip does not decode cleanly: %s" % whole.stderr.strip()[-200:])
        data = target.read_bytes()
    except Refused as exc:
        entry["why_not"] = str(exc)
        return entry, None
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        entry["why_not"] = "clip: %s" % exc
        return entry, None
    entry.update(file="%s.mp4" % item["id"], sha256=sha256_of(data), w=after["w"], h=after["h"], ok=True,
                 bytes=len(data), duration=after["duration"], fps=after["fps"], frames=after["frames"])
    return entry, data


# ---- the index ----------------------------------------------------------------------------

def carry_over(previous: dict, entries: dict, wanted) -> tuple[list[str], list[str]]:
    """What a run keeps of the entries it did not build this time, and what it removes.

    A run for some ids (`wanted`) touches nothing else: every other entry stays as it was
    (it used to drop from the index every entry that was neither a pin nor a candidate).
    A full run removes what is no longer listed -- a pin excluded since, a candidate withdrawn
    (a real person found in it: its file must not stay where the engine can find it) -- but
    NOT an entry registered by hand (`hand`: who made it, how). Those are not this builder's
    to make or to drop: 18 Nov 2026 the Emma 3D clips (rendered by scripts/render_emma_*.py)
    and the owner's final-render sheets and crops were in the index without being candidates,
    and one run of this script would have taken all fifteen out of the film (and a full run
    deleted their files). A hand entry withdrawn is taken out of the index by hand."""
    kept, removed = [], []
    for aid in sorted(set(previous) - set(entries)):
        (kept if wanted is not None or previous[aid].get("hand") else removed).append(aid)
    return kept, removed


def read_index(out: Path = OUT) -> list[dict]:
    try:
        raw = json.loads((out / INDEX_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def write_index(entries: list[dict], out: Path = OUT) -> None:
    out.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: (bool(e.get("candidate")), str(e.get("id"))))
    (out / INDEX_NAME).write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def depth_still_fits(entry: dict, depth: object, out: Path = OUT) -> bool:
    """A depth map belongs to the bytes it was computed on (`of`), and is itself unchanged."""
    if not isinstance(depth, dict) or not entry.get("ok") or depth.get("of") != entry.get("sha256"):
        return False
    try:
        for part in (depth, depth.get("soft")):
            if part is not None and sha256_of((out / str(part.get("file"))).read_bytes()) != part.get("sha256"):
                return False
    except (OSError, AttributeError):
        return False
    return True


def depth_files(depth: object) -> set[str]:
    if not isinstance(depth, dict):
        return set()
    return {str(part["file"]) for part in (depth, depth.get("soft")) if isinstance(part, dict) and part.get("file")}


def file_problem(entry: dict, out: Path = OUT) -> str | None:
    """What is wrong with an ok entry's file on disk (None = it is the indexed bytes)."""
    try:
        data = (out / str(entry.get("file"))).read_bytes()
    except OSError:
        return "file missing"
    return None if sha256_of(data) == entry.get("sha256") else "file is not the indexed bytes"


# ---- reports ------------------------------------------------------------------------------

def _thumb(path: Path, box: tuple[int, int]):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    if path.suffix.lower() == ".svg":
        import fitz

        with fitz.open(path) as doc:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(8, 8), alpha=True)
        picture = Image.open(io.BytesIO(pix.tobytes("png")))
    else:
        picture = Image.open(path)
        picture.draft("RGB", (box[0] * 2, box[1] * 2))
    picture = picture.convert("RGBA")
    picture.thumbnail(box)
    under = Image.new("RGBA", picture.size, (58, 60, 84, 255))       # see-through pictures on grey
    under.alpha_composite(picture)
    return under.convert("RGB")


def _video_thumb(path: Path, box: tuple[int, int], at: float):
    from PIL import Image

    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", "%.3f" % at, "-i", str(path), "-frames:v", "1",
                          "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
    picture = Image.open(io.BytesIO(out)).convert("RGB")
    picture.thumbnail(box)
    return picture


def sheets(rows: list[dict], out: Path, stem: str, *, cols: int = 4, tw: int = 470, th: int = 300) -> int:
    """Contact sheets `<stem>_NN.png`: rows = {path, lines: [str, str], good: bool, at?}."""
    from PIL import Image, ImageDraw

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("%s_*.png" % stem):
        old.unlink()
    per = cols * 3
    label = 46
    for start in range(0, len(rows), per):
        part = rows[start:start + per]
        sheet = Image.new("RGB", (cols * tw, ((len(part) + cols - 1) // cols) * (th + label)), (20, 21, 34))
        draw = ImageDraw.Draw(sheet)
        for k, row in enumerate(part):
            x, y = (k % cols) * tw, (k // cols) * (th + label)
            try:
                path = Path(row["path"])
                picture = (_video_thumb(path, (tw - 10, th - 6), row.get("at", 0.0)) if path.suffix.lower() == ".mp4"
                           else _thumb(path, (tw - 10, th - 6)))
                sheet.paste(picture, (x + (tw - picture.width) // 2, y + 3))
            except (OSError, ValueError, subprocess.CalledProcessError):
                draw.text((x + 8, y + 8), "(cannot be opened)", fill=(255, 120, 120))
            colour = (170, 255, 190) if row.get("good") else (255, 140, 140)
            lines = list(row.get("lines") or [])
            for n, text in enumerate(lines[:3]):
                draw.text((x + 6, y + th + 1 + 14 * n), str(text)[:74], fill=colour if n == 0 else (205, 205, 220))
        sheet.save(out / ("%s_%02d.png" % (stem, start // per + 1)))
    return (len(rows) + per - 1) // per


def write_hires_md(entries: list[dict], report: Path) -> None:
    lines = ["# The ceremony film's pictures at full size", "",
             "Built by `scripts/build_ceremony_hires.py`; the files are in `data/ceremony/assets/`, the engine reads "
             "`data/ceremony/assets/index.json`. `x` = long side against the catalogue pin's; `larger: no` = the "
             "owner's source has no more pixels than the pin (same cut, encoded less hard). `SSIM` / `worst tile` = "
             "the picture brought down to its pin's size and compared with the pin (limits: SSIM >= %.2f, worst "
             "%dpx tile <= %.0f/255). Depth maps (`scripts/build_ceremony_depth.py`): near = white." %
             (SSIM_MIN, TILE, WORST_TILE_MAX), "",
             "| id | px | pin px | x | larger | SSIM | worst tile | depth | KB | state |", "|---|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        if e.get("candidate"):
            continue
        score = e.get("score") or {}
        pin = e.get("pin") or {}
        depth = e.get("depth") or {}
        lines.append("| `%s` | %s | %sx%s | %s | %s | %s | %s | %s | %s | %s |" % (
            e["id"], "%sx%s" % (e["w"], e["h"]) if e.get("ok") else "-", pin.get("w"), pin.get("h"),
            e.get("scale", "-"), "yes" if e.get("larger") else "no", score.get("ssim", "-"),
            score.get("worst_tile", "-"), "%sx%s" % (depth.get("w"), depth.get("h")) if depth else "-",
            (e.get("bytes") or 0) // 1024 or "-", "ok" if e.get("ok") else "NOT USABLE: %s" % e.get("why_not")))
    lines += ["", "## Candidates (not in the stories catalogue; looked at one by one -- see candidates.md)", "",
              "| id | kind | px | depth | KB | shows | about | checked | state |", "|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        if not e.get("candidate"):
            continue
        depth = e.get("depth") or {}
        size = "%sx%s" % (e["w"], e["h"]) if e.get("ok") else "-"
        if e.get("kind") == "video" and e.get("ok"):
            size += " %ss @%s" % (e.get("duration"), e.get("fps"))
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            e["id"], e.get("kind"), size, "%sx%s" % (depth.get("w"), depth.get("h")) if depth else "-",
            (e.get("bytes") or 0) // 1024 or "-", e.get("shows") or "", ", ".join(e.get("about") or []),
            e.get("checked") or "", "ok" if e.get("ok") else "NOT USABLE: %s" % e.get("why_not")))
    noted = [e for e in entries if e.get("at_full_size") or e.get("note") or e.get("registers_with")]
    if noted:
        lines += ["", "## Seen only at this size, and other notes", "", "| id | note |", "|---|---|"]
        for e in noted:
            parts = [e.get("at_full_size"), e.get("note"),
                     ("lies exactly on `%s` (same camera and cut)" % e["registers_with"]) if e.get("registers_with") else None]
            lines.append("| `%s` | %s |" % (e["id"], " -- ".join(p for p in parts if p)))
    report.mkdir(parents=True, exist_ok=True)
    (report / "hires.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index_sheets(entries: list[dict], out: Path, report: Path) -> int:
    rows = []
    for e in entries:
        if not e.get("ok"):
            continue
        depth = e.get("depth")
        second = "%sx%s" % (e["w"], e["h"])
        if e.get("kind") == "video":
            second += "  clip %ss" % e.get("duration")
        elif e.get("candidate"):
            second += "  candidate"
        else:
            second += "  x%s%s" % (e.get("scale"), "" if e.get("larger") else " (source not larger)")
        second += "  +depth" if depth else ""
        rows.append({"path": out / e["file"], "lines": [e["id"], second, (e.get("shows") or "")],
                     "good": True, "at": (e.get("duration") or 0) * 0.6})
    return sheets(rows, report, "index_sheet")


def survey_rows(doc: dict, sources: Path) -> list[dict]:
    """Every file that was looked at, in order: usable (with the candidate id it became) or
    excluded and why. `excluded` rules: {"path": exact} or {"glob": pattern}, first match wins."""
    by_source: dict[str, list[dict]] = {}
    for item in doc.get("candidates") or []:
        for name in ([item["source"]] if item.get("source") else []) + list(item.get("frames") or []):
            by_source.setdefault(name, []).append(item)
    rules = list(doc.get("excluded") or [])
    in_catalogue: dict[str, list[str]] = {}
    try:
        pinned = (stories.load_catalog() or {}).get("assets") or {}
        for recipe in bsa.load_recipes(stories.folder()):
            if recipe.get("id") in pinned:
                in_catalogue.setdefault(str(recipe.get("source")), []).append(recipe["id"])
    except (bsa.RecipeError, OSError, ValueError):
        pass
    rows = []
    seen: set[str] = set()
    for folder in doc.get("surveyed") or []:
        base = sources / folder["folder"]
        pattern = folder.get("pattern", "*")
        files = sorted(p for p in (base.rglob(pattern) if folder.get("deep") else base.glob(pattern)) if p.is_file())
        for path in files:
            rel = path.relative_to(sources).as_posix()
            if rel in seen or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".svg"):
                continue
            seen.add(rel)
            used = by_source.get(rel) or []
            good = [c for c in used if c.get("ok") is True]
            why = ""
            state = "usable" if good else "excluded"
            if not good and rel in in_catalogue:
                # already a stories pin: its fate is the catalogue's (catalog.md), not this survey's
                state = "catalogue"
                why = "in the stories catalogue as " + ", ".join("`%s`" % i for i in sorted(in_catalogue[rel]))
            elif not good:
                for rule in rules:
                    if rule.get("path") == rel or (rule.get("glob") and fnmatch.fnmatchcase(rel, rule["glob"])):
                        why = rule.get("why") or ""
                        break
                else:
                    why = (used[0].get("why_not") if used else "") or "NOT CLASSED"
            elif rel in in_catalogue:
                why = "also in the stories catalogue as " + ", ".join(sorted(in_catalogue[rel]))
            rows.append({"rel": rel, "folder": folder["folder"], "path": path, "good": bool(good), "state": state,
                         "ids": [c["id"] for c in good], "why": why})
    return rows


def write_survey(doc: dict, sources: Path, report: Path) -> tuple[int, int]:
    rows = survey_rows(doc, sources)
    lines = ["# Candidates survey -- the owner's material that is not in the stories catalogue", "",
             "Every file below was LOOKED at (17 Nov 2026, on the `cand_sheet_*.png` contact sheets at 470 px and, "
             "where text or small detail decides, at full size). `usable` files became the candidate ids named "
             "(recipes in `data/ceremony/asset_candidates.json`, files in `data/ceremony/assets/`, listed in "
             "`index.json`). Always excluded: prices, quotations, availability, unit numbers, dimensions/areas on "
             "plans, photos of real people, the Embassy Life sales kit, a rooftop shown as an amenity, 'concept / "
             "subject to change' captions, campus pictures whose roof lettering reads mirrored, parking/EV, other "
             "brands' logos.", ""]
    folder = None
    good = 0
    for row in rows:
        if row["folder"] != folder:
            folder = row["folder"]
            mine = [r for r in rows if r["folder"] == folder]
            lines += ["", "## `%s` -- %d files: %d usable, %d excluded, %d already in the catalogue" % (
                folder, len(mine), sum(r["state"] == "usable" for r in mine),
                sum(r["state"] == "excluded" for r in mine), sum(r["state"] == "catalogue" for r in mine)), "",
                "| file | class | id / reason |", "|---|---|---|"]
        good += row["good"]
        name = row["rel"][len(folder):].lstrip("/")
        lines.append("| `%s` | %s | %s |" % (name, row["state"],
                                            ", ".join("`%s`" % i for i in row["ids"]) if row["good"] else row["why"]))
    report.mkdir(parents=True, exist_ok=True)
    (report / "candidates.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sheets([{"path": r["path"], "good": r["good"], "at": 5.0,
             "lines": [r["rel"].rsplit("/", 1)[-1], r["rel"].rsplit("/", 2)[-2] if "/" in r["rel"] else "",
                       ("usable -> " + ", ".join(r["ids"])) if r["good"]
                       else (r["why"].replace("`", "") if r["state"] == "catalogue" else "excluded: " + r["why"])]}
            for r in rows], report, "cand_sheet")
    unclassed = [r["rel"] for r in rows if r["why"] == "NOT CLASSED"]
    if unclassed:
        print("NOT CLASSED (%d): %s" % (len(unclassed), "; ".join(unclassed[:12])))
    return len(rows), good


# ---- main ---------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ids", nargs="*", help="only these ids (default: every usable picture and every candidate)")
    ap.add_argument("--sources", type=Path, default=bsa.DEFAULT_SOURCES)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--report", type=Path, default=REPORT)
    ap.add_argument("--check", action="store_true", help="verify the files on disk against the index; write nothing")
    ap.add_argument("--survey", action="store_true", help="only write candidates.md and cand_sheet_*.png")
    ap.add_argument("--no-sheets", action="store_true", help="skip the contact sheets")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sources = args.sources.resolve()
    out = args.out.resolve()
    doc = load_candidates()

    if args.survey:
        total, good = write_survey(doc, sources, args.report)
        print("survey: %d files looked at, %d usable -> %s" % (total, good, args.report))
        return 0

    previous = {e.get("id"): e for e in read_index(out)}
    if args.check:
        bad = 0
        for e in previous.values():
            if not e.get("ok"):
                print("NOT USABLE  %-28s %s" % (e.get("id"), e.get("why_not")))
                continue
            why = file_problem(e, out)
            if why is None and e.get("depth") and not depth_still_fits(e, e["depth"], out):
                why = "depth map is not this picture's"
            print("%-11s %-28s %s" % ("ok" if why is None else "PROBLEM", e.get("id"), why or e.get("file")))
            bad += why is not None
        print("%d entries, %d problems" % (len(previous), bad))
        return 1 if bad else 0

    pins = usable_pins()
    recipes = {r["id"]: r for r in bsa.load_recipes(stories.folder())}
    listed = ceremony_slides.asset_registry()
    candidates = {c.get("id"): c for c in doc.get("candidates") or [] if isinstance(c, dict)}
    wanted = set(args.ids) if args.ids else None
    unknown = (wanted or set()) - set(pins) - set(candidates)
    if unknown:
        print("unknown ids (not a usable catalogue picture, not a candidate): %s" % ", ".join(sorted(unknown)))
        return 2
    out.mkdir(parents=True, exist_ok=True)
    work = out / ".work"
    entries: dict[str, dict] = {}
    refused = 0
    for aid in list(pins) + [c for c in candidates if c not in pins]:
        if wanted is not None and aid not in wanted:
            if aid in previous:
                entries[aid] = previous[aid]            # untouched this run
            continue
        if aid in pins:
            entry, data = build_pin(aid, pins[aid], recipes.get(aid), sources, listed)
        elif candidates[aid].get("kind") == "video":
            work.mkdir(parents=True, exist_ok=True)
            entry, data = build_candidate_video(candidates[aid], sources, work)
        elif candidates[aid].get("derive"):
            entry, data = build_candidate_derived(candidates[aid], sources)
        elif candidates[aid].get("format") == "svg":
            entry, data = build_candidate_svg(candidates[aid], sources, out)
        else:
            entry, data = build_candidate_picture(candidates[aid], sources)
        for key in ("picked_by", "note", "registers_with"):
            if aid in candidates and candidates[aid].get(key):
                entry[key] = candidates[aid][key]
        old = previous.get(aid) or {}
        if data is None:
            refused += 1
            # a picture that failed its check is not left on disk under this id
            for name in {old.get("file")} | depth_files(old.get("depth")):
                if name:
                    (out / name).unlink(missing_ok=True)
            print("REFUSED  %-28s %s" % (aid, entry.get("why_not")))
        else:
            target = out / entry["file"]
            if not target.is_file() or target.read_bytes() != data:
                target.write_bytes(data)
            if old.get("file") and old["file"] != entry["file"]:
                (out / old["file"]).unlink(missing_ok=True)
            if depth_still_fits(entry, old.get("depth"), out):
                entry["depth"] = old["depth"]
            else:
                for name in depth_files(old.get("depth")):                # another picture's depth
                    (out / name).unlink(missing_ok=True)
            score = entry.get("score") or {}
            print("ok       %-28s %5sx%-5s %s%s" % (
                aid, entry["w"], entry["h"],
                ("x%-5s ssim %.3f tile %5.1f" % (entry.get("scale"), score.get("ssim", 0), score.get("worst_tile", 0)))
                if not entry["candidate"] else entry.get("kind"),
                "" if entry.get("larger", True) else "  (source not larger)"))
        # what only shows at this size (a prop's lettering nobody could read at 720 px): looked
        # for by eye on the largest pictures, kept beside the candidates' notes
        note = (doc.get("at_full_size") or {}).get(aid)
        if note:
            entry["at_full_size"] = note
        # a real person found in a catalogue picture: a keep-out every slide is checked against
        keep = (doc.get("keep_out") or {}).get(aid)
        if keep:
            entry["keep_out"] = keep
        entries[aid] = entry
    if work.is_dir():
        for leftover in work.iterdir():
            leftover.unlink()
        work.rmdir()
    kept, removed = carry_over(previous, entries, wanted)
    for aid in kept:
        entries[aid] = previous[aid]
        if previous[aid].get("hand"):
            print("kept     %-28s (registered by hand: %s)" % (aid, str(previous[aid]["hand"])[:60]))
    for gone in removed:
        # no longer a usable picture or a candidate (excluded since, or withdrawn after a
        # second look): its files do not stay where the engine could still find them
        old = previous[gone]
        for name in {old.get("file")} | depth_files(old.get("depth")):
            if name:
                (out / name).unlink(missing_ok=True)
        print("removed  %-28s (no longer listed)" % gone)
    ordered = sorted(entries.values(), key=lambda e: (bool(e.get("candidate")), str(e.get("id"))))
    write_index(ordered, out)
    write_hires_md(ordered, args.report)
    if not args.no_sheets:
        write_index_sheets(ordered, out, args.report)
        write_survey(doc, sources, args.report)
    usable = [e for e in ordered if e.get("ok")]
    print("\n%d entries: %d usable (%d larger than their pin, %d candidates), %d refused -> %s" % (
        len(ordered), len(usable), sum(1 for e in usable if e.get("larger")),
        sum(1 for e in usable if e.get("candidate")), len(ordered) - len(usable), out / INDEX_NAME))
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
