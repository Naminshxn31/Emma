"""
The face scan behind the call screen's hold menu: `app/face_scan.py` and
the `/face-scan/*` routes in `app/main.py`.

Everything here runs in tmp. `enrollment.ENROLLED` (and with it
people.json), `settings.face_gallery` and the policy folder are pointed at
tmp_path, because data/faces/ holds real people and data/policies/ holds
the owner's words. The face models are never loaded (conftest stubs them):
`faces.available` is patched True, and the `enrollment.Session` methods are
faked wherever a test is about the gate rather than the faces. Names are
made up.

The switches are pinned here, not inherited: the machine's .env turns this
feature on, and a suite whose colour follows the .env is the trap CLAUDE.md
has a whole table about.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import consent, enrollment, face_scan, faces, greeter
from app.config import settings
from app.main import app

REPO = Path(__file__).resolve().parent.parent
#: Read before any fixture patches it: the path the owner's file must be at.
REAL_POLICY_DIR = face_scan.POLICY_DIR

PIN = "2468"
TOKEN = "scan-test-token"
#: Fixture text only — deliberately not policy wording of any kind.
POLICY = "(ข้อความทดสอบ ไม่ใช่นโยบายจริง)\nบรรทัดที่สอง — test fixture only\n"
NAME = "ทดสอบ สแกน"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def scan_env(monkeypatch, tmp_path):
    clock = _Clock()
    monkeypatch.setattr(settings, "face_scan_enabled", True)
    monkeypatch.setattr(settings, "face_scan_pin", PIN)
    monkeypatch.setattr(settings, "face_scan_session_s", 600.0)
    monkeypatch.setattr(settings, "face_gallery", str(tmp_path / "faces" / "gallery.npz"))
    monkeypatch.setattr(enrollment, "ENROLLED", tmp_path / "faces" / "enrolled")
    monkeypatch.setattr(face_scan, "POLICY_DIR", tmp_path / "policies")
    monkeypatch.setattr(face_scan, "_clock", clock)
    monkeypatch.setattr(faces, "available", lambda: True)
    monkeypatch.setattr(greeter, "_paused_until", 0.0)
    monkeypatch.setattr(greeter, "_holds", {})
    (tmp_path / "policies").mkdir()
    face_scan.policy_path().write_bytes(POLICY.encode("utf-8"))
    face_scan.reset()
    yield clock
    face_scan.reset()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _off_the_loop() -> bool:
    """True inside a worker thread, False on the event loop's own thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True
    return False


@pytest.fixture
def fake_faces(monkeypatch):
    """The Session's face work, recorded instead of run."""
    calls: list[tuple] = []
    real_discard = enrollment.Session.discard

    def judge(self, frame, full_width=None):
        calls.append(("judge", frame.shape, full_width, _off_the_loop()))
        return {"ok": True, "reason": "", "box": [1, 2, 5, 6], "frame": [8, 8], **self.state()}

    def capture(self, frame, full_width=None):
        calls.append(("capture", frame.shape, full_width, _off_the_loop()))
        return {"ok": True, "shots": [], **self.state()}

    def save(self, name, replace=False, add_to_gallery=False, consent_version=None):
        calls.append(("save", name, replace, add_to_gallery, _off_the_loop(), consent_version))
        return {"ok": True, "name": name, "total": 5, "gallery_total": 5}

    def discard(self):
        calls.append(("discard",))
        return real_discard(self)

    for attr, fn in (("judge", judge), ("capture", capture), ("save", save), ("discard", discard)):
        monkeypatch.setattr(enrollment.Session, attr, fn)
    return calls


def _kinds(calls) -> list[str]:
    return [c[0] for c in calls]


def _jpeg() -> str:
    import cv2

    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _start(client, pin=PIN, **extra):
    return client.post("/face-scan/start", json={"pin": pin, **extra})


def _open(client, **extra) -> str:
    """A scan past the consent screen."""
    r = _start(client, **extra)
    assert r.status_code == 200, r.text
    sid = r.json()["session"]
    r = client.post("/face-scan/consent", json={"session": sid, "agree": True, **extra})
    assert r.status_code == 200, r.text
    return sid


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def _write_gallery(names: list[str], vecs: list[np.ndarray]) -> None:
    path = Path(settings.face_gallery)
    path.parent.mkdir(parents=True, exist_ok=True)
    faces.Gallery(list(names), np.stack(vecs), [{"group": "enrolled"} for _ in names]).save(path)


def _seed(sid: str, vecs: list[np.ndarray]) -> None:
    """Put a batch into the real Session, as five captures would have."""
    pending = face_scan._sessions[sid].enroll._pending
    for vec, step in zip(vecs, enrollment.STEPS):
        pending.append({"vec": vec, "crop": np.full((24, 24, 3), 128, np.uint8),
                        "step": step["key"], "width": 200, "side": 0})


def _complete(sid: str) -> None:
    """Every pose done, as far as the step flow can tell — for the tests
    whose save is faked and whose vectors therefore never matter. A save
    before the last pose is refused (see the incomplete-batch test)."""
    _seed(sid, [_unit(40 + i) for i in range(len(enrollment.STEPS))])
    assert face_scan._sessions[sid].enroll.state()["step"] is None


