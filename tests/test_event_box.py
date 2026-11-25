"""She is in the box (app/event_box.py; reviews of 23 Nov 2026, owner: "หุ่นอยู่ในกล่องอาจจะต้องให้หุ่นเดินออกมาก่อน
จึงค่อยยกมือนะ").

What these hold down:
* the walk out is SINGLE USE, armed only by staff's word that she is in the box (the Event page's "หุ่นอยู่ในกล่องแล้ว",
  the ceremony order's in-box tick) -- and arming locks the arm. A press uses it; only "positively never moved" (no
  walker started) gives it back. Disarmed = card 1 and the opening are refused, honestly: out, partly out, not known;
* the second review's three holes (probe_r2.py): a pose jump (board_walk's `line_lost`, its only line the one before the
  wheels: "0.0 m"), out but the wheels not seen still, a restart / a staff clear after a lift-out -- each disarmed, the
  card by hand from there, never another whole metre;
* the latch: kept across a restart (an unreadable file = in the box, not armed), cleared the next morning, read only for
  the Emma Event / Ceremony apps' calls (the sales app's arm never), let go by a walk out that walked ALL of its
  forward_m (the walker's closing line) with the wheels still, or by staff;
* every arm path asks it: her talking arm, a staff/voice body request, the camera's handshake, the pageant -- the rest
  and the stop still go;
* a walk out short is not out; "เดินออกให้ครบ" walks only the measured rest; the recorded stage is never the walk out;
  board_walk's own forward is all-or-nothing for it (`short_ok`).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import event_box, event_host, event_pageant, event_tour, event_walk, robot_arm, robot_body, speech_gestures
from app.config import settings
from tests.test_debut_box import _arm_asks, _kinds, _line, _press, in_the_box  # noqa: F401 -- fixture, used by name
from tests.test_event_host import _settle, event, stage  # noqa: F401 -- fixtures, used by name
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the browser fixture, used by name
from tests.test_event_page_debut import served  # noqa: F401 -- fixture, used by name
from tests.test_event_pageant import robot  # noqa: F401 -- fixture, used by name
from tests.test_floor_sim import _block, _layout, fs
from tests.test_robot_arm_lock import Face, shell  # noqa: F401 -- fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
EVENT_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
CEREMONY_PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")
EVENT_CALL = SimpleNamespace(profile="event", provider=None)
SALES_CALL = SimpleNamespace(profile="condo", provider=None)


def _walker(*metres: float, done: bool = True) -> str:
    """A fake board_walk walking ahead from the box: its line before the wheels, its closing line at each of `metres`,
    then done (board_walk's "done" -- also what its "short but near enough" said)."""
    out = "import json, time; time.sleep(0.2); print('@@ {\"started\": true}', flush=True); " + _line(0.0, end=False)
    for m in metres:
        out += "time.sleep(0.2); " + _line(m)
    if done:
        out += "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)"
    return out


def _run(started=True, line=None, state="arrived", line_lost=False, killed=False):
    run = event_walk.Run(event_host.FORWARD_SPOT, "x")
    run.saw_started, run.state, run.forward_line, run.line_lost, run.killed = started, state, line, line_lost, killed
    return run


def _closing(m):
    return {"from": [1.0, 2.0, 0.0], "to": [1.0 + m, 2.0, 0.0], "on_dock": False, "end": True}


# ---- the arm: single use, staff's word only --------------------------------------------------------------------------

def test_arming_locks_the_arm_and_a_press_uses_it_for_good():
    assert not event_box.armed() and not event_box.held()
    event_box.arm("event_page")
    assert event_box.armed() and event_box.held()
    assert event_box.press("event_card", 1.0) is True
    assert not event_box.armed() and event_box.held()
    assert event_box.press("event_card", 1.0) is False    # used: the second press is refused
    event_box.reset()                                     # a restart while that press was under way
    assert not event_box.armed() and event_box.partial() and event_box.view()["left"] == "unknown"
    assert "ไม่แน่ใจ" in event_box.view()["text_th"]


@pytest.mark.parametrize("case, kind, armed_after, in_after", [
    ("refused before it started", "never", True, True),
    ("started, then cut at 0.40 m", "moved", False, True),
    ("started, held at the door, 0.00 m", "moved", False, True),
    ("a pose jump (line_lost)", "unknown", False, True),
    ("the walker killed", "unknown", False, True),
    ("only the line before the wheels", "unknown", False, True),
    ("all of it, wheels not yet seen", "full", False, True),
])
def test_the_arming_matrix(case, kind, armed_after, in_after):
    """Only "positively never moved" (no walker started) gives the arm back; every other end of a walk out leaves it
    used (second review 23 Nov)."""
    runs = {
        "refused before it started": _run(started=False, line=None, state="refused"),
        "started, then cut at 0.40 m": _run(line=_closing(0.4), state="stopped"),
        "started, held at the door, 0.00 m": _run(line=_closing(0.0), state="failed"),
        "a pose jump (line_lost)": _run(line={"from": [1, 2, 0], "to": [1, 2, 0], "on_dock": False}, state="failed",
                                        line_lost=True),
        "the walker killed": _run(line=_closing(0.6), state="stopped", killed=True),
        "only the line before the wheels": _run(line={"from": [1, 2, 0], "to": [1, 2, 0], "on_dock": False},
                                                state="stopped"),
        "all of it, wheels not yet seen": _run(line=_closing(1.0)),
    }
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    assert event_box.walk_ended(runs[case], 1.0, tol_m=event_host.WALKED_OUT_TOL_M) == kind
    assert event_box.armed() is armed_after and event_box.held() is in_after
    if kind == "full":
        event_box.outcome("out")                          # the wheels' word: out
        assert not event_box.held() and not event_box.armed() and event_box.out_known()


def test_a_staff_clear_disarms_and_the_order_reset_does_not_arm(served):  # noqa: F811
    event_box.arm("ceremony_tick")
    event_box.clear("staff")                              # staff lifted her out
    assert not event_box.held() and not event_box.armed() and event_box.out_known()
    from app import event_order

    event_order.reset_order()
    assert not event_box.armed()


def test_the_box_clears_itself_the_next_morning(monkeypatch):
    monkeypatch.setattr(settings, "mic_hold_reset_hour", 6)
    event_box.arm("event_page")
    until = event_box.STATE["until"]
    assert until and until > time.time() and time.localtime(until).tm_hour == 6
    assert event_box.readiness_row()["ok"] is None and "ล้างเองตอน" in event_box.readiness_row()["detail_th"]
    real = time.time
    monkeypatch.setattr(event_box.time, "time", lambda: real() + (until - real()) + 1.0)
    assert not event_box.held() and not event_box.armed() and event_box.view()["left"] is None
    event_box.reset()                                     # and so it stays on disk
    assert not event_box.held()


def test_an_unreadable_latch_file_is_in_the_box_and_not_armed():
    event_walk.RUN_DIR.mkdir(parents=True, exist_ok=True)
    event_box._path().write_text("{ half a file", encoding="utf-8")
    event_box.reset()
    assert event_box.held() and not event_box.armed() and event_box.press("event_card", 1.0) is False


def test_the_morning_card_shows_the_box(served, monkeypatch):  # noqa: F811
    from app import event_readiness

    event_box.arm("event_page")
    rows = event_readiness._live()
    row = [r for r in rows if r["th"].startswith("กล่อง")]
    assert row and row[0]["ok"] is None and "พร้อมเดินออก" in row[0]["th"]


# ---- the second review's holes (scratchpad review_final_motion/r2/probe_r2.py) -------------------------------------

def test_hole_1_a_pose_jump_is_not_known_not_zero(in_the_box):  # noqa: F811
    """board_walk printed `line_lost` instead of its closing line (a pose jump): walked_m was 0.0 from the line before
    the wheels, ignored, and card 1 could be pressed again. Now not known, disarmed, refused."""
    run = event_walk.Run("ahead", "x")
    run.forward_line = {"from": [1.0, 2.0, 90.0], "to": [1.0, 2.0, 90.0], "on_dock": False}   # the probe's 1.
    assert event_walk.walked_m(run) is None
    run.forward_line = dict(run.forward_line, end=True)
    run.line_lost = True
    assert event_walk.walked_m(run) is None
    box = in_the_box
    box["script"] = ("import json, time, sys; print('@@ {\"started\": true}', flush=True); " + _line(0.0, end=False)
                     + "time.sleep(0.3); print('@@ ' + json.dumps({'line_lost': 1}), flush=True); "
                     "print('[ 0.5] STOP: step 1: the board\\'s pose jumped', flush=True); sys.exit(2)")
    _press(box, lambda: event_box.view()["left"] == "unknown")
    assert not event_box.armed() and event_box.held() and event_box.partial() and event_box.left_m() is None
    again = asyncio.run(event_host.start_scene("debut"))
    assert again["ok"] is False and again["error"] == "not_armed" and len(box["routes"]) == 1
    assert again["error_th"] == event_box.NOT_ARMED_TH
    out = asyncio.run(event_host.box_finish())
    assert out["ok"] is False and out["error"] == "unknown"            # nothing to finish by: staff look


def test_a_walker_that_had_to_be_killed_is_not_known(in_the_box, monkeypatch):  # noqa: F811
    """board_walk not exiting on the red stop is killed after KILL_AFTER_S: no closing line of its own -- whatever line it
    printed last is not where she stopped (the second review's SIGKILL case)."""
    box = in_the_box
    box["script"] = ("import json, time; print('@@ {\"started\": true}', flush=True); " + _line(0.0, end=False)
                     + "time.sleep(0.2); " + _line(0.6) + "time.sleep(30)")
    monkeypatch.setattr(event_walk, "_signal", lambda proc: None)       # it ignores the stop
    monkeypatch.setattr(event_walk, "KILL_AFTER_S", 0.3)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: event_walk.walking() and event_walk.current().saw_started, 8.0)
        await asyncio.sleep(0.5)                          # its 0.6 line is out
        await event_walk.stop("staff")
        assert await _settle(lambda: not event_walk.walking(), 8.0)
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert event_box.view()["left"] == "unknown" and not event_box.armed() and event_box.left_m() is None


