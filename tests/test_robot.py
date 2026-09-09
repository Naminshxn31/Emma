"""Moving the robot.

The tools here are the first ones whose failure has a turning circle. A wrong
slide is corrected with a sentence; a robot walking a customer into the wrong
room, or across one, is not.

Nothing in this file needs a robot. `ROBOT_ENABLED=false`, or simply no robot
app connected, is mock mode — the same contract the IR tools have had since
`_worst()` was written, because "mock" reported as "ok" once had the assistant
announcing it had switched off an air conditioner that never heard a thing.
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.tools import registry, robot_link


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    from app.tools import load_tools

    load_tools()
    robot_link.reset_state()
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(settings, "turn_log", False)
    yield
    robot_link.reset_state()


class FakeProvider:
    def __init__(self):
        self.said = []

    async def send_text(self, text):
        self.said.append(text)


class FakeSession:
    """Stands in for a connected robot app."""

    def __init__(self):
        self.sent = []
        self.provider = FakeProvider()
        self.ws = self

    async def send_text(self, text):
        import json

        await self._send_json(json.loads(text))

    async def _send_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def robot(monkeypatch):
    """A robot app, connected, with a map."""
    from app import session as session_module

    live = FakeSession()
    monkeypatch.setattr(session_module, "_active", live)
    robot_link.app_connected(["ห้องตัวอย่าง", "สระว่ายน้ำ", "ฟิตเนส", "โต๊ะเซลส์"])
    return live


# ==================== the one that would ruin a demo ====================


def test_walking_somewhere_does_not_hold_the_conversation(robot):
    """The bug this file exists to prevent.

    Function calling is synchronous — the model produces nothing until the
    tool returns. Walking to a viewing room takes half a minute. A tool that
    waited for arrival would be half a minute of a robot standing mute beside
    the customer it just offered to guide.

    This has already happened here once, in a smaller form:
    `start_presentation` waited on a browser window and measured four seconds
    of silence in front of a guest who had just asked to see something.
    """
    async def body():
        started = asyncio.get_event_loop().time()
        out = await registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"})
        elapsed = asyncio.get_event_loop().time() - started

        assert elapsed < 0.5, "held the conversation for %.1fs" % elapsed
        assert out["ok"] is True
        assert out["moving"] is True
        assert "ห้ามบอกว่าถึงแล้ว" in out["instruction"]

    run(body())


def test_arrival_comes_back_as_its_own_turn(robot):
    """Because nothing would ever read it otherwise.

    After the tool result the model has said its piece and the turn is over.
    It is not polling. If arrival only updated state, the robot would reach
    the viewing room and stand there silently — the same dead end as a tour
    waiting on an answer that never comes, which is why
    `_resume_after_silence` has its own clock.
    """
    run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))
    assert robot_link.STATE["moving"] is True

    run(robot_link.arrived("ห้องตัวอย่าง", ok=True))

    assert robot_link.STATE["moving"] is False
    assert len(robot.provider.said) == 1
    assert "ถึง" in robot.provider.said[0]


def test_a_failed_journey_is_reported_with_a_reason(robot):
    """"ไปไม่ได้" tells a guest nothing and invites the model to invent a
    cause. Naming the likely one — something in the way — gives it something
    true to say."""
    run(registry.dispatch("go_to_place", {"place": "สระว่ายน้ำ"}))
    run(robot_link.arrived("สระว่ายน้ำ", ok=False))

    said = robot.provider.said[0]
    assert "ไม่สำเร็จ" in said
    assert "สิ่งกีดขวาง" in said
    assert "เจ้าหน้าที่" in said, "a dead end needs a way out for the guest"


# ==================== not guessing where to go ====================


def test_an_unknown_place_is_refused_rather_than_guessed(robot):
    """No fuzzy fallback, on purpose.

    `documents.find` reasons the same way: the failure mode of reaching for
    the nearest match is the wrong thing happening in the physical world, and
    by the time anyone notices, the robot is already there.
    """
    out = run(registry.dispatch("go_to_place", {"place": "ห้องน้ำชั้นสาม"}))
    assert out["ok"] is False
    assert out["error"] == "unknown place"
    assert robot_link.STATE["moving"] is False
    assert robot.sent == [], "sent a movement command anyway"


def test_the_refusal_lists_where_it_can_go(robot):
    """So the model can ask a useful question instead of apologising."""
    out = run(registry.dispatch("go_to_place", {"place": "ห้องน้ำชั้นสาม"}))
    assert "ห้องตัวอย่าง" in out["instruction"]
    assert "ห้ามเดา" in out["instruction"]


def test_an_empty_map_says_so_instead_of_listing_nothing(monkeypatch):
    """A robot whose map hasn't loaded must not answer "you can go to: "."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", FakeSession())
    robot_link.app_connected([])

    out = run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))
    assert out["ok"] is False
    assert "เรียกเจ้าหน้าที่" in out["instruction"]


