"""
The shared enrolment rules, and the promise that both front ends use them.

`scripts/enroll_face.py` (a command line) and `scripts/enroll_app.py` (a page
you leave open) both put faces in the gallery. The reason this module exists
is that they must not each have an opinion about when that is allowed — the
four-exits bug, where every path that ended a call decided for itself and all
four were wrong in different ways.
"""
from __future__ import annotations

import re

import numpy as np
import pytest

from app import enrollment, faces


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


ALICE = _unit(1)
BOB = _unit(0, 1)
ALICE_AGAIN = _unit(1, 0.3)
FRAME = np.zeros((720, 1280, 3), np.uint8)


def _locates(monkeypatch, *faces_):
    monkeypatch.setattr(faces, "locate", lambda img, det_size=640: list(faces_))


def _station_with(monkeypatch, pending: list[dict]) -> enrollment.Session:
    """Seed the station's batch.

    The batch used to be a module list in the station; it is an
    `enrollment.Session` now (the call screen needed the same capture), so
    a fresh session per test replaces patching the list.
    """
    from scripts import enroll_app

    session = enrollment.Session(pending=pending)
    monkeypatch.setattr(enroll_app, "SESSION", session)
    return session


# -- one copy of the rules -------------------------------------------------

def test_both_front_ends_share_the_same_rules():
    """Not "they agree" — the same function object.

    A test that only checked behaviour would still pass on the day the two
    copies drifted, right up until one of them let a face through that the
    other would have refused.
    """
    from scripts import enroll_face

    assert enroll_face.shots_agree is enrollment.shots_agree
    assert enroll_face.check_against_gallery is enrollment.check_against_gallery
    assert enroll_face.crop_face is enrollment.crop_face
    assert enroll_face.ENROLLED is enrollment.ENROLLED


def test_the_station_never_listens_on_the_network():
    """Faces and names, on a socket, is not something to get wrong once.

    `warn_if_open_to_the_network` exists in this project because a default
    of `HOST=0.0.0.0` with an empty `WS_TOKEN` shipped already. This one has
    no host flag at all, and the test reads the source so it cannot grow one
    by accident.
    """
    import ast
    from pathlib import Path

    from scripts import enroll_app

    src = Path(enroll_app.__file__).read_text(encoding="utf-8")
    # The module docstring explains *why* by naming the setting that caused
    # the original bug, so scanning the raw text finds `0.0.0.0` in the
    # explanation and fails on the sentence warning against it. Strip the
    # docstring and read the code.
    tree = ast.parse(src)
    tree.body = [n for n in tree.body if not (isinstance(n, ast.Expr)
                                              and isinstance(n.value, ast.Constant)
                                              and isinstance(n.value.value, str))]
    code = ast.unparse(tree)

    assert "127.0.0.1" in code
    assert "0.0.0.0" not in code
    assert "--host" not in code


# -- is this frame a portrait of somebody? ---------------------------------

def test_an_empty_frame_is_not_a_portrait(monkeypatch):
    _locates(monkeypatch)
    face, why = enrollment.usable_face(FRAME)
    assert face is None and why == "no face"


def test_two_people_in_frame_is_not_a_portrait(monkeypatch):
    """Whose vector would it be? Taking the bigger one answers that silently."""
    _locates(monkeypatch,
             faces.Face((0, 0, 200, 200), 0.9, ALICE),
             faces.Face((0, 0, 180, 180), 0.8, BOB))
    face, why = enrollment.usable_face(FRAME)
    assert face is None and "only one person" in why


def test_somebody_across_the_room_is_told_to_come_closer(monkeypatch):
    _locates(monkeypatch, faces.Face((0, 0, 40, 40), 0.9, ALICE))
    face, why = enrollment.usable_face(FRAME, min_px=110)
    assert face is None and why == "come closer"


def test_a_passer_by_behind_the_subject_does_not_spoil_the_frame(monkeypatch):
    """Measured at 7x to 60x smaller in the real studio photographs."""
    subject = faces.Face((0, 0, 200, 200), 0.9, ALICE)
    _locates(monkeypatch, subject, faces.Face((0, 0, 20, 20), 0.6, BOB))

    face, why = enrollment.usable_face(FRAME)
    assert face is subject and why == ""


# -- would this batch be allowed in? ---------------------------------------

def test_a_batch_with_two_people_in_it_is_refused():
    refusal = enrollment.review([ALICE, ALICE_AGAIN, BOB], "ต้า")
    assert refusal is not None and "same person" in refusal


def _mix(*parts) -> np.ndarray:
    """Unit vector from (weight, axis) pairs over orthogonal axes."""
    v = np.zeros(512, np.float32)
    for weight, axis in parts:
        v[axis] += weight
    return v / np.linalg.norm(v)


def test_five_poses_of_one_person_are_one_person_even_when_the_sides_differ(monkeypatch, tmp_path):
    """Owner, 2026-09-29, on the robot: one person, five poses, refused as
    "not the same person". The poses are MEANT to differ: turned left against
    turned right is the least alike pair of a good batch. Each turned shot
    only has to look like its neighbour pose (the front), not like the
    opposite side."""
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "none.npz"))
    front = _mix((1, 0))
    batch = [front, _mix((1, 0), (.15, 1)),              # front2
             _mix((1, 0), (.25, 2)),                     # far
             _mix((.6, 0), (.8, 3)),                     # turned one way  (.6 to front)
             _mix((.6, 0), (-.8, 3))]                    # turned the other (.6 to front)
    assert enrollment.shots_agree(batch) < enrollment.AGREEMENT_FLOOR     # the old test refused this
    assert enrollment.batch_link(batch) >= enrollment.LINK_FLOOR
    assert enrollment.review(batch, "ต้า") is None


def test_two_shots_of_someone_else_are_refused_even_though_they_match_each_other():
    """The case a nearest-neighbour check alone would miss: the stranger's two
    shots vouch for each other, but nothing links them to the rest."""
    a, stranger = _mix((1, 0)), _mix((.3, 0), (.95, 5))
    batch = [a, _mix((1, 0), (.1, 1)), _mix((1, 0), (.2, 2)), stranger, _mix((.3, 0), (.95, 5), (.1, 6))]
    assert enrollment.batch_link(batch) < enrollment.LINK_FLOOR
    refusal = enrollment.review(batch, "ต้า")
    assert refusal is not None and refusal.startswith("the shots do not all look like the same person")


def test_the_link_floor_sits_in_the_measured_gap():
    # 2026-09-29: batches with 1-2 shots of somebody else at most .438; the
    # station's genuine batches .558-.690 — and the robot's camera, poorer,
    # refused one genuine batch at .489 while the floor was .50.
    assert .438 < enrollment.LINK_FLOOR <= .489
    assert enrollment.batch_link([_mix((1, 0))]) == 1.0


def test_every_save_logs_its_pair_table_by_pose_and_never_the_name(caplog):
    """The floor is tuned from these lines; the robot's first refusal could
    only say 'link .489' because nothing more was written."""
    import logging

    batch = [_mix((1, 0)), _mix((1, 0), (.2, 1)), _mix((.6, 0), (.8, 3))]
    with caplog.at_level(logging.INFO, logger="condo_voice.enrollment"):
        enrollment._log_batch(["front", "front2", "side_a"], batch)
    line = [r.getMessage() for r in caplog.records if "enrolment batch" in r.getMessage()]
    assert line and "front/front2=" in line[0] and "front/side_a=0.60" in line[0]
    assert "link " in line[0] and "floor 0.45" in line[0]
    # Session.save writes it for every batch, before the review can refuse,
    # and the helper is never handed the name.
    from pathlib import Path

    src = Path(enrollment.__file__).read_text(encoding="utf-8")
    after = src.split("            _log_batch(keys, vecs)\n", 1)
    assert len(after) == 2 and "refusal = review(vecs, name)" in after[1][:400]
    assert "def _log_batch(keys: list[str], vecs: list[np.ndarray]) -> None:" in src


def test_a_clean_batch_of_a_new_person_is_allowed(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "none.npz"))
    assert enrollment.review([ALICE, ALICE_AGAIN], "ต้า") is None


def test_a_batch_that_is_already_in_the_gallery_under_another_name_is_refused(
        monkeypatch, tmp_path):
    from app.config import settings

    path = tmp_path / "gallery.npz"
    faces.Gallery(["ต้า"], np.stack([ALICE]), [{"group": "staff"}]).save(path)
    monkeypatch.setattr(settings, "face_gallery", str(path))
    monkeypatch.setattr(settings, "face_threshold", 0.45)

    refusal = enrollment.review([ALICE_AGAIN], "มิกซ์")
    assert refusal is not None and "ต้า" in refusal


