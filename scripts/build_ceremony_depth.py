#!/usr/bin/env python3
"""Depth maps for the ceremony film's pictures (parallax: a camera that moves a little).

    python scripts/build_ceremony_depth.py               # every photo / render in the index
    python scripts/build_ceremony_depth.py ID [ID ...]   # only these (any picture in the index)
    python scripts/build_ceremony_depth.py --check       # verify the maps on disk, write nothing
    python scripts/build_ceremony_depth.py --proof ID .. # sheets to LOOK at: picture | depth | moved

Reads data/ceremony/assets/index.json (written by scripts/build_ceremony_hires.py), runs
Depth-Anything-V2-Small on the CPU through `transformers` for each picture and writes
`<id>.depth.png` beside it: 8-bit grayscale, long side <= 1024, NEAR = WHITE -- and
`<id>.depth_soft.png`, the same map blurred by 1.5% of its width at half the size, which is
the one to displace a picture with (see SOFT_BLUR). The map is recorded in the picture's
index entry as {file, sha256, w, h, of, model, near, soft: {file, sha256, w, h, blur}};
`of` is the sha256 of the picture it was computed on, so a picture that is rebuilt loses
its map instead of keeping another picture's.

By default only photos and renders get a map (the index's `class` "render", plus the ids
in data/ceremony/asset_candidates.json `depth.also`): a map, a plan or a model floating on
black has no scene for a camera to move in, and the network invents one.

A map is written only if it passes its checks: the picture on disk is the indexed bytes
and is not blank, the prediction is finite, the map has the picture's shape, is not flat
and is a surface (slow slopes and a few edges, not speckle). These catch a broken run, not
a wrong idea: the network answers with a confident scene whatever it is shown.
The model's weights (~100 MB) come from Hugging Face once; no picture leaves this machine.

What the maps are good for, measured by eye on 17 Nov (the --proof sheets): see
.tmp/ceremony-show/assets/hires.md and the report of that day.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT, ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import build_ceremony_hires as hires  # noqa: E402  (the index, its checks, the folders)

MODEL = "depth-anything/Depth-Anything-V2-Small-hf"
#: The weights that made the maps in the index (model.safetensors, 99 MB): another
#: revision is another network, and its maps are not comparable with these.
REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
LONG_SIDE = 1024
#: The network's input: the shorter side in pixels (a multiple of 14). 518 is what it was
#: trained at; 770 keeps thin things (chair legs, railings, palm trunks) that 518 smears,
#: and on these pictures still orders the planes correctly (compared by eye, 17 Nov).
INPUT_SHORT = 770
#: Depth as the network gives it is "disparity-like": clip the far and near 0.5% so one
#: speck does not squeeze the whole scene into a few grey levels.
CLIP = (0.5, 99.5)
FLAT_STD = 10.0            # of 255: below this the map is flat
BLANK_STD = 6.0            # of 255: a picture with less contrast than this shows nothing
ROUGH_MAX = 4.0            # mean step between neighbours, of 255 (the good maps: 0.3 .. 1.3; noise: > 40)
#: The map a displacement shader should read. Looked at on 17 Nov (depth_proof / zoomed
#: crops at the stage's 1920 px): moved with the RAW map, leaves against the sky and thin
#: slats beside a cushion break into fragments even at a 1.5% camera move; with the map
#: blurred by 1.5% of its width the same pictures hold at 1.5-2% and interiors at 3%.
SOFT_BLUR = 0.015
SOFT_LONG_SIDE = 512
ENDS_MAX = 0.10            # pure black AND pure white each above this = not a depth map
DEPTH_CLASSES = ("render", "photo")


class Refused(Exception):
    pass


def default_ids(entries: list[dict]) -> list[str]:
    doc = hires.load_candidates()
    also = set((doc.get("depth") or {}).get("also") or [])
    never = set((doc.get("depth") or {}).get("never") or [])
    return [e["id"] for e in entries
            if e.get("ok") and e.get("kind") == "picture" and e["id"] not in never
            and (e.get("class") in DEPTH_CLASSES or e["id"] in also)]


_net = None
#: The network's own preprocessing (its preprocessor_config.json): bicubic resize to a
#: multiple of its 14 px patch, 0..1, ImageNet mean / std. Done here with PIL and numpy:
#: transformers' image processors need torchvision, which this project does not install.
_MEAN = (0.485, 0.456, 0.406)
_STD = (0.229, 0.224, 0.225)
_PATCH = 14


def _model():
    global _net
    if _net is None:
        import torch
        from transformers import AutoModelForDepthEstimation

        torch.manual_seed(0)
        try:                 # the cached weights first: no request leaves the machine once they are here
            _net = AutoModelForDepthEstimation.from_pretrained(MODEL, revision=REVISION, local_files_only=True)
        except OSError:
            from huggingface_hub import hf_hub_download

            for name in ("config.json", "model.safetensors"):
                hf_hub_download(MODEL, name, revision=REVISION)
            _net = AutoModelForDepthEstimation.from_pretrained(MODEL, revision=REVISION, local_files_only=True)
        _net.eval()
    return _net


def predict(image) -> "object":
    """The network's relative depth for an RGB picture (float array, larger = nearer)."""
    import numpy as np
    import torch
    from PIL import Image

    model = _model()
    w, h = image.size
    scale = INPUT_SHORT / min(w, h)
    size = (max(_PATCH, round(w * scale / _PATCH) * _PATCH), max(_PATCH, round(h * scale / _PATCH) * _PATCH))
    pixels = np.asarray(image.convert("RGB").resize(size, Image.BICUBIC), dtype=np.float32) / 255.0
    pixels = (pixels - np.array(_MEAN, dtype=np.float32)) / np.array(_STD, dtype=np.float32)
    batch = torch.from_numpy(np.ascontiguousarray(pixels.transpose(2, 0, 1))[None])
    with torch.no_grad():
        out = model(pixel_values=batch).predicted_depth
    raw = out.squeeze().cpu().numpy().astype(np.float32)
    if raw.ndim != 2 or not np.isfinite(raw).all():
        raise Refused("the network's output is not a finite 2-D map")
    return raw


