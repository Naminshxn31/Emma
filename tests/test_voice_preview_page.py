from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


@pytest.fixture
def client():
    return TestClient(app)


def test_voice_preview_keeps_the_reference_layout_and_accessible_controls():
    html = PAGE.read_text(encoding="utf-8")

    assert "<title>Emma Voice — Preview</title>" in html
    assert "I'm Emma — how can I help?" in html
    assert "â€”" not in html
    assert '<div class="brand">' not in html
    assert "Listening..." in html
    assert 'class="mascot-art"' in html
    assert 'class="reply"' in html
    assert html.index('class="mascot-art"') < html.index('class="reply"')

    # One coherent icon family: inline stroke SVGs, not platform-dependent emoji.
    assert html.count('viewBox="0 0 24 24"') == 3
    assert 'stroke="currentColor"' in html
    assert not any(icon in html for icon in ("💬", "🎤", "✕"))

    for label in ("Open transcript", "Start voice conversation", "End conversation"):
        assert f'aria-label="{label}"' in html


def test_voice_preview_keeps_continuous_3d_motion_around_the_layered_puppet():
    html = PAGE.read_text(encoding="utf-8")
    # The visual renderer is the FIRST <script>; the real voice engine is a
    # second, separate <script> after it. The "no image swapping / no
    # setInterval" invariant below is about the puppet renderer only — the
    # engine legitimately sets img/iframe .src to paint the stage — so scope
    # this to the first block, not the whole tail.
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]

    # Emma is one layered puppet: transforms interpolate between joint targets
    # while the whole rig still floats, turns and tracks the pointer in 3D.
    # The orbit rings were removed on the owner's call — they sat on the wrap,
    # not the dragged rig, so they stayed centred as a ghost circle when Emma
    # moved. The floating puppet keeps every other layer.
    assert 'class="orbit' not in html

    for hook in (
        "perspective: 760px",
        "transform-style: preserve-3d",
        'class="mascot-art"',
        'class="rig-character"',
        'class="puppet-head puppet-part"',
        'class="puppet-body puppet-part"',
        'class="puppet-arm-left puppet-part"',
        'class="puppet-arm-right puppet-part"',
        'class="puppet-wai-hands puppet-part"',
        'class="eyes-smile face-part"',
        'class="mouth-wide face-part"',
        "transition:\n      transform .72s cubic-bezier(.2,.86,.25,1)",
        "@keyframes puppet-blink",
        "@keyframes puppet-wave",
        "rotateX(var(--tilt-x)) rotateY(var(--tilt-y))",
        'class="mascot-turn"',
        "@keyframes ambient-turn",
        "@keyframes float-3d",
        "@keyframes glint",
        "addEventListener('pointermove', aim",
        "requestAnimationFrame(render)",
        'href="assets/emma/emma-puppet-atlas-reference-v2.png"',
        'class="emma-data-orbit"',
        'class="face-display"',
        "--state-accent",
        "--gaze-x",
        "@keyframes emma-data-spin",
        'class="emma-motion-prop prop-search"',
        'class="emma-motion-prop prop-alert"',
        'class="emma-motion-prop prop-heart"',
        'class="emma-motion-prop prop-scan"',
        "@keyframes emma-listen-lean",
        "@keyframes emma-search-head",
        "@keyframes emma-talk-head",
        "@keyframes emma-greeting-wave",
        "@keyframes emma-happy-bounce",
        "@keyframes emma-frustrated-shake",
        "@keyframes emma-thank-bow",
        "@keyframes emma-prop-scan",
        '<clipPath id="emma-mouth-slot">',
        'clip-path="url(#emma-mouth-slot)"',
        ".mouth-smile { display: none; }",
        "transform: translateY(-72px)",
        "transform-origin: 478px 680px",
        "transform-origin: 744px 680px",
        '<svg x="289" y="520" width="220" height="210"',
        'data-emma-left-hand="relaxed"',
        'data-emma-right-hand="relaxed"',
        "scene.dataset.emmaLeftHand = pose.leftHand || pose.hands",
        "scene.dataset.emmaRightHand = pose.rightHand || pose.hands",
    ):
        assert hook in html

    assert 'class="emma-visor"' not in html
    assert 'class="emma-head-shell"' not in html
    assert 'class="emma-face-screen"' not in html
    assert 'class="emma-antenna"' not in html
    assert html.count("mouth: 'none'") >= 8

    assert "setInterval" not in script
    assert ".src =" not in script


