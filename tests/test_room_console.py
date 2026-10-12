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
        # base_url sets the Host header to an allowed one; the console now runs a
        # Host allow-list (anti DNS-rebinding), so a plain TestClient (Host
        # "testserver") would otherwise be refused.
        return TestClient(rcs.app, base_url="http://127.0.0.1")
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


def test_a_room_that_blows_up_returns_json_not_a_bare_500(api):
    """If the room call raises anything (an upstream read timeout is raised bare,
    not as RoomError), /do must still answer JSON the browser can parse -- the
    console showed 'Unexpected token I, Internal S...' on 2026-09-25 because a
    500 plain-text leaked through."""
    def boom(*a, **k):
        raise TimeoutError("read timed out")
    client = api(do=boom)
    r = client.post("/do", json={"room": "R", "action": "monitor", "args": {"state": "off"}})
    assert r.status_code == 502
    body = r.json()
    assert body["ok"] is False and "did not respond" in body["error"]


def test_the_console_never_drives_a_room_on_the_event_loop():
    """room_control talks to a room over the network / adb -- a blocking call. If
    the async endpoints call it directly on the event loop, one slow room (or a
    hung adb subprocess) freezes the WHOLE console: even /rooms times out, and a
    click appears to do nothing. Every room_control call must be offloaded to a
    thread. Found 2026-09-26 when clicking a live Android screen hung the panel."""
    src = (ROOT / "room_console_server.py").read_text(encoding="utf-8")
    assert "run_in_threadpool" in src
    # no direct blocking call left on the loop
    for call in ("room_control.do(", "room_control.health(", "room_control.rooms("):
        assert call not in src, call
    assert "run_in_threadpool(room_control.do" in src
    assert "run_in_threadpool(room_control.health" in src
    assert "run_in_threadpool(room_control.rooms" in src


def test_discover_lists_android_panels_for_setup(api):
    client = api(discover_android=lambda: [{"serial": "S", "model": "M6APro"}])
    d = client.get("/discover").json()
    assert d["devices"][0]["serial"] == "S"


def test_scrcpy_launch_is_proxied_through_room_control(api):
    seen = {}

    def fake(room, audio="off"):
        seen.update(room=room, audio=audio)
        return {"ok": True, "launched": "x"}

    client = api(launch_scrcpy=fake)
    r = client.post("/scrcpy", json={"room": "จอ A"})
    assert r.json()["ok"] is True and seen == {"room": "จอ A", "audio": "off"}
    client.post("/scrcpy", json={"room": "จอ A", "audio": "mic"})   # listen to the room
    assert seen == {"room": "จอ A", "audio": "mic"}


def test_wake_on_lan_is_proxied_through_room_control(api):
    seen = {}
    client = api(wake=lambda room: seen.update(room=room) or {"ok": True, "woke": "AA:BB"})
    r = client.post("/wake", json={"room": "ห้องขาย 1"})
    assert r.json()["ok"] is True and seen["room"] == "ห้องขาย 1"


def test_the_console_page_supports_drag_and_continuous_live():
    """The operator can drag (not just tap) and run a continuous live view. Small
    move -> tap, larger move -> mouse_drag (swipe); a pointer dot shows where it
    will land; 'เร็วสุด' grabs back-to-back instead of on a fixed timer."""
    html = (ROOT / "client" / "room-console.html").read_text(encoding="utf-8")
    assert "'mouse_drag'" in html and "'mouse_click'" in html
    assert "เร็วสุด" in html and 'value="0"' in html and "liveLoop" in html
    assert "cursor" in html                     # the pointer marker
    assert "scrcpyBtn" in html and "'/scrcpy'" in html   # per-room real-time launch (Android)
    assert "scrcpyAudioBtn" in html and "scrcpyMicBtn" in html    # hear panel audio / room mic
    assert "'playback'" in html and "'mic'" in html              # the two audio modes
    assert "discoverBtn" in html and "'/discover'" in html   # one-click serial lookup for setup
    assert 'data-act="reboot"' in html and 'data-act="shutdown"' in html
    assert "data-confirm" in html            # power actions ask before firing
    assert "updateBtn" in html and "'update'" in html   # one-click agent self-update (Windows)
    assert "wolBtn" in html and "'/wake'" in html       # Wake-on-LAN for a powered-off Windows PC


def test_a_post_without_json_content_type_is_refused_csrf(api):
    """A page open on any OTHER site in the operator's browser can POST here (the
    reply is opaque to it, but the action would run). A CORS 'simple' request can
    carry a text/plain or no-type body but NOT application/json without a
    preflight it will fail. Typing the body makes FastAPI require application/json,
    so a shutdown smuggled as text/plain is refused, not executed. Found 2026-09-26."""
    called = {"n": 0}

    def fake_do(room, action, args=None):
        called["n"] += 1
        return {"ok": True}

    client = api(do=fake_do)
    body = '{"room":"ห้องขาย 1","action":"shutdown","args":{}}'
    r1 = client.post("/do", content=body, headers={"content-type": "text/plain"})
    r2 = client.post("/do", content=body)                       # no content type
    r3 = client.post("/do", data={"room": "R", "action": "shutdown"})   # form
    assert r1.status_code in (415, 422) and r2.status_code in (415, 422)
    assert r3.status_code in (415, 422)
    assert called["n"] == 0                                     # nothing ran


def test_a_forged_host_is_refused_dns_rebinding():
    """A DNS-rebinding page rebinds its own hostname to 127.0.0.1 to become
    same-origin and drive the rooms. A Host allow-list refuses the forged Host
    before the route runs; the real loopback host still works."""
    evil = TestClient(rcs.app, base_url="http://attacker.example")
    assert evil.get("/rooms").status_code == 400
    good = TestClient(rcs.app, base_url="http://127.0.0.1")
    assert good.get("/rooms").status_code != 400


def test_responses_carry_anti_framing_headers(api):
    client = api(rooms=lambda: {"R": {}})
    r = client.get("/rooms")
    assert r.headers.get("x-frame-options") == "DENY"
    assert "frame-ancestors" in r.headers.get("content-security-policy", "")


def test_the_self_update_action_carries_a_locally_computed_bundle_hash(api, monkeypatch):
    """update is the one action whose payload is code that will run on the room
    PC. The console attaches the sha256 it read from its OWN bundle server (over
    loopback), so the agent can refuse a bundle a rogue LAN host substituted.
    It is attached server-side, never trusted from the browser."""
    seen = {}

    def fake_do(room, action, args=None):
        seen.update(room=room, action=action, args=dict(args or {}))
        return {"ok": True}

    monkeypatch.setattr(rcs, "_bundle_sha", lambda: "deadbeef")
    client = api(do=fake_do)
    client.post("/do", json={"room": "R", "action": "update", "args": {}})
    assert seen["args"].get("sha256") == "deadbeef"
    client.post("/do", json={"room": "R", "action": "list_windows"})
    assert "sha256" not in seen["args"]                        # only update gets one


def test_the_console_is_not_reachable_from_emma():
    for path in sorted((ROOT / "app").rglob("*.py")):
        assert "room_console" not in path.read_text(encoding="utf-8"), path
