"""
A rebuild on a machine that does not have every source.

The staff portraits live on D:, and the showroom machine has no
D:/Data/รูปพนักงาน. `build_face_gallery.py` used to read every CSV row's
photo as "file not found", list the colleagues as problems at the bottom,
and replace the gallery anyway — without a single staff portrait vector.
Now a *missing* source lends the previous gallery's vectors for exactly the
rows it still names; a photo that is present but unusable is still dropped.

Everything here is in tmp_path: data/faces/ holds real people, and the
names below are made up.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import numpy as np
import pytest

from app import consent, enrollment, faces


def _vec(label: str) -> np.ndarray:
    """A fixed unit vector per label, so "carried" and "re-embedded" can be
    told apart by value."""
    rng = np.random.default_rng(zlib.crc32(label.encode("utf-8")))
    v = rng.standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


A, B, C = _vec("portrait-one"), _vec("portrait-two"), _vec("broker-one")


@pytest.fixture
def build(monkeypatch, tmp_path):
    from scripts import build_face_gallery as bfg

    root = tmp_path / "faces"
    (root / "enrolled").mkdir(parents=True)
    monkeypatch.setattr(bfg, "OUT", root / "gallery.npz")
    monkeypatch.setattr(bfg, "STAFF_CSV", root / "staff.csv")
    monkeypatch.setattr(bfg, "STAFF_DIR", tmp_path / "not-on-this-machine")
    monkeypatch.setattr(bfg, "ENROLLED_DIR", root / "enrolled")
    monkeypatch.setattr(bfg, "BROKER_JSON", tmp_path / "db" / "data_master.json")
    monkeypatch.setattr(bfg, "BROKER_PHOTOS", tmp_path / "db" / "photos")
    # people.json follows ENROLLED; the machine's real records never see this.
    monkeypatch.setattr(enrollment, "ENROLLED", root / "enrolled")
    monkeypatch.setattr(faces, "available", lambda: True)

    def embed(path, det_size=640):
        path = Path(path)
        if not path.is_file():
            return None
        return faces.Face((0, 0, 100, 100), 0.9, _vec("fresh:" + path.name))

    monkeypatch.setattr(faces, "embed_file", embed)
    return bfg


def _csv(bfg, *rows):
    lines = ["name,file,role"] + [f"{n},{f}," for n, f in rows]
    bfg.STAFF_CSV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _previous(bfg, names, vecs, metas, model_tag=faces.MODEL_TAG):
    faces.Gallery(names, np.stack(vecs), metas, model_tag).save(bfg.OUT)


def _camera_shot(bfg, name, file="20260901-100000_1.jpg"):
    folder = bfg.ENROLLED_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / file).write_bytes(b"jpeg")


def _rows(bfg):
    g = faces.Gallery.load(bfg.OUT)
    return {(n, m.get("photo")): (v, m) for n, v, m in zip(g.names, g.vecs, g.meta)}


# -- the staff portraits ------------------------------------------------------

def test_staff_portraits_survive_a_rebuild_without_the_photo_folder(build, capsys):
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"), ("ทดสอบสอง", "p2.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสอง"], [A, B],
              [{"group": "staff", "role": "", "photo": "p1.jpg", "consent": "ok"},
               {"group": "staff", "role": "", "photo": "p2.jpg"}])
    _camera_shot(build, "ทดสอบสาม")

    assert build.main([]) == 0

    rows = _rows(build)
    assert np.allclose(rows[("ทดสอบหนึ่ง", "p1.jpg")][0], A)
    assert np.allclose(rows[("ทดสอบสอง", "p2.jpg")][0], B)
    assert rows[("ทดสอบหนึ่ง", "p1.jpg")][1]["group"] == "staff"
    # Consent is re-read, not copied: the carried "ok" had no record behind it.
    assert rows[("ทดสอบหนึ่ง", "p1.jpg")][1]["consent"] == consent.MISSING
    assert ("ทดสอบสาม", "enrolled/ทดสอบสาม/20260901-100000_1.jpg") in rows
    out = capsys.readouterr().out
    assert "CARRIED OVER" in out and "not on this machine" in out


def test_a_row_removed_from_the_csv_is_not_resurrected(build):
    """The CSV says who; a missing photo only lends a vector to a row that
    is still in it."""
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสอง"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"}, {"group": "staff", "photo": "p2.jpg"}])

    assert build.main([]) == 0

    assert faces.Gallery.load(build.OUT).names == ["ทดสอบหนึ่ง"]


def test_a_renamed_row_does_not_carry_the_old_name(build, capsys):
    _csv(build, ("ทดสอบใหม่", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}])
    _camera_shot(build, "ทดสอบสาม")

    build.main([])

    assert "ทดสอบหนึ่ง" not in faces.Gallery.load(build.OUT).names
    assert "file not found" in capsys.readouterr().out


def test_only_the_missing_photo_is_carried(build, monkeypatch, tmp_path, capsys):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "p1.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(build, "STAFF_DIR", photos)
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"), ("ทดสอบสอง", "p2.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสอง"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"}, {"group": "staff", "photo": "p2.jpg"}])

    build.main([])

    rows = _rows(build)
    assert np.allclose(rows[("ทดสอบหนึ่ง", "p1.jpg")][0], _vec("fresh:p1.jpg"))
    assert np.allclose(rows[("ทดสอบสอง", "p2.jpg")][0], B)
    assert "missing from" in capsys.readouterr().out


def test_a_photo_that_is_there_but_unusable_is_dropped_not_carried(build, monkeypatch, tmp_path):
    """That is a judgement about the photo, not an absence."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "p1.jpg").write_bytes(b"jpeg")
    monkeypatch.setattr(build, "STAFF_DIR", photos)
    monkeypatch.setattr(faces, "embed_file", lambda path, det_size=640: None)
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "อื่น"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"}, {"group": "broker"}])
    before = build.OUT.read_bytes()

    assert build.main([]) == 1          # nothing left to enrol...
    assert build.OUT.read_bytes() == before   # ...and nothing overwritten


