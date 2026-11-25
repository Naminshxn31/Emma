"""ท่านางงาม, the server's half (owner 9 Nov 2026: "ขอเป็นท่านางงามเลยได้ไหม ที่เดินแล้วหันหน้าโบกมือ
ไปมา" -> "โบกไปด้วยระหว่างเดิน", "ท่านี้จะใช้เฉพาะโหมดนี้แก้กฏได้") -- app/event_pageant.py, the
debut scene in app/event_host.py, and the one exception it opens in robot_face_link.

What these hold down:
* the pageant is its own two ids with its own style -- never a move_body id, never one of those
  ids dressed as "event" -- asked of an app whose caps say it plays it, and nothing else;
* on a walk only the debut's own walk may START it; its END passes any walk; every other
  gesture is still refused on a walk;
* the debut: the hand comes up once the wheels turn, waves through the line at the spot, and
  comes down after it -- no greeting group on top; standing, it comes up BEFORE her line and the
  line waits for it (owner 11 Nov "มือจะขึ้นช้ากว่าเสียง"); refused, the old greeting wave after her
  first words is the fallback, and the pageant is never asked for again once she speaks;
* pressed again while its standing hand is up, the hand stays up for the new line;
* it comes down when the walk ends short, at the next scene, and before any walk rests the arm;
* while it runs the head is its own (no look, no talking arm, no walk-picture angle).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app import event_host, event_pageant, event_walk, robot_body
from app import robot_contracts as rc
from app import robot_face_link as link
from app.config import settings
from tests.test_event_host import _settle, _walker, event, line_at_the_spot, stage  # noqa: F401 -- fixtures
from tests.test_robot_face_link import _answer_when_sent, _face

CAPS = {"type": "body_caps", "enabled": True, "allow_direction_known": False, "gestures": {}}


def _event_face(pageant: bool = True):
    s = _face()
    link.receive(s, dict(CAPS, pageant=pageant))
    return s


@pytest.fixture(autouse=True)
def _clean():
    event_pageant.reset()
    yield
    event_pageant.reset()


@pytest.fixture
def open_gate(monkeypatch):
    """The server's arm keys on and the desk's /arm armed (conftest leaves the arm off)."""
    monkeypatch.setattr(event_pageant, "_gate", lambda: None)


# ---- the contract ------------------------------------------------------------------------

def test_its_ids_are_its_own_and_never_a_voice_gesture():
    assert rc.EVENT_BODY_ACTIONS == ("pageant", "pageant_end")
    assert not set(rc.EVENT_BODY_ACTIONS) & set(rc.BODY_ACTION_IDS)
    # move_body's enum is BODY_ACTION_IDS (the heard-guarded voice path), so no pageant there;
    # a guest cannot talk the arm up on a walk
    source = (event_host.ROOT / "app" / "tools" / "body.py").read_text(encoding="utf-8")
    assert '"enum": list(BODY_ACTION_IDS)' in source and "EVENT_BODY_ACTIONS" not in source
    assert rc.body_reason_th("not_event_app") == "ท่านี้ใช้ได้เฉพาะแอป Emma Event และ Emma Ceremony"


def test_the_link_sends_it_with_its_style_and_nothing_dressed_as_it():
    s = _event_face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(s, accepted=True, reason=None, status=None, est_ms=None))
        result = await link.request(s, "body", {"action": "pageant", "style": "event", "pulse": 1700}, 1.0)
        return result, await answer

    result, command = asyncio.run(scenario())
    assert result["accepted"] is True
    assert set(command) == {"type", "command_id", "action", "style"}
    assert command["action"] == "pageant" and command["style"] == "event"
    for payload in ({"action": "wave", "style": "event"}, {"action": "pageant"},
                    {"action": "pageant", "style": "speech"}, {"action": "pageant_end", "style": "bogus"}):
        out = asyncio.run(link.request(s, "body", payload, 0.2))
        assert out["reason"] == "bad_request", payload
    assert len(s.ws.sent) == 1


def test_an_app_that_does_not_say_pageant_is_never_asked():
    for face in (_event_face(pageant=False), _face()):
        out = asyncio.run(link.request(face, "body", {"action": "pageant", "style": "event"}, 0.2))
        assert out == {"accepted": False, "reason": "not_event_app", "command_id": None}
        assert face.ws.sent == []


def test_on_a_walk_only_the_debuts_walk_starts_it_and_its_end_always_passes(monkeypatch):
    s = _event_face()
    link.receive(s, dict(CAPS, pageant=True, gestures={"nod": "verified"}))
    monkeypatch.setattr(link, "server_walking", lambda: True)
    mine = {"v": False}
    monkeypatch.setattr(event_pageant, "walk_is_mine", lambda: mine["v"])
    out = asyncio.run(link.request(s, "body", {"action": "pageant", "style": "event"}, 0.2))
    assert out["reason"] == "navigating" and s.ws.sent == []
    nod = asyncio.run(link.request(s, "body", {"action": "nod"}, 0.2))
    assert nod["reason"] == "navigating", "every other gesture is still refused on a walk"

    async def ask(payload):
        answer = asyncio.create_task(_answer_when_sent(s, index=len(s.ws.sent), accepted=True, reason=None))
        return await link.request(s, "body", payload, 1.0), await answer

    result, command = asyncio.run(ask({"action": "pageant_end", "style": "event"}))
    assert result["accepted"] and command["action"] == "pageant_end"
    mine["v"] = True
    result, command = asyncio.run(ask({"action": "pageant", "style": "event"}))
    assert result["accepted"] and command["action"] == "pageant"


def test_walk_is_mine_names_one_run_under_way(monkeypatch):
    class Run:
        id, state = "r1", "walking"

    monkeypatch.setattr(event_walk, "current", lambda: Run)
    assert event_pageant.walk_is_mine() is False          # nobody asked for it on this walk
    event_pageant.STATE["want_run"] = "r1"
    assert event_pageant.walk_is_mine() is True
    Run.state = "starting"                                # the arm's rest before the wheels
    assert event_pageant.walk_is_mine() is False
    Run.state, Run.id = "walking", "r2"
    assert event_pageant.walk_is_mine() is False


def test_its_status_is_a_body_status_and_ends_it(monkeypatch, open_gate):
    s = _event_face()
    monkeypatch.setattr(event_pageant, "_session", lambda: s)
    from app import greeter

    async def go():
        answer = asyncio.create_task(_answer_when_sent(s, accepted=True, reason=None))
        out = await event_pageant.start(7)
        await answer
        return out

    assert asyncio.run(go())["ok"] is True
    assert event_pageant.active() and event_pageant.active(7) and not event_pageant.active(8)
    assert greeter.paused_except("nobody"), "the camera's greetings wait while it runs"
    link.receive(s, {"type": "body_status", "seq": 1, "action": "pageant", "state": "started", "reason": None})
    assert event_pageant.active()
    link.receive(s, {"type": "body_status", "seq": 2, "action": "pageant", "state": "done", "reason": None})
    assert robot_body.STATE["last_action"] == "pageant"
    assert not event_pageant.active() and not greeter.paused_except("nobody")


def test_a_status_that_never_comes_does_not_hold_the_head_for_ever():
    event_pageant.STATE.update(on=True, seq=1, since=time.time() - event_pageant.APP_MAX_S - event_pageant.END_SLACK_S - 1)
    assert not event_pageant.active()


def test_an_older_pageants_end_does_not_end_the_new_one():
    now = time.time()
    event_pageant.STATE.update(on=True, seq=2, since=now, ended_at=now - 5.0)
    assert event_pageant.active(2)
    event_pageant._on_status(None, {"action": "nod", "state": "done"})      # another gesture's row
    assert event_pageant.active(2), "only the pageant's own status ends it"
    event_pageant._on_status(None, {"action": "pageant", "state": "failed"})
    assert not event_pageant.active()


def test_an_older_pageants_late_done_does_not_end_the_one_just_accepted(monkeypatch, open_gate):
    """Re-check 9 Nov (probed): the old pageant still coming down, the new start retried on busy;
    the old one's `done` reached the server after the request left, before the ack."""
    t0 = time.time() - 30.0
    event_pageant.STATE.update(on=True, seq=1, since=t0, accepted_at=t0)       # pageant 1, not over

    async def request(session, kind, payload, timeout):
        event_pageant._on_status(None, {"action": "pageant", "state": "done"})  # pageant 1's late done
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    assert asyncio.run(event_pageant.start(2))["ok"] is True
    assert event_pageant.active(2), "the new one is up"


