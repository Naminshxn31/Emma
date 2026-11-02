package com.emma.robot.control;

import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioRecordingConfiguration;
import android.media.AudioTimestamp;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import javax.net.ssl.HttpsURLConnection;

/**
 * The robot's ears for DIRECTION (docs/listening-focus-2026-10-09.md, input
 * 4): opens the USB array "Bothlent UAC Dongle" natively -- 8 channels,
 * 16 kHz, channel index mask 0xff (measured: the audio policy exposes
 * 0x800000ff for it) -- and sends GCC-PHAT lags of channels 0-3 to the
 * server's /ws/robot-doa. No audio leaves the robot on that socket: lags,
 * peak strengths, one RMS, and a status line.
 *
 * When it runs, all of these at once:
 *  - the server says so: /ws/robot-doa answers `doa_ready` only with
 *    DOA_ENABLED on (the master switch, off by default), `doa_off` otherwise
 *    -- the array is not even opened for a server that will not use it;
 *  - the operator screen's switch "ฟังทิศเสียง" is on (default on: the
 *    server switch is the master; this one is the robot-side way out if the
 *    capture turns out to hurt the conversation mic);
 *  - the face is in front (MainActivity: onResume and showEmma start it,
 *    onPause and the operator screen stop it -- never a mic behind another
 *    screen).
 *
 * The open question it is built to answer on the robot: can this capture
 * run beside the WebView's own (getUserMedia, stereo)? It is watched, not
 * assumed. Each of these stops the capture, says why ONCE in logcat
 * (tag EmmaArray), reports it on the link and on the operator screen, and
 * stays stopped until the face comes back to the front:
 *  - no 8-channel USB input, or every audio source refused   (capture_refused)
 *  - our record or any OTHER record of this app (the WebView's) reported
 *    silenced by the audio policy (API 29+ isClientSilenced)   (conflict)
 *  - a capture of this app that was running when ours started is gone
 *    within START_GRACE_MS (the WebView lost its mic to us)    (conflict)
 *  - ZERO_LIMIT_MS of all-zero samples: digital silence, which a real
 *    microphone never produces (CLAUDE.md: peak=0.0000 for 16 s was a
 *    second capture of one mic on Windows)                     (conflict)
 * The conversation is the product; direction is an extra.
 *
 * Threads: "emma-doa-link" (connect, read, backoff), "emma-doa-send" (the
 * only writer; statuses every stats period), "emma-doa-capture" (AudioRecord
 * + DoaMath). A generation counter retires all three on pause.
 *
 * ARRAY_AUDIO (app/array_audio.py): when doa_ready carries `array`, the same
 * capture also runs {@link ArrayBeam} on channels 0-3, hop by hop (16 ms),
 * steered by the server's `array_steer` messages, and sends each hop of the
 * beam up the same socket as a binary frame -- the conversation's audio,
 * which the server uses instead of the WebView's while it is live. Without
 * `array` in doa_ready nothing here changes: the reads are the DOA frame
 * size, no binary frame is ever sent. The debug-only `do capture8` uploads
 * N seconds of the RAW 8 channels (the channels 4-7 question) only when
 * doa_ready carries `capture8` (ARRAY_CAPTURE8 on the server).
 *
 * ARRAY_MIC (owner 2 Nov 2026: "ต้องแก้แอปให้ถือไมค์เองและทำเส้นเสียงใหม่";
 * docs/emma-array-mic-2026-11-02.md): the measured answer to the question
 * above is NO -- the array gives Android one capture stream (10 Oct: this
 * capture first = the WebView's mic reads exact zeros, the WebView first =
 * this one gets 2 of 8 channels). So when doa_ready carries `array_mic` this
 * capture is the array's ONLY owner and the conversation's audio is its
 * beam; the face page runs a silent source instead of getUserMedia. The page
 * takes the mic back only after this capture has ENDED -- for one call
 * ({@link #micHold}: emma-face:mic:hold / :hold:deaf, lifted by
 * {@link #micFree} once the call's mic tracks are stopped or a page loads) or
 * for the rest of this app run ({@link #micRelease}, or the server's
 * `array_release` for a final reason) -- and {@link #micState} says
 * "capturing" until the AudioRecord is released: the page polls it before it
 * opens its own mic. The rules are {@link MicOwner}'s; never both capturing
 * at once: startCapture checks {@code micOwner.mayStart()} inside
 * {@code synchronized (micOwner)}, the lock every hold and release takes.
 */
final class ArrayListener {
    interface Host {
        /** wss://.../ws/robot-doa?token=..., or null (no server or token). */
        String doaUrl();
        /** ROBOT_FACE_TOKEN as typed on the operator screen, or "". */
        String faceToken();
        /** Any thread. */
        void changed();
    }

    static final String PREFS = "emma_doa";
    private static final String PREF_ENABLED = "enabled";
    private static final String TAG = "EmmaArray";
    /** EmmaRobot/ is what the server checks (like /ws/robot-face). */
    /** EmmaArrayMic/1: this build can hold the array alone (ARRAY_MIC); the
     *  server never asks an app without it (an older one gets doa_off). */
    static final String USER_AGENT = "EmmaRobot/0.1 EmmaArray/1 EmmaArrayMic/1";
    /** micState when there is no listener at all: nothing holds the array. */
    static final String NO_ARRAY_STATE = "{\"sole\":false,\"capturing\":false,\"released\":false,\"held\":false}";
    private static final int RATE = 16000;
    private static final int INDEX_MASK_8 = 0xff;
    private static final int CONNECT_TIMEOUT_MS = 8000;
    /** The server beats every 15 s: five beats missed = the link is dead. */
    private static final int READ_TIMEOUT_MS = 75000;
    private static final long START_GRACE_MS = 2000L;
    private static final long NO_SERVER_RETRY_MS = 30000L;

