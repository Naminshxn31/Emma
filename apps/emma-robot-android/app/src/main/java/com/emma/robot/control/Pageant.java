package com.emma.robot.control;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * ท่านางงาม -- the Emma Event debut's beauty-queen walk (owner 9 Nov 2026: "ขอปุ่มกดท่าเปิดตัวหุ่น
 * ขอเป็นท่านางงามเลยได้ไหม ที่เดินแล้วหันหน้าโบกมือไปมา", then, asked: "โบกไปด้วยระหว่างเดิน" and
 * "ให้ทำไปก่อนเลย ... เพราะเรามีข้อมูลแต่ละส่วนแล้ว แล้วก็ท่านี้จะใช้เฉพาะโหมดนี้แก้กฏได้ เพราะถ้าจบงาน
 * ก็กลับไปโหมดเดิม").
 *
 * The right arm comes up out to the side and waves, the head turns to one side of the room and then the
 * other, while the base walks -- the one gesture allowed while the wheels turn, and only in the Emma
 * Event app (BodyController.requestPageant; everywhere else the arm stays rested through a walk, and
 * the head does not pan on one -- HeadLook.walkSafe). The server says only "start" and "end"
 * (`emma-body:pageant:event`, `emma-body:pageant_end:event`); every pulse, channel and time is here.
 *
 * First built from the 15 Oct camera sweep alone; on the robot 10 Nov it was the forearm held out
 * at the waist like a handshake -- the hand never came above the belt (owner: "ท่าไม่เหมือนนะ", then
 * "ให้ดูจากกล้อง ... ว่าหุ่นมือขึ้นมาถึงไหน ... ปรับท่าโบกมือทักทายก่อน"). Rebuilt that afternoon with
 * the PC camera on the robot, one channel at a time through the operator screen (3 s moves): the
 * raise below is the path that was driven, in its order, and nothing touched the body or the screen.
 * Then rebuilt three more times against the owner's references (photos, a video, then the owner
 * at the robot 15:40): the pose below.
 *   ch2 right wrist turn, rest ~1500: with the arm up out to the side, 1180-1260 = the back of the
 *       hand to the room, 1340 = edge-on, 1500-1580 = the open palm to the room
 *   ch3 right elbow: more = bent more, 1700 = the forearm up with the arm down; driven to 1940 with
 *       the arm out (moving at every 40); rest below 1300 (straight)
 *   ch4 right upper-arm turn: with the arm forward-up, 1500 = the hand in front of the face; with the
 *       arm out at ch5 1420 / ch6 1220 / elbow 1940 more = the forearm turned in: 1380 leans it out,
 *       1460 nearly upright, 1540 upright with the hand close beside the head
 *   ch5 right shoulder out to the side: 1300 = ~45 deg, 1460 near the shoulder's height; rest far
 *       below 1300
 *   ch6 right shoulder swing: lower = forward-up (1300 with the arm out stands the bent forearm up;
 *       1260 / 1220 lift the hand clearly above the head, moving at both, no stall); 1700 = behind;
 *       rest ~1500
 *   ch7 head turn: more = the robot's LEFT
 * ch5 (the shoulder out to the side) since 10 Nov's video: its rest is far below 1300, so every
 * allowed value is the arm at least ~45 deg out -- ON A WALK THE ARM STICKS OUT TO THE RIGHT, keep
 * the robot's right side clear. Not ch8 (the head tilt that stalled and burned on 12 Sep), not the
 * fingers, not the left arm.
 *
 * Free of android.* and org.json on purpose: tests/test_pageant_app.py compiles and runs it with
 * the plain JDK (with BodyGestures and HeadLook, free of them too -- check() reads HeadLook's clamp).
 */
final class Pageant {
    private Pageant() {}

    static final int WRIST = 2;
    static final int ELBOW = 3;
    static final int UPPER_ARM = 4;
    static final int SHOULDER_OUT = 5;
    static final int SHOULDER_SWING = 6;
    static final int HEAD_TURN = BodyGestures.HEAD_TURN;
    /** The only channels a pageant frame may drive. */
    static final int[] CHANNELS = {WRIST, ELBOW, UPPER_ARM, SHOULDER_OUT, SHOULDER_SWING, HEAD_TURN};
    /** This pose's own clamps, differing from BodyGestures' 1300-1700 only where 10 Nov's camera
     *  session drove them with no stall: the elbow up to 1940 (moving at every step to there), ch5 only
     *  up to 1460 (narrower: driven to 1500/1540 too, where the forearm leans out instead of up -- no
     *  frame wants it there), ch6 down to 1220 (driven 1300 -> 1260 -> 1220 with the arm out, moving
     *  at each). Every other lower clamp is 1300. */
    static final int ELBOW_LIMIT_MAX = 1940;
    static final int OUT_LIMIT_MAX = 1460;
    static final int SWING_LIMIT_MIN = 1220;

    /** The head turns this far each way: HeadLook's 50 %. HeadLook's pan ran +-60 % both ways on
     *  4 Nov without a stop (docs/robot-servo-limits-2026-10-31.md). ~18 deg at ~0.37 deg/%. */
    static final int HEAD_SWING_US = 100;
    /** "อย่าหันหัวไวเกินและถี่เกิน" (owner 4 Nov, after ch8 smoked on 12 Sep): a head move here takes
     *  at least this many ms per microsecond -- 10, where a call's look moves at 4
     *  (HeadLook.MS_PER_US). 200 us takes 2 s. */
    static final int HEAD_MS_PER_US = 10;

    /** A head move from where nobody knows (after group 99, at app start -- only HeadLook's clamp,
     *  LIMIT_MIN..LIMIT_MAX = 1300..1700, bounds where a look left it) takes at least this many ms per
     *  microsecond of its WORST travel, from whichever end of that clamp is farther: the slowest the
     *  pageant's head ever ran blind. Until 23 Nov the raise's first frame centred it -- at most 200 us
     *  in 3000 ms = 15 ms/us. Review 23 Nov: tonight's first cut made the first turn the blind move,
     *  up to 300 us (1300 -> 1600) in 3000 ms = 10 ms/us, faster than it had ever run blind (and
     *  BodyGestures.HEAD_PREFIX_MS: "the blind first move is the slowest one available"). check()
     *  computes the travel, so no constant can make the blind move faster than this. */
    static final int HEAD_BLIND_MS_PER_US = 15;
    /** The head's first move since 23 Nov is the first turn (to 1600: the hand no longer waits for a
     *  centring in the raise, owner "ยกมือช้าไปหน่อย"): 300 us from the far end of HeadLook's clamp (1300)
     *  at HEAD_BLIND_MS_PER_US = 4500 ms -- the old blind rate, not tonight's first cut (3000 ms,
     *  10 ms/us). A shorter value fails check() and plays nothing. */
    static final int HEAD_FIRST_MS = 4500;
    /** The raise's first frame: the arm out to the side from its rest (the longest travel of the
     *  raise -- ch5 from far below 1300). 3000 ms until 23 Nov only because the head centred in it;
     *  owner that night at the ceremony's opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม"
     *  (the raise was 6.9 s). Half of it, still the slowest frame of the raise; an end inside it is
     *  group 99 (BodyController.pageantLower). */
    static final int RAISE_MS = 1500;
    /** A turn of the head to one side, the hand held up in the L pose (from the other side, 200 us:
     *  HEAD_MS_PER_US exactly). The first side's turn is HEAD_FIRST_MS. */
    static final int TURN_MS = 2000;
    /** One stroke of the wave. BodyGestures.MIN_FRAME_MS is 800. */
    static final int WAVE_MS = 1200;
    /** Strokes after each turn: in, out, in (the turn itself is the first "out" -- owner 10 Nov 15:10
     *  "ยังไม่เห็นโบกอะไรเลย เห็นแค่ยกขึ้น": the side used to start with an "in" stroke to where the arm
     *  already was, so a side had one sway in 5 s). */
    static final int STROKES = 3;
    /** The way down before group 99: one frame, slower than a recorded pose's
     *  (BodyGestures.ARM_LOWER_MIN_MS), at the length it was played on the robot (10 Nov 19:44, the
     *  hand down on line_done) -- the elbow's 1940 -> 1300 in it is the longest travel of the whole
     *  run (640 us). Its own number since review 23 Nov: it was `= HEAD_FIRST_MS`, so slowing the
     *  head's first turn would have changed how the arm comes down. Played by the server's end after
     *  the first raise frame, the run's own end (MAX_MS) and the watchdog -- never by a red stop (that
     *  freezes the arm where it is: stopArm) and never inside the first raise frame (group 99,
     *  BodyController.pageantLower). An end in the raise's later frames finds a head this run never
     *  sent: centring it from either end of HeadLook's clamp is 200 us, and at HEAD_BLIND_MS_PER_US
     *  that is these 3000 ms exactly -- check() holds the lowering to it at every play. */
    static final int LOWER_MS = 3000;
    /** The longest the hand stays up: the app ends it by itself (a server that died, a walk that
     *  never ended). Holding the arm out is new for these servos: the handshake held ~5 s, her
     *  talking arm 1.5 s. A walk longer than this arrives with the arm down (the server then asks
     *  for the pageant again standing, the greeting group if refused -- event_host). A guess to
     *  watch, not a measurement. */
    static final int MAX_MS = 45000;
    /** Two starts no closer than this (a press repeated, a page re-sending). */
    static final long MIN_GAP_MS = 10000L;

    /** The raise's later stages (10 Nov's order, driven on the robot): 1500 / 1200 / 1200 until 23 Nov
     *  (the owner, above: faster). The second moves ch6 1500 -> 1300 and the elbow 1660 -> 1820; tonight's
     *  first cut played it in 900 ms -- review 23 Nov: ch6 is the joint the 10 Nov camera found very
     *  sensitive (docs/robot-arm-command-map-2026-09-16.md: ~80 us ~ 45 deg), and 200 us in 900 ms
     *  (0.22 us/ms) was faster than anything ever played on it (10 Nov's raise: 1500 ms, 0.13). 1200 ms
     *  here (0.17 us/ms, still faster than 10 Nov -- not yet seen on the robot). The raise is
     *  1.5 + 1.2 + 0.8 + 0.8 = 4.3 s (was 6.9), the last two at BodyGestures.MIN_FRAME_MS -- the speech
     *  gestures' arm frames already run at 800-1500 ms (BodyGestures.STUDIO_FRAME_MS). These three move
     *  a joint 200 us at most (ch6 in the second; 120 and 80 in the last two); the long travel from
     *  rest is the first frame's. */
    static final int RAISE_UP_MS = 1200;
    static final int RAISE_TOP_MS = 800;
    static final int RAISE_HIGH_MS = 800;

    /** The pose: owner 10 Nov, a video of a hand on the robot's own arm holding it "แบบนี้ลองสั่งดู" --
     *  the upper arm OUT TO THE SIDE, the elbow bent, the forearm upright, the palm to the room, and
     *  the wave a small tilt of the forearm left-right (the three photos of a crowned queen were the
     *  same). Driven on the robot that afternoon with the PC camera in front of it:
     *  - ch5 out to the side with the elbow bent sends the forearm FORWARD, and ch4 cannot turn it up
     *    (it stops moving near 1220) -- so the arm also swings forward a little (ch6 1300), which
     *    stands the bent forearm up;
     *  - ch3 (elbow) far past the 15 Oct sweep: 1820 / 1900 / 1940 still moving, no stall;
     *  - measured on camera at 5=1460 3=1940 4=1380 6=1300: forearm ~13 deg off upright (outward),
     *    the hand above the head and away from it, the palm to the front; the elbow ~35 deg under
     *    the shoulder's height (5 higher leans the forearm out instead);
     *  - the palm OUT to the room (owner 15:05 "เหลือหันมือออกมา"): the wrist 1540-1580 shows the open
     *    palm to the camera in front (1180-1260 the back of the hand, 1340 edge-on);
     *  - 15:40, owner "ยกสูงกว่านี้ได้ไหม" then "พับเข้ามานิดหน่อย แต่ยกสูงพอแล้ว": the arm further
     *    forward-up (ch6 1220: the hand clearly above the head), then less far out (ch5 1420: the
     *    elbow lower, the hand beside the head), then "หมุนเข้ามานิดนึง": the upper arm turned in
     *    (ch4 1460: the forearm nearly upright; 1540 upright but the hand close beside the head);
     *  - the wave: owner 15:45 "ล็อคไหล่ไว้ที่ความสูงนั้น ขยับต้นแขนกับข้อมือ" -- the shoulder (ch5, ch6)
     *    and the elbow stay where the raise left them; the upper arm turns 1400 <-> 1520 (the
     *    forearm tilting out and back) with the wrist 1440 <-> 1640 (1420-1660 driven at ch4 1460:
     *    the palm to the room throughout, turning). The elbow wave before it (1780 <-> 1940) bent
     *    the arm down and up; this one keeps the hand at the height the owner chose. */
    static final int ELBOW_HALF = 1660;
    static final int ELBOW_UP = 1820;
    static final int ELBOW_L = 1940;
    static final int WRIST_PALM = 1540;
    static final int WRIST_OUT = 1440;
    static final int WRIST_IN = 1640;
    /** ch4 on the way up (the raise as driven), held (the owner's "a little in"), and the wave's two
     *  ends -- never past 1520: 1540 put the hand close beside the head on camera. */
    static final int UPPER_RAISE = 1380;
    static final int UPPER_L = 1460;
    static final int UPPER_OUT = 1400;
    static final int UPPER_IN = 1520;
    /** ch5: 1300 the arm ~45 deg out, 1460 near the shoulder's height (the lowest allowed value is the
     *  arm well out: its rest is far below 1300, group 99 brings it in). */
    static final int OUT_HALF = 1300;
    static final int OUT_L = 1420;
    /** ch6 on the way up (the forearm stood up) and held (the hand above the head). */
    static final int SWING_UP = 1300;
    static final int SWING_L = 1220;
    /** The way down: the measured rests (wrist, upper arm, swing ~1500), the elbow as straight as
     *  the clamp allows and the arm still 45 deg out (ch5 1300, the lowest it may be sent); group 99
     *  finishes it. */
    static final int ELBOW_DOWN = 1300;

    /** Up in four stages: the arm out to the side with the elbow half bent (the slowest of the four),
     *  the arm forward a little and the forearm up -- the order driven on 10 Nov -- then the
     *  elbow fully bent with the arm out to where it stays, then the arm further forward-up (the hand
     *  above the head) with the upper arm turned a little in. The last two reach the pose the owner
     *  chose in another order than it was driven (there: ch6 1220 with ch5 at 1540, then ch5 down,
     *  then ch4 alone); their in-between poses were not held on camera -- the whole raise was played
     *  from the app at 15:54 and touched nothing (ch4 is still 1380 there, the forearm leaning out,
     *  away from the face). No head in it since 23 Nov: the head's first move is the first turn
     *  (HEAD_FIRST_MS), so the hand is up 2.6 s sooner (4.3 s, was 6.9) and the head no faster per
     *  microsecond than it ran blind before. These timings have not been played on the robot yet (the
     *  poses are 10 Nov's). */
    static List<BodyGestures.Frame> raise() {
        ArrayList<BodyGestures.Frame> out = new ArrayList<>();
        out.add(BodyGestures.frame(RAISE_MS, WRIST, WRIST_PALM, ELBOW, ELBOW_HALF, UPPER_ARM, UPPER_RAISE,
                SHOULDER_OUT, OUT_HALF));
        out.add(BodyGestures.frame(RAISE_UP_MS, ELBOW, ELBOW_UP, SHOULDER_SWING, SWING_UP));
        out.add(BodyGestures.frame(RAISE_TOP_MS, ELBOW, ELBOW_L, SHOULDER_OUT, OUT_L));
        out.add(BodyGestures.frame(RAISE_HIGH_MS, SHOULDER_SWING, SWING_L, UPPER_ARM, UPPER_L));
        return out;
    }

    /** +1 = to the robot's left, -1 = to its right; the forearm tilts out on the way (the wave never
     *  stops for a turn of the head). The shoulder and the elbow are restated where the raise left
     *  them: the wave never moves them. In `ms`: TURN_MS from the last side, HEAD_FIRST_MS for the
     *  first (the head's first move since the raise left it alone). One signature only (review 23 Nov:
     *  the 1-arg turn defaulting to TURN_MS was called by nothing). */
    static BodyGestures.Frame turn(int side, int ms) {
        int head = BodyGestures.CENTRE + Integer.signum(side) * HEAD_SWING_US;
        return BodyGestures.frame(ms, HEAD_TURN, head, WRIST, WRIST_OUT, UPPER_ARM, UPPER_OUT,
                ELBOW, ELBOW_L, SHOULDER_OUT, OUT_L, SHOULDER_SWING, SWING_L);
    }

    /** The wave: the upper arm turns the forearm out and back with a turn of the wrist, the palm to
     *  the room, the shoulder and the elbow still. */
    static BodyGestures.Frame stroke(boolean out) {
        return out
                ? BodyGestures.frame(WAVE_MS, UPPER_ARM, UPPER_OUT, WRIST, WRIST_OUT)
                : BodyGestures.frame(WAVE_MS, UPPER_ARM, UPPER_IN, WRIST, WRIST_IN);
    }

    static BodyGestures.Frame lower() {
        return BodyGestures.frame(LOWER_MS, WRIST, 1500, ELBOW, ELBOW_DOWN, UPPER_ARM, 1500,
                SHOULDER_OUT, OUT_HALF, SHOULDER_SWING, 1500, HEAD_TURN, BodyGestures.CENTRE);
    }

    /** A later side (its turn TURN_MS, from the side before): PageantTiming counts a side's frames
     *  with it. Not the first side -- that turn is the head's blind move (HEAD_FIRST_MS); sequence()
     *  builds it with side(side, HEAD_FIRST_MS), and check() refuses a first turn at TURN_MS. */
    static List<BodyGestures.Frame> side(int side) {
        return side(side, TURN_MS);
    }

    /** One side: the turn (in `turnMs`), then in-out-in. */
    static List<BodyGestures.Frame> side(int side, int turnMs) {
        ArrayList<BodyGestures.Frame> out = new ArrayList<>();
        out.add(turn(side, turnMs));
        for (int i = 0; i < STROKES; i++) out.add(stroke(i % 2 == 1));  // in, out, in: the turn was "out"
        return out;
    }

    /** The raise, then left and right sides for as long as MAX_MS holds whole sides -- the first
     *  side's turn is the head's first move (HEAD_FIRST_MS). The lowering is not in it: it is played
     *  when the run ends, however it ends (BodyController). */
    static List<BodyGestures.Frame> sequence() {
        ArrayList<BodyGestures.Frame> out = new ArrayList<>(raise());
        int total = BodyGestures.totalMs(out);
        int side = 1;
        boolean first = true;
        while (true) {
            List<BodyGestures.Frame> next = side(side, first ? HEAD_FIRST_MS : TURN_MS);
            first = false;
            int ms = BodyGestures.totalMs(next);
            if (total + ms > MAX_MS) break;
            out.addAll(next);
            total += ms;
            side = -side;
        }
        return Collections.unmodifiableList(out);
    }

    static boolean channelAllowed(int channel) {
        for (int each : CHANNELS) if (each == channel) return true;
        return false;
    }

    /**
     * null when every frame is inside the clamps, else why not. The head's start is unknown, so
     * the first frame that moves it is timed by its worst travel -- from whichever end of HeadLook's
     * clamp is farther -- at HEAD_BLIND_MS_PER_US (review 23 Nov: it was `>= HEAD_FIRST_MS`, a constant
     * that tonight's first cut had lowered with the travel unchanged, and no floor of HEAD_FIRST_MS on
     * top: the lowering centres an unsent head, 200 us, in its 3000 ms at that same rate); after that
     * each head move is timed from where the last one left it (HEAD_MS_PER_US). The lowering (played
     * when the run ends, BodyController.pageantLower) is checked on its own as well: a head nobody
     * sent this run. Checked at play time on the robot, not only by the tests: an edit that breaks a
     * clamp plays nothing.
     */
    static String check(List<BodyGestures.Frame> frames) {
        String bad = checkFrames(frames);
        if (bad != null) return bad;
        bad = checkFrames(Collections.singletonList(lower()));
        return bad == null ? null : "lower_" + bad;
    }

    private static String checkFrames(List<BodyGestures.Frame> frames) {
        if (frames == null || frames.isEmpty()) return "empty";
        int total = 0;
        Integer head = null;
        for (BodyGestures.Frame frame : frames) {
            if (frame.durationMs < BodyGestures.MIN_FRAME_MS || frame.durationMs > BodyGestures.MAX_FRAME_MS) {
                return "frame_ms";
            }
            total += frame.durationMs;
            for (Map.Entry<Integer, Integer> target : frame.targets.entrySet()) {
                int channel = target.getKey();
                int pulse = target.getValue();
                if (!channelAllowed(channel)) return "channel_" + channel;
                if (channel == HEAD_TURN) {
                    if (Math.abs(pulse - BodyGestures.CENTRE) > HEAD_SWING_US) return "head_range";
                    if (head == null) {
                        int worst = Math.max(Math.abs(pulse - HeadLook.LIMIT_MIN), Math.abs(pulse - HeadLook.LIMIT_MAX));
                        if (frame.durationMs < worst * HEAD_BLIND_MS_PER_US) return "head_first_fast";
                    } else if (frame.durationMs < Math.abs(pulse - head) * HEAD_MS_PER_US) {
                        return "head_fast";
                    }
                    head = pulse;
                } else if (pulse < (channel == SHOULDER_SWING ? SWING_LIMIT_MIN : BodyGestures.ARM_LIMIT_MIN)
                        || pulse > (channel == ELBOW ? ELBOW_LIMIT_MAX
                                    : channel == SHOULDER_OUT ? OUT_LIMIT_MAX : BodyGestures.ARM_LIMIT_MAX)) {
                    return "arm_range";
                }
            }
        }
        return total > MAX_MS + LOWER_MS ? "too_long" : null;
    }
}
