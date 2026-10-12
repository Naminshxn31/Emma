"""scripts/emma_nav.py -- the robot's own navigation launcher (design
docs/emma-body-nav-design-2026-10-08.md §2e). Nothing here talks to a robot:
the board, the child process, the clock and the pid probe are fakes, because
the real ones are a robot that moves (and on Windows os.kill(pid, 0) is
TerminateProcess, not a probe)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    path = ROOT / "scripts" / "emma_nav.py"
    spec = importlib.util.spec_from_file_location("emma_nav_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                      # dataclasses look their module up there
    spec.loader.exec_module(mod)
    return mod


nav = _load()
from app import robot_contracts as rc  # noqa: E402
from app import robot_places as rp  # noqa: E402

RUN_ID = "0123456789abcdef0123456789abcdef"

#: The 20 steps of data/robot/routes/floor2_bigroom_roundtrip.json (8 Oct),
#: structure only. The route file is gitignored (data/robot/), so a test
#: that read it would pass on this machine and skip everywhere else.
ROUTE_STEPS = [
    {"do": "checkpoint", "label": "on the dock", "use": ["ea_east", "ea_west"]},
    {"do": "drive", "label": "east lane out of the dock", "to": [-2.45, 1.43], "use": ["ea_east"]},
    {"do": "drive", "label": "south onto D8's line", "to": [-2.44, 0.25], "use": ["ea_east", "ea_west"]},
    {"do": "door", "label": "D8 out", "door": "D8", "heading": -90, "until": -2.45, "use": ["ea_east", "ea_west"]},
    {"do": "checkpoint", "label": "corridor below D8", "heading": 0, "use": ["cor_north", "cor_south", "cor_south_w"]},
    {"do": "drive", "label": "east along the corridor", "to": [2.46, -2.45],
     "use": ["cor_north", "cor_north_w", "cor_south", "cor_south_w", "cor_end"]},
    {"do": "drive", "label": "north to D11", "to": [2.55, -0.75], "use": ["mr_west", "mr_east"]},
    {"do": "checkpoint", "label": "in front of D11", "heading": 0, "use": ["mr_east"]},
    {"do": "door", "label": "D11 in", "door": "D11", "heading": 0, "until": 4.7},
    {"do": "checkpoint", "label": "inside the big room", "heading": 180, "use": ["br_west_in"]},
    {"do": "door", "label": "D11 out", "door": "D11", "heading": 180, "until": 2.55},
    {"do": "checkpoint", "label": "corridor between meeting rooms", "heading": -90, "use": ["mr_west", "mr_east"]},
    {"do": "drive", "label": "south to the junction", "to": [2.5, -2.45], "use": ["mr_west", "mr_east"]},
    {"do": "drive", "label": "west along the corridor", "to": [-2.42, -2.45],
     "use": ["cor_north", "cor_north_w", "cor_south", "cor_south_w"]},
    {"do": "checkpoint", "label": "below D8", "heading": 90, "use": ["cor_north", "cor_north_w", "cor_west"]},
    {"do": "door", "label": "D8 in", "door": "D8", "heading": 90, "until": 0.25},
    {"do": "drive", "label": "north lane", "to": [-2.45, 1.43]},
    {"do": "checkpoint", "label": "above D8, back, facing the east wall", "heading": 0, "use": ["ea_east", "ea_west"]},
    {"do": "drive", "label": "west lane, toward the dock", "to": [-3.0, 1.43]},
    {"do": "dock", "label": "dock"},
]
ROUTE_REL = "data/robot/routes/floor2_bigroom_roundtrip.json"
BIG_ROOM_POSE = (4.10, -0.75, 3.1416)


def _places_doc(route_bytes: bytes) -> dict:
    doc = json.loads((ROOT / "data" / "robot-places" / "floor2.json").read_text(encoding="utf-8"))
    doc["route_sha256"] = hashlib.sha256(route_bytes).hexdigest()
    return doc


# -- fakes ----------------------------------------------------------------------

class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.hooks = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.now = round(self.now + dt, 6)
        for hook in list(self.hooks):
            hook(self.now)


class FakeBoard:
    def __init__(self, battery=80, docking="on_dock", pose=(-4.295, 1.43, -0.089), fail=False,
                 current=None, on_power=None):
        self.battery, self.docking, self._pose, self.fail = battery, docking, pose, fail
        self._current, self.cancelled, self.calls, self.on_power = current, 0, [], on_power

    def power(self):
        self.calls.append("power")
        if self.on_power is not None:
            self.on_power()
        if self.fail:
            raise nav.BoardError("GET power: timeout")
        return {"batteryPercentage": self.battery, "dockingStatus": self.docking}

    def pose(self):
        self.calls.append("pose")
        if self.fail:
            raise nav.BoardError("GET pose: timeout")
        return self._pose

    def current(self):
        self.calls.append("current")
        return self._current

    def cancel(self):
        self.calls.append("cancel")
        self.cancelled += 1
        self._current = None

    def localization_on(self):
        return False


class FakeChild:
    """exits with `code` at `exit_at` (clock time), or `on_interrupt` s
    after the first interrupt."""

    def __init__(self, clock, pid=4242, code=0, exit_at=None, on_interrupt=0.4, int_code=130):
        self.clock, self.pid, self.code, self.exit_at = clock, pid, code, exit_at
        self.on_interrupt, self.int_code = on_interrupt, int_code
        self.interrupts, self.kills, self.ended = [], [], None

    def poll(self):
        if self.ended is not None:
            return self.ended
        if self.kills:
            self.ended = -9
        elif self.interrupts and self.on_interrupt is not None and \
                self.clock() >= self.interrupts[0] + self.on_interrupt:
            self.ended = self.int_code
        elif self.exit_at is not None and self.clock() >= self.exit_at:
            self.ended = self.code
        return self.ended

    def interrupt(self):
        self.interrupts.append(self.clock())

    def kill(self):
        self.kills.append(self.clock())


#: /proc/<pid>/cmdline of a launcher running a leg, as the app starts it
LAUNCHER_CMDLINE = ("/data/local/tmp/emma-nav/glibc/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 --library-path x "
                    "/data/local/tmp/emma-nav/python/bin/python3.13 -u scripts/emma_nav.py go big_room "
                    "--origin tap --run-id %s --tether --arms-rested " % RUN_ID)


class World:
    """A robot's /data/local/tmp/emma-nav in a tmp dir, plus fakes.

    `go` defaults to arms_rested=True: the app's normal path (its
    nav_require_arm_rest switch on, restArms() returned true). The tests of
    review 2 finding 7 pass False."""

    def __init__(self, tmp: Path, board: FakeBoard | None = None, child_kw=None, places_doc=None):
        self.paths = nav.Paths(tmp / "emma-nav")
        p = self.paths
        route_bytes = json.dumps({"name": "fixture", "steps": ROUTE_STEPS}).encode("utf-8")
        (p.code / ROUTE_REL).parent.mkdir(parents=True)
        (p.code / ROUTE_REL).write_bytes(route_bytes)
        p.places.parent.mkdir(parents=True)
        doc = places_doc(route_bytes) if callable(places_doc) else _places_doc(route_bytes)
        p.places.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        p.runs.mkdir(parents=True)
        p.nav.mkdir(parents=True)
        self.clock = FakeClock()
        self.board = board or FakeBoard()
        self.alive_pids: set[int] = set()
        self.signalled: list[int] = []
        self.spawned: list[list[str]] = []
        self.child_kw = dict(child_kw or {})
        self.child: FakeChild | None = None
        self.lines: list[dict] = []
        #: /proc/<pid>/cmdline of live pids; unlisted live ones are drive_route
        self.cmdlines: dict[int, str | None] = {}
        #: /proc/<pid>/stat start times; unlisted = cannot be read. Never the
        #: real /proc: on a Linux CI, pid 1000 is somebody's process.
        self.starts: dict[int, int] = {}
        self.killed: list[int] = []
        self.signal_result = "sent"
        self.env = nav.Env(paths=p, board=self.board, alive=self.alive, spawn=self.spawn,
                           signal_pid=self.signal, kill_pid=self.kill, cmdline=self.cmdline,
                           start_time=self.start_time,
                           clock=self.clock, wall=lambda: 1791459060.0 + self.clock.now,
                           sleep=self.clock.sleep, out=self.lines.append, my_pid=1000)

    def alive(self, pid):
        return pid in self.alive_pids

    def cmdline(self, pid):
        if pid not in self.alive_pids:
            return None
        return self.cmdlines.get(pid, "/data/local/tmp/emma-nav/py -u scripts/drive_route.py r.json")

    def start_time(self, pid):
        return self.starts.get(pid) if pid in self.alive_pids else None

    def launcher(self, pid, start=None):
        """A live launcher process running a leg."""
        self.alive_pids.add(pid)
        self.cmdlines[pid] = LAUNCHER_CMDLINE
        if start is not None:
            self.starts[pid] = start

    def signal(self, pid):
        self.signalled.append(pid)
        return self.signal_result

    def kill(self, pid):
        self.killed.append(pid)
        self.alive_pids.discard(pid)
        return "sent"

    def spawn(self, cmd, cwd, out):
        self.spawned.append(list(cmd))
        # a child nobody scripted still ends: a test that should not have
        # spawned fails on its assertion, not on pytest's timeout
        self.child = FakeChild(self.clock, **{"exit_at": 30.0, **self.child_kw})
        self.alive_pids.add(self.child.pid)
        return self.child

    # what drive_route would leave behind
    def drive(self, **kw):
        st = {"format": 1, "state": "running", "step": None, "step_from": None, "step_to": None,
              "do": None, "label": None, "door": None, "reason_code": None, "reason": None,
              "pose": None, "run_log": None}
        st.update(kw)
        self.paths.drive_status.write_text(json.dumps(st), encoding="utf-8")

    def log(self, name: str, *events) -> None:
        with open(self.paths.runs / name, "a", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")

    def here(self, place="big_room", pose=BIG_ROOM_POSE, run_log="20261012-103100_floor2_bigroom_roundtrip.jsonl"):
        self.log(run_log, {"kind": "leg_done", "pose": list(pose)})
        self.paths.here.write_text(json.dumps({"place": place, "pose": list(pose), "at": 1.0,
                                               "run_log": run_log}), encoding="utf-8")
        return run_log

    def go(self, place="big_room", origin="tap", run_id=RUN_ID, tether=None, flags=None,
           arms_rested=True) -> int:
        return nav.go(self.env, place, origin, run_id, tether=tether, flags=flags, arms_rested=arms_rested)

    @property
    def final(self) -> dict:
        return self.lines[-1]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _places():
    route_bytes = json.dumps({"steps": ROUTE_STEPS}).encode()
    return rp.parse(_places_doc(route_bytes))


# -- the tracked places file ------------------------------------------------------

def test_the_tracked_places_file_is_valid_against_the_round_trip():
    places = rp.load(ROOT / "data" / "robot-places" / "floor2.json")
    rp.validate(places, ROUTE_STEPS)
    assert places.route == ROUTE_REL
    one, two = rp.leg(places, "dock", "big_room"), rp.leg(places, "big_room", "dock")
    # leg 2 starts on the checkpoint leg 1 ends on (build review #2), and ends on the dock
    assert (one.from_step, one.to_step, two.from_step, two.to_step) == (1, 10, 10, 20)
    assert ROUTE_STEPS[9]["label"] == "inside the big room" and ROUTE_STEPS[19]["do"] == "dock"
    # nothing has run on the robot: both legs ship unverified, no invented times
    assert not one.verified and not two.verified and one.est_s is None and two.est_s is None
    assert one.min_battery > two.min_battery          # going home is allowed lower


def test_the_tracked_route_sha256_is_the_route_on_this_machine():
    real = ROOT / ROUTE_REL
    if not real.exists():
        pytest.skip("data/robot/ is gitignored and absent here: route_sha256 vs the real route "
                    "floor2_bigroom_roundtrip.json is UNCHECKED on this machine")
    places = rp.load(ROOT / "data" / "robot-places" / "floor2.json")
    assert rp.sha256_file(real) == places.route_sha256
    steps = json.loads(real.read_text(encoding="utf-8"))["steps"]
    assert [(s["do"], s.get("label")) for s in steps] == [(s["do"], s["label"]) for s in ROUTE_STEPS]


# -- where am I --------------------------------------------------------------------

LOG = "20261012-103100_floor2_bigroom_roundtrip.jsonl"
REC = {"place": "big_room", "pose": list(BIG_ROOM_POSE), "at": 1.0, "run_log": LOG}


@pytest.mark.parametrize("power,pose,rec,newest,want", [
    ({"dockingStatus": "on_dock"}, (0, 0, 0), None, None, ("dock", False)),
    # the board's on_dock wins over a record that says elsewhere
    ({"dockingStatus": "on_dock"}, BIG_ROOM_POSE, REC, LOG, ("dock", False)),
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, REC, LOG, ("big_room", True)),
    # a later PC run made a newer log: the robot may be anywhere
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, REC, "20261012-120000_floor2_out.jsonl", (None, False)),
    # pushed by hand / joystick: odometry moved past the tolerance
    ({"dockingStatus": "not_on_dock"}, (4.40, -0.75, 3.1416), REC, LOG, (None, False)),
    ({"dockingStatus": "not_on_dock"}, (4.20, -0.70, 3.1416), REC, LOG, ("big_room", True)),
    # heading off by 20 degrees (tolerance 15)
    ({"dockingStatus": "not_on_dock"}, (4.10, -0.75, 3.1416 - 0.35), REC, LOG, (None, False)),
    # across +-pi is not 360 degrees off
    ({"dockingStatus": "not_on_dock"}, (4.10, -0.75, -3.1316), REC, LOG, ("big_room", True)),
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, None, LOG, (None, False)),
    # a dock record off the dock is a robot pulled off it
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, dict(REC, place="dock"), LOG, (None, False)),
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, dict(REC, place="lobby"), LOG, (None, False)),
    ({"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE, dict(REC, pose=[1, 2]), LOG, (None, False)),
])
def test_where_am_i(power, pose, rec, newest, want):
    assert nav.where_am_i(_places(), power, pose, rec, newest) == want


# -- legs and refusals -----------------------------------------------------------------

def _with_lobby(route_bytes):
    doc = _places_doc(route_bytes)
    doc["places"].append({"id": "lobby", "name_th": "ล็อบบี้", "name_en": "lobby", "kind": "room"})
    return doc


@pytest.mark.parametrize("here,place,origin,battery,want", [
    ("dock", "big_room", "tap", 80, None),
    ("dock", "nowhere", "tap", 80, "unknown_place"),
    (None, "big_room", "tap", 80, "location_unknown"),
    ("dock", "dock", "tap", 80, "already_there"),
    ("dock", "lobby", "tap", 80, "no_leg"),
    ("dock", "big_room", "voice", 80, "leg_unverified"),
    ("dock", "big_room", "tap", 29, "low_battery"),
    ("dock", "big_room", "tap", None, "low_battery"),
    ("big_room", "dock", "tap", 10, None),        # home is allowed lower than out
])
def test_leg_lookup(here, place, origin, battery, want):
    places = rp.parse(_with_lobby(json.dumps({"steps": ROUTE_STEPS}).encode()))
    leg, why = nav.plan_leg(places, here, place, origin, battery)
    assert why == want
    assert (leg is None) == (want is not None)


@pytest.mark.parametrize("place,origin,run_id", [
    ("big_room", "tap", RUN_ID.upper()),
    ("big_room", "tap", RUN_ID[:-1]),
    ("big_room", "tap", RUN_ID + "\n"),
    ("big_room", "tap", ""),
    ("Big_room", "tap", RUN_ID),
    ("big_room;reboot", "tap", RUN_ID),
    ("big_room", "pc", RUN_ID),
])
def test_bad_ids_are_refused_before_anything(world, place, origin, run_id):
    assert world.go(place, origin, run_id) == 3
    assert world.final["state"] == "refused" and world.final["reason"] == "bad_request"
    assert world.board.calls == [] and world.spawned == []


def test_a_live_run_pid_refuses_and_leaves_its_record_alone(world):
    world.paths.run_pid.write_text("777\n")
    world.alive_pids.add(777)
    record = nav.new_status(state="running", origin="pc", child_pid=777)
    world.paths.status.write_text(json.dumps(record))
    assert world.go() == 3
    assert world.final["reason"] == "run_active" and world.spawned == []
    # a refusal from a launcher that never owned the run does not overwrite it
    assert json.loads(world.paths.status.read_text()) == record


def test_a_launcher_still_in_its_checks_holds_the_lock(world):
    world.paths.status.write_text(json.dumps(nav.new_status(state="starting", launcher_pid=555)))
    world.launcher(555)
    assert world.go() == 3 and world.final["reason"] == "run_active"
    world.alive_pids.discard(555)                     # that launcher died: its record holds nothing
    world.child_kw = {"exit_at": 1.0}
    world.drive(state="done", step=10, pose=list(BIG_ROOM_POSE), run_log="x.jsonl")
    world.go()
    assert world.spawned


def test_places_not_matching_the_deployed_route_refuse_everything(tmp_path):
    def wrong_sha(route_bytes):
        doc = _places_doc(route_bytes)
        doc["route_sha256"] = "0" * 64
        return doc
    w = World(tmp_path, places_doc=wrong_sha)
    assert w.go() == 3
    assert w.final["reason"] == "places_stale" and w.spawned == [] and "sha256" in w.final["detail"]


def test_an_invalid_places_file_is_stale_not_repaired(world):
    doc = json.loads(world.paths.places.read_text(encoding="utf-8"))
    doc["legs"][0]["to_step"] = 9                     # a door: a leg must end standing still
    world.paths.places.write_text(json.dumps(doc), encoding="utf-8")
    assert world.go() == 3 and world.final["reason"] == "places_stale" and world.spawned == []


def test_unknown_place_and_unreachable_board(world, tmp_path):
    assert world.go("lobby") == 3 and world.final["reason"] == "unknown_place"
    w = World(tmp_path / "b", board=FakeBoard(fail=True))
    assert w.go() == 3 and w.final["reason"] == "board_unreachable" and w.spawned == []


@pytest.mark.parametrize("setup,place,origin,want", [
    (dict(docking="not_on_dock"), "big_room", "tap", "location_unknown"),
    (dict(), "dock", "tap", "already_there"),
    (dict(), "big_room", "voice", "leg_unverified"),
    (dict(battery=12), "big_room", "tap", "low_battery"),
])
def test_go_refusals_name_their_reason_and_spawn_nothing(tmp_path, setup, place, origin, want):
    w = World(tmp_path, board=FakeBoard(**setup))
    assert w.go(place, origin) == 3
    assert w.final["state"] == "refused" and w.final["reason"] == want and w.spawned == []
    assert want in rc.NAV_REASONS


# -- the command it spawns ----------------------------------------------------------------

def test_the_leg_command_is_an_argv_with_the_legs_own_steps(world):
    world.child_kw = {"exit_at": 0.6}
    world.drive(state="done", step=10, pose=list(BIG_ROOM_POSE), run_log="a.jsonl")
    world.go()
    (cmd,) = world.spawned
    assert cmd[:4] == [str(world.paths.py), "-u", "scripts/drive_route.py", ROUTE_REL]
    s = " ".join(cmd)
    assert "--from-step 1 --to-step 10" in s and "--min-battery 30" in s
    assert "--relay http://192.168.11.1:1448" in s and "--sdk 192.168.11.1:1445" in s
    assert cmd[cmd.index("--adb-serial") + 1] == ""
    for flag in ("--arms-in", "--watch-parent", "--status-file", "--state-out"):
        assert flag in cmd
    # review finding 10: the launcher's own pid, not "whatever parent drive_route finds"
    assert cmd[cmd.index("--watch-parent") + 1] == "1000" == str(world.env.my_pid)
    assert "--state-in" not in cmd                    # from the dock: a fresh odometry frame


def test_state_in_only_with_an_accepted_here_and_the_same_legs_state(tmp_path):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE),
              child_kw={"exit_at": 0.6})
    log = w.here()
    w.paths.route_state.write_text(json.dumps({"format": 1, "run_log": log, "measured_at": {},
                                               "door_seen": {}}))
    w.go("dock")
    assert "--state-in" in w.spawned[0]
    assert "--from-step 10 --to-step 20" in " ".join(w.spawned[0])
    w2 = World(tmp_path / "2", board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE),
               child_kw={"exit_at": 0.6})
    w2.here()
    w2.paths.route_state.write_text(json.dumps({"format": 1, "run_log": "older.jsonl", "measured_at": {},
                                                "door_seen": {}}))
    w2.go("dock")
    assert "--state-in" not in w2.spawned[0]


# -- the run, its status, and how it ends --------------------------------------------------

def test_status_merge():
    st = nav.new_status(state="starting", step_from=1, step_to=10)
    before = nav.merge_drive(st, {"state": "running", "step": None, "run_log": "a.jsonl"})
    assert before["state"] == "starting" and before["run_log"] == "a.jsonl"   # its preflight: nothing moved
    on = nav.merge_drive(st, {"state": "running", "step": 4, "do": "door", "door": "D8", "label": "D8 out",
                              "pose": [-2.44, 0.25, -1.571], "step_from": 1, "step_to": 10})
    assert (on["state"], on["step"], on["do"], on["door"], on["label"]) == ("running", 4, "door", "D8", "D8 out")
    assert on["pose"] == [-2.44, 0.25, -1.571]
    done = nav.merge_drive(on, {"state": "done", "step": 10})
    assert done["state"] == "running"                 # terminal only from the exit code
    assert nav.merge_drive(st, None) == st


@pytest.mark.parametrize("code,drive,recorded,in_log,want", [
    (0, {"state": "done"}, None, False, ("arrived", None)),
    (0, None, None, True, ("arrived", None)),         # leg_done / route_done in its log
    (0, {"state": "running"}, None, False, ("failed", "error")),
    (3, {"reason_code": "battery_low"}, None, False, ("refused", "refused")),
    (1, {"reason_code": "door_blocked"}, None, False, ("stopped", "door_blocked")),
    (1, {"reason_code": "dock_blocked"}, None, False, ("stopped", "dock_blocked")),
    (1, {"reason_code": "link_lost"}, None, False, ("stopped", "link_lost")),
    (1, {"reason_code": "something new"}, None, False, ("stopped", "stopped")),
    (1, None, None, False, ("stopped", "stopped")),
    (130, None, "stop_pressed", False, ("stopped", "stop_pressed")),
    (130, None, "app_lost", False, ("stopped", "app_lost")),
    (130, {"reason_code": "launcher_lost"}, None, False, ("stopped", "launcher_lost")),
    (130, None, None, False, ("stopped", "interrupted")),
    (-2, None, "stop_pressed", False, ("stopped", "stop_pressed")),
    (2, None, None, False, ("failed", "board_unreachable")),
    (-9, None, None, False, ("failed", "error")),
    # review findings 11/25: killed after a stop this launcher recorded -- the app's
    # kill -9 2 s after STOP, or our own after the grace -- is that stop
    (-9, None, "stop_pressed", False, ("stopped", "stop_pressed")),
    (-9, {"state": "interrupted"}, "app_lost", False, ("stopped", "app_lost")),
    (-15, None, "launcher_lost", False, ("stopped", "launcher_lost")),
    # review finding 27: drive_route's own uncaught exception, never "stopped mid-way"
    (4, None, None, False, ("failed", "error")),
    (7, None, None, False, ("failed", "error")),
])
def test_exit_code_to_state(code, drive, recorded, in_log, want):
    state, reason = nav.final_state(code, drive, recorded, done_in_log=in_log)
    assert (state, reason) == want
    assert state in rc.NAV_STATES and (reason is None or reason in rc.NAV_REASONS)


def _arrive_at(world, t_done=2.0, run_log="20261012-110000_floor2_bigroom_roundtrip.jsonl", step=10,
               pose=BIG_ROOM_POSE):
    world.child_kw = {"exit_at": t_done + 0.1, "code": 0}

    def drive_route(now):
        if now >= 0.5 and world.paths.drive_status.exists() is False:
            world.drive(step=None, run_log=run_log)
        if now >= 1.0 and json.loads(world.paths.drive_status.read_text())["step"] is None:
            world.drive(step=1, do="checkpoint", label="on the dock", run_log=run_log)
        if now >= t_done:
            world.drive(state="done", step=step, pose=list(pose), run_log=run_log)
    world.clock.hooks.append(drive_route)
    return run_log


def test_an_arrival_writes_here_with_the_end_pose_and_this_runs_log(world):
    run_log = _arrive_at(world)
    assert world.go() == 0
    assert world.final["state"] == "arrived" and world.final["here"] == "big_room"
    here = json.loads(world.paths.here.read_text())
    assert here["place"] == "big_room" and here["run_log"] == run_log
    assert here["pose"] == pytest.approx(list(BIG_ROOM_POSE))
    assert not world.paths.run_pid.exists()           # the lock goes with the run
    assert [l["state"] for l in world.lines][:2] == ["starting", "starting"]
    assert "running" in [l["state"] for l in world.lines]


def test_here_survives_until_the_first_step_status(tmp_path):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    w.here()
    w.paths.route_state.write_text(json.dumps({"format": 1, "run_log": LOG, "measured_at": {}, "door_seen": {}}))
    seen = {}

    def watch(now):
        if abs(now - 0.2) < 1e-6:
            seen["at_spawn"] = w.paths.here.exists()
        if abs(now - 1.0) < 1e-6:
            seen["before_step"] = w.paths.here.exists()
        if abs(now - 2.0) < 1e-6:
            seen["after_step"] = w.paths.here.exists()
            seen["state_after_step"] = w.paths.route_state.exists()
    w.child_kw = {"exit_at": 3.0, "code": 1}

    def drive_route(now):
        if now >= 0.3 and not w.paths.drive_status.exists():
            w.drive(step=None, run_log="b.jsonl")
        if now >= 1.5:
            w.drive(step=10, do="checkpoint", label="inside the big room", run_log="b.jsonl")
        if now >= 2.8:
            w.drive(state="stopped", step=11, reason_code="door_blocked", reason="door D11: ...",
                    run_log="b.jsonl")
    w.clock.hooks += [drive_route, watch]
    assert w.go("dock") == 1
    assert seen == {"at_spawn": True, "before_step": True, "after_step": False, "state_after_step": False}
    assert w.final["state"] == "stopped" and w.final["reason"] == "door_blocked"
    assert w.final["detail"] == "door D11: ..." and w.final["here"] is None
    assert not w.paths.here.exists()


def test_every_leg_shares_one_door_memory_the_launcher_never_deletes(tmp_path):
    # 9 ต.ค.: a door's middle is a place in the route's frame, kept across runs --
    # not route_state.json (one leg's odometry), which goes the moment the robot moves
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    w.here()
    mem = w.paths.nav / "door_memory.json"
    mem.write_text(json.dumps({"format": 1, "doors": {}}))
    w.child_kw = {"exit_at": 3.0, "code": 1}

    def drive_route(now):
        if now >= 1.5:
            w.drive(step=10, do="checkpoint", label="inside the big room", run_log="b.jsonl")
        if now >= 2.8:
            w.drive(state="stopped", step=11, reason_code="door_blocked", reason="door D11: ...", run_log="b.jsonl")
    w.clock.hooks.append(drive_route)
    assert w.go("dock") == 1
    (cmd,) = w.spawned
    assert (w.paths.code / cmd[cmd.index("--door-memory") + 1]).resolve() == mem.resolve()
    assert mem.exists() and not w.paths.here.exists()                   # the place is forgotten, the doors not


def test_a_refusal_keeps_here_and_points_it_at_the_refusing_runs_log(tmp_path):
    """drive_route makes a new jsonl even to refuse. Without the rewrite the
    "newest log" rule makes a robot that never moved "location unknown" --
    the next tap is refused for a robot standing exactly where it was."""
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    old = w.here()
    w.paths.route_state.write_text(json.dumps({"format": 1, "run_log": old, "measured_at": {}, "door_seen": {}}))
    new = "20261012-113000_floor2_bigroom_roundtrip.jsonl"
    w.child_kw = {"exit_at": 1.0, "code": 3}

    def refuses(now):
        if now >= 0.5 and not (w.paths.runs / new).exists():
            w.log(new, {"kind": "run_start"}, {"kind": "refused", "code": "action_running"})
            w.drive(state="refused", reason_code="action_running", reason="an action is already running",
                    run_log=new)
    w.clock.hooks.append(refuses)
    assert w.go("dock") == 3
    assert w.final["state"] == "refused" and w.final["reason"] == "refused"
    assert json.loads(w.paths.here.read_text())["run_log"] == new
    assert json.loads(w.paths.route_state.read_text())["run_log"] == new
    # and the next go knows where the robot is
    w.lines.clear()
    w.clock.hooks.clear()
    w.child_kw = {"exit_at": 0.6}
    w.go("dock")
    assert w.lines[-1]["reason"] != "location_unknown" and len(w.spawned) == 2


def test_a_refusal_does_not_revive_a_record_that_was_already_stale(tmp_path):
    w = World(tmp_path)                               # on the dock: here.json not used
    w.here(run_log="20261001-090000_old.jsonl")
    w.log("20261005-090000_pc.jsonl", {"kind": "run_start"})
    w.child_kw = {"exit_at": 1.0, "code": 3}
    w.clock.hooks.append(lambda now: now >= 0.5 and w.drive(state="refused", run_log="20261012-1_x.jsonl"))
    w.go()
    assert json.loads(w.paths.here.read_text())["run_log"] == "20261001-090000_old.jsonl"


def test_a_stopped_run_forgets_where_it_was_even_if_it_ended_between_polls(tmp_path):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    w.here()
    w.child_kw = {"exit_at": 0.2, "code": 1}
    # the step and the stop both land between two polls, and the process is gone by the next
    w.clock.hooks.append(lambda now: now >= 0.2 and w.drive(state="stopped", step=11, reason_code="obstacle",
                                                            run_log="c.jsonl"))
    w.go("dock")
    assert all(l["state"] == "starting" for l in w.lines[:-1])     # no poll ever saw it running
    assert not w.paths.here.exists() and w.final["reason"] == "obstacle"


def test_every_status_line_carries_the_run_id_and_the_launcher_pid(world):
    _arrive_at(world)
    world.go()
    for line in world.lines:
        assert line["run_id"] == RUN_ID and line["launcher_pid"] == 1000 and line["format"] == 1
        assert line["state"] in rc.NAV_STATES and line["state"] != "preparing"   # the app's, never ours
    assert json.loads(world.paths.status.read_text())["state"] == "arrived"


# -- the tether -------------------------------------------------------------------------------

def _pinging(world, tether, until):
    def ping(now):
        if now <= until and abs(now - round(now)) < 1e-9:
            tether.feed("ping\n")
    world.clock.hooks.append(ping)


def test_pings_keep_a_run_going(world):
    tether = nav.Tether(world.clock)
    _pinging(world, tether, until=100)
    _arrive_at(world, t_done=12.0)
    assert world.go(tether=tether) == 0
    assert world.child.interrupts == [] and world.final["state"] == "arrived"


def test_four_seconds_without_a_ping_interrupts_app_lost(world):
    tether = nav.Tether(world.clock)
    _pinging(world, tether, until=3)
    world.child_kw = {"exit_at": 60.0}
    assert world.go(tether=tether) == 1
    (at,) = world.child.interrupts
    assert 7.0 < at <= 7.4                            # last ping at 3 s, + TETHER_S
    assert world.final["state"] == "stopped" and world.final["reason"] == "app_lost"


def test_eof_on_the_pipe_interrupts_app_lost(world):
    tether = nav.Tether(world.clock)
    _pinging(world, tether, until=100)
    world.child_kw = {"exit_at": 60.0}
    world.clock.hooks.append(lambda now: now >= 2.0 and tether.close())
    world.go(tether=tether)
    assert world.child.interrupts and world.child.interrupts[0] <= 2.2
    assert world.final["reason"] == "app_lost"


def test_a_stop_line_interrupts_stop_pressed(world):
    tether = nav.Tether(world.clock)
    _pinging(world, tether, until=100)
    world.child_kw = {"exit_at": 60.0}
    world.clock.hooks.append(lambda now: now >= 2.0 and tether.feed("stop"))
    world.go(tether=tether)
    assert len(world.child.interrupts) == 1           # once: a second SIGINT lands in the cleanup
    assert world.final["state"] == "stopped" and world.final["reason"] == "stop_pressed"


def test_a_stop_during_the_checks_spawns_nothing(tmp_path):
    """Safety review #2: "accepted" means queued, and the STOP bar pressed
    while the launcher is still checking must not leave a leg to start after
    it. The stop arrives while the board is being read (step 3)."""
    tether = None

    def press():
        tether.feed("stop\n")
    w = World(tmp_path, board=FakeBoard(on_power=press))
    tether = nav.Tether(w.clock)
    assert w.go(tether=tether) == 1
    assert w.spawned == []
    assert w.final["state"] == "stopped" and w.final["reason"] == "stop_pressed"


def test_an_app_gone_during_the_checks_spawns_nothing(tmp_path):
    tether = None
    w = World(tmp_path, board=FakeBoard(on_power=lambda: tether.close()))
    tether = nav.Tether(w.clock)
    w.go(tether=tether)
    assert w.spawned == [] and w.final["reason"] == "app_lost"


def test_a_sigint_to_the_launcher_itself_is_a_stop(world):
    flags = nav.Flags()
    world.child_kw = {"exit_at": 60.0}
    world.clock.hooks.append(lambda now: now >= 1.0 and setattr(flags, "stop", True))
    world.go(flags=flags)
    assert world.child.interrupts and world.final["reason"] == "stop_pressed"


class _TimedBoard(FakeBoard):
    """Records when the board's action was cancelled, and can refuse to."""

    def __init__(self, clock, cancel_error=None, **kw):
        super().__init__(**kw)
        self.clock, self.cancel_error, self.cancelled_at = clock, cancel_error, []

    def cancel(self):
        if self.cancel_error is not None:
            self.calls.append("cancel")
            raise self.cancel_error
        self.cancelled_at.append(self.clock())
        super().cancel()


