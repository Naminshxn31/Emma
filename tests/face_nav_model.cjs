'use strict';
// Runs the face page's REAL robot-bridge + navigation script, extracted
// verbatim from client/voice-preview.html, against a fake DOM, a fake app
// bridge (window.prompt), a fake /robot-nav server and a virtual clock.
// Two modes (tests/test_face_body_nav_page.py drives both):
//   node face_nav_model.cjs <page> model            stdin: [{fn, args}]  -> [result]
//   node face_nav_model.cjs <page> scenario <name>  -> one JSON object: what the page did
// The pure part (navModel, relayCommand, the message builders, the hold) is
// the block between `// <nav-model>` and `// </nav-model>`; the scenarios run
// the whole script, so a guard reverted anywhere in it shows up here.
const fs = require('fs');
const vm = require('vm');
const [, , PAGE, MODE, SCENARIO] = process.argv;
const html = fs.readFileSync(PAGE, 'utf8');

function between(startMarker, endMarker) {
  const a = html.indexOf(startMarker);
  if (a < 0) throw new Error('marker not found: ' + startMarker);
  const b = html.indexOf(endMarker, a);
  if (b < 0) throw new Error('marker not found: ' + endMarker);
  return html.slice(a, b);
}

// ---- virtual clock ----
let now = 1790000000000, nextId = 1;
const timers = new Map();
const clock = {
  setTimeout(fn, ms, ...a) { const id = nextId++; timers.set(id, { at: now + Math.max(0, Number(ms) || 0), fn, a, every: 0 }); return id; },
  setInterval(fn, ms, ...a) { const id = nextId++; const every = Math.max(1, Number(ms) || 0); timers.set(id, { at: now + every, fn, a, every }); return id; },
  clearTimeout(id) { timers.delete(id); },
  clearInterval(id) { timers.delete(id); },
};
async function flush() { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)); }
async function advance(ms) {
  const end = now + ms;
  await flush();
  for (;;) {
    let bestId = 0, best = null;
    for (const [id, t] of timers) {
      if (t.at <= end && (!best || t.at < best.at || (t.at === best.at && id < bestId))) { best = t; bestId = id; }
    }
    if (!best) break;
    now = best.at;
    if (best.every) best.at += best.every; else timers.delete(bestId);
    best.fn(...best.a);
    await flush();
  }
  now = end;
  await flush();
}
const FakeDate = { now: () => now };

// ---- model mode ----
function loadModel() {
  const src = between('// <nav-model>', '// </nav-model>');
  const ctx = vm.createContext({ TextEncoder, unescape, encodeURIComponent });
  vm.runInContext(src + `
    this.__api = { navModel, relayCommand, bodyMessage, speechMessage, navGoMessage, allowedMessage, bodyCapsFrame,
      bodyStatusFrame, navStatusFrame, navCapsFrame, frameFits, holdController, joinDoors,
      NAV_REASONS_TH, BODY_IDS, SPEECH_BODY_IDS, START_HOLD_MS, MAX_INBOUND_BYTES, ID_RE: ID_RE.source,
      COMMAND_ID_RE: COMMAND_ID_RE.source, BODY_STATUSES, NAV_STATES, BODY_STATUS_STATES,
      autoState, autoBanner, activityMessage, statusKey, NAV_ORIGINS, AUTO_PHASES, ACTIVITY_KINDS };`, ctx);
  return ctx.__api;
}

function runModel(cases) {
  const api = loadModel();
  return cases.map((c) => {
    if (c.fn === 'constants') {
      return { NAV_REASONS_TH: api.NAV_REASONS_TH, BODY_IDS: api.BODY_IDS, SPEECH_BODY_IDS: api.SPEECH_BODY_IDS,
        START_HOLD_MS: api.START_HOLD_MS,
        MAX_INBOUND_BYTES: api.MAX_INBOUND_BYTES, ID_RE: api.ID_RE, COMMAND_ID_RE: api.COMMAND_ID_RE,
        BODY_STATUSES: api.BODY_STATUSES, NAV_STATES: api.NAV_STATES, BODY_STATUS_STATES: api.BODY_STATUS_STATES,
        NAV_ORIGINS: api.NAV_ORIGINS, AUTO_PHASES: api.AUTO_PHASES, ACTIVITY_KINDS: api.ACTIVITY_KINDS };
    }
    if (c.fn === 'relayCommand') {
      // args: [evt, answers {message: reply object}, onRobot]
      const asked = [];
      const ack = api.relayCommand(c.args[0], (m) => { asked.push(m); return (c.args[1] || {})[m] || null; }, c.args[2]);
      return { ack, asked };
    }
    if (c.fn === 'hold') {
      // args: [[op, ms]...] where op is start|cancel|wait
      let t = 0, fired = 0, seq = 1;
      const pending = new Map();
      const h = api.holdController(api.START_HOLD_MS, () => { fired += 1; },
        (fn, ms) => { const id = seq++; pending.set(id, { at: t + ms, fn }); return id; },
        (id) => { pending.delete(id); });
      for (const [op, ms] of c.args) {
        if (op === 'start') h.start();
        else if (op === 'cancel') h.cancel();
        else if (op === 'wait') {
          t += ms;
          for (const [id, p] of [...pending]) if (p.at <= t) { pending.delete(id); p.fn(); }
        }
      }
      return { fired };
    }
    return api[c.fn].apply(null, c.args || []);
  });
}

