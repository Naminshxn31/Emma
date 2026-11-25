import re
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
    # The label is a resValue since "Emma Event" (owner 30 Oct) and "Emma Ceremony"
    # (owner 9 Nov) are built from the same sources: a plain build is still "Emma Robot".
    assert 'android:label="@string/app_label"' in manifest
    assert ("resValue 'string', 'app_label', emmaEvent ? 'Emma Event' : (emmaCeremony ? 'Emma Ceremony' : 'Emma Robot')"
            in build)
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
    # the sibling apps let go of the arm's serial port before this one takes it
    stop = main.split("private void stopConflictingVendorApp()", 1)[1].split("\n    }\n", 1)[0]
    assert "for (String sibling : siblingPackages())" in stop and '"am force-stop " + sibling' in stop


# ---- Emma Ceremony: the third app (owner 9 Nov 2026) ----------------------------------------------
# "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน อันนี้จะเป็นสำหรับพิธี ที่สามารถใช้ข้อมูลอันเก่าได้": its own APK
# like Emma Event, opening it = the ceremony's mode, and its last step opens Emma Event.

PACKAGES = {"robot": "com.emma.robot.control", "event": "com.emma.robot.event",
            "ceremony": "com.emma.robot.ceremony"}


def _method(text: str, signature: str) -> str:
    """A Java method's body, by brace matching from its signature."""
    at = text.index(signature)
    start = text.index("{", at)
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 1:i]
    raise AssertionError("unbalanced " + signature)


def test_emma_ceremony_is_a_third_package_only_when_asked_for():
    build = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    assert "def emmaCeremony = project.findProperty('emmaApp') == 'ceremony'" in build
    assert "if (emmaCeremony) {\n            applicationId 'com.emma.robot.ceremony'" in build
    # its own manifest overlay, never the event one: no start-on-boot for the ceremony
    ceremony = build.split("if (emmaCeremony) {\n        sourceSets", 1)[1].split("}", 1)[0]
    assert "src/ceremony/AndroidManifest.xml" in ceremony and "src/event" not in ceremony
    event = build.split("if (emmaEvent) {\n        sourceSets", 1)[1].split("}", 1)[0]
    assert "src/event/AndroidManifest.xml" in event and "src/ceremony" not in event
    # a misspelt -PemmaApp is refused, not built as the sales app under the asked-for name
    guard = build.split("if (project.findProperty('emmaApp') != null", 1)[1].split("\n}\n", 1)[0]
    assert "!emmaEvent && !emmaCeremony" in guard and "throw new GradleException(" in guard


def test_the_ceremony_manifest_sees_emma_event_and_starts_nothing_on_boot():
    """Android 11+: another package's launch intent is null unless this one declares it may
    see it -- an installed Emma Event read as "not_installed" without <queries>. And Emma
    Event keeps the start-on-boot: the ceremony app declares no receiver, no permission."""
    text = (APP / "app" / "src" / "ceremony" / "AndroidManifest.xml").read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    queries = body.split("<queries>", 1)[1].split("</queries>", 1)[0]
    assert re.findall(r'<package android:name="([^"]+)"', queries) == [PACKAGES["event"]]
    for word in ("receiver", "BOOT_COMPLETED", "uses-permission", "BootReceiver", "activity"):
        assert word not in body, word


def test_the_ceremony_app_opens_the_face_as_the_ceremony():
    face = (JAVA / "EmmaFace.java").read_text(encoding="utf-8")
    assert 'static final String CEREMONY_PACKAGE = "com.emma.robot.ceremony";' in face
    assert "return CEREMONY_PACKAGE.equals(context.getPackageName());" in _method(
        face, "static boolean isCeremonyApp(Context context)")
    load = face.split("private void loadEmma()", 1)[1].split("webView.loadUrl", 1)[0]
    assert 'if (isCeremonyApp(activity)) page.appendQueryParameter("ceremony", "1");' in load
    assert face.count('appendQueryParameter("ceremony"') == 1
    # the event app's marker stays its own
    assert 'if (isEventApp(activity)) page.appendQueryParameter("event", "1");' in load