def test_voice_preview_exposes_ten_copy_states_without_assigning_numbered_poses():
    html = PAGE.read_text(encoding="utf-8")

    assert 'data-emma-state="listening"' in html
    assert "window.EmmaVoicePreview" in html
    assert "setState: setState" in html
    assert "getState:" in html
    for state in (
        "idle",
        "listening",
        "thinking",
        "speaking",
        "error",
        "greeting",
        "happy",
        "frustrated",
        "grateful",
        "loading",
    ):
        assert f"{state}: {{" in html

    assert "var demoOrder = ['greeting', 'idle', 'listening', 'thinking', 'speaking', 'happy', 'grateful', 'frustrated', 'error', 'loading'];" in html
    assert "setTimeout(nextDemoState, 2600)" in html


def test_voice_preview_maps_live_voice_states_to_original_emma_motion():
    html = PAGE.read_text(encoding="utf-8")

    for mapping in (
        "idle: 'pose-01'", "listening: 'pose-01'", "thinking: 'pose-03'",
        "speaking: 'pose-05'", "error: 'pose-07'", "greeting: 'pose-05'",
        "happy: 'pose-06'", "frustrated: 'pose-08'", "grateful: 'pose-09'",
        "loading: 'pose-10'",
    ):
        assert mapping in html
    assert "scene.dataset.emmaPose !== statePoses[name]" in html
    assert "scene.dataset.emmaState === 'idle' || scene.dataset.emmaState === 'listening'" in html
    assert "--gaze-y" in html


def test_voice_preview_exposes_numbered_joint_poses_and_wai_without_round_eyes():
    html = PAGE.read_text(encoding="utf-8")

    for hook in (
        "poses: Object.freeze(poses.slice())",
        "setPose: setPose",
        "getPose:",
        "startPoseDemo: startPoseDemo",
        "stopPoseDemo: stopPoseDemo",
        "query.get('pose')",
        "query.get('poseDemo')",
        "query.get('autoPose')",
        "startPoseDemo(2200)",
        "emma-pose-change",
    ):
        assert hook in html

    for index in range(1, 11):
        assert f"'pose-{index:02d}': {{" in html

    assert "var rigPoses = {" in html
    assert "poseNodes" not in html
    assert "eyes-open" not in html
    assert "emma-pose-library-transparent-v1.png" not in html
    assert 'class="face-rig"' not in html
    assert 'class="mouth-layer"' not in html


def test_voice_preview_mascot_atlas_is_packaged_with_the_page():
    html = PAGE.read_text(encoding="utf-8")
    asset = ROOT / "client" / "assets" / "emma" / "emma-puppet-atlas-reference-v2.png"

    assert 'href="assets/emma/emma-puppet-atlas-reference-v2.png"' in html
    assert asset.is_file()
    data = asset.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[25] == 6  # RGBA atlas with real transparency around the pieces.


def test_voice_preview_packages_and_drives_the_rive_mascot():
    html = PAGE.read_text(encoding="utf-8")
    rive_asset = ROOT / "client" / "assets" / "emma" / "emma.riv"
    runtime_js = ROOT / "client" / "assets" / "vendor" / "rive" / "rive.js"
    runtime_wasm = ROOT / "client" / "assets" / "vendor" / "rive" / "rive.wasm"

    for asset in (rive_asset, runtime_js, runtime_wasm):
        assert asset.is_file()
        assert asset.stat().st_size > 1024

    assert rive_asset.read_bytes().startswith(b"RIVE")
    for hook in (
        'class="mascot-rive"',
        'src="/assets/vendor/rive/rive.js"',
        "RuntimeLoader.setWasmUrl('/assets/vendor/rive/rive.wasm')",
        "src: '/assets/emma/emma.riv?v=20260922-connect1'",
        "stateMachine: 'EmmaVoice'",
        "stateMachineInputs('EmmaVoice')",
        "setRiveInput('mode'",
        "setRiveInput('speechLevel'",
        "setRiveInput('lookX'",
        "setRiveInput('lookY'",
        "triggerGesture:",
        "scene.classList.add('rive-ready')",
    ):
        assert hook in html


