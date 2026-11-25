"""Card 1 with a line at Khun Miki's handshake and an end spot of its own (app/event_tour.py, owner 24 Nov 2026)."""
from __future__ import annotations

import time

import pytest

from app import event_host, event_tour, event_walk
from tests.test_event_host import event, stage  # noqa: F401 -- fixtures, used by name
from tests.test_event_tour import GUESTS, _handshakes, _run, _until, _walks, tour  # noqa: F401 -- fixture + helpers


# ---- 24 Nov 2026: a line at Khun Miki's handshake, and an end spot of its own ---------------------------------
# Owner: "จับมือแล้วหุ่นก็พูดว่า hello ka khun miki ... แล้วสุดท้ายก็เดินมาข้างคุณมิกิที่เคาร์เตอร์"

LINE = {"file": "data/events/clips/miki.wav", "sha256": "a" * 64, "text": "Hello ka, Khun Miki!", "lang": "en"}


def _with(monkeypatch, **extra):
    """The tracked tour, with `lines` / `end` as an event file would give them."""
    real = event_tour.parse
    monkeypatch.setattr(event_tour, "parse", lambda raw, spots: real({**raw, **extra} if isinstance(raw, dict) else raw, spots))
    event_host.reset()


def _says(tour, monkeypatch, *, pcm=b"\1\0", ok=True, voice=0.0):
    """play_line faked: logged in order with the walks and the arm, her voice on for `voice` s after it."""
    from app import event_cues

    tour["said"], until = [], {"t": 0.0}

    async def play_line(line_id, text, pcm_, *, lang="en", scene_th=None, still=None):
        if not line_id.startswith("tour_"):              # the debut's own recorded line: the same road, not this test's
            return {"ok": ok, "error": None if ok else "no_call"}
        tour["log"].append(("line", line_id))
        tour["said"].append({"id": line_id, "text": text, "lang": lang, "still": still()})
        until["t"] = time.monotonic() + voice
        return {"ok": ok, "error": None if ok else "no_call"}

    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (pcm, None) if pcm else (None, "missing"))
    real = event_tour._her_voice_on
    monkeypatch.setattr(event_tour, "_her_voice_on", lambda: until["t"] > time.monotonic() or real())
    monkeypatch.setattr(event_cues, "play_line", play_line)


def test_a_tours_lines_and_end_are_read_and_anything_else_left_out(event):  # noqa: F811
    spots = event_host.load()["spots"]
    raw = {"counter": "beside_host", "guests": ["guest_1", "guest_2", "guest_3"]}
    plain = event_tour.parse(raw, spots)
    assert "lines" not in plain and "end" not in plain                # no `lines`, no `end`: the tour as it was
    got = event_tour.parse({**raw, "end": "table_1", "lines": {"beside_host": LINE, "guest_2": LINE}}, spots)
    assert got["end"] == "table_1" and set(got["lines"]) == {"beside_host", "guest_2"}
    assert got["lines"]["beside_host"]["text"] == "Hello ka, Khun Miki!"
    bad = event_tour.parse({**raw, "end": "guest_2", "lines": {"table_2": LINE, "beside_host": {**LINE, "sha256": "x"}}}, spots)
    assert bad == plain, "a guest is no end, a line for no handshake and an unpinned one are no lines"
    for end in ("dock", "nowhere", 3):
        assert "end" not in event_tour.parse({**raw, "end": end}, spots)
    assert "lines" not in event_tour.parse({**raw, "lines": ["beside_host"]}, spots)


def test_the_round_ends_at_its_own_spot_once_that_is_marked_and_at_the_counter_until_then():
    spots = {"beside_host": {"name_th": "ข้างคุณมิกิ", "pose": [-8.62, -4.2, 0.0]}, "end": {"name_th": "จุดจบ"},
             **{g: {"name_th": g, "pose": list(p)} for g, p in GUESTS.items()}}
    t = {"counter": "beside_host", "guests": list(GUESTS), "staff_th": "", "walk_up": None, "end": "end",
         "lines": {"beside_host": LINE}}
    last = event_tour.planned(t, spots)["steps"][-1]
    assert (last["kind"], last["spot"], last["back"]) == ("walk", "beside_host", True)
    spots["end"]["pose"] = [-8.5, -4.9, 0.0]
    steps = event_tour.planned(t, spots)["steps"]
    assert (steps[-1]["kind"], steps[-1]["spot"], steps[-1]["back"]) == ("walk", "end", True)
    hands = [s for s in steps if s["kind"] == "handshake"]
    assert [bool(s.get("line")) for s in hands] == [True, False, False, False], "Khun Miki's alone"