def test_hole_2_out_but_not_seen_still_is_used(in_the_box, monkeypatch):  # noqa: F811
    """All of it walked, the wheels never seen still: the latch held "in the box" and the press could be made again."""
    box = in_the_box
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.4)
    box["poses"] = [{"x": -7.3 - 0.05 * k, "y": -6.2, "yaw": 0.0} for k in range(400)]   # creeping on
    _press(box, lambda: (event_host.perform_view() or {}).get("gesture_error_th") == event_host.NOT_STILL_TH)
    assert event_box.view()["left"] == "not_still" and event_box.held() and not event_box.armed()
    again = asyncio.run(event_host.start_scene("debut"))
    assert again["ok"] is False and again["error"] == "not_armed"     # 6a: card 1's own "moved out"
    assert asyncio.run(event_host.box_finish())["error"] == "nothing_left"


def test_hole_2b_an_unreadable_pose_after_the_walk_is_used_too(in_the_box, monkeypatch):  # noqa: F811
    from app import robot_chassis

    box = in_the_box

    async def gone():
        if box.get("walked"):
            raise RuntimeError("the board does not answer")
        box["walked"] = True                              # the walk ahead's own read (its floor hazard)
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "pose", gone)
    _press(box, lambda: (event_host.perform_view() or {}).get("gesture_error_th") == event_host.NOT_STILL_TH)
    assert event_box.view()["left"] == "not_still" and not event_box.armed()
    assert asyncio.run(event_host.start_scene("debut"))["error"] == "not_armed"


