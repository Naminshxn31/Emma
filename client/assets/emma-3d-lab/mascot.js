// Emma's 3D mascot on the voice call screen (/preview).
//
// This is the approved GLB (owner sign-off 2026-09-23) rendered with the SAME
// environment, lights, tone mapping and
// selective-glow chain as the lab viewer next door (viewer.js). Every number
// below that describes the LOOK is copied verbatim from that file, including
// the measurement comments, because those values were matched band-by-band
// against the owner's reference sheet. Do not re-light this from theory.
//
// What is intentionally DIFFERENT from viewer.js, and why:
//
//   1. No OrbitControls. The wrap already owns drag / pinch / wheel / Home
//      (voice-preview.html), and a second pointer handler on the canvas would
//      fight it. The canvas is pointer-events:none for the same reason.
//
//   2. The final composite is TRANSPARENT here. viewer.js writes
//      gl_FragColor = vec4(color, 1.0) and adds an opaque studio backdrop, so
//      ported unchanged it would paint a dark rectangle over the page's own
//      gradient — and a dark tile in the staging corner. The backdrop term is
//      dropped and alpha is carried through (model alpha + the glow's own
//      luminance), so Emma floats. The glow was tuned ADDITIVELY over that
//      dark backdrop; over the page's near-black gradient the difference is
//      small, but it is a real deviation from the signed-off sheet and is
//      listed for the owner rather than hidden.
//
//   3. State motion is authored HERE, in JS, on the bones. The GLB ships two
//      clips ('idle', 'look') and six bones (Root/Body/Neck/Head/Arm.L/Arm.R)
//      — NOT the 40 bones / 4 clips its README still claims (measured against
//      the file itself). So "a clip per voice state" is planning against a
//      model that does not exist. 'idle' is the base loop for every state;
//      each state layers cheap procedural offsets on top of the sampled pose.
//      Nothing is ever frozen: the float + breathe layer runs in every state.
//
//   4. There is no window.emma3d inspection API, no skeleton helper, no
//      wireframe, no timeline, no clip buttons, no view buttons, no status
//      writes. Those are lab furniture.
//
// Failure contract: this module NEVER throws out to the page. If WebGL is
// missing or the GLB fails, it returns quietly and the inline SVG puppet —
// which is still fully wired to every data-emma-* attribute — stays visible.
// The ready class (emma-3d-ready) is added only when the renderer AND the
// model have both succeeded, and it is the class that hides the SVG rig.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const wrap = document.querySelector('.mascot-wrap');
const canvas = wrap && wrap.querySelector('[data-emma-3d]');
if (!wrap || !canvas) throw new Error('emma-3d: no mascot canvas');

