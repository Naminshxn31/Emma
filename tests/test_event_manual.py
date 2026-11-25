"""The Event page's "สั่งเอง ทีละอย่าง" card (owner 14 Nov 2026: "ทำหน้าปุ่มกดยัง ทำแบบ manual").

A scene's box does its walk, its line and its gesture at once. The card presses ONE thing: a scene's
line where she stands (POST /event/say {scene}), a gesture (POST /event/body {action}), a walk to a
spot (the existing /event/go). What these hold down:

* the line said is never "I have arrived" from somewhere else (10 Nov: the multimedia room's arrival
  from across the lobby) -- the arrival line only when the last walk ARRIVED at the scene's spot;
  a scene with nothing to say where she stands (rest) has no button;
* the card is not a scene press: the scene stays the scene and `seq` does not move (a line pressed
  here never cancels the scene's own arrival line or wave);
* ids only: an unknown scene or a gesture outside MANUAL_GESTURES (a head gesture: ch8) is refused;
* one press of each kind in flight; the red stop drops a line still waiting; the ceremony app on the
  robot refuses everything but bringing the hand down;
* a gesture with no call rings the camera's bell first (the call opens muted under a held mic);
* "accepted", never "it waved"; and in a browser: the card's buttons send exactly these, the walk
  takes two taps, and a line waiting for her voice does not hold the arm's buttons.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from app import event_host, event_pageant, event_walk, robot_body
from app.config import settings
from tests.test_event_order_page import Page, chromium  # noqa: F401 -- the fixture, used by name

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "tests" / "data" / "floor1_event_rehearsed.json"
EVENT_PAGE = (ROOT / "client" / "event-control.html").read_text(encoding="utf-8")


@pytest.fixture
def served(tmp_path, monkeypatch):
    path = tmp_path / "floor1_event.json"
    path.write_text(TRACKED.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "event_file", str(path))
    monkeypatch.setattr(event_walk, "RUN_DIR", tmp_path / ".run")
    event_host.reset()
    yield path
    event_host.reset()


def _arrived(spot: str) -> None:
    run = event_walk.Run(spot, spot)
    run.state = "arrived"
    event_walk.STATE["run"] = run


# ---- which line ---------------------------------------------------------------------------------

def test_the_line_is_where_she_stands_unless_she_arrived_at_the_scenes_spot(served):
    escort = event_host.scene("escort_multimedia")
    line = event_host.manual_line(escort)
    # from anywhere else: its line for where she stands, never "ถึงห้องมัลติมีเดียแล้ว"
    assert escort["on_stand_th"] in line and escort["on_arrive_th"] not in line
    assert event_host.STANDING in line and event_host.manual_kind(escort) == "stand"
    _arrived("reception")                                  # arrived -- somewhere else
    assert escort["on_arrive_th"] not in event_host.manual_line(escort)
    _arrived("multimedia_door")
    line = event_host.manual_line(escort)
    assert escort["on_arrive_th"] in line and event_host.STANDING not in line
    assert event_host.manual_kind(escort) == "arrive"
    run = event_walk.current()
    run.state = "stopped"                                  # a walk there that stopped short
    assert escort["on_arrive_th"] not in event_host.manual_line(escort)


def test_each_scene_of_the_event_file_gets_the_line_start_scene_would_say_standing(served):
    sc = event_host.scene
    # no spot: its own line, and no "standing" note (it goes nowhere)
    intro = event_host.manual_line(sc("introduce"))
    assert sc("introduce")["on_start_th"] in intro and event_host.STANDING not in intro
    # the roam's line for where she stands, not "I'll walk round the room"
    roam = event_host.manual_line(sc("roam_lobby"))
    assert sc("roam_lobby")["on_stand_th"] in roam and sc("roam_lobby")["on_start_th"] not in roam
    # rest: nothing to say where she stands -- nor at the dock (it has no arrival line)
    assert event_host.manual_line(sc("rest")) is None and event_host.manual_kind(sc("rest")) is None
    _arrived("dock")
    assert event_host.manual_line(sc("rest")) is None
    # the debut: its quoted sentence (said word for word)
    assert "Hello everyone!" in event_host.manual_line(sc("debut"))
    kinds = {s["id"]: s["manual_line"] for s in event_host.snapshot()["scenes"]}
    assert kinds["rest"] is None and kinds["welcome"] == "stand" and kinds["introduce"] == "stand"


# ---- saying it ----------------------------------------------------------------------------------

@pytest.fixture
def mouth(served, monkeypatch):
    """`_say` and the recorded take faked; the robot standing still; a call up."""
    said = []

    async def say(text, source, still, *, held_language=True):
        said.append({"text": text, "source": source, "held_language": held_language, "still": still()})
        return True

    async def recorded(sc, still):
        said.append({"recorded": sc["id"]})
        return None                                        # no take: her own voice

    monkeypatch.setattr(event_host, "_say", say)
    monkeypatch.setattr(event_host, "_say_recorded", recorded)
    monkeypatch.setattr(event_host, "_robot_settling", lambda: False)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    return said


def test_saying_a_line_is_not_a_scene_press(mouth):
    event_host.STATE.update(scene="welcome", seq=7)
    r = asyncio.run(event_host.manual_say("escort_multimedia"))
    assert r == {"ok": True, "scene": "escort_multimedia", "kind": "stand", "recorded": False}
    assert len(mouth) == 1 and mouth[0]["source"] == "event_manual" and mouth[0]["held_language"] is True
    assert "ทีมงานกดให้พูด" in mouth[0]["text"]
    # the scene staff chose stays, and nothing of the scene's own press is cancelled
    assert event_host.STATE["scene"] == "welcome" and event_host.STATE["seq"] == 7
    assert event_host.MANUAL["saying"] is False


def test_the_debut_plays_its_take_first_and_a_quoted_sentence_keeps_its_language(mouth):
    r = asyncio.run(event_host.manual_say("debut"))
    assert r["ok"] and mouth[0] == {"recorded": "debut"}
    assert mouth[1]["held_language"] is False             # «...»: its language is its own


def test_the_take_played_is_the_answer(mouth, monkeypatch):
    async def recorded(sc, still):
        return True

    monkeypatch.setattr(event_host, "_say_recorded", recorded)
    r = asyncio.run(event_host.manual_say("debut"))
    assert r["ok"] and r["recorded"] is True and mouth == []


def test_with_no_call_the_take_rings_the_bell_first(mouth, monkeypatch):
    rung = []

    async def call_for(still, *, reason="event_walk"):
        rung.append(reason)
        return True

    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "call_for", call_for)
    asyncio.run(event_host.manual_say("debut"))
    assert rung == ["event_manual"]                        # `event_*`: answered muted under a held mic


@pytest.mark.parametrize("scene, error", [("nope", "no_scene"), ("rest", "no_line")])
def test_unknown_or_silent_scenes_are_refused(mouth, scene, error):
    r = asyncio.run(event_host.manual_say(scene))
    assert r["ok"] is False and r["error"] == error and r["error_th"] and mouth == []


def test_the_ceremony_app_refuses_the_line(mouth, monkeypatch):
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    r = asyncio.run(event_host.manual_say("welcome"))
    assert r["error"] == event_host.CEREMONY_ERROR and mouth == []


def test_one_line_in_flight_and_the_red_stop_drops_one_waiting(mouth, monkeypatch):
    async def run():
        settling = {"on": True}
        monkeypatch.setattr(event_host, "_robot_settling", lambda: settling["on"])
        first = asyncio.ensure_future(event_host.manual_say("welcome"))
        await asyncio.sleep(0.05)
        second = await event_host.manual_say("introduce")
        assert second["error"] == "busy"                   # said, never queued behind it
        event_walk.STATE["stops"] += 1                     # a person's red stop while it waited
        return await first

    r = asyncio.run(run())
    assert r["ok"] is False and r["error"] == "stopped" and mouth == []
    assert event_host.MANUAL["saying"] is False


def test_a_line_not_said_says_so(mouth, monkeypatch):
    async def say(text, source, still, *, held_language=True):
        return False

    monkeypatch.setattr(event_host, "_say", say)
    r = asyncio.run(event_host.manual_say("welcome"))
    assert r["ok"] is False and r["error"] == "not_said"


# ---- gestures -----------------------------------------------------------------------------------

@pytest.fixture
def arm(served, monkeypatch):
    asked = []

    async def staff_request(action):
        asked.append(action)
        return {"ok": True, "action": action, "hardware": "requested", "error": None, "error_th": None}

    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    return asked


def test_a_gesture_is_asked_by_id_and_answered_accepted(arm):
    event_host.STATE["seq"] = 3
    r = asyncio.run(event_host.manual_gesture("wave"))
    assert r == {"ok": True, "action": "wave", "note": None, "error": None, "error_th": None} and arm == ["wave"]
    assert event_host.STATE["seq"] == 3 and event_host.MANUAL["arm"] is False


@pytest.mark.parametrize("action", ["nod", "head_left", "stop", "pageant_end_", "", "WAVE"])
def test_anything_else_is_refused(arm, action):
    r = asyncio.run(event_host.manual_gesture(action))
    assert r["ok"] is False and r["error"] == "bad_request" and arm == []


def test_the_head_is_never_on_the_card():
    assert not set(event_host.MANUAL_GESTURES) & {"head_up", "head_down", "head_left", "head_right",
                                                   "head_center", "nod", "shake_head", "stop"}


def test_a_refusal_is_the_robots_words(arm, monkeypatch):
    async def staff_request(action):
        return {"ok": False, "action": action, "hardware": "refused", "error": "latched", "error_th": "ล็อกอยู่"}

    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    r = asyncio.run(event_host.manual_gesture("handshake"))
    assert r == {"ok": False, "action": "handshake", "note": None, "error": "latched", "error_th": "ล็อกอยู่"}


def test_with_no_call_a_gesture_rings_the_bell_first(arm, monkeypatch):
    rung = []

    async def call_for(still, *, reason="event_walk"):
        rung.append(reason)
        return True

    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "call_for", call_for)
    asyncio.run(event_host.manual_gesture("greet_arm"))
    assert rung == ["event_manual"] and arm == ["greet_arm"]


def test_the_ceremony_app_refuses_all_but_the_hand_down(arm, monkeypatch):
    ended = []

    async def end(why, *, wait=False):
        ended.append(why)
        return True

    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    monkeypatch.setattr(event_pageant, "end", end)
    assert asyncio.run(event_host.manual_gesture("wave"))["error"] == event_host.CEREMONY_ERROR
    assert asyncio.run(event_host.manual_gesture("pageant"))["error"] == event_host.CEREMONY_ERROR
    assert asyncio.run(event_host.manual_gesture("pageant_end"))["ok"] is True
    assert arm == [] and ended == ["staff"]


def test_the_pageant_where_she_stands_belongs_to_the_press_now(arm, monkeypatch):
    started = []

    async def start(seq, *, run=None, still=None):
        started.append((seq, run))
        return {"ok": True, "error": None, "error_th": None}

    monkeypatch.setattr(event_pageant, "start", start)
    event_host.STATE["seq"] = 5
    assert asyncio.run(event_host.manual_gesture("pageant"))["ok"] is True
    assert started == [(5, None)] and arm == []


def test_one_gesture_in_flight(arm, monkeypatch):
    gate = asyncio.Event()

    async def slow(action):
        await gate.wait()
        return {"ok": True, "action": action, "hardware": "requested", "error": None, "error_th": None}

    monkeypatch.setattr(robot_body, "staff_request", slow)

    async def run():
        first = asyncio.ensure_future(event_host.manual_gesture("wave"))
        await asyncio.sleep(0.05)
        second = await event_host.manual_gesture("rest")
        gate.set()
        return second, await first

    second, first = asyncio.run(run())
    assert second["error"] == "busy" and first["ok"] and event_host.MANUAL["arm"] is False


def test_the_snapshot_offers_what_the_robot_can_be_asked(served, monkeypatch):
    why = {"wave": None, "greet_arm": "no_call", "handshake": "latched", "raise_right_arm": "needs_mapping",
           "raise_left_arm": "bad_request", "rest": "navigating"}
    monkeypatch.setattr(robot_body, "staff_ready", lambda action: why[action])
    m = event_host.snapshot()["manual"]
    # every one, in the same order whatever the call: buttons never move under a finger (review 14 Nov)
    assert [g["id"] for g in m["gestures"]] == list(event_host.MANUAL_GESTURES)
    by = {g["id"]: g for g in m["gestures"]}
    assert by["raise_right_arm"]["why_th"] == robot_body.staff_reason_th("needs_mapping")   # shown, disabled
    assert by["wave"]["why_th"] is None and by["greet_arm"]["why_th"] is None    # the press rings the call
    assert by["handshake"]["why_th"] == robot_body.staff_reason_th("latched")
    assert by["rest"]["why_th"] == robot_body.staff_reason_th("navigating")
    assert m["pageant"] is True and m["pageant_up"] is False           # the tracked file has the debut
    assert by["wave"]["name_th"] == event_host.gesture_th("wave")


# ---- the endpoints ------------------------------------------------------------------------------

@pytest.fixture
def client(served, monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    monkeypatch.setattr(settings, "event_control_key", "staff-secret")
    c = TestClient(event_control_server.app)
    yield c
    c.close()


def test_the_endpoints_take_ids_behind_the_key(client, monkeypatch):
    calls = []

    async def manual_say(scene_id):
        calls.append(("say", scene_id))
        return {"ok": True, "scene": scene_id, "kind": "stand", "recorded": False}

    async def manual_gesture(action):
        calls.append(("body", action))
        return {"ok": True, "action": action, "error": None, "error_th": None}

    monkeypatch.setattr(event_host, "manual_say", manual_say)
    monkeypatch.setattr(event_host, "manual_gesture", manual_gesture)
    assert client.post("/event/say", json={"scene": "welcome"}).status_code == 403      # no cookie yet
    client.get("/event-control?key=staff-secret")
    assert client.post("/event/say", json={"scene": "welcome"}).json()["ok"]
    assert client.post("/event/body", json={"action": "wave"}).json()["ok"]
    assert client.post("/event/say", json={"scene": "x" * 500}).json()["ok"]            # cut, never words
    assert calls == [("say", "welcome"), ("body", "wave"), ("say", "x" * 64)]
    # the ceremony app: refused at the door -- but the hand still comes down
    monkeypatch.setattr(event_host, "ceremony_now", lambda: True)
    assert client.post("/event/say", json={"scene": "welcome"}).json()["error"] == event_host.CEREMONY_ERROR
    assert client.post("/event/body", json={"action": "wave"}).json()["error"] == event_host.CEREMONY_ERROR
    assert client.post("/event/body", json={"action": "pageant_end"}).json()["ok"]
    assert calls[-1] == ("body", "pageant_end") and len(calls) == 4


def test_the_event_state_carries_the_card(client):
    client.get("/event-control?key=staff-secret")
    doc = client.get("/event/state").json()
    assert set(doc["manual"]) == {"gestures", "pageant", "pageant_why_th", "pageant_up", "saying", "arm"}
    assert all("manual_line" in sc and "manual_says" in sc for sc in doc["scenes"])


# ---- review 14 Nov, round 2: the pageant's button and what each line says ------------------------

def test_the_pageant_button_says_why_the_server_would_refuse_it(served, monkeypatch):
    """It stood pressable with the arm switched off at the server, every gesture greyed beside it,
    and every press came back refused."""
    from app import robot_face_link

    monkeypatch.setattr(robot_face_link, "server_walking", lambda: False)
    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_pageant, "_gate", lambda: "server_disabled")
    assert event_host.snapshot()["manual"]["pageant_why_th"] == robot_body.staff_reason_th("server_disabled")
    monkeypatch.setattr(event_pageant, "_gate", lambda: None)
    assert event_host.snapshot()["manual"]["pageant_why_th"] is None           # no call: the press rings it
    monkeypatch.setattr(robot_face_link, "server_walking", lambda: True)       # robot_face_link: `navigating`
    assert event_host.snapshot()["manual"]["pageant_why_th"] == robot_body.staff_reason_th("navigating")
    monkeypatch.setattr(robot_face_link, "server_walking", lambda: False)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    monkeypatch.setattr(robot_face_link, "caps", lambda session, kind: {"pageant": False})
    assert event_host.snapshot()["manual"]["pageant_why_th"] == robot_body.staff_reason_th("not_event_app")
    monkeypatch.setattr(robot_face_link, "caps", lambda session, kind: {"pageant": True})
    assert event_host.snapshot()["manual"]["pageant_why_th"] is None


def test_each_line_says_which_of_its_lines_it_is(served):
    """From the lobby "พาแขกไปห้องมัลติมีเดีย" is the invitation; at the door, the arrival -- the
    preview is read from the same choice the press makes, never shown as her words unless quoted."""
    escort = event_host.scene("escort_multimedia")
    p = event_host.manual_preview(escort)
    assert p == {"quote": False, "text": escort["on_stand_th"].strip()}
    _arrived(escort["spot"])
    assert event_host.manual_preview(escort) == {"quote": False, "text": escort["on_arrive_th"].strip()}
    debut = event_host.manual_preview(event_host.scene("debut"))
    assert debut["quote"] is True and debut["text"].startswith("Hello everyone!") and "«" not in debut["text"]
    assert event_host.manual_preview(event_host.scene("rest")) is None
    by = {sc["id"]: sc for sc in event_host.snapshot()["scenes"]}
    assert all((s["manual_says"] is None) == (s["manual_line"] is None) for s in by.values())


# ---- review 14 Nov: the races and the words ----------------------------------------------------

def test_a_scene_of_stops_says_its_arrival_at_whichever_stop_she_reached(served):
    tables = event_host.scene("tables")
    _arrived("table_3")                                    # the card's own walk -- not the scene's stop
    line = event_host.manual_line(tables)
    assert event_host.manual_kind(tables) == "arrive" and tables["on_arrive_th"] in line
    assert event_host._spot("table_3")["name_th"] in line


def test_no_line_while_she_walks(mouth):
    run = event_walk.Run("reception", "ส่วนต้อนรับ")
    run.state = "walking"
    event_walk.STATE["run"] = run
    r = asyncio.run(event_host.manual_say("welcome"))
    assert r["error"] == "walking" and mouth == []


def test_a_walk_setting_out_while_the_line_waits_drops_it(mouth, monkeypatch):
    """Chosen at the press, said up to a minute later: she must not be told she stands still
    while the wheels turn (review 14 Nov)."""
    ticks = {"n": 0}

    def settling():
        ticks["n"] += 1
        if ticks["n"] == 2:                                # a scene's walk sets out meanwhile
            run = event_walk.Run("reception", "ส่วนต้อนรับ")
            run.state = "starting"
            event_walk.STATE["run"] = run
        return True

    monkeypatch.setattr(event_host, "_robot_settling", settling)
    r = asyncio.run(event_host.manual_say("introduce"))
    assert r["error"] == "moved" and mouth == []


def test_an_arrival_gone_stale_is_not_said(mouth, monkeypatch):
    _arrived("multimedia_door")

    async def say(text, source, still, *, held_language=True):
        _arrived("reception")                              # another walk arrived elsewhere meanwhile
        return bool(still())

    monkeypatch.setattr(event_host, "_say", say)
    r = asyncio.run(event_host.manual_say("escort_multimedia"))
    assert r["ok"] is False and r["error"] == "moved"


def test_a_take_not_played_says_so(mouth, monkeypatch):
    async def recorded(sc, still):
        return False                                       # already playing / cut half way

    monkeypatch.setattr(event_host, "_say_recorded", recorded)
    r = asyncio.run(event_host.manual_say("debut"))
    assert r["error"] == "already" and r["error_th"] == event_host.MANUAL_ERRORS_TH["already"]


def test_a_red_stop_while_the_bell_rings_sends_no_gesture(arm, monkeypatch):
    async def call_for(still, *, reason="event_walk"):
        event_walk.STATE["stops"] += 1                     # a person's stop while it rang
        return True

    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "call_for", call_for)
    r = asyncio.run(event_host.manual_gesture("handshake"))
    assert r["error"] == "stopped" and arm == []


def test_the_ceremony_opening_while_the_bell_rings_sends_no_gesture(arm, monkeypatch):
    on = {"v": False}

    async def call_for(still, *, reason="event_walk"):
        on["v"] = True
        return True

    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "call_for", call_for)
    monkeypatch.setattr(event_host, "ceremony_now", lambda: on["v"])
    r = asyncio.run(event_host.manual_gesture("wave"))
    assert r["error"] == event_host.CEREMONY_ERROR and arm == []


def test_the_hand_down_makes_a_start_on_its_way_nobodys(arm, monkeypatch):
    """Review 14 Nov: "เอามือลง" while the start waited for the app found nothing up, said "down",
    and the start was accepted after it -- 45 s of waving. Now the start's `still` goes false, and
    the end waits for it (`wait=True`)."""
    gate, seen, ends = asyncio.Event(), {}, []

    async def start(seq, *, run=None, still=None):
        seen["still"] = still
        await gate.wait()
        return {"ok": True, "error": None, "error_th": None}

    async def end(why, *, wait=False):
        ends.append((why, wait))
        return True

    monkeypatch.setattr(event_pageant, "start", start)
    monkeypatch.setattr(event_pageant, "end", end)

    async def run():
        first = asyncio.ensure_future(event_host.manual_gesture("pageant"))
        await asyncio.sleep(0.05)
        assert seen["still"]() is True
        down = await event_host.manual_gesture("pageant_end")
        assert seen["still"]() is False                    # a late accept is brought straight down
        gate.set()
        return down, await first

    down, _ = asyncio.run(run())
    assert down["ok"] and ends == [("staff", True)]


def test_a_scene_press_or_a_red_stop_also_ends_the_manual_pageants_claim(arm, monkeypatch):
    seen = {}

    async def start(seq, *, run=None, still=None):
        seen["still"] = still
        return {"ok": True, "error": None, "error_th": None}

    monkeypatch.setattr(event_pageant, "start", start)
    asyncio.run(event_host.manual_gesture("pageant"))
    assert seen["still"]() is True
    event_host.STATE["seq"] += 1                           # a scene pressed
    assert seen["still"]() is False
    event_host.STATE["seq"] -= 1
    event_walk.STATE["stops"] += 1                         # a red stop
    assert seen["still"]() is False


def test_the_card_buttons_never_double_tap_a_walk_in_the_source():
    script = EVENT_PAGE[EVENT_PAGE.index("<script>"):]
    two = script[script.index("function twoTaps("):script.index("function renderScenes(")]
    assert "if (Date.now() - armed[key] < TWO_TAP_MIN_MS) return;" in two
    assert two.index("TWO_TAP_MIN_MS) return;") < two.index("run();")


# ---- review 14 Nov, round 2 ----------------------------------------------------------------------

def test_hand_down_says_when_nothing_was_up_and_takes_back_the_scenes_arm(arm, monkeypatch):
    ends = []

    async def end(why, *, wait=False):
        ends.append(why)
        return True

    monkeypatch.setattr(event_pageant, "end", end)
    event_host.STATE["seq"] = 7
    r = asyncio.run(event_host.manual_gesture("pageant_end"))
    assert r["ok"] and r["note"] == "nothing_up" and ends == ["staff"]       # nothing was sent: said so
    assert event_host.MANUAL["down_seq"] == 7 and not event_host._arm_free(7) and event_host._arm_free(8)
    monkeypatch.setattr(event_pageant, "_maybe_up", lambda: True)
    assert asyncio.run(event_host.manual_gesture("pageant_end"))["note"] is None


def test_a_gesture_during_a_walk_is_refused_before_anything_is_touched(arm, monkeypatch):
    started, rung = [], []

    async def start(seq, *, run=None, still=None):
        started.append(seq)
        return {"ok": True, "error": None, "error_th": None}

    async def call_for(still, reason):
        rung.append(reason)

    monkeypatch.setattr(event_pageant, "start", start)
    monkeypatch.setattr(event_walk, "call_for", call_for)
    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "walking", lambda: True)
    for action in ("pageant", "wave"):
        r = asyncio.run(event_host.manual_gesture(action))
        assert r["ok"] is False and r["error"] == "arm_walking"
    assert started == [] and rung == [] and arm == []
    # the hand down still goes
    async def end(why, *, wait=False):
        return True

    monkeypatch.setattr(event_pageant, "end", end)
    assert asyncio.run(event_host.manual_gesture("pageant_end"))["ok"] is True


def test_a_walk_that_set_out_while_the_bell_rang_refuses_the_gesture(arm, monkeypatch):
    walking = {"on": False}

    async def call_for(still, reason):
        walking["on"] = True                               # a scene's walk set out meanwhile

    monkeypatch.setattr(event_walk, "call_for", call_for)
    monkeypatch.setattr(event_pageant, "_session", lambda: None)
    monkeypatch.setattr(event_walk, "walking", lambda: walking["on"])
    r = asyncio.run(event_host.manual_gesture("wave"))
    assert r["error"] == "arm_walking" and arm == []


def test_a_card_gesture_is_asked_once_never_queued_behind_the_last(arm, monkeypatch):
    """A re-press while her last gesture played was retried until it ended -- the second played
    after it (a handshake twice), and a "เอามือลง" in between did not stop it."""
    asked = []

    async def staff_request(action):
        asked.append(action)
        return {"ok": False, "action": action, "hardware": "refused", "error": "busy", "error_th": "แขนกำลังทำท่าอยู่"}

    monkeypatch.setattr(robot_body, "staff_request", staff_request)
    monkeypatch.setattr(event_host, "ARM_RETRY_EVERY_S", 0.01)
    r = asyncio.run(event_host.manual_gesture("handshake"))
    assert asked == ["handshake"] and r["error"] == "busy" and r["error_th"] == "แขนกำลังทำท่าอยู่"


def test_a_hand_already_up_is_said_not_asked_again(arm, monkeypatch):
    started = []

    async def start(seq, *, run=None, still=None):
        started.append(seq)
        return {"ok": True, "error": None, "error_th": None}

    monkeypatch.setattr(event_pageant, "start", start)
    monkeypatch.setattr(event_pageant, "active", lambda seq=None: True)
    monkeypatch.setattr(event_pageant, "ending", lambda: False)
    r = asyncio.run(event_host.manual_gesture("pageant"))
    assert r["ok"] and r["note"] == "already_up" and started == []
    monkeypatch.setattr(event_pageant, "ending", lambda: True)              # on its way down: asked again
    assert asyncio.run(event_host.manual_gesture("pageant"))["note"] is None and started == [0]


def test_after_a_stop_the_apps_latch_greys_every_arm_button(served, monkeypatch):
    monkeypatch.setattr(robot_body, "staff_ready", lambda action: None)
    monkeypatch.setattr(event_host, "_manual_pageant_why", lambda: None)
    monkeypatch.setattr(event_walk, "_app_latched", lambda: True)
    m = event_host.snapshot()["manual"]
    assert {g["why_th"] for g in m["gestures"]} == {event_host.MANUAL_ERRORS_TH["app_latched"]}
    assert m["pageant_why_th"] == event_host.MANUAL_ERRORS_TH["app_latched"]
    monkeypatch.setattr(event_walk, "_app_latched", lambda: False)
    assert {g["why_th"] for g in event_host.snapshot()["manual"]["gestures"]} == {None}


def test_a_card_that_cannot_be_read_does_not_take_the_page_down(served, monkeypatch):
    def broken(action):
        raise TypeError("body_caps.gestures is not a dict")

    monkeypatch.setattr(robot_body, "staff_ready", broken)
    monkeypatch.setattr(event_host, "manual_kind", lambda sc: 1 / 0)
    snap = event_host.snapshot()
    assert snap["manual"] is None and snap["scenes"] and snap["walk"] is not None
    assert all(sc["manual_line"] is None and sc["manual_says"] is None for sc in snap["scenes"])


def test_pushed_off_the_spot_after_arriving_is_not_there(served, monkeypatch):
    """Pushed back, driven by the joystick, docked by hand after the walk arrived: the arrival line
    ("ถึงห้องมัลติมีเดียแล้ว") is not hers to say from where she stands now."""
    from app import walk_frames

    escort = event_host.scene("escort_multimedia")
    _arrived(escort["spot"])
    event_walk.STATE["run"].ended = time.monotonic() - 30
    monkeypatch.setattr(walk_frames, "moved_at", lambda: time.monotonic() - 40)     # moved before it ended
    assert event_host.manual_kind(escort) == "arrive"
    monkeypatch.setattr(walk_frames, "moved_at", lambda: time.monotonic() - 5)      # after: pushed off
    assert event_host.manual_kind(escort) == "stand"
    assert escort["on_arrive_th"] not in event_host.manual_line(escort)
    monkeypatch.setattr(walk_frames, "moved_at", lambda: None)                      # nothing seen
    assert event_host.manual_kind(escort) == "arrive"


# ---- the card in a browser ----------------------------------------------------------------------

def _state(walk_on: bool = True) -> dict:
    snap = json.loads(json.dumps(event_host.snapshot(), default=str))
    snap["walk"]["enabled"] = walk_on
    snap["walk"]["health"] = {"known": True, "board": True, "battery": 80, "charging": False, "q": 60,
                              "lost": False, "low_battery": False, "docking": "on_dock"}
    snap["face"] = {"connected": True, "event_call": True, "ceremony_call": False}
    snap["mode"]["ceremony_app"] = False
    snap["mode"]["warnings"] = []
    snap["manual"]["gestures"] = [
        {"id": "wave", "name_th": "โบกมือ", "why_th": None},
        {"id": "handshake", "name_th": "ยื่นมือจับมือ", "why_th": "หุ่นล็อกแขนอยู่ (เพิ่งกดหยุด)"}]
    snap["manual"]["pageant_why_th"] = None                # this PC's .env has no arm keys
    for sid in ("reception", "multimedia_door"):
        for p in snap["spots"]:
            if p["id"] == sid:
                p["walkable"] = True
    return snap


def test_the_card_in_a_browser(chromium, served):  # noqa: F811
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        assert page.visible("#manual") and page.visible("#manualHead")
        says = page.page.locator("#manualSay button .mt").all_inner_texts()
        titles = [s["title_th"] for s in json.loads(TRACKED.read_text(encoding="utf-8"))["scenes"]]
        assert not any(t.endswith("กลับแท่นชาร์จ") for t in says)              # rest: nothing to say standing
        # card 2, the counter's (21 Nov): its greeting and hand are the counter's -- nothing to say standing either
        assert not any(t.endswith("ไปหน้าเคาน์เตอร์ ทักทาย ยื่นมือจับมือ") for t in says)
        # ... and card 3, the guest's (22 Nov): the same -- its greeting and hand are in front of the guest
        assert not any(t.endswith("หันหลังเดินไปจับมือแขก") for t in says)
        assert says[0] == "1. " + titles[0] and len(says) == len(titles) - 3
        # under each, what it says: the debut's sentence in quotes, an instruction as what she will do
        previews = page.page.locator("#manualSay button .mp").all_inner_texts()
        assert previews[0].startswith("“Hello everyone!") and previews[1].startswith("Emma จะ")
        # a line: one tap, the scene's id
        page.page.click("#manualSay button >> nth=0")
        assert "ส่งให้ Emma พูดแล้ว" in page.toast()
        assert page.sent == [("/event/say", {"scene": "debut"})]
        page.clear_toast()
        # a gesture: one tap, its id; one the robot refuses now is disabled, and why is said
        page.page.click("#manualBody button:has-text('โบกมือ') >> nth=0")
        assert "หุ่นรับคำสั่ง โบกมือ แล้ว" in page.toast()
        assert page.sent[-1] == ("/event/body", {"action": "wave"})
        assert page.page.locator("#manualBody button:has-text('ยื่นมือจับมือ')").is_disabled()
        assert "หุ่นล็อกแขนอยู่" in page.text("#manualWhy")
        # the debut's pageant and the hand down
        assert page.page.locator("#manualBody button:has-text('ท่านางงาม (โบกมือ)')").count() == 1
        assert page.page.locator("#manualBody button:has-text('ท่านางงาม (โบกมือ)')").is_enabled()
        page.page.click("#manualBody button:has-text('เอามือลง (ท่านางงาม)')")
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/event/body", {"action": "pageant_end"})
        # a walk: two taps, then /event/go with the spot; the dock is not on the card
        walk = page.page.locator("#manualWalk button")
        assert "แท่นชาร์จ" not in " ".join(walk.all_inner_texts())
        n = len(page.sent)
        page.page.dblclick("#manualWalk button >> nth=0")           # a double tap is not a second thought
        page.page.wait_for_timeout(150)
        assert len(page.sent) == n and "แตะอีกครั้งเพื่อเดินไป" in page.text("#manualWalk")
        page.page.wait_for_timeout(400)
        page.page.click("#manualWalk button >> nth=0")
        page.page.wait_for_timeout(300)
        assert page.sent[-1][0] == "/event/go" and page.sent[-1][1]["spot"] in ("reception", "multimedia_door")
        assert page.page.evaluate("document.documentElement.scrollWidth") <= 400      # no sideways scroll
    finally:
        page.close()


def test_a_line_waiting_for_her_voice_does_not_hold_the_arm(chromium, served):  # noqa: F811
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        page.posts["/event/say"] = "hang"
        page.page.click("#manualSay button >> nth=1")
        page.page.wait_for_timeout(80)                     # the hung POST is cut at 0.3 s here (SHORT_POST)
        assert page.page.locator("#manualSay button >> nth=0").is_disabled()          # one line in flight
        assert "กำลังส่งประโยค" in page.text("#manualSayTitle")
        page.page.click("#manualBody button:has-text('โบกมือ') >> nth=0")             # the arm is not held
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/event/body", {"action": "wave"}) and len(page.sent) == 2
    finally:
        page.close()


def test_a_pageant_the_server_would_refuse_is_greyed_and_says_why(chromium, served):  # noqa: F811
    doc = _state()
    doc["manual"]["pageant_why_th"] = "ปิดการสั่งท่าทางไว้ที่เซิร์ฟเวอร์"
    doc["manual"]["pageant_up"] = True
    page = Page(chromium, doc, html=EVENT_PAGE, path="/event-control")
    try:
        assert page.page.locator("#manualBody button:has-text('ท่านางงาม (โบกมือ)')").is_disabled()
        assert "ปิดการสั่งท่าทางไว้ที่เซิร์ฟเวอร์" in page.text("#manualWhy")
        assert page.page.locator("#manualBody button:has-text('เอามือลง')").is_enabled()   # never held
    finally:
        page.close()


def test_walking_off_says_so_on_the_card(chromium, served):  # noqa: F811
    page = Page(chromium, _state(walk_on=False), html=EVENT_PAGE, path="/event-control")
    try:
        assert "การเดินปิดอยู่" in page.text("#manualWalk")
        assert all(page.page.locator("#manualWalk button").nth(i).is_disabled()
                   for i in range(page.page.locator("#manualWalk button").count()))
    finally:
        page.close()


def test_an_older_server_without_the_card_hides_it(chromium, served):  # noqa: F811
    doc = _state()
    del doc["manual"]
    page = Page(chromium, doc, html=EVENT_PAGE, path="/event-control")
    try:
        assert page.page.locator("#manual").is_hidden() and page.page.locator("#manualHead").is_hidden()
    finally:
        page.close()


def test_the_card_in_the_source():
    script = EVENT_PAGE[EVENT_PAGE.index("<script>"):]
    card = script[script.index("function renderManual("):script.index("// ---- actions")]
    assert "manualPost('say', '/event/say', { scene: sc.id }" in card
    assert "manualPost('arm', '/event/body', { action: g.id }" in card
    assert "manualPost('down', '/event/body', { action: 'pageant_end' }" in card
    assert "twoTaps(k, function () { act('/event/go', { spot: p.id }" in card
    assert "act('/event/say'" not in script and "act('/event/body'" not in script   # its own busy, not act's
    assert "if (!sc.manual_line) return;" in card


def test_while_the_ceremony_app_holds_the_robot_only_the_hand_down_is_offered(chromium, served):  # noqa: F811
    doc = _state()
    doc["mode"]["ceremony_app"] = True
    page = Page(chromium, doc, html=EVENT_PAGE, path="/event-control")
    try:
        buttons = page.page.locator("#manual button")
        enabled = [buttons.nth(i).inner_text() for i in range(buttons.count()) if buttons.nth(i).is_enabled()]
        assert enabled == ["เอามือลง (ท่านางงาม)"]
        assert "Emma Ceremony" in page.text("#manualWhy")
    finally:
        page.close()


def test_the_hand_down_and_a_hand_already_up_say_what_happened(chromium, served):  # noqa: F811
    page = Page(chromium, _state(), html=EVENT_PAGE, path="/event-control")
    try:
        page.posts["/event/body"] = {"ok": True, "action": "pageant_end", "note": "nothing_up"}
        page.page.click("#manualBody button:has-text('เอามือลง (ท่านางงาม)')")
        assert "ไม่มีท่านางงามยกอยู่" in page.toast()
        page.clear_toast()
        page.page.wait_for_timeout(200)
        page.posts["/event/body"] = {"ok": True, "action": "pageant", "note": "already_up"}
        page.page.click("#manualBody button:has-text('ท่านางงาม (โบกมือ)')")
        assert "ยกอยู่แล้ว" in page.toast()
        # her talking arm moves with her voice: the line's title does not promise "no gesture"
        assert "ไม่ทำท่า" not in page.text("#manualSayTitle")
    finally:
        page.close()


def test_the_servers_in_flight_flags_do_not_rebuild_the_card(chromium, served):  # noqa: F811
    doc = _state()
    page = Page(chromium, doc, html=EVENT_PAGE, path="/event-control")
    try:
        page.page.evaluate("window.__mark = document.querySelector('#manualSay button'); 0")
        flipped = json.loads(json.dumps(doc))
        flipped["manual"]["saying"] = flipped["manual"]["arm"] = True               # another phone's press
        page.redraw(flipped)
        assert page.page.evaluate("window.__mark === document.querySelector('#manualSay button')")
    finally:
        page.close()
