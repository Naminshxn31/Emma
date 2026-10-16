package com.emma.robot.control;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * The voice gesture vocabulary and its hard clamps (docs/emma-body-nav-design-2026-10-08.md §2c).
 *
 * The server only ever names an id; every number that moves a joint lives
 * here, compiled in. That is the show_unit(room) rule: the model picks WHICH
 * named thing, the machine that owns the hardware owns the numbers.
 *
 * Deliberately free of android.* and org.json: tests/test_emma_robot_body_nav_app.py
 * compiles this file with the plain JDK and runs the clamps and the refusal
 * order for real, instead of only reading the text.
 */
final class BodyGestures {
    private BodyGestures() {
    }

    static final String VERIFIED = "verified";
    static final String DIRECTION_KNOWN = "direction_known";
    static final String OPERATOR_MAPPED = "operator_mapped";
    static final String NEEDS_MAPPING = "needs_mapping";

    /** Same ids, same order as app/robot_contracts.BODY_ACTIONS: a test
     *  compares the two, because two hand-written copies of a list drift. */
    static final String[] IDS = {
            "head_up", "head_down", "head_left", "head_right", "head_center", "nod",
            "shake_head", "raise_left_arm", "raise_right_arm", "greet_arm", "wave", "handshake",
            "hands_left", "hands_right", "rest", "stop",
    };

    // ---- clamps (named, and read by the tests) --------------------------------
    static final long COOLDOWN_MS = 3000;
    static final int MAX_PER_MINUTE = 6;
    static final int MIN_FRAME_MS = 800;
    static final int MAX_FRAME_MS = 5000;
    static final int MAX_FRAMES = 6;
    static final int MAX_TOTAL_MS = 12000;
    static final long STOP_LATCH_MS = 60000;

    static final int HEAD_TURN = 7;
    static final int HEAD_TILT = 8;
    static final int CENTRE = 1500;
    /** ch7/ch8: ±40 around centre and nothing more. #8P1540 is the ONE head
     *  pulse the owner has measured (30 Sep: +40 = the camera looks down); the
     *  minus sides of ch7 and ch8 have never been driven, and ch8 is the servo
     *  that stalled and burned on 12 Sep. Widening this takes an APK build AND
     *  a written measurement cited here (docs/robot-servo-limits-*.md). */
    static final int HEAD_LIMIT_MIN = 1460;
    static final int HEAD_LIMIT_MAX = 1540;
    /** Every other channel: the same ±200 as the server's ROBOT_ARM_SPAN. */
    static final int ARM_LIMIT_MIN = 1300;
    static final int ARM_LIMIT_MAX = 1700;
    /** The only channels with camera evidence (servo-channel-map.json):
     *  5 = an arm moved, 7 = head turn, 8 = head tilt. Editable on the
     *  operator screen only. */
    static final int[] DEFAULT_VOICE_CHANNELS = {5, 7, 8};

    /** Head position cannot be read back after a group or at app start, so the
     *  first head move is the slowest frame a PoseFrame allows (the rule from
     *  robot_arm.centre: the blind first move is the slowest one available). */
    static final int HEAD_PREFIX_MS = 5000;
    static final int HEAD_RETURN_MS = 1200;
    static final int REST_AFTER_FRAMES_MS = 1000;

    static final int HANDSHAKE_GROUP = 6;     // verified: logcat 30 Sep + owner
    static final int GREET_GROUP = 3;         // verified: 11 Sep camera test
    static final int HANDSHAKE_HOLD_MS = 5000;
    static final int GREET_HOLD_MS = 1500;
    /** A lost #AGF must not leave the arm out: the rest-return still fires. */
    static final int GROUP_FALLBACK_MS = 30000;
    /** Group 99 answering #AGF is recorded at bench B1, not assumed: the
     *  rest-return ends on its #AGF or on this. */
    static final int REST_AGF_TIMEOUT_MS = 15000;
    /** Measured: 11x #CC then #AGF inside 13 s for group 6 (30 Sep). Group 3
     *  and group 99 have no measured length, so they report none. */
    static final int HANDSHAKE_EST_MS = 13000;

    // ---- her arm while she talks (owner 15 Oct: "ขยับซ้ายขวาสลับกันตอนคุย") ----
    /** A speech gesture's frames end with the arm still out; it waits this
     *  long for her next beat, then group 99. The first build rested after
     *  every gesture and ran 3 s frames: "ท่ายังช้าแล้วดูไม่เป็นธรรมชาติ". */
    static final int SPEECH_REST_AFTER_MS = 1500;
    /** Speech gestures run back to back (no COOLDOWN_MS between them) under
     *  their own cap; voice gestures keep MAX_PER_MINUTE. */
    static final int MAX_SPEECH_PER_MINUTE = 15;
    /** The keyframe lengths the studio offers, all inside MIN..MAX_FRAME_MS.
     *  The studio used to capture every frame at 3000 ms. */
    static final int[] STUDIO_FRAME_MS = {800, 1000, 1200, 1500, 2000, 3000};

