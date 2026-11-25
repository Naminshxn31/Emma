"""The event morning by buttons (owner 19 Nov 2026, after a night of scripts at the robot:
"ตรวจสอบให้ด้วยวันงานถ้าจะใช้ปุ่มกดเพื่อทำแต่ละซีนจะไม่มีปัญหาใช่ไหม ไม่จำเป็นต้องมาโค้ดแบบนี้").

That night the dock had been put down 17 cm from its file pose (the owner: it is set up again
every time), the board's pose after the reboot was stale, and finding the dock took a terminal: a
lidar search round the file's pose, `--set-dock-pose`, a temporary route of 0.8 m out and
`back_to` in, the file edited by hand. Then the first walk did not move at all -- the E-stop was
still in, which the board does not report. What these hold down:

* board_walk --find-dock-pose: the search finds a dock put down 11 cm deeper / 9 cm aside /
  1.4 deg turned; a best on the search's edge is not taken; --set writes the pose only when good;
* the staff page's "วัดแท่นใหม่" (event_host.relearn_dock): only on the dock, then ONE walk of
  board_walk's own `forward` and `back_to`, the second look on the contacts, halfway written to the
  robot's spots file -- which every walk's dock then uses (dock_doc) -- and every refusal in Thai;
* "ตั้งตำแหน่งที่แท่น" on a moved dock says the dock moved and points at the re-learn;
* the morning's GO / NO-GO read on the robot (GET /event/ready, app/event_readiness.py);
* a walk held or stopped where it set out says the wheels never turned (E-stop?); a board_walk
  stop with no kind of its own reaches the phone in Thai, its English kept in the details;
* the page: the card, its verdict, the re-learn's two taps, the E-stop line (Chromium).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
import struct
import sys
import time
from pathlib import Path

import pytest

from app import event_host, event_readiness, event_walk
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
CEREMONY_PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")
FILE_DOCK = [-7.11, -6.78, 169.6]


def _bw():
    spec = importlib.util.spec_from_file_location("board_walk_setup", ROOT / "scripts" / "board_walk.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def event(tmp_path, monkeypatch):
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"dock": {"pose": FILE_DOCK, "approach": [-9.0, -6.2], "back_from_m": 1.9},
                                 "paths": {}}), encoding="utf-8")
    doc["route_file"] = str(route)
    path = tmp_path / "floor1_event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(event_walk, "GATE_WAIT_S", 0.0)
    monkeypatch.setattr(event_host, "RELEARN_SETTLE_S", 0.0)
    event_host.reset()
    event_readiness.reset()
    yield path
    event_host.reset()


# ---- board_walk: the search ---------------------------------------------------------------------

def _room_map(bw):
    """A 6 x 6 m room with a nook (walls 5 cm thick) as board_walk's explore blob -> MapFit."""
    res, w, h, ox, oy = 0.05, 160, 160, -12.0, -10.0
    cells = bytearray([0] * (w * h))

    def wall(x0, y0, x1, y1):
        n = int(max(abs(x1 - x0), abs(y1 - y0)) / 0.01) + 1
        for k in range(n + 1):
            x, y = x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n
            c, r = int((x - ox) / res), int((y - oy) / res)
            if 0 <= c < w and 0 <= r < h:
                cells[r * w + c] = 0x9c                   # int8 -100: occupied
    corners = [(-10.0, -9.0), (-4.0, -9.0), (-4.0, -3.0), (-10.0, -3.0)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        wall(*a, *b)
    wall(-7.6, -7.4, -7.6, -6.2)                          # the nook the dock stands in
    wall(-7.6, -7.4, -6.6, -7.4)
    wall(-6.9, -4.2, -6.2, -4.2)                          # a table edge
    blob = struct.pack("<ffIIf", ox, oy, w, h, res) + b"\0" * 12 + struct.pack("<I", w * h) + bytes(cells)
    return bw.MapFit(blob), corners


def _scan(true_pose, corners):
    """Robot-frame lidar points from `true_pose`: every wall, a ray every 0.5 deg over 218 deg."""
    segs = list(zip(corners, corners[1:] + corners[:1])) + [((-7.6, -7.4), (-7.6, -6.2)),
                                                           ((-7.6, -7.4), (-6.6, -7.4)), ((-6.9, -4.2), (-6.2, -4.2))]
    x, y, yaw = true_pose
    pts = []
    for k in range(-218, 219):
        a = math.radians(k * 0.5)
        dx, dy = math.cos(yaw + a), math.sin(yaw + a)
        best = None
        for (x0, y0), (x1, y1) in segs:
            ex, ey = x1 - x0, y1 - y0
            den = dx * ey - dy * ex
            if abs(den) < 1e-9:
                continue
            t = ((x0 - x) * ey - (y0 - y) * ex) / den
            u = ((x0 - x) * dy - (y0 - y) * dx) / den
            if t > 0.05 and 0.0 <= u <= 1.0 and (best is None or t < best):
                best = t
        if best is not None and best < 8.0:
            pts.append((best * math.cos(a), best * math.sin(a)))
    return pts


def test_the_search_finds_a_dock_put_down_somewhere_else():
    """19 Nov: 11 cm deeper, 9 cm aside, 1.4 deg -- the file's pose fit 0.14, the search 0.71."""
    bw = _bw()
    fit, corners = _room_map(bw)
    file_pose = (-7.11, -6.78, math.radians(169.6))
    true = (-7.00, -6.92, math.radians(171.0))
    frames = [_scan(true, corners)] * 3
    assert fit.score(true, frames[0]) > 0.9 and fit.score(file_pose, frames[0]) < 0.6
    found = bw.search_dock(fit, frames, file_pose)
    x, y, yaw = found["pose"]
    assert not found["edge"]
    assert math.hypot(x - true[0], y - true[1]) < 0.03, found          # under a 5 cm map cell
    assert abs(math.degrees(yaw - true[2])) < 0.75, found


def _real_night():
    """19 Nov on floor 1, the board's own map round the dock and three lidar frames twice: as the
    owner put the robot on the re-placed dock, and after it docked itself (`back_to`). Laser
    returns only (tests/data/dock_relearn_20261119.npz, cropped from .tmp/floor1-20261119/)."""
    import numpy as np

    bw = _bw()
    d = np.load(ROOT / "tests" / "data" / "dock_relearn_20261119.npz")
    ox, oy, w, h, res = d["map_header"]
    cells = d["map_cells"].astype(np.int8)
    blob = struct.pack("<ffIIf", ox, oy, int(w), int(h), res) + b"\0" * 12 + struct.pack("<I", cells.size) + cells.tobytes()
    return bw, bw.MapFit(blob), list(d["placed"]), list(d["self_docked"])


def test_on_that_nights_real_frames_the_search_finds_what_the_terminal_found():
    """By hand that night: the file's pose fit 0.14, a search round it 0.71 at (-7.02, -6.91, 171.0);
    after docking itself 0.72 at (-7.07, -6.90, 169.4); the file then got (-7.04, -6.89, 171.0)."""
    bw, fit, placed, docked = _real_night()
    file_pose = (-7.11, -6.78, math.radians(169.6))

    def look(frames):
        found = bw.search_dock(fit, frames, file_pose)
        best = sorted(fit.score(found["pose"], f) for f in frames)[1]
        return found, best

    a, fit_a = look(placed)
    assert not a["edge"] and fit_a >= bw.DOCK_FIND_MIN
    assert sorted(fit.score(file_pose, f) for f in placed)[1] < bw.DOCK_FIT_MIN       # why the old button failed
    assert math.hypot(a["pose"][0] - -7.02, a["pose"][1] - -6.91) < 0.02 and abs(math.degrees(a["pose"][2]) - 171.0) < 0.5
    b, fit_b = look(docked)
    assert not b["edge"] and fit_b >= bw.DOCK_FIND_MIN
    assert math.hypot(b["pose"][0] - a["pose"][0], b["pose"][1] - a["pose"][1]) < event_host.RELEARN_AGREE_M
    half = ((a["pose"][0] + b["pose"][0]) / 2, (a["pose"][1] + b["pose"][1]) / 2)
    assert math.hypot(half[0] - -7.04, half[1] - -6.89) < 0.03                        # the hand value, within a map cell


def test_a_best_on_the_searchs_edge_is_not_taken():
    bw = _bw()
    fit, corners = _room_map(bw)
    file_pose = (-7.11, -6.78, math.radians(169.6))
    far = (-6.70, -6.80, math.radians(169.6))             # 41 cm out: beyond DOCK_FIND_RADIUS_M
    found = bw.search_dock(fit, [_scan(far, corners)] * 3, file_pose)
    assert found["edge"] is True


class _Board:
    """`docking` / `busy`: what each read answers, in turn (the last one repeats) -- so a test can
    change the board between the search's first read and the read right before the write."""
    def __init__(self, docking=("on_dock",), busy=(None,), at=(-7.00, -6.92, math.radians(171.0))):
        self.puts, self.docking, self.busy, self.at = [], list(docking), list(busy), at
        self.c = self

    def current(self):
        return self.busy.pop(0) if len(self.busy) > 1 else self.busy[0]

    def pose(self):
        return self.at

    def explore_map(self):
        return self.blob

    def power(self):
        return {"dockingStatus": self.docking.pop(0) if len(self.docking) > 1 else self.docking[0]}

    def quality(self):
        return 66

    def put(self, path, json=None):
        self.puts.append((path, json))
        return self

    def raise_for_status(self):
        return None


def _walker(bw, true, set_it, capsys, monkeypatch, board=None):
    fit, corners = _room_map(bw)
    board = board or _Board()
    board.blob = b""                                      # MapFit is the room's below
    monkeypatch.setattr(bw, "MapFit", lambda blob: fit)
    monkeypatch.setattr(bw.time, "sleep", lambda s: None)
    w = type("W", (), {})()
    w.b, w.doc = board, {"dock": {"pose": FILE_DOCK}}
    w.scan = lambda: _scan(true, corners)
    code = bw.find_dock_pose(w, set_it)
    line = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("@@ ")][-1]
    return code, json.loads(line[3:]), board


