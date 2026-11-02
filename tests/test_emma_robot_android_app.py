from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "emma-robot-android"
JAVA = APP / "app" / "src" / "main" / "java" / "com" / "emma" / "robot" / "control"


def test_production_android_app_is_independent_and_portrait():
    settings = (APP / "settings.gradle").read_text(encoding="utf-8")
    manifest = (APP / "app" / "src" / "main" / "AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")

    assert 'rootProject.name = "EmmaRobotAndroid"' in settings
    assert "applicationId 'com.emma.robot.control'" in build
    assert 'android:screenOrientation="portrait"' in manifest
    # The label is a resValue since "Emma Event" (owner 30 Oct) is built from
    # the same sources: a plain build is still "Emma Robot".
    assert 'android:label="@string/app_label"' in manifest
    assert "resValue 'string', 'app_label', emmaEvent ? 'Emma Event' : 'Emma Robot'" in build
    assert (APP / "app" / "libs" / "aoborobotsdk-v2.1.2.aar").is_file()


def test_emma_event_is_a_second_package_only_when_asked_for():
    """`-PemmaApp=event` builds the event host's app beside this one: its own
    package (own icon, own settings), the face opened with event=1. Without
    the property the build is the robot app, unchanged."""
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    face = (JAVA / "EmmaFace.java").read_text(encoding="utf-8")
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

    assert "def emmaEvent = project.findProperty('emmaApp') == 'event'" in build
    assert "if (emmaEvent) {\n            applicationId 'com.emma.robot.event'" in build
    assert 'page.appendQueryParameter("event", "1")' in face
    load = face.split("private void loadEmma()", 1)[1].split("webView.loadUrl", 1)[0]
    assert "isEventApp(" in load
    # the sibling app lets go of the arm's serial port before this one takes it
    stop = main.split("private void stopConflictingVendorApp()", 1)[1].split("\n    }\n", 1)[0]
    assert "siblingPackage()" in stop


def test_production_app_starts_motion_locked_and_stops_on_control_loss():
    source = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

    assert "private volatile boolean screenArmed = false;" in source
    assert 'showState("ยังไม่เปิดควบคุม"' in source
    assert "onPause()" in source and "stopAndDisarm(" in source
    assert "onInputDeviceRemoved" in source
    assert 'stopAndDisarm("จอยหลุด' in source
    assert "onWindowFocusChanged" in source


def test_arm_connection_does_not_send_a_pose_automatically():
    source = (JAVA / "ArmController.java").read_text(encoding="utf-8")
    connect_body = source.split("boolean connect()", 1)[1].split("boolean isConnected()", 1)[0]

    assert "connectArm" in connect_body
    assert "executeArmActionGroup" not in connect_body
    assert "setPosition" not in connect_body
    assert "#99GC1" not in connect_body


def test_known_arm_evidence_is_not_presented_as_a_complete_wiring_map():
    source = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

    assert 'if (channel == 5)' in source and "เคยเห็นแขนขยับ" in source
    assert 'if (channel == 7)' in source and "ภาพทดสอบยืนยันการหันหัว" in source
    assert 'if (channel == 8)' in source and "ภาพทดสอบยืนยันการก้มเงยหัว" in source
    assert "ยังไม่ได้ทดสอบและจับคู่กับส่วนจริง" in source
