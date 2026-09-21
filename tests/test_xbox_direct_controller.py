from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "tools/android/xbox-direct/app/src/main/java/com/emma/robot/xbox/MainActivity.java"
MANIFEST = ROOT / "tools/android/xbox-direct/app/src/main/AndroidManifest.xml"
ARM_JAVA = ROOT / "tools/android/xbox-direct/app/src/main/java/com/emma/robot/xbox/ArmController.java"
DRAFT_JAVA = ROOT / "tools/android/xbox-direct/app/src/main/java/com/emma/robot/xbox/MotionDraft.java"


def source() -> str:
    return JAVA.read_text(encoding="utf-8")


def test_xbox_controller_runs_directly_on_robot_android():
    text = source()
    assert 'SDK_HOST = "192.168.11.1"' in text
    assert "SDK_PORT = 1445" in text
    for pc_dependency in ("127.0.0.1", ":8001", "WS_TOKEN", "new WebSocket"):
        assert pc_dependency not in text
    assert "InputDevice.SOURCE_JOYSTICK" in text
    assert "InputDevice.SOURCE_GAMEPAD" in text
    assert "dispatchGenericMotionEvent" in text


def test_movement_needs_screen_arm_then_left_stick_only():
    text = source()
    assert "screenArmed = false" in text
    assert "KEYCODE_BUTTON_A" in text
    assert "KEYCODE_BUTTON_L1" not in text
    assert "KEYCODE_BUTTON_R1" not in text
    a_button = text[text.index("KEYCODE_BUTTON_A"):text.index("KEYCODE_BUTTON_B")]
    assert "!screenArmed && controllerConnected && baseConnected" in a_button
    assert "armControls()" in a_button
    ready = text[text.index("private void startPumpIfReady"):text.index("private void driveLoop")]
    assert "!screenArmed" in ready
    assert "direction == DIR_NONE" in ready
    assert "deadmanHeld" not in text
    on_create = text[text.index("protected void onCreate"):text.index("private View buildUi")]
    assert "postMove" not in on_create
    assert "ปล่อยปุ่มหยุดฉุกเฉินด้านหลังให้เด้งขึ้น" in text


def test_every_loss_of_control_stops_and_disarms():
    text = source()
    for callback in ("onPause()", "onDestroy()", "onWindowFocusChanged", "onInputDeviceRemoved"):
        block = text[text.index(callback):]
        assert "stopAndDisarm(" in block[:700]
    assert "KEYCODE_BUTTON_B" in text
    assert 'request("DELETE", CURRENT_ACTION, null)' in text
    assert 'stopMotion("ก้านอยู่กลาง — หยุด")' in text


def test_forward_and_reverse_fail_closed_on_lidar():
    text = source()
    assert "CLEARANCE_METRES = 0.15" in text
    assert "LASER_SCAN" in text
    assert "if (scan == null) return Double.NaN" in text
    assert "points == null || points.length() == 0" in text
    assert "nearestClearance(commandDirection) >= CLEARANCE_METRES" in text
    assert "safeToMove(commandDirection)" in text


def test_live_base_label_exposes_dock_or_charging_state():
    text = source()
    assert 'optString("dockingStatus", "")' in text
    assert 'optBoolean("isCharging", false)' in text
    assert '"on_dock".equalsIgnoreCase(docking)' in text
    assert "อยู่บนแท่นชาร์จ" in text


