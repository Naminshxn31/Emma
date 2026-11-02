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
    assert 'class="mascot-3d"' in html
    assert 'class="reply"' in html
    assert html.index('class="mascot-3d"') < html.index('class="reply"')

    # One coherent icon family: inline stroke SVGs, not platform-dependent emoji.
    # Counted in the controls bar: the hold buttons' placeholder plus is the
    # same family, drawn from script.
    controls = html.split('<nav class="controls"', 1)[1].split("</nav>", 1)[0]
    assert controls.count('viewBox="0 0 24 24"') == 3
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


def test_voice_preview_keeps_continuous_3d_motion_without_the_svg_puppet():
    html = PAGE.read_text(encoding="utf-8")
    script = _puppet_renderer_code()
    # If the slice ever mis-targets again it collapses to a few hundred
    # characters; the real block is tens of thousands.
    assert len(script) > 10_000
    # Positive control: this really is the renderer, and it really does drive
    # the rig from a rAF loop rather than a timer.
    assert "function setPose(name, fromDemo)" in script
    assert "requestAnimationFrame(render)" in script

    # The orbit rings were removed on the owner's call — they sat on the wrap,
    # not the dragged rig, so they stayed centred as a ghost circle when Emma
    # moved.
    assert 'class="orbit' not in html

    for hook in (
        "perspective: 760px",
        "transform-style: preserve-3d",
        "rotateX(var(--tilt-x)) rotateY(var(--tilt-y))",
        "addEventListener('pointermove', aim",
        "requestAnimationFrame(render)",
        "--state-accent",
        "--gaze-x",
        'data-emma-left-hand="relaxed"',
        'data-emma-right-hand="relaxed"',
        "scene.dataset.emmaLeftHand = pose.leftHand || pose.hands",
        "scene.dataset.emmaRightHand = pose.rightHand || pose.hands",
    ):
        assert hook in html
    # The SVG puppet that used to stand under the 3D canvas is gone (owner,
    # 14 Oct: "หุ่นตัวเดิมนี้ลบออกจากโปรเจคได้ไหม มันชอบเด้งขึ้นมาเวลาโหลด" -- it
    # showed for a moment on every load before the 3D Emma faded in). The
    # renderer above still sets the same data-emma-* attributes and CSS vars,
    # because the 3D model (mascot.js) reads them.
    for gone in ('class="mascot-rig"', 'class="mascot-art"', "puppet-part", "face-part",
                 "emma-motion-prop", "emma-puppet-atlas", "@keyframes puppet-",
                 "@keyframes float-3d", "@keyframes ambient-turn", ".mascot-glint"):
        assert gone not in html, gone

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


def test_voice_preview_no_longer_loads_the_puppet_atlas():
    """The atlas was the SVG puppet's artwork; the puppet left the page on the
    owner's word (14 Oct). Nothing on the page may still ask for it."""
    html = PAGE.read_text(encoding="utf-8")
    assert "emma-puppet-atlas" not in html


def test_voice_preview_packages_and_drives_the_approved_3d_mascot():
    """The mascot on the call screen is the GLB the owner signed off
    (2026-09-23), rendered with the lab viewer's approved look. Every hook
    here fails if the renderer is removed, mis-wired, or pointed at a path
    that does not exist on a fresh clone."""
    html = PAGE.read_text(encoding="utf-8")
    lab = ROOT / "client" / "assets" / "emma-3d-lab"
    # The file the page LOADS. Anchoring this on the loaded name is the point:
    # another GLB sits beside it, so a check against that one stays green on a
    # page whose model 404s. 2026-09-25 to -29 it was the 68k reduction, whose
    # coarse head shell tore the chrome band's edges when she is turned; the
    # page is back on the 162k original (see mascot.js "load the approved
    # model" for the measured cause).
    glb = lab / "emma.glb"
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
    assert "new GLTFLoader().loadAsync('/assets/emma-3d-lab/emma.glb')" in code
    # The reduction that tore the seams must not come back by a quiet edit:
    # a lighter model needs a fine head shell and a check from the side.
    assert "emma-light-68k.glb')" not in code
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


def test_voice_preview_3d_mascot_fails_quietly():
    """If WebGL or the GLB fails, the ready class is never added, her spot
    stays empty (the SVG puppet that used to stand in was deleted on the
    owner's word, 14 Oct: it popped up on every load) and the page keeps
    working — one warning, no broken canvas, no spam."""
    html = PAGE.read_text(encoding="utf-8")
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")

    # The canvas stays invisible until the model is in: nothing half-built
    # shows during the load, and nothing else stands in its place.
    assert ".mascot-wrap.emma-3d-ready .mascot-3d { opacity: 1; }" in html
    assert "opacity: 0;" in html.split(".mascot-3d {", 1)[1].split("}", 1)[0]
    assert 'class="mascot-rig"' not in html

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
    assert "'Tap to talk': thaiUi() ? 'เริ่มสนทนาด้วยเสียง'" in html
    assert html.count("micBtn.setAttribute('aria-pressed', 'false')") >= 4


def test_preview_3d_mascot_assets_are_served(client):
    """The page fetches these three by absolute URL. The content type matters
    as much as the bytes: a module served as text/plain is refused by the
    browser outright, and the mascot would simply never arrive."""
    glb = client.get("/assets/emma-3d-lab/emma.glb")
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


def test_zooming_re_renders_the_3d_mascot_instead_of_stretching_it():
    """The zoom is a CSS transform on the wrap, and a ResizeObserver never
    fires for a transform: the WebGL buffer stayed at its zoom-1 size and a
    1.35x zoom stretched that bitmap (owner, 2026-09-29 "ซูมแล้วดูเบลอๆ").
    Measured in Chrome after the fix: at dpr 1 the buffer goes 360x430 ->
    486x580 at 1.35x (on screen 486x581), at dpr 2 720x860 -> 972x1161.

    Source-level on purpose, with the reason for each anchor: the buffer must
    follow the page's own --user-scale, be re-sized on the event the page fires
    for every zoom, and be sized from the untransformed layout box (a
    transformed rect double-counts the zoom at the next layout change)."""
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    sizing = code.split("function resize() {", 1)[1].split("new ResizeObserver(resize)", 1)[0]
    assert "wrap.offsetWidth" in sizing and "wrap.getBoundingClientRect()" not in sizing
    assert "base * scale" in sizing, "the pixel ratio no longer follows the zoom"
    assert "getPropertyValue('--user-scale')" in code
    listener = code.split("wrap.addEventListener('emma-transform-change'", 1)[1][:400]
    assert "resize()" in listener and "requestAnimationFrame" in listener
    # A changed file under an unchanged ?v= is served from cache to the robot.
    html = PAGE.read_text(encoding="utf-8")
    assert "mascot.js?v=20260924-orbit5" not in html


def test_press_and_hold_opens_six_action_slots_around_her():
    """Owner, 2026-09-29, with a sketch of three circles on each side of her:
    "ออกแบบให้กดค้างที่ตัวหุ่นจะมีปุ่มฟังก์ชั่นขึ้นมา ... ส่วนพวกฟังก์ชั่นต่างๆ
    ค่อยเพิ่มเข้าไป". Checked end to end in Chrome (25/25): a tap and a drag
    open nothing and the drag still turns her; a 500ms hold fills a ring and
    opens six empty slots, left and right of her art and on screen at 1400x1250
    and 540x960; a press on her closes them without turning her (0.00/255); a
    hold on the chest opens the menu instead of changing mode; a quick chest
    tap still changes it; the menu key, arrows and Escape work.

    Source-level anchors for what those runs depend on."""
    code = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    html = PAGE.read_text(encoding="utf-8")

    # mascot.js detects, the page decides -- the chest-tap split.
    for event in ("'emma-press-start'", "'emma-press-end'", "'emma-long-press'"):
        assert event in code
    assert "const HOLD_MS = 500;" in code and "const HOLD_SLOP = 8;" in code
    fire = code.split("hold.fired = true;", 1)[1][:200]
    # When the hold fires: the turn goes back to where the press began, and the
    # rest of the press neither turns her nor counts as a chest tap.
    assert "orbitTheta = hold.theta" in fire
    assert "spinFrom = null" in fire and "tapFrom = null" in fire
    assert "if (touches.size > 1) endHold();" in code          # a pinch is not a hold
    assert "> HOLD_SLOP) endHold();" in code                   # a drag is not a hold

    # The layer sits outside <main>: .shell isolates and the wrap is transformed.
    assert html.index('id="emmaActions"') > html.index("</main>")
    assert 'id="emmaHoldRing"' in html
    block = html.split("var ACTIONS = [", 1)[1].split("</script>", 1)[0]
    before = html.split("var ACTIONS = [", 1)[0]
    # A CLASSIC script, so its capture listener is registered before the
    # module's and can swallow the press that closes the menu.
    assert before.rsplit("<script", 1)[1].startswith(">")
    # Six slots, declared once. Slot 1 has since been filled (the face scan,
    # test_hold_menu_slot_one_opens_the_face_scan); the other five stay empty.
    assert html.count("var ACTIONS = [") == 1
    slots = block.split("];", 1)[0]
    assert slots.count("run: ") + slots.count("{}") == 6
    assert "stopImmediatePropagation" in block
    # An empty slot says so; it never looks like a function that ran.
    assert "ยังไม่มีฟังก์ชัน" in block and "is-empty" in block
    assert "emma-staging" in block                              # not in the corner
    assert "window.EmmaActions = Object.freeze(" in block

    # From the adversarial review (CDP touch, 2026-09-29), each reproduced
    # and then re-run green against the fix:
    # - the finger that opened the menu released onto a slot that appeared
    #   under it and pressed it: buttons ignore the pointer until that release
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert ".emma-actions.is-arming .emma-action { pointer-events: none; }" in css
    assert "layer.classList.add('is-arming');" in block
    # - a pinch begun on her while the menu was open turned her
    assert "swallowed.add(e.pointerId);" in block and "if (e.isPrimary) swallowed.clear();" in block
    # - the transcript drawer (z 50, later in the DOM) covered the bottom row
    layer_css = css.split(".emma-actions {", 1)[1].split("}", 1)[0]
    assert "z-index: 52;" in layer_css
    # - buttons clamped one by one stacked on short screens and at the edges
    assert "function pitchFor(size)" in block and "Math.max(pitch, room / 2)" in block
    # - Escape closed the drawer too; only the top layer closes now
    assert "e.stopImmediatePropagation();\n      closeActions(" in block
    # - any other pointer's release cancelled a hold in progress
    assert "if (hold && event.pointerId === hold.id) endHold();" in code
    # - the staging "hold to peek" had been dead since the 3D mascot landed
    assert "mascotEl.addEventListener('emma-long-press'" in html


def test_drag_moves_the_mascot_hit_area_and_keeps_a_regrabbable_edge_visible():
    html = PAGE.read_text(encoding="utf-8")
    wrap_css = html.split(".mascot-wrap {", 1)[1].split("}", 1)[0]
    three_css = html.split(".mascot-3d {", 1)[1].split("}", 1)[0]

    assert "transform: translate(var(--user-x), var(--user-y)) scale(var(--user-scale))" in wrap_css
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
    assert "aria-label=\"' + uiText('Close transcript', 'ปิดกล่องข้อความ')" in html
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
    assert 'class="mascot-3d"' in resp.text
    assert "no-cache" in resp.headers.get("cache-control", "")

    home = client.get("/")
    assert home.status_code == 200
    assert home.text != resp.text          # two different pages
    assert 'class="mascot-3d"' not in home.text


def test_preview_assets_route_serves_the_3d_module(client):
    """client/assets/ is served under /assets (the puppet atlas that this
    test used to fetch left with the puppet, 14 Oct); the 3D Emma's module
    is what the page loads from there now."""
    resp = client.get("/assets/emma-3d-lab/mascot.js")
    assert resp.status_code == 200


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
    assert "(bargeGate ? '&echo_barge=1' : '')" in code
    assert "getUserMedia" in code
    assert "registerProcessor('cap', Cap)" in code   # the debugged pipeline, copied
    # The renderer stays a renderer: the engine only ever calls the public API.
    assert "Emma.setState" in code
    assert "Emma.setMouthLevel" in code
    # Ordinary modes gate playback plus the acoustic tail. Joke verifies a
    # voice during playback before sending it upstream.
    assert "if ((halfDuplex || bargeIn) && now < echoGuardUntil) return;" in code
    assert "var decision = bargeGate.sample(frameRms(frame), now);" in code
    assert "personAudio.forEach(function (audio) { ws.send(audio); });" in code