# -- the switches -------------------------------------------------------------

def test_the_scan_is_off_by_default_and_off_means_refused(client, monkeypatch):
    # The code default, read from the source: the live value is whatever
    # this machine's .env says, and that is exactly what must not decide.
    source = (REPO / "app" / "config.py").read_text(encoding="utf-8")
    assert re.search(r'face_scan_enabled: bool = _get_bool\("FACE_SCAN_ENABLED", False\)', source)
    assert re.search(r'face_scan_pin: str = os\.getenv\("FACE_SCAN_PIN", ""\)\.strip\(\)', source)

    monkeypatch.setattr(settings, "face_scan_enabled", False)
    r = _start(client)
    assert r.status_code == 403
    assert "FACE_SCAN_ENABLED" in r.json()["reason"]
    assert r.json()["ok"] is False
    assert face_scan._sessions == {}
    assert not greeter.paused()


def test_no_pin_configured_refuses_even_an_empty_pin(client, monkeypatch):
    monkeypatch.setattr(settings, "face_scan_pin", "")
    r = _start(client, pin="")
    assert r.status_code == 403
    assert "FACE_SCAN_PIN" in r.json()["reason"]
    assert face_scan._sessions == {}


def test_multi_session_refuses_the_whole_feature(client, fake_faces, monkeypatch):
    sid = _open(client)
    monkeypatch.setattr(settings, "multi_session", True)
    r = _start(client)
    assert r.status_code == 403 and "MULTI_SESSION" in r.json()["reason"]
    r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
    assert r.status_code == 403
    assert "judge" not in _kinds(fake_faces)


def test_missing_models_are_said_after_the_pin(client, monkeypatch):
    monkeypatch.setattr(faces, "available", lambda: False)
    assert _start(client, pin="0000").status_code == 403       # nothing said without the PIN
    r = _start(client)
    assert r.status_code == 503
    assert face_scan._sessions == {}


def test_look_and_capture_say_nothing_about_the_models_without_a_session(client, monkeypatch):
    # `start` keeps "are the models here" behind the PIN; look and capture
    # used to check the models first, so a made-up session id got 503 on a
    # machine without them and 404 on one with them.
    sid = _open(client)
    monkeypatch.setattr(faces, "available", lambda: False)
    for step in ("look", "capture"):
        r = client.post(f"/face-scan/{step}", json={"session": "nope", "image": _jpeg()})
        assert r.status_code == 404, step
        # A real scan whose models went away still hears why.
        r = client.post(f"/face-scan/{step}", json={"session": sid, "image": _jpeg()})
        assert r.status_code == 503, step


# -- the PIN ------------------------------------------------------------------

def test_five_wrong_pins_lock_that_client_out_for_a_minute(client, scan_env):
    # A success wipes the slips before it: three typos in the morning do
    # not leave the afternoon two tries from a lockout.
    for _ in range(3):
        assert _start(client, pin="1111").status_code == 403
    sid = _start(client).json()["session"]
    client.post("/face-scan/cancel", json={"session": sid})

    for _ in range(4):
        r = _start(client, pin="1111")
        assert r.status_code == 403 and r.json()["reason"] == "รหัส PIN ไม่ถูกต้อง"
    r = _start(client, pin="1111")
    assert r.status_code == 429

    # Locked means not compared: the right PIN is refused too.
    assert _start(client, pin=PIN).status_code == 429
    # Per client: the robot is not locked out by a guess at the PC.
    other = TestClient(app, client=("10.0.0.9", 50000))
    assert _start(other).status_code == 200

    scan_env.now += face_scan.LOCKOUT_S + 1
    assert _start(client).status_code == 200
    # A success starts the count again: four more slips are four 403s.
    for _ in range(4):
        assert _start(client, pin="1111").status_code == 403


def test_a_thai_keyboard_slip_is_a_wrong_pin_not_a_crash(client):
    r = _start(client, pin="๒๔๖๘")
    assert r.status_code == 403
    r = _start(client, pin=2468)                    # a number, not a string
    assert r.status_code == 403


def test_the_wrong_pin_table_forgets_what_no_longer_counts_and_has_a_ceiling(scan_env,
                                                                           monkeypatch):
    # Nothing used to remove an entry whose count stayed under five: with
    # uvicorn trusting X-Forwarded-For from 127.0.0.1, one entry per made-up
    # header, for ever (41 keys after 40 requests).
    for i in range(40):
        lan = TestClient(app, client=(f"10.0.1.{i}", 50000))
        assert _start(lan, pin="1111").status_code == 403
    assert len(face_scan._failures) == 40
    scan_env.now += face_scan.LOCKOUT_S         # slips older than a lockout
    late = TestClient(app, client=("10.0.2.1", 50000))
    assert _start(late, pin="1111").status_code == 403
    assert list(face_scan._failures) == ["10.0.2.1"]

    # Full: a new client is refused without its PIN being compared (the
    # right one included) — dropping old entries instead would hand their
    # owners a fresh five.
    monkeypatch.setattr(face_scan, "MAX_FAILURE_KEYS", 3)
    for i in range(2):
        lan = TestClient(app, client=(f"10.0.3.{i}", 50000))
        assert _start(lan, pin="1111").status_code == 403
    newcomer = TestClient(app, client=("10.0.4.1", 50000))
    assert _start(newcomer).status_code == 429
    assert len(face_scan._failures) == 3 and face_scan._sessions == {}
    # A client already counted is still judged on its PIN.
    assert _start(late).status_code == 200


