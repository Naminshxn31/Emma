"""The wheel fence in the chassis primitives (docs/emma-body-nav-design-2026-10-08.md §4, P2).

Two drivers on the wheels is the hazard: the board's planner (MoveTo/GoHome,
reached from /robot/command, /mapview and the planner `go_to_place`) and the
leg runner the robot face starts (drive_route, velocity). The fence is in the
five primitives themselves — `move_to`, `go_home`, `drive`, `nudge`,
`undock` — because /robot/command calls most of them without passing through
`send()` (safety review #3).

Each refusal test asserts the board heard **nothing at all**, not just "no
POST": that is what makes reverting one primitive's own guard go red even
when a deeper primitive would have refused later.
"""
from __future__ import annotations

import asyncio

import pytest

from app import robot_chassis, robot_face_link
from app.config import settings

RUN = "c" * 32


def run(coro):
    return asyncio.run(coro)


class Board:
    def __init__(self):
        self.calls: list[tuple] = []
        self.localization: object = True
        self.power = {"batteryPercentage": 80, "isCharging": True, "dockingStatus": "on_dock"}

    async def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == robot_chassis.LOCALIZATION_ENABLE:
            if isinstance(self.localization, Exception):
                raise self.localization
            return self.localization
        if path == robot_chassis.LOCALIZATION_QUALITY:
            return 80
        if path == "/api/core/system/v1/power/status":
            return dict(self.power)
        if path == "/api/core/slam/v1/localization/pose":
            return {"x": 0.0, "y": 0.0, "yaw": 0.0}
        if path == robot_chassis.LASERSCAN:
            return {"pose": {"x": 0, "y": 0, "yaw": 0}, "laser_points": []}
        if path == "/api/core/motion/v1/actions" and method == "POST":
            return {"action_id": 9}
        if path == "/api/core/motion/v1/actions/:current":
            return None
        return None

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class Face:
    """The robot face page. Records what the server sends and, like the real
    page relaying the app, acks every command it is sent (`answers=False`
    for a page that has gone quiet)."""

    class _WS:
        def __init__(self, face, answers):
            self.face = face
            self.answers = answers
            self.sent: list[dict] = []

        async def send_text(self, text):
            import json

            frame = json.loads(text)
            self.sent.append(frame)
            if self.answers:
                kind = frame["type"].split("_")[0]
                asyncio.get_running_loop().call_soon(
                    robot_face_link.receive, self.face,
                    {"type": "%s_ack" % kind, "command_id": frame["command_id"],
                     "action": frame["action"], "accepted": True})

    def __init__(self, answers=True):
        self.ws = self._WS(self, answers)


@pytest.fixture
def board(monkeypatch):
    fake = Board()
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_chassis_url", "http://chassis.test")
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", True)
    monkeypatch.setattr(settings, "robot_drive_min_clearance_m", 0.0)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    monkeypatch.setattr(settings, "robot_nav_panel", "off")
    monkeypatch.setattr(robot_chassis, "request", fake.request)
    robot_chassis.reset_state()
    yield fake
    robot_chassis.reset_state()


def running(monkeypatch, activity, answers=True):
    """The robot face reports a leg run: `active`, or `stale` (no frame for
    longer than ROBOT_NAV_STATUS_STALE_S)."""
    clock = [100.0]
    monkeypatch.setattr(robot_face_link, "_clock", lambda: clock[0])
    face = Face(answers)
    robot_face_link.claim(face)
    robot_face_link.receive(face, {"type": "nav_status", "state": "running", "run_id": RUN,
                                   "origin": "tap", "place": "big_room"})
    if activity == "stale":
        clock[0] += settings.robot_nav_status_stale_s + 1
    assert robot_face_link.nav_activity() == activity
    return face


PRIMITIVES = {
    "move_to": lambda: robot_chassis.move_to(1.0, 2.0),
    "go_home": lambda: robot_chassis.go_home(),
    "drive": lambda: robot_chassis.drive("forward"),
    "nudge": lambda: robot_chassis.nudge(forward_m=0.3),
    "undock": lambda: robot_chassis.undock(),
}


