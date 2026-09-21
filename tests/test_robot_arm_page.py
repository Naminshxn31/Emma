"""Manual control of the upper body, at `/arm`.

Every test here is about something the servo board cannot tell us. It takes
text on a wire, this module never reads back, and its stop frame has been
recovered from the vendor's code but never watched working, so the properties
worth holding this page to are the ones that decide what happens when a press
means something different from what the person pressing expected.

Nothing reaches a robot: `robot_arm._run` — the one function that shells out
to adb — is replaced by a recorder, the same shape as the fake navigation
board in `tests/test_robot_chassis.py`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import robot_arm
from app.config import settings
from app.main import app

PAGE = Path(__file__).resolve().parent.parent / "client" / "robot-arm.html"
TOKEN = "let-me-in"


class FakeShell:
    """Stands in for the robot's shell. Records, answers, never executes."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.port_exists = True
        self.auto_ports = ["/dev/ttyUSB11"]
        self.fail = False

    async def run(self, args, timeout):
        self.calls.append(list(args))
        joined = " ".join(args)
        if "idVendor" in joined and "idProduct" in joined:
            return 0, "\n".join(self.auto_ports)
        if joined.startswith("adb") is False:
            pass
        if " ls " in joined:
            return (0, "/dev/ttyUSB10") if self.port_exists \
                else (1, "ls: /dev/ttyUSB10: No such file or directory")
        if self.fail:
            return 1, "write error"
        return 0, ""

    def lines(self) -> list[str]:
        """Just the protocol lines that reached the wire."""
        out = []
        for call in self.calls:
            text = call[-1]
            if text.startswith("printf "):
                # Strip the literal CRLF the shell will interpret, so the
                # assertions below read as the protocol line itself.
                out.append(text.split("'")[1].replace(chr(92) + "r"
                                                      + chr(92) + "n", ""))
        return out


@pytest.fixture
def shell(monkeypatch):
    fake = FakeShell()
    monkeypatch.setattr(settings, "robot_arm_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_port", "/dev/ttyUSB10")
    monkeypatch.setattr(settings, "robot_arm_span", 200)
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", True)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", True)
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(robot_arm, "_run", fake.run)
    robot_arm.reset_state()
    yield fake
    robot_arm.reset_state()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _visible(source: str) -> str:
    """The page with its comments removed.

    A test that scans for forbidden words will otherwise match the comment
    explaining why the word is forbidden — which happened once already on
    `client/hardware.html`.
    """
    import re

    source = re.sub(r"<!--.*?-->", " ", source, flags=re.S)
    return re.sub(r"//.*", " ", source)


def send(client, action, **extra):
    return client.post("/arm/command",
                       json={"token": TOKEN, "action": action, **extra}).json()


# ==================== the gate ====================


def test_the_page_is_served(client):
    assert client.get("/arm").status_code == 200


def test_moving_a_joint_needs_the_token(shell, client):
    assert client.post("/arm/command", json={"action": "centre", "channel": 1})\
        .json() == {"ok": False, "error": "unauthorized"}
    assert shell.lines() == [], "nothing may reach the board unauthenticated"


def test_the_feature_is_off_until_two_switches_are_set(client, monkeypatch):
    """Default off, and off means nothing is written — not written to a
    guessed port. On 2026-09-10 the documented device node was not the one
    the kernel actually created."""
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "robot_arm_enabled", False)
    monkeypatch.setattr(settings, "robot_arm_port", "")

    out = send(client, "centre", channel=1)
    assert out["ok"] is False
    assert out["error"] == "arm_not_configured"
    assert "ROBOT_ARM_PORT" in out["hint"]


# ==================== what cannot be pretended about ====================


def test_stop_sends_the_board_its_stop_frame(shell, client):
    """`#STOP` came out of the vendor app's own stop helper. A stop that
    exists and is unproven beats no stop at all, so it goes on the wire."""
    out = send(client, "stop")

    assert out["ok"] is True
    assert out["stop_write_ok"] is True
    assert shell.lines() == ["#STOP", "#STOP"],         "twice, as the vendor's own stop helper does — a repeat is a hint that one frame is sometimes missed"


