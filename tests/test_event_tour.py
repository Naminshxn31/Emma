"""Card 1's whole opening in one press (owner 23 Nov 2026) -- app/event_tour.py.

"การ์ดแรก ให้หุ่นเปิดตัวตามที่ซ้อมไว้แล้วก็พูดทักทายกับท่านางงาม จากนั้นไปหน้าเคาน์เตอร์แล้วจับมือมิกิ แล้วหันหลัง ไปจับมือกับ
แขกสักสามจุดใกล้ๆตรงนั้น แล้วก็กลับมา ไม่ต้องให้หุ่นขยับหัวนะ แล้วก็กลับมาหน้าเคาน์เตอร์เพื่อคุยกับคุณมิกิ"

What these hold down:
* one press: the debut as before, then the counter + its handshake, each guest spot + its handshake, back to the
  counter -- in that order, each handshake waited out to the app's own `done` before the next walk, nothing said at
  a handshake, a watched slow turn first for a point behind her;
* every way it ends ends ALL of it -- no walk and no handshake after: the red stop, a voice stop, the desk's, the
  page quiet, the face gone, B, the ceremony app, another press, a walk to a spot, a leg that does not arrive, a
  handshake refused / failed / stopped / never answered; nothing goes on by itself (a restart says where it was cut);
* a guest spot not recorded = no guest round (she stays at the counter), said on the page before the press;
* the head still throughout: head_follow sends no look (walk scan, face, look down, a hint, recentre), the camera's
  greetings wait, her talking arm, a voice's gesture or handshake and the card's buttons are refused.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from pathlib import Path

import pytest

from app import event_host, event_tour, event_walk, greeter, robot_body
from app.config import settings
from tests.test_event_host import event, stage  # noqa: F401 -- fixtures, used by name
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the browser fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
EVENT_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")
GUESTS = {"guest_1": [-9.35, -3.4, 180.0], "guest_2": [-9.35, -4.2, 180.0], "guest_3": [-9.35, -5.0, 180.0]}
STARTED = "import json; print('@@ ' + json.dumps({'started': True, 'at': [0.0, 0.0]}), flush=True); "
ARRIVES = STARTED + "print('[ 0.1] step 1 done', flush=True)"
SLOW = STARTED + "import time; print('[ 0.1] step 1', flush=True); time.sleep(30)"
SHORT = (STARTED + "import sys; print('[ 0.1] STOP: step 1: ended 0.90 m from the point (the board gave up on it)', "
         "flush=True); sys.exit(2)")
TOUR_SPOTS = ("beside_host", "guest_1", "guest_2", "guest_3")


def _record(guests=GUESTS):
    """The stage and the guests recorded at the robot (the robot's spots file beside the event file)."""
    spots = {"stage_centre": {"pose": [-9.0, -3.0, 0.0], "recorded_at": "2026-11-24 18:00"}}
    spots.update({g: {"pose": list(p), "recorded_at": "2026-11-24 18:01"} for g, p in guests.items()})
    event_host.spots_path().write_text(json.dumps({"spots": spots}), encoding="utf-8")


@pytest.fixture
def tour(event, stage, monkeypatch):  # noqa: F811
    """EVENT_TOUR on, a fake board_walk per walk (`box["walkers"]`: the Nth walk's script, else ARRIVES), a fake arm
    whose handshake reports started, then `box["end"]` after `box["hold"]` s, the page polling, a call up.
    `box["log"]`, in order: ("walk", spot, step kinds) / ("arm", action) / ("arm_end", state);
    `box["stage"]["log"]`: what reached the model."""
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(settings, "event_walk_enabled", True)
    monkeypatch.setattr(event_walk, "_faces", lambda: True)
    monkeypatch.setattr(event_walk, "_other_walker", lambda: False)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    monkeypatch.setattr(event_walk, "FACE_GRACE_S", 0.2)
    monkeypatch.setattr(event_host, "ceremony_now", lambda: False)
    monkeypatch.setattr(event_tour, "TICK_S", 0.05)
    log: list = []
    box = {"log": log, "walkers": {}, "n": 0, "end": "done", "hold": 0.15, "reason": None, "silent": False,
           "arm_answer": None, "polling": True, "stage": stage, "routes": []}

    async def spawn(route):
        steps = json.loads(Path(route).read_text(encoding="utf-8"))["paths"]["event"]["steps"]
        box["n"] += 1
        box["routes"].append(steps)
        return await asyncio.create_subprocess_exec(sys.executable, "-c", box["walkers"].get(box["n"], ARRIVES),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    monkeypatch.setattr(event_walk, "_spawn", spawn)
    real_start = event_walk.start

    async def start(spot, name, steps, **kw):
        log.append(("walk", spot, [s.get("do") or ("to" if s.get("to") else "other") for s in steps or []]))
        return await real_start(spot, name, steps, **kw)

    monkeypatch.setattr(event_walk, "start", start)

    async def staff_request(action):
        log.append(("arm", action))
        if box["arm_answer"] is not None and action == "handshake":
            return box["arm_answer"]
        if action == "handshake" and not box["silent"]:
            async def play():
                await asyncio.sleep(0.03)
                robot_body.STATE.update(last_action="handshake", state="started", reason=None, at=time.time())
                await asyncio.sleep(box["hold"])
                if box["end"] is None:
                    return                                   # never answers its end
                log.append(("arm_end", box["end"]))
                robot_body.STATE.update(last_action="handshake", state=box["end"], reason=box["reason"], at=time.time())
            asyncio.get_running_loop().create_task(play())
        return {"ok": True, "action": action, "hardware": "requested", "error": None, "error_th": None}

    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    monkeypatch.setattr(robot_body, "_staff_session", lambda: object())
    robot_body.reset_state()
    _record()
    event_host.reset()
    return box


async def _polls(box):
    """The staff page polling /event/state while `box["polling"]`."""
    while True:
        if box["polling"]:
            event_walk.heartbeat()
        await asyncio.sleep(0.1)


async def _until(cond, timeout=20.0):
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def _walks(log):
    return [e[1] for e in log if e[0] == "walk"]


def _handshakes(log):
    return [e for e in log if e[0] == "arm" and e[1] == "handshake"]


def _run(box, then=None, timeout=25.0, where=None):
    """Press card 1 and wait for the tour's end (its view kept in box["view"]); `then(box)` runs alongside (a stop
    at the right moment). `where`: the board's pose for each leg's builder -- (x, y, heading) of the spot the last
    walk went to."""
    async def go():
        poll = asyncio.create_task(_polls(box))
        try:
            out = await event_host.start_scene("debut")
            assert out["ok"] and out["walk"]["ok"], out
            side = asyncio.create_task(then(box)) if then else None
            assert await _until(lambda: event_tour.STATE["ended"] is not None, timeout), event_tour.view()
            box["view"] = event_tour.view()
            if side is not None:
                await side
            await asyncio.sleep(0.6)                     # nothing comes after the end
            await _until(lambda: not event_walk.walking(), 5.0)
        finally:
            poll.cancel()
            event_tour.reset()
            await asyncio.sleep(0)
    asyncio.run(go())


def _where_from_the_last_walk(monkeypatch, box):
    """The board's pose at each leg: where the walk before it went (its spot's pose or last point)."""
    def pose_of(spot_id):
        sp = event_host.load()["spots"][spot_id]
        if sp.get("pose"):
            p = sp["pose"]
            return (p[0], p[1], p[2] if p[2] is not None else 0.0)
        last = [s for s in sp["steps"] if s.get("to")][-1]
        return (last["to"][0], last["to"][1], float(last.get("face") or 0.0))

    async def where():
        walked = [e[1] for e in box["log"] if e[0] == "walk"]
        return pose_of(walked[-1]) if walked else (-7.1, -6.84, 168.0)     # the dock, before the debut

    monkeypatch.setattr(event_tour, "_where", where)
    monkeypatch.setattr(event_tour, "_chassis", lambda: True)


# ---- the plan --------------------------------------------------------------------------------------------------

def test_the_tracked_debut_has_the_tour_and_its_guests_are_spots_to_mark():
    doc = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})
    debut = next(s for s in doc["scenes"] if s["id"] == "debut")
    assert debut["tour"]["counter"] == "beside_host" and debut["tour"]["guests"] == ["guest_1", "guest_2", "guest_3"]
    assert [s["id"] for s in doc["scenes"] if s.get("tour")] == ["debut"]
    for g in ("guest_1", "guest_2", "guest_3"):
        assert doc["spots"][g]["pose"] is None and "from_spot" not in doc["spots"][g]
    # the debut itself is as it was: its walk ahead, its pageant, its recorded line on the way
    assert debut["forward_m"] == 1.0 and debut["pageant"] and debut["say_on_walk"] and debut["recorded"]
    # cards 2 and 3 stay cards of their own (fallbacks)
    assert {"counter_handshake", "guest_handshake"} <= {s["id"] for s in doc["scenes"]}
    # robot.env turns it on; the code's default is off (a PC that pulls: card 1 as before)
    env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nEVENT_TOUR=true" in env.replace("\r\n", "\n")
    assert '_get_bool("EVENT_TOUR", False)' in (ROOT / "app" / "config.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("raw", [
    {"counter": "dock", "guests": ["guest_1"]},                  # the dock is no place to stand
    {"counter": "beside_host", "guests": ["guest_front"]},       # a spot that walks on from another
    {"counter": "beside_host", "guests": ["guest_1", "guest_1"]},
    {"counter": "beside_host", "guests": ["beside_host"]},
    {"counter": "beside_host", "guests": []},
    {"counter": "nowhere", "guests": ["guest_1"]},
    ["beside_host"],
])
def test_a_tour_that_names_no_place_to_stand_is_no_tour(raw):
    spots = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})["spots"]
    assert event_tour.parse(raw, spots) is None


