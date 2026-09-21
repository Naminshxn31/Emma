"""Driving the chassis directly, over SLAMTEC's RESTful API.

Nothing here touches a robot. Every test drives `robot_chassis.request`
through a stand-in for the navigation board, which is the only place this
module reaches the network — `conftest` pins `ROBOT_CHASSIS_URL` empty for
the same reason it pins `INVENTORY_URL`, except that this one has motors on
the end of it.

The theme of the file is the one rule the movement path cannot lose: **an
HTTP 200 is not an arrival.** Sending a command proves a message left the
building. Whether the robot got anywhere is a separate measurement, and when
it cannot be made the guest is told so rather than told a story.
"""
from __future__ import annotations

import asyncio
import math

import pytest

from app import robot_chassis
from app.config import settings
from app.tools import registry, robot_link


def run(coro):
    return asyncio.run(coro)


#: Shaped the way SLAMTEC documents `/api/multi-floor/map/v1/pois`. The
#: gallery's own map had none of these on 2026-09-10 — nobody has walked the
#: robot around and saved any points yet — so this is the documented shape,
#: not a capture, and `_poi_name` tries several spellings for that reason.
POIS = [
    {"id": "poi-1", "pose": {"x": 3.0, "y": 4.0, "yaw": 1.5},
     "metadata": {"display_name": "ห้องตัวอย่าง"}},
    {"id": "poi-2", "pose": {"x": -1.0, "y": 2.0},
     "metadata": {"display_name": "สระว่ายน้ำ"}},
]


def explore_blob(origin=(-1.0, -2.0), size=(4, 3), resolution=0.5, cells=None) -> bytes:
    """A `getExploreMap` response, laid out the way this robot's `/js/spec.js`
    describes it: 20 bytes of header, 12 reserved, a uint32 byte count, then
    one byte per cell. Little-endian. Built here rather than captured because
    the robot was offline the day this was written; the layout is the spec's."""
    import struct

    width, height = size
    # Default cells: 0 (never observed), then 1..n — all "free" under the
    # measured reading (positive int8) except the very first cell.
    cells = bytes(range(width * height)) if cells is None else cells
    return (struct.pack("<ffIIf", origin[0], origin[1], width, height, resolution)
            + b"\0" * 12 + struct.pack("<I", len(cells)) + cells)


