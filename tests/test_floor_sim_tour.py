"""scripts/floor_sim.py: card 1's tour (owner 23 Nov 2026) walked in one simulated lobby -- app/event_tour.py's legs.

"การ์ดแรก ... จากนั้นไปหน้าเคาน์เตอร์แล้วจับมือมิกิ แล้วหันหลัง ไปจับมือกับแขกสักสามจุดใกล้ๆตรงนั้น แล้วก็กลับมา ... หน้าเคาน์เตอร์".
Its guest spots have no position yet: ASSUMED (a row west of the counter, each guest in front of her spot), said in
every label, replaced by positions a person gives (--spots). Needs the 16 Nov layout under .tmp/ (as test_floor_sim's
`today`): skipped, saying what is unchecked, without it.
"""
from __future__ import annotations

import json
import math

import pytest

from pathlib import Path

from app import event_host
from app import event_tour
from tests.test_floor_sim import fs, today as _today  # noqa: F401 -- a fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
#: The event file as these tests were written against it (23 Nov): guest spots unmarked, the counter at (-8.62, -4.2).
REHEARSED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"


@pytest.fixture
def today(_today, monkeypatch):  # noqa: F811
    """The simulated lobby, walked by the event file of 23 Nov (the tests' own numbers); the tracked file's own round is
    `test_the_owners_marked_round_is_walked_in_one_world`."""
    monkeypatch.setattr(fs, "EVENT_FILE", REHEARSED)
    event_host.reset()
    yield _today
    event_host.reset()


def _quiet(*_):
    pass


def test_card_1s_tour_is_walked_in_one_world(today):  # noqa: F811
    seqs = fs.tour_sequences(today)
    # nothing known outside the bounds since 23 Nov: the debut's arm rises out of the box, not on the dock; no guest
    # spot marked = also what the card does today, one guest card 3's way (its own test below)
    assert list(seqs) == [fs.TOUR_NAME, fs.TOUR_WALK_UP_NAME] and fs.TOUR_NAME not in fs.KNOWN_OUTSIDE
    legs = seqs[fs.TOUR_NAME]
    assert [leg["what"].split(":")[0] for leg in legs] == ["card 1 debut"] + ["card 1 (tour)"] * 5 + ["card 11 rest"]
    guests = [leg for leg in legs if leg.get("tour_guest")]
    assert len(guests) == 3 and all("ASSUMED place" in leg["what"] for leg in guests)   # nobody has marked them
    run = fs.run_sequence(today, legs, seed=3)
    assert len(run["parts"]) == len(legs) and all(p["end"] == "done" for p in run["parts"]), \
        [(p["what"], p["reason"]) for p in run["parts"]]
    sm = fs.tour_summary([run])
    assert fs.tour_faults(sm) == [], fs.tour_faults(sm)
    for g in sm["guests"]:
        assert 0.0 < g["hand_to_guest"] and g["front_to_guest"] <= fs.TOUR_FRONT_MAX_M, g
    back = sm["back"][0]
    assert fs.TOUR_BACK_FRONT_M[0] <= back["front_to_top"] <= fs.TOUR_BACK_FRONT_M[1] and back["face_err_deg"] < 10.0, back
    # her turns round: the counter -> guest 1 and guest 3 -> the counter start with our watched slow turn; the walk to
    # the counter turns at its head-on point (owner 23 Nov: "ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามา", ~107 deg -- ours)
    turned = [any(s.get("do") == "turn" for s in p["steps"]) for p in run["parts"]]
    assert turned == [False, True, True, False, False, True, False], turned
    assert all(p.get("rear_turns") == [] for p in run["parts"] if "rear_turns" in p)
    # touched nobody: the guests stand there all day, the arms keep clear of them on every walk
    assert min(p["min_person_gap"] for p in run["parts"]) > 0.0


def test_without_our_turn_the_gate_names_the_turn_round(today, monkeypatch):  # noqa: F811
    """The leg builder that forgot the watched turn: the board's MoveTo would turn her round blind -- a fault. Three:
    the counter -> guest 1, guest 3 -> the counter, and (since 23 Nov) the turn to the table at the counter's head-on
    point, ~107 deg off the way she comes."""
    def no_turn(spot, here, dock, head_on=False, via=None, glide=None, end=False):
        steps = event_host.spot_steps(spot)
        steps = event_tour.head_on_steps(spot, steps, here) if head_on else steps
        return event_host.route_from(steps, (here[0], here[1]), dock)

    monkeypatch.setattr(event_tour, "leg_steps", no_turn)
    legs = fs.tour_sequences(today)[fs.TOUR_NAME]
    run = fs.run_sequence(today, legs, seed=3)
    faults = fs.tour_faults(fs.tour_summary([run]))
    assert sum("a turn round with no watched turn first" in f for f in faults) == 3, faults
    assert any("(-9.62, -4.20) ends with the board's turn" in f for f in faults), faults