def depth_png(picture_bytes: bytes) -> tuple[bytes, int, int, dict]:
    """(PNG bytes, w, h, facts) of one picture's map, or Refused."""
    import numpy as np
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    image = hires._on_stage(hires._image(picture_bytes))        # see-through pictures as the film shows them
    # The network cannot say "there is nothing here": for a plain blue card it returns a
    # confident, full-range scene (measured 17 Nov). So an empty picture is refused before
    # it is asked -- and a drawing or a model on black is kept out by class, not by a check.
    if float(np.asarray(image.convert("L").resize((256, 256)), dtype=np.float32).std()) < BLANK_STD:
        raise Refused("the picture is blank (nothing for a depth map to be of)")
    raw = predict(image)
    lo, hi = np.percentile(raw, CLIP)
    if not hi - lo > 1e-6:
        raise Refused("the prediction has no range")
    # (float32 on purpose: percentile() answers in float64, and PIL reads a float64 array's
    # bytes as float32 -- the first maps on 17 Nov were noise with a "healthy" std of 127)
    norm = np.clip((raw - np.float32(lo)) / np.float32(hi - lo), 0.0, 1.0).astype(np.float32)
    scale = min(1.0, LONG_SIDE / max(image.size))
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    # float resize, then 8 bit: resizing 8-bit steps would band a slow gradient (a floor, the sea)
    depth = Image.fromarray(norm).resize(size, Image.BICUBIC)
    values = np.asarray(depth, dtype=np.float32)
    if not np.isfinite(values).all():
        raise Refused("the resized map is not finite")
    grey = np.clip(values * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if abs(size[0] / size[1] - image.width / image.height) > 0.01:
        raise Refused("the map's shape %dx%d is not the picture's" % size)
    std = float(grey.std())
    if std < FLAT_STD:
        raise Refused("the map is flat (std %.1f/255 < %.0f): nothing to move" % (std, FLAT_STD))
    # A depth map is a surface: mostly slow slopes, a few edges. Noise is all edges.
    step = grey.astype(np.int16)
    rough = float(np.abs(np.diff(step, axis=1)).mean() + np.abs(np.diff(step, axis=0)).mean()) / 2
    black, white = float((grey <= 1).mean()), float((grey >= 254).mean())
    # (one end may be large -- a sunset sky is a fifth of fac_3_sky_pool and all of it "far";
    # both ends large at once is the two-level speckle the float64 bug produced)
    if rough > ROUGH_MAX or min(black, white) > ENDS_MAX:
        raise Refused("the map is not a surface (mean step %.1f/255, %.0f%% black and %.0f%% white)"
                      % (rough, black * 100, white * 100))
    out = io.BytesIO()
    Image.fromarray(grey).save(out, "PNG", optimize=True)
    return out.getvalue(), size[0], size[1], {"std": round(std, 1), "rough": round(rough, 2)}


def soft_png(depth_bytes: bytes) -> tuple[bytes, int, int]:
    """The same map blurred by SOFT_BLUR of its width and halved: what to displace with."""
    from PIL import Image, ImageFilter

    depth = Image.open(io.BytesIO(depth_bytes)).convert("L")
    soft = depth.filter(ImageFilter.GaussianBlur(SOFT_BLUR * depth.width))
    scale = min(1.0, SOFT_LONG_SIDE / max(depth.size))
    soft = soft.resize((max(1, round(depth.width * scale)), max(1, round(depth.height * scale))), Image.BICUBIC)
    lo, hi = soft.getextrema()
    if hi - lo < 24:
        raise Refused("the softened map is flat (%d..%d): nothing to move" % (lo, hi))
    out = io.BytesIO()
    soft.save(out, "PNG", optimize=True)
    return out.getvalue(), soft.width, soft.height


def moved(picture, depth, shift: float, soften: float = 0.0):
    """The picture as a depth-displacement shader would show it with the camera `shift`
    (a fraction of the width) to one side: each output pixel reads the source at
    x + (depth - 0.5) * shift * width. This is the cheap parallax a browser can afford --
    and where it breaks (a near edge smearing over what is behind it) is what to look for."""
    import numpy as np
    from PIL import Image, ImageFilter

    d = depth.resize(picture.size, Image.BICUBIC)
    if soften:
        d = d.filter(ImageFilter.GaussianBlur(soften * picture.width))
    field = (np.asarray(d, dtype=np.float32) / 255.0 - 0.5) * shift * picture.width
    src = np.asarray(picture.convert("RGB"))
    h, w = field.shape
    xs = np.clip(np.arange(w, dtype=np.float32)[None, :] + field, 0, w - 1)
    x0 = np.floor(xs).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    t = (xs - x0)[..., None]
    rows = np.arange(h)[:, None]
    out = src[rows, x0] * (1 - t) + src[rows, x1] * t
    return Image.fromarray(out.astype(np.uint8))


def write_proof(ids: list[str], entries: dict[str, dict], out: Path, report: Path, shift: float) -> list[Path]:
    """One sheet per picture to look at: the picture, its map, and the picture moved left and
    right by `shift` of its width with a softened map (what the engine should do)."""
    from PIL import Image, ImageDraw

    Image.MAX_IMAGE_PIXELS = None
    made = []
    for aid in ids:
        e = entries.get(aid) or {}
        if not e.get("ok") or not e.get("depth"):
            print("no map for %s" % aid)
            continue
        picture = hires._on_stage(Image.open(out / e["file"]))
        picture.thumbnail((1280, 1280))
        depth = Image.open(out / e["depth"]["file"]).convert("L")
        cells = [("picture", picture), ("depth (near = white)", depth.resize(picture.size, Image.BICUBIC).convert("RGB")),
                 ("camera %.1f%% left, map softened 0.6%%" % (shift * 100), moved(picture, depth, -shift, 0.006)),
                 ("camera %.1f%% right, map softened 0.6%%" % (shift * 100), moved(picture, depth, shift, 0.006)),
                 ("camera %.1f%% right, raw map" % (shift * 100), moved(picture, depth, shift, 0.0)),
                 ("difference left/right x4", None)]
        import numpy as np
        a = np.asarray(cells[2][1], dtype=np.int16)
        b = np.asarray(cells[3][1], dtype=np.int16)
        cells[5] = (cells[5][0], Image.fromarray(np.clip(np.abs(a - b) * 4, 0, 255).astype(np.uint8)))
        w, h = picture.size
        sheet = Image.new("RGB", (w * 2, (h + 26) * 3), (16, 17, 28))
        draw = ImageDraw.Draw(sheet)
        for k, (label, cell) in enumerate(cells):
            x, y = (k % 2) * w, (k // 2) * (h + 26)
            sheet.paste(cell, (x, y + 26))
            draw.text((x + 8, y + 6), "%s -- %s" % (aid, label), fill=(220, 225, 240))
        target = report / ("depth_proof_%s.png" % aid)
        report.mkdir(parents=True, exist_ok=True)
        sheet.save(target)
        made.append(target)
        print("proof -> %s" % target)
    return made


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--out", type=Path, default=hires.OUT)
    ap.add_argument("--report", type=Path, default=hires.REPORT)
    ap.add_argument("--check", action="store_true", help="verify the maps on disk against the index; write nothing")
    ap.add_argument("--proof", action="store_true", help="write depth_proof_<id>.png sheets for the ids, nothing else")
    ap.add_argument("--shift", type=float, default=0.03, help="--proof: camera move as a fraction of the width")
    ap.add_argument("--force", action="store_true", help="compute again even where a map still fits")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    out = args.out.resolve()
    listing = hires.read_index(out)
    if not listing:
        print("no index at %s -- run scripts/build_ceremony_hires.py first" % (out / hires.INDEX_NAME))
        return 2
    entries = {e["id"]: e for e in listing}

    if args.check:
        bad = 0
        for e in listing:
            if not e.get("depth"):
                continue
            fits = hires.depth_still_fits(e, e["depth"], out)
            print("%-8s %-28s %s" % ("ok" if fits else "PROBLEM", e["id"], e["depth"].get("file")))
            bad += not fits
        print("%d maps, %d problems" % (sum(1 for e in listing if e.get("depth")), bad))
        return 1 if bad else 0

    if args.proof:
        write_proof(args.ids or default_ids(listing)[:4], entries, out, args.report, args.shift)
        return 0

    wanted = args.ids or default_ids(listing)
    unknown = [i for i in wanted if i not in entries]
    if unknown:
        print("not in the index: %s" % ", ".join(unknown))
        return 2
    refused = 0
    for aid in wanted:
        e = entries[aid]
        name, soft_name = "%s.depth.png" % aid, "%s.depth_soft.png" % aid
        try:
            if not e.get("ok") or e.get("kind") != "picture":
                raise Refused("not a usable picture in the index")
            why = hires.file_problem(e, out)
            if why:
                raise Refused("the picture on disk: %s" % why)
            if not args.force and hires.depth_still_fits(e, e.get("depth"), out):
                print("kept     %-28s %s" % (aid, e["depth"]["file"]))
                continue
            data, w, h, facts = depth_png((out / e["file"]).read_bytes())
            soft, sw, sh = soft_png(data)
        except Refused as exc:
            refused += 1
            e["depth"] = None
            for stale in (name, soft_name):               # no map that failed its check stays on disk
                (out / stale).unlink(missing_ok=True)
            print("REFUSED  %-28s %s" % (aid, exc))
            continue
        for stale, payload in ((name, data), (soft_name, soft)):
            target = out / stale
            if not target.is_file() or target.read_bytes() != payload:
                target.write_bytes(payload)
        e["depth"] = {"file": name, "sha256": hires.sha256_of(data), "w": w, "h": h, "of": e["sha256"],
                      "model": "%s@%s" % (MODEL, REVISION[:8]), "input_short": INPUT_SHORT, "near": "white", **facts,
                      "soft": {"file": soft_name, "sha256": hires.sha256_of(soft), "w": sw, "h": sh,
                               "blur": SOFT_BLUR}}
        print("ok       %-28s %4dx%-4d std %.1f" % (aid, w, h, facts["std"]))
    hires.write_index(list(entries.values()), out)
    hires.write_hires_md(sorted(entries.values(), key=lambda x: (bool(x.get("candidate")), str(x.get("id")))), args.report)
    print("\n%d maps in the index, %d refused" % (sum(1 for e in entries.values() if e.get("depth")), refused))
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
