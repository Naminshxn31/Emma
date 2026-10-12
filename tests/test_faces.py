"""
What the face layer has to get right before it is allowed to say a name.

None of these load the real models — `_no_real_face_models` in conftest
stubs the loader, which is also what a machine without `data/faces/` gets.
The graceful-degradation tests below are therefore testing the same code
path a showroom without the models runs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import faces


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


def test_importing_faces_does_not_load_the_models():
    """Import must stay cheap.

    `FaceAnalysis.prepare` reads ONNX off disk. An import that does that is
    the sentence-transformers bug: slower than the test timeout, and only on
    the machine that has the weights.
    """
    assert faces._app is None


def test_no_models_means_no_recognition_not_an_exception():
    """A showroom without the pack keeps working; it just never says a name."""
    assert faces.detect(np.zeros((64, 64, 3), np.uint8)) == []


def test_gallery_survives_a_round_trip(tmp_path: Path):
    g = faces.Gallery(["Mai", "JJ"], np.stack([_unit(1), _unit(0, 1)]),
                      [{"company": "Lazudi"}, {"company": "Etagi"}])
    g.save(tmp_path / "gallery.npz")
    back = faces.Gallery.load(tmp_path / "gallery.npz")

    assert back.names == ["Mai", "JJ"]
    assert back.meta[0]["company"] == "Lazudi"
    assert np.allclose(back.vecs, g.vecs)


def test_gallery_from_another_model_is_refused_not_ranked(tmp_path: Path):
    """The `EMBED_PROVIDER` lesson, applied to faces.

    Cosine between vectors from two different models does not raise. It
    returns a number, and the number puts a stranger at the top. So a
    gallery built by another model has to be an error on load — the one
    place it can still be caught.
    """
    g = faces.Gallery(["Mai"], np.stack([_unit(1)]), model_tag="some/other/model")
    g.save(tmp_path / "stale.npz")

    with pytest.raises(ValueError) as exc:
        faces.Gallery.load(tmp_path / "stale.npz")
    assert "not comparable" in str(exc.value)


def test_empty_gallery_matches_nobody():
    empty = faces.Gallery([], np.zeros((0, 512), np.float32))
    assert empty.match(_unit(1)) == (-1, -1.0)


def test_match_returns_the_nearest_and_its_cosine():
    g = faces.Gallery(["a", "b"], np.stack([_unit(1), _unit(0, 1)]))
    i, sim = g.match(_unit(0.1, 1))
    assert g.names[i] == "b"
    assert sim > 0.9


def test_match_does_not_decide_whether_it_is_near_enough():
    """No threshold inside `match`.

    A stranger still has a nearest neighbour, and `match` reports it with a
    low score rather than hiding it. Whoever calls this owns the number that
    turns a cosine into a greeting, because that number has to be measured
    against the real camera — see scripts/eval_faces.py.
    """
    g = faces.Gallery(["a"], np.stack([_unit(1)]))
    i, sim = g.match(_unit(0, 1))
    assert i == 0 and sim < 0.1


def _found(monkeypatch, *faces_):
    monkeypatch.setattr(faces, "read_image", lambda p: np.zeros((8, 8, 3), np.uint8))
    monkeypatch.setattr(faces, "detect", lambda img, det_size=640: list(faces_))


def test_a_photo_of_two_people_enrols_nobody(monkeypatch, tmp_path: Path):
    """Picking the bigger face would file a colleague under the wrong name.

    And it would stay wrong for as long as the gallery lives, with no
    symptom anywhere — the greeting is confident either way. Two people
    posing together come out within about 1.5x of each other, which is what
    this pair is.
    """
    _found(monkeypatch,
           faces.Face((0, 0, 100, 100), 0.9, _unit(1)),
           faces.Face((0, 0, 90, 90), 0.8, _unit(0, 1)))
    assert faces.embed_file(tmp_path / "two_people.jpg") is None


def test_somebody_walking_past_in_the_background_does_not_block_enrolment(
        monkeypatch, tmp_path: Path):
    """Four real photographs were thrown away by the stricter rule.

    The studio sessions have people crossing behind the sitter. Measured,
    those faces are 7x to 60x smaller — nowhere near the ambiguity the rule
    above exists for, and two brokers lost their enrolment over them.
    """
    subject = faces.Face((0, 0, 100, 100), 0.9, _unit(1))
    passerby = faces.Face((0, 0, 12, 12), 0.6, _unit(0, 1))
    _found(monkeypatch, subject, passerby)

    face = faces.embed_file(tmp_path / "with_background.jpg")
    assert face is not None and face.bbox == subject.bbox


def test_a_photo_with_no_face_enrols_nobody(monkeypatch, tmp_path: Path):
    _found(monkeypatch)
    assert faces.embed_file(tmp_path / "empty.jpg") is None


def test_a_crop_the_face_fills_is_retried_with_a_border(monkeypatch, tmp_path: Path):
    """The scan saves crops cut tight around the face, and SCRFD misses a
    face that fills the whole image — a real front shot was dropped from
    every rebuild until it was padded (2026-10-08). The retry must see the
    same pixels, centred, on a canvas twice the size."""
    crop = np.full((6, 4, 3), 200, np.uint8)
    monkeypatch.setattr(faces, "read_image", lambda p: crop)
    subject = faces.Face((0, 0, 100, 100), 0.85, _unit(1))
    seen = []

    def detect(img, det_size=640):
        seen.append(img)
        return [subject] if img.shape[:2] == (12, 8) else []

    monkeypatch.setattr(faces, "detect", detect)
    assert faces.embed_file(tmp_path / "tight.jpg") is subject
    assert len(seen) == 2
    assert (seen[1][3:9, 2:6] == crop).all() and seen[1].sum() == crop.sum()


def test_unreadable_file_enrols_nobody(tmp_path: Path):
    assert faces.embed_file(tmp_path / "does_not_exist.jpg") is None


def test_thai_paths_are_readable(tmp_path: Path):
    """The enrolment photos live in a folder called รูปพนักงาน.

    `cv2.imread` hands the name to the ANSI code page and returns None on a
    Thai Windows box — cp874 again, the same trap `documents.py` paid for
    with `subprocess(encoding=...)`. `read_image` goes through numpy so the
    filesystem name never meets a code page.
    """
    import cv2

    folder = tmp_path / "รูปพนักงาน"
    folder.mkdir()
    path = folder / "พนักงาน01.png"
    ok, buf = cv2.imencode(".png", np.full((8, 8, 3), 127, np.uint8))
    assert ok
    buf.tofile(str(path))

    img = faces.read_image(path)
    assert img is not None and img.shape == (8, 8, 3)


def test_faces_are_returned_nearest_first(monkeypatch):
    """The person in front of the robot, not the one behind them."""

    class _Det:
        def detect(self, image, metric="default"):
            boxes = np.array([[0, 0, 10, 10, 0.9], [0, 0, 100, 100, 0.5]], np.float32)
            kps = np.zeros((2, 5, 2), np.float32)
            return boxes, kps

    class _App:
        det_model = _Det()
        models: dict = {}

    monkeypatch.setattr(faces, "_analyzer", lambda det_size=640: _App())
    out = faces.locate(np.zeros((8, 8, 3), np.uint8))

    assert [f.bbox for f in out] == [(0, 0, 100, 100), (0, 0, 10, 10)]


def test_locating_a_face_does_not_describe_it(monkeypatch):
    """The embedding is the expensive half.

    Measured: describing a face costs about twice what finding it does, and
    a camera at a door spends most of its frames looking at nobody, or at
    somebody across the room. Both are ruled out from the box alone.
    """

    class _Det:
        def detect(self, image, metric="default"):
            return (np.array([[0, 0, 40, 40, 0.9]], np.float32),
                    np.zeros((1, 5, 2), np.float32))

    class _App:
        det_model = _Det()
        models: dict = {}

    monkeypatch.setattr(faces, "_analyzer", lambda det_size=640: _App())
    (face,) = faces.locate(np.zeros((8, 8, 3), np.uint8))

    assert face.vec is None
    assert face.kps is not None


def test_the_age_and_gender_models_are_never_loaded():
    """buffalo_l ships them; a robot at the door has no business running them.

    Asserted against the source because the thing being protected is a
    decision, not a value — nothing at runtime would look different if
    somebody widened the module list, until the day the estimate ends up in
    a log or a greeting.
    """
    src = Path(faces.__file__).read_text(encoding="utf-8")
    body = src.split("allowed_modules=")[1].split("]")[0]
    assert "detection" in body and "recognition" in body
    assert "genderage" not in body
    assert "genderage" not in src.replace("age and gender estimators", "")


# -- a gallery file is read by a live server while something rewrites it ------

def _saved(tmp_path: Path, name: str = "gallery.npz") -> Path:
    path = tmp_path / name
    faces.Gallery(["a", "b"], np.stack([_unit(1), _unit(0, 1)]), [{}, {}]).save(path)
    return path


@pytest.mark.parametrize("damage", ["truncated", "garbage", "empty", "missing", "bare-npy",
                                    "lengths"])
def test_a_damaged_gallery_is_a_value_error_never_a_crash(tmp_path: Path, damage):
    """Every caller treats ValueError as "no usable gallery" — the greeter
    at boot, the enrolment endpoint mid-save. A BadZipFile or EOFError
    escaping instead is a crash in exactly those two places."""
    path = _saved(tmp_path)
    if damage == "truncated":
        path.write_bytes(path.read_bytes()[:300])
    elif damage == "garbage":
        path.write_bytes(b"PK\x03\x04 but not really")
    elif damage == "empty":
        path.write_bytes(b"")
    elif damage == "missing":
        path.unlink()
    elif damage == "bare-npy":
        with path.open("wb") as fh:
            np.save(fh, np.zeros((2, 4), np.float32))
    else:
        with path.open("wb") as fh:
            np.savez(fh, names=np.array(["a", "b", "c"]), vecs=np.zeros((2, 4), np.float32),
                     meta=np.array(["{}", "{}"]), model_tag=np.array(faces.MODEL_TAG))

    with pytest.raises(ValueError) as exc:
        faces.Gallery.load(path)
    assert "build-face-gallery.cmd" in str(exc.value)


def test_a_save_that_dies_halfway_leaves_the_old_gallery(monkeypatch, tmp_path: Path):
    """Written in place, a crash between truncate and the last byte is a
    torn file for every later load — the greeter's gallery gone."""
    path = _saved(tmp_path)
    before = path.read_bytes()

    def torn(file, **arrays):
        if hasattr(file, "write"):
            file.write(b"PK\x03\x04 half a zip")
        else:
            Path(file).write_bytes(b"PK\x03\x04 half a zip")
        raise OSError("disk full")

    monkeypatch.setattr(faces.np, "savez", torn)
    with pytest.raises(OSError):
        faces.Gallery(["c"], np.stack([_unit(1)]), [{}]).save(path)

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [path.name], "a temp file was left behind"


