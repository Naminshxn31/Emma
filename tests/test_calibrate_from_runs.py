"""scripts/calibrate_from_runs.py: door middles and odometry constants from
drive_route's run logs -- exact numbers from small synthetic logs, outliers
dropped, and no constant where the evidence is too thin (the rule that keeps
a guess out of drive_route)."""
import importlib.util
import math
from pathlib import Path

import pytest


def _cal():
    path = Path(__file__).resolve().parents[1] / "scripts" / "calibrate_from_runs.py"
    spec = importlib.util.spec_from_file_location("calibrate_from_runs_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROUTE = {
    "name": "test route",
    "dock_pose": [-4.3, 1.43, 0.0],
    "doors": {"D8": {"axis": "y", "at": -1.633, "lo": -2.85, "hi": -2.05, "leaf": "hi"}},
    "steps": [
        {"do": "checkpoint", "label": "on the dock"},
        {"do": "drive", "label": "south onto D8's line", "to": [-2.44, 0.25]},
        {"do": "door", "label": "D8 out", "door": "D8", "heading": -90, "until": -2.45},
    ],
}


def _door_trace(x_robot, left, right, n=10):
    """A velocity door pass south: `n` ticks with both jambs beside (the box's
    left/right gaps), then out of the frame."""
    rows = []
    for k in range(n):
        y = -1.5 - 0.05 * k
        rows.append([k * 0.1, x_robot, y, -90.0, -90.0, None, None, left, right, 0.06, 0.0, 0, left, right, 0.0])
    rows.append([n * 0.1, x_robot, -2.4, -90.0, -90.0, None, None, 1.5, 1.5, 0.06, 0.0, 0, 1.5, 1.5, 0.0])
    return rows


def _d8_out_run(stamp, off, arrival=-2.397, frame=(0.08, 0.06), closest=(0.086, 0.066), t0=0.0):
    """One D8-out pass: the lane south ends at `arrival` (asked for -2.44), the
    aim steps `off` sideways, the jambs beside read `frame` (left, right)."""
    return {"name": "%s_floor2_bigroom_roundtrip" % stamp, "stamp": stamp, "source": "jsonl", "events": [
        {"kind": "run_start", "route": "test route", "door_width": 0.7, "t": t0},
        {"kind": "step", "i": 2, "do": "drive", "label": "south onto D8's line", "t": t0 + 1},
        {"kind": "drive", "mode": "velocity", "label": "south onto D8's line", "to": [-2.44, 0.25],
         "end": [arrival, 0.25, -math.pi / 2], "trace": [[arrival, 1.0, -90.0], [arrival, 0.25, -90.0]], "t": t0 + 2},
        {"kind": "step", "i": 3, "do": "door", "label": "D8 out", "t": t0 + 3},
        {"kind": "door_aim", "door": "D8", "pose": [arrival, 0.25, -math.pi / 2],
         "gap": {"face": 1.9, "width": 0.8, "centre": 0.01, "edges": 1}, "t": t0 + 4},
        {"kind": "door_step_across", "door": "D8", "off": off, "t": t0 + 5},
        {"kind": "door_aim", "door": "D8", "pose": [-2.45, 0.2, -math.pi / 2],
         "gap": {"face": 1.85, "width": 0.8, "centre": 0.0, "edges": 1}, "t": t0 + 6},
        {"kind": "door", "mode": "velocity", "door": "D8", "ok": True, "closest": list(closest),
         "trace": _door_trace(-2.45, *frame), "t": t0 + 20},
    ]}


# -- small statistics -------------------------------------------------------

def test_robust_drops_the_outlier_and_reports_it():
    c = _cal()
    s = c.robust([0.048, 0.050, 0.052, 0.20], floor=0.02)
    assert s["n"] == 3 and s["dropped"] == [0.20]
    assert s["median"] == pytest.approx(0.050)
    assert s["mad"] == pytest.approx(1.4826 * 0.002)


def test_robust_never_lets_three_outvote_one():
    s = _cal().robust([1.0, 2.0, 100.0])
    assert s["n"] == 3 and s["dropped"] == [] and s["median"] == 2.0


def test_confidence_levels():
    c = _cal()
    assert c.confidence(2, 1.0, [0.5, 1.5]) == "insufficient"
    assert c.confidence(5, 0.1, [-0.1, 0.3]) == "low"
    assert c.confidence(5, 1.0, [0.2, 1.8]) == "medium"
    assert c.confidence(9, 1.0, [0.6, 1.4]) == "high"


