import * as T from './vendor/three/three.module.min.js';

function surface(world,wood=false) {
  const c=document.createElement('canvas');c.width=c.height=256;const g=c.getContext('2d');
  g.fillStyle=wood?'#ac8360':'#d6d3ca';g.fillRect(0,0,256,256);
  // Deterministic procedural grain: local assets, no runtime image requests.
  for(let i=0;i<1600;i++) {
    const x=(i*71)%256,y=(i*137)%256;
    g.fillStyle=wood?`rgba(59,35,17,${.03+(i%7)*.012})`:`rgba(70,72,68,${.015+(i%5)*.006})`;
    g.fillRect(x,y,wood?30+i%110:1,1);
  }
  g.strokeStyle=wood?'#75573e':'#b7b5ae';g.lineWidth=1;
  for(let y=0;y<256;y+=wood?64:128){g.beginPath();g.moveTo(0,y);g.lineTo(256,y);g.stroke();}
  for(let x=0;x<256;x+=128){g.beginPath();g.moveTo(x,0);g.lineTo(x,256);g.stroke();}
  const texture=new T.CanvasTexture(c);texture.wrapS=texture.wrapT=T.RepeatWrapping;
  texture.colorSpace=T.SRGBColorSpace;texture.anisotropy=Math.min(4,world.renderer.capabilities.getMaxAnisotropy());
  world.textures.push(texture);return texture;
}

export function buildInterior(w) {
  w.upperWalls=[];
  const upper=(x,y,width,depth,h=2.1,z=.65)=>{
    const m=w.box(x,y,width,depth,h,0xe8e4d9,z);w.upperWalls.push(m);return m;
  };
  upper(.35,.35,11.25,.12);upper(.35,.35,.12,7.8);
  // Rear wall has a real opening for the show apartment's window.
  upper(.35,8.13,7,.14,2.75,0);upper(10.95,8.13,.5,.14,2.75,0);
  upper(7.35,8.13,3.6,.14,.85,0);upper(7.35,8.13,3.6,.14,.3,2.45);
  upper(11.3,.4,.12,7.8,2.75,0);
  // A ceiling is present at eye level; hide it with the upper walls in overview.
  upper(.35,.35,11.1,7.9,.08,2.75);
  for(const [x,y,width] of [[.5,4.7,2],[3.5,4.7,.8],[6.85,4.7,1.2],[9,4.7,2.3]])upper(x,y,width,.12);
  for(const x of [3,8.5])upper(x-.5,4.7,1,.12,.48,2.27);
  // Window frame, distant city and garden seen through the glazing.
  const glass=new T.Mesh(new T.PlaneGeometry(3.55,1.6),new T.MeshPhysicalMaterial({color:0xb5d8e3,transparent:true,opacity:.2,roughness:.08,metalness:.15,side:T.DoubleSide}));
  glass.position.set(9.15,1.65,-8.1);w.scene.add(glass);
  for(const x of [7.35,9.15,10.95])w.box(x,8.08,.045,.06,1.65,0x33474d,.83);
  for(const z of [.83,2.45])w.box(7.35,8.08,3.65,.06,.045,0x33474d,z);
  w.exteriorViews=[];
  for(let i=0;i<14;i++) {
    const h=3+(i*7)%9;const building=w.box(-8+i*2.3,20+(i%3)*3,1.5,2,h,i%2?0x405565:0x667983);
    // Distant props have no player/camera collision inside the apartment.
    building.castShadow=false;w.exteriorViews.push(building);
    for(let level=1;level<h;level+=1.1)w.exteriorViews.push(w.box(-7.85+i*2.3,19.98+(i%3)*3,1.15,.025,.2,0xc6b896,level));
  }
  const tile=surface(w),wood=surface(w,true);
  w.scene.traverse(mesh => {
    if(!mesh.isMesh||!mesh.geometry.parameters?.width)return;
    const color=mesh.material.color?.getHex();
    if([0xe7e2d6,0xe6c7a7,0xdac0a1,0xe5c79f].includes(color)) {
      mesh.material=mesh.material.clone();const map=(color===0xe7e2d6?tile:wood).clone();
      map.repeat.set(Math.max(1,mesh.geometry.parameters.width/1.2),Math.max(1,mesh.geometry.parameters.depth/1.2));
      map.needsUpdate=true;w.textures.push(map);mesh.material.map=map;mesh.material.color.setHex(0xffffff);mesh.material.roughness=.58;
    }
  });
  // A media console, wall-mounted AC, pleated curtains and visible room lamps.
  w.furnish(7.1,7.85,()=>{
  w.box(7.1,7.85,1.25,.32,.46,0x8c7258);
  w.box(7.15,7.82,1.12,.075,.7,0x17242c,.65);
  w.tv=w.box(7.2,7.8,1.02,.012,.6,0x111a20,.7);
  w.tv.material=w.tv.material.clone();
  });
  const screen=document.createElement('canvas');screen.width=512;screen.height=300;
  const paint=screen.getContext('2d'),gradient=paint.createLinearGradient(0,0,512,300);
  gradient.addColorStop(0,'#0d293c');gradient.addColorStop(1,'#487e80');paint.fillStyle=gradient;paint.fillRect(0,0,512,300);
  paint.fillStyle='#b9d5b8';paint.beginPath();paint.arc(390,85,34,0,Math.PI*2);paint.fill();
  paint.fillStyle='#284b53';paint.beginPath();paint.moveTo(0,300);paint.lineTo(0,190);paint.lineTo(130,112);paint.lineTo(310,260);paint.lineTo(512,170);paint.lineTo(512,300);paint.fill();
  paint.fillStyle='#e6ede6';paint.font='22px sans-serif';paint.fillText('EMMA HOME',28,46);
  w.tvTexture=new T.CanvasTexture(screen);w.tvTexture.colorSpace=T.SRGBColorSpace;w.textures.push(w.tvTexture);
  w.furnish(9.5,4.73,()=>{
  w.ac=w.box(9.5,4.73,1.15,.22,.34,0xe5e8e5,2.1);
  w.acLed=w.box(10.42,4.965,.1,.025,.028,mintColor(),2.16);w.acLed.material=w.acLed.material.clone();
  w.vents=[];
  for(let i=0;i<5;i++)w.vents.push(w.box(9.63+i*.17,4.97,.07,.03,.012,0x5c787c,2.14));
  });
  w.curtains=[];
  const cloth=new T.MeshStandardMaterial({color:0xab927d,roughness:1,side:T.DoubleSide});
  for(const side of [-1,1]) {
    const group=new T.Group();group.position.set(side<0?7.3:11,0,-8.01);w.scene.add(group);
    const geometry=new T.PlaneGeometry(1.87,2.1,96,1);
    const positions=geometry.attributes.position;
    for(let i=0;i<positions.count;i++)positions.setZ(i,Math.sin((positions.getX(i)+.935)*Math.PI*24/1.87)*.045);
    geometry.computeVertexNormals();
    const mesh=new T.Mesh(geometry,cloth);mesh.position.set(side<0?.935:-.935,1.38,0);
    mesh.castShadow=true;group.add(mesh);
    w.curtains.push(group);
  }
  w.roomLights=[];w.lampMeshes=[];
  for(const [x,y] of [[2.3,6.4],[8.8,6.8],[5.9,4.3],[1.6,1.4]]) {
    w.furnish(x,y,()=>{
    const light=new T.PointLight(0xffd5a1,22,7,2);light.position.set(x,2.4,-y);w.scene.add(light);w.roomLights.push(light);
    // Suspended fixtures keep a ceiling-free overview but read as indoor lights at eye level.
    const shade=w.mesh(new T.CylinderGeometry(.2,.34,.15,20),0xd2b481,x,2.47,-y);
    const bulb=w.mesh(new T.CylinderGeometry(.29,.29,.018,20),0xffe9bd,x,2.385,-y);
    bulb.material=bulb.material.clone();w.lampMeshes.push(bulb);shade.castShadow=false;
    });
  }
  w.colliders.push(new T.Box3(new T.Vector3(9.65,0,-2.35),new T.Vector3(11.15,1,-.6)));
  w.curtainPosition=.8;
}
function mintColor(){return 0x78edca;}