def test_save_writes_exactly_the_path_it_was_given(tmp_path: Path):
    """np.savez appends ".npz" to a bare path; the temp file would have been
    written under a name nothing then renamed."""
    path = _saved(tmp_path, "gallery.current")
    assert path.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gallery.current"]
    assert faces.Gallery.load(path).names == ["a", "b"]


def test_a_replace_windows_refuses_for_a_moment_is_retried(monkeypatch, tmp_path: Path):
    """Measured here: os.replace onto a file another handle has open is
    PermissionError on Windows. The watcher reads people.json every
    decision, so that instant is a real one."""
    path = _saved(tmp_path)
    real_replace = faces.os.replace
    refusals = []

    def busy(src, dst):
        if len(refusals) < 2:
            refusals.append(dst)
            raise PermissionError(13, "in use")
        return real_replace(src, dst)

    monkeypatch.setattr(faces.os, "replace", busy)
    monkeypatch.setattr(faces, "_REPLACE_PAUSE_S", 0.0)
    faces.Gallery(["c"], np.stack([_unit(1)]), [{}]).save(path)

    assert len(refusals) == 2
    assert faces.Gallery.load(path).names == ["c"]


def test_a_replace_that_never_succeeds_gives_up_and_cleans_up(monkeypatch, tmp_path: Path):
    path = _saved(tmp_path)
    before = path.read_bytes()

    def always_busy(src, dst):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(faces.os, "replace", always_busy)
    monkeypatch.setattr(faces, "_REPLACE_PAUSE_S", 0.0)
    with pytest.raises(PermissionError):
        faces.Gallery(["c"], np.stack([_unit(1)]), [{}]).save(path)

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [path.name]


