"""Robot-face navigation by voice: go_to_place / return_to_base / stop_moving /
get_robot_status in routes mode (docs/emma-body-nav-design-2026-10-08.md §2d, P2).

Nothing here reaches a robot. The face page is a fake socket that records
every frame the server sends and answers through the real
`robot_face_link.receive`, so routing, validation and per-session state are
what is under test. The places table is a fixture written per test: conftest
pins ROBOT_NAV_PLACES_FILE empty so no test reads the tracked floor plan.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import heard, robot_face_link, robot_nav
from app import session as session_module
from app.config import settings
from app.tools import registry, robot_link

SHA = "ab" * 32
RUN_A = "a" * 32
RUN_B = "b" * 32


def run(coro):
    return asyncio.run(coro)


def places_file(tmp_path, *, to_room_verified=True, to_dock_verified=False):
    data = {
        "format": 1, "route": "data/robot/routes/floor2_bigroom_roundtrip.json",
        "route_sha256": SHA,
        "places": [
            {"id": "dock", "name_th": "แท่นชาร์จ", "name_en": "charging dock",
             "aliases_th": ["ที่ชาร์จ", "แท่น", "จุดจอด"], "aliases_en": ["dock", "charger"],
             "kind": "dock"},
            {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
             "aliases_th": ["ห้องประชุม"], "aliases_en": ["meeting room"], "kind": "room"},
        ],
        "legs": [
            {"from": "dock", "to": "big_room", "from_step": 1, "to_step": 10,
             "min_battery": 30, "verified": to_room_verified, "est_s": None,
             "doors": ["D8", "D11"], "note": ""},
            {"from": "big_room", "to": "dock", "from_step": 10, "to_step": 20,
             "min_battery": 10, "verified": to_dock_verified, "est_s": None,
             "doors": ["D11", "D8"], "note": ""},
        ],
    }
    path = tmp_path / "floor2.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


class Client:
    def __init__(self, host):
        self.host = host


class Page:
    """The face page's socket."""

    def __init__(self, host="10.0.0.5"):
        self.sent: list[dict] = []
        self.client = Client(host)

    async def send_text(self, text):
        self.sent.append(json.loads(text))


class Provider:
    def __init__(self):
        self.said: list[str] = []

    async def send_text(self, text):
        self.said.append(text)


class Face:
    def __init__(self, host="10.0.0.5"):
        self.ws = Page(host)
        self.provider = Provider()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    from app.tools import load_tools

    load_tools()
    robot_link.reset_state()
    monkeypatch.setattr(settings, "turn_log", False)
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    monkeypatch.setattr(settings, "robot_nav_voice", "on")
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 1.0)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 1.0)
    monkeypatch.setattr(settings, "robot_nav_places_file", str(places_file(tmp_path)))
    yield
    robot_link.reset_state()


@pytest.fixture
def face(monkeypatch):
    """The robot face: claimed, the provider task's face, and `_active`."""
    f = Face()
    robot_face_link.claim(f)
    token = registry.robot_face_session.set(f)
    monkeypatch.setattr(session_module, "_active", f)
    yield f
    registry.robot_face_session.reset(token)


def say(session, text):
    """The guest said `text`: a fresh caption after a fresh activity_start."""
    heard.mark_activity(session)
    heard.record(text, session=session, utterance_id="u%d" % id(text))


async def answer(session, index, **ack):
    """Ack the index-th command the way the page would."""
    for _ in range(400):
        if len(session.ws.sent) > index:
            break
        await asyncio.sleep(0.005)
    cmd = session.ws.sent[index]
    kind = cmd["type"].split("_")[0]
    robot_face_link.receive(session, {"type": "%s_ack" % kind,
                                      "command_id": cmd["command_id"],
                                      "action": cmd["action"], **ack})
    return cmd


async def go(session, place, *, accept=True, run_id=RUN_A, reason=None, answer_it=True):
    task = None
    if answer_it:
        task = asyncio.ensure_future(answer(
            session, len(session.ws.sent), accepted=accept,
            reason=None if accept else reason, run_id=run_id if accept else None))
    out = await registry.dispatch("go_to_place", {"place": place})
    if task is not None:
        if not task.done():
            task.cancel()
        else:
            task.result()
    return out


def nav_cmds(session):
    return [c for c in session.ws.sent if c["type"] == "nav_command"]


# ==================== routing: only the face, only with the switch ====================


def test_routes_mode_needs_the_switch_and_the_face(monkeypatch, face):
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่"))
    assert out["hardware"] == "requested" and nav_cmds(face)[0]["place"] == "big_room"

    # Switch off: the old planner path, which here is mock — and no frame to the face.
    # A fresh caption, so it is the switch and not a spent sentence that decides.
    face.ws.sent.clear()
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(registry.dispatch("go_to_place", {"place": "ห้องประชุมใหญ่"}))
    assert face.ws.sent == [] and out.get("hardware") != "requested"


