package com.emma.robot.control;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * NavLauncher's decisions that need no Android: kept here so the tests can
 * COMPILE and RUN them with the plain JDK (the BodyGestures pattern), instead
 * of only finding their text.
 */
final class NavRules {
    private NavRules() {
    }

    // ---- who started a leg (app/robot_contracts.py NAV_ORIGINS) -------------------

    static final String TAP = "tap";
    static final String VOICE = "voice";
    /** The app's own walk back to the dock after idle (AutoReturn, owner
     *  10 Oct 2026: "ทำงานเสร็จก็ให้เข้าไปชาร์จที่แท่น"). */
    static final String AUTO = "auto";
    /** robot_contracts.NAV_ORIGINS, in its order (a test compares the two). */
    static final List<String> ORIGINS = Collections.unmodifiableList(Arrays.asList(TAP, VOICE, AUTO));

    /** Whether go()/goCommand may start a leg for this origin. */
    static boolean goOrigin(String origin) {
        return origin != null && ORIGINS.contains(origin);
    }

    /** What the face page may ask for (emma-nav:go:<place>:tap|voice). Never
     *  "auto": a page -- any page the trust gate lets in -- cannot start the
     *  app's own walk; only AutoReturn can, after its warning was on screen. */
    static boolean pageOrigin(String origin) {
        return TAP.equals(origin) || VOICE.equals(origin);
    }

    /** An unverified leg is refused to a voice start only. "auto" goes like
     *  "tap" (staff switched it on, the face warns 10 s first), never like
     *  "voice" -- the launcher's plan_leg makes the same call. */
    static boolean needsVerifiedLeg(String origin) {
        return VOICE.equals(origin);
    }

    /** Whole seconds left of the 60 s stop latch, 0 when none: the status
     *  carries it (stop_latch_s) so server-side motion -- the greeter's
     *  FACE_TURN_TO_CHECK -- holds still through it too (review, 10 Oct). */
    static int latchSeconds(long latchedUntil, long now) {
        long left = latchedUntil - now;
        return left <= 0 ? 0 : (int) Math.min(Integer.MAX_VALUE, (left + 999) / 1000);
    }

    // ---- auto-return to the dock (AutoReturn) ---------------------------------------

    /** The idle time, operator-adjustable on the robot in AUTO_IDLE_STEP_S
     *  steps. Never below a minute: shorter walks away from a guest who only
     *  paused to look around the room. */
    static final int AUTO_IDLE_DEFAULT_S = 90;
    static final int AUTO_IDLE_MIN_S = 60;
    static final int AUTO_IDLE_MAX_S = 600;
    static final int AUTO_IDLE_STEP_S = 30;
    /** The banner with "อยู่ต่อ" is on the face this long before the robot
     *  leaves -- counted from the page's FIRST confirmation that it is
     *  showing it, not from when the app asked. */
    static final long AUTO_WARN_MS = 10000L;
    /** The page has this long to put the warning on screen; no page, no walk. */
    static final long AUTO_CONFIRM_MS = 3000L;
    /** ...and must keep saying so: the page re-confirms every second. */
    static final long AUTO_SEEN_MS = 2500L;
    /** The places read the walk is decided on is at most this old. */
    static final long AUTO_CAPS_FRESH_MS = 15000L;

    static final String STEP_COUNT = "count";
    static final String STEP_WARN = "warn";
    static final String STEP_WAIT = "wait";
    static final String STEP_GO = "go";
    static final String STEP_NOT_SHOWN = "not_shown";

    /** A stored value outside the range (an old pref, a hand edit) never
     *  shortens the wait below the minimum. */
    static int clampIdleS(int seconds) {
        return Math.max(AUTO_IDLE_MIN_S, Math.min(AUTO_IDLE_MAX_S, seconds));
    }

    static int stepIdleS(int seconds, int direction) {
        return clampIdleS(clampIdleS(seconds) + Integer.signum(direction) * AUTO_IDLE_STEP_S);
    }

