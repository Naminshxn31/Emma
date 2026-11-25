/* smarthome_brand -- the Smart Home console (scene 8) and the project's mark (the story title,
   the closing). One act, two modes (options.mode):

   console  Emma's glass console: her command types in as she says it, the state lands only on
            the cue that says it is done -- said once, in the panel -- and the room
            follows the device: lights off = darkness falls over the whole stage from above
            (and the stage's light, ctx.light), lights on = it lifts and a bloom of light comes
            back, curtains = drapes over the stage that gather to the sides. No picture: the
            screen never pretends to show the hall.
   brand    the story title and the closing, on the film's stage. No mark since 23 Nov (the owner took
            the logo clip out of both): the story line opens a window onto the project and the
            camera dives into it until it is the whole screen; the closing opens on that frame,
            pulls it back into a wheel of cards, and ends on the invitation.

   Motion (skill pass 18 Nov, the film's identity): Premium -- the signature curve for moves on
   the screen, a decelerating one for entrances, an accelerating one for exits, no overshoot.
   The switch is the one state-feedback moment: the knob is pressed (anticipation), travels on
   the signature curve, then its glow follows (follow-through). Every tween in here is eased;
   none is linear except the working arc (a spinner).

   Stall pass, 18 Nov afternoon (owner: "เหมือนบางช่วงกระตุก ค้างด้วย"): a line that is over
   holds the screen until the next one -- through Khun Miki's whole question -- and the "Off"
   card in the dark stood completely still there. Every held shot now keeps an ambient layer
   alive (dust in the console's light, its glow breathing under the card, a sheen on the
   glass, the switch's rim light; the mark floating, the reel never quite stopping), and the
   brand shots arrive already moving. Type, the same afternoon: the film's one face, the
   core's look and arrival; no giant backlit words (OFF / ON / OPEN, THANK YOU, THE STORY OF).
   Handover, 18 Nov late afternoon: the whole console room floats on a slow sine (a held frame
   of scene 8 is never a still); a console that goes on from the line before starts from that
   line's camera (it restarted at scale 1 -- a jolt of 3-4% at every join); the panel's lines,
   the headline over the wheel and the invitations light up the core's way (they were typed
   with a caret -- the console's command line is the one typed thing left: a device at work).

   Every word comes from the slide through ctx.text; every picture / clip through ctx.picture /
   ctx.video; the clock is her voice (progress + beats). Nothing here is read by the hall. */