def test_rive_source_embeds_the_approved_layered_emma_artwork():
    source = (ROOT / "client" / "rive" / "emma" / "scene.rml").read_text(encoding="utf-8")
    layers = ROOT / "client" / "rive" / "emma" / "assets"
    expected = (
        "head.png", "body.png", "arm-left.png", "arm-right.png",
        "eye-left.png", "eye-right.png", "mouth-smile.png",
        "mouth-open.png", "ring.png",
    )

    for name in expected:
        asset = layers / name
        assert f'ImageAsset file="assets/{name}"' in source
        assert asset.is_file()
        assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    assert 'originX="0.78" originY="0.17" assetId="0:1102"' in source
    assert 'originX="0.22" originY="0.17" assetId="0:1103"' in source
    assert (ROOT / "client" / "assets" / "emma" / "emma.riv").stat().st_size > 500_000


def test_rive_idle_matches_the_approved_front_pose_without_extra_overlays():
    source = (ROOT / "client" / "rive" / "emma" / "scene.rml").read_text(encoding="utf-8")

    assert '<Shape y="6" opacity="0.78" name="Core Glow" id="0:54">' in source
    assert 'colorValue="CCFFFFFF" position="0.48"' in source
    assert 'colorValue="99EAFDFF"' not in source
    assert 'opacity="0" assetId="0:1106" name="Smile"' in source
    assert '<Shape x="360" y="274" opacity="0" name="Aura" id="0:80">' in source

    # Every artwork layer is cropped from one atlas that already has Emma
    # assembled, so they must all render at the same scale. Scaling them apart
    # (head .68, body .72, arms .60, ring .52) is what drifted the proportions:
    # the arms ended up 20% too close to the body and the ring 23% too small.
    for asset, name in (
        ("0:1100", "Head Artwork"),
        ("0:1101", "Body Artwork"),
        ("0:1102", "Left Arm Artwork"),
        ("0:1103", "Right Arm Artwork"),
        ("0:1108", "Ground Ring"),
    ):
        layer = source.split(f'assetId="{asset}"', 1)[0].rsplit("<Image", 1)[1]
        assert 'scaleX="0.68" scaleY="0.68"' in layer, name

    # Node positions are the atlas coordinates mapped into the artboard with
    # that same scale: artboard_x = 136.06 + atlas_x * 0.68, artboard_y =
    # 77.48 + atlas_y * 0.68, with the rig at (360, 390).
    assert '<Node x="-11" y="-95" name="Head" id="0:20">' in source
    assert '<Node x="2" y="182" name="Torso" id="0:50">' in source
    assert '<Node x="-115" y="109" rotation="-0.18" name="Left Arm" id="0:60">' in source
    assert '<Node x="119" y="109" rotation="0.18" name="Right Arm" id="0:70">' in source
    assert '<Image x="362" y="705" scaleX="0.68" scaleY="0.68"' in source
    assert 'originX="0.78" originY="0.17" assetId="0:1102"' in source
    assert 'originX="0.22" originY="0.17" assetId="0:1103"' in source


def test_rive_blink_compresses_the_chrome_eyes_without_white_discs():
    source = (ROOT / "client" / "rive" / "emma" / "scene.rml").read_text(encoding="utf-8")
    blink = source.split('name="blink" id="0:270">', 1)[1].split("</LinearAnimation>", 1)[0]

    assert 'objectId="0:34"' in blink
    assert 'objectId="0:35"' in blink
    assert 'objectId="0:36"' not in blink
    assert 'objectId="0:37"' not in blink
    assert '<KeyFrameDouble value="0.08" frame="156"/>' in blink
    assert '<Shape x="-87" y="-17" scaleY="0.02" opacity="0" name="Left Eyelid"' in source
    assert '<Shape x="87" y="-17" scaleY="0.02" opacity="0" name="Right Eyelid"' in source


