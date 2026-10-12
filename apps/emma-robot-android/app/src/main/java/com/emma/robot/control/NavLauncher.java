package com.emma.robot.control;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;

/**
 * Starts and stops one leg of a walk on the robot itself (§2b, §2e): the app
 * runs `scripts/emma_nav.py` through su and keeps it on a tether. The launcher
 * on the robot owns the legs and the step numbers; this class only ever names
 * a place id, an origin and a run id, and never composes a step range.
 *
 * Threads (build review #4): MainActivity's `io` is ONE thread that also runs
 * driveLoop, the ~5 s arm connect and the <=15 s rest wait. A tether ping or a
 * STOP queued behind those would miss the launcher's 4 s tether (a false
 * app_lost) or stop seconds late. So the tether, the pings, the stop
 * escalation and the status poll each have their own thread here, and only
 * the launch itself -- which has to come after the arm rest -- is handed to
 * io (Host.runArmWork).
 */
final class NavLauncher {
    interface Host {
        boolean foreground();
        boolean operatorVisible();
        boolean bodyLatched();
        boolean bodyBusy();
        /** MainActivity's io: arm work stays there (§2b). */
        void runArmWork(Runnable work);
        /** On io: group 99, then wait for #AGF. */
        boolean restArms(long timeoutMs);
        /** Force-stop the vendor app and drop this app's SDK connection: two
         *  drivers on the wheels is the hazard the launcher must not join. */
        void releaseBase() throws Exception;
        /** DELETE /actions/:current on the board. */
        void deleteBoardAction();
        void changed();
    }

    static final String PREFS = BodyController.PREFS;
    static final String PREF_ENABLED = "nav_enabled";
    static final String PREF_REQUIRE_ARM_REST = "nav_require_arm_rest";

    private static final String TAG = "EmmaNav";
    static final String SU_BINARY = "/system/xbin/su";
    /** Installed and deployed from the PC by scripts/robot_python.py. */
    static final String REMOTE = "/data/local/tmp/emma-nav";
    static final String LAUNCHER = "scripts/emma_nav.py";
    /** The launcher's own exit code when the runtime is not on the robot. */
    static final int NOT_INSTALLED_EXIT = 86;

    static final long PING_MS = 1000;
    static final long POLL_MS = 5000;
    static final long PLACES_REFRESH_MS = 10000;
    /** A poll older than this is not knowledge: two missed polls. */
    static final long POLL_STALE_MS = 12000;
    static final long STOP_KILL_AFTER_MS = 2000;
    /** Review #28/#29: after the controller is killed, the launcher gets this
     *  long to write its own final status and clean up run.pid before it is
     *  killed itself. */
    static final long LAUNCHER_EXIT_GRACE_MS = 500;
    /** Review #29: a launcher that outlives even a kill through su is given up
     *  on after this long -- the Run is finished as stopped so the SafetyBar,
     *  "navigating" and "run_active" cannot stay pinned on a hung process. */
    static final long LAUNCHER_GIVE_UP_MS = 2000;
    static final long NAV_STOP_LATCH_MS = 60000;
    static final long REST_TIMEOUT_MS = 15000;
    static final long SHELL_TIMEOUT_MS = 6000;
    /** How long the operator screen waits for queued stops before it
     *  connects the SDK: escalate's worst case. Review #29 made escalate
     *  longer (grace, a kill through su, the give-up wait), and the old
     *  STOP_KILL_AFTER_MS + 4000 = 6 s could end while a hung launcher was
     *  still alive (integrator's check). Three su shells at their timeout,
     *  the waits between them, and 4 s for the board DELETE. */
    static final long STOP_DRAIN_MS = STOP_KILL_AFTER_MS + LAUNCHER_EXIT_GRACE_MS
            + LAUNCHER_GIVE_UP_MS + 3 * SHELL_TIMEOUT_MS + 4000;

    /** fullmatch, never find(): Java's `$` also matches before a final line
     *  terminator, and a trailing newline turns one id into two shell lines. */
    private static final Pattern PLACE_ID = Pattern.compile("[a-z][a-z0-9_]{1,31}");
    private static final Pattern RUN_ID = Pattern.compile("[0-9a-f]{32}");

    /** Runs only when the runtime and the launcher are deployed; exit 86 says
     *  they are not, so "not installed" is a fact, not a guess from a failure. */
    private static final String GUARD = "cd " + REMOTE + "/code 2>/dev/null && [ -x ../py ] && [ -f "
            + LAUNCHER + " ] || exit " + NOT_INSTALLED_EXIT + "; ";
    /** The pid in run.pid is a drive_route (a reused pid must never be signalled). */
    private static final String RUN_PID_IS_DRIVE_ROUTE = "p=$(cat " + REMOTE + "/run.pid 2>/dev/null) && kill -0 \"$p\" 2>/dev/null"
            + " && tr '\\0' ' ' < /proc/$p/cmdline | grep -q drive_route";
    /** emma_nav.py status when it is deployed; otherwise run.pid alone, so a
     *  PC `robot_python run` (it shares run.pid) is seen even before the
     *  launcher is deployed (§2c: no gestures during ANY live run). */
    private static final String POLL_SCRIPT =
            "if cd " + REMOTE + "/code 2>/dev/null && [ -x ../py ] && [ -f " + LAUNCHER + " ]; then exec ../py -u "
                    + LAUNCHER + " status; fi; " + RUN_PID_IS_DRIVE_ROUTE
                    + " && echo '{\"format\":1,\"state\":\"running\",\"origin\":\"pc\",\"reason\":\"pc_run\"}' && exit 0; exit "
                    + NOT_INSTALLED_EXIT;
    private static final String PLACES_SCRIPT = GUARD + "exec ../py -u " + LAUNCHER + " places";
    private static final String CHECK_SCRIPT = GUARD + "exec ../py -u " + LAUNCHER + " check";
    private static final String REAP_SCRIPT = GUARD + "exec ../py -u " + LAUNCHER + " reap";
    /** SIGINT via run.pid -- works for a robot_python run from the PC too. */
    private static final String STOP_SCRIPT =
            "if cd " + REMOTE + "/code 2>/dev/null && [ -x ../py ] && [ -f " + LAUNCHER + " ]; then ../py -u "
                    + LAUNCHER + " stop; else " + RUN_PID_IS_DRIVE_ROUTE + " && kill -INT \"$p\"; fi; exit 0";
    /** The board's velocity watchdog stops the wheels ~1 s after the
     *  controller dies, so a hung controller is killed rather than waited on. */
    private static final String KILL_SCRIPT = RUN_PID_IS_DRIVE_ROUTE + " && kill -9 \"$p\"; exit 0";

