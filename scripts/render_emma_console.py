"""Render Emma standing on a stage -- a transparent clip for the ceremony screen (s06_l05, s06_l07).

Owner, 18 Nov 2026, about the glowing star-orb that stood in for Emma in these two cues:
"ไม่ต้องเอาขึ้นมาเลย ก็ให้ตัวหุ่นที่โดดน้ำมายืนที่แท่นเลย" -- the robot that dived into the pool comes
and stands on the podium. The act `emma_console` lays this clip over its picture wall and its
podium and moves the clip's BOX (crouch, hop, squash) on her voice; the clip is only her own
performance, in place, on one timeline:

    preen  0.0-3.0 s   "Besides my good looks": a shoulder to the hall, a fin to her cheek, a toss
    idle   3.0-7.0 s   the standing loop
    cheer  7.0-9.0 s   "I'd take first place!": arms up, two bounces

Every seam (0 s, 3 s, 7 s, and each loop's own end) is the SAME pose, so the act may loop a
part by seeking and cut from one part to another on any of those frames.

The way scripts/render_emma_dive.py does it (that file and its page are another clip's: this
is a copy, not an edit): the owner-approved `client/assets/emma-3d-lab/emma.glb`, lit as
mascot.js lights it, drawn by scripts/emma_console_render/page.html in headless Chromium on
this PC's GPU, one frame at a time (the frame's number is passed in -- nothing depends on how
fast the PC is). Here the page draws at twice the size and this script scales down (smooth
edges on the chrome), fills the colour under the transparent pixels from her own edge (a lossy
codec smears what is under alpha 0 into the edge: black there is a dark halo), and ffmpeg
encodes VP9 with alpha.

    python scripts/render_emma_console.py --sheet preen@0.9,preen@1.7,cheer@0.4   # look at poses
    python scripts/render_emma_console.py --render      # the frames (GPU)
    python scripts/render_emma_console.py --encode      # frames -> webm, checks, contact sheets
    python scripts/render_emma_console.py --register "what a person looked at"

Output: .tmp/ceremony-show/emma-console/ (frames/, emma_stage.webm, points.json, sheets).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / ".tmp" / "ceremony-show" / "emma-console"
PAGE = ROOT / "scripts" / "emma_console_render" / "page.html"
LAB = ROOT / "client" / "assets" / "emma-3d-lab"
ORIGIN = "http://emma-console.render"
# 60, the film's own rate (18 Nov 2026, the pacing pass): at 30 fps she moved in two-frame
# steps against a camera that glides every frame -- the owner's "ไม่สมูท". Every time of the page is
# in seconds (its blinks too), so the rate changes only how often a pose is sampled.
FPS = 60
GOP = FPS // 2                 # a key frame every 0.5 s whatever the rate
PARTS = {"preen": (0.0, 3.0), "idle": (3.0, 7.0), "cheer": (7.0, 9.0)}
END = 9.0
FRAMES = int(round(END * FPS))
CLIP = "emma_stage.webm"

# The shot. Frontal, the lens of the dive's close shot, the camera at the top of her chest
# looking a little up at her (she is on a podium); her body a little to screen-left (-x), where
# the key light is. `dist` and `lookY` are solved at each run (fit) so that in the neutral pose
# the lowest thing drawn of her is at BASE of the frame's height and the top of her head at TOP.
SPEC = {"w": 720, "h": 960, "ss": 2, "fps": FPS, "fov": 22, "exposure": 1.05, "yaw0": -0.16,
        "camY": 2.0, "lookY": 2.8608, "dist": 12.8341}
BASE, TOP = 0.92, 0.20
SEEN = 16                      # alpha (0..255) from which a pixel counts as her, for every box here
CARD = (58, 110, 160)          # the mid-blue card the sheets are laid on


def serve(route) -> None:
    url = route.request.url[len(ORIGIN):].split("?")[0]
    if url in ("/", "/page.html"):
        path = PAGE
    elif url == "/emma.glb":
        path = LAB / "emma.glb"
    elif url.startswith("/vendor/"):
        path = (LAB / url.lstrip("/")).resolve()
        if not path.is_relative_to((LAB / "vendor").resolve()):
            return route.abort()
    else:
        return route.abort()
    if not path.is_file():
        return route.abort()
    kind = "text/javascript" if path.suffix == ".js" else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    route.fulfill(status=200, body=path.read_bytes(), headers={"Content-Type": kind})


def open_page(p, spec: dict):
    browser = p.chromium.launch(headless=True, args=["--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11",
                                                     "--enable-unsafe-swiftshader"])
    page = browser.new_page(viewport={"width": spec["w"], "height": spec["h"]})
    page.on("console", lambda m: print("page:", m.text) if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: print("page error:", e))
    page.route(ORIGIN + "/**", serve)
    page.goto(ORIGIN + "/page.html")
    page.wait_for_function("window.ready === true", timeout=30000)
    info = page.evaluate("spec => window.setup(spec)", spec)
    renderer = page.evaluate("""() => { const gl = document.getElementById('c').getContext('webgl2');
        const d = gl && gl.getExtension('WEBGL_debug_renderer_info');
        return d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'unknown'; }""")
    print("model:", json.dumps(info), "| GPU:", renderer)
    return browser, page


# ---- a frame: from the page's double-size PNG to the clip's size --------------------------------

def small(data_url: str):
    """The page's frame (straight alpha, twice the size) at the clip's size: averaged with the
    colour weighted by its alpha, so an edge pixel keeps her colour and not the nothing beside it."""
    import cv2
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGBA")
    big = np.asarray(im).astype(np.float32)
    a = big[..., 3:4] / 255.0
    pre = np.concatenate([big[..., :3] * a, big[..., 3:4]], axis=2)
    pre = cv2.resize(pre, (SPEC["w"], SPEC["h"]), interpolation=cv2.INTER_AREA)
    alpha = pre[..., 3:4]
    rgb = np.where(alpha > 0.25, pre[..., :3] / np.maximum(alpha / 255.0, 1e-6), 0.0)
    return np.concatenate([np.clip(rgb, 0, 255), alpha], axis=2).round().astype(np.uint8)


def bleed(rgba):
    """Her edge's colour carried out under the transparent pixels round her (a few pixels, then
    flat grey). Nothing that is seen changes: only pixels of alpha < 8 are written."""
    import numpy as np
    from scipy import ndimage

    alpha = rgba[..., 3]
    solid = alpha >= 200
    if not solid.any():
        return rgba
    dist, (iy, ix) = ndimage.distance_transform_edt(~solid, return_indices=True)
    near = rgba[iy, ix, :3].astype(np.float32)
    w = np.clip((20.0 - dist) / 12.0, 0.0, 1.0)[..., None]
    fill = near * w + 128.0 * (1.0 - w)
    out = rgba.copy()
    hide = alpha < 8
    out[hide, :3] = fill[hide].round().astype(np.uint8)
    return out


def box_of(rgba) -> list[float] | None:
    """[x0, y0, x1, y1] of what is seen of her, as fractions of the frame (x1, y1 = the far edge)."""
    import numpy as np

    seen = rgba[..., 3] >= SEEN
    if not seen.any():
        return None
    ys, xs = np.where(seen.any(axis=1))[0], np.where(seen.any(axis=0))[0]
    h, w = seen.shape
    return [round(int(xs[0]) / w, 4), round(int(ys[0]) / h, 4), round((int(xs[-1]) + 1) / w, 4), round((int(ys[-1]) + 1) / h, 4)]


def on_card(rgba, card=CARD):
    import numpy as np

    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return (rgba[..., :3].astype(np.float32) * a + np.array(card, np.float32) * (1 - a)).round().astype(np.uint8)


def where(spec: str) -> tuple[float, str | None, str]:
    """'1.5' = clip seconds; 'cheer@0.4' = 0.4 s into that part (its own function, also at its end)."""
    if "@" in spec:
        part, local = spec.split("@", 1)
        return PARTS[part][0] + float(local), part, spec
    return float(spec), None, "t=%s" % spec


def fit(page) -> dict:
    """Solve the camera's distance and aim so the neutral pose stands where the act expects it."""
    cam = {"camY": SPEC["camY"], "lookY": SPEC["lookY"], "dist": SPEC["dist"]}
    h = SPEC["h"]
    for step in range(12):
        page.evaluate("c => window.setCamera(c)", cam)
        box = box_of(small(page.evaluate("() => window.frame(3.0)")))
        top, base = box[1] * h, box[3] * h
        tall, mid = base - top, (base + top) / 2
        want_tall, want_mid = (BASE - TOP) * h, (BASE + TOP) / 2 * h
        if abs(tall - want_tall) < 0.6 and abs(mid - want_mid) < 0.6:
            break
        per_unit = tall / 3.57                       # px per unit of her (she is ~3.57 tall)
        cam["dist"] *= tall / want_tall
        cam["lookY"] += (want_mid - mid) / per_unit
    cam = {k: round(float(v), 4) for k, v in cam.items()}
    page.evaluate("c => window.setCamera(c)", cam)
    print("camera:", cam, "neutral box:", box_of(small(page.evaluate("() => window.frame(3.0)"))), "after", step + 1, "steps")
    return cam