def test_find_dock_pose_sets_the_pose_only_when_the_search_is_good(capsys, monkeypatch):
    bw = _bw()
    code, word, board = _walker(bw, (-7.00, -6.92, math.radians(171.0)), True, capsys, monkeypatch)
    assert code == 0 and word["ok"] and word["set"] and word["on_dock"]
    assert word["fit"] >= bw.DOCK_FIND_MIN and word["file_fit"] < word["fit"]
    assert len(board.puts) == 1 and board.puts[0][0] == "/api/core/slam/v1/localization/pose"
    assert abs(board.puts[0][1]["x"] - -7.00) < 0.03 and word["along_m"] < -0.05      # deeper in
    # far beyond the search: said, never written
    code, word, board = _walker(bw, (-6.70, -6.80, math.radians(169.6)), True, capsys, monkeypatch)
    assert code == 2 and word["edge"] and not word["ok"] and "set" not in word and board.puts == []
    # without --set nothing is written even when it is good
    code, word, board = _walker(bw, (-7.00, -6.92, math.radians(171.0)), False, capsys, monkeypatch)
    assert word["ok"] and board.puts == []


def test_find_dock_pose_reads_the_board_again_right_before_writing(capsys, monkeypatch):
    """Review 20 Nov: the search takes seconds. The pose is written only if the board STILL says
    on the dock and runs no action at the moment of the write."""
    bw = _bw()
    true = (-7.00, -6.92, math.radians(171.0))
    off = _Board(docking=["on_dock", "not_on_dock"])               # pushed off the contacts meanwhile
    code, word, board = _walker(bw, true, True, capsys, monkeypatch, board=off)
    assert word["ok"] and "set" not in word and word["not_set"] == "not_on_dock" and board.puts == []
    busy = _Board(busy=[None, "MoveAction"])                        # an action began meanwhile
    code, word, board = _walker(bw, true, True, capsys, monkeypatch, board=busy)
    assert word["ok"] and "set" not in word and word["not_set"] == "busy" and board.puts == []