class FakeBoard:
    """The navigation board, minus the robot."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.pois = list(POIS)
        self.artifact_pois: list[dict] = []
        self.power = {"batteryPercentage": 35, "isCharging": True,
                      "dockingStatus": "on_dock"}
        self.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self.action: dict | None = None
        self.fail: Exception | None = None
        self.map_blob: bytes | None = explore_blob()
        self.quality = 71           # a localized robot; tests set 0..12 for "lost"
        self.localization_enabled = True
        self.homedocks: list[dict] = []
        self.floors: list[dict] = []
        self.map_saves = 0
        # Two real points from this robot's frame on 2026-09-10, plus the
        # kinds of entry a page must never draw at the origin.
        self.scan: dict | None = {
            "pose": {"x": 0.03, "y": 0.2, "yaw": 0.05},
            "laser_points": [
                {"angle": 1.5854684114456177, "distance": 0.43447986245155334, "valid": True},
                {"angle": -0.2, "distance": 0.0, "valid": False},
                {"angle": "north", "distance": 1.0, "valid": True},
                {"angle": 0.5, "distance": float("nan"), "valid": True},
                "garbage",
            ],
        }

    async def request_raw(self, method, path):
        self.calls.append((method, path, None))
        if self.fail is not None:
            raise self.fail
        if path == robot_chassis.MAP_EXPLORE:
            return self.map_blob
        if path == robot_chassis.MAP_STCM:
            return b"STCM" * 100
        raise AssertionError("unexpected raw request: %s %s" % (method, path))

    async def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.fail is not None:
            raise self.fail
        if path == "/api/core/system/v1/power/status":
            return dict(self.power)
        if path == "/api/multi-floor/map/v1/pois":
            return list(self.pois)
        if path == "/api/core/artifact/v1/pois" and method == "GET":
            return list(self.artifact_pois)
        if path == "/api/core/system/v1/robot/info":
            return {"modelName": "Slamware SDP"}
        if path == robot_chassis.LASERSCAN:
            return None if self.scan is None else dict(self.scan)
        if path == robot_chassis.LOCALIZATION_QUALITY:
            return self.quality
        if path == robot_chassis.LOCALIZATION_ENABLE:
            if method == "PUT":
                self.localization_enabled = bool(payload["enable"])
                return True
            return self.localization_enabled
        if path == robot_chassis.HOMEDOCKS:
            return list(self.homedocks)
        if path.startswith(robot_chassis.HOMEDOCKS + "/") and method == "PUT":
            dock_id = path.rsplit("/", 1)[1]
            for d in self.homedocks:
                if d["id"] == dock_id:
                    d["pose"] = dict(payload["pose"])
                    return True
            return None
        if path == "/api/core/slam/v1/localization/pose":
            if method == "PUT":
                self.pose = {k: payload[k] for k in ("x", "y", "yaw")}
                return None
            return dict(self.pose)
        if path == "/api/core/motion/v1/actions/:current":
            if method == "DELETE":
                self.action = None
                return None
            return dict(self.action) if self.action else None
        if method == "POST" and path == "/api/core/motion/v1/actions":
            self.action = {"action_id": 77, "action_name": payload["action_name"],
                           "state": {"status": robot_chassis.STATUS_RUNNING}}
            return dict(self.action)
        if path == robot_chassis.FLOORS:
            return list(self.floors)
        if method == "POST" and path == robot_chassis.MAP_SAVE:
            self.map_saves += 1
            return None
        if method == "POST" and path == robot_chassis.POIS:
            # Measured 2026-09-12: this board refuses a POI that carries its
            # own pose (403 "operation fail") and files a pose-less one at
            # the robot's current position.
            if "pose" in payload:
                raise RuntimeError("403 operation fail")
            self.artifact_pois.append({**payload, "pose": dict(self.pose)})
            return None
        raise AssertionError("unexpected request: %s %s" % (method, path))


class FakeProvider:
    def __init__(self):
        self.said: list[str] = []

    async def send_text(self, text):
        self.said.append(text)


class FakeSession:
    def __init__(self):
        self.provider = FakeProvider()
        self.ws = self

    async def send_text(self, text):        # pragma: no cover - unused here
        pass


@pytest.fixture(autouse=True)
def _tools():
    from app.tools import load_tools

    load_tools()


@pytest.fixture
def board(monkeypatch):
    """A reachable chassis with two points on its map."""
    fake = FakeBoard()
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_chassis_url", "http://chassis.test")
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", True)
    monkeypatch.setattr(robot_chassis, "request", fake.request)
    monkeypatch.setattr(robot_chassis, "request_raw", fake.request_raw)
    robot_chassis.reset_state()
    robot_link.reset_state()
    run(robot_chassis.refresh_places())
    robot_chassis.STATE["connected"] = True
    yield fake
    robot_chassis.reset_state()
    robot_link.reset_state()


@pytest.fixture
def live(monkeypatch):
    """Somebody is on the line, so an announcement has somewhere to land."""
    from app import session as session_module

    session = FakeSession()
    monkeypatch.setattr(session_module, "_active", session)
    return session


# ==================== the suite must not reach real motors ====================


def test_the_suite_cannot_reach_a_real_chassis():
    """The `INVENTORY_URL` pin with a turning circle.

    A machine whose .env points at the robot would otherwise send every
    movement test's command to the board, and `available()` would answer True
    in tests written for a robot that is not in the room.
    """
    assert settings.robot_chassis_url == ""
    assert robot_chassis.configured() is False
    assert robot_chassis.connected() is False


def test_a_url_alone_does_not_arm_the_chassis(monkeypatch):
    """`ROBOT_ENABLED` stays the master switch. Anything else and the one
    knob the gallery is told to keep off would stop being the one knob."""
    monkeypatch.setattr(settings, "robot_chassis_url", "http://chassis.test")
    monkeypatch.setattr(settings, "robot_enabled", False)
    assert robot_chassis.configured() is False


# ==================== the map is the authority ====================


def test_places_and_poses_come_from_the_chassis_map(board):
    assert robot_chassis.places() == ["ห้องตัวอย่าง", "สระว่ายน้ำ"]
    assert robot_chassis._POSES["ห้องตัวอย่าง"] == {"x": 3.0, "y": 4.0, "yaw": 1.5}
    assert robot_chassis._POSES["สระว่ายน้ำ"]["yaw"] == 0.0, "missing yaw is 0, not a crash"


def test_a_point_with_no_pose_is_dropped_rather_than_offered(board):
    """A destination we cannot send the robot to is not a destination.

    Keeping it in `places()` would put it in the list the model reads out,
    and every guest who picked it would get a refusal after being offered it.
    """
    board.pois = POIS + [{"id": "poi-3", "metadata": {"display_name": "ไม่มีพิกัด"}}]
    names = run(robot_chassis.refresh_places())
    assert names == ["ห้องตัวอย่าง", "สระว่ายน้ำ"]


#: Captured verbatim from this robot's `/api/core/slam/v1/homedocks` on
#: 2026-09-10. The map has no POIs yet, so this is the only real example of
#: the shape the firmware actually emits — and it matches what `_poi_name`
#: and `_poi_pose` were written against, which until now was only documented.
REAL_DOCK = {
    "id": "home_dock",
    "metadata": {"display_name": "9b3054e8"},
    "pose": {"x": -0.2497546523809433, "y": 0.011072637513279915,
             "yaw": -0.044305011630058289},
}


def test_the_shape_this_firmware_really_emits_parses(board):
    """A captured payload, not a documented one.

    `display_name` here is a hex id rather than a human name, which is worth
    keeping in the test: it is what an unnamed point looks like, and it is
    why the destination names the technician types matter so much.
    """
    assert robot_chassis._poi_name(REAL_DOCK) == "9b3054e8"
    position = robot_chassis._poi_pose(REAL_DOCK)
    assert position is not None
    assert round(position["x"], 4) == -0.2498
    assert round(position["yaw"], 4) == -0.0443


def test_an_empty_multi_floor_list_is_not_the_final_answer(board):
    """Found on the real board, not in the documentation.

    `/api/multi-floor/map/v1/pois` answers `[]` with a 200 when nothing is
    filed under a floor, and this robot has no saved floors at all — it runs
    a single unsaved map. Stopping at that empty list would report "no
    destinations" on a robot that has some, and the failure would look like
    an empty map rather than like asking the wrong endpoint.
    """
    board.pois = []
    board.artifact_pois = list(POIS)

    assert run(robot_chassis.refresh_places()) == ["ห้องตัวอย่าง", "สระว่ายน้ำ"]
    assert ("GET", "/api/core/artifact/v1/pois", None) in board.calls


def test_an_empty_map_is_not_papered_over_with_mock_places(board, monkeypatch):
    """The one that would have embarrassed us on day one.

    `ROBOT_MOCK_PLACES` exists so the guiding conversation can be rehearsed
    before the robot arrives. Once a real chassis is answering, those names
    are fiction — and the robot's map really was empty on the day this was
    written. Offering them would have the assistant proposing a walk to a
    point that exists in a .env file and nowhere on the floor.
    """
    monkeypatch.setattr(settings, "robot_mock_places", ["ฟิตเนส", "ห้องตัวอย่าง"])
    board.pois = []
    run(robot_chassis.refresh_places())

    assert robot_link.places() == []
    result = run(registry.dispatch("go_to_place", {"place": "ฟิตเนส"}))
    assert result["ok"] is False
    assert result["known_places"] == []


# ==================== ordering a walk ====================


def test_a_walk_is_ordered_with_the_poi_pose_and_obstacle_avoidance(board):
    assert run(robot_link.send("move_to_point", place="ห้องตัวอย่าง")) == "ok"

    method, path, payload = board.calls[-1]
    assert (method, path) == ("POST", "/api/core/motion/v1/actions")
    assert payload["action_name"] == robot_chassis.MOVE_TO
    assert payload["options"]["target"] == {"x": 3.0, "y": 4.0, "z": 0}
    assert payload["options"]["move_options"]["yaw"] == 1.5
    assert payload["options"]["move_options"]["mode"] == 0, (
        "mode 1 follows a fixed track; a room with customers in it needs the "
        "mode that goes around them")


def test_a_place_with_no_pose_is_refused_and_nothing_is_sent(board):
    """`find_place` already refuses to guess a destination. This is the same
    rule one layer down, where a guess would reach the motors."""
    before = len(board.calls)
    assert run(robot_link.send("move_to_point", place="ห้องน้ำ")) == "failed"
    assert len(board.calls) == before


def test_an_unreachable_chassis_reports_mock_rather_than_a_journey(board):
    """And does not quietly hand the wheels back to the Android app path.

    Two routes to the same motors, each with its own idea of where the robot
    is going, is the two-clocks bug this project has already paid for with
    Canva's position and the tour nudge.
    """
    robot_chassis.STATE["connected"] = False
    before = len(board.calls)
    assert run(robot_link.send("move_to_point", place="ห้องตัวอย่าง")) == "mock"
    assert len(board.calls) == before


def test_stopping_goes_straight_to_the_board(board):
    assert run(robot_link.send("cancel_navigation")) == "ok"
    assert board.calls[-1][:2] == ("DELETE", "/api/core/motion/v1/actions/:current")


# ==================== when is a walk over ====================


@pytest.mark.parametrize("status", [robot_chassis.STATUS_WAITING,
                                    robot_chassis.STATUS_RUNNING,
                                    robot_chassis.STATUS_PAUSED,
                                    99])
def test_an_unfinished_or_unknown_status_is_never_an_arrival(status):
    """99 is the point of this test.

    The status numbers follow the REST schema on this robot. An unrecognised code has
    to mean "still going" — that stalls into the arrival timeout, which says
    out loud that the trip failed. Read the other way it would announce an
    arrival that never happened.
    """
    assert robot_chassis._finished({"state": {"status": status}}) == (False, None)


def test_finished_stopped_and_error_are_told_apart():
    assert robot_chassis._finished(
        {"state": {"status": robot_chassis.STATUS_FINISHED, "result": 0}}) == (True, True)
    assert robot_chassis._finished(
        {"state": {"status": robot_chassis.STATUS_FINISHED, "result": 5}}) == (True, False)
    assert robot_chassis._finished(
        {"state": {"status": 4, "result": -2}}) == (True, False)
    assert robot_chassis._finished(
        {"state": {"status": 4, "result": -1}}) == (True, False)


@pytest.mark.parametrize("state", [{"status": 4}, {"status": 4, "result": False}])
def test_done_without_a_numeric_result_does_not_prove_success(state):
    assert robot_chassis._finished({"state": state}) == (True, None)


@pytest.mark.parametrize("code", [0, 1, 3])
def test_rest_navigation_lifecycle_matches_the_robot_schema(board, code):
    board.action = {"action_id": 4, "action_name": robot_chassis.GO_HOME,
                    "state": {"status": code, "result": 0}}
    run(robot_chassis._tick())
    assert robot_link.snapshot()["moving"] is True
    assert robot_chassis._finished(board.action) == (False, None)


def test_a_finished_action_becomes_its_own_turn(board, live):
    """Same road the Android app's `robot_arrived` would have taken.

    Left in state it would never be read: the model is not polling, and after
    a silent tool result there is no turn boundary coming.
    """
    async def scenario():
        await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
        board.action = {"action_id": 77, "action_name": robot_chassis.MOVE_TO,
                        "state": {"status": robot_chassis.STATUS_FINISHED, "result": 0}}
        await robot_chassis._tick()

    run(scenario())
    assert robot_link.STATE["moving"] is False
    assert len(live.provider.said) == 1
    assert "ถึง" in live.provider.said[0]


def test_a_vanished_action_is_judged_by_the_robots_own_pose(board, live):
    """The vendor documentation says a finished action simply disappears, so
    the honest question is not "did the HTTP call work" but "where is it"."""
    async def scenario():
        await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
        board.action = None
        board.pose = {"x": 3.1, "y": 4.05, "yaw": 1.5}
        await robot_chassis._tick()

    run(scenario())
    assert "ถึง" in live.provider.said[0]


def test_an_action_that_vanished_somewhere_else_is_a_failure(board, live):
    """Stopped by an obstacle two metres short looks identical over HTTP to
    a clean arrival. The distance is the only thing that tells them apart."""
    async def scenario():
        await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
        board.action = None
        board.pose = {"x": 1.0, "y": 4.0, "yaw": 0.0}
        await robot_chassis._tick()

    run(scenario())
    said = live.provider.said[0]
    assert "ไม่สำเร็จ" in said
    assert "เจ้าหน้าที่" in said


def test_going_home_is_confirmed_by_the_dock_not_by_the_http_call(board, live):
    """`go_home` has no target coordinate, so the measurement is the one the
    board already publishes: is it on the dock."""
    async def scenario():
        await registry.dispatch("return_to_base", {})
        board.action = None
        board.power = {"batteryPercentage": 35, "isCharging": False,
                       "dockingStatus": "not_on_dock"}
        await robot_chassis._tick()

    run(scenario())
    assert "ไม่สำเร็จ" in live.provider.said[0]


def test_losing_the_board_mid_walk_is_not_an_arrival(board, live):
    """The link dropping tells us nothing about the robot except that we can
    no longer see it. `ROBOT_ARRIVAL_TIMEOUT_S`, armed by `go_to_place`, is
    what ends the walk out loud — not a silent assumption here."""
    async def scenario():
        await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
        board.fail = RuntimeError("tunnel down")
        with pytest.raises(RuntimeError):
            await robot_chassis._tick()

    run(scenario())
    assert live.provider.said == []
    assert robot_link.STATE["moving"] is True
    assert robot_link.STATE["destination"] == "ห้องตัวอย่าง"


# ==================== what the model is allowed to say ====================


def test_status_is_measured_when_the_chassis_can_be_seen(board):
    """The one place this path is strictly better than the app path.

    Everywhere else `moving` is an echo of the last command, which is why
    `snapshot()` blanks it to None so often. Here it is a reading, and
    battery and charging exist at all.
    """
    run(robot_chassis._tick())
    snapshot = robot_link.snapshot()

    assert snapshot["status_source"] == "chassis"
    assert snapshot["battery"] == 35
    assert snapshot["charging"] is True
    assert snapshot["moving"] is False
    assert robot_link.available() is True


def test_the_model_is_not_told_telemetry_is_missing_when_it_is_not(board):
    """`get_robot_status` used to end every answer with "there is no
    telemetry, moving=null means unknown". On the chassis path the battery
    it just handed over is a reading, and that sentence would have the robot
    hedging about a number it knows — while dropping the hedge that still
    matters, which is that its map has nowhere to go.
    """
    run(robot_chassis._tick())
    state = run(registry.dispatch("get_robot_status", {}))

    assert state["battery"] == 35
    assert state["hardware"] == "ok"
    assert "ยังไม่มี telemetry" not in state["instruction"]
    assert "จุดที่พาไปได้คือรายการใน places" in state["instruction"]


def test_an_empty_map_is_said_out_loud_in_the_status(board):
    board.pois = []
    run(robot_chassis.refresh_places())
    run(robot_chassis._tick())
    state = run(registry.dispatch("get_robot_status", {}))

    assert state["places"] == []
    assert "ยังพาไปไม่ได้" in state["instruction"]


def test_health_reports_the_chassis_separately_from_the_app(board):
    """"app_connected" and "the wheels answer" are different facts, and the
    day they disagree is the day somebody needs to read both."""
    run(robot_chassis._tick())
    status = robot_link.status()

    assert status["app_connected"] is False
    assert status["chassis"]["connected"] is True
    assert status["chassis"]["model"] == "Slamware SDP"
    assert status["places_known"] == 2
    assert status["usable"] is True


def test_external_navigation_is_polled_without_our_pending_command(board):
    board.action = {"action_id": 91, "action_name": robot_chassis.MOVE_TO,
                    "state": {"status": robot_chassis.STATUS_RUNNING}}
    board.pose = {"x": 2.0, "y": 1.0, "yaw": 0.4}
    run(robot_chassis._tick())
    snapshot = robot_link.snapshot()
    assert snapshot["moving"] is True
    assert snapshot["pose"] == board.pose
    assert robot_chassis._pending is None


def test_background_action_is_not_reported_as_physical_motion(board):
    board.action = {"action_id": 92,
                    "action_name": "slamtec.agent.actions.HealthSupervisoryAction",
                    "state": {"status": robot_chassis.STATUS_RUNNING}}
    run(robot_chassis._tick())
    state = run(registry.dispatch("get_robot_status", {}))
    assert state["moving"] is None
    assert state["action_name"] == board.action["action_name"]
    assert "ไม่ใช่การวัดความเร็วล้อ" in state["instruction"]


def test_unknown_external_action_does_not_claim_stopped(board):
    board.action = {"action_id": 91, "state": {"status": 999}}
    run(robot_chassis._tick())
    assert robot_link.snapshot()["moving"] is None


def test_stop_ack_waits_for_a_fresh_board_reading(board):
    board.action = {"action_id": 91, "state": {"status": robot_chassis.STATUS_RUNNING}}
    run(robot_chassis._tick())
    assert run(robot_chassis.send("cancel_navigation")) == "ok"
    assert robot_link.snapshot()["moving"] is None
    run(robot_chassis._tick())
    assert robot_link.snapshot()["moving"] is False


def test_disconnected_chassis_cannot_fall_back_to_cached_app_connection(board):
    robot_link.app_connected(["Lobby"])
    robot_chassis.STATE["connected"] = False
    snapshot = robot_link.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["moving"] is None
    assert snapshot["status_source"] == "disconnected"
    assert robot_link.status()["usable"] is False


# ==================== hold-to-drive ====================


def posts(board) -> list[dict]:
    return [c[2] for c in board.calls if c[0] == "POST"]


def deletes(board) -> int:
    return sum(1 for c in board.calls if c[0] == "DELETE")


def test_driving_sends_the_boards_remote_control_action_with_its_direction_code(board):
    async def scenario():
        started = await robot_chassis.drive("left")
        await robot_chassis.drive_stop()
        return started

    assert run(scenario()) is True
    payload = posts(board)[-1]
    assert payload["action_name"] == robot_chassis.MOVE_BY
    assert payload["options"] == {"direction": 3}, "ActionDirection: 0 fwd, 1 back, 2 right, 3 left"


def test_a_direction_the_board_has_no_code_for_is_refused(board):
    with pytest.raises(ValueError):
        run(robot_chassis.drive("up"))
    assert posts(board) == []


def test_the_motion_lock_covers_driving(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    with pytest.raises(PermissionError):
        run(robot_chassis.drive("forward"))
    assert posts(board) == []


def test_a_page_that_goes_quiet_has_its_robot_stopped_by_the_server(board, monkeypatch):
    """The whole point. Closed tab, dropped Wi-Fi, a finger off the button —
    the server hears nothing and cancels the action itself, without waiting
    to find out how long the board's own deadman takes."""
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 50)

    async def scenario():
        await robot_chassis.drive("forward")
        assert deletes(board) == 0
        await asyncio.sleep(0.2)

    run(scenario())
    assert deletes(board) == 1
    assert robot_chassis._drive_task is None
    assert robot_chassis.STATE["moving"] is None, "not False: nothing has measured it stopped"