// ---- scenario mode: a DOM just big enough ----
function makeEl(tag, props) {
  const handlers = {};
  const subs = {};
  const el = {
    tagName: String(tag || 'div').toUpperCase(), hidden: false, value: '', disabled: false, inert: false,
    scrollTop: 0, attrs: {}, children: [], dataset: {}, innerHTML: '', offsetWidth: 10, _text: '',
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); },
      contains(c) { return this.set.has(c); },
      toggle(c, on) { if (on === undefined ? !this.set.has(c) : on) this.set.add(c); else this.set.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    addEventListener(t, fn) { (handlers[t] = handlers[t] || []).push(fn); },
    removeEventListener(t, fn) { handlers[t] = (handlers[t] || []).filter((f) => f !== fn); },
    fire(t, e) { (handlers[t] || []).slice().forEach((fn) => fn(Object.assign({ preventDefault() {}, stopImmediatePropagation() {} }, e || {}))); },
    focus() { doc.activeElement = el; },
    appendChild(c) { this.children.push(c); return c; },
    contains(x) { return x === el; },
    getClientRects() { return [1]; },
    querySelector(sel) { return (subs[sel] = subs[sel] || makeEl('span')); },
    querySelectorAll() { return []; },
  };
  Object.defineProperty(el, 'textContent', {
    get() { return this._text; }, set(v) { this._text = String(v); }, configurable: true,
  });
  return Object.defineProperties(el, Object.getOwnPropertyDescriptors(props || {}));
}

const VIEW_NAMES = ['pin', 'message', 'places', 'progress', 'result'];
const views = {};
const closeButtons = [];
for (const n of VIEW_NAMES) {
  views[n] = makeEl(n === 'pin' ? 'form' : 'section');
  views[n].attrs['data-nav-view'] = n;
  views[n].hidden = true;
  const c = makeEl('button');
  closeButtons.push(c);
  views[n].closeButton = c;
}
const xButton = makeEl('button');
closeButtons.push(xButton);
const root = makeEl('div', {
  querySelectorAll(sel) {
    if (sel === '[data-nav-view]') return VIEW_NAMES.map((n) => views[n]);
    if (sel === '[data-nav-close]') return closeButtons;
    return [];
  },
  querySelector(sel) {
    if (sel === '.emma-nav-x') return xButton;
    const m = /^\[data-nav-view="(\w+)"\]$/.exec(sel);
    return m ? views[m[1]] : null;
  },
});
root.hidden = true;
const ids = { emmaNav: root };
const byId = (id) => (ids[id] = ids[id] || makeEl(id === 'emmaNavPin' ? 'input' : 'div'));
byId('emmaNavSheet').hidden = true;
const created = [];
const docHandlers = {};
const doc = {
  hidden: false, readyState: 'complete', activeElement: null,
  getElementById: byId,
  createElement(t) { const e = makeEl(t); created.push(e); return e; },
  body: { children: [root, makeEl('main')], classList: { set: new Set(), add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); } } },
  documentElement: makeEl('html'),
  querySelector() { return null; },
  addEventListener(t, fn) { (docHandlers[t] = docHandlers[t] || []).push(fn); },
  dispatch(t, e) { (docHandlers[t] || []).slice().forEach((fn) => fn(Object.assign({ preventDefault() {}, stopImmediatePropagation() {} }, e || {}))); },
};

function harness(opts) {
  const winHandlers = {};
  const prompts = [];
  const fetches = [];
  const answers = opts.answers || {};
  // The status link's socket (/ws/robot-face). Only with `opts.ws`: the older
  // scenarios run with no WebSocket at all, so they keep checking the call
  // path alone. open()/message()/drop() are the test's hands on the server end.
  const sockets = [];
  class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.at = now; sockets.push(this); }
    send(text) { if (this.readyState !== 1) throw new Error('not open'); this.sent.push(JSON.parse(text)); }
    close() {
      if (this.readyState === 3) return;
      this.readyState = 3;
      clock.setTimeout(() => { if (this.onclose) this.onclose({}); }, 0);
    }
    open() { this.readyState = 1; if (this.onopen) this.onopen({}); }
    message(obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); }
    drop() { if (this.readyState === 3) return; this.readyState = 3; if (this.onclose) this.onclose({}); }
  }
  const warnings = [];
  const ctx = {
    console: { log() {}, error() {}, warn(m) { warnings.push(String(m)); } },
    JSON, Math, Object, Array, Promise, TextEncoder, unescape, encodeURIComponent,
    URLSearchParams, Date: FakeDate,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    setInterval: clock.setInterval, clearInterval: clock.clearInterval,
    location: { search: opts.search || '', protocol: opts.protocol || 'https:', host: opts.host || 'robot.test:8001' },
    navigator: { userAgent: opts.ua || 'Mozilla/5.0' },
    document: doc,
    addEventListener(t, fn) { (winHandlers[t] = winHandlers[t] || []).push(fn); },
    prompt(message, def) {
      prompts.push(message);
      const a = typeof answers === 'function' ? answers(message) : answers[message];
      return a === undefined ? null : (typeof a === 'string' ? a : JSON.stringify(a));
    },
    fetch(url, init) {
      const body = JSON.parse(init.body);
      fetches.push({ url, body, method: init.method });
      const route = (opts.routes || {})[url.replace('/robot-nav/', '')];
      const [status, reply] = typeof route === 'function' ? route(body) : (route || [404, null]);
      return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(reply) });
    },
  };
  ctx.window = ctx;
  if (opts.ws) ctx.WebSocket = FakeWS;
  vm.createContext(ctx);
  vm.runInContext(between('// ---- robot bridge + navigation:', '</script>'), ctx);
  ctx.fireWin = (t) => (winHandlers[t] || []).slice().forEach((fn) => fn({}));
  return { ctx, prompts, fetches, sockets, warnings };
}

const ROBOT = { search: '?app=1&token=tok123', ua: 'Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1 EmmaBridge/1' };
const RUN = 'ab'.repeat(16);
const CMD = 'cd'.repeat(16);
const PLACES = { enabled: true, installed: true, places_sha256: 'e'.repeat(64), here: 'dock', battery: 82,
  places: [
    { id: 'dock', name_th: 'แท่นชาร์จ', available: false, reason: 'already_there', verified: false },
    { id: 'big_room', name_th: 'ห้องประชุมใหญ่', name_en: 'Big meeting room', available: true, reason: null,
      verified: false, doors: ['D8', 'D11'] },
  ], run: null };