def test_each_app_stops_both_of_the_other_two():
    """The three are one app built three times: a backgrounded sibling holds the arm's serial
    port and the SDK link -- both others go, whichever this one is."""
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
    body = _method(main, "private String[] siblingPackages()")
    constants = {"EmmaFace.ROBOT_PACKAGE": PACKAGES["robot"], "EmmaFace.EVENT_PACKAGE": PACKAGES["event"],
                 "EmmaFace.CEREMONY_PACKAGE": PACKAGES["ceremony"]}
    branches = {}
    for test, names in re.findall(r"if \(EmmaFace\.is(\w+)App\(this\)\) return new String\[\] \{([^}]*)\};", body):
        branches[test.lower()] = {constants[n.strip()] for n in names.split(",")}
    tail = re.search(r"\n\s*return new String\[\] \{([^}]*)\};\s*$", body).group(1)
    branches["robot"] = {constants[n.strip()] for n in tail.split(",")}
    assert set(branches) == {"ceremony", "event", "robot"}, branches
    for app, stops in branches.items():
        assert stops == set(PACKAGES.values()) - {PACKAGES[app]}, (app, stops)


def test_only_the_ceremony_app_opens_emma_event_and_without_su():
    face = (JAVA / "EmmaFace.java").read_text(encoding="utf-8")
    answer = _method(face, "private String answer(String message)")
    case = answer.index('case "emma-face:open:event":')
    assert answer.startswith('return bridge.openApp("event");', answer.index("return", case))
    assert answer.count("bridge.openApp(") == 1                 # the app's own literal, one case
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
    open_app = _method(main, "@Override public String openApp(String app)")
    lines = [ln.strip() for ln in open_app.strip().splitlines()]
    # the ceremony app alone, Emma Event alone -- both before anything is looked up or started
    assert lines[0] == 'if (!EmmaFace.isCeremonyApp(this)) return openRefusal("not_ceremony_app");'
    assert lines[1] == 'if (!"event".equals(app)) return openRefusal("unknown_app");'
    assert "getPackageManager().getLaunchIntentForPackage(EmmaFace.EVENT_PACKAGE)" in open_app
    assert 'if (launch == null) return openRefusal("not_installed");' in open_app
    assert open_app.index("launch == null") < open_app.index("startActivity(launch)")
    for word in ("RootShell", "am start", " su ", "Runtime.getRuntime"):
        assert word not in open_app, word
    assert 'return "{\\"accepted\\":true}";' in open_app
    # ...and it LEAVES once Emma Event is started (review of the split, 9 Nov): only paused, its
    # face page kept reporting the 60 s latch onPause sets, and the server refused Emma Event's
    # first walk and its walk-up-to-guests for a minute. After the start, never before it.
    assert "ui.post(this::finishAndRemoveTask);" in open_app
    assert open_app.index("startActivity(launch)") < open_app.index("finishAndRemoveTask")
    assert open_app.index("finishAndRemoveTask") < open_app.index('return "{\\"accepted\\":true}";')


def test_the_open_app_marker_is_one_literal_for_all_three_builds():
    """EmmaOpenApp/1 in the one place the user agent is set; the page asks only an app that
    says so (an APK without it answers bad_request, an older one shows the prompt itself)."""
    face = (JAVA / "EmmaFace.java").read_text(encoding="utf-8")
    suffix = re.findall(r'setUserAgentString\(settings\.getUserAgentString\(\)\s*\+\s*"([^"]*)"\)', face)
    assert len(suffix) == 1 and "EmmaOpenApp/1" in suffix[0].split()
    page = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    gate = re.search(r"var OPEN_APP = ON_ROBOT && (.*);", page).group(1)
    ua = "Mozilla/5.0 (Linux; Android 15; ZC-3588A) AppleWebKit/537.36 wv" + suffix[0]
    regexes = re.findall(r"/((?:[^/\\]|\\.)+)/\.test\(UA\)", gate)
    assert regexes and all(re.search(rx.replace("\\/", "/"), ua) for rx in regexes)
    assert not re.search(regexes[0].replace("\\/", "/"), ua.replace(" EmmaOpenApp/1", ""))


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


# ---- sibling takeover (24 Nov 2026) --------------------------------------------
# Tonight `am start` of Emma Ceremony paused Emma Event; the pause's 60 s latch rode Event's
# still-open status link and the server refused the ceremony's opening until Event was
# force-stopped by adb. The pause still stops and latches; a sibling that came in front straight
# from it lifts only THAT pause's latch (NavRules.siblingTakeover).

