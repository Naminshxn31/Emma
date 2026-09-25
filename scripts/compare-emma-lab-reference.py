"""Silhouette comparison: an Emma lab render against a concept-sheet panel crop.

The concept sheet (client/assets/emma/emma-character-concept-v1.png) is the single
source of truth for the 3D lab. This tool turns "does the render look like the
sheet" into numbers: it extracts a silhouette from a lab render (transparent PNG,
alpha = coverage) and from a sheet panel crop (dark background, white/chrome
character, white label text such as FRONT/SIDE at the bottom), normalises both
by a common anchor, and reports IoU, boundary distances and a width profile.

Usage
-----
One pair::

    .venv/Scripts/python.exe scripts/compare-emma-lab-reference.py \
        --render client/assets/emma-3d-lab/build/front.png \
        --ref <scratch>/emma-ref/full_front.png --out <dir> \
        [--ref-box x0,y0,x1,y1] [--render-box x0,y0,x1,y1] \
        [--anchor width|height|head] [--drop-ring] [--head-only] [--mirror] \
        [--label NAME] [--upsample N]

    --ref-box / --render-box   crop the input first (pixel coordinates of the
                               file, x1/y1 exclusive), e.g. to cut a panel out
                               of the full sheet with the boxes in panels.json.
    --anchor                   what "1.0" means when the two silhouettes are put
                               on the same canvas: bounding-box width (default),
                               bounding-box height, or the width of the topmost
                               blob (the head, the project's measurement unit).
    --drop-ring                remove the hover ring (thin, wide component at
                               the bottom) from both silhouettes.
    --head-only                crop the render to its topmost blob (head split
                               at the neck: the narrowest row plus the collar
                               rows of nearly the same extent above it) before
                               comparing; use this against the head_front/side/
                               back crops.
    --mirror                   mirror the render horizontally. The sheet's SIDE
                               panels face +x (right); the lab side camera at
                               (+9,0,3.25) looks along -x so the face ends up on
                               the left, hence side pairs need this.
    --upsample N               reference upsample factor before tracing
                               (default 4 for crops up to 300 px, else 1;
                               measured: x1 vs x4 moves IoU by ~0.005).

All standard views in one go::

    .venv/Scripts/python.exe scripts/compare-emma-lab-reference.py --views \
        --build-dir client/assets/emma-3d-lab/build \
        --ref-dir <scratch>/emma-ref --out <dir> [--anchor head]

    Runs full_front/full_side/full_back against front/side/back.png (ring kept)
    plus head-only pairs against head_front/head_side/head_back, mirrors the
    side pairs, writes one <pair>_metrics.json per pair, a reference-compare.json
    summary and reference-compare.png (contact sheet: overlay, difference and
    metric text for every pair). The --views default anchor is "head" because
    the head width is the unit the rest of the lab is measured in; a single
    pair defaults to "width" as documented above.

Outputs (per pair, in --out)
----------------------------
<label>_overlay.png      reference at 50 % over the render, both normalised;
                         magenta contour = reference mask, cyan = render mask
<label>_difference.png   grey = overlap, magenta = reference only, cyan = render only
<label>_masks.png        the two inputs next to the two masks the numbers came from
<label>_metrics.json     everything printed, plus the 24-row width profile

Metrics
-------
* IoU of the two normalised masks.
* Boundary distance (mean / p95 / max) from each mask's outline to the other's,
  symmetric, in reference-crop pixels (the crop as given, before upsampling)
  and in anchor units.
* 24-row width profile, top to bottom over the union of both figures: mean row
  extent of each mask per band, in anchor units and reference pixels, with the
  delta (render minus reference) and the left/right edge positions relative to
  the bounding-box centre, so a shift and a size change can be told apart.

How the reference mask is made (numbers measured on the emma-ref crops)
-----------------------------------------------------------------------
Panels smaller than 300 px are upsampled x4 (bicubic) first. The background is
~24 grey (border strip p1/p50/p99 = 19/24/32), Otsu lands at 125-130, chrome
shadows go down to 0 and the chrome rim along the silhouette is a mid-tone
(108-168) separated from the white body by a 1-3 px dark line, so plain Otsu
loses the rim and bites notches where chrome shadows touch the outline
(side band, back rim, torso tip). Therefore:

1. core = grey > Otsu; low = grey > bg + 0.35 * (Otsu - bg) or grey darker than
   the darkest background pixel (chrome shadow);
2. hysteresis: keep low pixels whose component contains core pixels (drops the
   body glow, which stays <= bg + 20);
3. closing with a ~1.6 % kernel (3-4 px on the original crop; armpit gaps are
   ~9 px and the torso-ring gap 7 px, so they survive) and hole filling (eyes,
   mouth, band);
4. label text: components that touch the bottom 18 % of the crop and are
   text-sized (height <= 12 % of the crop, width <= 50 %) are dropped -
   measured: text rows sit at 0.85-0.92 of the height, the ring at 0.72-0.79;
5. the hover ring (thin, wide component in the lower 40 % of the figure that
   lies below every body component - the chin segment under the mouth line
   is wide and thin too, but inside the ball's rows) is located on the Otsu
   core, where it is a separate 58x19 px component 7 px clear of the torso tip; its glow (36-131 grey for ~10 px around it) would
   otherwise weld it to the torso and inflate it, so within 0.2 x ring width
   of the core only Otsu-level pixels survive. --drop-ring removes it, else
   the crisp ring is kept - the render's ring has no glow;
6. keep the largest component plus every component of at least 2 % of it
   (gloves and the ring are separate components in the sheet).

Render mask: alpha > 127, same component rule, same optional ring drop.

Only the standard library, numpy, Pillow and cv2 are used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

CANVAS = 768            # normalised comparison canvas, px
FILL = 0.92             # fraction of the canvas the larger normalised figure may span
PROFILE_ROWS = 24
UPSAMPLE_MAX_SIDE = 300  # crops whose larger side is <= this get x4 bicubic before tracing
UPSAMPLE = 4
LOW_FRACTION = 0.35     # low threshold = bg + this * (otsu - bg); body glow stays below bg+20
CLOSE_FRACTION = 0.016  # closing kernel as a fraction of the larger side (3-4 px on the crop; armpit gaps are ~9 px)
TEXT_ZONE = 0.18        # label text lives in the bottom 18 % of a panel crop
TEXT_MAX_H = 0.12       # ... and is at most this tall (crop fraction)
TEXT_MAX_W = 0.50       # ... and at most this wide (crop fraction), even as one word
KEEP_FRACTION = 0.02    # secondary components survive when >= 2 % of the largest
RING_MIN_WIDTH = 0.30   # ring: at least this fraction of the figure width ...
RING_MAX_ASPECT = 0.45  # ... height/width at most this ...
RING_MIN_TOP = 0.60     # ... top at or below this fraction of the figure height ...
RING_MAX_AREA = 0.25    # ... and clearly smaller than the largest component
RING_HALO = 0.20        # glow halo stripped within this fraction of the ring width of its Otsu core
NECK_DROP = 0.50        # head split: width falls below this fraction of the running max ...
NECK_RISE = 0.25        # ... then rises again by this fraction above the running min
NECK_TOL = 0.15         # rows within this of the narrowest extent are collar, not head

BG = (28, 28, 32)
GREY = (170, 170, 175)
MAGENTA = (230, 72, 158)
CYAN = (60, 211, 241)
WHITE = (240, 240, 240)

VIEW_PAIRS = [
    # label, reference crop, render, head_only, mirror
    ("full_front", "full_front.png", "front.png", False, False),
    ("full_side", "full_side.png", "side.png", False, True),
    ("full_back", "full_back.png", "back.png", False, False),
    ("head_front", "head_front.png", "front.png", True, False),
    ("head_side", "head_side.png", "side.png", True, True),
    ("head_back", "head_back.png", "back.png", True, False),
]


# --------------------------------------------------------------------------- io
def parse_box(text):
    if text is None:
        return None
    parts = [int(p) for p in text.split(",")]
    if len(parts) != 4 or parts[2] <= parts[0] or parts[3] <= parts[1]:
        raise SystemExit(f"bad box {text!r}: want x0,y0,x1,y1 with x1>x0 and y1>y0")
    return tuple(parts)


def crop(array, box):
    if box is None:
        return array
    x0, y0, x1, y1 = box
    h, w = array.shape[:2]
    return array[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]


def load_rgb(path, box=None):
    return crop(np.asarray(Image.open(path).convert("RGB")), box)


def load_rgba(path, box=None):
    image = Image.open(path)
    if "A" not in image.getbands():
        raise SystemExit(f"{path}: no alpha channel; the render must be a transparent PNG")
    return crop(np.asarray(image.convert("RGBA")), box)


def load_font(size):
    for name in ("consola.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSansMono.ttf", "DejaVuSans.ttf"):
        for folder in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            candidate = folder / name
            if candidate.exists():
                try:
                    return ImageFont.truetype(str(candidate), size)
                except OSError:
                    pass
    return ImageFont.load_default()


# ------------------------------------------------------------------ mask helpers
def fill_holes(mask):
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), np.uint8)
    padded[1:-1, 1:-1] = mask
    flood = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(padded, flood, (0, 0), 1)
    return mask | (padded[1:-1, 1:-1] == 0)


def bbox(mask):
    """(x0, y0, x1, y1) with x1/y1 exclusive, or None for an empty mask."""
    ys = np.nonzero(mask.any(1))[0]
    xs = np.nonzero(mask.any(0))[0]
    if len(ys) == 0:
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def keep_main_components(mask):
    """Largest component plus everything >= KEEP_FRACTION of it. Returns (mask, kept, dropped)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask, 0, 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = np.zeros(n, bool)
    keep[1:] = areas >= KEEP_FRACTION * areas.max()
    return keep[labels], int(keep.sum()), int(n - 1 - keep.sum())


