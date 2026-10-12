"""scripts/drive_route.py run as legs (design docs/emma-body-nav-design-2026-10-08.md
§2g): --to-step, --status-file, --state-out/--state-in, --watch-parent, the
`refused`/`interrupted` log kinds, exit 3, and Stop codes set where they are
raised. Nothing here moves anything: the board and the runner's motions are
fakes, and the round trip's step list is copied in below because
data/robot/ is gitignored (a test that read it would skip on every other
machine and still read "passed")."""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import signal
from pathlib import Path

import numpy as np
import pytest

from app import robot_lanes as rl

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "drive_route.py"


@pytest.fixture(scope="module")
def dr():
    spec = importlib.util.spec_from_file_location("drive_route_legs_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


#: data/robot/routes/floor2_bigroom_roundtrip.json of 9 Oct 2026 without its
#: notes: 10 = checkpoint inside the big room, 11 = door D11 out, 20 = dock.
ROUND_TRIP = {
    "name": "floor 2: dock -> D8 -> corridor -> D11 -> big meeting room -> back -> dock (velocity, arms in)",
    "start_from_dock": True, "dock_pose": [-4.295, 1.43, -0.089], "dock_check": ["ea_east"],
    "anchors": {
        "ea_east": {"axis": "x", "at": -1.825, "lo": -0.6, "hi": 1.68},
        "ea_west": {"axis": "x", "at": -4.74, "lo": -1.5, "hi": 0.9},
        "cor_north": {"axis": "y", "at": -1.683, "lo": -1.875, "hi": 1.7},
        "cor_north_w": {"axis": "y", "at": -1.683, "lo": -4.7, "hi": -2.8},
        "cor_south": {"axis": "y", "at": -3.46, "lo": -0.8, "hi": 2.3},
        "cor_south_w": {"axis": "y", "at": -3.46, "lo": -2.9, "hi": -1.2},
        "mr_west": {"axis": "x", "at": 1.715, "lo": -1.6, "hi": 1.5},
        "mr_east": {"axis": "x", "at": 3.325, "lo": -3.1, "hi": -1.0},
        "mr_north": {"axis": "y", "at": 1.413, "lo": 1.9, "hi": 3.3},
        "br_west_in": {"axis": "x", "at": 3.425, "lo": -3.1, "hi": -1.0},
        "cor_west": {"axis": "x", "at": -4.8, "lo": -3.3, "hi": -1.8},
        "cor_end": {"axis": "x", "at": 3.325, "lo": -2.45, "hi": -1.7}},
    "doors": {"D8": {"axis": "y", "at": -1.633, "lo": -2.85, "hi": -2.05, "leaf": "hi"},
              "D11": {"axis": "x", "at": 3.375, "lo": -1.13, "hi": -0.37, "leaf": "hi"}},
    "steps": [
        {"do": "checkpoint", "label": "on the dock", "use": ["ea_east", "ea_west"]},
        {"do": "drive", "label": "east lane out of the dock", "to": [-2.45, 1.43], "use": ["ea_east"]},
        {"do": "drive", "label": "south onto D8's line", "to": [-2.49, 0.25], "use": ["ea_east", "ea_west"]},
        {"do": "door", "label": "D8 out", "door": "D8", "heading": -90, "until": -2.45, "use": ["ea_east", "ea_west"],
         "remember": True},
        {"do": "checkpoint", "label": "corridor below D8", "heading": 0,
         "use": ["cor_north", "cor_south", "cor_south_w"]},
        {"do": "drive", "label": "east along the corridor", "to": [2.46, -2.45],
         "use": ["cor_north", "cor_north_w", "cor_south", "cor_south_w", "cor_end"]},
        {"do": "drive", "label": "north to D11", "to": [2.55, -0.75], "use": ["mr_west", "mr_east"]},
        {"do": "checkpoint", "label": "in front of D11", "heading": 0, "use": ["mr_east"]},
        {"do": "door", "label": "D11 in", "door": "D11", "heading": 0, "until": 4.7},
        {"do": "checkpoint", "label": "inside the big room", "heading": 180, "use": ["br_west_in"]},
        {"do": "door", "label": "D11 out", "door": "D11", "heading": 180, "until": 2.55},
        {"do": "checkpoint", "label": "corridor between meeting rooms", "heading": -90, "use": ["mr_west", "mr_east"]},
        {"do": "drive", "label": "south to the junction", "to": [2.5, -2.45], "use": ["mr_west", "mr_east"]},
        {"do": "drive", "label": "west along the corridor", "to": [-2.376, -2.45],
         "use": ["cor_north", "cor_north_w", "cor_south", "cor_south_w"]},
        {"do": "checkpoint", "label": "below D8", "heading": 90, "use": ["cor_north", "cor_north_w", "cor_west"]},
        {"do": "door", "label": "D8 in", "door": "D8", "heading": 90, "until": 0.25, "remember": True},
        {"do": "drive", "label": "north lane", "to": [-2.45, 1.43]},
        {"do": "checkpoint", "label": "above D8, back, facing the east wall", "heading": 0,
         "use": ["ea_east", "ea_west"]},
        {"do": "drive", "label": "west lane, toward the dock", "to": [-3.0, 1.43]},
        {"do": "dock", "label": "dock"},
    ]}


def test_the_fixture_is_the_route_on_this_machine():
    real = ROOT / "data" / "robot" / "routes" / "floor2_bigroom_roundtrip.json"
    if not real.exists():
        pytest.skip("data/robot/ is gitignored and absent here: the fixture vs the real round trip "
                    "is UNCHECKED on this machine (the leg tests below still run on the fixture)")
    doc = json.loads(real.read_text(encoding="utf-8"))
    strip = lambda s: {k: v for k, v in s.items() if k != "note"}     # noqa: E731
    assert [strip(s) for s in doc["steps"]] == ROUND_TRIP["steps"]
    assert doc["anchors"] == ROUND_TRIP["anchors"] and doc["doors"] == ROUND_TRIP["doors"]


class _Board:
    def __init__(self):
        self.cancelled = 0

    def pose(self):
        return (0.0, 0.0, 0.0)

    def scan(self):
        return np.zeros((0, 2))

    def power(self):
        return {"dockingStatus": "not_on_dock", "batteryPercentage": 80}

    def cancel(self):
        self.cancelled += 1


def _leg_runner(dr, route_doc=ROUND_TRIP):
    """A Runner whose motions only record which step they were asked for:
    chains, door exits and the loop are the real code; the robot is not."""
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.parse_route(route_doc)
    r.sdk = object()                       # chains and door exits are planned as on the robot
    r.turn_width = r.door_width = rl.ROBOT_BASE_WIDTH
    r.b = _Board()
    r.logged, r.visited = [], []
    r.log = lambda **e: r.logged.append(e)
    r.say = lambda m: None
    r.fast_pose = lambda: (0.0, 0.0, 0.0)

    def run_step(i, step, handlers, **kw):
        r.visited.append(i)

    def run_chain(chain, lead=None):
        r.visited.extend(([lead[0]] if lead else []) + list(chain))
        return chain[-1]
    r.run_step, r.run_chain = run_step, run_chain
    return r


@pytest.mark.parametrize("frm,to", [(1, 10), (10, 20)])
def test_a_leg_runs_only_its_own_steps_and_ends_leg_done(dr, frm, to):
    r = _leg_runner(dr)
    r.run(frm, to)
    stepped = [e["i"] for e in r.logged if e.get("kind") == "step"] + r.visited
    assert min(stepped) == frm and max(stepped) == to, sorted(set(stepped))
    assert set(range(frm, to + 1)) <= set(stepped) | {e["i"] for e in r.logged if e.get("kind") == "step_skipped"}
    # leg 2 ends on step 20 = the route's last: still leg_done (build review #3)
    assert r.logged[-1]["kind"] == "leg_done" and "pose" in r.logged[-1]
    assert not any(e.get("kind") == "route_done" for e in r.logged)


def test_without_to_step_the_whole_route_runs_and_ends_route_done(dr):
    r = _leg_runner(dr)
    r.run(1)
    stepped = [e["i"] for e in r.logged if e.get("kind") == "step"] + r.visited
    assert max(stepped) == 20 and r.logged[-1]["kind"] == "route_done"


#: door -> checkpoint -> drive, the shape door_exit_plan rounds out (from
#: test_robot_door's D8-out fixture). On the real route door_exit_plan(9) is
#: already None (step 11 is a door), so a test there could not go red.
CORRIDOR = {"cor_north": rl.Anchor("y", -1.70, -6.0, 6.0), "cor_south": rl.Anchor("y", -3.42, -6.0, 6.0),
            "cor_west": rl.Anchor("x", -4.80, -6.0, 6.0)}


def _d8_out(dr):
    r = dr.Runner.__new__(dr.Runner)
    r.sdk, r.turn_width = object(), 0.70
    r.route = rl.Route(name="d8", anchors=dict(CORRIDOR), doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)},
                       steps=[rl.Step(do="door", door="D8", heading_deg=-90, until=-2.45, label="D8 out"),
                              rl.Step(do="checkpoint", heading_deg=0, use=("cor_north",), label="corridor below D8"),
                              rl.Step(do="drive", to=(-1.30, -2.45), use=("cor_south",),
                                      label="east along the corridor")])
    return r