def _hung_child_world(tmp_path, **board_kw):
    """A controller that ignores SIGINT, with a GoHome running on the board."""
    w = World(tmp_path)
    w.board = _TimedBoard(w.clock, current={"action_name": "slamtec.agent.actions.GoHomeAction"}, **board_kw)
    w.env.board = w.board
    w.child_kw = {"exit_at": 120.0, "on_interrupt": None}
    return w


def test_a_child_that_will_not_stop_is_killed_after_the_grace(tmp_path):
    w = _hung_child_world(tmp_path)
    tether = nav.Tether(w.clock)
    w.clock.hooks.append(lambda now: now >= 1.0 and tether.feed("stop"))
    _pinging(w, tether, until=1000)
    w.go(tether=tether)
    (at,) = w.child.interrupts
    (killed,) = w.child.kills
    assert killed - at == pytest.approx(nav.INTERRUPT_GRACE_S, abs=0.3)
    # review finding 25: the guest pressed STOP -- not "a navigation error"
    assert (w.final["state"], w.final["reason"]) == ("stopped", "stop_pressed")
    # review finding 11: the action the hung controller started is cancelled, after the kill
    assert w.board.cancelled == 1 and w.board.cancelled_at[0] >= killed
    assert not w.paths.run_pid.exists()


def test_a_killed_orphan_of_a_gone_app_still_has_its_board_action_cancelled(tmp_path):
    """Review finding 11's probe: the app is gone (SIGTERM/SIGHUP to the
    launcher), the controller hangs. Only the app's own escalation used to
    DELETE the action -- the one escalation that is missing here."""
    w = _hung_child_world(tmp_path)
    flags = nav.Flags()
    w.clock.hooks.append(lambda now: now >= 1.0 and setattr(flags, "gone", True))
    w.go(flags=flags)
    assert w.child.kills and w.board.cancelled == 1
    assert (w.final["state"], w.final["reason"]) == ("stopped", "app_lost")
    assert nav.reap(w.env) == {"reap": "nothing"}     # nothing left over for the next start