def test_one_front_shot_under_another_name_is_enough_to_refuse(monkeypatch, tmp_path):
    """2026-10-08: a robot scan matched a studio portrait of the same person,
    filed under the Thai spelling of her name, at 0.507/0.546 front-on; the
    side poses pulled the batch *mean* to 0.45 and it passed. From then on
    every frame of her had a runner-up of another name within the margin,
    so she was never named. Recognition judges single frames — so must this."""
    from app.config import settings

    def unit(*xs):
        v = np.zeros(512, np.float32)
        v[:len(xs)] = xs
        return v / np.linalg.norm(v)

    path = tmp_path / "gallery.npz"
    faces.Gallery(["แก้ว"], np.stack([unit(1)]), [{"group": "staff"}]).save(path)
    monkeypatch.setattr(settings, "face_gallery", str(path))
    front = unit(0.55, 0.835)                       # 0.55 against the portrait
    left, right = unit(0.38, 0.5, 0.778), unit(0.38, 0.5, -0.778)
    batch = [front, left, right]
    assert enrollment.batch_link(batch) >= enrollment.LINK_FLOOR       # one person
    assert float(np.mean(batch, axis=0) @ unit(1)) < settings.face_threshold
    refusal = enrollment.review(batch, "kaew")
    assert refusal is not None and "แก้ว" in refusal


# -- writing to disk, which happens last and only once -----------------------

def test_shots_are_saved_under_a_thai_name(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    crops = [np.full((90, 90, 3), 120, np.uint8) for _ in range(3)]

    total = enrollment.save_shots("คุณต้า", crops)

    assert total == 3
    assert len(list((tmp_path / "คุณต้า").glob("*.jpg"))) == 3


def test_a_second_session_adds_rather_than_replaces(monkeypatch, tmp_path):
    """Another angle later is the point; it must not cost the first one."""
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    crop = [np.full((90, 90, 3), 120, np.uint8)]

    enrollment.save_shots("ต้า", crop)
    assert enrollment.save_shots("ต้า", crop) == 2


def test_replace_drops_what_was_there(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    crop = [np.full((90, 90, 3), 120, np.uint8)]

    enrollment.save_shots("ต้า", crop + crop)
    assert enrollment.save_shots("ต้า", crop, replace=True) == 1


def test_enrolled_lists_who_has_shots(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    crop = [np.full((90, 90, 3), 120, np.uint8)]
    enrollment.save_shots("ต้า", crop)
    enrollment.save_shots("mix", crop + crop)

    assert enrollment.enrolled() == [("mix", 2), ("ต้า", 1)]


def test_nothing_enrolled_yet_is_an_empty_list_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "not_created")
    assert enrollment.enrolled() == []


# -- the station's own refusals --------------------------------------------

@pytest.mark.parametrize("name,pending,expect", [
    ("", [ALICE], "ชื่อ"),
    ("ต้า", [], "เก็บภาพ"),
])
def test_the_station_refuses_to_save_without_a_name_or_without_shots(
        monkeypatch, name, pending, expect):
    from scripts import enroll_app

    _station_with(monkeypatch,
                        [{"vec": v, "crop": np.zeros((4, 4, 3), np.uint8)}
                         for v in pending])
    result = enroll_app._save(name, replace=False)

    assert result["ok"] is False and expect in result["reason"]


def test_the_station_clears_the_pending_shots_after_saving(monkeypatch, tmp_path):
    """The next person is the next person.

    Shots left over would be enrolled a second time under the following
    name, which is the wrong-name failure arriving by the back door.
    """
    from app.config import settings
    from scripts import enroll_app

    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "none.npz"))
    _station_with(monkeypatch,
                        [{"vec": ALICE, "crop": np.full((40, 40, 3), 90, np.uint8)}])

    result = enroll_app._save("ต้า", replace=False)

    assert result["ok"] is True and result["total"] == 1
    assert enroll_app.SESSION.pending_count == 0


def test_a_refused_batch_stays_pending(monkeypatch, tmp_path):
    """Refusing must not also throw the shots away.

    The operator's next move is usually to fix the name, not to stand
    everybody in front of the camera again.
    """
    from app.config import settings
    from scripts import enroll_app

    path = tmp_path / "gallery.npz"
    faces.Gallery(["ต้า"], np.stack([ALICE]), [{"group": "staff"}]).save(path)
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path)
    monkeypatch.setattr(settings, "face_gallery", str(path))
    monkeypatch.setattr(settings, "face_threshold", 0.45)
    _station_with(monkeypatch,
                        [{"vec": ALICE_AGAIN, "crop": np.zeros((4, 4, 3), np.uint8)}])

    result = enroll_app._save("มิกซ์", replace=False)

    assert result["ok"] is False
    assert enroll_app.SESSION.pending_count == 1


# -- the guided capture ----------------------------------------------------

def _kps(yaw: float, pitch: float = 0.58, eye_width: float = 60.0):
    """Landmarks that produce this yaw and pitch, in the detector's order."""
    left_eye = np.array([100.0, 100.0])
    right_eye = left_eye + np.array([eye_width, 0.0])
    eye_mid = (left_eye + right_eye) / 2
    mouth_y = eye_mid[1] + 80.0
    nose = np.array([eye_mid[0] + yaw * eye_width, eye_mid[1] + pitch * 80.0])
    return np.array([left_eye, right_eye, nose,
                     [eye_mid[0] - 20, mouth_y], [eye_mid[0] + 20, mouth_y]])


def _face(yaw: float, width: int = 200, vec=None):
    return faces.Face((0, 0, width, width), 0.9, vec if vec is not None else ALICE,
                      _kps(yaw))


@pytest.mark.parametrize("yaw", [-0.4, -0.2, 0.0, 0.2, 0.4])
def test_head_pose_reads_the_yaw_it_was_given(yaw):
    got, _ = enrollment.head_pose(_face(yaw))
    assert got == pytest.approx(yaw, abs=1e-6)


def test_pitch_is_reported_but_gates_nothing():
    """Measured across 163 frontal portraits it spans 0.41 to 0.83 — three
    times the spread of yaw, on faces that are all facing the camera. It is
    reading facial proportion, not head position, so no step may use it."""
    from pathlib import Path

    src = Path(enrollment.__file__).read_text(encoding="utf-8")
    steps_block = src[src.index("STEPS = ["):src.index("FAR_RATIO")]
    assert "pitch" not in steps_block

    _, pitch = enrollment.head_pose(_face(0.0, ))
    assert pitch == pytest.approx(0.58, abs=1e-6)


def test_the_frontal_step_wants_a_head_that_is_not_turned():
    front = enrollment.STEPS[0]
    assert enrollment.step_ready(front, 0.05, 200, 200, set())[0] is True
    assert enrollment.step_ready(front, 0.30, 200, 200, set())[0] is False


def test_a_side_step_accepts_whichever_way_they_actually_turned():
    """The prompt names a direction read off six photographs by eye.

    If that reading is backwards the flow still has to work, so the first
    side step takes either sign and the second asks for the one left over.
    """
    side_a = enrollment.STEPS[1]
    assert enrollment.step_ready(side_a, -0.3, 200, 200, set())[0] is True
    assert enrollment.step_ready(side_a, +0.3, 200, 200, set())[0] is True


def test_the_second_side_step_wants_the_other_side():
    side_b = enrollment.STEPS[2]
    assert enrollment.step_ready(side_b, +0.3, 200, 200, {-1})[0] is True
    ready, why = enrollment.step_ready(side_b, -0.3, 200, 200, {-1})
    assert ready is False and "อีกด้าน" in why


def test_a_barely_turned_head_does_not_count_as_turned():
    """Otherwise the frontal shot satisfies the side step and the batch is
    three copies of one view."""
    assert enrollment.step_ready(enrollment.STEPS[1], 0.15, 200, 200, set())[0] is False


def test_the_step_back_wants_a_smaller_face():
    """The runtime meets people at whatever distance they stop at. A gallery
    built entirely at arm's length has only ever seen one scale."""
    far = enrollment.STEPS[3]
    assert enrollment.step_ready(far, 0.0, 200, 400, set())[0] is True
    assert enrollment.step_ready(far, 0.0, 380, 400, set())[0] is False


# -- five shots only mean something if they differ -------------------------

def test_a_shot_the_batch_already_has_is_not_novel():
    """Five frames of somebody holding still carry what one carries.

    This is the whole answer to "are five enough": count spread, not shots.
    """
    assert enrollment.is_novel(ALICE, [ALICE]) is False


def test_a_different_view_of_the_same_person_is_novel():
    assert enrollment.is_novel(ALICE_AGAIN, [ALICE]) is True


def test_the_first_shot_is_always_novel():
    assert enrollment.is_novel(ALICE, []) is True


def test_the_station_refuses_a_shot_that_adds_nothing(monkeypatch):
    from scripts import enroll_app

    monkeypatch.setattr(faces, "locate", lambda img, det_size=640: [_face(0.0)])
    monkeypatch.setattr(faces, "describe", lambda img, f, det_size=640: f)
    _station_with(monkeypatch, [
        {"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8),
         "step": "front", "width": 200, "side": 0}])

    result = enroll_app._capture(np.zeros((720, 1280, 3), np.uint8))
    assert result["ok"] is False