def test_connecting_and_listening_keep_the_arms_in_the_approved_rest_pose():
    html = PAGE.read_text(encoding="utf-8")
    source = (ROOT / "client" / "rive" / "emma" / "scene.rml").read_text(encoding="utf-8")
    listening = source.split('name="listening" id="0:220">', 1)[1].split("</LinearAnimation>", 1)[0]

    assert "listening: 2, thinking: 3, loading: 0" in html
    assert 'objectId="0:60"' in listening
    assert 'value="-0.18" frame="72"' in listening
    assert 'objectId="0:70"' in listening
    assert 'value="0.18" frame="72"' in listening
    assert 'value="-0.45"' not in listening
    assert 'value="0.45"' not in listening
    assert 'body[data-emma-state="idle"] .topbar { opacity: .78; }' in html
    assert "'Tap to talk': appShell ? 'เริ่มสนทนาด้วยเสียง'" in html
    assert html.count("micBtn.setAttribute('aria-pressed', 'false')") >= 4

    # The eyes are the one exception to the shared scale: the atlas eye cell is
    # flatter than the eyes in the approved render, so scaleY is measured off
    # that render instead. They sit just above the head centre, not on it.
    assert '<Image x="-87" y="-17" scaleX="0.68" scaleY="0.88"' in source
    assert '<Image x="87" y="-17" scaleX="0.68" scaleY="0.88"' in source
    assert source.count('<Ellipse width="102" height="76" name="Path"/>') == 2
    assert source.count('y="-17" scaleY="0.02" opacity="0"') == 2

    # Lip-sync overlays sit over the mouth cavity of the head artwork, measured
    # at (299, 450) inside head.png - not 71px below it.
    assert source.count('<Image x="4" y="119" scaleX="0.68" scaleY="0.68"') == 2

    mouth_closed = source.split('name="mouthClosed"', 1)[1].split(
        'name="mouthOpen"', 1
    )[0]
    assert 'objectId="0:42"' in mouth_closed
    assert '<KeyFrameDouble value="0"' in mouth_closed

    ambient_glow = source.split('name="ambientGlow"', 1)[1]
    core_glow = ambient_glow.split('objectId="0:54"', 1)[1].split('</KeyedObject>', 1)[0]
    core_opacity = core_glow.split('propertyKey="18"', 1)[1].split('</KeyedProperty>', 1)[0]
    assert '<KeyFrameDouble value="0.78"' in core_opacity
    assert 'value="0.78" frame="100"' in core_opacity

    aura = ambient_glow.split('objectId="0:80"', 1)[1].split('</KeyedObject>', 1)[0]
    aura_opacity = aura.split('propertyKey="18"', 1)[1].split('</KeyedProperty>', 1)[0]
    assert '<KeyFrameDouble value="0"' in aura_opacity
    assert 'value="0" frame="100"' in aura_opacity


def test_preview_rive_assets_are_served(client):
    riv = client.get("/assets/emma/emma.riv")
    runtime_js = client.get("/assets/vendor/rive/rive.js")
    runtime_wasm = client.get("/assets/vendor/rive/rive.wasm")

    assert riv.status_code == 200
    assert riv.content.startswith(b"RIVE")
    assert runtime_js.status_code == 200
    assert runtime_js.content.startswith(b"(function webpackUniversalModuleDefinition")
    assert runtime_wasm.status_code == 200
    assert runtime_wasm.content.startswith(b"\x00asm")


