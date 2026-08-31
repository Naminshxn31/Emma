#!/usr/bin/env python3
"""
Teach the gallery a face from the camera it will actually be recognised on.

    python scripts/enroll_face.py --name "ต้า"              # 5 shots from the camera
    python scripts/enroll_face.py --name "ต้า" --shots 8
    python scripts/enroll_face.py --name "คุณเอ" --photos D:/shots
    python scripts/enroll_face.py --list                    # who has camera shots
    python scripts/enroll_face.py --name "ต้า" --no-window   # headless machine

For more than one or two people, `scripts/enroll_app.py` is the better tool:
it stays open, takes the name in Thai, and lets the camera be picked by its
real device name. This script is the same rules with a command line on it.

A preview window shows what the camera is seeing while it captures, and the
crops that are about to be written are put on screen for a yes before
anything is saved. The person being enrolled is standing in front of the
lens with their back to the console, so the console is the one place the
feedback is no use to them.

Then rebuild the gallery, which is the step that makes them count:

    python scripts/build_face_gallery.py

Why enrol from the camera at all
--------------------------------
The staff portraits are studio work: front on, evenly lit, a metre away, one
face filling the frame. The entrance camera is a different instrument, and
every measured number in this project so far comes from the first kind of
picture. Lowering `FACE_THRESHOLD` until the entrance camera clears it is
the wrong fix — the margin over strangers was measured at 0.342 and there is
not much room above it. Adding a picture *taken by the entrance camera* is
the right one: it moves the enrolment to where the question is asked instead
of moving the answer.

This is the same shape as the wake word's third round. The spelling that
finally caught the owner's own "เอ็มม่า" did not come from cleaner
text-to-speech; it came from recordings of the real mouth in the real room.

Why the crops are kept as images
--------------------------------
`app/faces.py` says the gallery holds vectors, not photographs, and that
stays true of the gallery. But a vector is frozen to one model: the day
buffalo_l is replaced, every vector in the file is unusable, and if the
crops were not kept the whole staff would have to stand in front of a camera
again. So the crops live here, in gitignored `data/faces/enrolled/`, and the
gallery is rebuilt from them — the same reason `data/slides/index.json` is
committed while `embeddings.npz` is not.

Enrolling the wrong person is permanent
---------------------------------------
A face filed under the wrong name is a robot confidently greeting somebody
else's colleague, for as long as nobody notices, and nothing about the
greeting looks uncertain. So before anything is written this checks the new
face against everybody already enrolled, and refuses when it looks like
somebody who is already in there under a different name.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import enrollment, faces  # noqa: E402
from app.config import settings  # noqa: E402

# The rules live in `app/enrollment.py`, shared with the enrolment station
# (`scripts/enroll_app.py`). Two front ends, one set of refusals — this
# project already paid for four copies of "what to do when a call ends".
ENROLLED = enrollment.ENROLLED
CROP_MARGIN = enrollment.CROP_MARGIN
AGREEMENT_FLOOR = enrollment.AGREEMENT_FLOOR
crop_face = enrollment.crop_face
shots_agree = enrollment.shots_agree
check_against_gallery = enrollment.check_against_gallery


#: Overlay text stays ASCII. `cv2.putText` has no Thai glyphs and renders
#: them as boxes, so the name being enrolled is printed to the terminal and
#: the window says how the capture is going, which is the part you need
#: while you are standing in front of the lens and cannot read the console.
_OK = (60, 200, 60)
_BUSY = (0, 165, 255)
_BAD = (60, 60, 220)


def _window(title: str):
    import cv2

    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    try:
        cv2.setWindowProperty(title, cv2.WND_PROP_TOPMOST, 1)
    except cv2.error:  # pragma: no cover - not every build has it
        pass
    return title


def _draw(frame, found, status: str, colour, taken: int, count: int):
    """Box, margin box, and a one-line status, drawn on a copy."""
    import cv2

    view = frame.copy()
    if found:
        x1, y1, x2, y2 = found[0].bbox
        pad = int(CROP_MARGIN * max(x2 - x1, y2 - y1))
        # Two boxes: the face, and what actually gets written to disk. The
        # second one is the one that matters and is invisible otherwise.
        cv2.rectangle(view, (x1 - pad, y1 - pad), (x2 + pad, y2 + pad), colour, 1)
        cv2.rectangle(view, (x1, y1), (x2, y2), colour, 2)
        cv2.putText(view, f"{x2 - x1}px", (x1, max(22, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
    h = view.shape[0]
    cv2.putText(view, status, (14, h - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colour, 2)
    cv2.putText(view, f"shots {taken}/{count}   [q] cancel", (14, h - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
    return view


def shots_from_camera(count: int, camera: int | None, spacing: float,
                      show: bool = True) -> list[np.ndarray]:
    """Frames with one clear subject, spaced out in time.

    Spaced because thirty frames of somebody holding still is one
    photograph thirty times. A blink, a turn, a step is what makes the
    second shot worth having.

    The preview window is not decoration. The person being enrolled is
    standing in front of the lens with their back to the console, so
    "nothing was captured", "you are too far away" and "it caught the
    colleague behind you" are indistinguishable to the one person who could
    fix any of them. Same argument as the level meter on the sleep screen:
    a microphone that is off and a name pronounced wrong looked identical
    until the page started showing what was arriving.
    """
    import cv2

    from scripts.watch_camera import open_camera  # noqa: PLC0415

    cap = open_camera(camera)
    if cap is None:
        print(f"camera {camera} never delivered a frame — "
              f"try `python scripts/watch_camera.py --list`")
        return []

    title = _window("enrol - look at the camera") if show else None
    kept: list[np.ndarray] = []
    print(f"\nlook at the camera. Taking {count} shots, one every {spacing:.1f}s.")
    print("Move a little between them — a different angle is worth more than "
          "a sharper copy of the same one.\n")

    start = time.monotonic()
    next_shot = start + 3.0          # three seconds to get into frame
    next_look = 0.0
    found: list = []
    cancelled = False
    interval = 1.0 / max(settings.face_fps, 0.5)
    try:
        while len(kept) < count:
            ok, frame = cap.read()
            if not ok:
                print("  camera stopped returning frames")
                break

            # Look at every FACE_FPS-th frame, not at all thirty a second.
            # This loop had no rate limit at all, and one frame costs three
            # seconds of CPU time across sixteen cores — which is the whole
            # of "the machine sits at 100% while the camera is on". The
            # preview keeps running at camera speed with the last box drawn
            # on it, because a preview that stutters looks broken.
            now = time.monotonic()
            if now >= next_look:
                found = faces.locate(frame)
                next_look = now + interval
            wide_enough = (found and
                           (found[0].bbox[2] - found[0].bbox[0]) >= settings.face_min_px)
            if not found:
                status, colour = "no face", _BAD
            elif len(found) > 1:
                status, colour = f"{len(found)} faces - only one person please", _BAD
            elif not wide_enough:
                status, colour = "come closer", _BAD
            else:
                left = next_shot - time.monotonic()
                if left > 0:
                    status, colour = f"hold still... {left:.1f}s", _BUSY
                else:
                    status, colour = "captured", _OK

            ready = found and len(found) == 1 and wide_enough
            if ready and now >= next_shot:
                kept.append(frame.copy())
                next_shot = now + spacing
                width = found[0].bbox[2] - found[0].bbox[0]
                print(f"  shot {len(kept)}/{count}  ({width}px)", flush=True)
                if show:
                    # A white frame, so the shot is visible as an event and
                    # not just as a counter that moved.
                    flash = _draw(frame, found, "captured", _OK, len(kept), count)
                    cv2.rectangle(flash, (0, 0), (flash.shape[1] - 1, flash.shape[0] - 1),
                                  (255, 255, 255), 12)
                    cv2.imshow(title, flash)
                    cv2.waitKey(120)
                    continue
            elif not ready:
                # Do not start the clock while the frame is unusable, or the
                # spacing runs out during "come closer" and the batch ends
                # up being whatever happened to be in front of the lens.
                next_shot = max(next_shot, now + 0.4)

            if show:
                cv2.imshow(title, _draw(frame, found, status, colour, len(kept), count))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    cancelled = True
                    break
            elif now - start > 3 + count * spacing * 8:
                print("  gave up waiting for usable frames")
                break
    finally:
        cap.release()
        if show:
            cv2.destroyAllWindows()
    if cancelled:
        print("  cancelled — nothing saved")
        return []
    return kept


#: Thumbnails per row on the confirmation sheet.
SHEET_COLS = 6
SHEET_CELL = 220


def contact_sheet(crops: list[np.ndarray]) -> np.ndarray:
    """The crops laid out for a human to look at, wrapped into rows.

    Wrapped rather than one long strip because `--photos` on a folder can
    hand this fifty crops, and a window eleven thousand pixels wide shows
    the first three of them — a confirmation step nobody can see is worse
    than none, because it looks like one happened.
    """
    import cv2

    cols = max(1, min(len(crops), SHEET_COLS))
    rows = (len(crops) + cols - 1) // cols
    cell = SHEET_CELL
    sheet = np.zeros((rows * cell + 44, cols * cell, 3), np.uint8)
    for i, crop in enumerate(crops):
        r, c = divmod(i, cols)
        sheet[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell] = cv2.resize(
            crop, (cell, cell))
    cv2.putText(sheet, "save these? [y] yes   [n] no", (12, rows * cell + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2)
    return sheet


def confirm(crops: list[np.ndarray], name: str) -> bool:
    """Show what is about to be written and wait for a yes.

    The whole point of the question "how would I know?": a capture that
    looks fine on the counter can still be five photographs of the person
    walking past behind you, and the moment to catch that is before it
    becomes a name in the gallery — not weeks later when the robot greets
    somebody wrong.
    """
    import cv2

    strip = contact_sheet(crops)

    # ASCII only. OpenCV hands window titles to the Windows ANSI code page,
    # so an em dash comes out as "enrol a?" and a Thai name comes out as
    # boxes — cp874 for the third time in this project, now in a title bar.
    title = _window(f"confirm - {len(crops)} shot(s) - answer y or n")
    cv2.imshow(title, strip)
    try:
        while True:
            key = cv2.waitKey(50) & 0xFF
            if key in (ord("y"), 13):
                return True
            if key in (ord("n"), ord("q"), 27):
                return False
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                return False
    finally:
        cv2.destroyAllWindows()


def shots_from_photos(where: Path) -> list[np.ndarray]:
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    files = ([where] if where.is_file()
             else sorted(p for p in where.rglob("*") if p.suffix.lower() in exts))
    out = []
    for path in files:
        img = faces.read_image(path)
        if img is None:
            print(f"  {path.name}: unreadable")
            continue
        out.append(img)
    return out


def list_enrolled() -> int:
    people = enrollment.enrolled()
    if not people:
        print("nothing enrolled from a camera yet")
        return 0
    for name, count in people:
        print(f"  {name:<20} {count} shot(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="the name the robot should say")
    ap.add_argument("--shots", type=int, default=5)
    ap.add_argument("--camera", type=int, default=None,
                    help="capture index; omit to find the first live one "
                         "(or set FACE_CAMERA in .env)")
    ap.add_argument("--spacing", type=float, default=0.8, help="seconds between shots")
    ap.add_argument("--photos", type=Path, default=None,
                    help="use image files instead of the camera")
    ap.add_argument("--list", action="store_true", help="who has camera shots already")
    ap.add_argument("--replace", action="store_true",
                    help="drop this person's existing shots first")
    ap.add_argument("--force", action="store_true",
                    help="enrol even if the face matches somebody already in the gallery")
    ap.add_argument("--no-window", action="store_true",
                    help="no preview and no confirmation step (headless machines)")
    args = ap.parse_args()

    if args.list:
        return list_enrolled()
    if not args.name:
        print("--name is required (or --list)")
        return 1
    if not faces.available():
        print("face models not found — run scripts/fetch_face_model.py")
        return 1

    import cv2

    show = not args.no_window
    frames = (shots_from_photos(args.photos) if args.photos is not None
              else shots_from_camera(args.shots, args.camera, args.spacing, show=show))
    if not frames:
        print("nothing captured")
        return 1

    # Embed first, write nothing yet. Everything below can still refuse.
    vecs, crops = [], []
    for frame in frames:
        found = faces.detect(frame)
        if len(found) != 1:
            continue
        vecs.append(found[0].vec)
        crops.append(crop_face(frame, found[0]))
    if not vecs:
        print("no shot had exactly one face in it — nothing enrolled")
        return 1

    worst = shots_agree(vecs)
    refusal = enrollment.review(vecs, args.name)
    if refusal and not args.force:
        print(f"\nrefused: {refusal}")
        return 1
    if refusal:
        print(f"\nwarning (--force): {refusal}\n")

    # Last gate, and the only one a person is behind. Everything above is
    # the machine checking itself; this is somebody looking at the actual
    # pictures that are about to become a name.
    if show and not confirm(crops, args.name):
        print("not saved")
        return 1

    folder = ENROLLED / args.name
    if args.replace and folder.is_dir():
        shutil.rmtree(folder)
        print(f"dropped the previous shots for {args.name}")
    folder.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for i, crop in enumerate(crops, 1):
        out = folder / f"{stamp}_{i}.jpg"
        # `imencode` + tofile, not `imwrite`: the names are Thai and OpenCV's
        # writer hands the path to the ANSI code page. Same cp874 trap as
        # `faces.read_image`, on the way out.
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            buf.tofile(str(out))

    total = len(sorted(folder.glob("*.jpg")))
    print(f"\nsaved {len(crops)} shot(s) to {folder}  ({total} in total for {args.name})")
    print(f"shots agree with each other at {worst:+.3f} and up")
    print("\nnow run:  python scripts/build_face_gallery.py")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
