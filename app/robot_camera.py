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

Active speaker (ASD_ENABLED, app/active_speaker.py) reads the same feed at
ASD_FPS, so with it on the page is asked to send that fast (`sender_fps`,
relayed as a `camctl` like the zoom) and each JPEG is decoded once for both
readers (`decoded_for`). ASD off: no rate is asked for and nothing the
page or the greeter sees changes.

A closer look (FACE_ZOOM_ENABLED)
---------------------------------
The greeter can ask the page for a crop of one face (`request_crop` -> a
`camctl` `crop`), cut from the camera's own frame and encoded at q95 rather
than the stream's q70 — on synthetic 40 px faces that lifted a colleague's
p10 score from 0.459 to 0.552 (scripts/eval_face_distance.py). The answer
comes back on the same socket as a *tagged* binary message (`CROP_TAG`,
then a JSON header line, then the JPEG): a frame starts 0xFF 0xD8, a tagged
message with a NUL, so `push` tells them apart without the socket handler in
main.py knowing either exists. A crop never becomes a frame — it is one
look at one face, kept apart (`take_crop`, at most once each), because a
crop served as a frame would be the same picture confirming itself.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.robot_camera")

#: The most the page may be asked for. Each frame is a full-resolution JPEG
#: encoded in the robot's WebView and decoded here; past this the encode
#: alone is a real share of the robot's CPU.
MAX_SENDER_FPS = 30.0

#: The first bytes of a tagged binary message from the page (a crop answer).
#: Must match `CROP_TAG` in client/voice-preview.html (a test compares).
CROP_TAG = b"\x00EMMACROP\n"
#: A crop request older than this is not sent again with a later camctl (a
#: zoom change, a reconnect): it was about where somebody stood back then.
CROP_REQUEST_TTL_S = 2.0
#: A crop answer older than this is about another moment of the face.
CROP_MAX_AGE_S = 1.0
#: The most a crop may weigh. A q95 crop of a face region is tens of KB;
#: anything near this is not what was asked for.
MAX_CROP_BYTES = 2_000_000


