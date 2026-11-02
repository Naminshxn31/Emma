// A tap on the robot face (sendQuickAsk) that the server drops -- a second
// tap inside its 1.2 s gap, a story id no longer on screen -- gets nothing
// back at all. The page used to stand on "Thinking..." for good; it now falls
// back to listening as heard_end's "thinking" does, and only when nothing at
// all came from the server for that tap. What goes over the socket is
// unchanged. Run by tests/test_voice_preview_page.py (cwd = repo root).
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const handler = html.slice(html.indexOf('    function onMessage(msg)'), html.indexOf('    // ---- dial / hang up ----'));
const tap = html.slice(html.indexOf('    function sendQuickAsk(id)'), html.indexOf('    function toggleMute()'));
assert.ok(tap.includes("type: 'quick_ask'"), 'the tap slice was found');

function page() {
  const timers = [], shown = [], said = [], sent = [];
  const c = vm.createContext({
    ArrayBuffer, JSON, isFinite, performance: { now: () => 1 },
    WebSocket: { OPEN: 1 },
    ws: { readyState: 1, send: (v) => sent.push(JSON.parse(v)) },
    quickAsks: {}, inCall: true, playing: false, muted: false, uiLang: 'th',
    bargePendingUntil: 0, bargeRelayUntil: 0, sentMs: 0, curBot: '',
    answerOwed: false, answerStaged: false, appShell: true, choiceSet: null,
    thinkingTimer: 0, THINKING_FALLBACK_MS: 6000,
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {},
    show: (s) => shown.push(s), status: (s) => said.push(s),
    stopPlayback: () => 0, resetAssistantTurn() {}, renderQuick() {}, attendFromServer() {},
    window: {}, document: { body: { classList: { contains() { return false; } } } },
  });
  vm.runInContext(handler + tap, c);
  return { c, timers, shown, said, sent };
}
const last = (a) => a[a.length - 1];

{
  // dropped: nothing comes back -> listening after the fallback, never before
  const p = page();
  assert.equal(p.c.sendQuickAsk('about'), true);
  same(p.sent, [{ type: 'quick_ask', id: 'about', lang: 'th', cut: false }]);
  assert.equal(p.c.answerOwed, true, 'a topic is a question: its answer owns the screen');
  assert.equal(last(p.shown), 'thinking');
  assert.equal(last(p.said), 'Thinking...');
  const t = last(p.timers);
  assert.equal(t.ms, 6000, 'the same fallback as heard_end');
  t.fn();
  assert.equal(last(p.shown), 'listening');
  assert.equal(last(p.said), 'Listening...');
}
{
  // taken: anything from the server for it (here her choices) keeps "thinking"
  const p = page();
  p.c.sendQuickAsk('about');
  p.c.onMessage({ data: JSON.stringify({ type: 'choices', items: [] }) });
  last(p.timers).fn();
  assert.equal(last(p.shown), 'thinking');
}
{
  // ...and her audio counts too (a binary frame)
  const p = page();
  p.c.outputRate = 24000;
  vm.runInContext('function beginAssistantTurn() {} function playChunk() {}', p.c);
  p.c.sendQuickAsk('about');
  p.c.onMessage({ data: new ArrayBuffer(480) });
  last(p.timers).fn();
  assert.equal(last(p.shown), 'thinking');
}
{
  // the head-turn cue alone is not an answer
  const p = page();
  p.c.sendQuickAsk('about');
  p.c.onMessage({ data: JSON.stringify({ type: 'attention', bearing: 10 }) });
  last(p.timers).fn();
  assert.equal(last(p.shown), 'listening');
}
{
  // she is speaking when it fires, or the call ended: the fallback leaves it alone
  const p = page();
  p.c.sendQuickAsk('about');
  p.c.playing = true;
  last(p.timers).fn();
  assert.equal(last(p.shown), 'thinking');
  const q = page();
  q.c.sendQuickAsk('about');
  q.c.inCall = false;
  last(q.timers).fn();
  assert.equal(last(q.shown), 'thinking');
}
{
  // muted: back to the muted pill, not "Listening..."
  const p = page();
  p.c.sendQuickAsk('about');
  p.c.muted = true;
  last(p.timers).fn();
  assert.equal(last(p.said), 'Muted');
}
{
  // a pick on the language menu (review 29 Oct): the server's lang_pinned is
  // not her answer -- counted, the fallback never fired -- and a pick leaves
  // the screen alone (answerOwed untouched)
  const p = page();
  vm.runInContext('function langPinned() {}', p.c);
  p.c.sendQuickAsk('lang_zh');
  same(p.sent, [{ type: 'quick_ask', id: 'lang_zh', lang: 'th', cut: false }]);
  assert.equal(p.c.answerOwed, false);
  p.c.onMessage({ data: JSON.stringify({ type: 'lang_pinned', lang: 'zh' }) });
  last(p.timers).fn();
  assert.equal(last(p.shown), 'listening');
}
{
  // picked while she spoke: the page says it cut her off
  const p = page();
  vm.runInContext('function captionHook() {}', p.c);
  p.c.playing = true;
  p.c.sendQuickAsk('lang_ru');
  same(p.sent, [{ type: 'played', heard_ms: 0 }, { type: 'quick_ask', id: 'lang_ru', lang: 'th', cut: true }]);
}
function same(a, b) { assert.equal(JSON.stringify(a), JSON.stringify(b)); }
console.log('tap fallback: a dropped tap falls back to listening PASS');
