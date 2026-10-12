package com.emma.robot.control;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Locale;

/**
 * The robot walks back to its charging dock by itself once nobody has used it
 * for a while at the place it walked to (owner, 10 Oct 2026: "เหมือนทำงาน
 * เสร็จก็ให้เข้าไปชาร์จที่แท่น"). Off by default -- a switch on the operator
 * screen, "กลับแท่นเองเมื่อว่าง", like nav_enabled -- and with it off nothing
 * here runs a gate, asks the launcher anything or tells the page anything new.
 *
 * What it does, in order (the timing rules are NavRules.autoStep, the place
 * rules NavRules.autoBlock -- both compiled and run by the tests):
 *
 *  1. ARM. A run of this app (tap or voice) ends `arrived` at a place that is
 *     not the dock. Nothing else arms it: not an app start, not a PC run, not
 *     a stopped or failed walk, not its own walk.
 *  2. COUNT. The idle time (default 90 s, 60-600 s on the operator screen)
 *     starts again on every sign that somebody is still being served: a touch
 *     anywhere on the face screen or a gamepad key (MainActivity), a call
 *     starting or ending and anybody speaking in it (the page:
 *     emma-nav:activity:*), the operator screen being open, the app coming
 *     back to the front. A new walk of anybody's disarms it.
 *  3. WARN. 10 s before the end the face page shows a banner with a big
 *     "อยู่ต่อ" and confirms every second that it is ON SCREEN. The walk is due
 *     10 s after the page's first confirmation; no confirmation within 3 s,
 *     or a gap of more than 2.5 s, and there is no walk (an old page, a dead
 *     or reloading one, a hidden one) -- the count starts again. Emma says
 *     nothing: no frame goes to the server for this, nothing is announced.
 *  4. GO, only if everything passes on a places read at most 15 s old: the
 *     robot is where it arrived (not on the dock, the board says not_on_dock
 *     and not charging -- unknown is no), the dock card is available from
 *     here (the launcher's own leg, location and battery check) and every
 *     gate of a go (nav on, foreground, no latch, no run, body idle, no
 *     operator screen). Then go(dock, "auto"): the same launch, arm-rest rule
 *     and tether as a tap. "auto" goes like "tap" on an unverified leg and
 *     never like "voice".
 *  5. NO RETRY. A walk that ends anything but `arrived` is shown on the face
 *     with its reason and never started again: a robot stopped in a corridor
 *     has an unknown location, and only staff and a new walk change that.
 *     A refusal BEFORE anything moved (latched, busy, battery...) only starts
 *     the count again, and a refusal that only a new arrival can lift (on the
 *     dock, moved, no dock card) disarms it.
 *
 * UI thread only: the tick is posted on the main looper, and every entry
 * point (bridge prompts, touches, keys, operator buttons) runs there; one
 * that does not is re-posted.
 */
final class AutoReturn {
    interface Host {
        boolean operatorVisible();
        /** Tell the page (emma-nav-changed) and the operator screen. */
        void changed();
    }

    static final String PREF_ENABLED = "nav_auto_return";
    static final String PREF_IDLE_S = "nav_auto_return_idle_s";
    static final long TICK_MS = 1000L;
    /** A places read is asked for this long before the warning starts, so
     *  the read the walk is decided on is fresh without a su call a second. */
    private static final long CAPS_LEAD_MS = 5000L;
    private static final String TAG = "EmmaAutoReturn";

    enum Phase { OFF, COUNTING, WARNING, GOING, FAILED }

    private final NavLauncher nav;
    private final Host host;
    private final SharedPreferences prefs;
    private final Handler ui;
    private final Runnable tick = this::tick;

    private Phase phase = Phase.OFF;
    private String armedPlace;          // where the run that armed it arrived
    private String goingRunId;          // the auto walk's run id
    private String dockId;              // the places card of kind "dock", last seen
    private String seenRunId;           // the last run whose end was handled
    private String failReason;          // why the auto walk did not arrive (FAILED only)
    /** For the operator screen, until the next arrival: why the last count
     *  did not end in a walk, or how the last walk back ended. Codes only. */
    private String lastNote;
    private long lastActivity;
    private long warnStartedAt;
    private long shownAt;
    private long seenAt;
    private boolean placesAsked;

