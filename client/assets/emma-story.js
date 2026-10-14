/* Emma story engine — the robot face's Interactive Presentation Engine.

   A guest asks; Emma answers by voice and this draws the same story on the
   portrait screen, beat by beat WITH her voice, then rests on an overview the
   guest can tap (owner, 12 Oct 2026). docs/emma-story-engine.md has the format.

   What reaches here is the server's (app/stories.py): the template, the
   pictures by pinned URL, every label and number, the cue words of each line.
   The model only chose WHICH story. The guest's own words never reach this
   file -- it is fed Emma's transcript (to hear where she is) and the times of
   the audio the page scheduled, nothing else.

   Sync. The model's text arrives several times faster than her voice plays,
   so a beat never fires on text: it fires when the AUDIO that carries its cue
   word is playing. The page reports every audio chunk it schedules (start,
   duration on the AudioContext clock); a character of her transcript is
   mapped onto that audio (`charTime`), and a beat is due when its cue is
   reached in PLAYED audio. No cue heard ~4 s after the last beat (she
   paraphrased) = the beat fires anyway, earlier if the line lengths say she
   must be there by now. No audio yet = nothing moves.

   Interrupted (the guest speaks, a tap over her) or prefers-reduced-motion =
   the end state at once, no motion. One story at a time: a new one, or any
   other picture on the stage, tears the old one down and frees its images.

   Performance on the robot's weak GPU: transform/opacity only (+ a count-up
   text node), no filter/blur/backdrop-filter, no infinite animation, pictures
   decoded before their beat (emma-story.css says the same). */