# -- the gate -----------------------------------------------------------------

def test_the_token_comes_from_the_body_and_a_failed_gate_touches_nothing(client, monkeypatch):
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    assert _start(client).status_code == 403
    assert _start(client, token="wrong").status_code == 403
    assert _start(client, token=12345).status_code == 403  # not a 500

    # Ten gate failures with a wrong PIN spend no PIN tries...
    for _ in range(10):
        assert _start(client, pin="1111").status_code == 403
    assert face_scan._failures == {}
    sid = _open(client, token=TOKEN)

    # ...and cannot close somebody's scan either.
    r = client.post("/face-scan/cancel", json={"session": sid})
    assert r.status_code == 403
    assert sid in face_scan._sessions


def test_the_token_is_never_read_from_the_query_string(client, monkeypatch):
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    r = client.post(f"/face-scan/start?token={TOKEN}", json={"pin": PIN})
    assert r.status_code == 403
    assert face_scan._sessions == {}


def test_the_pcs_own_preview_passes_without_a_token_and_nothing_else_does(monkeypatch):
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    body = {"pin": PIN}

    local = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))
    r = local.post("/face-scan/start", json=body, headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 200, r.text
    local.post("/face-scan/cancel", json={"session": r.json()["session"]},
               headers={"Origin": "http://127.0.0.1:8000"})

    # A site open in the same browser, posting to localhost.
    r = local.post("/face-scan/start", json=body, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    # A local program with no Origin at all still needs the token.
    assert local.post("/face-scan/start", json=body).status_code == 403
    # DNS rebinding: Origin matches Host, but the name is not loopback.
    rebound = TestClient(app, base_url="http://rebind.example:8000",
                         client=("127.0.0.1", 50000))
    r = rebound.post("/face-scan/start", json=body,
                     headers={"Origin": "http://rebind.example:8000"})
    assert r.status_code == 403
    # A LAN device claiming the loopback Origin is still a LAN device.
    lan = TestClient(app, base_url="http://127.0.0.1:8000", client=("192.168.1.24", 50000))
    r = lan.post("/face-scan/start", json=body, headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code == 403
    assert face_scan._sessions == {}


def test_a_body_past_the_ceiling_is_refused_before_it_is_read(client, monkeypatch):
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    huge = b'{"pin": "' + b"1" * (face_scan.MAX_IMAGE_BYTES + 128 * 1024) + b'"}'
    r = client.post("/face-scan/start", content=huge,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert face_scan._failures == {} and face_scan._sessions == {}


def test_a_body_that_is_not_json_is_400_before_the_gate(monkeypatch):
    # 200,000 '[' (200 KB, under the ceiling) makes `json` raise
    # RecursionError, which is not a ValueError: it escaped as a bare 500
    # with a traceback in the log, from a LAN client with no token or PIN.
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    called = []

    async def spy(*a, **k):
        called.append(a)
        return {"ok": True}

    monkeypatch.setattr(face_scan, "start", spy)
    lan = TestClient(app, client=("192.168.1.77", 50000), raise_server_exceptions=False)
    for body in (b"[" * 200_000, b"not json", b"\xff\xfe{"):
        for route in ("start", "consent", "look", "capture", "save", "cancel"):
            r = lan.post(f"/face-scan/{route}", content=body,
                         headers={"Content-Type": "application/json"})
            assert r.status_code == 400, (route, body[:8])
            assert r.json()["ok"] is False
    assert called == []
    assert face_scan._failures == {} and face_scan._sessions == {}


def test_the_server_keeps_proxy_headers_and_bounds_the_lockout_instead():
    """proxy_headers=False was tried and reverted (2026-09-29): behind a
    reverse proxy on this machine (tailscale serve) every remote client would
    arrive as 127.0.0.1 and pass the loopback gates. With uvicorn's default a
    forged X-Forwarded-For can only make a caller look LESS trusted; the
    address rotation it allows against the per-address PIN lockout is bounded
    by the wrong-PIN table's ceiling. Read from the call, not a string."""
    import ast

    tree = ast.parse((REPO / "run_server.py").read_text(encoding="utf-8"))
    runs = [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "uvicorn.run"]
    assert len(runs) == 1
    kwargs = {kw.arg: kw.value for kw in runs[0].keywords}
    assert "proxy_headers" not in kwargs and "forwarded_allow_ips" not in kwargs
    assert 0 < face_scan.MAX_FAILURE_KEYS <= 256


# -- the owner's words --------------------------------------------------------

def test_the_policy_is_the_owners_file_named_by_the_consent_version():
    assert REAL_POLICY_DIR == REPO / "data" / "policies"
    assert face_scan.policy_path().name == f"{consent.VERSION}.txt"


def test_no_policy_file_skips_the_screen_and_the_record_says_staff_attested(client):
    # Owner, 2026-09-29: staff only, consented as staff, and the PIN holder
    # asks the person first — no consent text. The scan opens consented,
    # nothing stands in for the missing words, and the record names no
    # policy the person never saw.
    face_scan.policy_path().unlink()
    r = _start(client)
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["consented"] is True
    assert reply["consent_text"] is None
    assert reply["consent_version"] == consent.STAFF_ATTESTED != consent.VERSION
    sid = reply["session"]
    assert greeter.paused()

    base = _unit(1)
    _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 200, r.text
    record = consent.load()[NAME]
    assert record["consent_version"] == consent.STAFF_ATTESTED
    assert record["consent_by"] == NAME
    assert consent.status(NAME) == consent.OK


def test_no_policy_file_goes_straight_to_the_camera(client, fake_faces):
    face_scan.policy_path().unlink()
    sid = _start(client).json()["session"]
    r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
    assert r.status_code == 200 and r.json()["ok"] is True
    _complete(sid)
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 200, r.text
    assert [c for c in fake_faces if c[0] == "save"][0][-1] == consent.STAFF_ATTESTED


def test_a_policy_file_that_is_there_but_unreadable_refuses(client):
    # A file somebody put there to be shown is a broken screen, not the
    # owner's "no screen": refused, never quietly staff-attested.
    for raw in ("  \n\n ".encode("utf-8"), "ไม่ใช่ UTF-8".encode("cp874")):
        face_scan.policy_path().write_bytes(raw)
        r = _start(client)
        assert r.status_code == 503
        assert "อ่านไม่ได้" in r.json()["reason"]
        assert face_scan._sessions == {}
        assert not greeter.paused()


def test_with_a_policy_file_the_scan_still_waits_for_the_persons_yes(client):
    reply = _start(client).json()
    assert reply["consented"] is False
    assert reply["consent_version"] == consent.VERSION


def test_the_policy_text_is_returned_verbatim(client):
    face_scan.policy_path().write_bytes(b"\xef\xbb\xbf" + POLICY.encode("utf-8"))
    r = _start(client)
    assert r.status_code == 200
    reply = r.json()
    assert reply["consent_text"] == POLICY
    assert reply["consent_version"] == consent.VERSION
    assert [s["key"] for s in reply["steps"]] == [s["key"] for s in enrollment.STEPS]


# -- consent ------------------------------------------------------------------

def test_look_capture_and_save_wait_for_the_persons_own_yes(client, fake_faces):
    sid = _start(client).json()["session"]
    for step, body in (("look", {"image": _jpeg()}), ("capture", {"image": _jpeg()}),
                       ("save", {"name": NAME})):
        r = client.post(f"/face-scan/{step}", json={"session": sid, **body})
        assert r.status_code == 409, step
        assert "ยินยอม" in r.json()["reason"]
    assert _kinds(fake_faces) == []
    assert not enrollment.ENROLLED.exists()


def test_a_no_ends_the_scan_and_hands_the_camera_back(client, fake_faces):
    sid = _start(client).json()["session"]
    assert greeter.paused()
    r = client.post("/face-scan/consent", json={"session": sid, "agree": False})
    assert r.status_code == 200 and r.json()["closed"] is True
    assert face_scan._sessions == {}
    assert "discard" in _kinds(fake_faces)
    assert not greeter.paused()
    assert client.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).status_code == 404


def test_anything_but_json_true_is_a_no(client):
    for answer in ("true", 1, None):
        sid = _start(client).json()["session"]
        r = client.post("/face-scan/consent", json={"session": sid, "agree": answer})
        assert r.status_code == 200 and r.json()["closed"] is True, answer
        assert face_scan._sessions == {}


# -- the happy path -----------------------------------------------------------

def test_a_full_scan_files_into_the_live_gallery_and_gives_the_camera_back(client, fake_faces):
    r = _start(client)
    assert r.status_code == 200
    sid = r.json()["session"]
    assert greeter.pause_left() > 500                        # held for the whole scan

    r = client.post("/face-scan/consent", json={"session": sid, "agree": True})
    assert r.status_code == 200 and r.json()["step"] == enrollment.STEPS[0]["key"]

    r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg(), "fullWidth": 1280})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["box"] == [1, 2, 5, 6]
    r = client.post("/face-scan/capture", json={"session": sid, "image": _jpeg(), "fullWidth": 1280})
    assert r.status_code == 200 and r.json()["ok"] is True

    _complete(sid)                           # the other four poses
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": NAME, "total": 5, "gallery_total": 5}

    judge, cap, save = [c for c in fake_faces if c[0] != "discard"]
    assert judge == ("judge", (8, 8, 3), 1280, True)          # off the event loop
    assert cap == ("capture", (8, 8, 3), 1280, True)
    # into the live gallery, stamped with the policy the person was shown
    assert save == ("save", NAME, False, True, True, consent.VERSION)

    assert face_scan._sessions == {}
    # The long pause is over; what is left is the grace that lets the
    # greeter load the new gallery before it looks at this person again.
    assert greeter.paused()
    assert greeter.pause_left() <= face_scan.SAVE_GRACE_S


def test_every_step_holds_the_door_camera_off_again(client, fake_faces):
    sid = _open(client)
    greeter.resume()                         # as if the pause had run out
    client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
    assert greeter.pause_left() > 500


def test_the_real_save_writes_shots_consent_and_gallery_through_the_one_writer(client):
    sid = _open(client)
    base = _unit(1)
    _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 200, r.text
    assert r.json()["gallery_total"] == 5 and r.json()["total"] == 5

    assert len(list((enrollment.ENROLLED / NAME).glob("*.jpg"))) == 5
    assert consent.status(NAME) == consent.OK
    assert consent.load()[NAME]["consent_by"] == NAME
    assert consent.load()[NAME]["consent_version"] == consent.VERSION
    gallery = faces.Gallery.load(settings.face_gallery)
    assert gallery.names == [NAME] * 5
    assert {m["source"] for m in gallery.meta} == {"camera"}


# -- names --------------------------------------------------------------------

@pytest.mark.parametrize("name", ["..\\..\\app", "../app", "CON", "a/b", "   "])
def test_names_that_are_paths_or_devices_are_refused(client, fake_faces, name):
    sid = _open(client)
    r = client.post("/face-scan/save", json={"session": sid, "name": name})
    assert r.status_code == 400
    assert "save" not in _kinds(fake_faces)
    assert sid in face_scan._sessions


def test_an_existing_folder_name_is_refused_not_appended_to(client, fake_faces):
    (enrollment.ENROLLED / "Somchai Test").mkdir(parents=True)
    sid = _open(client)
    _complete(sid)
    r = client.post("/face-scan/save", json={"session": sid, "name": "somchai   test"})
    assert r.status_code == 409
    assert r.json()["reason"] == "ชื่อนี้มีอยู่แล้ว — ถ้าจะสแกนเพิ่มให้ใช้สถานีลงทะเบียน"
    assert "save" not in _kinds(fake_faces)
    # The scan stays: the next move is another name, not five more poses.
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 200


def test_a_name_only_in_the_gallery_is_refused(client, fake_faces):
    _write_gallery(["ทดสอบ แกลเลอรี"], [_unit(3)])
    sid = _open(client)
    _complete(sid)
    r = client.post("/face-scan/save", json={"session": sid, "name": "ทดสอบ แกลเลอรี"})
    assert r.status_code == 409
    assert r.json()["reason"].startswith("ชื่อนี้มีอยู่แล้ว")
    assert "save" not in _kinds(fake_faces)


def test_a_revoked_name_is_not_consented_again_from_this_screen(client, fake_faces):
    consent.revoke(NAME)                      # a revocation, no folder, no vectors
    sid = _open(client)
    _complete(sid)
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 409
    assert r.json()["reason"].startswith("ชื่อนี้มีอยู่แล้ว")
    assert "save" not in _kinds(fake_faces)
    assert consent.status(NAME) == consent.REVOKED


def test_an_unreadable_gallery_refuses_before_anybody_poses(client):
    path = Path(settings.face_gallery)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a gallery")
    r = _start(client)
    assert r.status_code == 503
    assert face_scan._sessions == {}


def test_a_refusal_from_the_review_is_thai_and_names_nobody(client):
    other = "ทดสอบ คนอื่น"
    face = _unit(7)
    _write_gallery([other], [face])
    sid = _open(client)
    _seed(sid, [face] * 5)
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 409
    reason = r.json()["reason"]
    assert other not in reason
    assert not re.search(r"[A-Za-z]", reason)
    assert face_scan._sessions[sid].enroll.pending_count == 5      # batch kept

    sid2 = _open(client)
    _seed(sid2, [_unit(20 + i) for i in range(5)])                 # five strangers
    r = client.post("/face-scan/save", json={"session": sid2, "name": NAME})
    assert r.status_code == 409
    assert not re.search(r"[A-Za-z]", r.json()["reason"])
    assert not enrollment.ENROLLED.exists()


# -- endings ------------------------------------------------------------------

def test_cancel_discards_and_the_camera_comes_back_with_the_last_scan(client, fake_faces):
    a = _open(client)
    b = _open(client)
    r = client.post("/face-scan/cancel", json={"session": a})
    assert r.status_code == 200 and r.json()["closed"] is True
    assert _kinds(fake_faces).count("discard") == 1
    assert greeter.paused()                  # b is still standing at the lens
    client.post("/face-scan/cancel", json={"session": b})
    assert not greeter.paused()
    assert face_scan._sessions == {}
    assert client.post("/face-scan/cancel", json={"session": a}).status_code == 404


def test_a_scan_ends_at_its_limit_however_busy_its_preview_is(client, fake_faces, scan_env):
    # The capture step posts a look every 200ms whether or not anybody is
    # at the lens. A limit counted from the last request never ran out
    # while the overlay stayed open — measured: a scan four hours old, the
    # door camera still paused, one of the two slots taken.
    sid = _open(client)
    started = scan_env.now
    for _ in range(9):                       # a look a minute, all the way
        scan_env.now += 60
        r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
        assert r.status_code == 200
        # and the hold never reaches past the scan's own deadline
        assert greeter.pause_left() <= 600 - (scan_env.now - started) + 1
    scan_env.now = started + 600
    r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
    assert r.status_code == 404
    assert r.json()["reason"] == "ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่"
    assert "discard" in _kinds(fake_faces)
    assert face_scan._sessions == {}
    assert not greeter.paused()


def test_an_abandoned_scan_is_dropped_on_the_clock_with_no_request_at_all(monkeypatch):
    # The robot's WebView killed mid-scan: the keepalive cancel dies with
    # it. Expiry used to run only inside a request, so on a quiet day the
    # person's crops stayed in this process until the next scan or a
    # restart. Real clock, a real loop, and nothing sent after the start.
    monkeypatch.setattr(face_scan, "_clock", time.monotonic)
    monkeypatch.setattr(settings, "face_scan_session_s", 0.3)

    async def scenario():
        sid = (await face_scan.start(PIN, "10.0.0.5"))["session"]
        scan = face_scan._sessions[sid]
        _seed(sid, [_unit(1)])
        held = greeter.paused()
        await asyncio.sleep(1.0)             # three limits' worth, in silence
        return scan, held

    scan, held = asyncio.run(scenario())
    assert held
    assert face_scan._sessions == {}
    assert scan.enroll.pending_count == 0    # the crop is gone, not just the id
    assert not greeter.paused()


@pytest.mark.parametrize("limit", [-5.0, 0.0, float("nan")])
def test_a_nonsense_session_limit_falls_back_instead_of_killing_every_scan(
        client, monkeypatch, limit):
    monkeypatch.setattr(settings, "face_scan_session_s", limit)
    sid = _start(client).json()["session"]
    r = client.post("/face-scan/consent", json={"session": sid, "agree": True})
    assert r.status_code == 200
    assert greeter.pause_left() > 500


def test_a_session_limit_past_the_greeters_cap_is_cut_to_it(client, fake_faces, scan_env,
                                                             monkeypatch):
    # FACE_SCAN_SESSION_S=1800 against a greeter that holds 900 at most:
    # between the two, a scan open and saveable with the door camera awake
    # — the person on the name form greeted, and remembered, as a stranger.
    monkeypatch.setattr(settings, "face_scan_session_s", 1800.0)
    sid = _open(client)
    assert face_scan._ttl() == greeter.MAX_PAUSE_S
    scan_env.now += greeter.MAX_PAUSE_S - 1
    assert client.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).status_code == 200
    assert greeter.paused()
    scan_env.now += 2
    assert client.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).status_code == 404
    assert not greeter.paused()


