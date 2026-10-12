"""robot_face_link: commands to the robot face page and the acks back (§2a).

Every test drives the real module with a fake page. The page answers by
calling `receive` with what it would send over /ws, so the routing, the
validation and the per-session state are what is under test — not a mock of
them.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import robot_face_link as link
from app.config import settings

HEX = "0123456789abcdef" * 2


class Page:
    """The face page's socket: records every frame the server sends."""

    def __init__(self, fail: bool = False):
        self.sent: list[dict] = []
        self.fail = fail

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("socket closed")
        self.sent.append(json.loads(text))


class Session:
    def __init__(self, fail: bool = False):
        self.ws = Page(fail)


def _face(**kw) -> Session:
    s = Session(**kw)
    link.claim(s)
    return s


async def _answer_when_sent(session, index=0, **ack):
    """Wait for the index-th command, then ack it the way the page would."""
    for _ in range(200):
        if len(session.ws.sent) > index:
            break
        await asyncio.sleep(0.005)
    command = session.ws.sent[index]
    kind = command["type"].split("_")[0]
    link.receive(session, {"type": "%s_ack" % kind, "command_id": command["command_id"],
                           "action": command["action"], **ack})
    return command


def test_ack_resolves_its_request():
    s = _face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(
            s, accepted=True, reason=None, status="direction_known", est_ms=6000))
        result = await link.request(s, "body", {"action": "nod"}, 1.0)
        return result, await answer

    result, command = asyncio.run(scenario())
    assert command == {"type": "body_command", "command_id": command["command_id"],
                       "action": "nod"}
    assert len(command["command_id"]) == 32
    assert result == {"accepted": True, "reason": None, "command_id": command["command_id"],
                      "action": "nod", "status": "direction_known", "est_ms": 6000}


def test_nav_command_shape_is_closed():
    """Only the §2a fields leave the server, whatever the caller put in."""
    s = _face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(
            s, accepted=True, reason=None, run_id="a" * 32))
        result = await link.request(
            s, "nav", {"action": "go", "place": "big_room", "origin": "voice",
                       "from_step": 1, "pulse": 1540}, 1.0)
        return result, await answer

    result, command = asyncio.run(scenario())
    assert set(command) == {"type", "command_id", "action", "place", "origin"}
    assert result["accepted"] and result["run_id"] == "a" * 32


@pytest.mark.parametrize("payload", [
    {"action": "fly"}, {"action": "go", "place": "Big Room"}, {"action": "go"},
    {"action": "go", "place": "dock", "origin": "tap"}, {"action": "jump"}])
def test_bad_requests_send_nothing(payload):
    s = _face()
    kind = "body" if payload["action"] in ("fly",) else "nav"
    result = asyncio.run(link.request(s, kind, payload, 0.1))
    assert result["reason"] == "bad_request" and s.ws.sent == []


def test_a_page_that_is_not_the_face_gets_nothing():
    s = Session()                          # never claimed
    result = asyncio.run(link.request(s, "body", {"action": "nod"}, 0.1))
    assert result == {"accepted": False, "reason": "not_robot_face", "command_id": None}
    assert s.ws.sent == []


def test_an_ack_from_another_session_or_with_another_id_is_ignored():
    s, other = _face(), _face()

    async def scenario():
        async def impostor():
            while not s.ws.sent:
                await asyncio.sleep(0.005)
            cid = s.ws.sent[0]["command_id"]
            link.receive(other, {"type": "body_ack", "command_id": cid, "accepted": True})
            link.receive(s, {"type": "body_ack", "command_id": "f" * 32, "accepted": True})
            link.receive(s, {"type": "nav_ack", "command_id": cid, "accepted": True})

        task = asyncio.create_task(impostor())
        result = await link.request(s, "body", {"action": "nod"}, 0.2)
        await task
        return result

    result = asyncio.run(scenario())
    assert result["accepted"] is False and result["reason"] == "timeout"


def test_timeout():
    """No ack is "may be moving" (finding #5): the request answers timeout,
    and a stop of its own kind is already on the wire when it does."""
    s = _face()
    result = asyncio.run(link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05))
    assert result["reason"] == "timeout"
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("nav_command", "go"), ("nav_command", "stop")]