def sheet(images: list, labels: list[str], dest: Path, cols: int, tile: tuple[int, int] = (360, 480), card=CARD) -> None:
    from PIL import Image, ImageDraw

    rows = (len(images) + cols - 1) // cols
    out = Image.new("RGB", (tile[0] * cols, tile[1] * rows), card)
    for i, (rgba, label) in enumerate(zip(images, labels)):
        im = Image.fromarray(on_card(rgba, card)).resize(tile, Image.LANCZOS)
        d = ImageDraw.Draw(im)
        d.text((8, 6), label, fill=(255, 255, 255))
        d.rectangle([0, 0, tile[0] - 1, tile[1] - 1], outline=(30, 60, 90))
        out.paste(im, ((i % cols) * tile[0], (i // cols) * tile[1]))
    out.save(dest, quality=90)
    print("sheet:", dest)


# ---- the three steps ------------------------------------------------------------------------

def render(page, cam: dict) -> None:
    import numpy as np
    from PIL import Image

    frames = OUT / "frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("f*.png"):
        old.unlink()
    boxes = []
    for i in range(FRAMES):
        rgba = small(page.evaluate("t => window.frame(t)", i / FPS))
        boxes.append(box_of(rgba))
        Image.fromarray(bleed(rgba)).save(frames / ("f%04d.png" % i))
    # every seam is one pose: the file's frames at 0 s, 3 s, 7 s and each part's own function at its end
    seams = {"0.0 (preen starts)": small(page.evaluate("() => window.frame(0)")),
             "3.0 (idle starts)": small(page.evaluate("() => window.frame(3.0)")),
             "7.0 (cheer starts)": small(page.evaluate("() => window.frame(7.0)")),
             "preen's end (its function at 3.0)": small(page.evaluate("() => window.frame(3.0, 'preen')")),
             "idle's end (its function at 7.0)": small(page.evaluate("() => window.frame(7.0, 'idle')")),
             "cheer's end (its function at 9.0)": small(page.evaluate("() => window.frame(9.0, 'cheer')"))}
    ref = seams["3.0 (idle starts)"].astype(np.int16)
    seam_diff = {name: int(np.abs(im.astype(np.int16) - ref).max()) for name, im in seams.items()}
    print("seams, largest difference from the neutral frame (0..255, any channel):", json.dumps(seam_diff))
    points = page.evaluate("() => window.points()")

    def span(a: float, b: float) -> list[list[float]]:
        return [bx for bx in boxes[int(round(a * FPS)):int(round(b * FPS))] if bx]

    neutral = boxes[int(round(3.0 * FPS))]
    doc = {"spec": {**SPEC, **cam}, "fps": FPS, "frames": FRAMES, "seen_from_alpha": SEEN, **points,
           "neutral_box": neutral, "baseline": neutral[3],
           "parts": {name: {"from": a, "to": b, "frames": [int(round(a * FPS)), int(round(b * FPS)) - 1],
                            "box": [min(x[0] for x in span(a, b)), min(x[1] for x in span(a, b)),
                                    max(x[2] for x in span(a, b)), max(x[3] for x in span(a, b))]}
                     for name, (a, b) in PARTS.items()},
           "cheer_highest": min(x[1] for x in span(*PARTS["cheer"])),
           "lowest_anywhere": max(x[3] for x in boxes if x), "seam_difference": seam_diff}
    (OUT / "points.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print("points:", json.dumps({k: doc[k] for k in ("neutral_box", "baseline", "cheer_highest", "lowest_anywhere", "parts")}))


def encode(ffmpeg: str, crf: int) -> Path:
    """VP9 with alpha (yuva420p). A key frame every 0.5 s, and forced at 0 s, 3 s, 7 s: the
    act loops a part by seeking to its first frame."""
    dest = OUT / CLIP
    args = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(OUT / "frames" / "f%04d.png"),
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(crf), "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "1", "-g", str(GOP), "-keyint_min", str(GOP),
            "-force_key_frames", "0,3,7", "-auto-alt-ref", "0", "-an", str(dest)]
    subprocess.run(args, check=True)
    print("clip:", dest, dest.stat().st_size, "bytes")
    return dest


def check(ffmpeg: str) -> None:
    """Read the clip back as a player would (libvpx, with its alpha) and look at the numbers:
    frame count, key frames, the seams, each loop's step against its own neighbours, the edge."""
    import numpy as np
    from PIL import Image

    clip = OUT / CLIP
    at = lambda s: int(round(s * FPS))           # a clip second -> its frame
    dec = OUT / "decoded"
    dec.mkdir(exist_ok=True)
    for old in dec.glob("f*.png"):
        old.unlink()
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-c:v", "libvpx-vp9", "-i", str(clip),
                    "-pix_fmt", "rgba", "-start_number", "0", str(dec / "f%04d.png")], check=True)
    files = sorted(dec.glob("f*.png"))
    frames = [np.asarray(Image.open(f).convert("RGBA")) for f in files]
    source = [np.asarray(Image.open(OUT / "frames" / f.name).convert("RGBA")) for f in files]
    report: dict = {"frames": len(frames), "size": list(frames[0].shape[1::-1]), "bytes": clip.stat().st_size,
                    "sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
                    "fps": FPS, "alpha": [int(frames[at(3)][..., 3].min()), int(frames[at(3)][..., 3].max())]}
    probe = str(Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix))
    rows = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
                           "-of", "csv=p=0", str(clip)], check=True, capture_output=True, text=True).stdout.split()
    keys = [int(round(float(r.split(",")[0]) * FPS)) for r in rows if "K" in r.split(",")[1]]
    report["packets"] = len(rows)
    report["key_frames"] = keys
    report["key_frames_ok"] = (all(k in keys for k in (0, at(3), at(7)))
                               and max(b - a for a, b in zip(keys, keys[1:] + [len(rows)])) <= GOP)

    def step(a, b) -> float:                          # mean difference as it is seen on the card (0..255)
        return float(np.abs(on_card(a).astype(np.int16) - on_card(b).astype(np.int16)).mean())

    report["decoded_seams"] = {"0 s vs 3 s": round(step(frames[0], frames[at(3)]), 3), "3 s vs 7 s": round(step(frames[at(3)], frames[at(7)]), 3)}
    loops = {}
    for name, (a, b) in PARTS.items():
        first, last = int(round(a * FPS)), int(round(b * FPS)) - 1
        inside = [step(source[i], source[i + 1]) for i in range(first, last)]
        loops[name] = {"last_to_first": round(step(source[last], source[first]), 3),
                       "neighbours_median": round(float(np.median(inside)), 3), "neighbours_max": round(max(inside), 3)}
    report["loop_steps_in_the_rendered_frames"] = loops
    # the edge: where she is half there, is the decoded colour darker than the rendered one?
    edge = []
    for i in (at(s) for s in (0, 1.5, 3.0, 5.0, 7.4, 8.0)):
        half = (source[i][..., 3] > 40) & (source[i][..., 3] < 215)
        if half.any():
            edge.append(float((frames[i][..., :3][half].astype(np.float32).mean() - source[i][..., :3][half].astype(np.float32).mean())))
    report["edge_brightness_decoded_minus_rendered"] = [round(v, 2) for v in edge]
    report["decoded_against_rendered_on_the_card"] = round(float(np.mean([step(frames[i], source[i]) for i in range(0, len(frames), 9)])), 3)
    (OUT / "check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("check:", json.dumps(report))

    # contact sheets from the DECODED clip: a frame every 0.5 s, and the last frame
    picks = [int(round(s * FPS)) for s in [x * 0.5 for x in range(int(END * 2))]] + [len(frames) - 1]
    labels = ["%.2f s  f%d  %s" % (i / FPS, i, next(n for n, (a, b) in PARTS.items() if a <= i / FPS < b)) for i in picks]
    sheet([frames[i] for i in picks[:10]], labels[:10], OUT / "contact_1.jpg", cols=5)
    sheet([frames[i] for i in picks[10:]], labels[10:], OUT / "contact_2.jpg", cols=5)
    sheet([frames[i] for i in picks], labels, OUT / "contact_all.jpg", cols=7, tile=(240, 320))
    # the edge, enlarged, on three cards (a halo shows on one of them if there is one)
    crops = []
    for card in ((58, 110, 160), (245, 245, 245), (12, 12, 14)):
        for i, (x, y) in ((at(3.0), (150, 150)), (at(3.0), (430, 560)), (at(7.4), (500, 380))):
            im = Image.fromarray(on_card(frames[i], card)).crop((x, y, x + 120, y + 160)).resize((360, 480), Image.NEAREST)
            crops.append(np.dstack([np.asarray(im), np.full((480, 360), 255, np.uint8)]))
    sheet(crops, ["edge x3"] * len(crops), OUT / "edges.jpg", cols=3, card=(0, 0, 0))


def browser_check() -> None:
    """Chromium itself (what the hall's screen is): does it play the clip with its alpha, and
    does a seek to a part's first frame show that frame, at once?"""
    import numpy as np
    from PIL import Image
    from playwright.sync_api import sync_playwright

    clip = (OUT / CLIP).read_bytes()
    html = ("<!doctype html><html><body style='margin:0;background:transparent'>"
            "<video id='v' muted playsinline preload='auto'></video><canvas id='c'></canvas></body></html>")

    def answer(route) -> None:
        url = route.request.url[len(ORIGIN):].split("?")[0]
        if url == "/check.html":
            return route.fulfill(status=200, body=html, headers={"Content-Type": "text/html"})
        if url == "/clip.webm":
            return route.fulfill(status=200, body=clip, headers={"Content-Type": "video/webm"})
        return route.abort()

    script = """async ([times, tol]) => {
      const v = document.getElementById('v'), c = document.getElementById('c');
      v.src = '/clip.webm';
      await new Promise((ok, no) => { v.addEventListener('loadeddata', ok, { once: true }); v.addEventListener('error', () => no(new Error('the clip did not load')), { once: true }); });
      c.width = v.videoWidth; c.height = v.videoHeight;
      const g = c.getContext('2d'), out = { w: v.videoWidth, h: v.videoHeight, duration: v.duration, shots: [] };
      for (const t of times) {
        // (the 'seeked' event is not the picture: wait for the frame of that time to be PRESENTED)
        const from = performance.now();
        const shown = new Promise(ok => {
          const tick = (now, meta) => { if (Math.abs(meta.mediaTime - t) < tol) ok(performance.now() - from); else v.requestVideoFrameCallback(tick); };
          v.requestVideoFrameCallback(tick);
        });
        v.currentTime = t;
        const ms = await Promise.race([shown, new Promise(ok => setTimeout(() => ok(null), 3000))]);
        g.clearRect(0, 0, c.width, c.height); g.drawImage(v, 0, 0);
        out.shots.push({ t, ms, png: c.toDataURL('image/png') });
      }
      return out;
    }"""
    # key frames first (1.0, 3.0, 7.5, 7.0, 4.5, 0.0, 8.0 are all one), then frames between them
    times = [1.0, 3.0, 7.5, 7.0, 4.5, 0.0, 8.0, 3.0, 7.4, 1.7, round((FRAMES - 1) / FPS, 4)]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--ignore-gpu-blocklist", "--enable-gpu", "--use-angle=d3d11",
                                                         "--enable-unsafe-swiftshader"])
        try:
            page = browser.new_page(viewport={"width": SPEC["w"], "height": SPEC["h"]})
            page.route(ORIGIN + "/**", answer)
            page.goto(ORIGIN + "/check.html")
            got = page.evaluate(script, [times, 0.5 / FPS])
        finally:
            browser.close()
    decoded = {i: np.asarray(Image.open(f).convert("RGBA")) for i, f in enumerate(sorted((OUT / "decoded").glob("f*.png")))}
    rows = []
    for shot in got["shots"]:
        im = np.asarray(Image.open(io.BytesIO(base64.b64decode(shot["png"].split(",", 1)[1]))).convert("RGBA"))
        want = int(round(shot["t"] * FPS))
        near = {i: float(np.abs(on_card(im).astype(np.int16) - on_card(decoded[i]).astype(np.int16)).mean())
                for i in range(max(0, want - 2), min(len(decoded), want + 3))}
        best = min(near, key=near.get)
        rows.append({"seek": shot["t"], "ms": None if shot["ms"] is None else round(shot["ms"], 1), "frame_wanted": want, "frame_shown": best,
                     "difference": round(near[best], 3), "alpha": [int(im[..., 3].min()), int(im[..., 3].max())]})
    # The act seeks only to a part's first frame (0, 3, 7 s: forced key frames) -- those must show
    # that very frame, with its alpha. A seek between key frames lands a frame or two off in
    # headless Chromium (reported, not required: the act never does it). `ms` stays None in a
    # headless page that presents no frames for a paused video (reported only).
    keyed = [r for r in rows if r["frame_wanted"] in (0, int(round(PARTS["idle"][0] * FPS)), int(round(PARTS["cheer"][0] * FPS)))]
    report = {"w": got["w"], "h": got["h"], "duration": got["duration"], "seeks": rows,
              "ok": bool(keyed) and all(r["frame_shown"] == r["frame_wanted"] and r["difference"] < 1.0 and r["alpha"][0] == 0
                                        for r in keyed)}
    (OUT / "browser_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("chromium:", json.dumps(report))


ASSET_ID = "c_emma_stage"
ASSETS = ROOT / "data" / "ceremony" / "assets"
# (only what the SET-UP line says: s06_l05 is the set-up of the superfan joke, and nothing it shows
# may be "about" the punchline -- tests/test_ceremony_slides.py. s06_l07, the punchline, goes on in
# the same shot: its slide says `"continues": "s06_l05"` and is backed by this line.)
ABOUT = ["good looks"]


def register(checked: str) -> None:
    """Copy the clip into the ceremony's assets and add it to index.json: this one entry, every
    other row written back as it was, in the file's own formatting. `checked` = what a person
    looked at (the entry is usable only with it); the clip is the robot character alone, no
    real person (`people: []`)."""
    clip = OUT / CLIP
    data = clip.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    dest = ASSETS / (ASSET_ID + ".webm")
    dest.write_bytes(data)
    entry = {
        "id": ASSET_ID, "kind": "video", "file": dest.name, "sha256": sha, "w": SPEC["w"], "h": SPEC["h"],
        "from_pin": None, "candidate": True, "checked": checked, "ok": True, "depth": None,
        "about": ABOUT, "class": "model",
        "shows": "Emma, the project's own robot (the owner-approved 3D model), standing in place on a transparent "
                 "background, camera and feet fixed: 0-3 s she turns a shoulder, lifts a fin to her cheek and tosses "
                 "her head (proud of her looks); 3-7 s her standing idle loop; 7-9 s a winner's cheer loop, fins up, "
                 "two bounces. Every part starts and ends in the same pose",
        "text": "", "source": "scripts/render_emma_console.py (client/assets/emma-3d-lab/emma.glb)",
        "alpha": True, "bytes": len(data), "duration": END, "fps": float(FPS), "frames": FRAMES,
        "picked_by": ["emma_console"], "people": [],
    }
    index = ASSETS / "index.json"
    raw = index.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    rows = json.loads(raw.decode("utf-8"))
    before = json.dumps(rows, indent=1, ensure_ascii=False) + "\n"
    if before.replace("\n", eol).encode("utf-8") != raw:
        raise SystemExit("index.json is not in the formatting this writes (indent 1, ensure_ascii off): not touched")
    rows = [r for r in rows if r.get("id") != ASSET_ID] + [entry]
    text = json.dumps(rows, indent=1, ensure_ascii=False) + "\n"
    index.write_bytes(text.replace("\n", eol).encode("utf-8"))
    print("registered", ASSET_ID, sha, len(data), "bytes;", len(rows), "entries")
    # (23 Nov: the film shows CUT copies of this clip -- the notebook decodes VP9 alpha on its CPU)
    print("the film shows the cut copies: cut them again from these frames, look at them, then mark --\n"
          "  python scripts/cut_emma_clips.py --make %s_crop,%s_crop30\n"
          "  python scripts/cut_emma_clips.py --mark %s_crop,%s_crop30 --checked \"<what you looked at>\""
          % ((ASSET_ID,) * 4))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sheet", help="comma-separated poses (clip seconds, or part@seconds): a sheet of those only")
    ap.add_argument("--plan", help="a JSON file {sheets: [{name, cols?, crop?: [x, y, w, h], items: [pose | "
                    "{label, pose: {offsets from the neutral pose}}]}]}: several sheets in one run of the page")
    ap.add_argument("--name", default="sheet", help="the sheet's file name (without .jpg)")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--render", action="store_true", help="draw every frame (GPU)")
    ap.add_argument("--encode", action="store_true", help="frames -> webm, then the checks and the contact sheets")
    ap.add_argument("--register", metavar="CHECKED", help="pin the clip in data/ceremony/assets/index.json with this "
                    "note of what was looked at")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=NUMBER", help="override a number of the shot")
    ap.add_argument("--crf", type=int, default=30, help="the encoder's quality (lower = better and larger)")
    ap.add_argument("--browser-check", action="store_true", help="play the clip in Chromium: its alpha, its seeks (GPU)")
    ap.add_argument("--ffmpeg")
    args = ap.parse_args()
    for pair in args.set:
        name, value = pair.split("=", 1)
        SPEC[name] = float(value)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.register:
        register(args.register)
        return 0
    if args.render or args.sheet or args.plan:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, page = open_page(p, SPEC)
            try:
                cam = fit(page)
                jobs = []
                if args.sheet:
                    jobs.append({"name": args.name, "cols": args.cols, "items": [x.strip() for x in args.sheet.split(",")]})
                if args.plan:
                    jobs += json.loads(Path(args.plan).read_text(encoding="utf-8"))["sheets"]
                for job in jobs:
                    images, labels = [], []
                    for item in job["items"]:
                        if isinstance(item, str):
                            t, part, label = where(item)
                            images.append(small(page.evaluate("([t, part]) => window.frame(t, part)", [t, part])))
                        else:
                            label = str(item.get("label", ""))
                            images.append(small(page.evaluate("pose => window.shot(pose)", item.get("pose") or {})))
                        labels.append(label)
                    wide = int(job.get("tile_w", 360))
                    tile = (wide, wide * 4 // 3)
                    if job.get("crop"):
                        x, y, w, h = (int(v) for v in job["crop"])
                        images = [im[y:y + h, x:x + w] for im in images]
                        tile = (wide, int(round(wide * h / w)))
                    sheet(images, labels, OUT / (job["name"] + ".jpg"), cols=int(job.get("cols", 5)), tile=tile)
                if args.render:
                    render(page, cam)
            finally:
                browser.close()
    if args.encode:
        from voice_line import find_ffmpeg

        ffmpeg = find_ffmpeg(args.ffmpeg)
        encode(ffmpeg, args.crf)
        check(ffmpeg)
    if args.browser_check:
        browser_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