def test_stop_never_claims_the_arm_stopped(shell, client):
    """The field means the shell ran the write and exited zero. It does not
    mean the bytes reached the board, that the board understood them, or that
    a joint stopped — and the reply has to be narrow enough to say so,
    because somebody reads it while deciding whether to reach for the switch.
    It was called `stop_sent` until that name was noticed asserting the
    first of those three."""
    out = send(client, "stop")

    assert "stop_write_ok" in out and "stop_sent" not in out
    assert "ไม่ยืนยันว่าเฟรมถึงบอร์ดหรือแขนหยุด" in out["note"]
    assert "ไฟเลี้ยงเซอร์โว" in out["note"]


def test_the_latch_is_set_before_the_stop_frame_is_written(shell, client):
    """The half that cannot fail must not wait behind the half that can. If
    the write hangs or errors, everything afterwards is still refused."""
    send(client, "centre", channel=1)          # a baseline to step from
    shell.fail = True

    out = send(client, "stop")
    assert out["ok"] is True
    assert out["stop_write_ok"] is False, "a failed write must be reported as failed"

    shell.fail = False                         # the wire is fine again
    assert send(client, "up", channel=1)["error"] == "disarmed",         "the latch has to survive a stop whose write failed"


def test_stop_works_when_no_port_is_configured(client, monkeypatch):
    """The latch is the part that always works. A machine with nothing set up
    still has to be able to refuse."""
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "robot_arm_enabled", False)
    monkeypatch.setattr(settings, "robot_arm_port", "")
    robot_arm.reset_state()

    out = send(client, "stop")
    assert out["ok"] is True
    assert out["stop_write_ok"] is False
    assert robot_arm.armed() is False


def test_stop_latches_and_refuses_everything_after(shell, client):
    send(client, "centre", channel=1)
    send(client, "stop")
    before = len(shell.lines())

    assert send(client, "up", channel=1)["error"] == "disarmed"
    assert send(client, "group", group=3)["error"] == "disarmed"
    assert len(shell.lines()) == before, "a latched stop is not a suggestion"

    assert send(client, "arm")["ok"] is True
    assert send(client, "up", channel=1)["ok"] is True


def test_a_missing_port_is_reported_not_written_into(shell, client):
    """`mock` must never be reported as `ok`. A page that says "sent" into a
    device node that is not there is that bug wearing a serial cable."""
    shell.port_exists = False

    out = send(client, "centre", channel=1)
    assert out["ok"] is False
    assert out["error"] == "port_missing"
    assert shell.lines() == []