    AutoReturn(Context context, NavLauncher nav, Handler ui, Host host) {
        this.nav = nav;
        this.host = host;
        this.ui = ui;
        this.prefs = context.getSharedPreferences(NavLauncher.PREFS, Context.MODE_PRIVATE);
    }

    // ---- the operator screen's switch and idle time --------------------------------

    boolean enabled() {
        return prefs.getBoolean(PREF_ENABLED, false);
    }

    void setEnabled(boolean value) {
        prefs.edit().putBoolean(PREF_ENABLED, value).apply();
        if (!value) setPhase(Phase.OFF);
        host.changed();
    }

    int idleS() {
        return NavRules.clampIdleS(prefs.getInt(PREF_IDLE_S, NavRules.AUTO_IDLE_DEFAULT_S));
    }

    void stepIdle(int direction) {
        prefs.edit().putInt(PREF_IDLE_S, NavRules.stepIdleS(idleS(), direction)).apply();
        host.changed();
    }

    // ---- the clock --------------------------------------------------------------------

    void start() {
        ui.removeCallbacks(tick);
        ui.postDelayed(tick, TICK_MS);
    }

    void stop() {
        ui.removeCallbacks(tick);
    }

    private void tick() {
        ui.postDelayed(tick, TICK_MS);
        try {
            step(SystemClock.elapsedRealtime());
        } catch (RuntimeException e) {
            // on the UI thread: an exception here would take the app -- and
            // its native STOP bar -- down with it. A tick that failed walks
            // nobody anywhere; the count is simply off until the next arrival.
            Log.e(TAG, "auto-return tick failed; disarmed", e);
            setPhase(Phase.OFF);
        }
    }

    private void step(long now) {
        rememberDock();
        if (!enabled()) {
            if (phase != Phase.OFF) {
                setPhase(Phase.OFF);
                host.changed();
            }
            return;
        }
        observeRun(now);
        // staff at the robot: the count waits for them to close the screen
        if (host.operatorVisible()) activityAt(now);
        if (phase == Phase.COUNTING || phase == Phase.WARNING) countdown(now);
    }

    /** The dock's id from the latest places read that has card kinds. The
     *  read is dropped when a walk ends, so the id from before is what tells
     *  whether that walk ended AT the dock. */
    private void rememberDock() {
        String id = dockOf(nav.placesSnapshot());
        if (id != null) dockId = id;
    }

    private static String dockOf(JSONObject caps) {
        JSONArray list = caps == null ? null : caps.optJSONArray("places");
        if (list == null) return null;
        String found = null;
        for (int i = 0; i < list.length(); i++) {
            JSONObject card = list.optJSONObject(i);
            if (card == null || !"dock".equals(card.optString("kind"))) continue;
            if (found != null) return null;             // two docks: which one is ours?
            found = card.optString("id", null);
        }
        return found;
    }

    private static JSONObject cardOf(JSONObject caps, String id) {
        JSONArray list = caps == null ? null : caps.optJSONArray("places");
        if (list == null || id == null) return null;
        for (int i = 0; i < list.length(); i++) {
            JSONObject card = list.optJSONObject(i);
            if (card != null && id.equals(card.optString("id"))) return card;
        }
        return null;
    }

    private void observeRun(long now) {
        NavLauncher.RunView run = nav.ownRun();
        if (run == null) return;
        if (phase == Phase.GOING) {
            if (!run.id.equals(goingRunId) || !run.terminal()) return;
            seenRunId = run.id;
            if ("arrived".equals(run.state)) {
                lastNote = "กลับถึงแท่นแล้ว";
                setPhase(Phase.OFF);
            } else {
                // no retry, ever: show why and leave it to staff
                String why = run.reason == null ? run.state : run.reason;
                setPhase(Phase.FAILED);
                failReason = why;
                lastNote = "เดินกลับไม่ถึงแท่น: " + why;
                Log.w(TAG, "the walk back to the dock ended " + run.state + " (" + failReason + "): not retried");
            }
            host.changed();
            return;
        }
        if (run.active()) {
            // somebody's walk: the count is for after the NEXT arrival
            if (phase == Phase.COUNTING || phase == Phase.WARNING) {
                setPhase(Phase.OFF);
                host.changed();
            }
            return;
        }
        if (!run.terminal() || run.id.equals(seenRunId)) return;
        seenRunId = run.id;
        if (NavRules.armsAutoReturn(run.state, run.origin, run.place, dockId)) {
            setPhase(Phase.COUNTING);
            armedPlace = run.place;
            lastNote = null;
            lastActivity = now;
            host.changed();
        } else if (phase != Phase.OFF && phase != Phase.FAILED) {
            setPhase(Phase.OFF);
            host.changed();
        }
    }