def test_a_desk_session_takes_the_old_path_even_with_routes_on(monkeypatch):
    desk = Face()
    monkeypatch.setattr(session_module, "_active", desk)
    say(desk, "พาไปห้องประชุมใหญ่")
    out = run(registry.dispatch("go_to_place", {"place": "ห้องประชุมใหญ่"}))
    assert desk.ws.sent == []
    assert "reason" not in out          # the planner tool's own shape, not robot_nav's


# ==================== the voice key ====================


def test_voice_is_off_by_default_and_sends_nothing(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_voice", "off")
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["ok"] is False and out["reason"] == "voice_disabled"
    assert "หน้าจอ" in out["instruction"] and "ห้ามบอกว่ากำลัง" in out["instruction"]
    assert face.ws.sent == []


def test_a_typo_in_the_voice_mode_is_off(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_voice", "yes")
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "voice_disabled" and face.ws.sent == []


def test_unlocked_mode_needs_a_pin_unlock_inside_its_ttl(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_voice", "unlocked")
    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_pin", "2468")
    clock = [1000.0]
    monkeypatch.setattr(robot_nav, "_clock", lambda: clock[0])

    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "voice_disabled"

    # An unlock from another machine does not open this face.
    robot_nav.unlock("2468", "10.0.0.99")
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "voice_disabled"

    assert robot_nav.unlock("2468", "10.0.0.5") == {"ok": True, "ttl_s": 120}
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"

    clock[0] += robot_nav.UNLOCK_TTL_S + 1
    face.ws.sent.clear()
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "voice_disabled"
    assert face.ws.sent == []


def _pin_mode(monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_voice", "unlocked")
    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_pin", "2468")


def test_a_desk_unlock_does_not_open_a_face_behind_adb_reverse(monkeypatch):
    """Finding #23: under adb reverse the face's peer is 127.0.0.1 — and so is
    every browser on the server PC, which /robot-nav/unlock lets in without
    a token. A PIN typed at the desk must not enable voice walks on the
    robot; one sent over the face's own socket does, for that face only."""
    _pin_mode(monkeypatch)
    face = Face(host="127.0.0.1")
    robot_face_link.claim(face)
    assert robot_nav.unlock("2468", "127.0.0.1") == {"ok": True, "ttl_s": 120}
    assert robot_nav.unlocked(face) is False
    assert robot_nav.voice_may_start(face) == (False, "voice_disabled")

    robot_nav.unlock("2468", "127.0.0.1", session=face)
    assert robot_nav.unlocked(face) is True
    other = Face(host="127.0.0.1")
    robot_face_link.claim(other)
    assert robot_nav.unlocked(other) is False, "the unlock is the session's, not the address's"


@pytest.mark.parametrize("host", ["::1", "localhost", "::ffff:127.0.0.1", "127.0.0.2"])
def test_every_loopback_spelling_is_loopback(monkeypatch, host):
    _pin_mode(monkeypatch)
    face = Face(host=host)
    robot_nav.unlock("2468", host)
    assert robot_nav.unlocked(face) is False


def test_a_socket_unlock_ends_with_its_ttl_and_its_session(monkeypatch):
    _pin_mode(monkeypatch)
    clock = [10.0]
    monkeypatch.setattr(robot_nav, "_clock", lambda: clock[0])
    face = Face(host="127.0.0.1")
    robot_face_link.claim(face)
    robot_nav.unlock("2468", "127.0.0.1", session=face)
    clock[0] += robot_nav.UNLOCK_TTL_S + 1
    assert robot_nav.unlocked(face) is False
    robot_nav.unlock("2468", "127.0.0.1", session=face)
    robot_face_link.session_closed(face)
    assert robot_nav.unlocked(face) is False


def _session_on(host, user_agent="Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1"):
    """A VoiceSession built the way test_robot_face_link builds one, with the
    frames of `_browser_to_provider` scripted."""
    sess = session_module.VoiceSession.__new__(session_module.VoiceSession)

    class WS:
        def __init__(self):
            self.queue = []
            self.sent = []
            self.client = Client(host)

        async def receive(self):
            return self.queue.pop(0)

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    sess.ws = WS()
    sess._ears_closed_until = None
    sess.provider = None
    if "EmmaRobot/" in user_agent:
        robot_face_link.claim(sess)
    return sess


def _feed(sess, *frames):
    sess.ws.queue = [{"type": "websocket.receive", "text": json.dumps(f)} for f in frames]
    sess.ws.queue.append({"type": "websocket.disconnect"})
    run(sess._browser_to_provider())


def test_the_face_unlocks_over_its_own_socket(monkeypatch):
    _pin_mode(monkeypatch)
    sess = _session_on("127.0.0.1")
    _feed(sess, {"type": "nav_unlock", "pin": "2468"})
    assert robot_nav.unlocked(sess) is True
    assert sess.ws.sent == [{"type": "nav_unlock_result", "ok": True, "ttl_s": 120}]


def test_a_wrong_pin_over_the_socket_unlocks_nothing(monkeypatch):
    _pin_mode(monkeypatch)
    sess = _session_on("127.0.0.1")
    _feed(sess, {"type": "nav_unlock", "pin": "0000"})
    assert robot_nav.unlocked(sess) is False
    assert sess.ws.sent[0]["ok"] is False and sess.ws.sent[0]["status"] == 403
    assert "0000" not in json.dumps(sess.ws.sent)


def test_a_desk_tab_cannot_unlock_over_its_socket(monkeypatch):
    _pin_mode(monkeypatch)
    desk = _session_on("127.0.0.1", user_agent="Mozilla/5.0 (Windows NT 10.0)")
    _feed(desk, {"type": "nav_unlock", "pin": "2468"})
    assert robot_nav.unlocked(desk) is False
    assert desk.ws.sent[0]["ok"] is False


# ==================== which place ====================


def test_places_match_by_whole_words(face):
    say(face, "ไปห้องประชุมหน่อย")
    out = run(go(face, "ห้องประชุม"))          # the alias
    assert out["place"] == "big_room"
    say(face, "ไปห้องน้ำ")
    out = run(go(face, "ห้องน้ำ", answer_it=False))
    assert out["reason"] == "unknown_place"
    assert out["known_places"] == ["แท่นชาร์จ", "ห้องประชุมใหญ่"]
    assert len(nav_cmds(face)) == 1


def test_an_unusable_places_file_refuses_rather_than_guesses(monkeypatch, face, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": 1, "route": "x"}', encoding="utf-8")
    monkeypatch.setattr(settings, "robot_nav_places_file", str(bad))
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "places_stale" and face.ws.sent == []


# ==================== legs not yet verified are tap-only ====================


def test_an_unverified_leg_is_refused_for_voice(monkeypatch, face, tmp_path):
    monkeypatch.setattr(settings, "robot_nav_places_file",
                        str(places_file(tmp_path, to_room_verified=False)))
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "leg_unverified" and face.ws.sent == []

    # With `here` known the same rule holds on that exact leg.
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "here": "dock", "places": []})
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "leg_unverified"

    # The switch skips only the SERVER's pre-check (review 2, finding #16):
    # the app and the launcher still refuse a voice go on an unverified leg,
    # so a real robot answers leg_unverified anyway. This fake face acks.
    monkeypatch.setattr(settings, "robot_nav_voice_unverified", True)
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"


def test_here_decides_already_there_and_the_leg(face):
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "here": "big_room", "places": []})
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "already_there"
    # big_room -> dock is unverified in the fixture: voice may not start it.
    say(face, "กลับแท่นชาร์จ")
    out = run(registry.dispatch("return_to_base", {}))
    assert out["reason"] == "leg_unverified" and face.ws.sent == []


def test_an_arrival_beats_caps_from_before_it(monkeypatch, face, tmp_path):
    """Review 2, finding #5: when a walk ends the page forces caps from the
    app's cache, which still says where the walk LEFT from. "กลับแท่นชาร์จ"
    in big_room was answered already_there ('อยู่ที่นี่แล้ว')."""
    monkeypatch.setattr(settings, "robot_nav_places_file",
                        str(places_file(tmp_path, to_dock_verified=True)))
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "here": "dock", "places": []})
    robot_face_link.receive(face, {"type": "nav_status", "state": "arrived", "run_id": RUN_A,
                                   "origin": "tap", "place": "big_room"})
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "here": "dock", "places": [],
                                   "run": {"state": "running", "run_id": RUN_A}})
    assert robot_nav.status(face)["nav"]["here"] == "big_room"
    say(face, "กลับแท่นชาร์จ")

    async def scenario():
        task = asyncio.ensure_future(answer(face, 0, accepted=True, reason=None, run_id=RUN_B))
        out = await registry.dispatch("return_to_base", {})
        await task
        return out

    out = run(scenario())
    assert out["hardware"] == "requested" and nav_cmds(face)[0]["place"] == "dock"


