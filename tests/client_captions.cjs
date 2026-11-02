// The robot face's motion captions (owner 27 Oct, "motion ตัวหนังสือ"): the
// timing core in client/assets/emma-story.js (EmmaStory.caption), run in node.
// Her answers are played the way Gemini delivers them -- each transcript
// piece just before its own audio, the audio generated 4x faster than it
// plays, pieces cut every 18 characters (mid-word) -- and at every moment
// the words that may be on screen are checked against when she actually
// SAID them: never ahead of her voice (but for the lead), never far behind,
// a word never split, all of it once her voice has played out.
// Run by tests/test_stories.py.   usage: node tests/client_captions.cjs
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const SRC = fs.readFileSync('client/assets/emma-story.js', 'utf8');

function load(noSegmenter) {
  const sandbox = { console, setTimeout, clearTimeout, setInterval, clearInterval, Promise, Date, Math, Number,
                    matchMedia: () => ({ matches: false }) };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  if (noSegmenter) vm.runInContext('delete Intl.Segmenter;', sandbox);
  vm.runInContext(SRC, sandbox);
  return sandbox.EmmaStory;
}
const S = load(false);
const { norm, CAPTION } = S._clock;
const words = c => c.words.map(w => w.t);
const same = (a, b, msg) => assert.equal(JSON.stringify(a), JSON.stringify(b), msg);

const TH = 'ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ ชั้นใต้ดินเป็นสปาและสระว่ายน้ำในร่ม '
  + 'ชั้น G มีฟิตเนส Biogenesis ริมสระ ห้องปั่นจักรยาน พิลาทิสสตูดิโอ บันจี้ยิมและโยคะ '
  + 'ส่วนชั้นสามของอาคาร A มีเลานจ์และ Sky Pool ขนาด 51.5 ตร.ม. ลูกค้าอยากเห็นชั้นไหนเป็นพิเศษไหมคะ';
const EN = "We have facilities on three floors. The ground floor has the Biogenesis fitness by the pool, "
  + "a Spinning Class studio (on the left), Bungee Gym and Yoga. Which floor would you like to see?";

// One answer through a fresh caption. Returns, per sample, what was due.
function play(text, cps, opt) {
  opt = opt || {};
  const piece = opt.piece || 18, cap = S.caption(), events = [];
  let gen = 0.3;                               // wall/audio-clock seconds the model sends at
  for (let k = 0; k < text.length; k += piece) {
    const p = text.slice(k, k + piece), n = norm(p).length;
    events.push([gen - 0.001, 'text', p]);
    const dur = n / cps, chunks = Math.max(1, Math.ceil(dur / 0.1));
    for (let j = 0; j < chunks; j++) { events.push([gen, 'audio', dur / chunks]); gen += 0.1 / 4; }
  }
  if (!opt.noComplete) events.push([gen + 0.2, 'complete']);
  events.sort((a, b) => a[0] - b[0]);
  let head = 0, ei = 0, start = null;
  const out = [];
  for (let t = 0; t < 60; t += 0.05) {
    while (ei < events.length && events[ei][0] <= t) {
      const [at, kind, v] = events[ei++];
      if (kind === 'text') cap.text(v, at * 1000);
      else if (kind === 'audio') {
        const s = Math.max(at, head); head = s + v; if (start == null) start = s;
        cap.audio(s, v, at * 1000);
      } else cap.complete();
    }
    const n = cap.due(opt.noClock ? null : t, t * 1000);
    out.push({ t, n, played: start == null ? 0 : Math.max(0, t - start) });
    if (start != null && t > head + 1.5) break;
  }
  return { cap, out, start, head };
}

