"""Floor-1 remembered paths (scripts/board_walk.py, data/robot/waypoints/floor1.json)."""
import importlib.util
import json
import math
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
    monkeypatch.setattr(bw.time, "sleep", lambda s: None)

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
    monkeypatch.setattr(bw.time, "sleep", lambda s: None)
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
    monkeypatch.setattr(bw.time, "sleep", lambda s: None)
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
