"""Render Emma diving into the pool -- a transparent clip for the ceremony screen (cue s04_l11).

Owner, 18 Nov 2026, on the punchline "Swimming in the 155-metre pool myself!": the plan with
words on it "ไม่สวยเลย เปลี่ยนเป็นทำเป็นใกล้เห็นตัวหุ่นแล้วโดดลงไปเลยได้ไหม" -- a close shot of the
robot herself jumping in. The act `emma_dive` lays this clip over the real picture of the pool
and draws the splash and the ripples itself.

The model is the owner-approved `client/assets/emma-3d-lab/emma.glb` (lit as mascot.js lights
it). The GLB has only 'idle' and 'look', so the jump is procedural (scripts/emma_dive/page.html).
The page runs in headless Chromium on this PC's GPU, one frame at a time at a fixed rate (the
frame's time is passed in -- nothing depends on how fast the PC is), and ffmpeg encodes the
PNGs into a VP9 webm with alpha.

    python scripts/render_emma_dive.py --sheet 0.3,1.0,1.7,2.1,2.4,2.6,4.0,5.0   # look at poses
    python scripts/render_emma_dive.py                                          # the whole clip
    python scripts/render_emma_dive.py --clip swim [--sheet 0.5,2.3,2.9,3.2,5.2,6.0,6.8,7.2]   # her laps (s04_l13 / l15)

Output: .tmp/ceremony-show/emma-dive/ (frames/, emma_dive.webm, points.json). Registering
the clip for the screen is a separate, deliberate step (data/ceremony/assets/index.json).
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
OUT = ROOT / ".tmp" / "ceremony-show" / "emma-dive"
PAGE = ROOT / "scripts" / "emma_dive" / "page.html"
LAB = ROOT / "client" / "assets" / "emma-3d-lab"
ORIGIN = "http://emma-dive.render"
# 60, the film's own rate (18 Nov 2026, the pacing pass): at 30 fps she moved in two-frame
# steps against a camera that glides every frame -- the owner's "ไม่สมูท". A pose is a function
# of the clip's seconds (page.html), so the rate changes only how often it is sampled.
FPS = 60
GOP = FPS // 2                 # a key frame every 0.5 s whatever the rate (the act's seeks land on 0.1 s marks)

# The shot (units = her standing height H). She hovers at the origin -- on the deck of the
# shell pavilion in c_pavilion_day, near the camera, at the right of the frame -- and the
# water she dives into is to the screen's LEFT (-x) and away (-z). The camera was solved so
# that on the 16:9 frame her base sits at (0.80, 0.77), her head at (0.81, 0.26) and her hands
# meet the water at (0.39, 0.62) -- the act lays the picture under those points (points.json).
# Framings dropped on 18 Nov, both for a real person on the stage: c_lagoon_path (a head in
# its ring pavilion that is in no keep-out box of the index) and prj_life_lagoon (its boxed
# hot-tub swimmer sat mid-frame: the core's paint-over read as a grey disc in the action).
SPEC = {
    "w": 1920, "h": 1080, "fov": 22, "exposure": 1.05, "poolBounce": True,
    "cam": [-0.3, 1.1, 5.0], "look": [-1.2, 0.4, -1.0],
    "yaw0": -0.5, "yawReady": -1.1, "yawDive": -2.3, "yawFace": 0.15,
    "land": [-2.0, -2.4], "waterDrop": 0.12, "peak": 0.38, "handLead": 0.45,
    "chestDeep": 0.47, "riseShift": [0.1, 0.3],
}

# The SWIM clip (cues s04_l13 / s04_l15, owner 18 Nov: "ให้หุ่นว่ายไปมายังดีกว่าอีก"): the same
# camera, water line and place as the dive's end -- its first two seconds ARE the dive clip's
# bob, pose for pose, so the line before joins it without a cut. She stays where she came up;
# the act (emma_dive, modes laps / edge) moves, flips and scales the clip over the real water.
SPEC_SWIM = dict(SPEC, mode="swim", yawSwim=-1.0, swimPitch=0.5, swimRoll=0.22, swimDeep=0.42, swimNod=-0.4,
                 armMid=1.3, armAmp=1.3, armLift=0.5, hop=0.14, waveSwing=0.3, waveLift=1.1)

CLIPS = {
    "dive": {"spec": SPEC, "file": "emma_dive.webm", "frames": "frames", "points": "points.json", "sheet": "sheet.jpg",
             "keys": [0, 0.6, 1.6, 4.4],
             "id": "c_emma_dive", "about": ["swimming pool", "pool", "swimming", "water"],
             "shows": "Emma, the project's own robot (the owner-approved 3D model), on a transparent background: "
                      "she turns to the water, bounces ready, leaps and dives head-first, then pops up and bobs "
                      "head above the water (the water itself is not in the clip)"},
    "swim": {"spec": SPEC_SWIM, "file": "emma_swim.webm", "frames": "frames_swim", "points": "points_swim.json",
             "sheet": "sheet_swim.jpg", "keys": [0, 2.0, 2.6, 5.0, 6.5],
             "id": "c_emma_swim", "about": ["swimming pool", "pool", "swimming", "water", "laps"],
             "shows": "Emma, the project's own robot (the owner-approved 3D model), on a transparent background, in "
                      "the water where the dive clip leaves her: she bobs, turns side-on and paddles (a swimming "
                      "stroke, looped for her laps), ducks under, pops up facing us and waves both arms, then bobs "
                      "(the water itself is not in the clip)"},
}


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
    print("model:", info, "| GPU:", renderer)
    return browser, page


def grab(page, t: float, dest: Path) -> None:
    data = page.evaluate("t => window.frame(t)", t)
    dest.write_bytes(base64.b64decode(data.split(",", 1)[1]))


def sheet(page, times: list[float], dest: Path, zoom: tuple[float, float, float, float] | None = None) -> None:
    """`zoom` = (x0, y0, x1, y1), fractions of the frame: only that part of each pose (her, close)."""
    from PIL import Image, ImageDraw

    tiles = []
    for t in times:
        f = OUT / "sheet_tmp.png"
        grab(page, t, f)
        im = Image.open(f).convert("RGBA")
        bg = Image.new("RGBA", im.size, (40, 90, 120, 255))
        bg.alpha_composite(im)
        if zoom:
            w, h = bg.size
            bg = bg.crop((int(zoom[0] * w), int(zoom[1] * h), int(zoom[2] * w), int(zoom[3] * h)))
        d = ImageDraw.Draw(bg)
        d.text((8, 8), "t=%.2f" % t, fill=(255, 255, 255, 255))
        tiles.append(bg.convert("RGB").resize((640, 360)))
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    out = Image.new("RGB", (640 * cols, 360 * rows), (0, 0, 0))
    for i, im in enumerate(tiles):
        out.paste(im, ((i % cols) * 640, (i // cols) * 360))
    out.save(dest, quality=88)
    (OUT / "sheet_tmp.png").unlink(missing_ok=True)
    print("sheet:", dest)


def encode(frames: Path, dest: Path, ffmpeg: str, keys: list[float]) -> None:
    """VP9 with alpha (yuva420p). Key frames every 0.5 s, and forced on each time the act seeks
    to (`keys`: the ready loop, the dive, the bob loop, the stroke loop, the duck): at 60 fps a
    seek to a 0.1 s mark between key frames decodes twice the frames it did at 30 -- a loop's
    seek shows her frozen while it does."""
    args = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "30", "-row-mt", "1",
            "-deadline", "good", "-cpu-used", "2", "-g", str(GOP), "-keyint_min", str(GOP), "-auto-alt-ref", "0",
            "-force_key_frames", ",".join("%g" % k for k in keys), "-an", str(dest)]
    subprocess.run(args, check=True)


ASSETS = ROOT / "data" / "ceremony" / "assets"
INDEX_LOCK = ROOT / ".tmp" / "ceremony-show" / "index.lock"


def register(name: str, checked: str) -> None:
    """Copy the clip into the ceremony's assets and pin it in index.json (this one entry only;
    every other row is written back as it was). `checked` = what a person looked at: the
    entry is `ok` only with it -- the clip is the robot character, no person (`people: []`).
    The index is shared with other render tools: the file is read and written inside the
    lock (.tmp/ceremony-show/index.lock), never from an earlier read."""
    import hashlib
    import time

    clip_def = CLIPS[name]
    clip = OUT / clip_def["file"]
    data = clip.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    dest = ASSETS / (clip_def["id"] + ".webm")
    points = json.loads((OUT / clip_def["points"]).read_text(encoding="utf-8"))
    entry = {
        "id": clip_def["id"], "kind": "video", "file": dest.name, "sha256": sha,
        "w": clip_def["spec"]["w"], "h": clip_def["spec"]["h"],
        "from_pin": None, "candidate": True, "checked": checked, "ok": True, "depth": None,
        "about": clip_def["about"], "class": "model", "shows": clip_def["shows"],
        "text": "", "source": "scripts/render_emma_dive.py (client/assets/emma-3d-lab/emma.glb)",
        "alpha": True, "bytes": len(data), "duration": points["times"]["end"], "fps": float(FPS),
        "frames": int(round(points["times"]["end"] * FPS)), "picked_by": ["emma_dive"], "people": [],
    }
    INDEX_LOCK.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    while True:
        try:
            INDEX_LOCK.mkdir()
            break
        except FileExistsError:
            if waited > 120:
                raise SystemExit("the index lock is held (%s): nothing registered" % INDEX_LOCK)
            time.sleep(2)
            waited += 2
    try:
        dest.write_bytes(data)
        index = ASSETS / "index.json"
        rows = json.loads(index.read_text(encoding="utf-8"))
        rows = [r for r in rows if r.get("id") != clip_def["id"]] + [entry]
        index.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    finally:
        INDEX_LOCK.rmdir()
    print("registered", clip_def["id"], sha, len(data), "bytes")
    # (23 Nov: the film shows CUT copies of this clip -- the notebook decodes VP9 alpha on its CPU)
    print("the film shows the cut copies: cut them again from these frames, look at them, then mark --\n"
          "  python scripts/cut_emma_clips.py --make %s_crop,%s_crop30\n"
          "  python scripts/cut_emma_clips.py --mark %s_crop,%s_crop30 --checked \"<what you looked at>\""
          % ((clip_def["id"],) * 4))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clip", choices=sorted(CLIPS), default="dive", help="which clip (default: the dive)")
    ap.add_argument("--sheet", help="comma-separated clip seconds: a contact sheet of those poses only")
    ap.add_argument("--zoom", help="with --sheet: x0,y0,x1,y1 (fractions of the frame) -- that part of each pose only")
    ap.add_argument("--register", metavar="CHECKED", help="pin the rendered clip in data/ceremony/assets/index.json "
                    "with this note of what was looked at (no render)")
    ap.add_argument("--ffmpeg")
    args = ap.parse_args()
    clip_def = CLIPS[args.clip]
    spec = clip_def["spec"]
    if args.register:
        register(args.clip, args.register)
        return 0
    from playwright.sync_api import sync_playwright
    from voice_line import find_ffmpeg

    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser, page = open_page(p, spec)
        points = page.evaluate("() => window.points()")
        (OUT / clip_def["points"]).write_text(json.dumps({"spec": spec, "fps": FPS, **points}, indent=1), encoding="utf-8")
        print("points:", json.dumps(points))
        if args.sheet:
            zoom = tuple(float(x) for x in args.zoom.split(",")) if args.zoom else None
            sheet(page, [float(x) for x in args.sheet.split(",")], OUT / clip_def["sheet"], zoom)
            browser.close()
            return 0
        frames = OUT / clip_def["frames"]
        frames.mkdir(exist_ok=True)
        for old in frames.glob("f*.png"):
            old.unlink()
        total = int(round(points["times"]["end"] * FPS))
        for i in range(total):
            grab(page, i / FPS, frames / ("f%04d.png" % i))
        browser.close()
    dest = OUT / clip_def["file"]
    encode(frames, dest, find_ffmpeg(args.ffmpeg), clip_def["keys"])
    print("clip:", dest, dest.stat().st_size, "bytes,", total, "frames at", FPS, "fps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
