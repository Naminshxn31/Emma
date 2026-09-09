/* A local, hand-drawn isometric game scene. Coordinates come from the server.
   No image downloads, WebGL dependency, physics claims or hardware control. */
"use strict";
(() => {
  const TAU = Math.PI * 2;
  const palette = {mint:"#71edc6",amber:"#ffbe76",ink:"#172637",cream:"#ece6d9"};
  const names = {base:"ฐานชาร์จ",ห้องตัวอย่าง:"ห้องตัวอย่าง",โต๊ะเซลส์:"โต๊ะเซลส์",สระว่ายน้ำ:"สระว่ายน้ำ",ฟิตเนส:"ฟิตเนส"};
  const labels = {base:[1.65,0.0],ห้องตัวอย่าง:[9.1,8.0],โต๊ะเซลส์:[2.4,8.0],สระว่ายน้ำ:[9.6,0.0],ฟิตเนส:[5.95,7.6]};
  const tint = (hex, delta) => {
    const value = parseInt(hex.slice(1),16);
    return `rgb(${[16,8,0].map(shift => Math.max(0,Math.min(255,((value>>shift)&255)+delta))).join(",")})`;
  };
  class RobotScene {
    constructor(canvas, {select, activate, stop}) {
      this.canvas=canvas; this.ctx=canvas.getContext("2d");
      this.onSelect=select; this.onActivate=activate; this.onStop=stop;
      this.state=null; this.selected="ห้องตัวอย่าง"; this.zoom=1;
      this.pan={x:0,y:0}; this.follow=false; this.hits=[]; this.hover=null;
      this.robot=[1.5,1.5]; this.drag=null; this.lastFrame=0; this.arrivalAt=0;
      this.reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.minimap=document.getElementById("minimap");
      this.resize=new ResizeObserver(()=>this.measure()); this.resize.observe(canvas);
      this.bind(); this.measure();
      this.animate=time=> { this.frame(time); this.frameId=requestAnimationFrame(this.animate); };
      this.frameId=requestAnimationFrame(this.animate);
      window.addEventListener("pagehide",()=> {cancelAnimationFrame(this.frameId); this.resize.disconnect();},{once:true});
    }
    measure() {
      this.width=this.canvas.clientWidth; this.height=this.canvas.clientHeight;
      this.dpr=Math.min(window.devicePixelRatio||1,2);
      this.canvas.width=Math.round(this.width*this.dpr); this.canvas.height=Math.round(this.height*this.dpr);
    }
    bind() {
      this.canvas.addEventListener("wheel",event=> {event.preventDefault();this.zoomBy(event.deltaY<0?1.12:1/1.12);},{passive:false});
      this.canvas.addEventListener("pointerdown",event=> {
        if (event.button!==0) return;
        this.canvas.focus({preventScroll:true});
        this.drag={id:event.pointerId,x:event.clientX,y:event.clientY,px:this.pan.x,py:this.pan.y,moved:false};
        this.canvas.setPointerCapture(event.pointerId);
      });
      this.canvas.addEventListener("pointermove",event=> {
        const rect=this.canvas.getBoundingClientRect();
        this.hover=this.hit(event.clientX-rect.left,event.clientY-rect.top)?.name||null;
        this.canvas.classList.toggle("over-point",!!this.hover);
        if (!this.drag) return;
        const dx=event.clientX-this.drag.x,dy=event.clientY-this.drag.y;
        if (Math.hypot(dx,dy)>5) this.drag.moved=true;
        if (this.drag.moved) {
          this.follow=false; this.pan={x:this.drag.px+dx,y:this.drag.py+dy}; this.syncCamera();
          this.canvas.classList.add("dragging");
        }
      });
      this.canvas.addEventListener("pointerup",event=> {
        if (!this.drag || this.drag.id!==event.pointerId) return;
        const rect=this.canvas.getBoundingClientRect();
        const hit=this.hit(event.clientX-rect.left,event.clientY-rect.top);
        if (!this.drag.moved && hit) { this.select(hit.name); this.onSelect(hit.name); }
        this.drag=null; this.canvas.classList.remove("dragging");
      });
      this.canvas.addEventListener("pointercancel",()=> {this.drag=null;this.canvas.classList.remove("dragging");});
      this.canvas.addEventListener("dblclick",event=> {
        const rect=this.canvas.getBoundingClientRect(); const hit=this.hit(event.clientX-rect.left,event.clientY-rect.top);
        if (hit) this.onActivate(hit.name);
      });
      this.canvas.addEventListener("keydown",event=> {
        if(event.code==="Space") {event.preventDefault();this.onStop();}
        if(event.key.toLowerCase()==="f") this.toggleFollow();
        if(event.key==="0") this.center();
        if(event.key==="+" || event.key==="=") this.zoomBy(1.15);
        if(event.key==="-") this.zoomBy(1/1.15);
      });
    }
    hit(x,y) {return [...this.hits].reverse().find(h=>x>=h.x&&x<=h.x+h.w&&y>=h.y&&y<=h.y+h.h);}
    select(name) {this.selected=name;}
    setState(state) {
      if(this.state?.epoch!==state.epoch) {this.robot=[...state.position];this.arrivalAt=0;}
      if(state.phase==="arrived" && this.state?.phase!=="arrived") this.arrivalAt=performance.now();
      this.state=state;
    }
    zoomBy(factor) {this.zoom=Math.max(.65,Math.min(2.3,this.zoom*factor));this.syncCamera();}
    center() {this.pan={x:0,y:0};this.zoom=1;this.follow=false;this.syncCamera();}
    toggleFollow() {this.follow=!this.follow;this.pan={x:0,y:0};this.syncCamera();}
    syncCamera() {
      document.getElementById("zoomLevel").textContent=`${Math.round(this.zoom*100)}%`;
      document.getElementById("followRobot").setAttribute("aria-pressed",String(this.follow));
    }
    project(x,y,z=0) {return [this.ox+(x-y)*this.scale,this.oy+(x+y)*this.scale*.52-z*this.scale];}
    polygon(points,fill,stroke=null) {
      const ctx=this.ctx;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();
      if(fill){ctx.fillStyle=fill;ctx.fill();}if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.7;ctx.stroke();}
    }
    floor(x,y,w,d,color,z=.01) {
      this.polygon([[x,y],[x+w,y],[x+w,y+d],[x,y+d]].map(p=>this.project(...p,z)),color);
    }
    line(points,color,width=1) {
      const ctx=this.ctx;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));
      ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineCap="round";ctx.lineJoin="round";ctx.stroke();
    }
    box(x,y,w,d,h,color,z=0) {
      const top=[[x,y],[x+w,y],[x+w,y+d],[x,y+d]].map(p=>this.project(...p,z+h));
      this.polygon([top[1],top[2],this.project(x+w,y+d,z),this.project(x+w,y,z)],tint(color,-34));
      this.polygon([top[2],top[3],this.project(x,y+d,z),this.project(x+w,y+d,z)],tint(color,-15));
      this.polygon(top,tint(color,9),"#ffffff20");
    }
    ellipse(x,y,z,rx,ry,color) {
      const ctx=this.ctx,p=this.project(x,y,z);ctx.fillStyle=color;ctx.beginPath();
      ctx.ellipse(p[0],p[1],rx*this.scale,ry*this.scale,0,0,TAU);ctx.fill();
    }
    plant(x,y,size=1) {
      this.ellipse(x,y,0,.3*size,.15*size,"#273d3230");
      this.box(x-.16*size,y-.16*size,.32*size,.32*size,.32*size,"#e4b999");
      this.line([this.project(x,y,.25*size),this.project(x,y,.85*size)],"#67816c",2);
      for(const [dx,dy,z] of [[-.18,0,.57],[.17,.02,.63],[0,-.13,.83],[0,.13,.72],[-.09,.1,.91]]) {
        this.ellipse(x+dx*size,y+dy*size,z*size,.2*size,.11*size,dx<0?"#3d8267":"#69aa7b");
      }
    }
    sofa(x,y,w=1.5,color="#d79070") {
      this.box(x,y,w,.75,.3,color,.15);
      this.box(x,y,w,.17,.65,color,.1);
      this.box(x,y,.15,.75,.46,color,.1);
      this.box(x+w-.15,y,.15,.75,.46,color,.1);
      for(let i=0;i<2;i++)this.box(x+.2+i*(w-.4)/2,y+.24,(w-.45)/2,.4,.08,tintToHex(color,16),.45);
    }
    table(x,y,w,d,color="#d6b88e") {
      for(const [dx,dy] of [[.06,.06],[w-.1,.06],[.06,d-.1],[w-.1,d-.1]])this.box(x+dx,y+dy,.07,.07,.5,"#59626a");
      this.box(x,y,w,d,.12,color,.5);
    }
    bed(x,y) {
      this.box(x,y,1.35,1.9,.22,"#ac917e");
      this.box(x,y,1.35,.13,.85,"#b8a695");
      this.box(x+.04,y+.1,1.27,1.75,.25,"#fff4df",.22);
      this.box(x+.04,y+.75,1.27,1.1,.04,"#91b3b3",.47);
      this.box(x+.14,y+.2,.46,.32,.12,"#ffffff",.47);
      this.box(x+.75,y+.2,.46,.32,.12,"#ffffff",.47);
    }
    treadmill(x,y) {
      this.box(x,y,.55,1.05,.13,"#526470");
      this.box(x+.07,y+.15,.41,.78,.03,"#273b45",.13);
      this.box(x+.06,y+.03,.05,.1,.7,"#aab7bc");
      this.box(x+.45,y+.03,.05,.1,.7,"#aab7bc");
      this.box(x,y,.55,.17,.08,"#364d59",.7);
      this.box(x+.15,y+.02,.23,.1,.03,"#65c1b8",.78);
    }
    scenery(time) {
      const c=this.ctx;
      this.floor(.05,.05,11.9,8.6,"#00000030",-.55);
      this.box(.25,.25,11.5,8.2,.3,"#87979c",-.3);
      this.floor(.3,.3,11.4,8.1,"#e7e3d9");
      // Broad public corridor and five fully fictional, furnished rooms.
      this.floor(.4,2.7,11.2,.9,"#d2d8d1");
      this.floor(.5,.45,3.0,2.2,"#d1d9dc");
      this.floor(.5,4.8,3.8,3.1,"#e8c9aa");
      this.floor(4.8,3.65,1.95,4.25,"#b3c2c2");
      this.floor(6.95,4.8,4.35,3.1,"#ddc2a3");
      this.floor(7.45,.45,3.85,2.2,"#e9d9b9");
      // Wood planks / tiles remain under navigation markings.
      for(let x=.5;x<11.5;x+=.5)this.line([this.project(x,.4,.02),this.project(x,8.2,.02)],"#52697010");
      for(let y=.4;y<8.3;y+=.5)this.line([this.project(.4,y,.02),this.project(11.5,y,.02)],"#52697010");
      this.floor(9.7,.65,1.35,1.65,"#4ba4b1",.02);
      this.floor(9.82,.77,1.11,1.41,"#75c7d0",.03);
      c.save();c.globalAlpha=.55;
      for(let i=0;i<7;i++) {
        const y=.84+i*.19,drift=this.reduced?0:Math.sin(time/850+i)*.025;
        this.line([this.project(9.86,y+drift,.05),this.project(10.88,y+drift+.03,.05)],"#d9ffff",1.2);
      }c.restore();
      this.floor(.95,5.75,2.5,1.9,"#b5826648");
      this.floor(7.5,5.45,3.25,2.25,"#bb8e7440");
      // Door thresholds make the server's access lanes visible.
      for(const [x,y] of [[1.5,2.7],[3,4.8],[6,3.65],[8.5,4.8],[9,2.7]])this.floor(x-.4,y-.05,.8,.12,"#f6e9c9");
      this.floor(1.15,1.15,.7,.7,"#669da5");
      this.floor(1.23,1.23,.54,.54,"#93d8ce");
      const dock=this.project(1.5,1.5,.04);
      c.font=`bold ${Math.max(10,this.scale*.38)}px system-ui`;c.fillStyle="#faffed";c.textAlign="center";c.fillText("ϟ",dock[0],dock[1]+4);
    }
    objects() {
      const items=[];
      const add=(x,y,draw)=>items.push({depth:x+y,draw});
      const box=(x,y,w,d,h,color,z=0)=>add(x+w/2,y+d/2,()=>this.box(x,y,w,d,h,color,z));
      // Low cutaway walls and glazed partitions: doors stay on every route.
      box(.35,.35,11.25,.12,.62,"#d5dedb");
      box(.35,.35,.12,7.8,.62,"#d5dedb");
      box(3.45,.45,.1,2.15,.55,"#c4d5d5");
      box(.5,4.7,2.0,.12,.65,"#e8dbca");
      box(3.5,4.7,.8,.12,.65,"#e8dbca");
      box(4.25,4.8,.12,3.1,.28,"#e8dbca");
      box(6.85,4.7,1.2,.12,.6,"#e2d7c8");
      box(9.0,4.7,2.3,.12,.6,"#e2d7c8");
      box(11.3,4.8,.12,3.1,.35,"#e2d7c8");
      box(4.75,3.7,.1,4.2,.27,"#d4dedd");
      box(6.7,3.7,.1,4.2,.27,"#d4dedd");
      // Reception console, charging column and green lounge.
      box(2.35,.85,.65,1.2,.55,"#acb9bb");
      box(2.3,.8,.75,1.3,.09,"#f2eee4",.55);
      box(2.4,1.08,.07,.48,.45,"#435b69",.64);
      box(.75,.6,.22,.65,.72,"#718d9d");
      add(1.2,6.6,()=>this.sofa(.8,6.05,1.6));
      add(2.2,7.2,()=>this.table(1.1,7.02,1.5,.5));
      add(2.6,5.6,()=>this.table(1.9,5.1,1.3,.75,"#ecddbf"));
      box(2.1,5.2,.35,.35,.025,"#6f9eb0",.63);
      box(3.4,7.1,.48,.48,.42,"#cb977b");
      // Show apartment: bed, side table, sofa and warm cabinet.
      add(10.25,6.3,()=>this.bed(9.5,5.8));
      add(7.6,6.1,()=>this.sofa(7.2,5.65,1.0,"#c6b3a2"));
      add(7.7,7.3,()=>this.table(7.3,7.05,.9,.55));
      box(10.95,5.95,.28,.45,.45,"#a9886b");
      box(10.97,6.04,.22,.22,.3,"#fae3ad",.5);
      // Gym equipment sits beside the lane, not at the POI.
      add(5.35,5.6,()=>this.treadmill(5.08,5.05));
      add(6.08,6.8,()=>this.treadmill(5.8,6.25));
      box(5.1,7.2,1.3,.23,.25,"#64747d");
      for(let i=0;i<4;i++)box(5.17+i*.28,7.2,.16,.22,.11,"#314955",.26);
      // Pool deck and loungers.
      box(7.8,.75,.52,1.1,.15,"#f4eee0");
      box(8.45,.75,.52,1.1,.15,"#f4eee0");
      box(7.8,.75,.52,.24,.3,"#e5c2a0",.15);
      box(8.45,.75,.52,.24,.3,"#e5c2a0",.15);
      for(const [x,y,s] of [[.8,3.9,1.1],[3.85,3.9,.8],[4.25,1.1,1.2],[6.45,1.2,1.2],[11.05,3.85,1.1],[.8,7.7,.7],[10.95,7.8,.7]])add(x,y,()=>this.plant(x,y,s));
      add(this.robot[0],this.robot[1],()=>this.drawRobot());
      items.sort((a,b)=>a.depth-b.depth);items.forEach(item=>item.draw());
    }
    path(time) {
      const state=this.state;if(!state)return;
      const c=this.ctx,points=state.route||[];
      if(points.length>1) {
        const color=state.phase==="blocked"?palette.amber:palette.mint;
        this.line(points.map(p=>this.project(...p,.035)),"#173d4855",8);
        c.save();c.setLineDash([3,10]);c.lineDashOffset=this.reduced?0:-time/65;
        this.line(points.map(p=>this.project(...p,.045)),color,3);c.restore();
        if(state.phase==="blocked") {
          const [x,y]=this.project(...state.position,.12);
          c.fillStyle="#ffb576";c.beginPath();c.moveTo(x,y-15);c.lineTo(x+14,y+10);c.lineTo(x-14,y+10);c.closePath();c.fill();
        }
      }
      for(const point of state.points) {
        const active=point.name===this.selected || point.name===state.destination;
        const [x,y]=this.project(point.x,point.y,.035);
        c.strokeStyle=active?palette.mint:"#70888370";c.lineWidth=active?2:1;
        c.beginPath();c.ellipse(x,y,this.scale*.38,this.scale*.2,0,0,TAU);c.stroke();
        if(active) {
          c.fillStyle="#7affe633";c.fill();
          const pulse=this.reduced?.5:(time%1800)/1800;
          c.globalAlpha=1-pulse;c.beginPath();c.ellipse(x,y,this.scale*(.4+pulse*.35),this.scale*(.21+pulse*.18),0,0,TAU);c.stroke();c.globalAlpha=1;
        }
      }
    }
    drawRobot() {
      const c=this.ctx,s=this.scale,[x,y]=this.project(...this.robot),time=this.time;
      const moving=this.state?.moving===true && this.state?.phase!=="blocked";
      const bob=this.reduced?0:Math.sin(time/(moving?95:550))*s*(moving?.025:.008);
      this.ellipse(...this.robot,.02,.4,.19,"#152f4140");
      c.save();c.translate(x,y+bob);
      const round=(x,y,w,h,r,color)=> {c.fillStyle=color;c.beginPath();c.roundRect(x*s,y*s,w*s,h*s,r*s);c.fill();};
      round(-.26,-.2,.18,.19,.07,"#31495a");round(.08,-.2,.18,.19,.07,"#31495a");
      const body=c.createLinearGradient(-.3*s,0,.35*s,0);body.addColorStop(0,"#a9c5ce");body.addColorStop(.4,"#fffdf1");body.addColorStop(1,"#d6e7e6");
      round(-.29,-.91,.58,.76,.2,body);
      const arm=moving&&!this.reduced?Math.sin(time/95)*.07:0;
      round(-.42,-.83+arm,.13,.48,.065,"#e2eeea");round(.29,-.83-arm,.13,.48,.065,"#b7d1d4");
      round(-.17,-.72,.34,.3,.07,"#294957");
      round(-.105,-.65,.21,.055,.02,palette.mint);
      round(-.07,-.51,.14,.03,.015,"#799e9e");
      round(-.4,-1.53,.8,.64,.27,body);
      round(-.315,-1.4,.63,.31,.13,"#143a4b");
      const blink=this.reduced?false:time%4800>4590;
      const eye=this.state?.connected===false?"#ffb880":palette.mint;
      round(-.18,-1.3,.1,blink?.022:.075,.035,eye);round(.085,-1.3,.1,blink?.022:.075,.035,eye);
      round(-.055,-1.16,.11,.025,.012,"#70c8c5");
      round(-.055,-1.69,.11,.17,.04,"#afccd0");
      round(-.065,-1.74,.13,.095,.05,palette.mint);
      c.restore();
    }
    tags() {
      if(!this.state)return;
      this.hits=[];const c=this.ctx;
      for(const point of this.state.points) {
        const location=labels[point.name]||[point.x,point.y];
        const [x,y]=this.project(...location,.2);
        const selected=point.name===this.selected,hover=point.name===this.hover;
        const label=names[point.name]||point.name;
        c.font="600 12px 'Leelawadee UI',Tahoma,sans-serif";
        const w=c.measureText(label).width+28,h=29,left=x-w/2,top=y-13;
        c.fillStyle=selected?"#315c59":hover?"#304357":"#142331e8";
        c.strokeStyle=selected?palette.mint:"#7c94964d";c.lineWidth=1;
        c.beginPath();c.roundRect(left,top,w,h,8);c.fill();c.stroke();
        c.fillStyle=selected?"#baffdf":"#dce8e8";c.textAlign="center";c.fillText(label,x,top+19);
        this.hits.push({name:point.name,x:left,y:top,w,h});
        const marker=this.project(point.x,point.y,.05);
        this.hits.push({name:point.name,x:marker[0]-17,y:marker[1]-17,w:34,h:34});
      }
      const [x,y]=this.project(...this.robot,1.98);
      c.font="bold 10px system-ui";c.textAlign="center";c.fillStyle=palette.mint;c.fillText("EMMA",x,y);
    }
    mini() {
      if(!this.minimap||!this.state)return;
      const c=this.minimap.getContext("2d"),w=this.minimap.width,h=this.minimap.height;
      c.clearRect(0,0,w,h);c.fillStyle="#152432";c.fillRect(0,0,w,h);
      const p=([x,y])=>[10+x/12*(w-20),h-8-y/8.5*(h-16)];
      c.strokeStyle="#456067";c.lineWidth=6;c.beginPath();c.moveTo(...p([1,3.1]));c.lineTo(...p([11,3.1]));c.stroke();
      if(this.state.route?.length) {c.strokeStyle=palette.mint;c.lineWidth=1.5;c.beginPath();this.state.route.forEach((point,i)=>i?c.lineTo(...p(point)):c.moveTo(...p(point)));c.stroke();}
      for(const point of this.state.points){c.fillStyle=point.name===this.selected?palette.amber:"#70888c";const [x,y]=p([point.x,point.y]);c.fillRect(x-2,y-2,4,4);}
      const [x,y]=p(this.robot);c.fillStyle=palette.mint;c.beginPath();c.arc(x,y,3.5,0,TAU);c.fill();
    }
    frame(time) {
      if(!this.width||!this.height||document.hidden)return;
      const dt=Math.min(60,time-(this.lastFrame||time));this.lastFrame=time;this.time=time;
      if(this.state) {
        const blend=this.reduced?1:1-Math.exp(-dt/65);
        this.robot=this.robot.map((v,i)=>v+(this.state.position[i]-v)*blend);
      }
      this.scale=Math.min(this.width/23.5,this.height/16)*this.zoom;
      this.ox=this.width/2-this.scale*1.5+this.pan.x;
      this.oy=this.height/2-this.scale*5.15+this.pan.y;
      if(this.follow) {this.ox=this.width/2-(this.robot[0]-this.robot[1])*this.scale;this.oy=this.height/2-(this.robot[0]+this.robot[1])*.52*this.scale+this.scale*.5;}
      const c=this.ctx;c.setTransform(this.dpr,0,0,this.dpr,0,0);
      const bg=c.createRadialGradient(this.width*.52,this.height*.43,10,this.width*.5,this.height*.5,this.width*.7);
      bg.addColorStop(0,"#253b47");bg.addColorStop(1,"#101b2a");c.fillStyle=bg;c.fillRect(0,0,this.width,this.height);
      c.fillStyle="#819ca518";for(let x=16;x<this.width;x+=28)for(let y=16;y<this.height;y+=28)c.fillRect(x,y,1,1);
      this.scenery(time);this.path(time);this.objects();this.tags();this.mini();
      if(this.arrivalAt && time-this.arrivalAt<1600 && !this.reduced) {
        const t=(time-this.arrivalAt)/1600,[x,y]=this.project(...this.robot,.05);
        c.globalAlpha=1-t;c.strokeStyle=palette.mint;c.lineWidth=3;c.beginPath();c.ellipse(x,y,this.scale*(.5+2*t),this.scale*(.25+t),0,0,TAU);c.stroke();c.globalAlpha=1;
      }
    }
  }
  function tintToHex(hex,delta) {
    const value=parseInt(hex.slice(1),16);
    return "#"+[16,8,0].map(shift=>Math.min(255,Math.max(0,((value>>shift)&255)+delta)).toString(16).padStart(2,"0")).join("");
  }
  window.RobotScene=RobotScene;
})();
