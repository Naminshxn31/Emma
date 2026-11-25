// The mute hold's page half, run from the real page source (tests/test_voice_preview_page.py).
// Owner, 5 Nov: "ปิดไมค์ แต่เหมือนรีแล้วก็กลับมาพูด". sessionStorage dies with the
// WebView, so a fresh page must learn the hold from the server; and only a
// person's tap may ever change what the server keeps.
process.env.TZ = 'Asia/Shanghai';   // the robot's Android clock: an hour ahead of the room it stands in
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'voice-preview.html'), 'utf8');

// ---- the engine: the hold, adoptHold, and the one place "Muted" is written ----
const hold = html.slice(html.indexOf('    var MUTE_HOLD_KEY ='), html.indexOf('    var inputRate = 16000, outputRate'));
const statusFn = html.slice(html.indexOf('    function status(text) {'), html.indexOf('    // OFFLINE_FALLBACK (26 Oct): a call'));
assert(hold.includes('function adoptHold(state)') && statusFn.includes('storyStatus(text)'), 'source slices moved');

function engine(stored, opts) {
  opts = opts || {};
  const log = { rest: 0, events: [], labels: [], sent: [],
                store: opts.store || (stored ? { 'emma-mute-hold': '1' } : {}) };
  const storage = {
    getItem: (k) => (k in log.store ? log.store[k] : null),
    setItem: (k, v) => { log.store[k] = v; },
    removeItem: (k) => { delete log.store[k]; },
  };
  const doc = { dispatchEvent: (e) => log.events.push(e.detail) };
  class CE { constructor(name, opts) { this.detail = opts.detail; } }
  const body = `
    var inCall = false, muted = false, playing = false, lastStatusKey = '';
    var statusNode = { textContent: '' }, appStatus = {}, appStatusZh = {};
    function storyStatus(t) { return t; }
    function offlineMark(t) { return t; }
    function chromeText(t) { return t; }
    function uiText(en) { return en; }
    function setMicLabel(t) { log.labels.push(t); }
    function restScreen() { log.rest++; status(muteHold ? 'Muted' : 'Tap the mic to talk'); }
    var appShell = true, ws = { readyState: 1, send: (t) => log.sent.push(JSON.parse(t)) }, WebSocket = { OPEN: 1 };
    var ceremonyApp = ${!!opts.ceremony};
    ${hold}
    ${statusFn}
    return { adoptHold, setMuteHold, status, micForDial, greetingHeard, opensMic: () => greetingOpensMic,
             get: () => ({ muted, muteHold, holdSince, text: statusNode.textContent }),
             call: (c, m) => { inCall = c; muted = m; } };`;
  const api = new Function('sessionStorage', 'document', 'CustomEvent', 'log', body)(storage, doc, CE, log);
  return { api, log };
}

// 18:05 in the room the robot stands in, whatever the clock of the machine running this says
const SINCE = Math.floor(Date.UTC(2026, 10, 4, 11, 5) / 1000);

{ // A fresh WebView (app restart, reboot): sessionStorage empty, the server remembers.
  const { api, log } = engine(false);
  assert.strictEqual(api.get().muteHold, false);
  api.adoptHold({ on: true, since: SINCE, until: SINCE + 3600 });
  assert.strictEqual(api.get().muteHold, true, 'the server\'s hold is the page\'s hold');
  assert.strictEqual(log.rest, 1, 'the screen goes to its rest state');
  assert.match(api.get().text, /^Muted since 18:05/, 'and says since when');
  assert.strictEqual(log.store['emma-mute-hold'], '1');
  assert.deepStrictEqual(log.events, [], 'adopting is never written back as a press');
  api.adoptHold({ on: true, since: SINCE, until: SINCE + 3600 });
  assert.strictEqual(log.rest, 1, 'the same word again is not a new rest screen');
}

{ // The morning: the server says it ended.
  const { api, log } = engine(true);
  assert.strictEqual(api.get().muteHold, true);
  api.adoptHold({ on: false });
  assert.strictEqual(api.get().muteHold, false);
  assert.strictEqual(log.rest, 1);
  assert.strictEqual(api.get().text, 'Tap the mic to talk');
  assert.ok(!('emma-mute-hold' in log.store));
  assert.deepStrictEqual(log.events, []);
}

