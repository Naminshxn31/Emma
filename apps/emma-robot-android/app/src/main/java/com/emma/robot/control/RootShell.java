package com.emma.robot.control;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

/**
 * One fixed command through su, bounded in time (review #32).
 *
 * MainActivity.stopConflictingVendorApp and ArmController.runRoot both used
 * an unbounded process.waitFor() on `io`. A walk's launch runs on io too
 * (releaseBase, and the arm connect inside the rest), so an su or `am` that
 * hung left the accepted walk in "preparing" for ever: the SafetyBar pinned,
 * every gesture refused "navigating", every go refused "run_active". A
 * timeout now becomes the existing launcher_failed / arm_unavailable path.
 *
 * The output is read after the exit, like NavLauncher.runShell: these are
 * one-line system commands, far under the 64 KiB a pipe holds, so the child
 * cannot block on a full pipe while we wait.
 */
final class RootShell {
    static final String SU_BINARY = "/system/xbin/su";
    static final long TIMEOUT_MS = 6000L;

    static final class Result {
        final int exit;
        final String out;

        Result(int exit, String out) {
            this.exit = exit;
            this.out = out;
        }
    }

    private RootShell() {
    }

    /** Throws IllegalStateException on a timeout; the caller judges the exit. */
    static Result run(String command, long timeoutMs) throws Exception {
        Process process = new ProcessBuilder(SU_BINARY, "0", "sh", "-c", command)
                .redirectErrorStream(true)
                .start();
        try {
            process.getOutputStream().close();
            boolean exited;
            try {
                exited = process.waitFor(timeoutMs, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw e;
            }
            if (!exited) {
                throw new IllegalStateException("su ไม่ตอบภายใน " + timeoutMs + " ms");
            }
            StringBuilder output = new StringBuilder();
            try (InputStream stream = process.getInputStream();
                 BufferedReader reader = new BufferedReader(new InputStreamReader(
                         stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) output.append(line).append('\n');
            }
            return new Result(process.exitValue(), output.toString());
        } finally {
            // closes our ends of the pipes on every path; a root child that
            // outlived the timeout cannot be signalled from the app uid
            // (review #29), but it no longer holds io
            if (process.isAlive()) process.destroyForcibly();
        }
    }
}
