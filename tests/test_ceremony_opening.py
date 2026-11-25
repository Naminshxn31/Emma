"""The ceremony's opening is card 1, as ONE step of the Emma Ceremony app (owner 23 Nov 2026: "ช่วงพิธีอยู่แอปพิธีทั้งหมด"
-- no app switch on stage; "ดับไฟก่อน ปิดม่านจากนั้นก็เอาซาวน์นับถอยหลัง 10 9 8 ขึ้นจอด้วยโดยเมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มี
ซาวน์ แล้วหุ่นก็เปิดตัว"). app/event_tour.py `begin_ceremony`, pressed through app/event_order.py.

What these hold down:
* one press: the room screen's countdown (app/ceremony_reveal.py, stubbed here), the arm rested meanwhile, the walk out
  of the box only at the hit (arms down, no second rest), then the standing pageant, the recorded line through
  event_cues.play_line, the hand down, and the tour (Khun Miki, the guests, back) -- the order's mark running to the end;
* every way it ends before the hit = no walk out, no tour, and the screen's debut taken down (`abort`): the red stop,
  the screen's own stop, the ceremony app gone, the order reset; a stop later takes it down too;
* no room screen = not ready, said; started on purpose without it only through its own flag (the page's two-tap button);
* nothing else of the order goes in the middle of it; a card that stopped short leaves its steps by hand (`opening_hand`);
* the order file names the opening once, a scene that walks out of the box.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from app import ceremony, ceremony_reveal, event_cues, event_host, event_order, event_pageant, event_script
from app import event_tour, event_walk, robot_body
from app import robot_face_link as link
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_event_order_page import chromium  # noqa: F401 -- the browser fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
CEREMONY_PAGE = (ROOT / "client" / "ceremony-control.html").read_text(encoding="utf-8")
GUESTS = {"guest_1": [-9.35, -3.4, 180.0], "guest_2": [-9.35, -4.2, 180.0], "guest_3": [-9.35, -5.0, 180.0]}
STARTED = "import json; print('@@ ' + json.dumps({'started': True, 'at': [0.0, 0.0]}), flush=True); "


def LINE(m: float, end: bool = True) -> str:
    """board_walk's `@@ forward_line` of a walk ahead (from the box, heading 0): `m` along it; `end` = the step's
    closing line (what a walk out is measured by), not the one before the wheels."""
    return ("print('@@ ' + json.dumps({'forward_line': {'from': [-7.3, -6.2, 0.0], 'to': [%r, -6.2, 0.0], "
            "'on_dock': False%s}}), flush=True); " % (round(-7.3 + m, 4), ", 'end': True" if end else ""))


ARRIVES = STARTED + "import time; time.sleep(0.1); print('[ 0.1] step 1 done', flush=True)"
#: The walk out as board_walk walks it: its line before a wheel turns, its whole forward_m, done.
OUT = STARTED + LINE(0.0, end=False) + "import time; time.sleep(0.1); " + LINE(1.0) + "print('[ 0.1] step 1 done', flush=True)"
SLOW = STARTED + LINE(0.0, end=False) + "import time; print('[ 0.1] step 1', flush=True); time.sleep(30)"
SHORT = (STARTED + LINE(0.0, end=False) + LINE(0.0) + "import sys; print('[ 0.1] STOP: step 1: something 0.05 m ahead for 45 s (0.00 of 1.00 m)', "
         "flush=True); sys.exit(2)")
#: Out of the box part of the way, then still walking (a stop cuts it there).
PART_WAY = STARTED + LINE(0.0, end=False) + "import time; time.sleep(0.1); " + LINE(0.4) + "time.sleep(30)"


def _signed(raw: dict) -> dict:
    c = event_script._normalise(raw)
    return dict(raw, approved_by="test", approved_at="2026-11-23 10:00", approved_sha256=event_script.digest(c))


SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "cues": [
    _signed({"id": "s2_greet", "scene": 2, "th": "สวัสดีค่ะคุณมิกิ ฉันชื่อเอ็มม่าค่ะ", "numbers": []}),
], "order": [
    {"n": 1, "title_th": "เปิดตัว", "steps": [
        {"do": "manual", "label_th": "ไฟห้องปิด ม่านปิด หุ่นพร้อมในกล่อง", "in_box": True},
        {"do": "opening", "scene": "debut", "label_th": "เปิดตัว (การ์ด 1)"},
    ]},
    {"n": 2, "title_th": "ทักทาย", "steps": [
        {"do": "cue", "cue": "s2_greet"},
        {"do": "body", "action": "nod"},
    ]},
]}


def _kinds(log):
    return [e[0] for e in log]


@pytest.fixture
def stage(tmp_path, monkeypatch):
    """The tracked event file (the debut walks out of the box), the guests recorded, SCRIPT as the ceremony's order, the
    ceremony app's call up, walking on, a fake board_walk per walk (`box["walkers"]`: the Nth walker's script), the arm's
    rest and the handshake answered by a fake app, the pageant's link answered, the recorded line played, the screen's
    debut stubbed (`box["impact_in"]`). `box["log"]`, in order: ("rest",) ("walk", spot, forward?) ("pageant", action)
    ("line",) ("arm", action) ("reveal", ...) ("abort", why)."""
    target = tmp_path / "floor1_event.json"
    target.write_text(TRACKED.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "floor1_ceremony.json").write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(target))
    monkeypatch.setattr(settings, "ceremony_file", str(tmp_path / "floor1_ceremony.json"))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(settings, "event_walk_arm_rest", True)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    monkeypatch.setattr(event_tour, "TICK_S", 0.05)
    event_host.reset()
    event_script.reset()
    event_cues.reset()
    event_order.reset()
    event_tour.reset()
    event_pageant.reset()
    ceremony_reveal.reset()
    robot_body.reset_state()
    spots = {g: {"pose": list(p), "recorded_at": "2026-11-24 18:01"} for g, p in GUESTS.items()}
    event_host.spots_path().write_text(json.dumps({"spots": spots}), encoding="utf-8")
    ceremony_on()
    log: list = []
    box = {"log": log, "walkers": {}, "n": 0, "routes": [], "impact_in": 0.6, "polling": True, "line": {"ok": True},
           "warnings": [],
           "ready": (True, None), "start": None, "hand_end": "done"}

    async def spawn(route):
        steps = json.loads(Path(route).read_text(encoding="utf-8"))["paths"]["event"]["steps"]
        box["n"] += 1
        box["routes"].append(steps)
        log.append(("walk", box["n"], "forward" in steps[0]))
        # the first walk is the walk out of the box: by default all of it (OUT); every leg after it a MoveTo
        default = OUT if box["n"] == 1 else ARRIVES
        return await asyncio.create_subprocess_exec(sys.executable, "-c", box["walkers"].get(box["n"], default),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    async def call_up(run, autonomous):
        return True

    async def rested():
        log.append(("rest",))
        robot_body.STATE.update(last_action="rest", state="done", reason=None, at=time.time())
        return True

    async def staff_request(action):
        log.append(("arm", action))

        async def play():
            await asyncio.sleep(0.03)
            robot_body.STATE.update(last_action=action, state="started", reason=None, at=time.time())
            await asyncio.sleep(0.1)
            robot_body.STATE.update(last_action=action, state=box["hand_end"], reason=None, at=time.time())
        asyncio.get_running_loop().create_task(play())
        return {"ok": True, "action": action, "hardware": "requested", "error": None, "error_th": None}

    async def request(session, kind, payload, timeout):
        log.append(("pageant", payload["action"]))
        return {"accepted": True, "reason": None}

    async def play_line(line_id, text, pcm, *, lang="en", scene_th=None, still=None):
        log.append(("line", line_id, lang))
        return dict(box["line"])

    async def where():
        walked = [e for e in log if e[0] == "walk"]
        return (-8.1, -6.6, 170.0) if len(walked) <= 1 else (-8.62, -4.2, 0.0)

    t0 = {"at": None, "run": None, "down": None}

    def ready():
        return box["ready"]

    async def start():
        log.append(("reveal", "start"))
        if box["start"] is not None:
            return box["start"]
        t0.update(at=time.monotonic() + box["impact_in"], run="debut-1", down=None)
        return {"ok": True, "why": None, "run": "debut-1", "impact_in_s": box["impact_in"], "length_s": 2.0,
                "zero_in_s": box["impact_in"] - 0.2, "numbers_in_s": [], "warnings": list(box["warnings"])}

    def impact_at():
        return None if t0["at"] is None or t0["down"] is not None else t0["at"]

    def cancelled_before_impact(run):
        return t0["run"] != run or (t0["down"] is not None and t0["down"] < t0["at"])

    def abort(why="aborted"):
        log.append(("abort", why))
        if t0["at"] is not None and t0["down"] is None:
            t0["down"] = time.monotonic()

    def running():
        return t0["at"] is not None and t0["down"] is None and time.monotonic() < t0["at"] + 2.0

    box["screen_stop"] = lambda: t0.update(down=time.monotonic())
    monkeypatch.setattr(event_walk, "_spawn", spawn)
    monkeypatch.setattr(event_walk, "_call_up", call_up)
    monkeypatch.setattr(event_walk, "_arm_rested", rested)
    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    monkeypatch.setattr(robot_body, "_staff_session", lambda: object())
    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    monkeypatch.setattr(event_pageant, "_gate", lambda: None)
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 0.2)
    monkeypatch.setattr(event_pageant, "TAIL_S", 0.05)
    monkeypatch.setattr(event_cues, "play_line", play_line)
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\x00\x01" * 100, None))
    monkeypatch.setattr(event_tour, "_where", where)
    monkeypatch.setattr(event_tour, "_chassis", lambda: True)
    from app import robot_chassis

    async def board_pose():
        return {"x": -7.3, "y": -6.2, "yaw": 0.0}    # the box: the walk out's floor-hazard check, the wheels still

    monkeypatch.setattr(robot_chassis, "configured", lambda: True)
    monkeypatch.setattr(robot_chassis, "pose", board_pose)
    monkeypatch.setattr(event_host, "WHEELS_POSE_GAP_S", 0.05)
    from app import event_box

    event_box.arm("ceremony_tick")                        # the order's in-box tick, as staff press it before the opening
    monkeypatch.setattr(ceremony_reveal, "warnings", lambda: list(box["warnings"]))
    for name, fn in (("ready", ready), ("start", start), ("impact_at", impact_at), ("abort", abort),
                     ("cancelled_before_impact", cancelled_before_impact), ("running", running)):
        monkeypatch.setattr(ceremony_reveal, name, fn)
    yield box
    event_tour.reset()
    event_host.reset()
    event_script.reset()
    event_cues.reset()


async def _polls(box):
    while True:
        if box["polling"]:
            event_walk.heartbeat()
        await asyncio.sleep(0.1)


async def _until(cond, timeout=15.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def _mark():
    return event_order.STATE["marks"].get(event_order._key(1, 1))


def _run(box, then=None, timeout=20.0, **press):
    """Press the opening and wait for its card's end (its mark settled); `then(box)` runs alongside."""
    async def go():
        poll = asyncio.create_task(_polls(box))
        try:
            out = await event_order.press(1, 1, **press)
            box["press"] = out
            if not out.get("ok"):
                return
            side = asyncio.create_task(then(box)) if then else None
            assert await _until(lambda: event_tour.STATE["ended"] is not None, timeout), event_tour.view()
            await event_order.snapshot()                  # settles the mark
            box["view"] = event_tour.view()
            if side is not None:
                await side
            await asyncio.sleep(0.4)                     # nothing comes after the end
            await _until(lambda: not event_walk.walking(), 5.0)
        finally:
            poll.cancel()
    asyncio.run(go())