def test_find_dock_pose_says_the_lidar_at_the_boards_own_pose():
    """`board_fit`: the morning card's check of a stale pose (Q can still say 50+)."""
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    assert 'word["board_fit"] = round(median_fit(tuple(float(v) for v in w.b.pose()[:3])), 2)' in src


def test_set_dock_pose_reads_the_board_again_before_writing():
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    body = src[src.index("def set_dock_pose(w: Walker)"):src.index("def search_dock(")]
    assert body.count("if w.b.current() is not None:") == 2         # at the start, and before the write


def test_board_walk_says_where_it_set_out_and_where_it_stopped():
    src = (ROOT / "scripts" / "board_walk.py").read_text(encoding="utf-8")
    assert "self.event(started=True, q=q, at=at)" in src
    assert "w.event(stopped_at=" in src
    assert '"--find-dock-pose"' in src and "find_dock_pose(w, args.set)" in src


# ---- event_walk: the wheels never turned ------------------------------------------------------------

def _run(**more):
    run = event_walk.Run("reception", "กลางส่วนต้อนรับ")
    run.steps = [{"do": "undock"}, {"to": [-9.0, -5.2]}]
    for k, v in more.items():
        setattr(run, k, v)
    return run


def test_held_where_it_set_out_says_the_e_stop():
    run = _run(state="walking")
    event_walk._on_line(run, {"started": True, "q": 60, "at": [-7.04, -6.89]})
    event_walk._on_line(run, {"held": 1, "at": [-7.04, -6.89]})
    view = run.view()
    assert view["still"] is True and "E-stop" in view["still_th"] and "บอร์ดไม่บอก" in view["still_th"]
    # it rolled on: no more hint
    event_walk._on_line(run, {"held": 2, "at": [-8.2, -5.9]})
    assert run.view()["still"] is False and run.view()["still_th"] is None


def test_stopped_where_it_set_out_says_it_too_but_not_after_a_persons_stop():
    run = _run(state="failed", reason="undock: 20 s and not out")
    event_walk._on_line(run, {"started": True, "q": 60, "at": [-7.04, -6.89]})
    event_walk._on_line(run, {"stopped_at": [-7.045, -6.892]})
    view = run.view()
    assert view["still"] is True and view["kind"] == "blocked"          # "not out" has a kind now
    person = _run(state="stopped", reason="staff")
    event_walk._on_line(person, {"started": True, "at": [0, 0]})
    event_walk._on_line(person, {"stopped_at": [0, 0]})
    assert person.view()["still"] is False
    # no start pose (an old walker, a route without events): nothing guessed
    assert _run(state="failed", reason="stuck").view()["still"] is False


def test_a_board_walk_stop_with_no_kind_reaches_the_phone_in_thai():
    """Harness 19 Nov: "turn: 20 s and still ..." came through as it was, with no next move."""
    run = _run(state="failed", reason="turn: 20 s and still 30 deg off")
    view = run.view()
    assert view["kind"] is None
    assert view["reason_th"].startswith("เดินไม่สำเร็จ") and "เดินต่อ" in view["next_th"]
    assert run.reason == "turn: 20 s and still 30 deg off"               # the English stays for the details
    known = _run(state="stopped", reason="staff").view()
    assert known["reason_th"] == "มีคนสั่งหยุด"                           # a kind of its own is untouched


# ---- the re-learnt dock ------------------------------------------------------------------------------

def test_a_relearnt_dock_is_every_walks_dock_and_a_spot_save_keeps_it(event):
    assert event_host.dock_doc(event_host.load())["pose"] == FILE_DOCK
    event_host._save_recorded(lambda spots: None, dock={"pose": [-7.04, -6.89, 171.0], "recorded_at": "2026-11-25 07:40"})
    dock = event_host.dock_doc(event_host.load())
    assert dock["pose"] == [-7.04, -6.89, 171.0]
    assert dock["approach"] == [-9.0, -6.2] and dock["back_from_m"] == 1.9      # the rest is the file's
    event_host._save_recorded(lambda spots: spots.update(table_1={"pose": [-8.0, -2.0, 0.0]}))
    saved = json.loads(event_host.spots_path().read_text(encoding="utf-8"))
    assert saved["dock"]["pose"] == [-7.04, -6.89, 171.0] and saved["spots"]["table_1"]
    assert event_host.dock_doc(event_host.load())["pose"] == [-7.04, -6.89, 171.0]


def _found(pose, fit=0.71, **more):
    x0, y0, deg0 = FILE_DOCK
    yaw0 = math.radians(deg0)
    dx, dy = pose[0] - x0, pose[1] - y0
    word = {"dock_find": True, "ok": fit >= 0.55, "fit": fit, "file_fit": 0.14, "pose": list(pose),
            "shift_m": round(math.hypot(dx, dy), 3), "along_m": dx * math.cos(yaw0) + dy * math.sin(yaw0),
            "aside_m": -dx * math.sin(yaw0) + dy * math.cos(yaw0), "turn_deg": pose[2] - deg0,
            "edge": False, "on_dock": True}
    word.update(more)
    return {"ok": True, "found": word}


def test_set_pose_on_a_moved_dock_says_so_and_points_at_the_relearn(event, monkeypatch):
    async def set_pose(dock):
        return {"ok": False, "error": "fit", "error_th": event_walk.REASONS_TH["fit"], "fit": 0.14}

    looked = []

    async def find(dock, *, set_it, hold=None):
        looked.append(set_it)
        return _found((-7.00, -6.92, 171.0))

    monkeypatch.setattr(event_walk, "set_dock_pose", set_pose)
    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    out = asyncio.run(event_host.dock_pose())
    assert out["ok"] is False and out["error"] == "dock_moved" and out["relearn"] is True
    assert "วัดแท่นใหม่" in out["error_th"] and "ลึกเข้าไป" in out["error_th"]
    assert looked == [False]                              # a look only: nothing written
    # not a moved dock (the search finds nothing better either): the old answer, as it was
    monkeypatch.setattr(event_walk, "find_dock_pose", lambda dock, *, set_it: asyncio.sleep(0, _found(FILE_DOCK, fit=0.3)))
    assert asyncio.run(event_host.dock_pose())["error"] == "fit"


