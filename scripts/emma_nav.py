"""The robot's own navigation launcher: one leg of the one verified route,
started by the face app (or by hand over adb), never by the server.

    emma_nav.py check     read-only: runtime, deployed files, places, board, where the robot is
    emma_nav.py places    the nav_caps body (+ "run"): which places can be gone to from here
    emma_nav.py status    one JSON line: the run now (also a PC `robot_python run`)
    emma_nav.py go <place_id> --origin tap|voice|auto --run-id <32 hex> [--tether] [--arms-rested]
    emma_nav.py stop [--wait S] [--hold]  SIGINT the run in run.pid (a robot_python run too);
                          with --wait, kill -9 + cancel the board's action if it has not
                          ended; with --hold and nothing on disk, refuse a `go` that comes
                          within STOP_HOLD_S (a walk the app is still preparing)
    emma_nav.py reap      on every app start: stop an orphan of a dead app, cancel a board
                          action nobody is driving any more

docs/emma-body-nav-design-2026-10-08.md §2e. It runs on the robot under
/data/local/tmp/emma-nav/py (python-build-standalone, scripts/robot_python.py)
and imports only the stdlib plus app/robot_contracts.py and app/robot_places.py,
both stdlib-only. **Never numpy, never drive_route**: the app waits on this to
answer, and the first leg starts from here.

Why a launcher at all, and not the app running drive_route itself: the
where-am-I decision, the leg lookup, the tether and the exit-code mapping are
logic, and logic in Python is tested offline with pytest (design D6); the Java
side keeps the process plumbing only.

What it refuses, each for a reason that happened or was found in review:

* a second run while run.pid lives -- `robot_python run` shares the file, so
  there is one lock for both entry points;
* a places file whose route_sha256 is not the deployed route's (a leg's step
  numbers mean nothing against another step list);
* a start from a place it cannot vouch for: the board's on_dock, or here.json
  written at the end of the last leg *and* still the newest run log *and* the
  pose still within tolerance -- a robot pushed by hand or driven with the
  joystick has moved odometry, and a later PC run moved the robot;
* a voice start on a leg nobody has watched run on its own (`verified:false`);
* a leg through a door when the app did not say it rested the arms
  (`--arms-rested`, review 2 finding 7): drive_route counts the 0.70 m base
  in doorways only with `--arms-in`, which is true only with the arms
  hanging at rest -- and the app's operator switch nav_require_arm_rest can
  skip the rest. Without the flag the arms' 0.78 m is counted, and D8/D11
  (3.8 cm a side for the base) cannot be passed at that width: refused here,
  not stopped in front of the door half way through the leg;
* a `go` within STOP_HOLD_S of a `robot_python stop` that found nothing on
  disk (review 2 finding 8): the app's walk is in its memory only while it
  rests the arms, and that stop used to say "no run going" and let it start.

Every status line is printed with flush=True and the app starts this with
`python -u`: stdout is a pipe, block-buffered otherwise, and the app would see
"running" seconds after the robot started moving (build review #13).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from app import robot_contracts as rc  # noqa: E402
from app import robot_places as rp  # noqa: E402

BOARD_IP = "192.168.11.1"
BOARD_URL = "http://%s:1448" % BOARD_IP
SDK_ADDR = "%s:1445" % BOARD_IP
#: A board read that takes longer than this is a board we do not start a leg
#: against. The app is waiting on the answer (design §2e step 3).
BOARD_TIMEOUT_S = 2.0
#: No "ping" from the app for this long, or its pipe closed: the face app is
#: gone and nobody holds the STOP bar -- the run is interrupted (`app_lost`).
#: The app pings every 1 s; four missed is not a hiccup.
TETHER_S = 4.0
STATUS_EVERY_S = 1.0
TICK_S = 0.2
#: After SIGINT, drive_route cancels the board's action, logs and summarises.
#: Past this, it is killed; the board's velocity watchdog stops the wheels in
#: ~1 s (design §4). The app's own STOP escalates to kill -9 after 2 s anyway.
INTERRUPT_GRACE_S = 20.0
PLACES_FILE = "data/robot-places/floor2.json"
#: The contract's own tuple (tap, voice, auto): "auto" is the app's walk back
#: to the dock after idle (AutoReturn.java, owner 10 Oct). plan_leg refuses an
#: unverified leg to "voice" only, so "auto" goes like "tap". An app run of any
#: of these is the app's to reap; "pc" is not one of them.
ORIGINS = rc.NAV_ORIGINS
DETAIL_MAX = 160

#: `stop --wait`: how long `robot_python stop` gives drive_route's cleanup
#: (cancel, log, summary) before it escalates to kill -9 + the board's DELETE
#: (review finding 1). scripts/robot_python.py greps for this name to know the
#: deployed launcher takes --wait: an older one would fail argparse and stop
#: nothing.
STOP_WAIT_S = 10.0
#: `stop --hold` with nothing on disk (review 2 finding 8): between the app
#: accepting a walk and spawning this launcher (arm rest up to 15 s, then the
#: base released -- NavLauncher REST_TIMEOUT_MS) the run exists only in the
#: app's memory, and `robot_python stop` printed "no run going" while the
#: walk was about to start. A `go` that comes within this many seconds of
#: such a stop ends `stopped`/`stop_pressed` before anything is spawned --
#: the same idea as the app's own 60 s latch after its STOP. Only
#: `robot_python stop` passes --hold: the app's STOP runs `stop` on every
#: press, and it latches by itself. robot_python greps for this name.
STOP_HOLD_S = 30.0
#: What /proc/<pid>/cmdline must hold for a pid to be a launcher running a
#: leg (review 2 finding 9): "go", not just the script -- the app polls
#: `emma_nav.py status` every second, and one of those can get a stale
#: launcher_pid back from Android.
LAUNCHER_ARGV = "emma_nav.py go"
DRIVE_ROUTE = "drive_route.py"
#: What a file a root launcher creates may be read as: the PC side
#: (`robot_python status/tail/nav-status`, adb shell's uid) reads them too, and
#: su can hand down the app's umask 077 (review finding 9).
SHARED_UMASK = 0o022

# Exit codes of drive_route (scripts/drive_route.py EXIT_*).
DR_DONE, DR_STOP, DR_NO_BOARD, DR_REFUSED, DR_INTERRUPTED, DR_ERROR = 0, 1, 2, 3, 130, 4
#: Any of these recorded = the launcher itself asked drive_route to stop.
RECORDED_STOPS = ("stop_pressed", "app_lost", "launcher_lost")


# -- where things are ---------------------------------------------------------

@dataclass(frozen=True)
class Paths:
    """REMOTE = /data/local/tmp/emma-nav on the robot; a tmp dir in tests."""
    remote: Path

    @property
    def code(self) -> Path:
        return self.remote / "code"

    @property
    def nav(self) -> Path:
        return self.remote / "nav"

    @property
    def run_pid(self) -> Path:            # shared with `robot_python run`: one lock
        return self.remote / "run.pid"

    @property
    def run_out(self) -> Path:
        return self.remote / "run.out"

    @property
    def py(self) -> Path:
        return self.remote / "py"

    @property
    def status(self) -> Path:
        return self.nav / "status.json"

    @property
    def drive_status(self) -> Path:
        return self.nav / "drive_status.json"

    @property
    def here(self) -> Path:
        return self.nav / "here.json"

    @property
    def route_state(self) -> Path:
        return self.nav / "route_state.json"

    @property
    def stop_hold(self) -> Path:            # `stop --hold` (review 2 finding 8)
        return self.nav / "stop_hold.json"

    @property
    def manifest(self) -> Path:
        return self.code / "MANIFEST.json"

    @property
    def runs(self) -> Path:
        return self.code / "data" / "robot" / "runs"

    @property
    def places(self) -> Path:
        return self.code / PLACES_FILE


# -- output -------------------------------------------------------------------

def emit(obj: dict) -> None:
    """One JSON line on stdout, flushed. ASCII-escaped: the app reads a pipe,
    and a Thai name must never be what makes a status line unreadable."""
    print(json.dumps(obj), flush=True)


def new_status(**kw) -> dict:
    """The status object of design §2e. `child_pid` is added here: `status`
    and `reap` need to know whether run.pid is *this* launcher's child or a PC
    `robot_python run` (both share run.pid). `launcher_start` is the
    launcher's /proc start time (review 2 finding 9): a record outlives its
    launcher -- across a reboot too -- and its pid alone names whatever
    Android gave that number to next."""
    st = {"format": 1, "run_id": None, "run_log": None, "origin": None, "launcher_pid": None,
          "launcher_start": None,
          "child_pid": None, "state": "idle", "place": None, "from": None, "step": None,
          "step_from": None, "step_to": None, "do": None, "door": None, "label": None,
          "elapsed_s": None, "battery": None, "reason": None, "detail": None, "pose": None,
          "here": None, "exit_code": None, "t": None}
    st.update(kw)
    return st


def _write_json(path: Path, obj: dict) -> None:
    """Whole or not at all: the app and `status` poll these files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and int(text) > 0 else None


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def newest_log(runs: Path) -> str | None:
    """The newest drive_route log by name -- names are local times
    (YYYYmmdd-HHMMSS_route.jsonl), so the name order is the time order, and
    a copy or a sync that touches mtime cannot reorder them."""
    try:
        names = [p.name for p in runs.glob("*.jsonl")]
    except OSError:
        return None
    return max(names) if names else None