{ // Inside a camera's call the mic is open on purpose (owner, 14 Oct): no change.
  const { api, log } = engine(true);
  api.call(true, false);
  api.adoptHold({ on: true, since: SINCE, until: SINCE + 3600 });
  assert.strictEqual(api.get().muted, false);
  assert.strictEqual(log.rest, 0, 'a call is not torn down to a rest screen');
}

{ // A muted call when the morning comes: listening again, and the screen says so.
  const { api, log } = engine(true);
  api.call(true, true);
  api.adoptHold({ on: false });
  assert.strictEqual(api.get().muted, false);
  assert.deepStrictEqual(log.labels, ['Listening']);
}

{ // No server keeping it (no bell, MIC_HOLD_PERSIST off): exactly the old words.
  const { api, log } = engine(false);
  api.setMuteHold(true);
  assert.deepStrictEqual(log.events, [true], 'a tap is the event the bell reads as a press');
  api.status('Muted');
  assert.strictEqual(api.get().text, 'Muted');
  assert.strictEqual(api.get().holdSince, 0);
}

{ // Kept by the server, then pressed here: since now.
  const { api } = engine(false);
  api.adoptHold({ on: false });
  api.setMuteHold(true);
  api.status('Muted');
  assert.match(api.get().text, /^Muted since \d\d:\d\d/);
  api.setMuteHold(false);
  assert.strictEqual(api.get().holdSince, 0);
}

{ // The camera's bell under a hold: the pre-ring comes for any close face, a greeting may never follow.
  const { api, log } = engine(true);
  api.micForDial();
  assert.strictEqual(api.get().muted, true, 'dialled, and the mic stays shut');
  assert.strictEqual(api.opensMic(), true);
  api.greetingHeard();                     // her first sound
  assert.strictEqual(api.get().muted, false, "14 Oct's rule: that call's mic is for the person greeted");
  assert.strictEqual(api.get().muteHold, true, 'the hold is still there for when it ends');
  assert.deepStrictEqual(log.labels, ['Listening']);
  assert.strictEqual(log.sent[0].why, 'hold_greeting', 'and the turn log says why it opened');
}

{ // No hold: as ever, the mic is open from the dial.
  const { api } = engine(false);
  api.micForDial();
  assert.strictEqual(api.get().muted, false);
  assert.strictEqual(api.opensMic(), false);
}

{ // The morning ends a hold while that call waits for its greeting: listening, and no greeting owed.
  const { api } = engine(true);
  api.micForDial();
  api.call(true, true);
  api.adoptHold({ on: false });
  assert.strictEqual(api.get().muted, false);
  assert.strictEqual(api.opensMic(), false);
}

{ // The Emma Ceremony app (robot, 23 Nov: a 19:55 tap held the mic to 06:00, the ceremony app opened with
  // no call, and the tap that brought it up at 22:25 lifted the hold the Event face came back to). The
  // server's hold is not this app's: never adopted, and a '1' an older page adopted under the shared key
  // is not a tap here. It dials with the mic open (in ceremony mode the server keeps it deaf itself).
  const { api, log } = engine(true, { ceremony: true });
  assert.strictEqual(api.get().muteHold, false, "an adopted '1' under the shared key is not this app's tap");
  api.adoptHold({ on: true, since: SINCE, until: SINCE + 3600 });
  assert.strictEqual(api.get().muteHold, false, "the server's hold is not adopted");
  assert.strictEqual(log.rest, 0, 'no rest screen for a hold that is not its own');
  assert.deepStrictEqual(log.events, [], 'and nothing written back');
  assert.deepStrictEqual(log.store, { 'emma-mute-hold': '1' }, 'neither key touched');
  api.micForDial();
  assert.strictEqual(api.get().muted, false, 'its call comes up with the mic open');
  assert.strictEqual(api.opensMic(), false, 'no greeting owed');
  // ...and a person's tap in this app still holds this app's line, under its own key
  api.setMuteHold(true);
  assert.deepStrictEqual(log.events, [true], 'a tap is still a press for the bell (the server keeps it apart)');
  assert.strictEqual(log.store['emma-ceremony-mute-hold'], '1');
  api.micForDial();
  assert.strictEqual(api.get().muted, true, 'its own tap holds its own line');
  api.adoptHold({ on: false });
  assert.strictEqual(api.get().muteHold, true, "the server's morning is not its word either: a tap ends it");
}

