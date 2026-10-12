"""move_body / robot_body: voice gestures for the robot's own head and arms (§2d, P1).

Every test drives the real tool, the real registry and the real
`robot_face_link` with a fake face page. The page answers by calling
`robot_face_link.receive` with the frame it would send over /ws, so the
gates, the heard guard and the ack mapping are what is under test — not a
mock of them. Nothing reaches a robot: the page is a recorder.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import time

import pytest

from app import heard, robot_contracts as rc, robot_face_link as link
from app.config import settings
from app.tools import registry as reg

#: The built-in statuses of design §2c, as the app will report them in
#: body_caps before anybody records a pose. The four head ids whose minus
#: side was never driven are needs_mapping here, on purpose.
CONTRACT_CAPS = {
    "handshake": "verified", "greet_arm": "verified", "rest": "verified",
    "head_center": "direction_known", "head_down": "direction_known",
    "nod": "direction_known",
    "head_up": "needs_mapping", "shake_head": "needs_mapping",
    "head_left": "needs_mapping", "head_right": "needs_mapping",
    "raise_left_arm": "needs_mapping", "raise_right_arm": "needs_mapping",
    "wave": "needs_mapping", "hands_left": "needs_mapping", "hands_right": "needs_mapping",
}


class Page:
    """The face page's socket. Records every frame; answers each command
    with `answer(command)` (a dict of ack fields, or None = never answer)."""

    def __init__(self, answer=None):
        self.sent: list[dict] = []
        self.answer = answer
        self.session = None
        self.headers = {"user-agent": "Mozilla/5.0 EmmaRobot/1.0"}

    async def send_text(self, text: str) -> None:
        frame = json.loads(text)
        frame["_at"] = time.monotonic()
        self.sent.append(frame)
        ack = self.answer(frame) if self.answer else None
        if ack is not None:
            kind = frame["type"].split("_")[0]
            asyncio.get_running_loop().call_soon(
                link.receive, self.session,
                {"type": "%s_ack" % kind, "command_id": frame["command_id"],
                 "action": frame["action"], **ack})

    def commands(self) -> list[str]:
        return [f["action"] for f in self.sent if f["type"] == "body_command"]


class Face:
    def __init__(self, answer=None):
        self.ws = Page(answer)
        self.ws.session = self
        link.claim(self)


def accept(frame):
    return {"accepted": True, "reason": None, "status": "verified", "est_ms": 6000}


@pytest.fixture
def body(monkeypatch):
    """Every server key open, the body group named, the real tool registered."""
    for flag in ("robot_body_enabled", "robot_arm_enabled", "robot_arm_motion_enabled",
                 "robot_arm_groups_enabled"):
        monkeypatch.setattr(settings, flag, True)
    monkeypatch.setattr(settings, "tool_groups", "robot,body")
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.5)
    import app.robot_body as robot_body
    import app.tools.body as body_tool

    entry = reg.get("move_body")
    if entry is None or entry.handler is not body_tool.move_body:
        # Another test cleared the registry while the module stayed imported:
        # re-run the decorator the way a fresh process would.
        reg._REGISTRY.pop("move_body", None)
        importlib.reload(body_tool)
    robot_body.reset_state()
    return robot_body


def run(face, coro_fn):
    """Run a scenario as the robot face's own session (the provider task's
    context), the way session.py binds it."""

    async def scenario():
        reg.robot_face_session.set(face)
        heard.bind_session(face)
        return await coro_fn()

    return asyncio.run(scenario())


def say(face, text, utterance="u1"):
    heard.record(text, face, utterance_id=utterance)


def caps(face, gestures=None, enabled=True, allow=False):
    link.receive(face, {"type": "body_caps", "enabled": enabled,
                        "allow_direction_known": allow,
                        "gestures": dict(CONTRACT_CAPS if gestures is None else gestures)})


# ==================== the declaration ====================


def test_parameters_are_exactly_the_action_enum(body):
    entry = reg.get("move_body")
    params = entry.parameters
    assert set(params["properties"]) == {"action"}
    assert params["properties"]["action"]["enum"] == list(rc.BODY_ACTION_IDS)
    assert params["required"] == ["action"]
    assert entry.exclusive is True and entry.group == "body"


def test_description_quotes_phrasings_and_the_question_guard(body):
    text = reg.get("move_body").description
    for phrase in ("'ก้มหน้าหน่อย'→head_down", "'ยกแขนซ้าย'→raise_left_arm",
                   "'หยุด' ระหว่างหุ่นกำลังทำท่า→stop", "ซ้าย/ขวาคือของตัวหุ่นเอง"):
        assert phrase in text
    assert "อย่าเรียกเพียงเพราะมีคนถาม" in text
    assert "ผลไม่สำเร็จห้ามเรียกซ้ำเอง" in text


@pytest.mark.parametrize("face,switch,visible", [
    (False, True, False), (True, False, False), (True, True, True)])
def test_hidden_without_the_face_or_the_switch(body, monkeypatch, face, switch, visible):
    monkeypatch.setattr(settings, "robot_body_enabled", switch)
    token = reg.robot_face_session.set(object() if face else None)
    try:
        assert reg.allowed(reg.get("move_body")) is visible
    finally:
        reg.robot_face_session.reset(token)


# ==================== step 2: the server's keys ====================


@pytest.mark.parametrize("flag", ["robot_body_enabled", "robot_arm_enabled",
                                  "robot_arm_motion_enabled"])
def test_each_interlock_flag_makes_it_a_mock(body, monkeypatch, flag):
    monkeypatch.setattr(settings, flag, False)
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    out = run(face, lambda: body.request("nod"))
    assert out["hardware"] == "mock" and out["reason"] == "server_disabled"
    assert out["ok"] is False
    assert "ห้ามบอกว่าทำแล้ว" in out["instruction"]
    assert face.ws.sent == [], "a closed key must not reach the page"


@pytest.mark.parametrize("action,phrase", [
    ("handshake", "จับมือกันไหม"), ("greet_arm", "ยกมือทักทาย"), ("rest", "เอาแขนลง")])
def test_groups_flag_closes_the_group_gestures(body, monkeypatch, action, phrase):
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", False)
    face = Face(accept)
    say(face, phrase)
    out = run(face, lambda: body.request(action))
    assert out["hardware"] == "mock" and out["reason"] == "server_disabled"
    assert face.ws.sent == []


def test_groups_flag_does_not_touch_a_frame_gesture(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", False)
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    out = run(face, lambda: body.request("nod"))
    assert out["hardware"] == "requested"
    assert face.ws.commands() == ["nod"]


def test_stop_bypasses_the_gate_and_the_heard_guard(body, monkeypatch):
    for flag in ("robot_body_enabled", "robot_arm_enabled", "robot_arm_motion_enabled",
                 "robot_arm_groups_enabled"):
        monkeypatch.setattr(settings, flag, False)
    face = Face(accept)      # nothing was said at all
    out = run(face, lambda: body.request("stop"))
    assert face.ws.commands() == ["stop"]
    assert out["ok"] is True and out["hardware"] == "requested"
    assert "ห้ามพูดว่า 'แขนหยุดแล้ว'" in out["instruction"]


def test_a_desk_stop_on_the_arm_page_refuses_every_gesture_but_stop(body, monkeypatch):
    """Review 12 Oct: /arm's STOP disarms until "arm" is pressed again; the
    camera greeting honoured it, the voice did not."""
    from app import robot_arm

    monkeypatch.setattr(robot_arm, "_armed", False)
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    out = run(face, lambda: body.request("nod"))
    assert out["ok"] is False and out["reason"] == "latched"
    assert face.ws.sent == [], "a disarmed arm must not reach the page"
    out = run(face, lambda: body.request("stop"))
    assert face.ws.commands() == ["stop"]


def test_no_face_session_is_not_robot_face(body, monkeypatch):
    """Decided before any round trip — not left to the link to refuse."""
    tried = []

    async def fake_request(*a, **k):
        tried.append(a)
        return {"accepted": False, "reason": "not_robot_face"}

    monkeypatch.setattr(link, "request", fake_request)

    async def scenario():
        reg.robot_face_session.set(None)
        return await body.request("stop")

    out = asyncio.run(scenario())
    assert out["reason"] == "not_robot_face" and out["ok"] is False
    assert tried == []


def test_unknown_action_is_a_bad_request(body):
    face = Face(accept)
    out = run(face, lambda: body.request("backflip"))
    assert out["reason"] == "bad_request" and face.ws.sent == []


# ==================== step 4: the heard guard ====================


LOOKALIKES = ["ลดราคา", "ข้างหน้า", "ยกเลิก", "มือถือ", "ไปไหนมา", "ห้องพักมีกี่แบบ"]


@pytest.mark.parametrize("phrase", LOOKALIKES)
def test_lookalikes_authorise_no_gesture(body, phrase):
    for action in rc.BODY_HEARD:
        face = Face(accept)
        say(face, phrase)
        out = run(face, lambda a=action: body.request(a))
        assert out["reason"] == "heard_missing", (phrase, action)
        assert face.ws.sent == []
        assert "ให้ถามสั้นๆ" in out["instruction"]


def test_the_verb_must_be_the_actions_own(body):
    face = Face(accept)
    say(face, "ก้มหน้าหน่อย")
    assert run(face, lambda: body.request("raise_left_arm"))["reason"] == "heard_missing"
    face = Face(accept)
    say(face, "ก้มหน้าหน่อย")
    assert run(face, lambda: body.request("head_down"))["hardware"] == "requested"


def test_the_side_must_match(body):
    face = Face(accept)
    say(face, "ยกแขนซ้าย")
    assert run(face, lambda: body.request("raise_right_arm"))["reason"] == "heard_missing"
    assert face.ws.sent == []


def test_a_caption_before_the_latest_activity_start_is_stale(body):
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    time.sleep(0.002)
    heard.mark_activity(face)        # new speech began; its caption has not landed
    out = run(face, lambda: body.request("nod"))
    assert out["reason"] == "heard_missing" and face.ws.sent == []


def test_a_caption_older_than_the_age_bound_is_stale(body, monkeypatch):
    face = Face(accept)
    heard.record("พยักหน้าหน่อย", face, utterance_id="u1", now=time.monotonic() - 11.0)
    out = run(face, lambda: body.request("nod"))
    assert out["reason"] == "heard_missing"


def test_one_sentence_authorises_one_motion(body):
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    first = run(face, lambda: body.request("nod"))
    second = run(face, lambda: body.request("nod"))
    assert first["hardware"] == "requested"
    assert second["reason"] == "heard_missing"
    assert face.ws.commands() == ["nod"]


def test_a_caption_that_asked_for_something_else_is_not_spent(body):
    """Consumed only when the words match: the wrong call must not use up
    the sentence the right call needs."""
    face = Face(accept)
    say(face, "พยักหน้าหน่อย")
    assert run(face, lambda: body.request("wave"))["reason"] == "heard_missing"
    assert run(face, lambda: body.request("nod"))["hardware"] == "requested"


def test_the_guard_switch_off_lets_it_through(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_body_require_heard", False)
    face = Face(accept)
    out = run(face, lambda: body.request("nod"))
    assert out["hardware"] == "requested"


def test_without_the_thai_segmenter_thai_authorises_nothing(body, monkeypatch):
    """The n-gram fallback hands out `พัก` from `ห้องพัก` (a condo word) as
    if it were a word — `rest` would ride on a question about rooms."""
    from app.tools import retrieval

    monkeypatch.setattr(retrieval, "_tokeniser", None)
    monkeypatch.setattr(retrieval, "_tokeniser_ready", True)
    face = Face(accept)
    say(face, "ห้องพักมีกี่แบบ")
    assert run(face, lambda: body.request("rest"))["reason"] == "heard_missing"
    # Latin still works without the segmenter.
    face = Face(accept)
    say(face, "please nod")
    assert run(face, lambda: body.request("nod"))["hardware"] == "requested"


# ==================== step 5: what the robot already said ====================


@pytest.mark.parametrize("action", ["head_up", "shake_head", "head_left", "head_right"])
def test_cached_needs_mapping_refuses_without_a_round_trip(body, monkeypatch, action):
    monkeypatch.setattr(settings, "robot_body_require_heard", False)
    face = Face(accept)
    caps(face)
    out = run(face, lambda: body.request(action))
    assert out["reason"] == "needs_mapping" and out["hardware"] == "refused"
    assert face.ws.sent == [], "the cache answers; nothing goes to the page"
    assert out["available"] == ["greet_arm", "handshake", "rest"]
    assert "ยกแขนทักทาย" in out["instruction"], "the instruction lists what she can do"


def test_direction_known_is_refused_until_the_robot_allows_it(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_body_require_heard", False)
    face = Face(accept)
    caps(face, allow=False)
    out = run(face, lambda: body.request("head_down"))
    assert out["reason"] == "direction_unconfirmed" and face.ws.sent == []
    caps(face, allow=True)
    out = run(face, lambda: body.request("head_down"))
    assert out["hardware"] == "requested" and face.ws.commands() == ["head_down"]
    assert "nod" in out["available"]


# ==================== steps 6-7: the ack ====================


def test_accepted_is_requested_not_done(body):
    face = Face(accept)
    say(face, "จับมือกันไหม")
    out = run(face, lambda: body.request("handshake"))
    assert out["ok"] is True and out["hardware"] == "requested"
    assert out["status"] == "verified" and out["est_s"] == 6.0
    assert "ห้ามพูดว่า 'ทำเสร็จแล้ว'" in out["instruction"]


def test_a_robot_refusal_carries_its_thai_reason_and_what_is_available(body):
    face = Face(lambda f: {"accepted": False, "reason": "latched", "status": "verified"})
    caps(face)
    say(face, "จับมือกันไหม")
    out = run(face, lambda: body.request("handshake"))
    assert out["ok"] is False and out["hardware"] == "refused"
    assert out["reason"] == "latched"
    assert rc.BODY_REASONS["latched"] in out["instruction"]
    assert out["available"] == ["greet_arm", "handshake", "rest"]


def test_busy_on_the_robot_says_wait(body):
    face = Face(lambda f: {"accepted": False, "reason": "busy"})
    say(face, "จับมือกันไหม")
    out = run(face, lambda: body.request("handshake"))
    assert out["reason"] == "busy" and "รอสักครู่" in out["instruction"]


def test_timeout_is_failed_and_must_not_be_retried(body):
    face = Face(None)        # a page that never answers
    say(face, "จับมือกันไหม")
    out = run(face, lambda: body.request("handshake"))
    assert out["ok"] is False and out["hardware"] == "failed"
    assert out["reason"] == "timeout"
    assert "ห้ามสั่งซ้ำ" in out["instruction"]


def test_a_timed_out_gesture_is_followed_by_a_stop_and_says_so(body):
    """Finding #5: the page relays the gesture whether or not its ack made
    it back. The arm may be moving, so a body stop follows on the wire
    before the tool answers, and the model is told that — never that the
    gesture "could not be ordered"."""
    face = Face(None)
    say(face, "จับมือกันไหม")
    out = run(face, lambda: body.request("handshake"))
    assert face.ws.commands() == ["handshake", "stop"]
    assert "ส่งคำสั่งหยุดแขนตามไปแล้ว" in out["instruction"]
    assert "ปุ่มหยุดฉุกเฉิน" in out["instruction"]
    assert "สั่งท่าไม่ได้" not in out["instruction"]


def test_every_instruction_asks_for_the_users_language(body, monkeypatch):
    face = Face(lambda f: {"accepted": False, "reason": "busy"})
    outs = [run(face, lambda: body.request("nod"))]            # heard_missing
    say(face, "พยักหน้าหน่อย")
    outs.append(run(face, lambda: body.request("nod")))        # busy
    outs.append(run(Face(accept), lambda: body.request("stop")))
    monkeypatch.setattr(settings, "robot_body_enabled", False)
    outs.append(run(face, lambda: body.request("nod")))        # server_disabled
    for out in outs:
        assert out["instruction"].endswith("ตอบเป็นภาษาเดียวกับที่ผู้ใช้พูด"), out


# ==================== the registry: busy, and a stop that never is ====================


def test_a_second_gesture_is_busy_while_the_first_waits(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_body_require_heard", False)
    face = Face(None)

    async def scenario():
        first = asyncio.create_task(reg.dispatch("move_body", {"action": "nod"}))
        await asyncio.sleep(0.05)
        second = await reg.dispatch("move_body", {"action": "head_down"})
        await first
        return second

    second = run(face, scenario)
    assert second["error"] == "busy"
    # head_down never left; the stop is the nod's own timeout (finding #5).
    assert face.ws.commands() == ["nod", "stop"]


def test_stop_is_never_busy(body, monkeypatch):
    """Revert-red against `preempt`: a nod whose ack never comes must not
    hold the stop behind it. The stop must reach the page within 100 ms."""
    monkeypatch.setattr(settings, "robot_body_require_heard", False)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 1.0)
    face = Face(lambda f: accept(f) if f["action"] == "stop" else None)

    async def scenario():
        first = asyncio.create_task(reg.dispatch("move_body", {"action": "nod"}))
        await asyncio.sleep(0.05)
        asked = time.monotonic()
        stop = await reg.dispatch("move_body", {"action": "stop"})
        await first
        return asked, stop

    asked, stop = run(face, scenario)
    stops = [f for f in face.ws.sent if f["action"] == "stop"]
    assert stop.get("error") != "busy"
    assert stop["ok"] is True
    assert stops and stops[0]["_at"] - asked < 0.1


# ==================== afterwards: body_status ====================


def test_body_status_updates_state_and_announces_nothing(body, monkeypatch):
    from app import events, session as session_module, turnlog

    face = Face(accept)
    session_module._active = face        # an announcement would not be dropped
    announced = []

    async def fake_announce(*a, **k):
        announced.append((a, k))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    logged = []
    monkeypatch.setattr(turnlog, "record", lambda kind, **f: logged.append((kind, f)))

    async def scenario():
        link.receive(face, {"type": "body_status", "seq": 3, "action": "handshake",
                            "state": "done", "reason": None})
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert body.STATE["last_action"] == "handshake" and body.STATE["state"] == "done"
    assert body.STATE["at"] is not None
    assert ("body", {"action": "handshake", "state": "done", "reason": None}) in logged
    assert announced == [], "a gesture ending is never spoken"


def test_a_repeated_status_frame_is_logged_once(body, monkeypatch):
    from app import turnlog

    logged = []
    monkeypatch.setattr(turnlog, "record", lambda kind, **f: logged.append(kind))
    face = Face(accept)
    frame = {"type": "body_status", "seq": 4, "action": "nod", "state": "started",
             "reason": None}
    link.receive(face, frame)
    link.receive(face, dict(frame))
    assert logged.count("body") == 1


def test_refusals_are_logged_by_code_only(body, monkeypatch):
    from app import turnlog

    logged = []
    monkeypatch.setattr(turnlog, "record", lambda kind, **f: logged.append((kind, f)))
    face = Face(accept)
    say(face, "ลดราคาไหม")
    run(face, lambda: body.request("nod"))
    assert ("body_refused", {"action": "nod", "reason": "heard_missing",
                             "hardware": "refused"}) in logged
    assert not any("ลดราคา" in json.dumps(f, ensure_ascii=False) for _, f in logged)


# ==================== stop_moving with routes off (findings #6/#17) ====================


def _stop_moving(face):
    import app.tools.robot  # noqa: F401  (registers stop_moving)

    return run(face, lambda: reg.dispatch("stop_moving", {}))


def test_stop_moving_also_stops_the_arm_when_routes_are_off(body):
    """Body on, routes off: "หยุด" during a gesture can be routed to
    stop_moving, whose planner cancel never reached the arm. Revert-red
    against the face-session branch in stop_moving."""
    assert settings.robot_nav_routes_enabled is False
    face = Face(accept)
    out = _stop_moving(face)
    assert face.ws.commands() == ["stop"]
    assert out["body_stop"]["accepted"] is True
    assert "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"]


def test_the_body_switch_off_does_not_block_that_arm_stop(body, monkeypatch):
    """A switch must never be the reason something cannot stop."""
    monkeypatch.setattr(settings, "robot_body_enabled", False)
    face = Face(accept)
    _stop_moving(face)
    assert face.ws.commands() == ["stop"]


def test_an_unanswered_arm_stop_points_at_the_red_button(body, monkeypatch):
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.05)
    face = Face(None)
    out = _stop_moving(face)
    assert out["body_stop"]["accepted"] is False
    assert "ปุ่มหยุดฉุกเฉิน" in out["instruction"] and "ห้ามบอกว่าหยุดแล้ว" in out["instruction"]


def test_a_desk_conversation_sends_no_arm_stop(body):
    """No robot face session: the sales room's stop_moving, unchanged."""
    import app.tools.robot  # noqa: F401

    out = asyncio.run(reg.dispatch("stop_moving", {}))
    assert "body_stop" not in out


