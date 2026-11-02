package com.emma.robot.control;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * The one owner of a voice/page gesture on the arm (§2c). The camera
 * greeting, a voice gesture and the rest before a walk all go through here,
 * so there is ONE lock on the arm -- before this the greeting had its own
 * `greetingPending` and nothing stopped a second writer joining it.
 *
 * Threads (§2b): arm work runs on MainActivity's `io` (connect, gestures,
 * rest-before-run). A stop never waits on `io`: it sets the latch and voids
 * every timer on the CALLING thread, then calls stopAll() on its own
 * `body-stopper` thread. `io` is a single thread that also runs driveLoop and
 * a ~5 s arm connect; a stop queued behind those would arrive seconds late.
 * Every task that was queued on `io` before a stop re-checks the generation,
 * the latch and the nav state before it moves anything.
 */
final class BodyController {
    interface Host {
        /** Activity resumed AND the window has focus. */
        boolean foreground();
        boolean operatorVisible();
        /** null, "navigating" or "nav_unknown" (NavLauncher.bodyBlock). */
        String navBlock();
        /** Free the CH340 from the vendor app and connect; throws when the
         *  arm board is not there. Runs on io. */
        void ensureArm() throws Exception;
        /** Something the page or the SafetyBar shows changed (any thread). */
        void changed();
    }

    static final String PREFS = "emma_body_nav";
    static final String PREF_ENABLED = "body_enabled";
    static final String PREF_ALLOW_DIRECTION_KNOWN = "allow_direction_known";
    static final String PREF_VOICE_CHANNELS = "voice_channels";

    private static final String TAG = "EmmaBody";
    private static final Object TIMERS = new Object();   // token: one removeCallbacks voids them all
    /** Review #3: a second #STOP this long after the first. The vendor SDK
     *  writes every serial command on a NEW thread with no lock, so a group
     *  command issued a moment before the STOP can reach the board after it;
     *  the repeat cancels a group that overtook the first. */
    static final long STOP_REPEAT_MS = 180L;
    /** Review #13: slack on a frame gesture's own length before the watchdog
     *  calls it lost. */
    static final long FRAMES_WATCHDOG_SLACK_MS = 3000L;

    private final ArmController arm;
    private final ExecutorService io;
    private final Host host;
    private final SharedPreferences prefs;
    private final BodyPoseStore poses;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService bodyStopper = Executors.newSingleThreadExecutor(
            runnable -> new Thread(runnable, "body-stopper"));
    /** Bumped by every stop/halt: a timer or a queued task carrying an older
     *  value is void ("after an explicit stop NOTHING may move on a timer"). */
    private final AtomicInteger generation = new AtomicInteger();
    /** Review #3: the final generation check and the arm command it guards
     *  happen under this lock, and every stop bumps the generation under it.
     *  Before, play() checked stale(gen) on io and only then sent the command:
     *  a stop landing in between was acknowledged ("stopped", SafetyBar gone)
     *  and the whole gesture still played. The arm calls inside do not block
     *  (the SDK spawns its write; frames are queued on the pose thread), so a
     *  stop waits microseconds at most and never waits on io. Lock order:
     *  armGate, then this -- nothing holding `this` takes armGate. */
    private final Object armGate = new Object();
    private final AtomicBoolean busy = new AtomicBoolean(false);
    /** One per gesture: a timer keyed only on the generation would also fire
     *  during the NEXT gesture (a stop is the only thing that bumps it). */
    private final AtomicInteger attempts = new AtomicInteger();
    private final ArrayDeque<Long> starts = new ArrayDeque<>();
    /** Starts of speech gestures, their own per-minute cap. */
    private final ArrayDeque<Long> speechStarts = new ArrayDeque<>();

    private volatile long latchedUntil = 0L;
    private volatile long lastEnd = -BodyGestures.COOLDOWN_MS;
    /** Groups move joints nobody can read back; app start knows nothing either. */
    private volatile boolean headKnown = false;

