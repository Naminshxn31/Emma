package com.emma.robot.control;

import android.content.Context;
import android.os.SystemClock;
import android.util.Log;

import com.aobo.robotsdk.AoboRobotManager;
import com.aobo.robotsdk.arm.ArmManager;
import com.aobo.robotsdk.config.SdkMode;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

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
    }

    private static final String TAG = "EmmaArmDirect";
    private static final String SU_BINARY = "/system/xbin/su";
    private static final String USB_VENDOR = "1a86";
    private static final String USB_PRODUCT = "7523";
    private static final String CH341_BIND = "/sys/bus/usb/drivers/ch341/bind";
    private static final int BAUD_RATE = 115200;
    private static final long CONNECT_TIMEOUT_MS = 5000L;

    private final AoboRobotManager robotManager;
    private final ArmManager armManager;
    private final Listener listener;
    private final ExecutorService poseExecutor = Executors.newSingleThreadExecutor();
    private final AtomicInteger poseGeneration = new AtomicInteger();
    private volatile String port;

    ArmController(Context context, Listener listener) {
        this.listener = listener;
        AoboRobotManager.init(context.getApplicationContext(), SdkMode.REAL);
        robotManager = AoboRobotManager.getInstance();
        armManager = robotManager.getArmManager();
        armManager.setCallback(new ArmManager.ArmCallback() {
            @Override
            public void onConnected() {
                listener.onArmConnected(port == null ? armManager.getDevice() : port);
            }

            @Override
            public void onDisconnected() {
                listener.onArmDisconnected();
            }

            @Override
            public void onError(String detail) {
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
                    listener.onArmData(new String(data, 0, safeLength, StandardCharsets.US_ASCII).trim());
                }
            }
        });
    }

    boolean connect() throws Exception {
        if (armManager.isConnected()) return true;
        port = prepareInternalArmPort();
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

    boolean isConnected() {
        return armManager.isConnected();
    }

    void playGroup(int group) {
        requireConnected();
        if (group < 1 || group > 26) {
            throw new IllegalArgumentException("กลุ่มท่าต้องอยู่ระหว่าง 1–26");
        }
        poseGeneration.incrementAndGet();
        robotManager.executeArmActionGroup(group, 1);
    }

    void resetHome() {
        requireConnected();
        poseGeneration.incrementAndGet();
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
        requireConnected();
        if (source == null || source.isEmpty()) {
            throw new IllegalArgumentException("ร่างท่ายังไม่มีคีย์เฟรม");
        }
        final ArrayList<PoseFrame> frames = new ArrayList<>(source);
        final int run = poseGeneration.incrementAndGet();
        poseExecutor.execute(() -> {
            for (int index = 0; index < frames.size(); index++) {
                if (run != poseGeneration.get() || !armManager.isConnected()) return;
                PoseFrame frame = frames.get(index);
                String command = frameCommand(frame);
                armManager.f(command + "\r\n");
                listener.onArmCommand(command + " · คีย์เฟรม " + (index + 1)
                        + "/" + frames.size());
                long deadline = SystemClock.elapsedRealtime() + frame.durationMs;
                while (run == poseGeneration.get() && SystemClock.elapsedRealtime() < deadline) {
                    SystemClock.sleep(Math.min(50L,
                            Math.max(1L, deadline - SystemClock.elapsedRealtime())));
                }
            }
        });
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
        if (!armManager.isConnected()) throw new IllegalStateException("ยังไม่ได้เชื่อมบอร์ดแขน");
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

    private static void runRoot(String command) throws Exception {
        Process process = new ProcessBuilder(SU_BINARY, "0", "sh", "-c", command)
                .redirectErrorStream(true)
                .start();
        StringBuilder output = new StringBuilder();
        try (InputStream stream = process.getInputStream();
             BufferedReader reader = new BufferedReader(new InputStreamReader(
                     stream, StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) output.append(line).append('\n');
        }
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IllegalStateException("เตรียมพอร์ตแขนไม่สำเร็จ (exit " + exitCode
                    + "): " + output.toString().trim());
        }
    }
}
