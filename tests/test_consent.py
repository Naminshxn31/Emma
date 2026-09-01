"""
Naming policy for the door camera (2026-09-01, from the outside review).

The review's rule, verbatim: enrolled AND consent in force AND confidence
past the bar AND confirmed over frames AND rank-1 clear of rank-2 AND the
only usable face in frame → say the name; ELSE a plain greeting. The first
four already existed; these tests pin the last two and the consent record,
and the one sentence the review was sharpest about — that the name is for
the greeting only, because nothing here can tell who is talking afterwards.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app import consent, enrollment, facewatch, faces
from app.config import settings

FRAME = np.zeros((480, 640, 3), np.uint8)


def _unit(*values: float) -> np.ndarray:
    v = np.zeros(512, np.float32)
    for i, x in enumerate(values):
        v[i] = x
    return v / np.linalg.norm(v)


ALICE = _unit(1)
BOB = _unit(0, 1)
#: Looks like both — closer to Alice, but Bob is a near second.
TWIN = _unit(1, 0.92)


@pytest.fixture(autouse=True)
def _people_in_tmp(monkeypatch, tmp_path):
    """Consent records next to a relocated enrolment store — the machine's
    real people.json must never take a test's writes."""
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    monkeypatch.setattr(settings, "face_require_consent", False)
    monkeypatch.setattr(settings, "face_consent_days", 365)


def _gallery(names=("ต้า", "mix"), vecs=(ALICE, BOB)) -> faces.Gallery:
    return faces.Gallery(list(names), np.stack(list(vecs)), [{"group": "staff"} for _ in names])


def _watcher(**kw) -> facewatch.Watcher:
    kw.setdefault("gallery", _gallery())
    kw.setdefault("threshold", 0.45)
    kw.setdefault("confirm_frames", 1)
    kw.setdefault("cooldown_s", 600)
    kw.setdefault("min_face_px", 110)
    kw.setdefault("margin", 0.10)
    kw.setdefault("name_when_alone", True)
    return facewatch.Watcher(**kw)


def _frame_with(monkeypatch, *vecs, width=200):
    found = [faces.Face((i * 250, 0, i * 250 + width, width), 0.9, v) for i, v in enumerate(vecs)]
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: found)


def _twice(w, now):
    """A stranger needs twice the confirmation of a colleague (a garbage
    frame greeted once as a stranger is why); an unnamed recognition is
    treated as a stranger, so it takes two frames too."""
    w.see(FRAME, now=now)
    return w.see(FRAME, now=now + 0.2)


# ==================== the consent record ====================

def test_enrolling_yourself_records_consent():
    """The one kind of consent this system can witness: the person took
    the shots and pressed save."""
    crop = np.zeros((112, 112, 3), np.uint8)
    enrollment.save_shots("โชกุน", [crop])
    entry = consent.load()["โชกุน"]
    assert entry["consent_by"] == "โชกุน"
    assert entry["consent_version"] == consent.VERSION
    assert entry["revoked_at"] is None
    assert consent.status("โชกุน") == consent.OK


def test_nobody_is_given_a_record_they_did_not_make():
    """Staff portraits from HR have no record, and nothing invents one."""
    assert consent.load() == {}
    assert consent.status("ต้า") == consent.MISSING
    src = inspect.getsource(consent)
    assert "staff.csv" in src and "Nothing in this module invents" in src


def test_missing_consent_is_tolerated_only_while_the_switch_is_off(monkeypatch):
    assert consent.may_name("ต้า") == (True, consent.MISSING)
    monkeypatch.setattr(settings, "face_require_consent", True)
    assert consent.may_name("ต้า") == (False, consent.MISSING)


def test_revoked_and_expired_are_never_tolerated(monkeypatch):
    now = datetime.now(timezone.utc).astimezone()
    consent.record("ต้า", now=now - timedelta(days=400))          # expired
    assert consent.status("ต้า") == consent.EXPIRED
    assert consent.may_name("ต้า") == (False, consent.EXPIRED)

    consent.record("mix")
    assert consent.revoke("mix") is True
    assert consent.status("mix") == consent.REVOKED
    assert consent.may_name("mix") == (False, consent.REVOKED)
    monkeypatch.setattr(settings, "face_require_consent", False)
    assert consent.may_name("mix")[0] is False, "the switch must not override a revocation"