def test_a_legs_last_checkpoint_is_never_rounded_out_on_the_move(dr):
    r = _d8_out(dr)
    plan = r.door_exit_plan(1)
    assert plan is not None and plan["skipped"] == 2 and plan["lane_k"] == 3     # it would be, without a leg
    r.to_step = 2
    assert r.door_exit_plan(1) is None
    r.to_step = 3
    assert r.door_exit_plan(1) is not None


@pytest.mark.parametrize("frm,to,ok", [
    (1, 10, True), (10, 20, True), (1, 1, True), (1, 20, True),
    (1, 3, False),                        # a drive
    (1, 9, False),                        # a door
    (1, 21, False), (5, 4, False), (0, 10, False),
])
def test_validate_to_step(dr, frm, to, ok):
    why = dr.validate_to_step(rl.parse_route(ROUND_TRIP), frm, to)
    assert (why is None) == ok, why
    assert dr.validate_to_step(rl.parse_route(ROUND_TRIP), 1, None) is None


def test_to_step_on_a_drive_exits_3_before_the_board(dr, tmp_path, monkeypatch, capsys):
    route = tmp_path / "r.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    assert dr.main([str(route), "--to-step", "3"]) == dr.EXIT_REFUSED == 3
    assert "REFUSED" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()           # no run log for a run that never was


