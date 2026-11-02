"""Walk pictures (app/walk_frames.py) and the PC's contact sheet (scripts/walk_frames.py)."""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import pytest

from app import robot_camera, robot_chassis, walk_frames
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


def _script():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("walk_frames_script", ROOT / "scripts" / "walk_frames.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def frames_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(walk_frames, "FRAMES_DIR", tmp_path / "walk-frames")
    monkeypatch.setattr(robot_chassis, "configured", lambda: False)
    return tmp_path / "walk-frames"


def test_off_by_default_and_pinned_off_in_tests():
    assert settings.walk_frames is False
    walk_frames.start()
    assert walk_frames._task["task"] is None


def test_prune_by_name_age_and_size_and_leaves_other_folders(tmp_path):
    root = tmp_path / "wf"
    now = dt.datetime(2026, 11, 10, 12, 0, 0)
    old = root / "20261101-090000-app"
    mid = root / "20261108-090000-app"
    new = root / "20261110-110000-event-table_1"
    other = root / "notes"
    for p in (old, mid, new, other):
        p.mkdir(parents=True)
    (mid / "0000.jpg").write_bytes(b"x" * 700_000)
    (new / "0000.jpg").write_bytes(b"x" * 500_000)
    # the folder's NAME is its age, not its mtime (a copy rewrites mtimes)
    gone = walk_frames.prune(root, keep_days=7, max_mb=1, now=now)
    assert gone == ["20261101-090000-app", "20261108-090000-app"]
    assert new.is_dir() and other.is_dir() and not old.exists() and not mid.exists()


def test_snap_keeps_only_a_fresh_new_frame(frames_dir, monkeypatch):
    run = walk_frames._open("event-table_1")
    assert run.folder.parent == frames_dir and run.folder.name.endswith("-event-table_1")
    seq_at = {"v": (5, JPEG, time.monotonic())}
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: seq_at["v"])

    first = asyncio.run(walk_frames.snap(run))
    assert first["file"] == "0000.jpg" and (run.folder / "0000.jpg").read_bytes() == JPEG
    # the same frame again is not a new picture (a stalled camera)
    again = asyncio.run(walk_frames.snap(run))
    assert again["file"] is None
    # a stale frame is not a picture of now
    seq_at["v"] = (6, JPEG, time.monotonic() - 5)
    stale = asyncio.run(walk_frames.snap(run))
    assert stale["file"] is None
    rows = [json.loads(line) for line in (run.folder / "frames.jsonl").read_text("utf-8").splitlines()]
    assert [r["n"] for r in rows] == [0, 1, 2]
    assert run.pictures == 1 and run.no_camera == 2


def test_snap_records_the_board(frames_dir, monkeypatch):
    run = walk_frames._open("app")
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: (1, JPEG, time.monotonic()))
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)

    async def scan(max_points=0):
        pts = [{"angle": 0.0, "distance": 0.8, "valid": True}, {"angle": 1.57, "distance": 0.4, "valid": True},
               {"angle": -1.57, "distance": 9.0, "valid": False}]
        return {"pose": {"x": 1.0, "y": 2.0, "yaw": 0.1}, "points": pts, "total": 3, "valid": 2,
                "clearance": robot_chassis.clearance(pts)}

    async def quality():
        return 77

    monkeypatch.setattr(robot_chassis, "laserscan", scan)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)
    rec = asyncio.run(walk_frames.snap(run))
    assert rec["pose"] == {"x": 1.0, "y": 2.0, "yaw": 0.1} and rec["q"] == 77
    assert rec["clear"]["front"] == 0.8 and rec["clear"]["left"] == 0.4
    assert rec["scan"] == [[0.0, 0.8], [1.57, 0.4]]          # invalid returns are not kept


