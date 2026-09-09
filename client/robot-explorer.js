/* Local visitor controls: never sends robot movement or physical device I/O. */
import * as T from './vendor/three/three.module.min.js';
export class Explorer {
  constructor(world) {
    this.world=world;this.camera=world.camera;this.canvas=world.canvas;
    this.keys=new Set();this.active=false;this.yaw=-.18;this.pitch=.24;this.distance=2.8;
    this.actor=new T.Group();this.actor.name='visitor';this.actor.position.copy(world.worldPoint(8.65,6.9));
    world.scene.add(this.actor);this.limbs=[];
    const part=(geometry,color,x,y,z,parent=this.actor)=>world.mesh(geometry,color,x,y,z,parent);
    const torso=part(new T.CapsuleGeometry(.22,.36,6,16),0x344d60,0,1.05,0);torso.scale.set(1,1,.65);
    const shoulders=part(new T.SphereGeometry(.245,20,12),0x344d60,0,1.29,0);shoulders.scale.set(1.13,.52,.63);
    part(new T.CylinderGeometry(.18,.19,.07,16),0x25323d,0,.8,0);
    part(new T.CylinderGeometry(.085,.09,.13,12),0xc99577,0,1.45,0);
    const head=part(new T.SphereGeometry(.16,20,16),0xc99577,0,1.63,0);head.scale.set(.86,1.12,.9);
    part(new T.SphereGeometry(.165,16,12,0,Math.PI*2,0,Math.PI*.5),0x302924,0,1.67,0);
    part(new T.SphereGeometry(.163,16,12,Math.PI,Math.PI,.3,1.9),0x302924,0,1.65,0);
    for(const side of [-1,1]){
      part(new T.SphereGeometry(.029,12,8),0xc99577,side*.143,1.63,0);
      part(new T.SphereGeometry(.016,12,8),0x342b28,side*.06,1.66,.126);
    }
    const nose=part(new T.SphereGeometry(.026,12,8),0xc08e72,0,1.61,.14);nose.scale.set(.65,1,1.1);
    // Forward is +Z; articulated limbs swing only while moving.
    for(const side of [-1,1]) {
      const leg=new T.Group();leg.position.set(side*.12,.78,0);this.actor.add(leg);
      part(new T.CylinderGeometry(.093,.07,.6,14),0x24303d,0,-.33,0,leg);
      const shoe=part(new T.CapsuleGeometry(.079,.13,4,12),0xbac0c1,0,-.69,.045,leg);shoe.rotation.x=Math.PI/2;shoe.scale.z=.7;
      shoe.castShadow=true;this.limbs.push({node:leg,sign:side});
      const arm=new T.Group();arm.position.set(side*.29,1.28,0);this.actor.add(arm);
      part(new T.CapsuleGeometry(.066,.34,5,12),0x344d60,0,-.21,0,arm);
      part(new T.SphereGeometry(.065,12,8),0xc99577,0,-.45,0,arm);
      this.limbs.push({node:arm,sign:-side});
    }
    this.onKey=e=>{
      if(!this.active||document.activeElement!==this.canvas)return;
      if(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','ShiftLeft','ShiftRight'].includes(e.code)){
        e.preventDefault();this.keys.add(e.code);
      }
    };
    this.onUp=e=>this.keys.delete(e.code);this.clear=()=>{this.keys.clear();this.drag=null;};
    window.addEventListener('keydown',this.onKey);window.addEventListener('keyup',this.onUp);
    window.addEventListener('blur',this.clear);this.canvas.addEventListener('blur',this.clear);
    document.addEventListener('visibilitychange',this.clear);
    this.canvas.addEventListener('pointerdown',e=>{
      if(this.active && e.button===0){this.canvas.setPointerCapture(e.pointerId);this.drag={id:e.pointerId,x:e.clientX,y:e.clientY};}
    });
    this.canvas.addEventListener('pointermove',e=>{
      if(!this.active||this.drag?.id!==e.pointerId)return;
      this.yaw-=(e.clientX-this.drag.x)*.006;
      this.pitch=T.MathUtils.clamp(this.pitch+(e.clientY-this.drag.y)*.004,-.08,.7);
      this.drag.x=e.clientX;this.drag.y=e.clientY;
    });
    const release=()=>{this.drag=null;};this.canvas.addEventListener('pointerup',release);this.canvas.addEventListener('pointercancel',release);
    this.canvas.addEventListener('wheel',e=>{if(this.active){e.preventDefault();this.distance=T.MathUtils.clamp(this.distance+e.deltaY*.003,1.6,5);}}, {passive:false});
    for(const button of document.querySelectorAll('[data-walk]')) {
      button.addEventListener('pointerdown',e=>{e.preventDefault();button.setPointerCapture(e.pointerId);this.keys.add(button.dataset.walk);});
      for(const name of ['pointerup','pointercancel','lostpointercapture'])button.addEventListener(name,()=>this.keys.delete(button.dataset.walk));
    }
    this.ray=new T.Raycaster();this.actor.visible=false;
  }
  setActive(value) {
    this.active=value;this.clear();this.actor.visible=value;this.world.controls.enabled=!value;
    if(value){this.world.follow=false;this.camera.fov=65;}else this.camera.fov=40;
    this.camera.updateProjectionMatrix();
    document.getElementById('walkMode').setAttribute('aria-pressed',String(value));
    document.getElementById('walkPad').hidden=!value;
    document.getElementById('explorerHint').hidden=!value;
    document.getElementById('sceneModeHelp').textContent=value
      ? 'WASD / ลูกศร เดิน · Shift เดินเร็ว · ลากมองรอบตัว · V ภาพรวม · Space หยุดหุ่น'
      : 'ลากหมุน 3D · คลิกขวาลากเลื่อน · ล้อเมาส์ซูม · ดับเบิลคลิกจุดหมาย';
    for(const wall of this.world.upperWalls)wall.visible=value;
    for(const prop of this.world.exteriorViews)prop.visible=value;
    for(const target of this.world.targets)if(target.isSprite)target.visible=!value;
    this.world.robotLabel.visible=!value;
    this.canvas.dataset.camera=value?'third-person':'overview';
    if(value)this.update(0,0,true);
  }
  reset(){this.actor.position.copy(this.world.worldPoint(8.65,6.9));this.actor.rotation.y=Math.PI;this.yaw=-.18;this.clear();}
  blocked(x,z) {
    const r=.2;
    const scale=this.world.layoutScale;
    if(x<.65*scale||x>11.05*scale||z>-.65*scale||z< -7.95*scale)return true;
    return this.world.colliders.some(b=>{
      const dx=x-T.MathUtils.clamp(x,b.min.x,b.max.x),dz=z-T.MathUtils.clamp(z,b.min.z,b.max.z);
      return dx*dx+dz*dz<r*r;
    });
  }
  update(dt,time,snap=false) {
    if(!this.active)return;
    const k=this.keys;
    let forward=Number(k.has('KeyW')||k.has('ArrowUp'))-Number(k.has('KeyS')||k.has('ArrowDown'));
    let strafe=Number(k.has('KeyD')||k.has('ArrowRight'))-Number(k.has('KeyA')||k.has('ArrowLeft'));
    const length=Math.hypot(forward,strafe);const old=this.actor.position.clone();
    if(length) {
      forward/=length;strafe/=length;
      const speed=(k.has('ShiftLeft')||k.has('ShiftRight')?2.4:1.45)*dt;
      const dx=(-Math.sin(this.yaw)*forward+Math.cos(this.yaw)*strafe)*speed;
      const dz=(-Math.cos(this.yaw)*forward-Math.sin(this.yaw)*strafe)*speed;
      // Small swept substeps avoid tunnelling across thin walls on slow frames.
      const steps=Math.max(1,Math.ceil(speed/.06));
      for(let i=0;i<steps;i++){
        if(!this.blocked(this.actor.position.x+dx/steps,this.actor.position.z))this.actor.position.x+=dx/steps;
        if(!this.blocked(this.actor.position.x,this.actor.position.z+dz/steps))this.actor.position.z+=dz/steps;
      }
      const angle=Math.atan2(dx,dz);this.actor.rotation.y+=Math.atan2(Math.sin(angle-this.actor.rotation.y),Math.cos(angle-this.actor.rotation.y))*Math.min(1,dt*12);
    }
    const moved=old.distanceToSquared(this.actor.position)>.000001;
    for(const limb of this.limbs)limb.node.rotation.x=moved&&!this.world.reduced?Math.sin(time/120)*.42*limb.sign:0;
    const focus=this.actor.position.clone().add(new T.Vector3(0,1.1,0));
    const offset=new T.Vector3(Math.sin(this.yaw)*Math.cos(this.pitch),Math.sin(this.pitch),Math.cos(this.yaw)*Math.cos(this.pitch)).multiplyScalar(this.distance);
    this.ray.set(focus,offset.clone().normalize());this.ray.far=this.distance;
    const hit=this.ray.intersectObjects(this.world.cameraSolids,false).find(h=>h.object.visible);
    if(hit)offset.setLength(Math.max(.3,hit.distance-.18));
    const goal=focus.clone().add(offset);
    this.camera.position.lerp(goal,snap?1:1-Math.exp(-dt*14));this.camera.lookAt(focus);
    document.getElementById('zoomLevel').textContent=`${Math.round(2.8/this.distance*100)}%`;
    this.canvas.dataset.visitor=`${this.actor.position.x.toFixed(2)},${(-this.actor.position.z).toFixed(2)}`;
  }
  dispose(){
    window.removeEventListener('keydown',this.onKey);window.removeEventListener('keyup',this.onUp);
    window.removeEventListener('blur',this.clear);document.removeEventListener('visibilitychange',this.clear);
  }
}