def test_heartbeats_inside_the_timeout_keep_it_going(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 80)

    async def scenario():
        first = await robot_chassis.drive("forward")
        for _ in range(5):
            await asyncio.sleep(0.03)
            again = await robot_chassis.drive("forward")
            assert again is False, "a heartbeat is not a new press"
        assert deletes(board) == 0, "never went quiet, never stopped"
        await robot_chassis.drive_stop()
        return first

    assert run(scenario()) is True
    assert deletes(board) == 1
    assert len(posts(board)) == 6


def test_releasing_stops_at_once_and_disarms_the_deadman(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 50)

    async def scenario():
        await robot_chassis.drive("back")
        await robot_chassis.drive_stop()
        await asyncio.sleep(0.15)

    run(scenario())
    assert deletes(board) == 1, "the deadman did not fire a second cancel"


def test_stop_with_nothing_held_is_still_a_stop(board):
    run(robot_chassis.drive_stop())
    assert deletes(board) == 1


def ring(front=2.0, back=2.0, left=2.0, right=2.0) -> dict:
    """A lidar frame with one return per arc, at the given distances."""
    return {"laser_points": [
        {"angle": 0.0, "distance": front, "valid": True},
        {"angle": math.pi, "distance": back, "valid": True},
        {"angle": math.pi / 2, "distance": left, "valid": True},
        {"angle": -math.pi / 2, "distance": right, "valid": True},
    ]}