# -- state across legs ----------------------------------------------------------------

def _rememberer(dr):
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.parse_route(ROUND_TRIP)
    return r


def test_door_memory_survives_from_one_leg_to_the_next(dr, tmp_path):
    a = _rememberer(dr)
    a.log_name = "20261012-103100_floor2_bigroom_roundtrip.jsonl"
    a.measured_at = {"x": (np.float64(3.4251234567891), np.float64(-0.7612345678)),
                     "y": (np.float64(4.1037), np.float64(-0.7712345678912))}
    a.door_seen = {"D11": {"offset": (np.float64(0.0012), np.float64(-0.0437)), "lat_axis": "y",
                           "measured": a.measured_at["y"]}}
    want = a._door_remembered("D11", math.pi, "y")
    assert want is not None
    path = tmp_path / "route_state.json"
    dr.save_state(a, path)
    assert json.loads(path.read_text())["run_log"] == a.log_name
    b = _rememberer(dr)
    assert b._door_remembered("D11", math.pi, "y") is None          # a cold leg 2 has no memory
    assert dr.load_state(b, path) is True
    assert b._door_remembered("D11", math.pi, "y") == want          # exactly as in one process
    # and it lapses exactly as in one process once a checkpoint measures that axis again
    for r in (a, b):
        r.measured_at["y"] = (4.2, -0.75)
    assert a._door_remembered("D11", math.pi, "y") is None
    assert b._door_remembered("D11", math.pi, "y") is None


