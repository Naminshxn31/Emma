"""Manual control of the robot, at `/robot`.

Everything in this file exists because the thing on the other end has wheels.
The page is a test instrument for one question — does the robot answer us —
and the properties worth holding it to are the ones that decide what happens
when something goes wrong halfway through a step.

Nothing here reaches a robot: `robot_chassis.request` is replaced by a stand-in
for the navigation board, the same way `tests/test_robot_chassis.py` does it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import robot_chassis
from app.config import settings
from app.main import _NUDGE_METRES, _NUDGE_RADIANS, app
from tests.test_robot_chassis import explore_blob

PAGE = Path(__file__).resolve().parent.parent / "client" / "robot-control.html"
TOKEN = "let-me-in"


class FakeBoard:
    def __init__(self):
        self.calls: list[tuple] = []
        self.pose = {"x": 2.0, "y": 1.0, "yaw": 0.0}
        self.front = 1.5            # every lidar return, metres
        self.docking = "not_on_dock"
        self.quality = 61
        self.artifact_pois: list[dict] = []
        # A 10 m × 8 m map at 0.1 m per cell, origin at (-1, -1): the robot at
        # (2, 1) is inside it, (50, 50) is not. Every cell 127 = seen free,
        # except one wall cell at (3.5, 4.0): col 45, row 50.
        cells = bytearray([127] * 8000)
        cells[50 * 100 + 45] = 196
        self.map_blob: bytes | None = explore_blob(origin=(-1.0, -1.0), size=(100, 80),
                                                   resolution=0.1, cells=bytes(cells))

    async def request_raw(self, method, path):
        self.calls.append((method, path, None))
        if path == robot_chassis.MAP_STCM:
            return b"STCM"
        return self.map_blob if path == robot_chassis.MAP_EXPLORE else None

    async def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/api/core/system/v1/power/status":
            return {"batteryPercentage": 40, "isCharging": False,
                    "dockingStatus": self.docking, "powerStage": "running"}
        if path == "/api/core/slam/v1/localization/pose":
            return dict(self.pose)
        if path == "/api/core/motion/v1/actions/:current":
            return None
        if path == "/api/core/system/v1/robot/info":
            return {"modelName": "Slamware SDP"}
        if path == robot_chassis.LASERSCAN:
            return {"pose": dict(self.pose), "laser_points": [
                {"angle": i * 0.004, "distance": self.front, "valid": i % 10 != 0}
                for i in range(1586)]}
        if path == robot_chassis.LOCALIZATION_QUALITY:
            return self.quality
        if path == robot_chassis.LOCALIZATION_ENABLE:
            return True
        if path == robot_chassis.HOMEDOCKS:
            return [{"id": "home_dock", "pose": {"x": -0.25, "y": 0.01, "yaw": -0.04}}]
        if path == robot_chassis.FLOORS:
            return []
        if path == robot_chassis.MAP_SAVE:
            return None
        if path == robot_chassis.POIS:
            if method == "POST":
                if "pose" in payload:
                    raise RuntimeError("403 operation fail")     # measured 2026-09-12
                self.artifact_pois.append({**payload, "pose": dict(self.pose)})
                return None
            return list(self.artifact_pois)
        if path.endswith("/pois"):
            return []
        if method == "POST" and path == "/api/core/motion/v1/actions":
            return {"action_id": 5}
        return None

    def posted(self) -> list[dict]:
        return [payload for method, path, payload in self.calls
                if method == "POST" and payload]


@pytest.fixture
def board(monkeypatch):
    fake = FakeBoard()
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_chassis_url", "http://chassis.test")
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", True)
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(robot_chassis, "request", fake.request)
    monkeypatch.setattr(robot_chassis, "request_raw", fake.request_raw)
    robot_chassis.reset_state()
    yield fake
    robot_chassis.reset_state()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def send(client, action, **extra):
    return client.post("/robot/command",
                       json={"token": TOKEN, "action": action, **extra}).json()


# ==================== the gate ====================


def test_the_page_is_served(client):
    assert client.get("/robot").status_code == 200


@pytest.mark.parametrize("path", ["/drive", "/mapview"])
def test_the_driving_and_map_pages_are_served(client, path):
    """Two more faces of the same `/robot/command`; the distance and the
    on-map check stay on the server whichever page is open."""
    response = client.get(path)
    assert response.status_code == 200
    assert "/robot/command" in response.text


def test_moving_a_robot_needs_the_token(board, client):
    """`WS_TOKEN` guards every socket here because this server presses keys and
    opens programs. This endpoint drives a machine across a room, so it cannot
    be the one thing on the LAN that answers to anybody."""
    assert client.post("/robot/command", json={"action": "forward"})\
        .json() == {"ok": False, "error": "unauthorized"}
    assert client.get("/robot/state").json()["ok"] is False
    assert board.posted() == [], "nothing may reach the board unauthenticated"


def test_a_wrong_token_is_not_close_enough(board, client):
    assert client.post("/robot/command",
                       json={"token": TOKEN + "x", "action": "forward"})\
        .json()["ok"] is False
    assert board.posted() == []


# ==================== stop ====================


def test_stop_still_works_while_motion_is_locked(board, client, monkeypatch):
    """A lock that also disabled the brake would be worse than no lock.

    `ROBOT_CHASSIS_MOTION_ENABLED` exists to stop a robot being sent anywhere
    before somebody has stood next to it with a stop button. It must never be
    the reason a robot that is already moving cannot be told to stop.
    """
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)

    assert send(client, "stop")["ok"] is True
    assert ("DELETE", "/api/core/motion/v1/actions/:current", None) in board.calls


def test_movement_is_refused_while_locked_and_says_how_to_unlock(board, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)

    out = send(client, "forward")
    assert out["ok"] is False
    assert out["error"] == "motion_locked"
    assert "ROBOT_CHASSIS_MOTION_ENABLED" in out["hint"]
    assert board.posted() == [], "a locked robot receives nothing at all"


# ==================== one bounded step ====================


def test_forward_is_one_step_from_the_current_pose(board, client):
    assert send(client, "forward")["ok"] is True

    target = board.posted()[-1]["options"]["target"]
    assert round(target["x"], 4) == round(2.0 + _NUDGE_METRES, 4)
    assert round(target["y"], 4) == 1.0, "facing yaw 0, so y must not change"


def test_back_is_the_same_step_the_other_way(board, client):
    assert send(client, "back")["ok"] is True
    assert round(board.posted()[-1]["options"]["target"]["x"], 4) \
        == round(2.0 - _NUDGE_METRES, 4)


def test_turning_moves_the_heading_and_not_the_robot(board, client):
    """Rotating in place is the safest first proof that the wheels answer:
    it cannot carry the robot into anything."""
    assert send(client, "left")["ok"] is True

    payload = board.posted()[-1]
    assert payload["options"]["target"]["x"] == 2.0
    assert payload["options"]["target"]["y"] == 1.0
    assert round(payload["options"]["move_options"]["yaw"], 4) \
        == round(_NUDGE_RADIANS, 4)

    send(client, "right")
    assert round(board.posted()[-1]["options"]["move_options"]["yaw"], 4) \
        == round(-_NUDGE_RADIANS, 4)


def test_the_page_cannot_choose_how_far(board, client):
    """The distance lives on the server, and the client naming one changes
    nothing. A control surface that takes a number from a web page anyone on
    the LAN can open is one typo away from crossing a gallery."""
    before = send(client, "forward") and board.posted()[-1]["options"]["target"]["x"]
    send(client, "forward", metres=50, distance=50, forward_m=50)
    assert board.posted()[-1]["options"]["target"]["x"] == before


def test_an_unknown_command_is_refused_rather_than_guessed(board, client):
    assert send(client, "dance")["ok"] is False
    assert board.posted() == []


def test_a_destination_that_is_not_on_the_map_is_refused(board, client):
    """Same rule as `go_to_place`: a wrong slide costs a sentence, a wrong
    destination walks a customer across the gallery."""
    out = send(client, "goto", place="ห้องที่ไม่มีจริง")
    assert out["ok"] is False
    assert board.posted() == []


# ==================== the map, and clicking on it ====================


def test_the_map_needs_the_token_like_everything_else_here(board, client):
    assert client.get("/robot/map").json() == {"ok": False, "error": "unauthorized"}
    assert board.calls == []


def test_the_map_goes_out_as_numbers_plus_the_boards_own_bytes(board, client):
    """Base64 of the raw cells, one byte per cell, and the four numbers a page
    needs to turn a pixel into metres. Nothing here decides what a byte means:
    that has not been measured on this robot, and a colour scale guessed from
    another vendor's convention would paint walls as floor."""
    import base64

    out = client.get("/robot/map", params={"token": TOKEN}).json()
    assert out["ok"] is True
    grid = out["map"]
    assert (grid["origin_x"], grid["origin_y"]) == (-1.0, -1.0)
    assert (grid["width"], grid["height"]) == (100, 80)
    assert abs(grid["resolution"] - 0.1) < 1e-6
    cells = base64.b64decode(grid["cells_b64"])
    assert len(cells) == 8000 and cells[0] == 127 and cells[50 * 100 + 45] == 196