@pytest.mark.parametrize("activity", ["active", "stale"])
@pytest.mark.parametrize("name", list(PRIMITIVES))
def test_every_primitive_refuses_while_a_run_is_live_whatever_the_switches(
        board, monkeypatch, name, activity):
    assert settings.robot_nav_routes_enabled is False and settings.robot_body_enabled is False
    running(monkeypatch, activity)
    with pytest.raises(robot_chassis.Fenced) as exc:
        run(PRIMITIVES[name]())
    assert exc.value.code == "run_active"
    assert board.calls == [], "%s touched the board while a run owned it" % name


def test_a_terminal_run_releases_the_fence(board, monkeypatch):
    face = running(monkeypatch, "active")
    robot_face_link.receive(face, {"type": "nav_status", "state": "arrived", "run_id": RUN,
                                   "origin": "tap", "place": "big_room"})
    assert run(robot_chassis.move_to(1.0, 2.0)) == 9


def test_no_face_ever_seen_is_not_fenced(board):
    """The sales room / desk machine: no robot face, nothing to fence."""
    assert robot_face_link.nav_activity() == "unknown"
    assert run(robot_chassis.move_to(1.0, 2.0)) == 9


@pytest.mark.parametrize("name", ["move_to", "go_home"])
def test_the_planner_refuses_while_routes_own_the_base(board, monkeypatch, name):
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    with pytest.raises(robot_chassis.Fenced) as exc:
        run(PRIMITIVES[name]())
    assert exc.value.code == "routes_own_the_base"
    assert board.posts() == []


@pytest.mark.parametrize("reading", [False, None, "on", RuntimeError("relay down")])
@pytest.mark.parametrize("name", ["move_to", "go_home"])
def test_the_planner_refuses_unless_localization_reads_true(board, name, reading):
    """drive_route leaves localization off on purpose; MoveTo/GoHome on that
    board walks to where the target would be if odometry were the map.
    Unreadable is not "on" (safety review #10)."""
    board.localization = reading
    with pytest.raises(robot_chassis.Fenced) as exc:
        run(PRIMITIVES[name]())
    assert exc.value.code == "relocalize"
    assert board.posts() == []


def test_drive_is_not_a_planner_move(board, monkeypatch):
    """MoveBy is remote control: routes mode and localization do not fence it,
    only a live run does (or, with routes on, one nobody can rule out — the
    face here says idle, see the run_unknown tests below)."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    face = Face()
    robot_face_link.claim(face)
    robot_face_link.receive(face, {"type": "nav_status", "state": "idle", "run_id": None})
    board.localization = False
    assert run(robot_chassis.drive("forward")) is True
    run(robot_chassis.drive_stop())


# ---- review 2, finding #3: "unknown" on a routes machine is not "free" ----


UNKNOWN_FENCED = {
    "drive": lambda: robot_chassis.drive("forward"),
    "relocalize": lambda: robot_chassis.relocalize("dock"),
}


@pytest.mark.parametrize("name", list(UNKNOWN_FENCED))
def test_with_routes_on_an_unseen_walk_fences_the_wheels(board, monkeypatch, name):
    """No face page reporting (before the status link: any walk between
    calls; now: app closed, link silent, poll stale) reads "unknown". With
    routes on the app can start a walk, so
    the joystick's MoveBy and a relocalize must not become a second driver
    on the wheels mid-leg. Revert-red against run_fence's unknown branch."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert robot_face_link.nav_activity() == "unknown"
    with pytest.raises(robot_chassis.Fenced) as exc:
        run(UNKNOWN_FENCED[name]())
    assert exc.value.code == "run_unknown"
    assert "หน้าหุ่น" in exc.value.thai and "ปุ่มหยุดฉุกเฉิน" in exc.value.thai
    assert board.calls == []