@pytest.mark.parametrize("doc", [
    "{not json",
    json.dumps({"format": 2, "measured_at": {}, "door_seen": {}}),
    json.dumps({"format": 1, "measured_at": {"x": [1.0, 2.0]},
                "door_seen": {"D99": {"offset": [0, 0], "lat_axis": "y", "measured": None}}}),
    json.dumps({"format": 1, "measured_at": {"x": [1.0, "2"]}, "door_seen": {}}),
    json.dumps({"format": 1, "measured_at": {"z": [1.0, 2.0]}, "door_seen": {}}),
])
def test_a_state_file_that_does_not_parse_whole_is_not_trusted_in_part(dr, tmp_path, doc):
    path = tmp_path / "s.json"
    path.write_text(doc, encoding="utf-8")
    r = _rememberer(dr)
    r.measured_at, r.door_seen = {}, {}
    assert dr.load_state(r, path) is False
    assert r.measured_at == {} and r.door_seen == {}


def test_state_out_is_written_at_the_end_of_a_leg(dr, tmp_path):
    r = _leg_runner(dr)
    r.state_out, r.log_name = tmp_path / "out.json", "x.jsonl"
    r.measured_at = {"x": (1.0, 2.0)}
    r.run(1, 10)
    doc = json.loads((tmp_path / "out.json").read_text())
    assert doc["format"] == 1 and doc["measured_at"] == {"x": [1.0, 2.0]} and doc["run_log"] == "x.jsonl"
    assert not (tmp_path / "out.json.tmp").exists()


# -- the status file -----------------------------------------------------------------------

def test_the_status_file_is_replaced_whole_and_says_the_state(dr, tmp_path, monkeypatch):
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.parse_route(ROUND_TRIP)
    r.log_file = open(tmp_path / "run.jsonl", "a", encoding="utf-8")
    r.status_path, r.from_step, r.to_step, r.log_name = tmp_path / "status.json", 1, 10, "run.jsonl"
    replaced = []
    real_replace = dr.os.replace

    def spy(src, dst):
        replaced.append((Path(src).name, Path(dst).name, json.loads(Path(src).read_text())))
        real_replace(src, dst)
    monkeypatch.setattr(dr.os, "replace", spy)
    try:
        def read():
            return json.loads((tmp_path / "status.json").read_text())
        r.log(kind="run_start", route="x")
        assert read()["state"] == "running" and read()["step"] is None and read()["step_to"] == 10
        r.log(kind="step", i=4, do="door", label="D8 out")
        st = read()
        assert (st["state"], st["step"], st["do"], st["door"], st["label"]) == ("running", 4, "door", "D8", "D8 out")
        assert (st["step_from"], st["step_to"], st["steps_total"], st["run_log"]) == (1, 10, 20, "run.jsonl")
        n = len(replaced)
        r.log(kind="drive", label="x", seconds=3.0)                 # not a status kind
        r.log(kind="door", door="D8", ok=True)
        assert len(replaced) == n
        r.log(kind="stop", reason="door D8: stopped", code="door_blocked", pose=[1.0, 2.0, 0.5])
        st = read()
        assert (st["state"], st["reason_code"], st["reason"], st["pose"]) == \
            ("stopped", "door_blocked", "door D8: stopped", [1.0, 2.0, 0.5])
        r.log(kind="refused", code="battery_low", reason="battery 12% < 25%")
        assert read()["state"] == "refused" and read()["reason_code"] == "battery_low"
        r.log(kind="interrupted", code="launcher_lost")
        assert read()["state"] == "interrupted" and read()["reason_code"] == "launcher_lost"
        r.log(kind="leg_done", pose=[4.1, -0.75, 3.14])
        assert read()["state"] == "done" and read()["pose"] == [4.1, -0.75, 3.14]
        # every status kind went through a whole tmp file and one replace -- 6 of them
        assert len(replaced) == 6
        assert all(src == "status.json.tmp" and dst == "status.json" for src, dst, _ in replaced)
        assert not (tmp_path / "status.json.tmp").exists()
        assert {"format", "state", "step", "step_from", "step_to", "steps_total", "do", "label", "door",
                "reason_code", "reason", "pose", "t"} <= set(read())
    finally:
        r.log_file.close()