def test_a_walk_opens_a_run_and_its_end_closes_it_after_the_tail(frames_dir, monkeypatch):
    monkeypatch.setattr(walk_frames, "POLL_S", 0.01)
    monkeypatch.setattr(walk_frames, "TAIL_S", 0.05)
    monkeypatch.setattr(settings, "walk_frames_every_s", 0.2)
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: (int(time.monotonic() * 100), JPEG, time.monotonic()))
    state = {"walking": True}
    monkeypatch.setattr(walk_frames, "_label", lambda: "app" if state["walking"] else None)

    async def go():
        task = asyncio.create_task(walk_frames._watch())
        await asyncio.sleep(0.15)
        state["walking"] = False
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    runs = list(frames_dir.iterdir())
    assert len(runs) == 1
    doc = json.loads((runs[0] / "run.json").read_text("utf-8"))
    assert doc["label"] == "app" and doc["pictures"] >= 1


def test_label_names_the_event_spot_and_an_app_run(monkeypatch):
    from app import event_walk, robot_face_link

    class FakeRun:
        spot = "table_3"

    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_walk, "current", lambda: FakeRun())
    assert walk_frames._label() == "event-table_3"
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "active")
    assert walk_frames._label() == "app"
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "idle")
    assert walk_frames._label() is None
    # "unknown" is not a walk to photograph (no face page reporting)
    monkeypatch.setattr(robot_face_link, "nav_activity", lambda session=None: "unknown")
    assert walk_frames._label() is None


def test_sheet_puts_the_nearest_per_side_first(tmp_path):
    pytest.importorskip("PIL", reason="Pillow missing: the contact sheet is not checked")
    from PIL import Image

    mod = _script()
    folder = tmp_path / "20261110-110000-app"
    folder.mkdir()
    buf = folder / "0001.jpg"
    Image.new("RGB", (64, 48), (120, 100, 80)).save(buf)
    rows = [
        {"n": 0, "s": 0.0, "file": None, "q": 80, "pose": {"x": 0, "y": 0, "yaw": 0},
         "clear": {"front": 2.0, "left": 1.0, "right": 1.0, "back": None}, "scan": []},
        {"n": 1, "s": 1.0, "file": "0001.jpg", "q": 70, "pose": {"x": 0.5, "y": 0, "yaw": 0},
         "clear": {"front": 1.5, "left": 0.35, "right": 0.9, "back": None}, "scan": [[1.57, 0.35]]},
        {"n": 2, "s": 2.0, "file": None, "q": 75, "pose": {"x": 1.0, "y": 0.1, "yaw": 0},
         "clear": {"front": 0.7, "left": 0.8, "right": 0.45, "back": None}, "scan": []},
    ]
    (folder / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"half', "utf-8")
    near = mod.nearest(mod.load(folder))
    assert near["left"]["n"] == 1 and near["right"]["n"] == 2 and near["front"]["n"] == 2
    outs = mod.sheet(folder)
    assert outs and outs[0].is_file() and (folder / "path.png").is_file()
    text = (folder / "summary.txt").read_text("utf-8")
    assert "nearest left  0.35 m at #1" in text
    assert "nearest right 0.45 m at #2 (2.0 s) -- no picture" in text


def test_a_record_says_what_the_walk_said_and_where_the_head_looked(frames_dir, monkeypatch):
    from app import event_walk, head_follow

    run = walk_frames._open("event-table_3")
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: (1, JPEG, time.monotonic()))
    walk = event_walk.Run("table_3", "Table 3")
    walk.state = "walking"
    walk.tail.append("step 2/3 table_3 ... x 3.10 y -1.20")
    monkeypatch.setattr(event_walk, "current", lambda: walk)
    monkeypatch.setattr(head_follow, "offset_deg", lambda: (12.04, -3.0))
    rec = asyncio.run(walk_frames.snap(run))
    assert rec["walk"] == {"by": "event", "spot": "table_3", "state": "walking", "reason": None,
                           "step": "step 2/3 table_3 ... x 3.10 y -1.20"}
    assert rec["head"] == [12.0, -3.0]
    # it stopped: the run's end is that state, in run.json
    walk.state, walk.reason, walk.ended = "stopped", "staff_stop", time.monotonic()
    asyncio.run(walk_frames.snap(run))
    walk_frames._close(run)
    doc = json.loads((run.folder / "run.json").read_text("utf-8"))
    assert doc["end"]["state"] == "stopped" and doc["end"]["reason"] == "staff_stop"
    # a walk that ended long ago is not this one's
    walk.ended = time.monotonic() - 60
    assert walk_frames._walk_state() is None or walk_frames._walk_state()["by"] != "event"