    // ---- the head look (HeadLook; owner 31 Oct) -- its own lock: held only
    // for these fields and the gate check, never while waiting on the arm
    // (armGate may take it inside, never the other way round) ----
    private static final Object LOOK_TIMER = new Object();
    private final Object lookLock = new Object();
    /** The last look's move, for HeadLook.position. moveValid=false after
     *  anything that moved the head without saying where to (a group, a rest,
     *  a head gesture, a stop mid-move, a failed write). */
    private boolean moveValid = false;
    private boolean moveFromKnown = false;
    private int moveFromPan = HeadLook.CENTRE;
    private int moveFromTilt = HeadLook.CENTRE;
    private int moveToPan = HeadLook.CENTRE;
    private int moveToTilt = HeadLook.CENTRE;
    private long moveStart = 0L;
    private int moveMs = 0;
    private long lastLookAt = -HeadLook.IDLE_CENTRE_MS;
    /** Only the newest queued look may reach the board: io is shared with the
     *  gestures, and a backlog of old looks would swing the head back. */
    private final AtomicInteger lookSeq = new AtomicInteger();
    /** idle | running | holding | rest_return. "holding" = a speech
     *  gesture's frames ended with the arm out, waiting for her next beat
     *  (SPEECH_REST_AFTER_MS) before group 99; busy is free in it. */
    private volatile String phase = "idle";
    private volatile String active = null;
    private volatile boolean groupRunning = false;
    private volatile boolean groupAgfSeen = false;
    private volatile int groupHoldMs = 0;
    private volatile CountDownLatch restAgf = null;
    private int seq = 0;
    private JSONObject last = null;

    BodyController(Context context, ArmController arm, ExecutorService io, Host host) {
        this.arm = arm;
        this.io = io;
        this.host = host;
        this.prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        this.poses = new BodyPoseStore(context);
    }

    // ---- robot-side switches (operator screen only) ---------------------------

    /** Gestures by voice: ON unless the operator turned them off (owner,
     *  13 Oct: "ท่าทางให้ emma สั่งการไม่ได้หรอ ... ทำเลย"). A robot that never
     *  touched the switch used to read it as off, so "ขอจับมือ" reached the
     *  model and the arm never moved. The operator screen's switch still turns
     *  it off and that choice is kept; walking (NavLauncher nav_enabled) stays
     *  OFF by default. Every other gate -- foreground, operator screen, stop
     *  latch, walking, cooldown, the server's heard guard -- is unchanged. */
    boolean enabled() {
        return prefs.getBoolean(PREF_ENABLED, true);
    }

    boolean allowDirectionKnown() {
        return prefs.getBoolean(PREF_ALLOW_DIRECTION_KNOWN, false);
    }

    void setEnabled(boolean value) {
        prefs.edit().putBoolean(PREF_ENABLED, value).apply();
        host.changed();
    }

    void setAllowDirectionKnown(boolean value) {
        prefs.edit().putBoolean(PREF_ALLOW_DIRECTION_KNOWN, value).apply();
        host.changed();
    }

    Set<Integer> voiceChannels() {
        LinkedHashSet<Integer> out = new LinkedHashSet<>();
        String stored = prefs.getString(PREF_VOICE_CHANNELS, null);
        if (stored == null) {
            for (int channel : BodyGestures.DEFAULT_VOICE_CHANNELS) out.add(channel);
            return out;
        }
        for (String part : stored.split(",")) {
            try {
                int channel = Integer.parseInt(part.trim());
                if (channel >= 1 && channel <= 20) out.add(channel);
            } catch (NumberFormatException ignored) {
                // a hand-edited pref is read as what it says, never "fixed"
            }
        }
        return out;
    }

    void setVoiceChannel(int channel, boolean allowed) {
        Set<Integer> channels = voiceChannels();
        if (allowed) channels.add(channel);
        else channels.remove(channel);
        StringBuilder joined = new StringBuilder();
        for (int each : channels) {
            if (joined.length() > 0) joined.append(',');
            joined.append(each);
        }
        prefs.edit().putString(PREF_VOICE_CHANNELS, joined.toString()).apply();
        host.changed();
    }

    // ---- what the page reads (UI thread, cached state only) -------------------

    String effectiveStatus(String id) {
        return BodyGestures.effectiveStatus(id, poses.has(id));
    }

    String list() {
        try {
            JSONObject gestures = new JSONObject();
            for (String id : BodyGestures.IDS) {
                String status = effectiveStatus(id);
                if (status != null) gestures.put(id, status);
            }
            return new JSONObject()
                    .put("enabled", enabled())
                    .put("allow_direction_known", allowDirectionKnown())
                    .put("gestures", gestures)
                    .put("speech", true)          // this build plays emma-body:<id>:speech
                    .put("look", true)            // and emma-body:look:<pan>:<tilt> (HeadLook)
                    .toString();
        } catch (Exception e) {
            return "{\"enabled\":false,\"allow_direction_known\":false,\"gestures\":{}}";
        }
    }

    synchronized String status() {
        try {
            return new JSONObject()
                    .put("seq", seq)
                    .put("state", stateName(SystemClock.elapsedRealtime()))
                    .put("active", active == null ? JSONObject.NULL : active)
                    .put("last", last == null ? JSONObject.NULL : last)
                    .toString();
        } catch (Exception e) {
            return "{\"seq\":0,\"state\":\"idle\",\"active\":null,\"last\":null}";
        }
    }