def test_the_line_is_said_as_the_hand_goes_out_and_her_voice_is_over_before_the_next_walk(tour, monkeypatch):
    _with(monkeypatch, lines={"beside_host": LINE})
    _says(tour, monkeypatch, voice=0.8)
    stamps = []
    real = event_walk.start

    async def start(spot, name, steps, **kw):
        stamps.append((spot, event_tour._her_voice_on()))
        return await real(spot, name, steps, **kw)

    monkeypatch.setattr(event_walk, "start", start)
    _run(tour)
    log = tour["log"]
    assert [s["id"] for s in tour["said"]] == ["tour_beside_host"], tour["said"]
    assert tour["said"][0]["text"] == "Hello ka, Khun Miki!" and tour["said"][0]["still"] is True
    k = log.index(("line", "tour_beside_host"))
    assert ("arm", "handshake") in log[:k] and log[k + 1] == ("arm_end", "done"), log      # out, then said, then down
    assert dict(stamps)["guest_1"] is False, "the walk to the first guest waited for her voice"
    assert tour["view"]["ended"] == "done" and len(_handshakes(log)) == 4


@pytest.mark.parametrize("pcm,ok", [(None, True), (b"\1\0", False)])
def test_a_line_that_cannot_be_played_is_silence_and_the_tour_goes_on(tour, monkeypatch, pcm, ok):
    _with(monkeypatch, lines={"beside_host": LINE})
    _says(tour, monkeypatch, pcm=pcm, ok=ok)
    _run(tour)
    assert tour["view"]["ended"] == "done" and len(_handshakes(tour["log"])) == 4
    assert len([e for e in tour["stage"]["log"] if e[0] == "say"]) == 1, "only the debut's own line reached the model"


def test_a_stop_while_she_says_it_ends_the_tour_before_the_next_walk(tour, monkeypatch):
    _with(monkeypatch, lines={"beside_host": LINE})
    _says(tour, monkeypatch, voice=30.0)

    async def stop(box):
        assert await _until(lambda: box["said"])
        assert await _until(lambda: ("arm_end", "done") in box["log"])
        await event_host.stop()

    _run(tour, then=stop)
    assert "guest_1" not in _walks(tour["log"]), tour["log"]
    assert tour["view"]["ended"] not in ("done", None)


# ---- the owner's marked round: the event file itself (24 Nov evening) -------------------------------------------
# "แต่จุดที่ออกตัวไปจุดแรกให้ตั้งฉากนะ ไปยืนแนวนั้นแล้วก็ค่อยหันขวา" + five marks on the map (mark_spots_round.html)

MARKS = {"beside_host": (-8.92, -4.24, 0.0), "guest_1": (-9.53, -2.09, 116.0), "guest_2": (-8.88, -1.91, 100.0),
         "guest_3": (-8.23, -1.82, 63.5), "screen_end": (-10.9, -7.18, 45.0)}


def _real():
    import json
    from pathlib import Path

    doc = json.loads((Path(__file__).resolve().parents[1] / "data/events/floor1_event.json").read_text(encoding="utf-8"))
    out = event_host._normalise(doc, {})
    return out, next(sc for sc in out["scenes"] if sc["id"] == "debut")


def test_the_event_files_round_is_the_owners_marks_in_his_order():
    doc, debut = _real()
    assert {k: tuple(event_tour.spot_pose(doc["spots"][k])) for k in MARKS} == MARKS
    plan = event_tour.planned(debut["tour"], doc["spots"])
    assert plan["missing"] == [] and plan["guests_ok"]
    assert [(s["kind"], s["spot"]) for s in plan["steps"]] == [
        ("walk", "beside_host"), ("handshake", "beside_host"), ("walk", "guest_1"), ("handshake", "guest_1"),
        ("walk", "guest_2"), ("handshake", "guest_2"), ("walk", "guest_3"), ("handshake", "guest_3"),
        ("walk", "screen_end")]
    # card 3 walks on from Khun Miki's spot: its first point is that spot's
    assert doc["spots"]["guest_front"]["steps"][0]["to"] == [-8.92, -4.24]


