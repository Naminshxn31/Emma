"""Our own handshake instead of the vendor's group 6 (owner 25 Nov 2026, the opening ceremony).

Group 6 offered the hand, came almost down, then bent the elbow back UP inside the group itself
(the owner's video). Its keyframes live in the Torobot board's flash and cannot be read back, so
the app now plays its own offer frame, the existing hold, the existing slow lowering frame (now
really lowering from the offer) and group 99 for the rest -- behind ONE constant,
BodyGestures.HANDSHAKE_OWN_FRAMES, so going back to group 6 is a one-line edit.

25 Nov, later the same day: the owner kept the vendor's handshake ("ใช้ท่าของผู้ผลิตอย่างเดียว"). The switch is
OFF, and BodyGestures.HANDSHAKE_VENDOR_ONLY ends group 6 at its #AGF with the vendor's rest group 99 at once
-- no hold, no lowering frame of ours (those bent a resting elbow back up after group 6 had lowered the hand).
Our frames stay compiled and checked here for a later day.

Compiles BodyGestures with the plain JDK (as tests/test_emma_robot_body_nav_app.py does) and runs
the frames and the checker for real, including on mutated copies (the checker must say no), and
reads BodyController's routing (it needs the Android SDK to compile).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.test_emma_robot_body_nav_app import (
    ARM_MOTION, BODY, GESTURES, ROOT, _javac, code_only, gate_of, method, read,
)

PLAY = "private void play(String id, int gen, List<BodyGestures.Frame> frames, boolean speech)"
OWN = "private void playOwnHandshake(int gen)"

PROBE = r"""
package com.emma.robot.control;
public class Probe {
    public static void main(String[] a) {
        BodyGestures.Frame o = BodyGestures.handshakeOffer();
        BodyGestures.Frame d = BodyGestures.handshakeLowering();
        System.out.println("own=" + BodyGestures.HANDSHAKE_OWN_FRAMES);
        System.out.println("vendor_only=" + BodyGestures.HANDSHAKE_VENDOR_ONLY);
        System.out.println("fallback=" + BodyGestures.GROUP_FALLBACK_MS);
        System.out.println("offer=" + o.durationMs + " " + o.targets);
        System.out.println("lower=" + d.durationMs + " " + d.targets);
        System.out.println("problem=" + BodyGestures.handshakeProblem());
        java.util.LinkedHashSet<Integer> ch = new java.util.LinkedHashSet<>();
        for (int c : BodyGestures.HANDSHAKE_CHANNELS) ch.add(c);
        System.out.println("channels=" + ch);
        System.out.println("offer_clamps=" + (BodyGestures.checkFrames(java.util.Collections.singletonList(o), ch) == null ? "null" : "refused"));
        System.out.println("rest5=" + BodyGestures.handshakeRest(5));
        System.out.println("hold=" + BodyGestures.HANDSHAKE_HOLD_MS);
        System.out.println("est=" + BodyGestures.HANDSHAKE_EST_MS);
        System.out.println("group=" + BodyGestures.HANDSHAKE_GROUP);
        System.out.println("rest_after=" + BodyGestures.REST_AFTER_FRAMES_MS);
        System.out.println("rest_agf=" + BodyGestures.REST_AGF_TIMEOUT_MS);
    }
}
"""


def _run(tmp: Path, source: str) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: the own handshake's frames and checker were NOT compiled or run")
    javac, java = tools
    src = tmp / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "BodyGestures.java").write_text(source, encoding="utf-8")
    (src / "Probe.java").write_text(PROBE, encoding="utf-8")
    out = tmp / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(src / "BodyGestures.java"), str(src / "Probe.java")],
                   check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.Probe"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    values = {}
    for line in run.stdout.splitlines():
        key, _, value = line.partition("=")
        values[key] = value
    return values


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> dict[str, str]:
    return _run(tmp_path_factory.mktemp("own_handshake"), read(GESTURES))


def _pairs(text: str) -> dict[int, int]:
    return {int(c): int(p) for c, p in re.findall(r"(\d+)=(\d+)", text)}


def test_the_vendors_handshake_alone_and_our_frames_still_inside_every_rule(probe):
    assert probe["own"] == "false", "owner 25 Nov: the vendor's handshake"
    assert probe["vendor_only"] == "true", "owner 25 Nov: group 6, then its rest group 99 -- nothing of ours"
    assert probe["problem"] == "null"
    # the 9 Nov pageant's first pose seen on the robot "like a handshake", wrist at rest
    assert probe["offer"] == "1500 {2=1500, 3=1700, 4=1500, 6=1380}"
    assert probe["lower"] == "3000 {2=1500, 3=1300, 4=1500, 6=1500}"
    assert probe["channels"] == "[2, 3, 4, 6]"
    assert probe["offer_clamps"] == "null"
    assert probe["rest5"] == "-1", "ch5: every allowed value is the arm out sideways"
    # the group-6 fallback and the server's numbers are untouched
    assert probe["group"] == "6" and probe["hold"] == "5000" and probe["est"] == "13000"


def test_the_way_down_only_goes_down(probe):
    """The owner's complaint: a joint going back UP on the way down. From the offer, every joint
    of the lowering moves toward its rest (2/4/6 ~1500, the elbow 1300 = the lowest allowed) and
    never past it; nothing the offer left alone moves."""
    offer, lower = _pairs(probe["offer"].split(" ", 1)[1]), _pairs(probe["lower"].split(" ", 1)[1])
    rest = {2: 1500, 3: 1300, 4: 1500, 6: 1500}
    assert set(lower) == set(offer)
    for ch, to in lower.items():
        frm = offer[ch]
        assert abs(to - rest[ch]) <= abs(frm - rest[ch]), ch
        assert (to - rest[ch]) * (frm - rest[ch]) >= 0, ch
    assert offer[3] > lower[3], "the elbow straightens"
    assert offer[6] < lower[6], "the shoulder swings back from forward"


def test_a_blind_done_is_still_not_read_as_sure_by_the_server(probe):
    """app/event_tour.HANDSHAKE_SURE_S: a "done" within it of "started" = the app's own #AGF of
    group 99 (the arm really at rest). The app's no-#AGF timer must still land after it:
    offer + hold + lowering + the beat + REST_AGF_TIMEOUT_MS."""
    sure = float(re.search(r"^HANDSHAKE_SURE_S = ([\d.]+)", read(ROOT / "app/event_tour.py"), re.M).group(1))
    blind = (int(probe["offer"].split()[0]) + int(probe["hold"]) + int(probe["lower"].split()[0])
             + int(probe["rest_after"]) + int(probe["rest_agf"]))
    assert blind > sure * 1000, (blind, sure)


def test_with_the_vendors_handshake_alone_only_a_rested_arm_can_say_done_early(probe):
    """HANDSHAKE_VENDOR_ONLY: every done after group 6's #AGF follows a group 99 command (the arm is put at rest
    either way); the one done with the arm possibly still out is the no-#AGF fallback -- group 99 after
    GROUP_FALLBACK_MS, then REST_AGF_TIMEOUT_MS -- and that must land after HANDSHAKE_SURE_S."""
    sure = float(re.search(r"^HANDSHAKE_SURE_S = ([\d.]+)", read(ROOT / "app/event_tour.py"), re.M).group(1))
    assert int(probe["fallback"]) + int(probe["rest_agf"]) > sure * 1000


def test_group_6s_agf_goes_straight_to_group_99_with_the_vendors_handshake():
    body = code_only(read(BODY))
    finished = method(body, "void onArmFinished()")
    vendor = finished.index('if ("handshake".equals(active) && BodyGestures.HANDSHAKE_VENDOR_ONLY) '
                            'later(gen, () -> restReturn(gen, "#AGF, vendor handshake"), 0);')
    ours = finished.index('else if ("handshake".equals(active)) later(gen, () -> lowerThenRest(gen, "#AGF"), groupHoldMs);')
    assert vendor < ours, "the vendor-only branch is taken before the hold + lowering"
    assert 'else later(gen, () -> restReturn(gen, "#AGF"), groupHoldMs);' in finished, "group 3 rests as before"
    assert body.count("HANDSHAKE_VENDOR_ONLY") == 1, "one switch, read in one place"
    # the arm-out backstop is unchanged: a lost #AGF still gets group 99
    assert 'later(gen, () -> restReturn(gen, "no #AGF heard"), BodyGestures.GROUP_FALLBACK_MS);' in method(body, PLAY)


MUTATIONS = {
    # the offer drives ch5 (the arm sideways) -> refused
    "channel_5": ("frame(HANDSHAKE_OFFER_MS, 2, 1500,", "frame(HANDSHAKE_OFFER_MS, 5, 1300, 2, 1500,"),
    # the lowering swings the shoulder further forward = the hand back up
    "lowering_up_6": ("frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1500)",
                      "frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1300)"),
    # the lowering leaves the shoulder out
    "not_lowered_6": ("frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1500)",
                      "frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500)"),
    # past the rest on the other side (behind the body)
    "lowering_up_6 ": ("frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1500)",
                       "frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1600)"),
    # the elbow past the clamp
    "arm_range_3": ("frame(HANDSHAKE_OFFER_MS, 2, 1500, 3, 1700,", "frame(HANDSHAKE_OFFER_MS, 2, 1500, 3, 1800,"),
    # a fast flick out
    "frame_ms": ("static final int HANDSHAKE_OFFER_MS = 1500;", "static final int HANDSHAKE_OFFER_MS = 500;"),
    # the head
    "channel_8": ("frame(HANDSHAKE_OFFER_MS, 2, 1500,", "frame(HANDSHAKE_OFFER_MS, 8, 1500, 2, 1500,"),
}


@pytest.mark.parametrize("want", sorted(MUTATIONS))
def test_the_checker_refuses_a_bad_handshake(tmp_path, want):
    old, new = MUTATIONS[want]
    text = read(GESTURES)
    assert text.count(old) == 1, old
    out = _run(tmp_path, text.replace(old, new))
    assert out["problem"] == want.strip(), out["problem"]


def test_play_routes_the_handshake_to_our_frames_behind_one_constant():
    body = code_only(read(BODY))
    play = method(body, PLAY)
    own = play.index('if ("handshake".equals(id) && BodyGestures.HANDSHAKE_OWN_FRAMES) {')
    group = play.index('if ("handshake".equals(id) || "greet_arm".equals(id)) {')
    assert own < group
    assert "playOwnHandshake(gen);" in play[own:group] and "return;" in play[own:group]
    assert play.index("host.ensureArm();") < own, "after the arm connect, like every gesture"
    # the group-6 path is still whole behind it: the one-line revert
    assert 'arm.playGroup("handshake".equals(id) ? BodyGestures.HANDSHAKE_GROUP : BodyGestures.GREET_GROUP);' in play
    assert 'later(gen, () -> restReturn(gen, "no #AGF heard"), BodyGestures.GROUP_FALLBACK_MS);' in play
    assert body.count("HANDSHAKE_OWN_FRAMES") == 1, "one switch, read in one place"


def test_our_handshake_offers_holds_then_lowers_slowly():
    body = code_only(read(BODY))
    own = method(body, OWN)
    # the checker at every play, before anything moves
    check = own.index("BodyGestures.handshakeProblem()")
    assert check < own.index("arm.playFrames(")
    assert 'finish(gen, "failed", "needs_mapping");' in own[check:own.index("arm.playFrames(")]
    # the offer frame, the owner's generation, then the hold, then lowerThenRest (slow frame + group 99)
    assert "BodyGestures.handshakeOffer()" in own
    assert "arm.playFrames(frames, () -> gen == generation.get() && !latched()," in own
    assert '() -> later(gen, () -> lowerThenRest(gen, "held"), BodyGestures.HANDSHAKE_HOLD_MS)' in own
    # never a group: no #AGF is read as group 6's, nothing waits on one
    assert "groupRunning = true" not in own and "playGroup" not in own and "resetHome" not in own
    # a run that never reports still comes down -- slowly, and never inside the hold
    assert 'lowerThenRest(gen, "offer overran");' in own
    assert "offer.durationMs + BodyGestures.HANDSHAKE_HOLD_MS + FRAMES_WATCHDOG_SLACK_MS" in own
    assert '"running".equals(phase) && "handshake".equals(active)' in own
    # a lost board ends it; a stop between the check and the command is caught after it
    assert 'finish(gen, "failed", "arm_unavailable");' in own
    assert "stopIfOvertaken(gen);" in own


def test_the_offer_command_is_inside_the_arm_gate_behind_its_check():
    """Review #3 for the new call too: no stop between the generation check and the command."""
    body = code_only(read(BODY))
    own_at = body.index(OWN)
    calls = [m.start() for m in ARM_MOTION.finditer(body) if m.start() > own_at]
    first = calls[0]
    assert first < own_at + len(method(body, OWN)) + 200
    gate = gate_of(body, first)
    assert gate is not None
    assert gate.strip().splitlines()[0].strip() == "if (stale(gen)) return;"


def test_the_head_stays_still_during_our_handshake_as_during_group_6():
    """headBusy: the handshake has no framesFor (a verified id cannot be recorded: checkSave), so a
    head look never interleaves with the hand out -- unchanged from group 6."""
    body = code_only(read(BODY))
    busy = method(body, "private boolean headBusy()")
    assert "if (frames == null) return true;" in busy
    assert "List<BodyGestures.Frame> frames = framesFor(id);" in busy
    # the lowering and group 99 hold the head as before
    assert 'if (groupRunning || "rest_return".equals(phase) || "lowering".equals(phase)) return true;' in busy
