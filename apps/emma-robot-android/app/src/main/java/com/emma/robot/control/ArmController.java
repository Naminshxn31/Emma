package com.emma.robot.control;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.os.Build;
import android.os.SystemClock;
import android.util.Log;

import com.aobo.robotsdk.AoboRobotManager;
import com.aobo.robotsdk.arm.ArmManager;
import com.aobo.robotsdk.config.SdkMode;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

/** Owns the robot's internal CH340 arm board while the vendor app is stopped. */
final class ArmController {
    static final class PoseFrame {
        final Map<Integer, Integer> targets;
        final int durationMs;

        PoseFrame(Map<Integer, Integer> targets, int durationMs) {
            if (targets == null || targets.isEmpty()) {
                throw new IllegalArgumentException("คีย์เฟรมต้องมีอย่างน้อยหนึ่งช่อง");
            }
            if (durationMs < 100 || durationMs > 5000) {
                throw new IllegalArgumentException("เวลาคีย์เฟรมต้องอยู่ระหว่าง 100–5000 ms");
            }
            LinkedHashMap<Integer, Integer> checked = new LinkedHashMap<>();
            ArrayList<Integer> channels = new ArrayList<>(targets.keySet());
            Collections.sort(channels);
            for (int channel : channels) {
                int pulse = targets.get(channel);
                if (channel < 1 || channel > 20) {
                    throw new IllegalArgumentException("ช่องเซอร์โวต้องอยู่ระหว่าง 1–20");
                }
                if (pulse < 500 || pulse > 2500) {
                    throw new IllegalArgumentException("ค่าเซอร์โวต้องอยู่ระหว่าง 500–2500");
                }
                checked.put(channel, pulse);
            }
            this.targets = Collections.unmodifiableMap(checked);
            this.durationMs = durationMs;
        }
    }

    interface Listener {
        void onArmConnected(String port);
        void onArmDisconnected();
        void onArmError(String detail);
        void onArmCommand(String command);
        void onArmData(String data);
        /** The board's #AGF ("action group finished"), framed across reads:
         *  fired once per #AGF however the serial chunks split it. */
        void onArmFinished();
        /** Review2 #10: the serial link died under us (a write failed, the
         *  CH340 was detached, its ttyUSB node vanished). Called once per
         *  loss, BEFORE the link is torn down (onArmDisconnected follows). */
        void onArmLinkLost(String why);
    }

    private static final String TAG = "EmmaArmDirect";
    private static final String USB_VENDOR = "1a86";
    private static final String USB_PRODUCT = "7523";
    private static final int USB_VENDOR_ID = 0x1a86;
    private static final int USB_PRODUCT_ID = 0x7523;
    private static final String CH341_BIND = "/sys/bus/usb/drivers/ch341/bind";
    private static final int BAUD_RATE = 115200;
    private static final long CONNECT_TIMEOUT_MS = 5000L;

