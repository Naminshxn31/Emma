/* emma_dive -- Emma herself in the real pool: the punchline of the pool joke and the two lines
   after it. One shot, three lines (options.mode), joined without a cut:

     dive  s04_l11  "Swimming in the 155-metre pool myself!"
     laps  s04_l13  "The day I'm completely waterproof, I'd love to go and show off a few laps."
     edge  s04_l15  "With pleasure, Khun Miki!"

   Owner, 18 Nov 2026, on the plan with words: "ตรงนี้ทำแบบนี้ไม่สวยเลย เปลี่ยนเป็นทำเป็นใกล้เห็นตัวหุ่น
   แล้วโดดลงไปเลยได้ไหม". So: a close, eye-level view of the real pool, Emma herself (the approved
   3D model, rendered offline by scripts/render_emma_dive.py into transparent clips) hovering at
   its edge, large in the frame. On "Swimming" she turns to the water and bounces, ready; on
   "155-metre" the figure lights; on "pool" she crouches, leaps, tips head-first and plunges in
   -- the splash is drawn here on the real water -- and she pops up, head above the water,
   bobbing.
   Owner, the same afternoon, on the two lines after it (then an abstract night pool with a dot
   of light): "แก้ตรงนี้ด้วยคืออะไรไม่รู้ ทำให้มันต่อเนื่องกันกับอันก่อนหน้าก็ได้ โดยให้หุ่นว่ายไปมายังดีกว่าอีก".
   So the shot goes on: on "completely waterproof" she turns side-on and sets off; she swims
   lengths of the real water, there and back, a turn with a small splash at each end, the
   camera easing with her -- faster on "show off"; and on "With pleasure" she ducks under,
   comes up close to us facing the room, and waves.

   The clips are never the clock of WHEN things happen: each part starts on her word (a seek +
   play). Where she is on the water is this act's (the swim clip holds her in one place: the
   act moves, turns and scales it), on the wall clock -- so the line that takes over, built
   and held under this frame for a moment, is at the same place when it comes up.
   Every place on the screen is a number of the slide's options (the render script printed them:
   .tmp/ceremony-show/emma-dive/points.json, points_swim.json). */