def test_clearance_is_the_nearest_return_per_arc():
    points = ring(front=0.9, back=1.1, left=0.4, right=0.5)["laser_points"] + [
        {"angle": math.radians(30), "distance": 0.25, "valid": True},    # still "front"
        {"angle": math.radians(40), "distance": 0.05, "valid": True},    # between arcs: nobody's
        {"angle": 0.1, "distance": 0.01, "valid": False},                # no return: ignored
        {"angle": -math.pi + 0.1, "distance": 0.7, "valid": True},       # wraps into "back"
    ]
    assert robot_chassis.clearance(points) == {"front": 0.25, "back": 0.7, "left": 0.4, "right": 0.5}


def test_an_empty_frame_has_no_clearance_not_infinite_clearance():
    assert robot_chassis.clearance([]) == {"front": None, "back": None, "left": None, "right": None}


def test_driving_into_something_closer_than_the_line_is_refused(board):
    board.scan = ring(front=0.25)
    with pytest.raises(robot_chassis.ObstacleError) as caught:
        run(robot_chassis.drive("forward"))
    assert caught.value.direction == "forward"
    assert caught.value.distance == 0.25
    assert posts(board) == [], "nothing reached the motors"


def test_the_line_is_only_in_the_direction_of_travel(board):
    """0.7 m from a wall on 2026-09-10 and unable to move would be stuck,
    not safe: backing away and turning must still work with a wall in
    front, and driving forward must still work with a wall behind."""
    board.scan = ring(front=0.2, back=0.2, left=0.1, right=0.1)

    async def scenario():
        with pytest.raises(robot_chassis.ObstacleError):
            await robot_chassis.drive("forward")
        with pytest.raises(robot_chassis.ObstacleError):
            await robot_chassis.drive("back")
        assert await robot_chassis.drive("left") is True
        await robot_chassis.drive("right")
        await robot_chassis.drive_stop()
        board.scan = ring(front=2.0, back=0.2)
        assert await robot_chassis.drive("forward") is True
        await robot_chassis.drive_stop()

    run(scenario())
    assert [p["options"]["direction"] for p in posts(board)] == [3, 2, 0]