def test_no_csv_keeps_every_staff_portrait(build, capsys):
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสาม"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"},
               {"group": "staff", "photo": "enrolled/ทดสอบสาม/x.jpg", "source": "camera"}])

    assert build.main([]) == 0

    # The portrait is carried; the camera row is not — its folder is gone.
    assert faces.Gallery.load(build.OUT).names == ["ทดสอบหนึ่ง"]
    assert "CARRIED OVER" in capsys.readouterr().out


def test_a_camera_folder_that_is_gone_is_not_carried(build):
    """data/faces/enrolled is this machine's own folder; a missing one was
    deleted on purpose (revoke_face.py does exactly that)."""
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสี่"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"},
               {"group": "enrolled", "photo": "enrolled/ทดสอบสี่/x.jpg", "source": "camera"}])

    build.main([])

    assert "ทดสอบสี่" not in faces.Gallery.load(build.OUT).names


def test_withdrawn_consent_leaves_even_when_carried(build):
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"), ("ทดสอบสอง", "p2.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "ทดสอบสอง"], [A, B],
              [{"group": "staff", "photo": "p1.jpg"}, {"group": "staff", "photo": "p2.jpg"}])
    consent.revoke("ทดสอบสอง")

    build.main([])

    assert faces.Gallery.load(build.OUT).names == ["ทดสอบหนึ่ง"]


def test_a_gallery_from_another_model_lends_nothing(build, capsys):
    """Carrying a vector across embedding spaces is the bug the model tag
    exists to stop."""
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}],
              model_tag="other/model")
    _camera_shot(build, "ทดสอบสาม")

    assert build.main([]) == 0

    assert faces.Gallery.load(build.OUT).names == ["ทดสอบสาม"]
    assert "cannot lend" in capsys.readouterr().out


# -- the brokers --------------------------------------------------------------

def _with_a_broker(build):
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง", "นายหน้าหนึ่ง"], [A, C],
              [{"group": "staff", "photo": "p1.jpg"},
               {"group": "broker", "company": "", "record": 7, "photo": "b7.jpg"}])


def test_brokers_carry_over_when_their_json_is_gone(build, capsys):
    _with_a_broker(build)

    build.main(["--with-brokers"])

    rows = _rows(build)
    assert np.allclose(rows[("นายหน้าหนึ่ง", "b7.jpg")][0], C)
    assert "CARRIED OVER" in capsys.readouterr().out


def test_a_broker_whose_photo_is_gone_keeps_that_photo_s_vector(build):
    _with_a_broker(build)
    build.BROKER_JSON.parent.mkdir(parents=True)
    build.BROKER_JSON.write_text(json.dumps({"records": [
        {"no": 7, "name": "นายหน้าหนึ่ง", "photos": ["b7"]},
        {"no": 8, "name": "นายหน้าสอง", "photos": ["b8"]},
    ]}, ensure_ascii=False), encoding="utf-8")

    build.main(["--with-brokers"])

    rows = _rows(build)
    assert np.allclose(rows[("นายหน้าหนึ่ง", "b7.jpg")][0], C)
    assert not any(n == "นายหน้าสอง" for n, _ in rows)