# -- doors ------------------------------------------------------------------

def test_the_jambs_beside_put_the_middle_where_the_sides_say():
    c = _cal()
    # heading south: the robot's left is +x. Left gap 8 cm, right 6 cm: the robot is
    # 1 cm right of the middle, so the middle is 1 cm to its left, at -2.44
    f = c.in_frame({"trace": _door_trace(-2.45, 0.08, 0.06)}, 270, "x", 0.70)
    assert f["mid"] == pytest.approx(-2.44)
    assert f["width"] == pytest.approx(0.84)
    assert f["entry_off"] == pytest.approx(-0.01)
    # a corridor beside the robot is no doorway
    assert c.in_frame({"trace": _door_trace(-2.45, 0.4, 0.4)}, 270, "x", 0.70) is None


def test_a_step_taken_run_after_run_moves_the_lane_by_it():
    c = _cal()
    runs = [_d8_out_run("20261009-10%02d00" % k, off, t0=1000.0 * k)
            for k, off in enumerate([0.050, 0.048, 0.052, 0.20])]     # 0.20: someone in the doorway
    cal = c.calibrate(runs, ROUTE, doors_since="")
    g = cal["doors"]["D8 out"]
    assert g["stepped_sideways"] == 4 and g["first_aims"] == 4
    assert g["first_step_off"]["dropped"] == [0.2]
    lane = g["recommendation"]["lane"]
    # off + = the robot left of the middle; facing south left is +x, so the lane moves -x
    assert lane["step"] == 2 and lane["coord"] == "x"
    assert lane["proposed"] == pytest.approx(-2.49)
    assert lane["delta"] == pytest.approx(-0.05)
    assert [ln["door"] for ln in cal["proposal"]["lanes"]] == ["D8 out"]
    # where the lane must end by the jambs: middle -2.44 less the lane's own +4.3 cm
    assert lane["by_jambs"] == pytest.approx(-2.44 - (-2.397 + 2.44))
    rm = g["recommendation"]["remember_middle"]
    assert rm["at"] == pytest.approx(-2.44) and rm["from_route_mid"] == pytest.approx(0.01)


def test_two_runs_are_not_enough_to_move_a_lane():
    c = _cal()
    runs = [_d8_out_run("20261009-10%02d00" % k, 0.05, t0=1000.0 * k) for k in range(2)]
    cal = c.calibrate(runs, ROUTE, doors_since="")
    lane = cal["doors"]["D8 out"]["recommendation"]["lane"]
    assert "proposed" not in lane and lane["why_not"] == "2 sample(s), 3 needed"
    assert cal["proposal"]["lanes"] == []


def test_steps_that_disagree_move_nothing():
    c = _cal()
    runs = [_d8_out_run("20261009-10%02d00" % k, off, t0=1000.0 * k)
            for k, off in enumerate([0.05, -0.04, 0.06, -0.05, 0.03])]
    lane = c.calibrate(runs, ROUTE, doors_since="")["doors"]["D8 out"]["recommendation"]["lane"]
    assert "proposed" not in lane


def test_runs_before_the_frame_changed_are_left_out():
    c = _cal()
    old = [_d8_out_run("20261007-10%02d00" % k, 0.05, t0=1000.0 * k) for k in range(3)]
    new = [_d8_out_run("20261009-10%02d00" % k, 0.05, t0=9000.0 + 1000.0 * k) for k in range(2)]
    g = c.calibrate(old + new, ROUTE, doors_since="20261008")["doors"]["D8 out"]
    assert g["passes_all"] == 5 and g["passes"] == 2
    assert "proposed" not in g["recommendation"]["lane"]


def test_no_middle_to_remember_that_moves_the_robot_toward_its_close_side():
    """D11: the frame's middle is not the passage's -- centring on the jambs
    moved the robot toward the leaf, already the closer side."""
    c = _cal()
    # the robot stands 3 cm right of the jambs' middle (left 0.105, right 0.045) and
    # passes closer on its left (the leaf past the frame): L 3 cm, R 5 cm
    runs = [_d8_out_run("20261009-10%02d00" % k, 0.05, frame=(0.105, 0.045), closest=(0.03, 0.05), t0=1000.0 * k)
            for k in range(3)]
    g = c.calibrate(runs, ROUTE, doors_since="")["doors"]["D8 out"]
    rm = g["recommendation"]["remember_middle"]
    assert "at" not in rm and "toward its L side" in rm["why_not"]
    # to even the two sides the line moves 1 cm right
    assert g["closest"]["0.70"]["balance_left_m"] == pytest.approx(-0.01)