# ==================== the heard guard names the place ====================


@pytest.mark.parametrize("caption", ["ไปไหนมา", "ไปเลย", "ห้องน้ำอยู่ไหน", "ลดราคาไหม"])
def test_a_motion_word_alone_moves_nothing(face, caption):
    say(face, caption)
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "heard_missing"
    assert "ถาม" in out["instruction"]
    assert face.ws.sent == []


def test_the_caption_naming_the_place_passes(face):
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"


@pytest.mark.parametrize("caption", [
    "อย่าไปห้องประชุมใหญ่", "ไม่ต้องไปห้องประชุมใหญ่", "ห้องประชุมใหญ่อยู่ไหน",
    "ไม่อยากไปห้องประชุมใหญ่", "ห้องประชุมใหญ่ไปยังไง",
])
def test_a_refusal_or_a_where_question_naming_the_place_walks_nowhere(face, caption):
    """Review #18: these name big_room, and used to start the walk."""
    say(face, caption)
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "heard_missing"
    assert nav_cmds(face) == []
    # Nothing was spent: the guest's next real request still walks.
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"


@pytest.mark.parametrize("caption", ["ห้องประชุมใหญ่อยู่ไหน", "อย่าไปห้องประชุมใหญ่"])
def test_a_where_question_or_a_no_is_not_asked_where_to_go(face, caption):
    """Review 2, finding #27 (live eval R-heard-where-room): the generic
    heard_missing text said 'ให้ถามสั้นๆ ว่าต้องการไปที่ไหน', and Emma
    answered a where-question with 'ไม่แน่ใจว่าต้องการให้พาไปที่ไหนนะคะ'."""
    say(face, caption)
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["reason"] == "heard_missing" and out["heard"] == "not_a_request"
    assert "ห้ามพูดว่า 'ไม่แน่ใจว่าต้องการให้พาไปที่ไหนนะคะ'" in out["instruction"]
    assert "ให้ถามสั้นๆ ว่าต้องการไปที่ไหน" not in out["instruction"]
    assert nav_cmds(face) == []