def test_ending_another_scan_does_not_take_back_the_grace_after_a_save(client, fake_faces):
    # The grace keeps the person just filed from being confirmed a stranger
    # before the greeter loads the new gallery. The last scan to close used
    # to `resume()` the one global deadline — whoever it belonged to.
    a, b = _open(client), _open(client)
    _complete(a)
    assert client.post("/face-scan/save", json={"session": a, "name": NAME}).status_code == 200
    assert greeter.pause_left() > 500        # b is still at the lens
    client.post("/face-scan/cancel", json={"session": b})
    assert face_scan._sessions == {}
    assert greeter.paused()
    assert 0 < greeter.pause_left() <= face_scan.SAVE_GRACE_S

    # ...and a scan opened and closed on the other screen inside the grace.
    c = _open(client)
    client.post("/face-scan/cancel", json={"session": c})
    assert greeter.paused()
    assert 0 < greeter.pause_left() <= face_scan.SAVE_GRACE_S


def test_a_save_before_every_pose_is_refused_and_the_scan_kept(client):
    # One capture, then a direct POST: `Session.save` refuses only an empty
    # batch, and one shot "agrees with itself", so the same-person check
    # never ran on what went into the gallery.
    sid = _open(client)
    _seed(sid, [_unit(1)])                   # one pose of five
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 409
    assert r.json()["reason"] == "ยังเก็บภาพไม่ครบทุกท่า — ทำให้ครบก่อนบันทึก"
    assert face_scan._sessions[sid].enroll.pending_count == 1
    assert not enrollment.ENROLLED.exists()
    assert not Path(settings.face_gallery).exists()
    assert NAME not in consent.load()