def test_a_robot_with_no_map_says_so(board, client):
    board.map_blob = None
    out = client.get("/robot/map", params={"token": TOKEN}).json()
    assert out["ok"] is False
    assert "แมพ" in out["error"]


def test_a_map_the_relay_cut_short_is_an_error_not_a_smaller_room(board, client):
    board.map_blob = board.map_blob[:-100]
    out = client.get("/robot/map", params={"token": TOKEN}).json()
    assert out["ok"] is False
    assert "อ่านไม่ออก" in out["error"]


def test_a_click_on_the_map_walks_to_that_point(board, client):
    out = send(client, "goto", x=3.5, y=2.25)
    assert out == {"ok": True, "did": "goto"}

    payload = board.posted()[-1]
    assert payload["action_name"] == robot_chassis.MOVE_TO
    assert payload["options"]["target"] == {"x": 3.5, "y": 2.25, "z": 0}
    assert payload["options"]["move_options"]["yaw"] == 0.0, "faces the way it already faces"


def test_a_click_arrives_as_strings_too(board, client):
    """Form fields and query strings hand over "3.5", not 3.5."""
    assert send(client, "goto", x="3.5", y="2.25")["ok"] is True
    assert board.posted()[-1]["options"]["target"]["x"] == 3.5


def test_a_click_off_the_map_is_refused_before_the_motors_hear_it(board, client):
    out = send(client, "goto", x=50, y=50)
    assert out["ok"] is False
    assert "นอกแมพ" in out["error"]
    assert board.posted() == []