def test_leaving_the_flag_off_still_drops_brokers_but_says_so(build, capsys):
    """Not a missing source — the decision the flag exists for."""
    _with_a_broker(build)

    build.main([])

    assert "นายหน้าหนึ่ง" not in faces.Gallery.load(build.OUT).names
    assert "--with-brokers" in capsys.readouterr().out


# -- the write ------------------------------------------------------------------

def test_a_rebuild_that_dies_while_writing_leaves_the_old_gallery(build, monkeypatch):
    _csv(build, ("ทดสอบหนึ่ง", "p1.jpg"))
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}])
    before = build.OUT.read_bytes()

    def torn(file, **arrays):
        file.write(b"PK\x03\x04 half")
        raise OSError("disk full")

    monkeypatch.setattr(faces.np, "savez", torn)
    with pytest.raises(OSError):
        build.main([])

    assert build.OUT.read_bytes() == before
    assert not [p.name for p in build.OUT.parent.iterdir() if p.name.startswith("gallery.npz.")]


# -- the rebuild and the call screen's live append --------------------------------

def test_a_scan_saved_during_a_rebuild_is_never_called_known_and_then_dropped(
        build, monkeypatch):
    """The rebuild lists enrolled/ once and replaces the file minutes later.
    A scan saved in between used to append itself, tell the screen
    "หุ่นจำได้แล้ว" — and vanish when the rebuild wrote its file (review,
    2026-09-29). With the rebuild holding the gallery lock, the append waits
    a moment and says so beside the save instead."""
    from app.config import settings

    monkeypatch.setattr(settings, "face_gallery", str(build.OUT))
    monkeypatch.setattr(enrollment, "GALLERY_LOCK_WAIT_S", 0.2)
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}])
    _camera_shot(build, "ทดสอบสาม")
    def view(i):                         # five views of one new face, ~0.9 alike
        v = 0.95 * _vec("new") + 0.3 * _vec(f"new-view-{i}")
        return (v / np.linalg.norm(v)).astype(np.float32)

    session = enrollment.Session(pending=[{"vec": view(i), "crop": np.full((40, 40, 3), 90, np.uint8)}
                                          for i in range(5)])
    saved = []
    real_embed = faces.embed_file

    def embed_while_somebody_is_scanned(path, det_size=640):
        if not saved:
            # enrolled/ is already listed: this is the window.
            saved.append(session.save("ทดสอบใหม่", add_to_gallery=True,
                                      consent_version=consent.STAFF_ATTESTED))
        return real_embed(path, det_size)

    monkeypatch.setattr(faces, "embed_file", embed_while_somebody_is_scanned)

    assert build.main([]) == 0

    result = saved[0]
    assert result["ok"] is True, result
    final = faces.Gallery.load(build.OUT).names
    assert result["gallery_total"] is None or "ทดสอบใหม่" in final, \
        "the screen said the robot knows them, and the rebuild dropped them"
    assert "build-face-gallery" in result["gallery_reason"]
    assert consent.status("ทดสอบใหม่") == consent.OK      # saved all the same


def test_a_rebuild_that_cannot_get_the_gallery_refuses_and_writes_nothing(build, monkeypatch,
                                                                          capsys):
    """Another rebuild (or an append that hangs) holding the file: refused
    in Thai, never a second writer racing the first."""
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}])
    _camera_shot(build, "ทดสอบสาม")
    before = build.OUT.read_bytes()
    monkeypatch.setattr(build, "LOCK_WAIT_S", 0.2)

    with faces.file_lock(build.OUT, 1.0, "unused"):
        assert build.main([]) == 1

    assert build.OUT.read_bytes() == before
    assert "ยังไม่ได้สร้างใหม่" in capsys.readouterr().out


def test_an_unreadable_consent_file_refuses_the_rebuild(build, capsys):
    """The rebuild is where withdrawn people's vectors stop existing. With
    people.json unreadable it cannot tell who withdrew, and building as if
    nobody had is how a revoked person gets their vectors back."""
    _previous(build, ["ทดสอบหนึ่ง"], [A], [{"group": "staff", "photo": "p1.jpg"}])
    _camera_shot(build, "ทดสอบสาม")
    consent.people_path().write_text('{"ทดสอบสาม": {"revoked_at": "2026-09-01"},}',
                                     encoding="utf-8")
    before = build.OUT.read_bytes()

    assert build.main([]) == 1

    assert build.OUT.read_bytes() == before
    assert "NOT rebuilt" in capsys.readouterr().out
