"""The microphone and camera bench at `/hardware`.

It exists because three different faults arrive at the server looking the
same. A muted microphone, a microphone another app is holding, and a person
standing too far away all reach `session.py` as quiet audio, and the log can
only print `avg=0.0000` and guess which. The page runs in the browser that
owns the device, so it can say which one.

Two properties are worth a test rather than a comment, because both are the
kind that quietly stop being true:

* it sends nothing anywhere — a diagnostic page that uploaded audio would be
  a microphone in a showroom with nobody accountable for the recordings;
* it decides from measurements, never from the absence of an error, which is
  the same rule the movement tools carry as `hardware:`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

PAGE = Path(__file__).resolve().parent.parent / "client" / "hardware.html"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def source() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture
def code(source) -> str:
    """The page with its prose removed.

    The forbidden-word scans below have to read what the page *does*, not
    what it says about itself — this file's own comments name every one of
    the things the page must not contain, and a scan that cannot tell those
    apart would either fail on a comment or have to stop mentioning them.
    """
    import re

    without_html_comments = re.sub(r"<!--.*?-->", "", source, flags=re.S)
    return "\n".join(
        line for line in without_html_comments.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


def test_the_page_is_served(client):
    resp = client.get("/hardware")
    assert resp.status_code == 200
    assert "getUserMedia" in resp.text
    assert "เปิดบนหุ่นจึงวัดหุ่น · เปิดบนคอมจึงวัดคอม" in resp.text


def test_it_is_not_cached(client):
    """A kiosk tab that loaded yesterday's HTML keeps yesterday's JS, and
    "reload the page" quietly serves it from cache — the trap `_NO_CACHE`
    was added for. A diagnostic reporting last week's behaviour is worse
    than no diagnostic."""
    resp = client.get("/hardware")
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_no_audio_or_video_ever_leaves_the_page(code):
    """The line that must never move.

    The page holds a live microphone in a room strangers walk through. It may
    carry its *verdicts* to the sales desk, because a reading nobody can reach
    is not a diagnostic — but the moment a sample of audio can leave, this
    stops being an instrument and becomes a recorder, and the retention
    question `data/logs/` had to be given `TURN_LOG_KEEP_DAYS` to answer
    arrives here with nobody having decided anything.
    """
    for forbidden in ("WebSocket", "XMLHttpRequest", "sendBeacon", "FormData"):
        assert forbidden not in code, f"{forbidden} must not appear on this page"
    # The recording exists only as a blob URL for the <audio> element.
    assert "URL.createObjectURL" in code
    for line in code.splitlines():
        if "fetch(" in line or "body:" in line:
            for carrier in ("blob", "Blob", "chunks", "recorder", "stream"):
                assert carrier not in line, f"a request must not carry {carrier}"


def test_the_only_calls_out_are_the_text_summary(code):
    """Two endpoints, both text.

    Named explicitly rather than counted, so adding a third has to be a
    decision somebody makes here rather than a line that slips in.
    """
    import re

    targets = set(re.findall(r"fetch\(\s*'([^']+)'", code))
    assert targets == {"/hardware/report", "/hardware/reports"}, targets
    assert "JSON.stringify({ label:" in code, "the body is the summary, nothing else"


def test_it_judges_from_numbers_not_from_the_absence_of_an_error(source):
    """`getUserMedia` resolving proves a track was handed over, nothing more.

    The robot's own session log has printed `peak=0.0000` for sixteen seconds
    on a stream that opened perfectly. So every verdict here has to come from
    a sample, and the all-zero case has to be called out separately from
    "quiet" — they have completely different fixes.
    """
    assert "getFloatTimeDomainData" in source, "levels must come from real samples"
    assert "0.02" in source, "the server's own speech threshold, so they agree"
    assert "ศูนย์ล้วน" in source, "digital silence needs its own verdict"


def test_channels_are_metered_separately(source):
    """The manual says four microphones; `MicConfig.channels` says eight.

    Which channel carries the echo-cancelled mix is still an open question
    with the vendor, and sending the wrong one to Gemini means sending audio
    with the robot's own voice still in it. Splitting the stream shows the
    answer instead of asking for it.
    """
    assert "createChannelSplitter" in source
    assert "channelCount" in source


def test_the_browsers_own_processing_is_turned_off(source):
    """Echo cancellation and auto gain would hide the fault being looked for:
    a channel that is empty at the device is indistinguishable from one a
    filter emptied, and auto gain lifts a noise floor until it looks like
    signal."""
    assert "echoCancellation: false" in source
    assert "autoGainControl: false" in source
    assert "noiseSuppression: false" in source


def test_it_says_when_the_origin_is_not_secure(source):
    """The most common reason the microphone "does not work" on this robot is
    not the microphone. An origin the browser does not trust has no
    getUserMedia at all, and the certificate warning that causes it is one
    tap to dismiss and easy to forget."""
    assert "isSecureContext" in source


def test_it_lets_go_of_the_devices_when_the_page_is_left(source):
    """A held camera keeps its light on and stops the voice client opening
    the same device — the failure `greeter.stop()` exists for, one page
    over."""
    assert "pagehide" in source
    assert "getTracks().forEach((t) => t.stop())" in source


def test_frame_rate_is_counted_not_believed(source):
    """This machine's own face camera logs "can't grab frame" while the
    camera reports 30 fps. A number read back from `getSettings()` is what
    was asked for, not what arrived."""
    assert "requestVideoFrameCallback" in source


def test_the_page_carries_no_controls(code):
    """Why it needs no token: it drives nothing.

    Every other page here can reach this machine — open programs, move
    slides, move a robot — which is what `WS_TOKEN` guards. This one is most
    needed exactly when the token is the thing that is wrong, so it must stay
    unable to do anything worth guarding.
    """
    for forbidden in ("go_to_place", "return_to_base", "/api/", "robot_ready"):
        assert forbidden not in code


# ==================== carrying the readings to whoever is reading ====================


def test_a_summary_sent_from_one_device_can_be_read_from_another(client):
    """The whole reason the endpoint exists.

    A browser can only measure the devices of the machine it runs on, so the
    robot's microphone is unreachable from the desk where the person reading
    it is standing — and the robot's screen is a portrait panel on its chest
    that somebody else is usually already using.
    """
    posted = client.post("/hardware/report",
                         json={"label": "หุ่น", "report": "mic: digital silence"})
    assert posted.json()["ok"] is True

    got = client.get("/hardware/reports").json()["reports"]
    assert got[0]["label"] == "หุ่น"
    assert got[0]["report"] == "mic: digital silence"
    assert got[0]["at"], "a reading with no time on it cannot be told from a stale one"


def test_an_empty_summary_is_refused(client):
    assert client.post("/hardware/report", json={"report": "   "}).json()["ok"] is False
    assert client.post("/hardware/report", json={}).json()["ok"] is False


def test_junk_does_not_crash_the_endpoint(client):
    """It takes a POST without a token, so it has to survive being poked."""
    assert client.post("/hardware/report", json=["not", "an", "object"]).json()["ok"] is False
    assert client.post("/hardware/report", content=b"not json").json()["ok"] is False


def test_the_store_is_bounded_in_both_directions(client):
    """No token on the way in means the only thing stopping a device from
    parking a megabyte in this process is the cap."""
    from app.main import _HARDWARE_REPORT_LIMIT, _HARDWARE_REPORTS

    client.post("/hardware/report", json={"report": "x" * (_HARDWARE_REPORT_LIMIT + 500)})
    assert len(client.get("/hardware/reports").json()["reports"][0]["report"]) \
        == _HARDWARE_REPORT_LIMIT

    for i in range(20):
        client.post("/hardware/report", json={"label": str(i), "report": "r"})
    assert len(_HARDWARE_REPORTS) <= 8


def test_reports_are_never_written_to_disk(source):
    """Memory only, and said out loud where somebody would look.

    A file of microphone diagnostics gathered in a showroom is the same shape
    of quiet retention decision that `data/logs/` had to be walked back from.
    """
    from app import main

    assert type(main._HARDWARE_REPORTS).__name__ == "deque", (
        "a bounded in-memory ring, not a file handle and not a list that grows")
    assert "หน่วยความจำของเซิร์ฟเวอร์" in source, "the page has to say where it goes"