(function (root) {
  'use strict';

  // ---------------------------------------------------------------- the clock
  // Pure (no DOM): node runs it in tests/client_story_clock.cjs.

  var PUNCT = /[\s​‌‍\-‐-―.,!?'"“”‘’()…:;·\/、。，！？]+/g;
  function norm(s) { return String(s == null ? '' : s).toLowerCase().replace(PUNCT, ''); }

  var CLOCK = {
    fallbackS: 4.0,   // no cue this long after the last beat: fire anyway
    marginS: 0.35,    // a line-length estimate is never trusted to the edge
    ratioMarginS: 0.15, // nor the whole answer's text/audio ratio (pauses are not uniform)
    syncMarginS: 0.12,  // nor a piece's settled audio: a piece and its audio need not
                        // split at the same chunk (one chunk of slack)
    maxRate: 22,      // chars/s: the hard floor once the answer is complete
    fastRate: 20,     // chars/s no voice reaches: "this text ended by then"
    leadRate: 16,     // chars/s floor before the answer is complete, a language not measured
    // Her own voice, measured on the robot (story answers 13-14 Oct, chars of
    // `norm` text per second of her audio): Thai 11.5-13.6. With the old 16
    // as the floor, every beat before turn_complete fired at 0.84-0.93 of its
    // word's time -- pictures ~2 s ahead of her by the 20th second (owner 14
    // Oct: "ตอนโชว์... ยังไม่ตรง") -- while every beat after it, on the
    // answer's own ratio, landed at 1.0. The floor is her rate; each finished
    // answer then teaches the page her real one (`learnRate`).
    // zh: not measured on the robot -- Mandarin runs ~4-6 chars/s, and the
    // generic 16 would put every Chinese picture 2-3x ahead (review 14 Oct).
    voiceRate: { th: 12.5, en: 15, zh: 5 },
    noAudioS: 7.0,    // a story whose answer never plays rests on its overview
    stallS: 3.0,      // her audio played out and nothing new came: the answer is over
    blindS: 2.0,      // the page's audio clock gone (call closed) this long: rest
    endEpsS: 0.12,    // "her audio has played out"
    maxStoryS: 120    // and nothing waits forever
  };

  function scriptOf(text) {
    var th = 0, zh = 0, en = 0, t = String(text || '');
    for (var i = 0; i < t.length; i++) {
      var c = t.charCodeAt(i);
      if (c >= 0x0E00 && c <= 0x0E7F) th++;
      else if (c >= 0x3400 && c <= 0x9FFF) zh++;
      else if ((c | 32) >= 97 && (c | 32) <= 122) en++;
    }
    var top = Math.max(th, zh, en);
    if (top < 4) return null;
    return top === th ? 'th' : (top === zh ? 'zh' : 'en');
  }

  function BeatClock(beats, opt) {
    opt = opt || {};
    beats = beats || [];
    this.n = beats.length;
    this.cues = beats.map(function (b) {
      return ((b && b.cues) || []).map(norm).filter(function (c) { return !!c; });
    });
    this.w = beats.map(function (b) { return (b && b.w) || {}; });
    this.fallbackS = opt.fallbackS || CLOCK.fallbackS;
    this.fired = 0;       // beats fired so far (always in order)
    this.lastFire = 0;    // played-speech seconds the last beat was due at
    this.from = 0;        // where the next cue search starts
    this.cueAt = [];      // normalized index of each beat's cue, if heard
    this.segs = [];       // [start, duration] of each audio chunk of this answer
    this.sched = 0;       // seconds of her answer scheduled so far
    this.text = '';       // her transcript since the story arrived, normalized
    this.raw = '';
    // [text length, seconds of audio by which that text has surely been
    // spoken]. The seconds are filled in LATER, not when the text arrives:
    // Gemini sends each piece of transcript just BEFORE the audio it
    // transcribes (live runs 12 Oct: 91/91 answers opened with text, never
    // two pieces without audio between them, ~1.4 s of audio after each) --
    // so the audio scheduled when a piece arrives is the audio BEFORE it,
    // and a beat timed on it fired up to a piece early (review 12 Oct). A
    // piece's audio is all scheduled once audio has come after it and the
    // next piece arrives (or the turn completes). If the text trails instead,
    // that is one piece later than needed -- late, never early.
    this.sync = [];
    this.pend = [];       // indexes in `sync` still waiting for their audio
    this.audioSince = false; // audio scheduled since the last piece of text
    this.complete = false;
    this.lang = null;
  }
  BeatClock.prototype.audio = function (start, dur) {
    if (!(dur > 0) || !isFinite(start)) return;
    this.segs.push([start, dur]);
    this.sched += dur;
    this.audioSince = true;
  };
  BeatClock.prototype.settleText = function () {
    for (var k = 0; k < this.pend.length; k++) this.sync[this.pend[k]][1] = this.sched;
    this.pend = [];
  };
  BeatClock.prototype.addText = function (chunk) {
    var raw = String(chunk || '');
    if (!raw) return;
    if (!this.lang) { this.raw += raw; this.lang = scriptOf(this.raw); }
    // the pieces before this one: their audio came between them and now
    if (this.audioSince) this.settleText();
    this.audioSince = false;
    this.text += norm(raw);
    this.pend.push(this.sync.length);
    this.sync.push([this.text.length, null]);
    this.match();
  };
  // The turn's own turn_complete -- only once some of this answer's audio has
  // arrived (gemini-3.8 completes a turn BEFORE its tool call too).
  BeatClock.prototype.turnComplete = function () {
    if (!this.segs.length) return;
    this.complete = true;
    this.settleText();
  };
  // Cue words in order: beat i's cue is searched after beat i-1's.
  BeatClock.prototype.match = function () {
    for (var i = this.fired; i < this.n; i++) {
      if (this.cueAt[i] !== undefined) continue;
      var best = -1, len = 0, cues = this.cues[i];
      if (!cues.length) continue;        // a beat with no cue word waits on time alone
      for (var k = 0; k < cues.length; k++) {
        var p = this.text.indexOf(cues[k], this.from);
        if (p >= 0 && (best < 0 || p < best)) { best = p; len = cues[k].length; }
      }
      if (best < 0) break;
      this.cueAt[i] = best;
      this.from = best + len;
    }
  };
  // Seconds of played audio a character is spoken at.
  // Before her answer is complete: the piece holding character p has its
  // audio-so-far settled (see `sync`) -- an upper bound for where the piece
  // ENDS -- and a character `n` places before the end was spoken at least
  // n / fastRate earlier (fastRate: faster than anyone speaks, so this never
  // comes out early, and a pause inside the piece only makes it later).
  // A piece whose audio is not settled yet: not yet. `leadRate` floors it.
  // Once the answer is complete (turn_complete): the answer's own text/audio
  // ratio, which is right whether the text trailed, led, or came in one late
  // block -- plus a small margin, because pauses are not spread evenly.
  BeatClock.prototype.charTime = function (p) {
    var L = this.text.length;
    if (this.complete && L > 0) return Math.max(p * this.sched / L + CLOCK.ratioMarginS, p / CLOCK.maxRate);
    var floor = p / leadRateFor(this.lang);
    for (var k = 0; k < this.sync.length; k++) {
      var c = this.sync[k][0], s = this.sync[k][1];
      if (c > p) return s == null ? Infinity : Math.max(s + CLOCK.syncMarginS - (c - p) / CLOCK.fastRate, floor);
    }
    return Infinity;
  };
  // The pre-complete floor: her rate in this language, learned from her
  // finished answers on this page (a slower voice or a list read with pauses
  // moves it down), else the measured default, else the old generic floor.
  var learnedRate = {};
  function leadRateFor(lang) {
    return learnedRate[lang] || (lang && CLOCK.voiceRate[lang]) || CLOCK.leadRate;
  }
  // A finished answer of 3 s or more: its own text/audio ratio, kept inside a
  // sane band (a transcript that lost pieces must not teach a crawl) and
  // moved half-way each time (one odd answer does not swing the next one).
  function learnRate(lang, chars, seconds) {
    if (!lang || !(seconds >= 3) || !(chars > 0)) return;
    var r = Math.min(20, Math.max(lang === 'zh' ? 3 : 9, chars / seconds));
    // the first answer moves half-way from the measured default too: one odd
    // answer (a transcript that outran its audio) must not set the floor
    // alone (review 14 Oct: one 20 cps answer made Thai beats lead again)
    learnedRate[lang] = ((learnedRate[lang] || leadRateFor(lang)) + r) / 2;
  }
  // Where line i should start if she says the lines as written (their
  // lengths in her language) -- known only once her whole answer is in.
  BeatClock.prototype.expected = function (i) {
    if (!this.complete) return Infinity;
    var key = this.lang === 'th' ? 'th' : 'en', total = 0, before = 0;
    for (var k = 0; k < this.n; k++) {
      var v = +(this.w[k] && this.w[k][key]) || 0;
      total += v;
      if (k < i) before += v;
    }
    return total > 0 ? this.sched * before / total : Infinity;
  };
  BeatClock.prototype.due = function (i) {
    if (i === 0) return 0;                       // the scene opens with her voice
    if (this.cueAt[i] !== undefined) return this.charTime(this.cueAt[i]);
    var t = Math.min(this.lastFire + this.fallbackS, this.expected(i) + CLOCK.marginS);
    if (!this.cues[i].length) {
      // no cue word at all: it is past once the next line's cue is heard
      for (var k = i + 1; k < this.n; k++) {
        if (this.cueAt[k] !== undefined) { t = Math.min(t, this.charTime(this.cueAt[k])); break; }
        if (this.cues[k].length) break;
      }
    }
    return t;
  };
  BeatClock.prototype.played = function (now) {
    var t = 0;
    for (var k = 0; k < this.segs.length; k++) {
      var d = now - this.segs[k][0];
      if (d > 0) t += d < this.segs[k][1] ? d : this.segs[k][1];
    }
    return t;
  };
  // What is due now: {fire: [beat indexes, in order], rest: her answer is over}.
  BeatClock.prototype.poll = function (now) {
    var out = { fire: [], rest: false };
    if (!this.segs.length || now == null) return out;      // no audio: wait
    var played = this.played(now);
    if (!(played > 0)) return out;
    while (this.fired < this.n) {
      var i = this.fired, t = this.due(i);
      if (played < t) break;
      out.fire.push(i);
      this.fired = i + 1;
      this.lastFire = Math.max(this.lastFire, Math.min(t, played));
      this.match();
    }
    if (this.complete && played >= this.sched - CLOCK.endEpsS) {
      while (this.fired < this.n) out.fire.push(this.fired++);
      if (!this.learned) { this.learned = true; learnRate(this.lang, this.text.length, this.sched); }
      out.rest = true;
    }
    return out;
  };

  // ------------------------------------------------------------- DOM helpers

  function el(tag, cls, text) {
    var d = document.createElement(tag);
    if (cls) d.className = cls;
    if (text != null) d.textContent = text;
    return d;
  }
  function arr(v) { return Array.isArray(v) ? v : []; }
  function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }
  function decimals(v) {
    if (v && typeof v.dec === 'number') return v.dec;
    var d = 0;
    [v && v.from, v && v.to].forEach(function (x) { if (typeof x === 'number' && x % 1) d = 1; });
    return d;
  }
  // Text that must not break where a line break reads wrong (review 12 Oct):
  // "11.2 / km" (a number and its unit), "≈ / 155", and a "·" left at the end
  // of a line -- the dot goes with the word after it.
  function tidy(t) {
    return String(t == null ? '' : t)
      .replace(/(\d)\s+(km|min|hr|h|m|sq\.m\.|SQ\.M\.)(?![A-Za-z])/g, '$1\u00a0$2')
      .replace(/≈\s+/g, '≈\u00a0')
      .replace(/\s·\s/g, ' ·\u00a0');
  }
  function fmt(x, dec) {
    return Number(x).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  }
  function valueText(v) {
    if (!v) return '';
    var dec = decimals(v), unit = v.unit_en ? ' ' + v.unit_en : '';
    var head = (v.prefix_en ? v.prefix_en + ' ' : '');
    return head + (num(v.from) != null ? fmt(v.from, dec) + '–' : '') + fmt(v.to, dec) + unit;
  }
  function reducedMotion() {
    try { return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches); }
    catch (e) { return false; }
  }
  var TOP_VH = 14;       // below the status pill and her caption band
  // a plan: on white paper, or on the dark stage when the picture is
  // transparent lines (the build's lines_to_alpha)
  function planClass(it) {
    return it.asset_kind === 'plan' ? ' plan' + (it.asset && it.asset.alpha ? ' plan-dark' : '') : '';
  }

  // ------------------------------------------------------------- one story

  function Story(data, opts) {
    var self = this;
    this.data = data;
    this.opts = opts || {};
    this.n = arr(data.beats).length;
    this.clock = new BeatClock(arr(data.beats));
    this.timers = [];
    this.rafs = [];
    this.imgs = [];
    this.counts = [];
    this.dead = false;
    this.ended = false;
    this.instant = false;
    this.fired = 0;
    this.taps = {};
    arr(data.taps).forEach(function (t) { if (t && typeof t.id === 'string') self.taps[t.id] = t; });
    // taps a template element carries itself (a docked row, a floor slab);
    // the rest become chips on the overview
    var bound = {};
    var p = data.params || {};
    (data.template === 'stack' || data.template === 'drill' ? arr(p.items) : data.template === 'slabs' ? arr(p.floors) : [])
      .forEach(function (it) { if (it && it.tap) bound[it.tap] = true; });
    this.unbound = arr(data.taps).filter(function (t) { return t && !bound[t.id] && self.taps[t.id]; });
    this.root = el('div', 'es-root es-t-' + data.template);
    this.opts.mount.appendChild(this.root);
    this.W = this.root.clientWidth || root.innerWidth || 1080;
    this.H = this.root.clientHeight || root.innerHeight || 1920;
    // The overview's chips are laid out now (still invisible), so the lowest
    // point content may reach is measured, not guessed: three chips can wrap
    // onto three rows.
    this.floorY = this.H * 0.965;
    if (this.unbound.length) {
      this.tapRow = this.buildTaps();
      var top = this.tapRow.offsetTop;
      this.floorY = top > this.H * 0.5 ? top - this.H * 0.015 : this.H * 0.885;
    }
    this.tpl = TEMPLATES[data.template](this, p);
    if (BALANCED[data.template]) this.balance();
    if (this.tpl.layout) this.tpl.layout();
    this.t0 = Date.now();
    this.fireAt = [];
    // The story's own report (opts.onReport), from real conversations: the
    // reviewer (13 Oct) could not check picture-to-voice timing on silent
    // screen recordings, nor smoothness with her voice and the 3D Emma
    // running. Frame times are sampled only while a report is wanted.
    if (this.opts.onReport) this.sampleFrames();
    if (reducedMotion()) { this.toEnd(true); return; }
    this.tick = setInterval(function () { self.step(); }, 90);
  }
  Story.prototype.sampleFrames = function () {
    var self = this, last = 0;
    this.frames = [];
    function f(t) {
      if (self.dead || self.reported) return;
      if (last) self.frames.push(t - last);
      last = t;
      self.sampler = requestAnimationFrame(f);
    }
    this.sampler = requestAnimationFrame(f);
  };
  // Once per story, at its overview or when the guest cut in. Per beat: when
  // it went up (`at`, seconds of her voice played; null = put up by the
  // overview, not by her voice) and where its cue word fell in her voice
  // (`cue`, by her answer's own text/audio ratio; null = no cue heard).
  // Numbers and the story id only -- never her words.
  Story.prototype.report = function (interrupted) {
    if (this.reported || !this.opts.onReport) return;
    this.reported = true;
    if (this.sampler) cancelAnimationFrame(this.sampler);
    var c = this.clock, L = c.text.length, beats = [];
    for (var k = 0; k < this.n; k++) {
      var at = this.fireAt[k], p = c.cueAt[k];
      var cue = p !== undefined && L > 0 && c.sched > 0 ? p * c.sched / L : null;
      beats.push({ at: at == null ? null : Math.round(at * 100) / 100,
                   cue: cue == null ? null : Math.round(cue * 100) / 100 });
    }
    var fr = (this.frames || []).slice().sort(function (a, b) { return a - b; }), sum = 0;
    for (var j = 0; j < fr.length; j++) sum += fr[j];
    try {
      this.opts.onReport({
        id: this.data.id, beats: beats, interrupted: !!interrupted,
        audio_s: Math.round(c.sched * 100) / 100, frames: fr.length,
        fps: sum > 0 ? Math.round(fr.length / (sum / 1000) * 10) / 10 : null,
        p95_ms: fr.length ? Math.round(fr[Math.floor(fr.length * 0.95)] * 10) / 10 : null,
        long_frames: fr.filter(function (d) { return d > 50; }).length
      });
    } catch (e) { if (root.console) console.warn('story report', e); }
  };

  Story.prototype.add = function (node) { this.root.appendChild(node); return node; };
  // A wide picture and three rows leave the lower half of a portrait screen
  // empty (the designers' 20 stories, 12 Oct: a 2.5:1 campus render filled a
  // fifth of the height). Once laid out -- everything exists from the start,
  // hidden by opacity only -- the content block moves down by part of the
  // room left above the overview's taps, so it sits a little above centre.
  // Positions only, set once before anything shows: no layout animation.
  // Elements anchored to the bottom (taps, hint, footnote) stay where they are.
  Story.prototype.balance = function () {
    var kids = this.root.children, movable = [], bottom = 0;
    for (var k = 0; k < kids.length; k++) {
      var c = kids[k];
      if (c === this.tapRow || !c.style || !c.style.top) continue;
      movable.push(c);
      bottom = Math.max(bottom, (c.offsetTop || 0) + (c.offsetHeight || 0));
    }
    var room = this.floorY - this.H * 0.03 - bottom;
    if (!movable.length || !(room > this.H * 0.05)) return;
    var dy = Math.round(room * 0.42);
    movable.forEach(function (c) {
      var t = parseFloat(c.style.top);
      if (isFinite(t)) { c.style.top = Math.round(t + dy) + 'px'; if (c._top != null) c._top += dy; }
    });
  };
  // The scale (<= 1) at which a block anchored at its top stays above
  // `limitY`. Lists that grow with the catalog (five pools, six nearby
  // places) ran into the taps and Emma's corner on the 13 Oct recordings.
  // Measured once, before anything shows; a transform only.
  Story.prototype.fitScale = function (node, limitY) {
    var top = node.offsetTop || 0, h = node.offsetHeight || 0;
    if (!h || top + h <= limitY) return 1;
    return Math.max(0.6, (limitY - top) / h);
  };
  // Where full-width content must end: above the taps' row and above Emma's
  // head in the bottom-right corner (~80% of the height).
  Story.prototype.contentBottom = function () {
    return Math.min(this.floorY - this.H * 0.012, this.H * 0.78);
  };
  Story.prototype.later = function (ms, fn) {
    if (this.dead) return;
    if (this.instant) { fn(); return; }
    var self = this;
    this.timers.push(setTimeout(function () { if (!self.dead) fn(); }, ms));
  };
  Story.prototype.frame = function (fn) {
    if (this.dead) return;
    if (this.instant) { fn(); return; }
    var self = this;
    var id = requestAnimationFrame(function () {
      var id2 = requestAnimationFrame(function () { if (!self.dead) fn(); });
      self.rafs.push(id2);
    });
    this.rafs.push(id);
  };
  Story.prototype.img = function (ref, cls) {
    var im = new Image();
    im.alt = '';
    im.decoding = 'async';
    im.draggable = false;
    if (cls) im.className = cls;
    var src = ref && ref.src ? ref.src : '';
    // A picture that fails (a 404 once approval is withdrawn mid-session, a
    // swapped file) is hidden, not drawn as the browser's broken-image icon
    // under a title that describes it (review 12 Oct).
    function broken() {
      if (im.classList.contains('es-broken')) return;
      im.classList.add('es-broken');
      if (im._onBroken) im._onBroken();
    }
    im.onerror = broken;
    if (src) im.src = this.opts.assetUrl ? this.opts.assetUrl(src) : src;
    im._ready = src && im.decode ? im.decode().catch(broken) : Promise.resolve();
    this.imgs.push(im);
    return im;
  };
  // Pictures inside `node` decoded (or 1.2 s passed): a beat never shows a
  // half-painted image, and never waits on one for long.
  Story.prototype.ready = function (node) {
    var waits = [];
    var imgs = node.tagName === 'IMG' ? [node] : node.querySelectorAll('img');
    for (var k = 0; k < imgs.length; k++) if (imgs[k]._ready) waits.push(imgs[k]._ready);
    if (!waits.length) return Promise.resolve();
    return Promise.race([Promise.all(waits), new Promise(function (r) { setTimeout(r, 1200); })]);
  };
  Story.prototype.reveal = function (node, then) {
    if (!node || this.dead) return;
    var self = this;
    if (this.instant) { node.classList.add('on'); if (then) then(); return; }
    this.ready(node).then(function () {
      self.frame(function () { node.classList.add('on'); if (then) then(); });
    });
  };
  // A number that counts up (the one non-transform/opacity motion allowed:
  // one text node, a second at most).
  Story.prototype.count = function (node, from, to, dec, ms) {
    var self = this, gen = (node._gen || 0) + 1;
    node._gen = gen;
    node._final = fmt(to, dec);
    if (this.counts.indexOf(node) < 0) this.counts.push(node);
    // once interrupted, a count that a late callback starts is just its end
    if (this.instant || this.endedInstant || !(ms > 0)) { node.textContent = node._final; return; }
    // Every frame of a count is a photograph: "124 m" on the way to 155,
    // "5,865" to 6,000, "17 items" to 20 (13 Oct recordings; the owner's
    // reviewer: a guest can photograph a figure that is not the fact). On the
    // way up the number moves in round steps only, dimmed, and the exact
    // figure lands at the end -- still the owner's "the number counts up".
    var step = countStep(to);
    node.classList.add('es-count');
    if (!step) {
      // under 100 there is no round step that is not a believable wrong
      // figure ("20 items" on the way to a one-bedroom's 25, when the studio
      // really has 20): it lands, it does not count
      node.textContent = node._final;
      node.classList.add('es-landed');
      return;
    }
    node.classList.add('es-counting');
    var t0 = 0;
    function f(t) {
      if (self.dead || node._gen !== gen) return;
      if (!t0) t0 = t;
      var k = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - k, 3);
      if (k < 1) {
        node.textContent = fmt(Math.floor((from + (to - from) * e) / step) * step, 0);
        self.rafs.push(requestAnimationFrame(f));
      } else {
        node.textContent = node._final;
        node.classList.remove('es-counting');
        node.classList.add('es-landed');
      }
    }
    this.rafs.push(requestAnimationFrame(f));
  };
  // The step a count moves in: none under 100 (it lands), tens up to 999,
  // then two significant figures (1,066 counts 0, 100 ... 1,000, then 1,066).
  function countStep(to) {
    var a = Math.abs(num(to) || 0);
    if (a < 100) return 0;
    var digits = Math.floor(Math.log(a) / Math.LN10) + 1;
    return digits <= 3 ? 10 : Math.pow(10, digits - 2);
  }
  // {node, go()} for a value. One number ("≈ 224") counts up when go() runs
  // -- the owner's "the number counts up". A RANGE ("24–29 sq.m.") is drawn
  // at its final value from the start: counted, both ends ran up from 0, so
  // "0–0", then "15–18", "20–24 sq.m." stood on screen for a second while she
  // said "24 to 29" -- a wrong size a guest can photograph (review 12 Oct).
  Story.prototype.valueNode = function (v, cls) {
    var self = this, dec = decimals(v), range = num(v.from) != null;
    var node = el('em', (cls || 'es-v') + (range ? ' es-range' : ''));
    if (v.prefix_en) node.appendChild(document.createTextNode(v.prefix_en + ' '));
    var b = el('span', null, range ? fmt(v.to, dec) : fmt(0, dec));
    // its final figure is known now: an overview put up before go() ran (the
    // guest cut in) shows it, not "≈ 0 CAR SPACES" (review 13 Oct)
    if (!range) { b._final = fmt(num(v.to) != null ? v.to : 0, dec); self.counts.push(b); }
    if (range) {
      node.appendChild(el('span', null, fmt(v.from, dec)));
      node.appendChild(document.createTextNode('–'));
    }
    node.appendChild(b);
    if (v.unit_en) node.appendChild(el('small', null, ' ' + v.unit_en));
    return {
      node: node,
      go: function () {
        if (!range) self.count(b, 0, num(v.to) != null ? v.to : 0, dec, 1000);
      }
    };
  };
  Story.prototype.bindTap = function (node, id) {
    var self = this;
    node.classList.add('es-tap');
    node.setAttribute('data-tap', id);     // the id only (the harness picks a tap by it)
    if (node.tagName === 'BUTTON') node.type = 'button';
    node.addEventListener('click', function () {
      if (self.dead || !self.opts.onTap) return;
      if (self.opts.onTap(id)) node.classList.add('is-sent');
    });
  };
  // A picture in a frame of its own aspect ratio, centred, at most maxW% of
  // the width and maxH% of the height, its top at topVh.
  Story.prototype.box = function (ref, maxW, maxH, topVh, cls) {
    var ar = ref && ref.w && ref.h ? ref.w / ref.h : 4 / 3;
    var w = this.W * maxW / 100, h = w / ar;
    if (h > this.H * maxH / 100) { h = this.H * maxH / 100; w = h * ar; }
    var b = el('div', 'es-box' + (cls ? ' ' + cls : ''));
    b._w = Math.round(w); b._h = Math.round(h);
    b._left = Math.round((this.W - b._w) / 2); b._top = Math.round(this.H * topVh / 100);
    b.style.width = b._w + 'px'; b.style.height = b._h + 'px';
    b.style.left = b._left + 'px'; b.style.top = b._top + 'px';
    return b;
  };
  Story.prototype.spot = function (x, y) {
    var s = el('div', 'es-spot');
    s.style.left = x + '%';
    s.style.top = y + '%';
    s.appendChild(el('i'));
    s.appendChild(el('b'));
    return s;
  };
  // A label for a point (x%, y%) on a picture in `box`, clear of the ring:
  // offset by the label's OWN height, above the point when it fits, else
  // below, and kept inside the picture. Placed by a % of the picture before,
  // on a wide picture it landed on the ring it named and on the plan's own
  // printed name (review 12 Oct). Measured once, before anything shows.
  Story.prototype.placeLabel = function (label, box, x, y, prefer) {
    var gap = Math.round(this.W * 0.055);
    var h = label.offsetHeight || Math.round(this.W * 0.1), w = label.offsetWidth || Math.round(this.W * 0.3);
    var py = y / 100 * box._h, roomUp = py - gap - h, roomDown = box._h - (py + gap + h);
    var up = prefer !== 'below' ? (roomUp >= 4 || roomUp >= roomDown) : !(roomDown >= 4 || roomDown >= roomUp);
    label.style.top = y + '%';
    label.style.translate = up ? '-50% calc(-100% - ' + gap + 'px)' : '-50% ' + gap + 'px';
    var half = Math.min(50, (w / 2 + 6) / box._w * 100);
    label.style.left = Math.max(half, Math.min(100 - half, x)) + '%';
    return up;
  };

  Story.prototype.step = function () {
    if (this.dead || this.ended) return;
    var now = this.opts.clock ? this.opts.clock() : null, wall = Date.now(), c = this.clock;
    if (now != null) {
      this.blindSince = 0;
      var r = c.poll(now);
      // when each beat went up, in seconds of her voice played (the report)
      if (r.fire.length) { var at = c.played(now); for (var j = 0; j < r.fire.length; j++) this.fireAt[r.fire[j]] = at; }
      for (var k = 0; k < r.fire.length; k++) this.fire(r.fire[k]);
      if (r.rest) { this.finish(); return; }
      // Her audio played out and nothing new came, with no turn_complete (a
      // provider rotation, a stalled stream): left alone, the story sat on an
      // empty frame for the 120 s cap (review 12 Oct).
      if (c.segs.length && c.played(now) >= c.sched - CLOCK.endEpsS) {
        if (!this.idleSince) this.idleSince = wall;
        else if (wall - this.idleSince > CLOCK.stallS * 1000) { this.finish(); return; }
      } else {
        this.idleSince = 0;
      }
    } else if (c.segs.length) {
      // The page's audio clock is gone after her voice began: the call
      // closed (network, a restart). Its overview, not a half story frozen
      // under "Disconnected" for two minutes.
      if (!this.blindSince) this.blindSince = wall;
      else if (wall - this.blindSince > CLOCK.blindS * 1000) { this.finish(); return; }
    }
    var waited = (wall - this.t0) / 1000;
    if ((!c.segs.length && waited > CLOCK.noAudioS) || waited > CLOCK.maxStoryS) this.finish();
  };
  Story.prototype.fire = function (i) {
    while (this.fired <= i && this.fired < this.n) {
      var k = this.fired++;
      try { this.tpl.beat(k); } catch (e) { if (root.console) console.warn('story beat', k, e); }
    }
  };
  Story.prototype.stopTick = function () { if (this.tick) { clearInterval(this.tick); this.tick = 0; } };
  // Her answer played out: the remaining beats, then the overview.
  Story.prototype.finish = function () {
    if (this.ended || this.dead) return;
    this.stopTick();
    this.fire(this.n - 1);
    this.ended = true;
    var self = this;
    this.later(this.n ? 700 : 0, function () {
      try { self.tpl.rest(false); } catch (e) { if (root.console) console.warn('story rest', e); }
      self.showTaps();
      self.report(false);
    });
  };
  // Interrupted / reduced motion: the overview now, with no motion at all.
  Story.prototype.toEnd = function () {
    if (this.dead || this.endedInstant) return;
    var self = this;
    this.stopTick();
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.rafs.forEach(function (id) { cancelAnimationFrame(id); });
    this.rafs = [];
    this.instant = true;
    this.root.classList.add('es-instant');
    try {
      this.fire(this.n - 1);
      this.tpl.rest(true);
    } catch (e) { if (root.console) console.warn('story end', e); }
    // a count cut short lands too: its figure, at full strength (review 13 Oct)
    this.counts.forEach(function (node) {
      if (node._final != null) { node._gen = (node._gen || 0) + 1; node.textContent = node._final; }
      node.classList.remove('es-counting');
    });
    this.showTaps();
    // the chips' own reveal frames were just cancelled with every other
    // timer: invisible chips that still take a tap (review 13 Oct)
    if (this.tapRow) for (var c = 0; c < this.tapRow.children.length; c++) this.tapRow.children[c].classList.add('on');
    this.ended = true;
    this.endedInstant = true;
    this.report(true);
    void this.root.offsetWidth;      // commit the end state before motion returns
    this.instant = false;
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { if (!self.dead) self.root.classList.remove('es-instant'); });
    });
  };
  Story.prototype.buildTaps = function () {
    var self = this, row = el('div', 'es-taps');
    this.unbound.forEach(function (t) {
      var b = el('button', 'es-chipbtn es-up', t.label_en || '');
      self.bindTap(b, t.id);
      row.appendChild(b);
    });
    return this.add(row);
  };
  Story.prototype.showTaps = function () {
    if (this.tapsShown || !this.tapRow || this.dead) { this.tapsShown = true; return; }
    this.tapsShown = true;
    var self = this, buttons = this.tapRow.children;
    for (var k = 0; k < buttons.length; k++) {
      (function (b, k) { self.later(k * 90, function () { self.reveal(b); }); })(buttons[k], k);
    }
  };
  Story.prototype.teardown = function () {
    if (this.dead) return;
    this.report(true);             // gone before its overview: cut short
    this.dead = true;
    this.stopTick();
    this.timers.forEach(clearTimeout);
    this.rafs.forEach(function (id) { cancelAnimationFrame(id); });
    this.timers = []; this.rafs = [];
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    // The pictures go with the tree: nothing here keeps a reference, so the
    // collector frees them and their decoded bitmaps. Clearing `src` on the
    // detached images (as this once did "to free memory") LEAKED instead:
    // Chromium registers each such image with the document's media-query
    // matcher and never lets go -- every story's whole DOM stayed alive
    // (review 12 Oct: +60 nodes and ~15 KB per story, over 38 stories).
    this.imgs.forEach(function (im) { im._ready = null; im.onerror = null; im._onBroken = null; });
    this.imgs = [];
    this.counts = [];
  };

  // ------------------------------------------------------------- templates
  // Each returns {beat(i), rest(instant)}: beat(i) is line i being spoken;
  // rest() leaves the overview whatever has or has not fired (it is also the
  // interrupted / reduced-motion end state, where nothing animates).

  var TEMPLATES = {};
  // Templates whose content block may sit lower when it is short (balance):
  // stack moves only its list (the big card has its own place); not slabs
  // (its own geometry fills the screen) nor details (its
  // mood strip already stretches to the floor) nor drill (laid out from the
  // bottom up, clear of Emma's head).
  var BALANCED = { stack: 1, assembly: 1, distance: 1, metric: 1, spotlight: 1, features: 1,
                   journey: 1, neighborhood: 1 };

  // Room types (the owner's storyboard 1): each plan big while she talks
  // about it, then it shrinks into its row of the list; the next comes up
  // BELOW the rows already docked, so the list visibly grows; the end state
  // is that list, to tap. While she talks the list is the same rows drawn
  // small right under her caption (one transform, set before anything shows
  // -- not a relayout); at rest it grows to its full size where balance()
  // placed it. It used to dock each card into a row under the NEXT card and a
  // dim layer: the guest saw cards shrink and vanish, and the list only at
  // the very end (review 12 Oct).
  TEMPLATES.stack = function (S, p) {
    var items = arr(p.items);
    // two or three rows: bigger plans and type -- the end state is a list
    // a guest taps, and three small rows left the lower half of the screen bare
    var dock = S.add(el('div', 'es-dock' + (items.length <= 3 ? ' roomy' : '')));
    dock.style.top = Math.round(S.H * 0.17) + 'px';
    var SMALL = 0.56, LIST_TOP = S.H * 0.15, CARD_BOTTOM = S.H * 0.68;
    var rows = items.map(function (it) {
      var tapId = it.tap && S.taps[it.tap] ? it.tap : null;
      var row = el(tapId ? 'button' : 'div', 'es-row');
      var th = el('div', 'es-th' + planClass(it));
      if (it.asset) th.appendChild(S.img(it.asset));
      var tx = el('div', 'es-tx');
      tx.appendChild(el('b', null, it.label_en || ''));
      if (it.sub_en) tx.appendChild(el('span', null, tidy(it.sub_en)));
      // the value under the label, not beside it: a long label and a range
      // ("2 BEDROOMS" / "51.5–52.5 sq.m.") never share one line
      if (it.value) tx.appendChild(el('em', 'es-v', valueText(it.value)));
      row.appendChild(th);
      row.appendChild(tx);
      if (tapId) { row.appendChild(el('i', 'es-go', '›')); S.bindTap(row, tapId); }
      dock.appendChild(row);
      return row;
    });
    var hint = p.end_hint_en ? S.add(el('p', 'es-hint es-fade', p.end_hint_en)) : null;
    if (hint) hint.style.bottom = Math.round(S.H - S.floorY + S.H * 0.01) + 'px';
    var cur = null, presented = false;
    // Her opening line names no item ("We have three room types"): the rows
    // wait as empty frames, so the screen answers "three" before the first
    // plan comes up -- their labels stay hidden until she says them.
    function ghosts() { rows.forEach(function (r) { if (!r.classList.contains('on')) r.classList.add('ghost'); }); }
    // the next card's top: under the last row already in the list
    function cardTop() {
      var top = LIST_TOP, base = S.root.getBoundingClientRect().top || 0;
      rows.forEach(function (r) {
        if (r.classList.contains('on') || r._docked) top = Math.max(top, r.getBoundingClientRect().bottom - base + S.H * 0.012);
      });
      return Math.min(top, CARD_BOTTOM - S.H * 0.25);
    }
    function present(k) {
      presented = true;
      var it = items[k];
      var big = el('div', 'es-big' + planClass(it));
      var top = S.instant ? LIST_TOP : cardTop();
      big.style.top = Math.round(top) + 'px';
      big.style.height = Math.round(CARD_BOTTOM - top) + 'px';
      var ph = el('div', 'es-ph');
      if (it.asset) ph.appendChild(S.img(it.asset));
      var lab = el('div', 'es-lab');
      var t = el('div', 'es-t', it.label_en || '');
      if (it.sub_en) t.appendChild(el('span', 'es-s', it.sub_en));
      lab.appendChild(t);
      var v = it.value ? S.valueNode(it.value) : null;
      if (v) lab.appendChild(v.node);
      big.appendChild(ph);
      big.appendChild(lab);
      S.add(big);
      S.reveal(big, function () { if (v) S.later(350, v.go); });
      cur = { big: big, k: k };
    }
    function dockCurrent() {
      if (!cur) return;
      var c = cur, row = rows[c.k];
      cur = null;
      row._docked = true;             // the next card measures under it already
      var a = S.instant ? null : c.big.getBoundingClientRect();
      var r = S.instant ? null : row.querySelector('.es-th').getBoundingClientRect();
      if (!a || !a.width || !r.width) { c.big.parentNode && c.big.parentNode.removeChild(c.big); row.classList.add('on'); return; }
      var sc = r.width / a.width;
      var dx = (r.left + r.width / 2) - (a.left + a.width / 2);
      var dy = (r.top + r.height / 2) - (a.top + a.height / 2);
      c.big.classList.add('es-docking');
      c.big.style.transform = 'translate3d(' + dx.toFixed(1) + 'px,' + dy.toFixed(1) + 'px,0) scale(' + sc.toFixed(4) + ')';
      c.big.style.opacity = '0';
      S.later(470, function () { row.classList.add('on'); });
      S.later(820, function () { if (c.big.parentNode) c.big.parentNode.removeChild(c.big); });
    }
    var small = false;
    // the list at rest fits above the hint, the taps and Emma's head: five
    // pools in full-size rows ran into all three (13 Oct recording)
    function fit() {
      var limit = S.contentBottom();
      if (hint && hint.offsetTop) limit = Math.min(limit, hint.offsetTop - S.H * 0.01);
      return S.fitScale(dock, limit);
    }
    function grow() {
      small = false;
      var k = fit();
      dock.style.transform = k < 1 ? 'scale(' + k.toFixed(4) + ')' : '';
    }
    return {
      // after balance(): the small list sits under the caption band
      layout: function () {
        if (S.instant) return;
        small = true;
        var dy = Math.round(LIST_TOP - (parseFloat(dock.style.top) || 0));
        dock.style.transform = 'translate3d(0,' + dy + 'px,0) scale(' + SMALL + ')';
      },
      beat: function (i) {
        var ks = [];
        items.forEach(function (it, k) { if (it.line === i) ks.push(k); });
        if (!ks.length) { if (!presented) ghosts(); return; }
        dockCurrent();
        ks.forEach(function (k, j) { if (j < ks.length - 1) rows[k].classList.add('on'); else present(k); });
      },
      rest: function (instant) {
        dockCurrent();
        rows.forEach(function (r) { r.classList.add('on'); });
        if (hint) hint.classList.add('on');
        if (instant) {
          var bigs = S.root.querySelectorAll('.es-big');
          for (var k = 0; k < bigs.length; k++) bigs[k].parentNode.removeChild(bigs[k]);
          grow();
        } else if (small) {
          // the last card lands in its small row, then the list grows
          S.later(480, function () { dock.classList.add('es-grow'); grow(); });
        }
      }
    };
  };

  // Facilities by floor: plates rise one by one, the one she names glows.
  TEMPLATES.slabs = function (S, p) {
    var floors = arr(p.floors), n = floors.length;
    var order = arr(p.order_bottom_up).slice();
    if (order.length !== n) order = floors.map(function (_, k) { return k; });
    var areaTop = S.H * 0.185, areaH = Math.min(S.H * 0.62, S.floorY - S.H * 0.06 - areaTop);
    var slot = Math.min(areaH / Math.max(n, 1), S.H * 0.23);
    var slabs = [], labs = [];
    floors.forEach(function (f, k) {
      var rankTop = n - 1 - order.indexOf(k);
      var y = areaTop + rankTop * slot;
      var tapId = f.tap && S.taps[f.tap] ? f.tap : null;
      var slab = el(tapId ? 'button' : 'div', 'es-slab');
      slab.style.top = Math.round(y) + 'px';
      slab.style.zIndex = String(10 + n - rankTop);
      if (f.asset) slab.appendChild(S.img(f.asset));
      slab.appendChild(el('i', 'es-glow'));
      var lab = el(tapId ? 'button' : 'div', 'es-fl');
      lab.style.top = Math.round(y + slot * 0.18) + 'px';
      lab.style.zIndex = '30';
      lab.appendChild(el('i', 'es-flmark'));
      var tx = el('div');
      tx.appendChild(el('b', null, tidy(f.label_en)));
      if (f.sub_en) tx.appendChild(el('span', null, tidy(f.sub_en)));
      lab.appendChild(tx);
      if (tapId) {
        lab.appendChild(el('i', 'es-go', '›'));
        S.bindTap(slab, tapId);
        S.bindTap(lab, tapId);
      }
      S.add(slab);
      S.add(lab);
      slabs[k] = slab;
      labs[k] = lab;
    });
    var hint = p.end_hint_en ? S.add(el('p', 'es-hint es-fade', p.end_hint_en)) : null;
    if (hint) hint.style.bottom = Math.round(S.H - S.floorY + S.H * 0.01) + 'px';
    function act(k) {
      slabs.forEach(function (s, j) { s.classList.toggle('act', j === k); });
      labs.forEach(function (l, j) { l.classList.toggle('act', j === k); });
    }
    return {
      beat: function (i) {
        floors.forEach(function (f, k) {
          if (f.line !== i) return;
          act(k);
          S.reveal(slabs[k]);
          S.later(220, function () { labs[k].classList.add('on'); });
        });
      },
      rest: function () {
        act(-1);
        slabs.forEach(function (s) { s.classList.add('on'); });
        labs.forEach(function (l) { l.classList.add('on'); });
        if (hint) hint.classList.add('on');
      }
    };
  };

  // One floor, one place at a time (owner 13 Oct: "ภาพใหญ่ทีละภาพ ตัวหนังสือ
  // ใหญ่ขึ้น ให้อ่านได้จากระยะยืน"). The plan stays on top with the ring on
  // the place she names; under it that place alone, as big as the screen
  // allows, its name in display type and what it is in one line; the next
  // slides in as she names it. A strip of thumbnails at the bottom left says
  // how many there are and which one this is; a thumbnail brings its place
  // back up big (on this screen, nothing sent), and the big card is what the
  // guest taps for that place's own story. Before: 3-5 cards of 2.9vw type
  // under the plan -- on the robot's own screen recording (13 Oct) unreadable
  // from where a guest stands. Laid out from the bottom up (Emma's head is at
  // ~80% of the height on the right), so not in BALANCED.
  TEMPLATES.drill = function (S, p) {
    var items = arr(p.items), n = items.length;
    var box = S.add(S.box(p.base_asset, 92, 21, TOP_VH, 'es-fade'));
    box.appendChild(S.img(p.base_asset, 'es-fill'));
    var spots = items.map(function (it) {
      if (!Array.isArray(it.at) || it.at.length !== 2) return null;
      var s = S.spot(it.at[0], it.at[1]);
      box.appendChild(s);
      return s;
    });
    var y = S.floorY;
    var more = p.more_en ? S.add(el('p', 'es-more es-fade', tidy(p.more_en))) : null;
    // by its own height: a two-line note ("Sea view depends on the room ...")
    // sat on the taps when this assumed one line (13 Oct recording)
    if (more) {
      var mh = more.offsetHeight || S.H * 0.022;
      more.style.top = Math.round(y - mh - S.H * 0.004) + 'px';
      y -= mh + S.H * 0.008;
    }
    // the strip stays left of Emma's corner, however many places there are
    var gap = S.W * 0.015;
    var tw = Math.min(S.W * 0.13, (S.W * 0.64 - (n - 1) * gap) / Math.max(n, 1));
    var strip = S.add(el('div', 'es-strip es-fade'));
    strip.style.top = Math.round(y - tw * 0.75 - S.H * 0.006) + 'px';
    var cardTop = box._top + box._h + S.H * 0.016;
    var cardBottom = Math.min(parseFloat(strip.style.top) - S.H * 0.014, S.H * 0.78);
    var cur = -1;
    var cards = items.map(function (it, k) {
      // a place with a story of its own (the Sky Pool, the fitness) is a
      // card the guest can tap; the server bound the tap to the item
      var tapId = it.tap && S.taps[it.tap] ? it.tap : null;
      var c = el(tapId ? 'button' : 'div', 'es-fc');
      c.style.top = Math.round(cardTop) + 'px';
      c.style.height = Math.round(cardBottom - cardTop) + 'px';
      var ph = el('div', 'es-fc-ph');
      if (it.asset) ph.appendChild(S.img(it.asset));
      if (n > 1) ph.appendChild(el('i', 'es-fc-n', (k + 1) + ' / ' + n));
      if (tapId) ph.appendChild(el('em', 'es-fc-go', (p.tap_label_en ? tidy(p.tap_label_en) + ' ' : '') + '›'));
      c.appendChild(ph);
      var tx = el('div', 'es-fc-tx');
      tx.appendChild(el('b', null, it.label_en || ''));
      if (it.sub_en) tx.appendChild(el('span', null, tidy(it.sub_en)));
      c.appendChild(tx);
      if (tapId) S.bindTap(c, tapId);
      return S.add(c);
    });
    var thumbs = items.map(function (it, k) {
      var t = el('button', 'es-thumb');
      t.type = 'button';
      t.style.width = Math.round(tw) + 'px';
      if (it.asset) t.appendChild(S.img(it.asset));
      t.addEventListener('click', function () {
        if (S.dead || !t.classList.contains('on')) return;
        // the guest's pick: the queued places do not take it back a second
        // later, and the ring follows it (review 13 Oct)
        picked = true;
        queue = [];
        shownAt = Date.now();
        light(k);
        show(k);
      });
      strip.appendChild(t);
      return t;
    });
    // the place on screen: cards before it wait (or leave) to the left, the
    // ones after it to the right -- forward slides left, a thumbnail back
    // slides right
    function show(k) {
      if (k === cur || !cards[k]) return;
      cur = k;
      cards.forEach(function (c, j) {
        if (j === k) return;
        c.classList.remove('on');
        c.classList.toggle('left', j < k);
      });
      thumbs.forEach(function (t, j) { t.classList.toggle('cur', j === k); });
      S.reveal(cards[k]);
    }
    var active = -1;
    function light(k) {
      if (active >= 0 && spots[active]) { spots[active].classList.remove('on'); spots[active].classList.add('done'); }
      if (spots[k]) spots[k].classList.add('on');
      active = k;
    }
    // Each place stays up at least DWELL_MS, in the order she names them:
    // short Thai lines ("ซาวน่าคริสตัล", "และไฮโดร...") are 0.8 s apart in
    // the recording, less than the slide itself takes -- a card that only
    // flashes past is a place the guest never saw. Lags her by tenths of a
    // second at most; the ring on the plan moves with the card.
    var DWELL_MS = 1000, queue = [], shownAt = -1e9, waiting = false, picked = false;
    function pump() {
      if (waiting || !queue.length || S.dead) return;
      var wait = shownAt + DWELL_MS - Date.now();
      if (wait > 0 && !S.instant) {
        waiting = true;
        S.later(wait, function () { waiting = false; pump(); });
        return;
      }
      var k = queue.shift();
      shownAt = Date.now();
      light(k);
      show(k);
      pump();
    }
    return {
      beat: function (i) {
        if (i === 0) { S.reveal(box); S.reveal(strip); }
        items.forEach(function (it, k) {
          if (it.line !== i) return;
          thumbs[k].classList.add('on');
          if (!picked) queue.push(k);
        });
        pump();
      },
      rest: function () {
        box.classList.add('on');
        strip.classList.add('on');
        spots.forEach(function (s) { if (s) { s.classList.remove('on'); s.classList.add('done'); } });
        thumbs.forEach(function (t) { t.classList.add('on'); });
        var last = !picked && queue.length ? queue[queue.length - 1] : cur;
        queue = [];
        show(last >= 0 ? last : 0);
        // a reveal cancelled mid-frame (the guest cut in) left no card up
        if (cards[cur]) cards[cur].classList.add('on');
        if (more) more.classList.add('on');
      }
    };
  };

  // Where is it: the picture, a zoom, a ring on the spot, then the real photo
  // (the zoomed picture shrinks into its corner) and what makes it special.
  TEMPLATES.spotlight = function (S, p) {
    var base = S.add(S.box(p.base_asset, 90, 42, TOP_VH, 'es-spbase'));
    base.style.opacity = '0';
    var zoom = el('div', 'es-zoom');
    zoom.appendChild(S.img(p.base_asset));
    base.appendChild(zoom);
    var z = p.zoom || {}, zx0 = num(z.x) != null ? z.x : 50, zy0 = num(z.y) != null ? z.y : 50;
    var scale = num(z.scale) != null && z.scale >= 1 ? z.scale : 1;
    zoom.style.transformOrigin = zx0 + '% ' + zy0 + '%';
    var sp = p.spot || {};
    // where the spot lands once zoomed: the overlay is not scaled, so the
    // ring stays a ring of the same size
    var sx = zx0 + ((num(sp.x) != null ? sp.x : 50) - zx0) * scale;
    var sy = zy0 + ((num(sp.y) != null ? sp.y : 50) - zy0) * scale;
    var inView = sx > 2 && sx < 98 && sy > 2 && sy < 98;
    var spot = inView ? S.spot(sx, sy) : null;
    if (spot) base.appendChild(spot);
    var label = el('div', 'es-label es-up', sp.label_en || '');
    if (sp.sub_en) label.appendChild(el('small', null, tidy(sp.sub_en)));
    if (sp.label_en) { base.appendChild(label); S.placeLabel(label, base, sx, sy); }
    var ph = p.photo || {}, photo = null, chips = [], rows = null, phImg = null;
    if (ph.asset) {
      // the photo in a frame of its OWN shape, sharing the picture's top and
      // width (so the zoomed picture insets into its top-right corner): in the
      // picture's frame a 2.5:1 site plan cut a 4:3 photo to a strip
      photo = S.box(ph.asset, 90, 42, TOP_VH, 'es-photo es-fade');
      if (photo._w < base._w) {
        photo._left = base._left + base._w - photo._w;
        photo.style.left = photo._left + 'px';
      }
      phImg = photo.appendChild(S.img(ph.asset, 'es-fill es-cover'));
      phImg._onBroken = function () { dropPhoto(); };
      photo.appendChild(el('i', 'es-shade'));
      var title = el('div', 'es-label', ph.title_en || '');
      if (ph.sub_en) title.appendChild(el('small', null, ph.sub_en));
      if (ph.title_en) photo.appendChild(title);
      S.root.insertBefore(photo, base);
      rows = S.add(el('div', 'es-rows narrow'));
      // the chips come with the photo, under it (the picture is inset by then)
      rows.style.top = Math.round(photo._top + photo._h + S.H * 0.025) + 'px';
      // a chip is a line of text, or {label_en, value_en}: the figure the
      // guest asked about ("Length · ≈ 155 m"), drawn like a distance row
      arr(ph.chips_en).forEach(function (c) {
        var r = el('div', 'es-r es-left');
        if (c && typeof c === 'object') {
          r.classList.add('es-key');
          r.appendChild(el('span', null, tidy(c.label_en)));
          if (c.value_en) r.appendChild(el('b', null, tidy(c.value_en)));
        } else {
          r.appendChild(el('span', null, tidy(c)));
        }
        rows.appendChild(r);
        chips.push(r);
      });
    }
    var spotLine = num(p.spot_line) != null ? p.spot_line : 0, photoLine = num(ph.line);
    var zoomed = false, photoOn = false, ringAt = 0, gone = false;
    function showBase() { S.ready(base).then(function () { S.frame(function () { base.style.opacity = '1'; }); }); }
    function ringOn() {
      if (spot) { spot.classList.remove('done'); spot.classList.add('on'); }
      label.classList.add('on');
    }
    function zoomIn() {
      if (zoomed) return;
      zoomed = true;
      if (scale > 1) zoom.style.transform = 'scale(' + scale + ')';
      ringAt = Date.now() + (scale > 1 ? 1150 : 200);
      // not once the photo is up: the end state used to bring the label
      // back inside the shrunken corner picture, ~12 px tall (review 12 Oct)
      S.later(scale > 1 ? 1150 : 200, function () { if (!photoOn || gone) ringOn(); });
    }
    function chipsOn() { chips.forEach(function (c, k) { S.later(450 + k * 230, function () { c.classList.add('on'); }); }); }
    // The photo would not load: the zoomed picture keeps its ring and label,
    // and the chips sit under IT -- not a dark frame titled for a photo
    // that is not there.
    function dropPhoto() {
      if (!photo || gone) return;
      gone = true;
      photo.classList.remove('on');
      photo.style.visibility = 'hidden';
      base.classList.remove('inset');
      rows.style.top = Math.round(base._top + base._h + S.H * 0.025) + 'px';
      if (photoOn && zoomed) ringOn();
    }
    function showPhoto() {
      if (!photo || photoOn) return;
      photoOn = true;
      if (gone) { chipsOn(); return; }
      S.reveal(photo);
      base.classList.add('inset');
      if (spot) { spot.classList.remove('on'); spot.classList.add('done'); }
      label.classList.remove('on');
      chipsOn();
    }
    return {
      beat: function (i) {
        if (i === 0) showBase();
        if (i === spotLine) S.later(i === 0 ? 500 : 0, zoomIn);
        if (photoLine != null && i === photoLine) {
          if (!zoomed) zoomIn();
          // the ring gets ~1.5 s on screen before the cut to the photo, even
          // when the next line comes quickly ("Sky Pool is on level 3" is two
          // seconds): the photo may trail her voice a little, never lead it
          S.later(Math.max(i === spotLine ? 1800 : 0, ringAt + 1500 - Date.now()), showPhoto);
        }
      },
      rest: function () {
        base.style.opacity = '1';
        zoomIn();
        if (!photo || gone) ringOn();
        showPhoto();
        if (photo && !gone) photo.classList.add('on');   // its reveal frame may have been cancelled
        chips.forEach(function (c) { c.classList.add('on'); });
      }
    };
  };

  // Built from the ground: the hologram frames play, the real render rises
  // in from the ground, the floors tick, a meter runs up, the numbers count.
  TEMPLATES.assembly = function (S, p) {
    var frames = arr(p.frames), fin = p.final_asset || null;
    var box = S.add(S.box(fin || frames[frames.length - 1], 94, 40, TOP_VH, 'es-fade'));
    box.style.background = '#03050f';
    var fwrap = el('div', 'es-frames es-fill');
    var fimgs = frames.map(function (f) { var im = S.img(f, 'es-frame es-fill es-cover'); fwrap.appendChild(im); return im; });
    box.appendChild(fwrap);
    var rise = null;
    // `final_mode: "fade"` when the real render is a different camera from
    // the hologram (a wipe from the ground would slide one building across
    // another); the default rise is for the same camera, pixel on pixel.
    var fade = p.final_mode === 'fade';
    // `rise_line`: the real render waits for that line (the hologram rests on
    // its last frame until then); without it, it follows the frames at once.
    var riseLine = num(p.rise_line) != null ? p.rise_line : null;
    if (fin) {
      rise = el('div', 'es-rise' + (fade ? ' fade' : ''));
      var rin = el('div', 'es-rise-in');
      rin.appendChild(S.img(fin));
      rise.appendChild(rin);
      box.appendChild(rise);
    }
    var gy = num(p.ground_y), ry = num(p.roof_y), lines = gy != null && ry != null && gy > ry;
    var ground = null;
    if (gy != null) { ground = el('i', 'es-hline'); ground.style.top = gy + '%'; box.appendChild(ground); }
    var counters = arr(p.counters), figs = [], ticks = [], meter = null, mlab = null;
    var below = S.add(el('div', 'es-below'));
    below.style.top = Math.round(box._top + box._h + S.H * 0.03) + 'px';
    var figRow = below.appendChild(el('div', 'es-figs'));
    counters.forEach(function (c, k) {
      var fig = el('div', 'es-fig es-up');
      var nn = el('div', 'es-n');
      if (c.prefix_en) nn.appendChild(document.createTextNode(c.prefix_en + ' '));
      var val = el('span', null, '0');
      nn.appendChild(val);
      if (c.unit_en) nn.appendChild(el('small', null, c.unit_en));
      fig.appendChild(nn);
      if (c.label_en) fig.appendChild(el('div', 'es-u', c.label_en));
      if (k % 2) fig.style.textAlign = 'right';        // two columns (.es-figs)
      figRow.appendChild(fig);
      var isFloors = c.ticks === true || (c.ticks !== false && /floor/i.test(c.unit_en || ''));
      var isMeter = c.meter === true || (c.meter !== false && /^(m|metres?|meters?)$/i.test(c.unit_en || ''));
      var own = [];
      if (lines && isFloors && num(c.to) && c.to <= 60) {
        for (var f = 0; f <= c.to; f++) {
          var t = el('i', 'es-tick');
          t.style.top = (gy - (gy - ry) * f / c.to) + '%';
          box.appendChild(t);
          own.push(t);
        }
      }
      if (lines && isMeter && !meter) {
        meter = el('i', 'es-meter');
        meter.style.top = ry + '%';
        meter.style.height = (gy - ry) + '%';
        // `meter_x`: where on the picture the meter stands (a gap between
        // buildings), % of the picture's width; the label sits on its open side
        var mx = num(p.meter_x) != null && p.meter_x >= 0 && p.meter_x <= 100 ? p.meter_x : 7;
        meter.style.left = mx + '%';
        box.appendChild(meter);
        mlab = el('div', 'es-label es-up', valueText({ to: c.to, dec: c.dec || 0, unit_en: (c.unit_en || '').toLowerCase(), prefix_en: c.prefix_en }));
        if (mx <= 60) mlab.style.left = (mx + 3) + '%';
        else { mlab.style.left = (mx - 3) + '%'; mlab.style.translate = '-100% 0'; }
        mlab.style.top = ((gy + ry) / 2 - 4) + '%';
        box.appendChild(mlab);
      }
      figs.push({ c: c, fig: fig, val: val, ticks: own, meter: isMeter && meter && !meter._owner ? (meter._owner = true, meter) : null });
    });
    var chipRow = below.appendChild(el('div', 'es-chips'));
    var chips = arr(p.chips).map(function (c) {
      var ch = el('b', 'es-chip es-pop', c.label_en || '');
      chipRow.appendChild(ch);
      return ch;
    });
    var built = false, risen = false;
    function riseNow() {
      if (risen) return;
      risen = true;
      if (rise) S.reveal(rise, function () { S.later(900, function () { fwrap.classList.add('gone'); }); });
      else fimgs.forEach(function (im, k) { im.classList.toggle('on', k === fimgs.length - 1); });
    }
    function build() {
      if (built) return;
      built = true;
      S.reveal(box);
      if (ground) S.later(150, function () { ground.classList.add('on'); });
      if (S.instant) return;              // rest() puts the final picture up
      var ms = num(p.frame_ms) || 160;
      // the frames play first, whatever line comes next (riseAtLine waits)
      framesDone = false;
      function framesOver() { framesDone = true; if (riseLine == null || riseDue) riseNow(); }
      if (!fimgs.length) { S.later(300, framesOver); return; }
      S.ready(fwrap).then(function () {
        if (risen || S.endedInstant) return;
        fimgs.forEach(function (im, k) {
          S.later(k * ms, function () {
            if (risen) return;
            if (k) fimgs[k - 1].classList.remove('on');
            im.classList.add('on');
          });
        });
        S.later(fimgs.length * ms + 150, framesOver);
      });
    }
    var framesDone = true, riseDue = false;
    // the line that brings the real render: after the frames have played
    function riseAtLine() {
      riseDue = true;
      if (framesDone) riseNow();
    }
    function showFig(f) {
      f.fig.classList.add('on');
      S.count(f.val, 0, num(f.c.to) || 0, f.c.dec || 0, 1300);
      f.ticks.forEach(function (t, k) { S.later(k * 110, function () { t.classList.add('on'); }); });
      if (f.meter) { f.meter.classList.add('on'); S.later(800, function () { mlab.classList.add('on'); }); }
    }
    return {
      beat: function (i) {
        if (i === 0) build();
        if (riseLine != null && i === riseLine) riseAtLine();
        figs.forEach(function (f) { if (f.c.line === i) showFig(f); });
        var j = 0;
        arr(p.chips).forEach(function (c, k) {
          if (c.line === i) { var at = j++; S.later(at * 120, function () { chips[k].classList.add('on'); }); }
        });
      },
      rest: function (instant) {
        build();
        box.classList.add('on');          // its reveal frame may have been cancelled
        if (instant || !fimgs.length || risen) {
          risen = true;
          if (rise) rise.classList.add('on');
          fwrap.classList.add('gone');
        } else {
          S.later(0, riseNow);
        }
        if (ground) ground.classList.add('on');
        figs.forEach(function (f) {
          if (!f.fig.classList.contains('on')) showFig(f);
          f.ticks.forEach(function (t) { t.classList.add('on'); });
        });
        if (mlab) mlab.classList.add('on');
        if (meter) meter.classList.add('on');
        chips.forEach(function (c) { c.classList.add('on'); });
      }
    };
  };

  // One room: the blue wire draws, the solid room fills it, the area counts
  // up, then the mood pictures (labelled as mood, not this room).
  TEMPLATES.details = function (S, p) {
    var ref = p.solid_asset || p.wire_asset;
    var box = S.add(S.box(ref, 90, 35, TOP_VH, 'es-fade'));
    box.style.background = '#070a1f';
    if (p.wire_asset) box.appendChild(S.img(p.wire_asset, 'es-fill es-contain'));
    var solid = p.solid_asset ? S.img(p.solid_asset, 'es-fill es-contain es-solid') : null;
    if (solid) box.appendChild(solid);
    ['t', 'r', 'b', 'l'].forEach(function (s) { box.appendChild(el('i', 'es-edge ' + s)); });
    var below = S.add(el('div', 'es-below'));
    below.style.top = Math.round(box._top + box._h + S.H * 0.022) + 'px';
    below.style.bottom = Math.round(S.H - S.floorY) + 'px';
    var area = below.appendChild(el('div', 'es-area es-up'));
    var v = p.value ? S.valueNode(p.value, 'es-n') : null;
    if (v) area.appendChild(v.node);
    var meta = el('div', 'es-m');
    arr(p.meta_en).forEach(function (t) { meta.appendChild(el('div', null, t)); });
    area.appendChild(meta);
    var moods = arr(p.mood_assets).slice(0, 3), tag = null, mood = null;
    if (moods.length) {
      tag = below.appendChild(el('p', 'es-tag es-fade', p.mood_tag_en || 'Interior mood'));
      mood = below.appendChild(el('div', 'es-mood es-up'));
      if (moods.length === 1) mood.style.gridTemplateColumns = '1fr';
      moods.forEach(function (m) { var mi = el('div', 'es-mi'); mi.appendChild(S.img(m)); mood.appendChild(mi); });
      // two: side by side in one row (the first spanning two rows left an
      // empty cell under the second, review 12 Oct)
      if (moods.length === 2) { mood.style.gridTemplateRows = '1fr'; mood.children[0].style.gridRow = 'auto'; }
    }
    // `lines`: three entries = [wire, solid + area, mood]; four = [wire,
    // solid, area, mood] (the area counts on the line that says the size,
    // not on the one before it). A later stage brings every earlier one.
    var stages = arr(p.lines).length ? arr(p.lines).slice(0, 4) : [0, 1, 2];
    var steps = [
      function () { S.reveal(box, function () { S.later(150, function () { box.classList.add('drawn'); }); }); },
      function () { if (solid) S.reveal(solid); },
      function () { area.classList.add('on'); if (v) S.later(300, v.go); },
      function () { if (tag) tag.classList.add('on'); if (mood) S.reveal(mood); }
    ];
    var plan = stages.length >= 4 ? [[0], [1], [2], [3]] : [[0], [1, 2], [3]];
    var done = [];
    function stage(k) {
      for (var j = 0; j <= k && j < plan.length; j++) {
        if (done[j]) continue;
        done[j] = true;
        plan[j].forEach(function (s) { steps[s](); });
      }
    }
    return {
      beat: function (i) {
        if (i === 0 && stages[0] !== 0) stage(0);
        stages.forEach(function (line, k) { if (line === i && k < plan.length) stage(k); });
      },
      rest: function () { stage(plan.length - 1); box.classList.add('on', 'drawn'); if (solid) solid.classList.add('on'); if (mood) mood.classList.add('on'); }
    };
  };

  // How far: the map, the pin, a route drawn to it, then the places nearest
  // first as she says them.
  TEMPLATES.distance = function (S, p) {
    var box = S.add(S.box(p.map_asset, 92, 42, TOP_VH, 'es-fade'));
    box.appendChild(S.img(p.map_asset, 'es-fill es-cover'));
    var pin = p.pin || null, to = p.route_to || null, spot = null, plabel = null, route = null, rlabel = null;
    var pinUp = true;
    function lab(text, x, y, prefer) {
      var l = el('div', 'es-label es-up', text);
      box.appendChild(l);
      l._up = S.placeLabel(l, box, x, y, prefer);
      return l;
    }
    if (pin && num(pin.x) != null && num(pin.y) != null) {
      spot = S.spot(pin.x, pin.y);
      box.appendChild(spot);
      if (pin.label_en) { plabel = lab(pin.label_en, pin.x, pin.y); pinUp = plabel._up; }
    }
    if (spot && to && num(to.x) != null && num(to.y) != null) {
      var x1 = to.x / 100 * box._w, y1 = to.y / 100 * box._h, x2 = pin.x / 100 * box._w, y2 = pin.y / 100 * box._h;
      var len = Math.hypot(x2 - x1, y2 - y1), ang = Math.atan2(y2 - y1, x2 - x1);
      route = el('i', 'es-route');
      route.style.left = x1.toFixed(1) + 'px';
      route.style.top = y1.toFixed(1) + 'px';
      route.style.width = len.toFixed(1) + 'px';
      route._ang = ang.toFixed(4);
      route.style.transform = 'rotate(' + route._ang + 'rad) scaleX(0)';
      box.appendChild(route);
      // the route's label (a distance) above the middle of the route, not
      // at its end, where it sat on the map's own printed place name
      // (on the other side from the pin's own label, which it met head on)
      if (to.label_en) rlabel = lab(to.label_en, (to.x + pin.x) / 2, (to.y + pin.y) / 2, pinUp ? 'below' : 'above');
    }
    var list = S.add(el('div', 'es-rows narrow'));
    list.style.top = Math.round(box._top + box._h + S.H * 0.025) + 'px';
    var rows = arr(p.rows).map(function (r) {
      var d = el('div', 'es-r es-left');
      d.appendChild(el('span', null, tidy(r.label_en)));
      if (r.value_en) d.appendChild(el('b', null, tidy(r.value_en)));
      list.appendChild(d);
      return d;
    });
    function drawRoute() { if (route) route.style.transform = 'rotate(' + route._ang + 'rad) scaleX(1)'; }
    // The route (and its label, often a distance) waits for the line that
    // says it -- "≈ 0.6 km" on the map while she is still saying "Jomtien"
    // is the picture leading her voice. Without `route_to.line`: at once.
    var routeLine = to && num(to.line) != null ? to.line : 0;
    function routeNow(delay) {
      S.later(delay, drawRoute);
      S.later(delay + 800, function () { if (rlabel) rlabel.classList.add('on'); });
    }
    return {
      beat: function (i) {
        if (i === 0) {
          S.reveal(box);
          S.later(500, function () { if (spot) spot.classList.add('on'); if (plabel) plabel.classList.add('on'); });
        }
        if (i === routeLine) routeNow(i === 0 ? 1000 : 0);
        var j = 0;
        arr(p.rows).forEach(function (r, k) {
          if (r.line === i) { var at = j++; S.later(at * 260, function () { rows[k].classList.add('on'); }); }
        });
      },
      rest: function () {
        box.classList.add('on');
        if (spot) spot.classList.add('on');
        if (plabel) plabel.classList.add('on');
        drawRoute();
        if (rlabel) rlabel.classList.add('on');
        rows.forEach(function (r) { r.classList.add('on'); });
      }
    };
  };

  // One big number over a picture, then what it is made of.
  // The picture keeps its own shape, like every other template's (a fixed
  // 92% x 38% frame cut a 2.5:1 hologram to a soft blocky fragment and the
  // parking plan to an empty dark panel, review 12 Oct). A wide picture --
  // shorter than ~30% of the screen -- keeps the number UNDER it: a 17vw
  // number over the bottom of a 470 px band hid what the band shows.
  TEMPLATES.metric = function (S, p) {
    var box = S.add(S.box(p.bg_asset, 92, 38, TOP_VH, 'es-fade'));
    var h = box._h, over = h >= S.H * 0.3;
    if (p.bg_asset) box.appendChild(S.img(p.bg_asset, 'es-fill es-contain'));
    if (over) box.appendChild(el('i', 'es-shade-all'));
    var big = el('div', 'es-big-n es-up' + (over ? '' : ' under'));
    var v = p.value ? S.valueNode(p.value, 'es-n') : null;
    // The picture and its caption open with her voice; the number itself
    // waits for the line that says it (`line`) -- "≈ 224" counting while she
    // is still saying "our parking is underground" is the picture leading.
    if (v) { v.node.classList.add('es-fade'); big.appendChild(v.node); }
    if (p.label_en) big.appendChild(el('p', null, p.label_en));
    var below = box._top + h;
    if (over) {
      box.appendChild(big);
    } else {
      big.style.top = Math.round(below + S.H * 0.02) + 'px';
      S.add(big);
      below = big.offsetTop + (big.offsetHeight || S.H * 0.12);
    }
    var parts = arr(p.parts), max = 0;
    parts.forEach(function (q) { if (num(q.to) > max) max = q.to; });
    var list = S.add(el('div', 'es-rows narrow'));
    list.style.top = Math.round(below + S.H * 0.025) + 'px';
    var rows = parts.map(function (q) {
      var r = el('div', 'es-r es-part es-left');
      var bar = el('i', 'es-bar');
      r.appendChild(bar);
      r.appendChild(el('span', null, q.label_en || ''));
      var b = el('b');
      if (q.prefix_en) b.appendChild(document.createTextNode(q.prefix_en + ' '));
      var n = el('span', null, '0');
      b.appendChild(n);
      if (q.unit_en) b.appendChild(el('small', null, ' ' + q.unit_en));
      r.appendChild(b);
      list.appendChild(r);
      return { r: r, bar: bar, b: n, q: q };
    });
    var line = num(p.line) != null ? p.line : 0, opened = false, counted = false;
    function showPart(x) {
      if (x.r.classList.contains('on')) return;
      x.r.classList.add('on');
      x.bar.style.transform = 'scaleX(' + (max > 0 ? (num(x.q.to) || 0) / max : 0).toFixed(4) + ')';
      S.count(x.b, 0, num(x.q.to) || 0, x.q.dec || 0, 900);
    }
    function countNow() {
      if (counted) return;
      counted = true;
      if (v) { v.node.classList.add('on'); S.later(250, v.go); }
    }
    function open(then) {
      if (opened) { if (then) then(); return; }
      opened = true;
      S.reveal(box, function () { big.classList.add('on'); if (then) then(); });
    }
    return {
      beat: function (i) {
        if (i === 0) open(line === 0 ? countNow : null);
        else if (i === line) open(countNow);
        var j = 0;
        rows.forEach(function (x) {
          var at = num(x.q.line);
          if (at === i || (at == null && i === line)) {
            var k = j++;
            S.later((at === line || at == null ? 1100 : 0) + k * 350, function () { showPart(x); });
          }
        });
      },
      rest: function () {
        open(countNow);
        box.classList.add('on');
        big.classList.add('on');
        rows.forEach(showPart);
      }
    };
  };

  // A set of features: each one pops in as she names it.
  TEMPLATES.features = function (S, p) {
    var items = arr(p.items).slice(0, 6);
    var grid = S.add(el('div', 'es-feats'));
    grid.style.top = Math.round(S.H * TOP_VH / 100) + 'px';
    // one card: one column. Two side by side, not stacked: two full-width
    // 16:10 cards ran 1,300 px down into Emma's corner and under the
    // footnote on the robot's screen (smart_home, 13 Oct)
    if (items.length <= 1) grid.style.gridTemplateColumns = '1fr';
    var cards = items.map(function (it) {
      var c = el('div', 'es-card es-feat es-pop');
      var ci = el('div', 'es-cimg');
      if (it.asset) ci.appendChild(S.img(it.asset));
      c.appendChild(ci);
      c.appendChild(el('span', null, it.label_en || ''));
      if (it.note_en) c.appendChild(el('small', null, tidy(it.note_en)));
      grid.appendChild(c);
      return c;
    });
    var foot = p.footnote_en ? S.add(el('p', 'es-foot es-fade', p.footnote_en)) : null;
    if (foot) foot.style.bottom = Math.round(S.H - S.floorY + S.H * 0.01) + 'px';
    // `footnote_line`: the line that says it ("an optional package, not
    // included in the room price") puts it up; without one, the overview does
    var footLine = num(p.footnote_line) != null ? p.footnote_line : null, any = false;
    return {
      beat: function (i) {
        var j = 0;
        items.forEach(function (it, k) {
          if (it.line === i) { any = true; var at = j++; S.later(at * 200, function () { S.reveal(cards[k]); }); }
        });
        // an opening line that names none of them: the empty frames wait
        if (!any) cards.forEach(function (c) { if (!c.classList.contains('on')) c.classList.add('ghost'); });
        if (foot && i === footLine) foot.classList.add('on');
      },
      rest: function () {
        cards.forEach(function (c) { c.classList.add('on'); });
        if (foot) foot.classList.add('on');
      }
    };
  };

  // ---------------------------------------------- drawn, not photographed
  // Owner 13 Oct, of the map stories: "ไม่ค่อยสวย ... ดูแล้วเข้าใจได้ง่าย" and
  // "ไม่ต้องใช้รูปภาพแต่ให้เป็น motion". A slide's map is dense, printed in a
  // style of its own, its places named in its own type -- a pin on it read as
  // "somewhere in there". These two draw the answer from plain shapes: no
  // picture to decode, every element placed by the catalog's numbers.

  var SVGNS = 'http://www.w3.org/2000/svg';
  // Line icons on a 24-unit grid, one path each (stroke only).
  var ICONS = {
    home: 'M4 20V10.5L12 4l8 6.5V20M9.5 20v-5.5h5V20',
    mall: 'M5.5 8.5h13l-1.2 11.5H6.7zM9 8.5V7a3 3 0 0 1 6 0v1.5',
    plane: 'M12 3.2c.9 0 1.4.8 1.4 1.8v4.3l6.6 4v1.9l-6.6-1.9v4.2l2 1.5v1.6L12 19.7l-3.4.9V19l2-1.5v-4.2L4 15.2v-1.9l6.6-4V5c0-1 .5-1.8 1.4-1.8z',
    beach: 'M3 18.5c1.5 0 1.5-1.1 3-1.1s1.5 1.1 3 1.1 1.5-1.1 3-1.1 1.5 1.1 3 1.1 1.5-1.1 3-1.1 1.5 1.1 3 1.1M15.5 10.5a3.5 3.5 0 1 0-7 0',
    park: 'M12 21v-6.5M12 3.5a4.5 4.5 0 0 0-4.3 5.8A4 4 0 0 0 9 16.5h6a4 4 0 0 0 1.3-7.2A4.5 4.5 0 0 0 12 3.5z',
    road: 'M9.5 3 6 21M14.5 3 18 21M12 5v2.5M12 11v2.5M12 17v2.5',
    // nearby_places (13 Oct): without them hospitals and schools fell back to
    // the house -- the very icon that marks the project
    hospital: 'M5 20V8.5h14V20M3.5 20h17M12 11v5M9.5 13.5h5M9 8.5V5h6v3.5',
    school: 'M3 9.5 12 5l9 4.5-9 4.5zM7 11.6V16c1.5 1.4 3.2 2 5 2s3.5-.6 5-2v-4.4M21 9.5v5'
  };
  function icon(name) {
    var s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('class', 'es-ico');
    s.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(SVGNS, 'path');
    path.setAttribute('d', ICONS[name] || ICONS.home);
    s.appendChild(path);
    return s;
  }

  // Travel times (location_travel). From the project at the top, a line runs
  // down past each place; as she names one, its row slides in and a dot
  // drives along its bar to the drive time -- one scale for every row
  // (`bar_max`, minutes), so near looks near and the airport looks far. A
  // range ("12–17 min": free-flow to rush hour) is a solid bar to its low
  // end and a lighter one to its high end. No picture at all.
  TEMPLATES.journey = function (S, p) {
    var rows = arr(p.rows), max = num(p.bar_max) || 0;
    rows.forEach(function (r) { arr(r.bar).forEach(function (v) { if (num(v) > max) max = v; }); });
    // two places (a narrow question's story): bigger rows, the same scale
    var wrap = S.add(el('div', 'es-jr' + (rows.length <= 2 ? ' roomy' : '')));
    wrap.style.top = Math.round(S.H * (TOP_VH + 1) / 100) + 'px';
    var spine = el('i', 'es-jr-spine');
    wrap.appendChild(spine);
    var head = el('div', 'es-jr-o es-up');
    var hn = el('i', 'es-jr-node es-jr-home');
    hn.appendChild(icon('home'));
    head.appendChild(hn);
    var ht = el('div', 'es-jr-n');
    ht.appendChild(el('b', null, p.origin_en || ''));
    if (p.origin_sub_en) ht.appendChild(el('span', null, tidy(p.origin_sub_en)));
    head.appendChild(ht);
    wrap.appendChild(head);
    function frac(v) { return max > 0 && num(v) != null ? Math.max(0, Math.min(1, v / max)) : 0; }
    var items = rows.map(function (r) {
      var row = el('div', 'es-jr-r es-left');
      var node = el('i', 'es-jr-node');
      node.appendChild(icon(r.icon));
      row.appendChild(node);
      // the name (and its km) on one line; under it the bar, with the
      // time at its end -- a name beside a "≈ 12–17 min" wrapped to two lines
      var body = el('div', 'es-jr-b');
      var name = el('div', 'es-jr-n');
      name.appendChild(el('b', null, r.label_en || ''));
      if (r.sub_en) name.appendChild(el('span', null, tidy(r.sub_en)));
      body.appendChild(name);
      var go = el('div', 'es-jr-g');
      var bar = arr(r.bar), lo = frac(bar[0]), hi = frac(bar[bar.length - 1]);
      var track = el('div', 'es-jr-track');
      // the bars clip to the rounded lane; the dot rides above it unclipped
      var lane = el('i', 'es-jr-lane'), span = el('i', 'es-jr-span'), fill = el('i', 'es-jr-fill');
      var run = el('i', 'es-jr-run');
      run.appendChild(el('i', 'es-jr-dot'));
      lane.appendChild(span);
      lane.appendChild(fill);
      track.appendChild(lane);
      track.appendChild(run);
      go.appendChild(track);
      var val = r.value_en ? el('em', 'es-jr-v es-pop', tidy(r.value_en)) : null;
      if (val) go.appendChild(val);
      body.appendChild(go);
      row.appendChild(body);
      // the farther, the longer the drive (0.7-2.1 s), so the dot's speed
      // reads the same on every row
      row.style.setProperty('--d', (0.7 + 1.4 * hi).toFixed(2) + 's');
      wrap.appendChild(row);
      return { row: row, val: val, span: span, fill: fill, run: run, lo: lo, hi: hi, line: r.line };
    });
    function drive(x) {
      if (x.driven) return;
      x.driven = true;
      x.fill.style.transform = 'scaleX(' + x.lo.toFixed(4) + ')';
      x.span.style.transform = 'scaleX(' + x.hi.toFixed(4) + ')';
      x.run.style.transform = 'translate3d(' + (x.hi * 100).toFixed(2) + '%, 0, 0)';
    }
    // the line from the project grows down to each place as it is named
    function reach(x) {
      if (num(x.f) != null) spine.style.transform = 'scaleY(' + x.f.toFixed(4) + ')';
    }
    function show(x, delay) {
      S.later(delay, function () {
        reach(x);
        x.row.classList.add('on');
        S.later(280, function () { drive(x); });
        if (x.val) S.later(280 + (0.7 + 1.4 * x.hi) * 1000 - 150, function () { x.val.classList.add('on'); });
      });
    }
    var opened = false;
    function open() {
      if (opened) return;
      opened = true;
      head.classList.add('on');
    }
    return {
      // the line runs from the project's node to the last row's; each row
      // knows how far down it is (`f`) so the line can stop at it
      layout: function () {
        var mid = function (n) { return n.offsetTop + (n.offsetHeight || 0) / 2; };
        var y0 = mid(head), y1 = items.length ? mid(items[items.length - 1].row) : y0;
        spine.style.top = Math.round(y0) + 'px';
        spine.style.height = Math.max(0, Math.round(y1 - y0)) + 'px';
        items.forEach(function (x) { x.f = y1 > y0 ? Math.max(0, Math.min(1, (mid(x.row) - y0) / (y1 - y0))) : 1; });
        // six places (nearby_places) ran under Emma and pushed the taps off
        var k = S.fitScale(wrap, S.contentBottom());
        if (k < 1) {
          wrap.style.transformOrigin = '0 0';
          wrap.style.transform = 'scale(' + k.toFixed(4) + ')';
        }
      },
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        items.forEach(function (x) {
          if (x.line === i) show(x, (i === 0 ? 450 : 0) + 380 * j++);
        });
      },
      rest: function () {
        open();
        spine.classList.add('on');
        spine.style.transform = 'scaleY(1)';
        items.forEach(function (x) { x.row.classList.add('on'); drive(x); if (x.val) x.val.classList.add('on'); });
      }
    };
  };

  // Where it is (location_beach): a schematic map drawn on the screen -- the
  // sea slides in, the beach and the roads draw themselves, the project's
  // marker lands; the park next door grows as she names it, and each
  // distance is a dashed line that runs from the project to its place with
  // the figure on it. Positions are % of the map (`x`, `y`; a road or a
  // measure from x1,y1 to x2,y2) and say "which side", not "how far": the
  // map says "not to scale" on itself, and the figures are the catalog's.
  TEMPLATES.neighborhood = function (S, p) {
    var W = Math.round(S.W * 0.92), H = Math.round(W * (num(p.aspect) || 0.86));
    var map = S.add(el('div', 'es-nb es-fade'));
    map._w = W; map._h = H;
    map.style.width = W + 'px';
    map.style.height = H + 'px';
    map.style.left = Math.round((S.W - W) / 2) + 'px';
    map.style.top = Math.round(S.H * TOP_VH / 100) + 'px';
    function pct(v) { return (num(v) != null ? Math.max(-5, Math.min(105, v)) : 0) + '%'; }
    function at(i, line) { return num(line) != null ? line === i : i === 0; }
    var sea = p.sea && num(p.sea.to_x) != null ? el('i', 'es-nb-sea') : null;
    if (sea) { sea.style.width = pct(p.sea.to_x); map.appendChild(sea); }
    var beach = p.beach && num(p.beach.to_x) != null ? el('i', 'es-nb-beach') : null;
    if (beach) {
      var from = sea ? p.sea.to_x : 0;
      beach.style.left = pct(from);
      beach.style.width = pct(p.beach.to_x - from);
      map.appendChild(beach);
    }
    var drawn = [];       // {node, line, delay}: everything that appears on a line
    function line(x1, y1, x2, y2, cls) {
      var ax = x1 / 100 * W, ay = y1 / 100 * H, bx = x2 / 100 * W, by = y2 / 100 * H;
      var d = el('i', cls);
      d.style.left = ax.toFixed(1) + 'px';
      d.style.top = ay.toFixed(1) + 'px';
      d.style.width = Math.hypot(bx - ax, by - ay).toFixed(1) + 'px';
      d._ang = Math.atan2(by - ay, bx - ax).toFixed(4);
      d.style.transform = 'rotate(' + d._ang + 'rad) scaleX(0)';
      return d;
    }
    function draw(d) { if (d._ang != null) d.style.transform = 'rotate(' + d._ang + 'rad) scaleX(1)'; d.classList.add('on'); }
    // a label in the map, kept inside it -- measured all at once at the end
    // (`placeTags`): one read per label between writes forced a layout per
    // label, a 217 ms frame as the map opened on the robot (13 Oct)
    var tags = [];
    function tag(text, x, y, cls) {
      var t = el('div', cls, tidy(text));
      map.appendChild(t);
      tags.push({ t: t, x: x, y: y });
      return t;
    }
    function placeTags() {
      var sizes = tags.map(function (g) { return [g.t.offsetWidth || W * 0.3, g.t.offsetHeight || W * 0.08]; });
      tags.forEach(function (g, k) {
        var w = sizes[k][0], h = sizes[k][1];
        g.t.style.left = Math.round(Math.max(w / 2 + 8, Math.min(W - w / 2 - 8, g.x / 100 * W))) + 'px';
        g.t.style.top = Math.round(Math.max(h / 2 + 8, Math.min(H - h / 2 - 8, g.y / 100 * H))) + 'px';
      });
    }
    // the streets of the scene draw one after another as it opens; a road
    // she names (`line`) draws as she says it, before its distance runs
    var streets = 0;
    arr(p.roads).forEach(function (r) {
      if ([r.x1, r.y1, r.x2, r.y2].some(function (v) { return num(v) == null; })) return;
      var d = line(r.x1, r.y1, r.x2, r.y2, 'es-nb-road' + (r.label_en ? ' main' : ''));
      map.appendChild(d);
      var delay = num(r.line) != null && r.line > 0 ? 0 : 250 + 120 * streets++;
      drawn.push({ node: d, line: r.line, delay: delay, draw: true });
      if (r.label_en) {
        var lx = num(r.label_x) != null ? r.label_x : (r.x1 + r.x2) / 2;
        var ly = num(r.label_y) != null ? r.label_y : Math.min(r.y1, r.y2) + 8;
        drawn.push({ node: tag(r.label_en, lx, ly, 'es-nb-tag es-pop'), line: r.line, delay: delay + 500 });
      }
    });
    if (p.beach && p.beach.label_en && beach) {
      // along the sand, reading upwards (placed by %, not by its size)
      var bt = map.appendChild(el('div', 'es-nb-vtag es-fade', tidy(p.beach.label_en)));
      bt.style.left = ((sea ? p.sea.to_x : 0) + (p.beach.to_x - (sea ? p.sea.to_x : 0)) / 2) + '%';
      bt.style.top = pct(num(p.beach.label_y) != null ? p.beach.label_y : 62);
      drawn.push({ node: bt, line: p.beach.line, delay: 900 });
    }
    if (p.area_label_en) drawn.push({ node: tag(p.area_label_en, num(p.area_x) != null ? p.area_x : 70,
                                               num(p.area_y) != null ? p.area_y : 7, 'es-nb-area es-fade'),
                                      line: null, delay: 600 });
    var marker = null;
    arr(p.places).forEach(function (pl) {
      if (num(pl.x) == null || num(pl.y) == null) return;
      if (pl.kind === 'park' && num(pl.w) != null && num(pl.h) != null) {
        var park = el('div', 'es-nb-park es-pop');
        park.style.left = pct(pl.x); park.style.top = pct(pl.y);
        park.style.width = pct(pl.w); park.style.height = pct(pl.h);
        park.appendChild(el('i', 'es-nb-lake'));
        map.appendChild(park);
        drawn.push({ node: park, line: pl.line, delay: 0 });
        if (pl.label_en) drawn.push({ node: tag(pl.label_en, pl.x + pl.w / 2, pl.y + pl.h + 6, 'es-nb-tag park es-up'),
                                      line: pl.line, delay: 350 });
        return;
      }
      // the project: a marker that lands, its name above it
      var m = el('div', 'es-nb-pin es-pop');
      m.style.left = pct(pl.x); m.style.top = pct(pl.y);
      var ring = S.spot(50, 50);
      m.appendChild(ring);
      var hm = el('i', 'es-jr-node es-jr-home');
      hm.appendChild(icon('home'));
      m.appendChild(hm);
      map.appendChild(m);
      marker = { m: m, ring: ring };
      drawn.push({ node: m, line: pl.line, delay: 650, then: function () { ring.classList.add('on'); } });
      if (pl.label_en) drawn.push({ node: tag(pl.label_en, pl.x, pl.y - 9, 'es-nb-tag project es-up'),
                                    line: pl.line, delay: 850 });
    });
    arr(p.measures).forEach(function (ms) {
      if ([ms.x1, ms.y1, ms.x2, ms.y2].some(function (v) { return num(v) == null; })) return;
      var d = line(ms.x1, ms.y1, ms.x2, ms.y2, 'es-nb-measure');
      map.appendChild(d);
      drawn.push({ node: d, line: ms.line, delay: 450, draw: true });
      var end = el('i', 'es-nb-end es-pop');
      end.style.left = pct(ms.x2); end.style.top = pct(ms.y2);
      map.appendChild(end);
      drawn.push({ node: end, line: ms.line, delay: 1300 });
      if (ms.label_en) {
        var mx = num(ms.label_x) != null ? ms.label_x : (ms.x1 + ms.x2) / 2;
        var my = num(ms.label_y) != null ? ms.label_y : (ms.y1 + ms.y2) / 2 + 7;
        drawn.push({ node: tag(ms.label_en, mx, my, 'es-nb-pill es-pop'), line: ms.line, delay: 1400 });
      }
    });
    if (p.compass !== false) map.appendChild(el('div', 'es-nb-n', 'N'));
    var note = p.footnote_en ? el('div', 'es-nb-note', p.footnote_en) : null;
    if (note) {
      if (num(p.footnote_x) != null) note.style.left = pct(p.footnote_x);
      map.appendChild(note);
    }
    placeTags();
    // the legend under the map: what each figure is, nearest first
    var list = S.add(el('div', 'es-nb-rows'));
    list.style.gridTemplateColumns = 'repeat(' + Math.max(1, arr(p.rows).length) + ', minmax(0, 1fr))';
    list.style.top = Math.round(S.H * TOP_VH / 100 + H + S.H * 0.022) + 'px';
    var rows = arr(p.rows).map(function (r) {
      var d = el('div', 'es-nb-r es-up');
      var ic = el('i', 'es-jr-node');
      ic.appendChild(icon(r.icon));
      d.appendChild(ic);
      if (r.value_en) d.appendChild(el('b', null, tidy(r.value_en)));
      d.appendChild(el('span', null, r.label_en || ''));
      if (r.sub_en) d.appendChild(el('small', null, tidy(r.sub_en)));
      list.appendChild(d);
      return { node: d, line: r.line };
    });
    function put(x) {
      if (x.done) return;
      x.done = true;
      if (x.draw) draw(x.node); else x.node.classList.add('on');
      if (x.then) S.later(400, x.then);
    }
    var opened = false;
    function open() {
      if (opened) return;
      opened = true;
      S.reveal(map, function () {
        if (sea) sea.classList.add('on');
        S.later(300, function () { if (beach) beach.classList.add('on'); });
      });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        drawn.forEach(function (x) { if (at(i, x.line)) S.later(x.delay + (i === 0 ? 300 : 0), function () { put(x); }); });
        var j = 0;
        rows.forEach(function (x) { if (at(i, x.line)) S.later(500 + 200 * j++, function () { x.node.classList.add('on'); }); });
      },
      rest: function () {
        open();
        map.classList.add('on');
        if (sea) sea.classList.add('on');
        if (beach) beach.classList.add('on');
        drawn.forEach(put);
        if (marker) marker.ring.classList.add('on');
        rows.forEach(function (x) { x.node.classList.add('on'); });
      }
    };
  };

  // ------------------------------------------------------------- public API

  var active = null;
  var api = {
    templates: Object.keys(TEMPLATES),
    // A new story replaces whatever story was on screen.
    play: function (data, opts) {
      api.teardown();
      if (!data || !TEMPLATES[data.template] || !opts || !opts.mount) return false;
      active = new Story(data, opts);
      return true;
    },
    // An audio chunk the page scheduled: [start, start + dur) on its clock.
    audio: function (start, dur) { if (active && !active.ended) active.clock.audio(start, dur); },
    // Emma's transcript (never the guest's).
    transcript: function (text) { if (active && !active.ended) active.clock.addText(text); },
    turnComplete: function () { if (active && !active.ended) active.clock.turnComplete(); },
    // The guest spoke / tapped over her: the overview, now.
    settle: function () { if (active && !active.dead) active.toEnd(); },
    teardown: function () { if (active) { active.teardown(); active = null; } },
    isActive: function () { return !!(active && !active.dead); },
    // For the dev harness (scripts/record_stories.py) and debugging only.
    state: function () {
      if (!active) return null;
      var now = active.opts.clock ? active.opts.clock() : null;
      return { id: active.data.id, fired: active.fired, beats: active.n, ended: !!active.ended,
               instant: !!active.endedInstant, dead: active.dead,
               played: now != null ? active.clock.played(now) : null, sched: active.clock.sched,
               complete: active.clock.complete, cueAt: active.clock.cueAt.slice() };
    },
    _clock: { BeatClock: BeatClock, norm: norm, CLOCK: CLOCK, tidy: tidy,
              leadRateFor: leadRateFor, resetRates: function () { learnedRate = {}; } },
    // tests/client_story_clock.cjs only: apply beats up to i, as the clock would
    _fireTo: function (i) { if (active && !active.dead) active.fire(i); }
  };
  root.EmmaStory = api;
})(typeof window !== 'undefined' ? window : globalThis);