{ // ...its own tap survives the app's reload on resume, like any page's.
  const { api } = engine(false, { ceremony: true, store: { 'emma-ceremony-mute-hold': '1' } });
  assert.strictEqual(api.get().muteHold, true);
}

{ // The Event app's page (not the ceremony's): under the same stored hold, exactly as before.
  const { api, log } = engine(false, { ceremony: false });
  api.adoptHold({ on: true, since: SINCE, until: SINCE + 3600 });
  assert.strictEqual(api.get().muteHold, true);
  api.micForDial();
  assert.strictEqual(api.get().muted, true, 'a staff press dials it muted, as on 10 Nov');
  assert.strictEqual(log.store['emma-mute-hold'], '1');
}

// ---- the mic button, from the page's own source ----
const lf = html.replace(/\r\n/g, '\n');
const tapAt = lf.indexOf('    if (micBtn) micBtn.onclick = function () {');
const tapSrc = lf.slice(tapAt, lf.indexOf('    };\n', tapAt) + 7);
const muteAt = lf.indexOf('    function tellServerMuted() {');
const muteSrc = lf.slice(muteAt, lf.indexOf('    var appMicLabels', muteAt));
const upAt = lf.indexOf('    function micIsUp() {');
const upSrc = lf.slice(upAt, lf.indexOf('\n    }\n', upAt) + 7);
assert(tapSrc.includes('toggleMute()') && muteSrc.includes('function toggleMute()') && upSrc.includes('tellServerMuted'), 'button slices moved');

function button(start) {
  const log = { sent: [], labels: [], status: [], holds: [], dials: 0 };
  const body = `
    var inCall = ${!!start.inCall}, micStream = ${start.mic ? '{}' : 'null'}, arrayMicSilent = ${!!start.array};
    var dialledByBell = ${!!start.bell}, muteHold = ${!!start.hold}, muted = ${!!start.muted}, playing = false;
    var greetingOpensMic = ${!!start.waiting};
    var ws = { readyState: ${start.closedSocket ? 0 : 1}, send: (t) => log.sent.push(JSON.parse(t)) };
    var WebSocket = { OPEN: 1 };
    var micBtn = { setAttribute: () => {} };
    function setMuteHold(on) { muteHold = !!on; log.holds.push(!!on); }
    function startCall() { log.dials++; }
    function setMicLabel(t) { log.labels.push(t); }
    function show() {}
    function status(t) { log.status.push(t); }
    ${tapSrc}
    ${muteSrc}
    ${upSrc}
    return { tap: () => micBtn.onclick(), up: micIsUp, get: () => ({ muted, muteHold }) };`;
  return { api: new Function('log', body)(log), log };
}

{ // A call somebody tapped for, still connecting: a double tap does not mute it.
  const { api, log } = button({ inCall: true });
  api.tap();
  assert.strictEqual(api.get().muted, false);
  assert.deepStrictEqual(log.holds, []);
  api.up();
  assert.deepStrictEqual(log.labels, ['Listening']);
}

{ // A call a bell dialled, still connecting: the tap can only mean "no". It used to be dropped.
  const { api, log } = button({ inCall: true, bell: true, closedSocket: true });
  api.tap();
  assert.strictEqual(api.get().muted, true, 'it holds');
  assert.deepStrictEqual(log.holds, [true]);
  assert.deepStrictEqual(log.sent, [], 'no socket to say it on yet');
  api.up();
  assert.strictEqual(log.labels[log.labels.length - 1], 'Muted', 'the mic came up, and it says Muted, not Listening');
  assert.strictEqual(log.status[log.status.length - 1], 'Muted');
}