const IDLE = { format: 1, state: 'idle', run_id: null };

function visibleView() { return VIEW_NAMES.filter((n) => !views[n].hidden); }
function card(id) { return created.find((e) => e.dataset && e.dataset.place === id); }

const scenarios = {
  // A desk page (no ?app=1) and an app without the bridge both answer the
  // server's command with not_robot_face and never touch window.prompt.
  async not_robot() {
    const out = {};
    for (const [name, opts] of [['desk', { search: '?token=t', ua: 'Mozilla/5.0' }],
      ['old_apk', { search: '?app=1', ua: 'Mozilla/5.0 wv EmmaRobot/0.1' }],
      ['ua_only', { search: '', ua: ROBOT.ua }]]) {
      const h = harness(Object.assign({ answers: () => ({ accepted: true }) }, opts));
      const sent = [];
      h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
      h.ctx.EmmaRobotBridge.command({ type: 'body_command', command_id: CMD, action: 'nod' }, (f) => sent.push(f));
      h.ctx.fireWin('emma-body-changed');
      h.ctx.fireWin('emma-nav-changed');
      out[name] = { prompts: h.prompts, sent, offered: h.ctx.EmmaNav.offered() };
    }
    return out;
  },

  // The relay on the robot: exact prompt strings, ids only after ID_RE.
  async relay() {
    const answers = {
      'emma-body:nod': { accepted: true, action: 'nod', reason: null, status: 'direction_known', est_ms: 4000 },
      'emma-body:stop': { accepted: true },
      'emma-nav:go:big_room:voice': { accepted: true, reason: null, run_id: RUN },
      'emma-nav:stop': { accepted: true },
      'emma-nav:status': IDLE,
    };
    const h = harness(Object.assign({ answers }, ROBOT));
    const acks = [];
    const send = (f) => { acks.push(f); };
    const B = h.ctx.EmmaRobotBridge;
    B.command({ type: 'body_command', command_id: CMD, action: 'nod' }, send);
    B.command({ type: 'body_command', command_id: CMD, action: 'stop' }, send);
    B.command({ type: 'body_command', command_id: CMD, action: 'nod\nemma-nav:go:x:tap' }, send);
    B.command({ type: 'body_command', command_id: CMD, action: 'list' }, send);
    B.command({ type: 'body_command', command_id: 'XYZ', action: 'nod' }, send);
    B.command({ type: 'nav_command', command_id: CMD, action: 'go', place: 'big_room', origin: 'tap' }, send);
    B.command({ type: 'nav_command', command_id: CMD, action: 'go', place: '../etc', origin: 'voice' }, send);
    B.command({ type: 'nav_command', command_id: CMD, action: 'go', place: 'ห้องประชุม', origin: 'voice' }, send);
    B.command({ type: 'nav_command', command_id: CMD, action: 'stop' }, send);
    await advance(10);
    return { prompts: h.prompts, acks };
  },

  // `ready`: face_hello first (when the app has a token), then the caps.
  async ready() {
    const out = {};
    for (const tok of ['s3cret', '']) {
      const h = harness(Object.assign({ answers: {
        'emma-face:token': { token: tok },
        'emma-body:list': { enabled: true, allow_direction_known: false, gestures: { nod: 'direction_known', bogus: 'verified', handshake: 'verified' } },
        'emma-nav:places': PLACES,
        'emma-nav:status': IDLE,
      } }, ROBOT));
      const sent = [];
      h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
      out[tok || 'none'] = { types: sent.map((f) => f.type), first: sent[0], body: sent.find((f) => f.type === 'body_caps'),
        prompts: h.prompts };
    }
    return out;
  },

  // body_status goes once per new seq.
  async body_status() {
    let seq = 4;
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-body:status') return { seq, state: 'idle', active: null, last: { action: 'nod', state: 'done', reason: null } };
      if (m === 'emma-body:list') return { enabled: true, allow_direction_known: true, gestures: { nod: 'direction_known' } };
      if (m === 'emma-face:token') return { token: '' };
      return null;
    } }, ROBOT));
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    sent.length = 0;
    h.ctx.fireWin('emma-body-changed');
    h.ctx.fireWin('emma-body-changed');
    seq = 5;
    h.ctx.fireWin('emma-body-changed');
    return { statuses: sent.filter((f) => f.type === 'body_status').map((f) => f.seq), caps: sent.filter((f) => f.type === 'body_caps').length };
  },

  // A walk sends nav_status on every change AND every 5 s even when nothing
  // changed -- also while the panel reads the status every second.
  async heartbeat() {
    let status = { format: 1, state: 'running', run_id: RUN, origin: 'tap', place: 'big_room', step: 4, step_from: 1,
      step_to: 10, do: 'door', door: 'D8', elapsed_s: 61, t: now / 1000 };
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-nav:status') return Object.assign({}, status, { t: now / 1000 });
      if (m === 'emma-nav:places') return PLACES;
      if (m === 'emma-face:token') return { token: '' };
      return null;
    }, routes: { config: [200, { mode: 'open' }] } }, ROBOT));
    await advance(1500);                       // the config arrives
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    h.ctx.EmmaNav.open();
    await advance(100);
    const opened = { view: visibleView(), phase: byId('emmaNavPhase').textContent, step: byId('emmaNavStep').textContent,
      going: byId('emmaNavGoing').textContent, x: !xButton.hidden };
    const before = sent.filter((f) => f.type === 'nav_status').length;
    await advance(16000);
    const beats = sent.filter((f) => f.type === 'nav_status').length - before;
    // A stray close during the walk does nothing.
    h.ctx.EmmaNav.close();
    xButton.fire('click');
    doc.dispatch('keydown', { key: 'Escape' });
    const stillOpen = h.ctx.EmmaNav.isOpen();
    status = Object.assign({}, status, { state: 'arrived', step: 10 });
    h.ctx.fireWin('emma-nav-changed');
    await advance(1100);
    return { opened, beats, stillOpen, after: { view: visibleView(), result: byId('emmaNavResult').textContent, x: !xButton.hidden } };
  },

  // nav_caps at most every 30 s while idle, even with the panel reading the
  // places every second and the battery changing on every read.
  async caps_rate() {
    let battery = 90;
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-nav:places') return Object.assign({}, PLACES, { battery: battery-- });
      if (m === 'emma-nav:status') return IDLE;
      if (m === 'emma-face:token') return { token: '' };
      return null;
    }, routes: { config: [200, { mode: 'open' }] } }, ROBOT));
    await advance(1500);
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    const atReady = sent.filter((f) => f.type === 'nav_caps').length;
    h.ctx.EmmaNav.open();
    await advance(20000);
    const after20 = sent.filter((f) => f.type === 'nav_caps').length - atReady;
    await advance(11000);
    const after31 = sent.filter((f) => f.type === 'nav_caps').length - atReady;
    return { atReady, after20, after31, reads: h.prompts.filter((m) => m === 'emma-nav:places').length };
  },

  // The slot and the PIN; the token always in the body, never in a URL.
  async pin() {
    let unlockReply = [403, { ok: false, reason: 'รหัสไม่ถูกต้อง' }];
    const h = harness(Object.assign({
      answers: { 'emma-nav:places': PLACES, 'emma-nav:status': IDLE },
      routes: { config: [200, { mode: 'pin' }], unlock: () => unlockReply },
    }, ROBOT));
    const offeredBefore = h.ctx.EmmaNav.offered();
    await advance(1500);
    const offeredAfter = h.ctx.EmmaNav.offered();
    h.ctx.EmmaNav.open();
    await advance(10);
    const first = visibleView();
    const pin = byId('emmaNavPin');
    pin.value = '0000';
    views.pin.fire('submit');
    await advance(10);
    const wrong = { error: byId('emmaNavPinError').textContent, view: visibleView(), pinLeft: pin.value };
    unlockReply = [200, { ok: true, ttl_s: 120 }];
    pin.value = '4321';
    views.pin.fire('submit');
    await advance(10);
    const right = { view: visibleView(), where: byId('emmaNavWhere').textContent };
    await advance(121000);
    const expired = visibleView();
    return { offeredBefore, offeredAfter, first, wrong, right, expired, fetches: h.fetches };
  },

  // ③ the 1 s hold: released early = nothing; held = exactly one tap go.
  async hold() {
    const h = harness(Object.assign({
      answers: { 'emma-nav:places': PLACES, 'emma-nav:status': IDLE,
        'emma-nav:go:big_room:tap': { accepted: true, reason: null, run_id: RUN } },
      routes: { config: [200, { mode: 'open' }] },
    }, ROBOT));
    await advance(1500);
    h.ctx.EmmaNav.open();
    await advance(10);
    const placesView = visibleView();
    const big = card('big_room'), dock = card('dock');
    dock.fire('click');
    const dockSheet = !byId('emmaNavSheet').hidden;
    big.fire('click');
    const sheet = { open: !byId('emmaNavSheet').hidden, title: byId('emmaNavSheetTitle').textContent,
      items: byId('emmaNavSheetList').children.map((li) => li.textContent), badge: byId('emmaNavSheetBadge').textContent };
    const hold = byId('emmaNavHold');
    hold.fire('pointerdown', { button: 0 });
    await advance(999);
    hold.fire('pointerup');
    await advance(2000);
    const early = h.prompts.filter((m) => m.startsWith('emma-nav:go')).length;
    hold.fire('pointerdown', { button: 0 });
    await advance(400);
    hold.fire('pointerleave');
    await advance(2000);
    const slid = h.prompts.filter((m) => m.startsWith('emma-nav:go')).length;
    hold.fire('pointerdown', { button: 0 });
    await advance(1000);
    const goes = h.prompts.filter((m) => m.startsWith('emma-nav:go'));
    return { placesView, dockSheet, sheet, early, slid, goes, after: visibleView(),
      kicker: byId('emmaNavKicker').textContent, phase: byId('emmaNavPhase').textContent };
  },

  // Finding 15: a finger that slides off the hold button onto ยกเลิก (no
  // pointerup -- touch capture keeps it on the button) must not start the
  // walk. Also: the pointerdown releases the implicit touch capture.
  async hold_slide_off() {
    const h = harness(Object.assign({
      answers: { 'emma-nav:places': PLACES, 'emma-nav:status': IDLE,
        'emma-nav:go:big_room:tap': { accepted: true, reason: null, run_id: RUN } },
      routes: { config: [200, { mode: 'open' }] },
    }, ROBOT));
    const hold = byId('emmaNavHold');
    const released = [];
    hold.getBoundingClientRect = () => ({ left: 100, right: 500, top: 1500, bottom: 1600 });
    hold.hasPointerCapture = (id) => id === 7;
    hold.releasePointerCapture = (id) => { released.push(id); };
    await advance(1500);
    h.ctx.EmmaNav.open();
    await advance(10);
    card('big_room').fire('click');
    const go = () => h.prompts.filter((m) => m.startsWith('emma-nav:go')).length;
    hold.fire('pointerdown', { button: 0, pointerId: 7, clientX: 300, clientY: 1550 });
    hold.fire('pointermove', { pointerId: 7, clientX: 320, clientY: 1560 });   // still on the button
    await advance(400);
    const stillHolding = hold.classList.contains('is-holding');
    hold.fire('pointermove', { pointerId: 7, clientX: 300, clientY: 1700 });   // onto ยกเลิก
    await advance(2000);
    const slidOff = go();
    // A press that stays on the button still commits after 1 s.
    hold.fire('pointerdown', { button: 0, pointerId: 7, clientX: 300, clientY: 1550 });
    hold.fire('pointermove', { pointerId: 7, clientX: 310, clientY: 1555 });
    await advance(1000);
    return { stillHolding, slidOff, released, stayed: go() };
  },

  // Finding 34: the page knows when the native SafetyBar is up (a walk is
  // active -- walks only since 10 Oct) and lifts its own controls above it.
  async safety_bar() {
    let nav = IDLE, body = { seq: 1, state: 'idle', active: null, last: null };
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-nav:status') return nav;
      if (m === 'emma-nav:places') return PLACES;
      if (m === 'emma-body:status') return body;
      if (m === 'emma-face:token') return { token: '' };
      return null;
    } }, ROBOT));
    const on = () => doc.documentElement.classList.contains('safety-bar-on');
    const out = {};
    h.ctx.fireWin('emma-nav-changed');
    out.idle = on();
    nav = { format: 1, state: 'running', run_id: RUN, origin: 'pc', place: 'big_room' };
    h.ctx.fireWin('emma-nav-changed');
    out.walking = on();
    nav = { format: 1, state: 'arrived', run_id: RUN, place: 'big_room' };
    h.ctx.fireWin('emma-nav-changed');
    out.arrived = on();
    body = { seq: 2, state: 'running', active: 'nod', last: null };
    h.ctx.fireWin('emma-body-changed');
    out.gesture = on();
    body = { seq: 2, state: 'rest_return', active: null, last: null };
    h.ctx.fireWin('emma-body-changed');
    out.rest_return = on();
    body = { seq: 3, state: 'cooldown', active: null, last: null };
    h.ctx.fireWin('emma-body-changed');
    out.cooldown = on();
    return out;
  },

  // Review2 #5 (page half): when a walk ends the app drops its places cache
  // and answers {loading:true} until a refresh made after the end lands. The
  // forced nav_caps is OWED until one goes out (a plain read meanwhile does
  // not cancel it), and for 30 s after the end a changed caps text goes at
  // once on the app's "refreshed" event -- it used to wait for the 30 s tick
  // with the server holding the pre-walk `here`.
  async caps_after_run() {
    let status = { format: 1, state: 'running', run_id: RUN, origin: 'tap', place: 'big_room', step: 4,
      step_from: 1, step_to: 10 };
    let places = PLACES, loading = 0;
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-nav:status') return Object.assign({}, status, { t: now / 1000 });
      if (m === 'emma-nav:places') {
        if (loading > 0) { loading -= 1; return { loading: true }; }
        return places;
      }
      if (m === 'emma-face:token') return { token: '' };
      return null;
    } }, ROBOT));
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    await advance(100);
    const caps = () => sent.filter((f) => f.type === 'nav_caps');
    const atReady = caps().length;
    // the walk ends; the app answers loading for its first reads after it
    status = { format: 1, state: 'arrived', run_id: RUN, origin: 'tap', place: 'big_room', step: 10, here: 'big_room' };
    loading = 2;
    h.ctx.fireWin('emma-nav-changed');        // terminal: forced read -> loading
    h.ctx.fireWin('emma-nav-changed');        // a plain read meanwhile -> loading again
    await advance(1500);                      // the retry reads the (unchanged) caps: still owed, so sent
    const owed = caps().length - atReady;
    // the app's refresh lands: here moved, the dock card is available now
    places = Object.assign({}, PLACES, { here: 'big_room', places: [
      { id: 'dock', name_th: 'แท่นชาร์จ', available: true, reason: null, verified: false },
      { id: 'big_room', name_th: 'ห้องประชุมใหญ่', available: false, reason: 'already_there', verified: false }] });
    await advance(5000);
    h.ctx.fireWin('emma-nav-changed');        // the app's "places refreshed" event (no status change)
    await advance(10);
    const refreshed = caps().slice(atReady);
    // past the 30 s window a change waits for the tick again
    await advance(30000);
    const beforeLate = caps().length;
    places = Object.assign({}, places, { battery: 70 });
    h.ctx.fireWin('emma-nav-changed');
    await advance(10);
    const lateAtOnce = caps().length - beforeLate;
    return { atReady, owed, refreshed: refreshed.map((f) => f.here), lateAtOnce,
      placesReads: h.prompts.filter((m) => m === 'emma-nav:places').length };
  },

  // Review2 #26: opening นำทาง to watch a PC run or a walk started by voice
  // must not lock the face page (X gone, Escape dead, mic/End inert) for the
  // whole run. Only the panel's own tap walk holds it.
  async watch_walks() {
    let status = IDLE;
    const h = harness(Object.assign({ answers: (m) => {
      if (m === 'emma-nav:status') return Object.assign({}, status, { t: now / 1000 });
      if (m === 'emma-nav:places') return PLACES;
      if (m === 'emma-face:token') return { token: '' };
      return null;
    }, routes: { config: [200, { mode: 'open' }] } }, ROBOT));
    await advance(1500);
    const main = doc.body.children[1];
    const out = {};
    const walks = {
      pc: { format: 1, state: 'running', run_id: null, origin: 'pc', reason: 'pc_run', place: 'big_room' },
      voice: { format: 1, state: 'running', run_id: RUN, origin: 'voice', place: 'big_room', step: 2, step_from: 1, step_to: 10 },
      tap: { format: 1, state: 'running', run_id: RUN, origin: 'tap', place: 'big_room', step: 2, step_from: 1, step_to: 10 },
    };
    for (const name of ['pc', 'voice', 'tap']) {
      status = walks[name];
      h.ctx.fireWin('emma-nav-changed');
      h.ctx.EmmaNav.open();
      await advance(100);
      const opened = { view: visibleView(), x: !xButton.hidden, inert: main.inert };
      xButton.fire('click');
      await advance(10);
      const viaX = !h.ctx.EmmaNav.isOpen();
      let viaEscape = false;
      if (!viaX) {
        doc.dispatch('keydown', { key: 'Escape' });
        await advance(10);
        viaEscape = !h.ctx.EmmaNav.isOpen();
      }
      out[name] = { opened, viaX, viaEscape, inertAfter: main.inert };
      if (h.ctx.EmmaNav.isOpen()) {
        status = Object.assign({}, walks[name], { state: 'arrived' });
        h.ctx.fireWin('emma-nav-changed');
        await advance(1100);
        out[name].afterEnd = { view: visibleView(), x: !xButton.hidden };
        xButton.fire('click');
        await advance(10);
        out[name].closedAfterEnd = !h.ctx.EmmaNav.isOpen();
      }
      status = IDLE;
      h.ctx.fireWin('emma-nav-changed');
      await advance(10);
    }
    return out;
  },
};

