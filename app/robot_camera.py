"""The robot's camera, arriving over a WebSocket instead of a USB cable.

The face greeter was built around `cv2.VideoCapture` on the desk PC. The
robot's two cameras are on the robot — an Android machine across the room —
and the server cannot open them. What the robot *does* run is the kiosk page
in Chrome, which already holds a token and a socket to this server and can
ask Android for the camera the same way it asks for the microphone. So the
page sends JPEG frames to `/ws/camera`, and this module hands them to the
greeter through the one interface it already speaks: a capture handle with
`read()`. No app on the robot, no second protocol, no second decision about
who is standing at the door.

Two things `RobotCapture.read` does on purpose:

- **It waits for a new frame and never serves the same one twice.** The
  greeter reads at FACE_FPS; if the link stalls, a frozen face served over
  and over would confirm itself across the frames the greeter demands and
  greet a photograph. A stall reads as an empty read, exactly like a USB
  camera that stopped delivering, so the greeter's existing reopen path
  handles it.
- **Bad bytes are an empty read, not an exception.** The sender is a
  browser on a Wi-Fi link; a truncated JPEG must not end the greeting loop.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

logger = logging.getLogger("condo_voice.robot_camera")


class RobotCameraFeed:
    """The newest JPEG from the robot, and how fresh it is."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._at = 0.0
        self._seq = 0
        self._senders = 0
        # Control the robot sends BACK to its own page (e.g. camera zoom). The
        # cockpit is a different browser and cannot touch the robot's camera
        # track directly; it writes a wish here and the /ws/camera handler
        # relays it to the page, which applies it. Kept latest-wins with a
        # version so a page that reconnects picks up the current setting.
        self._ctl: dict = {}
        self._ctl_seq = 0
        # Audio is captured by the robot's kiosk page too.  Keep only a
        # short-lived RMS reading: no PCM, recording, or transcript lands
        # here.  This lets a desk console prove which machine is hearing
        # without accidentally opening the desk browser's microphone.
        self._mic_level = 0.0
        self._mic_at = 0.0
        self._mic_label = ""

    def set_control(self, **kw) -> int:
        with self._lock:
            for k, v in kw.items():
                if v is not None:
                    self._ctl[k] = v
            self._ctl_seq += 1
            return self._ctl_seq

    def control(self) -> tuple[int, dict]:
        with self._lock:
            return self._ctl_seq, dict(self._ctl)

    def clear_control(self, *names: str) -> None:
        """Forget transient controls after their connected kiosk saw them."""
        with self._lock:
            for name in names:
                self._ctl.pop(name, None)

    def attach(self) -> None:
        with self._lock:
            self._senders += 1
            n = self._senders
        logger.info("robot camera: sender connected (%d)", n)

    def detach(self) -> None:
        with self._lock:
            self._senders = max(0, self._senders - 1)
            n = self._senders
        logger.info("robot camera: sender gone (%d left)", n)

    def push(self, data: bytes) -> None:
        with self._lock:
            self._jpeg = bytes(data)
            self._at = time.monotonic()
            self._seq += 1

    def latest(self) -> tuple[int, bytes | None, float]:
        with self._lock:
            return self._seq, self._jpeg, self._at

    def push_mic_level(self, level: float, label: str = "") -> None:
        """Publish one bounded loudness reading from the robot kiosk."""
        value = max(0.0, min(1.0, float(level)))
        with self._lock:
            self._mic_level = value
            self._mic_at = time.monotonic()
            self._mic_label = str(label)[:120]

    def mic_level(self) -> dict:
        """Return a fresh robot-kiosk level, never a frozen meter value."""
        with self._lock:
            level, at, label = self._mic_level, self._mic_at, self._mic_label
        age = time.monotonic() - at if at else None
        return {
            "level": round(level, 5),
            "age_s": None if age is None else round(age, 2),
            "streaming": age is not None and age < 2.0,
            "source": "robot_kiosk",
            "device_label": label or None,
        }

    @property
    def senders(self) -> int:
        with self._lock:
            return self._senders


class RobotCapture:
    """A `cv2.VideoCapture` look-alike over a RobotCameraFeed.

    Only the members the greeter and the camera probe use: `isOpened`,
    `read`, `release`, `get(3|4)`.
    """

    def __init__(self, feed: RobotCameraFeed, wait_s: float = 1.5) -> None:
        self._feed = feed
        self._wait_s = wait_s
        self._seen = feed.latest()[0]
        self._size = (0, 0)
        self._announced = False

    def isOpened(self) -> bool:  # noqa: N802 - cv2's spelling
        return True

    def release(self) -> None:
        return None

    def get(self, prop: int) -> float:
        # 3 = CAP_PROP_FRAME_WIDTH, 4 = CAP_PROP_FRAME_HEIGHT
        return float(self._size[0] if prop == 3 else self._size[1] if prop == 4 else 0)

    def read(self):
        deadline = time.monotonic() + self._wait_s
        while True:
            seq, jpeg, _ = self._feed.latest()
            if seq != self._seen and jpeg:
                self._seen = seq
                frame = _decode(jpeg)
                if frame is None:
                    return False, None
                self._size = (frame.shape[1], frame.shape[0])
                if not self._announced:
                    self._announced = True
                    logger.info("robot camera: first frame %dx%d", *self._size)
                return True, frame
            if time.monotonic() >= deadline:
                return False, None
            time.sleep(0.02)


def _decode(jpeg: bytes):
    try:
        import cv2

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        logger.debug("robot camera: undecodable frame", exc_info=True)
        return None
    if frame is None or frame.size == 0:
        return None
    return frame


#: One feed per process - the robot is one robot. The socket handler pushes
#: into it; `capture()` is what `camera.open_camera` returns when
#: FACE_CAMERA_SOURCE=robot.
feed = RobotCameraFeed()


def capture(wait_s: float = 1.5) -> RobotCapture:
    return RobotCapture(feed, wait_s=wait_s)