def test_voice_preview_packages_only_the_rive_mascot_renderer():
    html = PAGE.read_text(encoding="utf-8")

    for removed_hook in (
        'class="mascot-three"',
        'data-emma-three',
        'emma-three.js',
        "query.get('renderer') === '3d'",
        'three-ready',
        'emma-three-error',
    ):
        assert removed_hook not in html

    assert not (ROOT / "client" / "assets" / "emma" / "emma.glb").exists()
    assert not (ROOT / "client" / "assets" / "emma" / "emma-three.js").exists()
    assert not (ROOT / "client" / "assets" / "vendor" / "three").exists()
    assert not (ROOT / "scripts" / "build-emma-glb.mjs").exists()


def test_voice_preview_mascot_can_be_moved_and_resized_accessibly():
    html = PAGE.read_text(encoding="utf-8")

    for hook in (
        "--user-x: 0px",
        "--user-scale: 1",
        "touch-action: none",
        "pinch or use mouse wheel to resize",
        "setMascotScale",
        "moveMascot",
        "resetMascotTransform",
        "setScale: setMascotScale",
        "moveTo: moveMascot",
        "resetTransform: resetMascotTransform",
        "getTransform:",
        "emma-transform-change",
        "event.key === 'Home'",
    ):
        assert hook in html


def test_drag_moves_the_mascot_hit_area_and_keeps_a_regrabbable_edge_visible():
    html = PAGE.read_text(encoding="utf-8")
    wrap_css = html.split(".mascot-wrap {", 1)[1].split("}", 1)[0]
    rig_css = html.split(".mascot-rig {", 1)[1].split("}", 1)[0]
    rive_css = html.split(".mascot-rive {", 1)[1].split("}", 1)[0]

    assert "transform: translate(var(--user-x), var(--user-y)) scale(var(--user-scale))" in wrap_css
    assert "translate(var(--user-x)" not in rig_css
    assert "translate(var(--user-x)" not in rive_css
    assert "function mascotLayoutCentre()" in html
    assert "function constrainMascotPosition(x, y)" in html
    assert "var grab = Math.min(96, Math.max(48" in html
    assert ".mascot-wrap.is-dragging { cursor: grabbing; transition: none; }" in html


def test_transcript_drawer_can_be_closed_with_button_toggle_or_escape():
    html = PAGE.read_text(encoding="utf-8")

    assert 'class="vp-drawer-close"' in html
    assert 'aria-label="ปิดกล่องข้อความ"' in html
    assert "function setTranscriptDrawerOpen(open)" in html
    assert "setTranscriptDrawerOpen(!drawer || drawer.hidden)" in html
    assert "event.key !== 'Escape'" in html
    assert "msgBtn.setAttribute('aria-expanded'" in html


def test_preview_is_served_at_its_own_url_not_over_slash(client):
    """The new design lives at /preview. `/` stays index.html untouched —
    the whole point of building it beside the production page rather than on
    top of it, while the showroom keeps using `/`."""
    resp = client.get("/preview")
    assert resp.status_code == 200
    assert 'class="mascot-art"' in resp.text
    assert "no-cache" in resp.headers.get("cache-control", "")

    home = client.get("/")
    assert home.status_code == 200
    assert home.text != resp.text          # two different pages
    assert 'class="mascot-art"' not in home.text


def test_preview_mascot_atlas_is_served(client):
    """The puppet PNG is referenced with a relative path, so it must resolve
    to a real route — nothing serves client/assets/ otherwise."""
    resp = client.get("/assets/emma/emma-puppet-atlas-reference-v2.png")
    assert resp.status_code == 200
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


def _engine_code() -> str:
    """The real-engine <script>, prose stripped, so forbidden-word scans read
    what it does, not what its comments say it must not do."""
    import re

    html = PAGE.read_text(encoding="utf-8")
    engine = html.split("real voice engine", 1)[1]
    engine = re.sub(r"<!--.*?-->", "", engine, flags=re.S)
    return "\n".join(
        line for line in engine.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "<!--"))
    )


