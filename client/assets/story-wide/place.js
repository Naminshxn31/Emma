/* One place (TEMPLATES.spotlight): its pictures over the whole screen, its name, and what she says of it one phrase at
   a time. The photo (and `shots`, more pictures of the same place) are cut between her lines; the figure with a
   line of its own comes with that line; the phrases without one are spread over the lines. */
(function () {
  'use strict';
  var SW = window.StoryWide;
  SW.scenes.spotlight = function (S, p) {
    var ph = p.photo || {}, refs = [ph.asset || p.base_asset].concat((p.shots || []).map(function (s) { return s.asset; })).filter(Boolean);
    var n = Math.max(1, S.n || (S.data.beats || []).length);
    var total = 0;
    for (var i = 0; i < n; i++) total += SW.secs(S, i);
    var moves = ['push', 'right', 'pull', 'left'];
    var shots = refs.map(function (r, k) { return SW.shot(S, r, { move: moves[k % 4], secs: total / refs.length + 2 }); });
    var head = SW.head(S, ph.title_en || (p.spot || {}).label_en || '', ph.sub_en || (p.spot || {}).sub_en || '');
    var slot = SW.phrases(S), steps = refs.length > 1 ? SW.steps(S, refs.length) : null;
    // the phrases: the ones with a line of their own there, the rest in order over the lines from the second on
    var plain = [], byLine = {};
    (ph.chips_en || []).forEach(function (c) {
      var fig = c && typeof c === 'object';
      var item = fig ? { figure: { label: c.label_en, value: c.value_en || '' } } : { text: c };
      // a figure with no line of its own is said at the end ("it is about 20 square metres")
      if (fig) { var at0 = typeof c.line === 'number' ? Math.min(n - 1, c.line) : n - 1; (byLine[at0] = byLine[at0] || []).push(item); }
      else plain.push(item);
    });
    // ... over the lines that have no figure (a figure stands alone while she says it), the opening line left to the name
    var free = [];
    for (var f = n > 1 ? 1 : 0; f < n; f++) if (!byLine[f]) free.push(f);
    if (!free.length) free = [n > 1 ? 1 : 0];
    plain.forEach(function (it, k) {
      var line = free[Math.min(free.length - 1, Math.floor(k * free.length / Math.max(1, plain.length)))];
      (byLine[line] = byLine[line] || []).push(it);
    });
    var cur = null, at = -1, said = null;
    function shot(k) {
      if (k === at || !shots[k]) return;
      var from = cur;
      at = k; cur = shots[k];
      SW.cut(S, cur, from);
      if (steps) steps.at(k);
    }
    function say(it, secs) { said = it; slot.say(it.text, { secs: secs, figure: it.figure }); }
    function line(i) {
      shot(Math.min(refs.length - 1, Math.floor(i * refs.length / n)));
      var list = byLine[i] || [];
      if (!list.length) return;
      var each = SW.secs(S, i) / list.length;
      list.forEach(function (it, k) {
        if (S.instant) say(it, 0);
        else S.later(k * each * 1000, function () { say(it, Math.min(1.4, each * 0.5)); });
      });
    }
    return {
      beat: function (i) { if (i === 0) head._show(); line(i); },
      rest: function () { head._show(); shot(refs.length - 1 < 0 ? 0 : at < 0 ? 0 : at); if (!said && plain[0]) say(plain[0], 0); if (cur) cur.classList.add('on'); }
    };
  };
})();