def test_preview_echo_guard_and_joke_barge_in_execute_in_javascript():
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the browser audio gate")
    result = subprocess.run(
        [node, str(ROOT / "tests" / "client_echo_barge.cjs")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
    # ws = null makes the socket's onclose return early; its cleanup lives
    # here too (review 12 Oct), the same lines endCall runs.
    for line in ("quickAsks = null; setUiLang(FIXED_UI_LANG || 'th'); renderQuick();", "clearTranscript();",
                 "planDisplay.reset();", "attendFromServer(null);",
                 "if (window.EmmaRobotBridge) window.EmmaRobotBridge.detach();"):
        assert line in failure, line


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


# ==================== face scan: "สแกนใบหน้า ให้หุ่นจำ" ====================
# Owner, 2026-09-29: slot 1 of the hold menu scans a face so the robot knows
# the person -- staff PIN, the scanned person's own consent, the enrolment
# station's five poses, a name. Checked end to end in headless Chromium against
# a stub server with a fake camera (1391x1250, 1080x1920 ?cam=1, 390x844, and a
# desk window): opened through a real long-press and slot 1, 117/117 checks.
# These are the source-level anchors for what those runs depend on.

def _without_comments(code: str) -> str:
    code = re.sub(r"<!--.*?-->", "", code, flags=re.S)
    return "\n".join(line for line in code.splitlines()
                     if not line.lstrip().startswith(("//", "*", "/*")))


def _face_scan_code() -> str:
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("// ---- face scan: ", 1)[1].split("</script>", 1)[0]
    return _without_comments(block)


def _camera_sender_code() -> str:
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("// ---- the robot's own camera, sent to the server (`?cam=1`) ----", 1)[1]
    return block.split("</script>", 1)[0]


def _css_block(css: str, selector: str) -> str:
    return css.split(selector + " {", 1)[1].split("}", 1)[0]


def test_hold_menu_slot_one_opens_the_face_scan():
    html = PAGE.read_text(encoding="utf-8")
    menu = html.split("var ACTIONS = [", 1)[1].split("</script>", 1)[0]
    slots = menu.split("];", 1)[0]
    first, rest = slots.split("},", 1)
    assert "label: 'สแกนใบหน้า'" in first and "run: openFaceScan" in first
    assert "icon: SCAN_ICON" in first
    # Slot 2 is navigation since 2026-10-09 (design 2026-10-08 §3, P5; its own
    # anchors are in tests/test_face_body_nav_page.py); slot 3 the big screen
    # since 28 Oct (app/room_screens.py, tests/test_room_screens.py); the
    # other three stay empty.
    second, rest = rest.split("},", 1)
    assert "label: 'นำทาง'" in second and "run: openNav" in second and "when: navOffered" in second
    third, others = rest.split("},", 1)
    assert "label: 'จอใหญ่'" in third and "run: toggleScreen" in third and "when: screenOffered" in third
    assert others.split() == ["{},", "{},", "{}"]
    # Wired to the overlay's own API, and null (an honest empty slot) if the
    # overlay script is missing -- never a button that silently does nothing.
    assert "var openFaceScan = window.EmmaFaceScan ? window.EmmaFaceScan.open : null;" in html
    # ...which only works if the overlay script ran first.
    assert html.index("window.EmmaFaceScan = Object.freeze(") < html.index("var ACTIONS = [")
    # Same icon family as the call controls: an inline stroke SVG.
    icon = html.split("var SCAN_ICON = ", 1)[1].split(";\n", 1)[0]
    assert 'viewBox="0 0 24 24"' in icon and 'stroke="currentColor"' in icon


def test_face_scan_stops_the_live_emma_call_before_showing_its_own_voice(tmp_path):
    run = _page_run(tmp_path, "scan_stops_call")
    assert run == {"calls": [{"beforeOpen": True, "hidden": True}],
                   "open": True, "step": "pin"}

    code = _engine_code()
    end = code.split("function endCall() {", 1)[1].split("function toggleMute()", 1)[0]
    assert end.index("ws = null;") < end.index("closingSocket.close()")
    assert "releaseCallHardware();" in end
    assert "stopForFaceScan: function () { if (inCall || ws) endCall(); }" in code
    start = code.split("function startCall() {", 1)[1].split("function endCall()", 1)[0]
    assert "window.EmmaFaceScan.isOpen()" in start


def test_face_scan_posts_every_step_with_the_token_in_the_body_never_the_url():
    """WS_TOKEN also guards /ws, which drives the robot, and uvicorn's access
    log writes each request line verbatim -- the /data-console rule."""
    code = _face_scan_code()
    html = PAGE.read_text(encoding="utf-8")
    for route in ("start", "consent", "look", "capture", "save", "cancel"):
        assert f"post('{route}'" in code, route
    post = code.split("function post(path, data, keepalive) {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (token) body.token = token;" in post
    assert "fetch('/face-scan/' + path, {" in post
    assert "method: 'POST'" in post and "body: JSON.stringify(body)" in post
    # One way to reach the server, and no token anywhere near a URL.
    assert code.count("fetch(") == 1
    assert "?token=" not in code
    assert not re.search(r"/face-scan/[^'\"]*\?", html)
    # The PIN goes in the body too, and does not stay in the page.
    assert "post('start', { pin: pin })" in code
    assert "pinInput.value = '';" in code


def test_face_scan_shows_the_owners_consent_text_as_text_and_the_person_answers():
    code = _face_scan_code()
    html = PAGE.read_text(encoding="utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    # Text, never markup: the server's words cannot become elements here.
    assert "consentText.textContent = r.consent_text;" in code
    assert "innerHTML" not in code
    # His line breaks survive, and a long text scrolls instead of pushing the
    # two buttons off a short screen.
    consent_css = _css_block(css, ".emma-scan-consent")
    assert "white-space: pre-wrap;" in consent_css and "overflow-y: auto;" in consent_css
    assert "ขอความยินยอมเก็บข้อมูลใบหน้า" in html
    assert ">ยินยอม</button>" in html and ">ไม่ยินยอม</button>" in html
    # The person being scanned presses it, and staff are told so.
    assert "ผู้ที่จะถูกสแกนต้องอ่านและกดปุ่มด้วยตัวเอง" in html
    # "ยินยอม" is never offered over an empty box.
    assert "!r.consent_text.trim()" in code
    # The answer goes to the server as an answer: agree true, or agree false.
    assert "post('consent', { session: session, agree: true })" in code
    assert "post('consent', { session: sid, agree: false }, true)" in code


def test_face_scan_skips_the_consent_screen_only_on_the_servers_own_true():
    """Owner, 2026-09-29: staff only, no consent text -- the PIN holder asks
    the person first. The server decides (it opens the scan consented); the
    page follows only a JSON true, checked BEFORE the empty-text refusal, and
    otherwise still refuses to offer "ยินยอม" over an empty box."""
    code = _face_scan_code()
    html = PAGE.read_text(encoding="utf-8")
    start = code.split("post('start', { pin: pin }).then(function (r) {", 1)[1]
    skip = start.index("if (r.consented === true) {")
    assert skip < start.index("!r.consent_text.trim()")
    branch = start[skip:].split("\n        }\n", 1)[0]
    assert "startCapture();" in branch and "return;" in branch
    assert "show('consent')" not in branch
    # The PIN step tells staff their part of it.
    assert "ถามคนที่จะถูกสแกนก่อน แล้วใส่รหัส PIN เพื่อเริ่ม" in html


def test_face_scan_says_recognised_only_when_the_gallery_took_the_face():
    """Saved is not recognised: with gallery_total null the shots are on disk
    but the live gallery refused them."""
    code = _face_scan_code()
    assert code.count("'เรียบร้อย — หุ่นจำ '") == 1
    done = code.split("doneLine.textContent = r.gallery_total", 1)[1]
    done = "r.gallery_total" + done.split(";\n", 1)[0]
    assert done.startswith("r.gallery_total == null")
    assert "r.gallery_reason ||" in done
    assert done.index("r.gallery_reason") < done.index("'เรียบร้อย — หุ่นจำ '")


def test_face_scan_says_every_step_out_loud_in_english():
    """Owner, 2026-09-29: "ตอนแสกนให้พูดบอกด้วยคนจะได้รู้ แล้วขอภาษาอังกฤษ".
    One pre-rendered clip per enrollment step plus the page's own moments;
    each step is said once when it becomes the step, not on every look."""
    from app import enrollment

    voice_dir = ROOT / "client" / "assets" / "face-scan" / "voice"
    for key in [s["key"] for s in enrollment.STEPS] + ["noface", "done", "saved"]:
        clip = voice_dir / f"{key}.wav"
        assert clip.is_file() and clip.read_bytes()[:4] == b"RIFF", key
    code = _face_scan_code()
    assert "var VOICE_DIR = '/assets/face-scan/voice/';" in code
    # The step is announced once the camera is up, and again only when it changes.
    announce = code.split("function announceStep() {", 1)[1].split("\n    }\n", 1)[0]
    assert "stepKey === spokenStep" in announce and "!camStream" in announce
    assert "attach(cam);\n        announceStep();" in code
    assert "if (Object.prototype.hasOwnProperty.call(st, 'step')) stepKey = st.step || '';" in code
    # No face for a while: told where to stand, a bounded number of times.
    assert "noteFace(Array.isArray(r.box) && r.box.length >= 4);" in code
    assert "noFaceSaid >= NOFACE_MAX" in code
    # The last pose, and "I'll remember you" only when the gallery took the face.
    to_name = code.split("function toName() {", 1)[1].split("\n    }\n", 1)[0]
    assert "speak('done');" in to_name
    assert "if (r.gallery_total != null) speak('saved');" in code
    # Every way out that is not the finished card stops the voice.
    assert "hush();" in code.split("function fail(text) {", 1)[1].split("\n    }\n", 1)[0]
    close = code.split("function closeScan() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (current !== 'done') hush();" in close


def test_face_scan_round_two_review_fixes_hold():
    """The verifier of the first round of scan fixes found three places a
    revert stayed green, and two defects the fixes themselves introduced."""
    code = _face_scan_code()
    # fail() makes every in-flight reply stale, as closeScan does: a camera
    # request answering after the scan failed must not light the lens.
    fail = code.split("function fail(text) {", 1)[1].split("\n    }\n", 1)[0]
    assert fail.lstrip().startswith("gen += 1;")
    # A close during a save waits for the reply -- but not forever.
    wait = code.split("function closeAfterSave() {", 1)[1].split("\n    }\n", 1)[0]
    assert "}, CLOSE_WAIT_MS);" in wait
    assert "if (mine === gen && open && closeWanted && current === 'name')" in wait
    assert "var CLOSE_WAIT_MS = 20000;" in code
    # The idle deadline is wound again by every accepted shot, not only once.
    shoot = code.split("function shoot(mine) {", 1)[1].split("\n    }\n", 1)[0]
    ok_path = shoot.split("if (!r.ok) {", 1)[1]
    assert "armIdle();" in ok_path
    # A good answer clears the failure count, so failures between good looks
    # never add up to the three-in-a-row that ends a scan.
    tick = code.split("function tick() {", 1)[1].split("\n    }\n", 1)[0]
    assert tick.count("failures = 0;") == 1 and "failures = 0;" in shoot
    # A hidden desk tab ends the scan only on the camera step, never in the app.
    assert ("if (open && document.hidden && !IN_APP && current === 'capture') closeScan();"
            in code)


def test_the_robot_page_answers_the_cameras_bell_through_the_one_engine():
    """Owner, 2026-09-29: "ตอนนี้พอเจอหน้าคนจะทักเลยไหม" — it did not. The
    greeter recognised a face and logged "no standby browser rang ... no live
    session — dropping", because /preview never opened /ws/wake."""
    html = PAGE.read_text(encoding="utf-8")
    bell = html.split("// ---- the camera's bell (`?cam=1`)", 1)[1].split("</script>", 1)[0]
    # Only the page whose camera saw the person answers.
    assert "if (q.get('cam') !== '1') return;" in bell
    assert "'/ws/wake'" in bell
    # It dials through the engine's own door, not a second engine...
    assert "window.EmmaCall.answerSummons(reason);" in bell     # the bell's reason: face_* rings even when muted
    assert "new WebSocket(" in bell and bell.count("new WebSocket(") == 1
    engine = html.split("// ---- the robot's own camera, sent to the server (`?cam=1`) ----", 1)[0]
    # one dial of the call; the other socket is the room screen's watch
    # (/mirror, app/face_mirror.py), which never dials /ws
    assert engine.count("new WebSocket(") == 2 and engine.count("'/ws?voice='") == 1
    watch = engine.split("function startMirror() {", 1)[1].split("function startMirrorAudio()", 1)[0]
    assert watch.count("new WebSocket(") == 1 and "'/ws/mirror'" in watch and "'/ws?" not in watch
    # ...which flags the dial `summoned=1` once, so the server skips its own
    # opening line and the camera's greeting is the first thing said.
    assert "+ (summonedDial ? '&summoned=1' : ''));\n      summonedDial = false;" in engine
    door = engine.split("window.EmmaCall = Object.freeze({", 1)[1].split("});", 1)[0]
    assert "if (inCall || !apiKeyOK || (window.EmmaFaceScan && window.EmmaFaceScan.isOpen())) return false;" in door
    assert "startCall();" in door
    # Never over a live call, a face scan, or an app in the background.
    ring = bell.split("function ring(evt) {", 1)[1].split("\n    }\n", 1)[0]
    for guard in ("document.hidden", "EmmaFaceScan.isOpen()", "EmmaCall.busy()"):
        assert guard in ring, guard
    # Backoff resets on the server's own first message, never on onopen (the
    # server accepts-then-closes a bad token); a refusal stops it for good.
    assert ".onopen" not in bell
    assert "evt.type === 'standby_only' || evt.type === 'wake_listening'" in bell
    assert "evt.code === 'unauthorized'" in bell and "stopped = true;" in bell


def test_emma_does_not_shake_or_vibrate():
    """Owner, 2026-09-29: "ทำไมมีสั่นหยิกๆด้วย เอาออกไปก่อนยังไม่ต้องมีสั่น".
    The twitch was the playback level fed into the RATE of sin(phase * rate)
    with phase = seconds since load: a changing rate multiplies the whole
    history, so each frame jumped to a random point of the wave.

    Round two, 2026-09-30 ("สั่นตอนกดจบ และกดเพื่อพูด หรือทำอะไรจะมีสั่นหัว"):
    the same disease through the STATE blend — blendMotion eases bobRate/
    turnRate on every button press, and an eased rate on the wall clock sweeps
    the sine argument by (clock × Δrate×2π), a second of visible head buzz per
    press that grows the longer the kiosk page stays open. Every rate-driven
    sine must integrate its own phase (p += rate * dt); only constant-rate
    sines may multiply the clock."""
    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "const voice = 0;" in mascot
    assert "const voice = mouthLevel;" not in mascot
    shakes = re.findall(r"shake:\s*([\d.]+)\s*}", mascot)
    assert shakes and all(float(v) == 0 for v in shakes), shakes
    # No motion-table rate may multiply the running clock, anywhere.
    assert not re.findall(r"Math\.sin\(phase[^)]*(?:m\.|bobRate|turnRate)", mascot)
    for integrated in ("bobPhase += bobRate * dt;",
                       "turnPhase += m.turnRate * dt;",
                       "swayPhase += m.bobRate * .5 * dt;"):
        assert integrated in mascot, integrated
    html = PAGE.read_text(encoding="utf-8")
    assert "navigator.vibrate" not in html
    # A new cache key, so the robot's WebView does not keep the shaking copy
    # (20260930-clean); bumped again 10 Oct for the attention cue.
    assert "/assets/emma-3d-lab/mascot.js?v=20261010-attend" in html


def test_face_scan_cancels_the_session_and_releases_the_camera_on_every_way_out():
    code = _face_scan_code()
    close = code.split("function closeScan() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (session) { sendCancel(session); session = null; }" in close
    assert "releaseCamera();" in close and "stopLoop();" in close
    assert "isolate(false);" in close
    assert "wrap.focus({ preventScroll: true });" in close        # focus back on her
    # Fire-and-forget, and keepalive so it still leaves on pagehide.
    assert "post('cancel', { session: sid }, true)" in code
    assert "keepalive: !!keepalive" in code
    # Every close button, Escape, the page going away, the app backgrounded.
    assert "b.addEventListener('click', closeScan);" in code
    escape = code.split("document.addEventListener('keydown', function (e) {", 1)[1].split("}, true);", 1)[0]
    assert "e.stopImmediatePropagation();\n        closeScan();" in escape
    assert "addEventListener('pagehide', function () { if (open) closeScan(); });" in code
    # The app's pause still closes it -- unless this scan's own camera
    # request is out, when the pause is Android's permission dialog (below).
    pause = code.split("addEventListener('emma-native-pause', function () {", 1)[1].split("});", 1)[0]
    assert "if (asking && asking === gen) { pausedWhileAsking = true; return; }" in pause
    assert pause.rstrip().endswith("closeScan();")
    # A saved scan is not cancelled afterwards; an early end is.
    save = code.split("nameForm.addEventListener('submit'", 1)[1]
    assert save.index("session = null;") < save.index("show('done');")
    fail = code.split("function fail(text) {", 1)[1].split("\n    }\n", 1)[0]
    assert "sendCancel(session)" in fail and "releaseCamera();" in fail


def test_face_scan_looks_through_the_robots_stream_and_stops_only_its_own():
    code = _face_scan_code()
    html = PAGE.read_text(encoding="utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    camera = code.split("function openCamera(mine) {", 1)[1].split("\n    }\n", 1)[0]
    # The robot's shared stream first: one lens, one getUserMedia.
    assert camera.index("window.__emmaCamStream") < camera.index("getUserMedia(")
    assert "if (liveVideo(shared)) return Promise.resolve({ stream: shared, owned: false });" in camera
    assert "{ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false }" in camera
    assert "}, 8000);" in camera                                 # the robot's hung request
    release = code.split("function releaseCamera() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (camStream && camOwned) stopTracks(camStream);" in release
    # Off the moment the last pose is in.
    to_name = code.split("function toName() {", 1)[1].split("\n    }\n", 1)[0]
    assert "releaseCamera();" in to_name
    # The station's loop: a small copy to look, the whole frame to keep, one
    # request at a time, the hold time from the step, widths in camera pixels.
    assert "Math.min(1, 640 / vw)" in code and "full ? 0.92 : 0.7" in code
    assert "post('look', { session: session, image: grab(false), fullWidth: video.videoWidth })" in code
    assert "post('capture', { session: session, image: grab(true), fullWidth: video.videoWidth })" in code
    assert "if (inFlight ||" in code and "Number(st.hold_ms)" in code
    # Mirrored for display only, box and video together.
    assert "transform: scaleX(-1);" in _css_block(css, ".emma-scan-stage video, .emma-scan-stage canvas")


def test_robot_camera_sender_is_ported_and_runs_only_with_cam_1():
    """A desk browser must never start uploading its webcam because a flag was
    left on. Measured in Chromium: with ?cam=1 frames reach /ws/camera?token=
    at camfps and the scan reuses the stream (getUserMedia 1 -> 1); without it
    no socket and no camera; `unauthorized` withdraws the stream, stops its
    tracks, and never redials."""
    raw = _camera_sender_code()
    code = _without_comments(raw)
    assert "if (q.get('cam') !== '1') return;" in code
    guard = code.index("if (q.get('cam') !== '1') return;")
    assert guard < code.index("getUserMedia(") and guard < code.index("new WebSocket(")
    assert "q.get('kiosk')" not in code          # this page has no kiosk skin; cam=1 alone
    assert "window.__emmaCamStream = stream;" in code
    assert "if (window.__emmaCamStream === stream) window.__emmaCamStream = null;" in code
    # index.html's behaviour, kept.
    assert "'/ws/camera'" in code and "'?token=' + encodeURIComponent(token)" in code
    # Default 6 fps since 2026-09-30 (the confirm streak counts frames, so the
    # frame rate is half of how fast a passer-by can be greeted); cap stays 10.
    assert "Math.min(10, Math.max(1, Number(q.get('camfps')) || 6))" in code
    assert "Math.max(0, Number(q.get('camdev')) || 0)" in code
    assert "cams[CAM_DEV] || cams[0]" in code
    assert "}, 8000);" in code and "if (tries >= 6) { down = true; return; }" in code
    assert "setTimeout(start, 10000);" in code
    assert "setTimeout(start, 1500);" in code and "addEventListener('load', later);" in code
    assert "s.bufferedAmount > 400000" in code and "Date.now() - lastVTat > 3000" in code
    assert "evt.type === 'camctl'" in code and "adv.zoom" in code
    # Unauthorized is final -- and the lens goes off with it.
    unauthorized = code.split("evt.code === 'unauthorized') {", 1)[1].split("}", 1)[0]
    assert "down = true;" in unauthorized and "teardown();" in unauthorized
    # Video only: no second capture of the mic the call already holds.
    assert "audio: false" in code
    assert "mic_probe" not in code and "runRobotMicProbe" not in code
    # The measured robot quirks keep their explanations.
    assert raw.count("Measured on the robot 2026-09-11") + raw.count("measured on the robot 2026-09-11") == 2
    assert 'Android reported its entries as "facing back"' in raw


def test_face_scan_overlay_sits_above_every_layer_and_holds_the_page_still():
    html = PAGE.read_text(encoding="utf-8")
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    code = _face_scan_code()
    z = lambda block: int(re.search(r"z-index:\s*(\d+)", block).group(1))
    scan = _css_block(css, ".emma-scan")
    below = max(z(_css_block(css, ".emma-actions")), z(_css_block(css, ".emma-hold-ring")),
                int(re.search(r"\.vp-drawer\{[^}]*z-index:(\d+)", html).group(1)))
    assert z(scan) > below and below == 52
    assert "position: fixed;" in scan and "inset: 0;" in scan
    # Outside <main> (transformed/isolated ancestors), and a real modal.
    assert html.index('id="emmaScan"') > html.index("</main>")
    assert 'role="dialog" aria-modal="true"' in html
    # A step sets display:grid, which beats the UA [hidden]: without these two
    # rules every step would show at once.
    assert ".emma-scan[hidden] { display: none; }" in css
    assert ".emma-scan-step[hidden] { display: none; }" in css
    # Opening it closes the hold menu and makes the rest of the page inert.
    open_fn = code.split("function openScan() {", 1)[1].split("\n    }\n", 1)[0]
    assert "window.EmmaActions.close()" in open_fn and "isolate(true);" in open_fn
    assert "el.inert = true;" in code
    # The inputs the brief specifies.
    assert '<input id="emmaScanPin" type="password" inputmode="numeric" autocomplete="off"' in html
    assert '<input id="emmaScanName" type="text" maxlength="40"' in html
    assert ">ชื่อที่หุ่นจะเรียก</span>" in html


# ==================== face scan: what the review found (2026-09-29) ====================
# The source anchors above say the code is there; these RUN it. The page's own
# face-scan script and ?cam=1 camera sender, cut out of voice-preview.html
# verbatim, are executed in node against a fake DOM, a fake /face-scan server
# and a virtual clock -- so "two minutes with nobody at the lens" is two
# minutes of the page's own timers, not a grep for a constant. Each scenario
# below was run against the page before these fixes and came out the other
# way (the bug) -- that is what each assertion is here to keep.

_SCAN_HARNESS = r"""'use strict';
// Runs the page's REAL face-scan script (or its ?cam=1 camera sender),
// extracted verbatim, against a fake DOM, a fake /face-scan server and a
// virtual clock. usage: node harness.cjs <voice-preview.html> <scenario>
// Prints one JSON object: what the page did.
const fs = require('fs');
const vm = require('vm');
const [, , PAGE, SCENARIO] = process.argv;
const html = fs.readFileSync(PAGE, 'utf8');
function block(marker) {
  const at = html.indexOf(marker);
  if (at < 0) throw new Error('marker not found: ' + marker);
  return html.slice(at, html.indexOf('</script>', at));
}

// ---- virtual clock ----
let now = 1000000, nextId = 1;
const timers = new Map();
const clock = {
  setTimeout(fn, ms, ...a) {
    const id = nextId++;
    timers.set(id, { at: now + Math.max(0, Number(ms) || 0), fn, a, every: 0 });
    return id;
  },
  setInterval(fn, ms, ...a) {
    const id = nextId++;
    const every = Math.max(1, Number(ms) || 0);
    timers.set(id, { at: now + every, fn, a, every });
    return id;
  },
  clearTimeout(id) { timers.delete(id); },
  clearInterval(id) { timers.delete(id); },
};
async function flush() { for (let i = 0; i < 40; i++) await new Promise(r => setImmediate(r)); }
async function advance(ms) {
  const end = now + ms;
  await flush();
  for (;;) {
    let bestId = 0, best = null;
    for (const [id, t] of timers) {
      if (t.at <= end && (!best || t.at < best.at || (t.at === best.at && id < bestId))) { best = t; bestId = id; }
    }
    if (!best) break;
    now = best.at;
    if (best.every) best.at += best.every; else timers.delete(bestId);
    best.fn(...best.a);
    await flush();
  }
  now = end;
  await flush();
}

// ---- a DOM just big enough ----
function makeEl(tag, props) {
  const handlers = {};
  const el = {
    tagName: String(tag || 'div').toUpperCase(), hidden: false, value: '', disabled: false,
    inert: false, scrollTop: 0, attrs: {}, children: [], width: 300, height: 150, _text: '',
    style: { setProperty() {} },
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
      toggle(c, on) { if (on === undefined ? !this.set.has(c) : on) this.set.add(c); else this.set.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    removeEventListener(t, fn) { handlers[t] = (handlers[t] || []).filter(f => f !== fn); },
    fire(t, e) {
      (handlers[t] || []).slice().forEach(fn => fn(e || { preventDefault() {}, stopImmediatePropagation() {} }));
    },
    focus() { doc.activeElement = el; },
    appendChild(c) { this.children.push(c); return c; },
    remove() { this.removed = true; },
    contains(x) { return x === el; },
    getClientRects() { return [1]; },
    getContext() { return { drawImage() {}, clearRect() {}, strokeRect() {} }; },
    toDataURL() { return 'data:image/jpeg;base64,AAAA'; },
    toBlob(cb) { cb({ size: 10 }); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  Object.defineProperty(el, 'textContent', {
    get() { return this._text; },
    set(v) { this._text = String(v); if (this.onText) this.onText(this._text); },
    configurable: true,
  });
  return Object.defineProperties(el, Object.getOwnPropertyDescriptors(props || {}));
}

function makeStream(label) {
  const track = { readyState: 'live', label, stop() { this.readyState = 'ended'; } };
  return { label, track, stalled: false, stalledAt: 0,
    getVideoTracks: () => [track], getTracks: () => [track] };
}

const STEP_NAMES = ['pin', 'consent', 'capture', 'name', 'done', 'fail'];
const steps = {};
const xButton = makeEl('button');
const closeButtons = [xButton];
for (const n of STEP_NAMES) {
  const sub = { close: makeEl('button'), error: makeEl('p'), agree: makeEl('button'),
    decline: makeEl('button'), submit: makeEl('button') };
  closeButtons.push(sub.close);
  const busy = n === 'consent' ? [sub.agree, sub.decline] : [sub.submit];
  const map = { '[data-scan-close]': sub.close, '[data-scan-error]': sub.error,
    '[data-scan-agree]': sub.agree, '[data-scan-decline]': sub.decline };
  steps[n] = makeEl('form', {
    sub,
    querySelector(sel) { return map[sel] || null; },
    querySelectorAll(sel) { return sel === '[data-scan-busy]' ? busy : []; },
  });
  steps[n].attrs['data-scan-step'] = n;
}
const root = makeEl('div', {
  querySelectorAll(sel) {
    if (sel === '[data-scan-step]') return STEP_NAMES.map(n => steps[n]);
    if (sel === '[data-scan-close]') return closeButtons;
    return [];
  },
  querySelector(sel) { const m = /data-scan-step="(\w+)"/.exec(sel); return m ? steps[m[1]] : null; },
});
root.hidden = true;
const scanVideo = makeEl('video', {
  srcObject: null, readyState: 4,
  get videoWidth() { return this.srcObject ? 1280 : 0; },
  get videoHeight() { return this.srcObject ? 720 : 0; },
  play() { return Promise.resolve(); },
  pause() {},
});
const ids = { emmaScan: root, emmaScanVideo: scanVideo };
const byId = id => (ids[id] = ids[id] || makeEl(id === 'emmaScanPin' || id === 'emmaScanName' ? 'input' : 'div'));

// The camera sender's own <video>: currentTime runs with the clock unless
// the stream is stalled (what its freeze guard watches for).
function makeSenderVideo() {
  return makeEl('video', {
    srcObject: null, muted: false, playsInline: false,
    get videoWidth() { return this.srcObject && this.srcObject.track.readyState === 'live' ? 1920 : 0; },
    get videoHeight() { return this.videoWidth ? 1080 : 0; },
    get currentTime() {
      const s = this.srcObject;
      if (!s) return 0;
      return s.stalled ? s.stalledAt : now / 1000;
    },
    play() { return Promise.resolve(); },
  });
}

const docHandlers = {};
const doc = {
  hidden: false, readyState: 'complete', activeElement: null,
  getElementById: byId,
  createElement(t) { return t === 'video' ? makeSenderVideo() : makeEl(t); },
  body: { children: [], classList: { add() {}, remove() {} }, appendChild(c) { this.children.push(c); return c; } },
  querySelector() { return null; },
  addEventListener(t, fn) { (docHandlers[t] = docHandlers[t] || []).push(fn); },
  removeEventListener(t, fn) { docHandlers[t] = (docHandlers[t] || []).filter(f => f !== fn); },
  dispatch(t, e) { (docHandlers[t] || []).slice().forEach(fn => fn(e || {})); },
};
function setHidden(yes) { doc.hidden = yes; doc.dispatch('visibilitychange'); }

// ---- the fake /face-scan server ----
const KEYS = ['front', 'left', 'right', 'far', 'front2'];
const server = { done: 0, log: [], hooks: {} };
function state() {
  const cur = server.done < KEYS.length ? KEYS[server.done] : null;
  return { steps: KEYS.map((k, i) => ({ key: k, done: i < server.done, skipped: false, prompt: 'ท่า ' + k })),
    step: cur, hold_ms: 1, prompt: cur ? 'ท่า ' + cur : 'ครบแล้ว' };
}
function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}
function defaultRoute(path, body) {
  if (path === 'start') return [200, { ok: true, session: 'S1', consented: true, consent_text: null,
    consent_version: 'staff-attested', ...state() }];
  if (path === 'look') return [200, { ok: true, reason: '', box: [1, 2, 3, 4], frame: [640, 360], ...state() }];
  if (path === 'capture') {
    server.done = Math.min(KEYS.length, server.done + 1);
    return [200, { ok: true, shots: [], ...state() }];
  }
  if (path === 'save') return [200, { ok: true, name: body.name, total: 5, gallery_total: 5 }];
  if (path === 'cancel') return [200, { ok: true, closed: true }];
  if (path === 'consent') return [200, { ok: true, ...state() }];
  return [404, { ok: false, reason: 'ไม่พบ' }];
}
function deferred() {
  const d = {};
  d.promise = new Promise((res, rej) => {
    d.reply = (status, body) => res(response(status, body));
    d.drop = () => rej(new TypeError('Failed to fetch'));
  });
  return d;
}
function fetchFake(url, opts) {
  const path = String(url).replace('/face-scan/', '');
  const body = JSON.parse(opts.body);
  server.log.push({ t: now, path, session: body.session || null });
  const hook = server.hooks[path];
  const out = hook ? hook(body) : undefined;
  if (out === undefined) { const [s, b] = defaultRoute(path, body); return Promise.resolve(response(s, b)); }
  if (out === 'network') return Promise.reject(new TypeError('Failed to fetch'));
  if (out && out.promise) return out.promise;
  return Promise.resolve(response(out[0], out[1]));
}
const paths = () => server.log.map(r => r.path);
const count = p => server.log.filter(r => r.path === p).length;

// ---- camera ----
const gum = { calls: 0, times: [], plan: [], pending: null, streams: [] };
function getUserMedia() {
  gum.calls += 1;
  gum.times.push(now);
  const next = gum.plan.length ? gum.plan.shift() : 'ok';
  if (next === 'defer') {
    return new Promise((res, rej) => {
      gum.pending = {
        grant() { const s = makeStream('own' + gum.calls); gum.streams.push(s); res(s); },
        deny() { const e = new Error('Permission denied'); e.name = 'NotAllowedError'; rej(e); },
      };
    });
  }
  if (next === 'fail') {
    const e = new Error('Could not start video source');
    e.name = 'NotReadableError';
    return Promise.reject(e);
  }
  const s = makeStream('own' + gum.calls);
  gum.streams.push(s);
  return Promise.resolve(s);
}

// ---- sockets (the sender) ----
const sockets = [];
class FakeWS {
  constructor(url) {
    this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = 0;
    sockets.push(this);
    clock.setTimeout(() => { if (this.readyState === 0) { this.readyState = 1; if (this.onopen) this.onopen(); } }, 5);
  }
  send() { this.sent += 1; }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    clock.setTimeout(() => { if (this.onclose) this.onclose(); }, 0);
  }
}
FakeWS.OPEN = 1;

const audio = [];
class FakeAudio {
  constructor(src) { this.src = src; audio.push(src); }
  play() { return Promise.resolve(); }
  pause() {}
}

const winHandlers = {};
function sandboxFor(search) {
  const sb = {
    document: doc, fetch: fetchFake, URLSearchParams, Audio: FakeAudio, WebSocket: FakeWS,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    Date: { now: () => now },
    console: { log() {}, warn() {}, error() {} },
    location: { search, protocol: 'https:', host: 'robot.test' },
    navigator: { mediaDevices: { getUserMedia,
      enumerateDevices: () => Promise.resolve([{ kind: 'videoinput', deviceId: 'cam0' }]) } },
    addEventListener(t, fn) { (winHandlers[t] = winHandlers[t] || []).push(fn); },
    removeEventListener(t, fn) { winHandlers[t] = (winHandlers[t] || []).filter(f => f !== fn); },
  };
  sb.window = sb;
  return sb;
}
function winDispatch(t) { (winHandlers[t] || []).slice().forEach(fn => fn({ type: t })); }

let sb, scan;
function loadScan(search) {
  sb = sandboxFor(search || '');
  vm.createContext(sb);
  vm.runInContext(block('// ---- face scan: '), sb);
  scan = sb.EmmaFaceScan;
}
function loadSender(search) {
  sb = sandboxFor(search);
  vm.createContext(sb);
  vm.runInContext(block("// ---- the robot's own camera, sent to the server (`?cam=1`) ----"), sb);
}

const promptLine = () => byId('emmaScanPrompt');
function snapshot(extra) {
  return Object.assign({
    open: scan.isOpen(), step: scan.step(), paths: paths(),
    prompt: promptLine().textContent, sub: byId('emmaScanSub').textContent,
    done: byId('emmaScanDone').textContent, fail: byId('emmaScanFail').textContent,
    nameError: steps.name.sub.error.textContent, gumCalls: gum.calls,
  }, extra || {});
}

async function openToCapture(search) {
  loadScan(search);
  scan.open();
  byId('emmaScanPin').value = '1234';
  steps.pin.fire('submit', { preventDefault() {} });
  await advance(50);
}
async function toNameStep(search) {
  await openToCapture(search);
  await advance(6000);
  if (scan.step() !== 'name') throw new Error('did not reach the name step: ' + scan.step());
}
function submitName(name) {
  byId('emmaScanName').value = name;
  steps.name.fire('submit', { preventDefault() {} });
}
function closeBy(how) {
  const ev = { key: 'Escape', preventDefault() {}, stopImmediatePropagation() {} };
  if (how === 'x') xButton.fire('click');
  else if (how === 'cancel') steps.name.sub.close.fire('click');
  else if (how === 'escape') (docHandlers.keydown || []).forEach(fn => fn(ev));
  else if (how === 'pagehide') winDispatch('pagehide');
  else if (how === 'pause') winDispatch('emma-native-pause');
  else throw new Error(how);
}

const scenarios = {
  // ---- findings 0/18 + contract 1: a close while the save is out ----
  async save_close() {
    const [how, saveEnd, cancelEnd] = (process.argv[4] || 'x:ok:closed').split(':');
    await toNameStep();
    const save = deferred();
    server.hooks.save = () => save;
    const cancel = deferred();
    server.hooks.cancel = () => cancel;
    submitName('Dave');
    await advance(10);
    closeBy(how);
    await advance(10);
    const during = { open: scan.isOpen(), step: scan.step(), cancelSent: count('cancel'),
      nameError: steps.name.sub.error.textContent };
    if (saveEnd === 'ok') save.reply(200, { ok: true, name: 'Dave', total: 5, gallery_total: 5 });
    else if (saveEnd === 'lost') save.drop();
    else if (saveEnd === '500') save.reply(500, { ok: false, reason: 'เกิดข้อผิดพลาดที่เซิร์ฟเวอร์ — ลองใหม่' });
    else if (saveEnd === 'refused') save.reply(409, { ok: false, reason: 'ชื่อนี้มีอยู่แล้ว' });
    else if (saveEnd === 'gone') save.reply(404, { ok: false, reason: 'ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่' });
    await advance(10);
    const afterSave = { open: scan.isOpen(), step: scan.step() };
    if (cancelEnd === 'saved') cancel.reply(200, { ok: true, closed: true, saved: 'Dave' });
    else if (cancelEnd === 'closed') cancel.reply(200, { ok: true, closed: true });
    else if (cancelEnd === 'lost') cancel.drop();
    await advance(10);
    return snapshot({ during, afterSave, saidSaved: audio.filter(a => a.endsWith('/saved.wav')).length });
  },
  // ---- contract 2: the scan ran out before the name was saved ----
  async save_gone() {
    await toNameStep();
    server.hooks.save = () => [404, { ok: false, reason: 'ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่' }];
    submitName('Dave');
    await advance(50);
    return snapshot();
  },
  // ---- the app paused mid-capture (no camera request out): still closes ----
  async pause_capture() {
    await openToCapture('?app=1');
    await advance(300);
    winDispatch('emma-native-pause');
    await advance(50);
    return snapshot({ track: gum.streams.map(s => s.track.readyState) });
  },
  // ---- contract 3: a save before every pose is in ----
  async incomplete() {
    await toNameStep();
    server.done = 4;
    server.hooks.save = () => [409, { ok: false, reason: 'ยังเก็บภาพไม่ครบทุกท่า — ทำให้ครบก่อนบันทึก' }];
    submitName('Dave');
    await advance(1000);
    const back = snapshot({ looks: count('look') });
    await advance(4000);
    return snapshot({ back, captures: count('capture') });
  },
  // ---- finding 2: nobody finishes the capture step ----
  async idle() {
    await openToCapture();
    server.hooks.look = () => [200, { ok: false, reason: 'ยังไม่เห็นหน้า', box: null, frame: [640, 360], ...state() }];
    await advance(118000);
    const at118 = snapshot({ looks: count('look') });
    await advance(4000);
    const at122 = snapshot();
    const looksAfter = count('look');
    await advance(10000);
    return { at118, at122, looksLater: count('look') - looksAfter };
  },
  // ---- finding 2: the tab hidden mid-capture ----
  async hidden() {
    const search = process.argv[4] || '';
    await openToCapture(search);
    await advance(300);
    setHidden(true);
    await advance(50);
    return snapshot({ track: gum.streams.map(s => s.track.readyState) });
  },
  // ---- finding 17: a capture refusal stays readable ----
  async refusal() {
    await openToCapture();
    const reason = 'ภาพนี้เหมือนใบเดิม ลองขยับอีกนิด';
    const seen = [];
    promptLine().onText = v => seen.push([now, v]);
    server.hooks.capture = () => [200, { ok: false, reason, shots: [], ...state() }];
    await advance(4000);
    const first = seen.find(s => s[1] === reason);
    if (!first) return { error: 'refusal never shown', seen };
    const next = seen.find(s => s[0] > first[0] && s[1] !== reason);
    const captures = server.log.filter(r => r.path === 'capture').map(r => r.t - first[0]);
    return { onScreenMs: next ? next[0] - first[0] : null, capturesAfterMs: captures };
  },
  // ---- finding 34: transient failures ----
  async transient() {
    const mode = process.argv[4] || 'network';      // network | 500 | 404
    const times = Number(process.argv[5] || 1);
    await openToCapture();
    let left = times;
    server.hooks.look = () => {
      if (server.done !== 2 || left <= 0) return undefined;
      left -= 1;
      if (mode === 'network') return 'network';
      if (mode === '500') return [500, { ok: false, reason: 'เกิดข้อผิดพลาดที่เซิร์ฟเวอร์ — ลองใหม่' }];
      return [404, { ok: false, reason: 'ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่' }];
    };
    await advance(20000);
    return snapshot({ poses: server.done, looksFailed: times - left });
  },
  // ---- finding 20: every step starts at the top ----
  async scroll() {
    loadScan('');
    scan.open();
    root.scrollTop = 130;              // the person scrolled down to a button
    byId('emmaScanPin').value = '1234';
    steps.pin.fire('submit', { preventDefault() {} });
    await advance(50);
    return snapshot({ scrollTop: root.scrollTop });
  },
  // ---- contract 4: no English reaches the card ----
  async english() {
    await openToCapture();
    server.hooks.look = () => [200, { ok: false, reason: 'no face found (need 1, got 0)', box: null,
      frame: [640, 360], ...state() }];
    await advance(1000);
    const look = promptLine().textContent;
    delete server.hooks.look;
    server.hooks.capture = () => [200, { ok: false, reason: 'face too small: 40 px < 80 px', shots: [], ...state() }];
    await advance(700);
    const capture = promptLine().textContent;
    delete server.hooks.capture;
    server.hooks.look = () => [409, { ok: false, reason: 'this face is already in the gallery as Bob Smith' }];
    await advance(1000);
    return snapshot({ look, capture });
  },
  // ---- finding 22: on the robot, wait for the shared stream ----
  async shared() {
    const appears = Number(process.argv[4]);        // ms after the scan starts; NaN = never
    loadScan('?app=1&cam=1');
    sb.__emmaCamStream = null;
    const shared = makeStream('shared');
    if (!Number.isNaN(appears)) clock.setTimeout(() => { sb.__emmaCamStream = shared; }, appears + 50);
    scan.open();
    byId('emmaScanPin').value = '1234';
    steps.pin.fire('submit', { preventDefault() {} });
    await advance(2500);
    const at2500 = { gumCalls: gum.calls, usingShared: scanVideo.srcObject === shared, looks: count('look') };
    await advance(1500);
    return snapshot({ at2500, usingShared: scanVideo.srcObject === shared, looks: count('look') });
  },
  // ---- finding 22: the app paused while the scan's own camera request is out ----
  async asking() {
    const back = process.argv[4] || 'back';          // back | away | slow
    loadScan('?app=1');
    gum.plan = ['defer'];
    scan.open();
    byId('emmaScanPin').value = '1234';
    steps.pin.fire('submit', { preventDefault() {} });
    await advance(100);
    winDispatch('emma-native-pause');              // Android's permission dialog
    doc.hidden = true;
    doc.dispatch('visibilitychange');
    await advance(back === 'slow' ? 20000 : 2000);  // a person reading the dialog
    if (back !== 'away') setHidden(false);
    await advance(50);
    gum.pending.grant();
    await advance(2000);
    return snapshot({ track: gum.streams.map(s => s.track.readyState), looks: count('look') });
  },

  // ---- the ?cam=1 sender ----
  // finding 19: isolated failures over a day must not switch the camera off
  async sender_tries() {
    const episodes = Number(process.argv[4] || 7);
    loadSender('?cam=1&token=t');
    await advance(3000);
    const log = [];
    for (let i = 1; i <= episodes; i++) {
      const live = sb.__emmaCamStream;
      if (!live) { log.push('episode ' + i + ': not live'); break; }
      gum.plan = ['fail'];                       // the first reopen after the stall fails
      live.stalled = true;
      live.stalledAt = now / 1000;
      await advance(16000);
      log.push(!!(sb.__emmaCamStream && sb.__emmaCamStream !== live));
    }
    const s = sb.__emmaCamStream;
    return { live: !!(s && s.track.readyState === 'live'), log, gumCalls: gum.calls };
  },
  // finding 25: the operator screen / background stops the lens
  async sender_pause() {
    loadSender('?cam=1&token=t&app=1');
    await advance(3000);
    const before = sb.__emmaCamStream;
    const sentBefore = sockets.reduce((n, s) => n + s.sent, 0);
    winDispatch('emma-native-pause');
    doc.hidden = true;
    doc.dispatch('visibilitychange');
    const sentAtPause = sockets.reduce((n, s) => n + s.sent, 0);
    await advance(20000);
    const paused = {
      track: before.track.readyState, published: !!sb.__emmaCamStream,
      socketsOpen: sockets.filter(s => s.readyState !== 3).length,
      framesAfter: sockets.reduce((n, s) => n + s.sent, 0) - sentAtPause, gumCalls: gum.calls,
    };
    setHidden(false);
    await advance(3000);
    const s = sb.__emmaCamStream;
    return { sentBefore, paused,
      resumed: { live: !!(s && s !== before && s.track.readyState === 'live'), gumCalls: gum.calls,
        socketsOpen: sockets.filter(x => x.readyState === 1).length,
        frames: sockets.filter(x => x.readyState === 1).reduce((n, x) => n + x.sent, 0) } };
  },
  // a request that lands while the app is paused is stopped, not run
  async sender_pause_pending() {
    gum.plan = ['defer'];
    loadSender('?cam=1&token=t&app=1');
    await advance(2000);                              // start() at 1.5s: request out
    winDispatch('emma-native-pause');
    doc.hidden = true;
    doc.dispatch('visibilitychange');
    gum.pending.grant();                              // lands while she is away
    await advance(5000);
    const landed = gum.streams[0];
    const paused = { track: landed.track.readyState, published: !!sb.__emmaCamStream, sockets: sockets.length };
    setHidden(false);
    await advance(3000);
    const s = sb.__emmaCamStream;
    return { paused, resumed: { live: !!(s && s !== landed && s.track.readyState === 'live'), gumCalls: gum.calls } };
  },
  // the resume must not re-ask Android the moment a refused dialog closes
  async sender_denied() {
    const order = process.argv[4] || 'deny-first';   // deny-first | resume-first
    gum.plan = ['defer'];
    loadSender('?cam=1&token=t&app=1');
    await advance(2000);                              // start() at 1.5s: request out
    winDispatch('emma-native-pause');                 // the dialog
    doc.hidden = true;
    doc.dispatch('visibilitychange');
    await advance(1000);
    gum.plan = ['defer'];
    if (order === 'deny-first') { gum.pending.deny(); await advance(10); setHidden(false); }
    else { setHidden(false); await advance(10); gum.pending.deny(); }
    await advance(3000);
    const soon = gum.calls;
    await advance(9000);
    return { callsSoonAfter: soon, callsAfterBackoff: gum.calls, times: gum.times.map(t => t - 1000000) };
  },
  async scan_stops_call() {
    loadScan('?app=1&cam=1');
    const calls = [];
    sb.EmmaCall = { stopForFaceScan() { calls.push({ beforeOpen: !scan.isOpen(), hidden: root.hidden }); } };
    scan.open();
    return { calls, open: scan.isOpen(), step: scan.step() };
  },
};

(async () => {
  const fn = scenarios[SCENARIO];
  if (!fn) throw new Error('no scenario ' + SCENARIO);
  const out = await fn();
  process.stdout.write(JSON.stringify(out) + '\n');
  process.exit(0);
})().catch(e => { process.stderr.write(String(e && e.stack || e) + '\n'); process.exit(2); });
"""


def _page_run(tmp_path, scenario, *args):
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run the page's scan/camera scripts: the save/close race, "
                    "the idle deadline, the retries and the camera pause are NOT being checked on "
                    "this machine -- only test_face_scan_hardening_keeps_its_source_anchors is")
    harness = tmp_path / "scan_harness.cjs"
    harness.write_text(_SCAN_HARNESS, encoding="utf-8")
    # encoding, not text=True: the page's Thai comes back through stdout, and
    # Windows' default codepage (cp874) cannot decode node's UTF-8.
    result = subprocess.run([node, str(harness), str(PAGE), scenario, *args],
                            capture_output=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_closing_during_a_save_waits_for_it_and_says_what_happened(tmp_path):
    """Findings 0/18: X, ยกเลิก, Escape, pagehide and the app's pause all used to
    close the card while the save was out and throw its reply away -- the
    person was filed (shots, consent record, gallery rows) and staff saw a
    plain cancel. Now the cancel goes at once (the server holds it until the
    save is done) and the card stays until a reply says what happened."""
    for how in ("x", "cancel", "escape", "pagehide", "pause"):
        run = _page_run(tmp_path, "save_close", f"{how}:ok:closed")
        assert run["during"]["open"] and run["during"]["step"] == "name", how
        assert run["during"]["cancelSent"] == 1, how
        assert run["during"]["nameError"] == "กำลังบันทึกอยู่ — รอผลก่อนปิด…", how
        assert run["step"] == "done" and run["done"] == "เรียบร้อย — หุ่นจำ Dave แล้ว", how
    # The save's reply lost: the cancel's answer decides.
    run = _page_run(tmp_path, "save_close", "escape:lost:saved")
    assert run["step"] == "done" and run["done"] == "ยกเลิกไม่ทัน — บันทึก Dave ไปแล้ว"
    assert run["saidSaved"] == 0          # "I'll remember you" only with the gallery's word
    # A 5xx is not the server's "no" either: it may have fallen over part-way.
    run = _page_run(tmp_path, "save_close", "x:500:saved")
    assert run["step"] == "done" and run["done"] == "ยกเลิกไม่ทัน — บันทึก Dave ไปแล้ว"
    run = _page_run(tmp_path, "save_close", "pause:lost:closed")
    assert not run["open"]
    run = _page_run(tmp_path, "save_close", "cancel:lost:lost")
    assert run["step"] == "fail" and run["fail"].startswith("ไม่ทราบว่าบันทึกสำเร็จหรือไม่")
    # The server refused the save: nothing was filed, and the close stands.
    for end in ("refused", "gone"):
        run = _page_run(tmp_path, "save_close", f"x:{end}:closed")
        assert run["during"]["open"] and not run["afterSave"]["open"] and not run["open"], end
        assert run["paths"].count("cancel") == 1, end


def test_a_save_refused_as_incomplete_goes_back_for_the_missing_poses(tmp_path):
    """Contract: the server answers 409 "ยังเก็บภาพไม่ครบทุกท่า — ..." and keeps
    the scan. A retry of the name form can only be refused again."""
    run = _page_run(tmp_path, "incomplete")
    back = run["back"]
    assert back["step"] == "capture" and back["looks"] > 0
    assert back["prompt"] == "ยังเก็บภาพไม่ครบทุกท่า — ทำให้ครบก่อนบันทึก"   # still up after 1s
    assert back["paths"].count("cancel") == 0
    assert run["step"] == "name" and run["captures"] == 6            # the missing pose, then the form


def test_a_scan_that_ran_out_before_the_save_says_so_instead_of_offering_a_retry(tmp_path):
    run = _page_run(tmp_path, "save_gone")
    assert run["step"] == "fail" and run["fail"] == "ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่"


def test_an_abandoned_capture_step_ends_itself(tmp_path):
    """Finding 2: nothing ended a capture step nobody finished -- ~5 looks a
    second with the lens open, and every look kept the door greeting paused."""
    run = _page_run(tmp_path, "idle")
    assert run["at118"]["step"] == "capture" and run["at118"]["paths"].count("cancel") == 0
    assert run["at122"]["step"] == "fail"
    assert run["at122"]["fail"] == "หมดเวลา — ไม่ได้ภาพใหม่ใน 2 นาที ปิดแล้วเริ่มสแกนใหม่"
    assert run["at122"]["paths"].count("cancel") == 1
    assert run["looksLater"] == 0


def test_a_hidden_desk_tab_closes_the_scan_but_the_app_waits_for_its_own_pause(tmp_path):
    run = _page_run(tmp_path, "hidden")
    assert not run["open"] and run["paths"][-1] == "cancel" and run["track"] == ["ended"]
    # In the app the permission dialog hides the page too; 'emma-native-pause'
    # is the app's word that it is really leaving.
    run = _page_run(tmp_path, "hidden", "?app=1")
    assert run["open"] and run["step"] == "capture" and run["track"] == ["live"]
    run = _page_run(tmp_path, "pause_capture")
    assert not run["open"] and run["paths"][-1] == "cancel" and run["track"] == ["ended"]


def test_a_capture_refusal_stays_on_screen_and_the_hold_starts_again(tmp_path):
    """Finding 17: the refusal was overwritten by the next look ~200ms later and
    the full-resolution capture fired again every ~400ms."""
    run = _page_run(tmp_path, "refusal")
    assert run["onScreenMs"] >= 1500
    assert all(b - a >= 1500 for a, b in zip(run["capturesAfterMs"], run["capturesAfterMs"][1:]))


def test_one_dropped_look_does_not_throw_the_poses_away(tmp_path):
    """Finding 34: one failed request ended the scan and cancelled the batch."""
    for mode, times in (("network", "1"), ("500", "2")):
        run = _page_run(tmp_path, "transient", mode, times)
        assert run["step"] == "name" and run["poses"] == 5, (mode, times)
        assert "cancel" not in run["paths"], (mode, times)
    run = _page_run(tmp_path, "transient", "network", "3")
    assert run["step"] == "fail" and run["looksFailed"] == 3 and run["paths"][-1] == "cancel"
    assert run["fail"] == "ติดต่อเซิร์ฟเวอร์ไม่ได้ — ตรวจการเชื่อมต่อแล้วลองใหม่"
    # A 404 is the server's answer (the scan ran out), not a blip.
    run = _page_run(tmp_path, "transient", "404", "1")
    assert run["step"] == "fail" and run["looksFailed"] == 1
    assert run["fail"] == "ไม่พบการสแกนนี้ หรือหมดเวลาแล้ว — เริ่มใหม่"


def test_every_scan_step_starts_at_the_top(tmp_path):
    """Finding 20: a sideways phone scrolled to ยินยอม opened the camera step
    with the preview's top and the X above the screen."""
    assert _page_run(tmp_path, "scroll")["scrollTop"] == 0


def test_no_english_reason_reaches_the_scan_card(tmp_path):
    """Contract: the server says every look/capture refusal in Thai; the page
    keeps its own sentence for anything that is not (a library's English, the
    gallery's "already in the gallery as <somebody else>")."""
    run = _page_run(tmp_path, "english")
    assert run["look"] == "ท่า front"
    assert run["capture"] == "เก็บภาพไม่สำเร็จ ลองอีกครั้ง"
    assert run["step"] == "fail" and "Bob" not in run["fail"] and "gallery" not in run["fail"]


def test_on_the_robot_the_scan_waits_for_the_shared_stream(tmp_path):
    """Finding 22: a second getUserMedia for the lens the sender holds -- and
    in the app, Android's permission dialog."""
    run = _page_run(tmp_path, "shared", "1000")
    assert run["at2500"]["gumCalls"] == 0 and run["at2500"]["usingShared"]
    assert run["gumCalls"] == 0
    run = _page_run(tmp_path, "shared", "NaN")          # never comes: its own camera after 3s
    assert run["at2500"]["gumCalls"] == 0 and run["gumCalls"] == 1 and run["looks"] > 0


def test_the_permission_dialog_does_not_close_the_scan_that_asked(tmp_path):
    """Finding 22: requestPermissions pauses the Activity, and the pause closed
    the scan -- staff tapped Allow over a card that was already gone."""
    for back in ("back", "slow"):                       # slow: 20s reading the dialog
        run = _page_run(tmp_path, "asking", back)
        assert run["open"] and run["looks"] > 0 and "cancel" not in run["paths"], back
    run = _page_run(tmp_path, "asking", "away")         # the app really left
    assert not run["open"] and run["track"] == ["ended"] and run["paths"][-1] == "cancel"


def test_robot_camera_gives_up_only_on_failures_in_a_row(tmp_path):
    """Finding 19: six separate stalls over a day, each with one failed reopen,
    switched the robot's camera off until a reload."""
    run = _page_run(tmp_path, "sender_tries", "7")
    assert run["live"] and run["log"] == [True] * 7


def test_robot_camera_stops_while_the_app_is_paused_and_comes_back(tmp_path):
    """Finding 25: the operator screen sends 'emma-native-pause'; the lens kept
    uploading to the greeter behind it."""
    run = _page_run(tmp_path, "sender_pause")
    assert run["sentBefore"] > 0
    assert run["paused"] == {"track": "ended", "published": False, "socketsOpen": 0,
                             "framesAfter": 0, "gumCalls": 1}
    assert run["resumed"]["live"] and run["resumed"]["socketsOpen"] == 1 and run["resumed"]["frames"] > 0


def test_robot_camera_request_that_lands_while_paused_is_stopped(tmp_path):
    """A request still out when the app pauses (Android's permission dialog
    is exactly that) must not start the lens behind the operator screen."""
    run = _page_run(tmp_path, "sender_pause_pending")
    assert run["paused"] == {"track": "ended", "published": False, "sockets": 0}
    assert run["resumed"] == {"live": True, "gumCalls": 2}


def test_robot_camera_resume_does_not_re_ask_a_refused_camera_at_once(tmp_path):
    """The permission dialog is a pause too: a resume that started the camera
    straight away would raise the dialog again the moment it closed."""
    for order in ("deny-first", "resume-first"):
        run = _page_run(tmp_path, "sender_denied", order)
        assert run["callsSoonAfter"] == 1 and run["callsAfterBackoff"] == 2, (order, run)


def test_face_scan_hardening_keeps_its_source_anchors():
    """The same guards, read from the source, for a machine without node."""
    code = _face_scan_code()
    sender = _without_comments(_camera_sender_code())
    close = code.split("function closeScan() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (saving) { closeAfterSave(); return; }" in close
    assert close.index("if (saving) { closeAfterSave(); return; }") < close.index("gen += 1;")
    assert "saving = true;" in code
    assert "if (!transient(err)) { saveRefused(err.reason, err.status); return; }" in code
    assert "typeof said.saved === 'string' && said.saved" in code
    assert "var CAPTURE_IDLE_MS = 120000;" in code and "fail(IDLE_TEXT);" in code
    assert "armIdle();" in code.split("function startCapture() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (open && document.hidden && !IN_APP && current === 'capture') closeScan();" in code
    assert "if (Date.now() < noticeUntil) { holdingSince = 0; setHold(0); return; }" in code
    assert "notice(thaiOr(r.reason, 'เก็บภาพไม่สำเร็จ ลองอีกครั้ง'), NOTICE_MS);" in code
    assert "if (transient(err) && ++failures < MAX_FAILURES) {" in code
    assert "return status === 0 || status >= 500;" in code
    assert "root.scrollTop = 0;" in code.split("function show(name) {", 1)[1].split("\n    }\n", 1)[0]
    assert "setPrompt(thaiOr(r.reason, stepPrompt(st)), false);" in code
    assert "reason.indexOf(INCOMPLETE) === 0" in code and "var INCOMPLETE = 'ยังเก็บภาพไม่ครบทุกท่า';" in code
    assert "(CAM_SHARED ? sharedSoon(mine) : Promise.resolve(null))" in code
    assert "if (asking === mine && (nativeDialog || (pausedWhileAsking && document.hidden))) {" in code
    run = sender.split("function run(stream, mine) {", 1)[1]
    assert run.lstrip().startswith("tries = 0;")
    assert "if (down || paused || halt || asking || sock) return;" in sender
    assert "if (mine !== gen || paused) { stopTracks(stream); return; }" in sender
    pause = sender.split("addEventListener('emma-native-pause', function () {", 1)[1].split("});", 1)[0]
    assert "paused = true;" in pause and "if (halt) halt();" in pause
    assert "document.addEventListener('visibilitychange', resume);" in sender
    assert "if (!retryTimer) start();" in sender


def test_the_temporary_camera_debug_panel_is_robot_only_and_speaks_server_words():
    """Owner 2026-09-30: "อยากให้เพิ่มกล้อง... อยากรู้ว่ามันจับคนได้ไหม
    เอามาไว้ชั่วคราว". The panel lives INSIDE the ?cam=1 sender block, so a
    desk browser never builds it; ?camdebug=0 hides it without a new APK; its
    one line of text is the server's own verdict (evt.verdict) and never
    anything a customer said (the kiosk-chip rule); and it looks through the
    greeter's own lens instead of opening a second getUserMedia."""
    html = PAGE.read_text(encoding="utf-8")
    gate = html.index("if (q.get('cam') !== '1') return;")
    panel = html.index("id = 'camDebug'")
    assert gate < panel
    assert "q.get('camdebug') !== '0'" in html
    handler = html.index("evt.type === 'face_status'")
    assert gate < handler
    branch = html[handler:handler + 500]
    assert "evt.verdict" in branch
    for forbidden in ("curUser", "heard", "transcript"):
        assert forbidden not in branch, forbidden
    assert "dbgVideo.srcObject = stream;" in html
    # Owner 14 Oct ("ตรงนี้ขอเพิ่มขนาดหน่อย"): on the robot's app page the
    # opened panel is read from standing height, and a tap must open it to
    # that size, not back to the desk's 210 px.
    app = html[html.index("if (q.get('app') === '1') {", panel):]
    app = app[:app.index("if (document.body) document.body.appendChild(dbgPanel);")]
    assert "dbgOpenW = '40vw';" in app and "dbgVideo.style.height = '30vw';" in app
    assert "dbgPanel.style.width = collapsed ? dbgOpenW : '20px';" in html


def test_a_mute_holds_across_calls_until_the_mic_is_tapped():
    """Owner, 14 Oct: "เวลา mute ไม่ต้องให้ reconnect เพราะไม่งั้นหุ่นจะพูด
    ตลอดเมื่อได้ยินเสียงรอบๆ". A muted call idles out, the standing line
    rings a fresh line within seconds, and startCall() starts it unmuted --
    the room was back in her ears. The hold must cover every door: the
    toggle sets it, the standing line's bell is not answered while it
    stands, a call that closes under it does not invite a redial, and only a
    tap on the mic with no call open lifts it. Same day: "ตอน Mute ถ้ากล้อง
    จับหน้าคนได้ จะต้องทักนะ" -- the camera's bells (face_*) still open a
    line, so the bell socket stays open and tells the server about the hold."""
    html = PAGE.read_text(encoding="utf-8")
    toggle = html[html.index("function toggleMute() {"):]
    toggle = toggle[:toggle.index("\n    }")]
    assert "setMuteHold(muted);" in toggle
    summons = html[html.index("answerSummons: function (reason) {"):]
    assert summons[:summons.index("startCall();")].count(
        "if (muteHold && !FACE_RING.test(String(reason || ''))) return false;") == 1
    assert "if (!inCall) { if (muteHold) setMuteHold(false); startCall(); }" in html
    # review 14 Oct: a second tap while connecting muted -- and held -- a call that
    # then said "Listening" with every frame dropped; no mic yet = the tap does nothing
    assert "else if (micStream) toggleMute();" in html
    # an idle-timed-out held call rests on "Muted", and an error keeps its own words
    assert "status(muteHold ? 'Muted' : 'Call ended — tap the mic to start again');" in html
    assert "if (!callError) status('Muted'); return; }" in html and "callError = true;" in html
    onclose = html[html.index("ws.onclose = function () {"):]
    onclose = onclose[:onclose.index("function endCall()")]
    assert onclose.index("if (muteHold) {") < onclose.index("setMicLabel('Tap to talk');")
    assert "sessionStorage.getItem(MUTE_HOLD_KEY) === '1'" in html     # survives the app's reload on resume
    bell = html[html.index("var RETRY_MIN_MS = 2000, RETRY_MAX_MS = 30000;"):]
    bell = bell[:bell.index("function later()")]
    assert "if (held() && !FACE_RING.test(reason)) return;" in bell
    assert "window.EmmaCall.answerSummons(reason);" in bell
    assert "sock.send(JSON.stringify({ type: 'hold', on: held() }))" in bell
    assert "document.addEventListener('emma-mute-hold', sendHold);" in bell
    assert "retryMs = RETRY_MIN_MS; sendHold(); return;" in bell          # told again on every reconnect
    assert "if (stopped || sock) return;" in bell and "held()) return;" not in bell.split("function ring")[0]
    import re as _re
    face = _re.compile(r"^face_")
    assert all(face.match(r) for r in ("face_approach", "face_known", "face_stranger"))
    assert not face.match("standing_line")


def test_the_floor_ring_is_gone_everywhere():
    """Owner, 2026-09-30 (screenshot of the ring assembly under her feet):
    "อันนี้ลบทิ้งหรือเอาออกไปได้เลย". Both renderings go together — the SVG
    fallback's ring/dashed halo/spinning orbit, and the 3D GLB's hover ring —
    or a machine without WebGL shows the ring the 3D build deleted."""
    html = PAGE.read_text(encoding="utf-8")
    for gone in ("puppet-floor-ring puppet-part", 'class="emma-data-orbit"',
                 "@keyframes puppet-ring-pulse", "@keyframes puppet-ring-loading",
                 "@keyframes emma-data-spin"):
        assert gone not in html, gone
    mascot = (ROOT / "client" / "assets" / "emma-3d-lab" / "mascot.js").read_text(encoding="utf-8")
    assert "if (o.name.startsWith('Hover__')) o.visible = false;" in mascot
    # And the half-rate glow that came with the same request ("ตัวแอบช้าๆ ...
    # ให้มันลื่นขึ้น"): the second full-rig render runs every other frame.
    assert "if ((glowTick++ & 1) === 0) {" in mascot
    assert "glowTick = 0;   // reallocated targets are black until the next glow pass" in mascot


def test_hold_speech_queues_over_her_voice_and_flushes_only_when_she_finishes():
    """Owner 2026-09-30: with barge-in rejected and half-duplex dropping
    everything said over her, hold-speech is the third road — queue what
    clears the floor while she speaks, send it the moment her audio ends.
    The contracts that must not rot: it rides on the server flag AND
    half-duplex AND no barge-in; frames flow into the queue only from the
    old drop branch; the flush happens at the NATURAL end of playback and
    nowhere else — an interruption or teardown throws the queue away (held
    speech from a cancelled moment is nobody's next turn); the queue is
    bounded and floor-gated with a pre-roll so first syllables survive."""
    html = PAGE.read_text(encoding="utf-8")
    assert "holdSpeech = !!evt.hold_speech && halfDuplex && !bargeIn;" in html
    drop = html.index("if (!halfDuplex) { ws.send(frame); return; }")
    assert "if (holdSpeech) holdFrame(frame, now);" in html[drop:drop + 200]
    # flush only at the natural end of playback (src.onended, queue empty)
    ended = html.index("if (!sources.length) {")
    assert "flushHeldSpeech();" in html[ended:ended + 700]
    assert html.count("flushHeldSpeech();") == 1
    stop = html.index("function stopPlayback()")
    assert "dropHeldSpeech();" in html[stop:stop + 900]
    release = html.index("function releaseCallHardware()")
    assert "dropHeldSpeech();" in html[release:release + 700]
    # floor + hangover + pre-roll + bound
    assert "frameRms(frame) >= holdFloor" in html
    assert "holdVoicedUntil = now + HOLD_HANG_MS" in html
    assert "if (holdPre.length > HOLD_PRE_FRAMES) holdPre.shift();" in html
    assert "while (holdBytes > holdMaxBytes && holdQueue.length) {" in html
    # a muted or closed socket never receives a flush
    assert "if (!ws || ws.readyState !== WebSocket.OPEN || muted) return;" in html


def test_a_soft_cue_marks_her_turn_ending_and_never_an_interruption():
    """Owner 2026-09-30: "ให้มีเสียงบอกไหมว่าพูดได้" — a quiet two-note cue
    when her reply finishes playing, i.e. the moment the line is the guest's.
    It must follow real speech ending only: not stopPlayback (interruptions,
    teardowns) and nothing on the standing line's idle cycling, or the robot
    dings at an empty room every two minutes. ?cue=0 switches it off."""
    html = PAGE.read_text(encoding="utf-8")
    assert "function playListenCue()" in html
    assert "get('cue') === '0'" in html
    ended = html.index("if (!sources.length) {")
    # ...and never over a muted mic: the guest can't speak now (audit 27 Oct)
    assert "if (inCall && !muted) playListenCue();" in html[ended:ended + 1200]
    stop = html.index("function stopPlayback()")
    stop_body = html[stop:html.index("function releaseCallHardware()")]
    assert "playListenCue" not in stop_body
    assert html.count("playListenCue();") == 1


def test_a_muted_mic_stays_muted_on_the_pill_whatever_ends_her_turn():
    """Audit 27 Oct: mute while she speaks, and when her voice ended the pill
    said "Listening..." over the closed mic (the speak-now chime too, above).
    Every way back to listening says "Muted" while muted: her voice ending,
    an interruption, the "thinking" fallbacks and the mute button itself.
    The two bare "Listening..." left answer the guest's own voice -- the
    local barge-in and speech_started -- which comes up only in mic frames,
    and handleMicAudio drops every frame while muted. The real page:
    test_muted_her_answer_ends_on_the_muted_pill_without_the_cue."""
    html = PAGE.read_text(encoding="utf-8")
    ended = html[html.index("if (!sources.length) {"):]
    ended = ended[:ended.index("\n      };\n")]
    rest = "if (inCall) { show('listening'); if (muted) status('Muted'); } else paintReply();"
    assert rest in ended
    assert rest in html.split("case 'interrupted': {", 1)[1].split("break;", 1)[0]
    assert "status(muted ? 'Muted' : 'Listening...')" in html.split("case 'heard_end':", 1)[1].split("break;", 1)[0]
    bare = [line.strip() for line in html.splitlines() if "show('listening')" in line and "muted" not in line]
    # the room screen's copy (/mirror) has no mic to mute: its call-started pose
    assert "show('listening');" in html.split("case 'mirror_start':", 1)[1].split("break;", 1)[0]
    bare.remove("show('listening');")
    assert bare == ["show('listening'); status('Listening...');",
                    "if (inCall) { show('listening'); status('Listening...'); } else paintReply();"], bare
    assert bare[1] in html.split("case 'speech_started':", 1)[1].split("break;", 1)[0]
    mic = html[html.index("function handleMicAudio(frame) {"):]
    assert mic.index("if (muted || !ws") < mic.index("show('listening'); status('Listening...');")


def test_the_mode_lamp_colours_are_the_owners_latest_pick():
    """Owner 2026-10-01: "เปลี่ยนสี สีขาวเป็น emma สำหรับโครงการ สีดำเป็น
    emmajoke" — the third repaint (Emma ทอง → ดำ → ขาว, EmmaJoke ฟ้าขาว → ดำ).
    The colours are the owner's decision, not a style choice, so the test pins
    them; and the aura belongs to whichever lamp is BLACK (a black lens has
    nothing for the glow pass to blur), so it must sit on joke now."""
    html = PAGE.read_text(encoding="utf-8")
    modes = html[html.index("var MODES = ["):]
    modes = modes[:modes.index("];")]
    joke = next(l for l in modes.splitlines() if "'joke'" in l)
    condo = next(l for l in modes.splitlines() if "'condo'" in l)
    assert "#000000" in joke and "aura" in joke
    assert "#ffffff" in condo and "aura" not in condo


def test_the_robot_face_speaks_english_on_screen():
    """Owner 12 Oct on the robot: "แก้เป็นภาษาอังกฤษ ทั้งหมด". One value
    decides the face's own words; setUiLang cannot move off it (a Thai guest
    used to turn the pill Thai), and every reset goes back to it, not to Thai.
    Emma's replies are not this: she still answers in the guest's language."""
    html = PAGE.read_text(encoding="utf-8")
    assert "var FIXED_UI_LANG = 'en';" in html
    assert "var uiLang = FIXED_UI_LANG || 'th';" in html
    assert "uiLang = 'th'" not in html                      # no second declaration that resets it
    fn = html[html.index("function setUiLang(code) {"):]
    assert fn.split("\n")[1].strip() == "if (FIXED_UI_LANG) code = FIXED_UI_LANG;"
    assert "setUiLang('th')" not in html
    # the buttons start from the table, not from hard-coded Thai
    assert "messageLabel.textContent = (CHROME[uiLang] || CHROME.th).message;" in html
    # the cards and the drawer go through uiText; the desktop /preview keeps Thai
    assert "function uiText(en, th) { return appShell && uiLang !== 'th' ? en : th; }" in html
    for en in ("'Price — please ask our sales team'", "'Room photo — coming from our sales team'",
               "uiText('Conversation', 'บทสนทนา')", "UNIT_STATUS_EN[u.status]"):
        assert en in html, en


def test_each_answer_owns_the_screen():
    """Owner 12 Oct on the robot: the Sky Pool stayed up while she answered
    the next question (a price) with no picture. A guest question opens an
    answer; an answer that stages nothing clears the old picture at its
    turn_complete. Her own announcements (no speech_started) clear nothing."""
    html = PAGE.read_text(encoding="utf-8")
    started = html.split("case 'speech_started':", 1)[1].split("break;", 1)[0]
    assert "answerOwed = true;" in started and "answerStaged = false;" in started
    done = html.split("case 'turn_complete':", 1)[1].split("break;", 1)[0]
    # ...only a turn that said something (audio or words): a dropped bare "ค่ะ"
    # or gemini-3.8's empty pre-tool turn is no answer, and took the story
    # down with nothing in its place (audit 27 Oct; the real page below:
    # test_a_turn_that_said_nothing_keeps_the_screen)
    clear = ("if (turnSaid && answerOwed && !answerStaged && (document.body.classList.contains('emma-staging') "
             "|| document.body.classList.contains('mirror-story'))) "
             "clearStage();")
    assert clear in done
    assert done.index("var turnSaid = assistantTurnStarted;") < done.index("resetAssistantTurn();") < done.index(clear)
    begin = html[html.index("function beginAssistantTurn() {"):]
    assert "assistantTurnStarted = true;" in begin[:begin.index("\n    }\n")]   # set by her audio and her words
    opener = html.split("function openStage() {", 1)[1].split("}", 1)[0]
    assert "answerStaged = true;" in opener


def test_an_answer_without_a_picture_clears_the_old_one_at_her_first_sound():
    """Owner 15 Oct: "ถามคำถามที่ไม่มี motion ก็ยังไม่ออกจากอันเก่า" -- turn_complete
    comes when generation ends, seconds into her new answer; the old story
    stayed up over its start. The first audio chunk of an answer that staged
    nothing clears it (a story/slide of this answer arrives before her voice)."""
    html = PAGE.read_text(encoding="utf-8")
    audio = html.split("if (msg.data instanceof ArrayBuffer) {", 1)[1].split("return;\n      }", 1)[0]
    clear = ("if (answerOwed && !answerStaged && (document.body.classList.contains('emma-staging') "
             "|| document.body.classList.contains('mirror-story'))) clearStage();")
    assert clear in audio
    assert audio.index(clear) < audio.index("playChunk(msg.data);")
    # a barged-in old reply's late chunks are dropped before anything is cleared
    assert audio.index("if (bargePendingUntil > performance.now()) return;") < audio.index(clear)


def test_a_staged_card_puts_emma_in_the_corner_in_the_app_shell():
    """Owner's photo, 13 Oct: the room card (show_room_type) with Emma 610 px
    wide over its lower right. `html.app-shell .mascot-wrap` is as specific
    as `body.emma-staging .mascot-wrap` and comes later in the file, so the
    corner size never applied in the robot's app; the staged rule must be
    the more specific one."""
    html = PAGE.read_text(encoding="utf-8")
    shell = html.index("html.app-shell .mascot-wrap {")
    staged = html.index("html.app-shell body.emma-staging .mascot-wrap {")
    assert staged > shell
    assert "width: min(30vw, 300px);" in html[staged:staged + 120]


def test_while_a_story_plays_the_pill_says_she_is_explaining_and_you_may_ask():
    """Reviewer, 13 Oct: during a story the pill read "Listening..." /
    "Speaking..." -- a guest could not tell she was explaining, or that a
    question was still welcome. Both writers of the pill (status() and the
    renderer bridge show()) go through one mapping, in every UI language."""
    html = PAGE.read_text(encoding="utf-8")
    fn = html[html.index("function storyStatus(key)"):]
    fn = fn[:fn.index("\n    }\n")]
    assert "'Speaking...'" in fn and "EmmaStory.isActive()" in fn and "'Explaining — ask anytime'" in fn
    status = html[html.index("function status(text) {"):]
    assert status.index("storyStatus(text)") < status.index("}")
    show = html[html.index("function show(state) {"):]
    assert "storyStatus(statusNode.textContent)" in show[:show.index("\n    }\n")]
    assert html.count("'Explaining — ask anytime':") == 2          # Thai and Chinese


def test_a_story_report_goes_to_the_server():
    """showStory hands the engine an onReport that sends `story_report` --
    the server's numbers-only log of timing and frame times from real use."""
    html = PAGE.read_text(encoding="utf-8")
    show = html[html.index("function showStory(story) {"):]
    show = show[:show.index("\n    }\n")]
    assert "onReport: function (r) {" in show
    assert "type: 'story_report', report: r" in show


def test_her_choices_put_a_storys_chips_aside_and_the_pill_follows_the_story_away():
    """Review 13 Oct: her room-type choices (pinned at the bottom) landed on a
    story's own chips, which took the taps; and the pill kept "Explaining"
    after the story left the stage while she still spoke."""
    html = PAGE.read_text(encoding="utf-8")
    assert "html.app-shell body.emma-choices .es-taps { visibility: hidden; pointer-events: none; }" in html
    assert "document.body.classList.toggle('emma-choices', pick);" in html
    assert "right: calc(min(30vw, 300px) + clamp(16px, 3vw, 40px));" in html
    end = html[html.index("function endStory() {"):]
    end = end[:end.index("\n    }\n")]
    assert "if (playing && lastStatusKey === 'Explaining — ask anytime') status('Speaking...');" in end


# ---- her words as she says them: the robot face's motion captions (owner 27 Oct) ----
#
# "อันนี้มันยาวเกินไม่สวย อยากให้ทำเป็น motion ตัวหนังสือ" -- the reply was the
# whole turn's text, painted the moment it came (seconds ahead of her voice),
# growing to 5-7 lines of 16 px over the story; a guest asked her to say it
# again. Now a window of lines (3 beside her, 2 in the band) whose words come
# up with her PLAYED audio. The timing core is the story engine's clock and is
# checked to the word in node (tests/client_captions.cjs, test_stories.py);
# here: the wiring, and the real page in a real browser.

def test_the_caption_hears_her_audio_and_text_and_draws_only_on_the_robot_face():
    html = PAGE.read_text(encoding="utf-8")
    paint = html[html.index("function paintReply() {"):]
    paint = paint[:paint.index("\n    }\n")]
    # the single gate is untouched; the caption only draws what it lets through
    assert "var visible = !!(inCall && curBot);" in paint
    assert "var up = (captions ? captions.paint(visible ? curBot : '') : false) && inCall;" in paint
    assert "replySection.style.display = visible || up ? '' : 'none';" in paint   # a new answer taking over
    # ...and what takes the line down at once: the guest speaking or tapping over her
    for branch in ("case 'speech_started':", "case 'interrupted': {"):
        block = html[html.index(branch):]
        assert "captionHook('clear');" in block[:block.index("break;")], branch
    tap = html[html.index("function sendQuickAsk(id) {"):]
    assert "captionHook('clear');" in tap[:tap.index("ws.send(JSON.stringify({ type: 'quick_ask'")]
    cap = html[html.index("var captions = (function () {"):]
    cap = cap[:cap.index("\n    })();\n")]
    assert "if (!appShell || !replySection" in cap                  # the desk /preview draws as before
    assert "var now = heardClock(), wall = performance.now();" in cap and "var n = c.due(now, wall);" in cap  # her PLAYED audio
    assert "innerHTML" not in cap and "setInterval" not in cap
    assert "timer = setTimeout(sync, 100)" in cap and "if (core.pending())" in cap   # only while words wait
    # the same stream the story hears, from the same places
    at = html.index("function playChunk(arrayBuffer) {")
    assert "captionHook('audio', at, buf.duration);" in html[at:html.index("src.onended", at)]
    assert "captionHook('complete');" in html.split("case 'turn_complete':", 1)[1].split("break;", 1)[0]
    begin = html[html.index("function beginAssistantTurn() {"):]
    assert "captionHook('begin');" in begin[:begin.index("\n    }\n")]
    append = html[html.index("function appendAssistantTranscript(text) {"):]
    append = append[:append.index("\n    }\n")]
    assert append.index("captionHook('text', text);") < append.index("paintReply();")
    clear = html[html.index("function clearTranscript() {"):]
    assert "captionHook('clear');" in clear[:clear.index("\n    }\n")]
    # never the guest's words
    assert "captionHook" not in html.split("case 'user_transcript':", 1)[1].split("break;", 1)[0]
    # one clock for the story's beats and the caption's words
    assert "clock: heardClock," in html
    # light: opacity/transform only, a reduced-motion rule, no blur
    css = html[html.index("/* Her words as she says them (owner 27 Oct"):html.index("/* the status pill: what she is doing")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert ".reply-cap" in css
    for banned in ("infinite", "backdrop-filter", "blur(", "box-shadow", "will-change"):
        assert banned not in css, banned
    for decl in re.findall(r"transition\s*:\s*([^;}]+)", css):
        assert decl.split()[0] in ("opacity", "transform", "none"), decl
    reduced = html[html.index("@media (prefers-reduced-motion: reduce) {\n    html.app-shell .reply.fx-pop"):]
    reduced = reduced[:reduced.index("\n  }\n")]
    assert "html.app-shell .reply-cap-track { transition: none; }" in reduced


def test_a_tap_the_server_drops_falls_back_to_listening():
    """Review 27 Oct: a tap the server drops (its 1.2 s gap, a stale story id)
    gets nothing back, and the page stood on "Thinking..." for good."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required -- the dropped tap's fallback to listening is NOT being checked")
    result = subprocess.run([node, "tests/client_tap_fallback.cjs"], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


_CAPTION_STATE = """() => {
  const box = document.querySelector('.reply-cap'), sec = document.querySelector('.reply');
  const shown = box ? Array.from(box.querySelectorAll('.cw.on')) : [];
  const cs = box ? getComputedStyle(box) : null, r = box ? box.getBoundingClientRect() : null;
  // lines wholly inside the window (mid-slide, the line leaving and the one
  // arriving are each partly in it)
  const tops = new Set(shown.filter(w => { const b = w.getBoundingClientRect();
    return b.top >= r.top - 2 && b.bottom <= r.bottom + 2; }).map(w => Math.round(w.getBoundingClientRect().top)));
  return { has: !!box, shown: shown.map(w => w.textContent).join(''),
           received: document.querySelector('.reply-text').textContent,
           lines: tops.size, pitch: box ? parseFloat(cs.lineHeight) : 0, height: r ? r.height : 0,
           font: box ? parseFloat(cs.fontSize) : 0, width: Math.round(sec.getBoundingClientRect().width),
           below: shown.length ? shown[shown.length - 1].getBoundingClientRect().bottom - r.bottom : 0,
           slid: !!box && getComputedStyle(box.querySelector('.reply-cap-track')).transform !== 'none',
           hidden: sec.style.display === 'none' || getComputedStyle(sec).visibility === 'hidden',
           elapsed: (performance.now() - (window.__t0 || 0)) / 1000 };
}"""


def _answer(text, cps, piece=18, start_ms=300):
    """Her answer as Gemini sends it: each piece just before its own audio,
    the audio 4x faster than it plays. Returns (events, when each character
    of `text` has been said, in seconds after her first audio)."""
    events, said, send, at = [], [], float(start_ms), 0.0
    for k in range(0, len(text), piece):
        p = text[k:k + piece]
        dur = len(p) / cps
        chunks = max(1, int(-(-dur // 0.1)))
        events.append([round(send) - 1, "json", {"type": "assistant_transcript", "text": p}])
        for _ in range(chunks):
            events.append([round(send), "audio", {"ms": dur * 1000 / chunks, "amp": 0.09, "seed": len(events)}])
            send += 100 / 4
        said += [at + (j + 1) * dur / len(p) for j in range(len(p))]
        at += dur
    events.append([round(send + 200), "json", {"type": "turn_complete"}])
    return sorted(events, key=lambda e: e[0]), said


@pytest.fixture(scope="module")
def _chromium_face():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- the motion captions on the real page (the window of "
                    "lines, in step with her voice) are NOT being checked")
    from scripts import record_stories as harness

    server = harness._Server({}, ROOT / "client")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=[
            "--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream", "--disable-webgl", "--disable-3d-apis"])
    except Exception as exc:                               # no Chromium downloaded for playwright
        pw.stop()
        server.close()
        pytest.skip("headless Chromium is not installed (%s) -- the motion captions on the real page are "
                    "NOT being checked" % str(exc).splitlines()[0])
    yield browser, server.port, harness.FAKE_SOCKET
    browser.close()
    pw.stop()
    server.close()


def _in_a_call(chromium, app=True, cue=False, init=None):
    browser, port, fake_socket = chromium
    ctx = browser.new_context(viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
                              permissions=["microphone"])
    ctx.add_init_script(fake_socket)
    if init:
        ctx.add_init_script(init)
    page = ctx.new_page()
    query = [p for p in ("app=1" if app else "", "" if cue else "cue=0") if p]   # the robot's URL has no cue=0
    page.goto("http://127.0.0.1:%d/preview?%s" % (port, "&".join(query)))
    page.wait_for_function("() => document.querySelector('.control.mic') && window.EmmaStory")
    page.wait_for_timeout(1200)                            # /voices + /health
    page.click(".control.mic")
    page.wait_for_function("() => !!window.__callWS && window.__callWS.readyState === 1")
    page.evaluate("""() => window.__emit({type: 'ready', provider: 'gemini', profile: 'condo', voice: 'Kore',
        input_rate: 16000, output_rate: 24000, half_duplex: true, barge_in: false, hold_speech: false,
        quick_asks: {brand: 'Embassy World', langs: [], hint: {}, items: []}})""")
    page.wait_for_timeout(400)
    return ctx, page


_EN = ("We have facilities on three floors. The basement has the spa and the indoor pools. The ground "
       "floor has the Biogenesis fitness by the pool, a Spinning Class studio, a Pilates Studio, Bungee Gym "
       "and Yoga, and Junior World for the children. Which floor would you like to see?")
_TH = ("ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ ชั้นใต้ดินเป็นสปาและสระว่ายน้ำในร่ม "
       "ชั้น G มีฟิตเนส Biogenesis ริมสระ ห้องปั่นจักรยาน พิลาทิสสตูดิโอ บันจี้ยิมและโยคะ")


def _said_up_to(text, said, shown):
    """When she finished the last character on screen (`shown`, the words
    without their spaces), and when she finished the next one."""
    i, n = 0, 0
    while n < len(shown) and i < len(text):
        if not text[i].isspace():
            n += 1
        i += 1
    return (said[i - 1] if i else 0.0), (said[i] if i < len(said) else float("inf"))


@pytest.mark.timeout(120)
def test_her_words_come_with_her_voice_in_a_window_of_lines(_chromium_face):
    ctx, page = _in_a_call(_chromium_face)
    try:
        # beside Emma: the bubble, three lines at most
        events, said = _answer(_EN, 15.0)
        page.evaluate("(ev) => { window.__t0 = window.__play(ev); }", events)
        rows, widths = [], set()
        for _ in range(18):
            page.wait_for_timeout(500)
            s = page.evaluate(_CAPTION_STATE)
            rows.append(s)
            assert s["has"] and s["font"] >= 20
            assert s["lines"] <= 3 and s["height"] <= 3 * s["pitch"] + 1, s
            # her newest word is in the window (at most a line below it, mid-slide)
            assert s["below"] <= s["pitch"] + 2, s
            if s["shown"]:
                widths.add(s["width"])
                played = s["elapsed"] - 0.3
                last, nxt = _said_up_to(_EN, said, s["shown"].replace(" ", ""))
                # never ahead of her voice (but the lead, a tick, the page's own timers) ...
                assert last <= played + 0.25 + 0.35, (round(played, 2), round(last, 2), s["shown"][-30:])
                # ... nor far behind it
                assert nxt >= played - 1.5, (round(played, 2), round(nxt, 2), s["shown"][-30:])
        # the text came ~4x faster than she speaks: most of it waited for her voice
        mid = rows[3]
        assert len(mid["received"]) > 2 * len(mid["shown"]) > 0, (len(mid["received"]), len(mid["shown"]))
        # it slid: far more than three lines said, three on screen
        assert len(rows[-1]["shown"]) > 90 and max(r["lines"] for r in rows[-4:]) == 3 and rows[-1]["slid"]
        # the bubble had its width before the first word: nothing moved sideways
        assert len(widths) == 1, widths
        # the guest speaks: the line goes
        page.evaluate("() => { window.__stop(); window.__emit({type: 'speech_started'}); }")
        page.wait_for_timeout(300)
        assert page.evaluate(_CAPTION_STATE)["hidden"]
        assert page.evaluate("() => document.querySelectorAll('.reply-cap .cw').length") == 0

        # over a card on the stage: the band, two lines, bigger type
        page.evaluate("""() => window.__emit({type: 'tool_result', name: 'show_unit', result: {screen: 'unit',
            unit: {room: 'A-801', floor: 8, type: '1 Bedroom', sqm: 34, status: 'available', status_th: 'x'}}})""")
        events, said = _answer(_TH, 12.5)
        page.evaluate("(ev) => { window.__t0 = window.__play(ev); }", events)
        band = []
        for _ in range(20):
            page.wait_for_timeout(500)
            s = page.evaluate(_CAPTION_STATE)
            band.append(s)
            assert s["lines"] <= 2 and s["height"] <= 2 * s["pitch"] + 1, s
            assert s["below"] <= s["pitch"] + 2, s
        assert page.evaluate("() => document.body.classList.contains('emma-staging')")
        assert band[-1]["font"] >= 26 and max(r["lines"] for r in band[-4:]) == 2
        assert band[-1]["slid"]                            # more than two lines said by now
        assert len(band[-1]["received"].replace(" ", "")) > len(band[-1]["shown"]) > 40
    finally:
        ctx.close()


@pytest.mark.timeout(120)
def test_the_desk_preview_still_paints_her_whole_line(_chromium_face):
    """The motion captions are the robot face's (?app=1); the desk /preview
    keeps its paragraph exactly as it was."""
    ctx, page = _in_a_call(_chromium_face, app=False)
    try:
        page.evaluate("""() => { window.__emit({type: 'assistant_transcript', text: 'Hello, I am Emma.'});
                                 window.__audio(300, 0.09, 0); }""")
        page.wait_for_timeout(300)
        assert page.evaluate("() => document.querySelector('.reply-cap')") is None
        assert page.evaluate("() => document.querySelector('.reply-text').textContent") == "Hello, I am Emma."
        assert page.evaluate("() => getComputedStyle(document.querySelector('.reply')).display") != "none"
    finally:
        ctx.close()


@pytest.mark.timeout(120)
@pytest.mark.parametrize("order", ["text_first", "audio_first"])
def test_a_new_answer_takes_the_line_at_its_own_first_word(_chromium_face, order):
    """Review 27 Oct: gemini-3.8 completes a turn before its tool call and
    speaks again after it, so a new answer often begins while the last one's
    voice is still queued. beginAssistantTurn empties curBot at once: the
    last answer's words vanished with seconds of her voice still to come,
    and the new answer -- timed from its chunk's arrival, not its start --
    stood up whole ~7 s before she said it. Now the old words follow her old
    voice to its end and the new answer takes the line at its first word."""
    ctx, page = _in_a_call(_chromium_face)
    try:
        first = _EN[:150]
        a_events, a_said = _answer(first, 15.0)                       # ~10 s of her voice, sent in ~3 s
        b_text = "Here is the third floor plan for you. "
        b_events, _ = _answer(b_text, 15.0, start_ms=4500)            # after her "tool call"
        if order == "audio_first":
            # her new answer's first sound before its text: beginAssistantTurn
            # runs from the audio, and paintReply sees an empty curBot
            b_events = sorted(([e[0] + 60] + e[1:] if e[1] == "json" else e for e in b_events),
                              key=lambda e: e[0])
        page.evaluate("(ev) => { window.__t0 = window.__play(ev); }", a_events + b_events)
        a_end = 0.3 + a_said[-1]                                      # her first answer's voice ends here
        b_word = a_end + len("Here") / 15.0                          # ...and she has said "Here" here
        a_flat = first.replace(" ", "")
        seen_b = False
        for _ in range(int((b_word + 2.5) / 0.5)):
            page.wait_for_timeout(500)
            s = page.evaluate(_CAPTION_STATE)
            shown = s["shown"].replace(" ", "")
            if 5.0 < s["elapsed"] < a_end - 0.6:
                # the new answer has begun (curBot holds it), her old voice still plays
                assert s["received"].startswith("Here"), s["received"][:30]
                assert not s["hidden"] and shown and a_flat.startswith(shown), (round(s["elapsed"], 2), s["shown"][-40:])
            if s["elapsed"] < b_word - 0.6:
                assert "Here" not in s["shown"], (round(s["elapsed"], 2), s["shown"][-40:])
            if s["elapsed"] > b_word + 1.0:
                assert shown.startswith("Here"), (round(s["elapsed"], 2), s["shown"][-40:])
                seen_b = True
        assert seen_b
    finally:
        ctx.close()


# ---- the robot face's turn edges (audit 27 Oct) ----
#
# A turn that said nothing, a muted mic when her voice ends, an answer with
# audio and no words, a story the engine cannot draw: each left the face
# wrong (the picture gone, "Listening..." over a closed mic, a 10 Hz timer
# for good, an empty stage nobody owned). The wiring here; the real page in
# headless Chromium below.

def test_a_finished_answer_with_no_words_stops_the_captions_look():
    """The hand-over look (an old line still up, a new answer with nothing
    due yet) re-armed every 100 ms for good when the new answer had audio
    and no transcript: it never takes the line. Complete, nothing pending on
    either side: it stops (its next text or turn starts a sync again)."""
    html = PAGE.read_text(encoding="utf-8")
    cap = html[html.index("var captions = (function () {"):]
    cap = cap[:cap.index("\n    })();\n")]
    hand = cap[cap.index("if (handingOver()) {"):]
    hand = hand[:hand.index("fresh();")]
    stop = "if (core.clock.complete && !core.pending() && !drawnFor.pending()) return;"
    assert hand.index("draw(drawnFor, now, wall);") < hand.index(stop) < hand.index("timer = setTimeout(sync, 100);")
    # what starts it again: her next answer's text/audio (paint) and turn_complete
    assert "if (!timer) sync();" in cap and "complete: function () { if (core) { core.complete(); sync(); } }" in cap
    engine = (ROOT / "client" / "assets" / "emma-story.js").read_text(encoding="utf-8")
    assert "Caption.prototype.pending = function () {" in engine and "this.clock = new BeatClock([]);" in engine


def test_a_story_the_engine_cannot_draw_takes_the_screen_back_in_the_wiring():
    html = PAGE.read_text(encoding="utf-8")
    show = html[html.index("function showStory(story) {"):]
    show = show[:show.index("\n    }\n")]
    assert "var drawn = window.EmmaStory.play(story, {" in show
    assert show.rstrip().endswith("if (!drawn) { storyOwns = false; clearStage(); }")


_STAGED = "() => document.body.classList.contains('emma-staging')"
_UNIT_CARD = """() => window.__emit({type: 'tool_result', name: 'show_unit', result: {screen: 'unit',
    unit: {room: 'A-801', floor: 8, type: '1 Bedroom', sqm: 34, status: 'available', status_th: 'x'}}})"""


@pytest.mark.timeout(120)
def test_a_turn_that_said_nothing_keeps_the_screen(_chromium_face):
    """The guest speaks over the picture and the turn ends with nothing said:
    the bare "ค่ะ" the server drops (SILENCE_BARE_ACK), gemini-3.8's empty
    turn_complete before its tool call. That turn_complete cleared the stage
    -- the picture the guest was looking at gone, nothing in its place. An
    answer that did say something still takes it down at its turn_complete."""
    ctx, page = _in_a_call(_chromium_face)
    try:
        page.evaluate(_UNIT_CARD)
        page.wait_for_timeout(200)
        assert page.evaluate(_STAGED)
        page.evaluate("() => window.__emit({type: 'speech_started'})")
        page.wait_for_timeout(200)
        page.evaluate("() => window.__emit({type: 'turn_complete'})")       # no audio, no words
        page.wait_for_timeout(300)
        assert page.evaluate(_STAGED), "a turn that said nothing cleared the screen"
        assert page.evaluate("() => document.querySelectorAll('.vp-stage .vp-card').length") == 1
        # her answer in words, no picture of its own: it owns the screen
        page.evaluate("() => window.__emit({type: 'speech_started'})")
        page.wait_for_timeout(200)
        page.evaluate("() => window.__emit({type: 'assistant_transcript', text: 'Our sales team will confirm it.'})")
        page.wait_for_timeout(200)
        page.evaluate("() => window.__emit({type: 'turn_complete'})")
        page.wait_for_timeout(300)
        assert not page.evaluate(_STAGED)
    finally:
        ctx.close()


_CUES = """(() => {
  // the speak-now cue is the page's only oscillator: two per cue
  const AC = window.AudioContext || window.webkitAudioContext, make = AC.prototype.createOscillator;
  window.__cues = 0;
  AC.prototype.createOscillator = function () { window.__cues++; return make.apply(this, arguments); };
})();"""
_PILL = "() => document.querySelector('[data-emma-status]').textContent"
_STATE_IS = "(s) => document.body.dataset.emmaState === s"


@pytest.mark.timeout(120)
def test_muted_her_answer_ends_on_the_muted_pill_without_the_cue(_chromium_face):
    """Mute while she speaks: when her voice ended, the pill said
    "Listening..." and the speak-now chime played, over a closed mic. Now
    "Muted" and no chime; an interruption while muted rests on "Muted" too.
    Unmuted, her answer still ends on "Listening..." with the chime."""
    ctx, page = _in_a_call(_chromium_face, cue=True, init=_CUES)
    try:
        page.wait_for_function("() => document.querySelector('.control.mic .control-label').textContent === 'Listening'")
        events, _ = _answer("We have facilities on three floors, from the spa to the Sky Pool. ", 15.0)
        page.evaluate("(ev) => window.__play(ev)", events)
        page.wait_for_timeout(1500)
        page.click(".control.mic")                                         # muted while she speaks
        page.wait_for_timeout(300)
        assert page.evaluate(_PILL) == "Speaking..."
        page.wait_for_function(_STATE_IS, arg="listening", timeout=15000)  # her voice has ended
        page.wait_for_timeout(300)
        assert page.evaluate(_PILL) == "Muted"
        assert page.evaluate("() => window.__cues") == 0, "the speak-now chime over a muted mic"
        # interrupted while muted
        events, _ = _answer("The Sky Pool is on the roof of building A, with a view of the sea. ", 15.0)
        page.evaluate("(ev) => window.__play(ev)", events)
        page.wait_for_function(_STATE_IS, arg="speaking", timeout=5000)
        page.wait_for_timeout(800)
        page.evaluate("() => { window.__stop(); window.__emit({type: 'interrupted'}); }")
        page.wait_for_timeout(300)
        assert page.evaluate(_STATE_IS, "listening") and page.evaluate(_PILL) == "Muted"
        # unmuted: the cue and "Listening..." as before
        page.click(".control.mic")
        page.wait_for_timeout(300)
        assert page.evaluate(_PILL) == "Listening..."
        events, _ = _answer("It is open every day. ", 15.0)
        page.evaluate("(ev) => window.__play(ev)", events)
        page.wait_for_function(_STATE_IS, arg="speaking", timeout=5000)
        page.wait_for_function(_STATE_IS, arg="listening", timeout=10000)
        page.wait_for_timeout(300)
        assert page.evaluate(_PILL) == "Listening..."
        assert page.evaluate("() => window.__cues") == 2
    finally:
        ctx.close()


_LOOKS = """(() => {
  // once an answer is done, the caption's look is the page's only 100 ms timer
  const set = window.setTimeout;
  window.__looks = 0;
  window.setTimeout = function (fn, ms) { if (ms === 100) window.__looks++; return set.apply(this, arguments); };
})();"""


@pytest.mark.timeout(120)
@pytest.mark.parametrize("words", [False, True], ids=["audio_no_words", "with_words"])
def test_after_an_answer_the_captions_stop_looking(_chromium_face, words):
    """An answer with words, then one with audio and no transcript at all:
    the second never takes the line, and the caption's 10 Hz look re-armed
    for good (97 looks in 10 s, audit 27 Oct) on a face that must stay light.
    It stops once that answer is complete; the first answer's words stay up.
    With words, the second answer takes the line and the look stops too."""
    ctx, page = _in_a_call(_chromium_face, init=_LOOKS)
    try:
        first = "Sure, let me check the floor plan for you. "
        events, said = _answer(first, 15.0)
        page.evaluate("(ev) => window.__play(ev)", events)
        page.wait_for_timeout(int((0.3 + said[-1]) * 1000) + 1300)
        events, _ = _answer("Here is the plan. ", 15.0, start_ms=0)
        if not words:
            events = [e for e in events if not (e[1] == "json" and e[2]["type"] == "assistant_transcript")]
        page.evaluate("(ev) => window.__play(ev)", events)
        page.wait_for_timeout(3500)                                        # said and complete
        looks = page.evaluate("() => window.__looks")
        page.wait_for_timeout(5000)
        assert page.evaluate("() => window.__looks") - looks == 0
        shown = page.evaluate("() => Array.from(document.querySelectorAll('.reply-cap .cw.on'))"
                              ".map(w => w.textContent).join(' ')")
        assert shown == ("Here is the plan." if words else first.strip())
    finally:
        ctx.close()


@pytest.mark.timeout(120)
def test_a_story_the_engine_cannot_draw_gives_the_screen_back(_chromium_face):
    """EmmaStory.play returns false when it draws nothing -- an unknown
    template; a template that throws on a malformed payload (a null row,
    audit 27 Oct). showStory opened the stage first and ignored it: an empty
    stage under the close button nobody owned, and the story's claim on the
    answer kept the answer's picture off."""
    ctx, page = _in_a_call(_chromium_face)
    try:
        page.evaluate("() => window.__emit({type: 'speech_started'})")
        page.evaluate("""() => window.__emit({type: 'tool_result', name: 'present_story', result: {ok: true,
            screen: 'story', topic: 'x', story: {id: 'x', template: 'no_such_template', params: {}}}})""")
        page.wait_for_timeout(300)
        assert page.evaluate("""() => ({staged: document.body.classList.contains('emma-staging'),
            story: document.body.classList.contains('emma-story'),
            inner: (document.querySelector('.vp-stage-inner') || {}).childElementCount || 0})""") == {
            "staged": False, "story": False, "inner": 0}
        # the same answer's picture is not kept off by a story that never drew
        page.evaluate("""() => window.__emit({type: 'tool_result', name: 'search_condo_info', result: {found: true},
            slide: {url: '/slides/none.png', title_en: 'Sky Pool'}})""")
        page.wait_for_timeout(200)
        assert page.evaluate(_STAGED)
        assert page.evaluate("() => document.querySelectorAll('.vp-stage-inner img').length") == 1
    finally:
        ctx.close()


@pytest.mark.parametrize("script", ["client_audio_start.cjs", "client_voice_lifecycle.cjs"])
def test_the_desk_pages_audio_start_and_lifecycle_checks_pass_in_node(script):
    """These two ran only as CI steps, and went red when startMic() and
    startWakeMode() began opening the mic through openPreferredMic() (and
    reporting through micDenied()) without anybody noticing (audit 27 Oct).
    Run here too, so a stale slice fails the suite on every machine."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required -- %s (the desk page's first greeting before the mic / its socket and "
                    "wake-mic lifecycle) is NOT being checked" % script)
    result = subprocess.run([node, "tests/" + script], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
