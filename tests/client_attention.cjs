// FOCUS_ATTENTION on the robot face: the page's socket handler hands the
// bearing to the 3D mascot, and the mascot maps it to a small head turn
// toward the person. Both pieces are executed from the shipped files.
// usage: node tests/client_attention.cjs   (cwd = repo root)
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');

const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const handler = html.slice(html.indexOf('    function onMessage(msg)'), html.indexOf('    // ---- dial / hang up ----'));
assert.ok(handler.includes("case 'attention':"), 'the handler has the attention case');

const calls = [];
const c = vm.createContext({
  window: { EmmaMascot: { attend(b) { calls.push(b); return true; } } },
  ArrayBuffer, JSON, isFinite, performance: { now: () => 1 },
});
vm.runInContext(handler, c);
const send = obj => c.onMessage({ data: JSON.stringify(obj) });

send({ type: 'attention', bearing: 12.5, source: 'mouth' });
send({ type: 'attention', bearing: -30, source: 'doa' });
send({ type: 'attention', bearing: null, source: null });
send({ type: 'attention', bearing: 'left' });
send({ type: 'attention' });
assert.deepEqual(calls, [12.5, -30, null, null, null], 'only a finite number is a bearing');

// A page whose 3D mascot never loaded (SVG fallback) and one whose mascot
// throws: the call's socket handler must survive both.
c.window = {};
send({ type: 'attention', bearing: 5 });
c.window = { EmmaMascot: { attend() { throw new Error('webgl lost'); } } };
send({ type: 'attention', bearing: 5 });
c.window = { EmmaMascot: { setAccent() {} } };            // an older mascot.js
send({ type: 'attention', bearing: 5 });
assert.equal(c.attendFromServer(5), false);

// The mascot: + bearing = the robot's left = a positive Neck turn (her own
// left, +x, toward the person), small and clamped, centre for anything else.
const js = fs.readFileSync('client/assets/emma-3d-lab/mascot.js', 'utf8');
const block = js.slice(js.indexOf('const ATTEND_GAIN'), js.indexOf('let attendTarget'));
assert.ok(block.includes('function attendYaw('), 'the mapping block was found');
const m = vm.createContext({ Math, Number });
vm.runInContext(block + '\nthis.attendYaw = attendYaw; this.MAX = ATTEND_MAX_YAW;', m);
assert.ok(m.attendYaw(20) > 0, 'a person on the robot\'s left turns her to her left');
assert.ok(m.attendYaw(-20) < 0);
assert.ok(Math.abs(m.attendYaw(10) - 10 * Math.PI / 180 * 0.35) < 1e-12);
assert.equal(m.attendYaw(90), m.MAX);
assert.equal(m.attendYaw(-90), -m.MAX);
assert.ok(m.MAX <= 0.12, 'the decal slides off the front past a small yaw');
for (const v of [null, undefined, NaN, Infinity, '', 'left']) assert.equal(m.attendYaw(v), 0, String(v));
console.log('Attention: page handler, fallbacks and the bearing-to-yaw map PASS');