    private final Activity activity;
    private final Host host;
    private final AudioManager audio;
    private final SharedPreferences prefs;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final AtomicInteger generation = new AtomicInteger(0);
    /** Strings (JSON) and byte[] (binary frames). Bounded: a full outbox
     *  drops the newest frame and counts it -- a late beam chunk is worth
     *  less than a live one, and the server falls back to the page. */
    private final ArrayBlockingQueue<Object> outbox = new ArrayBlockingQueue<>(96);

    private volatile boolean resumed = false;
    /** One run = one link thread + one sender, with its own socket: a run
     *  that was replaced can never hand its socket to the next one. */
    private static final class Run {
        final int gen;
        volatile DoaLink link;

        Run(int gen) {
            this.gen = gen;
        }
    }

    private volatile Run run;
    private volatile AudioRecord record;
    private volatile Thread captureThread;
    /** Our record's session id while capturing, else 0. */
    private volatile int session = 0;
    private volatile Set<Integer> othersAtStart = new HashSet<>();
    private volatile long startedAt = 0L;
    private volatile boolean captureWanted = false;
    /** Why capture is off until the next resume (null = it may start). */
    private volatile String latched;
    /** The server refused the link in a way a retry cannot fix (this process). */
    private volatile String refusedForGood;
    private volatile DoaMath.Params params = new DoaMath.Params().clamp(RATE);
    /** ARRAY_AUDIO: doa_ready's `array` said to beamform ("ds"/"mvdr"), else null. */
    private volatile String beamMode;
    private volatile ArrayBeam.Steer beamSteer = ArrayBeam.Steer.none();
    /** The capture thread's beam, read for the status counters only. */
    private volatile ArrayBeam liveBeam;
    private volatile long beamSent = 0;
    private volatile long beamDropped = 0;
    /** ARRAY_CAPTURE8: doa_ready's `capture8` allows the raw upload. */
    private volatile boolean capture8Allowed = false;
    private volatile double capture8MaxS = 0;
    private volatile long capture8Left = 0;
    private volatile int capture8Seq = 0;
    private volatile long raw8Sent = 0;
    /** ARRAY_MIC: doa_ready's `array_mic` -- this capture is the array's only owner. */
    private volatile boolean soleMode = false;
    /** ARRAY_MIC: who may capture the array (held for a call / released for
     *  the run); also the lock of startCapture's last check. resume() does NOT
     *  reset it: a hold is lifted by the page (micFree), a release by a restart. */
    private final MicOwner micOwner = new MicOwner();
    /** After a hold is lifted: the page's recordings must end first (up to this). */
    private static final long FREE_WAIT_MS = 3000L;

    // status (any thread writes, the sender and the screen read)
    private volatile String state = "stopped";
    private volatile String linkState = "idle";
    private volatile String source;
    private volatile String device;
    private volatile String detail;
    private volatile double[] rms8;
    private volatile int others = 0;
    private volatile Boolean othersSilenced;
    private volatile Boolean selfSilenced;
    private volatile long zeroMs = 0;
    private volatile long sent = 0;
    private volatile long gated = 0;
    private volatile long nextRetryAt = 0;
    /** The line as it stood when the face left the front: the operator
     *  screen pauses the capture, and is also where staff read its result. */
    private volatile String lastActive;

    private final AudioManager.AudioRecordingCallback recordingCallback = new AudioManager.AudioRecordingCallback() {
        @Override
        public void onRecordingConfigChanged(List<AudioRecordingConfiguration> configs) {
            checkConcurrency(configs);
        }
    };

