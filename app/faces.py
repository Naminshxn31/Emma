"""
Turning a face in front of the camera into a name — and, more often, into
"I don't know", which is the answer this module is built to be able to give.

Why a module and not a tool
---------------------------
The model never decides who somebody is. Same shape as `show_unit`: the
server looks the person up and hands the assistant a name, or hands it
nothing. A tool shaped `greet(name=...)` would let a machine that mishears
numbers also guess faces, and put the guess in a greeting spoken out loud
in front of the person it got wrong.

The gallery holds vectors, not photographs
------------------------------------------
An embedding cannot be shown to anyone, cannot be posted, and is useless to
whoever copies the file without the same model. The enrolment photographs
stay where their owner put them; this project keeps a `.npz` of numbers.
That is also why `allowed_modules` below lists exactly two models: buffalo_l
ships age and gender estimators, and a robot at the entrance guessing how
old a visitor is has no business being one import away from happening.

Two embedding spaces must never meet
------------------------------------
The lesson `EMBED_PROVIDER` paid for in `slide_search`: cosine between
vectors from two different models does not error, it returns a number, and
the number ranks strangers above the right person. So the model tag is
written into the gallery file and checked on load — a changed model means a
stale gallery, loudly, not a gallery that quietly matches the wrong face.

Loading is lazy on purpose
--------------------------
`FaceAnalysis.prepare` reads ONNX weights off disk. Doing that at import
time is the sentence-transformers bug again: an import slower than the test
timeout, on the one machine that has the models installed.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Callable

import numpy as np

from app.config import settings

logger = logging.getLogger("condo_voice.faces")

#: The packs this module knows how to load, keyed by the directory name
#: under `data/faces/models/`. Each entry is (tag, recognition file).
#:
#: auraface is the default because of a licence, not a benchmark: the
#: buffalo_l weights are "for non-commercial research purposes only"
#: (insightface python-package README, checked 2026-08-31), and a condo
#: sales gallery is neither. AuraFace (fal/AuraFace-v1) is Apache-2.0,
#: trained on commercially usable data, the same ArcFace family behind the
#: same SCRFD detector — same shapes, different licence. buffalo_l stays
#: loadable (FACE_MODEL_PACK=buffalo_l) for research comparison only.
#:
#: The detector is the exception, and so is active_speaker's 2d106det: the
#: auraface pack's scrfd_10g_bnkps.onnx is byte-identical to buffalo_l's
#: det_10g.onnx (sha256 5838f7fe…), and its 2d106det.onnx to buffalo_l's
#: (f001b856…) — insightface model-zoo files whatever the HF repo's tag
#: says. Using those two here: owner approved 9 Oct 2026. The recogniser
#: (glintr100, the one that names people) is AuraFace's own.
#:
#: Every threshold in config.py is measured against ONE of these spaces.
#: Changing the pack without re-running scripts/eval_faces.py is running on
#: numbers from a model that is no longer there.
_PACKS = {
    "auraface": ("fal/auraface-v1/glintr100", "glintr100.onnx"),
    "buffalo_l": ("insightface/buffalo_l/w600k_r50", "w600k_r50.onnx"),
}


def _pack() -> str:
    """The configured pack, with the profile rule: misspelt = default."""
    return settings.face_model_pack if settings.face_model_pack in _PACKS         else "auraface"


#: Written into every gallery file and checked on load — the vectors are
#: not comparable across models, and a mismatch has to be an error, never a
#: silent rank. Fixed per process: comparing two packs means two processes.
MODEL_TAG = _PACKS[_pack()][0]

_MODEL_ROOT = Path(__file__).resolve().parent.parent / "data" / "faces"

_app = None
_load_lock = threading.Lock()
_load_failed = False


class Face:
    """One detected face: where it is, how sure the detector is, and its vector.

    `vec` is None until somebody asks for it. Describing a face costs about
    twice what finding it does, and most frames from a camera at a door
    either have nobody in them or have somebody too far away to greet —
    neither is worth the embedding. `kps` is the five-point landmark the
    recogniser needs to align the crop, kept from the detection pass.
    """

    __slots__ = ("bbox", "det_score", "vec", "kps")

    def __init__(self, bbox, det_score: float, vec: np.ndarray | None = None, kps=None):
        self.bbox = tuple(int(v) for v in bbox)
        self.det_score = float(det_score)
        self.vec = vec
        self.kps = kps

    @property
    def area(self) -> int:
        x1, y1, x2, y2 = self.bbox
        return max(0, x2 - x1) * max(0, y2 - y1)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Face(bbox={self.bbox}, det={self.det_score:.2f})"


def available() -> bool:
    """Whether the models are on this machine at all.

    Checked before anything tries to load them, so a showroom without the
    face models degrades to "no recognition" instead of an exception on the
    first visitor.
    """
    pack = _pack()
    return (_MODEL_ROOT / "models" / pack / _PACKS[pack][1]).is_file()


@contextmanager
def _thread_limit(threads: int):
    """Make onnxruntime use `threads` cores instead of every core there is.

    Measured on the owner's 16-core machine, one 1280x720 frame through
    detection + recognition:

        threads   wall     CPU time   cores busy
        default   201 ms   2978 ms    14.8      <- the machine at 100%
        4         201 ms   1409 ms     7.0
        2         326 ms    977 ms     3.0      <- default
        1         822 ms    808 ms     1.0

    Past a few threads this model buys almost nothing and spends
    everything: the work per convolution is small enough that synchronising
    sixteen threads costs more than it saves — at four the frame takes
    exactly as long as it did with all sixteen. The number that matters
    here is the third column, not the first, because nothing is waiting on
    this frame; see `FACE_THREADS` in config.py for the same measurement
    expressed as a share of the machine.

    It has to be done by patching, because `FaceAnalysis` forwards only
    `providers` and `provider_options` to `InferenceSession` and drops
    `sess_options` on the floor (insightface 1.0.1, `model_zoo.get_model`).
    Scoped to the construction call and put back afterwards, so nothing else
    in the process inherits it.
    """
    if threads <= 0:
        yield
        return

    import insightface.model_zoo.model_zoo as mz
    import onnxruntime as ort

    original = mz.PickableInferenceSession

    class _Limited(original):  # type: ignore[valid-type,misc]
        def __init__(self, model_path, **kwargs):
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            kwargs.setdefault("sess_options", options)
            super().__init__(model_path, **kwargs)

    mz.PickableInferenceSession = _Limited
    try:
        yield
    finally:
        mz.PickableInferenceSession = original


def _analyzer(det_size: int = 640):
    global _app, _load_failed
    if _app is not None or _load_failed:
        return _app
    with _load_lock:
        if _app is not None or _load_failed:
            return _app
        try:
            from insightface.app import FaceAnalysis

            with _thread_limit(settings.face_threads):
                app = FaceAnalysis(
                    name=_pack(),
                    root=str(_MODEL_ROOT),
                    providers=["CPUExecutionProvider"],
                    # Detection and recognition only. See the module
                    # docstring: the age/gender models ship in the same pack
                    # and are not something a machine should be running on
                    # visitors.
                    allowed_modules=["detection", "recognition"],
                )
            app.prepare(ctx_id=-1, det_size=(det_size, det_size))
            _app = app
            logger.info("face models ready (%s, %d threads)",
                        MODEL_TAG, settings.face_threads)
        except Exception as exc:  # pragma: no cover - depends on the machine
            _load_failed = True
            logger.warning("face models unavailable: %s", exc)
    return _app


def reset() -> None:
    """Drop the loaded models. For tests, and for a config change at runtime."""
    global _app, _load_failed
    _app = None
    _load_failed = False


def locate(image: np.ndarray, det_size: int = 640) -> list[Face]:
    """Where the faces are, largest first. No embeddings.

    Largest first because the person standing closest to the robot is the
    one being spoken to, and the row behind them is not.
    """
    app = _analyzer(det_size)
    if app is None:
        return []
    boxes, kpss = app.det_model.detect(image, metric="default")
    faces = [
        Face(box[:4], float(box[4]), None, None if kpss is None else kpss[i])
        for i, box in enumerate(boxes)
    ]
    faces.sort(key=lambda f: f.area, reverse=True)
    return faces


def describe(image: np.ndarray, face: Face, det_size: int = 640) -> Face | None:
    """Fill in `face.vec`. None when the recogniser cannot be reached.

    Separate from `locate` because it is the expensive half — measured at
    roughly twice the cost of finding the face in the first place — and
    because most of the frames it would run on are of nobody, or of somebody
    across the room. Deciding *whether* a face is worth describing is the
    caller's, and the callers have cheaper tests to apply first.
    """
    app = _analyzer(det_size)
    if app is None or face.kps is None:
        return None
    rec = app.models.get("recognition")
    if rec is None:  # pragma: no cover - the pack always ships one
        return None

    from insightface.app.common import Face as _RawFace

    raw = _RawFace(bbox=np.array(face.bbox, dtype=np.float32),
                   kps=face.kps, det_score=face.det_score)
    rec.get(image, raw)
    face.vec = np.asarray(raw.normed_embedding, dtype=np.float32)
    return face


def detect(image: np.ndarray, det_size: int = 640) -> list[Face]:
    """Every face in a BGR frame, largest first, all of them described.

    The whole-frame version, for enrolment and for measurement where every
    face is going to be compared anyway. A live camera should use `locate`
    and describe only what it intends to act on.
    """
    faces = locate(image, det_size)
    for face in faces:
        describe(image, face, det_size)
    return [f for f in faces if f.vec is not None]


def read_image(path: str | Path) -> np.ndarray | None:
    """Load a file to BGR.

    Goes through numpy rather than handing the path to `cv2.imread`, because
    the photographs live under a Thai directory name and OpenCV's file
    reader passes it to the ANSI code page and returns None — the same cp874
    trap that `subprocess(encoding="utf-8")` paid for, wearing a different hat.
    """
    import cv2

    try:
        buf = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


#: How long `write_atomically` keeps retrying a replace that Windows refused.
#: See there; a second is far longer than any reader holds these files.
_REPLACE_ATTEMPTS = 20
_REPLACE_PAUSE_S = 0.05


def write_atomically(path: str | Path, write: Callable[[IO[bytes]], object]) -> None:
    """Write `path` so that nobody ever reads half of it.

    The gallery and `people.json` are read by a running server while
    something else rewrites them — the enrolment endpoint adds a person
    mid-day, the rebuild runs next to a live greeter. Written in place, a
    reader landing between truncate and the last byte gets a torn file, and
    a crash there leaves one on disk for every later load. So the bytes go
    to a temp file in the *same directory* (a rename across drives is a
    copy, not a swap) and `os.replace` puts it in position in one step.

    The retry is Windows, measured on this machine before it was written:
    `os.replace` onto a file another handle has open fails with
    PermissionError (WinError 5), because Python opens files without
    FILE_SHARE_DELETE. The watcher reads `people.json` on every decision,
    so an enrolment landing on that instant would fail on a race the old
    in-place write never had. The reads take microseconds; waiting them
    out is the fix, not a different file layout.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_PAUSE_S)
    except BaseException:
        # A temp file left behind is harmless to readers but piles up next
        # to the real one; nothing else would ever clean it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


