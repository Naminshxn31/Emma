package com.emma.robot.control;

/**
 * The head turning toward a person (owner 31 Oct 2026: "เวลาคุยอยากให้หุ่นลอง
 * หมุนหัวเพื่อหันหน้าไปคุยกับคน ... ก้มหน้าหรือขึ้นลง เพื่อจับหน้าคน").
 *
 * The server (app/head_follow.py) says where to look as a PERCENT of this
 * file's span -- `emma-body:look:<pan>:<tilt>`, each -100..100, + = the
 * robot's left / down -- and never a pulse. The pulses, the channels and the
 * hard clamps are here, in the robot's own code.
 *
 * Why the span is wider than BodyGestures' +-40 (which stays, for the voice
 * gestures): on 15 Oct the owner had ch7 and ch8 driven to 1300 and 1700
 * with a camera on the robot, twice, 60/60 commands without an error --
 * ch7 + = the robot's left, ch8 + = down (docs/robot-arm-command-map-
 * 2026-09-16.md, "หมายเลขช่องเซอร์โว"; the limits and their evidence are in
 * docs/robot-servo-limits-2026-10-31.md). +-40 is ~4 degrees: a head that
 * cannot turn toward anybody. Nothing outside the range those two sweeps
 * drove is ever sent. ch8 is the servo that stalled and burned on 12 Sep,
 * so every move is timed (never a jump) and the first one from an unknown
 * position is the slow one the owner measured with (T3000).
 *
 * Free of android.* and org.json on purpose: the tests compile and run it
 * with the plain JDK (tests/test_head_look_app.py).
 */
final class HeadLook {
    private HeadLook() {}

    static final int PAN = 7;
    static final int TILT = 8;
    static final int CENTRE = 1500;
    /** Pulses at 100%: the 15 Oct sweep's +-200, nothing further. */
    static final int PAN_SPAN_US = 200;
    static final int TILT_DOWN_SPAN_US = 200;
    static final int TILT_UP_SPAN_US = 200;
    /** The hard clamp every look is checked against at send time. */
    static final int LIMIT_MIN = 1300;
    static final int LIMIT_MAX = 1700;
    /** ch8's own, tighter (4 Nov 2026, the tilt ladder at the dock: 5 % steps, 3 s
     *  apart, the head camera's picture shift per step): the head STOPS going down
     *  at ~1545 (22-23 %) and up at ~1468 (~16 %) -- the ch8 board still takes
     *  1300-1700 (15 Oct), but past those the servo pushes on a head that no longer
     *  moves, which is how ch8 smoked on 12 Sep ("ค้างแล้วไม่รู้หันทางไหน"). The walk
     *  scan's 40 % (1580) and an 80 % try (1660) did exactly that the same day.
     *  So 1472-1540: a gear's slack short of each stop. ch7 ran to +-60 % both
     *  ways without one. */
    static final int TILT_LIMIT_MIN = 1472;
    static final int TILT_LIMIT_MAX = 1540;

    /** ms per microsecond of travel: 200 us takes 0.8 s. */
    static final int MS_PER_US = 4;
    static final int MIN_MS = 500;
    static final int MAX_MS = 3000;
    /** After a group, a stop or at start nobody knows where the head is: the
     *  slowest move, the owner's own T3000 (robot_arm.centre's rule). */
    static final int UNKNOWN_MS = 3000;
    /** At most one look this often (the server sends one per second). */
    static final long MIN_GAP_MS = 400L;
    /** No look for this long and the head is not at centre: back to centre,
     *  by the app itself -- a call that ended or a server that died must not
     *  leave the head turned. */
    static final long IDLE_CENTRE_MS = 10000L;

    static boolean isCentre(int panPulse, int tiltPulse) {
        return panPulse == CENTRE && tiltPulse == CENTRE;
    }
    /** How far a look may turn while the base WALKS (4 Nov 2026, owner: "หันตอนเดินไปด้วย
     *  ไม่ได้หรอ ให้มันสแกนไปด้วยว่ารอบมีอะไร ตำแหน่งถูกไหม"): the server sweeps the head so
     *  the walk pictures see round the robot. The head turns inside the body's footprint
     *  (the arms, which reach out of it, stay locked through a walk) and the base drives
     *  on the lidar, never on this camera. Down only: a nod lets the camera see what is
     *  below the lidar's plane (chair legs, 28 Oct); looking up shows nothing a walk needs.
     *  4 Nov afternoon, owner watching a survey: "ตอนเดินไม่เห็นก้มมองดูวัดพื้น หรือโต๊ะเลย
     *  ต้องเช็คให้ทั่วเลยนะ" -- then, minutes later: "อย่าหันหัวไวเกินและถี่เกิน
     *  เดี๋ยวมอเตอร์ที่หัวจะไหม้เหมือนเหตุการณ์เดิมได้" (12 Sep: ch8 smoked, stuck working
     *  without knowing where it was). So on a walk the head does NOT pan at all -- the
     *  BASE turns for the walk pictures (a survey's look stops) -- and nods down no
     *  further than the server's own cap for a call (HEAD_TILT_DOWN_MAX 18 % = 36 us,
     *  ~11 deg -- it was 40 % = 80 us until the same evening's dock ladder found the
     *  head stops at ~45 us: TILT_LIMIT_MAX), slowly (WALK_MIN_MS) and seldom
     *  (WALK_MIN_GAP_MS). */
    static final int WALK_PAN_US = 0;
    static final int WALK_TILT_DOWN_US = 36;
    /** A walk's look takes at least this long (36 us at MS_PER_US would be the
     *  0.5 s floor): the owner's "ไม่ไวเกิน". */
    static final int WALK_MIN_MS = 1500;
    /** And at most one this often on a walk (a call's follow: MIN_GAP_MS). */
    static final long WALK_MIN_GAP_MS = 2000L;