def test_preview_runs_a_real_speech_to_speech_call_through_the_renderer():
    """It is a live call, not a mock: it opens /ws with the token guard
    intact, streams the mic through the same debugged `cap` worklet, and
    drives the mascot only through the public EmmaVoicePreview API."""
    code = _engine_code()

    assert "new WebSocket(" in code
    assert "/ws?voice=" in code
    assert "q.get('token')" in code            # the LAN guard still applies
    assert "getUserMedia" in code
    assert "registerProcessor('cap', Cap)" in code   # the debugged pipeline, copied
    # The renderer stays a renderer: the engine only ever calls the public API.
    assert "Emma.setState" in code
    assert "Emma.setMouthLevel" in code
    # Half-duplex gating survives the port — Emma must not hear herself.
    assert "if (halfDuplex && playing) return;" in code


def test_preview_wipes_each_guests_transcript_on_every_exit_and_new_call():
    code = _engine_code()
    clear_fn = code[code.index("function clearTranscript()") :]
    clear_fn = clear_fn[:clear_fn.index("\n    }")]
    start = code[code.index("function startCall()") : code.index("function endCall()")]
    end = code[code.index("function endCall()") : code.index("function toggleMute()")]
    close = code[code.index("ws.onclose = function") : code.index("function endCall()")]

    assert "transcript = []" in clear_fn
    assert "setTranscriptDrawerOpen(false)" in clear_fn
    assert "curUser = ''" in clear_fn
    assert "userLine = null" in clear_fn
    assert "clearTranscript();" in start
    assert "clearTranscript();" in end
    assert "clearTranscript();" in close


def test_preview_labels_asr_honestly_and_keeps_one_user_line_per_utterance():
    code = _engine_code()
    html = PAGE.read_text(encoding="utf-8")

    assert "ข้อความฝั่งคุณเป็นคำถอดเสียงอัตโนมัติ" in html
    assert "อาจต่างจากสิ่งที่ Emma เข้าใจจากเสียง" in html
    assert "var curUser = ''" in code
    assert "var userLine = null" in code
    user_branch = code.split("case 'user_transcript':", 1)[1].split("break;", 1)[0]
    assert "curUser += evt.text || ''" in user_branch
    assert "userLine.text = curUser" in user_branch
    speech_branch = code.split("case 'speech_started':", 1)[1].split("break;", 1)[0]
    assert "curUser = ''" in speech_branch
    assert "userLine = null" in speech_branch


def test_preview_closes_the_provider_session_when_the_microphone_cannot_open():
    code = _engine_code()
    failure = code[code.index("function stopFailedCall()") : code.index("function armAudioUnlock")]
    mic = code[code.index("async function startMic()") : code.index("function onMessage")]

    assert "ws = null" in failure
    assert "inCall = false" in failure
    assert "releaseCallHardware();" in failure
    assert "failedSocket.close()" in failure
    assert "if (!micStream)" in mic and "stopFailedCall();" in mic


def test_preview_does_not_fork_the_production_pages_dom():
    """The engine reuses index.html's audio code, never its DOM. Reaching for
    index.html's element ids here would be the start of two copies drifting —
    the exact thing this repo refuses to do (one bug, fixed once)."""
    code = _engine_code()
    for stray in ("'callOrb'", "'stateLabel'", "'pickerScreen'", "'callScreen'", "'levelBar'", "'convo'"):
        assert stray not in code


def test_preview_shows_emmas_displays_on_a_centre_stage_and_corners_the_mascot():
    """Emma's on-screen tools (unit card, floor plan, embed, slide) paint on a
    centre stage; the mascot only steps to the corner while something is
    staged (owner: centred by default, aside when an image replaces her). No
    price is ever pulled onto this public screen."""
    code = _engine_code()
    html = PAGE.read_text(encoding="utf-8")

    assert "case 'tool_result':" in code
    for fn in ("function showUnit", "function showPlan", "function showFrame", "function showSlide"):
        assert fn in code
    # The corner move is a state, not the default: toggled on/off, driven by CSS.
    assert "classList.add('emma-staging')" in code
    assert "classList.remove('emma-staging')" in code
    assert "body.emma-staging .mascot-wrap" in html
    # Never a price on the public screen, and no tap-to-reveal port that could.
    assert "ราคา — สอบถามฝ่ายขาย" in code
    assert "unit_price" not in code