def test_send_failure_is_disconnected():
    s = _face(fail=True)
    result = asyncio.run(link.request(s, "body", {"action": "nod"}, 1.0))
    assert result["reason"] == "disconnected"


def test_session_closed_resolves_pending_as_disconnected_at_once():
    s = _face()

    async def scenario():
        async def close_soon():
            while not s.ws.sent:
                await asyncio.sleep(0.005)
            link.session_closed(s)

        task = asyncio.create_task(close_soon())
        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await link.request(s, "body", {"action": "nod"}, 5.0)
        await task
        return result, loop.time() - started

    result, took = asyncio.run(scenario())
    assert result["reason"] == "disconnected" and took < 1.0
    assert not link.is_robot_face(s)


def test_oversize_and_bad_id_frames_are_dropped():
    s = _face()

    async def scenario():
        async def page():
            while not s.ws.sent:
                await asyncio.sleep(0.005)
            cid = s.ws.sent[0]["command_id"]
            ack = {"type": "body_ack", "command_id": cid, "accepted": True}
            link.receive(s, ack, raw_size=link.rc.MAX_INBOUND_BYTES + 1)
            link.receive(s, dict(ack, padding="x" * link.rc.MAX_INBOUND_BYTES))
            link.receive(s, dict(ack, command_id=cid.upper()))

        task = asyncio.create_task(page())
        result = await link.request(s, "body", {"action": "nod"}, 0.2)
        await task
        return result

    assert asyncio.run(scenario())["reason"] == "timeout"


def test_the_size_limit_is_the_guard(monkeypatch):
    """Revert-red companion of the drop test: with the limit lifted the very
    same oversize frame resolves the request — so it was the limit that
    dropped it, not something incidental."""
    s = _face()
    monkeypatch.setattr(link.rc, "MAX_INBOUND_BYTES", 10 ** 9)

    async def scenario():
        async def page():
            while not s.ws.sent:
                await asyncio.sleep(0.005)
            cid = s.ws.sent[0]["command_id"]
            link.receive(s, {"type": "body_ack", "command_id": cid, "accepted": True,
                             "padding": "x" * 9000})

        task = asyncio.create_task(page())
        result = await link.request(s, "body", {"action": "nod"}, 1.0)
        await task
        return result

    assert asyncio.run(scenario())["accepted"] is True


def test_unknown_reason_from_the_page_is_not_passed_on():
    s = _face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(s, accepted=False, reason="lol_no"))
        result = await link.request(s, "body", {"action": "nod"}, 1.0)
        await answer
        return result

    assert asyncio.run(scenario())["reason"] == "bad_request"


def test_one_outstanding_non_stop_request_per_kind():
    s = _face()

    async def scenario():
        first = asyncio.create_task(link.request(s, "body", {"action": "nod"}, 0.3))
        await asyncio.sleep(0.02)
        second = await link.request(s, "body", {"action": "wave"}, 0.3)
        other_kind = await link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05)
        return second, other_kind, await first

    second, other_kind, first = asyncio.run(scenario())
    assert second["reason"] == "request_pending"
    assert other_kind["reason"] == "timeout"         # nav has its own slot
    assert first["reason"] == "timeout"
    # Each timed-out request is followed by a stop of its own kind (finding #5).
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("body_command", "nod"), ("nav_command", "go"), ("nav_command", "stop"),
        ("body_command", "stop")]


def test_a_stop_is_sent_while_a_request_is_pending():
    """Safety review #1. With a `nod` ack pending (a page that never answers
    it), a stop goes out within 100 ms with its own command id and resolves
    on its own ack; the nod keeps waiting for its own."""
    s = _face()

    async def scenario():
        nod = asyncio.create_task(link.request(s, "body", {"action": "nod"}, 0.5))
        await asyncio.sleep(0.02)
        loop = asyncio.get_running_loop()
        started = loop.time()
        answer = asyncio.create_task(_answer_when_sent(s, index=1, accepted=True))
        stop = await link.request(s, "body", {"action": "stop"}, 1.0)
        sent_after = loop.time() - started
        await answer
        return stop, sent_after, await nod

    stop, took, nod = asyncio.run(scenario())
    # The third frame is the stop that follows the nod's own timeout (#5).
    assert [c["action"] for c in s.ws.sent] == ["nod", "stop", "stop"]
    assert s.ws.sent[0]["command_id"] != s.ws.sent[1]["command_id"]
    assert stop["accepted"] is True and took < 0.1
    assert nod["reason"] == "timeout"


