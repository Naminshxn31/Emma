// Socket ownership, stale redial, the wake mic's unlock order and late mic
// cleanup on the desk page (client/index.html). Run by
// tests/test_voice_preview_page.py (cwd = repo root) as well as CI.
//
// startWakeMode() opens the mic through openPreferredMic() and reports a
// refusal through micDenied() since b5f3f50; the wake slice had neither, so
// every case died on a ReferenceError and the file was red for five weeks
// with nobody running it (audit 27 Oct). Both are in the vm now -- the real
// openPreferredMic, a counting micDenied -- and the refused case is checked.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('client/index.html','utf8');
const at=html.indexOf('  ws.onclose = () => {',html.indexOf('function startCall()'));
const closeCode=html.slice(at,html.indexOf('\n  };\n}',at)+6);
assert.ok(at>0&&closeCode.includes('releaseCallHardware'),'the call socket onclose slice was found');
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
const preferred=html.slice(html.indexOf('const ROBOT_MIC_LABEL'),html.indexOf('async function startRobotCamera()'));
assert.ok(wake.includes('openPreferredMic(')&&wake.includes('micDenied()'),'the startWakeMode slice was found');
assert.ok(preferred.includes('async function openPreferredMic('),'the openPreferredMic slice was found');
async function checkWake(stale,active=false,refused=false){
  let resolveMic,rejectMic,stops=0,denials=0,teardowns=0;const order=[];
  const stream={getTracks:()=>[{stop(){stops++;}}],getAudioTracks:()=>[{label:'Default'}]};
  class Socket{close(){} send(){}} Socket.OPEN=1;Socket.CLOSED=3;Socket.CONNECTING=0;
  class Audio{constructor(){this.state='suspended';this.destination={};this.audioWorklet={addModule:async()=>{}};}
    resume(){order.push('resume');return new Promise(()=>{});}
    createMediaStreamSource(){return {};}
    createGain(){return {gain:{},connect(){}};}}
  const c=vm.createContext({window:{AudioContext:Audio,isSecureContext:true},ws:active?{readyState:1}:null,
    navigator:{mediaDevices:{getUserMedia:()=>{order.push('mic');return new Promise((r,j)=>{resolveMic=r;rejectMic=j;});}}},
    kioskMode:false,micFailure:null,wakeCanBeRung:true,
    micDenied(){denials++;},stopWakeMode(){teardowns++;},
    wakeAvailable:true,wakeWs:null,wakeListens:true,wakeHandover:false,wakeStopping:false,
    wakeMicStream:null,wakeCtx:null,wakeWorklet:null,micNS:true,micAGC:false,
    location:{protocol:'http:',host:'testserver',search:''},URLSearchParams,WebSocket:Socket,
    armAudioUnlock(){order.push('unlock');},Blob:class{},URL:{createObjectURL(){return 'blob:test';},revokeObjectURL(){}},WORKLET_SRC:'',
    AudioWorkletNode:class{constructor(){this.port={};}connect(){}},buildMicChain(){return {connect(){}};}});
  vm.runInContext(preferred+'\n'+wake,c);const pending=vm.runInContext('startWakeMode()',c);
  if(active){await pending;assert.equal(resolveMic,undefined,'old sleep timer must not reopen mic during a call');return;}
  assert.equal(typeof resolveMic,'function','standby asked for the microphone');
  if(refused){
    rejectMic({name:'NotAllowedError'});await pending;
    assert.equal(denials,1,'a refused mic is said on the sleep screen');
    assert.equal(c.wakeListens,false);assert.equal(c.micFailure.name,'NotAllowedError');
    // the door camera rings this same socket: it stays open, so a fix takes effect
    assert.equal(c.wakeAvailable,true);assert.equal(teardowns,0);assert.ok(c.wakeWs);
    assert.equal(c.wakeCtx,null,'no standby audio without a mic');
    return;
  }
  if(stale)c.wakeWs=null;
  resolveMic(stream);await pending;
  assert.equal(denials,0);
  if(stale){assert.equal(stops,1);assert.equal(c.wakeMicStream,null);}
  else{assert.deepEqual(order,['mic','unlock','resume']);assert.equal(c.wakeMicStream,stream);
    assert.ok(c.wakeWorklet,'suspended resume must not block worklet setup');}
}
(async()=>{await checkWake(false);await checkWake(true);await checkWake(false,true);await checkWake(false,false,true);
  console.log('Socket ownership, stale redial, wake unlock, refused wake mic and late mic cleanup: PASS');})().catch(e=>{console.error(e);process.exitCode=1;});