# -- a new page against an old server --------------------------------------

def test_the_page_and_the_server_agree_on_a_protocol_number():
    """The page is read off disk every load; the server is loaded once.

    A station left open across an edit therefore serves the new page from
    the old process, and the new page's features silently do not exist —
    which is exactly how an enrolment session collected twenty-four
    near-identical shots with no step list and no complaint. The number is
    the handshake; this test is what keeps the two copies of it together.
    """
    import re
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    in_page = int(re.search(r"const PROTOCOL = (\d+)", page).group(1))

    assert in_page == enroll_app.PROTOCOL


def test_the_page_refuses_to_run_against_a_mismatched_server():
    """And says which button to press, not just that something is wrong."""
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "checkVersion" in page
    assert "enroll-faces.cmd" in page


def test_the_page_never_swallows_a_failed_request():
    """The silence was the bug, not the mismatch.

    An empty `catch` around the polling loop meant a page talking to a
    server that could not answer looked exactly like a page with nobody in
    front of the camera.
    """
    import re
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    for body in re.findall(r"catch\s*\([^)]*\)\s*\{([^}]*)\}", page):
        stripped = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S).strip()
        assert stripped, "an empty catch block is back in the enrolment page"


# -- a station that is already running -------------------------------------

def test_a_free_port_reads_as_free():
    from scripts import enroll_app

    assert enroll_app._already_running(8901) is None