def test_a_pending_stop_does_not_block_the_next_command():
    s = _face()

    async def scenario():
        stop = asyncio.create_task(link.request(s, "nav", {"action": "stop"}, 0.3))
        await asyncio.sleep(0.02)
        go = await link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05)
        await stop
        return go

    assert asyncio.run(scenario())["reason"] == "timeout"     # sent, not pending


# ---- nav_activity ----


def _status(state, run_id="b" * 32, **extra):
    return {"type": "nav_status", "format": 1, "state": state, "run_id": run_id,
            "place": "big_room", **extra}


@pytest.mark.parametrize("state,expected", [
    ("idle", "idle"), ("preparing", "active"), ("starting", "active"), ("running", "active"),
    ("arrived", "idle"), ("stopped", "idle"), ("refused", "idle"), ("failed", "idle")])
def test_nav_activity_for_each_state(state, expected):
    s = _face()
    assert link.nav_activity(s) == "unknown"
    assert link.nav_activity() == "unknown"
    link.receive(s, _status(state))
    assert link.nav_activity(s) == expected
    assert link.nav_activity() == expected


def test_nav_activity_goes_stale_by_clock(monkeypatch):
    s = _face()
    now = [1000.0]
    monkeypatch.setattr(link, "_clock", lambda: now[0])
    link.receive(s, _status("running"))
    now[0] += settings.robot_nav_status_stale_s - 0.5
    assert link.nav_activity(s) == "active"
    now[0] += 1.0
    assert link.nav_activity(s) == "stale"
    link.receive(s, _status("running"))
    assert link.nav_activity(s) == "active"


def test_nav_caps_run_counts_including_a_pc_run():
    s = _face()
    link.receive(s, {"type": "nav_caps", "enabled": True, "installed": True, "places": [],
                     "run": {"state": "running", "origin": "pc", "reason": "pc_run"}})
    assert link.nav_activity(s) == "active"
    # Caps never clear a run (finding #4): `run: null` there is the app's
    # 10 s places cache, not an end. The status poll's idle is.
    link.receive(s, {"type": "nav_caps", "enabled": True, "places": [], "run": None})
    assert link.nav_activity(s) == "active" and link.nav_activity() == "active"
    link.receive(s, _status("idle", run_id=None))
    assert link.nav_activity(s) == "idle" and link.nav_activity() == "idle"


def test_a_walk_outlives_its_page():
    """The face page dropping mid-walk does not stop the robot; the fence
    must not read the silence as "nothing running"."""
    s = _face()
    link.receive(s, _status("running"))
    link.session_closed(s)
    assert link.nav_activity(s) == "unknown"
    assert link.nav_activity() in ("active", "stale")


def test_malformed_nav_frames_change_nothing():
    s = _face()
    link.receive(s, _status("running"))
    link.receive(s, _status("teleporting"))
    link.receive(s, _status("idle", run_id="X" * 32))
    assert link.nav_activity(s) == "active"


def test_frames_from_a_page_that_is_not_the_face_are_ignored():
    s = Session()
    link.receive(s, _status("running"))
    assert link.nav_activity() == "unknown"


# ---- finding #4: one face cannot release another face's run ----


def _fenced() -> tuple[bool, object]:
    """Both fences that read nav_activity(): the wheels and the arm port."""
    from app import robot_arm, robot_chassis

    try:
        robot_chassis.run_fence()
        wheels = False
    except robot_chassis.Fenced as exc:
        wheels = exc.code == "run_active"
    return wheels, robot_arm.app_owns_port()


