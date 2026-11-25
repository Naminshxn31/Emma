"""The scan booth (app/booth.py, scripts/booth_app.py). No camera and no models: a
seeded batch of poses, the real save, a stand-in for the robot."""
from __future__ import annotations

import importlib.util
import json
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from app import booth as booth_module, consent, enrollment, face_scan, faces, guests
from app.booth import Booth, Link, Outbox
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
UNTIL_DAYS = 3


def batch(seed=1) -> list[dict]:
    """The five poses of one person: alike enough for `review`'s link floor."""
    rng = np.random.default_rng(seed)
    base = rng.normal(size=512).astype(np.float32)
    base /= np.linalg.norm(base)
    out = []
    for i, step in enumerate(enrollment.STEPS):
        v = base + 0.3 * rng.normal(size=512).astype(np.float32) / 22.6
        v = (v / np.linalg.norm(v)).astype(np.float32)
        out.append({"vec": v, "crop": np.full((40, 40, 3), i * 30, np.uint8), "step": step["key"],
                    "width": 300 - i, "side": 0})
    return out


class FakeLink:
    """The robot: answers what the test says, keeps what it was sent."""

    def __init__(self, status=200, body=None):
        self.status, self.body = status, body or {"ok": True}
        self.sent: list[tuple[str, dict]] = []
        self.down = False

    def post(self, path, doc, timeout=1.0):
        if self.down:
            raise OSError("no route")
        self.sent.append((path, doc))
        return self.status, self.body


@pytest.fixture
def policy(monkeypatch, tmp_path):
    folder = tmp_path / "policies"
    folder.mkdir()
    (folder / ("%s.txt" % consent.EVENT_GUEST)).write_text("ข้อความของเจ้าของ", encoding="utf-8")
    monkeypatch.setattr(booth_module, "POLICY_DIR", folder)
    return folder


@pytest.fixture
def make(monkeypatch, tmp_path, policy):
    # Booth points the process's store at its own folder; these put it back.
    monkeypatch.setattr(enrollment, "ENROLLED", enrollment.ENROLLED)
    monkeypatch.setattr(settings, "face_gallery", settings.face_gallery)
    monkeypatch.setattr(settings, "face_consent_days", settings.face_consent_days)
    monkeypatch.setattr(settings, "face_threshold", settings.face_threshold)
    monkeypatch.setattr(settings, "face_scan_pin", "4321")
    monkeypatch.setattr(settings, "face_scan_enabled", True)
    face_scan.reset()

    def build(link=None) -> Booth:
        until = datetime.now(timezone.utc).astimezone() + timedelta(days=UNTIL_DAYS)
        return Booth(tmp_path / "booth", until=until, link=link)

    return build


def scanned(b: Booth, seed=1) -> Booth:
    assert b.start()["ok"]
    assert b.answer(True)["ok"]
    b._scan = enrollment.Session(pending=batch(seed))
    return b


# ---- the notice ------------------------------------------------------------------------------

def test_no_notice_no_scan_and_no_words_of_our_own(make, policy):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).unlink()
    b = make()
    r = b.start()
    assert r["ok"] is False and "consent_text" not in r


@pytest.mark.parametrize("content", [b"", b"   \n", b"\xff\xfe not utf-8"])
def test_a_blank_or_broken_notice_is_no_notice(make, policy, content):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).write_bytes(content)
    assert make().start()["ok"] is False


def test_the_guest_reads_the_owners_words(make):
    r = make().start()
    assert r["consent_text"] == "ข้อความของเจ้าของ"
    assert r["consent_version"] == consent.EVENT_GUEST


SHIPPED = ROOT / "data" / "policies" / ("%s.txt" % consent.EVENT_GUEST)


def test_the_shipped_notice_says_what_the_owner_decided_and_leaves_his_blanks():
    # Owner, 23 Nov 2026: written by us on his word ("เขียนแจ้งไว้"): kept for later visits,
    # and the name goes to Google. Who holds the data and whom to ask are his to fill in.
    text = SHIPPED.read_text(encoding="utf-8")
    for blank in ("{until_th}", "{until_en}", "{controller}", "{contact}"):
        assert blank in text
    assert "Google" in text and "กลับมาเยี่ยมชมโครงการ" in text and "come back to visit" in text
    assert "ถอนความยินยอม" in text and "withdraw" in text


def test_a_blank_this_machine_cannot_fill_opens_no_scan(make, policy):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).write_bytes(SHIPPED.read_bytes())
    r = make().start()
    assert r["ok"] is False and "BOOTH_CONTROLLER" in r["reason"] and "BOOTH_CONTACT" in r["reason"]
    assert "consent_text" not in r


def test_an_unknown_blank_is_no_notice_either(make, policy):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).write_text("ถึง {until_th} โดย {nobody}", encoding="utf-8")
    r = make().start()
    assert r["ok"] is False and "{nobody}" in r["reason"]