def test_a_station_of_the_same_version_is_recognised():
    """Then the second launch hands the browser over instead of failing."""
    import threading
    from http.server import ThreadingHTTPServer

    from scripts import enroll_app

    server = ThreadingHTTPServer(("127.0.0.1", 8902), enroll_app.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert enroll_app._already_running(8902) == enroll_app.PROTOCOL
    finally:
        server.shutdown()
        server.server_close()


def test_something_older_on_the_port_reads_as_older_not_as_free():
    """The failure this exists for.

    An old station kept the port, the new launch died on "address already in
    use", its console closed on a traceback, and the browser went on talking
    to the old server — which is where the twenty-four shots came from.
    Anything listening that cannot answer `/version` is old, and saying so
    beats a stack trace nobody sees.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from scripts import enroll_app

    class NoVersion(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 8903), NoVersion)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert enroll_app._already_running(8903) == -1
    finally:
        server.shutdown()
        server.server_close()


# -- a step that cannot be done is not a step ------------------------------

def test_the_step_back_is_skipped_when_there_is_no_room_for_it():
    """Two rules of mine contradicted each other on a desk camera.

    `FAR_RATIO` asked for a face 0.75x the one at step one — 89px for a
    sitter measured at 119px — while `usable_face` refuses anything under
    `FACE_MIN_PX` at 110px. No distance satisfies both, so the station stood
    somebody in front of a lens repeating an instruction with no answer.
    """
    assert enrollment.far_target(119, 110) is None
    assert enrollment.step_applies(enrollment.STEPS[3], 119, 110) is False


def test_the_step_back_is_offered_when_there_is_room():
    target = enrollment.far_target(200, 110)
    assert target is not None
    assert target >= 110 + enrollment.FAR_MIN_MARGIN
    assert enrollment.step_applies(enrollment.STEPS[3], 200, 110) is True


def test_the_pose_steps_always_apply():
    """Turning your head needs no room, so nothing may skip those."""
    for step in enrollment.STEPS:
        if step["axis"] == "yaw":
            assert enrollment.step_applies(step, 119, 110) is True


def test_the_station_drops_an_impossible_step_from_the_list(monkeypatch):
    """And marks it skipped rather than leaving it looking unfinished."""
    from scripts import enroll_app

    _station_with(monkeypatch, [
        {"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8),
         "step": "front", "width": 119, "side": 0}])

    state = enroll_app._state()
    far = next(s for s in state["steps"] if s["key"] == "far")

    assert far["skipped"] is True
    assert "ข้าม" in far["prompt"]
    assert state["step"] != "far"


def test_a_wide_enough_first_shot_keeps_the_step(monkeypatch):
    from scripts import enroll_app

    _station_with(monkeypatch, [
        {"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8),
         "step": "front", "width": 260, "side": 0}])

    far = next(s for s in enroll_app._state()["steps"] if s["key"] == "far")
    assert far["skipped"] is False


# -- one scale, everywhere -------------------------------------------------

def test_face_width_is_reported_in_camera_pixels_whatever_was_analysed(monkeypatch):
    """The preview sends a 640-wide copy; a capture sends the whole frame.

    Measuring on each and comparing both to the same thresholds was wrong
    twice: `FACE_MIN_PX` silently doubled during the preview, so people had
    to sit at half the distance the runtime actually needs, and the distance
    step compared a 640-scale width against a full-scale reference — the
    screen read 155px while the instruction demanded 307px, of the same face
    in the same second.
    """
    from scripts import enroll_app

    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=140)])

    small = np.zeros((360, 640, 3), np.uint8)
    full = np.zeros((720, 1280, 3), np.uint8)

    assert enroll_app._judge(small, full_width=1280)["width"] == 280
    assert enroll_app._judge(full, full_width=1280)["width"] == 140


def test_a_request_without_the_camera_width_is_taken_at_face_value(monkeypatch):
    """Nothing invents a scale it was not told about."""
    from scripts import enroll_app

    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=140)])

    assert enroll_app._judge(np.zeros((360, 640, 3), np.uint8))["width"] == 140


# -- the step that asks for something no code can see -----------------------

def test_the_smile_step_holds_longer_than_the_others():
    """Nothing here can see a smile, so the only honest gate is time.

    `faces.py` loads detection and recognition and nothing else, so the last
    step's instruction is checked by no one: the shot fired the instant the
    face came back to frontal, which on the owner's own enrolment was before
    the smile arrived. A smile detector is not the fix — room to obey the
    instruction is.
    """
    smile = [s for s in enrollment.STEPS if s["key"] == "front2"][0]
    other = [s for s in enrollment.STEPS if s["key"] == "front"][0]

    assert enrollment.hold_ms(smile) > enrollment.hold_ms(other)
    assert enrollment.hold_ms(other) == enrollment.HOLD_MS


def test_the_station_tells_the_page_how_long_to_hold(monkeypatch):
    """The page cannot know which step is the unverifiable one."""
    from scripts import enroll_app

    _station_with(monkeypatch, [])
    assert enroll_app._state()["hold_ms"] == enrollment.hold_ms(enrollment.STEPS[0])

    _station_with(monkeypatch, [
        {"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8),
         "step": s["key"], "width": 400, "side": 0}
        for s in enrollment.STEPS if s["key"] != "front2"])
    assert enroll_app._state()["hold_ms"] == enrollment.hold_ms(
        [s for s in enrollment.STEPS if s["key"] == "front2"][0])


def test_the_page_holds_for_as_long_as_the_step_asks():
    """A constant in the page would put the number back in the wrong file."""
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "lastLook.hold_ms" in page


# -- a spoken instruction on a machine with no Thai voice -------------------

def test_the_page_does_not_read_thai_with_an_english_voice():
    """The browser cannot say any of this, and asking it to was the bug.

    Measured on the showroom machine: SAPI5, OneCore, Windows Settings and
    Edge all offer the same three en-US voices and nothing Thai. So the page
    asks the server for audio instead — one path, not a fallback chain that
    sounds different depending on who is standing there.
    """
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", page)
    assert "speechSynthesis" not in code
    assert "/voice?text=" in code


def test_the_page_does_not_claim_to_speak_when_the_server_cannot():
    """A ticked box over silence is the sleep-screen level meter again."""
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "speechAvailable" in page
    assert "$('speak').checked = false" in page


def test_the_page_has_a_sound_that_needs_no_language_pack():
    """Whoever is being enrolled is looking at the lens, not at the panel."""
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "function beep(" in page
    assert "beep(" in page.split("async function shoot()")[1]


# -- Emma's voice, on a machine with no Thai voice --------------------------

def test_a_clip_is_not_rendered_twice(monkeypatch, tmp_path):
    """The first person of the day pays for a sentence; nobody else does."""
    from app import voice
    from app.config import settings

    calls = []

    def render(text, model):
        calls.append((text, model))
        return b"\0\0" * 2400, 24000

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    first = voice.clip("มองตรงมาที่กล้อง")
    second = voice.clip("มองตรงมาที่กล้อง")

    assert first == second and first.exists()
    assert len(calls) == 1


def test_the_cache_name_carries_the_voice_and_the_model(monkeypatch):
    """Two voices do not sound alike, so they cannot share a filename.

    The same rule the embedding cache learned: a key that leaves out what
    produced the bytes will serve the wrong ones and sound almost right.
    """
    from app import voice
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_voice", "Kore")
    kore = voice._key("สวัสดี", voice.MODELS[0])
    monkeypatch.setattr(settings, "gemini_voice", "Leda")
    leda = voice._key("สวัสดี", voice.MODELS[0])

    assert kore != leda
    assert kore != voice._key("สวัสดี", voice.MODELS[1])


def test_a_half_written_clip_is_never_served(monkeypatch, tmp_path):
    """A render killed halfway must not become a permanent cache hit."""
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    def die(text, model):
        (tmp_path / "voice").mkdir(parents=True, exist_ok=True)
        raise KeyboardInterrupt

    monkeypatch.setattr(voice, "_render", die)
    with pytest.raises(KeyboardInterrupt):
        voice.clip("ครึ่งทาง")

    assert voice.cached("ครึ่งทาง") is None


def test_no_key_means_no_voice_and_no_exception(monkeypatch, tmp_path):
    """Silence with a beep is a bad outcome; a traceback is a worse one."""
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(settings, "gemini_api_key", "")

    assert voice.clip("ไม่มีคีย์") is None


def test_a_failed_render_gives_up_quietly(monkeypatch, tmp_path):
    """The caller is a page standing between a colleague and a camera."""
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(voice, "_render",
                        lambda text, model: (_ for _ in ()).throw(RuntimeError("boom")))

    assert voice.clip("พังไปเลย") is None


def test_a_withdrawn_model_falls_through_to_the_next_one(monkeypatch, tmp_path):
    """`-preview` means withdrawable, and only that error may fall back.

    Falling back on any error at all would turn a five-second network hiccup
    into a permanent quiet downgrade — the rule `GEMINI_MODEL_FALLBACK`
    already follows for the live session.
    """
    from app import voice
    from app.config import settings

    tried = []

    def render(text, model):
        tried.append(model)
        if model == voice.MODELS[0]:
            raise RuntimeError("404 model not found")
        return b"\0\0" * 100, 24000

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    assert voice.clip("ถอยไปรุ่นก่อน") is not None
    assert tried == list(voice.MODELS)


def test_a_network_hiccup_does_not_switch_models(monkeypatch, tmp_path):
    from app import voice
    from app.config import settings

    tried = []

    def render(text, model):
        tried.append(model)
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    assert voice.clip("เน็ตสะดุด") is None
    assert tried == [voice.MODELS[0]]


def test_the_lines_said_at_every_enrolment_are_the_ones_warmed():
    """Warming the wrong sentences is warming nothing."""
    from app import voice
    from app.config import settings

    lines = voice.opening_lines()
    for step in enrollment.STEPS:
        assert step["prompt"] in lines
    assert voice.TAKEN in lines and voice.SAVED in lines


def test_the_station_warms_the_voice_off_the_request_path():
    """A page waiting on a render is the warm-deck bug wearing a new hat."""
    from pathlib import Path

    from scripts import enroll_app

    source = Path(enroll_app.__file__).read_text(encoding="utf-8")
    assert "threading.Thread(target=voice.warm, daemon=True).start()" in source


def test_the_saved_line_does_not_carry_the_name():
    """A name is a sentence nobody has rendered before.

    Speaking it would cost a round trip per colleague, at the one moment the
    queue is moving. The name is already on the screen in the green box.
    """
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "say_out_loud('บันทึกเรียบร้อย คนต่อไปได้เลย'" in page


def test_a_rate_limit_waits_instead_of_changing_voices(monkeypatch, tmp_path):
    """This one was measured, not imagined.

    Warming seven lines back to back spends the free tier's three requests a
    minute. Treating that as "model unavailable" sent the rest to the older
    model, and the station read four instructions in one voice and three in
    another — two embedding spaces mixed, in audio. A per-minute limit is
    waited out; it is not a different model.
    """
    from app import voice
    from app.config import settings

    seen = []
    slept = []

    def render(text, model):
        seen.append(model)
        if len(seen) < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
        return b"\0\0" * 100, 24000

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(voice.time, "sleep", slept.append)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    path = voice.clip("รอโควตา", attempts=6)

    assert path == tmp_path / "voice" / voice._key("รอโควตา", voice.MODELS[0])
    assert seen == [voice.MODELS[0]] * 3
    assert slept == [5.0, 10.0]


def test_a_rate_limit_inside_a_request_does_not_wait(monkeypatch, tmp_path):
    """The wait *is* the failure when somebody is standing at the camera."""
    from app import voice
    from app.config import settings

    slept = []
    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", lambda text, model: (_ for _ in ()).throw(
        RuntimeError("429 rate limit")))
    monkeypatch.setattr(voice.time, "sleep", slept.append)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    assert voice.clip("ไม่รอ") is None
    assert slept == []


# -- two audiences, two sentences ------------------------------------------

def test_the_screen_gets_the_number_and_the_ear_does_not(monkeypatch):
    """"155px ขอ 169px" is for whoever runs the station, not for the person
    standing at the lens being asked to move.
    """
    from scripts import enroll_app

    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=60)])

    look = enroll_app._judge(FRAME, full_width=1280)

    assert "px" in look["reason"]
    assert look["say"] == enrollment.SPOKEN["closer"]


def test_every_sentence_the_station_speaks_is_one_that_gets_rendered():
    """A phrase assembled at runtime is a phrase nothing rendered.

    The free tier allows three renders a minute, so the moment somebody
    needs telling is the worst moment to ask for one. Every fixed line the
    page can speak has to be in the warm list.
    """
    from app import voice

    lines = voice.opening_lines()
    for spoken in enrollment.SPOKEN.values():
        assert spoken in lines
    for step in enrollment.STEPS:
        assert step["prompt"] in lines


def test_the_page_speaks_the_servers_sentence_not_its_own_edit_of_one():
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "look.say" in page
    # The old version cut the "(120px)" tail off the on-screen text with a
    # regex. Building a sentence in the page is how one stops matching a
    # rendered clip.
    assert "look.reason.replace" not in page


def test_the_crowd_warning_counts_on_screen_and_stays_fixed_out_loud(monkeypatch):
    """"มี 2 หน้า" and "มี 3 หน้า" are two clips for one situation."""
    from scripts import enroll_app

    monkeypatch.setattr(faces, "locate", lambda img, det_size=640: [
        _face(0.0, width=200), _face(0.0, width=180)])

    look = enroll_app._judge(FRAME, full_width=1280)

    assert "2" in look["reason"]
    assert look["say"] == enrollment.SPOKEN["crowd"]


def test_a_daily_quota_is_not_waited_out(monkeypatch, tmp_path):
    """Measured: the free tier renders ten sentences a day, per model.

    Sleeping through that is 135 seconds spent to be refused again — the same
    429 the per-minute limit sends, and the only difference is a word inside
    it. Waiting is right only when the thing being waited for arrives.
    """
    from app import voice
    from app.config import settings

    seen, slept = [], []

    def render(text, model):
        seen.append(model)
        raise RuntimeError(
            "429 RESOURCE_EXHAUSTED quotaId: "
            "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'")

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(voice.time, "sleep", slept.append)
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    assert voice.clip("หมดโควตาวันนี้", attempts=6) is None
    assert slept == []
    # One model, one try. Not the next model: today's remaining lines in a
    # second voice is worse than today's remaining lines missing.
    assert seen == [voice.MODELS[0]]


def test_the_station_says_how_much_of_its_voice_it_has(monkeypatch, tmp_path):
    """"Emma says three things and then stops" must be diagnosable.

    It is the microphone-nobody-was-holding shape again: a half-rendered
    cache and a broken one look identical from the chair.
    """
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    assert "0/" in voice.state()

    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(voice, "_render", lambda text, model: (b"\x01\x01" * 50, 24000))
    lines = voice.opening_lines()
    voice.clip(lines[0])

    assert voice.state().startswith("1/")
    assert "10/day" in voice.state()


def test_a_cache_in_two_voices_says_so(monkeypatch, tmp_path):
    """Half the instructions in one voice is the mixed-embedding bug, heard."""
    from app import voice

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    (tmp_path / "voice").mkdir()
    lines = voice.opening_lines()
    for line, model in zip(lines[:2], voice.MODELS):
        (tmp_path / "voice" / voice._key(line, model)).write_bytes(b"x")

    assert "2 different models" in voice.state()


def test_the_day_s_quota_is_only_discovered_once(monkeypatch, tmp_path):
    """Five doomed round trips appeared in the owner's first real log.

    Each one happens while somebody stands at the camera waiting to be told
    what to do. The daily cap is the one failure that is knowably permanent
    for the rest of the run, so it is the one worth latching.
    """
    from app import voice
    from app.config import settings

    calls = []

    def render(text, model):
        calls.append(model)
        raise RuntimeError("429 quotaId GenerateRequestsPerDayPerProject-FreeTier")

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", render)
    monkeypatch.setattr(voice, "_spent", set())
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    assert voice.clip("ประโยคแรก") is None
    spent_after_one = list(calls)
    assert voice.clip("ประโยคที่สอง") is None

    assert calls == spent_after_one          # nothing asked a second time
    assert calls == [voice.MODELS[0]]


def test_a_sentence_waits_for_the_one_before_it():
    """"เก็บแล้ว" and the next instruction land in the same tick.

    The second used to cut the first off a syllable in — the audio-lead bug
    in miniature, where the thing that changed the state raced the thing
    announcing it.
    """
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "player.onended" in page
    assert "queue.push(text)" in page
    # And nothing inside say_out_loud cuts a clip off mid-word any more.
    body = page.split("function say_out_loud")[1].split(chr(10) + "}")[0]
    assert "player.pause()" not in body
    assert "player.src" not in body


def test_one_cache_keeps_one_voice(monkeypatch, tmp_path):
    """Whatever rendered the clips that exist is the voice this station has.

    A directory in two voices is the mixed-embedding bug, heard rather than
    ranked, and the only symptom is that something sounds slightly wrong to
    somebody who cannot say why.
    """
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_spent", set())
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    older = voice.MODELS[1]
    (tmp_path / "voice").mkdir()
    line = voice.opening_lines()[0]
    (tmp_path / "voice" / voice._key(line, older)).write_bytes(b"x")

    asked = []
    monkeypatch.setattr(voice, "_render", lambda text, model: (
        asked.append(model), (b"\x01\x01" * 50, 24000))[1])

    voice.clip("ประโยคใหม่")
    assert asked == [older]


# -- the voice that does not need a quota ------------------------------------

def test_the_local_voice_renders_without_a_key_or_a_quota(monkeypatch, tmp_path):
    """The free tier renders ten sentences a day; a name is always a new
    sentence. The local provider exists so the station can speak names and
    keep speaking when the network is gone — no key, no _spent latch."""
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(settings, "tts_provider", "local")
    monkeypatch.setattr(settings, "gemini_api_key", "")   # deliberately no key
    monkeypatch.setattr(voice, "_render_local",
                        lambda text: (b"\x01\x00" * 220, 22050))

    path = voice.clip("บันทึก โชกุน เรียบร้อย")
    assert path == tmp_path / "voice" / voice._key("บันทึก โชกุน เรียบร้อย",
                                                   voice.LOCAL_TAG)


def test_a_failed_local_render_is_silence_not_a_traceback(monkeypatch, tmp_path):
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(settings, "tts_provider", "local")

    assert voice.clip("พังเงียบๆ") is None   # conftest's _render_local raises


def test_a_cache_holding_gemini_and_local_voices_is_named_as_mixed(
        monkeypatch, tmp_path):
    """Same rule whichever pair of voices it is."""
    from app import voice

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    (tmp_path / "voice").mkdir()
    line = voice.opening_lines()[0]
    (tmp_path / "voice" / voice._key(line, voice.MODELS[0])).write_bytes(b"x")
    (tmp_path / "voice" / voice._key(line, voice.LOCAL_TAG)).write_bytes(b"x")

    assert "2 different models" in voice.state()


def test_the_local_provider_counts_only_its_own_clips(monkeypatch, tmp_path):
    """Six Gemini clips are six clips in the wrong voice once the provider
    is local — counting them as ready would hide exactly the mixed-voice
    flow the count exists to prevent."""
    from app import voice
    from app.config import settings

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    (tmp_path / "voice").mkdir()
    line = voice.opening_lines()[0]
    (tmp_path / "voice" / voice._key(line, voice.MODELS[0])).write_bytes(b"x")

    monkeypatch.setattr(settings, "tts_provider", "local")
    assert voice.cached(line) is None
    assert voice.state().startswith("0/")


# -- the portrait bar and the greeting range are different knobs -------------

def test_lowering_the_greeting_range_does_not_lower_the_portrait_bar(monkeypatch):
    """FACE_MIN_PX=50 made the doorway wake the robot — and the station
    quietly started accepting 62px portraits, because both read the same
    setting. A far-away greeting degrades one sentence; a far-away portrait
    degrades every match that person ever gets."""
    from app.config import settings

    monkeypatch.setattr(settings, "face_min_px", 50)
    assert enrollment.portrait_min_px() == 110

    # A machine that deliberately *raises* the range raises the bar too.
    monkeypatch.setattr(settings, "face_min_px", 150)
    assert enrollment.portrait_min_px() == 150


def test_the_station_judges_against_the_portrait_bar(monkeypatch):
    from app.config import settings
    from scripts import enroll_app

    monkeypatch.setattr(settings, "face_min_px", 50)
    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=62)])

    look = enroll_app._judge(FRAME, full_width=1280)
    assert look["ok"] is False
    assert "110px" in look["reason"]


def test_the_countdown_waits_for_emma_to_finish_the_sentence():
    """The smile clip runs 3.4s against a 2.5s hold; counted concurrently,
    the shutter fired before the word "ยิ้ม" was said — the last portrait of
    the owner's own enrolment is a man listening, not smiling."""
    from pathlib import Path

    from scripts import enroll_app

    page = Path(enroll_app.PAGE).read_text(encoding="utf-8")
    assert "if (lastLook.ok && playing) {" in page
    # And that branch resets the countdown rather than pausing mid-count.
    waiting = page.split("if (lastLook.ok && playing) {")[1].split("} else if")[0]
    assert "holdingSince = 0" in waiting


# -- five steps promised, five steps delivered -------------------------------

def test_the_first_shot_leaves_room_for_the_step_back():
    """A first portrait too small has no step-back — and the station used
    to find that out after promising five steps and delivering four
    ("หายไปหนึ่ง"). The number is the step-back solved backwards."""
    assert enrollment.first_shot_min_px(min_px=110) == 163     # ceil(122 / 0.75)
    assert enrollment.far_target(163, min_px=110) == 122
    assert enrollment.far_target(162, min_px=110) is None


def test_the_station_asks_for_that_room_before_the_first_shot(monkeypatch):
    from scripts import enroll_app

    _station_with(monkeypatch, [])
    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=140)])

    look = enroll_app._judge(FRAME, full_width=1280)
    assert look["ok"] is False
    assert "163px" in look["reason"]

    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(0.0, width=200)])
    assert enroll_app._judge(FRAME, full_width=1280)["ok"] is True


