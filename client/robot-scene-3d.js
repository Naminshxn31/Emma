/* Real WebGL geometry; server coordinates remain the authority for navigation.
   Three.js r180 is vendored locally. This is a fictional showroom, not SLAM. */
import * as T from './vendor/three/three.module.min.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import {Explorer} from './robot-explorer.js';
import {buildInterior, updateInterior} from './robot-interior.js';

const mint = 0x78edca, amber = 0xffbd79;
const ui = id => document.getElementById(id);
export class RobotScene3D {
  constructor(canvas, callbacks) {
    this.canvas = canvas;
    // Fail before registering controls/listeners if WebGL 2 is unavailable.
    this.renderer = new T.WebGLRenderer({canvas, antialias:true, alpha:false});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.5));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = T.PCFSoftShadowMap;
    this.renderer.toneMapping = T.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.callbacks = callbacks; this.selected = 'ห้องตัวอย่าง'; this.follow = false;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.scene = new T.Scene(); this.scene.background = new T.Color(0x172735);
    this.scene.fog = new T.Fog(0x172735, 32, 65);
    this.camera = new T.PerspectiveCamera(40, 1, .1, 100);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = !this.reduced;
    this.controls.dampingFactor = .12;
    this.controls.minDistance = 4; this.controls.maxDistance = 35;
    this.controls.maxPolarAngle = Math.PI * .47;
    this.controls.minPolarAngle = .12;
    this.controls.maxTargetRadius = 16;
    this.controls.cursor.set(6, 0, -4);
    this.controls.addEventListener('start', () => {this.follow=false; this.syncCamera();});
    this.controls.addEventListener('change', () => this.syncCamera());
    this.raycaster = new T.Raycaster(); this.targets=[]; this.markers=[];
    this.materials = new Map(); this.textures=[];this.colliders=[];this.cameraSolids=[];
    this.layoutScale=1.5;
    this.buildRoom(); buildInterior(this); this.expandFloor(); this.buildRobot();
    this.explorer=new Explorer(this);
    this.path = new T.Group(); this.scene.add(this.path);
    this.bind(); this.center();this.explorer.setActive(true);
    this.resize = new ResizeObserver(() => this.measure()); this.resize.observe(canvas);
    this.measure();
    this.animate = time => {
      this.frameId=requestAnimationFrame(this.animate);
      if(document.hidden || this.contextLost) return;
      // Limit decorative rendering to 30 fps; state polling remains independent.
      if(time-(this.lastFrame||0)<32) return;
      const dt=Math.min(.1,(time-(this.lastFrame||time))/1000); this.lastFrame=time;
      this.frame(time,dt);
    };
    this.frameId=requestAnimationFrame(this.animate);
    window.addEventListener('pagehide',()=>this.dispose(),{once:true});
    window.addEventListener('pageshow',e=>{if(e.persisted) location.reload();});
    canvas.addEventListener('webglcontextlost',e=>{
      e.preventDefault(); this.contextLost=true;
      ui('sceneStatus').textContent='กราฟิกหยุดชั่วคราว · ยังสั่งงานด้วยเสียงหรือปุ่มได้';
    });
    canvas.addEventListener('webglcontextrestored',()=>{
      this.contextLost=false; ui('sceneStatus').textContent='3D · FLOOR 01';
    });
    canvas.dataset.renderer='webgl';
  }
  material(color) {
    if(!this.materials.has(color)) this.materials.set(color,new T.MeshStandardMaterial({color,roughness:.72,metalness:.08}));
    return this.materials.get(color);
  }
  furnish(x,y,build) {
    const existing=new Set(this.scene.children),start=this.colliders.length;
    build();
    const group=new T.Group();group.position.set(x,0,-y);group.userData.keepSize=true;
    for(const object of [...this.scene.children])if(!existing.has(object)){
      object.position.sub(group.position);group.add(object);
    }
    this.scene.add(group);
    for(const bounds of this.colliders.slice(start))bounds.anchor=group.position.clone();
  }
  expandFloor() {
    const s=this.layoutScale;
    // Spread the architectural plan horizontally; furniture groups retain their dimensions.
    for(const object of this.scene.children){
      object.position.x*=s;object.position.z*=s;
      if(!object.userData.keepSize&&!object.isLight){object.scale.x*=s;object.scale.z*=s;}
    }
    for(const bounds of this.colliders){
      if(bounds.anchor){const delta=bounds.anchor.clone().multiplyScalar(s-1);bounds.translate(delta);}
      else for(const end of [bounds.min,bounds.max]){end.x*=s;end.z*=s;}
    }
    for(const light of this.roomLights)light.distance*=s;
    for(const key of ['left','right','top','bottom'])this.sun.shadow.camera[key]*=s;
    this.sun.shadow.camera.far=75;this.sun.shadow.camera.updateProjectionMatrix();
    this.controls.maxDistance=35*s;this.controls.maxTargetRadius=16*s;
    this.controls.cursor.set(6*s,0,-4*s);this.scene.fog.near=32*s;this.scene.fog.far=65*s;
    this.camera.far=160;this.camera.updateProjectionMatrix();this.scene.updateMatrixWorld(true);
    this.canvas.dataset.layoutScale=String(s);
  }
  worldPoint(x,y,height=0){return new T.Vector3(x*this.layoutScale,height,-y*this.layoutScale);}
  mesh(geometry,color,x,y,z,parent=this.scene) {
    const mesh=new T.Mesh(geometry,this.material(color)); mesh.position.set(x,y,z);
    mesh.castShadow=true; mesh.receiveShadow=true; parent.add(mesh); return mesh;
  }
  box(x,y,w,d,h,color,z=0,parent=this.scene) {
    const mesh=this.mesh(new T.BoxGeometry(w,h,d),color,x+w/2,z+h/2,-y-d/2,parent);
    if(parent===this.scene && z+h>.18 && z<1.8 && h>.08 && w<20){
      this.colliders.push(new T.Box3(new T.Vector3(x,z,-y-d),new T.Vector3(x+w,z+h,-y)));
    }
    if(parent===this.scene && z+h>.4 && h>.08 && w<20)this.cameraSolids.push(mesh);
    return mesh;
  }
  sphere(x,y,z,r,color,parent=this.scene) {
    return this.mesh(new T.SphereGeometry(r,16,12),color,x,y,z,parent);
  }
  plant(x,y,s=1) {
    this.colliders.push(new T.Box3(new T.Vector3(x-.2*s,0,-y-.2*s),new T.Vector3(x+.2*s,1,-y+.2*s)));
    this.mesh(new T.CylinderGeometry(.18*s,.13*s,.32*s,12),0xe9bc96,x,.16*s,-y);
    this.mesh(new T.CylinderGeometry(.028,.028,.6*s,6),0x776544,x,.58*s,-y);
    for(let i=0;i<6;i++) {
      const a=i*Math.PI/3;
      const leaf=this.sphere(x+Math.cos(a)*.17*s,(.7+i*.04)*s,-y+Math.sin(a)*.17*s,.23*s,i%2?0x69a780:0x3c826a);
      leaf.scale.set(.7,1.2,.7); leaf.rotation.z=Math.cos(a)*.5;
    }
  }
  sofa(x,y,w=1.5,color=0xd99679) {
    this.box(x,y,w,.75,.25,color,.18);
    this.box(x,y,w,.16,.62,color,.16);
    for(const dx of [0,w-.13])this.box(x+dx,y,.13,.75,.42,color,.18);
    for(let i=0;i<2;i++)this.box(x+.16+i*(w-.32)/2,y+.2,(w-.37)/2,.48,.12,0xe8c5ae,.43);
  }
  table(x,y,w,d) {
    for(const [dx,dy] of [[.08,.08],[w-.13,.08],[.08,d-.13],[w-.13,d-.13]])this.box(x+dx,y+dy,.05,.05,.55,0x65777b);
    this.box(x,y,w,d,.09,0xe5c79f,.55);
  }
  buildRoom() {
    this.ambient=new T.HemisphereLight(0xdaeeff,0xb3977f,1.9);this.scene.add(this.ambient);
    const sun=this.sun=new T.DirectionalLight(0xffe6be,2.4); sun.position.set(-3,16,8);
    sun.target.position.set(6,0,-4); this.scene.add(sun.target);
    sun.castShadow=true; sun.shadow.mapSize.set(2048,2048);
    Object.assign(sun.shadow.camera,{left:-13,right:13,top:12,bottom:-12,near:.5,far:45});
    sun.shadow.normalBias=.04; sun.shadow.bias=-.0002; this.scene.add(sun);
    const fill=this.fill=new T.DirectionalLight(0x97caff,1.1);fill.position.set(15,6,-12);this.scene.add(fill);
    this.box(-30,-30,72,68,.2,0x172735,-.85);
    this.box(.15,.15,11.7,8.35,.45,0x687f87,-.45);
    this.box(.3,.3,11.4,8.05,.04,0xe7e2d6,-.03);
    const floor=(x,y,w,d,c)=>this.box(x,y,w,d,.012,c,.007);
    floor(.4,2.7,11.2,.9,0xbecfca);
    floor(.5,.45,3,2.2,0xc8d9db); floor(.5,4.8,3.8,3.1,0xe6c7a7);
    floor(4.8,3.65,1.95,4.25,0xb4c6c6); floor(6.95,4.8,4.35,3.1,0xdac0a1);
    floor(7.45,.45,3.85,2.2,0xebd9b4);
    // Floor textures provide joints without coplanar strips that flicker at a distance.
    // Cutaway walls keep the model and entrances readable from all angles.
    for(const v of [[.35,.35,11.25,.1],[.35,.35,.1,7.8],[3.45,.45,.1,2.15],[.5,4.7,2,.12],[3.5,4.7,.8,.12],[6.85,4.7,1.2,.12],[9,4.7,2.3,.12]])this.box(...v,.65,0xf0eee1);
    for(const v of [[4.25,4.8,.1,3.1],[4.75,3.7,.1,4.2],[6.7,3.7,.1,4.2],[11.3,4.8,.1,3.1]])this.box(...v,.3,0xc9d8d6);
    for(const [x,y] of [[1.5,2.7],[3,4.8],[6,3.65],[8.5,4.8],[9,2.7]])floor(x-.4,y-.05,.8,.12,0xf6e9c9);
    this.box(1.1,1.1,.8,.8,.06,0x6bada7);
    this.furnish(.7,.6,()=>{this.box(.7,.6,.25,.6,.85,0x668998);this.box(.69,.68,.025,.32,.15,mint,.52);});
    this.furnish(2.3,.8,()=>{
    this.box(2.3,.8,.75,1.3,.6,0x9eb6ba);this.box(2.25,.75,.85,1.4,.08,0xf2eee4,.6);
    this.box(2.4,1.08,.07,.48,.4,0x314d5a,.68);
    });
    this.furnish(.8,6.05,()=>this.sofa(.8,6.05,1.6));
    this.furnish(1.1,7.02,()=>this.table(1.1,7.02,1.5,.5));
    this.furnish(1.9,5.1,()=>{this.table(1.9,5.1,1.3,.75);this.box(2.1,5.2,.35,.35,.025,0x709dad,.65);});
    this.furnish(9.5,5.8,()=>{
    this.box(9.5,5.8,1.35,1.9,.22,0xa98b73);this.box(9.5,5.8,1.35,.13,.9,0xb3a087);
    this.box(9.54,5.95,1.27,1.7,.25,0xfff3dc,.22);
    this.box(9.54,6.55,1.27,1.1,.05,0x90b5b5,.47);
    for(const x of [9.64,10.25])this.box(x,6,.46,.32,.12,0xffffff,.47);
    });
    this.furnish(7.2,5.65,()=>this.sofa(7.2,5.65,1,0xbdafa0));
    this.furnish(7.3,7.05,()=>this.table(7.3,7.05,.9,.55));
    this.furnish(10.95,5.95,()=>{
    this.box(10.95,5.95,.28,.45,.45,0xa9886b);
    this.mesh(new T.CylinderGeometry(.16,.2,.23,16),0xffe6ad,11.09,.73,-6.17);
    });
    for(const [x,y] of [[5.08,5.05],[5.8,6.25]]) {
      this.furnish(x,y,()=>{
      this.box(x,y,.55,1.05,.13,0x526470);this.box(x+.07,y+.15,.41,.78,.035,0x243944,.13);
      for(const dx of [.06,.45])this.box(x+dx,y+.03,.04,.08,.75,0xb8c7c9);
      this.box(x,y,.55,.17,.08,0x344f5a,.74);this.box(x+.14,y+.01,.25,.1,.02,mint,.83);
      });
    }
    this.furnish(5.1,7.2,()=>{
    this.box(5.1,7.2,1.3,.25,.25,0x677b82);
    for(let i=0;i<4;i++)this.box(5.17+i*.28,7.2,.16,.23,.11,0x304751,.26);
    });
    this.box(9.65,.6,1.5,1.75,.07,0xf7edda);
    this.water=this.box(9.77,.72,1.26,1.51,.015,0x52b9c7,.071);
    for(let i=0;i<7;i++)this.box(9.82,.82+i*.19,1.15,.014,.003,0xa5e4e5,.09);
    for(const x of [7.8,8.45])this.furnish(x,.75,()=>{this.box(x,.75,.52,1.1,.15,0xf4eee0);this.box(x,.75,.52,.24,.25,0xe5c2a0,.15);});
    for(const [x,y,s] of [[.8,3.9,1.1],[3.85,3.9,.8],[4.25,1.1,1.2],[6.45,1.2,1.2],[11.05,3.85,1.1],[.8,7.7,.7],[10.95,7.8,.7]])this.furnish(x,y,()=>this.plant(x,y,s));
  }
  buildRobot() {
    this.robot=new T.Group();this.robot.name='emma-robot';this.scene.add(this.robot);
    const body=this.mesh(new T.CapsuleGeometry(.27,.38,6,16),0xe6f2ec,0,.62,0,this.robot);
    body.scale.z=.83;
    for(const x of [-.19,.19]) {
      this.mesh(new T.SphereGeometry(.15,12,8),0x304852,x,.13,0,this.robot).scale.set(.8,1,1.4);
      const arm=this.mesh(new T.CapsuleGeometry(.065,.29,4,10),0xc8dedb,x*1.85,.64,0,this.robot);
      arm.rotation.z=x>0?.1:-.1;
    }
    const helmet=this.sphere(0,1.19,0,.39,0xeff7ee,this.robot);helmet.scale.set(1,.88,.9);
    const visor=this.sphere(0,1.2,.27,.3,0x163c4e,this.robot);visor.scale.set(1,.52,.36);
    this.eyes=[];
    for(const x of [-.105,.105])this.eyes.push(this.sphere(x,1.22,.369,.038,mint,this.robot));
    this.mesh(new T.BoxGeometry(.24,.18,.025),0x245361,0,.64,.235,this.robot);
    this.mesh(new T.BoxGeometry(.15,.025,.03),mint,0,.66,.255,this.robot);
    this.mesh(new T.CylinderGeometry(.025,.025,.16,8),0xb1ceca,0,1.57,0,this.robot);
    this.sphere(0,1.66,0,.057,mint,this.robot);
    this.robotLabel=this.label('EMMA',0,1.95,0,1.1);this.robot.add(this.robotLabel);
    this.robot.position.copy(this.worldPoint(1.5,1.5));
  }
  label(text,x,y,z,width=2) {
    const c=document.createElement('canvas'); c.width=384;c.height=88;
    const ctx=c.getContext('2d');ctx.fillStyle='#183e49';ctx.strokeStyle='#83dabe';ctx.lineWidth=3;
    ctx.beginPath();ctx.roundRect(3,3,378,82,20);ctx.fill();ctx.stroke();
    ctx.font='600 34px "Leelawadee UI", Tahoma, sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#ebfff3';ctx.fillText(text,192,44,360);
    const texture=new T.CanvasTexture(c);texture.colorSpace=T.SRGBColorSpace;this.textures.push(texture);
    const sprite=new T.Sprite(new T.SpriteMaterial({map:texture,depthTest:false,depthWrite:false}));
    sprite.scale.set(width,width*88/384,1);sprite.position.set(x,y,z);sprite.renderOrder=5;
    return sprite;
  }
  setState(state) {
    const changedEpoch=this.state?.epoch!==state.epoch;
    this.state=state;
    if(changedEpoch)this.explorer.reset();
    if(changedEpoch || state.phase!=='moving')this.robot.position.copy(this.worldPoint(...state.position));
    if(!this.markers.length) {
      for(const point of state.points) {
        const ring=new T.Mesh(new T.TorusGeometry(.35,.035,8,40),new T.MeshBasicMaterial({color:mint}));
        ring.rotation.x=Math.PI/2;ring.position.copy(this.worldPoint(point.x,point.y,.08));ring.userData.place=point.name;
        const target=new T.Mesh(new T.CircleGeometry(.48,24),new T.MeshBasicMaterial({visible:false}));
        target.rotation.x=-Math.PI/2;target.position.copy(ring.position);target.userData.place=point.name;
        const label=this.label(point.name==='base'?'ฐานชาร์จ':point.name,point.x*this.layoutScale,.95,(-point.y-.75)*this.layoutScale);
        label.userData.place=point.name;
        label.visible=!this.explorer.active;
        this.scene.add(ring,target,label);this.targets.push(target,label);this.markers.push(ring);
      }
    }
    const key=JSON.stringify([state.epoch,state.route,state.phase]);
    if(key!==this.routeKey) {
      this.routeKey=key;
      for(const child of [...this.path.children]){child.geometry.dispose();child.material.dispose();this.path.remove(child);}
      const route=state.route||[],color=state.phase==='blocked'?amber:mint;
      for(let i=1;i<route.length;i++) {
        const a=this.worldPoint(...route[i-1],.055);
        const b=this.worldPoint(...route[i],.055);const delta=b.clone().sub(a),length=delta.length();
        if(!length)continue;
        const line=new T.Mesh(new T.CylinderGeometry(.035,.035,length,6),new T.MeshBasicMaterial({color}));
        line.position.copy(a).add(b).multiplyScalar(.5);line.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),delta.normalize());this.path.add(line);
      }
    }
    this.select(this.selected);this.mini();
  }
  select(name) {
    this.selected=name;
    for(const marker of this.markers)marker.material.color.setHex(marker.userData.place===name?amber:mint);
  }
  hit(event) {
    const rect=this.canvas.getBoundingClientRect();
    this.raycaster.setFromCamera(new T.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),this.camera);
    return this.raycaster.intersectObjects(this.targets,false)[0]?.object.userData.place;
  }
  bind() {
    const pointers=new Set();
    this.canvas.addEventListener('pointerdown',e=>{
      pointers.add(e.pointerId);this.canvas.focus({preventScroll:true});
      if(pointers.size===1)this.drag={id:e.pointerId,x:e.clientX,y:e.clientY,moved:false,button:e.button};
      else if(this.drag)this.drag.moved=true;
    });
    this.canvas.addEventListener('pointermove',e=>{
      if(this.drag && Math.hypot(e.clientX-this.drag.x,e.clientY-this.drag.y)>5)this.drag.moved=true;
      this.canvas.classList.toggle('over-point',!!this.hit(e));
    });
    this.canvas.addEventListener('pointerup',e=>{
      pointers.delete(e.pointerId);
      if(!this.explorer.active && this.drag?.id===e.pointerId && !this.drag.moved && this.drag.button===0){const name=this.hit(e);if(name)this.callbacks.select(name);}
      if(!pointers.size)this.drag=null;
    });
    this.canvas.addEventListener('pointercancel',e=>{pointers.delete(e.pointerId);this.drag=null;});
    this.canvas.addEventListener('dblclick',e=>{if(!this.explorer.active){const name=this.hit(e);if(name)this.callbacks.activate(name);}});
    this.canvas.addEventListener('keydown',e=>{
      if(e.code==='Space'){e.preventDefault();this.callbacks.stop();}
      if(e.key.toLowerCase()==='f')this.toggleFollow();
      if(e.key.toLowerCase()==='v')this.toggleWalk();
      if(e.key==='0')this.center();
      if(e.key==='+'||e.key==='=')this.zoomBy(1.15);
      if(e.key==='-')this.zoomBy(1/1.15);
    });
  }
  measure() {
    const w=this.canvas.clientWidth,h=this.canvas.clientHeight;if(!w||!h)return;
    this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();
  }
  center() {
    if(this.explorer)this.explorer.setActive(false);
    this.follow=false;this.controls.target.copy(this.worldPoint(6,4.1,.15));
    const narrow=this.canvas.clientWidth<550;
    this.camera.position.copy(this.controls.target).add(new T.Vector3(11,15,14).multiplyScalar((narrow?1.3:.9)*this.layoutScale));
    this.defaultDistance=this.camera.position.distanceTo(this.controls.target);
    this.controls.update();this.syncCamera();
  }
  zoomBy(factor) {
    if(this.explorer.active){this.explorer.distance=T.MathUtils.clamp(this.explorer.distance/factor,1.6,5);return;}
    const offset=this.camera.position.clone().sub(this.controls.target);
    offset.setLength(T.MathUtils.clamp(offset.length()/factor,4,this.controls.maxDistance));
    this.camera.position.copy(this.controls.target).add(offset);this.controls.update();this.syncCamera();
  }
  toggleFollow(){
    if(this.explorer.active)this.explorer.setActive(false);
    this.follow=!this.follow;
    if(this.follow) {
      const offset=this.camera.position.clone().sub(this.controls.target).setLength(8);
      this.controls.target.copy(this.robot.position);this.controls.target.y=.6;
      this.camera.position.copy(this.controls.target).add(offset);this.controls.update();
    }
    this.syncCamera();
  }
  toggleWalk(){if(this.explorer.active)this.center();else{this.explorer.setActive(true);this.syncCamera();}}
  syncCamera() {
    ui('zoomLevel').textContent=`${Math.round((this.defaultDistance||23)/this.camera.position.distanceTo(this.controls.target)*100)}%`;
    ui('followRobot').setAttribute('aria-pressed',String(this.follow));
  }
  mini() {
    if(!this.state)return;
    const c=ui('minimap').getContext('2d'),w=c.canvas.width,h=c.canvas.height;
    c.fillStyle='#152432';c.fillRect(0,0,w,h);
    const p=([x,y])=>[8+x/12*(w-16),h-7-y/8.5*(h-14)];
    c.strokeStyle='#526e74';c.lineWidth=6;c.beginPath();c.moveTo(...p([1,3.1]));c.lineTo(...p([11,3.1]));c.stroke();
    c.strokeStyle='#78edca';c.lineWidth=1.5;c.beginPath();(this.state.route||[]).forEach((v,i)=>i?c.lineTo(...p(v)):c.moveTo(...p(v)));c.stroke();
    for(const point of this.state.points){const [x,y]=p([point.x,point.y]);c.fillStyle=point.name===this.selected?'#ffbd79':'#829fa4';c.fillRect(x-2,y-2,4,4);}
    const [x,y]=p([this.robot.position.x/this.layoutScale,-this.robot.position.z/this.layoutScale]);c.fillStyle='#78edca';c.beginPath();c.arc(x,y,3.5,0,Math.PI*2);c.fill();
    if(this.explorer.active){const [vx,vy]=p([this.explorer.actor.position.x/this.layoutScale,-this.explorer.actor.position.z/this.layoutScale]);c.fillStyle='#ffbd79';c.beginPath();c.arc(vx,vy,3,0,Math.PI*2);c.fill();}
  }
  frame(time,dt) {
    if(this.state) {
      const goal=this.worldPoint(...this.state.position);
      const delta=goal.clone().sub(this.robot.position);
      if(delta.lengthSq()>.0001 && this.state.phase==='moving') {
        const angle=Math.atan2(delta.x,delta.z);
        this.robot.rotation.y+=Math.atan2(Math.sin(angle-this.robot.rotation.y),Math.cos(angle-this.robot.rotation.y))*Math.min(1,dt*12);
      }
      this.robot.position.lerp(goal,this.reduced?1:1-Math.exp(-dt*18));
      for(const eye of this.eyes)eye.scale.y=this.reduced?1:time%4800>4630?.12:1;
      if(this.follow) {
        const target=this.robot.position.clone();target.y=.6;
        const delta=target.sub(this.controls.target).multiplyScalar(this.reduced?1:Math.min(1,dt*6));
        this.camera.position.add(delta);this.controls.target.add(delta);
      }
    }
    updateInterior(this,dt);
    if(this.explorer.active)this.explorer.update(dt,time);else this.controls.update();
    this.renderer.render(this.scene,this.camera);this.mini();
  }
  dispose() {
    cancelAnimationFrame(this.frameId);this.resize.disconnect();this.controls.dispose();this.explorer.dispose();
    const materials=new Set();
    this.scene.traverse(obj=>{obj.geometry?.dispose();if(obj.material)materials.add(obj.material);});
    for(const material of materials)material.dispose();for(const texture of this.textures)texture.dispose();
    this.renderer.dispose();
  }
}
