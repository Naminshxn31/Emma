package com.emma.robot.control;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.Uri;
import android.os.SystemClock;
import android.util.Log;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;

/**
 * Keeps the Emma server that runs ON the robot up, so the face needs no PC
 * (owner, 9 Oct 2026). Before, the page came from a PC through `adb reverse`:
 * the PC asleep, or its adb losing the robot, was a face that could not talk.
 *
 * What it does, and nothing more: at start-up and every WATCH_MS while the
 * app is in front, `su 0 sh emma_server.sh start` (idempotent -- "running N"
 * when it is up) plus a TCP probe of the server's port, turned into one line
 * for the operator screen (ServerRules.Watch). It never stops the server --
 * the server serves the page -- and never touches a secret: the .env and the
 * TLS key are root:nobody 0640 under a 0750 directory this uid cannot list.
 *
 * Only for a face pointed at this robot (127.0.0.1 / localhost): a robot
 * provisioned to a PC URL must not start a second Emma nobody talks to.
 *
 * Its own thread: io carries the arm work and the walk's launch, and the nav
 * poller's 5 s cadence is what keeps gestures out of nav_unknown -- a 6 s su
 * of ours queued on either would cost them.
 */
final class ServerLauncher {
    interface Host {
        /** The face's configured server (EmmaFace.serverUrl()). */
        String serverUrl();
        /** The operator line changed. Any thread. */
        void changed();
    }

    private static final String TAG = "EmmaServer";
    static final long WATCH_MS = 30000L;
    /** The script answers after its own `sleep 1`; past the nav shells'
     *  budget the su is wedged and NavLauncher counts it as such. */
    static final long START_TIMEOUT_MS = NavLauncher.SHELL_TIMEOUT_MS;
    static final int PROBE_TIMEOUT_MS = 800;

    private final Context context;
    private final Host host;
    private final ScheduledExecutorService watchdog = Executors.newSingleThreadScheduledExecutor(
            runnable -> new Thread(runnable, "server-watchdog"));
    /** Touched on the watchdog thread only. */
    private final ServerRules.Watch watch = new ServerRules.Watch();
    private volatile String line = ServerRules.label(ServerRules.State.CHECKING, "", "");
    private volatile ServerRules.State state = ServerRules.State.CHECKING;
    private ScheduledFuture<?> task;      // guarded by this
    private boolean loggedMissing = false; // watchdog thread only

    ServerLauncher(Context context, Host host) {
        this.context = context.getApplicationContext();
        this.host = host;
    }

    /** First check now, then every WATCH_MS. */
    void resume() {
        synchronized (this) {
            if (task != null) return;
            task = watchdog.scheduleWithFixedDelay(this::tick, 0, WATCH_MS, TimeUnit.MILLISECONDS);
        }
    }

    /** The watchdog rests while the app is not in front. The SERVER does not
     *  stop: it serves the page, and a face paused by a permission dialog or
     *  the operator's Settings must come back to a server that answers. */
    void pause() {
        synchronized (this) {
            if (task != null) task.cancel(false);
            task = null;
        }
    }

    void shutdown() {
        pause();
        watchdog.shutdownNow();
    }

    String statusLine() {
        return line;
    }

    ServerRules.State state() {
        return state;
    }

    private void tick() {
        try {
            check();
        } catch (RuntimeException e) {
            // a periodic task that throws is never scheduled again: the watchdog
            // would be gone for the rest of the process without a word
            Log.w(TAG, "server check failed", e);
        }
    }

    private void check() {
        String configured = host.serverUrl();
        Uri server = Uri.parse(configured == null ? "" : configured);
        String name = server.getHost();
        if (!ServerRules.localServer(name)) {
            watch.off(name);
        } else {
            int port = server.getPort() > 0 ? server.getPort() : ("http".equals(server.getScheme()) ? 80 : 443);
            boolean open = answers(port);
            long now = SystemClock.elapsedRealtime();
            // a silent port inside the restart back-off: no su this tick
            if (open || watch.mayStart(now)) {
                NavLauncher.Shell result = NavLauncher.runPeriodic(ServerRules.startScript(dns()), START_TIMEOUT_MS);
                if (result.exit == ServerRules.NOT_INSTALLED_EXIT && !loggedMissing) {
                    loggedMissing = true;
                    Log.i(TAG, ServerRules.SCRIPT + " is not on this robot: nothing to start"
                            + " (scripts/robot_server.py install/deploy/secrets)");
                }
                watch.observe(result.exit, result.out, open, now);
            }
        }
        state = watch.state;
        String next = watch.label();
        if (!next.equals(line)) {
            line = next;
            Log.i(TAG, next);
            host.changed();
        }
    }

    /** Android's DNS servers for the active network, for scripts/robot_dns.py:
     *  the server's glibc has no /etc/resolv.conf to read (measured: gaierror
     *  -3 on the robot). Empty is fine -- it falls back to 1.1.1.1/8.8.8.8. */
    private String dns() {
        List<String> found = new ArrayList<>();
        try {
            ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            Network network = cm == null ? null : cm.getActiveNetwork();
            LinkProperties link = network == null ? null : cm.getLinkProperties(network);
            if (link != null) {
                for (InetAddress address : link.getDnsServers()) {
                    if (address instanceof Inet4Address) found.add(address.getHostAddress());
                }
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "DNS servers not readable; the server uses public resolvers", e);
        }
        return ServerRules.dnsList(found);
    }

    /** Something accepts a TCP connection on 127.0.0.1:port. Not TLS, not
     *  HTTP: uvicorn binds only after the app's start-up finished, so an
     *  accepted connection is a server that is ready. */
    private static boolean answers(int port) {
        Socket socket = new Socket();
        try {
            socket.connect(new InetSocketAddress("127.0.0.1", port), PROBE_TIMEOUT_MS);
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            try {
                socket.close();
            } catch (Exception ignored) {
                // nothing to release
            }
        }
    }
}