# ==================== review 2: what a refusal or a stop is allowed to claim ====================


def test_nav_unknown_is_not_said_as_walking(body):
    """Review 2, finding #6: the app answers nav_unknown whenever its own nav
    poll is not fresh (before the first poll, or for good when su fails).
    That is "cannot tell", and it shared the "หุ่นกำลังเดินอยู่" text with
    navigating — Emma told guests the robot was walking while it stood."""
    say_gesture = "จับมือกันไหม"
    unknown_face = Face(lambda f: {"accepted": False, "reason": "nav_unknown"})
    say(unknown_face, say_gesture)
    unknown = run(unknown_face, lambda: body.request("handshake"))
    walking_face = Face(lambda f: {"accepted": False, "reason": "navigating"})
    say(walking_face, say_gesture, utterance="u2")
    walking = run(walking_face, lambda: body.request("handshake"))
    assert unknown["reason"] == "nav_unknown" and walking["reason"] == "navigating"
    assert unknown["instruction"] != walking["instruction"]
    assert walking["instruction"].startswith("หุ่นกำลังเดินอยู่")
    assert not unknown["instruction"].startswith("หุ่นกำลังเดินอยู่")
    assert "ห้ามพูดว่า 'หุ่นกำลังเดินอยู่'" in unknown["instruction"]


