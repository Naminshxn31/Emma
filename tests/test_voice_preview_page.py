import re
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


def _puppet_renderer_code() -> str:
    """The SVG-puppet renderer block, sliced by markers that only it contains.

    This used to be `html.split("<script>", 1)[1]`, which returned 127
    characters — the app-shell detector at the top of the page, not the
    renderer at all. The two forbidden-word scans below therefore inspected
    nothing while reporting green (the file already contained five `.src =`).
    The caller asserts the block's size, so a future mis-target fails loudly
    instead of quietly shrinking back to a comment.
    """
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("var scene = document.querySelector('.mascot-wrap');", 1)[1]
    return block.split("real voice engine", 1)[0]


def test_voice_preview_keeps_continuous_3d_motion_around_the_layered_puppet():
    html = PAGE.read_text(encoding="utf-8")
    script = _puppet_renderer_code()
    # If the slice ever mis-targets again it collapses to a few hundred
    # characters; the real block is tens of thousands.
    assert len(script) > 10_000
    # Positive control: this really is the renderer, and it really does drive
    # the rig from a rAF loop rather than a timer.
    assert "function setPose(name, fromDemo)" in script
    assert "requestAnimationFrame(render)" in script

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


def test_voice_preview_packages_and_drives_the_approved_3d_mascot():
    """The mascot on the call screen is the GLB the owner signed off
    (2026-09-23), rendered with the lab viewer's approved look. Every hook
    here fails if the renderer is removed, mis-wired, or pointed at a path
    that does not exist on a fresh clone."""
    html = PAGE.read_text(encoding="utf-8")
    lab = ROOT / "client" / "assets" / "emma-3d-lab"
    # The file the page LOADS, which since 2026-09-25 is the 68k reduction,
    # not the 162k emma.glb it was sculpted from. Anchoring this on the
    # loaded name is the point: emma.glb still sits beside it as the
    # fallback, so a check against that one stays green on a page whose
    # model 404s.
    glb = lab / "emma-light-68k.glb"
    three = lab / "vendor" / "build" / "three.module.js"
    three_core = lab / "vendor" / "build" / "three.core.js"
    loader = lab / "vendor" / "examples" / "jsm" / "loaders" / "GLTFLoader.js"
    renderer = lab / "mascot.js"

    assert glb.is_file()
    assert glb.read_bytes()[:4] == b"glTF"
    assert glb.stat().st_size > 1_000_000
    for module in (three, three_core, loader, renderer):
        assert module.is_file(), module
        assert module.stat().st_size > 1024

    # The page: an importmap with ABSOLUTE paths (a relative "./vendor/..."
    # would resolve against /preview and 404), then the module renderer.
    assert '<script type="importmap">' in html
    assert '"three": "/assets/emma-3d-lab/vendor/build/three.module.js"' in html
    assert '"three/addons/": "/assets/emma-3d-lab/vendor/examples/jsm/"' in html
    assert html.index('<script type="importmap">') < html.index('type="module"')
    assert '<script type="module" src="/assets/emma-3d-lab/mascot.js?v=' in html
    assert 'class="mascot-3d"' in html
    assert "data-emma-3d" in html

    # The renderer itself: the real model, the real loader, a real loop.
    # Both checks are anchored on purpose. A bare "GLTFLoader" is satisfied by
    # the import line, and a bare "renderer.setAnimationLoop(" is satisfied by
    # the pagehide teardown's setAnimationLoop(null) -- both stay green on a
    # mascot that never loads or never draws a second frame. Measured: breaking
    # the call site and disabling the startup loop each left this file at
    # 27 passed before the anchors went in.
    code = renderer.read_text(encoding="utf-8")
    assert "new GLTFLoader().loadAsync('/assets/emma-3d-lab/emma-light-68k.glb')" in code
    startup = code.split("const clock = new THREE.Clock();", 1)[1]
    startup = startup.split("addEventListener('pagehide'", 1)[0]
    assert "renderer.setAnimationLoop(" in startup