def find_ring(mask):
    """The hover ring: a thin, wide, separate component that floats below the body.
    Bool mask or None.

    Shape alone is not enough: on the x4 head_front crop the dark mouth line splits
    the ball's Otsu core and the chin segment below it is wide, thin and low in the
    figure too. The ring is the only such component that lies below *every* body
    component (full_front core: ring top 192 px, lowest body bottom 186 px; the chin
    segment sits inside the ball's rows)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n < 3:
        return None
    figure = bbox(mask)
    fw, fh = figure[2] - figure[0], figure[3] - figure[1]
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_area = int(areas.max())
    ring_shaped, body_bottom = [], None
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if a < KEEP_FRACTION * largest_area:
            continue                                   # specks decide nothing
        if (w >= RING_MIN_WIDTH * fw and h <= RING_MAX_ASPECT * w
                and y >= figure[1] + RING_MIN_TOP * fh and a <= RING_MAX_AREA * largest_area):
            ring_shaped.append((y, i))
        else:
            body_bottom = y + h if body_bottom is None else max(body_bottom, y + h)
    if not ring_shaped or body_bottom is None:
        return None
    ring = np.zeros(mask.shape, bool)
    for y, i in ring_shaped:            # arcs of one ring may be separate components
        if y >= body_bottom - 0.01 * fh:
            ring |= labels == i
    return ring if ring.any() else None


def row_extents(mask):
    """Per-row (width, xmin, xmax); width 0 and xmin/xmax -1 on empty rows."""
    h, w = mask.shape
    columns = np.arange(w)[None, :]
    any_row = mask.any(1)
    xmin = np.where(mask, columns, w).min(1)
    xmax = np.where(mask, columns, -1).max(1)
    width = np.where(any_row, xmax - xmin + 1, 0)
    xmin = np.where(any_row, xmin, -1)
    xmax = np.where(any_row, xmax, -1)
    return width, xmin, xmax


def head_split(mask):
    """Topmost blob of a figure: rows above the narrowest neck row, then the component
    holding the topmost pixel. Returns (head_mask, cut_row); cut_row None = no neck
    found, the whole mask is the head (head-only crops end that way by design).

    Measured on build/front.png: row extent 402 px at the head, 108 px at the neck,
    372 px at the torso; the sheet's full views have a dark neck so the head is a
    separate component there (extent 0 between them). Both cases take the same path:
    the extent drops below NECK_DROP of the running max, keeps falling to a running
    minimum, and then rises by NECK_RISE of the running max again; the cut is the top
    of the run of collar rows ending at that minimum. A round bottom with nothing
    below it never rises again."""
    width, _, _ = row_extents(mask)
    rows = np.nonzero(width)[0]
    if len(rows) == 0:
        return mask, None
    top, bottom = int(rows[0]), int(rows[-1])
    running_max = 0
    drop = None
    for y in range(top, bottom + 1):
        running_max = max(running_max, int(width[y]))
        if width[y] < NECK_DROP * running_max:
            drop = y
            break
    if drop is None:
        return mask, None
    running_min, cut = int(width[drop]), drop
    recovered = False
    for y in range(drop, bottom + 1):
        if width[y] < running_min:
            running_min, cut = int(width[y]), y
        if width[y] >= running_min + NECK_RISE * running_max:
            recovered = True
            break
    if not recovered:
        return mask, None
    # the narrowest row is the bottom of the collar; walk back up through rows of
    # (nearly) the same extent so the cut lands where the ball ends, like the
    # sheet's head crops (build/front.png: 118/108 px collar rows above a 162 px cap)
    while cut - 1 >= top and width[cut - 1] <= running_min * (1 + NECK_TOL) and running_min > 0:
        cut -= 1
    head = mask.copy()
    head[cut:] = False
    n, labels = cv2.connectedComponents(head.astype(np.uint8), connectivity=8)
    top_y = int(np.nonzero(head.any(1))[0][0])
    top_x = int(np.nonzero(head[top_y])[0][0])
    return labels == labels[top_y, top_x], int(cut)


def anchor_size(mask, anchor):
    box = bbox(mask)
    if anchor == "width":
        return box[2] - box[0]
    if anchor == "height":
        return box[3] - box[1]
    if anchor == "head":
        head, _ = head_split(mask)
        hb = bbox(head)
        return hb[2] - hb[0]
    raise SystemExit(f"unknown anchor {anchor!r}")


# ------------------------------------------------------------- reference mask
def drop_text(mask):
    """Remove label text: small components touching the bottom TEXT_ZONE of the crop.
    Returns (mask, dropped_count). Measured: text rows sit at 0.85-0.92 of the crop
    height, the hover ring at 0.72-0.79, so the zone separates them."""
    h, w = mask.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    dropped = 0
    for i in range(1, n):
        x, y, cw, ch, a = (int(v) for v in stats[i])
        if y + ch >= (1 - TEXT_ZONE) * h and ch <= TEXT_MAX_H * h and cw <= TEXT_MAX_W * w:
            mask[labels == i] = False
            dropped += 1
    return mask, dropped


def reference_mask(rgb, upsample=None, drop_ring=False):
    """Silhouette of a sheet panel crop. Returns dict(mask, image, up, core, info)."""
    h0, w0 = rgb.shape[:2]
    if upsample is None:
        upsample = UPSAMPLE if max(h0, w0) <= UPSAMPLE_MAX_SIDE else 1
    image = rgb
    if upsample > 1:
        image = cv2.resize(rgb, (w0 * upsample, h0 * upsample), interpolation=cv2.INTER_CUBIC)
    grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    h, w = grey.shape
    strip = max(3, int(round(0.02 * max(h, w))))
    border = np.concatenate([grey[:strip].ravel(), grey[-strip:].ravel(),
                             grey[:, :strip].ravel(), grey[:, -strip:].ravel()])
    bg = float(np.median(border))
    otsu, _ = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if otsu <= bg + 20:
        raise SystemExit("reference crop has no bright figure against its background "
                         f"(background {bg:.0f}, Otsu {otsu:.0f})")
    low_t = bg + LOW_FRACTION * (otsu - bg)
    dark_t = max(6.0, float(border.min()) - 2.0)
    core = grey > otsu
    # the ring is found on the crisp Otsu core, where it is a separate component
    # (full_front: 58x19 px, 7 px clear of the torso tip); in the hysteresis mask its
    # glow (36-131 grey for ~10 px around it) would weld it to the torso
    core_clean, _ = drop_text(core.copy())
    ring_core = find_ring(core_clean)
    low = (grey > low_t) | (grey < dark_t)
    # hysteresis: a low component only counts when it holds core pixels
    n, labels = cv2.connectedComponents(low.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(labels[core])] = True
    keep[0] = False
    mask = keep[labels]
    k = max(3, int(round(CLOSE_FRACTION * max(h, w))) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)
    mask = fill_holes(mask)
    mask, text_dropped = drop_text(mask)
    if not mask.any():
        raise SystemExit("reference mask is empty after dropping label text")
    ring_state = "none"
    if ring_core is not None:
        # strip the glow: near the ring only Otsu-level pixels survive (torso-tip core
        # pixels stay, halo and the filled ring hole go); the render's ring has no glow
        rb = bbox(ring_core)
        radius = max(k, int(round(RING_HALO * (rb[2] - rb[0]))))
        zone = cv2.dilate(ring_core.astype(np.uint8),
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))).astype(bool)
        mask &= ~(zone & ~core)
        if drop_ring:
            mask &= ~ring_core
            ring_state = "dropped"
        else:
            mask |= ring_core
            ring_state = "kept (re-cut at Otsu)"
    elif drop_ring:
        ring = find_ring(mask)          # fallback: a ring the Otsu core did not isolate
        if ring is not None:
            mask &= ~ring
            ring_state = "dropped (found on the hysteresis mask only)"
    mask, kept, dropped = keep_main_components(mask)
    box = bbox(mask)
    info = {
        "upsample": upsample, "background": bg, "otsu": float(otsu), "low_threshold": round(low_t, 1),
        "dark_threshold": round(dark_t, 1), "closing_kernel_px": k / upsample,
        "text_components_dropped": text_dropped, "ring": ring_state,
        "components_kept": kept, "components_dropped": dropped,
        "bbox_crop_px": [round(v / upsample, 2) for v in box],
        "area_crop_px": round(float(mask.sum()) / upsample ** 2, 1),
    }
    return {"mask": mask, "image": image, "up": upsample, "core": core, "info": info}


# ---------------------------------------------------------------- render mask
def render_mask(rgba, drop_ring=False, head_only=False):
    """Silhouette of a lab render: alpha > 127. Returns dict(mask, image, info)."""
    alpha = rgba[..., 3]
    mask = alpha > 127
    if not mask.any():
        raise SystemExit("render alpha is empty")
    mask, kept, dropped = keep_main_components(mask)
    ring = find_ring(mask)
    ring_state = "none"
    if ring is not None and drop_ring:
        mask &= ~ring
        ring_state = "dropped"
    elif ring is not None:
        ring_state = "kept"
    info = {"ring": ring_state, "components_kept": kept, "components_dropped": dropped}
    if head_only:
        head, cut = head_split(mask)
        hb = bbox(head)
        margin = 4
        x0, y0 = max(0, hb[0] - margin), max(0, hb[1] - margin)
        x1, y1 = min(mask.shape[1], hb[2] + margin), min(mask.shape[0], hb[3] + margin)
        rgba = rgba[y0:y1, x0:x1]
        mask = head[y0:y1, x0:x1]
        info.update({"head_cut_row": cut, "head_crop_box": [x0, y0, x1, y1], "ring": "outside head crop"})
    box = bbox(mask)
    info.update({"bbox_px": list(box), "area_px": int(mask.sum())})
    flat = np.empty(rgba.shape[:2] + (3,), np.uint8)
    flat[:] = BG
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    flat = (rgba[..., :3].astype(np.float32) * a + flat.astype(np.float32) * (1 - a)).astype(np.uint8)
    return {"mask": mask, "image": flat, "info": info}


# --------------------------------------------------------------- normalising
def normalise(mask, image, scale):
    """Scale mask+image by `scale` and centre the mask's bounding box on the canvas.
    Returns (canvas_mask, canvas_image, offset) with offset = canvas px of source (0,0)."""
    h, w = mask.shape
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    m = cv2.resize(mask.astype(np.float32), (nw, nh), interpolation=interp) >= 0.5
    im = cv2.resize(image, (nw, nh), interpolation=interp)
    box = bbox(m)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    ox, oy = int(round(CANVAS / 2 - cx)), int(round(CANVAS / 2 - cy))
    canvas_mask = np.zeros((CANVAS, CANVAS), bool)
    canvas_image = np.empty((CANVAS, CANVAS, 3), np.uint8)
    canvas_image[:] = BG
    sx0, sy0 = max(0, -ox), max(0, -oy)
    dx0, dy0 = max(0, ox), max(0, oy)
    cw, ch = min(nw - sx0, CANVAS - dx0), min(nh - sy0, CANVAS - dy0)
    if cw > 0 and ch > 0:
        canvas_mask[dy0:dy0 + ch, dx0:dx0 + cw] = m[sy0:sy0 + ch, sx0:sx0 + cw]
        canvas_image[dy0:dy0 + ch, dx0:dx0 + cw] = im[sy0:sy0 + ch, sx0:sx0 + cw]
    clipped = int(canvas_mask.sum()) != int(m.sum())   # figure pixels lost off-canvas
    return canvas_mask, canvas_image, (ox, oy), clipped


def outline(mask):
    m = mask.astype(np.uint8)
    return (m - cv2.erode(m, np.ones((3, 3), np.uint8))).astype(bool)


def boundary_stats(mask_a, mask_b):
    """Symmetric outline-to-outline distances in canvas px: dict of arrays per direction."""
    ea, eb = outline(mask_a), outline(mask_b)
    to_b = cv2.distanceTransform((~eb).astype(np.uint8), cv2.DIST_L2, 5)
    to_a = cv2.distanceTransform((~ea).astype(np.uint8), cv2.DIST_L2, 5)
    return {"a_to_b": to_b[ea], "b_to_a": to_a[eb]}


def summarise(distances):
    if len(distances) == 0:
        return {"mean": None, "p95": None, "max": None}
    return {"mean": float(np.mean(distances)), "p95": float(np.percentile(distances, 95)),
            "max": float(np.max(distances))}


def width_profile(ref_mask, ren_mask, K, ref_px_per_canvas_px):
    union = ref_mask | ren_mask
    box = bbox(union)
    edges = np.linspace(box[1], box[3], PROFILE_ROWS + 1)
    rw, rmin, rmax = row_extents(ref_mask)
    sw, smin, smax = row_extents(ren_mask)
    centre = CANVAS / 2
    rows = []
    for i in range(PROFILE_ROWS):
        y0, y1 = int(round(edges[i])), int(round(edges[i + 1]))
        y1 = max(y1, y0 + 1)
        band = slice(y0, y1)

        def edge_mean(vals):
            vals = vals[band]
            vals = vals[vals >= 0]
            return float((vals.mean() - centre) / K) if len(vals) else None

        ref_w = float(rw[band].mean()) / K
        ren_w = float(sw[band].mean()) / K
        rows.append({
            "row": i, "canvas_y": [y0, y1],
            "ref_width": round(ref_w, 4), "render_width": round(ren_w, 4),
            "delta_width": round(ren_w - ref_w, 4),
            "delta_width_ref_px": round((ren_w - ref_w) * K * ref_px_per_canvas_px, 2),
            "ref_left": edge_mean(rmin), "ref_right": edge_mean(rmax),
            "render_left": edge_mean(smin), "render_right": edge_mean(smax),
        })
    for r in rows:
        for key in ("ref_left", "ref_right", "render_left", "render_right"):
            if r[key] is not None:
                r[key] = round(r[key], 4)
    return rows


# ------------------------------------------------------------------ drawing
def draw_overlay(ref_image, ren_image, ref_mask, ren_mask):
    blend = (0.5 * ref_image.astype(np.float32) + 0.5 * ren_image.astype(np.float32)).astype(np.uint8)
    thick = np.ones((3, 3), np.uint8)
    ref_edge = cv2.dilate(outline(ref_mask).astype(np.uint8), thick).astype(bool)
    ren_edge = cv2.dilate(outline(ren_mask).astype(np.uint8), thick).astype(bool)
    blend[ren_edge] = CYAN
    blend[ref_edge] = MAGENTA
    return blend


def draw_difference(ref_mask, ren_mask):
    diff = np.empty((CANVAS, CANVAS, 3), np.uint8)
    diff[:] = BG
    diff[ref_mask & ren_mask] = GREY
    diff[ref_mask & ~ren_mask] = MAGENTA
    diff[ren_mask & ~ref_mask] = CYAN
    return diff


def draw_masks_sheet(ref, ren):
    """Inputs next to the masks they produced, all at the reference's upsampled scale."""
    def to_rgb(mask):
        out = np.zeros(mask.shape + (3,), np.uint8)
        out[mask] = WHITE
        return out

    tiles = [ref["image"], to_rgb(ref["mask"]), ren["image"], to_rgb(ren["mask"])]
    height = max(t.shape[0] for t in tiles)
    scaled = []
    for t in tiles:
        s = height / t.shape[0]
        scaled.append(cv2.resize(t, (max(1, int(round(t.shape[1] * s))), height),
                                 interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_NEAREST))
    gap = np.full((height, 6, 3), 90, np.uint8)
    pieces = []
    for i, t in enumerate(scaled):
        if i:
            pieces.append(gap)
        pieces.append(t)
    return np.concatenate(pieces, axis=1)


