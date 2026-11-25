/* The worked example of an act (docs/emma-ceremony-slides.md, section 0): a picture in a
   glass panel, the line typed beside it in step with her voice, and a figure that lands on
   its beat while the camera pushes in. Copy the SHAPE of this file, not its look.

   Everything on the screen comes from the slide (data/ceremony/slides/s04_l11.json) through
   ctx: the words via ctx.text / ctx.figure, the picture via ctx.picture, the numbers that
   place things via ctx.opt. Nothing is written here that the hall could read. */
CeremonyShow.register('example_panel', function (ctx) {
  var fx = ctx.fx;
  ctx.bg('dim');                                   // the shared stage, quieter behind a panel

  // ---- the picture: in a glass panel, inside a layer the camera moves ----
  var scene = ctx.el('scene');
  var frame = fx.glass('frame');
  var pic = ctx.picture('pool');                   // {el, w, h, url, depth, caption, ...} or null
  var view = fx.parallax(pic, { amount: 1.4 });    // depth map + WebGL when there are both; a drift otherwise
  view.drift();
  ctx.add(frame, view.el);
  // where the panel sits: numbers of the slide's `options`, used as CSS variables
  frame.style.setProperty('--x', String(ctx.opt('panel.x', 46)));
  frame.style.setProperty('--y', String(ctx.opt('panel.y', 5)));
  frame.style.setProperty('--w', String(ctx.opt('panel.w', 49)));
  frame.style.setProperty('--tilt', String(ctx.opt('tilt', -5)));
  ctx.add(scene, frame);
  var cam = fx.camera(scene);

  // ---- the words, beside it ----
  var words = ctx.el('words');
  var line = fx.typed(ctx.text('line', { split: 'chars', cls: 'cs-type-h2' }));
  var figure = ctx.figure('length', { glow: true });     // hidden by the core until its beat
  ctx.add(words, ctx.has('kicker') ? ctx.text('kicker', { cls: 'cs-type-label' }) : null, line.el, figure);
  ctx.add(ctx.stage, scene, words);

  return {
    // once per beat of the slide, when her voice reaches it (late = no entrance, just the state)
    beat: function (b, late) {
      if (b.do !== 'figure') return;
      if (figure) figure.classList.add('in');
      cam.to({ x: ctx.opt('focus.x', 53), y: ctx.opt('focus.y', 47), scale: ctx.opt('push', 1.1) }, late ? 0 : 1800, 'push');
    },
    // her voice is at f (0..1) of the line: the only clock. The line is typed as she says it.
    progress: function (f) { line.set(f * 1.15); },
    // the line is over: a calm frame that can stay
    settle: function () { line.set(1); }
  };
});
