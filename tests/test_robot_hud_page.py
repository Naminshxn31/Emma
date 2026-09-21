"""The robot status screen at `/hud`.

The look was asked for from diagnostic-poster artwork, and those posters are
dense with readouts that mean nothing — "792/1000", "671/0.032". On artwork
that is style. On a console somebody reads before deciding whether to send a
robot across a room, a decorative readout is worse than a blank one, because
a blank one gets checked and a plausible one gets believed.

So every test in this file is a version of the same question: can a number
reach this screen without something having measured it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAGE = Path(__file__).resolve().parent.parent / "client" / "robot-hud.html"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _script(source: str) -> str:
    """The page's own script, without comments.

    A test that scans for forbidden content will otherwise match the comment
    explaining why it is forbidden — which has happened twice in this repo.
    """
    body = source.split("<script>")[1].split("</script>")[0]
    return re.sub(r"//.*", " ", body)


def test_the_page_is_served(client):
    assert client.get("/hud").status_code == 200


def test_every_reading_starts_empty(source):
    """No cell ships with a value in it. A page that renders plausible
    numbers before the first fetch shows them to whoever opens it next to a
    robot that has not answered yet."""
    cells = re.findall(r'<td class="v[^"]*" id="(\w+)">([^<]*)</td>', source)

    assert cells, "the readout cells must be findable here"
    for name, initial in cells:
        assert initial.strip() in ("—", "ยังไม่ได้ตรวจ", "ยังไม่ยืนยัน", "ยังไม่ทดลอง"), \
            f"{name} ships with {initial!r}, which is a number nobody measured"


def test_values_reach_the_screen_through_one_function(source):
    """`set()` is the only writer, so "unknown" cannot be handled in one place
    and forgotten in another — the failure mode being a stale number left on
    screen after the robot stopped answering."""
    code = _script(source)

    assert "function set(id, value, kind)" in code
    direct = re.findall(r"\$\('(\w+)'\)\.textContent\s*=", code)
    # `fresh` is the clock line and the `*Why` lines carry the *reason* a
    # panel is empty. None of them is a reading, which is the distinction
    # that matters: a reason going stale reads as a stale reason, while a
    # number going stale reads as the robot's current state.
    assert set(direct) <= {"fresh", "aWhy", "cWhy"}, \
        f"these bypass set() and can leave a stale value: {direct}"


def test_a_missing_value_is_drawn_as_missing(source):
    """The dash is not cosmetic: it is the difference between "zero" and
    "we do not know", which for a battery reading is the whole message."""
    code = _script(source)

    assert "value === null || value === undefined || value === ''" in code
    assert "missing ? '—'" in code


def test_the_lidar_is_not_inferred_from_the_chassis_answering(source):
    """The page never reads the lidar. Lighting it up because a different
    subsystem replied would be exactly the measurement-one-layer-away mistake
    this project has made three times."""
    code = _script(source)

    lidar = [line for line in code.splitlines() if "nLidar" in line]
    assert lidar, "the lidar node must be addressed somewhere"
    assert all("'live'" not in line for line in lidar), \
        "nothing on this page measures the lidar, so it cannot be shown live"


def test_the_arm_positions_are_labelled_as_commanded_not_measured(source):
    """A PWM servo never reports where it is. The number shown is what we
    last told it, and the page has to say so or it is a position readout."""
    assert "สั่งไว้ที่" in source
    assert "ไม่ใช่ค่าที่วัดจากข้อต่อ" in source


def test_the_channel_nodes_are_not_drawn_on_joints(source):
    """Placing channel 1 on a shoulder draws a wiring map nobody has, and a
    diagram is believed faster than the caption under it. They sit in their
    own box, labelled as channels of unknown position, until something
    measures which joint each one moves."""
    assert "ยังไม่ทราบตำแหน่งข้อต่อ" in source
    assert "ยังไม่ทราบว่าช่องใดขยับข้อต่อไหน" in source
    for guess in ("ไหล่", "ข้อศอก", "ข้อมือ"):
        assert guess not in source, f"{guess} is a guess, not a measurement"


def test_a_missing_port_is_stated_without_naming_a_cause(source):
    """That the port is absent is what this page checked. That something else
    is holding it is a separate measurement — a root read of /proc — which
    nothing here performs."""
    code = _script(source)

    assert "ไม่พบพอร์ตที่ตั้งค่าไว้" in code
    assert "Aobo" not in code, "the cause needs its own evidence, not a guess"


def test_the_page_does_not_say_the_board_is_silent(source):
    """It says this page has not listened. The vendor's code parses
    `#STOP+OK` and an action-group-finished state, so the board does say
    something — we have never read it, which is a gap here, not a property
    of the hardware."""
    assert "ยังไม่ได้อ่านค่าตอบกลับจากบอร์ดแขน" in source


def test_the_page_carries_the_servo_power_warning(source):
    """The one hazard that no software on this screen can mitigate."""
    assert "จุดตัดไฟเลี้ยงเซอร์โว" in source


def test_the_page_does_not_repeat_the_controls(source):
    """Controls live at /robot and /arm. Two copies of a stop button drift
    apart, and the copy that drifted is found by whoever needed it most."""
    code = _script(source)

    assert "method: 'POST'" not in code
    assert "/robot/command" not in code
    assert "/arm/command" not in code
    assert 'href="/robot"' in source and 'href="/arm"' in source


def test_it_only_reads_the_endpoints_that_already_exist(source):
    """New telemetry means a new measurement, not a new field on a page."""
    code = _script(source)

    assert set(re.findall(r"fetch\('([^']+)'", code)) == {
        "/robot/state", "/arm/state", "/hardware/reports",
    }