def test_hole_3_a_restart_neither_re_arms_nor_forgets(served, monkeypatch):  # noqa: F811
    """The probe's 5.: a restart mid walk out (tour.json says walk_out "now"), then a staff clear: the opening was
    ready again and walked a metre from outside the box. Now: not armed, the card's steps by hand."""
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    event_box.arm("ceremony_tick")
    event_box.press("ceremony_opening", 1.0)
    event_walk.RUN_DIR.mkdir(parents=True, exist_ok=True)
    plan = event_tour.planned(event_host.scene("debut")["tour"], event_host.load()["spots"])
    (event_walk.RUN_DIR / "tour.json").write_text(json.dumps({
        "seq": 1000001, "scene": "debut", "plan": (plan or {}).get("steps") or [], "at": None, "phase": "walk_out",
        "ended": None, "owner": "ceremony",
        "opening": [{"key": "reveal", "state": "done"}, {"key": "walk_out", "state": "now"},
                    {"key": "wave", "state": "todo"}]}), encoding="utf-8")
    event_box.reset()                                     # the server's start
    event_tour.reset()
    event_tour.recover()
    assert not event_box.armed() and event_box.view()["left"] == "unknown"
    view = event_tour.ceremony_view("debut", {"reveal": (True, None), "line": "ok"})
    assert view["ready"] is False and event_box.NOT_ARMED_CEREMONY_TH in view["why_th"]
    assert asyncio.run(event_tour.begin_ceremony("debut", order={"gen": 0}))["error"] == "not_armed"
    event_box.clear("staff")                              # staff lifted her out
    v = event_tour.view()
    assert v["hand"] and v["hand"][0]["kind"] == "wave" and v["by_hand_th"], v
    assert asyncio.run(event_tour.begin_ceremony("debut", order={"gen": 0}))["error"] == "not_armed"
    event_tour.reset()


def test_hole_3b_a_refused_walk_out_lifted_out_by_staff(served):  # noqa: F811
    """A walk out refused before its walker (the arm back), then staff lift her out and clear: no arm, by hand."""
    event_box.arm("ceremony_tick")
    event_box.press("ceremony_opening", 1.0)
    event_box.never_moved()
    assert event_box.armed()
    event_tour.STATE.update(owner="ceremony", seq=event_tour.CEREMONY_SEQ_BASE + 4, scene="debut", plan=[], at=None,
                            phase="walk_out", ended="debut_refused", ended_at=time.time(), since=time.time(),
                            hand_done=[], opening=[{"key": "reveal", "state": "done", "note_th": None},
                                                   {"key": "walk_out", "state": "cut", "note_th": None},
                                                   {"key": "wave", "state": "todo", "note_th": None}])
    assert event_tour.view()["by_hand_th"] == [event_tour.OPENING_AGAIN_TH]      # still in, armed: the press again
    event_box.clear("staff")
    v = event_tour.view()
    assert [h["kind"] for h in v["hand"]] == ["wave"] and not event_box.armed()
    event_tour.reset()


def test_a_walker_refused_at_its_own_gate_gives_the_arm_back(in_the_box):  # noqa: F811
    box = in_the_box
    box["script"] = "import sys; print('[ 0.1] REFUSED: Q 31 under 40', flush=True); sys.exit(3)"
    _press(box, lambda: not event_walk.walking() and event_box.armed(), timeout=8.0)
    assert event_box.armed() and event_box.held() and event_box.view()["left"] is None


def test_the_box_arm_is_refused_while_she_walks(monkeypatch):
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    out = event_host.box_arm()
    assert out["ok"] is False and out["error"] == "walking" and not event_box.armed()


def test_the_event_pages_box_arm_is_refused_while_the_ceremony_app_is_on(monkeypatch):
    """Cross-review 24 Nov: two stray taps on the Event phone after the opening latched the arm, and every later
    ceremony walk refused until someone cleared it. The ceremony's own in-box tick still arms."""
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    out = event_host.box_arm("event_page")
    assert out["ok"] is False and out["error"] == event_host.CEREMONY_ERROR and not event_box.armed()
    assert event_host.box_arm("ceremony_tick")["ok"] is True and event_box.armed()


# ---- the latch: every arm path, the Event and Ceremony apps only ---------------------------------------------------

def test_every_arm_path_asks_the_latch_but_the_rest_and_the_stop(monkeypatch):
    monkeypatch.setattr(robot_body, "_server_gate", lambda action: True)
    monkeypatch.setattr(robot_arm, "armed", lambda: True)
    monkeypatch.setattr(robot_body, "_staff_session", lambda: EVENT_CALL)
    monkeypatch.setattr(event_pageant, "_session", lambda: EVENT_CALL)
    assert event_pageant._gate() is None
    assert speech_gestures._why_not(EVENT_CALL, "raise_right_arm") != "in_box"
    assert robot_body.staff_ready("handshake") != "in_box"
    event_box.arm("event_page")
    assert event_pageant._gate() == "in_box"
    assert speech_gestures._why_not(EVENT_CALL, "raise_right_arm") == "in_box"
    assert robot_body.staff_ready("handshake") == "in_box"
    staff = asyncio.run(robot_body.staff_request("wave"))
    assert staff["ok"] is False and staff["error"] == "in_box" and "อยู่ในกล่อง" in staff["error_th"]
    voice = asyncio.run(robot_body._request("greet_arm", EVENT_CALL))
    assert voice["ok"] is False and voice["reason"] == "in_box"
    assert robot_body.staff_ready("rest") != "in_box"
    assert asyncio.run(robot_body._request("rest", EVENT_CALL, by="staff")).get("reason") != "in_box"
    assert asyncio.run(robot_body._request("stop", EVENT_CALL)).get("reason") != "in_box"
    event_box.clear("staff")
    assert event_pageant._gate() is None and robot_body.staff_ready("handshake") != "in_box"