def test_arming_uses_a_bounded_sdk_undock_before_manual_drive():
    text = source()
    assert "UNDOCK_METRES = 0.15f" in text
    assert "UNDOCK_TIMEOUT_MS = 6000L" in text
    assert "sdkPlatform.getPose()" in text
    assert "sdkPlatform.moveBy(MoveDirection.FORWARD)" in text
    assert "SystemClock.sleep(120L)" in text
    assert "pulseCount % 5" in text
    assert "UNDOCK_STOP_MARGIN_METRES = 0.15" in text
    assert "beforePose.getLocation().distanceTo(afterPose.getLocation())" in text
    assert "if (moved >= UNDOCK_METRES)" in text
    assert "cancelSdkMove()" in text
    assert "ฐานล้อยังรายงานว่าอยู่บนแท่น" in text
    assert "publishBaseStatus(sdkPlatform.getModelName(), verifiedPower)" in text
    arm = text[text.index("private void armControls"):text.index("private void ensureSdkConnected")]
    assert arm.index("leaveDockIfNeeded()") < arm.index("startPumpIfReady()")


def test_arming_prepares_the_navigation_sdk_before_starting_the_pump():
    text = source()
    arm = text[text.index("private void armControls"):text.index("@Override", text.index("private void armControls"))]
    assert arm.index("ensureSdkConnected()") < arm.index("startPumpIfReady()")
    assert "getRequiredJson(POWER, 3)" in text
    assert 'setSystemParameter("base.brake_release", "off")' not in text
    assert 'setSystemParameter("base.emergency_stop", "off")' not in text
    assert "เตรียมฐานล้อไม่สำเร็จ" in arm


def test_resuming_controller_stops_aobo_then_reconnects_exclusively():
    text = source()
    assert 'VENDOR_PACKAGE = "com.aobo.robot.ai3"' in text
    assert 'SU_BINARY = "/system/xbin/su"' in text
    refresh = text[text.index("private void refreshBaseStatus"):text.index("private void publishBaseStatus")]
    assert refresh.index("stopConflictingVendorApp()") < refresh.index("disconnectSdk()")
    assert refresh.index("disconnectSdk()") < refresh.index("ensureSdkConnected()")
    stop = text[text.index("private void stopConflictingVendorApp"):text.index("private void publishBaseStatus")]
    assert '"am force-stop " + VENDOR_PACKAGE' in stop
    assert "process.waitFor()" in stop
    assert "exitCode != 0" in stop


def test_drive_and_stop_use_the_vendor_navigation_sdk():
    text = source()
    assert "SlamwareCorePlatform.connect(SDK_HOST, SDK_PORT)" in text
    assert "sdkPlatform.moveBy(sdkDirection)" in text
    drive = text[text.index("private void driveLoop"):text.index("private boolean safeToMove")]
    assert "while (run == generation.get()" in drive
    assert "safeToMove(commandDirection)" in drive
    assert "pulseSdkMove(commandDirection)" in drive
    assert "SystemClock.sleep(120L)" in drive
    assert "pulseCount % 10" in drive
    assert "MoveDirection.FORWARD" in text
    assert "MoveDirection.BACKWARD" in text
    assert "MoveDirection.TURN_RIGHT" in text
    assert "MoveDirection.TURN_LEFT" in text
    assert "move.cancel()" in text
    assert 'request("POST", ACTIONS' not in text


def test_arm_commands_need_explicit_x_then_y_and_b_locks_both_systems():
    text = source()
    assert "KEYCODE_BUTTON_X" in text
    assert "connectUpperBody()" in text
    assert "KEYCODE_BUTTON_Y" in text
    assert "playSelectedArmGroup()" in text
    assert "upperBodyArmed = false" in text
    stop = text[text.index("private void stopAndDisarm"):text.index("private void stopFromWorker")]
    assert "upperBody.stopAll()" in stop
    assert "แขน: เชื่อมแล้ว · ล็อกอยู่ กด X เพื่อเปิด" in stop
    on_create = text[text.index("protected void onCreate"):text.index("private View buildUi")]
    assert "playGroup(" not in on_create
    assert "setServo(" not in on_create


