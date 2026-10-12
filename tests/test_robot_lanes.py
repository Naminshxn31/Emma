"""Position fix from named walls (app/robot_lanes.py).

Synthetic scans are ray-cast against hand-made walls so the truth is exact;
two real frames from 6 ต.ค. 2026 pin the behaviour on the robot that matters.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from app import robot_lanes as rl

DATA = Path(__file__).parent / "data" / "robot"


def raycast(walls, pose, fov=math.radians(111), step=0.004, max_range=12.0, noise=0.0, seed=1):
    """Robot-frame lidar points for `pose` among `walls` (list of x1,y1,x2,y2)."""
    rng = np.random.default_rng(seed)
    x, y, yaw = pose
    W = np.array(walls, dtype=float)
    pts = []
    for a in np.arange(-fov, fov, step):
        dx, dy = math.cos(yaw + a), math.sin(yaw + a)
        best = max_range
        for x1, y1, x2, y2 in W:
            ex, ey = x2 - x1, y2 - y1
            den = dx * ey - dy * ex
            if abs(den) < 1e-12:
                continue
            t = ((x1 - x) * ey - (y1 - y) * ex) / den
            u = ((x1 - x) * dy - (y1 - y) * dx) / den
            if t > 0.05 and 0 <= u <= 1 and t < best:
                best = t
        if best < max_range:
            d = best + (rng.normal(0, noise) if noise else 0.0)
            pts.append((d * math.cos(a), d * math.sin(a)))
    return np.array(pts)


def real_scan(name):
    doc = json.loads((DATA / name).read_text(encoding="utf-8"))
    a, d = np.array(doc["angle"]), np.array(doc["distance"])
    keep = (d > 0.05) & (d < 12)
    return np.c_[d[keep] * np.cos(a[keep]), d[keep] * np.sin(a[keep])], tuple(doc["pose_used"])


CORRIDOR = [(-6, 0.0, 6, 0.0), (-6, 1.8, 6, 1.8)]
CORRIDOR_ANCHORS = {"south": rl.Anchor("y", 0.0, -6, 6), "north": rl.Anchor("y", 1.8, -6, 6)}


def test_a_corridor_corrects_across_it_and_leaves_along_it_alone():
    truth = (0.0, 0.9, 0.05)
    pts = raycast(CORRIDOR, truth, noise=0.005)
    fix = rl.correct_pose((0.3, 0.75, 0.0), pts, CORRIDOR_ANCHORS)
    assert fix.accepted, fix.reason
    assert fix.dy == pytest.approx(0.15, abs=0.02)
    assert fix.dyaw == pytest.approx(0.05, abs=0.01)
    # Nothing in a corridor says how far along it we are: the board's AMCL
    # guesses here, which is the jump this module exists to stop.
    assert fix.dx is None
    assert fix.pose[0] == pytest.approx(0.3)


ROOM = [(0, 0, 4, 0), (4, 0, 4, 3), (4, 3, 0, 3), (0, 3, 0, 0)]
ROOM_ANCHORS = {"south": rl.Anchor("y", 0, 0, 4), "east": rl.Anchor("x", 4, 0, 3),
                "north": rl.Anchor("y", 3, 0, 4), "west": rl.Anchor("x", 0, 0, 3)}


def test_a_room_corrects_both_axes_and_heading():
    truth = (1.5, 1.2, math.radians(20))
    pts = raycast(ROOM, truth, noise=0.005)
    fix = rl.correct_pose((1.7, 1.0, math.radians(14)), pts, ROOM_ANCHORS)
    assert fix.accepted, fix.reason
    assert fix.pose[0] == pytest.approx(1.5, abs=0.02)
    assert fix.pose[1] == pytest.approx(1.2, abs=0.02)
    assert fix.pose[2] == pytest.approx(math.radians(20), abs=math.radians(0.5))


def test_a_desk_near_a_wall_is_ignored_unless_that_wall_is_named():
    # A desk edge 0.35 m in front of the south wall: inside the gate of the
    # south wall. Named or not decides whether it can move y — the dock lesson.
    desk = [(1.0, 0.35, 3.0, 0.35)]
    truth = (2.0, 1.5, 0.0)
    pts = raycast(ROOM + desk, truth)
    only_east = rl.correct_pose((1.9, 1.5, 0.0), pts, {"east": ROOM_ANCHORS["east"]})
    assert only_east.accepted, only_east.reason
    assert only_east.dy is None and only_east.pose[1] == pytest.approx(1.5)
    assert only_east.dx == pytest.approx(0.1, abs=0.02)


def test_too_far_off_is_rejected_not_snapped():
    truth = (1.5, 1.2, 0.0)
    pts = raycast(ROOM, truth)
    fix = rl.correct_pose((1.5, 2.1, 0.0), pts, {"south": ROOM_ANCHORS["south"]})
    assert not fix.accepted
    assert fix.pose == (1.5, 2.1, 0.0)


def test_a_door_gap_pins_the_coordinate_along_its_wall():
    # Wall x = 3.375 with an opening from y -0.95 to -0.09 (D11's measured gap).
    wall = [(3.375, -3.0, 3.375, -0.95), (3.375, -0.09, 3.375, 1.0)]
    door = rl.Door("x", 3.375, -0.95, -0.09)
    truth = (2.75, -0.40, 0.0)
    pts = raycast(wall, truth)
    fix = rl.correct_pose((2.75, -0.52, 0.0), pts, {}, {"D11": door})
    assert fix.accepted, fix.reason
    assert fix.dy == pytest.approx(0.12, abs=0.02)
    assert fix.doors[0]["measured"]["width"] == pytest.approx(0.86, abs=0.03)


def test_real_dock_frame_fixes_x_and_heading_but_not_y():
    pts, truth = real_scan("scan_dock_fayarkarn_20261006.json")
    east = {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68)}
    fix = rl.correct_pose((-4.20, 1.43, 0.0), pts, east)
    assert fix.accepted, fix.reason
    assert fix.pose[0] == pytest.approx(truth[0], abs=0.03)
    assert fix.pose[2] == pytest.approx(truth[2], abs=math.radians(1.0))
    assert fix.dy is None


def test_real_dock_frame_does_not_take_the_desk_for_the_south_wall():
    # The desk edge in this frame is only 0.41 m long, so the default
    # min_len (0.5) already drops it — and a test resting on that would pass
    # with any gate at all (it did: gate 0.65 stayed green). Pull the short
    # runs out on purpose so this test is about the gate: the desk sits
    # 0.57 m inside the south wall and must stay outside a 0.40 m gate.
    pts, truth = real_scan("scan_dock_fayarkarn_20261006.json")
    segs = rl.extract_segments(pts, min_len=0.3, min_pts=15)
    world_y = [rl.points_to_world(truth, [(s.cx, s.cy)])[0][1] for s in segs]
    assert any(abs(y - (-1.0)) < 0.05 for y in world_y), "the desk edge should be in the frame"
    anchors = {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68),
               "ea_south": rl.Anchor("y", -1.583, -4.74, -2.80)}
    fix = rl.correct_pose(truth, pts, anchors, segments=segs)
    assert fix.accepted, fix.reason
    assert not any(m["anchor"] == "ea_south" for m in fix.matches)
    assert fix.dy is None


def test_real_d11_frame_finds_the_opening():
    pts, pose = real_scan("scan_d11_outside_20261006.json")
    fix = rl.correct_pose(pose, pts, {}, {"D11": rl.Door("x", 3.375, -0.946, -0.09)})
    assert fix.accepted, fix.reason
    assert fix.doors[0]["measured"]["width"] == pytest.approx(0.856, abs=0.04)
    assert abs(fix.dy) < 0.05


def test_side_clearance_sees_the_jambs_in_a_doorway():
    # A 0.856 m opening, robot centred 3 cm toward the left jamb, standing in it.
    jambs = [(-1.0, 0.458, 0.05, 0.458), (-1.0, -0.398, 0.05, -0.398)]   # walls along x, beside the robot
    pts = raycast(jambs, (0.0, 0.0, 0.0), fov=math.radians(111))
    left, right = rl.side_clearance(pts)
    assert left == pytest.approx(0.458 - rl.ROBOT_HALF_WIDTH, abs=0.01)
    assert right == pytest.approx(0.398 - rl.ROBOT_HALF_WIDTH, abs=0.01)


def test_side_clearance_ignores_a_single_stray_return():
    pts = np.array([[0.0, 0.29], [3.0, 0.0], [3.0, 0.1]])
    left, right = rl.side_clearance(pts)
    assert left is None and right is None


def test_real_frame_from_where_the_d11_push_hit_the_door():
    # Taken at 13:13 on 6 ต.ค. with the robot wedged in D11 after the contact:
    # the left side is 5-6 cm from the frame. A push must have stopped long
    # before this.
    doc = json.loads((DATA / "scan_d11_contact_20261006.json").read_text(encoding="utf-8"))
    a, d = np.array(doc["angle"]), np.array(doc["distance"])
    pts = np.c_[d * np.cos(a), d * np.sin(a)]
    left, right = rl.side_clearance(pts)
    # With the measured 0.78 m width the frame overlaps the arm: the
    # contact the owner saw. With the board's 0.55 m disc it read +5 cm.
    assert left is not None and left < 0
    assert rl.side_clearance(pts, half_width=0.275)[0] > 0.04


def test_the_measured_robot_barely_fits_the_head_office_doors():
    # D11 measured 0.856 m, robot 0.78 m across the arms: under 4 cm a side.
    assert rl.door_margin(0.856) == pytest.approx(0.038, abs=0.001)
    assert rl.TURN_SWEEP_RADIUS == pytest.approx(0.461, abs=0.001)


def test_a_short_slanted_run_inside_a_wall_gate_is_dropped_not_fatal():
    # 13:38 on 6 ต.ค.: the charger + column B4 made a 0.53 m run 8.7° off the
    # west wall, inside its gate; the 2.43 m east wall said 0.8°. The stop
    # used to be rejected outright ("walls disagree on heading").
    pts, guess = real_scan("scan_above_d8_charger_line_20261006.json")
    anchors = {"ea_east": rl.Anchor("x", -1.825, -0.6, 1.68),
               "ea_west": rl.Anchor("x", -4.74, -1.5, 1.4)}      # the old, too-long extent
    fix = rl.correct_pose(guess, pts, anchors)
    assert fix.accepted, fix.reason
    assert [d["anchor"] for d in fix.dropped] == ["ea_west"]
    assert math.degrees(fix.dyaw) == pytest.approx(-0.8, abs=0.5)
    assert fix.dx == pytest.approx(-1.825 - (-1.84 + -1.80) / 2, abs=0.03)


def test_front_distance_watches_the_robots_whole_width_not_a_cone():
    # A chair leg 0.35 m to the left, 0.6 m ahead: outside a narrow cone, inside a 0.78 m robot.
    leg = [(0.58, 0.33, 0.62, 0.33), (0.62, 0.33, 0.62, 0.37), (0.62, 0.37, 0.58, 0.37), (0.58, 0.37, 0.58, 0.33)]
    far_wall = [(3.5, -2, 3.5, 2)]
    pts = raycast(leg + far_wall, (0.0, 0.0, 0.0), step=0.002)
    assert rl.front_distance(pts) == pytest.approx(0.58, abs=0.02)
    assert rl.front_distance(raycast(far_wall, (0.0, 0.0, 0.0))) is None    # beyond 3 m


def test_gap_ahead_finds_the_opening_relative_to_the_robot():
    # Wall 0.7 m ahead with a 0.86 m opening whose middle is 0.10 m to the left.
    wall = [(0.7, -2.0, 0.7, 0.10 - 0.43), (0.7, 0.10 + 0.43, 0.7, 2.0), (3.0, -2.0, 3.0, 2.0)]
    g = rl.gap_ahead(raycast(wall, (0.0, 0.0, 0.0)))
    assert g is not None
    assert g["face"] == pytest.approx(0.7, abs=0.02)
    assert g["width"] == pytest.approx(0.86, abs=0.03)
    assert g["centre"] == pytest.approx(0.10, abs=0.02)


def test_gap_ahead_on_the_real_d11_frame():
    # Robot 0.26 m outside D11 at y -0.457; the measured gap is -0.946..-0.09,
    # middle -0.518 — so the opening's middle is ~6 cm to the robot's right.
    pts, pose = real_scan("scan_d11_outside_20261006.json")
    g = rl.gap_ahead(pts)
    assert g is not None
    assert g["width"] == pytest.approx(0.856, abs=0.04)
    assert g["centre"] == pytest.approx(-0.06, abs=0.03)


def test_gap_ahead_is_not_fooled_by_the_open_leaf_beside_the_door():
    # D8 from inside ฝ่ายอาคาร: the leaf stands at right angles to the wall on
    # the left edge of the opening, 0.75 m long, reaching toward the robot.
    wall = [(1.2, -2.0, 1.2, -0.43), (1.2, 0.43, 1.2, 2.0), (3.5, -2.0, 3.5, 2.0)]
    leaf = [(1.2, 0.47, 0.45, 0.47)]
    g = rl.gap_ahead(raycast(wall + leaf, (0.0, 0.0, 0.0), step=0.003))
    assert g is not None
    assert g["face"] == pytest.approx(1.2, abs=0.03)
    assert g["centre"] == pytest.approx(0.0, abs=0.03)


def test_gap_ahead_sees_a_closed_door_as_no_gap():
    wall = [(0.7, -2.0, 0.7, 2.0)]
    assert rl.gap_ahead(raycast(wall, (0.0, 0.0, 0.0))) is None


def test_route_file_mistakes_are_caught_before_the_robot_moves():
    base = {"anchors": {"a": {"axis": "x", "at": 0, "lo": 0, "hi": 1}}}
    with pytest.raises(ValueError, match="unknown anchor"):
        rl.parse_route({**base, "steps": [{"do": "checkpoint", "use": ["b"]}]})
    with pytest.raises(ValueError, match="straight lanes"):
        rl.parse_route({**base, "steps": [{"do": "checkpoint", "use": ["a"], "heading": 45}]})
    with pytest.raises(ValueError, match="must name"):
        rl.parse_route({**base, "steps": [{"do": "checkpoint"}]})
    route = rl.parse_route({**base, "steps": [{"do": "move", "to": [1, 2]},
                                               {"do": "checkpoint", "use": ["a"], "heading": -90}]})
    assert route.steps[1].heading_deg == -90


def test_a_diagonal_leg_is_refused_not_rounded():
    assert rl.lane_heading((0, 0), (3, 0.2)) == pytest.approx(0.0)
    assert rl.lane_heading((0, 0), (0, -2)) == pytest.approx(-math.pi / 2)
    assert rl.lane_heading((0, 0), (2, 2)) is None


def test_a_door_width_from_the_drawing_is_not_a_float_short_of_its_margin():
    # 8 ต.ค. 11:30, D11 in: -0.37 - -1.13 = 0.7599999999999999, 2.99999 cm a side
    # against the 3.0 needed for the 0.70 base -- the route ended at the door
    assert rl.door_margin((-0.37) - (-1.13), 0.70) >= 0.03
    assert rl.door_margin(0.80, 0.70) == 0.05
    assert rl.door_margin(0.759, 0.70) < 0.03


# -- the charger's surroundings, facing the dock (DockGap) ------------------------
DOCK_GAP = rl.DockGap(wall_x=-4.767, wall_spans=((0.70, 1.10), (1.85, 2.30)), depth_lo=0.070, depth_hi=0.109,
                      outer_offset=0.0225, centre_y=1.477)
# 8 ต.ค. 17:31, from a dock the robot backed onto itself: wall, face A, the gap (the
# wall 0.5 cm further back), face B, wall -- as segments (x1, y1, x2, y2)
DOCK_SCENE = [(-4.767, 0.65, -4.767, 1.125), (-4.697, 1.153, -4.697, 1.429), (-4.772, 1.429, -4.772, 1.529),
              (-4.658, 1.529, -4.658, 1.758), (-4.767, 1.825, -4.767, 2.30)]


def _dock_front():
    d = json.loads((DATA / "dock_front_scans_20261008.json").read_text(encoding="utf-8"))
    hx, hy, hyaw = d["home"]
    line_y = hy + (DOCK_GAP.wall_x + (DOCK_GAP.face_from + DOCK_GAP.face_to) / 2 - hx) * math.tan(hyaw)
    return d, line_y


def _dock_scene(drop_deg):
    """DOCK_SCENE with the gap's edges where they are, for a lidar losing
    `drop_deg` beside each edge: what read 0.10 m from 0.85 m is narrower."""
    k = 0.85 * math.tan(math.radians(drop_deg))
    (w1, a_, gw, b_, w2) = DOCK_SCENE
    return [w1, (a_[0], a_[1], a_[2], a_[3] + k), (gw[0], gw[1] + k, gw[2], gw[3] - k),
            (b_[0], b_[1] - k, b_[2], b_[3]), w2]


def _lidar(pose, scene, step_deg=0.102, drop_deg=1.0, legs=()):
    """A lidar at `pose` over `scene` segments and round `legs` ((x, y, diameter)):
    one return per `step_deg` ray, the nearest; none for `drop_deg` on the near
    side of every jump in range (each edge in the real frames had 0.7-1.4°)."""
    x0, y0, yaw = pose
    angs = np.radians(np.arange(-100.0, 100.0, step_deg))
    best = np.full(angs.size, np.inf)
    for x1, y1, x2, y2 in scene:
        n = int(max(abs(x2 - x1), abs(y2 - y1)) / 0.0005) + 2
        px, py = np.linspace(x1, x2, n) - x0, np.linspace(y1, y2, n) - y0
        a = (np.arctan2(py, px) - yaw + math.pi) % (2 * math.pi) - math.pi
        r = np.hypot(px, py)
        k = np.round((a - angs[0]) / math.radians(step_deg)).astype(int)
        ok = (k >= 0) & (k < angs.size)
        np.minimum.at(best, k[ok], r[ok])
    for lx, ly, diam in legs:
        dx, dy = lx - x0, ly - y0
        la = (math.atan2(dy, dx) - yaw + math.pi) % (2 * math.pi) - math.pi
        lr = math.hypot(dx, dy)
        half = math.asin(diam / 2 / lr)
        hit = np.abs(angs - la) <= half
        best[hit] = np.minimum(best[hit], lr - diam / 2)
    ok = np.isfinite(best)
    drop = np.zeros(angs.size, dtype=bool)
    idx = np.nonzero(ok)[0]
    for i, j in zip(idx[:-1], idx[1:]):
        if abs(best[j] - best[i]) > 0.03:
            near = i if best[i] < best[j] else j            # the nearer surface loses the rays by the edge
            side = 1.0 if near == i else -1.0             # rays further onto the near surface
            drop |= (side * (angs - angs[near]) <= 0) & (side * (angs - angs[near]) >= -math.radians(drop_deg))
    keep = ok & ~drop
    return np.c_[best[keep] * np.cos(angs[keep]), best[keep] * np.sin(angs[keep])]


def test_the_gap_beside_the_charger_reads_the_robot_across_the_dock_line():
    # 8 ต.ค.: nothing else in the dock's room measures y; the robot came to the
    # charger 4 cm aside and met the column. Two real looks from the pre-dock
    # spot: frame to frame they agree within a millimetre and a tenth of a degree.
    d, line_y = _dock_front()
    want = {"1725_hand_docked_start": (-0.0218, -0.16), "1731_self_docked_start": (-0.0139, 2.97)}
    for key, frames in d["captures"].items():
        for f in frames:
            fix = rl.dock_gap_fix(np.array(f["points"]), tuple(f["pose"]), DOCK_GAP, line_y)
            assert fix is not None, key
            assert fix["width"] == pytest.approx(0.10, abs=0.005) and fix["spread"] < 0.005
            assert fix["dy"] == pytest.approx(want[key][0], abs=0.0015)
            assert math.degrees(fix["dyaw"]) == pytest.approx(want[key][1], abs=0.1)
            assert abs(fix["dx"]) < 0.005
            assert fix["outer"] == pytest.approx(fix["centre"] - 0.023, abs=0.004)   # logged: the faces' far ends
            x, y, yaw = f["pose"]
            again = rl.dock_gap_fix(np.array(f["points"]), (x + fix["dx"], y + fix["dy"], yaw + fix["dyaw"]),
                                    DOCK_GAP, line_y)
            assert abs(again["dy"]) < 0.001 and abs(again["dx"]) < 0.001 and abs(again["dyaw"]) < math.radians(0.05)


def test_a_wrong_belief_comes_back_to_the_same_answer():
    # reviewer: read once from the believed pose, heading +6° and x +2 cm out left
    # 3° in it -- 8.7 cm across at 1.77 m. Read again until it settles: the same answer.
    d, line_y = _dock_front()
    f = d["captures"]["1731_self_docked_start"][0]
    pts, (x, y, yaw) = np.array(f["points"]), f["pose"]
    base = rl.dock_gap_fix(pts, (x, y, yaw), DOCK_GAP, line_y)
    for e in (-6, -3, 0, 3, 6):
        for ex in (-0.02, 0.0, 0.02):
            for ey in (-0.04, 0.0, 0.04):
                fix = rl.dock_gap_fix(pts, (x + ex, y + ey, yaw + math.radians(e)), DOCK_GAP, line_y)
                assert fix is not None, (e, ex, ey)
                assert (y + ey + fix["dy"]) - (y + base["dy"]) == pytest.approx(0.0, abs=0.002), (e, ex, ey)
                assert math.degrees(math.radians(e) + fix["dyaw"] - base["dyaw"]) == pytest.approx(0.0, abs=0.1)


def test_from_the_end_of_the_west_lane_the_gap_reads_as_well():
    # the approach look, ~1.8 m off: rays 0.102° apart are 3.2 mm there, but the
    # rays lost beside each edge are twice as wide as from the pre-dock spot --
    # the gap reads 3-4 cm wider; its centre stays
    hx, hy, hyaw = -4.544, 1.4522, -0.089
    line_y = hy + (DOCK_GAP.wall_x + 0.095 - hx) * math.tan(hyaw)
    near = _lidar((-3.85, 1.43, math.pi), _dock_scene(1.0), drop_deg=1.0)
    assert rl.dock_gap_fix(near, (-3.85, 1.43, math.pi), DOCK_GAP, line_y)["width"] == pytest.approx(0.10, abs=0.004)
    for true in ((-3.0, 1.43, math.pi), (-3.0, 1.40, math.radians(176)), (-3.2, 1.46, math.radians(184))):
        for drop in (0.7, 1.0, 1.4):
            pts = _lidar(true, _dock_scene(drop), drop_deg=drop)
            for e in (-4, 0, 4):
                for ey in (-0.03, 0.03):
                    fix = rl.dock_gap_fix(pts, (true[0], true[1] + ey, true[2] + math.radians(e)), DOCK_GAP, line_y)
                    assert fix is not None, (true, drop, e, ey)
                    # the robot is put where the gap's centre (1.479) is on the line
                    got = true[1] + ey + fix["dy"]
                    assert got - true[1] == pytest.approx(line_y - 1.479, abs=0.003), (true, drop, e, ey)


def test_a_chair_leg_s_shadow_is_not_taken_for_the_gap():
    # reviewer: 107 of ~20k legs made a 10 cm hole in a face that was taken for the
    # gap, 11-19 cm off -- one with no odometry error at all (6 cm at (-4.35, 1.36))
    hx, hy, hyaw = -4.544, 1.4522, -0.089
    line_y = hy + (DOCK_GAP.wall_x + 0.095 - hx) * math.tan(hyaw)
    wrong = []
    for true in ((-3.85, 1.39, math.radians(177)), (-3.0, 1.43, math.pi)):       # pre-dock spot, west lane's end
        for diam in (0.03, 0.06):
            for lx in (-4.50, -4.35, -4.20):
                for ly in np.arange(1.16, 1.80, 0.02):
                    pts = _lidar(true, _dock_scene(1.0), legs=[(lx, ly, diam)])
                    for ey in (-0.03, 0.0, 0.03):
                        fix = rl.dock_gap_fix(pts, (true[0], true[1] + ey, true[2]), DOCK_GAP, line_y)
                        if fix is not None and abs(ey + fix["dy"] - (line_y - 1.479)) > 0.01:
                            wrong.append((true[0], diam, lx, round(float(ly), 2), ey))
    assert wrong == []
    true = (-3.85, 1.39, math.radians(177))
    pts = _lidar(true, _dock_scene(1.0), legs=[(-4.35, 1.36, 0.06)])
    fix = rl.dock_gap_fix(pts, true, DOCK_GAP, line_y)
    assert fix is None or abs(fix["dy"] - (line_y - 1.479)) < 0.01


def test_no_gap_plainly_there_is_no_fix():
    d, line_y = _dock_front()
    f = d["captures"]["1731_self_docked_start"][0]
    pts, pose = np.array(f["points"]), tuple(f["pose"])
    c, s = math.cos(pose[2]), math.sin(pose[2])
    wx, wy = pose[0] + c * pts[:, 0] - s * pts[:, 1], pose[1] + s * pts[:, 0] + c * pts[:, 1]
    face_b = (np.abs(wx + 4.658) < 0.03) & (wy > 1.5) & (wy < 1.8)
    assert rl.dock_gap_fix(pts[~face_b], pose, DOCK_GAP, line_y) is None          # half the gap is not a gap
    for across in (0.0, -0.05):                         # a leg standing in it, at one edge or the middle
        leg = np.c_[np.full(30, 0.86), np.linspace(across - 0.03, across + 0.03, 30)]
        assert rl.dock_gap_fix(np.vstack([pts, leg]), pose, DOCK_GAP, line_y) is None, across
    north = wy > 1.80                                       # one stretch of the wall out of sight
    assert rl.dock_gap_fix(pts[~north], pose, DOCK_GAP, line_y) is None
    away = (pose[0], pose[1], pose[2] + math.radians(40))                          # not facing the dock
    assert rl.dock_gap_fix(pts, away, DOCK_GAP, line_y) is None
    why = []
    far = (pose[0], pose[1] + 0.10, pose[2])           # odometry 10 cm out: plainly the gap, but no guess
    assert rl.dock_gap_fix(pts, far, DOCK_GAP, line_y, why=why) is None
    assert why and why[-1].startswith("far:")


def test_odometry_out_either_way_is_counted_from_where_the_gap_is():
    # reviewer: counted from the board's home line (1.4-1.6 cm south of the gap) the
    # window was -7.0..+4.5 cm; 4.5 cm south read "no gap" and the robot backed in
    # on odometry 6 cm off. From where the gap is (centre_y): the same either way.
    d, line_y = _dock_front()
    f = d["captures"]["1731_self_docked_start"][0]
    pts, (x, y, yaw) = np.array(f["points"]), f["pose"]
    base = rl.dock_gap_fix(pts, (x, y, yaw), DOCK_GAP, line_y)
    for ey in (-0.065, -0.05, 0.05, 0.065):
        fix = rl.dock_gap_fix(pts, (x, y + ey, yaw), DOCK_GAP, line_y)
        assert fix is not None, ey
        assert ey + fix["dy"] == pytest.approx(base["dy"], abs=0.002), ey
    for ey in (-0.08, 0.08):
        why = []
        assert rl.dock_gap_fix(pts, (x, y + ey, yaw), DOCK_GAP, line_y, why=why) is None
        assert why[-1].startswith("far:"), (ey, why)


def test_a_route_can_name_the_dock_gap():
    doc = {"name": "t", "anchors": {"w": {"axis": "x", "at": 0.0, "lo": 0, "hi": 1}}, "doors": {},
           "steps": [{"do": "checkpoint", "use": ["w"]}],
           "dock_gap": {"wall_x": -4.767, "wall_spans": [[0.7, 1.1], [1.85, 2.3]], "offset": 0.005,
                        "depth_lo": 0.07, "depth_hi": 0.109}}
    g = rl.parse_route(doc).dock_gap
    assert g.wall_x == -4.767 and g.wall_spans == ((0.7, 1.1), (1.85, 2.3)) and g.offset == 0.005
    assert g.width == 0.10 and (g.depth_lo, g.depth_hi) == (0.07, 0.109)
    assert rl.parse_route({k: v for k, v in doc.items() if k != "dock_gap"}).dock_gap is None


def _remember_doc(**over):
    doc = {"name": "t", "anchors": {"w": {"axis": "x", "at": 0.0, "lo": 0, "hi": 1}},
           "doors": {"D8": {"axis": "y", "at": -1.633, "lo": -2.85, "hi": -2.05}},
           "steps": [{"do": "checkpoint", "use": ["w"]},
                     {"do": "door", "door": "D8", "heading": 90, "until": 0.25, "remember": True},
                     {"do": "door", "door": "D8", "heading": -90, "until": -2.45}],
           "door_memory_seed": {"_note": "a note", "D8@90": [-2.3805, -2.391, -2.3982]}}
    doc.update(over)
    return doc


def test_a_door_step_may_remember_its_middle_and_the_route_seed_it():
    r = rl.parse_route(_remember_doc())
    assert [s.remember for s in r.steps] == [False, True, False]
    assert r.door_seed == {"D8@90": (-2.3805, -2.391, -2.3982)}            # the note is not a door
    assert rl.door_key("D8", 90.0) == "D8@90" and rl.door_key("D8", -90) == "D8@-90"
    assert rl.parse_route({k: v for k, v in _remember_doc().items() if k != "door_memory_seed"}).door_seed == {}


@pytest.mark.parametrize("over", [
    {"door_memory_seed": {"D9@90": [-2.39]}},                       # not this route's door
    {"door_memory_seed": {"D8@45": [-2.39]}},                       # not a lane heading
    {"door_memory_seed": {"D8@90": ["-2.39"]}},
    {"door_memory_seed": {"D8@90": []}},
    {"steps": [{"do": "checkpoint", "use": ["w"], "remember": True}]},   # only a door remembers
    {"steps": [{"do": "door", "door": "D8", "heading": 90, "until": 0.25, "remember": "yes"}]},
])
def test_a_route_that_seeds_or_remembers_wrongly_is_refused_before_anything_moves(over):
    with pytest.raises(ValueError):
        rl.parse_route(_remember_doc(**over))


def test_the_wall_behind_the_dock_alone_reads_what_the_gap_s_look_read():
    # --dock-turn-test reads the heading facing the dock from the wall alone: the same
    # numbers as the whole look's, on both real captures
    d, line_y = _dock_front()
    for key, frames in d["captures"].items():
        for f in frames:
            pts, pose = np.array(f["points"]), tuple(f["pose"])
            whole, wall = rl.dock_gap_fix(pts, pose, DOCK_GAP, line_y), rl.dock_wall_fix(pts, pose, DOCK_GAP)
            assert (wall["dx"], wall["dyaw"], wall["wall_n"]) == (whole["dx"], whole["dyaw"], whole["wall_n"]), key
    why = []
    assert rl.dock_wall_fix(np.zeros((10, 2)), (-3.85, 1.43, math.pi), DOCK_GAP, why=why) is None
    assert why == ["too few returns"]


def test_no_gap_says_what_the_holes_of_its_width_were_refused_for():
    # 9 ต.ค.: both looks of the round trip said "no gap" and nothing else; the frames of
    # 8 ต.ค. seen from the same two poses find it 11/11 -- so the scene: say what about
    # it. Here the charger pushed 3 cm toward the wall: face B no longer at its depth.
    line_y = _min_line()
    gap = rl.DockGap(wall_x=-4.767, wall_spans=((0.70, 1.10), (1.85, 2.30)), depth_lo=0.070, depth_hi=0.109)
    scene = _dock_scene(1.0)
    pushed = [scene[0], scene[1], scene[2], (scene[3][0] - 0.03, scene[3][1], scene[3][2] - 0.03, scene[3][3]),
              scene[4]]
    why = []
    assert rl.dock_gap_fix(_lidar(PRE_DOCK, pushed), PRE_DOCK, gap, line_y, why=why) is None
    assert why[-1].startswith("no gap (") and "face north of it -3.0 cm off its depth" in why[-1], why
    why = []
    assert rl.dock_gap_fix(_lidar(PRE_DOCK, scene), PRE_DOCK, gap, line_y, why=why) is not None and why == []


# Each guard on its own: a DockGap with only the guard under test, so the others
# (above all outer_offset, which catches most of these first) do not hide it.
MIN_GAP = rl.DockGap(wall_x=-4.767, wall_spans=((0.70, 1.10), (1.85, 2.30)))
PRE_DOCK = (-3.85, 1.43, math.pi)


def _min_line():
    return 1.4522 + (MIN_GAP.wall_x + 0.095 + 4.544) * math.tan(-0.089)


def test_guard_a_cluttered_wall_is_not_read_for_the_heading():
    # a box 3 cm out from the wall over most of one stretch: the line through it
    # is not the wall's, and the heading it gives turns the whole answer
    line_y = _min_line()
    clean = _lidar(PRE_DOCK, _dock_scene(1.0))
    assert rl.dock_gap_fix(clean, PRE_DOCK, MIN_GAP, line_y) is not None
    boxed = _lidar(PRE_DOCK, _dock_scene(1.0) + [(-4.735, 0.78, -4.735, 1.06)])
    assert rl.dock_gap_fix(boxed, PRE_DOCK, MIN_GAP, line_y) is None


def test_guard_two_gaps_the_charger_s_size_are_no_answer():
    # a second 10 cm hole (here in face B) near enough the line to count: which is
    # the charger's is a guess
    line_y = _min_line()
    scene = _dock_scene(1.0)
    b = scene[3]
    notched = scene[:3] + [(b[0], b[1], b[2], 1.60), (b[0], 1.70, b[2], b[3])] + scene[4:]
    pts = _lidar(PRE_DOCK, notched)
    assert rl.dock_gap_fix(pts, PRE_DOCK, MIN_GAP, line_y, max_dy=0.30) is None
    assert rl.dock_gap_fix(_lidar(PRE_DOCK, scene), PRE_DOCK, MIN_GAP, line_y, max_dy=0.30) is not None


def test_guard_an_edge_must_be_an_unbroken_run_of_face():
    # returns every 2 cm by the edge (a chair's mesh, a grille) are not a face
    line_y = _min_line()
    pts = _lidar(PRE_DOCK, _dock_scene(1.0))
    c, s = math.cos(PRE_DOCK[2]), math.sin(PRE_DOCK[2])
    wy = PRE_DOCK[1] + s * pts[:, 0] + c * pts[:, 1]
    wx = PRE_DOCK[0] + c * pts[:, 0] - s * pts[:, 1]
    by_edge = (np.abs(wx + 4.697) < 0.02) & (wy > 1.36) & (wy < 1.45)
    keep = ~by_edge | (np.round(wy / 0.02) * 0.02 - wy < 0.0012)
    keep &= ~by_edge | (np.abs(np.round(wy / 0.02) * 0.02 - wy) < 0.0012)
    assert rl.dock_gap_fix(pts[keep], PRE_DOCK, MIN_GAP, line_y) is None


@pytest.mark.parametrize("side", ["lo", "hi"])
def test_guard_an_edge_must_be_its_own_face(side):
    # a box standing 13 cm out from the wall, in front of the face beside the gap,
    # makes the edge: the gap measured between it and the other face is off
    line_y = _min_line()
    gap = rl.DockGap(wall_x=-4.767, wall_spans=((0.70, 1.10), (1.85, 2.30)), depth_lo=0.070, depth_hi=0.109)
    box = (-4.637, 1.25, -4.637, 1.43) if side == "lo" else (-4.637, 1.543, -4.637, 1.70)
    pts = _lidar(PRE_DOCK, _dock_scene(1.0) + [box])
    assert rl.dock_gap_fix(pts, PRE_DOCK, gap, line_y) is None


def test_guard_something_in_front_of_the_edges_hides_them():
    # a 6 cm box 0.45 m in front of the faces, its shadow on face A's edge: read
    # anyway, 2.2 cm off
    line_y = _min_line()
    pts = _lidar(PRE_DOCK, _dock_scene(1.0) + [(-4.30, 1.415, -4.30, 1.475)])
    assert rl.dock_gap_fix(pts, PRE_DOCK, MIN_GAP, line_y) is None