def test_the_servers_arm_keys_and_the_desk_stop_hold_for_it(monkeypatch):
    """Review 9 Nov: the start went straight to the link -- ROBOT_BODY/ARM/ARM_MOTION and the
    desk's /arm STOP (robot_body._request steps 2, 2b) were skipped."""
    from app import robot_arm

    sent = []

    async def request(*a, **k):
        sent.append(a)
        return {"accepted": True}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    for key in ("robot_body_enabled", "robot_arm_enabled", "robot_arm_motion_enabled"):
        monkeypatch.setattr(settings, key, True)
    monkeypatch.setattr(robot_arm, "armed", lambda: False)
    assert asyncio.run(event_pageant.start(1))["error"] == "latched"
    monkeypatch.setattr(robot_arm, "armed", lambda: True)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", False)
    assert asyncio.run(event_pageant.start(1))["error"] == "server_disabled"
    assert sent == []
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    assert asyncio.run(event_pageant.start(1))["ok"] is True and len(sent) == 1


def test_a_start_outrun_by_the_press_is_never_left_up(monkeypatch, open_gate):
    """Review 9 Nov: a start retried on busy was accepted on the NEXT scene and never ended; one
    whose answer was in flight when staff pressed on was not ended by that scene's end."""
    asked = []
    box = {"current": True, "busy": 1, "flip_on_accept": False}

    async def request(session, kind, payload, timeout):
        asked.append(payload["action"])
        if payload["action"] == "pageant" and box["busy"]:
            box["busy"] -= 1
            box["current"] = False                       # staff pressed on while the arm was busy
            return {"accepted": False, "reason": "busy"}
        if payload["action"] == "pageant" and box["flip_on_accept"]:
            box["current"] = False                       # ...or while the answer was on its way
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    monkeypatch.setattr(event_pageant, "RETRY_EVERY_S", 0.01)
    out = asyncio.run(event_pageant.start(1, still=lambda: box["current"]))
    assert out["error"] == "moot" and asked == ["pageant"], "no second try for a press that is gone"
    assert not event_pageant.active()
    asked.clear()
    box.update(current=True, busy=0, flip_on_accept=True)
    out = asyncio.run(event_pageant.start(2, still=lambda: box["current"]))
    assert out["error"] == "moot" and asked == ["pageant", "pageant_end"]
    assert not event_pageant.active() and not event_pageant.claims_arm()