def _relearn_mocks(monkeypatch, *, docking=("on_dock", "on_dock"), first=None, second=None, walk_state="arrived"):
    statuses = list(docking)
    calls = {"find": [], "start": [], "set_pose": []}

    async def status():
        return statuses.pop(0) if statuses else "on_dock"

    finds = [first or _found((-7.00, -6.92, 171.0), set=True), second or _found((-7.06, -6.90, 169.4), fit=0.72, set=True)]

    async def find(dock, *, set_it, hold=None):
        calls["find"].append((set_it, list(dock["pose"])))
        return finds.pop(0)

    async def start(spot, name, steps, *, dock, on_end=None, on_event=None, stops_seen=None, **kw):
        calls["start"].append({"spot": spot, "steps": steps, "dock": dock})
        run = event_walk.Run(spot, name)
        run.steps, run.state = steps, walk_state
        run.reason = None if walk_state == "arrived" else "step 2 back to the dock: not on the dock (stall)"
        await on_end(run)
        return {"ok": True, "run": run.view()}

    async def set_pose(dock):
        calls["set_pose"].append(list(dock["pose"]))
        return {"ok": True, "fit": 0.72, "q": 66}

    monkeypatch.setattr(event_host, "_docking_status", status)
    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(event_walk, "set_dock_pose", set_pose)
    return calls


def test_relearn_walks_out_and_back_by_board_walks_own_steps_and_writes_halfway(event, monkeypatch):
    calls = _relearn_mocks(monkeypatch)
    out = asyncio.run(event_host.relearn_dock())
    assert out["ok"] is True
    walk = calls["start"][0]
    # the motion is board_walk's own: the debut's `forward` and the walk card's `back_to` -- nothing new
    assert [sorted(s) for s in walk["steps"]] == [["forward", "label", "speed"], ["back_to", "label"]]
    assert walk["steps"][0]["forward"] == 0.8 and walk["steps"][1]["back_to"] == [-7.0, -6.92, 171.0]
    assert walk["dock"] is None                            # no map, no MoveTo: no dock to hand over
    assert [c[0] for c in calls["find"]] == [True, True]   # set as placed, then where she sat down herself
    saved = json.loads(event_host.spots_path().read_text(encoding="utf-8"))["dock"]
    assert saved["pose"] == [-7.03, -6.91, 171.0]          # halfway A..B, heading A (19 Nov's rule)
    assert saved["was"] == FILE_DOCK and saved["shift_cm"] > 10
    assert calls["set_pose"] == []                         # the second look set it: where she sits (B), not halfway
    view = event_host.relearn_view()
    assert view["phase"] == "done" and view["today"] is True and "พร้อมเดิน" in view["msg_th"]
    assert event_host.dock_doc(event_host.load())["pose"] == [-7.03, -6.91, 171.0]
    assert event_host.shared_snapshot()["relearn"]["today"] is True


@pytest.mark.parametrize("case, kw, want", [
    ("not_on_dock", {"docking": ("not_on_dock",)}, "เข็นหุ่นเข้าแท่น"),
    ("edge", {"first": _found((-6.7, -6.8, 169.6), edge=True, ok=False)}, "เกิน 30 ซม."),
    ("not_found", {"first": _found((-7.0, -6.9, 171.0), fit=0.31)}, "หาตำแหน่งแท่นด้วยเลเซอร์ไม่เจอ"),
])
def test_relearn_refuses_before_any_walk(event, monkeypatch, case, kw, want):
    calls = _relearn_mocks(monkeypatch, **kw)
    out = asyncio.run(event_host.relearn_dock())
    assert out["ok"] is False and out["error"] == case and want in out["error_th"]
    assert calls["start"] == []
    assert event_host.relearn_view()["phase"] == "failed"
    assert "dock" not in (json.loads(event_host.spots_path().read_text(encoding="utf-8"))
                          if event_host.spots_path().exists() else {})


@pytest.mark.parametrize("case, kw, want", [
    ("not_docked", {"docking": ("on_dock", "not_on_dock")}, "ไม่ได้ชาร์จ"),
    ("disagree", {"second": _found((-7.20, -6.90, 169.4), fit=0.7)}, "ห่างกัน"),
    ("walk", {"walk_state": "failed"}, "การเดินวัดแท่นไม่สำเร็จ"),
])
def test_relearn_writes_nothing_when_the_end_is_not_a_dock(event, monkeypatch, case, kw, want):
    _relearn_mocks(monkeypatch, **kw)
    asyncio.run(event_host.relearn_dock())
    view = event_host.relearn_view()
    assert view["phase"] == "failed" and want in view["msg_th"] and view["today"] is False
    assert event_host.dock_doc(event_host.load())["pose"] == FILE_DOCK


def test_relearn_written_but_the_board_pose_not_set_is_not_ready(event, monkeypatch):
    """The dock is written (two good fits), but the second look could not set the board's pose:
    the phone says so and points at "ตั้งตำแหน่งที่แท่น" -- never "พร้อมเดิน"."""
    _relearn_mocks(monkeypatch, second=_found((-7.06, -6.90, 169.4), fit=0.72))      # no "set"
    asyncio.run(event_host.relearn_dock())
    view = event_host.relearn_view()
    assert view["phase"] == "failed" and "ตั้งตำแหน่งที่แท่น" in view["msg_th"] and "พร้อมเดิน" not in view["msg_th"]
    assert view["today"] is True                          # the dock itself was written


