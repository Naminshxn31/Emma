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

  // ------------------------------------------------------------- the caption
  // Her reply line on the robot face, word by word WITH her voice (owner 27
  // Oct: "อันนี้มันยาวเกินไม่สวย อยากให้ทำเป็น motion ตัวหนังสือ"). The page
  // painted the whole turn's text the moment it arrived -- seconds ahead of
  // her voice, the text comes several times faster than she speaks -- and a
  // guest asked her the same day to say it again ("ฟังไม่ทัน"). The owner kept
  // her pace; the words follow it on this clock, the beats' own: a word may
  // go up once the audio carrying its LAST character has played, less
  // `leadS`, never when its text arrives. Pure, like the clock (node runs it
  // in tests/client_captions.cjs); the page owns the DOM (voice-preview.html,
  // `captions`).
  var CAPTION = {
    leadS: 0.25,      // a word may show this long before her voice finishes it
    endMs: 1500,      // her audio has played out, no turn_complete: the rest after this
    quietMs: 500,     // no new audio this long mid-turn: her last piece's audio is all here
    slackS: 0.6,      // the page's clock runs this far behind the context's (output latency, <= 0.5)
    noAudioMs: 3000,  // text and no audio (or audio that never plays) this long: as it is
    holdWords: 8      // word segments after the last space that wait for more text (split)
  };
  var OPENER = /^[(\[{"'“‘]+$/;      // goes with the word after it
  var REPEAT = /^[ๆฯ]+$/;             // Thai repetition marks go with the word before
  // A lone Thai consonant is the segmenter missing a loanword, not a word:
  // "สปา" came out "ส" + "ปา", and the caption broke the line between them
  // (27 Oct, first render). It goes with the Thai after it.
  var LONE_TH = /^[ก-ฮ]$/, TH_START = /^[ก-ฮเ-ไ]/;
  var SPACE = /^\s+$/;
  var segmenters = {};
  // [{s, word}] -- words never split: Thai has no spaces, so the browser's
  // own word segmenter (Chrome 124 has Intl.Segmenter), else whitespace.
  function segments(text, lang) {
    var out = [];
    var Seg = typeof Intl === 'object' && Intl ? Intl.Segmenter : null;
    if (typeof Seg === 'function') {
      try {
        var seg = segmenters[lang] || (segmenters[lang] = new Seg(lang, { granularity: 'word' }));
        var it = seg.segment(text)[Symbol.iterator]();
        for (var r = it.next(); !r.done; r = it.next()) out.push({ s: r.value.segment, word: !!r.value.isWordLike });
        return out;
      } catch (e) { out = []; }
    }
    String(text).split(/(\s+)/).forEach(function (s) { if (s) out.push({ s: s, word: !SPACE.test(s) }); });
    return out;
  }
  function Caption() {
    this.clock = new BeatClock([]);
    this.raw = '';         // her transcript of this answer, as it came
    this.cut = 0;          // characters of `raw` already made into words
    this.words = [];       // {t: the word as written, sep: a space before it, end: `norm` chars through it}
    this.normAt = 0;
    this.sep = false;      // a space is owed before the next word
    this.shown = 0;        // words that may be on screen
    this.firstText = null; // wall-clock ms of her first text
    this.audioAt = null;   // wall-clock ms of the latest audio chunk
    this.stuckSince = 0;   // wall ms her audio clock stood at its start, nothing played
    this.overSince = 0;    // wall ms her voice was first seen played out
  }
  // An audio chunk the page scheduled for this answer: [start, start + dur).
  Caption.prototype.audio = function (start, dur, wall) {
    this.clock.audio(start, dur);
    this.audioAt = wall;
    this.overSince = 0;    // more of her voice: whatever was "over" is not
  };
  Caption.prototype.text = function (piece, wall) {
    piece = String(piece == null ? '' : piece);
    if (!piece) return;
    this.clock.addText(piece);
    this.raw += piece;
    if (this.firstText == null) this.firstText = wall;
    // a new piece after her voice played out (a tool call, a stalled stream)
    // is not "over": it waits for its own audio (review 27 Oct: it showed
    // whole on arrival, from a stale overSince)
    this.overSince = 0;
    this.quiet = false;    // more text came: the stream is not quiet
    this.split(false);
  };
  // turn_complete: her text is all in (and her audio, once some has come)
  Caption.prototype.complete = function () { this.clock.turnComplete(); this.split(true); };
  Caption.prototype.split = function (all) {
    var rest = this.raw.slice(this.cut);
    if (!rest) return;
    var segs = segments(rest, this.clock.lang || scriptOf(rest) || 'th');
    var stop = segs.length;
    // A piece may end inside a word ("ห้อง" + "นอน", "51." + "5", "Bioge" +
    // "nesis") -- and a half word is not one segment: "สำหรั" segments
    // "สำ" + "หรั", "อำนวยค" "อำ" + "นวย" + "ค", so holding only the last
    // segment let "สำ|หรับ", "มา|กมาย" through (review 27 Oct). Text up to
    // the last space is final (the segmenter never looks across one). After
    // it, the dictionary looks a few words ahead, so the last `holdWords`
    // word segments of that run wait for more text (measured on the 63
    // catalog stories' Thai, cut into pieces of every size 1-40: holding 5
    // still let a wrong word out in 29 of 2,560 runs, 6 in none; 8 for
    // margin) -- not the whole run, which would hold the first words of a
    // long first clause back by up to a second (estimated, not measured). That
    // costs nothing either: the clock cannot place a piece's text before the
    // next piece comes.
    if (!all && !/\s$/.test(rest)) {
      var run = segs.length, held = 0, k;
      for (k = segs.length - 1; k >= 0 && !SPACE.test(segs[k].s); k--) run = k;
      for (k = segs.length - 1; k >= run && held < CAPTION.holdWords; k--) if (segs[k].word) { held++; stop = k; }
      if (held < CAPTION.holdWords) stop = run;
      // never between pieces glued together (an opener, a lone consonant,
      // a repetition mark or punctuation and the word they go with)
      while (stop > run && (OPENER.test(segs[stop - 1].s) || LONE_TH.test(segs[stop - 1].s) ||
                            REPEAT.test(segs[stop].s) || !segs[stop].word)) stop--;
    }
    var cur = null, made = [];
    for (var j = 0; j < stop; j++) {
      var s = segs[j].s;
      this.cut += s.length;
      if (SPACE.test(s)) { this.sep = true; cur = null; continue; }
      // Punctuation closes the word before it ("Emma." "ค่ะ," "เยอะๆ"); a word
      // after an opener ("(Sky" ) joins it -- one piece, so a line never
      // starts on a comma or ends on a bracket. Within this batch only: a word
      // already on screen is never rewritten.
      if (cur && (cur.open || !segs[j].word || REPEAT.test(s) || (LONE_TH.test(cur.t) && TH_START.test(s)))) {
        cur.t += s;
        if (segs[j].word) cur.open = false;
        continue;
      }
      cur = { t: s, sep: this.sep && this.words.length + made.length > 0, open: !segs[j].word && OPENER.test(s) };
      this.sep = false;
      made.push(cur);
    }
    for (var m = 0; m < made.length; m++) {
      this.normAt += norm(made[m].t).length;
      this.words.push({ t: made[m].t, sep: made[m].sep, end: this.normAt });
    }
  };
  Caption.prototype.all = function () {
    this.split(true);
    this.shown = this.words.length;
    return this.shown;
  };
  // How many words may be on screen at `now` (the page's audio clock, null
  // = it has none: as the text comes) and `wall` (ms, for the fallbacks).
  Caption.prototype.due = function (now, wall) {
    var c = this.clock;
    if (now == null) return this.all();
    if (!c.segs.length) {
      return this.firstText != null && wall - this.firstText > CAPTION.noAudioMs ? this.all() : this.shown;
    }
    var played = c.played(now);
    if (!(played > 0)) {
      // Nothing of it has played yet. Queued behind her previous answer's
      // voice (gemini-3.8 completes a turn before its tool call and speaks
      // again after it): its start is still ahead on the audio clock, so it
      // waits, however long -- timed from the chunk's arrival, a whole answer
      // stood up ~7 s before she said it (review 27 Oct). A clock standing
      // at the start (a suspended context: it never plays): as it is, after
      // a while.
      if (now < c.segs[0][0] - CAPTION.slackS) { this.stuckSince = 0; return this.shown; }
      if (!this.stuckSince) this.stuckSince = wall;
      return wall - this.stuckSince > CAPTION.noAudioMs ? this.all() : this.shown;
    }
    this.stuckSince = 0;
    // No audio for a while in the middle of her turn (a tool call with no
    // turn_complete before it, a pause in the stream): her last piece's
    // audio is all here, so its words are placed by it -- not held for a
    // next piece that is not coming, then shown all at once after her
    // voice ended (review 27 Oct).
    // The held word tail is released only once her voice reaches it: released
    // with the settle, a word split across a >0.5 s gap in the stream came
    // out in two ("ความ | สะ | ดวก", audit 27 Oct) -- the hold rule above.
    if (c.pend.length && c.audioSince && wall - this.audioAt > CAPTION.quietMs) { c.settleText(); this.quiet = true; }
    // (her voice at the END of the text so far: the last word made is not
    // enough -- with a whole Thai run held, that is its first word, and the
    // half word went out at once)
    if (this.quiet && this.cut < this.raw.length &&
        c.charTime(Math.max(0, norm(this.raw).length - 1)) <= played + CAPTION.leadS) this.split(true);
    if (played >= c.sched - CLOCK.endEpsS) {
      // her voice has played out: with turn_complete, all of it; without
      // one (a rotation, a stalled stream, text that came after its audio),
      // a moment after the later of her voice ending and her last text
      if (c.complete) return this.all();
      if (!this.overSince) this.overSince = wall;
      else if (wall - this.overSince > CAPTION.endMs) return this.all();
    } else {
      this.overSince = 0;
    }
    while (this.shown < this.words.length &&
           c.charTime(Math.max(0, this.words[this.shown].end - 1)) <= played + CAPTION.leadS) this.shown++;
    return this.shown;
  };
  // Words not on screen yet, or text not yet made into words.
  Caption.prototype.pending = function () {
    return this.shown < this.words.length || this.cut < this.raw.length;
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
      // "≤ 31.4 sq.m." broke after its "≤" (ice_bath_where, audit 27 Oct)
      .replace(/([≤≥<>~])\s+(?=\d)/g, '$1\u00a0')
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
  // A value in a list row: the figure, its unit a size smaller (the unit
  // set as large as the figure ran a long range off the row)
  function rowValue(v) {
    var node = el('em', 'es-v');
    var unit = v && v.unit_en ? ' ' + v.unit_en : '';
    node.appendChild(document.createTextNode(valueText(v ? { from: v.from, to: v.to, dec: v.dec, prefix_en: v.prefix_en } : null)));
    if (unit) node.appendChild(el('small', null, unit));
    return node;
  }
  function reducedMotion() {
    try { return !!(root.matchMedia && root.matchMedia('(prefers-reduced-motion: reduce)').matches); }
    catch (e) { return false; }
  }
  var TOP_VH = 14;       // below the status pill and her caption band
  // The templates with a landscape layout (the room TV, Story.land). Each
  // one keeps its beats and its parts; only where things go changes. distance
  // and neighborhood (no story uses them) stay portrait: the page shows the
  // robot's own screen for those.
  var LAND = { spotlight: 1, metric: 1, features: 1, stack: 1, drill: 1, map: 1, journey: 1,
               assembly: 1, details: 1, slabs: 1 };
  // The landscape unit: one robot "vw" is 0.9% of the stage's height -- the
  // chips' 3.4vw type is ~3% of the TV's height (~64 px of a 4K TV), about
  // what the portrait story fitted to the TV made it (v2, read from 3-6 m).
  var LAND_UNIT = 0.009;
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
    // Every picture the server listed can be tapped to see it large (owner,
    // 15 Oct: "กดรูปบนจอ ... ขยายภาพนั้นและอธิบายเพิ่มเติม"): src -> its label
    // and the "more" tap id the server will accept for it.
    this.zoomMap = {};
    arr(data.zoom).forEach(function (z) {
      if (z && typeof z.src === 'string' && typeof z.tap === 'string') self.zoomMap[z.src] = z;
    });
    this.root = el('div', 'es-root es-t-' + data.template);
    this.opts.mount.appendChild(this.root);
    this.root.addEventListener('click', function (e) { self.onPictureTap(e); });
    this.W = this.root.clientWidth || root.innerWidth || 1080;
    this.H = this.root.clientHeight || root.innerHeight || 1920;
    // The room TV (owner 29 Oct: "ทำเรื่องเล่าแบบแนวนอน"): the SAME story laid
    // out across a landscape stage -- the picture large on one side, its words
    // and figures beside it -- asked for by the page (opts.layout), never on
    // the robot. W/H become the robot's screen scaled to the stage (so every
    // size a template computes from them is the robot's at that scale, as is
    // every CSS size: --es-vw/--es-vh, set here on the root), and LW/LH are
    // the stage the landscape branches (`S.land`) place things in.
    this.land = this.opts.layout === 'land' && LAND[data.template] === 1 && this.W > this.H;
    if (this.land) {
      this.LW = this.W; this.LH = this.H;
      var unit = this.LH * LAND_UNIT;
      this.W = unit * 100; this.H = unit * 100 * 1920 / 1080;
      this.root.style.setProperty('--es-vw', unit.toFixed(3) + 'px');
      this.root.style.setProperty('--es-vh', (unit * 1920 / 1080).toFixed(3) + 'px');
      this.root.classList.add('es-land');
      this.M = Math.round(this.LH * 0.03);       // the stage's margin
      this.G = Math.round(this.LW * 0.028);      // between a picture and its words
    }
    // The overview's chips are laid out now (still invisible), so the lowest
    // point content may reach is measured, not guessed: three chips can wrap
    // onto three rows.
    this.floorY = this.H * 0.965;
    if (this.unbound.length) {
      this.tapRow = this.buildTaps();
      var top = this.tapRow.offsetTop;
      this.floorY = top > this.H * 0.5 ? top - this.H * 0.015 : this.H * 0.885;
    }
    if (this.land) this.floorY = this.LH - this.M;
    this.tpl = TEMPLATES[data.template](this, p);
    if (BALANCED[data.template] && !this.land) this.balance();
    if (this.tpl.layout) this.tpl.layout();
    this.t0 = Date.now();
    this.fireAt = [];
    // The story's own report (opts.onReport), from real conversations: the
    // reviewer (13 Oct) could not check picture-to-voice timing on silent
    // screen recordings, nor smoothness with her voice and the 3D Emma
    // running. Frame times are sampled only while a report is wanted.
    if (this.opts.onReport) this.sampleFrames();
    if (reducedMotion()) { this.toEnd(true); return; }
    // 60 ms, not 90: a due beat waited ~45 ms on average for the next look
    // at the clock, on top of everything else that makes a picture late
    // (owner 21 Oct: "คำพูดไปแล้วแต่ภาพยังไม่มา"); a poll is a few additions
    this.tick = setInterval(function () { self.step(); }, 60);
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
  // The screen is a tall portrait (owner 21 Oct: "จอเรามันยาวสูง ... ไม่ให้มี
  // พื้นที่โล่งเกินไป"). Each template first sizes its own content to the room
  // it has -- bigger pictures, bigger type (`room()`). What is still left is
  // shared out here, once, before anything shows: some of it between the
  // blocks (the picture, the figure, the list -- at most 4.5vh a gap), the
  // rest above them, so the whole sits a little above centre. It used to
  // move everything down by 42% of the room left above the taps: a third of
  // the screen stayed empty above the content and a third below it, and a
  // full-width block could be pushed into Emma's corner (location_beach's
  // cards ended at 82% of the height, 21 Oct recording). A block that
  // reaches the right third ends above her head (contentBottom); one on the
  // left may go down to the taps. Positions only: no layout animation.
  // Elements anchored to the bottom (taps, hint, footnote) stay where they are.
  Story.prototype.balance = function () {
    var kids = this.root.children, items = [], room = Infinity;
    var head = this.contentBottom(), side = this.floorY - this.H * 0.015;
    for (var k = 0; k < kids.length; k++) {
      var c = kids[k];
      if (c === this.tapRow || !c.style || !c.style.top || !isFinite(parseFloat(c.style.top))) continue;
      var t = c.offsetTop || 0, b = t + (c.offsetHeight || 0);
      var right = (c.offsetLeft || 0) + (c.offsetWidth || 0);
      items.push({ c: c, t: t, b: b });
      room = Math.min(room, (right > this.W * 0.7 ? head : side) - b);
    }
    if (!items.length || !(room > this.H * 0.02)) return;
    // blocks: what overlaps moves together (a photo over its plan)
    items.sort(function (a, b) { return a.t - b.t; });
    var blocks = [];
    items.forEach(function (it) {
      var last = blocks[blocks.length - 1];
      if (last && it.t < last.b - 1) { last.items.push(it); last.b = Math.max(last.b, it.b); }
      else blocks.push({ b: it.b, items: [it] });
    });
    var gaps = blocks.length - 1;
    var per = gaps ? Math.min(this.H * 0.045, room * 0.5 / gaps) : 0;
    var lead = (room - per * gaps) * 0.4;
    blocks.forEach(function (bl, j) {
      var dy = Math.round(lead + per * j);
      if (!dy) return;
      bl.items.forEach(function (it) {
        it.c.style.top = Math.round(parseFloat(it.c.style.top) + dy) + 'px';
        if (it.c._top != null) it.c._top += dy;
      });
    });
  };
  // The height a template may fill under the caption band and above Emma's
  // head: what each one sizes its picture and type from.
  Story.prototype.room = function () {
    return this.contentBottom() - this.H * TOP_VH / 100;
  };
  // A soft light that crosses a hero picture once as it lands (a transform
  // of a gradient strip: no filter, plays once -- emma-story.css .es-sheen)
  Story.prototype.sheen = function (box) {
    box.appendChild(el('i', 'es-sheen'));
    return box;
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
    if (src && this.zoomMap[src]) { im.classList.add('es-zoomable'); im._zoom = this.zoomMap[src]; }
    this.imgs.push(im);
    return im;
  };
  // A tap on a picture. The story's pictures are layered (a photo waits at
  // opacity 0 over its plan until its line), so the picture tapped is the
  // topmost one the guest can SEE under the finger, not the event's target.
  // It opens large on the page at once; then Emma tells more: a row/slab/card
  // that leads to a story sends its own tap (bindTap, already on its way), any
  // other picture sends its "more" tap -- with `keep`, so this story and the
  // enlarged picture stay this answer's screen.
  Story.prototype.onPictureTap = function (e) {
    if (this.dead) return;
    if (this.zoomView && this.zoomView.contains(e.target)) { this.closeZoom(); return; }
    // A thumbnail in the drill's strip is the strip's own control: it brings
    // its place's card up, on this screen, and sends nothing (TEMPLATES.drill).
    // Its picture is a story picture too, so this listener -- the root's,
    // after the thumbnail's own -- opened it large and sent its "more" tap
    // as well (review 27 Oct).
    if (e.target && e.target.closest && e.target.closest('.es-thumb')) return;
    var pick = null;
    var under = document.elementsFromPoint ? document.elementsFromPoint(e.clientX, e.clientY) : [e.target];
    for (var k = 0; k < under.length && !pick; k++) {
      var n = under[k];
      if (n && n._zoom && this.root.contains(n) && seen(n, this.root)) pick = n;
    }
    // ...or, on a row with no story of its own (the stack's .es-pictap), that
    // row's picture wherever the row was tapped
    if (!pick && e.target && e.target.closest) {
      var row = e.target.closest('.es-pictap');
      var im = row && this.root.contains(row) ? row.querySelector('.es-zoomable') : null;
      if (im && im._zoom && seen(im, this.root)) pick = im;
    }
    if (!pick) return;
    this.showZoom(pick._zoom, pick.src, pick.naturalWidth, pick.naturalHeight);
    var inBoundTap = pick.closest ? pick.closest('[data-tap]') : null;
    if (!inBoundTap && this.opts.onTap) this.opts.onTap(pick._zoom.tap, true);
  };
  function seen(node, stop) {
    for (var n = node; n && n !== stop; n = n.parentElement) {
      var cs = root.getComputedStyle ? root.getComputedStyle(n) : null;
      if (cs && (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) < 0.3)) return false;
      if (n.classList && n.classList.contains('es-broken')) return false;
    }
    return true;
  }
  // The picture, large, over the story (still there underneath): its own
  // label under it. Opacity and a scale only (the robot's frame budget, as
  // every motion here); a tap anywhere on it closes it.
  Story.prototype.showZoom = function (z, src, nw, nh) {
    this.closeZoom();
    var v = el('div', 'es-zoomview');
    var fig = el('div', 'es-zv-fig');
    var im = new Image();
    im.alt = '';
    im.className = 'es-zv-img es-contain';
    im.src = src;
    // Scaled UP to the room it has, its own shape kept: at its own pixel size
    // a 640 px portrait stayed a third of the screen -- "enlarged" was not
    // (16 Oct, the first recording of every picture tap).
    var W = this.root.clientWidth || this.W, H = this.root.clientHeight || this.H;
    // the landscape stage: the whole stage is its room (no caption band)
    if (this.land) H = H * 0.78 / 0.58;
    if (nw > 0 && nh > 0) {
      var s = Math.min(W * 0.94 / nw, H * 0.58 / nh);
      im.style.width = Math.round(nw * s) + 'px';
      im.style.height = Math.round(nh * s) + 'px';
    }
    fig.appendChild(im);
    v.appendChild(fig);
    if (z.label_en) v.appendChild(el('div', 'es-zv-label', tidy(z.label_en)));
    v.appendChild(el('div', 'es-zv-hint', 'Tap to close'));
    this.root.appendChild(v);
    this.zoomView = v;
    void v.offsetWidth;
    v.classList.add('on');
  };
  Story.prototype.closeZoom = function () {
    if (this.zoomView && this.zoomView.parentNode) this.zoomView.parentNode.removeChild(this.zoomView);
    this.zoomView = null;
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
    // A value under 100 never counts (it lands): it starts at its own figure.
    // It started at "0" -- "0 sq.m.", "0 items" at full strength on every
    // room-type card for the 350 ms before go() (audit 27 Oct). One that
    // counts starts at the dimmed round step 0, as its count goes on.
    var counts = !range && countStep(v.to) > 0;
    var b = el('span', null, range || !counts ? fmt(num(v.to) != null ? v.to : 0, dec) : fmt(0, dec));
    if (counts) { b.classList.add('es-count'); b.classList.add('es-counting'); }
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
  // ---- landscape (the room TV) ----
  // A frame of the picture's own shape, at most maxW x maxH px, its top-left
  // at (left, top) of the stage.
  Story.prototype.lbox = function (ref, maxW, maxH, left, top, cls) {
    var ar = ref && ref.w && ref.h ? ref.w / ref.h : 4 / 3;
    var w = maxW, h = w / ar;
    if (h > maxH) { h = maxH; w = h * ar; }
    var b = el('div', 'es-box' + (cls ? ' ' + cls : ''));
    b._w = Math.round(w); b._h = Math.round(h); b._left = Math.round(left); b._top = Math.round(top);
    b.style.width = b._w + 'px'; b.style.height = b._h + 'px';
    b.style.left = b._left + 'px'; b.style.top = b._top + 'px';
    return b;
  };
  // A block put where the landscape layout wants it (its CSS left/right in
  // robot units would span the stage): left/top/width in px, null = unset.
  Story.prototype.place = function (node, left, top, width, height) {
    if (left != null) { node.style.left = Math.round(left) + 'px'; node.style.right = 'auto'; }
    if (top != null) { node.style.top = Math.round(top) + 'px'; node.style.bottom = 'auto'; }
    if (width != null) { node.style.width = Math.round(width) + 'px'; node.style.maxWidth = 'none'; }
    if (height != null) node.style.height = Math.round(height) + 'px';
    return node;
  };
  // The picture side of a landscape story: pictures sharing one width (the
  // narrower sets it, as on the robot) as tall as the stage allows, and the
  // column beside them for the words (`info`: false = none, the picture
  // centred; true = 30% of the stage at least; a number = that share). The
  // two are centred together, the words never wider than 42% of the stage.
  Story.prototype.lpics = function (refs, info) {
    var M = this.M, G = this.G, LW = this.LW, LH = this.LH;
    var need = info ? Math.round(LW * (info === true ? 0.3 : info)) : 0;
    var vh = LH - 2 * M, w = LW - 2 * M - (info ? G + need : 0);
    refs.forEach(function (ref) { if (ref && ref.w > 0 && ref.h > 0) w = Math.min(w, vh * ref.w / ref.h); });
    var h = 0;
    refs.forEach(function (ref) {
      var ar = ref && ref.w > 0 && ref.h > 0 ? ref.w / ref.h : 4 / 3;
      h = Math.max(h, Math.min(vh, w / ar));
    });
    if (!refs.some(function (ref) { return ref; })) h = vh;
    var infoW = info ? Math.min(Math.round(LW * 0.42), LW - 2 * M - G - w) : 0;
    var group = w + (info ? G + infoW : 0);
    var left = Math.round((LW - group) / 2);
    return { left: left, top: Math.round((LH - h) / 2), w: w, h: h, maxH: vh,
             infoLeft: Math.round(left + w + G), infoW: Math.round(infoW) };
  };
  // The picture(s) and the blocks of words that go with them (top to
  // bottom), laid out the way that leaves the picture more of the stage:
  // BESIDE it (the words in a column on the right, centred on the picture)
  // or, when the picture is wide, UNDER it (the words as wide as the
  // picture, the whole centred). Measured, before anything shows: each
  // block at the width it would have. `prep(mode)` lets a template set a
  // block's own columns for that mode first. The blocks are placed here;
  // the picture's frame comes back ({left, top, w, maxH, mode}).
  Story.prototype.lplace = function (refs, blocks, prep) {
    var M = this.M, G = this.G, LW = this.LW, LH = this.LH, gap = Math.round(this.H * 0.025);
    blocks = blocks.filter(function (b) { return b; });
    var mode = 'side';
    function heightAt(w) {
      var t = 0;
      blocks.forEach(function (b) { b.style.width = Math.round(w) + 'px'; b.style.maxWidth = 'none'; });
      if (prep) prep(mode);
      blocks.forEach(function (b, k) { t += (b.offsetHeight || 0) + (k ? gap : 0); });
      return t;
    }
    function widthFor(avail, cap) {
      var w = cap;
      refs.forEach(function (ref) { if (ref && ref.w > 0 && ref.h > 0) w = Math.min(w, avail * ref.w / ref.h); });
      return w;
    }
    function tallest(w) {
      var h = 0;
      refs.forEach(function (ref) { var ar = ref && ref.w > 0 && ref.h > 0 ? ref.w / ref.h : 4 / 3; h = Math.max(h, w / ar); });
      return h;
    }
    var vh = LH - 2 * M;
    // the words' column as narrow as its longest line allows (22-30% of the
    // stage): short chips leave the picture more width
    var need = true;
    if (blocks.length) {
      var nat = 0;
      blocks.forEach(function (b) { b.style.width = 'max-content'; });
      if (prep) prep('side');
      blocks.forEach(function (b) { nat = Math.max(nat, b.offsetWidth || 0); });
      if (nat > 0) need = Math.max(0.22, Math.min(0.3, (nat + 4) / LW));
    }
    var side = this.lpics(refs, blocks.length ? need : false), sideH = blocks.length ? heightAt(side.infoW) : 0;
    var g = { mode: 'side', left: side.left, top: side.top, w: side.w, maxH: vh };
    if (blocks.length) {
      mode = 'stack';
      var ih = heightAt(LW * 0.7), w = 0, avail = 0;
      for (var it = 0; it < 2; it++) {
        avail = vh - ih - gap * 1.5;
        w = widthFor(avail, LW - 2 * M);
        ih = heightAt(Math.max(w, LW * 0.5));
      }
      avail = vh - ih - gap * 1.5;
      w = widthFor(avail, LW - 2 * M);
      var h = Math.min(avail, tallest(w));
      // under it only when that gives the picture clearly more of the
      // stage: the landscape stage reads side by side
      if (avail > LH * 0.3 && w * h > side.w * Math.min(vh, tallest(side.w)) * 1.2) {
        var bw = Math.max(w, LW * 0.5);
        ih = heightAt(bw);
        var top = Math.max(M, Math.round((LH - (h + gap * 1.5 + ih)) / 2)), y = top + h + gap * 1.5;
        blocks.forEach(function (b) {
          b.style.left = Math.round((LW - bw) / 2) + 'px'; b.style.right = 'auto';
          b.style.top = Math.round(y) + 'px'; b.style.bottom = 'auto';
          y += (b.offsetHeight || 0) + gap;
        });
        return { mode: 'stack', left: Math.round((LW - w) / 2), top: top, w: w, maxH: avail };
      }
      mode = 'side';
      sideH = heightAt(side.infoW);
      var y0 = Math.max(M, Math.min(LH - M - sideH, side.top + Math.min(vh, tallest(side.w)) / 2 - sideH / 2));
      blocks.forEach(function (b) {
        b.style.left = side.infoLeft + 'px'; b.style.right = 'auto';
        b.style.top = Math.round(y0) + 'px'; b.style.bottom = 'auto';
        y0 += (b.offsetHeight || 0) + gap;
      });
    }
    return g;
  };
  // A block in the words' column, centred on the picture's middle, inside the stage.
  Story.prototype.lmid = function (node, mid) {
    var h = node.offsetHeight || 0;
    var top = Math.max(this.M, Math.min(this.LH - this.M - h, mid - h / 2));
    node.style.top = Math.round(top) + 'px';
    return top;
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
  Story.prototype.placeLabel = function (label, box, x, y, prefer, gapPx) {
    var gap = gapPx > 0 ? gapPx : Math.round(this.W * 0.055);
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
    // the last beats' own motion first, then the overview: 0.45 s (it was
    // 0.7 -- the last line's picture is the one most often late already)
    this.later(this.n ? 450 : 0, function () {
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
    var LIST_TOP = S.H * 0.15;
    // landscape: the list down the left of the stage at its own size from the
    // start, each item's big card on the right as tall as the stage; at rest
    // the last card stays up beside the list (a picture, not an empty half)
    var LD = null;
    if (S.land) {
      // more than four: the list in two columns (eight room types in one
      // column came out at 0.43 of their size)
      var two = items.length > 4, dw = Math.round(S.LW * (two ? 0.54 : 0.4));
      LD = { dw: dw, two: two, cardLeft: S.M + dw + S.G, cardW: S.LW - 2 * S.M - dw - S.G, k: 1 };
      LIST_TOP = S.M;
      S.place(dock, S.M, null, dw);
      // columns that never grow past their half (a long name wraps instead)
      if (two) { dock.classList.add('two'); dock.style.display = 'grid'; dock.style.gridTemplateColumns = 'repeat(2, minmax(0, 1fr))'; }
    }
    dock.style.top = Math.round(LIST_TOP) + 'px';
    // the big card may come down to just above Emma's head (it stopped at
    // 68% of the height: a third of the screen stood empty under every plan)
    var SMALL = 0.56, CARD_BOTTOM = S.contentBottom() - S.H * 0.008;
    if (LD) CARD_BOTTOM = S.LH - S.M;
    var rows = items.map(function (it) {
      var tapId = it.tap && S.taps[it.tap] ? it.tap : null;
      var row = el(tapId ? 'button' : 'div', 'es-row');
      var th = el('div', 'es-th' + planClass(it));
      var pic = it.asset ? th.appendChild(S.img(it.asset)) : null;
      // A row with no story of its own is its picture (owner 27 Oct, room
      // types: "1 Bedroom, large", the combined units -- only the small plan
      // answered a tap, the words were dead): a tap anywhere on it opens that
      // plan large and asks her for more, as the plan itself does
      // (onPictureTap). A row with a story keeps its own tap.
      if (!tapId && pic && pic._zoom) row.classList.add('es-pictap');
      var tx = el('div', 'es-tx');
      tx.appendChild(el('b', null, it.label_en || ''));
      if (it.sub_en) tx.appendChild(el('span', null, tidy(it.sub_en)));
      // the value under the label, not beside it: a long label and a range
      // ("2 BEDROOMS" / "51.5–52.5 sq.m.") never share one line
      if (it.value) tx.appendChild(rowValue(it.value));
      row.appendChild(th);
      row.appendChild(tx);
      if (tapId) { row.appendChild(el('i', 'es-go', '›')); S.bindTap(row, tapId); }
      dock.appendChild(row);
      return row;
    });
    var hint = p.end_hint_en ? S.add(el('p', 'es-hint es-fade', p.end_hint_en)) : null;
    if (hint) hint.style.bottom = Math.round(S.H - S.floorY + S.H * 0.01) + 'px';
    if (hint && LD) { S.place(hint, S.M, null, LD.dw); hint.style.bottom = S.M + 'px'; }
    // The list at rest fills the tall screen: each row's picture as large as
    // its share of the height allows (two layouts, measured once before
    // anything shows). Three room plans were 25vw thumbnails in a list that
    // filled half the screen (21 Oct recording).
    (function size() {
      var n = rows.length;
      if (!n) return;
      var limit = S.contentBottom();
      if (LD) limit = S.LH - S.M;
      if (hint && hint.offsetTop) limit = Math.min(limit, hint.offsetTop - S.H * 0.012);
      var gap = S.H * (n <= 3 ? 0.022 : 0.016), pad = S.W * 0.02;
      var each = (limit - LIST_TOP - gap * (n - 1)) / n;
      if (LD && LD.two) { var rn = Math.ceil(n / 2); each = (limit - LIST_TOP - gap * (rn - 1)) / rn; }
      // 36vw at most: wider, a long size ("51.5–52.5 sq.m.") ran off the row
      var tw = Math.max(S.W * 0.22, Math.min(S.W * (n <= 3 ? 0.36 : 0.3), (each - pad * 2) * 4 / 3));
      if (LD) tw = Math.min(tw, LD.dw * (LD.two ? 0.14 : 0.4));
      dock.style.gap = Math.round(gap) + 'px';
      rows.forEach(function (r) { var th = r.querySelector('.es-th'); if (th) th.style.width = Math.round(tw) + 'px'; });
      // while she talks the list is small under her caption: one row about
      // 8.5% of the height, whatever size the rows are at rest
      var rh = rows[0].offsetHeight || 0;
      if (rh > 0) SMALL = Math.max(0.36, Math.min(0.56, S.H * 0.085 / rh));
      // eight layouts (22 Oct): seven docked rows at 0.56 reached ~y 1000 and
      // the last cards had 480 px for a title and a plan -- the combined
      // one-bedrooms' plan came up 49 x 29 px (audit 27 Oct)
      if (n > 5) SMALL = 0.36;
      if (LD) {
        // the whole list in its column: smaller when long, centred when short
        var room = limit - LIST_TOP, lh = dock.offsetHeight || 0;
        LD.k = lh > room ? room / lh : 1;
        // the cards start where the (smaller) list ends
        LD.cardLeft = Math.round(S.M + LD.dw * LD.k + S.G);
        LD.cardW = S.LW - S.M - LD.cardLeft;
        dock.style.transformOrigin = '0 0';
        if (LD.k < 1) dock.style.transform = 'scale(' + LD.k.toFixed(4) + ')';
        else dock.style.top = Math.round(LIST_TOP + (room - lh) / 2) + 'px';
      }
    })();
    var cur = null, presented = false;
    // Her opening line names no item ("We have three room types"): the rows
    // wait as empty frames, so the screen answers "three" before the first
    // plan comes up -- their labels stay hidden until she says them.
    function ghosts() { rows.forEach(function (r) { if (!r.classList.contains('on')) r.classList.add('ghost'); }); }
    // the next card's top: under the last row already in the list
    function cardTop() {
      if (LD) return S.M;
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
      if (LD) S.place(big, LD.cardLeft, null, LD.cardW);
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
      // the picture keeps a fifth of the screen under its title, over the
      // last docked rows if it must (they are back in the list at the end)
      var minH = (lab.offsetHeight || 0) + S.H * 0.2;
      if (!LD && !S.instant && CARD_BOTTOM - top < minH) {
        top = Math.max(LIST_TOP, CARD_BOTTOM - minH);
        big.style.top = Math.round(top) + 'px';
        big.style.height = Math.round(CARD_BOTTOM - top) + 'px';
      }
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
        if (S.instant || LD) return;
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
        if (LD) {
          // the list complete beside the last card she named (or, cut short
          // before any, the last item's), never an empty half of the stage
          rows.forEach(function (r) { r.classList.add('on'); });
          if (hint) hint.classList.add('on');
          if (!cur && items.length) present(items.length - 1);
          if (cur) cur.big.classList.add('on');
          return;
        }
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
    // The plates fill the tall screen: each one as large as its slot allows
    // (a plate 16:9, turned like a floor seen from above, is ~0.54 of its
    // width tall on screen), the stack from under her caption to just above
    // Emma's head -- the labels on the right must clear her. At 54vw the
    // three floors left ~100 px between plates and 300 below (21 Oct).
    var areaTop = S.H * 0.16, areaBottom = Math.min(S.contentBottom(), S.floorY - S.H * 0.05);
    var slot = Math.min((areaBottom - areaTop) / Math.max(n, 1), S.H * 0.26);
    var pw = Math.max(S.W * 0.5, Math.min(S.W * 0.6, slot * 0.94 / 0.543));
    // landscape: the stack as tall as the stage, its plates larger, the
    // labels in a column on the right
    var cx0 = S.W * 0.36, LS = null;
    if (S.land) {
      areaTop = S.M; areaBottom = S.LH - S.M;
      slot = Math.min((areaBottom - areaTop) / Math.max(n, 1), S.LH * 0.42);
      pw = Math.min(S.LW * 0.5, slot * 0.94 / 0.543);
      // the plates (turned, ~0.95 of their width on screen) and the labels
      // centred together
      var lw0 = Math.round(S.LW * 0.34), left0 = (S.LW - (pw * 0.95 + S.G + lw0)) / 2;
      cx0 = left0 + pw * 0.475;
      LS = { left: Math.round(left0 + pw * 0.95 + S.G), w: lw0 };
    }
    var ph = pw * 9 / 16;
    // centred on the room the stack actually takes
    var used = slot * n, y0 = areaTop + Math.max(0, (areaBottom - areaTop - used) * 0.4);
    var slabs = [], labs = [];
    floors.forEach(function (f, k) {
      var rankTop = n - 1 - order.indexOf(k);
      var mid = y0 + rankTop * slot + slot / 2;
      var tapId = f.tap && S.taps[f.tap] ? f.tap : null;
      var slab = el(tapId ? 'button' : 'div', 'es-slab');
      slab.style.width = Math.round(pw) + 'px';
      slab.style.left = Math.round(cx0 - pw / 2) + 'px';
      slab.style.top = Math.round(mid - ph / 2) + 'px';
      slab._mid = mid;
      slab.style.zIndex = String(10 + n - rankTop);
      if (f.asset) slab.appendChild(S.img(f.asset));
      slab.appendChild(el('i', 'es-glow'));
      var lab = el(tapId ? 'button' : 'div', 'es-fl');
      if (LS) S.place(lab, LS.left, null, LS.w);
      lab._mid = mid;
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
    // each label centred on its plate (by its own height, read once for all)
    var lh = labs.map(function (l) { return l.offsetHeight || S.H * 0.07; });
    labs.forEach(function (l, k) { l.style.top = Math.round(l._mid - lh[k] / 2) + 'px'; });
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
    // landscape: the place's card big on the left, the height of the stage;
    // the plan, the strip and the note in a column on the right
    var LR = null;
    if (S.land) {
      var cw0 = Math.round(S.LW * 0.56);
      LR = { cw: cw0, left: S.M + cw0 + S.G, w: S.LW - 2 * S.M - cw0 - S.G };
    }
    var box = S.add(LR ? S.lbox(p.base_asset, LR.w, S.LH * 0.42, LR.left, S.M, 'es-fade')
      : S.box(p.base_asset, 92, 21, TOP_VH, 'es-fade'));
    box.appendChild(S.img(p.base_asset, 'es-fill'));
    var spots = items.map(function (it) {
      if (!Array.isArray(it.at) || it.at.length !== 2) return null;
      var s = S.spot(it.at[0], it.at[1]);
      box.appendChild(s);
      return s;
    });
    var y = S.floorY;
    var more = p.more_en ? S.add(el('p', 'es-more es-fade', tidy(p.more_en))) : null;
    if (more && LR) S.place(more, LR.left, null, LR.w);
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
    // landscape: up to five thumbnails a row under the plan
    var perRow = Math.max(1, Math.min(n, 5));
    if (LR) tw = Math.floor(Math.min(S.W * 0.17, (LR.w - (perRow - 1) * gap) / perRow)) - 1;
    var strip = S.add(el('div', 'es-strip es-fade'));
    strip.style.top = Math.round(y - tw * 0.75 - S.H * 0.006) + 'px';
    if (LR) { S.place(strip, LR.left, null, LR.w); strip.style.flexWrap = 'wrap'; strip.style.rowGap = Math.round(gap * 1.6) + 'px'; }
    var cardTop = box._top + box._h + S.H * 0.016;
    var cardBottom = Math.min(parseFloat(strip.style.top) - S.H * 0.014, S.H * 0.78);
    if (LR) { cardTop = S.M; cardBottom = S.LH - S.M; }
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
    if (LR) {
      cards.forEach(function (c) { S.place(c, S.M, null, LR.cw); });
      // the plan, the strip and the note centred together in their column
      var g2 = Math.round(S.H * 0.03), sh = strip.offsetHeight || 0, mh2 = more ? more.offsetHeight || 0 : 0;
      var total = box._h + g2 + sh + (more ? g2 + mh2 : 0);
      var t0 = Math.max(S.M, Math.round((S.LH - total) / 2));
      box._top = t0;
      box.style.top = t0 + 'px';
      strip.style.top = (t0 + box._h + g2) + 'px';
      if (more) more.style.top = (t0 + box._h + g2 + sh + g2) + 'px';
    }
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
    var ph = p.photo || {}, photo = null, chips = [], rows = null, phImg = null, heard = -1;
    // The chips first, measured: the picture takes the rest of the height
    // (it was 42% at most, with a third of the screen empty under the chips)
    if (ph.asset) {
      rows = S.add(el('div', 'es-rows'));
      // a chip is a line of text, or {label_en, value_en}: the figure the
      // guest asked about ("Length · ≈ 155 m"), drawn like a distance row
      arr(ph.chips_en).forEach(function (c) {
        var r = el('div', 'es-r es-left');
        if (c && typeof c === 'object') {
          r.classList.add('es-key');
          r.appendChild(el('span', null, tidy(c.label_en)));
          if (c.value_en) r.appendChild(el('b', null, tidy(c.value_en)));
          // "≈ 155 m" sits beside "≈ 1.30 m"; "≈ 27 m wide · ≈ 143 sq.m."
          // needs the whole row
          if (String(c.value_en || '').length > 14) r.classList.add('wide');
          if (num(c.line) != null) r._line = c.line;
        } else {
          r.appendChild(el('span', null, tidy(c)));
        }
        rows.appendChild(r);
        chips.push(r);
      });
      // three or more plain chips sit in two columns (the figure keeps a
      // whole row): five chips one per row took 600 px from the photo
      var plain = chips.filter(function (r) { return !r.classList.contains('es-key'); }).length;
      if (plain >= 3) rows.classList.add('grid');
    }
    // landscape: the pictures on the left as tall as the stage, the chips in
    // one column beside them, centred on the photo
    var gridOK = !!(rows && rows.classList.contains('grid'));
    var L = S.land ? S.lplace([p.base_asset, ph.asset], [rows], function (mode) {
      // one column beside the picture; two under a wide one
      if (rows) rows.classList.toggle('grid', gridOK && mode === 'stack');
    }) : null;
    var rowsH = rows ? rows.offsetHeight || 0 : 0;
    var maxH = Math.max(30, Math.min(56, (S.room() - rowsH - S.H * 0.03) / S.H * 100));
    // the plan and the photo share one width, so the plan insets into the
    // photo's own corner: the narrower of the two sets it
    var mw = 94;
    [p.base_asset, ph.asset].forEach(function (ref) {
      if (!ref || !(ref.w > 0 && ref.h > 0)) return;
      var w = Math.min(S.W * 0.94, S.H * maxH / 100 * ref.w / ref.h);
      mw = Math.min(mw, w / S.W * 100);
    });
    var base = L ? S.lbox(p.base_asset, L.w, L.maxH, L.left, L.top, 'es-spbase')
      : S.box(p.base_asset, mw, maxH, TOP_VH, 'es-spbase');
    S.root.insertBefore(base, rows);
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
    if (ph.asset) {
      // the photo in a frame of its OWN shape, sharing the picture's top and
      // width (so the zoomed picture insets into its top-right corner): in the
      // picture's frame a 2.5:1 site plan cut a 4:3 photo to a strip
      photo = L ? S.lbox(ph.asset, L.w, L.maxH, L.left, L.top, 'es-photo es-fade')
        : S.box(ph.asset, mw, maxH, TOP_VH, 'es-photo es-fade');
      if (photo._w < base._w) {
        photo._left = base._left + base._w - photo._w;
        photo.style.left = photo._left + 'px';
      }
      if (L) {
        // landscape: each centred on the same middle line (the plan stood
        // at the photo's top, over an empty band, until the photo came);
        // the plan still lands in the photo's top-right corner
        var hm = Math.max(base._h, photo._h);
        base._top = Math.round(L.top + (hm - base._h) / 2); base.style.top = base._top + 'px';
        photo._top = Math.round(L.top + (hm - photo._h) / 2); photo.style.top = photo._top + 'px';
        base.style.setProperty('--inset-dy', (photo._top - base._top) + 'px');
      }
      phImg = photo.appendChild(S.img(ph.asset, 'es-fill es-cover'));
      phImg._onBroken = function () { dropPhoto(); };
      photo.appendChild(el('i', 'es-shade'));
      S.sheen(photo);
      var title = el('div', 'es-label', ph.title_en || '');
      if (ph.sub_en) title.appendChild(el('small', null, ph.sub_en));
      if (ph.title_en) photo.appendChild(title);
      S.root.insertBefore(photo, base);
      // the chips come with the photo, under it (the picture is inset by
      // then); full width when they end above Emma's head, as they now do
      if (L) {
        // placed by lplace: beside the pictures, or under them
      } else {
        rows.style.top = Math.round(photo._top + photo._h + S.H * 0.025) + 'px';
        if (photo._top + photo._h + S.H * 0.025 + rowsH > S.contentBottom()) rows.classList.add('narrow');
      }
    }
    var spotLine = num(p.spot_line) != null ? p.spot_line : 0, photoLine = num(ph.line);
    var zoomed = false, photoOn = false, ringAt = 0, gone = false;
    function showBase() { S.ready(base).then(function () { S.frame(function () { base.style.opacity = '1'; }); }); }
    function ringOn() {
      if (spot) { spot.classList.remove('done'); spot.classList.add('on'); }
      label.classList.add('on');
    }
    // the zoom lands in ~0.95 s (emma-story.css .es-zoom); the ring with it
    var ZOOM_MS = 900;
    function zoomIn() {
      if (zoomed) return;
      zoomed = true;
      if (scale > 1) zoom.style.transform = 'scale(' + scale + ')';
      ringAt = Date.now() + (scale > 1 ? ZOOM_MS : 200);
      // not once the photo is up: the end state used to bring the label
      // back inside the shrunken corner picture, ~12 px tall (review 12 Oct)
      S.later(scale > 1 ? ZOOM_MS : 200, function () { if (!photoOn || gone) ringOn(); });
    }
    // a figure chip with its own `line` waits for it: "≈ 27 m wide" up with
    // the photo, two lines before she says it, was the picture leading
    function chipsOn() {
      var k = 0;
      chips.forEach(function (c) {
        if (c._line != null && c._line > heard) return;
        S.later(350 + (k++) * 160, function () { c.classList.add('on'); });
      });
    }
    // The photo would not load: the zoomed picture keeps its ring and label,
    // and the chips sit under IT -- not a dark frame titled for a photo
    // that is not there.
    function dropPhoto() {
      if (!photo || gone) return;
      gone = true;
      photo.classList.remove('on');
      photo.style.visibility = 'hidden';
      base.classList.remove('inset');
      if (L && L.mode === 'stack') rows.style.top = Math.round(base._top + base._h + S.H * 0.025) + 'px';
      else if (L) S.lmid(rows, base._top + base._h / 2);
      else rows.style.top = Math.round(base._top + base._h + S.H * 0.025) + 'px';
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
        heard = Math.max(heard, i);
        if (i === 0) showBase();
        if (i === spotLine) S.later(i === 0 ? 350 : 0, zoomIn);
        if (photoOn) chips.forEach(function (c) { if (c._line === i) S.later(150, function () { c.classList.add('on'); }); });
        if (photoLine != null && i === photoLine) {
          if (!zoomed) zoomIn();
          // the ring gets ~1 s on screen before the cut to the photo, even
          // when the next line comes quickly ("Sky Pool is on level 3" is two
          // seconds): the photo may trail her voice a little, never lead it.
          // It was 1.5 s after a 1.15 s zoom (1.8 s on one line): when her
          // photo line came quickly the photo trailed it by ~1 s (owner
          // 21 Oct: "ภาพยังไม่มา หรือมาช้า")
          S.later(Math.max(i === spotLine ? 1300 : 0, ringAt + 1000 - Date.now()), showPhoto);
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
    // what goes under the picture first (counters, chips), measured: the
    // render takes the rest of the height -- at 40% the six-building campus
    // filled a fifth of the screen (21 Oct)
    var counters = arr(p.counters), figs = [], ticks = [], meter = null, mlab = null;
    var below = S.add(el('div', 'es-below'));
    var figRow = below.appendChild(el('div', 'es-figs'));
    var figEls = counters.map(function (c, k) {
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
      return { fig: fig, val: val };
    });
    var chipRow = below.appendChild(el('div', 'es-chips'));
    var chips = arr(p.chips).map(function (c) {
      var ch = el('b', 'es-chip es-pop', c.label_en || '');
      chipRow.appendChild(ch);
      return ch;
    });
    // landscape: the building as tall as the stage on the left, the figures
    // and the chips in one column beside it
    var LB = null;
    if (S.land) {
      chipRow.style.marginRight = '0';
      // one column of figures beside the building, two under a wide one
      LB = S.lplace([fin || frames[frames.length - 1]], [below], function (mode) {
        figRow.style.gridTemplateColumns = mode === 'side' ? '1fr' : '';
        figEls.forEach(function (f, k) { f.fig.style.textAlign = mode === 'side' || !(k % 2) ? 'left' : 'right'; });
      });
    }
    var belowH = below.offsetHeight || 0;
    var maxH = Math.max(30, Math.min(50, (S.room() - belowH - S.H * 0.035) / S.H * 100));
    var box = LB ? S.lbox(fin || frames[frames.length - 1], LB.w, LB.maxH, LB.left, LB.top, 'es-fade')
      : S.box(fin || frames[frames.length - 1], 96, maxH, TOP_VH, 'es-fade');
    S.root.insertBefore(box, below);
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
    // the light crosses the real render as it comes up, not the hologram
    if (rise) S.sheen(rise);
    var gy = num(p.ground_y), ry = num(p.roof_y), lines = gy != null && ry != null && gy > ry;
    var ground = null;
    if (gy != null) { ground = el('i', 'es-hline'); ground.style.top = gy + '%'; box.appendChild(ground); }
    if (!LB) below.style.top = Math.round(box._top + box._h + S.H * 0.03) + 'px';
    counters.forEach(function (c, k) {
      var fig = figEls[k].fig, val = figEls[k].val;
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
    // landscape: the room as tall as the stage on the left; its area, notes
    // and the mood pictures in a column beside it, top to bottom
    var LT = S.land ? S.lpics([ref], 0.36) : null;
    var box = S.add(LT ? S.lbox(ref, LT.w, LT.maxH, LT.left, LT.top, 'es-fade') : S.box(ref, 94, 38, TOP_VH, 'es-fade'));
    box.style.background = '#070a1f';
    if (p.wire_asset) box.appendChild(S.img(p.wire_asset, 'es-fill es-contain'));
    var solid = p.solid_asset ? S.img(p.solid_asset, 'es-fill es-contain es-solid') : null;
    if (solid) box.appendChild(solid);
    ['t', 'r', 'b', 'l'].forEach(function (s) { box.appendChild(el('i', 'es-edge ' + s)); });
    var below = S.add(el('div', 'es-below'));
    below.style.top = Math.round(box._top + box._h + S.H * 0.022) + 'px';
    below.style.bottom = Math.round(S.H - S.floorY) + 'px';
    if (LT) {
      S.place(below, LT.infoLeft, Math.max(S.M, box._top), LT.infoW);
      below.style.bottom = Math.round(S.LH - Math.min(S.LH - S.M, box._top + box._h)) + 'px';
    }
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
      function () { S.reveal(box, function () { S.later(100, function () { box.classList.add('drawn'); }); }); },
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
    // the rows first, measured: the map takes the rest of the height
    var list = S.add(el('div', 'es-rows'));
    var rows = arr(p.rows).map(function (r) {
      var d = el('div', 'es-r es-left');
      d.appendChild(el('span', null, tidy(r.label_en)));
      if (r.value_en) d.appendChild(el('b', null, tidy(r.value_en)));
      list.appendChild(d);
      return d;
    });
    var listH = list.offsetHeight || 0;
    var maxH = Math.max(30, Math.min(56, (S.room() - listH - S.H * 0.03) / S.H * 100));
    var box = S.box(p.map_asset, 96, maxH, TOP_VH, 'es-fade');
    S.root.insertBefore(box, list);
    box.appendChild(S.img(p.map_asset, 'es-fill es-cover'));
    var pin = p.pin || null, to = p.route_to || null, spot = null, plabel = null, route = null, rlabel = null;
    var pinUp = true;
    function lab(text, x, y, prefer, gap) {
      var l = el('div', 'es-label es-up', text);
      box.appendChild(l);
      l._up = S.placeLabel(l, box, x, y, prefer, gap);
      return l;
    }
    var marker = null;
    if (pin && num(pin.x) != null && num(pin.y) != null) {
      spot = S.spot(pin.x, pin.y);
      box.appendChild(spot);
      // a map pin stands on the point (drops in with the ring, .es-mpin)
      marker = el('div', 'es-mpin');
      marker.style.left = pin.x + '%';
      marker.style.top = pin.y + '%';
      marker.appendChild(pinGlyph());
      box.appendChild(marker);
      // clear of the pin standing over the point (~10vw tall), not only of the ring
      if (pin.label_en) { plabel = lab(pin.label_en, pin.x, pin.y, null, Math.round(S.W * 0.115)); pinUp = plabel._up; }
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
    list.style.top = Math.round(box._top + box._h + S.H * 0.025) + 'px';
    // full width when the rows end above Emma's head, else clear of her
    if (box._top + box._h + S.H * 0.025 + listH > S.contentBottom()) list.classList.add('narrow');
    function drawRoute() { if (route) route.style.transform = 'rotate(' + route._ang + 'rad) scaleX(1)'; }
    // The route (and its label, often a distance) waits for the line that
    // says it -- "≈ 0.6 km" on the map while she is still saying "Jomtien"
    // is the picture leading her voice. Without `route_to.line`: at once.
    var routeLine = to && num(to.line) != null ? to.line : 0;
    function routeNow(delay) {
      S.later(delay, drawRoute);
      S.later(delay + 650, function () { if (rlabel) rlabel.classList.add('on'); });
    }
    return {
      beat: function (i) {
        if (i === 0) {
          S.reveal(box);
          S.later(400, function () {
            if (marker) marker.classList.add('on');
            if (spot) spot.classList.add('on');
            if (plabel) plabel.classList.add('on');
          });
        }
        if (i === routeLine) routeNow(i === 0 ? 850 : 0);
        var j = 0;
        arr(p.rows).forEach(function (r, k) {
          if (r.line === i) { var at = j++; S.later(at * 180, function () { rows[k].classList.add('on'); }); }
        });
      },
      rest: function () {
        box.classList.add('on');
        if (marker) marker.classList.add('on');
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
    // 96% of the width (a wide plan is width-bound: every point of width is
    // height too), up to 46% of the height for a taller picture
    // landscape: the figure over a picture that is not a wide strip (as on
    // the robot); the parts -- and a wide picture's figure -- beside it or
    // under it, whichever leaves the picture more (lplace, below)
    var L = null, lover = false;
    if (S.land) {
      var ar0 = p.bg_asset && p.bg_asset.w > 0 && p.bg_asset.h > 0 ? p.bg_asset.w / p.bg_asset.h : 4 / 3;
      lover = ar0 < 2;
      L = S.lpics([p.bg_asset], false);
    }
    var box = S.add(L ? S.lbox(p.bg_asset, L.w, L.maxH, L.left, L.top, 'es-fade')
      : S.box(p.bg_asset, 96, 46, TOP_VH, 'es-fade'));
    var h = box._h, over = L ? lover : h >= S.H * 0.3;
    if (p.bg_asset) box.appendChild(S.img(p.bg_asset, 'es-fill es-contain'));
    if (over) box.appendChild(el('i', 'es-shade-all'));
    S.sheen(box);
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
    } else if (L) {
      S.add(big);
      big.style.textAlign = 'left';
    } else {
      big.style.top = Math.round(below + S.H * 0.02) + 'px';
      S.add(big);
      below = big.offsetTop + (big.offsetHeight || S.H * 0.12);
    }
    var parts = arr(p.parts), max = 0;
    parts.forEach(function (q) { if (num(q.to) > max) max = q.to; });
    var list = S.add(el('div', 'es-rows'));
    if (!L) list.style.top = Math.round(below + S.H * 0.025) + 'px';
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
    // full width when the parts end above Emma's head, else clear of her
    if (L) {
      // the figure measured at its FINAL value (it counts from 0: "0 UNITS"
      // fit one line, "1,066 UNITS" wrapped and ran into the parts), kept on
      // one line, smaller if its column is narrow
      var finals = [];
      if (!over && v) {
        Array.prototype.forEach.call(big.querySelectorAll('.es-count'), function (c) {
          if (c._final != null) { finals.push([c, c.textContent]); c.textContent = c._final; }
        });
      }
      var blocks = over ? [] : [big];
      if (parts.length) blocks.push(list);
      var g = S.lplace([p.bg_asset], blocks, function () {
        if (over || !v) return;
        v.node.style.whiteSpace = 'nowrap';
        v.node.style.fontSize = '';
        var cw = big.clientWidth || 0, sw = v.node.getBoundingClientRect().width || 0;   // an inline em: no scrollWidth
        if (cw > 0 && sw > cw) {
          var fs = parseFloat(root.getComputedStyle(v.node).fontSize) || 0;
          if (fs) v.node.style.fontSize = Math.floor(fs * cw / sw) + 'px';
        }
      });
      finals.forEach(function (f) { f[0].textContent = f[1]; });
      var arB = p.bg_asset && p.bg_asset.w > 0 && p.bg_asset.h > 0 ? p.bg_asset.w / p.bg_asset.h : 4 / 3;
      var bw = g.w, bh0 = bw / arB;
      if (bh0 > g.maxH) { bh0 = g.maxH; bw = bh0 * arB; }
      box._w = Math.round(bw); box._h = Math.round(bh0); box._left = g.left; box._top = g.top;
      box.style.width = box._w + 'px'; box.style.height = box._h + 'px';
      box.style.left = box._left + 'px'; box.style.top = box._top + 'px';
      h = box._h;
    } else if (parts.length && below + S.H * 0.025 + (list.offsetHeight || 0) > S.contentBottom()) list.classList.add('narrow');
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
      if (v) { v.node.classList.add('on'); S.later(150, v.go); }
    }
    function open(then) {
      if (opened) { if (then) then(); return; }
      opened = true;
      S.reveal(box, function () { big.classList.add('on'); if (then) then(); });
    }
    return {
      // six buildings' parts ran past the screen's edge, over the tap chips
      // and into Emma's corner (audit 27 Oct, units_by_building): a list
      // that grows with the catalog fits (as journey's and stack's do) --
      // clear of her head, or of the taps when it keeps to the left
      layout: function () {
        var narrow = list.classList.contains('narrow');
        var k = S.fitScale(list, L ? S.LH - S.M : narrow ? S.floorY - S.H * 0.015 : S.contentBottom());
        if (k < 1) {
          list.style.transformOrigin = '0 0';
          list.style.transform = 'scale(' + k.toFixed(4) + ')';
        }
      },
      beat: function (i) {
        if (i === 0) open(line === 0 ? countNow : null);
        else if (i === line) open(countNow);
        var j = 0;
        rows.forEach(function (x) {
          var at = num(x.q.line);
          if (at === i || (at == null && i === line)) {
            var k = j++;
            // after the big figure has counted most of the way (it runs 1 s)
            S.later((at === line || at == null ? 750 : 0) + k * 260, function () { showPart(x); });
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
    // one or two: one column of wide cards; more: two columns
    var n = items.length, cols = n <= 2 ? 1 : 2, rowsN = Math.max(1, Math.ceil(n / cols));
    // landscape: the cards side by side across the stage -- all in one row up
    // to three, else two rows (2 + 2, 3 + 2, 3 + 3), a short row centred
    if (S.land) { cols = n <= 3 ? Math.max(1, n) : n === 4 ? 2 : 3; rowsN = Math.max(1, Math.ceil(n / cols)); }
    var grid = S.add(el('div', 'es-feats' + (cols === 1 || (S.land && n <= 2) ? ' wide' : '')));
    var top = Math.round(S.H * TOP_VH / 100);
    if (S.land) top = S.M;
    grid.style.top = top + 'px';
    if (cols === 1) grid.style.gridTemplateColumns = '1fr';
    var cards = items.map(function (it) {
      var c = el('div', 'es-card es-feat es-pop');
      var ci = el('div', 'es-cimg');
      if (it.asset) ci.appendChild(S.img(it.asset));
      c.appendChild(ci);
      c.appendChild(el('span', null, it.label_en || ''));
      if (it.note_en) c.appendChild(el('small', null, tidy(it.note_en)));
      grid.appendChild(c);
      c._ci = ci;
      return c;
    });
    var foot = p.footnote_en ? S.add(el('p', 'es-foot es-fade', p.footnote_en)) : null;
    if (foot) foot.style.bottom = Math.round(S.H - S.floorY + S.H * 0.01) + 'px';
    if (S.land) {
      S.place(grid, S.M, null, S.LW - 2 * S.M);
      grid.classList.add('es-lgrid');
      grid.style.setProperty('--cols', String(cols));
      if (foot) { S.place(foot, S.M, null, S.LW - 2 * S.M); foot.style.bottom = S.M + 'px'; }
    }
    // The pictures are as tall as the screen allows (two smart-home cards
    // were 300 px pictures with half the screen empty under them, 21 Oct),
    // computed from the room above Emma's head and the footnote -- so two
    // wide cards one above the other no longer run into her corner and
    // under the footnote, as fixed 16:10 ones did on the robot (13 Oct).
    // A picture keeps between 2.4:1 and 1.5:1 (one column) or 2:1 and
    // 1.25:1 (two columns) -- a cover crop; the floor is what lets five
    // cards clear the footnote and her head (1.6:1 ran them into it).
    (function size() {
      if (!n) return;
      var limit = S.contentBottom();
      if (foot && foot.offsetTop) limit = Math.min(limit, foot.offsetTop - S.H * 0.015);
      var gap = S.W * 0.022, colW = (S.W * 0.9 - gap * (cols - 1)) / cols;
      if (S.land) {
        // the stage's width shared by the cards of a row (each its own width,
        // so a short last row centres), the pictures as tall as the rows allow
        limit = foot && foot.offsetTop ? foot.offsetTop - S.H * 0.015 : S.LH - S.M;
        colW = (S.LW - 2 * S.M - gap * (cols - 1)) / cols;
        grid.style.gap = Math.round(gap) + 'px';
        cards.forEach(function (c) { c.style.width = Math.floor(colW) + 'px'; });
      }
      var text = 0;
      cards.forEach(function (c) { text = Math.max(text, (c.offsetHeight || 0) - (c._ci.offsetHeight || 0)); });
      var each = (limit - top - gap * (rowsN - 1)) / rowsN - text;
      var lo = colW / (cols === 1 ? 2.4 : 2), hi = colW / (cols === 1 ? 1.5 : 1.25);
      if (S.land) { lo = colW / 2.6; hi = colW / 1.05; }
      var ih = Math.round(Math.max(lo, Math.min(hi, each)));
      cards.forEach(function (c) { c._ci.style.aspectRatio = 'auto'; c._ci.style.height = ih + 'px'; });
      if (S.land) {
        // the grid centred in the room above the footnote
        var gh = grid.offsetHeight || 0;
        grid.style.top = Math.round(Math.max(S.M, (limit + S.M - gh) / 2)) + 'px';
      }
    })();
    // `footnote_line`: the line that says it ("an optional package, not
    // included in the room price") puts it up; without one, the overview does
    var footLine = num(p.footnote_line) != null ? p.footnote_line : null, any = false;
    return {
      beat: function (i) {
        var j = 0;
        items.forEach(function (it, k) {
          if (it.line === i) { any = true; var at = j++; S.later(at * 160, function () { S.reveal(cards[k]); }); }
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
    school: 'M3 9.5 12 5l9 4.5-9 4.5zM7 11.6V16c1.5 1.4 3.2 2 5 2s3.5-.6 5-2v-4.4M21 9.5v5',
    // the nearby stories of 21 Oct: restaurants/cafés (a fork and a knife)
    // and tourist attractions (a camera) -- not the mall's bag or the house
    food: 'M6 3v5a2 2 0 0 0 4 0V3M8 3v18M17 21V3c-2.2 1.6-3.2 4.3-3.2 7.4 0 2.4 1.4 3.6 3.2 3.6',
    sight: 'M4 8h3l1.6-2.4h6.8L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM12 16.5a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z',
    // the rings view (21 Oct): a shop front with its awning (7-Eleven,
    // FamilyMart, a supermarket, a market -- the bag stays the mall's), and
    // the two ways to get there: by car (every drive time) and on foot
    shop: 'M3.5 9.5 5 4.5h14l1.5 5M3.5 9.5a2.13 2.13 0 0 0 4.25 0 2.13 2.13 0 0 0 4.25 0 2.13 2.13 0 0 0 4.25 0 2.13 2.13 0 0 0 4.25 0M5 12v8h14v-8M10 20v-4.5h4V20',
    car: 'M5 17v-4.6l1.8-4.6A2 2 0 0 1 8.7 6.5h6.6a2 2 0 0 1 1.9 1.3l1.8 4.6V17M5 12.4h14M4 17h16M7 17v2.2M17 17v2.2M7.6 14.6h1.2M15.2 14.6h1.2',
    walk: 'M14.9 4.2a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0zM12.6 7.6l-1.4 5.8-2.6 7.1M11.2 13.4l3 3 .8 4.4M12.3 8.4 9.4 11.6M12.3 8.4l3.5 3.4'
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
    // the same rows drawn around the project instead of down a list
    if (p.view === 'rings') return aroundRings(S, p);
    var rows = arr(p.rows), max = num(p.bar_max) || 0;
    rows.forEach(function (r) { arr(r.bar).forEach(function (v) { if (num(v) > max) max = v; }); });
    // fewer places, bigger rows (the same scale): two (a narrow question's
    // story) were 300 px rows with a third of the screen empty above them
    // and a third below (21 Oct); five or more, tighter rows -- shrunk as a
    // whole to fit, they left a fifth of the width empty on the right
    var wrap = S.add(el('div', 'es-jr' + (rows.length <= 2 ? ' xl' : rows.length <= 4 ? ' roomy' : ' dense')));
    wrap.style.top = Math.round(S.H * (TOP_VH + 1) / 100) + 'px';
    // landscape: the same road and lanes across the stage (one scale, longer)
    if (S.land) S.place(wrap, S.M + S.G, S.M, S.LW - 2 * (S.M + S.G));
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
      // the farther, the longer the drive (0.6-1.6 s), so the dot's speed
      // reads the same on every row -- it was 0.7-2.1 s, and the airport's
      // time came up a second after she had said it
      row.style.setProperty('--d', (0.6 + 1.0 * hi).toFixed(2) + 's');
      wrap.appendChild(row);
      return { row: row, val: val, span: span, fill: fill, run: run, lo: lo, hi: hi, line: r.line };
    });
    // what room is left goes between the rows first (up to 4.5vh more each)
    (function spread() {
      var h = wrap.offsetHeight || 0, n = items.length;
      if (!h || !n) return;
      var spare = S.room() - S.H * 0.01 - h;
      if (S.land) spare = S.LH - 2 * S.M - h;
      if (!(spare > 0)) return;
      var cs = root.getComputedStyle ? root.getComputedStyle(wrap) : null;
      var gap = cs ? parseFloat(cs.rowGap || cs.gap) || 0 : 0;
      var add = Math.min(S.H * 0.045, spare * 0.6 / n);
      if (gap > 0 && add > 1) wrap.style.gap = Math.round(gap + add) + 'px';
    })();
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
        S.later(200, function () { drive(x); });
        if (x.val) S.later(200 + (0.6 + 1.0 * x.hi) * 1000 - 150, function () { x.val.classList.add('on'); });
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
        var k = S.fitScale(wrap, S.land ? S.LH - S.M : S.contentBottom());
        if (k < 1) {
          wrap.style.transformOrigin = '0 0';
          wrap.style.transform = 'scale(' + k.toFixed(4) + ')';
        } else if (S.land) {
          wrap.style.top = Math.round((S.LH - (wrap.offsetHeight || 0)) / 2) + 'px';
        }
      },
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        items.forEach(function (x) {
          if (x.line === i) show(x, (i === 0 ? 300 : 0) + 300 * j++);
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

  // What is around the project (journey, `view: "rings"` -- the nearby
  // stories of 21 Oct, 7-Eleven to the Big Buddha; owner: "ยังไม่ได้ทำ motion
  // ที่มันโอเค", and of the drawn map before them, "Google Maps ... สวยกว่านี้").
  // The same rows as the bars, drawn as a little map: the project's pin in a
  // disc of city blocks, rings of drive time around it (every 5 minutes out to
  // `bar_max`, labelled), and as she names a place a route draws out from the
  // pin and the place drops onto its own ring -- its icon, its short name and
  // time beside it. Under the disc the same places as a list, large enough to
  // read standing at the robot, each row coming in with its place.
  // DIRECTION IS NOT DATA: the approved figures are drive times with no
  // bearing. The places are spread evenly round the pin in the order she
  // names them (clockwise from the top; the top is kept for the rings'
  // labels), there is no compass, and the design's footnote says so ("...
  // not a map"). A place's distance from the pin is its minutes on the
  // rings' scale (its `bar`, the first figure of a range); the figures on
  // screen are the catalog's `value_en` -- a ring's minutes come from
  // `bar_max`, never from her.
  function ringStep(max) {
    var steps = [1, 2, 5, 10, 15, 20, 30, 60];
    for (var k = 0; k < steps.length; k++) if (max / steps[k] <= 4 + 1e-9) return steps[k];
    return Math.ceil(max / 4);
  }
  // a category's own colour (CSS --k): the same mark on the disc and in the list
  var KINDS = { hospital: 1, school: 1, food: 1, sight: 1, park: 1, beach: 1, plane: 1 };
  function kindCls(name) { return KINDS[name] ? ' k-' + name : ''; }
  function boxHit(a, b) {
    var w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]), h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
    return w > 0 && h > 0 ? w * h : 0;
  }
  function aroundRings(S, p) {
    var rows = arr(p.rows).filter(function (r) { return r && typeof r === 'object'; });
    var n = rows.length, W = S.W;
    var mins = rows.map(function (r) { var v = num(arr(r.bar)[0]); return v != null && v >= 0 ? v : null; });
    var max = num(p.bar_max) || 0;
    mins.forEach(function (v) { if (v != null && v > max) max = v; });
    if (!(max > 0)) max = 10;
    var step = ringStep(max), size = n <= 2 ? 'xl' : n <= 4 ? 'roomy' : 'dense';
    var top0 = Math.round(S.H * TOP_VH / 100), gap = Math.round(S.H * 0.016);
    // the project's name over the disc, on one line with how the times were
    // taken (by car: every figure on the disc is a drive)
    var head = S.add(el('div', 'es-ar-o es-up ' + size));
    head.appendChild(el('b', null, p.origin_en || ''));
    if (p.origin_sub_en) {
      var hs = el('span');
      hs.appendChild(icon('car'));
      hs.appendChild(document.createTextNode(tidy(p.origin_sub_en)));
      head.appendChild(hs);
    }
    head.style.top = top0 + 'px';
    // the list under the disc, built and measured first: the disc gets the
    // rest of the height above Emma's head
    var list = S.add(el('div', 'es-ar-list ' + size));
    var legend = rows.map(function (r) {
      var d = el('div', 'es-ar-r es-left' + kindCls(r.icon));
      var ic = el('i', 'es-jr-node');
      ic.appendChild(icon(r.icon));
      d.appendChild(ic);
      var body = el('div', 'es-ar-b');
      body.appendChild(el('b', null, r.label_en || ''));
      if (r.sub_en) body.appendChild(el('span', null, tidy(r.sub_en)));
      // on foot: its own chip, which waits for the line that says it (a
      // figure on screen before she says it -- the spotlight's rule, 21 Oct)
      var walk = null;
      if (r.walk && r.walk.value_en) {
        walk = el('em', 'es-ar-walk es-pop');
        walk.appendChild(icon('walk'));
        walk.appendChild(document.createTextNode(tidy(r.walk.value_en))); // not a span: .es-ar-b span is the note's
        body.appendChild(walk);
      }
      d.appendChild(body);
      // landscape: the time under the name (a narrow column: a long name and
      // its time side by side ran into each other)
      if (r.value_en) (S.land ? body : d).appendChild(timeText(r.value_en, 'es-ar-v'));
      list.appendChild(d);
      return { node: d, walk: walk };
    });
    // Five schools' full names wrapped to three lines a row and the list
    // took 0.6 of the screen (first recording, 21 Oct): a long list first
    // tightens (one more layout, measured), and only then gives way by
    // scale; the disc keeps at least ~half the width.
    // landscape: the disc as tall as the stage on the left, the project's
    // name and the list in a column beside it (MW = the map's own width; on
    // the robot it is the screen's)
    var MW = W, LA = null;
    if (S.land) {
      var mw0 = Math.min(Math.round(S.LW * 0.56), S.LH - 2 * S.M);
      LA = { iw: Math.min(Math.round(S.LW * 0.42), S.LW - 2 * S.M - mw0 - S.G) };
      LA.left = Math.round((S.LW - (mw0 + S.G + LA.iw)) / 2);
      LA.ileft = LA.left + mw0 + S.G;
      MW = mw0;
      S.place(head, LA.ileft, null, LA.iw);
      S.place(list, LA.ileft, null, LA.iw);
    }
    var headH = head.offsetHeight || 0;
    var spare = S.room() - headH - gap * 2;
    var minH = Math.round(W * (size === 'dense' ? 0.52 : 0.56));
    var listH = n ? list.offsetHeight || 0 : 0;
    var Hb, mapTop;
    if (LA) {
      var roomA = S.LH - 2 * S.M - headH - gap;
      if (listH > roomA && size !== 'xl') { list.classList.add('tight'); listH = list.offsetHeight || 0; }
      var kA = listH > roomA ? roomA / Math.max(1, listH) : 1;
      if (kA < 1) {
        list.style.transformOrigin = '0 0';
        list.style.transform = 'scale(' + kA.toFixed(4) + ')';
      }
      var gA = Math.max(S.M, Math.round(S.LH / 2 - (headH + gap + listH * kA) / 2));
      head.style.top = gA + 'px';
      list.style.top = (gA + headH + gap) + 'px';
      Hb = S.LH - 2 * S.M;
      mapTop = S.M;
    } else {
      if (spare - listH < minH && size !== 'xl') {
        list.classList.add('tight');
        listH = list.offsetHeight || 0;
      }
      Hb = Math.round(spare - listH);
      if (Hb < minH) {
        Hb = minH;
        var k = (spare - Hb) / Math.max(1, listH);
        if (k < 1) {
          list.style.transformOrigin = '50% 0';
          list.style.transform = 'scale(' + Math.max(0.6, k).toFixed(4) + ')';
        }
      }
      Hb = Math.min(Hb, Math.round(W * 0.84));
      mapTop = top0 + headH + gap;
      list.style.top = (mapTop + Hb + gap) + 'px';
    }
    var map = el('div', 'es-ar es-fade ' + size);
    S.root.insertBefore(map, list);
    map.style.top = mapTop + 'px';
    map.style.height = Hb + 'px';
    if (LA) S.place(map, LA.left, null, MW);
    // the caption ("... not a map") on a line of its own under the disc:
    // in a corner of the map it met the marks of the places nearest the rim
    var note = p.footnote_en ? map.appendChild(el('div', 'es-ar-note es-fade', tidy(p.footnote_en))) : null;
    var noteH = note ? Math.round(W * 0.036) : 0;   // its CSS (2.6vw, one line): no read, no layout
    var Hd = Hb - noteH;                          // the disc's own height
    var cx = MW / 2, cy = Hd / 2;
    var R = Math.max(W * 0.2, Math.min(Hd / 2 - W * 0.012, LA ? MW / 2 - W * 0.012 : W * 0.34));
    var rOut = R * 0.93;                         // the outer ring, inside the disc's rim
    var M = Math.round(W * (size === 'xl' ? 0.104 : size === 'roomy' ? 0.092 : 0.082));
    var pinW = W * 0.08, pinH = W * 0.1;         // .es-ar-pin .es-pinsvg
    // the pin's own ground: no place inside it (the head of the pin stands
    // over the middle, its point on it)
    var r0 = Math.max(R * 0.34, pinH * 0.5 + M * 0.62);
    function radius(v) { return r0 + (rOut - r0) * Math.max(0, Math.min(1, v / max)); }
    function put(node, x, y, w, h) {
      node.style.left = Math.round(x) + 'px'; node.style.top = Math.round(y) + 'px';
      if (w != null) { node.style.width = Math.round(w) + 'px'; node.style.height = Math.round(h) + 'px'; }
    }
    var disc = el('div', 'es-ar-disc');
    put(disc, cx - R, cy - R, 2 * R, 2 * R);
    var blocks = discBlocks(R, r0 * 0.78);
    if (blocks) disc.appendChild(blocks);
    map.appendChild(disc);
    // the rings: the scale of the disc, each a zone of its own (the inner
    // ones drawn last, over the outer: nearer reads brighter)
    var ringVals = [];
    for (var rv = step; rv <= max + 1e-9; rv += step) ringVals.push(rv);
    var rings = [];
    ringVals.slice().reverse().forEach(function (v) {
      var rr = radius(v), rg = el('i', 'es-ar-ring' + (v === ringVals[0] ? ' d0' : ''));
      put(rg, cx - rr, cy - rr, 2 * rr, 2 * rr);
      map.appendChild(rg);
      rings.unshift(rg);                         // ripple order: inner first
    });
    // a label per ring -- every other ring when they lie closer than a
    // label is tall (5/10/15/20 on a small disc: 10 and 20, the outer one
    // always), the scale still reads from the even spacing
    var every = (rOut - r0) * step / max < W * 0.05 ? 2 : 1;
    var ringTags = ringVals.map(function (v, j) {
      return (ringVals.length - 1 - j) % every ? null : map.appendChild(el('div', 'es-ar-rl es-fade', v + ' min'));
    });
    // the project: its ground ring, a shadow, the pin standing on the middle
    var ground = S.spot(50, 50);
    ground.classList.add('es-nb-ring');
    put(ground, cx, cy);
    map.appendChild(ground);
    var shadow = el('i', 'es-nb-shadow es-fade');
    put(shadow, cx, cy);
    map.appendChild(shadow);
    var pin = el('div', 'es-nb-pin es-ar-pin es-drop');
    put(pin, cx, cy);
    pin.appendChild(pinGlyph());
    map.appendChild(pin);
    // the places: evenly round the pin in the order she names them, the
    // first at the top and on clockwise (started half a step round, five
    // places put one at 6 o'clock whose label had nowhere to go but over the
    // middle of the disc, across the routes), each at its minutes on the
    // rings. Two the same time away sit on one ring at different angles; one
    // that would touch the pin or an earlier place moves out along its own
    // spoke, a few pixels at a time, never past the rim (deterministic).
    var turn = 360 / Math.max(1, n);
    var headC = [cx, cy - pinH * 0.61], headR = pinW * 0.44;
    var places = rows.map(function (r, k) {
      var deg = n === 1 ? -35 : n === 2 ? (k ? 160 : -20) : -90 + k * turn;   // the first at 12 o'clock
      var th = deg * Math.PI / 180;
      return { r: r, k: k, th: th, ux: Math.cos(th), uy: Math.sin(th), rr: radius(mins[k] != null ? mins[k] : max),
               line: num(r.line) != null ? r.line : 0,
               walkLine: r.walk && num(r.walk.line) != null ? r.walk.line : null,
               row: legend[k].node, walk: legend[k].walk };
    });
    places.forEach(function (x, k) {
      for (var guard = 0; guard < 80; guard++) {
        x.mx = cx + x.rr * x.ux; x.my = cy + x.rr * x.uy;
        var clash = Math.hypot(x.mx - headC[0], x.my - headC[1]) < headR + M / 2 + W * 0.006;
        for (var j = 0; j < k && !clash; j++) {
          clash = Math.hypot(x.mx - places[j].mx, x.my - places[j].my) < M + W * 0.01;
        }
        if (!clash || x.rr >= R - M / 2) break;
        x.rr = Math.min(R - M / 2, x.rr + W * 0.004);
      }
    });
    places.forEach(function (x) {
      var r = x.r, kc = kindCls(r.icon);
      var route = el('i', 'es-ar-route' + kc);
      put(route, cx, cy);
      route.style.width = Math.round(x.rr) + 'px';
      route._ang = x.th.toFixed(4);
      route.style.transform = 'rotate(' + route._ang + 'rad) scaleX(0)';
      map.appendChild(route);
      var m = el('div', 'es-ar-m es-drop' + kc);
      put(m, x.mx, x.my);
      var ping = el('b', 'es-ar-mping');
      put(ping, -M / 2, -M / 2, M, M);
      m.appendChild(ping);
      var dot = el('i', 'es-jr-node es-ar-dot');
      put(dot, -M / 2, -M / 2, M, M);
      dot.appendChild(icon(r.icon));
      m.appendChild(dot);
      map.appendChild(m);
      var tag = el('div', 'es-ar-tag es-pop' + kc);
      tag.appendChild(el('b', null, r.short_en || r.label_en || ''));
      if (r.value_en) tag.appendChild(timeText(r.value_en));
      map.appendChild(tag);
      x.route = route; x.m = m; x.tag = tag;
    });
    // Every label measured at once (one read between the writes -- a read
    // per label forced a layout each, 217 ms as the map opened, 13 Oct),
    // then placed, clear of the pin, every place's mark and every label
    // placed before it, inside the map. Deterministic: the same story, the
    // same screen.
    var tagSize = places.map(function (x) { return [x.tag.offsetWidth || W * 0.25, x.tag.offsetHeight || W * 0.1]; });
    var rlSize = ringTags.map(function (t) { return t ? [t.offsetWidth || W * 0.12, t.offsetHeight || W * 0.05] : null; });
    var noteW = note ? note.offsetWidth || W * 0.5 : 0;
    var pad = W * 0.008, edge = W * 0.012, half = M / 2 + 2;
    var taken = [[cx - pinW / 2, cy - pinH, cx + pinW / 2, cy + W * 0.012]];
    places.forEach(function (x) { taken.push([x.mx - half, x.my - half, x.mx + half, x.my + half]); });
    // over a mark, the pin, a label: never while anywhere else is clear
    // (a fixed price far above a route crossed); over a route: a little
    function cost(box, routes, more) {
      var c = 0, all = more ? taken.concat(more) : taken;
      for (var t = 0; t < all.length; t++) { var a = boxHit(box, all[t]); if (a) c += W * 4 + a; }
      if (routes) {
        for (var q = 0; q < routePts.length; q++) {
          var pt = routePts[q];
          if (pt[0] > box[0] && pt[0] < box[2] && pt[1] > box[1] && pt[1] < box[3]) c += W * 0.04;
        }
      }
      return c;
    }
    if (note) {
      put(note, (MW - noteW) / 2, Hb - noteH);
      taken.push([(MW - noteW) / 2, Hb - noteH, (MW + noteW) / 2, Hb]);
    }
    // the routes: a label may lie over one (a map's labels do), at a cost,
    // so it does only where every other side is taken
    var routePts = [];
    places.forEach(function (x) {
      for (var d = M * 0.6; d < x.rr - M * 0.5; d += W * 0.012) routePts.push([cx + d * x.ux, cy + d * x.uy]);
    });
    // each place's label beside it: outward first, then every other side by
    // how outward it points, then the same a step farther out, kept inside
    // the map. Who chooses first matters (the first takes the clear side the
    // next one needed): the order she names them, then outer places first
    // (least room by the rim), then inner first -- the first clean layout
    // wins, else the least crowded. Arithmetic only, no reads: cheap to
    // try three times, and the same story always lays out the same.
    var DIRS = [[1, 0], [-1, 0], [0, -1], [0, 1], [0.71, -0.71], [-0.71, -0.71], [0.71, 0.71], [-0.71, 0.71]];
    function away(px, py, b) {               // a point's distance to a box
      return Math.hypot(Math.max(b[0] - px, 0, px - b[2]), Math.max(b[1] - py, 0, py - b[3]));
    }
    function label(x, fixed) {
      var k = x.k, w = tagSize[k][0], h = tagSize[k][1], reach0 = half + W * 0.012;
      var dirs = [[x.ux, x.uy]].concat(DIRS.slice().sort(function (a, b) {
        return (b[0] * x.ux + b[1] * x.uy) - (a[0] * x.ux + a[1] * x.uy);
      }));
      // beside the mark, a step farther, and slid along that side
      var cands = [];
      [1, 1.8].forEach(function (f) {
        dirs.forEach(function (d) {
          var side = Math.abs(d[1]) <= 0.25, end = Math.abs(d[0]) <= 0.25;
          var slides = side ? [0, -0.45, 0.45] : end ? [0, -0.35, 0.35] : [0];
          slides.forEach(function (sl) { cands.push([d[0], d[1], reach0 * f, side ? 0 : sl * w, side ? sl * h : 0]); });
        });
      });
      var best = null, bestCost = Infinity;
      for (var c = 0; c < cands.length; c++) {
        var dx = cands[c][0], dy = cands[c][1], reach = cands[c][2];
        var tx = x.mx + dx * reach + (dx > 0.25 ? w / 2 : dx < -0.25 ? -w / 2 : 0) + cands[c][3];
        var ty = x.my + dy * reach + (dy > 0.25 ? h / 2 : dy < -0.25 ? -h / 2 : 0) + cands[c][4];
        tx = Math.max(w / 2 + edge, Math.min(MW - w / 2 - edge, tx));
        // up into the gap under the header (the place at 12 o'clock)
        ty = Math.max(h / 2 - gap * 0.8, Math.min(Hb - h / 2 - edge * 0.5, ty));
        var c2 = cost([tx - w / 2 - pad, ty - h / 2 - pad, tx + w / 2 + pad, ty + h / 2 + pad], true, fixed);
        // a label pushed far from its place by the edge reads as another's,
        // and one as near another mark as its own names the wrong one (five
        // cameras: "Sanctuary of Truth" sat between two of them, 21 Oct)
        c2 += Math.max(0, Math.hypot(tx - x.mx, ty - x.my) - (reach0 + Math.max(w, h))) * 2;
        var bb = [tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2], own = away(x.mx, x.my, bb);
        for (var o = 0; o < places.length; o++) {
          if (o !== k && away(places[o].mx, places[o].my, bb) < own + W * 0.03) { c2 += W * 1.2; break; }
        }
        if (c2 < bestCost) { bestCost = c2; best = [tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2]; }
        if (!c2) break;
      }
      return { box: best, cost: bestCost };
    }
    var orders = [places.slice(),
                  places.slice().sort(function (a, b) { return (b.rr - a.rr) || (a.k - b.k); }),
                  places.slice().sort(function (a, b) { return (a.rr - b.rr) || (a.k - b.k); })];
    var layout = null;
    for (var oi = 0; oi < orders.length && !(layout && layout.cost === 0); oi++) {
      var fixed = [], total = 0, boxes = [];
      orders[oi].forEach(function (x) {
        var got = label(x, fixed);
        boxes[x.k] = got.box;
        fixed.push(got.box);
        total += got.cost;
      });
      if (!layout || total < layout.cost) layout = { cost: total, boxes: boxes };
    }
    places.forEach(function (x) {
      var bx = layout.boxes[x.k];
      put(x.tag, bx[0], bx[1]);
      taken.push(bx);
      // a label that could not sit right by its mark gets a short leader
      var nx = Math.max(bx[0], Math.min(bx[2], x.mx)), ny = Math.max(bx[1], Math.min(bx[3], x.my));
      var d = Math.hypot(nx - x.mx, ny - x.my);
      if (d - M / 2 > W * 0.028) {
        var ex = (nx - x.mx) / d, ey = (ny - x.my) / d, lead = el('i', 'es-ar-lead');
        put(lead, x.mx + ex * M / 2, x.my + ey * M / 2);
        lead.style.width = Math.round(d - M / 2) + 'px';
        lead.style.transform = 'rotate(' + Math.atan2(ey, ex).toFixed(4) + 'rad)';
        map.insertBefore(lead, x.m);
        x.lead = lead;
      }
    });
    // A ring's label sits on its ring at the top, or in the clear gap between
    // two places nearest the top: the first recording put "5 min" under the
    // nearest school's mark. Placed after the places' own labels (a name
    // matters more than a scale mark) and outer ring first (it sets the
    // scale); a label with no clear place on its ring is left out -- a ring
    // unlabelled, never two labels on each other.
    var angs = [-Math.PI / 2];
    if (n >= 2) {
      var sorted = places.map(function (x) { return (x.th % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI); })
        .sort(function (a, b) { return a - b; });
      sorted.forEach(function (a, k) {
        var b = k + 1 < sorted.length ? sorted[k + 1] : sorted[0] + 2 * Math.PI;
        angs.push((a + b) / 2);
      });
    }
    angs = angs.concat([Math.PI / 2, 0, Math.PI]);
    function fromTop(a) {
      var d = Math.abs(((a + Math.PI / 2) % (2 * Math.PI) + 3 * Math.PI) % (2 * Math.PI) - Math.PI);
      return d;
    }
    var firstAng = angs.shift();
    angs.sort(function (a, b) { return fromTop(a) - fromTop(b); });
    angs.unshift(firstAng);
    for (var ri = ringVals.length - 1; ri >= 0; ri--) {
      if (!ringTags[ri]) continue;
      var w = rlSize[ri][0], h = rlSize[ri][1], rr = radius(ringVals[ri]), at = null;
      for (var ai = 0; ai < angs.length && !at; ai++) {
        var lx = cx + rr * Math.cos(angs[ai]), ly = cy + rr * Math.sin(angs[ai]);
        if (!cost([lx - w / 2 - pad, ly - h / 2 - pad, lx + w / 2 + pad, ly + h / 2 + pad])) at = [lx, ly];
      }
      if (!at) { map.removeChild(ringTags[ri]); ringTags[ri] = null; continue; }
      put(ringTags[ri], at[0], at[1]);
      taken.push([at[0] - w / 2, at[1] - h / 2, at[0] + w / 2, at[1] + h / 2]);
    }
    ringTags = ringTags.filter(function (t) { return !!t; });
    function draw(x) {
      x.route.style.transform = 'rotate(' + x.route._ang + 'rad) scaleX(1)';
      x.route.classList.add('on');
    }
    // the place she is naming stands out (its mark, its label, its row);
    // the overview has none -- not even from a landing still on its timer
    // when the overview went up (the last line's places are staggered)
    var rested = false;
    function act(x, on) {
      [x.m, x.tag, x.row].forEach(function (e) { e.classList.toggle('act', !!on && !rested); });
    }
    function land(x) {
      places.forEach(function (o) { if (o.line !== x.line) act(o, false); });
      x.m.classList.add('on');
      x.row.classList.add('on');
      act(x, true);
      // one ripple as it lands -- a class of its own, taken off again: left
      // on, the ripple replayed when an interruption's no-motion end state
      // lifted (`animation: none` off = a new animation)
      if (!S.instant && !rested) {
        x.m.classList.add('ping');
        S.later(1700, function () { x.m.classList.remove('ping'); });
      }
    }
    function show(x, delay) {
      S.later(delay, function () {
        if (x.shown) return;
        x.shown = true;
        draw(x);
        S.later(420, function () { land(x); });
        S.later(640, function () { x.tag.classList.add('on'); if (x.lead) x.lead.classList.add('on'); });
        if (x.walk && (x.walkLine == null || x.walkLine <= x.line)) S.later(900, function () { x.walk.classList.add('on'); });
      });
    }
    var opened = false;
    function open() {
      if (opened) return;
      opened = true;
      head.classList.add('on');
      S.reveal(map, function () {
        rings.forEach(function (rg, j) { S.later(100 + 120 * j, function () { rg.classList.add('on'); }); });
        ringTags.forEach(function (t, j) { S.later(520 + 120 * j, function () { t.classList.add('on'); }); });
        S.later(250, function () { pin.classList.add('on'); shadow.classList.add('on'); });
        S.later(800, function () { ground.classList.add('on'); });
        if (note) S.later(700, function () { note.classList.add('on'); });
      });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        places.forEach(function (x) { if (x.line === i) show(x, (i === 0 ? 650 : 0) + 380 * j++); });
        // "...or about 23 minutes on foot": the walk on its own line
        places.forEach(function (x) {
          if (x.walk && x.walkLine === i && x.walkLine > x.line) S.later(250, function () { x.walk.classList.add('on'); });
        });
      },
      rest: function (instant) {
        rested = true;
        open();
        map.classList.add('on');
        rings.forEach(function (rg) { rg.classList.add('on'); });
        ringTags.forEach(function (t) { t.classList.add('on'); });
        pin.classList.add('on'); shadow.classList.add('on'); ground.classList.add('on');
        // the end state on an interruption: no ripple left to replay
        if (instant) ground.classList.add('done');
        if (note) note.classList.add('on');
        places.forEach(function (x) {
          x.shown = true;
          draw(x);
          x.m.classList.add('on'); x.tag.classList.add('on'); x.row.classList.add('on');
          if (x.lead) x.lead.classList.add('on');
          if (instant) x.m.classList.remove('ping');
          if (x.walk) x.walk.classList.add('on');
          act(x, false);
        });
      }
    };
  }
  // "≈ 13 min": the figure, its unit a size smaller (as a list row's value)
  function timeText(t, cls) {
    var node = el('em', cls || null), s = tidy(t), m = /^(.*\d)\s+([A-Za-z.]+)$/.exec(s);
    node.appendChild(document.createTextNode(m ? m[1] : s));
    if (m) node.appendChild(el('small', null, ' ' + m[2]));
    return node;
  }
  // City blocks inside the disc: a street map's texture, one SVG that never
  // moves inside (it fades in with the disc). Generic -- a grid, a fixed
  // pattern of split blocks -- so it says nothing about what stands where;
  // only blocks wholly inside the rim, none on the pin's own ground.
  function discBlocks(R, clear) {
    var D = 2 * R, cell = Math.max(36, R * 0.25), lane = cell * 0.2, rx = cell * 0.09, lim = R * 0.95;
    var cols = Math.ceil(D / cell) + 1, off = (cols * cell - D) / 2, count = 0;
    var s = svgEl('svg', { viewBox: '0 0 ' + D.toFixed(1) + ' ' + D.toFixed(1), 'class': 'es-ar-blocks', 'aria-hidden': 'true' });
    function inside(x, y) { return Math.hypot(x - R, y - R) <= lim; }
    function rect(x0, y0, x1, y1) {
      if (!(inside(x0, y0) && inside(x1, y0) && inside(x0, y1) && inside(x1, y1))) return;
      if (Math.hypot((x0 + x1) / 2 - R, (y0 + y1) / 2 - R) < clear + cell * 0.35) return;
      s.appendChild(svgEl('rect', { x: x0.toFixed(1), y: y0.toFixed(1), width: (x1 - x0).toFixed(1),
                                    height: (y1 - y0).toFixed(1), rx: rx.toFixed(1), 'class': 'es-ar-block' }));
      count++;
    }
    for (var i = 0; i < cols; i++) {
      for (var j = 0; j < cols; j++) {
        var x0 = i * cell - off + lane / 2, y0 = j * cell - off + lane / 2, x1 = x0 + cell - lane, y1 = y0 + cell - lane;
        var cut = (i * 5 + j * 3) % 4;
        if (cut === 0) {                         // two blocks side by side
          var mx = (x0 + x1) / 2;
          rect(x0, y0, mx - lane * 0.3, y1); rect(mx + lane * 0.3, y0, x1, y1);
        } else if (cut === 2) {                  // two blocks one above the other
          var my = (y0 + y1) / 2;
          rect(x0, y0, x1, my - lane * 0.3); rect(x0, my + lane * 0.3, x1, y1);
        } else {
          rect(x0, y0, x1, y1);
        }
      }
    }
    return count ? s : null;
  }

  // Where it is (location_beach): a schematic map drawn on the screen -- the
  // sea slides in, the beach and the streets draw themselves, the project's
  // pin drops onto its spot; the park next door grows as she names it, and
  // each distance is a dashed line that runs from the project to its place
  // with the figure on it. Positions are % of the map (`x`, `y`; a road or a
  // measure from x1,y1 to x2,y2) and say "which side", not "how far": the
  // map says "not to scale" on itself, and the figures are the catalog's.
  // Second pass (owner 21 Oct: "Google Maps อาจจะต้องทำให้สวยกว่านี้"): a
  // street map's own texture -- generic city blocks between the streets, a
  // shoreline with surf, an organic park with its lake and trees, roads with
  // a casing and the main road in amber with a centre line, a teardrop pin --
  // all drawn, no map picture ("ไม่ต้องใช้รูปภาพแต่ให้เป็น motion", 13 Oct).
  // Every shape is still: what moves is whole layers, by transform/opacity.
  TEMPLATES.neighborhood = function (S, p) {
    // the legend under the map first, measured: the map takes the rest of
    // the height above Emma's head (it was 0.86 of its width whatever the
    // room, and pushed down until its legend reached her corner)
    var list = S.add(el('div', 'es-nb-rows'));
    list.style.gridTemplateColumns = 'repeat(' + Math.max(1, arr(p.rows).length) + ', minmax(0, 1fr))';
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
    var listH = rows.length ? list.offsetHeight || 0 : 0;
    var top = Math.round(S.H * TOP_VH / 100);
    var W = Math.round(S.W * 0.92);
    // 0.72-1.2 of its width: a legend that grew a line (a "walk 15–24 min"
    // under the beach, 21 Oct) must still end above her head, so the map
    // gives way rather than the cards running under her
    var fit = (S.room() - listH - (rows.length ? S.H * 0.022 : 0)) / W;
    var H = Math.round(W * (num(p.aspect) || Math.max(0.72, Math.min(1.2, fit))));
    var map = el('div', 'es-nb es-fade');
    S.root.insertBefore(map, list);
    map._w = W; map._h = H;
    map.style.width = W + 'px';
    map.style.height = H + 'px';
    map.style.left = Math.round((S.W - W) / 2) + 'px';
    map.style.top = top + 'px';
    list.style.top = Math.round(top + H + S.H * 0.022) + 'px';
    function pct(v) { return (num(v) != null ? Math.max(-5, Math.min(105, v)) : 0) + '%'; }
    function at(i, line) { return num(line) != null ? line === i : i === 0; }
    var drawn = [];       // {node, line, delay}: everything that appears on a line
    var sea = p.sea && num(p.sea.to_x) != null ? el('i', 'es-nb-sea') : null;
    if (sea) {
      sea.style.width = pct(p.sea.to_x);
      sea.appendChild(shoreline(H));                  // the surf along the sand
      map.appendChild(sea);
    }
    var beach = p.beach && num(p.beach.to_x) != null ? el('i', 'es-nb-beach') : null;
    var landX = 0;
    if (beach) {
      var from = sea ? p.sea.to_x : 0;
      beach.style.left = pct(from);
      beach.style.width = pct(p.beach.to_x - from);
      map.appendChild(beach);
      landX = p.beach.to_x;
    } else if (sea) {
      landX = p.sea.to_x;
    }
    var roads = arr(p.roads).filter(function (r) {
      return ![r.x1, r.y1, r.x2, r.y2].some(function (v) { return num(v) == null; });
    });
    var parks = arr(p.places).filter(function (pl) {
      return pl.kind === 'park' && num(pl.x) != null && num(pl.y) != null && num(pl.w) != null && num(pl.h) != null;
    });
    var projects = arr(p.places).filter(function (pl) {
      return pl.kind !== 'park' && num(pl.x) != null && num(pl.y) != null;
    });
    // the pin is ~13.75vw tall over its point
    var PIN_H = S.W * 0.1375;
    // ...and leave the footnote ("Schematic · not to scale") on open land
    var keep = [];
    if (p.footnote_en) {
      var fx = num(p.footnote_x) != null ? p.footnote_x / 100 * W : S.W * 0.03;
      keep.push([fx - S.W * 0.02, H - S.W * 0.085, fx + S.W * 0.46, H]);
    }
    var blk = cityBlocks(W, H, landX, roads, parks, projects, PIN_H, keep);
    if (blk) { map.appendChild(blk); drawn.push({ node: blk, line: null, delay: 150 }); }
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
    // label, a 217 ms frame as the map opened on the robot (13 Oct).
    // `over`: y is the point the label stands OVER (its bottom edge `over`
    // px above it, or under the point if there is no room above)
    var tags = [];
    function tag(text, x, y, cls, over) {
      var t = el('div', cls, tidy(text));
      map.appendChild(t);
      tags.push({ t: t, x: x, y: y, over: over });
      return t;
    }
    // the compass owns the top-right corner: a label that would sit under it
    // ("Sukhumvit Rd" placed 8% down, fixture recording 21 Oct) moves below it
    var NORTH = p.compass !== false ? S.W * 0.13 : 0;
    function placeTags() {
      var sizes = tags.map(function (g) { return [g.t.offsetWidth || W * 0.3, g.t.offsetHeight || W * 0.08]; });
      tags.forEach(function (g, k) {
        var w = sizes[k][0], h = sizes[k][1], cy = g.y / 100 * H;
        if (g.over != null) {
          var above = cy - g.over - S.W * 0.012 - h / 2;
          cy = above - h / 2 >= 8 ? above : cy + S.W * 0.05 + h / 2;
        }
        var cx = Math.max(w / 2 + 8, Math.min(W - w / 2 - 8, g.x / 100 * W));
        if (NORTH && cx + w / 2 > W - NORTH && cy - h / 2 < NORTH) cy = NORTH + h / 2 + 6;
        g.t.style.left = Math.round(cx) + 'px';
        g.t.style.top = Math.round(Math.max(h / 2 + 8, Math.min(H - h / 2 - 8, cy))) + 'px';
      });
    }
    // the streets of the scene draw one after another as it opens; a road
    // she names (`line`) draws as she says it, before its distance runs
    var streets = 0;
    roads.forEach(function (r) {
      var d = line(r.x1, r.y1, r.x2, r.y2, 'es-nb-road' + (r.label_en ? ' main' : ''));
      map.appendChild(d);
      var delay = num(r.line) != null && r.line > 0 ? 0 : 150 + 90 * streets++;
      drawn.push({ node: d, line: r.line, delay: delay, draw: true });
      if (r.label_en) {
        var lx = num(r.label_x) != null ? r.label_x : (r.x1 + r.x2) / 2;
        var ly = num(r.label_y) != null ? r.label_y : Math.min(r.y1, r.y2) + 8;
        drawn.push({ node: tag(r.label_en, lx, ly, 'es-nb-tag road es-pop'), line: r.line, delay: delay + 400 });
      }
    });
    if (p.beach && p.beach.label_en && beach) {
      // along the sand, reading upwards (placed by %, not by its size)
      var bt = map.appendChild(el('div', 'es-nb-vtag es-fade', tidy(p.beach.label_en)));
      bt.style.left = ((sea ? p.sea.to_x : 0) + (p.beach.to_x - (sea ? p.sea.to_x : 0)) / 2) + '%';
      bt.style.top = pct(num(p.beach.label_y) != null ? p.beach.label_y : 62);
      drawn.push({ node: bt, line: p.beach.line, delay: 700 });
    }
    if (p.area_label_en) drawn.push({ node: tag(p.area_label_en, num(p.area_x) != null ? p.area_x : 70,
                                               num(p.area_y) != null ? p.area_y : 7, 'es-nb-area es-fade'),
                                      line: null, delay: 500 });
    var marker = null;
    parks.forEach(function (pl) {
      var park = el('div', 'es-nb-park es-pop');
      park.style.left = pct(pl.x); park.style.top = pct(pl.y);
      park.style.width = pct(pl.w); park.style.height = pct(pl.h);
      park.appendChild(parkGlyph());
      map.appendChild(park);
      drawn.push({ node: park, line: pl.line, delay: 0 });
      if (pl.label_en) drawn.push({ node: tag(pl.label_en, pl.x + pl.w / 2, pl.y + pl.h + 6, 'es-nb-tag park es-up'),
                                    line: pl.line, delay: 250 });
    });
    projects.forEach(function (pl) {
      // the project: a pin that drops onto its spot, a ring on the ground
      // under it, its name above it
      var ring = S.spot(pl.x, pl.y);
      ring.classList.add('es-nb-ring');
      map.appendChild(ring);
      var shadow = el('i', 'es-nb-shadow es-fade');
      shadow.style.left = pct(pl.x); shadow.style.top = pct(pl.y);
      map.appendChild(shadow);
      var m = el('div', 'es-nb-pin es-drop');
      m.style.left = pct(pl.x); m.style.top = pct(pl.y);
      m.appendChild(pinGlyph());
      map.appendChild(m);
      marker = { m: m, ring: ring };
      drawn.push({ node: shadow, line: pl.line, delay: 450 });
      drawn.push({ node: m, line: pl.line, delay: 450, then: function () { ring.classList.add('on'); } });
      if (pl.label_en) drawn.push({ node: tag(pl.label_en, pl.x, pl.y, 'es-nb-tag project es-up', PIN_H),
                                    line: pl.line, delay: 650 });
    });
    arr(p.measures).forEach(function (ms) {
      if ([ms.x1, ms.y1, ms.x2, ms.y2].some(function (v) { return num(v) == null; })) return;
      var d = line(ms.x1, ms.y1, ms.x2, ms.y2, 'es-nb-measure');
      map.appendChild(d);
      drawn.push({ node: d, line: ms.line, delay: 150, draw: true });
      var end = el('i', 'es-nb-end es-pop');
      end.style.left = pct(ms.x2); end.style.top = pct(ms.y2);
      map.appendChild(end);
      drawn.push({ node: end, line: ms.line, delay: 850 });
      if (ms.label_en) {
        var mx = num(ms.label_x) != null ? ms.label_x : (ms.x1 + ms.x2) / 2;
        var my = num(ms.label_y) != null ? ms.label_y : (ms.y1 + ms.y2) / 2 + 7;
        drawn.push({ node: tag(ms.label_en, mx, my, 'es-nb-pill es-pop'), line: ms.line, delay: 900 });
      }
    });
    if (p.compass !== false) {
      var n = el('div', 'es-nb-n');
      n.appendChild(el('i', 'es-nb-needle'));
      n.appendChild(el('b', null, 'N'));
      map.appendChild(n);
    }
    var note = p.footnote_en ? el('div', 'es-nb-note', p.footnote_en) : null;
    if (note) {
      if (num(p.footnote_x) != null) note.style.left = pct(p.footnote_x);
      map.appendChild(note);
    }
    placeTags();
    function put(x) {
      if (x.done) return;
      x.done = true;
      if (x.draw) draw(x.node); else x.node.classList.add('on');
      if (x.then) S.later(450, x.then);
    }
    var opened = false;
    function open() {
      if (opened) return;
      opened = true;
      S.reveal(map, function () {
        if (sea) sea.classList.add('on');
        S.later(250, function () { if (beach) beach.classList.add('on'); });
      });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        drawn.forEach(function (x) { if (at(i, x.line)) S.later(x.delay + (i === 0 ? 200 : 0), function () { put(x); }); });
        var j = 0;
        rows.forEach(function (x) { if (at(i, x.line)) S.later(350 + 160 * j++, function () { x.node.classList.add('on'); }); });
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

  // The shoreline: a wavy edge of sea over the sand with a line of surf on
  // it, on a 10 x 100 box stretched to the sea's edge (still; it slides in
  // with the sea). About one wave per 4.4vw of height.
  function shoreline(hpx) {
    var n = Math.max(8, Math.round(hpx / (root.innerWidth || 1080) / 0.044));
    var d = 'M0 0', k;
    for (k = 0; k < n; k++) {
      var y0 = k * 100 / n, y1 = (k + 1) * 100 / n;
      d += 'Q9 ' + ((y0 + y1) / 2).toFixed(2) + ' 0 ' + y1.toFixed(2);
    }
    var s = svgEl('svg', { viewBox: '0 0 10 100', preserveAspectRatio: 'none', 'class': 'es-nb-shore', 'aria-hidden': 'true' });
    s.appendChild(svgEl('path', { d: d + 'Z', 'class': 'es-shore-sea' }));
    s.appendChild(svgEl('path', { d: d, 'class': 'es-shore-surf' }));
    return s;
  }
  function svgEl(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) e.setAttribute(k, String(attrs[k]));
    return e;
  }
  // A map pin: a teardrop standing on its point, the project's house in its
  // head. Drawn once; it moves as a whole (.es-drop / .es-mpin).
  function pinGlyph() {
    var s = svgEl('svg', { viewBox: '0 0 48 60', 'class': 'es-pinsvg', 'aria-hidden': 'true' });
    s.appendChild(svgEl('path', { d: 'M24 59C24 59 4 37.6 4 22.6A20 20 0 0 1 44 22.6C44 37.6 24 59 24 59Z', 'class': 'es-pin-body' }));
    s.appendChild(svgEl('circle', { cx: 24, cy: 22.6, r: 13.2, 'class': 'es-pin-face' }));
    s.appendChild(svgEl('path', { d: ICONS.home, transform: 'translate(13.6 12.2) scale(.866)', 'class': 'es-pin-ico' }));
    return s;
  }
  // A park seen from above: an organic lawn, a lake with its shore, a ring
  // of trees, a footpath. On a 100-unit square stretched to the park's box
  // (the parks in the catalog are about square, so circles stay round).
  var PARK_TREES = [[15, 30], [22, 19], [36, 12], [53, 11], [70, 14], [84, 26], [90, 44], [88, 62],
                    [80, 78], [64, 87], [45, 89], [27, 83], [15, 70], [11, 50]];
  function parkGlyph() {
    var s = svgEl('svg', { viewBox: '0 0 100 100', preserveAspectRatio: 'none', 'class': 'es-nb-parksvg', 'aria-hidden': 'true' });
    s.appendChild(svgEl('path', { 'class': 'es-park-land',
      d: 'M14 16C27 4 58 1 78 8C95 15 99 38 96 58C93 81 76 97 51 96C27 95 7 85 4 61C2 42 4 27 14 16Z' }));
    s.appendChild(svgEl('path', { 'class': 'es-park-path', d: 'M7 66C27 79 61 82 93 47' }));
    s.appendChild(svgEl('path', { 'class': 'es-park-lake',
      d: 'M31 37C39 25 62 24 70 35C78 46 72 62 58 66C44 70 27 65 25 53C24 46 27 41 31 37Z' }));
    s.appendChild(svgEl('path', { 'class': 'es-park-glint', d: 'M38 36C45 31 56 31 62 35' }));
    PARK_TREES.forEach(function (t) { s.appendChild(svgEl('circle', { cx: t[0], cy: t[1], r: 4.6, 'class': 'es-park-tree' })); });
    return s;
  }
  // Generic city blocks between the streets: the texture of a street map,
  // one SVG with no motion inside it (it fades in as a whole). Blocks, not
  // buildings -- nothing here says what stands where: they fill the land
  // between the catalog's own streets, a fixed pattern per cell, and leave
  // the park and the project's spot open.
  function cityBlocks(W, H, landX, roads, parks, projects, pinH, keep) {
    var xs = [landX, 100], ys = [0, 100];
    roads.forEach(function (r) {
      if (Math.abs(r.x1 - r.x2) < 0.5 && r.x1 > landX + 1) xs.push(r.x1);
      else if (Math.abs(r.y1 - r.y2) < 0.5) ys.push(r.y1);
    });
    function uniq(v) {
      v.sort(function (a, b) { return a - b; });
      return v.filter(function (x, k) { return k === 0 || x - v[k - 1] > 1.5; });
    }
    xs = uniq(xs); ys = uniq(ys);
    if (xs.length < 2 || ys.length < 2) return null;
    var avoid = parks.map(function (pl) {
      return [(pl.x - 2) / 100 * W, (pl.y - 2) / 100 * H, (pl.x + pl.w + 2) / 100 * W, (pl.y + pl.h + 2) / 100 * H];
    }).concat(projects.map(function (pl) {
      var x = pl.x / 100 * W, y = pl.y / 100 * H;
      return [x - pinH * 0.55, y - pinH * 1.1, x + pinH * 0.55, y + pinH * 0.35];
    })).concat(arr(keep));
    function hits(a, b, c, d) {
      for (var k = 0; k < avoid.length; k++) {
        var r = avoid[k];
        if (a < r[2] && c > r[0] && b < r[3] && d > r[1]) return true;
      }
      return false;
    }
    var s = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, preserveAspectRatio: 'none', 'class': 'es-nb-blocks es-fade', 'aria-hidden': 'true' });
    var m = W * 0.017, g = W * 0.013, rx = W * 0.009, count = 0;
    for (var i = 0; i < xs.length - 1; i++) {
      for (var j = 0; j < ys.length - 1; j++) {
        var x0 = xs[i] / 100 * W + m, x1 = xs[i + 1] / 100 * W - m;
        var y0 = ys[j] / 100 * H + m, y1 = ys[j + 1] / 100 * H - m;
        if (x1 - x0 < W * 0.028 || y1 - y0 < W * 0.028) continue;
        var wide = x1 - x0 >= y1 - y0, span = wide ? x1 - x0 : y1 - y0;
        var parts = Math.max(1, Math.min(1 + ((i * 5 + j * 3) % 3), Math.floor(span / (W * 0.09))));
        for (var k = 0; k < parts; k++) {
          var a = k / parts, b = (k + 1) / parts;
          var bx0 = x0, bx1 = x1, by0 = y0, by1 = y1;
          if (wide) { bx0 = x0 + (x1 - x0) * a + (k ? g / 2 : 0); bx1 = x0 + (x1 - x0) * b - (k < parts - 1 ? g / 2 : 0); }
          else { by0 = y0 + (y1 - y0) * a + (k ? g / 2 : 0); by1 = y0 + (y1 - y0) * b - (k < parts - 1 ? g / 2 : 0); }
          if (hits(bx0, by0, bx1, by1)) continue;
          s.appendChild(svgEl('rect', { x: bx0.toFixed(1), y: by0.toFixed(1), width: (bx1 - bx0).toFixed(1),
                                        height: (by1 - by0).toFixed(1), rx: rx.toFixed(1), 'class': 'es-nb-block' }));
          count++;
        }
      }
    }
    return count ? s : null;
  }

  // The real map (owner 27 Oct: "พวกแมพเอาจากตรงนี้" -- the sales gallery's own
  // map export -- and his choice, "แผนที่จริง + motion ตามเสียง"). The picture
  // IS the map, rendered on the PC from that export by
  // scripts/render_story_maps.py: its OSM streets and buildings, the project's
  // 3D model, its night palette. Over it, what moves: the project's pin drops
  // onto the model; as she names a place, its route draws itself along the
  // real streets (the car route the report's times were taken on), the place
  // drops at its end with one ripple, its short name and time beside it, its
  // row slides into the list under the map. On foot (a row's `walk`): its own
  // route, dotted, drawn on the line that says it.
  // Every position is the projection the renderer computed on that very
  // picture (`project`, `rows[].pin`, `rows[].route` in % of it -- the build
  // refuses an overlay computed on another picture); every figure on screen
  // is the catalog's `value_en`, never a length measured off a route. A row
  // without a pin (no checked coordinate) is a row in the list only.
  // Motion is transform/opacity, plus the routes' stroke-dashoffset (a few
  // short SVG paths, each drawn once).
  var mapSeq = 0;
  TEMPLATES.map = function (S, p) {
    var rows = arr(p.rows).filter(function (r) { return r && typeof r === 'object'; });
    var n = rows.length, W = S.W;
    var size = n <= 2 ? 'xl' : n <= 4 ? 'roomy' : 'dense';
    var top0 = Math.round(S.H * TOP_VH / 100), gap = Math.round(S.H * 0.014);
    var aspect = num(p.aspect) > 0 ? p.aspect : 1;
    var head = S.add(el('div', 'es-ar-o es-up ' + size));
    head.appendChild(el('b', null, p.origin_en || ''));
    if (p.origin_sub_en) {
      var hs = el('span');
      if (p.origin_icon !== 'none') hs.appendChild(icon(p.origin_icon || 'car'));
      hs.appendChild(document.createTextNode(tidy(p.origin_sub_en)));
      head.appendChild(hs);
    }
    head.style.top = top0 + 'px';
    // the list under the map, built and measured first (the rings' rows)
    var list = S.add(el('div', 'es-ar-list es-mp-list ' + size));
    var legend = rows.map(function (r) {
      var d = el('div', 'es-ar-r es-left' + kindCls(r.icon));
      var ic = el('i', 'es-jr-node');
      ic.appendChild(icon(r.icon));
      d.appendChild(ic);
      var body = el('div', 'es-ar-b');
      body.appendChild(el('b', null, r.label_en || ''));
      if (r.sub_en) body.appendChild(el('span', null, tidy(r.sub_en)));
      var walk = null;
      if (r.walk && r.walk.value_en) {
        walk = el('em', 'es-ar-walk es-pop');
        walk.appendChild(icon('walk'));
        walk.appendChild(document.createTextNode(tidy(r.walk.value_en)));
        body.appendChild(walk);
      }
      d.appendChild(body);
      // landscape: the time under the name (a narrow column: a long name and
      // its time side by side ran into each other)
      if (r.value_en) (S.land ? body : d).appendChild(timeText(r.value_en, 'es-ar-v'));
      list.appendChild(d);
      return { node: d, walk: walk };
    });
    // The map takes what the list leaves above Emma's head, its own shape
    // kept (the pins are % of THIS picture: a cropped or stretched map moves
    // every pin off its street). A long list tightens first, then the map
    // gives way down to ~0.62 of the width, and only then the list scales.
    // landscape: the map as tall as the stage on the left (its own shape),
    // the project's name and the list in a column beside it
    var LM = null;
    if (S.land) {
      var cwL = Math.min(Math.round(S.LW * 0.6), Math.round((S.LH - 2 * S.M) * aspect));
      LM = { cw: cwL, ch: Math.round(cwL / aspect), iw: Math.min(Math.round(S.LW * 0.42), S.LW - 2 * S.M - cwL - S.G) };
      LM.left = Math.round((S.LW - (cwL + S.G + LM.iw)) / 2);
      LM.ileft = LM.left + cwL + S.G;
      S.place(head, LM.ileft, null, LM.iw);
      S.place(list, LM.ileft, null, LM.iw);
    }
    var headH = head.offsetHeight || 0;
    var spare = S.room() - headH - gap * 2;
    var mx = Math.round(W * 0.045), cw = W - 2 * mx, ch = Math.round(cw / aspect);
    var listH = n ? list.offsetHeight || 0 : 0;
    if (LM) {
      cw = LM.cw; ch = LM.ch;
      var roomL = S.LH - 2 * S.M - headH - gap;
      if (listH > roomL && size !== 'xl') { list.classList.add('tight'); listH = list.offsetHeight || 0; }
      var kL = listH > roomL ? roomL / Math.max(1, listH) : 1;
      if (kL < 1) {
        list.style.transformOrigin = '0 0';
        list.style.transform = 'scale(' + kL.toFixed(4) + ')';
      }
      var gTop = Math.max(S.M, Math.round(S.LH / 2 - (headH + gap + listH * kL) / 2));
      head.style.top = gTop + 'px';
    } else {
      if (spare - listH < ch && size !== 'xl') {
        list.classList.add('tight');
        listH = list.offsetHeight || 0;
      }
      if (spare - listH < ch) {
        ch = Math.max(Math.round(W * 0.62 / aspect), Math.round(spare - listH));
        cw = Math.min(W - 2 * mx, Math.round(ch * aspect));
        ch = Math.round(cw / aspect);
      }
      if (spare - listH < ch) {
        var k = (spare - ch) / Math.max(1, listH);
        if (k < 1) {
          list.style.transformOrigin = '50% 0';
          list.style.transform = 'scale(' + Math.max(0.6, k).toFixed(4) + ')';
        }
      }
    }
    var cardTop = top0 + headH + gap;
    if (LM) cardTop = Math.round((S.LH - ch) / 2);
    var card = el('div', 'es-ar es-mp es-fade ' + size);
    S.root.insertBefore(card, list);
    card.style.left = Math.round(LM ? LM.left : (W - cw) / 2) + 'px';
    card.style.width = cw + 'px';
    card.style.top = cardTop + 'px';
    card.style.height = ch + 'px';
    list.style.top = (LM ? gTop + headH + gap : cardTop + ch + gap) + 'px';
    var base = S.img(p.map_asset, 'es-mp-img');
    card.appendChild(base);
    function px(pt) { return [pt.x / 100 * cw, pt.y / 100 * ch]; }
    // the routes: one SVG over the picture, in its pixels
    var svg = svgEl('svg', { viewBox: '0 0 ' + cw + ' ' + ch, 'class': 'es-mp-svg', 'aria-hidden': 'true' });
    card.appendChild(svg);
    var uid = 'esmp' + (++mapSeq);
    function d3(line) {
      return arr(line).map(function (q, j) {
        return (j ? 'L' : 'M') + (q[0] / 100 * cw).toFixed(1) + ' ' + (q[1] / 100 * ch).toFixed(1);
      }).join('');
    }
    // its length on screen, for how long it takes to draw (the farther, the
    // longer: 0.7-1.6 s, so every route draws at about the same speed)
    function len(line) {
      var L = 0, a = arr(line);
      for (var j = 1; j < a.length; j++) {
        L += Math.hypot((a[j][0] - a[j - 1][0]) / 100 * cw, (a[j][1] - a[j - 1][1]) / 100 * ch);
      }
      return L;
    }
    var M = Math.round(W * (size === 'xl' ? 0.088 : size === 'roomy' ? 0.078 : 0.07));
    var pinW = W * 0.08, pinH = W * 0.1;
    var pr = p.project ? px(p.project) : [cw / 2, ch / 2];
    function put(node, x, y, w, h) {
      node.style.left = Math.round(x) + 'px'; node.style.top = Math.round(y) + 'px';
      if (w != null) { node.style.width = Math.round(w) + 'px'; node.style.height = Math.round(h) + 'px'; }
    }
    // the project: its ground ring, a shadow, the pin standing on the model
    var ground = S.spot(50, 50);
    ground.classList.add('es-nb-ring');
    put(ground, pr[0], pr[1]);
    card.appendChild(ground);
    var shadow = el('i', 'es-nb-shadow es-fade');
    put(shadow, pr[0], pr[1]);
    card.appendChild(shadow);
    var pin = el('div', 'es-nb-pin es-ar-pin es-drop');
    put(pin, pr[0], pr[1]);
    pin.appendChild(pinGlyph());
    card.appendChild(pin);
    var area = p.area_label_en ? card.appendChild(el('div', 'es-mp-area es-fade', tidy(p.area_label_en))) : null;
    var attr = card.appendChild(el('div', 'es-mp-attr es-fade', tidy(p.attribution_en || '© OpenStreetMap contributors')));
    var attrBox = [0, ch - (attr.offsetHeight || W * 0.04) - W * 0.01, (attr.offsetWidth || W * 0.5) + W * 0.03, ch];
    var areaBox = null;
    if (area) {
      var aw = area.offsetWidth || W * 0.4, ah = area.offsetHeight || W * 0.05;
      put(area, (cw - aw) / 2, W * 0.02);
      areaBox = [(cw - aw) / 2, W * 0.02, (cw + aw) / 2, W * 0.02 + ah];
    }
    var places = rows.map(function (r, k) {
      var x = { r: r, k: k, line: num(r.line) != null ? r.line : 0, row: legend[k].node, walk: legend[k].walk,
                walkLine: r.walk && num(r.walk.line) != null ? r.walk.line : null };
      if (!r.pin) return x;
      var kc = kindCls(r.icon), at = px(r.pin);
      x.tx = at[0]; x.ty = at[1];
      if (r.route) {
        var dd = d3(r.route);
        x.case = svg.appendChild(svgEl('path', { d: dd, pathLength: 1, 'class': 'es-mp-case' + kc }));
        x.path = svg.appendChild(svgEl('path', { d: dd, pathLength: 1, 'class': 'es-mp-route' + kc }));
        x.drawS = Math.max(0.7, Math.min(1.6, len(r.route) / (W * 0.55)));
        x.case.style.setProperty('--d', x.drawS.toFixed(2) + 's');
        x.path.style.setProperty('--d', x.drawS.toFixed(2) + 's');
      }
      if (r.walk_route) {
        // dotted, drawn by a mask that grows along it (a dotted line has no
        // dash left to animate itself with)
        var wd = d3(r.walk_route), mid = uid + 'w' + k;
        var mask = svgEl('mask', { id: mid, maskUnits: 'userSpaceOnUse', x: 0, y: 0, width: cw, height: ch });
        x.wmask = mask.appendChild(svgEl('path', { d: wd, pathLength: 1, 'class': 'es-mp-wmask' }));
        svg.appendChild(mask);
        x.wpath = svg.appendChild(svgEl('path', { d: wd, 'class': 'es-mp-walkline' + kc, mask: 'url(#' + mid + ')' }));
      }
      x.kc = kc;
      return x;
    });
    var marked = places.filter(function (x) { return x.tx != null; });
    // A place too near the pin or another place to be seen (Big Whale is
    // 150 m from the project: ~20 px on a map that must also reach Na
    // Jomtien) moves to the nearest clear spot round its real point, away
    // from the pin first, and keeps a thin stem back to a dot ON its real
    // point -- the map still says exactly where it is. Places that are clear
    // where they are stay there; then the others, in the order she names them.
    var r0 = M / 2 + W * 0.006, edge0 = W * 0.012;
    var pinBox = [pr[0] - pinW / 2 - W * 0.008, pr[1] - pinH - W * 0.008, pr[0] + pinW / 2 + W * 0.008, pr[1] + W * 0.035];
    var spots = [];
    function clash(cx, cy) {
      var nx = Math.max(pinBox[0], Math.min(pinBox[2], cx)), ny = Math.max(pinBox[1], Math.min(pinBox[3], cy));
      if (Math.hypot(cx - nx, cy - ny) < r0) return true;
      if (cx < r0 + edge0 || cx > cw - r0 - edge0 || cy < r0 + edge0 || cy > ch - r0 - edge0) return true;
      // under the map's credit line (7-Eleven's mark half behind it, first recording)
      if (cx + r0 > attrBox[0] && cx - r0 < attrBox[2] && cy + r0 > attrBox[1]) return true;
      if (areaBox && cx + r0 > areaBox[0] && cx - r0 < areaBox[2] && cy - r0 < areaBox[3]) return true;
      for (var s = 0; s < spots.length; s++) if (Math.hypot(cx - spots[s][0], cy - spots[s][1]) < M + W * 0.01) return true;
      return false;
    }
    marked.forEach(function (x) {
      if (!clash(x.tx, x.ty)) { x.mx = x.tx; x.my = x.ty; spots.push([x.mx, x.my]); }
    });
    marked.forEach(function (x) {
      if (x.mx != null) return;
      var th0 = Math.atan2(x.ty - pr[1], x.tx - pr[0]);
      if (Math.hypot(x.ty - pr[1], x.tx - pr[0]) < 1) th0 = Math.PI / 2;
      var radii = [0.75, 1.05, 1.4, 1.8, 2.3, 2.9, 3.6], found = null;
      for (var ri = 0; ri < radii.length && !found; ri++) {
        for (var ai = 0; ai < 16 && !found; ai++) {
          var th = th0 + (ai % 2 ? 1 : -1) * Math.ceil(ai / 2) * Math.PI / 8;
          var cx = x.tx + radii[ri] * M * Math.cos(th), cy = x.ty + radii[ri] * M * Math.sin(th);
          if (!clash(cx, cy)) found = [cx, cy];
        }
      }
      // nowhere clear (a crowded corner): on its point, over the others
      x.mx = found ? found[0] : x.tx; x.my = found ? found[1] : x.ty;
      spots.push([x.mx, x.my]);
    });
    marked.forEach(function (x) {
      var kc = x.kc, r = x.r;
      var moved = Math.hypot(x.mx - x.tx, x.my - x.ty);
      if (moved > M * 0.5) {
        // the real point and the stem to it, under the mark
        var tdot = el('i', 'es-mp-true es-fade' + kc);
        put(tdot, x.tx, x.ty);
        card.appendChild(tdot);
        var stem = el('i', 'es-ar-lead es-mp-stem' + kc);
        var ex = (x.mx - x.tx) / moved, ey = (x.my - x.ty) / moved;
        put(stem, x.tx, x.ty);
        stem.style.width = Math.round(moved - M / 2) + 'px';
        stem.style.transform = 'rotate(' + Math.atan2(ey, ex).toFixed(4) + 'rad)';
        card.appendChild(stem);
        x.tdot = tdot; x.stem = stem;
      }
      var m = el('div', 'es-ar-m es-drop' + kc);
      put(m, x.mx, x.my);
      var ping = el('b', 'es-ar-mping');
      put(ping, -M / 2, -M / 2, M, M);
      m.appendChild(ping);
      var dot = el('i', 'es-jr-node es-ar-dot');
      put(dot, -M / 2, -M / 2, M, M);
      dot.appendChild(icon(r.icon));
      m.appendChild(dot);
      card.appendChild(m);
      // four places or more: the name only on the map (its time is in its row
      // right under it) -- five name-and-time labels on one map covered each
      // other's marks (first recording, nearby_attractions)
      var tag = el('div', 'es-ar-tag es-pop' + kc + (n >= 4 ? ' brief' : ''));
      tag.appendChild(el('b', null, r.short_en || r.label_en || ''));
      if (r.value_en && n < 4) tag.appendChild(timeText(r.value_en));
      card.appendChild(tag);
      x.m = m; x.tag = tag;
    });
    // Every label measured at once, then placed beside its mark: clear of the
    // pin, the marks, the labels placed before it, the map's own credit line
    // and the area name, inside the map; a route under it costs a little (a
    // map's labels lie over roads). Deterministic -- arithmetic on one read.
    var tagSize = marked.map(function (x) { return [x.tag.offsetWidth || W * 0.25, x.tag.offsetHeight || W * 0.1]; });
    var pad = W * 0.008, edge = W * 0.012, half = M / 2 + 2;
    var taken = [[pr[0] - pinW / 2, pr[1] - pinH, pr[0] + pinW / 2, pr[1] + W * 0.012], attrBox];
    if (areaBox) taken.push(areaBox);
    marked.forEach(function (x) {
      taken.push([x.mx - half, x.my - half, x.mx + half, x.my + half]);
      if (x.tdot) taken.push([x.tx - W * 0.012, x.ty - W * 0.012, x.tx + W * 0.012, x.ty + W * 0.012]);
    });
    var routePts = [];
    marked.forEach(function (x) {
      arr(x.r.route).concat(arr(x.r.walk_route)).forEach(function (q) { routePts.push(px({ x: q[0], y: q[1] })); });
    });
    function cost(box, more) {
      var c = 0, all = more ? taken.concat(more) : taken;
      for (var t = 0; t < all.length; t++) { var a = boxHit(box, all[t]); if (a) c += W * 4 + a; }
      for (var q = 0; q < routePts.length; q++) {
        var pt = routePts[q];
        if (pt[0] > box[0] && pt[0] < box[2] && pt[1] > box[1] && pt[1] < box[3]) c += W * 0.03;
      }
      return c;
    }
    var DIRS8 = [[1, 0], [-1, 0], [0, -1], [0, 1], [0.71, -0.71], [-0.71, -0.71], [0.71, 0.71], [-0.71, 0.71]];
    function label(x, i, fixed) {
      var w = tagSize[i][0], h = tagSize[i][1], reach0 = half + W * 0.01, best = null, bestCost = Infinity;
      // away from the project first: a label toward the pin hides where it is
      var ox = x.mx - pr[0], oy = x.my - pr[1], ol = Math.hypot(ox, oy) || 1;
      var dirs = DIRS8.slice().sort(function (a, b) { return (b[0] * ox + b[1] * oy) / ol - (a[0] * ox + a[1] * oy) / ol; });
      [1, 1.8, 2.6, 3.4].forEach(function (f) {
        dirs.forEach(function (d) {
          if (bestCost === 0) return;
          var tx = x.mx + d[0] * reach0 * f + (d[0] > 0.25 ? w / 2 : d[0] < -0.25 ? -w / 2 : 0);
          var ty = x.my + d[1] * reach0 * f + (d[1] > 0.25 ? h / 2 : d[1] < -0.25 ? -h / 2 : 0);
          tx = Math.max(w / 2 + edge, Math.min(cw - w / 2 - edge, tx));
          ty = Math.max(h / 2 + edge, Math.min(ch - h / 2 - edge, ty));
          var c = cost([tx - w / 2 - pad, ty - h / 2 - pad, tx + w / 2 + pad, ty + h / 2 + pad], fixed);
          c += Math.max(0, Math.hypot(tx - x.mx, ty - x.my) - (reach0 + Math.max(w, h))) * 2;
          if (c < bestCost) { bestCost = c; best = [tx - w / 2, ty - h / 2, tx + w / 2, ty + h / 2]; }
        });
      });
      return { box: best, cost: bestCost };
    }
    var orders = [marked.map(function (x, i) { return i; }),
                  marked.map(function (x, i) { return i; }).reverse()];
    var layout = null;
    orders.forEach(function (order) {
      if (layout && layout.cost === 0) return;
      var fixed = [], total = 0, boxes = [];
      order.forEach(function (i) {
        var got = label(marked[i], i, fixed);
        boxes[i] = got.box;
        fixed.push(got.box);
        total += got.cost;
      });
      if (!layout || total < layout.cost) layout = { cost: total, boxes: boxes };
    });
    marked.forEach(function (x, i) {
      var bx = layout.boxes[i];
      put(x.tag, bx[0], bx[1]);
      var nx = Math.max(bx[0], Math.min(bx[2], x.mx)), ny = Math.max(bx[1], Math.min(bx[3], x.my));
      var d = Math.hypot(nx - x.mx, ny - x.my);
      if (d - M / 2 > W * 0.028) {
        var ex = (nx - x.mx) / d, ey = (ny - x.my) / d, lead = el('i', 'es-ar-lead');
        put(lead, x.mx + ex * M / 2, x.my + ey * M / 2);
        lead.style.width = Math.round(d - M / 2) + 'px';
        lead.style.transform = 'rotate(' + Math.atan2(ey, ex).toFixed(4) + 'rad)';
        card.insertBefore(lead, x.m);
        x.lead = lead;
      }
    });
    var rested = false;
    function act(x, on) {
      [x.m, x.tag, x.row, x.path].forEach(function (e) { if (e) e.classList.toggle('act', !!on && !rested); });
    }
    function draw(x) {
      if (x.case) x.case.classList.add('on');
      if (x.path) x.path.classList.add('on');
      // the route being drawn is the bright one, from its first pixel
      if (x.path && !rested) {
        places.forEach(function (o) { if (o.line !== x.line && o.path) o.path.classList.remove('act'); });
        x.path.classList.add('act');
        card.classList.add('has-act');
      }
    }
    function land(x) {
      places.forEach(function (o) { if (o.line !== x.line) act(o, false); });
      x.row.classList.add('on');
      if (x.m) x.m.classList.add('on');
      if (x.tdot) { x.tdot.classList.add('on'); x.stem.classList.add('on'); }
      act(x, true);
      card.classList.toggle('has-act', !rested && !!x.path);
      if (x.m && !S.instant && !rested) {
        x.m.classList.add('ping');
        S.later(1700, function () { x.m.classList.remove('ping'); });
      }
    }
    function walkOn(x) {
      if (x.wmask) x.wmask.classList.add('on');
      if (x.walk) x.walk.classList.add('on');
    }
    function show(x, delay) {
      S.later(delay, function () {
        if (x.shown) return;
        x.shown = true;
        draw(x);
        var ms = x.path ? Math.round(x.drawS * 1000) - 150 : 0;
        S.later(ms, function () { land(x); });
        S.later(ms + 220, function () { if (x.tag) x.tag.classList.add('on'); if (x.lead) x.lead.classList.add('on'); });
        if (x.walkLine != null && x.walkLine <= x.line) S.later(ms + 500, function () { walkOn(x); });
      });
    }
    var opened = false;
    function open() {
      if (opened) return;
      opened = true;
      head.classList.add('on');
      S.reveal(card, function () {
        S.later(250, function () { pin.classList.add('on'); shadow.classList.add('on'); });
        S.later(800, function () { ground.classList.add('on'); });
        S.later(400, function () { attr.classList.add('on'); if (area) area.classList.add('on'); });
      });
    }
    return {
      beat: function (i) {
        if (i === 0) open();
        var j = 0;
        places.forEach(function (x) { if (x.line === i) show(x, (i === 0 ? 700 : 0) + 420 * j++); });
        places.forEach(function (x) {
          if (x.walkLine === i && x.walkLine > x.line) S.later(200, function () { walkOn(x); });
        });
      },
      rest: function (instant) {
        rested = true;
        open();
        card.classList.add('on');
        card.classList.remove('has-act');
        pin.classList.add('on'); shadow.classList.add('on'); ground.classList.add('on');
        if (instant) ground.classList.add('done');
        attr.classList.add('on');
        if (area) area.classList.add('on');
        places.forEach(function (x) {
          x.shown = true;
          draw(x);
          x.row.classList.add('on');
          if (x.m) { x.m.classList.add('on'); if (instant) x.m.classList.remove('ping'); }
          if (x.tdot) { x.tdot.classList.add('on'); x.stem.classList.add('on'); }
          if (x.tag) x.tag.classList.add('on');
          if (x.lead) x.lead.classList.add('on');
          walkOn(x);
          act(x, false);
        });
      }
    };
  };

  // ------------------------------------------------------------- public API

  var active = null;
  var api = {
    templates: Object.keys(TEMPLATES),
    // Does this template lay out across a landscape stage (play's
    // opts.layout = 'land', the room TV)? Else the page shows it portrait.
    landscape: function (template) { return LAND[template] === 1; },
    // A new story replaces whatever story was on screen.
    play: function (data, opts) {
      api.teardown();
      if (!data || !TEMPLATES[data.template] || !opts || !opts.mount) return false;
      // A template that throws on a malformed payload (a null row: audit 27
      // Oct) left a half-built stage nobody owned and the error escaped the
      // socket handler: an empty stage under the close button. Gone, and the
      // page is told (false) so it can take its screen back.
      try {
        active = new Story(data, opts);
      } catch (e) {
        if (root.console) console.warn('story', data.id, e);
        active = null;                   // the template throws before any timer starts
        var left = opts.mount.querySelectorAll ? opts.mount.querySelectorAll('.es-root') : [];
        for (var k = 0; k < left.length; k++) if (left[k].parentNode) left[k].parentNode.removeChild(left[k]);
        return false;
      }
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
    // One answer's caption timing (the page's reply line): see Caption.
    caption: function () { return new Caption(); },
    // For the dev harness (scripts/record_stories.py) and debugging only.
    state: function () {
      if (!active) return null;
      var now = active.opts.clock ? active.opts.clock() : null;
      return { id: active.data.id, fired: active.fired, beats: active.n, ended: !!active.ended,
               instant: !!active.endedInstant, dead: active.dead,
               played: now != null ? active.clock.played(now) : null, sched: active.clock.sched,
               complete: active.clock.complete, cueAt: active.clock.cueAt.slice() };
    },
    _clock: { BeatClock: BeatClock, norm: norm, CLOCK: CLOCK, tidy: tidy, CAPTION: CAPTION, segments: segments,
              leadRateFor: leadRateFor, resetRates: function () { learnedRate = {}; } },
    // tests/client_story_clock.cjs only: apply beats up to i, as the clock would
    _fireTo: function (i) { if (active && !active.dead) active.fire(i); }
  };
  root.EmmaStory = api;
})(typeof window !== 'undefined' ? window : globalThis);
