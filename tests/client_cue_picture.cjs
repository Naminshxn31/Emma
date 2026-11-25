// A ceremony cue pressed with a picture (app/event_cues.py `show`, owner 7 Nov): the
// frames the server really sent the robot face (captured by
// tests/test_event_cue_picture.py) replayed through the page's own onMessage and its
// own stage code. What it holds: the story goes up as THIS answer's screen -- her
// line's first sound does not clear it (answerOwed/answerStaged), nothing settles or
// tears it down while she says the line, and every piece of the line's audio and
// words reaches the story's clock (none of it came before the picture).
// `"mirror": true` in the spec: the ROOM SCREEN's own road (review 7 Nov, B9) -- the
// page in /mirror mode stages a story in its frame (showMirrorStory: answerStaged,
// body.mirror-story) and its story hooks go to the frame's engine (forwardStoryEngine);
// replayed with mirror:false, the TV's staging was never run at all.
// Usage: node tests/client_cue_picture.cjs <frames.json>   (cwd = repo root)
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const cut = (from, to) => {
  const a = html.indexOf(from), b = html.indexOf(to, a);
  assert.ok(a >= 0 && b > a, 'slice not found: ' + from);
  return html.slice(a, b);
};
const handler = cut('    function onMessage(msg)', '    // ---- dial / hang up ----');
const stage = cut('    function openStage()', '    function elDiv(');
const story = cut('    function showStory(story)', '    var UNIT_STATUS_EN');
const mirrorStory = cut('    function forwardStoryEngine()', '    function showMirrorSlide(');
// the stand-ins below do what the real ones do for the story (playChunk schedules
// the chunk and tells the story; stopPlayback settles it) -- the Python side checks
// those lines are in the page
assert.ok(html.includes("storyHook('audio', at, buf.duration);"), 'playChunk tells the story');
assert.ok(/function stopPlayback\(\) \{\s*storyHook\('settle'\);/.test(html), 'stopPlayback settles the story');

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const onTv = spec.mirror === true;
const log = [];                       // what reached the story engine that drew it, in order
const own = [];                       // the TV page's own engine (its captions): never the story
const classes = new Set();
const engine = (into) => ({
  play(s, opts) { into.push(['play', s.id, (opts && opts.layout) || null]); return true; },
  audio(at, d) { into.push(['audio', d]); },
  transcript(t) { into.push(['transcript', t]); },
  turnComplete() { into.push(['turnComplete']); },
  settle() { into.push(['settle']); },
  teardown() { into.push(['teardown']); },
  landscape() { return true; },          // emma-story.js: every template has its across-the-TV layout
});
const mount = { innerHTML: '' };
const c = vm.createContext({
  ArrayBuffer, JSON, isFinite, URL, performance: { now: () => 1 },
  WebSocket: { OPEN: 1 }, ws: null, location: { href: 'https://robot.local/' },
  inCall: true, playing: false, muted: false, appShell: true, mirror: onTv,
  bargePendingUntil: 0, bargeRelayUntil: 0, sentMs: 0, outputRate: 24000,
  curBot: '', curUser: '', userLine: null, userLinesById: Object.create(null),
  answerOwed: false, answerStaged: false, choiceSet: null, greetingComing: false,
  assistantTurnStarted: false, lastStatusKey: '', frameWatch: 0, stageInner: null,
  q: { get: () => null },
  window: { EmmaStory: engine(onTv ? own : log) },
  // the room screen's story frame (voice-preview.html mirrorStage): its own engine
  storyFrame: { style: {}, contentWindow: { EmmaStory: engine(log) },
                contentDocument: { querySelector: (sel) => (sel === '.vp-stage-inner' ? mount : null) } },
  document: { documentElement: { classList: { contains: () => false } }, body: { classList: {
    add: (k) => classes.add(k), remove: (k) => classes.delete(k), contains: (k) => classes.has(k),
  } } },
  show() {}, status() {}, paintReply() {}, renderDrawer() {}, renderQuick() {}, setUiLang() {},
  scriptLang() { return null; }, sendQuickAsk() { return false; }, heardClock() { return 0; },
  clearTimeout() {}, setTimeout() { return 0; },
});
vm.runInContext(handler + stage + story + (onTv ? mirrorStory : ''), c);
vm.runInContext(`
  var clock = 0;
  function ensureStage() { if (!stageInner) stageInner = { innerHTML: '' }; }
  function beginAssistantTurn() { assistantTurnStarted = true; }
  function resetAssistantTurn() { assistantTurnStarted = false; }
  function appendAssistantTranscript(t) { beginAssistantTurn(); }
  function playChunk(b) { var d = b.byteLength / 2 / outputRate; storyHook('audio', clock, d); clock += d; playing = true; }
  function stopPlayback() { storyHook('settle'); playing = false; return 0; }
`, c);
if (onTv) {
  // the frame is up and loaded; laying it out is the browser's (not replayed here)
  vm.runInContext(`
    var mirrorFrame = storyFrame, mirrorFrameReady = true, mirrorPending = null, mirrorStoryId = null,
        mirrorLand = false;
    function mirrorStage() { return mirrorFrame; }
    function mirrorFrameMode(land) { mirrorLand = !!land; return mirrorFrame; }
    function fitMirrorStage() {}
    forwardStoryEngine();
  `, c);
}

let played = -1, audioFrames = 0, audioBefore = 0, words = '';
for (const f of spec.frames) {
  if (f.audio) {
    audioFrames += 1;
    if (played < 0) audioBefore += 1;
    c.onMessage({ data: new ArrayBuffer(f.audio) });
  } else {
    if (played < 0 && f.json.type === 'assistant_transcript') words += '<before>';
    c.onMessage({ data: JSON.stringify(f.json) });
  }
  if (played < 0) played = log.findIndex((e) => e[0] === 'play');
}
assert.ok(played >= 0, 'the story was drawn');
assert.deepEqual(log.filter((e) => e[0] === 'play').map((e) => e[1]), [spec.topic], 'one story, the pressed one');
const after = log.slice(played + 1);
assert.deepEqual(after.filter((e) => e[0] === 'teardown' || e[0] === 'settle'), [],
  'her line cleared or settled the picture pressed with it');
assert.equal(audioBefore, 0, 'some of her line played before its picture');
assert.ok(!words.includes('<before>'), 'some of her line\'s words came before its picture');
assert.equal(after.filter((e) => e[0] === 'audio').length, audioFrames, 'the story heard every chunk of her line');
assert.equal(after.filter((e) => e[0] === 'transcript').map((e) => e[1]).join(''), spec.line,
  'the story heard her line from its first word');
if (onTv) {
  // the TV's own staging: its frame drew it across the screen, the page's own engine
  // (the captions) never did, and the room screen is still showing it at the end
  assert.deepEqual(log[played], ['play', spec.topic, 'land'], 'the frame drew it across the TV');
  assert.deepEqual(own.filter((e) => e[0] === 'play' || e[0] === 'teardown'), [],
    "the TV page's own engine drew or tore down a story");
  assert.ok(classes.has('mirror-story') && !classes.has('emma-story'), 'the room screen is showing the story');
  assert.equal(c.mirrorStoryId, spec.topic);
} else {
  assert.ok(classes.has('emma-staging') && classes.has('emma-story'), 'the stage is down at the end');
}
assert.equal(c.answerStaged, true);
console.log('cue picture' + (onTv ? ' (room screen)' : '') + ': drawn before her first word, kept through her line PASS');