// ---- the status link (/ws/robot-face), 2026-10-09 ----
const LINK_ANSWERS = (status) => (m) => {
  if (m === 'emma-face:token') return { token: 's3cret' };
  if (m === 'emma-body:list') return { enabled: true, allow_direction_known: false, gestures: { nod: 'direction_known' } };
  if (m === 'emma-nav:places') return PLACES;
  // an app that states its poll on every status (NavLauncher.status since
  // the 9 Oct review): false unless the scenario's status says otherwise
  if (m === 'emma-nav:status') {
    const s = status();
    return s === null ? null : Object.assign({ poll_stale: false }, s, { t: now / 1000 });
  }
  if (m === 'emma-nav:stop' || m === 'emma-body:stop') return { accepted: true };
  if (m === 'emma-body:nod') return { accepted: true, action: 'nod', reason: null, status: 'direction_known', est_ms: 4000 };
  if (m.startsWith('emma-nav:go:')) return { accepted: true, reason: null, run_id: RUN };
  return null;
};
const types = (frames) => frames.map((f) => f.type);

Object.assign(scenarios, {
  // Only the robot's own app (?app=1 + EmmaRobot/ + EmmaBridge/1) opens it:
  // the sales room, a desk tab and the robot's kiosk Chrome never do.
  async link_not_on_robot() {
    const out = {};
    for (const [name, opts] of [['desk', { search: '?token=t', ua: 'Mozilla/5.0' }],
      ['old_apk', { search: '?app=1&token=t', ua: 'Mozilla/5.0 wv EmmaRobot/0.1' }],
      ['ua_only', { search: '?token=t', ua: ROBOT.ua }],
      ['kiosk_chrome', { search: '?kiosk=1&cam=1&token=t', ua: 'Mozilla/5.0 (Linux; Android 15) Chrome/130' }]]) {
      const h = harness(Object.assign({ answers: () => ({ accepted: true }), ws: true }, opts));
      await advance(65000);
      out[name] = { sockets: h.sockets.length, prompts: h.prompts };
    }
    return out;
  },

  // Opened at load with the page's own `token` parameter; silent until the
  // server's face_link_ready (not onopen); then hello first, the snapshot,
  // and an idle nav_status every 10 s.
  async link_open() {
    const h = harness(Object.assign({ answers: LINK_ANSWERS(() => IDLE), ws: true }, ROBOT));
    await advance(10);
    const s = h.sockets[0];
    const opened = { count: h.sockets.length, url: s && s.url };
    s.open();
    await advance(10);
    const beforeReady = s.sent.length;
    s.message({ type: 'face_link_ready' });
    await advance(10);
    const snapshot = types(s.sent);
    const hello = s.sent[0];
    const mark = s.sent.length;
    await advance(31000);
    const beats = s.sent.slice(mark).filter((f) => f.type === 'nav_status').length;
    return { opened, beforeReady, snapshot, hello, beats,
      goPrompts: h.prompts.filter((m) => m.startsWith('emma-nav:go') || m === 'emma-body:nod').length };
  },

  // The link carries back ONE kind of command: a stop. Anything else that
  // arrives on it is dropped without touching the app or answering.
  async link_stop_only() {
    const h = harness(Object.assign({ answers: LINK_ANSWERS(() => IDLE), ws: true }, ROBOT));
    await advance(10);
    const s = h.sockets[0];
    s.open();
    s.message({ type: 'face_link_ready' });
    await advance(10);
    const mark = s.sent.length;
    h.prompts.length = 0;
    s.message({ type: 'nav_command', command_id: CMD, action: 'go', place: 'big_room', origin: 'voice' });
    s.message({ type: 'body_command', command_id: CMD, action: 'nod' });
    s.message({ type: 'arm_greeting', group: 6 });
    s.message({ type: 'nav_command', command_id: 'XYZ', action: 'stop' });
    await advance(10);
    const ignored = { prompts: h.prompts.slice(), sent: s.sent.slice(mark) };
    s.message({ type: 'nav_command', command_id: CMD, action: 'stop' });
    s.message({ type: 'body_command', command_id: CMD, action: 'stop' });
    await advance(10);
    const acks = s.sent.slice(mark).filter((f) => f.type.endsWith('_ack'));
    return { ignored, stopPrompts: h.prompts.filter((m) => m.endsWith(':stop')), acks };
  },

  // A refusal is final (wrong token, not the robot): one socket, no redial,
  // nothing on screen. A socket that never says face_link_ready (an old
  // server, a refused handshake) is redialled 2 s, 4 s, 8 s ... 30 s, and
  // onopen does not reset that -- face_link_ready does.
  async link_backoff() {
    const out = {};
    for (const code of ['unauthorized', 'not_robot_face']) {
      const h = harness(Object.assign({ answers: LINK_ANSWERS(() => IDLE), ws: true }, ROBOT));
      await advance(10);
      const s = h.sockets[0];
      s.open();
      s.message({ type: 'error', code, message: 'ต้องใส่ token' });
      s.drop();
      await advance(120000);
      out[code] = { sockets: h.sockets.length, sent: s.sent.length, warned: h.warnings.length };
    }
    const h = harness(Object.assign({ answers: LINK_ANSWERS(() => IDLE), ws: true }, ROBOT));
    await advance(10);
    const t0 = h.sockets[0].at;
    const drops = [];
    for (let i = 0; i < 7; i++) {
      const s = h.sockets[h.sockets.length - 1];
      s.open();                                  // the gate accepts-then-closes: onopen fires
      drops.push(now);
      s.drop();
      await advance(31000);
    }
    const gaps = drops.map((at, i) => (h.sockets[i + 1] ? h.sockets[i + 1].at - at : null));
    // ...then a real server: ready resets the backoff for the next drop
    const last = h.sockets[h.sockets.length - 1];
    last.open();
    last.message({ type: 'face_link_ready' });
    await advance(10);
    const n = h.sockets.length;
    const droppedAt = now;
    last.drop();
    await advance(2100);
    out.redial = { gaps, afterReady: h.sockets.length - n, afterReadyGap: h.sockets[n] ? h.sockets[n].at - droppedAt : null,
      t0 };
    return out;
  },

  // Both channels at once: every frame goes to the call AND the link; after
  // the call ends the link keeps reporting (a walk the poller sees, the caps
  // tick) and the call's sender hears nothing more.
  async link_and_call() {
    let status = IDLE;
    let battery = 80;
    const answers = LINK_ANSWERS(() => status);
    const h = harness(Object.assign({ ws: true, answers: (m) => {
      if (m === 'emma-nav:places') return Object.assign({}, PLACES, { battery: battery });
      return answers(m);
    } }, ROBOT));
    await advance(10);
    const s = h.sockets[0];
    s.open();
    s.message({ type: 'face_link_ready' });
    await advance(10);
    const call = [];
    h.ctx.EmmaRobotBridge.ready((f) => { call.push(f); });
    const linkMark = s.sent.length;
    h.ctx.EmmaRobotBridge.detach();
    const callAfterDetach = call.length;
    // a PC run the app's poller sees, between calls
    status = { format: 1, state: 'running', run_id: null, origin: 'pc', reason: 'pc_run', place: 'big_room' };
    h.ctx.fireWin('emma-nav-changed');
    await advance(10);
    const pcFrame = s.sent.slice(linkMark).find((f) => f.type === 'nav_status');
    const beatMark = s.sent.length;
    await advance(16000);
    const walkBeats = s.sent.slice(beatMark).filter((f) => f.type === 'nav_status' && f.state === 'running').length;
    status = IDLE;
    h.ctx.fireWin('emma-nav-changed');
    // past the 30 s after a run (when any caps change goes at once): from
    // here only the caps clock can carry a change -- the one detach() used to
    // stop for good, which would leave the link reporting stale caps
    await advance(35000);
    battery = 60;
    const capsMark = s.sent.length;
    await advance(65000);
    return { callTypes: types(call), callAfterDetach, callNow: call.length, pcFrame, walkBeats,
      laterCaps: s.sent.slice(capsMark).filter((f) => f.type === 'nav_caps').length };
  },

  // An APK from before poll_stale (EmmaBridge/1, so ON_ROBOT) states nothing
  // about its poll: no link at all, for good -- what it had before the link.
  // An app that does not answer yet is asked again on the backoff.
  async link_old_apk() {
    const out = {};
    const h = harness(Object.assign({ ws: true, answers: (m) => {
      if (m === 'emma-nav:status') return { format: 1, state: 'idle', run_id: null };
      return LINK_ANSWERS(() => IDLE)(m);
    } }, ROBOT));
    await advance(65000);
    out.old = { sockets: h.sockets.length, statusAsks: h.prompts.filter((m) => m === 'emma-nav:status').length,
      warned: h.warnings.length };
    let answering = false;
    const h2 = harness(Object.assign({ ws: true, answers: LINK_ANSWERS(() => (answering ? IDLE : null)) }, ROBOT));
    await advance(5000);
    const before = h2.sockets.length;
    answering = true;
    await advance(10000);
    out.late = { before, after: h2.sockets.length };
    return out;
  },

  // The app's poll_stale flag (a stale run.pid poll) reaches the server, and
  // a change of it goes at once, not on the next beat.
  async link_poll_stale() {
    let status = IDLE;
    const h = harness(Object.assign({ answers: LINK_ANSWERS(() => status), ws: true }, ROBOT));
    await advance(10);
    const s = h.sockets[0];
    s.open();
    s.message({ type: 'face_link_ready' });
    await advance(10);
    const mark = s.sent.length;
    status = Object.assign({}, IDLE, { poll_stale: true });
    h.ctx.fireWin('emma-nav-changed');
    const stale = s.sent.slice(mark).filter((f) => f.type === 'nav_status');
    const mark2 = s.sent.length;
    status = IDLE;
    h.ctx.fireWin('emma-nav-changed');
    const fresh = s.sent.slice(mark2).filter((f) => f.type === 'nav_status');
    return { stale, fresh, firstHasKey: 'poll_stale' in s.sent.find((f) => f.type === 'nav_status') };
  },
});

