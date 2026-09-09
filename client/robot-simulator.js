"use strict";
(async () => {
const $ = id => document.getElementById(id);
const phases = {idle:"พร้อมทดสอบ",moving:"กำลังเดินจำลอง",arrived:"ถึงจุดหมายจำลอง",stopped:"หยุดจำลองแล้ว",cancelled:"ยกเลิก",blocked:"มีสิ่งกีดขวาง",timeout:"หมดเวลารอ",navigation_failed:"นำทางไม่สำเร็จ",unknown:"ยังยืนยันผลไม่ได้",disconnected:"ไม่ทราบสถานะ"};
const reasons = {unknown_place:"ไม่รู้จักจุดหมาย",disconnected:"การเชื่อมต่อหลุด",busy:"มีงานเดินทางค้างอยู่",ack_lost:"ไม่ได้รับ ACK",id_conflict:"เลขคำสั่งซ้ำแต่เนื้อหาต่างกัน",arrived:"ถึงจุดหมายจำลองแล้ว",cancelled:"ยกเลิกการเดินทาง",navigation_failed:"นำทางล้มเหลว",timeout:"หมดเวลารอผล"};
let latest = null;
let eventKey = "";
const callbacks = {
  select: selectPoint,
  activate: name => { selectPoint(name); navigate().catch(error=>message(error.message,true)); },
  stop: () => tool("stop_moving").catch(error=>message(error.message,true)),
};
let scene;
try {
  const {RobotScene3D}=await import('/robot-scene-3d.js');
  scene=new RobotScene3D($("map"),callbacks);
  $("sceneStatus").textContent='3D · FLOOR 01';
} catch {
  // A canvas that attempted WebGL cannot always acquire a 2D context.
  const replacement=$("map").cloneNode(false);$("map").replaceWith(replacement);
  scene=new RobotScene(replacement,callbacks);
  replacement.dataset.renderer='2d';
  $("walkMode").disabled=true;$("walkPad").hidden=true;$("explorerHint").hidden=true;
  $("sceneStatus").textContent='2.5D · สำรอง';
  $("sceneModeHelp").textContent='อุปกรณ์นี้เปิด 3D ไม่สำเร็จ ใช้ฉากสำรอง · ลากเลื่อน · ล้อเมาส์ซูม';
}
function selectPoint(name) {
  scene.select(name);
  $("place").value=name;
  $("utterance").value=name==="base"?"ฐานชาร์จ":name;
  message(`เลือก${name==="base"?"ฐานชาร์จ":name}แล้ว กดเริ่มเดินทาง หรือสั่ง Emma ได้เลย`);
}
function navigate() {
  return $("place").value==="base" && $("utterance").value==="ฐานชาร์จ"
    ? tool("return_to_base") : tool("go_to_place",$("utterance").value);
}
function message(text, error=false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : "ข้อมูลไม่ถูกต้อง กรุณาตรวจค่าที่กรอก");
  }
  return response.json();
}
function render(state) {
  latest = state;
  $("connection").textContent = state.connected ? "เชื่อมต่อแล้ว" : "การเชื่อมต่อหลุด";
  $("phase").textContent = phases[state.phase] || state.phase;
  $("destination").textContent = state.destination === "base" ? "ฐานชาร์จ" : state.destination || "—";
  $("battery").textContent = `${state.battery}%${state.charging ? " · ชาร์จจำลอง" : ""}`;
  $("disconnect").textContent = state.connected ? "จำลองหลุดการเชื่อมต่อ" : "เชื่อมต่อจำลองอีกครั้ง";
  scene.setState(state);
  renderHome(state.smart_home);
  const progress=Math.round((state.progress||0)*100);
  $("journeyProgress").value=progress;
  $("missionTitle").textContent=state.destination
    ? `ไป${state.destination==="base"?"ฐานชาร์จ":state.destination}` : phases[state.phase]||"พร้อมออกเดินทาง";
  $("missionDetail").textContent=state.active_command_id
    ? `${progress}% · ${phases[state.phase]||state.phase}`
    : state.phase==="arrived" ? "ถึงจุดหมายในตัวจำลองแล้ว" : "คลิกจุดหมาย หรือสั่งด้วยเสียง Emma";
  if (!$("place").options.length) {
    for (const point of state.points) {
      $("place").add(new Option(point.name==="base"?"ฐานชาร์จ":point.name,point.name));
    }
    $("place").value=scene.selected;
  }
  const key = `${state.epoch}:${state.events.at(-1)?.seq}`;
  if (key !== eventKey) {
    eventKey = key;
    $("events").replaceChildren();
    for (const event of [...state.events].reverse()) {
      const row = document.createElement("tr");
      const details = event.detail || (event.result ? `${event.tool} → ${event.result.hardware || event.result.error}` : [event.action,event.place,reasons[event.reason] || event.reason,event.accepted === true ? "รับคำสั่งแล้ว" : event.accepted === false ? "ไม่ยืนยันการรับคำสั่ง" : ""].filter(Boolean).join(" · "));
      for (const value of [`${event.t.toFixed(1)}s`,event.type,details,event.command_id?.slice(0,8) || "—"]) {
        const cell = document.createElement("td"); cell.textContent=value; row.append(cell);
      }
      $("events").append(row);
    }
    const last = state.events.at(-1);
    if (["robot_arrived","connection_lost","navigation_timeout","obstacle"].includes(last?.type)) {
      message(last.detail || reasons[last.reason] || "พบสิ่งกีดขวางในตัวจำลอง",last.ok === false || last.type !== "robot_arrived");
    }
  }
}
function action(id, fn) {
  $(id).addEventListener("click",async () => {
    $(id).disabled=true;
    try { await fn(); } catch (error) { message(error.message,true); }
    finally { $(id).disabled=false; }
  });
}
async function tool(name, place="") {
  const data = await request("/api/tool",{tool:name,place});
  render(data.state);
  $("result").textContent=JSON.stringify(data.result,null,2);
  message(data.result.instruction || "อ่านสถานะจำลองแล้ว",data.result.ok === false);
}
$("place").addEventListener("change",()=>selectPoint($("place").value));
action("go",navigate);
action("stop",()=>tool("stop_moving"));
action("home",()=>tool("return_to_base"));
action("status",()=>tool("get_robot_status"));
action("configure",async()=> {
  render(await request("/api/config",{fault:$("fault").value,duration:Number($("duration").value),timeout:Number($("timeout").value)}));
  message("บันทึกสถานการณ์สำหรับคำสั่งถัดไปแล้ว");
});
action("disconnect",async()=>render(await request("/api/connection",{connected:!latest.connected})));
action("reset",async()=> {
  render(await request("/api/reset",{}));
  $("fault").value="none"; $("duration").value="6"; $("timeout").value="12";
  $("result").textContent="ยังไม่มีคำสั่ง"; message("เริ่มรอบใหม่แล้ว ประวัติรอบก่อนถูกล้าง");
  scene.center();
});
action("duplicate",async()=> {
  const command=latest?.commands.at(-1);
  if (!command) return message("ส่งคำสั่งเดินทางก่อน แล้วลองส่งเลขคำสั่งเดิมซ้ำ");
  const reply=await request("/api/command",{action:command.action,args:command.args,command_id:command.command_id});
  $("result").textContent=JSON.stringify(reply,null,2);
  message(reply.duplicate ? "รับรู้ว่าเป็นคำสั่งซ้ำ จึงไม่เริ่มเคลื่อนที่อีกครั้ง" : reasons[reply.reason] || "ส่งคำสั่งแล้ว");
  render(await request("/api/state"));
});
action("export",async()=> {
  const [state,diagnostics]=await Promise.all([request("/api/state"),request("/api/diagnostics")]);
  if(state.epoch!==diagnostics.epoch)throw new Error("รอบจำลองเปลี่ยนระหว่างบันทึก กรุณากดอีกครั้ง");
  const data={format:"condo-voice-robot-simulation-v2",exported_at:new Date().toISOString(),limitations:"Command simulation only; not Aobo SDK or hardware validation",...state,voice_diagnostics:diagnostics};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
  const anchor=document.createElement("a"); anchor.href=url; anchor.download=`robot-simulation-${state.epoch}.json`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
action("zoomIn",()=>scene.zoomBy(1.15));
action("walkMode",()=>scene.toggleWalk?.());
action("zoomOut",()=>scene.zoomBy(1/1.15));
action("centerCamera",()=>scene.center());
action("followRobot",()=>scene.toggleFollow());
action("fullscreen",async()=> {
  if(document.fullscreenElement) await document.exitFullscreen();
  else if($("world").requestFullscreen) await $("world").requestFullscreen();
  else message("เบราว์เซอร์นี้ไม่รองรับเต็มจอ ใช้ปุ่มซูมแทนได้ครับ");
});
async function poll() {
  try { render(await request("/api/state")); }
  catch { message("ติดต่อเซิร์ฟเวอร์ตัวจำลองไม่ได้ สถานะบนจออาจเป็นข้อมูลเก่า",true); $("connection").textContent="ติดต่อเซิร์ฟเวอร์ไม่ได้"; }
  setTimeout(poll,250);
}
poll();

const diagnosticLabels={idle:"ยังไม่เปิดสาย",connecting:"กำลังต่อสาย",connected:"เชื่อม AI แล้ว",error:"มีข้อผิดพลาด",permission:"ตรวจสิทธิ์เครือข่าย",quota:"โควตาเต็ม",auth:"ตรวจ API key/สิทธิ์",network:"ต่อเครือข่ายไม่ได้",provider:"บริการเสียงขัดข้อง"};
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$("emmaVoice").contentWindow||event.data?.type!=="emma-readiness")return;
  const s=event.data.state;
  const mic={live:"ไมค์พร้อม",flowing:"ไมค์มีข้อมูลเข้า",muted:"ไมค์ปิดเสียง",denied:"ไมค์ไม่ได้รับสิทธิ์",unavailable:"ไมค์ไม่พร้อม",idle:"ไมค์ยังไม่เปิด"};
  const playback={running:"เสียงออกพร้อม",suspended:"แตะเพื่อเปิดเสียง",closed:"เสียงปิดแล้ว",idle:"ยังไม่เปิดเสียง"};
  $("voiceSummary").textContent=s.wake==="busy"?"มีอีกแท็บใช้ไมค์อยู่ ปิดแท็บนั้นก่อนเริ่มคุยที่นี่":`${mic[s.mic]||"กำลังตรวจไมค์"} · ${playback[s.playback]||"กำลังตรวจเสียง"} · ${diagnosticLabels[s.provider]||"ยังไม่ต่อ AI"}`;
});
let diagnosticKey='';
async function pollDiagnostics(){
  try{
    const data=await request('/api/diagnostics'),s=data.summary;
    $("diagnosticCount").textContent=`${s.retained} / ${s.limit} เหตุการณ์`;
    $("providerStatus").textContent=s.error!=='none'?(diagnosticLabels[s.error]||s.error):(diagnosticLabels[s.provider]||s.provider);
    for(const [id,value] of [['latency50',s.end_to_audio_p50_ms],['latency95',s.end_to_audio_p95_ms]])$(id).textContent=value===null?'ยังไม่มีข้อมูล':`${Math.round(value)} ms (${s.audio_samples} ตัวอย่าง)`;
    const key=`${data.epoch}:${s.sequence}`;
    if(key!==diagnosticKey){
      diagnosticKey=key;$("diagnosticRows").replaceChildren();
      for(const entry of data.events.slice(-35).reverse()){
        const row=document.createElement('tr');
        const detail=Object.entries(entry).filter(([k,v])=>!['seq','t','event','client_id','session_id','command_id'].includes(k)&&v!==null).map(([k,v])=>`${k}: ${v}`).join(' · ');
        for(const value of [`${entry.t.toFixed(1)}s`,entry.event,detail||'—']){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
        $("diagnosticRows").append(row);
      }
    }
  }catch{$("providerStatus").textContent='ติดต่อเซิร์ฟเวอร์ไม่ได้';}
  setTimeout(pollDiagnostics,document.hidden?5000:1200);
}
pollDiagnostics();

function renderHome(home){
  if(!home)return;
  for(const [id,on] of [['lightsToggle',home.lights.on],['acToggle',home.ac.on],['tvToggle',home.tv.on]]){
    $(id).textContent=on?'เปิดอยู่ · กดปิด':'ปิดอยู่ · กดเปิด';$(id).setAttribute('aria-pressed',String(on));
  }
  $("curtainToggle").textContent=home.curtains.position>0?'ปิดม่าน':'เปิดม่าน';
  $("curtainToggle").setAttribute('aria-pressed',String(home.curtains.position>0));
  for(const [id,value] of [['lightLevel',home.lights.brightness],['acTemperature',home.ac.temperature],['curtainLevel',home.curtains.position]]){
    if(document.activeElement!==$(id))$(id).value=value;
  }
  $("lightValue").value=`${home.lights.brightness}%`;$("curtainValue").value=`${home.curtains.position}%`;
  if($("homeFeedback").textContent==='กำลังอ่านสถานะอุปกรณ์…')$("homeFeedback").textContent='พร้อมรับคำสั่งจำลอง';
}
async function homeCommand(body){
  const data=await request('/api/home',body);render(data.state);
  $("homeFeedback").textContent=data.result.ok?'เปลี่ยนสถานะในฉากแล้ว':data.result.error;
}
for(const [id,device] of [['lightsToggle','lights'],['acToggle','ac'],['tvToggle','tv']])action(id,()=>homeCommand({device,on:!latest.smart_home[device].on}));
action('curtainToggle',()=>homeCommand({device:'curtains',on:latest.smart_home.curtains.position===0}));
for(const [id,device] of [['lightLevel','lights'],['acTemperature','ac'],['curtainLevel','curtains']]){
  $(id).addEventListener('change',async()=>{
    if(!$(id).reportValidity())return;
    $(id).disabled=true;
    try{await homeCommand({device,value:Number($(id).value)});}catch(error){$("homeFeedback").textContent=error.message;}finally{$(id).disabled=false;}
  });
}
})();
