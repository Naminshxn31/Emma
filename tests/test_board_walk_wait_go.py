"""board_walk --wait-go (24 Nov 2026): ready in the build, wheels on the screen's hit.

23 Nov on the robot all five ceremony openings left the box 1.29-1.39 s after the hit (turnlog `event_tour
late_walk_out`): board_walk's own start-up before `@@ started`. With --wait-go the walker does its gates and warm-up,
says `@@ ready`, and drives only on a "go" line on stdin -- never says `started` (= never moved, app/event_box.py)
without it. These hold the walker's half; the server's half (spawn early, go on the hit) is not wired yet.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: A mutation run swaps this for a changed copy of the script (proving these red without the change).
SCRIPT = ROOT / "scripts" / "board_walk.py"


def _load():
    spec = importlib.util.spec_from_file_location("board_walk_wait_go", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bw = _load()


class _Board:
    def __init__(self):
        self.busy = False

    def quality(self):
        return 60

    def current(self):
        return {"action_name": "other"} if self.busy else None

    def pose(self):
        return (-7.3, -6.2, 0.0)


class _Words:
    """A GoLine: `None` for each wait until its word."""

    def __init__(self, word, after=0):
        self.word, self.after, self.waits = word, after, 0

    def wait(self, timeout):
        self.waits += 1
        return self.word if self.waits > self.after else None


def _walker(word, after=0, wait_s=30.0):
    w = bw.Walker(_Board(), {"dock": {"pose": [0, 0, 0]}, "events": True}, False, False)
    w.go_line, w.go_wait_s = _Words(word, after), wait_s
    seen = []
    w.warm_up = lambda: seen.append("warm")
    w.keep_warm = lambda: seen.append("keep")
    w.step = lambda step, i, through=False: seen.append(("step", i))
    return w, seen


def _lines(out: str) -> list[dict]:
    return [json.loads(line[3:]) for line in out.splitlines() if line.startswith("@@ ")]


def test_the_go_releases_the_walk_and_started_comes_only_after_it(capsys):
    w, seen = _walker("go", after=2)
    w.run([{"forward": 1.0, "short_ok": False}], 1)
    lines = _lines(capsys.readouterr().out)
    kinds = [next(iter(d)) for d in lines]
    # ready (after the gates and the warm-up), then the waits kept warm, then started, then the step
    assert kinds.index("ready") < kinds.index("started"), lines
    assert seen == ["warm", "keep", "keep", ("step", 1)], seen
    started = next(d for d in lines if "started" in d)
    assert started["at"] == [-7.3, -6.2]


@pytest.mark.parametrize("word", ["stop", "eof", "error", "empty"])
def test_anything_but_go_is_no_walk_and_never_says_started(capsys, word):
    w, seen = _walker(word)
    with pytest.raises(bw.NoGo):
        w.run([{"forward": 1.0}], 1)
    lines = _lines(capsys.readouterr().out)
    assert not [d for d in lines if "started" in d] and ("step", 1) not in seen


def test_no_go_in_its_time_is_no_walk(capsys, monkeypatch):
    clock = {"t": 0.0}

    class T:
        @staticmethod
        def monotonic():
            clock["t"] += 0.4
            return clock["t"]

        @staticmethod
        def sleep(s):
            clock["t"] += s

    monkeypatch.setattr(bw, "time", T)
    w, seen = _walker(None, wait_s=3.0)
    with pytest.raises(bw.NoGo, match="no go in 3 s"):
        w.run([{"forward": 1.0}], 1)
    assert not [d for d in _lines(capsys.readouterr().out) if "started" in d] and ("step", 1) not in seen


def test_another_action_on_the_board_while_waiting_is_the_start_gate_again(capsys):
    w = bw.Walker(_Board(), {"dock": {"pose": [0, 0, 0]}, "events": True}, False, False)

    class Sdk:
        def call(self, command, args=None, timeout=None):
            return {"code": 1}

        def close(self):
            pass

    w.sdk = Sdk()
    w.keep_warm()                                         # the board idle: nothing said
    w.b.busy = True
    with pytest.raises(bw.Stop, match="another action"):
        w.keep_warm()


def test_without_the_flag_nothing_waits(capsys):
    w, seen = _walker("never asked")
    w.go_line = None
    w.run([{"forward": 1.0}], 1)
    kinds = [next(iter(d)) for d in _lines(capsys.readouterr().out)]
    assert "ready" not in kinds and "started" in kinds and seen == [("step", 1)]


def test_main_exits_no_go_without_a_started_line(tmp_path, monkeypatch, capsys):
    route = tmp_path / "walk.json"
    route.write_text(json.dumps({"events": True, "paths": {"event": {"steps": [{"forward": 1.0}]}}}), encoding="utf-8")

    class B(_Board):
        def speed(self):
            return None

        def power(self):
            return {"dockingStatus": "not_on_dock"}

    monkeypatch.setattr(bw, "Board", B)
    monkeypatch.setattr(bw, "GoLine", lambda: _Words("eof"))
    monkeypatch.setattr(bw.Walker, "warm_up", lambda self: None)
    monkeypatch.setattr(bw, "stop_on_signals", lambda: None)
    assert bw.main([str(route), "event", "--wait-go", "20"]) == bw.NO_GO_EXIT
    lines = _lines(capsys.readouterr().out)
    assert {"no_go": "eof"} in lines and not [d for d in lines if "started" in d], lines


_CHILD = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('bw', sys.argv[1]); "
          "bw = importlib.util.module_from_spec(spec); spec.loader.exec_module(bw); "
          "print(bw.GoLine().wait(float(sys.argv[2])), flush=True)")


@pytest.mark.parametrize("send, wait_s, want", [(b"go\n", 20.0, "go"), (None, 20.0, "eof"), (b"", 0.3, "None")])
def test_the_go_line_reads_a_real_pipe_and_the_process_exits_cleanly(send, wait_s, want):
    """The real stdin path (os.read in a daemon thread): "go", EOF (the server gone), nothing in time -- and the child
    exits 0 either way (no 'could not acquire lock for <stdin>' abort at shutdown)."""
    proc = subprocess.Popen([sys.executable, "-c", _CHILD, str(SCRIPT), str(wait_s)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        if send is None:
            proc.stdin.close()
        elif send:
            proc.stdin.write(send)
            proc.stdin.flush()
        out, err = proc.communicate(timeout=30) if send is None else _finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert out.decode().strip() == want and proc.returncode == 0, (out, err)


def _finish(proc):
    out = proc.stdout.read()
    err = proc.stderr.read()
    proc.wait(timeout=30)
    with_close = proc.stdin
    try:
        with_close.close()
    except OSError:
        pass
    return out, err