def test_a_pose_search_and_a_walk_hold_each_other_off(event, monkeypatch):
    """Review 20 Nov, proved: find_dock_pose asked walking() once, then searched for seconds, a walk
    set out meanwhile and the board's pose was PUT under the moving wheels. One flag, both ways."""
    seen = {}

    async def board_once(dock, *flags, timeout=0):
        seen["held"] = event_walk.pose_busy()
        walk = await event_walk.start("table_1", "x", [{"forward": 0.5}], dock=None)
        seen["walk"] = walk
        seen["find_again"] = await event_walk.find_dock_pose(dock, set_it=True)
        seen["set_again"] = await event_walk.set_dock_pose(dock)
        return {"dock_find": True, "ok": True, "fit": 0.7, "pose": FILE_DOCK}

    monkeypatch.setattr(event_walk, "_board_once", board_once)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    dock = event_host.dock_doc(event_host.load())
    out = asyncio.run(event_walk.find_dock_pose(dock, set_it=True))
    assert out["ok"] and seen["held"] is True
    assert seen["walk"]["ok"] is False and seen["walk"]["error"] == "pose_busy"
    assert "เลเซอร์" in seen["walk"]["error_th"]
    assert seen["find_again"]["error"] == "pose_busy" and seen["set_again"]["error"] == "pose_busy"
    assert event_walk.pose_busy() is False                         # given back
    assert event_walk.snapshot()["pose_busy"] is False
    # the other way: a walk on = no search, no write
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    assert asyncio.run(event_walk.find_dock_pose(dock, set_it=True))["error"] == "walking"
    assert asyncio.run(event_walk.set_dock_pose(dock))["error"] == "walking"


def test_the_relearns_look_on_the_contacts_holds_the_pose_against_walks(event, monkeypatch):
    """Review 20 Nov: the buttons came back as the wheels stopped, while she was being measured."""
    calls = _relearn_mocks(monkeypatch)
    seen = {}
    finds = [_found((-7.00, -6.92, 171.0), set=True), _found((-7.06, -6.90, 169.4), fit=0.72, set=True)]

    async def find(dock, *, set_it, hold=None):
        if len(finds) == 1:                                        # the second look, on the contacts
            seen["busy"] = event_walk.pose_busy()
            seen["hold"] = hold
            seen["running"] = event_host.relearn_view()["running"]
        return finds.pop(0)

    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    asyncio.run(event_host.relearn_dock())
    assert seen == {"busy": True, "hold": "relearn", "running": True}
    assert event_walk.pose_busy() is False and event_host.relearn_view()["running"] is False
    assert event_host.relearn_view()["phase"] == "done" and len(calls["start"]) == 1


def test_a_second_relearn_press_is_refused_and_says_nothing_on_the_first(event, monkeypatch):
    """Review 20 Nov, proved: two presses both searched with --set; the loser wrote "busy" over
    the phone's line (and RELEARN["first"]) while the robot was out."""
    calls = _relearn_mocks(monkeypatch)
    gate = {}

    async def status():
        await gate["go"].wait()
        return "on_dock"

    monkeypatch.setattr(event_host, "_docking_status", status)

    async def go():
        gate["go"] = asyncio.Event()
        a = asyncio.create_task(event_host.relearn_dock())
        await asyncio.sleep(0.01)
        before = dict(event_host.RELEARN)
        try:
            # a second press that is NOT refused waits on the same board read as the first: cut, said
            second = await asyncio.wait_for(event_host.relearn_dock(), 2.0)
        except asyncio.TimeoutError:
            second = "ran (waited on the board like the first)"
        after = dict(event_host.RELEARN)
        gate["go"].set()
        return await a, second, before, after

    first, second, before, after = asyncio.run(go())
    assert second == {"ok": False, "error": "relearn_busy", "error_th": event_host.RELEARN_BUSY_TH}
    assert before == after                                         # the first one's line untouched
    assert first["ok"] is True and len(calls["find"]) == 2 and len(calls["start"]) == 1
    assert event_host.relearn_view()["running"] is False           # given back at its end


def test_a_relearn_refused_before_walking_gives_the_button_back(event, monkeypatch):
    _relearn_mocks(monkeypatch, docking=("not_on_dock",))
    assert asyncio.run(event_host.relearn_dock())["error"] == "not_on_dock"
    assert event_host.relearn_view()["running"] is False
    _relearn_mocks(monkeypatch)
    assert asyncio.run(event_host.relearn_dock())["ok"] is True


def test_the_ceremony_app_coming_up_during_the_search_stops_the_relearn_walk(event, monkeypatch):
    """Review 20 Nov: ceremony_now() was asked once, then up to ~48 s of search before the walk."""
    calls = _relearn_mocks(monkeypatch)
    on = {"now": False}
    monkeypatch.setattr(event_host, "ceremony_now", lambda: on["now"])

    async def find(dock, *, set_it, hold=None):
        on["now"] = True                                           # Emma Ceremony opened meanwhile
        return _found((-7.00, -6.92, 171.0), set=True)

    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    out = asyncio.run(event_host.relearn_dock())
    assert out["error"] == event_host.CEREMONY_ERROR and calls["start"] == []
    assert event_host.relearn_view()["phase"] == "failed" and event_host.relearn_view()["running"] is False


