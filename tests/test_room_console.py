"""The operator's control panel: the proxy endpoints and the boundary.

The console drives sales-room PCs, so the two things worth pinning are that it
speaks through room_control (inheriting its explicit-list / per-room-token /
ask-don't-guess rules) and that, like the agent and the caller, it stays OUT of
app/ -- Emma must not be able to reach it.
"""

import base64
import importlib.util
import io as _io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("room_console_server", ROOT / "room_console_server.py")
rcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcs)


@pytest.fixture
def api(monkeypatch):
    def stub(**fns):
        for name, fn in fns.items():
            monkeypatch.setattr(rcs.room_control, name, fn)
        return TestClient(rcs.app)
    return stub


def _tiny_jpeg_b64() -> str:
    from PIL import Image  # Pillow is a room-agent dep; present in the dev env
    buf = _io.BytesIO()
    Image.new("RGB", (4, 3), (10, 20, 30)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_rooms_are_listed_from_room_control(api):
    client = api(rooms=lambda: {"ห้องขาย 2": {}, "ห้องขาย 1": {}})
    body = client.get("/rooms").json()
    assert body["rooms"] == ["ห้องขาย 1", "ห้องขาย 2"]   # sorted


def test_an_action_is_proxied_through_room_control(api):
    seen = {}
    def fake_do(room, action, args=None):
        seen.update(room=room, action=action, args=args)
        return {"ok": True, "windows": ["A", "B"]}
    client = api(do=fake_do)
    r = client.post("/do", json={"room": "ห้องขาย 1", "action": "list_windows"})
    assert r.json()["ok"] is True
    assert seen == {"room": "ห้องขาย 1", "action": "list_windows", "args": {}}


def test_a_do_reply_never_carries_the_raw_screen_image(api):
    """The base64 frame belongs on /screen, not inside a JSON /do reply the
    browser would decode again; /do strips it so a stray 'screen' via /do stays light."""
    client = api(do=lambda *a, **k: {"ok": True, "jpeg_base64": "AAAA", "size": [8, 6]})
    body = client.post("/do", json={"room": "R", "action": "screen"}).json()
    assert body["jpeg_base64"] == "(use /screen)"


def test_screen_returns_an_image_the_browser_can_show(api):
    client = api(do=lambda *a, **k: {"ok": True, "jpeg_base64": _tiny_jpeg_b64(), "size": [1920, 1080]})
    r = client.get("/screen?room=R")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"                 # JPEG magic
    assert r.headers["X-Screen-Size"] == "1920,1080"    # real size, for click mapping


def test_screen_when_the_capability_is_off_is_an_error_not_a_blank_image(api):
    client = api(do=lambda *a, **k: {"ok": False, "error": "not enabled here"})
    r = client.get("/screen?room=R")
    assert r.status_code == 502
    assert r.json()["error"] == "not enabled here"


def test_an_unknown_room_is_a_400_not_a_guess(api):
    def boom(room, *a, **k):
        raise rcs.room_control.RoomError("no room called 'X'")
    client = api(health=boom)
    r = client.get("/health?room=X")
    assert r.status_code == 400
    assert "no room" in r.json()["error"]


def test_the_console_is_not_reachable_from_emma():
    for path in sorted((ROOT / "app").rglob("*.py")):
        assert "room_console" not in path.read_text(encoding="utf-8"), path