    private String stateName(long now) {
        if (latched(now)) return "latched";
        // holding: the arm is still out -- reported as running, never idle
        if ("running".equals(phase) || "holding".equals(phase)) return "running";
        if ("rest_return".equals(phase)) return "rest_return";
        if (now - lastEnd < BodyGestures.COOLDOWN_MS) return "cooldown";
        return "idle";
    }

    boolean latched() {
        return latched(SystemClock.elapsedRealtime());
    }

    private boolean latched(long now) {
        return now < latchedUntil;
    }

    boolean busy() {
        return busy.get();
    }

    /** For the SafetyBar: something on the arm is moving or returning. */
    boolean moving() {
        return !"idle".equals(phase);
    }

    void clearLatch() {
        latchedUntil = 0L;
        host.changed();
    }

    // ---- requests ----------------------------------------------------------------

    /** emma-body:<id> from the trusted page (the server relayed move_body).
     *  "Accepted" means queued after the cheap gates passed (§2b); the
     *  definitive outcome arrives as status. */
    synchronized String request(String id) {
        if (!BodyGestures.known(id) || "stop".equals(id)) return ack(false, id, "bad_request", null, -1);
        long now = SystemClock.elapsedRealtime();
        String status = effectiveStatus(id);
        List<BodyGestures.Frame> frames = framesFor(id);
        String reason = BodyGestures.refusal(enabled(), host.operatorVisible(), host.foreground(),
                host.navBlock(), latched(now), status, allowDirectionKnown(), busy.get(),
                now - lastEnd < BodyGestures.COOLDOWN_MS, rateLimited(now));
        if (reason == null && frames != null
                && BodyGestures.checkFrames(frames, voiceChannels()) != null) {
            // the clamps are re-checked at play time, not only at save: an
            // allow-list narrowed after a save makes the gesture unplayable
            reason = "needs_mapping";
        }
        if (reason != null) return ack(false, id, reason, status, -1);
        if (!busy.compareAndSet(false, true)) return ack(false, id, "busy", status, -1);
        int est = begin(id, now, false);
        return ack(true, id, null, status, est);
    }

    /** emma-body:<id>:speech -- her arm while she talks (owner 15 Oct), sent
     *  by the server timed to her voice, never by a guest. Every gate of a
     *  voice gesture holds (switch, operator screen, foreground, walking,
     *  stop latch, mapping, busy, the clamps and allow-list) except the 3 s
     *  cooldown, which made each gesture wait out the previous one's rest;
     *  speech gestures have their own per-minute cap instead. Its frames end
     *  in "holding", not in group 99: the next beat starts from where the arm
     *  is, and the rest comes once, SPEECH_REST_AFTER_MS after the last. */
    synchronized String requestSpeech(String id) {
        if (!BodyGestures.speechCapable(id)) return ack(false, id, "bad_request", null, -1);
        long now = SystemClock.elapsedRealtime();
        String status = effectiveStatus(id);
        List<BodyGestures.Frame> frames = framesFor(id);
        String reason = BodyGestures.refusal(enabled(), host.operatorVisible(), host.foreground(),
                host.navBlock(), latched(now), status, allowDirectionKnown(), busy.get(),
                false, speechRateLimited(now));
        if (reason == null && (frames == null || !BodyGestures.restAfter(frames)
                || BodyGestures.checkFrames(frames, voiceChannels()) != null)) {
            reason = "needs_mapping";
        }
        if (reason != null) return ack(false, id, reason, status, -1);
        if (!busy.compareAndSet(false, true)) return ack(false, id, "busy", status, -1);
        // her next beat: the pending rest of the previous one is void (the
        // only timer a holding arm has; its frames' watchdog is spent)
        if ("holding".equals(phase)) ui.removeCallbacksAndMessages(TIMERS);
        int est = begin(id, now, true);
        return ack(true, id, null, status, est);
    }

    /** The legacy `emma-arm:greet:6` (camera greeter). MainActivity has
     *  already checked the gates it always had (foreground, focus, not the
     *  operator screen); it now also shares the arm's one lock, and never
     *  starts while the robot walks or a stop latch holds -- an arm swung out
     *  in a doorway is the reason the walk rests it first. Not gated on
     *  body_enabled: the greeting predates that switch and is keyed by the
     *  server's ROBOT_ARM_* flags, so the robot's behaviour on pull is the same. */
    synchronized void greet() {
        long now = SystemClock.elapsedRealtime();
        if (latched(now) || host.navBlock() != null) return;
        if (!busy.compareAndSet(false, true)) return;
        begin("handshake", now, false);
    }

