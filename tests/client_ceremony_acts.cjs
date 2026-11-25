// Runs the event screen's engine (client/assets/ceremony-show.js) with ACTS against a fake
// document, a clock and an animation-frame queue the test turns by hand. Prints one JSON
// object. Used by tests/test_ceremony_acts.py: the contract an act is written against --
// when its file is asked for, what its factories put in the page, what stays hidden until
// which beat, what happens when it throws -- is behaviour; a string pin of the source
// would stay green with any of it broken.
//
// A case: { acts: {name: "<source>" | null}, manifest?: {...}, steps: [...] }. An act's
// source is evaluated when the engine appends its <script> (null = the file is missing).
const fs = require('fs');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '..', 'client', 'assets', 'ceremony-show.js'), 'utf8');

function styleOf(e) {
  const s = { setProperty: function (k, v) { e.vars[k] = String(v); }, getPropertyValue: function (k) { return e.vars[k] || ''; } };
  return s;
}
function node(tag) {
  const classes = new Set();
  const e = { tag: tag, children: [], parentNode: null, attrs: {}, vars: {}, textContent: '', innerHTML: '', listeners: {},
    nodeType: tag === '#text' ? 3 : 1, offsetWidth: 0, offsetHeight: 0,
    classList: { add: function (c) { classes.add(c); }, remove: function (c) { classes.delete(c); },
      contains: function (c) { return classes.has(c); } },
    setAttribute: function (k, v) { e.attrs[k] = String(v); if (k === 'class') e.className = String(v); },
    getAttribute: function (k) { return k in e.attrs ? e.attrs[k] : null; },
    removeAttribute: function (k) { delete e.attrs[k]; },
    addEventListener: function (k, fn) { (e.listeners[k] = e.listeners[k] || []).push(fn); },
    appendChild: function (c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = e; e.children.push(c); return c; },
    insertBefore: function (c, ref) {
      if (c.parentNode) c.parentNode.removeChild(c);
      const at = ref ? e.children.indexOf(ref) : -1;
      c.parentNode = e;
      if (at < 0) e.children.push(c); else e.children.splice(at, 0, c);
      return c;
    },
    removeChild: function (c) { e.children = e.children.filter(function (x) { return x !== c; }); c.parentNode = null; return c; } };
  e.style = styleOf(e);
  Object.defineProperty(e, 'className', {
    get: function () { return Array.from(classes).join(' '); },
    set: function (v) { classes.clear(); String(v).split(/\s+/).forEach(function (c) { if (c) classes.add(c); }); } });
  Object.defineProperty(e, 'nextSibling', {
    get: function () { const p = e.parentNode; if (!p) return null; return p.children[p.children.indexOf(e) + 1] || null; } });
  e.has = function (c) { return classes.has(c); };
  if (tag === 'video') { e.play = function () { e.played = (e.played || 0) + 1; }; e.pause = function () { e.paused = (e.paused || 0) + 1; }; e.load = function () {}; }
  return e;
}
function walk(e, fn) { fn(e); (e.children || []).forEach(function (c) { walk(c, fn); }); }
function textOf(e) {
  let out = e.textContent || '';
  // (a letter -- cs-tc: an act's own split; cs-tl: the core's, for the film's type -- joins its word)
  (e.children || []).forEach(function (c) { out += (c.tag === '#text' || (c.has && (c.has('cs-tc') || c.has('cs-tl') || c.has('cs-caret2'))) ? '' : ' ') + textOf(c); });
  return out.replace(/\s+/g, ' ').trim();
}

