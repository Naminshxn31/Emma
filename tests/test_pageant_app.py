"""ท่านางงาม on the robot app (Pageant.java + BodyController; owner 9 Nov 2026: "ขอเป็นท่านางงามเลย
ได้ไหม ที่เดินแล้วหันหน้าโบกมือไปมา" -> "โบกไปด้วยระหว่างเดิน" and "ท่านี้จะใช้เฉพาะโหมดนี้แก้กฏได้").

The project's Android pattern (test_head_look_app.py): `Pageant.java` has no android.* and is
COMPILED and RUN with the plain JDK -- its frames, its clamps, its head timing; the rest are source
contracts read from the Java (the only-in-Emma-Event gate, the way down, the head it owns).
Nothing here talks to a robot; the pose was played standing on one on 10 Nov, never yet on a walk.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.test_emma_robot_body_nav_app import BODY, FACE, GESTURES, MAIN, SRC, _javac, code_only, method, read

PAGEANT = SRC / "Pageant.java"
HEADLOOK = SRC / "HeadLook.java"   # check() reads HeadLook's clamp for the blind first head move

HARNESS = r"""
package com.emma.robot.control;

import java.util.ArrayList;
import java.util.List;

public class PageantHarness {
    static void p(String k, Object v) { System.out.println(k + "=" + v); }
    static List<BodyGestures.Frame> with(BodyGestures.Frame extra) {
        ArrayList<BodyGestures.Frame> out = new ArrayList<>(Pageant.sequence());
        out.add(extra);
        return out;
    }
    static List<BodyGestures.Frame> one(BodyGestures.Frame f) {
        ArrayList<BodyGestures.Frame> out = new ArrayList<>();
        out.add(f);
        return out;
    }
    public static void main(String[] args) {
        List<BodyGestures.Frame> seq = Pageant.sequence();
        p("frames", seq.size());
        p("total", BodyGestures.totalMs(seq));
        p("check", Pageant.check(seq));
        p("check.lower", Pageant.check(one(Pageant.lower())));
        p("check.with_lower", Pageant.check(with(Pageant.lower())));
        StringBuilder chans = new StringBuilder();
        java.util.TreeSet<Integer> used = new java.util.TreeSet<>();
        int heads = 0;
        Integer last = null;
        StringBuilder headPath = new StringBuilder();
        for (BodyGestures.Frame f : seq) {
            used.addAll(f.targets.keySet());
            Integer h = f.targets.get(7);
            if (h != null) {
                if (last != null && !h.equals(last)) heads++;
                last = h;
                headPath.append(h).append('@').append(f.durationMs).append(' ');
            }
        }
        p("channels", used);
        p("head_moves", heads);
        p("head_path", headPath.toString().trim());
        p("first", seq.get(0).durationMs + " " + seq.get(0).targets);
        p("two_sides_ms", BodyGestures.totalMs(seq.subList(0, Pageant.raise().size() + 2 * (1 + Pageant.STROKES))));
        p("raise_ms", BodyGestures.totalMs(Pageant.raise()));
        p("raise_first_ms", Pageant.RAISE_MS);
        p("lead_ms", Pageant.RAISE_MS + Pageant.RAISE_UP_MS);
        int raiseHeads = 0;
        for (BodyGestures.Frame f : Pageant.raise()) if (f.targets.containsKey(7)) raiseHeads++;
        p("raise_heads", raiseHeads);
        ArrayList<BodyGestures.Frame> turnAtTurnMs = new ArrayList<>(Pageant.raise());
        turnAtTurnMs.addAll(Pageant.side(1, Pageant.TURN_MS));
        p("bad.first_turn_at_turn_ms", Pageant.check(turnAtTurnMs));
        p("third", seq.get(2).durationMs + " " + seq.get(2).targets);
        for (int i = 3; i <= 8; i++) p("frame" + i, seq.get(i).durationMs + " " + seq.get(i).targets);
        p("lower", Pageant.lower().durationMs + " " + Pageant.lower().targets);
        p("bad.ch5", Pageant.check(with(BodyGestures.frame(1000, 5, 1461))));
        p("ok.ch5", Pageant.check(with(BodyGestures.frame(1000, 5, 1300))));
        p("ok.elbow_high", Pageant.check(with(BodyGestures.frame(1000, 3, 1940))));
        p("bad.upper_high", Pageant.check(with(BodyGestures.frame(1000, 4, 1701))));
        p("bad.ch8", Pageant.check(with(BodyGestures.frame(1000, 8, 1500))));
        p("bad.ch13", Pageant.check(with(BodyGestures.frame(1000, 13, 1500))));
        p("bad.arm_high", Pageant.check(with(BodyGestures.frame(1000, 3, 1941))));
        p("bad.arm_low", Pageant.check(with(BodyGestures.frame(1000, 3, 1299))));
        p("bad.swing_low", Pageant.check(with(BodyGestures.frame(1000, 6, 1219))));
        p("ok.swing_high", Pageant.check(with(BodyGestures.frame(1000, 6, 1220))));
        p("bad.upper_low", Pageant.check(with(BodyGestures.frame(1000, 4, 1299))));
        p("second", seq.get(1).durationMs + " " + seq.get(1).targets);
        StringBuilder upper = new StringBuilder();
        for (BodyGestures.Frame f : seq) { Integer u = f.targets.get(4); if (u != null) upper.append(u).append(' '); }
        p("upper_path", upper.toString().trim());
        StringBuilder wrist = new StringBuilder();
        for (BodyGestures.Frame f : seq) { Integer w = f.targets.get(2); wrist.append(w == null ? -1 : w).append(' '); }
        p("wrist_path", wrist.toString().trim());
        StringBuilder shoulder = new StringBuilder();
        for (BodyGestures.Frame f : seq) {
            shoulder.append(f.targets.get(5)).append('/').append(f.targets.get(6)).append(' ');
        }
        p("shoulder_path", shoulder.toString().trim());
        StringBuilder elbow = new StringBuilder();
        for (BodyGestures.Frame f : seq) { Integer e = f.targets.get(3); elbow.append(e == null ? -1 : e).append(' '); }
        p("elbow_path", elbow.toString().trim());
        p("bad.head_wide", Pageant.check(with(BodyGestures.frame(3000, 7, 1601))));
        p("bad.head_fast", Pageant.check(with(BodyGestures.frame(1500, 7, 1600))));
        p("bad.head_first_fast", Pageant.check(one(BodyGestures.frame(2999, 7, 1500))));
        // the blind first move by its worst travel from HeadLook's clamp (review 23 Nov), 15 ms/us
        p("ok.blind_centre", Pageant.check(one(BodyGestures.frame(3000, 7, 1500))));
        p("ok.blind_1600", Pageant.check(one(BodyGestures.frame(4500, 7, 1600))));
        p("bad.blind_1600", Pageant.check(one(BodyGestures.frame(4499, 7, 1600))));
        p("bad.blind_1400", Pageant.check(one(BodyGestures.frame(4499, 7, 1400))));
        p("bad.blind_1600_at_3000", Pageant.check(one(BodyGestures.frame(3000, 7, 1600))));
        ArrayList<BodyGestures.Frame> tonight = new ArrayList<>(Pageant.raise());
        tonight.addAll(Pageant.side(1, 3000));
        p("bad.first_turn_at_3000", Pageant.check(tonight));
        p("head_first_ms", Pageant.HEAD_FIRST_MS);
        // side(int) is a LATER side (PageantTiming counts frames with it): the sequence's second side, frame for frame
        List<BodyGestures.Frame> later = Pageant.side(-1);
        int at = Pageant.raise().size() + 1 + Pageant.STROKES;
        boolean same = later.size() == 1 + Pageant.STROKES;
        for (int i = 0; same && i < later.size(); i++) {
            same = later.get(i).durationMs == seq.get(at + i).durationMs && later.get(i).targets.equals(seq.get(at + i).targets);
        }
        p("side_later_is_second_side", same);
        p("side1_turn_ms", Pageant.side(1).get(0).durationMs);
        p("lower_ms", Pageant.LOWER_MS);
        for (int i = 0; i < seq.size(); i++) p("f" + i, seq.get(i).durationMs + " " + seq.get(i).targets);
        p("f_lower", Pageant.lower().durationMs + " " + Pageant.lower().targets);
        p("bad.frame_short", Pageant.check(one(BodyGestures.frame(799, 2, 1500))));
        p("bad.empty", Pageant.check(new ArrayList<>()));
        ArrayList<BodyGestures.Frame> longer = new ArrayList<>(seq);
        for (int i = 0; i < 4; i++) longer.add(BodyGestures.frame(5000, 2, 1500));
        p("bad.too_long", Pageant.check(longer));
    }
}
"""


@pytest.fixture(scope="module")
def pageant(tmp_path_factory) -> dict[str, str]:
    tools = _javac()
    if tools is None:
        pytest.skip("no JDK found: Pageant was NOT compiled or run -- the pageant's frames, clamps and "
                    "head timing are unchecked beyond the source-text tests")
    javac, java = tools
    work = tmp_path_factory.mktemp("pageant")
    (work / "PageantHarness.java").write_text(HARNESS, encoding="utf-8")
    out = work / "out"
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(out), str(GESTURES), str(HEADLOOK), str(PAGEANT),
                    str(work / "PageantHarness.java")], check=True, capture_output=True, encoding="utf-8", timeout=120)
    run = subprocess.run([java, "-cp", str(out), "com.emma.robot.control.PageantHarness"],
                         check=True, capture_output=True, encoding="utf-8", timeout=60)
    return dict(line.partition("=")[::2] for line in run.stdout.splitlines())


def test_the_pageant_is_inside_every_clamp_and_ends_by_itself(pageant):
    assert pageant["check"] == "null" and pageant["check.lower"] == "null" and pageant["check.with_lower"] == "null"
    # the raise (1.5 + 1.2 + 0.8 + 0.8 s since 23 Nov's review), then whole sides only: 4.3 s + the first side (its
    # turn 4.5 s, the head's blind first move, + 3 x 1.2 s strokes) + 5 sides x (2 s turn + 3 x 1.2 s) = 40.4 s,
    # under 45 s (a seventh side would end at 46.0 -- tonight's first cut fitted 7 sides in 44.2 s only by
    # moving the head blind at 10 ms/us). Until 23 Nov: 6.9 s + 6 sides = 40.5 s in 28 frames
    assert pageant["frames"] == "28" and pageant["total"] == "40400"
    # right arm (wrist, elbow, upper arm, shoulder out since the owner's video, shoulder swing) and the
    # head turn -- never ch8 (the tilt that burned), a finger, the left arm
    assert pageant["channels"] == "[2, 3, 4, 5, 6, 7]"
    # 10 Nov, the owner's video ("แบบนี้ลองสั่งดู": the upper arm out to the side, the forearm upright),
    # driven on the robot with the PC camera in this order: out 45 deg with the elbow half bent, the arm
    # a little forward and the forearm up, then further out and the elbow fully bent -- the same poses in
    # the same order; only the times are 23 Nov's (owner, the ceremony's opening on the robot: "ยกมือช้าไป
    # หน่อย อยากให้ไวกว่านี้ได้ไหม" -- 3 + 1.5 + 1.2 + 1.2 s, the first 3 s only because the head centred in it)
    assert pageant["first"] == "1500 {2=1540, 3=1660, 4=1380, 5=1300}"
    # The second 1.2 s, not tonight's first cut 0.9 (review 23 Nov: ch6, the sensitive swing, 200 us at 0.22 us/ms)
    assert pageant["second"] == "1200 {3=1820, 6=1300}"
    assert pageant["third"] == "800 {3=1940, 5=1420}"
    # 15:40, the owner at the robot: higher (ch6 1220), folded a little in (ch5 1420 above), the upper
    # arm turned a little in (ch4 1460)
    assert pageant["frame3"] == "800 {4=1460, 6=1220}"


def test_the_hand_is_up_in_four_seconds_and_the_head_no_faster(pageant):
    """23 Nov, the owner watching the ceremony's opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม"
    (the raise was 6.9 s). The raise is 4.3 s with no head in it -- the head's centring (its first move from
    where nobody knows) moved to the first turn. Review 23 Nov: tonight's first cut gave that turn 3 s, i.e.
    up to 300 us (from HeadLook's clamp end 1300 to 1600) at 10 ms/us -- faster than the head ever ran blind
    (the old centring: <= 200 us in 3 s = 15 ms/us). check() now times the blind move by its worst travel at
    15 ms/us, so the first turn is 4.5 s and tonight's 3 s (or TURN_MS) plays nothing. The end inside the
    first raise frame stays group 99: RAISE_MS is that frame's own length."""
    assert pageant["raise_ms"] == "4300" and pageant["raise_heads"] == "0"
    assert pageant["first"].startswith(pageant["raise_first_ms"] + " ")
    assert pageant["frame4"].startswith("4500 ") and "7=1600" in pageant["frame4"]
    assert pageant["head_first_ms"] == "4500"
    assert pageant["bad.first_turn_at_turn_ms"] == "head_first_fast"
    assert pageant["bad.first_turn_at_3000"] == "head_first_fast", "tonight's first cut: 300 us blind in 3 s"


def test_the_blind_head_move_is_timed_by_its_worst_travel(pageant):
    """Review 23 Nov: "the first head-moving frame must be >= worst-case travel from either clamp end (1300/1700)"
    -- the head's start is wherever a look left it inside HeadLook's clamp. 15 ms/us of that travel: 1600 or
    1400 is 300 us from the far end (4.5 s), the centre 200 us (3 s -- the lowering, which centres a head this
    run never sent, is exactly that and passes on its own)."""
    assert pageant["ok.blind_1600"] == "null" and pageant["bad.blind_1600"] == "head_first_fast"
    assert pageant["bad.blind_1400"] == "head_first_fast", "from 1700: the other clamp end counts too"
    assert pageant["bad.blind_1600_at_3000"] == "head_first_fast"
    assert pageant["ok.blind_centre"] == "null" and pageant["bad.head_first_fast"] == "head_first_fast"
    assert pageant["check.lower"] == "null"


def test_the_one_arg_side_is_a_later_side(pageant):
    """Review 23 Nov #5: the 1-arg turn(int) was dead (removed); side(int) stays -- B2's PageantTiming counts a
    side's frames with it -- and is pinned: it is exactly the sequence's second side (turn at TURN_MS from the
    side before), never the first (the blind move, which check() refuses at TURN_MS)."""
    assert pageant["side_later_is_second_side"] == "true"
    assert pageant["side1_turn_ms"] == "2000"
    assert "static BodyGestures.Frame turn(int side) {" not in code_only(read(PAGEANT))


def test_an_end_during_the_arm_connect_plays_no_frames():
    """Review 23 Nov #4: an end (pageant_end) arriving while ensureArm connects finds pageantPlayAt 0 and queues
    group 99 (pageantLower -> restReturn), but playPageant still went on to "started" and the raise's frames
    after it. Now both the "started" record and the frames re-check that the run is still the running pageant
    -- the frames under armGate, next to the command."""
    body = code_only(read(BODY))
    play = method(body, "private void playPageant(int gen, List<BodyGestures.Frame> frames)")
    still = 'if (!"running".equals(phase) || !"pageant".equals(active)) return;'
    started = play.index('record("pageant", "started", null);')
    assert play.index("host.ensureArm();") < play.rindex(still, 0, started) < started
    gate = play.index("synchronized (armGate) {")
    assert gate < play.index(still, gate) < play.index("pageantPlayAt = SystemClock.elapsedRealtime();") \
        < play.index("arm.playFrames(run,")


def test_the_lowering_is_its_own_number(pageant):
    """Review 23 Nov: LOWER_MS was `= HEAD_FIRST_MS`, so slowing the head's first turn (3 -> 4.5 s) would have
    slowed the arm's way down too. It stays the 3 s it was played at on 10 Nov (the elbow 1940 -> 1300 in it)."""
    assert pageant["lower_ms"] == "3000" != pageant["head_first_ms"]
    source = code_only(read(PAGEANT))
    assert "LOWER_MS = HEAD_FIRST_MS" not in source
    # and the lowering is checked at play time with the run (a head this run never sent: the blind rule)
    assert "checkFrames(Collections.singletonList(lower()))" in method(source, "static String check(List<BodyGestures.Frame> frames)")
    assert pageant["f_lower"].startswith("3000 ")


# The fastest each channel has moved in a pageant frame whose start is known (every frame after the first --
# the first leaves the unmeasured rests). 10 Nov's raise was played on the robot (standing, 15:54 and 19:44):
# ch2 0.167 us/ms (a stroke, 200 us / 1.2 s), ch3 0.213 (the lowering, 640 us / 3 s), ch4 0.1 (a stroke),
# ch5 0.1 (its third frame, 120 us / 1.2 s), ch6 0.133 (its second, 200 us / 1.5 s), ch7 0.1 (a turn).
# 23 Nov (review): ch6 0.167 (the second frame 1.2 s -- tonight's first cut 0.9 s was 0.222) and ch5/ch3 0.15
# (the third frame 0.8 s, was 1.2): faster than 10 Nov, not yet played on the robot -- pinned here so a further
# speed-up is a decision, not an edit.
MAX_US_PER_MS = {2: 200 / 1200, 3: 640 / 3000, 4: 120 / 1200, 5: 120 / 800, 6: 200 / 1200, 7: 200 / 2000}


def _frames(pageant: dict[str, str]) -> list[tuple[int, dict[int, int]]]:
    out, i = [], 0
    while f"f{i}" in pageant:
        ms, _, targets = pageant[f"f{i}"].partition(" ")
        pairs = [kv.split("=") for kv in targets.strip("{}").split(", ")]
        out.append((int(ms), {int(k): int(v) for k, v in pairs}))
        i += 1
    ms, _, targets = pageant["f_lower"].partition(" ")
    out.append((int(ms), {int(k): int(v) for k, v in (kv.split("=") for kv in targets.strip("{}").split(", "))}))
    return out


def test_no_joint_moves_faster_than_its_pinned_rate(pageant):
    frames = _frames(pageant)
    assert len(frames) == int(pageant["frames"]) + 1
    # starts: the rests the 15 Oct scan read as ~1500 (wrist, upper arm, swing); the elbow's and ch5's rests are
    # unmeasured ("below 1300", "far below 1300") and the head's start is the blind rule's (check()), so a
    # channel's rate counts from its first frame on
    at: dict[int, int] = {2: 1500, 4: 1500, 6: 1500}
    fastest: dict[int, float] = {}
    for ms, targets in frames:
        for ch, pulse in targets.items():
            if ch in at:
                fastest[ch] = max(fastest.get(ch, 0.0), abs(pulse - at[ch]) / ms)
            at[ch] = pulse
    for ch, rate in fastest.items():
        assert rate <= MAX_US_PER_MS[ch] + 1e-9, (ch, rate)
    assert fastest[6] == pytest.approx(200 / 1200), "the second raise frame: ch6 1500 -> 1300"


def test_the_server_lead_is_the_raise_s_first_two_stages(pageant):
    """EVENT_PAGEANT_LEAD_S (owner 11 Nov "เหมือนมือจะขึ้นช้ากว่าเสียงที่พูด"): her line waits from the app's
    "started" until the arm is out and the forearm up -- the raise's first two stages (4.5 s of the 6.9 s
    raise until 23 Nov, 2.7 s of the 4.3 s one since its review -- 2.4 with tonight's first-cut 0.9 s second
    frame); the last two land on her "Hello everyone". robot.env does not set it, so the default in
    app/config.py is what the robot runs."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = (root / "app" / "config.py").read_text(encoding="utf-8")
    default = re.search(r'os\.getenv\("EVENT_PAGEANT_LEAD_S", "([0-9.]+)"\)', config)
    assert default and round(float(default.group(1)) * 1000) == int(pageant["lead_ms"]) == 2700
    robot_env = (root / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*EVENT_PAGEANT_LEAD_S\s*=", robot_env)


def test_the_head_turns_gently_and_seldom(pageant):
    """"อย่าหันหัวไวเกินและถี่เกิน" (owner 4 Nov): +-100 us (HeadLook's 50 %, inside the +-60 % it ran
    without a stop), 2 s for a turn (10 ms per us -- a call's look moves at 4), one turn every 5.6 s:
    six in its 40.4 s, ~9 a minute (the walk scan the owner stopped on 4 Nov was ~37). The first turn is
    the head's blind first move -- 4.5 s, 15 ms per us of its worst travel, as slow as the centring it
    replaced (review 23 Nov; tonight's first cut had 3 s)."""
    assert pageant["head_path"] == "1600@4500 1400@2000 " + " ".join(["1600@2000 1400@2000"] * 2)
    assert pageant["head_moves"] == "5"
    # down: the elbow straight and the arm still 45 deg out (ch5 1300, its lowest allowed value);
    # group 99 brings it in
    assert pageant["lower"] == "3000 {2=1500, 3=1300, 4=1500, 5=1300, 6=1500, 7=1500}"


def test_anything_outside_the_pageant_box_is_refused_at_play_time(pageant):
    # ch5 since the owner's video, only as high as it was driven (1460); the elbow to 1940 likewise
    assert pageant["bad.ch5"] == "arm_range" and pageant["ok.ch5"] == "null" and pageant["bad.ch8"] == "channel_8"
    assert pageant["ok.elbow_high"] == "null" and pageant["bad.upper_high"] == "arm_range"
    assert pageant["bad.ch13"] == "channel_13"
    assert pageant["bad.arm_high"] == "arm_range" and pageant["bad.arm_low"] == "arm_range"
    # ch6 down to 1220 (driven on camera 15:40 with the arm out, moving at each step), no lower
    assert pageant["bad.swing_low"] == "arm_range" and pageant["ok.swing_high"] == "null"
    assert pageant["bad.upper_low"] == "arm_range", "only ch6's lower clamp is wider"
    assert pageant["bad.head_wide"] == "head_range"
    assert pageant["bad.head_fast"] == "head_fast", "1400 (where the 6 sides end) -> 1600 in 1.5 s is faster than 10 ms/us"
    assert pageant["bad.head_first_fast"] == "head_first_fast", "from an unknown pose: the slow move"
    assert pageant["bad.frame_short"] == "frame_ms" and pageant["bad.empty"] == "empty"
    assert pageant["bad.too_long"] == "too_long"


def test_only_the_emma_event_app_plays_it_and_every_other_gate_holds():
    body = code_only(read(BODY))
    req = method(body, "synchronized String requestPageant()")
    # the event app first, then the voice gestures' own gates in their order, then the clamps again
    assert req.index('if (!host.pageantApp()) return ack(false, "pageant", "not_event_app"') \
        < req.index("BodyGestures.refusal(enabled(), host.operatorVisible(), host.foreground(),")
    assert "host.navBlock(), latched(now)" in req, "a walk THIS app runs still refuses it"
    assert "now - lastPageantAt < Pageant.MIN_GAP_MS" in req
    # busy unless IDLE (review 9 Nov): a speech beat "holding" frees busy with its arm still out
    assert 'busy.get() || !"idle".equals(phase)' in req
    assert "Pageant.check(Pageant.sequence()) != null" in req
    assert req.index("busy.compareAndSet(false, true)") < req.index('phase = "running";')
    end = method(body, "String endPageant()")
    assert 'if (!host.pageantApp()) return ack(false, "pageant_end", "not_event_app"' in end
    assert 'pageantLower(gen, "server");' in end and "stopArm" not in end, "the end lowers, never a stop"
    # its ack tells the server whether a pageant is still up: est null = none, a number = coming down
    assert 'if (!"pageant".equals(active)) return ack(true, "pageant_end", null, null, -1);' in end
    assert 'if (!"running".equals(phase)) return ack(true, "pageant_end", null, null, Pageant.LOWER_MS);' in end
    # BodyGestures' own id table never learns it: move_body / emma-body:<id> cannot ask for it
    assert '"pageant"' not in code_only(read(GESTURES))
    # the caps say so only in Emma Event and Emma Ceremony (owner 23 Nov: the debut is the ceremony's first step), and
    # MainActivity answers by the package -- those two packages, never the sales app
    assert '.put("pageant", host.pageantApp())' in method(body, "String list()")
    main = code_only(read(MAIN))
    assert "@Override public boolean pageantApp() { return EmmaFace.playsPageant(MainActivity.this); }" in main
    face = code_only(read(FACE))
    plays = method(face, "static boolean playsPageant(Context context)")
    assert "return isEventApp(context) || isCeremonyApp(context);" in plays
    assert method(face, "static boolean isEventApp(Context context)").count("EVENT_PACKAGE") == 1
    assert method(face, "static boolean isCeremonyApp(Context context)").count("CEREMONY_PACKAGE") == 1
    assert 'static final String ROBOT_PACKAGE = "com.emma.robot.control";' in face and "ROBOT_PACKAGE" not in plays
    assert "return start ? body.requestPageant() : body.endPageant();" in main


def test_the_bridge_knows_two_fixed_messages_and_no_shape():
    face = code_only(read(FACE))
    assert 'case "emma-body:pageant:event":\n                return bridge.bodyPageant(true);' in face
    assert 'case "emma-body:pageant_end:event":\n                return bridge.bodyPageant(false);' in face
    # the generic emma-body:<id> pattern cannot reach it: ":event" is no id character
    assert 'Pattern.compile("emma-body:([a-z][a-z0-9_]{1,31})")' in face


def test_it_comes_down_slowly_once_and_owns_the_head_until_rested():
    body = code_only(read(BODY))
    play = method(body, "private void playPageant(int gen, List<BodyGestures.Frame> frames)")
    assert "String bad = Pageant.check(frames);" in play and 'finish(gen, "failed", "needs_mapping");' in play
    assert play.index("later(gen, () -> {") < play.index("arm.playFrames(run,")
    assert '() -> pageantLower(gen, "max_ms")' in play
    assert "headKnown = false;" in play and "headLost();" in play
    low = method(body, "private void pageantLower(int gen, String why)")
    assert 'if (gen != generation.get() || !"running".equals(phase) || !"pageant".equals(active)) return;' in low
    assert low.index("synchronized (this) {") < low.index('phase = "lowering";') < low.index("io.execute(")
    assert "Pageant.lower()" in low
    assert low.index('restReturn(gen, "pageant " + why + ", lowering overran", "lowering")') < low.index("arm.playFrames(")
    assert "BodyGestures.REST_AFTER_FRAMES_MS)" in low
    # a look is refused while it runs (the app's own head clock would fight it)
    assert 'if (BodyGestures.isHead(id) || "pageant".equals(id)) return true;' in method(body, "private boolean headBusy()")


def test_the_hand_never_crosses_the_face(pageant):
    """10 Nov on camera: ch4 1500 with the arm forward-up = the hand in front of her face. With the
    arm out to the side and up (ch5 1420, ch6 1220) ch4 1540 put the hand close beside the head: the
    wave turns it 1400 <-> 1520, never past."""
    path = [int(v) for v in pageant["upper_path"].split()]
    assert path[:2] == [1380, 1460] and set(path[2:]) == {1400, 1520} and max(path) <= 1520


def test_standing_she_waves_a_whole_turn_each_way(pageant):
    """app/event_pageant.MIN_STANDING_S holds the hand up for one turn each way however short her line
    (review 10 Nov: the two-stage raise made that 13.6 s; 13.0 lowered mid-stroke; the side pose's
    raise makes it 15.7 s, its slower wave 16.9 s, the higher raise of 15:40 18.1 s; 23 Nov's first cut 16.2 s;
    its review's 4.3 s raise and 4.5 s blind first turn 18.0 s, MIN_STANDING_S 18.6 -- B2) -- and no more than
    1 s over it: later the hand comes down in the middle of the third side's turn (18.0 -> 20.0 s), the
    mid-stroke end 10 Nov's review took out."""
    from app import event_pageant

    two_sides = int(pageant["two_sides_ms"])
    assert two_sides == 18000
    assert two_sides <= event_pageant.MIN_STANDING_S * 1000 <= two_sides + 1000


def test_the_turn_and_the_wave_hold_the_l_pose(pageant):
    """Review 10 Nov: only the raise and ch4's range were pinned -- a wrist turned to the back of the
    hand on a stroke passed every test. Owner 15:10 "ยังไม่เห็นโบกอะไรเลย": every frame after the raise
    MOVES -- the turn turns the forearm out (upper arm 1400, wrist 1440) as the head goes, then in /
    out / in; the palm out to the room (1440-1640, never the back of the hand at 1180-1260). The first
    turn is 4.5 s since 23 Nov's review (the head's blind first move), the next ones 2 s."""
    assert pageant["frame4"] == "4500 {2=1440, 3=1940, 4=1400, 5=1420, 6=1220, 7=1600}"
    assert pageant["frame5"] == "1200 {2=1640, 4=1520}"
    assert pageant["frame6"] == "1200 {2=1440, 4=1400}"
    assert pageant["frame7"] == "1200 {2=1640, 4=1520}"
    assert pageant["frame8"] == "2000 {2=1440, 3=1940, 4=1400, 5=1420, 6=1220, 7=1400}"


def test_no_wave_stroke_stands_still(pageant):
    """Owner 10 Nov 15:10: "ยังไม่เห็นโบกอะไรเลย เห็นแค่ยกขึ้น" -- each side opened with a stroke to the
    pose the arm already held. Consecutive frames after the raise always turn the upper arm and the
    wrist."""
    upper = [int(v) for v in pageant["upper_path"].split()][2:]          # every frame after the raise
    wrists = [int(v) for v in pageant["wrist_path"].split()][4:]
    assert len(upper) == len(wrists) == 24 and -1 not in wrists          # a frame without the wrist (6 sides)
    assert all(a != b for a, b in zip(upper, upper[1:])) and all(a != b for a, b in zip(wrists, wrists[1:]))


def test_the_shoulder_and_elbow_hold_where_the_raise_left_them(pageant):
    """Owner 15:45 "ล็อคไหล่ไว้ที่ความสูงนั้น ขยับต้นแขนกับข้อมือดู": after the raise the shoulder (ch5,
    ch6) and the elbow are only ever restated at the raise's top -- the hand stays at the height the
    owner chose and the wave is the upper arm and the wrist."""
    elbows = [int(v) for v in pageant["elbow_path"].split()]
    assert set(elbows[4:]) == {-1, 1940}
    shoulder = pageant["shoulder_path"].split()[4:]
    assert set(shoulder) == {"1420/1220", "null/null"}


def test_an_end_during_the_first_raise_frame_is_group_99():
    """Review 10 Nov: ch5 leaves its rest (far below 1300) in the raise's first frame; the lowering
    frame's ch5 1300 would swing an early-ended arm OUT. Inside RAISE_MS of the frames going to the
    board, the end is group 99 (resetHome ends the frame run and takes every target back)."""
    body = BODY.read_text(encoding="utf-8")
    lower = method(body, "private void pageantLower(int gen, String why)")
    assert "SystemClock.elapsedRealtime() - played < Pageant.RAISE_MS" in lower
    assert lower.index("Pageant.RAISE_MS") < lower.index("arm.playFrames(frames")
    early = lower[lower.index("Pageant.RAISE_MS"):lower.index("later(gen, () -> restReturn")]
    assert 'restReturn(gen, "pageant " + why + " during the raise -- group 99", "lowering");' in early
    assert "return;" in early
    play = method(body, "private void playPageant(int gen, List<BodyGestures.Frame> frames)")
    assert play.index("pageantPlayAt = SystemClock.elapsedRealtime();") < play.index("arm.playFrames(run")
    request = method(body, "synchronized String requestPageant()")
    assert "pageantPlayAt = 0L;" in request


def test_started_is_reported_when_the_frames_go_not_at_the_request():
    """Review 10 Nov: the server counts the standing minimum (MIN_STANDING_S, 0.6 s over two sides)
    from the app's "started"; recorded at the request it came before the arm connect, which on the
    first press after the app starts takes seconds, and the hand came down before the second side."""
    body = code_only(read(BODY))
    req = method(body, "synchronized String requestPageant()")
    assert 'record("pageant", "started"' not in req
    play = method(body, "private void playPageant(int gen, List<BodyGestures.Frame> frames)")
    assert play.index("host.ensureArm();") < play.index("Pageant.check(frames)") \
        < play.index('record("pageant", "started", null);') < play.index("arm.playFrames(run,")
    # inside the same synchronized (this) block as the stale check (no block closes between them; since 23 Nov's
    # review the "still the running pageant" check sits in it too, so a fixed-width window no longer reached)
    at = play.index('record("pageant", "started", null);')
    lock = play.rindex("synchronized (this) {", 0, at)
    started = play[lock + len("synchronized (this) {"):at]
    assert "if (stale(gen)) return;" in started and "}" not in started