    // ---- the head toward a person (HeadLook) -------------------------------------

    /** emma-body:look:<pan>:<tilt> from the trusted page: the server's camera
     *  loop (app/head_follow.py), about once a second during a call -- never a
     *  guest's words. The voice gestures' gates hold (switch, operator screen,
     *  foreground, walking, stop latch), plus the arm being in a group or in a
     *  gesture that moves the head itself. "Accepted" = queued; nothing here
     *  waits on the board. */
    String look(int pan, int tilt) {
        if (!HeadLook.valid(pan, tilt)) return lookAck(false, "bad_request", -1);
        long now = SystemClock.elapsedRealtime();
        int toPan = HeadLook.panPulse(pan);
        int toTilt = HeadLook.tiltPulse(tilt);
        // every input read OUTSIDE lookLock: it guards two fields, never a call out
        String gate = HeadLook.refusal(enabled(), host.operatorVisible(), host.foreground(), host.navBlock(),
                latched(now), headBusy(), false, HeadLook.isCentre(toPan, toTilt));
        String reason = gate;
        synchronized (lookLock) {
            if (reason == null && now - lastLookAt < HeadLook.MIN_GAP_MS) reason = "too_soon";
            if (reason == null) lastLookAt = now;
        }
        if (reason != null) return lookAck(false, reason, -1);
        // how long the move will take, from where the head is now: the server
        // reads the next picture after it (sendLook works it out again on io)
        int[] at = headPosition(now);
        int ms = at == null ? HeadLook.UNKNOWN_MS : HeadLook.durationMs(true, at[0], at[1], toPan, toTilt);
        sendLook(toPan, toTilt);
        ui.removeCallbacksAndMessages(LOOK_TIMER);
        ui.postAtTime(this::centreIfIdle, LOOK_TIMER, SystemClock.uptimeMillis() + HeadLook.IDLE_CENTRE_MS);
        return lookAck(true, null, ms);
    }

    /** A vendor group, a rest, or a gesture whose frames move ch7/ch8 owns the
     *  head. A speech gesture's arm frames (and its "holding") do not. */
    private boolean headBusy() {
        if (groupRunning || "rest_return".equals(phase)) return true;
        String id = active;
        if (!"running".equals(phase) || id == null) return false;
        if (BodyGestures.isHead(id)) return true;
        List<BodyGestures.Frame> frames = framesFor(id);
        if (frames == null) return true;             // handshake, greet_arm, rest: groups
        for (BodyGestures.Frame frame : frames) {
            if (frame.targets.containsKey(HeadLook.PAN) || frame.targets.containsKey(HeadLook.TILT)) return true;
        }
        return false;
    }

    private int[] headPosition(long now) {
        synchronized (lookLock) {
            if (!moveValid) return null;
            return HeadLook.position(moveFromKnown, moveFromPan, moveFromTilt, moveToPan, moveToTilt,
                    moveStart, moveMs, now);
        }
    }

    /** Something moved or stopped the head without saying where it left it. */
    private void headLost() {
        synchronized (lookLock) {
            moveValid = false;
        }
    }

    /** On io, like every arm command: the gates again under the arm's lock
     *  (a stop between the prompt and here wins), then one timed line. The
     *  move's time comes from where the head is -- the slow UNKNOWN_MS when
     *  nobody knows (after a group, a stop, at start). */
    private void sendLook(int toPan, int toTilt) {
        final int gen = generation.get();
        final int mine = lookSeq.incrementAndGet();
        io.execute(() -> {
            try {
                if (mine != lookSeq.get()) return;
                host.ensureArm();
                synchronized (armGate) {
                    if (mine != lookSeq.get() || gen != generation.get() || latched()
                            || (host.navBlock() != null && !HeadLook.isCentre(toPan, toTilt))
                            || headBusy()) return;
                    long now = SystemClock.elapsedRealtime();
                    int[] at = headPosition(now);
                    int ms = at == null ? HeadLook.UNKNOWN_MS
                            : HeadLook.durationMs(true, at[0], at[1], toPan, toTilt);
                    String line = HeadLook.command(toPan, toTilt, ms);
                    if (line == null) return;
                    arm.writeLine(line);
                    synchronized (lookLock) {
                        moveFromKnown = at != null;
                        if (at != null) {
                            moveFromPan = at[0];
                            moveFromTilt = at[1];
                        }
                        moveToPan = toPan;
                        moveToTilt = toTilt;
                        moveStart = now;
                        moveMs = ms;
                        moveValid = true;
                    }
                }
            } catch (Exception e) {
                Log.w(TAG, "head look failed", e);
                headLost();
            }
        });
    }

