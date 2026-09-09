const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('client/index.html','utf8');
const at=html.indexOf('  ws.onclose = () => {',html.indexOf('function startCall()'));
const closeCode=html.slice(at,html.indexOf('\n  };\n}',at)+6);
let releases=0,dials=0;const timers=[],oldSocket={readyState:3};
const ctx=vm.createContext({ws:oldSocket,callSocket:oldSocket,window:{},
  releaseCallHardware(){releases++;},$(){return {classList:{contains(){return false;}}};},
  standDown:false,modeRestart:false,autoConnect:true,manualEnd:false,idleParked:false,
  redialDelay:2000,WebSocket:{OPEN:1,CONNECTING:0},startCall(){dials++;},
  setTimeout(fn){timers.push(fn);}});
vm.runInContext(closeCode,ctx);
const close=oldSocket.onclose;
ctx.ws={readyState:1};close();assert.equal(releases,0,'old close must not release new hardware');
ctx.ws=oldSocket;close();assert.equal(releases,1,'current close releases hardware');
ctx.ws={readyState:3};timers[0]();assert.equal(dials,0,'stale redial must not start another call');

const wake=html.slice(html.indexOf('async function startWakeMode()'),html.indexOf('async function micDenied()'));
async function checkWake(stale,active=false){
  let resolveMic,stops=0;const order=[];
  const stream={getTracks:()=>[{stop(){stops++;}}]};
  class Socket{close(){} send(){}} Socket.OPEN=1;Socket.CLOSED=3;
  class Audio{constructor(){this.state='suspended';this.destination={};this.audioWorklet={addModule:async()=>{}};}
    resume(){order.push('resume');return new Promise(()=>{});}
    createMediaStreamSource(){return {};}
    createGain(){return {gain:{},connect(){}};}}
  const c=vm.createContext({window:{AudioContext:Audio,isSecureContext:true},ws:active?{readyState:1}:null,
    navigator:{mediaDevices:{getUserMedia:()=>new Promise(r=>{resolveMic=r;})}},
    wakeAvailable:true,wakeWs:null,wakeListens:true,wakeHandover:false,wakeStopping:false,
    wakeMicStream:null,wakeCtx:null,wakeWorklet:null,micNS:true,micAGC:false,
    location:{protocol:'http:',host:'testserver',search:''},URLSearchParams,WebSocket:Socket,
    armAudioUnlock(){order.push('unlock');},Blob:class{},URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(){}},WORKLET_SRC:'',
    AudioWorkletNode:class{constructor(){this.port={};}connect(){}},buildMicChain(){return {connect(){}};}});
  vm.runInContext(wake,c);const pending=vm.runInContext('startWakeMode()',c);
  if(active){await pending;assert.equal(resolveMic,undefined,'old sleep timer must not reopen mic during a call');return;}
  if(stale)c.wakeWs=null;
  resolveMic(stream);await pending;
  if(stale){assert.equal(stops,1);assert.equal(c.wakeMicStream,null);}
  else{assert.deepEqual(order,['unlock','resume']);assert.ok(c.wakeWorklet,'suspended resume must not block worklet setup');}
}
(async()=>{await checkWake(false);await checkWake(true);await checkWake(false,true);console.log('Socket ownership, stale redial, wake unlock and late mic cleanup: PASS');})().catch(e=>{console.error(e);process.exitCode=1;});