// WebGLRenderer construction is the one call viewer.js leaves unguarded; a
// machine without a GL context dies there, before its own try/catch around the
// GLB load is ever reached. Here a failure just leaves the SVG puppet up.
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, canvas });
} catch (error) {
  console.warn('emma-3d: WebGL unavailable, keeping the SVG mascot', error);
  throw error;
}

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(33, 1, .05, 50);
const target = new THREE.Vector3(0, 2.35, 0);
// The lab's "reference" framing is (-1.8, 2.65, 9.65) in a WIDE viewport. The
// mascot wrap is 720/860 (portrait), so at that distance Emma reads small with
// air at the sides. The first pass fixed that by moving the camera IN to 8.05,
// which fills the frame but also shortens the lens: at 8.1 units the hover ring
// sits 1.85 below the look-at point and the steeper downward angle projects it
// up against the body, so the gap the owner approved in the lab closes
// (2026-09-24: "ระยะวงฐานที่ดูชิดตัวกว่าในภาพจาก lab"). Same framing without the
// distortion comes from the other knob: stand at the lab's distance and narrow
// the fov by the same ratio. 2*atan(tan(33/2) * 8.12/9.82) = 27.5 deg.
camera.fov = 27.5;
camera.position.set(-1.27, 2.68, 9.73);
camera.updateProjectionMatrix();   // fov was changed after construction
camera.lookAt(target);
// Orbit state, so a drag moves the CAMERA around her the way the lab's
// OrbitControls does (2026-09-24, owner: "ทำไมหมุนเหมือนใน lab ไม่ได้").
// Turning the model instead was wrong in a way that is easy to see once it is
// named: the hover ring is part of the same glTF scene, so it tilted with her
// instead of staying flat on the floor, and the studio reflections rode along
// with the body instead of sweeping across it. Orbiting leaves the ring, the
// lights and the environment where they are, which is what makes the chrome
// read as chrome while it turns.
const HOME_SPHERICAL = new THREE.Spherical().setFromVector3(camera.position.clone().sub(target));
let orbitTheta = HOME_SPHERICAL.theta, orbitPhi = HOME_SPHERICAL.phi;
let viewTheta = orbitTheta, viewPhi = orbitPhi;
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setClearColor(0, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.VSMShadowMap;

const pmrem = new THREE.PMREMGenerator(renderer);
// Graded neutral studio (PART LOOK, 2026-09-23), copied verbatim from
// viewer.js. Measured on the sheet's CHROME sphere (8 vertical bands top->
// bottom: 23 54 153 100 140 120 25 68): the room it reflects is dark straight
// up, bright around +45 deg, mid grey at the horizon, bright again around
// -30 deg (floor bounce) and dark straight down. Card powers are those band
// values as linear radiance.
const studio = new THREE.Scene();
studio.background = new THREE.Color(.006, .006, .0065);
// Three CONTIGUOUS rings — separate cards with gaps made a small sphere mirror
// the black between them. az 0 = +z (toward the camera); index 7 = the key.
const cards = [];
const cardRing = (elevationDeg, radius, count, size, power, sectors = {}) => {
  const elevation = elevationDeg * Math.PI / 180, y = radius * Math.sin(elevation), r = radius * Math.cos(elevation);
  for (let i = 0; i < count; i++) {
    const az = i / count * Math.PI * 2;
    cards.push({ p: [r * Math.sin(az), y, r * Math.cos(az)], s: size, power: sectors[i] ?? power });
  }
};
cardRing(45, 8.5, 8, [5.2, 3.6], 1.2, { 7: 8, 6: 4, 0: 4, 4: 1.5 });  // upper band, key at 7
cardRing(3, 9, 4, [14, 5], .16, { 0: .22 });                          // horizon, mid grey
cardRing(-25, 6.5, 8, [5.4, 2.6], .40, { 0: .55, 1: .5, 7: .5 });     // floor bounce
for (const card of cards) {
  const material = new THREE.MeshBasicMaterial({ color: new THREE.Color().setScalar(card.power), side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(...card.s), material);
  mesh.position.set(...card.p); mesh.lookAt(0, 0, 0); studio.add(mesh);
}
const environment = pmrem.fromScene(studio, .04, .1, 100, { size: 512 });
scene.environment = environment.texture;
scene.environmentIntensity = 1.0;
studio.traverse(o => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
pmrem.dispose();

// Key / fill / rim exactly as approved. The directional key sets the front
// median almost alone; the >240 patch comes from the coat reflecting the
// bright key card above it.
const key = new THREE.DirectionalLight(0xfffdf9, 2.2);
key.position.set(-5, 6, 4); scene.add(key);
key.castShadow = true; key.shadow.mapSize.set(1024, 1024); key.shadow.radius = 5; key.shadow.blurSamples = 8;
Object.assign(key.shadow.camera, { left: -3, right: 3, top: 5, bottom: -1, near: .5, far: 20 });
key.shadow.bias = -.00012; key.shadow.normalBias = .008;
const fill = new THREE.DirectionalLight(0xf4f7ff, .15);
fill.position.set(4, 3, 0); scene.add(fill);
const rim = new THREE.DirectionalLight(0xffffff, .8);
rim.position.set(1, 4, -4); scene.add(rim);

// ---- selective glow, copied from viewer.js ----
const hdrTarget = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, depthBuffer: true, samples: 4 });
const glowTargets = Array.from({ length: 5 }, (_, i) => new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, depthBuffer: i === 0 }));
const screenScene = new THREE.Scene();
const screenCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
const screenVertex = 'varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}';
const glowOccluder = new THREE.MeshBasicMaterial({ color: 0x000000 });
const blurGlow = new THREE.ShaderMaterial({
  uniforms: { source: { value: null }, direction: { value: new THREE.Vector2() } }, vertexShader: screenVertex, depthTest: false, depthWrite: false,
  fragmentShader: `uniform sampler2D source;uniform vec2 direction;varying vec2 vUv;
    void main(){vec3 c=vec3(0.0);float total=0.0;
      for(int i=-8;i<=8;i++){float weight=exp(-float(i*i)/24.5);
        c+=texture2D(source,vUv+direction*float(i)).rgb*weight;total+=weight;}
      gl_FragColor=vec4(c/total,1.0);}`
});
const finishFrame = new THREE.ShaderMaterial({
  uniforms: { source: { value: hdrTarget.texture }, glow: { value: glowTargets[2].texture }, glowWide: { value: glowTargets[4].texture } },
  vertexShader: screenVertex, depthTest: false, depthWrite: false, transparent: true, toneMapped: true,
  // Glow weights near .42 / wide .08 and the blur steps .55 / 2.5 are the
  // approved pair (build 2 at .28/.06 matched the sheet's tail from 12 px out
  // but read 148 at the first pixel against the sheet's 195).
  //
  // THE ONE DELIBERATE DEVIATION from viewer.js: the lab adds
  // `backdrop*(1.0-sampleColor.a)` and writes alpha 1.0, which is an opaque
  // dark studio card. On this page the mascot sits over the call screen's own
  // gradient and steps into the bottom-right corner while a display is staged
  // — an opaque tile there would be obvious. So the backdrop is dropped and
  // alpha is carried: the model's own coverage plus the glow's luminance, so
  // the halo fades out instead of ending at a rectangle edge.
  fragmentShader: `uniform sampler2D source;uniform sampler2D glow;uniform sampler2D glowWide;varying vec2 vUv;
    void main(){vec4 sampleColor=texture2D(source,vUv);
      vec3 near=texture2D(glow,vUv).rgb*.42;
      vec3 wide=texture2D(glowWide,vUv).rgb*.08;
      vec3 color=sampleColor.rgb+near+wide;
      float halo=clamp(max(max(near.r,near.g),near.b)+max(max(wide.r,wide.g),wide.b),0.0,1.0);
      gl_FragColor=vec4(color,clamp(sampleColor.a+halo,0.0,1.0));
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`
});
const screenQuad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), finishFrame);
screenQuad.frustumCulled = false; screenScene.add(screenQuad);
let glowWidth = 1, glowHeight = 1;