def test_the_sales_app_is_never_held(monkeypatch):
    """A leftover latch must not silently take sales Emma's arm (second review 23 Nov): for a call that is not the
    Emma Event / Ceremony app's the latch is not even read."""
    monkeypatch.setattr(robot_body, "_server_gate", lambda action: True)
    monkeypatch.setattr(robot_arm, "armed", lambda: True)
    monkeypatch.setattr(robot_body, "_staff_session", lambda: SALES_CALL)
    monkeypatch.setattr(event_pageant, "_session", lambda: SALES_CALL)
    event_box.arm("event_page")
    assert event_box.held()
    assert speech_gestures._why_not(SALES_CALL, "raise_right_arm") != "in_box"
    assert robot_body.staff_ready("handshake") != "in_box"
    assert asyncio.run(robot_body._request("greet_arm", SALES_CALL)).get("reason") != "in_box"
    assert event_pageant._gate() is None
    assert event_box.holds(None) is False and event_box.holds(SimpleNamespace()) is False

    def never(*a, **k):
        raise AssertionError("the latch file was read for a sales call")

    monkeypatch.setattr(event_box, "_load", never)
    assert event_box.holds(SALES_CALL) is False


def test_the_cameras_handshake_asks_the_latch_for_the_event_app_only(shell, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    face = Face(claim=False)
    face.profile = "event"
    event_box.arm("event_page")
    assert asyncio.run(robot_arm.greet(session=face)) is False and face.ws.sent == []
    sales = Face(claim=False)
    sales.profile = "condo"                               # the sales app: the latch is not its business
    assert asyncio.run(robot_arm.greet(session=sales)) is True
    event_box.clear("staff")
    assert asyncio.run(robot_arm.greet(session=face)) is True


# ---- out, or not out -------------------------------------------------------------------------------------------------

def test_out_of_the_box_all_of_it_lets_the_arm_go(in_the_box):  # noqa: F811
    box = in_the_box
    _press(box, lambda: ("pageant", "pageant_end") in _kinds(box["log"]))
    assert not event_box.held() and not event_box.armed() and event_box.out_known()
    assert asyncio.run(event_host.start_scene("debut"))["error"] == "not_armed"   # out: not again


def test_a_walk_out_short_of_its_metre_is_not_out(in_the_box):  # noqa: F811
    box = in_the_box
    box["script"] = _walker(0.85)
    _press(box, lambda: (event_host.perform_view() or {}).get("phase") == "cut")
    assert _arm_asks(box["log"]) == [] and ("say", "event_arrived") not in _kinds(box["log"]), box["log"]
    p = event_host.perform_view()
    assert p["gesture_ok"] is False and "0.85 จาก 1.00" in p["gesture_error_th"]
    assert event_box.held() and event_box.partial() and event_box.left_m() == 0.15 and not event_box.armed()
    again = asyncio.run(event_host.start_scene("debut"))
    assert again["ok"] is False and again["error"] == "not_armed" and len(box["routes"]) == 1
    snap = event_host.shared_snapshot()["box"]
    assert snap["partial"] is True and snap["left_m"] == 0.15 and snap["armed"] is False


def test_the_rest_of_a_short_walk_out_and_only_the_rest(in_the_box):  # noqa: F811
    box = in_the_box
    box["script"] = _walker(0.85)
    _press(box, lambda: (event_host.perform_view() or {}).get("phase") == "cut")
    box["script"] = _walker(0.15)

    async def finish():
        out = await event_host.box_finish()
        assert await _settle(lambda: not event_box.held(), 8.0), (out, event_box.STATE)
        return out

    out = asyncio.run(finish())
    assert out["ok"], out
    assert box["routes"][1] == [{"forward": 0.15, "speed": event_host.FORWARD_SPEED, "label": event_host.FINISH_NAME_TH,
                                 "short_ok": False}]
    assert _arm_asks(box["log"]) == [] and event_box.out_known() and not event_box.armed()


def test_a_finish_that_stops_short_again_keeps_what_it_walked(in_the_box):  # noqa: F811
    box = in_the_box
    box["script"] = _walker(0.5)
    _press(box, lambda: (event_host.perform_view() or {}).get("phase") == "cut")
    box["script"] = _walker(0.2, done=False) + "import sys; sys.exit(2)"

    async def finish():
        out = await event_host.box_finish()
        assert await _settle(lambda: not event_walk.walking(), 8.0)
        return out

    asyncio.run(finish())
    assert event_box.partial() and event_box.left_m() == 0.3


def test_the_recorded_stage_is_never_the_walk_out(in_the_box):  # noqa: F811
    box = in_the_box
    event_host.spots_path().write_text(json.dumps({"spots": {"stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}),
                                       encoding="utf-8")
    out = _press(box, lambda: ("pageant", "pageant_end") in _kinds(box["log"]))
    assert out["walk"]["run"]["spot"] == event_host.FORWARD_SPOT
    assert box["routes"][0][0]["forward"] == 1.0 and box["routes"][0][0]["short_ok"] is False
    walk = asyncio.run(event_host.debut_walk(event_host.scene("debut")))
    assert walk["spot"] == event_host.FORWARD_SPOT and walk["steps"][0]["short_ok"] is False


def test_the_event_page_never_offers_a_spot_for_the_walk_out():
    page = EVENT_PAGE
    assert "var ahead = sc.walk_out_of_box ? (sc.forward_m || null)" in page
    assert "if (sp && !sp.walkable && !sp.dock && !sc.walk_out_of_box) {" in page
    assert "ห้ามบันทึกจุด" in page and "ยังไม่ได้บันทึกจุด' + name + ' — เดินตรงออกจากกล่อง" not in page


@pytest.mark.parametrize("short_ok", [True, False])
def test_the_real_forward_is_all_or_nothing_only_for_the_walk_out(short_ok):
    """A thing ahead that holds her 0.1 m short of a metre: board_walk's "near enough" ends a walk ahead done -- the walk
    out of the box (`short_ok` False) waits on it and its patience ends it a stop. Its closing line says `end`."""
    lay = _layout(extra=_block(2.75, 2.85, 2.85, 3.15), name="gate", dock=(1.0, 3.0, 0.0))
    step = {"forward": 1.0, "speed": 0.15, "label": "เดินออกไปข้างหน้า"}
    if not short_ok:
        step["short_ok"] = False
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}, "events": True, "patience_s": 6}, [step], (1.0, 3.0, 0.0),
                      roll_off_deg=0.0)
    if short_ok:
        assert res["end"] == "done", res["reason"]
    else:
        assert res["end"] == "stop" and "ahead" in res["reason"], res["reason"]


def test_board_walks_closing_line_is_marked():
    """The real board_walk in the simulator: its line before the wheels has no `end`, its closing line has -- the
    one a walk out is measured by (event_walk.walked_m)."""
    lay = _layout(name="gate", dock=(1.0, 3.0, 0.0))
    step = {"forward": 0.5, "speed": 0.15, "label": "เดินออกไปข้างหน้า", "short_ok": False}
    res = fs.run_walk(lay, {"dock": {"pose": [1.0, 3.0, 0.0]}}, [step], (1.0, 3.0, 0.0), roll_off_deg=0.0)
    lines = [e["forward_line"] for e in res["events"] if "forward_line" in e]
    assert res["end"] == "done" and len(lines) == 2, res["events"]
    assert "end" not in lines[0] and lines[-1].get("end") is True
    run = _run(line=lines[-1])
    assert abs(event_walk.walked_m(run) - 0.5) < 0.05


# ---- the wheels, once out; the card after it -------------------------------------------------------------------------

def test_the_wheels_are_still_only_over_several_reads(monkeypatch):
    from app import robot_chassis

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(event_host, "WHEELS_POSE_GAP_S", 0.01)
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.5)

    def reads(xs):
        it = iter(xs)

        async def pose():
            return {"x": next(it, xs[-1]), "y": 0.0, "yaw": 0.0}
        return pose

    creep = [0.0, 0.005] + [0.05 * k for k in range(1, 200)]
    monkeypatch.setattr(robot_chassis, "pose", reads(creep))
    assert asyncio.run(event_host._wheels_still(lambda: True)) is False
    monkeypatch.setattr(robot_chassis, "pose", reads([0.10, 0.104, 0.098, 0.103, 0.101]))
    assert asyncio.run(event_host._wheels_still(lambda: True)) is True
    monkeypatch.setattr(robot_chassis, "pose", reads([0.01 * k for k in range(200)]))
    assert asyncio.run(event_host._wheels_still(lambda: True)) is False