def test_a_walk_up_is_card_3s_kind_from_the_tours_own_counter():
    """The tour's `walk_up` (23 Nov): a spot that walks on from ITS counter, turned round, up to a guest (`forward`
    with `meet_m`). Anything else is no walk-up (logged), the tour kept: she stays at the counter as before."""
    spots = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})["spots"]
    base = {"counter": "beside_host", "guests": ["guest_1"]}
    assert event_tour.parse(dict(base, walk_up="guest_front"), spots)["walk_up"] == "guest_front"
    for bad in ("guest_1", "reception", "dock", "nowhere", 3, ["guest_front"]):
        assert event_tour.parse(dict(base, walk_up=bad), spots)["walk_up"] is None, bad
    other = {"counter": "reception", "guests": ["guest_1"], "walk_up": "guest_front"}
    assert event_tour.parse(other, spots)["walk_up"] is None           # card 3's spot walks on from beside_host
    no_meet = dict(spots, guest_front=dict(spots["guest_front"],
                                           steps=[s for s in spots["guest_front"]["steps"] if "forward" not in s]))
    assert event_tour.parse(dict(base, walk_up="guest_front"), no_meet)["walk_up"] is None


def test_the_plan_is_what_is_recorded_now():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    norm = event_host._normalise(doc, {})
    tour = norm["scenes"][0]["tour"]
    plan = event_tour.planned(tour, norm["spots"])
    # nothing recorded: the counter (its file steps) and its handshake, then -- no round of the marked spots -- ONE
    # guest card 3's way (owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคนแล้วทักทายได้ไหม"): the
    # tour's walk_up (guest_front), its hand only to someone met, then back to the counter
    assert plan["missing"] == ["แขก 1", "แขก 2", "แขก 3"] and plan["counter_ok"] and not plan["guests_ok"]
    assert tour["walk_up"] == "guest_front" and plan["walk_up_ok"]
    assert [(s["kind"], s["spot"], bool(s.get("if_met")), bool(s.get("back"))) for s in plan["steps"]] == [
        ("walk", "beside_host", False, False), ("handshake", "beside_host", False, False),
        ("walk", "guest_front", False, False), ("handshake", "guest_front", True, False),
        ("walk", "beside_host", False, True)]
    assert plan["walk_up_th"] == event_tour.WALK_UP_STEP_TH and "แขก 1, แขก 2, แขก 3" in plan["missing_th"]
    # one guest missing is no round of the marked spots either -- never a round with a gap, never a walk to an
    # unmeasured spot: the walk-up instead
    two = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in list(GUESTS.items())[:2]}})
    assert event_tour.planned(tour, two["spots"])["missing"] == ["แขก 3"]
    assert [s["spot"] for s in event_tour.planned(tour, two["spots"])["steps"] if s["kind"] == "walk"] == [
        "beside_host", "guest_front", "beside_host"]
    # a tour that names no walk_up: she stays at the counter (as before 23 Nov)
    plain = dict(tour, walk_up=None)
    assert [(s["kind"], s["spot"]) for s in event_tour.planned(plain, norm["spots"])["steps"]] == [
        ("walk", "beside_host"), ("handshake", "beside_host")]
    assert not event_tour.planned(plain, norm["spots"])["walk_up_ok"]
    # all three: counter, each guest, back
    full = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in GUESTS.items()}})
    steps = event_tour.planned(tour, full["spots"])["steps"]
    assert [(s["kind"], s["spot"], bool(s.get("back"))) for s in steps] == [
        ("walk", "beside_host", False), ("handshake", "beside_host", False),
        ("walk", "guest_1", False), ("handshake", "guest_1", False),
        ("walk", "guest_2", False), ("handshake", "guest_2", False),
        ("walk", "guest_3", False), ("handshake", "guest_3", False),
        ("walk", "beside_host", True)]


def test_a_point_behind_her_is_turned_to_first_slowly_and_one_ahead_is_not():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    spots = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in GUESTS.items()}})["spots"]
    # the counter (facing the table) -> the first guest, behind her: the watched turn, card 3's rate
    steps = event_tour.leg_steps(spots["guest_1"], (-8.62, -4.2, 0.0), None)
    turn = next(s for s in steps if s.get("do") == "turn")
    assert steps.index(turn) < next(i for i, s in enumerate(steps) if s.get("to"))
    assert turn["w_spin"] == 0.3 and abs(turn["face"] - math.degrees(math.atan2(-3.4 + 4.2, -9.35 + 8.62))) < 0.2
    assert event_tour.rear_turns(steps, (-8.62, -4.2, 0.0)) == []
    # the last guest -> the counter, behind her again
    assert any(s.get("do") == "turn" for s in event_tour.leg_steps(spots["beside_host"], (-9.35, -5.0, 180.0), None))
    # guest to guest, beside her: the board's own turn (no extra turn of ours)
    assert not any(s.get("do") == "turn" for s in event_tour.leg_steps(spots["guest_2"], (-9.35, -3.4, 180.0), None))
    # the same leg with no turn of ours: the simulator's gate names it
    assert event_tour.rear_turns(event_host.spot_steps(spots["guest_1"]), (-8.62, -4.2, 0.0))
    # no heading: nothing to turn by; no spot: nothing to walk
    assert not any(s.get("do") == "turn" for s in event_tour.leg_steps(spots["guest_1"], (-8.62, -4.2, None), None))
    assert event_tour.leg_steps(None, (0.0, 0.0, 0.0), None) is None


#: The route file's dock as card 1's counter leg leaves it (out by its approach point), and where the debut's metre out
#: of the box leaves her.
DOCK = {"pose": [-7.1, -6.84, 168.0], "approach": [-9.0, -6.2], "leave_by_approach": True}
OUT_OF_THE_BOX = (-8.08, -6.63, 168.0)


def _points(steps):
    return [tuple(s["to"]) for s in steps or [] if s.get("to")]