def test_an_unreadable_spots_file_is_never_written_over(event, monkeypatch):
    """Review 20 Nov: after a power cut mid-write the next arrival's count rewrote it as empty --
    every recorded spot, and now the re-learnt dock, gone."""
    path = event_host.spots_path()
    path.write_text('{"spots": {"table_1": {"pose": [1, 2', encoding="utf-8")      # cut mid-write
    raw = path.read_bytes()
    with pytest.raises(event_host.SpotsUnreadable):
        event_host._save_recorded(lambda spots: spots.update(x={"arrivals": 1}))
    event_host._count_arrival("reception")                         # the silent path: logs, writes nothing
    assert path.read_bytes() == raw
    assert event_host.spots_file_broken() is True
    monkeypatch.setattr(event_walk, "walking", lambda: False)

    async def pose():
        return {"x": -8.0, "y": -2.0, "yaw": 0.0}

    async def quality():
        return 60

    from app import robot_chassis
    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", pose)
    monkeypatch.setattr(robot_chassis, "localization_quality", quality)
    out = asyncio.run(event_host.save_spot("table_1"))
    assert out == {"ok": False, "error": "spots_unreadable", "error_th": event_host.SPOTS_UNREADABLE_TH}
    assert path.read_bytes() == raw
    rows = event_readiness._spot_rows()
    assert rows == [{"ok": False, "th": "ไฟล์จุดที่บันทึกบนหุ่น", "detail_th": event_host.SPOTS_UNREADABLE_TH}]
    # the re-learn's write is refused the same way, and said
    _relearn_mocks(monkeypatch)
    asyncio.run(event_host.relearn_dock())
    assert event_host.relearn_view()["msg_th"] == event_host.SPOTS_UNREADABLE_TH and path.read_bytes() == raw
    # a missing file is no damage: written fresh, whole
    path.unlink()
    event_host._save_recorded(lambda spots: spots.update(table_1={"pose": [1.0, 2.0, 0.0]}))
    assert json.loads(path.read_text(encoding="utf-8"))["spots"]["table_1"]["pose"] == [1.0, 2.0, 0.0]
    assert not path.with_suffix(".tmp").exists()


def test_a_save_is_flushed_to_disk_before_it_replaces_the_file():
    src = (ROOT / "app" / "event_host.py").read_text(encoding="utf-8")
    body = src[src.index("def _save_recorded("):src.index("#: The route file's dock, kept")]
    assert body.index("os.fsync(f.fileno())") < body.index("tmp.replace(path)")
    assert "raise SpotsUnreadable" in body


def test_relearn_is_a_walk_the_ceremony_app_and_a_running_walk_refuse(event, monkeypatch):
    calls = _relearn_mocks(monkeypatch)
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    assert asyncio.run(event_host.relearn_dock())["error"] == event_host.CEREMONY_ERROR
    monkeypatch.setattr(event_host, "ceremony_now", lambda: False)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    assert asyncio.run(event_host.relearn_dock())["error"] == "walking"
    assert calls["find"] == [] and calls["start"] == []


def test_the_relearn_with_the_real_walker_ends_in_its_own_callback(event, monkeypatch):
    """event_walk.start for real (a fake board_walk process that arrives): its end runs the second
    look and the write -- the callback is the walk's own `on_end`, not a guess at timing."""
    calls = {"find": [], "routes": []}
    finds = [_found((-7.00, -6.92, 171.0), set=True), _found((-7.06, -6.90, 169.4), fit=0.72, set=True)]

    async def find(dock, *, set_it, hold=None):
        calls["find"].append(set_it)
        return finds.pop(0)

    async def status():
        return "on_dock"

    async def set_pose(dock):
        return {"ok": True}

    async def spawn(route):
        calls["routes"].append(json.loads(Path(route).read_text(encoding="utf-8")))
        script = ("print('@@ {\"started\": true, \"q\": 60, \"at\": [-7.0, -6.92]}', flush=True); "
                  "print('@@ {\"step\": 1}', flush=True); print('@@ {\"docked\": 2}', flush=True); "
                  "print('@@ {\"step\": 2}', flush=True)")
        return await asyncio.create_subprocess_exec(sys.executable, "-c", script, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)

    monkeypatch.setattr(event_host, "_docking_status", status)
    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    monkeypatch.setattr(event_walk, "set_dock_pose", set_pose)
    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(settings, "event_walk_arm_rest", False)
    event_host.reset()

    async def go():
        out = await event_host.relearn_dock()
        assert out["ok"], out
        for _ in range(200):
            if event_host.RELEARN["phase"] in ("done", "failed"):
                break
            await asyncio.sleep(0.05)

    asyncio.run(go())
    steps = calls["routes"][0]["paths"]["event"]["steps"]
    assert [next(k for k in ("forward", "back_to") if k in s) for s in steps] == ["forward", "back_to"]
    assert calls["find"] == [True, True] and event_host.RELEARN["phase"] == "done", event_host.RELEARN


# ---- the staff server --------------------------------------------------------------------------------

