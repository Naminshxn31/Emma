"""scripts/analyze_run.py: what a robot run did, read back from its log --
the numbers that pointed at each fix on 7 ต.ค., found without a person
reading the jsonl."""
import importlib.util
import math
from pathlib import Path

import pytest


def _ar():
    path = Path(__file__).resolve().parents[1] / "scripts" / "analyze_run.py"
    spec = importlib.util.spec_from_file_location("analyze_run_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _door(t0, crawl_from=None, door="D8", seconds=34.0):
    """A door pass south at 0.15 then the creep; from `crawl_from` s on it
    sends 0.061 and moves 6 mm/s (15:50 past D8)."""
    tr, y = [], 0.25
    for k in range(int(seconds * 10)):
        t = k / 10.0
        crawling = crawl_from is not None and t >= crawl_from
        v = 0.061 if crawling else 0.15
        y -= (0.006 if crawling else v) * 0.1
        tr.append([round(t, 2), -2.43, round(y, 4), -90.0, -90.0, None, 0.0, 0.3, 0.3, v, 0.0, 0, 0.1, 0.1, 0.0])
    return {"kind": "door", "door": door, "ok": True, "seconds": seconds, "closest": [0.07, 0.09],
            "trace": tr, "t": t0 + seconds}


def _run(t0=1000.0, shift=(0.06, 0.0), crawl=False):
    return [
        {"kind": "run_start", "t": t0},
        {"kind": "step", "i": 1, "do": "door", "label": "D8 in", "t": t0},
        {"kind": "door_aim", "door": "D8", "pose": [-2.48, -2.45, math.pi / 2], "gap": {}, "t": t0 + 1},
        {"kind": "door_step_across", "door": "D8", "off": -shift[0], "t": t0 + 21,
         "to": [-2.48 + shift[0], -2.45 + shift[1]]},
        {"kind": "turn", "seconds": 8.0, "goes": 1, "t": t0 + 30},
        _door(t0 + 30, crawl_from=20.0 if crawl else None, seconds=50.0 if crawl else 34.0),
        {"kind": "checkpoint", "label": "junction", "accepted": True, "dx": 0.09, "dy": 0.18, "dyaw": 0.005,
         "t": t0 + 90},
        {"kind": "route_done", "pose": [0, 0, 0], "t": t0 + 100},
    ]


def test_a_crawl_past_the_frame_is_named():
    ar = _ar()
    s = ar.summarize(_run(crawl=True))
    (d,) = s["doors"]
    assert d["crawl_s"] > 10
    assert any("crawling" in n for n in s["notes"])
    assert ar.summarize(_run())["doors"][0]["crawl_s"] == 0.0


def test_the_same_step_across_run_after_run_says_move_the_lane():
    ar = _ar()
    history = [_run(t0=1000.0 * k) for k in (1, 2)]
    notes = ar.summarize(_run(t0=5000.0), history)["notes"]
    assert any("move the lane" in n and "+0.06" in n for n in notes)
    once = ar.summarize(_run(t0=5000.0), [])["notes"]          # one run is not a pattern
    assert not any("move the lane" in n for n in once)
    mixed = ar.summarize(_run(t0=5000.0, shift=(-0.06, 0.0)), history)["notes"]
    assert not any("move the lane" in n for n in mixed)        # back and forth is not a lane off
    fixed = [e for e in _run(t0=5000.0) if e["kind"] != "door_step_across"]
    assert not any("move the lane" in n for n in ar.summarize(fixed, history)["notes"])   # not this run: done


def test_a_step_after_a_block_is_not_paired_with_the_aim_before_the_pass():
    # reviewer: its "shift" would include the pass's forward travel
    ar = _ar()
    ev = _run()
    i = next(k for k, e in enumerate(ev) if e["kind"] == "door_step_across")
    blocked = ev[:i + 1] + [dict(ev[i - 1], kind="door", ok=False, seconds=5.0, closest=[], trace=[]),
                            dict(ev[i], t=ev[i]["t"] + 10)] + ev[i + 1:]
    shifts = [a["shift"] for a in ar.summarize(blocked)["steps_across"]]
    assert shifts[0] is not None and shifts[-1] is None


def test_stops_and_drift_are_said_with_their_kind():
    ar = _ar()
    ev = _run()[:-1] + [{"kind": "stop", "reason": "door D8: no opening found 0.86 m ahead in 30 s", "t": 1200.0}]
    s = ar.summarize(ev)
    assert not s["done"]
    assert any("door shut" in n for n in s["notes"])
    assert any("junction" in n and "18 cm" in n for n in s["notes"])
    text = ar.report(s, "t")
    assert "not finished" in text and "D8" in text


def test_a_door_too_narrow_to_go_in_is_not_called_a_side_stop():
    ar = _ar()
    assert ar._stop_kind("door D11: gap 0.760 m leaves 3.0 cm a side for a 0.70 m robot (need 3.0) "
                         "-- not going in") == "doorway too narrow to go in"
    assert ar._stop_kind("door D8: stopped at (-2.31, -1.56) -- side 0.008 m") == "doorway side stop"
    # reviewer: "sideways" is not a side stop
    assert ar._stop_kind("door D8: +0.050 m off the opening's middle after 2 steps sideways") != "doorway side stop"
    assert ar._stop_kind('drive "sideways onto D8\'s line": entered a forbidden area at (1, 2)') == "forbidden area"


def test_a_leg_counts_as_done_even_on_the_routes_last_step():
    # drive_route --to-step (the robot's launcher) ends `leg_done`, also when the
    # leg's last step is the route's last (leg 2 of the round trip ends on the dock)
    ar = _ar()
    leg = _run()[:-1] + [{"kind": "leg_done", "pose": [0, 0, 0], "t": 1100.0}]
    s = ar.summarize(leg)
    assert s["done"] and s["leg"]
    assert "leg done" in ar.report(s, "t")
    whole = ar.summarize(_run())
    assert whole["done"] and not whole["leg"] and "route done" in ar.report(whole, "t")


def test_a_refusal_before_moving_is_said_as_one_not_as_a_stop():
    ar = _ar()
    ev = [{"kind": "run_start", "t": 1.0},
          {"kind": "refused", "code": "battery_low", "reason": "battery 12% < 25%", "t": 2.0}]
    s = ar.summarize(ev)
    assert not s["done"] and s["stops"] == []
    assert s["refused"] == [{"code": "battery_low", "reason": "battery 12% < 25%"}]
    assert any(n.startswith("REFUSED before moving (battery_low)") for n in s["notes"])


def test_a_crash_is_said_as_an_error_not_as_a_stop():
    # review finding 27: drive_route logs `error` and exits 4 for an exception
    # nobody caught; the summary must not read "nothing stands out"
    ar = _ar()
    ev = [{"kind": "run_start", "t": 1.0},
          {"kind": "error", "code": "error", "reason": "HTTPStatusError('500')", "t": 2.0}]
    s = ar.summarize(ev)
    assert not s["done"] and s["stops"] == [] and s["refused"] == []
    assert s["errors"] == ["HTTPStatusError('500')"]
    assert any(n.startswith("ERROR (drive_route crashed") for n in s["notes"])
    assert "nothing stands out" not in ar.report(s, "t")


def test_the_lidar_maps_stops_are_summed_up_beside_the_walls():
    # drive_route logs `shadow_fix` at every checkpoint (19 ต.ค., log only): the
    # summary says how far it was from the walls where they measured, how many
    # axes only the map pinned (the dock's y), errors, and the slowest match
    ar = _ar()
    ev = [{"kind": "run_start", "t": 0.0},
          {"kind": "shadow_fix", "label": "on the dock", "pinned": [True, True, True], "score": 0.96,
           "vs_walls": {"x_m": 0.002, "yaw_deg": 0.7}, "seconds": 0.15, "t": 1.0},
          {"kind": "shadow_fix", "label": "below D8", "pinned": [True, True, True], "score": 0.91,
           "vs_walls": {"x_m": -0.031, "y_m": 0.012, "yaw_deg": -0.2}, "seconds": 0.21, "t": 2.0},
          {"kind": "shadow_fix", "label": "junction", "error": "MemoryError: x", "seconds": 0.01, "t": 3.0},
          {"kind": "drive", "label": "east", "seconds": 4.0, "seen": [{"along": 0.0}, {"along": 0.6}], "t": 4.0}]
    s = ar.summarize(ev)
    sh = s["shadow"]
    assert sh["n"] == 3 and sh["errors"] == 1
    assert sh["vs_walls_max"] == {"x_m": 3.1, "y_m": 1.2, "yaw_deg": 0.7}
    assert sh["only_map"] == 1                       # the dock's y: no wall there measured it
    assert sh["seconds_max"] == 0.21 and sh["score_min"] == 0.91
    assert s["frames_on_the_move"] == 2
    text = ar.report(s)
    assert "lidar map (log only): 3 stops (1 errors)" in text and "x 3.1 cm (n 2)" in text
    assert "lidar frames logged on the move: 2" in text