def test_a_goodbye_is_not_asked_which_point_to_return_to(monkeypatch, face, tmp_path):
    """Live eval R-heard-going-home: 'กลับบ้านก่อนนะ' was answered
    'ไม่แน่ใจว่าต้องการให้กลับที่จุดไหน'. Not a decline or a where-question
    (บ้าน is just not the dock), so it is the generic text — which now says
    a goodbye is answered as one, quoting the sentence."""
    monkeypatch.setattr(settings, "robot_nav_places_file",
                        str(places_file(tmp_path, to_dock_verified=True)))
    say(face, "กลับบ้านก่อนนะ")
    out = run(registry.dispatch("return_to_base", {}))
    assert out["reason"] == "heard_missing" and "heard" not in out
    assert "บอกลา" in out["instruction"]
    assert "ห้ามพูดว่า 'ไม่แน่ใจว่าต้องการให้กลับที่จุดไหน'" in out["instruction"]
    assert face.ws.sent == []


def test_a_caption_naming_another_place_does_not_pass(face):
    say(face, "ไปแท่นชาร์จ")
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "heard_missing"


def test_going_home_is_a_goodbye_not_the_dock(monkeypatch, face, tmp_path):
    monkeypatch.setattr(settings, "robot_nav_places_file",
                        str(places_file(tmp_path, to_dock_verified=True)))
    say(face, "กลับบ้าน")
    out = run(registry.dispatch("return_to_base", {}))
    assert out["reason"] == "heard_missing" and face.ws.sent == []


@pytest.mark.parametrize("caption", ["กลับแท่นชาร์จ", "กลับฐาน", "ไปที่ชาร์จ"])
def test_the_dock_by_name_or_by_กลับ_with_แท่น_ฐาน(monkeypatch, face, tmp_path, caption):
    monkeypatch.setattr(settings, "robot_nav_places_file",
                        str(places_file(tmp_path, to_dock_verified=True)))
    say(face, caption)

    async def scenario():
        task = asyncio.ensure_future(answer(face, 0, accepted=True, reason=None, run_id=RUN_A))
        out = await registry.dispatch("return_to_base", {})
        await task
        return out

    out = run(scenario())
    assert out["hardware"] == "requested"
    assert nav_cmds(face)[0] == {"type": "nav_command", "command_id": nav_cmds(face)[0]["command_id"],
                                 "action": "go", "place": "dock", "origin": "voice"}


def test_a_caption_from_before_the_latest_activity_start_is_stale(face):
    heard.record("พาไปห้องประชุมใหญ่", session=face, utterance_id="old")
    heard.mark_activity(face)          # new speech began; nothing captioned yet
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "heard_missing"


def test_one_sentence_starts_one_walk(face):
    say(face, "พาไปห้องประชุมใหญ่")
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"
    assert run(go(face, "ห้องประชุมใหญ่", answer_it=False))["reason"] == "heard_missing"
    assert len(nav_cmds(face)) == 1