def test_the_way_to_khun_miki_is_a_right_angle_straight_out_then_right_then_up_his_line():
    import math

    doc, debut = _real()
    tour, spot = debut["tour"], doc["spots"]["beside_host"]
    dock, out_deg = (-6.86, -6.88), 167.0                            # the charger's contacts, 23 Nov
    here = (dock[0] + debut["forward_m"] * math.cos(math.radians(out_deg)),
            dock[1] + debut["forward_m"] * math.sin(math.radians(out_deg)), out_deg)
    steps = [s for s in event_tour.leg_steps(spot, here, {"pose": [*dock, out_deg], "approach": [-9.0, -6.2],
                                                          "leave_by_approach": True}, via=tour["via"]) if s.get("to")]
    assert len(steps) == 2 and "face" in steps[0], steps                  # no approach point, no head-on point
    corner, to = steps[0]["to"], steps[1]["to"]
    a = math.degrees(math.atan2(corner[1] - here[1], corner[0] - here[0]))
    b = math.degrees(math.atan2(to[1] - corner[1], to[0] - corner[0]))
    assert abs(event_tour._wrap_deg(a - out_deg)) < 4.0, a                # straight on, as she stands
    assert abs(event_tour._wrap_deg(steps[0]["face"] - b)) < 2.0          # she faces his line before she goes up it
    turn = event_tour._wrap_deg(b - a)
    assert -95.0 < turn < -85.0, turn                                     # a right angle, to the right
    assert steps[1]["face"] == 0 and event_tour._wrap_deg(0 - b) < 0      # and right again, to Khun Miki


def test_a_tour_without_via_walks_to_the_counter_as_it_did():
    spots = {"beside_host": {"name_th": "ข้างคุณมิกิ", "head_on_m": 1.0,
                             "steps": [{"to": [-8.62, -4.2], "face": 0, "speed": 0.45}]},
             **{g: {"name_th": g, "pose": list(p)} for g, p in GUESTS.items()}}
    t = {"counter": "beside_host", "guests": list(GUESTS), "staff_th": "", "walk_up": None}
    plain = event_tour.planned(t, spots)["steps"][0]
    assert plain["head_on"] is True and "via" not in plain
    got = event_tour.planned({**t, "via": [[-9.37, -6.35, 78.0]]}, spots)["steps"][0]
    assert got["head_on"] is True and got["via"] == [[-9.37, -6.35, 78.0]], "the by-hand walk of this spot keeps head-on"
    walked = event_tour.leg_steps(spots["beside_host"], (-8.1, -6.6, 167.0), None, head_on=True, via=got["via"])
    assert [tuple(s["to"]) for s in walked if s.get("to")] == [(-9.37, -6.35), (-8.62, -4.2)], "the corner, not the head-on point"
    assert [tuple(s["to"]) for s in event_tour.leg_steps(spots["beside_host"], (-8.1, -6.6, 167.0), None, head_on=True)
            if s.get("to")] == [(-9.62, -4.2), (-8.62, -4.2)], "and without the corner the head-on point"
    here = (-8.1, -6.6, 167.0)
    assert event_tour.leg_steps(spots["beside_host"], here, None, head_on=True) == \
        event_tour.leg_steps(spots["beside_host"], here, None, head_on=True, via=None)


@pytest.mark.parametrize("raw", [[], [[1, 2]], [[1, 2, "x"]], "x", [[1, 2, True]], [[1.0, 2.0, 3.0]] * 4, [[1, 2, float("nan")]]])
def test_a_via_that_is_not_points_is_none(event, raw):  # noqa: F811
    spots = event_host.load()["spots"]
    base = {"counter": "beside_host", "guests": ["guest_1", "guest_2", "guest_3"]}
    assert "via" not in event_tour.parse({**base, "via": raw}, spots)
    assert event_tour.parse({**base, "via": [[-9.37, -6.35, 78]]}, spots)["via"] == [[-9.37, -6.35, 78.0]]


# ---- review of 24 Nov 21:27 -------------------------------------------------------------------------------------

@pytest.mark.parametrize("here,kept", [((-8.1, -6.6, 167.0), True), ((-8.1, -6.6, 120.0), False), ((-8.9, -5.0, 90.0), False),
                                       ((-9.38, -6.35, 78.0), True), ((-8.1, -6.6, None), True)])
def test_a_corner_that_is_not_ahead_of_her_is_left_out(here, kept):
    """The box put down elsewhere on the day, or a walk out that ended short: no sideways start for an absolute point."""
    spot = {"name_th": "ข้างคุณมิกิ", "head_on_m": 1.0, "steps": [{"to": [-8.62, -4.2], "face": 0, "speed": 0.45}]}
    steps = event_tour.leg_steps(spot, here, None, head_on=True, via=[[-9.37, -6.35, 78.0]])
    points = [tuple(s["to"]) for s in steps if s.get("to")]
    assert ((-9.37, -6.35) in points) is kept, (here, points)


def test_the_way_home_from_the_end_spot_starts_with_a_watched_turn():
    dock = {"pose": [-7.1, -6.84, 168.0], "approach": [-9.0, -6.2], "back_from_m": 1.9}
    home = {"dock": True, "name_th": "แท่น"}
    steps = event_tour.leg_steps(home, (-8.56, -5.18, 90.0), dock)
    turns = [s for s in steps if s.get("do") == "turn"]
    assert turns and steps.index(turns[0]) < next(i for i, s in enumerate(steps) if s.get("do") == "dock"), steps
    # a home that is ahead of her needs none
    assert not [s for s in event_tour.leg_steps(home, (-8.56, -5.18, -100.0), dock) if s.get("do") == "turn"]


