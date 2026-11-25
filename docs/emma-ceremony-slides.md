# Authoring a ceremony slide (the event screen, 16:9)

For whoever writes one line's slide. Owner, 17 Nov 2026: *"มันเป็นสไลด์ที่มีแค่ตัวหนังสือ อยากจะให้ทำเป็น
วิดีโอ โชว์รูปต่างๆให้คนเห็นภาพ ... พวกโมเดลกับภาพเราก็มีแล้ว ... ให้เอเจนท์แยกทำแต่ละหน้าเลย"* — every line
Emma says in the opening ceremony should feel like **a short video of the project**: real pictures
(renders, the model, plans, maps) moving in step with her voice, a few English words as a supporting
layer, and the line's Thai subtitle in the band at the bottom (not yours to draw).

You write **one file**: `data/ceremony/slides/<cue id>.json`. Nothing else. Do not edit
`floor1_ceremony.json` (the script), `floor1_ceremony.slides.json` (the base file — your file lies
over its slide of the same cue), `slide_assets.json`, the engine, or any `approved_*` field.

## 0. Acts — how the film is built (read this first)

> **Status, 17 Nov evening.** This section is the contract the part designers build on. Sections
> 1–7 describe the older fixed templates, which stay for now — designers replace them cue by cue.
>
> LANDED, tested, and looked at in the real page (the mirror listener, headless Chromium): the act
> loader (`register`, on-demand + preload), `ctx` and all its factories, the gate (`key` = hidden
> until its beat), the generic validator, hi-res pictures and depth maps from
> `data/ceremony/assets/index.json`, `/ceremony-asset/<sha>`, every primitive of 0.4 (one throwaway
> act drew them all; the parallax with WebGL on this PC's GPU), the worked example. See the end of
> 0.7 for what is not done.

Owner, 17 Nov: *"แยกเอเจนท์สำหรับออกแบบแต่ละส่วน แล้วก็ให้ทำ ลองทำมาแล้วค่อยปรับ"*. Each part's designer
writes an **act**: one JavaScript file (+ optional CSS) that draws and moves its own piece of film.
The **core** owns everything that must be the same for all, or must be checked: the stage and its
look, the words / numbers / pictures that may reach the screen, the clock (her voice), the files.

### 0.1 The three files you write (all yours alone — no shared file to edit)

| file | what |
|---|---|
| `client/assets/ceremony-show/acts/<name>.js` | the act. `<name>`: lower-case letters, digits, `_`; starts with a letter; 2–32 long |
| `client/assets/ceremony-show/acts/<name>.css` | optional. Every selector starts with `.cs-act-<name>`; every `@keyframes` name with `<name>-` |
| `data/ceremony/slides/<cue id>.json` | one slide per cue: which act, and ALL the content |

There is no list to add your act to: the server lists the folder (`/ceremony-acts.json`), the engine
preloads every act when the page opens and loads one on demand when a slide names it. There is no
cache tag to bump: an act's address carries a tag of its own bytes. One act may serve many cues.

### 0.2 The slide — one content model for every act

The validator checks this with no per-act code, so the shape is fixed
(this one is `data/ceremony/slides/s04_l11.json`):

```json
{
  "template": "act",
  "act": "example_panel",
  "texts": {"kicker": "Embassy World", "line": "Swimming in the pool myself!"},
  "figures": {"length": {"value": "155", "unit": "metre pool"}},
  "pictures": {"pool": {"asset": "lagoon_scene58"}},
  "beats": [
    {"at_en": "155-metre", "do": "figure", "key": "length"}
  ],
  "options": {"panel": {"x": 46, "y": 5, "w": 49}, "tilt": -5, "push": 1.12, "focus": {"x": 53, "y": 47}}
}
```

- **`texts`** `{key: "words"}` — every string obeys section 1 (words of THIS cue's English line,
  numbers as the line writes them and beside their own word, the owner's qualifiers, characters,
  never price / promotion / rooftop / concept …). At most 90 characters each.
- **`figures`** `{key: {"lead"?, "value", "unit"?, "label"?}}` — ONE figure is one number with its
  own words: `lead value` and `value unit` must each be a run of the line (`about 600`,
  `600 metres`, `floor 3`); it needs a `unit` or a `lead` (never a bare number); the owner's
  qualifiers must be inside the same figure (`about` … `straight line`; `around` … `estimate`;
  `about` 23); a `label`'s words come from the sentence the number is said in. A figure is drawn
  whole and **never counts up**.
- **`pictures`** `{key: {"asset": "<id>", "caption"?, "fit"?: "cover"|"contain"}}` — section 2's
  rules: the picture is backed by the **sentence its beat is in** (or, with no beat, by the line,
  or is `establishing`); a caption contains one of its picture's `about` phrases.
- **`videos`** `{key: {"asset": "<id>"}}` — a clip from the assets index (0.6). Always muted; it
  plays on its own time and is never the clock.
- **`beats`** `[{"at_en": "<piece of the English line>" | "at": 0–1, "do": "<your verb>",
  "key"?: "<key>" | ["<key>", …], …your own numbers / booleans / short ids}]`, in the line's order
  (`at_en` = the first place that piece occurs: pick a piece that is unique).
  - **`key` means "this comes on the screen here".** The engine keeps everything a factory made
    for that key invisible until its first such beat, and the validator checks the thing against
    the sentence that beat is in. A text, figure or caption may not use a word or number she has
    not reached: its words must be in the line **up to the end of the sentence its beat is in**
    (`words_before_she_says_them:<key>:<word>`) — a joke's punchline cannot be on the screen
    during its set-up.
  - A key no beat brings in is **opening** content: on the screen before her first sound. An
    opening text/figure must be made of words of the line's **first sentence** (when the line
    opens with "Of course." put your title on a beat at the next sentence's first words); an
    opening picture is backed by the line or is `establishing`.
  - To point at something already on the screen (push the camera into picture `pool`), use any
    other field name (`"to": "pool"`): only `key` hides and checks.
- **`options`** — numbers, booleans, short ids (`^[a-z][a-z0-9_]{0,31}$`), and arrays / objects of
  those, at most 5 deep and 6,000 characters as JSON. Coordinates live here. **No sentence can live
  in options** (a string with a space or a capital is refused), and nothing in options or beats can
  reach the screen as words (0.5). A beat's own fields: the same values, at most 2 deep.
- `at_en` is found whatever its case ("a real highlight" is the line's "A real highlight").
- Keys: `^[a-z][a-z0-9_]{0,31}$`, unique across texts, figures, pictures and videos. At most 24
  texts, 12 figures, 16 pictures, 4 videos, 40 beats.

### 0.3 The act file

The shape (the whole worked example is `client/assets/ceremony-show/acts/example_panel.js`):

```js
CeremonyShow.register('example_panel', function (ctx) {
  var fx = ctx.fx;
  var scene = ctx.el('scene'), frame = fx.glass('frame');   // a layer for the camera, a glass panel
  var view = fx.parallax(ctx.picture('pool'));              // a picture: depth + WebGL, or a drift
  view.drift();
  ctx.add(scene, ctx.add(frame, view.el));
  var cam = fx.camera(scene);
  var line = fx.typed(ctx.text('line', { split: 'chars', cls: 'cs-type-h2' }));
  var figure = ctx.figure('length', { glow: true });        // hidden by the core until its beat
  ctx.add(ctx.stage, scene, line.el, figure);
  return {
    beat: function (b, late) {                               // once per beat, when her voice reaches it
      if (b.do === 'figure') { figure.classList.add('in'); cam.to({ scale: ctx.opt('push', 1.1) }, late ? 0 : 1800, 'push'); }
    },
    progress: function (f) { line.set(f * 1.15); },          // her voice is at f (0..1): the only clock
    settle: function () { line.set(1); },                    // the line is over: a calm frame that can stay
    clear: function () {},                                   // going down: release what the core does not know about
    handover: function () { return { view: cam.view() }; }   // what the NEXT line's act gets as ctx.prev.state
  };
});
```

- The factory runs once per line, with your stage box already in the page. Build the **opening**
  state in it and return the handlers (all five optional).
- `beat(b, late)` — once per beat, in order, when her voice reaches it. `b` is the beat as you
  wrote it, with `at` resolved to 0..1. Things made for `b.key` have JUST become visible: animate
  them in. `late === true` → no entrance, jump to the state (a screen that joined mid-line, your
  file arriving after her voice began, or the line ending before this beat fired).
- `progress(f)` — the only clock: where her voice is, 0..1 of the line, **every animation frame**
  (about 7 times a second in a hidden tab). Never time WHEN something appears with a timer.
- **Line to line.** A new line builds a new instance over the old one (the old root fades out in
  0.5 s underneath). `ctx.prev` is `null`, or `{act, slide, settled, state}` of the act slide that
  was up — `state` = what its `handover()` returned. Build your opening from it and two cues of a
  part join without a cut (same camera view, same cards up): the frames are identical, so nothing
  is seen to change.
- The root (`.cs-act-<name>`) gets `.cs-live` one frame after the opening is built (at once when
  late): hang the opening's entrance **transitions** on `.cs-act-<name>.cs-live …`. While late
  (`.cs-instant`, ~0.4 s) every transition under the root is switched off; a keyframe entrance must
  check `late` itself.
- The root gets `.cs-out` when its slide is retired, on every way out (`retire()`): an act may hang its
  exit on `.cs-act-<name>.cs-out …` (units_views does: the line going out takes its title and caption away
  in 0.2 s, so the next line's title never lands on it — 22 Nov). A core change to `retire()` must keep it.
- A factory returns `null` when the slide has no such key (`ctx.add` skips `null`; `ctx.has(key)`
  asks first) — so one act can serve cues with and without a figure.
- An act that throws — in the factory, a beat, `progress`, a frame — is taken down to the plain
  stage (the subtitle still shows). An act file that fails to load: the same.

**`ctx`**

| member | gives |
|---|---|
| `ctx.stage` | your 16:9 box: 100cu × 56.25cu from the top of the screen, `overflow: hidden`. `--cu` = 1% of its width. The Thai subtitle band covers its bottom 22% (below `43.8cu`): nothing that matters goes there |
| `ctx.root` | the act's slide root (`.cs-act-<name>`): put classes for the whole act's state here (never reach it as `stage.parentNode` — the scan refuses that) |
| `ctx.slide` | the slide as the server sent it |
| `ctx.late` | `true`: build the opening with no entrance |
| `ctx.prev` | the act slide that was up when this line came: `{act, slide, settled, state}` or `null` |
| `ctx.has(key)` | does this slide have that key |
| `ctx.text(key, {split, cls})` | `span.cs-text` holding `texts[key]`. `split: 'words'` → a `span.cs-tw` per word; `'chars'` → a `span.cs-tc` per letter (inside its word's `cs-tw`); each with `--i` (its index), the element with `--n` (the count), for staggered CSS |
| `ctx.figure(key, {glow, cls})` | `div.cs-fig` = `.cs-fig-row` (`.cs-fig-lead` `.cs-fig-value` `.cs-fig-unit`) + `.cs-fig-label`, one piece. Its size is the `font-size` of `.cs-fig` (the numeral is 1em). `glow: true` → the glowing numeral |
| `ctx.picture(key, {fit, keyed})` | `{el, img, url, w, h, depth, alpha, fit, caption}` — `el` (`span.cs-apic`) fills the box you put it in; the hi-res file when the index has one; clear until decoded, then in. Size, move and fade `el` freely. `keyed: 'black'` = a model photographed on black, keyed to alpha by the core (no `mix-blend-mode` of your own). A map from OpenStreetMap brings its credit: the core draws it at the stage's corner whenever the picture shows — you cannot and must not draw or hide it. `depth` = depth-map url or `null`; `caption` = an element (`span.cs-acap`) or `null` |
| `ctx.video(key)` | `{el, w, h, ready(), play(), pause(), seek(s), rate(k), time(), duration(), ended()}` — always muted, not looping, starts paused; it holds its last frame when it ends |
| `ctx.opt('a.b.0', fallback)` | a value of `options` by path; `fallback` when it is missing |
| `ctx.el(cls, key?)` | an empty `div`; with a `key`, a wrapper hidden with that key until its beat (a card, a frame round a keyed thing) |
| `ctx.hold(el, key)` | makes `el` wait with `key` the same way |
| `ctx.svg(tag, attrs)` | an SVG shape (`svg g path circle ellipse rect line polyline polygon defs linearGradient radialGradient stop clipPath mask`; `cls` = its class) — never `text` |
| `ctx.add(parent, …children)` | appends (elements only, skips `null`) and returns `parent` |
| `ctx.after(ms, fn)` / `ctx.frame(fn)` | a timer / a per-frame callback `fn(ms, dt)` (returns its `stop()`) that dies with the slide. For polish after a beat — never for WHEN something appears |
| `ctx.bg('full' \| 'dim' \| 'off')` | the shared stage background behind your box (`off` under a full-bleed picture: it is hidden and paused) |
| `ctx.light(level, ms)` | the stage's light, 0..1: the shared background, the glow of every glass panel of this act, and anything you give the class `cs-lit`, follow it ("lights off" is a cue). Every line starts at 1 |
| `ctx.fx` | the shared primitives (0.4) |

### 0.4 The shared look — tokens and primitives

Tokens (CSS variables on the stage; use them, do not restate colours):
`--cu`; `--cs-night` #03050d, `--cs-night2` #070b1c; `--cs-blue` 47,107,255 · `--cs-sky` 77,163,255 ·
`--cs-cyan` 110,231,255 (use as `rgba(var(--cs-blue), .5)`); `--cs-ink` #f4f7ff, `--cs-ink2`
rgba(220,232,255,.72); `--cs-glass` rgba(18,34,96,.34), `--cs-glass-edge` rgba(130,175,255,.75),
`--cs-radius` 1.2cu. Type: the stage's system sans; classes for the `cls` option: `cs-type-h1`
(5.2cu/800), `cs-type-h2` (3.4cu/700), `cs-type-label` (1.5cu, wide tracking, caps, cyan),
`cs-type-scene` (bold italic caps — words standing in a picture), `cs-type-giant` (15cu, lit ink and
a halo: the giant word behind a panel — set your own `font-size`).

| primitive | what it is |
|---|---|
| stage background | horizon arc, beam, perspective grid (no ghost panels since the owner's review of 20 Nov) — always behind your box; `ctx.bg(mode)` |
| `fx.glass(cls)` | a glass panel: fill, 1px edge, a glow drawn with gradients (no blur). Size and place it yourself (`--g` = glow width, `--r` = radius). A picture inside takes its rounded corners |
| `fx.console(cls)` → `{el, body}` | a glass panel with the three-dot bar; build into `body` |
| `fx.sat(child, cls)` | a satellite card: a small glass card around `child` |
| `fx.sat(child, cls)` wraps; so does `fx.parallax` | what wraps a keyed thing is hidden with it until its beat (no empty card, no picture drawn early) |
| `fx.fan(cards, {spread, tilt, lift, cls})` | lays `cards` out as a fan of tilted cards (outer ones turned in and set back); returns the row element |
| `fx.wheel(cards, {radius, step, at, cls})` → `{el, slots, turn(i, ms), at()}` | cards round a cylinder (`radius` in cu, `step` in degrees: 360/n by default), turned by one transform; the card at the front has the class `cs-front`, the ones facing away are not drawn. Give `el` a size |
| `fx.wipe(child, {dir, from, edge})` → `{el, set(f), to(f, ms, ease), at()}` | a wipe by transform alone (no clip-path): `child` is revealed from one side (`dir`: right/left/down/up) with a line of light at the cut. Lay `el` over the picture it replaces — a scan from a hologram to its model |
| `ctx.figure(key, {glow: true})` | the glowing numeral (lit ink + a drawn halo) |
| `fx.plane(parent, child, quad, {w, h, cls})` | `child` standing IN the picture: its `w`×`h` (cu) box mapped onto `quad` = four corners `[[x,y]×4]` (TL, TR, BR, BL) in % of `parent` (CSS matrix3d). `parent` = the positioned layer the picture is in (a camera group is), so the camera moves both |
| `fx.route(points, {aspect, close, width, cls})` → `{el, paths, draw(f), play(ms)}` | a glowing polyline over a picture; `points` in % of the picture, `aspect` = its w/h, `width` in % of its width, `close` = an outline (filled when complete). `draw(f)` shows the first `f` (tie it to `progress`), `play(ms)` runs it once. Put `el` in the picture's layer |
| `fx.arc(a, b, {bend, aspect, width})` | the same, a curved line from `a` to `b` |
| `fx.camera(group)` → `{set(v), to(v, ms, ease), push(k, ms), pull(k, ms), pan(dx, dy, ms), drift(ms, k), view()}` | moves a layer group (it is made to fill the stage). `v` = `{x, y, scale, rotate}` — `x,y` = the point of the group, in %, that sits at the stage's centre — or `{rect: [x, y, w, h]}` (zoom until that rectangle of the group fills the stage; x and w in % of its width, y and h of its height — a 16:9 rectangle has `w` = `h`). `ease`: `move`, `inout`, `push`, `linear` |
| `fx.portal(cam, group, rect, child, {shape, show, cls})` → `{el, open(ms, landed)}` | the portal: picture B in a rectangle (or a `shape` polygon in % of it) of picture A, hidden until it opens; `open` pushes the camera into it until B fills the stage ("cover"), then calls `landed()` — cut there to B's own scene framed the same way and nothing is seen to change. `child` keyed to B's beat: B appears exactly as she names it |
| `fx.inset(group, rect, child)` + `cam.to({rect}, ms)` | the same by hand: `child` in a rectangle of the group |
| `fx.whip(outEl, inEl, {dir, ms})` | a whip: one layer shoots out, the next shoots in, streaks between (`dir`: left/right/up/down). Whip layers that have no transform of their own; build the incoming layer with the class `cs-whipped` (hidden until its whip) |
| `fx.typed(textEl)` → `{el, set(f), play(ms)}` | typed text with a caret; `textEl` = `ctx.text(key, {split: 'chars'})`. `set(f)` from `progress` types in step with her voice. A text that is a piece of her line (the server sends its place in the line, `said`) never shows a letter before her voice reaches it — `play(ms)` too: it types at most as fast as `ms` and never ahead of her (a test holds every beat for seconds of act time) |
| `fx.parallax(pic, {amount, zoom, focus})` → `{el, set(x, y), drift(on), gl()}` | a picture with depth: a WebGL quad displaced by the depth map when `pic.depth` exists and WebGL works; otherwise the same picture moved by transform. Never blank: the plain picture is always under it. `set(x, y)` = the view's shift, −1..1; `drift()` = a slow wander. At most 4 WebGL pictures in one slide (a page has few contexts): the rest move plain |

Motion rules (tested on your files): only `transform` and `opacity` animate (a route's
`stroke-dashoffset` is the one exception); no `blur()` / `drop-shadow()`, no blurred `box-shadow` or
`text-shadow`, no `backdrop-filter`, no `will-change`; sizes in `cu`, `%` or `em` (no `px` beyond a
1px hairline, no `vw`/`vh`). Nothing blinks. A rig of your own (a 3D world, a document being built,
confetti) is your act's CSS and `style.transform` — `perspective` on a wrapper, `translate3d` /
`rotate` in `calc(N * var(--cu))`; hide things with `opacity`, not `visibility` (a child that sets
it back shows through a hidden parent).

**What costs on the event notebook (integrated GPU, measured 23 Nov — held stages at 40–47 fps):** a
`mask-image` or `clip-path` on a layer that MOVES is an offscreen pass every frame — put the mask on a
still child and move the parent (the core's living stage does this); a mask on a layer that SCALES is
drawn again at every new scale; a `mix-blend-mode` makes its whole stacking context an offscreen pass
even while the blended thing is clear (the core's logo clip: its wrapper is `visibility: hidden` once
its own opacity fade is over — the core's, not an act's way: acts keep hiding with opacity); a 2D transform written
from `ctx.frame` on an element that is not its own layer repaints its whole layer every frame (add
`translateZ(0)` to the transform); a stroke drawn along its length (`stroke-dashoffset`) repaints the
whole box it lies in, every frame of the draw; two animations of the same property on one element at
once (a push `forwards` + a delayed breath, a bloom + its life) cannot run on the compositor — the
layer is rastered again every frame (emma_console's photo, ~470 tiles a second): put the second on a
wrapper inside. Under a full-bleed picture or map, `ctx.bg('off')`.

### 0.5 What an act file may not do (a test reads your source)

Words reach the screen only through `ctx.text`, `ctx.figure` and a picture's `caption`; pictures
and clips only through `ctx.picture` / `ctx.video`. So in `acts/*.js`: no `textContent`,
`innerText`, `innerHTML`, `outerHTML`, `insertAdjacent…`, `createTextNode`, `document.write`,
`.append(` / `.prepend(` / `.before(` / `.after(` / `.replaceWith(` / `.replaceChildren(` (use
`ctx.add` or `appendChild`), `fillText` / `strokeText`, `createElementNS` (use `ctx.svg`);
`createElement` only of `div`, `span`, `i`, `b`, `em`, `section`; no `parentNode`, `parentElement`,
`closest`, `querySelector…`, `children`, `firstChild`/`lastChild`/siblings, `ownerDocument`,
`documentElement`, `document.body` (your box, `ctx.root` and the factories' handles are all you
need); no `cs-credit`; no `new Image`, `.src`, `.href`,
`url(`, `fetch`, `XMLHttpRequest`, `WebSocket`, `import(`, `eval`, `new Function`; no `setTimeout`,
`setInterval`, `requestAnimationFrame` (use `ctx.after` / `ctx.frame`); no `className`,
`setAttribute('class'|'style', …)`, `cssText` (use `classList` and the `cls` option), no
`important`, no `cs-await`, no `cs-fig-lead/-value/-unit/-label`; no `px`/`vw`/`vh` sizes.
In `acts/*.css`: no `content:` other than `""` / `none`, no `attr(`, `url(`, `counter`, `@import`,
`@font-face`, `!important`; only `@keyframes` at-rules; no rule may hide a piece of a figure
(`.cs-fig-…` with `display: none`, `visibility: hidden`, `opacity: 0`, `font-size: 0`, `clip…`,
`color: transparent`); selectors and keyframes prefixed as in 0.1. Inline SVG shapes are fine.
The same test also RUNS every act with every slide that names it in a real browser page and
fails when any text on the screen is not one of the slide's checked strings, or when something
made for a beat's `key` can be seen before that beat.

**Name your parts with classes the mirror page does not style** (23 Nov): your stylesheet is scoped
under `.cs-act-<name>`, the page's own (`client/voice-preview.html`, `emma-story.css`) is not.
smarthome_brand's full-screen picture was a `.hero`, and the page's `html.mirror .hero { position:
fixed; inset: 0 }` took it -- on a screen wider than 16:9 the picture hung from the screen's left edge
and left bare stage on the right. `tests/test_ceremony_brand.py` checks every act's classes against
the page's rules.

### 0.6 Pictures, hi-res, depth maps, clips

A slide names a picture by **id**:

- a catalogue id (section 2). When `data/ceremony/assets/index.json` has an entry whose `from_pin`
  is that id, `ctx.picture` gives the hi-res file — and its depth map (the `soft` one when the
  index has it) — automatically; the catalogue's copy is the fallback if the big file fails;
- **where a picture may stand:** on a beat, backed by the sentence its beat is in — and an
  `establishing` picture (the project seen whole: the buildings, the campus model, the entrance
  court, the garden between the buildings; never a picture of the pool) also wherever that
  sentence names the project ("Embassy World", "our project", "this project"); in the opening,
  backed by the line or `establishing`. The buildings' pictures are `about` "units" and "buildings";
- **a shot held over from the line before (18 Nov, `continues`):** a slide may carry
  `"continues": "<cue id>"` (a top-level field of the slide; the page is not sent it). Its
  pictures and clips may then be backed by the WHOLE line of that cue as well as by its own
  words — the owner's call on s04_l13 / s04_l15, which go on in the pool of "Swimming in the
  155-metre pool myself!" without saying "pool" themselves. Narrow on purpose: the cue named is
  the step directly before this one in its scene of the running order (nothing between them), its
  slide is of the same act, and a chain goes at most 3 lines back. Nothing else is loosened.
  Refusals: `bad_continues`, `continues_not_the_line_before:<id>`, `continues_another_act:<id>`.
  Use it only where the owner has asked for a shot to go on — it is not a way to keep a picture
  the line does not speak of;
- an index id that is a **candidate** (`candidate: true`) — usable only when the index marks it
  `ok: true` with a `checked` note and describes it (`shows`, `about`, optional `establishing`).
  Unchecked → `asset_not_checked`. A clip is a candidate whose file is `.mp4` / `.webm`.
  Every candidate also carries `people`: where a real person is in it (boxes `[left, top, right,
  bottom]`, fractions of the file) or `[]` for "looked, nobody". No `people` → `people_not_checked`
  (the gym renders, whose glass ceilings carry photographic swimmers, are refused this way);
- **real people (18 Nov):** the owner's renders carry photo cut-outs of real people (the egg
  pavilion, the entrance, the pool's edge, the gym ceiling). Each one found is a box in the index
  (`people` on a candidate, `keep_out` on a catalogue picture's full-size row, both from
  `asset_candidates.json`). The core paints every box over in the colour round it wherever the
  picture shows (and never gives such a picture to WebGL), and the acts' run in Chromium checks
  every beat, landed: a box on the stage must be painted over and stand no taller than 96 px at
  1080 p — a camera may not push in on a person. Frame past them (`lag_cam`, a scene's from/to)
  or choose another picture. A box list is what was found (detector + eyes), not a proof that
  nobody else is there: look at your frames.
  **Exception, the owner's final renders of 18 Nov** (`.tmp/ceremony-show/sources-1118/`): "ตัวหนังสือฝัง
  ในภาพ มีคนในภาพ ไม่เป็นไร" — their rendered (not photographed) figures and the small burned-in
  "AI-generated … / PRELIMINARY CONCEPT" stamps are accepted for those files: `people: []` and the
  owner's words in `checked`, nothing painted;
- **an entry registered by hand** (a clip rendered by `scripts/render_emma_*.py`, a sheet or crop an
  act designer made — not built by `scripts/build_ceremony_hires.py` from a pin or from
  `asset_candidates.json`) carries **`hand`**: who made it, how. The builder keeps such entries; an
  unmarked one is removed by its next full run (and `test_every_ceremony_index_entry_is_built_or_
  marked_by_hand` fails first). A hand entry withdrawn is taken out of the index by hand;
- **a clip with alpha (VP9 `yuva420p`) is decoded on the CPU** — Chrome has no hardware path for its
  alpha plane (23 Nov, the event notebook: the whole-frame Emma clips dropped 35–43% of their frames;
  an H.264 clip without alpha decodes on the graphics chip and costs little). Cut such a clip to the
  part of its frame its subject is ever in: `scripts/cut_emma_clips.py` makes the cut from the
  lossless render frames and pins it with `window` [x, y, w, h, W, H], `cut_of` and `cut_of_sha256`
  (a re-render of the original then fails the check until the cut is made again); the act lays the
  file at that window of the box the whole frame filled (emma_dive / emma_console read the slide's
  `window` option, which a test holds equal to the row's). The film names the cuts, never the
  originals; `*_crop30` are 30 fps fallbacks no slide names.

Every file is pinned by sha256 and served by the screen's own listener at
`/ceremony-asset/<sha256>` (behind its cookie, like story pictures); the slides' signature covers
each file a slide names (picture, hi-res, depth, clip) and the act's own two files. You never
write a path or a URL.

### 0.7 Try it

```
python scripts/approve_ceremony_slides.py --status                 # your cue must say "ok"
python scripts/preview_ceremony_show.py --only <cue id> --shots .tmp/ceremony-show/shots --shots-every 1.5
python scripts/preview_ceremony_show.py --only <cue id> --shots .tmp/ceremony-show/shots --webgl   # GPU page: look at parallax
python -m pytest tests/test_ceremony_acts.py -q -p no:cacheprovider --basetemp <a temp folder>   # contract + source scan + the browser run
```

The ceremony OPENS on the project's own logo clip (`c_logo_reveal`, the owner's 6.7 s clip of 18 Nov,
`IDLE_CLIP`) over the stage's floor and horizon — muted, once per load of the screen, then it holds
its last frame — the core's, only in the ceremony's show mode. **It is never an interstitial**
(owner 18 Nov night: "ตอนรอระหว่างบทไม่ต้องมีเลย ... ไม่อยากให้มีคั่น อยากให้มันสมูทไปเลย"): a line
that ends settles and stays, through Khun Miki's question and across scene boundaries, until the
next line's slide comes in FROM it (your act's opening must read well from any act's held frame).
The screen is idle again only after a stop or a call ending; then the clip's last frame shows, still.

**The debut on card 1 (owner 23 Nov: "...เมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มีซาวน์ แล้วหุ่นก็เปิดตัว", "แนวนอน.mp4
เพิ่มในตอนเปิดตัว", then after listening "เอาเวลานับออก" — no countdown)** is the core's too (`revealStart`,
`cs-dbt-*`; the server side is `app/ceremony_reveal.py`: `ready / start / impact_at / abort / cancelled /
running`). Card 1's press: the music's build (~5.3 s from a bar line, no digits, no counting voice), a
concert's light pulsing on its beats and rising; at 0 everything black and silent (1.5 s); on the hit a
flash, rays, the logo clip bursting in lit (started `CLIP_PEAK_S` before the hit, hidden until it) and the
owner's glitter wave rising behind it (muted); when the wave has drained the dark lifts and the clip
holds its last frame — the frame s02_l03 comes in from. Its times are data: `data/ceremony/reveal.json`
names the sound and the video, the sound is ONE mix pinned in the asset index with `beats`, `numbers`
(empty — the engine still draws a counting mix's numbers), `zero_s`, `impact_s` measured on the file
(`scripts/ceremony_reveal_sound.py mix`, `video`) — swapping a sound or the video is that script, nothing
in code. Without a usable sound it builds silent on reveal.json's times; without the video, the logo comes
alone. Stops — the page and the
server's run on exactly the same ones (review 23 Nov): `abort()`, a second start, the red stop (a line's
own end, even cut short, is not one), the call over, show mode over, a slide coming up. Never an
`interrupted` (the counting voice in the hall may be heard as one while card 1 rests the arm) and never a
screen's socket blinking (the page plays on; the snapshot stops it when it is back, if the debut is over).
The clock: the page's own from the message, the sound followed within 0.5 s; a sound late is sent on to
where the page is, a stalled or never-started one is paused and given up (by 0 at the latest) — it ends
by itself every way. The debut goes to every connected screen in show mode — **a screen is never refused
for what it is** (re-review: a 1920 TV at 200% scaling read 960 px and was). Screens say what they are
(`/ws/mirror?sound=&w=&h=&coarse=`, size in device pixels, again after turning landscape) and only
warnings come of it (`warnings()` lists them all, `ready()` gives the first): `small_screen_only` (no
screen that plays aloud or is landscape and not touch-first), `no_sound_screen`, `two_sound_screens`,
`not_listened` (`ceremony_reveal_sound.py listened --by`, pinned to the mix's bytes; the mix script
records only what it `measured`). Refusals: `no_screen`, `not_ceremony`. A slide coming up after the
hit dissolves the debut under it (~0.65 s); before the hit it takes the stage at once. **To measure at the rehearsal: `START_LAG_S` (0.15 s, a
judgement)** — the turnlog row `debut_start` has the send's wall clock (`wall_ms`); film the screen with a
phone, the hit's frame less the mix's `impact_s` is the lag. **On software rendering the debut keeps time
but runs at 5–15 fps** (headless without a GPU here: choppy) — open `chrome://gpu` on the lobby laptop
and check "Compositing: Hardware accelerated" before the day. Watch it: `python
scripts/preview_ceremony_reveal.py [--gpu] [--shots <folder>] [--size 2048x1080] [--perf]`; tests:
`tests/test_ceremony_reveal.py`.

Not done yet (17 Nov evening): the pictures of the NEXT line are not warmed ahead (each slide's
files are asked for when its line starts — over the hall's Wi-Fi a 2 MB picture may come a moment
after its beat); the subtitle band can still break inside a Thai word (two designers saw it: the
band is the page's, not an act's); a long evening of many WebGL pictures has not been soak-tested;
nobody has signed anything.

## 1. The line you illustrate

`docs/emma-ceremony-script-en-th.md` has every cue: `en` is what she **says** (the voice the slide
follows), `th` is the subtitle. A slide is made of that cue's own English line and nothing else:

- every word on the slide is a word of the line (or `Embassy World` / `Emma`, or one of
  `a an the and of to in on for with at is are it this that`); plural/singular count as the same word;
- every number is a number the line says, written as the line writes it (`1,066`, not `1066`), and
  stays beside the word the line puts next to it (`155 metres`, `Floor 3`) — a bare figure is refused;
- the owner's qualifiers stay with their number in the same text/figure: **about 600 … straight
  line**, **around 2,500 … estimate**, **about 23**;
- characters: letters, digits, spaces and `· , . - ' ! ? :` only. No Thai, no `+ % €`, no brackets;
- never: price, promotion, rooftop / roof deck (the word *roof* only as the text `Roof level`),
  "concept", "subject to change", invest;
- a joke's set-up must not show its punchline (the rules above enforce it: the punchline's words
  are not in the set-up's line — do not work round it with a picture).

## 2. Pictures

Only the pictures in the catalogue: `.tmp/ceremony-show/assets/catalog.md` + `sheet_NN.png`
(rebuild with `python scripts/build_ceremony_asset_catalog.py`). **Open the sheets and choose by
looking**, then name a picture by its id. They are the stories catalogue's pinned pictures; a slide
cannot use any other file, URL or path.

- `state` must be `ok`. Excluded ones (people's portraits, anything with a price or quotation, the
  app screen with baked-in claims, the developer's awards, parking/EV) are refused.
- `about` lists the specific words that back the picture (a phrase = all its words; numbers
  exactly: `floor 3` is not `ground floor`). A picture is a claim, checked where it appears:
  a picture **on a beat** must be backed by the **sentence its beat is in** (what she is saying
  when it comes up, not something said elsewhere in the line); a picture in the **opening** (no
  beat, or a `backdrop`) by the line -- or be one of the few `establishing` pictures (the
  entrance `rm_signature`, the project view `liv_trees`, the site holograms
  `campus_holo_f1060..f1240`), which may open any cue but never come in on a beat unbacked;
  a **caption or label** must contain one of ITS picture's `about` phrases (the level-3 pool
  captioned "Ground floor" or "155-metre pool" is refused). If nothing is backed, use a text
  template -- no picture is better than one that says something the line does not.
- Read the `baked-in text` column: a plan's room names or a map's labels are on the screen too.
- Pictures are ~1080 px wide; full-bleed they are enlarged ~1.8×. Renders hold up; small files
  (under ~800 px) belong in a `spotlight` frame or a `mosaic` tile, not a `film`.
- Models on black (`campus_*`, `bld_*`, `room_*`) and see-through plans (`layout_*`, `plan_*`) sit
  well on the dark stage; use `"fit": "contain"` for plans and maps so nothing is cropped.
- `fac_3_sky_pool` is level 3, **not** a roof. Never caption it as one.
- There is **no** picture of: lobby lights, a quotation, the water-quality dashboard, the videos,
  the host or Emma. Smart-home has `sh_curtain`, `sh_aircon`, `prj_tech_home` only. For those lines
  keep the text templates (`status`, `hero`, `joke`), with a `backdrop` if a picture is backed.

## 3. Templates

Every slide: `"template"`, its fields, and `"beats"`. Optional on all: `"kicker"` (small line above
the title). Texts are at most 90 characters.

### `film` — full-bleed pictures, one after another (the default for a line with pictures)

```json
{
 "template": "film",
 "kicker": "Embassy World",
 "title": "Main common areas in Building A",
 "shots": [
  {"asset": "fac_ue_building_a"},
  {"asset": "fac_b_aqua_cinema", "caption": "Basement level"},
  {"asset": "fac_g_biogenesis", "caption": "Ground floor"},
  {"asset": "lagoon_islands_aerial"}
 ],
 "figure": {"value": "155", "unit": "metres long", "label": "The swimming pool"},
 "chips": ["Several more places to relax"],
 "beats": [
  {"show": "shot:1", "at_en": "on the basement level"},
  {"show": "shot:2", "at_en": "the ground floor"},
  {"show": "shot:3", "at_en": "A real highlight"},
  {"show": "figure", "at_en": "a full 155"},
  {"show": "chip:0", "at_en": "several more places"}
 ]
}
```

`shots` 1–6 × `{asset, caption?, fit?, figure?}`; `title?`, `figure?` `{lead?, value, unit, label?}`,
`chips?`, `end?`. Parts: `title`, `shot:N`, `figure`, `chip:N`, `end`.

- A shot's own `figure` (same fields) comes in with that picture and gives way when the next
  picture comes: for a line with several numbers, one picture and one figure each
  (`data/ceremony/slides/s06_l03.json`). Its `label` is the small heading above the number.
- `end`: a large centred title for the last frame, on a beat (`{"show": "end", ...}`); the side
  words, captions and scrim give way to it and the last picture steps back (`s10_l05`, `s09_l02`).
- `"fit": "contain"` melts a picture's edge into the stage (models on black, plans, anything under
  ~1000 px wide that would be soft full-bleed). Words sit on a dark scrim on the left; a
caption is a plate at the bottom right of its picture. `shot:0` is the establishing picture: it is
up before her voice and **may not be on a beat**.

### `spotlight` — one large picture in a frame, the words beside it

```json
{
 "template": "spotlight",
 "kicker": "Embassy World",
 "title": "Jomtien Second Road area",
 "side": "left",
 "pictures": [
  {"asset": "rm_signature"},
  {"asset": "map_jomtien", "caption": "Jomtien Beach"}
 ],
 "figure": {"lead": "about", "value": "600", "unit": "metres", "label": "from Jomtien Beach in a straight line"},
 "chips": ["Attractions", "Shopping"],
 "beats": [
  {"show": "picture:1", "at_en": "about 600"},
  {"show": "figure", "at_en": "about 600"},
  {"show": "chip:0", "at_en": "attractions"},
  {"show": "chip:1", "at_en": "shopping"}
 ]
}
```

`title` (required), `sub?`, `side?` (`left`/`right`: where the picture is), `pictures` 1–5 ×
`{asset, caption?, fit?}`, `figure?`, `chips?`. Parts: `title`, `sub`, `picture:N`, `figure`,
`chip:N`. `picture:0` may not be on a beat. Best when the line carries a figure and a list, or the
pictures are maps, plans, models or smaller files.

### `mosaic` — three to six picture tiles arriving one by one

```json
{
 "template": "mosaic",
 "title": "Main common areas in Building A",
 "tiles": [
  {"asset": "fac_b_aqua_cinema", "label": "Basement level"},
  {"asset": "fac_g_biogenesis", "label": "Ground floor"},
  {"asset": "fac_3_sky_pool", "label": "Floor 3"}
 ],
 "foot": "Several more places to relax",
 "beats": [
  {"show": "tile:0", "at_en": "on the basement level"},
  {"show": "tile:1", "at_en": "the ground floor"},
  {"show": "tile:2", "at_en": "floor 3"},
  {"show": "foot", "at_en": "several more places"}
 ]
}
```

`tiles` 3–6 × `{asset, label?, fit?}`, `title?`, `foot?`. Parts: `title`, `tile:N`, `foot`. For a
line that lists things quickly and you want them all to stay on the screen. Keep the title off the
beats so the stage is not empty before the first tile.

### The seven text templates, and `backdrop`

`hero`, `number`, `list`, `choice`, `status`, `joke`, `stats` — as in the base file
`data/ceremony/floor1_ceremony.slides.json` (read your cue's entry there: it already passes every
rule and is a good source of wording). Any of them takes `"backdrop": "<asset id>"`: that picture,
dimmed, drifting behind the words.

```json
{"template": "hero", "kicker": "Embassy World", "icon": "play", "title": "The story of Embassy World",
 "backdrop": "liv_trees", "beats": []}
```

Fields in brief — `hero`: `title`, `sub?`, `chips?`, `icon?`, `mood?` (`closing`). `number`:
`title?`, `lead?`, `value`, `unit`, `label?`, `chips?`, `graphic?` (`span`/`lane`). `list`: `title`,
`items` 2–5 × `{text, icon?}`, `highlight?`, `foot?`. `choice`: `title`, `options` 2–5 ×
`{text, icon?}`. `status`: `state` (`working`/`done`), `device` (`lights_off`, `lights_on`,
`curtain`, `document`), `title`, `detail?`. `joke`: `title?`, `tail?`, `punch?`, `icon?`. `stats`:
`title?`, `stats` 2–5 × `{lead?, value, unit, label}`. Icons and the words that back them:
`app/ceremony_slides.py` `ICONS`.

## 4. Beats — how the slide follows her voice

`{"show": "<part>", "at_en": "<a piece of the English line, exactly as written>"}`. The part comes
in when her voice reaches that piece. `"at": 0.0–1.0` (a fraction of the line) also works; `at_en`
is better. A part with **no** beat is the opening: up before her first sound.

- Beats must be in the line's order; each part at most once; the piece must be in the line
  (first occurrence counts — pick a piece that is unique).
- Nothing that gives the line away goes in the opening: a figure, a punchline, a "done ✓" land on
  their words. Headlines and the first picture do belong in the opening — no empty stage.
- A figure appears as its true value and never counts up: no number on the way to it is ever
  on the screen.
- She speaks ~14 characters a second. Two picture beats closer than ~14 characters are under a
  second apart: fine for a quick run of three or four cuts, tiring for a whole line. Give the
  picture that matters (the one under the figure, the last one) several seconds.
- The last beat should leave a calm frame that can stay: the line over, the slide **stays up**
  until the next line. Do not end on something that only makes sense for half a second.

## 5. Shoot it and LOOK

```
python scripts/approve_ceremony_slides.py --status                       # your cue must say "ok"
python scripts/preview_ceremony_show.py --only <cue id> --shots .tmp/ceremony-show/shots --shots-every 2
```

`--status` prints what is wrong (`word_not_in_line:sauna`, `asset_not_in_line:sh_curtain`,
`number_not_with_its_words:155`, `first_picture_on_a_beat:shot:0` …). A slide with a problem is not
shown at all — the screen falls back to the plain backdrop. The second command plays the line with
its real preview voice in a headless browser and writes `<cue id>_t00.0.png`, `_t02.0.png`, … (one
frame every 2 s and one after the end) into the folder; only your cue's frames are replaced. Open
them. Without `--shots` it prints a URL to watch live in Chrome with sound.

Look for: an empty or text-only frame; a picture that does not match what the Thai subtitle is
saying at that moment; a caption over the important part of a picture; words that wrap badly or
touch the edge; a picture cropped so its subject is gone (try `"fit": "contain"` or another
picture); a figure visible before she says it; the end frame.

## 6. What "good" looks like

A line is a small film: **an establishing picture** while she begins (the building, the campus,
the lagoon) → **detail pictures as she names things** (each on its word, captioned with her own
words) → **the figure landing on its number** over the picture it belongs to → **a calm end frame**
that can stay on the screen while the host talks. Pictures carry it; the words are few and large.
The two worked examples are `data/ceremony/slides/s04_l06.json` (`film`) and
`data/ceremony/slides/s03_l03.json` (`spotlight`).

## 7. What you cannot do (and why it is not a bug)

- Show a draft on the stage: the whole set is unsigned until the owner runs
  `scripts/approve_ceremony_slides.py --by NAME`; any later edit of any per-cue file makes it a
  draft again. The preview shows drafts; the robot sends none.
- Use a picture that is not in the catalogue, or whose `about` words your line lacks. If you think a
  picture is wrongly excluded or described, say so in your report — do not edit `slide_assets.json`.
- Change timing with delays or durations: there are none. The only clock is her voice.