{ // ...and the server hears the press when the mic is up (no follow-up into that silence).
  const { api, log } = button({ inCall: true, bell: true });
  api.tap();
  api.up();
  const taps = log.sent.filter((m) => m.why === 'mute_tap');
  assert.ok(taps.length >= 1 && taps.every((m) => m.muted === true), 'the server is told');
}

{ // A camera's call waiting for its greeting: it says Muted, does not tell the server somebody pressed,
  // and says it is waiting muted (review 6 Nov: the server held that line open for ever as a standing line).
  const { api, log } = button({ inCall: true, muted: true, waiting: true, hold: true });
  api.up();
  assert.strictEqual(log.labels[log.labels.length - 1], 'Muted');
  assert.strictEqual(log.status[log.status.length - 1], 'Muted');
  assert.deepStrictEqual(log.sent.map((m) => [m.why, m.muted]), [['hold_waiting', true]]);
}

{ // ...and a tap on it, which the screen asks for, is not dropped while the camera's dial connects.
  const { api, log } = button({ inCall: true, muted: true, waiting: true, hold: true, closedSocket: true });
  api.tap();
  assert.strictEqual(api.get().muted, false, 'under a hold this tap can only mean listen');
  assert.deepStrictEqual(log.holds, [false]);
}

{ // ...and a person there can still tap their way in: that lifts the hold, and no greeting is owed.
  const { api, log } = button({ inCall: true, muted: true, waiting: true, hold: true, mic: true });
  api.tap();
  assert.strictEqual(api.get().muted, false);
  assert.deepStrictEqual(log.holds, [false]);
}

{ // The array's silent clock: a mic like any other. It used to have no mute at all.
  const { api, log } = button({ inCall: true, array: true });
  api.tap();
  assert.strictEqual(api.get().muted, true);
  assert.deepStrictEqual(log.holds, [true]);
}

{ // A tap with no call open is still the way out, and still dials.
  const { api, log } = button({ hold: true });
  api.tap();
  assert.deepStrictEqual(log.holds, [false]);
  assert.strictEqual(log.dials, 1);
}

// ---- the bell ----
const bellAt = html.indexOf('    var RETRY_MIN_MS = 2000, RETRY_MAX_MS = 30000;');
const bell = html.slice(bellAt, html.indexOf('    function later()', bellAt));
assert(bell.includes('function sendHold(evt)'), 'bell slice moved');

function bellRun(initial, ceremony) {
  const socks = [];
  const state = { held: initial, adopted: [], answered: [] };
  class WS {
    constructor() { this.readyState = 1; this.sent = []; socks.push(this); }
    send(text) { this.sent.push(JSON.parse(text)); }
    close() {}
  }
  const win = { EmmaCall: {
    held: () => state.held,
    busy: () => false,
    answerSummons: (reason) => state.answered.push(reason),
    // the real adoptHold: the ceremony app's page never takes the server's hold as its own
    adoptHold: (s) => { state.adopted.push(s); if (!ceremony) state.held = !!s.on; },
    ceremony: ceremony === undefined ? undefined : !!ceremony,
  } };
  let tap = null;
  const doc = { hidden: false, addEventListener: (name, fn) => { if (name === 'emma-mute-hold') tap = fn; } };
  const api = new Function('WebSocket', 'window', 'document', 'location', 'setTimeout',
    "var proto = 'wss', token = 't';\n" + bell + '\nreturn { open: open };')(WS, win, doc, { host: 'robot' }, () => 0);
  api.open();
  const say = (sock, frame) => sock.onmessage({ data: JSON.stringify(frame) });
  return { socks, state, say, tap: () => tap({ type: 'emma-mute-hold' }), open: api.open };
}