def test_the_guest_reads_the_booths_own_date_and_names(monkeypatch, tmp_path, make, policy):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).write_bytes(SHIPPED.read_bytes())
    b = make()
    b.until = datetime(2027, 12, 2, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    b.fields = {"controller": "บริษัทตัวอย่าง", "contact": "สำนักงานขาย"}
    th = b.start("th")["consent_text"]
    en = b.set_lang("en")["consent_text"]
    assert "{" not in th and "{" not in en
    # Midnight on the 2nd is the end of the 1st: the last day kept is what a person reads.
    assert "1 ธันวาคม 2570" in th and "1 December 2027" in en
    assert "บริษัทตัวอย่าง" in th and "สำนักงานขาย" in th and "บริษัทตัวอย่าง" in en
    # one language at a time (owner 23 Nov: "เลือกภาษาได้ th en")
    assert "Google" in th and "Google" in en
    assert "December" not in th and "ธันวาคม" not in en and booth_module.NOTICE_SPLIT not in th + en


def test_dates_are_bangkok_days_whatever_the_machines_zone():
    china = timezone(timedelta(hours=8))
    assert booth_module.thai_date(datetime(2027, 12, 2, 1, 0, tzinfo=china)) == "1 ธันวาคม 2570"   # 00:00 BKK
    assert booth_module.thai_date(datetime(2027, 12, 2, 2, 0, tzinfo=china)) == "2 ธันวาคม 2570"   # 01:00 BKK
    assert booth_module.thai_date(datetime(2027, 12, 2, 0, 30, tzinfo=china)) == "1 ธันวาคม 2570"  # 23:30 BKK
    assert booth_module.english_date(datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc)) == "1 January 2027"


@pytest.mark.parametrize("answer", [False, None, "true", 1, "ยินยอม"])
def test_only_a_real_yes_is_a_yes(make, answer):
    b = make()
    b.start()
    assert b.answer(answer).get("closed") is True
    assert b._scan is None


def test_nothing_is_looked_at_before_the_yes(make, monkeypatch):
    b = make()
    b.start()
    monkeypatch.setattr(faces, "locate", lambda f: pytest.fail("looked before consent"))
    assert b.look("data:image/jpeg;base64,AAAA")["ok"] is False
    assert b.capture("data:image/jpeg;base64,AAAA")["ok"] is False
    assert b.save("คุณแขก")["ok"] is False


# ---- the end of the event --------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "tomorrow", "2026-11-27T00:00:00", "2020-01-01T00:00:00+07:00",
                                   "2099-01-01T00:00:00+07:00"])
def test_no_booth_without_a_real_end(value):
    with pytest.raises(ValueError):
        booth_module.parse_until(value)


def test_the_booth_will_not_start_with_no_end_at_all(tmp_path):
    with pytest.raises(ValueError):
        Booth(tmp_path / "booth", until=None)


def test_a_guests_consent_is_stamped_and_ends_on_the_booths_date(make):
    b = make(FakeLink())
    scanned(b)
    r = b.save("คุณแขก")
    assert r["ok"] is True
    entry = consent.load()["คุณแขก"]
    assert entry["consent_version"] == consent.EVENT_GUEST and entry["consent_by"] == "คุณแขก"
    end = datetime.fromisoformat(entry["expires_at"])
    assert abs((end - b.until).total_seconds()) < 5
    assert consent.status("คุณแขก") == consent.OK


def test_past_the_end_everything_goes_from_the_booth(make):
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    assert b.purge(b.until + timedelta(seconds=1)) == 1
    assert "คุณแขก" not in consent.load()
    assert "คุณแขก" not in faces.Gallery.load(settings.face_gallery).names
    assert not (enrollment.ENROLLED / "คุณแขก").exists()


# ---- its own store -----------------------------------------------------------------------------

def test_a_guest_never_lands_in_this_machines_own_gallery(make, tmp_path):
    before = settings.face_gallery
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    assert Path(settings.face_gallery).parent == b.store
    assert Path(settings.face_gallery) != Path(before)
    assert enrollment.ENROLLED.parent == b.store
    assert consent.people_path().parent == b.store


def test_a_taken_name_is_refused_and_the_scan_kept(make):
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    scanned(b, seed=2)
    r = b.save("คุณแขก")
    assert r["ok"] is False and b._scan is not None and b._scan.pending_count == 5


def test_a_face_filed_under_another_name_never_shows_that_name(make):
    b = make(FakeLink())
    scanned(b, seed=1)
    b.save("คนแรก")
    scanned(b, seed=1)
    r = b.save("คนที่สอง")
    assert r["ok"] is False and "คนแรก" not in r["reason"]


def test_the_booths_same_face_check_is_at_the_events_bar(make):
    make()
    assert settings.face_threshold == settings.face_approach_threshold


def test_an_unfinished_scan_is_not_saved(make):
    b = make(FakeLink())
    b.start()
    b.answer(True)
    b._scan = enrollment.Session(pending=batch()[:2])
    assert b.save("คุณแขก")["ok"] is False


def test_somebody_who_walked_off_is_forgotten(make):
    b = make()
    scanned(b)
    b.sweep(b._touched + booth_module.IDLE_S + 1)
    assert b._scan is None


# ---- the push ------------------------------------------------------------------------------------