def test_no_status_file_unless_asked(dr, tmp_path):
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.parse_route(ROUND_TRIP)
    r.log_file = open(tmp_path / "run.jsonl", "a", encoding="utf-8")
    try:
        r.log(kind="step", i=1, do="checkpoint", label="on the dock")
    finally:
        r.log_file.close()
    assert [p.name for p in tmp_path.iterdir()] == ["run.jsonl"]


# -- main(): refused, stopped, interrupted --------------------------------------------------

class _MainBoard:
    def __init__(self, *a, **k):
        self.cancels = 0

    def power(self):
        return {"batteryPercentage": 80, "dockingStatus": "on_dock"}

    def pose(self):
        return (0.0, 0.0, 0.0)

    def current(self):
        return None

    def cancel(self):
        self.cancels += 1

    def set_speed(self, v):
        pass


def _main(dr, tmp_path, monkeypatch, *extra):
    route = tmp_path / "floor2_bigroom_roundtrip.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", _MainBoard)
    code = dr.main([str(route), "--sdk", "off", "--status-file", str(tmp_path / "status.json"), *extra])
    (log,) = (tmp_path / "data" / "robot" / "runs").glob("*.jsonl")
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return code, events, json.loads((tmp_path / "status.json").read_text())


def test_a_preflight_stop_is_refused_exit_3_with_its_code(dr, tmp_path, monkeypatch):
    def preflight(self, **kw):
        raise dr.Stop("battery 12% < 25%", code="battery_low")
    monkeypatch.setattr(dr.Runner, "preflight", preflight)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: pytest.fail("ran after a refused preflight"))
    code, events, status = _main(dr, tmp_path, monkeypatch, "--to-step", "10")
    assert code == 3
    assert events[-1]["kind"] == "refused" and events[-1]["code"] == "battery_low"
    assert not any(e["kind"] == "stop" for e in events)
    assert status["state"] == "refused" and status["reason_code"] == "battery_low"


def test_a_stop_on_the_way_is_exit_1_with_its_code(dr, tmp_path, monkeypatch):
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)

    def run(self, frm=1, to=None):
        self.log(kind="step", i=frm, do="checkpoint", label="x")
        raise dr.Stop("door D11: no opening found", code="door_blocked")
    monkeypatch.setattr(dr.Runner, "run", run)
    code, events, status = _main(dr, tmp_path, monkeypatch)
    assert code == 1 and events[-1]["kind"] == "stop" and events[-1]["code"] == "door_blocked"
    assert status["state"] == "stopped" and status["reason_code"] == "door_blocked"


def test_door_memory_is_the_runners_and_off_without_the_flag(dr, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: seen.append(self.door_memory_path))
    _main(dr, tmp_path, monkeypatch, "--door-memory", str(tmp_path / "nav" / "door_memory.json"))
    (tmp_path / "2").mkdir()
    _main(dr, tmp_path / "2", monkeypatch)
    assert seen == [tmp_path / "nav" / "door_memory.json", None]


DOCK_GAP_DOC = {"wall_x": -4.767, "wall_spans": [[0.7, 1.1], [1.85, 2.3]], "depth_lo": 0.07, "depth_hi": 0.109}