def test_auto_port_follows_the_ch340_identity_not_the_tty_number(shell, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_port", "auto")

    out = client.get("/arm/state", params={"token": TOKEN}).json()["state"]

    assert out["port"] == "/dev/ttyUSB11"
    assert out["port_config"] == "auto"
    assert out["port_present"] is True
    discovery = " ".join(shell.calls[0])
    assert "1a86:7523" in discovery
    assert "/sys/class/tty/ttyUSB*" in discovery


def test_auto_port_refuses_ambiguous_ch340_matches(shell, client, monkeypatch):
    monkeypatch.setattr(settings, "robot_arm_port", "auto")
    shell.auto_ports = ["/dev/ttyUSB10", "/dev/ttyUSB11"]

    out = send(client, "prepare")

    assert out["ok"] is False
    assert out["error"] == "port_missing"
    assert "CH340 (1a86:7523)" in out["hint"]
    assert shell.lines() == []


def test_the_motion_interlock_refuses_every_mover_but_not_the_stop(shell, client, monkeypatch):
    """Switched off on 2026-09-11 after the owner's probe: the board answered
    `#STOP+OK` three times while channel 5 kept moving to its target. Until a
    stop that has been seen to halt a joint exists, nothing here may start
    one — and the lock must never be the reason the stop frame is not sent."""
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", False)

    for action, extra in (("centre", {"channel": 1}), ("up", {"channel": 1}),
                          ("group", {"group": 6})):
        out = send(client, action, **extra)
        assert out["ok"] is False, action
        assert out["error"] == "motion_locked", action
        assert "ROBOT_ARM_MOTION_ENABLED" in out["hint"]
    assert shell.lines() == [], "a locked arm receives nothing at all"

    out = send(client, "stop")
    assert out["ok"] is True and out["stop_write_ok"] is True
    assert shell.lines() == ["#STOP", "#STOP"], "the lock does not disable the brake"
    assert send(client, "prepare")["ok"] is True


def test_the_interlock_defaults_to_locked():
    """Asserted on the source default, not on Settings() — which bakes this
    developer's .env at import, the documented trap."""
    import inspect
    import re

    from app import config

    assert re.search(r'_get_bool\("ROBOT_ARM_MOTION_ENABLED",\s*False\)', inspect.getsource(config))


# ==================== the opening move ====================


def test_stepping_is_refused_until_the_channel_has_been_centred(shell, client):
    """A PWM servo cannot be asked where it is, so there is no position to
    step from until we have commanded one. Assuming centre would be inventing
    a number about a physical joint."""
    out = send(client, "up", channel=1)

    assert out["ok"] is False
    assert "1500" in out["error"]
    assert shell.lines() == []


def test_the_opening_move_is_the_slowest_the_protocol_allows(shell, client):
    """It is the one command that may swing a joint by an unknown amount, so
    "slow" is not good enough — it has to be the slowest the protocol defines.

    9999 is the top of the documented 100-9999 range for `T`. This was 3000
    until somebody read the range and noticed that 3000 is merely slow.
    """
    assert send(client, "centre", channel=1)["ok"] is True

    assert shell.lines() == ["#1P1500T9999"]
    assert robot_arm.SLOW_MS == 9999
    assert robot_arm.SLOW_MS > robot_arm.STEP_MS


def test_a_step_is_one_fixed_increment_from_the_last_commanded_position(shell, client):
    send(client, "centre", channel=7)
    send(client, "up", channel=7)
    send(client, "up", channel=7)

    assert shell.lines()[-2:] == [
        "#7P%dT%d" % (1500 + robot_arm.STEP, robot_arm.STEP_MS),
        "#7P%dT%d" % (1500 + 2 * robot_arm.STEP, robot_arm.STEP_MS),
    ]


def test_the_page_cannot_choose_how_far(shell, client):
    """Same rule as the chassis page: the client names a direction, never a
    number. A control surface that takes a pulse width from a web page anyone
    on the LAN can open is one typo away from a joint against its stop."""
    send(client, "centre", channel=1)
    send(client, "up", channel=1, pulse=2500, step=900, target=2400)

    assert shell.lines()[-1] == "#1P%dT%d" % (1500 + robot_arm.STEP,
                                              robot_arm.STEP_MS)


def test_travel_is_bounded_and_the_edge_is_reported(shell, client):
    """`ROBOT_ARM_SPAN` is small on purpose and widened by somebody who has
    watched the joint move. Hitting the edge has to say so rather than
    silently sending the same line for ever."""
    send(client, "centre", channel=11)
    for _ in range(200):
        out = send(client, "up", channel=11)
        if not out["ok"]:
            break

    assert out["ok"] is False
    assert "ROBOT_ARM_SPAN" in out["error"]
    last = int(shell.lines()[-1].split("P")[1].split("T")[0])
    assert last == robot_arm.PULSE_CENTRE + robot_arm.span()
    assert robot_arm.PULSE_MIN <= last <= robot_arm.PULSE_MAX


def test_only_the_channels_with_evidence_behind_them_are_addressable(shell, client):
    """The board has twenty channels; the robot's own app exposes four. On a
    robot whose wiring map nobody has, the other sixteen are joints nobody can
    predict."""
    assert set(robot_arm.CHANNELS) == {1, 11, 7, 8}

    out = send(client, "centre", channel=14)
    assert out["ok"] is False
    assert shell.lines() == []


def test_a_port_that_went_away_takes_the_commanded_positions_with_it(shell, client):
    """A port that is gone is a port somebody else may have. The vendor app
    was restarted between two sessions, took the board back and ran its home
    pose; every joint ended up somewhere other than the last number here.
    Stepping from that number would be a fast move from an unknown start."""
    send(client, "centre", channel=1)
    send(client, "up", channel=1)
    assert robot_arm._commanded == {1: 1540}

    shell.port_exists = False
    client.get("/arm/state", params={"token": TOKEN})
    assert robot_arm._commanded == {}, "absence of the port must clear the memory"

    shell.port_exists = True
    out = send(client, "up", channel=1)
    assert out["ok"] is False, "no stepping until the channel is centred again"


# ==================== action groups ====================


def test_a_group_always_runs_exactly_once(shell, client):
    """The cycle count is the `C` in the protocol, and zero loops for ever.
    A test instrument whose worst typo is an arm repeating until somebody
    finds the power switch is not a test instrument."""
    send(client, "group", group=7, cycles=0, loops=99, C=0)

    assert shell.lines() == ["#7GC1"]


def test_groups_need_their_own_switch(shell, client, monkeypatch):
    """None of this module's limits reach a stored group: it may drive any of
    the board's twenty channels anywhere in their range. Turning on the
    bounded stepping controls must not turn on an unbounded one."""
    monkeypatch.setattr(settings, "robot_arm_groups_enabled", False)

    out = send(client, "group", group=7)
    assert out["ok"] is False
    assert out["error"] == "groups_locked"
    assert "ROBOT_ARM_GROUPS_ENABLED" in out["hint"]
    assert shell.lines() == []

    assert send(client, "centre", channel=1)["ok"] is True,         "locking groups must not lock the bounded controls"


def test_a_group_forgets_every_commanded_position(shell, client):
    """A group moved the joints and there is no way to ask where to. Stepping
    from what we told a channel *before* that would compute a small step from
    a number that is no longer about anything."""
    send(client, "centre", channel=1)
    send(client, "up", channel=1)
    send(client, "group", group=2)

    out = send(client, "up", channel=1)
    assert out["ok"] is False
    assert "1500" in out["error"]
    assert shell.lines()[-1] == "#2GC1", "nothing may follow the group blindly"


def test_emmas_greeting_uses_the_vendor_handshake_group_once(shell, monkeypatch):
    monkeypatch.setattr(settings, "robot_greeting_gesture_enabled", True)
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)

    assert asyncio.run(robot_arm.greet()) is True
    assert shell.lines() == ["#6GC1"]


