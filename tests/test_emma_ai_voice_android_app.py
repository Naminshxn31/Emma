from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "emma-ai-voice"
MAIN = APP / "app" / "src" / "main" / "java" / "com" / "emma" / "ai" / "voice" / "MainActivity.java"
MANIFEST = APP / "app" / "src" / "main" / "AndroidManifest.xml"
NETWORK_RELEASE = APP / "app" / "src" / "main" / "res" / "xml" / "network_security_config.xml"
NETWORK_DEBUG = APP / "app" / "src" / "main" / "res" / "xml" / "network_security_config_debug.xml"
PREVIEW = ROOT / "client" / "voice-preview.html"


def test_voice_app_is_a_separate_microphone_only_android_package():
    manifest = MANIFEST.read_text(encoding="utf-8")
    gradle = (APP / "app" / "build.gradle").read_text(encoding="utf-8")

    assert "com.emma.ai.voice" in gradle
    assert 'android:label="Emma AI Voice"' in manifest
    assert "android.permission.RECORD_AUDIO" in manifest
    assert "android.permission.MODIFY_AUDIO_SETTINGS" in manifest
    assert "android.permission.INTERNET" in manifest
    assert "android.permission.CAMERA" not in manifest
    assert "BLUETOOTH" not in manifest


def test_voice_app_reuses_the_real_preview_and_does_not_embed_model_keys():
    source = MAIN.read_text(encoding="utf-8")
    all_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in APP.rglob("*") if path.is_file() and path.suffix not in {".jar", ".crt"}
    )

    assert 'appendQueryParameter("app", "1")' in source
    assert 'appendQueryParameter("autoPose", "0")' in source
    assert 'appendQueryParameter("token", token)' in source
    assert "gemini_api_key" not in all_text.lower()
    assert "openai_api_key" not in all_text.lower()
    assert "lan-key.pem" not in all_text


def test_voice_app_grants_only_same_origin_audio_capture():
    source = MAIN.read_text(encoding="utf-8")

    assert "RESOURCE_AUDIO_CAPTURE" in source
    assert "RESOURCE_VIDEO_CAPTURE" not in source
    assert "originOf(request.getOrigin()).equals(allowedOrigin)" in source
    assert "request.deny()" in source
    assert "requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}" in source


def test_release_ignores_external_provisioning_intents():
    source = MAIN.read_text(encoding="utf-8")
    consume = source[source.index("private boolean consumeLaunchConfiguration") :]
    consume = consume[:consume.index("private View buildUi")]

    assert "if (!BuildConfig.DEBUG)" in consume
    assert "intent.removeExtra(PREF_SERVER)" in consume
    assert "intent.removeExtra(PREF_TOKEN)" in consume
    assert "return false" in consume


def test_release_requires_https_while_debug_http_is_loopback_only():
    source = MAIN.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    gradle = (APP / "app" / "build.gradle").read_text(encoding="utf-8")
    release_network = NETWORK_RELEASE.read_text(encoding="utf-8")
    debug_network = NETWORK_DEBUG.read_text(encoding="utf-8")

    assert 'android:usesCleartextTraffic="${usesCleartextTraffic}"' in manifest
    assert "usesCleartextTraffic: 'false'" in gradle
    assert "usesCleartextTraffic: 'true'" in gradle
    assert 'cleartextTrafficPermitted="false"' in release_network
    assert 'cleartextTrafficPermitted="true"' in debug_network
    assert 'if ("https".equals(uri.getScheme())) return true' in source
    assert 'BuildConfig.DEBUG && "http".equals(uri.getScheme())' in source
    assert '"10.0.2.2".equals(host)' in source


def test_voice_app_releases_the_live_call_when_backgrounded():
    source = MAIN.read_text(encoding="utf-8")
    preview = PREVIEW.read_text(encoding="utf-8")

    assert "emma-native-pause" in source
    assert "addEventListener('emma-native-pause'" in preview
    assert "if (inCall) endCall()" in preview
    assert "addEventListener('pagehide'" in preview


def test_voice_app_hides_android_navigation_without_hiding_status():
    source = MAIN.read_text(encoding="utf-8")

    assert "SYSTEM_UI_FLAG_IMMERSIVE_STICKY" in source
    assert "SYSTEM_UI_FLAG_HIDE_NAVIGATION" in source
    assert "SYSTEM_UI_FLAG_FULLSCREEN" not in source


def test_voice_app_uses_media_volume_and_boosts_only_its_reply_audio():
    source = MAIN.read_text(encoding="utf-8")
    preview = PREVIEW.read_text(encoding="utf-8")

    assert "setVolumeControlStream(AudioManager.STREAM_MUSIC)" in source
    assert "if (!appShell) return ctx.destination" in preview
    assert "playbackGainNode.gain.value = 1.75" in preview
    assert "createDynamicsCompressor()" in preview
    assert "src.connect(playbackOutput(audioCtx))" in preview
    assert "setMicLabel('Listening')" in preview
    assert "setMicLabel('Mic unavailable')" in preview
    assert "ระบบเสียงยังไม่พร้อมหรือมีแอปอื่นกำลังใช้งาน" in preview


def test_preview_has_the_android_home_layout_without_forking_the_engine():
    preview = PREVIEW.read_text(encoding="utf-8")

    assert "document.documentElement.classList.add('app-shell')" in preview
    assert "html.app-shell .control.mic .control-icon" in preview
    assert "'กดเพื่อพูด'" in preview
    assert "'ข้อความ'" in preview
    assert "'จบ'" in preview
    assert preview.count("new WebSocket(") == 1
    assert "/ws?voice=" in preview
