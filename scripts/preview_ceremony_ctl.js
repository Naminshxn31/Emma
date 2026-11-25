// The PREVIEW's controls (scripts/preview_ceremony_show.py only -- never served on the robot
// or on the day: the preview injects this into its own copy of /mirror).
// Owner 18 Nov 2026: "ตอนเล่นมันกดกลับกดหยุดไม่ได้หรอ จะได้เช็ค".
//
// Pause has to stop EVERY clock the film reads, or the frame you stop on is not a frame of
// the film: her voice (the AudioContext -- the subtitle and the beats follow it), the
// page's wall clock (Date.now: its "silent 6 s = take the slide down" watchdog would clear
// the stage under a pause), the frame clock (requestAnimationFrame's time: drifting cameras),
// CSS transitions/animations and videos. Everything is wrapped here, before the page's own
// scripts run (this file is the first script of <head>).
(function () {
  'use strict';
  var rawPerf = performance.now.bind(performance);
  var rawDate = Date.now.bind(Date);
  var rawRaf = window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : null;
  var off = 0, pausedAt = null;           // ms of pause so far; when this pause began (rawPerf)
  function lag() { return off + (pausedAt !== null ? rawPerf() - pausedAt : 0); }
  Date.now = function () { return rawDate() - lag(); };
  performance.now = function () { return rawPerf() - lag(); };
  // the page's timers too (review 18 Nov: a beat or a clean-up on a setTimeout fired inside
  // a pause): one due during a pause waits for play, its id still clears it
  var rawSetTimeout = window.setTimeout.bind(window), rawClearTimeout = window.clearTimeout.bind(window);
  var rawSetInterval = window.setInterval.bind(window), rawClearInterval = window.clearInterval.bind(window);
  var timers = {}, timerSeq = 1e6;
  window.setTimeout = function (fn, ms) {
    var args = Array.prototype.slice.call(arguments, 2), id = ++timerSeq;
    var run = function () {
      if (pausedAt !== null) { timers[id] = rawSetTimeout(run, 50); return; }
      delete timers[id];
      if (typeof fn === 'function') fn.apply(window, args);
    };
    timers[id] = rawSetTimeout(run, ms);
    return id;
  };
  window.clearTimeout = function (id) {
    if (id in timers) { rawClearTimeout(timers[id]); delete timers[id]; } else rawClearTimeout(id);
  };
  window.setInterval = function (fn, ms) {
    var args = Array.prototype.slice.call(arguments, 2);
    return rawSetInterval(function () { if (pausedAt === null && typeof fn === 'function') fn.apply(window, args); }, ms);
  };
  window.clearInterval = function (id) { rawClearInterval(id); };
  if (rawRaf) {
    window.requestAnimationFrame = function (cb) {
      return rawRaf(function (t) { cb(t - off - (pausedAt !== null ? t - pausedAt : 0)); });
    };
  }

  // her voice: every context the page makes is known here, and none resumes during a pause
  // (a chunk already on its way would call resume() and play)
  var ctxs = [];
  ['AudioContext', 'webkitAudioContext'].forEach(function (name) {
    var C = window[name];
    if (typeof C !== 'function') return;
    var W = function (opts) {
      var c = opts === undefined ? new C() : new C(opts);
      var resume = c.resume.bind(c);
      c.resume = function () { return pausedAt !== null ? Promise.resolve() : resume(); };
      ctxs.push(c);
      return c;
    };
    W.prototype = C.prototype;
    window[name] = W;
  });

  var taps = [];
  if (window.AudioNode && window.AudioDestinationNode) {
    var connect = AudioNode.prototype.connect;
    AudioNode.prototype.connect = function (dest) {
      var r = connect.apply(this, arguments);
      try {
        if (dest instanceof AudioDestinationNode) {
          var a = this.context.createAnalyser();
          a.fftSize = 1024;
          connect.call(this, a);
          taps.push(a);
        }
      } catch (e) { /* a node that cannot fan out: no meter for it */ }
      return r;
    };
  }
  function level() {
    var best = 0, buf = new Float32Array(1024);
    taps.forEach(function (a) {
      a.getFloatTimeDomainData(buf);
      var sum = 0;
      for (var k = 0; k < buf.length; k++) sum += buf[k] * buf[k];
      best = Math.max(best, Math.sqrt(sum / buf.length));
    });
    return best;
  }
  function soundState() {
    if (!ctxs.length) return 'none';
    return ctxs.some(function (c) { return c.state === 'running'; }) ? 'on' : 'locked';
  }
  // Chrome lets a page's sound start only after a click or a key on it: any one starts it
  // (the bar's own clicks too -- they used to stop the page from seeing the click)
  function unlock() {
    if (pausedAt === null) ctxs.forEach(function (c) { if (c.state === 'suspended') c.resume().catch(function () {}); });
  }
  window.addEventListener('pointerdown', unlock, true);
  window.addEventListener('keydown', unlock, true);

  var heldAnims = [], heldVideos = [], holdTick = 0;
  function holdAll() {
    ctxs.forEach(function (c) { if (c.state === 'running') c.suspend().catch(function () {}); });
    if (document.getAnimations) {
      // (frozen by rate, not pause(): pause()/play() on a CSS animation take it from its
      // stylesheet for good -- the backdrop's own animation-play-state stopped working after
      // the first pause; review 18 Nov)
      document.getAnimations().forEach(function (a) {
        if (a.playState === 'running' && a.playbackRate !== 0) {
          try { heldAnims.push([a, a.playbackRate]); a.updatePlaybackRate(0); } catch (e) { /* gone */ }
        }
      });
    }
    Array.prototype.forEach.call(document.querySelectorAll('video'), function (v) {
      if (!v.paused) { v.pause(); heldVideos.push(v); }
    });
  }
  function pause() {
    if (pausedAt !== null) return;
    pausedAt = rawPerf();
    holdAll();
    holdTick = rawSetInterval(holdAll, 100);   // what starts during the pause holds too
  }
  function play(keepVoice) {
    if (pausedAt === null) return;
    off += rawPerf() - pausedAt;
    pausedAt = null;
    rawClearInterval(holdTick);
    // (one cancelled meanwhile -- its slide went with a jump -- stays gone, review 18 Nov)
    heldAnims.forEach(function (h) { try { if (h[0].playState !== 'idle') h[0].updatePlaybackRate(h[1]); } catch (e) { /* gone */ } });
    heldVideos.forEach(function (v) { var p = v.play(); if (p && p.catch) p.catch(function () {}); });
    heldAnims = []; heldVideos = [];
    // (a jump: the voice queued for the old line is thrown away by the server's
    // "interrupted"; the next line's first chunk resumes the context itself)
    if (!keepVoice) ctxs.forEach(function (c) { if (c.state === 'suspended') c.resume().catch(function () {}); });
  }

  var state = null, bar, label, pauseBtn, pick, hideTimer = 0, snd, sndText, meter;
  var pressed = 0;
  function send(cmd, i) {
    pressed++;
    if (cmd === 'toggle') cmd = pausedAt !== null ? 'play' : 'pause';
    if (cmd === 'pause') pause();
    else if (cmd === 'play') play(false);
    else play(true);
    paint();
    return fetch('/preview-ctl', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: cmd, i: i }) }).then(function (r) { return r.json(); })
      .then(function (s) { state = s; paint(); }).catch(function () {});
  }
  function poll() {
    var asked = pressed;
    fetch('/preview-ctl').then(function (r) { return r.json(); })
      .then(function (s) {
        if (asked !== pressed) return;          // a press since: its own answer rules
        state = s;
        if (window.__pvHostCaption) window.__pvHostCaption(s.caption || '');
        // the server is the one that knows a jump unpaused the film
        if (!s.paused && pausedAt !== null && s.seq !== lastSeq) play(true);
        lastSeq = s.seq;
        paint();
      }).catch(function () {});
  }
  var lastSeq = null;
  function paint() {
    if (!bar) return;
    var paused = pausedAt !== null;
    pauseBtn.textContent = paused ? '▶' : '❚❚';
    pauseBtn.title = paused ? 'เล่นต่อ (Space)' : 'หยุด (Space)';
    bar.classList.toggle('pv-paused', paused);
    if (!state) return;
    label.textContent = (state.i + 1) + '/' + state.n + '  ' + (state.id || '') + (state.done ? '  · จบแล้ว' : '') +
      (paused ? '  · หยุดอยู่' : '');
    if (pick.options.length !== state.cues.length) {
      pick.innerHTML = '';
      state.cues.forEach(function (c, k) {
        var o = document.createElement('option');
        o.value = String(k);
        o.textContent = (k + 1) + '  ' + c.id + '  ' + c.en;
        pick.appendChild(o);
      });
    }
    if (document.activeElement !== pick) pick.value = String(state.i);
  }
  function show() {
    if (!bar) return;
    bar.classList.add('pv-on');
    rawClearTimeout(hideTimer);
    hideTimer = rawSetTimeout(function () { if (document.activeElement !== pick) bar.classList.remove('pv-on'); }, 2500);
  }
  function build() {
    var css = document.createElement('style');
    css.textContent =
      '#pv-bar{position:fixed;left:50%;top:14px;transform:translateX(-50%);z-index:2147483647;display:flex;gap:8px;' +
      'align-items:center;padding:8px 12px;border-radius:12px;background:rgba(6,10,24,.82);color:#e9efff;' +
      'font:15px "Segoe UI",Tahoma,sans-serif;box-shadow:0 0 0 1px rgba(120,160,255,.35);opacity:0;transition:opacity .25s}' +
      '#pv-bar.pv-on{opacity:1}#pv-bar button{all:unset;cursor:pointer;min-width:38px;height:34px;text-align:center;' +
      'border-radius:8px;background:rgba(80,120,220,.28);font-size:17px;line-height:34px}#pv-bar button:hover{background:rgba(110,150,255,.5)}' +
      '#pv-bar select{max-width:340px;background:#0b1430;color:#e9efff;border:1px solid #2a3b70;border-radius:6px;height:32px}' +
      '#pv-bar .pv-l{min-width:150px;white-space:nowrap}' +
      '#pv-bar .pv-snd{display:flex;align-items:center;gap:6px;white-space:nowrap;font-size:14px}' +
      '#pv-bar .pv-snd i{display:block;width:60px;height:8px;border-radius:4px;background:#1b2850;' +
      'background-image:linear-gradient(90deg,#4fd18b,#4fd18b);background-repeat:no-repeat;background-size:0% 100%}' +
      '#pv-bar.pv-locked{opacity:1}#pv-bar.pv-locked .pv-snd{color:#ffcf5a}';
    document.head.appendChild(css);
    bar = document.createElement('div');
    bar.id = 'pv-bar';
    function btn(text, title, cmd) {
      var b = document.createElement('button');
      b.textContent = text; b.title = title;
      b.addEventListener('click', function (e) { e.stopPropagation(); b.blur(); send(cmd); show(); });
      bar.appendChild(b);
      return b;
    }
    btn('⏮', 'บทก่อนหน้า (←)', 'prev');
    pauseBtn = btn('❚❚', 'หยุด (Space)', 'toggle');
    btn('↺', 'เล่นบทนี้ใหม่ (R)', 'replay');
    btn('⏭', 'บทถัดไป (→)', 'next');
    label = document.createElement('span'); label.className = 'pv-l'; bar.appendChild(label);
    snd = document.createElement('span'); snd.className = 'pv-snd';
    sndText = document.createElement('span'); meter = document.createElement('i');
    snd.appendChild(sndText); snd.appendChild(meter); bar.appendChild(snd);
    pick = document.createElement('select');
    pick.title = 'ข้ามไปบท';
    pick.addEventListener('change', function () { send('goto', Number(pick.value)); pick.blur(); show(); });
    bar.appendChild(pick);
    document.body.appendChild(bar);
    document.addEventListener('mousemove', show, true);
    document.addEventListener('keydown', function (e) {
      if (e.target === pick) return;
      var k = e.key, cmd = k === ' ' ? 'toggle' : k === 'ArrowLeft' ? 'prev' : k === 'ArrowRight' ? 'next'
        : (k === 'r' || k === 'R') ? 'replay' : null;
      if (!cmd) return;
      e.preventDefault(); e.stopPropagation();
      send(cmd); show();
    }, true);
    paint(); show();
    rawSetInterval(function () {
      var st = soundState(), lv = level();
      sndText.textContent = pausedAt !== null ? '🔈' : st === 'on' ? '🔊' : st === 'locked' ? '🔇 คลิกที่หน้าเพื่อเปิดเสียง' : '';
      meter.style.backgroundSize = Math.min(100, Math.round(lv * 400)) + '% 100%';
      // sound not started: the bar stays up and says so (no click = no sound in Chrome)
      bar.classList.toggle('pv-locked', st === 'locked' && pausedAt === null);
    }, 120);
    rawSetInterval(poll, 250);
    poll();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