def test_a_line_waits_for_its_own_handshake_only(tour, monkeypatch):  # noqa: F811
    """A line that was held by the announcement lock is not said at the next guest: `still` is the step's too."""
    _with(monkeypatch, lines={"beside_host": LINE})
    asked = {}

    async def play_line(line_id, text, pcm, *, lang="en", scene_th=None, still=None):
        if line_id.startswith("tour_"):
            asked["now"] = still()
            event_tour.STATE["at"] = 99
            asked["moved_on"] = still()
            event_tour.STATE["at"] = 1
        return {"ok": True, "error": None}

    from app import event_cues

    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\1\0", None))
    monkeypatch.setattr(event_cues, "play_line", play_line)
    _run(tour)
    assert asked == {"now": True, "moved_on": False}, asked


def test_a_walk_with_the_corner_is_driven_by_it_through_the_server(tour, monkeypatch):  # noqa: F811
    """The tour's own walk to Khun Miki hands board_walk the corner (a plan that names it and a leg that forgets it are
    the same to the plan's test): the robot stands a way ahead of it, as after the walk out."""
    _with(monkeypatch, via=[[-9.37, -6.35, 78.0]])

    async def where():
        return (-8.0, -6.6, 167.0)

    monkeypatch.setattr(event_tour, "_where", where)
    _run(tour)
    first = [s for s in tour["routes"][1] if s.get("to")]
    assert [tuple(s["to"]) for s in first][:1] == [(-9.37, -6.35)], tour["routes"][1]


def test_her_voice_that_never_ends_does_not_hold_the_round(tour, monkeypatch):  # noqa: F811
    _with(monkeypatch, lines={"beside_host": LINE})
    _says(tour, monkeypatch, voice=60.0)
    monkeypatch.setattr(event_tour, "LINE_OVER_S", 0.6)
    _run(tour)
    assert tour["view"]["ended"] == "done", "the next walk waits for her voice at most LINE_OVER_S"


def test_the_events_files_of_a_tours_lines_are_shipped(tmp_path):
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("robot_server_lines", root / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    (tmp_path / "data/events/clips").mkdir(parents=True)
    (tmp_path / "data/events/clips/miki.wav").write_bytes(b"x")
    (tmp_path / "data/events/floor1_event.json").write_text(json.dumps({"scenes": [
        {"id": "debut", "tour": {"lines": {"beside_host": {**LINE, "file": "data/events/clips/miki.wav"}}}}]}), encoding="utf-8")
    assert rs.event_clip_files(tmp_path) == {"data/events/clips/miki.wav"}


def test_a_spots_show_lines_are_shipped(tmp_path):
    """Rehearsal 4 (25 Nov 03:04): the globe's tease and reveal were never deployed -- show_line said=false error=missing."""
    import importlib.util
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("robot_server_show", root / "scripts" / "robot_server.py")
    rs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rs)
    (tmp_path / "data/events/clips").mkdir(parents=True)
    for n in ("tease.wav", "reveal.wav"):
        (tmp_path / "data/events/clips" / n).write_bytes(b"x")
    (tmp_path / "data/events/floor1_event.json").write_text(json.dumps({"scenes": [], "spots": {
        "beside_globe": {"show": {"first": {**LINE, "file": "data/events/clips/tease.wav"},
                                  "arrive": {**LINE, "file": "data/events/clips/reveal.wav"}, "gesture": "raise_right_arm"}},
        "other": {"show": {"first": {**LINE, "file": "../secret.wav"}}}, "plain": {"pose": [0, 0, 0]}}}), encoding="utf-8")
    assert rs.event_clip_files(tmp_path) == {"data/events/clips/tease.wav", "data/events/clips/reveal.wav"}
    real = rs.event_clip_files(root)
    doc = json.loads((root / "data/events/floor1_event.json").read_text(encoding="utf-8"))
    for k in ("first", "arrive"):
        f = doc["spots"]["beside_globe"]["show"][k]["file"]
        assert f in real or not (root / f).is_file(), f


# ---- glides, the music, and the arm after a handshake (owner 24 Nov 22:28) ---------------------------------------------