    /** Which ids may run as a speech gesture: recorded arm poses only -- no
     *  head (ch8 burned on 12 Sep; the head keeps its slow prefix), no
     *  vendor group (groups end at their own pose), no rest, no stop. */
    static boolean speechCapable(String id) {
        return known(id) && !isHead(id) && !"handshake".equals(id) && !"greet_arm".equals(id)
                && !"rest".equals(id) && !"stop".equals(id);
    }

    /** One timed target set. Its own type (not ArmController.PoseFrame) so this
     *  file compiles without the vendor SDK. */
    static final class Frame {
        final int durationMs;
        final Map<Integer, Integer> targets;

        Frame(int durationMs, Map<Integer, Integer> targets) {
            this.durationMs = durationMs;
            this.targets = Collections.unmodifiableMap(new TreeMap<>(targets));
        }
    }

    static Frame frame(int durationMs, int... channelPulse) {
        if (channelPulse.length == 0 || channelPulse.length % 2 != 0) {
            throw new IllegalArgumentException("frame needs channel/pulse pairs");
        }
        LinkedHashMap<Integer, Integer> targets = new LinkedHashMap<>();
        for (int i = 0; i < channelPulse.length; i += 2) targets.put(channelPulse[i], channelPulse[i + 1]);
        return new Frame(durationMs, targets);
    }

    static boolean known(String id) {
        if (id == null) return false;
        for (String each : IDS) if (each.equals(id)) return true;
        return false;
    }

    static boolean isHead(String id) {
        return "head_up".equals(id) || "head_down".equals(id) || "head_left".equals(id)
                || "head_right".equals(id) || "head_center".equals(id) || "nod".equals(id)
                || "shake_head".equals(id);
    }

    /** The compiled-in status (§2c). Only measured motion is anything but
     *  needs_mapping; `stop` is not a gesture and has none. */
    static String builtInStatus(String id) {
        if (!known(id) || "stop".equals(id)) return null;
        switch (id) {
            case "handshake":
            case "greet_arm":
            case "rest":
                return VERIFIED;
            case "head_center":
            case "head_down":
            case "nod":
                return DIRECTION_KNOWN;
            default:
                // head_up / shake_head need the ch8/ch7 MINUS side, never
                // driven; head_left/right: ch7 + is IMAGE-right and the
                // robot's own left is unconfirmed; the arms and hands have no
                // channel evidence at all (group 3 is not a clean "raise").
                return NEEDS_MAPPING;
        }
    }

    /** What a recorded pose makes of an id: verified gestures cannot be
     *  overwritten, anything else becomes operator_mapped. */
    static String effectiveStatus(String id, boolean hasRecordedPose) {
        String builtIn = builtInStatus(id);
        if (builtIn == null) return null;
        if (VERIFIED.equals(builtIn)) return VERIFIED;
        return hasRecordedPose ? OPERATOR_MAPPED : builtIn;
    }

    /** Built-in frame gestures. ONLY the measured +40 on ch8 (and centre).
     *  The first revision used ±80 and the ch7/ch8 minus sides; the build
     *  review rejected that and a test now scans this file for any ch7/ch8
     *  pulse outside 1460-1540. The first move of each is T3000 -- the timing
     *  the owner measured with. */
    static List<Frame> builtInFrames(String id) {
        if ("head_center".equals(id)) {
            return list(frame(3000, 7, 1500, 8, 1500));
        }
        if ("head_down".equals(id)) {
            return list(frame(3000, 8, 1540), frame(2500, 8, 1540), frame(3000, 8, 1500));
        }
        if ("nod".equals(id)) {
            // §2c's table writes the first nod frame as T1500, while the same
            // section says the first move of EACH head gesture is T3000; the
            // slower reading is taken.
            return list(frame(3000, 8, 1540), frame(1500, 8, 1500),
                    frame(1500, 8, 1540), frame(1500, 8, 1500));
        }
        return null;
    }

    /** The prefix played once while the head position is unknown. */
    static Frame headPrefix() {
        return frame(HEAD_PREFIX_MS, 7, 1500, 8, 1500);
    }

    private static List<Frame> list(Frame... frames) {
        ArrayList<Frame> out = new ArrayList<>();
        Collections.addAll(out, frames);
        return Collections.unmodifiableList(out);
    }

    static int limitMin(int channel) {
        return channel == HEAD_TURN || channel == HEAD_TILT ? HEAD_LIMIT_MIN : ARM_LIMIT_MIN;
    }

    static int limitMax(int channel) {
        return channel == HEAD_TURN || channel == HEAD_TILT ? HEAD_LIMIT_MAX : ARM_LIMIT_MAX;
    }