def test_the_shipped_model_still_carries_every_name_the_renderer_looks_up():
    """mascot.js reaches into the GLB BY NAME -- the chest tap target is every
    node called 'Chest__...', the halo is the material 'White light', and the
    idle/look motion is six named bones. None of that is a hard failure at
    runtime: a missing name gives a mascot that renders perfectly and simply
    has no mode button, which is exactly what happened on 2026-09-24 when
    GLTFLoader's sanitising turned 'Chest / luminous ring' into
    'Chest__luminous_ring' and the code was still matching 'Chest /'.

    This is read from the GLB the page actually loads, so swapping in another
    build (the 68k reduction did exactly that on 2026-09-25) cannot quietly
    drop one. Note the loader's sanitising: node names keep their spaces and
    slashes in the FILE, and become 'Chest__luminous_ring' in the scene graph,
    so the check is written against the file's spelling."""
    import json
    import struct

    lab = ROOT / "client" / "assets" / "emma-3d-lab"
    loaded = re.search(r"loadAsync\('/assets/emma-3d-lab/([^']+)'\)",
                       (lab / "mascot.js").read_text(encoding="utf-8"))
    assert loaded, "the renderer's load call moved -- this test cannot find the model"
    raw = (lab / loaded.group(1)).read_bytes()
    assert raw[:4] == b"glTF"
    chunk_len = struct.unpack("<I", raw[12:16])[0]
    gltf = json.loads(raw[20:20 + chunk_len].decode("utf-8"))

    names = {n.get("name", "") for n in gltf["nodes"]}
    chest = {n for n in names if n.startswith("Chest /")}
    assert len(chest) >= 3, f"the chest button lost its parts: {sorted(chest)}"
    assert {"Root", "Body", "Neck", "Head", "Arm.L", "Arm.R"} <= names
    assert any(m.get("name") == "White light" for m in gltf["materials"]),         "the glow pass selects this material by name; without it the halo is gone"
    assert len(gltf.get("animations", [])) >= 2, "idle + look"


def test_voice_preview_3d_mascot_keeps_the_approved_look():
    """CLAUDE.md: reuse the lab's look, do not invent new lighting. These are
    the values measured band-by-band against the owner's reference sheet — if
    someone reaches for default tone mapping or re-lights the studio, the
    approved render changes and this goes red."""
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    viewer = (ROOT / "client" / "assets" / "emma-3d-lab" / "viewer.js").read_text(encoding="utf-8")

    for approved in (
        "toneMappingExposure = 1.0",
        "THREE.VSMShadowMap",
        "cardRing(45, 8.5, 8, [5.2, 3.6], 1.2, { 7: 8, 6: 4, 0: 4, 4: 1.5 })",
        "cardRing(3, 9, 4, [14, 5], .16, { 0: .22 })",
        "cardRing(-25, 6.5, 8, [5.4, 2.6], .40, { 0: .55, 1: .5, 7: .5 })",
        "pmrem.fromScene(studio, .04, .1, 100, { size: 512 })",
        "scene.environmentIntensity = 1.0",
        "DirectionalLight(0xfffdf9, 2.2)",
        "DirectionalLight(0xf4f7ff, .15)",
        "DirectionalLight(0xffffff, .8)",
        "'Polished chrome': 2.2",
        "'Pearl enamel': .45",
        # the two-scale selective glow, with its approved weights and steps
        "texture2D(glow,vUv).rgb*.42",
        "texture2D(glowWide,vUv).rgb*.08",
        ".55 / glowWidth",
        "2.5 / glowWidth",
    ):
        assert approved in code, approved

    # The ACES grade must be the renderer's SETUP value, not just a string in
    # the file: renderScene() writes the identical line every frame to restore
    # tone mapping after the NoToneMapping HDR pass, so both a bare substring
    # check AND an anchored one pass on a renderer that was set up with
    # NoToneMapping. Measured here in the setup block only.
    setup = code.split("const scene = new THREE.Scene();", 1)[1].split("const pmrem", 1)[0]
    assert "renderer.toneMapping = THREE.ACESFilmicToneMapping;" in setup
    assert "NoToneMapping" not in setup
    # ...and the glow chain still flips to NoToneMapping for the HDR pass and
    # back, which is what makes the tonemap apply exactly once, in the composite.
    frame = code.split("function renderScene()", 1)[1].split(chr(10) + "}", 1)[0]
    assert "renderer.toneMapping = THREE.NoToneMapping;" in frame
    assert "renderer.toneMapping = THREE.ACESFilmicToneMapping;" in frame

    # The lab's own numbers, so a change there is caught here too.
    assert "cardRing(45, 8.5, 8, [5.2,3.6], 1.2, {7:8, 6:4, 0:4, 4:1.5})" in viewer
    assert "THREE.ACESFilmicToneMapping" in viewer

    # The ONE deliberate deviation: the lab composites an opaque dark studio
    # backdrop and writes alpha 1.0. On /preview the mascot floats over the
    # page's own gradient and steps into the staging corner, so the backdrop
    # is dropped and alpha is carried. Recorded on purpose — if it comes
    # back, the corner grows a dark tile.
    shader = code.split("fragmentShader: `uniform sampler2D source;uniform sampler2D glow;", 1)[1]
    shader = shader.split("`", 1)[0]
    assert "backdrop" not in shader          # prose about it is fine; the term is not
    assert "gl_FragColor=vec4(color,clamp(sampleColor.a+halo,0.0,1.0))" in shader
    assert "vec3 backdrop=mix(" in viewer          # still there in the lab


