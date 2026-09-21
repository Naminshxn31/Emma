package com.emma.robot.control;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.hardware.input.InputManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import com.slamtec.slamware.SlamwareCorePlatform;
import com.slamtec.slamware.action.ActionStatus;
import com.slamtec.slamware.action.IMoveAction;
import com.slamtec.slamware.action.MoveDirection;
import com.slamtec.slamware.robot.Pose;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Standalone Xbox controller for the robot's SLAMTEC chassis.
 *
 * The controller is paired to Android on the robot. Commands go from this
 * Android board to the chassis over its internal Ethernet link; no PC, web
 * page, ADB tunnel, Emma server, token, or vendor application is in the path.
 * Nothing moves merely because the app or controller connects: the operator
 * must arm this screen once with A, then move the left stick. Returning the
 * stick to centre is the driving deadman; B and every loss-of-control path
 * still stop and disarm the chassis.
 */
public final class MainActivity extends Activity implements InputManager.InputDeviceListener {
    private static final String TAG = "EmmaRobotAndroid";
    private static final String BASE_URL = "http://192.168.11.1:1448";
    private static final String ACTIONS = "/api/core/motion/v1/actions";
    private static final String CURRENT_ACTION = ACTIONS + "/:current";
    private static final String LASER_SCAN = "/api/core/system/v1/laserscan";
    private static final String POWER = "/api/core/system/v1/power/status";
    private static final String ROBOT_INFO = "/api/core/system/v1/robot/info";
    private static final String SDK_HOST = "192.168.11.1";
    private static final int SDK_PORT = 1445;
    private static final String VENDOR_PACKAGE = "com.aobo.robot.ai3";
    private static final String SU_BINARY = "/system/xbin/su";
    private static final int ARM_GROUP_MIN = 1;
    private static final int ARM_GROUP_MAX = 26;
    private static final int SERVO_CHANNEL_MIN = 1;
    private static final int SERVO_CHANNEL_MAX = 20;
    private static final int SERVO_PULSE_STEP = 40;
    private static final String STUDIO_PREFS = "emma_arm_studio";
    private static final String STUDIO_DRAFT = "draft_json";

    private static final int DIR_NONE = -1;
    private static final int DIR_FORWARD = 0;
    private static final int DIR_BACKWARD = 1;
    private static final int DIR_RIGHT = 2;
    private static final int DIR_LEFT = 3;

    private static final float STICK_DEAD_ZONE = 0.38f;
    private static final double CLEARANCE_METRES = 0.15;
    private static final double UNDOCK_STOP_MARGIN_METRES = 0.15;
    private static final double ARC_RADIANS = Math.toRadians(35.0);
    private static final float UNDOCK_METRES = 0.15f;
    private static final long UNDOCK_TIMEOUT_MS = 6000L;
    private static final int HTTP_TIMEOUT_MS = 800;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private final AtomicBoolean pumping = new AtomicBoolean(false);
    private final AtomicInteger generation = new AtomicInteger(0);

    private InputManager inputManager;
    private TextView controllerText;
    private TextView baseText;
    private TextView stateText;
    private TextView axesText;
    private TextView upperBodyText;
    private TextView armGroupText;
    private TextView servoText;
    private TextView channelEvidenceText;
    private TextView poseDraftText;
    private TextView armCommandText;
    private Button armButton;
    private Button upperBodyButton;