def test_the_guard_can_be_switched_off(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_require_heard", False)
    assert run(go(face, "ห้องประชุมใหญ่"))["hardware"] == "requested"


# ==================== the ack ====================


def test_accepted_is_requested_not_arrived(face):
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่"))
    assert out["ok"] is True and out["hardware"] == "requested" and out["run_id"] == RUN_A
    assert "ห้ามบอกว่าถึงแล้ว" in out["instruction"]
    assert "ตอบเป็นภาษาเดียวกับที่ผู้ใช้พูด" in out["instruction"]
    assert nav_cmds(face)[0]["origin"] == "voice"
    assert set(nav_cmds(face)[0]) == {"type", "command_id", "action", "place", "origin"}


def test_a_refusal_carries_the_thai_reason(face):
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", accept=False, reason="low_battery"))
    assert out["ok"] is False and out["hardware"] == "refused"
    assert out["reason"] == "low_battery" and "แบตเตอรี่ไม่พอ" in out["instruction"]


def test_no_ack_is_failed_and_says_not_to_retry(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert out["ok"] is False and out["hardware"] == "failed" and out["reason"] == "timeout"
    assert "ห้ามสั่งซ้ำ" in out["instruction"]


def test_a_timed_out_go_is_followed_by_a_stop_and_says_so(monkeypatch, face):
    """Finding #5: the page relays the go whether or not its ack made it
    back, so the robot may be leaving. A nav stop is on the wire before the
    tool answers, and the model is not told "สั่งเดินไม่ได้"."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)
    say(face, "พาไปห้องประชุมใหญ่")
    out = run(go(face, "ห้องประชุมใหญ่", answer_it=False))
    assert [c["action"] for c in nav_cmds(face)] == ["go", "stop"]
    assert "ส่งคำสั่งหยุดตามไปแล้ว" in out["instruction"]
    assert "สั่งเดินไม่ได้" not in out["instruction"]
    assert "ปุ่มหยุดฉุกเฉิน" in out["instruction"]


def test_a_late_accepted_go_is_stopped_and_its_end_announced(monkeypatch, face):
    """The go's ack lands after the timeout: a second stop goes out, and the
    run is this session's, so the robot's stopped status is still spoken."""
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        out = await go(face, "ห้องประชุมใหญ่", answer_it=False)
        go_cmd = nav_cmds(face)[0]
        robot_face_link.receive(face, {"type": "nav_ack", "command_id": go_cmd["command_id"],
                                       "action": "go", "accepted": True, "run_id": RUN_A})
        await asyncio.sleep(0.1)
        terminal(face, state="stopped", reason="stop_pressed")
        await settle()
        return out

    out = run(scenario())
    assert out["reason"] == "timeout"
    assert [c["action"] for c in nav_cmds(face)] == ["go", "stop", "stop"]
    assert len(face.provider.said) == 1 and "ไม่สำเร็จ" in face.provider.said[0]


# ==================== the end of a walk ====================


def terminal(session, state="arrived", run_id=RUN_A, origin="voice", **extra):
    robot_face_link.receive(session, {"type": "nav_status", "format": 1, "state": state,
                                      "run_id": run_id, "origin": origin, "place": "big_room",
                                      **extra})


async def settle(seconds=0.6):
    await asyncio.sleep(seconds)


def test_arrival_is_announced_once(face):
    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        terminal(face)
        terminal(face)          # the page repeats a state; the guest hears it once
        # Long enough for a second announcement to clear the lock and its
        # 0.4 s pause: a shorter wait would pass with the dedupe removed.
        await settle(1.6)

    run(scenario())
    assert len(face.provider.said) == 1
    assert face.provider.said[0].startswith("ถึงห้องประชุมใหญ่แล้ว")


def test_a_stopped_run_says_why_and_where_in_thai(face):
    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        terminal(face, state="stopped", reason="door_blocked", step=4, do="door",
                 door="D8", label="D8 out", detail="door D8 blocked -- English detail")
        await settle()

    run(scenario())
    [text] = face.provider.said
    assert "ไม่สำเร็จ" in text and "ประตูปิดหรือมีคนยืนขวางประตู" in text
    assert "ประตู D8 (ขั้น 4)" in text
    assert "English detail" not in text and "D8 out" not in text


def test_a_tap_run_is_not_announced(face):
    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        terminal(face, origin="tap")
        await settle()

    run(scenario())
    assert face.provider.said == []


def test_a_run_issued_to_another_session_is_not_announced(monkeypatch, face):
    """Safety review #9: a second page that claims EmmaRobot/ cannot fake an
    arrival into a conversation. B reports A's run as arrived while B is the
    active conversation — the announce gate is `issued_run(B, run)`."""
    other = Face(host="10.0.0.6")
    robot_face_link.claim(other)

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        monkeypatch.setattr(session_module, "_active", other)
        terminal(other)
        await settle()

    run(scenario())
    assert other.provider.said == [] and face.provider.said == []


def test_an_arrival_is_not_read_to_another_conversation(monkeypatch, face):
    """`events.announce` speaks into whoever is `_active`. If a desk tab took
    over while the robot walked, the robot's arrival is not that person's news."""
    desk = Face(host="10.0.0.7")

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        monkeypatch.setattr(session_module, "_active", desk)
        terminal(face)
        await settle()

    run(scenario())
    assert desk.provider.said == [] and face.provider.said == []


@pytest.mark.parametrize("newer_run", [True, False])
def test_an_arrival_overtaken_by_a_newer_run_is_not_read(face, newer_run):
    """Finding #42: the run-id half of `_is_latest`. The arrival waits for the
    guest to hear the current sentence; if a newer walk's status arrives in
    that wait, "ถึง… แล้ว" about the old one is no longer true to say. The
    control (no newer frame) proves the wait itself still delivers."""
    from app import display

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        display.set_audio_lead(800)
        terminal(face)                                      # RUN_A arrived
        if newer_run:
            robot_face_link.receive(face, {"type": "nav_status", "format": 1,
                                           "state": "running", "run_id": RUN_B,
                                           "origin": "voice", "place": "big_room"})
        await settle(2.0)

    run(scenario())
    if newer_run:
        assert face.provider.said == []
    else:
        assert len(face.provider.said) == 1 and face.provider.said[0].startswith("ถึง")


def test_a_walk_that_goes_quiet_is_said_once_and_no_stop_is_sent(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_status_stale_s", 0.15)

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        robot_face_link.receive(face, {"type": "nav_status", "state": "running",
                                       "run_id": RUN_A, "origin": "voice", "place": "big_room"})
        await asyncio.sleep(1.2)

    run(scenario())
    assert len(face.provider.said) == 1
    assert "ขาดการติดต่อ" in face.provider.said[0] and "ห้ามบอกว่าถึงแล้ว" in face.provider.said[0]
    assert [c["action"] for c in face.ws.sent] == ["go"], "the watchdog must never send a stop"


def test_status_frames_keep_the_watchdog_quiet(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_status_stale_s", 0.3)

    async def scenario():
        say(face, "พาไปห้องประชุมใหญ่")
        await go(face, "ห้องประชุมใหญ่")
        # Four times the stale bound of steady frames: without the refresh
        # the watchdog would fire (and be spoken) long before the arrival.
        for _ in range(12):
            robot_face_link.receive(face, {"type": "nav_status", "state": "running",
                                           "run_id": RUN_A, "origin": "voice"})
            await asyncio.sleep(0.1)
        terminal(face)
        await settle(0.8)

    run(scenario())
    assert len(face.provider.said) == 1 and face.provider.said[0].startswith("ถึง")


# ==================== stop ====================


def test_stop_sends_a_nav_stop_and_a_body_stop_while_a_go_is_pending(face):
    """Safety review #1: the `go` ack never comes (a fake page that does not
    answer); `stop_moving` must still put both stops on the wire at once."""
    say(face, "พาไปห้องประชุมใหญ่")

    async def scenario():
        pending = asyncio.ensure_future(registry.dispatch("go_to_place", {"place": "ห้องประชุมใหญ่"}))
        for _ in range(200):
            if face.ws.sent:
                break
            await asyncio.sleep(0.005)
        assert face.ws.sent[0]["action"] == "go"
        stopping = asyncio.ensure_future(registry.dispatch("stop_moving", {}))
        await asyncio.sleep(0.1)
        sent = [(c["type"], c["action"]) for c in face.ws.sent]
        for i, c in enumerate(face.ws.sent):
            if c["action"] == "stop":
                await answer(face, i, accepted=True, reason=None)
        out = await stopping
        pending.cancel()
        return sent, out

    sent, out = run(scenario())
    assert ("nav_command", "stop") in sent and ("body_command", "stop") in sent
    assert out["ok"] is True and out["hardware"] == "requested"
    assert "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"]


def test_stop_never_goes_to_the_board_in_routes_mode(monkeypatch, face):
    """A DELETE of the board's action mid-leg reads to drive_route as a link
    drop, and it redoes the step (§4)."""
    called = []

    async def board_send(action, **args):
        called.append(action)
        return "ok"

    monkeypatch.setattr(robot_link, "send", board_send)

    async def scenario():
        task = asyncio.ensure_future(registry.dispatch("stop_moving", {}))
        await asyncio.sleep(0.05)
        for i in range(len(face.ws.sent)):
            await answer(face, i, accepted=True, reason=None)
        return await task

    run(scenario())
    assert called == []


@pytest.mark.parametrize("mode", ["off", "unlocked", "yes"])
def test_stop_works_whatever_the_voice_mode(monkeypatch, face, mode):
    """Finding #41: "stopping is never gated" had no test under the mode the
    robot ships with — every other test here runs with voice `on`."""
    monkeypatch.setattr(settings, "robot_nav_voice", mode)
    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_pin", "2468")   # set, never unlocked
    assert robot_nav.voice_may_start(face)[0] is False       # the gate is shut

    async def scenario():
        task = asyncio.ensure_future(registry.dispatch("stop_moving", {}))
        for _ in range(200):
            if len(face.ws.sent) >= 2:
                break
            await asyncio.sleep(0.005)
        for i in range(len(face.ws.sent)):
            await answer(face, i, accepted=True, reason=None)
        return await task

    out = run(scenario())
    sent = [(c["type"], c["action"]) for c in face.ws.sent]
    assert ("nav_command", "stop") in sent and ("body_command", "stop") in sent
    assert out["ok"] is True and out["hardware"] == "requested"


def test_a_failed_stop_tells_people_to_press_the_red_bar(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_ack_timeout_s", 0.05)
    monkeypatch.setattr(settings, "robot_body_ack_timeout_s", 0.05)
    out = run(registry.dispatch("stop_moving", {}))
    assert out["ok"] is False and "ปุ่มหยุดฉุกเฉิน" in out["instruction"]
    assert "ห้ามบอกว่าหยุดแล้ว" in out["instruction"]


# ---- review 2, finding #4: "หยุด" from a session that is not the face ----


def _board(monkeypatch):
    called = []

    async def board_send(action, **args):
        called.append(action)
        return "ok"

    monkeypatch.setattr(robot_link, "send", board_send)
    return called


@pytest.mark.parametrize("routes", [True, False])
def test_a_stop_from_a_desk_tab_during_a_walk_goes_through_the_face(monkeypatch, routes):
    """Only the face's own session took the face path; "หยุด" spoken at a
    desk tab or the robot's kiosk Chrome sent the board DELETE — the #0 bug,
    a ~1 s pause to drive_route — and told the model the stop was sent."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", routes)
    walking = Face()
    robot_face_link.claim(walking)
    robot_face_link.receive(walking, {"type": "nav_status", "state": "running",
                                      "run_id": RUN_A, "origin": "tap", "place": "big_room"})
    board = _board(monkeypatch)
    assert registry.robot_face_session.get() is None, "a desk session"

    async def scenario():
        task = asyncio.ensure_future(registry.dispatch("stop_moving", {}))
        for _ in range(200):
            if len(walking.ws.sent) >= 2:
                break
            await asyncio.sleep(0.005)
        for i in range(len(walking.ws.sent)):
            await answer(walking, i, accepted=True, reason=None)
        return await task

    out = run(scenario())
    sent = [(c["type"], c["action"]) for c in walking.ws.sent]
    assert ("nav_command", "stop") in sent and ("body_command", "stop") in sent
    assert board == [], "a DELETE mid-run makes drive_route redo the step"
    assert out["ok"] is True and out["hardware"] == "requested"