def test_the_robot_gets_vectors_of_the_kept_poses_and_the_booths_record(make):
    link = FakeLink()
    b = make(link)
    scanned(b)
    r = b.save("คุณแขก")
    assert r["robot"] == "accepted"
    path, doc = link.sent[0]
    assert path == "/guests/add"
    assert doc["name"] == "คุณแขก" and doc["model_tag"] == faces.MODEL_TAG
    assert len(doc["vectors"]) == len(booth_module.ROWS)
    assert doc["consent"] == consent.load()["คุณแขก"]
    assert guests.decode_vectors(doc["vectors"]) is not None
    assert "crop" not in json.dumps(doc, ensure_ascii=False)


def test_pictures_do_not_outlive_the_robots_yes(make):
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    assert list((enrollment.ENROLLED / "คุณแขก").glob("*.jpg")) == []
    assert b.outbox.pending() == 0


def test_a_robot_that_is_away_costs_a_delay_not_the_guest(make):
    link = FakeLink()
    link.down = True
    b = make(link)
    scanned(b)
    r = b.save("คุณแขก")
    assert r["ok"] is True and r["robot"] == "pending"
    assert b.outbox.pending() == 1
    assert len(list((enrollment.ENROLLED / "คุณแขก").glob("*.jpg"))) == 5    # not told yet
    link.down = False
    b.flush()
    assert b.outbox.pending() == 0 and link.sent[0][1]["name"] == "คุณแขก"


def test_a_wrong_key_or_a_busy_robot_keeps_the_guest_in_the_queue(make):
    for status in (401, 429, 503):
        link = FakeLink(status=status, body={"ok": False, "error": "x"})
        b = make(link)
        scanned(b, seed=status)
        assert b.save("คุณ%d" % status)["robot"] == "pending"
        assert b.outbox.pending() >= 1
        link.status, link.body = 200, {"ok": True}
        b.flush()
        assert b.outbox.pending() == 0


def test_a_refusal_is_told_to_the_guest_without_anybody_elses_name(make):
    link = FakeLink(status=409, body={"ok": False, "error": "same_face"})
    b = make(link)
    scanned(b)
    r = b.save("คุณแขก")
    assert r["robot"] == "refused" and "เจ้าหน้าที่" in r["reason"]
    assert b.outbox.pending() == 0


def test_a_scan_the_robot_refused_does_not_sit_on_the_booth_for_a_year(make):
    # Kept a year now: a refusal's pictures, vectors and record have no further use here.
    b = make(FakeLink(status=409, body={"ok": False, "error": "same_face"}))
    scanned(b)
    b.save("คุณแขก")
    assert "คุณแขก" not in consent.load()
    assert not (enrollment.ENROLLED / "คุณแขก").exists()
    gallery = Path(settings.face_gallery)
    assert not gallery.is_file() or "คุณแขก" not in faces.Gallery.load(gallery).names
    scanned(b, seed=2)
    b.outbox.link = FakeLink()
    assert b.save("คุณแขก")["robot"] == "accepted"         # they scan again, with the staff


class StatusLink(FakeLink):
    def __init__(self, status=200, body=None, error=None):
        super().__init__()
        self.get_status, self.get_body, self.error = status, body, error
        self.asked = 0

    def get(self, path, timeout=1.0):
        self.asked += 1
        if self.error is not None:
            raise self.error
        return self.get_status, self.get_body or {}


def _ok_body(**over):
    body = {"ok": True, "guests": 4, "model_tag": faces.MODEL_TAG, "max_days": consent.MAX_GUEST_DAYS}
    body.update(over)
    return body


@pytest.mark.parametrize("link,state", [
    (StatusLink(body=_ok_body()), "ok"),
    (StatusLink(body=_ok_body(model_tag="other")), "model"),
    (StatusLink(body=_ok_body(max_days=None)), "old"),        # a robot from before 23 Nov
    (StatusLink(body=_ok_body(max_days=1.0)), "old"),
    (StatusLink(status=401, body={"ok": False, "error": "key"}), "key"),
    (StatusLink(status=429, body={"ok": False, "error": "locked"}), "locked"),
    (StatusLink(error=ssl.SSLError("pin")), "certificate"),
    (StatusLink(error=OSError("no route")), "unreachable"),
    (StatusLink(status=500), "error"),
])
def test_the_booth_knows_whether_a_scan_will_reach_the_robot(make, link, state):
    b = make(link)
    assert b.check_robot(force=True)["state"] == state
    s = b.status()
    assert s["robot"] == state and s["robot_th"]
    assert "คุณ" not in json.dumps(s, ensure_ascii=False)


def test_the_robot_is_asked_twice_a_minute_not_every_frame(make):
    link = StatusLink(body=_ok_body())
    b = make(link)
    b.check_robot(now=100.0)
    b.check_robot(now=110.0)
    assert link.asked == 1
    b.check_robot(now=100.0 + booth_module.ROBOT_CHECK_S + 1)
    assert link.asked == 2


def test_no_link_is_said_not_guessed(make):
    b = make(None)
    assert b.check_robot(force=True)["state"] == "no_link"


