"""The all-controls test console at `/console`.

It is the union of /robot and /arm on one screen, so it inherits every rule
those pages are held to, plus one of its own: the two stop buttons must stay
different things. One cancels a chassis action and has been seen to work; the
other writes a frame nobody has watched stop a joint. A single merged "stop"
would let the proven half lend its credibility to the unproven half.

Nothing here reaches a robot. The page is checked as source; the endpoints it
calls are tested in their own files.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAGE = Path(__file__).resolve().parent.parent / "client" / "robot-console.html"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _visible(source: str) -> str:
    """The page without comments, so a scan for a forbidden word cannot match
    the comment explaining why it is forbidden."""
    source = re.sub(r"<!--.*?-->", " ", source, flags=re.S)
    return re.sub(r"//.*", " ", source)


def _script(source: str) -> str:
    return re.sub(r"//.*", " ", source.split("<script>")[1].split("</script>")[0])


def test_the_page_is_served(client):
    assert client.get("/console").status_code == 200


def test_local_direct_navigation_bootstraps_token_but_cross_site_does_not(monkeypatch):
    from app.config import settings
    from app.main import serve_robot_console
    from starlette.requests import Request

    monkeypatch.setattr(settings, "ws_token", "local-only-test-token")
    def request(site):
        return Request({"type": "http", "method": "GET", "path": "/console",
                        "query_string": b"", "headers": [(b"sec-fetch-site", site.encode())],
                        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 443),
                        "scheme": "https"})

    response = asyncio.run(serve_robot_console(request("none")))
    assert response.status_code == 302
    assert response.headers["location"] == "/console?token=local-only-test-token"

    cross_site = asyncio.run(serve_robot_console(request("cross-site")))
    assert cross_site.status_code == 200
    assert "local-only-test-token" not in str(cross_site.headers)


def test_local_token_is_removed_from_the_visible_url(source):
    code = _script(source)

    assert "location.hostname === '127.0.0.1'" in code
    assert "history.replaceState(null, '', '/console')" in code


def test_token_is_not_persisted_in_link_hrefs_or_the_accessibility_tree(source):
    code = _script(source)

    assert "a.href += q" not in code
    assert "location.assign(a.getAttribute('href') + q)" in code


# ==================== two stops, kept apart ====================


def test_there_are_two_stop_buttons_and_they_say_different_things(source):
    page = _visible(source)

    assert 'id="stopBase"' in source and 'id="stopArm"' in source
    assert "ยกเลิกคำสั่งเดินที่แชสซี" in page
    assert "พบแขนขยับต่อหลังส่ง #STOP" in page


def test_both_stops_skip_the_busy_gate(source):
    """The control that exists for the moment something is moving cannot be
    the control that does nothing while something is moving."""
    code = _script(source)

    assert "if (busy[lane] && !urgent) return;" in code
    assert "'base', action === 'stop'" in code
    assert "'arm', action === 'stop' || action === 'arm'" in code


def test_the_two_lanes_have_separate_busy_flags(source):
    """A slow chassis read must never be why an arm stop waits."""
    code = _script(source)

    assert "const busy = { base: false, arm: false, device: false };" in code


def test_the_page_says_nothing_here_is_the_emergency_stop(source):
    page = _visible(source)

    assert "ไม่มีปุ่มไหนบนหน้านี้เป็นปุ่มหยุดฉุกเฉิน" in page
    assert "ไฟเลี้ยงเซอร์โว" in page


def test_the_page_says_the_robots_own_estop_path_dies_with_the_vendor_app(source):
    page = _visible(source)

    assert "ผ่านแอป Aobo" in page
    assert "เส้นทางนั้นไม่ทำงาน" in page


def test_every_log_line_names_its_lane(source):
    """Two stops pressed in the same second once logged as "stop — sent" and
    "stop — failed". The failure was the chassis being unreachable; the arm
    frame had gone out. Unlabelled, that reads as the arm stop failing."""
    code = _script(source)

    assert "lane === 'base' ? 'ฐานล้อ · ' : lane === 'arm' ? 'แขน · ' : 'อุปกรณ์ · '" in code


# ==================== intents, never numbers ====================


def test_the_page_names_intents_and_never_a_distance_or_pulse(source):
    """Same rule as both control pages: the server decides how far. A page
    that sends a number is one typo away from a joint against its stop."""
    code = _script(source)

    assert "JSON.stringify(Object.assign({ token }, body))" in code
    assert code.count("JSON.stringify(") == 1, "one body builder, so it can be read here"
    for forbidden in ("metres", "pulse", "cycles", "forward_m", "turn_rad", "target:"):
        assert forbidden not in code, f"{forbidden} would be the client choosing a magnitude"


def test_it_only_talks_to_endpoints_that_already_exist(source):
    code = _script(source)

    posts = set(re.findall(r"post\('([^']+)'", code))
    gets = set(re.findall(r"fetch\('([^']+)'", code))
    assert posts == {"/robot/command", "/arm/command", "/android/command",
                     "/cam/control", "/mic/control"}
    assert gets == {"/arm/state", "/robot/state", "/hardware/reports",
                    "/android/state", "/mic/level"}


def test_device_controls_are_allowlisted_and_label_unknown_backlights(source):
    page = _visible(source)
    code = _script(source)

    assert "Backlight A" in page and "Backlight B" in page
    assert "จอกลางหุ่น" in page
    assert "ไม่ใช่ไฟหัว" in page
    assert "ไฟหัวไม่มีคำสั่งที่ยืนยันแล้ว" in page
    assert "data-brightness=\"0\"" in source and "data-brightness=\"100\"" in source
    assert "data-rotation=" not in source
    assert "Android รับค่า 0/90/180/270 แต่ภาพจริงคงอยู่ที่ 0°" in page
    assert "ADB หรือ serial" in page
    assert "shell" not in code.lower()


def test_live_camera_and_microphone_are_shown_as_measured_feeds(source):
    page = _visible(source)

    assert 'id="cameraPreview"' in source
    assert 'id="micBar"' in source
    assert 'id="micGateText"' in source
    assert 'id="probeRobotMic"' in source
    assert "ส่งกลับเฉพาะระดับเสียง" in page
    assert "ไมค์ของเครื่องที่เปิดหน้านั้น" in page
    assert "ไมค์/กล้องของเครื่องที่เปิดหน้านี้" in page


# ==================== honesty of the readings ====================


def test_every_reading_starts_empty(source):
    cells = re.findall(r'<td class="v[^"]*" id="(\w+)">([^<]*)</td>', source)

    assert cells
    for name, initial in cells:
        assert initial.strip() == "—", f"{name} ships with {initial!r}"


def test_arm_positions_are_commanded_not_measured(source):
    page = _visible(source)

    assert "สั่งไว้ที่" in page
    assert "ไม่ใช่ค่าที่วัดจากข้อต่อ" in page
    assert "สั่งไป 1500" in page
    assert "ตั้งจุดเริ่ม" not in page


def test_the_channels_are_not_drawn_on_joints(source):
    from app.tools.retrieval import tokenize

    page = _visible(source)
    assert "ยังไม่ทราบตำแหน่งข้อต่อ" in page
    words = set(tokenize(page))
    for guess in ("ไหล่", "ข้อศอก", "ข้อมือ"):
        assert guess not in words, f"{guess} is a guess, not a measurement"


def test_a_missing_port_is_stated_without_naming_a_cause(source):
    code = _script(source)

    assert "ไม่พบพอร์ตที่ตั้งค่าไว้" in code
    assert "Aobo" not in code


def test_the_lidar_is_not_inferred_from_the_chassis_answering(source):
    code = _script(source)

    lidar = [line for line in code.splitlines() if "nLidar" in line]
    assert lidar and all("'live'" not in line for line in lidar)


def test_group_six_is_presented_as_app_level_evidence_only(source):
    """Three sources point at 6 being the handshake; none of them is the
    board. The page has to say that next to the number, because the number
    is pre-filled and pre-filled numbers get pressed."""
    page = _visible(source)

    assert 'value="6"' in source
    assert "ยังไม่มีข้อใดเป็นการสังเกตบอร์ด" in page