def test_voice_preview_3d_mascot_moves_in_every_voice_state():
    """The GLB ships two clips ('idle', 'look') and six bones, so the ten
    voice states cannot be one clip each — they are procedural offsets on the
    bones over the idle loop. Nothing may look frozen: the float and the
    breathe run in every state, including those with no motion of their own."""
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")

    table = code.split("const STATE_MOTION = {", 1)[1].split("\n};", 1)[0]
    # Exactly the page's ten state names — no new vocabulary.
    for state in ("idle", "listening", "thinking", "speaking", "error",
                  "greeting", "happy", "frustrated", "grateful", "loading"):
        rows = [line for line in table.splitlines() if line.strip().startswith(state + ":")]
        assert len(rows) == 1, state
        entry = rows[0]
        assert "clip:" in entry and "bob:" in entry, state
        # Every state carries a non-zero bob, so none of them stands still.
        bob = entry.split("bob:", 1)[1].split(",", 1)[0].strip()
        assert float(bob) > 0, state

    # Only clips that exist in the GLB are ever asked for.
    assert "clip: 'idle'" in table and "clip: 'look'" in table
    assert "'wave'" not in code and "'grip'" not in code

    # The always-on layer, and the six bones it moves.
    assert "const breathe = Math.sin(phase * .52 * Math.PI * 2) * .006;" in code
    for bone in ("'Root'", "'Body'", "'Neck'", "'Head'", "'Arm.L'", "'Arm.R'"):
        assert "boneByName(" + bone + ")" in code, bone

    # Driven by what the page already sets — not a second state channel.
    assert "attributeFilter: ['data-emma-state']" in code
    # The listener, not a mention of it: the prose comment two lines above the
    # real wiring names the event, so a bare "'emma-mouth-level'" survives a
    # typo in addEventListener and ships a speaking bob that ignores the voice.
    assert "wrap.addEventListener('emma-mouth-level'" in code

    # The mixer samples a cloned hierarchy (the lab's fix for stale cached
    # tracks); blending on the mixer's own bones snaps when a static track
    # resumes.
    assert "model.clone(true)" in code
    assert "new THREE.AnimationMixer(poseSource)" in code


def test_voice_preview_3d_mascot_keeps_the_face_on_the_front_of_the_head():
    """The face is a flat decal on a sphere, so yaw past a point rotates it off
    the front: one eye is cut by the silhouette and the rest of the head reads
    as glossy black back-of-head. Measured in real Chrome on /preview in the
    settled loop (face centroid offset from head centre, in head half-widths):
    thinking .199, loading .208, greeting .212 -- against .056-.086 for every
    other state. Exactly the three states on the 'look' clip, which is authored
    as a yaw sweep to +/-0.6 rad on the same Neck bone the procedural `turn` is
    added to. After capping: .066 / .086 / .089, all inside the healthy band,
    and those three still carry the WIDEST motion range of any state.

    thinking is where Emma sits during every lookup, so this is what a customer
    standing at the screen sees routinely."""
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    table = code.split("const STATE_MOTION = {", 1)[1].split("\n};", 1)[0]

    def field(state, name):
        row = [l for l in table.splitlines() if l.strip().startswith(state + ":")][0]
        return float(row.split(name + ":", 1)[1].split(",", 1)[0].strip())

    # Cutting `turn` alone cannot fix this: the clip's own sweep is the larger
    # term. Both halves are asserted, because either one alone ships the bug.
    for state in ("thinking", "loading", "greeting"):
        assert field(state, "clip" + "Yaw") <= .40, state
        assert field(state, "turn") <= .12, state
    # ...and the cap must actually be APPLIED to the sampled clip pose. Without
    # this line clipYaw is a number in a table that changes nothing.
    assert "neck.rotation.y *= m.clipYaw" in code

    # The states that were already correct keep their yaw: this is a cap on the
    # three that clip, not a flattening of the whole mascot.
    for state in ("idle", "listening", "speaking", "happy"):
        assert field(state, "clip" + "Yaw") == 1, state

    # Nothing froze to buy the fix -- the three capped states must still move,
    # and the brief's rule is to spend the lost yaw on lean/armLift/bobRate,
    # which cannot rotate the decal away from camera.
    for state in ("thinking", "loading", "greeting"):
        assert field(state, "bob") > 0, state
        assert field(state, "bobRate") > 0, state
        assert field(state, "turnRate") > 0, state
        assert abs(field(state, "lean")) > 0, state
        assert field(state, "armLift") > 0, state