def test_the_walk_to_khun_mikis_hand_comes_straight_in_from_a_metre_out(today):  # noqa: F811
    """Owner 23 Nov, after card 1 ran on the robot: "ตอนเดินปรับเป็นเดินให้ไกลกว่าโต๊ะเคาน์เตอร์แล้วค่อยเดินเข้ามาจับมือกับ
    คุณมิกิ" (that run came north along the counter and turned to the table on the spot). In the simulated lobby the
    counter's walk -- card 1's and card 2's -- stops at the head-on point 1 m west of the counter's spot, turns to the
    table there, and drives the last metre straight in; the walk back after the guests comes as it was. Card 2's guest
    in the way stands on the walk the board now drives (the dock's approach -> the head-on point)."""
    legs = fs.tour_sequences(today)[fs.TOUR_NAME]
    run = fs.run_sequence(today, legs, seed=3)
    day = fs.event_day_sequences(today)
    card2 = fs.run_sequence(today, day[next(iter(day))], seed=3)
    for counter in (run["parts"][1], card2["parts"][1]):
        assert counter["end"] == "done", counter["reason"]
        assert [tuple(s["to"]) for s in counter["steps"] if s.get("to")][-2:] == [(-9.62, -4.2), (-8.62, -4.2)]
    # where she drove: to the head-on point, then along the line in, facing the table
    i0, i1 = run["parts"][1]["trace"]
    trace = run["trace"][i0:i1]
    k = min(range(len(trace)), key=lambda j: math.hypot(trace[j][1] + 9.62, trace[j][2] + 4.2))
    assert math.hypot(trace[k][1] + 9.62, trace[k][2] + 4.2) < 0.2
    last = [(x, y, yaw) for _, x, y, yaw in trace[k:] if x > -9.3]
    assert last and max(abs(math.degrees(yaw)) for _, _, yaw in last) < 12.0, last
    assert max(abs(y + 4.2) for _, y, _ in last) < 0.2, last
    # the walk back after the guests: no head-on point
    back = run["parts"][5]
    assert "back to the counter" in back["what"] and (-9.62, -4.2) not in [tuple(s["to"]) for s in back["steps"] if s.get("to")]
    # card 2's guest in the way: halfway along the board's MoveTo from the dock's approach to the head-on point
    spec = next(v for k, v in fs.in_the_way_cases(day[next(iter(day))], today).items() if k.startswith("card 2"))
    ax, ay = fs.route_doc(today, event=True)["dock"]["approach"]
    assert spec["people"][0]["at"] == pytest.approx(((ax - 9.62) / 2, (ay - 4.2) / 2))
    # the pre-flight walks card 2's way in from every spot she may be pressed at (beside the plain walk to the spot)
    walks = fs.event_plan(today)["walks"]
    assert "event dock -> beside_host" in walks and "event dock -> beside_host head-on" in walks
    assert (-9.62, -4.2) in [tuple(s["to"]) for s in walks["event dock -> beside_host head-on"][0] if s.get("to")]
    assert (-9.62, -4.2) not in [tuple(s["to"]) for s in walks["event dock -> beside_host"][0] if s.get("to")]


