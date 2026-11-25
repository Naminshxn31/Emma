/* Part "units_views" of the ceremony film: the unit types, and the question of the view.
   Three cues, one act (options.mode): `units` (1,066 units: studios, 1-bedroom, 2-bedroom),
   `views` (what kind of view do you like?), `suggest` (a 2-bedroom unit with a view of the sea).

   One small 3D world: a perspective box, a camera (x, y, z, yaw) that glides between things
   standing on a dark floor, and objects placed by (x, y, z, turn, tilt, scale) in cu. Every
   object is a flat layer placed by one transform (no preserve-3d; a model photographed on black
   is keyed to alpha by the core -- ctx.picture(key, {keyed: 'black'}) -- so it stands on the
   world's own dark ground with no blend mode). Only transform and opacity move.

   Motion pass, 18 Nov (owner: "เอาสกิลที่ได้มาไปปรับของเราให้ดียิ่งขึ้น"; the film's motion identity:
   Premium, no overshoot, signature curve (0.4, 0, 0.2, 1), entrances decelerate, exits accelerate
   and are shorter, staggers within 500 ms, one hero per moment):
   - the figure lands with an anticipation (the camera draws a breath back, the court dims) and a
     follow-through (its words trail the numeral, the halo blooms after it, the core's line of
     light crosses it once it stands), and it LIFTS away upward as the header comes up into place;
   - the unit cards rise on arcs, the hero first, the others 80 ms apart; the card being named is
     the hero, the rest step back into the dark;
   - wireframe -> model is one scan line: the model above the cut, the wire below it, the same
     line (the wire used to fade out on its own clock and was gone before the scan reached it);
   - the camera between the windows travels on an arc (it draws back a little half-way), a window
     opens with its curtains a beat apart and its light blooming after them;
   - the words at the top sit on a soft dark plate, so the world's ring and the holograms' lines
     never run through them (review 18 Nov: the ring crossed TOTAL; UNITS IN TOTAL sat on lines);
   - ambient: the holograms breathe slowly, each on its own period; the camera never stops.

   Stall pass, 18 Nov afternoon (owner on the scene videos: "เหมือนบางช่วงกระตุก ค้างด้วย"):
   - the hitch as s04_l03 and s05_l06 came up was the page keying this act's pictures on black
     all at once -- twelve canvas reads in a row on the main thread, 0.5 s in which this
     world's camera (a frame loop) stood still right in the cut. A keyed picture is now made
     when its turn comes (`soon`), one at a time, after the cut and well before its beat;
   - a held shot is never a still: the camera's drift is slow but SEEN (it was a 40-second
     sine, a few pixels), a card waiting for the next type floats and a light crosses its
     glass, the view in an open window drifts, the floor's light breathes;
   - a shot arrives already moving (the suggest line used to open on a standing camera).
   Type and full screen, the same afternoon (Apple's MacBook Pro film as the reference): the
   film's one face, upright, sentence case; "1,066 units in total" is ONE phrase that lights
   up in the middle of the court and then goes up to the top as the header (it was a 36cu
   monument and a second copy at the top); the title and the kicker are plain phrases the
   core lights up (no typing, no caret); the floor runs on to the bottom edge of the screen.
   Handover, the same evening: the cards rise from 3cu below their place, not 5 (their feet
   passed through the subtitle box's zone), and the headline's glow is drawn soft at its
   edges (the shared one stood as a pale rectangle round the question).
   Pacing pass, 18 Nov night (owner: "บางช่วงเปลี่ยนหน้าไวมากแทบมองไม่ทัน แล้วพวกโมชั่นไม่สมูทอีก";
   rule: no view under 2.0 s): s04_l03's wheel turned a new card to the front for each type
   she named -- views of 1.0 and 1.6 s. The three types now gather in one row on "studios"
   and the camera holds while each lights up in its place; "to suit different ways of living"
   is ONE eased camera move (back and across) that brings in the owner's own interiors of the
   three types (his 18 Nov renders), shown one at a time, 3.6 s each, their card answering.

   Owner's notes, 19 Nov morning (on version 5):
   - s04_l03 -> s04_l06, "transition ตรงนี้ดูไม่สมูท": the blend laid the cards, the header and the
     interiors' frame over the next line's building. Now the held end frame is ONE picture: when her
     line is over the camera goes INTO the interiors' frame (one eased dolly, 3 s) -- the cards leave
     the shot with the camera, the header and the frame's words cool away -- and what holds while
     Khun Miki asks is the owner's interiors full screen, one at a time, nothing else on it. No new
     picture change starts once the next line is pressed (the blend never shows a second change).
     The other side (s04_l06, living_photos) opens on one subject: OWNER_ROUND_1119_handover45.md.
   - s05_l03, "ตอนโชว์อยากให้ซูมเข้าไปดูแต่ละ แล้วก็ซูมออก แล้วก็หมุนเหมือนที่ทำ ... แล้วเส้นตรงนี้เอามาทำไม
     เอาออก": as each view is named the camera goes INTO that window's picture until it fills the
     screen, comes back out, then turns to the next window. The camera of this line is an EYE now
     (its position and its turn; the stage's camera point follows from it) flown along a monotone
     cubic through a few framings -- out, turn, in are one fluid move with eased ends, never a
     snap. Looking through a window as you walk up to it, the frame grows faster than the view:
     the picture in a pane is laid out at its own proportions and, as the pane fills the screen,
     is drawn smaller inside it so that the WHOLE picture fills the screen at the end (sharp: the
     hi-res file at about one pixel per pixel), and never leaves a gap in the pane. The window's
     words ride in a caption at the top left while the camera tours (labels hanging under a pane
     that fills the screen would pass through the subtitle box); they hang under the windows again
     when the camera draws back to all three, after her line. The two white lines in each pane
     (mullions) are gone and the curtains have no stripes (the owner marked them, 34.png).

   Nothing that can be read or seen is written here: words, the figure and every picture come
   from the slide through ctx; the numbers that place things are this file's own geometry. */