// ---- never ahead of her voice, never far behind it ----
for (const [text, cps, lang] of [[TH, 12.5, 'th'], [EN, 15, 'en'], [TH, 11.5, 'th slow'], [EN, 17, 'en fast']]) {
  const { cap, out, start, head } = play(text, cps);
  const ends = cap.words.map(w => start + w.end / cps);        // when she finished saying each word
  let lastN = 0, worstLag = 0;
  for (const s of out) {
    assert.ok(s.n >= lastN, 'a word once up stays up');
    lastN = s.n;
    for (let k = 0; k < s.n; k++) {
      assert.ok(ends[k] <= s.t + CAPTION.leadS + 1e-6,
                `${lang}: "${cap.words[k].t}" up at ${s.t.toFixed(2)} s, said at ${ends[k].toFixed(2)} s`);
    }
    // every word she finished saying a while ago is up (her voice is not over yet)
    if (s.t < head) for (let k = s.n; k < ends.length; k++) if (ends[k] < s.t) worstLag = Math.max(worstLag, s.t - ends[k]);
  }
  assert.ok(worstLag < 0.8, `${lang}: a word trailed her voice by ${worstLag.toFixed(2)} s`);
  assert.equal(out[out.length - 1].n, cap.words.length, `${lang}: all of it once her voice played out`);
  assert.equal(cap.pending(), false);
}

// ---- words, never pieces of one ----
{
  const { cap } = play(TH, 12.5);
  // every character, in order, no space inside a word
  assert.equal(words(cap).join(''), TH.replace(/\s+/g, ''));
  assert.equal(cap.normAt, norm(TH).length);
  for (const w of cap.words) assert.ok(!/\s/.test(w.t), w.t);
  // a piece cut every 18 characters splits "51.5", "Biogenesis", Thai words
  // (and the segmenter itself cut the loanword "สปา" into "ส" + "ปา"):
  // the caption's words are the segmenter's words of the WHOLE text
  const seg = new Intl.Segmenter('th', { granularity: 'word' });
  const cuts = new Set(); let at = 0;                 // offsets in the text without its spaces
  for (const s of seg.segment(TH)) { if (/^\s+$/.test(s.segment)) continue; cuts.add(at); at += s.segment.length; }
  at = 0;
  for (const w of cap.words) { assert.ok(cuts.has(at), 'a cut inside a word before "' + w.t + '"'); at += w.t.length; }
  for (const whole of ['51.5', 'Biogenesis', 'ตร.ม.', 'ฟิตเนส', 'สปา']) assert.ok(words(cap).includes(whole), whole);
  // a space before a word is kept as a space (and only there)
  assert.equal(cap.words.filter(w => w.sep).length, TH.trim().split(/\s+/).length - 1);
}
{
  const { cap } = play(EN, 15, { piece: 7 });
  same(cap.words.map(w => (w.sep ? ' ' : '') + w.t).join(''), EN);
  // punctuation goes with its word: no line can start on a comma or end on "("
  for (const w of words(cap)) assert.ok(!/^[,.!?)]/.test(w) && !/\($/.test(w), w);
  assert.ok(words(cap).includes('(on') && words(cap).includes('left),'), JSON.stringify(words(cap)));
}
{
  // Thai repetition mark with its word; held word across pieces
  const c = S.caption();
  ['มีให้เลือกเย', 'อะๆ ค่ะ'].forEach(p => c.text(p, 0));
  c.complete();
  assert.ok(words(c).includes('เยอะๆ'), JSON.stringify(words(c)));
}

