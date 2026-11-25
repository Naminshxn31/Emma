/* A run of places or items (TEMPLATES.drill, TEMPLATES.stack): each is a picture over the whole screen, cut to as she
   names it, its name large above the subtitle and lit word by word, its sub line under it; a figure it carries stands
   big beside the name. Items named close together are queued and each stays up DWELL_MS at least. Drawings (plans, the
   developer's timelines) are shown whole on their own sheet; the unit comparison ends with its plans side by side. */
(function () {
  'use strict';
  var SW = window.StoryWide;
  var MOVES = ['push', 'right', 'pull', 'left'];

  function build(S, p) {
    var items = (p.items || []).filter(function (it) { return it && it.asset; });
    var n = Math.max(1, S.n || (S.data.beats || []).length);
    var u = S.LH * 0.009;                              // one unit of the stage, in px
    var capBottom = Math.round(S.LH - S.TF + 1.2 * u);
    var total = 0, i;
    for (i = 0; i < n; i++) total += SW.secs(S, i);
    var timeline = S.data.id === 'developer_projects';   // timelines on white, shown whole
    var white = function () { return timeline; };
    var drawing = function (it) { return it.asset_kind === 'plan' || white(it); };
    var twoUp = items.length === 2 && items.every(function (it) { return it.asset_kind === 'plan'; });
    var lastLine = items.reduce(function (m, it) { return Math.max(m, typeof it.line === 'number' ? it.line : -1); }, -1);

    var steps = items.length > 1 ? SW.steps(S, items.length) : null;
    var capH = Math.round(17 * u);                     // room for a name, its sub line and a figure
    var shots = items.map(function (it, k) {
      var d = drawing(it);
      var sh = SW.shot(S, it.asset, d ? { move: '', fit: 'contain', lift: 0 } : { move: MOVES[k % 4], secs: total / items.length + 2 });
      if (d) {
        var cam = sh.querySelector('.sw-cam'), st = cam.style;
        cam.classList.add('sq-sheet');
        if (white(it)) cam.classList.add('sq-white');
        st.inset = 'auto';
        st.top = Math.round(white(it) ? 2 * u : 8 * u) + 'px';
        st.bottom = (capBottom + (white(it) ? Math.round(14.5 * u) : capH)) + 'px';
        st.left = st.right = white(it) ? '3.4%' : '6%';
      }
      return sh;
    });
    var caps = items.map(function (it) {
      var c = S.add(SW.el('div', 'sq-cap'));
      c.style.bottom = capBottom + 'px';
      var tx = c.appendChild(SW.el('div', 'sq-tx'));
      var name = tx.appendChild(SW.words(it.label_en || '', 'sq-name'));
      if (it.sub_en) tx.appendChild(SW.el('div', 'sq-sub', SW.tidy(it.sub_en)));
      var v = it.value ? S.valueNode(it.value, 'sq-v') : null;
      if (v) c.appendChild(SW.el('div', 'sq-val')).appendChild(v.node);
      c._name = name; c._v = v;
      return c;
    });

    var cur = -1, queue = [], shownAt = -1e9, waiting = false, fired = -1, closed = false, two = null, intro = false;
    var DWELL_MS = 1200;                               // longer than the cut itself: a place that only flashes past is never seen

    function capOut(k) {
      var c = caps[k];
      if (!c || !c.classList.contains('on')) return;
      c.classList.remove('on'); c.classList.add('out');
      S.later(500, function () { c.classList.remove('out'); });
    }
    function show(k) {
      if (k === cur || !shots[k]) return;
      var from = cur >= 0 ? shots[cur] : intro && k !== 0 ? shots[0] : null;
      if (cur >= 0) capOut(cur);
      cur = k;
      SW.cut(S, shots[k], from);
      if (steps) steps.at(k);
      var c = caps[k];
      c.classList.remove('out');
      if (S.instant) { c.classList.add('on'); SW.light(S, c._name, 0); if (c._v) c._v.go(); return; }
      S.later(from ? 420 : 250, function () {
        if (cur !== k) return;
        c.classList.add('on');
        SW.light(S, c._name, Math.min(1.2, SW.secs(S, items[k].line || 0) * 0.5));
        if (c._v) S.later(500, c._v.go);
      });
    }
    function pump() {
      if (waiting || !queue.length || S.dead || closed) return;
      var wait = shownAt + DWELL_MS - Date.now();
      if (wait > 0 && !S.instant) { waiting = true; S.later(wait, function () { waiting = false; pump(); }); return; }
      shownAt = Date.now();
      show(queue.shift());
      pump();
    }

    // the two plans side by side, the same size, each with its name and its figure
    function sideBySide() {
      if (two || !twoUp) return;
      closed = true; queue = [];
      two = S.add(SW.el('div', 'sq-two'));
      two.style.bottom = capBottom + 'px';
      two.style.top = Math.round(8 * u) + 'px';
      items.forEach(function (it) {
        var col = two.appendChild(SW.el('div', 'sq-col'));
        var pic = col.appendChild(SW.el('div', 'sq-pic'));
        pic.appendChild(S.img(it.asset));
        var lab = col.appendChild(SW.el('div', 'sq-lab'));
        lab.appendChild(SW.el('div', 'sq-name2', it.label_en || ''));
        if (it.value) { var v = S.valueNode(it.value, 'sq-v'); lab.appendChild(SW.el('div', 'sq-val')).appendChild(v.node); v.go(); }
      });
      if (cur >= 0) capOut(cur);                       // the single plan and its caption step aside
      if (S.instant) { two.classList.add('on'); shots.forEach(function (s) { s.classList.remove('on'); }); return; }
      S.later(160, function () { two.classList.add('on'); });
      S.later(900, function () { shots.forEach(function (s) { s.classList.remove('on'); }); });
    }

    return {
      beat: function (i) {
        if (closed) return;
        // her opening line names none of them: the first picture is up already (its name waits for its own line)
        if (i === 0 && cur < 0 && shots[0] && !items.some(function (it) { return it.line === 0; })) { intro = true; SW.cut(S, shots[0], null); }
        items.forEach(function (it, k) { if (it.line === i && k !== cur && queue.indexOf(k) < 0) { queue.push(k); fired = k; } });
        if (twoUp && lastLine >= 0 && i > lastLine) S.later(S.instant ? 0 : 900, sideBySide);
        pump();
      },
      rest: function () {
        if (S.dead || !items.length) return;
        if (twoUp) { sideBySide(); return; }
        queue = [];
        var last = fired >= 0 ? fired : (cur >= 0 ? cur : items.length - 1);
        if (cur !== last) show(last);
        shots[last].classList.add('on', 'sw-cutdone');
        var c = caps[last];
        c.classList.remove('out');
        c.classList.add('on');
        c._name.classList.add('on', 'sw-all');          // lit at once
        if (c._v) c._v.go();
        if (steps) steps.at(last);
      }
    };
  }

  ['drill', 'stack'].forEach(function (t) { SW.scenes[t] = build; });
})();