def test_a_consent_no_during_a_save_waits_for_it_too(monkeypatch):
    """The first fix covered /cancel and left /consent {agree:false} racing
    exactly as before (review, round 2): it popped the scan and answered
    "closed" while the save wrote shots, consent and gallery rows."""
    import threading

    face_scan.policy_path().unlink()
    entered, release = threading.Event(), threading.Event()
    real_review = enrollment.review

    def held_review(vecs, name):
        entered.set()
        release.wait(10)
        return real_review(vecs, name)

    monkeypatch.setattr(enrollment, "review", held_review)

    async def scenario():
        sid = (await face_scan.start(PIN, "10.0.0.5"))["session"]
        base = _unit(1)
        _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
        saving = asyncio.create_task(face_scan.save(sid, NAME, "10.0.0.5"))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.005)
            declining = asyncio.create_task(face_scan.answer_consent(sid, False, "10.0.0.5"))
            await asyncio.sleep(0.1)
            waited = not declining.done()
        finally:
            release.set()
        return waited, await saving, await declining

    waited, saved, declined = asyncio.run(scenario())
    assert waited, "the consent 'no' answered while the save was still writing"
    assert saved["ok"] is True
    assert declined == {"ok": True, "closed": True, "saved": NAME}
    assert 0 < greeter.pause_left() <= face_scan.SAVE_GRACE_S


