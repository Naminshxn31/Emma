package com.emma.robot.control;

/**
 * Finds the arm board's "#AGF" ("action group finished") in a serial stream
 * that arrives in arbitrary chunks (review #30).
 *
 * The vendor read loop hands onDataReceived whatever available() returned,
 * with no line framing, so "#AGF" can arrive as "#A" + "GF". A per-chunk
 * contains() missed it: the handshake stayed out until the 30 s fallback
 * instead of its 5 s hold, and a walk's rest waited the full 15 s and then
 * refused the walk (arms_not_rest). The last few bytes are carried into the
 * next chunk.
 *
 * Deliberately free of android.*: the tests compile and run it with the JDK.
 * Not thread-safe on its own; ArmController feeds it under one lock.
 */
final class AgfFramer {
    static final String MARK = "AGF";

    private String tail = "";

    /** True when this chunk completes an AGF (with or without its '#'),
     *  counting the bytes carried over from the previous chunk. */
    boolean feed(String chunk) {
        String joined = tail + (chunk == null ? "" : chunk);
        if (joined.contains(MARK)) {
            tail = "";
            return true;
        }
        int keep = MARK.length();      // "#AG" is the longest unfinished prefix worth keeping
        tail = joined.length() > keep ? joined.substring(joined.length() - keep) : joined;
        return false;
    }

    /** A stale prefix from an earlier frame must not join a later one: on a
     *  disconnect and before every new group. */
    void reset() {
        tail = "";
    }
}