def label_image(array, text, font):
    image = Image.fromarray(array)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 22], fill=(0, 0, 0))
    draw.text((6, 3), text, fill=WHITE, font=font)
    return np.asarray(image)


# ------------------------------------------------------------------ one pair
def compare_pair(render_path, ref_path, out_dir, label, ref_box=None, render_box=None,
                 anchor="width", drop_ring=False, head_only=False, mirror=False, upsample=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = reference_mask(load_rgb(ref_path, ref_box), upsample=upsample, drop_ring=drop_ring)
    rgba = load_rgba(render_path, render_box)
    if mirror:
        rgba = np.ascontiguousarray(rgba[:, ::-1])
    ren = render_mask(rgba, drop_ring=drop_ring, head_only=head_only)

    a_ref = anchor_size(ref["mask"], anchor)
    a_ren = anchor_size(ren["mask"], anchor)
    if a_ref <= 0 or a_ren <= 0:
        raise SystemExit("anchor measured as zero")
    ref_box_px, ren_box_px = bbox(ref["mask"]), bbox(ren["mask"])
    extent = max(max(ref_box_px[2] - ref_box_px[0], ref_box_px[3] - ref_box_px[1]) / a_ref,
                 max(ren_box_px[2] - ren_box_px[0], ren_box_px[3] - ren_box_px[1]) / a_ren)
    K = FILL * CANVAS / extent                     # canvas px per anchor unit
    s_ref, s_ren = K / a_ref, K / a_ren
    ref_mask, ref_image, ref_offset, ref_clipped = normalise(ref["mask"], ref["image"], s_ref)
    ren_mask, ren_image, ren_offset, ren_clipped = normalise(ren["mask"], ren["image"], s_ren)
    ref_px_per_canvas_px = 1.0 / (s_ref * ref["up"])   # reference-crop pixels (as given) per canvas px

    inter = int((ref_mask & ren_mask).sum())
    union = int((ref_mask | ren_mask).sum())
    iou = inter / union if union else 0.0
    dist = boundary_stats(ren_mask, ref_mask)
    both = np.concatenate([dist["a_to_b"], dist["b_to_a"]])

    def in_units(stats):
        return {k: (None if v is None else {"ref_px": round(v * ref_px_per_canvas_px, 2),
                                             "anchor": round(v / K, 4)}) for k, v in stats.items()}

    ref_head, ref_cut = head_split(ref["mask"])
    ren_head, ren_cut = head_split(ren["mask"])
    ref_head_w = bbox(ref_head)[2] - bbox(ref_head)[0]
    ren_head_w = bbox(ren_head)[2] - bbox(ren_head)[0]
    up = ref["up"]
    metrics = {
        "label": label,
        "reference": str(ref_path), "render": str(render_path),
        "ref_box": ref_box, "render_box": render_box,
        "anchor": anchor, "drop_ring": drop_ring, "head_only": head_only, "mirror_render": mirror,
        "anchor_px": {"ref_crop_px": round(a_ref / up, 2), "render_px": a_ren},
        "canvas_px_per_anchor": round(K, 3),
        "canvas_clipped": ref_clipped or ren_clipped,
        "reference_mask": ref["info"], "render_mask": ren["info"],
        "proportions": {
            "ref": {"bbox_w_over_anchor": round((ref_box_px[2] - ref_box_px[0]) / a_ref, 4),
                    "bbox_h_over_anchor": round((ref_box_px[3] - ref_box_px[1]) / a_ref, 4),
                    "head_width_crop_px": round(ref_head_w / up, 2),
                    "head_width_over_anchor": round(ref_head_w / a_ref, 4),
                    "height_in_heads": round((ref_box_px[3] - ref_box_px[1]) / ref_head_w, 4),
                    "head_cut_row_crop_px": None if ref_cut is None else round(ref_cut / up, 2)},
            "render": {"bbox_w_over_anchor": round((ren_box_px[2] - ren_box_px[0]) / a_ren, 4),
                       "bbox_h_over_anchor": round((ren_box_px[3] - ren_box_px[1]) / a_ren, 4),
                       "head_width_px": ren_head_w,
                       "head_width_over_anchor": round(ren_head_w / a_ren, 4),
                       "height_in_heads": round((ren_box_px[3] - ren_box_px[1]) / ren_head_w, 4),
                       "head_cut_row_px": ren_cut},
        },
        "iou": round(iou, 4),
        "intersection_px": inter, "union_px": union,
        "boundary_distance": {
            "symmetric": in_units(summarise(both)),
            "render_to_ref": in_units(summarise(dist["a_to_b"])),
            "ref_to_render": in_units(summarise(dist["b_to_a"])),
            "note": "ref_px = pixels of the reference crop as given (before upsampling); "
                    "anchor = fraction of the anchor size",
        },
        "width_profile": {
            "rows": PROFILE_ROWS, "order": "top to bottom over the union of both figures",
            "units": "widths and edges in anchor units (edges relative to the canvas centre); "
                     "delta = render - ref",
            "bands": width_profile(ref_mask, ren_mask, K, ref_px_per_canvas_px),
        },
    }

    font = load_font(15)
    overlay = label_image(draw_overlay(ref_image, ren_image, ref_mask, ren_mask),
                          f"{label}  overlay: reference 50% over render  magenta=ref cyan=render", font)
    difference = label_image(draw_difference(ref_mask, ren_mask),
                             f"{label}  grey=overlap  magenta=ref only  cyan=render only  IoU {iou:.3f}", font)
    masks = draw_masks_sheet(ref, ren)
    Image.fromarray(overlay).save(out_dir / f"{label}_overlay.png")
    Image.fromarray(difference).save(out_dir / f"{label}_difference.png")
    Image.fromarray(masks).save(out_dir / f"{label}_masks.png")
    (out_dir / f"{label}_metrics.json").write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    return metrics, overlay, difference


def print_summary(m):
    b = m["boundary_distance"]["symmetric"]
    print(f"[{m['label']}] IoU {m['iou']:.4f}  boundary mean {b['mean']['ref_px']} px  "
          f"p95 {b['p95']['ref_px']} px  max {b['max']['ref_px']} px (reference-crop px)  "
          f"anchor={m['anchor']} ref {m['anchor_px']['ref_crop_px']} px / render {m['anchor_px']['render_px']} px  "
          f"ref ring: {m['reference_mask']['ring']}  text dropped: {m['reference_mask']['text_components_dropped']}"
          + ("  CANVAS CLIPPED" if m["canvas_clipped"] else ""))
    pr = m["proportions"]
    print(f"    height in heads  ref {pr['ref']['height_in_heads']}  render {pr['render']['height_in_heads']}   "
          f"bbox h/w  ref {pr['ref']['bbox_h_over_anchor'] / pr['ref']['bbox_w_over_anchor']:.3f}  "
          f"render {pr['render']['bbox_h_over_anchor'] / pr['render']['bbox_w_over_anchor']:.3f}")
    widest = max(m["width_profile"]["bands"], key=lambda r: abs(r["delta_width"]))
    print(f"    largest width delta: row {widest['row']}/{PROFILE_ROWS} render-ref = "
          f"{widest['delta_width']:+.3f} anchor ({widest['delta_width_ref_px']:+.1f} ref px)")


# ----------------------------------------------------------------- views mode
def contact_sheet(results, out_path):
    font, small = load_font(17), load_font(14)
    tile = CANVAS // 2
    panel_w = 420
    row_h = tile
    sheet = Image.new("RGB", (tile * 2 + panel_w, row_h * len(results)), BG)
    draw = ImageDraw.Draw(sheet)
    for i, (m, overlay, difference) in enumerate(results):
        y = i * row_h
        sheet.paste(Image.fromarray(overlay).resize((tile, tile), Image.LANCZOS), (0, y))
        sheet.paste(Image.fromarray(difference).resize((tile, tile), Image.LANCZOS), (tile, y))
        x = tile * 2 + 12
        b = m["boundary_distance"]["symmetric"]
        pr = m["proportions"]
        lines = [
            f"{m['label']}",
            f"ref {Path(m['reference']).name}  render {Path(m['render']).name}"
            + ("  (render mirrored)" if m["mirror_render"] else ""),
            f"anchor {m['anchor']}: ref {m['anchor_px']['ref_crop_px']} px, render {m['anchor_px']['render_px']} px",
            f"IoU {m['iou']:.3f}",
            f"boundary mean {b['mean']['ref_px']} / p95 {b['p95']['ref_px']} / max {b['max']['ref_px']} ref px",
            f"           = {b['mean']['anchor']:.3f} / {b['p95']['anchor']:.3f} / {b['max']['anchor']:.3f} anchor",
            f"height in heads: ref {pr['ref']['height_in_heads']:.2f}  render {pr['render']['height_in_heads']:.2f}",
            f"bbox h/w: ref {pr['ref']['bbox_h_over_anchor'] / pr['ref']['bbox_w_over_anchor']:.3f}"
            f"  render {pr['render']['bbox_h_over_anchor'] / pr['render']['bbox_w_over_anchor']:.3f}",
            f"ref ring {m['reference_mask']['ring']}; text dropped {m['reference_mask']['text_components_dropped']}",
        ]
        ty = y + 10
        for j, line in enumerate(lines):
            draw.text((x, ty), line, fill=WHITE, font=font if j == 0 else small)
            ty += 22 if j == 0 else 18
        # width profile bars: magenta = reference, cyan = render (anchor units, top to bottom)
        ty += 6
        draw.text((x, ty), "width profile  magenta=ref  cyan=render", fill=GREY, font=small)
        ty += 18
        bands = m["width_profile"]["bands"]
        scale = (panel_w - 40) / max(1e-6, max(max(r["ref_width"], r["render_width"]) for r in bands))
        bar_h = max(2, (row_h - (ty - y) - 8) // (len(bands) * 2))
        for r in bands:
            draw.rectangle([x, ty, x + r["ref_width"] * scale, ty + bar_h - 1], fill=MAGENTA)
            draw.rectangle([x, ty + bar_h, x + r["render_width"] * scale, ty + 2 * bar_h - 1], fill=CYAN)
            ty += 2 * bar_h
        draw.line([(0, y + row_h - 1), (sheet.width, y + row_h - 1)], fill=(70, 70, 76))
    sheet.save(out_path)


def views_mode(args):
    build = Path(args.build_dir)
    ref_dir = Path(args.ref_dir)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    anchor = args.anchor or "head"
    results, summary = [], {"anchor": anchor, "pairs": {}}
    for label, ref_name, render_name, head_only, mirror in VIEW_PAIRS:
        ref_path, render_path = ref_dir / ref_name, build / render_name
        if not ref_path.exists() or not render_path.exists():
            print(f"[{label}] skipped: missing {ref_path if not ref_path.exists() else render_path}")
            summary["pairs"][label] = {"skipped": "missing input"}
            continue
        metrics, overlay, difference = compare_pair(
            render_path, ref_path, out, label, anchor=anchor, drop_ring=args.drop_ring,
            head_only=head_only, mirror=mirror, upsample=args.upsample)
        print_summary(metrics)
        results.append((metrics, overlay, difference))
        b = metrics["boundary_distance"]["symmetric"]
        summary["pairs"][label] = {
            "iou": metrics["iou"],
            "boundary_ref_px": {k: b[k]["ref_px"] for k in ("mean", "p95", "max")},
            "boundary_anchor": {k: b[k]["anchor"] for k in ("mean", "p95", "max")},
            "height_in_heads": {"ref": metrics["proportions"]["ref"]["height_in_heads"],
                                "render": metrics["proportions"]["render"]["height_in_heads"]},
            "mirror_render": mirror, "head_only": head_only,
            "metrics_json": f"{label}_metrics.json",
        }
    if results:
        contact_sheet(results, out / "reference-compare.png")
    (out / "reference-compare.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"wrote {out / 'reference-compare.png'} and {out / 'reference-compare.json'}")


# ----------------------------------------------------------------------- cli
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="Full usage notes are in the module docstring.")
    parser.add_argument("--render", help="lab render, transparent PNG (alpha = coverage)")
    parser.add_argument("--ref", help="sheet panel crop (dark background, label text at the bottom)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--ref-box", type=parse_box, help="x0,y0,x1,y1 crop of the reference file first")
    parser.add_argument("--render-box", type=parse_box, help="x0,y0,x1,y1 crop of the render file first")
    parser.add_argument("--anchor", choices=("width", "height", "head"),
                        help="size that maps both figures to the same scale (single pair: width; --views: head)")
    parser.add_argument("--drop-ring", action="store_true", help="remove the hover ring from both masks")
    parser.add_argument("--head-only", action="store_true", help="crop the render to its topmost blob first")
    parser.add_argument("--mirror", action="store_true", help="mirror the render horizontally (side views)")
    parser.add_argument("--label", help="output file prefix (default: <ref stem>_vs_<render stem>)")
    parser.add_argument("--upsample", type=int, help="reference upsample factor (default: 4 for crops <= 300 px)")
    parser.add_argument("--views", action="store_true", help="run the standard front/side/back + head pairs")
    parser.add_argument("--build-dir", default=str(Path(__file__).resolve().parents[1] / "client/assets/emma-3d-lab/build"),
                        help="--views: directory with front/side/back.png")
    parser.add_argument("--ref-dir", help="--views: directory with full_*.png and head_*.png crops")
    args = parser.parse_args(argv)

    if args.views:
        if not args.ref_dir:
            parser.error("--views needs --ref-dir")
        views_mode(args)
        return 0
    if not args.render or not args.ref:
        parser.error("--render and --ref are required (or use --views)")
    label = args.label or f"{Path(args.ref).stem}_vs_{Path(args.render).stem}"
    metrics, _, _ = compare_pair(args.render, args.ref, args.out, label, ref_box=args.ref_box,
                                 render_box=args.render_box, anchor=args.anchor or "width",
                                 drop_ring=args.drop_ring, head_only=args.head_only,
                                 mirror=args.mirror, upsample=args.upsample)
    print_summary(metrics)
    print(json.dumps({k: v for k, v in metrics.items() if k != "width_profile"}, indent=1))
    print(f"wrote {Path(args.out) / (label + '_metrics.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
