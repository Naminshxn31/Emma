"""The booth's guests on the robot (app/guests.py), its door (app/guest_sync_server.py)
and how they reach the running watcher."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import consent, enrollment, faces, facewatch, greeter, guest_sync_server as sync, guests
from app.config import settings

KEY = "guest-sync-key-for-tests"


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def record(name, *, at=None, days=3.0):
    at = at or datetime.now(timezone.utc).astimezone()
    return {"consent_at": at.isoformat(timespec="seconds"), "consent_by": name,
            "consent_version": consent.EVENT_GUEST,
            "expires_at": (at + timedelta(days=days)).isoformat(timespec="seconds"), "revoked_at": None}


def payload(name="คุณแขก", seed=1, **over):
    doc = {"name": name, "vectors": [guests.encode_vector(unit(seed)), guests.encode_vector(unit(seed + 100))],
           "model_tag": faces.MODEL_TAG, "consent": record(name), "station": "booth-1"}
    doc.update(over)
    return doc


@pytest.fixture
def robot(monkeypatch, tmp_path):
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "enrolled")
    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "gallery.npz"))
    monkeypatch.setattr(settings, "face_guests_file", str(tmp_path / "guests.npz"))
    monkeypatch.setattr(settings, "guest_sync_key", KEY)
    guests.reset()
    return tmp_path


def client():
    return TestClient(sync.app, headers={"Authorization": "Bearer " + KEY})


# ---- what goes in --------------------------------------------------------------------------

def test_a_guest_goes_in_with_the_booths_record_and_nothing_made_up(robot):
    assert guests.add(payload())["ok"] is True
    entry = consent.load()["คุณแขก"]
    assert entry["consent_version"] == consent.EVENT_GUEST
    assert entry["witnessed_by"] == "booth-1"
    assert "approved_by" not in entry
    assert consent.status("คุณแขก") == consent.OK
    assert set(guests.load().names) == {"คุณแขก"}
    assert guests.load().meta[0]["group"] == guests.GROUP


@pytest.mark.parametrize("change,error", [
    ({"name": "../../app"}, "bad_name"),
    ({"name": ""}, "bad_name"),
    ({"model_tag": "insightface/buffalo_l/w600k_r50"}, "wrong_model"),
    ({"vectors": []}, "bad_vectors"),
    ({"vectors": ["not base64!"]}, "bad_vectors"),
    ({"vectors": [guests.encode_vector(unit(1)[:100])]}, "bad_vectors"),
    ({"vectors": [guests.encode_vector(unit(1) * 2.0)]}, "bad_vectors"),
    ({"vectors": [guests.encode_vector(unit(1))] * (guests.MAX_ROWS + 1)}, "bad_vectors"),
    ({"consent": None}, "bad_consent"),
    ({"consent": {}}, "bad_consent"),
])
def test_what_is_refused_writes_nothing(robot, change, error):
    assert guests.add(payload(**change)) == {"ok": False, "error": error}
    assert guests.load().names == []
    assert consent.load() == {}


@pytest.mark.parametrize("break_it", [
    lambda r: r.update(consent_by="คนอื่น"),                # somebody else's consent
    lambda r: r.update(consent_version=consent.VERSION),    # not the booth's words
    lambda r: r.update(revoked_at="2026-11-25T10:00:00+07:00"),
    lambda r: r.update(expires_at=r["consent_at"]),
    lambda r: r.update(expires_at=(datetime.fromisoformat(r["consent_at"])
                                   + timedelta(days=consent.MAX_GUEST_DAYS + 1)).isoformat()),
    lambda r: r.update(consent_at="2026-11-25T10:00:00"),   # no zone: not comparable to a revocation
])
def test_a_record_that_is_not_a_guests_own_is_refused(robot, break_it):
    doc = payload()
    break_it(doc["consent"])
    assert guests.add(doc)["error"] == "bad_consent"
    assert guests.load().names == []


def test_a_retry_of_a_lost_answer_adds_nothing(robot):
    doc = payload()
    assert guests.add(doc)["state"] == "recorded"
    assert guests.add(doc) == {"ok": True, "state": "already"}
    assert len(guests.load()) == 2


def test_scanning_again_replaces_the_rows(robot):
    assert guests.add(payload())["ok"]
    later = datetime.now(timezone.utc).astimezone() + timedelta(minutes=5)
    assert guests.add(payload(seed=1, consent=record("คุณแขก", at=later)))["state"] == "recorded"
    assert len(guests.load()) == 2


def test_one_face_under_two_names_is_refused_at_the_booth_not_found_out_at_the_door(robot):
    faces.Gallery(["เพื่อนร่วมงาน"], unit(1).reshape(1, -1), [{"group": "staff"}]).save(settings.face_gallery)
    assert guests.add(payload(seed=1))["error"] == "same_face"
    assert consent.load() == {}


def test_a_resemblance_over_the_doors_bar_does_not_turn_a_guest_away(robot):
    other = unit(50)
    faces.Gallery(["เพื่อนร่วมงาน"], other.reshape(1, -1), [{"group": "staff"}]).save(settings.face_gallery)
    close = unit(1) - float(unit(1) @ other) * other
    close = close / np.linalg.norm(close)
    v = (0.55 * other + np.sqrt(1 - 0.55 ** 2) * close).astype(np.float32)
    assert float(v @ other) == pytest.approx(0.55, abs=1e-3)
    assert guests.add(payload(vectors=[guests.encode_vector(v)]))["ok"] is True


def test_too_many_guests_is_refused(robot, monkeypatch):
    monkeypatch.setattr(guests, "MAX_GUESTS", 1)
    assert guests.add(payload())["ok"]
    assert guests.add(payload("คุณสอง", seed=5, consent=record("คุณสอง")))["error"] == "full"


# ---- withdrawing --------------------------------------------------------------------------

def test_revoking_removes_the_rows_and_marks_the_record(robot):
    guests.add(payload())
    assert guests.revoke("คุณแขก")["rows"] == 2
    assert guests.load().names == []
    assert consent.status("คุณแขก") == consent.REVOKED


def test_a_queued_save_cannot_undo_a_withdrawal(robot):
    doc = payload()
    guests.revoke("คุณแขก")                 # before the booth's save arrived
    assert guests.add(doc)["error"] == "revoked"
    assert guests.load().names == []
    assert consent.status("คุณแขก") == consent.REVOKED


def test_scanning_again_after_withdrawing_is_consenting_again(robot):
    guests.add(payload())
    guests.revoke("คุณแขก")
    later = datetime.now(timezone.utc).astimezone() + timedelta(minutes=5)
    assert guests.add(payload(consent=record("คุณแขก", at=later)))["ok"] is True
    assert consent.status("คุณแขก") == consent.OK


def test_an_unreadable_people_file_refuses_rather_than_writes_over(robot):
    guests.add(payload())
    consent.people_path().write_text("{ not json", encoding="utf-8")
    assert guests.add(payload("คุณสอง", seed=5, consent=record("คุณสอง")))["error"] in ("consent_unreadable", "bad_consent")
    assert guests.revoke("คุณแขก")["error"] == "consent_unreadable"


# ---- the end of the event -----------------------------------------------------------------

def test_past_the_consent_rows_and_record_are_gone_and_staff_are_not_touched(robot):
    guests.add(payload())
    consent.record("พนักงาน", by="พนักงาน")
    gone = guests.purge(datetime.now(timezone.utc).astimezone() + timedelta(days=5))
    assert gone == {"rows": 2, "records": 1}
    assert guests.load().names == []
    assert "คุณแขก" not in consent.load() and "พนักงาน" in consent.load()


def test_before_the_end_nothing_is_purged(robot):
    guests.add(payload())
    assert guests.purge() == {"rows": 0, "records": 0}
    assert len(guests.load()) == 2


def test_a_revoked_guests_record_outlives_the_rows_until_its_own_end(robot):
    guests.add(payload())
    guests.revoke("คุณแขก")
    guests.purge()
    assert consent.status("คุณแขก") == consent.REVOKED
    guests.purge(datetime.now(timezone.utc).astimezone() + timedelta(days=5))
    assert "คุณแขก" not in consent.load()


# ---- reaching the running watcher ---------------------------------------------------------

def test_off_the_greeter_loads_what_it_always_loaded(tmp_path, monkeypatch):
    path = tmp_path / "gallery.npz"
    faces.Gallery(["ก"], unit(1).reshape(1, -1), [{}]).save(path)
    monkeypatch.setattr(settings, "face_guests_file", "")
    assert greeter._live_signature(path) == greeter._gallery_signature(path)
    assert greeter._load_live(path).names == ["ก"]


def test_a_scan_arriving_is_seen_by_the_running_watcher_without_a_restart(robot):
    base = robot / "gallery.npz"
    faces.Gallery(["พนักงาน"], unit(50).reshape(1, -1), [{"group": "staff"}]).save(base)

    class Watcher:
        gallery = None

    async def scenario():
        follow = greeter._GalleryFollower(base, greeter._live_signature(base))
        follow.next_check = 0.0
        await follow.poll(Watcher)
        assert Watcher.gallery is None             # nothing changed yet
        guests.add(payload())
        follow.next_check = 0.0
        await follow.poll(Watcher)

    asyncio.run(scenario())
    assert set(Watcher.gallery.names) == {"พนักงาน", "คุณแขก"}


def test_a_watcher_starts_on_a_robot_with_no_guests_and_no_gallery_yet(robot, monkeypatch):
    monkeypatch.setattr(faces, "available", lambda: True)
    w = facewatch.Watcher.from_settings()
    assert w is not None and len(w.gallery) == 0


def test_a_damaged_guest_file_does_not_lose_the_staff(robot):
    base = robot / "gallery.npz"
    faces.Gallery(["พนักงาน"], unit(50).reshape(1, -1), [{"group": "staff"}]).save(base)
    guests.path().write_bytes(b"not a gallery")
    assert guests.live_gallery(base).names == ["พนักงาน"]


def test_deploy_neither_ships_nor_deletes_the_guest_list():
    import importlib.util
    from fnmatch import fnmatch
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "robot_server_for_test", Path(__file__).resolve().parent.parent / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    rel = "data/faces/guests.npz"
    assert not any(fnmatch(rel, g) for g in rs.EXTRA_GLOBS)
    # Even if a manifest had it: gone from the PC is not gone from the robot.
    assert rs.spared_deletes([rel], {rel: "h"}, {rel: "h"}) == [rel]
    assert rs.spared_deletes(["data/faces/people.json"], {"data/faces/people.json": "h"}, {}) != []
    # and the area staff marked with the robot
    area = "data/events/floor1_event.approach.spots.json"
    assert not any(fnmatch(area, g) for g in rs.EXTRA_GLOBS)


# ---- the door --------------------------------------------------------------------------------

def test_no_key_no_entry_and_no_hint(robot):
    bare = TestClient(sync.app)
    assert bare.get("/guests/status").status_code == 401
    assert bare.post("/guests/add", json=payload()).status_code == 401
    wrong = TestClient(sync.app, headers={"Authorization": "Bearer nope"})
    assert wrong.post("/guests/revoke", json={"name": "ก"}).status_code == 401
    assert guests.load().names == []


def test_a_key_in_the_url_is_not_a_key(robot):
    assert TestClient(sync.app).get("/guests/status?key=" + KEY).status_code == 401


def test_five_wrong_keys_lock_the_client_out(robot):
    wrong = TestClient(sync.app, headers={"Authorization": "Bearer nope"})
    for _ in range(sync.KEY_TRIES):
        assert wrong.get("/guests/status").status_code == 401
    assert wrong.get("/guests/status").status_code == 429
    assert client().get("/guests/status").status_code == 429      # the same address


def test_the_door_adds_and_revokes_and_says_only_how_many(robot):
    c = client()
    assert c.post("/guests/add", json=payload()).json()["ok"] is True
    status = c.get("/guests/status")
    assert status.json() == {"ok": True, "guests": 1, "model_tag": faces.MODEL_TAG,
                             "max_days": consent.MAX_GUEST_DAYS}
    assert "คุณแขก" not in status.text
    assert c.post("/guests/revoke", json={"name": "คุณแขก"}).json()["rows"] == 2
    assert c.get("/guests/status").json()["guests"] == 0


def test_a_refusal_is_a_409_the_booth_keeps_and_a_busy_robot_a_503_it_retries(robot, monkeypatch):
    c = client()
    assert c.post("/guests/add", json=payload(model_tag="x")).status_code == 409
    monkeypatch.setattr(guests, "add", lambda doc: {"ok": False, "error": "busy"})
    assert c.post("/guests/add", json=payload()).status_code == 503


def test_an_oversized_body_is_refused(robot):
    assert client().post("/guests/add", content=b"x" * (sync.MAX_BODY + 1)).status_code == 400


@pytest.mark.parametrize("unset,why", [
    ("guest_sync_key", "GUEST_SYNC_KEY"),
    ("ssl_certfile", "SSL_CERTFILE"),
    ("ssl_keyfile", "SSL_CERTFILE"),
    ("face_guests_file", "FACE_GUESTS_FILE"),
])
def test_it_does_not_listen_without_its_key_its_certificate_or_a_file_to_write(robot, monkeypatch, unset, why):
    monkeypatch.setattr(settings, "guest_sync_port", 8004)
    monkeypatch.setattr(settings, "ssl_certfile", "cert.pem")
    monkeypatch.setattr(settings, "ssl_keyfile", "key.pem")
    assert sync._refusal() is None
    monkeypatch.setattr(settings, unset, "")
    assert why in sync._refusal()


def test_off_by_default_no_listener():
    assert settings.guest_sync_port == 0
    assert sync._refusal() == "off"


# ---- the log ----------------------------------------------------------------------------------

def test_a_guests_name_stays_out_of_the_door_cameras_log(monkeypatch):
    rows = []
    from app import turnlog

    monkeypatch.setattr(turnlog, "record", lambda event, **f: rows.append((event, f)))

    async def announce(*a, **k):
        return True

    from app import events

    monkeypatch.setattr(events, "announce", announce)
    sighting = facewatch.Sighting(kind="known", name="คุณแขก", score=0.7,
                                  meta={"group": guests.GROUP, "source": "booth"})
    asyncio.run(greeter._greet(sighting))
    assert rows[0][1]["name"] is None
    staff = facewatch.Sighting(kind="known", name="พนักงาน", score=0.7, meta={"group": "staff"})
    rows.clear()
    asyncio.run(greeter._greet(staff))
    assert rows[0][1]["name"] == "พนักงาน"


# ---- kept for later visits (owner 23 Nov 2026) ------------------------------------------------

def test_a_guest_is_kept_a_year_not_cut_at_the_event(robot):
    doc = payload()
    doc["consent"] = record("คุณแขก", days=370)
    assert guests.add(doc)["ok"] is True
    assert guests.purge(datetime.now(timezone.utc).astimezone() + timedelta(days=30))["rows"] == 0
    assert guests.purge(datetime.now(timezone.utc).astimezone() + timedelta(days=371))["rows"] == 2


def test_a_row_remembers_the_day_it_was_scanned(robot):
    doc = payload()
    guests.add(doc)
    assert {m["since"] for m in guests.load().meta} == {doc["consent"]["consent_at"]}


def test_a_row_whose_record_was_lost_is_purged(robot):
    guests.add(payload())
    guests.add(payload(name="คุณสอง", seed=7))
    consent.forget({"คุณแขก"})                       # a forced deploy put another people.json here
    out = guests.purge()
    assert out["rows"] == 2
    assert set(guests.load().names) == {"คุณสอง"}


def test_a_guest_row_without_its_own_record_is_never_named(robot, monkeypatch):
    monkeypatch.setattr(settings, "face_require_consent", False)    # tolerates MISSING for staff
    meta = {"group": guests.GROUP, "source": "booth"}
    assert facewatch.Watcher._guest_unrecorded("คุณแขก", meta) == "guest consent missing"
    guests.add(payload())
    assert facewatch.Watcher._guest_unrecorded("คุณแขก", meta) is None
    guests.revoke("คุณแขก")
    assert facewatch.Watcher._guest_unrecorded("คุณแขก", meta) == "guest consent revoked"
    assert facewatch.Watcher._guest_unrecorded("พนักงาน", {"group": "staff"}) is None


def _guest(since: str | None) -> facewatch.Sighting:
    meta = {"group": guests.GROUP, "source": "booth"}
    if since is not None:
        meta["since"] = since
    return facewatch.Sighting(kind="known", name="คุณแขก", score=0.7, meta=meta)


def test_a_guest_back_on_a_later_day_is_welcomed_back():
    bkk = timezone(timedelta(hours=7))
    now = datetime(2026, 12, 5, 10, 0, tzinfo=bkk)
    assert greeter._returning_guest(_guest("2026-11-30T15:00:00+07:00"), now) is True
    assert greeter._returning_guest(_guest("2026-12-05T09:00:00+07:00"), now) is False   # scanned today
    # 23:30 Bangkok on the 4th is 00:30 on the 5th in China, the robot's clock: still yesterday here.
    assert greeter._returning_guest(_guest("2026-12-05T00:30:00+08:00"), now) is True
    assert greeter._returning_guest(_guest(None), now) is False            # a row from before `since`
    assert greeter._returning_guest(_guest("2026-11-30T15:00:00"), now) is False   # no zone: unknown
    staff = facewatch.Sighting(kind="known", name="พนักงาน", score=0.7, meta={"since": "2020-01-01T00:00:00+07:00"})
    assert greeter._returning_guest(staff, now) is False


@pytest.mark.parametrize("lang", ["th", "en"])
def test_the_welcome_back_greeting_drops_the_first_meeting_question(monkeypatch, lang):
    monkeypatch.setattr(settings, "face_camera_source", "robot")
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "talk_default_language", lang)
    first = greeter.greeting_for(_guest(datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")))
    back = greeter.greeting_for(_guest("2020-01-01T10:00:00+07:00"))
    assert "รู้จักโครงการ" in first or "heard about our project" in first
    assert "รู้จักโครงการ" not in back and "heard about our project" not in back
    assert "ต้อนรับกลับมา" in back and "คุณแขก" in back
    if lang == "en":
        assert "Welcome back" in back and "ยินดีต้อนรับกลับมาค่ะ" not in back
    else:
        assert "ยินดีต้อนรับกลับมาค่ะ" in back



# ---- review 23 Nov: one name, one person -------------------------------------------------------

def test_a_guest_cannot_take_a_colleagues_name_or_record(robot):
    faces.Gallery(["พนักงาน"], unit(50).reshape(1, -1), [{"group": "staff"}]).save(settings.face_gallery)
    consent.record("พนักงาน", version=consent.VERSION)
    before = dict(consent.load()["พนักงาน"])
    assert guests.add(payload(name="พนักงาน"))["error"] == "name_taken"
    assert consent.load()["พนักงาน"] == before and guests.load().names == []


def test_a_colleagues_record_without_rows_is_still_theirs(robot):
    consent.revoke("ปีเตอร์")                          # revoke_face.py: a bare revocation, no version
    doc = payload(name="ปีเตอร์")
    doc["consent"] = record("ปีเตอร์", at=datetime.now(timezone.utc).astimezone() + timedelta(minutes=5))
    assert guests.add(doc)["error"] == "name_taken"
    assert consent.status("ปีเตอร์") == consent.REVOKED


def test_a_second_guest_under_a_live_guests_name_is_refused(robot):
    assert guests.add(payload())["ok"] is True
    other = payload(seed=40)
    other["consent"] = record("คุณแขก", at=datetime.now(timezone.utc).astimezone() + timedelta(minutes=5))
    other["station"] = "booth-2"
    assert guests.add(other)["error"] == "name_taken"
    assert len(guests.load()) == 2 and consent.load()["คุณแขก"]["witnessed_by"] == "booth-1"


def test_after_withdrawing_the_same_name_may_scan_again(robot):
    guests.add(payload())
    guests.revoke("คุณแขก")
    again = payload(seed=41)
    again["consent"] = record("คุณแขก", at=datetime.now(timezone.utc).astimezone() + timedelta(minutes=5))
    assert guests.add(again)["ok"] is True


def test_a_withdrawn_guests_record_goes_a_week_later(robot):
    doc = payload()
    doc["consent"] = record("คุณแขก", days=300)
    guests.add(doc)
    guests.revoke("คุณแขก")
    now = datetime.now(timezone.utc).astimezone()
    guests.purge(now + timedelta(days=6))
    assert consent.status("คุณแขก") == consent.REVOKED
    guests.purge(now + timedelta(days=guests.REVOKED_KEEP_DAYS, hours=1))
    assert "คุณแขก" not in consent.load()


def test_a_withdrawal_for_a_name_that_never_arrived_is_a_guests_and_goes(robot):
    guests.revoke("คุณพิมพ์ผิด")
    assert consent.load()["คุณพิมพ์ผิด"]["consent_version"] == consent.EVENT_GUEST
    guests.purge(datetime.now(timezone.utc).astimezone() + timedelta(days=guests.REVOKED_KEEP_DAYS, hours=1))
    assert "คุณพิมพ์ผิด" not in consent.load()


def test_a_guests_withdrawal_does_not_write_their_name_to_server_log(robot, caplog):
    guests.add(payload())
    with caplog.at_level("INFO"):
        guests.revoke("คุณแขก")
    assert "คุณแขก" not in caplog.text
