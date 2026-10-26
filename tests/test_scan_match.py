"""Lidar-map localization (app/scan_match.py).

Synthetic rooms, ray-cast so the truth is exact (the same caster as
test_robot_lanes). What has to hold on the robot: the match finds the pose it
was started off from, says which axes a corridor cannot pin, keeps a passer-by
out of the map, never wraps round the grid, and refine() moves only the axes
no anchor measured.
"""
import math

import numpy as np
import pytest

from app import scan_match as sm
from tests.test_robot_lanes import raycast

#: A furnished room, 6 x 4 m, door gap in the south wall, a cabinet and a desk
#: -- the kind of room where the plan (an empty box) fails and our map has to
#: be the thing it matches.
ROOM = [
    (0, 0, 2.5, 0), (3.4, 0, 6, 0),          # south wall, door 2.5-3.4
    (6, 0, 6, 4), (6, 4, 0, 4), (0, 4, 0, 0),
    (0.0, 2.6, 0.6, 2.6), (0.6, 2.6, 0.6, 3.5), (0.6, 3.5, 0.0, 3.5),   # cabinet on the west wall
    (3.8, 1.6, 4.9, 1.6), (4.9, 1.6, 4.9, 2.2), (4.9, 2.2, 3.8, 2.2), (3.8, 2.2, 3.8, 1.6),  # desk
]
#: A bare corridor: two long walls, nothing along it.
CORRIDOR = [(-10, 0, 10, 0), (-10, 1.8, 10, 1.8)]


def _map(walls, poses, runs=("a", "b")):
    frames = [(run, raycast(walls, p, seed=i + 7 * j), p) for j, run in enumerate(runs) for i, p in enumerate(poses)]
    grid = sm.grid_for([(pts, p) for _, pts, p in frames])
    return grid, sm.Field.from_occupancy(grid, sm.occupancy(grid, frames))


ROOM_POSES = [(1.5, 1.2, 0.0), (4.5, 1.0, math.pi / 2), (3.0, 3.0, math.pi), (1.2, 2.0, -math.pi / 2)]


def test_it_finds_the_pose_it_was_started_off_from():
    grid, field = _map(ROOM, ROOM_POSES)
    truth = (2.2, 1.4, math.radians(20))
    pts = raycast(ROOM, truth, noise=0.005, seed=99)
    for off in ((0.25, -0.2, 7), (-0.3, 0.25, -8), (0.0, 0.0, 0.0)):
        prior = np.array(truth) + np.array([off[0], off[1], math.radians(off[2])])
        m = sm.search(field, sm.voxel(pts), prior)
        assert abs(m.pose[0] - truth[0]) < 0.015 and abs(m.pose[1] - truth[1]) < 0.015, (off, m.pose)
        assert abs(math.degrees(m.pose[2] - truth[2])) < 0.5, (off, m.pose)
        assert all(m.pinned()), m.sharp


def test_a_corridor_does_not_pin_its_own_length_and_apply_keeps_the_wheels_there():
    poses = [(x, 0.9, 0.0) for x in (-4, -2, 0, 2, 4)]
    grid, field = _map(CORRIDOR, poses)
    truth = (0.7, 0.85, math.radians(3))
    prior = np.array([0.9, 0.95, 0.0])                    # wheels 20 cm long, 10 cm across
    m = sm.search(field, sm.voxel(raycast(CORRIDOR, truth, seed=5)), prior)
    sx, sy, syaw = m.sharp
    assert sx < sm.SHARP_MIN, m.sharp                    # along the corridor: flat
    assert sy >= sm.SHARP_MIN and syaw >= sm.SHARP_MIN, m.sharp
    out = m.apply()
    assert out[0] == pytest.approx(prior[0])             # not "corrected" to wherever the flat peak fell
    assert out[1] == pytest.approx(truth[1], abs=0.015)
    assert math.degrees(out[2]) == pytest.approx(3.0, abs=0.5)


def test_a_passer_by_seen_in_one_run_is_not_map():
    grid = sm.Grid(-1.0, -1.0, 800, 600)
    wall = np.c_[np.linspace(0, 5, 200), np.zeros(200)]          # robot frame = world (pose 0)
    person = np.array([[2.0, 2.0], [2.02, 2.0], [2.0, 2.02]])
    occ = sm.occupancy(grid, [("run1", np.vstack([wall, person]), (0, 0, 0)), ("run2", wall, (0, 0, 0))])
    r, c = grid.cells(person)
    assert not occ[r, c].any()
    r, c = grid.cells(wall[50:150])
    assert occ[r, c].all()


def test_returns_past_the_grid_score_nothing_not_the_far_side():
    grid = sm.Grid(0.0, 0.0, 200, 200)
    occ = np.zeros((200, 200), bool)
    occ[:, 0] = True                                   # a wall along the grid's west edge
    field = sm.Field.from_occupancy(grid, occ)
    # 1.99 m east of the grid's east edge -- an index that wrapped would land on the wall
    assert field.score(np.array([[3.99, 1.0]]), (0, 0, 0)) == 0.0
    assert field.score(np.array([[0.0, 1.0]]), (0, 0, 0)) == pytest.approx(1.0)


def test_too_few_returns_is_no_answer():
    grid, field = _map(ROOM, ROOM_POSES)
    m = sm.search(field, np.zeros((5, 2)), (1, 1, 0))
    assert m.score == 0.0 and not any(m.pinned())
    assert list(m.apply()) == [1, 1, 0]


def test_refine_moves_only_the_axes_nobody_measured():
    # four runs see the room from the same spots; one stop's y was never
    # measured and the wheels left it 0.15 m out -- refine must bring y back
    # and leave its measured x and heading exactly where the anchors put them
    poses_true = {("r%d" % j, i): p for j in range(4) for i, p in enumerate(ROOM_POSES)}
    frames = {k: (k[0], raycast(ROOM, p, noise=0.003, seed=n)) for n, (k, p) in enumerate(poses_true.items())}
    bad = ("r2", 1)
    logged = dict(poses_true)
    logged[bad] = (poses_true[bad][0] + 0.004, poses_true[bad][1] + 0.15, poses_true[bad][2])
    measured = {k: (True, True, True) for k in frames}
    measured[bad] = (True, False, True)
    grid = sm.grid_for([(frames[k][1], logged[k]) for k in frames])
    out = sm.refine(frames, logged, measured, grid, passes=2)
    assert out[bad][1] == pytest.approx(poses_true[bad][1], abs=0.02)
    assert out[bad][0] == logged[bad][0] and out[bad][2] == logged[bad][2]
    for k in frames:
        if k != bad:
            assert tuple(out[k]) == tuple(logged[k])


def test_the_scan_from_the_log_keeps_the_robots_shell_out():
    pts = sm.scan_points([0.0, 0.5, 1.0], [0.03, 2.0, 11.0])
    assert pts.shape == (1, 2)
    assert pts[0, 0] == pytest.approx(2.0 * math.cos(0.5))