def test_the_dock_turn_test_is_all_the_run_does(dr, tmp_path, monkeypatch):
    goes = []
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: pytest.fail("the route ran"))
    monkeypatch.setattr(dr.Runner, "dock_turn_test", lambda self, n: goes.append(n))
    route = tmp_path / "floor2_bigroom_roundtrip.json"
    route.write_text(json.dumps(dict(ROUND_TRIP, dock_gap=DOCK_GAP_DOC)), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", _MainBoard)
    assert dr.main([str(route), "--sdk", "off", "--dock-turn-test", "3"]) == 0
    assert goes == [3]


@pytest.mark.parametrize("extra", [["--dock-turn-test", "3", "--from-step", "10"],
                                   ["--dock-turn-test", "3", "--to-step", "10"],
                                   ["--dock-turn-test", "9"]])
def test_the_dock_turn_test_refuses_anything_but_a_start_on_the_dock_before_the_board(dr, tmp_path, monkeypatch,
                                                                                      capsys, extra):
    route = tmp_path / "r.json"
    route.write_text(json.dumps(dict(ROUND_TRIP, dock_gap=DOCK_GAP_DOC)), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    assert dr.main([str(route), *extra]) == dr.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().out


def test_the_dock_turn_test_needs_the_dock_s_walls_in_the_route(dr, tmp_path, monkeypatch, capsys):
    route = tmp_path / "r.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")            # no dock_gap
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    assert dr.main([str(route), "--dock-turn-test", "1"]) == dr.EXIT_REFUSED
    assert "dock_gap" in capsys.readouterr().out


def test_ctrl_c_is_logged_interrupted(dr, tmp_path, monkeypatch):
    handlers = []
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: handlers.append((sig, h)))
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    code, events, status = _main(dr, tmp_path, monkeypatch)
    assert code == 130 and events[-1] == dict(events[-1], kind="interrupted", code=None)
    assert status["state"] == "interrupted"
    # review finding 1: a PC run (no --watch-parent) too -- SIGINT raises even when
    # inherited ignored, and a second stop cannot land in the cleanup and skip the cancel
    assert handlers == [(signal.SIGINT, signal.default_int_handler), (signal.SIGINT, signal.SIG_IGN)]


def test_sigint_raises_in_a_run_started_with_it_ignored(dr, tmp_path, monkeypatch):
    """Review finding 1. `robot_python run` starts drive_route as `(nohup ... &)`
    from a non-interactive adb shell: SIGINT arrives SIG_IGN, CPython keeps
    that, and `stop` "sent" a signal nothing heard while the robot drove on.
    The real signal table, not a recorder: what counts is what SIGINT does."""
    route = tmp_path / "r.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    before = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)            # as nohup-in-background leaves it
        assert dr.main([str(route), "--to-step", "3"]) == dr.EXIT_REFUSED     # no flags at all
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGINT, before)


def test_an_uncaught_exception_is_its_own_exit_and_log_kind(dr, tmp_path, monkeypatch):
    """Review finding 27: an exception that is not a Stop (an httpx status error
    out of preflight) left as Python's exit 1 = EXIT_STOP, and the launcher said
    "stopped mid-way" for a robot that never moved."""
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: None)

    def preflight(self, **kw):
        raise ValueError("HTTP 500 from /api/core/system/v1/power/status")
    monkeypatch.setattr(dr.Runner, "preflight", preflight)
    code, events, status = _main(dr, tmp_path, monkeypatch, "--to-step", "10")
    assert code == dr.EXIT_ERROR == 4 and code != dr.EXIT_STOP
    assert events[-1]["kind"] == "error" and events[-1]["code"] == "error" and "HTTP 500" in events[-1]["reason"]
    assert status["state"] == "failed" and status["reason_code"] == "error"


def test_pid_file_goes_at_the_end_only_while_it_names_this_process(dr, tmp_path, monkeypatch):
    """Review finding 12: a PC run's run.pid outlived the run, and a reused pid
    was then signalled / kill -9'd / read as a PC run that locked every card."""
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: None)
    route = tmp_path / "r.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    mine, other = tmp_path / "mine.pid", tmp_path / "other.pid"
    mine.write_text("%d\n" % dr.os.getpid())
    other.write_text("%d\n" % (dr.os.getpid() + 1))
    assert dr.main([str(route), "--to-step", "3", "--pid-file", str(mine)]) == 3     # ends early: still goes
    assert not mine.exists()
    assert dr.main([str(route), "--to-step", "3", "--pid-file", str(other)]) == 3
    assert other.read_text().strip() == str(dr.os.getpid() + 1)                      # someone else's lock
    assert dr.release_pid_file(tmp_path / "absent.pid") is False