def test_end_asks_the_app_until_nothing_is_up(monkeypatch):
    answers = [{"accepted": True, "est_ms": 3000}, {"accepted": True, "est_ms": 3000},
               {"accepted": True, "est_ms": None}]
    asked = []

    async def request(session, kind, payload, timeout):
        asked.append(payload["action"])
        return answers.pop(0)

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())
    monkeypatch.setattr(event_pageant, "END_ASK_EVERY_S", 0.01)
    event_pageant.STATE.update(on=True, seq=1, since=time.time(), accepted_at=time.time())
    assert asyncio.run(event_pageant.end("walk", wait=True)) is True
    assert asked == ["pageant_end"] * 3 and not event_pageant.active()
    # what the server believed is not what decides: an app with nothing up says so at once
    asked.clear()
    answers[:] = [{"accepted": True, "est_ms": None}]
    event_pageant.STATE.update(on=True, seq=2, since=time.time(), accepted_at=time.time())
    assert asyncio.run(event_pageant.end("walk", wait=True)) is True and asked == ["pageant_end"]
    # and with nothing accepted lately, nothing is asked
    asked.clear()
    assert asyncio.run(event_pageant.end("walk", wait=True)) is True and asked == []


def test_end_waits_for_a_start_in_flight(monkeypatch):
    order = []

    async def request(session, kind, payload, timeout):
        order.append(payload["action"])
        return {"accepted": True, "est_ms": None}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: object())

    async def go():
        event_pageant.STATE["asking"] = True

        async def answered():
            await asyncio.sleep(0.2)
            order.append("start acked")
            event_pageant.STATE.update(asking=False, on=True, seq=1, since=time.time(), accepted_at=time.time())

        task = asyncio.create_task(answered())
        assert await event_pageant.end("walk", wait=True) is True
        await task

    asyncio.run(go())
    assert order == ["start acked", "pageant_end"]


# ---- the debut -----------------------------------------------------------------------------

@pytest.fixture
def robot(monkeypatch, stage):
    """A robot face that accepts (or refuses, from `box["refuse"]`) every pageant command."""
    box = {"asked": [], "refuse": [], "session": object(), "walker_out": []}

    async def request(session, kind, payload, timeout):
        assert kind == "body" and payload.get("style") == "event"
        box["asked"].append(payload["action"])
        box.setdefault("at", []).append(time.monotonic())
        run = event_walk.current()
        if payload["action"] == "pageant" and run is not None:
            box["walker_out"].append((run.saw_started, run.state))
        stage["log"].append(("pageant", payload["action"]))
        if box["refuse"]:
            return {"accepted": False, "reason": box["refuse"].pop(0)}
        return {"accepted": True, "reason": None}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: box["session"])
    monkeypatch.setattr(event_pageant, "_gate", lambda: None)
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 0.3)
    monkeypatch.setattr(event_pageant, "TAIL_S", 0.05)
    return box


async def _quiet():
    await asyncio.sleep(0)
    await _settle(lambda: not event_host._tasks)


def _stage_recorded():
    event_host.spots_path().write_text(json.dumps({"spots": {
        "stage_centre": {"pose": [-9.0, -3.0, 0.0]}}}), encoding="utf-8")


STARTED_THEN_ARRIVES = ("import time; time.sleep(0.4); print('@@ {\"started\": true}', flush=True); time.sleep(0.8); "
                        "print('[ 0.1] step 1 done', flush=True); print('[ 0.2] path event done', flush=True)")


def test_standing_the_hand_comes_up_before_her_words_and_down_after(event, stage, robot):
    claimed = []

    async def when_said():
        claimed.append(event_pageant.claims_arm())       # her talking arm keeps off from the line on

    stage["before_answer"] = when_said

    async def go():
        out = await event_host.start_scene("debut")
        await _quiet()
        return out

    out = asyncio.run(go())
    assert out["ok"] and out["walk"]["ok"] is False
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("say", "event_scene"), ("pageant", "pageant_end")]
    assert ("arm", "greet_arm") not in stage["log"], "the greeting group would fight the pageant's arm"
    p = event_host.perform_view()
    assert p["phase"] == "done" and p["gesture"] == "pageant" and p["gesture_ok"] is True
    assert p["gesture_th"] == "ท่านางงาม (โบกมือ หันซ้าย-ขวา)"
    # however short her line, one turn each way first (MIN_STANDING_S, 0.3 s here)
    up, down = robot["at"]
    assert down - up >= 0.3
    assert claimed == [True] and not event_pageant.claims_arm()