function renderScene() {
  renderer.toneMapping = THREE.NoToneMapping;
  renderer.setRenderTarget(hdrTarget); renderer.render(scene, camera);
  // Keep the original meshes as depth occluders so the chest light does not
  // shine through the back of the body.
  const originals = [];
  model?.traverse(o => {
    if (o.isMesh) {
      const luminous = m => m.emissive && (m.emissive.r + m.emissive.g + m.emissive.b) > 0;
      const forGlow = m => (m === glowMaterial && auraMaterial) ? auraMaterial
        : (luminous(m) ? m : glowOccluder);
      originals.push([o, o.material]);
      o.material = Array.isArray(o.material) ? o.material.map(forGlow) : forGlow(o.material);
    }
  });
  const floorVisible = floor.visible;
  floor.visible = false;
  renderer.setRenderTarget(glowTargets[0]); renderer.render(scene, camera);
  originals.forEach(([o, material]) => { o.material = material; });
  floor.visible = floorVisible;
  screenQuad.material = blurGlow;
  blurGlow.uniforms.source.value = glowTargets[0].texture;
  blurGlow.uniforms.direction.value.set(.55 / glowWidth, 0);
  renderer.setRenderTarget(glowTargets[1]); renderer.render(screenScene, screenCamera);
  blurGlow.uniforms.source.value = glowTargets[1].texture;
  blurGlow.uniforms.direction.value.set(0, .55 / glowHeight);
  renderer.setRenderTarget(glowTargets[2]); renderer.render(screenScene, screenCamera);
  // wide tail: blur the near halo again at 2.5x the step
  blurGlow.uniforms.source.value = glowTargets[2].texture;
  blurGlow.uniforms.direction.value.set(2.5 / glowWidth, 0);
  renderer.setRenderTarget(glowTargets[3]); renderer.render(screenScene, screenCamera);
  blurGlow.uniforms.source.value = glowTargets[3].texture;
  blurGlow.uniforms.direction.value.set(0, 2.5 / glowHeight);
  renderer.setRenderTarget(glowTargets[4]); renderer.render(screenScene, screenCamera);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  screenQuad.material = finishFrame;
  renderer.setRenderTarget(null); renderer.render(screenScene, screenCamera);
}

// Soft floor contact cue beneath the GLB's own luminous ring, generated
// locally — no external file.
const shadowCanvas = document.createElement('canvas');
shadowCanvas.width = shadowCanvas.height = 128;
const ctx = shadowCanvas.getContext('2d');
const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
grad.addColorStop(0, 'rgba(222,226,228,0.07)'); grad.addColorStop(.45, 'rgba(180,186,190,0.03)'); grad.addColorStop(1, 'rgba(150,158,162,0)');
ctx.fillStyle = grad; ctx.fillRect(0, 0, 128, 128);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(3.2, 3.2), new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(shadowCanvas), transparent: true, depthWrite: false }));
floor.rotation.x = -Math.PI / 2; floor.position.y = .02; scene.add(floor);

// ---- voice-state motion -----------------------------------------------
//
// One entry per value data-emma-state can hold. setState() in the page
// validates against exactly these ten names, so this table is the whole
// vocabulary — nothing new is invented here.
//
// Every field is an amplitude/rate for the procedural layer below; the 'idle'
// CLIP is the base pose underneath all of them. `clip` picks which of the two
// baked clips plays: 'look' is the attention gesture and is used where the
// state means "I have turned toward you" (greeting, thinking's sweep).
//
//   bob     head/body vertical, px of bone units, at `bobRate` Hz
//   sway    body roll, radians
//   lean    forward lean at the neck, radians (positive = toward the guest)
//   nodRate speaking bob rate, raised by live mouth level
//   turn    steady head yaw amplitude
//   clipYaw scale on the baked clip's OWN yaw (see the note below)
//   armLift shoulder lift on both arms, radians
//   shake   fast head yaw, radians (frustration only)
//
// YAW IS CAPPED, AND THIS IS NOT A TASTE CALL. The face is a flat decal on a
// sphere, so past a certain yaw it rotates off the front of the head: one eye
// is cut by the silhouette edge and the rest of the head reads as a big glossy
// black back-of-head. Measured in real Chrome on /preview, settled loop, face
// centroid offset from head centre in head half-widths:
//
//     thinking .199   loading .208   greeting .212      <- broken
//     every other state .056 .. .086                    <- correct
//
// The three broken states are exactly the three on the 'look' clip, and that is
// the whole story: build-emma-3d-lab.py authors 'look' as a yaw sweep to
// -0.6 / +0.6 rad on the Neck, and the procedural `turn` below is added to the
// SAME bone on top of it. Cutting `turn` alone cannot fix this -- the clip is
// the larger term -- so `clipYaw` damps the baked sweep as well. The energy the
// states lose in yaw is paid back in lean / armLift / bobRate, which cannot
// clip because they never rotate the decal away from camera.
//
// Judge any change to these numbers BY EYE on re-shot frames. The damage does
// not track `turn` monotonically (loading at .20 read better than thinking at
// .17), because what matters is where the decal ends up on the mesh, not the
// parameter. Numbers alone will mislead you here.
const STATE_MOTION = {
  idle:       { clip: 'idle', bob: .022, bobRate: .34, sway: .012, lean: 0,     turn: .10, turnRate: .11, clipYaw: 1,   armLift: 0,    shake: 0 },
  listening:  { clip: 'idle', bob: .020, bobRate: .46, sway: .010, lean: .055,  turn: .05, turnRate: .18, clipYaw: 1,   armLift: .03,  shake: 0 },
  thinking:   { clip: 'look', bob: .018, bobRate: .38, sway: .008, lean: -.030, turn: .09, turnRate: .34, clipYaw: .34, armLift: .08,  shake: 0 },
  loading:    { clip: 'look', bob: .016, bobRate: .34, sway: .006, lean: -.022, turn: .11, turnRate: .26, clipYaw: .34, armLift: .05,  shake: 0 },
  speaking:   { clip: 'idle', bob: .030, bobRate: .92, sway: .016, lean: .030,  turn: .06, turnRate: .42, clipYaw: 1,   armLift: .07,  shake: 0 },
  greeting:   { clip: 'look', bob: .036, bobRate: .78, sway: .022, lean: .058,  turn: .08, turnRate: .46, clipYaw: .30, armLift: .20,  shake: 0 },
  happy:      { clip: 'idle', bob: .046, bobRate: 1.05, sway: .026, lean: .020, turn: .07, turnRate: .55, clipYaw: 1,   armLift: .20,  shake: 0 },
  grateful:   { clip: 'idle', bob: .020, bobRate: .38, sway: .008, lean: .085,  turn: .03, turnRate: .14, clipYaw: 1,   armLift: .11,  shake: 0 },
  error:      { clip: 'idle', bob: .014, bobRate: .28, sway: .010, lean: -.075, turn: .04, turnRate: .12, clipYaw: 1,   armLift: -.05, shake: 0 },
  frustrated: { clip: 'idle', bob: .018, bobRate: .40, sway: .014, lean: -.030, turn: 0,   turnRate: 0,   clipYaw: 1,   armLift: .04,  shake: .05 }
};
const DEFAULT_MOTION = STATE_MOTION.idle;