def test_a_launcher_gone_is_logged_as_why_and_a_second_sigint_cannot_skip_the_cancel(dr, tmp_path, monkeypatch):
    handlers = []
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: handlers.append((sig, h)))

    class Watch:
        reason = "launcher_lost"

        def __init__(self, *a, **k):
            pass

        def start(self):
            return self

        def stop(self):
            pass
    monkeypatch.setattr(dr, "ParentWatch", Watch)
    monkeypatch.setattr(dr.Runner, "preflight", lambda self, **kw: None)
    monkeypatch.setattr(dr.Runner, "run", lambda self, *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    code, events, status = _main(dr, tmp_path, monkeypatch, "--watch-parent")
    assert code == 130 and events[-1]["kind"] == "interrupted" and events[-1]["code"] == "launcher_lost"
    assert status["reason_code"] == "launcher_lost"
    # SIGINT must raise KeyboardInterrupt even when inherited ignored; then ignored
    # while the board's action is cancelled
    assert handlers == [(signal.SIGINT, signal.default_int_handler), (signal.SIGINT, signal.SIG_IGN)]


# -- --watch-parent ---------------------------------------------------------------------------

def test_watch_parent_interrupts_once_when_the_parent_changes(dr):
    ppids = iter([4711, 4711, 4711, 1, 1])
    fired = []
    w = dr.ParentWatch(getppid=lambda: next(ppids), interrupt=lambda: fired.append(1), every=0.01)
    assert w.parent == 4711
    assert w.check() is False and w.check() is False and fired == []
    assert w.check() is True and fired == [1] and w.reason == "launcher_lost"
    assert w.check() is False and fired == [1]        # once


def test_watch_parent_compares_against_the_launchers_pid_not_whatever_parent_is_left(dr):
    """Review finding 10: a launcher killed during drive_route's imports leaves
    it re-parented to init before the watch is built -- a getppid() baseline
    then reads 1 and never fires. The launcher's own pid does."""
    fired = []
    late = dr.ParentWatch(getppid=lambda: 1, interrupt=lambda: fired.append("late"), every=0.01)
    assert late.check() is False and fired == []                  # the gap the pid closes
    w = dr.ParentWatch(getppid=lambda: 1, interrupt=lambda: fired.append("pid"), every=0.01, parent=4711)
    assert w.parent == 4711 and w.check() is True and fired == ["pid"]


def test_a_leg_whose_launcher_is_already_gone_refuses_before_the_board(dr, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: None)
    route = tmp_path / "r.json"
    route.write_text(json.dumps(ROUND_TRIP), encoding="utf-8")
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setattr(dr, "Board", lambda *a, **k: pytest.fail("the board was touched"))
    monkeypatch.setattr(dr, "ParentWatch", lambda *a, **k: pytest.fail("watching init"))
    monkeypatch.setattr(dr.os, "getppid", lambda: 1)                # re-parented: the launcher died
    assert dr.main([str(route), "--watch-parent", "4711", "--to-step", "10"]) == dr.EXIT_REFUSED
    assert "4711" in capsys.readouterr().out
    assert not (tmp_path / "data").exists()


def test_the_launchers_pid_is_what_the_watch_compares_against(dr, tmp_path, monkeypatch):
    monkeypatch.setattr(dr.signal, "signal", lambda sig, h: None)
    seen = {}

    class Watch:
        reason = None

        def __init__(self, *a, **k):
            seen.update(k)

        def start(self):
            return self

        def stop(self):
            seen["stopped"] = True
    monkeypatch.setattr(dr, "ParentWatch", Watch)
    monkeypatch.setattr(dr.os, "getppid", lambda: 4711)

    def preflight(self, **kw):
        raise dr.Stop("battery 12% < 25%", code="battery_low")
    monkeypatch.setattr(dr.Runner, "preflight", preflight)
    code, _, _ = _main(dr, tmp_path, monkeypatch, "--watch-parent", "4711", "--to-step", "10")
    assert code == 3 and seen == {"parent": 4711, "stopped": True}


def test_watch_parent_thread_fires_through_the_injected_interrupt(dr):
    import threading
    state = {"ppid": 4711}
    hit = threading.Event()
    w = dr.ParentWatch(getppid=lambda: state["ppid"], interrupt=hit.set, every=0.01).start()
    try:
        assert not hit.wait(0.1)
        state["ppid"] = 1                              # the launcher died: re-parented to init
        assert hit.wait(2.0) and w.reason == "launcher_lost"
    finally:
        w.stop()


# -- Stop codes ---------------------------------------------------------------------------------

def _raise_sites():
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call) \
                    and getattr(child.exc.func, "id", None) == "Stop":
                kw = {k.arg: k.value for k in child.exc.keywords}
                out.append((name, child.lineno, kw.get("code")))
            walk(child, name)
    walk(tree, None)
    return out


