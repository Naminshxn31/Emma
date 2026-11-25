package com.emma.robot.control;

import java.util.List;

/**
 * What the server is told of the pageant's timing: body_caps `pageant_v` / `pageant_lead_ms` /
 * `pageant_two_sides_ms` (BodyController.list), read by app/event_pageant.py `timing`.
 *
 * Review 24 Nov: there was no version handshake. 23 Nov's faster raise (owner at the ceremony's
 * opening on the robot: "ยกมือช้าไปหน่อย อยากให้ไวกว่านี้ได้ไหม" -- the raise 6.9 s -> 4.0 s) moved the
 * server's numbers with it (EVENT_PAGEANT_LEAD_S 4.5 -> 2.4, MIN_STANDING_S 18.7 -> 16.8); a new
 * server with the APK of 10-23 Nov would start her line inside the old raise's 3 s first frame and
 * bring the hand down mid-stroke of the second side -- the mid-stroke end 10 Nov's review took out.
 * So the APK says what its own frames are, and the server never hard-codes them: an APK without
 * `pageant_v` is the old one (the server's known-good 4.5 / 18.7 for it).
 *
 * Derived from Pageant's own frames, so a change of frame times there moves these with it. Free of
 * android.* and org.json like Pageant: tests/test_pageant_timing.py compiles and runs it with the
 * plain JDK.
 */
final class PageantTiming {
    private PageantTiming() {}

    /** 2 = the timing below is in the caps. 1 (no field) = the APK of 10-23 Nov. */
    static final int VERSION = 2;

    /** From the frames going to the board to her line: the raise's first two stages (the arm out,
     *  the forearm up -- "the hand up enough"); the rest of the raise lands on her "Hello everyone".
     *  The frames themselves, not their constants' names: a raise rebuilt with other stages still
     *  reports what it plays. */
    static int leadMs() {
        List<BodyGestures.Frame> raise = Pageant.raise();
        return BodyGestures.totalMs(raise.subList(0, Math.min(2, raise.size())));
    }

    /** From the frames going to the board to both wave sides done: the raise, then one side each
     *  way (the first side's turn is the head's first move, the slow one). The server holds a
     *  standing hand up at least this long (+ its slack) so it never comes down mid-stroke. */
    static int twoSidesMs() {
        List<BodyGestures.Frame> seq = Pageant.sequence();
        int n = Pageant.raise().size() + Pageant.side(1).size() + Pageant.side(-1).size();
        return BodyGestures.totalMs(seq.subList(0, Math.min(n, seq.size())));
    }
}