def test_a_stop_the_robot_did_not_ack_may_be_sent_again(body, monkeypatch):
    """Review 2, finding #15: a timed-out stop got the gesture text
    "ห้ามสั่งซ้ำเอง". A stop is idempotent and never refused on a retry."""
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.05)
    face = Face(None)
    out = run(face, lambda: body.request("stop"))
    assert out["ok"] is False and out["hardware"] == "failed" and out["reason"] == "timeout"
    assert "ห้ามสั่งซ้ำ" not in out["instruction"]
    assert "สั่งหยุดซ้ำได้" in out["instruction"]
    assert "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"] and "ปุ่มหยุดฉุกเฉิน" in out["instruction"]


def test_a_stop_with_no_face_session_is_not_told_never_to_retry(body):
    out = run(None, lambda: body.request("stop"))
    assert out["reason"] == "not_robot_face" and out["hardware"] == "failed"
    assert "ห้ามสั่งซ้ำ" not in out["instruction"]


def _stop_with_board(face, monkeypatch, hardware):
    from app.tools import robot_link

    async def board_send(action, **args):
        return hardware

    monkeypatch.setattr(robot_link, "send", board_send)
    if face is None:
        import app.tools.robot  # noqa: F401

        return asyncio.run(reg.dispatch("stop_moving", {}))
    return _stop_moving(face)