def test_a_killed_child_whose_cancel_failed_leaves_run_pid_for_reap(tmp_path):
    w = _hung_child_world(tmp_path, cancel_error=nav.BoardError("DELETE :current: timeout"))
    w.here(place="dock")                              # any record: a killed run may have moved
    flags = nav.Flags()
    w.clock.hooks.append(lambda now: now >= 1.0 and setattr(flags, "stop", True))
    w.go(flags=flags)
    assert w.child.kills and "not cancelled" in w.final["detail"]
    assert int(w.paths.run_pid.read_text()) == w.child.pid           # kept: names a dead pid
    assert not w.paths.here.exists() and w.final["here"] is None
    w.alive_pids.discard(w.child.pid)
    w.board.cancel_error = None
    assert nav.reap(w.env)["reap"] == "cancelled_board_action"       # the next app start retries it


# -- where the robot is after a run that never moved (review finding 27) -------------------------

NEW_LOG = "20261012-113000_floor2_bigroom_roundtrip.jsonl"


def _from_big_room(tmp_path, code, *, log_events=None, drive=None, at=0.5):
    """A leg back from big_room, whose drive_route ends with `code` at `at` s
    after (maybe) writing `log_events` to a new log and `drive` as its status."""
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=BIG_ROOM_POSE))
    old = w.here()
    w.paths.route_state.write_text(json.dumps({"format": 1, "run_log": old, "measured_at": {}, "door_seen": {}}))
    w.child_kw = {"exit_at": at + 0.1, "code": code}

    def drive_route(now):
        if now >= at and log_events is not None and not (w.paths.runs / NEW_LOG).exists():
            w.log(NEW_LOG, *log_events)
        if now >= at and drive is not None and not w.paths.drive_status.exists():
            w.drive(**drive)
    w.clock.hooks.append(drive_route)
    return w


