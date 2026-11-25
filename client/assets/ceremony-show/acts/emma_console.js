/* Act "emma_console" -- the cues where Emma herself is the subject (ceremony film, 17 Nov 2026).
   The screen is her own console: two rules meeting at a notch, glass panels floating over the
   horizon arc, each thing she can do a panel with a real picture of the project in it; the
   panels turn, the camera visits them, her words light up.

   Everything the hall can read comes from the slide through ctx (words: ctx.text; pictures and
   the clips: ctx.picture / ctx.video). What is drawn here is shapes: glass, bars of light,
   rings, threads, status lights. The quotation is bars of light -- never a number, never a price.

   One file serves ten cues through options.mode:
     intro (s02_l03)  caps (s02_l05)  quote (s05_l08 / l11 / l13 / backup)
     wall (s06_l05 / l07)  doors (s07_l02)  water (s07_l07)
   A cue's slide names the verbs (`do`) of its beats; the verbs below are this file's own.

   Owner, 18 Nov afternoon, on the glowing star-ball that used to stand for her here: "ตรงนี้อีก
   คืออะไรไม่ต้องเอาขึ้นมาเลย ก็ให้ตัวหุ่นที่โดดน้ำมายืนที่แท่นเลย" -- the ball is out of this act
   everywhere (the console's own marks are plain UI now: a notch, status lights, a voice button),
   and in the wall cues EMMA HERSELF stands in front of the pictures: the approved 3D model, the
   one that dives into the pool, rendered offline by scripts/render_emma_console.py into one
   transparent clip (preen 0-3 s, idle loop 3-7 s, cheer 7-9 s; every seam the same pose). The
   clip is her performance in place; where she stands, her crouch, her hop onto the podium and
   its squash are this act's moves of the clip's box.

   Type (owner 18 Nov, Apple's MacBook Pro film): one face (Inter), sentence case, normal
   tracking, moderate sizes; words light up left to right (the core's arrival) and cool out the
   same way (fx.depart). Typed letters are kept only where words are typed INTO her console (the
   prompt bar) and on the joke, where the letters follow her voice so the punchline is never
   read before she says it.

   Motion (the film's identity -- Premium; the superfan quiz alone Playful):
   - one entrance direction: a panel RISES into place; a satellite card slides OUT from behind
     the panel it belongs to; a pill rises
   - anticipation: the console's notch answers first, the panel moves a beat later
   - follow-through: a glass panel's glow edge arrives after the panel has landed (`lit`)
   - arcs: a panel that travels across the stage lifts off its line mid-way (`lift`)
   - staging: one hero at a time; the others dim and step back (`dim`)
   - ambient: every held shot breathes (a slow camera drift under the panels, a glint along a
     glass edge, the hologram's bands) so a hold never reads as a freeze
   A keyframe move never plays when the screen joined late (`late`): the state is simply there. */