    private final AoboRobotManager robotManager;
    private final ArmManager armManager;
    private final Listener listener;
    private final ExecutorService poseExecutor = Executors.newSingleThreadExecutor();
    private final AtomicInteger poseGeneration = new AtomicInteger();
    private volatile String port;
    /** Review #30: the vendor read loop hands onDataReceived whatever
     *  available() returned, with no line framing, so "#AGF" can arrive as
     *  "#A" + "GF". A per-chunk contains() missed it: the handshake stayed out
     *  until the 30 s fallback and a walk's rest waited 15 s, then refused
     *  (arms_not_rest). AgfFramer carries the last bytes into the next chunk. */
    private final Object rxLock = new Object();
    private final AgfFramer agf = new AgfFramer();
    /**
     * Review2 #10: review #13's "the board dropped off" check read
     * ArmManager.isConnected(), and javap of the vendor SDK (v2.1.2) shows that
     * flag is cleared ONLY by disconnect(): a dead CH340 makes the read loop
     * log and exit, and every write post onError -- the flag stays true and
     * onDisconnected never comes. So a frame gesture "played" into a dead fd
     * and was recorded done, ensureArm never reconnected (the arm stayed dead
     * until an app restart, X could not recover it), and every walk sat 15 s
     * in "preparing" before arms_not_rest. The loss is now read from real
     * signals -- a write error (onError while connected), the USB detach
     * broadcast for the CH340, the ttyUSB node gone -- and linkLost() tears
     * the link down itself, so the SDK flag clears and the next connect is
     * a real one.
     */
    private final Object linkLock = new Object();
    /** The two writers of raw board lines -- a pose run's frames and the head
     *  look (writeLine) -- take this around the write, so their bytes never
     *  interleave on the serial line. */
    private final Object writeLock = new Object();
    private volatile boolean linkDown = false;
    /** The current frame run's abort, once; null when none is playing. */
    private final AtomicReference<Runnable> frameAbort = new AtomicReference<>(null);
    private final Context appContext;
    private final BroadcastReceiver usbDetach = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (intent == null || !UsbManager.ACTION_USB_DEVICE_DETACHED.equals(intent.getAction())) return;
            UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
            if (device != null && device.getVendorId() == USB_VENDOR_ID && device.getProductId() == USB_PRODUCT_ID) {
                linkLost("CH340 detached");
            }
        }
    };
    private volatile boolean detachRegistered = false;

    ArmController(Context context, Listener listener) {
        this.listener = listener;
        this.appContext = context.getApplicationContext();
        AoboRobotManager.init(appContext, SdkMode.REAL);
        robotManager = AoboRobotManager.getInstance();
        armManager = robotManager.getArmManager();
        armManager.setCallback(new ArmManager.ArmCallback() {
            @Override
            public void onConnected() {
                listener.onArmConnected(port == null ? armManager.getDevice() : port);
            }

            @Override
            public void onDisconnected() {
                clearRxTail();
                listener.onArmDisconnected();
            }

            @Override
            public void onError(String detail) {
                // review2 #10: the SDK posts onError for a failed connect
                // (flag never set) and for a failed write. While the flag says
                // connected, it is the write: the link is gone.
                if (armManager.isConnected()) linkLost("serial write failed");
                listener.onArmError(detail);
            }

            @Override
            public void onCommandSent(String command) {
                listener.onArmCommand(command);
            }

            @Override
            public void onDataReceived(byte[] data, int length) {
                int safeLength = Math.max(0, Math.min(length, data == null ? 0 : data.length));
                if (safeLength > 0) {
                    String chunk = new String(data, 0, safeLength, StandardCharsets.US_ASCII);
                    boolean finished = agfIn(chunk);
                    listener.onArmData(chunk.trim());
                    if (finished) listener.onArmFinished();
                }
            }
        });
        // ACTION_USB_DEVICE_DETACHED is a protected system broadcast sent to
        // every receiver (no USB permission needed); EXPORTED only because it
        // comes from the system, nothing else can send that action.
        try {
            IntentFilter filter = new IntentFilter(UsbManager.ACTION_USB_DEVICE_DETACHED);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                appContext.registerReceiver(usbDetach, filter, Context.RECEIVER_EXPORTED);
            } else {
                appContext.registerReceiver(usbDetach, filter);
            }
            detachRegistered = true;
        } catch (RuntimeException e) {
            // the write-error and ttyUSB checks still see the loss
            Log.w(TAG, "USB detach receiver not registered", e);
        }
    }

    /** Review2 #10, once per loss, from any thread: the current frame run
     *  ends as aborted (its owner records failed/arm_unavailable, not done
     *  and not a bare stop), the listener hears it, and the SDK link is torn
     *  down -- that is what clears isConnected() and posts onDisconnected, so
     *  the next gesture, walk rest or X reconnects for real. The abort runs
     *  FIRST: onDisconnected leads to body.halt(), which would otherwise end
     *  the gesture as a plain "stopped". */
    void linkLost(String why) {
        synchronized (linkLock) {
            if (linkDown || !armManager.isConnected()) return;
            linkDown = true;
        }
        Log.w(TAG, "arm link lost: " + why);
        Runnable abort = frameAbort.get();
        if (abort != null) abort.run();
        try {
            listener.onArmLinkLost(why);
        } catch (RuntimeException e) {
            Log.w(TAG, "link-lost listener failed", e);
        }
        try {
            armManager.disconnect();
        } catch (RuntimeException e) {
            Log.w(TAG, "SDK disconnect after link loss failed", e);
        }
        clearRxTail();
    }

    /** False when the link is gone. A ttyUSB node that vanished (ueventd
     *  removes it on a USB drop) is reported as a loss right here. */
    private boolean linkUp() {
        if (linkDown || !armManager.isConnected()) return false;
        String node = port;
        if (node != null && !new File(node).exists()) {
            linkLost("ttyUSB node gone");
            return false;
        }
        return true;
    }

    /** onDestroy: the receiver is the app context's, so it outlives the Activity otherwise. */
    void release() {
        if (!detachRegistered) return;
        detachRegistered = false;
        try {
            appContext.unregisterReceiver(usbDetach);
        } catch (RuntimeException ignored) {
            // already gone
        }
    }

    private boolean agfIn(String chunk) {
        synchronized (rxLock) {
            return agf.feed(chunk);
        }
    }

    /** A stale prefix from an earlier frame must not join a later one. */
    private void clearRxTail() {
        synchronized (rxLock) {
            agf.reset();
        }
    }

    boolean connect() throws Exception {
        if (armManager.isConnected()) {
            if (!linkDown) return true;
            // review2 #10: a lost link the SDK still calls connected
            armManager.disconnect();
        }
        port = prepareInternalArmPort();
        linkDown = false;      // a new link; errors of the old one were handled
        robotManager.connectArm(port, BAUD_RATE);
        long deadline = SystemClock.elapsedRealtime() + CONNECT_TIMEOUT_MS;
        while (!armManager.isConnected() && SystemClock.elapsedRealtime() < deadline) {
            SystemClock.sleep(50L);
        }
        if (!armManager.isConnected()) {
            throw new IllegalStateException("ต่อบอร์ดแขนไม่สำเร็จที่ " + port);
        }
        return true;
    }

    /** Review2 #10: the SDK flag alone stays true over a dead CH340. */
    boolean isConnected() {
        return armManager.isConnected() && !linkDown;
    }

    void playGroup(int group) {
        requireConnected();
        if (group < 1 || group > 26) {
            throw new IllegalArgumentException("กลุ่มท่าต้องอยู่ระหว่าง 1–26");
        }
        poseGeneration.incrementAndGet();
        clearRxTail();
        robotManager.executeArmActionGroup(group, 1);
    }

    void resetHome() {
        requireConnected();
        poseGeneration.incrementAndGet();
        clearRxTail();
        // The vendor SDK demo uses action group 99 for its reset/home button.
        robotManager.executeArmActionGroup(99, 1);
    }

    void setServo(int channel, int pulseWidth, int durationMs) {
        requireConnected();
        if (channel < 1 || channel > 20) {
            throw new IllegalArgumentException("ช่องเซอร์โวต้องอยู่ระหว่าง 1–20");
        }
        armManager.setChannel(channel);
        armManager.setPosition(pulseWidth, durationMs);
    }

    void playFrames(List<PoseFrame> source) {
        playFrames(source, null, null, null);
    }

    /** onDone runs (on the pose thread) only when EVERY frame was sent and its
     *  time ran out. A stop, a group or a newer sequence bumps poseGeneration
     *  and the callback never fires: the voice gesture's owner (BodyController)
     *  ends its own state on a stop, and a rest-return keyed on a sequence
     *  that was stopped is exactly the "moves on a timer after a stop" bug.
     *
     *  stillCurrent (review #3): the OWNER's generation, checked next to
     *  poseGeneration before every frame. A stopAll() that lands between the
     *  owner's last check and this call bumps poseGeneration BEFORE this run
     *  takes its number, so poseGeneration alone saw the run as current and
     *  played every frame after an acknowledged STOP. null = no owner (the
     *  operator's draft).
     *
     *  onAbort (review #13): the loop also ends when the arm board drops off
     *  (CH340 glitch, cable, board reset). That used to return silently, so
     *  the owner never heard an end: busy, the SafetyBar and every gate stayed
     *  stuck until somebody pressed STOP. It runs only when the run was still
     *  current -- a run ended by a stop or a newer run is not an abort.
     *
     *  Review2 #10: "dropped off" is linkUp(), not the SDK flag (which a dead
     *  CH340 never clears), and the run is reachable from linkLost() through
     *  frameAbort -- a write that fails during a frame's wait aborts the run
     *  at once, and the last frame failing can no longer end in onDone. The
     *  abort runs at most once, whoever notices first. */
    void playFrames(List<PoseFrame> source, BooleanSupplier stillCurrent, Runnable onDone, Runnable onAbort) {
        requireConnected();
        if (source == null || source.isEmpty()) {
            throw new IllegalArgumentException("ร่างท่ายังไม่มีคีย์เฟรม");
        }
        final ArrayList<PoseFrame> frames = new ArrayList<>(source);
        final int run = poseGeneration.incrementAndGet();
        final AtomicBoolean ended = new AtomicBoolean(false);
        final Runnable abort = () -> {
            if (onAbort != null && run == poseGeneration.get() && ended.compareAndSet(false, true)) onAbort.run();
        };
        poseExecutor.execute(() -> {
            frameAbort.set(abort);
            try {
                for (int index = 0; index < frames.size(); index++) {
                    if (run != poseGeneration.get() || !ownerCurrent(stillCurrent)) return;
                    if (!linkUp()) {
                        abort.run();
                        return;
                    }
                    PoseFrame frame = frames.get(index);
                    String command = frameCommand(frame);
                    synchronized (writeLock) {
                        armManager.f(command + "\r\n");
                    }
                    listener.onArmCommand(command + " · คีย์เฟรม " + (index + 1)
                            + "/" + frames.size());
                    long deadline = SystemClock.elapsedRealtime() + frame.durationMs;
                    while (run == poseGeneration.get() && ownerCurrent(stillCurrent) && !linkDown
                            && SystemClock.elapsedRealtime() < deadline) {
                        SystemClock.sleep(Math.min(50L,
                                Math.max(1L, deadline - SystemClock.elapsedRealtime())));
                    }
                }
                // the last frame's write can fail during its own wait
                if (!linkUp()) {
                    abort.run();
                    return;
                }
                if (onDone != null && run == poseGeneration.get() && ownerCurrent(stillCurrent)
                        && ended.compareAndSet(false, true)) onDone.run();
            } finally {
                frameAbort.compareAndSet(abort, null);
            }
        });
    }

    /** One raw line for the head look (HeadLook.command, owner 31 Oct). Not a
     *  pose run: poseGeneration is NOT bumped, so a speech gesture's frames on
     *  the arm channels keep playing while the head turns (the board takes
     *  each channel's target on its own -- to be watched on the bench). A
     *  stop still wins: stopAll is the board's stop for every channel. */
    void writeLine(String command) {
        requireConnected();
        if (!linkUp()) throw new IllegalStateException("arm link down");
        synchronized (writeLock) {
            armManager.f(command + "\r\n");
        }
        listener.onArmCommand(command + " · หันหัว");
    }

    private static boolean ownerCurrent(BooleanSupplier stillCurrent) {
        return stillCurrent == null || stillCurrent.getAsBoolean();
    }

    private static String frameCommand(PoseFrame frame) {
        StringBuilder command = new StringBuilder();
        for (Map.Entry<Integer, Integer> target : frame.targets.entrySet()) {
            command.append('#').append(target.getKey())
                    .append('P').append(target.getValue());
        }
        command.append('T').append(frame.durationMs);
        return command.toString();
    }

    void stopAll() {
        poseGeneration.incrementAndGet();
        if (armManager.isConnected()) robotManager.stopArmAll();
    }

    void disconnect() {
        poseGeneration.incrementAndGet();
        if (armManager.isConnected()) {
            robotManager.stopArmAll();
            SystemClock.sleep(80L);
            robotManager.disconnectArm();
        }
    }

    private void requireConnected() {
        if (!isConnected()) throw new IllegalStateException("ยังไม่ได้เชื่อมบอร์ดแขน");
    }

    private String prepareInternalArmPort() throws Exception {
        File usbRoot = new File("/sys/bus/usb/devices");
        File[] devices = usbRoot.listFiles();
        if (devices == null) throw new IllegalStateException("อ่านรายการ USB ภายในไม่ได้");

        File armDevice = null;
        for (File device : devices) {
            if (USB_VENDOR.equalsIgnoreCase(readOneLine(new File(device, "idVendor")))
                    && USB_PRODUCT.equalsIgnoreCase(readOneLine(new File(device, "idProduct")))) {
                armDevice = device;
                break;
            }
        }
        if (armDevice == null) throw new IllegalStateException("ไม่พบบอร์ดแขน CH340 1a86:7523");

        File armInterface = null;
        File[] entries = usbRoot.listFiles();
        String interfacePrefix = armDevice.getName() + ":";
        if (entries != null) {
            for (File entry : entries) {
                if (entry.getName().startsWith(interfacePrefix)
                        && "00".equals(readOneLine(new File(entry, "bInterfaceNumber")))) {
                    armInterface = entry;
                    break;
                }
            }
        }
        if (armInterface == null) throw new IllegalStateException("ไม่พบ interface ของบอร์ดแขน");
        if (!new File(armInterface, "driver").exists()) {
            String name = armInterface.getName();
            if (!name.matches("[0-9]+-[0-9.]+:1\\.0")) {
                throw new IllegalStateException("ชื่อ interface ไม่อยู่ในรูปแบบที่อนุญาต: " + name);
            }
            runRoot("echo " + name + " > " + CH341_BIND);
            SystemClock.sleep(250L);
        }

        File serialRoot = new File("/sys/bus/usb-serial/devices");
        long deadline = SystemClock.elapsedRealtime() + 2500L;
        while (SystemClock.elapsedRealtime() < deadline) {
            File[] serialDevices = serialRoot.listFiles();
            if (serialDevices != null) {
                for (File serial : serialDevices) {
                    String canonical = new File(serial, "device").getCanonicalPath();
                    if (!canonical.contains(armInterface.getName())) continue;
                    String nodeName = serial.getName();
                    if (!nodeName.matches("ttyUSB[0-9]+")) continue;
                    String deviceNode = "/dev/" + nodeName;
                    runRoot("chmod 666 " + deviceNode);
                    Log.i(TAG, "prepared internal arm port " + deviceNode
                            + " from " + armInterface.getName());
                    return deviceNode;
                }
            }
            SystemClock.sleep(50L);
        }
        throw new IllegalStateException("ผูก CH340 แล้วแต่ไม่พบ ttyUSB ของแขน");
    }

    private static String readOneLine(File file) {
        if (!file.isFile()) return "";
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(
                new FileInputStream(file), StandardCharsets.US_ASCII))) {
            String value = reader.readLine();
            return value == null ? "" : value.trim();
        } catch (Exception ignored) {
            return "";
        }
    }

    /** Review #32: a bare waitFor() here hung io for as long as su or the
     *  shell did, and a walk's arm connect runs on io. Bounded like every
     *  other su call; a timeout is the existing arm_unavailable path. */
    private static void runRoot(String command) throws Exception {
        RootShell.Result result = RootShell.run(command, RootShell.TIMEOUT_MS);
        if (result.exit != 0) {
            throw new IllegalStateException("เตรียมพอร์ตแขนไม่สำเร็จ (exit " + result.exit
                    + "): " + result.out.trim());
        }
    }
}
