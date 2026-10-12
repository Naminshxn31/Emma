const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const createGate = require(path.join(root, 'client/assets/echo-barge-gate.js'));
const html = fs.readFileSync(path.join(root, 'client/voice-preview.html'), 'utf8');
assert.match(html, /assets\/echo-barge-gate\.js/);

function runGate(after) {
  const gate = createGate();
  for (let t = 0; t <= 140; t += 20) assert.equal(gate.sample(0.006, t).action, 'hold');
  for (let t = 160; t < 220; t += 20) assert.equal(gate.sample(0.025, t).action, 'hold');
  assert.equal(gate.sample(0.025, 220).action, 'duck');
  let result;
  for (let t = 240; t <= 340; t += 20) result = gate.sample(after, t);
  return result.action;
}
assert.equal(runGate(0.015), 'restore', 'speaker bleed must fall with the duck');
assert.equal(runGate(0.023), 'barge', 'a continuing person must pass the duck test');

{
  const gate = createGate();
  for (let t = 0; t <= 140; t += 20) gate.sample(0.006, t);
  assert.equal(gate.sample(0.025, 160).action, 'hold');
  assert.equal(gate.sample(0.006, 180).action, 'clear', 'a brief loud syllable must not change speaker volume');
}

const body = 'function frameRms(frame) {' + html.split('function frameRms(frame) {', 2)[1]
  .split('async function startMic()', 1)[0];
function context() {
  const sent = [], ducks = [];
  const sb = {
    Int16Array, Math, Number, JSON,
    WebSocket: { OPEN: 1 },
    performance: { now: () => sb.now },
    now: 0, muted: false, playing: true, halfDuplex: false, bargeIn: true,
    bargeGate: createGate(), bargePreroll: [], bargeFrames: [],
    echoGuardUntil: 0, bargePendingUntil: 0, bargeRelayUntil: 0, sentMs: 900,
    ws: { readyState: 1, send: value => sent.push(value) },
    duckPlayback: factor => ducks.push(factor),
    stopPlayback: () => { sb.playing = false; sb.bargeFrames = []; sb.echoGuardUntil = sb.now + 350; return 400; },
    show: () => {}, status: () => {},
  };
  vm.createContext(sb);
  vm.runInContext(body, sb);
  return { sb, sent, ducks };
}
function frame(rms) {
  return new Int16Array(320).fill(Math.round(rms * 32768)).buffer;
}
function feed(sb, rms, at) {
  sb.now = at;
  sb.handleMicAudio(frame(rms));
}

{
  const { sb, sent, ducks } = context();
  for (let t = 0; t <= 140; t += 20) feed(sb, 0.006, t);
  for (let t = 160; t <= 220; t += 20) feed(sb, 0.025, t);
  for (let t = 240; t <= 340; t += 20) feed(sb, 0.015, t);
  assert.equal(sent.length, 0, 'echo-only probe must never reach the provider');
  assert.deepEqual(ducks, [0.6, 1]);
  for (let t = 360; t <= 1400; t += 20) feed(sb, 0.025, t);
  assert.deepEqual(ducks, [0.6, 1], 'speaker gain must stay steady during the probe cooldown');
}
{
  const { sb, sent, ducks } = context();
  for (let t = 0; t <= 140; t += 20) feed(sb, 0.006, t);
  for (let t = 160; t <= 220; t += 20) feed(sb, 0.025, t);
  for (let t = 240; t <= 340; t += 20) feed(sb, 0.023, t);
  assert.equal(sb.playing, false, 'confirmed barge-in stops Emma immediately');
  assert.deepEqual(ducks, [0.6]);
  assert.deepEqual(JSON.parse(sent[0]), { type: 'barge_in' });
  assert.deepEqual(JSON.parse(sent[1]), { type: 'played', heard_ms: 400 });
  assert.ok(sent.length > 4, 'buffered human speech reaches the provider');
  const before = sent.length;
  feed(sb, 0.010, 360);
  assert.equal(sent.length, before + 1, 'the rest of the utterance passes the acoustic tail');
}
{
  const { sb, sent } = context();
  sb.playing = false;
  sb.halfDuplex = true;
  sb.bargeIn = false;
  sb.echoGuardUntil = 350;
  feed(sb, 0.010, 200);
  assert.equal(sent.length, 0, 'ordinary mode rejects speaker decay after playback');
  feed(sb, 0.010, 400);
  assert.equal(sent.length, 1, 'ordinary mode listens again after decay');
}
console.log('echo barge gate: ok');