// ---- the fallbacks ----
{
  // no audio clock on the page: the words as they come
  const { out, cap } = play(EN, 15, { noClock: true });
  assert.ok(out.some(s => s.n > 0 && s.t < 0.5), 'shown at once without a clock');
  assert.equal(out[out.length - 1].n, cap.words.length);
}
{
  // text and no audio at all: nothing for noAudioMs, then all of it
  const c = S.caption();
  c.text('Hello, I am Emma.', 0);
  assert.equal(c.due(1.0, 1000), 0);
  assert.equal(c.due(2.9, CAPTION.noAudioMs - 1), 0);
  assert.equal(c.due(3.1, CAPTION.noAudioMs + 1), c.words.length);
  assert.equal(c.words.length, 4);
}
{
  // audio that never plays (a suspended context: its clock stands still at
  // the chunk's start): as it is, once it has stood there noAudioMs
  const c = S.caption();
  c.text('Hello, I am Emma. ', 0);
  c.audio(5, 1.2, 10);
  assert.equal(c.due(5, 1000), 0);
  assert.equal(c.due(5, 1000 + CAPTION.noAudioMs - 1), 0);
  assert.equal(c.due(5, 1000 + CAPTION.noAudioMs + 1), c.words.length);
}
{
  // review 27 Oct: a new answer whose audio is queued behind her previous
  // answer's (gemini-3.8 completes a turn before its tool call and speaks
  // after it) waits for its OWN audio however long, not 3 s from its arrival
  const c = S.caption();
  c.text('Here is the third floor plan for you. ', 0);
  c.audio(9.0, 2.5, 0);
  for (const t of [1, 2, 3.01, 4, 6, 8, 8.5, 9.0]) assert.equal(c.due(t, t * 1000), 0, 'up at ' + t + ' s, her voice at 9 s');
  assert.ok(c.due(10.5, 10500) > 0 && c.due(10.5, 10500) < c.words.length, 'with her voice, word by word');
  assert.equal(c.due(11.6, 11600), c.words.length);
}
{
  // review 27 Oct: her voice played out mid-turn for longer than endMs (a
  // tool call), then the next piece comes -- it waits for its own audio
  // instead of showing whole on arrival (a stale overSince)
  const c = S.caption();
  c.text('Sure, let me check. ', 0); c.audio(0, 1.4, 0);
  for (let t = 0; t <= 3.0; t += 0.1) c.due(t, t * 1000);        // the page's 10 Hz look
  const before = c.shown;
  assert.equal(before, 4);
  c.text('The sky pool is on level three. ', 3000);
  assert.equal(c.due(3.0, 3000), before, 'the new piece waits for its audio');
  c.audio(3.05, 2.1, 3001);
  assert.equal(c.due(3.1, 3100), before);
  assert.ok(c.due(4.0, 4000) > before && c.due(4.0, 4000) < c.words.length, 'and comes with it');
}
{
  // review 27 Oct: the sentence before a tool call with no turn_complete
  // (its last piece has no next piece to say where its audio ends): its
  // words come while she says them, not all at once after her voice ended
  const a = 'สักครู่นะคะ ขอดูข้อมูลให้ก่อนนะคะ';
  const c = S.caption();
  c.text(a, 0);
  const dur = norm(a).length / 12.5;
  let at = 0;
  for (let j = 0; at < dur - 1e-9; j++) { const d = Math.min(0.1, dur - at); c.audio(at, d, j * 25); at += d; }
  const seen = [];
  for (let t = 0.05; t < dur + 0.5; t += 0.1) seen.push([t, c.due(t, 1000 + t * 1000)]);
  const mid = seen.filter(([t]) => t > dur * 0.4 && t < dur * 0.6).map(([, n]) => n);
  assert.ok(mid.length && mid.every(n => n > 0 && n < c.words.length), 'part of it while she says it: ' + JSON.stringify(seen));
  assert.equal(seen[seen.length - 1][1], c.words.length, 'all of it by the end of her voice');
}