def test_with_routes_on_the_planner_still_says_routes_own_the_base(board, monkeypatch):
    """run_unknown must not replace the permanent reason: starting a call
    would not unlock a planner move on a routes machine anyway."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    for name in ("move_to", "go_home", "nudge", "undock"):
        with pytest.raises(robot_chassis.Fenced) as exc:
            run(PRIMITIVES[name]())
        assert exc.value.code == "routes_own_the_base", name
    assert board.calls == []


def test_with_routes_off_unknown_stays_open_as_before(board):
    """The sales-room / desk machine: no routes, no face — nothing changes."""
    assert settings.robot_nav_routes_enabled is False
    assert run(robot_chassis.drive("forward")) is True
    run(robot_chassis.drive_stop())


def test_the_brake_is_never_fenced_by_an_unseen_walk(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    run(robot_chassis.drive_stop(reason="stop button"))
    assert run(robot_chassis.send("cancel_navigation")) == "ok"
    assert len([c for c in board.calls if c[0] == "DELETE"]) == 2


def test_the_arm_port_is_owned_on_a_routes_machine_whatever_the_face_reports(monkeypatch):
    """robot_arm.app_owns_port: routes on already owns the port, so an
    unseen walk (activity unknown) cannot swing the arm through the serial
    greeting either."""
    from app import robot_arm

    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_arm.app_owns_port() is not None


def test_the_brake_is_never_fenced(board, monkeypatch):
    running(monkeypatch, "active")
    run(robot_chassis.drive_stop(reason="stop button"))
    assert run(robot_chassis.send("cancel_navigation")) == "ok"
    deletes = [c for c in board.calls if c[0] == "DELETE"]
    assert len(deletes) == 2


def test_relocalizing_mid_run_is_refused(board, monkeypatch):
    """Not one of the five, fenced anyway: relocalize switches localization
    on and `dock` rewrites the pose — the frame a running leg steers by."""
    running(monkeypatch, "active")
    with pytest.raises(robot_chassis.Fenced):
        run(robot_chassis.relocalize("dock"))
    assert board.calls == []


def test_a_fenced_send_does_not_mark_the_chassis_disconnected(board, monkeypatch):
    robot_chassis._POSES["ห้องตัวอย่าง"] = {"x": 1.0, "y": 1.0, "yaw": 0.0}
    robot_chassis.STATE["connected"] = True
    board.localization = False
    assert run(robot_chassis.send("move_to_point", place="ห้องตัวอย่าง")) == "failed"
    assert robot_chassis.STATE["connected"] is True
    assert board.posts() == []


def test_live_state_reports_localization_for_the_map_page(board):
    board.localization = False
    assert run(robot_chassis.live_state())["localization_enabled"] is False
    board.localization = RuntimeError("down")
    assert run(robot_chassis.live_state())["localization_enabled"] is None


# ==================== through /robot/command ====================

TOKEN = "t-guards"


@pytest.fixture
def client(monkeypatch, board):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    return TestClient(app)


def command(client, action, **extra):
    return client.post("/robot/command", json={"token": TOKEN, "action": action, **extra}).json()


def test_a_joystick_drive_during_a_run_is_refused_with_the_reason(client, board, monkeypatch):
    running(monkeypatch, "active")
    out = command(client, "drive", direction="forward")
    assert out["ok"] is False and out["error"] == "run_active"
    assert "ปุ่มหยุดฉุกเฉิน" in out["hint"]
    assert board.calls == []
    assert command(client, "stop")["ok"] is True, "stop still works"


# ---- the desk STOP during a route run (finding #0) ----


def _deletes(board):
    return [c for c in board.calls if c[0] == "DELETE"]


@pytest.mark.parametrize("activity", ["active", "stale"])
def test_the_desk_stop_during_a_run_goes_through_the_face(client, board, monkeypatch,
                                                          activity):
    """/robot and /mapview STOP used to DELETE the board's action, which
    drive_route reads as a deleted action: it renews velocity control and
    drives on. During a run the stop must be the run's own — a nav stop and
    a body stop through the face — and no DELETE at all."""
    face = running(monkeypatch, activity)
    out = command(client, "stop")
    assert out["ok"] is True and out["did"] == "stop_requested"
    sent = [(c["type"], c["action"]) for c in face.ws.sent]
    assert ("nav_command", "stop") in sent and ("body_command", "stop") in sent
    assert _deletes(board) == [], "a DELETE mid-run makes drive_route redo the step"


def test_a_desk_stop_the_face_does_not_ack_says_press_the_red_button(client, board,
                                                                     monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.05)
    running(monkeypatch, "active", answers=False)
    out = command(client, "stop")
    assert out["ok"] is False and out["did"] == "stop_requested"
    assert "ปุ่มหยุดฉุกเฉิน" in out["hint"]
    assert _deletes(board) == []


def test_a_desk_stop_with_the_face_gone_is_not_called_stopped(client, board, monkeypatch):
    """The face dropped mid-run: the server cannot reach the launcher. The
    page is told so; the board cancel is all that is left (a pause)."""
    face = running(monkeypatch, "active")
    robot_face_link.session_closed(face)
    assert robot_face_link.nav_activity() == "active"
    out = command(client, "stop")
    assert out["ok"] is False and out["error"] == "no_robot_face"
    assert "ฉุกเฉิน" in out["hint"]
    assert len(_deletes(board)) == 1


def test_a_desk_stop_with_no_run_is_the_board_cancel_as_before(client, board, monkeypatch):
    face = running(monkeypatch, "active")
    robot_face_link.receive(face, {"type": "nav_status", "state": "arrived", "run_id": RUN,
                                   "origin": "tap", "place": "big_room"})
    face.ws.sent.clear()
    out = command(client, "stop")
    assert out == {"ok": True, "did": "stop"}
    assert len(_deletes(board)) == 1 and face.ws.sent == []


# ---- review 2, findings #0-#2: a walk the server cannot see ----


def test_a_desk_stop_with_an_unseen_walk_on_a_routes_machine_is_not_called_stopped(
        client, board, monkeypatch):
    """No face page reporting (before the status link: a panel-tap walk or a
    PC run between calls) is nav_activity "unknown". The button used to answer
    {"ok": true, "did": "stop"} — /mapview said 'สั่งหยุดแล้ว' — over a
    board DELETE that drive_route reads as a link drop and redoes. The
    DELETE still goes; the page is told it is not a confirmed stop."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert robot_face_link.nav_activity() == "unknown"
    out = command(client, "stop")
    assert out["ok"] is False and out["did"] == "stop_unconfirmed"
    assert out["error"] == "run_unknown"
    assert "ฉุกเฉิน" in out["hint"] and "หน้าหุ่น" in out["hint"]
    assert len(_deletes(board)) == 1


