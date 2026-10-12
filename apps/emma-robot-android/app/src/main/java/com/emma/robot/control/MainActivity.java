package com.emma.robot.control;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.hardware.input.InputManager;
import android.os.Build;
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
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.graphics.Typeface;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
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
 *
 * It is also the native end of Emma's body and navigation
 * (docs/emma-body-nav-design-2026-10-08.md): it implements the face's
 * {@link EmmaFace.Bridge}, owns the {@link BodyController} (one lock on the
 * arm) and the {@link NavLauncher} (one leg of a walk through su), and the
 * native red SafetyBar -- the only STOP on screen, so it still works when the
 * page, the WebView or the server is dead. Every stop path stops BOTH.
 */
public final class MainActivity extends Activity
        implements InputManager.InputDeviceListener, EmmaFace.Bridge {
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
    /** Bumped by every stopAndDisarm. An operator arm command checks
     *  `upperBodyArmed` on the UI thread and then waits its turn on `io` --
     *  behind a stick drive, a base-status poll (up to ~26 s) or the ~5 s arm
     *  connect. Review 12 Oct: B pressed in that gap sent #STOP, and then the
     *  queued group ran anyway. A command runs only if no stop came after it
     *  was queued. (Not `generation`: a stick direction change bumps that.) */
    private final AtomicInteger armStops = new AtomicInteger(0);
    // The greeting's rest-return (group 99 on #AGF + hold, a fallback timer,
    // voided by any stop or operator group) moved into BodyController, which
    // now does the same for every arm gesture: one lock, one wave counter.
    private static final String FACE_TOKEN = "face_token";

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
    /** The arm board's serial link was up (onArmConnected) -- so a disconnect
     *  is a real loss, not the SDK tidying up before its first connect. */
    private volatile boolean armLinkUp = false;
    private volatile boolean faceForeground = false;
    private volatile int selectedArmGroup = 3;
    private volatile int selectedServoChannel = 5;
    private volatile int selectedServoPulse = 1500;
    private MotionDraft motionDraft;
    private ArmController upperBody;
    private EmmaFace emmaFace;
    private BodyController body;
    private NavLauncher nav;
    /** The walk back to the dock after idle (owner 10 Oct, default off). */
    private AutoReturn autoReturn;
    /** The Emma server on the robot itself (scripts/robot_server.py): started
     *  and watched from here, never stopped -- it serves the face. */
    private ServerLauncher server;
    private TextView serverText;
    /** The mic array's direction features (docs/listening-focus-2026-10-09.md):
     *  runs only while the face is in front and the server asks for it. */
    private ArrayListener array;
    private TextView arrayText;
    private Button arrayButton;
    private volatile boolean windowFocused = false;
    private Button bodyEnabledButton;
    private Button directionKnownButton;
    private Button navEnabledButton;
    private Button armRestButton;
    private Button autoReturnButton;
    private TextView autoIdleText;
    private Button voiceChannelButton;
    private TextView voiceChannelsText;
    private TextView poseTargetText;
    private TextView bodyNavText;
    private TextView navCheckText;
    private EditText faceTokenInput;
    private int poseTargetIndex = 0;
    private static final long CORNER_HOLD_MS = 3000;   // deliberate 3s hold, not a stray long-press
    private boolean cornerHeld;
    private final Runnable cornerRevealRun = () -> {
        if (cornerHeld) { cornerHeld = false; toggleOperator(); }
    };
    private View operatorRoot;
    private volatile boolean operatorVisible = false;
    private boolean startHeld = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        inputManager = (InputManager) getSystemService(INPUT_SERVICE);
        SharedPreferences studio = getSharedPreferences(STUDIO_PREFS, MODE_PRIVATE);
        motionDraft = MotionDraft.fromJson(studio.getString(STUDIO_DRAFT, ""));
        upperBody = new ArmController(this, new ArmController.Listener() {
            @Override public void onArmConnected(String port) {
                // Review2 #24: no `upperBodyArmed = true` here. The SDK posts
                // this to the main looper AFTER its connect thread set the
                // flag ArmController.connect polls, so for a voice gesture it
                // could land after ensureArm's reset and leave the operator's
                // Y/servo/draft keys working without X. Arming happens only in
                // connectUpperBody (the X press).
                armLinkUp = true;
                updateUpperBody("แขน: เชื่อมแล้ว " + port, 0xFF5EE6A8);
            }
            @Override public void onArmDisconnected() {
                upperBodyArmed = false;
                // Review #13: a frame gesture whose board dropped off never
                // finished -- busy, the SafetyBar and every gate stuck until
                // somebody pressed STOP (which then latched body AND nav for
                // 60 s). halt() ends it with no latch.
                boolean wasUp = armLinkUp;
                armLinkUp = false;
                if (wasUp && body != null) body.halt();
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
            @Override public void onArmFinished() {
                // #AGF is the board's own "the action group finished" -- the
                // only measured end-of-gesture signal this side has (11x #CC
                // then #AGF inside 13s for group 6, read on 2026-09-30).
                // ArmController frames it across serial reads (review #30):
                // a per-chunk contains() here missed a split "#A" | "GF".
                if (body != null) body.onArmFinished();
            }
            @Override public void onArmLinkLost(String why) {
                // Review2 #10: a dead CH340 never cleared the SDK's flag, so
                // this used to go unnoticed: gestures "done" with nothing
                // moving, no reconnect until an app restart. Any thread.
                upperBodyArmed = false;
                if (body != null) body.armLost();
                updateUpperBody("แขน: บอร์ดแขนหลุด (" + why + ") — กด X เพื่อเชื่อมใหม่", 0xFFFF5F73);
            }
        });
        body = new BodyController(this, upperBody, io, new BodyController.Host() {
            @Override public boolean foreground() { return faceForeground && windowFocused; }
            @Override public boolean operatorVisible() { return operatorVisible; }
            @Override public String navBlock() { return nav == null ? "nav_unknown" : nav.bodyBlock(); }
            @Override public void ensureArm() throws Exception {
                if (!upperBody.isConnected()) {
                    stopConflictingVendorApp();
                    upperBody.connect();
                }
                // a voice gesture unlocks none of the operator's manual controls
                upperBodyArmed = false;
            }
            @Override public void changed() { bodyOrNavChanged(true); }
        });
        nav = new NavLauncher(this, new NavLauncher.Host() {
            @Override public boolean foreground() { return faceForeground && windowFocused; }
            @Override public boolean operatorVisible() { return operatorVisible; }
            @Override public boolean bodyLatched() { return body.latched(); }
            @Override public boolean bodyBusy() { return body.busy(); }
            @Override public void runArmWork(Runnable work) { io.execute(work); }
            @Override public boolean restArms(long timeoutMs) { return body.restBeforeRun(timeoutMs); }
            @Override public void releaseBase() throws Exception {
                stopConflictingVendorApp();
                disconnectSdk();
            }
            @Override public void deleteBoardAction() {
                try {
                    request("DELETE", CURRENT_ACTION, null);
                } catch (Exception ignored) {
                    // best effort: the controller was already interrupted/killed
                }
            }
            @Override public void changed() { bodyOrNavChanged(false); }
        });
        autoReturn = new AutoReturn(this, nav, ui, new AutoReturn.Host() {
            @Override public boolean operatorVisible() { return operatorVisible; }
            @Override public void changed() { bodyOrNavChanged(false); }
        });
        emmaFace = new EmmaFace(this, this);
        server = new ServerLauncher(this, new ServerLauncher.Host() {
            @Override public String serverUrl() { return emmaFace.serverUrl(); }
            @Override public void changed() { publishServerStatus(); }
        });
        array = new ArrayListener(this, new ArrayListener.Host() {
            @Override public String doaUrl() { return emmaFace.doaSocketUrl(); }
            @Override public String faceToken() {
                return getSharedPreferences(BodyController.PREFS, MODE_PRIVATE).getString(FACE_TOKEN, "");
            }
            @Override public void changed() { publishArrayStatus(); }
        });
        operatorRoot = buildUi();
        FrameLayout stack = new FrameLayout(this);
        stack.addView(emmaFace.view(), new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        stack.addView(operatorRoot, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        operatorRoot.setVisibility(View.GONE);   // customer sees Emma; the operator UI stays hidden
        // No SafetyBar any more (owner, 10 Oct 2026: "ปุ่มแดงๆ ตรงนี้เอาออกได้ไหม"
        // -> "เอาออกทั้งหมด", after being told the other ways to stop). A walk
        // stops on the robot's E-stop button, gamepad B, the operator screen's
        // STOP, a spoken stop (move_body/stop_moving), the app pausing or
        // losing focus, and the main switch.
        setContentView(stack);
        // An app killed with its whole cgroup (SIGKILL, no cancel) can leave a
        // controller or a board action behind: every start reaps an orphan of a
        // dead app launcher (never a PC run).
        nav.reapOnStart();
        // Reveal the operator screen without a gamepad: a DELIBERATE 3-second hold
        // in the top-left corner. A stock long-press fires at ~0.5s, so a customer
        // resting a finger in the corner would open the chassis controls; the hold
        // is tracked in dispatchTouchEvent (observes, never consumes the taps the
        // Emma page needs) and cancelled the moment the finger moves out or lifts.
        showState("ยังไม่เปิดควบคุม", 0xFFFFC857);
        emmaFace.start(getIntent());
        autoReturn.start();
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
        // one line: running / starting / not installed / failed + the last error
        serverText = text(server == null ? "" : server.statusLine(), 17, 0xFFB8C7D9);
        root.addView(serverText, matchWrap(0, 4, 0, 10));
        // the 8-channel direction capture: its state, and the robot-side way
        // out if it turns out to fight the face's own microphone
        arrayText = text("", 15, 0xFFB8C7D9);
        root.addView(arrayText, matchWrap(0, 2, 0, 4));
        arrayButton = button("", 0xFF31465E);
        arrayButton.setOnClickListener(v -> array.setEnabled(!array.enabled()));
        root.addView(arrayButton, matchWrap(0, 2, 0, 10));
        publishArrayStatus();
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
        stop.setOnClickListener(v -> {
            // review #21: a live walk's DELETE comes from NavLauncher.escalate,
            // last, after the controller is stopped -- not from here at once
            boolean navLive = stopBodyAndNav(null, "stop_pressed", true);
            stopAndDisarm("หยุดและล็อกแล้ว", true, !navLive);
        });
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

        buildBodyNavControls(root);

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

    /** Body or nav state changed: tell the page. (This used to raise or drop
     *  the SafetyBar too; the bar was removed on the owner's call, 10 Oct.) */
    private void publishBodyNavStatusSoon() {
        ui.post(this::publishBodyNavStatus);
    }

    private void bodyOrNavChanged(boolean bodyChanged) {
        if (emmaFace != null) {
            if (bodyChanged) emmaFace.bodyChanged();
            else emmaFace.navChanged();
        }
        publishBodyNavStatusSoon();
    }

    /**
     * Every stop path calls BOTH -- the SafetyBar, gamepad B, the operator
     * STOP, the bridge stops, the app pausing, focus loss, opening the operator
     * screen and the app closing. Nav first: its cancel token is what keeps a launch that
     * is still behind the arm rest from ever reaching su; the body stop then
     * wakes that rest wait. Both set their latch on this thread.
     *
     * Returns whether a walk (this app's, or a PC run the poller sees) was
     * live when the stop was pressed: NavLauncher.escalate then owns the board
     * DELETE, in order (review #21).
     */
    private boolean stopBodyAndNav(String bodyReason, String navReason, boolean explicit) {
        boolean navWasActive = nav != null && nav.stop(navReason, explicit);
        if (body != null) body.stop(bodyReason);
        return navWasActive;
    }

    // ---- EmmaFace.Bridge (UI thread, cached state only) ----------------------

    @Override public void armGreeting() {
        requestArmGreeting();
    }

    @Override public String bodyList() {
        return body.list();
    }

    @Override public String bodyStatus() {
        return body.status();
    }

    @Override public String bodyStop() {
        stopBodyAndNav(null, "stop_pressed", true);
        return "{\"accepted\":true}";
    }

    @Override public String bodyGesture(String id) {
        return body.request(id);
    }

    @Override public String navPlaces() {
        return nav.places();
    }

    @Override public String navStatus() {
        return nav.status();
    }

    @Override public String navGo(String placeId, String origin) {
        // tap/voice only: "auto" is AutoReturn's, after its warning was on
        // screen. EmmaFace's NAV_GO shape already says so; this is the
        // second lock on the same door.
        if (!NavRules.pageOrigin(origin)) return "{\"accepted\":false,\"reason\":\"bad_request\",\"run_id\":null}";
        return nav.go(placeId, origin);
    }

    /** emma-nav:auto -- the auto-return state for the face's banner. */
    @Override public String navAuto() {
        return autoReturn.json();
    }

    /** emma-nav:auto:shown -- the warning banner is on screen now. */
    @Override public String navAutoShown() {
        autoReturn.shown();
        return autoReturn.json();
    }

    /** emma-nav:auto:stay -- "อยู่ต่อ" / "รับทราบ". */
    @Override public String navAutoStay() {
        autoReturn.stay();
        return autoReturn.json();
    }

    /** emma-nav:activity:* -- a call started or ended, somebody spoke in it.
     *  Can only hold a walk back, never start one. */
    @Override public String navActivity(String kind) {
        autoReturn.activity(kind);
        return autoReturn.json();
    }

    @Override public String navStop() {
        stopBodyAndNav(null, "stop_pressed", true);
        return "{\"accepted\":true}";
    }

    /** Review #14: the WebView renderer died and EmmaFace is rebuilding the
     *  page. The page was the conversation that asked for whatever is moving;
     *  nobody can answer for it now, so the walk and the arm stop -- as loss of
     *  control, not as a STOP somebody pressed (that reason would lie in the
     *  turn log, the way review #26 found for the other stop paths). */
    @Override public void pageLost() {
        stopBodyAndNav("not_foreground", "app_lost", false);
    }

    /** Typed on the operator screen; read by the trusted page once on ready
     *  for face_hello. Never in a URL. */
    @Override public String faceToken() {
        try {
            return new JSONObject().put("token",
                    getSharedPreferences(BodyController.PREFS, MODE_PRIVATE).getString(FACE_TOKEN, ""))
                    .toString();
        } catch (Exception e) {
            return "{\"token\":\"\"}";
        }
    }

    // ---- operator screen: voice body + navigation --------------------------------

    private void buildBodyNavControls(LinearLayout root) {
        TextView title = text("ท่าทางด้วยเสียงและการนำทาง (ตั้งค่าบนหุ่น)", 20, Color.WHITE);
        title.setGravity(Gravity.CENTER);
        root.addView(title, matchWrap(0, 18, 0, 4));
        TextView sides = text("ซ้าย/ขวาของท่าทาง = ของตัวหุ่นเอง: แขนซ้ายของหุ่น (ขวามือเราเมื่อยืนหน้าหุ่น)",
                14, 0xFFFFC0A8);
        sides.setGravity(Gravity.CENTER);
        root.addView(sides, matchWrap(0, 2, 0, 6));

        bodyEnabledButton = button("", 0xFF31465E);
        bodyEnabledButton.setOnClickListener(v -> body.setEnabled(!body.enabled()));
        root.addView(bodyEnabledButton, matchWrap(0, 4, 0, 4));
        directionKnownButton = button("", 0xFF31465E);
        directionKnownButton.setOnClickListener(v -> body.setAllowDirectionKnown(!body.allowDirectionKnown()));
        root.addView(directionKnownButton, matchWrap(0, 4, 0, 4));
        navEnabledButton = button("", 0xFF31465E);
        navEnabledButton.setOnClickListener(v -> nav.setEnabled(!nav.enabled()));
        root.addView(navEnabledButton, matchWrap(0, 4, 0, 4));
        armRestButton = button("", 0xFF31465E);
        armRestButton.setOnClickListener(v -> {
            nav.setRequireArmRest(!nav.requireArmRest());
            publishBodyNavStatus();
        });
        root.addView(armRestButton, matchWrap(0, 4, 0, 8));

        // Owner 10 Oct: "ทำงานเสร็จก็ให้เข้าไปชาร์จที่แท่น". Off by default;
        // the idle time steps with ◀ ▶ (no dialog: a dialog takes window
        // focus, and focus loss is a stop path).
        autoReturnButton = button("", 0xFF31465E);
        autoReturnButton.setOnClickListener(v -> autoReturn.setEnabled(!autoReturn.enabled()));
        root.addView(autoReturnButton, matchWrap(0, 4, 0, 4));
        LinearLayout idleRow = new LinearLayout(this);
        idleRow.setOrientation(LinearLayout.HORIZONTAL);
        Button idleDown = button("◀", 0xFF31465E);
        Button idleUp = button("▶", 0xFF31465E);
        autoIdleText = text("", 15, 0xFF80DFFF);
        autoIdleText.setGravity(Gravity.CENTER);
        idleDown.setOnClickListener(v -> autoReturn.stepIdle(-1));
        idleUp.setOnClickListener(v -> autoReturn.stepIdle(1));
        idleRow.addView(idleDown, weightedWrap());
        idleRow.addView(autoIdleText, weightedWrap());
        idleRow.addView(idleUp, weightedWrap());
        root.addView(idleRow, matchWrap(0, 4, 0, 8));

        // The voice channel allow-list is toggled for the channel selected
        // above, so its camera evidence (channelEvidence) is on screen beside it.
        voiceChannelButton = button("", 0xFF73551A);
        voiceChannelButton.setOnClickListener(v -> {
            int channel = selectedServoChannel;
            body.setVoiceChannel(channel, !body.voiceChannels().contains(channel));
            publishServoSelection();
        });
        root.addView(voiceChannelButton, matchWrap(0, 4, 0, 2));
        voiceChannelsText = text("", 14, 0xFFB8C7D9);
        voiceChannelsText.setGravity(Gravity.CENTER);
        root.addView(voiceChannelsText, matchWrap(0, 2, 0, 8));

        // No dialog for picking the id: a dialog takes window focus, and focus
        // loss is a stop path that latches the arm and the walk.
        LinearLayout targetRow = new LinearLayout(this);
        targetRow.setOrientation(LinearLayout.HORIZONTAL);
        Button targetDown = button("◀", 0xFF31465E);
        Button targetUp = button("▶", 0xFF31465E);
        poseTargetText = text("", 15, 0xFF80DFFF);
        poseTargetText.setGravity(Gravity.CENTER);
        targetDown.setOnClickListener(v -> selectPoseTarget(-1));
        targetUp.setOnClickListener(v -> selectPoseTarget(1));
        targetRow.addView(targetDown, weightedWrap());
        targetRow.addView(poseTargetText, weightedWrap());
        targetRow.addView(targetUp, weightedWrap());
        root.addView(targetRow, matchWrap(0, 4, 0, 4));
        Button savePose = button("บันทึกร่างนี้เป็นท่า…", 0xFF167A58);
        savePose.setOnClickListener(v -> saveDraftAsPose());
        root.addView(savePose, matchWrap(0, 4, 0, 8));

        LinearLayout latchRow = new LinearLayout(this);
        latchRow.setOrientation(LinearLayout.HORIZONTAL);
        Button clearBodyLatch = button("ล้างการล็อกท่าทาง", 0xFF653142);
        Button clearNavLatch = button("ล้างการล็อกการเดิน", 0xFF653142);
        clearBodyLatch.setOnClickListener(v -> body.clearLatch());
        clearNavLatch.setOnClickListener(v -> nav.clearLatch());
        latchRow.addView(clearBodyLatch, weightedWrap());
        latchRow.addView(clearNavLatch, weightedWrap());
        root.addView(latchRow, matchWrap(0, 4, 0, 8));

        Button check = button("ตรวจระบบนำทาง (ไม่ขยับ)", 0xFF16677A);
        check.setOnClickListener(v -> {
            navCheckText.setText("กำลังตรวจ…");
            nav.check(() -> ui.post(() -> navCheckText.setText(nav.checkResult())));
        });
        root.addView(check, matchWrap(0, 4, 0, 2));
        navCheckText = text("", 12, 0xFFB8C7D9);
        navCheckText.setTypeface(Typeface.MONOSPACE);
        root.addView(navCheckText, matchWrap(0, 2, 0, 8));

        faceTokenInput = new EditText(this);
        faceTokenInput.setHint("face token (ROBOT_FACE_TOKEN ของเซิร์ฟเวอร์)");
        faceTokenInput.setTextColor(Color.WHITE);
        faceTokenInput.setHintTextColor(0xFF728392);
        faceTokenInput.setSingleLine(true);
        faceTokenInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(faceTokenInput, matchWrap(0, 4, 0, 2));
        Button saveToken = button("บันทึก face token", 0xFF31465E);
        saveToken.setOnClickListener(v -> {
            String token = faceTokenInput.getText().toString().trim();
            getSharedPreferences(BodyController.PREFS, MODE_PRIVATE).edit().putString(FACE_TOKEN, token).apply();
            faceTokenInput.setText("");      // never shown back, like WS_TOKEN
            updateArmCommand(token.isEmpty() ? "ล้าง face token แล้ว" : "บันทึก face token แล้ว", 0xFF5EE6A8);
        });
        root.addView(saveToken, matchWrap(0, 4, 0, 4));

        bodyNavText = text("", 14, 0xFFB8C7D9);
        bodyNavText.setGravity(Gravity.CENTER);
        root.addView(bodyNavText, matchWrap(0, 4, 0, 8));
        publishBodyNavStatus();
    }

    private void publishServerStatus() {
        ui.post(() -> {
            if (serverText == null || server == null) return;
            ServerRules.State state = server.state();
            serverText.setText(server.statusLine());
            serverText.setTextColor(state == ServerRules.State.RUNNING ? 0xFF5EE6A8
                    : state == ServerRules.State.OFF ? 0xFFB8C7D9
                    : state == ServerRules.State.FAILED ? 0xFFFF5F73 : 0xFFFFC857);
        });
    }

    private void publishArrayStatus() {
        ui.post(() -> {
            if (arrayText == null || array == null) return;
            arrayText.setText(array.statusLine());
            arrayText.setTextColor(array.statusColor());
            arrayButton.setText("ฟังทิศเสียงด้วยไมค์ 8 ช่อง: " + onOff(array.enabled()));
        });
    }

    private static String onOff(boolean value) {
        return value ? "เปิด" : "ปิด";
    }

    private void publishBodyNavStatus() {
        ui.post(() -> {
            if (bodyEnabledButton == null || body == null || nav == null || autoReturn == null) return;
            bodyEnabledButton.setText("สั่งท่าทางด้วยเสียง: " + onOff(body.enabled()));
            directionKnownButton.setText("เปิดท่าที่ยังไม่ทดสอบทิศ: " + onOff(body.allowDirectionKnown()));
            navEnabledButton.setText("ฟังก์ชันนำทาง: " + onOff(nav.enabled()));
            // review2 #7: off = the launcher is not told the arms rest, so it
            // refuses every leg through a door (arms_not_rest)
            armRestButton.setText("เก็บแขนก่อนเดิน: " + onOff(nav.requireArmRest())
                    + (nav.requireArmRest() ? "" : " · ไม่เก็บแขน: ต้องเห็นว่าแขนแนบตัวก่อนกดเดิน"));
            autoReturnButton.setText("กลับแท่นเองเมื่อว่าง: " + onOff(autoReturn.enabled()));
            autoIdleText.setText("ว่างนาน " + autoReturn.idleS() + " วินาที แล้วกลับแท่น (เตือนบนจอ "
                    + NavRules.AUTO_WARN_MS / 1000 + " วิก่อนออก)");
            String id = BodyGestures.IDS[poseTargetIndex];
            poseTargetText.setText(id + " · " + body.effectiveStatus(id));
            // review2 #25: su stopped answering -- the app no longer piles up
            // stuck root children; staff are told to reboot instead
            bodyNavText.setText("ท่าทาง: " + (body.latched() ? "ล็อกอยู่" : "ไม่ล็อก")
                    + " · การเดิน: " + (nav.latched() ? "ล็อกอยู่" : "ไม่ล็อก")
                    + (nav.suWedged() ? "\nsu ไม่ตอบสนอง — สถานะการเดินอ่านไม่ได้ ให้รีบูตหุ่น" : "")
                    + "\n" + autoReturn.operatorLine());
            voiceChannelsText.setText("ช่องที่ท่าเสียงใช้ได้: " + body.voiceChannels());
            voiceChannelButton.setText("ช่อง " + selectedServoChannel + " ใช้กับท่าเสียง: "
                    + (body.voiceChannels().contains(selectedServoChannel) ? "อนุญาต" : "ไม่อนุญาต"));
        });
    }

    /** Only ids a pose may be recorded for: needs_mapping or direction_known. */
    private void selectPoseTarget(int delta) {
        int n = BodyGestures.IDS.length;
        int next = poseTargetIndex;
        for (int i = 0; i < n; i++) {
            next = (next + delta + n) % n;
            String status = BodyGestures.builtInStatus(BodyGestures.IDS[next]);
            if (BodyGestures.NEEDS_MAPPING.equals(status) || BodyGestures.DIRECTION_KNOWN.equals(status)) break;
        }
        poseTargetIndex = next;
        publishBodyNavStatus();
    }

    private void saveDraftAsPose() {
        String id = BodyGestures.IDS[poseTargetIndex];
        String refusal = body.savePose(id, motionDraft.frames(), motionDraft.playedSinceEdit());
        if (refusal == null) updateArmCommand("บันทึกร่างเป็นท่า " + id + " แล้ว", 0xFF5EE6A8);
        else updateArmCommand("บันทึกไม่ได้: " + refusal, 0xFFFF5F73);
        publishBodyNavStatus();
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
        hideSystemBars();
        faceForeground = true;
        if (nav != null) nav.resume();     // the poller sees a PC run while we are in front
        // back in front: whoever brought it back is here -- the count restarts
        if (autoReturn != null) autoReturn.activity("resume");
        if (server != null) server.resume();   // start the on-robot server now, then watch it
        inputManager.registerInputDeviceListener(this, ui);
        scanControllers();
        if (operatorVisible) refreshBaseStatus();   // only the operator screen touches the chassis / Aobo
        if (emmaFace != null) emmaFace.onActivityResume();
        if (array != null && !operatorVisible) array.resume();
    }

    @Override
    protected void onPause() {
        faceForeground = false;
        startHeld = false;
        stopBodyAndNav("not_foreground", "not_foreground", false);
        if (nav != null) nav.pause();
        stopAndDisarm("แอปไม่ได้อยู่ด้านหน้า — หยุดแล้ว");
        inputManager.unregisterInputDeviceListener(this);
        if (emmaFace != null) emmaFace.onActivityPause();
        if (array != null) array.pause();      // never a mic behind another screen
        // the watchdog rests; the server itself keeps serving the page
        if (server != null) server.pause();
        super.onPause();
    }

    @Override
    protected void onStop() {
        // EmmaFace swallows the pause its own permission dialog causes (that
        // pause used to close the face scan / end the call that asked). If the
        // app is actually left while the dialog is up, this is where the page
        // finally hears it.
        if (emmaFace != null) emmaFace.onActivityStop();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        if (autoReturn != null) autoReturn.stop();
        stopBodyAndNav("not_foreground", "app_lost", false);
        stopAndDisarm("ปิดแอป — หยุดแล้ว");
        if (body != null) body.shutdown();
        if (nav != null) nav.shutdown();
        if (server != null) server.shutdown();   // the watchdog only: the server outlives the app
        if (array != null) array.shutdown();
        if (emmaFace != null) emmaFace.onActivityDestroy();
        if (upperBody != null) {
            upperBody.disconnect();
            upperBody.release();     // review2 #10: the USB detach receiver
        }
        disconnectSdk();
        io.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        windowFocused = hasFocus;
        if (hasFocus) hideSystemBars();     // a dialog or the shade brought them back
        if (!hasFocus) {
            startHeld = false;   // a missed START key-up must not latch the staff toggle open
            stopBodyAndNav("not_foreground", "not_foreground", false);
            stopAndDisarm("หน้าจอเสียโฟกัส — หยุดแล้ว");
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (emmaFace != null) emmaFace.onNewIntent(intent);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (emmaFace != null) emmaFace.onRequestPermissionsResult(requestCode, results);
    }

    /** Full screen, like every reception robot's face (owner, 10 Oct 2026:
     *  "uxui ของแอพให้ดูดีที่สุด"): the Android status and navigation bars
     *  took the top and bottom of the face, under the brand and the mic.
     *  Staff still reach them -- a swipe from the edge shows them for a few
     *  seconds (transient) -- and nothing here changes focus, so the
     *  focus-loss stop in onWindowFocusChanged keeps its meaning. */
    private void hideSystemBars() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController bars = getWindow().getInsetsController();
            if (bars == null) return;
            bars.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            bars.hide(WindowInsets.Type.systemBars());
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
    }

    /** Default face for customers; the operator UI stays hidden until staff reveal it. */
    private void showEmma() {
        operatorVisible = false;
        stopAndDisarm("กลับหน้า Emma", true);   // explicit: a guaranteed final REST stop
        disconnectSdk();                          // release the SDK: Emma mode is not the wheel owner
        operatorRoot.setVisibility(View.GONE);
        emmaFace.view().setVisibility(View.VISIBLE);
        emmaFace.onActivityResume();              // Emma listens again
        if (array != null) array.resume();
    }

    private void showOperator() {
        operatorVisible = true;
        // Review2 #24: every operator visit starts with the manual arm keys
        // locked until X, whatever a voice gesture's connect left behind.
        upperBodyArmed = false;
        updateUpperBody(upperBody != null && upperBody.isConnected()
                ? "แขน: เชื่อมอยู่ · กด X เพื่อเปิดแขน" : "แขน: ยังไม่เชื่อม", 0xFFFFC857);
        // A walk is stopped BEFORE the operator screen connects the SDK
        // (refreshBaseStatus waits for the stop): two drivers on the wheels.
        stopBodyAndNav("operator_screen", "operator_screen", false);
        emmaFace.onActivityPause();         // release the mic while staff drive -- Emma isn't listening
        if (array != null) array.pause();   // the array too (its last state stays on this screen)
        emmaFace.view().setVisibility(View.GONE);
        operatorRoot.setVisibility(View.VISIBLE);
        refreshBaseStatus();
    }

    private void toggleOperator() {
        if (operatorVisible) showEmma();
        else showOperator();
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
        startHeld = false;   // a controller unplugged mid-hold must not latch the toggle
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
                if (nav != null) nav.awaitStopped(NavLauncher.STOP_DRAIN_MS);
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

    /** Review #32: bounded. This runs on io inside a walk's launch
     *  (releaseBase) and a gesture's ensureArm; an unbounded waitFor on a hung
     *  su/am left an accepted walk in "preparing" for ever. A timeout throws,
     *  which is the existing launcher_failed / arm_unavailable path. */
    private void stopConflictingVendorApp() throws Exception {
        RootShell.Result result = RootShell.run("am force-stop " + VENDOR_PACKAGE, RootShell.TIMEOUT_MS);
        if (result.exit != 0) {
            throw new IllegalStateException("ปิด Aobo ไม่สำเร็จ (exit " + result.exit + ")");
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
    public boolean dispatchTouchEvent(MotionEvent event) {
        trackCornerHold(event);        // observes only, never consumes
        // any finger on the face is somebody being served: the walk back to
        // the dock waits (observes only, like the corner hold)
        if (event.getActionMasked() == MotionEvent.ACTION_DOWN && autoReturn != null) autoReturn.activity("touch");
        return super.dispatchTouchEvent(event);
    }

    /** A 3-second hold in the top-left corner reveals the operator screen. Moving
     *  out of the corner or lifting cancels it, so only a deliberate hold counts. */
    private void trackCornerHold(MotionEvent e) {
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                if (e.getX() < dp(90) && e.getY() < dp(90)) {
                    cornerHeld = true;
                    ui.postDelayed(cornerRevealRun, CORNER_HOLD_MS);
                }
                break;
            case MotionEvent.ACTION_MOVE:
                if (cornerHeld && (e.getX() > dp(120) || e.getY() > dp(120))) {
                    cornerHeld = false;
                    ui.removeCallbacks(cornerRevealRun);
                }
                break;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                cornerHeld = false;
                ui.removeCallbacks(cornerRevealRun);
                break;
            default:
                break;
        }
    }

    private static boolean isGamepadButton(int key) {
        return KeyEvent.isGamepadButton(key)
                || key == KeyEvent.KEYCODE_DPAD_UP || key == KeyEvent.KEYCODE_DPAD_DOWN
                || key == KeyEvent.KEYCODE_DPAD_LEFT || key == KeyEvent.KEYCODE_DPAD_RIGHT
                || key == KeyEvent.KEYCODE_DPAD_CENTER;
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int key = event.getKeyCode();
        boolean down = event.getAction() == KeyEvent.ACTION_DOWN;
        if (down && autoReturn != null) autoReturn.activity("key");   // somebody is at the robot
        // Hidden staff toggle between the Emma face and the operator screen:
        // hold START, then press SELECT. Invisible to a customer bumping the pad.
        if (key == KeyEvent.KEYCODE_BUTTON_START) {
            startHeld = down;
            return true;
        }
        if (key == KeyEvent.KEYCODE_BUTTON_SELECT && down && event.getRepeatCount() == 0) {
            if (startHeld) toggleOperator();
            return true;
        }
        // B always stops and locks, in either mode -- and EXPLICITLY, so the
        // chassis REST cancel fires even in Emma mode where the SDK was never
        // connected here (motion started by the vendor app / RoboStudio still stops).
        if (key == KeyEvent.KEYCODE_BUTTON_B && down && event.getRepeatCount() == 0) {
            // review #21: with a walk live, the board DELETE is NavLauncher's
            // step 4 (after SIGINT and kill); an immediate one here raced the
            // tether stop and drive_route renewed the deleted action
            boolean navLive = stopBodyAndNav(null, "stop_pressed", true);
            stopAndDisarm("ปุ่ม B — หยุดและล็อกแล้ว", true, !navLive);
            return true;
        }
        // Arm/drive keys only while the operator screen is up, so a customer's
        // stray button press can never arm the chassis or move the arm.
        if (operatorVisible) {
            if (key == KeyEvent.KEYCODE_BUTTON_A && down && event.getRepeatCount() == 0) {
                if (!screenArmed && controllerConnected && baseConnected) armControls();
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
        }
        // In customer mode, swallow every other gamepad button so a stray press
        // cannot trigger a framework fallback (e.g. B->BACK) that backgrounds the
        // kiosk or clicks a focused control on the Emma page.
        if (!operatorVisible && isGamepadButton(key)) return true;
        return super.dispatchKeyEvent(event);
    }

    private void connectUpperBody() {
        if (upperBodyArmed && upperBody.isConnected()) {
            updateUpperBody("แขน: พร้อม · เลือกชุดท่าแล้วกด Y", 0xFF5EE6A8);
            return;
        }
        updateUpperBody("แขน: กำลังเตรียม CH340…", 0xFFFFC857);
        final int stopsAtQueue = armStops.get();
        io.execute(() -> {
            try {
                stopConflictingVendorApp();
                upperBody.connect();
                if (armStops.get() != stopsAtQueue) {
                    updateUpperBody("แขน: เชื่อมแล้ว · ล็อกอยู่ กด X เพื่อเปิด", 0xFFFFC857);
                    return;
                }
                upperBodyArmed = true;
                updateUpperBody("แขน: พร้อม · เลือกชุดท่าแล้วกด Y", 0xFF5EE6A8);
            } catch (Exception e) {
                Log.e(TAG, "arm connection failed", e);
                upperBodyArmed = false;
                updateUpperBody("แขน: เชื่อมไม่สำเร็จ — " + e.getMessage(), 0xFFFF5F73);
            }
        });
    }

    /** Called only by the trusted face page for the verified handshake group.
     *  The gates it always had stay here; the arm itself is BodyController's,
     *  so the greeting shares the one lock with voice gestures and the rest
     *  before a walk, and its rest-return is the same one they use. */
    private void requestArmGreeting() {
        if (!faceForeground || operatorVisible || !windowFocused) return;
        body.greet();
    }

    /** The operator's group owns the next #AGF: a scheduled rest-return of an
     *  earlier gesture is void (after a stop NOTHING may move on a timer). */
    private void cancelGreetingHome() {
        if (body != null) body.voidRestReturn();
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
        cancelGreetingHome();   // the operator's group owns the next #AGF
        final int group = selectedArmGroup;
        final int stopsAtQueue = armStops.get();
        io.execute(() -> {
            if (!armCommandStillWanted(stopsAtQueue)) return;
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
        publishBodyNavStatus();
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
        final int stopsAtQueue = armStops.get();
        io.execute(() -> {
            if (!armCommandStillWanted(stopsAtQueue)) return;
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
        final int stopsAtQueue = armStops.get();
        io.execute(() -> {
            if (!armCommandStillWanted(stopsAtQueue)) return;
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
            // §2c rule 5: somebody watched it move -- review2 #22: ALL of it.
            // Only onDone marks it played: a stop (B, the STOP bar, a newer
            // command) ends the run without onDone, and a dead board aborts.
            final int editsAtStart = motionDraft.startPlayback();
            upperBody.playFrames(motionDraft.frames(), null,
                    () -> ui.post(() -> {
                        motionDraft.markPlayed(editsAtStart);
                        updateArmCommand("เล่นร่างท่าจบครบทุกคีย์เฟรม — บันทึกเป็นท่าได้", 0xFF5EE6A8);
                    }),
                    () -> ui.post(() -> {
                        motionDraft.playbackCut();
                        updateArmCommand("ร่างท่าเล่นไม่จบ — บอร์ดแขนหลุด (ต้องเล่นใหม่ให้จบก่อนบันทึก)", 0xFFFF5F73);
                    }));
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
        stopAndDisarm(reason, false);
    }

    private void stopAndDisarm(String reason, boolean explicit) {
        stopAndDisarm(reason, explicit, true);
    }

    /** deleteNow=false when a live walk's stop escalation will DELETE the
     *  board action itself, last (§4, review #21): a DELETE while drive_route
     *  lives reads as a link drop and its VelocitySession renews the action
     *  before the launcher's next 0.2 s tick sees the stop. The SDK move this
     *  app may own is still cancelled at once. */
    /** On `io`, just before an operator arm command touches the arm. */
    private boolean armCommandStillWanted(int stopsAtQueue) {
        if (upperBodyArmed && armStops.get() == stopsAtQueue) return true;
        updateArmCommand("ยกเลิกคำสั่งแขนที่รออยู่ — กดหยุดไปแล้ว", 0xFFFFC857);
        return false;
    }

    private void stopAndDisarm(String reason, boolean explicit, boolean deleteNow) {
        armStops.incrementAndGet();
        screenArmed = false;
        upperBodyArmed = false;
        generation.incrementAndGet();
        direction = DIR_NONE;
        // the arm stops and nothing scheduled may move it later; the latch is
        // stopBodyAndNav's, on the paths that are a body stop
        if (body != null) body.halt();
        else if (upperBody != null) upperBody.stopAll();
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
        if (!io.isShutdown()) {
            io.execute(() -> {
                if (deleteNow) cancelCurrent(explicit);
                else cancelSdkMove();
            });
        }
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
        cancelCurrent(false);
    }

    private void cancelCurrent(boolean explicit) {
        cancelSdkMove();
        // A DELETE to the chassis's current-action endpoint stops motion started
        // ANYWHERE -- this app's SDK path, the vendor app, or RoboStudio. On an
        // EXPLICIT stop (the B button, the STOP button) always send it, so "B
        // stops in every mode" holds even in Emma mode where this app never
        // connected the SDK. On an AUTOMATIC stop (focus loss, stick centre) only
        // send it when this app is the wheel owner (sdkPlatform set), so a routine
        // focus change in Emma mode does not keep poking a chassis it isn't driving.
        if (!explicit && sdkPlatform == null) return;
        try {
            request("DELETE", CURRENT_ACTION, null);
        } catch (Exception ignored) {
            // Best-effort second stop path; the SDK cancel above is primary.
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