def test_a_shared_word_is_not_a_match(robot):
    """The bug this rule was written for, kept as its own test.

    ห้องน้ำ and ห้องตัวอย่าง share ห้อง. Scoring on overlap and taking the best
    made that one shared word enough, because one is more than zero. The first
    version was worse still: it used `robust_tokens`, which adds character
    n-grams to widen *search* recall, so the pair also matched on ห้อง, ห้อ and
    ้อง and scored three. Same shape as `ราคา` matching `อาคาร` through the run
    าคา — a function built for recall, reused where a wrong answer walks a
    customer across the room.
    """
    assert robot_link.find_place("ห้องน้ำอยู่ไหน") is None
    assert robot_link.find_place("ขอดูห้องตัวอย่างหน่อย") == "ห้องตัวอย่าง"


def test_half_a_name_is_not_enough(robot):
    """"พาไปสระ" could mean the pool, and could mean something the robot has
    never heard of. Asking costs a sentence; guessing costs a walk."""
    assert robot_link.find_place("พาไปสระ") is None
    assert robot_link.find_place("พาไปสระว่ายน้ำ") == "สระว่ายน้ำ"


def test_the_more_specific_destination_wins(monkeypatch):
    """One POI name can contain another. The longer one is what was asked
    for — otherwise "ห้องตัวอย่าง 2 ห้องนอน" always loses to "ห้องตัวอย่าง"."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", FakeSession())
    robot_link.app_connected(["ห้องตัวอย่าง", "ห้องตัวอย่าง 2 ห้องนอน"])
    assert robot_link.find_place("ขอดูห้องตัวอย่าง 2 ห้องนอน") == "ห้องตัวอย่าง 2 ห้องนอน"
    assert robot_link.find_place("ขอดูห้องตัวอย่าง") == "ห้องตัวอย่าง"


# ==================== mock mode ====================


def test_no_robot_connected_is_mock_not_failure(monkeypatch):
    """Developing the conversation must not require a robot in the room."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    robot_link.app_connected(["ห้องตัวอย่าง"])

    out = run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))
    assert out["hardware"] == "mock"
    assert out["moving"] is None, "physical motion must be unknown without a robot report"


def test_mock_mode_tells_the_model_not_to_claim_it_is_walking(monkeypatch):
    """The whole reason `hardware` is on every result.

    An earlier version of the IR tools reported "mock" as "ok" and the
    assistant announced it had switched off an air conditioner that had never
    received anything. A robot is a louder version of that mistake: the guest
    is standing next to it, watching it not move.
    """
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    robot_link.app_connected(["ห้องตัวอย่าง"])

    out = run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))
    assert "ห้ามบอกว่ากำลังเดินไป" in out["instruction"]


def test_a_browser_on_the_socket_is_not_a_robot(monkeypatch):
    """`available()` must not mean "some websocket is open".

    The voice client in a browser is on that same socket and has no arms. Only
    a `robot_ready` message — which a browser never sends — counts.
    """
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", FakeSession())
    assert robot_link.available() is False

    out = run(registry.dispatch("go_to_place", {"place": "ที่ไหนก็ได้"}))
    assert out["ok"] is False


def test_disabling_the_robot_disables_the_hardware_not_the_tools(robot, monkeypatch):
    monkeypatch.setattr(settings, "robot_enabled", False)
    out = run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))
    assert out["hardware"] == "mock"
    assert robot.sent == []


# ==================== stopping ====================


def test_stop_is_sent_even_when_we_believe_it_is_standing_still(robot):
    """Our `moving` flag is what we last sent plus what the app reported. If
    those have drifted, the guest is looking at the disagreement — and a robot
    replying "I'm not moving" while rolling toward someone is the worst
    possible moment to trust a cached flag."""
    assert robot_link.STATE["moving"] is False

    out = run(registry.dispatch("stop_moving", {}))
    assert out["ok"] is True
    assert any(m["action"] == "cancel_navigation" for m in robot.sent), \
        "trusted local state and sent nothing"


def test_stopping_clears_the_destination(robot):
    run(registry.dispatch("go_to_place", {"place": "ฟิตเนส"}))
    run(registry.dispatch("stop_moving", {}))
    assert robot_link.STATE["moving"] is False
    assert robot_link.STATE["destination"] is None


def test_losing_the_link_stops_us_claiming_to_know_anything(robot):
    """A robot that is walking when the socket drops keeps walking. The honest
    state on this side is "unknown", not the last thing we sent."""
    run(registry.dispatch("go_to_place", {"place": "ฟิตเนส"}))
    assert robot_link.STATE["moving"] is True

    robot_link.app_gone()

    assert robot_link.available() is False
    assert robot_link.STATE["moving"] is False
    assert robot_link.KNOWN_PLACES == []


