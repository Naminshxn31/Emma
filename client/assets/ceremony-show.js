/* The ceremony's motion slides on the event screen (owner 17 Nov 2026: "ทำตัวพรีเซ้นต์
   โมชั่น ... ลิ้งระหว่างสไลด์กับเสียงหุ่น ... มีซับภาษาไทยด้านล่าง ... ขนาด 16:9").

   Loaded only by the room screen (/mirror, client/voice-preview.html startMirror). The
   server sends one slide with a line's subtitle (app/ceremony_slides.py: a template,
   its words, beats as fractions of the line); the page tells this engine how far her
   voice has got (the subtitle's own clock -- the voice THIS screen plays) and the
   engine reveals each part at its beat. There is no clock in here: animations run once
   a part is revealed, but WHEN it is revealed is always progress().

     CeremonyShow.show(slide, {late})  the slide's opening state: the parts no beat
                                       names. late = a screen that joined mid-line: the
                                       opening is simply there (nothing animates in) and
                                       the next progress() brings it to where she is --
                                       never the end state ahead of her words.
     CeremonyShow.progress(f)          her voice is at f (0..1) of the line
     CeremonyShow.settle()             the line is over: every part, and it stays
     CeremonyShow.clear()              down (a stop, the call over, a picture came up)
     CeremonyShow.register(name, fn)   an act: a part of the film in its own file
                                       (assets/ceremony-show/acts/<name>.js), run for a
                                       slide {template: "act", act: name} -- see "acts"

   Motion rules of this project: only transform and opacity animate, no blurred shadows,
   no backdrop-filter. Every size is calc(N * var(--cu)): one unit = 1% of a 16:9
   stage's width, limited by the height on a wider screen (ceremony-show.css). */
