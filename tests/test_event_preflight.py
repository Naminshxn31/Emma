"""scripts/event_preflight.py: the event day's go / no-go reads the facts and never believes a robot
it could not reach (19 Nov 2026: with the robot off, `deploy --dry-run` read "1896 to push, robot has
0 keys" -- an empty robot, not a missing one)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pf():
    spec = importlib.util.spec_from_file_location("event_preflight", ROOT / "scripts" / "event_preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _event(tmp_path, spots, scenes):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"spots": spots, "scenes": scenes}), encoding="utf-8")
    return p


def _ceremony(tmp_path, walks):
    p = tmp_path / "ceremony.json"
    p.write_text(json.dumps({"order": [{"n": 1, "steps": [{"do": "walk", "spot": s} for s in walks]}]}), encoding="utf-8")
    return p


def test_a_spot_with_no_position_is_a_no_go_until_the_robot_has_recorded_it(tmp_path):
    pf = _pf()
    event = _event(tmp_path, {"reception": {"pose": [1, 2, 90]}, "dock": {"dock": True}, "stage": {"pose": None}},
                   [{"id": "welcome", "spot": "reception"}, {"id": "rest", "spot": "dock"}])
    cer = _ceremony(tmp_path, ["stage"])
    rep = pf.Report()
    pf.check_spots(rep, event, cer, None)
    assert not rep.go and "stage (ceremony part 1)" in rep.rows[-1][2]
    rep = pf.Report()
    pf.check_spots(rep, event, cer, {"spots": {"stage": {"pose": [3, 4, 0]}}})       # recorded on the robot
    assert rep.go
    rep = pf.Report()
    pf.check_spots(rep, event, cer, {"spots": {"stage": {"note": "no pose yet"}}})
    assert not rep.go


def test_an_unreachable_robot_is_said_and_nothing_about_it_is_believed(tmp_path, monkeypatch):
    pf = _pf()
    calls = []
    monkeypatch.setattr(pf, "_reachable", lambda serial, adb: False)
    monkeypatch.setattr(pf, "_robot_spots", lambda serial, adb: calls.append("spots") or {})
    monkeypatch.setattr(pf, "check_drift", lambda rep, serial: calls.append("drift"))
    monkeypatch.setattr(pf, "check_ceremony", lambda rep, path: None)
    monkeypatch.setattr(pf, "check_slides", lambda rep, path: None)
    monkeypatch.setattr(pf, "check_recorded", lambda rep, path: None)
    monkeypatch.setattr(pf, "check_spots", lambda rep, event, cer, robot: calls.append(("spots-check", robot)))
    assert pf.main(["--serial", "10.0.0.9:5555"]) == 1                              # NO-GO
    assert calls == [("spots-check", None)]                                         # no drift, no robot spots


def test_a_note_never_blocks_and_a_no_does():
    pf = _pf()
    rep = pf.Report()
    rep.add(True, "a")
    rep.add(None, "a note")
    assert rep.go
    rep.add(False, "b")
    assert not rep.go


def test_the_robots_spots_file_missing_is_none_recorded_not_unreadable(monkeypatch):
    pf = _pf()

    class Out:
        def __init__(self, text):
            self.stdout = text
    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Out("NOFILE\n"))
    assert pf._robot_spots("x", "adb") == {}
    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Out("error: device offline"))
    assert pf._robot_spots("x", "adb") is None
    monkeypatch.setattr(pf.subprocess, "run", lambda *a, **k: Out('{"spots": {"a": {"pose": [1, 2, 3]}}}'))
    assert pf._robot_spots("x", "adb") == {"spots": {"a": {"pose": [1, 2, 3]}}}
