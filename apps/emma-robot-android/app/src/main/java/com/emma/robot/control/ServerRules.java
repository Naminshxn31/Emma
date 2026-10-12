package com.emma.robot.control;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * ServerLauncher's decisions that need no Android (the NavRules pattern): the
 * tests COMPILE and RUN these with the plain JDK.
 *
 * The whole Emma server runs on the robot since 9 Oct 2026 (owner: "ทำให้
 * ทุกอย่างรันบนหุ่น"), at the same https://127.0.0.1:8001 the face used to reach
 * through `adb reverse` to a PC. scripts/robot_server.py installs it; this app
 * only starts it -- `emma_server.sh start`, idempotent -- at start-up and as a
 * watchdog, and never handles a key: the .env and the TLS key are root:nobody
 * 0640 files the app's uid cannot even list.
 */
final class ServerRules {
    private ServerRules() {
    }

    static final String BASE = "/data/local/emma-server";
    static final String SCRIPT = BASE + "/code/scripts/emma_server.sh";
    /** The start script's own guard: not installed is a fact, not a guess from
     *  a failure (NavLauncher.NOT_INSTALLED_EXIT is the same number). */
    static final int NOT_INSTALLED_EXIT = 86;
    /** A first boot measured ~20 s (imports, the face models, the slide
     *  index). Past this, a live process with a silent port is a fault. */
    static final long BOOT_GRACE_MS = 90000L;
    static final long RESTART_BACKOFF_MS = 30000L;
    static final long RESTART_BACKOFF_MAX_MS = 300000L;
    static final int DETAIL_MAX = 160;

    enum State { OFF, CHECKING, RUNNING, STARTING, NOT_INSTALLED, FAILED }

    private static final Pattern IPV4 = Pattern.compile("(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])(\\.(25[0-5]|2[0-4][0-9]|1?[0-9]?[0-9])){3}");
    private static final Pattern DNS_ARG = Pattern.compile("[0-9.,]*");

    /** The watchdog runs only for a face pointed at THIS robot. A robot
     *  provisioned to a PC URL (the old `adb reverse` set-up, a desk server)
     *  must not start a second Emma nobody is talking to. */
    static boolean localServer(String host) {
        if (host == null) return false;
        String h = host.trim().toLowerCase(Locale.ROOT);
        return "127.0.0.1".equals(h) || "localhost".equals(h);
    }

    /** Android's DNS servers for the active network, as EMMA_DNS: IPv4 only
     *  (the robot's Wi-Fi has no IPv6 route -- scripts/robot_dns.py asks A
     *  records over UDP), deduplicated, at most three, comma-joined. Anything
     *  that is not a dotted quad is dropped: this string ends up in a shell. */
    static String dnsList(List<String> addresses) {
        List<String> out = new ArrayList<>();
        if (addresses != null) {
            for (String a : addresses) {
                if (a == null) continue;
                String s = a.trim();
                if (IPV4.matcher(s).matches() && !out.contains(s) && out.size() < 3) out.add(s);
            }
        }
        StringBuilder joined = new StringBuilder();
        for (String s : out) {
            if (joined.length() > 0) joined.append(',');
            joined.append(s);
        }
        return joined.toString();
    }

    /** The one su script: constants plus an EMMA_DNS of digits, dots and
     *  commas (re-checked here; emma_server.sh strips anything else again).
     *  Exit 86 when the server is not installed; otherwise the script's own
     *  answer -- "running N", "started N", "starting (...)", "no runtime..."
     *  (exit 2), "failed to start..." (exit 1). */
    static String startScript(String dns) {
        String d = dns == null ? "" : dns;
        if (!DNS_ARG.matcher(d).matches()) throw new IllegalArgumentException("dns");
        return "[ -f " + SCRIPT + " ] || exit " + NOT_INSTALLED_EXIT + "; export EMMA_DNS='" + d
                + "'; exec sh " + SCRIPT + " start";
    }

    /** The face page's reload delay while a local server boots: 1, 2, 4,
     *  then every 8 s -- the face is up at most ~8 s after the server is. */
    static long pageRetryDelayMs(int attempt) {
        if (attempt <= 1) return 1000L;
        if (attempt == 2) return 2000L;
        if (attempt == 3) return 4000L;
        return 8000L;
    }

    /** After `failures` starts that never answered: how long before the next
     *  start. A server that crashes on boot must not be relaunched every 30 s
     *  for ever (each boot is ~20 s of a full core on the robot). */
    static long restartBackoffMs(int failures) {
        if (failures <= 1) return 0L;
        long wait = RESTART_BACKOFF_MS;
        for (int i = 2; i < failures && wait < RESTART_BACKOFF_MAX_MS; i++) wait *= 2;
        return Math.min(wait, RESTART_BACKOFF_MAX_MS);
    }

    static String firstLine(String out) {
        if (out == null) return "";
        for (String line : out.split("\n")) {
            String t = line.trim();
            if (!t.isEmpty()) return t.length() > DETAIL_MAX ? t.substring(0, DETAIL_MAX) : t;
        }
        return "";
    }

    /** One line for the operator screen. */
    static String label(State state, String detail, String lastError) {
        String head;
        switch (state) {
            case OFF: head = "ไม่ใช้"; break;
            case RUNNING: head = "ทำงานอยู่"; break;
            case STARTING: head = "กำลังเริ่ม…"; break;
            case NOT_INSTALLED: head = "ยังไม่ได้ติดตั้ง"; break;
            case FAILED: head = "เริ่มไม่สำเร็จ"; break;
            default: head = "กำลังตรวจ…"; break;
        }
        StringBuilder line = new StringBuilder("เซิร์ฟเวอร์บนหุ่น: ").append(head);
        if (detail != null && !detail.isEmpty()) line.append(" · ").append(detail);
        if (lastError != null && !lastError.isEmpty() && (detail == null || !detail.equals(lastError))
                && state != State.OFF) {
            line.append(" · ล่าสุด: ").append(lastError);
        }
        return line.toString();
    }

    /**
     * What one watchdog tick saw, turned into a state. Kept between ticks;
     * touched by the watchdog thread only.
     */
    static final class Watch {
        State state = State.CHECKING;
        String detail = "";
        String lastError = "";
        long unansweredSince = -1L;
        int startsUnanswered = 0;
        long nextStartAt = 0L;

        void off(String host) {
            state = State.OFF;
            detail = "หน้า Emma ชี้ไปที่ " + (host == null ? "?" : host);
            unansweredSince = -1L;
            startsUnanswered = 0;
        }

        /** No su this tick (back-off, or the port already answers and the
         *  last check vouched for it): only the port's answer is news. */
        boolean mayStart(long now) {
            return now >= nextStartAt;
        }

        private void fail(String why, long now, boolean countsAsStart) {
            state = State.FAILED;
            detail = why;
            lastError = why;
            if (countsAsStart) {
                startsUnanswered++;
                nextStartAt = now + restartBackoffMs(startsUnanswered);
            }
        }

        /**
         * @param exit     the su script's exit (-1: su timed out, was wedged
         *                 or could not run)
         * @param out      its output
         * @param portOpen something answers on the server's port right now
         */
        void observe(int exit, String out, boolean portOpen, long now) {
            String first = firstLine(out);
            if (exit == NOT_INSTALLED_EXIT || exit == 2) {
                state = State.NOT_INSTALLED;
                // 2 = installed only in part: the script says which step is missing
                detail = exit == 2 ? first : "ติดตั้งจากคอมพิวเตอร์: robot_server.py install/deploy/secrets";
                if (portOpen) detail += " · มีโปรแกรมอื่นตอบที่พอร์ตนี้ (adb reverse ค้าง?)";
                unansweredSince = -1L;
                return;
            }
            if (exit < 0) {
                // su did not answer. A port that answers is still a working face;
                // say what could not be checked, never "failed" over a live page.
                if (portOpen) {
                    state = State.RUNNING;
                    detail = "ตรวจผ่าน su ไม่ได้";
                } else {
                    fail("su ไม่ตอบ — ตรวจหรือเริ่มเซิร์ฟเวอร์ไม่ได้", now, false);
                }
                return;
            }
            if (exit == 0 && first.startsWith("running")) {
                if (portOpen) {
                    state = State.RUNNING;
                    detail = "";
                    unansweredSince = -1L;
                    startsUnanswered = 0;
                    nextStartAt = 0L;
                    return;
                }
                if (unansweredSince < 0) unansweredSince = now;
                if (now - unansweredSince < BOOT_GRACE_MS) {
                    state = State.STARTING;
                    detail = "";
                } else {
                    fail("กระบวนการอยู่แต่พอร์ตไม่ตอบเกิน " + (BOOT_GRACE_MS / 1000) + " วิ — ดู robot_server.py logs",
                            now, false);
                }
                return;
            }
            if (exit == 0 && first.startsWith("starting")) {
                state = State.STARTING;           // another starter holds the lock
                detail = "";
                return;
            }
            if (exit == 0 && first.startsWith("started")) {
                unansweredSince = now;
                if (portOpen) {
                    // 1 s after a launch nothing of ours can answer yet: the port
                    // is somebody else's, and our server will lose it and exit
                    fail("พอร์ตถูกโปรแกรมอื่นใช้อยู่ (adb reverse ค้าง?) — เซิร์ฟเวอร์บนหุ่นเปิดพอร์ตไม่ได้", now, true);
                    return;
                }
                if (startsUnanswered >= 1) {
                    // the previous start died before it ever answered
                    fail("เริ่มใหม่ " + (startsUnanswered + 1) + " ครั้งแล้วยังไม่ตอบ — ดู robot_server.py logs", now, true);
                    return;
                }
                startsUnanswered++;
                state = State.STARTING;
                detail = "";
                return;
            }
            fail(exit == 1 && !first.isEmpty() ? first : "exit " + exit + (first.isEmpty() ? "" : ": " + first),
                    now, true);
        }

        String label() {
            return ServerRules.label(state, detail, lastError);
        }
    }
}