def test_every_stop_that_ends_a_run_names_its_code():
    """No reason is recovered from message text (build review #6). The four
    functions that end a run directly must say the code at the raise; every
    stop in the docking functions is the robot standing by the charger."""
    sites = _raise_sites()
    assert len(sites) > 40
    must = {"run", "run_step", "recover_link", "preflight"}
    missing = [(fn, line) for fn, line, code in sites if fn in must and code is None]
    assert not missing, missing
    dock = {"dock", "back_into_dock", "_dock_gap", "_dock_check", "_back_in_once"}
    docking = [(fn, line, getattr(code, "value", code)) for fn, line, code in sites if fn in dock]
    assert docking and all(c == "dock_blocked" for _, _, c in docking), docking
    pre = {getattr(code, "value", None) for fn, _, code in sites if fn == "preflight"}
    assert pre == {"mapping_on", "action_running", "localization", "battery_low", "not_on_dock"}


def test_the_rewrapped_sites_carry_the_original_code():
    sites = {(fn, line): code for fn, line, code in _raise_sites()}
    run_step = [code for (fn, _), code in sites.items() if fn == "run_step"]
    assert any(isinstance(c, ast.Attribute) and c.attr == "code" for c in run_step)     # DoorBlocked -> e.code
    assert any(getattr(c, "value", None) == "link_lost" for c in run_step)
    assert [getattr(c, "value", None) for (fn, _), c in sites.items() if fn == "recover_link"] == ["link_lost"]


def test_class_codes(dr):
    assert dr.Stop("x").code == "stopped" and dr.Stop("x", code="obstacle").code == "obstacle"
    assert dr.DoorBlocked("x", 0.1, 0.2).code == "door_blocked"
    assert dr.StepBlocked("x").code == "obstacle" and dr.LinkLost("x").code == "link_lost"
    from app import robot_contracts as rc
    for code in ("stopped", "door_blocked", "obstacle", "link_lost", "dock_blocked"):
        assert code in rc.NAV_REASONS                 # what a stop mid-route can say to a customer


def _step_runner(dr, monkeypatch, exc):
    r = dr.Runner.__new__(dr.Runner)
    r.route = rl.Route(name="t", anchors={}, doors={"D8": rl.Door("y", -1.633, -2.80, -1.96)}, steps=[])
    r.sdk, r.b = None, _Board()
    r.log = lambda **e: None
    r.say = lambda m: None
    r.step_across = lambda *a, **k: None
    r.recover_link = lambda why: None
    r.keep_corrections = lambda e: None
    step = rl.Step(do="checkpoint", heading_deg=0.0, use=(), label="x")

    def handler(st):
        raise exc()
    return r, step, {"checkpoint": handler}


def test_a_door_blocked_every_go_ends_door_blocked_not_stopped(dr, monkeypatch):
    r, step, handlers = _step_runner(dr, monkeypatch, lambda: dr.DoorBlocked("door D8: held", 0.05, 0.0))
    with pytest.raises(dr.Stop) as e:
        r.run_step(4, step, handlers)
    assert type(e.value) is dr.Stop and e.value.code == "door_blocked"


def test_a_link_that_keeps_dropping_ends_link_lost(dr, monkeypatch):
    r, step, handlers = _step_runner(dr, monkeypatch, lambda: dr.LinkLost("gone"))
    with pytest.raises(dr.Stop) as e:
        r.run_step(4, step, handlers)
    assert type(e.value) is dr.Stop and e.value.code == "link_lost"


def test_a_link_that_never_comes_back_ends_link_lost(dr, monkeypatch):
    monkeypatch.setattr(dr, "LINK_WAIT_S", 0.0)
    r = dr.Runner.__new__(dr.Runner)
    r.log, r.say = (lambda **e: None), (lambda m: None)
    with pytest.raises(dr.Stop) as e:
        r.recover_link("x")
    assert e.value.code == "link_lost"