def test_the_staff_list_is_for_the_terminal_with_names_and_ends(make):
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    rows = b.guest_list()
    assert [r["name"] for r in rows] == ["คุณแขก"] and rows[0]["expires_at"] and rows[0]["revoked"] is False
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "/list" not in page and "guest_list" not in page


def test_a_withdrawal_cannot_overtake_its_own_save(make):
    link = FakeLink()
    link.down = True
    b = make(link)
    scanned(b)
    b.save("คุณแขก")
    b.revoke("คุณแขก", "4321")
    assert b.outbox.pending() == 2
    link.down = False
    b.flush()
    assert [p for p, _ in link.sent] == ["/guests/add", "/guests/revoke"]


def test_an_unreadable_queue_file_does_not_block_the_queue(make, tmp_path):
    link = FakeLink()
    b = make(link)
    b.outbox.folder.mkdir(parents=True, exist_ok=True)
    (b.outbox.folder / "00000000000000001-0001-add.json").write_text("{ torn", encoding="utf-8")
    b.outbox.add("revoke", {"name": "ก"})
    b.flush()
    assert [p for p, _ in link.sent] == ["/guests/revoke"]


def test_no_link_keeps_everything_queued(make):
    b = make(None)
    scanned(b)
    assert b.save("คุณแขก")["robot"] == "pending"
    assert b.status()["linked"] is False and b.status()["pending"] == 1
    assert "คุณแขก" not in json.dumps(b.status(), ensure_ascii=False)


def test_end_to_end_the_saved_guest_lands_on_a_robot_as_a_named_consented_row(make, monkeypatch, tmp_path):
    """The booth's document through the robot's real code, on a store of its own."""
    robot = tmp_path / "robot"
    answers = []

    class ToRobot(FakeLink):
        def post(self, path, doc, timeout=1.0):
            here = enrollment.ENROLLED, settings.face_gallery, settings.face_guests_file
            enrollment.ENROLLED = robot / "enrolled"
            settings.face_gallery, settings.face_guests_file = str(robot / "g.npz"), str(robot / "guests.npz")
            try:
                result = guests.add(doc) if path.endswith("add") else guests.revoke(doc.get("name"))
                answers.append(result)
                assert consent.status(doc["name"]) in (consent.OK, consent.REVOKED)
                return (200 if result["ok"] else 409), result
            finally:
                enrollment.ENROLLED, settings.face_gallery, settings.face_guests_file = here

    monkeypatch.setattr(settings, "face_guests_file", "")
    b = make(ToRobot())
    scanned(b)
    assert b.save("คุณแขก")["robot"] == "accepted"
    assert answers == [{"ok": True, "state": "recorded"}]
    rows = faces.Gallery.load(robot / "guests.npz")
    assert set(rows.names) == {"คุณแขก"} and len(rows) == len(booth_module.ROWS)
    assert b.revoke("คุณแขก", "4321")["robot_pending"] == 0
    assert faces.Gallery.load(robot / "guests.npz").names == []


# ---- withdrawing at the booth -----------------------------------------------------------------------

def test_withdrawing_needs_the_pin_and_counts_wrong_ones(make):
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    with pytest.raises(face_scan.ScanError) as wrong:
        b.revoke("คุณแขก", "0000")
    assert wrong.value.status == 403
    assert consent.status("คุณแขก") == consent.OK
    for _ in range(face_scan.PIN_TRIES):
        with pytest.raises(face_scan.ScanError):
            b.revoke("คุณแขก", "0000")
    with pytest.raises(face_scan.ScanError) as locked:
        b.revoke("คุณแขก", "4321")
    assert locked.value.status == 429


def test_withdrawing_takes_it_all_off_the_booth(make):
    link = FakeLink()
    b = make(link)
    scanned(b)
    b.save("คุณแขก")
    assert b.revoke("คุณแขก", "4321")["had"] is True
    assert consent.status("คุณแขก") == consent.REVOKED
    assert "คุณแขก" not in faces.Gallery.load(settings.face_gallery).names
    assert not (enrollment.ENROLLED / "คุณแขก").exists()
    assert link.sent[-1] == ("/guests/revoke", {"name": "คุณแขก"})


# ---- the link -------------------------------------------------------------------------------------