function run(c) {
  const body = node('body'), head = node('head');
  const asked = [], logs = [], warnings = [];
  let now = 0, timers = [], seq = 0, frames = [], frameSeq = 0;
  const setTimeout = function (fn, ms) { timers.push({ id: ++seq, at: now + (ms || 0), fn: fn }); return seq; };
  const clearTimeout = function (id) { timers = timers.filter(function (t) { return t.id !== id; }); };
  function tick(ms) {
    const until = now + ms;
    for (;;) {
      const due = timers.filter(function (t) { return t.at <= until; }).sort(function (a, b) { return a.at - b.at || a.id - b.id; })[0];
      if (!due) break;
      timers = timers.filter(function (t) { return t !== due; });
      now = due.at;
      due.fn();
    }
    now = until;
  }
  const window = { __log: logs, __flags: c.flags || {}, console: { warn: function () { warnings.push(Array.prototype.slice.call(arguments).map(String).join(' ')); } },
    requestAnimationFrame: function (fn) { frames.push({ id: ++frameSeq, fn: fn }); return frameSeq; },
    addEventListener: function (k, fn) { (window.listeners[k] = window.listeners[k] || []).push(fn); }, listeners: {} };
  if (c.manifest !== undefined) {
    window.location = { protocol: 'http:' };
    window.fetch = function (url, opts) {
      asked.push('fetch ' + url + ' ' + JSON.stringify(opts || {}));
      const answer = { ok: c.manifest !== null, json: function () { return c.manifest; } };
      return { then: function (f) { const r = f(answer); return { then: function (g) { g(r); return { 'catch': function () {} }; } }; } };
    };
  }
  // the page's root (html.mirror-show = the ceremony's screen) and an observer of its class
  const htmlEl = node('html');
  if (c.showMode) htmlEl.classList.add('mirror-show');
  const observers = [];
  window.MutationObserver = function (fn) { this.observe = function () { observers.push(fn); }; };
  const document = { body: body, head: head, documentElement: htmlEl,
    createElement: function (tag) { return node(tag); },
    createElementNS: function (ns, tag) { const e = node(tag); e.ns = ns; return e; },
    createTextNode: function (t) { const n = node('#text'); n.textContent = t; return n; } };
  // a <script>/<link> appended to the head = the browser fetching an act's file
  head.appendChild = function (el) {
    el.parentNode = head;
    head.children.push(el);
    const src = el.tag === 'script' ? el.src : el.href;
    asked.push(el.tag + ' ' + src);
    const m = /\/acts\/([a-z0-9_]+)\.(js|css)(?:\?v=([0-9a-f]+))?$/.exec(src || '');
    setTimeout(function () {
      if (!m) { if (el.onerror) el.onerror(); return; }
      if (m[2] === 'css') { if (el.onload) el.onload(); return; }
      const source = (c.acts || {})[m[1] + (m[3] ? '@' + m[3] : '')] !== undefined ? c.acts[m[1] + '@' + m[3]] : (c.acts || {})[m[1]];
      if (typeof source !== 'string') { if (el.onerror) el.onerror(); return; }
      new Function('CeremonyShow', 'window', 'document', 'setTimeout', source)(window.CeremonyShow, window, document, undefined);
      if (el.onload) el.onload();
    }, c.loadMs === undefined ? 20 : c.loadMs);
    return el;
  };
  new Function('window', 'document', 'setTimeout', 'clearTimeout', code)(window, document, setTimeout, clearTimeout);
  const eng = window.CeremonyShow;
  (c.register || []).forEach(function (name) {
    new Function('CeremonyShow', 'window', 'document', c.acts[name])(eng, window, document);
  });

  function stageBox() {
    const stage = body.children[0] || null;
    const live = stage ? stage.children.filter(function (s) { return s.has('cs-slide') && !s.has('cs-out'); }) : [];
    return { stage: stage, live: live };
  }
  function look() {
    const got = stageBox(), stage = got.stage, live = got.live;
    const hidden = [], keyed = {}, imgs = [], videos = [], classes = {}, vars = {}, styles = {}, tags = {}, paths = [];
    let streaks = 0; const typed = [0, 0];
    live.forEach(function (slide) {
      walk(slide, function (e) {
        const k = e.attrs && e.attrs['data-k'];
        if (k) { keyed[k] = (keyed[k] || 0) + 1; if (e.has('cs-await')) hidden.push(k); }
        if (e.tag === 'img') imgs.push(e.src || null);
        if (e.tag === 'video') videos.push({ src: e.src || null, muted: e.muted === true, loop: e.loop, attrs: Object.keys(e.attrs).sort(), played: e.played || 0,
          time: e.currentTime === undefined ? null : e.currentTime, rate: e.playbackRate === undefined ? null : e.playbackRate });
        tags[e.tag] = (tags[e.tag] || 0) + 1;
        if (e.tag === 'path') paths.push({ cls: e.attrs['class'] || '', d: e.attrs.d || '', attrs: Object.assign({}, e.attrs), style: plain(e.style), 'in': e.has('cs-in') });
        if (e.has('cs-whip-streak')) streaks++;
        if (e.has('cs-tc')) { typed[1]++; if (e.has('cs-on')) typed[0]++; }
        const id = e.attrs && e.attrs['data-t'];
        if (id) { classes[id] = e.className; vars[id] = Object.assign({}, e.vars); styles[id] = plain(e.style); }
      });
    });
    let credit = null, creditParent = null, idle = null;
    if (stage) stage.children.forEach(function (e) {
      if (e.has('cs-credit')) { credit = [e.className, e.textContent]; creditParent = stage.className; }
      if (e.has('cs-idle')) {
        // (the clip sits in its still wrapper, span.cs-idle-m, since 23 Nov: the blend and the mask)
        const wrap = e.children[0], v = wrap && wrap.tag === 'video' ? wrap : (wrap ? wrap.children[0] : undefined);
        idle = { on: stage.has('cs-idle-on'), src: v.src, muted: v.muted === true, loop: v.loop, played: v.played || 0,
                 paused: v.paused || 0 };
        if (v.currentTime !== undefined) idle.time = v.currentTime;
      }
    });
    return { credit: credit, creditParent: creditParent, idle: idle,
             state: eng.state(), hidden: hidden.sort(), keyed: keyed, imgs: imgs, videos: videos, tags: tags,
             text: live.map(textOf).join(' | '), roots: live.map(function (s) { return s.className; }),
             slides: stage ? stage.children.filter(function (s) { return s.has('cs-slide'); }).length : 0,
             stage: stage ? stage.className : null, boxes: live.map(function (s) { return s.children.length; }),
             log: JSON.parse(JSON.stringify(logs)), asked: asked.slice(), warnings: warnings.slice(), frames: frames.length,
             timers: timers.length, classes: classes, vars: vars, styles: styles, acts: eng.acts(),
             paths: paths, streaks: streaks, typed: typed,
             bg: stage && stage.children[0] ? plain(stage.children[0].style) : null,
             links: head.children.filter(function (e) { return e.tag === 'link'; }).map(function (e) { return e.href; }) };
  }
  function plain(style) {
    const out = {};
    Object.keys(style).forEach(function (k) { if (typeof style[k] === 'string' || typeof style[k] === 'number') out[k] = style[k]; });
    return out;
  }

  const seen = [];
  (c.steps || []).forEach(function (s) {
    let ret;
    if (s.show !== undefined) ret = eng.show(s.show, s.late ? { late: true } : undefined);
    if (s.progress !== undefined) eng.progress(s.progress);
    if (s.settle) eng.settle();
    if (s.clear) eng.clear();
    if (s.preload) eng.preload();
    if (s.size) {                                   // the page is laid out: boxes have a size
      const got = stageBox();
      got.live.forEach(function (slide) { walk(slide, function (e) {
        const id = (e.attrs && e.attrs['data-t']) || (e.has && e.has('cs-act-stage') ? 'stage' : null);
        if (id && s.size[id]) { e.offsetWidth = s.size[id][0]; e.offsetHeight = s.size[id][1]; }
      }); });
    }
    if (s.load !== undefined) {                     // the browser has fetched (and decoded) the pictures
      const all = [];
      walk(body, function (e) { if (e.tag === 'img') all.push(e); });
      all.forEach(function (e) { if (s.load === 'fail') { if (e.onerror) e.onerror(); } else if (e.onload) e.onload(); });
    }
    if (s.frame !== undefined) {                    // n animation frames, 16 ms apart
      for (let i = 0; i < s.frame; i++) {
        const due = frames; frames = [];
        now += 16;
        due.forEach(function (f) { f.fn(now); });
      }
    }
    if (s.resize) (window.listeners.resize || []).forEach(function (fn) { fn(); });
    if (s.mode !== undefined) {                      // the page turns the ceremony's show mode on or off
      if (s.mode) htmlEl.classList.add('mirror-show'); else htmlEl.classList.remove('mirror-show');
      observers.forEach(function (fn) { fn(); });
    }
    if (s.call) ret = window.__call[s.call].apply(null, s.args || []);
    if (s.tick !== undefined) tick(s.tick);
    const l = look();
    if (ret !== undefined) l.ret = ret;
    seen.push(l);
  });
  return { seen: seen };
}

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
process.stdout.write(JSON.stringify(out));