def test_voice_preview_3d_mascot_fails_back_to_the_svg_puppet():
    """Removing Rive removed a renderer, not the fallback. If WebGL or the GLB
    fails, the ready class is never added, the inline SVG puppet stays visible
    and the page keeps working — one warning, no broken canvas, no spam."""
    html = PAGE.read_text(encoding="utf-8")
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")

    # The crossfade pair: nothing else hides the puppet, and no test used to
    # assert it — without it the model and the puppet render stacked, green.
    assert ".mascot-wrap.emma-3d-ready .mascot-3d { opacity: 1; }" in html
    hidden = html.split(".mascot-wrap.emma-3d-ready .mascot-rig {", 1)[1].split("}", 1)[0]
    assert "opacity: 0" in hidden
    assert "visibility: hidden" in hidden

    # The class is added only after BOTH the renderer and the model succeed —
    # i.e. inside the load block, after the model is in the scene.
    ready = code.split("scene.add(model);", 1)[1].split("} catch", 1)[0]
    assert "wrap.classList.add('emma-3d-ready')" in ready
    assert code.count("classList.add('emma-3d-ready')") == 1

    # WebGLRenderer construction is guarded. viewer.js leaves it bare, so a
    # machine with no GL context dies before its own try/catch is reached.
    guard = code.split("let renderer;", 1)[1].split("const scene", 1)[0]
    assert "try {" in guard and "catch" in guard
    assert "new THREE.WebGLRenderer(" in guard
    assert guard.count("console.warn") == 1

    # The SVG puppet is still mounted and still driven, so the fallback is a
    # real mascot rather than an empty box.
    assert 'class="mascot-rig"' in html
    assert 'class="mascot-art"' in html


def test_voice_preview_has_retired_the_rive_mascot():
    """Rive is gone from the page: markup hooks, runtime tag, state machine,
    inputs and ready class. Its files may linger on disk unreferenced, but
    nothing may load them."""
    html = PAGE.read_text(encoding="utf-8")

    for gone in (
        'class="mascot-rive"',
        "data-emma-rive",
        "/assets/vendor/rive/rive.js",
        "/assets/vendor/rive/rive.wasm",
        "emma.riv",
        "stateMachine: 'EmmaVoice'",
        "setRiveInput",
        "riveInputs",
        "riveModeValues",
        "rive-ready",
        "triggerGesture",
    ):
        assert gone not in html, gone


# The three scene.rml tests that stood here (artwork layers, the idle front
# pose, the blink) were retired on 2026-09-23 with the Rive renderer. They read
# client/rive/emma/scene.rml and asserted nothing about the page, so after the
# swap they would have kept passing while describing artwork /preview no longer
# renders — the "green because it tests a retired artefact" shape this repo
# has paid for before. The 3D renderer's equivalents are
# test_voice_preview_packages_and_drives_the_approved_3d_mascot and
# test_voice_preview_3d_mascot_keeps_the_approved_look.


def test_connecting_and_listening_keep_the_page_chrome_in_its_approved_state():
    """What survived the Rive retirement: the app-shell chrome and the mic
    button's honest pressed state. The `riveModeValues` literal this used to
    assert ("listening: 2, thinking: 3, loading: 0") was source text that the
    3D renderer never reads — it could have stayed in the file as dead data
    with the test still green, so it is gone with the table itself. The 3D
    equivalent, which fails if a state stops moving, is
    test_voice_preview_3d_mascot_moves_in_every_voice_state."""
    html = PAGE.read_text(encoding="utf-8")

    assert 'body[data-emma-state="idle"] .topbar { opacity: .78; }' in html
    assert "'Tap to talk': appShell ? 'เริ่มสนทนาด้วยเสียง'" in html
    assert html.count("micBtn.setAttribute('aria-pressed', 'false')") >= 4


def test_preview_3d_mascot_assets_are_served(client):
    """The page fetches these three by absolute URL. The content type matters
    as much as the bytes: a module served as text/plain is refused by the
    browser outright, and the mascot would simply never arrive."""
    glb = client.get("/assets/emma-3d-lab/emma-light-68k.glb")
    three = client.get("/assets/emma-3d-lab/vendor/build/three.module.js")
    core = client.get("/assets/emma-3d-lab/vendor/build/three.core.js")
    loader = client.get("/assets/emma-3d-lab/vendor/examples/jsm/loaders/GLTFLoader.js")
    mascot = client.get("/assets/emma-3d-lab/mascot.js")

    assert glb.status_code == 200
    assert glb.content.startswith(b"glTF")
    assert len(glb.content) > 1_000_000
    for resp in (three, core, loader, mascot):
        assert resp.status_code == 200, resp.url
        assert "javascript" in resp.headers.get("content-type", ""), resp.url

    # three.module.js re-exports from "./three.core.js" with a RELATIVE
    # specifier, so the two must stay together under vendor/build/.
    assert "three.core.js" in three.text