def test_an_unreadable_consent_file_refuses_the_save_in_thai_not_a_bare_500(client, fake_faces,
                                                                           monkeypatch):
    def unreadable():
        raise consent.Unreadable("ไฟล์ความยินยอมอ่านไม่ได้ (ทดสอบ)")

    sid = _open(client)
    monkeypatch.setattr(consent, "load", unreadable)
    monkeypatch.setattr(enrollment.Session, "state", lambda self: {"step": None, "steps": []})
    r = client.post("/face-scan/save", json={"session": sid, "name": NAME})
    assert r.status_code == 503
    assert r.json()["reason"] == "ไฟล์ความยินยอมอ่านไม่ได้ (ทดสอบ)"
    assert "save" not in _kinds(fake_faces)


def test_a_cancel_during_a_save_waits_for_it_and_says_what_was_filed(monkeypatch):
    # บันทึก, then ✕ (or the robot app going to the background) while the
    # save is writing. The cancel used to pop the scan and answer "closed";
    # the save, holding its own copy of the batch, wrote five shots, a
    # consent record and five gallery rows behind it — and the cancel's
    # bare resume had taken the post-save grace. One loop, as uvicorn runs
    # it; the save is held inside its thread, after the batch was copied.
    import threading

    face_scan.policy_path().unlink()         # staff-attested: consented at start
    entered, release = threading.Event(), threading.Event()
    real_review = enrollment.review

    def held_review(vecs, name):
        entered.set()
        release.wait(10)
        return real_review(vecs, name)

    monkeypatch.setattr(enrollment, "review", held_review)

    async def scenario():
        sid = (await face_scan.start(PIN, "10.0.0.5"))["session"]
        base = _unit(1)
        _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
        saving = asyncio.create_task(face_scan.save(sid, NAME, "10.0.0.5"))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.005)
            cancelling = asyncio.create_task(face_scan.cancel(sid, "10.0.0.5"))
            await asyncio.sleep(0.1)
            waited = not cancelling.done()
        finally:
            release.set()
        return waited, await saving, await cancelling

    waited, saved, cancelled = asyncio.run(scenario())
    assert waited, "the cancel answered while the save was still writing"
    assert saved["ok"] is True and saved["name"] == NAME
    assert cancelled == {"ok": True, "closed": True, "saved": NAME}
    assert face_scan._sessions == {}
    assert len(list((enrollment.ENROLLED / NAME).glob("*.jpg"))) == 5
    # The grace is there, as after any save.
    assert greeter.paused()
    assert 0 < greeter.pause_left() <= face_scan.SAVE_GRACE_S


