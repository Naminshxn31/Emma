"""The head look on the robot app (HeadLook.java; owner 31 Oct 2026).

The server (app/head_follow.py) sends `emma-body:look:<pan>:<tilt>` in
PERCENT; the app owns the pulses, the channels and the hard clamps. Two kinds
of check, the project's Android pattern (test_emma_robot_body_nav_app.py):
`HeadLook.java` has no android.* and is COMPILED and RUN with the plain JDK;
the rest are source contracts read from the Java. Nothing talks to a robot.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_emma_robot_body_nav_app import (ARM, BODY, FACE, GESTURES, MAIN, SRC, _javac, code_only,
                                                java_string_constant, method, read)

HEAD = SRC / "HeadLook.java"
ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
package com.emma.robot.control;

public class LookHarness {
    static void p(String k, Object v) { System.out.println(k + "=" + v); }
    static String arr(int[] a) { return a == null ? "null" : a[0] + "," + a[1]; }
    public static void main(String[] args) {
        p("pan.0", HeadLook.panPulse(0));
        p("pan.100", HeadLook.panPulse(100));
        p("pan.-100", HeadLook.panPulse(-100));
        p("pan.50", HeadLook.panPulse(50));
        p("pan.999", HeadLook.panPulse(999));
        p("tilt.100", HeadLook.tiltPulse(100));
        p("tilt.-100", HeadLook.tiltPulse(-100));
        p("tilt.-25", HeadLook.tiltPulse(-25));
        p("valid.ok", HeadLook.valid(-100, 100));
        p("valid.bad", HeadLook.valid(101, 0));
        p("ms.unknown", HeadLook.durationMs(false, 1500, 1500, 1510, 1500));
        p("ms.short", HeadLook.durationMs(true, 1500, 1500, 1510, 1500));
        p("ms.200", HeadLook.durationMs(true, 1500, 1500, 1700, 1500));
        p("ms.full", HeadLook.durationMs(true, 1300, 1500, 1700, 1500));
        p("ms.tilt", HeadLook.durationMs(true, 1500, 1300, 1500, 1600));
        p("cmd.ok", HeadLook.command(1550, 1480, 800));
        p("cmd.low", HeadLook.command(1299, 1500, 800));
        p("cmd.high", HeadLook.command(1500, 1701, 800));
        p("cmd.fast", HeadLook.command(1500, 1500, 100));
        p("pos.done", arr(HeadLook.position(false, 0, 0, 1600, 1550, 1000L, 800, 1800L)));
        p("pos.mid", arr(HeadLook.position(true, 1500, 1500, 1600, 1500, 1000L, 800, 1400L)));
        p("pos.unknown", arr(HeadLook.position(false, 0, 0, 1600, 1500, 1000L, 3000, 1400L)));
        p("ref.none", HeadLook.refusal(true, false, true, null, false, false, false, false));
        p("ref.off", HeadLook.refusal(false, true, false, "navigating", true, true, true, false));
        p("ref.op", HeadLook.refusal(true, true, false, "navigating", true, true, true, false));
        p("ref.fg", HeadLook.refusal(true, false, false, "navigating", true, true, true, false));
        p("ref.nav", HeadLook.refusal(true, false, true, "navigating", true, true, true, false));
        p("ref.navu", HeadLook.refusal(true, false, true, "nav_unknown", true, true, true, false));
        p("ref.latch", HeadLook.refusal(true, false, true, null, true, true, true, false));
        p("ref.busy", HeadLook.refusal(true, false, true, null, false, true, true, false));
        p("ref.soon", HeadLook.refusal(true, false, true, null, false, false, true, false));
        p("ref.centre.nav", HeadLook.refusal(true, false, true, "navigating", false, false, false, true));
        p("ref.centre.navu", HeadLook.refusal(true, false, true, "nav_unknown", false, false, false, true));
        p("ref.centre.latch", HeadLook.refusal(true, false, true, "navigating", true, false, false, true));
        p("ref.centre.off", HeadLook.refusal(false, false, true, "navigating", false, false, false, true));
        p("ref.centre.op", HeadLook.refusal(true, true, true, "navigating", false, false, false, true));
        p("centre.yes", HeadLook.isCentre(1500, 1500));
        p("centre.no", HeadLook.isCentre(1500, 1520));
    }
}
"""