# ==================== what goes on the wire ====================


def test_commands_go_down_the_socket_that_is_already_open(robot):
    """Not a new port on the robot.

    The app is already connected for voice. A second channel would be a second
    thing to authenticate, reconnect and debug — and one that could disagree
    with the first about which robot it is talking to.
    """
    run(registry.dispatch("go_to_place", {"place": "ฟิตเนส"}))
    assert robot.sent[0]["type"] == "robot"
    assert robot.sent[0]["action"] == "move_to_point"
    assert robot.sent[0]["args"]["place"] == "ฟิตเนส"


def test_going_home_is_its_own_command(robot):
    out = run(registry.dispatch("return_to_base", {}))
    assert out["ok"] is True
    assert robot.sent[0]["action"] == "go_home"


def test_status_reports_mock_when_nothing_is_attached(monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    out = run(registry.dispatch("get_robot_status", {}))
    assert out["hardware"] == "mock"
    assert "ยังพาไปไม่ได้" in out["instruction"]


def test_status_lists_the_places_for_a_guest_who_asks(robot):
    out = run(registry.dispatch("get_robot_status", {}))
    assert "ห้องตัวอย่าง" in out["places"]
    assert out["hardware"] == "ok"


# ==================== the session's side ====================


def test_the_app_reporting_in_is_what_enables_the_robot(monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", FakeSession())
    assert robot_link.available() is False
    robot_link.app_connected(["ห้องตัวอย่าง"])
    assert robot_link.available() is True
    assert robot_link.KNOWN_PLACES == ["ห้องตัวอย่าง"]


def test_reconnecting_replaces_the_map_rather_than_adding_to_it():
    """The robot may have been re-mapped between sessions. Appending would
    leave a POI on our side that no longer exists on its."""
    robot_link.app_connected(["เก่า1", "เก่า2"])
    robot_link.app_connected(["ใหม่"])
    assert robot_link.KNOWN_PLACES == ["ใหม่"]


def test_pretend_places_make_the_conversation_testable_before_the_robot(monkeypatch):
    """Without this there is nothing to rehearse against.

    `KNOWN_PLACES` is empty until a robot reports its own map, so every request
    hits the "I don't know that place" branch and the interesting half — how it
    offers to guide, whether it waits before claiming to have arrived — cannot
    be reached at all until the hardware is in the room.
    """
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    monkeypatch.setattr(settings, "robot_mock_places", ["ห้องตัวอย่าง", "ฟิตเนส"])

    out = run(registry.dispatch("go_to_place", {"place": "พาไปห้องตัวอย่างหน่อย"}))
    assert out["ok"] is True, "the guiding path is still unreachable"
    assert out["place"] == "ห้องตัวอย่าง"
    assert out["hardware"] == "mock"
    assert out["moving"] is None, "a pretend place cannot confirm physical motion"


def test_a_real_map_overrides_the_pretend_one(robot, monkeypatch):
    """The moment a robot reports in, its own map is the only one that counts —
    otherwise a leftover ROBOT_MOCK_PLACES in .env sends it somewhere that
    doesn't exist."""
    monkeypatch.setattr(settings, "robot_mock_places", ["ที่ไม่มีจริง"])
    assert robot_link.find_place("พาไปที่ไม่มีจริง") is None
    assert robot_link.find_place("พาไปห้องตัวอย่าง") == "ห้องตัวอย่าง"


def test_arrival_with_nobody_listening_does_not_raise():
    """The guest left, the socket closed, and the robot arrives anyway."""
    from app import session as session_module

    session_module._active = None
    run(robot_link.arrived("ห้องตัวอย่าง", ok=True))   # must not raise


def test_arriving_mid_sentence_does_not_cut_the_sentence_off(robot):
    """`arrived()` went straight into `send_text`, and a walk finishes
    whenever it finishes — routinely while the robot is still describing the
    walk. Text arriving during playback is a barge-in, so the arrival
    cancelled the model's own generation and dropped the rest of the audio:
    the robot interrupted itself to announce that it had arrived.

    Exactly the bug the tour nudge already had and had already fixed. It was
    fixed there and nowhere else, which is what `app/events.py` is for.
    """
    from app import display

    run(registry.dispatch("go_to_place", {"place": "ห้องตัวอย่าง"}))

    async def body():
        display.set_audio_lead(20_000)      # still talking about the walk
        pending = asyncio.create_task(robot_link.arrived("ห้องตัวอย่าง", ok=True))
        await asyncio.sleep(0.05)
        assert robot.provider.said == [], (
            "reported arrival over the sentence that was still being spoken"
        )

        display.set_audio_lead(0)
        await pending
        assert len(robot.provider.said) == 1
        assert "ถึง" in robot.provider.said[0], "the arrival was lost, not delayed"

    asyncio.run(body())