(function () {
  'use strict';

  var SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" '
    + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">';
  // Drawn here, line icons only -- no picture of anything real (app/ceremony_slides.py
  // ICONS lists the same names with the words that must back each one).
  var ICONS = {
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M18 6l-2.5 2.5M8.5 15.5L6 18"/>',
    orbit: '<circle cx="12" cy="12" r="4"/><ellipse cx="12" cy="12" rx="10.5" ry="4" transform="rotate(-20 12 12)"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.4v.4"/>',
    home: '<path d="M4 11l8-7 8 7M6 10v10h12V10M10 20v-6h4v6"/>',
    plan: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 12h8M11 4v9M11 16v4M11 16h10M16 4v5"/>',
    doc: '<path d="M7 3h8l4 4v14H7zM15 3v4h4M10 12h6M10 16h6"/>',
    relax: '<circle cx="12" cy="9" r="3.5"/><path d="M3 17c2-1.5 4-1.5 6 0s4 1.5 6 0 4-1.5 6 0M12 2.5V4M5.5 9H4M20 9h-1.5M7.2 4.6l1 1M16.8 4.6l-1 1"/>',
    chip: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    pin: '<path d="M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    sea: '<path d="M2 8c2.5-2 5-2 7.5 0s5 2 7.5 0 3.5-1.6 5-.6M2 13c2.5-2 5-2 7.5 0s5 2 7.5 0 3.5-1.6 5-.6M2 18c2.5-2 5-2 7.5 0s5 2 7.5 0 3.5-1.6 5-.6"/>',
    pool: '<path d="M8 4v10M16 4v10M8 7h8M8 11h8M2 18c2.5-2 5-2 7.5 0s5 2 7.5 0 3.5-1.6 5-.6"/>',
    tree: '<path d="M12 21v-6M12 15c-3.6 0-6-2.5-6-5.7S8.5 3 12 3s6 3.100 6 6.300S15.600 15 12 15zM5 21h14"/>',
    building: '<path d="M5 21V5l8-2v18M13 9h6v12M3 21h18M8 8h2M8 12h2M8 16h2M16 13h.6M16 17h.6"/>',
    layers: '<path d="M12 3l9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.500 10.900c.6.5 1 1.200 1 2.100h5c0-.9.4-1.600 1-2.100A6 6 0 0012 3z"/>',
    curtain: '<path d="M3 4h18M5 4v16M19 4v16M5 4c0 6 3 9 3 16M19 4c0 6-3 9-3 16M5 20h3M16 20h3"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="M10 8.500l6 3.500-6 3.500z"/>',
    trophy: '<path d="M8 4h8v5a4 4 0 01-8 0zM8 6H5a3 3 0 003 3M16 6h3a3 3 0 01-3 3M12 13v4M8.500 20h7M10 17h4"/>',
    drop: '<path d="M12 3s6 6.500 6 11a6 6 0 01-12 0c0-4.500 6-11 6-11z"/>',
    gauge: '<path d="M4 17a8 8 0 1116 0M12 17l4-6M4 17h16"/>',
    screen: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M9 20h6M12 16v4"/>',
    shop: '<path d="M5 8h14l-1 12H6zM9 8V6a3 3 0 016 0v2"/>',
    hospital: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 8v8M8 12h8"/>',
    school: '<path d="M3 9l9-5 9 5-9 5zM7 11.500V16c1.500 1.500 3.200 2 5 2s3.500-.5 5-2v-4.500M21 9v6"/>',
    star: '<path d="M12 3l2.700 5.600 6.100.8-4.500 4.200 1.100 6-5.400-3-5.400 3 1.100-6L3.200 9.400l6.100-.8z"/>',
    people: '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20c0-3.300 2.700-6 6-6s6 2.700 6 6M15.500 14.200c3 .2 5.500 2.600 5.500 5.800"/>',
    chat: '<path d="M4 5h16v11H10l-5 4v-4H4z"/>',
    heart: '<path d="M12 20s-7-4.600-7-10a4 4 0 017-2.600A4 4 0 0119 10c0 5.400-7 10-7 10z"/>',
    ruler: '<path d="M3 15L15 3l6 6L9 21zM7.500 13.500L9 15M10.500 10.500L12 12M13.500 7.500L15 9"/>',
    book: '<path d="M4 5c3-1 5-1 8 1 3-2 5-2 8-1v14c-3-1-5-1-8 1-3-2-5-2-8-1zM12 6v14"/>'
  };

  var doc = document, stage = null, cur = null;

  function h(tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) fill(e, String(text));
    return e;
  }
  // A hyphenated word stays on one line ("155-metre" broke after the hyphen, seen in
  // Chromium 17 Nov): each such word in its own no-wrap span.
  function fill(e, text) {
    if (text.indexOf('-') < 0) { e.textContent = text; return; }
    text.split(' ').forEach(function (tok, i) {
      if (i) e.appendChild(doc.createTextNode(' '));
      if (tok.indexOf('-') < 0) { e.appendChild(doc.createTextNode(tok)); return; }
      var s = doc.createElement('span');
      s.className = 'cs-nb';
      s.textContent = tok;
      e.appendChild(s);
    });
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) if (arguments[i]) parent.appendChild(arguments[i]);
    return parent;
  }
  function icon(name, cls) {
    var e = h('span', 'cs-ico' + (cls ? ' ' + cls : ''));
    e.innerHTML = SVG + (ICONS[name] || ICONS.spark) + '</svg>';
    return e;
  }
  // A part a beat may reveal (data-k = the key app/ceremony_slides.py keys_of gives it).
  function part(run, key, e, order) {
    e.classList.add('cs-el');
    e.setAttribute('data-k', key);
    if (order) e.style.setProperty('--d', String(order * 110));
    (run.parts[key] = run.parts[key] || []).push(e);
    return e;
  }
  // A headline whose words come in one after another (the delay is per word; the
  // reveal is still one class on the headline).
  function words(cls, text) {
    var e = h('div', cls + ' cs-words');
    String(text).split(/\s+/).forEach(function (w, i) {
      if (!w) return;
      var s = h('span', 'cs-w', w);
      s.style.setProperty('--w', String(i * 70));
      e.appendChild(s);
      e.appendChild(doc.createTextNode(' '));
    });
    return e;
  }
  function kicker(run, s) {
    return s.kicker ? part(run, 'kicker', h('div', 'cs-kicker', s.kicker)) : null;
  }
  function chips(run, list) {
    if (!list || !list.length) return null;
    var row = h('div', 'cs-chips');
    list.forEach(function (text, i) { row.appendChild(part(run, 'chip:' + i, h('span', 'cs-chip', text))); });
    return row;
  }
  // A figure: its own value and nothing else, ever (see land()).
  function figure(lead, value, unit, big) {
    var f = h('div', 'cs-figure' + (big ? ' cs-figure-big' : ''));
    if (lead) f.appendChild(h('span', 'cs-lead', lead));
    var n = h('span', 'cs-num', value);
    n.setAttribute('data-final', value);
    f.appendChild(n);
    if (unit) f.appendChild(h('span', 'cs-unit', unit));
    f.__num = n;
    return f;
  }
  // ---- pictures (owner 17 Nov: "โชว์รูปต่างๆให้คนเห็นภาพไม่ใช่แค่มีแค่โมชั่นหนังสือ") ----
  // A picture is {src, w, h} from the server: the stories catalogue's own, at
  // /story-asset/<sha256> on this screen's listener -- no other address is drawn. It is
  // asked for the moment the slide arrives, so it is decoded before its beat; `owner`
  // (the part that carries it) stays clear until it is, then fades in: never an empty box.
  var ASSET = /^\/story-asset\/[0-9a-f]{64}$/;
  function picture(run, a, owner, fit) {
    // (a see-through line drawing -- a plan -- gets a lit panel behind it: on the dark
    // stage its thin lines were an almost empty black box, review 17 Nov)
    var box = h('span', 'cs-pic' + (fit === 'contain' ? ' cs-fit' : '') + (a && a.alpha === true ? ' cs-drawing' : ''));
    owner.classList.add('cs-needs');
    if (!a || typeof a.src !== 'string' || !ASSET.test(a.src)) return box;      // stays clear
    var img = doc.createElement('img');
    img.className = 'cs-img';
    img.setAttribute('alt', '');
    img.setAttribute('decoding', 'async');
    var done = false;
    var ready = function () {
      if (done) return;
      done = true;
      run.loaded++;
      owner.classList.add('cs-loaded');
    };
    img.onload = function () {
      // decoded, not only fetched: the first frame of its fade must not be the decode
      if (typeof img.decode === 'function') img.decode().then(ready, ready); else ready();
    };
    run.pictures++;
    box.appendChild(img);
    img.src = a.src;
    return box;
  }
  // One picture of a sequence (a film's shot, a spotlight's picture): the whole frame,
  // with its caption. They lie on top of one another and cross-fade.
  function shot(run, key, row) {
    var e = part(run, key, h('div', 'cs-shot cs-glass'));
    e.appendChild(picture(run, row.asset, e, row.fit));
    if (row.caption) e.appendChild(h('span', 'cs-cap', row.caption));
    // a figure of its own: up with this picture, gone when the next one comes (a line with
    // several numbers). It is drawn with the words, above the scrim -- inside the picture
    // it was under the scrim and read as a ghost (seen 17 Nov) -- as a part of the same key.
    var own = null;
    if (row.figure) {
      var fig = figure(row.figure.lead, row.figure.value, row.figure.unit, false);
      own = part(run, key, h('div', 'cs-over-figure cs-shot-figure'));
      add(own, row.figure.label ? h('div', 'cs-over-label', row.figure.label) : null, fig);
      own.__num = fig.__num;
      (run.shotFigures = run.shotFigures || []).push(own);
    }
    (run.shots = run.shots || []).push({ key: key, el: e, fig: own });
    return e;
  }
  function figureOf(run, f) {
    if (!f) return null;
    var box = part(run, 'figure', h('div', 'cs-over-figure'));
    var fig = figure(f.lead, f.value, f.unit, false);
    box.__num = fig.__num;
    add(box, fig, f.label ? h('div', 'cs-over-label', f.label) : null);
    return box;
  }
  function lane() {
    var l = h('span', 'cs-lane');
    l.appendChild(h('span', 'cs-lane-water'));
    return l;
  }

  var TEMPLATES = {
    hero: function (run, s) {
      var box = h('div', 'cs-hero' + (s.mood === 'closing' ? ' cs-closing' : ''));
      if (s.mood === 'closing') box.appendChild(h('div', 'cs-rays'));
      add(box,
        s.icon ? part(run, 'icon', icon(s.icon, 'cs-hero-ico')) : null,
        kicker(run, s),
        part(run, 'title', words('cs-title', s.title)),
        s.sub ? part(run, 'sub', h('div', 'cs-sub', s.sub)) : null,
        chips(run, s.chips));
      return box;
    },
    number: function (run, s) {
      var box = h('div', 'cs-number');
      var fig = part(run, 'value', figure(s.lead, s.value, s.unit, true));
      var g = null;
      if (s.graphic === 'span') {
        g = h('div', 'cs-span');
        var line = h('span', 'cs-span-line');
        line.appendChild(h('span', 'cs-span-fill'));
        line.appendChild(h('span', 'cs-span-dot'));
        add(g, icon(s.from_icon || 'pin', 'cs-span-end'), line, icon(s.to_icon || 'sea', 'cs-span-end'));
        part(run, 'graphic', g);
      } else if (s.graphic === 'lane') {
        g = part(run, 'graphic', h('div', 'cs-lane-row'));
        g.appendChild(lane());
      }
      add(box, kicker(run, s), s.title ? part(run, 'title', words('cs-title cs-title-m', s.title)) : null, fig, g,
        s.label ? part(run, 'label', h('div', 'cs-label', s.label)) : null,
        chips(run, s.chips));
      return box;
    },
    list: function (run, s) {
      var box = h('div', 'cs-list');
      var cards = h('div', 'cs-cards cs-cards-' + s.items.length);
      s.items.forEach(function (it, i) {
        var c = part(run, 'item:' + i, h('div', 'cs-card'));
        add(c, icon(it.icon || 'spark', 'cs-card-ico'), h('div', 'cs-card-text', it.text));
        cards.appendChild(c);
      });
      var hl = null;
      if (s.highlight) {
        hl = part(run, 'highlight', h('div', 'cs-highlight'));
        add(hl, icon(s.highlight.icon || 'star', 'cs-highlight-ico'), h('span', 'cs-highlight-text', s.highlight.text), lane());
      }
      add(box, kicker(run, s), part(run, 'title', words('cs-title cs-title-m', s.title)), cards, hl,
        s.foot ? part(run, 'foot', h('div', 'cs-foot', s.foot)) : null);
      return box;
    },
    choice: function (run, s) {
      var box = h('div', 'cs-choice');
      var cards = h('div', 'cs-cards cs-cards-' + s.options.length + ' cs-options');
      run.options = [];
      s.options.forEach(function (it, i) {
        var c = part(run, 'option:' + i, h('div', 'cs-card cs-option'));
        add(c, icon(it.icon || 'spark', 'cs-option-ico'), h('div', 'cs-card-text', it.text));
        cards.appendChild(c);
        run.options.push(c);
      });
      run.optionRow = cards;
      run.pick = typeof s.pick === 'number' ? s.pick : null;
      add(box, kicker(run, s), part(run, 'title', words('cs-title cs-title-m', s.title)), cards);
      return box;
    },
    status: function (run, s) {
      var box = h('div', 'cs-status cs-s-' + s.state + ' cs-d-' + s.device);
      var dev = part(run, 'device', h('div', 'cs-device'));
      if (s.device === 'lights_off' || s.device === 'lights_on') {
        add(dev, h('span', 'cs-glow'), h('span', 'cs-rays cs-bulb-rays'), icon('bulb', 'cs-device-ico'));
      } else if (s.device === 'curtain') {
        var win = h('span', 'cs-window');
        add(win, h('span', 'cs-window-sky'), h('span', 'cs-drape cs-drape-l'), h('span', 'cs-drape cs-drape-r'));
        dev.appendChild(win);
      } else {
        var paper = h('span', 'cs-paper');
        for (var i = 0; i < 5; i++) {
          var ln = h('span', 'cs-paper-line');
          ln.style.setProperty('--n', String(i));
          paper.appendChild(ln);
        }
        dev.appendChild(paper);
      }
      var side = h('div', 'cs-status-words');
      var title = part(run, 'title', words('cs-title cs-title-m', s.title));
      // "done" is said by the tick too: it comes with the words that say so, never before
      var mark = s.state === 'done' ? part(run, 'title', h('span', 'cs-tick')) : h('span', 'cs-dots');
      if (s.state === 'done') mark.innerHTML = SVG + '<path d="M5 12.5l4.500 4.500L19 7"/></svg>';
      else for (var j = 0; j < 3; j++) mark.appendChild(h('i'));
      add(side, kicker(run, s), title, s.detail ? part(run, 'detail', h('div', 'cs-detail', s.detail)) : null, mark);
      add(box, dev, side);
      return box;
    },
    joke: function (run, s) {
      var box = h('div', 'cs-joke');
      var title = null;
      if (s.title) {
        title = part(run, 'title', h('div', 'cs-joke-title', s.title));
        if (s.tail) {
          var tail = h('span', 'cs-tail');
          for (var i = 0; i < 3; i++) tail.appendChild(h('i'));
          title.appendChild(tail);
        }
      }
      // the pool's water comes with the pool: part of the same `icon` beat
      var water = null;
      if (s.icon === 'pool') {
        water = part(run, 'icon', h('div', 'cs-lane-row'));
        water.appendChild(lane());
      }
      add(box, kicker(run, s),
        s.icon ? part(run, 'icon', icon(s.icon, 'cs-joke-ico')) : null,
        title,
        s.punch ? part(run, 'punch', h('div', 'cs-punch', s.punch)) : null,
        water);
      return box;
    },
    stats: function (run, s) {
      var box = h('div', 'cs-stats');
      var row = h('div', 'cs-tiles cs-tiles-' + s.stats.length);
      s.stats.forEach(function (st, i) {
        var t = part(run, 'stat:' + i, h('div', 'cs-tile'));
        var fig = figure(st.lead || ' ', st.value, null, false);   // a blank lead: the figures stay on one line
        t.__num = fig.__num;
        add(t, h('div', 'cs-tile-label', st.label), fig, h('div', 'cs-tile-unit', st.unit));
        row.appendChild(t);
      });
      add(box, kicker(run, s), s.title ? part(run, 'title', words('cs-title cs-title-m', s.title)) : null, row);
      return box;
    }
  };

  TEMPLATES.film = function (run, s) {
    var box = h('div', 'cs-film');
    var reel = h('div', 'cs-reel');
    s.shots.forEach(function (row, i) { reel.appendChild(shot(run, 'shot:' + i, row)); });
    var words = h('div', 'cs-film-words');
    var each = h('div', 'cs-shot-figures');
    (run.shotFigures || []).forEach(function (f) { each.appendChild(f); });
    add(words, kicker(run, s), s.title ? part(run, 'title', words_(s.title)) : null,
      (run.shotFigures || []).length ? each : null, figureOf(run, s.figure), chips(run, s.chips));
    // (a scrim only where words are: a film with none shows the pictures whole)
    var figures = s.shots.some(function (row) { return !!row.figure; });
    add(box, reel, (s.title || s.figure || s.chips || figures) ? h('div', 'cs-scrim') : null, words);
    // the last frame's large centred title: the words at the side give way to it
    if (s.end) {
      var end = part(run, 'end', h('div', 'cs-end'));
      end.appendChild(words_(s.end));
      // as large as its words allow: one line, or two when it is long
      var letters = String(s.end).length, rows = letters > 18 ? 2 : 1;
      end.style.setProperty('--end', String(Math.min(10.5, Math.round(880 / (0.78 * Math.ceil(letters / rows))) / 10)));
      box.appendChild(end);
      run.ender = box;
    }
    return box;
  };
  TEMPLATES.spotlight = function (run, s) {
    var box = h('div', 'cs-spot' + (s.side === 'right' ? ' cs-spot-right' : ''));
    var frame = h('div', 'cs-spot-frame');
    s.pictures.forEach(function (row, i) { frame.appendChild(shot(run, 'picture:' + i, row)); });
    var side = h('div', 'cs-spot-words');
    add(side, kicker(run, s), part(run, 'title', words_(s.title)),
      s.sub ? part(run, 'sub', h('div', 'cs-sub', s.sub)) : null, figureOf(run, s.figure), chips(run, s.chips));
    add(box, frame, side);
    return box;
  };
  TEMPLATES.mosaic = function (run, s) {
    var box = h('div', 'cs-mosaic');
    var grid = h('div', 'cs-ptiles cs-ptiles-' + s.tiles.length);
    s.tiles.forEach(function (row, i) {
      var t = part(run, 'tile:' + i, h('div', 'cs-ptile cs-glass'));
      t.appendChild(picture(run, row.asset, t, row.fit));
      if (row.label) t.appendChild(h('span', 'cs-cap', row.label));
      grid.appendChild(t);
    });
    add(box, kicker(run, s), s.title ? part(run, 'title', words_(s.title)) : null, grid,
      s.foot ? part(run, 'foot', h('div', 'cs-foot', s.foot)) : null);
    return box;
  };
  function words_(text) { return words('cs-title cs-title-m', text); }
  // The templates whose pictures are the slide; the others are words in a console panel.
  var PICTURE_LED = { film: true, spotlight: true, mosaic: true };

  // ---- acts (owner 17 Nov evening: "แยกเอเจนท์สำหรับออกแบบแต่ละส่วน แล้วก็ให้ทำ") ----
  // The film is built from acts: one file per part of the show
  // (/assets/ceremony-show/acts/<name>.js + an optional .css), each registering
  //     CeremonyShow.register('<name>', function (ctx) { return { beat, progress, settle, clear }; })
  // and drawing its own piece in ctx.stage. What an act can put on the screen is the
  // slide's content and nothing else: words through ctx.text / ctx.figure (and a picture's
  // caption), pictures and clips through ctx.picture / ctx.video -- the server checked every
  // one of them against the line (app/ceremony_slides.py, "acts") -- and anything those
  // factories made for a key that a beat brings in stays hidden until that beat: the checks
  // are about what the hall really sees. docs/emma-ceremony-slides.md section 0 is the
  // contract; tests/test_ceremony_acts.py reads every act file for another way to draw words.
  var ACT_NAME = /^[a-z][a-z0-9_]{1,31}$/, KEY = /^[a-z][a-z0-9_]{0,31}$/;
  var ACT_BASE = '/assets/ceremony-show/acts/';
  var FILE = /^\/(?:story-asset|ceremony-asset)\/[0-9a-f]{64}$/;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var SHAPES = { svg: 1, g: 1, path: 1, circle: 1, ellipse: 1, rect: 1, line: 1, polyline: 1, polygon: 1, defs: 1,
                 linearGradient: 1, radialGradient: 1, stop: 1, clipPath: 1, mask: 1 };
  var EASE = { move: 'cubic-bezier(.22, .8, .2, 1)', inout: 'cubic-bezier(.65, 0, .35, 1)',
               push: 'cubic-bezier(.3, 0, .1, 1)', linear: 'linear' };
  var ACTS = {}, actJobs = {}, bgEl = null, creditEl = null, gateEl = null;
  var GL_MOST = 4;          // WebGL pictures in one slide: a page has few contexts, and Emma's own is one
  // ---- line to line: THE CUT (owner 18 Nov afternoon, on the scene videos: "ดูเรื่อง transition
  // ด้วย" -- a cut read as "a picture fades to another, mostly still, picture"). One language
  // for the whole film, in the stylesheet ("line to line"): a sheet of light crosses the stage
  // left to right -- the way the film's words light up -- taking the shot that is up with it
  // and leaving the next one behind it, already moving. In here:
  //   hold   a new slide does not come up before its act has built it and the pictures of its
  //          opening and of its first beat are decoded (and drawn once, unseen, so the cut
  //          itself uploads nothing): the frame that is up HOLDS, alive, for at most CUT.hold
  //          -- CUT.soft for a picture of the opening that could not be seen yet anyway (an act
  //          that builds every picture of its line at once and brings them in itself).
  //          Her voice and the beats never wait: progress() is taken from the first moment.
  //   into   the sheet crosses (the new shot uncovered behind it); CUT.down the same for a line
  //          going down to the logo -- an exit, a third shorter.
  //   blend  WITHIN a scene there is no sheet (review 19 Nov: the same 0.56 s light-bar wipe on
  //          ~16 handovers, ~2 screen widths a second -- the fastest thing in the film -- and
  //          several of them snapped dark UI to a bright picture, +80 luma in 0.3 s). Two lines
  //          of one scene DISSOLVE, no hard edge: the camera keeps travelling -- the shot going
  //          down pushes on as it goes, the next one settles in as it comes -- the new shot
  //          over the old, which goes only once the new one covers it (a shot with see-through
  //          parts never shows the old through it, and never pops when the old leaves).
  //          The sheet is for a SCENE change only (the cue's sNN_ prefix: show(slide, {cue}))
  //          and slower now; the shot it uncovers comes up graded, through a veil that lifts
  //          after the light (no dark-to-bright snap at the seam).
  // A join (the same act going on in its mode: the frames are the same) and a screen that
  // joined late have no sheet; they still hold for their pictures.
  var CUT = { hold: 480, soft: 240, into: 1200, down: 800, blend: 1200, fade: 300 };          // ms
  var leaving = [];         // slides going down that are still seen: their frames go on until they are gone

  function register(name, factory) {
    if (typeof name !== 'string' || !ACT_NAME.test(name) || typeof factory !== 'function') return false;
    ACTS[name] = factory;
    return true;
  }
  function warn(what, why) {
    try { if (window.console && window.console.warn) window.console.warn('ceremony-show: ' + what, why || ''); } catch (e) { /* no console */ }
  }
  // An act's files, asked for once per tag. /assets sends no Cache-Control, so the address
  // carries a tag of the bytes (app/ceremony_slides.py acts()): a changed act is a new
  // address and is loaded again; the same one is never fetched twice.
  function loadAct(name, v, css, done) {
    var tag = (typeof v === 'string' && /^[0-9a-f]{6,64}$/.test(v)) ? v : '';
    var job = actJobs[name];
    if (job && job.tag === tag) {
      if (job.state === 'loading') { if (done) job.waiting.push(done); return; }
      if (job.state === 'ready' && ACTS[name]) { if (done) done(true); return; }
    } else if (ACTS[name] && (!job || !tag)) { if (done) done(true); return; }      // (registered some other way)
    var stale = job && job.link;
    job = actJobs[name] = { tag: tag, state: 'loading', waiting: done ? [done] : [], link: null };
    if (!doc.head) { job.state = 'failed'; if (done) done(false); return; }
    if (stale && stale.parentNode) stale.parentNode.removeChild(stale);      // (two stylesheets of one act would both apply)
    delete ACTS[name];
    var left = css ? 2 : 1, ok = true;
    var part = function (good) {
      if (!good) ok = false;
      if (--left > 0) return;
      if (actJobs[name] !== job) return;                    // (a newer tag took over)
      job.state = ok && ACTS[name] ? 'ready' : 'failed';
      var list = job.waiting;
      job.waiting = [];
      list.forEach(function (fn) { fn(job.state === 'ready'); });
    };
    if (css) {
      var link = doc.createElement('link');
      link.rel = 'stylesheet';
      link.href = ACT_BASE + name + '.css' + (tag ? '?v=' + tag : '');
      link.onload = function () { part(true); };
      link.onerror = function () { part(true); };           // (an act without its styles still runs)
      job.link = link;
      doc.head.appendChild(link);
    }
    var js = doc.createElement('script');
    js.src = ACT_BASE + name + '.js' + (tag ? '?v=' + tag : '');
    js.onload = function () { part(true); };
    js.onerror = function () { part(false); };
    doc.head.appendChild(js);
  }
  // Every act of the folder, asked for when the screen opens: the first line of each part
  // must not wait for its code.
  function preload() {
    var where = window.location;
    if (typeof window.fetch !== 'function' || !where || !/^https?:$/.test(where.protocol || '')) return;
    try {
      window.fetch('/ceremony-acts.json', { cache: 'no-store', credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (m) {
          warmFont();
          if (m && m.idle) idleSetup(m.idle);
          if (m && m.reveal) revealPrepare(m.reveal);
          var list = m && m.acts;
          if (!list || typeof list !== 'object') return;
          Object.keys(list).forEach(function (name) {
            if (ACT_NAME.test(name) && list[name]) loadAct(name, list[name].v, list[name].css === true);
          });
        })['catch'](function () { /* the acts load on demand */ });
    } catch (e) { /* the same */ }
  }

  // The film's face (ceremony-show.css @font-face, `block`): asked for when the screen opens
  // and again when the stage is built, so the first words are never held back for the file.
  // Since 22 Nov Kantalad's four faces (one weight inside each face's range: none of them is
  // drawn first in another face and then swapped -- the subtitles' too) and Inter behind them.
  var FACES = ['300 1em "Kantalad"', '470 1em "Kantalad"', '600 1em "Kantalad"', '700 1em "Kantalad"', '600 1em "Inter"'];
  // (review 22 Nov night: the page adds the stylesheet and this script in the same moment, and the
  // script may run first -- with the stylesheet 150 ms late, load() found no @font-face and asked for
  // nothing; Medium and Bold then came only with the first slide, its words held invisible (`block`)
  // for the trip over the hall's Wi-Fi. No Kantalad face registered yet = ask again in a quarter of a
  // second, for up to 15 s; one timer, however many callers.)
  var warmTries = 0, warmTimer = null;
  function kantaladRegistered() {
    var n = 0;
    try {
      doc.fonts.forEach(function (face) { if (String(face.family).replace(/["']/g, '') === 'Kantalad') n++; });
    } catch (e) { /* none seen */ }
    return n > 0;
  }
  function warmFont() {
    try {
      if (!doc.fonts || typeof doc.fonts.load !== 'function') return;
      if (!kantaladRegistered()) {
        if (!warmTimer && warmTries < 60) {
          warmTries++;
          warmTimer = setTimeout(function () { warmTimer = null; warmFont(); }, 250);
        }
        return;
      }
      FACES.forEach(function (face) {
        var asked = doc.fonts.load(face);
        if (asked && asked['catch']) asked['catch'](function () { /* the fallback face */ });
      });
    } catch (e) { /* the fallback face */ }
  }

  function own(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  function num(v, fallback) { return typeof v === 'number' && isFinite(v) ? v : fallback; }
  function fmt(v) { return String(Math.round(v * 10000) / 10000); }
  function cl(c) { return typeof c === 'string' && /^[A-Za-z0-9_\- ]{1,120}$/.test(c) ? ' ' + c : ''; }
  function isNode(e) { return !!e && typeof e === 'object' && typeof e.appendChild === 'function'; }
  function cu(n) { return 'calc(' + fmt(n) + ' * var(--cu))'; }

  // The map taking a w x h box onto four points (TL, TR, BR, BL), as the sixteen numbers
  // of a CSS matrix3d: how words stand IN a picture (on its ground, on a wall).
  function quadMatrix(w, hgt, q) {
    var x0 = q[0][0], y0 = q[0][1], x1 = q[1][0], y1 = q[1][1], x2 = q[2][0], y2 = q[2][1], x3 = q[3][0], y3 = q[3][1];
    var dx1 = x1 - x2, dx2 = x3 - x2, dx3 = x0 - x1 + x2 - x3;
    var dy1 = y1 - y2, dy2 = y3 - y2, dy3 = y0 - y1 + y2 - y3;
    var den = dx1 * dy2 - dx2 * dy1;
    if (!den || !w || !hgt) return null;
    var g = (dx3 * dy2 - dx2 * dy3) / den, k = (dx1 * dy3 - dx3 * dy1) / den;
    var a = x1 - x0 + g * x1, b = x3 - x0 + k * x3, d = y1 - y0 + g * y1, e = y3 - y0 + k * y3;
    return [a / w, d / w, 0, g / w, b / hgt, e / hgt, 0, k / hgt, 0, 0, 1, 0, x0, y0, 0, 1];
  }

  // ---- how words arrive (owner 18 Nov morning, the whole preview watched: "ทำพวกโมชั่นหนังสือ
  // ด้วย"). ctx.text's `reveal` picks the entrance; the CSS that plays it is the core's
  // ("words arrive", ceremony-show.css). It starts when the words can first be seen -- the
  // act goes live, or their beat lifts the gate (`:not(.cs-await *)` in the CSS) -- so it can
  // only ever come AFTER the moment the gate allows, never before. Only opacity (and, for
  // `pop`, transform) moves. Shown late (a screen that joined, a beat fired at the end of
  // the line): data-rv="off", the words are simply there.
  // A text the act split itself (split: 'words' / 'chars') is the act's own to move: no
  // entrance unless it asks for one by name. Typed text (fx.typed) never gets one.
  // The way out is the class `cs-leave` on the text or the figure (fx.depart(el) puts it on,
  // fx.depart(el, false) brings the words back with their arrival).
  //
  // THE FILM'S TYPE (owner 18 Nov afternoon, with Apple's MacBook Pro film as the reference:
  // "ปรับพวกฟ้อนกับการแสดงข้อความตัวหนังสือให้มันประมาณนี้"). The entrances above were this
  // morning's; they are gone. Every word of the film is now one face (--cs-font: Kantalad since 22 Nov),
  // upright, in the case the slide gives it, white running to pale teal ACROSS the phrase,
  // and it arrives the way that film's words do: the letters LIGHT UP left to right -- each
  // unseen, then dim, then bright, ~30 ms apart, the whole phrase inside half a second --
  // with a soft glow of the same hue blooming behind a headline. Nothing flies, tilts or
  // slams. It leaves the same way, shorter: it cools and fades left to right (~280 ms).
  //   light  the letters light up left to right (the default; `illuminate` is the same --
  //          the name the owner's brief gives it)
  //   pop    Playful, for a joke's beat: the words pop up one after another with a little
  //          overshoot
  //   none   simply there (also: `reveal: false`)
  //   depth, tilt, rise, track, flare  this morning's names: accepted, they mean `light`
  // `glow` (true / false / 'shade'; default: on for cs-type-h1 and cs-type-h2): the halo
  // behind the phrase -- a drawn gradient, never a blurred shadow; 'shade' = a soft dark one,
  // for words that stand on a bright picture.
  // The colour runs across the PIECES (each carries --t, its place 0..1 across the phrase;
  // ceremony-show.css ".cs-tg"), so every text is in pieces: by letter, or by word when the
  // act asked for words. Letters the core made for itself are plain inline spans (cs-tl):
  // the line is shaped as one run, kerning and all; letters an act asked for (`split:
  // 'chars'`) are the inline-blocks they always were (cs-tc) -- an act moves those.
  var RV_NAME = { light: 'light', illuminate: 'light', pop: 'pop', depth: 'light', tilt: 'light', rise: 'light', track: 'light', flare: 'light' };
  var RV_UNIT = { light: 'chars', pop: 'words' };
  // ms: the step from one piece to the next, how long the last piece takes, the whole phrase
  var RV_STEP = { light: 30, pop: 80 }, RV_LAST = { light: 260, pop: 320 }, RV_BUDGET = { text: 500, hero: 600 };
  var calm = false;                                     // prefers-reduced-motion (ensure())
  function textReveal(o) {
    var r = o.reveal;
    if (calm || r === false || r === 'none') return null;
    if (typeof r === 'string' && own(RV_NAME, r)) return RV_NAME[r];
    if (o.split === 'words' || o.split === 'chars') return null;
    return 'light';
  }
  function textEl(text, o) {
    var e = h('span', 'cs-text' + cl(o.cls)), i = 0, rv = textReveal(o), pieces = [];
    var asked = o.split === 'words' || o.split === 'chars' ? o.split : null;
    // (an act that did not split: the core does -- for the colour and for the entrance -- but
    // puts no --n on the text itself: its look is the act's)
    var split = asked || (rv ? RV_UNIT[rv] : 'chars');
    if (split === 'chars' && asked) e.__chars = [];
    text.split(' ').forEach(function (word, n) {
      if (!word) return;
      if (n) e.appendChild(doc.createTextNode(' '));
      var w = h('span', 'cs-tw');
      if (split === 'words') {
        w.textContent = word;
        w.style.setProperty('--i', String(i++));
        pieces.push(w);
      } else {
        word.split('').forEach(function (ch) {
          var c = h('span', asked ? 'cs-tc' : 'cs-tl');
          c.textContent = ch;
          c.style.setProperty('--i', String(i++));
          w.appendChild(c);
          pieces.push(c);
          if (asked) e.__chars.push(c);
        });
      }
      e.appendChild(w);
    });
    if (asked) e.style.setProperty('--n', String(i));
    pieces.forEach(function (p, k) {
      p.classList.add('cs-tg');
      p.style.setProperty('--t', i > 1 ? fmt(k / (i - 1)) : '0');
    });
    var c = ' ' + (typeof o.cls === 'string' ? o.cls : '') + ' ';
    var glow = o.glow === 'shade' ? 'shade' : (o.glow === true || (o.glow !== false && / cs-type-h[12] /.test(c)) ? 'glow' : null);
    if (glow) e.classList.add(glow === 'shade' ? 'cs-text-shade' : 'cs-text-glow');
    if (rv) {
      e.setAttribute('data-rv', rv);
      // (the cascade's budget: 30 ms a letter -- but the whole phrase lit inside 500 ms (a
      // hero title, h1 or giant, 600), so a long line closes up its steps and never runs long)
      var hero = / cs-type-(?:h1|giant) /.test(c);
      var room = (hero ? RV_BUDGET.hero : RV_BUDGET.text) - RV_LAST[rv];
      if (i > 1) e.style.setProperty('--rvs', Math.round(Math.min(RV_STEP[rv], room / (i - 1))) + 'ms');
      // (words with a number in them arrive together: a number is never on the screen a
      // moment without the words the line gives it -- "about" ... "in a straight line")
      if (/[0-9]/.test(text)) e.style.setProperty('--rvs', '0ms');
    }
    return e;
  }
  // ---- the cover over a real person (ctx.picture, `keep_out`) ----
  // One patch: `ko.reg` = the box and KO_PAD of it again each way, in fractions of the file.
  //  1. the picture's pixels of that region, read at a few dozen cells across; every cell that
  //     touches any box (or comes within a cell of one) is a HOLE, as is every cell outside the
  //     file or see-through;
  //  2. the hole is filled from its rim by repeated averaging (a membrane stretched over it:
  //     smooth, the colours of its edges) -- the low tones;
  //  3. the fine grain of the picture beside it (the same shape moved sideways, or up/down, to
  //     where no box is) is laid on that membrane -- so water stays rippled and leaves stay
  //     leafy, and the cover is not a smooth smudge (review 18 Nov: a flat disc drew the eye);
  //  4. round the hole the patch is the picture itself, and its alpha is 1 over the box (a
  //     rounded square holding the box's corners) falling smoothly to 0 at the patch's edge.
  // Nothing of a person can be in it: the membrane is made of cells that touch no box, the
  // grain comes from a place that touches no box, and over the box the picture's own pixels
  // have weight 0. Returns false when the picture gave nothing to read yet (all see-through:
  // not decoded -- read too soon, a GPU-decoded picture gives transparent zeros: the black
  // patch of one run).
  var KO_PAD = 0.5, KO_CELLS = 28, KO_MAX = 192, KO_GRAIN = 0.85;
  // `src` = where the pixels come from: {im, W, H, ox, oy} -- something drawable (the img element, or
  // an ImageBitmap of the file cut round one patch: koCut), the file's size, and where `im`'s
  // first pixel lies in the file. `rect` is in fractions of the file.
  function koRead(src, rect, w, hgt) {
    var c = doc.createElement('canvas');
    c.width = w;
    c.height = hgt;
    var g = c.getContext('2d', { willReadFrequently: true });
    g.imageSmoothingEnabled = true;
    try { g.imageSmoothingQuality = 'high'; } catch (e) { /* the default filter */ }
    var W = src.W, H = src.H;
    var x0 = Math.max(0, rect[0]), y0 = Math.max(0, rect[1]), x1 = Math.min(1, rect[0] + rect[2]), y1 = Math.min(1, rect[1] + rect[3]);
    if (x1 > x0 && y1 > y0) {
      g.drawImage(src.im, x0 * W - src.ox, y0 * H - src.oy, (x1 - x0) * W, (y1 - y0) * H,
        (x0 - rect[0]) / rect[2] * w, (y0 - rect[1]) / rect[3] * hgt, (x1 - x0) / rect[2] * w, (y1 - y0) / rect[3] * hgt);
    }
    return { c: c, g: g, d: g.getImageData(0, 0, w, hgt).data };
  }
  // The file's bitmap cut to what one patch can ever read (its region and, for the grain, that
  // region moved by its own size and a cell more, each way): what the page's thread then scales
  // is that piece, never the whole render.
  function koCut(bm, reg) {
    var W = bm.width, H = bm.height;
    var x0 = Math.max(0, reg[0] - 1.1 * reg[2]), y0 = Math.max(0, reg[1] - 1.1 * reg[3]);
    var x1 = Math.min(1, reg[0] + 2.1 * reg[2]), y1 = Math.min(1, reg[1] + 2.1 * reg[3]);
    var sx = Math.max(0, Math.floor(x0 * W)), sy = Math.max(0, Math.floor(y0 * H));
    var sw = Math.min(W, Math.ceil(x1 * W)) - sx, sh = Math.min(H, Math.ceil(y1 * H)) - sy;
    if (!(sw > 0 && sh > 0)) return Promise.resolve(null);
    return window.createImageBitmap(bm, sx, sy, sw, sh).then(function (piece) {
      return { im: piece, W: W, H: H, ox: sx, oy: sy };
    }, function () { return null; });
  }
  // a small canvas drawn up to w x h, smoothly: the low tones of a grid at full size
  function koUp(src, w, hgt) {
    var c = doc.createElement('canvas');
    c.width = w;
    c.height = hgt;
    var g = c.getContext('2d', { willReadFrequently: true });
    g.imageSmoothingEnabled = true;
    try { g.imageSmoothingQuality = 'high'; } catch (e) { /* the default filter */ }
    g.drawImage(src, 0, 0, src.width, src.height, 0, 0, w, hgt);
    return g.getImageData(0, 0, w, hgt).data;
  }
  function koGrid(data, gw, gh) {
    var c = doc.createElement('canvas');
    c.width = gw;
    c.height = gh;
    var g = c.getContext('2d'), img = g.createImageData(gw, gh);
    img.data.set(data);
    g.putImageData(img, 0, 0);
    return c;
  }
  function koFill(ko, src, boxes) {
    var W = src.W, H = src.H, reg = ko.reg;
    var aspect = (reg[2] * W) / Math.max(1e-6, reg[3] * H);
    var gw = aspect >= 1 ? KO_CELLS : Math.max(8, Math.round(KO_CELLS * aspect));
    var gh = aspect >= 1 ? Math.max(8, Math.round(KO_CELLS / aspect)) : KO_CELLS;
    var cw = reg[2] / gw, ch = reg[3] / gh, n = gw * gh;
    var grid = koRead(src, reg, gw, gh), px = grid.d;
    var touches = function (u0, v0, u1, v1) {
      for (var b = 0; b < boxes.length; b++) {
        var r = boxes[b];
        if (u1 + cw > r[0] && u0 - cw < r[2] && v1 + ch > r[1] && v0 - ch < r[3]) return true;
      }
      return false;
    };
    var col = [new Float32Array(n), new Float32Array(n), new Float32Array(n)], known = new Uint8Array(n);
    var sum = [0, 0, 0], got = 0, seen = 0, k, q;
    for (var j = 0; j < gh; j++) {
      for (var i = 0; i < gw; i++) {
        k = j * gw + i;
        var u0 = reg[0] + i * cw, v0 = reg[1] + j * ch;
        if (px[k * 4 + 3] > 0) seen++;
        if (u0 < 0 || v0 < 0 || u0 + cw > 1 || v0 + ch > 1 || px[k * 4 + 3] < 250 || touches(u0, v0, u0 + cw, v0 + ch)) continue;
        known[k] = 1;
        for (q = 0; q < 3; q++) { col[q][k] = px[k * 4 + q]; sum[q] += px[k * 4 + q]; }
        got++;
      }
    }
    if (!seen) return false;                                  // (nothing drawn: not decoded yet)
    // no ground of its own round it (all see-through, a keyed model): the stage's night
    var mean = got ? sum.map(function (t) { return t / got; }) : [3, 5, 13];
    for (k = 0; k < n; k++) if (!known[k]) { col[0][k] = mean[0]; col[1][k] = mean[1]; col[2][k] = mean[2]; }
    // 2. the membrane: each hole cell, again and again, the mean of its neighbours
    for (var it = 0; it < 260; it++) {
      for (var y = 0; y < gh; y++) {
        for (var x = 0; x < gw; x++) {
          var a = y * gw + x;
          if (known[a]) continue;
          for (q = 0; q < 3; q++) {
            var s = 0, m = 0, cc = col[q];
            if (x > 0) { s += cc[a - 1]; m++; }
            if (x < gw - 1) { s += cc[a + 1]; m++; }
            if (y > 0) { s += cc[a - gw]; m++; }
            if (y < gh - 1) { s += cc[a + gw]; m++; }
            cc[a] = s / m;
          }
        }
      }
    }
    var low = new Uint8ClampedArray(n * 4), hole = new Uint8ClampedArray(n * 4);
    for (k = 0; k < n; k++) {
      low[k * 4] = col[0][k]; low[k * 4 + 1] = col[1][k]; low[k * 4 + 2] = col[2][k]; low[k * 4 + 3] = 255;
      hole[k * 4] = hole[k * 4 + 1] = hole[k * 4 + 2] = known[k] ? 0 : 255; hole[k * 4 + 3] = 255;
    }
    // the patch's own size: about its file pixels, never more than KO_MAX on its long side
    var pw = reg[2] * W, ph = reg[3] * H, sc = Math.min(1, KO_MAX / Math.max(pw, ph));
    var ow = Math.max(gw * 2, Math.round(pw * sc)), oh = Math.max(gh * 2, Math.round(ph * sc));
    var up = koUp(koGrid(low, gw, gh), ow, oh), mask = koUp(koGrid(hole, gw, gh), ow, oh);
    var pic = koRead(src, reg, ow, oh).d;
    // 3. where the grain comes from: the hole moved one way by its own size and a cell more,
    // to a place inside the file where no hole cell lands on a box
    var lo = [gw, gh], hi = [0, 0];
    for (k = 0; k < n; k++) {
      if (known[k]) continue;
      var ci = k % gw, cj = (k - ci) / gw;
      lo[0] = Math.min(lo[0], ci); lo[1] = Math.min(lo[1], cj); hi[0] = Math.max(hi[0], ci + 1); hi[1] = Math.max(hi[1], cj + 1);
    }
    var holeW = (hi[0] - lo[0] + 1) * cw, holeH = (hi[1] - lo[1] + 1) * ch, grain = null;
    [[-holeW, 0], [holeW, 0], [0, holeH], [0, -holeH]].some(function (d) {
      for (var z = 0; z < n; z++) {
        if (known[z]) continue;
        var zi = z % gw, zj = (z - zi) / gw, su = reg[0] + zi * cw + d[0], sv = reg[1] + zj * ch + d[1];
        if (su < 0 || sv < 0 || su + cw > 1 || sv + ch > 1 || touches(su, sv, su + cw, sv + ch)) return false;
      }
      var from = [reg[0] + d[0], reg[1] + d[1], reg[2], reg[3]];
      var fine = koRead(src, from, ow, oh), coarse = koRead(src, from, gw, gh);
      grain = { d: fine.d, low: koUp(coarse.c, ow, oh) };
      return true;
    });
    // 4. the patch: the picture round the hole, membrane + grain in it, a feathered rim
    var out = doc.createElement('canvas');
    out.width = ow;
    out.height = oh;
    var og = out.getContext('2d'), img = og.createImageData(ow, oh), o = img.data;
    var bx = 0.5 / (1 + 2 * KO_PAD), solid = bx * 1.2 * 1.06, edge = 0.5 / solid;   // (1.2 > 2^(1/4): the corners)
    var b0 = [], cbox = boxes.map(function (r) { return [(r[0] - reg[0]) / reg[2], (r[1] - reg[1]) / reg[3], (r[2] - reg[0]) / reg[2], (r[3] - reg[1]) / reg[3]]; });
    for (var oy = 0; oy < oh; oy++) {
      for (var ox = 0; ox < ow; ox++) {
        var e = (oy * ow + ox) * 4, fu = (ox + 0.5) / ow, fv = (oy + 0.5) / oh, w = mask[e] / 255;
        for (var bb = 0; bb < cbox.length; bb++) {
          b0 = cbox[bb];
          if (fu >= b0[0] && fu <= b0[2] && fv >= b0[1] && fv <= b0[3]) { w = 1; break; }    // (over a box: never the picture)
        }
        for (q = 0; q < 3; q++) {
          var fill = up[e + q] + (grain ? KO_GRAIN * (grain.d[e + q] - grain.low[e + q]) : 0);
          o[e + q] = (1 - w) * pic[e + q] + w * fill;
        }
        var X = Math.abs(fu - 0.5) / solid, Y = Math.abs(fv - 0.5) / solid;
        var rho = Math.sqrt(Math.sqrt(X * X * X * X + Y * Y * Y * Y)), f = Math.max(0, Math.min(1, (rho - 1) / (edge - 1)));
        o[e + 3] = Math.round(255 * (1 - f * f * (3 - 2 * f)));
      }
    }
    og.putImageData(img, 0, 0);
    out.className = 'cs-ko-fill';
    if (ko.canvas && ko.canvas.parentNode) ko.canvas.parentNode.removeChild(ko.canvas);
    ko.canvas = out;
    ko.el.appendChild(out);
    ko.el.classList.add('cs-ko-filled');
    return true;
  }
  // Shown late: no entrance for what this element holds (its words are simply there).
  function noEntrance(e) {
    if (!e || typeof e.getAttribute !== 'function') return;
    if (e.getAttribute('data-rv') !== null) e.setAttribute('data-rv', 'off');
    var kids = e.children || [];
    for (var k = 0; k < kids.length; k++) noEntrance(kids[k]);
  }
  // A late build: what is on the screen now is there at once; what still waits for its beat
  // keeps its entrance for that beat.
  function noEntranceOpen(e) {
    if (!e || !e.classList || e.classList.contains('cs-await') || e.classList.contains('cs-whipped')) return;
    if (typeof e.getAttribute === 'function' && e.getAttribute('data-rv') !== null) e.setAttribute('data-rv', 'off');
    var kids = e.children || [];
    for (var k = 0; k < kids.length; k++) noEntranceOpen(kids[k]);
  }

  // What an act is given. Everything on the screen comes out of these factories.
  function context(run, late) {
    var s = run.slide, ctx = {};
    var maps = { text: s.texts, figure: s.figures, picture: s.pictures, video: s.videos };
    function keyed(key, e) {
      e.setAttribute('data-k', key);
      if (run.wait[key]) e.classList.add('cs-await');       // its beat has not come: not on the screen
      (run.keyed[key] = run.keyed[key] || []).push(e);
      return e;
    }
    // (a card round a text, the canvas of a picture with depth: what wraps a keyed thing is
    // hidden with it -- an empty card, or the picture itself drawn by WebGL, before its beat)
    function adopt(wrapper, child) {
      var key = isNode(child) && typeof child.getAttribute === 'function' ? child.getAttribute('data-k') : null;
      return key ? keyed(key, wrapper) : wrapper;
    }
    function entry(kind, key) {
      var map = maps[kind];
      if (typeof key === 'string' && map && typeof map === 'object' && own(map, key) && map[key] !== null) return map[key];
      warn('act ' + run.act + ': this slide has no ' + kind + ' "' + key + '"');
      return null;
    }
    ctx.stage = run.box;
    // the act's slide root (.cs-act-<name>): the place for classes that set the state of the
    // whole act (17 Nov evening: an act reached it as stage.parentNode -- the scan refuses that)
    ctx.root = run.root;
    ctx.slide = s;
    ctx.late = !!late;
    // the act slide that was up when this line came (null = none): {act, slide, settled,
    // state} -- state = what its handover() gave, so a cue can start from the frame the
    // cue before held (never a cut between two lines of one part)
    ctx.prev = run.prev;
    ctx.has = function (key) {
      return own(s.texts, key) || own(s.figures, key) || own(s.pictures, key) || own(s.videos, key);
    };
    ctx.text = function (key, o) {
      var t = entry('text', key);
      if (typeof t !== 'string') return null;
      var e = keyed(key, textEl(t, o || {}));
      // where it is in her line ([from, to] of it): typed text never runs ahead of her voice
      var sp = s.said && own(s.said, key) ? s.said[key] : null;
      if (sp && sp.length === 2 && sp[0] >= 0 && sp[1] > sp[0] && sp[1] <= 1) e.__said = [+sp[0], +sp[1]];
      return e;
    };
    // A figure is one piece -- its lead, its value, its unit, its label -- and shows its own
    // value only: nothing here counts up to it (see land()).
    ctx.figure = function (key, o) {
      var f = entry('figure', key);
      if (!f || typeof f.value !== 'string') return null;
      o = o || {};
      var e = h('div', 'cs-fig' + (o.glow ? ' cs-fig-glow' : '') + cl(o.cls)), row = h('div', 'cs-fig-row');
      add(row, f.lead ? h('span', 'cs-fig-lead', f.lead) : null, h('span', 'cs-fig-value', f.value),
        f.unit ? h('span', 'cs-fig-unit', f.unit) : null);
      add(e, row, f.label ? h('div', 'cs-fig-label', f.label) : null);
      // its entrance (the core's CSS, "a figure lights up"): the whole figure -- the value
      // and the words that say what it is -- lights up as ONE piece, unseen, dim, bright,
      // and the halo blooms behind it. `reveal`: 'light' (the default; this morning's 'land'
      // and 'rise' mean it too) | 'pop' (Playful, a joke's beat) | 'none' / false. Its own
      // value from the first frame it can be seen -- an entrance lights it, never counts.
      // `pulse` (default: with `glow`): the halo breathes while it is up.
      var rv = calm || o.reveal === false || o.reveal === 'none' ? null : (o.reveal === 'pop' ? 'pop' : 'light');
      if (rv) e.setAttribute('data-rv', rv);
      if (!calm && (o.pulse === true || (o.pulse !== false && o.glow))) e.setAttribute('data-pulse', '1');
      return keyed(key, e);
    };
    // A picture: the full-size file when the ceremony's index has one, else the
    // catalogue's copy -- asked for at once, shown only once decoded (never an empty box).
    ctx.picture = function (key, o) {
      var a = entry('picture', key);
      if (!a || typeof a !== 'object') return null;
      o = o || {};
      var fit = o.fit === 'contain' || o.fit === 'cover' ? o.fit : a.fit;
      var el = h('span', 'cs-apic' + (fit === 'contain' ? ' cs-fit' : '') + (a.alpha === true ? ' cs-drawing' : ''));
      var inner = h('span', 'cs-apic-in');
      el.appendChild(inner);
      var base = typeof a.src === 'string' && FILE.test(a.src) ? a.src : null;
      var full = typeof a.hires === 'string' && FILE.test(a.hires) ? a.hires : null;
      var out = { el: keyed(key, el), img: null, url: full || base, fit: fit === 'contain' ? 'contain' : 'cover',
                  w: num(full ? a.hw : a.w, 0), h: num(full ? a.hh : a.h, 0),
                  depth: typeof a.depth === 'string' && FILE.test(a.depth) ? a.depth : null,
                  near: a.depth_near === 'dark' ? 'dark' : 'bright',
                  alpha: a.alpha === true, caption: null };
      // a map's credit (ODbL): drawn by the core whenever this picture shows (creditTick)
      if (typeof a.credit === 'string' && a.credit) run.credited.push({ el: el, text: a.credit });
      // where a real person is in it (index `people` / `keep_out`, fractions of the file): no
      // slide may show that part -- the acts' run in Chromium checks every frame against it
      if (Array.isArray(a.keep_out)) el.__keepOut = a.keep_out.filter(function (b) {
        return Array.isArray(b) && b.length === 4 && b.every(function (v) { return typeof v === 'number'; });
      });
      if (el.__keepOut) el.__keepOutWH = [out.w, out.h];
      // ... and wherever it shows, the core paints that part over (a safety net under the
      // framing rule: a far thumbnail or a reel card still carries the person at a few
      // pixels). Review 18 Nov (prj_life_lagoon): a disc of ONE averaged colour read as a flat
      // grey coin on the water -- the eye went straight to it -- and once came out black (a
      // picture read before the GPU had decoded it gives transparent zeros). Now the cover is
      // the picture's own surroundings flowed in (koFill): the part of the picture round the
      // box, read small (that is the blur), every cell that touches ANY box thrown away, the
      // hole filled smoothly from its edges, laid on with a feathered rim. So it reads as the
      // water or the leaves that were there, and not a pixel of a person is in it. No filter.
      var koGiveUp = null;
      if (el.__keepOut && el.__keepOut.length) {
        // the patches sit in a frame laid out like the displayed picture itself (CSS: cover or
        // contain by container units, object-position by left/top/translate) -- so they follow
        // every size the card takes without a measurement that can be late
        var kof = h('span', 'cs-kof');
        kof.style.setProperty('--kar', fmt(out.w / out.h));
        inner.appendChild(kof);
        var kos = el.__keepOut.map(function (r) {
          var p = h('span', 'cs-keepout'), mx = (r[2] - r[0]) * KO_PAD, my = (r[3] - r[1]) * KO_PAD;
          // (the patch: the box and half of it again each way -- solid over the box, feathered
          // out to its rim; until it is filled, a soft oval of neutral ground whose solid
          // middle already holds the box)
          var reg = [r[0] - mx, r[1] - my, r[2] - r[0] + 2 * mx, r[3] - r[1] + 2 * my];
          p.style.left = fmt(reg[0] * 100) + '%';
          p.style.top = fmt(reg[1] * 100) + '%';
          p.style.width = fmt(reg[2] * 100) + '%';
          p.style.height = fmt(reg[3] * 100) + '%';
          kof.appendChild(p);
          return { el: p, b: r, reg: reg, canvas: null };
        });
        var tinted = null, tries = 0, asked = null, noBits = false;
        // (the cut waits for the cover to be the picture's own surroundings -- as it waits for
        // the picture: an oval swapped for the patch in the first frames of a shot is a flicker
        // over a person's place)
        var koWarm = { key: key, done: false, el: el };
        run.warm.push(koWarm);
        var covered = function (url) { tinted = url; if (!koWarm.done) { koWarm.done = true; warmed(run); } };
        koGiveUp = function () { covered(null); };
        var tint = function (im) {
          var url = out.url;                                // (the file shown: the catalogue's copy after a failed full-size one)
          if (!im || !im.naturalWidth || !url || tinted === url || asked === url) return;
          // off the page's thread (pixels()): once the picture's bytes are here, whether or not
          // the page has decoded it for the screen yet -- this is a decode of its own
          var job = noBits ? null : pixels(url);
          if (job) {
            asked = url;
            job.then(function (bm) {
              if (run.gone || out.url !== url) { shut(bm); return null; }
              if (!bm) { asked = null; noBits = true; return null; }          // (no bytes: the img element, below)
              return Promise.all(kos.map(function (ko) { return koCut(bm, ko.reg); })).then(function (cuts) {
                shut(bm);
                if (run.gone) { cuts.forEach(function (c) { if (c) shut(c.im); }); return; }
                try { kos.forEach(function (ko, i) { if (cuts[i]) koFill(ko, cuts[i], el.__keepOut); }); } catch (e) { /* the oval stays */ }
                cuts.forEach(function (c) { if (c) shut(c.im); });
                covered(url);
              });
            })['catch'](function () { covered(url); });
            return;
          }
          // (no bitmaps here: the img element read on the page's thread, as it was. Only a decoded
          // picture is read: before that, the pixels can be empty)
          if (!el.classList.contains('cs-loaded')) return;
          var ok = true;
          try {
            kos.forEach(function (ko) { if (!koFill(ko, { im: im, W: im.naturalWidth, H: im.naturalHeight, ox: 0, oy: 0 }, el.__keepOut)) ok = false; });
          } catch (e) { ok = false; }
          // (nothing readable yet: again next frame; a picture that never reads keeps the
          // neutral oval -- it still covers the box)
          if (ok || ++tries > 90) covered(url);
        };
        // the picture's own fit and position as its stylesheet says (an act may set them) --
        // looked at now and then, not every frame (a computed style read in a frame callback
        // pulls the page's style work forward into it)
        var koLook = '', koTick = 0;
        ctx.frame(function () {
          var im = inner.querySelector('img');
          if (!im || !im.isConnected) return;
          if (koLook && koTick++ % 12) { tint(im); return; }
          var cs = getComputedStyle(im), look = cs.objectFit + '|' + cs.objectPosition;
          if (look !== koLook) {
            koLook = look;
            var pos = objectPosition(cs.objectPosition);
            kof.classList.toggle('cs-kof-contain', cs.objectFit === 'contain');
            kof.style.setProperty('--kx', fmt(pos[0] * 100) + '%');
            kof.style.setProperty('--ky', fmt(pos[1] * 100) + '%');
          }
          if (!im.__koListen) { im.__koListen = true; im.addEventListener('load', function () { tint(im); }); }
          tint(im);
        });
        out.keepOut = true;
      }
      // a model photographed on black, keyed to alpha here: it lies on any ground the way
      // the act means it to, with no blend mode (that only works in a flat stacking context)
      var keyBlack = o.keyed === 'black';
      if (out.url) {
        var img = doc.createElement('img'), done = false;
        img.className = 'cs-aimg';
        img.setAttribute('alt', '');
        img.setAttribute('decoding', 'async');
        var keyedDone = false;
        // (the cut waits for it -- if it is of the opening or the first beat: see warmed())
        var warm = { key: key, done: false, el: el };
        run.warm.push(warm);
        var ready = function () {
          if (done) return;
          if (keyBlack && !keyedDone) {
            keyedDone = true;
            keyed_(img, out.url, function (url) {
              if (run.dead) { if (url && window.URL) window.URL.revokeObjectURL(url); return; }
              if (url) { run.blobs.push(url); img.setAttribute('data-src', out.url); img.onload = function () { ready(); }; img.src = url; el.classList.add('cs-keyed'); }
              else ready();
            });
            return;
          }
          done = true;
          run.loaded++;
          if (img.naturalWidth > 0 && img.naturalHeight > 0) { out.w = img.naturalWidth; out.h = img.naturalHeight; }
          if (!run.revealed) el.classList.add('cs-held');    // (decoded under the frame before: there, whole, as the slide comes up)
          el.classList.add('cs-loaded');
          warm.done = true;
          warmed(run);
        };
        img.onload = function () {
          if (typeof img.decode === 'function') img.decode().then(ready, ready); else ready();
        };
        // the full-size file did not come: the catalogue's copy of the same picture
        img.onerror = function () {
          if (!base || out.url === base) { if (koGiveUp) koGiveUp(); warm.done = true; warmed(run); return; }    // (it never will: nothing waits for it)
          out.url = base;
          out.w = num(a.w, 0);
          out.h = num(a.h, 0);
          img.src = base;
        };
        run.pictures++;
        inner.appendChild(img);
        out.img = img;
        img.src = out.url;
      }
      if (typeof a.caption === 'string' && a.caption) out.caption = keyed(key, h('span', 'cs-acap', a.caption));
      return out;
    };
    // A clip: always muted (her voice is the only sound of the line) and never the clock.
    ctx.video = function (key) {
      var a = entry('video', key);
      if (!a || typeof a.src !== 'string' || !FILE.test(a.src)) return null;
      var el = h('span', 'cs-avid'), v = doc.createElement('video');
      v.muted = true;
      v.defaultMuted = true;
      v.loop = false;
      v.controls = false;
      v.preload = 'auto';
      v.setAttribute('muted', '');
      v.setAttribute('playsinline', '');
      // (the cut waits for its first frame, like a picture -- a clip of the opening or the
      // first beat: a join that came up before its clip had a frame showed a hole where the
      // clip of the line before had just been, review 18 Nov "Emma blinks out")
      var warm = { key: key, done: false, el: el };
      var first = function () { if (!warm.done) { warm.done = true; warmed(run); } };
      if (typeof v.addEventListener === 'function') {
        v.addEventListener('volumechange', function () { if (!v.muted) v.muted = true; });
        v.addEventListener('loadeddata', first);
        v.addEventListener('error', first);
        run.warm.push(warm);
      }
      v.src = a.src;
      el.appendChild(v);
      run.videos.push(v);
      return { el: keyed(key, el), w: num(a.w, 0), h: num(a.h, 0),
               ready: function () { return num(v.readyState, 0) >= 2; },
               play: function () { try { var p = v.play(); if (p && p['catch']) p['catch'](function () {}); } catch (e) { /* not ready */ } },
               pause: function () { try { v.pause(); } catch (e) { /* not playing */ } },
               seek: function (sec) { try { v.currentTime = Math.max(0, num(sec, 0)); } catch (e) { /* not ready */ } },
               rate: function (k) { try { v.playbackRate = Math.max(0.1, Math.min(8, num(k, 1))); } catch (e) { /* not ready */ } },
               time: function () { return num(v.currentTime, 0); },
               duration: function () { return num(v.duration, 0); },
               ended: function () { return v.ended === true; } };
    };
    ctx.opt = function (path, fallback) {
      var at = s.options;
      if (typeof path !== 'string' || !path) return fallback;
      var parts = path.split('.');
      for (var i = 0; i < parts.length; i++) {
        if (at === null || typeof at !== 'object' || !own(at, parts[i])) return fallback;
        at = at[parts[i]];
      }
      if (typeof at === 'number') return isFinite(at) ? at : fallback;
      if (typeof at === 'boolean') return at;
      if (typeof at === 'string') return KEY.test(at) ? at : fallback;
      return at !== null && typeof at === 'object' ? at : fallback;
    };
    // an empty div; with a key, one that is hidden with that key until its beat (a wrapper:
    // a card, a frame, a group of words -- nothing in it shows before the thing it carries)
    ctx.el = function (c, key) {
      var e = h('div', cl(c).slice(1));
      return typeof key === 'string' && ctx.has(key) ? keyed(key, e) : e;
    };
    ctx.hold = function (e, key) { return isNode(e) && typeof key === 'string' && ctx.has(key) ? keyed(key, e) : e; };
    ctx.svg = function (tag, attrs) {
      if (!own(SHAPES, tag) || typeof doc.createElementNS !== 'function') return null;     // shapes only: never text
      var e = doc.createElementNS(SVG_NS, tag);
      Object.keys(attrs || {}).forEach(function (k) {
        var v = attrs[k];
        if (/^on|href$/i.test(k) || (typeof v !== 'string' && typeof v !== 'number')) return;
        e.setAttribute(k === 'cls' ? 'class' : k, String(v));
      });
      return e;
    };
    ctx.add = function (parent) {
      for (var i = 1; i < arguments.length; i++) if (isNode(arguments[i])) parent.appendChild(arguments[i]);
      return parent;
    };
    ctx.after = function (ms, fn) {
      later(run, function () { try { fn(); } catch (e) { fail(run, e); } }, ms > 0 ? ms : 0);
    };
    ctx.frame = function (fn) {
      if (typeof fn !== 'function') return function () {};
      run.frames.push(fn);
      kick();
      return function () { run.frames = run.frames.filter(function (x) { return x !== fn; }); };
    };
    // (while the slide is held under the frame before it, the stage is still that frame's:
    // what it asks for here is put up as it comes up -- bringIn())
    ctx.bg = function (mode) {
      run.bg = mode === 'off' || mode === 'dim' ? mode : 'full';
      if (cur === run && run.revealed) { backdrop(run.bg); if (run.light < 1) dimStage(run.light, 0); }
    };
    // The stage's light, 0..1: the shared background and every glass panel's glow of this
    // act follow it (and whatever the act gives the class cs-lit). "Lights off" is a cue.
    ctx.light = function (level, ms) {
      run.light = Math.max(0, Math.min(1, num(level, 1)));
      run.box.style.setProperty('--cs-light', fmt(run.light));
      if (cur === run && run.revealed) dimStage(run.light, run.instant ? 0 : num(ms, 900));
    };
    // How this slide comes up: {kind, ms}. kind: 'cut' (a new scene: the sheet of light crosses
    // in `ms`), 'blend' (the next line of the same scene: it dissolves in over `ms`, the camera
    // travelling), 'join' (the same act going on: there at once), 'late' (a screen that joined:
    // the state).
    // The act's `arrive(cut)` handler, if it has one, is called the moment the slide comes up
    // (not when late) -- the place to start a camera that should be travelling as the shot
    // arrives. Timers set while the slide is still held (ctx.after in the factory) count from
    // that moment too, so an opening's choreography is seen whole.
    ctx.cut = { kind: late ? 'late' : run.cut, ms: late ? 0 : cutMs(run.cut) };
    ctx.fx = effects(run, ctx, adopt);
    return ctx;
  }

  // ---- the shared primitives (docs/emma-ceremony-slides.md 0.4) ----
  // A picture photographed on black, keyed to alpha: alpha = the brightest channel, colour
  // divided by it -- what 'screen' would give on any ground, as a plain picture. Once per
  // picture, on a copy at most 1600 px long; the plain picture when a canvas cannot.
  function objectPosition(v) {
    var word = { left: 0, top: 0, center: 0.5, right: 1, bottom: 1 };
    var parts = String(v || '').trim().split(/\s+/);
    var one = function (t) {
      if (!t) return 0.5;
      if (own(word, t)) return word[t];
      return /%$/.test(t) && isFinite(parseFloat(t)) ? Math.max(0, Math.min(1, parseFloat(t) / 100)) : 0.5;
    };
    return [one(parts[0]), one(parts[1])];
  }
  // ---- a picture's pixels for the core's own reading: the cover over a person (koFill), a
  // model keyed on black (keyed_), a depth picture's two textures (parallax). Measured 18 Nov (a
  // trace of the cut s03_l03 -> s04_l06: 434 ms of frames lost in four runs): each of those read
  // an img element -- and a picture the page has decoded is decoded for the GPU, so reading it as
  // pixels decoded it AGAIN, whole, on the page's own thread: 119 ms for one 2560 x 1600 render,
  // 16-48 ms for the others, all in the hold before the cut and in its first frames. Now the
  // file's bytes (the browser's cache has them: the picture itself just loaded them) are
  // decoded by the browser's decoder threads into an ImageBitmap; the page's thread only cuts
  // and copies. null = this browser cannot (no createImageBitmap): the caller reads the img element
  // as before.
  var canBitmap = typeof window.createImageBitmap === 'function' && typeof window.fetch === 'function';
  function pixels(url) {
    if (!canBitmap || typeof url !== 'string' || !FILE.test(url)) return null;
    try {
      return window.fetch(url, { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.blob(); })
        .then(function (b) { return window.createImageBitmap(b); })['catch'](function () { return null; });
    } catch (e) { return null; }
  }
  function shut(bm) { try { if (bm && typeof bm.close === 'function') bm.close(); } catch (e) { /* gone */ } }
  // `url` = the picture's file: its bitmap when the browser can (off the page's thread), else the
  // img element itself. The keying is done in slices of the page's time -- one 1520 x 850 model is
  // 1.3 million pixels, ~10 ms of work in one piece.
  function keyed_(img, url, done) {
    var job = pixels(url);
    if (job) job.then(function (bm) { if (bm) keyOn(bm, done, true); else keyOn(img, done, false); });
    else keyOn(img, done, false);
  }
  function keyOn(src, done, own) {
    try {
      var w = src.naturalWidth || src.width, hgt = src.naturalHeight || src.height, k = Math.min(1, 1600 / Math.max(w, hgt, 1));
      var c = doc.createElement('canvas');
      c.width = Math.max(1, Math.round(w * k));
      c.height = Math.max(1, Math.round(hgt * k));
      // (read back at once: a canvas kept in memory -- one on the GPU is read back through the
      // GPU process, which stops every frame of the page while it does, 18 Nov)
      var g = c.getContext('2d', { willReadFrequently: true });
      g.drawImage(src, 0, 0, c.width, c.height);
      if (own) shut(src);
      var d = g.getImageData(0, 0, c.width, c.height), p = d.data, i = 0;
      var slice = function () {
        try {
          for (var end = Math.min(p.length, i + 4 * 300000); i < end; i += 4) {
            var m = Math.max(p[i], p[i + 1], p[i + 2]);
            if (m < 4) { p[i + 3] = 0; continue; }
            var f = 255 / m;
            p[i] = Math.min(255, p[i] * f);
            p[i + 1] = Math.min(255, p[i + 1] * f);
            p[i + 2] = Math.min(255, p[i + 2] * f);
            p[i + 3] = m;
          }
          if (i < p.length) { setTimeout(slice, 0); return; }
          g.putImageData(d, 0, 0);
          c.toBlob(function (b) { done(b && window.URL ? window.URL.createObjectURL(b) : null); }, 'image/png');
        } catch (e) { done(null); }
      };
      slice();
    } catch (e) { if (own) shut(src); done(null); }
  }
  function effects(run, ctx, adopt) {
    var fx = {};
    fx.glass = function (c) { return h('div', 'cs-glass cs-panel' + cl(c)); };
    fx.console = function (c) {
      var el = h('div', 'cs-glass cs-panel cs-panel-console' + cl(c)), bar = h('div', 'cs-console-bar'), body = h('div', 'cs-panel-body');
      add(bar, h('i'), h('i'), h('i'));
      add(el, bar, body);
      return { el: el, body: body };
    };
    fx.sat = function (child, c) {
      var el = h('div', 'cs-glass cs-sat' + cl(c));
      if (isNode(child)) el.appendChild(child);
      return adopt(el, child);
    };
    // The glow behind a thing that is lit (the film's type, 18 Nov: a soft halo of the words'
    // own hue -- a drawn gradient, never a blurred shadow): `child` wrapped with its halo
    // behind it. A text or a figure has its own (`glow`); this is for anything else an act
    // lights -- an icon, a mark, a small drawing. `shade: true` = a soft dark one (a thing
    // standing on a bright picture). What wraps a keyed thing waits for its beat with it.
    fx.halo = function (child, o) {
      o = o || {};
      var el = h('span', 'cs-halo' + (o.shade ? ' cs-halo-shade' : '') + cl(o.cls));
      el.appendChild(h('i', 'cs-halo-glow'));
      if (isNode(child)) el.appendChild(child);
      return adopt(el, child);
    };
    // The way out of a text or a figure (ctx.text / ctx.figure): it cools and fades, left to
    // right, in ~280 ms -- shorter than its arrival. depart(el, false) brings it back with
    // its arrival. (The same as the class cs-leave on it.)
    fx.depart = function (el, gone) {
      if (!isNode(el) || !el.classList) return el;
      if (gone === false) el.classList.remove('cs-leave'); else el.classList.add('cs-leave');
      return el;
    };
    // A wipe by transform alone: a clip box that moves one way while what is in it moves
    // the other, so the content stands still and its edge travels across it (a scan from
    // a hologram to its model, a plan to its room) -- no clip-path is animated.
    fx.wipe = function (child, o) {
      o = o || {};
      var dir = own({ right: 1, left: 1, down: 1, up: 1 }, o.dir) ? o.dir : 'right';
      var el = h('div', 'cs-wipe cs-wipe-' + dir + cl(o.cls)), inner = h('div', 'cs-wipe-in');
      var axis = dir === 'right' || dir === 'left' ? 'X' : 'Y', sign = dir === 'right' || dir === 'down' ? -1 : 1, at = 0;
      if (isNode(child)) inner.appendChild(child);
      add(el, inner, o.edge === false ? null : h('span', 'cs-wipe-edge'));
      var put = function (f, ms, ease) {
        at = Math.max(0, Math.min(1, +f || 0));
        var rest = (1 - at) * 100, move = ms > 0 ? 'transform ' + Math.round(ms) + 'ms ' + (EASE[ease] || EASE.inout) : 'none';
        if (ms > 0) { void el.offsetWidth; }
        [[el, sign], [inner, -sign]].forEach(function (one) {
          one[0].style.transition = move;
          one[0].style.transform = 'translate' + axis + '(' + fmt(one[1] * rest) + '%)';
        });
        if (at > 0 && at < 1) el.classList.add('cs-wiping'); else if (!(ms > 0)) el.classList.remove('cs-wiping');
      };
      put(num(o.from, 0), 0);
      return { el: el,
        set: function (f) { put(f, 0); },
        to: function (f, ms, ease) {
          var time = run.instant ? 0 : num(ms, 1200);
          if (time > 0) { el.classList.add('cs-wiping'); later(run, function () { el.classList.remove('cs-wiping'); }, time + 40); }
          put(f, time, ease);
        },
        at: function () { return at; } };
    };
    // A wheel: cards round a cylinder, turned by one transform. The card at the front faces
    // the hall (class cs-front); the ones turned away are not drawn.
    fx.wheel = function (cards, o) {
      o = o || {};
      var list = (cards || []).filter(isNode), n = list.length, step = num(o.step, n ? 360 / n : 0), radius = num(o.radius, 30);
      var el = h('div', 'cs-wheel' + cl(o.cls)), ring = h('div', 'cs-wheel-ring'), slots = [], at = 0;
      list.forEach(function (card, i) {
        var slot = h('div', 'cs-wheel-card');
        slot.style.transform = 'rotateY(' + fmt(i * step) + 'deg) translateZ(' + cu(radius) + ')';
        slot.style.setProperty('--i', String(i));
        slot.appendChild(card);
        ring.appendChild(slot);
        slots.push(slot);
      });
      el.appendChild(ring);
      var put = function (i, ms) {
        at = num(i, 0);
        ring.style.transition = ms > 0 ? 'transform ' + Math.round(ms) + 'ms ' + EASE.inout : 'none';
        ring.style.transform = 'translateZ(' + cu(-radius) + ') rotateY(' + fmt(-at * step) + 'deg)';
        var front = n ? ((Math.round(at) % n) + n) % n : -1;
        slots.forEach(function (slot, k) { if (k === front) slot.classList.add('cs-front'); else slot.classList.remove('cs-front'); });
      };
      put(num(o.at, 0), 0);
      return { el: el, slots: slots,
        turn: function (i, ms) { put(i, run.instant ? 0 : num(ms, 900)); },
        at: function () { return at; } };
    };
    // A fan of tilted cards: the outer ones turned in and set back.
    fx.fan = function (cards, o) {
      o = o || {};
      var row = h('div', 'cs-fan' + cl(o.cls)), list = (cards || []).filter(isNode), n = list.length, mid = (n - 1) / 2;
      var spread = num(o.spread, 34), lift = num(o.lift, 2.2), tilt = num(o.tilt, 0);
      list.forEach(function (card, i) {
        var slot = h('div', 'cs-fan-card'), k = mid ? (i - mid) / mid : 0;          // -1 .. 1 across the fan
        slot.style.setProperty('--ry', fmt(-k * spread / 2) + 'deg');
        slot.style.setProperty('--rz', fmt(k * tilt) + 'deg');
        slot.style.setProperty('--tz', fmt(-Math.abs(k) * lift));
        slot.style.setProperty('--i', String(i));
        slot.appendChild(card);
        row.appendChild(slot);
      });
      return row;
    };
    // `child` standing in a picture: a w x h (cu) box mapped onto four corners given in %
    // of `parent` (the layer the picture is in -- so the camera moves both together).
    fx.plane = function (parent, child, quad, o) {
      o = o || {};
      var w = num(o.w, 20), hgt = num(o.h, 6), el = h('div', 'cs-plane' + cl(o.cls));
      el.style.width = cu(w);
      el.style.height = cu(hgt);
      if (isNode(child)) el.appendChild(child);
      parent.appendChild(el);
      var place = function () {
        var unit = (run.box.offsetWidth || 0) / 100, pw = parent.offsetWidth || 0, ph = parent.offsetHeight || 0;
        if (!unit || !pw || !ph || !Array.isArray(quad) || quad.length !== 4) return false;
        var m = quadMatrix(w * unit, hgt * unit, quad.map(function (p) { return [num(p && p[0], 0) / 100 * pw, num(p && p[1], 0) / 100 * ph]; }));
        if (!m) return false;
        el.style.transform = 'matrix3d(' + m.map(function (v) { return String(Math.round(v * 1e8) / 1e8); }).join(',') + ')';
        el.classList.add('cs-placed');                      // (never seen before it stands where it belongs)
        return true;
      };
      if (!place()) later(run, place, 0, true);           // (now, held or not: it is placed before it is ever seen)
      run.resizers.push(place);
      return el;
    };
    // A glowing line over a picture, drawn along its length: a route, the site's outline.
    function stroke(d, o) {
      var aspect = num(o.aspect, 16 / 9), H = 1000 / (aspect > 0 ? aspect : 16 / 9), wide = num(o.width, 0.5) * 10;
      var svg = ctx.svg('svg', { viewBox: '0 0 1000 ' + fmt(H), preserveAspectRatio: 'none', cls: 'cs-route' + cl(o.cls) });
      if (!svg) return { el: h('div', 'cs-route'), draw: function () {}, play: function () {} };
      var fill = o.close && o.fill !== false ? ctx.svg('path', { d: d, cls: 'cs-route-fill' }) : null;
      var glow = ctx.svg('path', { d: d, pathLength: 1, cls: 'cs-route-glow', 'stroke-width': fmt(wide * 3.4) });
      var line = ctx.svg('path', { d: d, pathLength: 1, cls: 'cs-route-line', 'stroke-width': fmt(wide) });
      add(svg, fill, glow, line);
      var set = function (f, ms) {
        f = Math.max(0, Math.min(1, +f || 0));
        [glow, line].forEach(function (p) {
          p.style.transition = ms > 0 ? 'stroke-dashoffset ' + Math.round(ms) + 'ms linear' : 'none';
          p.style.strokeDashoffset = fmt(1 - f);
        });
        if (f > 0) svg.classList.add('cs-drawn'); else svg.classList.remove('cs-drawn');
        if (fill) { if (f >= 1 && !(ms > 0)) fill.classList.add('cs-in'); else if (f < 1) fill.classList.remove('cs-in'); }
      };
      set(0, 0);
      return { el: svg, paths: [fill, glow, line].filter(Boolean),
        draw: function (f) { set(f, 0); },
        play: function (ms) {
          if (run.instant || !(ms > 0)) { set(1, 0); return; }
          svg.classList.add('cs-drawn');
          later(run, function () { set(1, ms); }, 30);        // (one frame at 0, so it is drawn and not just there)
          later(run, function () { if (fill) fill.classList.add('cs-in'); }, ms + 40);
        } };
    }
    function at(p, H) { return fmt(num(p && p[0], 0) * 10) + ' ' + fmt(num(p && p[1], 0) / 100 * H); }
    fx.route = function (points, o) {
      o = o || {};
      var H = 1000 / num(o.aspect, 16 / 9), d = '';
      (Array.isArray(points) ? points : []).forEach(function (p, i) { d += (i ? ' L ' : 'M ') + at(p, H); });
      return stroke(d + (o.close ? ' Z' : ''), o);
    };
    fx.arc = function (a, b, o) {
      o = o || {};
      var H = 1000 / num(o.aspect, 16 / 9), bend = num(o.bend, 0.25);
      var ax = num(a && a[0], 0) * 10, ay = num(a && a[1], 0) / 100 * H, bx = num(b && b[0], 0) * 10, by = num(b && b[1], 0) / 100 * H;
      var mx = (ax + bx) / 2 + (by - ay) * bend, my = (ay + by) / 2 - (bx - ax) * bend;      // lifted off the straight line
      return stroke('M ' + fmt(ax) + ' ' + fmt(ay) + ' Q ' + fmt(mx) + ' ' + fmt(my) + ' ' + fmt(bx) + ' ' + fmt(by), o);
    };
    // The camera: moves a layer group that fills the stage. A view is the point of the group
    // (in %) that sits at the stage's centre, a scale and a turn -- or a rectangle to fill
    // the stage with. Transform only; `to` is one transition, never a loop.
    fx.camera = function (group) {
      group.classList.add('cs-cam');
      var view = { x: 50, y: 50, scale: 1, rotate: 0 };
      var take = function (v) {
        if (!v || typeof v !== 'object') return;
        if (Array.isArray(v.rect) && v.rect.length === 4 && v.rect[2] > 0 && v.rect[3] > 0) {
          view.x = v.rect[0] + v.rect[2] / 2;
          view.y = v.rect[1] + v.rect[3] / 2;
          view.scale = v.cover ? Math.max(100 / v.rect[2], 100 / v.rect[3]) : Math.min(100 / v.rect[2], 100 / v.rect[3]);
        }
        ['x', 'y', 'scale', 'rotate'].forEach(function (k) { view[k] = num(v[k], view[k]); });
        view.scale = Math.max(0.05, Math.min(40, view.scale));
      };
      var put = function (ms, ease) {
        if (ms > 0) { void group.offsetWidth; }              // (the move starts from where it is now)
        group.style.transition = ms > 0 ? 'transform ' + Math.round(ms) + 'ms ' + (EASE[ease] || EASE.move) : 'none';
        group.style.transform = 'translate(50%, 50%) rotate(' + fmt(view.rotate) + 'deg) scale(' + fmt(view.scale)
          + ') translate(' + fmt(-view.x) + '%, ' + fmt(-view.y) + '%)';
      };
      put(0);
      var cam = {
        el: group,
        view: function () { return { x: view.x, y: view.y, scale: view.scale, rotate: view.rotate }; },
        set: function (v) { take(v); put(0); return cam; },
        to: function (v, ms, ease) { take(v); put(run.instant ? 0 : num(ms, 1200), ease); return cam; },
        push: function (k, ms) { return cam.to({ scale: view.scale * num(k, 1.15) }, ms, 'push'); },
        pull: function (k, ms) { return cam.to({ scale: view.scale / num(k, 1.15) }, ms, 'inout'); },
        pan: function (dx, dy, ms) { return cam.to({ x: view.x + num(dx, 0), y: view.y + num(dy, 0) }, ms, 'inout'); },
        drift: function (ms, k) { return cam.to({ scale: view.scale * num(k, 1.06) }, num(ms, 14000), 'linear'); }
      };
      return cam;
    };
    // The far side of a portal: `child` in a rectangle (%) of `group`. Push the camera into
    // the rectangle (cam.to({rect})) and the screen lands on it.
    fx.inset = function (group, rect, child, c) {
      var el = h('div', 'cs-inset' + cl(c)), r = Array.isArray(rect) ? rect : [0, 0, 100, 100];
      el.style.left = fmt(num(r[0], 0)) + '%';
      el.style.top = fmt(num(r[1], 0)) + '%';
      el.style.width = fmt(num(r[2], 100)) + '%';
      el.style.height = fmt(num(r[3], 100)) + '%';
      if (isNode(child)) el.appendChild(child);
      group.appendChild(el);
      return el;
    };
    // A portal: picture B in a rectangle (or a polygon of it) of picture A; open() pushes the
    // camera into the rectangle until B fills the stage, then calls landed() -- where the act
    // hands over to B's own scene, framed the same way (cover), so the cut cannot be seen.
    // `rect` in % of the camera group; a 16:9 rectangle has w = h. `shape`: a polygon in % of
    // the rectangle (static clip). `show`: B visible from the start (a window), not only as
    // the camera arrives.
    fx.portal = function (cam, group, rect, child, o) {
      o = o || {};
      var el = fx.inset(group, rect, child, 'cs-portal' + (o.show ? ' cs-open' : '') + cl(o.cls));
      if (Array.isArray(o.shape) && o.shape.length >= 3) {
        el.style.clipPath = 'polygon(' + o.shape.map(function (p) {
          return fmt(num(p && p[0], 0)) + '% ' + fmt(num(p && p[1], 0)) + '%';
        }).join(', ') + ')';
      }
      adopt(el, child);
      return { el: el,
        open: function (ms, landed) {
          var time = run.instant ? 0 : num(ms, 1100);
          el.classList.add('cs-open');
          cam.to({ rect: rect, cover: true }, time, 'push');
          var go = function () { if (typeof landed === 'function') { try { landed(); } catch (e) { fail(run, e); } } };
          if (time > 0) later(run, go, time + 20); else go();
        } };
    };
    // A whip: one layer shoots out, the next shoots in, streaks across the stage between.
    // (Whip layers that carry no transform of their own: the animation takes it over.)
    fx.whip = function (outEl, inEl, o) {
      o = o || {};
      var dir = own({ left: 1, right: 1, up: 1, down: 1 }, o.dir) ? o.dir : 'left', ms = run.instant ? 0 : num(o.ms, 420);
      var names = ['cs-whip-out', 'cs-whip-in', 'cs-whip-left', 'cs-whip-right', 'cs-whip-up', 'cs-whip-down'];
      var rest = function () {
        [outEl, inEl].forEach(function (e) { if (isNode(e)) names.forEach(function (n) { e.classList.remove(n); }); });
        if (isNode(outEl)) outEl.classList.add('cs-whipped');
        if (isNode(inEl)) inEl.classList.remove('cs-whipped');
      };
      if (!(ms > 0)) { rest(); return; }
      var streak = h('div', 'cs-whip-streak cs-whip-' + dir);
      [streak, outEl, inEl].forEach(function (e) { if (isNode(e)) e.style.setProperty('--whip', Math.round(ms) + 'ms'); });
      run.box.appendChild(streak);
      if (isNode(outEl)) { outEl.classList.add('cs-whip-out'); outEl.classList.add('cs-whip-' + dir); }
      if (isNode(inEl)) { inEl.classList.remove('cs-whipped'); inEl.classList.add('cs-whip-in'); inEl.classList.add('cs-whip-' + dir); }
      later(run, function () {
        if (streak.parentNode) streak.parentNode.removeChild(streak);
        rest();
      }, ms + 60);
    };
    // Typed text: the letters are all in the page from the start (the line never reflows),
    // each shown in turn, a caret after the last one. `textEl` = ctx.text(key, {split: 'chars'}).
    fx.typed = function (textEl) {
      var none = { el: textEl, set: function () {}, play: function () {} };
      if (!isNode(textEl)) return none;
      var chars = textEl.__chars || [], n = chars.length, caret = h('span', 'cs-caret2'), shown = -1;
      textEl.classList.add('cs-typed');
      // a piece of her line types no further than her voice has got (review 18 Nov: a punchline
      // typed on a wall clock was read before she said it); settled or joined late = all of it
      var said = textEl.__said || null;
      var voice = function () {
        if (!said || run.settled || run.instant) return 1;
        var f = typeof run.last === 'number' ? run.last : 0;
        return Math.max(0, Math.min(1, (f - said[0]) / (said[1] - said[0])));
      };
      var set = function (f) {
        var k = Math.round(Math.max(0, Math.min(1, +f || 0, voice())) * n);
        if (k === shown) return;
        shown = k;
        for (var i = 0; i < n; i++) { if (i < k) chars[i].classList.add('cs-on'); else chars[i].classList.remove('cs-on'); }
        var after = k > 0 ? chars[k - 1] : null, home = (after || chars[0] || {}).parentNode;
        if (home && typeof home.insertBefore === 'function') home.insertBefore(caret, after ? after.nextSibling : chars[0]);
        if (k >= n) textEl.classList.add('cs-typed-done'); else textEl.classList.remove('cs-typed-done');
      };
      set(run.instant ? 1 : 0);
      // One play at a time (review 18 Nov: s05_l11 plays its title on two beats -- the second
      // play started again from no letters, the title dropped from 12 letters to 0 and was typed
      // again; and a play still running undid settle's set(1)). A new play stops the one before
      // and goes on from the letters already shown, at the same pace; set() from outside stops a
      // play. Neither ever types ahead of her voice (set() holds that).
      var running = null;
      var halt = function () { if (running) { var s = running; running = null; s(); } };
      return { el: textEl,
        set: function (f) { halt(); set(f); },
        play: function (ms) {
          halt();
          if (run.instant || !(ms > 0)) { set(1); return; }
          var from = n ? Math.max(0, shown) / n : 1, t0 = null, stop = null;
          stop = running = ctx.frame(function (t) {
            if (t0 === null) t0 = t;
            var f = Math.min(1, from + (t - t0) / ms);
            set(f);
            if (f >= 1 && voice() >= 1) { if (running === stop) running = null; stop(); }   // (waits on her voice, not a clock)
          });
        } };
    };
    fx.parallax = function (pic, o) { return parallax(run, ctx, pic, o || {}, adopt); };
    return fx;
  }

  // A picture with depth. With a depth map and WebGL: one quad, each pixel moved by how near
  // it is, as the view shifts -- a still that moves like a camera in its room. Without
  // either: the same picture moved by transform. The plain picture is ALWAYS in the page
  // under the canvas, and the canvas shows only once it has drawn a frame: never blank.
  // (`fy`: 1 for textures uploaded flipped -- an img element with UNPACK_FLIP_Y -- and -1 for an
  // ImageBitmap, which WebGL uploads as it is, top row first: the quad reads it the other way up)
  var PLX_VERT = 'attribute vec2 p; varying vec2 v; uniform float fy; void main() { v = vec2(p.x * .5 + .5, p.y * fy * .5 + .5); gl_Position = vec4(p, 0., 1.); }';
  var PLX_FRAG = 'precision mediump float; varying vec2 v; uniform sampler2D img, dep; uniform vec2 off, fit; '
    + 'uniform float zoom, focus, near; '
    + 'float d(vec2 u) { float x = texture2D(dep, u).r; return mix(1. - x, x, near); } '
    + 'void main() { vec2 u = (v - .5) * fit / zoom + .5; vec2 q = u + off * (d(u) - focus); '
    + 'q = u + off * (d(q) - focus); gl_FragColor = texture2D(img, clamp(q, 0., 1.)); }';
  function parallax(run, ctx, pic, o, adopt) {
    var el = h('div', 'cs-plx' + cl(o.cls)), nothing = function () {};
    if (!pic || !isNode(pic.el)) return { el: el, set: nothing, drift: nothing, gl: function () { return false; } };
    var amount = num(o.amount, 1.2), zoom = Math.max(1, num(o.zoom, 1.06)), x = 0, y = 0, live = null, dirty = true, stopDrift = null;
    pic.el.classList.add('cs-plx-pic');
    el.appendChild(pic.el);
    adopt(el, pic.el);                             // (the canvas shows the picture: it waits for the picture's beat too)
    var plain = function () {
      pic.el.style.transform = 'translate3d(' + cu(-x * amount) + ', ' + cu(-y * amount) + ', 0) scale(' + fmt(zoom) + ')';
    };
    var draw = function () {
      if (!live) { plain(); return; }
      if (!dirty) return;
      dirty = false;
      var gl = live.gl;
      gl.uniform2f(live.off, x * amount / 100, -y * amount / 100 * live.aspect * live.fy);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
    var set = function (nx, ny) {
      nx = Math.max(-1, Math.min(1, num(nx, 0)));
      ny = Math.max(-1, Math.min(1, num(ny, 0)));
      if (nx === x && ny === y) return;
      x = nx;
      y = ny;
      dirty = true;
      draw();
    };
    plain();
    if (pic.depth && pic.url && pic.fit !== 'contain' && !pic.alpha && !pic.keepOut && run.glAsked < GL_MOST) {
      run.glAsked++;
      try { glStart(); } catch (e) { live = null; }
    }
    function glStart() {
      if (typeof doc.createElement !== 'function' || typeof window.Image !== 'function') return;
      var canvas = doc.createElement('canvas');
      if (!canvas || typeof canvas.getContext !== 'function') return;
      var gl = canvas.getContext('webgl', { alpha: false, antialias: false, depth: false, stencil: false });
      if (!gl) return;
      canvas.className = 'cs-plx-gl';
      // (the cut waits for this too, like the picture itself: two textures going up to the GPU
      // in the middle of a cut were frames lost in it)
      var warm = { key: typeof pic.el.getAttribute === 'function' ? pic.el.getAttribute('data-k') : null, done: false, el: el };
      var given = function () { if (!warm.done) { warm.done = true; warmed(run); } };
      run.warm.push(warm);
      // photo, depth: ImageBitmaps (flipped = false) or img elements (true)
      var both = function (photo, depth, flipped) {
        if (run.dead) { if (!flipped) { shut(photo); shut(depth); } given(); return; }
        try {
          var bw = el.offsetWidth || 1280, bh = el.offsetHeight || 720, scale = Math.min(1, 1920 / bw);
          canvas.width = Math.max(2, Math.round(bw * scale));
          canvas.height = Math.max(2, Math.round(bh * scale));
          var prog = gl.createProgram();
          [[gl.VERTEX_SHADER, PLX_VERT], [gl.FRAGMENT_SHADER, PLX_FRAG]].forEach(function (one) {
            var sh = gl.createShader(one[0]);
            gl.shaderSource(sh, one[1]);
            gl.compileShader(sh);
            if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error('shader');
            gl.attachShader(prog, sh);
          });
          gl.linkProgram(prog);
          if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error('program');
          gl.useProgram(prog);
          gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
          gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
          var at = gl.getAttribLocation(prog, 'p');
          gl.enableVertexAttribArray(at);
          gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, flipped ? 1 : 0);
          [[photo, 'img'], [depth, 'dep']].forEach(function (one, unit) {
            gl.activeTexture(gl.TEXTURE0 + unit);
            gl.bindTexture(gl.TEXTURE_2D, gl.createTexture());
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, one[0]);
            gl.uniform1i(gl.getUniformLocation(prog, one[1]), unit);
          });
          var pw = photo.naturalWidth || photo.width || 16, ph = photo.naturalHeight || photo.height || 9;
          if (!flipped) { shut(photo); shut(depth); }                 // (on the GPU now: the CPU copies go)
          var stageAspect = canvas.width / canvas.height, shape = pw / ph;
          gl.uniform2f(gl.getUniformLocation(prog, 'fit'), Math.min(1, stageAspect / shape), Math.min(1, shape / stageAspect));
          gl.uniform1f(gl.getUniformLocation(prog, 'zoom'), zoom);
          gl.uniform1f(gl.getUniformLocation(prog, 'focus'), num(o.focus, 0.5));
          gl.uniform1f(gl.getUniformLocation(prog, 'near'), (o.near || pic.near) === 'dark' ? 0 : 1);
          gl.uniform1f(gl.getUniformLocation(prog, 'fy'), flipped ? 1 : -1);
          gl.viewport(0, 0, canvas.width, canvas.height);
          live = { gl: gl, off: gl.getUniformLocation(prog, 'off'), aspect: stageAspect, fy: flipped ? 1 : -1 };
          dirty = true;
          draw();
          pic.el.style.transform = 'scale(' + fmt(zoom) + ')';      // the plain picture rests under it
          el.appendChild(canvas);
          if (!run.revealed) canvas.classList.add('cs-held');
          canvas.classList.add('cs-on');
          run.gl++;
          // (a GL error is asked for a moment later, not now: getError waits for the GPU to finish
          // everything sent -- here, two textures going up -- with the page's thread stopped)
          setTimeout(function () {
            try {
              if (live && live.gl === gl && gl.getError() !== gl.NO_ERROR) {
                live = null;
                run.gl--;
                canvas.classList.remove('cs-on');
                plain();
                warn('parallax: WebGL gave up, the plain picture stays', 'draw');
              }
            } catch (e) { /* the context is gone: its own event says so */ }
          }, 400);
        } catch (e) { live = null; warn('parallax: WebGL gave up, the plain picture stays', e); }
        given();
      };
      if (typeof canvas.addEventListener === 'function') {
        canvas.addEventListener('webglcontextlost', function (ev) {
          if (ev && ev.preventDefault) ev.preventDefault();
          if (live) run.gl--;
          live = null;
          canvas.classList.remove('cs-on');
          plain();
        });
      }
      run.cleanups.push(function () {
        live = null;
        var lose = gl.getExtension && gl.getExtension('WEBGL_lose_context');
        if (lose) lose.loseContext();
      });
      // the two files as bitmaps, decoded off the page's thread (pixels()) -- or, where the
      // browser cannot, as img elements decoded before they are handed over (an undecoded
      // picture is decoded by texImage2D itself, on the page's own thread)
      var viaImages = function () {
        var photo = new window.Image(), depth = new window.Image(), got = 0;
        var one = function () { if (++got === 2) both(photo, depth, true); };
        var decoded = function (im) {
          return function () { if (typeof im.decode === 'function') im.decode().then(one, one); else one(); };
        };
        photo.onerror = given;
        depth.onerror = given;
        photo.onload = decoded(photo);
        depth.onload = decoded(depth);
        photo.src = pic.url;
        depth.src = pic.depth;
      };
      var jobs = [pixels(pic.url), pixels(pic.depth)];
      if (jobs[0] && jobs[1]) {
        Promise.all(jobs).then(function (bms) {
          if (bms[0] && bms[1]) both(bms[0], bms[1], false);
          else { shut(bms[0]); shut(bms[1]); if (!run.dead) viaImages(); else given(); }
        });
      } else viaImages();
    }
    return { el: el, set: set, gl: function () { return !!live; },
      drift: function (on) {
        if (stopDrift) { stopDrift(); stopDrift = null; }
        if (on === false) return;
        stopDrift = ctx.frame(function (t) { set(Math.sin(t / 5200), Math.sin(t / 7300 + 1.3) * 0.7); });
      } };
  }

  // ---- an act's run ----
  var raf = typeof window.requestAnimationFrame === 'function' ? function (fn) { return window.requestAnimationFrame(fn); } : null;
  var rafId = 0, rafLast = 0, creditTicks = 0;
  // Every act slide with a picture on the screen: the one that is up, the one it is still held
  // under (the frame that holds until the next is ready), and those seen going down. An
  // outgoing shot does not freeze before its cut: its frames go on until it is gone.
  function onStage() {
    var runs = leaving.slice();
    if (cur && cur.over && runs.indexOf(cur.over) < 0) runs.push(cur.over);
    if (cur && cur.act) runs.push(cur);
    return runs;
  }
  function loop(t) {
    rafId = 0;
    var runs = onStage().filter(function (run) { return run.frames && run.frames.length && !run.gone; });
    if (!runs.length) return;
    var dt = rafLast ? Math.min(100, t - rafLast) : 16;
    rafLast = t;
    runs.forEach(function (run) {
      run.frames.slice().forEach(function (fn) {
        if (run.gone || run.failed) return;
        try { fn(t, dt); } catch (e) { fail(run, e); }
      });
    });
    if (creditTicks++ % 8 === 0) creditTick();
    if (onStage().some(function (run) { return run.frames && run.frames.length && !run.gone; })) rafId = raf(loop);
  }
  function kick() {
    if (rafId || !raf) return;
    if (!onStage().some(function (run) { return run.frames && run.frames.length && !run.gone; })) return;   // (nothing to run: no frame asked for)
    rafLast = 0;
    rafId = raf(loop);
  }
  function dimStage(level, ms) {
    if (!bgEl) return;
    bgEl.style.transition = ms > 0 ? 'opacity ' + Math.round(ms) + 'ms ease' : 'none';
    bgEl.style.opacity = level >= 1 ? '' : fmt(level);
  }
  function backdrop(mode) {
    if (!stage) return;
    if (bgEl) { bgEl.style.transition = ''; bgEl.style.opacity = ''; }
    stage.classList.remove('cs-bg-dim');
    stage.classList.remove('cs-bg-off');
    if (mode === 'dim' || mode === 'off') stage.classList.add('cs-bg-' + mode);
  }
  // ---- a map's credit (ODbL, CLAUDE.md "เครดิต OSM บนภาพต้องอยู่"): the core's own line at
  // the stage's corner, on while a credited picture shows. Not an act's word: no act can
  // reach it (it is not in the act's slide; the scan refuses parentNode and the like) and
  // nothing of an act lies above it.
  function shows(run, e) {
    if (!e || !e.classList || !e.classList.contains('cs-loaded')) return false;
    if (e.isConnected === false) return false;
    for (var x = e; x && x !== run.root; x = x.parentNode) {
      if (x.classList && (x.classList.contains('cs-await') || x.classList.contains('cs-whipped'))) return false;
    }
    if (typeof e.checkVisibility === 'function' && !e.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    if (typeof e.getBoundingClientRect === 'function' && run.box && typeof run.box.getBoundingClientRect === 'function') {
      var r = e.getBoundingClientRect(), s = run.box.getBoundingClientRect(), win = s;
      // (a shot going down under the sheet of light: its root is the window that closes from
      // the left while the shot in it stands still -- what is seen of the map is what is left
      // in that window. 18 Nov: the map's line stayed up over the next shot until the map's
      // slide left the page, ~0.9 s after the light had taken the map away)
      if (run.dead && run.root && typeof run.root.getBoundingClientRect === 'function') {
        var o = run.root.getBoundingClientRect();
        win = { left: Math.max(s.left, o.left), right: Math.min(s.right, o.right), top: Math.max(s.top, o.top), bottom: Math.min(s.bottom, o.bottom) };
      }
      var w = Math.min(r.right, win.right) - Math.max(r.left, win.left), hh = Math.min(r.bottom, win.bottom) - Math.max(r.top, win.top);
      if (!(w > 0 && hh > 0) || w * hh < 0.03 * (s.width * s.height)) return false;
    }
    return true;
  }
  // (the credit follows what is SEEN: the slide that is up -- not one still held unseen under
  // the frame before it -- then the frame it is held under, then a map still going down: a
  // map is never on the screen a moment without its line)
  function creditTick() {
    if (!creditEl) return;
    var text = '', runs = [];
    if (cur && cur.act && cur.revealed) runs.push(cur);
    if (cur && cur.over) runs.push(cur.over);
    runs = runs.concat(leaving);
    for (var r = 0; r < runs.length && !text; r++) {
      var run = runs[r];
      if (run.failed || run.gone || !run.credited) continue;      // (a template's slide has none)
      for (var i = 0; i < run.credited.length; i++) if (shows(run, run.credited[i].el)) { text = run.credited[i].text; break; }
    }
    if (text) { if (creditEl.textContent !== text) creditEl.textContent = text; creditEl.classList.add('cs-on'); }
    else creditEl.classList.remove('cs-on');
  }
  function creditSoon(run) {
    [60, 450, 900, 1600, 2600].forEach(function (ms) { later(run, creditTick, ms); });
  }
  function quiet(run) {
    if (run.inst && typeof run.inst.clear === 'function') { try { run.inst.clear(); } catch (e) { /* going anyway */ } }
    run.inst = null;
    run.frames = [];
    run.resizers = [];
    (run.cleanups || []).forEach(function (fn) { try { fn(); } catch (e) { /* going anyway */ } });
    run.cleanups = [];
    (run.videos || []).forEach(function (v) {
      try { v.pause(); v.removeAttribute('src'); if (typeof v.load === 'function') v.load(); } catch (e) { /* gone */ }
    });
    run.videos = [];
    (run.blobs || []).forEach(function (u) { try { window.URL.revokeObjectURL(u); } catch (e) { /* gone */ } });
    run.blobs = [];
  }
  // An act that throws (or whose file did not come) is taken down to the plain stage: the
  // subtitle goes on, and a half-built frame is never left on the screen.
  function fail(run, why) {
    if (run.failed) return;
    run.failed = true;
    warn('act ' + run.act + ' is down', why);
    quiet(run);
    if (run.box && run.box.parentNode) run.box.parentNode.removeChild(run.box);
    run.bg = 'full';
    if (cur === run && !run.revealed) bringIn(run);       // (held: the plain stage comes up now, not the old frame for ever)
    else if (cur === run) backdrop('full');
  }
  function fire(run, item, late) {
    if (item.fired || run.failed) return;
    item.fired = true;
    item.keys.forEach(function (k) {
      if (!run.wait[k]) return;
      run.wait[k] = false;                                   // its first beat: on the screen from here
      (run.keyed[k] || []).forEach(function (e) {
        if (late) noEntrance(e);                             // (no entrance for what is simply there)
        e.classList.remove('cs-await');
      });
      run.shown.push(k);
    });
    run.did.push(item.beat['do']);
    if (run.inst && typeof run.inst.beat === 'function') {
      try { run.inst.beat(item.beat, !!late); } catch (e) { fail(run, e); }
    }
    if (run.credited.length) creditSoon(run);
  }
  function catchUp(run, late) {
    for (var i = 0; i < run.beats.length; i++) {
      if (run.beats[i].at <= run.last) fire(run, run.beats[i], late); else break;
    }
  }
  function settleAct(run) {
    run.beats.forEach(function (item) { fire(run, item, true); });
    if (!run.failed && run.inst && typeof run.inst.settle === 'function') {
      try { run.inst.settle(); } catch (e) { fail(run, e); }
    }
  }
  function startAct(run) {
    // (the act's code came after her voice began, or the screen joined mid-line: no entrances)
    var late = run.instant || run.last > 0 || run.settled;
    if (late && !run.instant) {
      run.instant = true;
      run.root.classList.add('cs-instant');
    }
    try { run.inst = ACTS[run.act](context(run, late)) || {}; } catch (e) { run.inst = null; fail(run, e); return; }
    run.started = true;
    if (run.credited.length) creditSoon(run);
    if (!late) {
      // (it goes live as it comes up -- now, if it is up already; else when its pictures are
      // in or the hold runs out: warmed())
      run.wantsLive = true;
      if (run.revealed) goLive(run); else warmed(run);
      return;
    }
    noEntranceOpen(run.box);
    run.root.classList.add('cs-live');
    catchUp(run, true);
    if (run.settled) settleAct(run);
    later(run, function () { run.instant = false; run.root.classList.remove('cs-instant'); }, 400, true);
    if (!run.revealed) warmed(run);
  }
  // The opening's entrances (the act hangs them on .cs-live) start one frame after the slide
  // is up, so each starts from its first frame; then the act's own arrive(), if it has one.
  function goLive(run) {
    if (!run.wantsLive) return;
    run.wantsLive = false;
    later(run, function () {
      run.root.classList.add('cs-live');
      if (!run.failed && run.inst && typeof run.inst.arrive === 'function') {
        try { run.inst.arrive({ kind: run.cut, ms: cutMs(run.cut) }); } catch (e) { fail(run, e); }
      }
    }, 40, true);
  }
  // ---- the hold: may the slide that waits under the frame before it come up? Its act has
  // built it (or failed), and every picture of its opening and of its first beat is decoded
  // -- and its depth picture, if it has one, is on the GPU. Called whenever one of those
  // lands; the hold's own timer (showAct) brings it up at CUT.hold whatever is missing.
  function warmed(run) {
    if (run.revealed || run.dead || cur !== run) return;
    if (!run.started && !run.failed) return;
    for (var i = 0; i < run.warm.length; i++) {
      var w = run.warm[i];
      if (w.done || (w.key && run.wait[w.key] && !run.firstKeys[w.key])) continue;     // (in, or a later beat's)
      // (of the opening, but nothing of it can be seen as things stand -- the act brings it
      // in itself, with the opening's entrance or later: it is waited for a shorter time)
      if (run.soft && !(w.key && run.firstKeys[w.key]) && w.el && typeof w.el.checkVisibility === 'function'
          && !w.el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
      return;
    }
    // ... and it has been DRAWN once, unseen, under the frame before: three frames of the page
    // (the third begins only after the first, with the new slide in it, is rastered and on
    // the GPU). Measured 18 Nov (a trace of a cut, s05_l06): a slide that came up the moment
    // it was built was rastered -- its shaders compiled, 50 ms in one flush -- in the first
    // frames of its own cut: five frames lost in the sheet. Where no frames are drawn (a
    // hidden page) there is nothing to wait for.
    if (run.drawn) return;
    run.drawn = true;
    var up = function () { bringIn(run); };
    if (!raf || doc.visibilityState !== 'visible') { up(); return; }
    raf(function () { raf(function () { raf(up); }); });
  }
  // The slide comes up: the stage is its own now, the sheet of light crosses (a cut) or it
  // dissolves in over the frame before (a blend), that frame goes, its timers start counting,
  // and it goes live.
  function bringIn(run) {
    if (run.revealed || run.dead || cur !== run) return;
    run.revealed = true;
    var old = run.over, was = stage.classList.contains('cs-bg-off') ? 0 : (stage.classList.contains('cs-bg-dim') ? 1 : 2);
    run.over = null;
    stage.classList.add('cs-up');
    doc.body.classList.add('cs-on');
    // (a darker stage than the frame before had: that frame keeps its own until the sheet has
    // crossed it; a brighter one is there behind the new shot from its first frame)
    // (a dissolve: the stage's light changes WITH it, over its whole length -- the shot going
    // down is still there, under the new one, and must not lose its floor in half the time)
    var want = run.bg === 'off' ? 0 : (run.bg === 'dim' ? 1 : 2), blend = run.cut === 'blend' && !!old && !calm;
    var set = function () {
      if (cur !== run) return;
      backdrop(run.bg);
      if (blend && bgEl) bgEl.style.transition = 'opacity ' + CUT.blend + 'ms var(--cs-ease-sine)';
      if (run.light < 1) dimStage(run.light, blend ? CUT.blend : 0);
    };
    if (run.cut === 'cut' && old && want < was && !calm) later(run, set, CUT.into, true); else set();
    if (run.cut === 'cut' && !calm) { run.root.setAttribute('data-cut', 'in'); gate(CUT.into); }
    else if (blend) { run.root.setAttribute('data-cut', 'blend'); run.blending = true; later(run, function () { run.blending = false; }, CUT.blend, true); }
    else if (run.cut === 'join') run.root.setAttribute('data-cut', 'join');
    else run.root.removeAttribute('data-cut');
    if (old) retire(old, run.cut === 'join' ? 'join' : (run.cut === 'cut' && !calm ? 'cut' : (blend ? 'blend' : 'fade')));
    idleUpdate();
    creditTick();
    var held = run.held || [];
    run.held = null;
    held.forEach(function (one) { later(run, one[0], one[1]); });
    goLive(run);
    kick();
  }
  // The sheet of light: one element of the stage, its crossing started again for each cut.
  function gate(ms) {
    if (!gateEl) return;
    gateEl.classList.remove('cs-go');
    if (ms === CUT.down) gateEl.classList.add('cs-down'); else gateEl.classList.remove('cs-down');
    void gateEl.offsetWidth;                                // (the same element again: its animation starts over)
    gateEl.classList.add('cs-go');
  }
  // Does the line going down hand on INTO this slide: the same act, a handover() that gave
  // something, and the same mode -- the mode it handed on (`mode` of its state; the acts with
  // modes, emma_console and smarthome_brand, hand theirs on), or else its own slide's
  // `options.mode` -- as this slide's `options.mode`. An act that hands nothing on
  // (living_photos) always cuts: its next line builds a new opening.
  function continues(prev, slide) {
    if (!prev || prev.act !== slide.act || !prev.state || typeof prev.state !== 'object') return false;
    var mode = function (s) { return s && s.options && typeof s.options.mode === 'string' ? s.options.mode : null; };
    var was = typeof prev.state.mode === 'string' ? prev.state.mode : mode(prev.slide);
    return was === mode(slide);
  }
  // The scene of a cue: "s04" of "s04_l06" (and of "s05_backup_quote" -> "s05"); null when
  // there is no cue id to read.
  function sceneOf(cue) {
    var m = typeof cue === 'string' ? /^s(\d{1,3})_/i.exec(cue) : null;
    return m ? 's' + Number(m[1]) : null;
  }
  function cutMs(kind) { return kind === 'cut' ? CUT.into : (kind === 'blend' ? CUT.blend : 0); }
  // What the act slide on the screen gives the next one (see ctx.prev).
  function handed(old) {
    if (!old || !old.act || old.failed || !old.started) return null;
    var state = null;
    try { if (old.inst && typeof old.inst.handover === 'function') state = old.inst.handover(); } catch (e) { state = null; }
    return { act: old.act, slide: old.slide, settled: old.settled, state: state === undefined ? null : state };
  }
  function showAct(slide, opts) {
    if (typeof slide.act !== 'string' || !ACT_NAME.test(slide.act)) { clear(); return false; }
    ensure();
    // (the frame that is SEEN now: the slide that is up -- or, when that one is itself still
    // held unseen, the one it is held under: a second press inside a hold)
    var old = cur, seen = old && old.act && !old.revealed ? old.over : old;
    var run = { slide: slide, act: slide.act, parts: {}, fired: {}, shown: [], did: [], timers: [], dead: false, settled: false,
                pictures: 0, loaded: 0, instant: !!(opts && opts.late), root: null, box: null, beats: [],
                wait: {}, keyed: {}, frames: [], resizers: [], cleanups: [], videos: [], last: 0, started: false,
                failed: false, inst: null, bg: 'full', gl: 0, glAsked: 0, light: 1, prev: handed(seen),
                credited: [], blobs: [], ticks: 0,
                warm: [], firstKeys: {}, held: [], soft: false, drawn: false, revealed: false, over: seen || null, cut: 'cut', wantsLive: false, gone: false };
    run.root = h('div', 'cs-slide cs-t-act cs-act-' + slide.act + (run.instant ? ' cs-instant' : ''));
    run.box = h('div', 'cs-act-stage');
    run.root.appendChild(run.box);
    (Array.isArray(slide.beats) ? slide.beats : []).forEach(function (b) {
      if (!b || typeof b['do'] !== 'string' || typeof b.at !== 'number' || !(b.at >= 0 && b.at <= 1)) return;
      var keys = typeof b.key === 'string' ? [b.key] : (Array.isArray(b.key) ? b.key.filter(function (k) { return typeof k === 'string'; }) : []);
      run.beats.push({ at: b.at, beat: b, keys: keys, fired: false });
      keys.forEach(function (k) { run.wait[k] = true; });
    });
    run.beats.sort(function (a, b) { return a.at - b.at; });
    // (what the first beat brings in: the cut waits for those pictures too -- a picture must
    // not appear a moment after its cut)
    if (run.beats.length) run.beats[0].keys.forEach(function (k) { run.firstKeys[k] = true; });
    // Line to line (ceremony-show.css "line to line"): an act going on into its next line is
    // a JOIN -- it builds its opening from ctx.prev, the frames the same, so the new one is
    // there at once and the old only fades under it. Anything else is a CUT: the sheet of
    // light crosses. Shown late: no entrance at all. (Review 18 Nov: decided by the act's
    // NAME, s04_l06 -> s04_l09, s06_l07 -> s07_l02 and s08_l16 -> s09_l02 -- one act in
    // another mode -- popped in with no move. It is the old act's handover() that says it
    // goes on.)
    // ... and a cut inside one scene is a dissolve (review 19 Nov): the sheet of light marks a
    // new scene only. The scene is the cue's (sNN_ of its id); a slide shown with no cue -- or
    // after none -- is taken as a new scene.
    run.scene = sceneOf(opts && opts.cue);
    run.cut = run.instant ? 'late' : (continues(run.prev, slide) ? 'join'
      : (run.scene && seen && seen.act && seen.scene === run.scene ? 'blend' : 'cut'));
    // (an attribute, not a class: the root's class list is the act's contract -- cs-live,
    // cs-instant. "hold": in the page, built and drawn, not yet seen -- the frame before holds)
    if (!run.instant) run.root.setAttribute('data-cut', 'hold');
    if (old && old !== seen) retire(old);                   // (a slide that never came up: gone at once)
    cur = run;
    stage.insertBefore(run.root, idleEl || gateEl || creditEl);
    if (run.instant) bringIn(run);                          // (a screen that joined mid-line: the state, now)
    else {
      setTimeout(function () { run.soft = true; warmed(run); }, CUT.soft);
      setTimeout(function () { if (!run.dead) bringIn(run); }, CUT.hold);
    }
    loadAct(slide.act, slide.v, slide.css === true, function (ok) {
      if (run.dead) return;
      if (!ok || !ACTS[run.act]) { fail(run, 'its file did not load'); return; }
      startAct(run);
    });
    return true;
  }

  function ensure() {
    if (stage) return;
    stage = h('div', 'cs-stage');
    var bg = h('div', 'cs-bg');
    // The launch-film stage (owner 17 Nov, two reference clips: "แบบนี้"): near-black with
    // electric-blue light -- a beam and a glowing horizon arc behind everything, a floor
    // grid running to the horizon. Painted once, moved by transform only. (Owner 20 Nov, review
    // PDF p.1, arrows on the four dim ghost panels: "ไม่ชอบ ดูแปลกๆเป็นกรอบๆ" -- they are gone.)
    // Owner 18 Nov, the whole preview watched: "พื้นหลังก็แบบนั้นหรอ ไม่มีลูกเล่นอะไรใช่ไหม" --
    // it looked painted. Now it lives, quietly (ceremony-show.css "the living stage"): an
    // aurora drifting in the sky, two slow searchlights, a light running round the horizon,
    // the floor grid flowing towards us, dust in the light at two depths. All transform and
    // opacity on layers painted once; paused with the rest when no slide or clip is up.
    // (23 Nov, the event notebook's integrated GPU: held stages at 40-47 fps. A mask or a clip-path
    // on a layer that MOVES is drawn through an offscreen pass of its own every frame -- seven of
    // them here, about four screens of extra fill a frame. So each moving light carries its look
    // on a still child (`i`) and moves it by its own transform/opacity: the mask is then painted
    // into the layer once, the frame draws one plain quad. The dust's two depths share ONE mask
    // (one pass instead of two). Same picture; ceremony-show.css "the living stage".)
    var arc = h('span', 'cs-arc'), grid = h('span', 'cs-grid');
    arc.appendChild(add(h('i', 'cs-arc-run'), h('i')));
    grid.appendChild(h('i', 'cs-grid-flow'));
    add(bg, h('span', 'cs-aurora cs-aurora-a'), h('span', 'cs-aurora cs-aurora-b'), add(h('span', 'cs-beam'), h('i')),
      add(h('span', 'cs-search cs-search-a'), h('i')), add(h('span', 'cs-search cs-search-b'), h('i')), arc, grid,
      add(h('span', 'cs-motes'), h('i', 'cs-motes-far'), h('i', 'cs-motes-near')));
    stage.appendChild(bg);
    try {
      calm = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { calm = false; }
    if (calm) stage.classList.add('cs-calm');
    bgEl = bg;
    // the sheet of light of a cut (ceremony-show.css "line to line"): over the slides, under the credit
    gateEl = h('div', 'cs-gate');
    gateEl.appendChild(h('i'));
    stage.appendChild(gateEl);
    // (one place for the cut's times: the stylesheet plays them, the engine takes the slides away by them)
    stage.style.setProperty('--cs-cut', CUT.into + 'ms');
    stage.style.setProperty('--cs-cut-down', CUT.down + 'ms');
    stage.style.setProperty('--cs-blend', CUT.blend + 'ms');
    creditEl = h('div', 'cs-credit');
    stage.appendChild(creditEl);
    doc.body.appendChild(stage);
    warmFont();
  }
  // ---- the opening: the project's own logo clip over the stage's floor and horizon
  // (owner's design for the brand part, 17 Nov; only in the ceremony's show mode -- the
  // stage is not drawn in any other). Muted. Owner 18 Nov night, with the new clip: "เอาเปิด
  // อันแรก ส่วนตอนรอระหว่างบทไม่ต้องมีเลย ... ไม่อยากให้มีคั่น" -- it OPENS the ceremony, once
  // per load of this screen, and is never an interstitial: between two lines the line before's
  // slide stays (it settles, the page never takes it down) until the next comes in from it.
  // The screen only falls idle again after a stop or a call ending; then the clip shows its
  // last frame, still (no second opening). The address comes with the acts' list.
  var idleEl = null, idleVideo = null, idleOn = false, idleOpened = false;
  function idleSetup(m) {
    if (idleEl || !m || typeof m.src !== 'string' || !FILE.test(m.src) || !doc.createElement) return;
    ensure();
    idleEl = h('div', 'cs-idle');
    var v = doc.createElement('video');
    v.muted = true;
    v.defaultMuted = true;
    v.loop = false;
    v.preload = 'auto';
    v.setAttribute('muted', '');
    v.setAttribute('playsinline', '');
    if (typeof v.addEventListener === 'function') v.addEventListener('volumechange', function () { if (!v.muted) v.muted = true; });
    v.src = m.src;
    // (the clip's feathered edges and its `screen` blend are on a still wrapper, its moves -- the
    // breathing hold, the burst of the debut -- on the video inside: a mask on a layer that scales is
    // drawn again at every new scale, ~470 tiles a second on the held logo, 23 Nov)
    idleEl.appendChild(add(h('span', 'cs-idle-m'), v));
    idleVideo = v;
    stage.insertBefore(idleEl, rvFlash || gateEl || creditEl);
    var root = doc.documentElement;
    if (root && typeof window.MutationObserver === 'function') {
      new window.MutationObserver(idleUpdate).observe(root, { attributes: true, attributeFilter: ['class'] });
    }
    idleUpdate();
  }
  function idleUpdate() {
    if (!idleEl) return;
    var root = doc.documentElement;
    // (no slide is SEEN: none is up -- or the one that is has not come up yet and holds over nothing)
    var none = !cur || (!!cur.act && !cur.revealed && !cur.over);
    var want = none && !!root && !!root.classList && root.classList.contains('mirror-show');
    if (want === idleOn) return;
    idleOn = want;
    if (want) {
      stage.classList.add('cs-idle-on');
      if (rv) { /* the debut plays the clip on its own clock */ }
      else if (!idleOpened && !stage.classList.contains('cs-black')) {   // (dark: no logo -- held unplayed)
        idleOpened = true;
        try { idleVideo.currentTime = 0; var p = idleVideo.play(); if (p && p['catch']) p['catch'](function () {}); } catch (e) { /* not ready */ }
      } else {
        // (the opening was played: its last frame, held -- a play() here would start it over)
        try { var d = idleVideo.duration; if (d > 0 && isFinite(d)) idleVideo.currentTime = Math.max(0, d - 0.05); } catch (e) { /* not ready */ }
      }
    } else {
      stage.classList.remove('cs-idle-on');
      try { idleVideo.pause(); } catch (e) { /* not playing */ }
    }
  }
  // ---- the debut (card 1, app/ceremony_reveal.py; owner 23 Nov: "soundeffect ให้มันเล้าใจ ดุดัน" -> "ฟิว
  // เหมือนคอนเสิร์ต" -> "ซาวน์นับถอยหลัง 10 9 8 ขึ้นจอด้วยโดยเมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มีซาวน์ แล้วหุ่นก็
  // เปิดตัว" -> "แนวนอน.mp4 เพิ่มในตอนเปิดตัว" -> after listening: "เอาเวลานับออก") ----
  // The build: the music on the dark stage, a concert's light rising with it -- beams sweeping up from
  // the floor and in from the sides, pulsing on its beats (the message's `beats`), haze -- then BLACK at
  // 0, everything (the music breaks there too); on its hit a flash, a burst of rays, the logo (the
  // opening clip itself, started so its own flare is lit on the hit) bursting in, and the owner's
  // glitter wave rising behind it (muted, under the logo: the clip's own `screen` blend is the stage's);
  // when the wave has drained away the dark lifts off the living stage and the clip holds its last
  // frame -- the frame the first line's slide comes in from. A mix with a counting voice brings its
  // `numbers`: each drawn huge, a ring going out from it (kept; the owner's debut has none). The sound
  // plays here (aloud where this screen plays her voice) and is followed (revealClock). Transform and
  // opacity only, every glow a drawn gradient. A press again starts it over; a stop takes it down at
  // once (revealStop); a slide coming up ends it.
  //   CeremonyShow.reveal({numbers: [], beats: [s, ...], zero_s, impact_s, clip_at_s, settle_s, length_s,
  //                        src, video: {src, at_s, length_s}, sound}) / .revealStop()
  var rv = null, rvEl = null, rvBuild = null, rvBeams = null, rvCount = null, rvWave = null, rvWaveVideo = null,
      rvFlash = null, rvAudio = null, rvAudioSrc = null, rvRaf = 0, rvFading = [];
  // a slide coming up after the hit: the debut dissolves under it this long (re-review 23 Nov: it was cut
  // in one frame -- the wave, the veil and the ringing hit gone at once)
  var RV_DISSOLVE_MS = 650;
  // the beams: [where along the floor (% of the width), from the side (0 = the floor), the sweep's two
  // angles (deg), its seconds, its delay, white core (1) or blue]
  var RV_BEAMS = [[6, 0, -32, 8, 2.9, 0, 0], [20, 0, -20, 14, 3.6, -1.1, 1], [36, 0, -12, 22, 3.1, -0.4, 0],
                  [64, 0, 12, -22, 3.3, -1.7, 0], [80, 0, 20, -14, 3.7, -0.6, 1], [94, 0, 32, -8, 2.8, -2.1, 0],
                  [0, 1, 34, 56, 4.2, -0.9, 1], [100, 1, -34, -56, 4.4, -2.6, 1]];
  // (asked for when the screen opens: the press starts the sound at once and the wave without a wait)
  // `dark` (reveal.json; owner 25 Nov ~02:15 "ตอนเปิดทุกอย่างจะดับ โลโก้ไม่ต้องขึ้นก็ได้"): the stage the
  // idle screen and the debut stand on is plain black from now on -- no living stage, no logo, no light
  // show, no wave (ceremony-show.css "the dark opening"); the music, its black and its hit are heard and
  // the robot walks out on the hit as ever. A line's slide brings the stage back with it.
  function revealPrepare(m) {
    if (!m || typeof m !== 'object') return;
    // `dark: 'build'` (owner 25 Nov ~04:25: "ฉากแรกตอนปิดไฟจอก็ควรดำ ถ้าจะขึ้นก็ต้องตรงสปอตไลท์นะ"): the same black
    // before the press and through the build and its black; the lit debut comes up on the HIT (the spotlight).
    if (m.dark === true || m.dark === 'build') {
      ensure();
      stage.classList.add('cs-black');
      if (idleVideo && !rv) { try { idleVideo.pause(); } catch (e) { /* not playing */ } }
    } else if (stage) stage.classList.remove('cs-black');
    if (typeof m.src === 'string' && FILE.test(m.src) && typeof window.Audio === 'function' && !(rvAudio && rvAudioSrc === m.src)) {
      try {
        var a = new window.Audio();
        a.preload = 'auto';
        a.src = m.src;
        if (typeof a.load === 'function') a.load();
        rvAudio = a;
        rvAudioSrc = m.src;
      } catch (e) { rvAudio = null; rvAudioSrc = null; }
    }
    if (m.dark !== true && m.video && typeof m.video.src === 'string' && FILE.test(m.video.src)) {
      revealParts();
      if (rvWaveVideo.getAttribute('src') !== m.video.src) {
        rvWaveVideo.setAttribute('src', m.video.src);
        try { rvWaveVideo.load(); } catch (e) { /* not ready */ }
      }
    }
  }
  function revealParts() {
    if (rvEl) return;
    ensure();
    rvEl = h('div', 'cs-dbt');
    rvBuild = h('span', 'cs-dbt-build');
    rvBeams = h('span', 'cs-dbt-beams');
    RV_BEAMS.forEach(function (b) {
      var beam = h('span', 'cs-dbt-beam' + (b[1] ? ' cs-dbt-side' : '') + (b[6] ? ' cs-dbt-white' : ''));
      beam.style.left = b[0] + '%';
      beam.style.setProperty('--a0', b[2] + 'deg');
      beam.style.setProperty('--a1', b[3] + 'deg');
      beam.style.setProperty('--d', b[4] + 's');
      beam.style.setProperty('--w', b[5] + 's');
      add(beam, h('i', 'cs-dbt-wide'), h('i', 'cs-dbt-core'));
      rvBeams.appendChild(beam);
    });
    add(rvBuild, h('span', 'cs-dbt-haze cs-dbt-haze-a'), h('span', 'cs-dbt-haze cs-dbt-haze-b'), rvBeams, h('span', 'cs-dbt-floor'));
    // the owner's wave: over the dark, under the light and the logo (its black is the dark's)
    rvWave = h('span', 'cs-dbt-wave');
    rvWaveVideo = h('video');
    rvWaveVideo.muted = true;
    rvWaveVideo.defaultMuted = true;
    rvWaveVideo.setAttribute('muted', '');
    rvWaveVideo.setAttribute('playsinline', '');
    rvWaveVideo.preload = 'auto';
    rvWave.appendChild(rvWaveVideo);
    rvCount = h('span', 'cs-dbt-count');
    var rays = h('span', 'cs-dbt-rays');
    for (var i = 0; i < 16; i++) {
      var ray = h('i');
      ray.style.setProperty('--r', (i * 22.5 + (i % 2 ? 6 : 0)) + 'deg');
      rays.appendChild(ray);
    }
    add(rvEl, h('span', 'cs-dbt-veil'), rvWave, rvBuild, rvCount, h('span', 'cs-dbt-glow'), rays);
    stage.insertBefore(rvEl, idleEl || gateEl || creditEl);
    // (the flash is over the logo: it is the light the logo bursts in from)
    rvFlash = h('div', 'cs-dbt-flash');
    stage.insertBefore(rvFlash, gateEl || creditEl);
  }
  function revealClass(on) {
    ['cs-dbt-on', 'cs-dbt-dark', 'cs-dbt-hit', 'cs-dbt-settle'].forEach(function (c) { if (on.indexOf(c) < 0) stage.classList.remove(c); });
    on.forEach(function (c) { stage.classList.add(c); });
  }
  // a countdown as the message gives it: [[10, s], [9, s], ... [1, s]] -- counting down to 1, in order;
  // [] = none (the owner's debut since 23 Nov: "เอาเวลานับออก" -- no digit is drawn)
  function revealNumbers(list, zero) {
    if (!Array.isArray(list) || list.length > 20) return null;
    if (!list.length) return [];
    var out = [];
    for (var i = 0; i < list.length; i++) {
      var p = list[i], n = p && p[0], at = p && num(p[1], -1);
      if (n !== list.length - i || !(at >= 0) || (out.length && at <= out[out.length - 1][1])) return null;
      out.push([n, at]);
    }
    return out[out.length - 1][1] < zero ? out : null;
  }
  // the build's beats (the light pulses on them): in order, before 0; none = none
  function revealBeats(list, zero) {
    if (!Array.isArray(list)) return [];
    var out = [];
    for (var i = 0; i < list.length && i < 64; i++) {
      var at = num(list[i], -1);
      if (!(at >= 0) || at >= zero || (out.length && at <= out[out.length - 1])) return [];
      out.push(at);
    }
    return out;
  }
  function revealStart(m) {
    if (!m || typeof m !== 'object') return false;
    var zero = num(m.zero_s, 0), impact = num(m.impact_s, 0), length = num(m.length_s, 0);
    var numbers = revealNumbers(m.numbers, zero);
    if (!numbers || !(zero < impact && impact < length && length < 90)) return false;
    var settle = Math.max(impact, Math.min(length, num(m.settle_s, impact + 1.4)));
    revealStop();                                           // (a press again: from the top)
    revealFadeCancel();                                     // (a dissolve still going: the element is ours again)
    if (cur) clear();                                       // (a line's slide up: the debut takes the stage)
    revealParts();
    revealPrepare(m);
    // (the sound's element is kept to the end whatever it does: revealEnd pauses and rewinds it)
    var a = typeof m.src === 'string' && rvAudio && rvAudioSrc === m.src ? rvAudio : null;
    var black = m.dark === true, untilHit = m.dark === 'build';
    var v = !black && m.video && typeof m.video.src === 'string' && FILE.test(m.video.src) &&
            rvWaveVideo.getAttribute('src') === m.video.src ? { at: Math.max(impact, num(m.video.at_s, impact)), started: false } : null;
    rv = { numbers: numbers, beats: revealBeats(m.beats, zero), beat: -1, zero: zero, impact: impact, settle: settle, length: length,
           clipAt: Math.max(0, Math.min(impact, num(m.clip_at_s, impact))), el: a, lost: !a, started: false,
           heardT: -1, heardAt: now(), seekAt: -1e9, video: v, asked: now(),
           clip: black, untilHit: untilHit, phase: 'build', seq: m.seq, shown: -1, level: -1 };   // (dark: the logo clip never plays)
    stage.classList.remove('cs-dbt-done');
    rvWave.classList.remove('cs-on');
    rvCount.textContent = '';
    revealClass(['cs-dbt-on']);
    rvBuild.style.opacity = '0';
    // the logo is the opening clip: held back, at its start, until its moment
    idleOpened = true;
    if (idleVideo) { try { idleVideo.pause(); idleVideo.currentTime = 0; } catch (e) { /* not ready */ } }
    try { rvWaveVideo.pause(); rvWaveVideo.currentTime = 0; } catch (e) { /* not ready */ }
    if (a) {
      try {
        a.muted = m.sound === false;
        a.volume = 1;
        a.currentTime = 0;
        var p = a.play();
        // (refused: the page's clock goes on -- the element stays ours, to be paused at the end)
        if (p && p['catch']) p['catch'](function () { if (rv && rv.el === a) rv.lost = true; });
      } catch (e) { rv.lost = true; }
    }
    // (frames only: the room's screen is a kiosk, always shown -- a hidden tab catches up when it is back)
    if (raf) rvRaf = raf(revealLoop);
    idleUpdate();
    return true;
  }
  function now() { return window.performance.now(); }
  function revealLoop() {
    rvRaf = 0;
    if (!rv) return;
    revealTick();
    if (rv && raf) rvRaf = raf(revealLoop);
  }
  // Where the debut is, in seconds of its timeline. The page's own clock from the moment the message
  // came is what the server counted from (app/ceremony_reveal.py: the walk goes out on its hit) and is
  // the one that always runs; the sound follows it. While the sound plays and its time moves, the light
  // follows the SOUND (it is what the room hears) -- as long as it is within RV_DRIFT of the page:
  //   * not started yet: the page's clock (the countdown never waits for a sound);
  //   * started late, or a hiccup put it more than RV_DRIFT off: sent on to where the page is (at most
  //     once a second) -- the hit stays where the server counted it;
  //   * stalled (playing, its time not moving for RV_STALL s -- a file that stopped arriving): paused,
  //     given up, the page's clock to the end. Review 23 Nov: the light froze on 8-7 for 16 s while the
  //     server's hit passed;
  //   * refused, ended: the page's clock.
  // It ends by itself every way: the page's clock always reaches the end.
  var RV_DRIFT = 0.5, RV_STALL = 0.5;
  function revealClock() {
    var r = rv, a = r.el, stamp = now(), page = (stamp - r.asked) / 1000;
    if (!a || r.lost) return page;
    var t = 0, playing = false;
    try { t = a.currentTime; playing = !a.paused && !a.ended; } catch (e) { r.lost = true; return page; }
    if (t !== r.heardT) { r.heardT = t; r.heardAt = stamp; }
    if (!r.started) {
      if (!(playing && t > 0)) {
        // (not started yet: one that comes before the hit is let in where the page is -- the hit is its
        // reason; by the hit it has not come: given up, never to start into the dark after it)
        if (page >= r.impact) { r.lost = true; try { a.pause(); } catch (e) { /* gone */ } }
        return page;
      }
      r.started = true;
    }
    if (a.ended) return page;
    if (playing && stamp - r.heardAt > RV_STALL * 1000) {
      r.lost = true;
      try { a.pause(); } catch (e) { /* gone */ }
      return page;
    }
    if (Math.abs(t - page) > RV_DRIFT) {
      if (stamp - r.seekAt > 1000 && page < r.length) {
        r.seekAt = stamp;
        try { a.currentTime = page; } catch (e) { /* not seekable: it plays where it is */ }
      }
      return page;
    }
    return t;
  }
  // one number up: the digit, a ring going out from it, the light behind it pulsing (and stronger on
  // the last three); late (a hidden tab, a clock step) = into its own animation by as much
  function revealNumber(k, t) {
    var r = rv, n = r.numbers[k][0], next = k + 1 < r.numbers.length ? r.numbers[k + 1][1] : r.zero;
    var dur = Math.max(0.3, next - r.numbers[k][1]), late = Math.max(0, t - r.numbers[k][1]);
    var last = n <= 3 ? ' cs-dbt-last' : '';
    var halo = h('span', 'cs-dbt-halo' + last), ring = h('span', 'cs-dbt-ring' + last), digit = h('span', 'cs-dbt-num' + last, String(n));
    [halo, ring, digit].forEach(function (e) {
      e.style.animationDuration = (e === ring ? Math.min(0.9, dur) : dur) + 's';
      if (late > 0) e.style.animationDelay = (-late) + 's';
    });
    rvCount.textContent = '';
    add(rvCount, halo, ring, digit);
    revealPulse(k, dur);
  }
  // the beams flare on a beat (two names, alternated: each beat starts the pulse over)
  function revealPulse(k, dur) {
    rvBeams.classList.remove('cs-dbt-pulse-a', 'cs-dbt-pulse-b');
    rvBeams.classList.add(k % 2 ? 'cs-dbt-pulse-b' : 'cs-dbt-pulse-a');
    rvBeams.style.animationDuration = Math.min(0.9, Math.max(0.3, dur)) + 's';
  }
  function revealTick() {
    var r = rv;
    if (!r) return;
    var t = revealClock();
    if (r.phase === 'build') {
      // a countdown's number now: the last one whose time has come
      var k = -1;
      for (var i = 0; i < r.numbers.length && r.numbers[i][1] <= t; i++) k = i;
      if (k !== r.shown && k >= 0 && t < r.zero) { r.shown = k; r.shownPage = (now() - r.asked) / 1000; revealNumber(k, t); }
      // the music's beat now: the beams pulse on it
      var b = -1;
      for (var j = 0; j < r.beats.length && r.beats[j] <= t; j++) b = j;
      if (b !== r.beat && b >= 0 && t < r.zero && !r.numbers.length) {
        r.beat = b;
        revealPulse(b, (b + 1 < r.beats.length ? r.beats[b + 1] : r.zero) - r.beats[b]);
      }
      // the light rises toward 0 (an opacity, a few steps a second at most): by the numbers, or by the time
      var f = r.numbers.length ? (k < 0 ? -1 : (k + 1) / r.numbers.length) : Math.min(1, t / r.zero);
      var level = Math.round((f < 0 ? 0.18 : 0.3 + 0.7 * Math.pow(f, 1.2)) * 20) / 20;
      if (level !== r.level) { r.level = level; rvBuild.style.opacity = String(level); }
    }
    if (!r.clip && t >= r.clipAt && idleVideo) {
      r.clip = true;
      try { idleVideo.currentTime = Math.max(0, t - r.clipAt); var p = idleVideo.play(); if (p && p['catch']) p['catch'](function () {}); } catch (e) { /* not ready */ }
    }
    if (r.phase === 'build' && t >= r.zero) {
      // 0: everything dark
      r.phase = 'dark';
      rvCount.textContent = '';
      rvBeams.classList.remove('cs-dbt-pulse-a', 'cs-dbt-pulse-b');
      revealClass(['cs-dbt-on', 'cs-dbt-dark']);
    }
    if (r.phase === 'dark' && t >= r.impact) {
      r.phase = 'hit';
      if (r.untilHit) stage.classList.remove('cs-black');  // (the spotlight: the lit debut from here)
      rvBuild.style.opacity = '1';
      revealClass(['cs-dbt-on', 'cs-dbt-hit']);
    }
    if (r.video && !r.video.started && t >= r.video.at && r.phase !== 'build' && r.phase !== 'dark') {
      r.video.started = true;
      try {
        rvWaveVideo.currentTime = Math.max(0, t - r.video.at);
        var q = rvWaveVideo.play();
        if (q && q['catch']) q['catch'](function () {});
      } catch (e) { /* not ready: the logo is there without it */ }
      rvWave.classList.add('cs-on');
    }
    if (r.phase === 'hit' && t >= r.settle) {
      r.phase = 'settle';
      revealClass(['cs-dbt-on', 'cs-dbt-hit', 'cs-dbt-settle']);
    }
    if (t >= r.length || (now() - r.asked) / 1000 >= r.length + 2) revealEnd(false);
  }
  // Over (`stopped`: taken down now): the light show and the wave go, the logo stays on the clip's held
  // last frame -- the idle stage the first line's slide comes in from.
  // Over. `stopped`: taken down now -- at once, sound and all. `soft` (a slide coming up after the hit):
  // the light, the wave and the ringing hit dissolve under it (RV_DISSOLVE_MS) -- the same end, eased.
  // Either way the sound's element is paused and rewound (a play() still pending -- a sound slow to come --
  // must not start into the dark hall after a stop) and the logo is left on the clip's held last frame.
  function revealEnd(stopped, soft) {
    var r = rv;
    if (!r) return;
    rv = null;
    if (rvRaf && typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(rvRaf);
    rvRaf = 0;
    rvCount.textContent = '';
    rvBeams.classList.remove('cs-dbt-pulse-a', 'cs-dbt-pulse-b');
    stage.classList.add('cs-dbt-done');
    if (soft) {
      // (the phase's own looks stay put while the whole fades: the veil must not snap back to black)
      stage.classList.add('cs-dbt-fade');
      stage.classList.remove('cs-dbt-on');
      var a = r.el, vol = a ? a.volume : 0, steps = 6;
      for (var i = 1; i <= steps; i++) {
        (function (k) {
          rvFading.push(setTimeout(function () { if (a) { try { a.volume = Math.max(0, vol * (1 - k / steps)); } catch (e) { /* gone */ } } },
                                   RV_DISSOLVE_MS * k / steps));
        })(i);
      }
      rvFading.push(setTimeout(function () { rvFading = []; revealSettled(a); }, RV_DISSOLVE_MS + 20));
      return;
    }
    if (stopped) stage.classList.add('cs-dbt-cut');
    revealSettled(r.el);
    if (stopped) setTimeout(function () { if (stage) stage.classList.remove('cs-dbt-cut'); }, 60);
  }
  function revealSettled(a) {
    if (a) { try { a.pause(); a.currentTime = 0; a.volume = 1; } catch (e) { /* gone */ } }
    try { rvWaveVideo.pause(); } catch (e) { /* gone */ }
    rvWave.classList.remove('cs-on');
    stage.classList.remove('cs-dbt-fade');
    revealClass([]);
    if (idleVideo) {
      try {
        idleVideo.pause();
        var d = idleVideo.duration;
        if (d > 0 && isFinite(d)) idleVideo.currentTime = Math.max(0, d - 0.05);
      } catch (e) { /* not ready */ }
    }
    idleUpdate();
  }
  // a dissolve still going when a stop or a new press comes: finished now
  function revealFadeCancel() {
    if (!rvFading.length) return;
    rvFading.forEach(function (id) { clearTimeout(id); });
    rvFading = [];
    revealSettled(rvAudio);
  }
  function revealStop() {
    var was = !!rv;
    revealEnd(true);
    revealFadeCancel();
    return was;
  }
  function revealState() {
    if (!rv) return null;
    var w = rv.video && rv.video.started ? { playing: !rvWaveVideo.paused, t: Math.round(rvWaveVideo.currentTime * 100) / 100 } : null;
    var shown = rv.phase === 'build' && rv.shown >= 0 ? rv.numbers[rv.shown][0] : null;
    return { phase: rv.phase, seq: rv.seq === undefined ? null : rv.seq, sound: !!rv.el && !rv.lost && !rv.el.muted,
             playing: !!rv.el && !rv.el.paused, t: Math.round(revealClock() * 100) / 100, number: shown, wave: w,
             // (the page's own clock, and where it was when the number now came up: what the sound did to the light)
             page: Math.round((now() - rv.asked) / 10) / 100, number_page: shown === null ? null : Math.round(rv.shownPage * 100) / 100 };
  }
  // (a slide still held under the frame before it: its timers count from the moment it comes
  // up -- an opening's choreography is not played out unseen. `now` = the core's own, at once.)
  function later(run, fn, ms, now) {
    if (run.held && !now) { run.held.push([fn, ms]); return; }
    var id = setTimeout(function () { if (!run.dead) fn(); }, ms);
    run.timers.push(id);
  }
  // A figure lands; it never counts. Picture review 17 Nov: "around 2,000 square metres" was
  // on the screen on its way to 2,500 -- a wrong figure a guest can photograph, dimmed or
  // not. The project's rule (the story engine's countStep): no value on the way that reads
  // as one. Here the only value a figure ever shows is its own.
  function land(num) {
    num.classList.add('cs-landed');
  }
  function reveal(run, key) {
    if (run.fired[key]) return;
    run.fired[key] = true;
    run.shown.push(key);
    if (key === 'pick') {
      if (run.optionRow && run.pick !== null && run.options[run.pick]) {
        run.optionRow.classList.add('cs-picking');
        run.options[run.pick].classList.add('cs-picked');
      }
      return;
    }
    (run.parts[key] || []).forEach(function (e) {
      e.classList.add('cs-in');
      if (e.__num) land(e.__num);
    });
    if (key === 'end' && run.ender) run.ender.classList.add('cs-ended');
    // a new picture of a sequence is up: once it has faded in, the ones under it stop
    // moving and go (six drifting layers under the one that shows would be work for nothing)
    var seq = run.shots || [], at = -1;
    for (var i = 0; i < seq.length; i++) if (seq[i].key === key) at = i;
    if (at > 0) {
      // (the picture before's own figure goes at once: two numbers must not share the place)
      for (var g = 0; g < at; g++) {
        if (seq[g].fig) seq[g].fig.classList.add('cs-gone');
        // ... and its caption: two captions cross-fading read as one ghosted line
        seq[g].el.classList.add('cs-capoff');
        // ... and the panel itself swings aside: the cut is a camera move, not a dissolve
        seq[g].el.classList.add('cs-past');
      }
      // a film keeps the panel just before as a dim one at the side (depth); older ones go
      var keep = key.indexOf('shot:') === 0 ? 1 : 0;
      var under = function () { for (var j = 0; j < at - keep; j++) seq[j].el.classList.add('cs-under'); };
      if (run.instant) under(); else later(run, under, 900);
    }
  }
  // A slide goes down. `how` (an act's slide): 'cut' = under the next shot, which the sheet of
  // light uncovers over it -- it is pushed on ahead of the light and goes when it is covered;
  // 'join' = under the same frame: it only fades; 'down' = to the logo: the sheet takes it
  // away; else it fades. Its timers stop now; its frames go on while it is still seen (an
  // outgoing shot does not freeze), and it lets go of what it holds when it is gone.
  function retire(run, how) {
    run.dead = true;
    run.timers.forEach(function (id) { clearTimeout(id); });
    run.held = null;
    var gone = function () {
      run.gone = true;
      leaving = leaving.filter(function (x) { return x !== run; });
      quiet(run);                                 // (an act lets go of what it holds)
      if (run.root.parentNode) run.root.parentNode.removeChild(run.root);
      creditTick();                               // (a map that was still going down: its credit goes with it)
    };
    if (run.act && !run.revealed) { gone(); return; }      // (never seen: nothing to play out)
    var ms = 700;
    if (run.act && (how === 'cut' || how === 'down') && !calm) {
      run.root.setAttribute('data-cut', how === 'cut' ? 'out' : 'down');
      ms = (how === 'cut' ? CUT.into : CUT.down) + 80;
    } else if (run.act && how === 'blend') {
      // (under the next line of its scene: whole until that one covers it, pushing on, then
      // gone -- see "blend" at CUT. One still dissolving in itself -- a second press inside a
      // second -- goes on coming in under the new one: swapped to its going-out, it would jump
      // from half-seen to whole)
      if (!run.blending) run.root.setAttribute('data-cut', 'under');
      ms = CUT.blend + 80;
    } else if (run.act && how === 'join') run.root.setAttribute('data-cut', 'join');   // (under the same frame: it only fades)
    else if (run.act) run.root.setAttribute('data-cut', 'fade');
    run.root.classList.add('cs-out');
    if (run.act) leaving.push(run);
    // (a map going down: its line goes when the light has taken the map, not when the slide
    // leaves the page -- looked at as the window closes, whatever else is ticking)
    if (run.act && run.credited && run.credited.length) {
      var look = function () { if (!run.gone) { creditTick(); setTimeout(look, 60); } };
      setTimeout(look, 60);
    }
    // It leaves the page when its window has closed: the closing is an animation, and an
    // animation starts with the first frame drawn after it is asked for -- a frame that can come
    // late in a cut, with a new shot to draw (18 Nov, a page under load: up to a quarter of a
    // second). Taken away on the clock alone, its last tenth went out at once. So at the time it
    // should be over, a closing still running is waited for (a little longer at most).
    setTimeout(function () {
      var going = [];
      try {
        if (typeof run.root.getAnimations === 'function') {
          going = run.root.getAnimations().filter(function (a) { return a.playState === 'running' || a.playState === 'pending'; });
        }
      } catch (e) { going = []; }
      if (!going.length || typeof Promise !== 'function') { gone(); return; }
      var over = false, end = function () { if (!over) { over = true; gone(); } };
      setTimeout(end, 500);
      Promise.all(going.map(function (a) { return a.finished; })).then(end, end);
    }, ms);
  }

  function show(slide, opts) {
    // (after the hit it dissolves under the slide; before it, the slide takes the stage at once)
    if (rv) revealEnd(true, rv.phase === 'hit' || rv.phase === 'settle');
    // (the debut's held-logo look ends with it: the idle stage's own comes back for the evening)
    if (stage) stage.classList.remove('cs-dbt-done');
    if (slide && typeof slide === 'object' && slide.template === 'act') return showAct(slide, opts);
    if (!slide || typeof slide !== 'object' || !TEMPLATES[slide.template]) { clear(); return false; }
    ensure();
    backdrop('full');
    var run = { slide: slide, parts: {}, fired: {}, shown: [], timers: [], dead: false, settled: false,
                pictures: 0, loaded: 0, revealed: true,
                instant: !!(opts && opts.late), root: null, beats: [] };
    try {
      run.root = h('div', 'cs-slide cs-t-' + slide.template + (run.instant ? ' cs-instant' : ''));
      // a dimmed picture behind the words (any of the text templates)
      if (slide.backdrop) {
        var back = h('div', 'cs-backdrop');
        back.appendChild(picture(run, slide.backdrop, back));
        run.root.appendChild(back);
      }
      var body = TEMPLATES[slide.template](run, slide);
      if (PICTURE_LED[slide.template]) run.root.appendChild(body);
      else {
        // words live in a glass console panel floating in the stage (no flat slide)
        var panel = h('div', 'cs-console cs-glass');
        var bar = h('div', 'cs-console-bar');
        add(bar, h('i'), h('i'), h('i'));
        add(panel, bar, body);
        run.root.appendChild(panel);
      }
    } catch (e) { clear(); return false; }        // a slide this engine cannot draw: the backdrop
    var gated = {};
    (Array.isArray(slide.beats) ? slide.beats : []).forEach(function (b) {
      if (!b || typeof b.show !== 'string' || typeof b.at !== 'number' || !(b.at >= 0 && b.at <= 1)) return;
      run.beats.push({ at: b.at, show: b.show });
      gated[b.show] = true;
    });
    run.beats.sort(function (a, b) { return a.at - b.at; });
    var old = cur;
    cur = run;
    stage.insertBefore(run.root, idleEl || gateEl || creditEl);
    stage.classList.add('cs-up');
    doc.body.classList.add('cs-on');
    if (old) down(old, 'fade');
    creditTick();
    idleUpdate();
    var open = function () {
      run.root.classList.add('cs-live');           // the camera arrives: the slide flies in from depth
      Object.keys(run.parts).forEach(function (k) { if (!gated[k]) reveal(run, k); });
    };
    // (one frame after it is in the page, so the opening parts animate in)
    if (run.instant) {
      open();
      later(run, function () { run.instant = false; run.root.classList.remove('cs-instant'); }, 400);
    } else later(run, open, 40);
    return true;
  }
  function progress(f) {
    var run = cur;
    if (!run || typeof f !== 'number' || !(f >= 0)) return;
    if (run.act) {
      run.last = Math.min(1, f);
      if (!run.started || run.failed) return;          // (its code is still coming: startAct catches up)
      catchUp(run, run.instant);
      if (!run.failed && run.inst && typeof run.inst.progress === 'function') {
        try { run.inst.progress(run.last); } catch (e) { fail(run, e); }
      }
      if (run.credited.length && (run.ticks++ % 3) === 0) creditTick();         // (a few rectangles: cheap)
      return;
    }
    for (var i = 0; i < run.beats.length; i++) {
      if (run.beats[i].at <= f) reveal(run, run.beats[i].show); else break;
    }
  }
  function settle() {
    var run = cur;
    if (!run) return;
    run.settled = true;
    if (run.act) { if (run.started) { settleAct(run); if (run.credited.length) creditSoon(run); } return; }
    Object.keys(run.parts).forEach(function (k) { reveal(run, k); });
    run.beats.forEach(function (b) { reveal(run, b.show); });
  }
  // The slide that is up goes down -- and, when it is itself still held unseen, the frame it
  // was held under with it (that one is what is on the screen).
  function down(run, how) {
    var under = run.act && !run.revealed ? run.over : null;
    run.over = null;
    retire(run, how);
    if (under) retire(under, how);
    return !!((run.act && run.revealed) || (under && under.act));     // (an act's slide was seen going)
  }
  function clear() {
    // (to the logo, or the bare stage: the sheet of light takes the line away -- the exit of
    // the film's one cut, a third shorter than its entrance)
    if (cur && down(cur, 'down') && !calm) gate(CUT.down);
    cur = null;
    backdrop('full');
    if (stage) stage.classList.remove('cs-up');
    if (doc) doc.body.classList.remove('cs-on');
    creditTick();
    idleUpdate();
  }
  function state() {
    // (`reveal`: only while the debut plays)
    if (!cur) return rv ? { up: false, idle: idleOn, reveal: revealState() } : { up: false, idle: idleOn };
    var out = { up: true, template: cur.slide.template, shown: cur.shown.slice(), settled: cur.settled,
                instant: cur.instant, pictures: cur.pictures, loaded: cur.loaded };
    if (cur.act) {
      out.act = cur.act;
      out.started = cur.started;
      out.failed = cur.failed;
      out.did = cur.did.slice();
      out.waiting = Object.keys(cur.wait).filter(function (k) { return cur.wait[k]; });
      out.frames = cur.frames.length;
      out.gl = cur.gl;                                        // parallax pictures drawn by WebGL now
      out.credit = !!creditEl && creditEl.classList.contains('cs-on') ? creditEl.textContent : null;
    }
    if (rv) out.reveal = revealState();
    return out;
  }
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('resize', function () {
      if (cur && cur.resizers) cur.resizers.forEach(function (fn) { try { fn(); } catch (e) { /* next resize */ } });
    });
  }

  window.CeremonyShow = { show: show, progress: progress, settle: settle, clear: clear, state: state,
                          icons: Object.keys(ICONS), templates: Object.keys(TEMPLATES),
                          register: register, preload: preload, acts: function () { return Object.keys(ACTS); },
                          quad: quadMatrix, reveal: revealStart, revealStop: revealStop };
  preload();
})();
