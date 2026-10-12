"""P7 of docs/emma-body-nav-design-2026-10-08.md: the seams between packages.

Each package tested its own side. These tests hold the places where one
package's output is another's input and neither side's suite would notice a
mismatch:

- the face page asks the new bridge only when the APK says it speaks it
  (`EmmaBridge/1` in the user agent). P5 gated on the marker, P4 had not added
  it: every package was green and the feature could never have switched on;
- the /arm desk page is told *why* the serial path refuses, instead of being
  sent to re-arm a page that is armed;
- `body` is a plain tool module, and still opt-in.

Nothing reaches a robot: `robot_arm._run` is a recorder and the face is a fake
page on robot_face_link, the shape of tests/test_robot_arm_lock.py.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import robot_arm, robot_face_link as link
from app.config import settings
from app.main import app

ROOT = Path(__file__).resolve().parent.parent
EMMA_FACE = (ROOT / "apps" / "emma-robot-android" / "app" / "src" / "main" / "java"
             / "com" / "emma" / "robot" / "control" / "EmmaFace.java")
FACE_PAGE = ROOT / "client" / "voice-preview.html"
ARM_PAGE = ROOT / "client" / "robot-arm.html"
TOKEN = "let-me-in"


# ==================== the bridge marker: Java <-> page ====================


def _java_user_agent_suffix() -> str:
    """The literal the app appends to the WebView's own user agent."""
    src = EMMA_FACE.read_text(encoding="utf-8")
    found = re.findall(r'setUserAgentString\(settings\.getUserAgentString\(\)\s*\+\s*"([^"]*)"\)', src)
    assert len(found) == 1, "exactly one place sets the user agent"
    return found[0]


def _page_on_robot_regexes() -> list[str]:
    code = FACE_PAGE.read_text(encoding="utf-8")
    line = re.search(r"var ON_ROBOT = IN_APP && (.*);", code)
    assert line, "the page's ON_ROBOT gate moved; this test must follow it"
    return re.findall(r"/((?:[^/\\]|\\.)+)/\.test\(UA\)", line.group(1))


def test_the_apk_user_agent_passes_the_pages_bridge_gate():
    """The page asks emma-body/emma-nav/emma-face prompts only on ON_ROBOT.
    Every regex in that gate must match what the app really sends, or the
    nav slot and every body/nav command stay off for good with all tests
    green (found in integration: the marker was missing on the Java side)."""
    ua = "Mozilla/5.0 (Linux; Android 15; ZC-3588A) AppleWebKit/537.36 wv" + _java_user_agent_suffix()
    patterns = _page_on_robot_regexes()
    assert len(patterns) >= 2, patterns
    for pattern in patterns:
        js_like = pattern.replace("\\/", "/")
        assert re.search(js_like, ua), (pattern, ua)


def test_the_server_still_recognises_the_face_by_its_old_claim():
    """session.py and robot_arm read `EmmaRobot/`; adding the marker must
    not have replaced that token."""
    assert "EmmaRobot/" in _java_user_agent_suffix()


def test_an_old_apk_without_the_marker_is_not_on_robot():
    """The other direction: an APK from before the bridge must stay off,
    because it would answer the new prompts with the WebView's own dialog."""
    old_ua = "Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1"
    assert not all(re.search(p.replace("\\/", "/"), old_ua) for p in _page_on_robot_regexes())


# ==================== the /arm desk page and the lock ====================


class FakeShell:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def run(self, args, timeout):
        self.calls.append(list(args))
        if " ls " in " ".join(args):
            return 0, "/dev/ttyUSB10"
        return 0, ""

    def wire(self) -> list[str]:
        return [c[-1] for c in self.calls
                if c[-1].startswith("printf ") or c[-1].startswith("stty ")]


@pytest.fixture
def shell(monkeypatch):
    fake = FakeShell()
    monkeypatch.setattr(settings, "robot_arm_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_port", "/dev/ttyUSB10")
    monkeypatch.setattr(settings, "robot_arm_span", 200)
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(robot_arm, "_run", fake.run)
    robot_arm.reset_state()
    yield fake
    robot_arm.reset_state()


@pytest.fixture
def client():
    return TestClient(app)