def test_standing_her_talking_arm_is_waited_out(event, stage, robot, monkeypatch):
    """Her talking arm (a beat of her last answer, still holding) or its request on the link:
    busy / request_pending is asked again (RETRY_S), not taken as the robot refusing the pageant."""
    monkeypatch.setattr(event_pageant, "RETRY_EVERY_S", 0.05)
    robot["refuse"] = ["busy", "request_pending"]

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant", "pageant", "pageant", "pageant_end"]
    assert ("arm", "greet_arm") not in stage["log"]
    assert event_host.perform_view()["gesture_ok"] is True


def test_on_the_walk_the_hand_waves_from_the_wheels_to_the_end_of_the_line(event, stage, robot, monkeypatch):
    line_at_the_spot(event)                              # the arrival road (the debut's own: on the walk)
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    seen: list = []

    async def go():
        out = await event_host.start_scene("debut")
        assert out["walk"]["ok"], out
        assert await _settle(lambda: ("pageant", "pageant") in stage["log"])
        seen.append((event_walk.walking(), event_host.perform_view()))
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"])
        await _quiet()

    asyncio.run(go())
    walking, view = seen[0]
    assert walking and view["phase"] == "walking" and view["pageant_ok"] is True
    kinds = [e[:2] for e in stage["log"]]
    # up on the way, the line at the spot, down after it -- once each, no greeting group
    assert kinds == [("pageant", "pageant"), ("say", "event_arrived"), ("pageant", "pageant_end")]
    assert robot["asked"] == ["pageant", "pageant_end"]
    assert robot["walker_out"] == [(True, "walking")], "up once the walker is out, not before"
    assert event_host.perform_view()["gesture"] == "pageant"


def test_refused_on_the_walk_it_tries_at_the_spot_then_waves_the_old_way(event, stage, robot, monkeypatch):
    line_at_the_spot(event)
    _stage_recorded()
    _walker(monkeypatch, STARTED_THEN_ARRIVES)
    robot["refuse"] = ["latched", "not_event_app"]

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("arm", "greet_arm") in stage["log"])
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant", "pageant"]
    # the spot's try comes before the line now (the hand leads it); refused, the greeting group after it
    assert [e[0] for e in stage["log"]] == ["pageant", "pageant", "say", "arm"]
    p = event_host.perform_view()
    assert p["gesture"] == "greet_arm" and p["gesture_ok"] is True
    assert p["pageant_ok"] is False and p["pageant_error_th"] == rc.body_reason_th("not_event_app")


def test_a_walk_that_ends_short_brings_the_hand_down(event, stage, robot, monkeypatch):
    _stage_recorded()
    _walker(monkeypatch, "import time; print('@@ {\"started\": true}', flush=True); time.sleep(30)")

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("pageant", "pageant") in stage["log"])
        await event_host.stop()
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"])
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant", "pageant_end"]
    assert ("arm", "greet_arm") not in stage["log"]


def test_the_next_scene_brings_it_down(event, stage, robot):
    async def go():
        event_pageant.STATE.update(on=True, seq=event_host.STATE["seq"], since=time.time())
        await event_host.start_scene("welcome")
        await _quiet()

    asyncio.run(go())
    assert robot["asked"][0] == "pageant_end"


def test_a_walks_arm_rest_waits_for_it_to_come_down(monkeypatch):
    """It is brought down first and waited for; a down the app never confirmed is no gate of its
    own (review 9 Nov: a stale belief refused every walk for 22 s) -- the rest's answer decides."""
    monkeypatch.setattr(settings, "event_walk_arm_rest", True)
    calls = []

    async def end(why, wait=False):
        calls.append((why, wait))
        return False

    monkeypatch.setattr(event_pageant, "end", end)
    # the call's socket (10 Nov: not current_face(), which can be the stop-only status link)
    monkeypatch.setattr(event_walk, "_call_face", lambda: calls.append("rest asked") or None)
    assert asyncio.run(event_walk._arm_rested()) is False      # no call here: no rest
    assert calls == [("walk", True), "rest asked"]


def test_a_stop_at_the_spot_brings_the_walks_wave_down(event, stage, robot):
    """Review 9 Nov: two early returns of the spot's line left the walk's wave up to its 45 s cap."""
    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        event_pageant.STATE.update(on=True, seq=seq, since=time.time(), accepted_at=time.time())
        await event_host._say_line("x", "event_arrived", sc, seq, stops_seen=event_walk.stops() - 1)
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant_end"] and [e[0] for e in stage["log"]] == ["pageant"]


def test_a_red_stop_while_she_says_it_brings_the_walks_wave_down(event, stage, robot):
    async def stopped_meanwhile():
        event_walk.STATE["stops"] = event_walk.stops() + 1

    stage["before_answer"] = stopped_meanwhile

    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        event_pageant.STATE.update(on=True, seq=seq, since=time.time(), accepted_at=time.time())
        await event_host._say_line("x", "event_arrived", sc, seq, stops_seen=event_walk.stops())
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant_end"]