export function updateInterior(w,dt) {
  const home=w.state?.smart_home;if(!home)return;
  const power=home.lights.on?home.lights.brightness/100:0;
  const target=home.curtains.position/100;
  w.curtainPosition+=(target-w.curtainPosition)*(w.reduced?1:1-Math.exp(-dt*3));
  for(const curtain of w.curtains)curtain.scale.x=(1-.88*w.curtainPosition)*w.layoutScale;
  const daylight=.22+.55*w.curtainPosition;
  w.sun.intensity=daylight;w.ambient.intensity=.23+.36*w.curtainPosition;
  w.fill.intensity=.2;
  for(const light of w.roomLights)light.intensity=power*24*w.layoutScale;
  for(const bulb of w.lampMeshes){bulb.material.emissive.setHex(0xffcf87);bulb.material.emissiveIntensity=power*2.3;}
  if(w.tvOn!==home.tv.on){
    w.tvOn=home.tv.on;w.tv.material.map=home.tv.on?w.tvTexture:null;w.tv.material.emissiveMap=home.tv.on?w.tvTexture:null;w.tv.material.needsUpdate=true;
  }
  w.tv.material.color.setHex(home.tv.on?0xffffff:0x111a20);w.tv.material.emissive.setHex(home.tv.on?0xffffff:0);w.tv.material.emissiveIntensity=home.tv.on?.8:0;
  w.acLed.material.color.setHex(home.ac.on?0x78edca:0x495958);
  w.acLed.material.emissive.setHex(home.ac.on?0x36d9a5:0);
  for(const vent of w.vents)vent.rotation.x=home.ac.on?-.25:0;
  w.canvas.dataset.lights=String(home.lights.on);w.canvas.dataset.curtains=String(home.curtains.position);
}
