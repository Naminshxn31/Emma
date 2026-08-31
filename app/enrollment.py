"""
The rules for putting a face into the gallery, in one place.

Two programs enrol people — the command line (`scripts/enroll_face.py`) and
the enrolment station (`scripts/enroll_app.py`) — and they must not each
have their own idea of when a batch is trustworthy. This project has the
scar: four separate places decided what to do when a call ended, all four
were bugs, and the fix was one `goToSleep()`. The comment there is worth
repeating here — *having a single copy is the reason the other exits stopped
existing as bugs*.

So the two front ends own the camera and the typing, and nothing else. Every
answer to "is this a face", "is this the same person", "is this somebody
already in the gallery under a different name" and "where does it go on
disk" is here.

Nothing in this module writes until it has been asked to. `save_shots` is
the only function that touches the filesystem, and by then all three
refusals have already had their chance.
"""
from __future__ import annotations

import logging
import math
import shutil
from datetime import datetime
from pathlib import Path

import numpy as np

from app import faces
from app.config import settings

logger = logging.getLogger("condo_voice.enrollment")

ENROLLED = Path(__file__).resolve().parent.parent / "data" / "faces" / "enrolled"

#: Margin around the detected box, as a fraction of its longest side. Kept
#: generous so a re-embed with a future model has hair, chin and ears to
#: work with rather than the tight crop this one happened to want.
CROP_MARGIN = 0.45

#: How alike a batch of shots of one person has to be. Below this the
#: capture caught more than one face over its few seconds, and averaging
#: that into a name is how a gallery quietly rots.
AGREEMENT_FLOOR = 0.45


def crop_face(image: np.ndarray, face: faces.Face) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = face.bbox
    pad = int(CROP_MARGIN * max(x2 - x1, y2 - y1))
    return image[max(0, y1 - pad):min(h, y2 + pad),
                 max(0, x1 - pad):min(w, x2 + pad)]


def portrait_min_px() -> int:
    """How wide a face must be to become an enrolment photograph.

    Not `settings.face_min_px`. That knob is the *greeting* range, and it
    was deliberately lowered to 50 so the doorway wakes the robot — at
    which point the station quietly started accepting 62px portraits,
    because both read the same setting. A far-away greeting degrades one
    sentence; a far-away portrait degrades every match that person ever
    gets. The two knobs move in different directions, so the portrait bar
    has a floor the range knob cannot pull down.
    """
    return max(settings.face_min_px, 110)


def usable_face(image: np.ndarray, min_px: int | None = None) -> tuple[faces.Face | None, str]:
    """(the one face worth enrolling, why not).

    The single place that decides whether a frame is a portrait of somebody.
    Both front ends show the reason to whoever is standing in front of the
    lens, so the strings are the user interface, not debug output.
    """
    min_px = portrait_min_px() if min_px is None else min_px
    found = faces.locate(image)
    if not found:
        return None, "no face"
    if len(found) > 1:
        biggest, runner_up = found[0].area, found[1].area
        if runner_up <= 0 or biggest < faces.DOMINANT_RATIO * runner_up:
            return None, f"{len(found)} faces - only one person please"
    face = found[0]
    width = face.bbox[2] - face.bbox[0]
    if width < min_px:
        return None, "come closer"
    if face.vec is None and faces.describe(image, face) is None:
        return None, "could not read the face"
    return face, ""


#: Frontal. Measured over 163 portraits from the studio and phone sessions:
#: yaw median -0.014, with 90% of them inside ±0.127 of it. So this band is
#: "the head is not turned", derived from faces rather than picked.
POSE_FRONTAL = 0.13

#: Turned enough to be a different view. Comfortably outside the frontal
#: spread above, and reachable without anyone having to perform.
POSE_TURNED = 0.20

#: Two shots closer than this are the same photograph twice. Five frames of
#: somebody holding still are all about 0.99 alike and carry the
#: information of one — which is the real answer to "are five shots
#: enough": it depends entirely on whether they are five *different* shots,
#: so the station refuses the ones that add nothing instead of counting to
#: five.
NOVELTY_CEILING = 0.97

#: Below this, the batch genuinely covers more than one view of the face.
#: Advisory: it is reported, not enforced, because a person who cannot turn
#: their head still has to be enrolable.
SPREAD_GOOD = 0.92


def head_pose(face: faces.Face) -> tuple[float, float]:
    """(yaw, pitch) from the five landmarks the detector already produced.

    Yaw is the nose's offset from the midpoint of the eyes, in eye-widths.
    Negative means the face points toward the left of the image; on an
    unmirrored camera that is the subject turning to their own right.

    Pitch is returned for diagnostics and **is not used to gate anything**.
    Measured across those same 163 frontal portraits it ranges from 0.41 to
    0.83 — three times the spread of yaw, on faces that are all facing the
    camera. It is reading how far the nose sits between the eyes and the
    mouth, which is a fact about a person's face, not about where their
    head is pointing. A "look up" step built on it would pass long-faced
    people before they moved and never pass short-faced ones at all.
    """
    kps = np.asarray(face.kps, dtype=float)
    eye = (kps[0] + kps[1]) / 2.0
    mouth = (kps[3] + kps[4]) / 2.0
    eye_width = float(np.linalg.norm(kps[1] - kps[0])) or 1.0
    span = float(mouth[1] - eye[1]) or 1.0
    return float((kps[2][0] - eye[0]) / eye_width), float((kps[2][1] - eye[1]) / span)


