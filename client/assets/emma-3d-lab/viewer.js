import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const viewport = $('viewport');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(33, 1, .05, 50);
const target = new THREE.Vector3(0, 2.35, 0);
camera.position.set(-1.8, 2.65, 9.65);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setClearColor(0, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.0;
renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.VSMShadowMap;
viewport.appendChild(renderer.domElement);
const pmrem = new THREE.PMREMGenerator(renderer);
// Graded neutral studio (PART LOOK, 2026-09-23). Measured on the sheet's CHROME sphere
// (materials panel, 8 vertical bands top->bottom: 23 54 153 100 140 120 25 68, bg 23). A
// front-lit sphere at band height s mirrors the direction with z = 2s*sqrt(1-s^2), so the
// room the sheet's chrome reflects is dark straight up, bright around +45 deg, mid grey at
// the horizon, bright again around -30 deg (floor bounce) and dark straight down.
// The previous seven small cards (power 4-12 on black) gave a small sphere only "white
// blob or black": shoulder balls and cuffs read as black barrels with white stripes. Card
// powers below are the sheet's band values as linear radiance (sRGB^2.2 / .96 chrome
// albedo: 153 -> .34, 100 -> .13, 140 -> .28, 68 -> .06); three.js ACES at exposure 1
// returns .34/.13/.28 as 172/104/158 sRGB, i.e. the sheet's bands within 20. Only the key
// softbox sits above its band (2.4) because the sheet's chrome has 17 % of its pixels
// > 240 (clipped reflections of the lights) and the WHITE BODY highlight centroid is
// upper-left (-0.19,-0.38 r).
const studio = new THREE.Scene();
studio.background = new THREE.Color(.006,.006,.0065);
// Three CONTIGUOUS rings of cards (build 3 used 13 separate cards with gaps: the balls'
// bright bands then read 103/96 with only 11 % / 4 % of the ball > 200, because a small
// sphere mirrors the black gaps between cards; the sheet's bands are 153/140 with 25 %
// > 200). Each ring's cards overlap slightly (8 x 5.2 on a 37.7 circumference, etc.).
// az 0 = +z (toward the camera); index 7 = upper-left-front = the key sector.
const cards = [];
const cardRing = (elevationDeg, radius, count, size, power, sectors={}) => {
  const elevation = elevationDeg*Math.PI/180, y = radius*Math.sin(elevation), r = radius*Math.cos(elevation);
  for (let i=0;i<count;i++) {
    const az = i/count*Math.PI*2;
    cards.push({p:[r*Math.sin(az), y, r*Math.cos(az)], s:size, power:sectors[i] ?? power});
  }
};
// Upper ring: round 4 (.9, key sector 2.4) read the balls' bands 145/139 = the sheet's
// 153/140 but only 1 % of the ball > 240 vs the sheet's 17 %; round 5 (whole ring 1.5)
// lifted the bands to 158/140 and 21 % > 200 without clipping anything (F0 .56 x 2.2 x
// 1.5 = 1.85 lands at ~230 after ACES) while flattening the pearl (p05 221). So the ring
// stays at 1.2 and the clipped reflection lives in the key sector and its neighbours,
// where the sheet's WHITE BODY highlight is too. Round 6 (3.5 / 2.6) still clipped only
// 2.7 % of a ball -> key card 8, neighbours 4: on chrome that is 8 x .56 x 2.2 = 9.9
// pre-tonemap (clipped), and on the pearl its coat + base specular (~5 % of 8 = .4) is
// what builds the sheet's broad >240 patch without lifting the diffuse median.
cardRing(45, 8.5, 8, [5.2,3.6], 1.2, {7:8, 6:4, 0:4, 4:1.5});  // upper band (+33..+57 deg), key at 7
cardRing(3, 9, 4, [14,5], .16, {0:.22});                       // horizon, mid grey
cardRing(-25, 6.5, 8, [5.4,2.6], .40, {0:.55, 1:.5, 7:.5});    // lower band (-14..-37 deg), floor bounce
for(const card of cards) {
  const material = new THREE.MeshBasicMaterial({color:new THREE.Color().setScalar(card.power),side:THREE.DoubleSide});
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(...card.s),material);
  mesh.position.set(...card.p);mesh.lookAt(0,0,0);studio.add(mesh);
}
const environment = pmrem.fromScene(studio, .04, .1, 100, {size:512});
scene.environment = environment.texture;
scene.environmentIntensity = 1.0;
studio.traverse(o=>{if(o.isMesh){o.geometry.dispose();o.material.dispose();}});pmrem.dispose();
// Directional key: 4.5 with the old cards clipped the pearl to a flat 245-249; 2.0 with
// the graded studio read p50 213, top band 218, 1.6 % > 240 (sheet WHITE BODY p50 225,
// top 244, 23 % > 240). Through three.js ACES a 244 top needs ~2.2 pre-tonemap radiance.
// Key 3.8 + pearl env 1.6 read p50 229 but a narrow spread (p05 209, 4 % > 240, top 231);
// the sheet's sphere runs 162..253. Directional diffuse here is albedo*I*cos/pi = .79 at
// the crown for 3.8. 6.5 overshot the front view (p50 239, 44 % > 240) while the quarter
// view sat on the sheet (226, 11 %, p05 159): 5.0 with the pearl env at 1.0 read 236 /
// 17 % front, 225 / 6 % quarter; 3.8 with pearl env .85 read 234 / 5.5 %, p05 221: the
// contiguous rings light a white sphere too evenly for the sheet's 162..253 range. Key 6.0
// with pearl env .5 read 241 / 57 % > 240 / p05 229: measured, the directional key sets the
// front median almost alone (3.8 -> 234, 5.0 -> 236, 6.0 -> 241, whatever the env), so the
// median is taken toward the sheet's 225 with a weak key and the >240 patch comes from the
// coat's reflection of the bright key card above (2.6 + env .55 read 237 / 28 % / p05 222
// front and 228 / 14 % / 195 quarter; the last trim is 2.2 + env .45).
const key = new THREE.DirectionalLight(0xfffdf9, 2.2);
key.position.set(-5, 6, 4); scene.add(key);
key.castShadow=true;key.shadow.mapSize.set(1024,1024);key.shadow.radius=5;key.shadow.blurSamples=8;
Object.assign(key.shadow.camera,{left:-3,right:3,top:5,bottom:-1,near:.5,far:20});
key.shadow.bias=-.00012;key.shadow.normalBias=.008;
const fill = new THREE.DirectionalLight(0xf4f7ff, .15);
fill.position.set(4, 3, 0); scene.add(fill);
const rim = new THREE.DirectionalLight(0xffffff, .8);
rim.position.set(1, 4, -4); scene.add(rim);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(target); controls.enableDamping = true;
controls.minDistance = 3; controls.maxDistance = 15;
controls.enablePan = false; controls.update();