@pytest.mark.parametrize("code,want", [
    (130, ("stopped", "interrupted")),               # SIGINT while drive_route was still importing
    (-2, ("stopped", "interrupted")),
    (4, ("failed", "error")),                         # drive_route's own uncaught exception
])
def test_a_run_that_never_stepped_keeps_where_the_robot_is(tmp_path, code, want):
    w = _from_big_room(tmp_path, code, log_events=[{"kind": "run_start"}])
    w.go("dock")
    assert (w.final["state"], w.final["reason"]) == want
    assert w.final["here"] == "big_room"
    assert json.loads(w.paths.here.read_text())["run_log"] == NEW_LOG
    assert json.loads(w.paths.route_state.read_text())["run_log"] == NEW_LOG
    # and the next where_am_i agrees with what the final line said
    assert nav.where_am_i(_places(), {"dockingStatus": "not_on_dock"}, BIG_ROOM_POSE,
                          json.loads(w.paths.here.read_text()), nav.newest_log(w.paths.runs)) == ("big_room", True)


def test_a_run_that_died_before_any_status_or_log_keeps_the_place(tmp_path):
    w = _from_big_room(tmp_path, -2)                  # no new log, no status: it never got going
    w.go("dock")
    assert w.final["here"] == "big_room" and w.paths.here.exists()


