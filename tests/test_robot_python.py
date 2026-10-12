"""scripts/robot_python.py -- the controller runs on the robot, so what gets
there has to be whole, and has to be everything drive_route imports."""
from __future__ import annotations

import ast
import io
import lzma
import os
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import robot_python as rp  # noqa: E402


def _imports(path: Path) -> set[str]:
    """Repo modules a file imports: `from app import x`, `import app.x`, and the
    scripts drive_route pulls in by putting scripts/ on sys.path."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module == "app":
            out |= {"app/%s.py" % a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            out.add(node.module.replace(".", "/") + ".py")
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("app."):
                    out.add(a.name.replace(".", "/") + ".py")
                elif (ROOT / "scripts" / (a.name + ".py")).exists():
                    out.add("scripts/%s.py" % a.name)
    return out


def test_deploy_carries_everything_the_controller_imports():
    # A module left on the PC is an ImportError on the robot -- found the first
    # time someone starts a run there, or worse, inside a lazy import mid-route.
    seen, todo = set(), ["scripts/drive_route.py"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo += sorted(_imports(ROOT / f) - seen)
    missing = seen - set(rp.CODE)
    assert not missing, "drive_route needs %s on the robot; add to robot_python.CODE" % sorted(missing)
    assert "app/__init__.py" in rp.CODE


def _ar(members: dict[str, bytes]) -> bytes:
    out = b"!<arch>\n"
    for name, data in members.items():
        out += ("%-16s%-12s%-6s%-6s%-8s%-10d`\n" % (name, 0, 0, 0, 100644, len(data))).encode() + data
        if len(data) & 1:
            out += b"\n"
    return out


def _deb(files: dict[str, bytes], links: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
        for name, target in links.items():
            ti = tarfile.TarInfo(name)
            ti.type, ti.linkname = tarfile.SYMTYPE, target
            t.addfile(ti)
    return _ar({"debian-binary": b"2.0\n", "control.tar.xz": lzma.compress(b"x"),
                "data.tar.xz": lzma.compress(buf.getvalue())})


def test_libraries_keep_their_symlinks_and_leave_the_rest():
    deb = _deb({"./lib/aarch64-linux-gnu/libz.so.1.2.13": b"ELF",
                "./usr/share/doc/zlib1g/copyright": b"text",
                "./usr/lib/aarch64-linux-gnu/libstdc++.so.6.0.30": b"ELF2"},
               {"./lib/aarch64-linux-gnu/libz.so.1": "libz.so.1.2.13"})
    with tarfile.open(fileobj=io.BytesIO(rp.stage_libs([deb]))) as t:
        got = {m.name: m for m in t.getmembers()}
        assert set(got) == {"glibc/lib/aarch64-linux-gnu/libz.so.1.2.13",
                            "glibc/lib/aarch64-linux-gnu/libz.so.1",
                            "glibc/usr/lib/aarch64-linux-gnu/libstdc++.so.6.0.30"}
        # the loader asks for libz.so.1, the file is libz.so.1.2.13 -- the link is the library
        assert got["glibc/lib/aarch64-linux-gnu/libz.so.1"].issym()
        assert got["glibc/lib/aarch64-linux-gnu/libz.so.1"].linkname == "libz.so.1.2.13"
        assert t.extractfile("glibc/lib/aarch64-linux-gnu/libz.so.1.2.13").read() == b"ELF"


def test_wheels_unpack_with_their_extensions_executable():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("numpy/_core/_multiarray_umath.cpython-313-aarch64-linux-gnu.so", b"ELF")
        z.writestr("numpy.libs/libopenblas64_p-r0.3.so.0", b"ELF")
        z.writestr("numpy/__init__.py", b"")
    with tarfile.open(fileobj=io.BytesIO(rp.stage_site([buf.getvalue()]))) as t:
        modes = {m.name: m.mode for m in t.getmembers()}
    assert modes["site/numpy/_core/_multiarray_umath.cpython-313-aarch64-linux-gnu.so"] == 0o755
    assert modes["site/numpy.libs/libopenblas64_p-r0.3.so.0"] == 0o755
    assert modes["site/numpy/__init__.py"] == 0o644


def test_launcher_starts_python_through_glibcs_loader():
    text = rp.launcher("/r")
    assert text.startswith("#!/system/bin/sh")
    assert "exec $G/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1" in text
    assert "--library-path $G/lib/aarch64-linux-gnu:$G/usr/lib/aarch64-linux-gnu:$R/python/lib" in text
    assert "export PYTHONHOME=$R/python" in text
    assert "export TZ=ICT-7" in text           # log names in the owner's time, not UTC
    assert '"$@"' in text


def test_a_run_on_the_robot_outlives_adb_and_talks_to_the_board_itself():
    cmd = rp.run_command("data/robot/routes/r.json", ["--from-step", "3"], remote="/r")
    assert "nohup ../py scripts/drive_route.py data/robot/routes/r.json" in cmd
    assert "--relay http://192.168.11.1:1448 --sdk 192.168.11.1:1445" in cmd
    assert "--adb-serial ''" in cmd             # no adb on the robot to re-forward with
    assert "echo $! > ../run.pid.tmp && mv -f ../run.pid.tmp ../run.pid" in cmd
    assert "--from-step 3" in cmd
    assert "--pid-file ../run.pid" in cmd       # review finding 12: the run removes its own pid
    # the door middles the launcher's legs keep (emma_nav.build_command): one memory, PC runs too
    assert "--door-memory ../nav/door_memory.json" in cmd


# -- the shell lines, run by a real POSIX sh against a fake robot dir (review findings 2, 9) ----------
# The robot is not needed to know what these lines do: they are sh, and the
# bugs were in what sh does with them (a redirect into a file the shell uid
# cannot write; a SIGINT from the wrong uid). A read-only run.out stands in for
# a root-owned one -- the shell cannot open either for writing.

SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX sh on this machine: what robot_python's run/stop "
                                                 "shell lines DO is UNCHECKED here (only their text is)")


def _robot_dir(tmp_path, py_body: str) -> Path:
    remote = tmp_path / "emma-nav"
    (remote / "code" / "scripts").mkdir(parents=True)
    py = remote / "py"
    py.write_text("#!/bin/sh\n" + py_body, encoding="utf-8", newline="\n")
    py.chmod(0o755)
    return remote


def _sh(cmd: str, timeout: float = 30) -> str:
    r = subprocess.run([SH, "-c", cmd], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout


def _posix(p: Path) -> str:
    r = subprocess.run([SH, "-c", 'cd "$1" && pwd', "sh", str(p)], capture_output=True, encoding="utf-8")
    return r.stdout.strip()


@needs_sh
def test_run_starts_over_a_run_out_the_shell_cannot_write_and_says_started_only_when_running(tmp_path):
    remote = _robot_dir(tmp_path, 'echo "driving $*"\nsleep 4\n')
    stale = remote / "run.out"
    stale.write_text("the app's last leg\n")
    stale.chmod(0o444)                                # root's, as far as the shell uid is concerned
    out = _sh(rp.run_command("r.json", [], remote=_posix(remote)))
    pid = rp.run_started(out)
    assert pid.isdigit() and (remote / "run.pid").read_text().strip() == pid
    assert not (remote / "run.pid.tmp").exists()
    time.sleep(0.5)
    assert "driving scripts/drive_route.py r.json" in (remote / "run.out").read_text()


@needs_sh
def test_run_that_ends_at_once_is_not_reported_started(tmp_path):
    remote = _robot_dir(tmp_path, 'echo "REFUSED: --to-step 3 is a drive"\nexit 3\n')
    out = _sh(rp.run_command("r.json", ["--to-step", "3"], remote=_posix(remote)))
    with pytest.raises(rp.Fail) as e:
        rp.run_started(out)
    assert "REFUSED: --to-step 3 is a drive" in str(e.value)


def _fake_su(tmp_path) -> Path:
    su = tmp_path / "su"
    su.write_text('#!/bin/sh\necho "su $1" >> "$(dirname "$0")/su.log"\nshift\nexec "$@"\n',
                  encoding="utf-8", newline="\n")
    su.chmod(0o755)
    return su


@needs_sh
def test_stop_runs_the_launchers_stop_as_root_and_waits(tmp_path, monkeypatch):
    remote = _robot_dir(tmp_path, 'echo "{\\"stop\\": \\"stopped\\", \\"pids\\": [$$], \\"argv\\": \\"$*\\"}"\n')
    (remote / "code" / "scripts" / "emma_nav.py").write_text("STOP_WAIT_S = 10.0\n")
    _fake_su(tmp_path)
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/su")
    out = _sh(rp.stop_command(_posix(remote)))
    assert (tmp_path / "su.log").read_text().strip() == "su 0"          # through su, as the app does
    assert "scripts/emma_nav.py stop --wait 10" in out
    assert rp.stop_report(out)[1] is True
    # an emma_nav deployed before --wait: the plain stop, never an argparse exit that stops nothing
    (remote / "code" / "scripts" / "emma_nav.py").write_text("# older launcher\n")
    out = _sh(rp.stop_command(_posix(remote)))
    assert "scripts/emma_nav.py stop\"" in out and "--wait" not in out
    # no su on the robot: the same line, as the shell
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/no-such-su")
    out = _sh(rp.stop_command(_posix(remote)))
    assert "scripts/emma_nav.py stop" in out and (tmp_path / "su.log").read_text().count("su 0") == 2


@needs_sh
def test_status_lines_go_through_su_with_their_dollars_intact(tmp_path, monkeypatch):
    remote = _robot_dir(tmp_path, "echo unused\n")
    (remote / "run.pid").write_text("%d\n" % os.getpid())
    _fake_su(tmp_path)
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/su")
    # no emma_nav deployed: the old probe, whose $p must be the inner shell's
    out = _sh(rp.busy_command(_posix(remote)))
    assert (tmp_path / "su.log").read_text().strip() == "su 0"
    assert out.strip() in (rp.NO_RUN_PID, str(os.getpid()))   # never the outer shell's empty $p as text
    assert "$p" not in out
    assert rp.nav_status_command("/r").startswith("if [ -x ")


def test_a_stop_that_did_not_happen_is_never_printed_as_one():
    """Review findings 1, 2: "denied" (a root run, signalled from the shell
    uid) used to read "none", and a SIGINT nobody heard read "sent"."""
    # before emma_nav is deployed the app cannot walk at all (its GUARD): nothing is nothing
    assert rp.stop_report(rp.NO_RUN_PID) == ("no run going", True)
    assert rp.stop_report('{"stop": "stopped", "pids": [7]}')[1] is True
    msg, ok = rp.stop_report('{"stop": "denied", "pids": [7]}')
    assert not ok and "NOT STOPPED" in msg
    msg, ok = rp.stop_report('{"stop": "not_stopped", "pids": [7], "kill": "denied"}')
    assert not ok and "NOT STOPPED" in msg
    msg, ok = rp.stop_report('{"stop": "escalated", "pids": [7], "kill": "sent", "board": "cancelled"}')
    assert ok and "kill -9" in msg
    assert rp.stop_report('{"stop": "escalated", "pids": [7], "kill": "sent", "board": "unreachable: x"}')[1] is False
    assert rp.stop_report("sent SIGINT to 7") == ("sent SIGINT to 7", True)    # the pre-emma_nav fallback
    # Integrator's check of finding 2: silence (su refused, launcher crashed)
    # and stray text are "could not stop", never "no run going".
    for out in ("", "Permission denied", "kill -INT 7 failed"):
        msg, ok = rp.stop_report(out)
        assert not ok and "NOT STOPPED" in msg, out


def test_nothing_on_disk_is_never_printed_as_no_walk_coming():
    """Review 2 finding 8: a walk the face app accepted lives in its memory
    alone while it rests the arms (up to 15 s). `stop` found nothing on disk,
    printed "no run going" -- and the robot walked."""
    msg, ok = rp.stop_report('{"stop": "none", "pids": []}')
    assert ok and "no run going" not in msg and "check the robot's screen" in msg
    msg, ok = rp.stop_report('{"stop": "none", "pids": [], "hold_s": 30.0}')
    assert ok and "no run going" not in msg and "refused if it starts within 30 s" in msg
    assert "STOP on its screen" in msg


@needs_sh
def test_stop_passes_hold_only_to_a_launcher_that_knows_it(tmp_path, monkeypatch):
    # an unknown flag is an argparse exit that stops nothing: grepped, as --wait is
    remote = _robot_dir(tmp_path, 'echo "{\\"stop\\": \\"none\\", \\"pids\\": [], \\"argv\\": \\"$*\\"}"\n')
    emma = remote / "code" / "scripts" / "emma_nav.py"
    emma.write_text("STOP_WAIT_S = 10.0\nSTOP_HOLD_S = 30.0\n")
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/no-such-su")
    assert "scripts/emma_nav.py stop --wait 10 --hold" in _sh(rp.stop_command(_posix(remote)))
    emma.write_text("STOP_WAIT_S = 10.0\n")
    out = _sh(rp.stop_command(_posix(remote)))
    assert "scripts/emma_nav.py stop --wait 10" in out and "--hold" not in out


@needs_sh
def test_run_and_stop_before_the_first_deploy_answer_for_themselves(tmp_path, monkeypatch):
    """Review 2 finding 19: `install` makes REMOTE but no code/. The lines
    began `cd REMOTE/code && ...`: the cd failed, stdout was empty, and `run`
    refused with "su refused the adb shell" before reaching its own deploy."""
    remote = tmp_path / "emma-nav"
    (remote / "python").mkdir(parents=True)            # what install leaves: no code/
    _fake_su(tmp_path)
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/su")
    assert rp.run_going(_sh(rp.busy_command(_posix(remote)))) is None
    assert rp.stop_report(_sh(rp.stop_command(_posix(remote)))) == ("no run going", True)
    # a REMOTE that is not there at all (no install yet) answers the same
    gone = _posix(tmp_path) + "/nothing-here"
    assert rp.run_going(_sh(rp.busy_command(gone))) is None
    # and silence still means "could not ask", never "nothing going"
    _refusing_su(tmp_path)
    assert rp.run_going(_sh(rp.busy_command(_posix(remote)))) is not None


def _refusing_su(tmp_path) -> Path:
    su = tmp_path / "su"
    su.write_text('#!/bin/sh\necho "su: uid 2000 not allowed" >&2\nexit 1\n', encoding="utf-8", newline="\n")
    su.chmod(0o755)
    return su


@needs_sh
def test_a_su_that_refuses_is_neither_no_run_nor_idle(tmp_path, monkeypatch):
    """The robot's su exists but refuses the shell uid: stdout is empty. That
    used to print "no run going" (exit 0) and let `run` start a second
    drive_route beside the app's."""
    remote = _robot_dir(tmp_path, "echo unused\n")
    _refusing_su(tmp_path)
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/su")
    msg, ok = rp.stop_report(_sh(rp.stop_command(_posix(remote))))
    assert not ok and "NOT STOPPED" in msg
    assert rp.run_going(_sh(rp.busy_command(_posix(remote)))) is not None
    # and with no su at all, the same lines answer for themselves
    monkeypatch.setattr(rp, "SU", _posix(tmp_path) + "/no-such-su")
    assert rp.stop_report(_sh(rp.stop_command(_posix(remote)))) == ("no run going", True)
    assert rp.run_going(_sh(rp.busy_command(_posix(remote)))) is None