def test_emmas_greeting_cannot_bypass_the_motion_interlock(shell, monkeypatch):
    monkeypatch.setattr(settings, "robot_greeting_gesture_enabled", True)
    monkeypatch.setattr(settings, "robot_greeting_arm_group", 6)
    monkeypatch.setattr(settings, "robot_arm_motion_enabled", False)

    assert asyncio.run(robot_arm.greet()) is False
    assert shell.lines() == []


def test_an_impossible_group_number_is_refused(shell, client):
    assert send(client, "group", group=999)["ok"] is False
    assert shell.lines() == []


# ==================== the wire itself ====================


def test_every_line_is_quoted_and_ends_with_crlf(shell, client):
    """An unquoted `#` or `&` in a device shell has cost this project a page
    opened on the robot without its token. The CRLF is mandatory per the
    board's protocol — a line without it is simply never acted on."""
    send(client, "centre", channel=1)

    written = shell.calls[-1][-1]
    assert written.startswith("printf '#")
    assert written.count("'") == 2
    assert "\\r\\n" in written
    assert "> /dev/ttyUSB10" in written


def test_a_failed_write_is_not_reported_as_sent(shell, client):
    shell.fail = True
    assert send(client, "centre", channel=1)["ok"] is False


def test_state_reports_what_was_measured_not_what_was_configured(shell, client):
    shell.port_exists = False
    out = client.get("/arm/state", params={"token": TOKEN}).json()

    assert out["ok"] is True
    assert out["state"]["configured"] is True
    assert out["state"]["port_present"] is False, \
        "configured and present are different questions"


