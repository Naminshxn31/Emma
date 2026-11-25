/* The music of card 1's walk (app/catwalk_music.py, app/face_mirror.py `catwalk`; owner 24 Nov) -- on a room screen that plays
   sound aloud, in the ceremony's show mode, nowhere else (voice-preview.html loads it at the first message).
   The server says what: walk (a loop: `track` walk | guests), duck / unduck (her voice is on), hit (the loop ends on a short
   hit), off. Everything here runs on this page's own audio clock: a Wi-Fi blink is not heard. Loaded and decoded when the
   screen connects, so a `walk` starts at once.
     CatwalkMusic.files({walk: address, guests: .., hit: ..})   preload
     CatwalkMusic.message({op, track, duck, back})              the server's word
     CatwalkMusic.stop(seconds) / .lost() / .found() / .state() */
(function () {
  'use strict';
  // under the debut's own loudness (its mix is -13.5 LUFS at 1): the loops are -14
  // DUCK: her voice is on = the music goes OFF (owner 25 Nov ~03:20, rehearsal 5: "ตอนหุ่นพูด เพลงไม่ดับ ทำไมมันแย่กว่าเดิม" --
  // since 24 Nov her voice comes from the robot only, the music from the hall's speakers: at 0.28 it still sat over her)
  var LEVEL = 0.64, DUCK = 0, RISE_UNDER_HIT_S = 2.5, START_S = 0.03, TRACKS = { walk: 1, guests: 1 };
  // another file's loudness is brought to this one's (RMS of the loops of 24 Nov), never past its own peak
  var RMS = 0.171;
  // nothing from the robot this long = it is gone: the hall is not left with a loop for ever
  var LOST_MS = 45000, LONGEST_MS = 12 * 60000, HIT_WAIT_MS = 3000;
  var sting = null, lastSting = 0, ac = null, master = null, duck = null, buf = {}, urls = {}, loading = {}, cur = null, want = null,
      lostT = null, capT = null, waitT = null;

  function context() {
    if (ac) return ac;
    try {
      ac = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000, latencyHint: 'playback' });
      master = ac.createGain();
      duck = ac.createGain();
      master.gain.value = LEVEL;
      duck.connect(master);
      master.connect(ac.destination);
      document.addEventListener('pointerdown', wake);
    } catch (e) { ac = null; }
    return ac;
  }
  function wake() {
    if (!ac || ac.state === 'running' || !ac.resume) return;
    try { var p = ac.resume(); if (p && p['catch']) p['catch'](function () {}); } catch (e) { /* a gesture is needed */ }
  }
  function evenness(b) {                                   // the gain that makes this file as loud as the first ones
    var s = 0, n = 0, top = 0;
    for (var c = 0; c < b.numberOfChannels; c++) {
      var d = b.getChannelData(c);
      for (var i = 0; i < d.length; i += 7) { var v = d[i]; s += v * v; n++; if (v < 0) v = -v; if (v > top) top = v; }
    }
    var rms = Math.sqrt(s / Math.max(1, n));
    if (!(rms > 0.001)) return 1;
    return Math.min(2, Math.max(0.3, RMS / rms), top > 0 ? 0.99 / top : 2);
  }
  function load(name) {
    var url = urls[name];
    if (!url || loading[name] === url || (buf[name] && buf[name].url === url) || !context()) return;
    loading[name] = url;
    fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.arrayBuffer();
    }).then(function (a) { return ac.decodeAudioData(a); }).then(function (b) {
      if (urls[name] !== url) return;
      buf[name] = { url: url, b: b, g: name === 'hit' ? 1 : evenness(b), beat: name === 'hit' ? null : analyse(b) };
      loading[name] = null;
      if (want && want.track === name) begin();
    })['catch'](function () {
      loading[name] = null;
      setTimeout(function () { if (urls[name] === url) load(name); }, 4000);          // (a Wi-Fi hole: again)
    });
  }
  function files(map) {
    Object.keys(map || {}).forEach(function (n) { if (typeof map[n] === 'string') urls[n] = map[n]; });
    ['walk', 'hit', 'guests'].forEach(load);
  }
  function kill(seconds) {
    if (!cur) return;
    var c = cur, t = ac.currentTime;
    cur = null;
    try {
      var now = c.g.gain.value;                            // (mid-rise: from where it is, not from the ramp's end)
      c.g.gain.cancelScheduledValues(t);
      c.g.gain.setValueAtTime(now, t);
      c.g.gain.setTargetAtTime(0, t, Math.max(0.01, seconds / 3));
      c.n.stop(t + seconds * 2 + 0.05);
    } catch (e) { /* over */ }
  }
  function voice(name, loop, rise) {
    var n = ac.createBufferSource(), g = ac.createGain(), t = ac.currentTime, to = buf[name].g;
    n.buffer = buf[name].b;
    n.loop = !!loop;
    if (rise > 0.1) { g.gain.setValueAtTime(to * 0.08, t); g.gain.exponentialRampToValueAtTime(to, t + rise); }
    else { g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(to, t + rise); }
    n.connect(g);
    g.connect(duck || master);                             // (the hit's sting too: her line right after the handshake silences it)
    n.start(t);
    return { n: n, g: g, name: name, t0: t };
  }
  function stingUp() { return !!(ac && lastSting && ac.currentTime < lastSting); }
  function low(on) { if (duck) duck.gain.setTargetAtTime(on ? DUCK : 1, ac.currentTime, 0.12); }
  // the debut (ceremony-show.js): null, or where it is -- its hit may be a moment later here than at the robot
  function debut() {
    try { var s = window.CeremonyShow && window.CeremonyShow.state(); return (s && s.reveal) || null; } catch (e) { return null; }
  }
  function begin() {
    clearTimeout(waitT);
    waitT = null;
    var w = want;
    if (!w || !context()) return;
    wake();
    if (cur && cur.name === w.track) { low(w.duck); return; }
    if (!buf[w.track]) { load(w.track); return; }                                       // (as soon as it is here)
    var d = debut();
    if (d && d.phase !== 'hit' && d.phase !== 'settle' && Date.now() - w.at < HIT_WAIT_MS) { waitT = setTimeout(begin, 40); return; }
    low(w.duck);
    kill(0.15);
    cur = voice(w.track, true, d ? RISE_UNDER_HIT_S : START_S);
  }
  function stop(seconds) {
    want = null;
    clearTimeout(waitT);
    waitT = null;
    clearTimeout(capT);
    capT = null;
    if (!ac) return;
    // (ducked = her line is on: cut, never a fade that rises over her as it un-ducks -- review 25 Nov)
    var hushed = !!duck && duck.gain.value < 0.5;
    kill(hushed ? 0.04 : (typeof seconds === 'number' ? seconds : 0.4));
    low(false);
  }
  // the hit's sting (2.6 s) is not `cur`: the red stop / call over / show mode left cut it too (review 25 Nov)
  function stingStop() {
    if (!sting || !ac) return;
    var s = sting, t = ac.currentTime;
    sting = null;
    lastSting = 0;
    try { s.g.gain.cancelScheduledValues(t); s.g.gain.setTargetAtTime(0, t, 0.03); s.n.stop(t + 0.2); } catch (e) { /* over */ }
  }
  function message(m) {
    if (!m || typeof m !== 'object') return;
    if (m.op === 'walk') {
      // (back from a blink: what plays here plays on, at the level it should have)
      want = { track: TRACKS[m.track] ? m.track : 'walk', duck: !!m.duck, at: Date.now() };
      clearTimeout(capT);
      capT = setTimeout(function () { stop(3); galleryOff(); }, LONGEST_MS);
      begin();
    } else if (m.op === 'duck' || m.op === 'unduck') {
      // (no walk on: the hit's sting may still be ringing under her line)
      var on = m.op === 'duck';
      if (want) want.duck = on;
      if (ac && (want || !on || cur || stingUp())) low(on);
    } else if (m.op === 'hit') {
      var was = !!(want || cur);
      stop(0.04);
      if (was && ac && buf.hit) { wake(); sting = voice('hit', false, 0.002); lastSting = ac.currentTime + buf.hit.b.duration; }
    } else if (m.op === 'off') {
      stop(0.4);
    }
  }
  // ---- the light show while she walks (owner 25 Nov: "ทำไมจอไม่ขึ้นตอนเดิน จอแกลรอลี่" -> "โมชั่นก็ให้เข้ากับเพลงและมีความ
  // futuristic กับ technology" -> ~03:25 "แสงก่อนก็ได้ ... ให้มันดูอะไรไม่ใช่แค่โชว์รูป อยากให้เพิ่มโมชั่น อนิเมชั่น"). Not a gallery
  // any more: a concert stage on the hall's screen, led by light on the music, the debut's own beams (ceremony-show.js RV_BEAMS)
  // in front of a 16:9 wall in the middle of it. The project's pictures are on that wall, behind a gate of light:
  //   every beat   the beams flare and fall back; the footlights along the stage's edge step out from the middle (a ripple a bar)
  //   every bar    the beams change their pose (fan, sweep left, sweep right) on a sine; the floor swells
  //   every phrase (4 bars, `barsFor`) the GATE: on its last beat the beams turn in on the wall's middle and two dark doors close
  //                over the picture, their inner edges lit; on the downbeat a seam of light blooms where they meet, the next
  //                picture is behind them, they open outward and the light rises on it; its place word is drawn in on beat 2
  // A walk's first picture is behind the closed gate as the stage comes up; it opens on the first beat. No music playing yet (or
  // none decoded): the same show on a plain clock (SHOW_MS a phrase). A `hit` lets it stay through a handshake (~20 s + its
  // line); anything that stops the music takes it down, and so does a line of the script or a slide coming up (`pictures_down`:
  // the film's stage is under it). Transform and opacity only; no blur, no blend; nothing runs while it is down.
  // WHICH LOOK the walk has on the hall's screen (owner's pick, 25 Nov): 'gate' (Light Gate: the wall, the doors), 'film' (the
  // pictures edge to edge, light on the music) or 'lights' (no pictures: the ceremony's stage, the debut's beams, the logo)
  var WALK_LOOK = 'film';                                // (owner 25 Nov ~05:00, after the three previews: "เอา 2")
  var SHOW_MS = 7600, FADE_MS = 1200, LINGER_MS = 45000;
  var gal = { urls: [], box: null, layers: [], k: 0, on: false, first: false, waiting: null, timer: null, linger: null,
              ph: [[], []], p: 0, log: [] };

  // ---- the beat, from the decoded loop
  var FALLBACK_BPM = 126, LEAD_S = 0.8, BEAT_FADE_MS = 700, MIN_LOOP_S = 4;
  function analyse(b) {
    var out = { bpm: FALLBACK_BPM, off: 0, sure: false };
    try {
      var sr = b.sampleRate, hop = 256, len = Math.min(b.length, Math.floor(sr * 90)), n = Math.floor(len / hop);
      if (n < 400) return out;
      var ch = [];
      for (var c = 0; c < Math.min(2, b.numberOfChannels); c++) ch.push(b.getChannelData(c));
      var full = new Float32Array(n), low = new Float32Array(n), lp = 0;
      for (var i = 0; i < n; i++) {
        var sf = 0, sl = 0, base = i * hop;
        for (var j = 0; j < hop; j++) {
          var v = 0;
          for (var q = 0; q < ch.length; q++) v += ch[q][base + j];
          v /= ch.length;
          lp += 0.05 * (v - lp);
          sf += v * v; sl += lp * lp;
        }
        full[i] = Math.sqrt(sf / hop); low[i] = Math.sqrt(sl / hop);
      }
      var o = new Float32Array(n), mf = 1e-9, ml = 1e-9, d, mean = 0;
      for (i = 0; i < n; i++) mean += full[i];
      mean = mean / n + 1e-9;
      for (i = 1; i < n; i++) {
        d = full[i] - full[i - 1]; full[i - 1] = d > 0 ? d : 0; if (full[i - 1] > mf) mf = full[i - 1];
        d = low[i] - low[i - 1]; low[i - 1] = d > 0 ? d : 0; if (low[i - 1] > ml) ml = low[i - 1];
      }
      if (mf / mean < 0.15) return out;                                              // (a steady sound: no beat in it)
      for (i = 0; i < n - 1; i++) o[i] = full[i] / mf + low[i] / ml;
      var omax = 1e-9, hits = 0;
      for (i = 0; i < n; i++) if (o[i] > omax) omax = o[i];
      for (i = 0; i < n; i++) if (o[i] > 0.4 * omax && (i === 0 || o[i - 1] <= 0.4 * omax)) hits++;
      if (hits < 12) return out;                                                     // (no onsets to speak of: not music with a beat)
      var fr = sr / hop, lo = Math.floor(fr * 60 / 180), hi = Math.ceil(fr * 60 / 70), maxL = Math.min(hi * 4 + 2, Math.floor(n / 2));
      var r = new Float32Array(maxL + 1);
      for (var L = lo; L <= maxL; L++) {
        var s = 0;
        for (i = 0; i + L < n; i++) s += o[i] * o[i + L];
        r[L] = s / (n - L);
      }
      var sc = new Float32Array(hi + 2), best = -1, bl = 0, tot = 0, cnt = 0;
      for (L = lo; L <= hi; L++) {
        sc[L] = r[L] + 0.5 * (r[2 * L] || 0) + 0.25 * (r[4 * L] || 0);
        tot += sc[L]; cnt++;
        if (sc[L] > best) { best = sc[L]; bl = L; }
      }
      if (!(best > 0) || bl <= lo || bl >= hi) return out;
      var a0 = sc[bl - 1], a1 = sc[bl], a2 = sc[bl + 1], den = a0 - 2 * a1 + a2;
      var lag = bl + (den < 0 ? 0.5 * (a0 - a2) / den : 0);
      var bpm = 60 * fr / lag;
      out.sure = best > 1.8 * (tot / cnt);
      if (!out.sure) return out;
      var dur = b.duration, nb = Math.round(dur * bpm / 60), b2 = nb * 60 / dur;    // (a loop is a whole number of beats)
      if (nb >= 4 && Math.abs(b2 - bpm) < 1) bpm = b2;
      var beat = 60 / bpm, first = 0, top = 0, lim = Math.min(n - 1, Math.floor(fr * 8));
      for (i = 0; i < lim; i++) if (o[i] > top) top = o[i];
      for (i = 0; i < lim; i++) if (o[i] >= 0.5 * top) { first = (i + 0.5) * hop / sr; break; }
      var phase = 0, bs = -1;                                                         // (the beat grid that holds the most onsets)
      for (var p = 0; p < 48; p++) {
        var ph = beat * p / 48, ss = 0;
        for (var t = ph; t < Math.min(dur, 60); t += beat) { var ix = Math.round(t * fr); if (ix < n) ss += o[ix]; }
        if (ss > bs) { bs = ss; phase = ph; }
      }
      var off = phase + Math.round((first - phase) / beat) * beat;
      if (off < 0) off += beat;
      out.bpm = bpm; out.off = off;
    } catch (e) { /* the fallback */ }
    return out;
  }
  // where in the loop the music is now, on the audio clock (what is heard: the output's own delay taken off)
  var fakeGrid = null;
  function grid() {
    if (fakeGrid) return fakeGrid();
    if (!ac || !cur || !cur.t0 || ac.state !== 'running' || !buf[cur.name] || !buf[cur.name].beat) return null;
    var bb = buf[cur.name], dur = bb.b.duration;
    if (dur < MIN_LOOP_S) return null;
    var pos = ac.currentTime - (ac.outputLatency || ac.baseLatency || 0) - cur.t0;
    if (!(pos < 0 && pos > -1)) pos = ((pos % dur) + dur) % dur;       // (a hair before its start: the start)
    return { pos: pos, dur: dur, bpm: bb.beat.bpm, off: bb.beat.off, beat: 60 / bb.beat.bpm };
  }
  // seconds from now to the first bar line at least `after` seconds ahead (the loop's end wraps to its first line)
  function nextLine(g, after) {
    var bar = g.beat * 4, i = Math.ceil((g.pos + after - g.off) / bar - 1e-9);
    if (i < 0) i = 0;
    var t = g.off + i * bar;
    if (t >= g.dur - 0.02) {                               // (past the loop's end: the lines of the next lap)
      var tw = g.dur + g.off;
      t = tw + Math.max(0, Math.ceil((g.pos + after - tw) / bar - 1e-9)) * bar;
    }
    return t - g.pos;
  }
  function barsFor(g) {                                    // every 4 bars; a fast track that makes that under 6 s waits 8
    var bar = g.beat * 4, n = 4;
    if (n * bar < 6) n = 8;
    else if (n * bar > 11) n = 2;
    return n;
  }

  // ---- the stage
  var BLUE = 'rgba(77,163,255,', DEEP = 'rgba(47,107,255,', CYAN = 'rgba(110,231,255,';
  // the wall the pictures are on: centred, 16:9, from 5vh to 75vh (below it the stage's floor; the lowest fifth holds only light)
  var WALL_TOP = 5, WALL_H = 70;
  // the beams (the debut's): [where along the floor (% of the width), from the side (1), their fan, sweep left and sweep right
  // (deg), a white core (1)]; the fourth pose, turned in on the wall's middle for the gate, is worked out from the screen's shape
  var BEAMS = [[6, 0, -26, -34, -12, 0], [20, 0, -16, -28, -3, 1], [36, 0, -7, -20, 7, 0],
               [64, 0, 7, -7, 20, 0], [80, 0, 16, 3, 28, 1], [94, 0, 26, 12, 34, 0],
               [0, 1, 36, 26, 48, 1], [100, 1, -36, -48, -26, 1]];
  var LAMPS = 8;                                           // (footlights a side: out from the middle, two a beat, a ripple a bar)
  function part(cls, parent, tag) {
    var e = document.createElement(tag || 'div');
    e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }
  function galBox() {
    if (gal.box) return gal.box;
    return WALK_LOOK === 'film' ? filmBox() : WALK_LOOK === 'lights' ? lightsBox() : gateBox();
  }
  function gateBox() {
    if (gal.box) return gal.box;
    var b = document.createElement('div');
    b.className = 'cw-gallery';
    b.style.cssText = 'position:fixed;inset:0;z-index:6;background:#000;opacity:0;transition:opacity ' + FADE_MS + 'ms ease;pointer-events:none;overflow:hidden;cursor:none;';
    var WALL = 'left:50%;top:' + WALL_TOP + 'vh;width:' + (WALL_H * 16 / 9).toFixed(2) + 'vh;height:' + WALL_H + 'vh;margin-left:-' + (WALL_H * 8 / 9).toFixed(2) + 'vh;';
    var st = document.createElement('style');
    // (every glow a drawn gradient on a layer that moves only by transform / opacity; a beam's wedge is a still child's clip-path)
    st.textContent = '@font-face{font-family:"CwKantalad";font-weight:500;font-display:block;src:url("/assets/ceremony-show/fonts/Kantalad-Medium.otf") format("opentype");}' +
      '.cw-gallery div,.cw-gallery i,.cw-gallery span,.cw-gallery img{position:absolute;display:block;}' +
      '.cw-gallery .cw-back{left:50%;top:-14vh;width:180vh;height:112vh;margin-left:-90vh;opacity:.7;' +
      'background:radial-gradient(ellipse closest-side,' + BLUE + '.32),' + DEEP + '.12) 55%,' + DEEP + '0) 100%);}' +
      '.cw-gallery .cw-haze{left:-20%;width:140%;bottom:-10vh;height:62vh;' +
      'background:radial-gradient(ellipse 46% 42% at 40% 70%,rgba(160,200,255,.15),' + BLUE + '.06) 55%,' + BLUE + '0) 80%);}' +
      '.cw-gallery .cw-wall{' + WALL + 'overflow:hidden;background:#02050b;}' +
      '.cw-gallery .cw-slide{left:0;top:0;width:100%;height:100%;opacity:0;overflow:hidden;}' +
      '.cw-gallery .cw-pic{left:0;top:0;width:100%;height:100%;object-fit:cover;transform-origin:50% 50%;}' +
      '.cw-gallery .cw-shade{left:0;top:0;width:100%;height:100%;background:linear-gradient(to top,rgba(2,6,16,.55) 0,rgba(2,6,16,0) 38%);}' +
      '.cw-gallery .cw-label{left:3.4vh;bottom:3.4vh;overflow:hidden;opacity:0;border-left:.4vh solid #4da3ff;' +
      'font:500 3.6vh/1.1 "CwKantalad","Inter","Segoe UI",system-ui,sans-serif;letter-spacing:.04em;color:#f4f8ff;text-shadow:0 .2vh 1.4vh rgba(0,0,0,.65);white-space:nowrap;}' +
      '.cw-gallery .cw-txt{position:relative;padding:0 1.4vh 0 1.5vh;transform:translate3d(-101%,0,0);}' +
      '.cw-gallery .cw-edge{top:0;bottom:0;right:0;width:.3vh;background:#fff;opacity:0;}' +
      // the gate: two dark doors (an LED wall's mullions on them), their inner edges lit
      '.cw-gallery .cw-door{top:0;height:100%;width:50.2%;' +
      'background:repeating-linear-gradient(90deg,' + BLUE + '.07) 0,' + BLUE + '.07) .14vh,transparent .14vh,transparent 2.4vh),linear-gradient(180deg,#060d1b,#02050b 58%,#050b18);}' +
      '.cw-gallery .cw-dl{left:0;}.cw-gallery .cw-dr{right:0;}' +
      '.cw-gallery .cw-dglow{top:0;height:100%;width:10vh;}' +
      '.cw-gallery .cw-dl .cw-dglow{right:0;background:linear-gradient(90deg,' + BLUE + '0),' + BLUE + '.3));}' +
      '.cw-gallery .cw-dr .cw-dglow{left:0;background:linear-gradient(270deg,' + BLUE + '0),' + BLUE + '.3));}' +
      '.cw-gallery .cw-dline{top:0;height:100%;width:.35vh;background:linear-gradient(180deg,' + BLUE + '.2),rgba(215,236,255,.9) 30%,#fff 50%,rgba(215,236,255,.9) 70%,' + BLUE + '.2));}' +
      '.cw-gallery .cw-dl .cw-dline{right:0;}.cw-gallery .cw-dr .cw-dline{left:0;}' +
      '.cw-gallery .cw-rim{' + WALL + 'border:.14vh solid ' + BLUE + '.3);margin-top:-.14vh;margin-left:-' + (WALL_H * 8 / 9 + 0.14).toFixed(2) + 'vh;}' +
      '.cw-gallery .cw-br{width:4.2vh;height:4.2vh;border:0 solid ' + BLUE + '.9);}' +
      // the seam where the doors meet: a line, and the light it blooms into
      '.cw-gallery .cw-seam{left:50%;top:' + WALL_TOP + 'vh;width:0;height:' + WALL_H + 'vh;}' +
      '.cw-gallery .cw-sglow{left:-36vh;top:-9vh;width:72vh;height:' + (WALL_H + 18) + 'vh;opacity:0;' +
      'background:radial-gradient(ellipse closest-side,rgba(205,228,255,.5),' + BLUE + '.24) 38%,' + DEEP + '0) 100%);}' +
      '.cw-gallery .cw-score{left:-.3vh;top:0;width:.6vh;height:100%;opacity:0;transform:scaleY(.12);' +
      'background:linear-gradient(180deg,' + BLUE + '0),rgba(225,240,255,.9) 22%,#fff 50%,rgba(225,240,255,.9) 78%,' + BLUE + '0));}' +
      // the beams, in front: a wide wedge and a core from a foot on the floor
      '.cw-gallery .cw-beams{left:0;top:0;width:100%;height:100%;}' +
      '.cw-gallery .cw-beam{bottom:-3vh;width:22vh;height:80vh;margin-left:-11vh;transform-origin:50% 100%;opacity:.55;}' +
      '.cw-gallery .cw-beam.cw-side{bottom:12vh;height:88vh;}' +
      '.cw-gallery .cw-bw{left:0;top:0;width:100%;height:100%;clip-path:polygon(46% 100%,54% 100%,100% 0,0 0);opacity:.3;' +
      'background:linear-gradient(0deg,' + CYAN + '.7) 0%,' + BLUE + '.3) 22%,' + BLUE + '.08) 62%,' + BLUE + '0) 96%);}' +
      '.cw-gallery .cw-bc{left:0;top:0;width:100%;height:100%;clip-path:polygon(49% 100%,51% 100%,66% 0,34% 0);opacity:.62;' +
      'background:linear-gradient(0deg,rgba(210,235,255,.95) 0%,' + BLUE + '.5) 18%,' + BLUE + '.14) 60%,' + BLUE + '0) 96%);}' +
      '.cw-gallery .cw-white .cw-bc{background:linear-gradient(0deg,#fff 0%,rgba(235,245,255,.7) 16%,rgba(200,225,255,.18) 60%,rgba(200,225,255,0) 96%);}' +
      // the stage's floor, its edge, and the footlights along it
      '.cw-gallery .cw-floor{left:0;width:100%;bottom:0;height:26vh;opacity:.5;background:linear-gradient(0deg,' + DEEP + '.42),' + DEEP + '.12) 45%,' + DEEP + '0));}' +
      '.cw-gallery .cw-stageline{left:8%;width:84%;top:' + (WALL_TOP + WALL_H + 2.2) + 'vh;height:.16vh;' +
      'background:linear-gradient(90deg,' + BLUE + '0),' + BLUE + '.5) 18%,rgba(200,228,255,.7) 50%,' + BLUE + '.5) 82%,' + BLUE + '0));}' +
      '.cw-gallery .cw-lamp{top:' + (WALL_TOP + WALL_H + 3.6) + 'vh;width:5vh;height:5vh;margin:-2.5vh 0 0 -2.5vh;opacity:.14;' +
      'background:radial-gradient(circle closest-side,#fff 0,rgba(200,228,255,.9) 14%,' + BLUE + '.32) 42%,' + BLUE + '0) 100%);}';
    b.appendChild(st);
    var parts = gal.parts = {};
    parts.back = part('cw-back', b);
    parts.haze = part('cw-haze', b);
    var wall = part('cw-wall', b);
    for (var i = 0; i < 2; i++) {
      var sl = part('cw-slide', wall), im = part('cw-pic', sl, 'img');
      im.alt = '';
      im.decoding = 'async';
      part('cw-shade', sl);
      var lb = part('cw-label', sl), tx = part('cw-txt', lb, 'span');
      tx.appendChild(document.createTextNode(''));
      gal.layers.push({ el: sl, img: im, label: lb, txt: tx, edge: part('cw-edge', tx, 'i'), anims: [] });
    }
    parts.doors = [part('cw-door cw-dl', wall), part('cw-door cw-dr', wall)];
    parts.doors.forEach(function (d) { part('cw-dglow', d); part('cw-dline', d); if (calm) d.style.display = 'none'; });
    var rim = part('cw-rim', b);
    ['top:-.6vh;left:-.6vh;border-width:.28vh 0 0 .28vh', 'top:-.6vh;right:-.6vh;border-width:.28vh .28vh 0 0',
     'bottom:-.6vh;left:-.6vh;border-width:0 0 .28vh .28vh', 'bottom:-.6vh;right:-.6vh;border-width:0 .28vh .28vh 0'].forEach(function (css) {
      part('cw-br', rim).style.cssText = css;
    });
    var seam = part('cw-seam', b);
    parts.glow = part('cw-sglow', seam);
    parts.core = part('cw-score', seam);
    parts.beams = part('cw-beams', b);
    gal.beamEls = BEAMS.map(function (d) {
      var e = part('cw-beam' + (d[1] ? ' cw-side' : '') + (d[5] ? ' cw-white' : ''), parts.beams);
      e.style.left = d[0] + '%';
      e.style.transform = 'rotate(' + d[2] + 'deg)';
      part('cw-bw', e, 'i');
      part('cw-bc', e, 'i');
      return e;
    });
    parts.floor = part('cw-floor', b);
    part('cw-stageline', b);
    gal.lampEls = [];
    for (var q = 0; q < LAMPS; q++) {
      [-1, 1].forEach(function (side) {
        var lamp = part('cw-lamp', b, 'i');
        lamp.style.left = (50 + side * (q + 0.5) * 5.2).toFixed(1) + '%';
        gal.lampEls.push({ el: lamp, q: q });
      });
    }
    document.body.appendChild(b);
    gal.box = b;
    return b;
  }
  // The camera on a picture: four slow moves in turn (push in, drift right, pull out, drift left), a sine ease, its whole stay.
  // Transform only; the picture is always larger than the wall so no edge shows; the wall's frame never flickers.
  var MOVES = [
    ['scale(1.03) translate3d(0,0,0)', 'scale(1.10) translate3d(0,0,0)'],
    ['scale(1.08) translate3d(-1.8%,0,0)', 'scale(1.08) translate3d(1.8%,0,0)'],
    ['scale(1.10) translate3d(0,0,0)', 'scale(1.03) translate3d(0,0,0)'],
    ['scale(1.08) translate3d(1.8%,0,0)', 'scale(1.08) translate3d(-1.8%,0,0)']
  ];
  var EASE = 'cubic-bezier(.37,0,.63,1)', OUT = 'cubic-bezier(.2,0,0,1)', IN = 'cubic-bezier(.3,0,1,1)',
      OPEN = 'cubic-bezier(.05,.7,.1,1)', HIT = 'cubic-bezier(.1,.6,.3,1)', SWELL = 'cubic-bezier(.2,.6,.35,1)';
  var POSES = ['F', 'L', 'R'];
  var calm = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  function can(el) { return !calm && el && typeof el.animate === 'function'; }
  function cancelAll(list) { while (list.length) { var a = list.pop(); try { a.cancel(); } catch (e) { /* gone */ } } }
  // pts = [[ms from now, {css}, easing to the next?], ...]: one animation from the first point to the last (a point already past
  // = into it by as much); `fill` forwards by default, so a phrase's last pose holds until the next phrase's first
  function at(list, el, pts, fill) {
    if (!can(el) || !pts.length) return null;
    for (var m = 1; m < pts.length; m++) if (pts[m][0] < pts[m - 1][0]) pts[m][0] = pts[m - 1][0];
    var t0 = pts[0][0], span = Math.max(1, pts[pts.length - 1][0] - t0);
    var kf = pts.map(function (p) {
      var f = {};
      for (var key in p[1]) f[key] = p[1][key];
      f.offset = Math.min(1, (p[0] - t0) / span);
      if (p[2]) f.easing = p[2];
      return f;
    });
    var a = el.animate(kf, { duration: span, delay: t0, easing: 'linear', fill: fill || 'forwards' });
    list.push(a);
    return a;
  }
  // a pulse `count` times, `period` ms apart, the first at `first` ms from now (it ends by itself: no fill)
  function every(list, el, kf, period, first, count) {
    if (!can(el) || !(count >= 1) || !(period > 0)) return null;
    var a = el.animate(kf, { duration: period, delay: first, iterations: count, easing: 'linear', fill: 'none' });
    list.push(a);
    return a;
  }
  function closing(B) { return Math.min(800, Math.max(300, 1.5 * B)); }  // the doors' close: the phrase's last beat and a half
  function opening(B) { return Math.min(1100, Math.max(600, 1.75 * B)); }
  function rot(a) { return 'rotate(' + a + 'deg)'; }
  function poses() {                                       // per beam: F fan, L sweep left, R sweep right, C turned in on the wall
    var W = window.innerWidth || 1920, H = window.innerHeight || 1080, mid = (WALL_TOP + WALL_H / 2) / 100 * H;
    return BEAMS.map(function (d) {
      var foot = d[1] ? 0.88 * H : 1.03 * H, c = Math.atan2((0.5 - d[0] / 100) * W, foot - mid) * 180 / Math.PI;
      return { F: d[2], L: d[3], R: d[4], C: Math.round(c * 10) / 10 };
    });
  }
  // One phrase of light, everything in ms from now: P.gate (the doors open: a downbeat, or a walk's first beat), P.end (the next
  // gate: a bar line), P.B (a beat), P.bar, P.beat0 (its first beat), P.close (the doors close on its last beat first).
  function lights(P, list) {
    var parts = gal.parts, g = P.gate, end = P.end, bar = P.bar, cl = closing(P.B), op = opening(P.B);
    var OPENED = ['translate3d(-100%,0,0)', 'translate3d(100%,0,0)'], SHUT = 'translate3d(0,0,0)';
    parts.doors.forEach(function (d, j) {
      // (shut a moment round the downbeat, so the eye has the gate before it opens)
      if (P.close) at(list, d, [[g - cl, { transform: OPENED[j] }, IN], [g - 60, { transform: SHUT }], [g + 90, { transform: SHUT }, OPEN], [g + 90 + op, { transform: OPENED[j] }]]);
      else at(list, d, [[g + 90, { transform: SHUT }, OPEN], [g + 90 + op, { transform: OPENED[j] }]]);
    });
    // the seam: a line drawn down the middle as the doors meet; on the downbeat it blooms and the light opens out with them
    at(list, parts.core, [[P.close ? g - cl : 0, { opacity: 0, transform: 'scaleY(.12)' }, OUT], [g - 60, { opacity: .85, transform: 'scaleY(1)' }],
                          [g + 90, { opacity: 1, transform: 'scaleY(1)' }, EASE], [g + 90 + 1.4 * P.B, { opacity: 0, transform: 'scaleY(1)' }]]);
    at(list, parts.glow, [[g - 120, { opacity: 0, transform: 'scale(.55,.8)' }, OUT], [g + 30, { opacity: .9, transform: 'scale(.8,1)' }, OUT],
                          [g + 30 + 2.6 * P.B, { opacity: 0, transform: 'scale(1.7,1)' }]]);
    // the beams: turned in on the gate; a new pose on every bar line; turned in again for the next gate
    var lines = [], t;
    for (t = end - bar; t > g + bar * 0.5; t -= bar) lines.unshift(t);
    if (!lines.length) lines.push((g + end) / 2);
    var pz = poses();
    gal.beamEls.forEach(function (e, j) {
      var p = pz[j], pts = [[g, { transform: rot(p.C) }, EASE]];
      lines.forEach(function (tl, n) { pts.push([tl, { transform: rot(p[POSES[n % 3]]) }, EASE]); });
      pts.push([end, { transform: rot(p.C) }]);
      if (!P.close) e.style.transform = rot(p.C);
      at(list, e, pts);
    });
    // every beat: the beams flare and fall back, each beam itself -- an opacity on their group is an offscreen pass of the whole
    // screen every frame (measured: 57 -> 21 fps); four spare: a late timer never leaves the light still before the next phrase's
    var beats = Math.max(1, Math.round((end - P.beat0) / P.B)) + 4;
    gal.beamEls.forEach(function (e) { every(list, e, [{ opacity: 1, easing: HIT }, { opacity: .55 }], P.B, P.beat0, beats); });
    // every bar: the floor swells on the downbeat; the footlights step out from the middle. (The light behind the wall and the
    // haze stay still: painted once with the stage -- each animated, they were a screen and a half of pixels a frame.)
    var bar0 = end - bar * Math.floor((end - P.beat0) / bar + 1e-6), bars = Math.max(1, Math.round((end - bar0) / bar)) + 1;
    every(list, parts.floor, [{ opacity: 1, easing: SWELL }, { opacity: .45 }], bar, bar0, bars);
    gal.lampEls.forEach(function (L) {
      every(list, L.el, [{ opacity: 1, easing: SWELL }, { opacity: 0.14, offset: 0.3 }, { opacity: 0.14 }], bar, bar0 - bar + (L.q >> 1) * P.B, bars + 1);
    });
  }
  // One picture on the wall: on the gate, behind the shut doors (dim), the light rising on it as they open; the camera's slow
  // move its whole stay; its word drawn in on beat 2. `dt` = ms since the phrase was planned (the picture may come late).
  function perform(s, old, k, P, dt, first) {
    cancelAll(s.anims);
    var g = P.gate - dt, end = P.end - dt, mv = MOVES[k % MOVES.length], op = opening(P.B);
    if (!can(s.el)) {
      s.el.style.opacity = '1';
      old.el.style.opacity = '0';
      s.img.style.transform = mv[0];
      s.label.style.opacity = s.txt.firstChild.nodeValue ? '1' : '0';
      s.txt.style.transform = 'none';
      return;
    }
    if (first || g >= 0) {
      var on = first ? 0 : g;
      at(s.anims, s.el, [[on - 1, { opacity: 0 }], [on, { opacity: 0.5 }], [g + 90, { opacity: 0.5 }, OUT], [g + 90 + op, { opacity: 1 }]], 'both');
      if (!first) at(old.anims, old.el, [[g, { opacity: 1 }], [g + 1, { opacity: 0 }]]);
    } else {                                               // (late: the gate went by without it -- a plain cross-fade now)
      at(s.anims, s.el, [[0, { opacity: 0 }, EASE], [450, { opacity: 1 }]], 'both');
      at(old.anims, old.el, [[0, { opacity: 1 }, EASE], [450, { opacity: 0 }]]);
    }
    at(s.anims, s.img, [[first ? 0 : g - closing(P.B), { transform: mv[0] }, EASE], [end + 600, { transform: mv[1] }]], 'both');
    if (!s.txt.firstChild.nodeValue) return;
    var L = g + P.B, E = Math.max(L + 1400, end - 0.6 * P.B);
    at(s.anims, s.label, [[L, { opacity: 0 }], [L + 120, { opacity: 1 }], [E - 400, { opacity: 1 }], [E, { opacity: 0 }]], 'both');
    at(s.anims, s.txt, [[L, { transform: 'translate3d(-101%,0,0)' }, OUT], [L + 650, { transform: 'translate3d(0,0,0)' }]], 'both');
    at(s.anims, s.edge, [[L, { opacity: 0 }], [L + 60, { opacity: 1 }], [L + 650, { opacity: 1 }], [L + 950, { opacity: 0 }]], 'both');
  }
  // ---- the planning of a phrase, the same for every look: P.gate (its start: a downbeat, or a walk's first beat), P.end (the
  // next phrase's start: a bar line), P.B (a beat), P.bar, P.beat0 (its first beat), P.close (not a walk's first), P.wait (the
  // next plan's timer). No music to follow: a plain clock, a phrase SHOW_MS.
  function plan(first) {
    var g = grid(), P = { close: !first }, w0 = 0, bars = 4;
    if (g) {
      bars = barsFor(g);
      P.B = g.beat * 1000;
      if (first) {
        // (a walk's first phrase starts on the music's next beat; it runs to the bar line nearest its span, so every phrase
        // after it starts on a line)
        P.end = nextLine(g, Math.max(LEAD_S + 0.2, bars * g.beat * 4 - g.beat * 2)) * 1000;
        P.beat0 = (((g.off - g.pos) % g.beat) + g.beat) % g.beat * 1000;
        P.gate = P.beat0 < 350 ? P.beat0 + P.B : P.beat0;
      } else {
        w0 = nextLine(g, LEAD_S) * 1000;
        P.gate = P.beat0 = w0;
        P.end = w0 + bars * g.beat * 4000;
      }
      gal.log.push({ planned: g.pos, cut: g.pos + w0 / 1000, gate: g.pos + P.gate / 1000, until: g.pos + P.end / 1000, bars: bars, first: first });
      if (gal.log.length > 50) gal.log.shift();
    } else {
      P.B = SHOW_MS / 16;
      P.gate = P.beat0 = first ? P.B : closing(P.B);
      P.end = P.gate + SHOW_MS;
    }
    P.bar = P.B * 4;
    P.wait = g ? Math.max(200, P.end - LEAD_S * 1000 - 100) : SHOW_MS;
    return P;
  }
  function galNext() {
    if (!gal.on || !gal.urls.length) return;
    var k = gal.k, first = gal.first;
    gal.k += 1;
    gal.first = false;
    var P = plan(first), list = gal.ph[gal.p % 2];
    cancelAll(list);                                       // (the phrase before last: long over)
    gal.p += 1;
    gal.timer = setTimeout(galNext, P.wait);
    if (WALK_LOOK === 'lights') { lightsPhrase(P, list, k, first); return; }
    var item = gal.urls[k % gal.urls.length], s = gal.layers[k % 2], old = gal.layers[(k + 1) % 2], film = WALK_LOOK === 'film';
    (film ? filmLights : lights)(P, list, k, first);
    var t0 = performance.now(), shown = false;
    function show() {
      if (shown || !gal.on) return;
      shown = true;
      (film ? filmPerform : perform)(s, old, k, P, performance.now() - t0, first);
    }
    cancelAll(s.anims);
    s.txt.firstChild.nodeValue = item.label || '';
    s.img.onload = show;
    s.img.onerror = function () { shown = true; };     // (a picture that does not come: the one before stays, the light goes on)
    s.img.src = item.src;
    if (s.img.complete && s.img.naturalWidth) show();
    var ahead = new Image();
    ahead.src = gal.urls[gal.k % gal.urls.length].src;   // (the next one decoded before its turn)
  }
  // the walk layer itself, the same for every look: fixed, opaque, over the film's stage, no pointer
  function shell(look) {
    var b = document.createElement('div');
    b.className = 'cw-gallery cw-' + look;
    b.style.cssText = 'position:fixed;inset:0;z-index:6;background:#000;opacity:0;transition:opacity ' + FADE_MS + 'ms ease;pointer-events:none;overflow:hidden;cursor:none;';
    return b;
  }
  var BASE_CSS = '@font-face{font-family:"CwKantalad";font-weight:500;font-display:block;src:url("/assets/ceremony-show/fonts/Kantalad-Medium.otf") format("opentype");}' +
    '.cw-gallery div,.cw-gallery i,.cw-gallery span,.cw-gallery img,.cw-gallery video{position:absolute;display:block;}';

  // ==== A. FILM: the ceremony film going on -- the project's pictures edge to edge, a slow camera on each, light on the music:
  //   every beat    a soft light leak breathes in a top corner (left on one beat, right on the next); the tempo mark steps on
  //   every bar     a band of light sweeps across the picture on the bar line (left to right, then back)
  //   every phrase  (4 bars) the CUT: a wide sweep crosses the middle on the downbeat, the next picture dissolves in under it,
  //                 an anamorphic streak flares across; the place word is written in on beat 2, low left, above the subtitles
  // No frame, no wall, no doors. Transform and opacity only; the sweep's slant and the streak's light are on still children.
  var FILM_MOVES = [
    ['scale(1.04) translate3d(0,0,0)', 'scale(1.13) translate3d(0,-1%,0)'],      // push in
    ['scale(1.10) translate3d(-2.2%,0,0)', 'scale(1.10) translate3d(2.2%,0,0)'],  // drift right
    ['scale(1.13) translate3d(0,1%,0)', 'scale(1.04) translate3d(0,0,0)'],        // pull out
    ['scale(1.10) translate3d(2.2%,0,0)', 'scale(1.10) translate3d(-2.2%,0,0)']   // drift left
  ];
  var TICKS = 16;                                          // (the tempo mark: a phrase of 4 bars, a step a beat)
  function filmBox() {
    var b = shell('film');
    var st = document.createElement('style');
    st.textContent = BASE_CSS +
      '.cw-film .cw-fs{left:0;top:0;width:100%;height:100%;opacity:0;}' +
      // (the two pictures take z 1 / 2 in turn; everything of the light is over both)
      '.cw-film .cw-grade,.cw-film .cw-leak,.cw-film .cw-sweep,.cw-film .cw-streak,.cw-film .cw-corner,.cw-film .cw-ruler{z-index:3;}' +
      '.cw-film .cw-fp{left:0;top:0;width:100%;height:100%;object-fit:cover;transform-origin:50% 50%;}' +
      // the grade, painted once over the pictures: the sky cooled to the film's navy, the edges and the subtitle zone darker
      '.cw-film .cw-grade{left:0;top:0;width:100%;height:100%;background:' +
      'linear-gradient(0deg,rgba(1,3,14,.74) 0,rgba(1,3,14,.4) 15%,rgba(1,3,14,0) 34%),' +
      'linear-gradient(180deg,rgba(4,12,48,.5) 0,rgba(4,12,48,.12) 16%,rgba(4,12,48,0) 30%),' +
      'radial-gradient(ellipse 72% 70% at 50% 46%,rgba(0,3,14,0) 58%,rgba(0,3,14,.55) 100%);}' +
      // the leaks: two corners of light, top left and top right
      '.cw-film .cw-leak{top:-8vh;width:48vw;height:62vh;opacity:.12;}' +
      '.cw-film .cw-lk-l{left:-8vw;background:radial-gradient(ellipse 50% 50% at 30% 28%,' + BLUE + '.55),' + DEEP + '.2) 45%,' + DEEP + '0) 100%);}' +
      '.cw-film .cw-lk-r{right:-8vw;background:radial-gradient(ellipse 50% 50% at 70% 28%,' + CYAN + '.42),' + BLUE + '.16) 45%,' + BLUE + '0) 100%);}' +
      // the sweep: a slanted band of light crossing the screen (the slant on its still child)
      '.cw-film .cw-sweep{left:0;top:-15vh;width:46vh;height:130vh;}' +
      '.cw-film .cw-sweep i{left:0;top:0;width:100%;height:100%;opacity:0;transform:skewX(-16deg);' +
      'background:linear-gradient(90deg,' + BLUE + '0) 0%,' + BLUE + '.08) 30%,rgba(200,228,255,.24) 47%,rgba(240,248,255,.36) 50%,rgba(200,228,255,.24) 53%,' + BLUE + '.08) 70%,' + BLUE + '0) 100%);}' +
      '.cw-film .cw-sweep.cw-big{width:72vh;}' +
      '.cw-film .cw-sweep.cw-big i{background:linear-gradient(90deg,' + BLUE + '0) 0%,' + BLUE + '.12) 26%,rgba(190,222,255,.34) 45%,rgba(240,248,255,.5) 50%,rgba(190,222,255,.34) 55%,' + BLUE + '.12) 74%,' + BLUE + '0) 100%);}' +
      // the streak: a thin line of light across the screen and the soft light round it
      '.cw-film .cw-streak{left:-10%;width:120%;height:14vh;margin-top:-7vh;opacity:0;transform:scaleX(.2);}' +
      '.cw-film .cw-streak i{left:0;width:100%;}' +
      '.cw-film .cw-streak .cw-sh{top:0;height:100%;background:radial-gradient(ellipse 50% 50% at 50% 50%,' + BLUE + '.3),' + DEEP + '.1) 50%,' + DEEP + '0) 100%);}' +
      '.cw-film .cw-streak .cw-sl{top:50%;height:.4vh;margin-top:-.2vh;background:linear-gradient(90deg,' + BLUE + '0),' + BLUE + '.5) 25%,rgba(225,240,255,.95) 50%,' + BLUE + '.5) 75%,' + BLUE + '0));}' +
      // the HUD the film's acts wear: thin corners at the top, and the tempo mark -- a ruler of ticks, a light stepping on it
      '.cw-film .cw-corner{top:3.2vh;width:3.6vh;height:3.6vh;border:0 solid ' + BLUE + '.75);}' +
      '.cw-film .cw-ruler{left:50%;top:3.6vh;width:' + (TICKS * 2.6) + 'vh;margin-left:-' + (TICKS * 1.3) + 'vh;height:1vh;opacity:.8;' +
      'background:repeating-linear-gradient(90deg,' + BLUE + '.5) 0,' + BLUE + '.5) .14vh,transparent .14vh,transparent 2.6vh),' +
      'repeating-linear-gradient(90deg,rgba(200,228,255,.75) 0,rgba(200,228,255,.75) .2vh,transparent .2vh,transparent 10.4vh);background-size:100% 55%,100% 100%;background-repeat:no-repeat;background-position:0 100%,0 0;}' +
      '.cw-film .cw-mark{left:-.3vh;top:-.6vh;width:.8vh;height:2.2vh;opacity:.5;background:linear-gradient(180deg,rgba(225,240,255,0),#fff 50%,rgba(225,240,255,0));}' +
      // the place word: small, low left, above the subtitles' zone
      '.cw-film .cw-label{left:5.5vw;bottom:25vh;overflow:hidden;opacity:0;padding-bottom:1.1vh;' +
      'font:500 3.4vh/1.1 "CwKantalad","Inter","Segoe UI",system-ui,sans-serif;letter-spacing:.06em;color:#f4f8ff;text-shadow:0 .2vh 1.2vh rgba(0,0,0,.7);white-space:nowrap;}' +
      '.cw-film .cw-txt{position:relative;padding:0 1.4vh 0 1.8vh;transform:translate3d(-101%,0,0);}' +
      '.cw-film .cw-tick{left:0;top:.2vh;width:.36vh;height:3.4vh;background:#4da3ff;}' +
      '.cw-film .cw-rule{left:1.8vh;bottom:0;width:22vh;height:.16vh;transform:scaleX(0);transform-origin:0 50%;' +
      'background:linear-gradient(90deg,rgba(200,228,255,.9),' + BLUE + '.5) 60%,' + BLUE + '0));}';
    b.appendChild(st);
    var parts = gal.parts = {};
    for (var i = 0; i < 2; i++) {
      var sl = part('cw-fs', b), im = part('cw-fp', sl, 'img');
      im.alt = '';
      im.decoding = 'async';
      var lb = part('cw-label', sl), tx = part('cw-txt', lb, 'span');
      tx.appendChild(document.createTextNode(''));
      part('cw-tick', lb, 'i');
      gal.layers.push({ el: sl, img: im, label: lb, txt: tx, rule: part('cw-rule', lb, 'i'), anims: [] });
    }
    part('cw-grade', b);
    parts.leaks = [part('cw-leak cw-lk-l', b), part('cw-leak cw-lk-r', b)];
    parts.sweeps = [part('cw-sweep', b), part('cw-sweep', b)];
    parts.big = part('cw-sweep cw-big', b);
    parts.sweeps.concat([parts.big]).forEach(function (s) { part('', s, 'i'); });
    parts.streak = part('cw-streak', b);
    part('cw-sh', parts.streak, 'i');
    part('cw-sl', parts.streak, 'i');
    [['left:3.2vh', '.24vh 0 0 .24vh'], ['right:3.2vh', '.24vh .24vh 0 0']].forEach(function (c) {
      part('cw-corner', b).style.cssText = c[0] + ';border-width:' + c[1];
    });
    var ruler = part('cw-ruler', b);
    parts.mark = part('cw-mark', ruler, 'i');
    document.body.appendChild(b);
    gal.box = b;
    return b;
  }
  // a band of light across the screen: from off one edge to off the other, `dir` 1 = left to right; its light up and down
  function sweep(list, el, t0, t1, dir, peak) {
    var W = window.innerWidth || 1920, w = el.offsetWidth || 0.5 * (window.innerHeight || 1080);
    var A = 'translate3d(' + Math.round(-1.35 * w) + 'px,0,0)', Z = 'translate3d(' + Math.round(W + 0.35 * w) + 'px,0,0)';
    at(list, el, [[t0, { transform: dir > 0 ? A : Z }], [t1, { transform: dir > 0 ? Z : A }]], 'none');
    var m = t0 + (t1 - t0) / 2;
    at(list, el.firstChild, [[t0, { opacity: 0 }, OUT], [m - 0.15 * (t1 - t0), { opacity: peak }], [m + 0.15 * (t1 - t0), { opacity: peak }, IN], [t1, { opacity: 0 }]], 'none');
  }
  function filmLights(P, list, k, first) {
    var parts = gal.parts, g = P.gate, end = P.end, B = P.B, bar = P.bar;
    // the cut: the wide sweep's middle crosses the screen's on the downbeat; the streak flares out from it
    sweep(list, parts.big, g - 0.8 * B, g + 0.9 * B, k % 2 ? -1 : 1, 1);
    parts.streak.style.top = (k % 2 ? 56 : 38) + '%';
    at(list, parts.streak, [[g - 0.1 * B, { opacity: 0, transform: 'scaleX(.15)' }, OUT], [g + 0.3 * B, { opacity: 1, transform: 'scaleX(.85)' }, EASE],
                            [g + 1.8 * B, { opacity: 0, transform: 'scaleX(1.2)' }]], 'none');
    // every bar line after it: a band crossing, one way then the other
    var lines = [], t;
    for (t = end - bar; t > g + bar * 0.5; t -= bar) lines.unshift(t);
    lines.forEach(function (tl, n) { sweep(list, parts.sweeps[n % 2], tl - 0.1 * B, tl + 1.7 * B, (n + k) % 2 ? 1 : -1, 0.85); });
    // every beat: a leak breathes in a top corner, left then right; four spare beats (a late timer never leaves the light still)
    var beats = Math.max(1, Math.round((end - P.beat0) / P.B)) + 4;
    every(list, parts.leaks[0], [{ opacity: .85, easing: HIT }, { opacity: .12 }], 2 * B, P.beat0, Math.ceil(beats / 2));
    every(list, parts.leaks[1], [{ opacity: .85, easing: HIT }, { opacity: .12 }], 2 * B, P.beat0 + B, Math.ceil(beats / 2));
    // the tempo mark: a step a beat along its ruler, home on every phrase's downbeat (counted back from the phrase's end)
    var pts = [], step = (window.innerHeight || 1080) * 0.026, prev = null;
    for (t = P.beat0; t < end + 4 * B; t += B) {
      var idx = ((TICKS - Math.round((end - t) / B)) % TICKS + TICKS) % TICKS, x = 'translate3d(' + (idx * step).toFixed(1) + 'px,0,0)';
      if (prev !== null) pts.push([t, { transform: prev }, OUT]);
      pts.push([t + Math.min(140, 0.3 * B), { transform: x }]);
      prev = x;
    }
    at(list, parts.mark, pts);
    every(list, parts.mark, [{ opacity: 1, easing: HIT }, { opacity: .45 }], B, P.beat0, beats);
  }
  // one picture: dissolving in under the cut's sweep (a walk's first: up out of the dark on the first beat); its camera's slow
  // move its whole stay, on into the next cut; its word written in on beat 2 and gone a beat before the next cut
  function filmPerform(s, old, k, P, dt, first) {
    cancelAll(s.anims);
    var g = P.gate - dt, end = P.end - dt, B = P.B, mv = FILM_MOVES[k % FILM_MOVES.length];
    s.el.style.zIndex = '2';
    old.el.style.zIndex = '1';
    if (!can(s.el)) {
      s.el.style.opacity = '1';
      old.el.style.opacity = '0';
      s.img.style.transform = mv[0];
      s.label.style.opacity = s.txt.firstChild.nodeValue ? '1' : '0';
      s.txt.style.transform = 'none';
      s.rule.style.transform = 'none';
      return;
    }
    var a = first ? g : g - 0.35 * B, z = first ? g + 1.3 * B : g + 0.5 * B;
    if (!first && a < -40) { a = 0; z = 450; }             // (late: the cut went by without it -- a plain dissolve now)
    at(s.anims, s.el, [[a, { opacity: 0 }, EASE], [z, { opacity: 1 }]], 'both');
    if (!first) at(old.anims, old.el, [[z + 30, { opacity: 1 }], [z + 31, { opacity: 0 }]]);
    at(s.anims, s.img, [[a, { transform: mv[0] }, EASE], [end + 1.5 * B, { transform: mv[1] }]], 'both');
    if (!s.txt.firstChild.nodeValue) return;
    var L = g + (first ? 2 : 1) * B, E = Math.max(L + 1600, end - B);
    at(s.anims, s.label, [[L, { opacity: 0 }], [L + 100, { opacity: 1 }], [E - 400, { opacity: 1 }], [E, { opacity: 0 }]], 'both');
    at(s.anims, s.txt, [[L, { transform: 'translate3d(-101%,0,0)' }, OUT], [L + 700, { transform: 'translate3d(0,0,0)' }]], 'both');
    at(s.anims, s.rule, [[L + 250, { transform: 'scaleX(0)' }, OUT], [L + 250 + 1.5 * B, { transform: 'scaleX(1)' }]], 'both');
  }

  // ==== B. LIGHTS: no pictures -- the ceremony's own stage (horizon arc, floor grid, aurora, searchlights) with the debut's concert
  // beams in front of it, the project's logo held in its sky (the opening clip's last frame, as the stage shows it between lines):
  //   every beat    every beam flares and falls back
  //   every bar     the beams take a new pose on the bar line (lean left, lean right, cross); the horizon's rim, the floor and
  //                 the light on the logo swell on the downbeat
  //   every phrase  (4 bars) on its last beat the beams turn in on the logo; on the downbeat they burst out wide, a ring of light
  //                 goes out from the horizon
  // The stage itself (aurora, searchlights, arc, haze, grid, the logo as a still picture) is painted once and never moves: each
  // of its layers moving was 11 fps at CPU x4 (the floor's flowing rows under their mask the dearest); still, 56.
  var STAGE_BEAMS = [[6, 0, 0], [20, 0, 1], [36, 0, 0], [64, 0, 0], [80, 0, 1], [94, 0, 0], [0, 1, 1], [100, 1, 1]];
  var BEAM_POSES = {
    W: [-46, -30, -13, 13, 30, 46, 30, -30],               // the burst
    F: [-30, -18, -7, 7, 18, 30, 40, -40],                 // the fan
    L: [-40, -31, -22, -12, -3, 6, 58, -26],               // all leaning left
    R: [-6, 3, 12, 22, 31, 40, 26, -58],                   // all leaning right
    X: [24, 15, 7, -7, -15, -24, 50, -50]                  // crossing
  };
  var LOGO_Y = 20;                                         // (vh: the logo's ring, where the beams meet)
  function lightsBox() {
    var b = shell('lights');
    b.style.background = 'radial-gradient(ellipse 85% 40vh at 50% 87vh,#0b2170 0%,#050d33 50%,#000 100%) #000';
    var st = document.createElement('style');
    st.textContent = BASE_CSS +
      // the logo: the opening clip's last frame, still, in the black sky (its own black is the sky's)
      '.cw-lights .cw-logo{left:50%;top:1vh;width:80vh;height:45vh;margin-left:-40vh;opacity:.92;visibility:hidden;}' +
      '.cw-lights .cw-logo img{left:0;top:0;width:100%;height:100%;object-fit:cover;}' +
      '.cw-lights .cw-lglow{left:50%;top:' + (LOGO_Y - 22) + 'vh;width:70vh;height:44vh;margin-left:-35vh;opacity:0;' +
      'background:radial-gradient(ellipse closest-side,' + BLUE + '.26),' + DEEP + '.08) 55%,' + DEEP + '0) 100%);}' +
      // the aurora and the searchlights (the ceremony's)
      '.cw-lights .cw-au{left:-25%;width:150%;top:-28vh;height:89vh;}' +
      '.cw-lights .cw-au-a{background:radial-gradient(ellipse 30% 26% at 30% 55%,rgba(78,132,255,.34),rgba(78,132,255,0) 100%),' +
      'radial-gradient(ellipse 20% 12% at 56% 44%,rgba(126,196,255,.16),rgba(126,196,255,0) 100%),radial-gradient(ellipse 14% 30% at 76% 60%,rgba(77,163,255,.14),rgba(77,163,255,0) 100%);}' +
      '.cw-lights .cw-au-b{background:radial-gradient(ellipse 26% 20% at 70% 50%,rgba(98,84,255,.24),rgba(98,84,255,0) 100%),' +
      'radial-gradient(ellipse 34% 10% at 42% 66%,rgba(77,163,255,.17),rgba(77,163,255,0) 100%);}' +
      '.cw-lights .cw-sl{top:-10vh;width:46vh;height:150vh;margin-left:-23vh;transform-origin:50% 0;opacity:.8;}' +
      '.cw-lights .cw-sl i{left:0;top:0;width:100%;height:100%;clip-path:polygon(46% 0,54% 0,100% 100%,0 100%);' +
      'background:linear-gradient(90deg,rgba(77,163,255,0) 8%,rgba(77,163,255,.13) 50%,rgba(77,163,255,0) 92%);' +
      '-webkit-mask-image:linear-gradient(180deg,#000 0%,rgba(0,0,0,.55) 45%,rgba(0,0,0,0) 85%);mask-image:linear-gradient(180deg,#000 0%,rgba(0,0,0,.55) 45%,rgba(0,0,0,0) 85%);}' +
      // the horizon: the top of a great lit circle; the light running round its rim; its rim's flash
      '.cw-lights .cw-arc{left:50%;top:48vh;width:170.7vh;height:170.7vh;margin-left:-85.35vh;border-radius:50%;border:.53vh solid rgb(120,170,255);' +
      'background:radial-gradient(closest-side,#020516 0%,#030a2a 80%,rgba(46,107,255,.7) 99%,rgba(46,107,255,0) 100%);}' +
      '.cw-lights .cw-rim{left:50%;top:35.6vh;width:195.5vh;height:195.5vh;margin-left:-97.75vh;border-radius:50%;' +
      'background:radial-gradient(closest-side,rgba(46,107,255,0) 84%,rgba(60,120,255,.55) 88%,rgba(46,107,255,0) 100%);}' +
      '.cw-lights .cw-flash{left:50%;top:38vh;width:150vh;height:22vh;margin-left:-75vh;opacity:0;background:radial-gradient(ellipse 50% 50% at 50% 50%,rgba(170,210,255,.55),rgba(78,132,255,.18) 45%,rgba(78,132,255,0) 100%);}' +
      '.cw-lights .cw-run{left:50%;top:47vh;width:172.7vh;height:172.7vh;margin-left:-86.35vh;border-radius:50%;}' +
      '.cw-lights .cw-run i{left:0;top:0;width:100%;height:100%;border-radius:50%;' +
      'background:conic-gradient(from 0deg,rgba(126,196,255,0) 0deg 128deg,rgba(126,196,255,.85) 172deg,#fff 178deg,rgba(126,196,255,0) 180deg 308deg,rgba(126,196,255,.85) 352deg,#fff 358deg,rgba(126,196,255,0) 360deg);' +
      '-webkit-mask-image:radial-gradient(closest-side,rgba(0,0,0,0) 97%,#000 98.6%,rgba(0,0,0,0) 100%);mask-image:radial-gradient(closest-side,rgba(0,0,0,0) 97%,#000 98.6%,rgba(0,0,0,0) 100%);}' +
      // the floor grid running to the horizon (the ceremony's plane; its rows step towards us on the beat)
      '.cw-lights .cw-grid{left:-40%;top:51.3vh;width:180%;height:58.7vh;opacity:.55;overflow:hidden;transform-origin:50% 26.7vh;transform:perspective(67.6vh) rotateX(64deg);' +
      '-webkit-mask-image:linear-gradient(0deg,rgba(0,0,0,.22) 0%,rgba(0,0,0,.5) 30%,#000 59%,rgba(0,0,0,0) 100%);mask-image:linear-gradient(0deg,rgba(0,0,0,.22) 0%,rgba(0,0,0,.5) 30%,#000 59%,rgba(0,0,0,0) 100%);}' +
      '.cw-lights .cw-flow{left:0;right:0;top:-4.27vh;bottom:0;' +
      'background-image:repeating-linear-gradient(90deg,rgba(90,150,255,.5) 0 1px,rgba(0,0,0,0) 1px 8.9vh),repeating-linear-gradient(0deg,rgba(90,150,255,.4) 0 1px,rgba(0,0,0,0) 1px 4.27vh);}' +
      // haze low over the floor, the light pooled at the stage's edge
      '.cw-lights .cw-hz{left:-20%;width:140%;bottom:-8vh;height:70vh;}' +
      '.cw-lights .cw-hz-a{opacity:.7;background:radial-gradient(ellipse 46% 42% at 40% 72%,rgba(160,200,255,.2),rgba(78,132,255,.08) 55%,rgba(78,132,255,0) 80%);}' +
      '.cw-lights .cw-hz-b{background:radial-gradient(ellipse 40% 36% at 62% 78%,rgba(77,163,255,.16),rgba(77,163,255,.05) 55%,rgba(77,163,255,0) 80%);}' +
      '.cw-lights .cw-pool{left:0;width:100%;bottom:0;height:26vh;opacity:.45;background:linear-gradient(0deg,rgba(78,132,255,.38),rgba(78,132,255,.1) 45%,rgba(78,132,255,0));}' +
      // the beams (the debut's): a wide wedge and a core from a foot on the floor or at the sides
      '.cw-lights .cw-beams{left:0;top:0;width:100%;height:100%;}' +
      '.cw-lights .cw-beam{bottom:-4vh;width:26vh;height:96vh;margin-left:-13vh;transform-origin:50% 100%;opacity:.5;}' +
      '.cw-lights .cw-beam.cw-side{bottom:14vh;height:104vh;}' +
      '.cw-lights .cw-bw{left:0;top:0;width:100%;height:100%;clip-path:polygon(46% 100%,54% 100%,100% 0,0 0);opacity:.32;' +
      'background:linear-gradient(0deg,rgba(126,196,255,.7) 0%,rgba(78,132,255,.3) 22%,rgba(78,132,255,.08) 62%,rgba(78,132,255,0) 96%);}' +
      '.cw-lights .cw-bc{left:0;top:0;width:100%;height:100%;clip-path:polygon(49% 100%,51% 100%,66% 0,34% 0);opacity:.7;' +
      'background:linear-gradient(0deg,rgba(210,235,255,.95) 0%,rgba(77,163,255,.5) 18%,rgba(77,163,255,.14) 60%,rgba(77,163,255,0) 96%);}' +
      '.cw-lights .cw-white .cw-bc{background:linear-gradient(0deg,#fff 0%,rgba(235,245,255,.7) 16%,rgba(200,225,255,.18) 60%,rgba(200,225,255,0) 96%);}' +
      // the phrase's ring, going out from the horizon
      '.cw-lights .cw-ring{left:50%;top:48vh;width:90vh;height:90vh;margin:-45vh 0 0 -45vh;border-radius:50%;opacity:0;transform:scale(.2);' +
      'background:radial-gradient(closest-side,rgba(150,200,255,0) 86%,rgba(200,228,255,.75) 95%,rgba(150,200,255,0) 100%);}';
    b.appendChild(st);
    var parts = gal.parts = {};
    // (everything still first, painted once into the layer's own picture; what moves on the music is over it -- measured: the
    // stage's layers each moving, or a still one over a moving one, were 11 fps at CPU x4)
    parts.logo = part('cw-logo', b);
    parts.logoImg = part('', parts.logo, 'img');
    parts.logoImg.alt = '';
    part('cw-au cw-au-a', b);
    part('cw-au cw-au-b', b);
    [['18%', -16], ['82%', 16]].forEach(function (s) { var e = part('cw-sl', b); e.style.left = s[0]; e.style.transform = rot(s[1]); part('', e, 'i'); });
    part('cw-arc', b);
    part('cw-rim', b);
    part('', part('cw-run', b), 'i');
    part('cw-hz cw-hz-a', b);
    part('cw-hz cw-hz-b', b);
    part('cw-flow', part('cw-grid', b));
    parts.glow = part('cw-lglow', b);
    parts.flash = part('cw-flash', b);
    parts.pool = part('cw-pool', b);
    var beams = part('cw-beams', b);
    gal.beamEls = STAGE_BEAMS.map(function (d, j) {
      var e = part('cw-beam' + (d[1] ? ' cw-side' : '') + (d[2] ? ' cw-white' : ''), beams);
      e.style.left = d[0] + '%';
      e.style.transform = rot(BEAM_POSES.F[j]);
      part('cw-bw', e, 'i');
      part('cw-bc', e, 'i');
      return e;
    });
    parts.ring = part('cw-ring', b);
    document.body.appendChild(b);
    gal.box = b;
    logoFind();
    return b;
  }
  // the logo: the stage's own opening clip (ceremony-show.js idleSetup), its last frame, never played
  function logoFind() {
    var parts = gal.parts;
    if (WALK_LOOK !== 'lights' || !parts || !parts.logoImg || gal.logoSrc) return;
    var v = document.querySelector('.cs-idle video'), src = v && v.getAttribute('src');
    if (!src || !/^\/ceremony-asset\/[0-9a-f]{64}$/.test(src)) return;
    gal.logoSrc = src;
    var me = document.createElement('video');
    me.muted = true;
    me.defaultMuted = true;
    me.preload = 'auto';
    me.addEventListener('loadedmetadata', function () { try { me.currentTime = Math.max(0, me.duration - 0.05); } catch (e) { /* not seekable */ } });
    me.addEventListener('seeked', function () {
      try {
        var c = document.createElement('canvas');
        c.width = me.videoWidth || 1920;
        c.height = me.videoHeight || 1080;
        c.getContext('2d').drawImage(me, 0, 0, c.width, c.height);
        parts.logoImg.onload = function () { parts.logo.style.visibility = 'visible'; };
        parts.logoImg.src = c.toDataURL('image/jpeg', 0.92);
      } catch (e) { /* no logo: the stage without it */ }
      me.removeAttribute('src');
      try { me.load(); } catch (e) { /* gone */ }
    });
    me.src = src;
  }
  function beamPoses() {                                   // per beam, the poses above and C: turned in on the logo
    var W = window.innerWidth || 1920, H = window.innerHeight || 1080, y = LOGO_Y / 100 * H;
    return STAGE_BEAMS.map(function (d, j) {
      var foot = d[1] ? H - 0.16 * H : H + 0.06 * H, c = Math.atan2((0.5 - d[0] / 100) * W, foot - y) * 180 / Math.PI, p = { C: Math.round(c * 10) / 10 };
      Object.keys(BEAM_POSES).forEach(function (n) { p[n] = BEAM_POSES[n][j]; });
      return p;
    });
  }
  function lightsPhrase(P, list, k, first) {
    var parts = gal.parts, g = P.gate, end = P.end, B = P.B, bar = P.bar;
    logoFind();
    // the beams: from the logo, burst out on the downbeat, settle to the fan; a new pose on every bar line (moving fastest on
    // it); on the last beat they turn in on the logo for the next phrase
    var lines = [], t;
    for (t = end - bar; t > g + bar * 0.5; t -= bar) lines.unshift(t);
    var order = k % 2 ? ['R', 'L', 'X'] : ['L', 'R', 'X'], pz = beamPoses();
    gal.beamEls.forEach(function (e, j) {
      var p = pz[j], pts = [], last = 'F';
      if (first) pts.push([0, { transform: rot(p.F) }, EASE], [g, { transform: rot(p.F) }, OPEN]);
      else pts.push([g - 0.15 * B, { transform: rot(p.C) }, OPEN]);
      pts.push([g + 0.85 * B, { transform: rot(p.W) }, EASE], [g + 2.6 * B, { transform: rot(p.F) }]);
      lines.forEach(function (tl, n) {
        var nx = order[n % order.length];
        pts.push([tl - 0.4 * B, { transform: rot(p[last]) }, EASE], [tl + 0.8 * B, { transform: rot(p[nx]) }]);
        last = nx;
      });
      pts.push([end - 1.1 * B, { transform: rot(p[last]) }, IN], [end - 0.15 * B, { transform: rot(p.C) }]);
      at(list, e, pts);
    });
    // every beat: each beam flares and falls back (each itself: an opacity on their group is a screen's offscreen pass a frame);
    // four spare beats: a late timer never leaves the light still before the next phrase's
    var beats = Math.max(1, Math.round((end - P.beat0) / B)) + 4;
    gal.beamEls.forEach(function (e) { every(list, e, [{ opacity: 1, easing: HIT }, { opacity: .5 }], B, P.beat0, beats); });
    // every bar: the horizon's rim and the floor light up on the downbeat
    var bar0 = end - bar * Math.floor((end - P.beat0) / bar + 1e-6), bars = Math.max(1, Math.round((end - bar0) / bar)) + 1;
    every(list, parts.flash, [{ opacity: .9, easing: SWELL }, { opacity: 0 }], bar, bar0, bars);
    every(list, parts.pool, [{ opacity: 1, easing: SWELL }, { opacity: .45 }], bar, bar0, bars);
    // the logo's light: a swell on every downbeat, a breath on every beat
    every(list, parts.glow, [{ opacity: 1, easing: SWELL }, { opacity: .3 }], bar, bar0, bars);
    // the phrase: the ring goes out from the horizon
    at(list, parts.ring, [[g, { opacity: .95, transform: 'scale(.2)' }, OUT], [g + 2.4 * B, { opacity: 0, transform: 'scale(2.1)' }]], 'none');
  }
  function galReset() {                                    // everything still: nothing runs while it is down
    gal.layers.forEach(function (s) { cancelAll(s.anims); });
    cancelAll(gal.ph[0]);
    cancelAll(gal.ph[1]);
  }
  // The debut on this same screen (card 1) plays to its end -- its logo clip, its wave, the held logo -- before
  // any picture: the walk's music starts on the debut's hit and the pictures came with it, over the logo
  // bursting in (owner 25 Nov ~03:10, rehearsal 4: "คลิปโลโก้ยังไม่ทันจบเลย โดนตัดไปก่อน" -> "โลโก้เล่นจนจบ").
  // The walk's ask waits while CeremonyShow says a debut is up; off, a line, the hit or a stop drop the wait.
  var DEBUT_POLL_MS = 300;
  function debutUp() {
    try {
      var s = window.CeremonyShow && typeof window.CeremonyShow.state === 'function' ? window.CeremonyShow.state() : null;
      return !!(s && s.reveal);
    } catch (e) { return false; }
  }
  function dropWait() {
    clearTimeout(gal.waiting);
    gal.waiting = null;
  }
  function galleryOn() {
    clearTimeout(gal.linger);
    gal.linger = null;
    if (gal.on || !gal.urls.length) return;
    if (debutUp()) {
      if (!gal.waiting) gal.waiting = setTimeout(function () { gal.waiting = null; galleryOn(); }, DEBUT_POLL_MS);
      return;
    }
    dropWait();
    var b = galBox();
    galReset();                                            // (back up while it was still fading out: from the top)
    gal.on = true;
    gal.first = true;
    b.style.opacity = '1';
    galNext();
  }
  function galleryOff(now) {
    dropWait();
    clearTimeout(gal.linger);
    gal.linger = null;
    clearTimeout(gal.timer);
    gal.timer = null;
    if (!gal.on) return;
    gal.on = false;
    if (gal.box) {
      // `now`: a black debut's press -- gone in this frame, not a 1.2 s fade over the dark hall (review 25 Nov)
      if (now === true) { gal.box.style.transition = 'none'; void gal.box.offsetWidth; }
      gal.box.style.opacity = '0';
      if (now === true) setTimeout(function () { if (gal.box) gal.box.style.transition = 'opacity ' + FADE_MS + 'ms ease'; }, 50);
    }
    setTimeout(function () { if (!gal.on) galReset(); }, now === true ? 60 : FADE_MS);
  }
  function galleryLinger() {
    dropWait();                                           // (the hit before the debut was over: no pictures after it)
    if (!gal.on || gal.linger) return;
    gal.linger = setTimeout(galleryOff, LINGER_MS);
  }
  // (the music's own `stop` is also its `hit`'s first step: the pictures go only on what ends the walk, below)
  window.CatwalkMusic = {
    files: function (map, pictures) {
      if (Array.isArray(pictures)) {
        // (a plain address, or {src, label}: the label a short fixed word from the server, never free text)
        gal.urls = pictures.map(function (p) {
          return typeof p === 'string' ? { src: p, label: '' } : (p && typeof p === 'object' ? { src: p.src, label: typeof p.label === 'string' ? p.label.slice(0, 40) : '' } : null);
        }).filter(function (p) { return p && typeof p.src === 'string' && /^\/ceremony-asset\/[0-9a-f]{64}$/.test(p.src); });
        gal.urls.slice(0, 2).forEach(function (p) { var im = new Image(); im.src = p.src; });
      }
      files(map);
    },
    message: function (m) {
      message(m);
      if (!m || typeof m !== 'object') return;
      if (m.op === 'walk') galleryOn();
      else if (m.op === 'hit') galleryLinger();
      else if (m.op === 'off') { if (m.linger === true) galleryLinger(); else galleryOff(); }
    },
    stop: function (seconds) { stop(seconds); stingStop(); galleryOff(); },
    tempo: function (b) { var r = analyse(b); return { bpm: r.bpm, off: r.off, sure: r.sure }; },
    nextLine: nextLine, barsFor: barsFor,
    _grid: function (f) { fakeGrid = f; },
    beat: function () { return { grid: grid(), log: gal.log.slice() }; },
    gallery: function () { return { on: gal.on, urls: gal.urls.length, k: gal.k, lingering: !!gal.linger, waiting: !!gal.waiting }; },
    pictures_down: function (now) { galleryOff(now === true); },
    // (from the FIRST close: each failed reconnect closes again, 2-30 s apart -- restarting the clock kept the hall's loop
    // playing to its 12-minute cap while the robot's server was away; review 25 Nov)
    lost: function () { if (lostT) return; lostT = setTimeout(function () { lostT = null; stop(3); stingStop(); galleryOff(); }, LOST_MS); },
    found: function () { clearTimeout(lostT); lostT = null; },
    state: function () {
      return { playing: cur ? cur.name : null, want: want ? want.track : null, duck: !!(want && want.duck),
               ready: Object.keys(buf).sort(), context: ac ? ac.state : 'none',
               duck_gain: duck ? Math.round(duck.gain.value * 100) / 100 : null, sting: !!(sting && stingUp()),
               gain: cur ? Math.round(cur.g.gain.value * 100) / 100 : null };
    }
  };
})();