def send(client, action, **extra):
    return client.post("/arm/command", json={"token": TOKEN, "action": action, **extra}).json()


@pytest.mark.parametrize("action,extra", [("centre", {"channel": 1}),
                                          ("group", {"group": 3}),
                                          ("prepare", {})])
def test_a_serial_refusal_says_the_app_owns_the_port(shell, client, monkeypatch, action, extra):
    monkeypatch.setattr(settings, "robot_body_enabled", True)
    out = send(client, action, **extra)
    assert out["ok"] is False
    assert out["error"] == "app_owns_arm_port", out
    assert "แอปบนหุ่น" in out["hint"]
    assert "กดปุ่มพร้อมสั่งงาน" not in out["hint"], "the page is armed; re-arming fixes nothing"
    assert shell.wire() == []


def test_disarmed_is_still_disarmed(shell, client):
    """The new branch must not swallow the old reason."""
    send(client, "stop")
    out = send(client, "centre", channel=1)
    assert out["error"] == "disarmed"


def test_arm_state_says_who_owns_the_port(shell, client, monkeypatch):
    out = client.get("/arm/state", params={"token": TOKEN}).json()
    assert out["ok"] is True and out["state"]["app_owns_port"] is None
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    out = client.get("/arm/state", params={"token": TOKEN}).json()
    assert out["state"]["app_owns_port"] == "robot_nav_routes_enabled"


class _Page:
    def __init__(self, session):
        self.session = session
        self.sent: list[dict] = []
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/0.1 EmmaBridge/1"}

    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if frame.get("type") == "body_command":
            asyncio.get_running_loop().call_soon(link.receive, self.session, {
                "type": "body_ack", "command_id": frame["command_id"],
                "action": frame["action"], "accepted": True, "reason": None})


class _Face:
    def __init__(self):
        self.ws = _Page(self)
        link.claim(self)


def test_the_arm_stop_reply_carries_the_apps_own_stop(shell, client):
    face = _Face()
    out = send(client, "stop")
    assert out["ok"] is True
    assert out["body_stop"] == {"accepted": True, "reason": None}
    assert [f["action"] for f in face.ws.sent if f["type"] == "body_command"] == ["stop"]
    assert "หน้าหุ่นรับคำสั่งหยุดของแอปแล้ว" in out["note"]
    assert "ไม่ยืนยันว่าเฟรมถึงบอร์ดหรือแขนหยุด" in out["note"], "accepted is not stopped"


def test_the_arm_stop_reply_without_a_face_is_unchanged(shell, client):
    out = send(client, "stop")
    assert out["body_stop"] is None
    assert "หน้าหุ่น" not in out["note"]


def test_the_arm_page_shows_the_owner_and_disables_the_movers():
    src = ARM_PAGE.read_text(encoding="utf-8")
    # The banner is its own branch of the alert, ahead of "disarmed": the
    # word alone also appears in the `blocked` line below.
    branch = re.search(r"\} else if \(s\.app_owns_port\) \{(.*?)\} else if", src, re.S)
    assert branch, "no alert branch for app_owns_port"
    assert "แอปบนหุ่นเป็นเจ้าของพอร์ตแขนอยู่" in branch.group(1)
    blocked = re.search(r"const blocked = ([^;]*);", src).group(1)
    assert "app_owns_port" in blocked
    prepare = re.search(r"\$\('prepare'\)\.disabled = ([^;]*);", src).group(1)
    assert "app_owns_port" in prepare
    # The red button never depends on it: a lock must not disable the brake.
    assert "app_owns_port" not in re.search(r"\$\('stop'\)\.onclick = ([^;]*);", src, re.S).group(1)


# ==================== registration and the provider event ====================


def test_body_is_a_plain_tool_module_and_still_opt_in():
    from app import tools

    assert tools._TOOL_MODULES.get("app.tools.body") == "body"
    assert "body" in tools._OPT_IN


def test_call_id_is_a_field_that_does_not_change_equality():
    from app.providers.base import ProviderEvent

    a = ProviderEvent(kind="tool_call", text="move_body", call_id="x1")
    b = ProviderEvent(kind="tool_call", text="move_body")
    assert a.call_id == "x1" and b.call_id is None
    assert a == b, "existing tests compare events by what the browser sees"