def test_later_shots_only_need_the_portrait_bar(monkeypatch):
    """The room is needed once; a 140px face on step three is fine."""
    from scripts import enroll_app

    _station_with(monkeypatch, [
        {"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8),
         "step": "front", "width": 200, "side": 0}])
    monkeypatch.setattr(faces, "locate",
                        lambda img, det_size=640: [_face(-0.3, width=140)])

    look = enroll_app._judge(FRAME, full_width=1280)
    assert "163px" not in look["reason"]


# -- a name is a folder, and the folder can be deleted -----------------------
#
# The names below are made up. data/faces/ holds real people; nothing here
# reads or writes it (ENROLLED and the gallery are moved to tmp_path).

@pytest.mark.parametrize("raw,expect", [
    ("ทดสอบ", "ทดสอบ"),
    ("คุณทดสอบ ใจดี", "คุณทดสอบ ใจดี"),
    ("  ทดสอบ  ", "ทดสอบ"),
    ("ทด   สอบ", "ทด สอบ"),
    ("ทด\u00a0สอบ", "ทด สอบ"),              # a pasted no-break space is still a space
    ("O'Neil", "O'Neil"),
    ("Jean-Luc", "Jean-Luc"),
    ("J. Doe", "J. Doe"),
    ("tester2", "tester2"),
    ("ก" * 40, "ก" * 40),
])
def test_a_real_name_is_accepted_and_tidied(raw, expect):
    assert enrollment.safe_person_name(raw) == expect


@pytest.mark.parametrize("raw", [
    "", "   ",
    "..", "../x", "..\\..\\app", "a..b",
    "a/b", "a\\b", "C:x", "C:\\x",
    ".hidden", "ทดสอบ.",
    "CON", "con", "con.txt", "Con .txt", "NUL", "aux.json", "COM1", "lpt9.x", "COM¹",
    "ก" * 41,
    "a\x00b", "a\tb", "a\nb", "ทดสอบ\u200b",     # control / invisible
    "<b>", "a*b", "a?b", 'a"b', "a|b",
])
def test_anything_that_is_not_just_a_name_is_refused(raw):
    """The station was 127.0.0.1 only; the call screen puts this save behind
    a LAN endpoint, where `..\\..\\app` with replace=True is an rmtree on the
    application. The refusal is a sentence for the operator, in Thai."""
    with pytest.raises(ValueError) as exc:
        enrollment.safe_person_name(raw)
    assert re.search(r"[\u0e00-\u0e7f]", str(exc.value)), "the reason is shown on screen"


def _outside(tmp_path):
    """An enrolment store at tmp/data/faces/enrolled with a victim beside it."""
    root = tmp_path / "data" / "faces" / "enrolled"
    root.mkdir(parents=True)
    victim = tmp_path / "data" / "app"
    victim.mkdir(parents=True)
    (victim / "keep.txt").write_text("still here", encoding="utf-8")
    return root, victim