#: How often a waiting `file_lock` tries again.
_LOCK_POLL_S = 0.05


class LockBusy(ValueError):
    """`file_lock` could not get the lock in time.

    A ValueError on purpose: every caller already turns ValueError into a
    refusal somebody reads (the scan's gallery_reason, the station's
    reason line, the rebuild's last printed line). Refusing is the point —
    the only other thing to do after a wait runs out is write anyway, which
    is the lost update the lock exists to stop.
    """


def lock_path(path: str | Path) -> Path:
    """The lock file that guards `path`: same folder, `.lock` for the
    suffix (`gallery.npz` -> `gallery.lock`). Not `gallery.npz.lock`: that
    starts with the temp-file prefix `write_atomically` uses, and a lock
    file is not a leftover."""
    return Path(path).with_suffix(".lock")


if os.name == "nt":
    import errno
    import msvcrt

    def _try_lock(fd: int) -> bool:
        # One byte at offset 0. Windows allows locking past the end of a
        # file, so the lock file stays empty. Locks belong to the handle:
        # a second handle in the *same* process is refused too, which is
        # what lets a test show a rebuild and an append excluding each
        # other without spawning anything.
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EDEADLOCK):
                return False
            raise
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:  # pragma: no cover - the showroom machine is Windows
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(path: str | Path, timeout: float, busy: str):
    """Hold `lock_path(path)` exclusively — across processes, not just threads.

    `gallery.npz` and `people.json` each have writers in more than one
    process: the server (the call screen's scan) appends to the gallery and
    records consent, `build_face_gallery.py` rewrites the gallery, the
    enrolment station records consent and `revoke_face.py` withdraws it.
    Each writer does
    load -> change -> save, and a `threading.Lock` only orders the ones in
    its own process. Measured in the 2026-09-29 review: a rebuild that
    listed enrolled/ before a scan was saved wrote the gallery without that
    person *after* the screen said "หุ่นจำได้แล้ว"; a revoke landing between
    a save's load and its replace was written over, and the revoked person
    was named again.

    An OS byte-range lock rather than "create the file if absent": the OS
    drops it when the holder dies, so a rebuild killed with Ctrl+C cannot
    leave a lock that refuses every enrolment until somebody deletes a file
    by hand. The lock file itself stays (deleting it on release races the
    next opener); it is empty.

    Waits up to `timeout` seconds, then raises `LockBusy(busy)`. Readers
    never take it — the watcher reads people.json every frame, and
    `write_atomically` already keeps a reader from seeing half a file.
    """
    target = lock_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockBusy(busy)
            time.sleep(_LOCK_POLL_S)
        try:
            yield
        finally:
            try:
                _unlock(fd)
            except OSError:
                pass                       # closing the handle releases it anyway
    finally:
        os.close(fd)