    /** Whether a run of this app that has just ended arms the countdown: it
     *  ARRIVED (a stopped or failed walk is staff's to look at, not ours to
     *  undo), it was somebody's walk -- tap or voice; the auto walk itself
     *  never re-arms -- and it ended somewhere that is not the dock. */
    static boolean armsAutoReturn(String state, String origin, String place, String dockId) {
        return "arrived".equals(state) && (TAP.equals(origin) || VOICE.equals(origin))
                && place != null && !place.equals(dockId);
    }

    /**
     * The countdown at `now` (all times SystemClock.elapsedRealtime, 0 = not
     * yet). `dueAt` = last sign of somebody + the idle time. Before
     * dueAt - AUTO_WARN_MS: count. Then the warning starts (warn), the page
     * must confirm it within AUTO_CONFIRM_MS and keep confirming at least
     * every AUTO_SEEN_MS (else not_shown: no walk, the count restarts), and
     * the walk is due AUTO_WARN_MS after the first confirmation (go).
     */
    static String autoStep(long now, long dueAt, long warnStartedAt, long shownAt, long seenAt) {
        if (warnStartedAt <= 0) return now >= dueAt - AUTO_WARN_MS ? STEP_WARN : STEP_COUNT;
        if (shownAt <= 0) return now - warnStartedAt > AUTO_CONFIRM_MS ? STEP_NOT_SHOWN : STEP_WAIT;
        if (now - seenAt > AUTO_SEEN_MS) return STEP_NOT_SHOWN;
        return now - shownAt >= AUTO_WARN_MS ? STEP_GO : STEP_WAIT;
    }

    /** Whole seconds until the walk, for the banner and the operator screen. */
    static int autoLeftS(long now, long dueAt, long warnStartedAt, long shownAt) {
        long at = warnStartedAt <= 0 ? dueAt : (shownAt <= 0 ? now + AUTO_WARN_MS : shownAt + AUTO_WARN_MS);
        long left = at - now;
        return left <= 0 ? 0 : (int) Math.min(Integer.MAX_VALUE, (left + 999) / 1000);
    }

    /**
     * Why the walk back may not start, from the places card list (the
     * launcher's caps), or null. Strict: anything it cannot read is no.
     * `dockAvailable`/`dockReason` are the dock card's (the launcher ran
     * plan_leg for it: location, leg, battery); goRefusal adds every gate of
     * a go after this.
     */
    static String autoBlock(String armedPlace, String here, String dockId, String docking, Boolean charging,
                            boolean dockAvailable, String dockReason, Integer battery, Integer minBattery) {
        if (dockId == null) return "no_dock";                 // an older launcher: no card kinds
        if (armedPlace == null || armedPlace.equals(dockId)) return "at_dock";
        if (here == null) return "location_unknown";
        if (here.equals(dockId)) return "at_dock";
        if (!here.equals(armedPlace)) return "moved";
        if ("on_dock".equals(docking)) return "at_dock";
        if (!"not_on_dock".equals(docking)) return "dock_unknown";
        if (!Boolean.FALSE.equals(charging)) return "charging_unknown";
        if (!dockAvailable) return dockReason == null || dockReason.isEmpty() ? "no_leg" : dockReason;
        if (battery == null || minBattery == null || battery < minBattery) return "low_battery";
        return null;
    }

    /** Blocks only a new arrival can lift: it is on the dock, it is somewhere
     *  else than where it arrived, or this launcher names no dock. The rest
     *  (latched, busy, battery, a loading read...) restart the count. */
    static boolean autoBlockDisarms(String block) {
        return "at_dock".equals(block) || "moved".equals(block) || "no_dock".equals(block);
    }

    /** Stop reasons the app gives that the launcher's tether cannot carry:
     *  the tether only knows "stop", which the launcher reports as
     *  stop_pressed (review #26). */
    static final Set<String> APP_STOP_REASONS = Collections.unmodifiableSet(new HashSet<>(
            Arrays.asList("not_foreground", "operator_screen", "app_lost")));

    /**
     * Review #24: which status the page gets. This app's own run wins over a
     * poll of that SAME run -- the poll is the run's status.json from up to
     * POLL_MS earlier, so when the launcher printed its terminal line the poll
     * still said "running", the page re-read "running", sent no terminal
     * nav_status, and arrival or failure reached the server one poll (5-12 s)
     * late. The poll wins only for a DIFFERENT live run (a PC run carries no
     * run_id at all).
     */
    static boolean ownStatusWins(boolean ownHasStatus, boolean ownActive, boolean pollActive,
                                 String ownRunId, String polledRunId) {
        if (!ownHasStatus) return false;
        if (ownActive) return true;
        boolean pollIsOtherRun = pollActive && (polledRunId == null || !polledRunId.equals(ownRunId));
        return !pollIsOtherRun;
    }