    private volatile boolean screenArmed = false;
    private volatile boolean controllerConnected = false;
    private volatile boolean baseConnected = false;
    private volatile boolean baseDocked = false;
    private volatile int direction = DIR_NONE;
    private volatile float lastX = 0f;
    private volatile float lastY = 0f;
    private volatile SlamwareCorePlatform sdkPlatform;
    private volatile IMoveAction sdkMove;
    private volatile boolean upperBodyArmed = false;
    private volatile int selectedArmGroup = 3;
    private volatile int selectedServoChannel = 5;
    private volatile int selectedServoPulse = 1500;
    private MotionDraft motionDraft;
    private ArmController upperBody;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        inputManager = (InputManager) getSystemService(INPUT_SERVICE);
        SharedPreferences studio = getSharedPreferences(STUDIO_PREFS, MODE_PRIVATE);
        motionDraft = MotionDraft.fromJson(studio.getString(STUDIO_DRAFT, ""));
        upperBody = new ArmController(this, new ArmController.Listener() {
            @Override public void onArmConnected(String port) {
                upperBodyArmed = true;
                updateUpperBody("แขน: เชื่อมแล้ว " + port, 0xFF5EE6A8);
            }
            @Override public void onArmDisconnected() {
                upperBodyArmed = false;
                updateUpperBody("แขน: ยังไม่เชื่อม", 0xFFFFC857);
            }
            @Override public void onArmError(String detail) {
                upperBodyArmed = false;
                updateUpperBody("แขน: " + detail, 0xFFFF5F73);
            }
            @Override public void onArmCommand(String command) {
                updateArmCommand("ส่ง: " + command.trim(), 0xFF80DFFF);
            }
            @Override public void onArmData(String data) {
                updateArmCommand("ตอบ: " + data, 0xFF5EE6A8);
            }
        });
        setContentView(buildUi());
        showState("ยังไม่เปิดควบคุม", 0xFFFFC857);
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_HORIZONTAL);
        root.setPadding(dp(36), dp(48), dp(36), dp(36));
        root.setBackgroundColor(Color.rgb(6, 17, 30));

        TextView title = text("EMMA · ROBOT CONTROL", 28, Color.WHITE);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap());

        TextView subtitle = text("ควบคุมหุ่นโดยตรง · ฐานล้อ แขน นิ้ว และจอย Xbox", 16, 0xFF80DFFF);
        subtitle.setGravity(Gravity.CENTER);
        root.addView(subtitle, matchWrap(0, 0, 0, 28));

        controllerText = text("จอย Xbox: ยังไม่พบ", 19, 0xFFFFC857);
        baseText = text("ฐานล้อ: กำลังตรวจ", 19, 0xFFFFC857);
        axesText = text("ก้านซ้าย  X 0.00  Y 0.00", 17, 0xFFB8C7D9);
        stateText = text("", 25, Color.WHITE);
        stateText.setGravity(Gravity.CENTER);
        root.addView(controllerText, matchWrap(0, 10, 0, 10));
        root.addView(baseText, matchWrap(0, 10, 0, 10));
        root.addView(axesText, matchWrap(0, 10, 0, 24));
        root.addView(stateText, matchWrap(0, 18, 0, 24));

        armButton = button("เปิดควบคุมฐานล้อ", 0xFF167A58);
        armButton.setEnabled(false);
        armButton.setOnClickListener(v -> {
            if (screenArmed) {
                stopAndDisarm("ปิดควบคุมแล้ว");
            } else if (controllerConnected && baseConnected) {
                armControls();
            }
        });
        root.addView(armButton, matchWrap(0, 18, 0, 18));

        Button stop = button("■ หยุดและล็อกทันที (B)", 0xFFB3263E);
        stop.setTextSize(22);
        stop.setOnClickListener(v -> stopAndDisarm("หยุดและล็อกแล้ว"));
        root.addView(stop, matchWrap(0, 18, 0, 20));

        TextView armTitle = text("แขนและนิ้ว", 23, Color.WHITE);
        root.addView(armTitle, matchWrap(0, 22, 0, 4));
        upperBodyText = text("แขน: ยังไม่เชื่อม", 17, 0xFFFFC857);
        root.addView(upperBodyText, matchWrap(0, 4, 0, 8));

        upperBodyButton = button("เชื่อมและเปิดแขน (X)", 0xFF16677A);
        upperBodyButton.setOnClickListener(v -> connectUpperBody());
        root.addView(upperBodyButton, matchWrap(0, 6, 0, 8));

        armGroupText = text("ชุดท่า 3 · เคยยืนยันว่าทำให้แขนยก", 18, 0xFF80DFFF);
        armGroupText.setGravity(Gravity.CENTER);
        root.addView(armGroupText, matchWrap(0, 8, 0, 4));
        LinearLayout groupRow = new LinearLayout(this);
        groupRow.setOrientation(LinearLayout.HORIZONTAL);
        Button groupDown = button("◀", 0xFF31465E);
        Button groupPlay = button("เล่น 1 รอบ (Y)", 0xFF167A58);
        Button groupUp = button("▶", 0xFF31465E);
        groupDown.setOnClickListener(v -> selectArmGroup(-1));
        groupPlay.setOnClickListener(v -> playSelectedArmGroup());
        groupUp.setOnClickListener(v -> selectArmGroup(1));
        groupRow.addView(groupDown, weightedWrap());
        groupRow.addView(groupPlay, weightedWrap());
        groupRow.addView(groupUp, weightedWrap());
        root.addView(groupRow, matchWrap(0, 4, 0, 10));

        servoText = text("ช่อง 5 · ค่าที่จะสั่ง 1500 (ไม่ใช่ตำแหน่งที่วัดได้)", 16, 0xFFB8C7D9);
        servoText.setGravity(Gravity.CENTER);
        root.addView(servoText, matchWrap(0, 8, 0, 4));
        LinearLayout servoRow = new LinearLayout(this);
        servoRow.setOrientation(LinearLayout.HORIZONTAL);
        Button channelDown = button("ช่อง −", 0xFF31465E);
        Button pulseDown = button("ค่า −40", 0xFF73551A);
        Button pulseUp = button("ค่า +40", 0xFF73551A);
        Button channelUp = button("ช่อง +", 0xFF31465E);
        channelDown.setOnClickListener(v -> selectServoChannel(-1));
        pulseDown.setOnClickListener(v -> changeServoPulse(-SERVO_PULSE_STEP));
        pulseUp.setOnClickListener(v -> changeServoPulse(SERVO_PULSE_STEP));
        channelUp.setOnClickListener(v -> selectServoChannel(1));
        servoRow.addView(channelDown, weightedWrap());
        servoRow.addView(pulseDown, weightedWrap());
        servoRow.addView(pulseUp, weightedWrap());
        servoRow.addView(channelUp, weightedWrap());
        root.addView(servoRow, matchWrap(0, 4, 0, 4));
        Button sendServo = button("ส่งค่าช่องนี้ช้า 3 วินาที", 0xFF73551A);
        sendServo.setOnClickListener(v -> sendSelectedServo());
        root.addView(sendServo, matchWrap(0, 4, 0, 8));
        Button resetArmPose = button("↺ คืนท่าพักเดิม (กลุ่ม 99)", 0xFF16677A);
        resetArmPose.setOnClickListener(v -> resetUpperBodyHome());
        root.addView(resetArmPose, matchWrap(0, 4, 0, 8));

        channelEvidenceText = text(channelEvidence(selectedServoChannel), 14, 0xFFFFC0A8);
        channelEvidenceText.setGravity(Gravity.CENTER);
        root.addView(channelEvidenceText, matchWrap(0, 2, 0, 10));

        TextView studioTitle = text("สร้างท่าใหม่ใน Emma", 20, Color.WHITE);
        studioTitle.setGravity(Gravity.CENTER);
        root.addView(studioTitle, matchWrap(0, 14, 0, 4));
        poseDraftText = text("", 15, 0xFFB8C7D9);
        poseDraftText.setGravity(Gravity.CENTER);
        root.addView(poseDraftText, matchWrap(0, 2, 0, 6));
        LinearLayout studioRow = new LinearLayout(this);
        studioRow.setOrientation(LinearLayout.HORIZONTAL);
        Button captureFrame = button("จำคีย์เฟรม", 0xFF73551A);
        Button playDraft = button("เล่นร่างท่า", 0xFF167A58);
        Button clearDraft = button("ล้างร่าง", 0xFF653142);
        captureFrame.setOnClickListener(v -> captureDraftFrame());
        playDraft.setOnClickListener(v -> playDraftMotion());
        clearDraft.setOnClickListener(v -> clearDraftMotion());
        studioRow.addView(captureFrame, weightedWrap());
        studioRow.addView(playDraft, weightedWrap());
        studioRow.addView(clearDraft, weightedWrap());
        root.addView(studioRow, matchWrap(0, 4, 0, 8));
        publishDraftStatus();

        armCommandText = text("ยังไม่ได้ส่งคำสั่งแขน", 15, 0xFFB8C7D9);
        armCommandText.setGravity(Gravity.CENTER);
        root.addView(armCommandText, matchWrap(0, 4, 0, 8));
        TextView armHelp = text(
                "ซ้าย/ขวา = เลือกชุดท่า · Y = เล่นหนึ่งรอบ · B = ส่ง #STOP และล็อก\n" +
                "บอร์ดมี 26 กลุ่ม แต่ยืนยันผลจริงแล้วเฉพาะกลุ่ม 3 ว่ายกแขน; " +
                "ช่อง 1–20 ยังไม่มีผังนิ้วครบ จึงแสดงเลขช่องตามจริง\n" +
                "คืนท่าพักใช้กลุ่ม 99 ตามตัวอย่าง SDK ผู้ขาย และล้างค่าทดลองชั่วคราว\n" +
                "ร่างท่าเก็บใน Emma และไม่เขียนทับกลุ่มเดิมบนบอร์ด",
                15, 0xFFFFC0A8);
        armHelp.setLineSpacing(0, 1.15f);
        root.addView(armHelp, matchWrap(0, 8, 0, 12));

        TextView help = text(
                "วิธีใช้\n1. ปล่อยปุ่มหยุดฉุกเฉินด้านหลังให้เด้งขึ้น\n" +
                "2. จับคู่จอย Xbox กับ Android ของหุ่น\n" +
                "3. กด A หรือแตะเปิดควบคุม แล้วใช้ก้านซ้าย\n\n" +
                "คืนก้านกลาง · จอยหลุด · แอปหลุดโฟกัส = หยุด\n" +
                "ปุ่ม B = หยุดและล็อก · เดินหน้า/ถอยตรวจลิดาร์ 0.15 ม.",
                16, 0xFFB8C7D9);
        help.setLineSpacing(0, 1.15f);
        root.addView(help, matchWrap(0, 16, 0, 0));
        scroll.addView(root);
        return scroll;
    }

    private TextView text(String value, int sp, int colour) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(colour);
        return view;
    }

    private Button button(String value, int colour) {
        Button view = new Button(this);
        view.setText(value);
        view.setTextColor(Color.WHITE);
        view.setTextSize(18);
        view.setPadding(dp(22), dp(16), dp(22), dp(16));
        view.setBackgroundColor(colour);
        return view;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return matchWrap(0, 0, 0, 0);
    }

    private LinearLayout.LayoutParams matchWrap(int l, int t, int r, int b) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        p.setMargins(dp(l), dp(t), dp(r), dp(b));
        return p;
    }

    private LinearLayout.LayoutParams weightedWrap() {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        p.setMargins(dp(3), 0, dp(3), 0);
        return p;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onResume() {
        super.onResume();
        inputManager.registerInputDeviceListener(this, ui);
        scanControllers();
        refreshBaseStatus();
    }

    @Override
    protected void onPause() {
        stopAndDisarm("แอปไม่ได้อยู่ด้านหน้า — หยุดแล้ว");
        inputManager.unregisterInputDeviceListener(this);
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        stopAndDisarm("ปิดแอป — หยุดแล้ว");
        if (upperBody != null) upperBody.disconnect();
        disconnectSdk();
        io.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (!hasFocus) stopAndDisarm("หน้าจอเสียโฟกัส — หยุดแล้ว");
    }

    @Override
    public void onInputDeviceAdded(int deviceId) {
        scanControllers();
    }

    @Override
    public void onInputDeviceChanged(int deviceId) {
        scanControllers();
    }

    @Override
    public void onInputDeviceRemoved(int deviceId) {
        scanControllers();
        stopAndDisarm("จอยหลุด — หยุดแล้ว");
    }

    private void scanControllers() {
        InputDevice found = null;
        for (int id : inputManager.getInputDeviceIds()) {
            InputDevice device = inputManager.getInputDevice(id);
            if (device == null) continue;
            int sources = device.getSources();
            if ((sources & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK ||
                    (sources & InputDevice.SOURCE_GAMEPAD) == InputDevice.SOURCE_GAMEPAD) {
                found = device;
                break;
            }
        }
        controllerConnected = found != null;
        final String label = found == null ? "จอย Xbox: ยังไม่พบ" : "จอย: " + found.getName();
        ui.post(() -> {
            controllerText.setText(label);
            controllerText.setTextColor(controllerConnected ? 0xFF5EE6A8 : 0xFFFFC857);
            updateArmButton();
        });
    }

    private void refreshBaseStatus() {
        io.execute(() -> {
            try {
                showState("กำลังสลับจาก Aobo และเชื่อมฐานล้อ…", 0xFFFFC857);
                stopConflictingVendorApp();
                disconnectSdk();
                ensureSdkConnected();
                JSONObject power = getJson(POWER);
                baseConnected = sdkPlatform != null;
                String model = sdkPlatform.getModelName();
                publishBaseStatus(model, power);
                showState("ยังไม่เปิดควบคุม", 0xFFFFC857);
            } catch (Exception e) {
                baseConnected = false;
                ui.post(() -> {
                    baseText.setText("ฐานล้อ: ติดต่อไม่ได้");
                    baseText.setTextColor(0xFFFF5F73);
                    updateArmButton();
                });
            }
        });
    }

    private void stopConflictingVendorApp() throws Exception {
        Process process = new ProcessBuilder(
                SU_BINARY, "0", "sh", "-c", "am force-stop " + VENDOR_PACKAGE)
                .redirectErrorStream(true)
                .start();
        try (InputStream ignored = process.getInputStream()) {
            byte[] buffer = new byte[256];
            while (ignored.read(buffer) != -1) {
                // Drain the fixed system command output so waitFor cannot block.
            }
        }
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IllegalStateException("ปิด Aobo ไม่สำเร็จ (exit " + exitCode + ")");
        }
        Log.i(TAG, "vendor app force-stopped before exclusive SDK connection");
    }

    private void publishBaseStatus(String model, JSONObject power) {
        int battery = power == null ? -1 : power.optInt("batteryPercentage", -1);
        String docking = power == null ? "" : power.optString("dockingStatus", "");
        boolean charging = power != null && power.optBoolean("isCharging", false);
        baseDocked = "on_dock".equalsIgnoreCase(docking);
        String powerState = baseDocked
                ? " · อยู่บนแท่นชาร์จ"
                : (charging ? " · กำลังชาร์จ" : "");
        String label = "ฐานล้อ: " + model
                + (battery >= 0 ? " · แบต " + battery + "%" : "")
                + powerState;
        ui.post(() -> {
            baseText.setText(label);
            baseText.setTextColor(baseConnected ? 0xFF5EE6A8 : 0xFFFF5F73);
            updateArmButton();
        });
    }

    private void updateArmButton() {
        armButton.setEnabled(controllerConnected && baseConnected);
        armButton.setAlpha(armButton.isEnabled() ? 1f : 0.45f);
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int key = event.getKeyCode();
        boolean down = event.getAction() == KeyEvent.ACTION_DOWN;
        if (key == KeyEvent.KEYCODE_BUTTON_A && down && event.getRepeatCount() == 0) {
            if (!screenArmed && controllerConnected && baseConnected) armControls();
            return true;
        }
        if (key == KeyEvent.KEYCODE_BUTTON_B && down && event.getRepeatCount() == 0) {
            stopAndDisarm("ปุ่ม B — หยุดและล็อกแล้ว");
            return true;
        }
        if (key == KeyEvent.KEYCODE_BUTTON_X && down && event.getRepeatCount() == 0) {
            connectUpperBody();
            return true;
        }
        if (key == KeyEvent.KEYCODE_DPAD_LEFT && down && event.getRepeatCount() == 0) {
            selectArmGroup(-1);
            return true;
        }
        if (key == KeyEvent.KEYCODE_DPAD_RIGHT && down && event.getRepeatCount() == 0) {
            selectArmGroup(1);
            return true;
        }
        if (key == KeyEvent.KEYCODE_BUTTON_Y && down && event.getRepeatCount() == 0) {
            playSelectedArmGroup();
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    private void connectUpperBody() {
        if (upperBodyArmed && upperBody.isConnected()) {
            updateUpperBody("แขน: พร้อม · เลือกชุดท่าแล้วกด Y", 0xFF5EE6A8);
            return;
        }
        updateUpperBody("แขน: กำลังเตรียม CH340…", 0xFFFFC857);
        io.execute(() -> {
            try {
                stopConflictingVendorApp();
                upperBody.connect();
                upperBodyArmed = true;
                updateUpperBody("แขน: พร้อม · เลือกชุดท่าแล้วกด Y", 0xFF5EE6A8);
            } catch (Exception e) {
                Log.e(TAG, "arm connection failed", e);
                upperBodyArmed = false;
                updateUpperBody("แขน: เชื่อมไม่สำเร็จ — " + e.getMessage(), 0xFFFF5F73);
            }
        });
    }

    private void selectArmGroup(int delta) {
        int next = selectedArmGroup + delta;
        if (next < ARM_GROUP_MIN) next = ARM_GROUP_MAX;
        if (next > ARM_GROUP_MAX) next = ARM_GROUP_MIN;
        selectedArmGroup = next;
        String evidence = next == 3 ? " · เคยยืนยันว่าทำให้แขนยก" : " · ยังไม่ระบุชื่อ";
        final String label = "ชุดท่า " + next + evidence;
        ui.post(() -> armGroupText.setText(label));
    }

    private void playSelectedArmGroup() {
        if (!upperBodyArmed || !upperBody.isConnected()) {
            updateArmCommand("กด X เพื่อเปิดแขนก่อน", 0xFFFF5F73);
            return;
        }
        final int group = selectedArmGroup;
        io.execute(() -> {
            try {
                upperBody.playGroup(group);
                updateArmCommand("สั่งชุดท่า " + group + " หนึ่งรอบ", 0xFF80DFFF);
            } catch (Exception e) {
                upperBodyArmed = false;
                updateArmCommand("ส่งชุดท่าไม่สำเร็จ: " + e.getMessage(), 0xFFFF5F73);
            }
        });
    }

    private void selectServoChannel(int delta) {
        int next = selectedServoChannel + delta;
        if (next < SERVO_CHANNEL_MIN) next = SERVO_CHANNEL_MAX;
        if (next > SERVO_CHANNEL_MAX) next = SERVO_CHANNEL_MIN;
        selectedServoChannel = next;
        selectedServoPulse = motionDraft.targetFor(next, 1500);
        publishServoSelection();
    }

    private void changeServoPulse(int delta) {
        selectedServoPulse = Math.max(500, Math.min(2500, selectedServoPulse + delta));
        publishServoSelection();
    }

    private void publishServoSelection() {
        final String label = "ช่อง " + selectedServoChannel + " · ค่าที่จะสั่ง "
                + selectedServoPulse + " (ไม่ใช่ตำแหน่งที่วัดได้)";
        final String evidence = channelEvidence(selectedServoChannel);
        ui.post(() -> {
            servoText.setText(label);
            if (channelEvidenceText != null) channelEvidenceText.setText(evidence);
        });
    }

    private String channelEvidence(int channel) {
        if (channel == 5) {
            return "หลักฐานช่อง 5: เคยเห็นแขนขยับ แต่ยังไม่ยืนยันด้านหรือข้อต่อ";
        }
        if (channel == 7) return "หลักฐานช่อง 7: ภาพทดสอบยืนยันการหันหัว";
        if (channel == 8) return "หลักฐานช่อง 8: ภาพทดสอบยืนยันการก้มเงยหัว";
        if (channel == 1 || channel == 2 || channel == 3 || channel == 4 || channel == 11) {
            return "ช่อง " + channel + ": เคยทดสอบแล้ว แต่ภาพเดิมยังไม่เห็นผลชัด";
        }
        return "ช่อง " + channel + ": ยังไม่ได้ทดสอบและจับคู่กับส่วนจริง";
    }

    private void sendSelectedServo() {
        if (!upperBodyArmed || !upperBody.isConnected()) {
            updateArmCommand("กด X เพื่อเปิดแขนก่อน", 0xFFFF5F73);
            return;
        }
        final int channel = selectedServoChannel;
        final int pulse = selectedServoPulse;
        io.execute(() -> {
            try {
                upperBody.setServo(channel, pulse, 3000);
                motionDraft.rememberTarget(channel, pulse);
                persistDraft();
                publishDraftStatus();
                updateArmCommand("สั่งช่อง " + channel + " = " + pulse + " / 3000 ms", 0xFF80DFFF);
            } catch (Exception e) {
                upperBodyArmed = false;
                updateArmCommand("ส่งช่องไม่สำเร็จ: " + e.getMessage(), 0xFFFF5F73);
            }
        });
    }

    private void resetUpperBodyHome() {
        if (!upperBodyArmed || !upperBody.isConnected()) {
            updateArmCommand("กด X เพื่อเปิดแขนก่อน", 0xFFFF5F73);
            return;
        }
        io.execute(() -> {
            try {
                upperBody.resetHome();
                motionDraft.clearCommandedTargets();
                selectedServoPulse = 1500;
                persistDraft();
                publishServoSelection();
                publishDraftStatus();
                updateArmCommand("ส่งกลุ่ม 99 ให้กลับท่าพักเดิมแล้ว", 0xFF5EE6A8);
            } catch (Exception e) {
                upperBodyArmed = false;
                updateArmCommand("คืนท่าพักไม่สำเร็จ: " + e.getMessage(), 0xFFFF5F73);
            }
        });
    }

    private void captureDraftFrame() {
        try {
            motionDraft.captureFrame();
            persistDraft();
            publishDraftStatus();
            updateArmCommand("จำคีย์เฟรม " + motionDraft.frameCount() + " แล้ว", 0xFF5EE6A8);
        } catch (Exception e) {
            updateArmCommand(e.getMessage(), 0xFFFF5F73);
        }
    }

    private void playDraftMotion() {
        if (!upperBodyArmed || !upperBody.isConnected()) {
            updateArmCommand("กด X เพื่อเปิดแขนก่อน", 0xFFFF5F73);
            return;
        }
        try {
            upperBody.playFrames(motionDraft.frames());
            updateArmCommand("เริ่มเล่นร่างท่า " + motionDraft.frameCount() + " คีย์เฟรม", 0xFF80DFFF);
        } catch (Exception e) {
            updateArmCommand("เล่นร่างท่าไม่ได้: " + e.getMessage(), 0xFFFF5F73);
        }
    }

    private void clearDraftMotion() {
        if (upperBody != null) upperBody.stopAll();
        motionDraft.clear();
        selectedServoPulse = 1500;
        persistDraft();
        publishServoSelection();
        publishDraftStatus();
        updateArmCommand("ล้างร่างท่าแล้ว", 0xFFFFC857);
    }

    private void persistDraft() {
        getSharedPreferences(STUDIO_PREFS, MODE_PRIVATE).edit()
                .putString(STUDIO_DRAFT, motionDraft.toJson()).apply();
    }

    private void publishDraftStatus() {
        final String label = "ร่างท่า: " + motionDraft.frameCount() + " คีย์เฟรม · "
                + motionDraft.targetCount() + " ช่องที่เคยสั่ง";
        ui.post(() -> {
            if (poseDraftText != null) poseDraftText.setText(label);
        });
    }

    private void updateUpperBody(String value, int colour) {
        ui.post(() -> {
            if (upperBodyText != null) {
                upperBodyText.setText(value);
                upperBodyText.setTextColor(colour);
            }
            if (upperBodyButton != null) {
                upperBodyButton.setText(upperBodyArmed ? "แขนพร้อม (X)" : "เชื่อมและเปิดแขน (X)");
            }
        });
    }

    private void updateArmCommand(String value, int colour) {
        ui.post(() -> {
            if (armCommandText != null) {
                armCommandText.setText(value);
                armCommandText.setTextColor(colour);
            }
        });
    }

    private void armControls() {
        screenArmed = true;
        ui.post(() -> {
            armButton.setText("ปิดควบคุมฐานล้อ");
            armButton.setBackgroundColor(0xFF73551A);
        });
        showState("กำลังตรวจฐานล้อ…", 0xFFFFC857);
        io.execute(() -> {
            try {
                ensureSdkConnected();
                leaveDockIfNeeded();
                showState("พร้อม · ใช้ก้านซ้ายได้เลย", 0xFF5EE6A8);
                startPumpIfReady();
            } catch (Exception e) {
                Log.e(TAG, "navigation SDK preparation failed", e);
                String detail = e.getMessage();
                stopFromWorker(detail == null || detail.trim().isEmpty()
                        ? "เตรียมฐานล้อไม่สำเร็จ — หยุดและล็อก"
                        : "เตรียมฐานล้อไม่สำเร็จ: " + detail + " — หยุดและล็อก");
            }
        });
    }

    private void ensureSdkConnected() {
        if (sdkPlatform != null) return;
        sdkPlatform = SlamwareCorePlatform.connect(SDK_HOST, SDK_PORT);
        Log.i(TAG, "Navigation SDK connected on port " + SDK_PORT);
    }

    private void leaveDockIfNeeded() throws Exception {
        JSONObject before = getRequiredJson(POWER, 3);
        baseDocked = "on_dock".equalsIgnoreCase(before.optString("dockingStatus", ""));
        if (!baseDocked) return;
        double requiredClearance = UNDOCK_METRES + UNDOCK_STOP_MARGIN_METRES;
        double frontClearance = nearestClearance(DIR_FORWARD);
        if (!Double.isFinite(frontClearance) || frontClearance < requiredClearance) {
            throw new IllegalStateException(String.format(Locale.US,
                    "ด้านหน้าว่าง %.2f ม. ต้องการอย่างน้อย %.2f ม.",
                    frontClearance, requiredClearance));
        }

        showState("กำลังขยับออกจากแท่น 15 ซม.…", 0xFFFFC857);
        Pose beforePose = sdkPlatform.getPose();
        if (beforePose == null) {
            throw new IllegalStateException("อ่านตำแหน่งฐานล้อไม่ได้");
        }
        long deadline = SystemClock.elapsedRealtime() + UNDOCK_TIMEOUT_MS;
        float moved = 0f;
        boolean releaseDistanceReached = false;
        int pulseCount = 0;
        while (screenArmed && SystemClock.elapsedRealtime() < deadline) {
            IMoveAction action = sdkPlatform.moveBy(MoveDirection.FORWARD);
            if (action == null || action.isEmpty()) {
                throw new IllegalStateException("ฐานล้อไม่สร้างงานออกจากแท่น");
            }
            sdkMove = action;
            pulseCount++;
            ActionStatus status = action.getStatus();
            if (pulseCount == 1 || pulseCount % 5 == 0) {
                Log.i(TAG, String.format(Locale.US,
                        "remote undock pulse=%d action=%d status=%s from=(%.3f,%.3f,%.3f) clearance=%.3f",
                        pulseCount, action.getActionId(), status, beforePose.getX(), beforePose.getY(),
                        beforePose.getYaw(), frontClearance));
            }
            SystemClock.sleep(120L);
            Pose currentPose = sdkPlatform.getPose();
            moved = currentPose == null ? 0f
                    : beforePose.getLocation().distanceTo(currentPose.getLocation());
            if (moved >= UNDOCK_METRES) {
                releaseDistanceReached = true;
                cancelSdkMove();
                Log.i(TAG, String.format(Locale.US,
                        "remote undock cancelled at measured distance %.3fm after %d pulses",
                        moved, pulseCount));
                break;
            }
            if (status == ActionStatus.ERROR) {
                throw new IllegalStateException("ออกจากแท่นไม่สำเร็จ: " + status
                        + " " + action.getReason());
            }
        }
        if (!screenArmed) {
            cancelSdkMove();
            throw new IllegalStateException("ผู้ใช้ยกเลิกการออกจากแท่น");
        }
        if (!releaseDistanceReached) {
            Pose timeoutPose = sdkPlatform.getPose();
            float timeoutMoved = timeoutPose == null ? 0f
                    : beforePose.getLocation().distanceTo(timeoutPose.getLocation());
            Log.w(TAG, String.format(Locale.US,
                    "remote undock timeout pulses=%d moved=%.3fm", pulseCount, timeoutMoved));
            cancelSdkMove();
            throw new IllegalStateException(String.format(Locale.US,
                    "ออกจากแท่นเกิน 6 วินาที (ขยับ %.2f ม.)", timeoutMoved));
        }
        sdkMove = null;
        Pose afterPose = sdkPlatform.getPose();
        moved = afterPose == null ? moved
                : beforePose.getLocation().distanceTo(afterPose.getLocation());
        long statusDeadline = SystemClock.elapsedRealtime() + 2000L;
        JSONObject verifiedPower;
        do {
            SystemClock.sleep(200L);
            verifiedPower = getRequiredJson(POWER, 3);
            baseDocked = "on_dock".equalsIgnoreCase(
                    verifiedPower.optString("dockingStatus", ""));
        } while (baseDocked && SystemClock.elapsedRealtime() < statusDeadline);
        Log.i(TAG, String.format(Locale.US,
                "undock verification moved=%.3fm dockingStatus=%s", moved,
                baseDocked ? "on_dock" : "off_dock"));
        if (moved < 0.08f) {
            throw new IllegalStateException(String.format(Locale.US,
                    "ฐานล้อขยับเพียง %.2f ม.", moved));
        }
        if (baseDocked) throw new IllegalStateException("ฐานล้อยังรายงานว่าอยู่บนแท่น");
        publishBaseStatus(sdkPlatform.getModelName(), verifiedPower);
        Log.i(TAG, "bounded SDK remote undock finished at " + moved + "m");
    }

    @Override
    public boolean dispatchGenericMotionEvent(MotionEvent event) {
        int source = event.getSource();
        boolean joystick = (source & InputDevice.SOURCE_JOYSTICK) == InputDevice.SOURCE_JOYSTICK;
        if (joystick && event.getAction() == MotionEvent.ACTION_MOVE) {
            InputDevice device = event.getDevice();
            lastX = centredAxis(event, device, MotionEvent.AXIS_X);
            lastY = centredAxis(event, device, MotionEvent.AXIS_Y);
            int next = directionFromAxes(lastX, lastY);
            if (next != direction) {
                generation.incrementAndGet();
                direction = next;
                Log.i(TAG, "gamepad direction=" + directionLabel(next));
                if (next == DIR_NONE) stopMotion("ก้านอยู่กลาง — หยุด");
                else startPumpIfReady();
            }
            final float x = lastX, y = lastY;
            ui.post(() -> axesText.setText(String.format(Locale.US,
                    "ก้านซ้าย  X %.2f  Y %.2f", x, y)));
            return true;
        }
        return super.dispatchGenericMotionEvent(event);
    }

    private float centredAxis(MotionEvent event, InputDevice device, int axis) {
        if (device == null) return 0f;
        InputDevice.MotionRange range = device.getMotionRange(axis, event.getSource());
        float value = event.getAxisValue(axis);
        float flat = range == null ? 0f : range.getFlat();
        return Math.abs(value) > Math.max(flat, STICK_DEAD_ZONE) ? value : 0f;
    }

    private int directionFromAxes(float x, float y) {
        if (Math.abs(x) < STICK_DEAD_ZONE && Math.abs(y) < STICK_DEAD_ZONE) return DIR_NONE;
        if (Math.abs(x) >= Math.abs(y)) return x > 0 ? DIR_RIGHT : DIR_LEFT;
        return y > 0 ? DIR_BACKWARD : DIR_FORWARD;
    }

    private void startPumpIfReady() {
        if (!screenArmed || !controllerConnected || !baseConnected ||
                direction == DIR_NONE || pumping.get()) return;
        final int run = generation.incrementAndGet();
        if (!pumping.compareAndSet(false, true)) return;
        io.execute(() -> driveLoop(run));
    }

    private void driveLoop(int run) {
        try {
            int commandDirection = direction;
            int pulseCount = 0;
            while (run == generation.get() && screenArmed && controllerConnected &&
                    direction == commandDirection) {
                if (!safeToMove(commandDirection)) {
                    stopFromWorker("มีสิ่งกีดขวางหรืออ่านลิดาร์ไม่ได้ — หยุดและล็อก");
                    return;
                }
                if (run != generation.get()) return;
                pulseSdkMove(commandDirection);
                pulseCount++;
                if (pulseCount == 1 || pulseCount % 10 == 0) {
                    Log.i(TAG, "SDK MoveBy pulse=" + pulseCount
                            + " direction=" + directionLabel(commandDirection));
                }
                if (pulseCount == 1) {
                    showState("กำลัง" + directionLabel(commandDirection), 0xFF5EE6A8);
                }
                SystemClock.sleep(120L);
            }
        } catch (Exception e) {
            Log.e(TAG, "Navigation SDK move failed", e);
            stopFromWorker("ติดต่อฐานล้อขัดข้อง — หยุดและล็อก");
        } finally {
            cancelCurrent();
            pumping.set(false);
            if (screenArmed && controllerConnected && direction != DIR_NONE) {
                startPumpIfReady();
            }
        }
    }

    private boolean safeToMove(int commandDirection) {
        if (commandDirection == DIR_LEFT || commandDirection == DIR_RIGHT) return true;
        try {
            return nearestClearance(commandDirection) >= CLEARANCE_METRES;
        } catch (Exception e) {
            return false;
        }
    }

    private double nearestClearance(int commandDirection) throws Exception {
        JSONObject scan = getJson(LASER_SCAN);
        if (scan == null) return Double.NaN;
        JSONArray points = scan.optJSONArray("laser_points");
        if (points == null || points.length() == 0) return Double.NaN;
        double nearest = Double.POSITIVE_INFINITY;
        for (int i = 0; i < points.length(); i++) {
            JSONObject p = points.optJSONObject(i);
            if (p == null || !p.optBoolean("valid", false)) continue;
            double angle = normaliseAngle(p.optDouble("angle", Double.NaN));
            double distance = p.optDouble("distance", Double.NaN);
            if (!Double.isFinite(angle) || !Double.isFinite(distance) || distance <= 0) continue;
            boolean inArc = commandDirection == DIR_FORWARD
                    ? Math.abs(angle) <= ARC_RADIANS
                    : Math.abs(Math.abs(angle) - Math.PI) <= ARC_RADIANS;
            if (inArc) nearest = Math.min(nearest, distance);
        }
        return Double.isFinite(nearest) ? nearest : Double.NaN;
    }

    private JSONObject getRequiredJson(String path, int attempts) throws Exception {
        for (int attempt = 1; attempt <= attempts; attempt++) {
            JSONObject value = getJson(path);
            if (value != null) return value;
            if (attempt < attempts) SystemClock.sleep(100L);
        }
        throw new IllegalStateException("อ่านสถานะฐานล้อไม่ได้");
    }

    private double normaliseAngle(double angle) {
        while (angle > Math.PI) angle -= Math.PI * 2.0;
        while (angle < -Math.PI) angle += Math.PI * 2.0;
        return angle;
    }

    private void pulseSdkMove(int commandDirection) {
        MoveDirection sdkDirection;
        switch (commandDirection) {
            case DIR_FORWARD: sdkDirection = MoveDirection.FORWARD; break;
            case DIR_BACKWARD: sdkDirection = MoveDirection.BACKWARD; break;
            case DIR_RIGHT: sdkDirection = MoveDirection.TURN_RIGHT; break;
            case DIR_LEFT: sdkDirection = MoveDirection.TURN_LEFT; break;
            default: throw new IllegalArgumentException("direction " + commandDirection);
        }
        sdkMove = sdkPlatform.moveBy(sdkDirection);
        if (sdkMove == null || sdkMove.isEmpty()) {
            throw new IllegalStateException("Navigation SDK returned an empty MoveBy action");
        }
    }

    private void stopMotion(String reason) {
        generation.incrementAndGet();
        direction = DIR_NONE;
        showState(reason, 0xFFFFC857);
        io.execute(this::cancelCurrent);
    }

    private void stopAndDisarm(String reason) {
        screenArmed = false;
        upperBodyArmed = false;
        generation.incrementAndGet();
        direction = DIR_NONE;
        if (upperBody != null) upperBody.stopAll();
        ui.post(() -> {
            if (armButton != null) {
                armButton.setText("เปิดควบคุมฐานล้อ");
                armButton.setBackgroundColor(0xFF167A58);
            }
            if (upperBodyButton != null) upperBodyButton.setText("เชื่อมและเปิดแขน (X)");
            if (upperBodyText != null && upperBody != null && upperBody.isConnected()) {
                upperBodyText.setText("แขน: เชื่อมแล้ว · ล็อกอยู่ กด X เพื่อเปิด");
                upperBodyText.setTextColor(0xFFFFC857);
            }
        });
        showState(reason, 0xFFFFC857);
        if (!io.isShutdown()) io.execute(() -> {
            cancelCurrent();
        });
    }

    private void stopFromWorker(String reason) {
        screenArmed = false;
        generation.incrementAndGet();
        direction = DIR_NONE;
        showState(reason, 0xFFFF5F73);
        ui.post(() -> {
            armButton.setText("เปิดควบคุมฐานล้อ");
            armButton.setBackgroundColor(0xFF167A58);
        });
    }

    private void cancelCurrent() {
        cancelSdkMove();
        try {
            request("DELETE", CURRENT_ACTION, null);
        } catch (Exception ignored) {
            // SDK cancellation above is the primary stop path. REST cancellation
            // remains as a second stop request for actions created elsewhere.
        }
    }

    private void cancelSdkMove() {
        IMoveAction move = sdkMove;
        sdkMove = null;
        if (move == null) return;
        try {
            move.cancel();
        } catch (Exception e) {
            Log.w(TAG, "Navigation SDK action cancel failed", e);
        }
    }

    private void disconnectSdk() {
        SlamwareCorePlatform platform = sdkPlatform;
        sdkPlatform = null;
        if (platform == null) return;
        try {
            platform.disconnect();
        } catch (Exception e) {
            Log.w(TAG, "Navigation SDK disconnect failed", e);
        }
    }

    private JSONObject getJson(String path) throws Exception {
        String body = request("GET", path, null);
        return body == null || body.isEmpty() ? null : new JSONObject(body);
    }

    private String request(String method, String path, String body) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(BASE_URL + path).openConnection();
        connection.setConnectTimeout(HTTP_TIMEOUT_MS);
        connection.setReadTimeout(HTTP_TIMEOUT_MS);
        connection.setRequestMethod(method);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Connection", "close");
        if (body != null) {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setFixedLengthStreamingMode(bytes.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(bytes);
            }
        }
        int code = connection.getResponseCode();
        InputStream stream = code >= 200 && code < 300
                ? connection.getInputStream() : connection.getErrorStream();
        StringBuilder result = new StringBuilder();
        if (stream != null) {
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) result.append(line);
            }
        }
        connection.disconnect();
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("HTTP " + code + " " + result);
        }
        return result.toString();
    }

    private String directionLabel(int value) {
        switch (value) {
            case DIR_FORWARD: return "เดินหน้า";
            case DIR_BACKWARD: return "ถอยหลัง";
            case DIR_LEFT: return "หมุนซ้าย";
            case DIR_RIGHT: return "หมุนขวา";
            default: return "หยุด";
        }
    }

    private void showState(String value, int colour) {
        ui.post(() -> {
            if (stateText != null) {
                stateText.setText(value);
                stateText.setTextColor(colour);
            }
        });
    }
}