let model = null, mixer = null, bones = [], poseBones = [], clips = [], rootRestY = 0;
// The chest lamp and the hover ring under her are ONE material in the GLB
// ('White light', on 'Chest / luminous ring' and the 'Torus' the hover ring
// uses), so the mode colour is a single write that lights both — which is the
// pair the owner pointed at (2026-09-24 "ตรงกลางและฐานด้วย").
let glowMaterial = null;
// What the GLOW pass draws in place of the lamp. The halo is generated by
// re-rendering the luminous meshes and blurring them, so a black lamp has
// nothing to blur and loses its aura entirely (owner, 2026-09-24: "สีดำไม่มี
// ออร่าหรอ"). Splitting the two lets the lens be black on screen while the
// halo is still lit — the surface colour and the light it throws stop being
// the same number.
let auraMaterial = null;
// The four 'Chest__...' nodes are the button a finger lands on.
const chestParts = [];
let action = null, currentClip = '', transition = null;
let motion = DEFAULT_MOTION, motionMix = DEFAULT_MOTION;   // motionMix eases toward motion
let phase = 0, mouthLevel = 0, gazeX = 0, gazeY = 0, smoothX = 0, smoothY = 0;
const boneByName = name => bones.find(b => b.name === name);

const snapshot = () => bones.map(b => ({ q: b.quaternion.clone(), p: b.position.clone(), s: b.scale.clone() }));
const applySample = () => bones.forEach((b, i) => {
  b.quaternion.copy(poseBones[i].quaternion);
  b.position.copy(poseBones[i].position);
  b.scale.copy(poseBones[i].scale);
});

function selectClip(name, blend = true) {
  if (!mixer || name === currentClip) return;
  const clip = clips.find(c => c.name === name);
  if (!clip) return;
  // Capture the VISIBLE pose, including an interrupted transition, before
  // stopping actions, so the blend starts where the last frame was drawn.
  const from = blend ? snapshot() : null;
  mixer.stopAllAction();
  currentClip = name;
  action = mixer.clipAction(clip).reset();
  action.setLoop(THREE.LoopRepeat, Infinity);
  action.play();
  mixer.update(0);
  applySample();
  transition = from ? { from, time: 0, duration: .65 } : null;
  if (from) bones.forEach((b, i) => { b.quaternion.copy(from[i].q); b.position.copy(from[i].p); b.scale.copy(from[i].s); });
}

// Damped ease between two motion tables so a state change never snaps.
function blendMotion(dt) {
  const eased = {};
  for (const field of Object.keys(DEFAULT_MOTION)) {
    if (field === 'clip') continue;
    eased[field] = THREE.MathUtils.damp(motionMix[field] ?? 0, motion[field] ?? 0, 3.2, dt);
  }
  eased.clip = motion.clip;
  motionMix = eased;
}