    /** No look for IDLE_CENTRE_MS: the head comes back to centre by itself (a
     *  call that ended, a server that died). Waits out a gesture that owns the
     *  head; a stop latch skips it. A walk does not (a centre look passes it). */
    private void centreIfIdle() {
        long now = SystemClock.elapsedRealtime();
        String reason = HeadLook.refusal(enabled(), host.operatorVisible(), host.foreground(), host.navBlock(),
                latched(now), headBusy(), false, true);
        synchronized (lookLock) {
            if (now - lastLookAt < HeadLook.IDLE_CENTRE_MS) return;
            if (reason == null) lastLookAt = now;
        }
        int[] at = headPosition(now);
        if (reason == null && at != null && at[0] == HeadLook.CENTRE && at[1] == HeadLook.CENTRE) return;
        if ("busy".equals(reason)) {
            ui.postAtTime(this::centreIfIdle, LOOK_TIMER, SystemClock.uptimeMillis() + 3000L);
            return;
        }
        if (reason == null) sendLook(HeadLook.CENTRE, HeadLook.CENTRE);
    }

    private static String lookAck(boolean accepted, String reason, int ms) {
        try {
            return new JSONObject()
                    .put("accepted", accepted)
                    .put("action", "look")
                    .put("reason", reason == null ? JSONObject.NULL : reason)
                    .put("ms", ms < 0 ? JSONObject.NULL : Integer.valueOf(ms))
                    .toString();
        } catch (Exception e) {
            return "{\"accepted\":false,\"reason\":\"bad_request\"}";
        }
    }

    private List<BodyGestures.Frame> framesFor(String id) {
        BodyPoseStore.Pose pose = poses.get(id);
        if (pose != null) return pose.frames;
        return BodyGestures.builtInFrames(id);
    }

    private boolean rateLimited(long now) {
        while (!starts.isEmpty() && now - starts.peekFirst() >= 60000L) starts.pollFirst();
        return starts.size() >= BodyGestures.MAX_PER_MINUTE;
    }

    private boolean speechRateLimited(long now) {
        while (!speechStarts.isEmpty() && now - speechStarts.peekFirst() >= 60000L) speechStarts.pollFirst();
        return speechStarts.size() >= BodyGestures.MAX_SPEECH_PER_MINUTE;
    }

    /** busy is already held. Returns the estimate in ms, or -1 for none. */
    private int begin(String id, long now, boolean speech) {
        attempts.incrementAndGet();
        (speech ? speechStarts : starts).addLast(now);
        phase = "running";
        active = id;
        record(id, "started", null);
        final int gen = generation.get();
        final List<BodyGestures.Frame> frames = framesFor(id);
        int est = -1;
        if ("handshake".equals(id)) est = BodyGestures.HANDSHAKE_EST_MS;
        else if (frames != null) est = BodyGestures.totalMs(frames)
                + (headKnown || !BodyGestures.isHead(id) ? 0 : BodyGestures.HEAD_PREFIX_MS);
        host.changed();
        io.execute(() -> play(id, gen, frames, speech));
        return est;
    }