#: How much bigger the subject has to be than anyone else in the frame for
#: the photograph to enrol them. Measured on the real photographs: the
#: bystanders caught in the background of the studio shots come out 7x to
#: 60x smaller than the sitter, while two people posing together are within
#: about 1.5x of each other. Anywhere in that gap works; 4 is in the middle.
DOMINANT_RATIO = 4.0


def embed_file(path: str | Path, det_size: int = 640) -> Face | None:
    """The subject of an enrolment photograph, or None when there isn't one.

    "Exactly one face in the frame" was the first rule here and it was too
    strict by four photographs: the studio sessions have staff and visitors
    wandering through the background, and two people lost their enrolment
    entirely over faces 60x smaller than theirs at the edge of the shot.

    But the reason for the rule stands. A photo with two people in it has no
    single answer to whose vector this is, and quietly taking the bigger one
    files a colleague under the wrong name for as long as the gallery lives,
    with nothing anywhere to say so. So the test is not "one face", it is
    "one face that is obviously the subject" — and when the frame cannot
    answer that, it still enrols nobody.
    """
    img = read_image(path)
    if img is None:
        return None
    found = detect(img, det_size)
    if not found:
        # A camera-scan crop is cut tight around the face, and a face that
        # fills the whole image is one SCRFD's anchors miss: a front shot
        # (540x558) found nothing at det 640 or 320 and 0.85 once padded
        # (2026-10-08) — every rebuild had been silently dropping it, so
        # that person was matched on their side poses only. Black padding
        # cannot add a second face for the subject rule below to trip on.
        h, w = img.shape[:2]
        padded = np.zeros((h * 2, w * 2) + img.shape[2:], dtype=img.dtype)
        padded[h // 2:h // 2 + h, w // 2:w // 2 + w] = img
        found = detect(padded, det_size)
    if not found:
        return None
    if len(found) > 1:
        runner_up = found[1].area
        if runner_up <= 0 or found[0].area < DOMINANT_RATIO * runner_up:
            logger.info("%s: %d faces and no clear subject, skipped",
                        Path(path).name, len(found))
            return None
    return found[0]


class Gallery:
    """Enrolled people: names, vectors, and the model that produced them."""

    def __init__(self, names: list[str], vecs: np.ndarray, meta: list[dict] | None = None,
                 model_tag: str = MODEL_TAG):
        self.names = names
        self.vecs = vecs.astype(np.float32) if len(vecs) else np.zeros((0, 512), np.float32)
        self.meta = meta or [{} for _ in names]
        self.model_tag = model_tag

    def __len__(self) -> int:
        return len(self.names)

    def match2(self, vec: np.ndarray) -> tuple[int, float, float]:
        """(index of nearest, its cosine, best cosine of a *different* name).

        The runner-up is another person, not another shot of the same
        person — five enrolment shots of โชกุน all scoring high is
        agreement, not ambiguity. (-1, -1.0, -1.0) when empty; a runner-up
        of -1.0 when only one person is enrolled.
        """
        if not len(self.vecs):
            return -1, -1.0, -1.0
        sims = self.vecs @ vec.astype(np.float32)
        i = int(np.argmax(sims))
        best = self.names[i]
        others = [float(s) for s, n in zip(sims, self.names) if n != best]
        return i, float(sims[i]), (max(others) if others else -1.0)

    def match(self, vec: np.ndarray) -> tuple[int, float]:
        """(index of nearest enrolled person, cosine). (-1, -1.0) when empty.

        No threshold here. Deciding *whether* the nearest is near enough is
        the caller's business and needs a number measured against a real
        camera, not a constant buried in a helper.
        """
        if not len(self.vecs):
            return -1, -1.0
        sims = self.vecs @ vec.astype(np.float32)
        i = int(np.argmax(sims))
        return i, float(sims[i])

    def appended(self, names: list[str], vecs, metas: list[dict]) -> "Gallery":
        """A new gallery: this one plus `names`/`vecs`/`metas` at the end.

        A new object rather than a mutation, because the one being extended
        may be the gallery a live watcher is matching against right now —
        `vecs` and `names` changing length between two reads of the same
        frame is an index into the wrong person. The caller swaps the whole
        thing in when it is ready.
        """
        names = [str(n) for n in names]
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.ndim == 1:
            vecs = vecs.reshape(1, -1)
        metas = [dict(m) for m in metas]
        if not (len(names) == len(vecs) == len(metas)):
            raise ValueError(f"{len(names)} names, {len(vecs)} vectors and "
                             f"{len(metas)} metas do not line up")
        # Another width is another model, whatever the tag says; concatenate
        # refuses it with a ValueError, which is the answer wanted here.
        joined = np.concatenate([self.vecs, vecs]) if len(self.vecs) else vecs
        return Gallery(list(self.names) + names, joined,
                       [dict(m) for m in self.meta] + metas, self.model_tag)

    def save(self, path: str | Path) -> None:
        import json

        # Unicode arrays, not object arrays: an object array can only be
        # read back with allow_pickle=True, and a pickle is code — a
        # gallery file somebody edits (or swaps in) would then run inside
        # this process on the next load. Names and JSON strings fit in
        # fixed-width 'U' arrays, which numpy reads without unpickling.
        arrays = dict(
            names=np.array(self.names, dtype=str),
            vecs=np.asarray(self.vecs, dtype=np.float32),
            meta=np.array([json.dumps(m, ensure_ascii=False) for m in self.meta], dtype=str),
            model_tag=np.array(self.model_tag, dtype=str),
        )
        # Through an open handle: handed a bare path, np.savez appends
        # ".npz" to anything not already ending in it, so the temp file
        # would be written under a name nobody then renames. Atomic because
        # the server now adds people while the greeter reads this file.
        write_atomically(path, lambda fh: np.savez(fh, **arrays))

    @classmethod
    def load(cls, path: str | Path) -> "Gallery":
        import json

        try:
            # `with`, so the handle is closed before this returns: an open
            # NpzFile on Windows is what makes the next atomic save's
            # os.replace fail (see `write_atomically`).
            with np.load(str(path), allow_pickle=False) as data:
                tag = str(data["model_tag"])
                # Read every array here: numpy raises for an object array on
                # *access*, not on open, so a check that only reads the tag
                # would pass the old format straight into the constructor.
                names = [str(n) for n in data["names"]]
                vecs = data["vecs"]
                meta = [json.loads(m) for m in data["meta"]]
        except ValueError as exc:
            # The pre-2026-09-01 format stored object arrays. Refusing is
            # the point — see `save` — and the fix is one command.
            raise ValueError(
                f"gallery {path} is in the old pickled format (or damaged): "
                f"{exc}. Rebuild it: build-face-gallery.cmd "
                f"(python scripts/build_face_gallery.py)"
            ) from None
        except (zipfile.BadZipFile, EOFError, OSError, KeyError,
                AttributeError, TypeError) as exc:
            # A torn file — a copy interrupted, a disk that filled, an
            # editor that saved half. Every caller already handles
            # ValueError as "no usable gallery"; anything else escaping
            # here is a crash in the greeter at boot or in the enrolment
            # endpoint mid-save. AttributeError/TypeError are a bare .npy
            # where an .npz belongs: np.load returns an array, not a
            # context manager.
            raise ValueError(
                f"gallery {path} is damaged or not a gallery "
                f"({type(exc).__name__}: {exc}). Rebuild it: "
                f"build-face-gallery.cmd (python scripts/build_face_gallery.py)"
            ) from None
        if not (len(names) == len(vecs) == len(meta)):
            # Every lookup indexes names and meta by a row of vecs; lengths
            # that disagree are an IndexError at the first match, or worse,
            # the right score next to the wrong name.
            raise ValueError(
                f"gallery {path} is damaged: {len(names)} names, {len(vecs)} "
                f"vectors, {len(meta)} metas. Rebuild it: build-face-gallery.cmd"
            )
        if tag != MODEL_TAG:
            # Loud, not lenient. Vectors from another model still produce
            # cosines; they just point at the wrong people.
            raise ValueError(
                f"gallery {path} was built with {tag!r}, this build uses "
                f"{MODEL_TAG!r} — rebuild it, the vectors are not comparable"
            )
        return cls(names, vecs, meta, tag)