_SIBLING_MAIN = r"""
package com.emma.robot.control;
public class SiblingMain {
    static void p(String k, boolean v) { System.out.println(k + "=" + v); }
    public static void main(String[] a) {
        String[] sib = {"com.emma.robot.control", "com.emma.robot.ceremony"};
        String me = "com.emma.robot.event";
        p("takeover", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 1000, 2500, false, false, false));
        p("not_sibling", NavRules.siblingTakeover("com.other.app", me, sib, 1000, 2500, false, false, false));
        p("self", NavRules.siblingTakeover(me, me, new String[] {me}, 1000, 2500, false, false, false));
        p("no_sender", NavRules.siblingTakeover(null, me, sib, 1000, 2500, false, false, false));
        p("no_loss", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 0, 2500, false, false, false));
        p("too_late", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 1000, 11001, false, false, false));
        p("home_between", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 1000, 2500, true, false, false));
        p("in_front", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 1000, 2500, false, true, false));
        p("other_stop", NavRules.siblingTakeover("com.emma.robot.ceremony", me, sib, 1000, 2500, false, false, true));
    }
}
"""


def test_a_sibling_lifts_only_the_latch_of_the_pause_it_caused(tmp_path):
    import subprocess

    from tests.test_emma_robot_body_nav_app import _javac

    tools = _javac()
    if tools is None:
        import pytest

        pytest.skip("no JDK found: NavRules.siblingTakeover was NOT compiled or run -- which pause "
                    "latch a sibling may lift is unchecked")
    javac, java = tools
    (tmp_path / "SiblingMain.java").write_text(_SIBLING_MAIN, encoding="utf-8")
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(tmp_path), str(JAVA / "NavRules.java"),
                    str(tmp_path / "SiblingMain.java")], check=True, capture_output=True)
    out = subprocess.run([java, "-cp", str(tmp_path), "com.emma.robot.control.SiblingMain"], check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout
    got = dict(line.split("=") for line in out.split())
    assert got.pop("takeover") == "true"
    # every other case is a real stop and keeps its 60 s: not a sibling, no loss pending, too
    # late, Home/launcher in between (stopped), back in front, another stop since the loss
    assert set(got.values()) == {"false"}, got


def test_the_app_lifts_a_pause_latch_only_on_a_verified_sibling():
    main = (JAVA / "MainActivity.java").read_text(encoding="utf-8")
    pause = _method(main, "protected void onPause()")
    # the pause still stops and latches first; the loss is noted around that very stop
    assert pause.index("noteForegroundLoss();") < pause.index(
        'stopBodyAndNav("not_foreground", "not_foreground", false);') < pause.index("noteLossLatched();")
    focus = _method(main, "public void onWindowFocusChanged(boolean hasFocus)")
    assert focus.index("noteForegroundLoss();") < focus.index(
        'stopBodyAndNav("not_foreground", "not_foreground", false);') < focus.index("noteLossLatched();")
    assert "stoppedSinceLoss = true;" in _method(main, "protected void onStop()")
    assert "tellSiblingsInFront();" in _method(main, "protected void onStart()")
    tell = _method(main, "private void tellSiblingsInFront()")
    assert "setShareIdentityEnabled(true)" in tell and "for (String sibling : siblingPackages())" in tell
    receive = _method(main, "private void registerSiblingReceiver()")
    assert "siblingInFront(Build.VERSION.SDK_INT >= 34 ? getSentFromPackage() : null);" in receive
    assert "Context.RECEIVER_EXPORTED" in receive
    lift = _method(main, "private void siblingInFront(String sender)")
    # identity shared AND our signature, the pure rule, then the latch lowered -- never cleared
    assert lift.index("PackageManager.SIGNATURE_MATCH") < lift.index("NavRules.siblingTakeover(") \
        < lift.index("body.liftLatchTo(bodyLatchBeforeLoss);")
    assert "nav.liftLatchTo(navLatchBeforeLoss);" in lift and "clearLatch" not in lift
    assert "body.latchSets() != bodyLatchSetsAtLoss" in lift and "nav.latchSets() != navLatchSetsAtLoss" in lift
    for name in ("BodyController.java", "NavLauncher.java"):
        text = (JAVA / name).read_text(encoding="utf-8")
        assert "if (until < latchedUntil) latchedUntil = until;" in _method(text, "void liftLatchTo(long until)")
        stop = _method(text, "void stop(String reason)" if name == "BodyController.java"
                       else "boolean stop(String reason, boolean explicit)")
        assert stop.index("latchedUntil = SystemClock.elapsedRealtime() +") < stop.index(
            "latchSets.incrementAndGet();")
    manifest = (APP / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    queries = manifest.split("<queries>", 1)[1].split("</queries>", 1)[0]
    assert set(re.findall(r'<package android:name="([^"]+)"', queries)) == set(PACKAGES.values()) | {
        "com.emma.robot.control"}