def test_revoking_somebody_without_a_record_still_takes_effect():
    assert consent.revoke("ghost") is False
    assert consent.status("ghost") == consent.REVOKED


def test_re_enrolling_after_revocation_is_consent_again():
    consent.record("ต้า")
    consent.revoke("ต้า")
    consent.record("ต้า")
    assert consent.status("ต้า") == consent.OK


# ==================== the watcher: three reasons a name stays unspoken ====================

def test_a_close_runner_up_turns_the_name_into_a_plain_greeting(monkeypatch):
    """Two colleagues who resemble each other score close together, and the
    close-second frame is the one where the nearest is most likely wrong.
    A greeting without a name is never wrong."""
    w = _watcher()
    _frame_with(monkeypatch, TWIN)
    s = _twice(w, 0.0)
    assert s is not None and s.kind == "stranger"
    assert s.meta["unnamed"].startswith("margin")
    assert s.meta["would_be"] == "ต้า"


def test_a_clear_winner_is_still_named(monkeypatch):
    w = _watcher()
    _frame_with(monkeypatch, ALICE)
    s = w.see(FRAME, now=0.0)
    assert s is not None and s.kind == "known" and s.name == "ต้า"


def test_two_people_in_frame_means_no_name(monkeypatch):
    """The model is told one name and addresses both of them by it."""
    w = _watcher()
    _frame_with(monkeypatch, ALICE, BOB)
    s = _twice(w, 0.0)
    assert s is not None and s.kind == "stranger"
    assert s.meta["unnamed"].startswith("company")


def test_a_second_face_too_small_to_count_does_not_block_the_name(monkeypatch):
    """Somebody across the room is not company; the bar is the same
    min_face_px that decides a visitor at all."""
    w = _watcher()
    near = faces.Face((0, 0, 200, 200), 0.9, ALICE)
    far = faces.Face((400, 0, 440, 40), 0.9, BOB)
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [near, far])
    s = w.see(FRAME, now=0.0)
    assert s is not None and s.kind == "known"


def test_the_company_rule_can_be_switched_off(monkeypatch):
    w = _watcher(name_when_alone=False)
    _frame_with(monkeypatch, ALICE, BOB)
    s = w.see(FRAME, now=0.0)
    assert s is not None and s.kind == "known"


def test_a_revoked_person_is_greeted_without_the_name_on_the_next_frame(monkeypatch):
    """Revocation is read at decision time, not at the next gallery build."""
    w = _watcher()
    _frame_with(monkeypatch, ALICE)
    assert w.see(FRAME, now=0.0).kind == "known"
    w.forget()
    consent.revoke("ต้า")
    s = _twice(w, 1.0)
    assert s is not None and s.kind == "stranger"
    assert s.meta["unnamed"] == "consent revoked"


def test_requiring_consent_unnames_the_unrecorded(monkeypatch):
    monkeypatch.setattr(settings, "face_require_consent", True)
    w = _watcher()
    _frame_with(monkeypatch, ALICE)
    s = _twice(w, 0.0)
    assert s is not None and s.kind == "stranger"
    assert s.meta["unnamed"] == "consent missing"
    consent.record("ต้า")
    w.forget()
    assert w.see(FRAME, now=1.0).kind == "known"


def test_an_unnamed_recognition_is_not_greeted_twice(monkeypatch):
    """Greeted like a stranger means remembered like one: the same face a
    moment later, alone, must not get a second (named) greeting."""
    w = _watcher()
    _frame_with(monkeypatch, ALICE, BOB)
    assert _twice(w, 0.0).kind == "stranger"
    _frame_with(monkeypatch, ALICE)
    assert w.see(FRAME, now=2.0) is None


def test_match2_reports_the_best_other_person_not_the_same_person_again():
    g = faces.Gallery(["ต้า", "ต้า", "mix"], np.stack([ALICE, _unit(1, 0.05), BOB]),
                      [{}, {}, {}])
    i, score, runner_up = g.match2(TWIN)
    assert g.names[i] == "ต้า"
    assert runner_up == pytest.approx(float(BOB @ TWIN))


