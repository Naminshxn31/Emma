/* The event screen's stories as film (owner 24 Nov: "ข้อความเอาไปทำโมชั่นเหมือนบทหลัก ... ลองออกแบบแต่ละหน้า").
   Loaded by the room screen's story frame alone (story-stage.html), never by the robot's face. A scene replaces a
   template's layout on the WIDE stage (emma-story.js Story.wide); the engine keeps the clock, the beats, the report
   and the end state. A scene file adds `StoryWide.scenes.<template> = function (S, p) { return {beat, rest}; }`;
   a template with no scene keeps the engine's own wide layout. Every word, figure and picture comes from `p` / S.data:
   a scene writes none of its own.

   Motion rules (the project's): transform and opacity only, no blur of any kind (no blurred shadow, no
   backdrop-filter, no filter), nothing infinite, one thing moves the eye at a time. Nothing of a scene below S.TF
   (the Thai subtitle's zone). `S.instant` = the end state at once. */
(function () {
  'use strict';
  var SW = window.StoryWide = { scenes: {} };

  SW.el = function (tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  // a sentence the engine's way: "·" spaced, no doubled spaces
  SW.tidy = function (t) { return String(t == null ? '' : t).replace(/\s+/g, ' ').trim(); };
  // how long her line `i` is expected to take, in seconds (its own length; the recording is ~14.5 characters a second)
  SW.secs = function (S, i) {
    var b = (S.data.beats || [])[i];
    var n = b && b.w ? b.w.en || 0 : 0;
    return Math.max(1.6, n / 14.5);
  };
  // when, inside line `i`, its cue word falls is the engine's business: a beat is already that moment.

  // ---- a shot: one picture over the whole screen, its camera moving slowly ----
  // o.move: 'push' (in), 'pull' (out), 'left', 'right' (a track), '' (still); o.fit 'contain' = shown whole (a drawing);
  // o.lift: how far the picture sits above its place, % (what stands low in a render clears the subtitle; default 8)
  SW.shot = function (S, ref, o) {
    o = o || {};
    var n = SW.el('div', 'sw-shot sw-' + (o.move == null ? 'push' : o.move || 'still') + (o.fit === 'contain' ? ' sw-contain' : ''));
    var win = n.appendChild(SW.el('div', 'sw-win')), cam = win.appendChild(SW.el('div', 'sw-cam'));
    cam.style.setProperty('--sw-lift', '-' + (o.lift == null ? 8 : o.lift) + '%');
    if (o.secs) cam.style.setProperty('--sw-secs', Math.max(5, o.secs).toFixed(1) + 's');
    var im = S.img(ref);
    cam.appendChild(im);
    n.appendChild(SW.el('i', 'sw-edge'));
    n._im = im;
    return S.add(n);
  };
  // bring a shot up over the one before it: a sheet of light crosses left to right and uncovers it (the film's own cut);
  // the first shot of a story just opens out of the dark. Returns when it is whole (ms).
  SW.cutMs = 1100;
  var order = 0;
  SW.cut = function (S, to, from) {
    if (!to || to === from || to._up) return 0;
    to._up = true;
    to.style.zIndex = String(++order % 40 + 2);
    if (S.instant) { to.classList.add('on', 'sw-cutdone'); if (from) from.classList.remove('on'); return 0; }
    to.classList.toggle('sw-first', !from);
    S.reveal(to, function () {
      S.later(SW.cutMs + 60, function () { to.classList.add('sw-cutdone'); if (from) { from.classList.remove('on'); from._up = false; from.classList.remove('sw-cutdone'); } });
    });
    return SW.cutMs;
  };

  // ---- words that light up ----
  // The node holds the text as words; `.on` lights them one after another over `secs` (SW.light). Not shown before.
  SW.words = function (text, cls) {
    var n = SW.el('div', 'sw-words' + (cls ? ' ' + cls : ''));
    SW.tidy(text).split(' ').forEach(function (w, k) {
      if (k) n.appendChild(document.createTextNode(' '));
      var s = SW.el('span', 'sw-w', w);
      s.style.setProperty('--i', String(k));
      n.appendChild(s);
    });
    n._n = n.querySelectorAll('.sw-w').length;
    return n;
  };
  SW.light = function (S, node, secs) {
    var step = Math.max(60, Math.min(220, 1000 * (secs || 1.2) / Math.max(1, node._n || 1)));
    node.style.setProperty('--step', step.toFixed(0) + 'ms');
    if (S.instant) { node.classList.add('on', 'sw-all'); return; }
    void node.offsetWidth;
    node.classList.add('on');
  };

  // ---- the place's name: top left, large, lit as the story opens ----
  SW.head = function (S, title, sub) {
    var h = S.add(SW.el('div', 'sw-head'));
    var t = h.appendChild(SW.words(title, 'sw-title'));
    var s = sub ? h.appendChild(SW.el('div', 'sw-sub', SW.tidy(sub))) : null;
    h._show = function () { SW.light(S, t, 0.7); if (s) { if (S.instant) s.classList.add('on'); else S.later(450, function () { s.classList.add('on'); }); } };
    h._set = function (title2, sub2) {          // another name in the same place (a sequence): the old one goes up and out
      var t2 = SW.words(title2, 'sw-title');
      h.replaceChild(t2, t); t = t2;
      if (s) { h.removeChild(s); s = null; }
      if (sub2) s = h.appendChild(SW.el('div', 'sw-sub', SW.tidy(sub2)));
      h._show();
    };
    return h;
  };

  // ---- one phrase at a time, large, above the subtitle ----
  // say(text, {secs, figure}): the one before leaves upward, this one lights. `figure` = {label, value}: the value big.
  SW.phrases = function (S) {
    var slot = S.add(SW.el('div', 'sw-slot')), cur = null;
    slot.style.bottom = (S.LH - S.TF) + 'px';
    slot.say = function (text, o) {
      o = o || {};
      var p = SW.el('div', 'sw-phrase' + (o.figure ? ' sw-fig' : ''));
      if (o.figure) {
        if (o.figure.label) p.appendChild(SW.el('div', 'sw-fl', SW.tidy(o.figure.label)));
        p.appendChild(SW.words(o.figure.value, 'sw-fv'));
      } else p.appendChild(SW.words(text));
      slot.appendChild(p);
      var old = cur;
      cur = p;
      if (old) { old.classList.add('out'); S.later(600, function () { if (old.parentNode) old.parentNode.removeChild(old); }); }
      var w = p.querySelector('.sw-words');
      if (S.instant) { if (old && old.parentNode) old.parentNode.removeChild(old); p.classList.add('on'); SW.light(S, w, 0); return p; }
      S.later(old ? 180 : 0, function () { p.classList.add('on'); SW.light(S, w, o.secs || 1.1); });
      return p;
    };
    slot.clear = function () { if (cur) { cur.classList.add('out'); cur = null; } };
    return slot;
  };

  // ---- the film's panel: dark glass with a lit edge (drawn, nothing blurred) ----
  SW.glass = function (cls) { return SW.el('div', 'sw-glass' + (cls ? ' ' + cls : '')); };

  // ---- a thin line of steps: how many, and which one this is ----
  SW.steps = function (S, n) {
    var box = S.add(SW.el('div', 'sw-steps')), dots = [];
    for (var k = 0; k < n; k++) dots.push(box.appendChild(SW.el('i')));
    box.at = function (k) { dots.forEach(function (d, j) { d.className = j < k ? 'done' : j === k ? 'cur' : ''; }); box.classList.add('on'); };
    return box;
  };
})();