def test_a_second_page_reporting_idle_does_not_open_the_fence():
    """The other ZC-3588A with the same APK, or a page left over after a
    reconnect, claims EmmaRobot/ and reports idle while the robot walks.
    Before the fix its frame overwrote the one global slot."""
    robot, other = _face(), _face()
    link.receive(robot, _status("running", origin="voice"))
    assert _fenced() == (True, "robot_walking")
    link.receive(other, _status("idle", run_id=None))
    assert link.nav_activity(other) == "idle"
    assert link.nav_activity() == "active"
    assert _fenced() == (True, "robot_walking")
    # The face that reported the run is the one that may end it.
    link.receive(robot, _status("arrived", origin="voice"))
    assert link.nav_activity() == "idle"
    assert _fenced() == (False, None)


def test_caps_with_no_run_during_a_walk_change_nothing():
    """The panel's pullCaps on openNav sends `run: null` from the app's 10 s
    places cache while the robot walks."""
    s = _face()
    link.receive(s, _status("running"))
    link.receive(s, {"type": "nav_caps", "enabled": True, "installed": True, "places": [],
                     "here": "dock", "run": None})
    assert link.nav_activity(s) == "active" and link.nav_activity() == "active"
    assert _fenced() == (True, "robot_walking")
    assert link.caps(s, "nav")["here"] == "dock", "the caps are still cached for status"


def test_a_page_that_comes_back_takes_its_run_with_it():
    """The face page reloads mid-walk: the old socket's entry is the same run
    as the new page's, so the new page's arrival ends it — a reload must not
    hold the fence for ever."""
    old = _face()
    link.receive(old, _status("running"))
    link.session_closed(old)
    assert link.nav_activity() == "active"
    new = _face()
    link.receive(new, _status("running"))
    link.receive(new, _status("arrived"))
    assert link.nav_activity() == "idle"


def test_a_dropped_faces_walk_is_not_ended_by_another_pages_idle(monkeypatch):
    robot = _face()
    link.receive(robot, _status("running"))
    link.session_closed(robot)
    other = _face()
    link.receive(other, _status("idle", run_id=None))
    assert link.nav_activity() == "active"
    assert _fenced() == (True, "robot_walking")


def test_walking_face_is_the_live_face_with_the_run():
    robot, other = _face(), _face()
    assert link.walking_face() is None
    link.receive(robot, _status("running"))
    link.receive(other, _status("idle", run_id=None))
    assert link.walking_face() is robot
    link.session_closed(robot)
    assert link.walking_face() is None, "a closed socket cannot be sent a stop"


# ---- review 2, finding #5: caps from before the end of a run ----


def _caps(here="dock", run=None, **extra):
    return {"type": "nav_caps", "enabled": True, "installed": True, "places": [],
            "here": here, "run": run, **extra}


def test_caps_for_a_run_already_over_cannot_raise_the_face_again():
    """When a run turns terminal the page forces a caps frame from the app's
    cache, which can still say the run is running. It used to raise the face
    to active after the arrival; the next caps (run: null) could not lower
    it and nothing else would, so the wheel fence and the arm port stayed
    shut (then stale) until the server restarted. Revert-red against
    _caps_overtaken in receive()."""
    s = _face()
    link.receive(s, _status("running"))
    link.receive(s, _status("arrived"))
    assert link.nav_activity() == "idle"
    link.receive(s, _caps(run={"state": "running", "run_id": "b" * 32, "origin": "voice"}))
    assert link.nav_activity(s) == "idle" and link.nav_activity() == "idle"
    assert _fenced() == (False, None)
    # The probe's tail: the call ends, a new one reports idle — still open.
    link.session_closed(s)
    new = _face()
    link.receive(new, _status("idle", run_id=None))
    assert link.nav_activity() == "idle" and _fenced() == (False, None)


def test_a_run_superseded_by_a_newer_terminal_status_cannot_come_back():
    """One leg at a time: the app starts a run only after the last one ended,
    so a run this face named before a newer terminal status is over."""
    s = _face()
    link.receive(s, _status("running", run_id="a" * 32))
    link.receive(s, _status("refused", run_id="b" * 32))
    link.receive(s, _caps(run={"state": "running", "run_id": "a" * 32}))
    assert link.nav_activity() == "idle"