CeremonyShow.register('smarthome_brand', function (ctx) {
  var fx = ctx.fx;
  var mode = ctx.opt('mode', 'console');
  var late = ctx.late;

  // ---- small helpers ----
  function cu(n) { return 'calc(' + (Math.round(n * 1000) / 1000) + ' * var(--cu))'; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function mk(cls, parent) { var e = ctx.el(cls); if (parent) ctx.add(parent, e); return e; }
  function shape(parent, tag, attrs) { var e = ctx.svg(tag, attrs); if (e) ctx.add(parent, e); return e; }
  // the film's curves (the core defines them on the stage; the fallbacks are the same values)
  var SIG = 'var(--cs-ease, cubic-bezier(0.4, 0, 0.2, 1))';
  var EIN = 'var(--cs-ease-in, cubic-bezier(0.05, 0.7, 0.1, 1))';
  // the same curves for the act's own frame tweens: a cubic-bezier solved by bisection
  function bez(x1, y1, x2, y2) {
    function at(t, p1, p2) { var u = 1 - t; return 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t; }
    return function (x) {
      x = Math.max(0, Math.min(1, x));
      var lo = 0, hi = 1, t = x;
      for (var i = 0; i < 22; i++) { t = (lo + hi) / 2; if (at(t, x1, x2) < x) lo = t; else hi = t; }
      return at(t, y1, y2);
    };
  }
  // (fix round 19 Nov, review: the dive through the ring read as a 0.45 s push and the picture
  // fell to a small card in 0.35 s -- full-frame scale changes on curves that spend them in a
  // few frames. A full-frame scale change now takes ~2 s, eased in and out, in log scale)
  var EASE = { sig: bez(0.4, 0, 0.2, 1), enter: bez(0.05, 0.7, 0.1, 1), exit: bez(0.3, 0, 1, 1), camera: bez(0.65, 0, 0.35, 1),
               dive: bez(0.4, 0, 0.3, 1), pull: bez(0.4, 0, 0.3, 1) };
  // (measured on 10 fps shots: a zoom's first part is spent off the screen -- the ring has to
  // grow before it fills it, the picture has to shrink below the screen before it is seen to --
  // so a 1.7 s dive read as ~1.0 s and a 1.65 s pull as ~0.8 s. 2.2 s each, gentler in the middle)
  var DIVE_MS = 2200, PULL_MS = 2200;
  var GLYPH = {
    lights: ['M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.3 1.5 3.5.7.7 1.3 1.5 1.5 2.5', 'M9 18h6', 'M10 22h4'],
    curtains: ['M3 4h18', 'M5 4c0 6 1 11-1 16h6c-1-5-1-10-1-16', 'M19 4c0 6-1 11 1 16h-6c1-5 1-10 1-16'],
    check: ['M20 6 9 17l-5-5'],
    send: ['M22 2 11 13', 'M22 2l-7 20-4-9-9-4 20-7z'],
    chat: ['M7.9 20A9 9 0 1 0 4 16.1L2 22Z'],
    search: ['m21 21-4.3-4.3'],
    house: ['M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8', 'M3 10a2 2 0 0 1 .7-1.5l7-6a2 2 0 0 1 2.6 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z'],
    chevron: ['m9 18 6-6-6-6']
  };
  function glyph(kind, cls) {
    var s = ctx.svg('svg', { cls: 'glyph ' + (cls || ''), viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
      'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    (GLYPH[kind] || []).forEach(function (d) { shape(s, 'path', { d: d }); });
    if (kind === 'search') shape(s, 'circle', { cx: 11, cy: 11, r: 8 });
    return s;
  }
  function typedText(key, cls) {
    if (!ctx.has(key)) return null;
    var t = ctx.text(key, { split: 'chars', cls: cls });
    return t ? fx.typed(t) : null;
  }
  // a chip that travels on an arc: the outer layer moves across on the entrance curve, the
  // chip inside drops the last bit on the signature curve -- two curves, one curved path
  function arcChip(inner, cls, side) {
    var chip = fx.sat(inner, 'chip ' + cls);
    var lane = mk('chiplane chiplane-' + side);
    ctx.add(lane, chip);
    return { el: lane, chip: chip, show: function () { lane.classList.add('in'); chip.classList.add('in'); } };
  }

  var runs = [];                       // typed texts that follow her voice: {typed, from, span}
  function follow(typed, b, late2) {
    if (!typed) return;
    if (late2) { typed.set(1); return; }
    runs.push({ typed: typed, from: b.at, span: Math.max(0.02, +b.span || 0.2) });
  }

  // ======================================================================================
  //  CONSOLE
  // ======================================================================================
  function buildConsole() {
    var device = ctx.opt('device', 'lights');
    var light0 = ctx.opt('light0', 1);
    var knob0 = ctx.opt('knob0', 'on');          // on | off (where the toggle rests in the opening)
    var lit0 = ctx.opt('lit0', true);            // is the disc lit in the opening
    var working0 = ctx.opt('working0', false);   // the arc already circling in the opening
    var tilt = ctx.opt('tilt', -5);
    var prev = ctx.prev && ctx.prev.act === 'smarthome_brand' && ctx.prev.state && ctx.prev.state.mode === 'console' ? ctx.prev.state : null;

    ctx.bg('full');
    ctx.light(light0, 0);
    var scene = ctx.el('scene');
    var cam = fx.camera(scene);
    // (handover 18 Nov: a console that goes on from the line before starts where that line's
    // camera stood -- it was made at scale 1 while the one under it stood at 1.045 or 0.97,
    // and for the half second the old frame fades out underneath both panels showed, a few
    // per cent apart: a jolt at every join of scene 8)
    if (prev && prev.view) cam.set(prev.view);
    // the room floats, slowly and always (a sine loop on a layer of its own, under the camera
    // and the rig's own turns): the whole console breathes a little -- a held frame, the
    // "Off" card in the dark included, is never a still
    var drift = mk('drift', scene);
    var rig = mk('rig', drift);
    var pose = { ry: tilt, rx: 2.5, s: 1, z: 0 };
    function setPose(p, ms, curve) {
      pose = { ry: p.ry, rx: p.rx, s: p.s, z: p.z || 0 };
      rig.style.transition = ms > 0 ? 'transform ' + ms + 'ms ' + (curve || SIG) + ', opacity 500ms ' + SIG : 'none';
      rig.style.transform = 'perspective(' + cu(88) + ') translateZ(' + cu(pose.z) + ') rotateY(' + p.ry + 'deg) rotateX(' + p.rx + 'deg) scale(' + p.s + ')';
    }

    // (no far panels round the console any more: four dim glass frames with lines in them stood
    // in the room's sky in every s08 cue -- the kind the owner turned down on the review PDF's
    // page 1, "ไม่ชอบ ดูแปลกๆเป็นกรอบๆ"; the core's went on 20 Nov, these with the audit of 22 Nov.
    // Nothing else read them: the veil, the bloom, the drapes and the panel stand on their own)

    // the room: darkness over the whole stage (lights off), a bloom of light (lights on / the
    // curtains open) -- above the stage's own floor, under the word and the glass
    var veil = mk('veil' + (light0 < 0.5 ? ' down' : ''), rig);
    var bloom = mk('bloom', rig);
    mk('bloomi', bloom);                             // (its glow and its slow life: one animation each, 23 Nov -- see the .css)

    // (no giant word behind the glass any more -- owner 18 Nov, the type pass: no giant
    // backlit words. The state is said once, in the panel, at a headline's size; the slide's
    // `giant` text is simply not drawn)

    // the curtains: two drapes of six folds each, closed over the stage until "open."
    var drapes = null;
    if (device === 'curtains') {
      drapes = mk('drapes' + (prev && prev.device === 'curtains' ? ' still' : ''));
      ['l', 'r'].forEach(function (side) {
        var d = mk('drape drape-' + side, drapes);
        for (var f = 0; f < 6; f++) { var fold = mk('fold fold-' + f, d); fold.style.setProperty('--f', String(f)); }
      });
      ctx.add(rig, drapes);
    }

    // the life of a held shot (owner 18 Nov, "ค้าง": the "Off" card in the dark stood completely
    // still for 2.5 s -- and a line that is over holds the screen until Khun Miki has asked
    // the next thing). Ambient, in the room and in every state alike: dust in the console's
    // light (two sparse layers drifting against each other), the foot of the shot graded
    // down behind the subtitle, the console's own glow breathing under the card ...
    mk('motes m1', rig); mk('motes m2', rig);
    mk('floorgrade', rig);
    mk('aura', rig);

    // the panel
    var panel = fx.glass('panel');
    ctx.add(rig, panel);
    mk('sheen', mk('sclip', panel));                 // ... a slow sheen crossing its glass ...
    var head = mk('head', panel);
    var logo = mk('logo', head);
    var ring = ctx.svg('svg', { cls: 'logo-ring', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6 });
    shape(ring, 'circle', { cx: 12, cy: 12, r: 9.5 });
    shape(ring, 'path', { d: 'M5 14c3 4 6 0 7-3 1 3 4 7 7 3' });
    // (a console that carries on from the cue before keeps its words still: the labels that
    // were already up do not close in again at the cut)
    var still = prev ? 'none' : undefined;
    ctx.add(logo, ring, ctx.text('brand', { cls: 'wmark', reveal: still }));
    var who = mk('who', head);
    ctx.add(who, ctx.text('emma', { cls: 'emma', reveal: still }));
    var meter = mk('meter', who), bars = [];
    for (var m = 0; m < 7; m++) bars.push(mk('', meter));

    var tile = mk('tile', panel);
    var disc = mk('disc' + (lit0 ? ' lit' : ''), tile);
    mk('halo', disc);
    var arcSpin = mk('arcspin' + (working0 ? ' on' : ''), disc);
    var arcSvg = ctx.svg('svg', { cls: 'arc', viewBox: '0 0 100 100', fill: 'none' });
    shape(arcSvg, 'circle', { cx: 50, cy: 50, r: 47, cls: 'arc-track' });
    shape(arcSvg, 'path', { d: 'M50 3a47 47 0 0 1 40 22', cls: 'arc-run' });
    ctx.add(arcSpin, arcSvg);
    var full = ctx.svg('svg', { cls: 'arc full', viewBox: '0 0 100 100', fill: 'none' });
    shape(full, 'circle', { cx: 50, cy: 50, r: 47, cls: 'arc-full' });
    ctx.add(disc, full);
    var core = mk('core', disc);
    ctx.add(core, glyph(device, 'dev'));
    var badge = mk('badge', disc);
    ctx.add(badge, glyph('check'));

    var words = mk('words', tile);
    // (the device's name: on its beat in a request line; already up in the line that says it is
    // done -- it used to vanish at that cut)
    function keyedHere(key) { return (ctx.slide.beats || []).some(function (b) { return [].concat(b.key || []).indexOf(key) >= 0; }); }
    var onBeat = keyedHere('label'), stateBeat = keyedHere('state');
    var label = ctx.has('label') ? ctx.text('label', { cls: 'devlabel', reveal: onBeat ? undefined : still }) : null;
    var state = ctx.has('state') ? ctx.text('state', { cls: 'state' }) : null;
    var dots = mk('dots' + (working0 ? ' on' : ''));
    for (var d = 0; d < 3; d++) mk('', dots);
    // Fix round 19 Nov (review: the status word stood BLANK 0.3-2.2 s between "Turning off" and
    // "Off", the same in l12 and l16): at a join the words of the line before fade out under
    // this frame, and this line's own wait for her to say them -- the panel's state was an
    // empty slot for up to two seconds. A line shows only its own words (the line before's
    // cannot be drawn here), so each word stands in a slot that keeps the AFTER-GLOW of the
    // word the line before left in it: a bar of the same light, as wide as that word, a sheen
    // running through it the way the film's words light up -- the old word cools into it and
    // the new one lights up in it, in place (a cross-fade; nothing moves, nothing is empty).
    var lslot = mk('slot'), sslot = mk('slot');
    ctx.add(lslot, label); ctx.add(sslot, state);
    function afterglow(slot, w) {
      if (!(w > 0.5)) return null;
      var g = mk('aglow', slot); g.style.width = cu(Math.min(44, w));
      return g;
    }
    var sGlow = prev && stateBeat && state ? afterglow(sslot, +prev.stateW) : null;
    var lGlow = prev && onBeat && label ? afterglow(lslot, +prev.labelW) : null;
    function cool(g) { if (g) g.classList.add('out'); }
    // (what the next line's after-glow is measured from: a word only once it has been seen)
    var shown = { state: !!state && !stateBeat, label: !!label && !onBeat };
    function widthCu(el) {
      var W = ctx.stage && ctx.stage.offsetWidth;
      return el && W ? Math.round(el.offsetWidth * 1000 / W) / 10 : 0;
    }
    ctx.add(words, lslot, sslot, dots);

    // the switch: a track that lights (crossfade), a glow that follows the knob, the knob
    // (a test reads the position from this element's pos-on / pos-off: it changes only on
    // the line that says the device is done)
    var toggle = mk('toggle pos-' + knob0, tile);
    mk('tglow', toggle);
    mk('trackon', toggle);
    mk('knob', toggle);
    // ... and the switch's rim catching the light (ambient, in either position: it says
    // nothing about the state -- the knob and the track do, on the line that says "done")
    mk('trim', toggle);

    var cmd = mk('cmd', panel);
    ctx.add(cmd, glyph('chevron', 'prompt'));
    var cmdT = typedText('command', 'cmdtext');
    ctx.add(cmd, cmdT ? cmdT.el : null);
    var send = mk('send', cmd);
    ctx.add(send, glyph('send', 'g-send'), glyph('check', 'g-check'));
    var streak = mk('streak', panel);

    // her courtesy words: a satellite chip arriving on an arc
    var chip = null;
    if (ctx.has('chip')) {
      var inner = mk('chipin');
      ctx.add(inner, glyph('chat'), ctx.text('chip', { cls: 'chiptext' }));
      var side = ctx.opt('chip_side', 'left') === 'right' ? 'right' : 'left';
      chip = arcChip(inner, 'chip-' + side, side);
      ctx.add(rig, chip.el);
    }
    ctx.add(ctx.stage, scene);

    // the opening pose: from the console of the cue before (no fly-in), or from depth
    var home = { ry: tilt, rx: 2.5, s: 1, z: 0 };
    if (prev) { setPose(prev.pose, 0); ctx.after(40, function () { setPose(home, 900); }); }
    else if (late) setPose(home, 0);
    else { rig.classList.add('flying'); setPose({ ry: tilt - 6, rx: 4, s: 1, z: -60 }, 0); ctx.after(40, function () { rig.classList.remove('flying'); setPose(home, 900, EIN); }); }
    if (!late) cam.to({ scale: 1.045 }, 9000, 'inout');

    // a console going on from the line before (a join: the new frame over the old at once):
    // its ambient loops go on from where they were -- the dust does not jump and the glow
    // does not dip at the join. `born` = when this console's loops began, `seen` = its last
    // frame; the stylesheet starts every loop `--age` into itself.
    var born = prev && typeof prev.born === 'number' ? prev.born : null, seen = prev && typeof prev.seen === 'number' ? prev.seen : 0;
    if (born !== null) scene.style.setProperty('--age', Math.max(0, Math.round(seen - born + 60)) + 'ms');

    // the meter breathes with the clock; louder while she is speaking
    var heard = 0;
    ctx.frame(function (t) {
      if (born === null) born = t;
      seen = t;
      for (var i = 0; i < bars.length; i++) {
        var a = 0.28 + 0.72 * Math.abs(Math.sin(t / 210 + i * 1.9) * Math.sin(t / 530 + i));
        // (translateZ(0): each bar a small layer the compositor scales -- a 2D scale written every
        // frame repainted the console's whole head bar 60 times a second, 23 Nov)
        bars[i].style.transform = 'translateZ(0) scaleY(' + (0.2 + a * (0.35 + 0.65 * heard)).toFixed(3) + ')';
      }
    });

    var toLight = ctx.opt('light1', light0);
    return {
      beat: function (b, late2) {
        // polish after a beat: a few ms of choreography; at once when late
        function then(ms, fn) { if (late2) fn(); else ctx.after(ms, fn); }
        if (b.do === 'chip') { if (chip) chip.show(); }
        else if (b.do === 'work') {
          arcSpin.classList.add('on'); dots.classList.add('on');
          // (the toggle stays where it was: "Turning on ... now" is a request -- it flips on the
          // line that says it is done, s08_l08/l12/l16; review 18 Nov saw it blue on "Turning")
          if (state) state.classList.add('in');
          if (label) label.classList.add('in');
          cool(sGlow); cool(lGlow); shown.state = !!state; shown.label = !!label;
          follow(cmdT, b, late2);
        } else if (b.do === 'sent') {
          if (!late2) { send.classList.add('pulse'); streak.classList.add('go'); }
        } else if (b.do === 'close') {
          full.classList.add('on'); arcSpin.classList.remove('on');
          if (!late2) setPose({ ry: 0, rx: 0, s: 0.9 }, 900);
          follow(cmdT, b, late2);
        } else if (b.do === 'done') {
          // 1. the switch: pressed, travels, its glow follows
          full.classList.add('on'); arcSpin.classList.remove('on'); dots.classList.remove('on');
          toggle.classList.remove('pos-mid'); toggle.classList.remove('pos-on'); toggle.classList.remove('pos-off');
          if (!late2) toggle.classList.add('flip');
          toggle.classList.add('pos-' + ctx.opt('knob1', 'on'));
          if (ctx.opt('lit1', true)) disc.classList.add('lit'); else disc.classList.remove('lit');
          panel.classList.add('done'); send.classList.add('ok');
          if (state) state.classList.add('in');
          cool(sGlow); cool(lGlow); shown.state = !!state;
          if (late2) { if (cmdT) cmdT.set(1); } else if (cmdT && !runs.length) cmdT.play(900);
          then(140, function () { badge.classList.add('on'); });
          // 2. the room follows the switch once the knob has landed
          var dark = toLight < light0 - 0.01, bright = toLight > light0 + 0.01;
          if (dark) then(260, function () {
            veil.classList.add('down'); if (!late2) veil.classList.add('anim');
            ctx.light(toLight, late2 ? 0 : 1100);
          });
          if (bright) then(220, function () {
            veil.classList.remove('down'); veil.classList.remove('anim'); if (!late2) veil.classList.add('lift');
            ctx.light(toLight, late2 ? 0 : 1000);
          });
          if (drapes) then(150, function () { drapes.classList.add('open'); if (!late2) drapes.classList.add('anim'); });
          if (ctx.opt('lit1', true) && (bright || drapes)) then(drapes ? 320 : 300, function () {
            bloom.classList.add('on'); if (!late2) bloom.classList.add('anim');
          });
          if (!late2) cam.to({ scale: 0.97 }, 2600, 'inout');
        }
      },
      progress: function (f) {
        heard = f > 0.02 && f < 0.97 ? 1 : 0.25;
        for (var i = 0; i < runs.length; i++) runs[i].typed.set((f - runs[i].from) / runs[i].span);
      },
      settle: function () { runs.forEach(function (r) { r.typed.set(1); }); if (cmdT) cmdT.set(1); heard = 0.25; },
      handover: function () {
        return { mode: 'console', device: device, pose: { ry: pose.ry, rx: pose.rx, s: pose.s, z: 0 }, born: born, seen: seen, view: cam.view(),
                 stateW: shown.state ? widthCu(state) : 0, labelW: shown.label ? widthCu(label) : 0 };
      }
    };
  }

  // ======================================================================================
  //  BRAND
  // ======================================================================================
  // No mark any more (owner 20 Nov, review PDF p.17 on s10_l05: "โลโก้แปลกๆไม่สวย"; 23 Nov on s09_l02:
  // "รูปโลโก้ไม่สวยให้เปลี่ยน ไม่ต้องเอาพื้นหลัง เอาโลโก้ออกไปเลย" -- the chrome clip stood on the black disc of
  // its own frame over the blue stage, and the way into the project was its ring). The story's title
  // carries the name now, each half landing on her words ("The story of" / "Embassy World"), and the way
  // into the story's video is a window onto the project that the camera dives into.
  //
  // The project's frame, full screen, is s09's LAST frame and s10's FIRST: one join (the same act going on
  // in its mode -- the new frame is there at once over the old), so both are built by framePic(): the
  // picture's 2560 x 1600 at FRAME_K cu a source pixel, its (1280, 1004) at the stage's middle, drifting on
  // the core's clock with the same numbers, under the same grade. (23 Nov: the frame was 1.0225x of a box
  // registered to the old logo's ring, and stopped 2cu short of the screen's foot -- the stage showed in a
  // band under the subtitle's box, "ทำไมไม่เต็มจอ". Its box now runs past the foot and out to a 2.2:1
  // screen's edges either side, for the bleed below; the framing is the old one within 2.5cu.)
  var FRAME_K = 0.0485;
  var MID_X = 50, MID_Y = 28.125;                  // the stage's middle (100 x 56.25cu)

  function buildBrand() {
    ctx.bg('full');
    var world = ctx.el('world');
    ctx.add(ctx.stage, world);
    // options.look 'land' (owner 20 Nov, review PDF p.17 on s10_l05: "ฉากนี้ขอเป็นภาพที่เกี่ยวข้องกับสิ่งที่พูด
    // ... โลโก้แปลกๆไม่สวย ... พื้นหลังฉากหลังทำเป็นแบบสไตล์ แลนด์ดิ่งเพจ อินไวเทชั่น"): the line opens on the
    // project itself, full screen (the frame the story line dived into), and ends on the project's front as
    // a landing page's hero: the picture across the screen, the title over its sky, the invitations over its
    // foot (the owner's own landing page and agent-preview invitation: one full-bleed picture, a wide-tracked
    // title, glass calls to action).
    var land = ctx.opt('look', '') === 'land';
    if (land) ctx.root.classList.add('sb-land');
    // the brand line this one goes on from (s09 -> s10: a join -- its last frame is this one's first)
    var joined = ctx.prev && ctx.prev.act === 'smarthome_brand' && ctx.prev.state && ctx.prev.state.mode === 'brand' ? ctx.prev.state : null;

    function framePic(key) {
      var view = fx.parallax(ctx.picture(key), { amount: 0.8 });
      view.drift();
      var pic = mk('heropic');
      pic.style.left = cu(MID_X - 1280 * FRAME_K); pic.style.top = cu(MID_Y - 1004 * FRAME_K);
      pic.style.width = cu(2560 * FRAME_K); pic.style.height = cu(1600 * FRAME_K);
      ctx.add(pic, view.el);
      return pic;
    }

    // ---- the bleed (owner 23 Nov on s10, his preview 2.2:1 wide: "ทำไมไม่เต็มจอ" -- the courtyard stopped a
    // fifth short of the right edge). Two things. The frame's box was named `hero` until that day and the
    // mirror page's own `html.mirror .hero { position: fixed; inset: 0 }` took it: it hung from the SCREEN's
    // left edge instead of the stage's, past the box's clip, and left bare stage on the right (the box is
    // .sbhero now). And the stage is a 16:9 box: on a wider screen (the event's TV is ~1.9:1) bare stage
    // stands either side of it. While the project's frame is the whole shot, its picture now goes on to the
    // screen's edges: a layer fixed to the screen (the one way past the box's clip) holds two wings, each a
    // copy of the frame's picture on the frame's own box -- so it carries the picture on across the box's
    // edge -- under the grade that edge has. While the core moves the line (a cut, a dissolve: a transform
    // on the box) the layer is held in the box and the wings are 0 wide; on a 16:9 screen they are 0 wide
    // always. They light up only once they are there (the end of a cut is not a pop), at once when the line
    // before held them up (the join), and they go before the frame becomes a card.
    function makeBleed(key) {
      var layer = mk('sbwings'), boxes = [], wings = [], state = 'down', stopWatch = null;
      ['l', 'r'].forEach(function (side) {
        var wing = mk('sbwing sbwing-' + side, layer);
        var box = mk('sbframe', wing);
        ctx.add(box, framePic(key));
        mk('sbwgrade', wing);
        mk('sbwtitle', wing);
        boxes.push(box); wings.push(wing);
      });
      return {
        el: layer, boxes: boxes,
        up: function () { return state === 'up'; },
        // (once they have a width: at once, or lit up; a screen that never gives them one -- 16:9 -- stops asking)
        watch: function (now) {
          if (state !== 'down') return;
          state = 'waiting';
          var t0 = null;
          stopWatch = ctx.frame(function (t) {
            if (t0 === null) t0 = t;
            if (wings[0].offsetWidth > 0) {
              if (now) layer.classList.add('now');
              layer.classList.add('on');
              state = 'up';
            } else if (t - t0 < 6000) return;
            else state = 'down';
            stopWatch(); stopWatch = null;
          });
        },
        hide: function (now) {
          if (stopWatch) { stopWatch(); stopWatch = null; }
          state = 'gone';
          if (now) layer.classList.add('now'); else layer.classList.remove('now');
          layer.classList.remove('on');
        }
      };
    }

    var landBg = null;
    if (land && ctx.has('endpic')) {
      var ep = ctx.picture('endpic');
      landBg = mk('landbg', world);
      // (options.end_zoom: the picture's own zoom in its drift -- the owner's front of 20 Nov,
      // page 4, exists at 2048 px only: at 1.03, times the hero's own 1.06 pull-back, the most it
      // is enlarged at 1080p is ~1.02x (1.09 of the screen's 1920 at the start, 1.03 settled);
      // still 1.5cu of room each side for the 0.6cu drift)
      // (never past the old 1.06: a bigger number would blow the owner's 2048 px front up -- review 22 Nov)
      var lv = fx.parallax(ep, { amount: 0.6, zoom: Math.min(1.06, ctx.opt('end_zoom', 1.06)) });
      lv.drift();
      ctx.add(landBg, ctx.add(mk('landpic'), lv.el), mk('landgrade'));
    }

    // ---- the title: one short headline (owner 18 Nov, the type pass: no giant backlit words). The core
    // lights it up left to right on its beat, blooms its halo behind it, and takes it out and brings it back
    // (fx.depart). s09's has two lines, each on her own words: "The story of", then the name -- the logo
    // that said "Embassy World" is gone (23 Nov) ----
    var titleT = ctx.has('giant') ? ctx.text('giant', { cls: 'cs-type-h1' }) : null;
    var nameT = ctx.has('name') ? ctx.text('name', { cls: 'cs-type-h1 sbname' }) : null;
    if (titleT) titleT.style.fontSize = cu(ctx.opt('title_size', 5.4));
    if (nameT) nameT.style.fontSize = cu(ctx.opt('name_size', 7.2));
    if (titleT || nameT) {
      var tbox = mk('titlebox' + (nameT ? ' named' : ''), world);
      if (titleT) ctx.add(mk('tline', tbox), titleT);
      if (nameT) ctx.add(mk('tline', tbox), nameT);
    }

    // ---- the reel (s09): its pictures over and over, so the strip never runs out (three times when
    // there are fewer than six: only the owner's final-design renders since 23 Nov -- four of them) ----
    var reel = null, strip = null, reelPos = 0, reelV = 0, reelGoal = 0;
    if (ctx.has('reel1')) {
      reel = mk('reel'); strip = mk('strip', reel);
      var reelN = 0;
      for (var q = 1; q <= 8; q++) if (ctx.has('reel' + q)) reelN++;
      for (var pass = 0; pass < (reelN < 6 ? 3 : 2); pass++) {
        for (var r = 1; r <= 8; r++) {
          if (!ctx.has('reel' + r)) continue;
          var rc = mk('reelcard', strip); var rp = ctx.picture('reel' + r); if (rp) ctx.add(rc, rp.el);
        }
      }
      ctx.add(world, reel);
    }

    // ---- the window (s09): the project's frame as a screen in the middle of the stage, on "the story of";
    // the line over, the camera dives into it until it is the whole screen -- where the hall's story video
    // starts, and the frame the closing line opens on. The window IS the frame's box, scaled about the
    // stage's middle (square-cornered: a rounded corner would be the stage showing at the screen's corners
    // once it fills it -- a border-radius cannot shrink as it grows, it does not animate here); its edge and
    // its glow are outside the box, off the screen at the end. Held by the core with its picture until her
    // words bring it in.
    var win = null, winClip = null, winOpen = false, wv = null, wTween = null, dg = null;
    var WIN = { s: ctx.opt('win.s', 0.3), x: MID_X, y: ctx.opt('win.y', 27) };
    if (ctx.has('into')) {
      win = mk('sbwinwrap');
      mk('sbwinglow', win);
      winClip = mk('sbwin', win);
      ctx.add(winClip, framePic('into'));
      ctx.hold(win, 'into');
      wv = { s: WIN.s, x: WIN.x, y: WIN.y };
    }
    function putWin() {
      win.style.transform = 'translate3d(' + cu(wv.x - MID_X) + ', ' + cu(wv.y - MID_Y) + ', 0) scale(' + wv.s.toFixed(4) + ')';
      // (the dive's grade follows the camera's own number, in log scale like the zoom: none at the
      // window's size, all of it as the frame fills the screen -- it ends as s10's own grade)
      if (dg) dg.style.opacity = String(Math.round(Math.max(0, Math.min(1, Math.log(wv.s) / Math.log(WIN.s) * -1 + 1)) * 1000) / 1000);
    }
    function tweenWin(to, ms, curve, done) {
      if (wTween) { wTween(); wTween = null; }
      if (late || !(ms > 0)) {
        if ('s' in to) wv.s = to.s;
        if ('x' in to) wv.x = to.x;
        if ('y' in to) wv.y = to.y;
        putWin(); if (done) done(); return;
      }
      var from = { s: wv.s, x: wv.x, y: wv.y }, t0 = null;
      wTween = ctx.frame(function (t) {
        if (t0 === null) t0 = t;
        var e = curve(Math.min(1, (t - t0) / ms));
        // (a zoom is even to the eye in log scale)
        if ('s' in to) wv.s = Math.exp(lerp(Math.log(from.s), Math.log(to.s), e));
        if ('x' in to) wv.x = lerp(from.x, to.x, e);
        if ('y' in to) wv.y = lerp(from.y, to.y, e);
        putWin();
        if (t - t0 >= ms) { wTween(); wTween = null; if (done) done(); }
      });
    }
    if (win) putWin();
    // (the reel never runs behind the window: review 23 Nov, a card's edge peeked out beside it and one was
    // half under it -- the owner had just turned down pictures over pictures in s10's strip, "ภาพซ้อนกัน".
    // The reel is see-through over the window's place and GAP_CU either side of it, fading in over
    // FADE_CU beyond that: a card goes out before it reaches the window and comes back after it -- from
    // the reel's first frame, so the window opens into a gap that is already there)
    if (reel && win) {
      var GAP_CU = 3, FADE_CU = 5, half = WIN.s * 50;
      reel.classList.add('gapped');
      [WIN.x - half - GAP_CU - FADE_CU, WIN.x - half - GAP_CU, WIN.x + half + GAP_CU, WIN.x + half + GAP_CU + FADE_CU].forEach(function (v, i) {
        reel.style.setProperty('--gap' + i, (Math.round(v * 100) / 100) + '%');
      });
    }
    function openWin(late2) {
      if (!win || winOpen) return;
      winOpen = true;
      win.classList.add('in');
      if (late2) { tweenWin({ s: WIN.s, x: WIN.x, y: WIN.y }, 0); return; }
      // it comes up out of the depth, decelerating into its place
      wv.s = WIN.s * 0.9; wv.y = WIN.y + 0.8; putWin();
      tweenWin({ s: WIN.s, y: WIN.y }, 1100, EASE.enter);
    }

    // ---- a gallery of cards (s10) ----
    var hero = null, heroPic = null, wheel = null, front = -1;
    if (ctx.has('full')) {
      hero = mk('sbhero');
      heroPic = framePic('full');
      ctx.add(hero, heroPic, mk('grade'));
    }
    var cards = [];
    ['w0', 'w1', 'w2', 'w3', 'w4', 'w5', 'w6'].forEach(function (k) {
      if (!ctx.has(k)) return;
      var c = mk('wcard'); var p = ctx.picture(k); if (p) ctx.add(c, p.el); cards.push(c);
    });
    if (cards.length) {
      // (the middle card is the front one: the picture that is pulled back into it -- `full` -- is its own.
      // Owner 23 Nov, "ภาพซ้อนกัน": 26cu cards on a 58cu cylinder 24 degrees apart are 24.7cu apart where
      // they meet -- every pair overlapped by a card's edge, and the two outer cards of seven faced away (never
      // drawn). The strip is five, the cylinder and the step options.wheel's, set so a gap of light stands
      // between every two cards; a test measures it in the page)
      front = Math.floor(cards.length / 2);
      wheel = fx.wheel(cards, { radius: ctx.opt('wheel.r', 58), step: ctx.opt('wheel.step', 24), at: front, cls: 'wheelbox' });
      ctx.add(world, wheel.el);
    }
    var bleed = ctx.opt('bleed', false) === true && (hero || win) ? makeBleed(hero ? 'full' : 'into') : null;
    if (bleed) ctx.add(world, bleed.el);
    // (the picture is OVER the wheel: as it is pulled back into the wheel's front card, the cards
    // beside that one come out from behind it -- fix round 19 Nov: it used to land alone, a
    // small thumbnail for 1.4 s, before the row came)
    if (hero) ctx.add(world, hero);
    if (win) ctx.add(world, win);

    // ---- the glass panel of shot B and the headline of shot C. Plain phrases the core lights
    // up on their beats, left to right (handover 18 Nov, the type pass: they were typed in with
    // a caret -- the console's command line is the one typed thing left, a device doing its
    // job); the headline goes the core's way out ----
    var panel = null;
    if (ctx.has('l1')) {
      panel = fx.glass('speak');
      var phd = mk('phead', panel);
      ctx.add(phd, ctx.text('emma', { cls: 'pwho' }));
      ctx.add(panel, ctx.text('l1', { cls: 'pline' }), ctx.text('l2', { cls: 'pline' }));
      ctx.add(world, panel);
    }
    var gkT = ctx.has('gk') ? ctx.text('gk', { cls: 'cs-type-h2 gktitle' }) : null;
    if (gkT) ctx.add(world, ctx.add(mk('gk'), gkT));

    // ---- chips (s09): arriving on an arc from their own side ----
    var chips = [];
    ['chip1', 'chip2'].forEach(function (k, i) {
      if (!ctx.has(k)) return;
      var inner = mk('chipin');
      ctx.add(inner, glyph(i ? 'chevron' : 'chat'), ctx.text(k, { cls: 'chiptext' }));
      var c = arcChip(inner, 'bchip', i ? 'bright' : 'bleft');
      ctx.add(world, c.el); chips.push(c);
    });

    // ---- the invitation cards (s10 shot D) ----
    var inv = [];
    [['c1t', 'c1h', 'search'], ['c2t', 'c2h', 'chat'], ['c3t', 'c3h', 'house']].forEach(function (c, i) {
      if (!ctx.has(c[0])) return;
      var card = fx.glass('inv inv-' + (i + 1));
      var ico = mk('ico', card); ctx.add(ico, glyph(c[2]));
      var col = mk('icol', card);
      // (its two lines light up with the card, the core's way: no caret)
      var tg = ctx.text(c[0], { cls: 'invtag' });
      var hd = ctx.text(c[1], { cls: 'invh' });
      ctx.add(col, tg, hd);
      ctx.add(world, card);
      inv.push({ el: card, tag: tg, head: hd });
    });

    // ---- the opening ----
    if (hero) hero.classList.add('on');
    // (the wings: at once when the line before held them up -- the join from s09's last frame -- else lit up
    // once the screen gives them a width)
    if (bleed && hero) bleed.watch(late || !!(joined && joined.bleed));

    // the reel: comes up to speed and slows to a drift (eased both ways), cu per second
    var stopReel = null;
    if (strip) {
      var lastT = null;
      stopReel = ctx.frame(function (t) {
        var dt = lastT === null ? 0 : Math.min(100, t - lastT); lastT = t;
        reelV += (reelGoal - reelV) * Math.min(1, dt / 520);
        reelPos -= reelV * dt / 1000;
        strip.style.transform = 'translate3d(' + cu(reelPos) + ', 0, 0)';
      });
    }

    // the pull back ("and I'm ready"): the picture's own slow push (CSS .near: 1 -> 1.05 over
    // 14 s on the signature curve) is taken over where it stands, then the act tweens it, in
    // log scale, down into the wheel's front card; there the wheel's own card -- the same
    // picture, its corners rounded -- comes up under it and it fades (the corners of a picture
    // that fills the screen cannot round as it shrinks: a border-radius does not animate here)
    var diveEnd = 0, nearAt = 0;
    function frames(cls, on) {
      [hero].concat(bleed ? bleed.boxes : []).forEach(function (e) { if (e) { if (on) e.classList.add(cls); else e.classList.remove(cls); } });
    }
    function pullHero(late2) {
      if (!hero) return;
      var S1 = ctx.opt('card.s', 0.26), Y1 = ctx.opt('card.y', -8.6);
      var s0 = 1 + 0.05 * EASE.sig(Math.min(1, Math.max(0, (Date.now() - nearAt) / 14000)));
      hero.classList.remove('near');
      hero.style.transition = 'opacity 420ms ' + SIG;
      var put = function (s, y) { hero.style.transform = 'translate3d(0, ' + cu(y) + ', 0) scale(' + s.toFixed(4) + ')'; };
      var landed = function (now) {
        var slot = wheel && wheel.slots[front];
        if (slot) { slot.classList.add('fast'); slot.classList.add('in'); }
        if (now) hero.classList.add('faded'); else ctx.after(260, function () { hero.classList.add('faded'); });
      };
      if (late2 || late) { put(S1, Y1); landed(true); return; }
      put(s0, 0);
      var t0 = null, stop = ctx.frame(function (t) {
        if (t0 === null) t0 = t;
        var k = Math.min(1, (t - t0) / PULL_MS), e = EASE.pull(k);
        put(Math.exp(lerp(Math.log(s0), Math.log(S1), e)), lerp(0, Y1, e));
        if (k >= 1) { stop(); landed(false); }
      });
    }

    // options.end 'dive' (owner 20 Nov, review PDF p.16 on s09_l02: "เปิดวิดีโอเต็มจอนะ จะทำโมชั่นแบบไหนก็ได้
    // ก่อนเข้าวิดีโอ"): once she has said it, a beat later the camera dives into the window until the project's
    // frame fills the screen -- the motion into the story video, which then plays full screen (the hall's
    // video; this screen holds the frame until the next line, the closing, which opens on it)
    var dived = false;
    function diveIn() {
      if (dived || !win) return;
      dived = true;
      var go2 = function () {
        chips.forEach(function (c) { c.el.classList.remove('in'); c.chip.classList.remove('in'); });
        if (reel) reel.classList.remove('in');
        fx.depart(titleT); fx.depart(nameT);
        openWin(true);
        win.classList.add('diving');
        // (the grade in the window itself, not over the stage: over the stage it dimmed the box and not
        // the bare stage beside it on a wide screen -- the box's edges showed as the camera went in)
        dg = mk('pgrade', winClip);
        var landed = function () {
          win.classList.add('landed');
          if (stopReel) { stopReel(); stopReel = null; }
          if (bleed) bleed.watch(late);
        };
        if (late) { tweenWin({ s: 1, x: MID_X, y: MID_Y }, 0); landed(); return; }
        // anticipation: the window draws back a little, then the camera goes in (2.7 s in log scale,
        // slow in and slow out)
        tweenWin({ s: WIN.s * 0.94, y: WIN.y + 0.4 }, 320, EASE.sig, function () {
          tweenWin({ s: 1, x: MID_X, y: MID_Y }, DIVE_MS + 500, EASE.dive, landed);
        });
      };
      if (late) go2(); else ctx.after(900, go2);
    }
    return {
      beat: function (b, late2) {
        function then(t, fn) { if (late2) fn(); else ctx.after(t, fn); }
        switch (b.do) {
          case 'title':                                          // (the core lights the title up on this beat)
            if (land && hero) { hero.classList.add('titled'); if (bleed) bleed.el.classList.add('titled'); }   // (... and the sky darkens under it: not in the first frame)
            break;
          case 'chip1': if (chips[0]) chips[0].show(); break;
          case 'reel':
            if (chips[1]) chips[1].show();
            if (reel) { reel.classList.add('in'); reelGoal = 2.4; if (late2) reelV = reelGoal; }
            break;
          case 'story': openWin(late2); break;                   // (the core lights "The story of" up)
          case 'name': if (win) win.classList.add('lit'); break; // (... and the name: the window lights with it)
          case 'ring':                                           // (s10: the slow push while she speaks over the picture)
            if (hero) { frames('near', true); nearAt = Date.now(); }
            break;
          case 'fly':                                            // (the title cools and fades)
            fx.depart(titleT); diveEnd = Date.now();
            break;
          case 'panel1':
            // (its glass comes up over the picture as the dive lands, never over the zoom: two
            // primary moves at once. Her words are not early -- only, at most, a moment late)
            if (panel) then(Math.max(0, diveEnd - 450 - Date.now()), function () { panel.classList.add('in'); });
            break;
          case 'panel2': break;                                  // (the core lights its line up)
          case 'pull':
            if (panel) panel.classList.remove('in');
            // (the wings go first: the frame is a card from here on, and a card has no wings)
            if (bleed) bleed.hide(late2);
            // the camera pulls back: the full picture becomes the wheel's front card (1.65 s, in
            // log scale), and the cards either side of it come out from behind it on the way --
            // one view gathering, not a thumbnail left alone (fix round 19 Nov)
            if (wheel) then(late2 ? 0 : 700, function () {
              wheel.el.classList.add('on');
              wheel.slots.forEach(function (s, i) { if (Math.abs(i - front) === 1) s.classList.add('in'); });
            });
            pullHero(late2);
            break;
          case 'pair1':
            // (the row builds on round the card -- its cards' fade is 1.4 s, see the CSS)
            if (wheel) { wheel.el.classList.add('on'); wheel.slots.forEach(function (s, i) { if (Math.abs(i - front) <= 2) s.classList.add('in'); }); }
            break;                                               // (the headline: the core lights it up)
          case 'pair2': if (wheel) wheel.slots.forEach(function (s) { s.classList.add('in'); }); break;
          case 'pair3': break;                                   // (the row is whole: it holds, alive)
          case 'back':
            if (hero) hero.classList.add('gone2');
            fx.depart(gkT);                                      // (the headline cools and fades, left to right)
            if (wheel) wheel.el.classList.add('far');
            // the end frame as a landing page's hero: the project's front comes up across the
            // screen (a slow push from a little closer), the title over its sky, the invitations
            // over its foot
            if (landBg) landBg.classList.add('on');
            if (inv[0]) inv[0].el.classList.add('in');
            then(450, function () { fx.depart(titleT, false); });   // (... and the title lights up again over it)
            break;
          case 'inv2': if (inv[1]) inv[1].el.classList.add('in'); break;
          case 'inv3': if (inv[2]) inv[2].el.classList.add('in'); break;
          case 'glint2': inv.forEach(function (c) { c.el.classList.add('relax'); }); break;   // (the outer cards ease towards the room)
        }
      },
      progress: function (f) {
        for (var i = 0; i < runs.length; i++) runs[i].typed.set((f - runs[i].from) / runs[i].span);
      },
      settle: function () {
        runs.forEach(function (r) { r.typed.set(1); });
        // the reel slows to a drift, never to a stop (a line that is over still holds the
        // screen for a moment: a strip that froze there read as a hang)
        reelGoal = 0.45;
        if (ctx.opt('end', '') === 'dive') diveIn();
      },
      // (the next brand line -- s10 after s09 -- opens on this frame: whether its wings were up)
      handover: function () { return { mode: 'brand', dived: dived, bleed: !!(bleed && bleed.up()) }; }
    };
  }

  return mode === 'console' ? buildConsole() : buildBrand();
});