def test_an_app_walk_is_described_from_the_faces_report_whitelisted(monkeypatch):
    from app import event_walk, robot_face_link

    monkeypatch.setattr(event_walk, "current", lambda: None)
    face = object()
    monkeypatch.setattr(robot_face_link, "walking_face", lambda: face)
    monkeypatch.setattr(robot_face_link, "nav_status", lambda s: {
        "type": "nav_status", "state": "running", "place": "big_room", "run_id": "a" * 32,
        "heard": "something a guest said", "step": 2})
    assert walk_frames._walk_state() == {"by": "app", "state": "running", "place": "big_room", "step": 2}


def test_the_sheet_shows_where_the_walk_changed_and_how_it_ended(tmp_path):
    pytest.importorskip("PIL", reason="Pillow missing: the contact sheet is not checked")
    mod = _script()
    folder = tmp_path / "20261110-110000-event-table_3"
    folder.mkdir()
    walking = {"by": "event", "spot": "table_3", "state": "walking", "reason": None, "step": "step 1/2"}
    stopped = dict(walking, state="stopped", reason="staff_stop", step="STOP: staff")
    rows = [
        {"n": 0, "s": 0.0, "file": None, "clear": {}, "walk": walking, "head": [0.0, 0.0]},
        {"n": 1, "s": 1.0, "file": None, "clear": {}, "walk": walking, "head": [0.0, 0.0]},
        {"n": 2, "s": 2.0, "file": None, "clear": {}, "walk": stopped, "head": [14.0, 0.0]},
    ]
    (folder / "frames.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    (folder / "run.json").write_text(json.dumps({"end": stopped}), "utf-8")
    assert mod.changes(rows) == {0: "-> walking", 2: "-> stopped"}
    mod.sheet(folder)
    text = (folder / "summary.txt").read_text("utf-8")
    assert "#2 2.0 s  table_3 stopped (staff_stop): STOP: staff" in text
    assert "ended: table_3 stopped (staff_stop): STOP: staff" in text
    assert "head turned on 1 records" in text


def test_the_lidar_strip_lays_what_the_lidar_saw_under_the_picture():
    mod = _script()
    # something 0.5 m away at +10 deg (the robot's left = the picture's left half),
    # a wall 3 m away straight ahead and nothing elsewhere
    scan = [[math.radians(a), 0.5] for a in (9.0, 10.0, 11.0)] + [[math.radians(a), 3.0] for a in (-1.0, 0.0, 1.0)]
    record = {"scan": scan, "head": [0.0, 0.0]}
    strip = mod.lidar_strip(record, 320, None)
    focal = 160 / math.tan(math.radians(35))
    col = int(round(160 - focal * math.tan(math.radians(10))))
    assert strip[col] == mod.STRIP_STEPS[0][1], "near = red, at its column"
    assert strip[160] == mod.STRIP_FAR
    assert strip[319] == mod.STRIP_NONE, "nothing seen is not 'clear'"
    # the head turned 10 deg left: the same thing is now straight ahead in the picture
    assert mod.lidar_strip(dict(record, head=[10.0, 0.0]), 320, None)[160] == mod.STRIP_STEPS[0][1]
    # a checked lidar that counts the other way round: it lands on the other side
    mirrored = [[-a, d] for a, d in scan]
    flipped = mod.lidar_strip({"scan": mirrored, "head": [0.0, 0.0]}, 320,
                              {"hfov_assumed": 70.0, "lidar_a": -1.0, "lidar_b": 0.0})
    assert flipped[col] == mod.STRIP_STEPS[0][1]
    assert mod.lidar_strip({"scan": []}, 10, None) == [None] * 10


def test_a_walk_keeps_the_boards_map_once(frames_dir, monkeypatch):
    """31 Oct, round 4: map.bin is what scripts/map_check.py lays the walk's
    lidar on -- read once per walk, and a cut-off read is no map."""
    import struct

    grid = struct.pack("<ffIIf", -1.0, -1.0, 4, 3, 0.05) + b"\0" * 12 + struct.pack("<I", 12) + bytes(range(12))
    reads = []

    async def raw(method, path):
        reads.append(path)
        return grid if len(reads) == 1 else grid[:-3]

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "request_raw", raw)
    run = walk_frames._open("app")
    asyncio.run(walk_frames.keep_map(run))
    assert reads == [robot_chassis.MAP_EXPLORE]
    assert (run.folder / "map.bin").read_bytes() == grid and run.map == "map.bin"
    walk_frames._close(run)
    assert json.loads((run.folder / "run.json").read_text("utf-8"))["map"] == "map.bin"

    cut = walk_frames._open("app2")
    asyncio.run(walk_frames.keep_map(cut))
    assert not (cut.folder / "map.bin").exists() and cut.map is None