@pytest.mark.parametrize("name", ["..\\..\\app", "../../app", "..", "C:\\"])
def test_save_shots_never_deletes_outside_the_enrolment_folder(monkeypatch, tmp_path, name):
    root, victim = _outside(tmp_path)
    monkeypatch.setattr(enrollment, "ENROLLED", root)

    with pytest.raises(ValueError):
        enrollment.save_shots(name, [np.full((40, 40, 3), 90, np.uint8)], replace=True)

    assert (victim / "keep.txt").read_text(encoding="utf-8") == "still here"
    assert not (tmp_path / "data" / "faces" / "people.json").exists(), \
        "a refused name must not get a consent record either"


def test_the_folder_check_holds_even_if_the_name_rule_let_something_through(
        monkeypatch, tmp_path):
    """Two locks on one door. With the name rule switched off, where the
    path *resolves* is still checked before anything is created or deleted."""
    root, victim = _outside(tmp_path)
    monkeypatch.setattr(enrollment, "ENROLLED", root)
    monkeypatch.setattr(enrollment, "safe_person_name", lambda raw: raw)

    with pytest.raises(ValueError):
        enrollment.save_shots("../../app", [np.full((40, 40, 3), 90, np.uint8)],
                              replace=True)

    assert (victim / "keep.txt").exists()


def test_save_shots_saves_under_the_tidied_name_and_reports_what_it_wrote(
        monkeypatch, tmp_path):
    """One person, one folder, one consent record — however the name was typed."""
    from app import consent

    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    written: list = []

    total = enrollment.save_shots("  ทด   สอบ ", [np.full((40, 40, 3), 90, np.uint8)] * 2,
                                  written=written)

    assert total == 2
    assert [p.parent.name for p in written] == ["ทด สอบ", "ทด สอบ"]
    assert all(p.is_file() for p in written)
    assert list(consent.load()) == ["ทด สอบ"]


# -- one guided capture, used by the station and the call screen -------------

class _FakeFace:
    """What `faces.locate` hands back, and nothing more: bbox, kps, vec, area."""

    def __init__(self, yaw: float, width: int, vec):
        self.bbox = (0, 0, width, width)
        self.kps = _kps(yaw)
        self.vec = vec
        self.det_score = 0.9

    @property
    def area(self) -> int:
        return self.bbox[2] * self.bbox[3]


def _view(i: int) -> np.ndarray:
    """Distinct views of one person: every pair 0.86 alike — novel (< 0.97)
    and in agreement (> 0.45)."""
    v = np.zeros(512, np.float32)
    v[0], v[i] = 1.0, 0.4
    return v / np.linalg.norm(v)


#: One pass through the five steps, with the refusals in between that the
#: station meets in real life: too small, too small for the step-back, a
#: crowd, the same shot twice, the same side twice, not stepped back far
#: enough, and nobody there.
WALK = [
    [_FakeFace(0.0, 60, _view(1))],
    [_FakeFace(0.0, 140, _view(1))],
    [_FakeFace(0.0, 200, _view(1)), _FakeFace(0.0, 190, _view(2))],
    [_FakeFace(0.0, 200, _view(1))],                   # front
    [_FakeFace(-0.3, 200, _view(1))],                  # turned, but the same vector
    [_FakeFace(-0.3, 200, _view(2))],                  # side_a
    [_FakeFace(-0.3, 200, _view(3))],                  # that side again
    [_FakeFace(0.3, 200, _view(3))],                   # side_b
    [_FakeFace(0.0, 190, _view(4))],                   # not far enough back
    [_FakeFace(0.0, 140, _view(4))],                   # far
    [_FakeFace(0.0, 200, _view(5))],                   # front2
    [],
]
#: The vectors the batch ends up holding, in shot order.
WALK_KEPT = [1, 2, 3, 4, 5]


def _walk(monkeypatch, judge, capture) -> list[dict]:
    monkeypatch.setattr(faces, "describe", lambda img, f, det_size=640: f)
    out = []
    for found in WALK:
        monkeypatch.setattr(faces, "locate", lambda img, det_size=640, found=found: list(found))
        out.append(judge(FRAME, 1280))
        out.append(judge(np.zeros((360, 640, 3), np.uint8), 1280))
        out.append(capture(FRAME, 1280))
    return out


def test_the_station_is_a_session_and_nothing_else():
    """Not "they agree" — the station holds no rule of its own to drift."""
    from pathlib import Path

    from scripts import enroll_app

    assert isinstance(enroll_app.SESSION, enrollment.Session)
    assert enroll_app._decode is enrollment.decode_data_url
    assert enroll_app._thumb is enrollment.thumbnail
    assert enroll_app._scale_to_camera is enrollment.scale_to_camera
    code = Path(enroll_app.__file__).read_text(encoding="utf-8")
    for rule in ("faces.locate", "step_ready", "is_novel", "usable_face", "STEPS"):
        assert rule not in code, f"{rule} is back in the station"


def test_the_station_and_a_session_answer_every_frame_the_same(monkeypatch, tmp_path):
    from app.config import settings
    from scripts import enroll_app

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "none.npz"))
    station = _station_with(monkeypatch, [])
    session = enrollment.Session()

    via_station = _walk(monkeypatch, enroll_app._judge, enroll_app._capture)
    direct = _walk(monkeypatch, session.judge, session.capture)

    assert via_station == direct
    assert station.pending_count == session.pending_count == 5
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "a" / "enrolled")
    saved_by_station = enroll_app._save("ทดสอบ", replace=False)
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "b" / "enrolled")
    assert saved_by_station == session.save("ทดสอบ")


def test_a_session_walks_the_five_steps_and_refuses_on_the_way(monkeypatch):
    session = enrollment.Session()
    results = _walk(monkeypatch, session.judge, session.capture)
    captures = results[2::3]

    assert [c["ok"] for c in captures] == [
        False, False, False, True, False, True, False, True, False, True, True, False]
    assert captures[4]["reason"] == "ภาพนี้เหมือนใบเดิม ลองขยับอีกนิด"
    assert captures[6]["reason"] == "หันไปอีกด้านหนึ่ง"
    assert results[3]["reason"].startswith("เข้ามาใกล้อีกนิด (140px ขอ 163px")
    assert results[6]["say"] == enrollment.SPOKEN["crowd"]
    assert results[-3]["say"] == enrollment.SPOKEN["no_face"]
    last = captures[-2]
    assert [s["step"] for s in last["shots"]] == [s["key"] for s in enrollment.STEPS]
    assert all(s["thumb"].startswith("data:image/jpeg;base64,") for s in last["shots"])
    assert last["step"] is None and last["prompt"] == "ครบแล้ว — กดบันทึกได้"


def test_saving_into_the_live_gallery_needs_no_rebuild(monkeypatch, tmp_path):
    """The call screen: the person enrolled is standing there waiting to be
    known. The gallery gets the rows a rebuild would write for them — the
    captured vectors, in shot order, each pointing at its own photo."""
    from app import consent
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    session = enrollment.Session()
    _walk(monkeypatch, session.judge, session.capture)

    result = session.save("ทดสอบ", add_to_gallery=True)

    assert result["ok"] is True and result["total"] == 5
    assert result["gallery_total"] == 5 and session.pending_count == 0
    g = faces.Gallery.load(tmp_path / "gallery.npz")
    assert g.names == ["ทดสอบ"] * 5
    assert np.allclose(g.vecs, np.stack([_view(i) for i in WALK_KEPT]))
    for meta in g.meta:
        assert meta["group"] == "enrolled" and meta["source"] == "camera"
        assert meta["consent"] == consent.OK
        assert (tmp_path / meta["photo"]).is_file()
    shots = sorted(p.name for p in (tmp_path / "enrolled" / "ทดสอบ").glob("*.jpg"))
    assert [m["photo"] for m in g.meta] == [f"enrolled/ทดสอบ/{s}" for s in shots]


def test_the_station_save_still_leaves_the_gallery_alone(monkeypatch, tmp_path):
    """Its documented second step is the rebuild; nothing changed there."""
    from app.config import settings
    from scripts import enroll_app

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    _station_with(monkeypatch, [])
    _walk(monkeypatch, enroll_app._judge, enroll_app._capture)

    result = enroll_app._save("ทดสอบ", replace=False)

    assert result["ok"] is True and "gallery_total" not in result
    assert not (tmp_path / "gallery.npz").exists()


def test_a_gallery_that_refuses_the_append_does_not_unsave_the_person(monkeypatch, tmp_path):
    """The shots are on disk and consent recorded; "not saved" would invite
    a second save of the same person. The refusal rides beside the success."""
    from app.config import settings

    stale = tmp_path / "gallery.npz"
    faces.Gallery(["อื่น"], np.stack([_view(9)]), [{}], model_tag="other/model").save(stale)
    before = stale.read_bytes()
    monkeypatch.setattr(settings, "face_gallery", str(stale))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    session = enrollment.Session()
    _walk(monkeypatch, session.judge, session.capture)

    result = session.save("ทดสอบ", add_to_gallery=True)

    assert result["ok"] is True and result["total"] == 5
    assert result["gallery_total"] is None and "ไม่เขียนทับ" in result["gallery_reason"]
    assert stale.read_bytes() == before