    private void countdown(long now) {
        long dueAt = lastActivity + idleS() * 1000L;
        String step = NavRules.autoStep(now, dueAt, warnStartedAt, shownAt, seenAt);
        if (NavRules.STEP_COUNT.equals(step)) {
            if (!placesAsked && now >= dueAt - NavRules.AUTO_WARN_MS - CAPS_LEAD_MS) {
                placesAsked = true;
                nav.refreshPlaces();
            }
        } else if (NavRules.STEP_WARN.equals(step)) {
            String why = block(false);
            if (why != null) {
                notGoing(why, now);
                return;
            }
            setPhase(Phase.WARNING);
            warnStartedAt = now;
            nav.refreshPlaces();          // the read the walk is decided on lands during the warning
            host.changed();               // the page pulls emma-nav:auto and draws the banner
        } else if (NavRules.STEP_NOT_SHOWN.equals(step)) {
            notGoing("not_shown", now);
        } else if (NavRules.STEP_GO.equals(step)) {
            String why = block(true);
            if (why != null) {
                notGoing(why, now);
                return;
            }
            String dock = dockOf(nav.placesSnapshot());
            JSONObject ack = parse(nav.go(dock, NavRules.AUTO));
            if (ack != null && ack.optBoolean("accepted", false)) {
                setPhase(Phase.GOING);
                goingRunId = ack.optString("run_id", null);
                lastNote = null;
                Log.i(TAG, "idle " + idleS() + " s at " + armedPlace + ": walking back to " + dock);
                host.changed();
            } else {
                String reason = ack == null || ack.isNull("reason") ? "launcher_failed" : ack.optString("reason");
                notGoing(reason, now);
            }
        }
    }

    /** Why the walk may not start now, or null. `fresh`: the places read must
     *  be at most AUTO_CAPS_FRESH_MS old (the moment of the walk); the check
     *  at the start of the warning takes what is cached. */
    private String block(boolean fresh) {
        JSONObject caps = nav.placesSnapshot();
        if (caps == null) return "places_loading";
        long age = nav.placesAgeMs();
        if (fresh && (age < 0 || age > NavRules.AUTO_CAPS_FRESH_MS)) return "places_old";
        String dock = dockOf(caps);
        JSONObject card = cardOf(caps, dock);
        String why = NavRules.autoBlock(armedPlace, idOrNull(caps, "here"), dock, idOrNull(caps, "docking"),
                caps.opt("charging") instanceof Boolean ? (Boolean) caps.opt("charging") : null,
                card != null && card.optBoolean("available", false),
                card == null ? null : idOrNull(card, "reason"),
                intOrNull(caps, "battery"), card == null ? null : intOrNull(card, "min_battery"));
        if (why != null) return why;
        return nav.autoRefusal(dock);
    }

    private void notGoing(String why, long now) {
        boolean wasWarning = phase == Phase.WARNING;
        lastNote = "ไม่ได้ออก: " + why;
        if (NavRules.autoBlockDisarms(why)) {
            setPhase(Phase.OFF);
        } else {
            setPhase(Phase.COUNTING);
            lastActivity = now;
        }
        Log.i(TAG, "no walk back to the dock (" + why + ")");
        if (wasWarning || phase == Phase.OFF) host.changed();
    }

    /** Every phase change goes through here, so no warning or walk field
     *  outlives the phase it belongs to. */
    private void setPhase(Phase next) {
        phase = next;
        warnStartedAt = 0L;
        shownAt = 0L;
        seenAt = 0L;
        placesAsked = false;
        failReason = null;
        if (next != Phase.GOING) goingRunId = null;
        if (next == Phase.OFF || next == Phase.FAILED) armedPlace = null;
    }

    // ---- signs that somebody is being served ---------------------------------------

