// The story engine (client/assets/emma-story.js) in node: the beat clock's
// rules, then every template driven through a small DOM shim to its end
// state on an interruption and under reduced motion. Run by
// tests/test_stories.py with a JSON file of page payloads (one per template).
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const SRC = fs.readFileSync('client/assets/emma-story.js', 'utf8');

// ---------------------------------------------------------------- DOM shim
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase(); this.children = []; this.parentNode = null;
    this._cls = new Set(); this._text = ''; this.listeners = {}; this.attrs = {};
    this.style = { setProperty(k, v) { this[k] = String(v); } };
  }
  get className() { return Array.from(this._cls).join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() {
    const s = this._cls;
    return {
      add: (...c) => c.forEach(x => s.add(x)), remove: (...c) => c.forEach(x => s.delete(x)),
      contains: c => s.has(c),
      toggle: (c, on) => { const want = on === undefined ? !s.has(c) : !!on; if (want) s.add(c); else s.delete(c); return want; },
    };
  }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = this.children.indexOf(ref); c.parentNode = this;
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c;
  }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get innerText() { return this.textContent; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  click() { (this.listeners.click || []).forEach(f => f({})); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; if (k === 'src') this.src = ''; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 100 }; }
  get clientWidth() { return 1080; } get clientHeight() { return 1920; }
  get offsetWidth() { return 100; } get offsetHeight() { return 100; } get offsetTop() { return 0; }
  _all(out) { this.children.forEach(c => { if (c instanceof El) { out.push(c); c._all(out); } }); return out; }
  _match(sel) { return sel[0] === '.' ? this._cls.has(sel.slice(1)) : this.tagName === sel.toUpperCase(); }
  querySelectorAll(sel) { return this._all([]).filter(e => e._match(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
class Img extends El {
  constructor() { super('img'); this.src = ''; }
  // a picture whose URL says "broken" fails, as a 404 does in the browser
  decode() { return /broken/.test(this.src) ? Promise.reject(new Error('404')) : Promise.resolve(); }
}
class Text { constructor(t) { this.textContent = String(t); this.parentNode = null; } }

function load(reduced) {
  const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval, Promise, Date, Math, Number,
    document: { createElement: t => (String(t).toLowerCase() === 'img' ? new Img() : new El(t)),
                createElementNS: (ns, t) => new El(t), createTextNode: t => new Text(t) },
    Image: Img, innerWidth: 1080, innerHeight: 1920,
    requestAnimationFrame: f => setTimeout(() => f(Date.now()), 0), cancelAnimationFrame: id => clearTimeout(id),
    matchMedia: () => ({ matches: !!reduced }),
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return sandbox;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
// values from the engine's own realm (vm): compare as data, not by prototype
const same = (a, b, msg) => assert.equal(JSON.stringify(a), JSON.stringify(b), msg);

// ---------------------------------------------------------------- the clock
const { BeatClock, norm, CLOCK } = load(false).EmmaStory._clock;

assert.equal(norm('Low-rise 8 ชั้น!'), 'lowrise8ชั้น');
assert.equal(norm('SKY  Pool'), 'skypool');

// One answer: line 0 (no cue needed), "studio" ~2 s in, "bedroom" ~4 s in.
// Her audio is scheduled from t=10 on the AudioContext clock in 0.1 s chunks,
// spoken at `cps` normalised characters a second. `order`:
//   'lead'  -- each piece of transcript arrives just BEFORE its own audio,
//              which is Gemini's order (live 12 Oct: 91/91 answers opened with
//              text, never two pieces without audio between them);
//   'trail' -- each piece arrives `lag` chunks AFTER its audio.
const TEXT = 'We have three types here. Studio is twenty four square metres. One bedroom is bigger.';
function answer(order, lag, cps, size) {
  const c = new BeatClock([{ cues: [] }, { cues: ['Studio'] }, { cues: ['one bedroom', '1 bedroom'] }]);
  const n = norm(TEXT), secs = n.length / cps, chunks = Math.ceil(secs / 0.1);
  const pieces = TEXT.match(new RegExp('.{1,' + size + '}', 'g'));
  const ends = []; let at = 0;
  for (const piece of pieces) { at += norm(piece).length; ends.push(at / cps); }   // audio second each piece ends
  let ti = 0;
  for (let k = 0; k < chunks; k++) {
    if (order === 'lead') {             // the piece whose audio starts in this chunk, first
      while (ti < pieces.length && (ti ? ends[ti - 1] : 0) < (k + 1) * 0.1) c.addText(pieces[ti++]);
    }
    c.audio(10 + k * 0.1, 0.1);
    if (order === 'trail') {
      while (ti < pieces.length && ends[ti] <= (k + 1 - lag) * 0.1 + 1e-9) c.addText(pieces[ti++]);
    }
  }
  while (ti < pieces.length) c.addText(pieces[ti++]);
  return { c, n, secs };
}
function sync(order, lag, cps, size) {
  const { c, n } = answer(order, lag, cps, size);
  const trueAt = word => n.indexOf(norm(word)) / cps;          // where the cue really is in her audio
  const fired = {};
  for (let t = 10; t < 20; t += 0.02) for (const i of c.poll(t).fire) fired[i] = c.played(t);
  return { fired, late: [fired[1] - trueAt('studio'), fired[2] - trueAt('one bedroom')] };
}

{ // nothing moves before her audio plays, however much text arrived
  const c = new BeatClock([{ cues: [] }, { cues: ['studio'] }]);
  c.addText('We have three room types here, the first is the studio');
  same(c.poll(100).fire, []);
  c.audio(50, 4.0);                      // scheduled, not yet playing
  same(c.poll(49.9).fire, []);
  same(c.poll(50.05).fire, [0], 'the scene opens with her voice');
  // the piece came BEFORE its audio: until audio has followed it and the next
  // piece arrives, its audio may not all be in -- its cue waits
  same(c.poll(53.9).fire, [], 'a piece whose audio is not settled does not fire');
  c.addText(' and the next');            // its audio was the 4 s after it
  same(c.poll(53.95).fire, [1], 'settled: "studio" ~6 characters before the piece ends at 4.0 s');
}

for (const cps of [12, 14, 15]) for (const size of [8, 13, 20]) {
  // Gemini's order: a beat never fires before its cue is spoken (review 12 Oct:
  // timed on the audio scheduled when the text arrived, beats fired 0.3-0.4 s
  // early on the real page), and not more than about a piece late
  const { fired, late } = sync('lead', 0, cps, size);
  assert.ok(fired[0] < 0.1, 'beat 0 at her first audio');
  late.forEach((d, k) => {
    assert.ok(d >= -0.02, `beat ${k + 1} led her voice (text first, ${cps} cps, ${size}-char pieces): ${d.toFixed(2)} s`);
    assert.ok(d < 1.7, `beat ${k + 1} far too late (text first, ${cps} cps, ${size}): ${d.toFixed(2)} s`);
  });
}
for (const lag of [0, 3, 6]) { // text trailing its audio: late by up to a piece, never early
  const { fired, late } = sync('trail', lag, 14, 8);
  assert.ok(fired[0] < 0.1, 'beat 0 at her first audio');
  late.forEach((d, k) => {
    assert.ok(d >= -0.05, `beat ${k + 1} led her voice (lag ${lag}): ${d.toFixed(2)} s`);
    assert.ok(d < 2.0, `beat ${k + 1} far too late (lag ${lag}): ${d.toFixed(2)} s`);
  });
}

{ // Robot 14 Oct: Thai at 11.5-13.6 chars/s, text arriving TWO pieces ahead of
  // its audio -- the old 16 chars/s floor fired every pre-complete beat at
  // 0.84-0.93 of its word ("ตอนโชว์... ยังไม่ตรง"). The floor is now her voice
  // (12.5 for Thai), and a finished answer teaches the page her real rate.
  const { BeatClock: BC, norm: nm, CLOCK: K, leadRateFor, resetRates } = load(false).EmmaStory._clock;
  resetRates();
  const TH = 'ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ ชั้นใต้ดินมีสระในร่ม Aqua Cinema '
    + 'และ Journey to Mars ชั้น G มีฟิตเนส Biogenesis ห้องปั่นจักรยาน และชั้นสามอาคาร A มี Sky Pool ค่ะ';
  function lead2(cps) {
    const c = new BC([{ cues: [] }, { cues: ['aqua cinema'] }, { cues: ['biogenesis'] }, { cues: ['sky pool'] }]);
    const n = nm(TH), pieces = TH.match(/.{1,14}/g), ends = [];
    let at = 0;
    for (const piece of pieces) { at += nm(piece).length; ends.push(at / cps); }
    const chunks = Math.ceil(n.length / cps / 0.1);
    let ti = 0;
    for (let k = 0; k < chunks; k++) {   // text two pieces ahead of the audio it transcribes
      while (ti < pieces.length && (ti >= 2 ? ends[ti - 2] : 0) < (k + 1) * 0.1) c.addText(pieces[ti++]);
      c.audio(10 + k * 0.1, 0.1);
    }
    while (ti < pieces.length) c.addText(pieces[ti++]);
    c.addText(' ');                      // the last piece settles on what follows it (turn_complete live)
    const fired = {};
    for (let t = 10; t < 30; t += 0.02) for (const i of c.poll(t).fire) if (!(i in fired)) fired[i] = c.played(t);
    return { c, n, fired };
  }
  for (const cps of [12, 13, 13.6]) {
    const { c, n, fired } = lead2(cps);
    ['aquacinema', 'biogenesis', 'skypool'].forEach((w, k) => {
      // faster than the floor, a cue near the very end waits for her last
      // word (turn_complete then fires it on the rest): late, never early
      const d = (k + 1 in fired ? fired[k + 1] : c.sched) - n.indexOf(w) / cps;
      assert.ok(d >= -0.6, `Thai at ${cps} cps, text two pieces ahead: beat ${k + 1} led her voice by ${(-d).toFixed(2)} s`);
      assert.ok(d < 2.0, `Thai at ${cps} cps: beat ${k + 1} far too late: ${d.toFixed(2)} s`);
    });
  }
  assert.equal(leadRateFor('th'), K.voiceRate.th, 'no answer finished yet: the measured default');
  const { c } = lead2(11.5);
  c.turnComplete();
  for (let t = 10; t < 40; t += 0.05) c.poll(t);              // played out: the page learns her rate
  assert.ok(Math.abs(leadRateFor('th') - (K.voiceRate.th + 11.5) / 2) < 0.6, 'learned half-way: ' + leadRateFor('th'));
  assert.equal(leadRateFor('xx'), K.leadRate, 'a language never measured keeps the generic floor');
  // review 14 Oct: Chinese has its own (slower) floor, not the generic 16
  assert.ok(K.voiceRate.zh > 0 && K.voiceRate.zh < K.voiceRate.th, 'Mandarin floor: ' + K.voiceRate.zh);
  resetRates();
}

{ // the turn_complete gemini-3.8 sends before its tool call is not this answer's
  const c = new BeatClock([{ cues: [] }]);
  c.turnComplete();
  assert.equal(c.complete, false);
  c.audio(0, 1); c.turnComplete();
  assert.equal(c.complete, true);
}

{ // once the answer is complete the whole answer's ratio places the cue
  const { c, n, secs } = answer('trail', 4, 14, 8);
  c.turnComplete();
  const at = n.indexOf('onebedroom');
  const t = c.charTime(at);
  assert.ok(t >= at / 14 && t <= at / 14 + 0.5, 'complete-answer estimate: ' + t);
  assert.ok(c.sched >= secs);
}

{ // cue words are found in order: line 2's cue said early does not fire it first
  const c = new BeatClock([{ cues: [] }, { cues: ['alpha'] }, { cues: ['beta'] }]);
  c.audio(0, 10);
  c.addText('beta first then alpha then beta again');
  assert.ok(c.cueAt[1] > 0 && c.cueAt[2] > c.cueAt[1], 'beta after alpha: ' + c.cueAt);
}

{ // a paraphrase: no cue for beat 1 -> it fires FALLBACK seconds after beat 0, not before
  const c = new BeatClock([{ cues: [] }, { cues: ['never said'] }, { cues: ['later'] }]);
  for (let k = 0; k < 100; k++) c.audio(k * 0.1, 0.1);   // 10 s of her voice
  c.addText('words words words words words');
  same(c.poll(0.05).fire, [0]);
  same(c.poll(CLOCK.fallbackS - 0.2).fire, [], 'no fallback before ~4 s');
  same(c.poll(CLOCK.fallbackS + 0.1).fire, [1]);
}

{ // with the answer complete, line lengths move the fallback earlier (never past its line)
  const c = new BeatClock([{ cues: [], w: { th: 10, en: 10 } }, { cues: ['x-never'], w: { th: 10, en: 10 } },
                           { cues: ['y-never'], w: { th: 20, en: 20 } }]);
  for (let k = 0; k < 80; k++) c.audio(k * 0.1, 0.1);    // 8 s
  c.addText('สวัสดีค่ะ ข้อความภาษาไทย'); c.turnComplete();
  assert.equal(c.lang, 'th');
  same(c.poll(0.05).fire, [0]);
  same(c.poll(1.9).fire, [], 'line 1 starts at 2.0 s of 8');
  same(c.poll(2.0 + CLOCK.marginS + 0.01).fire, [1]);
}

{ // her audio has played out: every remaining beat, then the overview
  const c = new BeatClock([{ cues: [] }, { cues: ['a1'] }, { cues: ['b2'] }]);
  c.audio(0, 2); c.addText('nothing matches'); c.turnComplete();
  const r = c.poll(2.0);
  same(r.fire, [0, 1, 2]);
  assert.equal(r.rest, true);
}

// ---------------------------------------------------------------- the templates
const payloads = process.argv[2] ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')) : [];

const SHOWN = ['es-row', 'es-slab', 'es-fl', 'es-card', 'es-r', 'es-fig', 'es-chip', 'es-chipbtn', 'es-area',
               'es-jr-r', 'es-jr-v', 'es-jr-spine', 'es-nb-r', 'es-nb-pill', 'es-nb-tag', 'es-nb-park', 'es-nb-pin',
               'es-thumb', 'es-strip'];

// A count on its way up shows round steps only, never a precise figure that
// is not the fact ("124 m" on the way to 155 -- 13 Oct recordings). Once, on
// the first counting template: sampling every template ran past the 20 s cap.
async function countsInRoundSteps(payload) {
  const S = load(false).EmmaStory, mount = new El('div');
  let now = null;
  S.play(payload, { mount, clock: () => now, onTap: () => true });
  S.transcript('first words');
  S.audio(1, 8); now = 4;
  const root = mount.children[0];
  let seen = 0;
  for (let n = 0; n < 30; n++) {
    for (const e of root._all([])) {
      if (e._final == null || !e.classList.contains('es-counting')) continue;
      const fin = Number(String(e._final).replace(/[^0-9.]/g, ''));
      const v = Number(String(e.textContent).replace(/[^0-9.]/g, ''));
      const digits = String(Math.floor(fin)).length;
      const step = fin < 10 ? 1 : digits <= 3 ? 10 : 10 ** (digits - 2);
      assert.ok(Number.isInteger(v / step) && v <= fin, `${payload.template}: count showed ${e.textContent} on the way to ${e._final}`);
      seen++;
    }
    await sleep(40);
  }
  assert.ok(seen > 0, payload.template + ': no count was seen on its way up -- the check checked nothing');
  // A count runs 1 s from its beat; the sampling above is ~1.2 s of timers, and
  // on a loaded machine (the full suite beside other runs, 15-16 Oct) it ended
  // on '1,000' of '1,066' -- the figure had not landed yet, not landed wrong.
  // Up to 3 s more for it to land; it must still land exactly.
  const pending = () => root._all([]).some((e) => e._final != null && e.textContent !== e._final);
  for (let w = 0; w < 75 && pending(); w++) await sleep(40);
  for (const e of root._all([])) if (e._final != null) assert.equal(e.textContent, e._final, 'the exact figure lands');
  S.teardown();
}

// A thumbnail the guest taps is their pick: the places still queued behind
// her voice do not take it back, later beats do not either, and the overview
// keeps it (review 13 Oct: the pick was replaced a second later).
async function drillPickHolds(payload) {
  const S = load(false).EmmaStory, mount = new El('div');
  S.play(payload, { mount, clock: () => null, onTap: () => true });
  const root = mount.children[0];
  S._fireTo(payload.beats.length - 2);            // every place but the last, queued
  await sleep(150);
  const thumbs = root.querySelectorAll('.es-thumb'), cards = root.querySelectorAll('.es-fc');
  thumbs[0].click();
  await sleep(1400);                              // past the queue's dwell
  assert.ok(cards[0].classList.contains('on'), 'drill: the pick stays up');
  S._fireTo(payload.beats.length - 1);            // her next place
  await sleep(1200);
  assert.ok(cards[0].classList.contains('on'), 'drill: a later beat does not take the pick back');
  S.settle();
  assert.ok(cards[0].classList.contains('on'), 'drill: the overview keeps the pick');
  assert.equal(cards.filter(c => c.classList.contains('on')).length, 1);
  S.teardown();
}

async function oneStory(payload) {
  const win = load(false), S = win.EmmaStory;
  const mount = new El('div');
  let now = null; const sent = [], reports = [];
  assert.equal(S.play(payload, { mount, clock: () => now, onTap: id => { sent.push(id); return true; },
                                 onReport: r => reports.push(r) }), true);
  assert.equal(mount.children.length, 1, payload.template + ': one root');
  const root = mount.children[0];
  assert.ok(root.classList.contains('es-root'));
  // her answer begins: beat 0 fires on her first audio, not before
  S.transcript('first words');
  await sleep(120);
  assert.equal(S.state().fired, 0, payload.template + ': nothing before audio');
  S.audio(1, 8); now = 1.3;
  await sleep(150);
  assert.ok(S.state().fired >= 1, payload.template + ': beat 0 with her voice');
  // the guest speaks: the end state at once, nothing animating
  S.settle();
  const st = S.state();
  assert.equal(st.ended, true); assert.equal(st.instant, true);
  // cut short by the guest: one report, numbers only, a beat per beat
  assert.equal(reports.length, 1, payload.template + ': one report');
  const rep = reports[0];
  assert.equal(rep.interrupted, true);
  assert.equal(rep.beats.length, payload.beats.length);
  assert.ok(rep.beats[0].at != null, payload.template + ': beat 0 went up on her voice');
  assert.ok(rep.beats.every(b => (b.at === null || typeof b.at === 'number') && (b.cue === null || typeof b.cue === 'number')));
  assert.equal(typeof rep.frames, 'number');
  assert.equal(st.fired, payload.beats.length, payload.template + ': every beat applied');
  for (const cls of SHOWN) {
    for (const e of root.querySelectorAll('.' + cls)) assert.ok(e.classList.contains('on'), `${payload.template}: .${cls} not shown`);
  }
  assert.equal(root.querySelectorAll('.es-big').length, 0, payload.template + ': no card left mid-flight');
  // a count cut short lands at full strength (review 13 Oct: left at 50%)
  assert.equal(root.querySelectorAll('.es-counting').length, 0, payload.template + ': a count left dimmed');
  // one floor, one place at a time: exactly one big card up at rest, and a
  // thumbnail brings another one up (nothing sent)
  const fcs = root.querySelectorAll('.es-fc');
  if (fcs.length) {
    assert.equal(fcs.filter(e => e.classList.contains('on')).length, 1, payload.template + ': one place on screen');
    const thumbs = root.querySelectorAll('.es-thumb');
    assert.equal(thumbs.length, fcs.length, payload.template + ': a thumbnail per place');
    if (thumbs.length > 1) {
      const other = fcs.findIndex(e => !e.classList.contains('on'));
      thumbs[other].click();
      await sleep(80);
      assert.ok(fcs[other].classList.contains('on'), payload.template + ': the thumbnail brought its place up');
      assert.equal(fcs.filter(e => e.classList.contains('on')).length, 1, payload.template + ': still one place');
    }
  }
  // the drawn templates: every bar driven, every line drawn
  for (const e of root.querySelectorAll('.es-jr-run')) assert.ok(/translate3d\((?!0\.00%)/.test(e.style.transform || ''), payload.template + ': a bar not driven');
  for (const cls of ['es-nb-road', 'es-nb-measure']) {
    for (const e of root.querySelectorAll('.' + cls)) assert.ok(/scaleX\(1\)/.test(e.style.transform || ''), `${payload.template}: .${cls} not drawn`);
  }
  await sleep(60);
  // count-ups read their final number (late callbacks do not restart them)
  for (const e of root._all([])) if (e._final != null) assert.equal(e.textContent, e._final, payload.template + ': count final');
  // a tap sends only its id
  const tap = root.querySelectorAll('.es-tap').find(e => e.classList.contains('on'));
  if (payload.taps && payload.taps.length) {
    assert.ok(tap, payload.template + ': a tappable overview');
    tap.click();
    assert.equal(sent.length, 1);
    assert.ok(payload.taps.some(t => t.id === sent[0]), 'tap id ' + sent[0]);
  }
  // audio/transcript after the end change nothing -- not even the clock
  const fired = S.state().fired, sched = S.state().sched;
  S.audio(20, 5); S.transcript('more');
  assert.equal(S.state().fired, fired);
  assert.equal(S.state().sched, sched, payload.template + ': audio after the end is not counted');
  // teardown: the tree leaves the page and the story keeps no picture. The
  // src stays: clearing it on detached images is what leaked every story's
  // DOM in Chromium (review 12 Oct) -- the collector frees them with the tree
  const imgs = root._all([]).filter(e => e.tagName === 'IMG');
  S.teardown();
  assert.equal(reports.length, 1, payload.template + ': teardown after the report does not report again');
  assert.equal(mount.children.length, 0);
  assert.ok(imgs.every(i => i._ready === null && i.src), payload.template + ': pictures dropped, src untouched');
  assert.equal(S.isActive(), false);

  // reduced motion: straight to the end state
  const calm = load(true).EmmaStory, m2 = new El('div');
  calm.play(payload, { mount: m2, clock: () => null, onTap: () => true });
  assert.equal(calm.state().ended, true, payload.template + ': reduced motion = end state');
  assert.equal(calm.state().instant, true);
  calm.teardown();
}

// ------------------------------------------- the picture follows her voice
// What a beat may show is what its line SAYS: a number, a distance or the
// real render waits for the line that carries it (integration review of the
// designers' 20 stories, 12 Oct). Each check: after the earlier beats, the
// thing is still hidden; after its own beat, it is shown.
const pic = (w, h) => ({ src: '/story-asset/x', w: w || 400, h: h || 300 });
const byCls = (root, cls) => root.querySelectorAll('.' + cls);

async function follows() {
  const run = async (payload, check) => {
    const S = load(false).EmmaStory, mount = new El('div');
    assert.equal(S.play(payload, { mount, clock: () => null }), true);
    await check(S, mount.children[0]);
    S.teardown();
  };
  const beats = n => Array.from({ length: n }, () => ({ cues: [], w: { th: 10, en: 10 } }));
  // metric: the picture and its caption open with her voice, the number at `line`
  await run({ id: 'm', template: 'metric', beats: beats(3), taps: [],
              params: { bg_asset: pic(), value: { to: 224, prefix_en: '≈', unit_en: 'CARS' }, label_en: 'Parking',
                        parts: [{ label_en: 'Motorbikes', to: 108, prefix_en: '≈', line: 2 }], line: 1 } },
    async (S, root) => {
      S._fireTo(0); await sleep(80);
      const n = root.querySelector('.es-n');
      assert.ok(byCls(root, 'es-big-n')[0].classList.contains('on'), 'metric: caption with her first line');
      assert.ok(!n.classList.contains('on'), 'metric: the number waits for its line');
      S._fireTo(1); await sleep(80);
      assert.ok(n.classList.contains('on'), 'metric: the number on its line');
      assert.ok(!byCls(root, 'es-part')[0].classList.contains('on'), 'metric: a part waits for its own line');
      S._fireTo(2); await sleep(80);
      assert.ok(byCls(root, 'es-part')[0].classList.contains('on'));
      assert.ok(byCls(root, 'es-part')[0].textContent.indexOf('≈') >= 0, 'metric: a part keeps its prefix');
    });
  // distance: the route and its distance label wait for `route_to.line`
  await run({ id: 'd', template: 'distance', beats: beats(3), taps: [],
              params: { map_asset: pic(), pin: { x: 60, y: 50, label_en: 'US' },
                        route_to: { x: 20, y: 50, label_en: '≈ 0.6 km', line: 2 },
                        rows: [{ label_en: 'Park', value_en: 'Next door', line: 1 }] } },
    async (S, root) => {
      S._fireTo(1); await sleep(1300);
      const route = byCls(root, 'es-route')[0];
      assert.ok(/scaleX\(0\)/.test(route.style.transform), 'distance: no route before its line');
      S._fireTo(2); await sleep(900);
      assert.ok(/scaleX\(1\)/.test(route.style.transform), 'distance: the route on its line');
    });
  // assembly: the frames play, the real render waits for `rise_line`; fade mode
  await run({ id: 'a', template: 'assembly', beats: beats(3), taps: [],
              params: { frames: [pic(), pic()], frame_ms: 10, final_asset: pic(), final_mode: 'fade',
                        rise_line: 2, ground_y: 80, roof_y: 20, meter_x: 70,
                        counters: [{ to: 23, unit_en: 'M', line: 2 }] } },
    async (S, root) => {
      S._fireTo(0); await sleep(300);
      const rise = byCls(root, 'es-rise')[0];
      assert.ok(rise.classList.contains('fade'), 'assembly: a different camera crossfades');
      assert.ok(!rise.classList.contains('on'), 'assembly: the render waits for its line');
      S._fireTo(2); await sleep(120);
      assert.ok(rise.classList.contains('on'), 'assembly: the render on its line');
      assert.equal(byCls(root, 'es-meter')[0].style.left, '70%', 'assembly: meter_x places the meter');
    });
  // details, four lines: wire, solid, area, mood -- the area on the size line
  await run({ id: 't', template: 'details', beats: beats(4), taps: [],
              params: { wire_asset: pic(), solid_asset: pic(), value: { from: 24, to: 29, unit_en: 'SQ.M.' },
                        meta_en: ['x'], mood_assets: [pic()], lines: [0, 1, 2, 3] } },
    async (S, root) => {
      S._fireTo(1); await sleep(80);
      const area = byCls(root, 'es-area')[0], mood = byCls(root, 'es-mood')[0];
      assert.ok(byCls(root, 'es-solid')[0].classList.contains('on'), 'details: the solid room on line 1');
      assert.ok(!area.classList.contains('on'), 'details: the size waits for the size line');
      S._fireTo(2); await sleep(80);
      assert.ok(area.classList.contains('on') && !mood.classList.contains('on'));
      S._fireTo(3); await sleep(80);
      assert.ok(mood.classList.contains('on'), 'details: the mood on its line');
    });
  // stack: an opening line that names no item shows the empty rows, unlabelled;
  // while she talks the list is drawn small (one transform), at rest full size
  await run({ id: 's', template: 'stack', beats: beats(3), taps: [],
              params: { items: [{ label_en: 'Studio', asset: pic(), line: 1, value: { from: 24, to: 29, unit_en: 'sq.m.' } },
                                { label_en: '1 Bed', asset: pic(), line: 2 }] } },
    async (S, root) => {
      const dock = byCls(root, 'es-dock')[0];
      assert.ok(/scale\(/.test(dock.style.transform || ''), 'stack: the small list during the beats');
      S._fireTo(0); await sleep(40);
      const rows = byCls(root, 'es-row');
      assert.ok(rows.every(r => r.classList.contains('ghost') && !r.classList.contains('on')), 'stack: frames wait');
      S._fireTo(1); await sleep(80);
      // a range is drawn at its final value -- never "0-0", never a count
      const big = byCls(root, 'es-big')[0];
      assert.ok(big && big.textContent.indexOf('24–29') >= 0, 'stack: the range at its final value');
      assert.ok(big.style.top && big.style.height, 'stack: the card is placed under the docked rows');
      S.settle();
      assert.ok(rows.every(r => r.classList.contains('on')), 'stack: the overview shows every row');
      assert.equal(dock.style.transform, '', 'stack: the list at full size at rest');
    });
  // spotlight: a photo that will not load -- the ring and label stay on the
  // zoomed picture, the photo frame is hidden
  await run({ id: 'p', template: 'spotlight', beats: beats(2), taps: [],
              params: { base_asset: pic(800, 300), zoom: { x: 50, y: 50, scale: 1.2 },
                        spot: { x: 50, y: 60, label_en: 'POOL' }, spot_line: 0,
                        photo: { asset: { src: '/story-asset/broken', w: 400, h: 300 }, title_en: 'Pool',
                                 chips_en: ['Glass floor', { label_en: 'Length', value_en: '≈ 155 m' }], line: 1 } } },
    async (S, root) => {
      await sleep(30);                                   // the 404 lands
      S.settle();
      const base = byCls(root, 'es-spbase')[0], photo = byCls(root, 'es-photo')[0];
      assert.ok(!base.classList.contains('inset'), 'spotlight: no inset without a photo');
      assert.equal(photo.style.visibility, 'hidden', 'spotlight: the failed photo frame is hidden');
      assert.ok(base.querySelector('.es-label').classList.contains('on'), 'spotlight: the label stays on the picture');
      assert.equal(byCls(root, 'es-broken').length, 1, 'spotlight: the broken picture is marked');
      assert.equal(byCls(root, 'es-key').length, 1, 'spotlight: a {label, value} chip is a key row');
    });
  // ... and with the photo, an answer that ends while the zoom's ring timer
  // is still pending: no label comes back inside the corner inset
  {
    const S = load(false).EmmaStory, mount = new El('div');
    let now = null;
    S.play({ id: 'p2', template: 'spotlight', beats: beats(2), taps: [],
             params: { base_asset: pic(800, 300), zoom: { x: 50, y: 50, scale: 1.2 },
                       spot: { x: 50, y: 60, label_en: 'POOL' }, spot_line: 0,
                       photo: { asset: pic(), title_en: 'Pool', chips_en: ['Glass floor'], line: 1 } } },
           { mount, clock: () => now });
    S.audio(0, 1); S.turnComplete(); now = 2;            // a short answer, already played out
    await sleep(2300);                                   // finish -> rest (+0.7 s) -> the ring timer (+1.15 s)
    const base = mount.children[0].querySelector('.es-spbase');
    assert.ok(S.state().ended && base.classList.contains('inset'));
    assert.ok(!base.querySelector('.es-label').classList.contains('on'), 'spotlight: no label inside the inset');
    S.teardown();
  }
}

// ------------------------------------------- the safety exits of a story
async function exits() {
  const pay = { id: 'x', template: 'features', beats: [{ cues: [] }, { cues: ['never'] }], taps: [],
                params: { items: [{ label_en: 'A', asset: pic(), line: 1 }] } };
  { // her audio played out, no turn_complete (a stalled stream): it rests
    const S = load(false).EmmaStory, C = S._clock.CLOCK, keep = C.stallS;
    C.stallS = 0.3;
    let now = 0;
    S.play(pay, { mount: new El('div'), clock: () => now });
    S.audio(0, 1); now = 2;
    await sleep(800);
    assert.equal(S.state().ended, true, 'a stalled answer rests on its overview');
    C.stallS = keep; S.teardown();
  }
  { // the page's audio clock went away mid-answer (the call closed): it rests
    const S = load(false).EmmaStory, C = S._clock.CLOCK, keep = C.blindS;
    C.blindS = 0.3;
    let now = 0.2;
    S.play(pay, { mount: new El('div'), clock: () => now });
    S.audio(0, 5); await sleep(200);
    assert.equal(S.state().ended, false);
    now = null; await sleep(800);
    assert.equal(S.state().ended, true, 'a story whose call closed rests on its overview');
    C.blindS = keep; S.teardown();
  }
  { // no audio at all: the overview after noAudioS
    const S = load(false).EmmaStory, C = S._clock.CLOCK, keep = C.noAudioS;
    C.noAudioS = 0.3;
    S.play(pay, { mount: new El('div'), clock: () => null });
    await sleep(800);
    assert.equal(S.state().ended, true, 'an answer that never plays rests');
    C.noAudioS = keep; S.teardown();
  }
}

(async () => {
  await follows();
  await exits();
  for (const payload of payloads) await oneStory(payload);
  const counting = payloads.find(p => p.template === 'metric');
  if (counting) await countsInRoundSteps(counting);
  const drill = payloads.find(p => p.template === 'drill' && p.params.items.length >= 3);
  if (drill) await drillPickHolds(drill);
  // one story at a time: a second play tears the first down
  if (payloads.length >= 2) {
    const S = load(false).EmmaStory, mount = new El('div');
    S.play(payloads[0], { mount, clock: () => null });
    S.play(payloads[1], { mount, clock: () => null });
    assert.equal(mount.children.length, 1);
    S.teardown();
  }
  // an unknown template draws nothing
  const S = load(false).EmmaStory, mount = new El('div');
  assert.equal(S.play({ template: 'video', beats: [], params: {} }, { mount }), false);
  assert.equal(mount.children.length, 0);
  console.log('Story clock and ' + payloads.length + ' templates PASS');
})().catch(e => { console.error(e); process.exit(1); });
