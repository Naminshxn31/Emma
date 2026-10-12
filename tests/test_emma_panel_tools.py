"""Emma Panel Tools: the throwaway probe that tries to open a locked panel's
hidden Developer options so ADB can be turned on by hand.

Source-contract test (the APK builds off-machine). The two things worth pinning:

  It stays a benign probe. It must not quietly grow into a control app -- no
  dangerous permission, no Accessibility service, no input injection. Its whole
  safety story is that it can only OPEN a settings screen.

  It actually tries the paths that work on a locked ROM: the public
  development-settings action first, then the explicit Settings activities, with
  the package-visibility <queries> that Android 11 needs to launch them.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/emma-panel-tools/app"
MAIN = APP / "src/main/java/com/emma/panel/tools/MainActivity.java"
MANIFEST = APP / "src/main/AndroidManifest.xml"


def test_it_tries_the_paths_that_open_a_hidden_developer_options():
    src = MAIN.read_text(encoding="utf-8")
    # the public action first (works on many ROMs even when the menu is hidden)
    assert "ACTION_APPLICATION_DEVELOPMENT_SETTINGS" in src
    # then explicit Settings activities: the Android-11 name and the old one
    assert "Settings$DevelopmentSettingsDashboardActivity" in src
    assert "com.android.settings.DevelopmentSettings" in src
    # tried in order, falling through on failure
    assert src.index("ACTION_APPLICATION_DEVELOPMENT_SETTINGS") < src.index("DevelopmentSettingsDashboardActivity")


def test_android_11_can_actually_launch_those_settings_components():
    """targetSdk 30+ hides other packages unless declared; without this the
    explicit fallbacks throw on the panel."""
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert "<queries>" in manifest
    assert 'android:name="com.android.settings"' in manifest


def test_it_stays_a_benign_probe_not_a_control_app():
    """No dangerous permission, no Accessibility service, no input injection, no
    screen capture -- its only power is opening a settings screen. Checked on the
    manifest (the capability contract) and on real API usage in the source, NOT
    on words: the comment names the native-app alternative on purpose, and that
    is documentation, not capability. If any of these appear for real, this has
    turned into the control app and belongs in its own package with its own
    review."""
    manifest = MANIFEST.read_text(encoding="utf-8")
    src = MAIN.read_text(encoding="utf-8")
    # the manifest declares no dangerous capability and no service at all
    for forbidden in ("RECORD_AUDIO", "SYSTEM_ALERT_WINDOW", "WRITE_SECURE_SETTINGS",
                      "QUERY_ALL_PACKAGES", "BIND_ACCESSIBILITY_SERVICE",
                      "INJECT_EVENTS", "FOREGROUND_SERVICE", "<service"):
        assert forbidden not in manifest, forbidden
    # only the two harmless permissions
    assert "android.permission.INTERNET" in manifest
    assert manifest.count("uses-permission") == 2
    # the source uses no control API (precise tokens, so a comment cannot trip it)
    for used in ("MediaProjectionManager", "createScreenCaptureIntent",
                 "AccessibilityService", "dispatchGesture(", "GLOBAL_ACTION",
                 "INJECT_EVENTS"):
        assert used not in src, used


def test_it_checks_whether_adb_is_actually_reachable_before_promising_it():
    """A locked panel can OPEN Developer options yet still block debugging (dev
    mode never unlocked, or a Device Owner's DISALLOW_DEBUGGING_FEATURES greys
    the toggle). The probe reads both -- permission-free -- so it routes to the
    native path instead of a dead-end, and does NOT claim success just because
    startActivity did not throw. (Review 2026-09-26.)"""
    src = MAIN.read_text(encoding="utf-8")
    assert "DISALLOW_DEBUGGING_FEATURES" in src
    assert "DEVELOPMENT_SETTINGS_ENABLED" in src
    # the old over-confident "opened it ✓" success claim is gone
    assert "แล้ว ✓" not in src


def test_the_app_is_its_own_package_separate_from_the_others():
    build = (APP / "build.gradle").read_text(encoding="utf-8")
    assert "com.emma.panel.tools" in build
    # not colliding with the voice app or the robot app
    assert "com.emma.ai.voice" not in build
    assert "com.emma.robot" not in build