def test_a_gesture_only_stop_is_answered_from_the_arm_stop(body, monkeypatch):
    """Review 2, finding #29: body on, routes off, no wheels connected
    ("mock"). The accepted arm stop used to be appended to "ยังยืนยันการหยุด
    ไม่ได้ … ให้แจ้งเจ้าหน้าที่ตรวจสอบหุ่น" — staff fetched for an arm stop
    that worked, and no quoted reply."""
    out = _stop_with_board(Face(accept), monkeypatch, "mock")
    assert out["ok"] is True and out["body_stop"]["accepted"] is True
    assert "ตรวจสอบหุ่น" not in out["instruction"]
    assert "ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ'" in out["instruction"]
    assert "'เรียบร้อยค่ะ'" in out["instruction"]


@pytest.mark.parametrize("hardware", ["ok", "mock", "failed"])
def test_every_routes_off_stop_quotes_what_must_not_be_said(body, monkeypatch, hardware):
    """The bare "ตอบรับสั้นๆ" on a sent stop is what let 'เรียบร้อยค่ะ' through
    on the routes path (live eval 9 ต.ค.); only a real failure sends people
    for staff."""
    out = _stop_with_board(None, monkeypatch, hardware)
    assert "ตอบรับสั้นๆ" not in out["instruction"]
    assert "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"]
    assert ("ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ'" in out["instruction"]) == (hardware == "ok")
    assert ("เจ้าหน้าที่" in out["instruction"]) == (hardware == "failed")