def test_a_walk_starts_recording_without_waiting_for_the_map(frames_dir, monkeypatch):
    monkeypatch.setattr(walk_frames, "POLL_S", 0.01)
    monkeypatch.setattr(settings, "walk_frames_every_s", 0.2)
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: (int(time.monotonic() * 100), JPEG, time.monotonic()))
    monkeypatch.setattr(walk_frames, "_label", lambda: "app")
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)

    asked = []

    async def slow(method, path):
        asked.append(path)
        await asyncio.sleep(10)

    async def scan(max_points=0):
        raise OSError("no lidar in this test")

    async def quality():
        return None

    monkeypatch.setattr(robot_chassis, "request_raw", slow)
    monkeypatch.setattr(robot_chassis, "laserscan", scan)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)

    async def go():
        task = asyncio.create_task(walk_frames._watch())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    run = next(frames_dir.iterdir())
    assert asked == [robot_chassis.MAP_EXPLORE]                       # the walk did ask for its map
    assert (run / "frames.jsonl").read_text("utf-8").strip()        # pictures while the map is still coming


def test_pull_last_n_pulls_the_newest_n_runs(monkeypatch, tmp_path):
    """map_check needs two walks or more: `pull --last 4` brings four."""
    wf_script = _script()

    names = ["20261031-100000-app", "20261031-110000-app", "20261031-120000-app"]
    pulled = []
    monkeypatch.setattr(wf_script, "_robot", lambda serial: object())
    monkeypatch.setattr(wf_script, "runs", lambda robot: names)
    monkeypatch.setattr(wf_script, "pull", lambda robot, name: pulled.append(name) or tmp_path / name)
    monkeypatch.setattr(wf_script, "sheet", lambda folder: [])
    assert wf_script.main(["pull", "--last", "2"]) == 0
    assert pulled == names[-2:]
    pulled.clear()
    assert wf_script.main(["pull", "--last"]) == 0
    assert pulled == names[-1:]