#: The board's pose at the end of 23 Nov 22:42's walk out of the box, read ~0.3 s apart on the robot (x, y, yaw deg):
#: the walker's creep to its end (10-21 mm a read) and the stop -- then the pose frozen (yaw within 0.02 deg).
TONIGHT_22_42 = [(-7.8401, -6.6881, 166.97), (-7.8362, -6.7011, 167.16), (-7.8454, -6.6990, 167.10),
                 (-7.8601, -6.6956, 167.10), (-7.8732, -6.6926, 167.15), (-7.8849, -6.6900, 167.15),
                 (-7.8971, -6.6872, 167.15), (-7.9107, -6.6841, 167.14), (-7.9215, -6.6816, 167.09),
                 (-7.9428, -6.6821, 167.04), (-7.9530, -6.6798, 167.04)]
TONIGHT_FROZEN = [(-7.9530, -6.6798, 167.04), (-7.9530, -6.6798, 167.02), (-7.9530, -6.6798, 167.04)] * 4


def test_the_wheels_are_still_three_reads_after_the_stop_and_never_on_a_creep(monkeypatch):
    """Owner 23 Nov, watching the opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม" -- 2 s of it was
    this check (0.4 s apart, 3 windows, 2 cm / 1 deg). The board's pose freezes when the wheels stop, so 2 windows
    0.25 s apart within 5 mm / 0.3 deg: tonight's real end of a walk out is still on the third read after the stop
    from wherever on its creep the check began, and never before it. A creep the old rule let through (3 mm a read
    = 12 mm/s; 0.2 deg a read) is not still."""
    from app import robot_chassis

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(event_host, "WHEELS_POSE_GAP_S", 0.0)
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.5)

    def reads(rows, taken):
        it, last = iter(rows), []

        async def pose():
            last[:] = [next(it, last[0] if last else None)]     # past the list: its last pose, held
            x, y, yaw = last[0]
            taken.append((x, y, yaw))
            return {"x": x, "y": y, "yaw": math.radians(yaw)}
        return pose

    stop = len(TONIGHT_22_42) - 1                        # the read that found the pose at its frozen place
    for start in range(stop + 1):
        taken: list = []
        monkeypatch.setattr(robot_chassis, "pose", reads(TONIGHT_22_42[start:] + TONIGHT_FROZEN, taken))
        assert asyncio.run(event_host._wheels_still(lambda: True)) is True
        assert len(taken) == (stop - start) + 3, (start, len(taken))   # the stop's read and two more
    creep = ((0.003 * k, 0.0, 0.0) for k in itertools.count())       # never ends: False at WHEELS_STILL_WAIT_S
    monkeypatch.setattr(robot_chassis, "pose", reads(creep, []))
    assert asyncio.run(event_host._wheels_still(lambda: True)) is False
    turn = ((0.0, 0.0, 0.2 * k) for k in itertools.count())
    monkeypatch.setattr(robot_chassis, "pose", reads(turn, []))
    assert asyncio.run(event_host._wheels_still(lambda: True)) is False