def test_appended_is_a_new_gallery_and_the_old_one_is_untouched():
    """The old one may be what a live watcher is matching against."""
    old = faces.Gallery(["a"], np.stack([_unit(1)]), [{"group": "staff"}])
    new = old.appended(["b", "b"], np.stack([_unit(0, 1), _unit(0, 0, 1)]),
                       [{"group": "enrolled"}, {"group": "enrolled"}])

    assert new is not old
    assert old.names == ["a"] and len(old.vecs) == 1 and old.meta == [{"group": "staff"}]
    assert new.names == ["a", "b", "b"] and new.vecs.shape == (3, 512)
    assert new.meta[0] == {"group": "staff"} and new.model_tag == old.model_tag
    new.meta[0]["group"] = "changed"
    assert old.meta[0]["group"] == "staff"


def test_appended_onto_an_empty_gallery():
    empty = faces.Gallery([], np.zeros((0, 512), np.float32))
    assert empty.appended(["a"], _unit(1), [{}]).names == ["a"]


def test_appended_refuses_rows_that_do_not_line_up():
    g = faces.Gallery(["a"], np.stack([_unit(1)]))
    with pytest.raises(ValueError):
        g.appended(["b", "c"], np.stack([_unit(0, 1)]), [{}, {}])
    with pytest.raises(ValueError):
        g.appended(["b"], np.zeros((1, 128), np.float32), [{}])


