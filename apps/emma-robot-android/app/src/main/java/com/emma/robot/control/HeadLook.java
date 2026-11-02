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
        return clamp(CENTRE + Math.round(p * span / 100f));
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
        if (panPulse < LIMIT_MIN || panPulse > LIMIT_MAX || tiltPulse < LIMIT_MIN || tiltPulse > LIMIT_MAX) {
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
     *  and centring is the one head move that makes a walk safer, not busier. */
    static String refusal(boolean enabled, boolean operatorVisible, boolean foreground, String navBlock,
                          boolean latched, boolean headBusy, boolean tooSoon, boolean centre) {
        if (!enabled) return "disabled";
        if (operatorVisible) return "operator_screen";
        if (!foreground) return "not_foreground";
        if (navBlock != null && !centre) return "nav_unknown".equals(navBlock) ? "nav_unknown" : "navigating";
        if (latched) return "latched";
        if (headBusy) return "busy";
        if (tooSoon) return "too_soon";
        return null;
    }
}