def test_guest_spots_not_marked_the_card_walks_up_to_one_guest(today, monkeypatch):  # noqa: F811
    """Owner 23 Nov: "ส่วนจุดแขกตอนนี้ให้หุ่นหันหลังแล้วเดินไปตรงๆก็เจอคนแล้วทักทายได้ไหม". With the guest spots not
    marked the card walks card 3's spot after Khun Miki's hand: turned round slowly at the counter, straight up to the
    guest (where card 3's is ASSUMED), stops in front -- board_walk says it met someone (`@@ met`: the hand only then)
    -- and back to the counter facing it. Nobody there: the whole 1.2 m, no meet, back. Marked spots: no walk-up."""
    legs = fs.tour_sequences(today)[fs.TOUR_WALK_UP_NAME]
    assert [leg["what"].split(":")[0] for leg in legs] == ["card 1 debut"] + ["card 1 (tour)"] * 3 + ["card 11 rest"]
    run = fs.run_sequence(today, legs, seed=3)
    assert all(p["end"] == "done" for p in run["parts"]), [(p["what"], p["reason"]) for p in run["parts"]]
    up = run["parts"][2]
    assert [s.get("do") for s in up["steps"] if s.get("do") == "turn"] and any("meet_m" in s for s in up["steps"])
    assert [e for e in up["events"] if "met" in e], up["events"]                       # the hand's go-ahead
    sm = fs.sequence_summary([run])
    assert fs.guest_faults(sm["guest"]) == [] and 0.0 < sm["guest"][0]["hand_to_guest"]
    tour = fs.tour_summary([run])
    assert fs.tour_faults(tour) == [] and tour["back"][0]["face_err_deg"] < 10.0, tour
    # nobody standing there: the whole walk, no meet -- and back
    nobody = [dict(leg) for leg in legs]
    nobody[2] = dict(nobody[2], guests=[])
    run = fs.run_sequence(today, nobody, seed=3)
    assert all(p["end"] == "done" for p in run["parts"]) and not [e for e in run["parts"][2]["events"] if "met" in e]
    assert run["parts"][2]["walked_to_guest_m"] > 1.1
    # the pre-flight walks it, and a fault there is NEW
    res = fs.preflight(runs=1, only="guest spots not marked", layout=today, adverse=False, out=_quiet)
    assert fs.TOUR_WALK_UP_NAME in res["walks"] and res["unexpected"] == [], res["unexpected"]
    monkeypatch.setattr(fs, "guest_faults", lambda rows: ["the handshake's hand reaches into card 3's guest (-0.01 m)"])
    res = fs.preflight(runs=1, only="guest spots not marked", layout=today, adverse=False, out=_quiet)
    assert [n for n, _ in res["unexpected"]] == [fs.TOUR_WALK_UP_NAME], res["unexpected"]
    # every guest spot marked: the round of them, no walk-up
    given = {g: {"x": -9.4, "y": y, "face_deg": 175.0, "name": g, "source": "the marking page"}
             for g, y in (("guest_1", -3.5), ("guest_2", -4.3), ("guest_3", -5.1))}
    assert list(fs.tour_sequences(today, given)) == [fs.TOUR_NAME]


def test_positions_a_person_gives_replace_the_assumed_guests(today):  # noqa: F811
    given = {g: {"x": -9.4, "y": y, "face_deg": 175.0, "name": g, "source": "the marking page"}
             for g, y in (("guest_1", -3.5), ("guest_2", -4.3), ("guest_3", -5.1))}
    legs = fs.tour_sequences(today, given)[fs.TOUR_NAME]
    guests = [leg for leg in legs if leg.get("tour_guest")]
    assert all("given (the marking page)" in leg["what"] for leg in guests)
    assert [leg["spot_pose"] for leg in guests] == [(-9.4, -3.5, 175.0), (-9.4, -4.3, 175.0), (-9.4, -5.1, 175.0)]
    # the guest stands in front of the given spot, as far as the assumed ones
    g = guests[0]["guest_at"]
    assert math.hypot(g[0] + 9.4, g[1] + 3.5) == pytest.approx(fs.FOOT_L / 2 + fs.TOUR_FRONT_TO_GUEST_M + fs.PERSON_R)


def test_the_tours_faults():
    ok = {"guests": [{"front_to_guest": 0.6, "hand_to_guest": 0.3, "face_err_deg": 2.0}],
          "back": [{"front_to_top": 0.7, "face_err_deg": 3.0}], "rear": []}
    assert fs.tour_faults(ok) == []
    assert "reaches into" in fs.tour_faults(dict(ok, guests=[dict(ok["guests"][0], hand_to_guest=-0.01)]))[0]
    assert "never reached" in fs.tour_faults(dict(ok, guests=[dict(ok["guests"][0], front_to_guest=1.2)]))[0]
    assert "facing" in fs.tour_faults(dict(ok, guests=[dict(ok["guests"][0], face_err_deg=30.0)]))[0]
    assert "back at the counter facing" in fs.tour_faults(dict(ok, back=[dict(ok["back"][0], face_err_deg=40.0)]))[0]
    assert "from the table's top" in fs.tour_faults(dict(ok, back=[dict(ok["back"][0], front_to_top=1.1)]))[0]
    assert "no watched turn" in fs.tour_faults(dict(ok, rear=["x: a MoveTo ..."]))[0]


def test_the_preflight_walks_card_1s_tour_and_a_fault_there_is_new(today, monkeypatch):  # noqa: F811
    res = fs.preflight(runs=1, only="card 1 tour", layout=today, adverse=False, out=_quiet)
    assert fs.TOUR_NAME in res["walks"] and res["unexpected"] == [], res["unexpected"]
    # nothing known outside since 23 Nov: the debut's arm rises once out of the box, not on the dock
    assert not any(n == fs.TOUR_NAME for n, _ in res["known_outside"])
    assert "tour" in res["walks"][fs.TOUR_NAME]
    monkeypatch.setattr(fs, "tour_faults", lambda sm: ["the handshake's hand reaches into a tour guest (-0.01 m)"])
    res = fs.preflight(runs=1, only="card 1 tour", layout=today, adverse=False, out=_quiet)
    assert any(n == fs.TOUR_NAME and any("tour guest" in b for b in bad) for n, bad in res["unexpected"]), res["unexpected"]


