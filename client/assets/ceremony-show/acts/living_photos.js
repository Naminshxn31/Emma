/* Act "living_photos": the project's own pictures brought to motion (owner 17 Nov 2026:
   "ทำเป็นวิดีโอ โชว์รูปต่างๆให้คนเห็นภาพ ไม่ใช่แค่โมชั่นหนังสือ").
   One act, three cues (options.cue): s04_l06 (the common areas), s02_l09 (what makes it
   distinctive) and the pool joke's set-up s04_l09. (The joke's other three lines -- the dive,
   the laps, "With pleasure" -- are the act emma_dive: Emma herself in the real pool.)

   Every word, number and picture comes from the slide through ctx. Nothing here is a
   sentence. The only clock is her voice (beats and progress); ctx.after / ctx.frame only polish
   what a beat began.

   18 Nov: the film's type (one upright face, sentence case, lit left to right by the core); full
   screen (a picture runs under the subtitle's box, graded down there; no word or card below
   43.8cu); nothing sits still; no flips -- the facilities GATHER into one wall, floor by floor,
   each floor under its own name, and a view lasts >= 2 s.

   Owner round 19 Nov (his notes on version 5):
   - s04_l06 "เปลี่ยนจากการขึ้นตึกแบบนี้เป็นวาดตึก ... ปรับให้มันไวขึ้นแล้วก็ซูมเข้าไปในภาพ เพื่อไปโชว์ว่าแต่ละชั้นมี
     อะไร": the rising hologram is gone. Building A is DRAWN, in light, from the landing site's own
     sketch (embassy-world-landing: sketch-data.js, the strokes its sketch.js engine draws in its
     'glass' look), faster than there (~2.3 s). Then the camera goes into the drawing: as she names
     each floor its band lights up in the drawing and that floor's pictures rise out of it into
     their place on the wall (the wall the owner keeps: "หน้าเดิมที่โชว์ว่าแต่ละชั้นมีอะไรบ้าง"); the
     drawing stays behind the wall, closer and dimmer.
   - the handover from s04_l03 ("transition ตรงนี้ดูไม่สมูท"): ONE subject comes in -- the drawing,
     on its own dark ground, already being drawn in the first frame; no words until "Building A".
   - "The swimming pool" (มีรูปที่เห็นทั้งสระไหม): the shot is now the widest view of the whole lagoon.
   - s04_l09 (ไม่ต้องมีไหม ให้คนดูหน้าเดิมที่โชว์ว่าแต่ละชั้นมีอะไรบ้าง): no facade. The act can carry the
     same wall on (`continues` + options.mode 'wall': a join, its slow life as far in, nothing of it
     -- the pool's two pictures least of all -- coming forward), but the joke's test refuses
     pictures on a set-up that its own line does not back, and every pool picture; so the slide
     as it stands shows the drawing of Building A that the wall stood on, its floors still lit,
     without its water (see build_l09b).
   - s02_l09 (ทำไมขนาดไม่เหมือนอันอื่น ... เปลี่ยนเป็นรูปแอพ smarthome): the three panels are one size and
     their three names one size; the Smart Home panel shows the Smart Home app's own screen.

   Owner round 23 Nov:
   - s04_l06, the "155 metres long" card ("เปลี่ยนเป็น plan นี้ ... สีโมชั่นในสระ ดีแล้วเอามาใช้กับภาพใหม่เลย"): his
     ground-floor plan (c_plan_ground_1123: no unit number, area or building tag on it) in place of the site
     plan; the pool's motion is the same, its outline and middle line traced again on the new picture's
     water (options.lagoon / centre), the card shaped by the picture's own aspect.
   - s04_l09 ("ฉากนี้ ... ให้เปลี่ยนเป็นโลโก้ ตอนแรก"): opens on the logo (options.open 'logo') instead of the
     drawing of Building A, and no longer goes to a logo page at its end -- s04_l11 follows at once. */
