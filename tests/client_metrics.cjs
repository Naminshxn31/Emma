// Run with node tests/client_metrics.cjs; no browser or microphone is opened.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('client/index.html', 'utf8');
for (const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) {
  new vm.Script(match[1]);
}
const code = html.slice(html.indexOf('// BEGIN CLIENT METRICS'), html.indexOf('// ---------------- call ----------------'));
let now = 0;
const reports = [];
let stopped = 0;
const context = vm.createContext({
  performance: {now: () => now++}, WebSocket: {OPEN: 1},
  ws: {readyState: 1, send: value => reports.push(JSON.parse(value))},
  audioCtx: {state: 'running', currentTime: 5, destination: {},
    resume: async () => {},
    createBuffer: (_channels, length, rate) => ({duration: length / rate, copyToChannel() {}}),
    createBufferSource: () => ({connect() {}, start() {}, stop() {stopped++;}})},
  outputRate: 24000, playHead: 5.4, sources: [], playing: false, sentMs: 1000,
  setState() {}, reportAudioLead() {}, lastReportedLead: -1,
});
vm.runInContext(code + '\nmetricTurn="turn-1";', context);
vm.runInContext('playChunk(new ArrayBuffer(960)); playChunk(new ArrayBuffer(960));', context);
assert.equal(reports.length, 1);
assert.equal(reports[0].name, 'playback_schedule_ms');
assert.ok(Math.abs(reports[0].ms - 401) < .01);
assert.equal(reports[0].turn_id, 'turn-1');
vm.runInContext('stopPlayback()', context);
assert.equal(stopped, 2);
assert.equal(context.playHead, context.audioCtx.currentTime);
vm.runInContext('metricTurn=null; reportClientMetric("playback_schedule_ms", 1)', context);
assert.equal(reports.length, 1);
console.log('Client syntax, first-chunk metric, queue timing and interruption: PASS');
