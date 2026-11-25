// Runs the room-screen page's keep-awake tone code (client/voice-preview.html), cut out
// verbatim, against a fake AudioContext. Prints one JSON object: what each case did.
// Used by tests/test_room_screens.py -- the page's own bounds and its one-tone guard are
// behaviour, and a string pin of the source would stay green with either of them broken.
const fs = require('fs');
const path = require('path');

// (line endings ignored: with git autocrlf the page checks out CRLF, and the slices below cut on LF)
const html = fs.readFileSync(path.join(__dirname, '..', 'client', 'voice-preview.html'), 'utf8').replace(/\r\n/g, '\n');
function cut(from, to) {
  const a = html.indexOf(from);
  const b = html.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error('page slice not found: ' + from);
  return html.slice(a, b);
}
// "var mirrorSound = ..." up to the line after the parser's closing "})();"
const parseEnd = '    })();\n';
const parseFrom = "    var mirrorSound = mirror && q.get('sound') === '1';";
const pa = html.indexOf(parseFrom);
const pb = html.indexOf(parseEnd, html.indexOf('var mirrorKeep = null;', pa)) + parseEnd.length;
if (pa < 0 || pb < parseEnd.length) throw new Error('parser slice not found');
const parser = html.slice(pa, pb);
const starter = cut('    var mirrorKeepCtx = null;\n    function startMirrorKeep() {', '    function startMirrorAudio() {');

function run(c) {
  const made = [];
  function Ctx() {
    const self = this;
    this.sampleRate = c.rate || 48000;
    this.state = c.suspended ? 'suspended' : 'running';
    this.closed = false;
    this.oscs = [];
    this.gains = [];
    this.destination = {};
    this.createOscillator = function () {
      const o = { frequency: { value: 0 }, type: '', started: 0, connect: function () {},
        start: function () { if (c.startThrows && made.length === 1) throw new Error('no start'); o.started++; } };
      self.oscs.push(o);
      return o;
    };
    this.createGain = function () {
      const g = { gain: { value: 1 }, connect: function () {} };
      self.gains.push(g);
      return g;
    };
    this.resume = function () {
      self.resumed = (self.resumed || 0) + 1;
      if (c.resumeNoPromise) return undefined;          // an old webkitAudioContext
      self.state = 'running';
      return Promise.resolve();
    };
    this.close = function () { self.closed = true; };
    made.push(this);
  }
  const listeners = [];
  const window = { AudioContext: Ctx };
  const document = { addEventListener: function (type, fn) { listeners.push(type); } };
  const mirror = c.mirror !== false;
  const q = new URLSearchParams(c.query);
  const body = parser + '\n' + starter + '\n' +
    'for (var i = 0; i < calls; i++) startMirrorKeep();\n' +
    'return { keep: mirrorKeep, guard: !!mirrorKeepCtx };';
  let res;
  try {
    res = new Function('window', 'document', 'mirror', 'q', 'calls', body)(window, document, mirror, q, c.calls || 2);
  } catch (e) {
    return { threw: String(e) };
  }
  return {
    keep: res.keep, guard: res.guard, contexts: made.length,
    open: made.filter(function (x) { return !x.closed; }).length,
    sounding: made.filter(function (x) { return !x.closed; })
      .reduce(function (n, x) { return n + x.oscs.filter(function (o) { return o.started > 0; }).length; }, 0),
    gains: made.filter(function (x) { return !x.closed; }).map(function (x) { return x.gains.map(function (g) { return g.gain.value; }); }),
    hz: made.filter(function (x) { return !x.closed; }).map(function (x) { return x.oscs.map(function (o) { return o.frequency.value; }); }),
    listeners: listeners,
  };
}

const cases = JSON.parse(process.argv[2]);
const out = {};
Object.keys(cases).forEach(function (k) { out[k] = run(cases[k]); });
process.stdout.write(JSON.stringify(out));
