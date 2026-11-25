// Runs the room-screen page's ceremony subtitle code (client/voice-preview.html), cut out
// verbatim, against a fake document and a fake audio clock. Prints one JSON object.
// Used by tests/test_ceremony_screen.py: which piece is up at which second of her voice
// is behaviour -- a string pin of the source stays green with the timing broken.
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'voice-preview.html'), 'utf8');
const from = '    var MIRROR_SUB_CHARS = ';
const to = '    // ---- a film over the face, on the room screen only (app/event_clips.py) ----';
const a = html.indexOf(from), b = html.indexOf(to, a);
if (a < 0 || b < 0) throw new Error('subtitle slice not found');
const code = html.slice(a, b);

function el() {
  const classes = new Set();
  let own = '';
  return { className: '', children: [],
    // (as a browser: setting it drops the children; reading it joins theirs)
    get textContent() { return own + this.children.map(function (c) { return c.textContent; }).join(''); },
    set textContent(v) { own = String(v); this.children = []; },
    classList: { add: function (c) { classes.add(c); }, remove: function (c) { classes.delete(c); },
      contains: function (c) { return classes.has(c); },
      toggle: function (c, on) { if (on) classes.add(c); else classes.delete(c); } },
    appendChild: function (c) { this.children.push(c); } };
}

function run(c) {
  const root = el(), body = el();
  const document = { documentElement: root, body: body, createElement: el };
  const clock = { ms: 0 };
  const DateFake = { now: function () { return clock.ms; } };
  const audioCtx = { currentTime: 0, state: c.blocked ? 'suspended' : 'running' };
  const env = { playHead: 0, fits: 0 };
  // the slide engine (client/assets/ceremony-show.js), as a recorder: what the page's glue
  // tells it and when. No `engine` in the case = the engine never loaded (window has none).
  const slides = [];
  const window = c.engine ? { CeremonyShow: {
    show: function (slide, opts) { slides.push(['show', slide.template, !!(opts && opts.late)]); return c.engine !== 'refuses'; },
    progress: function (f) { slides.push(['progress', Math.round(f * 1000) / 1000]); },
    settle: function () { slides.push(['settle']); },
    clear: function () { slides.push(['clear']); } } } : {};
  let ticking = false;
  // the page's animation frames (only with `frames` in the case: without them the page
  // has no requestAnimationFrame, as in a hidden tab -- the 150 ms tick alone)
  let queued = [];
  const raf = c.frames ? function (fn) { queued.push(fn); return queued.length; } : undefined;
  const frame = function () { const due = queued; queued = []; due.forEach(function (fn) { fn(); }); return queued.length; };
  const setInterval = function () { ticking = true; return 1; };
  const clearInterval = function () { ticking = false; };
  const body_ = code + `
    var seen = [];
    function up() { return mirrorSubBox && mirrorSubBox.classList.contains('on') ? mirrorSubText.textContent : null; }
    function at(t) { audioCtx.currentTime = t; clock.ms = t * 1000; if (ticking()) mirrorSubStep(); seen.push([t, up()]); }
    steps.forEach(function (s) {
      if (s.mode !== undefined) mirrorShowMode(s.mode);
      if (s.show) mirrorSubShow(s.show);
      if (s.end) mirrorSubEnd(s.end);
      if (s.picture) mirrorSlidePicture();
      // the face's interrupted message on the mirror socket, then the shared handler's stopPlayback
      if (s.interrupt) { mirrorSubInterrupted(); playHead = audioCtx.currentTime; }
      if (s.head !== undefined) playHead = s.head;
      // a frame on the mirror socket at audio time t (sock.onmessage's mirrorSubFrame: the robot
      // still talking to this screen). A case without any = nothing arrived after its start.
      if (s.link !== undefined) { clock.ms = s.link * 1000; mirrorSubFrame(); }
      if (s.at !== undefined) at(s.at);
      // the wall clock at t, a tick -- and the audio clock left where it was (an AudioContext that stopped)
      if (s.wall !== undefined) { clock.ms = s.wall * 1000; if (ticking()) mirrorSubStep(); seen.push(['wall ' + s.wall, up()]); }
      // one animation frame at audio time t (no tick): what the slide is told between two ticks
      if (s.frame !== undefined) { audioCtx.currentTime = s.frame; clock.ms = s.frame * 1000; seen.push([s.frame, 'frames:' + frame()]); }
    });
    return { seen: seen, chunks: typeof chunksOf === 'string' ? mirrorSubChunks(chunksOf) : null,
             show: document.documentElement.classList.contains('mirror-show'), ticking: ticking(),
             scene: mirrorBackScene ? mirrorBackScene.textContent : null, fits: env.fits,
             slides: slides, slideUp: mirrorSlideUp,
             runs: mirrorSubText ? mirrorSubText.children.map(function (c) { return [c.className, c.textContent]; }) : null };
  `;
  // the page's own names the slice reads: playHead is a closure variable there
  const src = body_.replace(/\bplayHead\b/g, 'env.playHead');
  const fn = new Function('document', 'audioCtx', 'env', 'Date', 'setInterval', 'clearInterval', 'ticking',
    'mirrorFrame', 'fitMirrorStage', 'steps', 'chunksOf', 'clock', 'window', 'slides', 'requestAnimationFrame', 'frame', src);
  return fn(document, audioCtx, env, DateFake, setInterval, clearInterval, function () { return ticking; },
    c.frame ? {} : null, function () { env.fits++; }, c.steps || [], c.chunks, clock, window, slides, raf, frame);
}

const cases = JSON.parse(process.argv[2]);
const out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
process.stdout.write(JSON.stringify(out));