    /** Any thread. */
    void activity(String kind) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            ui.post(() -> activity(kind));
            return;
        }
        long now = SystemClock.elapsedRealtime();
        if (phase == Phase.FAILED) {
            // the app coming back to the front is nobody reading the notice
            if ("resume".equals(kind)) return;
            // somebody is at the screen: the notice was seen (the operator
            // screen keeps the reason until the next arrival)
            setPhase(Phase.OFF);
            host.changed();
            return;
        }
        activityAt(now);
    }

    private void activityAt(long now) {
        lastActivity = now;
        if (phase == Phase.WARNING) {
            setPhase(Phase.COUNTING);
            lastActivity = now;
            host.changed();               // the page takes the banner down
        }
    }

    /** "อยู่ต่อ" (or "รับทราบ" on a failure notice). */
    void stay() {
        activity("stay");
    }

    /** emma-nav:auto:shown -- the page has the warning on screen right now. */
    void shown() {
        if (phase != Phase.WARNING) return;
        long now = SystemClock.elapsedRealtime();
        if (shownAt <= 0) shownAt = now;
        seenAt = now;
    }

    // ---- what the page and the operator screen read --------------------------------

    /** emma-nav:auto: {enabled, phase, left_s, place, reason}. Codes and ids only. */
    String json() {
        long now = SystemClock.elapsedRealtime();
        try {
            JSONObject out = new JSONObject()
                    .put("enabled", enabled())
                    .put("phase", phase.name().toLowerCase(Locale.ROOT))
                    .put("idle_s", idleS());
            boolean counting = phase == Phase.COUNTING || phase == Phase.WARNING;
            out.put("left_s", counting
                    ? NavRules.autoLeftS(now, lastActivity + idleS() * 1000L, warnStartedAt, shownAt)
                    : JSONObject.NULL);
            out.put("place", phase == Phase.GOING ? orNull(dockId) : orNull(armedPlace));
            out.put("reason", phase == Phase.FAILED ? orNull(failReason) : JSONObject.NULL);
            return out.toString();
        } catch (Exception e) {
            return "{\"enabled\":false,\"phase\":\"off\"}";
        }
    }

    /** One line for the operator screen. */
    String operatorLine() {
        if (!enabled()) return "กลับแท่นเอง: ปิด";
        long now = SystemClock.elapsedRealtime();
        String line;
        switch (phase) {
            case COUNTING:
                line = "รออยู่ที่ " + armedPlace + " · ออกในอีก "
                        + NavRules.autoLeftS(now, lastActivity + idleS() * 1000L, 0L, 0L) + " วิ";
                break;
            case WARNING:
                line = "เตือนบนหน้าจอ — ออกในอีก "
                        + NavRules.autoLeftS(now, lastActivity + idleS() * 1000L, warnStartedAt, shownAt) + " วิ";
                break;
            case GOING:
                line = "กำลังกลับแท่นชาร์จเอง";
                break;
            case FAILED:
                line = "กลับแท่นเองไม่สำเร็จ (" + failReason + ") — ไม่ลองซ้ำ ให้เจ้าหน้าที่ดูแล";
                break;
            default:
                line = "ว่าง — เริ่มนับเมื่อหุ่นเดินไปถึงจุดที่ไม่ใช่แท่น";
                break;
        }
        return "กลับแท่นเอง (รอ " + idleS() + " วิ): " + line
                + (lastNote == null ? "" : "\nครั้งล่าสุด — " + lastNote);
    }

    private static Object orNull(String value) {
        return value == null ? JSONObject.NULL : value;
    }

    private static String idOrNull(JSONObject o, String key) {
        if (o == null || o.isNull(key)) return null;
        Object v = o.opt(key);
        return v instanceof String && !((String) v).isEmpty() ? (String) v : null;
    }

    private static Integer intOrNull(JSONObject o, String key) {
        if (o == null || o.isNull(key)) return null;
        Object v = o.opt(key);
        if (!(v instanceof Number)) return null;
        double d = ((Number) v).doubleValue();
        return Double.isFinite(d) ? (int) Math.floor(d) : null;
    }

    private static JSONObject parse(String text) {
        try {
            return text == null ? null : new JSONObject(text);
        } catch (Exception e) {
            return null;
        }
    }
}
