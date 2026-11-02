"""robot_arm's serial path yields the port to the robot app (design §4, P1).

"Two writers on the arm port": once the Android app has played a gesture it
holds the CH340, and an adb `printf` from the server is a second writer on the
same board. The camera greeter reaches `robot_arm.greet` through
events.announce for a non-face session, and this machine has every ROBOT_ARM_*
flag on with an adb serial set — so before this, a greeting could swing the
arm in the middle of a walk with the body switch off (safety review #4).

Nothing reaches a robot: `robot_arm._run` is a recorder, the face page is a
recorder, and the shell's answers are canned.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import robot_arm, robot_face_link as link
from app.config import settings


class FakeShell:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def run(self, args, timeout):
        self.calls.append(list(args))
        if " ls " in " ".join(args):
            return 0, "/dev/ttyUSB10"
        return 0, ""

    def wire(self) -> list[str]:
        """Every command that touched the port: protocol lines and stty."""
        return [c[-1] for c in self.calls
                if c[-1].startswith("printf ") or c[-1].startswith("stty ")]

    def lines(self) -> list[str]:
        return [c[-1].split("'")[1].replace("\\r\\n", "")
                for c in self.calls if c[-1].startswith("printf ")]


class Page:
    def __init__(self, session, answer=True):
        self.session = session
        self.answer = answer
        self.sent: list[dict] = []
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}

    async def send_text(self, text):
        frame = json.loads(text)
        self.sent.append(frame)
        if self.answer and frame.get("type") == "body_command":
            asyncio.get_running_loop().call_soon(link.receive, self.session, {
                "type": "body_ack", "command_id": frame["command_id"],
                "action": frame["action"], "accepted": True, "reason": None})


class Face:
    def __init__(self, answer=True, claim=True):
        self.ws = Page(self, answer)
        if claim:
            link.claim(self)


@pytest.fixture
def shell(monkeypatch):
    fake = FakeShell()
    monkeypatch.setattr(settings, "robot_arm_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_port", "/dev/ttyUSB10")
    monkeypatch.setattr(settings, "robot_arm_span", 200)
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    monkeypatch.setattr(settings, "robot_greeting_gesture_enabled", True)
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 3)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    monkeypatch.setattr(robot_arm, "_run", fake.run)
    robot_arm.reset_state()
    yield fake
    robot_arm.reset_state()


def _walking(state="running") -> Face:
    face = Face()
    link.receive(face, {"type": "nav_status", "state": state, "run_id": None,
                        "place": "big_room"})
    return face


CONDITIONS = ["body_switch", "routes_switch", "walk_active", "walk_stale"]


@pytest.fixture
def condition(request, monkeypatch, shell):
    which = request.param
    if which == "body_switch":
        monkeypatch.setattr(settings, "robot_body_enabled", True)
    elif which == "routes_switch":
        monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    else:
        start = link._now()
        _walking()
        if which == "walk_stale":
            # No nav frame for longer than ROBOT_NAV_STATUS_STALE_S: a walk
            # that stopped reporting is not a walk that ended.
            monkeypatch.setattr(link, "_clock",
                                lambda: start + settings.robot_nav_status_stale_s + 1)
            assert link.nav_activity() == "stale"
        else:
            assert link.nav_activity() == "active"
    return which


def _owns(coro):
    with pytest.raises(PermissionError) as caught:
        asyncio.run(coro)
    return str(caught.value)


# ==================== every serial mover refuses ====================


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_centre_refuses(condition, shell):
    assert _owns(robot_arm.centre(1)) == "app_owns_arm_port"
    assert shell.wire() == []


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_step_refuses(condition, shell):
    robot_arm._commanded[1] = robot_arm.PULSE_CENTRE
    assert _owns(robot_arm.step(1, 1)) == "app_owns_arm_port"
    assert shell.wire() == []
    assert robot_arm._commanded[1] == robot_arm.PULSE_CENTRE


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_run_group_refuses(condition, shell):
    assert _owns(robot_arm.run_group(3)) == "app_owns_arm_port"
    assert shell.wire() == []


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_prepare_refuses(condition, shell):
    """`stty raw` at our baud on a port the app holds changes its line
    settings under the app's SDK — and it is the serial greeting's first
    write."""
    assert _owns(robot_arm.prepare()) == "app_owns_arm_port"
    assert shell.wire() == []


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_the_camera_greeter_does_not_write_serial(condition, shell, monkeypatch):
    """events.announce(arm_greeting=True) for a NON-face session goes down
    greet()'s serial branch (prepare + run_group). It must write nothing,
    and the greeting itself must still be spoken."""
    from app import display, events, session as session_module

    class Provider:
        def __init__(self):
            self.said = []

        async def send_text(self, text):
            self.said.append(text)

    class DeskSession:
        provider = Provider()

    desk = DeskSession()
    monkeypatch.setattr(session_module, "_active", desk)

    async def body():
        display.set_audio_lead(0)
        return await events.announce("สวัสดีค่ะ", source="face_known",
                                     arm_greeting=True, max_wait=5, then_pause=0)

    assert asyncio.run(body()) is True
    assert desk.provider.said == ["สวัสดีค่ะ"]
    assert shell.wire() == [], "the app owns the port: no stty, no printf"


def test_without_any_condition_the_desk_controls_still_write(shell):
    """The control: the lock is the condition, not the fixture."""
    assert asyncio.run(robot_arm.centre(1)) == robot_arm.PULSE_CENTRE
    assert shell.lines() == ["#1P1500T9999"]


@pytest.mark.parametrize("state", ["arrived", "stopped", "idle"])
def test_a_finished_walk_does_not_hold_the_port(shell, state):
    _walking(state)
    assert link.nav_activity() == "idle"
    asyncio.run(robot_arm.run_group(3))
    assert shell.lines() == ["#3GC1"]


def test_the_reason_is_said_for_the_desk_page(shell, monkeypatch):
    assert robot_arm.app_owns_port() is None
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert robot_arm.app_owns_port() == "robot_nav_routes_enabled"
    monkeypatch.setattr(settings, "robot_body_enabled", True)
    assert robot_arm.app_owns_port() == "robot_body_enabled"


# ==================== the brake is never locked ====================


@pytest.mark.parametrize("condition", CONDITIONS, indirect=True)
def test_stop_still_writes_the_stop_frame(condition, shell):
    out = asyncio.run(robot_arm.stop())
    assert out["stop_write_ok"] is True
    assert shell.lines() == ["#STOP", "#STOP"]


def test_stop_also_sends_body_stop_to_the_face(shell):
    face = Face(answer=True)
    out = asyncio.run(robot_arm.stop())
    assert [f["action"] for f in face.ws.sent if f["type"] == "body_command"] == ["stop"]
    assert out["body_stop"] == {"accepted": True, "reason": None}
    assert shell.lines() == ["#STOP", "#STOP"], "both stops, not one instead of the other"


def test_body_stop_is_sent_even_with_no_serial_port(shell, monkeypatch):
    """A machine whose app owns the arm may have no ROBOT_ARM_PORT at all:
    the state of a switch must never be why a stop did not go out."""
    monkeypatch.setattr(settings, "robot_arm_port", "")
    face = Face(answer=True)
    out = asyncio.run(robot_arm.stop())
    assert out["stop_write_ok"] is False
    assert [f["action"] for f in face.ws.sent] == ["stop"]
    assert out["body_stop"]["accepted"] is True


def test_stop_reports_a_face_that_did_not_answer(shell):
    face = Face(answer=False)
    out = asyncio.run(robot_arm.stop())
    assert [f["action"] for f in face.ws.sent] == ["stop"]
    assert out["body_stop"] == {"accepted": False, "reason": "timeout"}


def test_stop_without_a_face_sends_no_body_command(shell):
    out = asyncio.run(robot_arm.stop())
    assert out["body_stop"] is None
    assert shell.lines() == ["#STOP", "#STOP"]


def test_stop_skips_a_page_that_only_claims_the_face_when_a_token_is_set(shell, monkeypatch):
    monkeypatch.setattr(settings, "robot_face_token", "s3cret")
    face = Face(answer=True)          # EmmaRobot/ user agent, no face_hello
    out = asyncio.run(robot_arm.stop())
    assert face.ws.sent == [] and out["body_stop"] is None


# ==================== greet's native path ====================


def _native_greet(face):
    return asyncio.run(robot_arm.greet(session=face))


def test_native_greeting_is_unchanged_without_a_token(shell, monkeypatch):
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    face = Face(claim=False)        # existing callers: a UA, nothing else
    assert _native_greet(face) is True
    assert face.ws.sent == [{"type": "arm_greeting", "group": 6}]
    assert shell.wire() == []


def test_native_greeting_needs_the_hello_when_a_token_is_set(shell, monkeypatch):
    """With ROBOT_FACE_TOKEN set, a desk browser holding WS_TOKEN must not
    reach the arm by claiming the EmmaRobot/ user agent (design §2a)."""
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    monkeypatch.setattr(settings, "robot_face_token", "s3cret")
    face = Face()
    assert _native_greet(face) is False
    assert face.ws.sent == []
    link.receive(face, {"type": "face_hello", "token": "s3cret"})
    assert _native_greet(face) is True
    assert face.ws.sent == [{"type": "arm_greeting", "group": 6}]


def test_native_greeting_is_not_fenced_by_the_body_switch(shell, monkeypatch):
    """The app path is the production path; the switches lock only the
    second writer (serial). Its own gestures are refused on the robot
    (`navigating`) during a walk."""
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    monkeypatch.setattr(settings, "robot_body_enabled", True)
    face = Face(claim=False)
    assert _native_greet(face) is True
    assert face.ws.sent == [{"type": "arm_greeting", "group": 6}]


# ==================== the event host's own walk (31 Oct, round 2) ====================
# board_walk runs on the server; the app's nav poll (run.pid) never sees it,
# so the app's "navigating" gate stays open. The server holds the arm.


def test_no_hand_out_while_the_event_host_walks(shell, monkeypatch):
    from app import event_walk

    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    face = Face(claim=False)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    assert _native_greet(face) is False
    assert face.ws.sent == []
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    assert _native_greet(face) is True


def test_the_event_hosts_walk_refuses_every_arm_command_but_stop(shell, monkeypatch):
    from app import event_walk

    face = Face(answer=True)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    for payload in ({"action": "raise_right_arm", "style": "speech"}, {"action": "nod"}):
        out = asyncio.run(link.request(face, "body", payload, 0.5))
        assert out["accepted"] is False and out["reason"] == "navigating", payload
    assert face.ws.sent == []
    out = asyncio.run(link.request(face, "body", {"action": "stop"}, 0.5))
    assert out["accepted"] is True
    assert [f["action"] for f in face.ws.sent] == ["stop"]
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    out = asyncio.run(link.request(face, "body", {"action": "nod"}, 0.5))
    assert out["accepted"] is True