# -- odometry -----------------------------------------------------------------

def _fix(label, pose, dyaw_deg, t, walls=("w1",)):
    return {"kind": "checkpoint", "label": label, "accepted": True, "pose": list(pose), "guess": list(pose),
            "dx": 0.0, "dy": None, "dyaw": math.radians(dyaw_deg), "matches": [{"anchor": w} for w in walls], "t": t}


def _turn(start_deg, deg, t):
    n = 10
    trace = [[0.1 * k, start_deg + deg * k / n, 0.3 if deg > 0 else -0.3, 1] for k in range(n)]
    trace.append([1.0, start_deg + deg, "settled"])
    return {"kind": "turn", "mode": "velocity", "to_deg": start_deg + deg, "off_deg": 0.0, "trace": trace, "t": t}


def _turn_run(k, turns, dyaw_deg, where=(0.0, 0.0)):
    ev = [{"kind": "run_start", "route": "other", "t": 0.0}, _fix("spot", (*where, 0.0), 0.0, 1.0)]
    h = 0.0
    for i, d in enumerate(turns):
        ev.append(_turn(h, d, 2.0 + i))
        h += d
    ev.append(_fix("spot", (*where, 0.0), dyaw_deg, 10.0))
    return {"name": "2026100%d-120000_turns" % k, "stamp": "2026100%d-120000" % k, "source": "jsonl", "events": ev}


def test_full_turns_at_one_spot_give_a_turn_scale():
    c = _cal()
    # two CCW half turns, the same walls after as before: +0.72 deg each run
    runs = [_turn_run(k, [180.0, 180.0], 0.72) for k in range(1, 4)]
    cal = c.calibrate(runs, ROUTE, doors_since="")
    g = cal["odometry"]["spot_turns_same_view"]["one way CCW"]
    assert g["n"] == 3 and g["per_deg"]["k"] == pytest.approx(0.002)
    p = cal["proposal"]["TURN_SCALE_CCW"]
    assert p["value"] == pytest.approx(round(1 / 1.002, 4))
    assert cal["proposal"]["TURN_SCALE_CW"]["value"] == 1.0


def test_a_turn_between_two_different_spots_is_not_a_turn_sample():
    """Between different walls the fix is turn + the two wall sets'
    disagreement (8 ต.ค. "above D8" facing north): never a turn constant."""
    c = _cal()
    runs = []
    for k in range(1, 5):
        r = _turn_run(k, [90.0], 3.0)
        r["events"][-1]["pose"] = [2.0, 0.0, math.pi / 2]        # another spot, another heading
        r["events"][-1]["label"] = "elsewhere"
        runs.append(r)
    cal = c.calibrate(runs, ROUTE, doors_since="")
    assert cal["odometry"]["spot_turns_same_view"] == {}
    assert cal["proposal"]["TURN_SCALE_CCW"]["value"] == 1.0
    (leg,) = cal["odometry"]["legs"]
    assert leg["from"] == "fix spot" and leg["to"] == "fix elsewhere" and leg["median"] == pytest.approx(3.0)


def test_out_and_back_measures_the_difference_between_the_directions():
    c = _cal()
    runs = [_turn_run(k, [-90.0, 90.0], 0.18) for k in range(1, 5)]
    g = c.calibrate(runs, ROUTE, doors_since="")["odometry"]["spot_turns_same_view"]["out and back ~90°"]
    assert g["n"] == 4 and g["median"] == pytest.approx(0.18)
    assert g["ccw_minus_cw_per_deg"]["k"] == pytest.approx(0.002)


def _lane_run(k, rows, to=(5.0, 0.0), label="east"):
    return {"name": "2026100%d-130000_lane" % k, "stamp": "2026100%d-130000" % k, "source": "jsonl", "events": [
        {"kind": "run_start", "route": "other", "t": 0.0},
        {"kind": "drive", "mode": "velocity", "label": label, "to": list(to), "end": [5.0, 0.0, 0.0],
         "trace": [[0.0, 0.0, 0.0], [5.0, 0.0, 0.0]], "rolling": rows, "t": 5.0}]}