def test_a_wall_that_appears_mid_press_stops_the_burst(board, monkeypatch):
    """The check runs on every heartbeat against the watcher's frame (at
    most `SCAN_WATCH_S` old), and a burst already rolling is cancelled
    before the refusal goes back to the page."""
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 5000)
    monkeypatch.setattr(robot_chassis, "SCAN_WATCH_S", 0.02)

    async def scenario():
        await robot_chassis.drive("forward")
        assert deletes(board) == 0
        board.scan = ring(front=0.28)
        await asyncio.sleep(0.06)              # one watcher tick
        with pytest.raises(robot_chassis.ObstacleError):
            await robot_chassis.drive("forward")

    run(scenario())
    assert deletes(board) == 1
    assert robot_chassis._drive_task is None


def scans(board) -> int:
    return sum(1 for c in board.calls if c[1] == robot_chassis.LASERSCAN)


def test_a_heartbeat_is_one_round_trip_not_two(board, monkeypatch):
    """The stutter of 2026-09-12: scan-then-move per heartbeat through the
    relay could outlast the board's MoveBy lifetime. Heartbeats now read
    the watcher's frame; only the first of a burst fetches inline."""
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 2000)
    board.scan = ring(front=2.0)

    async def scenario():
        await robot_chassis.drive("forward")
        first = scans(board)
        for _ in range(5):
            await asyncio.sleep(0.02)          # well inside SCAN_FRESH_S
            await robot_chassis.drive("forward")
        inline = scans(board) - first
        assert robot_chassis._scan_task is not None and not robot_chassis._scan_task.done()
        await robot_chassis.drive_stop()
        return inline

    inline = run(scenario())
    assert inline <= 1, "five heartbeats did not each fetch a frame (the watcher may have fetched one)"
    assert len(posts(board)) == 6
    assert robot_chassis._scan_task is None, "watcher stopped with the burst"


def test_a_wall_seen_by_the_watcher_still_stops_the_burst(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 2000)
    monkeypatch.setattr(robot_chassis, "SCAN_WATCH_S", 0.02)
    board.scan = ring(front=2.0)

    async def scenario():
        await robot_chassis.drive("forward")
        board.scan = ring(front=0.2)
        await asyncio.sleep(0.08)              # the watcher sees the wall
        with pytest.raises(robot_chassis.ObstacleError):
            await robot_chassis.drive("forward")

    run(scenario())
    assert deletes(board) == 1


def test_a_stale_frame_is_refetched_not_trusted(board, monkeypatch):
    board.scan = ring(front=2.0)

    async def scenario():
        await robot_chassis._fresh_scan()
        n = scans(board)
        monkeypatch.setattr(robot_chassis, "_scan_at", -1e9)
        await robot_chassis._fresh_scan()
        return scans(board) - n

    assert run(scenario()) == 1


def test_a_clearance_of_zero_switches_the_line_off(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_drive_min_clearance_m", 0.0)
    board.scan = ring(front=0.05)

    async def scenario():
        await robot_chassis.drive("forward")
        await robot_chassis.drive_stop()

    run(scenario())
    assert len(posts(board)) == 1


# ==================== finding itself on the map ====================


DOCK = {"id": "home_dock", "metadata": {"display_name": "9b3054e8"},
        "pose": {"x": -0.2497546523809433, "y": 0.011072637513279915,
                 "yaw": -0.044305011630058289}}


def test_relocalizing_on_the_dock_tells_slam_where_it_is_and_asks_it_to_confirm_without_moving(board):
    board.homedocks = [DOCK]
    board.quality = 0
    board.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}          # odometry, drifted
    report = run(robot_chassis.relocalize("dock"))

    assert report["quality_before"] == 0
    assert board.pose == {"x": DOCK["pose"]["x"], "y": DOCK["pose"]["y"], "yaw": DOCK["pose"]["yaw"]}
    payload = posts(board)[-1]
    assert payload["action_name"] == robot_chassis.RECOVER_LOCALIZATION
    assert payload["options"]["relocalization_options"]["recover_movement_type"] == "NoMove"
    assert report["pose_set_to_dock"]["x"] == DOCK["pose"]["x"]
    assert report["action_id"] == 77


def test_relocalizing_on_the_dock_is_refused_when_the_robot_is_not_on_it(board):
    board.homedocks = [DOCK]
    board.power = {"batteryPercentage": 10, "isCharging": False, "dockingStatus": "not_on_dock"}
    with pytest.raises(ValueError, match="ไม่ได้อยู่บนแท่น"):
        run(robot_chassis.relocalize("dock"))
    assert posts(board) == []
    assert board.pose == {"x": 0.0, "y": 0.0, "yaw": 0.0}, "no pose was invented"


def test_relocalizing_switches_localization_back_on_if_the_board_had_paused_it(board):
    """"false = pure odometry mode" in the spec — quality stays 0 there no
    matter what the lidar sees, which is exactly the symptom of 2026-09-12."""
    board.localization_enabled = False
    report = run(robot_chassis.relocalize("static"))
    assert report["localization_was_paused"] is True
    assert board.localization_enabled is True
    assert ("PUT", robot_chassis.LOCALIZATION_ENABLE, {"enable": True}) in board.calls


