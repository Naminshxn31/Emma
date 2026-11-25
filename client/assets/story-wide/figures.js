/* Film scenes for the event screen's figure stories (kit.js is the shared kit; this file adds scenes only):
     features  -- executives (four portraits rising as she names them), park_green (full-screen cuts, the name lit large)
     metric    -- lagoon_islands, units_by_building (a photo, one big counting figure, its parts as glass plates),
                  site_area (the site plan whole and large, the figure under it)
     assembly  -- developer (the logo, three counters, the project names, the awards collage dimmed behind)
     details   -- unit_studio / unit_one_bedroom / unit_two_bedroom (the model wire then solid on black, the size range
                  huge, the meta lines, then the interior photos with the model small in a corner)
   Every word, number and picture comes from the story's params; nothing is written here. A template this file does
   not draw (another topic of the same template) goes to whichever scene was registered before it, else the engine's
   own layout (the scene throws, the engine catches it). Only transform and opacity move; no blur anywhere. */
(function () {
  'use strict';
  var SW = window.StoryWide;
  if (!SW) return;
  var el = SW.el, tidy = SW.tidy;
  function arr(a) { return Array.isArray(a) ? a : []; }
  function num(x) { return typeof x === 'number' && isFinite(x) ? x : null; }
  function fmt(v, dec) {
    return Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0 });
  }
  // the engine's unit (--es-vw) is 0.9% of the stage's height
  function unit(S) { return S.LH * 0.009; }
  // the bottom edge of what a scene may use (above the Thai subtitle), as css
  function floorCss(S, gap) { return 'calc(' + (S.LH - S.TF) + 'px + ' + (gap == null ? 1.2 : gap) + ' * var(--es-vw))'; }
  function total(S) {
    var n = Math.max(1, S.n || (S.data.beats || []).length), t = 0;
    for (var i = 0; i < n; i++) t += SW.secs(S, i);
    return t;
  }
  // register a scene for `tpl`: topics this file does not draw go on to the scene registered before it
  function register(tpl, pick) {
    var prev = SW.scenes[tpl];
    SW.scenes[tpl] = function (S, p) {
      var fn = pick(S.data.id);
      if (fn) return fn(S, p);
      if (prev) return prev(S, p);
      throw new Error('figures: no scene for ' + S.data.id);
    };
  }
  function rise(S, node, delay) {
    if (S.instant || !delay) { node.classList.add('on'); return; }
    S.later(delay, function () { node.classList.add('on'); });
  }

  // ======================================================================== features: executives
  function executives(S, p) {
    var items = arr(p.items).slice(0, 5);
    var foot = p.footnote_en ? S.add(el('div', 'fg-top')) : null;
    var fw = foot ? foot.appendChild(SW.words(p.footnote_en, 'fg-topw')) : null;
    var row = S.add(el('div', 'fg-pts'));
    row.style.bottom = floorCss(S, 1.2);
    var cards = items.map(function (it) {
      var c = row.appendChild(el('div', 'fg-pt fg-in'));
      var pic = c.appendChild(el('div', 'fg-pt-pic'));
      pic.appendChild(S.img(it.asset, 'fg-pt-img'));
      c.appendChild(el('i', 'fg-pt-shade'));
      var t = c.appendChild(el('div', 'fg-pt-txt'));
      t.appendChild(el('div', 'fg-pt-n', tidy(it.label_en)));
      if (it.note_en) t.appendChild(el('div', 'fg-pt-r', tidy(it.note_en)));
      c.appendChild(el('i', 'fg-lit'));
      return c;
    });
    var shown = false;
    function head() { if (shown || !fw) return; shown = true; SW.light(S, fw, 0.9); }
    function name(k) {
      cards.forEach(function (c, j) { c.classList.toggle('cur', j === k); });
      S.reveal(cards[k]);
    }
    return {
      beat: function (i) {
        if (i === 0) head();
        items.forEach(function (it, k) { if (it.line === i) name(k); });
      },
      rest: function () {
        head();
        cards.forEach(function (c) { c.classList.remove('cur'); c.classList.add('on'); });
      }
    };
  }

  // ======================================================================== features: park_green
  function park(S, p) {
    var items = arr(p.items).filter(function (it) { return it.asset; });
    var moves = ['push', 'right', 'pull', 'left'], n = Math.max(1, items.length);
    var shots = items.map(function (it, k) { return SW.shot(S, it.asset, { move: moves[k % 4], secs: total(S) / n + 3 }); });
    var scrim = S.add(el('i', 'fg-scrim'));
    var foot = p.footnote_en ? S.add(el('div', 'fg-top fg-fine', tidy(p.footnote_en))) : null;
    var steps = n > 1 ? SW.steps(S, n) : null;
    var caps = items.map(function (it) {
      var c = S.add(el('div', 'fg-cap'));
      c.style.bottom = floorCss(S, 1.4);
      var nm = c.appendChild(SW.words(it.label_en, 'fg-cap-n'));
      var nt = it.note_en ? c.appendChild(el('div', 'fg-cap-s', tidy(it.note_en))) : null;
      c._n = nm; c._s = nt;
      return c;
    });
    var cur = -1;
    function go(k) {
      if (k === cur || !shots[k]) return;
      var from = cur >= 0 ? shots[cur] : null, old = cur >= 0 ? caps[cur] : null;
      cur = k;
      SW.cut(S, shots[k], from);
      scrim.classList.add('on');
      if (steps) steps.at(k);
      if (foot) rise(S, foot, 900);
      if (old) { old.classList.add('out'); old.classList.remove('on'); }
      var c = caps[k];
      c.classList.remove('out');
      function lit() { c.classList.add('on'); SW.light(S, c._n, 0.8); if (c._s) rise(S, c._s, 450); }
      if (S.instant) { lit(); if (c._s) c._s.classList.add('on'); } else S.later(old ? 500 : 250, lit);
    }
    return {
      beat: function (i) {
        for (var k = 0; k < items.length; k++) if (items[k].line === i) { go(k); break; }
      },
      rest: function () {
        go(items.length - 1);
        var c = caps[cur];
        if (!c) return;
        c.classList.add('on'); c.classList.remove('out'); SW.light(S, c._n, 0);
        if (c._s) c._s.classList.add('on');
        if (foot) foot.classList.add('on');
        scrim.classList.add('on');
        if (shots[cur]) shots[cur].classList.add('on');
      }
    };
  }

  register('features', function (id) { return id === 'executives' ? executives : id === 'park_green' ? park : null; });

  // ======================================================================== metric
  function metric(S, p) {
    var plan = S.data.id === 'site_area';
    var u = unit(S);
    var parts = arr(p.parts), line = num(p.line) != null ? p.line : 0;
    var pic = null, shot = null;
    if (plan) {
      pic = S.add(el('div', 'fg-plan fg-in'));
      pic.style.bottom = 'calc(' + (S.LH - S.TF) + 'px + 22 * var(--es-vw))';
      pic.appendChild(S.img(p.bg_asset, 'fg-plan-img'));
    } else {
      shot = SW.shot(S, p.bg_asset, { move: 'push', secs: total(S) + 3 });
      S.add(el('i', 'fg-scrim fg-scrim-m on'));
    }
    var col = S.add(el('div', 'fg-col' + (plan ? ' fg-col-row' : '')));
    col.style.bottom = floorCss(S, 1.2);
    // the figure: huge, its label under it
    var fig = col.appendChild(el('div', 'fg-fig fg-in'));
    var v = p.value ? S.valueNode(p.value, 'fg-n') : null;
    if (v) fig.appendChild(v.node);
    if (p.label_en) fig.appendChild(el('div', 'fg-fl', tidy(p.label_en)));
    // its parts: glass plates, the figure above the name
    var rowEl = col.appendChild(el('div', 'fg-plates' + (parts.length > 3 ? ' fg-plates-grid' : '')));
    if (parts.length > 3) rowEl.style.gridTemplateColumns = 'repeat(' + parts.length + ', minmax(0, 1fr))';
    var plates = parts.map(function (q) {
      var pl = rowEl.appendChild(SW.glass('fg-plate fg-in'));
      var b = pl.appendChild(el('div', 'fg-pn'));
      if (q.prefix_en) b.appendChild(document.createTextNode(q.prefix_en + ' '));
      var nn = b.appendChild(el('span', null, fmt(0, q.dec || 0)));
      if (q.unit_en) b.appendChild(el('small', null, ' ' + q.unit_en));
      pl.appendChild(el('div', 'fg-pl', tidy(q.label_en)));
      return { el: pl, num: nn, q: q, done: false };
    });
    var opened = false, counted = false;
    function open() {
      if (opened) return; opened = true;
      if (pic) S.reveal(pic); else SW.cut(S, shot, null);
    }
    function showFig() {
      if (counted) return; counted = true;
      S.reveal(fig, function () { if (v) S.later(150, v.go); });
    }
    function showPart(x, delay) {
      if (x.done) return; x.done = true;
      function go() { x.el.classList.add('on'); S.count(x.num, 0, num(x.q.to) || 0, x.q.dec || 0, 900); }
      if (S.instant || !delay) go(); else S.later(delay, go);
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        if (i === line) { open(); showFig(); }
        var j = 0;
        plates.forEach(function (x) {
          var at = num(x.q.line) != null ? x.q.line : line;
          if (at === i) showPart(x, (at === line ? 700 : 0) + (j++) * 200);
        });
      },
      rest: function () {
        open(); showFig();
        fig.classList.add('on'); if (pic) pic.classList.add('on'); if (shot) shot.classList.add('on');
        plates.forEach(function (x) { showPart(x, 0); x.el.classList.add('on'); });
      }
    };
  }
  register('metric', function (id) {
    return id === 'lagoon_islands' || id === 'units_by_building' || id === 'site_area' ? metric : null;
  });

  // ======================================================================== assembly: developer
  function developer(S, p) {
    var counters = arr(p.counters), chips = arr(p.chips);
    // the awards collage waits behind everything, strongly dimmed, up on the line that says awards
    var coll = S.add(el('div', 'fg-coll fg-in'));
    coll.appendChild(S.img(p.final_asset, 'fg-coll-img'));
    coll.appendChild(el('i', 'fg-coll-dim'));
    var logoRef = arr(p.frames)[arr(p.frames).length - 1];
    var logo = S.add(el('div', 'fg-logo fg-in'));
    logo.appendChild(S.img(logoRef, 'fg-logo-img'));
    logo.appendChild(el('i', 'fg-lit on'));
    var cs = S.add(el('div', 'fg-cs'));
    var cEls = counters.map(function (c) {
      var pl = cs.appendChild(SW.glass('fg-cplate fg-in'));
      var nn = el('div', 'fg-cn');
      var sp = nn.appendChild(el('span', null, fmt(0, c.dec || 0)));
      if (c.unit_en) nn.appendChild(el('small', null, c.unit_en));
      pl.appendChild(nn);
      if (c.label_en) pl.appendChild(el('div', 'fg-cl', tidy(c.label_en)));
      return { el: pl, sp: sp, c: c, done: false };
    });
    var cr = S.add(el('div', 'fg-chips'));
    cr.style.bottom = floorCss(S, 1.2);
    cr.style.gridTemplateColumns = 'repeat(' + Math.max(1, chips.length) + ', minmax(0, 1fr))';
    var cEl = chips.map(function (c, k) {
      var ch = cr.appendChild(SW.glass('fg-chip fg-in' + (k === chips.length - 1 ? ' fg-chip-last' : '')));
      ch.appendChild(el('span', null, tidy(c.label_en)));
      return ch;
    });
    var riseLine = num(p.rise_line) != null ? p.rise_line : null, risen = false, logoUp = false;
    function showLogo() { if (logoUp) return; logoUp = true; S.reveal(logo); }
    function showC(x) {
      if (x.done) return; x.done = true;
      S.reveal(x.el, function () { S.count(x.sp, 0, num(x.c.to) || 0, x.c.dec || 0, 1100); });
    }
    function showColl() { if (risen) return; risen = true; S.reveal(coll); }
    return {
      beat: function (i) {
        if (i === 0) showLogo();
        cEls.forEach(function (x) { if (x.c.line === i) showC(x); });
        var j = 0;
        chips.forEach(function (c, k) { if (c.line === i) { var at = j++; S.later(at * 160, function () { S.reveal(cEl[k]); }); } });
        if (riseLine != null && i === riseLine) showColl();
      },
      rest: function () {
        showLogo(); showColl();
        logo.classList.add('on'); coll.classList.add('on');
        cEls.forEach(function (x) { showC(x); x.el.classList.add('on'); });
        cEl.forEach(function (c) { c.classList.add('on'); });
      }
    };
  }
  register('assembly', function (id) { return id === 'developer' ? developer : null; });

  // ======================================================================== details: the three unit models
  function details(S, p) {
    var u = unit(S), LW = S.LW;
    // the model: the left 52% of the stage, wire then solid on near-black (screen-blended)
    var ref = p.solid_asset || p.wire_asset || {};
    var ar = ref.w > 0 && ref.h > 0 ? ref.w / ref.h : 1.25;
    var bh = S.TF - 5.5 * u, bw = Math.min(LW * 0.52, bh * ar);
    bh = bw / ar;
    var bl = LW * 0.032 + (LW * 0.52 - bw) / 2, bt = 3 * u + (S.TF - 5.5 * u - bh) / 2;
    var model = S.add(el('div', 'fg-model fg-in'));
    model.style.cssText += ';left:' + bl.toFixed(1) + 'px;top:' + bt.toFixed(1) + 'px;width:' + bw.toFixed(1) + 'px;height:' + bh.toFixed(1) + 'px';
    var minner = model.appendChild(el('div', 'fg-model-in'));
    if (p.wire_asset) minner.appendChild(S.img(p.wire_asset, 'fg-mimg fg-wire'));
    var solid = p.solid_asset ? minner.appendChild(S.img(p.solid_asset, 'fg-mimg fg-solid fg-in')) : null;
    model.appendChild(el('i', 'fg-lit on'));
    // the size (huge) and the meta lines, the right column
    var side = S.add(el('div', 'fg-side'));
    var v = p.value ? S.valueNode(p.value, 'fg-n fg-nd') : null;
    var area = side.appendChild(el('div', 'fg-area fg-in'));
    if (v) area.appendChild(v.node);
    var metaBox = side.appendChild(el('div', 'fg-meta'));
    var metas = [];
    arr(p.meta_en).forEach(function (t) {
      tidy(t).split(' · ').forEach(function (part) {
        var m = metaBox.appendChild(el('div', 'fg-mline fg-in', part));
        metas.push(m);
      });
    });
    // the interior: tall panels across the model's place, the model shrinking to the corner
    var moods = arr(p.mood_assets).slice(0, 3);
    var mood = S.add(el('div', 'fg-moods'));
    mood.style.cssText += ';left:' + (LW * 0.032).toFixed(1) + 'px;width:' + (LW * 0.52).toFixed(1) + 'px;top:' + (3 * u).toFixed(1) + 'px;height:' + (S.TF - 5.5 * u).toFixed(1) + 'px';
    var mEls = moods.map(function (m) {
      var d = mood.appendChild(el('div', 'fg-mood fg-in'));
      d.appendChild(S.img(m, 'fg-mood-img'));
      d.appendChild(el('i', 'fg-lit on'));
      return d;
    });
    // where the model goes: small, under the meta lines in the right column
    var k = 0.36, tx = LW * 0.6 - bl, ty = S.TF - 3 * u - bh * k - bt;
    var stages = arr(p.lines).length ? arr(p.lines).slice(0, 4) : [0, 1, 2];
    var plan = stages.length >= 4 ? [[0], [1], [2], [3]] : [[0], [1, 2], [3]];
    var done = [];
    var steps = [
      function () { S.reveal(model, function () { S.later(100, function () { model.classList.add('drawn'); }); }); },
      function () { if (solid) S.reveal(solid); metas.forEach(function (m, j) { S.later(j * 130, function () { m.classList.add('on'); }); }); },
      function () { area.classList.add('on'); if (v) S.later(250, v.go); },
      function () {
        model.style.setProperty('--fg-to', 'translate3d(' + tx.toFixed(1) + 'px,' + ty.toFixed(1) + 'px,0) scale(' + k + ')');
        model.classList.add('away');
        mEls.forEach(function (d, j) { S.later(250 + j * 160, function () { S.reveal(d); }); });
      }
    ];
    function stage(n) {
      for (var j = 0; j <= n && j < plan.length; j++) {
        if (done[j]) continue;
        done[j] = true;
        plan[j].forEach(function (s) { steps[s](); });
      }
    }
    return {
      beat: function (i) {
        if (i === 0 && stages[0] !== 0) stage(0);
        stages.forEach(function (line, j) { if (line === i && j < plan.length) stage(j); });
      },
      rest: function () {
        stage(plan.length - 1);
        model.classList.add('on', 'drawn', 'away');
        if (solid) solid.classList.add('on');
        area.classList.add('on');
        metas.forEach(function (m) { m.classList.add('on'); });
        mEls.forEach(function (d) { d.classList.add('on'); });
      }
    };
  }
  register('details', function (id) {
    return id === 'unit_studio' || id === 'unit_one_bedroom' || id === 'unit_two_bedroom' ? details : null;
  });
})();