def test_the_key_never_leaves_for_a_certificate_it_did_not_pin():
    """A real TLS listener on loopback, on this machine's own certificate. Without
    it this test skips, and then the pin itself is NOT being checked anywhere."""
    cert, key = ROOT / "certs" / "lan-cert.pem", ROOT / "certs" / "lan-key.pem"
    if not (cert.is_file() and key.is_file()):
        pytest.skip("no certs/lan-*.pem on this machine: the certificate pin is NOT being verified here")
    import socket

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    received: list[bytes] = []
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve():
        for _ in range(2):
            raw, _addr = listener.accept()
            try:
                with ctx.wrap_socket(raw, server_side=True) as tls:
                    tls.settimeout(0.3)
                    # Headers and body arrive as two writes: everything, then answer.
                    while True:
                        try:
                            chunk = tls.recv(4096)
                        except OSError:
                            break
                        if not chunk:
                            break
                        received.append(chunk)
                    tls.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{"ok":true}')
            except (ssl.SSLError, OSError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        wrong = Link("https://127.0.0.1:%d" % port, "SECRET", "0" * 64)
        with pytest.raises(ssl.SSLError):
            wrong.post("/guests/status", {})
        assert not any(b"SECRET" in chunk for chunk in received)
        right = Link("https://127.0.0.1:%d" % port, "SECRET", booth_module.cert_pin(cert))
        status, body = right.post("/guests/status", {})
        assert status == 200 and body == {"ok": True}
        thread.join(3.0)
        assert any(b"SECRET" in chunk for chunk in received)
    finally:
        listener.close()


def test_outbox_with_no_link_says_later_and_keeps_the_file(tmp_path):
    box = Outbox(tmp_path, None)
    box.add("add", {"name": "ก"})
    assert box.flush()["pending"] == 1 and box.pending() == 1


# ---- the front -------------------------------------------------------------------------------------

def _app():
    spec = importlib.util.spec_from_file_location("booth_app_for_test", ROOT / "scripts" / "booth_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_it_binds_loopback_and_has_no_flag_for_anything_else():
    source = (ROOT / "scripts" / "booth_app.py").read_text(encoding="utf-8")
    assert '("127.0.0.1", args.port)' in source
    assert "--host" not in source and "0.0.0.0" not in source


def test_another_sites_page_cannot_post_to_it():
    mod = _app()

    class Fake:
        headers = {"Origin": "https://evil.example", "Host": "127.0.0.1:8771"}

    assert mod.Handler._own_page(Fake) is False
    Fake.headers = {"Origin": "http://127.0.0.1:8771", "Host": "127.0.0.1:8771"}
    assert mod.Handler._own_page(Fake) is True
    Fake.headers = {"Host": "127.0.0.1:8771"}
    assert mod.Handler._own_page(Fake) is True


def test_the_page_holds_no_rule_and_keeps_nothing():
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "localStorage" not in page and "sessionStorage" not in page
    assert "FRONTAL" not in page and "threshold" not in page.lower()


def test_scan_and_booth_share_the_save_rules_no_second_copy():
    source = (ROOT / "app" / "booth.py").read_text(encoding="utf-8")
    assert "cv2" not in source and "imwrite" not in source and "face_scan._name_taken" in source



# ---- review 23 Nov ------------------------------------------------------------------------------

def test_no_pin_set_means_nobody_withdraws_anybody_at_the_screen(make, monkeypatch):
    # "" == "" used to pass the compare: a passer-by could withdraw any guest.
    b = make(FakeLink())
    scanned(b)
    b.save("คุณแขก")
    monkeypatch.setattr(settings, "face_scan_pin", "")
    r = b.revoke("คุณแขก", "")
    assert r["ok"] is False and "BOOTH_PIN" in r["reason"]
    assert consent.status("คุณแขก") == consent.OK


@pytest.mark.parametrize("error", ["bad_consent", "wrong_model", "off", "full", "bad_vectors", "bad_request", "404"])
def test_a_fault_of_this_setup_keeps_the_guest_queued_not_deleted(make, error):
    # A robot on the old 30-day rule answers bad_consent to everybody: that is not the guest's refusal.
    link = FakeLink(status=404 if error == "404" else 409, body={} if error == "404" else {"ok": False, "error": error})
    b = make(link)
    scanned(b)
    assert b.save("คุณแขก")["robot"] == "pending"
    assert b.outbox.pending() == 1 and "คุณแขก" in consent.load()
    assert b.status()["push_error"] == error
    link.status, link.body = 200, {"ok": True}
    b.flush()
    assert b.outbox.pending() == 0 and b.status()["push_error"] is None


@pytest.mark.parametrize("error", sorted(booth_module.GUEST_REFUSALS))
def test_only_the_guests_own_refusals_end_the_scan(make, error):
    b = make(FakeLink(status=409, body={"ok": False, "error": error}))
    scanned(b)
    assert b.save("คุณแขก")["robot"] == "refused"
    assert b.outbox.pending() == 0 and "คุณแขก" not in consent.load()


def test_a_refusal_after_the_guest_left_is_named_to_the_staff_once(make):
    link = FakeLink()
    link.down = True
    b = make(link)
    scanned(b)
    assert b.save("คุณแขก")["robot"] == "pending"          # told "in a moment"
    link.down, link.status, link.body = False, 409, {"ok": False, "error": "same_face"}
    b.flush()                                               # housekeeping, nobody at the screen
    assert b.take_late_refusals() == [("คุณแขก", "same_face")]
    assert b.take_late_refusals() == []
    assert b.status()["late_refused"] == 1


def test_a_stale_answer_is_not_told_to_the_next_guest_under_that_name(make):
    link = FakeLink(status=409, body={"ok": False, "error": "same_face"})
    b = make(link)
    scanned(b)
    link.down = True
    b.save("คุณแขก")
    link.down = False
    b.flush()                                               # refused later: forgotten, result left behind
    link.down = True
    scanned(b, seed=5)
    assert b.save("คุณแขก")["robot"] == "pending"          # this scan is queued, not "refused"


def test_a_withdrawal_reaches_the_robot_even_if_the_local_delete_fails(make, monkeypatch):
    link = FakeLink()
    b = make(link)
    scanned(b)
    b.save("คุณแขก")

    def broken(names):
        raise OSError("gallery busy")

    monkeypatch.setattr(b, "_forget_here", broken)
    with pytest.raises(OSError):
        b.revoke("คุณแขก", "4321")
    assert b.outbox.pending() == 1            # queued for the robot before the local delete


def test_a_withdrawals_record_goes_a_week_later_not_a_year(make):
    b = make(FakeLink())
    b.until = datetime.now(timezone.utc).astimezone() + timedelta(days=300)
    scanned(b)
    b.save("คุณแขก")
    b.revoke("คุณแขก", "4321")
    now = datetime.now(timezone.utc).astimezone()
    assert b.purge(now + timedelta(days=6)) == 0 and "คุณแขก" in consent.load()
    assert b.purge(now + timedelta(days=guests.REVOKED_KEEP_DAYS, hours=1)) == 1
    assert "คุณแขก" not in consent.load()


def test_an_unreadable_queue_file_is_purged_it_holds_a_face(make):
    b = make(None)
    b.outbox.folder.mkdir(parents=True, exist_ok=True)
    (b.outbox.folder / "x.bad").write_text("{}", encoding="utf-8")
    b.purge()
    assert not list(b.outbox.folder.glob("*.bad"))


def test_wipe_leaves_nothing_of_the_guests_and_waits_for_the_queue(make):
    link = FakeLink()
    link.down = True
    b = make(link)
    scanned(b)
    b.save("คุณแขก")
    assert b.wipe() == {"ok": False, "pending": 1}
    link.down = False
    b.flush()
    assert b.wipe()["ok"] is True
    assert consent.load() == {}
    assert not Path(settings.face_gallery).exists() and not enrollment.ENROLLED.exists()


def test_a_rebinding_page_on_another_name_is_not_this_page():
    mod = _app()

    class Fake:
        headers = {"Origin": "http://rebind.example:8771", "Host": "rebind.example:8771"}

    assert mod.Handler._own_page(Fake) is False
    Fake.headers = {"Origin": "http://localhost:8771", "Host": "localhost:8771"}
    assert mod.Handler._own_page(Fake) is True


def test_check_is_not_ready_while_any_line_says_so(monkeypatch, tmp_path, policy, capsys):
    """--check counted its failures in `bad` and then reused the name for a file
    list: one [ !! ] line and still "พร้อมซ้อม", exit 0 (found running it, 23 Nov)."""
    mod = _app()
    monkeypatch.setattr(booth_module, "DEFAULT_STORE", tmp_path / "booth")
    monkeypatch.setattr(enrollment, "ENROLLED", enrollment.ENROLLED)
    monkeypatch.setattr(settings, "face_gallery", settings.face_gallery)
    monkeypatch.setattr(settings, "face_threshold", settings.face_threshold)
    monkeypatch.setattr(mod.faces, "available", lambda: True)
    monkeypatch.setenv("BOOTH_CONSENT_UNTIL", (datetime.now(timezone.utc).astimezone()
                                               + timedelta(days=30)).isoformat(timespec="seconds"))
    monkeypatch.setenv("BOOTH_PIN", "")                 # the one failing line
    monkeypatch.setenv("BOOTH_CONTROLLER", "x")
    monkeypatch.setenv("BOOTH_CONTACT", "x")
    robot = StatusLink(body=_ok_body())                 # a robot that answers: the branch that had the bug
    robot.pin = "0" * 64
    monkeypatch.setattr(mod, "_link", lambda: (robot, None))
    assert mod.check() == 1
    assert "พร้อมซ้อม" not in capsys.readouterr().out.splitlines()[-1]



# ---- the check-in kiosk hands a guest over (owner 23 Nov: one PC) -------------------------------

@pytest.mark.parametrize("value,expect", [
    ("https://embassy-agent-preview.embassy-mtk.workers.dev/kiosk", "https://embassy-agent-preview.embassy-mtk.workers.dev/kiosk"),
    ("http://127.0.0.1:8095/kiosk.html", "http://127.0.0.1:8095/kiosk.html"),
    ("http://localhost:8095/kiosk.html", "http://localhost:8095/kiosk.html"),
    ("http://evil.example/", None),            # plain http elsewhere: no
    ("javascript:alert(1)", None),
    ("//evil.example/", None),
    ("", None),
])
def test_the_way_back_is_https_or_this_machine_only(value, expect):
    assert booth_module.return_url(value) == expect


def test_the_way_back_comes_from_the_booths_setting_not_the_link(make):
    b = make(None)
    assert b.status()["return_url"] is None
    b.back = "https://kiosk.example/kiosk"
    assert b.status()["return_url"] == "https://kiosk.example/kiosk"
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "back = s.return_url" in page                  # from /status, i.e. BOOTH_RETURN_URL
    assert ".get('back')" not in page and ".get('return')" not in page and "get('url')" not in page


def test_a_handed_over_name_is_only_a_prefill_and_never_in_a_url(make):
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    # review 23 Nov: a #guest=<name> link sat in the browser's history; now a form body, asked for once
    assert "get('guest')" not in page and "location.hash === '#handoff'" in page and "post('/claim')" in page
    # the guest still reads the notice and presses "ยินยอม": the hand-over only opens the notice
    assert "$('begin').onclick();" in page and "agree: true" in page
    assert "window.top !== window.self" in page        # never from inside somebody else's frame


def test_the_kiosk_hands_over_once_and_not_for_long(make):
    b = make(None)
    b.hand_over("  Anan   Wongsa ")
    assert b.claim_hand_over() == "Anan Wongsa"
    assert b.claim_hand_over() is None                 # once
    b.hand_over("x" * 500)
    assert len(b.claim_hand_over()) == 80
    b.hand_over("Late")
    assert b.claim_hand_over(now=time.monotonic() + booth_module.HANDOFF_S + 1) is None
    b.hand_over("")
    assert b.claim_hand_over() is None


def test_the_hand_over_door_takes_a_form_from_any_page_but_only_on_this_machines_name(make, monkeypatch):
    import io

    mod = _app()
    b = make(None)
    monkeypatch.setattr(mod, "BOOTH", b)

    class Req:
        def __init__(self, host, body):
            self.headers = {"Host": host}
            self.rfile = io.BytesIO(body)
            self.sent = []

        def send_error(self, code):
            self.sent.append(code)

        def send_response(self, code):
            self.sent.append(code)

        def send_header(self, k, v):
            self.sent.append((k, v))

        def end_headers(self):
            pass

    body = "guest=Anan+Wongsa".encode()
    r = Req("rebind.example:8771", body)
    mod.Handler._hand_over(r, len(body))
    assert r.sent == [403] and b.claim_hand_over() is None
    r = Req("127.0.0.1:8771", body)
    mod.Handler._hand_over(r, len(body))
    assert r.sent[0] == 303 and ("Location", "/#handoff") in r.sent      # the name stays out of the URL
    assert b.claim_hand_over() == "Anan Wongsa"


def test_the_picked_camera_outlives_the_page(make):
    b = make(None)
    assert b.status()["camera"] is None
    b.camera = "device-2"
    assert b.status()["camera"] == "device-2"
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "post('/camera'" in page and "preferredCamera = s.camera" in page


def test_a_guest_typing_a_name_is_not_timed_out_by_the_server(make, monkeypatch):
    b = make(FakeLink())
    scanned(b)                                          # every pose in: the name screen
    b._touched -= booth_module.IDLE_S - 10              # the last pose was 80 s ago
    assert b.alive()["ok"] is True                      # a keystroke now
    b.sweep(now=time.monotonic() + 20)                  # 100 s after the last pose, 20 s after typing
    assert b._scan is not None
    b.sweep(now=time.monotonic() + booth_module.IDLE_S + 1)   # then they walked off
    assert b._scan is None
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "post('/alive')" in page


def test_a_booth_that_died_mid_way_still_lets_the_screen_go_back(make):
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "offline: true" in page                      # post() never throws
    assert "if (back) location.replace(back + '#booth=' + (how || 'cancel')); else history.back();" in page


def test_the_kiosk_is_told_a_guest_walked_off_so_it_keeps_their_pass_off_the_screen():
    # review 23 Nov: after 90 s untouched the booth went back to check-in, and the kiosk showed the pass
    # of a guest who was no longer there to whoever stood there next
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "abandon('idle')" in page and "home(how === 'idle' ? 'idle' : 'cancel')" in page
    assert "home('done')" in page and "home('cancel')" in page


def test_the_kiosk_settings_never_reach_the_robot():
    rs_spec = importlib.util.spec_from_file_location("rs_for_booth_test", ROOT / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(rs_spec)
    rs_spec.loader.exec_module(rs)
    assert {"BOOTH_RETURN_URL", "BOOTH_PIN", "GUEST_SYNC_URL"} <= rs.ENV_DROP and "GUEST_SYNC_KEY" not in rs.ENV_DROP


def test_a_second_booth_on_the_same_port_is_refused():
    """23 Nov: on Windows a second booth could bind the booth's port beside the first
    (SO_REUSEADDR), and the two split one guest's requests between them."""
    mod = _app()
    first = mod.BoothServer(("127.0.0.1", 0), mod.Handler)
    try:
        port = first.server_address[1]
        with pytest.raises(OSError):
            mod.BoothServer(("127.0.0.1", port), mod.Handler)
    finally:
        first.server_close()


# ---- TH / EN (owner 23 Nov: "เลือกภาษาได้ th en", "ค่าเริ่มต้น en") ------------------------------

def test_every_pose_and_every_spoken_line_has_english():
    for step in enrollment.STEPS:
        assert booth_module.to_en(step["prompt"]) != booth_module.EN_FALLBACK, step["prompt"]
    for line in enrollment.SPOKEN.values():
        assert booth_module.to_en(line) != booth_module.EN_FALLBACK, line
    for line in list(booth_module.REFUSED_TH.values()) + [booth_module._REFUSED]:
        assert booth_module.to_en(line) != booth_module.EN_FALLBACK, line
    for _prefix, line in face_scan._REVIEW_THAI:
        assert booth_module.to_en(line) != booth_module.EN_FALLBACK, line
    for bad in ["", ".x", "a..b", "a/b", "x" * 200, "CON"]:
        try:
            enrollment.safe_person_name(bad)
        except ValueError as exc:
            assert booth_module.to_en(str(exc)) != booth_module.EN_FALLBACK, str(exc)


def test_the_scans_own_words_with_numbers_come_out_in_english():
    assert booth_module.to_en("มี 3 หน้าในภาพ — ทีละคน") == "3 faces in the picture, one person at a time"
    assert booth_module.to_en("มองตรงมาที่กล้อง (ตอนนี้ 120px ขอ 160px)") == "Look straight at the camera"
    assert booth_module.to_en("เข้ามาใกล้อีกนิด (90px ขอ 120px)") == "Come a little closer"
    assert "skipped" in booth_module.to_en("ถอยห่างออกไปสักก้าว — ข้าม (กล้องใกล้เกินไป)")
    assert booth_module.to_en("ข้อความที่ไม่มีใครแปล") == booth_module.EN_FALLBACK     # never Thai to an English guest
    assert booth_module.to_en("Already English") == "Already English"


def test_a_reply_is_put_into_the_guests_language_at_the_door():
    reply = {"ok": False, "reason": "ยังไม่เห็นหน้า", "say": "ยังไม่เห็นหน้า", "prompt": "มองตรงมาที่กล้อง",
             "steps": [{"key": "front", "prompt": "มองตรงมาที่กล้อง", "done": False}], "box": None}
    en = booth_module.in_language(reply, "en")
    assert en["reason"] == en["say"] == "I can't see your face yet" and en["steps"][0]["prompt"] == "Look straight at the camera"
    assert booth_module.in_language(reply, "th") == reply                 # Thai: exactly as the scan wrote it


def _post(mod, path, doc):
    """Drive the booth's real request handler, no socket: what it would send."""
    import io

    h = mod.Handler.__new__(mod.Handler)
    raw = json.dumps(doc).encode("utf-8")
    h.headers = {"Host": "127.0.0.1:8771", "Content-Length": str(len(raw)), "Origin": "http://127.0.0.1:8771"}
    h.rfile, h.path, sent = io.BytesIO(raw), path, []
    h._send = lambda payload, content_type="application/json", status=200: sent.append((status, payload))
    h.send_error = lambda code, *a: sent.append((code, None))
    mod.Handler.do_POST(h)
    return sent[0]


def test_the_door_answers_each_screen_in_its_own_language_and_staff_in_theirs(make, monkeypatch):
    mod = _app()
    b = make(FakeLink())
    monkeypatch.setattr(mod, "BOOTH", b)
    status, reply = _post(mod, "/look", {"image": "", "lang": "en"})            # no scan open
    assert reply["reason"] == "Please read the notice and agree first"
    status, reply = _post(mod, "/look", {"image": "", "lang": "th"})
    assert reply["reason"] == "ยังไม่ได้เริ่ม หรือยังไม่ได้ยินยอม"
    status, reply = _post(mod, "/save", {"name": "x", "lang": "en"})
    assert reply["reason"] == "Please read the notice and agree first"
    # staff: the withdraw panel is not a guest's screen, a wrong PIN or no PIN stays as written
    monkeypatch.setattr(settings, "face_scan_pin", "")
    status, reply = _post(mod, "/revoke", {"name": "x", "pin": "", "lang": "en"})
    assert "BOOTH_PIN" in reply["reason"]
    monkeypatch.setattr(settings, "face_scan_pin", "4321")
    status, reply = _post(mod, "/revoke", {"name": "x", "pin": "0000", "lang": "en"})
    assert status >= 400 and "PIN" in reply["reason"]
    # a /start that failed on setup keeps the setting to fix (said for the staff on the guest's screen)
    monkeypatch.setattr(b, "fields", {})
    (booth_module.POLICY_DIR / ("%s.txt" % consent.EVENT_GUEST)).write_bytes(SHIPPED.read_bytes())
    status, reply = _post(mod, "/start", {"lang": "en"})
    assert reply["ok"] is False and "BOOTH_CONTROLLER" in reply["reason"]


def test_the_page_starts_every_guest_in_english():
    page = (ROOT / "client" / "booth.html").read_text(encoding="utf-8")
    assert "var lang = 'en';" in page and "lang = 'en'; applyLang();" in page
    assert "post('/start', { lang: lang })" in page and "post('/lang', { lang: lang })" in page
    assert "speechSynthesis" in page                   # English is the browser's voice; the clips are Thai


def test_the_language_switch_brings_the_notice_in_that_language(make, policy):
    (policy / ("%s.txt" % consent.EVENT_GUEST)).write_bytes(SHIPPED.read_bytes())
    b = make()
    b.fields = {"controller": "x", "contact": "y"}
    assert "Let Emma remember you" in b.start("en")["consent_text"]
    assert "ให้ Emma จำคุณได้" in b.set_lang("th")["consent_text"]
    assert b.set_lang("fr")["lang"] == "th"                               # only en / th