    private final Host host;
    private final SharedPreferences prefs;
    private final SecureRandom random = new SecureRandom();
    private final ExecutorService navStopper = Executors.newSingleThreadExecutor(
            runnable -> new Thread(runnable, "nav-stopper"));
    private final ScheduledExecutorService navPinger = Executors.newSingleThreadScheduledExecutor(
            runnable -> new Thread(runnable, "nav-pinger"));
    private final ScheduledExecutorService navPoller = Executors.newSingleThreadScheduledExecutor(
            runnable -> new Thread(runnable, "nav-poller"));
    /** §2b cancel token: the run id a stop was pressed for. "Accepted" for go
     *  means queued, and run.pid exists only once drive_route spawns, so a
     *  STOP during preparing/starting was a no-op and the leg still started
     *  after #AGF (safety review #2). */
    private final AtomicReference<String> cancelled = new AtomicReference<>(null);
    private final Object launchLock = new Object();

    private volatile long latchedUntil = 0L;
    private volatile Run run;                  // the current run, or the last one
    private volatile String cancelReason = "stop_pressed";
    private ScheduledFuture<?> pollTask;

    // the latest poll (nav-poller): any origin, including a PC run
    private volatile long polledAt = -1L;
    private volatile Boolean installed = null;  // null = not known yet
    private volatile JSONObject polled = null;
    private volatile boolean reportedWedged = false;   // review2 #25, nav-poller only

    // places cache (nav_caps body)
    private volatile JSONObject places = null;
    private volatile long placesAt = -1L;
    /** When `places` was last actually READ. placesAt also moves on a read
     *  that failed (it paces the retries), which would make an old answer
     *  look fresh to AutoReturn's "decided on a read at most 15 s old". */
    private volatile long placesReadAt = -1L;
    private final AtomicBoolean placesRefreshing = new AtomicBoolean(false);
    /** Review2 #5: bumped whenever a run ends. A refresh that STARTED before
     *  the bump may describe the walk that just ended (`here` from before it,
     *  or `run: running` if it was read mid-walk); its answer is thrown away
     *  and the refresh runs again instead of becoming the cache. */
    private final AtomicInteger placesGen = new AtomicInteger();
    /** Taken only around the cache write and the invalidation, nothing inside. */
    private final Object placesLock = new Object();
    private volatile String checkResult = "";

    private static final class Run {
        final String id;
        final String place;
        final String origin;
        volatile JSONObject status;
        Process tether;            // guarded by launchLock
        OutputStream stdin;        // guarded by launchLock
        /** Per run: the old run's reader reaches EOF after its terminal line,
         *  by which time a new run may already be pinging -- one shared handle
         *  would cancel the NEW run's pings and its launcher would stop the
         *  walk 4 s later as app_lost. */
        volatile ScheduledFuture<?> ping;
        /** The launcher's own pid, from its status lines (`launcher_pid`).
         *  The app cannot signal the su'd root process it started
         *  (destroyForcibly gets EPERM and swallows it, review #29), so a
         *  launcher that ignores "stop" is killed through su by this pid. */
        volatile int launcherPid = 0;

        Run(String id, String place, String origin) {
            this.id = id;
            this.place = place;
            this.origin = origin;
        }

        String state() {
            return status == null ? "idle" : status.optString("state", "idle");
        }

        boolean active() {
            String state = state();
            return "preparing".equals(state) || "starting".equals(state) || "running".equals(state);
        }

        boolean terminal() {
            String state = state();
            return "arrived".equals(state) || "stopped".equals(state) || "refused".equals(state)
                    || "failed".equals(state);
        }
    }

