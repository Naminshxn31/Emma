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

That now includes the guided capture itself (`Session`: which step is
next, whether this frame is the shot, what to say). It used to live in the
station, and then the call screen on the robot needed the same five steps
from a second server — a copy there would have been the fourth and fifth
exit again. The station and the server endpoint each hold a `Session`;
neither holds a rule.

Nothing in this module writes until it has been asked to. `save_shots`
writes the shots and `add_to_gallery` appends vectors to the live gallery,
both called last, after every refusal has had its chance.
"""
from __future__ import annotations

import base64
import logging
import math
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

import numpy as np

from app import faces
from app.config import settings

logger = logging.getLogger("condo_voice.enrollment")

ENROLLED = Path(__file__).resolve().parent.parent / "data" / "faces" / "enrolled"

#: Longest name accepted, in characters. A Thai first name and surname with
#: a space is well under twenty; forty leaves room without letting a pasted
#: paragraph become a folder.
NAME_MAX = 40

#: Names Windows will not create a file or folder under, whatever follows
#: the dot: `CON.txt` is still the console. Superscript digits included
#: because Windows reserves `COM¹`..`LPT³` too, and those are Unicode
#: digits that the character rule below lets through.
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{dev}{n}" for dev in ("COM", "LPT") for n in "0123456789¹²³"]
)

#: Punctuation a real name uses. Everything else outside letters, marks and
#: digits is refused rather than escaped, because the name is a folder.
_NAME_PUNCT = frozenset(" .-'")


def safe_person_name(raw: str) -> str:
    """The name, tidied, or ValueError with a reason to show the operator.

    The name becomes a folder under `ENROLLED`, and `save_shots` with
    `replace=True` deletes what was in that folder. While the only caller
    was the station on 127.0.0.1 a name was whatever the operator typed;
    the call screen puts the same save behind a LAN endpoint, and there
    `..\\..\\app` with replace is a delete in the application. So the rule
    is an allow-list (letters, marks, digits, space, `.-'`) — Thai needs
    the marks, and nothing that means "another directory" is a letter.

    Whitespace is stripped and collapsed so "ต้า  " and "ต้า" are one person,
    not two folders and two consent records. Control and invisible format
    characters (a pasted zero-width space) are refused, not collapsed: they
    make two names that look identical on screen.
    """
    name = (raw or "").strip()
    if not name:
        raise ValueError("ยังไม่ได้ใส่ชื่อ")
    if any(unicodedata.category(ch)[0] == "C" for ch in name):
        raise ValueError("ชื่อมีอักขระพิเศษที่มองไม่เห็น — พิมพ์ใหม่")
    name = " ".join(name.split())
    if len(name) > NAME_MAX:
        raise ValueError(f"ชื่อยาวเกิน {NAME_MAX} ตัวอักษร")
    if any(not (unicodedata.category(ch)[0] in "LMN" or ch in _NAME_PUNCT)
           for ch in name):
        raise ValueError("ชื่อใช้ได้เฉพาะตัวอักษร ตัวเลข เว้นวรรค และ . - '")
    if name[0] == "." or name[-1] == ".":
        # Leading: a hidden entry, and "." itself. Trailing: Windows drops
        # it, so "ต้า." and "ต้า" would be one folder under two names.
        raise ValueError("ชื่อขึ้นต้นหรือลงท้ายด้วยจุดไม่ได้")
    if ".." in name:
        raise ValueError("ชื่อมีจุดติดกันสองตัวไม่ได้")
    if name.split(".", 1)[0].rstrip().upper() in _RESERVED:
        raise ValueError("ชื่อนี้ Windows สงวนไว้ ใช้เป็นชื่อไม่ได้")
    return name


def _person_folder(name: str) -> Path:
    """`ENROLLED / name`, proven to be directly inside `ENROLLED`.

    `safe_person_name` already refuses every separator, so this is the
    second lock on the same door: it checks where the path *resolves*,
    which also catches a folder that is really a junction pointing
    somewhere else. It is what stands between `replace=True` and a delete
    outside `data/` — and, for the station's `existing_ok`, the only lock
    on a name `safe_person_name` never saw.
    """
    root = ENROLLED.resolve()
    folder = (ENROLLED / name).resolve()
    if folder.parent != root:
        raise ValueError("ชื่อนี้ใช้เป็นโฟลเดอร์ไม่ได้")
    return ENROLLED / name


def _existing_folder_name(raw: str) -> str | None:
    """`raw` (stripped) when it is exactly the name of a folder already
    directly inside `ENROLLED`, else None.

    For the station only (`existing_ok`). Before `safe_person_name` the
    station filed whatever the operator typed, so a folder like `Tom_2` or
    a 41-letter Thai name can exist — and the new rule refused the station
    its own person: the exact name failed the allow-list, and the tidied
    one ("Tom 2") was refused by `check_against_gallery` as a *different*
    person with the same face (review, 2026-09-29). An existing folder is
    somebody the station already filed, and topping them up is its job.

    Exact spelling, compared against the listing — not `is_dir()`, which
    on Windows would let "tom_2" into "Tom_2" and record consent under a
    name nobody is greeted by. `_person_folder` still has to agree that it
    resolves directly inside `ENROLLED`, so "..", a nested path or a
    junction pointing elsewhere is refused exactly as before.
    """
    name = (raw or "").strip()
    if not name or any(unicodedata.category(ch)[0] == "C" for ch in name):
        return None
    try:
        if not ENROLLED.is_dir() or name not in {p.name for p in ENROLLED.iterdir()
                                                 if p.is_dir()}:
            return None
        _person_folder(name)
    except (ValueError, OSError):
        return None
    return name


def checked_name(raw: str, existing_ok: bool = False) -> str:
    """`safe_person_name` — or, only with `existing_ok`, the exact name of a
    folder that already exists (see `_existing_folder_name`).

    The existing folder is asked first, not only when the rule refuses:
    "ทด  สอบ" passes the rule as the tidied "ทด สอบ", which is a new folder
    and, to the gallery check, a second name for the same face.

    The call screen never passes `existing_ok`: a LAN screen must not add
    shots to anybody who already exists, and it refuses taken names before
    it gets here anyway.
    """
    if existing_ok:
        legacy = _existing_folder_name(raw)
        if legacy is not None:
            return legacy
    return safe_person_name(raw)

#: Margin around the detected box, as a fraction of its longest side. Kept
#: generous so a re-embed with a future model has hair, chin and ears to
#: work with rather than the tight crop this one happened to want.
CROP_MARGIN = 0.45

#: How alike a batch of shots of one person has to be. Below this the
#: capture caught more than one face over its few seconds, and averaging
#: that into a name is how a gallery quietly rots.
#: No longer what `review` refuses on — see LINK_FLOOR. Kept for the
#: offline CLI (scripts/enroll_face.py) and the spread readout.
AGREEMENT_FLOOR = 0.45

#: What `review` refuses a batch on: the batch must hang together as ONE
#: group — every shot reachable from every other through links at least this
#: alike (`batch_link`). The worst-pair test it replaces asked the wrong
#: question: the five poses are MEANT to differ, and a frontal shot against
#: a turned one is the least alike pair of a perfectly good batch. Measured
#: 2026-09-29 on the station's own batches in the gallery (4 people, 5 shots):
#:     same person        worst pair .442-.574   link .558-.690
#:     1-2 shots swapped  worst pair up to .446  link <= .438
#: The worst pair overlaps (it refused one genuine batch at .442 and would
#: pass a mixed one at .446); the link does not. The owner hit exactly that
#: on the robot's camera the same day: one person, five poses, refused as
#: "not the same person".
#: First set at .50 (the middle of that gap) — and the robot refused the
#: owner's colleague again at link .489 (worst pair .331): its camera is
#: poorer than the PC's the gap was measured on, so the same person scores
#: lower there. Now .45: above every mixed batch measured (.438), below the
#: robot's genuine one. Thin on the mixed side, so every save logs its whole
#: pair table ("enrolment batch: ...", numbers only) — retune from those
#: lines, not from this comment.
LINK_FLOOR = 0.45


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


def batch_link(vecs: list[np.ndarray]) -> float:
    """How strongly the batch holds together as one group. 1.0 for one shot.

    The weakest link of the maximum spanning tree over the shots: the highest
    similarity floor at which every shot is still reachable from every other.
    A turned head only has to look like ONE other shot (its neighbour pose);
    a second person's shots look like nobody else's, so they fall off —
    whether one shot of them or two (two of the same stranger still link to
    each other, but not to the rest).
    """
    if len(vecs) < 2:
        return 1.0
    stack = np.stack([np.asarray(v, dtype=np.float32) for v in vecs])
    pairs = stack @ stack.T
    inside, weakest = [0], 1.0
    outside = list(range(1, len(vecs)))
    while outside:
        best, pick = max((pairs[a, b], b) for a in inside for b in outside)
        weakest = min(weakest, float(best))
        inside.append(pick)
        outside.remove(pick)
    return weakest


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


def _log_batch(keys: list[str], vecs: list[np.ndarray]) -> None:
    """One line per save: the batch link and every pair's similarity, by pose.
    No name — this goes to the console log."""
    if len(vecs) < 2:
        return
    stack = np.stack([np.asarray(v, dtype=np.float32) for v in vecs])
    sims = stack @ stack.T
    pairs = " ".join(f"{keys[a]}/{keys[b]}={sims[a, b]:.2f}"
                     for a in range(len(vecs)) for b in range(a + 1, len(vecs)))
    logger.info("enrolment batch: link %.3f (floor %.2f) | %s",
                batch_link(vecs), LINK_FLOOR, pairs)


def review(vecs: list[np.ndarray], name: str) -> str | None:
    """Everything that can refuse a batch, asked in order. None means fine."""
    link = batch_link(vecs)
    if link < LINK_FLOOR:
        # Logged with the numbers and no name: the call screen shows only a
        # Thai sentence, and "why was this refused" must not need a rerun.
        logger.info("enrolment refused: batch link %.3f < %.2f (worst pair %.3f, %d shots)",
                    link, LINK_FLOOR, shots_agree(vecs), len(vecs))
        return (f"the shots do not all look like the same person "
                f"(weakest link {link:+.3f}) — somebody else was probably "
                f"in frame")
    # Shot by shot, not the batch mean. Recognition judges one frame at a
    # time, and one front-on frame within FACE_MARGIN of another name is
    # enough to keep that person from ever being named. The mean let the
    # side poses dilute exactly that: a robot scan of a colleague whose
    # studio portrait was already enrolled under the Thai spelling of her
    # name matched it at 0.507/0.546 front-on, averaged 0.45, passed — and
    # 23 of the next 27 "recognised but not named" lines were her (merged
    # 2026-10-08).
    for vec in vecs:
        reason = check_against_gallery(np.asarray(vec, dtype=np.float32), name)
        if reason:
            return reason
    return None


def _undo_shots(mine: list[Path], folder: Path, created: bool) -> None:
    """Remove the shot files one `save_shots` call wrote, and the folder if
    that call made it and nothing else is in it now.

    Only these files: a station top-up that fails must not cost the person
    the shots they already had. Best effort — a file somebody else holds
    open stays, and says so in the log; the error that got us here is the
    one reported.
    """
    for path in mine:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("could not remove %s after a failed save: %s", path.name, exc)
    if created:
        try:
            folder.rmdir()
        except OSError:
            pass                            # not empty, or held — leave it


def save_shots(name: str, crops: list[np.ndarray], replace: bool = False,
               written: list | None = None, consent_version: str | None = None,
               existing_ok: bool = False) -> int:
    """Write the crops for `name`. Returns how many that person now has.

    Called last, after every refusal. The name goes through `checked_name`
    and the folder through `_person_folder` *before* anything is created or
    deleted — see those two for why that order is the whole point.
    `written`, when given, collects the path of every shot file this call
    wrote, in crop order, so a caller can say which photo each vector came
    from. `consent_version` is what the record is stamped with (default
    `consent.VERSION`); the call screen's scan passes
    `consent.STAFF_ATTESTED` when it showed no consent text. `existing_ok`
    is the station's: see `checked_name`.

    All or nothing, as far as files allow. The shots are written first and
    the consent record last, so anything failing in between (a disk that
    fills, a people.json another program holds for over a second, one that
    cannot be read) used to leave shots with no record — and a folder that
    made the retry "ชื่อนี้มีอยู่แล้ว", so the operator typed a second name
    and one face was filed twice (review, 2026-09-29). Now a failure before
    the record is written removes the files this call wrote (and the folder
    if this call made it) and raises. `replace` deletes the previous shots
    only *after* the record is written, for the same reason: a failed
    replace must not have already thrown the old ones away.
    """
    import cv2

    name = checked_name(name, existing_ok)
    folder = _person_folder(name)
    created = not folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)

    mine: list[Path] = []
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for i, crop in enumerate(crops, 1):
            # Names are only unique to the second, and at the enrolment
            # station two batches a second apart is a double-click. A
            # collision here is silent — the second save overwrites the
            # first and reports a total that never went up — so the name is
            # walked forward until it is free rather than trusted to be.
            out = folder / f"{stamp}_{i}.jpg"
            bump = 0
            while out.exists():
                bump += 1
                out = folder / f"{stamp}_{i}-{bump}.jpg"
            # `imencode` + tofile, not `imwrite`: the names are Thai and
            # OpenCV's writer hands the path to the ANSI code page. Same
            # cp874 trap as `faces.read_image`, on the way out.
            ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                # Claimed before the write: a tofile that dies halfway
                # leaves half a JPEG under a name that was free a moment
                # ago, and that is this call's file to remove too.
                mine.append(out)
                buf.tofile(str(out))
                if written is not None:
                    written.append(out)
            else:
                logger.warning("shot %d for %s could not be encoded — not saved", i, name)
        # Standing in front of the camera, taking five shots of yourself and
        # pressing save is consent to be recognised — the only kind of
        # consent this system can actually witness. Recorded here, in the
        # one function that writes shots, so the station and the CLI cannot
        # disagree.
        from app import consent

        consent.record(name, by=name, version=consent_version or consent.VERSION)
    except BaseException:
        _undo_shots(mine, folder, created)
        if written is not None:
            written[:] = [p for p in written if p not in mine]
        raise

    if replace:
        # Files only, and never an exception from here: the save has
        # happened (shots and record), and a "not saved" now would be the
        # second-save trap again. An old shot somebody holds open stays and
        # is counted.
        keep = set(mine)
        dropped = 0
        for old in list(folder.iterdir()):
            if old in keep or not old.is_file():
                continue
            try:
                old.unlink()
                dropped += 1
            except OSError as exc:
                logger.warning("could not drop the previous shot %s: %s", old.name, exc)
        if dropped:
            logger.info("dropped the previous shots for %s", name)
    return len(sorted(folder.glob("*.jpg")))


def enrolled() -> list[tuple[str, int]]:
    """(name, how many shots) for everybody with camera shots, by name."""
    if not ENROLLED.is_dir():
        return []
    return sorted((p.name, len(list(p.glob("*.jpg"))))
                  for p in ENROLLED.iterdir() if p.is_dir())


# -- straight into the live gallery ------------------------------------------

#: Around the gallery's load-append-save. Two kiosks finishing an enrolment
#: in the same second would each load the file, append their own person and
#: save, and the second save would drop the first person. Atomic replace
#: stops torn files, not lost updates. In-process only — the rebuild is a
#: separate process, which is what `faces.file_lock` (taken inside this) is
#: for.
_gallery_lock = threading.Lock()

#: How long an append waits for the gallery's cross-process lock. The one
#: other holder is `build_face_gallery.py`, which keeps it for its whole
#: run (tens of seconds to minutes), so waiting it out inside a save
#: request is not an option: a short wait, then the refusal below, reported
#: beside a save that did happen.
GALLERY_LOCK_WAIT_S = 3.0
_GALLERY_BUSY = ("ตอนนี้กำลังสร้างแกลเลอรีใหม่อยู่ (build-face-gallery) จึงยังเพิ่มเข้าแกลเลอรีไม่ได้ — "
                 "รอให้เสร็จแล้วรัน build-face-gallery.cmd อีกครั้ง หุ่นจึงจะจำคนนี้ได้")

#: `Session.save`'s answer when the disk refused the shots or the record
#: and `save_shots` rolled back. Nothing was kept, so it says so — and the
#: retry is a real one.
_SAVE_WRITE_FAILED = ("บันทึกไม่สำเร็จ — เขียนไฟล์ไม่ได้ (ดิสก์เต็มหรือไฟล์ถูกโปรแกรมอื่น"
                      "เปิดค้าง) ยังไม่มีอะไรถูกบันทึก ลองกดบันทึกอีกครั้ง")

#: `_join_gallery`'s answer to a write the disk refused. Thai, with no
#: path and no name in it: it reaches the scan screen as it is.
_GALLERY_WRITE_FAILED = ("บันทึกภาพแล้ว แต่เขียนไฟล์แกลเลอรีไม่ได้ (ไฟล์ถูกโปรแกรมอื่นเปิดค้าง"
                         "หรือดิสก์เต็ม) — ให้พนักงานรัน build-face-gallery.cmd")


def add_to_gallery(name: str, vecs: list[np.ndarray], photos: list[str]) -> int:
    """Append `name`'s vectors to the gallery file. Returns how many that
    name now has there.

    Enrolling and rebuilding used to be two steps on purpose (see
    `greeter` — "enrolled but not in the gallery"), and the station keeps
    them that way. The call screen cannot: the person it enrols is standing
    there waiting to be recognised, and "someone will run a script later"
    is not an answer at a desk. So this appends in place — the same meta a
    rebuild would write for a camera shot, so the next full rebuild
    replaces these rows with identical ones rather than different ones.

    Refuses (ValueError) rather than guesses:
    - a gallery built by another model, or one that cannot be read, is
      never overwritten — rebuilding it is a decision with its own command,
      and overwriting would silently delete everybody else in it;
    - a name whose consent is revoked or expired — the rebuild keeps those
      vectors out of the gallery, so appending them here would be the one
      path that puts them back; and a people.json that cannot be read,
      which might be exactly where that revocation is;
    - a gallery `build_face_gallery.py` is rewriting right now
      (`faces.LockBusy`): appended into the file it is about to replace,
      this person would be gone the moment the rebuild finished — after
      the screen had said the robot knows them (review, 2026-09-29).
    """
    from app import consent

    name = safe_person_name(name)
    if not vecs or len(vecs) != len(photos):
        raise ValueError(f"{len(vecs)} vectors and {len(photos)} photos do not line up")
    state = consent.status(name)
    if state in (consent.REVOKED, consent.EXPIRED):
        raise ValueError("ชื่อนี้ถอนความยินยอมแล้วหรือหมดอายุ — เพิ่มเข้าแกลเลอรีไม่ได้")
    if state == consent.UNREADABLE:
        raise ValueError("อ่านไฟล์ความยินยอม (people.json) ไม่ได้ — ยังไม่เพิ่มเข้าแกลเลอรี "
                         "ให้ผู้ดูแลแก้ไฟล์ก่อน")

    path = Path(settings.face_gallery).expanduser()
    stack = np.stack([np.asarray(v, dtype=np.float32) for v in vecs])
    with _gallery_lock, faces.file_lock(path, GALLERY_LOCK_WAIT_S, _GALLERY_BUSY):
        if path.is_file():
            try:
                current = faces.Gallery.load(path)
            except ValueError as exc:
                logger.warning("not appending to %s: %s", path, exc)
                raise ValueError("แกลเลอรีเดิมอ่านไม่ได้หรือสร้างจากโมเดลอื่น — "
                                 "ไม่เขียนทับ ให้รัน build-face-gallery.cmd ก่อน") from exc
        else:
            current = faces.Gallery([], np.zeros((0, stack.shape[1]), np.float32))
        # Same rule as the rebuild's camera shots: a colleague enrolled
        # from the camera is still a colleague, or the greeting depends on
        # which vector happened to be nearest.
        group = ("staff" if any(n == name and m.get("group") == "staff"
                                for n, m in zip(current.names, current.meta))
                 else "enrolled")
        metas = [{"group": group, "photo": photo, "source": "camera", "consent": state}
                 for photo in photos]
        updated = current.appended([name] * len(stack), stack, metas)
        updated.save(path)
    return sum(1 for n in updated.names if n == name)


def _join_gallery(name: str, vecs: list[np.ndarray], written: list[Path]) -> dict:
    """`add_to_gallery` for a batch `save_shots` just wrote, as result fields.

    The shots are already on disk and the consent is recorded by the time
    this runs, so a refusal here is reported beside a successful save, not
    instead of one — telling the page "not saved" would invite a second
    save of the same person.

    That includes a write the disk refuses (OSError: a gallery.npz held
    open past `write_atomically`'s retries, a full disk). It used to escape
    as a 500 "ลองใหม่", and the retry met "ชื่อนี้มีอยู่แล้ว" for a person
    who was in fact saved and consented (review, 2026-09-29).
    """
    photos = [f"enrolled/{name}/{p.name}" for p in written]
    if len(photos) != len(vecs):
        # A shot that failed to encode is missing from `written`, and then
        # which file belongs to which vector is unknown. Unknown is written
        # as "", never as the neighbour's photo.
        photos = [""] * len(vecs)
    try:
        return {"gallery_total": add_to_gallery(name, vecs, photos)}
    except ValueError as exc:
        return {"gallery_total": None, "gallery_reason": str(exc)}
    except OSError as exc:
        logger.warning("saved %s but could not append to the gallery: %s", name, exc)
        return {"gallery_total": None, "gallery_reason": _GALLERY_WRITE_FAILED}


# -- the guided capture, shared by every front end ---------------------------

def decode_data_url(data_url: str) -> np.ndarray | None:
    """A browser canvas's `data:image/...;base64,...` as BGR, or None.

    None for anything that is not an image, never an exception: this is
    the first thing a request body reaches, and once it is a LAN endpoint
    an empty or garbage body is the likeliest thing to arrive.
    """
    import cv2

    if not isinstance(data_url, str):
        return None
    _, _, payload = data_url.partition(",")
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None
    if not raw:
        # cv2.imdecode raises on an empty buffer instead of returning None.
        return None
    try:
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        return None


def thumbnail(crop: np.ndarray, size: int = 160) -> str:
    """A small JPEG of a shot as a data URL, for the row of taken shots."""
    import cv2

    h, w = crop.shape[:2]
    scale = size / max(h, w, 1)
    small = cv2.resize(crop, (max(1, int(w * scale)), max(1, int(h * scale))))
    ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else ""


def scale_to_camera(frame: np.ndarray, full_width) -> float:
    """How many camera pixels one analysed pixel is worth.

    The preview loop sends a 640-wide copy because a box is all it needs;
    a capture sends the whole frame. Measuring face width on each of those
    and comparing the two numbers to the same thresholds was wrong twice
    over: `FACE_MIN_PX` silently doubled during the preview, so people had
    to sit far closer than the runtime will ever require, and the distance
    step compared a 640-scale width against a reference stored at full
    scale — the screen said 155px while the instruction said 307px, about
    the same face in the same second.

    So every width this module reasons about is in camera pixels. A width
    that is missing or not a usable number is "not told", and nothing
    invents a scale it was not told about.
    """
    try:
        full = float(full_width) if full_width else 0.0
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(full) or full <= 0 or frame.shape[1] <= 0:
        return 1.0
    return full / float(frame.shape[1])


class Session:
    """One person being enrolled: the shots taken so far, and what to ask next.

    Everything the preview needs is decided here rather than in a page. The
    browser owns the camera and the drawing; it does not own any rule about
    whether a shot counts. One `Session` per front end — the station holds
    one, the server endpoint holds one — and neither front end holds a
    step, a threshold or a sentence.

    The pending list is the batch: one operator, one person in front of the
    lens. Cleared on save and on discard, never carried to the next name.
    """

    def __init__(self, pending: list[dict] | None = None):
        self._lock = threading.Lock()
        #: Held for the whole of `save`. A double-tap is two saves of one
        #: batch — ten files and every vector in the gallery twice. With
        #: this, the second call waits, then finds nothing pending.
        self._save_lock = threading.Lock()
        #: Used as given, so a test can seed a batch and watch it clear.
        self._pending: list[dict] = [] if pending is None else pending

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # -- helpers below expect `self._lock` held ---------------------------

    def _reference_width(self) -> int:
        """The face width from the first shot. 0 before there is one."""
        return self._pending[0]["width"] if self._pending else 0

    def _remaining(self) -> list[dict]:
        """Steps not taken yet, minus the ones this camera cannot offer.

        A step nobody can satisfy is not a step. The distance shot needs
        room to step back into, and on a desk camera there is none: the
        sitter's face was 119px, the step wanted 89px, and `FACE_MIN_PX`
        refuses anything under 110px. Somebody stood there trying to obey an
        instruction that had no answer, which is worse than not asking.
        """
        done = {p.get("step") for p in self._pending}
        reference = self._reference_width()
        return [s for s in STEPS
                if s["key"] not in done
                and (not reference or step_applies(s, reference))]

    def _current_step(self) -> dict | None:
        todo = self._remaining()
        return todo[0] if todo else None

    def _state_locked(self) -> dict:
        done = {p.get("step") for p in self._pending}
        reference = self._reference_width()
        steps = []
        for step in STEPS:
            skipped = bool(reference) and not step_applies(step, reference)
            steps.append({
                "key": step["key"], "done": step["key"] in done, "skipped": skipped,
                "prompt": step["prompt"] + (" — ข้าม (กล้องใกล้เกินไป)" if skipped else ""),
            })
        todo = self._remaining()
        # The hold time belongs to the step, not to the page: the step that
        # asks for a smile is the one nothing here can check, and the page
        # has no way to know which one that is.
        return {"steps": steps, "step": todo[0]["key"] if todo else None,
                "hold_ms": hold_ms(todo[0]) if todo else HOLD_MS,
                "prompt": todo[0]["prompt"] if todo else "ครบแล้ว — กดบันทึกได้"}

    # -- what the front ends call ------------------------------------------

    def state(self) -> dict:
        """Which step this is on, and what has been taken so far."""
        with self._lock:
            return self._state_locked()

    def judge(self, frame: np.ndarray, full_width=None) -> dict:
        """One preview frame: where the face is and whether it is the shot."""
        # Outside the lock: detection is the slow part, and a capture must
        # not queue behind a preview frame.
        found = faces.locate(frame)
        scale = scale_to_camera(frame, full_width)
        with self._lock:
            state = self._state_locked()
            first = not self._pending
            step = self._current_step()
            sides = {p.get("side") for p in self._pending if p.get("side")}
            reference = self._reference_width()

        base = {"box": None, "width": 0, "yaw": 0.0,
                "frame": [int(frame.shape[1]), int(frame.shape[0])]}
        if not found:
            return {**base, "ok": False, "reason": "ยังไม่เห็นหน้า",
                    "say": SPOKEN["no_face"], **state}

        face = found[0]
        width = int(round((face.bbox[2] - face.bbox[0]) * scale))
        base.update(box=list(face.bbox), width=width)

        if len(found) > 1:
            runner_up = found[1].area
            if runner_up > 0 and face.area < faces.DOMINANT_RATIO * runner_up:
                return {**base, "ok": False,
                        "reason": f"มี {len(found)} หน้าในภาพ — ทีละคน",
                        "say": SPOKEN["crowd"], **state}
        if width < portrait_min_px():
            return {**base, "ok": False,
                    "reason": f"เข้ามาใกล้อีกนิด ({width}px ขอ {portrait_min_px()}px)",
                    "say": SPOKEN["closer"], **state}
        if first and width < first_shot_min_px():
            # The first shot sets the scale every later step is measured
            # against, so it is the one place to insist on room: a first
            # portrait this small has no step-back, and the station used to
            # find that out only after promising five steps and delivering
            # four. One more step toward the lens now is the whole fix.
            return {**base, "ok": False,
                    "reason": f"เข้ามาใกล้อีกนิด ({width}px ขอ "
                              f"{first_shot_min_px()}px เผื่อท่าถอยหลัง)",
                    "say": SPOKEN["closer"], **state}
        if face.kps is None:
            return {**base, "ok": False, "reason": "อ่านใบหน้าไม่ได้",
                    "say": SPOKEN["unreadable"], **state}

        yaw, _pitch = head_pose(face)
        base["yaw"] = round(yaw, 3)

        if step is None:
            return {**base, "ok": False, "reason": "ครบแล้ว — กดบันทึกได้",
                    "say": SPOKEN["done"], **state}

        ready, why = step_ready(step, yaw, width, reference or width, sides)
        # What to say is the instruction itself, never the version of it
        # carrying pixel counts: those are for whoever is running the station.
        return {**base, "ok": ready, "reason": "" if ready else why,
                "say": "" if ready else step["prompt"], **state}

    def capture(self, frame: np.ndarray, full_width=None) -> dict:
        """Take the shot the current step asks for, if this frame is it."""
        scale = scale_to_camera(frame, full_width)
        with self._lock:
            first = not self._pending
        need = first_shot_min_px() if first else portrait_min_px()
        face, why = usable_face(frame, min_px=need / scale)
        if face is None:
            return {"ok": False, "reason": why, **self.state()}
        yaw, _ = head_pose(face)
        width = int(round((face.bbox[2] - face.bbox[0]) * scale))

        with self._lock:
            step = self._current_step()
            if step is None:
                return {"ok": False, "reason": "ครบแล้ว — กดบันทึกได้",
                        **self._state_locked()}
            sides = {p.get("side") for p in self._pending if p.get("side")}
            reference = self._reference_width() or width
            ready, why = step_ready(step, yaw, width, reference, sides)
            if not ready:
                return {"ok": False, "reason": why, **self._state_locked()}
            # A shot that the batch already has teaches the gallery nothing,
            # and five of those are one photograph counted five times. Held
            # to the same standard whether the operator pressed the button
            # or the hold timer fired.
            if not is_novel(face.vec, [p["vec"] for p in self._pending]):
                return {"ok": False, "reason": "ภาพนี้เหมือนใบเดิม ลองขยับอีกนิด",
                        **self._state_locked()}
            self._pending.append({"vec": face.vec, "crop": crop_face(frame, face),
                                  "step": step["key"], "width": width,
                                  "side": 0 if step["side"] == 0 else (-1 if yaw < 0 else 1)})
            taken = [(p["crop"], p["step"]) for p in self._pending]
            spread = shots_agree([p["vec"] for p in self._pending])
            state = self._state_locked()
        shots = [{"thumb": thumbnail(crop), "step": key} for crop, key in taken]
        return {"ok": True, "shots": shots, "spread": round(spread, 3), **state}

    def save(self, name: str, replace: bool = False, add_to_gallery: bool = False,
             consent_version: str | None = None, existing_ok: bool = False) -> dict:
        """Refuse, or write the batch under `name` and start the next person.

        `add_to_gallery` appends the batch's vectors to the live gallery as
        well (see the module function of that name). The station leaves it
        off and keeps its documented rebuild step; the call screen turns it
        on because the person is waiting to be recognised.
        `consent_version` goes to `save_shots` unchanged; so does
        `existing_ok`, which only the loopback station passes (see
        `checked_name`).
        """
        with self._save_lock:
            try:
                name = checked_name(name, existing_ok)
            except ValueError as exc:
                return {"ok": False, "reason": str(exc)}
            with self._lock:
                if not self._pending:
                    return {"ok": False, "reason": "ยังไม่ได้เก็บภาพ"}
                vecs = [p["vec"] for p in self._pending]
                crops = [p["crop"] for p in self._pending]
                keys = [str(p.get("step", "?")) for p in self._pending]

            # The data LINK_FLOOR is tuned from: which poses, and how alike
            # each pair was — numbers and pose names only, never the name.
            _log_batch(keys, vecs)
            # A refusal keeps the batch: the operator's next move is usually
            # to fix the name, not to stand everybody in front of the camera
            # again.
            refusal = review(vecs, name)
            if refusal:
                return {"ok": False, "reason": refusal}

            written: list[Path] = []
            try:
                total = save_shots(name, crops, replace=replace, written=written,
                                   consent_version=consent_version, existing_ok=existing_ok)
            except ValueError as exc:
                # Also people.json unreadable or held (`consent.Unreadable`,
                # `faces.LockBusy`) — save_shots has taken its files back.
                return {"ok": False, "reason": str(exc)}
            except OSError as exc:
                # The disk said no part-way (full, or a file held open past
                # the retries). save_shots has removed what this call wrote,
                # so nothing is half-filed, the name is still free and the
                # batch is still here: a retry is a real retry. It used to
                # be a 500 and then "ชื่อนี้มีอยู่แล้ว" (review, 2026-09-29).
                logger.warning("saving %s failed and was rolled back: %s", name, exc)
                return {"ok": False, "reason": _SAVE_WRITE_FAILED}
            # The next person is the next person. Shots left over would be
            # enrolled a second time under the following name.
            with self._lock:
                self._pending.clear()
                state = self._state_locked()
            result = {"ok": True, "name": name, "total": total,
                      "spread": round(shots_agree(vecs), 3),
                      "enrolled": enrolled(), **state}
            if add_to_gallery:
                result.update(_join_gallery(name, vecs, written))
            return result

    def discard(self) -> dict:
        with self._lock:
            self._pending.clear()
            return {"ok": True, **self._state_locked()}