    /** A move's time on a walk: never under WALK_MIN_MS. */
    static int walkMs(String navBlock, int ms) {
        if (navBlock == null) return ms;
        return Math.max(ms, Math.min(MAX_MS, WALK_MIN_MS));
    }

    /** The gap a look keeps from the last one: longer on a walk. */
    static long minGapMs(String navBlock) {
        return navBlock == null ? MIN_GAP_MS : WALK_MIN_GAP_MS;
    }
    /** A look a walk may take: centre always (the walk's own "look ahead"); inside the
     *  walk box only while the app KNOWS the base walks ("navigating") -- "nav_unknown"
     *  is a blind poll, and a blind app turns nothing but back to centre. */
    static boolean walkSafe(String navBlock, int panPulse, int tiltPulse) {
        if (isCentre(panPulse, tiltPulse)) return true;
        return "navigating".equals(navBlock)
                && Math.abs(panPulse - CENTRE) <= WALK_PAN_US
                && tiltPulse >= CENTRE && tiltPulse - CENTRE <= WALK_TILT_DOWN_US;
    }

    static boolean valid(int pan, int tilt) {
        return pan >= -100 && pan <= 100 && tilt >= -100 && tilt <= 100;
    }

    /** + = the robot's left = ch7 up (15 Oct: "ค่ามาก = หันไปทางซ้ายของหุ่น"). */
    static int panPulse(int percent) {
        int p = Math.max(-100, Math.min(100, percent));
        return clamp(CENTRE + Math.round(p * PAN_SPAN_US / 100f));
    }

    /** + = down = ch8 up (15 Oct: "ค่ามาก = ก้ม"). */
    static int tiltPulse(int percent) {
        int p = Math.max(-100, Math.min(100, percent));
        int span = p >= 0 ? TILT_DOWN_SPAN_US : TILT_UP_SPAN_US;
        return Math.max(TILT_LIMIT_MIN, Math.min(TILT_LIMIT_MAX, clamp(CENTRE + Math.round(p * span / 100f))));
    }

    static int clamp(int pulse) {
        return Math.max(LIMIT_MIN, Math.min(LIMIT_MAX, pulse));
    }

    /** How long the move takes: the longer travel of the two channels at
     *  MS_PER_US, inside MIN_MS..MAX_MS; UNKNOWN_MS when the start is unknown. */
    static int durationMs(boolean known, int fromPan, int fromTilt, int toPan, int toTilt) {
        if (!known) return UNKNOWN_MS;
        int travel = Math.max(Math.abs(toPan - fromPan), Math.abs(toTilt - fromTilt));
        return Math.max(MIN_MS, Math.min(MAX_MS, travel * MS_PER_US));
    }

    /** Where the head is at `now`, from the last move: its target once its
     *  time is up, else on the straight line from a KNOWN start (the board
     *  interpolates linearly over T), else null = unknown. A look that comes
     *  mid-move then starts from an estimate, not from "unknown" -- or every
     *  look would take UNKNOWN_MS once the first one did. */
    static int[] position(boolean fromKnown, int fromPan, int fromTilt, int toPan, int toTilt,
                          long startMs, int durationMs, long now) {
        if (now >= startMs + durationMs) return new int[] {toPan, toTilt};
        if (!fromKnown) return null;
        float f = Math.max(0f, (now - startMs) / (float) durationMs);
        return new int[] {Math.round(fromPan + (toPan - fromPan) * f),
                Math.round(fromTilt + (toTilt - fromTilt) * f)};
    }

    /** The one board line a look sends. Refuses (null) anything outside the clamp. */
    static String command(int panPulse, int tiltPulse, int ms) {
        if (panPulse < LIMIT_MIN || panPulse > LIMIT_MAX || tiltPulse < TILT_LIMIT_MIN || tiltPulse > TILT_LIMIT_MAX) {
            return null;
        }
        if (ms < MIN_MS || ms > MAX_MS) return null;
        return "#" + PAN + "P" + panPulse + "#" + TILT + "P" + tiltPulse + "T" + ms;
    }

    /** First refusal in order, or null. The voice gestures' gates, minus the
     *  ones that are about a GUEST asking (direction_known, cooldown) -- nobody
     *  asks for a look; plus the arm being in a vendor group or a gesture
     *  that moves the head itself. A look back to CENTRE passes a walk (31 Oct, round 2):
     *  a head left turned when a walk starts would face sideways for all of
     *  it -- the walk pictures (WALK_FRAMES) and anyone watching it come --
     *  and centring is the one head move that makes a walk safer, not busier.
     *  Since 4 Nov a look inside the walk box passes a known walk too (`walkSafe`:
     *  the server's scan for the walk pictures). */
    static String refusal(boolean enabled, boolean operatorVisible, boolean foreground, String navBlock,
                          boolean latched, boolean headBusy, boolean tooSoon, boolean walkSafe) {
        if (!enabled) return "disabled";
        if (operatorVisible) return "operator_screen";
        if (!foreground) return "not_foreground";
        if (navBlock != null && !walkSafe) return "nav_unknown".equals(navBlock) ? "nav_unknown" : "navigating";
        if (latched) return "latched";
        if (headBusy) return "busy";
        if (tooSoon) return "too_soon";
        return null;
    }
}