# -- the cross-process lock around load-modify-save -----------------------------
#
# gallery.npz and people.json each have writers in more than one process
# (the server's scan, the rebuild, the station, revoke_face.py). A
# threading.Lock orders one process; these pin that `file_lock` orders all
# of them, and that running out of patience is a refusal, never a write.

_HOLDER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from app import faces
with faces.file_lock(Path(sys.argv[2]), 5.0, "busy"):
    Path(sys.argv[3]).write_text("held", encoding="utf-8")
    time.sleep(60)
"""


def test_the_lock_file_is_not_named_like_a_leftover_temp_file():
    """`write_atomically`'s temp files start with "<name>." — a lock file
    named "gallery.npz.lock" would read as one left behind."""
    assert faces.lock_path(Path("x") / "gallery.npz").name == "gallery.lock"
    assert faces.lock_path(Path("x") / "people.json").name == "people.lock"


def test_a_second_holder_waits_then_refuses_in_the_callers_words(tmp_path: Path):
    target = tmp_path / "gallery.npz"
    with faces.file_lock(target, 1.0, "unused"):
        with pytest.raises(faces.LockBusy) as exc:
            with faces.file_lock(target, 0.2, "กำลังเขียนอยู่"):
                pytest.fail("two holders at once")
    assert str(exc.value) == "กำลังเขียนอยู่"
    assert isinstance(exc.value, ValueError), "callers already turn ValueError into a refusal"
    with faces.file_lock(target, 0.2, "unused"):
        pass                                            # released on the way out


def test_the_lock_holds_across_processes_and_dies_with_its_holder(tmp_path: Path):
    """Another process — the rebuild, the station — is who the lock is for.
    And a holder killed mid-run (Ctrl+C on a rebuild) must not leave a lock
    that refuses every enrolment until somebody deletes a file by hand."""
    import os
    import subprocess
    import sys
    import time

    root = Path(__file__).resolve().parent.parent
    target, held = tmp_path / "gallery.npz", tmp_path / "held.txt"
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(root), str(target), str(held)],
        cwd=root, env={**os.environ, "PYTHONUTF8": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
    try:
        deadline = time.monotonic() + 30
        while not held.exists():
            assert child.poll() is None, child.communicate()[1]
            assert time.monotonic() < deadline, "the child never took the lock"
            time.sleep(0.02)
        with pytest.raises(faces.LockBusy):
            with faces.file_lock(target, 0.3, "busy"):
                pytest.fail("the lock did not hold across processes")
    finally:
        child.kill()
        child.communicate(timeout=30)
    with faces.file_lock(target, 5.0, "the dead holder's lock was never released"):
        pass