def test_a_double_tap_saves_the_batch_once(monkeypatch, tmp_path):
    """Two saves of one batch were ten files and every vector twice."""
    import threading
    import time

    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    session = enrollment.Session()
    _walk(monkeypatch, session.judge, session.capture)

    first_in = threading.Event()
    release = threading.Event()
    real_review = enrollment.review

    def slow_review(vecs, name):
        if not first_in.is_set():
            first_in.set()
            release.wait(5)
        return real_review(vecs, name)

    monkeypatch.setattr(enrollment, "review", slow_review)
    results = []

    def tap():
        results.append(session.save("ทดสอบ", add_to_gallery=True))

    one, two = threading.Thread(target=tap), threading.Thread(target=tap)
    one.start()
    first_in.wait(5)
    two.start()
    time.sleep(0.2)
    release.set()
    one.join(5)
    two.join(5)

    assert sorted(r["ok"] for r in results) == [False, True]
    assert len(list((tmp_path / "enrolled" / "ทดสอบ").glob("*.jpg"))) == 5
    assert len(faces.Gallery.load(tmp_path / "gallery.npz")) == 5


def test_a_bad_name_leaves_the_batch_waiting(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    session = enrollment.Session(pending=[{"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8)}])

    result = session.save("../ทดสอบ")

    assert result["ok"] is False and "ชื่อ" in result["reason"]
    assert session.pending_count == 1
    assert not (tmp_path / "enrolled").exists()


@pytest.mark.parametrize("body", ["", "data:image/jpeg;base64,", "not a url", 123, None,
                                  "data:image/jpeg;base64,bm90IGFuIGltYWdl"])
def test_a_garbage_frame_is_none_not_an_exception(body):
    """First thing a LAN endpoint's body reaches."""
    assert enrollment.decode_data_url(body) is None


def test_a_real_frame_round_trips_through_a_data_url():
    import base64

    import cv2

    ok, buf = cv2.imencode(".png", np.full((12, 16, 3), 77, np.uint8))
    frame = enrollment.decode_data_url(
        "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode())
    assert frame is not None and frame.shape == (12, 16, 3)


@pytest.mark.parametrize("full_width", [None, 0, -5, "abc", float("nan"), float("inf")])
def test_a_camera_width_that_is_not_a_width_is_not_a_scale(full_width):
    assert enrollment.scale_to_camera(np.zeros((360, 640, 3), np.uint8), full_width) == 1.0


# -- add_to_gallery ------------------------------------------------------------

def _gallery_here(monkeypatch, tmp_path):
    from app.config import settings

    path = tmp_path / "gallery.npz"
    monkeypatch.setattr(settings, "face_gallery", str(path))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    return path


def test_add_to_gallery_creates_the_gallery_when_there_is_none(monkeypatch, tmp_path):
    path = _gallery_here(monkeypatch, tmp_path)

    assert enrollment.add_to_gallery("ทดสอบ", [_view(1), _view(2)],
                                     ["enrolled/ทดสอบ/a.jpg", "enrolled/ทดสอบ/b.jpg"]) == 2

    g = faces.Gallery.load(path)
    assert g.names == ["ทดสอบ", "ทดสอบ"] and g.model_tag == faces.MODEL_TAG
    assert g.meta[1] == {"group": "enrolled", "photo": "enrolled/ทดสอบ/b.jpg",
                         "source": "camera", "consent": "missing"}


def test_add_to_gallery_appends_and_keeps_everybody_else(monkeypatch, tmp_path):
    path = _gallery_here(monkeypatch, tmp_path)
    faces.Gallery(["อื่น", "ทดสอบ"], np.stack([_view(7), _view(8)]),
                  [{"group": "broker"}, {"group": "enrolled", "source": "camera"}]).save(path)

    assert enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["enrolled/ทดสอบ/c.jpg"]) == 2

    g = faces.Gallery.load(path)
    assert g.names == ["อื่น", "ทดสอบ", "ทดสอบ"]
    assert g.meta[0] == {"group": "broker"}
    assert np.allclose(g.vecs[:2], np.stack([_view(7), _view(8)]))


def test_a_colleague_added_from_the_camera_stays_a_colleague(monkeypatch, tmp_path):
    """Same rule as the rebuild: otherwise the greeting depends on which
    vector happened to be nearest."""
    path = _gallery_here(monkeypatch, tmp_path)
    faces.Gallery(["ทดสอบ"], np.stack([_view(7)]), [{"group": "staff"}]).save(path)

    enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["enrolled/ทดสอบ/c.jpg"])

    assert faces.Gallery.load(path).meta[-1]["group"] == "staff"


@pytest.mark.parametrize("content", ["other-model", "garbage", "truncated"])
def test_add_to_gallery_never_overwrites_a_gallery_it_cannot_read(monkeypatch, tmp_path, content):
    """Overwriting would silently delete everybody else in it."""
    path = _gallery_here(monkeypatch, tmp_path)
    if content == "other-model":
        faces.Gallery(["อื่น"], np.stack([_view(7)]), [{}], model_tag="other/model").save(path)
    elif content == "garbage":
        path.write_bytes(b"this is not a zip file at all")
    else:
        faces.Gallery(["อื่น"], np.stack([_view(7)]), [{}]).save(path)
        path.write_bytes(path.read_bytes()[:200])
    before = path.read_bytes()

    with pytest.raises(ValueError) as exc:
        enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["enrolled/ทดสอบ/c.jpg"])

    assert "ไม่เขียนทับ" in str(exc.value)
    assert path.read_bytes() == before


def test_add_to_gallery_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    """The gallery, and its (empty, permanent) cross-process lock file —
    deleting that on release would race the next opener. Nothing else."""
    path = _gallery_here(monkeypatch, tmp_path)
    enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["x"])
    enrollment.add_to_gallery("ทดสอบ", [_view(2)], ["y"])
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == sorted(
        [path.name, faces.lock_path(path).name])
    assert faces.lock_path(path).stat().st_size == 0


def test_add_to_gallery_does_not_put_back_somebody_who_withdrew(monkeypatch, tmp_path):
    """The rebuild keeps revoked people out; this must not be the one path
    that appends them again."""
    from app import consent

    path = _gallery_here(monkeypatch, tmp_path)
    consent.revoke("ทดสอบ")

    with pytest.raises(ValueError):
        enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["x"])
    assert not path.exists()


