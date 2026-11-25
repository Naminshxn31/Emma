"""A thing on the floor the lidar cannot see (owner 5 Nov, a photo: "ตอนหุ่นเดินสองรอบ โดนตรงนี้
ทำให้มีสะดุ้ง"). The 4 Nov walks put it at (-7.85, 0.80): driving straight at 0.3 m/s, the heading
kicked +19 then -22 degrees in two frames on both rounds. Neither the laser nor the map knows it,
so the event file does, and what may stand on or drive through it is held here.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

from app import event_host, map_check
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
ROUTES = ROOT / "data" / "robot" / "waypoints" / "floor1.json"
HALF_DIAGONAL_WITH_ARMS = math.hypot(0.35, 0.39)


@pytest.fixture
def event(tmp_path, monkeypatch):
    path = tmp_path / "floor1_event.json"
    path.write_text(TRACKED.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    event_host.reset()
    return path


def _tracked() -> dict:
    return json.loads(TRACKED.read_text(encoding="utf-8"))


def test_the_spot_is_in_the_file_with_room_for_the_whole_robot():
    hazards = _tracked()["floor_hazards"]
    bump = next(h for h in hazards if h["id"] == "lobby_floor_bump")
    assert (bump["x"], bump["y"]) == (-7.85, 0.8)
    assert bump["radius_m"] > HALF_DIAGONAL_WITH_ARMS


def _places(doc: dict) -> dict:
    """Where a scene can send her: a pose, or the end of its steps."""
    out = {}
    for sid, spot in doc["spots"].items():
        if spot.get("steps"):
            # a walk ahead (`forward`) taken its whole length along the last heading -- card 3's guest_front (22 Nov):
            # from the counter, turned round, up to 1.2 m on; its line from the counter is one of the pairs below
            x = y = face = None
            for st in spot["steps"]:
                if st.get("to"):
                    (x, y), face = st["to"], st.get("face", face)
                elif st.get("do") == "turn":
                    face = st["face"]
                elif "forward" in st:
                    x += st["forward"] * math.cos(math.radians(face))
                    y += st["forward"] * math.sin(math.radians(face))
            out[sid] = [x, y]
        elif spot.get("pose"):
            out[sid] = spot["pose"][:2]
    return out


def test_no_spot_stands_on_it_and_no_scene_walks_through_it(event):
    """The board plans straight when nothing is in the way, and nothing is, for it."""
    doc = _tracked()
    places = _places(doc)
    assert "entrance" in places and places["entrance"] != [-8.0, 0.6]    # 25 cm from the hit
    for sid, (x, y) in places.items():
        assert event_host.hazard_on_line(x, y, x, y) is None, sid
    for (a, pa), (b, pb) in itertools.combinations(places.items(), 2):
        assert event_host.hazard_on_line(*pa, *pb) is None, (a, b)


def test_it_catches_what_it_should_and_nothing_else(event):
    hit = event_host.hazard_on_line(-8.5, 1.0, -7.5, 0.5)       # the leg both rounds hit it on
    assert hit is not None and hit["id"] == "lobby_floor_bump"
    assert event_host.hazard_on_line(-8.0, 0.6, -8.0, 0.6) is not None
    assert event_host.hazard_on_line(-8.6, 0.2, -7.6, -0.6) is None
    assert event_host.hazard_on_line(-9.0, -5.2, -8.6, 0.2) is None
    # past the end of the line is not on it
    assert event_host.hazard_on_line(-10.0, 0.8, -9.0, 0.8) is None


def test_no_event_file_no_hazards():
    assert settings.event_file == ""
    assert event_host.hazard_on_line(-8.0, 0.6, -8.0, 0.6) is None


def test_a_broken_entry_does_not_take_the_event_down(event, caplog):
    doc = _tracked()
    doc["floor_hazards"].append({"id": "x", "y": "nope"})
    event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_host.reset()
    assert event_host.load() is not None
    assert [h["id"] for h in event_host.load()["hazards"]] == ["lobby_floor_bump"]
    assert "cannot be read" in caplog.text


def test_a_spot_cannot_be_recorded_on_it(event, monkeypatch):
    from app import robot_chassis

    at = {"x": -7.9, "y": 0.9}

    async def pose():
        return {**at, "yaw": 0.0}

    async def quality():
        return 72

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)
    out = asyncio.run(event_host.save_spot("table_1"))
    assert out["ok"] is False and out["error"] == "floor_hazard" and "สะดุด" in out["error_th"]
    assert event_host.load()["spots"]["table_1"]["pose"] is None
    at.update(x=-9.5, y=-2.0)
    assert asyncio.run(event_host.save_spot("table_1"))["ok"] is True


def test_walking_up_to_a_guest_does_not_cross_it(event):
    from app import face_approach as fa

    # a guest 3 m along +x, with the hazard on the straight line to them, nothing else near
    pose = {"x": -9.5, "y": 0.8, "yaw": 0.0}
    angles = np.radians(np.arange(-180, 180, 0.5))
    ranges = np.full(angles.shape, 8.0)
    pts = np.column_stack((ranges * np.cos(angles), ranges * np.sin(angles)))
    person = np.column_stack((np.full(12, 3.0), np.linspace(-0.12, 0.12, 12)))
    points = np.vstack((pts, person))
    area = [(-12.0, -3.0), (-5.0, -3.0), (-5.0, 4.0), (-12.0, 4.0)]
    planned = fa.plan(points, pose, 0.0, 3.0, area)
    assert planned["why"] == "floor_hazard" and "floor_hazard" in fa.REASONS_TH
    # the same walk, a metre and a half to the south: free
    clear = dict(pose, y=-0.9)
    assert fa.plan(points, clear, 0.0, 3.0, area)["why"] is None


@pytest.mark.skipif(not ROUTES.is_file(), reason="NOT CHECKED: floor1.json is gitignored and this machine has none")
def test_every_walked_route_keeps_off_it(event):
    doc = json.loads(ROUTES.read_text(encoding="utf-8"))
    for name in ("reception_tour", "event_rehearsal", "lobby_survey"):
        points = [s["to"] for s in doc["paths"][name]["steps"] if isinstance(s, dict) and "to" in s]
        assert len(points) >= 2, name
        for a, b in zip(points, points[1:]):
            assert event_host.hazard_on_line(*a, *b) is None, (name, a, b)


def test_the_clean_map_can_carry_what_the_laser_cannot_see(tmp_path):
    cells = np.full((100, 100), 127, dtype=np.int8)
    cells[0, 0] = -127
    grid = map_check.Grid(cells, -5.0, -5.0, 0.05, b"")
    out = map_check.blocked(grid, [(-2.5, -2.5, 0.2)])
    wx, wy = grid.world(*np.nonzero(out.occ()))
    far = np.hypot(wx + 2.5, wy + 2.5) > 0.25
    assert out.occ().sum() > 40 and far.sum() == 1          # the disc, and the one wall cell it had
    assert grid.cells[50, 50] == 127                         # the map it was given is left alone
    assert out.w == grid.w and out.ox == grid.ox and out.res == grid.res


def test_the_script_reads_the_mark():
    import importlib.util

    spec = importlib.util.spec_from_file_location("map_check_script", ROOT / "scripts" / "map_check.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script._block("-7.85,0.8") == (-7.85, 0.8, 0.2)
    assert script._block("-7.85,0.8,0.3") == (-7.85, 0.8, 0.3)
    with pytest.raises(ValueError):
        script._block("-7.85")