def test_a_step_in_the_log_counts_even_when_the_status_never_showed_it(tmp_path):
    # the status write can fail; the log is the record
    w = _from_big_room(tmp_path, 1, log_events=[{"kind": "run_start"}, {"kind": "step", "i": 10}])
    w.go("dock")
    assert not w.paths.here.exists() and w.final["here"] is None


def test_a_stop_pressed_before_the_first_step_keeps_the_place(tmp_path):
    w = _from_big_room(tmp_path, 130, log_events=[{"kind": "run_start"}],
                       drive={"state": "running", "step": None, "run_log": NEW_LOG}, at=0.3)
    w.child_kw = {"exit_at": 60.0}                    # it ends when interrupted
    flags = nav.Flags()
    w.clock.hooks.append(lambda now: now >= 0.6 and setattr(flags, "stop", True))
    w.go("dock", flags=flags)
    assert (w.final["state"], w.final["reason"], w.final["here"]) == ("stopped", "stop_pressed", "big_room")
    assert json.loads(w.paths.here.read_text())["run_log"] == NEW_LOG


def test_the_tether_reader_feeds_lines_and_eof():
    import io
    clock = FakeClock()
    t = nav.Tether(clock)
    clock.now = 3.0
    th = t.read(io.StringIO("ping\nstop\n"))
    th.join(2)
    assert t.last == 3.0 and t.stop and t.eof