// ---- the walk back to the dock after idle (AutoReturn.java, 2026-10-10) ----
// The app is a script here: `auto` is what emma-nav:auto answers, and every
// emma-nav:auto* / emma-nav:activity:* prompt is recorded in order.
function autoApp(initial) {
  const app = { state: Object.assign({ enabled: true, phase: 'off', left_s: null, place: null, reason: null }, initial),
    asked: [] };
  app.answers = (m) => {
    if (m === 'emma-nav:auto' || m.startsWith('emma-nav:auto:') || m.startsWith('emma-nav:activity:')) {
      app.asked.push(m);
      if (m === 'emma-nav:auto:stay' && app.onStay) app.onStay();
      return Object.assign({}, app.state);
    }
    if (m === 'emma-face:token') return { token: '' };
    if (m === 'emma-body:list') return { enabled: true, allow_direction_known: false, gestures: {} };
    if (m === 'emma-nav:places') return PLACES;
    if (m === 'emma-nav:status') return Object.assign({ poll_stale: false }, IDLE, app.status || {});
    return null;
  };
  return app;
}
const banner = () => ({ hidden: byId('emmaAutoReturn').hidden, kind: byId('emmaAutoReturn').getAttribute('data-kind'),
  title: byId('emmaAutoTitle').textContent, sub: byId('emmaAutoSub').textContent,
  button: byId('emmaAutoStay').hidden ? null : byId('emmaAutoStay').textContent });