# ---- the file ----------------------------------------------------------------------------------------------------

def test_the_real_order_opens_with_card_1_and_validates():
    doc = event_script.load(ROOT / "data" / "ceremony" / "floor1_ceremony.json")
    event = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    assert event_script.order_problems(doc, event) == []
    order = event_script.order(doc)
    # + card 2 (24 Nov, app/ceremony_follow.py): in the file, hidden from the order unless CEREMONY_FOLLOW_HOST is on
    assert [(st["do"], st.get("scene")) for st in order[0]["steps"]] == [("manual", None), ("opening", "debut"),
                                                                         ("follow", None)]
    # the ceremony's own steps it replaced are gone (they were card 1's twice over)
    steps = [st for sc in order for st in sc["steps"]]
    assert not [st for st in steps if st["do"] in ("walk", "body") and (st["spot"] or st["action"]) in
                ("stage_centre", "beside_host", "greet_arm", "handshake")]


def test_the_opening_names_a_scene_out_of_the_box_once():
    event = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    bad = json.loads(json.dumps(SCRIPT))
    bad["order"][0]["steps"][1]["scene"] = "welcome"         # a scene that does not walk out of a box
    bad["order"][1]["steps"].append({"do": "opening", "scene": "nobody"})
    bad["order"][1]["steps"].append({"do": "opening", "scene": "debut", "spot": "x"})
    problems = event_script.order_problems(bad, event)
    assert "n1.1_scene_not_out_of_the_box:welcome" in problems and "n2.2_unknown_scene:nobody" in problems
    assert "n2.3_second_opening" in problems and "n2.3_unexpected_field:spot" in problems
    assert event_script.order_problems(SCRIPT, event) == []
    # second review 23 Nov: nothing would arm its one walk out without the in-box tick before it
    untick = json.loads(json.dumps(SCRIPT))
    del untick["order"][0]["steps"][0]["in_box"]
    assert "n1.1_opening_without_in_box_tick" in event_script.order_problems(untick, event)
    twice = json.loads(json.dumps(SCRIPT))
    twice["order"][1]["steps"].append({"do": "manual", "label_th": "x", "in_box": True})
    twice["order"][1]["steps"].append({"do": "manual", "label_th": "y", "in_box": "yes"})
    problems = event_script.order_problems(twice, event)
    assert "n2.2_second_in_box_tick" in problems and "n2.3_bad_in_box" in problems


# ---- one press ---------------------------------------------------------------------------------------------------