def test_the_reader_starts_before_argparse_and_the_checks(monkeypatch):
    # main() must start the pipe reader before anything else that can take time
    order = []
    monkeypatch.setattr(nav.Tether, "read", lambda self, stream: order.append("reader"))
    monkeypatch.setattr(nav.argparse, "ArgumentParser",
                        lambda *a, **k: order.append("argparse") or (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        nav.main(["go", "big_room", "--origin", "tap", "--run-id", RUN_ID, "--tether"])
    assert order == ["reader", "argparse"]


# -- status / places / stop / reap / check ---------------------------------------------------------

def test_status_idle_pc_run_own_run_and_a_dead_launcher(world):
    assert nav.status(world.env)["state"] == "idle"
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    pc = nav.status(world.env)
    assert (pc["state"], pc["origin"], pc["reason"], pc["child_pid"]) == ("running", "pc", "pc_run", 777)
    own = nav.new_status(state="running", origin="tap", child_pid=777, launcher_pid=900, run_id=RUN_ID)
    world.paths.status.write_text(json.dumps(own))
    assert nav.status(world.env) == own
    world.alive_pids.discard(777)
    assert nav.status(world.env)["reason"] == "launcher_lost"   # nothing alive, record never finished
    world.paths.status.write_text(json.dumps(dict(own, state="arrived")))
    assert nav.status(world.env)["state"] == "arrived"


def test_places_caps_from_the_dock(world):
    caps = nav.places_caps(world.env)
    assert caps["installed"] and caps["here"] == "dock" and caps["battery"] == 80 and caps["run"] is None
    assert caps["places_sha256"] == rp.sha256_file(world.paths.places)
    cards = {c["id"]: c for c in caps["places"]}
    assert cards["dock"]["available"] is False and cards["dock"]["reason"] == "already_there"
    big = cards["big_room"]
    assert big["available"] and big["verified"] is False and big["doors"] == ["D8", "D11"]
    assert big["name_th"] == "ห้องประชุมใหญ่" and big["min_battery"] == 30 and big["est_s"] is None
    assert "enabled" not in caps                      # the app's switch, the app adds it
    assert len(json.dumps(caps).encode()) < rc.MAX_INBOUND_BYTES


def test_places_caps_while_running_or_board_down_or_stale(tmp_path, world):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    caps = nav.places_caps(world.env)
    assert caps["run"]["reason"] == "pc_run"
    assert {c["reason"] for c in caps["places"]} == {"run_active"}
    w = World(tmp_path / "x", board=FakeBoard(fail=True))
    assert {c["reason"] for c in nav.places_caps(w.env)["places"]} == {"board_unreachable"}
    (w.paths.code / ROUTE_REL).write_text("{}")
    caps = nav.places_caps(w.env)
    assert caps["places"] == [] and caps["reason"] == "places_stale"


def test_stop_signals_run_pid_and_a_launcher_still_checking(world):
    assert nav.stop(world.env) == {"stop": "none", "pids": []}
    world.paths.status.write_text(json.dumps(nav.new_status(state="starting", launcher_pid=900)))
    world.launcher(900)
    assert nav.stop(world.env)["pids"] == [900]       # no child yet: the launcher hears it
    world.signalled.clear()
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    assert nav.stop(world.env)["pids"] == [777]       # a child: only the child, never twice into it
    assert world.signalled == [777]


def test_a_stop_the_kernel_refused_is_denied_not_none(world):
    """Review finding 2: a root run (the face app's, through su) stopped from
    the adb shell's uid gets EPERM. That read "none" -- nothing running --
    while the robot walked."""
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.signal_result = "denied"
    assert nav.stop(world.env) == {"stop": "denied", "pids": [777]}
    world.signal_result = "gone"                      # ended between the probe and the signal
    assert nav.stop(world.env) == {"stop": "none", "pids": []}


def test_send_sigint_tells_denied_from_gone(monkeypatch):
    def kill(pid, sig):
        raise {1: PermissionError, 2: ProcessLookupError}[pid]()
    monkeypatch.setattr(nav.os, "kill", kill)
    assert nav.send_sigint(1) == "denied" and nav.send_sigint(2) == "gone"
    monkeypatch.setattr(nav.os, "kill", lambda pid, sig: None)
    assert nav.send_sigint(3) == "sent"


def _pc_run(world, dies_at=None):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.here(place="big_room")
    if dies_at is not None:
        world.clock.hooks.append(lambda now: now >= dies_at and world.alive_pids.discard(777))


def test_stop_with_wait_reports_stopped_when_drive_route_ends(world):
    _pc_run(world, dies_at=1.0)
    out = nav.stop(world.env, wait_s=nav.STOP_WAIT_S)
    assert out == {"stop": "stopped", "pids": [777]}
    assert world.killed == [] and world.board.cancelled == 0 and world.paths.here.exists()


def test_stop_with_wait_escalates_a_run_that_does_not_end(tmp_path):
    """Review finding 1's defence in depth: a SIGINT nobody heard (a run that
    inherited it ignored, or a hung cleanup) was reported "sent" and the robot
    drove on. Now: kill -9 after the wait, then the board's DELETE -- the
    order the app's own STOP uses."""
    w = World(tmp_path)
    w.board = _TimedBoard(w.clock, current={"action_name": "slamtec.agent.actions.MoveToAction"})
    w.env.board = w.board
    _pc_run(w)
    kill_at = []
    w.env.kill_pid = lambda pid: kill_at.append(w.clock()) or w.kill(pid)
    out = nav.stop(w.env, wait_s=nav.STOP_WAIT_S)
    assert out["stop"] == "escalated" and out["board"] == "cancelled" and w.killed == [777]
    assert kill_at[0] >= nav.STOP_WAIT_S and w.board.cancelled_at[0] >= kill_at[0]
    assert not w.paths.run_pid.exists() and not w.paths.here.exists()
    # without a wait (the app's call: it escalates by itself) nothing more than the SIGINT
    w2 = World(tmp_path / "2")
    _pc_run(w2)
    assert nav.stop(w2.env) == {"stop": "sent", "pids": [777]} and w2.killed == []


def test_stop_with_wait_says_so_when_even_the_kill_is_refused(world):
    _pc_run(world)
    world.env.kill_pid = lambda pid: "denied"
    out = nav.stop(world.env, wait_s=1.0)
    assert out["stop"] == "not_stopped" and world.board.cancelled == 0 and world.paths.run_pid.exists()


def test_a_reused_pid_in_a_stale_run_pid_is_not_a_run(world):
    """Review finding 12: a PC run never removed run.pid; once Android reused
    the number, STOP signalled whatever had it, `status` called it a PC run
    that locked every card, and `go` was refused."""
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.cmdlines[777] = "com.android.systemui"
    assert nav.status(world.env)["state"] == "idle"
    assert nav.stop(world.env) == {"stop": "none", "pids": []} and world.signalled == []
    assert nav.places_caps(world.env)["run"] is None
    world.child_kw = {"exit_at": 0.6}
    world.go()
    assert world.final["reason"] != "run_active" and world.spawned


def test_a_run_pid_whose_cmdline_cannot_be_read_still_counts_as_a_run(world):
    # from the shell's uid a root run's /proc is hidden: never read that as idle
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.cmdlines[777] = None
    assert nav.status(world.env)["reason"] == "pc_run"
    assert world.go() == 3 and world.final["reason"] == "run_active"


def test_a_zombie_drive_route_is_not_running(world):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.cmdlines[777] = ""                          # /proc/<zombie>/cmdline is empty
    assert nav.run_pid_alive(world.env, 777) is False


def test_main_sets_a_umask_the_shell_side_can_read_before_anything(monkeypatch):
    """Review finding 9: under su the app's umask 077 made run.pid, status.json
    and run.out root-only, and the PC's `status` read "idle" while an app leg
    walked."""
    order = []
    monkeypatch.setattr(nav.os, "umask", lambda m: order.append(("umask", m)) or 0o077)
    monkeypatch.setattr(nav.argparse, "ArgumentParser",
                        lambda *a, **k: order.append("argparse") or (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        nav.main(["status"])
    assert order == [("umask", 0o022), "argparse"]


def test_run_out_left_by_an_older_leg_is_made_readable(tmp_path, monkeypatch):
    out = tmp_path / "run.out"
    out.write_bytes(b"old")
    modes = []
    real_chmod = nav.os.chmod
    monkeypatch.setattr(nav.os, "chmod", lambda path, mode: modes.append((Path(path), mode)) or real_chmod(path, mode))
    child = nav.spawn_child([sys.executable, "-c", "pass"], tmp_path, out)
    child.proc.wait(30)
    assert (out, 0o644) in modes


def test_reap_interrupts_the_orphan_of_a_dead_launcher(world):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.paths.status.write_text(json.dumps(nav.new_status(state="running", origin="tap", child_pid=777,
                                                            launcher_pid=900)))
    world.here()
    out = nav.reap(world.env)
    assert out["reap"] == "interrupted_orphan" and world.signalled == [777]
    assert not world.paths.here.exists()              # it may have moved before its launcher saw a step
    assert json.loads(world.paths.status.read_text())["reason"] == "app_lost"


@pytest.mark.parametrize("record", [
    None,                                                                      # robot_python run
    nav.new_status(state="running", origin="pc", child_pid=777),
    nav.new_status(state="arrived", origin="tap", child_pid=111, launcher_pid=900),   # an old run's record
    nav.new_status(state="running", origin="voice", child_pid=777, launcher_pid=900),  # launcher alive
])
def test_reap_leaves_a_pc_run_and_a_live_launcher_alone(world, record):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.launcher(900)
    if record is not None:
        if record["origin"] == "voice":
            pass
        else:
            world.alive_pids.discard(900)
        world.paths.status.write_text(json.dumps(record))
    world.here()
    out = nav.reap(world.env)
    assert out["reap"] == "left_alone" and world.signalled == [] and world.board.cancelled == 0
    assert world.paths.here.exists()


def test_reap_cancels_a_board_action_nobody_drives_and_drops_the_dead_pid(tmp_path):
    w = World(tmp_path, board=FakeBoard(current={"action_id": 5}))
    w.paths.run_pid.write_text("777")                 # dead: killed with the app's cgroup
    w.here()
    assert nav.reap(w.env)["reap"] == "cancelled_board_action"
    assert not w.paths.here.exists()
    assert w.board.cancelled == 1 and not w.paths.run_pid.exists()
    # the next app start does not cancel an action somebody else started since
    w.board._current = {"action_id": 6}
    assert nav.reap(w.env) == {"reap": "nothing"} and w.board.cancelled == 1


def test_reap_with_no_action_and_a_board_that_does_not_answer(tmp_path):
    w = World(tmp_path)
    w.paths.run_pid.write_text("777")
    assert nav.reap(w.env)["reap"] == "nothing_running" and w.board.cancelled == 0
    w2 = World(tmp_path / "2", board=FakeBoard(fail=True))
    w2.board.current = lambda: (_ for _ in ()).throw(nav.BoardError("timeout"))
    w2.paths.run_pid.write_text("777")
    assert "unreachable" in nav.reap(w2.env)["board"]
    assert w2.paths.run_pid.exists()                  # try again next start


def test_check_is_read_only_and_names_what_differs(world):
    files = {"scripts/emma_nav.py": b"code", "app/robot_places.py": b"places"}
    for rel, data in files.items():
        (world.paths.code / rel).parent.mkdir(parents=True, exist_ok=True)
        (world.paths.code / rel).write_bytes(data)
    man = {"deployed_at": "2026-10-12T10:00:00+0700",
           "files": {rel: hashlib.sha256(d).hexdigest() for rel, d in files.items()}}
    man["files"]["app/robot_contracts.py"] = "0" * 64
    world.paths.manifest.write_text(json.dumps(man))
    (world.paths.code / "app/robot_places.py").write_bytes(b"edited on the robot")
    before = sorted((p, p.stat().st_mtime_ns) for p in world.paths.remote.rglob("*"))
    out = nav.check(world.env)
    assert sorted((p, p.stat().st_mtime_ns) for p in world.paths.remote.rglob("*")) == before
    assert out["manifest"]["changed"] == ["app/robot_places.py"]
    assert out["manifest"]["missing"] == ["app/robot_contracts.py"]
    assert out["manifest"]["deployed_at"] == "2026-10-12T10:00:00+0700" and not out["ok"]
    assert out["places"]["ok"] and out["board"]["reachable"] and out["here"] == "dock"
    assert world.board.cancelled == 0


# -- process plumbing ---------------------------------------------------------------------------

def test_every_line_is_flushed_before_the_process_ends():
    """stdout is the app's pipe: block-buffered, a "running" line would reach
    the app seconds after the robot started moving (build review #13). Run
    WITHOUT -u, so only the code's own flush can make the first line arrive
    while the process is still waiting."""
    code = ("import importlib.util,sys;"
            "s=importlib.util.spec_from_file_location('n', %r);m=importlib.util.module_from_spec(s);"
            "sys.modules['n']=m;s.loader.exec_module(m);m.emit({'a':'แท่นชาร์จ'});sys.stdin.readline();m.emit({'b':2})"
            % str(ROOT / "scripts" / "emma_nav.py"))
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=str(ROOT), encoding="utf-8")
    got = {}
    reader = threading.Thread(target=lambda: got.setdefault("first", proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(15)
    try:
        assert "first" in got, "the first line did not arrive while the process was still running"
        assert proc.poll() is None
        assert json.loads(got["first"]) == {"a": "แท่นชาร์จ"}
        proc.stdin.write("\n")
        proc.stdin.flush()
        rest, _ = proc.communicate(timeout=15)
        assert json.loads(rest.strip()) == {"b": 2}
    finally:
        if proc.poll() is None:
            proc.kill()


def test_every_print_in_the_launcher_flushes():
    import ast
    tree = ast.parse((ROOT / "scripts" / "emma_nav.py").read_text(encoding="utf-8"))
    prints = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print"]
    assert prints
    for n in prints:
        assert any(k.arg == "flush" and getattr(k.value, "value", None) is True for k in n.keywords), n.lineno


def test_the_launcher_starts_without_numpy_httpx_or_drive_route():
    code = ("import sys,importlib.util;"
            "s=importlib.util.spec_from_file_location('n', %r);m=importlib.util.module_from_spec(s);"
            "sys.modules['n']=m;s.loader.exec_module(m);"
            "print(sorted({k.split('.')[0] for k in sys.modules} & {'numpy','httpx','pythainlp','drive_route',"
            "'analyze_run'}))" % str(ROOT / "scripts" / "emma_nav.py"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, encoding="utf-8", cwd=str(ROOT),
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


def test_pid_alive_never_probes_with_os_kill_on_windows(monkeypatch):
    monkeypatch.setattr(nav.os, "name", "nt")
    killed = []
    monkeypatch.setattr(nav.os, "kill", lambda *a: killed.append(a))
    with pytest.raises(RuntimeError):
        nav.pid_alive(1234)
    assert killed == []


def test_launcher_reasons_are_contract_reasons():
    import ast
    tree = ast.parse((ROOT / "scripts" / "emma_nav.py").read_text(encoding="utf-8"))
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "end" and len(n.args) >= 2:
            if isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str):
                used.add(n.args[1].value)
    assert used and used <= set(rc.NAV_REASONS), used - set(rc.NAV_REASONS)


# -- review 2 (9 Oct) ------------------------------------------------------------------------------
# Finding 7: --arms-in only when the app says the arms rest.

def test_without_arms_rested_a_leg_through_a_door_is_refused_before_anything_moves(world):
    """The app's operator switch nav_require_arm_rest skips restArms, and the
    launcher passed --arms-in anyway: D8/D11 with a margin computed for the
    0.70 m base, an arm possibly out. Without --arms-rested the arms' 0.78 m
    counts, at which those doors cannot be passed -- refused here, not at
    the first door half way through the leg."""
    assert world.go(arms_rested=False) == 3
    assert (world.final["state"], world.final["reason"]) == ("refused", "arms_not_rest")
    assert "D8" in world.final["detail"] and "D11" in world.final["detail"]
    assert world.spawned == []
    assert "arms_not_rest" in rc.NAV_REASONS              # the app's own refusal code: the same Thai


def test_with_arms_rested_the_door_leg_counts_the_base(world):
    world.child_kw = {"exit_at": 0.6}
    world.go(arms_rested=True)
    (cmd,) = world.spawned
    assert "--arms-in" in cmd


def test_a_places_entry_that_forgot_its_doors_does_not_open_the_arms_gate(tmp_path):
    def no_doors(route_bytes):
        doc = _places_doc(route_bytes)
        for leg in doc["legs"]:
            leg["doors"] = []                         # hand-written, and nothing checks it
        return doc
    w = World(tmp_path, places_doc=no_doors)
    assert w.go(arms_rested=False) == 3 and w.final["reason"] == "arms_not_rest"
    assert "D8" in w.final["detail"] and w.spawned == []


CORRIDOR_POSE = (-2.44, -2.45, 0.0)


def _corridor_places(route_bytes):
    """A leg with no door: 'corridor below D8' (step 5) -> 'in front of D11' (step 8)."""
    doc = _places_doc(route_bytes)
    doc["places"] += [{"id": "corridor", "name_th": "ทางเดิน", "name_en": "corridor", "kind": "spot"},
                      {"id": "d11_front", "name_th": "หน้าประตูห้องใหญ่", "name_en": "front of D11", "kind": "spot"}]
    doc["legs"] = [{"from": "corridor", "to": "d11_front", "from_step": 5, "to_step": 8, "min_battery": 10,
                    "verified": False, "est_s": None, "doors": [], "note": ""}]
    return doc


@pytest.mark.parametrize("rested", [False, True])
def test_a_leg_without_doors_runs_either_way_with_the_width_it_was_told(tmp_path, rested):
    w = World(tmp_path, board=FakeBoard(docking="not_on_dock", pose=CORRIDOR_POSE), places_doc=_corridor_places,
              child_kw={"exit_at": 0.6})
    w.here(place="corridor", pose=CORRIDOR_POSE)
    w.go("d11_front", arms_rested=rested)
    (cmd,) = w.spawned
    assert ("--arms-in" in cmd) is rested             # not rested: the arms' width, the safe one


def test_build_command_counts_the_arms_unless_told_otherwise(world):
    leg = rp.leg(_places(), "dock", "big_room")
    assert "--arms-in" not in nav.build_command(world.paths, ROUTE_REL, leg, False, 1000)
    assert "--arms-in" in nav.build_command(world.paths, ROUTE_REL, leg, False, 1000, arms_in=True)
    assert nav.leg_doors(ROUTE_STEPS, leg) == ("D8", "D11")
    assert nav.leg_doors(ROUTE_STEPS, rp.leg(_places(), "big_room", "dock")) == ("D11", "D8")


def test_main_passes_arms_rested_and_hold_through(monkeypatch):
    got = {}
    monkeypatch.setattr(nav, "go", lambda env, place, origin, run_id, **kw: got.update(kw) or 0)
    monkeypatch.setattr(nav, "stop", lambda env, **kw: got.update(kw) or {"stop": "none", "pids": []})
    monkeypatch.setattr(nav, "emit", lambda obj: None)
    monkeypatch.setattr(nav.signal, "signal", lambda *a: None)
    monkeypatch.setattr(nav.os, "umask", lambda m: 0o022)
    nav.main(["go", "big_room", "--origin", "tap", "--run-id", RUN_ID, "--arms-rested"])
    assert got["arms_rested"] is True
    nav.main(["go", "big_room", "--origin", "tap", "--run-id", RUN_ID])
    assert got["arms_rested"] is False                # an older app: the safe width
    nav.main(["stop", "--wait", "10", "--hold"])
    assert got["hold"] is True and got["wait_s"] == 10.0
    nav.main(["stop"])
    assert got["hold"] is False                       # the app's STOP: it latches by itself


# Finding 8: a PC stop while the app is still preparing the walk.

def test_a_pc_stop_with_nothing_on_disk_refuses_the_walk_the_app_was_preparing(world):
    out = nav.stop(world.env, wait_s=nav.STOP_WAIT_S, hold=True)
    assert out == {"stop": "none", "pids": [], "hold_s": nav.STOP_HOLD_S}
    world.clock.now += 12.0                           # the app rested the arms, then spawned us
    assert world.go() == 1
    assert (world.final["state"], world.final["reason"]) == ("stopped", "stop_pressed")
    assert world.spawned == [] and world.board.calls == []
    assert json.loads(world.paths.status.read_text())["state"] == "stopped"
    # one stop, one walk: the next go is not refused
    world.child_kw = {"exit_at": world.clock.now + 0.6}
    world.go()
    assert world.spawned


def test_an_old_hold_or_a_clock_that_went_back_refuses_nothing(world):
    nav.stop(world.env, hold=True)
    world.clock.now += nav.STOP_HOLD_S + 1
    world.child_kw = {"exit_at": world.clock.now + 0.6}
    world.go()
    assert len(world.spawned) == 1 and not world.paths.stop_hold.exists()
    world.paths.stop_hold.write_text(json.dumps({"t": world.env.wall() + 600, "hold_s": nav.STOP_HOLD_S}))
    world.child_kw = {"exit_at": world.clock.now + 0.6}
    world.go()
    assert len(world.spawned) == 2


def test_only_a_stop_that_asks_holds_and_only_when_nothing_ran(world):
    assert nav.stop(world.env) == {"stop": "none", "pids": []}      # the app's STOP: no note
    assert not world.paths.stop_hold.exists()
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    assert nav.stop(world.env, hold=True) == {"stop": "sent", "pids": [777]}
    assert not world.paths.stop_hold.exists()         # a run was stopped: nothing to hold


# Finding 9: a launcher_pid is proven, never just alive.

def test_a_reused_launcher_pid_is_no_lock_and_is_never_signalled(world):
    """The finder's probe: a launcher SIGKILLed in its checks left 'starting'
    with launcher_pid 4243, and Android gave 4243 to surfaceflinger. status
    said 'starting' forever, go was run_active, and STOP sent SIGINT to
    surfaceflinger as root."""
    stale = nav.new_status(state="starting", origin="tap", run_id=RUN_ID, launcher_pid=4243)
    world.paths.status.write_text(json.dumps(stale))
    world.alive_pids.add(4243)
    world.cmdlines[4243] = "/system/bin/surfaceflinger "
    assert (nav.status(world.env)["state"], nav.status(world.env)["reason"]) == ("failed", "launcher_lost")
    assert nav.stop(world.env, wait_s=10) == {"stop": "none", "pids": []} and world.signalled == []
    assert nav.places_caps(world.env)["run"] is None
    assert nav.reap(world.env) == {"reap": "closed_stale_record", "launcher_pid": 4243}
    assert json.loads(world.paths.status.read_text())["reason"] == "launcher_lost"
    assert world.signalled == [] and world.board.cancelled == 0
    world.paths.status.write_text(json.dumps(stale))
    world.child_kw = {"exit_at": 0.6}
    world.go()
    assert world.spawned


def test_an_app_status_poll_with_the_launchers_old_pid_is_not_the_launcher(world):
    # the app runs `emma_nav.py status` every second: the script name alone proves nothing
    world.paths.status.write_text(json.dumps(nav.new_status(state="starting", launcher_pid=4243)))
    world.alive_pids.add(4243)
    world.cmdlines[4243] = "/data/local/tmp/emma-nav/python/bin/python3.13 -u scripts/emma_nav.py status "
    assert nav.stop(world.env) == {"stop": "none", "pids": []} and world.signalled == []
    assert nav.status(world.env)["reason"] == "launcher_lost"


def test_a_launcher_pid_with_another_start_time_is_another_launcher(world):
    world.paths.status.write_text(json.dumps(nav.new_status(state="starting", launcher_pid=555,
                                                            launcher_start=111)))
    world.launcher(555, start=222)                    # the same pid, started later: not the one recorded
    assert nav.status(world.env)["reason"] == "launcher_lost"
    assert nav.stop(world.env) == {"stop": "none", "pids": []} and world.signalled == []
    world.starts[555] = 111
    assert nav.stop(world.env) == {"stop": "sent", "pids": [555]} and world.signalled == [555]


def test_go_records_its_own_start_time(world):
    world.starts[1000] = 4321
    world.alive_pids.add(1000)
    _arrive_at(world)
    world.go()
    assert all(line["launcher_start"] == 4321 for line in world.lines)
    assert json.loads(world.paths.status.read_text())["launcher_start"] == 4321


def test_a_launcher_whose_proc_cannot_be_read_holds_the_lock_but_is_not_signalled(world):
    # from the shell uid a root launcher is hidden: a lock (never idle while it
    # may be walking), never a signal (finding 2's EPERM, without sending anything)
    world.paths.status.write_text(json.dumps(nav.new_status(state="starting", launcher_pid=555)))
    world.alive_pids.add(555)
    world.cmdlines[555] = None
    assert world.go() == 3 and world.final["reason"] == "run_active"
    assert nav.stop(world.env) == {"stop": "denied", "pids": [555]} and world.signalled == []


def test_a_run_pid_whose_proc_cannot_be_read_is_denied_without_a_signal(world):
    world.paths.run_pid.write_text("777")
    world.alive_pids.add(777)
    world.cmdlines[777] = None
    assert nav.stop(world.env) == {"stop": "denied", "pids": [777]} and world.signalled == []


def test_the_start_time_is_field_22_counted_after_the_last_paren():
    fields = ["S"] + [str(n) for n in range(4, 22)] + ["987654"] + ["0"] * 30
    assert nav.stat_start_time("4243 (a b) c) " + " ".join(fields)) == 987654
    assert nav.stat_start_time("garbage") is None


# Finding 18: exit 2 is the board only when the board does not answer.

def _exit_2(world, run_out: str, board_down: bool):
    world.child_kw = {"exit_at": 0.5, "code": 2}

    def child(now):
        if now >= 0.3:
            world.paths.run_out.write_text(run_out, encoding="utf-8")
            world.board.fail = board_down
    world.clock.hooks.append(child)


def test_exit_2_from_argparse_with_a_board_that_answers_is_a_launcher_failure(world):
    _exit_2(world, "usage: drive_route.py route [-h] ...\n"
                   "drive_route.py: error: unrecognized arguments: --to-step 10\n", board_down=False)
    assert world.go() == 1
    assert (world.final["state"], world.final["reason"]) == ("failed", "launcher_failed")
    assert "unrecognized arguments" in world.final["detail"]


def test_exit_2_with_a_board_that_does_not_answer_is_board_unreachable(world):
    _exit_2(world, "board not reachable at http://192.168.11.1:1448 (timeout)\n", board_down=True)
    world.go()
    assert (world.final["state"], world.final["reason"]) == ("failed", "board_unreachable")
    assert "board not reachable" in world.final["detail"]


def test_final_state_reads_exit_2_by_the_board_not_by_the_number():
    assert nav.final_state(2, None, None, board_answers=True) == ("failed", "launcher_failed")
    assert nav.final_state(2, None, None, board_answers=False) == ("failed", "board_unreachable")
    assert "launcher_failed" in rc.NAV_REASONS


# Finding 20: a run whose run.pid could not be written.

def test_a_walk_without_run_pid_is_still_stopped_locked_and_reported(world):
    world.paths.status.write_text(json.dumps(nav.new_status(state="running", origin="tap", run_id=RUN_ID,
                                                            launcher_pid=900, child_pid=4242)))
    world.launcher(900)
    world.alive_pids.add(4242)                        # drive_route (the fake's default cmdline)
    assert nav.stop(world.env) == {"stop": "sent", "pids": [4242]}
    assert world.signalled == [4242]                  # the child, not the launcher: never twice into it
    assert world.go() == 3 and world.final["reason"] == "run_active"
    world.alive_pids.discard(900)                     # its launcher died: the child still walks
    assert nav.status(world.env)["state"] == "running"
    assert world.go() == 3 and world.final["reason"] == "run_active"


def test_stop_reaches_the_child_when_go_could_not_write_run_pid(world):
    world.paths.run_pid.mkdir()                       # any write to run.pid now fails
    flags = nav.Flags()
    seen = {}

    def press(now):
        if "stop" not in seen and now >= 1.0:
            seen["stop"] = nav.stop(world.env, wait_s=0)
        if now >= 1.5:
            flags.stop = True
    world.child_kw = {"exit_at": 60.0}
    world.clock.hooks.append(press)
    world.go(flags=flags)
    assert seen["stop"] == {"stop": "sent", "pids": [world.child.pid]}


def test_reap_finds_an_orphan_that_only_status_json_names(world, tmp_path):
    world.paths.status.write_text(json.dumps(nav.new_status(state="running", origin="voice", run_id=RUN_ID,
                                                            launcher_pid=900, child_pid=4242)))
    world.alive_pids.add(4242)                        # launcher 900 is dead
    world.here()
    assert nav.reap(world.env) == {"reap": "interrupted_orphan", "pid": 4242}
    assert world.signalled == [4242] and not world.paths.here.exists()
    # and a dead one: its board action is cancelled and the record closed
    w = World(tmp_path / "w2", board=FakeBoard(current={"action_id": 9}))
    w.paths.status.write_text(json.dumps(nav.new_status(state="running", origin="tap", run_id=RUN_ID,
                                                        launcher_pid=900, child_pid=4242)))
    assert nav.reap(w.env)["reap"] == "cancelled_board_action" and w.board.cancelled == 1
    assert json.loads(w.paths.status.read_text())["state"] == "failed"
    assert nav.reap(w.env) == {"reap": "nothing"}