// dt is the ANIMATION clock and is zeroed under prefers-reduced-motion, which
// is what freezes the idle float. handDt is the real clock and is never zeroed:
// the orbit is the person's own hand moving, not decoration, and freezing it
// made the turn look broken while the debug readout showed the angle changing
// (2026-09-24, owner's Windows has reduced motion on; reproduced with
// Playwright reduced_motion="reduce": image delta 0.00 vs 9.27).
function renderStep(dt, handDt = dt) {
  if (mixer && action) {
    mixer.update(dt);
    applySample();
    if (transition) {
      transition.time += dt;
      const t = Math.min(1, transition.time / transition.duration);
      const smooth = t * t * (3 - 2 * t);
      bones.forEach((b, i) => {
        b.quaternion.slerpQuaternions(transition.from[i].q, poseBones[i].quaternion, smooth);
        b.position.lerpVectors(transition.from[i].p, poseBones[i].position, smooth);
        b.scale.lerpVectors(transition.from[i].s, poseBones[i].scale, smooth);
      });
      if (t >= 1) transition = null;
    }

    // Procedural layer, applied AFTER the clip sample (same order as the lab's
    // gaze block). Rule from the brief: nothing may look frozen — the float
    // and the breathe run in EVERY state, including the ones with no special
    // motion of their own, so a still state is calm, not dead.
    blendMotion(dt);
    phase += dt;
    const m = motionMix;
    const body = boneByName('Body');
    const neck = boneByName('Neck');
    const head = boneByName('Head');
    const root = boneByName('Root');
    // Speaking bobs with Emma's own voice: mouthLevel comes from the playback
    // RMS the engine already computes, so the bob follows the syllables rather
    // than a free-running timer.
    const voice = mouthLevel;
    const bobRate = m.bobRate + voice * 1.1;
    const bob = Math.sin(phase * bobRate * Math.PI * 2) * (m.bob + voice * .020);
    const breathe = Math.sin(phase * .52 * Math.PI * 2) * .006;   // always on
    // += would accumulate on any frame the mixer does not rewrite this track,
    // and a bare = threw away the bone's own rest height: the GLB puts Root at
    // y 0.45, so assigning the bob alone dropped the whole character 0.45 into
    // the hover ring, which is a SEPARATE object and does not follow the rig.
    // That is what made the ring look stuck to her underside on the voice page
    // while the lab showed a clear gap (2026-09-24, owner).
    if (root) root.position.y = rootRestY + bob + breathe;
    if (body) {
      body.rotation.z += Math.sin(phase * m.bobRate * Math.PI * 2 * .5) * m.sway;
      body.rotation.x += m.lean * .45;
    }
    if (neck) {
      // Damp the CLIP's own yaw before adding ours. 'look' is authored as a
      // sweep to +/-0.6 rad on this bone (build-emma-3d-lab.py), which on its
      // own is enough to carry the flat face decal off the front of the head.
      // Scaling the sampled value is what makes clipYaw a real cap rather than
      // a smaller number added to an unchanged large one. Read from the
      // sampled pose, not accumulated: applySample() has just overwritten
      // rotation.y this frame, so this multiplies the clip, once, every frame.
      neck.rotation.y *= m.clipYaw ?? 1;
      neck.rotation.x += m.lean;
      neck.rotation.y += Math.sin(phase * m.turnRate * Math.PI * 2) * m.turn;
      if (m.shake) neck.rotation.y += Math.sin(phase * 8.2 * Math.PI * 2) * m.shake;
    }
    if (head) {
      head.rotation.x += Math.sin(phase * (m.bobRate + voice) * Math.PI * 2 + 1.1) * (m.bob * .8 + voice * .05);
    }
    // Pointer gaze: the page's aim() already restricts look tracking to idle
    // and listening; it writes --gaze-x/--gaze-y and this reads the same
    // normalised target, so the two renderers agree about where she is looking.
    smoothX = THREE.MathUtils.damp(smoothX, gazeX, 5, dt);
    smoothY = THREE.MathUtils.damp(smoothY, gazeY, 5, dt);
    if (neck) neck.rotateY(-smoothX * .45);
    if (head) head.rotateX(smoothY * .14);
    // Arms: a lift at the shoulder, eased, so greeting/happy read as open and
    // error reads as dropped without needing bones the model does not have.
    const armL = boneByName('Arm.L');
    const armR = boneByName('Arm.R');
    const flap = Math.sin(phase * (m.bobRate + voice * .8) * Math.PI * 2) * (m.armLift * .35);
    if (armL) armL.rotation.z += m.armLift + flap;
    if (armR) armR.rotation.z -= m.armLift + flap;
  }
  // Inspection orbit, damped like the lab's controls so the turn carries a
  // little after the finger stops instead of snapping.
  viewTheta = THREE.MathUtils.damp(viewTheta, orbitTheta, 9, handDt);
  viewPhi = THREE.MathUtils.damp(viewPhi, orbitPhi, 9, handDt);
  camera.position.setFromSphericalCoords(HOME_SPHERICAL.radius, viewPhi, viewTheta).add(target);
  camera.lookAt(target);
  renderScene();
}