CeremonyShow.register('living_photos', function (ctx) {
  var fx = ctx.fx, stage = ctx.stage, cue = ctx.opt('cue', 'none'), late = ctx.late;
  // ---- the logo page when she has finished (owner 20 Nov, review PDF p.8 on s02_l09: "หลังพูดจบให้
  // ขึ้นเป็นหน้านี้" -- the held logo of the opening; p.10 on s04_l09: "ใส่ฉากนี้ได้นะ ... เวลาพูดจบ").
  // options.end 'logo' + a picture `logo` (the opening clip's last frame, keyed on black): everything
  // the line built is laid in one layer (`lp-all`), and once the line is over that layer fades
  // away and the logo comes up on the stage's own light -- the same page the ceremony opened on.
  var endLogo = null;
  if (ctx.opt('end', '') === 'logo' && ctx.has('logo')) {
    var all = ctx.el('lp-all');
    ctx.add(ctx.stage, all);
    stage = all;
    var logoPic = ctx.picture('logo', { fit: 'contain', keyed: 'black' });
    if (logoPic) endLogo = ctx.add(ctx.el('lp-endlogo'), ctx.add(ctx.el('lp-endbreathe'), logoPic.el));
  }
  var STAGE_H = 56.25, MID = 21.9;                 // the stage's height; where a shot's subject sits (clear of the subtitle's box)
  // a camera move that is under way when its shot arrives: fastest at its first frame, slowing
  // all the way (the core's own 'push' starts from rest: the first half second read as a still)
  var ARRIVE = 'cubic-bezier(.25, .46, .45, .94)';
  var EASE = 'var(--cs-ease, cubic-bezier(0.4, 0, 0.2, 1))';

  function C(n) { return 'calc(' + (Math.round(n * 1000) / 1000) + ' * var(--cu))'; }
  function P(n) { return (Math.round(n * 1000) / 1000) + '%'; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function smooth(k) { return k * k * (3 - 2 * k); }
  // (the film's motion identity: slow in and slow out for anything that travels)
  function inout(k) { return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; }
  // a line drawn on a curve of time (the core's play() runs at constant speed): it starts
  // slowly, runs, and closes slowly -- done() once, when it is whole
  function drawEased(route, ms, delay, ease, done) {
    ctx.after(delay, function () {
      var t0 = null, over = false, stop = null;
      stop = ctx.frame(function (t) {
        if (over) return;
        if (t0 === null) t0 = t;
        var k = clamp((t - t0) / ms, 0, 1);
        route.draw(ease(k));
        if (k >= 1) { over = true; if (stop) stop(); if (done) done(); }
      });
    });
  }

  // ---- words: the film's type (the core's faces and its arrival; this act only places them) ----
  function head(key, cls, onStage) {
    return ctx.text(key, { cls: 'cs-type-h1' + (cls ? ' ' + cls : ''), glow: !!onStage });
  }
  function sub(key, cls, onStage) {
    return ctx.text(key, { cls: 'cs-type-h2' + (cls ? ' ' + cls : ''), glow: !!onStage });
  }
  // a small supporting line, near what it names
  function note(key, cls) {
    return ctx.text(key, { cls: 'cs-type-label' + (cls ? ' ' + cls : ''), glow: false });
  }

  // ---- a scene: one picture as a world the camera looks into ----
  // o: {pic, aspect, from: [px, py, scale], to: [...], ms, words}
  function scene(o) {
    var s = { aspect: o.aspect || 16 / 9 }, H = 100 / s.aspect;
    s.H = H;
    s.layer = ctx.el('lp-scene');
    s.grp = ctx.el('lp-grp');
    s.wd = ctx.el('lp-world');
    s.wd.style.height = C(H);
    ctx.add(s.layer, ctx.add(s.grp, s.wd));
    s.cam = fx.camera(s.grp);
    // the camera's view of the picture point (px, py in %) so that it sits at MID of the stage
    s.look = function (px, py, k) {
      var a = STAGE_H / 2 - MID;
      return { x: px, y: (py * H / 100 + a / k) / STAGE_H * 100, scale: k, rotate: 0 };
    };
    // Full screen (18 Nov): a photograph fills the stage to its bottom edge -- the view is held
    // inside the picture: never smaller than the stage, and moved (not stretched) until no
    // edge of the picture is on the screen.
    s.at = function (v) {
      var k = Math.max(v[2], 1, STAGE_H / H), view = s.look(v[0], v[1], k), half = 50 / k;
      view.x = clamp(view.x, half, 100 - half);
      view.y = clamp(view.y, half, H / STAGE_H * 100 - half);
      return view;
    };
    var pic = ctx.picture(o.pic);
    if (pic) {
      var view = fx.parallax(pic, { amount: 0.7, zoom: 1.03 });
      view.drift();
      ctx.add(s.wd, ctx.add(ctx.el('lp-pic'), view.el));
    }
    // (the picture runs under the subtitle's box: its lowest part is graded down, softly --
    // part of the shot, so nothing bright or busy sits behind the Thai line)
    ctx.add(s.layer, ctx.el('lp-grade'));
    // the scene's words: one small block, top left, clear of the subject and of the subtitle,
    // on a grade of the picture's top-left corner (only where a scene has words: o.words)
    s.cap = ctx.el('lp-cap');
    ctx.add(s.layer, o.words ? ctx.el('lp-capshade') : null, s.cap);
    if (o.from) s.cam.set(s.at(o.from));
    s.run = function (isLate) {
      if (!o.to) return;
      if (isLate) { s.cam.set(s.at(o.to)); return; }
      var ms = o.ms || 5200;
      s.cam.to(s.at(o.to), ms, 'push');
      s.grp.style.transition = 'transform ' + ms + 'ms ' + ARRIVE;
    };
    return s;
  }
  // (when the shot comes up -- a timer set here counts from that moment -- its move begins)
  function begin(s) { if (late) s.run(true); else ctx.after(0, function () { s.run(false); }); }

  // a box placed in cu (left, top, width, height)
  function box(el, x, y, w, h) {
    el.style.left = C(x);
    el.style.top = C(y);
    if (w != null) el.style.width = C(w);
    if (h != null) el.style.height = C(h);
    return el;
  }
  // a late beat jumps to its state: no transition on these elements for that change
  function now(els) {
    els.forEach(function (e) { if (e) e.classList.add('lp-now'); });
    ctx.after(60, function () { els.forEach(function (e) { if (e) e.classList.remove('lp-now'); }); });
  }
  // where an element is on the stage now, in cu
  function rectOf(el) {
    var s = stage.getBoundingClientRect(), r = el.getBoundingClientRect(), k = 100 / (s.width || 1);
    return { x: (r.left - s.left) * k, y: (r.top - s.top) * k, w: r.width * k, h: r.height * k };
  }

  var hooks = { beat: function () {}, progress: function () {}, settle: function () {}, clear: function () {}, handover: function () { return null; } };

  // =====================================================================================
  // The drawing of Building A: the landing site's sketch (embassy-world-landing,
  // public/assets/js/sketch-data.js -- the strokes its sketch.js engine draws, a front elevation
  // of Building A over the lagoon), ported 19 Nov: smooth strokes sampled as its own
  // centripetal Catmull-Rom every 3 units and simplified (0.45 units), straight ones as their
  // corners; the hatching (its kind 4: 522 short strokes, the pencil look's texture) left out.
  // One stroke: [kind, weight, t, d, x0, y0, dx, dy, ...] -- t and d in thousandths of the
  // drawing's own timeline (its order: the drafting grid, the frame, the floors, the windows,
  // the garden and the water), x and y in half units of its 1600 x 900 sheet, the first point
  // as it is and the rest as steps. Kinds: 0 the drafting grid, 1 the frame and the floor
  // slabs, 2 the windows and mullions (and the water's edges), 3 small marks, 5 the trees,
  // 6 the water, 7 the wind. Shapes only (ctx.svg): nothing of it can be read.
  // =====================================================================================
  var SKETCH = [[0,3,0,46,-840,1279,4880,6],[0,3,5,46,1610,-1040,0,3880],[0,2,10,46,407,-1040,0,3880],[0,2,14,46,2803,-1040,0,3880],[0,2,19,46,-840,421,4880,-6],[0,2,24,46,-840,453,4880,2],[0,2,29,46,-840,553,4880,-3],[0,2,34,46,-840,657,4880,3],[0,2,38,46,-840,762,4880,-4],[0,2,43,46,-840,861,4880,2],[0,2,48,46,-840,960,4880,-3],[0,1,53,46,408,454,2394,828],[0,1,58,46,408,1282,2394,-828],[0,2,62,46,1041,-1040,0,3880],[0,2,67,46,2164,-1040,0,3880],[0,1,72,26,723,336,0,1000],[0,1,75,26,1418,336,0,1000],[0,1,78,26,1778,336,0,1000],[0,1,81,26,2478,336,0,1000],[0,1,83,26,1080,1096,1052,0],[0,2,86,26,1097,1147,15,25,16,20,18,17,24,20,26,17,27,14,28,14,41,16,35,12,36,9,88,17,80,9,79,3,79,-3,80,-9,88,-17,36,-9,35,-12,41,-16,28,-14,27,-14,26,-17,24,-20,18,-17,16,-20,15,-25],[0,2,89,26,2878,410,0,904],[3,3,96,8,398,1292,20,-20],[3,3,98,8,1031,1292,20,-20],[3,3,101,8,1600,1292,20,-20],[3,3,103,8,2154,1292,20,-20],[3,3,105,8,2792,1292,20,-20],[3,3,108,8,2870,446,16,16],[3,3,110,8,2870,543,16,16],[3,3,113,8,2870,651,16,16],[3,3,115,8,2870,752,16,16],[3,3,117,8,2870,854,16,16],[3,3,120,8,2870,951,16,16],[1,9,120,47,398,410,66,0,85,5,58,1,49,-3,78,-8,52,-2,59,3,90,10,49,1,73,-4,143,-16,90,-3,59,2,123,10,99,2,94,-1,183,-9,76,-1,65,3,101,10,59,2,85,-2,123,-13,53,-3,56,2,73,12,49,4,65,-2,97,-9,72,0],[1,6,132,46,420,456,95,2,107,-6,108,4,107,1,48,-1,120,-6,115,7,85,-5,70,-1,113,2,91,5,111,-2,116,2,32,-1,72,-5,110,-2,140,4,102,-3,115,5,58,-2,125,0,118,-6,107,6,69,1,42,-1],[2,4,144,46,408,418,62,1,90,5,41,0,55,-2,84,-9,57,-1,48,2,90,11,42,1,80,-5,143,-15,84,-4,59,2,129,10,133,3,72,-2,159,-9,88,-1,65,3,108,10,75,2,62,-1,118,-13,52,-3,62,1,78,13,50,3,59,-1,95,-10,64,0],[1,7,156,14,404,410,-10,6,-3,4,0,5,1,5,6,10,18,17],[1,7,160,14,2816,401,10,6,3,4,0,5,-6,15,-19,24],[1,8,170,40,400,547,53,-2,100,7,49,0,47,-1,99,-8,50,-1,56,2,86,7,54,1,65,-3,103,-9,30,-1,58,5,141,7,101,10,74,1,89,0,94,-4,31,-3,58,-11,64,-2,56,0,59,4,16,2,43,10,52,2,67,-1,95,-5,44,0,46,-8,20,-1,25,2,50,8,29,3,63,2,81,0,60,-3,75,-11,31,0],[1,8,175,40,400,651,42,-2,42,2,24,4,53,15,50,3,69,-3,72,-12,50,-2,36,1,57,5,79,1,70,8,29,1,17,-2,39,-10,35,-5,44,-5,38,-1,98,3,92,8,111,3,66,7,33,1,26,-1,50,-8,46,-4,58,-10,54,-2,68,0,61,3,29,4,64,14,36,4,35,-3,65,-10,50,-2,52,1,53,-8,29,1,65,9,37,1,153,0,34,-1,43,-4,60,0],[1,8,181,40,400,753,80,0,22,3,54,12,67,2,63,-1,64,-9,44,-2,38,0,79,5,143,-1,35,-3,55,-11,24,-2,90,7,100,2,74,6,129,3,135,-2,35,-2,70,-8,105,-3,84,1,30,3,48,8,69,1,131,-3,74,3,42,-7,16,-1,82,7,62,-1,88,1,34,-2,50,-9,22,-2,32,0,44,4],[1,8,186,40,400,855,77,1,67,7,86,1,56,5,104,-8,91,3,52,-1,85,1,114,-3,32,3,68,11,94,2,56,-3,76,-12,68,-7,60,3,105,-1,45,2,79,-5,177,0,28,1,52,6,80,1,42,4,71,-4,67,5,16,-1,31,-6,16,-2,86,5,66,1,96,-2,67,-6,56,5,48,-2],[1,8,191,40,414,956,106,4,82,1,390,-4,279,6,416,-6,227,6,256,-7,231,1,196,-3,73,1,128,6],[1,6,226,11,411,456,0,500],[1,7,229,11,478,455,0,503],[1,7,232,11,510,456,0,503],[1,7,234,12,707,454,0,506],[2,3,236,30,412,557,43,-1,98,6,64,1,45,-2,88,-7,50,-1,56,2,84,7,54,1,65,-3,103,-9,30,-1,205,12,95,10,69,1,100,0,78,-4,41,-4,52,-10,34,-2,74,0,61,2,27,3,48,11,57,2,62,-1,90,-5,49,0,62,-10,74,10,30,3,61,2,111,0,47,-3,83,-11],[1,7,237,11,740,454,0,506],[1,7,240,11,868,454,0,504],[2,5,241,8,400,547,-9,5,9,6],[2,5,242,8,2814,546,9,5,-9,5],[1,7,243,11,901,453,0,504],[2,3,243,30,412,661,36,-1,38,2,22,4,33,11,17,4,53,3,63,-3,25,-3,53,-9,56,-2,30,1,57,5,79,1,65,8,29,1,22,-3,39,-9,35,-5,44,-5,38,-1,98,3,92,8,116,3,94,8,122,-13,58,-10,56,-2,79,0,60,4,22,3,59,14,42,3,29,-2,65,-10,50,-2,52,1,53,-8,29,1,66,9,35,1,122,0,59,-2,50,-3,48,0],[1,7,246,12,1160,453,0,508],[2,5,249,8,400,651,-9,5,9,5],[1,7,249,12,1192,451,0,511],[2,5,250,8,2814,655,9,5,-9,6],[2,3,251,30,412,764,71,0,18,2,53,13,89,2,49,-1,58,-9,44,-2,38,0,79,5,143,-1,35,-3,55,-11,24,-2,90,7,100,2,74,6,135,3,129,-2,35,-2,59,-8,79,-3,121,1,30,3,55,8,65,1,128,-3,68,3,48,-7,16,-1,82,7,62,-1,66,1,58,-3,48,-8,20,-2,28,0,38,3],[1,7,252,12,1402,451,0,510],[1,7,254,12,1434,454,0,506],[2,5,256,8,400,753,-9,5,9,6],[2,5,257,8,2814,752,9,5,-9,6],[1,7,257,11,1762,453,0,505],[2,3,258,30,412,866,64,0,68,8,86,1,56,5,104,-9,96,4,47,-1,91,1,108,-3,32,3,75,11,82,2,61,-3,76,-12,68,-7,60,3,105,-1,45,2,79,-5,177,0,28,1,52,6,80,1,42,4,71,-4,65,5,16,-1,36,-7,20,-2,72,5,48,1,116,-2,72,-5,54,4,38,-1],[1,7,260,12,1795,451,0,508],[1,7,263,12,2005,450,0,511],[2,5,263,8,400,855,-9,5,9,6],[2,5,264,8,2814,859,9,5,-9,6],[2,4,265,30,424,966,196,4,370,-4,287,6,410,-6,235,6,246,-6,228,0,195,-3,73,1,128,6],[1,7,266,12,2038,451,0,509],[1,7,269,11,2298,453,0,504],[1,7,271,11,2332,452,0,505],[1,7,274,11,2461,451,0,505],[1,7,277,11,2495,450,0,505],[1,7,280,11,2693,452,0,505],[1,7,283,12,2727,452,0,506],[1,6,286,12,2799,454,0,507],[1,8,292,21,907,953,12,2,7,3,33,21,23,11,7,5,7,9,7,21,13,19,4,9,4,18,1,15,-6,28,-2,22,-4,22,-1,34,1,26,4,32,1,28],[1,8,297,22,1184,961,-30,1,-21,3,-13,4,-9,5,-9,8,-7,10,-4,9,-1,10,1,24,-4,33,-2,38,4,65,2,107],[1,6,302,15,1066,1009,-2,26,0,37,6,48,-7,52,1,106],[2,5,306,22,1220,960,-24,1,-31,-1,-18,4,-18,11,-13,12,-11,15,-7,14,-6,20,-3,17,-3,28,-1,33,4,103,7,55],[2,5,311,10,960,1038,35,0,25,2],[2,5,313,10,918,1142,61,0,45,4],[2,4,316,10,923,1144,41,12,7,5,6,7,3,12,1,29,7,29,0,34],[2,4,318,10,1025,1153,-8,6,-3,6,-11,48,-2,19,2,40],[1,8,321,22,2314,957,-25,-4,-7,0,-8,3,-18,11,-27,13,-11,7,-10,11,-8,16,-11,38,-7,36,2,43,0,64,4,39,0,42],[1,8,326,20,2036,960,33,5,18,9,9,8,7,9,8,14,4,10,6,32,8,26,1,11,-2,192],[2,5,331,22,2000,954,14,1,34,8,20,2,5,2,15,7,5,4,12,15,5,9,3,18,1,55,3,33,-2,78,1,17,4,29,-2,40],[1,6,336,15,2161,1010,-1,30,6,36,1,24,-1,40,-7,41,1,95],[1,7,338,21,1110,1053,996,0],[2,5,339,10,2181,1035,32,-4,26,2,14,-2],[1,8,343,22,1076,1089,64,11,85,4,81,2,116,0,165,2,248,0,76,-2,31,-3,52,-13,29,-3,45,0,64,5],[2,5,348,22,1084,1112,28,-3,28,-1,150,1,64,8,74,-3,321,0,165,-5,76,4,89,-5,49,1],[1,7,354,21,1095,1262,35,4,34,10,87,15,59,18,12,0,24,-5,17,-1,185,7,52,-2,55,1,123,-3,17,1,45,8,18,-1,31,-5,50,-4,60,-9,61,-14,51,-18],[2,5,359,22,1095,1270,51,15,19,7,36,23,12,6,19,5,24,5,47,5,37,-4,72,-2,44,4,33,10,11,2,85,1,50,-1,55,-7,143,-6,140,-15,42,-8,51,-13,17,-6,26,-14,13,-5],[1,6,376,13,1201,537,-31,34,-16,25,-14,29,-6,8,-9,7,-20,8,-9,6],[2,4,379,9,1199,555,-15,16,-16,22,-9,15,-14,29],[1,6,381,12,1201,649,-15,10,-11,10,-21,31,-15,30,-11,12,-20,16],[2,4,383,9,1199,662,-12,10,-10,12,-15,25,-13,26],[1,6,385,13,1204,750,-21,14,-11,13,-5,11,-4,22,-7,11,-48,41],[2,4,387,9,1201,764,-11,9,-10,11,-5,11,-5,25,-6,9,-11,10],[1,6,389,11,1105,957,37,-20,14,-10,19,-27,28,-30],[2,4,392,9,1203,886,-20,20,-17,26,-18,13],[1,6,393,13,2257,645,16,-12,22,-23,31,-18,5,-6,8,-16,4,-4,14,-14,14,-8],[2,4,396,9,2297,623,10,-8,27,-16,5,-6,11,-20,17,-15],[1,6,398,13,2258,747,18,-15,19,-20,31,-18,20,-28,13,-10,12,-7],[2,4,400,9,2297,725,10,-8,27,-16,16,-23,17,-16],[1,6,402,12,2257,849,14,-11,19,-20,22,-20,21,-15,21,-19,17,-10],[2,4,405,9,2297,826,23,-21,22,-15,26,-23],[1,6,407,12,2261,948,23,-6,8,-5,36,-35,10,-18,8,-9,11,-9,15,-7],[2,4,409,9,2369,871,-18,14,-14,24,-28,28],[2,5,410,8,451,458,0,86],[2,4,411,8,526,459,0,89],[2,5,412,8,563,457,0,94],[2,5,413,8,632,454,0,97],[2,5,414,8,686,456,0,92],[2,5,415,8,800,458,0,83],[2,4,416,8,946,454,0,97],[2,4,417,8,946,454,0,97],[2,5,418,8,1012,452,0,99],[2,5,420,8,1121,459,0,85],[2,5,421,8,1445,458,0,97],[2,5,422,8,1445,458,0,97],[2,4,423,8,1495,460,0,100],[2,5,424,8,1593,458,0,103],[2,5,425,8,1896,453,0,88],[2,5,426,8,2058,455,0,99],[2,5,427,8,2088,455,0,103],[2,5,428,8,2124,453,0,106],[2,5,430,8,2187,454,0,105],[2,5,431,8,2226,456,0,101],[2,4,432,8,2256,458,0,97],[2,3,432,8,414,515,60,0],[2,5,433,8,2284,457,0,96],[2,5,434,8,2351,456,0,95],[2,3,434,8,540,477,162,0],[2,5,435,8,2400,456,0,88],[2,5,436,8,2514,453,0,104],[2,3,437,8,742,517,122,0],[2,5,437,8,2542,451,0,107],[2,5,439,8,2575,450,0,109],[2,3,439,8,1196,485,200,0],[2,4,440,8,2612,451,0,108],[2,5,441,8,2659,455,0,102],[2,5,442,8,2754,456,0,95],[2,3,442,8,1798,485,202,0],[2,3,445,8,2336,509,120,0],[2,5,447,8,2754,564,0,90],[2,3,448,8,2730,509,64,0],[2,5,448,8,2659,570,0,88],[2,4,449,8,2612,572,0,87],[2,5,450,8,2575,572,0,87],[2,5,451,8,2542,571,0,87],[2,5,452,8,2514,570,0,88],[2,5,453,8,2400,557,0,91],[2,5,454,8,2284,566,0,89],[2,4,455,8,2256,568,0,89],[2,5,457,8,2226,570,0,89],[2,5,458,8,2187,572,0,93],[2,5,459,8,2124,572,0,97],[2,5,460,8,2088,571,0,93],[2,5,461,8,1896,554,0,91],[2,5,462,8,1692,573,0,93],[2,5,463,8,1593,574,0,92],[2,4,464,8,1495,573,0,88],[2,5,465,8,1445,568,0,92],[2,5,466,8,1300,558,0,92],[2,5,467,8,1300,558,0,92],[2,5,469,8,1072,561,0,110],[2,3,469,8,512,653,190,0],[2,4,470,8,1044,563,0,107],[2,5,471,8,1012,564,0,102],[2,3,471,8,742,615,122,0],[2,4,472,8,946,564,0,97],[2,3,473,9,908,655,246,0],[2,4,473,8,946,564,0,97],[2,5,475,8,800,555,0,100],[2,5,476,8,632,564,0,106],[2,3,476,8,1196,583,200,0],[2,5,477,8,563,564,0,106],[2,3,478,8,1798,581,202,0],[2,4,478,8,526,562,0,98],[2,5,479,8,451,558,0,90],[2,3,480,9,2042,653,250,0],[2,3,483,8,2336,613,120,0],[2,5,484,8,451,661,0,90],[2,3,485,8,2730,613,64,0],[2,4,485,8,526,673,0,88],[2,5,486,8,563,683,0,85],[2,5,487,8,632,683,0,86],[2,4,488,8,946,674,0,88],[2,4,489,8,975,675,0,88],[2,5,491,8,1012,679,0,83],[2,4,492,8,1044,684,0,77],[2,5,493,8,1072,684,0,76],[2,5,493,8,1121,674,0,78],[2,5,494,8,1217,662,0,87],[2,5,496,8,1300,663,0,90],[2,5,497,8,1445,673,0,87],[2,5,498,8,1593,679,0,84],[2,5,499,8,1692,679,0,82],[2,5,500,8,1896,658,0,91],[2,5,501,8,1976,659,0,91],[2,5,502,8,2058,670,0,89],[2,5,503,8,2088,677,0,84],[2,5,504,8,2124,683,0,78],[2,5,505,8,2187,679,0,82],[2,3,506,8,418,677,56,0],[2,5,506,8,2226,672,0,88],[2,4,507,8,2256,670,0,89],[2,3,508,8,512,755,190,0],[2,5,508,8,2284,669,0,90],[2,5,510,8,2351,668,0,92],[2,5,511,8,2400,661,0,92],[2,3,511,8,742,715,122,0],[2,5,512,8,2514,671,0,89],[2,5,513,8,2542,671,0,88],[2,3,514,8,1196,681,200,0],[2,5,514,8,2575,672,0,88],[2,4,515,8,2612,672,0,88],[2,5,516,8,2659,671,0,88],[2,3,516,8,1798,679,202,0],[2,5,517,8,2754,667,0,80],[2,3,519,8,2336,713,120,0],[2,5,521,8,2754,760,0,99],[2,3,522,8,2730,713,62,0],[2,5,522,8,2659,772,0,88],[2,4,523,8,2612,774,0,87],[2,5,524,8,2575,773,0,88],[2,5,525,8,2542,773,0,88],[2,5,526,8,2514,773,0,89],[2,5,527,8,2351,773,0,92],[2,5,528,8,2284,772,0,89],[2,4,529,8,2256,772,0,89],[2,5,530,8,2226,773,0,91],[2,5,531,8,2187,774,0,90],[2,5,532,8,2124,774,0,87],[2,5,533,8,2088,774,0,87],[2,5,534,8,2058,772,0,87],[2,5,535,8,1976,763,0,91],[2,5,536,8,1896,762,0,91],[2,5,537,8,1692,774,0,83],[2,5,538,8,1593,776,0,81],[2,4,539,8,1495,774,0,83],[2,5,540,8,1445,773,0,90],[2,5,541,8,1445,773,0,90],[2,5,542,8,1300,766,0,109],[2,5,543,8,1217,762,0,110],[2,3,543,8,414,817,60,0],[2,5,544,8,1121,766,0,94],[2,5,545,8,1072,773,0,89],[2,3,546,8,742,817,122,0],[2,4,546,8,1044,774,0,89],[2,5,547,8,1012,775,0,88],[2,4,548,8,975,776,0,87],[2,3,549,8,1196,779,200,0],[2,4,549,8,946,775,0,88],[2,4,550,8,946,775,0,88],[2,5,551,8,800,770,0,90],[2,5,552,8,686,782,0,86],[2,3,552,8,1798,777,202,0],[2,4,553,8,526,774,0,86],[2,5,554,8,451,764,0,90],[2,3,555,8,2336,815,120,0],[2,5,558,8,451,867,0,90],[2,3,558,8,2730,773,66,0],[2,4,559,8,526,874,0,85],[2,5,560,8,686,881,0,79],[2,4,561,8,946,876,0,80],[2,4,562,8,975,876,0,80],[2,5,563,8,1012,876,0,80],[2,4,564,8,1044,876,0,81],[2,5,566,8,1072,875,0,83],[2,5,567,8,1121,873,0,86],[2,5,568,8,1121,873,0,86],[2,5,569,8,1217,885,0,77],[2,5,570,8,1300,888,0,74],[2,5,571,8,1445,876,0,83],[2,5,572,8,1593,870,0,87],[2,5,573,8,1692,870,0,86],[2,5,574,8,1896,866,0,96],[2,5,576,8,1976,867,0,94],[2,5,577,8,1976,867,0,94],[2,5,578,8,2088,874,0,83],[2,5,579,8,2124,874,0,82],[2,3,580,8,418,919,56,0],[2,5,580,8,2187,878,0,77],[2,4,581,8,2256,874,0,81],[2,5,582,8,2284,875,0,81],[2,3,583,8,742,917,122,0],[2,5,583,8,2351,878,0,78],[2,5,584,8,2400,870,0,86],[2,5,585,8,2514,875,0,79],[2,3,586,8,1196,915,200,0],[2,5,587,8,2542,874,0,79],[2,5,588,8,2575,874,0,79],[2,4,589,8,2612,874,0,79],[2,3,589,8,1798,917,202,0],[2,5,590,8,2659,873,0,81],[2,5,591,8,2754,872,0,86],[2,3,592,8,2336,917,120,0],[2,6,595,11,1269,1120,10,9,5,8,4,24,2,56,-5,87],[2,3,595,8,2730,921,68,0],[2,6,596,11,1349,1117,-17,20,-7,15,-4,17,-1,48,5,91],[2,6,597,11,1854,1118,14,21,7,37,0,22,-5,81,2,29],[2,6,599,11,1934,1127,-12,11,-5,7,-15,30,-5,15,-3,60,2,56],[2,4,600,8,1128,1121,0,143],[2,4,601,8,1157,1121,0,152],[2,4,602,9,1200,1121,0,159],[2,4,603,9,1240,1121,0,167],[2,4,604,10,1382,1121,0,182],[2,4,605,10,1418,1121,0,183],[2,4,606,10,1454,1121,0,185],[2,4,607,10,1515,1121,0,187],[2,4,608,10,1550,1121,0,188],[2,4,609,10,1584,1121,0,187],[2,4,610,10,1610,1121,0,186],[2,4,611,10,1641,1121,0,187],[2,4,613,10,1668,1121,0,187],[2,4,614,10,1740,1121,0,185],[2,4,615,10,1805,1121,0,187],[2,4,616,10,1950,1121,0,182],[2,4,617,10,1991,1121,0,176],[2,4,618,9,2044,1121,0,164],[2,4,619,9,2072,1121,0,156],[2,4,620,8,2115,1121,0,142],[1,6,623,16,414,1029,492,0],[1,6,625,16,412,1063,494,0],[1,6,626,16,412,1175,492,0],[2,6,628,8,428,968,0,100],[2,4,628,8,450,968,0,154],[2,6,629,8,494,968,0,228],[2,4,630,8,550,968,0,228],[2,4,630,8,586,968,0,208],[2,6,631,8,634,968,0,228],[2,4,632,8,660,1068,0,128],[2,4,633,8,681,1070,0,94],[2,4,633,8,705,1058,0,106],[2,4,634,8,737,968,0,228],[2,4,635,8,763,968,0,82],[2,4,635,8,802,970,0,226],[2,4,636,8,836,1064,0,132],[2,6,637,8,868,982,0,214],[2,6,637,8,902,1058,0,124],[1,6,638,16,2220,1063,580,0],[1,6,640,16,2220,1165,580,0],[1,6,641,16,2220,1243,578,0],[2,4,643,8,2223,1046,0,202],[2,4,643,8,2254,1068,0,210],[2,6,644,11,2299,968,0,312],[2,6,645,11,2332,968,0,312],[2,4,646,8,2361,1064,0,188],[2,6,647,11,2400,968,0,312],[2,4,648,8,2431,1066,0,212],[2,6,649,11,2461,968,0,304],[2,6,650,9,2494,968,0,248],[2,4,651,11,2515,968,0,310],[2,4,652,8,2540,1066,0,148],[2,6,652,11,2574,968,0,312],[2,6,654,11,2611,968,0,310],[2,6,655,11,2647,968,0,312],[2,6,656,10,2692,968,0,272],[2,6,657,11,2726,968,0,312],[2,6,658,11,2766,968,0,312],[2,4,659,9,2789,1012,0,258],[2,7,663,30,642,1322,-3,4,-8,5,-33,9,-89,15,-53,5,-22,-1,-29,-9,-19,-2,-175,10,-47,-1,-34,-4,-28,-9,-28,-12,-12,-2,-18,0,-3,-2,0,-2,4,-4,10,-2,58,1,131,-16,12,0,34,5,42,-11,56,-5,42,0,48,1,32,4,57,3,17,2,24,9,23,13,9,7],[2,6,670,19,643,1352,-10,6,-21,5,-41,6,-42,0,-39,15,-25,4,-25,2,-24,-3,-36,-11,-23,-3,-122,7,-81,0,-46,-2,-17,-2,-20,-7,-16,-9,-9,-9],[2,5,674,8,648,1334,0,150],[2,5,676,22,46,1596,21,2,48,-2,57,23,20,3,41,-2,45,-3,112,-15,43,1,18,-1,102,-23,42,-12,35,-12,22,-12,22,-23,39,-31],[3,3,681,8,105,1377,0,184],[3,3,683,8,196,1384,-3,193],[3,3,685,8,258,1386,4,196],[3,3,687,8,357,1387,1,129],[3,3,689,8,428,1386,-1,163],[3,3,691,8,519,1382,-3,138],[3,3,693,8,582,1377,4,181],[2,6,693,42,704,1493,20,-1,31,3,21,0,200,-11,72,-1,48,-5,47,0,95,-6,66,-2,112,0,20,-4,46,-18,10,-2,48,-1,30,1,46,4,100,4,143,9,18,2,36,8,26,3,64,5,87,4,78,9,49,3,76,11,50,2,29,8,53,20,61,19,213,58,67,20,23,10,10,7,31,27,32,17,60,24,77,22,57,25],[2,4,701,20,1120,1435,126,-3,65,-4,30,-1,369,6,36,4,35,14,21,4,197,21],[2,4,705,11,405,1157,-36,7,-18,6,-15,10,-23,22,-12,6,-37,8,-39,15,-12,0,-7,-1,-10,-4,-3,-4,3,4,6,3,14,3,8,-1,12,-3,26,-11,38,-9,9,-5,21,-20,16,-11,24,-9,40,-8,8,-4,4,-8],[2,4,707,9,467,1209,-58,6,-37,3,-24,0,-30,-2,30,2,30,-1,20,-1,-35,0,114,-8],[2,4,709,9,467,1209,-58,6,-37,3,-24,0,-30,-2,30,2,30,-1,20,-1,-35,0,114,-8],[2,4,711,12,293,1242,-9,4,-13,2,-4,2,1,2,28,18,21,21,11,6,25,0,95,-5,139,0,13,-3,26,-13,28,-11,-30,11,-20,11,-10,4,-12,1,-25,0],[2,5,713,13,716,1447,-17,-8,-7,-6,3,-7,11,-13,9,-8,10,-5,34,-9,29,-5,158,-20,49,-15,32,-5,31,-2,19,2,32,9,5,3,9,7,6,9,-1,10,-3,6,-15,12,-19,12],[2,4,716,8,1425,1444,-70,1,-37,3,50,-4,121,-1],[2,4,718,8,1849,1502,52,3,37,3,12,4,14,12,14,3,21,1],[2,4,719,8,1895,1425,3,13,10,5,58,3,82,8,27,-1],[2,4,721,18,2325,1527,21,4,51,22,74,24,267,76,85,28,54,21,52,24,70,37,48,30],[3,4,725,8,2382,1304,18,5,66,7,-70,-7,-9,-4,1,-2,19,0],[3,4,726,8,2381,1289,73,12,61,7],[3,4,728,8,2450,1279,9,8,19,4,14,8,8,2,13,-1,3,-2,4,-6,3,-1,5,1,16,8,14,1,6,-2,4,-7,4,-2,24,12],[3,4,730,8,2541,1262,15,3,22,15,27,5],[3,4,732,8,2701,1285,23,10,10,1,10,-1],[7,3,733,11,3,563,51,19,41,12,59,13,86,15,71,18],[3,4,733,8,2739,1283,18,10,9,2,11,0,4,-6],[7,3,737,10,2908,641,126,-36,88,-32,61,-18],[7,3,741,16,3187,561,-71,20,-79,29,-132,36,-46,16,-8,5,-3,2,-1,3,7,5,16,2,22,-3,26,-5,41,-13,87,-33,30,-8,75,-14,17,-5,25,-11],[5,5,749,22,12,1010,7,-12,11,-4,13,-11,11,-5,11,-12,14,-8,9,-9,16,-2,15,-6,18,1],[5,5,754,52,143,938,13,2,16,-1,15,6,12,1,17,11,12,2,13,10,19,8,13,16,15,12,9,4,13,-3,17,-10,5,-7,2,-12,3,-5,13,-16,10,-5,16,-1,12,2,18,7,5,3,11,12,12,5,14,9,12,0,15,6,13,0,10,5,16,3,10,7,13,2,14,7,12,1,15,7,11,3,11,12,5,10,-1,16,4,15,-5,16,0,16,-9,13,-5,16,-11,17,-3,16,-6,15,-1,20,-4,12,-9,17,-14,7,-12,11,-12,4,-11,12,-7,2,-11,0,-10,-4,-3,-11,-8,-14,-2,-13,-7,-10,-8,-9,-7,-5,-8,-3,-12,-11,-12,-4,-9,-8,-12,-4,-20,-2,-12,3,-13,0,-15,7,-12,2,-13,9,-13,3,-17,10,-16,4,-9,9,-11,6,-6,5,-6,17,-2,16,6,20,1,13,-1,13,-6,10,-5,5,-11,6,-5,2,-5,0,-10,-3,-16,0,-14,-8,-17,-2,-15,-6,-12,2,-16,-4,-12,2,-16,-3,-12,2,-12,-4,-16,0,-22,-12],[5,5,766,28,730,1092,-10,-1,-16,5,-16,0,-10,8,-9,10,-2,7,0,16,-4,12,2,20,-4,16,0,17,-5,6,-28,23,-5,6,-3,18,0,13,6,13,9,9,16,2,11,4,16,-2,16,2,12,-3,16,0,11,-4,13,1,15,-4,12,1,12,-4,16,-1,11,-5,9,-8,9,-13,10,-11,2,-16,4,-12,-2,-16,6,-15,-2,-13,3,-19,-8,-13,-6,-15,-14,-14,-10,-7,-16,-4,-16,-12,-16,0,-16,-3,-13,5],[5,5,772,52,2824,936,-18,2,-9,7,-14,7,-6,9,-6,16,0,16,-3,12,2,15,-3,12,3,16,-2,12,3,12,-2,16,2,12,-1,16,3,12,-1,12,4,16,-1,13,-3,12,-4,7,-3,2,-17,6,-8,1,-14,-9,-15,-5,-13,-10,-16,0,-16,-5,-20,3,-16,-3,-15,5,-16,-1,-16,6,-20,1,-13,5,-10,8,-6,15,-10,13,-5,11,-9,6,-18,6,-17,-1,-12,3,-16,-2,-12,2,-12,-3,-16,2,-12,-3,-12,2,-15,-4,-17,0,-11,-4,-12,2,-16,-2,-18,5,-10,7,-7,9,-5,16,2,13,6,16,3,3,15,5,13,9,17,3,13,10,19,6,9,10,9,14,0,4,-5,7,-10,9,-6,4,-19,6,-9,6,-9,8,-6,11,-1,7,0,13,2,6,9,12,4,4,14,8,7,11,10,7,8,14,10,7,8,9,15,6,9,7,16,4,10,7,16,2,15,8,12,1,14,9,16,2,14,8,16,1,15,7,17,0,14,6,21,3,13,9,16,4,17,12,12,1,15,7,16,-1,15,5,17,0,15,6,12,1,15,7,12,1,14,8,12,2,14,7,12,1,11,6,13,0,14,7,13,1,14,9,12,2,13,9,16,5,8,9,14,9,9,14,16,15,12,4,13,1,11,6,20,5,10,13,14,9,12,13],[6,4,783,14,1180,1380,52,-5,52,-2,133,5,75,-1],[5,5,784,22,2830,934,21,-4,16,4,16,-1,15,5,12,-1,15,6,13,0,13,8,12,2,10,8,16,3,11,7,12,1,14,8,12,2,15,7,12,0,16,5,12,-2,16,3,16,-1,11,5,16,1,13,9,15,5,3,5,5,11],[6,4,785,18,1400,1404,98,6,64,1,110,-3,150,1,150,-8],[6,4,787,16,1720,1371,64,2,92,6,52,2,116,-5,75,-1,69,1,52,-3],[6,4,789,14,840,1525,75,-6,52,-2,116,1,109,-3,64,1],[6,4,791,21,1120,1552,64,3,144,-1,150,9,116,-5,133,1,69,-2],[6,4,793,23,1600,1537,87,6,104,1,144,4,46,-1,87,-9,52,-3,52,1,92,5,64,0],[6,4,796,16,660,1621,92,-7,41,-1,35,2,80,10,29,2,35,1,52,-2,52,0,52,3,52,5],[6,4,798,23,1280,1654,58,-2,115,0,139,-8,162,10,121,0,133,5],[6,4,801,21,1880,1619,64,-4,144,0,156,-7,144,8,116,-1,52,2],[6,4,803,21,940,1720,46,1,41,-1,109,-10,35,-1,133,4,98,-3,41,0,40,3,81,7,52,2],[6,4,806,24,1720,1746,52,-4,75,-13,35,-3,46,-1,98,2,110,-7,52,2,87,15,46,4,40,1,87,-5,52,1],[6,4,809,16,500,1739,87,6,34,1,41,-2,104,-12,46,-1,98,2,110,-6],[1,6,940,14,405,411,-10,6,-3,4,0,5,1,5,6,10,18,17],[1,6,945,40,399,411,66,1,90,5,47,0,55,-2,78,-9,57,-1,49,2,95,11,42,1,80,-5,143,-15,84,-4,59,2,129,10,133,3,72,-2,159,-9,88,-1,65,3,108,10,75,2,62,-1,118,-13,52,-3,62,1,78,13,50,3,59,-1,97,-10,72,1],[1,6,959,14,2817,403,10,6,3,4,0,5,-6,15,-19,23],[1,6,965,14,908,955,19,4,38,24,25,13,7,10,7,21,15,23,5,17,2,20,-6,28,-7,54,0,44,5,38,1,28],[1,6,970,14,1185,962,-24,0,-40,8,-9,5,-9,9,-7,9,-4,10,-1,10,1,24,-4,32,-2,38,4,65,2,107],[1,6,975,14,2037,961,33,6,14,5,9,7,8,8,9,15,6,14,6,32,7,21,2,17,-2,191],[1,6,981,14,2315,958,-25,-4,-15,4,-56,31,-10,11,-8,15,-11,38,-7,36,2,43,0,64,4,39,0,42]];
  // the sketch's own floor marks (its 'assist' look labels L1..L8 from these): the slabs of the
  // tower, the podium in three; below the ground line, the basement this act draws in on its word
  var SK = { w: 1600, h: 900, box: [204, 197, 1401, 641], ground: 641, base: 706 };
  var FLOORS = { cow: [479.3, 533.2], gym: [587.1, 641], bsmt: [641, 706] };
  var DRAW_MS = 2350, DRAW_LEAD = 0.1;             // the whole drawing; how far into its clock it is at its first frame
  var LOOK = {                                      // what each kind looks like, in the landing site's 'glass' look
    0: ['k0', 0.7], 1: ['k1', 1.15], 2: ['k2', 0.8], 3: ['k3', 0.7], 5: ['k5', 0.9], 6: ['k6', 0.8], 7: ['k7', 0.8]
  };

  function sketch(drawn) {
    var s = { strokes: [], lo: 0, done: !!drawn };
    s.layer = ctx.el('lp-scene lp-draw');
    s.grp = ctx.el('lp-grp');
    s.wd = ctx.el('lp-world');
    s.wd.style.height = C(STAGE_H);
    ctx.add(s.layer, ctx.add(s.grp, s.wd));
    s.cam = fx.camera(s.grp);
    function svg(cls) { return ctx.svg('svg', { viewBox: '0 0 1600 900', preserveAspectRatio: 'none', cls: 'lp-sksvg ' + cls }); }
    var glow = svg('lp-skglow'), main = svg('lp-skmain'), land = svg('lp-skland'), base = svg('lp-skbase');
    s.land = land;
    SKETCH.forEach(function (r) {
      var look = LOOK[r[0]];
      if (!look) return;
      var x = r[4], y = r[5], pts = [x / 2, y / 2], d = 'M ' + x / 2 + ' ' + y / 2, ysum = y / 2, n = 1, len = 0, cl = [0];
      for (var i = 6; i + 1 < r.length; i += 2) {
        x += r[i]; y += r[i + 1];
        var px = x / 2, py = y / 2;
        len += Math.hypot(px - pts[pts.length - 2], py - pts[pts.length - 1]);
        cl.push(len);
        pts.push(px, py);
        d += ' L ' + px + ' ' + py;
        ysum += py; n++;
      }
      // (the garden, the water and the wind are their own layer: "outdoors" lights them)
      var isLand = r[0] >= 5 || (ysum / n > 655);
      var width = look[1] + (r[0] === 1 ? 0.07 * r[1] : 0);
      var path = ctx.svg('path', { d: d, pathLength: 1, cls: look[0], 'stroke-width': width.toFixed(2) });
      var row = { t: r[2], d: Math.max(8, r[3]), pts: pts, cl: cl, len: len || 1, paths: [path], f: drawn ? 1 : 0 };
      ctx.add(isLand ? land : main, path);
      if (r[0] === 1) {                               // the frame's glow: a wide faint copy under it
        var g = ctx.svg('path', { d: d, pathLength: 1, cls: 'kg', 'stroke-width': (width * 4.2).toFixed(2) });
        ctx.add(glow, g);
        row.paths.push(g);
      }
      row.paths.forEach(function (p) {
        p.style.strokeDashoffset = drawn ? '0' : '1';
        if (!drawn) p.style.opacity = '0';
      });
      s.strokes.push(row);
    });
    s.strokes.sort(function (a, b) { return a.t - b.t; });
    // the basement, drawn on its word: its outline under the ground line and the piers going down
    var bx0 = 262, bx1 = 1348, by0 = SK.ground, by1 = SK.base, bp = [];
    [[bx0, by0, bx0, by1, bx1, by1, bx1, by0], [610, by0, 610, by1], [805, by0, 805, by1], [1000, by0, 1000, by1]].forEach(function (q, i) {
      var dd = 'M ' + q[0] + ' ' + q[1];
      for (var j = 2; j < q.length; j += 2) dd += ' L ' + q[j] + ' ' + q[j + 1];
      var p = ctx.svg('path', { d: dd, pathLength: 1, cls: i ? 'kb2' : 'kb', 'stroke-width': i ? '0.8' : '1.3' });
      p.style.strokeDashoffset = drawn ? '0' : '1';
      ctx.add(base, p);
      bp.push(p);
    });
    // the floors' bands: a band of light across the building at that floor, lit on its word
    s.bands = {};
    Object.keys(FLOORS).forEach(function (id) {
      var f = FLOORS[id], b = ctx.el('lp-band');
      b.style.left = P(SK.box[0] / SK.w * 100);
      b.style.width = P((SK.box[2] - SK.box[0]) / SK.w * 100);
      b.style.top = P(f[0] / SK.h * 100);
      b.style.height = P((f[1] - f[0]) / SK.h * 100);
      ctx.add(b, ctx.el('lp-bandsweep'));
      s.bands[id] = b;
    });
    // where the garden's pictures come from: the trees at the building's right
    s.garden = ctx.el('lp-mark');
    s.garden.style.left = P(92);
    s.garden.style.top = P(74);
    s.pen = ctx.el('lp-pen');
    ctx.add(s.wd, glow, main, land, base, s.bands.cow, s.bands.gym, s.bands.bsmt, s.garden, s.pen);
    // (its lower part, under the subtitle's box, graded down -- the water's lines stay quiet there)
    ctx.add(s.layer, ctx.el('lp-skgrade'));

    function set(row, f) {
      if (f === row.f) return;
      if (row.f === 0 && f > 0) row.paths.forEach(function (p) { p.style.opacity = ''; });
      row.f = f;
      var off = String(Math.round((1 - smooth(f)) * 10000) / 10000);
      row.paths.forEach(function (p) { p.style.strokeDashoffset = off; });
    }
    function tip(row) {                               // where the pen is on a stroke being drawn
      var at = row.len * smooth(row.f), i = 1;
      while (i < row.cl.length - 1 && row.cl[i] < at) i++;
      var a = clamp((at - row.cl[i - 1]) / ((row.cl[i] - row.cl[i - 1]) || 1), 0, 1);
      return [row.pts[i * 2 - 2] + (row.pts[i * 2] - row.pts[i * 2 - 2]) * a, row.pts[i * 2 - 1] + (row.pts[i * 2 + 1] - row.pts[i * 2 - 1]) * a];
    }
    function finish() {
      var was = s.done;
      s.strokes.forEach(function (row) { set(row, 1); });
      s.done = true;
      s.pen.classList.remove('lp-on');
      // (its frame brightens once as it closes -- not for a drawing that is there already: a
      // wall going on from the line before would flash)
      if (!was) s.layer.classList.add('lp-closed');
    }
    // the drawing in its own time (an entrance: it starts with the shot, ~6% in at its first
    // frame, and is whole long before the first floor is named)
    s.draw = function () {
      if (s.done) return;
      var t0 = null, stop = null;
      stop = ctx.frame(function (t) {
        if (s.done) { if (stop) stop(); return; }
        if (t0 === null) t0 = t - DRAW_LEAD * DRAW_MS;
        // (the drawing's clock starts slowly and gathers pace: the shot before is still dissolving
        // over its first second -- the frame should be drawn where it can be seen being drawn)
        var u = (t - t0) / DRAW_MS, T = (u < 1 ? Math.pow(u, 1.45) : u) * 1000, last = null, L = s.strokes;
        while (s.lo < L.length && L[s.lo].f >= 1) s.lo++;
        for (var i = s.lo; i < L.length && L[i].t <= T; i++) {
          var row = L[i];
          if (row.f >= 1) continue;
          set(row, clamp((T - row.t) / row.d, 0, 1));
          if (row.f < 1) last = row;
        }
        if (last) {
          var p = tip(last);
          s.pen.classList.add('lp-on');
          s.pen.style.transform = 'translate3d(' + C(p[0] / 16) + ', ' + C(p[1] / 16) + ', 0)';
        } else s.pen.classList.remove('lp-on');
        if (s.lo >= L.length || T > 1100) { finish(); if (stop) stop(); }
      });
    };
    s.finish = finish;
    // the basement's outline, drawn as she names it
    s.basement = function (isLate) {
      bp.forEach(function (p, i) {
        p.style.transition = isLate ? 'none' : 'stroke-dashoffset ' + (i ? 520 : 760) + 'ms ' + EASE + ' ' + (i ? 0.28 + i * 0.06 : 0) + 's';
        p.style.strokeDashoffset = '0';
      });
    };
    // a floor's band lights up on its word (and rests, lit a little); "outdoors" lights the garden and the water
    s.light = function (id, isLate) {
      if (id === 'gar') { if (isLate) now([land]); land.classList.add('lp-lit'); return; }
      var b = s.bands[id];
      if (!b) return;
      if (isLate) { now([b]); b.classList.add('lp-on', 'lp-rest'); return; }
      b.classList.add('lp-on');
      ctx.after(1500, function () { b.classList.add('lp-rest'); });
    };
    return s;
  }

  // =====================================================================================
  // The wall: the pictures of the five places, in three columns, each place under its own
  // name (s04_l06; s04_l09 has it without the words). A tile is one cell of its floor's sheet
  // (options.sheets: cols, rows, count); a tile can rise into its place from a floor of the
  // drawing (fly).
  // =====================================================================================
  var T = { w: 9, h: 5.0625, g: 0.45, lbl: 2.3, top: 10.4, gap: 1.0 };
  var COL = { a: 5.55, b: 36.05, c: 76 };
  var LOW = T.top + T.lbl + 3 * T.h + 2 * T.g + T.gap;          // the second place of columns a and c
  var WALL_IN = { x: 48, y: 47, scale: 1.06 }, WALL_END = { x: 50, y: 50, scale: 1 };
  // the drawing's camera: as it is drawn (a slow push already under way), then into the floors
  var DRAW_IN = { x: 50, y: 53.5, scale: 1.03 }, DRAW_ON = { x: 50, y: 55, scale: 1.1 }, DRAW_DEEP = { x: 50, y: 64, scale: 1.7 };

  function makeWall(words) {
    var SHEET = ctx.opt('sheet', [768, 432, 8]);     // a floor's sheet: one cell's width, height and the gutter (px of the file)
    var w = { groups: {}, all: [], said: [] };
    w.layer = ctx.el('lp-scene lp-wall');
    var wgrp = ctx.el('lp-grp'), wworld = ctx.el('lp-world');
    w.drift = ctx.el('lp-drift');
    wworld.style.height = C(STAGE_H);
    ctx.add(w.layer, ctx.add(wgrp, ctx.add(wworld, w.drift)));
    w.grp = wgrp;
    w.cam = fx.camera(wgrp);
    w.cam.set(words ? WALL_IN : WALL_END);
    var order = 0;
    function tile(key, i, x, y, still) {
      var t = box(ctx.el('lp-tile', key), x, y, T.w, T.h), fly = ctx.el('lp-fly'), tin = ctx.el('lp-tin'), cell = ctx.el('lp-cell');
      var pic = ctx.picture(key), meta = ctx.opt('sheets.' + key, null);
      if (pic && meta && meta.length >= 2) {
        // the whole sheet, moved so that cell i fills the tile exactly (the tile is 16:9 like a cell)
        var cols = meta[0], rows = meta[1], cw = SHEET[0], ch = SHEET[1], g = SHEET[2];
        var c = i % cols, r = Math.floor(i / cols);
        pic.el.classList.add('lp-sheet');
        pic.el.style.width = P((cols * cw + (cols - 1) * g) / cw * 100);
        pic.el.style.height = P((rows * ch + (rows - 1) * g) / ch * 100);
        pic.el.style.left = P(-c * (cw + g) / cw * 100);
        pic.el.style.top = P(-r * (ch + g) / ch * 100);
      }
      ctx.add(w.drift, ctx.add(t, ctx.add(fly, ctx.add(tin, ctx.add(cell, pic && pic.el), ctx.el('lp-sheen')))));
      t.fly = fly;
      // (the wall's slow life brings the pictures forward one at a time, in a scattered order --
      // the pool's two never: in s04_l09 they would hint at the joke)
      if (still) t.classList.add('lp-still');
      t.style.setProperty('--k', String((order * 17) % 40));
      order++;
      w.all.push(t);
      return t;
    }
    function label(key, x, y) {
      if (!words || !ctx.has(key)) return null;
      var l = box(ctx.el('lp-glabel'), x, y);
      ctx.add(w.drift, ctx.add(l, note(key, 'lp-gword')));
      return l;
    }
    function count(key, fallback) { var m = ctx.opt('sheets.' + key, null); return m && m.length >= 3 ? m[2] : fallback; }
    function place(id, labelKey, key, n, cols, x, y) {
      var gr = { id: id, tiles: [] };
      label(labelKey, x, y);
      for (var i = 0; i < n; i++) {
        gr.tiles.push(tile(key, i, x + (i % cols) * (T.w + T.g), y + T.lbl + Math.floor(i / cols) * (T.h + T.g)));
      }
      w.groups[id] = gr;
      return gr;
    }
    place('bsmt', 't_bsmt', 'p_wb', count('p_wb', 9), 3, COL.a, T.top);
    place('gym', 't_gym', 'p_wg', count('p_wg', 19), 4, COL.b, T.top);
    place('cow', 't_cow', 'p_w3', count('p_w3', 5), 3, COL.a, LOW);
    place('gar', 't_gar', 'p_wo', count('p_wo', 5), 2, COL.c, T.top);
    // "several more places to relax": a card of its own in the last place of the outdoor block
    if (words && ctx.has('t_lawn')) {
      w.relax = box(ctx.hold(fx.glass('lp-relax'), 't_lawn'), COL.c + T.w + T.g, T.top + T.lbl + 2 * (T.h + T.g), T.w, T.h);
      ctx.add(w.drift, ctx.add(w.relax, note('t_lawn', 'lp-relaxword')));
    }
    // the pool: its name, its two pictures (the whole lagoon first), and its length under them
    label('t_pool_l', COL.c, LOW);
    // (the tiles take the small copies -- the whole lagoon p_poolt, the eye-level view p_pool2 -- and only
    // the shot grown out of the first takes the 3840 px one, p_pool: a 3840 px picture in a 9cu tile was
    // drawn again at its size as the tiles landed and as the shot went back -- a 50 ms frame at 2552 px)
    w.p1 = tile(ctx.has('p_poolt') ? 'p_poolt' : 'p_pool', 0, COL.c, LOW + T.lbl, true);
    w.p2 = tile('p_pool2', 0, COL.c + T.w + T.g, LOW + T.lbl, true);
    w.groups.pool = { id: 'pool', tiles: [w.p1, w.p2] };
    var wfig = words ? ctx.figure('f_len', { cls: 'lp-wallfig' }) : null;
    if (wfig) ctx.add(w.drift, ctx.add(box(ctx.el('lp-wallfigat'), COL.c, LOW + T.lbl + T.h + 0.5), wfig));

    // a place's pictures come in: in reading order, one stagger budget for any size. With
    // `from` (a function: a tile's place now -> the point of the drawing it rises from, in cu),
    // each one rises out of that floor of the drawing into its place: across on a decelerating
    // curve, up on an eased one -- an arc -- growing from a third of its size as it comes.
    w.gather = function (id, isLate, from) {
      var gr = w.groups[id];
      if (!gr) return;
      var step = gr.tiles.length > 10 ? 0.024 : 0.05;
      if (isLate || !from) {
        gr.tiles.forEach(function (t, i) { t.style.setProperty('--d', (0.08 + i * step).toFixed(3) + 's'); });
        if (isLate) now(gr.tiles);
        gr.tiles.forEach(function (t) { t.classList.add('lp-on'); });
        w.said.push(gr);
        return;
      }
      gr.tiles.forEach(function (t) {
        t.style.transition = 'none'; t.style.transform = 'none';
        t.fly.style.transition = 'none'; t.fly.style.transform = 'none';
      });
      var rs = gr.tiles.map(rectOf);
      gr.tiles.forEach(function (t, i) {
        var r = rs[i], k = r.w / T.w || 1, o = from(r);
        var dx = (o.x - (r.x + r.w / 2)) / k, dy = (o.y - (r.y + r.h / 2)) / k;
        t.style.transform = 'translate3d(' + C(dx) + ', 0, 0) scale(.34)';
        t.fly.style.transform = 'translate3d(0, ' + C(dy) + ', 0)';
      });
      void stage.offsetWidth;
      // (each one first lights up ON its floor -- small, where the band is -- and then rises: the
      // eye sees where the pictures come from before they go)
      gr.tiles.forEach(function (t, i) {
        var d0 = 0.04 + i * step, d = (d0 + 0.16).toFixed(3) + 's';
        t.style.transition = 'transform 1150ms cubic-bezier(.2, .62, .32, 1) ' + d + ', opacity 300ms ' + EASE + ' ' + d0.toFixed(3) + 's';
        t.fly.style.transition = 'transform 1150ms cubic-bezier(.55, 0, .28, 1) ' + d;
        t.style.transform = '';
        t.fly.style.transform = '';
        t.classList.add('lp-on');
      });
      w.said.push(gr);
    };
    // the slow life: a drift of the whole wall, then one picture after another catching the
    // light. `at` (seconds) = how far in each is already (a wall that goes on from the line before)
    w.drifting = function (at) {
      if (at > 0) w.drift.style.animationDelay = (-at).toFixed(3) + 's';
      w.layer.classList.add('lp-drifting');
      w.driftAt = Date.now() - (at > 0 ? at * 1000 : 0);
    };
    w.alive = function (at) {
      if (w.layer.classList.contains('lp-alive')) return;
      if (at > 0) w.layer.style.setProperty('--al', at.toFixed(3) + 's');
      w.layer.classList.add('lp-alive');
      w.aliveAt = Date.now() - (at > 0 ? at * 1000 : 0);
    };
    return w;
  }

  // =====================================================================================
  // s04_l06: Building A drawn; into its floors, each floor's pictures rising out of it onto
  // the wall; the pool grows out of its tile, takes its 155 metres, and goes back; the wall
  // lives on through Khun Miki's answer
  // =====================================================================================
  function build_l06() {
    var LAG = ctx.opt('lagoon', []), CEN = ctx.opt('centre', []);
    ctx.bg('off');                                   // (the drawing has its own dark ground)
    var sk = sketch(late);
    sk.cam.set(late ? DRAW_DEEP : DRAW_IN);
    var w = makeWall(true);
    // its name top left -- where the wall's heading stays while the floors gather; it comes on
    // her words "Building A" (no words while the shot before dissolves into the drawing)
    var mainT = note('main', 'lp-kick'), giantT = sub('giant', '', true);
    // (owner 19 Nov, version 6: "มันดูติดๆ ซ้อนกันไหม" -- her question, and before it this heading, were two
    // lines that came down onto the floor names and the wall's first row, touching. Each is ONE line now,
    // the small words and the large on one baseline, glyphs 3.7-6.5cu: ~3.7cu of clear dark above the
    // floor names (from ~10.2cu, measured on shots). (A wall laid out smaller and lower instead cost frames as the pool grew out of it.) The
    // dark ground under the words comes with them, not before.)
    var title = ctx.add(ctx.el('lp-title'), ctx.hold(ctx.el('lp-titleshade'), 'giant'), mainT, giantT);

    // ---- the pool, grown out of its tile to the whole screen ----
    var hero = ctx.el('lp-scene lp-hero'), hgrp = ctx.el('lp-grp'), hworld = ctx.el('lp-world');
    hworld.style.height = C(STAGE_H);
    var hpic = ctx.picture('p_pool');
    ctx.add(hero, ctx.add(hgrp, ctx.add(hworld, ctx.add(ctx.el('lp-pic'), hpic && hpic.el))));
    var hcam = fx.camera(hgrp);
    // (owner 19 Nov, "มีรูปที่เห็นทั้งสระไหม": the widest view of the whole lagoon, from above. The
    // lagoon is the picture's lower half: the shot leans down to the water, its bottom edge
    // held at the stage's -- the far channels, the islands, the rings and the pavilion above the
    // subtitle's box; the near water runs on under it, graded)
    var HERO_TILE = { x: 50, y: 50, scale: 1 }, HERO_END = { x: 45, y: 61.5, scale: 1.3 };
    hcam.set(HERO_TILE);
    var hcap = ctx.el('lp-cap');
    var hlT = note('t_hl', 'lp-kick'), poolT = head('t_pool');
    ctx.add(hcap, hlT, poolT);
    ctx.add(hero, ctx.el('lp-grade'), ctx.el('lp-capshade'), hcap);
    // the whole pool from above, in a glass card: its outline traced in light, its length
    // under it. The water's own life: glints along the lagoon's middle line
    var spec = ctx.hold(fx.glass('lp-spec'), 'p_plan'), plan = ctx.el('lp-plan'), ppic = ctx.picture('p_plan');
    // (owner 23 Nov, "เปลี่ยนเป็น plan นี้ ... สีโมชั่นในสระ ดีแล้วเอามาใช้กับภาพใหม่เลย": the card's plan is his
    // ground-floor plan now. The card takes the picture's own shape and the outline is laid out in it --
    // the old 1963 x 773 site plan's aspect was written here and would have stretched the new one)
    var PA = ppic && ppic.w > 0 && ppic.h > 0 ? ppic.w / ppic.h : 2.6;
    plan.style.height = C(31 / PA);
    ctx.add(plan, ppic && ppic.el);
    var glints = ctx.el('lp-glints');
    [2, 5, 8, 11, 14, 17, 20, 23, 26].forEach(function (i, n) {
      var p = CEN[i];
      if (!p) return;
      var g = ctx.el('lp-glint'), s = ctx.el('lp-gl');
      g.style.left = P(p[0]);
      g.style.top = P(p[1] + (n % 2 ? 2.4 : -2.4));
      s.style.setProperty('--d', (2.7 + (n * 0.53) % 1.7).toFixed(2) + 's');
      s.style.setProperty('--dl', (-((n * 0.77) % 3)).toFixed(2) + 's');
      ctx.add(glints, ctx.add(g, s));
    });
    var outline = fx.route(LAG, { aspect: PA, close: true, width: 0.4 });
    var flare = fx.route(LAG, { aspect: PA, close: true, fill: false, width: 0.5, cls: 'lp-flare' });
    var centre = fx.route(CEN, { aspect: PA, width: 0.16, cls: 'lp-centre' });
    var ripple = ctx.el('lp-ripple');
    ctx.add(ripple, ctx.el('lp-rip'), ctx.el('lp-rip lp-rip2'));
    // the rings spread on the water itself: a third of the way along its middle line (where they stood
    // on the old plan), not at a place written for one picture
    var rip = CEN[Math.round((CEN.length - 1) / 3)];
    if (rip) { ripple.style.left = P(rip[0]); ripple.style.top = P(rip[1]); }
    ctx.add(plan, outline.el, flare.el, centre.el, glints, ripple);
    var sfig = ctx.figure('f_len', { cls: 'lp-specfig', glow: true });
    ctx.add(spec, plan, sfig);
    ctx.add(hero, spec);

    // ---- her question, top left, over the living wall ----
    var ask = ctx.add(ctx.el('lp-asking'), ctx.hold(ctx.el('lp-titleshade'), 't_ask'), note('t_miki', 'lp-kick'), sub('t_ask', '', true));

    ctx.add(stage, sk.layer, w.layer, title, hero, ask);

    // the opening: the shot comes up with the drawing already being drawn and the camera
    // already coming closer (one long decelerating move)
    if (late) sk.finish();
    else ctx.after(0, function () {
      sk.draw();
      sk.cam.to(DRAW_ON, 5200, 'push');
      sk.grp.style.transition = 'transform 5200ms ' + ARRIVE;
    });

    // ---- what the beats do ----
    var mode = 'open', left = false, heroMs = 1400, backMs = 1700;
    var GROW = 'cubic-bezier(0.8, 0, 0.6, 1)', SHRINK = 'cubic-bezier(0.4, 0, 0.2, 1)';
    // into the floors: the drawing's camera goes in (one eased move over the floors' four words),
    // the drawing steps back behind the wall as it fills; the wall's camera glides as before
    function leaveOpening(isLate) {
      if (left) return;
      left = true;
      sk.finish();
      title.classList.add('lp-heading');
      w.drifting(0);
      sk.layer.classList.add('lp-behind');
      if (isLate) { now([sk.layer]); sk.cam.set(DRAW_DEEP); w.cam.set(WALL_END); return; }
      sk.cam.to(DRAW_DEEP, 4600, 'inout');
      sk.grp.style.transition = 'transform 4600ms ' + EASE;
      w.cam.to(WALL_END, 4400, 'inout');
      w.grp.style.transition = 'transform 4400ms ' + EASE;
    }
    // a tile rises from the band of its floor: straight up from under its place, as far along
    // the band as the band goes
    function fromBand(id) {
      return function (r) {
        var b = rectOf(sk.bands[id]);
        return { x: clamp(r.x + r.w / 2, b.x + 2, b.x + b.w - 2), y: b.y + b.h / 2 };
      };
    }
    // (the garden's pictures come from the trees at the building's right -- off the screen's edge
    // by then, the camera being well in: they come in from just past it)
    function fromGarden(r) { var g = rectOf(sk.garden); return { x: Math.min(g.x, 104) + (r.x + r.w / 2 - 85) * 0.35, y: clamp(g.y, 20, 40) }; }
    function heroAt(r) {
      hero.style.transform = 'translate3d(' + C(r.x) + ', ' + C(r.y) + ', 0) scale(' + (r.w / 100).toFixed(5) + ')';
    }
    function expand(isLate) {
      fx.depart(mainT);
      fx.depart(giantT);
      title.classList.add('lp-out');
      mode = 'hero';
      if (isLate) {
        w.cam.set(WALL_END);
        hero.style.transition = 'none';
        hero.classList.add('lp-up');
        hero.style.transform = 'none';
        hcam.set(HERO_END);
        return;
      }
      // the pool's tile lands first (its gathering), then the shot grows out of it: the same
      // picture, the same framing, so the eye stays on it the whole way. The wall gives way
      // behind it (it dims: counter-motion). (Review 19 Nov: both moves are a zoom the eye
      // reads as even -- the size changes by the same factor in each moment, slow at both
      // ends: 1.4 s out of the tile, 1.7 s back.)
      ctx.after(450, function () {
        if (mode !== 'hero') return;             // (the line went on, or ended, before it grew)
        hero.style.transition = 'none';
        heroAt(rectOf(w.p1));
        hero.classList.add('lp-up');
        void hero.getBoundingClientRect();
        hero.style.transition = 'transform ' + heroMs + 'ms ' + GROW + ', opacity 240ms ' + EASE;
        hero.style.transform = 'none';
        w.layer.classList.add('lp-behind');
        // (it grows as the tile framed it -- the whole picture -- and only then leans down to the water,
        // one long eased push: a camera inside the shot scaling while the shot itself scales could not be
        // drawn at one size, and cost a frame of 33-50 ms half way through the growth, every run)
        ctx.after(heroMs, function () {
          if (mode !== 'hero') return;
          hcam.to(HERO_END, 7000, 'inout');
          hgrp.style.transition = 'transform 7000ms ' + EASE;
        });
      });
    }
    function length(isLate) {
      if (isLate) {
        now([spec]);
        spec.classList.add('lp-in');
        outline.draw(1);
        centre.draw(1);
        glints.classList.add('lp-on');
        return;
      }
      spec.classList.add('lp-in');
      ctx.after(400, function () { glints.classList.add('lp-on'); });
      drawEased(outline, 1300, 350, inout, function () {
        flare.draw(1);
        flare.el.classList.add('lp-go');
      });
      drawEased(centre, 1200, 560, inout);
      ctx.after(700, function () { ripple.classList.add('lp-go'); });
    }
    function back(isLate) {
      if (mode === 'wall') return;
      mode = 'wall';
      fx.depart(hlT);
      fx.depart(poolT);
      spec.classList.add('lp-out');
      now([w.layer]);
      w.layer.classList.remove('lp-behind');
      if (isLate || !hero.classList.contains('lp-up')) {
        hero.style.transition = 'none';
        w.cam.set(WALL_END);
        hero.classList.remove('lp-up');
        return;
      }
      var r = rectOf(w.p1);
      hero.style.transition = 'transform ' + backMs + 'ms ' + SHRINK;
      heroAt(r);
      hcam.to(HERO_TILE, backMs, 'inout');
      hgrp.style.transition = 'transform ' + backMs + 'ms ' + EASE;
      ctx.after(backMs + 40, function () {
        hero.style.transition = 'none';
        hero.classList.remove('lp-up');
      });
    }

    hooks.beat = function (b, isLate) {
      if (b.do === 'title') {
        // (the words come with the core's arrival; the drawing goes on under them)
      } else if (b.do === 'scene') {
        if (b.to === 'bsmt') { leaveOpening(isLate); sk.basement(isLate); }
        if (b.to === 'pool') { w.gather('pool', isLate); expand(isLate); return; }
        sk.light(b.to, isLate);
        if (b.to === 'gar') w.gather('gar', isLate, fromGarden);
        else w.gather(b.to, isLate, fromBand(b.to));
      } else if (b.do === 'length') {
        length(isLate);
      } else if (b.do === 'back') {
        if (isLate) now([w.relax]);
        if (w.relax) w.relax.classList.add('lp-in');
        back(isLate);
      } else if (b.do === 'ask') {
        if (isLate) w.alive(0); else ctx.after(1600, function () { w.alive(0); });
      }
    };
    hooks.settle = function () {
      leaveOpening(true);
      sk.basement(true);
      ['bsmt', 'gym', 'cow', 'gar'].forEach(function (id) { sk.light(id, true); });
      ['bsmt', 'gym', 'cow', 'gar', 'pool'].forEach(function (id) {
        if (w.said.indexOf(w.groups[id]) < 0) w.gather(id, true);
      });
      if (w.relax) w.relax.classList.add('lp-in');
      back(true);
      w.alive(0);
    };
    // the next line (s04_l09) goes on with this wall: the same frame, its slow life as far in
    hooks.handover = function () {
      return { mode: 'wall', drift: w.driftAt ? (Date.now() - w.driftAt) / 1000 : 0, alive: w.aliveAt ? (Date.now() - w.aliveAt) / 1000 : 0 };
    };
  }

  // =====================================================================================
  // s02_l09: the entrance; then, over it, three panels gather one by one as she names what
  // the project brings together -- design, relaxing, technology -- "in a single project"
  // =====================================================================================
  function build_l09() {
    // (options.sig_cam = [from x, y, scale, to x, y, scale]: the owner's front of 22 Nov, page 4,
    // exists at 2048 px only -- its push is held near 1:1 at 1080p rather than to 1.24x)
    var SC = [50, 47, 1.08, 50, 44, 1.24].map(function (d, i) { return ctx.opt('sig_cam.' + i, d); });
    var sig = scene({ pic: 'p_sig', from: [SC[0], SC[1], SC[2]], to: [SC[3], SC[4], SC[5]], ms: 9000, words: true });
    var what = head('t_what', 'lp-wrap');
    ctx.add(sig.cap, what);
    // (a dark veil, not the layer's opacity: the stage behind would show through a faded
    // photograph -- the arc across the facade read as muddy)
    ctx.add(sig.layer, ctx.el('lp-veil'));
    ctx.add(stage, sig.layer);
    begin(sig);

    // the board: three glass panels (the lobby, the courtyard, technology), the project's name
    // under them. Each one waits for its own words; once landed its picture comes slowly closer.
    // (Owner 19 Nov, 27.png: "ทำไมขนาดไม่เหมือนอันอื่น และไม่เท่า" -- the third panel grew 14% on "Smart
    // Home" and its name was a headline beside two small labels. Now one size, one row, one kind
    // of name: the panel that is spoken of is marked by light, not by size.)
    var board = ctx.el('lp-scene lp-board');
    var pc = ctx.picture('p_cor'), pl = ctx.picture('p_lawn');
    var cardA = ctx.hold(fx.glass('lp-pnl'), 'p_cor'), cardB = ctx.hold(fx.glass('lp-pnl'), 'p_lawn');
    var cardC = ctx.hold(fx.glass('lp-pnl lp-tech'), 't_tech');
    ctx.add(cardA, ctx.add(ctx.el('lp-pnlpic'), ctx.add(ctx.el('lp-slow'), pc && pc.el)));
    ctx.add(cardB, ctx.add(ctx.el('lp-pnlpic'), ctx.add(ctx.el('lp-slow'), pl && pl.el)));
    // The technology panel (owner 20 Nov, review PDF p.6: "เอาแบบภาพรวมเทคโนโลยี มาใส่ได้เลย ใน
    // landingpage"): the landing page's technology overview -- the phone with the project's mark in
    // its globe of light -- fills the third panel like the first two, and the words go under it as
    // under them (typed, in step with her voice). Without the picture: the words in the glass, as before.
    var tpic = ctx.has('p_tech') ? ctx.picture('p_tech') : null;
    var techPane = tpic ? ctx.add(ctx.el('lp-pnlpic lp-techpic'), ctx.add(ctx.el('lp-slow'), tpic.el)) : null;
    var typed = fx.typed(ctx.text('t_tech', { split: 'chars', cls: 'lp-typed' + (tpic ? ' lp-typedc' : '') }));
    var techW = tpic ? ctx.add(ctx.el('lp-under lp-under-c'), typed.el) : null;
    // The Smart Home panel: the Smart Home app's own screen (its design export, the "Devices"
    // card -- lights, curtains, door lock, air purifier, each switched on -- and its scenes),
    // owner 19 Nov: "เปลี่ยนเป็นรูปแอพ smarthome ไหม เหมือนจะมีในไฟล์นะ". It settles from a little close
    // as the typed words leave upward, then keeps coming in slowly; a ring of light answers on
    // each device, one after another, on "control their devices".
    // options.home 'phone' (owner 20 Nov, p.7 "เปลี่ยนเป็นแบบนี้"): the app on a phone, whole in the
    // panel on the night's blue; no rings (they stood on the Devices card's switches) -- on
    // "control" a second light crosses the phone instead.
    var phone = ctx.opt('home', '') === 'phone';
    var home = ctx.picture('p_ac', phone ? { fit: 'contain' } : undefined);
    var homePane = ctx.el('lp-pnlpic lp-home' + (phone ? ' lp-phone' : '')), homeCam = ctx.el('lp-homecam'), homeNear = ctx.el('lp-homenear');
    ctx.add(homeNear, home && home.el);
    if (!phone) {
      [[16.2, 26.9], [38.9, 26.9], [61.7, 26.9], [84.6, 26.9]].forEach(function (p, i) {
        var d = ctx.el('lp-dev');
        d.style.left = p[0] + '%';
        d.style.top = p[1] + '%';
        d.style.setProperty('--i', String(i));
        ctx.add(homeNear, ctx.add(d, ctx.el('lp-devring'), ctx.el('lp-devring lp-devans')));
      });
    }
    ctx.add(homePane, ctx.add(homeCam, homeNear), ctx.el('lp-pnlsweep'), phone ? ctx.el('lp-pnlsweep lp-sweep2') : null);
    ctx.add(cardC, techPane, techW ? null : typed.el, homePane);
    var row = fx.fan([cardA, cardB, cardC], { spread: 26, lift: 3, tilt: 0, cls: 'lp-fanrow' });
    var fan3 = ctx.add(ctx.el('lp-fan3'), ctx.add(ctx.el('lp-float'), row));
    // what each of the first two panels is, under it, as she says it (gone again at "in a
    // single project": fewer words up at once)
    var futT = sub('t_fut', '', true), relA = note('t_rel_a', 'lp-kick'), relT = sub('t_rel', '', true);
    var futW = ctx.add(ctx.el('lp-under lp-under-a'), futT), relW = ctx.add(ctx.el('lp-under lp-under-b'), relA, relT);
    // the three panels' names: one size, one line, each under its own panel
    var live = note('t_live', 'lp-lbl lp-lbl-a'), care = note('t_care', 'lp-lbl lp-lbl-b');
    var smart = note('t_smart', 'lp-lbl lp-lbl-c'), ctrl = note('t_ctrl', 'lp-ctrl');
    var single = note('t_single', 'lp-kick'), giant = head('t_giant', '', true);
    var proj = ctx.add(ctx.el('lp-proj'), single, giant);
    var list = ctx.add(ctx.el('lp-list'), note('t_for', 'lp-kick'), sub('t_in', 'lp-line', true),
      sub('t_new', 'lp-line', true), sub('t_mod', 'lp-line', true));
    ctx.add(board, fan3, futW, relW, techW, live, care, smart, ctrl, proj, list);
    ctx.add(stage, board);

    function cardIn(card, isLate) {
      if (isLate) now([card]);
      card.classList.add('lp-in');
    }
    hooks.beat = function (b, isLate) {
      if (b.do === 'scene') {
        if (b.to === 'cor') {
          // the entrance steps back and dims under the board: one move, the panels gather over it
          if (isLate) now([sig.layer]);
          sig.layer.classList.add('lp-dim');
          fx.depart(what);
          cardIn(cardA, isLate);
        } else if (b.to === 'lawn') {
          cardIn(cardB, isLate);
        } else if (b.to === 'board') {
          cardIn(cardC, isLate);
          typed.play(isLate ? 0 : 2600);
          if (isLate) typed.set(1);
        }
      } else if (b.do === 'together') {
        [futT, relA, relT, techW ? typed.el : null].forEach(function (e) { if (e) fx.depart(e); });
        board.classList.add('lp-s-together');
      } else if (b.do === 'labels') {
        fx.depart(single);                     // (the sentence before's small line: fewer words up at once)
        board.classList.add('lp-s-labels');
      } else if (b.do === 'smart') {
        if (isLate) now([homePane, homeCam]);
        board.classList.add('lp-s-smart');
        cardC.classList.add('lp-showhome');
      } else if (b.do === 'words') {
        board.classList.add('lp-s-' + (String(b.key) === 't_ctrl' ? 'ctrl' : (String(b.key) === 't_new' ? 'new' : 'mod')));
      } else if (b.do === 'end') {
        [live, care, smart, ctrl].forEach(function (e) { fx.depart(e); });
        board.classList.add('lp-s-end');
      }
    };
    hooks.settle = function () {
      sig.layer.classList.add('lp-dim');
      [cardA, cardB, cardC].forEach(function (c) { c.classList.add('lp-in'); });
      typed.set(1);
      endPage();
    };
  }

  // =====================================================================================
  // s04_l09: the pool joke's dry set-up, over the same wall (owner 19 Nov: no facade -- "ให้คน
  // ดูหน้าเดิมที่โชว์ว่าแต่ละชั้นมีอะไรบ้าง"). From s04_l06 it is a join: the same drawing, the same
  // wall, its slow life as far in; only the words of that line are not here. On "I've
  // studied" the camera takes a slow, deliberate look along the floors (away from the pool's
  // corner); on "single thing" the wall leans in a hair and backs away, and an empty panel pops
  // up (Playful: the joke's own beat). Nothing of the wall comes forward: the pool's two
  // pictures stay where they are, as small as the rest.
  // =====================================================================================
  // Two forms, chosen by the slide. WITH the wall's pictures (`continues` s04_l06, options.mode
  // 'wall'): the same wall goes on, a join. WITHOUT them -- the slide as it stands: the
  // set-up's test (tests/test_ceremony_slides.py, a joke's set-up shows no picture its own line
  // does not back, and nothing of a pool) refuses the wall's pictures here -- the shot is the
  // drawing of Building A on its own, the floors she has just shown still lit, its garden and
  // its water left out (no pool in the set-up); it dissolves in from the wall.
  // (the whole building sits under her words -- its roof below them, its basement above the subtitle's
  // box -- and the look along the floors keeps it so: a slow travel to the left, never up into the words)
  // (one scale for the whole line -- the camera only travels: a drawing of ~500 strokes drawn again at
  // another size mid-move cost a 50 ms frame at 2552 px)
  // Owner 23 Nov ("ฉากนี้ ... ให้เปลี่ยนเป็นโลโก้ ตอนแรก"): options.open 'logo' -- the line OPENS on the
  // project's logo instead of the drawing: the picture `logo` (c_logo_end, the opening clip's last frame)
  // keyed on black, so it stands on the stage's own light with no box round it, as the ceremony opened.
  // Its words stay as they were; on "I've studied" the logo is looked at slowly, closer (one eased push
  // into the ring); on "single thing" it leans in a hair and backs away, dimmed, under the panel that
  // pops. It does not go to a logo page at the end any more: s04_l11 (the punchline) follows at once,
  // dissolving in from this frame -- the logo backed off, the panel with its words and its thinking dots.
  var L09_IN = { x: 53, y: 55.6, scale: 0.9 }, L09_ON = { x: 50, y: 55.6, scale: 0.9 }, L09_STUDY = { x: 44.5, y: 55.6, scale: 0.9 };
  function build_l09b() {
    var prev = ctx.prev && ctx.prev.act === 'living_photos' && ctx.prev.state && ctx.prev.state.mode === 'wall' ? ctx.prev.state : null;
    var logoOpen = ctx.opt('open', '') === 'logo' && ctx.has('logo');
    var withWall = !logoOpen && ctx.has('p_wb');
    var rig = ctx.el('lp-scene lp-rig' + (logoOpen ? ' lp-logorig' : ''));
    var sk = null;
    if (logoOpen) {
      // (the stage's horizon and floor under it, as under the opening clip; the logo arrives as the
      // line's shot dissolves in -- a slow push already under way, the stylesheet's .cs-live -- and
      // then breathes; nothing else of it moves until "I've studied")
      ctx.bg('full');
      var lpic = ctx.picture('logo', { fit: 'contain', keyed: 'black' });
      ctx.add(rig, ctx.add(ctx.el('lp-logoat'), ctx.add(ctx.el('lp-logobreathe'), lpic && lpic.el)));
    } else {
      ctx.bg('off');
      sk = sketch(true);
      sk.basement(true);
      ['bsmt', 'gym', 'cow'].forEach(function (id) { sk.light(id, true); });
    }
    if (withWall) {
      var w = makeWall(false);
      sk.cam.set(DRAW_DEEP);
      sk.light('gar', true);
      sk.layer.classList.add('lp-behind');
      now([sk.layer]);
      ['bsmt', 'gym', 'cow', 'gar', 'pool'].forEach(function (id) { w.gather(id, true); });
      w.drifting(prev ? +prev.drift || 0 : 0);
      w.alive(prev ? +prev.alive || 0 : 0);
      ctx.add(rig, sk.layer, w.layer);
    } else if (sk) {
      sk.land.classList.add('lp-off');
      sk.cam.set(late ? L09_ON : L09_IN);
      ctx.add(rig, sk.layer);
      // (the shot is travelling as it arrives: a slow pull back to the whole building)
      if (!late) ctx.after(0, function () {
        sk.cam.to(L09_ON, 4200, 'push');
        sk.grp.style.transition = 'transform 4200ms ' + ARRIVE;
      });
    }
    var cap = ctx.el('lp-cap lp-thankcap');
    var thanks = note('t_thanks', 'lp-kick lp-thanks'), studied = sub('t_studied', '', true);
    ctx.add(cap, thanks, studied);
    ctx.add(stage, rig, ctx.el('lp-titleshade lp-thankshade'), cap);
    // "I've studied this project very carefully": a slow, deliberate look along the floors --
    // closer, over the floors she named (the wall: towards the basement and the ground floor; the
    // pool's corner, bottom right, goes a little further off)
    function study(isLate) {
      if (logoOpen) {
        rig.style.transition = isLate ? 'none' : 'transform 3800ms ' + EASE;
        rig.style.transform = 'translate3d(' + C(-0.8) + ', ' + C(0.6) + ', 0) scale(1.07)';
        return;
      }
      if (!withWall) {
        if (isLate) { sk.cam.set(L09_STUDY); return; }
        sk.cam.to(L09_STUDY, 3800, 'inout');
        sk.grp.style.transition = 'transform 3800ms ' + EASE;
        return;
      }
      rig.style.transition = isLate ? 'none' : 'transform 3800ms ' + EASE;
      rig.style.transform = 'translate3d(' + C(2.2) + ', ' + C(0.9) + ', 0) scale(1.07)';
    }

    var panel = fx.glass('lp-panel'), dots = ctx.el('lp-dots');
    var typed = fx.typed(ctx.text('t_single', { split: 'chars', cls: 'lp-typed2' }));
    ctx.add(dots, ctx.el('lp-dot'), ctx.el('lp-dot'), ctx.el('lp-dot'));
    ctx.add(panel, typed.el, dots);
    ctx.add(stage, panel);

    // The set-up's timing (Playful: the joke's own beats): on "single thing" the wall leans in a
    // hair and then backs away (an ease-in-back: the wind-up is in the curve), a beat of nothing,
    // and the panel POPS up with an overshoot. The three dots then wait like someone thinking --
    // a wave, not water: the set-up never hints at the pool.
    function back(isLate) {
      rig.style.transition = isLate ? 'none' : 'transform .9s cubic-bezier(.5, -.35, .25, 1), opacity .9s ' + EASE;
      rig.style.transform = 'scale(.9)';
      rig.style.opacity = '.22';
      panel.classList.add('lp-up');
      fx.depart(thanks);
      fx.depart(studied);
    }
    hooks.beat = function (b, isLate) {
      if (b.do === 'sweep') {
        study(isLate);
      } else if (b.do === 'panel') {
        back(isLate);
        if (isLate) { typed.set(1); return; }
        ctx.after(380, function () { typed.play(2400); });
      }
    };
    hooks.settle = function () { back(true); typed.set(1); endPage(); };
  }

  // the line is over: the last frame holds a moment (her last words read), then the line's layer
  // fades and the logo page comes up -- the logo out of the depth as the opening brought it, then it
  // breathes (a held page is never a freeze). A screen that joined late is shown the logo page at once.
  var ended = false;
  function endPage() {
    if (!endLogo || ended) return;
    ended = true;
    var go = function (isLate) {
      if (isLate) now([stage, endLogo]);
      ctx.root.classList.add('lp-ending');
      ctx.bg('full');
    };
    if (late) go(true); else ctx.after(1800, function () { go(false); });
  }

  if (cue === 's04_l06') build_l06();
  else if (cue === 's02_l09') build_l09();
  else if (cue === 's04_l09') build_l09b();
  if (endLogo) ctx.add(ctx.stage, endLogo);

  return {
    beat: function (b, isLate) { hooks.beat(b, isLate); },
    progress: function (f) { hooks.progress(f); },
    settle: function () { hooks.settle(); },
    clear: function () { hooks.clear(); },
    handover: function () { return hooks.handover(); }
  };
});