def test_a_stop_from_a_desk_tab_with_the_face_gone_is_not_called_sent(monkeypatch):
    walking = Face()
    robot_face_link.claim(walking)
    robot_face_link.receive(walking, {"type": "nav_status", "state": "running",
                                      "run_id": RUN_A, "origin": "tap", "place": "big_room"})
    robot_face_link.session_closed(walking)
    board = _board(monkeypatch)
    out = run(registry.dispatch("stop_moving", {}))
    assert board == ["cancel_navigation"]
    assert out["ok"] is False and out["did"] == "stop_unconfirmed"
    assert out["error"] == "no_robot_face"
    assert "ปุ่มหยุดฉุกเฉิน" in out["instruction"] and "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"]


def test_a_stop_with_a_walk_nobody_can_see_is_unconfirmed(monkeypatch):
    """Review 2, finding #0 (voice side): routes on and no face page
    reporting, so a walk is "unknown". The DELETE still goes; the
    model says sent-not-stopped and points at the red bar."""
    assert settings.robot_nav_routes_enabled is True
    assert robot_face_link.nav_activity() == "unknown"
    board = _board(monkeypatch)
    out = run(registry.dispatch("stop_moving", {}))
    assert board == ["cancel_navigation"]
    assert out["ok"] is False and out["did"] == "stop_unconfirmed"
    assert "ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ'" in out["instruction"]
    assert "ห้ามพูดว่า 'หยุดแล้ว'" in out["instruction"] and "ปุ่มหยุดฉุกเฉิน" in out["instruction"]