def test_staff_pressing_on_while_the_arm_is_busy_ends_the_try(event, stage, robot, monkeypatch):
    """The start belongs to the press (`still`): staff pressed another scene while the first try was
    answered busy -- no second try for the old scene, and no fallback wave for it either."""
    monkeypatch.setattr(event_pageant, "RETRY_EVERY_S", 0.05)
    robot["refuse"] = ["busy", "busy", "busy"]
    pressed_on = []

    async def first_try_then_staff_press(session, kind, payload, timeout, _real=link.request):
        if not pressed_on:
            pressed_on.append(True)
            event_host.STATE["seq"] += 1                 # another scene's press
        return await _real(session, kind, payload, timeout)

    monkeypatch.setattr(link, "request", first_try_then_staff_press)

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant"]
    assert ("arm", "greet_arm") not in stage["log"]


def test_end_asks_the_app_even_when_the_server_thinks_it_is_over(robot):
    """A status that ended the server's belief falsely (or a lost one) must not leave the hand up:
    a pageant accepted lately is asked down whatever `active` says -- a no-op on the app if done."""
    event_pageant.STATE.update(on=False, seq=1, since=time.time(), accepted_at=time.time())
    assert asyncio.run(event_pageant.end("line_done")) is True
    assert robot["asked"] == ["pageant_end"]


def test_a_later_press_keeps_its_own_pageant(robot):
    """The line's end brings down its own pageant only: one pressed since is not its to end."""
    event_pageant.STATE.update(on=True, seq=5, since=time.time(), accepted_at=time.time())
    asyncio.run(event_pageant.end_after_line(4, standing=False))
    assert robot["asked"] == []


# ---- the hand before her line (owner 11 Nov: "เหมือนมือจะขึ้นช้ากว่าเสียงที่พูด") --------------------
# On the robot that morning the line went out with the start and the raise was 6.9 s of her 7.4 s line (4.0 s since 23 Nov);
# pressed again 9 s later the arm was still coming down, the start was answered busy for 6 s and
# the hand rose after her line was over, alone for 19 s.

def _started_after(monkeypatch, delay):
    """The app's "started" for each accepted pageant `delay` s after its accept (its frames going)."""
    asked = link.request

    async def request(session, kind, payload, timeout):
        ack = await asked(session, kind, payload, timeout)
        if payload.get("action") == "pageant" and ack.get("accepted"):
            asyncio.get_running_loop().call_later(
                delay, event_pageant._on_status, None, {"action": "pageant", "state": "started"})
        return ack

    monkeypatch.setattr(link, "request", request)


def test_her_line_waits_for_the_hand_from_the_apps_started(event, stage, robot, monkeypatch):
    monkeypatch.setattr(settings, "event_pageant_lead_s", 0.4)
    _started_after(monkeypatch, 0.2)
    said_at = []
    phases = []

    async def when_said():
        said_at.append((time.time(), time.monotonic(), (event_host.perform_view() or {}).get("phase")))

    stage["before_answer"] = when_said

    async def go():
        await event_host.start_scene("debut")
        while event_host._tasks:
            phase = (event_host.perform_view() or {}).get("phase")
            if not phases or phases[-1] != phase:
                phases.append(phase)
            await asyncio.sleep(0.02)
        await _quiet()

    asyncio.run(go())
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("say", "event_scene"), ("pageant", "pageant_end")]
    started = event_pageant.STATE["started_at"]
    wall, mono, phase = said_at[0]
    # from the app's own "started" (its frames), not from the accept 0.2 s before it
    assert wall - started >= 0.4
    assert mono - robot["at"][0] >= 0.6
    # the phone said what it waited for, before the line went
    assert [x for x in phases if x][0] == "raising" and phase == "saying"
    assert event_host.perform_view()["gesture"] == "pageant" and event_host.perform_view()["gesture_ok"] is True


def test_no_started_from_the_app_counts_from_the_accept(event, stage, robot, monkeypatch):
    """A "started" that never comes (a dropped status) does not hold her line for ever, and one of an
    OLDER run (before this request) is not this start's."""
    monkeypatch.setattr(settings, "event_pageant_lead_s", 0.2)
    monkeypatch.setattr(event_pageant, "STARTED_WAIT_S", 0.5)
    event_pageant.STATE["started_at"] = time.time() - 30.0     # the last run's
    said_at = []

    async def when_said():
        said_at.append(time.monotonic())

    stage["before_answer"] = when_said

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    waited = said_at[0] - robot["at"][0]                 # both monotonic
    assert 0.5 <= waited < 2.0


def test_hand_up_answers(monkeypatch):
    async def go():
        event_pageant.STATE.update(on=True, seq=3, since=time.time(), accepted_at=time.time(),
                                   asked_at=time.time() - 0.01, started_at=0.0)
        assert await event_pageant.hand_up(4, lambda: True) is True          # not this press's: nothing to wait for
        assert await event_pageant.hand_up(3, lambda: False) is False        # the press is gone
        monkeypatch.setattr(settings, "event_pageant_lead_s", 0.0)
        assert await event_pageant.hand_up(3, lambda: True) is True          # 0 = the line with the start
        monkeypatch.setattr(settings, "event_pageant_lead_s", 0.3)
        event_pageant.STATE["started_at"] = time.time()
        t = time.monotonic()
        assert await event_pageant.hand_up(3, lambda: True) is True
        assert time.monotonic() - t >= 0.25

    asyncio.run(go())