{ // A fresh page: adopts the server's word first, then syncs it as NOT a press.
  const b = bellRun(false);
  b.say(b.socks[0], { type: 'standby_only', hold: { on: true, since: SINCE, until: SINCE + 9 } });
  assert.strictEqual(b.state.adopted.length, 1);
  assert.deepStrictEqual(b.socks[0].sent, [{ type: 'hold', on: true, press: false }]);
  b.say(b.socks[0], { type: 'wake', reason: 'standing_line' });
  assert.deepStrictEqual(b.state.answered, [], 'the standing line stays refused');
  b.say(b.socks[0], { type: 'wake', reason: 'face_stranger' });
  assert.deepStrictEqual(b.state.answered, ['face_stranger'], 'a bell the server sent is answered, as on 14 Oct');
  // The event staff page's press (owner 10 Nov, "เปิดสายแต่ mute ไมค์ได้ไหม"): answered under the
  // hold -- the line it opens is muted (micForDial) -- and the standing line is still not.
  b.say(b.socks[0], { type: 'wake', reason: 'event_walk' });
  b.say(b.socks[0], { type: 'wake', reason: 'standing_line' });
  assert.deepStrictEqual(b.state.answered, ['face_stranger', 'event_walk'], 'a staff press is answered muted');
  // The morning, pushed by the server, on a bell already open.
  b.say(b.socks[0], { type: 'hold_state', on: false, why: 'expired' });
  assert.strictEqual(b.state.held, false);
}

{ // A tap is a press.
  const b = bellRun(false);
  b.say(b.socks[0], { type: 'standby_only', hold: { on: false } });
  b.state.held = true;
  b.tap();
  assert.deepStrictEqual(b.socks[0].sent[1], { type: 'hold', on: true, press: true });
}

{ // A tap while the server restarts is not lost, and the server's stale word
  // does not erase it.
  const b = bellRun(false);
  b.say(b.socks[0], { type: 'standby_only', hold: { on: false } });
  b.socks[0].readyState = 3;
  b.socks[0].onclose();
  b.state.held = true;
  b.tap();
  assert.strictEqual(b.socks[0].sent.length, 1, 'nothing went down the closed socket');
  b.open();
  b.say(b.socks[1], { type: 'standby_only', hold: { on: false } });
  assert.strictEqual(b.state.adopted.length, 1, 'only the first bell\'s word was adopted');
  assert.strictEqual(b.state.held, true);
  assert.deepStrictEqual(b.socks[1].sent, [{ type: 'hold', on: true, press: true }]);
}

{ // A server with the switch off (or an old one): no word, the page's own state stands.
  const b = bellRun(true);
  b.say(b.socks[0], { type: 'standby_only' });
  assert.deepStrictEqual(b.state.adopted, []);
  assert.deepStrictEqual(b.socks[0].sent, [{ type: 'hold', on: true, press: false }]);
}

{ // The Emma Ceremony app's bell (23 Nov): says whose it is on every hold message, and under the
  // stored hold the standing line's ring opens its call -- the ceremony's mode is that call.
  const b = bellRun(false, true);
  b.say(b.socks[0], { type: 'standby_only', hold: { on: true, since: SINCE, until: SINCE + 9 } });
  assert.deepStrictEqual(b.socks[0].sent, [{ type: 'hold', on: false, press: false, ceremony: true }]);
  b.say(b.socks[0], { type: 'wake', reason: 'standing_line' });
  assert.deepStrictEqual(b.state.answered, ['standing_line'], 'the ceremony call comes up under the hold');
  b.state.held = true;                     // a person's tap in the ceremony app
  b.tap();
  assert.deepStrictEqual(b.socks[0].sent[1], { type: 'hold', on: true, press: true, ceremony: true });
  b.say(b.socks[0], { type: 'wake', reason: 'standing_line' });
  assert.deepStrictEqual(b.state.answered, ['standing_line'], 'its own tap holds its own line');
}

{ // Any other page (EmmaCall.ceremony false): the wire is what it was.
  const b = bellRun(false, false);
  b.say(b.socks[0], { type: 'standby_only', hold: { on: true, since: SINCE, until: SINCE + 9 } });
  assert.deepStrictEqual(b.socks[0].sent, [{ type: 'hold', on: true, press: false }]);
  b.say(b.socks[0], { type: 'wake', reason: 'standing_line' });
  assert.deepStrictEqual(b.state.answered, [], 'the Event page under the hold: refused, as before');
}

console.log('ok');