Object.assign(scenarios, {
  // Default off: the app answers enabled:false. One read at load and per
  // change event; no banner, no activity report for a call or for speech,
  // and no frame of this ever goes to the server.
  async auto_off() {
    const app = autoApp({ enabled: false, phase: 'off' });
    const h = harness(Object.assign({ answers: app.answers }, ROBOT));
    await advance(10);
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    h.ctx.EmmaRobotBridge.speech();
    h.ctx.fireWin('emma-nav-changed');
    h.ctx.EmmaRobotBridge.detach();
    await advance(5000);
    return { asked: app.asked, banner: banner(), frameTypes: sent.map((f) => f.type),
      autoFrames: sent.filter((f) => JSON.stringify(f).indexOf('auto') >= 0).length };
  },

  // The switch on: a call start/end and speech are reported (speech at most
  // once per 2 s); the answer (the app's state) is drawn at once.
  async auto_activity() {
    // off when the page loaded; switched on at the operator screen, which
    // hides this page (no change event reaches it): the call's start re-reads
    const app = autoApp({ enabled: false, phase: 'off' });
    const h = harness(Object.assign({ answers: app.answers }, ROBOT));
    await advance(10);
    app.state = { enabled: true, phase: 'counting', left_s: 80, place: 'big_room', reason: null };
    app.asked.length = 0;
    h.ctx.EmmaRobotBridge.ready(() => {});
    h.ctx.EmmaRobotBridge.speech();
    await advance(500);
    h.ctx.EmmaRobotBridge.speech();          // inside 2 s: not again
    await advance(2000);
    h.ctx.EmmaRobotBridge.speech();
    h.ctx.EmmaRobotBridge.detach();
    await advance(10);
    return { asked: app.asked, banner: banner() };
  },

  // The warning: drawn on the change event, confirmed ("shown") only from
  // the NEXT read on -- after it is on screen -- and every second while it
  // stays; "อยู่ต่อ" sends stay and the app's answer takes it down. A page
  // that is hidden does not confirm.
  async auto_warning() {
    const app = autoApp({ phase: 'counting', left_s: 15, place: 'big_room' });
    const h = harness(Object.assign({ answers: app.answers }, ROBOT));
    await advance(10);
    app.asked.length = 0;
    app.state = Object.assign({}, app.state, { phase: 'warning', left_s: 10 });
    h.ctx.fireWin('emma-nav-changed');
    const first = { asked: app.asked.slice(), banner: banner() };
    await advance(3100);
    const confirms = app.asked.filter((m) => m === 'emma-nav:auto:shown').length;
    app.state = Object.assign({}, app.state, { left_s: 7 });
    await advance(1000);
    const later = banner();
    doc.hidden = true;
    const before = app.asked.length;
    await advance(2000);
    const whileHidden = app.asked.slice(before);
    doc.hidden = false;
    app.onStay = () => { app.state = Object.assign({}, app.state, { phase: 'counting', left_s: 90 }); };
    byId('emmaAutoStay').fire('click');
    const afterStay = { banner: banner(), last: app.asked[app.asked.length - 1] };
    const mark = app.asked.length;
    await advance(5000);
    return { first, confirms, later, whileHidden, afterStay, readsAfterStay: app.asked.length - mark };
  },

  // While it walks back, and when the walk failed (never retried): the
  // reason in the contract's words, and "รับทราบ" instead of "อยู่ต่อ".
  async auto_going_failed() {
    const app = autoApp({ phase: 'going', place: 'dock' });
    const h = harness(Object.assign({ answers: app.answers }, ROBOT));
    await advance(10);
    const going = banner();
    app.state = Object.assign({}, app.state, { phase: 'failed', reason: 'obstacle', place: null });
    h.ctx.fireWin('emma-nav-changed');
    const failed = banner();
    app.state = Object.assign({}, app.state, { phase: 'failed', reason: '<b>x</b>' });
    h.ctx.fireWin('emma-nav-changed');
    const junk = banner();
    app.onStay = () => { app.state = Object.assign({}, app.state, { phase: 'off', reason: null }); };
    byId('emmaAutoStay').fire('click');
    return { going, failed, junk, dismissed: banner(), asked: app.asked };
  },

  // The app's 60 s stop latch rides the nav_status (review, 10 Oct), and a
  // change of it goes to the server at once.
  async latch_frame() {
    const app = autoApp({ enabled: false });
    const h = harness(Object.assign({ answers: app.answers }, ROBOT));
    await advance(10);
    const sent = [];
    h.ctx.EmmaRobotBridge.ready((f) => { sent.push(f); });
    const mark = sent.length;
    app.status = { stop_latch_s: 60 };
    h.ctx.fireWin('emma-nav-changed');
    const latched = sent.slice(mark).filter((f) => f.type === 'nav_status');
    const mark2 = sent.length;
    app.status = { stop_latch_s: 0 };
    h.ctx.fireWin('emma-nav-changed');
    const cleared = sent.slice(mark2).filter((f) => f.type === 'nav_status');
    return { latched, cleared };
  },
});

(async () => {
  let result;
  if (MODE === 'model') {
    const input = fs.readFileSync(0, 'utf8');
    result = runModel(JSON.parse(input));
  } else if (MODE === 'scenario') {
    if (!scenarios[SCENARIO]) throw new Error('no scenario ' + SCENARIO);
    result = await scenarios[SCENARIO]();
  } else {
    throw new Error('mode must be model or scenario');
  }
  process.stdout.write(JSON.stringify(result));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