// Bloom is extracted from the HDR render of the real model, so the halo follows
// the luminous ring and chest at every camera angle. All shaders run locally.
const hdrTarget = new THREE.WebGLRenderTarget(1,1,{type:THREE.HalfFloatType,depthBuffer:true,samples:4});
// Five glow targets: luminous pass, H/V blur (near halo), and a second H/V blur at x2.5 the
// step (wide tail). Sheet hover ring halo (2 px steps below a 12 px tube, bg 27): 214 158
// 127 101 81 66 56 49 43 40, still 10 over bg 50 px out at the sides; one Gaussian gave
// the viewer 157 at the edge and background by 25 px (ring 2/3 the sheet's size).
const glowTargets = Array.from({length:5},(_,i)=>new THREE.WebGLRenderTarget(1,1,{type:THREE.HalfFloatType,depthBuffer:i===0}));
const screenScene = new THREE.Scene();
const screenCamera = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
const screenVertex = 'varying vec2 vUv; void main(){vUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}';
const glowOccluder = new THREE.MeshBasicMaterial({color:0x000000});
const blurGlow = new THREE.ShaderMaterial({
  uniforms:{source:{value:null},direction:{value:new THREE.Vector2()}},vertexShader:screenVertex,depthTest:false,depthWrite:false,
  fragmentShader:`uniform sampler2D source;uniform vec2 direction;varying vec2 vUv;
    void main(){vec3 c=vec3(0.0);float total=0.0;
      for(int i=-8;i<=8;i++){float weight=exp(-float(i*i)/24.5);
        c+=texture2D(source,vUv+direction*float(i)).rgb*weight;total+=weight;}
      gl_FragColor=vec4(c/total,1.0);}`
});
const finishFrame = new THREE.ShaderMaterial({
  uniforms:{source:{value:hdrTarget.texture},glow:{value:glowTargets[2].texture},glowWide:{value:glowTargets[4].texture}},
  vertexShader:screenVertex,depthTest:false,depthWrite:false,toneMapped:true,
  // Backdrop (PART LOOK): the sheet's hero corners are neutral (22,25,26) sRGB, the old
  // teal (.0028,.004,.0052) came out as (3,6,9). Measured: .0205 pre-tonemap rendered as
  // 27 through three.js ACES, so .016 for 22; pool centre a little lighter, the ring's
  // floor glow does the rest (sheet under the ring: 85).
  // Glow weights: near .24 + wide .14 (build 1) gave a 132 plateau for 30 px under the ring
  // where the sheet falls 214 158 127 101 81 66 56. Build 2 (.28/.06): the tail right of
  // the ring matched the sheet within 10 from 12 px out, but the first pixel read 148 vs
  // 195 -> the near blur is tightened (step .55) and weighted .42, wide .08.
  fragmentShader:`uniform sampler2D source;uniform sampler2D glow;uniform sampler2D glowWide;varying vec2 vUv;
    void main(){vec4 sampleColor=texture2D(source,vUv);
      float pool=exp(-3.5*dot(vUv-vec2(.5,.52),vUv-vec2(.5,.52)));
      vec3 backdrop=mix(vec3(.016,.0188,.020),vec3(.028,.031,.032),pool);
      vec3 color=sampleColor.rgb+backdrop*(1.0-sampleColor.a)+texture2D(glow,vUv).rgb*.42+texture2D(glowWide,vUv).rgb*.08;
      gl_FragColor=vec4(color,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`
});
const screenQuad = new THREE.Mesh(new THREE.PlaneGeometry(2,2),finishFrame);
screenQuad.frustumCulled=false;screenScene.add(screenQuad);
let glowWidth=1,glowHeight=1;
function renderScene() {
  renderer.toneMapping=THREE.NoToneMapping;
  renderer.setRenderTarget(hdrTarget);renderer.render(scene,camera);
  // Keep the original meshes as depth occluders: the chest light must not shine
  // through the back of the body when the viewer turns the model around.
  const originals=[];
  model?.traverse(o=>{if(o.isMesh){
    const luminous=m=>m.emissive&&(m.emissive.r+m.emissive.g+m.emissive.b)>0;
    originals.push([o,o.material]);
    o.material=Array.isArray(o.material)?o.material.map(m=>luminous(m)?m:glowOccluder):(luminous(o.material)?o.material:glowOccluder);
  }});
  const floorVisible=floor.visible,helperVisible=helper?.visible;
  floor.visible=false;if(helper)helper.visible=false;
  renderer.setRenderTarget(glowTargets[0]);renderer.render(scene,camera);
  originals.forEach(([o,material])=>{o.material=material;});
  floor.visible=floorVisible;if(helper)helper.visible=helperVisible;
  screenQuad.material=blurGlow;
  blurGlow.uniforms.source.value=glowTargets[0].texture;
  blurGlow.uniforms.direction.value.set(.55/glowWidth,0);
  renderer.setRenderTarget(glowTargets[1]);renderer.render(screenScene,screenCamera);
  blurGlow.uniforms.source.value=glowTargets[1].texture;
  blurGlow.uniforms.direction.value.set(0,.55/glowHeight);
  renderer.setRenderTarget(glowTargets[2]);renderer.render(screenScene,screenCamera);
  // wide tail: blur the near halo again at 2.5x the step
  blurGlow.uniforms.source.value=glowTargets[2].texture;
  blurGlow.uniforms.direction.value.set(2.5/glowWidth,0);
  renderer.setRenderTarget(glowTargets[3]);renderer.render(screenScene,screenCamera);
  blurGlow.uniforms.source.value=glowTargets[3].texture;
  blurGlow.uniforms.direction.value.set(0,2.5/glowHeight);
  renderer.setRenderTarget(glowTargets[4]);renderer.render(screenScene,screenCamera);
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  screenQuad.material=finishFrame;
  renderer.setRenderTarget(null);renderer.render(screenScene,screenCamera);
}