def test_a_click_on_a_wall_is_refused_with_the_reason(board, client):
    out = send(client, "goto", x=3.55, y=4.05)
    assert out["ok"] is False
    assert "สิ่งกีดขวาง" in out["error"]
    assert board.posted() == []


def test_a_click_while_locked_is_locked_like_every_other_move(board, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    out = send(client, "goto", x=3.5, y=2.25)
    assert out["ok"] is False
    assert out["error"] == "motion_locked"
    assert board.posted() == []


def test_goto_with_neither_a_place_nor_a_point_is_refused(board, client):
    out = send(client, "goto")
    assert out["ok"] is False
    assert board.posted() == []
    assert send(client, "goto", x="three", y=2)["ok"] is False
    assert board.posted() == []


def test_a_named_place_still_wins_over_coordinates(board, client):
    """`place` is the destination the voice path can name, and the client
    cannot invent one — so when both are sent, the name is what is honoured
    and an unknown name is still a refusal, not a fall-through to the click."""
    out = send(client, "goto", place="ห้องที่ไม่มีจริง", x=3.5, y=2.25)
    assert out["ok"] is False
    assert board.posted() == []


# ==================== hold-to-drive ====================


def test_drive_sends_the_remote_control_action(board, client):
    assert send(client, "drive", direction="forward") == {"ok": True, "did": "drive"}
    payload = board.posted()[-1]
    assert payload["action_name"] == robot_chassis.MOVE_BY
    assert payload["options"] == {"direction": 0}
    assert send(client, "stop")["ok"] is True


def test_drive_needs_a_direction_the_board_knows(board, client):
    assert send(client, "drive", direction="diagonal")["ok"] is False
    assert send(client, "drive")["ok"] is False
    assert board.posted() == []


def test_drive_is_locked_like_every_other_move(board, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    out = send(client, "drive", direction="forward")
    assert out["ok"] is False and out["error"] == "motion_locked"
    assert board.posted() == []


def test_driving_at_a_wall_answers_with_a_shape_the_radar_can_show(board, client):
    board.front = 0.22
    out = send(client, "drive", direction="forward")
    assert out["ok"] is False
    assert out["error"] == "obstacle"
    assert out["blocked"] == {"direction": "forward", "distance": 0.22, "limit": 0.3}
    assert "หยุด" in out["hint"]
    assert board.posted() == []
    assert send(client, "drive", direction="left")["ok"] is True, "turning away is allowed"
    send(client, "stop")


def test_the_radar_carries_the_clearance_and_the_line(board, client):
    out = client.get("/robot/laserscan", params={"token": TOKEN}).json()
    assert out["clearance"]["front"] == 1.5
    assert out["min_clearance"] == 0.3


def test_relocalizing_on_the_dock_works_while_motion_is_locked(board, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    refused = send(client, "relocalize")
    assert refused["ok"] is False and "ไม่ได้อยู่บนแท่น" in refused["error"]
    board.docking = "on_dock"
    out = send(client, "relocalize")
    assert out["ok"] is True and out["did"] == "relocalize" and out["mode"] == "dock"
    assert out["pose_set_to_dock"]["x"] == -0.25
    assert board.posted()[-1]["action_name"] == robot_chassis.RECOVER_LOCALIZATION
    assert send(client, "relocalize", mode="rotate")["error"] == "motion_locked"


def test_saving_a_spot_works_while_locked_but_not_while_lost(board, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_chassis_motion_enabled", False)
    board.quality = 12
    lost = send(client, "save_poi", name="ที่ชาร์จ")
    assert lost["ok"] is False and "ปรับตำแหน่ง" in lost["error"]
    assert board.artifact_pois == []

    board.quality = 63
    out = send(client, "save_poi", name="ที่ชาร์จ")
    assert out["ok"] is True and out["did"] == "save_poi"
    assert out["name"] == "ที่ชาร์จ" and out["pose"] == {"x": 2.0, "y": 1.0, "yaw": 0.0}
    assert out["places"] == ["ที่ชาร์จ"]
    assert out["persisted"] is True
    assert send(client, "save_poi", name="")["ok"] is False
    assert send(client, "save_map") == {"ok": True, "did": "save_map", "saved": True, "map_bytes": 4}


def test_going_home_while_lost_is_refused_with_the_reason(board, client):
    board.quality = 5
    out = send(client, "home")
    assert out["ok"] is False and "ปรับตำแหน่ง" in out["error"]
    assert board.posted() == []
    board.quality = 71
    assert send(client, "home")["ok"] is True
    assert board.posted()[-1]["action_name"] == robot_chassis.GO_HOME


def test_stop_ends_a_held_drive(board, client):
    send(client, "drive", direction="right")
    send(client, "stop")
    assert ("DELETE", "/api/core/motion/v1/actions/:current", None) in board.calls
    assert robot_chassis._drive_task is None


# ==================== the radar ====================


def test_the_lidar_needs_the_token(board, client):
    assert client.get("/robot/laserscan").json() == {"ok": False, "error": "unauthorized"}
    assert board.calls == []


def test_the_lidar_frame_is_thinned_for_the_page_but_counted_whole(board, client):
    from app.main import _LASERSCAN_MAX_POINTS

    out = client.get("/robot/laserscan", params={"token": TOKEN}).json()
    assert out["ok"] is True
    assert out["total"] == 1586
    assert out["valid"] == 1586 - 159
    assert 0 < len(out["points"]) <= _LASERSCAN_MAX_POINTS
    assert set(out["points"][0]) == {"angle", "distance", "valid"}
    assert out["pose"] == {"x": 2.0, "y": 1.0, "yaw": 0.0}


def test_the_page_cannot_ask_for_more_lidar_than_the_cap(board, client):
    """`max` is a hint downward. A page asking for 100,000 points gets the cap,
    because the fetch crosses the relay a few times a second."""
    from app.main import _LASERSCAN_MAX_POINTS

    out = client.get("/robot/laserscan", params={"token": TOKEN, "max": 100000}).json()
    assert len(out["points"]) <= _LASERSCAN_MAX_POINTS
    fewer = client.get("/robot/laserscan", params={"token": TOKEN, "max": 50}).json()
    assert len(fewer["points"]) <= 50


# ==================== what the page itself promises ====================


def test_the_page_says_this_is_not_the_emergency_stop(source):
    """Software that promises to stop a machine is making a promise it cannot
    keep alone. The page has to say where the real button is, in the part
    nobody scrolls past."""
    assert "ปุ่มหยุดฉุกเฉิน" in source
    assert "ปุ่มจริงอยู่ที่ตัวหุ่น" in source


def test_stop_stays_on_screen(source):
    """Sticky, not somewhere down the page. The moment it is needed is the
    moment nobody is going to scroll for it."""
    assert "position: sticky" in source


def test_the_page_sends_intents_not_distances(source):
    """`action: 'forward'`, never `metres: 0.3` — so the server stays the only
    place a distance is decided."""
    import re

    body = re.search(r"body: JSON\.stringify\(\{([^}]*)\}\)", source)
    assert body, "the command body must be readable here"
    assert set(part.split(":")[0].strip() for part in body.group(1).split(",")) \
        == {"token", "action", "place"}