CeremonyShow.register('emma_console', function (ctx) {
  var fx = ctx.fx, stage = ctx.stage, late = ctx.late;
  var mode = ctx.opt('mode', 'caps');
  var verbs = {}, typers = [], handover = { mode: mode };
  var endHook = null;                  // what the line does once it is over (options.end), if anything

  // every class of this act carries the prefix ec- (the page's own stylesheet has classes called .topbar, .rail ...)
  function P(c) { return String(c || '').split(' ').map(function (t) { return !t ? '' : (t.indexOf('cs-') === 0 ? t : 'ec-' + t); }).join(' '); }
  var root = ctx.root;                                     // the slide element: the states of the whole scene live on it
  function cu(n) { return 'calc(' + n + ' * var(--cu))'; }
  function box(el, x, y, w, h) {
    if (!el) return el;
    if (x !== null && x !== undefined) el.style.left = cu(x);
    if (y !== null && y !== undefined) el.style.top = cu(y);
    if (w !== null && w !== undefined) el.style.width = cu(w);
    if (h !== null && h !== undefined) el.style.height = cu(h);
    return el;
  }
  function vars(el, o) { Object.keys(o).forEach(function (k) { el.style.setProperty('--' + k, String(o[k])); }); return el; }
  // a cut clip at its window of the box (in % of the box: the same place at any stage size)
  function windowed(el, win) {
    if (!win || win.length !== 6 || !win.every(function (v) { return typeof v === 'number' && isFinite(v); })
        || !(win[2] > 0 && win[3] > 0 && win[4] > 0 && win[5] > 0)) return;
    var pc = function (v, of) { return (Math.round(v / of * 1e6) / 1e4) + '%'; };
    set(el, 'cwin');
    el.style.left = pc(win[0], win[4]);
    el.style.top = pc(win[1], win[5]);
    el.style.width = pc(win[2], win[4]);
    el.style.height = pc(win[3], win[5]);
  }
  function div(c, parent) { var e = ctx.el(P(c)); if (parent) ctx.add(parent, e); return e; }
  function set(el, c, yes) { if (!el) return; if (yes === false) el.classList.remove(P(c)); else el.classList.add(P(c)); }
  function only(list, i, c) { list.forEach(function (e, k) { set(e, c, k === i); }); }
  // the line before was one of these cues: her console (notch, rules, menu) is already up -- no
  // second entrance at the cut (the frames of the two lines are the same)
  var cont = !!(ctx.prev && ctx.prev.act === 'emma_console');
  if (cont) set(root, 'cont');
  // a glass panel's glow edge follows it in (the CSS waits --ec-glow-at after the panel moves)
  function lit(el, on) { set(el, 'lit', on); }
  // a panel already up when the line began (the line before left it there): its glow is there too
  function held(el) { set(el, 'lit'); set(el, 'held'); }
  // one arc for this move: the CSS picks the keyframes by parity (p0 / p1), so the next move of
  // the same element plays its own arc; a late screen gets no arc, the state is simply there
  function lift(el) {
    if (!el || late) return;
    el._p = !el._p;
    set(el, 'p1', el._p); set(el, 'p0', !el._p);
  }
  // words: the core lights them up left to right when they can first be seen (their beat, or
  // the act going live). `glow: false` inside a small card or a pill (the halo would spill out
  // of it); `reveal: false` where a box brings its words in with its own entrance.
  function txt(key, cls, o) {
    if (!ctx.has(key)) return null;
    var opt = { cls: P(cls) };
    for (var k in o || {}) opt[k] = o[k];
    return ctx.text(key, opt);
  }
  function words(key, cls, own) { return txt(key, cls, own ? { reveal: false, glow: false } : { glow: false }); }
  function out(el) { if (el) fx.depart(el); }
  // typed letters (her prompt bar, the joke): they come in step with her voice (late = all there)
  function typed(key, cls, o) {
    var opt = { split: 'chars' };
    for (var k in o || {}) opt[k] = o[k];
    var t = ctx.has(key) ? fx.typed(txt(key, cls, opt)) : { el: null, set: function () {}, play: function () {} };
    if (t.el) typers.push(t);
    t.key = key;
    return t;
  }
  function type(t, ms) {
    if (!t || !t.el) return;
    var n = (ctx.slide.texts[t.key] || '').length;
    t.play(ms || Math.max(500, n * 52));
  }
  // things that happen a moment after a beat: at once when the screen joined late
  function seq(list) {
    list.forEach(function (s) { if (late) s[1](); else ctx.after(s[0], s[1]); });
  }
  function S(tag, attrs) { var a = {}; Object.keys(attrs || {}).forEach(function (k) { a[k] = k === 'cls' ? P(attrs[k]) : attrs[k]; }); return ctx.svg(tag, a); }
  function glass(c) { return fx.glass(P(c)); }
  function sat(child, c) { return fx.sat(child, P(c)); }
  function svg(tag, attrs, parent) { var s = S(tag, attrs); if (parent && s) ctx.add(parent, s); return s; }
  function icon(paths, c) {
    var s = S('svg', { viewBox: '0 0 24 24', cls: 'glyph' + (c ? ' ' + c : '') });
    paths.forEach(function (d) { svg('path', { d: d }, s); });
    return s;
  }
  var ICONS = {
    building: ['M3 21V10l5-3v14', 'M8 21V4l7 4v13', 'M15 21v-8l6 2v6', 'M2 21h20'],
    cards: ['M8 8h12v12H8z', 'M4 16V4h12'],
    plan: ['M3 3h18v18H3z', 'M3 12h8', 'M11 3v9', 'M11 16h10', 'M16 12v9'],
    doc: ['M6 3h9l4 4v14H6z', 'M9 12h7', 'M9 16h7', 'M9 8h3'],
    people: ['M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M3 20c0-3.5 2.7-6 6-6s6 2.5 6 6', 'M16 6a2.6 2.6 0 0 1 0 5', 'M17 14c2.4.4 4 2.3 4 5'],
    person: ['M12 9a3.2 3.2 0 1 0 0-6.4A3.2 3.2 0 0 0 12 9z', 'M5 21c0-4 3-7 7-7s7 3 7 7', 'M3 8l3 2', 'M21 8l-3 2'],
    play: ['M8 5v14l11-7z'],
    check: ['M5 12.5l4.5 4.5L19 7.5']
  };

  // ---- shared pieces of the console (plain UI: no character of its own) ----
  // a status light: a ring with a lit dot (on cards, in the window bar, on the document)
  function orb(c) { var o = div('orb' + (c ? ' ' + c : '')); div('orb-core', o); return o; }
  // the bar at the top: two rules meeting at a notch (the notch answers before a panel opens)
  function topbar() {
    var bar = div('topbar');
    div('ln l', bar); div('ln r', bar);
    var n = div('notch', bar); div('notchi', n);
    return bar;
  }
  function ping(bar) { if (!bar || late) return; bar._p = !bar._p; set(bar, 'pp1', bar._p); set(bar, 'pp0', !bar._p); }
  // her voice button on the prompt bar: four level bars
  function voice() { var b = div('bo'); for (var i = 0; i < 4; i++) vars(div('wv', b), { i: i }); return b; }
  // the ambient camera: a layer that breathes slowly under a held shot (transform only, a sine);
  // it starts already moving (a negative delay), so a shot's first second is never still
  function amb(c) { return div('amb' + (c ? ' ' + c : '')); }
  // a glint that runs along a glass panel's top edge now and then (secondary life on a hold)
  function glint(panel, c) { if (!panel) return null; var g = div('edge' + (c ? ' ' + c : ''), panel); div('edgei', g); return g; }
  var menuItems = [];
  function menu() {
    var rail = div('rail');
    ['building', 'cards', 'plan', 'doc'].forEach(function (k, i) {
      var b = div('mi', rail);
      vars(b, { i: i });
      ctx.add(b, icon(ICONS[k]));
      menuItems.push(b);
    });
    return rail;
  }
  function menuAt(n) { menuItems.forEach(function (b, i) { set(b, 'on', i === n - 1); set(b, 'was', i < n - 1); }); }
  // a heading: one phrase, lit by the core when its beat lifts it. (Fix round 19 Nov, review:
  // the small kickers over the headings -- "This page", "On a single screen", "I'm preparing",
  // "I can share" -- were 1-1.5% of the screen's height, unreadable at five metres. They are
  // dropped: the heading says the thing, her voice says the rest. `kk` is kept in the calls so
  // the slides read the same.)
  function head(kk, tk) {
    var w = div('head'), t = txt(tk, 'cs-type-h2');
    ctx.add(w, t);
    w._k = null; w._x = t;
    return w;
  }
  function headOut(w) { if (!w) return; set(w, 'out'); out(w._k); out(w._x); }
  // a glass panel with a picture inside it (rounded, clipped)
  function pix(picEl, c) { var p = div('pix' + (c ? ' ' + c : '')); if (picEl) ctx.add(p, picEl); return p; }
  function pic(key, o) { return ctx.has(key) ? ctx.picture(key, o) : null; }
  // a model photographed on black: keyed to alpha by the core (no blend mode on our own ground)
  function model(key, o) { var m = {}; for (var k in o || {}) m[k] = o[k]; m.keyed = 'black'; return pic(key, m); }
  function picEl(key, o) { var p = pic(key, o); return p ? p.el : null; }

  // ---- the document: bars of light, never a number ----
  function document_(rowCount) {
    var d = glass('dc fx'), h = div('dc-h', d), body = div('dc-rows', d), rows = [];
    ctx.add(h, orb('mini'), txt('head', 'cs-type-h2 dc-t', { glow: false }));
    var th = div('dc-thumb', h);
    var wp = pic('thumb');
    if (wp) ctx.add(th, wp.el); else ctx.add(th, icon(ICONS.plan));
    div('dc-sub', h);
    for (var i = 0; i < rowCount; i++) {
      var r = div('rw todo', body);
      vars(r, { i: i });
      div('lb', r); div('vb', r);
      rows.push(r);
    }
    var scan = div('scan', body), ring = div('ring', d), spin = div('spin', ring);
    var s = S('svg', { viewBox: '0 0 40 40', cls: 'rsv' });
    svg('circle', { cx: 20, cy: 20, r: 16, cls: 'rtrack' }, s);
    svg('circle', { cx: 20, cy: 20, r: 16, cls: 'rfull' }, s);
    var arc = svg('circle', { cx: 20, cy: 20, r: 16, cls: 'rarc' }, s);
    svg('path', { d: 'M12 21 L18 27 L29 14', cls: 'rtick', pathLength: 1 }, s);
    ctx.add(spin, s);
    glint(d);
    return { el: d, rows: rows, scan: scan, ring: ring, arc: arc };
  }
  function fill(doc, n) {                                  // rows 0..n-1 done, row n under the scan line, the rest waiting
    doc.rows.forEach(function (r, i) { set(r, 'done', i < n); set(r, 'now', i === n); set(r, 'todo', i > n); });
    doc.el.style.setProperty('--scan', String(Math.min(n, doc.rows.length - 1)));
  }
  // a thread: a line that draws itself, light packets run along it
  function thread(parent, x, y, w, c) {
    var t = div('th' + (c ? ' ' + c : ''), parent);
    box(t, x, y, w, null);
    div('thl', t); div('thp', t); div('thp b', t);
    return t;
  }
  // the facade of s02_l03, held: the picture lit, its wash flowing, its sparkles -- the frame
  // that line ends on, built again by the line after it (s02_l05) as its backdrop, so the new
  // line comes in OVER the held picture (fix round 19 Nov: an act handing over to itself must
  // never blank what was on the screen). `st` = the line before's handover: its camera push
  // and its wash go on from where they were.
  var SPK = [[8, 20], [18, 62], [24, 9], [47, 12], [63, 8], [78, 26], [88, 58], [95, 14]];
  // (owner 19 Nov on version 6, s02_l03: "พื้นหลัง ทำไมไม่โชว์ตึก A เลย" -- why isn't the background
  // Building A? The hologram facade was another building's close-up. He chose a REAL picture of
  // Building A (options.photo): his own final render, cropped to the building. A photograph gets
  // no hologram dressing -- no sparkles, no violet wash flowing along slats -- but a grade of its
  // own (darker sky round the title, the lower floors graded down under the subtitle box); its
  // reveal stays the band sweep, a soft warm front of light passing left to right)
  var PHOTO = !!ctx.opt('photo', false);
  // where the photo's camera is, t ms after its push began (the CSS: emma_console-fpush 20 s, then
  // emma_console-fbreathe 11 s alternating, both on the sine curve cubic-bezier(.37, 0, .63, 1)) -- the
  // breath is the wrapper inside the camera (.ec-fbr) since 23 Nov: the numbers here are the two composed,
  // so a change to emma_console-fbreathe is a change here in composed terms (push end x breath)
  function sine(x) {
    var lo = 0, hi = 1, m = 0;
    for (var n = 0; n < 18; n++) { m = (lo + hi) / 2; var bx = 3 * m * (1 - m) * (1 - m) * .37 + 3 * m * m * (1 - m) * .63 + m * m * m; if (bx < x) lo = m; else hi = m; }
    return 3 * m * m * (1 - m) + m * m * m;
  }
  function pushAt(t) {
    var a, b, e;
    if (t < 20000) { a = [0, 0, 1]; b = [-.8, .3, 1.06]; e = sine(Math.max(0, t) / 20000); }
    else { var u = ((t - 20000) % 22000) / 11000; a = [-.8, .3, 1.06]; b = [-1.1, .42, 1.075]; e = sine(u > 1 ? 2 - u : u); }
    function at(i) { return +(a[i] + (b[i] - a[i]) * e).toFixed(4); }
    // (3D on purpose: the held photo stays a layer of its own -- painted into the line's layer, every
    // word lighting up over it re-rastered the photo too; measured on the 2552 screen, a layer of
    // its own that neither moves nor fades under her first station: no dropped run in 3)
    return 'translate3d(calc(' + at(0) + ' * var(--cu)), calc(' + at(1) + ' * var(--cu)), 0) scale(' + at(2) + ')';
  }
  function heldFacade(st) {
    if (!ctx.has('facade')) return null;
    // (the photo comes in already stepped back to a third: it darkens THROUGH the cut's dissolve
    // instead of at her first station -- on the stage her first words and the cut nearly coincide,
    // and a full-screen fade starting with the station's rise cost a dropped run on the 2552 screen)
    var f = div('facade held on dim f-lit f-flow' + (PHOTO ? ' photo dim2' : '')), cam = div('fcam', f), p = pic('facade');
    ctx.add(div('fall', cam), p ? p.el : null);
    if (PHOTO) { div('fgrade', f); div('fveil', f); }
    else {
      var wash = div('wash', f); div('washi', wash);
      var sp = div('spks', f);
      SPK.forEach(function (q, i) { var s = div('spk twa', sp); s.style.left = q[0] + '%'; s.style.top = q[1] + '%'; vars(s, { k: i }); });
    }
    div('fshade', f);
    var now = Date.now();
    // (the photo: measured on the 2552 screen, a held photo that kept pushing cost this line a
    // dropped run as its first station rose -- standing still, none. It is set where the line
    // before's push is at the cut, and stays: behind her stations it is a backdrop at a third)
    if (st && typeof st.fsweep === 'number' && PHOTO) cam.style.transform = pushAt(now - st.fsweep);
    else if (st && typeof st.fsweep === 'number') { set(f, 'f-sweep'); f.style.setProperty('--fpd', Math.min(0, st.fsweep - now) + 'ms'); }
    if (st && typeof st.fflow === 'number') f.style.setProperty('--fwd', Math.min(0, st.fflow - now) + 'ms');
    return f;
  }

  // a small card: a status light and its words, padded all round (owner 18 Nov, a crop of
  // s02_l05: the second line ran to the card's edge) -- the card is as wide as its words
  function card(c, lead, main, sub) {
    var g = glass('card fx ' + c), body = div('cbody', g);
    ctx.add(body, words(lead, 'cs-type-label clead'), words(main, 'cmain'), words(sub, 'cs-type-label csub'));
    ctx.add(g, orb('mini'));
    return g;
  }

  // =====================================================================================
  // intro -- s02_l03: she switches her console on
  // =====================================================================================
  function intro() {
    ctx.bg('full');
    var top = topbar(), giantW = div('giantw intro'), giant = txt('giant', 'cs-type-giant gi');
    ctx.add(giantW, giant);
    var hello = txt('hello', 'cs-type-h2'), every = txt('every', 'cs-type-h2');
    var hw = div('head hello'), ew = div('head every');
    ctx.add(hw, hello); ctx.add(ew, every);
    // (owner 19 Nov, a screenshot of 0:22 with the project's logo card boxed in red: "รู้สึก
    // ไม่เข้าเลย เอาออกไหม" -- the logo clip in a glass window in the middle of the hologram facade
    // did not fit. It is out. The facade is the picture; her words stand on it as a title, the
    // way a film puts its title over the shot: "I represent Embassy World" lights up left to
    // right as the light sweeps the facade left to right behind it, "the smart assistant"
    // under it, on a soft shade that knocks the facade's lines back behind the letters (drawn:
    // a gradient, no blur). The two groups she works for hang from the title on a fork of
    // light: a stem drops from it, a rule draws out to each side, each card slides out along
    // its rule -- the title, the fork and the cards one figure, centred under her name.)
    var shade = div('repshade');
    var rep = txt('rep', 'cs-type-h2 rep-a'), smart = txt('smart', 'cs-type-h2 rep-b');
    var title = div('rept'); ctx.add(title, rep, smart);
    // the two groups she works for: each slides out from the fork's stem, along its rule
    function side(c, lead, textKey, ic) {
      var s = card('gsat ' + c, lead, textKey, null), g = div('gic', s);
      ctx.add(g, icon(ICONS[ic]));
      return s;
    }
    // (fix round 19 Nov: the cards' small leads "For" / "And" were 1.3cu -- unreadable from
    // the hall, and the cards say who without them: dropped)
    var left = side('gleft', null, 'sales', 'people'), right = side('gright', null, 'agents', 'person');
    var stem = div('fstem'), lt = div('tline l'), rt = div('tline r');
    // the facade (owner 18 Nov, a reference reel: "โมชั่นเปิดตัวแบบนี้" -- a dark slatted shop
    // front lit band by band by a glittering wave, then colour flowing along the slats). Ours
    // is the project's own hologram facade (its curved balcony bands are the slats), dark at
    // first, then lit band by band behind a glittering front. Its lit state is the picture
    // itself (a picture of a building's REAL render is not backed by this sentence: only an
    // establishing picture may stand where she names the project). Transform and opacity only:
    // each band is a wipe (a clip box moving one way, the picture the other). Lit, the camera
    // keeps pushing in on it, slowly (the hold after the sweep is never a still).
    var facade = null, fcam = null, bands = [], spks = [];
    if (ctx.has('facade')) {
      facade = div('facade' + (PHOTO ? ' photo' : ''));
      fcam = div('fcam', facade);
      // (the photo breathes on a wrapper of its own once the push is over: two transform animations on
      // one element run off the compositor -- the whole photo was rastered again every frame, 23 Nov)
      var fin0 = PHOTO ? div('fbr', fcam) : fcam;
      var base = pic('facade');
      ctx.add(div('fbase', fin0), base ? base.el : null);
      // (owner 19 Nov, the photo: on a smooth dusk sky the six bands' edges showed as hairlines --
      // two antialiased edges meeting under the push. Each band now reaches a hair (OV, % of the
      // stage) into the next one, its picture placed by the same numbers, so the same pixels
      // overlap; once the sweep is over the whole picture (fwhole) takes over and the bands go)
      var NB = 6, OV = PHOTO ? 0.25 : 0, BH = 100 / NB + OV;
      for (var k = 0; k < NB; k++) {
        var band = div('fband', fin0), fin = div('fbin'), bp = pic('facade');
        band.style.top = (k * 100 / NB) + '%'; band.style.height = BH + '%';
        fin.style.top = (-k * (100 / NB) / BH * 100) + '%'; fin.style.height = (100 / BH * 100) + '%';
        if (bp) ctx.add(fin, bp.el);
        var w = fx.wipe(fin, { dir: 'right', from: 0 });
        div('glit', w.el);                                // the glitter riding the front (it moves with the clip box)
        ctx.add(band, w.el);
        bands.push(w);
      }
      if (PHOTO) { var whole = pic('facade'); ctx.add(div('fall fwhole', fin0), whole ? whole.el : null); }
      div('gather', facade);
      if (PHOTO) { div('fgrade', facade); div('fveil', facade); }
      else {
        var wash = div('wash', facade); div('washi', wash);
        var sp = div('spks', facade);
        SPK.forEach(function (p, i) {
          var s = div('spk', sp);
          s.style.left = p[0] + '%'; s.style.top = p[1] + '%';
          vars(s, { k: i });
          spks.push(s);
        });
      }
      div('fshade', facade);                              // the bottom of the shot graded down under the subtitle
    }
    var a = amb('intro');
    ctx.add(a, shade, stem, lt, rt, title, left, right);
    ctx.add(stage, giantW, facade, top, hw, ew, a);
    verbs.everyone = function () { set(hw, 'out'); out(hello); set(ew, 'on'); ping(top); };
    verbs.name = function () { set(giantW, 'on'); };
    // where a band's front is at a share x of the stage's width (the wipe's ease, in and out)
    function frontAt(x) {
      var lo = 0, hi = 1;
      for (var n = 0; n < 14; n++) { var m = (lo + hi) / 2; if (m * m * (3 - 2 * m) < x) lo = m; else hi = m; }
      return (lo + hi) / 2;
    }
    function lightFacade() {
      if (!facade) return;
      var START = 800, BAND = 60, SWEEP = 1300;
      set(facade, 'on');                                     // the facade comes up dark
      if (late) {
        bands.forEach(function (w) { w.set(1); });
        set(facade, 'f-lit'); set(facade, 'f-flow'); set(facade, 'f-done'); set(giantW, 'gone'); spks.forEach(function (s) { set(s, 'twa'); }); ctx.bg('off');
        return;
      }
      ctx.after(400, function () { set(facade, 'f-gather'); });  // anticipation: light gathers at the left edge
      bands.forEach(function (w, k) { ctx.after(START + k * BAND, function () { w.to(1, SWEEP, 'inout'); }); });
      ctx.after(START, function () { set(facade, 'f-sweep'); handover.fsweep = Date.now(); });
      // follow-through: each sparkle twinkles once as the front passes it, then they live on
      spks.forEach(function (s, i) {
        var p = SPK[i], at = START + Math.floor(p[1] / (100 / bands.length)) * BAND + SWEEP * frontAt(p[0] / 100);
        ctx.after(at, function () { set(s, 'tw'); });
      });
      // lit: the name behind it is retired (it would show through when the facade steps back)
      ctx.after(START + (bands.length - 1) * BAND + SWEEP, function () { set(facade, 'f-lit'); set(giantW, 'gone'); ctx.bg('off'); });
      // (the photo: once the whole picture has taken over, the six band layers leave the page --
      // measured: kept, they cost the next line's cut a dropped run on the 2552 screen)
      ctx.after(START + (bands.length - 1) * BAND + SWEEP + 700, function () { set(facade, 'f-done'); });
      ctx.after(START + (bands.length - 1) * BAND + SWEEP + 400, function () {
        set(facade, 'f-flow'); spks.forEach(function (s) { set(s, 'tw', false); set(s, 'twa'); });
        handover.fflow = Date.now();
      });
    }
    verbs.represent = function () {
      // staging: the name and the greeting step back and up; the title lights up on its soft
      // shade (the core lights the words as her voice reaches them) and the facade behind it
      // lights up band by band, the same way, left to right
      set(giantW, 'dim'); set(ew, 'on', false); set(ew, 'out'); out(every); ping(top);
      set(shade, 'on'); set(title, 'on');
      lightFacade();
    };
    // ("the smart assistant": the title's second line lights in its place -- the core's arrival,
    // nothing else moves)
    // the cards' moment: the facade steps back; the stem drops from the title, the rule draws
    // out of it, the card follows along the rule (follow-through: its icon)
    verbs.sales = function () { set(facade, 'dim'); set(stem, 'on'); set(lt, 'on'); set(left, 'on'); lit(left); };
    verbs.agents = function () { set(stem, 'on'); set(rt, 'on'); set(right, 'on'); lit(right); };
  }

  // =====================================================================================
  // caps -- s02_l05: four stations of her console, one for each thing she can do
  // =====================================================================================
  // Fix round 19 Nov (review: "four full replacements in ~11 s; 'Project information' whole for
  // ~1.0 s, 'Floor plans and visualisations' ~0.8 s"): the stations no longer replace one
  // another. Each rises into the middle (the hero, scaled to fit under the tray), and when she
  // names the next thing it glides up into ITS place in the row she ends on -- the tray along
  // the top, slots 1-4 left to right, at the same x as the final row -- so the row builds up,
  // one station at a time, and nothing is torn down. At "I'd be glad" the last one joins the
  // row and the row settles, with its captions, under her name. The facade of the line before
  // stays as the backdrop all the while (dimmed), so the line also comes in over a held picture.
  function caps() {
    ctx.bg('full');
    var fac = heldFacade(cont && ctx.prev.state && typeof ctx.prev.state === 'object' ? ctx.prev.state : null);
    // (the held photo is opaque -- the shared stage background under it could not be seen, yet it
    // was drawn and animated under every frame: off, as s02_l03 has it once its facade is lit)
    if (fac && PHOTO) ctx.bg('off');
    var giantW = div('giantw caps'), giant = txt('giant', 'cs-type-giant gi');
    ctx.add(giantW, giant);
    var top = topbar(), rail = menu();
    var heads = [head('k1', 't1'), head('k2', 't2'), head('k3', 't3'), head('k4', 't4')];
    var world = div('world');
    // a station = its place on the stage (st: moved by transitions) + its body (b: the arc of a move)
    function station(c) { var st = div('st ' + c + ' s-below'), b = div('stb lift', st); st._b = b; return st; }
    // station 1 -- the project: her own window with the project in it, two satellites
    var st1 = station('st1'), b1 = st1._b, g1 = glass('pane fx');
    ctx.add(b1, g1);
    var bar1 = div('wbar', g1);
    ctx.add(bar1, orb('dot'), words('emma', 'wn'), div('wpill'), div('wdots'));
    // (over the held Building A photo the window's picture is not a WebGL depth view: measured on the
    // 2552 screen, a WebGL quad starting to draw over that full-detail photo dropped 3-4 frames in a
    // row as this station rose, 1 run in 2; as a plain picture drifting by transform, none in 3)
    var hero = pic('hero'), view = hero && !PHOTO ? fx.parallax(hero, { amount: 1.0 }) : null, still = null;
    if (view) view.drift();
    else if (hero) { still = div('wvdrift'); ctx.add(still, hero.el); }
    ctx.add(g1, pix(view ? view.el : still, 'wview'));
    // the left satellite: the owner's clip of the site plan drawing itself as a hologram (owner
    // 22 Nov, review page 2: "เปลี่ยนเป็นวิดีโอนี้") when the slide has it, else the hologram still.
    // It lies on the card's dark ground as the still did (its black is the card's). The card is up
    // for about two seconds ("I can share" to "recommend"), so the clip waits at `site_from` s and
    // runs at `site_rate` (options) from the moment the card comes in: the plot grows out of its
    // ring of light while it is seen, then holds its last frame. Late: the finished plot.
    var site = ctx.has('site') ? ctx.video('site') : null;
    if (site) site.seek(late ? ctx.opt('site_end', 6.9) : ctx.opt('site_from', 0));
    var satR = sat(pix(picEl('sigp')), 'satp fx r'), satL = sat(pix(site ? site.el : picEl('holo')), 'satp fx l');
    ctx.add(b1, satR, satL);
    // station 2 -- the unit types: a fan of three models, brackets close on the one in front
    var st2 = station('st2'), b2 = st2._b, g2 = glass('pane fx rack'), cards = [];
    ctx.add(b2, g2);
    ['studio', 'one', 'two'].forEach(function (k, i) {
      var o = div('cdo', b2), c = div('cd lift', o);     // o: its place in the fan; c: the card (its arc)
      var mk = model(k);
      ctx.add(c, pix(mk ? mk.el : null, 'mdl'));
      o._c = c;
      cards.push(o);
    });
    // the shuffle: card 'two' ends in front. a[i] = where card i sits
    function deal(a) {
      cards.forEach(function (o, i) {
        var was = ['pl', 'pc', 'pr'].filter(function (p) { return o.classList.contains(P(p)); })[0];
        ['pl', 'pc', 'pr'].forEach(function (p) { set(o, p, p === a[i]); });
        if (was && was !== a[i]) lift(o._c);
      });
    }
    deal(['pc', 'pr', 'pl']);                                // studio in front, 1-bed right, 2-bed left
    var brs = ['btl', 'btr', 'bbr', 'bbl'].map(function (k, i) { var b = div('br ' + k, b2); vars(b, { i: i }); return b; });
    var tag2 = words('tag2', 'tg'), tagp = sat(tag2, 'pill fx tag2p');
    ctx.add(b2, tagp);
    // station 3 -- floor plans and visualisations: the plan lies under the model, a sheet of light turns it into the model
    var st3 = station('st3'), b3 = st3._b, g3 = glass('pane fx table');
    ctx.add(b3, g3);
    div('tgrid', g3);
    var layer = div('mlayer', b3);
    var planBox = div('plbox'), planPic = pic('plan', { fit: 'contain' });
    var planImg = div('plimg', planBox);
    if (planPic) ctx.add(planImg, planPic.el);
    var quad = ctx.opt('quad', [[44.3, 16.0], [90.3, 50.4], [54.4, 92.0], [11.0, 40.2]]);
    fx.plane(layer, planBox, quad, { w: 30, h: 23.55, cls: P('planeplan') });
    var wireIn = div('wipein'), solidIn = div('wipein'), wirePic = model('wire'), solidPic = model('two');
    if (wirePic) ctx.add(wireIn, pix(wirePic.el, 'mdl full'));
    if (solidPic) ctx.add(solidIn, pix(solidPic.el, 'mdl full'));
    var wireW = fx.wipe(wireIn, { dir: 'right', from: 0 }), solidW = fx.wipe(solidIn, { dir: 'right', from: 0 });
    set(wireW.el, 'wirew'); set(solidW.el, 'solidw');
    ctx.add(layer, wireW.el, solidW.el);
    var lying = txt('lying', 'cs-type-scene lyt', { glow: false });
    if (lying) fx.plane(b3, lying, ctx.opt('lying.quad', [[30.25, 46.8], [47.1, 80.1], [43.9, 87.1], [27.0, 53.8]]), { w: 14, h: 2.8, cls: P('lyplane') });
    var flatPlan = pic('plan', { fit: 'contain' }), sat3L = sat(pix(flatPlan ? flatPlan.el : null, 'flat'), 'satp fx r3l');
    var sat3R = sat(pix(picEl('tech')), 'satp fx r3r');
    var call = div('call');
    ctx.add(call, words('call', 'callt'), div('cline'));
    ctx.add(b3, sat3L, sat3R, call);
    // station 4 -- quotation details: a document whose rows fill as bars
    var st4 = station('st4'), b4 = st4._b, g4 = glass('pane fx');
    ctx.add(b4, g4);
    var doc = document_(5);
    set(doc.el, 'cdoc');
    ctx.add(b4, doc.el);
    doc.rows.forEach(function (r) { set(r, 'todo', false); set(r, 'blank', true); });
    ctx.add(world, st1, st2, st3, st4);
    [g1, g2, g3, g4].forEach(function (g) { glint(g); });
    // the sales team's card + thread (stage 5)
    var salesCard = card('scard', null, 'sales', 'after');
    var thr = thread(world, 47.5, 30.4, 12.5, 'longth');
    // captions under the four stations (the arc): each arrives with its station. (Only the
    // phrase: the small verb over it -- 1.15cu -- was unreadable from the hall; fix round 19 Nov)
    var capEls = [['k1', 't1', 16.1], ['k2', 't2', 37.6], ['k3', 't3', 62.2], ['k4', 't4', 83.75]].map(function (c, i) {
      var e = div('cap c' + (i + 1));
      box(e, c[2] - 11, 29.4, 22, null);
      vars(e, { i: i });
      ctx.add(e, words(c[1], 'cap-t', true));
      return e;
    });
    // her prompt bar and the threads that run into it
    var bar = glass('pbar fx'), p1 = typed('p1', 'pt', { glow: false }), p2 = typed('p2', 'pt', { glow: false });
    var p1w = div('pw p1w'), p2w = div('pw p2w');
    ctx.add(p1w, p1.el); ctx.add(p2w, p2.el);
    ctx.add(bar, voice(), p1w, p2w, div('go'));
    glint(bar);
    var lines = S('svg', { viewBox: '0 0 100 56.25', preserveAspectRatio: 'none', cls: 'tl4' }), tpaths = [];
    [[16.1, 31, 38], [37.6, 31, 44], [62.2, 31, 56], [83.75, 31, 62]].forEach(function (p, i) {
      var d = 'M ' + p[0] + ' ' + (p[1] + 5.5) + ' C ' + p[0] + ' 35, ' + p[2] + ' 33, ' + p[2] + ' 36';
      var path = svg('path', { d: d, cls: 'tp', pathLength: 1 }, lines);
      vars(path, { i: i });
      tpaths.push(path);
    });
    var a = amb('caps');
    ctx.add(a, world, salesCard);
    ctx.add(stage, fac, giantW, top, rail);
    heads.forEach(function (h) { ctx.add(stage, h); });
    ctx.add(stage, a, lines);
    capEls.forEach(function (e) { ctx.add(stage, e); });
    ctx.add(stage, bar);

    var sts = [st1, st2, st3, st4], panes = [g1, g2, g3, g4];
    var STATE = ['s-below', 's-focus', 's-side', 's-gone', 'a1', 'a2', 'a3', 'a4', 't1', 't2', 't3', 't4', 'f-left'];
    function put(i, s) {
      var moved = !sts[i].classList.contains(P(s));
      STATE.forEach(function (c) { set(sts[i], c, false); }); set(sts[i], s);
      // a station that travels arcs off its line (an arrival from below rises straight)
      if (moved && s !== 's-focus' && s !== 's-gone') lift(sts[i]._b);
      lit(panes[i], s !== 's-gone' && s !== 's-below');
      // (small -- in the tray or the row: its own little words and satellites tuck away; they
      // would be specks there)
      set(sts[i], 'small', /^[ta][1-4]$/.test(s));
    }
    // one heading at a time: the one going cools out, the next lights up in its place
    function headAt(i) {
      heads.forEach(function (h, k) { if (k !== i && h.classList.contains(P('on'))) headOut(h); });
      only(heads, i, 'on');
    }
    verbs.s1 = function () {
      // (the first station has nothing to wait for: it rises at once -- its view is the
      // shortest of the four, 2 s before she names the next thing)
      menuAt(1); headAt(0); set(st1, 'first'); put(0, 's-focus'); set(root, 'z1'); set(fac, 'dim2');
      seq([[520, function () { set(satR, 'in'); lit(satR); }], [600, function () {
        set(satL, 'in'); lit(satL);
        if (site && late) site.seek(ctx.opt('site_end', 6.9));
        else if (site) { site.rate(ctx.opt('site_rate', 1)); site.play(); }
      }]]);
    };
    verbs.s2 = function () {
      menuAt(2); headAt(1); put(0, 't1'); put(1, 's-focus'); set(satR, 'in', false); set(satL, 'in', false);
      // (the shuffle waits for the station to land; once she says "to match" the deal is hers -- no more shuffles)
      seq([[1350, function () { if (!matched) deal(['pl', 'pc', 'pr']); }], [2300, function () { if (!matched) deal(['pr', 'pl', 'pc']); }]]);
    };
    var matched = false;
    verbs.match = function () {
      matched = true;
      set(tagp, 'in'); lit(tagp); deal(['pl', 'pr', 'pc']);
      seq([[600, function () { brs.forEach(function (b) { set(b, 'in'); }); }]]);
    };
    verbs.s3 = function () {
      menuAt(3); headAt(2); put(1, 't2'); put(2, 's-focus');
      brs.forEach(function (b) { set(b, 'in', false); });
    };
    verbs.vis = function () {
      wireW.to(0.68, 900, 'inout'); solidW.to(0.34, 900, 'inout');
      seq([[450, function () { set(sat3R, 'in'); lit(sat3R); }], [550, function () { set(sat3L, 'in'); lit(sat3L); }]]);
      set(call, 'in');
      set(st3, 'seen');
    };
    verbs.s4 = function () {
      menuAt(4); headAt(3); put(2, 't3'); put(3, 's-focus');
      doc.rows.forEach(function (r, i) { set(r, 'blank', false); vars(r, { i: i }); set(r, 'done'); });
      set(doc.el, 'filled'); set(sat3R, 'in', false); set(sat3L, 'in', false);
    };
    verbs.team = function () {
      put(3, 's-focus'); set(sts[3], 'f-left');
      set(thr, 'on'); set(salesCard, 'in'); lit(salesCard);
      seq([[900, function () { set(thr, 'run'); }]]);
    };
    verbs.arc = function () {
      // the name leads; the stations close ranks into one row, left to right; the captions follow
      ['a1', 'a2', 'a3', 'a4'].forEach(function (a, i) { put(i, a); });
      set(world, 'arcmode'); set(salesCard, 'in', false); lit(salesCard, false); set(thr, 'on', false); set(thr, 'run', false); set(giantW, 'on');
      heads.forEach(function (h) { if (h.classList.contains(P('on'))) headOut(h); set(h, 'on', false); });
      capEls.forEach(function (e) { set(e, 'on'); });
      set(root, 'pullbk');
    };
    // her prompt bar becomes the hero: the row of stations steps back under it
    verbs.ask1 = function () { set(bar, 'on'); lit(bar); set(world, 'under'); set(p1w, 'on'); type(p1, 1700); };
    verbs.ask2 = function () {
      set(p1w, 'on', false); set(p1w, 'gone'); set(p2w, 'on'); type(p2, 1600); set(bar, 'send');
      tpaths.forEach(function (p) { set(p, 'drawn'); });
      set(lines, 'on');
    };
  }

  // =====================================================================================
  // quote -- s05_l08 / l11 / l13 / backup: the same document object in four states
  // =====================================================================================
  function quote() {
    ctx.bg('full');
    var start = ctx.opt('start', 'unit');
    var top = topbar(), rail = menu();
    menuAt(start === 'unit' ? 3 : 4);
    var head_ = head('kick', 'title');
    var hasUnit = ctx.has('unit');
    var unitP = glass('unitp fx'), mpic = model('unit');
    ctx.add(unitP, pix(mpic ? mpic.el : null, 'mdl full'), div('uglow'));
    var plate = div('plate');
    ctx.add(plate, words('plate', 'plt'));
    ctx.add(unitP, plate);
    glint(unitP);
    var doc = document_(8), docW = div('docw');
    ctx.add(docW, doc.el);
    set(doc.el, 'lift');
    var a = amb('quote');
    var th = [0, 1, 2, 3].map(function (i) { var t = thread(a, 44.5, 19 + i * 5.3, 12.5, 'q' + i); vars(t, { i: i }); return t; });
    var cardS = card('qcard', null, 'staff', 'carry');
    var steps = div('steps');
    for (var i = 0; i < 4; i++) { var chev = div('chev', steps); vars(chev, { i: i }); ctx.add(chev, icon(['M7 5l8 7-8 7'])); }
    var stepW = div('stepw'); ctx.add(stepW, words('steps', 'stt'));
    var tag = div('stag'); ctx.add(tag, words('tag', 'stg', true));
    ctx.add(a, hasUnit ? unitP : null, docW, cardS, steps, stepW, tag);
    ctx.add(stage, top, rail, head_, a);
    var sample = ctx.has('tag');
    // the opening state of each cue (it is the end state of the one before it: the same frame)
    set(unitP, 'rest'); held(unitP);
    if (start === 'unit') { set(docW, 'away'); fill(doc, -1); doc.rows.forEach(function (r) { set(r, 'todo'); }); }
    if (start === 'waiting') { set(docW, 'rest'); held(doc.el); fill(doc, -1); doc.rows.forEach(function (r) { set(r, 'todo'); }); set(th[1], 'on'); }
    if (start === 'held') {                                // = the end of s05_l11: working, pushed in, threads running
      set(docW, 'rest'); held(doc.el); fill(doc, 4); set(doc.el, 'busy'); set(doc.el, 'holding'); set(root, 'push'); set(unitP, 'dim');
      if (ctx.has('plate')) set(plate, 'on');
      th.forEach(function (t) { set(t, 'on'); set(t, 'run'); });
      // its title is the line's first words: lit with the line (the document's "ready" swings in under it)
      if (ctx.has('title')) set(head_, 'on');
    }
    if (start === 'done') { set(docW, 'rest'); held(doc.el); fill(doc, 8); set(doc.el, 'full'); th.forEach(function (t) { set(t, 'on'); }); }

    // anticipation: her menu's quotation glyph lights, the document comes a beat later
    verbs.doc = function () {
      menuAt(4);
      seq([[150, function () { set(docW, 'away', false); set(docW, 'rest'); lit(doc.el); set(unitP, 'dim'); }]]);
    };
    // "for this unit": the unit comes forward again, the thread runs from the document to it
    verbs.thread = function () {
      set(unitP, 'dim', false);
      if (sample) { set(plate, 'on'); return; }
      set(th[1], 'on');
    };
    verbs.title = function () { set(head_, 'on'); };
    // "Certainly": the document is the hero -- the camera leans in, the unit steps back
    verbs.halo = function () { set(doc.el, 'halo'); set(root, 'push'); set(unitP, 'dim'); };
    verbs.prep = function () {
      set(doc.el, 'busy'); set(head_, 'on');
      th.forEach(function (t, k) { set(t, 'on'); seq([[200 + k * 80, function () { set(t, 'run'); }]]); });
    };
    verbs.rows = function (b) {
      set(head_, 'on');
      var n = b.n || 2; fill(doc, n);
      if (ctx.has('plate') && n >= 4) { set(plate, 'on'); set(unitP, 'dim', false); }
    };
    verbs.scan = function () { fill(doc, 4); set(doc.el, 'holding'); set(unitP, 'dim'); };
    // s05_l13: ready -- the unit bows out, the document swings to the centre (an arc), its ring ticks
    verbs.ready = function () {
      fill(doc, 8); set(doc.el, 'busy', false); set(doc.el, 'holding', false); set(doc.el, 'ready');
      set(unitP, 'away'); set(root, 'push', false); th.forEach(function (t) { set(t, 'on', false); set(t, 'run', false); });
      set(docW, 'centre'); lift(doc.el);
      set(head_, 'on');
    };
    // the staff's card slides out from behind the document; the document gives it the stage
    // straight on from s05_l11 (owner 24 Nov: question 10 is one answer, nothing says "ready"): the unit bows out and the
    // document swings to the centre -- no tick on it -- then the staff's card
    var straight = ctx.opt('straight', false) === true;
    function hand() {
      fill(doc, 8); set(doc.el, 'busy', false); set(doc.el, 'holding', false);
      set(unitP, 'away'); set(root, 'push', false); th.forEach(function (t) { set(t, 'on', false); set(t, 'run', false); });
      set(docW, 'centre'); lift(doc.el);
    }
    verbs.staff = function () {
      if (straight) hand();
      set(cardS, 'in'); lit(cardS); set(root, 'handed');
      if (sample) { set(docW, 'face'); set(tag, 'on'); }
    };
    verbs.carry = function () { set(cardS, 'carry'); };
    verbs.steps = function () { set(steps, 'on'); set(stepW, 'on'); };
    // backup: the document turns to face us wearing a Sample tag; the unit steps back behind it
    verbs.face = function () {
      set(docW, 'face'); lift(doc.el); set(root, 'faced'); set(unitP, 'dim');
      th.forEach(function (t) { set(t, 'run', false); });
      seq([[450, function () { set(tag, 'on'); }]]);
      set(head_, 'on'); set(doc.el, 'sample');
    };
  }

  // =====================================================================================
  // wall -- s06_l05 / s06_l07: Emma herself in front of a curved wall of pictures of the project
  // =====================================================================================
  function wall() {
    ctx.bg('full');
    var start = ctx.opt('start', 'stand');
    var from = cont && ctx.prev.state && typeof ctx.prev.state === 'object' ? ctx.prev.state : null;
    var top = topbar(), giantW = div('giantw hi'), giant = txt('giant', 'cs-type-giant gi', { reveal: 'pop' });
    ctx.add(giantW, giant);
    var beams = div('beams'); div('bm b1', beams); div('bm b2', beams); div('bm b3', beams);
    var cols = 7, rowsN = 3, cards = [], wallEl = div('wallw'), wallCam = div('wcam', wallEl);
    // (owner 19 Nov, a screenshot of this wall: "ภาพที่โชว์อยากเป็นภาพทุกอย่างไม่ใช่ภาพสีน้ำเงินหรือ
    // ตึกที่เราปั้น หรือทำ เอาภาพจริงๆ" -- it was blue holograms, a blueprint and the buildings we
    // modelled, cropped in. It is now the owner's own final renders of everything she studied --
    // the buildings round the lagoon, the facilities of every floor, the rooms: one sheet
    // (`study`, options.sheet = its cols, rows, cell w, h and gutter in px), one cell per card, 21
    // different pictures, each whole in its card)
    var SH = ctx.opt('sheet', null);
    function cell(i) {
      var p = pic('study');
      if (!p) return null;
      if (SH && SH.length >= 5) {
        var sc = SH[0], sr = SH[1], cw = SH[2], ch = SH[3], g = SH[4], c = i % sc, r = Math.floor(i / sc) % sr;
        set(p.el, 'sheet');
        p.el.style.width = ((sc * cw + (sc - 1) * g) / cw * 100) + '%';
        p.el.style.height = ((sr * ch + (sr - 1) * g) / ch * 100) + '%';
        p.el.style.left = (-c * (cw + g) / cw * 100) + '%';
        p.el.style.top = (-r * (ch + g) / ch * 100) + '%';
      }
      return p.el;
    }
    // (fix round 19 Nov, review: at the quiz the wall's pictures "blank out" -- every card
    // turned to an empty slab. The quiz never erases the wall she studied: each card turns
    // edge-on and comes round again AS a quiz card -- its own picture under a light quiz-blue
    // tint, an answer box drawn over it. Owner's notes 19 Nov: the wall is real pictures, so
    // the quiz tint is light and the picture reads through it. One face per card, no second
    // copy of the picture on a back: the turn's far half shows the same face (measured: the
    // backs' 21 extra pictures, first drawn at the turn, dropped 3 frames in a row there).
    // Only a line that flips the wall builds the quiz marks)
    var quiz = (ctx.slide.beats || []).some(function (b) { return b && b['do'] === 'flip'; });
    for (var r = 0; r < rowsN; r++) {
      for (var c = 0; c < cols; c++) {
        var i = r * cols + c, wc = div('wc r' + r), flip = div('wr', wc), face = div('wf', flip), inner = div('wi', face);
        div('wread', face);                              // the reading light that crosses the card ("very carefully")
        ctx.add(inner, cell(i));
        // (a hair of zoom about the card's centre: the sheet's gutters never show at its edge)
        vars(inner, { ox: 0, oy: 0, sc: 1.05 });
        if (quiz) { div('wqt', face); div('wqq', face); }  // (the quiz card's blue, its answer box)
        vars(wc, { c: c - (cols - 1) / 2, k: c, r: r - 1, i: i, d: Math.abs(c - (cols - 1) / 2), rr: r });
        ctx.add(wallCam, wc);
        cards.push(wc);
      }
    }
    // ---- Emma: the clip in a box. The box's place on the floor (ex, ey = the point under her,
    // in cu), the frame's size (fh = its height in cu; the clip is 3:4) and where her lowest
    // point is in the frame (base, of its height: the render printed it, points.json).
    var clip = ctx.has('emma') ? ctx.video('emma') : null;
    var E = { x: ctx.opt('emma.x', 20), y: ctx.opt('emma.y', 42.4), fh: ctx.opt('emma.fh', 30.6), base: ctx.opt('emma.base', 0.919),
              px: ctx.opt('podium.x', 50), py: ctx.opt('podium.top', 34) };
    var em = div('emma'), emy = div('emy', em), emb = div('emb', emy), eglow = div('eglow', em), ehalo = div('ehalo', emy);
    if (clip) ctx.add(emb, clip.el);
    // (23 Nov night, the event notebook: Chrome decodes VP9 with alpha on the CPU -- 35% of this
    // clip's frames dropped. It is cut to the part of the frame she is ever in, and made at the
    // size it is drawn at (x0.75: 720 px of frame for a box 588 px tall, x1.15 at most in a squash):
    // emma.window = [x, y, w, h, W, H], the file is the w x h window at (x, y) of the W x H frame
    // the box stands for, laid exactly there. No window = a clip of the whole frame, filling it.)
    if (clip) windowed(clip.el, ctx.opt('emma.window', null));
    ctx.add(emy, emb); ctx.add(em, eglow, emy);          // (the halo behind her, the clip in front of it; her hover light under both)
    vars(em, { ex: E.x, ey: E.y, fh: E.fh, fw: +(E.fh * 0.75).toFixed(3), fb: +(E.fh * E.base).toFixed(3), bo: +(E.base * 100).toFixed(2) + '%',
               hx: +(E.px - E.x).toFixed(3), hy: +(E.py - E.y).toFixed(3) });
    var podium = div('podium'); div('stp s2', podium); div('stp s1', podium); div('stp s3', podium);
    var conf = div('confetti');
    for (var q2 = 0; q2 < 20; q2++) { var f = div('cf', conf); vars(f, { x: (q2 * 37 % 100) - 50, d: (q2 * 53 % 23) / 10, s: 4 + (q2 * 7 % 5), h: (q2 * 29 % 6) * 1 }); }
    // the joke's words, typed in step with her voice at the head of the console, one at a time
    var kick = typed('kick', 'cs-type-h2 kk'), kw = div('head kickw'); ctx.add(kw, kick.el);
    // (the plate waits on its own row, a row under the set-up, before it shows: put there in the
    // same moment it lit up, it slid down over the set-up while its first letters were typed --
    // "I'm sure" over "If there's ever an", the Kantalad review 22 Nov night)
    var plateT = typed('plate', 'cs-type-h2 pp'), plate = div('head pplate low'); ctx.add(plate, plateT.el);
    // her lines of s06_l05, one phrase at a time beside her: the newest lit, the one before it
    // dimmed above it, older ones gone (the column moves up a row as each one comes)
    var lw = div('tlines'), rowsT = [];
    ['t1', 't2', 't3', 't4'].forEach(function (key, n) {
      var t = txt(key, 'cs-type-h2 tln');
      if (!t) return;
      var row = div('trow'); vars(row, { i: n }); ctx.add(row, t); row._t = t;
      ctx.add(lw, row); rowsT.push(row);
    });
    ctx.add(stage, beams, top, wallEl, podium, em, conf, giantW, kw, plate, lw);

    function wallState(s) { ['on', 'lit', 'flip', 'part', 'dim', 'stage'].forEach(function (cl) { set(root, 'w-' + cl, false); }); (s || []).forEach(function (cl) { set(root, 'w-' + cl); }); }
    if (start === 'wall') { wallState(['on', 'lit']); set(root, 'w-near'); set(root, 'e-held'); set(root, 'e-proud'); }   // (= the end of s06_l05)
    // (the opening carries the study wall, dim and a step back: the line before ends on a wall of
    // pictures, and a cut to an empty stage blanked it -- the wall steps forward at its beat)
    else if (ctx.has('study')) set(root, 'w-pre');
    // a keyframe beat of the joke plays only on time (a late screen gets the landed state)
    function beatFx(cl) { if (!late) set(root, cl); }
    function lineAt(n) {
      lw.style.setProperty('--at', String(n));
      rowsT.forEach(function (row, i) {
        set(row, 'now', i === n); set(row, 'prev', i === n - 1); set(row, 'old', i < n - 1);
        if (i < n - 1) out(row._t);
      });
    }

    // ---- the clip: a part starts on her word (a seek to its first frame, a key frame); the
    // preen runs on into the idle loop (the same pose at 3 s); the idle loops by a seek from its
    // end to its start (the same pose); the cheer goes back to the idle the same way
    var T = { preen: 0, idle: 3, idleEnd: 6.95, cheer: 7, cheerEnd: 8.95 };
    var part = 'idle', seekAt = -1e9;
    function playPart(p, at) {
      if (!clip) return;
      part = p;
      clip.seek(typeof at === 'number' ? at : T[p]); clip.play();
      seekAt = Date.now();
    }
    var startAt = from && typeof from.emma_t === 'number' && from.emma_t >= T.idle && from.emma_t < T.idleEnd ? from.emma_t : T.idle;
    playPart('idle', startAt);
    ctx.frame(function () {
      if (!clip) return;
      var t = clip.time(), now = Date.now();
      if (now - seekAt < 250) return;                     // (a seek just asked for: its frame is on its way)
      if (part === 'preen' && t >= T.idle - 0.02) part = 'idle';
      else if (part === 'idle' && (t >= T.idleEnd || t < T.idle - 0.1 || clip.ended())) playPart('idle');
      else if (part === 'cheer' && (t >= T.cheerEnd || clip.ended())) playPart('idle');
    });
    handover.emma_t = T.idle;
    ctx.frame(function () { if (clip) handover.emma_t = clip.time(); });

    // "Besides my good looks": she preens; a soft light comes up behind her (secondary)
    verbs.glint = function () { set(root, 'e-proud'); playPart('preen'); lineAt(0); };
    // the wall unrolls behind her: centre column first, outward
    verbs.wall = function () { set(root, 'w-on'); lineAt(1); };
    // "about Embassy World": the wall lights from the centre out
    verbs.light = function () { set(root, 'w-lit'); lineAt(2); };
    // "very carefully": a reading light crosses the wall, card by card, left to right, and the
    // wall leans in toward her a touch
    verbs.slow = function () { beatFx('w-read'); set(root, 'w-near'); lineAt(3); };
    // the joke (Playful): the set-up turns every picture into a quiz card in one wave and
    // she perks up to face them; in the pause ("...") she crouches -- anticipation of a jump she
    // does not make yet; "I'm sure": the cards swish apart, she winds up lower; "first place":
    // the podium pops up, she hops onto its top block, lands with a squash and cheers; the
    // title pops, beams, confetti
    // (fix round 19 Nov, review: the set-up was typed ~0.5 s behind her voice and wiped at
    // "superfan q..." -- never read whole. It is typed in step with her now (the typer never
    // runs ahead of her voice, so a short time only means "keep up"), it stands whole from
    // "I'm sure" -- dimmed, the punchline typed under it -- and only yields its place when she
    // jumps, ~1.5 s later: the punchline rises into the head as she leaves the floor)
    verbs.flip = function () {
      set(root, 'w-flip'); beatFx('w-flipping'); set(root, 'e-perk'); type(kick, 1100); set(kw, 'on');
      seq([[1500, function () { set(root, 'e-crouch'); }]]);
    };
    // "I'm sure": the cards turn back to her pictures as they swish apart
    verbs.part = function () {
      set(root, 'w-part'); set(root, 'w-flip', false); set(root, 'w-flipping', false); beatFx('w-unflip');
      set(root, 'w-lit'); set(root, 'e-crouch'); set(root, 'e-wind');
      kick.set(1); set(kw, 'prev'); set(plate, 'low'); set(plate, 'on'); type(plateT, 900);
    };
    verbs.podium = function () {
      set(root, 'w-stage'); set(giantW, 'on'); set(beams, 'on'); set(conf, 'on');
      set(kw, 'prev', false); set(kw, 'out'); out(kick.el); set(plate, 'low', false);
      set(root, 'e-pod'); set(root, 'e-crouch', false); set(root, 'e-wind', false); set(root, 'e-perk', false);
      if (late) { playPart('idle'); return; }
      set(root, 'e-hop');
      // (Playful, the joke's one big move -- review 19 Nov: the leap read as ~0.25 s. Now: a last
      // deeper crouch as the podium pops up (anticipation), a 0.9 s arc, a squash on landing and
      // a settle (follow-through); the camera holds still under it)
      // she lands 280 + 900 ms after the word: her cheer starts as she touches down
      ctx.after(1180, function () { playPart('cheer'); });
    };
  }

  // =====================================================================================
  // doors -- s07_l02: her console's window holds the light while the video plays on the hall's
  // LED screen (the mode keeps its old name: the slide names it)
  // =====================================================================================
  // Fix round 19 Nov (review: an empty three-pane shell 0.35-2.2 s, then an empty stage, then a
  // play button held ~12 s that never plays -- the video is on the hall's LED screen, not on
  // this one). No doors, no button, no UI chrome: her console's window is a SCREEN OF LIGHT --
  // soft bands of light drifting through it, motes in the beam, the way a picture-house screen
  // holds a projector's light. It is up from the line's first frame; at "Everyone" its light
  // gathers, the hall's light goes down and the title lights up in it; it lives through the
  // whole video. The next line (s07_l07) opens on this same window, at the same place, and its
  // light becomes the dashboard.
  function screenLight(win) {
    var scr = div('scr', win);
    div('scrb a', scr); div('scrb b', scr); div('scrb c', scr);
    div('scrm', scr); div('scrv', scr);
    return scr;
  }
  function doors() {
    ctx.bg('full');
    var top = topbar();
    var win = glass('wwin fx up screen'), scr = screenLight(win);
    held(win); glint(win);
    var say = txt('say', 'cs-type-h2 sy');
    var sw = div('sayw', win); ctx.add(sw, say);
    var a = amb('doors');
    ctx.add(a, win);
    // (the camera's layer round the window: the window's own layer breathes by an animation)
    var zw = div('zoomw');
    ctx.add(zw, a);
    ctx.add(stage, top, zw);
    handover.screen = true;
    // "Everyone": the notch answers and the screen's light gathers (anticipation of the title)
    verbs.doors = function () { ping(top); set(scr, 'sc-gather'); };
    verbs.disc = function () {
      set(scr, 'sc-show'); set(sw, 'on');
      ctx.light(0.34, 2200);
    };
    // options.end 'zoom' (owner 20 Nov, review PDF p.15: "ฉากนี้ขอซูมเข้าไป เป็นเต็มจอเลย ให้ชมวิดีโอ"):
    // once she has said it, a beat later the title fades and the camera goes into the screen of
    // light until it fills the whole screen -- the video's screen; it holds there, its light alive.
    // The next line (s07_l07) opens on that full screen and draws back out to its window.
    if (ctx.opt('end', '') === 'zoom') {
      endHook = function () {
        var go = function () {
          set(sw, 'on', false); set(sw, 'gone'); set(top, 'gone');
          set(zw, 'zin');
          handover.zoomed = true;
        };
        if (late) { set(zw, 'now'); go(); } else ctx.after(900, go);
      };
    }
  }

  // =====================================================================================
  // water -- s07_l07: a console of instruments round the project's hologram, its water moving
  // =====================================================================================
  function dashArt(c) {
    var s = S('svg', { viewBox: '0 0 160 64', cls: 'dash ' + c, preserveAspectRatio: 'xMidYMid meet' });
    svg('path', { d: 'M22 10 C 16 19, 10 26, 10 33 a12 12 0 0 0 24 0 C 34 26, 28 19, 22 10 Z', cls: 'dk-drop' }, s);
    [0, 1, 2].forEach(function (i) {
      var x = 46 + i * 38;
      var t = svg('rect', { x: x, y: 8, width: 32, height: 26, rx: 3, cls: 'dk-tile' }, s);
      var sp = svg('path', { d: 'M' + (x + 4) + ' ' + (27 - i) + ' L' + (x + 11) + ' ' + (20 + i * 2) + ' L' + (x + 18) + ' 24 L' + (x + 28) + ' ' + (14 + i * 3), cls: 'dk-spark', pathLength: 1 }, s);
      vars(t, { i: i }); vars(sp, { i: i });
    });
    svg('path', { d: 'M10 54 C 30 42, 50 60, 72 50 S 110 40, 150 47', cls: 'dk-trend', pathLength: 1 }, s);
    svg('line', { x1: 10, y1: 60, x2: 150, y2: 60, cls: 'dk-axis' }, s);
    return s;
  }
  function water() {
    ctx.bg('full');
    ctx.light(0.34, 0);
    // (fix round 19 Nov: the line opens on the screen of light the video line left -- the same
    // window, the same place -- not on an empty stage for 1.4 s; at "the water-quality
    // dashboard" its light becomes the dashboard. The kickers "This page" / "On a single
    // screen" (1.35cu) are dropped: unreadable from the hall)
    var top = topbar(), kick1 = head('kick1', 'title'), kick2 = null;
    var win = glass('wwin fx up'), scr = screenLight(win), holo = div('holo dark', win);
    held(win); set(scr, 'sc-show');
    var shown = 0;
    var layers = [['r00', 'a0'], ['r06', 'a1'], ['r14', 'a2'], ['r23', 'a3']].map(function (k) {
      var l = div('rl ' + k[1], holo), p = ctx.has(k[0]) ? pic(k[0]) : null;
      if (p) { ctx.add(l, p.el); shown++; }
      return l;
    });
    // no picture of a dashboard exists: the pool-ripple renders were a hologram with empty
    // gauges, not one (review 18 Nov) -- the screen is drawn: a drop, three tiles with a trend
    // in each and one long trend line, no figure anywhere
    if (!shown) ctx.add(holo, dashArt('dmain'));
    // the hologram's own life on a hold: faint bands drifting down its screen
    div('bands', holo);
    var plate = div('wplate'); ctx.add(plate, words('plate', 'wpt'));
    ctx.add(holo, plate, div('wscan'));
    glint(win);
    var inst = div('inst', win), rings = [];
    for (var i = 0; i < 4; i++) {
      var g = div('gauge', inst), s = S('svg', { viewBox: '0 0 40 40', cls: 'gsv' });
      svg('circle', { cx: 20, cy: 20, r: 16, cls: 'gtrack' }, s);
      var sp = div('gspin', g); vars(g, { i: i, len: [0.3, 0.42, 0.24, 0.36][i] });
      var s2 = S('svg', { viewBox: '0 0 40 40', cls: 'gsv' });
      svg('circle', { cx: 20, cy: 20, r: 16, cls: 'garc', pathLength: 1 }, s2);
      ctx.add(g, s); ctx.add(sp, s2);
      rings.push(g);
    }
    var wave = S('svg', { viewBox: '0 0 100 30', preserveAspectRatio: 'none', cls: 'wave' });
    svg('path', { d: 'M0 18 C 12 4, 24 4, 36 15 S 62 28, 76 12 S 92 8, 100 4', cls: 'wl1', pathLength: 1 }, wave);
    svg('path', { d: 'M0 25 C 14 20, 28 26, 42 21 S 70 18, 100 13', cls: 'wl2', pathLength: 1 }, wave);
    ctx.add(inst, wave);
    var info = div('winfo'); ctx.add(info, words('info', 'cs-type-label wil'));
    ctx.add(inst, info);
    var teamC = card('wteam', null, 'team', null);
    var zoomS = sat(ctx.has('zoom') ? pix(picEl('zoom')) : dashArt('dzoom'), 'satp fx wz');
    var tg = words('tag', 'tg'), tagp = sat(tg, 'pill fx wtag');
    var a = amb('water');
    ctx.add(a, win, teamC, zoomS, tagp);
    // (the line before went into its screen until it filled the stage: this line opens there, on
    // the same full screen of light, and the camera draws back out to the window -- one move)
    var zw = div('zoomw');
    ctx.add(zw, a);
    ctx.add(stage, top, kick1, zw);
    if (cont && ctx.prev.state && ctx.prev.state.zoomed === true && !late) {
      set(zw, 'now'); set(zw, 'zin');
      ctx.after(60, function () { set(zw, 'now', false); set(zw, 'zin', false); set(zw, 'out'); });
    }
    // the stage wakes from the dim state the video left
    seq([[500, function () { ctx.light(1, 1800); }]]);

    // "This page": the notch answers, the screen's light gathers (anticipation)
    verbs.wake = function () { ping(top); set(scr, 'sc-gather'); };
    // the screen's light becomes the dashboard: the light draws back as the dashboard's own
    // screen comes up in it, from a little lower (the window does not move: it was there)
    verbs.dash = function () {
      set(kick1, 'on');
      seq([[120, function () { set(scr, 'sc-gone'); set(holo, 'dark', false); }],
           [1000, function () { set(holo, 'cycle'); }]]);
    };
    verbs.gauges = function () { set(inst, 'in'); };
    // the team's card slides out from behind the dashboard; the dashboard gives it the stage
    verbs.team = function () { set(teamC, 'in'); lit(teamC); set(win, 'dim'); };
    // the instruments come alive: the dashboard is the hero again, the card steps back
    verbs.rings = function () {
      set(win, 'dim', false); set(teamC, 'dim'); set(inst, 'live');
      rings.forEach(function (g) { set(g, 'drawn'); }); set(wave, 'drawn'); set(info, 'on');
    };
    verbs.night = function () { set(zoomS, 'in'); lit(zoomS); };
    verbs.tag = function () { set(tagp, 'in'); lit(tagp); };
  }

  var scenes = { intro: intro, caps: caps, quote: quote, wall: wall, doors: doors, water: water };
  (scenes[mode] || caps)();

  return {
    beat: function (b, isLate) {
      late = ctx.late || !!isLate;
      var fn = verbs[b.do];
      if (fn) fn(b, isLate);
    },
    settle: function () { typers.forEach(function (t) { t.set(1); }); if (endHook) endHook(); },
    handover: function () {
      var o = { mode: handover.mode, emma_t: handover.emma_t };
      if (typeof handover.fsweep === 'number') o.fsweep = handover.fsweep;
      if (typeof handover.fflow === 'number') o.fflow = handover.fflow;
      if (handover.screen) o.screen = true;
      if (handover.zoomed) o.zoomed = true;
      return o;
    }
  };
});