def test_caps_can_still_raise_a_run_the_face_never_ended():
    """Fail toward fenced: a PC run has no run id, and a run id this face
    never reported over is a run that may be going — both still raise."""
    s = _face()
    link.receive(s, _status("arrived"))
    link.receive(s, _caps(run={"state": "running", "origin": "pc", "reason": "pc_run"}))
    assert link.nav_activity() == "active"
    t = _face()
    link.receive(t, _status("arrived", run_id="a" * 32))
    link.receive(t, _caps(run={"state": "running", "run_id": "c" * 32}))
    assert link.nav_activity(t) == "active"


def test_a_newer_nav_status_overrides_the_caps_here():
    """The forced caps after a walk carried the place the walk LEFT from:
    'dock' while the robot stood in big_room, and voice 'กลับแท่นชาร์จ' was
    answered already_there."""
    s = _face()
    link.receive(s, _caps(here="dock"))
    assert link.here(s) == "dock"
    link.receive(s, _status("running"))
    assert link.here(s) is None, "between places while a run is under way"
    link.receive(s, _status("arrived"))
    assert link.here(s) == "big_room"
    # The stale cache from before the arrival, sent after it.
    link.receive(s, _caps(here="dock", run={"state": "running", "run_id": "b" * 32}))
    assert link.here(s) == "big_room"
    # A fresh caps (after the app's refresh) speaks for itself again.
    link.receive(s, _caps(here="big_room"))
    assert link.here(s) == "big_room"
    link.receive(s, _status("stopped", run_id="c" * 32))
    assert link.here(s) is None, "stopped mid-route: somewhere on the way"


def test_the_leg_runner_latch():
    """review 2, finding #0: "unknown" hides a walk only where a leg runner
    can exist — routes on, or a face has reported the launcher installed
    since the server started (and that outlives the face's socket)."""
    assert link.leg_runner_possible() is False
    s = _face()
    link.receive(s, _caps(installed=False))
    assert link.leg_runner_possible() is False
    link.receive(s, _caps(installed=True))
    link.session_closed(s)
    assert link.leg_runner_possible() is True
    link.reset_state()
    assert link.leg_runner_possible() is False


def test_routes_on_alone_makes_a_leg_runner_possible(monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    assert link.leg_runner_possible() is True


def test_a_page_that_is_not_the_face_cannot_set_the_latch():
    link.receive(Session(), _caps(installed=True))
    assert link.leg_runner_possible() is False


# ---- finding #5: an ack after the timeout ----


def test_a_late_accepted_go_is_stopped_and_its_run_is_ours():
    """The go's ack arrives after the timeout: the robot accepted and may be
    walking while the model was told it could not. A stop went out at the
    timeout, a second goes out on the late ack, and the run id is this
    session's so its stopped status is still announced."""
    s = _face()
    run_id = "c" * 32

    async def scenario():
        out = await link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05)
        go = s.ws.sent[0]
        link.receive(s, {"type": "nav_ack", "command_id": go["command_id"], "action": "go",
                         "accepted": True, "run_id": run_id})
        for _ in range(100):
            if len(s.ws.sent) >= 3:
                break
            await asyncio.sleep(0.005)
        return out

    out = asyncio.run(scenario())
    assert out["reason"] == "timeout"
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("nav_command", "go"), ("nav_command", "stop"), ("nav_command", "stop")]
    assert link.issued_run(s, run_id) is True


def test_a_late_ack_does_not_stop_a_newer_go():
    """Integrator's check of #5: go A times out (stop 1 follows it), the model
    sends go B, then A's late accepted ack arrives. Stop 1 already sits ahead
    of B on the wire; a second stop would arrive after B and stop the walk
    the model was told was accepted."""
    s = _face()
    run_a, run_b = "a" * 32, "b" * 32

    async def scenario():
        await link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05)
        go_a = s.ws.sent[0]
        answer = asyncio.create_task(_answer_when_sent(s, 2, accepted=True, run_id=run_b))
        out_b = await link.request(s, "nav", {"action": "go", "place": "dock"}, 1.0)
        await answer
        link.receive(s, {"type": "nav_ack", "command_id": go_a["command_id"], "action": "go",
                         "accepted": True, "run_id": run_a})
        await asyncio.sleep(0.05)
        return out_b

    out_b = asyncio.run(scenario())
    assert out_b["accepted"] is True
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("nav_command", "go"), ("nav_command", "stop"), ("nav_command", "go")]
    # A's run is still this session's, so its stopped status is announced.
    assert link.issued_run(s, run_a) is True