def test_one_press_counts_down_walks_out_on_the_hit_waves_and_tours(stage):
    box = stage
    marks = {}

    async def watch(b):
        # the countdown: the arm rested, nothing walks before the hit
        assert await _until(lambda: ("rest",) in b["log"])
        assert not [e for e in b["log"] if e[0] == "walk"]
        marks["running"] = (_mark() or {}).get("state")
        assert await _until(lambda: any(e[0] == "walk" for e in b["log"]))
        marks["walked_at"] = time.monotonic()

    began = time.monotonic()
    _run(box, then=watch)
    log = box["log"]
    assert box["press"]["ok"] and box["press"]["result"]["reveal"] is True, box["press"]
    assert marks["running"] == "running"
    assert marks["walked_at"] - began >= box["impact_in"] - 0.05      # the walk out on the hit, not before
    kinds = _kinds(log)
    # the rest in the countdown, the walk out with no second rest, the pageant, the line, its end -- then the tour
    first_walk = kinds.index("walk")
    assert kinds[:first_walk] == ["reveal", "rest"], log
    assert log[first_walk] == ("walk", 1, True)                       # forward_m: the box's way out
    assert box["routes"][0] == [{"forward": 1.0, "speed": event_host.FORWARD_SPEED, "label": "เดินออกไปข้างหน้า",
                                 "short_ok": False}]
    assert ("pageant", "pageant") in log and ("line", "scene_debut", "en") in log
    assert kinds.index("pageant") < kinds.index("line") < len(kinds) - 1
    pageant_end = log.index(("pageant", "pageant_end"))
    second_walk = [k for k, e in enumerate(log) if e[0] == "walk"][1]
    assert pageant_end < second_walk and log[second_walk - 1] == ("rest",)   # each leg rests the arm itself
    assert [e[1] for e in log if e[0] == "arm"] == ["handshake"] * 4
    assert [e for e in log if e[0] == "walk"][-1][1] == 6                  # walk out, counter, 3 guests, back
    assert not [e for e in log if e[0] == "abort"]
    assert event_tour.STATE["ended"] == "done" and _mark()["state"] == "done", (event_tour.view(), _mark())
    v = box["view"]
    assert v["owner"] == "ceremony" and [s["state"] for s in v["steps"]] == ["done"] * len(v["steps"])
    assert v["steps"][0]["text_th"] == event_tour.OPENING_TH["reveal"]


def test_a_red_stop_in_the_countdown_is_no_walk_out_and_takes_the_screen_down(stage):
    box = stage
    box["impact_in"] = 3.0

    async def red(b):
        assert await _until(lambda: ("rest",) in b["log"])
        event_order.staff_stopped()                       # the red stop: the order first ...
        await event_host.stop()                           # ... then the walk, the tour

    _run(box, then=red)
    assert not [e for e in box["log"] if e[0] in ("walk", "pageant", "line", "arm")], box["log"]
    assert ("abort", "staff") in box["log"]
    assert event_tour.STATE["ended"] == "staff" and _mark()["state"] == "failed"
    v = box["view"]
    assert v["steps"][0]["state"] == "cut" and v["by_hand_th"] == [event_tour.OPENING_AGAIN_TH] and v["hand"] == []


@pytest.mark.parametrize("how", ["screen_stopped", "app_off", "order_reset", "order_stop"])
def test_anything_that_ends_it_before_the_hit_ends_it_all(stage, how, monkeypatch):
    box = stage
    box["impact_in"] = 3.0
    # the app gone and not back: past its re-dial grace (review 23 Nov), still before the hit
    monkeypatch.setattr(event_tour, "CALL_GRACE_S", 0.5)

    async def end(b):
        assert await _until(lambda: ("rest",) in b["log"])
        if how == "screen_stopped":
            b["screen_stop"]()                            # the screen's own stop (the call's end, subtitle_cut)
        elif how == "app_off":
            ceremony_off()
        elif how == "order_stop":
            event_order.staff_stopped()                   # the order's own stop count (the red stop's first act)
        else:
            event_order.reset_order()

    _run(box, then=end)
    assert not [e for e in box["log"] if e[0] in ("walk", "pageant", "line", "arm")], box["log"]
    assert event_tour.STATE["ended"] == {"screen_stopped": "reveal_cut", "app_off": "ceremony_app_off",
                                         "order_reset": "order_reset", "order_stop": "staff"}[how]
    if how != "screen_stopped":
        assert [e for e in box["log"] if e[0] == "abort"], box["log"]