def test_two_fixes_on_the_move_give_drift_and_distance_per_metre():
    c = _cal()
    rows = [{"along": 0.3, "dyaw_deg": 2.0, "lat": 0.0, "taken": True, "along_fix": 0.13, "along_taken": False},
            {"along": 0.9, "dyaw_deg": 0.3, "lat": 0.0, "taken": True, "along_fix": 0.124, "along_taken": True},
            {"along": 1.5, "dyaw_deg": 0.3, "lat": 0.0, "taken": True, "along_fix": -0.006, "along_taken": True},
            {"along": 2.1, "dyaw_deg": 0.3, "lat": 0.0, "taken": True, "along_fix": -0.006, "along_taken": True}]
    heads, alongs = c.rolling_pairs(_lane_run(1, rows))
    # the first fix of the lane (2 deg, 13 cm) carries everything before the lane: not a pair
    assert [round(p["dyaw_deg"], 3) for p in heads] == [0.3, 0.3, 0.3]
    # the 13 cm the first fix left standing is taken off the second: 0.124 - 0.13
    assert [round(p["err"], 3) for p in alongs] == [-0.006, -0.006, -0.006]
    # two lanes saying the same per metre: a constant of the base
    cal = c.calibrate([_lane_run(1, rows, label="east"), _lane_run(2, rows, label="corridor")], ROUTE,
                      doors_since="")
    o = cal["odometry"]
    assert o["heading_drift_on_lanes_deg_per_m"]["all"]["k"] == pytest.approx(0.5)
    assert o["distance_per_m"]["k"] == pytest.approx(-0.01)
    assert cal["proposal"]["LINEAR_SCALE"]["value"] == pytest.approx(0.99)
    assert cal["proposal"]["HEADING_DRIFT_DEG_PER_M"]["value"] == pytest.approx(0.5)


def test_no_constant_from_too_few_pairs():
    c = _cal()
    rows = [{"along": 0.3, "dyaw_deg": 0.0, "taken": True, "along_fix": 0.0, "along_taken": True},
            {"along": 0.9, "dyaw_deg": 0.6, "taken": True, "along_fix": -0.006, "along_taken": True}]
    p = c.calibrate([_lane_run(1, rows)], ROUTE, doors_since="")["proposal"]
    assert p["LINEAR_SCALE"]["value"] == 1.0 and "insufficient" in p["LINEAR_SCALE"]["why_not"]
    assert p["HEADING_DRIFT_DEG_PER_M"]["value"] == 0.0


# -- the printed page -----------------------------------------------------------

PAGE = """\
arms in: counting 0.70 m in doorways and for spot turns
route 'x', 20 steps, log /data/local/tmp/emma-nav/code/data/robot/runs/20261009-155505_floor2_bigroom_roundtrip.jsonl
15:55:05 resuming at step 15 from the board's pose (-2.41, -2.47, 3.1416)
15:55:48 step 15/20 checkpoint below D8
15:55:54   turned to +90°, off by -0.3° (1 go, 5.2 s)
15:55:54   fix at (-2.41, -2.47, 90.3°): OK dx -0.088 m dy -0.006 m dyaw -0.384° (3 walls, 0 doors)
15:55:54     wall cor_west     at -4.800 seen -4.712 (1.10 m of it)
15:55:54 step 16/20 door D8 in
15:55:55   D8 ahead at 0.77 m: 0.800 m wide (one edge + drawing), middle -0.064 m (+ = left), 5.0 cm a side
15:55:55   +0.052 m off D8's middle: stepping sideways on a 35° slant
15:55:58   drive 0.09 m to (-2.45, -2.40), velocity control
15:56:01   ended at (-2.45, -2.40), 0.01 m from target, off the lane at most 0.001 m, 3.1 s
15:56:24   through D8 in 18.2 s, now at (-2.39, 0.31); closest sides L 0.017 R 0.106 m (0.70 m width)
"""