def test_three_js_runtime_is_not_gitignored():
    """client/assets/emma-3d-lab/.gitignore used to say `build/`, unanchored,
    which also matched vendor/build/ — so three.module.js and three.core.js
    were ignored while GLTFLoader.js was not. On this machine the files are
    present and everything passes; the showroom would have pulled a page whose
    importmap 404s on the three.js core with no visible error but a blank
    mascot. Exactly the trap this repo has paid for four times: a check that
    can only fail on the one machine nobody runs it on."""
    import subprocess

    lab = ROOT / "client" / "assets" / "emma-3d-lab"
    rules = [line.strip() for line in (lab / ".gitignore").read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.strip().startswith("#")]
    # A bare "build/" is unanchored and matches vendor/build/ too. The comment
    # above the rule contains "vendor/build/", so a substring check on the
    # whole file passes on the broken version — read the RULES, not the prose.
    assert "/build/" in rules
    assert "build/" not in rules
    for needed in ("vendor/build/three.module.js", "vendor/build/three.core.js",
                   "vendor/examples/jsm/loaders/GLTFLoader.js", "emma.glb",
                   "emma-light-68k.glb", "mascot.js"):
        result = subprocess.run(
            ["git", "check-ignore", "-q", str((lab / needed).relative_to(ROOT))],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        )
        # exit 1 == "not ignored". Anything else means a fresh clone loses it.
        assert result.returncode == 1, needed


# test_voice_preview_packages_only_the_rive_mascot_renderer stood here and was
# the test whose whole job was to keep three.js out of this page. It was
# INVERTED rather than deleted — see
# test_voice_preview_packages_and_drives_the_approved_3d_mascot (the renderer is
# wired) and test_voice_preview_has_retired_the_rive_mascot (Rive is gone).
#
# It had to go rather than stay: measured before the swap, every hook it banned
# was spelled `mascot-three` / `emma-three.js`, and its four path assertions
# named client/assets/emma/emma.glb and client/assets/vendor/three. The real
# model lives at client/assets/emma-3d-lab/emma.glb, so the test stayed GREEN
# against a working three.js renderer — a guard permanently reporting success
# about a decision that had been reversed (2026-09-23).


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
    three_css = html.split(".mascot-3d {", 1)[1].split("}", 1)[0]

    assert "transform: translate(var(--user-x), var(--user-y)) scale(var(--user-scale))" in wrap_css
    assert "translate(var(--user-x)" not in rig_css
    assert "translate(var(--user-x)" not in three_css
    # ...and the 3D canvas does carry the tilt, so the pointer-tilt rAF loop
    # keeps driving it exactly as it drove the Rive canvas.
    assert "rotateX(var(--tilt-x)) rotateY(var(--tilt-y))" in three_css
    assert "pointer-events: none" in three_css
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


def test_first_audio_chunk_keeps_the_start_of_emmas_transcript():
    code = _engine_code()
    binary_branch = code.split("if (msg.data instanceof ArrayBuffer) {", 1)[1].split("var evt;", 1)[0]
    assistant_branch = code.split("case 'assistant_transcript':", 1)[1].split("break;", 1)[0]
    complete_branch = code.split("case 'turn_complete':", 1)[1].split("break;", 1)[0]
    clear_fn = code[code.index("function clearTranscript()") :]
    clear_fn = clear_fn[: clear_fn.index("\n    }")]

    assert "beginAssistantTurn();" in binary_branch
    assert "curBot = ''" not in binary_branch
    assert "appendAssistantTranscript(evt.text || '')" in assistant_branch
    assert "assistantTurnText += text" in code
    assert "assistantLine.text = assistantTurnText" in code
    assert "resetAssistantTurn();" in complete_branch
    assert "resetAssistantTurn();" in clear_fn
    assert "who + ': </span>'" in code