    /** On io. Everything queued before a stop re-checks before it moves. */
    private void play(String id, int gen, List<BodyGestures.Frame> frames, boolean speech) {
        if (stale(gen)) return;
        try {
            host.ensureArm();
        } catch (Exception e) {
            Log.e(TAG, "arm connect failed for " + id, e);
            finish(gen, "failed", "arm_unavailable");
            return;
        }
        try {
            if ("handshake".equals(id) || "greet_arm".equals(id)) {
                synchronized (armGate) {
                    if (stale(gen)) return;
                    groupRunning = true;
                    groupAgfSeen = false;
                    groupHoldMs = "handshake".equals(id) ? BodyGestures.HANDSHAKE_HOLD_MS : BodyGestures.GREET_HOLD_MS;
                    headKnown = false;
                    headLost();          // the group moves the head too
                    arm.playGroup("handshake".equals(id) ? BodyGestures.HANDSHAKE_GROUP : BodyGestures.GREET_GROUP);
                }
                stopIfOvertaken(gen);
                // a lost #AGF must not leave the arm out
                later(gen, () -> restReturn(gen, "no #AGF heard"), BodyGestures.GROUP_FALLBACK_MS);
                Log.i(TAG, "gesture " + id + ": group requested via SDK");
                return;
            }
            if ("rest".equals(id)) {
                synchronized (armGate) {
                    if (stale(gen)) return;
                    // phase moves only while the gen is still current: a stop
                    // that set it idle cannot be overwritten to rest_return
                    // (which kept moving() true and the SafetyBar up)
                    phase = "rest_return";
                    headKnown = false;
                    headLost();
                    arm.resetHome();
                }
                stopIfOvertaken(gen);
                later(gen, () -> finish(gen, "done", null), BodyGestures.REST_AGF_TIMEOUT_MS);
                host.changed();
                return;
            }
            if (stale(gen)) return;
            if (frames == null) {
                finish(gen, "failed", "needs_mapping");
                return;
            }
            String bad = BodyGestures.checkFrames(frames, voiceChannels());
            if (bad != null) {
                Log.w(TAG, "gesture " + id + " refused at play time: " + bad);
                finish(gen, "failed", "needs_mapping");
                return;
            }
            final boolean head = BodyGestures.isHead(id);
            final boolean restAfter = BodyGestures.restAfter(frames);
            ArrayList<BodyGestures.Frame> sequence = new ArrayList<>();
            if (head && !headKnown) sequence.add(BodyGestures.headPrefix());
            sequence.addAll(BodyGestures.withHeadReturn(frames));
            ArrayList<ArmController.PoseFrame> poseFrames = new ArrayList<>();
            for (BodyGestures.Frame frame : sequence) {
                poseFrames.add(new ArmController.PoseFrame(frame.targets, frame.durationMs));
            }
            // Review #13: a frame run that ends without onDone (the board
            // dropped off mid-run) used to leave busy, the SafetyBar and every
            // gate stuck. onAbort ends it at the source; this watchdog is the
            // backstop, and does nothing once the gesture left "running"
            // (done, or already returning to rest after the frames).
            final int attempt = attempts.get();
            later(gen, () -> {
                if (attempt == attempts.get() && "running".equals(phase)) finish(gen, "failed", "arm_unavailable");
            }, BodyGestures.totalMs(sequence) + BodyGestures.REST_AFTER_FRAMES_MS + FRAMES_WATCHDOG_SLACK_MS);
            synchronized (armGate) {
                if (stale(gen)) return;
                // Review2 #23: the head is "unknown" from the moment a head
                // sequence starts until ITS onDone. A stop, halt, abort or the
                // watchdog mid head-move left ch7/ch8 wherever they were, its
                // centre frame never ran, and the next head gesture skipped
                // the slow HEAD_PREFIX_MS from an unknown pose (ch8 stalled and
                // burned on 12 Sep).
                if (head) headKnown = false;
                if (head) headLost();          // a look's position is gone with it
                arm.playFrames(poseFrames, () -> gen == generation.get() && !latched(), () -> {
                    if (gen != generation.get()) return;
                    if (head) headKnown = true;
                    if (speech) hold(gen, id);
                    else if (restAfter) later(gen, () -> restReturn(gen, "after frames"), BodyGestures.REST_AFTER_FRAMES_MS);
                    else finish(gen, "done", null);
                }, () -> {
                    Log.w(TAG, "gesture " + id + ": arm board lost mid-run");
                    finish(gen, "failed", "arm_unavailable");
                });
            }
            stopIfOvertaken(gen);
        } catch (Exception e) {
            Log.e(TAG, "gesture " + id + " failed", e);
            finish(gen, "failed", "arm_unavailable");
        }
    }

    /** A speech gesture's frames are done: "done" for the server, the arm
     *  stays out, busy is free for her next beat, and group 99 follows unless
     *  one comes inside SPEECH_REST_AFTER_MS. */
    private synchronized void hold(int gen, String id) {
        if (gen != generation.get() || !"running".equals(phase)) return;
        phase = "holding";
        record(id, "done", null);
        // active stays: the arm is still out for this id, and a stop while
        // holding is reported as that gesture stopped (not an action-less row)
        busy.set(false);
        host.changed();
        later(gen, () -> speechRest(gen), BodyGestures.SPEECH_REST_AFTER_MS);
    }

    /** No beat followed: the one rest of the whole reply. Takes the arm lock
     *  like any gesture; a walk's rest or a voice gesture that started in the
     *  meantime owns the arm instead (phase is no longer holding). */
    private void speechRest(int gen) {
        synchronized (this) {
            if (gen != generation.get() || !"holding".equals(phase)) return;
            if (!busy.compareAndSet(false, true)) return;
            phase = "running";
            active = "rest";
        }
        restReturn(gen, "after speech");
    }

    /** Stale = overtaken by a stop (generation), latched, or the robot is
     *  walking. The last one ends the gesture here; the first two were ended
     *  by the stop itself. */
    private boolean stale(int gen) {
        if (gen != generation.get() || latched()) return true;
        String nav = host.navBlock();
        if (nav != null) {
            finish(gen, "failed", "nav_unknown".equals(nav) ? "nav_unknown" : "navigating");
            return true;
        }
        return false;
    }