def test_the_clock_does_not_end_a_scan_in_the_middle_of_its_save(monkeypatch, scan_env):
    # The deadline passing while the save writes is the cancel race again,
    # with the sweep as the canceller: popped and "expired" while shots,
    # consent and gallery rows land behind it. The save ends the scan.
    import threading

    face_scan.policy_path().unlink()
    entered, release = threading.Event(), threading.Event()
    real_review = enrollment.review

    def held_review(vecs, name):
        entered.set()
        release.wait(10)
        return real_review(vecs, name)

    monkeypatch.setattr(enrollment, "review", held_review)

    async def scenario():
        sid = (await face_scan.start(PIN, "10.0.0.5"))["session"]
        base = _unit(1)
        _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
        saving = asyncio.create_task(face_scan.save(sid, NAME, "10.0.0.5"))
        try:
            while not entered.is_set():
                await asyncio.sleep(0.005)
            scan_env.now += 601                  # past the deadline, mid-write
            face_scan._sweep()                   # what the timer, or any request, runs
            still_open = sid in face_scan._sessions
        finally:
            release.set()
        return still_open, await saving

    still_open, saved = asyncio.run(scenario())
    assert still_open, "the sweep ended a scan whose save was writing"
    assert saved["ok"] is True
    assert face_scan._sessions == {}
    assert greeter.paused() and greeter.pause_left() <= face_scan.SAVE_GRACE_S


def test_a_second_save_waiting_behind_the_first_finds_the_scan_ended():
    # A double tap on บันทึก: the second save queues on the scan's lock and
    # must find the scan filed and gone — not a batch to judge again.
    face_scan.policy_path().unlink()

    async def scenario():
        sid = (await face_scan.start(PIN, "10.0.0.5"))["session"]
        base = _unit(1)
        _seed(sid, [base + 0.02 * _unit(10 + i) for i in range(5)])
        return await asyncio.gather(face_scan.save(sid, NAME, "10.0.0.5"),
                                    face_scan.save(sid, NAME, "10.0.0.5"),
                                    return_exceptions=True)

    first, second = asyncio.run(scenario())
    assert first["ok"] is True
    assert isinstance(second, face_scan.ScanError) and second.status == 404
    assert len(list((enrollment.ENROLLED / NAME).glob("*.jpg"))) == 5
    assert faces.Gallery.load(settings.face_gallery).names == [NAME] * 5