def test_the_rounds_walks_are_glides_and_the_other_walks_are_not():
    doc, debut = _real()
    assert debut["tour"]["glide"] == 0.5
    here = (-8.15, -6.65, 168.0)
    for st in event_tour.planned(debut["tour"], doc["spots"])["steps"]:
        if st["kind"] != "walk":
            continue
        spot = doc["spots"][st["spot"]]
        steps = event_tour.leg_steps(spot, here, None, head_on=bool(st.get("head_on")), via=st.get("via"),
                                     glide=st.get("glide"))
        assert [s for s in steps if s.get("glide")] and not [s for s in steps if s.get("to") or s.get("do") == "turn"], steps
        assert steps[-1]["speed"] == 0.5 and steps[-1]["face"] == event_tour.spot_pose(spot)[2]
        # the same spot walked to by anything else (a card's own walk, by hand): the board's, with its watched turns
        assert not [s for s in event_tour.leg_steps(spot, here, None) if s.get("glide")]
        here = event_tour.spot_pose(spot)


def test_a_glide_speed_is_kept_within_its_bounds_and_a_wrong_one_is_none():
    spots = {"c": {"name_th": "c", "pose": [0, 0, 0]}, "g": {"name_th": "g", "pose": [1, 1, 0]}}
    base = {"counter": "c", "guests": ["g"]}
    for raw, want in ((0.42, 0.42), (2, event_tour.GLIDE_MAX), (0.01, event_tour.GLIDE_MIN), ("x", None), (True, None)):
        got = event_tour.parse({**base, "glide": raw}, spots)
        assert (got.get("glide") if got else "none") == want, raw
    plain = event_tour.planned(event_tour.parse(base, spots), spots)
    assert not [s for s in plain["steps"] if "glide" in s]


def test_a_walk_with_anything_else_in_it_is_not_made_a_glide():
    for other in ({"do": "dock"}, {"forward": 1.0}, {"to": [1, 2], "fallback": [{"to": [1, 1]}]}, {"do": "wait", "s": 1}):
        steps = [{"to": [0.0, 1.0], "speed": 0.4}, other]
        assert event_tour.glide_steps(steps, 0.4, "x") is steps
    assert event_tour.glide_steps([{"do": "turn", "face": 10}], 0.4, "x") == [{"do": "turn", "face": 10}]
    got = event_tour.glide_steps([{"do": "undock"}, {"do": "turn", "face": 90}, {"to": [1, 2], "face": 5},
                                  {"to": [3, 4], "face": 7}, {"do": "turn", "face": 12.5, "end": True},
                                  {"do": "turn", "face": 80, "end": True}], 0.4, "x")
    assert got == [{"do": "undock"}, {"glide": [[1.0, 2.0], [3.0, 4.0]], "speed": 0.4, "label": "x", "face": 80.0}]


def test_the_music_never_ends_anything_and_is_off_without_its_switch(monkeypatch):
    import types

    from app import face_mirror

    calls = []
    monkeypatch.setattr(event_tour, "settings", types.SimpleNamespace(catwalk_music=False))
    assert event_tour.music("walk") is False
    monkeypatch.setattr(event_tour, "settings", types.SimpleNamespace(catwalk_music=True))
    monkeypatch.setattr(face_mirror, "catwalk", lambda op, **kw: calls.append((op, kw)) or True, raising=False)
    assert event_tour.music("walk", track="guests") is True and calls == [("walk", {"track": "guests"})]
    monkeypatch.setattr(face_mirror, "catwalk", lambda op, **kw: False, raising=False)
    assert event_tour.music("hit") is False              # nothing was playing: no quiet to wait for

    def broken(op, **kw):
        raise RuntimeError("screen")

    monkeypatch.setattr(face_mirror, "catwalk", broken, raising=False)
    assert event_tour.music("off") is False


def test_a_handshake_just_over_stands_for_the_walks_arm_rest_and_nothing_else_does(monkeypatch):
    import time
    import types

    from app import event_pageant, event_walk, robot_body

    monkeypatch.setattr(event_walk, "settings", types.SimpleNamespace(event_walk_arm_rest=True))
    monkeypatch.setattr(event_pageant, "active", lambda *a: False)
    now = time.time()

    def arm(action, state, at):
        monkeypatch.setattr(robot_body, "STATE", {"last_action": action, "state": state, "at": at})

    arm("handshake", "done", now - 1)
    assert event_walk._rest_holds(now - 1, "handshake") is True
    assert event_walk._rest_holds(now - 1) is False                           # only the tour's own walk after it says so
    arm("handshake", "started", now - 1)
    assert event_walk._rest_holds(now - 1, "handshake") is False              # the hand still out
    arm("pageant", "done", now)
    assert event_walk._rest_holds(now - 1, "handshake") is False              # another gesture since
    arm("handshake", "done", now - 61)
    assert event_walk._rest_holds(now - 61, "handshake") is False             # too long ago
    arm("handshake", "done", now - 1)
    monkeypatch.setattr(event_pageant, "active", lambda *a: True)
    assert event_walk._rest_holds(now - 1, "handshake") is False
    assert event_walk._rest_holds(None, "handshake") is False