def is_novel(vec: np.ndarray, existing: list[np.ndarray]) -> bool:
    """Whether this shot carries anything the batch does not already have."""
    if not existing:
        return True
    return float(np.max(np.stack(existing) @ vec.astype(np.float32))) < NOVELTY_CEILING


def shots_agree(vecs: list[np.ndarray]) -> float:
    """The least alike pair in the batch. 1.0 for a single shot.

    The worst pair, not the average: one bad shot among four good ones
    still means the batch cannot be trusted, and an average hides exactly
    that.
    """
    if len(vecs) < 2:
        return 1.0
    stack = np.stack(vecs)
    pairs = stack @ stack.T
    return float(pairs[~np.eye(len(vecs), dtype=bool)].min())


def check_against_gallery(vec: np.ndarray, name: str) -> str | None:
    """A reason to refuse, or None.

    Only *other* names are a problem. Matching the person's own existing
    entries is the expected outcome and the whole point of a second angle.
    """
    path = Path(settings.face_gallery).expanduser()
    if not path.is_file():
        return None
    try:
        gallery = faces.Gallery.load(path)
    except ValueError:
        # Built by another model. It cannot judge this vector, so it does
        # not get a vote — `build_face_gallery.py` will rebuild it anyway.
        return None
    if not len(gallery):
        return None
    sims = gallery.vecs @ vec.astype(np.float32)
    for i in np.argsort(-sims)[:5]:
        other = gallery.names[int(i)]
        score = float(sims[int(i)])
        if score < settings.face_threshold:
            break
        if other != name:
            return (f"this face is already in the gallery as {other!r} "
                    f"({score:+.3f}). Enrolling it as {name!r} would put one "
                    f"person under two names, and the robot would greet them "
                    f"with whichever vector happens to be nearer.")
    return None


#: The guided capture, in order. `axis` is what the step wants varied and
#: `side` is which way, for the two that use yaw. Deliberately short: this
#: is a queue at a desk, not a phone setup screen somebody does once alone.
#:
#: There is no up/down step. See `head_pose` — pitch measured on this data
#: describes faces, not head positions.
STEPS = [
    {"key": "front", "axis": "yaw", "side": 0,
     "prompt": "มองตรงมาที่กล้อง"},
    {"key": "side_a", "axis": "yaw", "side": -1,
     "prompt": "หันหน้าไปทางขวาของคุณช้าๆ"},
    {"key": "side_b", "axis": "yaw", "side": +1,
     "prompt": "หันไปอีกด้านหนึ่ง"},
    {"key": "far", "axis": "distance", "side": 0,
     "prompt": "ถอยห่างออกไปสักก้าว"},
    {"key": "front2", "axis": "yaw", "side": 0, "hold_ms": 2500,
     "prompt": "กลับมามองตรง แล้วยิ้มค้างไว้"},
]

#: How long the pose has to hold before the shot is taken. Long enough that
#: walking past the lens cannot trigger it, short enough that nobody stands
#: there wondering.
HOLD_MS = 600

#: ...except where the step asks for something this pipeline cannot see.
#: `faces.py` loads detection and recognition only, so nobody checks the
#: smile in the last step — the shot fires the instant the face is frontal,
#: which on the owner's own enrolment was *before the smile arrived*. The
#: answer is not a smile detector, it is time: a step whose instruction no
#: code can verify has to leave room for a person to obey it. The hold bar
#: on the page is what makes the wait legible instead of a hang.


def hold_ms(step: dict) -> int:
    return int(step.get("hold_ms", HOLD_MS))


#: What the ear gets where the eye gets a number.
#:
#: The screen can say "ตอนนี้ 155px ขอ 169px" — it is being read by whoever
#: is running the station. The person being enrolled is looking at a lens and
#: needs "come a bit closer". Two audiences, two sentences.
#:
#: Fixed strings, and that is the point: every sentence this station speaks
#: is rendered once and kept (`app/voice.py`), so a sentence assembled at
#: runtime is a sentence nobody has rendered — it arrives as silence at the
#: exact moment somebody needed telling.
SPOKEN = {
    "no_face": "ยังไม่เห็นหน้า",
    "crowd": "ทีละคนนะคะ",
    "closer": "เข้ามาใกล้อีกนิด",
    "unreadable": "อ่านใบหน้าไม่ได้",
    "done": "ครบแล้ว กดบันทึกได้",
}

#: How much smaller the face has to get for the "step back" shot to count.
#: The runtime meets people at whatever distance they stop at, and a
#: gallery built entirely at arm's length has only ever seen one scale.
FAR_RATIO = 0.75

