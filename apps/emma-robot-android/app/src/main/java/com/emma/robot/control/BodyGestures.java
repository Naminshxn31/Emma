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
    /** ch8's minus side stops short of 1460: the 4 Nov dock ladder found the head
     *  stops going UP at ~1468 (HeadLook.TILT_LIMIT_MIN) -- a studio pose at 1460
     *  would push ch8 on a stopped head. Its plus side keeps 1540 (stop ~1545).
     *  The same number as HeadLook.TILT_LIMIT_MIN (a test compares the two; this
     *  file compiles on its own in tests). */
    static final int HEAD_TILT_LIMIT_MIN = 1472;
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
     *  and group 99 have no measured length, so they report none. Kept as the
     *  handshake's estimate with HANDSHAKE_OWN_FRAMES too: the offer 1.5 s + the
     *  hold 5 s + the lowering 3 s + 1 s + group 99's short finish is about the
     *  same 13 s (the server only reports it; app/robot_body.py est_s). */
    static final int HANDSHAKE_EST_MS = 13000;

    // ---- bringing the arm down slowly (owner 6 Nov: "แขนจะกลับท่าพักเองหลังจับมือ
    // 5 วิ ให้เอาลงช้าๆแทน", after a guest still holding the hand said it pulled
    // hard; and for her talking arm "ให้ขยับลงช้าๆแทน", not "stop the gestures") ----
    /** Group 99 runs at the vendor's own speed, which nobody can slow down or
     *  read back (#Read gives the group count only). So after the handshake's
     *  hold the arm first comes most of the way down in one slow frame of ours,
     *  and group 99 only finishes it. */
    static final int HANDSHAKE_LOWER_MS = 3000;
    /** The last frame of a recorded arm pose is at least this long. For the
     *  poses recorded so far (raise_right_arm / raise_left_arm, 15 Oct) it is
     *  the move back down -- elbow straight, wrist and upper arm centred -- and
     *  was recorded at 1000 ms. */
    static final int ARM_LOWER_MIN_MS = 2000;

    /** The handshake's way down: only joints whose rest the 15 Oct sweep
     *  pictures show (data/robot-inspection/arm-channel-sweep-20261015/zoom_chNN):
     *  ch2 / ch4 / ch6 look the same at 1500 as at rest, ch3 at 1300 is all but
     *  the straight elbow of rest. Not ch5 (rest "far below 1300", outside the
     *  clamps -- 1300 there is the arm raised ~45 deg), not the fingers (rest and
     *  1500 differ, which way they close is unknown), not the left arm: which
     *  joints group 6 moves was never recorded, and a handshake is offered with
     *  the right hand. A joint group 6 left at rest moves only by the gap between
     *  its rest and these values, slowly, and group 99 takes it back. Inside the
     *  arm clamps; not the operator's voice-channel list, which says what a
     *  RECORDED pose may drive -- this one is compiled in, like headPrefix().
     *  With HANDSHAKE_OWN_FRAMES (25 Nov) it lowers from handshakeOffer(): the
     *  elbow straightens 1700 -> 1300 while the shoulder swings back 1380 ->
     *  1500, both only ever toward rest, so the hand only goes down
     *  (handshakeProblem checks it). */
    static Frame handshakeLowering() {
        return frame(HANDSHAKE_LOWER_MS, 2, 1500, 3, 1300, 4, 1500, 6, 1500);
    }

    // ---- our own handshake (owner 25 Nov 2026, the morning of the opening ceremony: "ทำเลยแต่ยัง
    // ไม่ต้องอัพ ก็ไปดูจากของเขาก่อนได้ไหม ว่ามีคำสั่งอะไรบ้าง แล้วเช็คดูดีๆ") ----
    /** true: "handshake" plays OUR frames -- handshakeOffer(), HANDSHAKE_HOLD_MS with the hand out
     *  for the guest, handshakeLowering(), then group 99 only for what the clamps cannot reach (the
     *  elbow's last bit below 1300, the fingers). false: the vendor's group 6 exactly as before
     *  (#AGF, the hold, the lowering, group 99). This is the ONE line to revert.
     *
     *  Why: the owner's video of a handshake (filmed from behind): group 6 put the right hand out at
     *  ~3.5 s, brought it almost down, then bent the elbow back UP at ~9.75-10.25 s and was down by
     *  ~11.5 s -- all inside group 6 (its #AGF came ~13 s after the request; our hold and lowering
     *  frame ran at +14.6 s on an arm already down -- and its elbow 1300, on an elbow that rests
     *  below 1300, can only bend it up a little). Group 6's keyframes live in the Torobot board's flash:
     *  #Read answers the group count only (#R+OK+026), the vendor app (com.aobo.robot.ai3, read
     *  25 Nov: same APK sha as the Sep reference, packed, no frame strings in its files or private
     *  data) only ever sends #6GC1, so group 6 cannot be trimmed -- only replaced.
     *
     *  OFF since 25 Nov: the owner kept the vendor's handshake ("ใช้ท่าจับมือเดิมไปก่อน", then
     *  "ใช้ท่าของผู้ผลิตอย่างเดียว"); our frames stay here, tested, for a later day. */
    static final boolean HANDSHAKE_OWN_FRAMES = false;
    /** true (owner 25 Nov, the opening day: "ใช้ท่าของผู้ผลิตอย่างเดียว"): group 6's #AGF is followed at
     *  once by the vendor's rest group 99 -- no hold, no slow lowering frame of ours. Group 6 offers
     *  the hand, holds it and brings it down by itself (round 8's clip, 25 Nov: down ~5.5 s after the
     *  command); after its #AGF our 5 s hold held nothing, and our lowering frame (elbow 1300) on an
     *  elbow already resting below 1300 bent it back UP -- with group 99 after it, the two small
     *  outward moves the owner saw after the handshake. Group 99 stays: the vendor's own rest, and a
     *  second group 6 played from group 6's end pose did not raise the hand (30 Sep). false = the
     *  6 Nov hold + slow lowering + group 99. */
    static final boolean HANDSHAKE_VENDOR_ONLY = true;
    /** The hand going out: the vendor's took ~1.5 s on the video. The owner's recorded
     *  raise_right_arm moves the same elbow from rest to 1700 in 1000 ms (played on the robot as
     *  her talking arm since 15 Oct), so this is slower than anything the elbow already does. */
    static final int HANDSHAKE_OFFER_MS = 1500;
    /** The right-arm joints our handshake may drive -- never ch5 (rest far below 1300: every
     *  allowed value is the arm out sideways >= ~45 deg), not the fingers (which way they close is
     *  unknown), not the head (ch8 burned on 12 Sep), not the left arm (a handshake is offered with
     *  the right hand; the video shows the left arm at rest throughout). */
    static final int[] HANDSHAKE_CHANNELS = {2, 3, 4, 6};

    /** The rest each handshake joint comes DOWN toward (15 Oct sweep,
     *  data/robot-inspection/arm-channel-sweep-20261015): ch2 / ch4 / ch6 look the same at 1500 as
     *  at rest; ch3's rest is below 1300, the lowest the clamps allow (1300 = all but straight).
     *  -1 = not a handshake joint. */
    static int handshakeRest(int channel) {
        switch (channel) {
            case 2:
            case 4:
            case 6:
                return CENTRE;
            case 3:
                return ARM_LIMIT_MIN;
            default:
                return -1;
        }
    }

    /** The hand offered: the right forearm out in front at the waist, palm sideways. Not new to the
     *  robot -- the 9 Nov pageant's first pose (2=1350 3=1700 4=1500 6=1380), played from the app on
     *  10 Nov, was on camera "the forearm held out at the waist like a handshake -- the hand never
     *  came above the belt" (docs/emma-event-host.md, Pageant.java); this is that pose with the
     *  wrist at its rest instead of turned:
     *   ch2 wrist turn 1500 = its rest: with the arm hanging the hand is edge-on to the front (1300
     *       turns the palm to the front, 1700 the back of the hand), so with the forearm bent forward
     *       the palm faces in, thumb up -- a handshake's hand (on camera with the forearm forward:
     *       not yet seen);
     *   ch3 elbow 1700 = bent, the forearm forward (the sweep's top; raise_right_arm's 1700 too);
     *   ch4 upper-arm turn 1500 = its rest: the forearm straight ahead, not swung in or out;
     *   ch6 shoulder swing 1380 = the upper arm a little forward (10 Nov with the elbow at 1700:
     *       1380 = the hand at the waist, 1300 the chest -- this joint is sensitive, ~80 us ~ 45 deg
     *       near the top, so it stays at the measured 1380).
     *  ch5 is not in it: it stays where group 99 left it, at rest. */
    static Frame handshakeOffer() {
        return frame(HANDSHAKE_OFFER_MS, 2, 1500, 3, 1700, 4, 1500, 6, 1380);
    }

    /** null when our handshake is inside every rule, else which one it breaks. Checked by the tests
     *  AND at every play (BodyController.playOwnHandshake: a bad edit plays nothing, "needs_mapping"),
     *  like Pageant.check: both frames inside MIN..MAX_FRAME_MS and MAX_TOTAL_MS, only
     *  HANDSHAKE_CHANNELS, inside the arm clamps; and the way down only goes DOWN -- every joint the
     *  offer moved is in the lowering, each moving toward its rest from where the offer left it and
     *  never past it, and no joint the offer left alone (the owner's complaint was a joint going
     *  back up on the way down). */
    static String handshakeProblem() {
        Frame offer = handshakeOffer();
        Frame down = handshakeLowering();
        for (Frame each : new Frame[] {offer, down}) {
            if (each.durationMs < MIN_FRAME_MS || each.durationMs > MAX_FRAME_MS) return "frame_ms";
            for (Map.Entry<Integer, Integer> target : each.targets.entrySet()) {
                int channel = target.getKey();
                int pulse = target.getValue();
                if (handshakeRest(channel) < 0) return "channel_" + channel;
                if (pulse < limitMin(channel) || pulse > limitMax(channel)) return "arm_range_" + channel;
            }
        }
        if (offer.durationMs + down.durationMs > MAX_TOTAL_MS) return "too_long";
        for (Map.Entry<Integer, Integer> target : offer.targets.entrySet()) {
            int channel = target.getKey();
            Integer to = down.targets.get(channel);
            if (to == null) return "not_lowered_" + channel;
            int from = target.getValue();
            int rest = handshakeRest(channel);
            if (Math.abs(to - rest) > Math.abs(from - rest) || (long) (to - rest) * (from - rest) < 0) {
                return "lowering_up_" + channel;
            }
        }
        for (int channel : down.targets.keySet()) {
            if (!offer.targets.containsKey(channel)) return "lowering_moves_" + channel;
        }
        return null;
    }

    /** A recorded arm pose with its last frame stretched to ARM_LOWER_MIN_MS,
     *  inside MAX_FRAME_MS and MAX_TOTAL_MS (a pose near the total cap is
     *  stretched as far as the cap allows). Head-only poses come back as they
     *  are: the head keeps its own timings (HEAD_PREFIX_MS, HEAD_RETURN_MS). */
    static List<Frame> lowerLastSlowly(List<Frame> frames) {
        if (frames == null || frames.isEmpty() || !restAfter(frames)) return frames;
        Frame last = frames.get(frames.size() - 1);
        int room = Math.max(0, MAX_TOTAL_MS - totalMs(frames));
        int ms = Math.min(Math.min(ARM_LOWER_MIN_MS, MAX_FRAME_MS), last.durationMs + room);
        if (ms <= last.durationMs) return frames;
        ArrayList<Frame> out = new ArrayList<>(frames);
        out.set(out.size() - 1, new Frame(ms, last.targets));
        return Collections.unmodifiableList(out);
    }

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
        if (channel == HEAD_TILT) return HEAD_TILT_LIMIT_MIN;
        return channel == HEAD_TURN ? HEAD_LIMIT_MIN : ARM_LIMIT_MIN;
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