def sender_fps() -> float | None:
    """The frame rate the robot page should send at, or None for "its own".

    Only active speaker asks (lips need ~15 samples a second; the greeter is
    happy at FACE_FPS). And only when the greeter really reads the robot's
    camera -- a server on a local lens has no use for a faster robot page.
    The resolution is deliberately NOT lowered with it: FACE_MIN_PX,
    FOCUS_FACE_MIN_PX and the doorway measurements are all in frame pixels,
    and a smaller frame would move every one of them at once.
    """
    if not settings.asd_enabled or settings.face_camera_source != "robot":
        return None
    return max(1.0, min(MAX_SENDER_FPS, float(settings.asd_fps)))


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
        # The greeter's last per-frame verdict, relayed to the robot page's
        # temporary debug panel (owner, 2026-09-30: "อยากรู้ว่ามันจับคนได้
        # ไหม"). Same latest-wins shape as _ctl; diagnostic words only, no
        # frame data, and nothing reads it back into a decision.
        self._face_status: dict = {}
        self._face_seq = 0
        # One decode per frame however many readers there are (the greeter
        # at FACE_FPS, active speaker at ASD_FPS). Readers share the array:
        # they must not write into it -- none does (detector, recogniser and
        # landmark crops all copy). A 1080p decode is ~7 ms on the desk PC
        # and several times that on the robot's A76 cores.
        self._decode_lock = threading.Lock()
        self._dec_seq = -1
        self._dec_frame = None
        # Arrival rate, smoothed: what the page really sends, for the log.
        self._interval = 0.0
        # FACE_ZOOM: the outstanding crop request, the newest answer, and the
        # camera's reported capabilities (logged once per change).
        self._crop_seq = 0
        self._crop_asked_at = 0.0
        self._crop: dict | None = None
        self._caps_logged: tuple | None = None

    def set_control(self, **kw) -> int:
        with self._lock:
            for k, v in kw.items():
                if v is not None:
                    self._ctl[k] = v
            self._ctl_seq += 1
            return self._ctl_seq

    def control(self) -> tuple[int, dict]:
        with self._lock:
            seq, ctl = self._ctl_seq, dict(self._ctl)
            if "crop" in ctl and time.monotonic() - self._crop_asked_at > CROP_REQUEST_TTL_S:
                # Re-sent with a later control it would cut where somebody
                # stood seconds ago; the page answers each id once anyway.
                ctl.pop("crop")
        # Read live, not stored: a page that connects is told the rate the
        # server wants now, and with ASD off the dict is exactly what it
        # always was (so /ws/camera sends nothing it did not send before).
        fps = sender_fps()
        if fps is not None:
            ctl.setdefault("fps", fps)
        return seq, ctl

    def clear_control(self, *names: str) -> None:
        """Forget transient controls after their connected kiosk saw them."""
        with self._lock:
            for name in names:
                self._ctl.pop(name, None)

    def set_face_status(self, status: dict) -> None:
        with self._lock:
            self._face_status = dict(status)
            self._face_seq += 1

    def face_status(self) -> tuple[int, dict]:
        with self._lock:
            return self._face_seq, dict(self._face_status)

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
        if data[:len(CROP_TAG)] == CROP_TAG:
            self._push_crop(data)
            return
        with self._lock:
            now = time.monotonic()
            if self._at and 0 < now - self._at < 2.0:
                gap = now - self._at
                self._interval = gap if not self._interval else 0.9 * self._interval + 0.1 * gap
            self._jpeg = bytes(data)
            self._at = now
            self._seq += 1

    # -- FACE_ZOOM: one closer look at one face --------------------------

    def request_crop(self, bbox, frame_w: int, frame_h: int, photo: bool = False) -> int:
        """Ask the page for a crop around `bbox` (frame pixels of a
        `frame_w` x `frame_h` frame). Returns the request id."""
        with self._lock:
            self._crop_seq += 1
            self._crop_asked_at = time.monotonic()
            rid = self._crop_seq
        self.set_control(crop={"id": rid, "box": [int(v) for v in bbox][:4],
                               "w": int(frame_w), "h": int(frame_h),
                               "pad": 1.5, "photo": bool(photo)})
        return rid

    def _push_crop(self, data: bytes) -> None:
        """A tagged crop answer: header line (JSON), then the JPEG."""
        import json

        if len(data) > MAX_CROP_BYTES:
            return
        body = data[len(CROP_TAG):]
        cut = body.find(b"\n")
        if cut <= 0:
            return
        try:
            head = json.loads(body[:cut].decode("utf-8"))
            region = [float(v) for v in head["region"]][:4]
            scale = float(head.get("scale") or 0)
            rid = int(head["id"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, AttributeError):
            logger.debug("robot camera: unreadable crop header", exc_info=True)
            return
        if len(region) != 4 or not (0 < scale <= 16) or not all(map(_finite, region)):
            return
        jpeg = body[cut + 1:]
        if not jpeg:
            return
        with self._lock:
            if rid != self._crop_seq:
                return          # an answer to a request that is not the latest
            self._crop = {"id": rid, "region": region, "scale": scale, "jpeg": bytes(jpeg),
                          "src": str(head.get("src") or "video")[:8], "at": time.monotonic()}
        self._log_caps(head)

    def _log_caps(self, head: dict) -> None:
        """Say once what the robot's camera can do: the stream it sends, the
        largest video it offers, and the largest photo. FACE_ZOOM_PHOTO is
        worth turning on only when the photo is bigger than the video."""
        def size(key):
            v = head.get(key)
            try:
                return "%dx%d" % (int(v[0]), int(v[1]))
            except (TypeError, ValueError, IndexError):
                return "?"

        caps = (size("video"), size("video_max"), size("photo_max"))
        if caps == self._caps_logged:
            return
        self._caps_logged = caps
        logger.info("robot camera caps: video %s, video max %s, photo max %s (crop src %s)",
                    *caps, str(head.get("src") or "video")[:8])

    def take_crop(self, max_age_s: float = CROP_MAX_AGE_S) -> dict | None:
        """The newest crop answer, decoded, once — or None. A crop is one
        look; handed out twice it would confirm itself across frames."""
        with self._lock:
            crop, self._crop = self._crop, None
        if crop is None or time.monotonic() - crop["at"] > max_age_s:
            return None
        frame = _decode(crop.pop("jpeg"))
        if frame is None:
            return None
        crop["frame"] = frame
        return crop

    def latest(self) -> tuple[int, bytes | None, float]:
        with self._lock:
            return self._seq, self._jpeg, self._at

    def arrival_fps(self) -> float:
        """Frames per second the page is really sending (0 = none lately)."""
        with self._lock:
            fresh = self._at and time.monotonic() - self._at < 2.0
            return round(1.0 / self._interval, 1) if fresh and self._interval > 0 else 0.0

    def decoded_for(self, seq: int, jpeg: bytes):
        """The BGR frame for `seq` (whose bytes are `jpeg`), decoded once.

        A second reader of the same frame gets the same array back; a newer
        frame replaces it. None for bytes that are not a picture -- cached
        too, so the second reader does not pay to find that out again.
        """
        with self._decode_lock:
            if self._dec_seq == seq:
                return self._dec_frame
            frame = _decode(jpeg)
            self._dec_seq, self._dec_frame = seq, frame
            return frame

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
                frame = self._feed.decoded_for(seq, jpeg)
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


def _finite(v: float) -> bool:
    import math

    return math.isfinite(v)


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
