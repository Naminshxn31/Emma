"""
The two refusals that stand between a camera and a poisoned gallery.

Enrolment is the one operation here with no way back. A wrong photograph in
`data/faces/enrolled/` becomes a wrong vector in every gallery rebuilt from
it, and the only symptom is the robot greeting somebody by a colleague's
name — confidently, out loud, to their face. So both guards get tested at
the function, not left to the shape of the command line.
"""
from __future__ import annotations

import numpy as np
import pytest

from app import faces
from scripts import enroll_face


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


ALICE = _unit(1)
BOB = _unit(0, 1)
ALICE_AGAIN = _unit(1, 0.3)


def _gallery_at(tmp_path, names, vecs):
    path = tmp_path / "gallery.npz"
    faces.Gallery(names, np.stack(vecs), [{"group": "staff"}] * len(names)).save(path)
    return path


# -- "these shots are all one person" --------------------------------------

def test_a_single_shot_always_agrees_with_itself():
    assert enroll_face.shots_agree([ALICE]) == 1.0


def test_shots_of_one_person_agree():
    assert enroll_face.shots_agree([ALICE, ALICE_AGAIN]) > enroll_face.AGREEMENT_FLOOR


def test_somebody_walking_through_the_capture_fails_the_batch():
    """Five seconds of capture is long enough for a second face to appear.

    Reported as the worst pair, not the average: one bad shot among four
    good ones still means the batch cannot be trusted, and an average would
    hide exactly that.
    """
    assert enroll_face.shots_agree([ALICE, ALICE_AGAIN, BOB]) < enroll_face.AGREEMENT_FLOOR


# -- "this face is already in here under another name" ---------------------

def test_enrolling_a_face_already_filed_under_another_name_is_refused(
        monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery",
                        str(_gallery_at(tmp_path, ["ต้า"], [ALICE])))
    monkeypatch.setattr(settings, "face_threshold", 0.45)

    refusal = enroll_face.check_against_gallery(ALICE_AGAIN, "มิกซ์")
    assert refusal is not None and "ต้า" in refusal


def test_adding_another_angle_of_the_same_person_is_the_point(monkeypatch, tmp_path):
    """Matching your own existing entry is success, not a collision.

    This is the whole feature: a second enrolment through the real camera,
    for somebody whose only picture so far was a studio portrait.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery",
                        str(_gallery_at(tmp_path, ["ต้า"], [ALICE])))
    monkeypatch.setattr(settings, "face_threshold", 0.45)

    assert enroll_face.check_against_gallery(ALICE_AGAIN, "ต้า") is None


def test_a_genuinely_new_face_is_allowed(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery",
                        str(_gallery_at(tmp_path, ["ต้า"], [ALICE])))
    monkeypatch.setattr(settings, "face_threshold", 0.45)

    assert enroll_face.check_against_gallery(BOB, "มิกซ์") is None


def test_no_gallery_yet_is_not_a_refusal(monkeypatch, tmp_path):
    """The first person enrolled has nobody to collide with."""
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "nothing.npz"))
    assert enroll_face.check_against_gallery(ALICE, "ต้า") is None


def test_a_gallery_from_another_model_does_not_get_a_vote(monkeypatch, tmp_path):
    """Its cosines are meaningless here, so it can neither refuse nor bless.

    Refusing on them would block enrolment for a reason that is not real;
    trusting them would let a stranger through. It abstains, and
    `build_face_gallery.py` rebuilds the file anyway.
    """
    from app.config import settings

    stale = tmp_path / "stale.npz"
    faces.Gallery(["ต้า"], np.stack([ALICE]), model_tag="other/model").save(stale)
    monkeypatch.setattr(settings, "face_gallery", str(stale))

    assert enroll_face.check_against_gallery(ALICE, "มิกซ์") is None


# -- the crop kept on disk -------------------------------------------------

@pytest.mark.parametrize("bbox", [(100, 100, 200, 200), (0, 0, 60, 60)])
def test_the_crop_keeps_margin_around_the_face_and_stays_inside_the_frame(bbox):
    """Margin so a future model has hair and chin to work with; clamped so a
    face at the edge of the frame does not produce an empty array."""
    image = np.zeros((480, 640, 3), np.uint8)
    face = faces.Face(bbox, 0.9, ALICE)

    out = enroll_face.crop_face(image, face)
    assert out.size > 0
    assert out.shape[0] > (bbox[3] - bbox[1])


# -- the sheet a person says yes to ----------------------------------------

@pytest.mark.parametrize("n,rows", [(1, 1), (5, 1), (6, 1), (7, 2), (50, 9)])
def test_the_confirmation_sheet_wraps_instead_of_growing_sideways(n, rows):
    """`--photos` on a folder can produce fifty crops.

    One long strip would put a window eleven thousand pixels wide on screen
    and show the first three, which is worse than no confirmation step at
    all — it looks like one happened.
    """
    crops = [np.full((80, 80, 3), 40, np.uint8) for _ in range(n)]
    sheet = enroll_face.contact_sheet(crops)

    cell = enroll_face.SHEET_CELL
    assert sheet.shape[0] == rows * cell + 44
    assert sheet.shape[1] <= enroll_face.SHEET_COLS * cell


def test_every_crop_lands_on_the_sheet():
    """A shot that is about to be saved but is not shown is a shot nobody
    agreed to."""
    crops = [np.full((60, 60, 3), v, np.uint8) for v in (30, 90, 150, 210)]
    sheet = enroll_face.contact_sheet(crops)

    cell = enroll_face.SHEET_CELL
    corners = [int(sheet[10, i * cell + 10, 0]) for i in range(len(crops))]
    assert corners == [30, 90, 150, 210]