@pytest.mark.parametrize("late,newer", [
    (("body", {"action": "nod"}), ("nav", {"action": "go", "place": "dock"})),
    (("nav", {"action": "go", "place": "dock"}), ("body", {"action": "nod"})),
])
def test_a_late_ack_does_not_stop_a_newer_command_of_the_other_kind(late, newer):
    """Review 2, finding #14: on the app a body stop and a nav stop are the
    same stop (MainActivity.bodyStop/navStop -> stopBodyAndNav). A nod times
    out (a body stop follows it), the model starts a walk, then the nod's
    late accepted ack arrives: another body stop would land after the go and
    stop the walk the model was told was accepted. Only the app's 60 s stop
    latches hid it. Revert-red against the either-kind `newer` in _late_ack."""
    s = _face()
    (late_kind, late_payload), (new_kind, new_payload) = late, newer

    async def scenario():
        await link.request(s, late_kind, late_payload, 0.05)
        first = s.ws.sent[0]
        answer = asyncio.create_task(_answer_when_sent(s, 2, accepted=True, run_id="b" * 32))
        out = await link.request(s, new_kind, new_payload, 1.0)
        await answer
        link.receive(s, {"type": "%s_ack" % late_kind, "command_id": first["command_id"],
                         "action": first["action"], "accepted": True, "run_id": "a" * 32})
        await asyncio.sleep(0.05)
        return out

    out = asyncio.run(scenario())
    assert out["accepted"] is True
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("%s_command" % late_kind, late_payload["action"]), ("%s_command" % late_kind, "stop"),
        ("%s_command" % new_kind, new_payload["action"])]


def test_a_late_ack_is_only_honoured_for_its_own_session():
    s, other = _face(), _face()
    run_id = "c" * 32

    async def scenario():
        await link.request(s, "nav", {"action": "go", "place": "dock"}, 0.05)
        go = s.ws.sent[0]
        link.receive(other, {"type": "nav_ack", "command_id": go["command_id"],
                             "action": "go", "accepted": True, "run_id": run_id})
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert link.issued_run(other, run_id) is False and link.issued_run(s, run_id) is False
    assert other.ws.sent == []


def test_a_late_body_ack_sends_another_body_stop():
    s = _face()

    async def scenario():
        await link.request(s, "body", {"action": "nod"}, 0.05)
        nod = s.ws.sent[0]
        link.receive(s, {"type": "body_ack", "command_id": nod["command_id"],
                         "action": "nod", "accepted": True})
        for _ in range(100):
            if len(s.ws.sent) >= 3:
                break
            await asyncio.sleep(0.005)

    asyncio.run(scenario())
    assert [(c["type"], c["action"]) for c in s.ws.sent] == [
        ("body_command", "nod"), ("body_command", "stop"), ("body_command", "stop")]


# ---- issued_run ----


def test_issued_run_only_for_this_sessions_own_acked_go():
    s, other = _face(), _face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(s, accepted=True, run_id="c" * 32))
        await link.request(s, "nav", {"action": "go", "place": "dock", "origin": "voice"}, 1.0)
        await answer

    asyncio.run(scenario())
    assert link.issued_run(s, "c" * 32)
    assert not link.issued_run(other, "c" * 32)
    assert not link.issued_run(s, "d" * 32)
    # A status claiming the run from the other page does not make it theirs.
    link.receive(other, _status("arrived", run_id="c" * 32))
    assert not link.issued_run(other, "c" * 32)
    link.session_closed(s)
    assert not link.issued_run(s, "c" * 32)


def test_a_refused_go_issues_nothing():
    s = _face()

    async def scenario():
        answer = asyncio.create_task(_answer_when_sent(
            s, accepted=False, reason="location_unknown", run_id="c" * 32))
        result = await link.request(s, "nav", {"action": "go", "place": "dock"}, 1.0)
        await answer
        return result

    result = asyncio.run(scenario())
    assert result["reason"] == "location_unknown"
    assert not link.issued_run(s, "c" * 32)


# ---- face_hello ----


