const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('client/voice-preview.html', 'utf8');
const handler = html.slice(html.indexOf('    function onMessage(msg)'), html.indexOf('    // ---- dial / hang up ----'));
const helpers = html.slice(html.indexOf('    function clearTranscript()'), html.indexOf('    // ---- stage:'));
let playedChunks = 0;
const c = vm.createContext({
  ArrayBuffer, curUser: '', userLine: null, userLinesById: Object.create(null),
  curBot: '', assistantTurnText: '', assistantLine: null, assistantTurnStarted: false,
  transcript: [], sentMs: 0, playing: false, inCall: true, ws: null, drawer: null,
  bargePendingUntil: 0, bargeRelayUntil: 0, performance: { now: () => 1 },
  renderDrawer() {}, paintReply() {}, show() {}, status() {}, setTranscriptDrawerOpen() {},
  outputRate: 24000, playChunk() { playedChunks += 1; },
  // tap-to-ask state (outside the slices): no topics on this call
  quickAsks: null, setUiLang() {}, scriptLang() { return null; },
  choiceSet: null, renderQuick() {},
  // each answer owns the stage (owner 12 Oct): outside the slices too
  answerOwed: false, answerStaged: false, clearStage() {},
  document: { body: { classList: { contains() { return false; } } } },
});
vm.runInContext(handler + helpers, c);
const event = (type, text, utterance_id) => c.onMessage({ data: JSON.stringify({ type, text, utterance_id }) });
event('assistant_transcript', "Hello, I'm ");
c.onMessage({ data: new ArrayBuffer(480) });
assert.equal(playedChunks, 1);
c.bargePendingUntil = 1000;
c.onMessage({ data: new ArrayBuffer(480) });
assert.equal(playedChunks, 1, 'old audio cannot restart after a local barge-in');
c.bargePendingUntil = 0;
event('assistant_transcript', 'Emma.');
assert.equal(c.transcript[0].text, "Hello, I'm Emma.");
event('turn_complete');
// A locally confirmed barge must not swallow the next answer if Gemini has
// no separate interrupted event after speech_started.
c.bargePendingUntil = 10000;
event('speech_started');
assert.equal(c.bargePendingUntil, 701);
event('user_transcript', 'First ', '1');
event('assistant_transcript', 'First reply.');
event('user_transcript', 'question.', '1');
event('turn_complete');
event('speech_started');
event('user_transcript', 'Second question.', '2');
// A delayed chunk must update its original row even after the next utterance.
event('user_transcript', ' Extra detail.', '1');
event('user_transcript', ' More.', '2');
assert.equal(c.transcript.filter(r => r.who === 'you').length, 2);
assert.equal(c.userLinesById['1'].text, 'First question. Extra detail.');
assert.equal(c.userLinesById['2'].text, 'Second question. More.');
assert.equal(c.transcript.filter(r => r.who === 'emma')[1].text, 'First reply.');
c.clearTranscript();
assert.equal(c.transcript.length, 0);
assert.equal(Object.keys(c.userLinesById).length, 0);
event('user_transcript', 'New caller.', '1');
assert.equal(c.transcript[0].text, 'New caller.');
console.log('Preview: complete greeting, separate utterances, delayed chunks and call reset PASS');