@pytest.fixture
def client(event, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    c = TestClient(event_control_server.app)
    c.get("/event-control?key=staff-secret")
    yield c
    c.close()


def test_the_relearn_endpoint_is_a_walk_press(client, monkeypatch):
    seen = []

    async def relearn(*, stops_seen=None):
        seen.append(stops_seen)
        return {"ok": True, "walk": {"ok": True, "run": {"id": "x"}}}

    monkeypatch.setattr(event_host, "relearn_dock", relearn)
    assert client.post("/event/dock-relearn", json={}).json()["ok"] is True
    assert seen == [event_walk.stops()]                   # a red stop since the press began wins
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    assert client.post("/event/dock-relearn", json={}).json()["error"] == event_host.CEREMONY_ERROR
    assert len(seen) == 1


def test_the_morning_check_is_read_on_the_robot_in_thai(client, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")   # read, never written
    doc = client.get("/event/ready").json()
    assert set(doc) == {"go", "rows", "at", "not_here_th"}
    ths = [r["th"] for r in doc["rows"]]
    assert any(t.startswith("บทพิธีอนุมัติแล้ว") for t in ths)
    assert "ทุกจุดที่ฉาก/พิธีเดินไปมีตำแหน่ง" in ths and "วัดแท่นแล้ววันนี้" in ths and "หน้าหุ่นเชื่อมต่อ" in ths
    spots = next(r for r in doc["rows"] if r["th"] == "ทุกจุดที่ฉาก/พิธีเดินไปมีตำแหน่ง")
    assert spots["ok"] is False and "โต๊ะ 1" in spots["detail_th"]       # the tracked file: tables unrecorded
    assert doc["go"] is False
    # a dock re-learnt today turns its row; the robot's recorded spots count (no adb, its own file)
    event_host._save_recorded(lambda spots: spots.update({sid: {"pose": [-8.0, -2.0, 0.0]} for sid in (
        "stage_centre", "beside_host", "beside_globe", "table_1", "table_2", "table_3", "table_4", "table_5",
        "table_6")}), dock={"pose": [-7.04, -6.89, 171.0], "recorded_at": time.strftime("%Y-%m-%d %H:%M")})
    doc = client.get("/event/ready?fresh=1").json()
    rows = {r["th"]: r for r in doc["rows"]}
    assert rows["ทุกจุดที่ฉาก/พิธีเดินไปมีตำแหน่ง"]["ok"] is True and rows["วัดแท่นแล้ววันนี้"]["ok"] is True
    # an unreadable check is a row, never a dead page
    monkeypatch.setattr(event_readiness, "_ceremony_rows", lambda: 1 / 0)
    doc = client.get("/event/ready?fresh=1").json()
    assert any(r["th"] == "ตรวจไฟล์" and r["ok"] is False for r in doc["rows"])


def test_a_recording_that_will_not_play_is_its_own_no(monkeypatch):
    """Review 20 Nov, proved: with clip_sha None (no clip on the robot, a failed pin, another voice
    or model) the card ticked "heard" while every such line is Gemini reading."""
    from app import event_voice

    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")      # read only
    monkeypatch.setattr(event_voice, "status", lambda c, lang="th", base=None: {"recorded": False, "why": "no_clip"})
    monkeypatch.setattr(event_voice, "clip_sha", lambda c, lang="th", base=None: None)
    rows = {r["th"]: r for r in event_readiness._ceremony_rows()}
    row = rows["เสียงอัดของบทพิธีเล่นได้ครบบนหุ่น"]
    assert row["ok"] is False and "Gemini" in row["detail_th"] and "s02_l03" in row["detail_th"]
    monkeypatch.setattr(event_voice, "status", lambda c, lang="th", base=None: {"recorded": True, "why": None})
    rows = {r["th"]: r for r in event_readiness._ceremony_rows()}
    assert rows["เสียงอัดของบทพิธีเล่นได้ครบบนหุ่น"]["ok"] is True


def _on_the_dock(monkeypatch, docking="on_dock"):
    monkeypatch.setattr(event_walk, "health_view", lambda: {
        "known": True, "board": True, "battery": 90, "charging": True, "docking": docking, "q": 60,
        "lost": False, "low_battery": False})


def test_go_needs_the_lidar_at_the_boards_pose_on_the_dock(event, monkeypatch):
    """Review 20 Nov: Q >= 40 alone showed GO on a stale pose after a mid-day power cycle."""
    _on_the_dock(monkeypatch)
    looks = []

    async def find(dock, *, set_it, hold=None):
        looks.append(set_it)
        return {"ok": True, "found": {"dock_find": True, "ok": True, "board_fit": looks_fit[0]}}

    looks_fit = [0.21]
    monkeypatch.setattr(event_walk, "find_dock_pose", find)
    rows = {r["th"]: r for r in asyncio.run(event_readiness.check())["rows"]}
    assert rows[event_readiness.FIT_TH]["ok"] is False and "ตรวจอีกครั้ง" in rows[event_readiness.FIT_TH]["detail_th"]
    assert looks == []                                             # never on the background refresh
    doc = asyncio.run(event_readiness.check(fresh=True))
    rows = {r["th"]: r for r in doc["rows"]}
    assert looks == [False]                                        # a look, nothing written
    assert rows[event_readiness.FIT_TH]["ok"] is False and "0.21" in rows[event_readiness.FIT_TH]["detail_th"]
    assert doc["go"] is False
    looks_fit[0] = 0.71
    rows = {r["th"]: r for r in asyncio.run(event_readiness.check(fresh=True))["rows"]}
    assert rows[event_readiness.FIT_TH]["ok"] is True
    rows = {r["th"]: r for r in asyncio.run(event_readiness.check())["rows"]}
    assert rows[event_readiness.FIT_TH]["ok"] is True and looks == [False, False]       # kept, not re-read
    # off the dock it cannot be judged: said, not counted, and the kept answer is dropped
    _on_the_dock(monkeypatch, "not_on_dock")
    rows = {r["th"]: r for r in asyncio.run(event_readiness.check(fresh=True))["rows"]}
    assert rows[event_readiness.FIT_TH]["ok"] is None and looks == [False, False]
    _on_the_dock(monkeypatch)
    rows = {r["th"]: r for r in asyncio.run(event_readiness.check())["rows"]}
    assert rows[event_readiness.FIT_TH]["ok"] is False


def test_the_morning_check_never_writes_anything(event, tmp_path):
    before = {p: p.stat().st_mtime_ns for p in event.parent.rglob("*") if p.is_file()}
    asyncio.run(event_readiness.check(fresh=True))
    after = {p: p.stat().st_mtime_ns for p in event.parent.rglob("*") if p.is_file()}
    assert before == after


# ---- the page -----------------------------------------------------------------------------------------

def test_the_page_has_the_morning_card_and_its_two_buttons():
    assert 'id="ready"' in PAGE and "fetch('/event/ready'" in PAGE
    assert "$('relearnBtn').onclick = twoTaps('relearn'" in PAGE           # it walks: two taps
    assert "$('dockPoseBtn').onclick = twoTaps('dock'" in PAGE             # review 20 Nov: the 400 ms guard
    assert "dockArmed" not in PAGE
    assert "var held = moving || !!(walk && walk.pose_busy) || !!(rl && rl.running);" in PAGE
    assert "act('/event/dock-relearn'" in PAGE
    for page in (PAGE, CEREMONY_PAGE):
        assert "if (run && run.still_th) next.textContent = run.still_th;" in page
    server = (ROOT / "app" / "event_control_server.py").read_text(encoding="utf-8")
    assert '@app.post("/event/dock-relearn")' in server and '@app.get("/event/ready")' in server


@pytest.fixture(scope="module")
def chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- the morning card, the re-learn's two taps and the E-stop "
                    "line are NOT being checked in a browser")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:                             # noqa: BLE001
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- the morning card is NOT being checked" % exc)
    yield browser
    browser.close()
    pw.stop()


BASE = "http://event.test"


def _state(run=None, relearn=None):
    return {"ok": True, "event": {"name_th": None, "dates": ["2026-11-25"]},
            "walk": {"enabled": True, "walking": bool(run and run["state"] == "walking"), "run": run,
                     "health": {"known": True, "board": True, "battery": 90, "charging": True,
                                "docking": "on_dock", "q": 60, "lost": False, "low_battery": False}, "back": None},
            "face": {"connected": True, "event_call": True, "ceremony_call": False},
            "mode": {"ceremony": False, "ceremony_app": False, "warnings": []}, "clips": None,
            "screen": {"offered": False}, "spots": [], "net": None,
            "relearn": relearn or {"phase": None, "msg_th": None, "at": None, "recorded_at": None, "today": False},
            "scenes": [], "perform": None, "manual": None, "current": {"scene": None, "stop": 0, "since": None}}


READY = {"go": False, "at": 0, "not_here_th": "ตรวจจากในหุ่นเอง", "rows": [
    {"ok": True, "th": "หน้าหุ่นเชื่อมต่อ", "detail_th": ""},
    {"ok": False, "th": "วัดแท่นแล้ววันนี้", "detail_th": "แท่นถูกตั้งใหม่ทุกครั้งที่จัดงาน"}]}


class _Page:
    def __init__(self, browser, state, width=390):
        self.state, self.sent = state, []
        self.ctx = browser.new_context(viewport={"width": width, "height": 844}, device_scale_factor=2)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(4000)
        self.page.route(BASE + "/**", self._route)
        self.page.goto(BASE + "/event-control")
        self.page.wait_for_function("document.getElementById('lampServer').classList.contains('on')")

    def _route(self, route):
        req = route.request
        path = req.url[len(BASE):].split("?")[0]
        if path == "/event-control":
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=PAGE)
        if path == "/event/state":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(self.state))
        if path == "/event/ready":
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(READY))
        self.sent.append(path)
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps({"ok": True, "walk": {"ok": True, "run": {"name": "วัดแท่นใหม่"}}}))

    def close(self):
        self.ctx.close()