def test_a_whole_card_1_starts_card_2_by_itself_and_nothing_else_does(monkeypatch):
    """Owner 25 Nov 00:09: at card 2's spot the robot starts card 2 itself (app/event_order.follow_after_opening, the order's
    own press). Only after the WHOLE round ("done"); a missing or failing hook is never card 1's failure."""
    import asyncio

    from app import event_order

    calls = []

    async def follow():
        calls.append(1)
        return {"ok": True}

    monkeypatch.setattr(event_order, "follow_after_opening", follow, raising=False)
    asyncio.run(event_tour._card_two(7))
    assert calls == [1]

    def broken():
        raise RuntimeError("order")

    monkeypatch.setattr(event_order, "follow_after_opening", broken, raising=False)
    asyncio.run(event_tour._card_two(7))                  # logged, nothing raised
    monkeypatch.delattr(event_order, "follow_after_opening", raising=False)
    asyncio.run(event_tour._card_two(7))
    src = __import__("inspect").getsource(event_tour._ceremony_run)
    assert 'STATE.get("ended") == "done"' in src and "_card_two(seq)" in src


# ---- card 3 (owner 25 Nov 01:57): the globe's show, and the catwalk music on a flagged spot's walk ----------------------

def test_the_globe_spot_says_its_tease_first_and_on_arrival_raises_the_hand_and_says_the_reveal(monkeypatch):
    import asyncio
    import types

    from app import event_cues, event_roam, event_walk

    log = []
    rec = {"file": "x.wav", "sha256": "0" * 64, "text": "t", "lang": "en"}
    spot = {"name_th": "ลูกโลก", "pose": [-6.32, 0.33, 180],
            "show": {"first": dict(rec, text="tease"), "gesture": "raise_right_arm", "arrive": dict(rec, text="reveal")}}
    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"beside_globe": spot}})
    monkeypatch.setattr(event_host, "_recorded_entry", lambda raw: raw["recorded"])
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\0" * 10, None))

    async def play_line(cid, text, pcm, **kw):
        log.append(("say", text))
        return {"ok": True}

    async def nothing(*a, **k):
        return None

    async def gesture(action, **k):
        log.append(("gesture", action))
        return {"ok": True}

    ends = []

    async def start(spot_id, name, steps, *, dock, on_end, stops_seen=None, **k):
        log.append(("walk", spot_id))
        ends.append(on_end)
        return {"ok": True, "run": {"id": "r"}}

    async def steps(*a, **k):
        return [{"to": [0, 0]}]

    monkeypatch.setattr(event_cues, "play_line", play_line)
    monkeypatch.setattr(event_tour, "_her_voice_on", lambda: False)
    monkeypatch.setattr(event_roam, "stop", nothing)
    monkeypatch.setattr(event_host.event_pageant, "end", nothing)
    monkeypatch.setattr(event_walk, "stop", nothing)
    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(event_walk, "stops", lambda: 0)
    monkeypatch.setattr(event_host, "walk_steps", steps)
    monkeypatch.setattr(event_host, "_gesture", gesture)
    monkeypatch.setattr(event_host, "_count_only", nothing)
    run = types.SimpleNamespace(id="r", state="walking")
    monkeypatch.setattr(event_walk, "current", lambda: run)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    monkeypatch.setattr(event_tour, "music", lambda op, **kw: log.append(("music", op)) or True)
    from app import face_mirror

    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: log.append(("screen", slide, cue)))

    async def go():
        out = await event_host.go_spot("beside_globe")
        for _ in range(50):                               # the line goes over the walk: a task of its own
            if ("music", "walk") in log:
                break
            await asyncio.sleep(0.01)
        return out
    assert asyncio.run(go())["ok"]
    # owner after rehearsal 5: "การ์ด 3 ตอนพูดก็เดินไปด้วยเลยไหม" -- the walk first, the tease over it; after round 6:
    # "รอการ์ด 3 พูดแล้วเปิดเพลงรอบเดียว" -- the music ONCE, after her line
    # and card 2's slide down first (owner ~04:40: "ตอนไปการ์ด 3 จอก็ไม่ควรขึ้นของการ์ด 2 ไหม")
    assert log == [("screen", None, None), ("walk", "beside_globe"), ("say", "tease"), ("music", "walk")], log
    del log[:]
    asyncio.run(ends[0](types.SimpleNamespace(state="stopped")))
    assert log == [], "not arrived: no hand, no reveal"
    asyncio.run(ends[0](types.SimpleNamespace(state="arrived")))
    assert log == [("gesture", "raise_right_arm"), ("music", "duck"), ("say", "reveal")], log