def test_card_1_out_but_not_seen_still_ends_its_tour_with_the_arm_locked(in_the_box, monkeypatch):  # noqa: F811
    box = in_the_box
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.4)
    box["poses"] = [{"x": -7.3 - 0.05 * k, "y": -6.2, "yaw": 0.0} for k in range(400)]   # creeping on
    _press(box, lambda: event_tour.STATE["ended"] is not None)
    assert event_tour.STATE["ended"] == "not_still" and event_box.held()
    assert [e for e in box["log"] if e == ("wheels", "walker")] == [("wheels", "walker")]   # no walk to the counter


def test_the_wave_by_hand_is_refused_in_the_box_and_while_she_walks(served, monkeypatch):  # noqa: F811
    event_tour.STATE.update(owner="ceremony", seq=event_tour.CEREMONY_SEQ_BASE + 3, scene="debut", plan=[], at=None,
                            phase="waving", ended="staff", ended_at=0.0, since=0.0, hand_done=[],
                            opening=[{"key": "reveal", "state": "done", "note_th": None},
                                     {"key": "walk_out", "state": "done", "note_th": None},
                                     {"key": "wave", "state": "cut", "note_th": None}])
    event_box.arm("ceremony_tick")
    event_box.press("ceremony_opening", 1.0)
    event_box.outcome("not_still", 1.0)                  # out all of it, the wheels never seen still
    out = asyncio.run(event_tour.wave_by_hand(still=lambda: True))
    assert out["ok"] is False and out["error"] == "in_box"
    event_box.clear("staff")
    assert [h["kind"] for h in event_tour.view()["hand"]] == ["wave"]
    monkeypatch.setattr(event_walk, "walking", lambda: True)         # 6b: a walk by hand still under way
    out = asyncio.run(event_tour.wave_by_hand(still=lambda: True))
    assert out["ok"] is False and out["error"] == "moving"
    event_tour.reset()