def test_arm_port_discovery_targets_internal_ch340_and_excludes_debug_hub():
    text = ARM_JAVA.read_text(encoding="utf-8")
    assert 'USB_VENDOR = "1a86"' in text
    assert 'USB_PRODUCT = "7523"' in text
    assert 'CH341_BIND = "/sys/bus/usb/drivers/ch341/bind"' in text
    assert 'nodeName.matches("ttyUSB[0-9]+")' in text
    assert 'runRoot("chmod 666 " + deviceNode)' in text
    assert "8091" not in text
    assert "connectArm(port, BAUD_RATE)" in text


def test_arm_surface_keeps_unknown_groups_and_channels_honest():
    text = source()
    controller = ARM_JAVA.read_text(encoding="utf-8")
    assert "ARM_GROUP_MAX = 26" in text
    assert "SERVO_CHANNEL_MAX = 20" in text
    assert "selectedArmGroup = 3" in text
    assert "เคยยืนยันว่าทำให้แขนยก" in text
    assert "ยังไม่ระบุชื่อ" in text
    assert "ไม่ใช่ตำแหน่งที่วัดได้" in text
    assert "setPosition(pulseWidth, durationMs)" in controller
    assert "executeArmActionGroup(group, 1)" in controller
    for invented_joint in ("ไหล่ซ้าย", "ไหล่ขวา", "นิ้วโป้ง", "นิ้วชี้"):
        assert invented_joint not in text


def test_pose_studio_builds_local_keyframes_without_overwriting_board_groups():
    text = source()
    controller = ARM_JAVA.read_text(encoding="utf-8")
    draft = DRAFT_JAVA.read_text(encoding="utf-8")
    assert "จำคีย์เฟรม" in text
    assert "เล่นร่างท่า" in text
    assert "ล้างร่าง" in text
    assert "MotionDraft.fromJson" in text
    assert "STUDIO_DRAFT" in text
    assert "armManager.f(command + \"\\r\\n\")" in controller
    assert "poseGeneration.incrementAndGet()" in controller
    assert "#DOWN" not in controller
    assert "#CLEAR" not in controller
    assert 'root.put("format", 2)' in draft
    assert 'root.put("commanded_targets", liveTargets)' in draft
    assert "commandedTargets.isEmpty()" in draft


def test_reset_button_uses_vendor_home_group_and_keeps_saved_frames():
    text = source()
    controller = ARM_JAVA.read_text(encoding="utf-8")
    draft = DRAFT_JAVA.read_text(encoding="utf-8")
    assert "↺ คืนท่าพักเดิม (กลุ่ม 99)" in text
    reset_ui = text[text.index("private void resetUpperBodyHome"):text.index("private void captureDraftFrame")]
    assert "upperBody.resetHome()" in reset_ui
    assert "motionDraft.clearCommandedTargets()" in reset_ui
    assert "motionDraft.clear()" not in reset_ui
    reset_controller = controller[controller.index("void resetHome()"):controller.index("void setServo")]
    assert reset_controller.index("poseGeneration.incrementAndGet()") < reset_controller.index(
        "executeArmActionGroup(99, 1)"
    )
    assert "void clearCommandedTargets()" in draft


def test_channel_evidence_labels_only_claim_visually_verified_results():
    text = source()
    assert "หลักฐานช่อง 5: เคยเห็นแขนขยับ แต่ยังไม่ยืนยันด้านหรือข้อต่อ" in text
    assert "หลักฐานช่อง 7: ภาพทดสอบยืนยันการหันหัว" in text
    assert "หลักฐานช่อง 8: ภาพทดสอบยืนยันการก้มเงยหัว" in text
    assert "ยังไม่ได้ทดสอบและจับคู่กับส่วนจริง" in text


def test_manifest_allows_only_the_network_capability_needed_for_chassis():
    text = MANIFEST.read_text(encoding="utf-8")
    assert "android.permission.INTERNET" in text
    assert "android.permission.ACCESS_NETWORK_STATE" in text
    assert "BLUETOOTH_CONNECT" not in text
    assert "RECORD_AUDIO" not in text
    assert "CAMERA" not in text
    assert 'android:usesCleartextTraffic="true"' in text
