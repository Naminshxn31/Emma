#!/usr/bin/env python3
"""
Point the camera at people and watch the numbers, before anything speaks.

    python scripts/watch_camera.py --list        # which camera index works
    python scripts/watch_camera.py               # window + live readout
    python scripts/watch_camera.py --no-window   # numbers only (headless)
    python scripts/watch_camera.py --save        # keep frames for eval_faces
    python scripts/watch_camera.py --camera 1      # or set FACE_CAMERA in .env
    python scripts/watch_camera.py --photos D:/shots   # no camera needed

Why this comes before the greeting
----------------------------------
Every number `scripts/eval_faces.py` produced came from studio and phone
portraits: front on, evenly lit, a metre away. The camera at the entrance is
none of those things, and the gap between the two is not small — it is the
whole reason the wake word was green for weeks while the owner's own voice
could not open it. `emma.wav` was English text-to-speech; these photographs
are the visual version of that mistake, and this script is where it gets
caught instead of shipped.

So this prints the cosine of the nearest match on *every* frame, including
the ones far below the threshold, the way `WakeStream._report` prints RMS
whether or not the name was heard. "It didn't recognise me" and "the camera
never saw a face" look identical from outside and have completely different
fixes; the readout tells them apart.

    seen  น.ต้า          0.612  GREET      <- above threshold, confirmed
    seen  น.ต้า          0.601  waiting    <- confirmed count not reached
    seen  ?              0.284  stranger
    ....  no face

`--save` writes frames to `data/faces/camera_probe/<name>_<n>.jpg` so a real
threshold can be measured off the real camera afterwards rather than argued
about.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import faces, facewatch  # noqa: E402
from app.config import settings  # noqa: E402

SAVE_DIR = ROOT / "data" / "faces" / "camera_probe"

# One copy of "is this actually a camera", shared with the server — the same
# reason `app/enrollment.py` holds the enrolment rules for both front ends.
# `say=print` keeps this script talking to whoever ran it.
from app.camera import (  # noqa: E402
    LIVE_FLOOR, WANT_HEIGHT, WANT_WIDTH, _backends, _liveness, _try_open,
)
from app import camera as _camera  # noqa: E402


def open_camera(index: int | None = None):
    return _camera.open_camera(index, say=print)


def list_cameras(highest: int = 7, show: bool = True) -> int:
    """Which indices hand over a *moving* picture, and what each one sees.

    Worth its own mode because "no camera", "the wrong camera" and "a
    virtual camera showing a logo" produce the same silence otherwise — the
    same reason `WAKE_DEBUG` exists for the microphone, and the same reason
    the sleep screen grew a level meter.
    """
    import cv2

    working, previews = [], []
    for index in range(highest + 1):
        for backend, name in _backends():
            cap = _try_open(index, backend, warmup=6)
            if cap is None:
                continue
            w, h = int(cap.get(3)), int(cap.get(4))
            motion, frame = _liveness(cap)
            cap.release()
            live = motion >= LIVE_FLOOR
            verdict = "live" if live else "STATIC IMAGE - not a real camera"
            print(f"  camera {index}  {name:5s}  {w}x{h}  motion {motion:5.2f}  {verdict}")
            previews.append((index, frame, live))
            if live:
                working.append(index)
            break
        else:
            print(f"  camera {index}  -      -           no frames")

    if previews:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        for index, frame, _ in previews:
            ok, buf = cv2.imencode(".jpg", frame)
            if ok:
                buf.tofile(str(SAVE_DIR / f"index_{index}.jpg"))
        print(f"\none frame from each saved to {SAVE_DIR}")
        if show:
            # ASCII: see the note on window titles in enroll_face.py.
            cv2.imshow("which camera is which - press any key",
                       _preview_sheet(previews))
            cv2.waitKey(0)
            cv2.destroyAllWindows()

    if not working:
        print("\nno camera produced a moving picture. Worth checking, in this order:")
        print("  - the index that says STATIC IMAGE is a virtual camera, not the "
              "real one — the physical device is on a different index")
        print("  - another app holding it (Teams, Zoom, a browser tab, "
              "Elgato Camera Hub)")
        print("  - Settings > Privacy & security > Camera > 'Let desktop apps "
              "access your camera'")
        print("  - a laptop lid switch or a physical shutter")
        return 1
    print(f"\nuse: --camera {working[0]}")
    return 0


def _preview_sheet(previews) -> np.ndarray:
    """One labelled thumbnail per index, so a person can just look.

    No amount of resolution and motion reporting tells you which index is
    the camera pointed at the door. The picture does.
    """
    import cv2

    cell = 320
    cols = max(1, min(len(previews), 4))
    rows = (len(previews) + cols - 1) // cols
    sheet = np.zeros((rows * (cell + 34), cols * cell, 3), np.uint8)
    for i, (index, frame, live) in enumerate(previews):
        r, c = divmod(i, cols)
        y, x = r * (cell + 34), c * cell
        sheet[y:y + cell, x:x + cell] = cv2.resize(frame, (cell, cell))
        colour = (60, 200, 60) if live else (60, 60, 220)
        cv2.putText(sheet, f"--camera {index}" + ("" if live else "  (static)"),
                    (x + 10, y + cell + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)
    return sheet


def score_photos(where: Path, watcher: facewatch.Watcher) -> int:
    """The same readout, from image files instead of a live camera.

    Not a convenience. The question this script exists to answer — how far
    the scores fall once the picture is not a studio portrait — is answered
    just as well by a photograph taken on a phone in the room, and the
    camera was unavailable on the machine that needed the answer. A
    measurement that can only be taken under perfect conditions is the
    measurement that never gets taken.

    Every file is judged on its own: the confirm-frames streak and the
    cooldown are sequence rules for a camera and mean nothing here.
    """
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    files = ([where] if where.is_file()
             else sorted(p for p in where.rglob("*") if p.suffix.lower() in exts))
    if not files:
        print(f"no images in {where}")
        return 1

    print(f"scoring {len(files)} image(s) against {len(watcher.gallery)} enrolled\n")
    for path in files:
        img = faces.read_image(path)
        if img is None:
            print(f"{path.name:<34} unreadable")
            continue
        found = faces.detect(img)
        if not found:
            print(f"{path.name:<34} no face found")
            continue
        face = found[0]
        x1, _, x2, _ = face.bbox
        width = x2 - x1
        i, score = watcher.gallery.match(face.vec)
        verdict = ("MATCH" if score >= watcher.threshold else "below threshold")
        extra = "" if width >= watcher.min_face_px else f"  (face {width}px — under FACE_MIN_PX)"
        print(f"{path.name:<34} {watcher.gallery.names[i]:<12} {score:+.3f}  "
              f"{width:>4}px  {verdict}{extra}")
    print("\nA name you recognise scoring near the studio numbers (0.55+) means the "
          "threshold holds up.\nA correct name at 0.35-0.45 means FACE_THRESHOLD has "
          "to come down, and the margin over\nstrangers (0.342 measured) gets thin — "
          "tell me the numbers before changing it.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=None,
                    help="capture index; omit to find the first live one "
                         "(or set FACE_CAMERA in .env)")
    ap.add_argument("--list", action="store_true",
                    help="probe camera indices 0-7 and report which ones deliver frames")
    ap.add_argument("--photos", type=Path, default=None,
                    help="score image files instead of a live camera (file or folder)")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--save", action="store_true", help="write frames with a face to disk")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--fps", type=float, default=None,
                    help="frames per second to analyse (default: FACE_FPS)")
    args = ap.parse_args()

    import cv2

    if args.list:
        # Before the models: which camera works is a hardware question and
        # answering it should not need a 280MB download.
        print("probing camera indices 0-7 ...")
        return list_cameras(show=not args.no_window)

    if not faces.available():
        print("face models not found — run scripts/fetch_face_model.py")
        return 1

    watcher = facewatch.Watcher.from_settings()
    if watcher is None:
        print(f"no gallery at {settings.face_gallery} — "
              "run scripts/build_face_gallery.py")
        return 1
    if args.threshold is not None:
        watcher.threshold = args.threshold

    print(f"gallery: {len(watcher.gallery)} enrolled "
          f"({', '.join(sorted(set(watcher.gallery.names)))})")
    print(f"threshold {watcher.threshold:.2f}   confirm {watcher.confirm_frames} frames   "
          f"min face {watcher.min_face_px}px")
    # The cooldown is for a robot that greets people, not for a person
    # standing in front of a window reading numbers off it.
    watcher.cooldown_s = 3.0
    print("cooldown forced to 3s for this probe.  q or ctrl-c to stop\n")

    if args.photos is not None:
        return score_photos(args.photos, watcher)

    cap = open_camera(args.camera)
    if cap is None:
        print(f"camera {args.camera} never delivered a frame.")
        print("run `--list` to see which indices do.")
        return 1
    if args.save:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / max(args.fps if args.fps is not None else settings.face_fps, 0.5)
    saved = 0
    last = 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("camera stopped returning frames — it was working a moment "
                      "ago, so something took it (another app) or it was unplugged")
                break

            now = time.monotonic()
            if now - last < interval:
                if not args.no_window:
                    cv2.imshow("watch_camera", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
                continue
            last = now

            found = faces.detect(frame)
            sighting = watcher.see(frame, now=now)

            if not found:
                print("....  no face", flush=True)
            else:
                face = found[0]
                x1, y1, x2, y2 = face.bbox
                width = x2 - x1
                name, score = "?", -1.0
                if len(watcher.gallery):
                    i, score = watcher.gallery.match(face.vec)
                    name = watcher.gallery.names[i]
                verdict = ("GREET" if sighting and sighting.kind == "known" else
                           "stranger-greet" if sighting else
                           "too far" if width < watcher.min_face_px else
                           "waiting" if score >= watcher.threshold else
                           "below threshold")
                print(f"seen  {name:<14} {score:+.3f}  {width:>4}px  {verdict}", flush=True)

                if args.save:
                    saved += 1
                    out = SAVE_DIR / f"{saved:04d}_{width}px.jpg"
                    cv2.imwrite(str(out), frame)

                if not args.no_window:
                    colour = ((0, 200, 0) if verdict == "GREET"
                              else (0, 165, 255) if score >= watcher.threshold
                              else (0, 0, 220))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
                    cv2.putText(frame, f"{score:+.2f} {width}px", (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2)

            if sighting:
                who = sighting.name or "(a stranger)"
                print(f"      -> would greet {who}  ({sighting.score:+.3f})", flush=True)

            if not args.no_window:
                cv2.imshow("watch_camera", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if not args.no_window:
            cv2.destroyAllWindows()

    if args.save:
        print(f"\nsaved {saved} frames to {SAVE_DIR}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