def test_motion_needs_the_base_to_keep_moving_and_ends_after_it_stays_still():
    """2 Nov: a walk nobody reports (board_walk by hand over adb, driving by
    hand) is told from the board's pose. Dock jitter and one relocalization
    jump are not a walk; a pause at a point does not split one."""
    m = walk_frames.Motion()
    t = 0.0
    for i in range(20):                                   # on the dock: millimetres
        m.update({"x": 1.0 + 0.002 * (i % 2), "y": 2.0, "yaw": 0.001 * (i % 2)}, t)
        t += 1
    assert not m.active(t) and m.state(t) is None
    m.update({"x": 1.5, "y": 2.0, "yaw": 0.0}, t)         # the board relocalized: one jump
    m.update({"x": 1.5, "y": 2.0, "yaw": 0.0}, t + 1)
    assert not m.active(t + 1)
    t += 2
    for i in range(3):                                    # leaving: 0.3 m/s
        m.update({"x": 1.5 + 0.3 * (i + 1), "y": 2.0, "yaw": 0.0}, t + i)
    t += 2
    assert m.active(t) and m.state(t) == {"by": "motion", "state": "moving"}
    m.update({"x": 2.4, "y": 2.0, "yaw": 0.0}, t + 8)     # board_walk's 8 s wait at the door
    assert m.active(t + 8) and m.state(t + 8)["state"] == "still"
    m.update({"x": 2.4, "y": 2.0, "yaw": math.radians(10)}, t + 9)   # turning on the spot is moving
    assert m.active(t + 9 + walk_frames.MOTION_STILL_S - 1)
    assert not m.active(t + 9 + walk_frames.MOTION_STILL_S + 0.1)
    m.update(None, t + 30)                                # no answer: no claim
    assert m.pose is None and m.streak == 0


def test_a_walk_nobody_reports_is_recorded_from_the_base_moving(frames_dir, monkeypatch):
    monkeypatch.setattr(walk_frames, "POLL_S", 0.01)
    monkeypatch.setattr(walk_frames, "MOTION_POLL_S", 0.01)
    monkeypatch.setattr(walk_frames, "MOTION_STILL_S", 0.1)
    monkeypatch.setattr(walk_frames, "TAIL_S", 0.05)
    monkeypatch.setattr(settings, "walk_frames_every_s", 0.2)
    monkeypatch.setattr(robot_camera.feed, "latest", lambda: (int(time.monotonic() * 100), JPEG, time.monotonic()))
    monkeypatch.setattr(walk_frames, "_label", lambda: None)          # no app run, no event walk
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    state = {"x": 0.0, "driving": True}

    async def pose():
        if state["driving"]:
            state["x"] += 0.05
        return {"x": state["x"], "y": 0.0, "yaw": 0.0}

    async def scan(max_points=0):
        raise OSError("no lidar in this test")

    async def quality():
        return 70

    async def no_map(method, path):
        return None

    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "laserscan", scan)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)
    monkeypatch.setattr(robot_chassis, "request_raw", no_map)

    async def go():
        task = asyncio.create_task(walk_frames._watch())
        await asyncio.sleep(0.4)
        state["driving"] = False
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    runs = list(frames_dir.iterdir())
    assert len(runs) == 1 and runs[0].name.endswith("-moving")
    doc = json.loads((runs[0] / "run.json").read_text("utf-8"))
    assert doc["label"] == "moving" and doc["pictures"] >= 1
    rows = [json.loads(line) for line in (runs[0] / "frames.jsonl").read_text("utf-8").splitlines()]
    assert rows[0]["walk"] == {"by": "motion", "state": "moving"}


def test_a_still_robot_with_nothing_reported_records_nothing(frames_dir, monkeypatch):
    monkeypatch.setattr(walk_frames, "POLL_S", 0.01)
    monkeypatch.setattr(walk_frames, "MOTION_POLL_S", 0.01)
    monkeypatch.setattr(walk_frames, "_label", lambda: None)
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)

    async def pose():
        return {"x": 1.0, "y": 1.0, "yaw": 0.5}

    monkeypatch.setattr(robot_chassis, "pose", pose)

    async def go():
        task = asyncio.create_task(walk_frames._watch())
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert not frames_dir.exists() or not list(frames_dir.iterdir())