def test_a_stop_during_the_walk_out_stops_it_and_the_screen(stage):
    box = stage
    box["walkers"] = {1: SLOW}

    async def red(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        await asyncio.sleep(0.2)
        event_order.staff_stopped()
        await event_host.stop()

    _run(box, then=red)
    assert [e for e in box["log"] if e[0] == "walk"] == [("walk", 1, True)]
    assert not [e for e in box["log"] if e[0] in ("pageant", "line", "arm")]
    assert ("abort", "staff") in box["log"] and event_tour.STATE["ended"] == "staff"


def test_a_box_she_cannot_leave_is_no_wave_no_line_no_tour(stage):
    box = stage
    box["walkers"] = {1: SHORT}
    _run(box)
    assert not [e for e in box["log"] if e[0] in ("pageant", "line", "arm")], box["log"]
    assert event_tour.STATE["ended"] == "debut_cut" and _mark()["state"] == "failed"
    # its walker set out (held at the door, 0.00 m): the walk out is used -- not "press again" (second review 23 Nov)
    from app import event_box

    assert not event_box.armed() and event_box.view()["left"] == "moved" and event_box.left_m() == 1.0
    assert box["view"]["by_hand_th"] == [event_box.view()["text_th"], event_tour.BOX_PANEL_TH]
    again = asyncio.run(event_order.press(1, 1))
    assert again["ok"] is False and event_box.NOT_ARMED_CEREMONY_TH in again["error_th"], again


def test_no_room_screen_is_not_ready_and_starts_only_on_purpose(stage):
    box = stage
    box["ready"] = (False, "no_screen")
    snap = asyncio.run(event_order.snapshot())
    step = snap["scenes"][0]["steps"][1]
    assert step["ready"] is False and step["opening"]["no_screen"] and step["opening"]["ready_without_screen"]
    assert ceremony_reveal.why_th("no_screen") in step["why_th"] and "เริ่มโดยไม่มีจอเปิดตัว" in step["why_th"]
    out = asyncio.run(event_order.press(1, 1))
    assert out["ok"] is False and out["error"] == "not_ready" and box["log"] == []
    # the page's own two-tap button: the same step, said on purpose -- straight out of the box, no screen
    _run(box, without_screen=True)
    assert box["press"]["ok"] and box["press"]["result"]["reveal"] is False
    assert ("reveal", "start") not in box["log"] and event_tour.STATE["ended"] == "done"
    assert box["view"]["steps"][0]["text_th"] == event_tour.OPENING_TH["no_reveal"]
    # the flag on any other step is refused
    assert asyncio.run(event_order.press(2, 0, without_screen=True))["error"] == "bad_step"


def test_a_sound_or_video_problem_is_a_warning_not_a_stop(stage):
    box = stage
    box["ready"] = (False, "sound_unusable")
    snap = asyncio.run(event_order.snapshot())
    step = snap["scenes"][0]["steps"][1]
    assert step["ready"] is True and ceremony_reveal.why_th("sound_unusable") in step["why_th"]


@pytest.mark.parametrize("code", ["small_screen_only", "not_listened", "no_sound_screen", "two_sound_screens"])
def test_a_readiness_warning_is_said_and_the_opening_runs_as_ever(stage, code):
    """ready() answers (True, code) for a warning since 23 Nov (the screen agent's fix): a code with True is no
    refusal -- the step is ready, says it, and one press runs the whole opening."""
    box = stage
    box["ready"] = (True, code)
    snap = asyncio.run(event_order.snapshot())
    step = snap["scenes"][0]["steps"][1]
    assert step["ready"] is True and ceremony_reveal.why_th(code) in step["why_th"]
    assert step["opening"]["no_screen"] is False
    _run(box)
    assert box["press"]["ok"] and box["press"]["result"]["reveal"] is True
    assert event_tour.STATE["ended"] == "done"


def test_every_screen_warning_is_said_on_the_step_not_only_the_first(stage):
    """ready() names its first warning; the step says them all (warnings()) before the press, and the reveal row the
    ones start() gave -- only small screens among them, a warning since 23 Nov, never a refusal."""
    box = stage
    box["ready"] = (True, "small_screen_only")
    box["warnings"] = ["small_screen_only", "not_listened"]
    snap = asyncio.run(event_order.snapshot())
    step = snap["scenes"][0]["steps"][1]
    assert step["ready"] is True and not step["opening"]["no_screen"]
    for w in box["warnings"]:
        assert ceremony_reveal.why_th(w) in step["why_th"], w
    assert step["why_th"].count(ceremony_reveal.why_th("small_screen_only")) == 1
    assert "small_screen_only" not in event_tour.NO_SCREEN_CODES
    _run(box)
    note = event_tour.STATE["opening"][0]["note_th"] or ""
    assert all(ceremony_reveal.why_th(w) in note for w in box["warnings"]), note
    assert event_tour.STATE["ended"] == "done"


def test_an_arm_rest_slower_than_the_build_walks_out_late_and_says_so(stage, monkeypatch):
    """The build is ~7 s since the countdown went (23 Nov): an arm rest slower than that holds the walk out past the
    hit -- and the step says how late, never "on the hit"."""
    box = stage
    box["impact_in"] = 0.4

    async def slow_rest():
        await asyncio.sleep(1.4)
        return True

    monkeypatch.setattr(event_walk, "rest_arm", slow_rest)
    _run(box)
    walk_out = event_tour.STATE["opening"][1]
    assert walk_out["key"] == "walk_out" and walk_out["state"] == "done"
    assert "ช้ากว่าเสียงกระแทก" in (walk_out["note_th"] or ""), walk_out
    assert event_tour.STATE["ended"] == "done"


def test_the_late_walk_out_counts_the_walks_own_rest_too(stage, monkeypatch):
    """6c (review 23 Nov): the build's rest failed, the walk rests the arm itself inside event_walk.start -- the delay
    after the hit is measured once the walker is out, so that rest is in it."""
    box = stage
    box["impact_in"] = 0.3

    async def failed_rest():
        return False

    async def slow_own_rest():
        box["log"].append(("rest",))
        await asyncio.sleep(1.2)
        robot_body.STATE.update(last_action="rest", state="done", reason=None, at=time.time())
        return True

    monkeypatch.setattr(event_walk, "rest_arm", failed_rest)
    monkeypatch.setattr(event_walk, "_arm_rested", slow_own_rest)
    _run(box)
    note = event_tour.STATE["opening"][1]["note_th"] or ""
    assert event_tour.REST_AGAIN_TH in note and "ช้ากว่าเสียงกระแทก" in note, note
    late = float(note.split("ช้ากว่าเสียงกระแทก ")[1].split(" ")[0])
    assert late >= 1.0, note                              # the walk's own 1.2 s rest is in it


def test_an_arm_rest_that_failed_in_the_build_is_said(stage, monkeypatch):
    box = stage

    async def failed_rest():
        return False

    monkeypatch.setattr(event_walk, "rest_arm", failed_rest)
    _run(box)
    assert event_tour.REST_AGAIN_TH in (event_tour.STATE["opening"][1]["note_th"] or "")
    assert ("rest",) in box["log"]                        # the walk rested the arm itself before its wheels


def test_nothing_else_of_the_order_goes_in_the_middle_of_it(stage, monkeypatch):
    box = stage
    box["impact_in"] = 0.3
    box["walkers"] = {2: SLOW}
    seen = {}

    async def other(b):
        assert await _until(lambda: event_tour.STATE["phase"] == "walking")
        seen["cue"] = await event_order.press(2, 0)
        seen["body"] = await event_order.press(2, 1)
        seen["again"] = await event_order.press(1, 1)
        seen["tick"] = await event_order.press(1, 0)      # the in-box tick: not while she walks (its arm is in use)
        snap = await event_order.snapshot()
        seen["ready"] = [(st["ready"], st["why_th"]) for st in snap["scenes"][1]["steps"]]
        event_order.staff_stopped()
        await event_host.stop()

    _run(box, then=other)
    assert seen["cue"]["error"] == "opening" and seen["body"]["error"] == "opening"
    assert seen["again"]["error"] == "not_ready" and event_tour.RUNNING_CEREMONY_TH in seen["again"]["error_th"]
    # the page says why, on each (not the step's own reason: that one is for after the opening)
    assert seen["tick"]["ok"] is False and seen["ready"] == [(False, event_order.ERRORS_TH["opening"])] * 2


def test_a_card_cut_at_a_guest_leaves_its_steps_by_hand(stage, monkeypatch):
    box = stage
    box["walkers"] = {4: SHORT}                           # out, counter, guest 1, then guest 2 does not arrive
    _run(box)
    v = box["view"]
    assert event_tour.STATE["ended"] == "leg_cut", event_tour.STATE["ended"]
    assert _mark()["state"] == "failed", _mark()
    assert [h["text_th"] for h in v["hand"]] == ["ไปแขก 2", "ยื่นมือจับมือ (แขก 2)", "ไปแขก 3",
                                                  "ยื่นมือจับมือ (แขก 3)", "ไปข้างคุณมิกิ"]
    went, heads = [], []

    async def go_spot(spot_id, *, stops_seen=None, head_on=False):
        went.append(spot_id)
        heads.append(head_on)
        return {"ok": True, "walk": {"ok": True, "run": {"id": "hand-run"}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    k_walk = v["hand"][0]["k"]
    out = asyncio.run(event_order.opening_hand(1, 1, k_walk))
    assert out["ok"] and went == ["guest_2"] and event_tour.owns_walk(type("R", (), {"id": "hand-run"})())
    # the card's walk to a handshake, by hand, comes the card's way in (head-on where its spot asks for it, owner 23
    # Nov); the walk back to the counter -- no handshake -- does not
    assert heads == [True] and [h["head_on"] for h in v["hand"] if h["kind"] == "walk"] == [True, True, False]
    gate = {"why": "server_disabled"}
    monkeypatch.setattr(robot_body, "staff_ready", lambda action: gate["why"])
    out = asyncio.run(event_order.opening_hand(1, 1, v["hand"][1]["k"]))
    assert out["error"] == "not_ready" and out["error_th"] == robot_body.staff_reason_th("server_disabled")
    gate["why"] = None                                    # the handshake's own gates (the server's arm keys) open
    out = asyncio.run(event_order.opening_hand(1, 1, v["hand"][1]["k"]))
    assert out["ok"] and box["log"][-1] == ("arm", "handshake"), out
    after = event_tour.view()["hand"]
    assert [h["done"] for h in after][:2] == [True, True]
    assert asyncio.run(event_order.opening_hand(1, 1, k_walk))["error"] == "no_hand"      # taken: not twice
    assert asyncio.run(event_order.opening_hand(1, 1, 99))["error"] == "no_hand"
    assert asyncio.run(event_order.opening_hand(2, 0, k_walk))["error"] == "no_step"
    ceremony_off()
    assert asyncio.run(event_order.opening_hand(1, 1, v["hand"][2]["k"]))["error"] == "not_ceremony_app"


def test_a_re_dialled_call_keeps_the_openings_walk(stage):
    box = stage
    box["walkers"] = {1: SLOW}
    kept = {}

    async def redial(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        await asyncio.sleep(0.2)
        kept["ours"] = ceremony._ceremony_walk(event_walk.current())
        event_order.staff_stopped()
        await event_host.stop()

    _run(box, then=redial)
    assert kept["ours"] is True


# ---- review 23 Nov: in the box, part of the way out, the call re-dialled, the screen gone -------------------------

def test_the_arm_is_locked_from_the_press_until_she_is_out_and_still(stage):
    from app import event_box

    box = stage
    seen = {}

    async def watch(b):
        assert await _until(lambda: ("rest",) in b["log"])
        seen["counting"] = event_box.held()
        assert await _until(lambda: event_walk.walking())
        seen["walking"] = event_box.held()
        assert await _until(lambda: ("pageant", "pageant") in b["log"])
        seen["waving"] = event_box.held()

    _run(box, then=watch)
    assert seen == {"counting": True, "walking": True, "waving": False}, seen
    assert event_tour.STATE["ended"] == "done"


def test_a_stop_part_way_out_is_neither_in_nor_out_and_never_pressed_again(stage):
    """Cut 0.4 m out of a 1.0 m walk out (review 23 Nov: the card said "still in the box, press again", and the press
    walked another whole metre from there): the card says how far, the opening is refused, the way on is the box
    panel's -- the rest of the walk only, or staff move her and let the latch go; then the wave and the rest by hand."""
    from app import event_box

    box = stage
    box["walkers"] = {1: PART_WAY}

    async def red(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        await asyncio.sleep(0.5)                          # its line at 0.4 m is out
        event_order.staff_stopped()
        await event_host.stop()

    _run(box, then=red)
    assert event_tour.STATE["ended"] == "staff"
    assert event_box.partial() and event_box.left_m() == 0.6
    v = event_tour.view()
    assert v["hand"] == [] and v["by_hand_th"] == [event_box.view()["text_th"], event_tour.BOX_PANEL_TH]
    assert event_tour.OPENING_AGAIN_TH not in json.dumps(v, ensure_ascii=False)
    again = asyncio.run(event_order.press(1, 1))
    assert again["ok"] is False and event_box.NOT_ARMED_CEREMONY_TH in again["error_th"], again
    assert asyncio.run(event_order.opening_hand(1, 1, event_tour.HAND_WAVE))["error"] == "no_hand"
    # staff moved her out and let the latch go: the wave and the card's steps by hand -- the opening still not again
    assert event_host.box_clear()["ok"]
    v = event_tour.view()
    assert [h["kind"] for h in v["hand"]][:2] == ["wave", "walk"] and v["hand"][0]["k"] == event_tour.HAND_WAVE
    again = asyncio.run(event_order.press(1, 1))
    assert again["ok"] is False and event_box.NOT_ARMED_CEREMONY_TH in again["error_th"], again

    async def wave():
        out = await event_order.opening_hand(1, 1, event_tour.HAND_WAVE)
        assert await _until(lambda: ("pageant", "pageant_end") in box["log"])
        assert await _until(lambda: event_tour.STATE["opening"][2]["state"] == "done"), event_tour.STATE["opening"]
        return out

    out = asyncio.run(wave())
    assert out["ok"], out
    kinds = [e for e in box["log"] if e[0] in ("pageant", "line")]
    assert kinds == [("pageant", "pageant"), ("line", "scene_debut", "en"), ("pageant", "pageant_end")], kinds
    assert [h["kind"] for h in event_tour.view()["hand"]][0] == "walk"     # the wave done: not offered again
    # the order begun again does NOT arm it: only the in-box tick (staff's word that she is back in the box)
    event_order.reset_order()
    assert not event_box.armed()
    assert asyncio.run(event_order.press(1, 1))["ok"] is False
    assert asyncio.run(event_order.press(1, 0))["ok"] is True and event_box.armed() and event_box.held()


def test_a_box_finish_from_the_ceremony_page_is_the_ceremonys_own_walk(stage):
    from app import event_box, event_control_server

    box = stage
    box["walkers"] = {1: PART_WAY, 2: OUT}

    async def red(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        await asyncio.sleep(0.5)
        event_order.staff_stopped()
        await event_host.stop()

    _run(box, then=red)
    assert event_box.left_m() == 0.6

    async def finish():
        resp = await event_control_server.ceremony_box_finish()
        run = event_walk.current()
        ours = ceremony._ceremony_walk(run)
        assert await _until(lambda: not event_box.held(), 8.0), event_box.STATE
        return json.loads(resp.body), ours

    out, ours = asyncio.run(finish())
    assert out["ok"] and ours is True, out
    assert box["routes"][1] == [{"forward": 0.6, "speed": event_host.FORWARD_SPEED, "label": event_host.FINISH_NAME_TH,
                                 "short_ok": False}]
    # the Event page's own drives her: refused while the ceremony app is on
    refused = json.loads(asyncio.run(event_control_server.box_finish()).body)
    assert refused["error"] == event_host.CEREMONY_ERROR


def test_a_call_that_re_dials_within_its_grace_keeps_the_opening(stage, monkeypatch):
    """Review 23 Nov: one network hiccup ended the opening mid-walk (`ceremony.on()` false for a moment). A call gone
    for less than CALL_GRACE_S is a re-dial: the walk out goes on, the wave and the line after it."""
    box = stage
    box["walkers"] = {1: STARTED + LINE(0.0, end=False) + "import time; time.sleep(1.2); " + LINE(1.0)
                      + "print('[ 0.1] step 1 done', flush=True)"}

    async def redial(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        ceremony_off("network")                           # the call drops ...
        await asyncio.sleep(0.6)
        ceremony_on()                                     # ... and is back

    _run(box, then=redial)
    assert ("pageant", "pageant") in box["log"] and ("line", "scene_debut", "en") in box["log"], box["log"]
    assert event_tour.STATE["ended"] == "done"


def test_a_walk_out_short_in_the_ceremony_is_not_out(stage):
    """board_walk's "near enough" (0.85 of 1.0 m, the step done): not out -- no wave, no line, no tour, the latch kept
    with how far she went."""
    from app import event_box

    box = stage
    box["walkers"] = {1: STARTED + LINE(0.0, end=False) + "import time; time.sleep(0.1); " + LINE(0.85)
                      + "print('[ 0.1] step 1 done', flush=True)"}
    _run(box)
    assert event_tour.STATE["ended"] == "debut_short" and "0.85 จาก 1.00" in (event_tour.STATE["detail_th"] or "")
    assert not [e for e in box["log"] if e[0] in ("pageant", "line", "arm")], box["log"]
    assert event_box.partial() and event_box.left_m() == 0.15
    assert len([e for e in box["log"] if e[0] == "walk"]) == 1


def test_a_call_gone_in_the_countdown_holds_the_walk_out_until_it_is_back(stage):
    """No walk sets out while the ceremony's call re-dials (`_call_back`): gone at the hit, the wheels wait for it."""
    box = stage
    box["impact_in"] = 0.6
    seen = {}

    async def redial(b):
        assert await _until(lambda: ("rest",) in b["log"])
        ceremony_off("network")
        await asyncio.sleep(1.4)                          # past the hit, the call still gone
        seen["walked_while_gone"] = any(e[0] == "walk" for e in b["log"])
        ceremony_on()

    _run(box, then=redial)
    assert seen["walked_while_gone"] is False
    assert ("pageant", "pageant") in box["log"] and event_tour.STATE["ended"] == "done"


def test_a_call_gone_past_its_grace_ends_the_opening(stage, monkeypatch):
    box = stage
    monkeypatch.setattr(event_tour, "CALL_GRACE_S", 0.4)
    box["walkers"] = {1: SLOW}

    async def gone(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        ceremony_off("network")                           # and stays gone

    _run(box, then=gone)
    assert event_tour.STATE["ended"] == "ceremony_app_off"
    assert not [e for e in box["log"] if e[0] in ("pageant", "line", "arm")], box["log"]


def test_another_apps_call_ends_the_opening_at_once(stage, monkeypatch):
    from types import SimpleNamespace

    from app import session as session_module

    box = stage
    box["walkers"] = {1: SLOW}
    seen = {}

    async def other(b):
        assert await _until(lambda: event_walk.walking() and any(e[0] == "walk" for e in b["log"]))
        ceremony_off("takeover")
        session_module._active = SimpleNamespace(profile="event", provider=None)   # Emma Event's call is up
        seen["at"] = time.time()

    _run(box, then=other)
    assert event_tour.STATE["ended"] == "ceremony_app_off"
    assert event_tour.STATE["ended_at"] - seen["at"] < 1.5          # at once: no re-dial grace for another app


def test_the_screens_run_gone_before_its_hit_is_no_walk_out(stage, monkeypatch):
    """Review 23 Nov: the screen socket closed and the server no longer had the run -- `impact_at()` None, not
    "cancelled" -- read as "after its hit": she walked out of the box with no countdown on the screen."""
    box = stage
    box["impact_in"] = 2.0
    monkeypatch.setattr(ceremony_reveal, "impact_at", lambda: None)
    monkeypatch.setattr(ceremony_reveal, "cancelled_before_impact", lambda run: False)
    _run(box)
    assert not [e for e in box["log"] if e[0] in ("walk", "pageant", "line", "arm")], box["log"]
    assert event_tour.STATE["ended"] == "reveal_cut"


def test_every_end_of_the_opening_brings_its_hand_down(stage, monkeypatch):
    """Review 23 Nov: only the red stop did -- the order begun again while the hand was up (its lead before the line:
    the wave's own end_after_line is never reached) left it waving to the app's cap."""
    box = stage

    async def slow_lead(seq, still):
        await asyncio.sleep(0.6)                          # the line's lead after the hand comes up

    monkeypatch.setattr(event_pageant, "hand_up", slow_lead)

    async def reset_while_waving(b):
        assert await _until(lambda: ("pageant", "pageant") in b["log"])
        event_order.reset_order()

    _run(box, then=reset_while_waving)
    assert event_tour.STATE["ended"] == "order_reset"
    assert not [e for e in box["log"] if e[0] == "line"], box["log"]      # cut before her line
    assert ("pageant", "pageant_end") in box["log"], box["log"]


def test_out_but_not_seen_still_keeps_the_arm_locked_and_ends_the_card(stage, monkeypatch):
    from app import event_box, robot_chassis

    box = stage
    monkeypatch.setattr(event_host, "WHEELS_STILL_WAIT_S", 0.4)
    crept = {"x": -7.3}

    async def creeping():
        crept["x"] += 0.05
        return {"x": crept["x"], "y": -6.2, "yaw": 0.0}

    monkeypatch.setattr(robot_chassis, "pose", creeping)
    _run(box)
    assert event_tour.STATE["ended"] == "not_still" and event_box.held()
    assert not [e for e in box["log"] if e[0] in ("pageant", "arm")], box["log"]
    assert ("line", "scene_debut", "en") in box["log"]            # cue-only: the line, with no arm
    assert len([e for e in box["log"] if e[0] == "walk"]) == 1     # no tour


def test_the_legs_wheels_rule_waits_15_s_and_says_why():
    assert event_tour.WHEELS_STILL_END_S == 15.0
    assert "15 วิ" in event_tour.WHY_TH["wheels_still"] and "E-stop" in event_tour.WHY_TH["wheels_still"]
    page = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
    assert "กด E-stop แล้วให้กดหยุดสีแดงด้วย: ปลด E-stop ภายใน 15 วิ หุ่นเดินต่อเอง" in page


def test_the_rest_asked_in_the_countdown_is_taken_only_while_it_holds(monkeypatch):
    monkeypatch.setattr(settings, "event_walk_arm_rest", True)
    asked = time.time()
    robot_body.STATE.update(last_action="rest", state="done", at=asked + 0.1)
    assert event_walk._rest_holds(asked)
    robot_body.STATE.update(last_action="speech_beat", state="done", at=asked + 0.2)   # the arm moved since
    assert not event_walk._rest_holds(asked)
    robot_body.STATE.update(last_action="rest", state="done", at=asked - 1.0)          # an older rest
    assert not event_walk._rest_holds(asked)
    assert not event_walk._rest_holds(None)
    robot_body.STATE.update(last_action="rest", state="done", at=time.time())
    assert not event_walk._rest_holds(time.time() - event_walk.REST_HOLDS_S - 5)       # too old
    monkeypatch.setattr(settings, "event_walk_arm_rest", False)
    assert not event_walk._rest_holds(asked)


# ---- the in-box tick (third review, 23 Nov) ----------------------------------------------------------------------

def test_the_in_box_tick_is_two_taps_of_its_own_never_next(stage):
    """Reset + "ถัดไป" went through the tick in one tap each and re-armed the walk out: "ถัดไป" on it is refused, and
    the step says what it arms -- ONE walk out of the opening's forward_m."""
    from app import event_box

    event_box.clear("staff")                              # out of the box (a run before)
    event_order.reset_order()
    snap = asyncio.run(event_order.snapshot())
    tick = snap["scenes"][0]["steps"][0]
    assert snap["next"] == [1, 0] and tick["in_box"] is True and tick["ready"] is True
    assert tick["box_th"] == event_box.tick_th(1.0) and "1 ครั้ง" in tick["box_th"] and "1.00 ม." in tick["box_th"]
    assert tick["note_th"].endswith(tick["box_th"])
    assert "in_box" not in snap["scenes"][0]["steps"][1] and "box_th" not in snap["scenes"][1]["steps"][0]
    out = asyncio.run(event_order.next((1, 0)))
    assert out["ok"] is False and out["error"] == "in_box_tap" and out["error_th"] == event_order.ERRORS_TH["in_box_tap"]
    assert not event_box.armed() and asyncio.run(event_order.snapshot())["next"] == [1, 0]
    assert asyncio.run(event_order.press(1, 0))["ok"] is True and event_box.armed()     # its own press: armed
    # what a refused opening says is how to arm it: the tick's own button, two taps -- never a reset, never "ถัดไป"
    assert "สองครั้ง" in event_box.NOT_ARMED_CEREMONY_TH and "'ถัดไป' ไม่ยืนยันให้" in event_box.NOT_ARMED_CEREMONY_TH


def test_the_in_box_tick_is_refused_while_she_is_partly_out(stage):
    from app import event_box

    box = stage
    box["walkers"] = {1: SHORT}                           # held at the door: set out, 0.00 of 1.00 m
    _run(box)
    assert event_box.partial() and event_box.left_m() == 1.0
    tick = asyncio.run(event_order.snapshot())["scenes"][0]["steps"][0]
    assert tick["ready"] is False and tick["why_th"] == event_box.ARM_PARTIAL_TH
    assert asyncio.run(event_order.press(1, 0))["ok"] is False
    assert not event_box.armed() and event_box.left_m() == 1.0     # the distance kept: the finish walks the rest
    assert event_host.box_clear()["ok"]                   # out by hand -- then back in, and the tick says so
    assert asyncio.run(event_order.press(1, 0))["ok"] is True and event_box.armed()


def test_the_orders_walks_say_she_is_in_the_box(stage):
    from app import event_box

    view = event_order._view({"do": "walk", "spot": "guest_1"}, asyncio.run(event_order._context()))
    assert view["ready"] is False and event_walk.REASONS_TH["in_box"] in view["why_th"]
    event_box.clear("staff")
    view = event_order._view({"do": "walk", "spot": "guest_1"}, asyncio.run(event_order._context()))
    assert event_walk.REASONS_TH["in_box"] not in (view["why_th"] or "")


# ---- the ceremony page -------------------------------------------------------------------------------------------
# Last in the module: playwright's sync API keeps its own event loop running in this thread while the `chromium`
# fixture lives -- the server's async snapshot is taken in a thread of its own here.

# (before the page tests: their browser leaves a running loop for asyncio.run)
# ---- 25 Nov 2026: the turn toward the guests before the wave (scene `wave_face`), review 02:3x ---------------------

def _with_wave_face(face=100, corner=None):
    target = Path(settings.event_file)
    doc = json.loads(target.read_text(encoding="utf-8"))
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    debut["wave_face"] = face
    if corner is not None:
        debut["wave_at_via"] = True
        debut["tour"]["via"] = [corner]
    target.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    event_host.reset()


def test_the_wave_turn_comes_between_the_walk_out_and_the_wave_and_the_card_goes_on(stage):
    box = stage
    _with_wave_face(100)
    _run(box, timeout=25.0)
    log = box["log"]
    assert box["routes"][1][0]["do"] == "turn" and box["routes"][1][0]["face"] == 100.0, box["routes"][:2]
    walks = [k for k, e in enumerate(log) if e[0] == "walk"]
    assert walks[1] < log.index(("pageant", "pageant")) < walks[2]       # out, turn, the wave, then the tour
    assert event_tour.STATE["ended"] == "done", event_tour.view()
    assert event_tour.STATE["via_heading"] is None                       # read for the first leg, once


def test_the_wave_at_the_corner_is_one_walk_out_and_on_and_the_next_leg_starts_from_it(stage, monkeypatch):
    """Owner 25 Nov ~03:10: "จุดจะให้หุ่นโยกมือทักทายท่านางงามคือให้หุ่นเดินมาที่มุมก่อนจะเลี้ยวหาคุณมิกิ", then ~04:25:
    "เดินออกจากกล่อง → ไปมุม รวมกันเลยไม่ได้หรอ จะได้ไม่ต้องรอแล้วเสียเวลา". ONE walker: the box's forward, then the glide
    to the corner with the turn to the guests at its end. The board says she stands in the box at (-8.1, -6.6) 170 deg
    (the corner (-9.37, -6.35) ahead), then ON the corner facing the guests -- a leg that kept the corner would walk to
    the point under her feet."""
    box = stage
    _with_wave_face(85, corner=[-9.37, -6.35, 78.0])

    async def where():
        n = len([e for e in box["log"] if e[0] == "walk"])
        return (-8.1, -6.6, 170.0) if n == 0 else (-9.37, -6.35, 85.0) if n == 1 else (-8.62, -4.2, 0.0)

    monkeypatch.setattr(event_tour, "_where", where)
    _run(box, timeout=25.0)
    first = box["routes"][0]
    assert "forward" in first[0] and first[1].get("glide") == [[-9.37, -6.35]] and first[1]["face"] == 85.0, first
    assert len(first) == 2                                                # no second walker, no watched turn of its own
    walks = [k for k, e in enumerate(box["log"]) if e[0] == "walk"]
    assert walks[0] < box["log"].index(("pageant", "pageant")) < walks[1]
    second = box["routes"][1]                                             # up to Khun Miki: no corner again
    pts = [tuple(p) for s in second for p in (s.get("glide") or ([s["to"]] if s.get("to") else []))]
    assert (-9.37, -6.35) not in pts, second
    assert event_tour.STATE["ended"] == "done" and not event_tour.STATE["at_corner"], event_tour.view()


def test_out_of_the_box_but_held_on_the_way_to_the_corner_waves_where_she_stands(stage, monkeypatch):
    """The one walker ends short AFTER the box's forward (someone at the corner, the board): out of the box all the same
    (its forward's closing line), the arm hers again, the wave where she stands; the next leg goes by the corner."""
    box = stage
    _with_wave_face(85, corner=[-9.37, -6.35, 78.0])

    async def where():                                   # in the box; held short on the way (still facing out); turned
        n = len([e for e in box["log"] if e[0] == "walk"])
        return ((-8.1, -6.6, 170.0), (-8.8, -6.5, 168.0), (-8.8, -6.5, 85.0))[n] if n < 3 else (-8.62, -4.2, 0.0)

    monkeypatch.setattr(event_tour, "_where", where)
    box["walkers"][1] = (STARTED + LINE(0.0, end=False) + "import time; time.sleep(0.1); " + LINE(1.0)
                         + "import sys; print('[ 5.0] STOP: step 2 ไปมุมแล้วหันหาแขก: something 0.40 m ahead for 45 s', "
                         "flush=True); sys.exit(2)")
    _run(box, timeout=25.0)
    assert len(box["routes"][0]) == 2 and ("pageant", "pageant") in box["log"], box["log"]
    from app import event_box

    assert not event_box.held(), event_box.view()                         # out: the arm is hers
    # review 04:5x: held short of the corner she still turns to the guests where she stands (the slow watched turn)
    assert [s.get("do") for s in box["routes"][1]] == ["turn"] and box["routes"][1][0]["face"] == 85.0, box["routes"][1]
    walks = [k for k, e in enumerate(box["log"]) if e[0] == "walk"]
    assert walks[1] < box["log"].index(("pageant", "pageant")) < walks[2]
    third = box["routes"][2]
    pts = [tuple(p) for s in third for p in (s.get("glide") or ([s["to"]] if s.get("to") else []))]
    assert (-9.37, -6.35) in pts, third                                   # not at the corner: the leg goes by it
    assert event_tour.STATE["ended"] == "done", event_tour.view()


def test_a_person_stopping_the_one_walk_on_the_way_to_the_corner_ends_the_card(stage):
    box = stage
    _with_wave_face(85, corner=[-9.37, -6.35, 78.0])
    box["walkers"][1] = (STARTED + LINE(0.0, end=False) + "import time; time.sleep(0.1); " + LINE(1.0)
                         + "print('[ 1.0] step 2', flush=True); time.sleep(30)")

    from app import event_box

    async def red(b):
        await _until(lambda: len(b["routes"]) >= 1)
        await asyncio.sleep(0.4)
        await event_walk.stop("staff")
        # 25 Nov ~12:00: whoever sees the stop first (the card's tick, or the walker's end), the latch goes
        await _until(lambda: not event_box.held(), 8.0)
    _run(box, then=red, timeout=25.0)
    assert ("pageant", "pageant") not in box["log"] and event_tour.STATE["ended"] not in (None, "done"), event_tour.view()

    # final review 05:5x: out of the box all of it, then stopped -- not latched in a box she has left (every later walk
    # would be refused in_box until staff cleared it)
    assert not event_box.held(), event_box.view()


def test_a_corner_that_is_not_ahead_is_the_turn_where_she_stands(stage):
    box = stage
    _with_wave_face(85, corner=[-7.0, -9.0, 78.0])                        # behind/aside of the way out: the box moved
    _run(box, timeout=25.0)
    assert [s.get("do") for s in box["routes"][1]] == ["turn"], box["routes"][1]
    assert event_tour.STATE["ended"] == "done", event_tour.view()


def test_not_still_after_the_corner_walk_is_no_arm_up(stage, monkeypatch):
    """Review 25 Nov 03:3x: the second wheels check (after the corner walk) was thrown away -- the arm went up on a robot
    not seen still. Now: no pageant arm, her line said, and the card ends `not_still` -- the walk out's own rule
    (test_out_but_not_seen_still_keeps_the_arm_locked_and_ends_the_card): staff look and go on by hand."""
    box = stage
    _with_wave_face(85, corner=[-9.37, -6.35, 78.0])
    calls = {"n": 0}

    async def wheels_still(still):
        calls["n"] += 1
        return False                                     # NOT still after the one walk (out and on to the corner)

    monkeypatch.setattr(event_host, "_wheels_still", wheels_still)
    _run(box, timeout=25.0)
    assert calls["n"] >= 1
    assert ("pageant", "pageant") not in box["log"] and ("line", "scene_debut", "en") in box["log"], box["log"]
    assert event_tour.STATE["ended"] == "not_still", event_tour.view()
    assert len([e for e in box["log"] if e[0] == "walk"]) == 1           # the one walk: no leg after


def test_no_corner_walk_without_the_tour_switch(stage, monkeypatch):
    box = stage
    _with_wave_face(85, corner=[-9.37, -6.35, 78.0])
    monkeypatch.setattr(settings, "event_tour", False)
    _run(box, timeout=25.0)
    assert not any(s.get("glide") for r in box["routes"] for s in r), box["routes"]


@pytest.mark.parametrize("error,ends", [("gate", False), ("arm_not_rest", False), ("no_call", False),
                                        ("stop_latched", True)])
def test_a_wave_turn_refused_before_it_sets_out_is_a_wave_where_she_stands(stage, monkeypatch, error, ends):
    """Review 25 Nov 02:3x: the refusal branch of `_walk` ignored `soft` -- Q or the laser's fit just out of the box
    (a turn-only route is a map route) ended the whole opening. A latch from a person's stop still ends it."""
    box = stage
    _with_wave_face(100)
    real = event_walk.start

    async def start(spot, name, steps, **kw):
        if steps and steps[0].get("do") == "turn" and name == "หันหาแขก":
            box["log"].append(("turn_refused", error))
            return {"ok": False, "error": error, "error_th": "x"}
        return await real(spot, name, steps, **kw)

    monkeypatch.setattr(event_walk, "start", start)
    _run(box, timeout=25.0)
    assert ("turn_refused", error) in box["log"]
    if ends:
        assert event_tour.STATE["ended"] not in (None, "done") and ("pageant", "pageant") not in box["log"]
    else:
        assert ("pageant", "pageant") in box["log"] and event_tour.STATE["ended"] == "done", event_tour.view()


def _order_now() -> dict:
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, event_order.snapshot()).result()


def test_the_page_shows_the_opening_and_starts_it_without_the_screen_only_on_purpose(stage, chromium):  # noqa: F811
    from tests.test_event_order_page import Page, _state

    box = stage
    box["ready"] = (False, "no_screen")
    page = Page(chromium, _state(_order_now()), html=CEREMONY_PAGE, mode="manual")
    try:
        text = page.text("#step-1-1")
        # no countdown since 23 Nov evening (the owner): the build, black, the hit
        assert "นับถอยหลัง" not in text and "จอเปิดตัว: ดนตรีขึ้น → จอมืด → เสียงกระแทก" in text
        assert "เดินออกจากกล่อง (แขนลง)" in text and "ไปจับมือแขก 3 จุด (แขก 1 → แขก 2 → แขก 3)" in text
        assert "ยังไม่พร้อม" in text and "เริ่มโดยไม่มีจอเปิดตัว" in text
        assert "หุ่นจะหมุนตัวช้าๆ อยู่กับที่" in text and "Emma ไม่พูดอะไรนอกจากประโยคเปิดตัว" in text
        assert page.page.locator("#step-1-1 button.sgo:not(.bare)").first.is_disabled()     # the press itself: not ready
        bare = "#step-1-1 button.bare"
        page.page.click(bare)
        assert page.sent == [] and "แตะอีกครั้ง" in page.text(bare)                          # two taps, always
        page.page.wait_for_timeout(450)
        page.page.click(bare)
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/ceremony/order/step", {"n": 1, "i": 1, "without_screen": True})]
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400
    finally:
        page.close()


def test_the_page_says_where_it_stopped_and_offers_what_is_left_by_hand(stage, chromium):  # noqa: F811
    from tests.test_event_order_page import Page, _state

    plan = event_tour.planned(event_host.scene("debut")["tour"], event_host.load()["spots"])["steps"]
    event_tour.STATE.update(owner="ceremony", seq=event_tour.CEREMONY_SEQ_BASE + 9, scene="debut", plan=plan, at=4,
                            phase="walking", ended="leg_cut", detail_th="มีคนหรือของขวางทาง", since=time.time(),
                            ended_at=time.time(), missing=[], hand_done=[],
                            opening=[{"key": "reveal", "state": "done", "note_th": None},
                                     {"key": "walk_out", "state": "done", "note_th": None},
                                     {"key": "wave", "state": "done", "note_th": "ไม่ได้ยกมือท่านางงาม: x"}])
    from app import event_box

    event_box.press("ceremony_opening", 1.0)              # that press walked her out, still: the arm is hers
    event_box.outcome("out")
    page = Page(chromium, _state(_order_now()), html=CEREMONY_PAGE, mode="manual")
    try:
        text = page.text("#step-1-1")
        assert "หยุดแล้ว: เดินไม่ถึงจุด" in text and "มีคนหรือของขวางทาง" in text
        assert "ไปแขก 2 ✕ หยุดตรงนี้" in text and "ไปข้างคุณมิกิ ✓" in text and "⚠ ไม่ได้ยกมือท่านางงาม: x" in text
        assert "หุ่นไม่ทำต่อเอง — ทำต่อเองได้ (แตะสองครั้ง):" in text
        hands = page.page.locator("#step-1-1 button.hand")
        assert [hands.nth(k).inner_text() for k in range(hands.count())] == [
            "ไปแขก 2", "ยื่นมือจับมือ (แขก 2)", "ไปแขก 3", "ยื่นมือจับมือ (แขก 3)", "ไปข้างคุณมิกิ"]
        hands.nth(4).click()
        assert page.sent == []
        page.page.wait_for_timeout(450)
        page.page.locator("#step-1-1 button.hand").nth(4).click()
        page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
        assert page.sent == [("/ceremony/order/opening-hand", {"n": 1, "i": 1, "k": 8})]
    finally:
        page.close()
        event_tour.reset()


def test_the_page_takes_two_taps_for_the_in_box_tick_in_both_modes(stage, chromium):  # noqa: F811
    """The tick arms ONE walk out of the box: never a stray tap -- "ถัดไป" ("ตามลำดับ") and its own button ("กดเอง") both
    take two, and both send the step's own press (the server refuses "ถัดไป" on it)."""
    from app import event_box
    from tests.test_event_order_page import Page, _state

    event_box.clear("staff")
    event_order.reset_order()
    order = _order_now()
    for mode, target, said in (("order", "#nextBtn", "#nextTop"), ("manual", "#step-1-0 button.sgo",
                                                                     "#step-1-0 button.sgo")):
        page = Page(chromium, _state(order), html=CEREMONY_PAGE, mode=mode)
        try:
            assert event_box.tick_th(1.0) in page.text("#step-1-0")
            if mode == "order":
                assert "แตะสองครั้ง — ยืนยันหุ่นอยู่ในกล่อง" in page.text("#nextTop")
            else:
                assert "(แตะสองครั้ง)" in page.text(target)
            page.page.click(target)
            page.page.wait_for_timeout(100)
            assert page.sent == [] and "แตะอีกครั้ง" in page.text(said), mode
            page.page.wait_for_timeout(450)
            page.page.click(target)
            page.page.wait_for_function("document.getElementById('toast').classList.contains('show')")
            assert page.sent == [("/ceremony/order/step", {"n": 1, "i": 0})], (mode, page.sent)
        finally:
            page.close()