def test_the_sales_room_stop_is_still_the_board(monkeypatch):
    """Routes off, no face ever: the old path, no stop_unconfirmed."""
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    board = _board(monkeypatch)
    out = run(registry.dispatch("stop_moving", {}))
    assert board == ["cancel_navigation"]
    assert out["ok"] is True and out["hardware"] == "ok" and "did" not in out


def test_stop_moving_is_declared_as_before_and_preempts():
    entry = registry.get("stop_moving")
    assert entry.preempt is not None and entry.preempt({}) is True
    assert entry.parameters == {"type": "object", "properties": {}}


# ==================== status ====================


def test_status_reports_the_leg_system_not_the_planner(face):
    robot_face_link.receive(face, {"type": "nav_caps", "enabled": True, "installed": True,
                                   "here": "dock", "places": [
                                       {"id": "big_room", "name_th": "x", "available": True,
                                        "reason": None, "verified": True},
                                       {"id": "dock", "available": False,
                                        "reason": "already_there"}]})
    out = run(registry.dispatch("get_robot_status", {}))
    nav = out["nav"]
    assert nav["here"] == "dock" and nav["voice_can_start"] is True
    assert {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "available": True,
            "reason": None} in nav["places"]
    assert {"id": "dock", "name_th": "แท่นชาร์จ", "available": False,
            "reason": "already_there"} in nav["places"]
    assert out["places"] == ["แท่นชาร์จ", "ห้องประชุมใหญ่"]
    assert "body" in out
    assert "available" in out["instruction"]


def test_status_with_voice_off_says_staff_start_walks(monkeypatch, face):
    monkeypatch.setattr(settings, "robot_nav_voice", "off")
    out = run(registry.dispatch("get_robot_status", {}))
    assert out["nav"]["voice_can_start"] is False
    assert "หน้าจอหุ่น" in out["instruction"]


# ==================== the PIN endpoints ====================

TOKEN = "t-0123456789"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_pin", "2468")
    return TestClient(app)


def test_config_needs_the_token_and_never_echoes_the_pin(client):
    refused = client.post("/robot-nav/config", json={})
    assert refused.status_code == 403
    out = client.post("/robot-nav/config", json={"token": TOKEN})
    assert out.status_code == 200
    assert out.json() == {"ok": True, "mode": "pin", "pin_set": True}
    assert "2468" not in out.text


def test_config_is_off_while_routes_are_off(monkeypatch, client):
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    assert client.post("/robot-nav/config", json={"token": TOKEN}).json()["mode"] == "off"


def test_token_in_the_query_string_is_not_accepted(client):
    out = client.post("/robot-nav/unlock?token=" + TOKEN, json={"pin": "2468"})
    assert out.status_code == 403


