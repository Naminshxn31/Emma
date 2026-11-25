#!/usr/bin/env python3
"""The whole lagoon from straight above, with nothing on it that a guest may not read.

    python scripts/build_ceremony_site_clean.py            # report + look-at sheets, writes nothing else
    (the picture itself is built by scripts/build_ceremony_hires.py, candidate `c_site_lagoon_clean`,
     which calls `derive` below and refuses the file if any check here fails)

Owner, 17 Nov 2026, on the "155 metres" frame (a partial aerial render): "อันนี้ทำไมไม่เอาภาพมุมสูง
ที่เห็นสระทั้งเลย" -- why not a top view that shows the WHOLE pool? No render does. The only
picture that shows the whole Water Lagoon from above is the website's ground-floor site plan
(web/3.project details/master plan/.../3.png, 2000x1414: the largest copy the owner's material
has), and the stories catalogue excludes it (`plan_ground_site`): every building block carries
its units' numbers and areas, SHOP-1..6, LOBBY / JUNIOR WORLD / SOUL SPACE / BIOGENESIS, and
white BUILDING A..F tags.

So this makes a derivative, by colour and region and not by boxes guessed by hand:

  * the page's white ground becomes see-through (the plan stands on the film's dark stage);
  * every building footprint -- the pale unit cells (blue / pink / yellow), the white
    corridors, the pale podium of Building A, with every outline and letter inside them --
    becomes ONE flat dark navy shape with a thin light edge;
  * the BUILDING A..F tags go with the page; where a tag sat over the site's own edge the
    notch is filled FLAT with the colour of the hedge beside it (nothing is painted in);
  * the lagoon, islands, bridges, gardens, paths, drive and boundary are the drawing's own pixels.

and measures, in % of the picture's width / height:

  * `lagoon`: the water's edge as one closed path (bridges crossed, islands inside);
  * `centre`: a polyline along the lagoon's length (for lettering laid along the pool).

It refuses (DeriveError) unless: no "ink" (neutral near-black: the lettering and the cell
outlines) and no "paper" (the pale cell / corridor / tag colours) survives outside what was
deliberately kept, beyond the specks measured on 17 Nov; the navy shapes cover a plausible
share of the site; and the lagoon is one body of a plausible size. These catch a source
that changed or a segmentation that slipped; that NO letter or digit is left was checked by
eye at full size on 17 Nov (the look-at sheets this script writes) and must be looked at
again whenever the source's pin changes.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NAVY = (11, 21, 48)                # #0b1530
EDGE = (150, 186, 255)             # the thin light edge of a building's shape
EDGE_PX = 2
PAD = 8                            # px of see-through margin round the site

#: measured on the 17 Nov source (limits leave room for nothing but encoder-free arithmetic:
#: the derivation is exact, so these are the measured values with a small margin)
PATCH_MIN_AREA = 180               # px: a pale patch that could hold the plan's smallest label
INK_ON_PATCH_MAX = 12              # dark pixels on such a patch (5 at most on 17 Nov; a 4-figure label is 60+)
PAPER_PATCHES_MAX = 14             # 12 on 17 Nov: the bridges, the spa rings, the pavilions' decks, the drive's fountain
BUILDINGS_SHARE = (0.39, 0.49)     # navy / site area (measured 0.44)
LAGOON_SHARE = (0.14, 0.30)        # lagoon (filled) / site area


class DeriveError(ValueError):
    pass


def _masks(rgb):
    """(site, building, tags_in_site, water) boolean masks of the source page."""
    import cv2
    import numpy as np

    h, w = rgb.shape[:2]
    r, g, b = (rgb[..., k].astype(np.int16) for k in range(3))
    mx, mn = np.maximum(np.maximum(r, g), b), np.minimum(np.minimum(r, g), b)
    spread = mx - mn

    # --- the page: near-white connected to the page's border --------------------------------
    white = (mn >= 244).astype(np.uint8)
    n, labels = cv2.connectedComponents(white, connectivity=4)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    page = np.isin(labels, border[border != 0])
    # The site = the one big drawn thing on the page.
    drawn = (~page).astype(np.uint8)
    drawn = cv2.morphologyEx(drawn, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))      # lone specks, and the tags' lettering
    n, labels, stats, _ = cv2.connectedComponentsWithStats(drawn, connectivity=8)
    if n < 2:
        raise DeriveError("no drawing found on the page")
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    site = labels == biggest
    flood = (~site).astype(np.uint8) * 255
    cv2.floodFill(flood, np.zeros((h + 2, w + 2), np.uint8), (0, 0), 0)
    site |= flood > 0                                                                # inner holes belong to the site
    # The BUILDING A..F tags are white boxes joined to the page that cut notches into the site's
    # top and bottom edges. Each is found by its own lettering (ink lying on the page); inside
    # its box the site's edge is taken as the straight line between the edge just left and just
    # right of the box, and only what lies on the site's side of that line is given back to the
    # site (to be filled flat). Whatever of the tag lies beyond the line stays page: no stroke
    # of the lettering can end up in the picture.
    r0, g0, b0 = (rgb[..., k].astype(np.int16) for k in range(3))
    lettering = ((np.maximum(np.maximum(r0, g0), b0) <= 110) & ~site).astype(np.uint8)
    words = cv2.dilate(lettering, cv2.getStructuringElement(cv2.MORPH_RECT, (41, 9)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(words, connectivity=8)
    tags = np.zeros_like(site)
    cols = np.arange(w)
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if bw < 60 or bh > 40:
            continue                                         # not a line of lettering
        bx0, bx1 = max(0, x - TAG_PAD_X), min(w, x + bw + TAG_PAD_X)
        by0, by1 = max(0, y - TAG_PAD_Y), min(h, y + bh + TAG_PAD_Y)
        above = int(site[:by0, bx0:bx1].sum())
        below = int(site[by1:, bx0:bx1].sum())
        top_edge = below > above                             # the site lies below this tag

        def edge_at(c0, c1):
            rows = []
            for c in range(max(0, c0), min(w, c1)):
                on = np.nonzero(site[:, c])[0]
                if len(on):
                    rows.append(on[0] if top_edge else on[-1])
            return float(np.median(rows)) if rows else None

        left, right = edge_at(bx0 - 14, bx0 - 2), edge_at(bx1 + 2, bx1 + 14)
        if left is None and right is None:
            continue                                         # lettering nowhere near the site: it stays page
        left = right if left is None else left
        right = left if right is None else right
        line = left + (right - left) * (cols[bx0:bx1] - bx0) / max(1, bx1 - bx0 - 1)
        rows = np.arange(by0, by1)[:, None]
        inside = (rows >= line[None, :]) if top_edge else (rows <= line[None, :])
        box = np.zeros_like(site)
        box[by0:by1, bx0:bx1] = inside
        tags |= box & ~site
    site = site | tags

    # --- water: the lagoon's saturated blue ----------------------------------------------------
    water = (b > 120) & (b - r > 55) & (g - r > 25) & (b >= g - 12)

    # --- buildings: pale "paper" colours and the ink on them --------------------------------
    # white corridors and tags (spread small, bright); unit cells: pale blue, pink, yellow;
    # the podium's pale warm grey.  Never the water, the greens, the mid-grey paving.
    paper = (mx >= 196) & (spread <= 78) & ~water
    paper &= ~((g > r + 10) & (g > b + 10))                                           # pale greens of the lawns
    ink = (mx <= 78) & (spread <= 26)                                                 # outlines and lettering
    light = (paper & site).astype(np.uint8)
    # lettering and outlines are 1-3 px strokes on the paper: closing swallows them
    body = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    body = cv2.morphologyEx(body, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))          # hairlines, ripples, tile glints
    n, labels, stats, _ = cv2.connectedComponentsWithStats(body, connectivity=8)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        # a block of units is thousands of pixels; a bridge, a spa ring, the fountain are not
        keep[i] = stats[i, cv2.CC_STAT_AREA] >= BLOCK_MIN_AREA
    building = keep[labels]
    # the black outline round each block, and a rim for the anti-aliased edge
    grown = cv2.dilate(building.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    building |= grown & (ink | paper)
    building = cv2.dilate(building.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    # small enclosed leftovers (a stair, a lift core drawn dark inside a block)
    inv = (~building).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] <= HOLE_MAX_AREA:
            building |= labels == i
    # a clean edge: no single-pixel teeth where the rim met the paving
    building = cv2.morphologyEx(building.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    building = cv2.morphologyEx(building, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)) > 0
    building &= site & ~tags
    return site, building, tags, water, ink, paper, lettering > 0


BLOCK_MIN_AREA = 6000
TAG_PAD_X, TAG_PAD_Y = 40, 22      # px round a tag's lettering: its white box and a little more
HOLE_MAX_AREA = 2500


def _lagoon(water, site):
    """(filled lagoon mask, outline contour) -- one body: bridges crossed, islands inside."""
    import cv2
    import numpy as np

    m = (water & site).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    # a bridge is a pale strip up to ~40 px across the water: close over it
    joined = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (45, 45)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
    if n < 2:
        raise DeriveError("no water found")
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    body = (labels == biggest).astype(np.uint8)
    contours, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    outline = max(contours, key=cv2.contourArea)
    filled = np.zeros_like(body)
    cv2.drawContours(filled, [outline], -1, 1, thickness=-1)
    return filled > 0, outline


def _simplify(outline, lo: int = 60, hi: int = 120):
    import cv2

    eps = 1.0
    best = outline
    for _ in range(60):
        approx = cv2.approxPolyDP(outline, eps, True)
        if len(approx) <= hi:
            best = approx
            if len(approx) >= lo:
                break
            eps *= 0.85
        else:
            eps *= 1.25
    if not lo <= len(best) <= hi:
        raise DeriveError("the lagoon's outline did not simplify to %d..%d points (%d)" % (lo, hi, len(best)))
    return best.reshape(-1, 2)


def _centre(filled, points: int = 28):
    """A polyline along the lagoon's length, down its middle: the cheapest path from the
    widest water near its west end to the widest near its east end, where a step costs more
    the nearer it is to the edge (so the path keeps to the middle of the water), then
    smoothed and resampled. The lagoon is not a simple band -- it bends round islands and
    lobes -- so a column-by-column middle would leave the water."""
    import cv2
    import numpy as np
    from skimage.graph import route_through_array

    k = 4                                                        # work at quarter size
    small = cv2.resize(filled.astype(np.uint8), None, fx=1 / k, fy=1 / k, interpolation=cv2.INTER_NEAREST)
    dist = cv2.distanceTransform(small, cv2.DIST_L2, 5)
    ys, xs = np.nonzero(small)
    x0, x1 = int(xs.min()), int(xs.max())
    span = x1 - x0

    def widest(lo, hi):
        band = np.zeros_like(dist)
        band[:, lo:hi + 1] = dist[:, lo:hi + 1]
        y, x = np.unravel_index(int(np.argmax(band)), band.shape)
        return int(y), int(x)

    start, end = widest(x0, x0 + span // 8), widest(x1 - span // 8, x1)
    cost = np.where(small > 0, 1.0 + 400.0 / (1.0 + dist) ** 2, 1e9)
    path, _ = route_through_array(cost, start, end, fully_connected=True, geometric=True)
    path = np.array(path, dtype=np.float32)[:, ::-1] * k + k / 2      # (x, y) in full pixels
    # smooth (a moving average along the path), then resample evenly by length
    win = 31
    pad = np.pad(path, ((win // 2, win // 2), (0, 0)), mode="edge")
    kernel = np.ones(win, dtype=np.float32) / win
    smooth = np.stack([np.convolve(pad[:, 0], kernel, mode="valid"), np.convolve(pad[:, 1], kernel, mode="valid")], axis=1)
    seg = np.hypot(*np.diff(smooth, axis=0).T)
    along = np.concatenate([[0.0], np.cumsum(seg)])
    at = np.linspace(0.0, along[-1], points)
    line = np.stack([np.interp(at, along, smooth[:, 0]), np.interp(at, along, smooth[:, 1])], axis=1)
    for x, y in line:
        if not filled[int(round(y)), int(round(x))]:
            raise DeriveError("the centre line leaves the lagoon at (%d, %d)" % (x, y))
    return line


def derive(source: bytes) -> tuple[bytes, dict, dict]:
    """(PNG bytes with alpha, geometry in % of the picture, facts that were checked)."""
    import cv2
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(source)).convert("RGB")
    rgb = np.asarray(image)
    site, building, tags, water, ink, paper, lettering = _masks(rgb)
    filled, outline = _lagoon(water, site)

    out = rgb.copy()
    # a tag's notch: the flat colour of the hedge just inside it (median of the ring round the notch)
    n, labels = cv2.connectedComponents(tags.astype(np.uint8), connectivity=8)
    for i in range(1, n):
        notch = labels == i
        ring = (cv2.dilate(notch.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & site & ~tags & ~building
        if not ring.any():
            raise DeriveError("a tag's notch has nothing of the site beside it")
        out[notch] = np.median(rgb[ring], axis=0).astype(np.uint8)
    # a tag's own outline / lettering that fell inside the site but is not white
    stray = site & ~building & ~tags & ink & (cv2.dilate(tags.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0)
    if stray.any():
        ring = (cv2.dilate(stray.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0) & site & ~tags & ~building & ~stray
        out[stray] = np.median(rgb[ring], axis=0).astype(np.uint8)

    out[building] = NAVY
    inner = cv2.erode(building.astype(np.uint8), np.ones((2 * EDGE_PX + 1, 2 * EDGE_PX + 1), np.uint8)) > 0
    out[building & ~inner] = EDGE

    # alpha: the site, its rim softened by how white the rim pixel is (trees overhang the page)
    alpha = site.astype(np.float32)
    rim = site & ~(cv2.erode(site.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    whiteness = (rgb.min(axis=2).astype(np.float32) - 200.0) / 55.0
    alpha[rim] = np.clip(1.0 - whiteness[rim], 0.0, 1.0)
    alpha[building] = 1.0
    alpha[tags] = 1.0          # a flat patch with a straight edge: softening it by the source's whiteness
    #                            would cut the tag's lettering into its rim (seen 17 Nov: a comb of letter tops)

    ys, xs = np.nonzero(site)
    x0, y0 = max(0, int(xs.min()) - PAD), max(0, int(ys.min()) - PAD)
    x1, y1 = min(rgb.shape[1], int(xs.max()) + 1 + PAD), min(rgb.shape[0], int(ys.max()) + 1 + PAD)
    w, h = x1 - x0, y1 - y0

    # ---- the checks ---------------------------------------------------------------------------
    kept = site & ~building
    shown = kept & (alpha > 0.5)
    # What could still carry a label: a patch of the pale "paper" colours big enough to hold
    # one (the smallest label on the plan, a unit's area, is about 25 x 9 px on its cell).
    # Dark foliage is as black as lettering, so ink alone proves nothing; paper does.
    untouched = (out == rgb).all(axis=2)
    paper_left = cv2.morphologyEx((shown & paper & untouched).astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n2, labels2, stats2, _ = cv2.connectedComponentsWithStats(paper_left, connectivity=8)
    patches = sorted(([int(stats2[i, cv2.CC_STAT_AREA]), int(stats2[i, 0]) - x0, int(stats2[i, 1]) - y0,
                       int(stats2[i, 2]), int(stats2[i, 3])] for i in range(1, n2)
                      if stats2[i, cv2.CC_STAT_AREA] >= PATCH_MIN_AREA), reverse=True)
    # ... and on each such patch, is there ink (a stroke of lettering) at all?
    inked = []
    worst_strokes = 0
    for area, px, py, pw, ph in patches:
        box = (slice(py + y0, py + y0 + ph), slice(px + x0, px + x0 + pw))
        strokes = int((ink[box] & shown[box] & (labels2[box] > 0)).sum()) + int(
            (ink[box] & shown[box] & (cv2.dilate((labels2[box] > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)).sum())
        worst_strokes = max(worst_strokes, strokes)
        if strokes > INK_ON_PATCH_MAX:
            inked.append([px, py, pw, ph, strokes])
    # the tags' lettering and the lettering inside the blocks: not one pixel of it may still
    # show in the source's own colour
    letters_showing = int((lettering & untouched & (alpha > 0)).sum())
    ink_in_blocks = int((ink & building & untouched).sum())
    facts = {
        "site_px": int(site.sum()), "buildings_share": round(float(building.sum() / site.sum()), 4),
        "lagoon_share": round(float(filled.sum() / site.sum()), 4),
        "paper_patches_left": len(patches), "paper_patches": patches[:30], "inked_patches": inked,
        "most_ink_on_a_patch": worst_strokes, "tag_lettering_px_showing": letters_showing, "block_ink_px_showing": ink_in_blocks,
        "tag_notches": int(cv2.connectedComponents(tags.astype(np.uint8))[0] - 1),
        "source_crop": [x0, y0, x1, y1],
    }
    if not BUILDINGS_SHARE[0] <= facts["buildings_share"] <= BUILDINGS_SHARE[1]:
        raise DeriveError("the buildings' shapes cover %.0f%% of the site (expected %d-%d%%)" % (
            facts["buildings_share"] * 100, BUILDINGS_SHARE[0] * 100, BUILDINGS_SHARE[1] * 100))
    if not LAGOON_SHARE[0] <= facts["lagoon_share"] <= LAGOON_SHARE[1]:
        raise DeriveError("the lagoon covers %.0f%% of the site (expected %d-%d%%)" % (
            facts["lagoon_share"] * 100, LAGOON_SHARE[0] * 100, LAGOON_SHARE[1] * 100))
    if letters_showing or ink_in_blocks:
        raise DeriveError("%d px of the tags' lettering and %d px of ink inside the blocks still show" % (
            letters_showing, ink_in_blocks))
    if inked:
        raise DeriveError("%d pale patches that still show carry dark strokes -- lettering may survive (first at %s)" % (
            len(inked), inked[:3]))
    if len(patches) > PAPER_PATCHES_MAX:
        raise DeriveError("%d pale patches survive outside the buildings' shapes (%d on 17 Nov): look again" % (
            len(patches), PAPER_PATCHES_MAX))

    poly = _simplify(outline)
    centre = _centre(filled)

    def pct(points):
        return [[round(100.0 * (float(x) - x0) / w, 2), round(100.0 * (float(y) - y0) / h, 2)] for x, y in points]

    geometry = {
        "units": "% of the picture's width (x) and height (y)",
        "lagoon": pct(poly),
        "centre": pct(centre),
        "lagoon_bbox": pct([(float(outline[:, 0, 0].min()), float(outline[:, 0, 1].min())),
                            (float(outline[:, 0, 0].max()), float(outline[:, 0, 1].max()))]),
        "ends": pct([tuple(outline[int(outline[:, 0, 0].argmin()), 0]), tuple(outline[int(outline[:, 0, 0].argmax()), 0])]),
        "about": "ends = the water's westmost and eastmost points; lagoon = the water's edge as one closed path (bridges crossed, islands inside), measured on the "
                 "drawing's water colour; centre = the middle of the water band by band from its west end to its east end",
    }
    rgba = np.dstack([out, np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)])[y0:y1, x0:x1]
    rgba = rgba.copy()
    rgba[rgba[..., 3] == 0, :3] = NAVY                    # no page white hiding under alpha 0
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue(), geometry, facts


def look_sheets(png: bytes, geometry: dict, out_dir: Path) -> list[Path]:
    """Sheets to LOOK at: the result on the stage colour at full size in two halves, and the
    geometry drawn on it."""
    from PIL import Image, ImageDraw

    out_dir.mkdir(parents=True, exist_ok=True)
    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    stage = Image.new("RGBA", picture.size, (3, 5, 13, 255))
    stage.alpha_composite(picture)
    flat = stage.convert("RGB")
    made = []
    half = flat.width // 2 + 20
    for name, box in (("west", (0, 0, half, flat.height)), ("east", (flat.width - half, 0, flat.width, flat.height))):
        target = out_dir / ("site_clean_%s.png" % name)
        flat.crop(box).save(target)
        made.append(target)
    drawn = flat.copy()
    d = ImageDraw.Draw(drawn)
    to_px = lambda pts: [(x * flat.width / 100.0, y * flat.height / 100.0) for x, y in pts]  # noqa: E731
    lagoon = to_px(geometry["lagoon"])
    d.line(lagoon + [lagoon[0]], fill=(255, 60, 60), width=3)
    for x, y in lagoon:
        d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 230, 0))
    d.line(to_px(geometry["centre"]), fill=(255, 255, 255), width=4)
    target = out_dir / "site_clean_geometry.png"
    drawn.save(target)
    made.append(target)
    return made


def main(argv: list[str] | None = None) -> int:
    import json

    sources = ROOT / ".tmp" / "story-sources"
    source = sources / "web" / "3.project details" / "master plan" / "_Embassy World  Floor Plan" / "3.png"
    png, geometry, facts = derive(source.read_bytes())
    print(json.dumps(facts, indent=1))
    print("lagoon: %d points; centre: %d points; bbox %s" % (len(geometry["lagoon"]), len(geometry["centre"]), geometry["lagoon_bbox"]))
    for path in look_sheets(png, geometry, ROOT / ".tmp" / "ceremony-show" / "assets" / "site_clean_look"):
        print("look at ->", path)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
