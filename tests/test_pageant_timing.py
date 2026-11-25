"""The pageant's timing handshake (review 24 Nov 2026: there was none).

23 Nov's faster raise (owner at the ceremony's opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม")
moved the server's numbers with the APK's frames (EVENT_PAGEANT_LEAD_S 4.5 -> 2.4, MIN_STANDING_S 18.7 ->
16.8, and with the next frames 2.7 / 18.6). A new server with the APK of 10-23 Nov (raise 6.9 s, two sides
18.1 s) would start her line inside that APK's 3 s first raise frame and bring the hand down mid-stroke of
its second side -- the mid-stroke end 10 Nov's review took out. Now:
* the APK reports `pageant_v` / `pageant_lead_ms` / `pageant_two_sides_ms` in body_caps, computed from
  Pageant.java's own frames (PageantTiming.java, compiled and RUN here with the plain JDK);
* the server (app/event_pageant.py) times the pageant by what the accepting app said: v2 numbers = those,
  no `pageant_v` = the old APK's known-good 4.5 / 18.7; the ack's est_ms cross-checks the numbers.
Nothing here talks to a robot.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from app import event_pageant
from app import robot_face_link as link
from app.config import settings
from tests.test_emma_robot_body_nav_app import BODY, GESTURES, SRC, _javac, code_only, method, read
from tests.test_robot_face_link import _face

ROOT = Path(__file__).resolve().parents[1]
PAGEANT = SRC / "Pageant.java"
HEAD_LOOK = SRC / "HeadLook.java"            # Pageant.check reads HeadLook's clamp since 24 Nov
TIMING = SRC / "PageantTiming.java"
CAPS = {"type": "body_caps", "enabled": True, "allow_direction_known": False, "gestures": {}, "pageant": True}

HARNESS = r"""
package com.emma.robot.control;

import java.util.ArrayList;
import java.util.List;

public class PageantTimingHarness {
    static void p(String k, Object v) { System.out.println(k + "=" + v); }
    static String show(List<BodyGestures.Frame> frames) {
        StringBuilder out = new StringBuilder();
        for (BodyGestures.Frame f : frames) out.append(f.durationMs).append(' ').append(f.targets).append(';');
        return out.toString();
    }
    public static void main(String[] args) {
        List<BodyGestures.Frame> raise = Pageant.raise();
        p("version", PageantTiming.VERSION);
        p("lead_ms", PageantTiming.leadMs());
        p("two_sides_ms", PageantTiming.twoSidesMs());
        p("raise_first_two_ms", raise.get(0).durationMs + raise.get(1).durationMs);
        ArrayList<BodyGestures.Frame> two = new ArrayList<>(raise);
        two.addAll(Pageant.side(1, Pageant.HEAD_FIRST_MS));
        two.addAll(Pageant.side(-1, Pageant.TURN_MS));
        p("raise_and_two_sides_ms", BodyGestures.totalMs(two));
        p("prefix_is_the_sequence", show(Pageant.sequence().subList(0, two.size())).equals(show(two)));
        p("est_ms", BodyGestures.totalMs(Pageant.sequence()) + Pageant.LOWER_MS);
        p("check", Pageant.check(Pageant.sequence()));
    }
}
"""


@pytest.fixture(scope="module")
def timing(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: PageantTiming was NOT compiled or run -- the caps' pageant timing is "
                    "unchecked against Pageant.java's frames (only the source-text and server tests ran)")
    javac, java = tools
    work = tmp_path_factory.mktemp("pageant_timing")
    (work / "PageantTimingHarness.java").write_text(HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(GESTURES), str(HEAD_LOOK), str(PAGEANT), str(TIMING),
                    str(work / "PageantTimingHarness.java")], check=True, capture_output=True, encoding="utf-8",
                   timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.PageantTimingHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    return dict(line.partition("=")[::2] for line in run.stdout.splitlines())


# ---- the APK's half: computed from its own frames ----------------------------------------

def test_the_app_reports_the_timing_of_the_frames_it_plays(timing):
    """Whatever B1-style frame edits come next, the caps follow: the lead is the raise's first two frames
    (the arm out, the forearm up), two sides are the raise + the first side (its turn the head's slow
    first move) + the second -- the same frames sequence() plays, in its order."""
    assert timing["check"] == "null" and timing["version"] == "2"
    assert timing["lead_ms"] == timing["raise_first_two_ms"]
    assert timing["two_sides_ms"] == timing["raise_and_two_sides_ms"] and timing["prefix_is_the_sequence"] == "true"
    # the start ack's est_ms (frames + lowering) holds the two sides: the server's cross-check passes this APK
    assert int(timing["est_ms"]) > int(timing["two_sides_ms"]) > int(timing["lead_ms"]) > 0


def test_the_servers_v2_fallbacks_are_the_current_frames_and_robot_env_does_not_override(timing):
    """settings.event_pageant_lead_s and MIN_STANDING_S are only the fallback for an app saying v2 without
    numbers now -- but they must still be the frames' own (no hard-coded copy of a frame time here)."""
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    default = re.search(r'os\.getenv\("EVENT_PAGEANT_LEAD_S", "([0-9.]+)"\)', config)
    assert default and round(float(default.group(1)) * 1000) == int(timing["lead_ms"])
    assert round(event_pageant.MIN_STANDING_S * 1000) == int(timing["two_sides_ms"]) + round(
        event_pageant.STANDING_SLACK_S * 1000)
    robot_env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*EVENT_PAGEANT_LEAD_S\s*=", robot_env)
    # the old APK's known-good values: its 6.9 s raise's first two stages (3.0 + 1.5), its 18.1 s two sides + slack
    assert event_pageant.V1_LEAD_S == 4.5 and event_pageant.V1_MIN_STANDING_S == 18.7