@pytest.fixture(scope="module")
def look(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: HeadLook was NOT compiled or run -- the head's pulses, clamps, "
                    "timing and refusal order are unchecked beyond the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("headlook")
    (work / "LookHarness.java").write_text(HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(HEAD), str(work / "LookHarness.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.LookHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    return dict(line.partition("=")[::2] for line in run.stdout.splitlines())


def test_percent_maps_to_the_swept_range_and_no_further(look):
    # 15 Oct sweep: ch7/ch8 driven to 1300 and 1700; + = robot's left / down
    assert look["pan.0"] == "1500"
    assert look["pan.100"] == "1700" and look["pan.-100"] == "1300"
    assert look["pan.50"] == "1600"
    assert look["pan.999"] == "1700", "a bad percent is clamped, never extrapolated"
    assert look["tilt.100"] == "1700" and look["tilt.-100"] == "1300" and look["tilt.-25"] == "1450"
    assert look["valid.ok"] == "true" and look["valid.bad"] == "false"


def test_every_move_is_timed_and_the_blind_one_is_the_owners_t3000(look):
    assert look["ms.unknown"] == "3000"
    assert look["ms.short"] == "500", "never faster than MIN_MS"
    assert look["ms.200"] == "800"
    assert look["ms.full"] == "1600"
    assert look["ms.tilt"] == "1200", "the longer of the two channels' travel"


def test_the_board_line_is_one_fixed_shape_and_refuses_outside_the_clamp(look):
    assert look["cmd.ok"] == "#7P1550#8P1480T800"
    assert look["cmd.low"] == "null" and look["cmd.high"] == "null" and look["cmd.fast"] == "null"


def test_a_look_mid_move_starts_from_an_estimate(look):
    assert look["pos.done"] == "1600,1550", "a finished move is known even from an unknown start"
    assert look["pos.mid"] == "1550,1500"
    assert look["pos.unknown"] == "null"


def test_the_refusal_order(look):
    assert look["ref.none"] == "null"
    assert [look[k] for k in ("ref.off", "ref.op", "ref.fg", "ref.nav", "ref.navu", "ref.latch",
                              "ref.busy", "ref.soon")] == [
        "disabled", "operator_screen", "not_foreground", "navigating", "nav_unknown", "latched",
        "busy", "too_soon"]


def test_a_look_back_to_centre_passes_a_walk_and_nothing_else(look):
    # 31 Oct, round 2: a head left turned when a walk starts faced sideways for all of it
    assert look["ref.centre.nav"] == "null" and look["ref.centre.navu"] == "null"
    assert look["ref.centre.latch"] == "latched", "a stop still holds everything"
    assert look["ref.centre.off"] == "disabled" and look["ref.centre.op"] == "operator_screen"
    assert look["centre.yes"] == "true" and look["centre.no"] == "false"


# ---- source contracts --------------------------------------------------------

def test_the_head_limits_are_the_swept_range_and_the_gestures_keep_theirs():
    head = read(HEAD)
    for name, value in (("LIMIT_MIN", 1300), ("LIMIT_MAX", 1700), ("PAN_SPAN_US", 200),
                        ("TILT_DOWN_SPAN_US", 200), ("TILT_UP_SPAN_US", 200), ("UNKNOWN_MS", 3000),
                        ("PAN", 7), ("TILT", 8), ("CENTRE", 1500)):
        assert re.search(r"static final int %s = %d;" % (name, value), head), name
    # the cited evidence exists
    assert "docs/robot-servo-limits-2026-10-31.md" in head
    assert (ROOT / "docs" / "robot-servo-limits-2026-10-31.md").is_file()
    # the voice gestures' +-40 is untouched (their own scan test still holds)
    gestures = read(GESTURES)
    assert "static final int HEAD_LIMIT_MIN = 1460;" in gestures
    assert "static final int HEAD_LIMIT_MAX = 1540;" in gestures
    assert "HeadLook" not in code_only(gestures)
    assert "import android" not in code_only(head) and "org.json" not in code_only(head)


def test_the_grammar_has_the_look_shape_and_maps_it_to_its_bridge_call():
    face = read(FACE)
    pattern = re.compile(java_string_constant(face, "BODY_LOOK"))
    for good in ("emma-body:look:0:0", "emma-body:look:-100:100", "emma-body:look:45:-20"):
        assert pattern.fullmatch(good), good
    for bad in ("emma-body:look:1000:0", "emma-body:look:+5:0", "emma-body:look:5", "emma-body:look:5:0\n",
                "emma-body:look:5:0:speech", "emma-body:look:a:b", "emma-body:look:5.5:0"):
        assert not pattern.fullmatch(bad), bad
    answer = code_only(method(face, "private String answer(String message)"))
    assert ("if (look.matches()) return bridge.bodyLook(Integer.parseInt(look.group(1)), "
            "Integer.parseInt(look.group(2)));") in answer
    assert answer.rstrip().endswith("return BAD_REQUEST;")
    assert "String bodyLook(int pan, int tilt);" in face
    assert "return body.look(pan, tilt);" in code_only(method(read(MAIN), "public String bodyLook(int pan, int tilt)"))


def test_a_look_passes_the_gates_then_rechecks_them_under_the_arm_lock():
    body = code_only(read(BODY))
    look = method(body, "String look(int pan, int tilt)")
    assert "HeadLook.refusal(enabled(), host.operatorVisible(), host.foreground(), host.navBlock()," in look
    assert "latched(now), headBusy(), false, HeadLook.isCentre(toPan, toTilt));" in look
    # the answer says how long the move takes (the server reads the picture after it)
    assert "return lookAck(true, null, ms);" in look
    assert "HeadLook.UNKNOWN_MS : HeadLook.durationMs(true, at[0], at[1], toPan, toTilt);" in look
    ack = method(body, "private static String lookAck(boolean accepted, String reason, int ms)")
    assert '.put("ms", ms < 0 ? JSONObject.NULL : Integer.valueOf(ms))' in ack
    centre = method(body, "private void centreIfIdle()")
    assert "latched(now), headBusy(), false, true);" in centre
    # the gate calls out (nav launcher, pose store) before lookLock, never inside it
    locked = look[look.index("synchronized (lookLock)"):]
    locked = locked[:locked.index("}")]
    for call in ("host.", "headBusy()", "enabled()", "latched("):
        assert call not in locked, call
    send = method(body, "private void sendLook(int toPan, int toTilt)")
    assert "io.execute(" in send
    gate = send.index("synchronized (armGate)")
    check = send.index("if (mine != lookSeq.get() || gen != generation.get() || latched()", gate)
    write = send.index("arm.writeLine(line);", check)
    assert gate < check < write
    assert "|| (host.navBlock() != null && !HeadLook.isCentre(toPan, toTilt))" in send
    assert "|| headBusy()) return;" in send
    # a group, a rest or a gesture that moves the head owns it
    busy = method(body, "private boolean headBusy()")
    for part in ('groupRunning || "rest_return".equals(phase)', "BodyGestures.isHead(id)",
                 "frame.targets.containsKey(HeadLook.PAN)"):
        assert part in busy, part


def test_every_stop_voids_queued_looks_and_forgets_the_head():
    body = code_only(read(BODY))
    void = method(body, "private void voidEverything(String reason)")
    gate = void.index("synchronized (armGate)")
    assert void.index("lookSeq.incrementAndGet();", gate) < void.index("}", gate)
    assert "headLost();" in void
    # every place that already forgot the head for the gestures forgets the look's too
    resets = body.count("headKnown = false;") - 1          # minus the field's own declaration
    assert resets == 5
    assert body.count("headLost();") == resets + 2        # + voidEverything, + a failed write


def test_the_raw_line_shares_the_frames_write_lock_and_does_not_end_a_pose_run():
    arm = code_only(read(ARM))
    line = method(arm, "void writeLine(String command)")
    assert "synchronized (writeLock)" in line and "poseGeneration" not in line
    frames = method(arm, "void playFrames(List<PoseFrame> source, BooleanSupplier stillCurrent, Runnable onDone, Runnable onAbort)")
    assert "synchronized (writeLock)" in frames
    assert arm.count('armManager.f(') == 2


def test_the_app_says_it_can_look_and_centres_the_head_by_itself():
    body = code_only(read(BODY))
    assert '.put("look", true)' in method(body, "String list()")
    look = method(body, "String look(int pan, int tilt)")
    assert "ui.postAtTime(this::centreIfIdle, LOOK_TIMER" in look
    # its own timer token: a gesture's finish() clears TIMERS, never the look's
    assert "ui.removeCallbacksAndMessages(LOOK_TIMER)" in look
    assert "LOOK_TIMER" not in method(body, "private synchronized void finish(int gen, String state, String reason)")