# -- processes ----------------------------------------------------------------

def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) on Windows is TerminateProcess(pid, 0), not a probe.
        # This runs on the robot; a test passes its own `alive`.
        raise RuntimeError("pid_alive is POSIX only")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_cmdline(pid: int) -> str | None:
    """/proc/<pid>/cmdline with its NULs as spaces; None when it cannot be
    read (gone, or another uid's process behind Android's hidepid)."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode("utf-8", "replace")


def read_start_time(pid: int) -> int | None:
    """Field 22 of /proc/<pid>/stat: when the process started, in clock
    ticks after boot. A pid is reused; (pid, start time) is not, within one
    boot -- and a launcher never starts in the first ticks of a boot, where
    the daemons that get low pids back after a reboot do. None when it
    cannot be read. The command name (field 2) can hold spaces and ')', so
    the fields are counted from the last ')'."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            return stat_start_time(fh.read().decode("ascii", "replace"))
    except OSError:
        return None


def stat_start_time(raw: str) -> int | None:
    """Field 22 of one /proc/<pid>/stat line (fields 3.. follow the last ')')."""
    try:
        return int(raw[raw.rindex(")") + 1:].split()[22 - 3])
    except (ValueError, IndexError):
        return None


def _is_pid(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _ours(env: "Env", pid, marker: str, start=None) -> str:
    """Is `pid` the process a record says it is? "yes" / "no" / "unknown".

    "no": gone, a zombie (empty cmdline), a cmdline without `marker`, or a
    start time other than the one recorded. "unknown": /proc/<pid> cannot be
    read -- from the shell uid a root process is hidden (hidepid). A lock and
    a status treat "unknown" as alive (they must never read idle while a root
    run drives, finding 12); a signal is sent only on "yes" (review 2 finding
    9: STOP sent SIGINT, as root, to whatever process got a stale pid)."""
    if not _is_pid(pid) or not env.alive(pid):
        return "no"
    cmdline = env.cmdline(pid)
    if cmdline is None:
        return "unknown"
    if marker not in cmdline:
        return "no"
    if isinstance(start, int) and not isinstance(start, bool):
        now = env.start_time(pid)
        if now is not None and now != start:
            return "no"
    return "yes"


def run_pid_alive(env: "Env", pid: int | None) -> bool:
    """Whether run.pid names a live drive_route (review finding 12). A dead
    run's pid outlives it -- a PC run never removed it -- and once Android
    reuses the number, a bare `kill -0` made STOP signal an unrelated process,
    `status` call it a PC run that locked every card, and `go` refuse. The
    app's own probe (NavLauncher RUN_PID_IS_DRIVE_ROUTE) already checks the
    cmdline. A cmdline that cannot be read counts as the run: from the shell
    uid a root run's /proc is hidden, and a lock or a status must never read
    idle while that run drives. A zombie's cmdline is empty: not running."""
    return _ours(env, pid, DRIVE_ROUTE) != "no"


def launcher_alive(env: "Env", pid, start=None) -> bool:
    """Whether a status record's launcher_pid is still that launcher (review
    2 finding 9) -- the same check run.pid got in finding 12, which status.json
    never did. A launcher SIGKILLed in its checks (the app's kill -9, or its
    whole cgroup) left "starting" behind, and nothing rewrote it: once the
    pid was reused, status said "starting" forever (the app pinned its STOP
    bar and refused every card), every `go` was run_active, and every STOP
    sent SIGINT to that process as root. A reboot makes this likely: pids
    start low again, and status.json survives it."""
    return _ours(env, pid, LAUNCHER_ARGV, start) != "no"


def _signal(pid: int, sig: int) -> str:
    try:
        os.kill(pid, sig)
        return "sent"
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "denied"


def send_sigint(pid: int) -> str:
    """drive_route's KeyboardInterrupt path: cancel the board's action, log,
    summarise. The same signal `robot_python stop` has always sent.

    "sent" / "gone" / "denied" (review finding 2): EPERM -- a root run, stopped
    from the adb shell's uid -- used to be False like "gone", and `stop`
    answered "none" while the robot walked."""
    return _signal(pid, signal.SIGINT)


def send_sigkill(pid: int) -> str:
    """`stop --wait`'s escalation, as the app's own STOP does after 2 s."""
    return _signal(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


class ProcChild:
    """The `Child` interface (`poll`, `interrupt`, `kill`, `pid`) over a real
    process. Tests use a fake: there is no SIGINT for a child on Windows."""

    def __init__(self, proc: subprocess.Popen):
        self.proc, self.pid = proc, proc.pid

    def poll(self) -> int | None:
        return self.proc.poll()

    def interrupt(self) -> None:
        send_sigint(self.pid)

    def kill(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def spawn_child(cmd: list[str], cwd: Path, out: Path) -> ProcChild:
    """drive_route, in its own session: a hang-up of the app's su session
    must reach drive_route as *this* launcher dying (--watch-parent, then the
    KeyboardInterrupt path that cancels the board's action), never as a bare
    SIGHUP that kills it with the board's action still running."""
    with open(out, "wb") as fh:
        try:
            # an existing run.out keeps its old mode through "wb": one made
            # 0600 under su's umask 077 stays unreadable to `robot_python
            # tail` forever otherwise (review finding 9)
            os.chmod(out, 0o644)
        except OSError:
            pass
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
    return ProcChild(proc)


# -- the board (read-only here, except reap's cancel) -------------------------

class BoardError(Exception):
    pass


class Board:
    """The few REST reads the launcher needs, stdlib only (drive_route's
    Board needs httpx, which this must not import)."""

    def __init__(self, base: str = BOARD_URL, timeout: float = BOARD_TIMEOUT_S):
        self.base, self.timeout = base.rstrip("/"), timeout

    def _req(self, method: str, path: str):
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise BoardError("%s %s: HTTP %d" % (method, path, e.code)) from e
        except (OSError, ValueError) as e:
            raise BoardError("%s %s: %s" % (method, path, e.__class__.__name__)) from e
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError:
            return body.decode("utf-8", "replace")

    def power(self) -> dict:
        doc = self._req("GET", "/api/core/system/v1/power/status")
        if not isinstance(doc, dict):
            raise BoardError("power: no status")
        return doc

    def pose(self) -> tuple[float, float, float]:
        p = self._req("GET", "/api/core/slam/v1/localization/pose")
        try:
            return float(p["x"]), float(p["y"]), float(p["yaw"])
        except (TypeError, KeyError, ValueError) as e:
            raise BoardError("pose: %r" % (p,)) from e

    def current(self):
        return self._req("GET", "/api/core/motion/v1/actions/:current")

    def cancel(self) -> None:
        self._req("DELETE", "/api/core/motion/v1/actions/:current")

    def localization_on(self) -> bool | None:
        try:
            v = self._req("GET", "/api/core/slam/v1/localization/:enable")
        except BoardError:
            return None
        return v if isinstance(v, bool) else None


# -- pure decisions (tested one by one) ---------------------------------------

def _wrap_pi(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _pose3(v) -> tuple[float, float, float] | None:
    if (isinstance(v, (list, tuple)) and len(v) == 3
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in v)):
        return float(v[0]), float(v[1]), float(v[2])
    return None


def where_am_i(places: rp.Places, power: dict | None, pose, here_rec: dict | None,
               newest: str | None) -> tuple[str | None, bool]:
    """(place id or None, whether it came from here.json).

    The board's on_dock wins over any record. Otherwise here.json counts
    only when all hold: it names a known place that is not the dock (off the
    dock, a "dock" record is a robot pulled off it); its run log is the newest
    one (a later PC run moved the robot); the board pose is still within the
    place's tolerance of the recorded one (a push or the joystick moved
    odometry). Anything else is unknown -- and unknown refuses every card,
    which costs a staff walk to the dock, never a leg from the wrong place."""
    dock = next((p for p in places.places if p.kind == "dock"), None)
    if (power or {}).get("dockingStatus") == "on_dock":
        return (dock.id if dock is not None else None), False
    if not isinstance(here_rec, dict) or not isinstance(here_rec.get("place"), str):
        return None, False
    place = places.place(here_rec["place"])
    if place is None or place.kind == "dock":
        return None, False
    if not newest or here_rec.get("run_log") != newest:
        return None, False
    rec, now = _pose3(here_rec.get("pose")), _pose3(pose)
    if rec is None or now is None:
        return None, False
    if math.hypot(now[0] - rec[0], now[1] - rec[1]) > place.arrive_tolerance_m:
        return None, False
    if abs(_wrap_pi(now[2] - rec[2])) > math.radians(place.arrive_tolerance_deg):
        return None, False
    return place.id, True


def plan_leg(places: rp.Places, here: str | None, place_id: str, origin: str,
             battery) -> tuple[rp.Leg | None, str | None]:
    """The leg here -> place, or the NAV_REASONS code that refuses it.
    Order = design §2e steps 2, 4, 5, 6."""
    if places.place(place_id) is None:
        return None, "unknown_place"
    if here is None:
        return None, "location_unknown"
    if here == place_id:
        return None, "already_there"
    leg = rp.leg(places, here, place_id)
    if leg is None:
        return None, "no_leg"
    if origin == "voice" and not leg.verified:
        # the server checks this too; a leg nobody has watched run on its own
        # is started by a person tapping, who is standing there to watch it
        return None, "leg_unverified"
    if not isinstance(battery, (int, float)) or isinstance(battery, bool) or battery < leg.min_battery:
        return None, "low_battery"
    return leg, None


def final_state(code: int | None, drive: dict | None, recorded: str | None,
                done_in_log: bool = False, board_answers: bool | None = None) -> tuple[str, str | None]:
    """drive_route's exit -> (state, reason). §2e step 9. No reason is ever
    read out of an English message: drive_route sets `reason_code` at the
    raise site, and a code that is not a NAV_REASONS key reads as `stopped`.

    `board_answers`: whether the board answered the launcher after an exit 2
    (review 2 finding 18). drive_route's EXIT_NO_BOARD is 2, but so is Python
    rejecting an argument (a drive_route older than --to-step/--watch-parent,
    a partial deploy) and Python failing to open the script: all three read
    "ติดต่อฐานล้อไม่ได้" and nobody looked at the deploy. A board that answers
    now was not the reason; None (not asked) keeps the old reading."""
    drive = drive or {}
    if code == DR_DONE:
        if drive.get("state") == "done" or done_in_log:
            return "arrived", None
        return "failed", "error"
    if code == DR_REFUSED:
        return "refused", "refused"
    if code == DR_STOP:
        rcode = drive.get("reason_code")
        return "stopped", rcode if rcode in rc.NAV_REASONS else "stopped"
    if code == DR_ERROR:
        return "failed", "error"                    # drive_route's own uncaught exception (finding 27)
    if recorded in RECORDED_STOPS and code is not None and code < 0:
        # Killed by a signal after a stop this launcher recorded: the app's
        # kill -9 2 s after every full STOP, or our own after INTERRUPT_GRACE_S,
        # can land while drive_route is still in its cleanup. The guest pressed
        # STOP; "ระบบนำทางหยุดเพราะข้อผิดพลาด" was a lie (review findings 11, 25).
        return "stopped", recorded
    if code in (DR_INTERRUPTED, -signal.SIGINT):
        # -SIGINT: Python died of the signal before its own handler was up
        if recorded in RECORDED_STOPS:
            return "stopped", recorded
        if drive.get("reason_code") == "launcher_lost":
            return "stopped", "launcher_lost"
        return "stopped", "interrupted"
    if code == DR_NO_BOARD:
        return "failed", "launcher_failed" if board_answers else "board_unreachable"
    return "failed", "error"


_MERGED = ("step", "step_from", "step_to", "do", "label", "door", "pose", "run_log")


def merge_drive(st: dict, drive: dict | None) -> dict:
    """drive_route's --status-file into the launcher's status. The state goes
    to `running` only at drive_route's first step: before that it is still
    checking (its preflight), and nothing has moved."""
    out = dict(st)
    if not drive:
        return out
    for k in _MERGED:
        if drive.get(k) is not None:
            out[k] = drive[k]
    if out.get("state") in ("starting", "running") and drive.get("step") is not None \
            and drive.get("state") == "running":
        out["state"] = "running"
    return out


def build_command(paths: Paths, route: str, leg: rp.Leg, state_in: bool, launcher_pid: int,
                  arms_in: bool = False) -> list[str]:
    """drive_route for one leg, relative to code/ (its cwd). An argv list, no
    shell: nothing here is a string a place id or a run id could escape.

    --watch-parent carries this launcher's pid (review finding 10): a launcher
    killed while drive_route is still importing numpy left it re-parented to
    init before it read its parent, and the watch then compared against init
    and never fired. With the pid, drive_route refuses to start instead.

    --arms-in only with `arms_in` (review 2 finding 7): it was always passed,
    on the assumption that the app rests the arms first -- which the app's
    nav_require_arm_rest switch can skip, and an arm left out (a STOP mid-
    handshake) then went through D8/D11 with a margin computed for the base.
    The default is the wider, safe footprint."""
    cmd = [str(paths.py), "-u", "scripts/drive_route.py", route,
           "--relay", BOARD_URL, "--sdk", SDK_ADDR, "--adb-serial", "",
           "--from-step", str(leg.from_step), "--to-step", str(leg.to_step)]
    if arms_in:
        cmd.append("--arms-in")                     # the base's width: the app said the arms rest
    # --door-memory always (9 ต.ค.): each door's middle in the route's frame,
    # kept across runs; never deleted with route_state.json, which is odometry's
    # frame of one leg (drive_route DOOR_MEM_*)
    cmd += ["--min-battery", str(leg.min_battery),
            "--status-file", "../nav/drive_status.json", "--watch-parent", str(int(launcher_pid)),
            "--state-out", "../nav/route_state.json", "--door-memory", "../nav/door_memory.json"]
    if state_in:
        cmd += ["--state-in", "../nav/route_state.json"]
    return cmd


def leg_doors(steps: list, leg: rp.Leg) -> tuple[str, ...]:
    """The doors a leg goes through: its places entry's `doors`, and every
    `door` step of the route in the leg's range. The entry is written by
    hand and nothing checks it against the steps; the arms gate (finding 7)
    must not be opened by a `doors` list somebody forgot to fill in."""
    found = list(leg.doors)
    for i in range(leg.from_step, leg.to_step + 1):
        raw = steps[i - 1] if 0 < i <= len(steps) else None
        if isinstance(raw, dict) and raw.get("do") == "door":
            name = raw.get("door") if isinstance(raw.get("door"), str) and raw.get("door") else "step %d" % i
            if name not in found:
                found.append(name)
    return tuple(found)


def _safe_rel(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts and "\\" not in path


def load_places_and_steps(paths: Paths) -> tuple[rp.Places | None, list | None, str | None, str | None]:
    """(places, route steps, reason, detail). Invalid, or not the deployed
    route, is `places_stale`: everything is refused rather than a leg built
    on a guess. The steps are the ones the sha256 and validate() vouched for."""
    try:
        places = rp.load(paths.places)
    except (OSError, ValueError) as e:
        return None, None, "places_stale", str(e)
    if not _safe_rel(places.route):
        return None, None, "places_stale", "route path %r" % places.route
    route = paths.code / places.route
    try:
        sha = rp.sha256_file(route)
        steps = json.loads(route.read_text(encoding="utf-8")).get("steps")
        if sha != places.route_sha256:
            return None, None, "places_stale", "route %s sha256 %s, places file says %s" % (
                places.route, sha[:12], places.route_sha256[:12])
        rp.validate(places, steps)
    except (OSError, ValueError, AttributeError) as e:
        return None, None, "places_stale", str(e)
    return places, steps, None, None


def load_places(paths: Paths) -> tuple[rp.Places | None, str | None, str | None]:
    """(places, reason, detail) -- load_places_and_steps without the steps."""
    places, _, why, detail = load_places_and_steps(paths)
    return places, why, detail


# -- the tether -----------------------------------------------------------------

class Tether:
    """The app's su pipe. "ping" every second keeps a run going; "stop" ends
    it (`stop_pressed`); silence for TETHER_S or EOF ends it (`app_lost`).
    Lines are fed by a reader thread that starts before any check, so a STOP
    pressed during the preflight is heard and nothing is spawned (safety
    review #2)."""

    def __init__(self, clock: Callable[[], float], timeout: float = TETHER_S):
        self.clock, self.timeout = clock, timeout
        self.last = clock()
        self.stop = False
        self.eof = False

    def feed(self, line: str) -> None:
        word = (line or "").strip().lower()
        if word == "ping":
            self.last = self.clock()
        elif word == "stop":
            self.stop = True

    def close(self) -> None:
        self.eof = True

    def lost(self) -> bool:
        return self.eof or self.clock() - self.last > self.timeout

    def read(self, stream) -> threading.Thread:
        def pump() -> None:
            try:
                for line in stream:
                    self.feed(line)
            except (OSError, ValueError):
                pass
            self.close()
        t = threading.Thread(target=pump, name="tether", daemon=True)
        t.start()
        return t


@dataclass
class Flags:
    """Signals to the launcher itself. SIGINT is `emma_nav.py stop` reaching
    a launcher still in its preflight (no run.pid yet); SIGTERM/SIGHUP are
    the app's su session going away."""
    stop: bool = False
    gone: bool = False


@dataclass
class Env:
    paths: Paths
    board: object
    alive: Callable[[int], bool] = pid_alive
    spawn: Callable[[list, Path, Path], object] = spawn_child
    signal_pid: Callable[[int], str] = send_sigint
    kill_pid: Callable[[int], str] = send_sigkill
    cmdline: Callable[[int], str | None] = read_cmdline
    start_time: Callable[[int], int | None] = read_start_time
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    out: Callable[[dict], None] = emit
    my_pid: int = field(default_factory=os.getpid)


def _why_stop(tether: Tether | None, flags: Flags | None) -> str | None:
    if (tether is not None and tether.stop) or (flags is not None and flags.stop):
        return "stop_pressed"
    if (tether is not None and tether.lost()) or (flags is not None and flags.gone):
        return "app_lost"
    return None


def _run_log_has(paths: Paths, name: str | None, kinds: tuple[str, ...]) -> bool | None:
    """Whether a run log has an event of one of `kinds`. None = it exists and
    cannot be read: "cannot tell", which the caller decides about. A log that
    was never written has nothing in it."""
    if not name:
        return False
    try:
        with open(paths.runs / name, encoding="utf-8") as fh:
            for line in fh:
                if any('"%s"' % k in line for k in kinds):
                    try:
                        if json.loads(line).get("kind") in kinds:
                            return True
                    except ValueError:
                        continue
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return False


def _run_log_done(paths: Paths, name: str | None) -> bool:
    return bool(_run_log_has(paths, name, ("leg_done", "route_done")))


def _retag(path: Path, old: str | None, new: str | None) -> None:
    """Point a record at the new run log -- only if it pointed at `old`."""
    doc = _read_json(path)
    if doc is not None and old and new and doc.get("run_log") == old:
        doc["run_log"] = new
        _write_json(path, doc)


def _write_stop_hold(env: "Env") -> bool:
    """`stop --hold` found nothing on disk: leave a note the next `go` reads
    (review 2 finding 8). Unrounded wall time: a `go` 0.05 s later must not
    read as before it."""
    try:
        _write_json(env.paths.stop_hold, {"t": env.wall(), "hold_s": STOP_HOLD_S})
    except OSError:
        return False                                # from the shell uid nav/ is root's: no hold, and stop says so
    return True


def _take_stop_hold(env: "Env") -> bool:
    """Whether a `stop --hold` note is still in force; the note goes either
    way -- one PC stop cancels the one walk that was being prepared, and an
    old note never refuses a walk an hour later. A wall clock that went back
    (age < 0) or a hold longer than ours is not trusted."""
    path = env.paths.stop_hold
    if not path.exists():
        return False
    doc = _read_json(path)
    try:
        path.unlink()
    except OSError:
        pass
    if doc is None:
        return False
    t, hold = doc.get("t"), doc.get("hold_s")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (t, hold)):
        return False
    return 0.0 <= env.wall() - t <= min(hold, STOP_HOLD_S)


def _board_answers(env: "Env") -> bool:
    """One power read, after a drive_route exit 2 (finding 18)."""
    try:
        env.board.power()
    except Exception:                               # noqa: BLE001 -- any failure is "does not answer"
        return False
    return True


def _last_line(path: Path, tail: int = 2048) -> str | None:
    """The last non-empty line of run.out -- the detail an operator reads
    (argparse's "unrecognized arguments", Python's "can't open file", or
    drive_route's own "board not reachable"). Shown, never parsed."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - tail))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else None


# -- go -------------------------------------------------------------------------

def go(env: Env, place_id: str, origin: str, run_id: str, tether: Tether | None = None,
       flags: Flags | None = None, arms_rested: bool = False) -> int:
    """One leg, §2e steps 1-9. Returns 0 arrived, 3 refused, 1 otherwise.

    `arms_rested`: the app passed --arms-rested, which it does only after
    its restArms() returned true (review 2 finding 7). An app that does not
    send it -- an older one, or the operator switch that skips the rest --
    gets the arms' width, and no leg through a door."""
    p = env.paths
    t0 = env.clock()
    st = new_status(run_id=run_id if rc.valid_run_id(run_id) else None,
                    origin=origin if origin in ORIGINS else None,
                    place=place_id if rc.valid_id(place_id) else None,
                    launcher_pid=env.my_pid, launcher_start=env.start_time(env.my_pid), state="starting")

    claimed = False

    def put(**kw) -> None:
        st.update(kw)
        st["elapsed_s"] = round(env.clock() - t0, 1)
        st["t"] = round(env.wall(), 1)
        if claimed:
            # Only once this launcher owns the run: a refusal written over the
            # record of a run that is going would make `status` call that run
            # a PC run, and `reap` would no longer know whose child it is.
            try:
                _write_json(p.status, st)
            except OSError:
                pass            # the printed line is what the app reads
        env.out(dict(st))

    def end(state: str, reason: str | None, detail: str | None = None, code: int | None = None) -> int:
        put(state=state, reason=reason, detail=(detail or None) and str(detail)[:DETAIL_MAX], exit_code=code)
        return 0 if state == "arrived" else 3 if state == "refused" else 1

    if not (rc.valid_id(place_id) and origin in ORIGINS and rc.valid_run_id(run_id)):
        # ids cross a process boundary into an argv; the shape is the gate
        return end("refused", "bad_request")
    # 1. one run at a time, whoever started it
    pid = _read_pid(p.run_pid)
    if pid is not None and run_pid_alive(env, pid):
        return end("refused", "run_active", "run.pid %d is alive" % pid)
    prev = _read_json(p.status)
    if prev and prev.get("state") not in rc.NAV_TERMINAL + ("idle",):
        lp = prev.get("launcher_pid")
        # a stale record's pid reused by another process is no lock (review 2 finding 9)
        if lp != env.my_pid and launcher_alive(env, lp, prev.get("launcher_start")):
            return end("refused", "run_active", "launcher %s is starting a run" % lp)
        cp = prev.get("child_pid")
        if _is_pid(cp) and run_pid_alive(env, cp):
            # review 2 finding 20: a run.pid write that failed leaves the
            # walking child named only here -- still one run at a time
            return end("refused", "run_active", "drive_route %d of the last run is alive" % cp)
    claimed = True
    put()                                           # claimed: `starting`
    if _take_stop_hold(env):
        # review 2 finding 8: `robot_python stop` came while the app was
        # still resting the arms for this walk -- nothing was on disk then
        return end("stopped", "stop_pressed", "stopped from the PC before the walk started")
    # 2. places, against the deployed route
    places, steps, why, detail = load_places_and_steps(p)
    if places is None:
        return end("refused", why, detail)
    if places.place(place_id) is None:
        return end("refused", "unknown_place")
    if _why_stop(tether, flags):
        return end("stopped", _why_stop(tether, flags))
    # 3. the board
    try:
        power = env.board.power()
        pose = env.board.pose()
    except Exception as e:                          # noqa: BLE001 -- any failure is "cannot read it"
        return end("refused", "board_unreachable", str(e))
    battery = power.get("batteryPercentage")
    st["battery"] = battery if isinstance(battery, (int, float)) and not isinstance(battery, bool) else None
    st["pose"] = [round(v, 4) for v in pose]
    # 4. where the robot is
    here_rec = _read_json(p.here)
    here, from_record = where_am_i(places, power, pose, here_rec, newest_log(p.runs))
    st["from"] = st["here"] = here
    # 5-6. the leg, and the battery for it
    leg, why = plan_leg(places, here, place_id, origin, st["battery"])
    if leg is None:
        return end("refused", why)
    doors = leg_doors(steps, leg)
    if doors and not arms_rested:
        # Review 2 finding 7. Without --arms-in drive_route counts the arms'
        # 0.78 m, and D8/D11 leave 3.8 cm a side for the 0.70 m base: the leg
        # would walk to the first door and stop there ("not going in"). The
        # app's own refusal when restArms fails is the same code.
        return end("refused", "arms_not_rest",
                   "leg through %s; the app did not say the arms rest (--arms-rested)" % ", ".join(doors))
    # 7. a stop that came in during the checks: nothing is spawned
    why = _why_stop(tether, flags)
    if why is not None:
        return end("stopped", why)
    # --state-in only with an accepted here.json from the same leg's log:
    # measured_at and door_seen are odometry coordinates, meaningless once
    # the frame changes (the dock resets it)
    rs = _read_json(p.route_state) if from_record else None
    state_in = bool(rs) and rs.get("run_log") == (here_rec or {}).get("run_log")
    _unlink(p.drive_status)                         # a stale "step" must not read as this run moving
    cmd = build_command(p, places.route, leg, state_in, env.my_pid, arms_in=arms_rested)
    # Any log newer than this one is this run's: "did it step?" is read from
    # there when the status file never showed it (finding 27).
    log_at_spawn = newest_log(p.runs)
    st.update(step_from=leg.from_step, step_to=leg.to_step)
    try:
        child = env.spawn(cmd, p.code, p.run_out)
    except OSError as e:
        return end("failed", "launcher_failed", str(e))
    try:
        tmp = p.run_pid.with_name(p.run_pid.name + ".tmp")
        tmp.write_text("%d\n" % child.pid, encoding="utf-8")
        os.replace(tmp, p.run_pid)
    except OSError:
        pass                    # status.json still names the child; `stop` reaches it through the tether
    put(child_pid=child.pid)
    # 8. while it runs
    recorded, interrupted_at, killed, moved = None, None, False, False
    last_key, last_print = None, env.clock()
    while True:
        code = child.poll()
        if code is not None:
            break
        if recorded is None:
            recorded = _why_stop(tether, flags)
            if recorded is not None:
                child.interrupt()
                interrupted_at = env.clock()
        elif not killed and env.clock() - interrupted_at > INTERRUPT_GRACE_S:
            child.kill()
            killed = True
        drive = _read_json(p.drive_status)
        merged = merge_drive(st, drive)
        if not moved and drive and drive.get("step") is not None:
            moved = True
            if drive.get("state") == "running":
                # The robot is about to move: the place it was at is no longer
                # known. Not at spawn -- a refused preflight kept a perfectly
                # known location then (build review #7).
                _unlink(p.here)
                _unlink(p.route_state)
            merged["here"] = None
        key = tuple(merged.get(k) for k in ("state", "step", "do", "label", "door"))
        if key != last_key or env.clock() - last_print >= STATUS_EVERY_S:
            st.clear()
            st.update(merged)
            put()
            last_key, last_print = key, env.clock()
        else:
            st.clear()
            st.update(merged)
        env.sleep(TICK_S)
    # 9. how it ended
    drive = _read_json(p.drive_status)
    st.update(merge_drive(st, drive))
    run_log = (drive or {}).get("run_log") or newest_log(p.runs)
    # exit 2 is the board only if the board does not answer us either (finding 18)
    board_answers = _board_answers(env) if code == DR_NO_BOARD else None
    state, reason = final_state(code, drive, recorded,
                                done_in_log=code == DR_DONE and _run_log_done(p, run_log),
                                board_answers=board_answers)
    detail = (drive or {}).get("reason") if state in ("stopped", "refused") else None
    if code == DR_NO_BOARD:
        detail = _last_line(p.run_out)              # what Python or drive_route said, for the operator
    cancelled = True
    if killed:
        # Review finding 11: a controller that ignored SIGINT for the grace
        # never ran its cancel, and whatever board action it started (GoHome,
        # MoveTo, MoveBy -- the velocity watchdog covers velocity only) went
        # on. Only the app's own STOP escalation deleted it, and that is
        # exactly what is missing when the app is gone. After the kill, never
        # before: a DELETE while drive_route lives reads to it as a link drop,
        # and it redoes the step (recover_link).
        try:
            if env.board.current() is not None:
                env.board.cancel()
        except Exception as e:                      # noqa: BLE001 -- any failure: not cancelled
            cancelled = False
            detail = "board action not cancelled: %s" % e
    if _read_pid(p.run_pid) == child.pid and cancelled:
        # a cancel that failed keeps run.pid: it names a dead pid, and the next
        # app start's reap retries the DELETE
        _unlink(p.run_pid)
    old_log = (here_rec or {}).get("run_log")
    new_log = run_log if run_log and run_log != log_at_spawn else None
    # Moved, or may have: a step seen in the status, a step in this run's own
    # log (a status write can fail, the log is the record; unreadable = cannot
    # tell = moved), or killed mid-whatever. A refusal is before any motion.
    stepped = (moved or (drive or {}).get("step") is not None or killed
               or (code != DR_REFUSED and new_log is not None
                   and _run_log_has(p, new_log, ("step",)) is not False))
    if state == "arrived":
        end_pose = _pose3((drive or {}).get("pose"))
        if end_pose is None:
            try:
                end_pose = env.board.pose()
            except Exception:                       # noqa: BLE001
                end_pose = None
        if end_pose is not None and run_log:
            _write_json(p.here, {"place": place_id, "pose": [round(v, 4) for v in end_pose],
                                 "at": round(env.wall(), 1), "run_log": run_log})
            st["here"] = place_id
        else:
            _unlink(p.here)
            st["here"] = None
    elif stepped:
        _unlink(p.here)                             # it may have moved: unknown, not guessed
        _unlink(p.route_state)
        st["here"] = None
    elif from_record:
        # Never moved, whatever the exit (review finding 27: a STOP during its
        # imports or preflight, exit 130/-2, a crash): drive_route made a new
        # jsonl even so, and "newest log" would otherwise make a robot standing
        # exactly where it was "location unknown" -- a staff walk to the dock.
        _retag(p.here, old_log, run_log)
        _retag(p.route_state, old_log, run_log)
        rec = _read_json(p.here) or {}
        # what the final line says is what the next where_am_i will accept
        st["here"] = rec.get("place") if rec.get("run_log") == newest_log(p.runs) else None
    return end(state, reason, detail, code)


# -- status / places / check / stop / reap -------------------------------------

def status(env: Env) -> dict:
    """The run now. A live run.pid that no launcher record owns is a PC run
    (`robot_python run`): the app's poller uses that to refuse gestures and
    show the STOP bar for it too (safety review #5)."""
    p = env.paths
    pid = _read_pid(p.run_pid)
    rec = _read_json(p.status)
    if pid is not None and run_pid_alive(env, pid):
        if rec and rec.get("child_pid") == pid and rec.get("state") not in rc.NAV_TERMINAL:
            return rec
        return new_status(state="running", origin="pc", reason="pc_run", child_pid=pid, t=round(env.wall(), 1))
    if rec and rec.get("state") not in rc.NAV_TERMINAL + ("idle",):
        if launcher_alive(env, rec.get("launcher_pid"), rec.get("launcher_start")):
            return rec                              # still in its checks: no child yet
        cp = rec.get("child_pid")
        if _is_pid(cp) and run_pid_alive(env, cp):
            return rec                              # its run.pid could not be written (review 2 finding 20)
        # dead, or its pid is another process now (review 2 finding 9)
        return dict(rec, state="failed", reason="launcher_lost")
    return rec or new_status(state="idle", t=round(env.wall(), 1))


def place_cards(places: rp.Places, here: str | None, battery, block: str | None) -> list[dict]:
    """Each place, whether it can be gone to now, and the NAV_REASONS code
    when not. `block` refuses all of them (run active, board unreachable)."""
    cards = []
    for place in places.places:
        leg = rp.leg(places, here, place.id) if here else None
        reason = block
        if reason is None:
            _, reason = plan_leg(places, here, place.id, "tap", battery)
        cards.append({"id": place.id, "name_th": place.name_th, "name_en": place.name_en,
                      # the app's auto-return finds the dock by its kind, the
                      # way where_am_i does -- never by a hard-coded id
                      "kind": place.kind,
                      "available": reason is None, "reason": reason,
                      "verified": bool(leg and leg.verified),
                      # for the card text: "ผ่านประตู D8 และ D11", "ต้องมีอย่างน้อย {min}%"
                      "doors": list(leg.doors) if leg else [], "min_battery": leg.min_battery if leg else None,
                      "est_s": leg.est_s if leg else None})
    return cards


def places_caps(env: Env) -> dict:
    """The nav_caps body (§2a) plus "run". `enabled` is not here: that switch
    lives in the app (nav_enabled), and the app adds it."""
    p = env.paths
    run = status(env)
    active = run.get("state") not in rc.NAV_TERMINAL + ("idle",)
    # `docking` / `charging`: the board's own dockingStatus and isCharging,
    # for the app's auto-return (owner 10 Oct), which never starts while the
    # robot is on the dock or while it cannot tell: None = could not read
    # (an older launcher sends neither field, which the app reads the same).
    caps = {"installed": True, "places_sha256": None, "here": None, "battery": None, "places": [],
            "docking": None, "charging": None, "run": run if active else None}
    try:
        caps["places_sha256"] = rp.sha256_file(p.places)
    except OSError:
        pass
    places, why, _ = load_places(p)
    if places is None:
        caps["reason"] = why
        return caps
    block = "run_active" if active else None
    here = None
    try:
        power, pose = env.board.power(), env.board.pose()
        battery = power.get("batteryPercentage")
        caps["battery"] = battery if isinstance(battery, (int, float)) and not isinstance(battery, bool) else None
        docking = power.get("dockingStatus")
        caps["docking"] = docking if isinstance(docking, str) and rc.valid_id(docking) else None
        charging = power.get("isCharging")
        caps["charging"] = charging if isinstance(charging, bool) else None
        here, _ = where_am_i(places, power, pose, _read_json(p.here), newest_log(p.runs))
    except Exception:                               # noqa: BLE001
        block = block or "board_unreachable"
    caps["here"] = here
    caps["places"] = place_cards(places, here, caps["battery"], block)
    return caps


def check(env: Env) -> dict:
    """Read-only: what an operator needs to trust a deploy before the first
    walk (bench A2). Never moves anything, never writes anything."""
    p = env.paths
    out: dict = {"runtime": {"python": sys.version.split()[0], "ok": True}}
    man = _read_json(p.manifest)
    if man is None or not isinstance(man.get("files"), dict):
        out["manifest"] = {"ok": False, "deployed_at": None, "reason": "no MANIFEST.json"}
    else:
        bad, missing = [], []
        for rel, want in sorted(man["files"].items()):
            f = p.code / rel
            if not _safe_rel(rel) or not f.exists():
                missing.append(rel)
            elif rp.sha256_file(f) != want:
                bad.append(rel)
        out["manifest"] = {"ok": not bad and not missing, "deployed_at": man.get("deployed_at"),
                           "files": len(man["files"]), "changed": bad, "missing": missing}
    places, why, detail = load_places(p)
    out["places"] = {"ok": places is not None, "reason": why, "detail": detail}
    try:
        out["places"]["sha256"] = rp.sha256_file(p.places)
    except OSError:
        out["places"]["sha256"] = None
    try:
        power, pose = env.board.power(), env.board.pose()
        out["board"] = {"reachable": True, "battery": power.get("batteryPercentage"),
                        "docking": power.get("dockingStatus"),
                        "localization": env.board.localization_on(), "pose": [round(v, 4) for v in pose]}
        out["here"] = where_am_i(places, power, pose, _read_json(p.here), newest_log(p.runs))[0] \
            if places is not None else None
    except Exception as e:                          # noqa: BLE001
        out["board"] = {"reachable": False, "error": str(e)[:DETAIL_MAX]}
        out["here"] = None
    out["run"] = status(env)
    out["ok"] = bool(out["manifest"]["ok"] and out["places"]["ok"] and out["board"]["reachable"])
    return out


def stop(env: Env, wait_s: float = 0.0, hold: bool = False) -> dict:
    """SIGINT whatever run.pid names (a robot_python run too), and a launcher
    still in its checks (no run.pid yet): its SIGINT handler ends it before
    anything is spawned. A stop is never refused -- but it never reports a
    stop that did not happen either:

    * "none": nothing is running. With `hold` (`robot_python stop`, review 2
      finding 8) a `go` within STOP_HOLD_S is refused, and "hold_s" says so;
    * "denied": the run is another uid's (a root run, stopped from the adb
      shell's uid without su) -- it used to read "none" while the robot walked
      (review finding 2). Also a pid whose /proc cannot be read: nothing is
      signalled that is not proven ours (review 2 finding 9), and from a uid
      that cannot read it the kernel would have said EPERM anyway;
    * "sent": SIGINT delivered. With `wait_s` (`robot_python stop`, review
      finding 1): "stopped" once drive_route has gone, else kill -9 and then
      the board's DELETE, as the app's own STOP does -- "escalated", or
      "not_stopped" when even the kill was refused.

    The app calls this with no wait: it escalates by itself after 2 s."""
    p = env.paths
    pid = _read_pid(p.run_pid)
    rec = _read_json(p.status) or {}
    active = bool(rec) and rec.get("state") not in rc.NAV_TERMINAL + ("idle",)
    lp, cp = rec.get("launcher_pid"), rec.get("child_pid")
    target, marker, start = None, DRIVE_ROUTE, None
    if pid is not None and run_pid_alive(env, pid):
        target = pid
    elif active and _is_pid(cp) and run_pid_alive(env, cp):
        # Review 2 finding 20: go() carries on when it cannot write run.pid
        # ("status.json still names the child"), and this said "none" -- and
        # `robot_python stop` "no run going" -- while that leg walked.
        target = cp
    elif rec.get("state") in ("starting", "preparing") and lp != env.my_pid \
            and launcher_alive(env, lp, rec.get("launcher_start")):
        # The launcher only while it has no child yet: once drive_route runs, the
        # launcher would pass its own SIGINT on, and a second SIGINT into
        # drive_route's cleanup is the one that skips the board's cancel.
        target, marker, start = lp, LAUNCHER_ARGV, rec.get("launcher_start")
    if target is None:
        out = {"stop": "none", "pids": []}
        if hold and _write_stop_hold(env):
            out["hold_s"] = STOP_HOLD_S
        return out
    proof = _ours(env, target, marker, start)
    if proof == "unknown":
        return {"stop": "denied", "pids": [target]}
    if proof == "no":
        return {"stop": "none", "pids": []}         # it ended between the probe and the proof
    how = env.signal_pid(target)
    if how == "denied":
        return {"stop": "denied", "pids": [target]}
    if how != "sent":
        return {"stop": "none", "pids": []}         # it ended between the probe and the signal
    if wait_s <= 0 or marker != DRIVE_ROUTE:
        return {"stop": "sent", "pids": [target]}
    deadline = env.clock() + wait_s
    while run_pid_alive(env, target) and env.clock() < deadline:
        env.sleep(TICK_S)
    if not run_pid_alive(env, target):
        return {"stop": "stopped", "pids": [target]}
    # the same proof before a kill -9 as before the SIGINT
    killed = env.kill_pid(target) if _ours(env, target, DRIVE_ROUTE) == "yes" else "denied"
    out = {"stop": "escalated" if killed in ("sent", "gone") else "not_stopped", "pids": [target], "kill": killed}
    if killed in ("sent", "gone"):
        # the controller first, the board's action after (a DELETE while it
        # lives reads as a link drop and it redoes the step)
        for _ in range(int(2.0 / TICK_S)):
            if not run_pid_alive(env, target):
                break
            env.sleep(TICK_S)
        _forget_place(p)                            # killed mid-whatever: where it is, is unknown
        try:
            if env.board.current() is not None:
                env.board.cancel()
            out["board"] = "cancelled"
            if _read_pid(p.run_pid) == target:
                _unlink(p.run_pid)
        except Exception as e:                      # noqa: BLE001
            out["board"] = "unreachable: %s" % str(e)[:DETAIL_MAX]   # run.pid stays: reap retries
    return out


def _forget_place(p: Paths) -> None:
    """A run that ended with nobody watching (its launcher died) may have
    moved the robot before its launcher saw the first step: where the robot
    is, is unknown -- staff take it to the dock, nothing guesses."""
    _unlink(p.here)
    _unlink(p.route_state)


def reap(env: Env) -> dict:
    """On every app start (§4). An app killed with its whole cgroup takes the
    launcher with it and no `board.cancel()` ever runs: the velocity watchdog
    stops the wheels, but a board action (GoHome, MoveBy) carries on.

    * run.pid alive, owned by a launcher record (tap/voice) whose launcher is
      dead -> SIGINT it: an orphan of a dead app;
    * run.pid alive otherwise -> left alone: a PC `robot_python run`, or a
      live launcher (B14: `stop` on every app start would kill a PC run);
    * run.pid names a dead process (or a reused pid that is no drive_route,
      review finding 12) -> DELETE the board's current action, and
      the stale run.pid goes, so the next app start does not cancel an action
      somebody else started since;
    * no run.pid, but an unfinished record whose launcher is gone (review 2
      findings 9, 20): its child_pid stands in for run.pid when it has one
      (a run.pid write that failed), else the record is closed as
      failed/launcher_lost -- a launcher SIGKILLed in its checks left
      "starting" behind for good, and a reused pid made it look alive."""
    p = env.paths
    pid = _read_pid(p.run_pid)
    rec = _read_json(p.status) or {}
    active = bool(rec) and rec.get("state") not in rc.NAV_TERMINAL + ("idle",)
    lp = rec.get("launcher_pid")
    launcher_gone = not launcher_alive(env, lp, rec.get("launcher_start"))
    from_record = False
    if pid is None and active and launcher_gone and rec.get("origin") in ORIGINS and _is_pid(rec.get("child_pid")):
        pid, from_record = rec["child_pid"], True
    if pid is not None and run_pid_alive(env, pid):
        owned = rec.get("origin") in ORIGINS and rec.get("child_pid") == pid
        # signalled only when proven a drive_route (review 2 finding 9); reap
        # runs as root, so "cannot read" is practically never
        if owned and lp and launcher_gone and _ours(env, pid, DRIVE_ROUTE) == "yes":
            env.signal_pid(pid)
            _forget_place(p)
            try:
                _write_json(p.status, dict(rec, state="stopped", reason="app_lost", t=round(env.wall(), 1)))
            except OSError:
                pass
            return {"reap": "interrupted_orphan", "pid": pid}
        return {"reap": "left_alone", "pid": pid, "origin": rec.get("origin") if owned else "pc"}
    if pid is not None:
        result = {"reap": "nothing_running", "pid": pid}
        try:
            if env.board.current() is not None:
                env.board.cancel()
                result["reap"] = "cancelled_board_action"
        except Exception as e:                      # noqa: BLE001
            result["board"] = "unreachable: %s" % str(e)[:DETAIL_MAX]
            return result                           # keep run.pid / the record: try again next start
        if not from_record:
            _unlink(p.run_pid)
        _forget_place(p)
        if active:
            try:
                _write_json(p.status, dict(rec, state="failed", reason="app_lost", t=round(env.wall(), 1)))
            except OSError:
                pass
        return result
    if active and launcher_gone:
        # nothing was spawned (no child, no run.pid): nothing moved, the
        # robot is where it was -- only the record is closed
        try:
            _write_json(p.status, dict(rec, state="failed", reason="launcher_lost", t=round(env.wall(), 1)))
        except OSError:
            pass
        return {"reap": "closed_stale_record", "launcher_pid": lp}
    return {"reap": "nothing"}


# -- main ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    # Review finding 9: the app runs this through `su 0`, so every file it and
    # drive_route (which inherits the umask) create -- run.out, run.pid,
    # nav/*.json, runs/*.jsonl -- is root's, and su can pass the app's umask
    # 077 down. The PC side reads them as the adb shell's uid: status said
    # idle while an app leg walked, and `run`'s busy check would have started
    # a second drive_route beside it. One syscall, before any file is made.
    os.umask(SHARED_UMASK)
    argv = list(sys.argv[1:] if argv is None else argv)
    tether = None
    if argv[:1] == ["go"] and "--tether" in argv:
        # First, before any check: a STOP pressed while the checks run must be
        # heard, and the leg never spawned (safety review #2).
        tether = Tether(time.monotonic)
        tether.read(sys.stdin)
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=["check", "places", "status", "go", "stop", "reap"])
    ap.add_argument("place", nargs="?")
    ap.add_argument("--origin", choices=list(ORIGINS))
    ap.add_argument("--run-id")
    ap.add_argument("--tether", action="store_true")
    ap.add_argument("--arms-rested", action="store_true",
                    help="go: the app's restArms() returned true -- drive_route may count the base's "
                         "width in doorways (--arms-in). Without it, no leg through a door")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="stop: wait this long for drive_route to end, then kill -9 it and cancel the "
                         "board's action (robot_python stop passes %g)" % STOP_WAIT_S)
    ap.add_argument("--hold", action="store_true",
                    help="stop: when nothing is on disk, refuse a go that comes within %g s -- a walk the "
                         "app is still preparing (robot_python stop passes it)" % STOP_HOLD_S)
    a = ap.parse_args(argv)
    env = Env(paths=Paths(CODE_DIR.parent), board=Board())
    if a.what == "go":
        flags = Flags()

        def on_stop(signum, frame):
            flags.stop = True

        def on_gone(signum, frame):
            flags.gone = True
        signal.signal(signal.SIGINT, on_stop)
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), on_gone)
        return go(env, a.place or "", a.origin or "", a.run_id or "", tether=tether, flags=flags,
                  arms_rested=a.arms_rested)
    if a.what == "status":
        emit(status(env))
    elif a.what == "places":
        emit(places_caps(env))
    elif a.what == "check":
        emit(check(env))
    elif a.what == "stop":
        emit(stop(env, wait_s=max(0.0, a.wait), hold=a.hold))
    elif a.what == "reap":
        emit(reap(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