def test_a_cancel_that_crosses_a_finished_save_is_told_it_was_filed(client, fake_faces,
                                                                   scan_env):
    # The same race a moment later: the save finished before the cancel
    # arrived. "No such scan" would read as "nothing was saved".
    sid = _open(client)
    _complete(sid)
    assert client.post("/face-scan/save", json={"session": sid, "name": NAME}).status_code == 200
    r = client.post("/face-scan/cancel", json={"session": sid})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "closed": True, "saved": NAME}
    other = TestClient(app, client=("10.0.0.9", 50000))
    assert other.post("/face-scan/cancel", json={"session": sid}).status_code == 404
    scan_env.now += face_scan.FILED_MEMORY_S
    assert client.post("/face-scan/cancel", json={"session": sid}).status_code == 404


def test_a_third_scan_waits_for_one_of_the_first_two(client):
    first = _start(client).json()["session"]
    assert _start(client).status_code == 200
    r = _start(client)
    assert r.status_code == 409
    client.post("/face-scan/cancel", json={"session": first})
    assert _start(client).status_code == 200


def test_a_scan_belongs_to_the_client_that_started_it(client, fake_faces):
    sid = _open(client)
    other = TestClient(app, client=("10.0.0.9", 50000))
    assert other.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).status_code == 404
    assert other.post("/face-scan/cancel", json={"session": sid}).status_code == 404
    assert sid in face_scan._sessions
    assert client.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).status_code == 200


def test_unknown_sessions_are_404(client):
    for sid in ("nope", None, 7, ["x"]):
        r = client.post("/face-scan/consent", json={"session": sid, "agree": True})
        assert r.status_code == 404, sid


# -- images -------------------------------------------------------------------

def test_oversized_missing_and_garbage_images_are_refused(client, fake_faces):
    sid = _open(client)
    big = "data:image/jpeg;base64," + "A" * face_scan.MAX_IMAGE_BYTES
    assert client.post("/face-scan/look", json={"session": sid, "image": big}).status_code == 413
    assert client.post("/face-scan/capture", json={"session": sid, "image": big}).status_code == 413
    garbage = "data:image/jpeg;base64," + base64.b64encode(b"not an image").decode()
    assert client.post("/face-scan/look", json={"session": sid, "image": garbage}).status_code == 400
    assert client.post("/face-scan/capture", json={"session": sid}).status_code == 400
    assert "judge" not in _kinds(fake_faces) and "capture" not in _kinds(fake_faces)


@pytest.mark.parametrize("english, thai", [
    ("no face", "ยังไม่เห็นหน้า"),
    ("come closer", "เข้ามาใกล้อีกนิด"),
    ("could not read the face", "อ่านใบหน้าไม่ได้"),
    ("2 faces - only one person please", "มี 2 หน้าในภาพ — ทีละคน"),
    ("a sentence nobody has mapped yet", "เก็บภาพไม่สำเร็จ ลองอีกครั้ง"),
])
def test_a_capture_refusal_reaches_the_screen_in_thai(client, monkeypatch, english, thai):
    # `usable_face` answers the station's operator in English and
    # `Session.capture` hands that straight back: on the robot's chest it
    # was "come closer" in the one line the person at the lens reads,
    # whenever the full-size frame disagreed with the preview before it.
    monkeypatch.setattr(enrollment, "usable_face", lambda frame, min_px=None: (None, english))
    sid = _open(client)
    r = client.post("/face-scan/capture", json={"session": sid, "image": _jpeg(), "fullWidth": 1280})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.json()["reason"] == thai


def test_a_look_keeps_its_thai_and_its_empty_reason(client, monkeypatch):
    # The mapping must not touch what was right already: judge's Thai, and
    # "" — which is how a look says "this frame is the shot".
    replies = iter([{"ok": False, "reason": "ยังไม่เห็นหน้า", "say": "ยังไม่เห็นหน้า"},
                    {"ok": True, "reason": ""},
                    {"ok": False, "reason": "no face"}])
    monkeypatch.setattr(enrollment.Session, "judge",
                        lambda self, frame, full_width=None: dict(next(replies)))
    sid = _open(client)
    said = [client.post("/face-scan/look", json={"session": sid, "image": _jpeg()}).json()["reason"]
            for _ in range(3)]
    assert said == ["ยังไม่เห็นหน้า", "", "ยังไม่เห็นหน้า"]


def test_a_crash_in_the_face_work_is_a_sentence_not_a_bare_500(client, monkeypatch):
    def boom(self, frame, full_width=None):
        raise RuntimeError("onnx fell over")

    monkeypatch.setattr(enrollment.Session, "judge", boom)
    sid = _open(client)
    r = client.post("/face-scan/look", json={"session": sid, "image": _jpeg()})
    assert r.status_code == 500
    assert r.json()["ok"] is False and "onnx" not in r.json()["reason"]