def test_preview_transcript_with_interleaved_audio_and_delayed_user_chunks():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the preview event handler")
    result = subprocess.run([node, "tests/client_preview_transcript.cjs"],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_the_float_is_added_to_the_rigs_own_rest_height_not_assigned_over_it():
    """The hover ring is a separate object and does not follow the rig.

    The GLB puts the Root bone at y 0.45. Assigning the bob straight into
    ``root.position.y`` therefore did not just animate the float, it dropped the
    whole character 0.45 units, and the body's bottom tip sat inside the ring —
    on the voice page only, while the lab still showed the approved gap
    (2026-09-24). ``+=`` is not the fix either: on any frame the mixer does not
    rewrite that track it would accumulate and she would sink forever. The rest
    height has to be read once and added to.
    """
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "root.position.y = rootRestY + bob + breathe;" in code
    assert "root.position.y = bob + breathe;" not in code
    assert "root.position.y +=" not in code
    # Read from the bone before any clip or procedural motion has touched it.
    captured = code.index("rootRestY = bones.find")
    assert captured < code.index("scene.add(model)")


def test_inspection_spin_never_steals_the_plain_drag_the_page_owns():
    """Plain mouse drag turns the model; shift-drag still moves her.

    The page's own pointerdown handler lives on the same element, so the spin
    has to run in the CAPTURE phase and stop the event there. If it ever listens
    in the bubble phase, or forgets the modifier check, a plain drag would spin
    instead of move and the two drag tests above would still pass, because they
    assert the CSS variables the page writes rather than what the pointer did.
    """
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert 'const SPIN_GESTURE = event => event.button === 0 && !event.shiftKey;' in code
    # A finger turns her too, so the pinch has to be driven from here rather
    # than left to a page handler that would never see the first pointer.
    assert 'touches.size === 2' in code
    assert 'api.setScale(' in code
    # On the DOCUMENT: at-target listeners run in registration order, and the
    # page registers its move handler first, so a capture listener on the wrap
    # itself never got to stop it.
    for handler in ("pointerdown", "pointermove", "pointerup"):
        start = code.index(f"document.addEventListener('{handler}'")
        # The handler body may be several lines and contains ");" of its own,
        # so read up to the NEXT registration instead of the next paren.
        nxt = code.find("document.addEventListener(", start + 10)
        block = code[start:nxt if nxt > 0 else len(code)]
        assert ", true)" in block, f"{handler} must be a capture-phase listener"
    assert "event.stopPropagation();" in code
    # Orbiting the camera, like the lab: the ring stays flat on the floor and
    # the studio sweeps across the chrome instead of riding along with it.
    assert 'camera.position.setFromSphericalCoords(' in code
    assert 'model.rotation.y = spinY;' not in code
    # No OrbitControls: an orbiting camera would drag the studio reflections
    # around with the view and lose the chrome the owner signed off. Match the
    # import, not the word — the file's own header explains why it has none.
    assert "camera.position.set" in code
    assert "from 'three/addons/controls/OrbitControls.js'" not in code


def test_the_turn_gesture_survives_prefers_reduced_motion():
    """Reduced motion must silence the idle float, not freeze the person's hand.

    The loop zeroes dt when prefers-reduced-motion is set, which is how the
    breathing and the bob are switched off. The orbit damping read that same
    zeroed dt, so the camera never moved toward the angle the drag had already
    changed: the owner's screen showed a still robot while the on-screen debug
    readout showed theta swinging over a radian (2026-09-24). Reproduced with
    Playwright reduced_motion="reduce": mean image delta after a drag 0.00,
    against 9.27 with motion allowed; 8.63 after this fix.
    """
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "function renderStep(dt, handDt = dt) {" in code
    assert "renderStep(reduceMotion.matches ? 0 : dt, dt);" in code
    # The orbit damps on the hand clock; the body motion stays on the animation one.
    assert "damp(viewTheta, orbitTheta, 9, handDt)" in code
    assert "damp(viewTheta, orbitTheta, 9, dt)" not in code


def test_the_vertical_orbit_matches_the_lab_and_every_other_3d_viewer():
    """Drag down, look down on her — the same sign as OrbitControls.

    OrbitControls.rotateUp() subtracts from the polar angle, so a downward drag
    raises the camera. The first orbit here added instead, which felt inverted
    against the lab viewer sitting in the next tab (2026-09-24). The lab uses the
    real OrbitControls, so its convention is the one to match.
    """
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "spinFrom.phi - (event.clientY - spinFrom.y)" in code
    # Nearly the full polar range, as OrbitControls gives by default.
    assert "Math.max(.05, Math.min(3.09" in code
    assert "spinFrom.phi + (event.clientY - spinFrom.y)" not in code
    # Horizontal was already right: dragging right lowers theta, as it does there.
    assert "spinFrom.theta - (event.clientX - spinFrom.x)" in code


def test_emmas_line_is_a_speech_bubble_that_still_only_shows_while_she_speaks():
    """The owner asked for the bubble from his mockup — a look, not a behaviour.

    paintReply() is still the single place that decides when the line is on
    screen (now: any answer during a call, cleared the moment she is talked
    over — see the test below). Restyling it must not take that decision
    somewhere else, or the screen keeps a caption for something nobody said.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert "var visible = !!(inCall && curBot);" in html        # the single gate
    assert ".reply::after" in html                              # the tail
    assert "backdrop-filter: blur(7px);" in html
    # Its own accent: --state-rgb is declared on .mascot-wrap, a SIBLING, so it
    # never reached the bubble and every rgba() computed to nothing.
    assert "--bubble-rgb: 255, 255, 255;" in html
    reply_block = html[html.index("  .reply {"):html.index("  .reply-text {")]
    # The comment above the fix names the old variable on purpose, so match how
    # it is USED (inside an rgba) rather than the word appearing anywhere.
    assert "rgba(var(--state-rgb)" not in reply_block
    # With a card on the stage the reply is a caption band, so no tail may point
    # at a mascot that has stepped into the corner.
    assert "body.emma-staging .reply::after { display: none; }" in html


def test_the_reply_bubble_shows_for_every_answer_not_only_while_audio_plays():
    """Owner, 2026-09-24: "ให้ขึ้นทุกครั้งที่ emma ตอบ" — it never showed once.

    Measured in Chromium with a stubbed WebSocket, feeding a real turn's shape
    (audio chunk, then `assistant_transcript` pieces, then `turn_complete`):
    with the old `playing && curBot` gate the bubble's computed display stayed
    `none` at every step; with `inCall && curBot` it is `block`, 420x70, and
    carries the full sentence. Transcript and audio are independent streams and
    the text lands after the queued audio has ended, so a caption tied to a
    playback flag it does not arrive with loses the race every time.

    Two halves, and both matter: the gate, and the fact that the end of
    playback no longer wipes the text. Reverting either one alone puts the
    bubble back in the box nobody can see.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert "var visible = !!(inCall && curBot);" in html
    assert "!!(playing && curBot)" not in html

    # The end-of-audio branch may change Emma's state, but must not clear the
    # line. Sliced, not searched: `curBot = ''` is correct in five other places.
    onended = html[html.index("src.onended = function () {"):html.index("function stopPlayback()")]
    assert "playing = false;" in onended
    assert "curBot =" not in onended          # the assignment, not the comment

    # Still not sticky: a reply that was talked over is gone, and so is the
    # previous guest's line when a new call starts.
    for branch in ("case 'speech_started':", "case 'interrupted': {"):
        block = html[html.index(branch):]
        assert "curBot = '';" in block[:block.index("break;")]
    assert "function restScreen()" in html


def test_the_reply_bubble_follows_emma_wherever_she_is_dragged():
    """Owner, 2026-09-24: "อยากให้กล่องบอลลูนมันตามหุ่น...ให้มันแสดงข้างหุ่น".

    She is draggable and resizable; the bubble used to live in a grid cell, so
    when she was dragged into the bottom-right corner her line stayed floating
    in the middle of an empty screen. It is now pinned to `.mascot-wrap`'s live
    rect, which already carries both the drag and the scale in one transform —
    one source of truth instead of a second copy of "where is she now" that
    drifts from the one in mascot.js.

    Measured in Chromium with a real shift-drag (1600x900): dragged right, the
    bubble flipped to her left at x=745 against her box at 1015 with the tail
    turned round. On the robot's own 1080x1920 screen the room beside her is
    203px, so it goes under her instead, centred at 540.
    """
    html = PAGE.read_text(encoding="utf-8")
    assert "function trackReply()" in html
    assert "document.querySelector('.mascot-wrap')" in html
    assert "wrap.getBoundingClientRect()" in html
    assert "replySection.classList.add('is-tracking');" in html
    assert "position: fixed;" in html[html.index(".reply.is-tracking,"):]

    # Driven by a frame loop that starts with the bubble and stops with it: an
    # idle call must not keep measuring layout forever.
    assert "replyRaf = requestAnimationFrame(trackReply);" in html
    assert "else stopReplyTracking();" in html
    assert "if (replyRaf) { cancelAnimationFrame(replyRaf); replyRaf = 0; }" in html

    # Three placements, each with its own tail, so it always points back at her.
    for side in ("right", "left", "below"):
        assert '.reply.is-tracking[data-side="%s"]::after' % side in html

    # With a card on the stage the reply is a caption band and Emma is in the
    # corner — tracking has to keep its hands off that layout.
    track = html[html.index("function trackReply()"):]
    assert "document.body.classList.contains('emma-staging')" in track[:track.index("var m = wrap")]

    # Her box is not her body: ~20% of the canvas each side is empty, and
    # pinning to the box put the bubble a visible distance out in space
    # (owner: "กล่องมันห่างไหม"). Measured off a render, so it is a share of
    # the width and survives any zoom.
    assert "var inset = m.width * 0.20;" in html
    assert "m.right - inset + gap" in html
    assert "m.left + inset - gap - b.width" in html

    # And the bubble grows with her — never below 1, because her small sizes
    # were already right ("ถ้า emma หดประมาณนี้โอเค แต่พอตัว emma ใหญ่...").
    assert "var s = wrap.offsetWidth ? m.width / wrap.offsetWidth : 1;" in html
    assert "var rs = Math.min(1.9, Math.max(1, s));" in html
    assert "replySection.style.setProperty('--reply-scale', rs.toFixed(3));" in html
    assert "font-size: calc(clamp(1.02rem, 1.5vw, 1.26rem) * var(--reply-scale));" in html
    assert "padding: calc(14px * var(--reply-scale)) calc(20px * var(--reply-scale));" in html
    # The gap and the line width ride the same scale, or a big bubble ends up
    # as big type in a narrow column touching her cheek.
    assert "gap = 18 * rs" in html
    assert "var wide = 420 * rs;" in html

    # It comes out of her MOUTH — the smile band measured at 0.37-0.47 of the
    # wrap's height, centre 0.42 (the eyes are the band above it, peaking at
    # 0.29). And it is the TAIL that is anchored there: centring the box drifts
    # the tail up as soon as the line wraps to two, which is where the owner
    # saw it point at her cheek.
    assert "top = m.top + m.height * 0.42 - (26 + 9) * rs;" in html


def test_her_chest_lamp_is_the_mode_button_and_the_glow_is_the_indicator():
    """Owner, 2026-09-24: "ให้เปลี่ยนโดยกดปุ่มตรงกลางและฐานด้วย แล้วเปลี่ยนสีได้ไหม
    แต่ถ้าจะเปลี่ยนต้องกดที่ตรงกลางเท่านั้น".

    The chest lamp and the ring she hovers over are one material in the GLB
    ('White light'), so one write lights both — the colour IS the mode
    indicator. The hit test is a raycast against the chest meshes, not a
    rectangle: she can be dragged, scaled and turned, and a box in page
    coordinates would slide off the button the first time any of that happens.

    Measured in Chromium against the real page: three taps on her chest walked
    the URL emma -> condo -> translator with the ring going bluish (196,204,208),
    gold (207,195,162), mint (166,201,188); a tap on her HEAD and a drag across
    the chest both left the mode untouched; mid-call the tap redialled
    /ws?...&profile=condo. No page errors.
    """
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    html = PAGE.read_text(encoding="utf-8")

    # GLTFLoader sanitises node names, so the GLB's "Chest / luminous ring"
    # arrives as "Chest__luminous_ring". Matching the GLB spelling collected
    # nothing and the button silently did not exist — hasChestButton() measured
    # false while setAccent() worked, which is how it was caught.
    assert "o.name.startsWith('Chest__')" in code
    assert "o.name.startsWith('Chest /')" not in code
    assert "if (m.name === 'White light') glowMaterial = m;" in code

    # A raycast, and only a press that neither moved nor landed elsewhere.
    assert "new THREE.Raycaster()" in code
    assert "raycaster.intersectObjects(chestParts, true).length > 0" in code
    assert "tapFrom = chestUnder(event) ? { x: event.clientX, y: event.clientY } : null;" in code
    assert "const TAP_SLOP = 6;" in code
    assert "event.type === 'pointerup' && tapFrom" in code
    assert "new CustomEvent('emma-chest-tap'" in code

    # The model owns the pixels, the page owns the modes.
    assert "window.EmmaMascot = Object.freeze({" in code
    assert "glowMaterial.emissive.set(colour);" in code

    for mode in ("id: 'joke'", "id: 'condo'", "id: 'translator'"):
        assert mode in html
    assert "mascotWrap.addEventListener('emma-chest-tap', cycleMode);" in html
    # Repaint when the model finishes loading: setAccent() before that has
    # nothing to paint and returns false.
    assert "mascotWrap.addEventListener('emma-3d-ready', paintMode);" in html
    # The server's answer wins over the request, so a persona the machine
    # refuses shows what it actually opened.
    assert "if (evt.profile) { modeId = String(evt.profile).toLowerCase(); paintMode(); }" in html
    # A persona is chosen when the session opens, so a change has to redial —
    # through the close path, never over a live socket.
    assert "setTimeout(startCall, 220);" in html


def test_the_stage_projection_study_stays_in_the_lab():
    """Owner, 2026-09-24: "ทำใน lab ก่อนนะ อย่าเอาไปใช้จริง".

    The projection (a lit screen, a turned rig, beams out of her eyes) lives in
    client/assets/emma-3d-lab/stage-lab/ with its OWN copy of the renderer, so
    the page people actually talk to is untouched. This is what turns that
    verbal "not yet" into something that fails out loud — and it is the same
    guard shape as the room agent's.
    """
    lab = ROOT / "client" / "assets" / "emma-3d-lab" / "stage-lab"
    assert (lab / "index.html").is_file()
    assert (lab / "mascot-stage.js").is_file()

    # The shipping renderer has neither addition yet.
    shipped = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    for added in ("setEyes", "faceStage", "eyeMaterial", "facingTarget"):
        assert added not in shipped, added

    # And nothing the app loads points at the lab.
    for page in ("voice-preview.html", "index.html", "display.html"):
        path = ROOT / "client" / page
        if path.is_file():
            assert "stage-lab" not in path.read_text(encoding="utf-8"), page