    /**
     * The status link (2026-10-09): the face page now reports between calls,
     * and the server reads an idle status as "nothing is walking" -- it
     * opens the wheel fence and lets the desk STOP say "stopped". This app's
     * idle means that only while its run.pid poll is fresh: a stale poll (su
     * wedged, the poller paused in the background, the first second after
     * start) cannot see a PC robot_python run, which bodyBlock() already
     * reads as nav_unknown. Then the status carries poll_stale and the server
     * reads "cannot tell". This app's own live run needs no poll: it is the run.
     */
    static boolean statusPollStale(boolean ownActive, boolean pollFresh) {
        return !ownActive && !pollFresh;
    }

    /**
     * "เก็บแขนก่อนเดิน" switched off = the operator vouches that the arms hang
     * at rest, and the launcher gets --arms-rested on that word (0.70 m in the
     * doors). Owner, 9 Oct 2026, at the robot after the first panel walk was
     * refused because group 99 gave no #AGF: "ไม่ต้องเก็บแขน เอาแบบนี้ไปเลย".
     * Review2 #7 had made "off" refuse every door leg instead (arms of unknown
     * pose at 0.78 m do not fit D8/D11); the owner's call reverses that for
     * the switch, which lives on the operator-only screen and says so there.
     * With the switch ON nothing changed: --arms-rested still needs a rest
     * this launch actually saw finish.
     */
    static boolean operatorVouchesArms(boolean requireArmRest) {
        return !requireArmRest;
    }

    /**
     * Review #26: the reason a stopped run reports. When this app stopped the
     * run for a reason of its own (background, operator screen, app closing)
     * the launcher still says stop_pressed -- and the turn log, the panel and
     * the spoken "กดหยุดแล้ว" claimed somebody pressed STOP. Anything else is
     * left exactly as the launcher wrote it.
     */
    static String stopReason(String state, String reason, boolean cancelledByApp, String appReason) {
        if ("stopped".equals(state) && "stop_pressed".equals(reason) && cancelledByApp
                && APP_STOP_REASONS.contains(appReason)) {
            return appReason;
        }
        return reason;
    }

    /**
     * Review #29: kill -9 for the launcher, through su. The app cannot signal
     * the su'd root process it started (destroyForcibly gets EPERM and
     * swallows it). Built from constants plus a validated int pid, and only
     * if that pid is still an emma_nav.py process, so a pid the system
     * reused is never signalled.
     */
    static String killLauncherScript(int pid) {
        if (pid <= 1) throw new IllegalArgumentException("pid");
        return "p=" + pid + "; kill -0 \"$p\" 2>/dev/null && tr '\\0' ' ' < /proc/$p/cmdline"
                + " | grep -q emma_nav && kill -9 \"$p\"; exit 0";
    }

    /** Review2 #25: su children stuck past their timeout before the periodic
     *  su calls stop spawning entirely (each is a root process plus a reaper
     *  thread the app can never end). */
    static final int MAX_WEDGED_SU = 3;
    /** ...and while fewer are stuck, at most one new try this often -- a su
     *  that recovers is found again without growing ~330 stuck children an
     *  hour while it does not. */
    static final long WEDGE_RETRY_MS = 60000L;

    /**
     * Review2 #25: whether a periodic su call (poll, places, check, reap) must
     * not spawn now. null = go ahead; "su_wedged" = skip it (the caller treats
     * that as a failed call: the poll goes stale, nav_unknown). The stop path
     * never asks -- a stop is not refused.
     */
    static String suWedgeBlocks(int liveWedged, long lastWedgeAt, long now) {
        if (liveWedged <= 0) return null;
        if (liveWedged >= MAX_WEDGED_SU) return "su_wedged";
        if (now - lastWedgeAt < WEDGE_RETRY_MS) return "su_wedged";
        return null;
    }
}