def test_a_tease_not_said_after_a_refused_walk(monkeypatch):
    """The walk refused (arm, call, page): no line over a walk that is not there."""
    import asyncio

    from app import event_cues, event_roam, event_walk

    said = []
    rec = {"file": "x.wav", "sha256": "0" * 64, "text": "t", "lang": "en"}
    spot = {"name_th": "ลูกโลก", "pose": [-6.32, 0.33, 180], "show": {"first": dict(rec, text="tease")}}
    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"beside_globe": spot}})
    monkeypatch.setattr(event_host, "_recorded_entry", lambda raw: raw["recorded"])   # a take that WOULD play
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\0" * 10, None))
    monkeypatch.setattr(event_tour, "_her_voice_on", lambda: False)

    async def nothing(*a, **k):
        return None

    async def start(*a, **k):
        return {"ok": False, "error": "arm_not_rest", "error_th": "x"}

    async def steps(*a, **k):
        return [{"to": [0, 0]}]

    async def play_line(*a, **k):
        said.append(a[0])
        return {"ok": True}

    monkeypatch.setattr(event_cues, "play_line", play_line)
    monkeypatch.setattr(event_roam, "stop", nothing)
    monkeypatch.setattr(event_host.event_pageant, "end", nothing)
    monkeypatch.setattr(event_walk, "stop", nothing)
    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(event_host, "walk_steps", steps)

    async def go():
        out = await event_host.go_spot("beside_globe")
        await asyncio.sleep(0.05)
        return out
    out = asyncio.run(go())
    assert out["ok"] is False and out["error"] == "arm_not_rest" and said == []


def test_the_tease_is_cut_when_its_walk_ends_without_arriving(monkeypatch):
    """Review 25 Nov 03:3x: only a person's stop moved `stops()` -- B, the page quiet, the board giving up or another
    walk pressed left "Emma will take you there!" playing over a robot standing still."""
    import asyncio
    import types

    from app import event_cues, event_roam, event_walk

    log = []
    rec = {"file": "x.wav", "sha256": "0" * 64, "text": "t", "lang": "en"}
    spot = {"name_th": "ลูกโลก", "pose": [-6.32, 0.33, 180], "show": {"first": dict(rec, text="tease")}}
    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"beside_globe": spot}})
    monkeypatch.setattr(event_host, "_recorded_entry", lambda raw: raw["recorded"])
    monkeypatch.setattr(event_host, "recorded_pcm", lambda sc: (b"\0" * 10, None))
    monkeypatch.setattr(event_tour, "_her_voice_on", lambda: False)
    run = types.SimpleNamespace(id="r", state="walking")
    monkeypatch.setattr(event_walk, "current", lambda: run)
    monkeypatch.setattr(event_walk, "walking", lambda: run.state == "walking")
    monkeypatch.setattr(event_walk, "stops", lambda: 0)
    monkeypatch.setattr(event_tour, "music", lambda op, **kw: log.append(("music", op)) or True)
    released = {}

    async def play_line(cid, text, pcm, **kw):
        log.append(("say", text))
        released["ev"] = asyncio.Event()
        await released["ev"].wait()                      # her voice going on until cut
        return {"ok": True}

    async def cut(why="staff"):
        log.append(("cut", why))
        released["ev"].set()
        return True

    async def nothing(*a, **k):
        return None

    async def start(*a, **k):
        return {"ok": True, "run": {"id": "r"}}

    async def steps(*a, **k):
        return [{"to": [0, 0]}]

    monkeypatch.setattr(event_cues, "play_line", play_line)
    monkeypatch.setattr(event_cues, "cut", cut)
    monkeypatch.setattr(event_roam, "stop", nothing)
    monkeypatch.setattr(event_host.event_pageant, "end", nothing)
    monkeypatch.setattr(event_walk, "stop", nothing)
    monkeypatch.setattr(event_walk, "start", start)
    monkeypatch.setattr(event_host, "walk_steps", steps)

    async def go():
        assert (await event_host.go_spot("beside_globe"))["ok"]
        for _ in range(50):
            if ("say", "tease") in log:
                break
            await asyncio.sleep(0.01)
        run.state = "stopped"                            # B on the robot: the walker ends, no person's stop counted
        for _ in range(100):
            if ("cut", "walk_ended") in log:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
    asyncio.run(go())
    assert log == [("say", "tease"), ("cut", "walk_ended")], log         # and no music: no walk going