def test_static_relocalization_does_not_need_the_motion_lock_but_rotating_does(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    report = run(robot_chassis.relocalize("static"))
    assert posts(board)[-1]["options"]["relocalization_options"]["recover_movement_type"] == "NoMove"
    assert "pose_set_to_dock" not in report
    with pytest.raises(PermissionError):
        run(robot_chassis.relocalize("rotate"))


def test_rotating_relocalization_asks_for_rotate_only(board):
    run(robot_chassis.relocalize("rotate"))
    assert posts(board)[-1]["options"]["relocalization_options"]["recover_movement_type"] == "RotateOnly"


# ==================== saving where it stands ====================


@pytest.fixture
def standing_here(board):
    """A localized robot on a map with no saved points yet."""
    board.pois = []                 # the multi-floor list this robot answers [] on
    board.quality = 63
    board.pose = {"x": 3.0, "y": -4.12, "yaw": 3.0}
    run(robot_chassis.refresh_places())
    assert robot_chassis.places() == []
    return board


def test_saving_a_spot_lets_the_board_file_it_where_the_robot_stands(standing_here):
    """No pose in the request — measured on 2026-09-12, the board answers
    403 to a POI that brings its own and accepts one that does not, filing
    it at its own current position. The pose in the reply is read back from
    the board's list, so it is what a guest will actually be walked to."""
    import uuid

    saved = run(robot_chassis.save_poi("ที่ชาร์จ"))
    payload = [c[2] for c in standing_here.calls if c[0] == "POST" and c[1] == robot_chassis.POIS][-1]
    uuid.UUID(payload["id"])
    assert "pose" not in payload
    assert payload["metadata"] == {"display_name": "ที่ชาร์จ", "type": "point"}
    assert saved["pose"] == {"x": 3.0, "y": -4.12, "yaw": 3.0}
    assert saved["places"] == ["ที่ชาร์จ"]
    assert robot_chassis.places() == ["ที่ชาร์จ"], "offered on the next go_to_place"
    assert robot_chassis._POSES["ที่ชาร์จ"] == {"x": 3.0, "y": -4.12, "yaw": 3.0}


def test_a_saved_spot_is_persisted_to_the_boards_storage(standing_here):
    saved = run(robot_chassis.save_poi("ที่ชาร์จ"))
    assert saved["persisted"] is True
    assert standing_here.map_saves == 1


def test_a_spot_that_could_not_be_persisted_says_so_instead_of_failing(standing_here, monkeypatch):
    original = standing_here.request

    async def no_save(method, path, payload=None):
        if path == robot_chassis.MAP_SAVE:
            raise RuntimeError("500")
        return await original(method, path, payload)

    monkeypatch.setattr(robot_chassis, "request", no_save)
    saved = run(robot_chassis.save_poi("ที่ชาร์จ"))
    assert saved["persisted"] is False
    assert robot_chassis.places() == ["ที่ชาร์จ"], "the point still exists until the next reboot"


def test_saving_the_map_is_refused_on_a_multi_floor_robot(board):
    """The spec's own warning: on a multi-floor deployment this loses every
    other floor. This robot has none; the guard is for the day it does."""
    board.floors = [{"floor": "1F"}, {"floor": "2F"}]
    with pytest.raises(ValueError, match="หลายชั้น"):
        run(robot_chassis.save_map())
    assert board.map_saves == 0
    board.floors = []
    assert run(robot_chassis.save_map()) == {"saved": True, "map_bytes": 400}


def test_going_home_needs_the_robot_to_know_where_it_is(board):
    """A go-home from an odometry pose walks to where the dock would be if
    the robot were where it thinks it is — three metres off on 2026-09-12."""
    board.quality = 5
    with pytest.raises(ValueError, match="ปรับตำแหน่ง"):
        run(robot_chassis.go_home())
    assert posts(board) == []
    board.quality = 71
    assert run(robot_chassis.go_home()) == 77
    assert posts(board)[-1]["action_name"] == robot_chassis.GO_HOME


def test_a_spot_is_not_saved_from_an_odometry_pose(standing_here):
    """(0, 0) for a robot really at (3.0, -4.1) — 2026-09-12 before
    relocalizing. Saved, that would have been a destination to nowhere."""
    standing_here.quality = 12
    with pytest.raises(ValueError, match="ปรับตำแหน่ง"):
        run(robot_chassis.save_poi("ที่ชาร์จ"))
    assert standing_here.artifact_pois == []


def test_a_spot_needs_a_name_and_the_name_must_be_new(standing_here):
    with pytest.raises(ValueError):
        run(robot_chassis.save_poi("   "))
    run(robot_chassis.save_poi("ที่ชาร์จ"))
    with pytest.raises(ValueError, match="อยู่แล้ว"):
        run(robot_chassis.save_poi("ที่ชาร์จ"))
    assert len(standing_here.artifact_pois) == 1


def test_a_board_that_accepts_a_spot_but_does_not_list_it_is_reported(standing_here, monkeypatch):
    async def swallow(method, path, payload=None):
        if method == "POST" and path == robot_chassis.POIS:
            return None                      # 200, and then nothing
        return await standing_here.request(method, path, payload)

    monkeypatch.setattr(robot_chassis, "request", swallow)
    with pytest.raises(RuntimeError, match="ไม่แสดงในรายการ"):
        run(robot_chassis.save_poi("ที่ชาร์จ"))


def test_a_saved_spot_can_be_walked_to_by_the_voice_path(standing_here, live):
    run(robot_chassis.save_poi("ที่ชาร์จ"))
    result = run(registry.dispatch("go_to_place", {"place": "ที่ชาร์จ"}))
    assert result["ok"] is True
    payload = [c for c in standing_here.calls if c[0] == "POST"][-1][2]
    assert payload["action_name"] == robot_chassis.MOVE_TO
    assert payload["options"]["target"] == {"x": 3.0, "y": -4.12, "z": 0}


# ==================== the dock has to be where the dock is ====================


def test_a_dock_registered_in_an_old_map_frame_is_reported_stale(board):
    """2026-09-12: dock at (-0.25, 0.01), docked + localized robot at
    (3.05, -4.05). `go_home` from off the dock would have driven to the
    old point. The board never corrects this itself — its
    `docked_register_strategy` is `when_not_exists`."""
    import copy

    board.homedocks = [copy.deepcopy(DOCK)]
    board.pose = {"x": 3.05, "y": -4.05, "yaw": 3.02}
    check = run(robot_chassis.dock_check())
    assert check["docked"] is True and check["stale"] is True
    assert 5.0 < check["offset_m"] < 5.5, "hypot(3.30, 4.06) — the 'seven metres' first reported was a guess"


def test_dock_check_has_no_opinion_off_the_dock_or_without_a_fix(board):
    import copy

    board.homedocks = [copy.deepcopy(DOCK)]
    board.pose = {"x": 3.05, "y": -4.05, "yaw": 3.02}
    board.power = {"batteryPercentage": 35, "isCharging": False, "dockingStatus": "not_on_dock"}
    assert run(robot_chassis.dock_check())["stale"] is None
    board.power = {"batteryPercentage": 35, "isCharging": True, "dockingStatus": "on_dock"}
    board.quality = 10
    assert run(robot_chassis.dock_check())["stale"] is None


def test_re_registering_moves_the_existing_dock_to_the_robot_and_persists(board):
    import copy

    board.homedocks = [copy.deepcopy(DOCK)]
    board.pose = {"x": 3.05, "y": -4.05, "yaw": 3.02}
    report = run(robot_chassis.register_dock())

    assert report["dock"] == {"x": 3.05, "y": -4.05, "yaw": 3.02}
    assert report["was"]["x"] == DOCK["pose"]["x"]
    assert report["persisted"] is True and board.map_saves == 1
    assert len(board.homedocks) == 1, "edited in place — not a second dock for go_home to guess between"
    assert run(robot_chassis.dock_check())["stale"] is False


def test_re_registering_is_refused_off_the_dock_or_without_a_fix(board):
    import copy

    board.homedocks = [copy.deepcopy(DOCK)]
    board.power = {"batteryPercentage": 35, "isCharging": False, "dockingStatus": "not_on_dock"}
    with pytest.raises(ValueError, match="บนแท่น"):
        run(robot_chassis.register_dock())
    board.power = {"batteryPercentage": 35, "isCharging": True, "dockingStatus": "on_dock"}
    board.quality = 10
    with pytest.raises(ValueError, match="ปรับตำแหน่ง"):
        run(robot_chassis.register_dock())
    assert board.homedocks[0]["pose"] == DOCK["pose"], "untouched"
    assert board.map_saves == 0


def test_undocking_is_one_step_ahead_as_a_navigation_action(board):
    """Not MoveBy: on 2026-09-12 remote control on the dock made actions
    that finished with the robot never moving. MoveTo is how the vendor app
    leaves the dock."""
    board.map_blob = explore_blob(origin=(-5.0, -5.0), size=(20, 20), resolution=0.5, cells=bytes([127] * 400))
    board.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
    board.scan = ring(front=2.0)
    report = run(robot_chassis.undock())
    assert report["sent"] == "ok"
    assert report["target"] == {"x": 0.6, "y": 0.0}
    payload = posts(board)[-1]
    assert payload["action_name"] == robot_chassis.MOVE_TO
    assert payload["options"]["target"]["x"] == 0.6


def test_undocking_is_refused_off_the_dock_and_into_an_obstacle(board):
    board.map_blob = explore_blob(origin=(-5.0, -5.0), size=(20, 20), resolution=0.5, cells=bytes([127] * 400))
    board.scan = ring(front=0.5)
    with pytest.raises(robot_chassis.ObstacleError):
        run(robot_chassis.undock())
    board.scan = ring(front=2.0)
    board.power = {"batteryPercentage": 35, "isCharging": False, "dockingStatus": "not_on_dock"}
    with pytest.raises(ValueError, match="ไม่ได้อยู่บนแท่น"):
        run(robot_chassis.undock())
    assert posts(board) == []


def test_an_unknown_relocalize_mode_is_refused(board):
    with pytest.raises(ValueError):
        run(robot_chassis.relocalize("teleport"))
    assert posts(board) == []


def test_shutting_the_server_down_mid_press_stops_the_robot(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_drive_timeout_ms", 5000)

    async def scenario():
        await robot_chassis.drive("forward")
        await robot_chassis.stop()

    run(scenario())
    assert deletes(board) == 1


# ==================== the lidar frame ====================


def test_a_lidar_frame_keeps_real_points_and_drops_the_undrawable(board):
    scan = run(robot_chassis.laserscan())
    assert scan["pose"] == {"x": 0.03, "y": 0.2, "yaw": 0.05}
    assert scan["points"] == [
        {"angle": 1.5854684114456177, "distance": 0.43447986245155334, "valid": True},
        {"angle": -0.2, "distance": 0.0, "valid": False},
    ], "invalid returns stay (the radar shows where nothing came back); junk goes"
    assert (scan["total"], scan["valid"]) == (2, 1)


def test_a_thinned_frame_keeps_its_angular_coverage(board):
    board.scan = {"laser_points": [
        {"angle": i * 0.01, "distance": 1.0, "valid": True} for i in range(1586)]}
    scan = run(robot_chassis.laserscan(max_points=400))
    assert scan["total"] == 1586, "the count reported is the frame's, not the sample's"
    assert 300 <= len(scan["points"]) <= 400
    assert scan["points"][0]["angle"] == 0.0
    assert scan["points"][-1]["angle"] > 15.8, "every n-th point, so the last sector is still there"


def test_no_frame_is_an_empty_radar_not_a_crash(board):
    board.scan = None
    assert run(robot_chassis.laserscan()) == {
        "pose": None, "points": [], "total": 0, "valid": 0,
        "clearance": {"front": None, "back": None, "left": None, "right": None}}


# ==================== the map, and a point clicked on it ====================


def test_the_explore_map_unpacks_the_way_the_spec_lays_it_out():
    grid = robot_chassis.parse_explore_map(explore_blob())
    assert (grid["origin_x"], grid["origin_y"]) == (-1.0, -2.0)
    assert (grid["width"], grid["height"], grid["resolution"]) == (4, 3, 0.5)
    assert grid["cells"] == bytes(range(12))


@pytest.mark.parametrize("blob", [
    explore_blob()[:-3],                                   # truncated by a relay
    explore_blob(cells=bytes(5)),                          # size field ≠ w×h
    explore_blob()[:20],                                   # header only
    explore_blob(resolution=0.0),                          # unusable scale
])
def test_a_map_that_does_not_add_up_is_refused_not_rendered(blob):
    """The tunnel to the board is an `nc` relay on the robot. A short read
    would otherwise parse into half a room, and the page would offer clicks
    on the half that was never received."""
    with pytest.raises(ValueError):
        robot_chassis.parse_explore_map(blob)


def test_reading_the_map_remembers_its_extent(board):
    assert robot_chassis._map_bounds is None
    grid = run(robot_chassis.explore_map())
    assert grid["width"] == 4
    assert robot_chassis.inside_map(0.9, -0.6) is True       # inside 4×0.5 by 3×0.5
    assert robot_chassis.inside_map(1.0, -0.6) is False      # on the far edge = outside
    assert robot_chassis.inside_map(-1.5, 0.0) is False


def test_no_map_is_a_true_answer_not_an_exception(board):
    board.map_blob = None
    assert run(robot_chassis.explore_map()) is None
    assert robot_chassis.inside_map(0.0, 0.0) is None


def test_a_clicked_point_walks_there_facing_the_way_the_robot_faces(board):
    board.pose = {"x": 0.0, "y": 0.0, "yaw": 0.7}
    assert run(robot_chassis.goto_xy(0.25, -1.75)) == "ok"

    method, path, payload = board.calls[-1]
    assert (method, path) == ("POST", "/api/core/motion/v1/actions")
    assert payload["action_name"] == robot_chassis.MOVE_TO
    assert payload["options"]["target"] == {"x": 0.25, "y": -1.75, "z": 0}
    assert payload["options"]["move_options"]["yaw"] == 0.7, "no yaw was asked for, so none is invented"
    assert payload["options"]["move_options"]["mode"] == 0
    assert robot_chassis._pending["target"] == {"x": 0.25, "y": -1.75, "yaw": 0.7}
    assert robot_chassis.STATE["moving"] is True


@pytest.mark.parametrize("value, kind", [
    (0, robot_chassis.CELL_UNKNOWN),
    (20, robot_chassis.CELL_FREE), (127, robot_chassis.CELL_FREE),
    (129, robot_chassis.CELL_OCCUPIED), (196, robot_chassis.CELL_OCCUPIED),
    (255, robot_chassis.CELL_OCCUPIED),
])
def test_map_bytes_read_as_signed_int8_positive_free_negative_occupied(value, kind):
    """Measured on 2026-09-12 by projecting the live lidar frame onto the live
    map (`scripts/probe_map_semantics.py`): the robot stood on 127, the rays
    crossed 20..127, the returns landed on 129..196. The intuitive reading —
    bigger byte, more solid — is the wrong way round, and this test is what
    stops somebody "fixing" it back."""
    assert robot_chassis.cell_kind(value) == kind


def test_the_cell_under_a_coordinate_comes_from_the_last_map(board):
    run(robot_chassis.explore_map())
    # origin (-1, -2), 0.5 m cells, 4 wide: (0.25, -1.75) is col 2, row 0 -> index 2
    assert robot_chassis.cell_at(0.25, -1.75) == 2
    assert robot_chassis.cell_at(-0.9, -1.9) == 0, "col 0, row 0"
    assert robot_chassis.cell_at(0.9, -0.6) == 11, "col 3, row 2 -> last cell"
    assert robot_chassis.cell_at(5.0, 5.0) is None


def test_a_click_on_a_wall_sends_nothing(board):
    board.map_blob = explore_blob(cells=bytes([129] * 12))
    with pytest.raises(ValueError, match="สิ่งกีดขวาง"):
        run(robot_chassis.goto_xy(0.25, -1.75))
    assert [c for c in board.calls if c[0] == "POST"] == []


def test_a_click_on_unexplored_floor_sends_nothing(board):
    """On a freshly booted robot 80% of the map is 0. The planner's answer to
    a target there was, on 2026-09-10, an action that sat at status 1 with
    an empty stage forever. Refusing up front says why instead."""
    with pytest.raises(ValueError, match="ยังไม่เคยเห็น"):
        run(robot_chassis.goto_xy(-0.9, -1.9))     # cell 0 of the default blob
    assert [c for c in board.calls if c[0] == "POST"] == []


def test_a_stale_map_is_re_read_before_a_click_is_judged(board, monkeypatch):
    run(robot_chassis.explore_map())
    reads_before = sum(1 for c in board.calls if c[1] == robot_chassis.MAP_EXPLORE)
    monkeypatch.setattr(robot_chassis, "_map_read_at", -1e9)
    run(robot_chassis.goto_xy(0.25, -1.75))
    reads_after = sum(1 for c in board.calls if c[1] == robot_chassis.MAP_EXPLORE)
    assert reads_after == reads_before + 1


def test_a_click_off_the_map_sends_nothing(board):
    """The one manual command whose destination the client chooses, held to
    the one check a nudge does not need: the point has to exist on the map."""
    with pytest.raises(ValueError, match="นอกแมพ"):
        run(robot_chassis.goto_xy(50.0, 50.0))
    assert [c for c in board.calls if c[0] == "POST"] == []
    assert robot_chassis._pending is None


def test_a_click_reads_the_map_first_when_none_has_been_read(board):
    """Never checked against a bound that was never measured."""
    assert robot_chassis._map_bounds is None
    run(robot_chassis.goto_xy(0.0, -1.0))
    assert ("GET", robot_chassis.MAP_EXPLORE, None) in board.calls
    assert robot_chassis._map_bounds is not None


def test_a_click_with_no_map_at_all_is_refused(board):
    board.map_blob = None
    with pytest.raises(ValueError, match="ไม่มีแมพ"):
        run(robot_chassis.goto_xy(0.0, 0.0))
    assert [c for c in board.calls if c[0] == "POST"] == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_click_that_is_not_a_number_is_refused(board, bad):
    with pytest.raises(ValueError):
        run(robot_chassis.goto_xy(bad, 0.0))


def test_the_motion_lock_covers_clicked_points_too(board, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    assert run(robot_chassis.goto_xy(0.0, -1.0)) == "failed", "on the map, and still refused"
    assert [c for c in board.calls if c[0] == "POST"] == []


def test_a_clicked_walk_ends_the_way_a_named_one_does(board):
    """Same clock, same verdict: the action vanishes, and where the robot
    actually is decides whether it arrived."""
    run(robot_chassis.goto_xy(0.5, -1.5))
    board.action = None
    board.pose = {"x": 0.55, "y": -1.45, "yaw": 0.0}
    run(robot_chassis._tick())
    assert robot_chassis._pending is None
    assert robot_chassis.STATE["moving"] is False


@pytest.mark.parametrize("action", ["move_to_point", "go_home"])
def test_motion_lock_blocks_navigation_but_allows_stop(board, monkeypatch, action):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    board.calls.clear()
    assert run(robot_chassis.send(action, place="ห้องตัวอย่าง")) == "failed"
    assert board.calls == []
    assert run(robot_chassis.send("cancel_navigation")) == "ok"
    assert board.calls == [("DELETE", "/api/core/motion/v1/actions/:current", None)]
    run(robot_chassis._tick())
    state = run(registry.dispatch("get_robot_status", {}))
    assert state["motion_enabled"] is False
    assert "ล็อกการเริ่มเดิน" in state["instruction"]