def test_the_caps_carry_the_timing_only_in_the_pageant_apps_and_never_cost_the_gestures():
    body = code_only(read(BODY))
    caps = method(body, "String list()")
    assert '.put("pageant", host.pageantApp())' in caps
    block = caps[caps.index("if (host.pageantApp()) {"):caps.index("return caps.toString();")]
    assert 'caps.put("pageant_v", PageantTiming.VERSION)' in block
    assert '.put("pageant_lead_ms", PageantTiming.leadMs())' in block
    assert '.put("pageant_two_sides_ms", PageantTiming.twoSidesMs())' in block
    # its own try: a failure leaves the fields out (the server's longer old-APK wait), not the gestures
    assert block.index("try {") < block.index('caps.put("pageant_v"') < block.index("} catch (Exception e) {")
    timing_src = code_only(read(TIMING))
    assert "android." not in timing_src and "org.json" not in timing_src
    assert "Pageant.raise()" in timing_src and "Pageant.sequence()" in timing_src


# ---- the server's half: both pairings ---------------------------------------------------

def _accept(monkeypatch, caps: dict | None, est_ms=47200) -> dict:
    """event_pageant.start against a face whose body_caps are `caps` (None = none sent), the app
    accepting with `est_ms`."""
    s = _face()
    if caps is not None:
        link.receive(s, dict(caps))

    async def request(session, kind, payload, timeout):
        return {"accepted": True, "reason": None, "est_ms": est_ms if payload["action"] == "pageant" else None}

    monkeypatch.setattr(link, "request", request)
    monkeypatch.setattr(event_pageant, "_session", lambda: s)
    monkeypatch.setattr(event_pageant, "_gate", lambda: None)
    out = asyncio.run(event_pageant.start(1))
    assert out["ok"] is True
    return event_pageant.STATE["timing"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    event_pageant.reset()
    monkeypatch.setattr(settings, "event_pageant_lead_s", 2.7)        # the config default (conftest: 0)
    yield
    event_pageant.reset()


def test_new_server_with_the_old_apk_times_it_as_the_old_apk(monkeypatch):
    """The review's case: caps without `pageant_v` (the APK of 10-23 Nov, whose page frame had no such
    field either) -> its own 4.5 s lead and 18.7 s minimum, not this server's 2.7 / 18.6."""
    t = _accept(monkeypatch, CAPS, est_ms=43500)
    assert t["v"] == 1 and t["why"] == "old_app"
    assert event_pageant.lead_s() == 4.5 and event_pageant.min_standing_s() == 18.7


def test_new_server_with_the_new_apk_times_it_by_the_apps_own_numbers(monkeypatch):
    t = _accept(monkeypatch, dict(CAPS, pageant_v=2, pageant_lead_ms=2700, pageant_two_sides_ms=18000))
    assert t == {"v": 2, "lead_ms": 2700, "two_sides_ms": 18000, "why": "caps"}
    assert event_pageant.lead_s() == pytest.approx(2.7)
    assert event_pageant.min_standing_s() == pytest.approx(18.6)
    # the app's numbers, not the knob: frames changed again in the APK move the server with them
    event_pageant.reset()
    monkeypatch.setattr(settings, "event_pageant_lead_s", 9.0)
    _accept(monkeypatch, dict(CAPS, pageant_v=2, pageant_lead_ms=2000, pageant_two_sides_ms=15000))
    assert event_pageant.lead_s() == pytest.approx(2.0) and event_pageant.min_standing_s() == pytest.approx(15.6)


def test_numbers_not_believed_are_the_old_apks_wait(monkeypatch):
    cases = [
        (dict(CAPS, pageant_v=2, pageant_lead_ms=2700, pageant_two_sides_ms=18000), 12000, "est_ms_disagrees"),
        (dict(CAPS, pageant_v=2, pageant_lead_ms=0, pageant_two_sides_ms=18000), 47200, "implausible"),
        (dict(CAPS, pageant_v=2, pageant_lead_ms=19000, pageant_two_sides_ms=18000), 47200, "implausible"),
        (dict(CAPS, pageant_v=2, pageant_lead_ms=2700, pageant_two_sides_ms=90000), 47200, "implausible"),
        (dict(CAPS, pageant_v=3), 47200, "unknown_version"),
        (dict(CAPS, pageant_v="2", pageant_lead_ms=2700, pageant_two_sides_ms=18000), 47200, "old_app"),
        (dict(CAPS, pageant_v=True), 47200, "old_app"),
    ]
    for caps, est, why in cases:
        event_pageant.reset()
        t = _accept(monkeypatch, caps, est_ms=est)
        assert (t["v"], t["why"]) == (1, why), caps
        assert event_pageant.lead_s() == 4.5 and event_pageant.min_standing_s() == 18.7, caps


def test_v2_without_numbers_or_no_caps_at_all_is_this_servers_v2_constants(monkeypatch):
    t = _accept(monkeypatch, dict(CAPS, pageant_v=2))
    assert (t["v"], t["why"]) == (2, "v2_constants")
    assert event_pageant.lead_s() == 2.7 and event_pageant.min_standing_s() == event_pageant.MIN_STANDING_S
    # no caps from that session at all: never a real app's accepted start (the link refuses a pageant to a
    # face whose caps do not say it plays one) -- a test's own link; the knobs, as every other test uses them
    event_pageant.reset()
    t = _accept(monkeypatch, None)
    assert t["v"] is None and event_pageant.lead_s() == 2.7
    assert event_pageant.min_standing_s() == event_pageant.MIN_STANDING_S


def test_lead_zero_is_no_wait_whatever_the_app(monkeypatch):
    _accept(monkeypatch, CAPS, est_ms=43500)
    monkeypatch.setattr(settings, "event_pageant_lead_s", 0.0)
    assert event_pageant.lead_s() == 0.0
    assert event_pageant.min_standing_s() == 18.7, "the minimum has no off switch: one turn each way"


def test_hand_up_waits_the_apps_lead_not_the_knob(monkeypatch):
    """hand_up reads the accepting app's lead: 0.3 s from the caps under a 5 s knob."""
    import time

    _accept(monkeypatch, dict(CAPS, pageant_v=2, pageant_lead_ms=300, pageant_two_sides_ms=1000), est_ms=2000)
    monkeypatch.setattr(settings, "event_pageant_lead_s", 5.0)
    event_pageant.STATE["started_at"] = time.time()

    async def go():
        t = time.monotonic()
        assert await event_pageant.hand_up(1, lambda: True) is True
        return time.monotonic() - t

    waited = asyncio.run(go())
    assert 0.25 <= waited < 2.0


def test_end_after_line_holds_the_old_apks_two_sides(monkeypatch):
    """Standing, the old APK's hand stays up its own minimum (V1_MIN_STANDING_S, shortened here) even
    when this server's v2 minimum is shorter: never down mid-stroke of its second side."""
    import time

    _accept(monkeypatch, CAPS, est_ms=43500)
    monkeypatch.setattr(event_pageant, "MIN_STANDING_S", 0.1)
    monkeypatch.setattr(event_pageant, "V1_MIN_STANDING_S", 0.8)
    monkeypatch.setattr(event_pageant, "TAIL_S", 0.0)
    event_pageant.STATE["started_at"] = time.time()
    t = time.monotonic()
    asyncio.run(event_pageant.end_after_line(1, standing=True))
    assert time.monotonic() - t >= 0.7
