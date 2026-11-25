/* One room type (TEMPLATES.details): its model alone on black, wire then solid, what it has one phrase at a time, its
   size large -- then its own interiors over the whole screen, cut one after another with the size kept.
   The company (TEMPLATES.assembly): its mark, its figures in a row across the screen, the names, and the awards whole. */
(function () {
  'use strict';
  var SW = window.StoryWide;

  SW.scenes.details = function (S, p) {
    var n = Math.max(1, S.n || (S.data.beats || []).length);
    var stages = (p.lines || []).length ? p.lines.slice(0, 4) : [0, 1, 2];
    var at = { wire: stages[0], solid: stages[1], size: stages.length >= 4 ? stages[2] : stages[1], mood: stages[stages.length >= 4 ? 3 : 2] };
    var model = SW.shot(S, p.wire_asset || p.solid_asset, { move: 'push', fit: 'contain', lift: 0, secs: 16 });
    model.classList.add('un-model');
    var solid = p.wire_asset && p.solid_asset ? model.querySelector('.sw-cam').appendChild(S.img(p.solid_asset, 'un-solid')) : null;
    var moods = p.mood_assets || [], left = 0;
    for (var i = Math.max(0, at.mood); i < n; i++) left += SW.secs(S, i);
    var moves = ['right', 'push', 'left'];
    var shots = moods.map(function (r, k) { return SW.shot(S, r, { move: moves[k % 3], lift: 0, secs: left / Math.max(1, moods.length) + 2 }); });
    var slot = SW.phrases(S), steps = null;
    // the size: kept at the top left once she has said it
    var size = S.add(SW.el('div', 'un-size'));
    var v = p.value ? S.valueNode(p.value, 'un-v') : null;
    if (v) size.appendChild(v.node);
    var meta = [];
    (p.meta_en || []).forEach(function (m) { SW.tidy(m).split(' · ').forEach(function (x) { if (x) meta.push(x); }); });
    var cur = null, done = {}, spoke = 0;
    function shot(s) { var from = cur; cur = s; SW.cut(S, s, from); }
    function sizeOn() { if (done.size) return; done.size = true; size.classList.add('on'); if (v) v.go(); }
    function solidOn() { if (done.solid) return; done.solid = true; if (solid) S.reveal(solid); }
    function metaOver(secs) {
      if (done.meta) return;
      done.meta = true;
      var each = Math.max(1.3, secs / Math.max(1, meta.length));
      meta.forEach(function (m, k) { S.later(k * each * 1000, function () { if (!done.mood) slot.say(m, { secs: 0.7 }); }); });
    }
    function moodOn(secs) {
      if (done.mood) return;
      done.mood = true;
      slot.clear();
      var each = Math.max(2.2, secs / Math.max(1, shots.length));
      shots.forEach(function (s, k) { S.later(k * each * 1000, function () { shot(s); spoke = k; }); });
    }
    return {
      beat: function (i) {
        if (i === 0) shot(model);
        if (i === at.solid) { solidOn(); metaOver(SW.secs(S, i)); }
        if (i === at.size) sizeOn();
        if (i === at.mood && i !== at.wire) moodOn(left);
      },
      rest: function () {
        if (!cur) shot(model);
        solidOn(); sizeOn();
        if (shots.length && !done.mood) { done.mood = true; shot(shots[0]); }
        slot.clear();
        if (cur) cur.classList.add('on', 'sw-cutdone');
      }
    };
  };

  SW.scenes.assembly = function (S, p) {
    var frames = p.frames || [], mark = frames[0] || null, fin = p.final_asset || null;
    var awards = fin ? SW.shot(S, fin, { move: 'pull', lift: 0, secs: 12 }) : null;
    if (awards) awards.classList.add('un-awards');
    var board = S.add(SW.el('div', 'un-board'));
    board.style.bottom = (S.LH - S.TF) + 'px';
    var logo = mark ? board.appendChild(SW.el('div', 'un-logo')) : null;
    if (logo) logo.appendChild(S.img(mark));
    var row = board.appendChild(SW.el('div', 'un-figs'));
    var figs = (p.counters || []).map(function (c) {
      var f = row.appendChild(SW.el('div', 'un-fig'));
      var num = f.appendChild(SW.el('div', 'un-n'));
      if (c.prefix_en) num.appendChild(document.createTextNode(c.prefix_en + ' '));
      var val = num.appendChild(SW.el('span', null, ''));
      if (c.unit_en) num.appendChild(SW.el('small', null, c.unit_en));
      if (c.label_en) f.appendChild(SW.el('div', 'un-l', c.label_en));
      return { c: c, f: f, val: val, on: false };
    });
    var names = board.appendChild(SW.el('div', 'un-names'));
    var chips = (p.chips || []).map(function (c) {
      var b = names.appendChild(SW.el('div', 'un-name', c.label_en || ''));
      return { c: c, b: b };
    });
    var slot = SW.phrases(S), up = null;
    var riseLine = typeof p.rise_line === 'number' ? p.rise_line : null;
    var last = riseLine == null ? null : chips.filter(function (x) { return x.c.line === riseLine; })[0];
    if (last) last.b.style.display = 'none';            // (it stands alone over the awards)
    function fig(x) {
      if (x.on) return;
      x.on = true;
      board.classList.add('up');
      x.f.classList.add('on');
      S.count(x.val, 0, x.c.to || 0, x.c.dec || 0, 1100);
    }
    function rise() {
      if (up || !awards) return;
      up = awards;
      board.classList.add('away');
      SW.cut(S, awards, null);
      if (last) slot.say(last.c.label_en || '', { secs: 0.8 });
    }
    return {
      beat: function (i) {
        if (i === 0) { board.classList.add('on'); }
        figs.forEach(function (x) { if (x.c.line === i) fig(x); });
        var k = 0;
        chips.forEach(function (x) { if (x.c.line === i && x !== last) { var j = k++; S.later(j * 220, function () { x.b.classList.add('on'); }); } });
        if (riseLine != null && i === riseLine) rise();
      },
      rest: function () {
        board.classList.add('on');
        figs.forEach(fig);
        chips.forEach(function (x) { x.b.classList.add('on'); });
        rise();
      }
    };
  };
})();