    /** Review #3, after every arm command: a stop that arrived after the gate
     *  was released already sends its own #STOP; this one covers the SDK's
     *  unordered per-write threads from this side too. */
    private void stopIfOvertaken(int gen) {
        if (gen != generation.get() || latched()) stopArm();
    }

    /** #AGF from the board (the SDK's receive thread). The only measured
     *  end-of-gesture signal this side has (11x #CC then #AGF inside 13 s for
     *  group 6, read on 2026-09-30). */
    void onArmFinished() {
        CountDownLatch waiting = restAgf;
        if (waiting != null) waiting.countDown();
        final int gen = generation.get();
        if (groupRunning && !groupAgfSeen && "running".equals(phase)) {
            groupAgfSeen = true;
            // wait a beat for the person to actually shake the offered hand
            later(gen, () -> restReturn(gen, "#AGF"), groupHoldMs);
        } else if ("rest_return".equals(phase) && restAgf == null) {
            finish(gen, "done", null);
        }
    }

    /** Group 6 ends at its final pose and a PWM board cannot be asked to undo
     *  that; the SECOND greeting replayed group 6 from its own end pose and
     *  "มือไม่ขึ้น" (2026-09-30). So every arm gesture ends in the vendor's
     *  own rest group 99. */
    private void restReturn(int gen, String why) {
        if (gen != generation.get() || !"running".equals(phase)) return;
        if (host.operatorVisible()) {          // the operator owns the arm now
            finish(gen, "stopped", "operator_screen");
            return;
        }
        groupRunning = false;
        phase = "rest_return";
        host.changed();
        io.execute(() -> {
            try {
                synchronized (armGate) {     // review #3: no stop between the check and the command
                    if (gen != generation.get() || latched()) return;
                    // resetHome, not playGroup: playGroup's range is the operator's
                    // 1-26 and refuses 99 (seen live 2026-09-30)
                    arm.resetHome();
                    headKnown = false;
                    headLost();
                }
                stopIfOvertaken(gen);
                Log.i(TAG, "arm returned to rest group 99 (" + why + ")");
            } catch (Exception e) {
                Log.e(TAG, "rest return failed", e);
                finish(gen, "failed", "arm_unavailable");
                return;
            }
            later(gen, () -> finish(gen, "done", null), BodyGestures.REST_AGF_TIMEOUT_MS);
        });
    }

    private void later(int gen, Runnable work, long delayMs) {
        ui.postAtTime(() -> {
            if (gen == generation.get()) work.run();
        }, TIMERS, SystemClock.uptimeMillis() + delayMs);
    }

    private synchronized void finish(int gen, String state, String reason) {
        if (gen != generation.get() || "idle".equals(phase)) return;
        // every timer belongs to the one gesture the arm lock allows; once it
        // ends, a leftover (the 30 s group fallback, the frames watchdog) must
        // not fire into the NEXT gesture, which shares this generation
        ui.removeCallbacksAndMessages(TIMERS);
        groupRunning = false;
        phase = "idle";
        lastEnd = SystemClock.elapsedRealtime();     // the cooldown runs from the END, after the rest-return
        record(active, state, reason);
        active = null;
        busy.set(false);
        host.changed();
    }

    private void record(String action, String state, String reason) {
        try {
            last = new JSONObject()
                    .put("action", action == null ? JSONObject.NULL : action)
                    .put("state", state)
                    .put("reason", reason == null ? JSONObject.NULL : reason);
        } catch (Exception ignored) {
            last = null;
        }
        seq++;
    }

    // ---- the rest before a walk (§4 "arms out before doors") -------------------

    /** On io: play group 99 and wait for its #AGF. True only on a real #AGF
     *  inside the timeout -- no #AGF or no arm board means the walk does not
     *  start (arms_not_rest), because doors need the arms in. */
    boolean restBeforeRun(long timeoutMs) {
        final int gen;
        final CountDownLatch agf = new CountDownLatch(1);
        synchronized (this) {
            if (latched() || !busy.compareAndSet(false, true)) return false;
            gen = generation.get();
            attempts.incrementAndGet();
            phase = "running";
            active = "rest";
            record("rest", "started", null);
            restAgf = agf;
        }
        host.changed();
        boolean rested = false;
        try {
            host.ensureArm();
            synchronized (armGate) {         // review #3
                if (gen != generation.get() || latched()) return false;
                arm.resetHome();
                headKnown = false;
                headLost();
            }
            rested = agf.await(timeoutMs, TimeUnit.MILLISECONDS) && gen == generation.get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            Log.e(TAG, "rest before run failed", e);
        } finally {
            restAgf = null;
            finish(gen, rested ? "done" : "failed", rested ? null : "arm_unavailable");
        }
        return rested;
    }