CeremonyShow.register('emma_dive', function (ctx) {
  var fx = ctx.fx, late = ctx.late;
  var mode = ctx.opt('mode', 'dive');
  // what the line before handed on (the same shot going on): its camera, where she is
  var from = ctx.prev && ctx.prev.act === 'emma_dive' && ctx.prev.state && typeof ctx.prev.state === 'object' ? ctx.prev.state : null;
  ctx.bg('off');                                         // a full-bleed picture
  ctx.root.classList.add('m-' + (mode === 'laps' || mode === 'edge' ? mode : 'dive'));

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function lerp(a, b, k) { return a + (b - a) * k; }
  function num(v, d) { return typeof v === 'number' && isFinite(v) ? v : d; }
  // a cut clip at its window of the box (in % of the box: the same place at any stage size)
  function windowed(el, win) {
    if (!win || win.length !== 6 || !win.every(function (v) { return typeof v === 'number' && isFinite(v); })
        || !(win[2] > 0 && win[3] > 0 && win[4] > 0 && win[5] > 0)) return;
    var pc = function (v, of) { return (Math.round(v / of * 1e6) / 1e4) + '%'; };
    el.classList.add('cwin');
    el.style.left = pc(win[0], win[4]);
    el.style.top = pc(win[1], win[5]);
    el.style.width = pc(win[2], win[4]);
    el.style.height = pc(win[3], win[5]);
  }
  function wall() { return Date.now(); }                 // (a reading, not a timer: the laps' own clock)
  // a CSS curve as a function (the camera is moved by hand here, so the line that takes over
  // can be told exactly where it is)
  function bez(x1, y1, x2, y2) {
    return function (u) {
      if (u <= 0) return 0;
      if (u >= 1) return 1;
      var lo = 0, hi = 1, s = u, i, x;
      for (i = 0; i < 22; i++) {
        s = (lo + hi) / 2;
        x = 3 * (1 - s) * (1 - s) * s * x1 + 3 * (1 - s) * s * s * x2 + s * s * s;
        if (x < u) lo = s; else hi = s;
      }
      return 3 * (1 - s) * (1 - s) * s * y1 + 3 * (1 - s) * s * s * y2 + s * s * s;
    };
  }
  var PUSH = bez(0.3, 0, 0.1, 1), INOUT = bez(0.65, 0, 0.35, 1);
  var ARRIVE = bez(0.25, 0.46, 0.45, 0.94);              // at speed from its first frame, slowing all the way

  // the dive clip's parts, in clip seconds (scripts/emma_dive/page.html)
  var T = {
    readyA: ctx.opt('clip.ready_a', 0.6), readyB: ctx.opt('clip.ready_b', 1.6),
    dive: ctx.opt('clip.dive', 1.6), splash: ctx.opt('clip.splash', 2.5),
    rise: ctx.opt('clip.rise', 3.7), bobA: ctx.opt('clip.bob_a', 4.4), bobB: ctx.opt('clip.bob_b', 6.4)
  };
  // the swim clip's parts (its first two seconds are the dive clip's bob, pose for pose)
  var TS = {
    bobA: ctx.opt('swim.bob_a', 0), bobB: ctx.opt('swim.bob_b', 2), turn: ctx.opt('swim.turn', 2),
    strokeA: ctx.opt('swim.stroke_a', 2.6), strokeB: ctx.opt('swim.stroke_b', 5), duck: ctx.opt('swim.duck', 5),
    under: ctx.opt('swim.under', 5.3), rise: ctx.opt('swim.rise', 5.8), happyA: ctx.opt('swim.happy_a', 6.5),
    happyB: ctx.opt('swim.happy_b', 7.9)
  };
  var cu = function (path, fallback) { return 'calc(' + ctx.opt(path, fallback) + ' * var(--cu))'; };
  var C = function (n) { return 'calc(' + (Math.round(n * 1000) / 1000) + ' * var(--cu))'; };

  // ---- the world: the picture, the water's own light, Emma, the splash -- one camera group ----
  var scene = ctx.el('scene');
  var world = ctx.el('world');
  var pic = ctx.picture('pool', { fit: 'cover' });
  world.style.left = cu('picture.x', -11.35);
  world.style.top = cu('picture.y', -32.24);
  world.style.width = cu('picture.w', 140);
  world.style.height = cu('picture.h', 87.5);
  ctx.add(world, pic ? pic.el : null);

  // the water's shimmer: slow light streaks lying on the real water (graphics, no scenery)
  var glints = ctx.el('glints');
  (ctx.opt('glints', []) || []).forEach(function (g, i) {
    var s = ctx.el('glint');
    s.style.left = 'calc(' + g[0] + ' * var(--cu))';
    s.style.top = 'calc(' + g[1] + ' * var(--cu))';
    s.style.width = 'calc(' + g[2] + ' * var(--cu))';
    s.style.setProperty('--d', String((i * 0.73) % 3.1));
    ctx.add(glints, s);
  });
  // (the shot goes on from the line before: its shimmer and the rings round her go on where
  // they were -- the loops start as old as they were there)
  var born = wall(), age0 = from ? num(from.age, 0) : 0, since0 = from && from.up ? num(from.since, 0) : 0, upAt = from && from.up ? born : 0;
  ctx.root.style.setProperty('--age', String(Math.round(age0 * 100) / 100));
  ctx.root.style.setProperty('--since', String(Math.round(since0 * 100) / 100));
  function age() { return age0 + (wall() - born) / 1000; }
  function since() { return upAt ? since0 + (wall() - upAt) / 1000 : 0; }

  // where her hands meet the water, and where she comes up
  var sx = ctx.opt('splash.x', 38.96), sy = ctx.opt('splash.y', 37.75);
  var rx = ctx.opt('rise.x', 39.77), ry = ctx.opt('rise.y', 38.8);
  var at = function (cls, x, y) {
    var el = ctx.el(cls);
    el.style.left = 'calc(' + x + ' * var(--cu))';
    el.style.top = 'calc(' + y + ' * var(--cu))';
    return el;
  };
  var jetsOf = function (parent, list) {
    (list || []).forEach(function (j) {
      var jet = ctx.el('jet');
      jet.style.setProperty('--a', String(j[0]));         // lean, degrees
      jet.style.setProperty('--h', String(j[1]));         // height, cu
      jet.style.setProperty('--w', String(j[2]));         // width, cu
      jet.style.setProperty('--d', String(j[3] || 0));    // delay, s
      ctx.add(parent, jet);
    });
  };

  // the entry splash of the dive (rings, crown, foam): only the line she dives in
  var rings = null, crown = null, foam = null;
  if (mode === 'dive') {
    // rings on the water (ellipses: the water seen at this angle -- the render's own ratio)
    rings = at('rings', sx, sy);
    rings.style.setProperty('--flat', String(ctx.opt('splash.flat', 0.16)));
    for (var r = 0; r < 4; r++) {
      var ring = ctx.el('ring');
      ring.style.setProperty('--i', String(r));
      ctx.add(rings, ring);
    }
    // the crown of the splash: jets fanned round the entry, and the drops thrown out of it
    crown = at('crown', sx, sy);
    jetsOf(crown, ctx.opt('jets', []));
    (ctx.opt('drops', []) || []).forEach(function (d) {
      var arc = ctx.el('drop-x'), fall = ctx.el('drop-y'), dot = ctx.el('drop');
      arc.style.setProperty('--dx', String(d[0]));          // how far out, cu
      fall.style.setProperty('--up', String(d[1]));         // how high, cu
      dot.style.setProperty('--s', String(d[2]));           // size, cu
      arc.style.setProperty('--t', String(d[3]));           // flight time, s
      fall.style.setProperty('--t', String(d[3]));
      // follow-through: where each drop comes down, a small ring opens on the water
      var plink = ctx.el('plink');
      plink.style.setProperty('--t', String(d[3]));
      ctx.add(crown, ctx.add(arc, ctx.add(fall, dot), plink));
    });
    foam = at('foam', sx, sy);
    [rings, crown, foam].forEach(function (el) { el.style.setProperty('--k', String(ctx.opt('splash.size', 1))); });
  }

  // ---- the rig: everything that is where she is on the water. It is moved, scaled (nearer =
  // larger) and turned as one; `turn` holds what faces the way she swims ----
  var rig = ctx.el('rig'), turn = ctx.el('turn');
  [rig, turn].forEach(function (el) { el.style.transformOrigin = C(rx) + ' ' + C(ry); });
  // the water round her when she is up: a soft ring that breathes with her bob
  var bob = T.bobB - T.bobA;
  var rest = at('rest', rx, ry);
  rest.style.setProperty('--flat', String(ctx.opt('rise.flat', 0.17)));
  rest.style.setProperty('--bob', String(bob));
  for (var q = 0; q < 3; q++) {
    var rr = ctx.el('rest-ring');
    rr.style.setProperty('--i', String(q));
    ctx.add(rest, rr);
  }
  // the water's collar ON her (in front of the clip): hides the clip's flat cut at the surface
  var lid = at('lid', rx, ry);
  lid.style.setProperty('--bob', String(bob));
  ctx.add(lid, ctx.el('collar'));
  // secondary action when she pops up: a small splash thrown out to both sides (never over her face)
  jetsOf(lid, ctx.opt('pop', []));
  [rest, lid].forEach(function (el) { el.style.setProperty('--k', String(ctx.opt('rise.size', 1))); });
  // her wake: the water she leaves behind as she swims (behind her: the clip faces left) and the
  // wave she pushes ahead -- both as strong as her speed; and the splashes of her paddling, one
  // hand after the other, whenever she strokes (also while she paddles on the spot, getting ready)
  var wake = at('wake', rx, ry);
  ctx.add(wake, ctx.el('wake-a'), ctx.el('wake-b'), ctx.el('wake-c'), ctx.el('bow'));
  var strokes = at('strokes', rx, ry);
  ctx.add(strokes, ctx.el('paddle'), ctx.el('paddle paddle2'));

  // Emma: the transparent clip, the size of the stage, in the same group as the water. The
  // dive line has the dive clip; the lines after it the swim clip (the dive line names that
  // one too, unseen: it is in the browser before the next line asks for it).
  var emma = ctx.el('emma');
  var clip = ctx.video('emma');
  ctx.add(emma, clip ? clip.el : null);
  // (23 Nov night, the event notebook: Chrome decodes VP9 with alpha on the CPU, and a clip of
  // the whole 1920x1080 frame cost it every pixel of a frame she is only ever in part of -- 43%
  // of the dive's frames dropped where it began. The clips are cut to that part: options.window
  // = [x, y, w, h, W, H], the file is the w x h window at (x, y) of the W x H frame this box
  // stands for, laid exactly there. No window = a clip of the whole frame, filling the box.)
  if (clip) windowed(clip.el, ctx.opt('window', null));
  if (mode === 'dive' && ctx.has('swim')) {
    var ahead = ctx.video('swim');
    ctx.add(scene, ctx.add(ctx.el('ahead'), ahead ? ahead.el : null));
  }
  ctx.add(turn, wake, emma, strokes);
  ctx.add(rig, rest, turn, lid);
  // a turn at each end of a length, and where she ducks under: a small splash on the water
  var LEFT = ctx.opt('laps.left', 14), RIGHT = ctx.opt('laps.right', 49);
  var ends = {};
  if (mode !== 'dive') {
    [['l', LEFT - 2.2], ['r', RIGHT + 2.2], ['d', rx]].forEach(function (e) {
      var s = at('tsplash', e[1], ry);
      jetsOf(s, ctx.opt('turn_jets', []));
      ctx.add(s, ctx.el('tring'), ctx.el('tring tring2'));
      ends[e[0]] = s;
    });
  }
  // her way under the water to the near edge: a line of light under the surface
  var trail = ctx.add(ctx.el('trail'), ctx.el('trail-in'));
  ctx.add(scene, world, glints, rings, foam, trail, rig, ends.l, ends.r, ends.d, crown);

  // ---- the camera: moved by hand (one tween at a time, or easing after her), so its place
  // is known at every moment and can be handed on ----
  var cam = fx.camera(scene);
  var V = { x: 50, y: 50, scale: 1 }, tween = null, follow = null;
  var HOME = { x: ctx.opt('push.x', 42), y: ctx.opt('push.y', 62), scale: ctx.opt('push.scale', 1.06) };
  function hold(v) {
    // (never past the picture's left edge, never as far right as the person in the picture)
    v.scale = clamp(v.scale, 1, 1.4);
    v.x = clamp(v.x, 50 / v.scale + 0.1, ctx.opt('cam.max_x', 52));
    return v;
  }
  function camSet(v) { V = hold({ x: v.x, y: v.y, scale: v.scale }); cam.set(V); }
  function camTo(to, ms, ease, done) {
    tween = { a: { x: V.x, y: V.y, scale: V.scale }, b: to, t0: wall(), ms: Math.max(1, ms), ease: ease, done: done || null };
  }
  function camStep(now, dt) {
    if (tween) {
      var u = clamp((now - tween.t0) / tween.ms, 0, 1), e = tween.ease(u), tw = tween;
      camSet({ x: lerp(tw.a.x, tw.b.x, e), y: lerp(tw.a.y, tw.b.y, e), scale: lerp(tw.a.scale, tw.b.scale, e) });
      if (u >= 1) { tween = null; if (tw.done) tw.done(); }
    } else if (follow) {
      var want = follow(), k = 1 - Math.exp(-dt / 520);
      camSet({ x: lerp(V.x, want.x, k), y: lerp(V.y, want.y, k), scale: lerp(V.scale, want.scale, 1 - Math.exp(-dt / 1400)) });
    }
  }
  camSet(from && from.view ? { x: num(from.view.x, HOME.x), y: num(from.view.y, HOME.y), scale: num(from.view.scale, HOME.scale) }
    : (mode === 'dive' ? { x: 50, y: 50, scale: 1 } : HOME));

  // ---- words: the film's face (the core's), top left over the buildings ----
  var shade = ctx.el('shade', 'len');
  var fig = ctx.has('len') ? ctx.figure('len', { glow: true, cls: 'len', reveal: 'pop' }) : null;      // (the punchline's number: Playful)
  var figBox = ctx.el('figbox');
  ctx.add(figBox, fig);
  // the lines after the dive: one short phrase at a time, in one place (each waits until the
  // one before has cooled)
  var cap = ctx.el('cap'), phrases = {}, said = null;
  function phrase(key, cls, o) {
    var t = ctx.text(key, { cls: cls, glow: 'shade', reveal: o && o.pop ? 'pop' : 'light' });
    if (!t) return null;
    var w = ctx.el('phrase' + (o && o.wait ? ' cs-whipped' : '') + (o && o.row2 ? ' row2' : ''));
    ctx.add(cap, ctx.add(w, t));
    phrases[key] = { box: w, text: t, keep: !!(o && o.row2) };
    return t;
  }
  function say(key, isLate) {
    var p = phrases[key];
    if (!p || said === key) return;
    var before = said ? phrases[said] : null;
    said = key;
    // (a second row goes on under the phrase above it: "I'd love to show off / a few laps" --
    // the first half used to be gone 0.6 s after it lit, too soon to be read; pacing pass)
    if (before && !p.keep) fx.depart(before.text);
    if (isLate || !before) p.box.classList.remove('cs-whipped');
    else ctx.after(220, function () { p.box.classList.remove('cs-whipped'); });
  }
  if (mode === 'laps') {
    phrase('t_wp', 'cs-type-h1', { wait: true });
    phrase('t_love', 'cs-type-h1', { wait: true });
    phrase('t_laps', 'cs-type-h1', { wait: true, row2: true });
  } else if (mode === 'edge') {
    ctx.add(cap, ctx.text('t_giant', { cls: 'cs-type-h1', glow: 'shade' }),
      ctx.text('t_khun', { cls: 'cs-type-h2 khun', glow: 'shade', reveal: 'pop' }));
    cap.classList.add('stack');
  }
  // (the pool runs under the subtitle's box: its lowest part is graded down a little)
  ctx.add(ctx.stage, scene, ctx.el('grade'), shade, figBox, cap);

  // =====================================================================================
  // dive (s04_l11)
  // =====================================================================================
  var part = 'still', splashed = false;
  var start = function (sec) {
    if (!clip) return;
    clip.seek(sec);
    clip.play();
  };
  var surfaced = function () {
    if (upAt) return;
    upAt = wall();
    ctx.root.classList.add('up');
  };
  var hooks = { beat: function () {}, settle: function () {}, frame: function () {}, handover: function () { return null; } };

  function build_dive() {
    if (clip) { clip.pause(); clip.seek(0); }
    // the shot is already coming in on the pool as it arrives (her dive takes the camera over)
    if (!late) camTo({ x: 50.1, y: 50.8, scale: 1.035 }, 7000, ARRIVE);
    var splash = function (isLate) {
      if (splashed) return;
      splashed = true;
      ctx.root.classList.add(isLate ? 'calm' : 'splash');
    };
    // ambient: once the push has landed, the camera keeps breathing in, slow and eased both ends
    var drift = function () {
      camTo({ x: V.x, y: V.y, scale: ctx.opt('drift.scale', 1.09) }, ctx.opt('drift.ms', 16000), INOUT);
    };
    var dive = function (isLate) {
      part = 'dive';
      if (isLate) {                                          // straight to the end state: up and bobbing
        start(T.bobA);
        splash(true);
        ctx.root.classList.add('joined');                    // (no pop splash for a state that is simply there)
        surfaced();
        tween = null;
        camSet(HOME);
        drift();
        return;
      }
      start(T.dive);
      camTo(HOME, ctx.opt('push.ms', 2600), PUSH, drift);
      // a clip that cannot play (no decoder, no file): the water still breaks, where it would have
      var lag = (T.splash - T.dive) * 1000;
      ctx.after(lag + 700, function () { if (!splashed && !(clip && clip.ready())) splash(false); });
      ctx.after(lag + 1500, function () { if (!(clip && clip.ready())) surfaced(); });
    };
    // the clip's own time decides the loops and the moment the water breaks
    hooks.frame = function () {
      if (!clip) return;
      var t = clip.time();
      if (part === 'ready' && t >= T.readyB - 0.02) clip.seek(T.readyA);
      if (part === 'dive') {
        if (!splashed && t >= T.splash) splash(false);
        if (t >= T.rise) surfaced();
        if (t >= T.bobB - 0.04 || clip.ended()) { clip.seek(T.bobA); clip.play(); }
      }
    };
    hooks.beat = function (b, isLate) {
      if (b.do === 'ready' && part === 'still') {
        part = 'ready';
        start(isLate ? T.readyA : 0);
      } else if (b.do === 'figure') {
        shade.classList.add('in');
      } else if (b.do === 'dive' && part !== 'dive') {
        dive(isLate);
      }
    };
    hooks.settle = function () {
      if (part !== 'dive') dive(true);
      shade.classList.add('in');
    };
    // what the next line (her laps) starts from: the camera, and where her bob is
    hooks.handover = function () {
      var t = clip ? clip.time() : T.bobA, bobbing = !!upAt && part === 'dive' && t >= T.bobA - 0.05;
      return { mode: 'laps', view: { x: V.x, y: V.y, scale: V.scale }, up: bobbing,
               bob: bobbing ? clamp((t - T.bobA) / (T.bobB - T.bobA), 0, 1) : 0, at: wall(), age: age(), since: since() };
    };
  }

  // =====================================================================================
  // laps (s04_l13) and edge (s04_l15): she swims
  // =====================================================================================
  // Where she is: `x` along the water at her own depth (a length runs LEFT..RIGHT), `dir` the
  // way she swims (-1 = left, the way the clip faces). Everything is a function of the wall
  // clock and what is kept here, so the next line can go on from it.
  var EASY = ctx.opt('laps.easy', 12), FAST = ctx.opt('laps.fast', 19), TURN = ctx.opt('laps.turn_ms', 560);
  var COIL = ctx.opt('laps.coil_ms', 280), PADDLE = ctx.opt('laps.paddle', 1.6), PUSH_MS = ctx.opt('laps.push_ms', 320);
  var NEAR = { x: ctx.opt('edge.x', 36), y: ctx.opt('edge.y', 41.5), k: ctx.opt('edge.k', 1.3) };
  var sw = { phase: 'bob', x: rx, dir: -1, a: rx, b: LEFT, t0: 0, ms: 1, speed: EASY, face: 1, seen: rx, wake: 0, epoch: 0, y: ry, k: 1,
             x0: rx, pushing: false, stretched: false };
  // a length: she pushes off hard and glides into the far end (never constant speed)
  function glide(u) { return 1 - Math.pow(1 - clamp(u, 0, 1), 2.3); }
  function smooth(u) { u = clamp(u, 0, 1); return u * u * (3 - 2 * u); }
  function place() {
    rig.style.transform = 'translate3d(' + C(sw.x - rx) + ', ' + C(sw.y - ry) + ', 0) scale(' + (Math.round(sw.k * 1000) / 1000) + ')';
  }
  function facing(fx_, fy) {
    turn.style.transform = 'scale(' + (Math.round(fx_ * 1000) / 1000) + ', ' + (Math.round(fy * 1000) / 1000) + ')';
  }
  // Playful squash and stretch along the way she swims (motion identity: the joke's beats):
  // c > 0 = gathered (shorter, taller: coiled against the wall), c < 0 = stretched out (the push)
  function body(sign, c) {
    facing(sign * (c >= 0 ? 1 - 0.16 * c : 1 - 0.13 * c), c >= 0 ? 1 + 0.08 * c : 1 + 0.07 * c);
  }
  function strokeAt(now) {                                // the stroke loop's clip time at this moment
    var span = TS.strokeB - TS.strokeA;
    return TS.strokeA + ((((now - sw.epoch) / 1000) % span) + span) % span;
  }
  function length(now, fromX, dir, speed) {
    sw.phase = 'length';
    sw.dir = dir;
    sw.a = fromX;
    sw.b = dir < 0 ? LEFT : RIGHT;
    sw.speed = speed;
    sw.t0 = now;
    // (the glide's mean speed is `speed`; a length is never shorter than a breath)
    sw.ms = Math.max(1100, Math.abs(sw.b - sw.a) / speed * 1000);
    sw.face = dir < 0 ? 1 : -1;
    sw.stretched = true;                                  // (it leaves the wall stretched out: the push)
    body(sw.face, -1);
  }
  function setOff(now) {
    // "completely waterproof": she dips (the clip's own anticipation), turns side-on and starts
    // to paddle -- getting ready, edging along; the push-off waits for "show off"
    sw.phase = 'setoff';
    sw.t0 = now;
    sw.epoch = now + (TS.strokeA - TS.turn) * 1000;
    start(TS.turn);
    ctx.root.classList.add('stroking');
  }
  function coil(now) {
    // the anticipation before the first push-off: she draws back a little, gathered, and goes
    sw.phase = 'coil';
    sw.t0 = now;
    sw.x0 = sw.x;
    ctx.root.classList.add('stroking');
  }
  function splashAt(el) {
    if (!el) return;
    el.classList.remove('go');
    ctx.after(30, function () { el.classList.add('go'); });
  }
  function swimStep(now, dt) {
    var u, k, c;
    if (sw.phase === 'setoff') {
      if (now - sw.t0 >= (TS.strokeA - TS.turn) * 1000) {
        if (sw.pushing) coil(now); else { sw.phase = 'paddle'; sw.t0 = now; }
      }
    } else if (sw.phase === 'paddle') {
      // paddling on the spot, edging along the way she faces (slow, easing in -- never to the wall)
      u = smooth((now - sw.t0) / 900);
      if (sw.x > LEFT + 8) sw.x -= PADDLE * u * dt / 1000;
      if (sw.pushing) coil(now);
    } else if (sw.phase === 'coil') {
      // gathered and drawn back (decelerating), then released stretched into the push
      k = clamp((now - sw.t0) / COIL, 0, 1);
      c = k < 0.7 ? smooth(k / 0.7) : 1 - 2 * smooth((k - 0.7) / 0.3);
      sw.x = sw.x0 + 0.9 * smooth(k / 0.7);              // (back: she faces left, so behind her is +x)
      body(1, c);
      if (k >= 1) length(now, sw.x, -1, sw.speed);
    } else if (sw.phase === 'length') {
      u = (now - sw.t0) / sw.ms;
      sw.x = lerp(sw.a, sw.b, glide(u));
      if (sw.stretched) {                                 // the push's stretch eases out into the glide
        var ps = clamp(1 - (now - sw.t0) / PUSH_MS, 0, 1);
        body(sw.face, -ps * ps);
        if (ps <= 0) sw.stretched = false;
      }
      if (u >= 1) { sw.phase = 'turn'; sw.t0 = now; splashAt(sw.dir < 0 ? ends.l : ends.r); }
    } else if (sw.phase === 'turn') {
      // the turn: she runs into the wall and gathers (a squash, held), swings round -- fast
      // through the middle, under the splash: edge-on for a frame, not five -- and, still
      // coiled, springs off the other way stretched out (the anticipation of the next push)
      k = clamp((now - sw.t0) / TURN, 0, 1);
      sw.x = sw.b + sw.dir * 0.7 * Math.sin(Math.PI * Math.min(1, k / 0.82));
      c = k < 0.35 ? smooth(k / 0.35) : (k < 0.82 ? 1 : 1 - 2 * smooth((k - 0.82) / 0.18));
      body(-sw.face * Math.tanh((k - 0.5) * 11) / Math.tanh(5.5), c);
      if (k >= 1) length(now, sw.b, -sw.dir, sw.speed);
    }
    // the stroke loop of the clip (one seek each time round)
    if (clip && (sw.phase === 'length' || sw.phase === 'turn' || sw.phase === 'paddle' || sw.phase === 'coil')) {
      var t = clip.time();
      if (t >= TS.strokeB - 0.05 || t < TS.strokeA - 0.3 || clip.ended()) { sw.epoch = now; clip.seek(TS.strokeA); clip.play(); }
    } else if (clip && sw.phase === 'bob') {
      if (clip.time() >= TS.bobB - 0.04 || clip.ended()) { clip.seek(TS.bobA); clip.play(); }
    }
    // her wake follows her speed (it lags a little: the water takes a moment)
    var speed = dt > 0 ? Math.abs(sw.x - sw.seen) / dt * 1000 : 0;
    sw.seen = sw.x;
    sw.wake = lerp(sw.wake, sw.phase === 'length' || sw.phase === 'paddle' ? clamp(speed / 15, 0, 1) : 0, 1 - Math.exp(-dt / 260));
    wake.style.transform = 'scale(' + (Math.round((0.25 + 0.75 * sw.wake) * 1000) / 1000) + ', 1)';
    wake.style.opacity = String(Math.round(sw.wake * 100) / 100);
    place();
  }
  // the camera eases after her (the pool stays the shot: a few cu each way, held clear of the
  // picture's left edge and of the person at its right by hold())
  function after() {
    return { x: HOME.x + clamp((sw.x - rx) * ctx.opt('laps.follow', 0.22), -5, 3), y: HOME.y, scale: ctx.opt('laps.scale', 1.12) };
  }
  function swimming() {
    return { x: sw.x, dir: sw.dir, phase: sw.phase, a: sw.a, b: sw.b, t0: sw.t0, ms: sw.ms, speed: sw.speed, epoch: sw.epoch };
  }
  // go on from what the line before handed on (or, with nothing handed: mid-length, at ease)
  function resume(st, now) {
    ctx.root.classList.add('stroking');
    if (st && (st.phase === 'length' || st.phase === 'turn') && typeof st.t0 === 'number') {
      sw.phase = st.phase; sw.dir = st.dir < 0 ? -1 : 1; sw.a = num(st.a, rx); sw.b = num(st.b, LEFT);
      sw.t0 = st.t0; sw.ms = Math.max(1, num(st.ms, 2000)); sw.speed = num(st.speed, EASY); sw.epoch = num(st.epoch, now);
      sw.x = num(st.x, rx); sw.face = sw.dir < 0 ? 1 : -1;
      facing(sw.face, 1);
    } else if (st && (st.phase === 'paddle' || st.phase === 'coil' || st.phase === 'setoff')) {
      length(now, num(st.x, rx), -1, num(st.speed, EASY));    // (handed on before her push: she is away from there)
      sw.epoch = num(st.epoch, now);
    } else {
      length(now - 900, RIGHT - 6, -1, EASY);
      sw.epoch = now;
    }
    sw.seen = sw.x;
    if (clip) { clip.seek(strokeAt(now)); clip.play(); }
  }

  function build_laps() {
    ctx.root.classList.add('up');
    ctx.root.classList.add('joined');
    if (!upAt) upAt = born;
    var now = wall(), going = false;
    if (clip) {
      // the bob she was in, at the moment this line comes up (the clip's first two seconds)
      var p = from && from.up ? (num(from.bob, 0) + (now - num(from.at, now)) / 1000 / (TS.bobB - TS.bobA)) % 1 : 0;
      clip.seek(TS.bobA + p * (TS.bobB - TS.bobA));
      clip.play();
    }
    // (from the joined frame the camera is already easing in; once she moves it eases after her)
    follow = function () { return sw.phase !== 'bob' ? after() : { x: V.x, y: V.y, scale: lerp(V.scale, ctx.opt('laps.scale', 1.12), 0.4) }; };
    // "completely waterproof": she turns side-on and paddles on the spot, getting ready
    var ready = function (isLate) {
      if (sw.phase !== 'bob') return;
      if (isLate) { going = true; resume(null, wall()); return; }
      setOff(wall());
    };
    // "show off": the push-off -- gathered, drawn back, and away at speed (Playful anticipation)
    var off = function (isLate, speed) {
      if (going) return;
      going = true;
      sw.speed = speed;
      if (isLate) { resume(null, wall()); return; }
      sw.pushing = true;
      if (sw.phase === 'bob') setOff(wall());               // (it was never readied: the turn first, then the push)
    };
    hooks.frame = swimStep;
    hooks.beat = function (b, isLate) {
      if (b.do === 'off') {
        say('t_wp', isLate);
        ready(isLate);
      } else if (b.do === 'show') {
        say('t_love', isLate);
        off(isLate, FAST);
      } else if (b.do === 'laps') {
        say('t_laps', isLate);
        off(isLate, FAST);
        sw.speed = FAST;
      }
    };
    hooks.settle = function () {
      say('t_laps', true);
      off(false, EASY);                                     // (never pushed yet: she goes now, from where she is)
      sw.speed = EASY;                                      // the line is over: she swims on, at ease
    };
    hooks.handover = function () {
      var st = swimming();
      st.mode = 'edge';
      st.view = { x: V.x, y: V.y, scale: V.scale };
      st.at = wall(); st.age = age(); st.since = since(); st.up = true; st.going = going || sw.phase !== 'bob';
      return st;
    };
  }

  function build_edge() {
    ctx.root.classList.add('up');
    ctx.root.classList.add('joined');
    if (!upAt) upAt = born;
    var now = wall(), step = 'swim', t0 = 0, moved = false;
    follow = function () {
      return step === 'swim' || step === 'duck' ? after()
        : { x: HOME.x + ctx.opt('edge.cam_x', -1), y: HOME.y + ctx.opt('edge.cam_y', 1.5), scale: ctx.opt('edge.scale', 1.14) };
    };
    var landed = function () {                             // where she comes up: near us, facing the room
      moved = true;
      sw.phase = 'up';
      sw.x = NEAR.x; sw.y = NEAR.y; sw.k = NEAR.k;
      sw.wake = 0;
      wake.style.opacity = '0';
      ctx.root.classList.remove('stroking');
      facing(1, 1);
      place();
    };
    var end = function () {                                 // the calm end frame: up, near, bobbing
      step = 'up';
      landed();
      ctx.root.classList.remove('under');
      ctx.root.classList.add('near');
      if (clip) { clip.seek(TS.bobA); clip.play(); }
    };
    if (late) end(); else if (from && from.going === false) { sw.phase = 'bob'; if (clip) { clip.seek(TS.bobA); clip.play(); } }
    else resume(from, now);
    var duck = function () {
      if (step !== 'swim') return;
      step = 'duck';
      t0 = wall();
      sw.phase = 'duck';
      sw.wake = 0;
      wake.style.opacity = '0';
      ctx.root.classList.remove('stroking');
      body(sw.face, 0);                                     // (a duck from mid-turn: whole again, facing her way)
      // where she goes under: a small splash, and the line of light of her way to the near edge
      var d = ends.d;
      if (d) { d.style.left = C(sw.x); splashAt(d); }
      var dx = NEAR.x - sw.x, dy = NEAR.y - sw.y, len = Math.hypot(dx, dy);
      trail.style.width = C(len);
      trail.style.transform = 'translate3d(' + C(sw.x) + ', ' + C(sw.y) + ', 0) rotate(' + (Math.round(Math.atan2(dy, dx) * 18000 / Math.PI) / 100) + 'deg)';
      if (clip) { clip.seek(TS.duck); clip.play(); }
    };
    hooks.frame = function (nowMs, dt) {
      if (step === 'swim') { swimStep(nowMs, dt); return; }
      var el = nowMs - t0;
      if (step === 'duck') {
        place();
        if (el >= (TS.under - TS.duck) * 1000) { step = 'under'; ctx.root.classList.add('under'); trail.classList.add('go'); }
      } else if (step === 'under') {
        if (!moved && el >= (TS.under - TS.duck) * 1000 + 240) landed();
        if (el >= (TS.rise - TS.duck) * 1000) {
          step = 'pop';
          if (!moved) landed();
          ctx.root.classList.remove('under');
          ctx.root.classList.remove('joined');              // (this pop has its splash)
          ctx.root.classList.add('near');
          // the water breaks where she comes up (the same small splash, at the near edge's size)
          if (ends.d) {
            ends.d.style.left = C(NEAR.x);
            ends.d.style.top = C(NEAR.y);
            ends.d.style.setProperty('--k', String(NEAR.k));
            splashAt(ends.d);
          }
        }
      } else if (step === 'pop' || step === 'up') {
        if (step === 'pop' && el >= (TS.happyB - TS.duck) * 1000) step = 'up';
        // after her wave the clip goes round its bob (the same pose it ends on)
        if (clip && (clip.ended() || clip.time() >= TS.happyB - 0.04 || (step === 'up' && clip.time() >= TS.bobB - 0.04 && clip.time() < TS.duck))) {
          clip.seek(TS.bobA);
          clip.play();
        }
      }
    };
    hooks.beat = function (b, isLate) {
      if (b.do === 'pleasure') {
        if (isLate) end(); else duck();
      } else if (b.do === 'name' && isLate && step === 'swim') {
        end();
      }
    };
    hooks.settle = function () { if (step === 'swim') end(); };
    hooks.handover = function () { return null; };
  }

  if (mode === 'laps') build_laps();
  else if (mode === 'edge') build_edge();
  else build_dive();
  place();

  var last = 0;
  ctx.frame(function () {
    var now = wall(), dt = last ? Math.min(100, now - last) : 16;
    last = now;
    hooks.frame(now, dt);
    camStep(now, dt);
  });

  return {
    beat: function (b, isLate) { hooks.beat(b, isLate); },
    settle: function () { hooks.settle(); },
    clear: function () { if (clip) clip.pause(); },
    handover: function () { return hooks.handover(); }
  };
});