    /** Rules 2-4 of §2c, checked at save AND again at play time (a limit or an
     *  allow-list changed after a save must still hold). Returns the rule that
     *  failed, in Thai, or null. */
    static String checkFrames(List<Frame> frames, Set<Integer> allowedChannels) {
        if (frames == null || frames.isEmpty()) return "ท่าต้องมีอย่างน้อย 1 คีย์เฟรม";
        if (frames.size() > MAX_FRAMES) return "ท่ามีได้ไม่เกิน " + MAX_FRAMES + " คีย์เฟรม";
        int total = 0;
        for (Frame frame : frames) {
            if (frame.durationMs < MIN_FRAME_MS || frame.durationMs > MAX_FRAME_MS) {
                return "แต่ละคีย์เฟรมต้องยาว " + MIN_FRAME_MS + "–" + MAX_FRAME_MS + " ms";
            }
            total += frame.durationMs;
            if (frame.targets.isEmpty()) return "คีย์เฟรมต้องมีอย่างน้อยหนึ่งช่อง";
            for (Map.Entry<Integer, Integer> target : frame.targets.entrySet()) {
                int channel = target.getKey();
                int pulse = target.getValue();
                if (allowedChannels == null || !allowedChannels.contains(channel)) {
                    return "ช่อง " + channel + " ไม่อยู่ในรายการช่องที่อนุญาตให้ท่าเสียง";
                }
                if (pulse < limitMin(channel) || pulse > limitMax(channel)) {
                    return "ช่อง " + channel + " ค่า " + pulse + " เกินขอบเขต "
                            + limitMin(channel) + "–" + limitMax(channel);
                }
            }
        }
        if (total > MAX_TOTAL_MS) return "ท่ารวมยาวได้ไม่เกิน " + MAX_TOTAL_MS + " ms";
        return null;
    }

    /** "บันทึกร่างนี้เป็นท่า…": all six rules of §2c, in order, each named. */
    static String checkSave(String id, List<Frame> frames, Set<Integer> allowedChannels, boolean playedOk) {
        String builtIn = builtInStatus(id);
        if (builtIn == null) return "ไม่รู้จักท่านี้";
        if (!NEEDS_MAPPING.equals(builtIn) && !DIRECTION_KNOWN.equals(builtIn)) {
            return "ท่านี้ยืนยันแล้ว บันทึกทับไม่ได้";
        }
        String bad = checkFrames(frames, allowedChannels);
        if (bad != null) return bad;
        if (!playedOk) return "ต้องกด \"เล่นร่างท่า\" ดูให้เห็นก่อน หลังแก้ร่างครั้งล่าสุด";
        return null;
    }

    /** Rule 6: a pose that moved ch7/ch8 hands the head back at centre. Only
     *  the head channels the pose touched are returned (§2c writes both): an
     *  untouched channel's position is whatever it was, and a 1200 ms move of
     *  a joint nobody commanded would be a blind fast move. */
    static List<Frame> withHeadReturn(List<Frame> frames) {
        ArrayList<Frame> out = new ArrayList<>(frames);
        if (frames.isEmpty()) return out;
        boolean turn = false;
        boolean tilt = false;
        for (Frame frame : frames) {
            turn |= frame.targets.containsKey(HEAD_TURN);
            tilt |= frame.targets.containsKey(HEAD_TILT);
        }
        Map<Integer, Integer> last = frames.get(frames.size() - 1).targets;
        LinkedHashMap<Integer, Integer> back = new LinkedHashMap<>();
        if (turn && !Integer.valueOf(CENTRE).equals(last.get(HEAD_TURN))) back.put(HEAD_TURN, CENTRE);
        if (tilt && !Integer.valueOf(CENTRE).equals(last.get(HEAD_TILT))) back.put(HEAD_TILT, CENTRE);
        if (!back.isEmpty()) {
            if (turn) back.put(HEAD_TURN, CENTRE);
            if (tilt) back.put(HEAD_TILT, CENTRE);
            out.add(new Frame(HEAD_RETURN_MS, back));
        }
        return out;
    }

    /** rest_after is automatic: any channel other than the head means an arm
     *  moved, and arm gestures always end in group 99. */
    static boolean restAfter(List<Frame> frames) {
        for (Frame frame : frames) {
            for (int channel : frame.targets.keySet()) {
                if (channel != HEAD_TURN && channel != HEAD_TILT) return true;
            }
        }
        return false;
    }

    static int totalMs(List<Frame> frames) {
        int total = 0;
        for (Frame frame : frames) total += frame.durationMs;
        return total;
    }

    /**
     * The refusal for a voice/page gesture, or null to accept. The order IS
     * the contract (§2c): it is also the order the reasons are reported in,
     * so a robot that is both latched and busy says "latched".
     *
     * @param navBlock null, "navigating" or "nav_unknown" (NavLauncher decides)
     */
    static String refusal(boolean enabled, boolean operatorVisible, boolean foreground,
                          String navBlock, boolean latched, String status,
                          boolean allowDirectionKnown, boolean busy,
                          boolean coolingDown, boolean rateLimited) {
        if (!enabled) return "disabled";
        if (operatorVisible) return "operator_screen";
        if (!foreground) return "not_foreground";
        if (navBlock != null) return "nav_unknown".equals(navBlock) ? "nav_unknown" : "navigating";
        if (latched) return "latched";
        if (status == null || NEEDS_MAPPING.equals(status)) return "needs_mapping";
        if (DIRECTION_KNOWN.equals(status) && !allowDirectionKnown) return "direction_unconfirmed";
        if (busy) return "busy";
        if (coolingDown) return "cooldown";
        if (rateLimited) return "rate_limited";
        return null;
    }
}