def test_unlock_wrong_then_right(client):
    wrong = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": "0000"})
    assert wrong.status_code == 403 and wrong.json()["reason"] == "รหัสไม่ถูกต้อง"
    right = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": "2468"})
    assert right.json() == {"ok": True, "ttl_s": 120}


def test_five_wrong_pins_lock_out_even_the_right_one(client):
    for _ in range(robot_nav.PIN_TRIES - 1):
        assert client.post("/robot-nav/unlock",
                           json={"token": TOKEN, "pin": "0000"}).status_code == 403
    locked = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": "0000"})
    assert locked.status_code == 429 and "5 นาที" in locked.json()["reason"]
    still = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": "2468"})
    assert still.status_code == 429, "a lockout that still compares answers the guess"


def test_the_lockout_ends(monkeypatch):
    monkeypatch.setattr(settings, "robot_nav_panel", "pin")
    monkeypatch.setattr(settings, "robot_nav_pin", "2468")
    clock = [50.0]
    monkeypatch.setattr(robot_nav, "_clock", lambda: clock[0])
    for _ in range(robot_nav.PIN_TRIES):
        with pytest.raises(robot_nav.PinError):
            robot_nav.unlock("1111", "h")
    with pytest.raises(robot_nav.PinError) as exc:
        robot_nav.unlock("2468", "h")
    assert exc.value.status == 429
    clock[0] += robot_nav.LOCKOUT_S + 1
    assert robot_nav.unlock("2468", "h")["ok"] is True


def test_an_empty_pin_in_pin_mode_is_not_set_up(monkeypatch, client):
    monkeypatch.setattr(settings, "robot_nav_pin", "")
    out = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": ""})
    assert out.status_code == 503 and "ROBOT_NAV_PIN" in out.json()["reason"]
    assert client.post("/robot-nav/config", json={"token": TOKEN}).json()["pin_set"] is False


def test_unlock_is_refused_while_the_panel_is_off(monkeypatch, client):
    monkeypatch.setattr(settings, "robot_nav_panel", "off")
    out = client.post("/robot-nav/unlock", json={"token": TOKEN, "pin": "2468"})
    assert out.status_code == 403


def test_an_oversize_body_is_refused_before_the_gate(client):
    out = client.post("/robot-nav/unlock", content=b'{"pin": "' + b"1" * 5000 + b'"}',
                      headers={"content-type": "application/json"})
    assert out.status_code == 413


def test_the_boot_log_says_how_the_face_is_identified(monkeypatch, caplog):
    import logging

    from app.main import log_body_nav_boot

    log = logging.getLogger("test-boot")
    monkeypatch.setattr(settings, "nav_boot_warnings", [])
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", False)
    assert log_body_nav_boot(log) == [], "quiet when everything is off"
    monkeypatch.setattr(settings, "robot_nav_routes_enabled", True)
    monkeypatch.setattr(settings, "nav_boot_warnings", ["SAFETY: forced off"])
    lines = log_body_nav_boot(log)
    assert lines[0] == "SAFETY: forced off" and "user agent" in lines[1]


def test_a_stop_instruction_quotes_what_the_model_really_said(monkeypatch):
    # live eval 9 ต.ค. 02:38 (gemini-3.8-live, joke): after "หยุด" mid-walk Emma said
    # "หยุดให้เรียบร้อยแล้วค่ะ", and after a stop mid-gesture "เรียบร้อยค่ะ" -- the bare
    # "ห้ามพูดว่า 'หยุดแล้ว'" caught neither (CLAUDE.md: quote the real wrong sentence)
    from app import robot_body, robot_nav
    import inspect
    for mod in (robot_nav, robot_body):
        src = inspect.getsource(mod)
        assert "'หยุดให้เรียบร้อยแล้วค่ะ'" in src and "'เรียบร้อยค่ะ'" in src, mod.__name__
        assert "ให้ตอบว่า 'ส่งคำสั่งหยุดแล้วค่ะ'" in src, mod.__name__


def test_the_logged_run_states_of_a_session_stay_bounded(monkeypatch):
    """Review 10 Oct: `nav.logged` was a set that grew with every distinct
    (run id, terminal state) — the status link is a session that lives all
    day and a page may send any valid run id. Now the newest LOGGED_KEPT,
    and a run's terminal state is still written once however often the
    link's beat repeats it."""
    rows = []
    monkeypatch.setattr(robot_nav, "_record", lambda kind, **f: rows.append((kind, f)))
    sess = object()
    try:
        for n in range(robot_nav.LOGGED_KEPT + 40):
            run_id = "%032x" % (n + 1)
            for _beat in range(2):
                asyncio.run(robot_nav.on_nav_status(
                    sess, {"type": "nav_status", "state": "arrived", "run_id": run_id,
                           "origin": "tap"}))
        nav = robot_nav._nav(sess)
        assert len(nav.logged) == robot_nav.LOGGED_KEPT
        assert ("%032x" % (robot_nav.LOGGED_KEPT + 40), "arrived") in nav.logged
        assert len(rows) == robot_nav.LOGGED_KEPT + 40, "each run logged exactly once"
    finally:
        robot_nav._STATE.pop(id(sess), None)