def test_the_wave_ducks_after_the_corner_walk_and_is_not_watched_for_still_wheels():
    import inspect

    src = inspect.getsource(event_tour._wave_and_line)
    walk, duck = src.index("turned = await _walk("), src.index('and music("duck"):')
    assert 'if pcm is not None and music("duck"):' in src           # no line to say, no duck (review 04:5x)
    pageant, hand_up = src.index("await event_pageant.start("), src.index("await event_pageant.hand_up(")
    line = src.index("event_cues.play_line(")
    # owner after round 6: "จังหวะโบกมือเหมือนจะปิดเสียงไวไป หุ่นยังไม่ทันยกแขนเลย" -- the duck when the hand is up
    assert walk < pageant < hand_up < duck < line
    assert "soft=True, wheels=False)" in src and "wave_turn_cut" in event_tour.WHY_TH


def test_her_hello_at_khun_mikis_hand_has_the_music_down_first():
    import inspect

    src = inspect.getsource(event_tour._say_line)
    assert src.index('music("duck")') < src.index("event_cues.play_line(")


def test_a_spot_marked_catwalk_plays_the_music_on_its_walk_and_no_other_does(monkeypatch):
    import types

    from app import event_walk, face_mirror

    calls = []
    monkeypatch.setattr(face_mirror, "catwalk", lambda op, **kw: calls.append((op, kw)) or True, raising=False)
    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"g": {"catwalk": True}, "h": {}}})
    event_walk._catwalk(types.SimpleNamespace(spot="g", id="1", state="walking"), "start")
    event_walk._catwalk(types.SimpleNamespace(spot="g", id="1", state="arrived"), "end")
    event_walk._catwalk(types.SimpleNamespace(spot="g", id="1", state="stopped"), "end")
    event_walk._catwalk(types.SimpleNamespace(spot="h", id="2", state="arrived"), "end")
    assert calls == [("walk", {"track": "walk"}), ("hit", {}), ("off", {})]
    # a spot with a line before its music (card 3, owner after round 6): the walk's start leaves the music to that line
    monkeypatch.setattr(event_host, "load", lambda: {"spots": {"g": {"catwalk": True, "show": {"first": {"file": "x"}}}}})
    del calls[:]
    event_walk._catwalk(types.SimpleNamespace(spot="g", id="3", state="walking"), "start")
    event_walk._catwalk(types.SimpleNamespace(spot="g", id="3", state="arrived"), "end")
    assert calls == [("hit", {})]


def test_the_debut_turns_toward_the_guests_before_its_wave():
    import inspect

    doc, debut = _real()
    assert debut["wave_face"] == 85 and debut["wave_at_via"] is True      # 25 Nov ~03:10: at the corner, facing the guests
    src = inspect.getsource(event_tour._wave_and_line)
    turn, pageant = src.index('sc.get("wave_face")'), src.index("event_pageant.start")
    assert turn < pageant and "soft=True" in src


def test_the_corner_to_khun_miki_is_judged_by_the_walk_out_not_the_wave_turn():
    """Review 25 Nov 02:3x: after the turn to 100 deg for the wave, the owner's corner (~169 deg from there) was dropped
    as "not ahead" and she glided across to Khun Miki. `via_heading` = the way she walked out keeps it."""
    import math

    doc, debut = _real()
    tour, spot = debut["tour"], doc["spots"]["beside_host"]
    dock, out_deg = (-6.86, -6.88), 167.0
    x, y = dock[0] + debut["forward_m"] * math.cos(math.radians(out_deg)), dock[1] + debut["forward_m"] * math.sin(math.radians(out_deg))
    dk = {"pose": [*dock, out_deg], "approach": [-9.0, -6.2], "leave_by_approach": True}
    pts = lambda steps: [tuple(p) for s in steps for p in (s.get("glide") or ([s["to"]] if s.get("to") else []))]
    corner = tuple(tour["via"][0][:2])
    waved = event_tour.leg_steps(spot, (x, y, float(debut["wave_face"])), dk, head_on=True, via=tour["via"], glide=tour.get("glide"))
    assert corner not in pts(waved)                                       # the bug: judged by the wave heading
    kept = event_tour.leg_steps(spot, (x, y, float(debut["wave_face"])), dk, head_on=True, via=tour["via"],
                                glide=tour.get("glide"), via_heading=out_deg)
    assert pts(kept)[0] == corner, pts(kept)                              # the corner first, as the owner's route
    assert kept == event_tour.leg_steps(spot, (x, y, out_deg), dk, head_on=True, via=tour["via"],
                                        glide=tour.get("glide")) or pts(kept) == pts(
        event_tour.leg_steps(spot, (x, y, out_deg), dk, head_on=True, via=tour["via"], glide=tour.get("glide")))