def test_face_hello_gating(monkeypatch):
    monkeypatch.setattr(settings, "robot_face_token", "s3cret-token")
    s = _face()
    assert not link.is_robot_face(s)
    assert asyncio.run(link.request(s, "body", {"action": "nod"}, 0.05))["reason"] \
        == "not_robot_face"
    link.receive(s, {"type": "face_hello", "token": "wrong"})
    assert not link.is_robot_face(s)
    link.receive(s, {"type": "face_hello", "token": "s3cret-token"})
    assert link.is_robot_face(s)
    assert link.current_face() is s


def test_face_hello_from_a_desk_browser_does_nothing(monkeypatch):
    """Holding WS_TOKEN and the face token is still not enough without the
    robot's user agent: the hello proves the claim, it does not make one."""
    monkeypatch.setattr(settings, "robot_face_token", "s3cret-token")
    desk = Session()
    link.receive(desk, {"type": "face_hello", "token": "s3cret-token"})
    assert not link.is_robot_face(desk)


def test_without_a_token_the_user_agent_decides():
    s = _face()
    assert link.is_robot_face(s) and link.current_face() is s


# ---- handlers ----


def test_handlers_get_valid_frames_and_the_close():
    s = _face()
    seen = []

    def on_status(session, event):
        seen.append((session is s, event["type"], event.get("state")))

    async def on_close(session, event):
        seen.append((session is s, event["type"], None))

    link.on("body_status", on_status)
    link.on("body_status", on_status)                  # once is once
    link.on("session_closed", on_close)
    try:
        async def scenario():
            link.receive(s, {"type": "body_status", "seq": 1, "action": "nod",
                             "state": "done", "reason": None})
            link.receive(s, {"type": "body_status", "seq": "2", "action": "nod",
                             "state": "done"})      # malformed: dropped
            link.session_closed(s)
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
    finally:
        link.off("body_status", on_status)
        link.off("session_closed", on_close)
    assert seen == [(True, "body_status", "done"), (True, "session_closed", None)]
    with pytest.raises(ValueError):
        link.on("body_ack", on_status)


def test_caps_are_kept_per_session():
    s, other = _face(), _face()
    caps = {"type": "body_caps", "enabled": True, "allow_direction_known": False,
            "gestures": {"nod": "direction_known", "head_up": "needs_mapping"}}
    link.receive(s, caps)
    assert link.caps(s, "body")["gestures"]["head_up"] == "needs_mapping"
    assert link.caps(other, "body") is None
    link.receive(s, dict(caps, gestures={"fly": "verified"}))
    assert "fly" not in link.caps(s, "body")["gestures"]


# ---- session.py routes the frames here ----


def test_session_routes_inbound_frames_with_their_raw_size(monkeypatch):
    from app import session as session_module

    got = []
    monkeypatch.setattr(link, "receive",
                        lambda session, event, raw_size=None: got.append(
                            (event["type"], raw_size)))
    frames = [
        {"type": "body_ack", "command_id": HEX, "accepted": True},
        {"type": "nav_status", "state": "running"},
        {"type": "face_hello", "token": "x"},
        {"type": "robot_ready", "token": ""},
    ]
    texts = [json.dumps(f) for f in frames]

    class WS:
        def __init__(self):
            self.queue = [{"type": "websocket.receive", "text": t} for t in texts]
            self.queue.append({"type": "websocket.disconnect"})

        async def receive(self):
            return self.queue.pop(0)

        async def send_text(self, text):
            pass

    sess = session_module.VoiceSession.__new__(session_module.VoiceSession)
    sess.ws = WS()
    sess._ears_closed_until = None
    sess.provider = None
    asyncio.run(sess._browser_to_provider())
    assert got == [("body_ack", len(texts[0])), ("nav_status", len(texts[1])),
                   ("face_hello", len(texts[2]))]


def test_handle_connection_closes_the_link_for_its_session(monkeypatch):
    from app import session as session_module

    closed = []
    monkeypatch.setattr(link, "session_closed", lambda s: closed.append(s))

    async def run(self):
        return None

    monkeypatch.setattr(session_module.VoiceSession, "run", run)

    class WS:
        async def send_text(self, text):
            pass

    asyncio.run(session_module.handle_connection(WS()))
    assert len(closed) == 1 and isinstance(closed[0], session_module.VoiceSession)