def test_add_to_gallery_refuses_a_bad_name_and_mismatched_lists(monkeypatch, tmp_path):
    path = _gallery_here(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        enrollment.add_to_gallery("../x", [_view(1)], ["x"])
    with pytest.raises(ValueError):
        enrollment.add_to_gallery("ทดสอบ", [_view(1), _view(2)], ["x"])
    assert not path.exists()


def test_two_appends_at_once_lose_nobody(monkeypatch, tmp_path):
    """Two kiosks, one process, one second: load-append-save twice over
    must not end with one person's rows missing."""
    import threading
    import time

    path = _gallery_here(monkeypatch, tmp_path)
    real_load = faces.Gallery.load

    def slow_load(p):
        g = real_load(p)
        time.sleep(0.05)
        return g

    faces.Gallery(["อื่น"], np.stack([_view(7)]), [{}]).save(path)
    monkeypatch.setattr(faces.Gallery, "load", staticmethod(slow_load))
    names = [f"ทดสอบ{i}" for i in range(6)]
    threads = [threading.Thread(target=enrollment.add_to_gallery, args=(n, [_view(1)], ["x"]))
               for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert sorted(real_load(path).names) == sorted(["อื่น"] + names)


def test_an_append_while_the_gallery_is_locked_refuses_and_writes_nothing(monkeypatch, tmp_path):
    """The other holder is a rebuild, which replaces the whole file from a
    listing it took earlier. An append into the file it is about to replace
    was on the screen as "known" and gone when the rebuild finished; now it
    waits a moment and refuses in Thai."""
    path = _gallery_here(monkeypatch, tmp_path)
    faces.Gallery(["อื่น"], np.stack([_view(7)]), [{}]).save(path)
    before = path.read_bytes()
    monkeypatch.setattr(enrollment, "GALLERY_LOCK_WAIT_S", 0.2)

    with faces.file_lock(path, 1.0, "unused"):
        with pytest.raises(faces.LockBusy) as exc:
            enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["x"])

    assert re.search(r"[฀-๿]", str(exc.value))
    assert "build-face-gallery" in str(exc.value)
    assert path.read_bytes() == before


def test_add_to_gallery_refuses_while_the_consent_file_cannot_be_read(monkeypatch, tmp_path):
    """It might be exactly where this person's revocation is."""
    from app import consent

    path = _gallery_here(monkeypatch, tmp_path)
    consent.people_path().parent.mkdir(parents=True, exist_ok=True)
    consent.people_path().write_bytes(b"{ not json")

    with pytest.raises(ValueError) as exc:
        enrollment.add_to_gallery("ทดสอบ", [_view(1)], ["x"])

    assert "people.json" in str(exc.value)
    assert not path.exists()


# -- a save that fails part-way leaves nothing half-filed --------------------------
#
# save_shots writes the shots first and the consent record last. An OSError
# in between (a full disk, a people.json another program holds past the
# retries) used to leave shots with no record and a folder, answer 500
# "ลองใหม่", and then refuse the retry as "ชื่อนี้มีอยู่แล้ว" — so the
# operator typed a second name and one face was filed twice (review,
# 2026-09-29). Everything below is in tmp_path.

def _walked_session(monkeypatch, tmp_path) -> enrollment.Session:
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    session = enrollment.Session()
    _walk(monkeypatch, session.judge, session.capture)
    assert session.pending_count == 5
    return session


def _consent_write_fails(monkeypatch, times: int = 1) -> list:
    """The record's write dies the way Windows kills it when another program
    holds people.json past `write_atomically`'s retries."""
    from app import consent

    real = consent.save
    failed: list = []

    def dies(people):
        if len(failed) < times:
            failed.append(1)
            raise PermissionError(13, "held by another program")
        return real(people)

    monkeypatch.setattr(consent, "save", dies)
    return failed


def test_a_consent_write_that_fails_takes_the_shots_back(monkeypatch, tmp_path):
    from app import consent

    session = _walked_session(monkeypatch, tmp_path)
    _consent_write_fails(monkeypatch)

    result = session.save("ทดสอบ", add_to_gallery=True)

    assert result["ok"] is False
    assert re.search(r"[฀-๿]", result["reason"]), "a sentence for the screen"
    assert not (tmp_path / "enrolled" / "ทดสอบ").exists(), "shots with no record were left"
    assert consent.load() == {}
    assert not (tmp_path / "gallery.npz").exists()
    assert session.pending_count == 5, "the batch is kept for the retry"

    again = session.save("ทดสอบ", add_to_gallery=True)                 # the disk is back

    assert again["ok"] is True and again["total"] == 5 and again["gallery_total"] == 5
    assert consent.status("ทดสอบ") == consent.OK


def test_a_shot_that_fails_to_write_takes_back_the_ones_before_it(monkeypatch, tmp_path):
    import cv2

    from app import consent

    session = _walked_session(monkeypatch, tmp_path)
    real = cv2.imencode
    calls = []

    class _Buffer:
        def __init__(self, buf):
            self.buf = buf

        def tofile(self, path):
            calls.append(path)
            if len(calls) == 3:
                with open(path, "wb") as fh:                   # half a JPEG, then the disk fills
                    fh.write(bytes(self.buf)[:10])
                raise OSError(28, "No space left on device")
            self.buf.tofile(path)

    def encode(ext, img, params=None):
        ok, buf = real(ext, img, params or [])
        return ok, _Buffer(buf)

    monkeypatch.setattr(cv2, "imencode", encode)

    result = session.save("ทดสอบ")

    assert result["ok"] is False and re.search(r"[฀-๿]", result["reason"])
    assert len(calls) == 3
    assert not (tmp_path / "enrolled" / "ทดสอบ").exists()
    assert consent.load() == {}
    assert session.pending_count == 5


def test_a_failed_top_up_keeps_the_shots_already_there(monkeypatch, tmp_path):
    """Only this call's files are taken back: a station top-up that fails
    must not cost the person the shots they had."""
    session = _walked_session(monkeypatch, tmp_path)
    folder = tmp_path / "enrolled" / "ทดสอบ"
    folder.mkdir(parents=True)
    (folder / "20260901-100000_1.jpg").write_bytes(b"old")
    (folder / "20260901-100000_2.jpg").write_bytes(b"old")
    _consent_write_fails(monkeypatch)

    result = session.save("ทดสอบ", existing_ok=True)

    assert result["ok"] is False
    assert sorted(p.name for p in folder.iterdir()) == [
        "20260901-100000_1.jpg", "20260901-100000_2.jpg"]


def test_a_failed_replace_has_not_thrown_the_old_shots_away(monkeypatch, tmp_path):
    """`replace` used to delete the folder first; a record that then failed
    left the person with nothing. The old shots go only once the record is
    written."""
    folder = tmp_path / "enrolled" / "ทดสอบ"
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    crop = [np.full((90, 90, 3), 120, np.uint8)]
    enrollment.save_shots("ทดสอบ", crop + crop)
    old = sorted(p.name for p in folder.iterdir())
    _consent_write_fails(monkeypatch)

    with pytest.raises(OSError):
        enrollment.save_shots("ทดสอบ", crop, replace=True)
    assert sorted(p.name for p in folder.iterdir()) == old

    assert enrollment.save_shots("ทดสอบ", crop, replace=True) == 1   # and replace still replaces


def test_an_unreadable_consent_file_refuses_the_save_and_keeps_nothing(monkeypatch, tmp_path):
    """consent.record refuses to write over a file it cannot read; the shots
    this save wrote go with it, and the file is left exactly as it was."""
    from app import consent

    session = _walked_session(monkeypatch, tmp_path)
    consent.people_path().parent.mkdir(parents=True, exist_ok=True)
    consent.people_path().write_bytes(b'{"x": {"revoked_at": "2026-09-01"}, }')
    before = consent.people_path().read_bytes()

    result = session.save("ทดสอบ", add_to_gallery=True)

    assert result["ok"] is False and "people.json" in result["reason"]
    assert consent.people_path().read_bytes() == before
    assert not (tmp_path / "enrolled" / "ทดสอบ").exists()
    assert session.pending_count == 5


def test_a_gallery_write_the_disk_refuses_is_reported_beside_the_save(monkeypatch, tmp_path):
    """Shots and consent are on disk; an OSError from the gallery write
    used to be a 500 "ลองใหม่" for a person who was in fact saved."""
    from app import consent

    session = _walked_session(monkeypatch, tmp_path)

    def held(self, path):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(faces.Gallery, "save", held)

    result = session.save("ทดสอบ", add_to_gallery=True)

    assert result["ok"] is True and result["total"] == 5
    assert result["gallery_total"] is None
    assert re.search(r"[฀-๿]", result["gallery_reason"])
    assert "build-face-gallery" in result["gallery_reason"]
    assert consent.status("ทดสอบ") == consent.OK
    assert session.pending_count == 0


# -- the station can still top up the people it filed before the name rule ------

def test_the_station_tops_up_a_folder_named_before_the_name_rule(monkeypatch, tmp_path):
    """`Tom_2` was a fine station name until safe_person_name. The exact
    name then failed the rule, and "Tom 2" was refused as a second name for
    the same face — the station could no longer add a shot to its own
    person."""
    from app import consent
    from scripts import enroll_app

    root = tmp_path / "enrolled"
    for legacy in ("Tom_2", "ทด  สอบ"):
        (root / legacy).mkdir(parents=True)
        (root / legacy / "20260901-100000_1.jpg").write_bytes(b"old")
    monkeypatch.setattr(enrollment, "ENROLLED", root)
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))

    for legacy in ("Tom_2", "ทด  สอบ"):
        _station_with(monkeypatch, [{"vec": ALICE, "crop": np.full((40, 40, 3), 90, np.uint8)}])
        result = enroll_app._save(legacy, replace=False)
        assert result["ok"] is True and result["name"] == legacy and result["total"] == 2
        assert consent.status(legacy) == consent.OK
    assert sorted(p.name for p in root.iterdir()) == sorted(["Tom_2", "ทด  สอบ"])


@pytest.mark.parametrize("raw", ["tom_2", "Tom_3", "..", "Tom_2/..", "..\\enrolled\\Tom_2"])
def test_the_station_exception_is_only_an_exact_existing_folder(monkeypatch, tmp_path, raw):
    root = tmp_path / "enrolled"
    (root / "Tom_2").mkdir(parents=True)
    monkeypatch.setattr(enrollment, "ENROLLED", root)

    with pytest.raises(ValueError):
        enrollment.checked_name(raw, existing_ok=True)


def test_the_call_screen_never_gets_the_station_exception(monkeypatch, tmp_path):
    """A LAN screen must not add shots to anybody who already exists."""
    root = tmp_path / "enrolled"
    (root / "Tom_2").mkdir(parents=True)
    monkeypatch.setattr(enrollment, "ENROLLED", root)
    session = enrollment.Session(pending=[{"vec": ALICE, "crop": np.zeros((4, 4, 3), np.uint8)}])

    result = session.save("Tom_2", add_to_gallery=True)

    assert result["ok"] is False
    assert list((root / "Tom_2").iterdir()) == []