    NavLauncher(Context context, Host host) {
        this.host = host;
        this.prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // ---- robot-side switches (operator screen only) ---------------------------

    boolean enabled() {
        return prefs.getBoolean(PREF_ENABLED, false);
    }

    boolean requireArmRest() {
        return prefs.getBoolean(PREF_REQUIRE_ARM_REST, true);
    }

    void setEnabled(boolean value) {
        prefs.edit().putBoolean(PREF_ENABLED, value).apply();
        host.changed();
    }

    void setRequireArmRest(boolean value) {
        prefs.edit().putBoolean(PREF_REQUIRE_ARM_REST, value).apply();
    }

    boolean latched() {
        return SystemClock.elapsedRealtime() < latchedUntil;
    }

    /** The page re-reads the status when the latch runs out, so the
     *  stop_latch_s=0 frame reaches the server then and not on the next
     *  10 s idle beat. A later stop schedules its own; an early one firing
     *  while still latched changes nothing. */
    private void announceLatchEnd() {
        try {
            navPoller.schedule(() -> {
                if (!latched()) host.changed();
            }, NAV_STOP_LATCH_MS + 250, TimeUnit.MILLISECONDS);
        } catch (Exception ignored) {
            // shutting down: nobody is reading the status any more
        }
    }

    void clearLatch() {
        latchedUntil = 0L;
        host.changed();
    }

    // ---- what the gestures and the SafetyBar need ------------------------------

    private boolean ownRunActive() {
        Run current = run;
        return current != null && current.active();
    }

    private boolean pollFresh() {
        return polledAt >= 0 && SystemClock.elapsedRealtime() - polledAt < POLL_STALE_MS;
    }

    private boolean polledActive() {
        JSONObject status = polled;
        if (status == null || !pollFresh()) return false;
        String state = status.optString("state", "idle");
        return "preparing".equals(state) || "starting".equals(state) || "running".equals(state);
    }

    /** No gestures while navigating (§2c): this app's run, or a live run.pid
     *  of ANY origin (a PC robot_python run shares it). No fresh answer at all
     *  is nav_unknown -- we cannot tell, so we do not move. §2c says this for
     *  an installed runtime; it holds here whatever the install state, because
     *  the poll also reads run.pid, which a PC run writes before the launcher
     *  is ever deployed. Lock-free: BodyController asks holding its own lock. */
    String bodyBlock() {
        if (ownRunActive() || polledActive()) return "navigating";
        if (!pollFresh()) return "nav_unknown";
        return null;
    }

    // ---- what AutoReturn reads (UI thread, cached state only) --------------------

    /** This app's latest run, read lock-free (each field is one volatile read
     *  of a status that is replaced, never edited in place). */
    static final class RunView {
        final String id;
        final String place;
        final String origin;
        final String state;
        final String reason;

        RunView(String id, String place, String origin, String state, String reason) {
            this.id = id;
            this.place = place;
            this.origin = origin;
            this.state = state;
            this.reason = reason;
        }

        boolean active() {
            return "preparing".equals(state) || "starting".equals(state) || "running".equals(state);
        }

        boolean terminal() {
            return "arrived".equals(state) || "stopped".equals(state) || "refused".equals(state)
                    || "failed".equals(state);
        }
    }

    RunView ownRun() {
        Run current = run;
        if (current == null) return null;
        JSONObject status = current.status;
        String reason = status == null || status.isNull("reason") ? null : status.optString("reason", null);
        return new RunView(current.id, current.place, current.origin, current.state(), reason);
    }

    /** The launcher's places answer as cached (no app overlay), or null while
     *  none is loaded. Read only: places() copies before it adds anything. */
    JSONObject placesSnapshot() {
        return places;
    }

    /** How old that answer is, -1 with none. */
    long placesAgeMs() {
        long at = placesReadAt;
        return places == null || at < 0 ? -1L : SystemClock.elapsedRealtime() - at;
    }

    /** Every gate a go would meet, for AutoReturn's walk to `place`: the same
     *  goRefusal, with the auto origin. Starts nothing (it may ask for a
     *  places read, as a go would). */
    synchronized String autoRefusal(String place) {
        return goRefusal(place, NavRules.AUTO);
    }

    // ---- bridge answers (UI thread, cached state only) --------------------------

    /** Review #24: this app's own run wins over a poll of that SAME run. The
     *  poll is the run's status.json from up to POLL_MS earlier; when the
     *  launcher printed its terminal line, the poll still said "running", so
     *  the page re-read "running", sent no terminal nav_status, and arrival or
     *  failure reached the server one poll (5-12 s) late. The poll wins only
     *  when it describes a DIFFERENT run (a PC run carries no run_id). */
    String status() {
        Run current = run;
        JSONObject p = polled;
        JSONObject own = current == null ? null : current.status;
        boolean pollActive = polledActive() && p != null;
        String out;
        if (NavRules.ownStatusWins(own != null, current != null && current.active(), pollActive,
                current == null ? null : current.id,
                p == null || p.isNull("run_id") ? null : p.optString("run_id", null))) {
            out = own.toString();
        } else if (pollActive) {
            out = p.toString();
        } else {
            out = "{\"format\":1,\"state\":\"idle\",\"run_id\":null,\"reason\":null}";
        }
        // The status link reports this between calls, and the server opens
        // the wheel fence on "idle": say whether the poll can vouch for it --
        // on EVERY status, false included. An APK from before the flag sent
        // nothing when fresh and nothing when stale, so the server and the
        // page treat a status without it as "cannot tell" (the link is not
        // even opened for such an app); only an explicit false lets a link's
        // idle count (adversarial review, 9 Oct).
        boolean stale = NavRules.statusPollStale(current != null && current.active(), pollFresh());
        // stop_latch_s (review, 10 Oct): the 60 s latch every stop path sets
        // holds this app's walks and gestures; the server's own motion (the
        // greeter's FACE_TURN_TO_CHECK base turn) could not see it. Seconds
        // left, 0 = none; a frame with it reaches the server at once on a
        // stop (the page keys on it) and at the latch's end (stop()).
        int latch = NavRules.latchSeconds(latchedUntil, SystemClock.elapsedRealtime());
        try {
            return new JSONObject(out).put("poll_stale", stale).put("stop_latch_s", latch).toString();
        } catch (Exception e) {
            return out;   // no flag: the server reads a link's idle as "cannot tell"
        }
    }

    /** Doors of one places entry (the launcher's card), 0 when it names none. */
    private static int doorCount(JSONObject place) {
        JSONArray doors = place == null ? null : place.optJSONArray("doors");
        return doors == null ? 0 : doors.length();
    }

    String places() {
        JSONObject cached = places;
        if (enabled() && (cached == null || SystemClock.elapsedRealtime() - placesAt > PLACES_REFRESH_MS)) {
            refreshPlaces();
        }
        try {
            if (cached == null && !enabled()) {
                return new JSONObject().put("enabled", false)
                        .put("installed", installed == null ? JSONObject.NULL : installed)
                        .put("places", new JSONArray()).toString();
            }
            if (cached == null) return "{\"loading\":true}";
            JSONObject caps = new JSONObject(cached.toString());
            caps.put("enabled", enabled());
            // the confirm sheet promised "หุ่นจะเก็บแขนลงก่อนออกเดิน" with the
            // switch off too (owner, 9 Oct) -- the page reads the switch here
            caps.put("arm_rest", requireArmRest());
            String overlay = null;
            if (!enabled()) overlay = "disabled";
            else if (latched()) overlay = "nav_latched";
            else if (ownRunActive() || polledActive()) overlay = "run_active";
            else if (host.bodyLatched()) overlay = "body_latched";
            JSONArray list = caps.optJSONArray("places");
            if (overlay != null && list != null) {
                for (int i = 0; i < list.length(); i++) {
                    JSONObject place = list.optJSONObject(i);
                    if (place != null && place.optBoolean("available", false)) {
                        place.put("available", false).put("reason", overlay);
                    }
                }
            }
            // "เก็บแขนก่อนเดิน" off no longer greys out door legs: the operator
            // vouches for the arms (NavRules.operatorVouchesArms, owner 9 Oct).
            return caps.toString();
        } catch (Exception e) {
            return "{\"loading\":true}";
        }
    }

    /** emma-nav:go -- the cheap gates on cached state, in order; on accept the
     *  run id exists BEFORE the prompt returns, so the page can hand it to the
     *  server in nav_ack and the server can tell its own run from a stranger's. */
    synchronized String go(String place, String origin) {
        // tap/voice from the bridge (MainActivity.navGo lets the page ask for
        // nothing else), auto from AutoReturn only
        if (place == null || !PLACE_ID.matcher(place).matches() || !NavRules.goOrigin(origin)) {
            return ack(false, "bad_request", null);
        }
        String reason = goRefusal(place, origin);
        if (reason != null) return ack(false, reason, null);
        final Run next = new Run(newRunId(), place, origin);
        cancelled.set(null);
        run = next;
        setStatus(next, "preparing", null);
        host.runArmWork(() -> launch(next));
        return ack(true, null, next.id);
    }

    private String goRefusal(String place, String origin) {
        if (!enabled()) return "disabled";
        if (Boolean.FALSE.equals(installed)) return "not_installed";
        if (host.operatorVisible()) return "operator_screen";
        if (!host.foreground()) return "not_foreground";
        if (latched()) return "nav_latched";
        if (ownRunActive() || polledActive()) return "run_active";
        // Review 12 Oct: a stale run.pid poll (just resumed, su wedged) means
        // we cannot tell whether something is already walking -- gestures
        // refuse it (bodyBlock: nav_unknown), a walk did not, and launch()
        // rests the arms and stops the vendor app before the launcher's own
        // run_active check. launcher_failed is the not-ready code this method
        // already answers (no places read yet); nav_unknown is not a nav code.
        if (!pollFresh()) return "launcher_failed";
        if (host.bodyLatched()) return "body_latched";
        if (host.bodyBusy()) return "body_busy";
        JSONObject cached = places;
        // Review #8: a stale places file comes back as places=[] plus the
        // launcher's own top-level reason. Scanning that empty list answered
        // unknown_place ("ไม่รู้จักจุดหมายนี้") to a voice go, when the truth is
        // "reinstall from the computer" -- the same text the page now shows.
        if (cached != null && "places_stale".equals(cached.optString("reason", ""))) return "places_stale";
        JSONArray list = cached == null ? null : cached.optJSONArray("places");
        if (list == null) {
            // Review2 #17: no places read yet (app just started, or the cache
            // was dropped because a walk just ended) is "not ready", which
            // places() itself answers as {loading:true}. unknown_place made a
            // voice go say "ไม่รู้จักจุดหมายนี้" about a place the robot knows.
            // launcher_failed is the closest contract code that is not a
            // claim about the place ("เริ่มระบบนำทางไม่สำเร็จ"); a dedicated
            // loading code would need app/robot_contracts.py and the page too.
            refreshPlaces();
            return "launcher_failed";
        }
        for (int i = 0; i < list.length(); i++) {
            JSONObject entry = list.optJSONObject(i);
            if (entry == null || !place.equals(entry.optString("id"))) continue;
            if (!entry.optBoolean("available", false)) {
                String why = entry.optString("reason", "");
                return why.isEmpty() ? "no_leg" : why;
            }
            // the launcher checks this too; refusing here keeps a voice walk
            // on an unverified leg from even resting the arms. "auto" goes
            // like "tap" (NavRules.needsVerifiedLeg), never like "voice".
            if (NavRules.needsVerifiedLeg(origin) && !entry.optBoolean("verified", false)) return "leg_unverified";
            return null;
        }
        return "unknown_place";
    }

    /** 32 hex from SecureRandom, the shape of RUN_ID_RE. */
    private String newRunId() {
        byte[] bytes = new byte[16];
        random.nextBytes(bytes);
        StringBuilder hex = new StringBuilder(32);
        for (byte b : bytes) hex.append(String.format("%02x", b & 0xff));
        return hex.toString();
    }

    /** The go command line, built ONLY from constants plus a validated place
     *  id, origin and run id. No step numbers: the launcher owns the legs.
     *
     *  Review2 #7: `--arms-rested` is the fact "group 99 played and its #AGF
     *  came back", and only that. The launcher used to pass drive_route
     *  --arms-in always (the 0.70 m base instead of the 0.78 m arms width in
     *  doorways and spot turns), but with "เก็บแขนก่อนเดิน" switched off the
     *  app skipped the rest and an arm left out (a STOP mid-handshake, an
     *  operator pose) went through D8/D11 on a margin computed for arms in.
     *  Without the flag the launcher decides: the arms width, or refuses door
     *  legs (arms_not_rested). */
    static String[] goCommand(String place, String origin, String runId, boolean armsRested) {
        if (place == null || !PLACE_ID.matcher(place).matches()) throw new IllegalArgumentException("place");
        if (!NavRules.goOrigin(origin)) throw new IllegalArgumentException("origin");
        if (runId == null || !RUN_ID.matcher(runId).matches()) throw new IllegalArgumentException("run id");
        return new String[]{SU_BINARY, "0", "sh", "-c",
                GUARD + "exec ../py -u " + LAUNCHER + " go " + place + " --origin " + origin
                        + " --run-id " + runId + " --tether" + (armsRested ? " --arms-rested" : "")};
    }

    /** On io, after the go prompt returned. */
    private void launch(Run next) {
        if (isCancelled(next)) {
            finish(next, "stopped", cancelReason);
            return;
        }
        // review2 #7: with the switch on, true only after a rest this launch
        // actually saw finish; with it off, the operator's word (NavRules).
        boolean armsRested = NavRules.operatorVouchesArms(requireArmRest());
        if (requireArmRest()) {
            if (!host.restArms(REST_TIMEOUT_MS)) {
                if (isCancelled(next)) finish(next, "stopped", cancelReason);
                else finish(next, "refused", "arms_not_rest");
                return;
            }
            armsRested = true;
        }
        // the cancel token, after the rest: a STOP pressed while the arms
        // rested must leave nothing spawned
        if (isCancelled(next)) {
            finish(next, "stopped", cancelReason);
            return;
        }
        try {
            host.releaseBase();
        } catch (Exception e) {
            Log.e(TAG, "could not free the base before the walk", e);
            finish(next, "failed", "launcher_failed");
            return;
        }
        // a STOP may already have finished this run as stopped while io was
        // blocked above (review #32): "starting" must not revive it
        synchronized (next) {
            if (next.terminal()) return;
            setStatus(next, "starting", null);
        }
        synchronized (launchLock) {
            // ...and immediately before the su exec, under the lock the
            // stopper takes to find the tether: either the stop is seen here,
            // or the stopper sees the tether and sends it "stop"
            if (isCancelled(next)) {
                finish(next, "stopped", cancelReason);
                return;
            }
            try {
                Process process = new ProcessBuilder(goCommand(next.place, next.origin, next.id, armsRested))
                        .redirectErrorStream(true)
                        .start();
                next.tether = process;
                next.stdin = process.getOutputStream();
            } catch (Exception e) {
                Log.e(TAG, "launcher did not start", e);
                finish(next, "failed", "launcher_failed");
                return;
            }
        }
        // Review #31: the ping is registered BEFORE the reader starts. A
        // launcher that ends at once (run_active, not_installed, a bad place)
        // let the reader reach stopPinging while `ping` was still null; the
        // ping scheduled after that was never cancelled -- one dead 1 Hz task
        // per fast-failing run. ping() holds launchLock and checks stdin, so
        // an early tick is safe.
        next.ping = navPinger.scheduleWithFixedDelay(() -> ping(next), 0, PING_MS, TimeUnit.MILLISECONDS);
        Thread reader = new Thread(() -> readTether(next), "nav-reader");
        reader.setDaemon(true);
        reader.start();
    }

    private boolean isCancelled(Run which) {
        String token = cancelled.get();
        return token != null && (token.isEmpty() || token.equals(which.id));
    }

    /** nav-pinger: the launcher stops the walk after 4 s without one (app_lost). */
    private void ping(Run which) {
        synchronized (launchLock) {
            if (which.stdin == null) return;
            try {
                which.stdin.write("ping\n".getBytes(StandardCharsets.US_ASCII));
                which.stdin.flush();
            } catch (Exception ignored) {
                // the reader sees EOF and ends the run
            }
        }
    }

    /** nav-reader: one status JSON line per change and every second. */
    private void readTether(Run which) {
        Process process;
        synchronized (launchLock) {
            process = which.tether;
        }
        if (process == null) return;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                JSONObject status = parse(line);
                // only lines that echo THIS run's id; anything else (a stray
                // print, an old run) never becomes this run's status
                if (status == null || !which.id.equals(status.optString("run_id"))) continue;
                merge(which, status);
            }
        } catch (Exception e) {
            Log.w(TAG, "tether read ended", e);
        }
        int exit = -1;
        try {
            exit = process.waitFor();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        stopPinging(which);
        synchronized (launchLock) {
            which.stdin = null;
        }
        if (!which.terminal()) {
            if (exit == NOT_INSTALLED_EXIT) finish(which, "refused", "not_installed");
            else if (isCancelled(which)) finish(which, "stopped", cancelReason);
            else finish(which, "failed", "launcher_lost");
        }
        refreshPlaces();
    }