def test_the_walk_to_khun_mikis_hand_comes_straight_in_and_the_way_back_does_not():
    """Owner 23 Nov, after card 1 ran on the robot: "ตอนเดินปรับเป็นเดินให้ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามาจับมือกับ
    คุณมิกิ" (his sketch: "ออกห่างโต๊ะก่อน แล้วเดินตรงเข้า"). That run came north along the counter from the dock's approach
    point and turned to the table on the spot. The walk that ends in his handshake now goes first to a point 1 m back
    from the spot along its heading, turns there (ours, slowly: ~107 deg) to face the table, and walks the last metre
    straight in. The walk back after the guests (no handshake) comes as it was."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    spots = event_host._normalise(doc, {})["spots"]
    counter = spots["beside_host"]
    assert counter["head_on_m"] == 1.0 and event_tour.head_on_point(counter) == (-9.62, -4.2, 0.0)
    steps = event_tour.leg_steps(counter, OUT_OF_THE_BOX, DOCK, head_on=True)
    assert _points(steps) == [(-9.0, -6.2), (-9.62, -4.2), (-8.62, -4.2)], steps
    # at the head-on point our slow turn to the table (the board's own would be ~107 deg, unwatched), then the last
    # metre straight ahead: the last MoveTo's heading is its bearing
    last = max(k for k, s in enumerate(steps) if s.get("to"))
    turn = steps[last - 1]
    assert turn["do"] == "turn" and turn["face"] == 0.0 and turn["w_spin"] == event_tour.TURN_W_SPIN, steps
    assert steps[last]["face"] == 0 and event_tour.rear_turns(steps, OUT_OF_THE_BOX) == []
    # the same walk with no handshake at its end (the way back after the guests): as before 23 Nov
    assert _points(event_tour.leg_steps(counter, OUT_OF_THE_BOX, DOCK)) == [(-9.0, -6.2), (-8.62, -4.2)]
    assert _points(event_tour.leg_steps(counter, (-9.35, -5.0, 180.0), None)) == [(-8.62, -4.2)]
    # already nearer the counter than its head-on point: straight to it, never away to come back
    assert _points(event_tour.leg_steps(counter, (-8.9, -4.6, 90.0), None, head_on=True)) == [(-8.62, -4.2)]
    # recorded at the robot (its note: staff record it at the rehearsal) -- the point comes from what was recorded
    rec = event_host._normalise(doc, {"spots": {"beside_host": {"pose": [-8.60, -4.25, 2.0], "recorded_at": "x"}}})
    p = event_tour.head_on_point(rec["spots"]["beside_host"])
    assert p == pytest.approx((-8.60 - math.cos(math.radians(2.0)), -4.25 - math.sin(math.radians(2.0)), 2.0), abs=1e-3)
    got = _points(event_tour.leg_steps(rec["spots"]["beside_host"], OUT_OF_THE_BOX, DOCK, head_on=True))
    assert got[1] == pytest.approx(p[:2], abs=1e-3) and got[2] == (-8.6, -4.25), got
    # a spot that asks for none, or for what is no distance: the walk as it was
    for bad in (None, 0, -1.0, 3.0, True, "1"):
        sp = dict(counter, head_on_m=bad)
        assert event_tour.head_on_point(sp) is None, bad
        assert _points(event_tour.leg_steps(sp, OUT_OF_THE_BOX, DOCK, head_on=True)) == [(-9.0, -6.2), (-8.62, -4.2)]
    # the plan: the counter's walk ends in his hand, each guest's in theirs (no head-on point of their own); the walk
    # back has none
    full = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in GUESTS.items()}})
    plan = event_tour.planned(full["scenes"][0]["tour"], full["spots"])["steps"]
    assert [bool(s.get("head_on")) for s in plan if s["kind"] == "walk"] == [True, True, True, True, False]
    assert all(event_tour.head_on_point(full["spots"][g]) is None for g in GUESTS)


def test_by_hand_the_cards_walk_to_khun_mikis_hand_comes_the_same_way(monkeypatch):
    """Card 1 cut before the counter (23 Nov on the robot it ended and its counter walk was pressed by hand): staff's
    "ไปข้างคุณมิกิ" is the card's walk to his hand -- head-on as the card would have come; the walk back is not."""
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    full = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in GUESTS.items()}})
    plan = event_tour.planned(full["scenes"][0]["tour"], full["spots"])["steps"]
    event_tour.reset()
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    event_tour.STATE.update(seq=7, scene="debut", plan=plan, at=0, phase="walking", ended="leg_cut", owner="event",
                            ended_at=time.time())
    went = []

    async def go_spot(spot_id, *, stops_seen=None, head_on=False):
        went.append((spot_id, head_on))
        return {"ok": True, "walk": {"ok": True, "run": {"id": "h%d" % len(went)}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    try:
        walks = [h for h in event_tour.view()["hand"] if h["kind"] == "walk"]
        assert [(h["spot"], h["head_on"]) for h in walks] == [
            ("beside_host", True), ("guest_1", True), ("guest_2", True), ("guest_3", True), ("beside_host", False)]
        for h in (walks[0], walks[-1]):
            assert asyncio.run(event_tour.event_hand(h["k"], stops_seen=event_walk.stops()))["ok"]
        assert went == [("beside_host", True), ("beside_host", False)]
    finally:
        event_tour.reset()


# ---- one press ---------------------------------------------------------------------------------------------------

def test_one_press_is_the_debut_the_counter_three_guests_and_back(tour, monkeypatch):
    _where_from_the_last_walk(monkeypatch, tour)
    _run(tour)
    log = tour["log"]
    assert _walks(log) == ["stage_centre", "beside_host", "guest_1", "guest_2", "guest_3", "beside_host"], log
    # a handshake at the counter and at each guest -- none at the end, none on the way, each waited out
    kinds = [(e[0], e[1] if e[0] == "walk" else None) for e in log if e[0] in ("walk", "arm_end") or e == ("arm", "handshake")]
    assert kinds == [("walk", "stage_centre"),
                     ("walk", "beside_host"), ("arm", None), ("arm_end", None),
                     ("walk", "guest_1"), ("arm", None), ("arm_end", None),
                     ("walk", "guest_2"), ("arm", None), ("arm_end", None),
                     ("walk", "guest_3"), ("arm", None), ("arm_end", None),
                     ("walk", "beside_host")], kinds
    # a walk to a point behind her turns first (counter -> guest 1, guest 3 -> counter); the guest to guest walks do
    # not (the debut's own walk to the recorded stage is the scene's: it ends with a turn to the stage's heading, 116
    # deg off). The counter's walk ends in Khun Miki's hand: since 23 Nov it comes straight in from its head-on point
    # 1 m back (owner: "ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามา") -- from the stage the point is behind her, and there
    # she turns to the table, both ours; the walk back after the guests comes as it was
    walks = [e for e in log if e[0] == "walk"]
    assert ["turn" in e[2] for e in walks[1:]] == [True, True, False, False, True], walks
    turn = next(s for s in tour["routes"][2] if s.get("do") == "turn")
    assert turn["w_spin"] == event_tour.TURN_W_SPIN
    assert _points(tour["routes"][1])[-2:] == [(-9.62, -4.2), (-8.62, -4.2)], tour["routes"][1]
    assert _points(tour["routes"][5]) == [(-8.62, -4.2)], tour["routes"][5]
    # and ends back at the counter, all of it said so
    v = tour["view"]
    assert v["ok"] and v["ended"] == "done" and all(s["state"] == "done" for s in v["steps"])
    assert [s["text_th"] for s in v["steps"]] == [
        "เปิดตัว", "ไปข้างคุณมิกิ", "ยื่นมือจับมือ (ข้างคุณมิกิ)", "ไปแขก 1", "ยื่นมือจับมือ (แขก 1)",
        "ไปแขก 2", "ยื่นมือจับมือ (แขก 2)", "ไปแขก 3", "ยื่นมือจับมือ (แขก 3)", "กลับข้างคุณมิกิ"]
    assert v["by_hand_th"] == []
    # nothing said at a handshake: the debut's own line (on its walk, or at its end when the walk was too short for
    # it) is all that reached the model
    says = [e for e in tour["stage"]["log"] if e[0] == "say"]
    assert len(says) == 1 and "Hello everyone! I'm Emmaaa!" in says[0][2], says
    assert not event_tour.active() and not greeter.paused()


def test_each_handshake_is_waited_out_before_the_next_walk(tour):
    tour["hold"] = 1.0                                    # the app's group 6 + lowering + group 99, shortened
    _run(tour)
    log = tour["log"]
    for i, e in enumerate(log):
        if e == ("arm", "handshake"):
            nxt = next(x for x in log[i + 1:] if x[0] in ("walk", "arm_end"))
            assert nxt[0] == "arm_end", log                # its own end came before any walk
    assert len(_handshakes(log)) == 4


def test_the_phone_sees_where_it_is_as_it_goes(tour):
    seen = {}

    async def look(box):
        assert await _until(lambda: "แขก 2" in ((event_tour.view() or {}).get("now_th") or ""))
        seen["during"] = event_tour.view()

    _run(tour, look)
    during = seen["during"]
    assert during["ended"] is None and [s["state"] for s in during["steps"]][:4] == ["done"] * 4
    assert [s["state"] for s in during["steps"]].count("now") == 1


def test_missing_guest_spots_and_no_walk_up_stay_at_the_counter(tour, event):  # noqa: F811
    doc = json.loads(event.read_text(encoding="utf-8"))
    for s in doc["scenes"]:
        if s.get("tour"):
            s["tour"].pop("walk_up")                    # a tour that names no walk-up: as before 23 Nov
    event.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _record(guests={"guest_1": GUESTS["guest_1"]})
    event_host.reset()
    _run(tour)
    assert _walks(tour["log"]) == ["stage_centre", "beside_host"]
    assert len(_handshakes(tour["log"])) == 1
    assert tour["view"]["ok"] and tour["view"]["ended"] == "done_counter"
    assert tour["view"]["missing"] == ["แขก 2", "แขก 3"]


#: board_walk's walk up to a guest that ended in front of someone (its `@@ met` line, then the step done).
MEETS = (STARTED + "print('@@ ' + json.dumps({'met': 4, 'gap_m': 0.69, 'after_m': 0.71}), flush=True); "
         "print('[ 0.1] step 1 done', flush=True)")


@pytest.mark.parametrize("met", [True, False])
def test_guest_spots_not_marked_walk_up_to_one_guest_card_3s_way(tour, met):
    """Owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคนแล้วทักทายได้ไหม". A guest spot not marked:
    after Khun Miki's hand, card 3's own walk (guest_front: turned round slowly at the counter, straight ahead slowly
    up to whoever stands there) -- the hand only to someone it met (board_walk's `@@ met`), nobody = no hand -- then
    back to the counter facing it, no hand. One guest; the marked round is not walked with a gap in it."""
    _record(guests={"guest_1": GUESTS["guest_1"]})       # guests 2 and 3 not marked
    event_host.reset()
    if met:
        tour["walkers"] = {3: MEETS}
    _run(tour)
    log = tour["log"]
    assert _walks(log) == ["stage_centre", "beside_host", "guest_front", "beside_host"], log
    # card 3's own steps, not new ones
    up = tour["routes"][2]
    turn = next(s for s in up if s.get("do") == "turn")
    walk = next(s for s in up if "forward" in s)
    assert turn["w_spin"] == 0.3 and (walk["forward"], walk["speed"], walk["meet_m"]) == (1.2, 0.15, 0.7), up
    # Khun Miki's hand, the guest's only when someone was met -- each waited out before the next walk; none after
    kinds = [(e[0], e[1] if e[0] == "walk" else None) for e in log if e[0] in ("walk", "arm_end") or e == ("arm", "handshake")]
    guest_hand = [("arm", None), ("arm_end", None)] if met else []
    assert kinds == [("walk", "stage_centre"), ("walk", "beside_host"), ("arm", None), ("arm_end", None),
                     ("walk", "guest_front"), *guest_hand, ("walk", "beside_host")], kinds
    v = tour["view"]
    assert v["ok"] and v["ended"] == "done" and v["missing"] == ["แขก 2", "แขก 3"], v
    texts = [s["text_th"] for s in v["steps"]]
    assert texts[3] == event_tour.WALK_UP_TH and texts[-1] == "กลับข้างคุณมิกิ" and all(
        s["state"] == "done" for s in v["steps"]), v["steps"]
    assert texts[4] == (event_tour.WALK_UP_HAND_TH if met else event_tour.NOBODY_MET_TH)
    assert v["detail_th"] == (None if met else event_tour.NOBODY_MET_TH)


def test_the_ceremonys_opening_says_the_walk_up_too(served):
    """The Emma Ceremony app's opening step is card 1 (owner 23 Nov "ช่วงพิธีอยู่แอปพิธีทั้งหมด"): its page's words for what
    one press does come from the server (`ceremony_view`) -- the walk up to one guest and the way back, and where the
    guest stands, when the guest spots are not marked; the round when they are."""
    v = event_tour.ceremony_view("debut", {})
    assert v["steps_th"][3:] == ["ไปข้างคุณมิกิ ยื่นมือจับมือ (ไม่พูด)", event_tour.WALK_UP_STEP_TH,
                                 "กลับข้างคุณมิกิ หันเข้าเคาน์เตอร์"], v["steps_th"]
    assert event_tour.WALK_UP_MISSING_TH % ("แขก 1, แขก 2, แขก 3", "ข้างคุณมิกิ") in v["warn_th"], v["warn_th"]
    _record()
    event_host.reset()
    v = event_tour.ceremony_view("debut", {})
    assert v["steps_th"][4].startswith("ไปจับมือแขก 3 จุด") and not any("ยังไม่ได้บันทึกจุด" in w for w in v["warn_th"])


def test_a_walk_up_cut_leaves_the_rest_by_hand_card_3s_way(tour):
    """The card stopped on its walk up to the guest: by hand, the same walk (card 3's spot), the hand, back."""
    _record(guests={})
    event_host.reset()
    tour["walkers"] = {3: SHORT}
    _run(tour)
    v = tour["view"]
    assert v["ended"] == "leg_cut" and [s["state"] for s in v["steps"]][3] == "cut", v["steps"]
    assert [(h["kind"], h["spot"], h["text_th"]) for h in v["hand"]] == [
        ("walk", "guest_front", event_tour.WALK_UP_TH), ("handshake", "guest_front", "ยื่นมือจับมือ (แขกที่เดินไปเจอ)"),
        ("walk", "beside_host", "ไปข้างคุณมิกิ")]


# ---- every way it ends ------------------------------------------------------------------------------------------

def _stopper(kind):
    async def red(box):
        await event_host.stop()

    async def voice(box):
        await event_walk.stop_from("voice")

    async def desk(box):
        await event_walk.stop_from("desk")

    async def quiet(box):
        box["polling"] = False
        event_walk.STATE["heartbeat"] = time.monotonic() - 60.0

    async def face(box):
        event_walk._faces = lambda: False                # monkeypatch restores it (the fixture set it)

    async def latch(box):
        event_walk._app_latched = lambda: True

    async def ceremony(box):
        event_host.ceremony_now = lambda: True

    async def press(box):
        await event_host.start_scene("introduce")

    async def spot(box):
        await event_host.go_spot("reception")

    async def nowhere(box):
        # the card's walk to a spot with no position: refused, no walk at all -- but it was a press
        out = await event_host.go_spot("table_1")
        assert out["ok"] is False

    return {"red": red, "voice": voice, "desk": desk, "quiet": quiet, "face": face, "latch": latch,
            "ceremony": ceremony, "press": press, "spot": spot, "nowhere": nowhere}[kind]


ENDS = {"red": "staff", "voice": "voice", "desk": "desk", "quiet": "control_lost", "face": "face_gone",
        "latch": "app_stop", "ceremony": "ceremony_app", "press": "replaced", "spot": "replaced", "nowhere": "replaced"}


@pytest.mark.parametrize("when", ["walking", "handshake"])
@pytest.mark.parametrize("kind", sorted(ENDS))
def test_every_way_it_ends_ends_all_of_it(tour, kind, when):
    if when == "walking":
        tour["walkers"] = {3: SLOW}                       # the stage 1, the counter 2, guest_1 3: out on it
    else:
        tour["hold"] = 4.0                                # the counter's handshake still going
    marks = {}

    async def then(box):
        if when == "walking":
            assert await _until(lambda: "guest_1" in _walks(box["log"]))
            await asyncio.sleep(0.4)                     # out on it
        else:
            assert await _until(lambda: len(_handshakes(box["log"])) >= 1)
            await asyncio.sleep(0.2)
        marks["at"] = len(box["log"])
        await _stopper(kind)(box)

    _run(tour, then)
    after = tour["log"][marks["at"]:]
    # no walk of the tour and no handshake after it (a press's own walk -- the spot's -- is that press's)
    assert [e for e in after if (e[0] == "walk" and e[1] in TOUR_SPOTS) or e == ("arm", "handshake")] == [], after
    v = tour["view"]
    assert v["ended"] == ENDS[kind] and not v["ok"] and v["why_th"], v
    states = [s["state"] for s in v["steps"]]
    cut = 3 if when == "walking" else 2                   # guest 1's walk / the counter's handshake
    assert states[:cut] == ["done"] * cut and states[cut] == "cut" and set(states[cut + 1:]) == {"todo"}, states
    # what is left, by hand, from the step it was on, to the counter -- one two-tap button each since the second
    # review of 23 Nov (`hand`, the Event page's /event/tour-hand)
    assert v["by_hand_th"][0] == ("ไปแขก 1" if when == "walking" else "ยื่นมือจับมือ (ข้างคุณมิกิ)")
    assert v["by_hand_th"][-1] == "ไปข้างคุณมิกิ"
    assert [h["text_th"] for h in v["hand"]] == v["by_hand_th"]
    assert not event_tour.active()


def test_a_leg_that_does_not_arrive_ends_it(tour):
    tour["walkers"] = {3: SHORT}                          # guest_1: the board gave up
    _run(tour)
    assert _walks(tour["log"]) == ["stage_centre", "beside_host", "guest_1"]
    assert len(_handshakes(tour["log"])) == 1
    assert tour["view"]["ended"] == "leg_cut" and tour["view"]["detail_th"]


@pytest.mark.parametrize("how,why", [("failed", "handshake_failed"), ("stopped", "handshake_stopped"),
                                     ("silent", "handshake_silent"), ("no_end", "handshake_no_end"),
                                     ("refused", "handshake_refused")])
def test_a_handshake_that_is_not_done_ends_it(tour, monkeypatch, how, why):
    monkeypatch.setattr(event_tour, "HANDSHAKE_START_S", 0.4)
    monkeypatch.setattr(event_tour, "HANDSHAKE_DONE_S", 0.8)
    if how in ("failed", "stopped"):
        tour["end"], tour["reason"] = how, "arm_unavailable"
    elif how == "silent":
        tour["silent"] = True
    elif how == "no_end":
        tour["end"] = None
    else:
        tour["arm_answer"] = {"ok": False, "action": "handshake", "hardware": "refused", "error": "disabled",
                              "error_th": "ปิดการสั่งท่าทางไว้บนหุ่น"}
    _run(tour)
    assert _walks(tour["log"]) == ["stage_centre", "beside_host"], tour["log"]
    assert tour["view"]["ended"] == why
    assert tour["view"]["steps"][2]["state"] == "cut"   # the counter's handshake


def test_a_debut_that_never_got_there_has_no_tour(tour):
    tour["walkers"] = {1: SHORT}
    _run(tour)
    assert _walks(tour["log"]) == ["stage_centre"] and _handshakes(tour["log"]) == []
    assert tour["view"]["ended"] == "debut_cut" and tour["view"]["steps"][0]["state"] == "cut"


def test_a_debut_whose_wheels_never_turned_says_the_e_stop(tour, monkeypatch):
    """23 Nov on the robot: the E-stop was in, the walk out stopped at 0.00 m ("asked to go and not moving"), and
    card 1's panel read "มีคนหรือของขวางทาง" while the walk card said the E-stop. The panel says what the walk says."""
    monkeypatch.setattr(event_walk.Run, "still", lambda self: True)
    tour["walkers"] = {1: SHORT}
    _run(tour)
    assert tour["view"]["ended"] == "debut_cut" and tour["view"]["detail_th"] == event_walk.STILL_TH


@pytest.mark.parametrize("kind, says_e_stop", [("blocked", True), (None, True), ("control", False), ("face", False),
                                               ("lost", False)])
def test_the_e_stop_line_only_where_the_wheels_tell_nothing_else(tour, monkeypatch, kind, says_e_stop):
    """Second review 23 Nov: a stop of a known kind (the page went quiet, the face gone, lost) keeps its own words even
    with the wheels still -- the E-stop line over "the control page went quiet" misleads the other way."""
    event_tour.reset()
    event_tour.STATE.update(seq=7, scene="debut", phase="debut", ended=None, stops_seen=event_walk.stops(),
                            until=1e18, plan=[])
    monkeypatch.setitem(event_host.STATE, "seq", 7)

    class Run:
        state, reason = "stopped", "x"

        def view(self):
            return {"kind": kind, "still": True, "still_th": event_walk.STILL_TH, "reason_th": "ของมันเอง"}

    try:
        event_tour.debut_ended(7, Run())
        assert event_tour.STATE["ended"] == "debut_cut"
        assert event_tour.STATE["detail_th"] == (event_walk.STILL_TH if says_e_stop else "ของมันเอง")
    finally:
        event_tour.reset()


def test_a_refused_walk_by_hand_holds_no_head(monkeypatch):
    """The hold is the press's own and only for a walk that set out: refused = nothing held after it."""
    from app import event_host

    event_tour.reset()
    monkeypatch.setattr(event_walk, "walking", lambda: False)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    monkeypatch.setattr(event_tour, "view", lambda: {"owner": "event", "hand": [
        {"k": 0, "kind": "walk", "spot": "beside_host", "text_th": "ไปข้างคุณมิกิ", "done": False}]})

    async def go_spot(spot_id, *, stops_seen=None, head_on=False):
        return {"ok": False, "error": "stopped", "walk": {"ok": False, "run": {"id": "gone"}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    try:
        assert asyncio.run(event_tour.event_hand(0, stops_seen=event_walk.stops()))["ok"] is False
        assert event_tour.STATE["hand_run"] is None
    finally:
        event_tour.reset()


def test_walking_off_the_debut_where_she_stands_and_nothing_after(tour, monkeypatch):
    monkeypatch.setattr(settings, "event_walk_enabled", False)

    async def go():
        out = await event_host.start_scene("debut")
        assert out["ok"] and not out["walk"]["ok"]
        await asyncio.sleep(0.3)
        return event_tour.view()

    v = asyncio.run(go())
    assert v["ended"] == "walking_off" and not event_tour.active() and v["steps"][0]["state"] == "done"
    assert _walks(tour["log"]) == ["stage_centre"]


def test_with_the_switch_off_card_1_is_the_debut_alone(tour, monkeypatch):
    monkeypatch.setattr(settings, "event_tour", False)

    async def go():
        poll = asyncio.create_task(_polls(tour))
        try:
            out = await event_host.start_scene("debut")
            assert out["walk"]["ok"]
            await _until(lambda: not event_walk.walking())
            await asyncio.sleep(1.0)
        finally:
            poll.cancel()

    asyncio.run(go())
    assert _walks(tour["log"]) == ["stage_centre"] and event_tour.view() is None
    assert all(s["tour"] is None for s in event_host.snapshot()["scenes"])


def test_a_restart_says_where_it_was_cut_and_does_not_go_on(tour):
    tour["walkers"] = {3: SLOW}
    seen = {}

    async def then(box):
        assert await _until(lambda: "guest_1" in _walks(box["log"]))
        await asyncio.sleep(0.3)
        seen["note"] = (event_walk.RUN_DIR / "tour.json").read_text(encoding="utf-8")
        await event_host.stop()

    _run(tour, then)
    note = json.loads(seen["note"])
    assert note["ended"] is None and note["phase"] == "walking" and note["at"] == 2
    # the server died there: the note is what is left
    (event_walk.RUN_DIR / "tour.json").write_text(seen["note"], encoding="utf-8")
    event_tour.reset()
    event_tour.recover()
    v = event_tour.view()
    assert v["ended"] == "restarted" and not event_tour.active() and event_tour.STATE["task"] is None
    assert v["steps"][3]["state"] == "cut" and v["by_hand_th"][0] == "ไปแขก 1"


def test_a_tour_leg_is_not_the_pages_poll(tour):
    """beat=False: minutes after the press, a leg's start does not count as the page being alive."""
    async def go():
        event_walk.STATE["heartbeat"] = 123.0
        await event_walk.start("guest_1", "แขก 1", [{"to": [0.0, 0.0]}], dock=None, beat=False)
        beat = event_walk.STATE["heartbeat"]
        await event_walk.stop("staff")
        return beat

    assert asyncio.run(go()) == 123.0


# ---- the head still, and nothing else on the arm --------------------------------------------------------------------

@pytest.fixture
def held(tmp_path, monkeypatch):
    # its own run folder (review 23 Nov: without it a test wrote this PC's data/events/.run/tour.json); conftest pins
    # one for every test as well
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_tour.STATE.update(seq=1, scene="debut", phase="walking", ended=None, plan=[])
    event_tour._hold(30.0)
    yield
    event_tour.reset()


def test_a_walk_of_card_1_sent_by_hand_keeps_the_head_still(monkeypatch):
    """23 Nov on the robot: the card ended at its counter leg, "ไปข้างคุณมิกิ" was pressed by hand, and the walk scan
    nodded the head (tilt 18) on the way -- the owner's "ไม่ต้องให้หุ่นขยับหัวนะ" is the whole card's, its steps by
    hand too. Held from the press (the walk is under way ~3 s before go_spot answers) to that walk's end, only."""
    from app import event_host

    event_tour.reset()
    walking = {"on": False}
    current = {"run": None}
    monkeypatch.setattr(event_walk, "walking", lambda: walking["on"])
    monkeypatch.setattr(event_walk, "current", lambda: current["run"])
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    monkeypatch.setattr(event_tour, "view", lambda: {"owner": "event", "hand": [
        {"k": 0, "kind": "walk", "spot": "beside_host", "text_th": "ไปข้างคุณมิกิ", "done": False}]})
    seen = []

    async def go_spot(spot_id, *, stops_seen=None, head_on=False):
        walking["on"] = True                             # under way before the answer
        current["run"] = type("Run", (), {"id": "h1"})()
        seen.append(event_tour.holds_head())
        return {"ok": True, "walk": {"ok": True, "run": {"id": "h1"}}}

    monkeypatch.setattr(event_host, "go_spot", go_spot)
    try:
        assert not event_tour.holds_head()
        out = asyncio.run(event_tour.event_hand(0, stops_seen=event_walk.stops()))
        assert out["ok"] and seen == [True], (out, seen)
        assert event_tour.holds_head()                   # its walk still under way
        current["run"] = type("Run", (), {"id": "other"})()
        assert not event_tour.holds_head()               # another walk (a spot, home) is not the card's
        current["run"] = type("Run", (), {"id": "h1"})()
        walking["on"] = False
        assert not event_tour.holds_head()               # that walk over: the head is the loop's again
    finally:
        event_tour.reset()


def test_the_head_follows_nothing_during_the_tour(held, monkeypatch):
    from app import head_follow, walk_frames
    from tests.test_head_follow import Face, Scene, tick

    monkeypatch.setattr(settings, "head_follow", True)
    monkeypatch.setattr(settings, "head_calibrate", False)
    monkeypatch.setattr(settings, "head_walk_scan", True)
    monkeypatch.setattr(settings, "head_pan_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_tilt_full_deg", 20.0)
    monkeypatch.setattr(settings, "head_tilt_down_max", 40)
    monkeypatch.setattr(settings, "head_search_tilt", 30)
    monkeypatch.setattr(settings, "head_search_rms", 0.06)
    head_follow.reset()
    try:
        scene = Scene(monkeypatch)
        face = Face()
        follow = head_follow.HeadFollow(face)
        walking = {"on": True}
        monkeypatch.setattr(event_walk, "walking", lambda: walking["on"])
        monkeypatch.setattr(event_walk, "moving", lambda: walking["on"])
        monkeypatch.setattr(walk_frames, "still_s", lambda now=None: 3.0)   # a stop on a walk: the scan would nod
        for t in (100.0, 101.0, 104.0, 108.0):
            tick(follow, t)
        walking["on"] = False
        scene.face(15.0, t=110.0)                          # a face: it would turn
        tick(follow, 110.0)
        scene.nobody()
        scene.voice = (112.0, 0.09)                        # a voice at the robot: it would look down
        tick(follow, 112.5)
        head_follow.look_hint(18)                          # a face approach's hint
        tick(follow, 120.0)
        assert face.ws.looks() == [], face.ws.looks()
        # the tour over: the head is the loop's again -- one slow centring first (group 6 may have moved it)
        event_tour._end(1, "done")
        scene.voice = (None, None)
        tick(follow, 130.0)
        assert face.ws.looks() == [(0, 0)]
    finally:
        head_follow.reset()


def test_nothing_else_moves_the_arm_or_greets_during_the_tour(held, monkeypatch):
    from app import robot_arm, speech_gestures
    from app.tools import robot as robot_tools
    from app.tools.registry import robot_face_session

    monkeypatch.setattr(robot_body, "_server_gate", lambda action: True)
    monkeypatch.setattr(robot_arm, "armed", lambda: True)
    assert greeter.paused()                                # the camera's greetings (their handshake turns the head)
    assert speech_gestures._why_not(object(), "raise_right_arm") == "tour"
    # a voice's gesture: refused busy
    out = asyncio.run(robot_body._request("nod", object()))
    assert out["ok"] is False and out["reason"] == "busy"
    token = robot_face_session.set(object())
    try:
        res = asyncio.run(robot_tools.request_handshake())
    finally:
        robot_face_session.reset(token)
    assert res["ok"] is False and res["hardware"] == "refused"
    # the card's own buttons: refused and greyed, said why; the hand down never
    assert asyncio.run(event_host.manual_gesture("handshake"))["error"] == "tour"
    assert asyncio.run(event_host.manual_gesture("pageant_end"))["ok"] is True
    # the tour over: all of it back
    event_tour._end(1, "done")
    assert not greeter.paused()
    assert speech_gestures._why_not(object(), "raise_right_arm") != "tour"
    assert asyncio.run(robot_body._request("nod", object())).get("reason") != "busy"


def test_a_voice_stop_is_never_refused_during_the_tour(held, monkeypatch):
    from app import robot_face_link

    async def stop_everywhere(session, kind, timeout):
        return {"accepted": True}

    monkeypatch.setattr(robot_face_link, "request_stop", stop_everywhere)
    out = asyncio.run(robot_body._request("stop", object()))
    assert out["ok"] is True


def test_walking_up_to_guests_cannot_be_turned_on_during_the_tour(held, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server, face_approach

    monkeypatch.setattr(settings, "event_control_key", "k" * 32)
    monkeypatch.setattr(face_approach, "set_enabled", lambda on: {"ok": True, "on": on})
    with TestClient(event_control_server.app) as client:
        client.cookies.set(event_control_server.COOKIE, event_control_server._cookie_value())
        during = client.post("/event/approach", json={"on": True}).json()
        off = client.post("/event/approach", json={"on": False}).json()
        event_tour._end(1, "done")
        after = client.post("/event/approach", json={"on": True}).json()
    assert during["error"] == "tour" and off["ok"] is True and after["ok"] is True


def test_the_morning_check_names_the_tours_spots(event, monkeypatch):  # noqa: F811
    """A guest spot with no position = no guest round: the morning's GO / NO-GO says so (EVENT_TOUR on only)."""
    from app import event_readiness

    def spots_row():
        event_host.reset()
        return next(r for r in event_readiness._spot_rows() if r["th"] == "ทุกจุดที่ฉาก/พิธีเดินไปมีตำแหน่ง")

    assert "แขก 1" not in spots_row()["detail_th"]                # the switch off: as before
    monkeypatch.setattr(settings, "event_tour", True)
    row = spots_row()
    assert not row["ok"] and "แขก 1 (เดินเปิดตัว ต่อเนื่อง)" in row["detail_th"] and "แขก 3" in row["detail_th"]
    _record()
    assert "แขก" not in spots_row()["detail_th"]


# ---- the review of 23 Nov ------------------------------------------------------------------------------------------

def test_a_turn_at_the_end_of_a_walk_is_ours_too():
    """The board turns to a MoveTo's heading at its end (and board_walk after a miss) -- at its own speed, blind behind."""
    here = (-9.35, -3.4, 180.0)
    spot = {"name_th": "g", "pose": [-9.35, -4.4, 90.0]}   # a metre to her side, then facing back the way she came
    steps = event_tour.leg_steps(spot, here, None)
    to = next(i for i, s in enumerate(steps) if s.get("to"))
    assert "face" not in steps[to]                          # the board is not handed the heading
    ends = steps[to + 1:]
    # 180 deg from the way she arrives (south): in two halves (TURN_SPLIT_DEG), ours, slow
    assert [s["do"] for s in ends] == ["turn", "turn"] and ends[-1]["face"] == 90.0, ends
    assert all(s["w_spin"] == event_tour.TURN_W_SPIN and s["end"] for s in ends)
    assert event_tour.rear_turns(steps, here) == []
    # left to the board, the gate names that end turn
    assert any("ends with the board's turn" in r for r in event_tour.rear_turns(event_host.spot_steps(spot), here))
    # a point a hand's breadth behind her, its heading round the other way: turned to first, then the short move
    near = {"name_th": "n", "pose": [-9.10, -3.4, 0.0]}
    steps = event_tour.leg_steps(near, here, None)
    assert [s.get("do") or "to" for s in steps] == ["undock", "turn", "turn", "to"] and steps[2]["face"] == 0.0
    assert any("ends with the board's turn" in r for r in event_tour.rear_turns(event_host.spot_steps(near), here))


def test_a_turn_round_is_two_halves_the_same_way_round():
    """23 Nov, the simulator: a turn of ~180 deg dithered -- the shorter way flips with a tenth of a degree of pose
    noise and board_walk's turn stops at every flip -- and ran out of its 20 s (reception -> the multimedia door, 5
    of 20). Past TURN_SPLIT_DEG every turn of ours is two of ~half, one way round; a spot's own turn too (card 3's)."""
    def halves(steps, heading):
        turns = [s for s in steps if s.get("do") == "turn"]
        out, h = [], heading
        for s in turns:
            out.append(event_tour._wrap_deg(s["face"] - h))
            h = s["face"]
        return turns, out

    # a point straight behind her (her recorded reception pose, facing north; the door front south of it)
    door = {"name_th": "d", "pose": [-9.0, -6.2, -90.0]}
    turns, legs = halves(event_tour.leg_steps(door, (-9.0, -5.2, 90.0), None), 90.0)
    assert len(turns) == 2 and all(abs(d) <= 95.0 for d in legs) and (legs[0] > 0) == (legs[1] > 0), legs
    for heading in (89.9, 90.1):                            # a tenth of a degree either side: one way, never both
        _, legs = halves(event_tour.leg_steps(door, (-9.0, -5.2, heading), None), heading)
        assert len(legs) == 2 and (legs[0] > 0) == (legs[1] > 0), legs
    # card 3's own turn round at the counter (face 180 from facing the table): two halves, its own rate and words
    spots = event_host._normalise(json.loads(TRACKED.read_text(encoding="utf-8")), {})["spots"]
    steps = event_tour.leg_steps(spots["guest_front"], (-8.62, -4.2, 0.0), None)
    turns, legs = halves(steps, 0.0)
    assert len(turns) == 2 and turns[-1]["face"] == 180.0 and all(abs(d) <= 95.0 for d in legs), steps
    assert all(t["w_spin"] == 0.3 and "หันหลังให้เคาน์เตอร์" in t["label"] for t in turns)
    # under the line (120 deg): one turn, as it was
    turns, legs = halves(event_tour.leg_steps(door, (-9.0, -5.2, 30.0), None), 30.0)
    assert len(turns) == 1 and abs(legs[0]) == 120.0, legs


def test_every_walk_to_a_spot_turns_round_slowly(event, stage, monkeypatch):  # noqa: F811
    """Review 23 Nov: the card's "ไป<จุด>" (go_spot -- also the ceremony order's walks), a scene's walk, the roam and
    "เดินต่อ" all go through the one builder: from the counter, the reception behind her is turned to, slowly."""
    from app import event_roam

    started = []

    class Arrived:
        state, reason, saw_started, spot = "arrived", None, True, "reception"

    async def start(spot, name, steps, **kw):
        started.append((spot, [dict(s) for s in steps]))
        if kw.get("on_end") is not None and kw.get("autonomous"):
            await kw["on_end"](Arrived())
        return {"ok": True}

    async def where():
        return (-8.62, -4.2, 0.0)                           # at the counter, facing the table

    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(event_tour, "_where", where)
    monkeypatch.setattr(settings, "event_walk_enabled", True)

    async def go():
        await event_host.go_spot("reception")
        await event_host.start_scene("welcome")             # its spot is the reception too
        await event_roam._walk("reception", event_roam.STATE["seq"])
        run = event_walk.Run("reception", "กลางส่วนต้อนรับ")
        run.steps = [{"do": "undock"}, {"to": [-9.0, -5.2], "speed": 0.3, "face": 90.0}]
        run.done, run.state, run.reason, run.dock = 1, "stopped", "staff", None
        event_walk.STATE["run"] = run
        await event_walk.resume_last()

    asyncio.run(go())
    assert len(started) == 4, started
    for spot, steps in started:
        turns = [s for s in steps if s.get("do") == "turn"]
        assert spot == "reception" and turns and turns[0]["w_spin"] == event_tour.TURN_W_SPIN, steps
        assert event_tour.rear_turns(steps, (-8.62, -4.2, 0.0)) == [], steps


def test_a_tour_spot_needs_its_heading():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    saved = {g: {"pose": list(p)} for g, p in GUESTS.items()}
    saved["guest_2"] = {"pose": [-9.35, -4.2, None]}        # a place with no heading: her hand to nobody
    norm = event_host._normalise(doc, {"spots": saved})
    plan = event_tour.planned(norm["scenes"][0]["tour"], norm["spots"])
    assert plan["missing"] == ["แขก 2"] and not plan["guests_ok"] and plan["walk_up_ok"]   # the walk-up, not the round
    assert "guest_2" not in [s["spot"] for s in plan["steps"]]


def test_the_card_names_where_she_turns_from_the_recorded_spots():
    doc = json.loads(TRACKED.read_text(encoding="utf-8"))
    tour = event_host._normalise(doc, {})["scenes"][0]["tour"]

    def turns(guests):
        spots = event_host._normalise(doc, {"spots": {g: {"pose": p} for g, p in guests.items()}})["spots"]
        return [(r["at_th"], r["to_th"], r["end"]) for r in event_tour.planned(tour, spots)["turns"]]

    # a row west of the counter, facing it: round at the counter, round at the last guest
    assert turns(GUESTS) == [("ข้างคุณมิกิ", "แขก 1", False), ("แขก 3", "ข้างคุณมิกิ", False)]
    # a row north of the counter, facing north: round only at the last guest
    north = {"guest_1": [-8.62, -3.4, 90.0], "guest_2": [-8.62, -2.6, 90.0], "guest_3": [-8.62, -1.8, 90.0]}
    assert turns(north) == [("แขก 3", "ข้างคุณมิกิ", False)]


def test_card_1_again_over_its_tour_is_refused(tour):
    tour["walkers"] = {3: SLOW}
    seen = {}

    async def then(box):
        assert await _until(lambda: "guest_1" in _walks(box["log"]))
        await asyncio.sleep(0.3)
        n = len(box["log"])
        seen["out"] = await event_host.start_scene("debut")
        await asyncio.sleep(0.3)
        seen["after"], seen["active"] = list(box["log"][n:]), event_tour.active()
        seen["walking"] = event_walk.walking()
        await event_host.stop()

    _run(tour, then)
    assert seen["out"]["ok"] is False and seen["out"]["error"] == "tour_running"
    assert seen["out"]["error_th"] == event_tour.RUNNING_TH
    assert seen["after"] == [] and seen["active"] and seen["walking"]      # nothing touched: the tour went on
    assert tour["view"]["ended"] == "staff"


def test_wheels_that_never_turn_end_it_and_moving_ones_do_not(tour, monkeypatch):
    """The robot's E-stop is not reported by the board: left in between legs, the next leg's wheels never turn --
    the tour ends there (let go within the leg's 45 s patience, she used to drive on to the next guest)."""
    monkeypatch.setattr(event_tour, "WHEELS_STILL_END_S", 0.6)
    monkeypatch.setattr(event_tour, "POSE_EVERY_S", 0.05)
    monkeypatch.setattr(event_tour, "_chassis", lambda: True)
    tour["walkers"] = {3: SLOW}

    async def still():
        return (-8.62, -4.2, 0.0)

    monkeypatch.setattr(event_tour, "_where", still)
    _run(tour)
    assert _walks(tour["log"]) == ["stage_centre", "beside_host", "guest_1"] and len(_handshakes(tour["log"])) == 1
    assert tour["view"]["ended"] == "wheels_still" and "E-stop" in tour["view"]["why_th"]
    assert tour["view"]["steps"][3]["state"] == "cut"
    # wheels that turn: no end of their own (stopped here by a person after twice the limit)
    event_host.reset()
    tour["log"].clear()
    tour["n"] = 0
    tour["walkers"] = {3: SLOW}
    box = {"x": -8.62}

    async def moving():
        box["x"] -= 0.02
        return (box["x"], -4.2, 0.0)

    monkeypatch.setattr(event_tour, "_where", moving)
    seen = {}

    async def then(b):
        assert await _until(lambda: "guest_1" in _walks(b["log"]))
        await asyncio.sleep(1.5)
        seen["ended"] = event_tour.STATE["ended"]
        await event_host.stop()

    _run(tour, then)
    assert seen["ended"] is None and tour["view"]["ended"] == "staff"


@pytest.mark.parametrize("when", ["handshake", "walking"])
def test_the_hand_down_button_ends_it(tour, when):
    """Review 23 Nov: "เอามือลง" took the debut's hand back but not the tour's later handshakes."""
    if when == "walking":
        tour["walkers"] = {3: SLOW}
    else:
        tour["hold"] = 3.0
    seen = {}

    async def then(box):
        if when == "walking":
            assert await _until(lambda: "guest_1" in _walks(box["log"]))
            await asyncio.sleep(0.3)
        else:
            assert await _until(lambda: len(_handshakes(box["log"])) >= 1)
            await asyncio.sleep(0.2)
        seen["at"] = len(box["log"])
        seen["out"] = await event_host.manual_gesture("pageant_end")

    _run(tour, then)
    after = tour["log"][seen["at"]:]
    assert [e for e in after if (e[0] == "walk" and e[1] in TOUR_SPOTS) or e == ("arm", "handshake")] == [], after
    assert seen["out"]["ok"] is True and seen["out"]["tour_ended"] is True
    assert tour["view"]["ended"] == "hand_down" and not event_walk.walking()


REAL_RUN = event_walk.ROOT / "data" / "events" / ".run"


def test_no_test_here_writes_this_machines_run_folder(held):
    """Review 23 Nov: a test wrote this PC's data/events/.run/tour.json. The folder the notes go to is the test's."""
    assert Path(event_walk.RUN_DIR).resolve() != REAL_RUN.resolve()

    def stamp():
        p = REAL_RUN / "tour.json"
        return p.stat().st_mtime_ns if p.exists() else None

    before = stamp()
    event_tour._note()
    event_tour._end(1, "done")
    assert stamp() == before and (Path(event_walk.RUN_DIR) / "tour.json").exists()


# The browser tests last: playwright's sync API keeps its own event loop running in this thread while the module's
# `chromium` fixture lives -- an asyncio.run() after them fails ("cannot be called from a running event loop").
# ---- the phone --------------------------------------------------------------------------------------------------

def _state(walk_on=True):
    snap = json.loads(json.dumps(event_host.snapshot(), default=str))
    snap["walk"]["enabled"] = walk_on
    snap["walk"]["health"] = {"known": True, "board": True, "battery": 80, "charging": False, "q": 60,
                              "lost": False, "low_battery": False, "docking": "off_dock"}
    snap["face"] = {"connected": True, "event_call": True, "ceremony_call": False}
    snap["mode"]["ceremony_app"] = False
    snap["mode"]["warnings"] = []
    return snap


@pytest.fixture
def served(tmp_path, monkeypatch):
    path = tmp_path / "floor1_event.json"
    path.write_text(TRACKED.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(settings, "event_tour", True)
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    yield path
    event_host.reset()


def test_the_card_says_what_one_press_does_and_what_it_will_not(chromium, served):  # noqa: F811
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        # no guest recorded: the counter's handshake, then ONE guest card 3's way and back (owner 23 Nov: "ส่วนจุดแขก
        # ตอนนี้ให้หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคนแล้วทักทายได้ไหม") -- said before the press, with where the guest stands
        box = page.text("#scene-debut")
        assert "3. ไปข้างคุณมิกิ ยื่นมือจับมือ (ไม่พูด)" in box and "4. " + event_tour.WALK_UP_STEP_TH in box, box
        assert "5. กลับข้างคุณมิกิ หันเข้าเคาน์เตอร์" in box and "ยืนรอที่ข้างคุณมิกิ" not in box, box
        assert event_tour.WALK_UP_MISSING_TH % ("แขก 1, แขก 2, แขก 3", "ข้างคุณมิกิ") in box, box
        # ... and she turns round at the counter, and at the guest to come back: staff look behind her
        assert ("หุ่นจะหมุนตัวช้าๆ อยู่กับที่ (มองไม่เห็นข้างหลัง ดูข้างหลังหุ่นให้ว่างครึ่งเมตร): "
                "ที่ข้างคุณมิกิ ก่อนไปหน้าแขก (หันหลังให้เคาน์เตอร์) · ที่หน้าแขก (หันหลังให้เคาน์เตอร์) ก่อนไปข้างคุณมิกิ") in box, box
        # the head: what the SERVER does, not "still" (the pageant and the robot's handshake move it: review 23 Nov)
        assert "หัวไม่หันตามคน ไม่ก้มหา" in box and "นิ่ง" not in box
        assert "ท่านางงามตอนเปิดตัวและท่าจับมือของหุ่นยังขยับหัวเอง" in box
        assert "กดปุ่ม E-stop บนตัวหุ่นแล้ว ให้กดหยุดสีแดงบนมือถือด้วย" in box
        # every guest recorded: the round, back to the counter, and what staff watch for
        _record()
        page.redraw(_state())
        page.page.wait_for_function("document.getElementById('scene-debut').innerText.indexOf('ไปจับมือแขก 3 จุด') >= 0")
        box = page.text("#scene-debut")
        assert "4. ไปจับมือแขก 3 จุด (แขก 1 → แขก 2 → แขก 3)" in box
        assert "5. กลับข้างคุณมิกิ หันเข้าเคาน์เตอร์" in box and "ยังไม่ได้บันทึกจุด: แขก" not in box
        # where she turns on the spot: from the recorded spots, not a sentence written for one layout
        assert ("หุ่นจะหมุนตัวช้าๆ อยู่กับที่ (มองไม่เห็นข้างหลัง ดูข้างหลังหุ่นให้ว่างครึ่งเมตร): "
                "ที่ข้างคุณมิกิ ก่อนไปแขก 1 · ที่แขก 3 ก่อนไปข้างคุณมิกิ") in box, box
        # walking off: the debut where she stands, nothing after it
        page.redraw(_state(walk_on=False))
        page.page.wait_for_function("document.getElementById('scene-debut').innerText.indexOf('การเดินปิด') >= 0")
        box = page.text("#scene-debut")
        assert "ไม่ไปเคาน์เตอร์/แขกต่อ" in box and "ไปข้างคุณมิกิ" not in box
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400
    finally:
        page.close()


def test_the_card_shows_the_tour_as_it_goes_and_where_it_stopped(chromium, served):  # noqa: F811
    _record()
    event_host.reset()
    plan = event_tour.planned(event_host.scene("debut")["tour"], event_host.load()["spots"])["steps"]
    event_tour.STATE.update(seq=1, scene="debut", plan=plan, at=3, phase="handshake", ended=None, since=time.time(),
                            missing=[])
    event_tour._hold(30.0)
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        page.page.wait_for_function("!document.getElementById('scene-tour-debut').hidden")
        text = page.text("#scene-tour-debut")
        assert "ทำต่อเนื่อง: ยื่นมือจับมือ รอแขนกลับท่าพัก — ยื่นมือจับมือ (แขก 1)" in text, text
        assert "ไปแขก 1 ✓" in text and "ยื่นมือจับมือ (แขก 1) ← ตอนนี้" in text
        # card 1 itself is not pressed over its own tour (the server refuses it too)
        go = page.page.locator("#scene-debut button.go")
        assert go.is_disabled() and page.text("#scene-debut button.go") == "กำลังทำต่อเนื่อง — กดหยุดสีแดงก่อนเริ่มใหม่"
        # the card's gestures are greyed with the reason while it runs
        assert page.page.locator("#manualBody button:not(.down)").first.is_disabled()
        assert "การ์ด 1 กำลังทำต่อเนื่องอยู่" in page.text("#manualWhy")
        # stopped there: why, where, and what is left by hand
        event_tour._end(1, "staff")
        page.redraw(_state())
        page.page.wait_for_function("document.getElementById('scene-tour-debut').innerText.indexOf('หยุดแล้ว') >= 0")
        text = page.text("#scene-tour-debut")
        assert "หยุดแล้ว: ทีมงานกดหยุด" in text and "ยื่นมือจับมือ (แขก 1) ✕ หยุดตรงนี้" in text
        assert not page.page.locator("#scene-debut button.go").is_disabled()           # over: card 1 again
        # what is left, one two-tap button each (second review 23 Nov: as the ceremony's card)
        assert "หุ่นไม่ทำต่อเอง — ทำต่อเองได้ (แตะสองครั้ง):" in text
        hands = page.page.locator("#scene-tour-debut button")
        assert [hands.nth(k).inner_text() for k in range(hands.count())] == [
            "ยื่นมือจับมือ (แขก 1)", "ไปแขก 2", "ยื่นมือจับมือ (แขก 2)", "ไปแขก 3", "ยื่นมือจับมือ (แขก 3)", "ไปข้างคุณมิกิ"]
        assert page.page.locator("#scene-tour-debut .thead.bad").count() == 1
        # the card's walk list reaches every spot it names
        walk = page.text("#manualWalk")
        for name in ("ไปข้างคุณมิกิ", "ไปแขก 1", "ไปแขก 2", "ไปแขก 3"):
            assert name in walk, walk
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400
    finally:
        page.close()
        event_tour.reset()