def test_a_launcher_once_seen_makes_unknown_unconfirmed_even_with_routes_off(
        client, board, monkeypatch):
    """Routes off, but a face reported the launcher installed during this
    server's life: a PC run between calls is possible and invisible. The
    face's call ended idle, so its entry is gone and activity is unknown."""
    assert settings.robot_nav_routes_enabled is False
    face = Face()
    robot_face_link.claim(face)
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "places": [], "run": None})
    robot_face_link.receive(face, {"type": "nav_status", "state": "idle", "run_id": None})
    robot_face_link.session_closed(face)
    assert robot_face_link.nav_activity() == "unknown"
    assert robot_face_link.leg_runner_possible() is True
    out = command(client, "stop")
    assert out["ok"] is False and out["did"] == "stop_unconfirmed"
    assert len(_deletes(board)) == 1


def test_a_desk_stop_on_a_machine_with_no_leg_runner_is_as_before(client, board):
    """The sales-room desk machine: routes off, no face ever — byte for byte
    the old answer."""
    assert robot_face_link.leg_runner_possible() is False
    out = command(client, "stop")
    assert out == {"ok": True, "did": "stop"}
    assert len(_deletes(board)) == 1


def test_a_map_click_with_localization_off_says_relocalize(client, board):
    board.localization = False
    out = command(client, "goto", x=0.5, y=0.5)
    assert out["ok"] is False and out["error"] == "relocalize"
    assert "ตั้งตำแหน่งใหม่" in out["hint"]
    assert board.posts() == []


@pytest.mark.parametrize("action", ["home", "undock", "forward"])
def test_every_planner_button_says_why(client, board, monkeypatch, action):
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    out = command(client, action)
    assert out["ok"] is False and out["error"] == "routes_own_the_base"
    assert board.posts() == []