CeremonyShow.register('units_views', function (ctx) {
  var fx = ctx.fx;
  var mode = ctx.opt('mode', 'units');
  var late = !!ctx.late;
  var instant = late;                                // true while a beat is caught up late
  var P = 78, EYE = 25.4, FLOOR = 15.6;            // perspective, eye line, floor under the eye (cu)
  ctx.bg('off');

  function U(n) { return 'calc(' + (Math.round(n * 1000) / 1000) + ' * var(--cu))'; }
  function mix(a, b, f) { return a + (b - a) * f; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function inout(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function outq(t) { return 1 - Math.pow(1 - t, 3); }
  function rad(d) { return d * Math.PI / 180; }
  // the film's curves as functions of time (the same numbers as --cs-ease / -in / -out)
  function bez(x1, y1, x2, y2) {
    function cb(a, b, u) { var v = 1 - u; return 3 * a * u * v * v + 3 * b * u * u * v + u * u * u; }
    return function (t) {
      if (t <= 0) return 0;
      if (t >= 1) return 1;
      var lo = 0, hi = 1, u = t;
      for (var i = 0; i < 24; i++) {
        var x = cb(x1, x2, u);
        if (Math.abs(x - t) < 1e-4) break;
        if (x < t) lo = u; else hi = u;
        u = (lo + hi) / 2;
      }
      return cb(y1, y2, u);
    };
  }
  var EASE = {
    sig: bez(0.4, 0, 0.2, 1),                        // on-screen moves (80% of them)
    enter: bez(0.05, 0.7, 0.1, 1),                   // entrances: fast start, long gentle landing
    exit: bez(0.3, 0, 1, 1),                         // exits: gentle start, gone quickly
    sine: function (t) { return -(Math.cos(Math.PI * t) - 1) / 2; },
    inout: inout, out: outq, lin: function (t) { return t; }
  };

  // A picture keyed on black costs the page a pass over all its pixels when it loads (the
  // core keys it through a canvas, on the main thread: ~40 ms each, ~130 ms the first). Made
  // all together as the slide came up they held this world's frame loop for half a second,
  // in the cut (measured 18 Nov). So: what a picture stands in is built now, the picture
  // itself is made when its turn comes -- one at a time, 110 ms and more apart, after the
  // cut-in, always a second and more before the beat that shows it. Shown late: at once.
  function soon(ms, fn) { if (late) fn(); else ctx.after(ms, fn); }

  // ---- the world ----
  var world = ctx.el('world');
  var back = ctx.el('back');                        // the light behind everything
  var beam = ctx.el('beam'), arc = ctx.el('arc'), bloom = ctx.el('bloom');
  // the floor: the lines of the world's own ground, meeting at its eye line, running on to
  // the bottom edge of the screen (the film is full screen: there is no band to end at)
  var floor = ctx.svg('svg', { cls: 'floor', viewBox: '0 0 100 56.25', preserveAspectRatio: 'none', fill: 'none' });
  if (floor) {
    for (var fk = -14; fk <= 14; fk++) ctx.add(floor, ctx.svg('line', { x1: 50, y1: EYE, x2: 50 + fk * 15.84, y2: 56.25 }));
    for (var fj = 0; fj <= 14; fj++) {
      var fy = EYE + FLOOR * P / (39.4 + 8 * fj);
      ctx.add(floor, ctx.svg('line', { x1: 0, y1: fy.toFixed(3), x2: 100, y2: fy.toFixed(3) }));
    }
  }
  ctx.add(world, back, bloom, arc, beam, floor, ctx.el('grade'));
  ctx.add(ctx.stage, world);

  var objs = [];
  var cam = { x: 0, y: 0, z: 0, yaw: 0 };
  var bow = { f: 0, depth: 0 };                     // the camera's arc between two places
  var clock = 0;

  // an object: `el` stands on its anchor (a zero-size box at the object's foot, or its centre
  // for a flat one). pose(o) may set its place from other numbers each frame.
  function obj(el, p) {
    var a = ctx.el('anc');
    ctx.add(a, el);
    ctx.add(world, a);
    var o = { a: a, x: 0, y: FLOOR, z: 0, ry: 0, rx: 0, rz: 0, s: 1, op: 1, free: false, pose: null, last: '', lastOp: -1, lastZ: 0 };
    for (var k in p) { if (Object.prototype.hasOwnProperty.call(p, k)) o[k] = p[k]; }
    objs.push(o);
    return o;
  }

  function place(o, dx, dy, dz, yaw) {
    if (o.pose) o.pose(o);
    var t = 'rotateY(' + yaw.toFixed(3) + 'deg) translate3d(' + U(o.x - dx) + ', ' + U(o.y - dy) + ', ' + U(o.z - dz) + ')'
      + (o.ry ? ' rotateY(' + o.ry.toFixed(3) + 'deg)' : '') + (o.rx ? ' rotateX(' + o.rx.toFixed(3) + 'deg)' : '')
      + (o.rz ? ' rotateZ(' + o.rz.toFixed(3) + 'deg)' : '') + (o.s !== 1 ? ' scale(' + o.s.toFixed(4) + ')' : '');
    if (t !== o.last) { o.last = t; o.a.style.transform = t; }
    // (`vis`: a window the eye has come too close to -- off the screen by then -- is not drawn at
    // all: a layer reaching behind the eye is drawn back to front by the page)
    var op = clamp(o.op * (o.vis === undefined ? 1 : o.vis), 0, 1);
    if (Math.abs(op - o.lastOp) > 0.002) { o.lastOp = op; o.a.style.opacity = String(op); }
    var zi = Math.round((o.z - dz) * 10 + (o.bias || 0)) + 2000;
    if (zi !== o.lastZ) { o.lastZ = zi; o.a.style.zIndex = String(zi); }
  }

  // ---- tweens of any numbers, and the follow-ups that come after a beat ----
  // ONE clock for both: the frame's own time `t` (review 18 Nov: tweens summed the frame's
  // dt, capped at 100 ms, while the follow-ups ran on wall-clock timers -- below ~10 fps the
  // motion went into slow motion and a scan's flare fired before its line had passed). A tween
  // or a follow-up made in a beat starts on the next frame; one made by a follow-up starts at
  // the moment that follow-up was due, not when a slow frame got round to it -- so a chain
  // (rail -> scan -> flare) stays in step at any frame rate.
  var tweens = [], waits = [];
  var stampAt = null;                               // the time anything made right now starts at
  // (owner round 19 Nov) `real`: the act's own move AFTER her line (settle) is being set up --
  // it plays at its own pace although the line is over (late/instant would jump it). Only when
  // the line ended while this screen was showing it: a screen that comes up on a settled line
  // gets the end frame at once (see settle).
  var real = false;
  function jumps() { return (late || instant) && !real; }
  function go(target, to, ms, kind) {
    var rest = {};
    for (var k in to) { if (Object.prototype.hasOwnProperty.call(to, k)) rest[k] = to[k]; }
    tweens = tweens.filter(function (t) { return t.target !== target || !hasOverlap(t.to, rest); });
    if (jumps() || !(ms > 0)) { for (var k0 in rest) { target[k0] = rest[k0]; } return; }
    var from = {};
    for (var k1 in rest) { from[k1] = target[k1]; }
    tweens.push({ target: target, from: from, to: rest, t0: stampAt, ms: ms, kind: kind || 'sig' });
  }
  function hasOverlap(a, b) { for (var k in a) { if (Object.prototype.hasOwnProperty.call(b, k)) return true; } return false; }
  // polish that follows a beat (never WHEN something appears): at once while caught up late
  function then(ms, fn) {
    if (jumps() || !(ms > 0)) { fn(); return; }
    waits.push({ t0: stampAt, ms: ms, fn: fn, real: real });
  }
  // the line is over: what is still waiting happens now (and, late, at once)
  function flush() {
    var q = waits;
    waits = [];
    q.forEach(function (w) { w.fn(); });
  }
  // (the arc between two windows -- `travel`, the camera point on the film's curve with a small
  // step back half-way -- is the eye's flight now: see `fly`)

  // ---- the camera as an EYE (owner round 19 Nov, the views line): where the eye is and where it
  // looks (yaw); the stage's camera point -- the plane the world is placed against, P in front
  // of the eye -- follows from it. A push is then a straight dolly of the eye, and a turn turns
  // it where it stands (moving the camera point instead swings the eye round a point P ahead).
  var eye = null, flight = null, eyePrev = null, eyeVel = { x: 0, y: 0, z: 0, yaw: 0 };
  var CH = ['x', 'y', 'z', 'yaw'];
  function eyeOf(c) { return { x: c.x - P * Math.sin(rad(c.yaw)), y: c.y, z: c.z + P * Math.cos(rad(c.yaw)), yaw: c.yaw }; }
  function camFromEye() {
    cam.x = eye.x + P * Math.sin(rad(eye.yaw)); cam.z = eye.z - P * Math.cos(rad(eye.yaw));
    cam.y = eye.y; cam.yaw = eye.yaw;
  }
  // A flight: the eye from where it is through `pts` (eye framings) at `knots` (0..1 of the time,
  // the first 0 = where it is now), one monotone cubic per channel (Fritsch-Butland tangents: no
  // channel overshoots a framing -- the turn never swings past the window it turns to), at rest
  // at the end, starting with the velocity the eye already has (a new flight over a running
  // one never jerks). The time runs evenly; the ease is the curve's own (it starts and ends at
  // rest, and slows into every framing where a channel stops or turns back).
  function fly(pts, knots, ms) {
    var all = [{ x: eye.x, y: eye.y, z: eye.z, yaw: eye.yaw }].concat(pts);
    tweens = tweens.filter(function (t) { return t.target !== eye; });
    if (jumps() || !(ms > 0)) {
      var e = all[all.length - 1];
      CH.forEach(function (k) { eye[k] = e[k]; });
      flight = null; eyePrev = null; camFromEye();
      return;
    }
    var n = all.length, tan = {};
    CH.forEach(function (k) {
      var d = [], hs = [], m = [];
      for (var i = 0; i < n - 1; i++) { hs.push(knots[i + 1] - knots[i]); d.push((all[i + 1][k] - all[i][k]) / Math.max(1e-6, hs[i])); }
      for (var j = 0; j < n; j++) {
        if (j === 0) {
          var v = eyeVel[k] * ms;                     // (per unit of u: the velocity it has now)
          m.push(d[0] * v > 0 ? Math.sign(v) * Math.min(Math.abs(v), 3 * Math.abs(d[0])) : (d[0] === 0 ? 0 : v));
        } else if (j === n - 1) m.push(0);
        else if (d[j - 1] * d[j] <= 0) m.push(0);
        else {
          var h0 = hs[j - 1], h1 = hs[j];
          m.push(3 * (h0 + h1) / ((2 * h1 + h0) / d[j - 1] + (h1 + 2 * h0) / d[j]));
        }
      }
      tan[k] = m;
    });
    flight = { pts: all, knots: knots, tan: tan, t0: stampAt, ms: ms };
  }
  function flightAt(u) {
    var f = flight, K = f.knots, i = 0;
    while (i < K.length - 2 && u > K[i + 1]) i++;
    var h = K[i + 1] - K[i], s = h > 0 ? clamp((u - K[i]) / h, 0, 1) : 1;
    var s2 = s * s, s3 = s2 * s;
    var h00 = 2 * s3 - 3 * s2 + 1, h10 = s3 - 2 * s2 + s, h01 = -2 * s3 + 3 * s2, h11 = s3 - s2;
    CH.forEach(function (k) {
      eye[k] = h00 * f.pts[i][k] + h10 * h * f.tan[k][i] + h01 * f.pts[i + 1][k] + h11 * h * f.tan[k][i + 1];
    });
  }
  // the ambient wander's size (1 = as it always was): small while a picture fills the screen
  var ambient = { k: 1 };
  var framesSeen = 0;
  var sights = [];                                  // per-frame looks at the world before it is placed

  function frame(t) {
    clock = t;
    framesSeen++;
    // the follow-ups that are due, each run at the time it was due (what it starts starts then)
    var guard = 0;
    for (var i = 0; i < waits.length && guard < 200; guard++) {
      var q = waits[i];
      if (q.t0 === null) q.t0 = t;
      if (t - q.t0 >= q.ms) {
        waits.splice(i, 1);
        stampAt = q.t0 + q.ms;
        var was = real;
        real = !!q.real;
        try { q.fn(); } finally { stampAt = null; real = was; }
      } else i++;
    }
    for (var j0 = tweens.length - 1; j0 >= 0; j0--) {
      var w = tweens[j0];
      if (w.t0 === null) w.t0 = t;
      var f = clamp((t - w.t0) / w.ms, 0, 1), e = (EASE[w.kind] || EASE.sig)(f);
      for (var k in w.to) { if (Object.prototype.hasOwnProperty.call(w.to, k)) w.target[k] = mix(w.from[k], w.to[k], e); }
      if (f >= 1) tweens.splice(j0, 1);
    }
    if (eye) {
      if (flight) {
        if (flight.t0 === null) flight.t0 = t;
        var u = clamp((t - flight.t0) / flight.ms, 0, 1);
        flightAt(u);
        if (u >= 1) flight = null;
      }
      if (eyePrev && t > eyePrev.t) CH.forEach(function (c) { eyeVel[c] = (eye[c] - eyePrev[c]) / (t - eyePrev.t); });
      eyePrev = { t: t, x: eye.x, y: eye.y, z: eye.z, yaw: eye.yaw };
      camFromEye();
    }
    // the camera never stops (ambient: slow sines, each its own period -- 11 to 18 s, a few
    // pixels a second: enough that a held shot is seen to breathe. They used to be 38 to 61 s
    // long, under two pixels a second: a hold read as a frozen frame)
    var ak = ambient.k;
    var dx = Math.sin(t / 1700) * 0.6 * ak, dz = Math.sin(t / 2300 + 1) * 0.7 * ak, dy = Math.sin(t / 2000 + 2) * 0.16 * ak, dyaw = Math.sin(t / 2900) * 0.3 * ak;
    var arcz = -bow.depth * Math.sin(Math.PI * clamp(bow.f, 0, 1));
    var yaw = cam.yaw + dyaw;
    // (the camera the world is placed against this frame, wander and all: what looks at the
    // world -- how big a pane is on the screen, where a sharp copy must stand -- uses this one)
    seenFrom.x = cam.x + dx; seenFrom.y = cam.y + dy; seenFrom.z = cam.z + dz + arcz; seenFrom.yaw = yaw;
    for (var si = 0; si < sights.length; si++) sights[si](t);
    for (var j = 0; j < objs.length; j++) { place(objs[j], seenFrom.x, seenFrom.y, seenFrom.z, yaw); }
    for (var m = 0; m < movers.length; m++) { movers[m](yaw); }
  }
  var seenFrom = { x: 0, y: 0, z: 0, yaw: 0 };
  // Where a flat thing of the world (its middle at x, y, z, turned ry) is on the screen now:
  // {sx, sy} = its middle, s = its scale, frontal = it faces the camera (within 2.5 degrees).
  function onScreen(x, y, z, ry) {
    var cs = Math.cos(rad(seenFrom.yaw)), sn = Math.sin(rad(seenFrom.yaw));
    var dx = x - seenFrom.x, dz = z - seenFrom.z, dy = y - seenFrom.y;
    var X = dx * cs + dz * sn, Z = -dx * sn + dz * cs;
    var s = Z < P - 0.5 ? P / (P - Z) : 999;
    return { X: X, Z: Z, s: s, sx: 50 + X * s, sy: 25.4 + dy * s, frontal: Math.abs(seenFrom.yaw + (ry || 0)) < 2.5 };
  }
  // A SHARP COPY of a picture: a plain 2D layer over the world, for the moments a 3D frame fills
  // the screen. (Measured on the shots, 19 Nov: the page draws a layer under the world's
  // perspective at about its own size, and a pane seen 4.4 times its size was that texture
  // stretched -- soft and blocky; a 2D layer is drawn at the size it is seen.) `put(left, top,
  // width, cover)`: the copy stands exactly over the frame's picture (cu) and is seen as much as
  // the frame covers the whole screen -- `cover`, 0..1: until then the frame (around it, the
  // world) is what shows. It never goes to nothing: at 0.3% it is drawn, and while it is not
  // seen it waits PARKED where it will be seen (`park`: the frame filling the screen), at that
  // size -- so its picture is decoded once, early, at the size it is seen (decoded first in the
  // zoom's last moments, a 4-megapixel picture would be a hitch there).
  function sharpCopy(box, w0, park) {
    var lastT = '', lastO = -1;
    box.style.width = U(w0);
    ctx.add(ctx.stage, box);
    return function (left, top, width, cover) {
      if (!(cover > 0)) { left = park.left; top = park.top; width = park.width; }
      var t = 'translate3d(' + U(left) + ', ' + U(top) + ', 0) scale(' + (width / w0).toFixed(5) + ')';
      if (t !== lastT) { lastT = t; box.style.transform = t; }
      var op = Math.max(0.003, clamp(cover, 0, 1));
      if (Math.abs(op - lastO) > 0.0015) { lastO = op; box.style.opacity = String(op); }
    };
  }
  // how far a frame on the screen covers it: the smallest margin past the screen's edges, as
  // 0..1 over `ramp` cu (0 = an edge of it is on the screen)
  function covers(l, t, w, h, ramp) {
    var m = Math.min(-l, l + w - 100, -t, t + h - 56.25);
    return EASE.sine(clamp(m / ramp, 0, 1));
  }
  var movers = [];
  ctx.frame(frame);

  // ---- shared pieces ----
  function glass(cls) { var g = fx.glass(cls); return g; }

  // a flat pool of light lying on the floor
  function pool(w, d, a) {
    var el = ctx.el('pool');
    el.style.width = U(w); el.style.height = U(d); el.style.left = U(-w / 2); el.style.top = U(-d / 2);
    var o = obj(el, { y: FLOOR, rx: 90, bias: -400 });
    o.base = a;
    // ambient: the light on the floor breathes (a 12 s sine)
    // (and it goes while the camera is inside a window: a floor that would reach behind the eye)
    o.pose = function (q) { q.op = (0.8 + 0.2 * Math.sin(clock / 1900 + 0.7)) * (1 - EASE.sine(clamp((zoomMost - 1.3) / 1.0, 0, 1))); };
    return o;
  }

  // a headline at the top of the stage (title, kicker), on its own soft plate: one plain
  // phrase -- the core lights it up when its beat comes (it used to be typed with a caret)
  function topline(key) {
    var t = ctx.text(key, { cls: 'cs-type-h1' });
    if (!t) return null;
    var box = ctx.el('top');
    ctx.add(box, ctx.el('tplate'), t);
    ctx.add(ctx.stage, box);
    return { box: box, text: t };
  }


  // ---- a glass card with a unit model: the wire hologram, a scan, the lit model ----
  // The scan is ONE cut: the model is revealed above it (a wipe down, with its line of light)
  // and the wire is taken away below it (a wipe up, no line of its own), both set from the
  // same number every frame -- so what the line has passed is the model, what it has not is
  // still the wire.
  // `kd.at` = [wire, model]: when each of its two keyed pictures is made (ms after the slide
  // came up -- see `soon`); `kd.n` = which card it is (its ambient light keeps its own time).
  function makeCard(kd) {
    var box = ctx.el('card dim c' + (kd.n || 0));
    var halo = ctx.el('chalo');
    var gl = glass('cglass');
    var model = ctx.el('model');
    model.style.top = kd.mt + '%';
    // (a place for each picture inside its wipe: the picture comes into it when it is made)
    var wireSlot = ctx.el('slot'), solidSlot = ctx.el('slot');
    var at = kd.at || [0, 0];
    soon(at[0], function () { var p = ctx.picture(kd.w, { fit: 'contain', keyed: 'black' }); if (p) ctx.add(wireSlot, p.el); });
    soon(at[1], function () { var p = ctx.picture(kd.s, { fit: 'contain', keyed: 'black' }); if (p) ctx.add(solidSlot, p.el); });
    var wipe = fx.wipe(solidSlot, { dir: 'down' });
    var wireWipe = fx.wipe(wireSlot, { dir: 'up', edge: false, from: 1 });
    ctx.add(model, wireWipe.el, wipe.el);
    var mclip = ctx.el('mclip');
    // (ambient: a soft light crosses the glass now and then -- a card that waits is never a still)
    ctx.add(mclip, model, ctx.el('csheen'));
    var ctop = ctx.el('ctop');                       // the scanner's rail along the card's top edge
    var rule = ctx.el('rule');
    var label = ctx.text(kd.label, { cls: 'clabel' });
    // (a card that steps back goes darker, not see-through: at 55% opacity the hologram court
    // showed through the model)
    ctx.add(box, halo, gl, mclip, ctop, rule, label, ctx.el('cveil'));
    var o = obj(box, { x: 0, y: FLOOR - 1.8, z: 0, op: 0 });
    o.live = 0; o.fan = 0; o.a0 = 0; o.lum = 1; o.side = 0; o.sf = 0; o.sfLast = -1; o.box = box;
    o.scanning = false; o.wantDim = false;
    o.lit = function (on) {
      o.wantDim = !on;
      // a card whose scan is still running keeps its light until the line has passed (the
      // studios are named only 0.66 s before the 1-bedroom units)
      if (on || !o.scanning) box.classList.toggle('dim', !on);
    };
    // the scan: the rail lights (anticipation), the cut runs down, the rule and the halo answer
    // once it has passed (follow-through)
    o.scan = function (delay) {
      o.scanning = true;
      then(delay || 0, function () {
        box.classList.add('armed');
        then(160, function () {
          go(o, { sf: 1 }, 1000, 'sig');
          then(1000, function () {
            o.scanning = false;
            box.classList.remove('armed');
            box.classList.add('scanned', 'flare');
            then(420, function () { box.classList.remove('flare'); if (o.wantDim) box.classList.add('dim'); });
          });
        });
      });
    };
    movers.push(function () {
      if (o.sf === o.sfLast) return;
      o.sfLast = o.sf;
      wipe.set(o.sf);
      wireWipe.set(1 - o.sf);
    });
    return o;
  }
  // The three unit types stand in ONE row, studios -> 1-bedroom -> 2-bedroom, left to right
  // (her order, the reading order). Pacing pass, 18 Nov night (owner: "บางช่วงเปลี่ยนหน้าไวมาก
  // แทบมองไม่ทัน"): they used to stand on a wheel that turned a card to the front for each type
  // she named -- the whole picture changed at "1-bedroom" and again at "2-bedroom", views of
  // 1.0 and 1.6 s (measured on the window recordings). Now they gather together on one view
  // when she says "studios" and the camera holds: each type is named by its own card lighting
  // up in its place (a scan, wire -> model), nothing else moves. In the row's own world
  // numbers (cu); the foot stands 2.6 below the eye line so a card rising from 2cu below its
  // place stays clear of the subtitle box's zone (43.8cu) even while the camera is still close.
  var ROW = [{ x: -24.1, ry: 7 }, { x: 0, ry: 0 }, { x: 24.1, ry: -7 }];
  var ROW_Y = FLOOR - 2.6, ROW_Z = 7.8, ROW_S = 0.9;
  // a card's rise: from below and a little outward, the side converging first (an arc, not a lift).
  // (2cu: the screen is the whole film now and a card rising from further below its place
  // passed through the subtitle box's zone at its foot)
  function riseOffset(q) {
    var r = 1 - clamp(q.live, 0, 1);
    return { x: q.side * 3.2 * r * r, y: 2 * r, z: -3.5 * r * r };
  }

  // ---- a window: a pane with its view, two drawn curtains ----
  // (owner 19 Nov, 34.png: "แล้วเส้นตรงนี้เอามาทำไม เอาออก" -- the two white mullions across every
  // pane are gone, and the curtains are plain cloth: no lines run down a window any more)
  var PANE_W = 24.5, PANE_H = 29.2, WIN_Y = FLOOR - 4.8;
  var S_ZOOM = 4.4;                                 // a pane this many times its size fills the screen
  // where the picture of a view stands in its pane at rest -- the framing the owner has seen
  // (a box 128% x 112% of the pane, the picture covering it, the pool's to the right; the view
  // from above 1.3x closer): [left, top, width, height] in parts of the pane, then the picture's
  // place in that box (what object-position was)
  var VIEWBOX = { pool: [-0.14, -0.06, 1.28, 1.12, 0.78, 0.5], above: [-0.33, -0.23, 1.66, 1.46, 0.55, 0.40] };
  // the width on the screen (cu) of a view in a pane drawn s times its size: the view grows with
  // the frame (the picture is that much larger than the pane); from `sk` on it grows less and
  // less, towards PS -- the whole picture across the screen and a little more -- so the frame
  // runs on past the screen's edges while the view settles: walking up to a window
  function viewWidth(g, s) {
    if (s <= g.sk) return g.W * s;
    var p0 = g.W * g.sk, kk = g.W / Math.max(1e-3, g.PS - p0);
    return g.PS - (g.PS - p0) * Math.exp(-(s - g.sk) * kk);
  }
  function viewGeo(k, a) {
    var b = VIEWBOX[k] || [-0.14, -0.06, 1.28, 1.12, 0.5, 0.5];
    var bl = b[0] * PANE_W, bt = b[1] * PANE_H, bw = b[2] * PANE_W, bh = b[3] * PANE_H;
    var w = Math.max(bw, bh * a), h = w / a;
    var g = { a: a, L: bl + (bw - w) * b[4], T: bt + (bh - h) * b[5], W: w, H: h, bcx: bl + bw / 2, bcy: bt + bh / 2,
              PS: 1.05 * Math.max(100, 56.25 * a), sk: 2.4 };
    // (where the knee begins: the first scale from which the view, drawn smaller from there on,
    // still covers all of the pane that can be on the screen -- looked for once, here)
    for (var sk = 1.25; sk < 2.4; sk += 0.05) {
      g.sk = sk;
      var ok = true;
      for (var s = sk; s <= S_ZOOM + 0.01 && ok; s += 0.05) {
        var need = Math.max(Math.min(PANE_W, 100 / s), Math.min(PANE_H, 56.25 / s) * a) * s;
        if (viewWidth(g, s) < need * 1.02) ok = false;
      }
      if (ok) break;
    }
    return g;
  }
  function makeWindow(kd) {
    var box = ctx.el('win dim' + (kd.k === 'sea' ? ' sea' : ''));
    var halo = ctx.el('whalo');
    var pane = ctx.el('pane');
    var pv = null, sview = null, put = null;
    if (kd.k === 'sea') {
      // the sea has no checked picture: the film's own light, an arc and the word -- one word,
      // upright, in the middle of the window (it was three giant italic letters, one a pane).
      // The light is one layer: going into the window, it is drawn smaller as the pane grows
      sview = ctx.el('sview');
      var swrap = ctx.el('swrap');
      ctx.add(swrap, ctx.text('sea', { cls: 'swd' }));
      ctx.add(sview, ctx.el('glowup'), ctx.el('dome'), swrap);
      ctx.add(pane, sview);
    } else {
      pv = ctx.el('pview');
      ctx.add(pane, pv);
      // (`when`: the picture is made that long after the slide came up -- see `soon`; it is laid
      // out at its own proportions, the framing above)
      put = function () {
        var pic = ctx.picture(kd.pic);
        if (!pic) return;
        var g = viewGeo(kd.k, pic.w > 0 && pic.h > 0 ? pic.w / pic.h : 1.6);
        pv.style.left = U(g.L); pv.style.top = U(g.T); pv.style.width = U(g.W); pv.style.height = U(g.H);
        o.geo = g;
        ctx.add(pv, pic.el);
        // (its sharp copy, for when the pane fills the screen -- see sharpCopy; `copyAt`: made
        // that long after the picture, not in a cut -- s05_l06 opens with its views up)
        var copyIt = function () {
          var copy = ctx.picture(kd.pic);
          if (!copy) return;
          var zb = ctx.el('zshot');
          zb.style.height = U(g.PS / g.a);
          ctx.add(zb, copy.el);
          o.sharp = sharpCopy(zb, g.PS, { left: 50 - g.PS / 2, top: 56.25 / 2 - g.PS / g.a / 2, width: g.PS });
        };
        if (kd.copyAt > 0) soon(kd.copyAt, copyIt); else copyIt();
      };
    }
    // (ambient: daylight slides slowly across the glass of an open window)
    ctx.add(pane, ctx.el('shade'), ctx.el('wsheen'));
    var plates = ctx.el('plates');
    var plateEls = [];
    kd.plates.forEach(function (k) {
      var t = ctx.text(k, { cls: 'ptext' });
      if (!t) return;
      var p = fx.sat(t, 'plate');
      plateEls.push(p);
      ctx.add(plates, p);
    });
    // the veil: a closed window waits in the half-dark, a window seen before steps back
    ctx.add(box, halo, pane, ctx.el('drape l'), ctx.el('drape r'), ctx.el('veil'), ctx.el('wtop'), plates);
    box.classList.add('closed');
    var o = obj(box, { x: kd.x, y: WIN_Y, z: kd.z, ry: kd.ry, op: 1 });
    o.pv = pv; o.sview = sview; o.box = box; o.zoom = 1; o.kd = kd; o.geo = null;
    o.sc = 0.8; o.sx = 50; o.sy = 28; o.zin = false; o.ph = kd.k === 'above' ? 2.2 : 0;
    o.sready = false;
    if (sview) soon(1200, function () { o.sready = true; });
    if (put) { if (kd.when > 0) soon(kd.when, put); else put(); }
    // open: the curtains tighten a touch (anticipation), then part -- the right one a beat after
    // the left -- and the light blooms after them; the view settles from a little closer
    o.open = function (on, half, now) {
      if (!on) { box.classList.add('closed'); box.classList.remove('half'); box.classList.add('dim'); return; }
      if (now || instant || late) { box.classList.remove('closed', 'tug', 'dim'); box.classList.toggle('half', !!half); return; }
      if (box.classList.contains('closed')) {
        box.classList.add('tug');
        then(150, function () {
          box.classList.remove('tug', 'closed');
          box.classList.toggle('half', !!half);
          box.classList.remove('dim');
        });
        if (!half) { o.zoom = 1.07; go(o, { zoom: 1 }, 1800, 'enter'); }
      } else {
        box.classList.toggle('half', !!half);
        box.classList.remove('dim');
        if (!half) { o.zoom = 1.05; go(o, { zoom: 1 }, 1600, 'enter'); }
      }
    };
    // its plates rise into place one at a time, each on its own beat
    o.plate = function (i) {
      var p = plateEls[i];
      if (p) p.classList.add('in');
    };
    o.back = function (on) { box.classList.toggle('past', !!on); };
    return o;
  }
  // Each frame, before the world is placed: how big each window is on the screen now (its pane's
  // scale `sc` and where its middle is) from the camera alone (no wander), and whether the eye has
  // come so close past it that it must not be drawn (it is far off the screen by then).
  function watchWindows(list) {
    sights.push(function () {
      var sn = Math.sin(rad(seenFrom.yaw)), cs = Math.cos(rad(seenFrom.yaw)), most = 0;
      list.forEach(function (o) {
        var v = onScreen(o.x, o.y - PANE_H / 2, o.z, o.ry);
        // (the pane's two side edges, turned with it: the nearer one decides)
        var ex = PANE_W / 2 * Math.cos(rad(o.ry)), ez = -PANE_W / 2 * Math.sin(rad(o.ry));
        var near = v.Z + Math.abs(-ex * sn + ez * cs);
        o.vis = 1 - EASE.sine(clamp((near - 64) / 7, 0, 1));
        o.sc = v.s; o.sx = v.sx; o.sy = v.sy; o.frontal = v.frontal;
        // (the curtains draw right back as the pane comes to fill the screen)
        var zin = o.sc > (o.zin ? 2.2 : 2.4);
        if (zin !== o.zin) { o.zin = zin; o.box.classList.toggle('zin', zin); }
        if (o.vis > 0.5) most = Math.max(most, o.sc);
      });
      zoomMost = most;
    });
    // the views, after the world is placed (see paneView)
    movers.push(function (yaw) {
      var most = 0;
      list.forEach(function (o) { paneView(o, yaw); most = Math.max(most, o.cover || 0); });
      gradeOver(most);
    });
  }
  var zoomMost = 0;                                  // the largest pane on the screen now (its scale)
  // The view in a pane. At rest (the pane at about its size) as it always was: it slides against
  // the camera (looking through a window), drifts and breathes. As the pane grows past `sk` the
  // view is drawn smaller in it (viewWidth), slides to the middle of what of the pane is on the
  // screen, and its wander dies down; it always covers that part of the pane (never a gap).
  function paneView(o, yaw) {
    var s = o.sc, ph = o.ph;
    if (o.sview) {
      // (and lifted a little: the horizon's arc, seen whole, would run down behind the subtitle's
      // box at its sides -- shot 19 Nov)
      var qs = s <= 1.25 ? 0 : EASE.sine(clamp((s - 1.25) / (S_ZOOM - 1.25), 0, 1));
      var ks = mix(1, 0.62, qs);
      // (a layer of its own only from a second after the cut -- see the caption -- or once moving)
      var ts = !o.sready && qs === 0 ? 'none' : 'translate3d(0, ' + U(-2.6 * qs) + ', 0) scale(' + ks.toFixed(4) + ')';
      if (ts !== o.slast) { o.slast = ts; o.sview.style.transform = ts; }
      return;
    }
    var g = o.geo;
    if (!o.pv || !g) return;
    var q = s <= g.sk ? 0 : EASE.sine(clamp((s - g.sk) / (S_ZOOM - g.sk), 0, 1));
    var ax = 0.42 * Math.sin(clock / 2100 + ph), az = 1.022 + 0.014 * Math.sin(clock / 2700 + ph);
    var dx = clamp(-(cam.x * 0.16) - yaw * 0.1 + ax, -3.2, 3.2) * (1 - q), dy = clamp(-cam.y * 0.1, -1.5, 1.5) * (1 - q);
    var kb = mix(o.zoom * az, 1.006 + 0.006 * Math.sin(clock / 2700 + ph), q);
    var w = viewWidth(g, Math.max(s, 0.01)) / Math.max(s, 0.01) * kb;
    var cx = g.bcx + (g.L + g.W / 2 - g.bcx) * kb + dx, cy = g.bcy + (g.T + g.H / 2 - g.bcy) * kb + dy;
    if (q > 0) {
      // (the part of the pane on the screen: it is all but square to the eye by now)
      var vl = Math.max(0, PANE_W / 2 - o.sx / s), vr = Math.min(PANE_W, PANE_W / 2 + (100 - o.sx) / s);
      var vt = Math.max(0, PANE_H / 2 - o.sy / s), vb = Math.min(PANE_H, PANE_H / 2 + (56.25 - o.sy) / s);
      if (vr > vl && vb > vt) {
        cx = mix(cx, (vl + vr) / 2, q); cy = mix(cy, (vt + vb) / 2, q);
        w = Math.max(w, (vr - vl) * 1.02, (vb - vt) * g.a * 1.02);
        var hh = w / g.a;
        cx = clamp(cx, vr - w / 2, vl + w / 2); cy = clamp(cy, vb - hh / 2, vt + hh / 2);
      }
    }
    var t = 'translate3d(' + U(cx - w / 2 - g.L) + ', ' + U(cy - w / g.a / 2 - g.T) + ', 0) scale(' + (w / g.W).toFixed(4) + ')';
    if (t !== o.plast) { o.plast = t; o.pv.style.transform = t; }
    // the sharp copy, exactly over this view on the screen; seen as the pane comes to cover the
    // whole screen (square to the camera by then)
    o.cover = 0;
    if (o.sharp) {
      var hv = w / g.a;
      if (o.frontal && o.vis > 0.99 && s < 900) o.cover = covers(o.sx - PANE_W / 2 * s, o.sy - PANE_H / 2 * s, PANE_W * s, PANE_H * s, 2.5);
      o.sharp(o.sx + (cx - w / 2 - PANE_W / 2) * s, o.sy + (cy - hv / 2 - PANE_H / 2) * s, w * s, o.cover);
    }
  }
  // The bottom of the screen graded behind the subtitle's box over a sharp copy (the world's own
  // grade lies under the copy): as much as a copy is seen
  var zgrade = null, zgradeOp = -1;
  function gradeOver(op) {
    if (!zgrade) { zgrade = ctx.el('zgrade'); ctx.add(ctx.stage, zgrade); }
    op = clamp(op, 0, 1);
    if (Math.abs(op - zgradeOp) > 0.002) { zgradeOp = op; zgrade.style.opacity = String(op); }
  }

  // ======================================================================== units
  function units() {
    var kick = ctx.text('kicker', { cls: 'uvkick' });
    var kickBox = ctx.el('kicker');
    ctx.add(kickBox, kick);
    ctx.add(ctx.stage, kickBox);

    var pl = pool(150, 80, 0.5); pl.z = -65;

    // a building standing on the floor: its place is built now, its hologram (keyed on black)
    // is made `when` its turn comes and fades in there
    function standing(key, w, h, x, z, ry, op, cls, when) {
      if (!ctx.has(key)) return null;
      var box = ctx.el('stand ' + (cls || ''));
      box.style.width = U(w); box.style.height = U(h); box.style.left = U(-w / 2); box.style.bottom = '0';
      soon(when, function () { var pic = ctx.picture(key, { fit: 'contain', keyed: 'black' }); if (pic) ctx.add(box, pic.el); });
      var o = obj(box, { x: x, z: z, ry: ry, op: op });
      o.base = op;
      return o;
    }
    // the court: the six buildings the units are in, a horseshoe round the floor (integration
    // 17 Nov night -- the design's opening; each hologram keyed on black, its own proportions)
    var COURT = [['b_a', 1.79, 0, -84, 0, 84], ['b_c', 1.86, -70, -66, 24, 66], ['b_d', 1.96, 70, -66, -24, 66],
                 ['b_b', 1.5, -100, -34, 46, 54], ['b_e', 2.0, 100, -34, -46, 62], ['b_f', 1.74, -118, -4, 62, 50]];
    // (the court lights up from the middle outward as the cut lands: the building straight
    // ahead first, the others round the horseshoe ~115 ms apart -- one keyed picture at a
    // time, see `soon`; all six stand before she says the number)
    var holos = COURT.map(function (b, i) {
      var w = b[5];
      var o = standing(b[0], w, w / b[1], b[2], b[3], b[4], i ? 0.9 : 1, 'bld', 420 + i * 116);
      if (!o) return null;
      // ambient: each hologram breathes on its own slow period, 9 to 18 s (never in step,
      // never a blink)
      o.dimv = 1; o.per = 1500 + i * 270; o.ph = i * 1.9;
      o.pose = function (q) { q.op = q.base * q.dimv * (0.9 + 0.1 * Math.sin(clock / q.per + q.ph)); };
      return o;
    }).filter(Boolean);
    // (a seventh place, near right, is left open: the camera's dolly passes there)
    function dimHolos(to, ms) { holos.forEach(function (o) { go(o, { dimv: to }, ms, 'sig'); }); }

    // the figure: one phrase in the middle of the court ("1,066 units in total": the number
    // at the headline weight, its words smaller beside it), on a soft dark plate so the
    // hologram behind never runs through it. The core lights it up on its beat. When the
    // cards come, the SAME phrase goes up to the top as the header -- one element, one move
    // (it was a 36cu monument rising out of the floor, and a second copy at the top).
    var numBox = ctx.el('numbox');
    var fig = ctx.figure('total', { glow: true });
    ctx.add(numBox, ctx.el('numscrim'), fig);
    ctx.add(ctx.stage, numBox);

    // (their keyed pictures are made once the court stands, 130 ms apart, in the order they
    // are needed: `at` = [wire, model], ms after the slide came up. All six are asked for by
    // 1.8 s -- the cards come at ~3 s, a second and more to arrive over the hall's network)
    var kinds = [
      { w: 'wire_s', s: 'solid_s', label: 'studios', mt: 5, n: 0, at: [1130, 1260] },
      { w: 'wire_1', s: 'solid_1', label: 'onebr', mt: 0, n: 1, at: [1390, 1650] },
      { w: 'wire_2', s: 'solid_2', label: 'twobr', mt: 2, n: 2, at: [1520, 1780] }
    ];
    var cards = kinds.map(function (kd, i) {
      var c = makeCard(kd);
      c.box.classList.add('row');
      c.side = i === 0 ? -1 : (i === 1 ? 0 : 1);
      c.pose = function (q) {
        var r = riseOffset(q), t = ROW[i];
        q.x = t.x + r.x; q.y = ROW_Y + r.y; q.z = ROW_Z + r.z; q.ry = t.ry; q.rz = 0; q.s = ROW_S;
        q.op = q.live * q.lum * rowFade;
        // ambient: a card floats while it waits (each on its own time: 5.5 and 8 s sines)
        q.y += 0.24 * Math.sin(clock / 880 + i * 2.1); q.ry += 0.6 * Math.sin(clock / 1270 + i * 1.3);
      };
      return c;
    });
    // staging, as each type is named: the newest is the hero; the ones she has named stay lit,
    // a step back; the ones still to come wait in the dark, their wire holograms showing
    function stage(k) {
      cards.forEach(function (c, i) {
        c.lit(i <= k);
        go(c, { lum: i === k ? 1 : (i < k ? 0.9 : 0.92) }, 500, 'sig');
      });
    }

    // "different ways of living": the hero frame shows the owner's own interiors of the three
    // types (18 Nov renders), one at a time -- the studio, the 1-bedroom, the 2-bedroom -- each
    // a dissolve of 1.2 s into the next, 3.6 s apart (a view the eye can read), while its own
    // card answers with a soft lift and its halo. The frame comes into the shot with the
    // camera's one move; the pictures are made one at a time, seconds before their beat.
    var heroBox = ctx.el('uvhero');
    var hframe = glass('hframe'), hpicbox = ctx.el('hpic');
    var hwords = ctx.el('hwords');
    var lays = [];
    [['unit_s', 2300], ['unit_1', 2440], ['unit_2', 2580]].forEach(function (u, i) {
      if (!ctx.has(u[0])) return;
      var lay = ctx.el('hlay' + (i === 0 ? ' on' : ''));
      lays.push({ el: lay, card: i });
      ctx.add(hpicbox, lay);
      soon(u[1], function () { var p = ctx.picture(u[0]); if (p) ctx.add(lay, p.el); });
    });
    var sTxt = ctx.text('suit', { cls: 'hsmall' }), wTxt = ctx.text('ways', { cls: 'hbig' });
    ctx.add(hwords, sTxt, wTxt);
    // (hvig: the soft dark corners of the frame once it fills the screen)
    ctx.add(heroBox, ctx.el('hhalo'), hframe, hpicbox, ctx.el('hscrim'), ctx.el('hvig'), hwords);
    // (it stands beside the row, in the same plane: off the shot's right edge until the camera
    // turns to it; the gate keeps it unseen until "to suit" in any case)
    var hero0 = obj(heroBox, { x: 63, y: ROW_Y, z: ROW_Z, ry: -6, s: 1, op: 0 });
    hero0.push = 1;
    // the frame filling the screen: the camera square to it, its middle at the middle of the
    // screen, a little larger than the screen (the camera's wander never shows an edge)
    var FULL_S = 2.2;
    var HERO_FULL = { x: 63, y: ROW_Y - 28.1 / 2 - (56.25 / 2 - 25.4) / FULL_S, z: ROW_Z - P * (1 - 1 / FULL_S), yaw: 0 };
    // the room's sharp copy (see sharpCopy): the same three interiors in a plain 2D layer twice
    // the frame's size, standing over the frame as it fills the screen
    var roomBox = ctx.el('room'), rpic = ctx.el('rpic'), rlays = [];
    ctx.add(roomBox, rpic, ctx.el('rvig'));
    [['unit_s', 2360], ['unit_1', 2500], ['unit_2', 2640]].forEach(function (u, i) {
      if (!ctx.has(u[0])) return;
      var lay = ctx.el('hlay' + (i === 0 ? ' on' : ''));
      rlays.push(lay);
      ctx.add(rpic, lay);
      soon(u[1], function () { var p = ctx.picture(u[0]); if (p) ctx.add(lay, p.el); });
    });
    var roomCopy = sharpCopy(roomBox, 100, { left: 50 - 25 * FULL_S, top: 56.25 / 2 - 14.05 * FULL_S, width: 50 * FULL_S });
    movers.push(function () {
      var ps = 'scale(' + hero0.push.toFixed(4) + ')';
      hpicbox.style.transform = ps; rpic.style.transform = ps;
      var v = onScreen(hero0.x, hero0.y - 28.1 / 2, hero0.z, hero0.ry);
      var l = v.sx - 25 * v.s, tp = v.sy - 14.05 * v.s;
      var cov = r.full && v.frontal && v.s < 900 ? covers(l, tp, 50 * v.s, 28.1 * v.s, 2) : 0;
      roomCopy(l, tp, 50 * v.s, cov);
      gradeOver(cov);
      // the frame's words are gone before the frame has grown enough to carry them down to the
      // subtitle box's zone (on the camera's own clock: their departure is the core's, CSS time,
      // and a slow page or a fast clock must not let the push outrun it)
      var wk = r.full ? 1 - EASE.sine(clamp((v.s - 0.72) / 0.22, 0, 1)) : 1;
      if (Math.abs(wk - wordsOp) > 0.005) { wordsOp = wk; hwords.style.opacity = wk.toFixed(3); }
    });
    var wordsOp = 1, rowFade = 1;
    // and the row of cards steps back into the dark as the camera passes it -- gone before
    // their names could reach that zone (they grow with the frame: the same depth). Looked at
    // before the world is placed: the cards' light is set as they are placed, in this frame
    sights.push(function () {
      var v = onScreen(hero0.x, hero0.y - 28.1 / 2, hero0.z, hero0.ry);
      rowFade = r.full ? 1 - EASE.sine(clamp((v.s - 0.74) / 0.3, 0, 1)) : 1;
    });
    var cycle = null;
    function showLay(n) {
      lays.forEach(function (l, i) { l.el.classList.toggle('on', i === n); });
      rlays.forEach(function (l, i) { l.classList.toggle('on', i === n); });
      cards.forEach(function (c, i) {
        var mine = lays[n] && lays[n].card === i;
        c.box.classList.toggle('match', mine);         // (its lift and halo are the CSS's: a class
                                                       // change animates the same after the line)
      });
    }
    // the cycle keeps the frame's own time while she speaks -- and stops when the camera goes
    // into the frame after her line: the room it lands in is the one held, still, until the next
    // line comes in over it (owner 19 Nov: the blend into the next line must carry one picture;
    // shot with the recording's hold, a change that began just before the press dissolved inside
    // the blend). Nor does any change start once this slide is going down.
    ctx.frame(function (t) {
      if (!cycle || lays.length < 2 || cycle.frozen) return;
      if (cycle.next === null) cycle.next = t + 3600;
      if (ctx.root.classList.contains('cs-out')) return;
      if (t >= cycle.next) { cycle.n = (cycle.n + 1) % lays.length; cycle.next = t + 3600; showLay(cycle.n); }
    });
    function intoTheRoom(now) {
      r.full = true;
      if (cycle) cycle.frozen = true;
      heroBox.classList.add('full');
      roomBox.classList.add('full');
      numBox.classList.remove('up');
      if (fig) fx.depart(fig);
      if (sTxt) fx.depart(sTxt);
      if (wTxt) fx.depart(wTxt);
      if (now) {
        hero0.ry = 0; hero0.op = 1; ambient.k = 0.4;
        cam.x = HERO_FULL.x; cam.y = HERO_FULL.y; cam.z = HERO_FULL.z; cam.yaw = 0;
        return;
      }
      go(cam, HERO_FULL, 3000, 'sig');
      go(hero0, { ry: 0 }, 2600, 'sig');
      go(ambient, { k: 0.4 }, 2400, 'sig');
    }

    // camera: a slow dolly from the back of the court
    cam.z = late ? -2 : -15;
    go(cam, { z: -2 }, 6000, 'out');

    var r = {
      number: function () {
        // staging: the court dims and the camera draws a breath back, then pushes in gently
        // while the phrase lights up (the core's arrival); its plate comes with it, its halo
        // blooms a beat after (follow-through)
        dimHolos(0.4, 700);
        go(cam, { z: cam.z - 1.1 }, 200, 'sine');
        then(200, function () { go(cam, { z: 0 }, 2300, 'enter'); });
        numBox.classList.add('up');
        fx.depart(kick);                                 // (the kicker cools and fades, the core's way out)
      },
      studios: function () {
        // the phrase goes up to the top and becomes the header (one move, the film's curve)
        numBox.classList.add('up', 'hdr');
        world.classList.add('hdron');
        dimHolos(0.26, 900);
        stage(0);
        // the three cards gather into the row on arcs, left to right (80 ms apart): the
        // studios lit, the other two waiting in the dark. The camera settles and holds.
        [0, 1, 2].forEach(function (i, n) {
          var c = cards[i];
          c.live = 0;
          then(100 + n * 80, function () { go(c, { live: 1 }, 800, 'enter'); });
        });
        cards[0].scan(260);
        go(cam, { z: 2 }, 2000, 'sig');
      },
      onebr: function () {
        stage(1);
        cards[1].scan(200);
      },
      twobr: function () {
        stage(2);
        cards[2].scan(200);
      },
      suit: function () {
        // ONE camera move, eased at both ends: back and across to the right, so the row of
        // types settles on the left and the interiors come in beside it; everything in the
        // world moves with the camera, nothing on its own (the cards used to fan out, the
        // hero to fly in from the depth and the camera to turn, all at once)
        cards.forEach(function (c) { c.lit(true); go(c, { lum: 1 }, 600, 'sig'); });
        go(cam, { x: 27.2, y: -3, z: 42, yaw: 0 }, 2800, 'sig');
        go(hero0, { op: 1 }, 900, 'sig');
        go(hero0, { push: 1.08 }, 11000, 'sine');
        dimHolos(0.18, 1400);
        cycle = { next: null, n: 0, frozen: false };
        showLay(0);
      },
      // her line is over ("... to suit different ways of living."): the camera goes INTO the
      // interiors' frame -- one eased dolly, 3 s -- the row of cards leaves the shot on the left
      // with it, the header and the frame's words cool away, and the frame grows past the
      // screen's edges: the held frame (while Khun Miki asks) is the owner's interiors, full
      // screen, one at a time, and nothing else (owner 19 Nov, 30.png: the next line came in
      // over cards, header, frame and words, "transition ตรงนี้ดูไม่สมูท")
      _after: function () { if (!cycle) r.suit(); intoTheRoom(false); },
      _final: function () { if (!cycle) r.suit(); intoTheRoom(true); },
      full: false
    };
    return r;
  }

  // ======================================================================== views
  var WINDOWS = {
    sea: { k: 'sea', x: -32.4, z: -6.8, ry: 25, plates: ['seaplate'] },
    pool: { k: 'pool', pic: 'pool_pic', x: 0, z: -15.1, ry: 0, plates: ['poolplate', 'poolplate2'] },
    above: { k: 'above', pic: 'above_pic', x: 32.4, z: -6.8, ry: -25, plates: ['aboveplate', 'aboveplate2'] }
  };
  // (start and end stand a little lower and the start a little further back than they did:
  // the question at the top is a headline now, and the tops of the windows ran into it)
  var VIEW = {
    start: { x: 0, y: -1.5, z: -7.5, yaw: 0 },
    sea: { x: -19, y: -1.6, z: 8.9, yaw: -26 },
    pool: { x: 0, y: -1.8, z: 13, yaw: 0 },
    above: { x: 19, y: -1.6, z: 8.9, yaw: 26 },
    end: { x: 0, y: -1.4, z: 0, yaw: 0 }
  };
  // An eye in front of a window, square to it, `d` from the middle of its pane; `y` = its height
  // (by default the pane's middle at the middle of the screen)
  function eyeAt(kd, d, y) {
    var cy = WIN_Y - PANE_H / 2;
    return { x: kd.x + d * Math.sin(rad(kd.ry)), z: kd.z + d * Math.cos(rad(kd.ry)), yaw: -kd.ry,
             y: y === undefined ? cy - (56.25 / 2 - 25.4) * d / P : y };
  }
  function views() {
    // (fix round 19 Nov, measured: the line's cut lost 67-83 ms at 2552 px, every first run --
    // the page decoding its two views, ~97 ms each, on the raster workers while the cut needed
    // them (a trace: GpuImageDecodeCache::DecodeImage x2 in the cut). They are made after the
    // cut now, one at a time (`when`, see `soon`), behind their closed curtains -- opaque,
    // 2 x 50.4% of the pane -- and are seen when the curtains part on their beats, seconds later.)
    var pl = pool(110, 44, 0.42); pl.z = -28;
    var win = { sea: makeWindow(WINDOWS.sea), pool: makeWindow(Object.assign({ when: 1700 }, WINDOWS.pool)),
                above: makeWindow(Object.assign({ when: 2300 }, WINDOWS.above)) };
    var names = ['sea', 'pool', 'above'];
    // (while the camera tours the windows their words are the caption's; the labels under the
    // panes and the word in the sea's window wait for the end, when all three stand together)
    names.forEach(function (n) { win[n].box.classList.add('tour'); });
    // (the windows' glows come up a moment after the cut, not in it: the cut-in of this line
    // measured 67-100 ms stalls on the GPU, rastering the whole opening at once -- 19 Nov)
    if (!late) { ctx.root.classList.add('uv-cold'); soon(700, function () { ctx.root.classList.remove('uv-cold'); }); }
    watchWindows(names.map(function (n) { return win[n]; }));
    sights.push(function () { ambient.k = 1 - 0.8 * EASE.sine(clamp((zoomMost - 1.3) / 2.2, 0, 1)); });
    var top = topline('title');

    // The caption: the words of the window the camera is in, at the top left, on a soft dark
    // plate (a picture is behind them while the camera is in it). One group per window, the
    // window's own two labels; a group comes up when the camera turns to its window, a line
    // when its words are said (the core keeps each hidden until her voice reaches it anyway).
    // (built a second after the cut, not in it: perf 19 Nov measured a 67 ms stall at this line's
    // cut once the caption and the sea's light layer were built with the opening -- the caption is
    // first needed seconds later, on "a view of the sea")
    var capBox = null, caps = {}, capWant = null, capIn = {};
    soon(900, function () {
      capBox = ctx.el('caps');
      ctx.add(capBox, ctx.el('cplate'));
      names.forEach(function (n) {
        var grp = ctx.el('cap'), lines = [];
        WINDOWS[n].plates.forEach(function (k, i) {
          var t = ctx.text(k, { cls: i ? 'cap2' : 'cap1' });
          if (!t) return;
          var l = ctx.el('capl');
          ctx.add(l, t);
          ctx.add(grp, l);
          lines.push(l);
        });
        ctx.add(capBox, grp);
        caps[n] = { el: grp, lines: lines };
      });
      ctx.add(ctx.stage, capBox);
      caption(capWant);
      Object.keys(capIn).forEach(function (key) { var p = key.split(':'); capLine(p[0], +p[1]); });
    });
    function caption(name) {
      capWant = name;
      if (!capBox) return;
      names.forEach(function (n) { caps[n].el.classList.toggle('on', n === name); });
      capBox.classList.toggle('on', !!name);
    }
    function capLine(name, i) {
      capIn[name + ':' + i] = true;
      var c = caps[name];
      if (c && c.lines[i]) c.lines[i].classList.add('in');
    }

    // The framings, as eyes: IN = the window's pane filling the screen; OUT = back from it, the
    // whole window in the shot and clear of the subtitle box's zone (its foot at ~43cu) -- the
    // turn from one window to the next is made out there; the start and the end as they were
    // (the three windows). A tour = out of one window, the turn, into the next: one flight.
    var IN = {}, OUT = {};
    names.forEach(function (n) { IN[n] = eyeAt(WINDOWS[n], P / S_ZOOM); OUT[n] = eyeAt(WINDOWS[n], 70, -5.3); });
    // The sea has no picture to go into (no checked view of the sea exists -- the index has only
    // street maps, the owner's renders show none): a short, framed close look instead -- the whole
    // window large in the shot (1.45x its size, its foot clear of the subtitle box), its word in
    // it -- never a screen of plain blue (main session 19 Nov, on the first take of this round)
    IN.sea = eyeAt(WINDOWS.sea, P / 1.45, WIN_Y - (43.2 - 25.4) / 1.45);
    var START = eyeOf(VIEW.start), END = eyeOf(VIEW.end);
    eye = late ? { x: END.x, y: END.y, z: END.z, yaw: END.yaw } : { x: START.x, y: START.y, z: START.z, yaw: START.yaw };
    camFromEye();
    go(eye, { z: START.z + 3 }, 5000, 'out');
    function tour(from, to) { fly([OUT[from], OUT[to], IN[to]], [0, 0.34, 0.66, 1], 3100); }
    // the one being named is the hero: the windows already seen step back behind a light veil
    function seen(name) { names.forEach(function (n) { if (n !== name && !win[n].box.classList.contains('closed')) win[n].back(true); }); win[name].back(false); }
    // a window comes into the shot during the turn: its curtains part then, its words come up
    function arriveAt(name, x) {
      win[name].open(true);
      seen(name);
      caption(name);
      capLine(name, 0);
      go(pl, { x: x }, 1800, 'sig');
    }
    function together(now) {
      // all three, lit, their labels under them and the sea's word in its window, the question
      // back at the top: the frame the next line (s05_l06) opens on
      caption(null);
      names.forEach(function (n, i) {
        win[n].open(true, false, now);
        win[n].back(false);
        then(now ? 0 : i * 90, function () { win[n].box.classList.remove('tour'); win[n].plate(0); win[n].plate(1); });
      });
      win.sea.box.classList.add('wordon');
      if (top) { top.box.classList.add('on'); top.box.classList.remove('fade'); fx.depart(top.text, false); }
    }
    return {
      ask: function () {
        if (top) top.box.classList.add('on');             // (its plate; the core lights the phrase up)
      },
      lean: function () {
        // "Would you enjoy ...": the camera turns to the sea's window and walks up to it -- it
        // opens as she names it, and the camera comes close (the sea has no picture to go into)
        fly([OUT.sea, IN.sea], [0, 0.5, 1], 2200);
        go(pl, { x: -22 }, 2400, 'sig');
      },
      sea: function () {
        if (top) { top.box.classList.add('fade'); fx.depart(top.text); }
        arriveAt('sea', -22);
        win.sea.box.classList.add('wordon');            // (its word, in the window it names)
      },
      out: function () {
        // "... are you interested in": out of the sea, the turn, into the ground-floor view
        then(400, function () {
          tour('sea', 'pool');
          then(1300, function () { arriveAt('pool', 0); });
        });
      },
      pool: function () { capLine('pool', 0); },
      pool2: function () { capLine('pool', 1); },
      above: function () {
        tour('pool', 'above');
        then(1300, function () { arriveAt('above', 22); });
      },
      end: function () { capLine('above', 1); },
      // her line is over: a while on the view from above, then the camera draws back to all
      // three (one move; after it, the held frame) -- Khun Miki answers over it
      _after: function () {
        then(300, function () {
          caption(null);
          fly([OUT.above, END], [0, 0.4, 1], 3000);
          go(pl, { x: 0 }, 2600, 'sig');
          names.forEach(function (n) { win[n].back(false); });
          then(2300, function () { together(false); });
        });
      },
      _final: function () {
        CH.forEach(function (k) { eye[k] = END[k]; });
        flight = null; camFromEye();
        pl.x = 0;
        together(true);
      }
    };
  }

  // ======================================================================== suggest
  // Fix round 19 Nov (the review: "s05_l06 three answer cards turn to blank slabs, 'Sea'
  // goes, a bare slab alone 0.8-3.0 s"; the rule: keep the held picture until the new line's
  // first beat, then TRANSFORM it). The line opens on the frame the question ended on -- the
  // three windows of s05_l03, open, their views in them (the slide `continues` s05_l03, so
  // its two pictures may stay), "Sea" in its window -- the same windows at the same places
  // under the same camera, so the cut only takes the question's own words away. Then she
  // chooses: on "In that case" the two other views step back behind a veil (the sea is the
  // hero), on "a 2-bedroom unit" they drift out of the shot while the card rises where the
  // middle one stood. Nothing goes blank, nothing is rebuilt.
  // And at the end (review: "s05_l08 the 2-bedroom card jump-cuts centre -> left under the
  // wipe"): on "everyone in the family" the camera turns to the family -- one move -- so the
  // card ends on the left, turned in a little, where the quotation's own unit panel stands in
  // the next line (emma_console, s05_l08: its panel at 7.5..44 x 13.8..41.8cu, turned 13
  // degrees), and the family's pictures on the right, where the quotation comes in. The next
  // line comes in on a card that is already in its place.
  var SUGGEST = {
    // the camera after its turn to the family: the card's model then stands over the model of
    // the quotation's unit panel (shots of both lines laid over each other, 19 Nov: the same
    // place within ~2cu, the same size within ~5%) -- and the card's foot at ~43cu, clear of
    // the subtitle box's zone (43.8cu) with its float
    fam: { x: 23.4, y: -3.3, z: 4.6, yaw: 0 },
    // where the family's pictures stand (world x), the heading above them
    satX: 46.5, turn: 13
  };
  function suggest() {
    var held = ctx.prev && ctx.prev.act === 'units_views' && ctx.prev.state && ctx.prev.state.views ? ctx.prev.state : null;
    // the floor's light as the question left it (under the middle window)
    var pl = pool(110, 44, 0.42); pl.z = -28;
    // the three windows of the question, open, at their places (WINDOWS), their views in them
    var sea = makeWindow(WINDOWS.sea);
    var others = [
      makeWindow({ k: 'pool', pic: 'pool_pic', x: WINDOWS.pool.x, z: WINDOWS.pool.z, ry: WINDOWS.pool.ry, plates: [], copyAt: 1500 }),
      makeWindow({ k: 'above', pic: 'above_pic', x: WINDOWS.above.x, z: WINDOWS.above.z, ry: WINDOWS.above.ry, plates: [], copyAt: 1800 })
    ];
    [sea].concat(others).forEach(function (w) { w.open(true, false, true); });
    // (the sea's word stands in its window, as the question left it)
    sea.box.classList.add('wordon');
    // (the views in the two panes drift against the camera as they did in the question -- the
    // same code: if the question's camera was still drawing back from a pane, the view in it
    // is framed the same way here, and eases to rest with this line's camera)
    watchWindows([sea].concat(others));
    var kick = topline('kicker');
    // (it waits for the question's headline to go: see .top.after in the stylesheet)
    if (kick) kick.box.classList.add('after');
    // the 2-bedroom card, in front: it rises turning in to face the hall (its two keyed
    // pictures are made after the cut-in, one at a time, well before its beat at ~2.9 s --
    // and after the 1.2 s blend the line comes in by: made inside it, at 640/800 ms, the two
    // keyings on the main thread showed as a 50 ms stall there, perf 19 Nov).
    // `turn` = how far it has turned toward the family (the end of the line)
    var card = makeCard({ w: 'wire_2', s: 'solid_2', label: 'twobr', mt: 2, n: 0, at: [1350, 1650] });
    card.turn = 0;
    card.pose = function (q) {
      var r = 1 - clamp(q.live, 0, 1);
      // (it rises 2cu, not 5: its foot stands at ~42cu, and the subtitle box's zone begins at 43.8)
      q.x = 0; q.y = FLOOR - 1.8 + 2 * r; q.z = 7.8 - 3 * r * r; q.ry = 7 * r * r + q.turn; q.s = 1; q.op = q.live * q.lum;
      // ambient: the card floats while it stands (5.5 and 8 s sines)
      q.y += 0.24 * Math.sin(clock / 880); q.ry += 0.6 * Math.sin(clock / 1270 + 1.3);
    };
    // two satellites on the right: what the family gets (large photographs: asked for one
    // after the other once the card's pictures are in, seconds before their beats)
    function sat(pk, text, y, when, cls) {
      var box = ctx.el('pcard ' + cls);
      var fr = ctx.el('pframe'), slot = ctx.el('slot');
      soon(when, function () { var pic = ctx.picture(pk); if (pic) ctx.add(slot, pic.el); });
      ctx.add(fr, slot, ctx.el('pscrim'));
      ctx.add(box, ctx.el('phalo'), fr, ctx.el('ptop'), ctx.text(text, { cls: 'pcap' }));
      return obj(box, { x: SUGGEST.satX + 5, y: y + 1.6, z: -7.8 - 7, ry: -30, op: 0 });
    }
    var sLive = sat('living', 'roomtolive', 0.9, 1500, 'p1');
    var sBed = sat('bedroom', 'privacy', 17.2, 2000, 'p2');
    var headBox = ctx.el('head3d');
    ctx.add(headBox, ctx.el('hplate'), ctx.text('family', { cls: 'hd3' }));
    var head = obj(headBox, { x: SUGGEST.satX + 4, y: -17.4 + 1, z: -7.8 - 5, ry: -24, op: 0 });
    // a satellite arrives on an arc: out of the depth on the right, turning in as it lands
    function arrive(o, y, ms) {
      go(o, { op: 1 }, Math.round(ms * 0.55), 'sig');
      go(o, { x: SUGGEST.satX, y: y, z: -7.8, ry: -24 }, ms, 'enter');
    }

    // the camera where the question left it, still drifting in a little (a held frame is never
    // a still; the ambient wander is the frame clock's, the same as the question's)
    var c0 = held && held.cam ? held.cam : VIEW.end;
    cam.x = c0.x; cam.y = c0.y; cam.z = c0.z; cam.yaw = c0.yaw;
    go(cam, { x: 0, y: -1.3, z: 1.2, yaw: 0 }, 4200, 'sig');
    return {
      leave: function () {
        if (kick) kick.box.classList.add('on');       // (its plate; the core lights the phrase up)
        // staging: the sea is the hero -- the other two views step back behind their veil
        others.forEach(function (o, i) {
          o.back(true);
          then(i * 90, function () { go(o, { z: o.z - 3, y: o.y + 0.5 }, 1400, 'sig'); });
        });
        go(pl, { x: -18, z: -14 }, 2400, 'sig');
      },
      card: function () {
        card.lit(true); card.live = 0; go(card, { live: 1 }, 800, 'enter'); card.scan(320);
        if (kick) { kick.box.classList.add('fade'); fx.depart(kick.text); }
        // the two other views drift out of the shot (back and away, the middle one first:
        // the card rises where it stood) -- a slow exit, never a pop
        others.forEach(function (o, i) {
          then(i * 120, function () {
            go(o, { op: 0, z: o.z - 14, y: o.y + 2.5, x: o.x + (i ? 9 : 0) }, 1500, 'sig');
          });
        });
      },
      sea: function () {
        sea.open(true);
        sea.plate(0);
        go(pl, { x: -14, z: 2 }, 1800, 'sig');
      },
      family: function () {
        // ONE camera move: it turns to the family -- the card goes to the left, turning in a
        // little toward them, the sea window leaves the shot on the left; the line first, the
        // living room 100 ms after
        arrive(head, -17.4, 800);
        then(100, function () { arrive(sLive, 0.9, 900); });
        go(cam, SUGGEST.fam, 2800, 'sig');
        go(card, { turn: SUGGEST.turn }, 2800, 'sig');
        go(pl, { x: 4, z: 2 }, 2800, 'sig');
        sea.back(true);
        go(sea, { op: 0 }, 1800, 'sig');
      },
      privacy: function () {
        arrive(sBed, 17.2, 900);
      },
      _final: function () { sea.plate(0); }
    };
  }

  var made = null;
  if (mode === 'units') made = units();
  else if (mode === 'views') made = views();
  else if (mode === 'suggest') made = suggest();
  made = made || {};

  return {
    beat: function (b, isLate) {
      var fn = made[b['do']];
      instant = !!isLate || late;
      if (typeof fn === 'function') fn(b, isLate);
      instant = late;
    },
    progress: function () { },
    // The line is over. What was waiting happens now; then the act's move after the line
    // (`_after`: into the interiors / back to the three windows) plays at its own pace -- when
    // this screen was showing the line as it ended (frames were drawn). A screen that comes up
    // on a line already over gets the frame that move ends on, at once (`_final`).
    settle: function () {
      late = true; instant = true; flush();
      if (made._after && framesSeen > 0) {
        real = true;
        try { made._after(); } finally { real = false; }
      } else if (made._final) made._final();
    },
    // (the question's frame -- `views` -- is where the suggestion opens: its windows, its camera;
    // `full` = the units line ended inside its interiors' frame)
    handover: function () { return { cam: { x: cam.x, y: cam.y, z: cam.z, yaw: cam.yaw }, views: mode === 'views', units: mode === 'units', full: !!made.full }; }
  };
});