    // ---- stops -------------------------------------------------------------------

    /** Explicit stop, or loss of control (onPause, focus, operator screen,
     *  onDestroy). The latch and the voided timers happen HERE, on the
     *  calling thread; only the SDK call goes to body-stopper. */
    void stop(String reason) {
        latchedUntil = SystemClock.elapsedRealtime() + BodyGestures.STOP_LATCH_MS;
        voidEverything(reason);
        stopArm();
        // Review2 #21: the page hears it AFTER the arm stop is queued. The
        // notification reaches the WebView (evaluateJavascript) on the calling
        // thread; on the renderer-death path that view may be dead, and a
        // throw there used to skip stopArm() entirely.
        host.changed();
    }

    /** The chassis stop paths that are not a body stop (return to the face,
     *  a controller unplugged): the arm stops and nothing pending may move
     *  it later, but no latch. */
    void halt() {
        voidEverything(null);
        stopArm();
        host.changed();     // review2 #21: after the stop, as in stop()
    }

    /** Review2 #10: the arm's serial link died (ArmController.linkLost). A
     *  gesture in flight -- a group waiting for #AGF, a rest-return, a walk's
     *  rest -- ends as failed/arm_unavailable, which is what happened, then
     *  the halt voids its timers and wakes a walk's rest wait under a new
     *  generation, so the walk is refused now (arms_not_rest), not after
     *  15 s -- and never counted as rested. A frame gesture was already ended
     *  by its own onAbort; finish() is a no-op on it. No latch: nobody
     *  pressed STOP. */
    void armLost() {
        finish(generation.get(), "failed", "arm_unavailable");
        halt();
    }

    private void stopArm() {
        final int stoppedAt = generation.get();
        try {
            bodyStopper.execute(() -> {
                arm.stopAll();
                // review #3: the SDK's write threads are unordered, so a group
                // sent just before the stop can land after the first #STOP.
                // The repeat is skipped once anything newer owns the arm.
                SystemClock.sleep(STOP_REPEAT_MS);
                if (generation.get() == stoppedAt && !busy.get()) arm.stopAll();
            });
        } catch (RuntimeException closing) {
            arm.stopAll();     // after shutdown (app closing): stop here rather than not at all
        }
    }

    /** The operator played a group: it owns the next #AGF, so a scheduled
     *  rest-return of an earlier gesture is void. */
    void voidRestReturn() {
        voidEverything("operator_screen");
        host.changed();
    }

    /** The caller notifies (host.changed) -- after its arm stop, review2 #21. */
    private void voidEverything(String reason) {
        synchronized (armGate) {     // review #3: never between a gate check and its arm command
            generation.incrementAndGet();
            lookSeq.incrementAndGet();      // a queued head look is void too
        }
        headLost();                         // a stop can leave the head mid-move
        ui.removeCallbacksAndMessages(TIMERS);
        CountDownLatch waiting = restAgf;
        if (waiting != null) waiting.countDown();    // a rest-before-run wakes, sees the new generation, refuses
        synchronized (this) {
            groupRunning = false;
            if (!"idle".equals(phase)) {
                record(active, "stopped", reason);
                lastEnd = SystemClock.elapsedRealtime();
            }
            phase = "idle";
            active = null;
            busy.set(false);
        }
    }

    // ---- poses ---------------------------------------------------------------------

    /** "บันทึกร่างนี้เป็นท่า…". Returns null when saved, else the Thai rule. */
    String savePose(String id, List<ArmController.PoseFrame> draft, boolean playedOk) {
        ArrayList<BodyGestures.Frame> frames = new ArrayList<>();
        for (ArmController.PoseFrame frame : draft) frames.add(new BodyGestures.Frame(frame.durationMs, frame.targets));
        String refusal = poses.save(id, frames, "", playedOk, voiceChannels());
        if (refusal == null) host.changed();
        return refusal;
    }

    void shutdown() {
        bodyStopper.shutdown();
    }

    private static String ack(boolean accepted, String id, String reason, String status, int estMs) {
        try {
            return new JSONObject()
                    .put("accepted", accepted)
                    .put("action", id == null ? JSONObject.NULL : id)
                    .put("reason", reason == null ? JSONObject.NULL : reason)
                    .put("status", status == null ? JSONObject.NULL : status)
                    .put("est_ms", estMs < 0 ? JSONObject.NULL : estMs)
                    .toString();
        } catch (Exception e) {
            return "{\"accepted\":false,\"reason\":\"bad_request\"}";
        }
    }
}