// A soft floor contact cue beneath the actual GLB ring, generated locally.
const shadowCanvas = document.createElement('canvas');
shadowCanvas.width = shadowCanvas.height = 128;
const ctx = shadowCanvas.getContext('2d');
const grad = ctx.createRadialGradient(64,64,0,64,64,64);
// Pool alpha .14 -> .10 -> .07 and neutral: with the brighter ring glow the floor read 132,
// then 105, for 30 px under the ring; the sheet's hero pool is 85 there (neutral grey).
grad.addColorStop(0,'rgba(222,226,228,0.07)'); grad.addColorStop(.45,'rgba(180,186,190,0.03)'); grad.addColorStop(1,'rgba(150,158,162,0)');
ctx.fillStyle = grad; ctx.fillRect(0,0,128,128);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(3.2,3.2),new THREE.MeshBasicMaterial({map:new THREE.CanvasTexture(shadowCanvas),transparent:true,depthWrite:false}));
floor.rotation.x = -Math.PI/2; floor.position.y = .02; scene.add(floor);

let model, mixer, helper, bones = [], poseBones = [], clips = [], action, current = 'idle';
let transition = null, playing = true, elapsed = 0;
let gazeX = 0, gazeY = 0, smoothX = 0, smoothY = 0;
// wave/grip left the model with the arms (ARMS = False in build-emma-3d-lab.py);
// their labels stay so the pair comes back with one edit on each side.
const labels = {idle:'ท่าพัก',look:'หันหัว',wave:'โบกมือ',grip:'กำ–คลายมือ'};
const snapshot = () => bones.map(b => ({q:b.quaternion.clone(),p:b.position.clone(),s:b.scale.clone()}));
const applySample = () => bones.forEach((b,i) => {
  b.quaternion.copy(poseBones[i].quaternion);
  b.position.copy(poseBones[i].position);
  b.scale.copy(poseBones[i].scale);
});

