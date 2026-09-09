/* Loaded only by the simulator's existing voice client. No second microphone. */
"use strict";
(() => {
  let health, sessionId = null, providerState = 'idle', error = 'none';
  let releaseLock = null, acquiring = null, lastMic = 0, previous = '', timer;
  const clientId = crypto.randomUUID();
  const labels = {
    live:'พร้อม', flowing:'มีเสียงเข้า', muted:'ปิดเสียง', denied:'ไม่ได้รับสิทธิ์',
    unavailable:'ไม่พร้อม', idle:'ยังไม่เปิด', running:'พร้อมเล่น', suspended:'รอแตะเปิดเสียง',
    closed:'ปิดแล้ว', connected:'ต่อสายแล้ว', connecting:'กำลังต่อสาย',
    listening:'รอฟังชื่อ', disabled:'ปิดอยู่', error:'มีข้อผิดพลาด', busy:'อีกแท็บใช้อยู่',
    none:'—', permission:'สิทธิ์เครือข่าย/อุปกรณ์', quota:'โควตาเต็ม', auth:'ตรวจสิทธิ์/API key',
    network:'เชื่อมต่อไม่ได้', provider:'บริการเสียงขัดข้อง', missing_key:'ยังไม่ตั้ง API key',
  };
  const api = window.simulatorVoice = {
    ownsAudio:false,
    async acquire() {
      if (api.ownsAudio) return true;
      if (acquiring) return acquiring;
      acquiring = new Promise(resolve => {
        if (!navigator.locks) { api.ownsAudio = true; resolve(true); return; }
        navigator.locks.request('emma-audio-owner', {ifAvailable:true}, async lock => {
          api.ownsAudio = !!lock;
          resolve(!!lock);
          if (lock) await new Promise(done => { releaseLock = done; });
        }).catch(() => { api.ownsAudio = false; resolve(false); });
      });
      const ok = await acquiring; acquiring = null;
      if (!ok) { sleepNote('มีอีกแท็บใช้ไมค์อยู่ ปิดแท็บนั้นแล้วกดเริ่มคุยในหน้านี้'); publish(); }
      return ok;
    },
    async init(value) {
      health=value;
      const css=document.createElement('link'); css.rel='stylesheet'; css.href='/simulator-voice.css'; document.head.append(css);
      if (new URLSearchParams(location.search).get('embed') === '1') document.body.classList.add('sim-embedded');
      const panel=document.createElement('section'); panel.id='voiceReadiness'; panel.setAttribute('aria-label','ความพร้อมเสียง');
      panel.innerHTML='<div class="voice-checks"><span>ไมค์ <b id="checkMic">—</b></span><span>เสียงออก <b id="checkOutput">—</b></span><span>เรียกชื่อ <b id="checkWake">—</b></span><span>Gemini/AI <b id="checkProvider">—</b></span></div><div class="voice-check-actions"><button id="testSpeaker" type="button">♪ ทดสอบลำโพง</button><span id="checkAdvice" role="status">ช่วงรอฟังชื่อยังไม่เปิดสาย AI</span></div>';
      document.body.prepend(panel);
      document.getElementById('testSpeaker').onclick=testSpeaker;
      const meter=onMicLevel;
      onMicLevel = rms => { lastMic=performance.now(); meter(rms); };
      await api.acquire();
      timer=setInterval(publish,500); publish();
    },
    connecting() { providerState='connecting'; error='none'; sessionId=null; publish(); },
    ready(evt) { providerState='connected'; error='none'; sessionId=evt.diagnostic_session_id||null; publish(); },
    failed(evt) {
      providerState='error'; const message=String(evt.message||'').toLowerCase();
      error=evt.code==='simulator_busy'?'busy': /winerror 5|access is denied|permission/.test(message)?'permission'
        : /quota|429|resource.exhausted/.test(message)?'quota': /api.key|unauthorized|401|403/.test(message)?'auth'
        : /network|connect|timeout/.test(message)?'network':'provider'; publish();
    },
    closed() { if (providerState!=='error') providerState='idle'; publish(); },
  };
  function snapshot() {
    const track=(micStream||wakeMicStream)?.getAudioTracks()[0];
    const mic=micFailure ? (micFailure.name==='NotAllowedError'?'denied':'unavailable')
      : track?.readyState==='live' ? (muted||track.muted?'muted':performance.now()-lastMic<2000?'flowing':'live'):'idle';
    return {client_id:clientId,session_id:sessionId,mic,
      playback:(audioCtx||wakeCtx)?.state||'idle',
      wake: !api.ownsAudio?'busy':wakeServerReady&&wakeAudioFlowing?'listening':health?.wake?.ready?'idle':'disabled',
      provider:!health?.api_key_configured?'unavailable':providerState,
      error:!health?.api_key_configured?'missing_key':error};
  }
  function publish() {
    if (!health) return;
    const state=snapshot();
    for (const [id,key] of [['checkMic','mic'],['checkOutput','playback'],['checkWake','wake'],['checkProvider','provider']]) {
      const el=document.getElementById(id); if(el) el.textContent=labels[state[key]]||state[key];
    }
    const advice=document.getElementById('checkAdvice');
    if(advice) advice.textContent=state.error!=='none'?labels[state.error]
      :state.wake==='busy'?'ปิดแท็บเสียงอื่น แล้วกดเริ่มคุย':state.playback==='suspended'?'แตะปุ่มหรือหน้าจอเพื่อเปิดเสียง'
      :state.provider==='connected'?'เชื่อมบริการเสียงแล้ว': 'ช่วงรอฟังชื่อยังไม่เปิดสาย AI';
    if (parent!==window) parent.postMessage({type:'emma-readiness',state},location.origin);
    const key=JSON.stringify(state);
    if (key!==previous) {
      previous=key;
      fetch('/ws/diagnostics',{method:'POST',headers:{'Content-Type':'application/json'},body:key})
        .then(r=>{if(!r.ok)previous='';}).catch(()=>{previous='';});
    }
  }
  async function testSpeaker() {
    const button=document.getElementById('testSpeaker'); button.disabled=true;
    let context=audioCtx||wakeCtx, own=false;
    if(!context||context.state==='closed'){context=new AudioContext();own=true;}
    // Runs directly from the user's gesture and never opens a microphone/provider.
    armAudioUnlock(context); context.resume().catch(()=>{});
    const gain=context.createGain(); gain.gain.value=.06; gain.connect(context.destination);
    const osc=context.createOscillator(); osc.frequency.value=660; osc.connect(gain);
    osc.start(); osc.stop(context.currentTime+.35);
    setTimeout(()=>{if(own)context.close().catch(()=>{});button.disabled=false;},800);
    document.getElementById('checkAdvice').textContent='ทดสอบเสียงในเครื่อง — หากไม่ได้ยินให้ตรวจระดับเสียงและอุปกรณ์ลำโพง';
  }
  window.addEventListener('pagehide',()=>{
    clearInterval(timer);api.ownsAudio=false;stopWakeMode();try{ws?.close();}catch{}
    releaseCallHardware();releaseLock?.();releaseLock=null;
  });
  window.addEventListener('pageshow',e=>{if(e.persisted)location.reload();});
})();