def test_every_download_is_pinned():
    assert len(rp.PYTHON[1]) == 64
    for pins in (rp.DEBS, rp.WHEELS):
        assert pins and all(len(h) == 64 and int(h, 16) >= 0 for h in pins.values())


def _module_imports(path: Path) -> set[str]:
    """Repo modules imported when `path` is *imported* -- module level only.
    robot_places.match() imports app.tools.retrieval inside itself: that is
    server-side, and the launcher never calls it (checked below)."""
    out = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        nodes = [node] + (list(ast.walk(node)) if isinstance(node, (ast.If, ast.Try)) else [])
        for n in nodes:
            if isinstance(n, ast.ImportFrom) and n.module == "app":
                out |= {"app/%s.py" % a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("app."):
                out.add(n.module.replace(".", "/") + ".py")
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.startswith("app."):
                        out.add(a.name.replace(".", "/") + ".py")
                    elif (ROOT / "scripts" / (a.name + ".py")).exists():
                        out.add("scripts/%s.py" % a.name)
    return out


def test_deploy_carries_everything_the_launcher_imports():
    # emma_nav.py runs on the robot under a bare Python: a module left on the PC
    # is the app's first walk failing with an ImportError (design 2026-10-08 §2e)
    seen, todo = set(), ["scripts/emma_nav.py"]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo += sorted(_module_imports(ROOT / f) - seen)
    assert {"app/robot_contracts.py", "app/robot_places.py"} <= seen
    missing = seen - set(rp.CODE)
    assert not missing, "emma_nav needs %s on the robot; add to robot_python.CODE" % sorted(missing)
    # the one lazy server-side import in robot_places is never reached from the launcher
    src = (ROOT / "scripts" / "emma_nav.py").read_text(encoding="utf-8")
    assert "rp.match(" not in src and "robot_places.match(" not in src
    assert "drive_route" not in _module_imports(ROOT / "scripts" / "emma_nav.py")


class _FakeRobot:
    def __init__(self):
        self.shells, self.puts = [], {}

    def shell(self, cmd, check=True, timeout=600):
        self.shells.append(cmd)
        return ""

    def put(self, data, remote):
        self.puts[remote] = data


def test_deploy_pushes_the_places_and_writes_a_manifest_of_what_it_pushed(tmp_path):
    import hashlib
    import json as _json
    for rel in rp.CODE + ["data/robot-places/floor2.json", "data/robot/routes/r.json"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(("content of %s" % rel).encode())
    robot = _FakeRobot()
    rp.deploy(robot, [], root=tmp_path)
    remote = rp.REMOTE + "/code/"
    pushed = {k[len(remote):]: v for k, v in robot.puts.items() if k.startswith(remote)}
    assert "data/robot-places/floor2.json" in pushed and "scripts/emma_nav.py" in pushed
    assert "data/robot/routes/r.json" in pushed
    man = _json.loads(pushed.pop("MANIFEST.json"))
    assert set(man["files"]) == set(pushed)
    assert all(man["files"][f] == hashlib.sha256(b).hexdigest() for f, b in pushed.items())
    assert man["deployed_at"]
    assert list(robot.puts)[-1].endswith("/code/MANIFEST.json")       # last: a cut-off deploy is caught
    assert rp.REMOTE + "/nav" in robot.shells[0]


def test_stop_is_the_launchers_stop_with_the_old_one_as_fallback():
    cmd = rp.stop_command("/r")
    assert "../py scripts/emma_nav.py stop" in cmd
    assert "kill -INT $p" in cmd                      # before emma_nav is deployed, stop still stops
    assert "cd /r/code && ../py scripts/emma_nav.py status" in rp.nav_status_command("/r")


def test_a_run_the_face_app_started_counts_as_going():
    # a root-owned run: the shell's own kill -0 says EPERM and read "no run going"
    assert rp.run_going("") is not None              # silence = could not ask
    assert rp.run_going(rp.NO_RUN_PID) is None
    assert rp.run_going("1234") == "1234"
    running = '{"state": "running", "origin": "tap", "child_pid": 4711, "launcher_pid": 4700}'
    assert "4711" in rp.run_going(running)
    starting = '{"state": "starting", "origin": "voice", "child_pid": null, "launcher_pid": 4700}'
    assert "4700" in rp.run_going(starting)
    for idle in ("idle", "arrived", "stopped", "refused", "failed"):
        assert rp.run_going('{"state": "%s"}' % idle) is None
    assert rp.run_going("{broken") is not None        # cannot tell = do not start a second
    assert "emma_nav.py status" in rp.busy_command("/r")
