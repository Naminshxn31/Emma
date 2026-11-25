// Runs the event screen's slide engine (client/assets/ceremony-show.js) against a fake
// document and a clock the test turns by hand. Prints one JSON object.
// Used by tests/test_ceremony_slides.py: which parts of a slide are up at which point of
// her voice is behaviour -- a string pin of the source stays green with the beats broken.
const fs = require('fs');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '..', 'client', 'assets', 'ceremony-show.js'), 'utf8');

function node(tag) {
  const classes = new Set();
  const e = { tag: tag, children: [], parentNode: null, attrs: {}, vars: {}, textContent: '', innerHTML: '',
    classList: { add: function (c) { classes.add(c); }, remove: function (c) { classes.delete(c); },
      contains: function (c) { return classes.has(c); } },
    style: { setProperty: function (k, v) { e.vars[k] = v; } },
    setAttribute: function (k, v) { e.attrs[k] = String(v); },
    getAttribute: function (k) { return k in e.attrs ? e.attrs[k] : null; },
    appendChild: function (c) { c.parentNode = e; e.children.push(c); return c; },
    insertBefore: function (c, ref) {
      c.parentNode = e;
      const at = ref ? e.children.indexOf(ref) : -1;
      if (at < 0) e.children.push(c); else e.children.splice(at, 0, c);
      return c;
    },
    removeChild: function (c) { e.children = e.children.filter(function (x) { return x !== c; }); c.parentNode = null; return c; } };
  Object.defineProperty(e, 'className', {
    get: function () { return Array.from(classes).join(' '); },
    set: function (v) { classes.clear(); String(v).split(/\s+/).forEach(function (c) { if (c) classes.add(c); }); } });
  e.has = function (c) { return classes.has(c); };
  return e;
}
function walk(e, fn) { fn(e); (e.children || []).forEach(function (c) { walk(c, fn); }); }
function textOf(e) {
  let out = e.textContent || '';
  (e.children || []).forEach(function (c) { out += ' ' + textOf(c); });
  return out.replace(/\s+/g, ' ').trim();
}

function run(c) {
  const body = node('body');
  const document = { body: body, createElement: node,
    createTextNode: function (t) { const n = node('#text'); n.textContent = t; return n; } };
  const window = {};
  let now = 0, timers = [], seq = 0;
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
  new Function('window', 'document', 'setTimeout', 'clearTimeout', code)(window, document, setTimeout, clearTimeout);
  const eng = window.CeremonyShow;

  function look() {
    const stage = body.children[0] || null;
    const live = stage ? stage.children.filter(function (s) { return s.has('cs-slide') && !s.has('cs-out'); }) : [];
    const up = {}, nums = [], kinds = [], clear = [], under = [], imgs = [], past = [];
    let ended = false, drawings = 0; const gone = [], capoff = [], landed = [];
    live.forEach(function (slide) {
      kinds.push(slide.className);
      walk(slide, function (e) {
        const k = e.attrs['data-k'];
        if (k) up[k] = (up[k] === undefined ? true : up[k]) && e.has('cs-in');
        if (e.has('cs-num')) nums.push([e.textContent, e.has('cs-counting')]);
        // a part that carries a picture not decoded yet: clear, whatever its beat says
        if (e.has('cs-needs') && !e.has('cs-loaded')) clear.push(k || e.className.split(' ')[0]);
        if (e.has('cs-under')) under.push(k);
        if (e.has('cs-past')) past.push(k);
        if (e.has('cs-ended')) ended = true;
        if (e.has('cs-gone')) gone.push(textOf(e));
        if (e.has('cs-capoff')) capoff.push(k);
        if (e.has('cs-drawing')) drawings++;
        if (e.has('cs-num') && e.has('cs-landed')) landed.push(e.textContent);
        if (e.tag === 'img') imgs.push(e.src || null);
      });
    });
    const shown = Object.keys(up).filter(function (k) { return up[k]; }).sort();
    const picked = [];
    live.forEach(function (slide) { walk(slide, function (e) { if (e.has('cs-picked')) picked.push(textOf(e)); }); });
    return { state: eng.state(), shown: shown, hidden: Object.keys(up).filter(function (k) { return !up[k]; }).sort(),
             nums: nums, on: body.has('cs-on'), stageUp: !!stage && stage.has('cs-up'),
             slides: stage ? stage.children.filter(function (s) { return s.has('cs-slide'); }).length : 0,
             live: live.length, kinds: kinds, text: live.map(textOf).join(' | '), picked: picked,
             clear: clear.sort(), under: under.sort(), past: past.sort(), imgs: imgs, ended: ended, gone: gone, capoff: capoff.sort(), drawings: drawings, landed: landed };
  }

  const seen = [];
  (c.steps || []).forEach(function (s) {
    let ret;
    if (s.show !== undefined) ret = eng.show(s.show, s.late ? { late: true } : undefined);
    if (s.progress !== undefined) eng.progress(s.progress);
    if (s.settle) eng.settle();
    if (s.clear) eng.clear();
    if (s.load !== undefined) {              // the browser has fetched (and decoded) the first n pictures
      const all = [];
      walk(body, function (e) { if (e.tag === 'img') all.push(e); });
      all.slice(0, s.load === true ? all.length : s.load).forEach(function (e) { if (e.onload) e.onload(); });
    }
    if (s.tick !== undefined) tick(s.tick);
    const l = look();
    if (ret !== undefined) l.ret = ret;
    seen.push(l);
  });
  return { seen: seen, icons: eng.icons, templates: eng.templates };
}

const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
process.stdout.write(JSON.stringify(out));