    private void merge(Run which, JSONObject status) {
        synchronized (which) {     // the same lock finish() takes
            // Review #29 (integrator's check): endLauncher may give up on a
            // hung launcher and finish its Run as stopped. A late line from
            // that same launcher used to set it back to "running" -- the
            // SafetyBar, "navigating" and "run_active" pinned again on a
            // process we had already written off. A terminal Run stays ended.
            if (which.terminal()) return;
            JSONObject before = which.status;
            int pid = status.optInt("launcher_pid", 0);
            if (pid > 1) which.launcherPid = pid;
            try {
                status.put("format", 1).put("run_id", which.id).put("place", which.place)
                        .put("origin", which.origin);
                // Review #26: the tether only carries the word "stop", which the
                // launcher reports as stop_pressed. When this app stopped the run
                // for another reason (it went to the background, the operator
                // screen opened, the app is closing), the turn log, the panel and
                // the spoken "กดหยุดแล้ว" claimed somebody pressed STOP.
                String launcherReason = status.isNull("reason") ? null : status.optString("reason", null);
                String why = NavRules.stopReason(status.optString("state"), launcherReason,
                        isCancelled(which), cancelReason);
                if (why != null && !why.equals(launcherReason)) status.put("reason", why);
            } catch (Exception ignored) {
                // fields stay as the launcher wrote them
            }
            which.status = status;
            boolean changed = before == null
                    || !before.optString("state").equals(status.optString("state"))
                    || before.optInt("step", -1) != status.optInt("step", -1)
                    || !before.optString("reason").equals(status.optString("reason"));
            // review2 #5: the launcher's own terminal line ends the run here
            // (finish() is not called for it) -- the places go stale first
            boolean ended = which.terminal();
            if (ended) invalidatePlaces();
            if (changed) host.changed();
            if (ended) refreshPlaces();
        }
    }