def test_the_morning_card_in_a_browser(chromium):
    p = _Page(chromium, _state())
    try:
        p.page.wait_for_function("document.getElementById('readyVerdict').textContent.indexOf('NO-GO') >= 0")
        assert p.page.locator("#ready").evaluate("d => d.open") is True       # NO-GO opens it by itself
        assert "แท่นถูกตั้งใหม่ทุกครั้ง" in p.page.locator("#readyRows").inner_text()
        btn = p.page.locator("#relearnBtn")
        btn.click()
        p.page.wait_for_timeout(150)
        assert p.sent == [] and "แตะอีกครั้ง" in btn.inner_text()          # the first tap only arms
        p.page.wait_for_timeout(450)
        btn.click()
        p.page.wait_for_timeout(300)
        assert p.sent == ["/event/dock-relearn"]
        # the re-learn's own line, from the poll
        p.state = _state(relearn={"phase": "walking", "msg_th": "ตั้งตำแหน่งบนแท่นแล้ว — กำลังออก 0.8 ม. แล้วถอยกลับ",
                                  "at": 1, "recorded_at": None, "today": False})
        p.page.wait_for_function("document.getElementById('relearnLine').textContent.indexOf('กำลังออก') >= 0",
                                 timeout=3000)
        p.page.screenshot(path=str(ROOT / ".tmp" / "event-buttons-1119" / "test_morning_card.png"), full_page=True)
    finally:
        p.close()


def test_the_e_stop_line_in_a_browser(chromium):
    run = {"id": "r1", "spot": "reception", "name": "กลางส่วนต้อนรับ", "state": "walking", "reason": None,
           "reason_th": None, "elapsed_s": 12.0, "tail": [], "kind": None, "next_th": None, "can_resume": False,
           "held": True, "done_steps": 0, "total_steps": 2, "still": True, "still_th": event_walk.STILL_TH}
    p = _Page(chromium, _state(run))
    try:
        p.page.wait_for_function("document.getElementById('walkNext').textContent.indexOf('E-stop') >= 0")
        assert p.page.locator("#walkNext").is_visible()
        assert "มีคนหรือของขวางทาง" not in p.page.locator("#walkNext").inner_text()
    finally:
        p.close()


def test_the_ceremony_pages_dock_pose_button_takes_two_deliberate_taps(chromium):
    """Review 20 Nov: the ceremony page's "ตั้งตำแหน่งที่แท่น" had its own arming with no 400 ms guard --
    a double tap set the pose. Now the page's own `secondTap` (ARM_MIN_MS) like its steps."""
    from tests.test_event_order_page import Page, _state

    assert "if (!secondTap('dock')) { render(); return; }" in CEREMONY_PAGE and "dockArmed = true" not in CEREMONY_PAGE
    lost = {"enabled": True, "run": None, "health": {"known": True, "board": True, "battery": 82, "charging": True,
                                                      "q": 31, "lost": True, "low_battery": False, "docking": "on_dock"}}
    page = Page(chromium, _state(None, walk=lost))
    try:
        btn = page.page.locator("#dockPoseBtn")
        assert btn.is_visible()
        btn.dblclick()                                     # a double tap: armed once, nothing sent
        page.page.wait_for_timeout(300)
        assert page.sent == [] and "แตะอีกครั้ง" in btn.inner_text()
        page.page.wait_for_timeout(450)
        btn.click()                                        # the deliberate second tap
        page.page.wait_for_timeout(300)
        assert page.sent == [("/event/dock-pose", {})]
    finally:
        page.close()
