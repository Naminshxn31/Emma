/* Geography on the event screen (templates `map` and `journey`): a place and how far it is, as film.
   map     the owner's real map as large as the stage allows, its routes drawn as she names each place, each place a glass
           plate at its pin; the places in a column beside it, the one she names now large.
   journey a road of light across the screen, one lane per destination, the time huge at the end of its lane;
           `view: "rings"` (the schools): the project in the middle, a ring of light per drive time, a plate on each.
   Every word comes from the params. Only transform and opacity move (the route's stroke-dashoffset is the engine's own
   one exception). The map's credit is part of the picture's card and is never animated apart from it. */
(function () {
  'use strict';
  var SW = window.StoryWide;
  var SVGNS = 'http://www.w3.org/2000/svg';
  function obj(r) { return r && typeof r === 'object'; }
  function num(v) { v = +v; return isFinite(v) ? v : null; }
  function svgEl(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }
  function px(n) { return Math.round(n) + 'px'; }
  function put(node, x, y, w, h) {
    node.style.left = px(x); node.style.top = px(y);
    if (w != null) { node.style.width = px(w); node.style.height = px(h); }
    return node;
  }
  // the walker drawn as a few strokes (the walking time's mark; no words)
  function walker() {
    var s = svgEl('svg', { viewBox: '0 0 24 24', 'class': 'ge-walk-ic', 'aria-hidden': 'true' });
    s.appendChild(svgEl('circle', { cx: 14, cy: 4.2, r: 2.3, fill: 'currentColor' }));
    s.appendChild(svgEl('path', { d: 'M13 8.5 L10.5 14 L13.5 17 L13 22 M13 9 L8 11 M14 10 L17.5 13 M10.5 14 L8 21.5', fill: 'none', stroke: 'currentColor', 'stroke-width': 2.1, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return s;
  }
  function boxHit(a, b) {
    var w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }
  // a plate: the name over its time (the time only when `timed`)
  function plate(r, timed, name) {
    var d = SW.el('div', 'ge-plate');
    d.appendChild(SW.el('b', 'ge-pn', SW.tidy(name || r.short_en || r.label_en || '')));
    if (timed && r.value_en) d.appendChild(SW.el('em', 'ge-pt', SW.tidy(r.value_en)));
    return d;
  }

  /* ------------------------------------------------------------------ map */
  // the project behind it all, dimmed: a map and a road have no picture of their own (`backdrop`)
  function backdrop(S, p) {
    if (!p.backdrop || !p.backdrop.asset) return;
    var b = SW.shot(S, p.backdrop.asset, { move: 'push', lift: 0, secs: 30 });
    b.classList.add('ge-back');
    b.classList.add('sw-first');
    if (S.instant) b.classList.add('on', 'sw-cutdone');
    else S.reveal(b, function () { b.classList.add('sw-cutdone'); });
  }

  SW.scenes.map = function (S, p) {
    backdrop(S, p);
    var rows = (p.rows || []).filter(obj), n = rows.length;
    var u = S.LH * 0.009, aspect = num(p.aspect) > 0 ? num(p.aspect) : 1;
    var m = S.M, Hm = S.TF - 2 * m, Wm = Math.min(Hm * aspect, S.LW * 0.62);
    var cw = Math.round(Wm), ch = Math.round(Wm / aspect);
    var left = Math.round(S.LW * 0.04), top = Math.round((S.TF - ch) / 2);
    var colLeft = left + cw + Math.round(S.LW * 0.045), colW = S.LW - colLeft - Math.round(S.LW * 0.04);

    // ---- the picture, in a card whose own shape is the map's (the pins are % of it) ----
    var wrap = S.add(SW.el('div', 'ge-wrap'));
    put(wrap, left, top, cw, ch);
    var card = wrap.appendChild(SW.el('div', 'ge-card'));
    card.appendChild(S.img(p.map_asset, 'ge-img'));
    var svg = card.appendChild(svgEl('svg', { viewBox: '0 0 ' + cw + ' ' + ch, 'class': 'ge-svg', 'aria-hidden': 'true' }));
    function P(pt) { return [pt.x / 100 * cw, pt.y / 100 * ch]; }
    function d3(line) {
      return (line || []).map(function (q, j) { return (j ? 'L' : 'M') + (q[0] / 100 * cw).toFixed(1) + ' ' + (q[1] / 100 * ch).toFixed(1); }).join('');
    }
    function plen(line) {
      var L = 0;
      for (var j = 1; j < (line || []).length; j++) L += Math.hypot((line[j][0] - line[j - 1][0]) / 100 * cw, (line[j][1] - line[j - 1][1]) / 100 * ch);
      return L;
    }
    // the credit stays on the picture, whole, from the first frame the picture is there
    var attr = card.appendChild(SW.el('div', 'ge-attr', SW.tidy(p.attribution_en || '© OpenStreetMap contributors')));
    var attrBox = [0, ch - (attr.offsetHeight || 3 * u), attr.offsetWidth || cw * 0.7, ch];

    // ---- the project ----
    var pr = p.project ? P(p.project) : [cw / 2, ch / 2];
    var proj = card.appendChild(SW.el('i', 'ge-proj'));
    put(proj, pr[0], pr[1]);
    var boxes = [[pr[0] - 2.4 * u, pr[1] - 2.4 * u, pr[0] + 2.4 * u, pr[1] + 2.4 * u]];
    var projPlate = null;
    if (p.origin_en) {
      projPlate = card.appendChild(plate({}, false, p.origin_en));
      projPlate.classList.add('ge-pp');
    }

    // ---- the places ----
    var timed = true;
    var places = rows.map(function (r, k) {
      var x = { r: r, k: k, line: num(r.line) != null ? num(r.line) : 0, walkLine: obj(r.walk) && num(r.walk.line) != null ? num(r.walk.line) : null };
      if (obj(r.pin)) {
        x.at = P(r.pin);
        boxes.push([x.at[0] - 1.8 * u, x.at[1] - 1.8 * u, x.at[0] + 1.8 * u, x.at[1] + 1.8 * u]);
        x.dot = card.appendChild(SW.el('i', 'ge-dot'));
        put(x.dot, x.at[0], x.at[1]);
        x.plate = card.appendChild(plate(r, timed));
      }
      if (obj(r.pin) && r.route && r.route.length > 1) {
        var dd = d3(r.route);
        x.glow = svg.appendChild(svgEl('path', { d: dd, pathLength: 1, 'class': 'ge-rglow' }));
        x.path = svg.appendChild(svgEl('path', { d: dd, pathLength: 1, 'class': 'ge-route' }));
        x.secs = Math.max(0.8, Math.min(1.5, plen(r.route) / (cw * 0.6)));
        x.glow.style.setProperty('--d', x.secs.toFixed(2) + 's');
        x.path.style.setProperty('--d', x.secs.toFixed(2) + 's');
      }
      return x;
    });
    var marked = places.filter(function (x) { return x.at; });

    // ---- the plates: beside their pins, clear of every pin, each other, the credit and the card's edge; a route
    //      under one costs a little (a map's labels lie over its roads) ----
    var routePts = [];
    marked.forEach(function (x) { (x.r.route || []).forEach(function (q) { routePts.push([q[0] / 100 * cw, q[1] / 100 * ch]); }); });
    var gap = 1.4 * u, edge = 0.8 * u;
    function cost(b, fixed) {
      var c = 0, k;
      for (k = 0; k < boxes.length; k++) c += boxHit(b, boxes[k]) * 6;
      for (k = 0; k < fixed.length; k++) c += boxHit(b, fixed[k]) * 6;
      c += boxHit(b, attrBox) * 6;
      for (k = 0; k < routePts.length; k++) { var q = routePts[k]; if (q[0] > b[0] && q[0] < b[2] && q[1] > b[1] && q[1] < b[3]) c += 14; }
      return c;
    }
    var DIRS = [[1, 0], [-1, 0], [0, -1], [0, 1], [.71, -.71], [-.71, -.71], [.71, .71], [-.71, .71]];
    function fit(node, ax, ay, fixed, away) {
      var w = node.offsetWidth || 12 * u, h = node.offsetHeight || 6 * u, best = null, bc = Infinity;
      var dirs = DIRS.slice().sort(function (a, b) { return (b[0] * away[0] + b[1] * away[1]) - (a[0] * away[0] + a[1] * away[1]); });
      [0, 1.2 * u, 2.6 * u, 4.2 * u].forEach(function (more) {
        dirs.forEach(function (d) {
          if (bc === 0) return;
          var cx = ax + d[0] * (w / 2 + gap + more), cy = ay + d[1] * (h / 2 + gap + more);
          cx = Math.max(w / 2 + edge, Math.min(cw - w / 2 - edge, cx));
          cy = Math.max(h / 2 + edge, Math.min(ch - h / 2 - edge, cy));
          var b = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], c = cost(b, fixed) + more * 0.2;
          if (c < bc) { bc = c; best = b; }
        });
      });
      put(node, best[0], best[1]);
      return best;
    }
    var fixed = [];
    if (projPlate) fixed.push(fit(projPlate, pr[0], pr[1], fixed, [0, -1]));
    marked.forEach(function (x) {
      var ox = x.at[0] - pr[0], oy = x.at[1] - pr[1], l = Math.hypot(ox, oy) || 1;
      fixed.push(fit(x.plate, x.at[0], x.at[1], fixed, [ox / l, oy / l]));
    });

    // ---- the column: the project's name, then the places as she names them ----
    var col = S.add(SW.el('div', 'ge-col'));
    put(col, colLeft, m, colW, S.TF - 2 * m);
    var head = col.appendChild(SW.el('div', 'ge-ch'));
    var oname = head.appendChild(SW.words(p.origin_en || '', 'ge-origin'));
    var osub = p.origin_sub_en ? head.appendChild(SW.el('div', 'ge-osub', SW.tidy(p.origin_sub_en))) : null;
    var list = col.appendChild(SW.el('div', 'ge-list ge-n' + Math.min(5, Math.max(2, n))));
    var fitK = 1;
    places.forEach(function (x) {
      var r = x.r, row = list.appendChild(SW.el('div', 'ge-row'));
      row.appendChild(SW.el('b', 'ge-rn', SW.tidy((n >= 4 && r.short_en) || r.label_en || '')));
      var l2 = row.appendChild(SW.el('div', 'ge-r2'));
      if (r.value_en) l2.appendChild(SW.el('em', 'ge-rt', SW.tidy(r.value_en)));
      if (r.sub_en) l2.appendChild(SW.el('span', 'ge-rs', SW.tidy(r.sub_en)));
      if (obj(r.walk) && r.walk.value_en) {
        x.walk = l2.appendChild(SW.el('span', 'ge-rw'));
        x.walk.appendChild(walker());
        x.walk.appendChild(document.createTextNode(SW.tidy(r.walk.value_en)));
      }
      x.row = row;
    });

    (function fitCol() {
      var avail = S.TF - 2 * m, h = head.offsetHeight + list.offsetHeight + 3 * u;
      if (h > avail) {
        fitK = Math.max(0.8, avail / h);
        col.style.width = px(colW / fitK);
        col.style.transformOrigin = '0 50%';
        col.style.transform = 'scale(' + fitK.toFixed(4) + ')';
      }
    })();
    var rested = false, opened = false;
    function act(x) {
      places.forEach(function (o) {
        [o.row, o.plate, o.dot, o.path].forEach(function (e) { if (e) { e.classList.toggle('act', !rested && o === x); e.classList.toggle('dim', !rested && !!x && o !== x); } });
      });
    }
    function open() {
      if (opened) return;
      opened = true;
      S.reveal(wrap, function () {
        S.later(300, function () { proj.classList.add('on'); if (projPlate) projPlate.classList.add('on'); });
      });
      SW.light(S, oname, 0.8);
      if (osub) { if (S.instant) osub.classList.add('on'); else S.later(400, function () { osub.classList.add('on'); }); }
    }
    function draw(x) { if (x.glow) { x.glow.classList.add('on'); x.path.classList.add('on'); } }
    function land(x) {
      if (x.dot) x.dot.classList.add('on');
      if (x.plate) x.plate.classList.add('on');
      if (x.dot && !S.instant && !rested) { x.dot.classList.add('ping'); S.later(1500, function () { x.dot.classList.remove('ping'); }); }
    }
    function show(x, delay) {
      S.later(delay, function () {
        if (x.shown) return;
        x.shown = true;
        x.row.classList.add('on');
        act(x);
        draw(x);
        S.later(x.path ? Math.round(x.secs * 1000) - 150 : 0, function () { land(x); });
      });
    }
    function walkOn(x) { if (x.walk) x.walk.classList.add('on'); }
    return {
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        places.forEach(function (x) { if (x.line === i) show(x, (i === 0 ? 700 : 0) + 380 * Math.min(j++, 1)); });
        places.forEach(function (x) {
          if (x.walkLine === i) {
            S.later(x.line === i ? 900 : 100, function () { walkOn(x); if (!rested) act(x); });
          }
        });
      },
      rest: function () {
        rested = true;
        open();
        wrap.classList.add('on');
        proj.classList.add('on');
        if (projPlate) projPlate.classList.add('on');
        places.forEach(function (x) {
          x.shown = true; x.row.classList.add('on'); draw(x); land(x); walkOn(x);
        });
        act(null);
        oname.classList.add('on', 'sw-all'); if (osub) osub.classList.add('on');
      }
    };
  };

  /* -------------------------------------------------------------- journey */
  SW.scenes.journey = function (S, p) {
    backdrop(S, p);
    return p.view === 'rings' ? rings(S, p) : road(S, p);
  };

  function road(S, p) {
    var rows = (p.rows || []).filter(obj), n = rows.length;
    var u = S.LH * 0.009, m = S.M;
    var max = num(p.bar_max) || 0;
    rows.forEach(function (r) { (r.bar || []).forEach(function (v) { if (num(v) > max) max = num(v); }); });
    if (!(max > 0)) max = 1;
    var top = Math.round(S.LH * 0.05), bottom = S.TF - Math.round(S.LH * 0.03);
    var rowH = Math.min(Math.round(u * 26), Math.floor((bottom - top) / Math.max(1, n)));
    var y0 = Math.round((top + bottom) / 2 - rowH * n / 2);
    var spineX = Math.round(S.LW * 0.30), x0 = spineX + Math.round(u * 3), x1 = Math.round(S.LW * 0.735), trackW = x1 - x0;
    var timeRight = S.LW - Math.round(S.LW * 0.04);
    var big = n <= 2 ? ' ge-big' : '';
    var root = S.add(SW.el('div', 'ge-road' + big));
    var laneY = function (k) { return y0 + rowH * k + Math.round(rowH * 0.58); };

    // the project, at the road's start
    var o = root.appendChild(SW.el('div', 'ge-jo'));
    put(o, Math.round(S.LW * 0.04), 0);
    o.style.width = px(spineX - Math.round(S.LW * 0.04) - u * 3);
    var oname = o.appendChild(SW.words(p.origin_en || '', 'ge-origin'));
    var osub = p.origin_sub_en ? o.appendChild(SW.el('div', 'ge-osub', SW.tidy(p.origin_sub_en))) : null;
    var midY = (laneY(0) + laneY(Math.max(0, n - 1))) / 2;
    o.style.top = px(midY - (o.offsetHeight || 12 * u) / 2);
    var node0 = root.appendChild(SW.el('i', 'ge-node0'));
    put(node0, spineX, midY);
    var spine = root.appendChild(SW.el('i', 'ge-spine'));
    put(spine, spineX, laneY(0), null, null);
    spine.style.height = px(Math.max(1, laneY(n - 1) - laneY(0)));

    var items = rows.map(function (r, k) {
      var bar = (r.bar || []).map(num).filter(function (v) { return v != null; });
      var hi = bar.length ? Math.min(1, bar[bar.length - 1] / max) : 0.5, lo = bar.length ? Math.min(1, bar[0] / max) : hi;
      hi = Math.max(0.04, hi); lo = Math.max(0.04, lo);
      var y = laneY(k), row = root.appendChild(SW.el('div', 'ge-jr'));
      var name = row.appendChild(SW.el('div', 'ge-jn'));
      put(name, x0, y - u * 8.6);
      name.style.width = px(trackW);
      name.appendChild(SW.el('b', null, SW.tidy(r.label_en || '')));
      if (r.sub_en) name.appendChild(SW.el('span', null, SW.tidy(r.sub_en)));
      var lane = row.appendChild(SW.el('i', 'ge-lane')); put(lane, x0, y, trackW);
      var span = row.appendChild(SW.el('i', 'ge-span')); put(span, x0, y, trackW);
      var fill = row.appendChild(SW.el('i', 'ge-fill')); put(fill, x0, y, trackW);
      var dot = row.appendChild(SW.el('i', 'ge-sdot')); put(dot, x0, y);
      var st = row.appendChild(SW.el('i', 'ge-stn')); put(st, spineX, y);
      var time = row.appendChild(SW.el('div', 'ge-time', SW.tidy(r.value_en || '')));
      time.style.right = px(S.LW - timeRight); time.style.top = px(y - u * 4.5);
      var secs = 0.6 + 1.0 * hi;
      row.style.setProperty('--d', secs.toFixed(2) + 's');
      return { row: row, fill: fill, span: span, dot: dot, time: time, hi: hi, lo: lo, secs: secs, line: num(r.line) != null ? num(r.line) : k, k: k, trackW: trackW };
    });
    function drive(x, still) {
      x.fill.style.transform = 'scaleX(' + x.lo.toFixed(4) + ')';
      x.span.style.transform = 'scaleX(' + x.hi.toFixed(4) + ')';
      x.dot.style.transform = 'translate3d(' + (x.hi * x.trackW).toFixed(1) + 'px, 0, 0)';
    }
    var rested = false, opened = false;
    function act(x) { items.forEach(function (o2) { o2.row.classList.toggle('dim', !rested && !!x && o2 !== x); }); }
    function open() {
      if (opened) return;
      opened = true;
      SW.light(S, oname, 0.8);
      if (osub) { if (S.instant) osub.classList.add('on'); else S.later(400, function () { osub.classList.add('on'); }); }
      S.later(200, function () { node0.classList.add('on'); });
    }
    function show(x, delay) {
      S.later(delay, function () {
        if (x.shown) return;
        x.shown = true;
        spine.style.transform = 'scaleY(' + (items.length > 1 ? x.k / (items.length - 1) : 1).toFixed(4) + ')';
        x.row.classList.add('on');
        act(x);
        S.later(S.instant ? 0 : 160, function () { drive(x); });
        S.later(Math.round(x.secs * 1000) - 120, function () { x.time.classList.add('on'); });
      });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        items.forEach(function (x) { if (x.line === i) show(x, (i === 0 ? 450 : 0) + 300 * Math.min(j++, 1)); });
      },
      rest: function () {
        rested = true;
        open();
        node0.classList.add('on');
        oname.classList.add('on', 'sw-all'); if (osub) osub.classList.add('on');
        spine.style.transform = 'scaleY(1)';
        items.forEach(function (x) { x.shown = true; x.row.classList.add('on'); drive(x); x.time.classList.add('on'); });
        act(null);
      }
    };
  }

  /* ---------------------------------------------------------------- rings */
  function rings(S, p) {
    var rows = (p.rows || []).filter(obj), n = rows.length;
    var u = S.LH * 0.009, m = S.M;
    var mins = rows.map(function (r) { var v = num((r.bar || [])[0]); return v != null && v > 0 ? v : null; });
    var max = num(p.bar_max) || 0;
    mins.forEach(function (v) { if (v != null && v > max) max = v; });
    if (!(max > 0)) max = 1;
    var cx = Math.round(S.LW / 2), cy = Math.round(S.TF * 0.53);
    var RX = Math.min(S.LW * 0.40, S.LW / 2 - S.LW * 0.04 - 12 * u), RY = Math.min(S.TF * 0.36, 300 * (S.LH / 1080));
    var root = S.add(SW.el('div', 'ge-rings'));
    put(root, 0, 0, S.LW, S.TF);
    root.appendChild(SW.el('i', 'ge-floor'));
    var floor = root.lastChild;
    put(floor, cx - RX * 1.15, cy - RY * 1.2, RX * 2.3, RY * 2.4);

    var pdot = root.appendChild(SW.el('i', 'ge-proj')); put(pdot, cx, cy);
    var pname = root.appendChild(SW.el('div', 'ge-pname'));
    pname.appendChild(SW.words(p.origin_en || '', 'ge-origin'));
    if (p.origin_sub_en) pname.appendChild(SW.el('div', 'ge-osub on', SW.tidy(p.origin_sub_en)));
    pname.style.left = px(cx); pname.style.top = px(cy + 2.2 * u);
    var foot = p.footnote_en ? root.appendChild(SW.el('div', 'ge-foot', SW.tidy(p.footnote_en))) : null;
    if (foot) foot.style.top = px(m);

    var ANG = [330, 215, 170, 35, 250, 105, 345, 195];
    var items = rows.map(function (r, k) {
      var f = mins[k] != null ? mins[k] / max : (k + 1) / (n + 1);
      var rx = Math.max(u * 6, RX * f), ry = Math.max(u * 3, RY * f);
      var ring = root.appendChild(SW.el('i', 'ge-ring'));
      put(ring, cx - rx, cy - ry, rx * 2, ry * 2);
      var a = ANG[k % ANG.length] * Math.PI / 180, x = cx + rx * Math.cos(a), y = cy + ry * Math.sin(a);
      var dot = root.appendChild(SW.el('i', 'ge-dot')); put(dot, x, y);
      var pl = root.appendChild(plate(r, true));
      return { r: r, ring: ring, dot: dot, plate: pl, x: x, y: y, line: num(r.line) != null ? num(r.line) : k };
    });
    // each plate above its point, held inside the stage's margins
    items.forEach(function (x) {
      var w = x.plate.offsetWidth || 20 * u, h = x.plate.offsetHeight || 9 * u;
      var lx = Math.max(S.LW * 0.032, Math.min(S.LW - S.LW * 0.032 - w, x.x - w / 2));
      var ly = Math.max(m, Math.min(S.TF - m - h, x.y > cy ? x.y + 1.6 * u : x.y - 1.6 * u - h));
      put(x.plate, lx, ly);
    });
    var rested = false, opened = false;
    function act(x) {
      items.forEach(function (o) {
        [o.plate, o.dot, o.ring].forEach(function (e) { e.classList.toggle('act', !rested && o === x); e.classList.toggle('dim', !rested && !!x && o !== x); });
      });
    }
    function open() {
      if (opened) return;
      opened = true;
      pdot.classList.add('on'); pname.classList.add('on');
      SW.light(S, pname.querySelector('.sw-words'), 0.7);
      if (foot) { if (S.instant) foot.classList.add('on'); else S.later(500, function () { foot.classList.add('on'); }); }
      floor.classList.add('on');
    }
    function show(x) {
      if (x.shown) return;
      x.shown = true;
      x.ring.classList.add('on');
      act(x);
      S.later(S.instant ? 0 : 750, function () { x.dot.classList.add('on'); x.plate.classList.add('on'); });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        items.forEach(function (x) { if (x.line === i) { var k = j++; S.later(k * 250, function () { show(x); }); } });
      },
      rest: function () {
        rested = true;
        open();
        pdot.classList.add('on'); pname.classList.add('on'); floor.classList.add('on');
        pname.querySelector('.sw-words').classList.add('on', 'sw-all');
        if (foot) foot.classList.add('on');
        items.forEach(function (x) { x.shown = true; x.ring.classList.add('on'); x.dot.classList.add('on'); x.plate.classList.add('on'); });
        act(null);
      }
    };
  }
})();