#: How far the shrunken face still has to clear `FACE_MIN_PX`. Without this
#: the step asks for two contradictory things: `FAR_RATIO` says "smaller
#: than 0.75x what you started at" and `usable_face` says "no smaller than
#: FACE_MIN_PX", and when the first number lands under the second there is
#: no distance that satisfies both. That happened on a desk camera — the
#: sitter's face was 119px, the step wanted 89px, and the floor was 110px.
#: The person tried to obey an instruction that could not be obeyed.
FAR_MIN_MARGIN = 12


def far_target(reference_width: int, min_px: int | None = None) -> int | None:
    """How narrow the face has to get, or None when it cannot get there.

    None is a real answer, not a failure: a camera close enough that a step
    back would take the face under the floor simply has no distance shot to
    offer, and the honest thing is to skip that step rather than stand
    somebody in front of a lens repeating an impossible instruction.
    """
    min_px = portrait_min_px() if min_px is None else min_px
    target = int(reference_width * FAR_RATIO)
    return target if target >= min_px + FAR_MIN_MARGIN else None


def first_shot_min_px(min_px: int | None = None) -> int:
    """How wide the *first* portrait must be for the step-back to exist.

    The distance step wants FAR_RATIO of the first shot, and that shrunken
    face still has to clear the portrait floor by FAR_MIN_MARGIN. Solved
    the other way round: the first shot has to be at least this wide, or
    the step-back is impossible before anyone has tried it. Asking for it
    up front turns a skipped step ("หายไปหนึ่ง" — the owner's own words,
    looking at four thumbnails where five were promised) into one extra
    step toward the lens at the start.
    """
    min_px = portrait_min_px() if min_px is None else min_px
    return int(math.ceil((min_px + FAR_MIN_MARGIN) / FAR_RATIO))


def step_applies(step: dict, reference_width: int, min_px: int | None = None) -> bool:
    """Whether this step can be satisfied at all from where the camera is."""
    if step["axis"] != "distance":
        return True
    return far_target(reference_width, min_px) is not None


def step_ready(step: dict, yaw: float, width: int, reference_width: int,
               taken_sides: set[int]) -> tuple[bool, str]:
    """(is this shot the one the step is asking for, what to say if not).

    `taken_sides` carries which way the head has already been turned, so the
    second side step asks for the other one. That matters more than the
    labels: the prompt names a direction from a sign convention checked by
    eye against six photographs, and if that reading is backwards the two
    steps still cover both sides — the person turns one way, then the other,
    and the flow completes either way.
    """
    if step["axis"] == "distance":
        target = far_target(reference_width)
        if target is None:
            # Unreachable from here; the station skips it rather than asking.
            return False, "ข้ามข้อนี้ — กล้องอยู่ใกล้เกินกว่าจะถอยได้"
        if width > target:
            return False, f"{step['prompt']} (ตอนนี้ {width}px ขอ {target}px)"
        return True, ""

    if step["side"] == 0:
        if abs(yaw) > POSE_FRONTAL:
            return False, step["prompt"]
        return True, ""

    if abs(yaw) < POSE_TURNED:
        return False, step["prompt"]
    side = -1 if yaw < 0 else 1
    if side in taken_sides:
        return False, "หันไปอีกด้านหนึ่ง"
    return True, ""


def review(vecs: list[np.ndarray], name: str) -> str | None:
    """Everything that can refuse a batch, asked in order. None means fine."""
    worst = shots_agree(vecs)
    if worst < AGREEMENT_FLOOR:
        return (f"the shots do not all look like the same person "
                f"(lowest pair {worst:+.3f}) — somebody else was probably "
                f"in frame")
    return check_against_gallery(np.mean(vecs, axis=0), name)


def save_shots(name: str, crops: list[np.ndarray], replace: bool = False) -> int:
    """Write the crops for `name`. Returns how many that person now has.

    The only function here that touches the disk, and it is called last.
    """
    import cv2

    folder = ENROLLED / name
    if replace and folder.is_dir():
        shutil.rmtree(folder)
        logger.info("dropped the previous shots for %s", name)
    folder.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for i, crop in enumerate(crops, 1):
        # Names are only unique to the second, and at the enrolment station
        # two batches a second apart is a double-click. A collision here is
        # silent — the second save overwrites the first and reports a total
        # that never went up — so the name is walked forward until it is
        # free rather than trusted to be.
        out = folder / f"{stamp}_{i}.jpg"
        bump = 0
        while out.exists():
            bump += 1
            out = folder / f"{stamp}_{i}-{bump}.jpg"
        # `imencode` + tofile, not `imwrite`: the names are Thai and
        # OpenCV's writer hands the path to the ANSI code page. Same cp874
        # trap as `faces.read_image`, on the way out.
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            buf.tofile(str(out))
    return len(sorted(folder.glob("*.jpg")))


def enrolled() -> list[tuple[str, int]]:
    """(name, how many shots) for everybody with camera shots, by name."""
    if not ENROLLED.is_dir():
        return []
    return sorted((p.name, len(list(p.glob("*.jpg"))))
                  for p in ENROLLED.iterdir() if p.is_dir())