def test_card_1s_steps_by_hand_from_the_event_page(in_the_box, monkeypatch):  # noqa: F811
    """Card 1 cut out of the box, staff clear the latch: its steps are the Event page's own two-tap buttons."""
    box = in_the_box
    monkeypatch.setattr(settings, "event_tour", True)
    box["script"] = _walker(0.5)
    _press(box, lambda: event_tour.STATE["ended"] is not None)
    assert event_tour.STATE["ended"] == "debut_short"
    assert event_tour.view()["hand"] == []                # partly out: the box panel's, not the card's
    assert event_host.box_clear()["ok"]
    hand = event_tour.view()["hand"]
    assert hand and hand[0]["kind"] == "wave", hand
    went = []

    async def go_spot(spot_id, *, stops_seen=None, head_on=False):
        went.append(spot_id)
        return {"ok": True, "walk": {"ok": True, "run": {"id": "h1"}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    walk = next(h for h in hand if h["kind"] == "walk")
    out = asyncio.run(event_tour.event_hand(walk["k"], stops_seen=event_walk.stops()))
    assert out["ok"] and went == [walk["spot"]]
    assert asyncio.run(event_tour.event_hand(walk["k"], stops_seen=event_walk.stops()))["error"] == "no_hand"
    event_tour.reset()


# ---- the third review (23 Nov): arming half out, every other walk while in the box, a press that raised -------------

@pytest.mark.parametrize("left", ["moved", "unknown", "not_still"])
def test_staff_cannot_arm_while_she_is_partly_out(left):
    """Arming blanked the distance walked, and the next press walked another whole forward_m from half out. Out first
    (the rest of it, or by hand and a clear), then in again."""
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    {"moved": lambda: event_box.outcome("moved", 0.4), "unknown": lambda: event_box.outcome("unknown"),
     "not_still": lambda: event_box.outcome("not_still", 1.0)}[left]()
    before = dict(event_box.STATE)
    out = event_host.box_arm()
    assert out == {"ok": False, "error": "partial", "error_th": event_box.ARM_PARTIAL_TH}
    assert dict(event_box.STATE) == before and event_box.partial() and not event_box.armed()
    assert event_box.press("event_card", 1.0) is False
    if left == "moved":
        assert event_box.left_m() == 0.6                  # "เดินออกให้ครบ" still walks only the rest
    event_box.clear("staff")                              # out by hand -- then back in, and staff say so
    assert event_host.box_arm()["ok"] is True and event_box.armed()


def test_staff_cannot_arm_over_a_press_under_way():
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    out = event_host.box_arm()
    assert out["ok"] is False and out["error"] == "walking" and event_box.view()["left"] == "pending"
    assert not event_box.armed()


#: Every walk that is not the walk out of the box (board_walk's step shapes).
OTHER_WALKS = {
    "a spot: undock and MoveTo": [{"do": "undock"}, {"to": [-9.0, -3.0], "speed": 0.3, "label": "x"}],
    "a MoveTo": [{"to": [-9.0, -3.0], "speed": 0.3}],
    "home: the dock": [{"do": "dock"}],
    "home: back along the line": [{"back_to": [0.0, 0.0, 0.0], "label": "ถอยกลับแท่น"}],
    "a watched turn": [{"do": "turn", "face": 90.0}],
    "a walk ahead of a scene with no box": [{"forward": 1.0, "speed": 0.2, "label": "x"}],
    "a walk ahead, near enough is done": [{"forward": 1.0, "speed": 0.2, "short_ok": True}],
    "a walk out resumed (undock first)": [{"do": "undock"}, {"forward": 1.0, "speed": 0.2, "short_ok": False}],
    "a walk out, then a MoveTo": [{"forward": 1.0, "short_ok": False}, {"to": [-9.0, -3.0]}],
}
WALK_OUT = [{"forward": 1.0, "speed": event_host.FORWARD_SPEED, "label": "เดินออกไปข้างหน้า", "short_ok": False}]


@pytest.mark.parametrize("name", sorted(OTHER_WALKS))
def test_in_the_box_every_other_walk_is_refused(name, monkeypatch):
    """A MoveTo, a dock, a turn, a back_to from inside the box drives into its walls: while the latch holds only the
    walk out of it (and its finish) may move her -- for every caller of event_walk.start."""
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(event_walk, "_faces", lambda: False)   # past the box, the next gate refuses: nothing spawns
    event_box.arm("event_page")
    out = asyncio.run(event_walk.start("spot", "x", OTHER_WALKS[name], dock={"pose": [0.0, 0.0, 0.0]}))
    assert out == {"ok": False, "error": "in_box", "error_th": event_walk.REASONS_TH["in_box"]}
    assert not event_walk.walking()
    assert asyncio.run(event_walk.start("ahead", "x", WALK_OUT, dock=None))["error"] == "no_face"   # the walk out goes
    event_box.clear("staff")
    assert asyncio.run(event_walk.start("spot", "x", OTHER_WALKS[name], dock={"pose": [0.0, 0.0, 0.0]}))["error"] \
        == "no_face"                                      # out: walks as before


def test_in_the_box_cards_spots_home_and_resume_do_not_move_her(in_the_box):  # noqa: F811
    box = in_the_box
    spots = {"beside_host": {"pose": [-8.8, -4.6, 0.0]}, "stage_centre": {"pose": [-9.0, -3.0, 0.0]}}
    event_host.spots_path().write_text(json.dumps({"spots": spots}), encoding="utf-8")
    event_host.reset()
    card = asyncio.run(event_host.start_scene("counter_handshake"))       # card 2
    assert card["walk"]["ok"] is False and card["walk"]["error"] == "in_box", card
    for out in (asyncio.run(event_host.go_spot("stage_centre")), asyncio.run(event_host.go_home())):
        assert out["ok"] is False and out["error"] == "in_box", out
    run = event_walk.Run("stage_centre", "x")
    run.steps, run.done, run.state, run.reason = [{"do": "undock"}, {"to": [-9.0, -3.0]}], 1, "stopped", "staff"
    event_walk.STATE["run"] = run
    assert asyncio.run(event_walk.resume_last())["error"] == "in_box"
    assert box["routes"] == [] and ("wheels", "walker") not in box["log"]
    assert event_box.armed()                              # nothing of it used the walk out


def test_a_card_1_press_that_raises_before_its_walk_gives_the_arm_back(in_the_box, monkeypatch):  # noqa: F811
    """Raised between the press (the arm used) and the walk: the press stayed "pending" for good -- the arm gone and
    staff's word refused. No walker started = never moved."""
    from app import event_clips

    box = in_the_box

    async def boom(*a, **k):
        raise RuntimeError("the film would not stop")

    monkeypatch.setattr(event_clips, "stop", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(event_host.start_scene("debut"))
    assert event_box.armed() and event_box.view()["left"] is None and box["routes"] == []


def test_the_box_expires_by_the_mic_holds_own_clock(monkeypatch):
    """06:00 by the same clock as the mic hold (wake._next_reset; the robot's launcher runs the server with TZ=ICT-7,
    Thai time -- not Android's China clock): one function, never a second one."""
    from app import wake

    asked = []
    monkeypatch.setattr(wake, "_next_reset", lambda now: asked.append(now) or now + 123.0)
    event_box.arm("event_page")
    assert asked and event_box.STATE["until"] == asked[-1] + 123.0
    launcher = (ROOT / "scripts" / "robot_server_setup.py").read_text(encoding="utf-8")
    assert "export TZ=ICT-7" in launcher


# ---- both staff pages (last in the module: the browser fixture keeps playwright's own loop in this thread) ---------

def test_the_event_page_arms_shows_the_box_and_both_ways_out_by_two_taps(chromium, served):  # noqa: F811
    from tests.test_event_page_debut import _state

    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        # nothing said yet: the arm on offer, card 1 says to press it first
        assert page.visible("#boxCard") and page.visible("#boxArm") and not page.visible("#boxClear")
        assert "หุ่นอยู่ในกล่องแล้ว" in page.text("#scene-debut")
        page.page.click("#boxArm")
        assert page.sent == [] and "แตะอีกครั้ง" in page.text("#boxArm")
        page.page.wait_for_timeout(450)
        page.page.click("#boxArm")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/event/box-arm", {})]
        page.clear_toast()
        event_box.arm("event_page")
        event_box.press("event_card", 1.0)
        event_box.outcome("moved", 0.4)
        page.redraw(_state())
        page.page.wait_for_function("document.getElementById('boxNow').textContent.indexOf('0.40 จาก 1.00') >= 0")
        # partly out: "she is in the box" is not offered next to the finish (third review 23 Nov: out first)
        assert page.text("#boxFinish") == "เดินออกให้ครบ (อีก 0.60 ม.)" and not page.visible("#boxArm")
        card = page.text("#scene-debut")
        assert "ออกจากกล่องไปบางส่วน" in card and 'กด "หุ่นอยู่ในกล่องแล้ว"' not in card
        page.page.click("#boxFinish")
        page.page.wait_for_timeout(450)
        page.page.click("#boxFinish")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent[-1] == ("/event/box-finish", {})
        page.clear_toast()
        page.page.click("#boxClear")
        page.page.wait_for_timeout(450)
        page.page.click("#boxClear")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent[-1] == ("/event/box-clear", {})
    finally:
        page.close()


def test_the_ceremony_page_shows_the_box_with_its_own_finish(chromium):  # noqa: F811
    from tests.test_event_order_page import _state

    state = _state(None)
    state["box"] = {"in": True, "armed": False, "partial": True, "walked_m": 0.4, "of_m": 1.0, "left_m": 0.6,
                    "unknown": False, "left": "moved", "text_th": "หุ่นเริ่มเดินออกแล้วหยุด (ได้ 0.40 จาก 1.00 ม.)",
                    "clear_th": event_box.CLEAR_TH}
    page = Page(chromium, state, html=CEREMONY_PAGE, mode="manual")
    try:
        assert page.visible("#boxCard") and page.text("#boxFinish") == "เดินออกให้ครบ (อีก 0.60 ม.)"
        assert page.text("#boxClear") == event_box.CLEAR_TH
        page.page.click("#boxFinish")
        assert page.sent == [] and "แตะอีกครั้ง" in page.text("#boxFinish")
        page.page.wait_for_timeout(450)
        page.page.click("#boxFinish")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/ceremony/box-finish", {})]
        page.clear_toast()
        page.page.click("#boxClear")
        page.page.wait_for_timeout(450)
        page.page.click("#boxClear")
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent[-1] == ("/event/box-clear", {})
        state = dict(state, box=dict(state["box"], unknown=True, left_m=None, left="unknown"))
        page.redraw(state)
        page.page.wait_for_function("document.getElementById('boxFinish').hidden === true")
        assert page.visible("#boxClear")
        page.redraw(dict(state, box={"in": False, "partial": False, "armed": False}))
        page.page.wait_for_function("document.getElementById('boxCard').hidden === true")
    finally:
        page.close()


def test_the_event_page_offers_card_1s_steps_by_hand(chromium, served):  # noqa: F811
    from tests.test_event_page_debut import _state

    state = _state()
    state["tour"] = {"scene": "debut", "ended": "debut_short", "ok": False, "why_th": "เดินออกจากกล่องไม่ครบ",
                     "detail_th": None, "now_th": None, "steps": [{"text_th": "เปิดตัว", "state": "cut"}],
                     "missing": [], "owner": "event", "by_hand_th": ["ยกมือโบก + พูด", "ไปหน้าเคาน์เตอร์"],
                     "hand": [{"k": event_tour.HAND_WAVE, "kind": "wave", "spot": None, "text_th": "ยกมือโบก + พูด",
                               "done": False},
                              {"k": 0, "kind": "walk", "spot": "beside_host", "text_th": "ไปหน้าเคาน์เตอร์", "done": False}],
                     "ago_s": 3}
    page = Page(chromium, state, html=EVENT_PAGE, path="/event-control")
    try:
        btn = page.page.locator("#scene-tour-debut button", has_text="ไปหน้าเคาน์เตอร์")
        btn.click()
        assert page.sent == []
        page.page.wait_for_timeout(450)
        page.page.locator("#scene-tour-debut button", has_text="ไปหน้าเคาน์เตอร์").click()
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/event/tour-hand", {"k": 0})]
    finally:
        page.close()


def test_a_walk_out_that_is_the_walkers_first_step_is_judged_by_its_own_line(monkeypatch):
    """Owner 25 Nov ~04:25: the walk out and the corner in one walker. Its forward's closing line says out, whatever the
    glide after it did -- `first_step`; without it a walker that did not arrive is "moved" (as before)."""
    import types

    monkeypatch.setattr(event_walk, "walked_m", lambda run: 1.0)
    run = types.SimpleNamespace(saw_started=True, state="failed")
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    assert event_box.walk_ended(run, 1.0, tol_m=0.05) == "moved"
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    assert event_box.walk_ended(run, 1.0, tol_m=0.05, first_step=True) == "full"
    monkeypatch.setattr(event_walk, "walked_m", lambda run: 0.6)
    event_box.arm("event_page")
    event_box.press("event_card", 1.0)
    assert event_box.walk_ended(run, 1.0, tol_m=0.05, first_step=True) == "moved"


def test_in_the_box_only_the_walk_out_and_steps_marked_after_it_may_walk():
    """25 Nov: card 1's walk out and on to the corner in one walker. While the latch holds: the box's forward steps,
    then only steps marked `after_box` (the walker runs them once that forward is done); anything else, or an
    `after_box` step with no walk out before it, waits for staff's "out of the box"."""
    out = {"forward": 1.3, "short_ok": False}
    glide = {"glide": [[-9.37, -6.35]], "face": 85.0}
    event_box.arm("event_page")
    assert event_box.held()
    assert not event_walk._boxed([out])
    assert not event_walk._boxed([out, dict(glide, after_box=True)])
    assert event_walk._boxed([out, glide])                               # not marked
    assert event_walk._boxed([dict(glide, after_box=True)])               # no walk out before it
    assert event_walk._boxed([out, dict(glide, after_box=True), {"do": "dock"}])
    assert event_walk._boxed([{"forward": 1.3}, dict(glide, after_box=True)])   # a forward that may stop short
    event_box.clear("test")
    assert not event_walk._boxed([out, glide])                           # out of the box: the latch is not asked

