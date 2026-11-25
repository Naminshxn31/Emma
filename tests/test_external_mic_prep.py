"""Before an external mic is tried on the robot (owner 10 Nov 2026: "อาจจะใช้ไมค์แยกจากตัวหุ่น
เดี๋ยวค่อยทดสอบ แต่ให้เช็คให้ละเอียด แล้วดูว่ายังขาดอะไร"). docs/emma-external-mic.md.

What these hold down:
* the Event and Ceremony apps listen like the sales app on the robot (far: the measured 0.02
  floor, AGC on, no hold-speech) -- they had been left out of LISTEN_FAR_PROFILES and opened
  with the PC's 0.04 floor, the one robot.env itself says cut ~21% of real openings;
* every call says which input it opened (`mic_device`): name, rate, channels, what the browser
  applied, and the inputs it could see -- typed and cut short, after face_hello.
"""
from __future__ import annotations

from pathlib import Path

from app import session as session_module
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]
ROBOT_ENV = ROOT / "data" / "robot-server" / "robot.env"
PAGE = (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")


def _robot_env() -> dict[str, str]:
    out = {}
    for line in ROBOT_ENV.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def test_the_event_apps_listen_like_the_sales_app_on_the_robot(monkeypatch):
    env = _robot_env()
    far = {p.strip() for p in env["LISTEN_FAR_PROFILES"].split(",")}
    assert {"condo", "event", "ceremony"} <= far
    assert env["VAD_MIN_RMS_FAR"] == "0.02"
    s = Settings()                                         # field defaults are read at import: set them
    s.listen_far_profiles = frozenset(p.lower() for p in far)
    s.vad_min_rms_far = float(env["VAD_MIN_RMS_FAR"])
    s.vad_min_rms = 0.04                                   # the PC's, which the robot inherits
    for profile in ("condo", "event", "ceremony"):
        assert s.listens_far(profile) and s.call_vad_floor(profile) == 0.02, profile


def test_a_calls_input_is_logged_typed_and_short():
    row = session_module.mic_device_row({
        "label": "USB Audio Device (wireless receiver) " * 4, "rate": 48000, "channels": 2,
        "agc": True, "ec": True, "ns": False,
        "inputs": ["Default", "Bothlent", "USB Audio Device " * 5, 7, None, "a", "b", "c"]})
    assert row["label"] == ("USB Audio Device (wireless receiver) " * 4)[:60]
    assert (row["rate"], row["channels"], row["agc"], row["ec"], row["ns"]) == (48000, 2, True, True, False)
    assert row["inputs"] == ["Default", "Bothlent", ("USB Audio Device " * 5)[:40], "a"]
    bad = session_module.mic_device_row({"label": 5, "rate": "48000", "channels": True, "agc": "yes",
                                         "inputs": "Default"})
    assert bad == {"label": "", "rate": None, "channels": None, "agc": None, "ec": None, "ns": None,
                   "inputs": None}
    assert session_module.mic_device_row({"rate": 10 ** 9})["rate"] is None


def test_the_session_keeps_it_as_its_own_row():
    source = Path(session_module.__file__).read_text(encoding="utf-8")
    handler = source[source.index('if event.get("type") == "mic_state":'):]
    handler = handler[:handler.index("continue")]
    assert 'if event.get("why") == "call_open":\n' in handler
    assert 'turnlog.record("mic_device", **mic_device_row(event))' in handler


def test_the_page_reports_the_opened_input_after_face_hello():
    """From micIsUp, where the page's mic has just opened. Review 10 Nov: sent from the `ready` case
    it found no track on every call -- startMic runs AFTER `ready` -- and nothing was ever logged
    (this test then pinned that very order)."""
    ready = PAGE[PAGE.index("        case 'ready':"):]
    ready = ready[:ready.index("          break;")]
    assert "reportCallMic" not in ready
    assert ready.index("window.EmmaRobotBridge.ready(robotSender(ws));") < ready.index("startMic();")
    up = PAGE[PAGE.index("    function micIsUp() {"):]
    up = up[:up.index("\n    }\n")]
    assert "if (typeof reportCallMic === 'function') reportCallMic(ws);" in up
    start = PAGE[PAGE.index("    async function startMic() {"):PAGE.index("    function micIsUp() {")]
    assert start.index("micStream = openedStream;") < start.rindex("micIsUp();")
    fn = PAGE[PAGE.index("    function reportCallMic(sock) {"):PAGE.index("    function pageMicConstraints() {")]
    assert "why: 'call_open'" in fn and "String(track.label || '').slice(0, 60)" in fn
    assert "d.kind === 'audioinput'" in fn and ".slice(0, 6)" in fn and ".slice(0, 40)" in fn
    # what was heard or said never rides along: only the track, its settings and device names
    for word in ("transcript", "curUser", "evt.text", "heard"):
        assert word not in fn


RUN_MIC_UP = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8').replace(/\r\n/g, '\n');   // CRLF on Windows
const cut = (a, b) => src.slice(src.indexOf(a), src.indexOf(b, src.indexOf(a)));
const report = cut('    function reportCallMic(sock) {', '    function pageMicConstraints() {');
let up = src.slice(src.indexOf('    function micIsUp() {'));
up = up.slice(0, up.indexOf('\n    }\n') + 7);
const sent = [];
const ws = { readyState: 1, send: (t) => sent.push(JSON.parse(t)) };
const track = { label: 'USB Audio Device', muted: false, readyState: 'live',
  getSettings: () => ({ sampleRate: 48000, channelCount: 1, autoGainControl: true, echoCancellation: true,
                        noiseSuppression: false }) };
let micStream = process.argv[3] === 'open' ? { getAudioTracks: () => [track] } : null;
const navigator = { mediaDevices: { enumerateDevices: () => Promise.resolve([
  { kind: 'audioinput', label: 'Default' }, { kind: 'videoinput', label: 'cam' },
  { kind: 'audioinput', label: 'USB Audio Device' }]) } };
let muted = false, greetingOpensMic = false, playing = false;
const noop = () => {};
const tellServerMuted = noop, tellServerHoldWaiting = noop, setMicLabel = noop, show = noop, status = noop;
eval(report + up + '; micIsUp();');
setTimeout(() => console.log(JSON.stringify(sent)), 20);
"""


def test_the_real_code_sends_one_row_once_the_mic_is_open(tmp_path):
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: micIsUp/reportCallMic were NOT run -- only the source order is checked")
    script = tmp_path / "mic_up.cjs"
    script.write_text(RUN_MIC_UP, encoding="utf-8")
    page = ROOT / "client" / "voice-preview.html"

    def run(state):
        out = subprocess.run([node, str(script), str(page), state], capture_output=True, encoding="utf-8",
                             timeout=60)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    [frame] = run("open")
    assert frame == {"type": "mic_state", "why": "call_open", "muted": False, "state": "live",
                     "label": "USB Audio Device", "handed": False, "rate": 48000, "channels": 1,
                     "agc": True, "ec": True, "ns": False, "inputs": ["Default", "USB Audio Device"]}
    assert run("none") == [], "no page mic (the array has it): nothing to say"