# ==================== the name is for one sentence ====================

def test_the_greeting_tells_the_model_the_name_is_for_this_sentence_only():
    """No speaker identification: after the greeting the model cannot know
    whose voice it hears, and left alone it assumes."""
    from app import greeter

    class _S:
        kind, name, group, meta = "known", "โชกุน", "enrolled", {}

    text = greeter.greeting_for(_S())
    assert "ใช้ชื่อเฉพาะในประโยคทักนี้" in text
    assert "ไม่มีระบบจำเสียง" in text
    assert "ห้ามถือว่าคนที่พูดคือโชกุน" in text


def test_the_turn_log_carries_why_a_name_was_withheld(monkeypatch):
    from app import greeter, turnlog

    seen = []
    monkeypatch.setattr(turnlog, "record", lambda ev, **f: seen.append((ev, f)))

    async def fake_announce(*a, **k):
        return True

    from app import events

    monkeypatch.setattr(events, "announce", fake_announce)

    class _S:
        kind, name, group, score = "stranger", None, "", 0.61
        meta = {"unnamed": "company (2 faces in frame)"}

    import asyncio

    asyncio.run(greeter._greet(_S()))
    assert seen[0][1]["unnamed"] == "company (2 faces in frame)"


# ==================== the build and the launcher ====================

def test_the_gallery_build_leaves_out_the_withdrawn():
    src = Path("scripts/build_face_gallery.py").read_text(encoding="utf-8")
    assert "consent.REVOKED, consent.EXPIRED" in src
    assert "left out (consent revoked/expired)" in src


def test_the_revoke_script_and_launcher_exist():
    from scripts import revoke_face

    assert callable(revoke_face.main)
    cmd = Path("revoke-face.cmd").read_text(encoding="utf-8")
    assert "scripts\\revoke_face.py" in cmd


def test_the_revoke_script_revokes(capsys):
    from scripts import revoke_face

    consent.record("ต้า")
    assert revoke_face.main(["ต้า"]) == 0
    assert consent.status("ต้า") == consent.REVOKED
    assert revoke_face.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "revoked" in out and "ต้า" in out


def test_company_means_a_face_of_comparable_size_not_a_poster_across_the_room(monkeypatch):
    """min_face_px alone let a 50px face in the background unname the
    person at the desk (2026-09-01: the owner alone, "company (2 faces in
    frame)"). Company is somebody standing with them."""
    w = _watcher(min_face_px=50)
    near = faces.Face((0, 0, 200, 200), 0.9, ALICE)
    far = faces.Face((400, 0, 470, 70), 0.9, BOB)        # 70px: past min_face_px, a third of the primary
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [near, far])
    assert w.see(FRAME, now=0.0).kind == "known"
    beside = faces.Face((400, 0, 560, 160), 0.9, BOB)     # 160px: standing next to them
    monkeypatch.setattr(faces, "locate", lambda frame, det_size=640: [near, beside])
    w.forget()
    assert _twice(w, 1.0).meta["unnamed"].startswith("company")


def test_the_pre_ring_waits_for_a_stranger_to_nearly_confirm(monkeypatch):
    """Two frames of an unknown face rang Gemini for a colleague in
    cooldown whose score dipped under the bar (2026-09-01 15:xx): a
    session nobody greeted, held open for two minutes. A stranger rings
    at frame needed-1; a colleague still rings at two."""
    w = _watcher(confirm_frames=3)
    _frame_with(monkeypatch, _unit(0, 0, 1))          # nobody in the gallery
    w.see(FRAME, now=0.0); w.see(FRAME, now=0.2)
    assert w.someone_at is None, "rang on two frames of a stranger"
    w.see(FRAME, now=0.4); w.see(FRAME, now=0.6); w.see(FRAME, now=0.8)
    assert w.someone_at == 0.8, "frame 5 of 6 rings"
    w2 = _watcher(confirm_frames=3)
    _frame_with(monkeypatch, ALICE)
    w2.see(FRAME, now=0.0); w2.see(FRAME, now=0.2)
    assert w2.someone_at == 0.2, "a colleague still rings at two"