def test_the_printed_page_reads_as_the_log():
    c = _cal()
    ev = c.parse_text(PAGE)
    kinds = [e["kind"] for e in ev]
    assert kinds[:3] == ["run_start", "resume", "step"]
    assert ev[0]["text_log"] == "20261009-155505_floor2_bigroom_roundtrip" and ev[0]["door_width"] == 0.7
    (turn,) = [e for e in ev if e["kind"] == "turn"]
    deg, _ = c._turn_angle(turn)
    assert deg == pytest.approx(-89.7, abs=1e-3)             # from 180 to 90.3: clockwise
    (fix,) = [e for e in ev if e["kind"] == "checkpoint"]
    assert fix["label"] == "below D8" and fix["pose"][0] == pytest.approx(-2.498)
    assert math.degrees(fix["dyaw"]) == pytest.approx(-0.384)
    assert fix["matches"] == [{"anchor": "cor_west"}]
    (aim,) = [e for e in ev if e["kind"] == "door_aim"]
    assert aim["gap"] == {"face": 0.77, "width": 0.8, "edges": 1, "centre": -0.064}
    assert aim["pose"][0] == pytest.approx(-2.498)           # the fix's, not the guess's
    (drive,) = [e for e in ev if e["kind"] == "drive"]
    assert drive["label"].startswith("sideways")             # the step, not a lane
    (door,) = [e for e in ev if e["kind"] == "door"]
    assert door["closest"] == [0.017, 0.106] and door["door_width"] == 0.7
    passes = c.door_passes({"name": "p", "stamp": "20261009-155505", "source": "text", "events": ev}, ROUTE)
    (p,) = passes
    assert p["first_step_off"] == 0.052 and p["heading"] == 90 and p["first_in_step"]


def test_a_page_is_read_only_when_its_jsonl_did_not_come_back(tmp_path):
    c = _cal()
    (tmp_path / "20261009-155247_run.out").write_text(PAGE, encoding="utf-8")
    runs = c.load_runs(tmp_path)
    assert [(r["name"], r["source"]) for r in runs] == [("20261009-155505_floor2_bigroom_roundtrip", "text")]
    (tmp_path / "20261009-155505_floor2_bigroom_roundtrip.jsonl").write_text("", encoding="utf-8")
    assert [r["source"] for r in c.load_runs(tmp_path)] == ["jsonl"]


def test_main_writes_the_json_and_prints(tmp_path, capsys):
    c = _cal()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    import json
    for k, off in enumerate([0.05, 0.048, 0.052]):
        r = _d8_out_run("20261009-10%02d00" % k, off, t0=1000.0 * k)
        (runs_dir / (r["name"] + ".jsonl")).write_text("\n".join(json.dumps(e) for e in r["events"]), encoding="utf-8")
    route = tmp_path / "floor2_test.json"
    route.write_text(json.dumps(ROUTE), encoding="utf-8")
    out = tmp_path / "cal.json"
    assert c.main(["--runs", str(runs_dir), "--route", str(route), "--doors-since", "", "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["proposal"]["lanes"][0]["proposed"] == pytest.approx(-2.49)
    assert "lane step 2" in capsys.readouterr().out


def test_lanes_that_disagree_give_no_constant():
    """9 Oct: the dock lane said -0.84 %/m, the corridor -0.16 %/m, 8 of 9
    pairs from one run -- tight enough to clear zero, not a property of the
    wheels."""
    c = _cal()

    def rows(err):
        return [{"along": 0.3 + 0.6 * i, "dyaw_deg": 0.0, "taken": True, "along_fix": err if i else 0.0,
                 "along_taken": True} for i in range(4)]
    cal = c.calibrate([_lane_run(1, rows(-0.006), label="dock lane"), _lane_run(2, rows(-0.0012), label="corridor")],
                      ROUTE, doors_since="")
    d = cal["odometry"]["distance_per_m"]
    assert d["by_lane"]["dock lane"]["k"] == pytest.approx(-0.01)
    assert d["by_lane"]["corridor"]["k"] == pytest.approx(-0.002)
    assert d["confidence"] == "low" and "more than 2x" in d["capped"]
    assert cal["proposal"]["LINEAR_SCALE"]["value"] == 1.0
    # one lane alone is not the base either
    one = c.calibrate([_lane_run(1, rows(-0.006), label="dock lane")], ROUTE, doors_since="")
    assert one["proposal"]["LINEAR_SCALE"]["value"] == 1.0
    assert "2 needed" in one["odometry"]["distance_per_m"]["capped"]