def test_a_start_refused_before_her_line_is_never_asked_again_once_she_speaks(event, stage, robot, monkeypatch):
    """11 Nov 09:36: the arm still coming down from the last press answered `busy`; the start, retried
    while she spoke, was accepted after her line and waved 19 s alone. Now every try is before the
    line, and the greeting group after her first words is the fallback."""
    monkeypatch.setattr(event_pageant, "RETRY_EVERY_S", 0.05)
    monkeypatch.setattr(event_pageant, "RETRY_S", 0.2)
    robot["refuse"] = ["busy"] * 50

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    kinds = [e[:2] for e in stage["log"]]
    said = kinds.index(("say", "event_scene"))
    assert said > 0 and all(k == ("pageant", "pageant") for k in kinds[:said])
    assert kinds[said + 1:] == [("arm", "greet_arm")], kinds
    p = event_host.perform_view()
    assert p["gesture"] == "greet_arm" and p["gesture_ok"] is True and p["pageant_ok"] is False


def test_pressed_again_while_its_hand_is_up_it_stays_up_for_the_new_line(event, stage, robot, monkeypatch):
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 2.0)
    rows = []
    from app import turnlog

    real = turnlog.record
    monkeypatch.setattr(turnlog, "record", lambda ev, **f: (rows.append({"event": ev, **f}), real(ev, **f)))

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done")
        assert event_pageant.active()
        await event_host.start_scene("debut")            # "เริ่มเดินเปิดตัว อีกครั้ง"
        await _quiet()

    asyncio.run(go())
    # up once, the line twice, down once -- after the second line
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("say", "event_scene"),
                                             ("say", "event_scene"), ("pageant", "pageant_end")]
    assert {"event": "event_pageant", "phase": "kept", "seq": event_host.STATE["seq"]} in rows


def test_pressed_again_late_in_its_run_it_comes_down_and_up_again(event, stage, robot, monkeypatch):
    """The app ends its run by itself after its last whole side: past KEEP_WITHIN_S another line
    would outlast it, so the hand comes down for the press and up again before the line."""
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 2.0)
    monkeypatch.setattr(event_pageant, "KEEP_WITHIN_S", 0.0)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done")
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    assert robot["asked"] == ["pageant", "pageant_end", "pageant", "pageant_end"]


def test_only_its_own_scene_keeps_a_standing_hand_early_in_its_run():
    now = time.time()
    event_pageant.STATE.update(on=True, seq=1, since=now, accepted_at=now, asked_at=now, started_at=now)
    assert event_pageant.can_keep("debut", "debut")
    assert not event_pageant.can_keep("welcome", "debut")            # another pageant scene: down and up
    assert not event_pageant.can_keep(None, "debut")
    event_pageant.STATE["run_id"] = "walk-1"
    assert not event_pageant.can_keep("debut", "debut")              # the walk's hand: a walk rests it anyway
    event_pageant.STATE["run_id"] = None
    event_pageant.STATE.update(since=now - 30.0, started_at=now - 30.0)
    assert not event_pageant.can_keep("debut", "debut")              # too late in the app's own run
    event_pageant.STATE["on"] = False
    assert not event_pageant.can_keep("debut", "debut")


def test_at_the_spot_the_line_waits_for_the_walks_hand(event, stage, robot, monkeypatch):
    """The hand that came up with the wheels: the spot's line waits for it the same way (a short walk
    arrives before the raise is done), and is not asked for again."""
    monkeypatch.setattr(settings, "event_pageant_lead_s", 0.3)
    said = []

    async def when_said():
        said.append((time.time(), (event_host.perform_view() or {}).get("phase")))

    stage["before_answer"] = when_said
    phases = []

    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        now = time.time()
        event_pageant.STATE.update(on=True, seq=seq, since=now, accepted_at=now, asked_at=now - 0.01,
                                   started_at=now, run_id="walk-1")
        task = asyncio.create_task(event_host._say_line("x", "event_arrived", sc, seq))
        while not task.done():
            phases.append((event_host.perform_view() or {}).get("phase"))
            await asyncio.sleep(0.02)
        await _quiet()
        return now

    started = asyncio.run(go())
    assert "pageant" not in robot["asked"][:-1] and robot["asked"][-1:] == ["pageant_end"]
    assert said[0][0] - started >= 0.3 and said[0][1] == "saying"
    assert "raising" in phases


def test_another_scene_still_brings_it_down(event, stage, robot, monkeypatch):
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 2.0)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: (event_host.perform_view() or {}).get("phase") == "done")
        await event_host.start_scene("welcome")
        await _quiet()

    asyncio.run(go())
    assert robot["asked"][:2] == ["pageant", "pageant_end"]