function selectClip(name, blend = true) {
  if (!mixer) return;
  const clip = clips.find(c => c.name === name);
  if (!clip) throw new Error(`Missing animation: ${name}`);
  // Capture the VISIBLE pose, including an interrupted transition, before stopping
  // actions. Both fingertips and wrist start exactly where they were last drawn.
  const from = blend ? snapshot() : null;
  mixer.stopAllAction();
  current = name;
  action = mixer.clipAction(clip).reset();
  action.setLoop($('loop').checked ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
  action.clampWhenFinished = true; action.play();
  elapsed = 0; playing = true;
  mixer.update(0);
  applySample();
  transition = from ? {from,time:0,duration:.65} : null;
  if (from) bones.forEach((b,i) => {b.quaternion.copy(from[i].q);b.position.copy(from[i].p);b.scale.copy(from[i].s);});
  $('clipLabel').textContent = labels[name];
  $('play').textContent = 'หยุด'; $('play').setAttribute('aria-pressed','true');
  document.querySelectorAll('[data-clip]').forEach(b => b.setAttribute('aria-pressed',String(b.dataset.clip===name)));
}

function renderStep(dt) {
  if (mixer && action) {
    if (playing) {
      elapsed += dt;
      mixer.update(dt);
      applySample();
      if (transition) {
        transition.time += dt;
        const t = Math.min(1,transition.time/transition.duration);
        const eased = t*t*(3-2*t);
        bones.forEach((b,i) => {
          b.quaternion.slerpQuaternions(transition.from[i].q,poseBones[i].quaternion,eased);
          b.position.lerpVectors(transition.from[i].p,poseBones[i].position,eased);
          b.scale.lerpVectors(transition.from[i].s,poseBones[i].scale,eased);
        });
        if (t>=1) transition=null;
      }
      if ($('gaze').checked && current==='idle' && !transition) {
        smoothX = THREE.MathUtils.damp(smoothX,gazeX,5,dt);
        smoothY = THREE.MathUtils.damp(smoothY,gazeY,5,dt);
        const neck = bones.find(b=>b.name==='Neck');
        const head = bones.find(b=>b.name==='Head');
        if (neck) neck.rotateY(-smoothX*.45);
        if (head) head.rotateX(smoothY*.14);
      }
      $('timeline').value = action.time/action.getClip().duration;
    }
    $('time').textContent = `${action.time.toFixed(2)} / ${action.getClip().duration.toFixed(2)} s`;
    $('timeline').value = action.time/action.getClip().duration;
  }
  controls.update(); renderScene();
}

const clock = new THREE.Clock();
let manual = new URLSearchParams(location.search).has('manual');
renderer.setAnimationLoop(() => renderStep(manual ? 0 : Math.min(clock.getDelta(), .05)));
new ResizeObserver(() => {
  const {width,height} = viewport.getBoundingClientRect();
  camera.aspect = width/height; camera.updateProjectionMatrix();
  renderer.setSize(width,height);
  const pixelRatio=renderer.getPixelRatio();
  hdrTarget.setSize(Math.max(1,Math.round(width*pixelRatio)),Math.max(1,Math.round(height*pixelRatio)));
  glowWidth=Math.max(1,Math.round(width*pixelRatio*.3));glowHeight=Math.max(1,Math.round(height*pixelRatio*.3));
  glowTargets.forEach(t=>t.setSize(glowWidth,glowHeight));
}).observe(viewport);

try {
  const gltf = await new GLTFLoader().loadAsync('./emma.glb');
  model = gltf.scene; clips = gltf.animations;
  // Chrome base colour in the GLB is real chrome F0 (.56, set in Blender so the balls stop
  // mirroring the white body at full strength); the viewer has no inter-reflection, so its
  // chrome takes the calibrated studio at 2.2 (= 1.23 net: build 2 at 1.0 x .96 read the
  // balls' bright bands 125/117 vs the sheet's 153/140 and 2 % > 240 vs 17 %). Black parts
  // 1.0; pearl .5 (1.6 flattened it to 209..239, .85 to 221..241; sheet 162..253 - its
  // shading comes from the directional key); gloves .8 (they hang below the key's reach).
  const envIntensity={'Polished chrome':2.2,'Graphite joints':1.0,'Black chrome eyes':1.0,'Pearl enamel':.45,'Soft white glove':.8};
  model.traverse(o=>{if(o.isMesh)for(const m of Array.isArray(o.material)?o.material:[o.material]){
    o.castShadow=m.name!=='White light';o.receiveShadow=m.name==='Soft white glove';
    m.envMapIntensity=envIntensity[m.name]??1.0;
  }});
  model.traverse(o => {if(o.isBone) bones.push(o);});
  scene.add(model);
  // Sample on a separate hierarchy. AnimationMixer caches constant properties;
  // overwriting its own bones for a transition leaves stale cached values and
  // causes a jump when a static finger track resumes. Only the visible rig is blended.
  const poseSource = model.clone(true);
  poseSource.traverse(o=>{if(o.isBone)poseBones.push(o);});
  mixer = new THREE.AnimationMixer(poseSource);
  helper = new THREE.SkeletonHelper(model); helper.visible=false;
  helper.material.depthTest=false; helper.material.transparent=true; helper.material.opacity=.85; scene.add(helper);
  selectClip('idle',false);
  $('status').textContent = `พร้อมดู · ${clips.length} ท่าทดสอบ`;
  // Small explicit inspection API for repeatable GLB/browser validation.
  window.emma3d = {
    ready:true, model, bones, mixer, clips, selectClip,
    step(dt) { manual=true;renderStep(dt); },
    seek(name,time) { selectClip(name,false);action.time=Math.max(0,Math.min(time,action.getClip().duration));mixer.update(0);applySample();playing=false;$('play').textContent='เล่น';$('play').setAttribute('aria-pressed','false');renderStep(0); },
    view(name) { setView(name);renderStep(0); },
    getState() {return {clip:current,time:action.time,playing,transition:!!transition,boneCount:bones.length,clips:clips.map(c=>c.name)};}
  };
} catch (error) {
  $('status').textContent = `โหลดโมเดลไม่สำเร็จ: ${error.message}`;
  $('status').classList.add('error');
  console.error(error);
}

function setView(name) {
  const positions = {reference:[-1.8,2.65,9.65],front:[0,2.55,9.8],quarter:[6.8,2.85,7],side:[9.8,2.55,0],back:[0,2.55,-9.8]};
  camera.position.set(...positions[name]);
  controls.target.copy(target);
  controls.update();
}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>setView(b.dataset.view)));
document.querySelectorAll('[data-clip]').forEach(b=>b.addEventListener('click',()=>selectClip(b.dataset.clip)));
$('play').addEventListener('click',()=>{playing=!playing;$('play').textContent=playing?'หยุด':'เล่น';$('play').setAttribute('aria-pressed',String(playing));});
$('timeline').addEventListener('input',()=>{if(!action)return;playing=false;transition=null;action.time=Number($('timeline').value)*action.getClip().duration;mixer.update(0);applySample();$('play').textContent='เล่น';$('play').setAttribute('aria-pressed','false');renderStep(0);});
$('loop').addEventListener('change',()=>{if(action)action.setLoop($('loop').checked?THREE.LoopRepeat:THREE.LoopOnce,Infinity);});
$('skeleton').addEventListener('change',()=>{if(helper)helper.visible=$('skeleton').checked;});
$('wireframe').addEventListener('change',()=>{model?.traverse(o=>{if(o.isMesh) for(const m of Array.isArray(o.material)?o.material:[o.material])m.wireframe=$('wireframe').checked;});});
viewport.addEventListener('pointermove',e=>{if(e.buttons)return;const r=viewport.getBoundingClientRect();gazeX=(e.clientX-r.left)/r.width*2-1;gazeY=(e.clientY-r.top)/r.height*2-1;});
viewport.addEventListener('pointerleave',()=>{gazeX=0;gazeY=0;});
window.addEventListener('pagehide',()=>{
  renderer.setAnimationLoop(null);controls.dispose();environment.dispose();
  hdrTarget.dispose();glowTargets.forEach(t=>t.dispose());
  glowOccluder.dispose();blurGlow.dispose();finishFrame.dispose();screenQuad.geometry.dispose();
  renderer.dispose();
});