    private void setStatus(Run which, String state, String reason) {
        try {
            JSONObject status = new JSONObject()
                    .put("format", 1)
                    .put("run_id", which.id)
                    .put("origin", which.origin)
                    .put("place", which.place)
                    .put("state", state)
                    .put("reason", reason == null ? JSONObject.NULL : reason)
                    .put("detail", JSONObject.NULL)
                    .put("t", System.currentTimeMillis() / 1000.0);
            JSONObject previous = which.status;
            if (previous != null) {
                for (String key : new String[]{"step", "step_from", "step_to", "do", "label", "door", "pose", "from"}) {
                    if (previous.has(key)) status.put(key, previous.get(key));
                }
            }
            which.status = status;
        } catch (Exception ignored) {
            // keep the previous status
        }
        // review2 #5: BEFORE the page hears the terminal state. host.changed
        // makes the page force a nav_caps at once, and that frame used to
        // carry the cache from before the walk (`here` = where it started,
        // the old availability) or from mid-walk (`run: running`, which put
        // the server back to "active" with no way down).
        if (which.terminal()) invalidatePlaces();
        host.changed();
    }

    private void finish(Run which, String state, String reason) {
        synchronized (which) {     // the launch thread and the reader can both reach here
            if (which.terminal()) return;
            setStatus(which, state, reason);
        }
        refreshPlaces();
    }