// ---- inspection spin: right-drag, or shift-drag, anywhere on the mascot ----
// The page owns plain dragging (it moves the mascot) and has tests that say so,
// so this listens in the CAPTURE phase and stops the event before the page's
// own pointerdown handler on the same element ever sees it. Plain left-drag is
// untouched and still moves her.
let spinFrom = null;
// Which drag spins and which one moves (2026-09-24, owner: "หมุนไม่ได้นะ").
// The first pass put the spin on right-drag / shift-drag and left the plain
// drag moving her. Both gestures work — measured in real Edge, 8.9 and 8.8 mean
// grey levels of change against the resting frame — but neither is the gesture
// a person reaches for, and on a laptop TOUCHPAD a right-button drag is close
// to impossible. So a plain drag now spins, which is also what the lab viewer
// does, and moving her keeps the modifier.
// TOUCH IS DELIBERATELY EXCLUDED: a pinch is two pointers, and swallowing the
// first one here would leave the page's own handler without its pair and break
// pinch-to-resize on the showroom screen. Touch and pen still move and pinch.
// TOUCH IS IN NOW (2026-09-24, third report). Excluding it protected the
// page's two-finger pinch, but it also meant a finger drag could not turn her,
// and a finger is what a person uses on the showroom screen. So this owns the
// touch gestures instead of dodging them: ONE pointer turns her, and when a
// SECOND arrives the turn is abandoned and the pinch is driven from here
// through the page's own public setScale/moveTo, so there is still one place
// that writes the transform.
const SPIN_GESTURE = event => event.button === 0 && !event.shiftKey;
const touches = new Map();
let pinchFrom = null;
// ON THE DOCUMENT, NOT ON THE WRAP (2026-09-24, second report of "หมุนไม่ได้").
// The pointer lands on .mascot-wrap itself — the canvas and the SVG rig are
// both pointer-events:none, so document.elementFromPoint at the centre returns
// the wrap. When the target IS the element, the DOM runs its capture and bubble
// listeners together in REGISTRATION order, and the page's inline script
// registers its move handler before this module ever executes. A capture-phase
// listener on the wrap therefore did not run first and stopPropagation() could
// not take back a handler that had already moved her. Capturing on the document
// does run first, every time, because that is a real capture phase.
const inWrap = event => wrap === event.target || wrap.contains(event.target);
// ?spindebug=1 draws a small readout of what the pointer is actually doing.
// Three attempts at the turn gesture passed every measurement here and still
// failed on the owner's machine, which means the measurement was in the wrong
// place, not that the owner was wrong. This puts the measurement on HIS screen.
const spinDebug = new URLSearchParams(location.search).has('spindebug');
const debugBox = spinDebug ? document.createElement('div') : null;
if (debugBox) {
  debugBox.style.cssText = 'position:fixed;left:12px;top:12px;z-index:99999;max-width:46ch;' +
    'font:12px/1.5 Consolas,monospace;color:#cfe;background:rgba(6,10,14,.9);' +
    'border:1px solid #2b3a47;border-radius:8px;padding:8px 10px;white-space:pre-wrap;pointer-events:none';
  debugBox.textContent = 'spindebug: waiting for a drag';
  document.body.appendChild(debugBox);
}
const NEWLINE = String.fromCharCode(10);
const debugLines = [];
const say = line => {
  if (!debugBox) return;
  debugLines.push(line);
  if (debugLines.length > 12) debugLines.shift();
  debugBox.textContent = debugLines.join(NEWLINE);
};
if (debugBox) {
  document.addEventListener('pointerdown', event => {
    const el = document.elementFromPoint(event.clientX, event.clientY);
    say(`down type=${event.pointerType} button=${event.button} shift=${event.shiftKey}
` +
        `  target=${event.target.className || event.target.tagName}
` +
        `  topmost=${el ? (el.className || el.tagName) : 'none'}
` +
        `  inWrap=${inWrap(event)} gesture=${SPIN_GESTURE(event)}`);
  }, true);
}


document.addEventListener('pointerdown', event => {
  if (!inWrap(event) || !SPIN_GESTURE(event)) return;
  say('  -> taken, turning');
  event.stopPropagation();
  event.preventDefault();
  touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (touches.size === 2) {
    spinFrom = null;
    const [a, b] = [...touches.values()];
    const api = window.EmmaVoicePreview;
    pinchFrom = { distance: Math.hypot(b.x - a.x, b.y - a.y),
                  transform: api?.getTransform ? api.getTransform() : null };
  } else if (touches.size === 1) {
    spinFrom = { x: event.clientX, y: event.clientY, theta: orbitTheta, phi: orbitPhi };
    tapFrom = chestUnder(event) ? { x: event.clientX, y: event.clientY } : null;
  } else {
    tapFrom = null;                       // a second finger is a pinch, not a tap
  }
  wrap.setPointerCapture(event.pointerId);
}, true);
document.addEventListener('pointermove', event => {
  if (!touches.has(event.pointerId)) return;
  event.stopPropagation();
  touches.set(event.pointerId, { x: event.clientX, y: event.clientY });
  if (touches.size === 2 && pinchFrom) {
    const [a, b] = [...touches.values()];
    const api = window.EmmaVoicePreview;
    const t = pinchFrom.transform;
    if (api?.setScale && t) {
      api.setScale(t.scale * Math.hypot(b.x - a.x, b.y - a.y) / Math.max(1, pinchFrom.distance));
    }
    return;
  }
  if (!spinFrom) return;
  if (debugBox && Math.random() < .08) say(`  move dx=${(event.clientX - spinFrom.x).toFixed(0)} theta=${orbitTheta.toFixed(2)}`);
  // .009 -> .011 rad/px: a drag the width of the mascot itself now carries a
  // full turn (2*PI / 610 px in ?app=1) instead of 315 degrees of one.
  orbitTheta = spinFrom.theta - (event.clientX - spinFrom.x) * .011;
  // MINUS, matching OrbitControls (and so the lab): its rotateUp() does
  // sphericalDelta.phi -= angle, so dragging DOWN lifts the camera and you look
  // down on her. Adding here inverted the vertical axis against every other 3D
  // view the owner uses (2026-09-24: "เหมือนขึ้นลงจะสลับกันนะ").
  // Range is now nearly the full tumble, which is what OrbitControls gives by
  // default and so what the lab gives (minPolarAngle 0, maxPolarAngle PI). The
  // old 0.50..1.90 was me deciding for the owner which views of his own model
  // were worth having, and it is what "หมุน 360 องศาไม่ได้หรอ" was about: the
  // horizontal axis was already unbounded (measured 7.69 rad, 440 deg, in one
  // drag) and this was the axis that stopped. The sliver left at each pole is
  // not taste: lookAt() has no defined up vector exactly there and the view rolls.
  orbitPhi = Math.max(.05, Math.min(3.09, spinFrom.phi - (event.clientY - spinFrom.y) * .008));
}, true);
// ---- her chest lamp is a button (owner, 2026-09-24: "ให้เปลี่ยนโดยกดปุ่ม
// ตรงกลาง ... แต่ถ้าจะเปลี่ยนต้องกดที่ตรงกลางเท่านั้น") ----
// Raycast, not a rectangle: she can be dragged, scaled and turned, and a box in
// page coordinates would drift off the button the first time any of that
// happens. The ray asks the model itself what is under the finger, so the hit
// follows every transform for free — and a tap on her head or her arm is NOT a
// mode change, which is the half of the request that matters.
const raycaster = new THREE.Raycaster();
const pointerNDC = new THREE.Vector2();
const chestUnder = event => {
  if (!chestParts.length || !renderer) return false;
  const box = renderer.domElement.getBoundingClientRect();
  if (!box.width || !box.height) return false;
  pointerNDC.set(((event.clientX - box.left) / box.width) * 2 - 1,
                 -((event.clientY - box.top) / box.height) * 2 + 1);
  raycaster.setFromCamera(pointerNDC, camera);
  return raycaster.intersectObjects(chestParts, true).length > 0;
};
// A tap is a press that did not turn her: the spin stays exactly as it was, and
// only a press that goes down AND up on the chest within a few pixels counts.
let tapFrom = null;
const TAP_SLOP = 6;

