"""Floor-1 remembered paths (scripts/board_walk.py, data/robot/waypoints/floor1.json)."""
import importlib.util
import json
import math
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTE = ROOT / "data" / "robot" / "waypoints" / "floor1.json"


def _walk():
    spec = importlib.util.spec_from_file_location("board_walk", ROOT / "scripts" / "board_walk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sim():
    spec = importlib.util.spec_from_file_location("walk_sim", ROOT / "scripts" / "walk_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _passage():
    """A line due north (x = 0, y 0..4.6) between walls at x = +-1.2, chair
    legs as posts at x = +-0.50 for y 2.4..3.4 (1.0 m: 15 cm a side for the
    0.70 m base) and a door frame 0.86 m wide at y 4.0..4.1 -- the shape of
    the way out of the multimedia room, simplified."""
    import numpy as np

    pts = []
    for y in np.arange(-0.5, 5.0, 0.03):
        pts += [(-1.2, y), (1.2, y)]
    for y in np.arange(2.4, 3.41, 0.25):
        for dx in (0, 0.03):
            pts += [(-0.50 - dx, y), (0.50 + dx, y), (-0.50 - dx, y + 0.03), (0.50 + dx, y + 0.03)]
    for y in np.arange(4.0, 4.11, 0.02):
        pts += [(x, y) for x in np.arange(-1.2, -0.43, 0.02)] + [(x, y) for x in np.arange(0.43, 1.21, 0.02)]
    return np.array(pts)


PASSAGE_LINE = {"creep": [[0.0, 0.0, 0.2], [0.0, 2.2, 0.15], [0.0, 4.6]], "label": "sim"}


def _drive_passage(bw, ws, *, fit=True, board_off=(0.0, 0.0), **kw):
    """The passage line on the simulated base; the robot starts where the
    board's pose puts it on the line's first point (as after a MoveTo)."""
    env = _passage()
    base = ws.SimBase((-board_off[0], -board_off[1], math.pi / 2), env, board_off=board_off, **kw)
    res = ws.run_line(bw, PASSAGE_LINE, base, fit=bw.MapFit(ws.explore_blob(env)) if fit else None)
    res["x_in_gap"] = max((abs(p[0]) for p in base.path if 2.3 < p[1] < 3.5), default=None)
    return res


def test_every_path_is_well_formed():
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    assert len(doc["dock"]["pose"]) == 3
    assert {"dock_to_multimedia", "multimedia_to_dock", "reception_tour"} <= set(doc["paths"])
    for name, path in doc["paths"].items():
        for step in path["steps"]:
            if "do" in step:
                assert step["do"] in {"undock", "dock", "screen", "wait", "turn"}, (name, step)
                if step["do"] == "screen":
                    assert step["action"] in {"open", "close"}
                continue
            if "back_to" in step:
                # 16 Nov, the moved dock: home facing out, reversing along the dock's own line
                # (rehearsal_back_home) -- onto the file's dock pose and nothing else
                assert list(step["back_to"]) == list(doc["dock"]["pose"]), (name, step)
                continue
            if "forward" in step:
                # 20 Nov, the counter walk (event_counter_test): the debut's straight walk out of the
                # dock, as app/event_host.py `_walk_ahead` writes it -- no map point to check
                assert 0 < step["forward"] <= 3.0 and 0.05 <= step.get("speed", 0.15) <= 0.25, (name, step)
                assert 0 <= step.get("right_clear_m", 0) <= 0.5, (name, step)
                continue
            for x, y in [q[:2] for q in step["creep"]] if "creep" in step else [step["to"]]:
                assert -26 < x < 18 and -16 < y < 9, (name, step)    # inside the floor-1 map grid
            if "face" in step:
                assert -180 <= step["face"] <= 180
            if "speed" in step:
                assert 0.1 <= step["speed"] <= 0.5


def test_a_path_from_the_dock_undocks_first_and_a_path_home_ends_docked():
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    assert doc["paths"]["dock_to_multimedia"]["steps"][0] == {"do": "undock"}
    for name in ("multimedia_to_dock", "reception_tour", "to_dock"):
        steps = [s_ for s_ in doc["paths"][name]["steps"] if s_.get("do") not in ("screen", "wait")]
        assert steps[-1] == {"do": "dock"}
        assert all(s_.get("do") != "dock" for s_ in steps[:-1])


def test_the_lobby_survey_looks_all_round_at_every_stop_and_ends_docked():
    """4 Nov night (owner: "พยายามให้หุ่นเดินทั่ว lobby นะ ... แสกนทั้ง lobby โดยละเอียด"):
    eight stops over the lobby, each a full round in 20-degree turns (the head
    camera is 19 deg wide) with a hold after every one; out of the dock first,
    docked last, never into the multimedia room or its screen."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    steps = doc["paths"]["lobby_survey"]["steps"]
    assert steps[0] == {"do": "undock"} and steps[-1] == {"do": "dock"}
    assert not any(s_.get("do") == "screen" or "creep" in s_ for s_ in steps)
    # a `to` with no round after it is the way round something (the floor bump, 5 Nov)
    stops = [i for i, s_ in enumerate(steps) if "to" in s_ and steps[i + 1].get("do") == "turn"]
    assert len(stops) == 8
    ways = [s_ for i, s_ in enumerate(steps) if "to" in s_ and i not in stops]
    assert [s_["to"] for s_ in ways] == [[-8.6, 0.2]]
    stops_and_ways = [i for i, s_ in enumerate(steps) if "to" in s_]
    for n, i in enumerate(stops):
        later = [k for k in stops_and_ways if k > i]
        end = later[0] if later else len(steps) - 1
        turns = [s_["face"] for s_ in steps[i + 1:end] if s_.get("do") == "turn"]
        assert sorted(f % 360 for f in turns) == list(range(0, 360, 20)), steps[i]["label"]
        body = steps[i + 1:end]
        assert all(body[k].get("do") == "turn" and body[k + 1].get("do") == "wait" for k in range(0, len(body), 2))
        x, y = steps[i]["to"]
        assert -11.5 <= x <= -7.0 and -6.5 <= y <= 1.5, "inside the lobby, off its walls"
        assert steps[i]["speed"] <= 0.35


def test_the_room_is_only_its_doorway():
    """Owner 28 ต.ค., after the test line to the big screen caught on chairs'
    wheels (below the lidar): "ไม่ต้องเดินห้องนั้น ... เอาที่เดินเข้าออกพอ".
    The multimedia room is walked through its door and no deeper than just
    past it; the door itself is a creep line (the board will not take a
    target within ~0.55 m of anything, and the door leaves 0.50)."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    # (the big screen is its own path since the owner asked for it again the same
    # evening -- test_the_big_screen_path_keeps_off_the_table_side_chairs)
    for name in ("dock_to_multimedia", "multimedia_to_dock"):
        steps = doc["paths"][name]["steps"]
        creeps = [s_ for s_ in steps if "creep" in s_]
        assert len(creeps) == 1, name
        line = creeps[0]["creep"]
        assert any(y > -7.6 for _, y in line) and any(y < -9.0 for _, y in line)   # through the door
        assert all(y > -10.8 for _, y in line), line                                   # not deep into the room
        for a_, b_ in zip(line, line[1:]):
            assert math.dist(a_, b_) <= 0.95, (name, a_, b_)
        for s_ in steps:
            if "to" in s_:
                assert s_["to"][1] > -8.0, (name, s_)      # every board target is outside the room
    assert doc["paths"]["dock_to_multimedia"]["steps"][-1]["creep"][-1] == doc["places"]["multimedia_inside_door"]["pose"][:2]


def test_a_creep_line_starts_where_the_board_leg_before_it_ends():
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    for name, path in doc["paths"].items():
        # the screen and a wait do not move the robot
        steps = [s_ for s_ in path["steps"] if s_.get("do") not in ("screen", "wait")]
        for prev, step in zip(steps, steps[1:]):
            if "creep" in step:
                assert prev.get("to") == step["creep"][0][:2], (name, prev, step["creep"][0])


def test_pre_dock_spot_is_out_along_the_dock_heading():
    bw = _walk()
    doc = {"dock": {"pose": [-6.86, -5.64, 180]}}
    x, y, yaw = bw.pre_dock(doc)
    assert math.isclose(x, -6.86 - bw.PREDOCK_M, abs_tol=1e-9)
    assert math.isclose(y, -5.64, abs_tol=1e-9)
    assert math.isclose(yaw, math.pi)


def test_backing_in_steers_back_onto_the_line():
    bw = _walk()
    vx, w = bw.back_in_command(out=0.3, lat=0.03, herr=0.0)
    assert vx < 0                                   # reversing
    assert w > 0                                    # left of the line -> nose turns left
    _, w = bw.back_in_command(out=0.3, lat=-0.03, herr=0.0)
    assert w < 0
    _, w = bw.back_in_command(out=0.3, lat=0.0, herr=math.radians(3))
    assert w < 0                                    # heading drifted left -> turn back right
    slow, _ = bw.back_in_command(out=0.05, lat=0.0, herr=0.0)
    assert -bw.BACK_SPEED < slow < 0                # slower over the last 10 cm


def test_the_creep_drives_with_the_floor2_doorway_limits():
    """The base ignores small turn rates (0.06-0.10 rad/s turned it 0° on the
    spot, 6 ต.ค.): a hand-written controller stalled 8° short of a turn on
    28 ต.ค. The creep uses robot_lanes' lane_command/turn_command with the
    doorway limits measured on this robot."""
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    assert "rl.lane_command(" in src and "rl.DOOR_LIMITS" in src and "rl.turn_command(" in src
    bw = _walk()
    assert bw.CREEP_HALF_WIDTH >= 0.35                # the base, not the board's 0.55 m disc


def test_a_stopped_line_is_joined_where_the_robot_stands():
    bw = _walk()
    line = [(0.0, 0.0), (0.0, 1.0), (0.0, 2.0), (1.0, 2.0)]
    assert bw.join_line(line, (0.0, 0.0)) == (0, 0.0)
    k, d = bw.join_line(line, (0.05, 1.5))
    assert k == 1 and abs(d - 0.05) < 1e-9
    k, _ = bw.join_line(line, (0.0, 2.0))         # at a vertex: the segment ahead
    assert k == 2
    _, d = bw.join_line(line, (0.6, 0.5))
    assert d > 0.5


def test_docking_presses_onto_the_springs_and_checks_it_stayed():
    """28 ต.ค.: stopped at the first on_dock, the robot read not_on_dock 1.5 s
    later and never charged. The reverse goes on SEAT_M past the contact and
    the dock counts only if it is still on it SETTLE_S later."""
    bw = _walk()
    assert 0.005 <= bw.SEAT_M <= bw.OVERSHOOT_M
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    assert "self._seat(v, x, y)" in src and "self._stays_docked(" in src


def test_the_tour_opens_the_screen_at_the_room_front_and_closes_it_docked():
    """Owner 28 ต.ค.: "ไปหน้าห้องอันนั้นด้วย ยังไม่ต้องเข้า พอไปถึงหน้าห้องก็เปิดจอ
    แล้วก็เดินกลับแท่น แล้วก็ปิดจอ"."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    door = doc["places"]["multimedia_door"]["pose"]
    for name in ("reception_tour", "room_front_and_back"):
        steps = doc["paths"][name]["steps"]
        i_open = steps.index({"do": "screen", "action": "open"})
        # "ตอนอยู่หน้าห้องไม่ตรงทางเข้านะ": the screen opens with the robot in line
        # with the door, facing into the room -- not beside it. One MoveTo
        # ("ลื่นสมูทขึ้น"), and the three-part way that ran five times if the
        # board will not take the target.
        front = steps[i_open - 1]
        assert front["to"] == door[:2] and front["face"] == door[2]
        turn, creep = front["fallback"][-1], front["fallback"][-2]
        assert turn["do"] == "turn" and turn["face"] == door[2]
        assert -12.0 <= creep["creep"][-1][0] <= -11.7 and -12.0 <= door[0] <= -11.7   # the opening, x -12.45..-11.55
        assert steps[-1] == {"do": "screen", "action": "close"} and steps[-2] == {"do": "dock"}
        assert all(s_["to"][1] > -8.0 for s_ in steps if "to" in s_)      # never into the room


def test_the_token_is_read_from_the_env_file(tmp_path):
    bw = _walk()
    env = tmp_path / ".env"
    env.write_text('GEMINI_API_KEY=x\nWS_TOKEN="abc123"\n', encoding="utf-8")
    assert bw.ws_token(env) == "abc123"


def test_plain_points_are_rolled_through_not_stopped_at():
    """Owner 28 ต.ค. "ปรับให้ลื่นสมูทขึ้น": a point followed by another point is
    handed over before it is reached; one that sets a heading, or comes before
    a creep line or a dock, is a real stop."""
    bw = _walk()
    assert bw.rolls_through({"to": [0, 0]}, {"to": [1, 0]})
    assert bw.rolls_through({"to": [0, 0]}, {"to": [1, 0], "face": 90})
    assert not bw.rolls_through({"to": [0, 0], "face": 90}, {"to": [1, 0]})
    assert not bw.rolls_through({"to": [0, 0]}, {"creep": [[0, 0], [1, 0]]})
    assert not bw.rolls_through({"to": [0, 0]}, {"do": "dock"})
    assert not bw.rolls_through({"to": [0, 0]}, None)
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    tour = doc["paths"]["reception_tour"]["steps"]
    rolled = sum(bw.rolls_through(s, n) for s, n in zip(tour, tour[1:] + [None]))
    assert rolled >= 5                       # the reception points are no longer five stops


def test_backing_in_is_quicker_far_from_the_dock_only():
    bw = _walk()
    far, _ = bw.back_in_command(out=0.40, lat=0.0, herr=0.0)
    mid, _ = bw.back_in_command(out=0.20, lat=0.0, herr=0.0)
    near, _ = bw.back_in_command(out=0.05, lat=0.0, herr=0.0)
    assert far == -bw.BACK_FAST_SPEED and mid == -bw.BACK_SPEED and -bw.BACK_SPEED < near < 0
    assert bw.DOCK_TRIES >= 2


def test_a_side_closing_in_moves_the_line_away_and_only_then():
    bw = _walk()
    assert bw.side_nudge(None, None) == 0.0
    assert bw.side_nudge(0.30, 0.20) == 0.0                      # room both sides: the line as chosen
    assert bw.side_nudge(0.02, 0.30) < 0                         # left too close -> move right
    assert bw.side_nudge(0.30, 0.02) > 0
    assert abs(bw.side_nudge(-0.5, 0.30)) <= bw.NUDGE_MAX_M


def test_the_big_screen_path_keeps_off_the_table_side_chairs():
    """Owner 28 ต.ค. ค่ำ: into the room to the big screen ('จุดที่บอกแล้ว') and out
    again. Beside the table the line keeps to the round chairs' side (the
    table chairs' wheels are below the lidar and caught the robot twice)."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    steps = doc["paths"]["room_tv_and_back"]["steps"]
    tv = doc["places"]["multimedia_tv"]["pose"]
    assert any(s_.get("to") == tv[:2] and s_.get("face") == tv[2] for s_ in steps)
    lines = [[q[:2] for q in s_["creep"]] for s_ in steps if "creep" in s_]
    # out the way it came in, from the screen itself: the board would not take a
    # target out of the screen's alcove (CANNOT_REACH_TARGET twice, 28 ต.ค.)
    # (the alcove ends differ: in, the line stops on the table-side straight and the
    # board takes the robot into the alcove; out, it bends gently from the screen)
    room = lambda ln: [q for q in ln if q[1] > -12.0]
    assert len(lines) == 2 and lines[1][0] == tv[:2] and room(lines[1]) == list(reversed(room(lines[0])))
    beside_table = [p for p in lines[0] if -11.9 <= p[1] <= -11.1]
    assert beside_table and all(x <= -12.45 for x, _ in beside_table)
    assert steps[-2:] == [{"do": "dock"}, {"do": "screen", "action": "close"}]


def test_open_stretches_of_a_line_run_faster_and_narrow_ones_do_not():
    """28 ต.ค. exit trace: 58 s for the line, the open middle of the room at the
    doorway's 0.15 throughout ("เร็ว แม่นยำมากขึ้น"). A point's third number is
    the speed of the segment starting there; the gap and the door frame stay
    at 0.15, nothing above CREEP_SPEED_MAX."""
    bw = _walk()
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    for s_ in doc["paths"]["room_tv_and_back"]["steps"]:
        if "creep" not in s_:
            continue
        for p_, q_ in zip(s_["creep"], s_["creep"][1:]):
            speed = p_[2] if len(p_) > 2 else bw.CREEP_SPEED
            assert speed <= bw.CREEP_SPEED_MAX
            if min(p_[1], q_[1]) < -10.55 and max(p_[1], q_[1]) > -11.3:     # the gap (legs at y -10.6..-10.9)
                assert speed <= 0.15, (p_, q_)
            if max(p_[1], q_[1]) > -8.6 and min(p_[1], q_[1]) < -7.9:        # the door frame
                assert speed <= 0.15, (p_, q_)
        assert any(len(p_) > 2 and p_[2] > bw.CREEP_SPEED for p_ in s_["creep"])


def test_the_go_and_return_halves_are_the_round_trip_without_its_wait():
    """Owner 28 ต.ค.: to the big screen, people talk with Emma as long as they
    like, back on word -- the round trip split at its wait."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    full = doc["paths"]["room_tv_and_back"]["steps"]
    go, back = doc["paths"]["room_tv_go"]["steps"], doc["paths"]["room_tv_return"]["steps"]
    assert go + back == [s_ for s_ in full if s_.get("do") != "wait"]
    assert go[0] == {"do": "undock"} and go[-1].get("face") == doc["places"]["multimedia_tv"]["pose"][2]
    assert back[-2:] == [{"do": "dock"}, {"do": "screen", "action": "close"}]


def test_a_pose_correction_is_eased_in_not_kept(monkeypatch):
    """back1 (28-29 ต.ค.): leaving the screen's alcove the board corrected its
    pose four times (0.10/0.21/0.17/0.11 m); the creep kept each jump as an
    offset for good and followed a frame ~0.2 m east of the real one into the
    door leaf (the lidar frame at the stop matched the map at the BOARD's
    pose). Here the board starts 0.2 m off and corrects itself after 0.3 and
    0.8 m: eased in, the robot is back on the line before the chair legs."""
    bw, ws = _walk(), _sim()
    fixes = [(0.3, 0.5), (0.8, 1.0)]
    res = _drive_passage(bw, ws, board_off=(-0.2, 0.0), corrections=fixes)
    assert res["end"] == "done", res["reason"]
    assert res["jumps"] == 2 and res["x_in_gap"] < 0.03
    assert res["min_base_gap"] > 0.03
    assert max(abs(c) for c in res["corr_end"]) < 0.01            # on the board's pose again
    # the old way (the offset kept: nothing eased off) is the back1 failure
    monkeypatch.setattr(bw, "POSE_BLEND_PER_M", 0.0)
    old = _drive_passage(bw, ws, board_off=(-0.2, 0.0), corrections=fixes)
    assert old["end"] == "stop" and abs(old["corr_end"][0]) > 0.15


def test_a_jump_the_lidar_contradicts_is_not_followed():
    """The broken door map (28 ต.ค.): the board jumped 0.2 m the wrong way.
    The lidar against the board's map tells the two poses apart (MapFit), so
    that jump is held as an offset -- and only that one."""
    bw, ws = _walk(), _sim()
    res = _drive_passage(bw, ws, wrong_jumps=[(1.0, 0.2, 0.0)])
    assert res["end"] == "done", res["reason"]
    assert res["x_in_gap"] < 0.03
    assert any("keeping to it" in m for m in res["said"])
    blind = _drive_passage(bw, ws, fit=False, wrong_jumps=[(1.0, 0.2, 0.0)])
    assert blind["end"] == "stop" or blind["x_in_gap"] > 0.1     # without the map it would follow the board


def test_a_jump_no_base_could_make_stops_the_line():
    """28 ต.ค., stuck on chair wheels: the board's pose went 0.6 m north in one
    tick while the robot stood. No correction is that big -- stop."""
    bw, ws = _walk(), _sim()
    res = _drive_passage(bw, ws, wrong_jumps=[(1.0, 0.0, 0.6)])
    assert res["end"] == "stop" and "not a correction" in res["reason"]


def test_held_up_at_a_crawl_stops_after_the_hold_time_not_the_line_limit(monkeypatch):
    """back1: the door leaf's tip 0.14-0.3 m ahead, 0.06-0.08 m/s asked, the
    base held itself to ~0.005 m/s for 83 s -- the stall check wants >= 0.08
    asked and the hold timer wanted 0 asked, so only the 120 s line limit
    ended it. Whatever is asked, no progress for CREEP_HOLD_S is held up."""
    import numpy as np

    bw, ws = _walk(), _sim()
    post = [(x, y) for x in np.arange(-0.10, 0.11, 0.02) for y in (1.2, 1.22)]
    base = ws.SimBase((0.0, 0.0, math.pi / 2), np.array(post), noise_m=0.0)
    res = ws.run_line(bw, {"creep": [[0.0, 0.0], [0.0, 3.0]]}, base)
    assert res["end"] == "stop" and "held up" in res["reason"], res["reason"]
    assert res["s"] < bw.CREEP_HOLD_S + 15
    monkeypatch.setattr(bw, "PROGRESS_M", 0.0)                     # without the watchdog
    monkeypatch.setattr(bw, "CREEP_MAX_S", 60.0)
    base = ws.SimBase((0.0, 0.0, math.pi / 2), np.array(post), noise_m=0.0)
    old = ws.run_line(bw, {"creep": [[0.0, 0.0], [0.0, 3.0]]}, base)
    assert "60 s on the line" in old["reason"]


def test_a_pose_correction_is_not_eased_in_while_the_robot_is_held():
    """An offset is worked off per metre the robot really drives, not per
    metre asked: held right after the board corrected itself (asked 0.06,
    moving ~3 mm/s), easing on the asked speed swung the steered pose 0.15 m
    off the line in place ("off the line" stop) -- held is the watchdog's.
    The stall and progress checks read the board's motion with the jumps
    taken out (PoseFilter.odometry), not the steered pose."""
    import numpy as np

    bw, ws = _walk(), _sim()
    post = [(x, y) for x in np.arange(-0.10, 0.31, 0.02) for y in (1.5, 1.52)]
    base = ws.SimBase((0.2, 0.0, math.pi / 2), np.array(post), board_off=(-0.2, 0.0), corrections=[(0.75, 1.0)])
    res = ws.run_line(bw, {"creep": [[0.0, 0.0], [0.0, 3.0]]}, base)
    assert res["jumps"] == 1
    assert res["end"] == "stop" and "held up" in res["reason"], res["reason"]
    assert res["s"] < bw.CREEP_HOLD_S + 20



def test_held_crooked_on_the_line_turns_onto_it_instead_of_calling_it_caught(monkeypatch):
    """go1 / cyc1 / tv1c / tv1d (28 ต.ค.) at the gap's exit: heading 10-15° off
    the line, the round chair's backrest at the strip's edge 0.3 m ahead, the
    base holding itself. tv1c/tv1d stopped as "caught on a chair's wheels"
    (it was not: something was in sight ahead), go1 stood 7 s, cyc1 14 s.
    Held with the heading off: turn onto the line, then go on."""
    import numpy as np

    bw, ws = _walk(), _sim()
    # a chair's edge 0.44 m beside the line (9 cm from the base on it), 0.33 m ahead
    # of the front face and inside the strip of a robot heading 11° toward it; a
    # base that will not steer while it holds (the first gap trace, 9 s)
    post = np.array([(-0.44 + dx, 0.55 + dy) for dx in (0, -0.02, -0.04) for dy in (0, 0.02, 0.04)])
    line = {"creep": [[0.0, 0.0], [0.0, 2.0]]}

    def base():
        return ws.SimBase((0.0, 0.0, math.radians(101)), post, noise_m=0.0, held_steer=0.0)

    res = ws.run_line(bw, line, base())
    assert res["end"] == "done", res["reason"]
    assert res["spins"] == 1 and any("onto the line" in m for m in res["said"])
    assert res["min_base_gap"] > 0.05
    # the stall rule as it was (held or not, 0.08+ asked and still = caught)
    monkeypatch.setattr(bw, "HOLD_ZONE_M", 0.0)
    old = ws.run_line(bw, line, base())
    assert old["end"] == "stop" and "below the lidar" in old["reason"]


def test_the_map_fit_tells_a_pose_from_one_a_hand_away():
    import numpy as np

    bw, ws = _walk(), _sim()
    env = _passage()
    fit = bw.MapFit(ws.explore_blob(env))
    base = ws.SimBase((0.0, 2.0, math.pi / 2), env)
    scan = base.lidar()
    here = fit.score((0.0, 2.0, math.pi / 2), scan)
    assert here > 0.9
    assert fit.score((0.12, 2.0, math.pi / 2), scan) < here - 0.3
    assert fit.score((0.0, 2.0, math.pi / 2 + math.radians(4)), scan) < here - 0.2
    assert fit.score((0.0, 2.0, math.pi / 2), np.zeros((0, 2))) == 0.0


def test_the_creep_trace_says_what_the_board_said_and_what_was_steered_by():
    """Next time a run is read back, the board's own pose and the offset are
    in every trace row (back1's had only the steered pose -- the board's had
    to be recovered from the next run's first line)."""
    bw, ws = _walk(), _sim()
    env = _passage()
    base = ws.SimBase((0.0, 0.0, math.pi / 2), env)
    line = bw.CreepLine(PASSAGE_LINE, 1, base.board_pose(), 0.0)
    act = line.tick(0.1, base.board_pose(), base.lidar())
    assert act.kind == "drive"
    line.sent(act, False)
    row = line.last_row
    assert {"raw", "corr", "pose", "renewed"} <= set(row) and row["corr"] == [0.0, 0.0]


def test_a_stop_is_recorded_with_its_lidar_frame(tmp_path, monkeypatch):
    """back1 stopped with nothing on record; where the robot really was had to
    be read off a frame grabbed by hand. --record now writes the stop too."""
    bw = _walk()
    monkeypatch.setattr(bw, "time", _Clock())

    class FakeBoard:
        def quality(self):
            return 70

        def current(self):
            return None

        def pose(self):
            return (-12.0, -9.4, 1.5)

        def power(self):
            return {"dockingStatus": "not_on_dock"}

        def get(self, path):
            return {"laser_points": [{"angle": 0.0, "distance": 1.0, "valid": True}]}

    w = bw.Walker(FakeBoard(), {"dock": {"pose": [0, 0, 0]}}, False, False)
    w.record_dir = str(tmp_path)

    def stuck(step, i, through=False):
        raise bw.Stop("step %d: held up" % i)

    monkeypatch.setattr(w, "step", stuck)
    try:
        w.run([{"creep": [[0, 0], [1, 0]]}], 1)
    except bw.Stop:
        pass
    doc = json.loads((tmp_path / "step_01_stop.json").read_text(encoding="utf-8"))
    assert doc["pose"] == [-12.0, -9.4, 1.5] and doc["scan"]["laser_points"]


def test_a_fast_segment_is_fast_with_something_far_ahead_and_slows_near_it():
    """go1/res1 (28 ต.ค.): the 0.2-0.25 segments went at 0.15 whenever anything
    was in the strip within 1.5 m (DOOR_LIMITS.front_v) -- a quarter of their
    time. The segment's own speed holds until ~0.5 m before something."""
    import numpy as np

    bw, ws = _walk(), _sim()
    post = np.array([(x, 1.0) for x in np.arange(-0.10, 0.11, 0.02)])
    line = bw.CreepLine({"creep": [[0.0, 0.0, 0.25], [0.0, 3.0]]}, 1, (0.0, 0.0, math.pi / 2), 0.0)
    base = ws.SimBase((0.0, 0.0, math.pi / 2), post)
    act = None
    for n in range(12):                              # ramped up, 0.7-1.0 m from the post
        act = line.tick(n * 0.1, base.board_pose(), base.lidar())
        line.sent(act, False)
    assert line.last_row["front"] is not None and line.last_row["front"] > 0.8
    assert act.vx > 0.2
    near = bw.CreepLine({"creep": [[0.0, 0.4, 0.25], [0.0, 3.0]]}, 1, (0.0, 0.4, math.pi / 2), 0.0)
    near.prev = (0.25, 0.0)
    act = near.tick(0.1, (0.0, 0.4, math.pi / 2), ws.SimBase((0.0, 0.4, math.pi / 2), post).lidar())
    assert act.vx < 0.25 and near.last_row["why"] == "front"     # 0.3 m before it: tapering


def test_the_walker_drives_the_same_line_controller_the_simulator_does():
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    body = src[src.index("    def creep(self"):src.index("    def _board_event")]
    assert "CreepLine(" in body and "fit=self.map_fit()" in body and "line.tick(" in body


# -- review 29 ต.ค. (the robot off: everything below is offline) ------------------


class _Clock:
    """A clock for the Walker's own loops: sleep moves it, nothing waits."""

    def __init__(self, on_sleep=None):
        self.t, self.on_sleep = 1000.0, on_sleep

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.on_sleep is not None:
            self.on_sleep(s)


_KILLED_MID_LEG = r'''
import importlib.util, signal, sys
spec = importlib.util.spec_from_file_location("board_walk", sys.argv[1])
bw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bw)

class Board:
    running = False
    def speed(self): return "0.5"
    def set_speed(self, v): pass
    def quality(self): return 70
    def power(self): return {"dockingStatus": "not_on_dock"}
    def mapping(self, on): pass
    def events(self): return []
    def current(self): return {"action_id": 7} if Board.running else None
    def cancel(self):
        Board.running = False
        print("CANCELLED", flush=True)
    def move_to(self, x, y, yaw):
        Board.running = True
        return 7
    def pose(self):
        if Board.running:
            signal.raise_signal(signal.SIGTERM)       # `pkill -f board_walk` mid-leg
        return (0.0, 0.0, 0.0)

bw.Board = Board
sys.exit(bw.main([sys.argv[2], "p"]))
'''


def test_pkill_mid_leg_cancels_the_boards_moveto(tmp_path):
    """SIGTERM (pkill's default), and SIGHUP when the adb shell it was started
    from goes away (the multimedia room is a Wi-Fi dead zone), killed Python
    on the spot: no finally, no VelocitySession exit -- the board's MoveTo went
    on to its target with nobody watching. They end the run as Ctrl-C does."""
    import subprocess
    import sys

    route = tmp_path / "r.json"
    route.write_text(json.dumps({"dock": {"pose": [0, 0, 0]}, "paths": {"p": {"steps": [{"to": [1.0, 0.0]}]}}}),
                     encoding="utf-8")
    script = tmp_path / "killed.py"
    script.write_text(_KILLED_MID_LEG, encoding="utf-8")
    r = subprocess.run([sys.executable, str(script), str(ROOT / "scripts" / "board_walk.py"), str(route)],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(ROOT))
    assert "CANCELLED" in r.stdout, (r.returncode, r.stdout[-500:], r.stderr[-500:])
    assert r.returncode == 130 and "interrupted" in r.stdout


def _frames(monkeypatch, *counts):
    """app.slamware_sdk.laser_scan handing out frames of these many returns
    (an arc of returns 1.5 m out), one per call; the last one repeats."""
    import app.slamware_sdk as sw

    left = list(counts)

    def laser_scan(sdk, timeout=None):
        n = left.pop(0) if len(left) > 1 else left[0]
        return {"laser_points": [{"angle": -1.7 + 3.4 * k / max(n, 1), "distance": 1.5, "valid": True}
                                 for k in range(n)]}

    monkeypatch.setattr(sw, "laser_scan", laser_scan)


def test_an_empty_lidar_frame_is_not_room_to_turn(monkeypatch):
    """drive_route met an empty frame on 6 ต.ค. 13:58. Every check here reads
    "no returns" as "nothing there": turn_sweep_gap of nothing is 9 m of room,
    a spot turn in the door frame was let go blind. A short frame is read
    again, and refused if it is short again."""
    bw = _walk()
    monkeypatch.setattr(bw, "time", _Clock())
    w = bw.Walker(object(), {"dock": {"pose": [0, 0, 0]}}, False, False)
    w.sdk = object()
    _frames(monkeypatch, 0, 1500)
    assert len(w.scan()) == 1500                         # one bad frame: read again
    _frames(monkeypatch, 0)
    try:
        w.scan()
        raise AssertionError("an empty frame was used")
    except bw.Stop as e:
        assert "lidar" in str(e)

    class Board:
        def pose(self):
            return (0.0, 0.0, 0.0)

    turned = []

    class V:
        def set(self, vx, w_):
            turned.append(w_)
            return False

    w.b = Board()
    try:
        w._spot_turn(V(), math.radians(-80), tol_deg=2.0)
        raise AssertionError("turned")
    except bw.Stop as e:
        assert "lidar" in str(e), e
    assert not any(turned)


class _CreepBoard:
    """The board for one creep step: the pose stands at the line's start for
    the first `start_reads` reads, then moves 5 cm a read to its end."""

    def __init__(self, start, end, events=(), start_reads=2):
        self.start, self.end, self.reads, self.start_reads = start, end, 0, start_reads
        self.queue = list(events)
        self.calls = []

    def pose(self):
        self.reads += 1
        n = max(1, round(math.dist(self.start[:2], self.end[:2]) / 0.05))
        f = min(1.0, max(0, self.reads - self.start_reads) / n)
        return (self.start[0] + f * (self.end[0] - self.start[0]), self.start[1] + f * (self.end[1] - self.start[1]),
                self.start[2])

    def events(self):
        out, self.queue = self.queue, []
        return out

    def quality(self):
        return 70

    def mapping(self, on):
        self.calls.append(("mapping", on))


def _fake_session(monkeypatch, log):
    import app.slamware_sdk as sw

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            log.append("open")
            return self

        def __exit__(self, *exc):
            log.append("close")

        def set(self, vx, w_):
            log.append((round(vx, 3), round(w_, 3)))
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)


def test_a_creep_line_is_not_stopped_by_what_the_board_said_before_it(monkeypatch):
    """The board's events queue up until read (_back_in drops them first,
    drive_route before every push) and nothing reads them during a MoveTo
    leg: a PATH_OCCUPIED from someone crossing the reception on the way to
    the door was this line's "board event (bumper?)" at its first tick."""
    import numpy as np

    bw = _walk()
    monkeypatch.setattr(bw, "time", _Clock())
    log = []
    _fake_session(monkeypatch, log)
    board = _CreepBoard((0.0, 0.0, math.pi / 2), (0.0, 1.0, math.pi / 2), events=[{"type": "PATH_OCCUPIED"}])
    w = bw.Walker(board, {"dock": {"pose": [0, 0, 0]}}, False, False)
    w.sdk, w._map_fit = object(), None
    monkeypatch.setattr(w, "scan", lambda: np.zeros((0, 2)))
    w.creep({"creep": [[0.0, 0.0], [0.0, 1.0]]}, 4)
    assert any(isinstance(c, tuple) and c[0] > 0 for c in log)          # it drove, then the line was done
    # the same for driving out of the dock: the last run's events are not this one's
    board = _CreepBoard((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), events=[{"type": "BUMPER_TRIGGERED"}], start_reads=1)
    board.power = lambda: {"dockingStatus": "not_on_dock"}
    w.b = board
    w.drive_out("undock")


def test_mapping_is_off_before_a_line_through_the_rooms_door(monkeypatch):
    """CLAUDE.md, 28 ต.ค.: a --map run in the multimedia room wrote people and
    the door leaf into the board's map of the door; the pose jumped 0.2 m in
    the doorway after that. --map kept mapping on until docking, so a path
    with no dock (room_tv_go) mapped the room end to end."""
    import numpy as np

    bw = _walk()
    monkeypatch.setattr(bw, "time", _Clock())
    log = []
    _fake_session(monkeypatch, log)
    board = _CreepBoard((0.0, 0.0, math.pi / 2), (0.0, 1.0, math.pi / 2))
    w = bw.Walker(board, {"dock": {"pose": [0, 0, 0]}}, True, False)
    w.sdk, w._map_fit = object(), None
    monkeypatch.setattr(w, "scan", lambda: np.zeros((0, 2)))
    w.creep({"creep": [[0.0, 0.0], [0.0, 1.0]]}, 4)
    assert board.calls == [("mapping", False)] and log[0] == "open"


def test_a_creep_steps_own_speed_is_capped_like_a_points():
    """A point's speed is capped at CREEP_SPEED_MAX; a speed on the whole step
    was not (the file check lets a step ask up to 0.5 -- the board's legs)."""
    bw = _walk()
    line = bw.CreepLine({"creep": [[0.0, 0.0], [0.0, 2.0]], "speed": 0.5}, 1, (0.0, 0.0, math.pi / 2), 0.0)
    assert line.limits.v_max <= bw.CREEP_SPEED_MAX


class _DockBase:
    """The dock at (0, 0) facing +x; the robot reversing along x from the
    pre-dock spot, held at `wall` by something the lidar cannot see behind."""

    def __init__(self, wall):
        self.x, self.vx, self.wall = 0.45, 0.0, wall

    def pose(self):
        return (self.x, 0.0, 0.0)

    def power(self):
        return {"dockingStatus": "not_on_dock", "isCharging": False}

    def events(self):
        return []

    def set_speed(self, v):
        pass

    def quality(self):
        return 70

    def move(self, dt):
        self.x = max(self.wall, self.x + self.vx * dt)


def _dock_with(monkeypatch, wall):
    bw = _walk()
    base = _DockBase(wall)
    monkeypatch.setattr(bw, "time", _Clock(on_sleep=base.move))
    import app.slamware_sdk as sw

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = vx
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    w = bw.Walker(base, {"dock": {"pose": [0.0, 0.0, 0.0], "estimated": False}}, False, False)
    w.sdk = object()
    outs = []

    def drive_out(why):
        outs.append(why)
        base.x = 0.45

    monkeypatch.setattr(w, "leg", lambda *a, **k: None)
    monkeypatch.setattr(w, "drive_out", drive_out)
    try:
        w.dock()
        raise AssertionError("docked against a wall")
    except bw.Stop as e:
        return str(e), outs


def test_docking_does_not_back_into_what_stopped_it_a_second_time(monkeypatch):
    """The wheels stopping 0.38 m out from the dock is something behind the
    robot (the lidar does not see there), not the dock: the second try
    (DOCK_TRIES, for 28 ต.ค. run 4 -- crooked, 1.3 cm short) drove out and
    reversed into it again. In the dock's last stretch: tried again."""
    reason, outs = _dock_with(monkeypatch, 0.38)
    assert "wheels stopped" in reason and outs == [], (reason, outs)
    reason, outs = _dock_with(monkeypatch, 0.013)                    # run 4: at the contacts
    assert outs == ["dock again"], (reason, outs)


import pytest  # noqa: E402

bw = _walk()


class _HeldBoard:
    """A MoveTo that stands still for as long as `free_at` says, then arrives."""

    def __init__(self, clock, free_at):
        self.clock, self.free_at, self.x = clock, free_at, 0.0
        self.cancelled = 0

    def set_speed(self, v):
        pass

    def quality(self):
        return 70

    def move_to(self, x, y, yaw):
        return 7

    def pose(self):
        if self.clock.t >= self.free_at:
            self.x = 1.0
        return (self.x, 0.0, 0.0)

    def current(self):
        return None if self.x >= 1.0 else {"action_id": 7}

    def cancel(self):
        self.cancelled += 1

    def action_reason(self, aid):
        return "x"


def _held_walker(monkeypatch, capsys_doc, free_in):
    clock = _Clock()
    monkeypatch.setattr(bw, "time", clock)
    board = _HeldBoard(clock, clock.t + free_in)
    return bw.Walker(board, capsys_doc, False, False), board


def test_a_route_without_patience_still_gives_up_at_eight_seconds(monkeypatch):
    """The owner's watched walks: nothing changes for a route file that does not ask."""
    w, board = _held_walker(monkeypatch, {"dock": {"pose": [0, 0, 0]}}, free_in=20.0)
    assert w.patience_s == 0.0 and w.min_quality == bw.MIN_QUALITY and not w.events_on
    with pytest.raises(bw.Stop, match="stuck"):
        w.leg({"to": [1.0, 0.0]}, 1)
    assert board.cancelled == 1


def test_an_event_leg_waits_for_the_guest_and_says_it_is_waiting(monkeypatch, capsys):
    """Owner 5 Nov, the launch event: the thing in the way steps aside when asked."""
    doc = {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0, "events": True}
    w, board = _held_walker(monkeypatch, doc, free_in=20.0)
    w.leg({"to": [1.0, 0.0]}, 2)
    assert board.cancelled == 0                 # 20 s standing, not given up at 8
    out = capsys.readouterr().out
    assert '@@ {"held": 2' in out and '"moving": 2' in out


def test_an_event_leg_still_gives_up_when_the_patience_is_spent(monkeypatch, capsys):
    doc = {"dock": {"pose": [0, 0, 0]}, "patience_s": 45.0, "events": True}
    w, board = _held_walker(monkeypatch, doc, free_in=500.0)
    with pytest.raises(bw.Stop, match="stuck"):
        w.leg({"to": [1.0, 0.0]}, 2)
    assert board.cancelled == 1 and 1045 <= w.b.clock.t <= 1050


def test_the_start_gate_is_the_route_files_to_lower_and_no_one_elses():
    """Q under 50 at the event's entrance spot: 153 of 442 frames, 4 Nov."""
    class B:
        def __init__(self, q):
            self.q = q

        def quality(self):
            return self.q

        def current(self):
            return None

    with pytest.raises(bw.Stop, match="localization quality 44"):
        bw.Walker(B(44), {"dock": {"pose": [0, 0, 0]}}, False, False).run([], 1)
    bw.Walker(B(44), {"dock": {"pose": [0, 0, 0]}, "min_quality": 40}, False, False).run([], 1)
    with pytest.raises(bw.Stop, match="localization quality 32"):      # the stale pose of 4 Nov
        bw.Walker(B(32), {"dock": {"pose": [0, 0, 0]}, "min_quality": 40}, False, False).run([], 1)


class _StartBoard:
    """A board that reads a good enough Q whatever map it holds."""

    def quality(self):
        return 45

    def current(self):
        return None

    def explore_map(self):
        return b""

    def pose(self):
        return (-6.913, -6.638, -3.139)


def _start_gate(monkeypatch, scores, **doc):
    class Fit:
        left = list(scores)

        def __init__(self, blob):
            pass

        def score(self, pose, pts):
            return Fit.left.pop(0)

    monkeypatch.setattr(bw, "MapFit", Fit)
    monkeypatch.setattr(bw, "time", _Clock())
    w = bw.Walker(_StartBoard(), {"dock": {"pose": [0, 0, 0]}, "min_quality": 40, **doc}, False, False)
    monkeypatch.setattr(w, "scan", lambda: [])
    return w


def test_q_does_not_know_the_wrong_map_the_lidar_does(monkeypatch):
    """5 Nov, carried up to floor 2 with floor 1's map loaded: Q41-45, lidar 0.15. The
    bar of 40 alone would have walked it. The word "pose" makes it `lost` on the phone."""
    w = _start_gate(monkeypatch, [0.15, 0.14, 0.16], min_fit=0.20)
    with pytest.raises(bw.Stop, match="pose") as refused:
        w.run([], 1)
    assert "0.15" in str(refused.value) and "Q45" in str(refused.value)


def test_a_right_pose_at_the_lowest_measured_fit_starts(monkeypatch):
    _start_gate(monkeypatch, [0.26, 0.23, 0.29], min_fit=0.20).run([], 1)


def test_one_frame_with_somebody_across_the_laser_is_not_a_lost_robot(monkeypatch):
    _start_gate(monkeypatch, [0.05, 0.30, 0.31], min_fit=0.20).run([], 1)


def test_a_route_file_that_does_not_ask_reads_no_map(monkeypatch):
    """Every other walk as it was: `explore_map` is not even asked."""
    class Fit:
        def __init__(self, blob):
            raise AssertionError("no map read")

    monkeypatch.setattr(bw, "MapFit", Fit)
    bw.Walker(_StartBoard(), {"dock": {"pose": [0, 0, 0]}, "min_quality": 40}, False, False).run([], 1)


def test_dock_pose_is_written_only_when_the_lidar_agrees(monkeypatch, capsys):
    """fit 0.14 at a stale pose, 0.68-0.76 at the dock (4 Nov)."""
    calls = []

    class Http:
        def put(self, path, json):
            calls.append((path, json))

            class R:
                def raise_for_status(self):
                    pass

            return R()

    class B:
        c = Http()

        def current(self):
            return None

        def explore_map(self):
            return b""

        def quality(self):
            return 73

    w = bw.Walker(B(), {"dock": {"pose": [-7.324, -6.191, 168.8]}}, False, False)
    monkeypatch.setattr(w, "scan", lambda: [])
    monkeypatch.setattr(bw, "time", _Clock())

    class Fit:
        score_is = 0.14

        def __init__(self, blob):
            pass

        def score(self, pose, pts):
            return Fit.score_is

    monkeypatch.setattr(bw, "MapFit", Fit)
    assert bw.set_dock_pose(w) == 2 and not calls
    assert '"why": "fit"' in capsys.readouterr().out
    Fit.score_is = 0.70
    assert bw.set_dock_pose(w) == 0
    assert calls[0][0].endswith("localization/pose") and calls[0][1]["x"] == -7.324


def test_the_event_rehearsal_loop_covers_every_leg_and_ends_docked():
    """Rehearsal 5 Nov: reception, the entrance, the door spot, and home, with the
    stands long enough for map_check's pictures. Never the door's creep."""
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    steps = doc["paths"]["event_rehearsal"]["steps"]
    assert steps[0] == {"do": "undock"} and steps[-1] == {"do": "dock"}
    points = [tuple(s["to"]) for s in steps if "to" in s]
    assert (-9.0, -5.2) in points and (-8.6, 0.2) in points and (-11.0, -6.9) in points
    assert not any("creep" in s or "fallback" in s for s in steps)
    assert all(s.get("speed", 0.3) <= 0.3 for s in steps)


def test_an_event_walk_home_stays_put_when_already_on_the_dock():
    class B:
        def power(self):
            return {"dockingStatus": "on_dock"}

        def pose(self):
            raise AssertionError("a docked robot was asked to drive")

    bw.Walker(B(), {"dock": {"pose": [0, 0, 0]}, "events": True}, False, False).dock()


class _PlaneBase:
    """A base on a plane: it rolls along its own heading at the speed last asked."""

    def __init__(self, x, y, h):
        self.x, self.y, self.h, self.vx = x, y, h, 0.0

    def pose(self):
        return (self.x, self.y, self.h)

    def power(self):
        return {"dockingStatus": "not_on_dock", "isCharging": False}

    def events(self):
        return []

    def quality(self):
        return 70

    def set_speed(self, v):
        pass

    def move(self, dt):
        self.x += self.vx * dt * math.cos(self.h)
        self.y += self.vx * dt * math.sin(self.h)


def _square_up_from(monkeypatch, y, scan=None):
    """The dock at (0, 0) facing +x; she stands at the pre-dock spot, `y` aside of its line."""
    import numpy as np
    import app.slamware_sdk as sw

    bw = _walk()
    base = _PlaneBase(0.45, y, 0.0)
    monkeypatch.setattr(bw, "time", _Clock(on_sleep=base.move))

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = vx
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    w = bw.Walker(base, {"dock": {"pose": [0.0, 0.0, 0.0]}}, False, False)
    w.sdk = object()
    turns, said = [], []
    monkeypatch.setattr(w, "scan", lambda: np.zeros((0, 2)) if scan is None else scan)
    monkeypatch.setattr(w, "turn_to", lambda yaw, tol_deg=1.0: (turns.append(round(math.degrees(yaw))), setattr(base, "h", yaw)))
    monkeypatch.setattr(w, "say", said.append)
    return bw, w, base, turns, said


def test_a_dock_arrival_aside_of_the_line_steps_onto_it_before_reversing(monkeypatch):
    """16 Nov, floor 1: the board's MoveTo ended 9 cm aside of the dock's line; the 0.45 m
    reverse took none of it out and she charged 9.5 cm aside, 1.7° crooked. Left of the line
    (facing out) = turn right, square to it, and roll the offset; right of it = turn left."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.09)
    w._square_up(0.0, 0.0, 0.0)
    assert turns == [-90] and abs(base.y) <= bw.SQUARE_TOL_M and abs(base.x - 0.45) < 1e-6, (turns, base.pose())
    assert base.vx == 0.0 and any("stepping onto it" in s for s in said)
    bw, w, base, turns, said = _square_up_from(monkeypatch, -0.12)
    w._square_up(0.0, 0.0, 0.0)
    assert turns == [90] and abs(base.y) <= bw.SQUARE_TOL_M, (turns, base.pose())


def test_a_dock_arrival_on_the_line_or_far_off_it_takes_no_step(monkeypatch):
    """Within SQUARE_TOL_M the two good docks of 16 Nov sat within 1.1 cm: no turn in front of
    the dock for that. Past SQUARE_MAX_M it is not a MoveTo's tolerance: no blind step either."""
    for y in (0.02, -0.03, 0.45):
        bw, w, base, turns, said = _square_up_from(monkeypatch, y)
        w._square_up(0.0, 0.0, 0.0)
        assert turns == [] and base.pose() == (0.45, y, 0.0), (y, turns, base.pose())
    assert any("too far for a step" in s for s in said)


def test_the_step_onto_the_dock_line_stops_for_something_ahead(monkeypatch):
    """The step is forward with the lidar looking: a thing in the strip ahead is a Stop, wheels at zero."""
    import numpy as np

    thing = np.array([[0.45, 0.0], [0.45, 0.05], [0.45, -0.05], [0.46, 0.1]])   # 10 cm off the front face
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.09, scan=thing)
    w._square_up(0.0, 0.0, 0.0)
    assert base.vx == 0.0 and abs(base.y - 0.09) < 1e-6 and turns == [-90]
    assert any("something" in s and "backing in from here" in s for s in said), said


def test_the_step_onto_the_dock_line_is_bounded_by_its_own_distance_and_not_tried_twice(monkeypatch):
    """Review 16 Nov: a pose that stops advancing rolled for a fixed 12 s (0.72 m) and then a
    second try. The time is what the distance takes twice over plus slack, and there is one try."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.09)
    rolled = []
    base.move = lambda dt: rolled.append(base.vx * dt)              # wheels turn, the pose does not
    bw.time.on_sleep = base.move
    w._square_up(0.0, 0.0, 0.0)
    assert turns == [-90], turns
    assert 0.05 < sum(rolled) <= 2 * 0.09 + bw.SQUARE_SLACK_S * bw.SQUARE_SPEED + 0.02, sum(rolled)
    assert base.vx == 0.0 and any("pose is not there" in s for s in said)


def test_no_room_to_turn_at_the_pre_dock_spot_reverses_from_there(monkeypatch):
    """Review 16 Nov: a chair beside the pre-dock spot was a Stop out of dock() -- off the
    charger for the night. Before the step existed she charged aside: that again, said."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.09)

    def no_room(yaw, tol_deg=1.0):
        raise bw.Stop("no room to turn -90 here: the arms would pass 0.01 m from something")

    monkeypatch.setattr(w, "turn_to", no_room)
    w._square_up(0.0, 0.0, 0.0)
    assert base.pose() == (0.45, 0.09, 0.0) and any("no step onto the line" in s for s in said)


def test_docking_squares_up_at_the_pre_dock_spot_before_it_reverses(monkeypatch):
    """The step onto the line is part of every dock, after the leg to the pre-dock spot and
    before the reverse (a reverse first is the 9.5 cm of 16 Nov again)."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.09)
    order = []
    monkeypatch.setattr(w, "leg", lambda *a, **k: order.append("leg"))
    real = w._square_up
    monkeypatch.setattr(w, "_square_up", lambda *a: (order.append("square"), real(*a)))
    monkeypatch.setattr(w, "_back_in", lambda *a, **k: (order.append("back"), (True, None, False))[1])
    monkeypatch.setattr(w, "_stays_docked", lambda *a: (True, None))
    w.dock()
    assert order == ["leg", "square", "back"] and abs(base.y) <= bw.SQUARE_TOL_M, (order, base.pose())
    assert turns == [-90, 0]                              # square to the line, then back onto the dock's heading


# ---- home facing out (`dock.back_from_m`, `dock.axis_offset_deg`; 16 Nov, floor 1) ----
# The numbers behind the model are in scripts/walk_sim.py (SLIP_*, ROLL_OFF_DEG) and
# .tmp/floor1-20261116/slip_report.md: the walk frames of that night's ten runs.

def test_the_way_home_facing_out_docks_square_where_the_pre_dock_spot_fails():
    """Floor 1, the moved dock: the nook is not on the board's map, and a MoveTo to the
    pre-dock spot through it gave up twice of five and parked her 9.5 cm aside once. From
    1.9 m out on the dock's line, facing out, reversing on our own: every seeded way home
    is on the contacts within 3 cm and 1.5 degrees -- on the base as measured (it rolls 2.3
    degrees off the heading read), and on one that rolls true or further off."""
    ws = _sim()
    old = ws.dock_trials(40, roll_off_deg=ws.ROLL_OFF_DEG)
    assert 8 <= len(old["stops"]) <= 28, old                                # the board gives up in the nook
    assert all("the board gave up" in why for _, why in old["stops"]), old["stops"][:3]
    for roll in (ws.ROLL_OFF_DEG, 0.0, 3.5):
        new = ws.dock_trials(40, back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG, roll_off_deg=roll)
        assert new["docked"] == 40 and not new["stops"], (roll, new)
        # the file's 2.3 on a base that rolls true is a line 2.3 degrees wrong: still on the
        # contacts, square, a few cm aside (the lean holds most of it; the square end gives 1-2 cm)
        # (20 Nov: the long reverse starts up to SQUARE_TOL_LONG_M aside with no step -- the step's turns
        # moved her 4-16 cm on the robot -- so on a line 2.3 degrees wrong that much more is left: 5.6 cm
        # of 40, under the 9.5 cm she charged at on 16 Nov)
        assert new["worst_aside_m"] <= (0.03 if roll else 0.065) and new["worst_crooked_deg"] <= 1.5, (roll, new)
    # the line along the heading, on the base as measured: on the dock, but crooked the way
    # all five docks of 16 Nov were (0.4-2.7 degrees) -- what the turned line is for
    plain = ws.dock_trials(40, back_from=1.9, roll_off_deg=ws.ROLL_OFF_DEG, straight_m=0.0)
    assert plain["docked"] == 40 and plain["mean_crooked_deg"] > 1.5, plain


def test_a_dock_with_no_back_from_goes_to_the_pre_dock_spot_as_ever():
    """No `back_from_m` in the file (floor 2; every file before 16 Nov) = the board's MoveTo to
    the pre-dock spot 0.45 m out and the short reverse: the same legs, the same reverse."""
    ws, bw = _sim(), _walk()
    dock, start = ws.FLOOR1_DOCK, (-10.5, -3.0, 0.3)
    old = ws.run_dock(dock, start, approach=ws.FLOOR1_APPROACH, slip=False, seed=3)
    px, py, _ = bw.pre_dock({"dock": {"pose": list(dock)}})
    assert old["end"] == "docked", old
    assert old["moves"] == [(-9.0, -6.2, None), (round(px, 2), round(py, 2), 169.6)], old["moves"]
    assert not any("facing out at" in line for line in old["said"])
    for bad in (None, 0, 0.45, 0.3, -1.9, 2.6, "x", [1.9]):
        assert bw.back_from_m({"dock": {"pose": list(dock), "back_from_m": bad}}) == 0.0, bad
    assert bw.back_from_m({"dock": {"pose": list(dock), "back_from_m": 1.9}}) == 1.9
    for bad in (None, 0, 5.5, -9, "x"):
        doc = {"dock": {"pose": list(dock), "axis_offset_deg": bad}}
        assert bw.dock_line(doc) == bw.dock_pose(doc)[2], bad
    assert abs(math.degrees(bw.dock_line({"dock": {"pose": list(dock), "axis_offset_deg": 2.3}})) - 171.9) < 1e-6


def test_home_facing_out_ends_the_boards_leg_on_the_dock_line_and_skips_an_approach_that_is_that_place():
    """With `back_from_m` the board's one leg near the dock ends that far out ON the dock's
    line (turned by `axis_offset_deg`), facing out. Floor 1's approach point is 0.24 m from
    there: not a leg of its own. One that is somewhere else is still the way in."""
    ws = _sim()
    dock, start = ws.FLOOR1_DOCK, (-10.5, -3.0, 0.3)
    res = ws.run_dock(dock, start, back_from=1.9, axis_offset_deg=2.3, approach=ws.FLOOR1_APPROACH, seed=3,
                      roll_off_deg=2.3)
    line = math.radians(169.6 + 2.3)
    spot = (round(dock[0] + 1.9 * math.cos(line), 2), round(dock[1] + 1.9 * math.sin(line), 2), 169.6)
    assert res["end"] == "docked" and res["moves"] == [spot], res["moves"]
    assert any("facing out at" in said for said in res["said"])
    far = ws.run_dock(dock, start, back_from=1.9, axis_offset_deg=2.3, approach=(-9.6, -5.0), seed=3, roll_off_deg=2.3)
    assert far["end"] == "docked" and far["moves"][:2] == [(-9.6, -5.0, None), spot], far["moves"]
    # past SQUARE_TOL_LONG_M (this seed's leg: +-8 cm) she goes 1 m further out on the line and comes to the
    # spot again (AGAIN_OUT_M, 21 Nov) -- the board's own two legs, nothing else
    again = (round(dock[0] + 2.9 * math.cos(line), 2), round(dock[1] + 2.9 * math.sin(line), 2), None)
    assert far["moves"][2:] in ([], [again, spot]), far["moves"]


def test_the_long_reverse_is_squared_at_its_end_and_the_short_one_is_as_it_was():
    """`back_in_command`: the lean that brings her onto the line is dropped over the last
    stretch of the long reverse (she met the contacts still leaning, 16 Nov), and may be
    larger before it. With neither asked for, every command is the one it always was."""
    bw = _walk()
    for out in (1.5, 0.4, 0.2, 0.05, 0.0, -0.01, -0.03):          # past the dock pose too (review 16 Nov)
        for lat in (-0.05, -0.004, 0.0, 0.01, 0.2):
            for herr in (-0.03, 0.0, 0.02):
                vx, want = bw.back_in_command(out, lat, herr)
                lean = max(-math.radians(2), min(math.radians(2), 3.0 * lat))
                assert want == bw.wrap_pi(lean - herr) and vx == bw.back_in_command(out, lat, herr, 0.3, 3.5)[0]
    assert bw.back_in_command(0.29, 0.05, 0.01, bw.BACK_STRAIGHT_M)[1] == pytest.approx(-0.01)       # square to the dock
    assert bw.back_in_command(0.31, 0.05, 0.0, bw.BACK_STRAIGHT_M)[1] == pytest.approx(math.radians(2))
    assert bw.back_in_command(1.0, 0.05, 0.0, 0.3, bw.BACK_HOME_LEAN_DEG)[1] == pytest.approx(math.radians(bw.BACK_HOME_LEAN_DEG))
    assert 2.0 < bw.BACK_HOME_LEAN_DEG <= bw.HEADING_GUARD_DEG / 2        # the guard is not what stops a lean
    assert bw.back_in_command(-0.02, 0.05, 0.0)[1] == pytest.approx(math.radians(2))   # the short one, past the pose: as ever
    assert bw.back_in_command(-0.02, 0.05, 0.0, bw.BACK_STRAIGHT_M)[1] == 0.0


class _RecordingBase(_PlaneBase):
    def __init__(self, x, y, h):
        super().__init__(x, y, h)
        self.docked_at = None

    def power(self):
        on = self.docked_at is not None and self.x <= self.docked_at
        return {"dockingStatus": "on_dock" if on else "not_on_dock", "isCharging": on}


def _far_dock(monkeypatch, back_in, dock=None, leg_ends=None):
    """dock() with `back_from_m` 1.9 on a dock at (0, 0) facing +x; `back_in` stands in for
    the reverse. `dock`: other keys for the file's dock; `leg_ends`: where the board's leg
    really ends (default: on the point). Returns (walker, what it did in order)."""
    import numpy as np
    import app.slamware_sdk as sw

    bw = _walk()
    base = _RecordingBase(3.0, 0.5, 2.0)
    monkeypatch.setattr(bw, "time", _Clock(on_sleep=base.move))

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = vx
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    w = bw.Walker(base, {"dock": dict({"pose": [0.0, 0.0, 0.0], "back_from_m": 1.9} if dock is None else dock)},
                  False, False)
    w.sdk = object()
    did = []

    def leg(step, i, through=False):
        did.append(("leg", [round(v, 2) for v in step["to"]], step.get("face")))
        base.x, base.y, base.h = (step["to"][0], step["to"][1], 0.0) if leg_ends is None else leg_ends

    monkeypatch.setattr(w, "leg", leg)
    monkeypatch.setattr(w, "scan", lambda: np.zeros((0, 2)))
    monkeypatch.setattr(w, "turn_to", lambda yaw, tol_deg=1.0: (did.append(("turn", round(math.degrees(yaw)))), setattr(base, "h", yaw)))
    monkeypatch.setattr(w, "say", lambda line: None)
    monkeypatch.setattr(w, "_back_in", lambda *a, **k: back_in(bw, w, base, did, a, k))
    monkeypatch.setattr(w, "_stays_docked", lambda *a: (True, None))
    return bw, w, base, did


def test_the_reverse_home_is_back_tos_blind_one_and_a_second_try_goes_all_the_way_out_again(monkeypatch):
    """`back_from_m`: the reverse is under the pose-jump guard, a stall counts from the first
    metre (`back_to`'s rules -- it backs as far, as blind), squared at its end, with the wider
    lean; and DOCK_TRIES' second try is the whole manoeuvre again: out to where she reversed
    from (not the pre-dock spot's 0.45 m), onto the line, in."""
    tries = []

    def back_in(bw, w, base, did, a, k):
        tries.append((a, k, type(w.b).__name__, round(base.x, 2)))
        did.append("back")
        base.x = 0.02                                          # met the dock, 2 cm short of the contacts
        return (len(tries) > 1, "the wheels stopped after 1.88 m (something behind, not the contacts)", False)

    bw, w, base, did = _far_dock(monkeypatch, back_in)
    w.dock()
    assert did[0] == ("leg", [1.9, 0.0], 0.0) and did.count("back") == 2, did
    for a, k, guard, _x in tries:
        assert guard == "_JumpGuard"
        assert a[4:] == (bw.BACK_TO_STALL_FROM_M, bw.BACK_STRAIGHT_M, 0.0, bw.BACK_HOME_LEAN_DEG), a
        assert k == {"grace_s": bw.BACK_STALL_GRACE_S, "aside_stop_m": bw.BACK_HOME_ASIDE_STOP_M}, k
    assert tries[0][3] == 1.9 and tries[1][3] >= 1.9 - 0.02, tries    # the second try: from all the way out
    assert type(w.b).__name__ == "_RecordingBase"                     # the guard is taken off again


def test_a_pose_jump_under_the_long_reverse_is_a_stop_where_she_is(monkeypatch):
    """The board relocalizing under the line (the nook's slip, the wrong floor's map) while she
    reverses 1.9 m blind: stopped where she is, as `back_to` does -- not steered onto a line
    that just moved 0.4 m."""
    bw, w, base, did = _far_dock(monkeypatch, None)
    monkeypatch.undo()                                             # the real _back_in below
    import numpy as np
    import app.slamware_sdk as sw

    monkeypatch.setattr(bw, "time", _Clock(on_sleep=base.move))
    sets = []

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            base.vx = 0.0

        def set(self, vx, w_):
            base.vx = vx
            sets.append(vx)
            if len(sets) == 30:
                base.y += 0.4                                    # the board's pose jumps under her
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    monkeypatch.setattr(w, "leg", lambda step, i, through=False: (setattr(base, "x", step["to"][0]),
                                                                 setattr(base, "y", step["to"][1]), setattr(base, "h", 0.0)))
    monkeypatch.setattr(w, "scan", lambda: np.zeros((0, 2)))
    monkeypatch.setattr(w, "say", lambda line: None)
    with pytest.raises(bw.Stop, match="pose jumped"):
        w.dock()
    assert base.vx == 0.0 and base.x > 1.0 and type(w.b).__name__ == "_RecordingBase"


def test_the_reverse_may_take_as_long_as_its_distance_and_the_short_one_still_has_40_s(monkeypatch):
    """16 Nov: 1.95 m of reverse took ~35 s of a fixed 40 -- BACK_TO_MAX_M's 2.5 m could not
    have made it. The limit grows with the distance; from the pre-dock spot it is 40 s."""
    import app.slamware_sdk as sw

    def limit_from(out):
        bw = _walk()
        base = _PlaneBase(out, 0.0, 0.0)
        clock = _Clock()
        monkeypatch.setattr(bw, "time", clock)

        class Session:
            def __init__(self, sdk, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def set(self, vx, w_):
                return False

        monkeypatch.setattr(sw, "VelocitySession", Session)
        w = bw.Walker(base, {"dock": {"pose": [0.0, 0.0, 0.0]}}, False, False)
        w.sdk = object()
        docked, reason, blocked = w._back_in(0.0, 0.0, 0.0, bw.OVERSHOOT_M, stall_from=9.0)   # wheels that never turn
        assert not docked and "not on the dock" in reason, reason
        return float(reason.split(" s ")[0])

    assert limit_from(0.45) == 40.0 and limit_from(0.63) == 40.0
    assert 50.0 < limit_from(1.95) < 55.0 and limit_from(2.5) > 60.0


def test_driving_out_for_a_second_try_can_go_as_far_as_she_reversed_from(monkeypatch):
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.0)
    base.x = 0.02
    w.drive_out("dock again", 1.9)
    assert 1.9 <= base.x < 1.96 and base.vx == 0.0
    base.x = 0.02
    w.drive_out("undock")                                          # the pre-dock spot, as ever
    assert bw.PREDOCK_M <= base.x < bw.PREDOCK_M + 0.05


def test_floor_1_comes_home_facing_out():
    """floor1.json, the moved dock (16 Nov): the file asks for the way home that the walk
    frames support -- and for a distance out at which the pose was right facing any way."""
    if not ROUTE.exists():
        pytest.skip("data/robot/waypoints/floor1.json is not on this machine (gitignored): NOT checked here -- "
                    "that floor 1's dock asks for back_from_m outside the nook's slip zone, a small "
                    "axis_offset_deg, and that rehearsal_back_home ends with a plain dock step")
    doc = json.loads(ROUTE.read_text(encoding="utf-8"))
    bw, ws = _walk(), _sim()
    far = bw.back_from_m(doc)
    assert ws.SLIP_WITHIN_M + 0.2 <= far <= bw.BACK_FROM_MAX_M, far
    assert 0 < doc["dock"]["axis_offset_deg"] <= bw.AXIS_OFFSET_MAX_DEG
    line = bw.dock_line(doc)
    x, y, _ = bw.dock_pose(doc)
    px, py = x + far * math.cos(line), y + far * math.sin(line)
    assert -26 < px < 18 and -16 < py < 9
    assert doc["paths"]["rehearsal_back_home"]["steps"][-1] == {"do": "dock"}


# ---- the review of 16 Nov: nothing reverses 1.9 m blind from where it should not ----

def test_the_long_reverse_does_not_start_from_off_the_dock_line(monkeypatch):
    """Review 16 Nov (blocking): `leg` takes an end up to 0.6 m from the point and `_square_up`
    has exits that say "backing in from here" -- written for 0.45 m, where charging aside beat a
    night off the charger. From 1.9 m, blind, a start 0.45 m aside meets the nook that far aside.
    Aside, too near / too far out, or facing wrong after the last turn: a Stop, no reverse. The
    short way (no `back_from_m`) still backs in from where it is."""
    backs = []

    def back_in(bw, w, base, did, a, k):
        backs.append(round(base.y, 2))
        return (True, None, False)

    for ends in ((1.9, 0.45, 0.0), (1.9, -0.31, 0.0), (1.4, 0.0, 0.0), (2.4, 0.0, 0.0)):
        bw, w, base, did = _far_dock(monkeypatch, back_in, leg_ends=ends)
        with pytest.raises(bw.Stop, match="not on the dock's line to reverse from") as e:
            w.dock()
        assert backs == [] and base.vx == 0.0, (ends, backs)
        from app import event_walk
        assert event_walk.kind_of(str(e.value)) == "dock"         # the phone: "เข้าแท่นไม่สำเร็จ", not "blocked"
    # within what the lean takes out (and a step brings it within SQUARE_TOL_M first): it reverses
    bw, w, base, did = _far_dock(monkeypatch, back_in, leg_ends=(1.95, 0.2, 0.0))
    w.dock()
    assert backs == [0.0] or abs(backs[0]) <= bw.SQUARE_TOL_M, backs
    # a heading the turn left over 1.5 degrees: turned once more, and only THEN judged
    backs.clear()
    bw, w, base, did = _far_dock(monkeypatch, back_in)
    turned = []

    def sloppy(yaw, tol_deg=1.0):
        turned.append(yaw)
        base.h = yaw + math.radians(6.0 if len(turned) < 3 else 5.0)      # never nearer than 5 degrees

    monkeypatch.setattr(w, "turn_to", sloppy)
    base_leg = w.leg
    monkeypatch.setattr(w, "leg", lambda step, i, through=False: (base_leg(step, i, through), setattr(base, "h", 0.1)))
    with pytest.raises(bw.Stop, match="not on the dock's line to reverse from"):
        w.dock()
    assert len(turned) == 3 and backs == []                           # facing out, the try's own, and once more
    # ... and when that one more turn gets it right, she reverses
    bw, w, base, did = _far_dock(monkeypatch, back_in)
    turned.clear()

    def third_time(yaw, tol_deg=1.0):
        turned.append(yaw)
        base.h = yaw + math.radians(6.0 if len(turned) < 3 else 0.5)

    monkeypatch.setattr(w, "turn_to", third_time)
    base_leg2 = w.leg
    monkeypatch.setattr(w, "leg", lambda step, i, through=False: (base_leg2(step, i, through), setattr(base, "h", 0.1)))
    w.dock()
    assert len(turned) == 3 and backs == [0.0]
    # the short way: 0.45 m aside at the pre-dock spot is said and backed in from, as ever
    backs.clear()
    bw, w, base, did = _far_dock(monkeypatch, back_in, dock={"pose": [0.0, 0.0, 0.0]}, leg_ends=(0.45, 0.45, 0.0))
    w.dock()
    assert backs == [0.45]


def test_the_precondition_is_asked_again_before_a_second_try(monkeypatch):
    """The second try drives 1.9 m out open-loop in heading (after a heading-guard stop at 5
    degrees that is ~17 cm aside): squared up and checked again before it reverses."""
    n = []

    def back_in(bw, w, base, did, a, k):
        n.append(1)
        base.x = 0.02
        return (len(n) > 1, "heading 5.2° off the dock's line", False)

    bw, w, base, did = _far_dock(monkeypatch, back_in)
    ready = []
    real = w._ready_to_reverse
    monkeypatch.setattr(w, "_ready_to_reverse", lambda *a: (ready.append(round(base.x, 2)), real(*a)))
    w.dock()
    assert len(n) == 2 and len(ready) == 2 and ready[1] >= 1.88, ready


def test_the_long_reverse_stops_when_it_is_not_coming_onto_its_line(monkeypatch):
    """A line turned the wrong way, or a pose that walked: 10 cm aside while backing blind is
    not the dock she is backing at -- stopped, and not tried again. The short reverse (and
    `back_to`) have no such stop, as ever."""
    import app.slamware_sdk as sw

    def run(aside_stop):
        bw = _walk()
        base = _PlaneBase(1.5, 0.11, 0.0)
        monkeypatch.setattr(bw, "time", _Clock(on_sleep=base.move))

        class Session:
            def __init__(self, sdk, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                base.vx = 0.0

            def set(self, vx, w_):
                base.vx = vx
                return False

        monkeypatch.setattr(sw, "VelocitySession", Session)
        w = bw.Walker(base, {"dock": {"pose": [0.0, 0.0, 0.0]}}, False, False)
        w.sdk = object()
        return bw, base, w._back_in(0.0, 0.0, 0.0, bw.OVERSHOOT_M, 0.0, aside_stop_m=aside_stop)

    bw, base, (docked, reason, blocked) = run(bw_const := 0.10)
    assert not docked and blocked and "aside of the dock's line" in reason and base.x > 1.45, (reason, base.x)
    bw, base, (docked, reason, blocked) = run(0.0)
    # off: not the aside-of-line stop (the dock seek's own "not on blind" note may still name how far aside it is)
    assert "aside of the dock's line" not in (reason or "") and base.x < 0.1, (reason, base.x)
    assert bw.BACK_HOME_START_ASIDE_M + 0.04 <= bw.BACK_HOME_ASIDE_STOP_M < _sim().DOCK_CONTACT_HALF_M


def test_a_persons_stop_while_seating_is_not_tried_again(monkeypatch):
    """Review 16 Nov: `_stays_docked` answered "taken away" but the try was not marked blocked,
    so DOCK_TRIES ran again -- with `back_from_m` that is 1.9 m forward and 1.9 m blind back
    after somebody pressed B. Either way home: no second try."""
    for dock in (None, {"pose": [0.0, 0.0, 0.0]}):
        outs = []
        bw, w, base, did = _far_dock(monkeypatch, lambda bw, w, base, did, a, k: (True, None, False), dock=dock)
        monkeypatch.setattr(w, "_stays_docked", lambda *a: (False, bw.TAKEN_AWAY))
        monkeypatch.setattr(w, "drive_out", lambda *a: outs.append(a))
        with pytest.raises(bw.Stop, match="taken away"):
            w.dock()
        assert outs == [], (dock, outs)


def test_driving_out_stops_for_a_person_and_for_something_ahead(monkeypatch):
    """`drive_out` sent its speed whatever `set` answered: B on the way out and the session took
    the action up again (review 16 Nov). A watchdog gap is not a person. And the 1.9 m out of a
    second try has the lidar on the strip ahead, as the 0.45 m always had."""
    import numpy as np
    import app.slamware_sdk as sw

    def out_with(renew, scan=None):
        bw, w, base, turns, said = _square_up_from(monkeypatch, 0.0, scan=scan)
        base.x = 0.02
        sets = []

        class Session:
            def __init__(self, sdk, *a, **k):
                self.last_renew = None

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                base.vx = 0.0

            def set(self, vx, w_):
                base.vx = vx
                sets.append(vx)
                if renew and len(sets) == 20:
                    self.last_renew = renew
                    return True
                return False

        monkeypatch.setattr(sw, "VelocitySession", Session)
        return bw, w, base

    bw, w, base = out_with("taken")
    with pytest.raises(bw.Stop, match="taken away"):
        w.drive_out("dock again", 1.9)
    assert base.vx == 0.0 and base.x < 0.5
    bw, w, base = out_with("gap")
    w.drive_out("dock again", 1.9)
    assert base.x >= 1.9
    thing = np.array([[0.45, 0.0], [0.45, 0.05], [0.45, -0.05], [0.46, 0.1]])
    bw, w, base = out_with(None, scan=thing)
    with pytest.raises(bw.Stop, match="something"):
        w.drive_out("dock again", 1.9)
    assert base.x < 0.1 and base.vx == 0.0


def test_a_base_slow_to_start_is_not_a_stall_and_a_dead_one_still_is(monkeypatch):
    """Review 16 Nov: the long reverse counted a stall from its first tick; a base that answers
    0.8 s late has not made STALL_MPS over its first 1.2 s and read as "the wheels stopped after
    0.01 m -- not tried again", 1.9 m out. Counted from BACK_STALL_GRACE_S after the wheels were
    first asked (and again after a watchdog gap). Wheels that never turn are still a stall."""
    ws = _sim()
    f1 = dict(roll_off_deg=ws.ROLL_OFF_DEG, back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG)
    slow = ws.dock_trials(20, lag_s=0.8, **f1)
    assert not [why for _, why in slow["stops"] if "wheels stopped" in why], slow["stops"]
    assert slow["docked"] >= 17, slow                              # the rest: a turn that coasted past 4 degrees
    gap = ws.dock_trials(10, gap_at_out=1.0, **f1)
    assert gap["docked"] == 10, gap
    import app.slamware_sdk as sw

    bw = _walk()
    base = _PlaneBase(1.9, 0.0, 0.0)
    clock = _Clock()
    monkeypatch.setattr(bw, "time", clock)

    class Session:
        def __init__(self, sdk, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def set(self, vx, w_):
            return False

    monkeypatch.setattr(sw, "VelocitySession", Session)
    w = bw.Walker(base, {"dock": {"pose": [0.0, 0.0, 0.0]}}, False, False)
    w.sdk = object()
    t0 = clock.t
    docked, reason, blocked = w._back_in(0.0, 0.0, 0.0, bw.OVERSHOOT_M, bw.BACK_TO_STALL_FROM_M, grace_s=bw.BACK_STALL_GRACE_S)
    assert "wheels stopped" in reason and blocked and bw.BACK_STALL_GRACE_S <= clock.t - t0 < bw.BACK_STALL_GRACE_S + 1.0


def test_what_the_simulated_way_home_does_when_things_go_wrong():
    """The stops of the long way home, run through dock() itself (review 16 Nov: the first
    simulation had no lidar, no events and a session that never said no)."""
    ws = _sim()
    f1 = dict(roll_off_deg=ws.ROLL_OFF_DEG, back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG)
    start = (-10.5, -3.0, 0.3)

    def one(**kw):
        return ws.run_dock(ws.FLOOR1_DOCK, start, approach=ws.FLOOR1_APPROACH, seed=5, **dict(f1, **kw))

    taken = one(taken_at_out=1.0)
    assert taken["end"] == "stop" and "taken away" in taken["reason"] and 0.8 < taken["out_m"] < 1.05, taken
    bump = one(bumper_at_out=0.8)
    assert bump["end"] == "stop" and "board event" in bump["reason"] and "not tried again" in bump["reason"], bump
    again = one(contacts_miss=1)
    assert again["end"] == "docked" and again["arrivals"] == 2 and abs(again["aside_m"]) <= 0.03, again
    thing = one(contacts_miss=1, thing_ahead_from=1.0)
    assert thing["end"] == "stop" and "something" in thing["reason"] and 0.9 < thing["out_m"] < 1.3, thing
    # the file's line turned the wrong way for this base: on the contacts within their reach, or
    # stopped out on the floor -- never pushed against the dock aside of it
    wrong = ws.dock_trials(30, **dict(f1, roll_off_deg=-ws.ROLL_OFF_DEG))
    assert wrong["docked"] + len(wrong["stops"]) == 30 and wrong["docked"] >= 10, wrong
    assert wrong["worst_aside_m"] <= 0.10 and all("aside of the dock's line" in why for _, why in wrong["stops"]), wrong
    worse = ws.dock_trials(10, roll_off_deg=ws.ROLL_OFF_DEG, back_from=1.9, axis_offset_deg=-5.0)
    assert worse["docked"] == 0 and all("aside of the dock's line" in why for _, why in worse["stops"]), worse
    r = ws.run_dock(ws.FLOOR1_DOCK, start, approach=ws.FLOOR1_APPROACH, seed=5, roll_off_deg=ws.ROLL_OFF_DEG,
                    back_from=1.9, axis_offset_deg=-5.0)
    assert r["out_m"] > 0.3, r                                    # stopped on the floor, not at the dock's body
    # a heading that reads 1 degree noisy: a second try now and then, almost never a stop
    # (21 Nov: 100 ways home, not 30, and 85 not 28/30. The 30 seeds' 28 was their luck: what coming to the spot
    # again costs here was measured over 500 ways home a row (scratchpad walk_sim_1121/abc_compare.txt) -- 16 Nov's
    # legs with this heading noise 458 docked before (O), 437 with it (B): 4 in 100 more ways home lean onto the line
    # instead of stepping and trip the 5 deg guard on a noisy heading, SQUARE_TOL_LONG_M's price; 20 Nov's legs
    # 453 -> 456. These 30 seeds give B 25. The bar is the 500-run rate, 87.4 %, less two of 100 for 100 seeds.)
    noisy = ws.dock_trials(100, noise_deg=1.0, **f1)
    assert noisy["docked"] >= 85 and noisy["second_tries"] <= 30, noisy
    # the pose stepping 4 cm aside near the dock (three reverses of 16 Nov) is not the lateral stop
    blips = ws.dock_trials(20, blip_m=0.04, **f1)
    assert blips["docked"] == 20, blips
    # the pulses turning her a third of what the simulator's base gives: still square
    weak = ws.dock_trials(20, yaw_gain=0.3, **f1)
    assert weak["docked"] == 20 and weak["worst_crooked_deg"] <= 1.5 and weak["worst_aside_m"] <= 0.03, weak


def test_a_turned_line_without_back_from_is_the_short_reverse_on_that_line(monkeypatch):
    """`axis_offset_deg` alone: the pre-dock spot and the 0.45 m reverse as ever, with "aside"
    and "along" of the turned line -- and none of the long reverse's rules."""
    seen = []

    def back_in(bw, w, base, did, a, k):
        seen.append((a[4:], k, type(w.b).__name__))
        return (True, None, False)

    bw, w, base, did = _far_dock(monkeypatch, back_in, dock={"pose": [0.0, 0.0, 0.0], "axis_offset_deg": 2.3})
    w.dock()
    assert did[0] == ("leg", [bw.PREDOCK_M, 0.0], 0.0), did
    assert seen == [((), {"line": pytest.approx(math.radians(2.3))}, "_RecordingBase")], seen
    ws = _sim()
    res = ws.run_dock(ws.FLOOR1_DOCK, (-10.5, -3.0, 0.3), approach=ws.FLOOR1_APPROACH, seed=5, slip=False,
                      roll_off_deg=ws.ROLL_OFF_DEG, axis_offset_deg=ws.ROLL_OFF_DEG)
    assert res["end"] == "docked" and abs(res["aside_m"]) <= 0.03, res


def test_a_key_the_file_gives_and_the_walk_will_not_use_is_said_first(capsys):
    """Review 16 Nov: a `back_from_m` of 2.6 quietly became the old way home -- the one that
    gave up in the nook twice of five -- under a file that reads like the new one."""
    bw = _walk()
    pose = [-7.11, -6.78, 169.6]
    assert bw.dock_key_notes({"dock": {"pose": pose}}) == [] and bw.dock_key_notes({}) == []
    good = bw.dock_key_notes({"dock": {"pose": pose, "back_from_m": 1.9, "axis_offset_deg": 2.3}})
    assert len(good) == 2 and not any("NOT USED" in n for n in good) and "1.90 m" in good[0] and "+2.3" in good[1]
    bad = bw.dock_key_notes({"dock": {"pose": pose, "back_from_m": 2.6, "axis_offset_deg": 9}})
    assert len(bad) == 2 and all("IS NOT USED" in n for n in bad), bad
    assert bw.dock_key_notes({"dock": {"pose": pose, "back_from_m": "x"}})[0].count("IS NOT USED") == 1

    class Board:
        def quality(self):
            return 70

        def current(self):
            return None

    w = bw.Walker(Board(), {"dock": {"pose": pose, "back_from_m": 2.6}}, False, False)
    w.run([{"do": "wait", "s": 0}], 1)
    assert "back_from_m 2.6 IS NOT USED" in capsys.readouterr().out


# ---- 20 Nov, floor 1: turns on the spot move her; the long reverse leans off a few cm itself ----

def test_the_long_reverse_takes_a_few_cm_aside_itself_and_steps_only_past_that(monkeypatch):
    """n20j (the counter walk): 5.1 cm aside at 1.9 m out, the step's turns on the spot moved
    her 16.5 / 13 / 4 cm -- 11.9 cm aside after the step, no reverse, off the charger. Within
    SQUARE_TOL_LONG_M the long reverse starts where she is (its lean takes 7 cm out); past it,
    the step as before, and then the reverse from on the line."""
    backs = []

    def back_in(bw, w, base, did, a, k):
        backs.append(round(base.y, 3))
        return (True, None, False)

    for lat in (0.045, -0.045, 0.035):
        backs.clear()
        bw, w, base, did = _far_dock(monkeypatch, back_in, leg_ends=(1.9, lat, 0.0))
        w.dock()
        assert not any(d[0] == "turn" for d in did) and backs == [lat], (lat, did, backs)
    for lat in (0.07, -0.07):
        backs.clear()
        bw, w, base, did = _far_dock(monkeypatch, back_in, leg_ends=(1.9, lat, 0.0))
        w.dock()
        assert ("turn", -90 if lat > 0 else 90) in did, (lat, did)
        assert len(backs) == 1 and abs(backs[0]) <= bw.SQUARE_TOL_M, (lat, backs)
    assert bw.SQUARE_TOL_M < bw.SQUARE_TOL_LONG_M < bw.BACK_HOME_START_ASIDE_M


def test_the_short_reverse_still_steps_from_3_cm(monkeypatch):
    """No `back_from_m` (the 0.45 m reverse, floor 2): that reverse has no room to lean 4.5 cm
    out -- the step stays at SQUARE_TOL_M, as it was."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.045)
    monkeypatch.setattr(w, "leg", lambda *a, **k: None)
    monkeypatch.setattr(w, "_back_in", lambda *a, **k: (True, None, False))
    monkeypatch.setattr(w, "_stays_docked", lambda *a: (True, None))
    w.dock()                                                        # dock()'s own bar, not _square_up's default
    assert turns == [-90, 0] and abs(base.y) <= bw.SQUARE_TOL_M, (turns, base.pose())


def test_turns_that_slide_her_leave_fewer_ways_home_off_the_charger():
    """walk_sim's `turn_slide_m_per_rad` (20 Nov: 0.025-0.106 m per radian turned): with the
    step at 3 cm before the long reverse most ways home that came in 3-6 cm aside stopped in
    front of the dock; with SQUARE_TOL_LONG_M they reverse and charge. With no slide, floor1.json
    docks every way home either way."""
    ws = _sim()
    real = ws.load_board_walk
    bar = [None]

    def load():
        bw = real()
        bw.AGAIN_OUT_M = 0.0                       # the step's own bar: no second approach first (21 Nov)
        if bar[0] is not None:
            bw.SQUARE_TOL_LONG_M = bar[0]
        return bw

    ws.load_board_walk = load
    f1 = dict(back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG, roll_off_deg=ws.ROLL_OFF_DEG)
    now = ws.dock_trials(60, turn_slide_m_per_rad=0.10, **f1)
    bar[0] = 0.03
    before = ws.dock_trials(60, turn_slide_m_per_rad=0.10, **f1)
    assert now["docked"] >= before["docked"] + 10, (now, before)
    bar[0] = None
    plain = ws.dock_trials(60, **f1)
    assert plain["docked"] == 60 and plain["worst_aside_m"] <= 0.03, plain


# ---- 21 Nov: past SQUARE_TOL_LONG_M the long reverse's spot is come to again, not stepped onto ----

def _far_dock_legs(monkeypatch, back_in, ends, dock=None):
    """_far_dock whose board legs end, one after another, where `ends` says (None = on the point)."""
    bw, w, base, did = _far_dock(monkeypatch, back_in, dock=dock)
    ends = list(ends)

    def leg(step, i, through=False):
        did.append(("leg", [round(v, 2) for v in step["to"]], step.get("face")))
        end = ends.pop(0) if ends else None
        base.x, base.y, base.h = (step["to"][0], step["to"][1], 0.0) if end is None else end

    monkeypatch.setattr(w, "leg", leg)
    return bw, w, base, did


def test_past_the_bar_the_long_reverse_comes_to_its_spot_again_instead_of_stepping(monkeypatch):
    """20 Nov, floor 1: the dock legs of n20j/n20k ended 5.1/5.5 cm aside; the step's turns on the spot
    moved the board's pose 4-16.5 cm a turn, she ended 7-12 cm aside and did not reverse, twice. Now
    past SQUARE_TOL_LONG_M she goes AGAIN_OUT_M further out on the line and the board's leg brings her
    to the spot once more -- none of our turns -- and reverses from there."""
    backs = []

    def back_in(bw, w, base, did, a, k):
        backs.append(round(base.y, 3))
        did.append("back")
        return (True, None, False)

    for lat in (0.07, -0.055, 0.25):
        backs.clear()
        bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(1.9, lat, 0.0)])
        w.dock()
        out = round(1.9 + bw.AGAIN_OUT_M, 2)
        assert did == [("leg", [1.9, 0.0], 0.0), ("leg", [out, 0.0], None), ("leg", [1.9, 0.0], 0.0), "back"], (lat, did)
        assert backs == [0.0]
    assert bw.AGAIN_OUT_M >= 1.0                                    # the legs from >= ~1 m away ended <= 3 cm (4 of 5)
    # within the bar: no second approach (the lean takes it out), as before
    backs.clear()
    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(1.9, 0.045, 0.0)])
    w.dock()
    assert did == [("leg", [1.9, 0.0], 0.0), "back"] and backs == [0.045], did
    # the short reverse (no back_from_m) has no second approach: it steps, as ever
    backs.clear()
    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(0.45, 0.06, 0.0)], dock={"pose": [0.0, 0.0, 0.0]})
    w.dock()
    assert [d for d in did if d[0] == "leg"] == [("leg", [0.45, 0.0], 0.0)] and ("turn", -90) in did, did


def test_the_spot_is_come_to_again_once_a_way_home_and_then_the_step_is_the_last_resort(monkeypatch):
    """One second approach a way home: still past the bar after it (a leg that ends 8 cm aside again) =
    the step as before -- with no second approach the start gate would only stop her there."""
    backs = []

    def back_in(bw, w, base, did, a, k):
        backs.append(round(base.y, 3))
        did.append("back")
        return (True, None, False)

    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(1.9, 0.07, 0.0), None, (1.9, 0.08, 0.0)])
    w.dock()
    assert [d for d in did if d[0] == "leg"] == [("leg", [1.9, 0.0], 0.0), ("leg", [2.9, 0.0], None),
                                                 ("leg", [1.9, 0.0], 0.0)], did
    assert ("turn", -90) in did and len(backs) == 1 and abs(backs[0]) <= bw.SQUARE_TOL_M, (did, backs)
    # the board will not take her out (somebody there, the point refused): the step, from where she is
    backs.clear()
    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(1.9, -0.07, 0.0)])
    first = w.leg

    def refuse_out(step, i, through=False):
        if step.get("face") is None:
            raise bw.Stop("step 0: ended 0.90 m from the point (the board gave up on it)")
        return first(step, i, through)

    monkeypatch.setattr(w, "leg", refuse_out)
    w.dock()
    assert ("turn", 90) in did and len(backs) == 1 and abs(backs[0]) <= bw.SQUARE_TOL_M, (did, backs)


def test_a_second_try_lines_up_again_before_it_reverses(monkeypatch):
    """Review 20 Nov (2): the second try's call site was untested. The second try drives out along the
    line and lines up again there: past the bar it comes to the spot again (when the first try did not),
    then the start gate; after a second approach already, the step."""
    tries = []

    def back_in(bw, w, base, did, a, k):
        tries.append(round(base.y, 3))
        did.append("back")
        base.x = 0.02
        return (len(tries) > 1, "the wheels stopped 1 cm short of the contacts", False)

    def out_aside(w, base, did, lat):
        def drive_out(why, out_m=0.45):
            did.append(("out", round(out_m, 2)))
            base.x, base.y, base.h = out_m, lat, 0.0               # out on the line ... this far aside of it
        return drive_out

    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [None])          # the first leg on the line
    monkeypatch.setattr(w, "drive_out", out_aside(w, base, did, 0.08))
    w.dock()
    i = did.index(("out", 1.9))
    assert did[i + 1:] == [("leg", [2.9, 0.0], None), ("leg", [1.9, 0.0], 0.0), "back"], did
    assert tries == [0.0, 0.0]
    # the first try came again already: the second steps onto the line
    tries.clear()
    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, [(1.9, 0.07, 0.0)])
    monkeypatch.setattr(w, "drive_out", out_aside(w, base, did, 0.08))
    w.dock()
    i = did.index(("out", 1.9))
    assert not any(d[0] == "leg" for d in did[i + 1:]) and ("turn", -90) in did[i + 1:], did
    assert len(tries) == 2 and abs(tries[1]) <= bw.SQUARE_TOL_M, tries


def test_the_20_nov_models_of_the_dock_sim():
    """walk_sim.DockWorld (a) `near_leg_aside_m`: a dock leg that starts near its spot ends 5-6 cm aside
    (n20k); (b) `pose_slide_m_per_rad`: a turn on the spot moves the BOARD's pose, not the base (n20j:
    16.5 / 13 / 4 cm), until the board's next leg."""
    import numpy as np

    ws = _sim()
    dock = ws.FLOOR1_DOCK
    ux, uy = math.cos(math.radians(dock[2])), math.sin(math.radians(dock[2]))
    spot = (dock[0] + 1.9 * ux, dock[1] + 1.9 * uy)
    yaw = math.radians(dock[2])
    for seed in range(6):
        near = ws.DockWorld(dock, (spot[0] + 0.14 * ux, spot[1] + 0.14 * uy, yaw), seed=seed, slip=False,
                            near_leg_aside_m=(0.05, 0.06))
        near.move_to(spot[0], spot[1], yaw)
        assert 0.049 <= abs(near.out_lat()[1]) <= 0.061, near.out_lat()
        far = ws.DockWorld(dock, (spot[0] + 1.5 * ux, spot[1] + 1.5 * uy - 0.8, yaw), seed=seed, slip=False,
                           near_leg_aside_m=(0.05, 0.06), arrive_m=0.0)
        far.move_to(spot[0], spot[1], yaw)
        assert abs(far.out_lat()[1]) < 0.001                          # from afar: the leg's own arrival
    w = ws.DockWorld(dock, (spot[0], spot[1], yaw), seed=1, slip=False, noise_m=0.0, noise_deg=0.0,
                     pose_slide_m_per_rad=(0.05, 0.05))
    x0, y0, yaw0 = w.base.x, w.base.y, w.base.yaw
    w.cmd = (0.0, 0.5)
    w.sleep(3.0)
    w.cmd = (0.0, 0.0)
    w.sleep(1.0)
    turned = abs(ws.rl.wrap_pi(w.base.yaw - yaw0))
    assert turned > 0.5 and math.hypot(w.base.x - x0, w.base.y - y0) < 1e-9          # the base did not move
    px, py, _ = w.pose()
    assert math.hypot(px - w.base.x, py - w.base.y) == pytest.approx(0.05 * turned, rel=0.05)
    w.move_to(spot[0] - 1.0 * ux, spot[1] - 1.0 * uy, None)
    px, py, _ = w.pose()
    assert math.hypot(px - w.base.x, py - w.base.y) < 1e-9                            # the board's own leg re-finds it (ASSUMED)
    # `aside_repeats`: a second leg to the same spot ends where the first did -- the worst case for coming again
    # (re-review 21 Nov: the come-again's leg was a fresh draw; `pose_slide_held` changed no number and is gone)
    for repeats in (False, True):
        w = ws.DockWorld(dock, (spot[0] + 1.5 * ux, spot[1] + 1.5 * uy - 0.8, yaw), seed=4, slip=False,
                         far_leg_20nov=True, aside_repeats=repeats)
        w.move_to(spot[0], spot[1], yaw)
        first = w.out_lat()[1]
        w.move_to(spot[0] + ux, spot[1] + uy, None)
        w.move_to(spot[0], spot[1], yaw)
        assert (abs(w.out_lat()[1] - first) < 1e-9) == repeats, (repeats, first, w.out_lat())
    # the far legs of 20 Nov: 4 in 5 within 3 cm aside, the rest up to 6 cm (n20j 5.1 cm from 1.17 m) -- past the
    # 5 cm bar sometimes (review 21 Nov: the first model never was, so coming again could not fail in it)
    lats = []
    for seed in range(300):
        far = ws.DockWorld(dock, (spot[0] + 1.5 * ux, spot[1] + 1.5 * uy - 0.8, yaw), seed=seed, slip=False,
                           far_leg_20nov=True)
        far.move_to(spot[0], spot[1], yaw)
        lats.append(abs(far.out_lat()[1]))
    lats = np.array(lats)
    assert 0.72 < (lats <= 0.03).mean() < 0.88 and (lats > 0.05).sum() >= 5 and lats.max() <= 0.0601, lats
    # `near_leg_m` 1.1: the come-again's own 1.0 m leg counts as near (the pessimistic case for coming again)
    for near_m, near_too in ((ws.NEAR_LEG_M, False), (1.1, True)):
        w = ws.DockWorld(dock, (spot[0] + 1.0 * ux, spot[1] + 1.0 * uy, yaw), seed=2, slip=False, arrive_m=0.0,
                         near_leg_aside_m=(0.05, 0.06), near_leg_m=near_m)
        w.move_to(spot[0], spot[1], yaw)
        assert (abs(w.out_lat()[1]) >= 0.049) == near_too, (near_m, w.out_lat())


def test_coming_to_the_spot_again_leaves_fewer_ways_home_off_the_charger_than_the_step():
    """walk_sim.dock_trials, AGAIN_OUT_M against 0 (the step alone -- read again after its turn since the 21 Nov
    review): with what 20 Nov showed (a leg from near its spot ends 5-6 cm aside, the far legs <= 3 cm 4 in 5 and
    up to 6, our turns move the board's pose) and with the old model (the base slides as it turns) the second
    approach docks many more ways home; with neither (the 16 Nov legs ending +-8 cm) it docks every one, as the
    step did. (500 a row, scratchpad abc_compare: near start 197 -> 432, far legs + pose sliding 463 -> 490.)"""
    ws = _sim()
    real = ws.load_board_walk
    again = [None]

    def load():
        bw = real()
        if again[0] is not None:
            bw.AGAIN_OUT_M = again[0]
        return bw

    ws.load_board_walk = load
    try:
        f1 = dict(back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG, roll_off_deg=ws.ROLL_OFF_DEG)
        far = dict(f1, far_leg_20nov=True)
        seen = dict(far, starts="near", near_leg_aside_m=(0.05, 0.06), pose_slide_m_per_rad=(0.025, 0.106))
        for kw, n, more in ((seen, 60, 12), (dict(far, pose_slide_m_per_rad=(0.025, 0.106)), 200, 6),
                            (dict(far, turn_slide_m_per_rad=0.10), 200, 6)):
            again[0] = None
            now = ws.dock_trials(n, **kw)
            again[0] = 0.0
            before = ws.dock_trials(n, **kw)
            assert now["docked"] >= before["docked"] + more and now["come_agains"] > 0, (kw, now, before)
        again[0] = None
        plain = ws.dock_trials(60, **f1)
        assert plain["docked"] == 60 and plain["worst_aside_m"] <= 0.03 and plain["come_agains"] > 0, plain
    finally:
        ws.load_board_walk = real


# ---- 21 Nov, review: what a come-again leg's stop means, the start gate's Q, the step's offset after the turn ----

def _come_again_with(monkeypatch, refuse, *, quality=70, scan=None, legs_end=((1.9, 0.07, 0.0),)):
    """_far_dock_legs with the come-again's OUT leg raising `refuse` (a board_walk leg's own words)."""
    import numpy as np

    backs = []

    def back_in(bw, w, base, did, a, k):
        backs.append(round(base.y, 3))
        did.append("back")
        return (True, None, False)

    bw, w, base, did = _far_dock_legs(monkeypatch, back_in, list(legs_end))
    first = w.leg

    def leg(step, i, through=False):
        if step.get("face") is None and refuse:
            did.append(("leg", [round(v, 2) for v in step["to"]], None))
            raise bw.Stop(refuse)
        return first(step, i, through)

    monkeypatch.setattr(w, "leg", leg)
    monkeypatch.setattr(base, "quality", lambda: quality)
    if scan is not None:
        monkeypatch.setattr(w, "scan", lambda: scan)
    return bw, w, base, did, backs


def test_a_come_again_leg_stopped_for_anything_but_not_getting_there_is_the_docks_stop(monkeypatch):
    """Review 21 Nov: `_come_again` swallowed every Stop -- the leg's own "localization quality 38" and a guest
    holding it through the patience too -- and the step, the start gate (no Q) and the 1.9 m blind reverse went on.
    Only the board ending the leg short of its point is carried on from."""
    from app import event_walk

    for words, kind in (("step 0: localization quality 38", "lost"), ("step 0: stuck", "blocked"),
                        ("no room to turn 90° here: the arms would pass 0.01 m from something", "blocked")):
        bw, w, base, did, backs = _come_again_with(monkeypatch, words)
        with pytest.raises(bw.Stop) as e:
            w.dock()
        assert str(e.value) == words and backs == [] and event_walk.kind_of(str(e.value)) == kind, (words, did)
    # the board gave up on the leg: carried on from -- the step (nothing ahead), then the reverse
    bw, w, base, did, backs = _come_again_with(monkeypatch, "step 0: ended 0.90 m from the point (the board gave up on it)")
    w.dock()
    assert ("turn", -90) in did and len(backs) == 1 and abs(backs[0]) <= bw.SQUARE_TOL_M, (did, backs)


def test_when_the_way_to_come_again_was_not_walked_the_phone_says_something_is_in_the_way(monkeypatch):
    """The board gave up on the come-again leg and the step had something ahead: no reverse from 7 cm aside, and
    the stop reads "in the way" (event_walk `blocked`) -- not "the dock failed, look behind the robot"."""
    import numpy as np
    from app import event_walk

    wall = np.array([[0.30 + 0.05, y] for y in np.arange(-0.3, 0.31, 0.02)])   # 5 cm ahead of her front face
    bw, w, base, did, backs = _come_again_with(monkeypatch, "step 0: ended 0.90 m from the point (the board gave up on it)",
                                               scan=wall)
    with pytest.raises(bw.Stop) as e:
        w.dock()
    assert backs == [] and "something in the way" in str(e.value) and "0.90 m short" in str(e.value), str(e.value)
    assert event_walk.kind_of(str(e.value)) == "blocked", str(e.value)
    # re-review 21 Nov: "blocked" came from the quoted leg saying "ended" -- the constant's own words carry it now
    alone = bw.NOT_COME_AGAIN % (0.9, 0.07, 1.9)
    assert "ended" not in alone and event_walk.kind_of(alone) == "blocked", alone
    # the same 7 cm with no come-again in it is the dock's own stop, as ever
    assert event_walk.kind_of("dock: not on the dock's line to reverse from (1.90 m out for 1.90, 0.07 m aside, 0.0° off) "
                              "-- not backing that far blind from there") == "dock"


def test_a_second_tries_stop_is_its_own_not_the_first_tries_come_again(monkeypatch):
    """Re-review 21 Nov: try 1's come-again leg ended short (carried on from), she met the dock and missed it; try 2's
    drive out ended 8 cm aside with something ahead of the step. That is the dock's stop ("look behind the robot"),
    not try 1's "something in the way"."""
    import numpy as np
    from app import event_walk

    bw, w, base, did, backs = _come_again_with(monkeypatch, "step 0: ended 0.90 m from the point (the board gave up on it)")
    wall = np.array([[0.30 + 0.05, y] for y in np.arange(-0.3, 0.31, 0.02)])
    ahead = [False]
    monkeypatch.setattr(w, "scan", lambda: wall if ahead[0] else np.zeros((0, 2)))
    tries = []

    def back_in(*a, **k):
        tries.append(round(base.y, 3))
        return (False, "the wheels stopped 1 cm short of the contacts", False)

    def drive_out(why, out_m=0.45):
        base.x, base.y, base.h = out_m, 0.08, 0.0
        ahead[0] = True

    monkeypatch.setattr(w, "_back_in", back_in)
    monkeypatch.setattr(w, "drive_out", drive_out)
    with pytest.raises(bw.Stop) as e:
        w.dock()
    assert len(tries) == 1 and abs(tries[0]) <= bw.SQUARE_TOL_M, tries
    assert "not on the dock's line to reverse from" in str(e.value) and event_walk.kind_of(str(e.value)) == "dock", str(e.value)


def test_the_long_reverse_does_not_start_on_a_pose_the_board_is_not_sure_of(monkeypatch):
    """Review 21 Nov: the start gate read where she was and never how sure the board was -- Q38 at the spot, on
    the line, was reversed from 1.9 m blind."""
    from app import event_walk

    bw, w, base, did, backs = _come_again_with(monkeypatch, None, quality=38, legs_end=(None,))
    with pytest.raises(bw.Stop, match="localization quality 38") as e:
        w.dock()
    assert backs == [] and event_walk.kind_of(str(e.value)) == "lost"
    bw, w, base, did, backs = _come_again_with(monkeypatch, None, quality=bw.MIN_QUALITY, legs_end=(None,))
    w.dock()
    assert backs == [0.0]


def test_the_step_onto_the_line_steps_the_offset_read_after_its_turn(monkeypatch):
    """Review 21 Nov, the n20j trace: +5.1 cm aside; the -89 deg turn moved the board's pose to -7.1 and the old
    step went the 5.1 cm read before it, to -12.8; the 180 deg turn left -2.4 -- inside the bar -- and the old step
    went 12.8 cm, to +10.5. Now the offset after each turn is what is stepped, and inside the bar is no step."""
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.051)
    moved = iter([-0.071 - 0.051, -0.024 + 0.071])                # what each turn did to the board's pose (n20j)

    def turn(yaw, tol_deg=1.0):
        turns.append(round(math.degrees(yaw)))
        base.h = yaw
        base.y += next(moved)

    monkeypatch.setattr(w, "turn_to", turn)
    w._square_up(0.0, 0.0, 0.0, None, 0.05)
    assert turns == [-90, 90] and base.y == pytest.approx(-0.024), (turns, base.y)        # no step at all
    # the turn takes some of it away: only what is left is stepped
    bw, w, base, turns, said = _square_up_from(monkeypatch, 0.10)

    def turn2(yaw, tol_deg=1.0):
        turns.append(round(math.degrees(yaw)))
        base.h = yaw
        base.y -= 0.04 if len(turns) == 1 else 0.0

    monkeypatch.setattr(w, "turn_to", turn2)
    w._square_up(0.0, 0.0, 0.0, None, 0.03)
    assert turns == [-90] and abs(base.y) <= 0.012, (turns, base.y)


def test_a_routes_turn_is_watched_and_may_be_slower(monkeypatch):
    """Second review 22 Nov: card 3 turns round at the counter, where people stand BEHIND her -- the sweep check
    before a turn reads the lidar, which does not see behind. A route's `do: turn` is watched while it turns: a
    board event (the bumper) or the action taken away (B / the robot's screen) stops it there; its `w_spin` is
    the cruise rate (kept within TURN_W_SPIN_*). Without w_spin: the validated 0.5 rad/s, as before."""
    import app.slamware_sdk as sw

    bw = _walk()

    def run(step, bumper_from=None, taken_from=None):
        board = types.SimpleNamespace(h=0.0, w=0.0, reads=0)
        clock = _Clock(on_sleep=lambda dt: setattr(board, "h", board.h + board.w * dt))
        monkeypatch.setattr(bw, "time", clock)
        asked = []

        class Board:
            def pose(self):
                return (0.0, 0.0, board.h)

            def events(self):
                board.reads += 1
                return [{"type": "BUMPER_TRIGGERED"}] if bumper_from is not None and board.reads >= bumper_from else []

        class Session:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                board.w = 0.0

            def set(self, vx, w_):
                board.w = w_
                asked.append(w_)
                return taken_from is not None and len(asked) >= taken_from

        monkeypatch.setattr(sw, "VelocitySession", Session)
        w = bw.Walker(Board(), {"dock": {"pose": [0, 0, 0]}}, False, False)
        w.sdk = object()
        monkeypatch.setattr(w, "scan", lambda: __import__("numpy").array([[3.0, 0.1 * k - 1.0] for k in range(21)]))
        try:
            w.step(step, 3)
            return board, asked, None
        except bw.Stop as e:
            return board, asked, str(e)

    board, asked, err = run({"do": "turn", "face": 180, "w_spin": 0.3})
    assert err is None and abs(abs(board.h) - math.pi) < math.radians(3) and max(map(abs, asked)) <= 0.3 + 1e-9
    board, asked, err = run({"do": "turn", "face": 180})
    assert err is None and max(map(abs, asked)) == pytest.approx(0.5)
    for asked_w, top in ((0.05, bw.TURN_W_SPIN_MIN), (2.0, bw.TURN_W_SPIN_MAX)):
        board, asked, err = run({"do": "turn", "face": 180, "w_spin": asked_w})
        assert err is None and max(map(abs, asked)) == pytest.approx(top), (asked_w, max(map(abs, asked)))
    # the bumper mid-turn: stopped there, the wheels told 0
    board, asked, err = run({"do": "turn", "face": 180, "w_spin": 0.3}, bumper_from=6)
    assert err and "board event" in err and asked[-1] == 0.0 and 0.0 < abs(board.h) < math.pi - 0.1
    # B / the robot's screen took the action: stopped, not taken up again
    board, asked, err = run({"do": "turn", "face": 180, "w_spin": 0.3}, taken_from=4)
    assert err and "taken away" in err and asked[-1] == 0.0 and abs(board.h) < math.pi - 0.1


def test_a_charger_deeper_than_the_dock_pose_is_reached_and_nothing_further_is_pushed_blind():
    """23 Nov, floor 1: lidar fits on the contacts 9-25 cm DEEPER than the file's dock pose; five map docks
    and the re-learn's back_to stopped "0.04-0.07 m past the dock pose and no contact". Past the overshoot she
    goes on slowly (DOCK_SEEK_M, asked) for the contacts: a charger 0.10 / 0.25 m deeper is met; 0.40 m is past
    the cap (stopped there, not tried again); a dock that never closes (no power, or something behind) stops
    the wheels and is not pushed into twice. DOCK_SEEK_M 0 is the old stop -- the night's misses again."""
    ws = _sim()
    f1 = dict(roll_off_deg=ws.ROLL_OFF_DEG, back_from=1.9, axis_offset_deg=ws.ROLL_OFF_DEG)
    real = ws.load_board_walk

    def trials(seek, **kw):
        def load():
            bw = real()
            if seek is not None:
                bw.DOCK_SEEK_M = seek
            return bw
        ws.load_board_walk = load
        try:
            return ws.dock_trials(4, **dict(f1, **kw))
        finally:
            ws.load_board_walk = real

    for deeper in (0.0, 0.10, 0.25):
        res = trials(None, charger_deeper_m=deeper)
        assert res["docked"] == 4 and res["worst_aside_m"] < 0.05, (deeper, res["stops"])
    old = trials(0.0, charger_deeper_m=0.10)                     # the stop at the overshoot: tonight's misses
    assert old["docked"] == 0 and all("past the dock pose and no contact" in r for _, r in old["stops"]), old["stops"]
    far = trials(None, charger_deeper_m=0.40)
    assert far["docked"] == 0 and far["second_tries"] == 0
    assert all("no further blind" in r and "not tried again" in r for _, r in far["stops"]), far["stops"]
    dead = trials(None, charger_deeper_m=0.10, contacts_miss=99)    # never closes: its body stops the wheels
    assert dead["docked"] == 0 and dead["second_tries"] == 0
    assert all("wheels stopped" in r and "past the dock pose" in r and "not tried again" in r for _, r in dead["stops"])