    private static void stopPinging(Run which) {
        ScheduledFuture<?> task = which.ping;
        if (task != null) task.cancel(false);
    }

    // ---- stop (§4 "STOP itself causing a retry") ---------------------------------

    /**
     * Every stop path: SafetyBar, gamepad B, emma-nav:stop, onPause, focus
     * loss, the operator screen, onDestroy. Step 0 happens HERE, on the
     * calling thread; steps 1-4 on nav-stopper, never on io.
     */
    boolean stop(String reason, boolean explicit) {
        final Run current = run;
        // (0) the cancel token and the nav latch, synchronously: a launch
        // still queued behind the arm rest never starts
        cancelReason = reason == null ? "stop_pressed" : reason;
        cancelled.set(current == null ? "" : current.id);
        latchedUntil = SystemClock.elapsedRealtime() + NAV_STOP_LATCH_MS;
        announceLatchEnd();
        final boolean wasActive = (current != null && current.active()) || polledActive();
        // Review #32: a run with no tether yet (preparing, or io blocked in
        // su/am before the exec) is finished HERE. Only launch() and the
        // tether reader ever finished a Run, so a hung su kept it "preparing"
        // -- SafetyBar pinned, gestures refused, go refused -- and STOP did not
        // clear it. launch() re-checks the token under this same lock before
        // the exec, and finish() is a no-op on a finished run.
        if (current != null) {
            synchronized (launchLock) {
                if (current.stdin == null && current.tether == null) {
                    finish(current, "stopped", cancelReason);
                }
            }
        }
        host.changed();
        try {
            navStopper.execute(() -> escalate(current, explicit || wasActive));
        } catch (Exception e) {
            Log.w(TAG, "stop escalation not queued", e);
        }
        // the caller (B, the STOP button) leaves the board DELETE to
        // escalate's step 4 when a walk was live (review #21)
        return wasActive;
    }

    /** nav-stopper. The order matters: DELETE /actions/:current alone looks
     *  like a link drop to drive_route, which then REDOES the step
     *  (recover_link, up to 180 s) -- so the controller is stopped first and
     *  the board action is deleted last. */
    private void escalate(Run current, boolean full) {
        // (1) the tether's own "stop": emma_nav.py reads stdin from its first
        // line, so this also aborts a preflight before anything spawns
        Process tether = null;
        synchronized (launchLock) {
            if (current != null && current.stdin != null) {
                tether = current.tether;
                try {
                    current.stdin.write("stop\n".getBytes(StandardCharsets.US_ASCII));
                    current.stdin.flush();
                } catch (Exception ignored) {
                    // a dead pipe is the launcher's app_lost path anyway
                }
            }
        }
        // (2) SIGINT via run.pid (emma_nav.py stop)
        runShell(STOP_SCRIPT, SHELL_TIMEOUT_MS);
        if (!full && tether == null) return;
        // (3) still alive after STOP_KILL_AFTER_MS: kill -9 the controller
        SystemClock.sleep(STOP_KILL_AFTER_MS);
        runShell(KILL_SCRIPT, SHELL_TIMEOUT_MS);
        if (tether != null) endLauncher(current, tether);
        // (4) then the board's current action
        host.deleteBoardAction();
        refreshPlaces();
    }

