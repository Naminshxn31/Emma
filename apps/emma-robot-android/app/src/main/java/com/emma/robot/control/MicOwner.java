package com.emma.robot.control;

/**
 * ARRAY_MIC (docs/emma-array-mic-2026-11-02.md): who may capture the mic
 * array right now -- the app, or the face page for one call, or the page for
 * the rest of this app run. Pure Java (no android.*) so the tests run these
 * exact rules on a JVM; {@link ArrayListener} holds the one instance.
 *
 * Every method is synchronized on this object, and ArrayListener starts its
 * capture thread INSIDE {@code synchronized (owner)} right after
 * {@link #mayStart()}: a hold or a release that lands while a capture is
 * being started either comes first (no start) or after (the capture is then
 * stopped and the page waits for its thread to end). That is what keeps
 * "never both capturing" -- the array gives Android one capture stream
 * (measured 10 Oct: app first = the page's mic reads exact zeros).
 *
 * Lead's review, 2 Nov: the first build latched EVERY hand-back for the app
 * run, and a transient state at call start -- the robot booting, any server
 * restart (every deploy: the app's link backs off 2/4/8/16/32 s while the
 * page reloads at 1/2/4/8 s) -- turned the array off for the day, silently.
 * So:
 * <ul>
 * <li>{@link #hold}: the page takes its mic for ONE call. "start" (the array
 *   was not live at the call's `ready`: boot, a reconnect, still opening)
 *   never counts toward anything.</li>
 * <li>"deaf" (the array went quiet mid-call, or the page's silent clock never
 *   ticked) counts: the {@link #DEAF_HOLDS_PER_RUN}-th of one app run is a
 *   release for good. Why 3: each deaf episode costs the guest 2-5 s of
 *   unheard speech (ARRAY_MIC_DEAF_S + the wait for the app to let go);
 *   one or two in a day are a USB hiccup or a stall, a third in the same run
 *   says this run's array path is unreliable -- the page mic for the rest of
 *   it costs nothing audible.</li>
 * <li>{@link #free}: the call's mic tracks are stopped (the page's
 *   releaseCallHardware, after the tracks), or a page has just loaded (no
 *   call can hold a mic before it): the app may capture again.</li>
 * <li>{@link #release}: for the rest of this app run (a final reason: an old
 *   page's JS, barge-in, hold-speech, another input rate).</li>
 * </ul>
 */
final class MicOwner {
    static final int DEAF_HOLDS_PER_RUN = 3;

    private boolean held = false;
    private boolean released = false;
    private int deafHolds = 0;
    private String why;

    /** The page takes its mic for one call. True = this ended in a release
     *  for the rest of the run (the cap), false = a hold. */
    synchronized boolean hold(String kind) {
        if (released) return true;
        if ("deaf".equals(kind)) {
            deafHolds++;
            if (deafHolds >= DEAF_HOLDS_PER_RUN) {
                released = true;
                held = false;
                why = "deaf_x" + deafHolds;
                return true;
            }
        }
        held = true;
        why = "deaf".equals(kind) ? "hold_deaf" : "hold_start";
        return false;
    }

    /** For the rest of this app run. */
    synchronized void release(String reason) {
        if (!released) why = reason;
        released = true;
        held = false;
    }

    /** The call's mic is stopped (or a page just loaded). True = a hold was
     *  lifted and the app may capture again. A release is never undone. */
    synchronized boolean free() {
        boolean was = held;
        held = false;
        return was && !released;
    }

    synchronized boolean mayStart() {
        return !held && !released;
    }

    synchronized boolean held() {
        return held;
    }

    synchronized boolean released() {
        return released;
    }

    synchronized int deafHolds() {
        return deafHolds;
    }

    synchronized String why() {
        return why;
    }
}
