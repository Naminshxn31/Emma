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
import threading
from contextlib import contextmanager
from pathlib import Path

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

    def save(self, path: str | Path) -> None:
        import json

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unicode arrays, not object arrays: an object array can only be
        # read back with allow_pickle=True, and a pickle is code — a
        # gallery file somebody edits (or swaps in) would then run inside
        # this process on the next load. Names and JSON strings fit in
        # fixed-width 'U' arrays, which numpy reads without unpickling.
        np.savez(
            path,
            names=np.array(self.names, dtype=str),
            vecs=np.asarray(self.vecs, dtype=np.float32),
            meta=np.array([json.dumps(m, ensure_ascii=False) for m in self.meta], dtype=str),
            model_tag=np.array(self.model_tag, dtype=str),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Gallery":
        import json

        try:
            data = np.load(str(path), allow_pickle=False)
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
        if tag != MODEL_TAG:
            # Loud, not lenient. Vectors from another model still produce
            # cosines; they just point at the wrong people.
            raise ValueError(
                f"gallery {path} was built with {tag!r}, this build uses "
                f"{MODEL_TAG!r} — rebuild it, the vectors are not comparable"
            )
        return cls(names, vecs, meta, tag)