const endSpin = event => {
  const tapped = event.type === 'pointerup' && tapFrom
    && Math.abs(event.clientX - tapFrom.x) <= TAP_SLOP
    && Math.abs(event.clientY - tapFrom.y) <= TAP_SLOP;
  tapFrom = null;
  if (!touches.delete(event.pointerId) && !spinFrom) return;
  if (!touches.size) { spinFrom = null; pinchFrom = null; }
  if (wrap.hasPointerCapture?.(event.pointerId)) wrap.releasePointerCapture(event.pointerId);
  // The page owns what a tap MEANS; this only reports where it landed.
  if (tapped) wrap.dispatchEvent(new CustomEvent('emma-chest-tap', { bubbles: true }));
};
document.addEventListener('pointerup', endSpin, true);
document.addEventListener('pointercancel', endSpin, true);
document.addEventListener('lostpointercapture', endSpin, true);
// Without this a right-drag ends in the browser's context menu.
wrap.addEventListener('contextmenu', event => event.preventDefault());
// Home already resets the page's move/scale; resetting the spin with it means
// one key puts everything back rather than two habits to remember.
wrap.addEventListener('keydown', event => {
  if (event.key === 'Home') { orbitTheta = HOME_SPHERICAL.theta; orbitPhi = HOME_SPHERICAL.phi; }
});

// ---- sizing: driven by the WRAP, never a constant. This single observer is
// what makes the default min(88vw,360px), the app-shell min(72vw,610px), the
// <=520px min(88vw,410px), the max-height:760px min(70vw,285px) and the
// staging min(30vw,150px) all work with no further code. ----
function resize() {
  const { width, height } = wrap.getBoundingClientRect();
  if (!width || !height) return;
  // The staging corner is ~130-150px wide. A 0.3x glow buffer there is ~40
  // texels across and the 17-tap blur goes visibly blocky, so the pixel ratio
  // is eased down and the glow scale eased UP on small canvases.
  const small = Math.min(width, height) < 210;
  renderer.setPixelRatio(small ? Math.min(devicePixelRatio, 1.5) : Math.min(devicePixelRatio, 2));
  camera.aspect = width / height; camera.updateProjectionMatrix();
  renderer.setSize(width, height, false);
  const pixelRatio = renderer.getPixelRatio();
  const glowScale = small ? .6 : .3;
  hdrTarget.setSize(Math.max(1, Math.round(width * pixelRatio)), Math.max(1, Math.round(height * pixelRatio)));
  glowWidth = Math.max(1, Math.round(width * pixelRatio * glowScale));
  glowHeight = Math.max(1, Math.round(height * pixelRatio * glowScale));
  glowTargets.forEach(t => t.setSize(glowWidth, glowHeight));
}
new ResizeObserver(resize).observe(wrap);
resize();