def test_no_tour_in_the_file_is_said_not_dropped(today, tmp_path, monkeypatch):  # noqa: F811
    doc = json.loads(fs.EVENT_FILE.read_text(encoding="utf-8"))
    for s in doc["scenes"]:
        s.pop("tour", None)
    path = tmp_path / "event.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(fs, "EVENT_FILE", path)
    assert fs.tour_sequences(today) == {}
    res = fs.preflight(runs=1, only="event dock -> beside_host", layout=today, adverse=False, out=_quiet)
    assert any(line.startswith("card 1's tour:") for line in res["not_checked"]), res["not_checked"]


def test_the_pictures_know_the_tours_walks():
    assert fs._walk_role("card 1 (tour): back to the counter (beside_host), facing it") == "back"
    assert fs._thai_walk("card 1 (tour): back to the counter (beside_host), facing it") == "กลับเคาน์เตอร์ (card 1 (tour))"
    assert fs._thai_walk("card 1 (tour): to the guest guest_2 (ASSUMED place), the handshake there") == \
        "ไปหาแขก guest_2 (card 1 (tour))"
    assert fs._walk_role("card 1 (tour): to the counter (beside_host), the handshake there") == "counter"


def test_every_event_walk_leaves_no_big_turn_to_the_board(today, monkeypatch):  # noqa: F811
    """Review 23 Nov: every walk to a spot (scenes, the card's "ไป<จุด>", the ceremony's walks) is built by the server's
    one builder -- no turn of more than 100 deg left to the board, at a MoveTo's start or end. The pre-flight names one."""
    plan = fs.event_plan(today)
    for name, (steps, start) in plan["walks"].items():
        if start[2] is not None:
            assert event_tour.rear_turns(steps, start) == [], (name, steps)

    def no_turn(spot, here, dock, **_):
        return event_host.route_from(event_host.spot_steps(spot), (here[0], here[1]), dock)

    monkeypatch.setattr(event_tour, "leg_steps", no_turn)
    res = fs.preflight(runs=1, only="event beside_host -> lobby_west", layout=today, adverse=False, out=_quiet)
    assert any("a turn the board makes blind" in b for _, bad in res["unexpected"] for b in bad), res["unexpected"]


def test_the_owners_marked_round_is_walked_in_one_world(_today, monkeypatch):  # noqa: F811
    """Owner 24 Nov evening: five marks on the map, out of the box 1.3 m, straight on and a right angle to Khun Miki, three
    guests, and to the spot beside him. The tracked file itself, in the simulated lobby of 16 Nov (not a measurement)."""
    monkeypatch.setattr(fs, "EVENT_FILE", ROOT / "data" / "events" / "floor1_event.json")
    event_host.reset()
    try:
        seqs = fs.tour_sequences(_today)
        legs = seqs[fs.TOUR_NAME]
        guests = [leg for leg in legs if leg.get("tour_guest")]
        assert len(guests) == 3 and all("ASSUMED" not in leg["what"] for leg in guests), [leg["what"] for leg in guests]
        assert fs.TOUR_WALK_UP_NAME not in seqs                          # every guest is marked
        run = fs.run_sequence(_today, legs, seed=3)
        assert len(run["parts"]) == len(legs) and all(p["end"] == "done" for p in run["parts"]), \
            [(p["what"], p["reason"]) for p in run["parts"]]
        sm = fs.tour_summary([run])
        assert fs.tour_faults(sm) == [], fs.tour_faults(sm)
        for g in sm["guests"]:
            assert 0.0 < g["hand_to_guest"] and g["front_to_guest"] <= fs.TOUR_FRONT_MAX_M, g
        assert sm["back"][0]["end_err_m"] <= fs.TOUR_END_TOL_M and sm["back"][0]["face_err_deg"] < 10.0
        # straight on to the corner, turned right there, straight up Khun Miki's line
        steps = [s for s in run["parts"][1]["steps"] if s.get("glide")]
        assert [tuple(p) for p in steps[0]["glide"]] == [(-9.37, -6.35), (-8.92, -4.24)] and steps[0]["face"] == 0.0, steps
        # every walk after the debut is one glide: the board's own arrival nowhere in the round (owner 24 Nov)
        assert all([s for s in p["steps"] if s.get("glide")] and not [s for s in p["steps"] if s.get("to")]
                   for p in run["parts"][1:6]), [p["steps"] for p in run["parts"][1:6]]
        assert min(p["min_person_gap"] for p in run["parts"]) > 0.0
    finally:
        event_host.reset()
