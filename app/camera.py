"""
Getting a working camera handle, which is not the same as opening one.

Split out of `scripts/watch_camera.py` when the server grew a camera of its
own: two programs opening the same lens must not each have their own idea of
what "working" means. The scars are all in `_try_open` and `_liveness` and
they were expensive — an infrared camera that enumerates like a webcam and
streams like nothing, a virtual camera that hands over a flawless still
photograph of its own logo for ever, and `isOpened()` answering True to both.

The command line prints; the server logs. That is the only difference, and
it is a `say` argument rather than two copies of the search.
"""
from __future__ import annotations

import logging
import sys
import time

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.camera")


#: Capture size to ask for. See the note in `_try_open`: this is a face-size
#: knob, not a picture-quality one.
WANT_WIDTH, WANT_HEIGHT = 1280, 720


def _backends():
    """Capture backends to try, best first.

    `CAP_DSHOW` first on Windows: Media Foundation is the default there and
    it takes several seconds to open some webcams, sometimes never. `None`
    is OpenCV's own pick, and the only entry that exists off Windows.
    """
    import cv2

    if sys.platform == "win32":
        return [(cv2.CAP_DSHOW, "dshow"), (cv2.CAP_MSMF, "msmf"), (None, "auto")]
    return [(None, "auto")]


def _try_open(index: int, backend, warmup: int = 12):
    """A handle that has actually produced a frame, or None.

    `isOpened()` is not the test. It returned True on the owner's machine
    for a device that then failed every `read()` with
    `MF_E_HW_MFT_FAILED_START_STREAMING` — a laptop's Windows Hello infrared
    camera, which enumerates like a webcam and cannot stream like one. The
    same True comes back for a virtual camera and for a device another
    application is holding.

    So the check is a frame in hand. And it is a frame *after* a short
    warm-up: real webcams routinely fail the first few reads while exposure
    settles, so one failed read is not evidence of a broken device either.
    """
    import cv2

    cap = cv2.VideoCapture(index) if backend is None else cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None
    # Ask for 720p. DirectShow opened the Facecam at 960x540 by default,
    # and resolution here is not about picture quality — it is how many
    # pixels wide a face is at two metres, which is the thing
    # `FACE_MIN_PX` measures and the thing that decides how far away
    # somebody can be recognised. Requests, not demands: a camera that
    # cannot do it keeps whatever it had.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, WANT_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, WANT_HEIGHT)
    for _ in range(warmup):
        ok, frame = cap.read()
        if ok and frame is not None and frame.size:
            return cap
        time.sleep(0.1)
    cap.release()
    return None


def open_camera(index: int | None = None, say=None):
    """A working capture handle, or None.

    `index=None` means find one. Hard-coding 0 was wrong on the machine
    this was written for: index 0 there is the Elgato *Virtual* Camera,
    which hands over a flawless still image of its own logo, and index 1 is
    the actual lens. A default that has to be corrected by hand on the one
    machine that matters is not a default, it is a trap with a number in it
    — so the number is looked up instead, and `FACE_CAMERA` pins it once
    the answer is known.
    """
    say = logger.info if say is None else say
    if settings.face_camera_source == "robot":
        # Not a lens on this machine at all - see app/robot_camera.py. The
        # handle is always "open"; a robot that is not sending yet reads
        # as empty frames, which the greeter already treats as a camera
        # that stopped delivering.
        from app import robot_camera

        say("camera source: the robot's kiosk page (/ws/camera) - "
            f"{robot_camera.feed.senders} sender(s) connected")
        return robot_camera.capture()
    if index is None:
        index = settings.face_camera
    if index is not None and index >= 0:
        for backend, name in _backends():
            cap = _try_open(index, backend)
            if cap is None:
                continue
            motion, _ = _liveness(cap)
            if motion < LIVE_FLOOR:
                say(f"warning: camera {index} is not moving (motion {motion:.2f}) "
                      f"— that is usually a virtual camera showing a placeholder.")
                say("         `--list` shows what each index is looking at.")
            say(f"camera {index} opened via {name} "
                  f"({int(cap.get(3))}x{int(cap.get(4))})")
            return cap
        return None

    say("looking for a camera with a moving picture ...")
    for candidate in range(8):
        for backend, name in _backends():
            cap = _try_open(candidate, backend, warmup=6)
            if cap is None:
                continue
            motion, _ = _liveness(cap)
            if motion >= LIVE_FLOOR:
                say(f"using camera {candidate} via {name} "
                      f"({int(cap.get(3))}x{int(cap.get(4))}, motion {motion:.2f})")
                say(f"pin it with FACE_CAMERA={candidate} in .env to skip this search")
                return cap
            say(f"  camera {candidate}: static image, skipping")
            cap.release()
            break
    return None


#: Below this mean per-pixel change between two frames a third of a second
#: apart, nothing in front of the lens is moving — and nothing ever will,
#: because it is not a lens. Real sensors have noise; a rendered placeholder
#: is bit-identical frame to frame.
LIVE_FLOOR = 0.35


def _liveness(cap, gap: float = 0.35) -> tuple[float, np.ndarray]:
    """(how much the picture changes, a frame to look at).

    Delivering frames is not the same as being a camera. The Elgato Virtual
    Camera hands over a perfectly good 1080p image of its own logo and the
    words "Please run Camera Hub", forever, and every check this script had
    called that working. So the test is motion: point a real camera at a
    still room and sensor noise still moves the numbers.
    """
    import cv2

    ok, first = cap.read()
    if not ok or first is None:
        return 0.0, np.zeros((2, 2, 3), np.uint8)
    time.sleep(gap)
    last = first
    for _ in range(4):
        ok, frame = cap.read()
        if ok and frame is not None:
            last = frame
    a = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY).astype(np.float32)
    b = cv2.cvtColor(last, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(a - b).mean()), last