    ArrayListener(Activity activity, Host host) {
        this.activity = activity;
        this.host = host;
        this.audio = (AudioManager) activity.getSystemService(Context.AUDIO_SERVICE);
        this.prefs = activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if ((activity.getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0) {
            IntentFilter filter = new IntentFilter(DEBUG_ACTION);
            // Exported (the sender is `adb shell am broadcast`), so only a
            // sender holding DEBUG_SENDER_PERMISSION gets through.
            if (Build.VERSION.SDK_INT >= 33) {
                activity.registerReceiver(debugReceiver, filter, DEBUG_SENDER_PERMISSION, null, Context.RECEIVER_EXPORTED);
            } else {
                activity.registerReceiver(debugReceiver, filter, DEBUG_SENDER_PERMISSION, null);
            }
            debugRegistered = true;
        }
    }

    /** MainActivity.onDestroy. */
    void shutdown() {
        pause();
        if (debugRegistered) {
            debugRegistered = false;
            try {
                activity.unregisterReceiver(debugReceiver);
            } catch (IllegalArgumentException ignored) {
                // never registered
            }
        }
    }

    // ---- the on-robot concurrency test (debug builds only) ----

    /** `adb shell am broadcast -a com.emma.robot.control.DOA --es do restart|off|on`.
     *  The open question is whether this capture can share the array with the
     *  WebView's; testing it needs both ORDERS and an A/B inside one call, and
     *  every on-screen way to touch this capture (the operator screen) pauses
     *  the face and ends the call. restart = stop and reopen now (with a call
     *  running, the WebView is the one already capturing) and clears a
     *  conflict latch; off = hold the capture closed until `on` (the call
     *  without it); on = release the hold. Debug builds only -- the robot
     *  runs one -- and nothing here can open the array unless the server's
     *  link is ready: it only restarts or holds what the rules already allow. */
    static final String DEBUG_ACTION = "com.emma.robot.control.DOA";
    /** Review, 10 Oct: the receiver was exported with no permission, so in
     *  the debug build the robot runs ANY installed app could send
     *  `do capture8` (raw 8-channel room audio to the server) or hold the
     *  capture off. DUMP is held by the adb shell (and root) and by no
     *  installed app -- signature|privileged|development -- so the adb test
     *  hook keeps working and nothing else reaches it. */
    static final String DEBUG_SENDER_PERMISSION = android.Manifest.permission.DUMP;
    private boolean debugRegistered = false;
    private volatile boolean debugHeld = false;
    private final BroadcastReceiver debugReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String what = intent.getStringExtra("do");
            Log.i(TAG, "adb doa: " + what);
            if ("off".equals(what)) {
                debugHeld = true;
                stopCapture();
            } else if ("on".equals(what) || "restart".equals(what)) {
                debugHeld = false;
                restartCapture();
            } else if ("capture8".equals(what)) {
                startCapture8(intent.getIntExtra("seconds", 5));
            }
            host.changed();
        }
    };

    /** `--es do capture8 --ei seconds N`: the next N seconds (capped by the
     *  server's max_s) of the raw 8 channels go to the server, which writes
     *  them as ARRAY8-TEST-*.s16le and deletes them minutes later. Needs the
     *  server's consent (doa_ready `capture8`) and a running capture: this
     *  never opens anything the rules would not. */
    private void startCapture8(int seconds) {
        if (!capture8Allowed) {
            Log.w(TAG, "capture8: the server did not allow it (ARRAY_CAPTURE8 off) -- nothing recorded");
            return;
        }
        if (!"capturing".equals(state) || !"ready".equals(linkState)) {
            Log.w(TAG, "capture8: no capture running (state=" + state + ", link=" + linkState + ")");
            return;
        }
        double s = Math.max(1, Math.min(capture8MaxS, seconds));
        capture8Seq = 0;
        capture8Left = Math.round(s * RATE);
        Log.i(TAG, "capture8: uploading " + s + " s of the raw 8 channels");
    }

    private void restartCapture() {
        Thread t = new Thread(() -> {
            Run r = run;
            if (r == null || !live(r) || !"ready".equals(linkState)) {
                Log.i(TAG, "adb doa: no ready link -- nothing to restart");
                return;
            }
            stopCapture();
            latched = null;
            startCapture(r);            // joins the previous capture: off the UI thread
        }, "emma-doa-restart");
        t.setDaemon(true);
        t.start();
    }

    boolean enabled() {
        return prefs.getBoolean(PREF_ENABLED, true);
    }

    void setEnabled(boolean on) {
        prefs.edit().putBoolean(PREF_ENABLED, on).apply();
        if (on && resumed) start();
        else if (!on) stopAll("off_local");
        host.changed();
    }

    /** The face is in front. Clears a capture latch: a person coming back
     *  to the face is the retry (never a timer that reopens the mic). */
    void resume() {
        Run current = run;
        boolean running = resumed && current != null && current.gen == generation.get();
        resumed = true;
        if (running) {
            // onResume and showEmma both land here: a second start would
            // leave the first link holding the server's one slot (busy)
            host.changed();
            return;
        }
        latched = null;
        if (enabled()) start();
        else linkState = "off_local";
        host.changed();
    }

    /** The face left the front (pause, operator screen, destroy). */
    void pause() {
        if (resumed && "ready".equals(linkState)) lastActive = statusLine();
        resumed = false;
        stopAll("paused");
        host.changed();
    }

    private void start() {
        if (refusedForGood != null) {
            linkState = "refused";
            return;
        }
        retire(run);
        stopCapture();                  // the next ready waits for it to let go
        final Run next = new Run(generation.incrementAndGet());
        run = next;
        outbox.clear();
        Thread linkThread = new Thread(() -> linkLoop(next), "emma-doa-link");
        linkThread.setDaemon(true);
        linkThread.start();
        Thread sender = new Thread(() -> sendLoop(next), "emma-doa-send");
        sender.setDaemon(true);
        sender.start();
    }

    private void stopAll(String why) {
        generation.incrementAndGet();
        capture8Left = 0;
        linkState = why;
        stopCapture();
        retire(run);
        run = null;
    }

    /** Close a run's socket off the UI thread (close() may block on a
     *  stalled socket); its reader then fails and its threads end. */
    private static void retire(Run old) {
        if (old == null) return;
        final DoaLink current = old.link;
        old.link = null;
        if (current != null) {
            Thread closer = new Thread(current::close, "emma-doa-close");
            closer.setDaemon(true);
            closer.start();
        }
    }

    private boolean live(Run r) {
        return generation.get() == r.gen && resumed && enabled();
    }

    // ---- the link ----

    private void linkLoop(Run r) {
        int failures = 0;
        while (live(r)) {
            String url = host.doaUrl();
            if (url == null) {
                linkState = "no_server";
                host.changed();
                if (!sleepWhileLive(r, NO_SERVER_RETRY_MS)) break;
                continue;
            }
            linkState = "connecting";
            host.changed();
            long retry;
            DoaLink opened = null;
            try {
                opened = DoaLink.open(url, USER_AGENT, CONNECT_TIMEOUT_MS,
                        HttpsURLConnection.getDefaultHostnameVerifier());
                r.link = opened;
                if (!live(r)) throw new java.io.IOException("run retired while connecting");
                opened.setReadTimeout(READ_TIMEOUT_MS);
                opened.sendText("{\"type\":\"doa_hello\",\"token\":" + DoaMath.quote(host.faceToken()) + "}");
                retry = serve(r, opened);
                if (retry == 0) failures = 0;           // ready was seen: a fresh start
            } catch (Exception e) {
                if (live(r)) Log.w(TAG, "doa link failed: " + e);
                retry = -1;
            } finally {
                if (live(r)) stopCapture();             // a retired run's stop already happened
                if (opened != null) opened.close();
                if (r.link == opened) r.link = null;
            }
            if (!live(r) || refusedForGood != null) break;
            long wait = retry > 0 ? retry : DoaMath.backoffMs(++failures);
            nextRetryAt = SystemClock.elapsedRealtime() + wait;
            if (retry == -1) linkState = "link_down";
            host.changed();
            if (!sleepWhileLive(r, wait)) break;
        }
        if (refusedForGood != null) linkState = "refused";
        host.changed();
    }

    /** Reads until the link ends. Returns 0 after a ready link dropped (the
     *  backoff restarts from its first step), >0 = wait exactly that long
     *  (doa_off), -1 = failed before ready, -2 = busy (backoff: the first
     *  retries are quick because the slot is usually OUR previous link,
     *  closed a moment ago; a second robot ends up asking once a minute). */
    private long serve(Run r, DoaLink opened) throws Exception {
        boolean ready = false;
        while (live(r)) {
            String text = opened.readText();
            if (text == null) return ready ? 0 : -1;
            JSONObject message;
            try {
                message = new JSONObject(text);
            } catch (Exception e) {
                continue;
            }
            if ("array_steer".equals(message.optString("type"))) {
                beamSteer = readSteer(message, params.maxLag);
                continue;
            }
            if ("array_release".equals(message.optString("type"))) {
                // ARRAY_MIC: the call found the array deaf (or could not start
                // on it); the page takes its mic. The why is the server's code,
                // shape-checked before it reaches a log or the operator screen.
                micTake("release", "server:" + DoaMath.codeOrEmpty(message.optString("why", "")));
                continue;
            }
            int action = DoaMath.linkAction(message.optString("type"), message.optString("code"));
            if (action == DoaMath.LINK_READY) {
                ready = true;
                outbox.clear();                 // nothing from before this link
                params = readParams(message);
                readArray(message);
                linkState = "ready";
                Log.i(TAG, "doa link ready: frame=" + params.frame + " channels="
                        + java.util.Arrays.toString(params.channels) + " gate=" + params.gateRms);
                startCapture(r);
                host.changed();
            } else if (action == DoaMath.LINK_OFF) {
                linkState = "server_off";
                return DoaMath.OFF_RETRY_MS;
            } else if (action == DoaMath.LINK_BUSY) {
                linkState = "busy";
                Log.w(TAG, "the server's doa slot is taken -- backing off");
                return -2;
            } else if (action == DoaMath.LINK_STOP_FOR_GOOD) {
                String code = message.optString("code", "error");
                refusedForGood = code;
                Log.w(TAG, "server refused the doa link (" + code + ") -- stopped until the app restarts");
                return -1;
            }
        }
        return ready ? 0 : -1;
    }

    /** doa_ready's optional `array` / `capture8`: absent = neither runs. */
    private void readArray(JSONObject m) {
        JSONObject array = m.optJSONObject("array");
        beamMode = array == null ? null : ("mvdr".equals(array.optString("beam")) ? "mvdr" : "ds");
        beamSteer = ArrayBeam.Steer.none();
        JSONObject c8 = m.optJSONObject("capture8");
        capture8Allowed = c8 != null;
        capture8MaxS = c8 == null ? 0 : Math.max(1, Math.min(10, c8.optDouble("max_s", 10)));
        capture8Left = 0;
        soleMode = beamMode != null && m.optJSONObject("array_mic") != null;
        if (beamMode != null) Log.i(TAG, "array audio on: beam=" + beamMode + (soleMode ? " (the array's only owner)" : ""));
    }

    /** `array_steer`: the target's arrival times etc., checked by Steer.parse
     *  (a bad number means "no target", never a wild filter). */
    static ArrayBeam.Steer readSteer(JSONObject m, double maxLag) {
        return ArrayBeam.Steer.parse(numbers(m.optJSONArray("tau")), m.optDouble("sigma", 0.3),
                numbers(m.optJSONArray("unsteered")), m.optString("mode", "ds"), m.optInt("gen", 0), maxLag,
                m.optBoolean("talking", false));
    }

    private static double[] numbers(JSONArray a) {
        if (a == null) return null;
        double[] out = new double[a.length()];
        for (int k = 0; k < out.length; k++) out[k] = a.optDouble(k, Double.NaN);
        return out;
    }

    private static DoaMath.Params readParams(JSONObject m) {
        DoaMath.Params p = new DoaMath.Params();
        p.frame = m.optInt("frame", p.frame);
        JSONArray ch = m.optJSONArray("channels");
        if (ch != null) {
            int[] picked = new int[ch.length()];
            for (int k = 0; k < picked.length; k++) picked[k] = ch.optInt(k, -1);
            p.channels = picked;
        }
        p.maxLag = m.optDouble("max_lag", p.maxLag);
        p.gateRms = m.optDouble("gate_rms", p.gateRms);
        p.maxFps = m.optInt("max_fps", p.maxFps);
        JSONArray band = m.optJSONArray("band");
        if (band != null && band.length() == 2) {
            p.fLo = band.optDouble(0, p.fLo);
            p.fHi = band.optDouble(1, p.fHi);
        }
        p.statsMs = Math.round(m.optDouble("stats_s", p.statsMs / 1000.0) * 1000);
        return p.clamp(RATE);
    }

    private boolean sleepWhileLive(Run r, long ms) {
        long until = SystemClock.elapsedRealtime() + ms;
        while (live(r)) {
            long left = until - SystemClock.elapsedRealtime();
            if (left <= 0) return true;
            try {
                Thread.sleep(Math.min(left, 500));
            } catch (InterruptedException e) {
                return false;
            }
        }
        return false;
    }

    // ---- the only writer ----

    private void sendLoop(Run r) {
        long lastStatus = 0;
        while (live(r)) {
            Object next;
            try {
                next = outbox.poll(250, TimeUnit.MILLISECONDS);
            } catch (InterruptedException e) {
                return;
            }
            DoaLink current = r.link;
            long now = SystemClock.elapsedRealtime();
            if (current == null || !"ready".equals(linkState)) {
                continue;
            }
            try {
                if (next instanceof String) current.sendText((String) next);
                else if (next instanceof byte[]) current.sendBinary((byte[]) next);
                if (now - lastStatus >= params.statsMs) {
                    lastStatus = now;
                    current.sendText(statusJson());
                }
            } catch (Exception e) {
                // the reader sees the same dead socket and reconnects
                current.close();
            }
        }
    }

    private void statusNow() {
        outbox.offer(statusJson());
        host.changed();
    }

    private String statusJson() {
        return DoaMath.statusJson(state, source, device, detail, rms8, others, othersSilenced, selfSilenced,
                zeroMs, sent, gated, RATE, DoaMath.CAPTURE_CHANNELS, beamCounters());
    }

    /** DoaMath.BEAM_FIELDS order; null (nothing appended) when the server
     *  asked for neither the beam nor the raw test. */
    private long[] beamCounters() {
        if (beamMode == null && !capture8Allowed) return null;
        ArrayBeam b = liveBeam;
        ArrayBeam.Steer st = beamSteer;
        return new long[]{st.mode, st.gen, beamSent, beamDropped, b == null ? 0 : b.hops,
                b == null ? 0 : b.mvdrHops, b == null ? 0 : b.quietHops, b == null ? 0 : b.otherHops,
                b == null ? 0 : b.targetHops, b == null ? 0 : b.heldHops, b == null ? 0 : b.guardTrips, raw8Sent};
    }

    // ---- the capture ----

    /** Link thread only (it may wait for the previous capture to let go of
     *  the array: a reconnect within a second used to find `record` still
     *  set and skip the capture for the whole new link). */
    private void startCapture(Run r) {
        if (captureWanted) return;
        if (!micOwner.mayStart()) {
            ownedElsewhere();
            return;
        }
        if (debugHeld) {
            state = "stopped";
            detail = "held closed by adb (do off)";
            statusNow();
            return;
        }
        if (latched != null) {
            state = latched;
            statusNow();
            return;
        }
        Thread previous = captureThread;
        if (previous != null && previous.isAlive()) {
            try {
                previous.join(2000);
            } catch (InterruptedException e) {
                return;
            }
            if (previous.isAlive()) {
                Log.w(TAG, "previous doa capture still closing -- not started on this link");
                return;
            }
        }
        synchronized (micOwner) {
            // ARRAY_MIC: checked again under the lock every hold and release
            // takes -- one that landed during the join above must win, or the
            // array opens after the page was told it may open its mic.
            if (!micOwner.mayStart()) {
                ownedElsewhere();
                return;
            }
            captureWanted = true;
            Thread capture = new Thread(() -> captureLoop(r), "emma-doa-capture");
            capture.setDaemon(true);
            capture.setPriority(Thread.NORM_PRIORITY + 1);
            captureThread = capture;
            capture.start();
        }
    }

    /** The page holds the array (for a call) or has it for the run: the state
     *  the status line and the server read. */
    private void ownedElsewhere() {
        state = micOwner.released() ? "released" : "held";
        detail = micOwner.why();
        statusNow();
    }

    /** ARRAY_MIC: let go of the array -- `kind` "release" (the rest of this
     *  app run), "start" or "deaf" (one call; MicOwner counts deaf ones and
     *  the DEAF_HOLDS_PER_RUN-th is a release). Any thread (the page's bridge
     *  on the UI thread, the link's reader). */
    private String micTake(String kind, String why) {
        boolean wasReleased;
        boolean released;
        synchronized (micOwner) {
            wasReleased = micOwner.released();
            if ("release".equals(kind)) micOwner.release(why);
            else micOwner.hold(kind);
            released = micOwner.released();
            stopCapture();
        }
        if (released && !wasReleased) {
            Log.w(TAG, "array handed back to the page (" + micOwner.why() + ") -- closed until the app restarts");
        } else if (!released) {
            Log.i(TAG, "array held for the page's call (" + kind + ")");
        }
        // a running capture says it itself when its finally has run
        if (!captureRunning()) ownedElsewhere();
        else statusNow();
        return micState();
    }

    /** The AudioRecord may still be open: wanted, or its thread not ended
     *  (the thread ends only after r.release()). */
    private boolean captureRunning() {
        Thread t = captureThread;
        return captureWanted || (t != null && t.isAlive());
    }

    /** ARRAY_MIC (emma-face:mic): the page opens its own mic only on capturing=false. */
    String micState() {
        return "{\"sole\":" + soleMode + ",\"capturing\":" + captureRunning()
                + ",\"released\":" + micOwner.released() + ",\"held\":" + micOwner.held() + "}";
    }

    /** ARRAY_MIC (emma-face:mic:release): for the rest of this run; answers like micState. */
    String micRelease(String why) {
        return micTake("release", why);
    }

    /** ARRAY_MIC (emma-face:mic:hold / :hold:deaf): for the page's call only. */
    String micHold(String kind) {
        return micTake("deaf".equals(kind) ? "deaf" : "start", null);
    }

    /** ARRAY_MIC (emma-face:mic:free): the page's call mic is stopped (it calls
     *  this after stopping its tracks) or a page just loaded. The app captures
     *  again once the link offers -- but only after the recordings this app's
     *  WebView still has open have ENDED (a track stop is asynchronous in the
     *  renderer; opening on top of it is the 10 Oct concurrency again, and
     *  checkConcurrency would latch "conflict" until the next resume). */
    String micFree() {
        if (micOwner.free()) {
            final Set<Integer> page = activeOthers(-1);
            Thread t = new Thread(() -> {
                long until = SystemClock.elapsedRealtime() + FREE_WAIT_MS;
                while (SystemClock.elapsedRealtime() < until) {
                    Set<Integer> now = activeOthers(-1);
                    now.retainAll(page);
                    if (now.isEmpty()) break;
                    try {
                        Thread.sleep(100);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                Run r = run;
                if (r != null && live(r) && "ready".equals(linkState) && soleMode) startCapture(r);
                else if (!captureRunning()) {
                    state = "stopped";
                    detail = null;
                    statusNow();
                }
            }, "emma-doa-free");
            t.setDaemon(true);
            t.start();
        }
        return micState();
    }

    private void stopCapture() {
        captureWanted = false;
        AudioRecord current = record;
        if (current != null) {
            try {
                current.stop();             // unblocks a read in the capture thread
            } catch (IllegalStateException ignored) {
                // never started, or already stopped
            }
        }
    }

    private void latch(String newState, String why) {
        if (latched == null) Log.w(TAG, "doa capture stopped: " + newState + " (" + why + ")");
        latched = newState;
        state = newState;
        detail = why;
        stopCapture();
        statusNow();
    }

    private AudioDeviceInfo findArray() {
        AudioDeviceInfo fallback = null;
        for (AudioDeviceInfo d : audio.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
            int type = d.getType();
            if (type != AudioDeviceInfo.TYPE_USB_DEVICE && type != AudioDeviceInfo.TYPE_USB_HEADSET) continue;
            for (int mask : d.getChannelIndexMasks()) if ((mask & INDEX_MASK_8) == INDEX_MASK_8) return d;
            for (int count : d.getChannelCounts()) if (count >= 8) return d;
            if (fallback == null) fallback = d;
        }
        return fallback;
    }

    private static String sourceName(int s) {
        if (s == MediaRecorder.AudioSource.VOICE_RECOGNITION) return "VOICE_RECOGNITION";
        if (s == MediaRecorder.AudioSource.UNPROCESSED) return "UNPROCESSED";
        return "MIC";
    }

    /** VOICE_RECOGNITION first (minimal processing, no AEC), UNPROCESSED when
     *  the device says it has it, MIC last. Which one worked is reported. */
    private AudioRecord open(AudioDeviceInfo array, StringBuilder failures) {
        int frameBytes = params.frame * DoaMath.CAPTURE_CHANNELS * 2;
        int buffer = Math.max(frameBytes * 4,
                AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_STEREO, AudioFormat.ENCODING_PCM_16BIT) * 8);
        boolean unprocessed = "true".equals(audio.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED));
        int[] sources = unprocessed
                ? new int[]{MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.UNPROCESSED,
                        MediaRecorder.AudioSource.MIC}
                : new int[]{MediaRecorder.AudioSource.VOICE_RECOGNITION, MediaRecorder.AudioSource.MIC};
        for (int s : sources) {
            AudioRecord r = null;
            try {
                AudioFormat format = new AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(RATE)
                        .setChannelIndexMask(INDEX_MASK_8)
                        .build();
                r = new AudioRecord.Builder().setAudioSource(s).setAudioFormat(format)
                        .setBufferSizeInBytes(buffer).build();
                if (r.getState() != AudioRecord.STATE_INITIALIZED) throw new IllegalStateException("not initialized");
                r.setPreferredDevice(array);
                othersAtStart = activeOthers(-1);
                r.startRecording();
                if (r.getRecordingState() != AudioRecord.RECORDSTATE_RECORDING) {
                    throw new IllegalStateException("not recording");
                }
                if (r.getChannelCount() != DoaMath.CAPTURE_CHANNELS) {
                    throw new IllegalStateException("channels=" + r.getChannelCount());
                }
                source = sourceName(s);
                return r;
            } catch (Exception e) {
                failures.append(sourceName(s)).append(": ").append(e.getClass().getSimpleName())
                        .append(' ').append(e.getMessage()).append("; ");
                if (r != null) {
                    try {
                        r.stop();
                    } catch (Exception ignored) {
                        // never started
                    }
                    r.release();
                }
            }
        }
        return null;
    }

    private void captureLoop(Run owner) {
        if (activity.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            // never a dialog from here: the face asks when a person taps to talk
            state = "no_permission";
            detail = "RECORD_AUDIO not granted yet";
            captureWanted = false;
            statusNow();
            return;
        }
        AudioDeviceInfo array = findArray();
        if (array == null) {
            latch("no_array", "no USB audio input");
            captureWanted = false;
            return;
        }
        device = String.valueOf(array.getProductName());
        state = "starting";
        StringBuilder failures = new StringBuilder();
        AudioRecord r = open(array, failures);
        if (r == null) {
            latch("capture_refused", failures.toString());
            captureWanted = false;
            return;
        }
        record = r;
        session = r.getAudioSessionId();
        startedAt = SystemClock.elapsedRealtime();
        zeroMs = 0;
        audio.registerAudioRecordingCallback(recordingCallback, main);
        state = "capturing";
        detail = null;
        Log.i(TAG, "doa capture started: source=" + source + " device=" + device + " rate=" + r.getSampleRate()
                + " channels=" + r.getChannelCount() + " others_at_start=" + othersAtStart.size());
        statusNow();
        main.post(() -> checkConcurrency(audio.getActiveRecordingConfigurations()));
        DoaMath.Params p = params;
        DoaMath math = new DoaMath(p.frame, RATE, p.maxLag, 0.25, p.fLo, p.fHi);
        // ARRAY_AUDIO: read hop by hop (the DOA frame is still assembled from
        // the same samples, in the same order) and beamform each hop.
        ArrayBeam beam = beamMode != null ? new ArrayBeam(RATE, p.maxLag) : null;
        liveBeam = beam;
        int readShorts = (beam != null ? ArrayBeam.HOP : p.frame) * DoaMath.CAPTURE_CHANNELS;
        float[][] hopIn = new float[DoaMath.MICS][ArrayBeam.HOP];
        short[] hopPcm = new short[ArrayBeam.HOP];
        int beamSeq = 0;
        DoaMath.Result result = new DoaMath.Result();
        short[] buf = new short[p.frame * DoaMath.CAPTURE_CHANNELS];
        float[][] x = new float[DoaMath.MICS][p.frame];
        double[] sumSquares = new double[DoaMath.CAPTURE_CHANNELS];
        long statFrames = 0;
        long statsAt = SystemClock.elapsedRealtime();
        long lastSent = 0;
        long framesRead = 0;
        long frameMs = p.frame * 1000L / RATE;
        long minGapMs = 1000L / p.maxFps;
        AudioTimestamp stamp = new AudioTimestamp();
        boolean routeChecked = false;
        try {
            while (captureWanted && live(owner)) {
                int at = 0;
                int fed = 0;                                      // shorts already beamformed
                while (at < buf.length && captureWanted) {
                    int want = Math.min(buf.length - at, readShorts - at % readShorts);
                    int n = r.read(buf, at, want, AudioRecord.READ_BLOCKING);
                    if (n < 0 && captureWanted) throw new IllegalStateException("read error " + n);
                    if (n <= 0) break;
                    at += n;
                    while (beam != null && at - fed >= ArrayBeam.HOP * DoaMath.CAPTURE_CHANNELS) {
                        pickHop(buf, fed / DoaMath.CAPTURE_CHANNELS, p.channels, hopIn);
                        beam.steer(beamSteer);
                        ArrayBeam.toPcm(beam.process(hopIn), 0, ArrayBeam.HOP, hopPcm, 0);
                        // the hop that came out started 2 hops ago (the read + the beam's latency)
                        long tMs = System.nanoTime() / 1000000L - 2L * ArrayBeam.HOP * 1000L / RATE;
                        if (outbox.offer(ArrayBeam.pack(ArrayBeam.KIND_BEAM, beamSeq, tMs, hopPcm, ArrayBeam.HOP))) {
                            beamSent++;
                        } else {
                            beamDropped++;
                        }
                        beamSeq = (beamSeq + 1) & 0xffff;
                        fed += ArrayBeam.HOP * DoaMath.CAPTURE_CHANNELS;
                    }
                }
                if (at < buf.length) break;                       // stopped
                framesRead += p.frame;
                if (capture8Left > 0 && capture8Allowed) {
                    // the channels 4-7 test: this frame, all eight channels, raw
                    int seq = capture8Seq;
                    capture8Seq = (seq + 1) & 0xffff;
                    if (outbox.offer(ArrayBeam.pack(ArrayBeam.KIND_RAW8, seq, System.nanoTime() / 1000000L,
                            buf, buf.length))) {
                        raw8Sent++;
                    }
                    capture8Left -= p.frame;
                    if (capture8Left <= 0) Log.i(TAG, "capture8: done (" + raw8Sent + " blocks sent)");
                }
                if (!routeChecked) {
                    routeChecked = true;
                    AudioDeviceInfo routed = r.getRoutedDevice();
                    if (routed != null && routed.getId() != array.getId()) {
                        latch("conflict", "routed to " + routed.getProductName() + ", not the array");
                        break;
                    }
                }
                int peak = DoaMath.accumulate(buf, p.frame, sumSquares);
                statFrames += p.frame;
                zeroMs = peak == 0 ? zeroMs + frameMs : 0;
                if (zeroMs >= DoaMath.ZERO_LIMIT_MS) {
                    latch("conflict", "digital silence " + zeroMs + " ms (the system muted this capture)");
                    break;
                }
                long now = SystemClock.elapsedRealtime();
                if (now - statsAt >= p.statsMs && statFrames > 0) {
                    double[] next = new double[DoaMath.CAPTURE_CHANNELS];
                    for (int c = 0; c < next.length; c++) {
                        next[c] = Math.sqrt(sumSquares[c] / statFrames) / 32768.0;
                        sumSquares[c] = 0;
                    }
                    rms8 = next;
                    statFrames = 0;
                    statsAt = now;
                    host.changed();
                }
                double level = DoaMath.pick(buf, p.frame, p.channels, x);
                if (level < p.gateRms) {
                    gated++;
                    continue;
                }
                if (now - lastSent < minGapMs) continue;
                lastSent = now;
                math.analyze(x, result);
                long centreNs;
                if (r.getTimestamp(stamp, AudioTimestamp.TIMEBASE_MONOTONIC) == AudioRecord.SUCCESS) {
                    centreNs = stamp.nanoTime + (long) ((framesRead - p.frame / 2 - stamp.framePosition)
                            * 1e9 / RATE);
                } else {
                    centreNs = System.nanoTime() - (long) (p.frame / 2 * 1e9 / RATE);
                }
                if (outbox.offer(DoaMath.frameJson(centreNs / 1000000L, result))) sent++;
            }
        } catch (Exception e) {
            // a stop() from pause/latch can surface as a failed read: only a
            // capture that was still wanted failed
            if (captureWanted && live(owner)) latch("conflict", "capture failed: " + e.getMessage());
        } finally {
            liveBeam = null;
            capture8Left = 0;
            try {
                audio.unregisterAudioRecordingCallback(recordingCallback);
            } catch (Exception ignored) {
                // not registered
            }
            session = 0;
            record = null;
            try {
                r.stop();
            } catch (Exception ignored) {
                // already stopped
            }
            r.release();
            captureWanted = false;
            if (micOwner.released() || micOwner.held()) {
                state = micOwner.released() ? "released" : "held";
                detail = micOwner.why();
            } else if (latched == null) {
                state = "stopped";
                if (debugHeld) detail = "held closed by adb (do off)";
            }
            statusNow();
        }
    }

    /** Four picked channels of one hop of the interleaved capture, from frame
     *  `first` of `buf`, full scale 1 (DoaMath.pick at an offset). */
    static void pickHop(short[] buf, int first, int[] channels, float[][] out) {
        for (int m = 0; m < DoaMath.MICS; m++) {
            int c = channels[m];
            float[] dst = out[m];
            for (int n = 0; n < ArrayBeam.HOP; n++) {
                dst[n] = buf[(first + n) * DoaMath.CAPTURE_CHANNELS + c] / 32768f;
            }
        }
    }

    // ---- concurrency with the WebView's own capture ----

    private Set<Integer> activeOthers(int ours) {
        Set<Integer> out = new HashSet<>();
        try {
            for (AudioRecordingConfiguration c : audio.getActiveRecordingConfigurations()) {
                if (c.getClientAudioSessionId() != ours) out.add(c.getClientAudioSessionId());
            }
        } catch (Exception ignored) {
            // treated as nobody else
        }
        return out;
    }

    /** Main thread. Every active recording this app can see: ours and the
     *  WebView's (same process, same uid). Any of them silenced, or the one
     *  that was running when ours started gone right after: ours stops. */
    private void checkConcurrency(List<AudioRecordingConfiguration> configs) {
        int ours = session;
        if (ours == 0 || configs == null) return;
        boolean selfMuted = false;
        boolean otherMuted = false;
        Set<Integer> now = new HashSet<>();
        StringBuilder sources = new StringBuilder();
        for (AudioRecordingConfiguration c : configs) {
            boolean silenced = Build.VERSION.SDK_INT >= 29 && c.isClientSilenced();
            if (c.getClientAudioSessionId() == ours) {
                selfMuted = silenced;
            } else {
                now.add(c.getClientAudioSessionId());
                otherMuted |= silenced;
                sources.append(c.getClientAudioSource()).append(silenced ? "(silenced) " : " ");
            }
        }
        others = now.size();
        othersSilenced = Build.VERSION.SDK_INT >= 29 ? otherMuted : null;
        selfSilenced = Build.VERSION.SDK_INT >= 29 ? selfMuted : null;
        Log.i(TAG, String.format(Locale.US, "recording configs: ours silenced=%s, others=%d [%s]",
                selfSilenced, others, sources.toString().trim()));
        if (otherMuted) {
            latch("conflict", "the WebView's capture was silenced while ours ran");
        } else if (selfMuted) {
            latch("conflict", "our capture was silenced (another capture has priority)");
        } else if (SystemClock.elapsedRealtime() - startedAt < START_GRACE_MS && !now.containsAll(othersAtStart)) {
            latch("conflict", "a capture that was running stopped when ours started");
        } else {
            statusNow();
        }
    }

    // ---- the operator screen ----

    String statusLine() {
        if (!enabled()) return "ทิศเสียง (DOA): ปิดบนหุ่น";
        String ls = linkState;
        if (refusedForGood != null) {
            return "ทิศเสียง: เซิร์ฟเวอร์ปฏิเสธ (" + refusedForGood + ") — หยุดจนกว่าจะเปิดแอปใหม่";
        }
        if (!"ready".equals(ls)) {
            switch (ls) {
                case "no_server": return "ทิศเสียง: ยังไม่ได้ตั้งเซิร์ฟเวอร์และ token ของหน้า Emma";
                case "connecting": return "ทิศเสียง: กำลังต่อ /ws/robot-doa…";
                case "server_off": return "ทิศเสียง: เซิร์ฟเวอร์ปิดอยู่ (DOA_ENABLED=false) — ไม่เปิดไมค์ 8 ช่อง";
                case "busy": return "ทิศเสียง: หุ่นอีกตัวใช้ช่องนี้อยู่ — รอ";
                case "link_down": {
                    long left = Math.max(0, nextRetryAt - SystemClock.elapsedRealtime()) / 1000;
                    return "ทิศเสียง: ต่อเซิร์ฟเวอร์ไม่ได้ — ลองใหม่ใน " + left + " วิ";
                }
                case "paused": return "ทิศเสียง: พัก (หน้า Emma ไม่ได้อยู่ด้านหน้า)"
                        + (lastActive == null ? "" : " · ล่าสุด: " + lastActive);
                default: return "ทิศเสียง: " + ls;
            }
        }
        String st = state;
        switch (st) {
            case "capturing": {
                double[] r = rms8;
                String levels = r == null ? "" : String.format(Locale.US, " · rms 0-7 %.4f %.4f %.4f %.4f | %.4f %.4f %.4f %.4f",
                        r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]);
                return "ทิศเสียง: ฟังอยู่ · " + source + " · " + device + " · ส่ง " + sent
                        + " · อื่น " + others + (Boolean.TRUE.equals(othersSilenced) ? " (ถูกปิดเสียง!)" : "") + levels
                        + beamLine();
            }
            case "no_permission": return "ทิศเสียง: ยังไม่ได้สิทธิ์ไมค์ — แตะคุยกับ Emma หนึ่งครั้งแล้วอนุญาต";
            case "no_array": return "ทิศเสียง: ไม่พบไมค์ USB 8 ช่อง";
            case "capture_refused": return "ทิศเสียง: เปิดไมค์ 8 ช่องไม่ได้ — " + detail;
            case "conflict": return "ทิศเสียง: หยุดเพราะชนกับไมค์ของหน้า Emma — " + detail
                    + " (กลับหน้า Emma เพื่อลองใหม่)";
            case "starting": return "ทิศเสียง: กำลังเปิดไมค์ 8 ช่อง…";
            case "released": return "ไมค์ 8 ช่อง: คืนให้หน้า Emma แล้ว (" + detail
                    + ") — หน้า Emma ใช้ไมค์ของตัวเองจนกว่าจะเปิดแอปใหม่";
            case "held": return "ไมค์ 8 ช่อง: พักให้หน้า Emma ใช้ไมค์ของตัวเองระหว่างสายนี้ (" + detail + ")";
            case "stopped": return "ทิศเสียง: ปิดไมค์ 8 ช่องอยู่" + (detail == null ? "" : " — " + detail);
            default: return "ทิศเสียง: " + st;
        }
    }

    /** The beam's part of the operator line (empty when the server did not ask). */
    private String beamLine() {
        if (beamMode == null) return capture8Left > 0 ? " · กำลังส่งเสียงดิบ 8 ช่อง (ทดสอบ)" : "";
        ArrayBeam.Steer st = beamSteer;
        return " · เสียงคุยจากไมค์ 8 ช่อง (" + beamMode + (soleMode ? ", ถือไมค์คนเดียว" : "") + ") ส่ง " + beamSent
                + (beamDropped > 0 ? " ทิ้ง " + beamDropped : "")
                + (st.steered() ? " · เล็งไปที่คนพูด" : " · ยังไม่เล็ง")
                + (capture8Left > 0 ? " · กำลังส่งเสียงดิบ 8 ช่อง (ทดสอบ)" : "");
    }

    int statusColor() {
        if (!enabled() || refusedForGood != null || "released".equals(state) || "held".equals(state)) return 0xFFB8C7D9;
        if ("capturing".equals(state) && "ready".equals(linkState)) return 0xFF5EE6A8;
        if ("conflict".equals(state) || "capture_refused".equals(state) || "no_array".equals(state)) return 0xFFFF5F73;
        return 0xFFFFC857;
    }
}