    /** Review #28/#29, on nav-stopper. First a short wait, so the launcher
     *  writes its own final status and cleans up run.pid; it used to be
     *  force-destroyed at once, mid-cleanup. Then, if it is still
     *  there, a kill through su by its own pid: destroyForcibly() on the su'd
     *  root child gets EPERM and swallows it, so it was a no-op. Last, a Run
     *  whose launcher STILL lives is finished as stopped, so nothing stays
     *  pinned on it; its pings stop, and a launcher that is somehow alive ends
     *  itself on the lost tether (app_lost). */
    private void endLauncher(Run current, Process tether) {
        if (waitExit(tether, LAUNCHER_EXIT_GRACE_MS)) return;
        int pid = current == null ? 0 : current.launcherPid;
        if (pid > 1) runShell(NavRules.killLauncherScript(pid), SHELL_TIMEOUT_MS);
        tether.destroyForcibly();
        if (waitExit(tether, LAUNCHER_GIVE_UP_MS)) return;
        Log.w(TAG, "launcher " + pid + " outlived stop and kill; the run is ended here");
        if (current != null) {
            stopPinging(current);
            finish(current, "stopped", cancelReason);
        }
    }

    private static boolean waitExit(Process process, long timeoutMs) {
        try {
            return process.waitFor(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return !process.isAlive();
        }
    }

    /** Waits until queued stops have run (the operator screen connects the
     *  SDK only after a walk it interrupted has been stopped). */
    void awaitStopped(long timeoutMs) {
        try {
            navStopper.submit(() -> { }).get(timeoutMs, TimeUnit.MILLISECONDS);
        } catch (Exception ignored) {
            // the operator screen still opens; its own chassis stop follows
        }
    }

    // ---- nav-poller -------------------------------------------------------------

    void resume() {
        synchronized (this) {
            if (pollTask != null) return;
            pollTask = navPoller.scheduleWithFixedDelay(this::poll, 0, POLL_MS, TimeUnit.MILLISECONDS);
        }
        if (enabled()) refreshPlaces();
    }

    void pause() {
        synchronized (this) {
            if (pollTask != null) pollTask.cancel(false);
            pollTask = null;
        }
    }

    /** On every app start (§2e): an orphan of a dead app launcher is
     *  interrupted, a leftover board action of a dead run deleted; a PC run is
     *  left alone. Not `emma_nav.py stop`, which would SIGINT a legitimate PC
     *  run every time the app restarts (review B14). */
    void reapOnStart() {
        navPoller.execute(() -> runShell(REAP_SCRIPT, SHELL_TIMEOUT_MS, true));
    }

    private void poll() {
        Shell result = runShell(POLL_SCRIPT, SHELL_TIMEOUT_MS, true);
        JSONObject status = result.exit == 0 ? parse(firstJsonLine(result.out)) : null;
        boolean wasActive = polledActive();
        if (result.exit == NOT_INSTALLED_EXIT) {
            installed = false;
            polled = null;
            polledAt = SystemClock.elapsedRealtime();
        } else if (status != null) {
            if (!"pc".equals(status.optString("origin"))) installed = true;
            polled = status;
            polledAt = SystemClock.elapsedRealtime();
        }
        // anything else (timeout, no su, unparseable) leaves the poll to go
        // stale, and a stale poll blocks gestures (nav_unknown)
        boolean nowActive = polledActive();
        // review2 #5: a run seen only by the poll (a PC robot_python run)
        // ending is the same moment for the places as one of ours ending
        if (wasActive && !nowActive) invalidatePlaces();
        // review2 #25: the operator screen's "su ไม่ตอบสนอง" line follows it
        boolean wedgedNow = suWedged();
        boolean wedgeChanged = wedgedNow != reportedWedged;
        reportedWedged = wedgedNow;
        if (wasActive != nowActive || wedgeChanged) host.changed();
        if (wasActive && !nowActive) refreshPlaces();
    }

    /** Review2 #5: the cache describes the robot before (or during) a walk
     *  that has just ended. places() answers {loading:true} until a refresh
     *  made after the end lands -- the page retries every second -- and a
     *  voice go waits for it too (goRefusal). */
    private void invalidatePlaces() {
        synchronized (placesLock) {
            placesGen.incrementAndGet();
            places = null;
            placesAt = -1L;
        }
    }

    void refreshPlaces() {
        if (!placesRefreshing.compareAndSet(false, true)) return;
        try {
            navPoller.execute(() -> {
                final int gen = placesGen.get();
                boolean again = false;
                try {
                    Shell result = runShell(PLACES_SCRIPT, SHELL_TIMEOUT_MS, true);
                    boolean missing = result.exit == NOT_INSTALLED_EXIT;
                    JSONObject fresh = missing
                            ? new JSONObject().put("installed", false).put("places", new JSONArray())
                                    .put("here", JSONObject.NULL).put("battery", JSONObject.NULL)
                            : parse(firstJsonLine(result.out));
                    // the generation check and the write under one lock: an
                    // end landing between them would otherwise be overwritten
                    synchronized (placesLock) {
                        if (gen != placesGen.get()) {
                            // review2 #5: a run ended while this one was out; its
                            // answer may be the pre-walk `here` or `run: running`
                            again = true;
                        } else {
                            if (fresh != null) {
                                installed = !missing;
                                places = fresh;
                                placesReadAt = SystemClock.elapsedRealtime();
                            }
                            placesAt = SystemClock.elapsedRealtime();
                        }
                    }
                } catch (Exception e) {
                    Log.w(TAG, "places refresh failed", e);
                } finally {
                    placesRefreshing.set(false);
                    if (again) refreshPlaces();
                    // review2 #5: the page re-reads the caps on this event; a
                    // changed caps text after a run ended goes to the server
                    // at once, not on the next 30 s tick
                    host.changed();
                }
            });
        } catch (Exception e) {
            placesRefreshing.set(false);
        }
    }

    /** Review2 #25: for the operator screen -- su has stopped answering. */
    boolean suWedged() {
        return NavRules.suWedgeBlocks(liveWedged(), lastWedgeAt, SystemClock.elapsedRealtime()) != null;
    }

    /** "ตรวจระบบนำทาง (ไม่ขยับ)": emma_nav.py check, read-only. */
    void check(Runnable done) {
        navPoller.execute(() -> {
            Shell result = runShell(CHECK_SCRIPT, SHELL_TIMEOUT_MS * 2, true);
            if (result.exit == NOT_INSTALLED_EXIT) {
                checkResult = "ยังไม่ได้ติดตั้งระบบนำทางบนหุ่น (ติดตั้งจากคอมพิวเตอร์ก่อน)";
            } else {
                JSONObject json = parse(firstJsonLine(result.out));
                String deployed = json == null ? "" : json.optString("deployed_at",
                        json.optJSONObject("manifest") == null ? ""
                                : json.optJSONObject("manifest").optString("deployed_at", ""));
                checkResult = "exit " + result.exit + (deployed.isEmpty() ? "" : " · deployed_at " + deployed)
                        + "\n" + (json == null ? result.out : json.toString());
            }
            done.run();
        });
    }

    String checkResult() {
        return checkResult;
    }

    void shutdown() {
        pause();
        navPoller.shutdownNow();
        navPinger.shutdownNow();
        navStopper.shutdown();     // a queued stop still runs
    }

    // ---- su -------------------------------------------------------------------------

    static final class Shell {
        final int exit;
        final String out;

        Shell(int exit, String out) {
            this.exit = exit;
            this.out = out;
        }
    }

    /** ServerLauncher's su: the same bounded call and the same review2 #25
     *  wedge accounting as the nav poll, so the app keeps ONE su path. A
     *  wedged su then backs the server watchdog off too, instead of it
     *  growing stuck root children of its own every 30 s. */
    static Shell runPeriodic(String script, long timeoutMs) {
        return runShell(script, timeoutMs, true);
    }

    /** Review2 #25: su children that outlived their timeout. destroyForcibly()
     *  on a root child gets EPERM (review #29), so each one stays alive with a
     *  process-reaper thread blocked in waitpid; a poll every ~11 s and a
     *  places refresh every 10 s against a wedged su grew ~330 of them an
     *  hour in the kiosk until fork or memory failed -- and an app crash takes
     *  the SafetyBar with it. Guarded by itself. */
    private static final ArrayList<Process> wedged = new ArrayList<>();
    private static volatile long lastWedgeAt = 0L;

    private static int liveWedged() {
        synchronized (wedged) {
            wedged.removeIf(p -> !p.isAlive());
            return wedged.size();
        }
    }

    private static void noteWedged(Process process) {
        // a plain sh child might still die from destroyForcibly; a root one will not
        if (waitExit(process, 200)) return;
        synchronized (wedged) {
            wedged.add(process);
            lastWedgeAt = SystemClock.elapsedRealtime();
        }
        Log.w(TAG, "su child outlived its timeout and cannot be killed from the app uid (" + liveWedged()
                + " stuck); periodic su calls back off");
    }

    /** The stop path: never skipped, a wedged su or not (a stop is not refused). */
    private static Shell runShell(String script, long timeoutMs) {
        return runShell(script, timeoutMs, false);
    }

    /** One fixed script through su, bounded in time. Output is read after the
     *  exit: every launcher answer is a single JSON object well under the
     *  64 KiB a pipe holds (MAX_INBOUND_BYTES is 8 KiB end to end).
     *
     *  periodic (poll, places, check, reap): review2 #25 -- while su children
     *  are stuck, these do not spawn another one (NavRules.suWedgeBlocks): one
     *  retry a minute, none at all once MAX_WEDGED_SU are stuck. A skipped poll
     *  goes stale like a timed-out one, so gestures read nav_unknown -- the
     *  same "cannot tell, so do not move" -- and the operator screen says su
     *  is stuck so staff reboot the robot. */
    private static Shell runShell(String script, long timeoutMs, boolean periodic) {
        if (periodic && NavRules.suWedgeBlocks(liveWedged(), lastWedgeAt, SystemClock.elapsedRealtime()) != null) {
            return new Shell(-1, "");
        }
        Process process = null;
        try {
            process = new ProcessBuilder(SU_BINARY, "0", "sh", "-c", script)
                    .redirectErrorStream(true)
                    .start();
            process.getOutputStream().close();
            if (!process.waitFor(timeoutMs, TimeUnit.MILLISECONDS)) {
                process.destroyForcibly();
                noteWedged(process);
                return new Shell(-1, "");
            }
            StringBuilder out = new StringBuilder();
            try (InputStream stream = process.getInputStream();
                 BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) out.append(line).append('\n');
            }
            return new Shell(process.exitValue(), out.toString());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            if (process != null) process.destroyForcibly();
            return new Shell(-1, "");
        } catch (Exception e) {
            Log.w(TAG, "su script failed", e);
            return new Shell(-1, "");
        }
    }

    private static String firstJsonLine(String out) {
        if (out == null) return null;
        for (String line : out.split("\n")) {
            String trimmed = line.trim();
            if (trimmed.startsWith("{")) return trimmed;
        }
        return null;
    }

    private static JSONObject parse(String line) {
        if (line == null) return null;
        try {
            String trimmed = line.trim();
            return trimmed.startsWith("{") ? new JSONObject(trimmed) : null;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String ack(boolean accepted, String reason, String runId) {
        try {
            return new JSONObject()
                    .put("accepted", accepted)
                    .put("reason", reason == null ? JSONObject.NULL : reason)
                    .put("run_id", runId == null ? JSONObject.NULL : runId)
                    .toString();
        } catch (Exception e) {
            return "{\"accepted\":false,\"reason\":\"bad_request\",\"run_id\":null}";
        }
    }
}