def test_a_press_while_the_hand_comes_down_asks_a_new_one(event, stage, robot, monkeypatch):
    """Review 11 Nov: the last line's end was taken (the app lowering: `est_ms`), `active` stays true
    until its done -- a press then must not "keep" a hand on its way down and speak under it."""
    asked = link.request

    async def lowering(session, kind, payload, timeout):
        ack = await asked(session, kind, payload, timeout)
        if payload.get("action") == "pageant_end":
            return dict(ack, est_ms=3000)                # coming down, not yet done
        return ack

    monkeypatch.setattr(link, "request", lowering)

    async def go():
        await event_host.start_scene("debut")
        assert await _settle(lambda: ("pageant", "pageant_end") in stage["log"])
        assert event_pageant.active() and event_pageant.ending()
        assert not event_pageant.can_keep("debut", "debut")
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    kinds = [e[:2] for e in stage["log"]]
    second_say = len(kinds) - 1 - kinds[::-1].index(("say", "event_scene"))
    assert ("pageant", "pageant") in kinds[kinds.index(("pageant", "pageant_end")) + 1:second_say], kinds


def test_with_no_call_the_hand_rings_one_before_it_is_asked_for(event, stage, robot, monkeypatch):
    """Review 11 Nov: the mic held and the call hung up -- it was her line that rang the call, the
    start after it. Now the start goes first: it rings the call itself (the walk's staff bell)."""
    from app import wake

    up = {"call": False, "rang": []}

    async def summon(reason="server"):
        up["rang"].append(reason)
        up["call"] = True
        return 1

    class _Call:
        provider = object()

    monkeypatch.setattr(wake, "summon", summon)
    monkeypatch.setattr(event_walk, "_call_face", lambda: _Call() if up["call"] else None)
    monkeypatch.setattr(event_pageant, "_session", lambda: robot["session"] if up["call"] else None)

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    assert up["rang"] == ["event_scene"]                 # a staff ring: answered under a held mic, muted
    assert [e[:2] for e in stage["log"]] == [("pageant", "pageant"), ("say", "event_scene"), ("pageant", "pageant_end")]


def test_her_answer_still_playing_is_waited_out_before_the_hand(event, stage, robot, monkeypatch):
    """Review 11 Nov: the hand rose over the end of her answer to a guest (the 9 Nov case)."""
    import types

    from app import session as session_module

    ends = time.monotonic() + 0.5
    monkeypatch.setattr(session_module, "_active", types.SimpleNamespace(_play_until=ends))

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    assert robot["at"][0] >= ends


def test_the_line_still_playing_is_read_from_the_cues_own_runs():
    from app import event_cues

    sc = event_host.scene("debut") or {"id": "debut"}
    assert not event_host._line_playing(sc)
    event_cues.STATE["runs"][991] = {"id": "scene_debut"}
    try:
        assert event_host._line_playing(sc)
    finally:
        event_cues.STATE["runs"].pop(991, None)


def test_a_bell_nobody_heard_is_no_call_at_once(monkeypatch):
    """No page in standby heard the bell (closed, reloading): nobody will dial -- no CALL_WAIT_S of
    waiting before the hand and her line (events.announce's "empty house")."""
    from app import wake

    async def nobody(reason="server"):
        return 0

    monkeypatch.setattr(wake, "summon", nobody)
    monkeypatch.setattr(event_walk, "_call_face", lambda: None)
    monkeypatch.setattr(event_walk, "CALL_WAIT_S", 5.0)
    t = time.monotonic()
    assert asyncio.run(event_walk.call_for(lambda: True, reason="event_scene")) is False
    assert time.monotonic() - t < 1.0


def test_a_hand_that_ended_before_her_line_says_so_on_the_phone(event, stage, robot, monkeypatch):
    """Accepted, then the app's own `failed` (its arm connect) before her line: the phone says the
    hand came down, not "the robot refused" -- and the greeting group is the fallback."""
    asked = link.request

    async def fails_at_play(session, kind, payload, timeout):
        ack = await asked(session, kind, payload, timeout)
        if payload.get("action") == "pageant" and ack.get("accepted"):
            event_pageant._on_status(None, {"action": "pageant", "state": "failed"})
        return ack

    monkeypatch.setattr(link, "request", fails_at_play)

    async def go():
        await event_host.start_scene("debut")
        await _quiet()

    asyncio.run(go())
    p = event_host.perform_view()
    assert p["pageant_ok"] is False and p["pageant_error_th"] == event_host.HAND_DOWN_TH
    assert p["gesture"] == "greet_arm"


def test_a_hand_on_its_way_down_is_not_up_for_her_line(event, stage, robot):
    """The app took an end (lowering, `active` until its done): the line does not wait on it, nor
    report a wave it is not getting -- the greeting group is the fallback."""
    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        now = time.time()
        event_pageant.STATE.update(on=True, seq=seq, since=now - 5, accepted_at=now - 5, asked_at=now - 5,
                                   started_at=now - 5, end_asked_at=now)
        await event_host._say_line("x", "event_arrived", sc, seq)
        await _quiet()

    asyncio.run(go())
    p = event_host.perform_view()
    assert p["gesture"] == "greet_arm" and p["pageant_ok"] is False
    assert p["pageant_error_th"] == event_host.HAND_DOWN_TH


# ---- while it runs ---------------------------------------------------------------------

