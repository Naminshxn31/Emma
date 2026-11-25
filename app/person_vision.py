"""Is that a person? The head camera's answer, for walking up to whoever she sees (PERSON_VISION).

Owner, 5 Nov 2026 (of somebody who stood still before she stopped, and of a chair somebody carried
in, neither of which the lidar can tell): "ให้กล้องช่วยตรวจดูไม่ได้หรอ แบบเห็นเป็นคน ก็เข้าไปทัก".
The lidar knows where a thing stands and that it is the size of legs; it cannot know what it is.
The face detector needs a face turned to the lens. This sees a person's shape, in a picture, in
about the time of one frame.

YOLOX-nano (Megvii, Apache-2.0; COCO), one thread, only while staff have the walk on and at most
FACE_APPROACH's own pace of looks. Nothing leaves this file but boxes: no picture, no vector, no
identity, nothing kept. Measured on the robot (5 Nov, a real head-camera picture): 97 ms a frame on
one thread, 55 ms on two; its larger sibling yolox_tiny 300 / 170 ms.

The model file is pinned (`SHA256`): `scripts/fetch_person_model.py` on the PC, and `robot_server.py
deploy` has the robot fetch the same bytes. Anything else about the file (missing, another hash, a
load that fails) is one line in the log and `available()` False: face_approach then does not walk
to anything only this could have said was a person.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

logger = logging.getLogger("condo_voice.person_vision")

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data" / "models" / "person" / "yolox_nano.onnx"
URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx"
SHA256 = "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d"
SIZE = 416
STRIDES = (8, 16, 32)
PERSON = 0
NMS_IOU = 0.45
#: The export's letterbox fill: what the model was trained to read as "nothing here".
FILL = 114


@dataclass(frozen=True)
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    score: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


_lock = threading.Lock()
_state: dict = {"session": None, "grid": None, "strides": None, "failed": False}


def reset() -> None:
    _state.update(session=None, grid=None, strides=None, failed=False)


def _load():
    """The session, or None with one log line. Never raises: the door camera lives on this thread."""
    if _state["session"] is not None or _state["failed"]:
        return _state["session"]
    with _lock:
        if _state["session"] is not None or _state["failed"]:
            return _state["session"]
        try:
            import numpy as np
            import onnxruntime as ort

            digest = hashlib.sha256(MODEL.read_bytes()).hexdigest()
            if digest != SHA256:
                raise ValueError(f"{MODEL.name} is not the pinned model (sha256 {digest[:12]}...)")
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])
            grids, strides = [], []
            for stride in STRIDES:
                n = SIZE // stride
                xs, ys = np.meshgrid(np.arange(n), np.arange(n))
                grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
                strides.append(np.full((n * n, 1), stride))
            _state.update(session=session, grid=np.concatenate(grids).astype(np.float32),
                          strides=np.concatenate(strides).astype(np.float32))
            logger.info("person vision: %s loaded", MODEL.name)
        except Exception as exc:  # noqa: BLE001 -- no model is a refusal to walk, never a crash
            _state["failed"] = True
            logger.warning("person vision off: %s", exc)
    return _state["session"]


def enabled() -> bool:
    """The switch, with no question to the disk (the door camera asks this on every frame)."""
    return bool(settings.person_vision)


def available() -> bool:
    return enabled() and _load() is not None


def people(frame) -> list[Box]:
    """Person boxes in a BGR frame, best first, in the frame's own pixels. [] when unavailable."""
    session = _load() if enabled() else None
    if session is None:
        return []
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    ratio = min(SIZE / height, SIZE / width)
    small = cv2.resize(frame, (max(1, int(width * ratio)), max(1, int(height * ratio))), interpolation=cv2.INTER_LINEAR)
    pad = np.full((SIZE, SIZE, 3), FILL, dtype=np.uint8)
    pad[:small.shape[0], :small.shape[1]] = small
    x = pad.transpose(2, 0, 1)[None].astype(np.float32)
    try:
        raw = session.run(None, {session.get_inputs()[0].name: x})[0][0]
    except Exception:  # noqa: BLE001 -- one frame lost
        logger.warning("person vision: a frame failed", exc_info=True)
        return []
    floor = float(settings.person_vision_score)
    scores = raw[:, 4] * raw[:, 5 + PERSON]
    keep = scores >= floor
    if not keep.any():
        return []
    centres = (raw[keep, :2] + _state["grid"][keep]) * _state["strides"][keep]
    sizes = np.exp(raw[keep, 2:4]) * _state["strides"][keep]
    corners = np.concatenate((centres - sizes / 2.0, sizes), 1)
    chosen = cv2.dnn.NMSBoxes(corners.tolist(), scores[keep].tolist(), floor, NMS_IOU)
    found = []
    for i in np.array(chosen).reshape(-1):
        x0, y0, w, h = corners[i] / ratio
        found.append(Box(max(0, int(x0)), max(0, int(y0)), min(width, int(x0 + w)), min(height, int(y0 + h)),
                         float(scores[keep][i])))
    return sorted(found, key=lambda b: -b.score)