# ==================== what the page itself promises ====================


def test_the_page_says_the_robots_own_estop_path_dies_with_the_vendor_app(source):
    """The red button on the robot reaches the arm by the vendor app writing
    `#STOP`. We free the port by stopping that app. Whoever is about to press
    a button here must know that the machine's own button is, for the
    duration, not known to reach the arm."""
    page = _visible(source)

    assert "ผ่านแอป Aobo" in page
    assert "เส้นทางนั้นไม่ทำงาน" in page


def test_the_page_says_what_the_button_does_and_does_not_prove(source):
    """It sends a real frame and proves nothing about the arm. Both halves
    have to be on screen: the first so nobody thinks the button is decorative,
    the second so nobody trusts it instead of the power switch."""
    assert "#STOP" in source
    assert "พบแขนขยับต่อหลังส่ง #STOP" in source
    assert "ไฟเลี้ยงเซอร์โว" in source


def test_the_page_warns_that_the_vendor_app_moves_the_arm_on_connect(source):
    """Their `usbInit()` writes group 99 as soon as the port opens, so on
    their app the arm can move because something connected. Somebody testing
    needs to know that before they open it next to a person."""
    code = _visible(source)

    assert "99" in code
    assert "เปิดพอร์ต" in code


def test_stop_is_not_blocked_by_a_command_already_in_flight(source):
    """The control that exists for the moment something is already moving
    cannot be the control that does nothing while something is moving. The
    first version of this page shared one busy gate with every other button,
    so the red one returned early exactly when it was needed."""
    code = _visible(source)

    assert "if (busy && !urgent) return;" in code
    stop = [line for line in code.splitlines() if "$('stop').onclick" in line]
    assert stop, "the stop handler must be readable here"
    handler = code.split("$('stop').onclick")[1].split(";")[0]
    assert "true" in handler, "stop must be dispatched as urgent"


def test_the_page_does_not_call_the_opening_move_a_measurement(source):
    """It commands 1500. It does not read, find, or home anything — there is
    nothing to read — and a label implying otherwise would hide the fact that
    the joint may swing a long way on that press."""
    code = _visible(source)

    assert "สั่งไป 1500" in code
    assert "ตั้งจุดเริ่ม" not in code


def test_the_page_says_groups_are_outside_every_limit(source):
    code = _visible(source)

    assert "ROBOT_ARM_GROUPS_ENABLED" in code
    assert "ข้อจำกัดทุกข้อข้างบนไม่คุมส่วนนี้" in code


def test_the_page_never_labels_a_channel_with_a_joint_name(source):
    """Writing a joint name next to channel 1 would be inventing the wiring
    map this page exists to discover, and the label would outlive the guess.

    Compared as *words*, not substrings, and the first version of this test is
    why: `คอ` matched inside `โปรโตคอล` and failed a page that had done nothing
    wrong. Same bug as `ราคา` matching inside `อาคาร`, which this project has
    now paid for three times — Thai has no spaces between words, so `in` is
    never the right operator for it.
    """
    from app.tools.retrieval import tokenize

    words = set(tokenize(_visible(source)))
    for guess in ("ไหล่", "ข้อศอก", "ข้อมือ", "นิ้ว", "คอ", "หัว"):
        assert guess not in words, f"{guess} is a guess, not a measurement"


def test_stop_stays_on_screen(source):
    assert "position: sticky" in source


def test_the_page_sends_intents_not_pulse_widths(source):
    import re

    body = re.search(r"JSON\.stringify\(Object\.assign\(\{([^}]*)\}", source)
    assert body, "the command body must be readable here"
    assert set(p.split(":")[0].strip() for p in body.group(1).split(",")) \
        == {"token", "action"}
