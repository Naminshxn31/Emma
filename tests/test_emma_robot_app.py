"""Emma-on-robot Android app: the invariants that make it safe to stand in a
sales room, checked against the source of the tracked app tree.

This is a source-contract test (the project's pattern for Android, since the
APK is built off-machine): it reads the Java/manifest/gradle text and asserts
the properties that would be dangerous to lose, rather than compiling.

The app is `apps/emma-robot-android` (`com.emma.robot.control`) -- the
git-tracked one, evolved to show Emma's voice face by default while the Xbox/
arm/chassis operator UI stays hidden behind a staff gesture. The two failures
worth a test:

  A customer reaches the body. The default screen is a WebView; a customer
  bumping the gamepad or reloading the WebView must never arm the wheels.
  Drive/arm keys remain gated behind the hidden operator mode. The one face
  exception is a fixed handshake from the trusted preview page.

  The stop weakens. Button B (stop + lock) must fire in either mode -- it is
  the software stop that backs the physical one -- so it must stay OUTSIDE the
  operator-only gate.

And one the camera added (2026-09-29): the face WebView now holds the robot's
only USB camera (it streams /preview?cam=1 to /ws/camera for the greeter and
the face scan), so the grant must stay mic + camera for the trusted origin and
nothing for anyone else, and a refused camera must never cost Emma her ears.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/emma-robot-android/app"
SRC = APP / "src/main/java/com/emma/robot/control"
MAIN = SRC / "MainActivity.java"
FACE = SRC / "EmmaFace.java"
BODY = SRC / "BodyController.java"
GESTURES = SRC / "BodyGestures.java"
MANIFEST = APP / "src/main/AndroidManifest.xml"
GRADLE = APP / "build.gradle"
NET = APP / "src/main/res/xml/network_security_config.xml"
NET_DEBUG = APP / "src/main/res/xml/network_security_config_debug.xml"
CA = APP / "src/main/res/raw/emma_local_ca.crt"


def main() -> str:
    return MAIN.read_text(encoding="utf-8")


def face() -> str:
    return FACE.read_text(encoding="utf-8")


def method(text: str, signature: str) -> str:
    """The body of one Java method, by brace matching from its signature --
    slicing between two method names silently widens when methods move."""
    start = text.index(signature)
    open_at = text.index("{", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    raise AssertionError(f"unbalanced braces after {signature}")


def code_only(java: str) -> str:
    """Drop // and /* */ comments so a guard cannot be satisfied by prose --
    but not a "//" inside a string literal ("://" is in every origin)."""
    out, i, n = [], 0, len(java)
    while i < n:
        c = java[i]
        if c in "\"'":
            j = i + 1
            while j < n and java[j] != c:
                j += 2 if java[j] == "\\" else 1
            out.append(java[i:j + 1])
            i = j + 1
        elif java.startswith("//", i):
            i = java.find("\n", i)
            i = n if i < 0 else i
        elif java.startswith("/*", i):
            end = java.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ---- the face is the default, the operator UI is hidden ---------------------

def test_emma_face_is_the_default_screen_and_operator_ui_is_hidden():
    text = main()
    # MainActivity is the face's Bridge (2026-10-09, body/nav design §2b); the
    # legacy greeting still lands on requestArmGreeting
    assert "emmaFace = new EmmaFace(this, this)" in text
    assert "requestArmGreeting();" in method(text, "@Override public void armGreeting()")
    assert "setContentView(stack)" in text
    # the whole existing operator UI is kept as one hideable view, GONE by default
    assert "operatorRoot = buildUi()" in text
    assert "operatorRoot.setVisibility(View.GONE)" in text
    # and Emma's face is started with the launch intent
    assert "emmaFace.start(getIntent())" in text


def test_operator_screen_is_reachable_only_by_a_staff_gesture():
    text = main()
    # gamepad: hold START then SELECT
    assert "KEYCODE_BUTTON_START" in text
    assert "KEYCODE_BUTTON_SELECT" in text
    assert "toggleOperator()" in text
    # touch (emulator / no gamepad): a DELIBERATE 3-second corner hold, not a
    # stock long-press (~0.5s) a customer could trip by resting a finger there
    assert "trackCornerHold" in text
    assert "CORNER_HOLD_MS = 3000" in text
    assert "postDelayed(cornerRevealRun" in text


# ---- the stop must not weaken ----------------------------------------------

def test_button_b_stops_in_either_mode_and_arm_drive_keys_do_not():
    text = main()
    dispatch = text[text.index("public boolean dispatchKeyEvent"):text.index("return super.dispatchKeyEvent")]
    gate = dispatch.index("if (operatorVisible) {")
    # B (stop+lock) is handled BEFORE the operator-only gate -> always fires
    assert dispatch.index("KEYCODE_BUTTON_B") < gate
    assert 'stopAndDisarm("ปุ่ม B' in dispatch
    # arm/drive keys are INSIDE the gate -> a customer's stray press is inert
    for gated in ("KEYCODE_BUTTON_A", "KEYCODE_BUTTON_X", "KEYCODE_BUTTON_Y",
                  "KEYCODE_DPAD_LEFT", "KEYCODE_DPAD_RIGHT"):
        assert dispatch.index(gated) > gate, gated


def test_chassis_and_aobo_force_stop_run_only_in_operator_mode():
    """onResume must not force-stop Aobo or grab the SLAMTEC SDK on the emulator
    (there is no robot there) or in front of a customer -- only when the operator
    screen is actually up. The force-stop LOGIC is untouched; only its trigger
    is gated."""
    text = main()
    assert "if (operatorVisible) refreshBaseStatus();" in text
    # no un-gated bare refreshBaseStatus() call left inside onResume
    on_resume = text[text.index("protected void onResume"):text.index("protected void onPause")]
    assert "if (operatorVisible) refreshBaseStatus();" in on_resume
    assert "\n        refreshBaseStatus();" not in on_resume


# ---- the face cannot reach the body ----------------------------------------

def test_the_face_class_cannot_directly_drive_the_body():
    """EmmaFace validates one handshake request, while direct hardware
    control stays in MainActivity/ArmController."""
    text = face()
    for body in ("SlamwareCorePlatform", "ArmController", "moveBy",
                 "192.168.11.1", "1445", "com.aobo", "cancelNavigation"):
        assert body not in text, body


def test_face_handshake_requires_trusted_preview_and_foreground():
    prompt = method(face(), "public boolean onJsPrompt(")
    assert '"emma-arm:greet:6".equals(message)' in prompt
    # the gate itself moved into trustedPreview (shared by every emma-* message)
    assert "if (trustedPreview(url, view)) bridge.armGreeting();" in prompt
    gate = method(face(), "private boolean trustedPreview(String url, WebView view)")
    assert "trusted(originOf(source))" in gate
    assert "trusted(originOf(current))" in gate
    assert '"/preview".equals(source.getPath())' in gate
    assert '"1".equals(current.getQueryParameter("app"))' in gate
    greeting = method(main(), "private void requestArmGreeting()")
    assert "!faceForeground || operatorVisible" in greeting
    assert "body.greet();" in greeting
    # one lock on the arm (was greetingPending), and the verified group 6
    greet = code_only(method(BODY.read_text(encoding="utf-8"), "synchronized void greet()"))
    assert "busy.compareAndSet(false, true)" in greet
    assert 'begin("handshake", now, false);' in greet
    assert "HANDSHAKE_GROUP = 6;" in GESTURES.read_text(encoding="utf-8")
    assert "arm.playGroup(\"handshake\".equals(id) ? BodyGestures.HANDSHAKE_GROUP" in BODY.read_text(
        encoding="utf-8")


def test_the_greeting_gesture_returns_the_arm_to_rest_and_a_stop_voids_it():
    """Group 6 parks the arm at its final pose; nothing ever undid that, so
    every greeting after the first replayed the group with no travel left --
    the board answered #AGF while the hand never visibly rose (2026-09-30).
    The app now returns the arm to the vendor's rest group once the board says
    the gesture finished, and anything that makes that wrong (an explicit
    stop, the operator playing a group) voids the scheduled return -- after a
    stop NOTHING may move on a timer."""
    # 2026-10-09: the rest-return moved into BodyController, which now does it
    # for every arm gesture; the wave counter is its `generation`.
    text = main()
    body = BODY.read_text(encoding="utf-8")
    # fires on the board's own end-of-group answer, with a fallback timer.
    # The #AGF is framed across serial reads in ArmController (review #30);
    # MainActivity only relays the framed event.
    assert "if (body != null) body.onArmFinished();" in code_only(method(text, "@Override public void onArmFinished()"))
    assert 'data.contains("AGF")' not in code_only(text)
    play = code_only(method(body, "private void play(String id, int gen, List<BodyGestures.Frame> frames, boolean speech)"))
    assert "later(gen, () -> restReturn(gen, \"no #AGF heard\"), BodyGestures.GROUP_FALLBACK_MS);" in play
    finished = code_only(method(body, "void onArmFinished()"))
    assert "later(gen, () -> restReturn(gen, \"#AGF\"), groupHoldMs);" in finished
    gestures = GESTURES.read_text(encoding="utf-8")
    assert "HANDSHAKE_HOLD_MS = 5000;" in gestures and "GROUP_FALLBACK_MS = 30000;" in gestures
    # a timer carrying an older generation does nothing: overtaken returns are void
    later = code_only(method(body, "private void later(int gen, Runnable work, long delayMs)"))
    assert "if (gen == generation.get()) work.run();" in later
    home = code_only(method(body, "private void restReturn(int gen, String why)"))
    assert "gen != generation.get()" in home
    assert "host.operatorVisible()" in home             # the operator owns the arm
    # resetHome, not playGroup: playGroup's operator range (1-26) refuses 99 --
    # the first live rest-return died on exactly that check (2026-09-30)
    assert "arm.resetHome();" in home
    assert "playGroup" not in home
    # the return unlocks nothing: ensureArm (the only connect a gesture uses)
    # leaves the operator's manual controls locked
    assert "upperBodyArmed = false;" in method(text, "@Override public void ensureArm()")
    # every stop voids it: the generation moves and the timers are removed
    void = code_only(method(body, "private void voidEverything(String reason)"))
    assert "generation.incrementAndGet();" in void
    assert "ui.removeCallbacksAndMessages(TIMERS);" in void
    for path in ("void stop(String reason)", "void halt()", "void voidRestReturn()"):
        assert "voidEverything(" in code_only(method(body, path)), path
    stop = code_only(method(text, "private void stopAndDisarm(String reason, boolean explicit, boolean deleteNow)"))
    assert "body.halt();" in stop
    play_group = code_only(method(text, "private void playSelectedArmGroup()"))
    assert "cancelGreetingHome();" in play_group
    cancel = code_only(method(text, "private void cancelGreetingHome()"))
    assert "body.voidRestReturn();" in cancel


def test_the_face_grants_the_trusted_origin_audio_and_video_and_nothing_else():
    """Was "audio only" until the chest screen moved from Chrome's
    /?kiosk=1&cam=1 (which fed the greeter) into this WebView: from then on
    the WebView held the robot's only USB camera and nothing fed the greeter.
    So video joins audio -- for the trusted origin alone. An untrusted origin
    is refused before a single resource is looked at, and no other resource
    (MIDI sysex, protected media ID) is grantable at all."""
    text = face()
    handle = text[text.index("private void handleWebPermission"):
                  text.index("boolean onRequestPermissionsResult")]
    # untrusted origin: denied and returned, before anything can be granted
    gate = "if (!trusted(originOf(request.getOrigin()))) {   // only the trusted origin"
    assert gate in handle
    # and "trusted" means exactly one named origin: before loadEmma names it
    # (or after retirePage), "" -- what originOf gives a URL with no host --
    # must match nothing
    assert "return !allowedOrigin.isEmpty() && allowedOrigin.equals(origin);" in method(
        text, "private boolean trusted(String origin)")
    untrusted = handle[handle.index(gate):]
    untrusted = untrusted[:untrusted.index("}")]
    assert "request.deny();" in untrusted and "return;" in untrusted
    assert "grant" not in untrusted
    assert handle.index(gate) < handle.index("request.grant(")
    # the whole file names exactly two page resources: audio and video
    assert set(re.findall(r"PermissionRequest\.RESOURCE_\w+", text)) == {
        "PermissionRequest.RESOURCE_AUDIO_CAPTURE",
        "PermissionRequest.RESOURCE_VIDEO_CAPTURE",
    }
    # each rides its own Android permission; anything else maps to nothing
    table = text[text.index("private static String androidPermissionFor"):
                 text.index("private boolean holds")]
    assert "RESOURCE_AUDIO_CAPTURE.equals(resource)) return android.Manifest.permission.RECORD_AUDIO;" in table
    assert "RESOURCE_VIDEO_CAPTURE.equals(resource)) return android.Manifest.permission.CAMERA;" in table
    assert "return null;" in table
    # every grant hands over only the filtered list, never what the page asked for
    grants = re.findall(r"request\.grant\(([^;]*)\);", text)
    assert grants, "no grant call found"
    assert set(grants) <= {"grantableNow(request).toArray(new String[0])",
                           "allowed.toArray(new String[0])"}, grants
    assert "List<String> allowed = grantableNow(request);" in text
    # the grant-time re-check survives: origin again, and the page did not navigate away
    result = text[text.index("boolean onRequestPermissionsResult"):
                  text.index("private LinearLayout buildSetupPanel")]
    assert "trusted(originOf(request.getOrigin()))" in result
    assert "trusted(currentOrigin)" in result
    # kiosk audio starts without a tap; mixed content is refused
    assert "setMediaPlaybackRequiresUserGesture(false)" in text
    assert "MIXED_CONTENT_NEVER_ALLOW" in text


def test_the_camera_is_asked_with_the_mic_and_a_refused_camera_keeps_the_mic():
    """One system dialog asks for every missing media permission (first-run
    setup is one sitting), but the answer is judged per permission from what
    Android now holds -- camera refused + mic granted is a working Emma. The
    page asks twice (mic, camera), so a second request can land while the
    dialog is up: pending requests are a list, not one slot that would
    overwrite the mic request and leave Emma deaf."""
    text = face()
    media = text[text.index("MEDIA_PERMISSIONS = {"):]
    media = media[:media.index("};")]
    assert "android.Manifest.permission.RECORD_AUDIO" in media
    assert "android.Manifest.permission.CAMERA" in media
    assert "activity.requestPermissions(askedPermissions, MEDIA_PERMISSION_REQUEST)" in text
    # ...and the ONE dialog covers every media permission still worth asking for
    handle = method(text, "private void handleWebPermission(PermissionRequest request)")
    assert "for (String permission : MEDIA_PERMISSIONS) {" in handle
    assert "askedPermissions = missing.toArray(new String[0]);" in handle
    # per resource: only a HELD permission lets its resource through
    grantable = text[text.index("private List<String> grantableNow"):
                     text.index("private void handleWebPermission")]
    assert "if (permission != null && holds(permission)) allowed.add(resource);" in grantable
    # the result reads what Android holds, not results[0] (the mic's slot once)
    result = text[text.index("boolean onRequestPermissionsResult"):
                  text.index("private LinearLayout buildSetupPanel")]
    assert "grantableNow(request)" in result
    assert "results[0]" not in result
    # only the mic earns the customer-facing toast
    assert "if (micRefused) {" in result
    # overlapping requests queue behind the one open dialog
    assert "List<PermissionRequest> pendingWebPermissions" in text
    assert "PermissionRequest pendingWebPermission;" not in text
    assert "if (mediaDialogOpen) return;" in text


def test_the_face_loads_the_preview_app_page_with_the_token():
    text = face()
    assert 'appendQueryParameter("app", "1")' in text
    assert '/preview' in text
    assert 'appendQueryParameter("token", token)' in text


def test_the_face_turns_on_the_robot_camera_stream_and_keeps_the_other_params():
    """/preview streams the robot camera to /ws/camera only when the URL says
    cam=1 (a desk browser must never start uploading its webcam), so the robot
    app is where that opt-in lives. It is ADDED to app=1 and the token, on top
    of whatever query the configured server URL carries; a cam written there
    wins (cam=0 = off without a new APK)."""
    text = face()
    load = text[text.index("private void loadEmma"):text.index("private boolean validServer")]
    assert "Uri.Builder page = base.buildUpon();" in load     # the configured URL's query survives
    assert 'if (base.getQueryParameter("cam") == null) page.appendQueryParameter("cam", "1");' in load
    assert 'page.appendQueryParameter("app", "1");' in load
    assert 'page.appendQueryParameter("token", token);' in load


def test_release_is_https_only_and_ignores_intent_supplied_origins():
    """Release must accept only HTTPS and must not let another app hand it a
    server/token via Intent (the launcher activity is exported) -- that would be
    a mic-hijack path. Debug alone allows http to loopback/emulator."""
    text = face()
    # debug/release split is read from FLAG_DEBUGGABLE, not BuildConfig, so it
    # compiles without the buildConfig feature turned on
    assert "FLAG_DEBUGGABLE" in text
    assert "BuildConfig.DEBUG" not in text   # the code path, not the word in a comment
    # https always passes; http only in debug and only to loopback/emulator
    assert 'if ("https".equals(uri.getScheme())) return true;' in text
    assert "debugBuild() && \"http\".equals(uri.getScheme()) && isDebugLoopback(host)" in text
    # release strips Intent-supplied server/token
    assert "if (!debugBuild()) {" in text
    assert "intent.removeExtra(PREF_SERVER)" in text


# ---- manifest / gradle / network security ----------------------------------

def test_manifest_adds_mic_and_https_config_keeps_portrait():
    text = MANIFEST.read_text(encoding="utf-8")
    assert 'android.permission.RECORD_AUDIO' in text
    assert 'android.permission.MODIFY_AUDIO_SETTINGS' in text
    assert 'android:networkSecurityConfig="${networkSecurityConfig}"' in text
    assert 'android:usesCleartextTraffic="${usesCleartextTraffic}"' in text
    assert 'android:screenOrientation="portrait"' in text   # 1080x1920 chest screen


def test_manifest_adds_the_camera_without_making_it_a_requirement():
    """The face WebView streams the robot camera to the greeter, so the app
    needs CAMERA. But CAMERA implies camera + camera.autofocus as REQUIRED
    features, and the voice face must still install on a body with no lens:
    the camera is an extra, never a precondition for Emma to listen."""
    text = MANIFEST.read_text(encoding="utf-8")
    assert '<uses-permission android:name="android.permission.CAMERA" />' in text
    for feature in ("android.hardware.camera", "android.hardware.camera.any",
                    "android.hardware.camera.autofocus"):
        assert f'<uses-feature android:name="{feature}" android:required="false" />' in text, feature
    assert 'android:required="true"' not in text


def test_gradle_release_is_cleartext_off_debug_on():
    text = GRADLE.read_text(encoding="utf-8")
    # release/default: no cleartext, hardened network config
    assert "usesCleartextTraffic : 'false'" in text
    assert "networkSecurityConfig: '@xml/network_security_config'" in text
    # debug: cleartext + the debug config (for http loopback during dev)
    assert "usesCleartextTraffic : 'true'" in text
    assert "networkSecurityConfig: '@xml/network_security_config_debug'" in text


def test_network_config_refuses_cleartext_in_release_and_trusts_local_ca():
    release = NET.read_text(encoding="utf-8")
    debug = NET_DEBUG.read_text(encoding="utf-8")
    assert 'cleartextTrafficPermitted="false"' in release
    assert 'cleartextTrafficPermitted="true"' in debug
    for cfg in (release, debug):
        assert "@raw/emma_local_ca" in cfg     # the server's local HTTPS CA
    assert CA.is_file(), "the local CA cert must ship for HTTPS to validate"


def test_release_config_still_lets_the_chassis_speak_plain_http():
    """The chassis board is http://192.168.11.1:1448. Hardening Emma's origin to
    HTTPS-only must NOT block that, or the operator/chassis REST dies silently in
    the one build that ships (release). A scoped domain-config is the exception."""
    release = NET.read_text(encoding="utf-8")
    assert "192.168.11.1" in release
    # the chassis domain is its own cleartext-permitted block, while base stays false
    domain_block = release[release.index("<domain-config"):]
    assert 'cleartextTrafficPermitted="true"' in domain_block
    assert "192.168.11.1" in domain_block


# ---- regressions the review caught (2026-09-25) ----------------------------

def test_the_staff_toggle_cannot_latch_open():
    """startHeld is set on START-down and cleared on START-up; if the up is lost
    (controller unplugged mid-hold, focus stolen by the mic dialog), a single
    SELECT must NOT flip a customer into the operator screen. So startHeld is
    force-cleared on every such event."""
    text = main()
    for hook in ("onWindowFocusChanged", "onPause", "onInputDeviceRemoved"):
        block = text[text.index(hook):]
        assert "startHeld = false" in block[:400], hook


def test_the_corner_reveal_is_a_deliberate_hold_and_does_not_swallow_page_touches():
    """The reveal is a 3-second corner hold observed in dispatchTouchEvent and
    passed through (return super), so it never eats the taps the Emma page needs.
    It must NOT be a stock long-press (~0.5s) nor a touch-consuming overlay View."""
    text = main()
    assert "public boolean dispatchTouchEvent(MotionEvent event)" in text
    assert "return super.dispatchTouchEvent(event)" in text
    assert "CORNER_HOLD_MS = 3000" in text            # a deliberate hold
    assert "onLongPress" not in text                  # the ~0.5s stock long-press is gone
    assert "setOnLongClickListener" not in text       # the consuming-overlay approach is gone
    assert "removeCallbacks(cornerRevealRun)" in text  # moving out / lifting cancels it


def test_button_b_always_cancels_the_chassis_but_an_idle_emma_stop_does_not():
    """B is the software stop behind the physical one: it must cancel chassis
    motion in EVERY mode, including Emma mode where this app never connected the
    SDK (motion started by the vendor app / RoboStudio must still stop). An
    AUTOMATIC stop (focus loss, stick centre) in Emma mode -- where this app is
    not the wheel owner -- stays gated, so it does not keep poking a chassis it
    is not driving. This replaces the earlier test that pinned B's cancel as
    gated in Emma mode; the review (2026-09-26) called that the regression."""
    text = main()
    cancel = text[text.index("private void cancelCurrent(boolean explicit)"):
                  text.index("private void cancelSdkMove")]
    assert "if (!explicit && sdkPlatform == null) return;" in cancel   # only the automatic path is gated
    assert "\n        if (sdkPlatform == null) return;" not in cancel   # the old unconditional gate is gone
    # B and the operator STOP button take the explicit path (the third
    # argument only defers the board DELETE to a live walk's escalation,
    # review #21; with no walk live it is sent at once)
    assert 'stopAndDisarm("ปุ่ม B — หยุดและล็อกแล้ว", true, !navLive)' in text
    assert 'stopAndDisarm("หยุดและล็อกแล้ว", true, !navLive)' in text
    # returning to the Emma face releases the SDK, so Emma mode is not the owner
    show_emma = text[text.index("private void showEmma"):text.index("private void showOperator")]
    assert "disconnectSdk()" in show_emma


def test_customer_mode_swallows_stray_gamepad_buttons():
    """In customer mode an unhandled gamepad button falls through to a framework
    fallback (e.g. B->BACK) that backgrounds the kiosk or clicks a focused page
    control. All gamepad buttons are consumed in customer mode instead."""
    text = main()
    assert "if (!operatorVisible && isGamepadButton(key)) return true;" in text
    assert "KeyEvent.isGamepadButton(key)" in text


def test_the_setup_gear_leaves_the_customer_face_once_configured_in_every_build():
    """The gear sets the server URL and token; on the face a customer sees it must
    not stay reachable once Emma is configured (anyone at the kiosk could
    otherwise repoint the app at another server). It used to stay up in a debug
    build "for development against a PC" -- and the debug APK is the one the
    robot runs (no release signing; the owner provisions it over adb), so the
    gear was on the chest screen for good (review 2026-09-29). Now the build
    type plays no part: first-run only."""
    text = face()
    gear = code_only(method(text, "private void refreshGear()"))
    assert "settingsButton.setVisibility(isConfigured() ? View.GONE : View.VISIBLE);" in gear
    assert "debugBuild" not in gear
    save = text[text.index("private void saveAndLoad"):text.index("private void loadEmma")]
    assert "refreshGear()" in save                          # disappears once set up
    # a token lost later (intent to a new origin, mismatched prefs) brings it back
    assert "refreshGear();" in method(text, "private void forgetToken()")
    assert "refreshGear();" in method(text, "private void openOrSetup()")


def test_staff_reach_setup_with_a_deliberate_corner_hold_that_never_eats_a_touch():
    """With the gear gone, staff still need a way back into setup that a
    passer-by will not hit: a 5-second hold in the TOP-RIGHT corner (where the
    gear was) -- longer than, and in the other corner from, the operator
    screen's 3-second top-left hold. Observed from the WebView's touch listener,
    which returns false so the page still gets every tap; lifting, a second
    finger or leaving the corner cancels it."""
    text = face()
    assert "SETUP_HOLD_MS = 5000" in text
    # the listener is set where EVERY face WebView is made -- buildUi and the
    # renderer-death rebuild both use newWebView() (review #14)
    assert "newWebView()" in method(text, "private FrameLayout buildUi()")
    build = method(text, "private WebView newWebView()")
    listener = build[build.index("webView.setOnTouchListener("):]
    listener = listener[:listener.index("});")]
    assert "trackSetupHold(v, event);" in listener
    assert "return false;" in listener and "return true" not in listener
    hold = code_only(method(text, "private void trackSetupHold(View v, MotionEvent e)"))
    assert "e.getX() > v.getWidth() - corner && e.getY() < corner" in hold     # top-right
    assert "v.postDelayed(setupReveal, SETUP_HOLD_MS);" in hold
    for cancel in ("ACTION_UP", "ACTION_CANCEL", "ACTION_POINTER_DOWN", "ACTION_MOVE"):
        assert cancel in hold, cancel
    assert hold.count("v.removeCallbacks(setupReveal);") == 2               # move-out, and up/cancel/2nd finger
    reveal = text[text.index("private final Runnable setupReveal"):]
    reveal = reveal[:reveal.index("};")]
    assert "showSetup();" in reveal
    # the operator hold stays where it was, so the two gestures cannot collide
    assert "e.getX() < dp(90) && e.getY() < dp(90)" in main()


def test_the_stored_token_goes_only_to_the_origin_it_was_stored_for():
    """Review 2026-09-29 (finding 3): the setup panel refilled the stored
    WS_TOKEN, and loadEmma appended the stored token to whatever server was
    saved. A visitor who replaced only the server got
    /preview?...&token=<WS_TOKEN> on their own host -- the token that guards
    /ws, which drives the robot -- plus the mic and camera. The token now
    carries the origin it was stored for, and every path honours it."""
    text = face()
    assert 'PREF_TOKEN_ORIGIN = "ws_token_origin"' in text
    # 1. setup never puts the stored token back into the field
    show = code_only(method(text, "private void showSetup()"))
    assert 'tokenInput.setText("");' in show
    assert "PREF_TOKEN," not in show and "PREF_TOKEN)" not in show
    # 2. the panel: blank keeps the token ONLY for its own origin, else refuses
    #    and saves nothing; a typed token is bound to the origin typed with it
    save = code_only(method(text, "private void saveAndLoad()"))
    keep = save.index('} else if (origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {')
    refuse = save.index("tokenInput.setError(TOKEN_FOR_THIS_SERVER);", keep)
    assert save.index("return;", refuse) < save.index("prefs.edit()")
    assert ".putString(PREF_TOKEN, token).putString(PREF_TOKEN_ORIGIN, origin)" in save
    # 3. the last line in loadEmma, whatever moved the server: a token bound
    #    to another origin is forgotten and NEVER appended
    load = code_only(method(text, "private void loadEmma()"))
    guard = load.index('!origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))')
    tail = load[guard:]
    assert tail.index("forgetToken();") < tail.index("return;")
    assert guard < load.index('page.appendQueryParameter("token", token);')
    assert load.index("return;", guard) < load.index('page.appendQueryParameter("token", token);')
    assert "allowedOrigin = origin;" in load[guard:]            # trusted only once the pair matches
    # 4. the debug launch intent: a server on a new origin with no token of
    #    its own drops the stored one instead of walking it to the new host
    intent = code_only(method(text, "private boolean consumeLaunchConfiguration(Intent intent)"))
    assert 'else if (token != null || !origin.equals(prefs.getString(PREF_TOKEN_ORIGIN, ""))) {' in intent
    drop = intent[intent.index("else if (token != null || !origin.equals"):]
    assert "editor.remove(PREF_TOKEN).remove(PREF_TOKEN_ORIGIN);" in drop[:drop.index("}")]
    assert ".putString(PREF_TOKEN, token.trim()).putString(PREF_TOKEN_ORIGIN, origin)" in intent
    # ...and a refused server rejects the whole intent, so its token is not
    # bound to the server already stored
    assert "if (server != null && !validServer(server)) return false;" in intent
    assert intent.index("!validServer(server)) return false;") < intent.index("prefs.edit()")
    # 5. tokens stored by the APK before this existed are bound to the server
    #    stored beside them, so the upgraded robot keeps working
    legacy = code_only(method(text, "private void bindLegacyToken()"))
    assert "isConfigured() && !prefs.contains(PREF_TOKEN_ORIGIN)" in legacy
    assert "bindLegacyToken();" in method(text, "EmmaFace(Activity activity, Bridge bridge)")
    # 6. an intent that leaves Emma unconfigured retires the old page rather
    #    than letting it listen behind the setup panel
    assert "if (consumeLaunchConfiguration(intent) && webView != null) openOrSetup();" in text
    retire = code_only(method(text, "private void retirePage()"))
    assert 'allowedOrigin = "";' in retire and 'loadUrl("about:blank")' in retire


def test_debug_keeps_its_provisioning_and_debugging_but_no_longer_the_token():
    """The owner provisions the robot's debug APK with
    `am start --es server_url --es ws_token`, and uses WebContents debugging:
    both stay, debug-only. What changed is only that the extras can no longer
    carry the stored token to a new origin (see the test above)."""
    text = face()
    assert "WebView.setWebContentsDebuggingEnabled(debugBuild());" in text
    intent = code_only(method(text, "private boolean consumeLaunchConfiguration(Intent intent)"))
    strip = intent.index("if (!debugBuild()) {")
    read = intent.index("intent.getStringExtra(PREF_SERVER)")
    assert strip < read                                       # release strips, debug reads
    assert "intent.getStringExtra(PREF_TOKEN)" in intent
    assert 'PREF_SERVER = "server_url"' in text and 'PREF_TOKEN = "ws_token"' in text   # the adb extra names


def test_our_own_permission_dialog_is_not_the_app_going_to_the_background():
    """Review findings 22/23: requestPermissions starts the system dialog on top,
    which PAUSES MainActivity; onPause always sent 'emma-native-pause', and the
    page answers that by closing the face scan and ending the call. So the
    request that raised the dialog tore down its own caller -- Allow came too
    late, a refusal left the overlay vanishing with no reason. The pause our
    dialog causes is now swallowed, with the resume that answers it; a stop
    while it is swallowed (Home with the dialog up) is a real departure and
    reaches the page then."""
    text = face()
    handle = code_only(method(text, "private void handleWebPermission(PermissionRequest request)"))
    assert handle.index("dialogPauseExpected = true;") < handle.index("activity.requestPermissions(")
    pause = code_only(method(text, "void onActivityPause()"))
    swallow = pause.index("if (dialogPauseExpected) {")
    assert "pauseSwallowed = true;" in pause[swallow:pause.index("return;", swallow)]
    assert "dialogPauseExpected = false;" in pause[swallow:pause.index("return;", swallow)]
    assert pause.index("return;", swallow) < pause.index("pausePage();")
    # the page event lives in ONE place, reached only past the swallow
    assert text.count("emma-native-pause'))") == 1
    assert "emma-native-pause" in method(text, "private void pausePage()")
    resume = code_only(method(text, "void onActivityResume()"))
    assert resume.index("if (pauseSwallowed) {") < resume.index("return;") < resume.index("webView.onResume()")
    stop = code_only(method(text, "void onActivityStop()"))
    assert "if (pauseSwallowed) {" in stop and "pausePage();" in stop
    # an answer without a pause must not leave the NEXT real pause swallowed
    result = code_only(method(text, "boolean onRequestPermissionsResult(int requestCode, int[] results)"))
    assert "dialogPauseExpected = false;" in result
    # MainActivity forwards the stop
    on_stop = method(main(), "protected void onStop()")
    assert "emmaFace.onActivityStop();" in on_stop and "super.onStop();" in on_stop


def test_a_permission_refused_in_this_process_is_not_asked_for_again():
    """Review finding 24: the ?cam=1 sender retries a failed camera every 10 s
    (six tries per load). Each retry reached requestPermissions again -- the
    dialog came back over the customer face, and once refusal was permanent
    Android still ran the permission activity, a pause every time that hung up
    a live call. A refusal is now remembered for the process and answered at
    once from what Android holds; an INTERRUPTED dialog (empty results) is not
    a refusal."""
    text = face()
    assert "private final Set<String> refusedThisProcess = new HashSet<>();" in text
    askable = code_only(method(text, "private boolean askable(String permission)"))
    assert "return !holds(permission) && !refusedThisProcess.contains(permission);" in askable
    handle = code_only(method(text, "private void handleWebPermission(PermissionRequest request)"))
    assert "if (askable(permission)) ask = true;" in handle
    answer_now = handle.index("if (!ask) {")
    early = handle[answer_now:handle.index("return;", answer_now)]
    assert "grantableNow(request)" in early and "request.deny();" in early
    assert handle.index("return;", answer_now) < handle.index("pendingWebPermissions.add(request);")
    assert handle.index("return;", answer_now) < handle.index("activity.requestPermissions(")
    assert "if (askable(permission)) missing.add(permission);" in handle   # nor inside the combined dialog
    result = code_only(method(text, "boolean onRequestPermissionsResult(int requestCode, int[] results)"))
    remember = result.index("if (results != null && results.length > 0) {")
    # Only the camera's refusal is remembered (review, round 2): the mic is
    # asked for only on a person's tap, and remembering it left Emma deaf until
    # an app restart after one mis-tapped dialog.
    assert ("if (!holds(permission) && android.Manifest.permission.CAMERA.equals(permission)) {"
            in result[remember:])
    assert "refusedThisProcess.add(permission);" in result[remember:]


def test_the_page_is_told_while_the_apps_own_permission_dialog_is_up():
    """The app swallows the pause its own dialog causes (so a call is not hung
    up by it) -- which left the page neither paused nor hidden, and its 8 s
    'camera request hung' timer ended the face scan under a person reading the
    dialog (review, round 2). Now the app says so in words, both ways."""
    text = face()
    handle = code_only(method(text, "private void handleWebPermission(PermissionRequest request)"))
    assert handle.index('notifyPage("emma-native-asking");') < handle.index("activity.requestPermissions(")
    result = code_only(method(text, "boolean onRequestPermissionsResult(int requestCode, int[] results)"))
    assert 'notifyPage("emma-native-asked");' in result
    page = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")
    assert "addEventListener('emma-native-asking', function () { nativeDialog = true; });" in page
    assert "addEventListener('emma-native-asked', function () { nativeDialog = false; });" in page
    assert "if (asking === mine && (nativeDialog || (pausedWhileAsking && document.hidden))) {" in page


def test_origins_are_compared_in_canonical_form():
    """Review finding 26: the trusted origin came from android.net.Uri, which
    keeps the host's case and an explicit port as typed, while
    PermissionRequest.getOrigin()/WebView.getUrl() come from Chromium's
    canonical URL. https://Emma-PC.local:8001 or https://emma.example:443 left
    Emma deaf behind a message blaming a permission Android already held."""
    text = face()
    origin = code_only(method(text, "private static String originOf(Uri uri)"))
    assert "String scheme = uri.getScheme().toLowerCase(Locale.ROOT);" in origin
    assert "String host = uri.getHost().toLowerCase(Locale.ROOT);" in origin
    assert '("https".equals(scheme) && port == 443) || ("http".equals(scheme) && port == 80)' in origin
    assert 'return scheme + "://" + host' in origin
    # every comparison goes through originOf on both sides
    assert "allowedOrigin = origin;" in text and "String origin = originOf(base);" in text
    assert "String origin = originOf(target);" in text
    assert "originOf(Uri.parse(webView.getUrl()))" in text
    assert "return originOf(Uri.parse(normalizeServer(server)));" in text      # the token's origin too


def test_the_face_mic_is_released_while_staff_drive():
    """Switching to the operator screen pauses Emma's WebView so it is not holding
    the mic / streaming customer audio while a staff member drives."""
    text = main()
    show_operator = text[text.index("private void showOperator"):text.index("private void toggleOperator")]
    assert "emmaFace.onActivityPause()" in show_operator
