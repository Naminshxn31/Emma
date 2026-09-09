// Regression checks for the first audio arriving before microphone setup.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('client/index.html', 'utf8');
const playback = html.slice(html.indexOf('// BEGIN CLIENT METRICS'), html.indexOf('// ---------------- call ----------------'));
const mic = html.slice(html.indexOf('async function startMic()'), html.indexOf('// What the call\'s microphone'));

function rig({suspended = false, handover = false} = {}) {
  const calls = [], scheduled = [], states = [];
  let openMic, denyMic;
  let stopped = 0;
  const track = {readyState: 'live', stop() {stopped++;}};
  const stream = {getTracks: () => [track], getAudioTracks: () => [track]};
  class Audio {
    constructor() {
      calls.push('context'); this.state = suspended ? 'suspended' : 'running';
      this.currentTime = 0; this.destination = {};
      this.audioWorklet = {addModule: async () => {}};
    }
    resume() {calls.push('resume'); return suspended ? new Promise(() => {}) : Promise.resolve();}
    createBuffer(channels, length, rate) {
      return {duration: length / rate, rate, copyToChannel(samples) {this.samples = [...samples];}};
    }
    createBufferSource() {
      return {connect() {}, start(at) {scheduled.push({at, buffer: this.buffer});}, stop() {}};
    }
    createMediaStreamSource() {return {};}
  }
  const standby = handover ? new Audio() : null;
  const context = vm.createContext({
    performance: {now: () => 0}, WebSocket: {OPEN: 1},
    ws: {readyState: 1, send() {}}, window: {AudioContext: Audio},
    navigator: {mediaDevices: {getUserMedia: () => {calls.push('mic'); return new Promise((resolve, reject) => {openMic = resolve; denyMic = reject;});}}},
    Blob: class {}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    AudioWorkletNode: class {constructor() {this.port = {};}}, WORKLET_SRC: '',
    audioCtx: null, micStream: null, micSource: null, worklet: null, micFailure: null,
    wakeHandover: handover, wakeCtx: standby, wakeMicStream: handover ? stream : null, wakeTail: [],
    outputRate: 24000, inputRate: 16000, playHead: 0, sources: [], playing: false, sentMs: 0,
    micNS: true, micAGC: false, micCallBoost: 1, muted: false, halfDuplex: true,
    armAudioUnlock: () => calls.push('unlock'), reportMicState() {},
    buildMicChain: () => ({connect() {}}), onMicLevel() {},
    stopWakeMode() {context.wakeHandover = false;},
    setState: state => states.push(state), systemLine() {}, reportAudioLead() {}, lastReportedLead: -1,
  });
  vm.runInContext(playback + '\n' + mic, context);
  return {context, calls, scheduled, states, standby,
    resolveMic: () => openMic(stream), rejectMic: () => denyMic({name: 'NotAllowedError'}), stopped: () => stopped};
}

(async () => {
  const r = rig();
  const pending = vm.runInContext('startMic()', r.context);
  assert.deepEqual(r.calls.slice(0, 4), ['context', 'unlock', 'resume', 'mic']);
  vm.runInContext('playChunk(new Int16Array([1200, -1200]).buffer)', r.context);
  assert.equal(r.scheduled.length, 1, 'first greeting chunk survives pending microphone');
  assert.ok(r.scheduled[0].buffer.samples[0] > 0);
  r.resolveMic(); await pending;
  assert.equal(r.states.at(-1), 'speaking', 'mic readiness must not override playing greeting');
  const oldSource=r.context.sources[0], stateCount=r.states.length;
  r.context.audioCtx=null; oldSource.onended();
  assert.equal(r.states.length,stateCount,'old playback callback must not change a new call state');

  const denied = rig();
  const deniedStart = vm.runInContext('startMic()', denied.context);
  denied.rejectMic(); await deniedStart;
  vm.runInContext('playChunk(new Int16Array([1200, -1200]).buffer)', denied.context);
  assert.equal(denied.scheduled.length, 1, 'refused microphone must not disable playback');

  const locked = rig({suspended: true});
  const lockedStart = vm.runInContext('startMic()', locked.context);
  assert.ok(locked.calls.indexOf('unlock') < locked.calls.indexOf('resume'));
  locked.resolveMic(); await lockedStart;
  vm.runInContext('playChunk(new Int16Array([1200, -1200]).buffer)', locked.context);
  assert.equal(locked.scheduled.length, 1, 'audio waits on the suspended playback clock');

  const handed = rig({handover: true});
  await vm.runInContext('startMic()', handed.context);
  assert.equal(handed.context.audioCtx, handed.standby, 'reuse the unlocked standby context');
  assert.equal(handed.context.wakeCtx, null, 'wake teardown must not close playback');
  assert.equal(handed.calls.filter(x => x === 'context').length, 1);
  assert.ok(!handed.calls.includes('mic'), 'reuse the live microphone');
  vm.runInContext('playChunk(new Int16Array([1, 2]).buffer)', handed.context);
  assert.equal(handed.scheduled[0].buffer.rate, 24000);

  const stale = rig();
  const staleStart = vm.runInContext('startMic()', stale.context);
  stale.context.audioCtx = null; // The call ended while the permission prompt was open.
  stale.resolveMic(); await staleStart;
  assert.equal(stale.stopped(), 1);
  assert.equal(stale.context.micStream, null);
  console.log('First greeting, pending mic, suspended playback, wake handover and stale cleanup: PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