def test_no_talking_arm_and_no_walk_picture_angle_while_it_runs(monkeypatch):
    from app import speech_gestures, walk_frames

    monkeypatch.setattr(robot_body, "_server_gate", lambda action: True)
    event_pageant.want()                                 # the debut's line is going out
    assert speech_gestures._why_not(object(), "raise_right_arm") == "pageant"
    event_pageant.unwant()
    monkeypatch.setattr(event_pageant, "active", lambda seq=None: True)
    assert speech_gestures._why_not(object(), "raise_right_arm") == "pageant"
    assert walk_frames._head() is None


def test_standing_counts_from_the_apps_own_start():
    """Review 10 Nov: the app may take ~5 s to connect the arm before its frames start; the standing
    minimum counted from the request lowered the hand before one turn each way."""
    import time as _time

    event_pageant.reset()
    event_pageant.STATE["since"] = _time.time() - 10.0
    assert event_pageant._standing_from() == event_pageant.STATE["since"]       # no word from the app yet
    event_pageant._on_status(None, {"action": "pageant", "state": "started"})
    assert event_pageant._standing_from() == event_pageant.STATE["started_at"] > event_pageant.STATE["since"]
    event_pageant._on_status(None, {"action": "wave", "state": "started"})      # another gesture's word
    old = event_pageant.STATE["started_at"]
    event_pageant.STATE["since"] = _time.time() + 1.0                             # a newer request
    assert event_pageant._standing_from() == event_pageant.STATE["since"] and old < event_pageant.STATE["since"]
    event_pageant.reset()


# ---- the card's "เอามือลง" takes back what the scene press still had queued (review 14 Nov, round 2) --

def test_the_cards_hand_down_before_the_debuts_hand_rose_keeps_it_down(event, stage, robot):
    """Pressed while the debut's line rang the call or waited out her answer: the hand is not asked
    for, and no fallback gesture rises after her line -- the line itself still goes."""
    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        event_host.MANUAL["down_seq"] = seq               # the card's "เอามือลง" for this press
        await event_host._say_line("x", "event_arrived", sc, seq)
        await _quiet()

    asyncio.run(go())
    assert "pageant" not in robot["asked"]
    assert [e[0] for e in stage["log"]] == ["say"]                       # no ("arm", "greet_arm")
    p = event_host.perform_view()
    assert p["said"] is True and p["gesture_ok"] is False and p["gesture_error_th"] == event_host.STAFF_DOWN_TH


def test_a_hand_brought_down_by_the_card_is_not_one_that_came_down_by_itself(event, stage, robot):
    """The hand on its way down when her line goes (the card ended it): before, read as the app's own
    end (HAND_DOWN_TH) and the greeting group waved after it."""
    async def go():
        sc = event_host.scene("debut")
        event_host.STATE["seq"] += 1
        seq = event_host.STATE["seq"]
        now = time.time()
        event_pageant.STATE.update(on=True, seq=seq, since=now - 5, accepted_at=now - 5, asked_at=now - 5,
                                   started_at=now - 5, end_asked_at=now)
        event_host.MANUAL["down_seq"] = seq
        await event_host._say_line("x", "event_arrived", sc, seq)
        await _quiet()

    asyncio.run(go())
    assert ("arm", "greet_arm") not in stage["log"]
    assert event_host.perform_view()["gesture_error_th"] == event_host.STAFF_DOWN_TH


def test_the_hand_down_is_this_press_only(event, stage, robot):
    """Pressing the scene again is a new press: its hand is its own."""
    async def go():
        sc = event_host.scene("debut")
        event_host.MANUAL["down_seq"] = event_host.STATE["seq"]
        event_host.STATE["seq"] += 1
        await event_host._say_line("x", "event_arrived", sc, event_host.STATE["seq"])
        await _quiet()

    asyncio.run(go())
    assert "pageant" in robot["asked"]


def test_the_debut_walks_hand_is_taken_back_too(event, monkeypatch):
    seen = {}

    async def on_walk(seq, run, still):
        seen["still"] = still
        return {"ok": False, "error": "moot", "error_th": event_pageant.MOOT_TH}

    monkeypatch.setattr(event_pageant, "on_walk", on_walk)

    class _Run:
        saw_started, state = True, "walking"

    sc = dict(event_host.scene("debut"), say_on_walk=False)
    event_host.STATE["seq"] += 1
    seq = event_host.STATE["seq"]
    asyncio.run(event_host._on_walk(sc, seq, _Run(), event_walk.stops()))
    assert seen["still"]() is True
    event_host.MANUAL["down_seq"] = seq
    assert seen["still"]() is False


def test_a_start_while_the_hand_is_on_its_way_down_asks_again(robot):
    """`active(seq)` alone answered "accepted" for a hand going down (after the card's "เอามือลง", or
    the line's end): ending() is not up."""
    now = time.time()
    event_pageant.STATE.update(on=True, seq=4, since=now - 5, accepted_at=now - 5, asked_at=now - 5,
                               started_at=now - 5, end_asked_at=now)
    assert event_pageant.ending()
    r = asyncio.run(event_pageant.start(4))
    assert r["ok"] is True and robot["asked"] == ["pageant"]
    assert not event_pageant.ending()