// ---- load the approved model ----
// emma-light-68k.glb is the SAME sculpt at a coarser grid: 68,322 triangles
// against 162,380, 2.38 MB against 5.13 MB. Same 29 nodes, 22 meshes, 6
// materials, 2 clips, 1 skin, same names -- checked, because a reduction that
// silently renames a node takes the chest button with it (the tap target is
// found by the name 'Chest__...'). The shape is unchanged; a x6 amplified pixel
// diff puts the whole difference on the chrome highlight and the silhouette
// edge (mean 7.87/255, .tmp/stage-design-20260924/poly-compare.png).
// emma.glb, the 162k original, is still beside it: switching back is this one
// line -- kept switchable on purpose, the same way the old arms were.
// Measured on an RTX 5060 at 900x1250, median frame time: 3.30 ms full vs
// 2.60 ms light, and the glow chain costs 0.50 ms on the full model against
// 0.10 ms on this one, because that pass submits the whole model a second time.
try {
  const gltf = await new GLTFLoader().loadAsync('/assets/emma-3d-lab/emma-light-68k.glb');
  model = gltf.scene; clips = gltf.animations;
  // Per-material environment intensity, VERBATIM from viewer.js. NOTE, and do
  // not "fix" this: 'Graphite joints' and 'Soft white glove' match no material
  // in the current GLB (the gloves left with the arms — ARMS=False in
  // build-emma-3d-lab.py), so they fall through to 1.0, and receiveShadow is
  // therefore always false. That IS the render the owner signed off. Renaming
  // the keys to the GLB's real names ('Graphite satin', 'Black gloss detail')
  // would silently change the calibrated look.
  const envIntensity = { 'Polished chrome': 2.2, 'Graphite joints': 1.0, 'Black chrome eyes': 1.0, 'Pearl enamel': .45, 'Soft white glove': .8 };
  model.traverse(o => {
    if (o.isMesh) for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      o.castShadow = m.name !== 'White light';
      o.receiveShadow = m.name === 'Soft white glove';
      m.envMapIntensity = envIntensity[m.name] ?? 1.0;
    }
  });
  model.traverse(o => {
    if (!o.isMesh) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) {
      if (m.name === 'White light') glowMaterial = m;
    }
    // 'Chest__' with underscores: GLTFLoader sanitises node names, so the
    // GLB's 'Chest / luminous ring' arrives as 'Chest__luminous_ring'. Matching
    // the GLB spelling found nothing and the button silently did not exist —
    // measured, not guessed (hasChestButton() was false with setAccent working).
    if (o.name.startsWith('Chest__')) chestParts.push(o);
  });
  model.traverse(o => { if (o.isBone) bones.push(o); });
  // Read the rest height BEFORE any clip or procedural motion touches it.
  rootRestY = bones.find(b => b.name === 'Root')?.position.y ?? 0;
  scene.add(model);
  // Sample on a SEPARATE hierarchy. AnimationMixer caches constant properties;
  // overwriting its own bones for a transition leaves stale cached values and
  // a static track snaps when it resumes. Only the visible rig is blended.
  const poseSource = model.clone(true);
  poseSource.traverse(o => { if (o.isBone) poseBones.push(o); });
  mixer = new THREE.AnimationMixer(poseSource);
  selectClip('idle', false);
  applyState(wrap.dataset.emmaState || 'listening');
  wrap.classList.add('emma-3d-ready');
  wrap.dispatchEvent(new CustomEvent('emma-3d-ready'));
} catch (error) {
  // One warning, no spam, no broken canvas: the SVG puppet is still up because
  // emma-3d-ready was never added, and the wrap keeps its full drag/resize
  // behaviour either way.
  console.warn('emma-3d: model failed to load, keeping the SVG mascot', error);
  throw error;
}

function applyState(name) {
  motion = STATE_MOTION[name] || DEFAULT_MOTION;
  selectClip(motion.clip);
}

// ---- the page drives this, through the attributes it already sets ----
//
// No new state vocabulary and no second channel: the renderer observes
// data-emma-state on the wrap (setState writes it) and listens for the
// 'emma-mouth-level' event the page already dispatches for lip-sync. That
// keeps window.EmmaVoicePreview frozen exactly as it is, and means the SVG
// fallback and the model are always reading the same source of truth.
new MutationObserver(() => applyState(wrap.dataset.emmaState || 'listening'))
  .observe(wrap, { attributes: true, attributeFilter: ['data-emma-state'] });
wrap.addEventListener('emma-mouth-level', event => {
  mouthLevel = Math.max(0, Math.min(1, (event.detail && event.detail.level) || 0));
});

// The page's aim() writes --gaze-x / --gaze-y (idle + listening only, and it
// bails entirely under prefers-reduced-motion). Reading them keeps one clock:
// head tracking and the CSS tilt cannot drift apart.
addEventListener('pointermove', event => {
  const state = wrap.dataset.emmaState;
  if (state !== 'idle' && state !== 'listening') { gazeX = 0; gazeY = 0; return; }
  gazeX = (event.clientX / innerWidth - .5) * 2;
  gazeY = (event.clientY / innerHeight - .5) * 2;
}, { passive: true });
document.documentElement.addEventListener('pointerleave', () => { gazeX = 0; gazeY = 0; });

// prefers-reduced-motion: the page's own tilt loop returns early under this
// query, so the model must not keep swinging either. It still renders — a
// frozen mascot would be a worse answer than a still one — but the procedural
// layer and the clip are held at rest.
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
const clock = new THREE.Clock();
renderer.setAnimationLoop(() => {
  // A hidden tab used to cost nothing (the mascot was SVG). Skip the six draws
  // while nobody is looking; the clock's delta is clamped so the animation
  // does not jump on return.
  if (document.hidden) { clock.getDelta(); return; }
  const dt = Math.min(clock.getDelta(), .05);
  renderStep(reduceMotion.matches ? 0 : dt, dt);
});

addEventListener('pagehide', () => {
  renderer.setAnimationLoop(null);
  environment.dispose();
  hdrTarget.dispose(); glowTargets.forEach(t => t.dispose());
  glowOccluder.dispose(); blurGlow.dispose(); finishFrame.dispose();
  screenQuad.geometry.dispose(); floor.geometry.dispose(); floor.material.dispose();
  renderer.dispose();
});

// The page owns the modes, the model owns the pixels: one entry point, so the
// button and the colour it switches cannot drift into two places.
window.EmmaMascot = Object.freeze({
  // `aura` is optional and only earns its keep for dark lamps: without it the
  // halo is the lamp's own emissive, which is what every lit mode wants.
  setAccent(colour, aura) {
    if (!glowMaterial) return false;      // model still loading, or SVG fallback
    glowMaterial.color.set(colour);
    glowMaterial.emissive.set(colour);
    if (!aura) { auraMaterial = null; return true; }
    // Basic, not Standard: this material is never lit or shaded — it exists to
    // be a flat shape for the blur to smear into a halo.
    if (!auraMaterial) auraMaterial = new THREE.MeshBasicMaterial({ toneMapped: false });
    auraMaterial.color.set(aura);
    return true;
  },
  hasChestButton: () => chestParts.length > 0,
});
