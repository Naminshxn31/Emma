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