// ---- review 27 Oct: a half word is not one segment ----
// "สำหรั" segments "สำ" + "หรั": holding back only the last segment let
// "สำ|หรับ", "มา|กมาย", "อำ|นวย" through. Realistic answers cut into pieces of
// EVERY size 1..40: after every piece, each released word is the word the
// whole text has at that place -- never one it would not have produced.
{
  const answers = [
    TH,
    'ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ รวมถึง Junior World สำหรับเด็ก '
      + 'ลูกค้าสนใจห้องแบบไหนเป็นพิเศษคะ สำหรับครอบครัวเรามีห้องสองห้องนอนค่ะ',
    'โครงการอยู่ห่างจากชายหาดจอมเทียนประมาณหนึ่งกิโลเมตรค่ะ เดินไปได้ประมาณสิบห้าถึงยี่สิบนาที '
      + 'ใกล้ห้างสรรพสินค้าและโรงพยาบาลหลายแห่ง จากสนามบินสุวรรณภูมิใช้มอเตอร์เวย์ประมาณหนึ่งชั่วโมงครึ่งค่ะ',
    'ห้องสตูดิโอเหมือนกับคอนโดทั่วไปแต่มีระเบียงกว้าง ท่ามกลางสวนและสระว่ายน้ำ ดิฉันขอแนะนำยูนิตชั้นสูงค่ะ '
      + 'ถ้าลูกค้าขับมอเตอร์ไซค์มามีที่จอดให้ค่ะ ถ้าต้องการห้องสามหรือสี่ห้องนอนกรุณาติดต่อทีมขายโดยตรงนะคะ',
  ];
  let runs = 0;
  for (const text of answers) {
    const whole = S.caption(); whole.text(text, 0); whole.complete();
    const want = whole.words.map(w => w.t);
    for (let piece = 1; piece <= 40; piece++) {
      const c = S.caption();
      for (let k = 0; k < text.length; k += piece) {
        c.text(text.slice(k, k + piece), 0);
        c.words.forEach((w, i) => assert.equal(w.t, want[i], `piece ${piece} at ${k}: "${w.t}" where the whole text has "${want[i]}"`));
      }
      c.complete();
      same(c.words.map(w => w.t), want, 'piece ' + piece);
      runs++;
    }
  }
  assert.equal(runs, 160);
}
{
  // her voice played out with no turn_complete (a stalled stream): a moment later, all
  const { out, cap } = play(EN, 15, { noComplete: true });
  assert.equal(out[out.length - 1].n, cap.words.length, 'all of it after endMs');
  assert.equal(cap.pending(), false);
}
{
  // a transcript that lands after its audio has played (short replies, 24 Sep)
  const c = S.caption();
  c.audio(1, 1.0, 1000);
  assert.equal(c.due(2.5, 2500), 0);
  c.text('Sure, here it is.', 2600);
  c.complete();
  assert.equal(c.due(2.7, 2700), c.words.length, 'a late text after her voice: at once');
}

// ---- a pause in the stream inside a word (audit 27 Oct) ----
// The quiet fallback placed the pending text by its audio and released the
// held word tail with it: a piece ending inside a word, then a >0.5 s gap,
// put "ความ | สะ | ดวก" on screen. The tail waits for her voice to reach it.
{
  const runGap = (text, cut, gapMs) => {
    const c = S.caption();
    const a = text.slice(0, cut), b = text.slice(cut);
    c.text(a, 0);
    const durA = a.length / 12.5;
    c.audio(0, durA, 10);
    let t = 0;
    for (; t * 1000 <= 10 + gapMs; t += 0.1) c.due(t, t * 1000);
    const wall = 10 + gapMs + 1;
    c.text(b, wall);
    c.audio(Math.max(durA, t), b.length / 12.5, wall);
    c.complete();
    c.due(99, 99000);
    return c.words.map(w => w.t);
  };
  const whole = text => { const c = S.caption(); c.text(text, 0); c.complete(); return c.words.map(w => w.t); };
  for (const [text, upto] of [['ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึงสามชั้นค่ะ', 'ในโครงการของเรามีสิ่งอำนวยความสะ'],
                              ['สำหรับครอบครัวเรามีห้องสองห้องนอนค่ะ', 'สำหรับครอบครัวเรามีห้องสองห้องน'],
                              ['The ground floor has the Biogenesis fitness by the pool.', 'The ground floor has the Bioge']]) {
    for (const gap of [300, 700, 1500]) same(runGap(text, upto.length, gap), whole(text), 'gap ' + gap + ' ms: ' + text);
  }
}

// ---- without Intl.Segmenter: whitespace words, nothing lost ----
{
  const S2 = load(true);
  const c = S2.caption();
  [EN.slice(0, 20), EN.slice(20)].forEach(p => c.text(p, 0));
  c.complete();
  same(c.words.map(w => (w.sep ? ' ' : '') + w.t).join(''), EN);
  const t = S2.caption();
  t.text('ชั้น G มีฟิตเนส', 0); t.complete();
  same(t.words.map(w => w.t), ['ชั้น', 'G', 'มีฟิตเนส']);
}

console.log('captions: in step with her voice, whole words, fallbacks PASS');
