"""The room screen's copy of the robot face (owner 28 Oct: "ให้จอห้องนั้นโชว์
ตัวหน้าเหมือนในจอหุ่นเลย" / "หุ่นเปิดช่องดูอย่างเดียว"): app/face_mirror.py,
app/mirror_server.py, the session's hooks and the page's /mirror mode."""
import asyncio
import json
from pathlib import Path

import pytest

from app import face_mirror
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"


# ---- what may leave: rebuilt by type, never the guest's words ---------------------------

def test_the_guests_words_and_commands_never_reach_a_room_screen():
    for payload in (
            {"type": "user_transcript", "text": "my phone is 081", "utterance_id": "u1"},
            {"type": "choices", "items": [{"id": "x"}]},
            {"type": "display_command", "command_id": "c1", "action": "show_plan"},
            {"type": "body_command", "command_id": "c2"}, {"type": "nav_command", "command_id": "c3"},
            {"type": "error", "message": "x"}, {"type": "idle_timeout"}, {"type": "metric_turn", "turn_id": "t"},
            {"type": "attention", "bearing": 10}, {"type": "nav_unlock_result"}, "not a dict", None):
        assert face_mirror.view(payload) is None, payload
    # a tool's raw result can carry what was heard (tools/computer.py `heard`):
    # only a story, rebuilt field by field, or a clear passes
    leaky = {"type": "tool_result", "name": "x", "result": {"ok": True, "heard": "my phone is 081"}}
    assert face_mirror.view(leaky) is None
    story = {"type": "tool_result", "name": "present_story", "slide": {"x": 1},
             "result": {"ok": True, "screen": "story", "topic": "t", "story": {"template": "stack"},
                        "say": [{"en": "x"}], "instruction": "rules", "details": ["d"], "heard": "guest"}}
    out = face_mirror.view(story)
    assert out == {"type": "tool_result", "result": {"ok": True, "screen": "story", "topic": "t",
                                                    "story": {"template": "stack"}}}
    assert face_mirror.view({"type": "tool_result", "result": {"screen": "clear", "heard": "g"}}) == {
        "type": "tool_result", "result": {"screen": "clear"}}


def test_what_the_face_draws_passes():
    for kind in ("speech_started", "interrupted", "turn_complete", "heard_end"):
        assert face_mirror.view({"type": kind, "extra": "x"}) == {"type": kind}
    assert face_mirror.view({"type": "assistant_transcript", "text": "สวัสดีค่ะ"}) == {
        "type": "assistant_transcript", "text": "สวัสดีค่ะ"}
    ready = {"type": "ready", "output_rate": 24000, "profile": "condo", "quick_asks": {"a": 1},
             "hold_speech": True, "input_rate": 16000}
    assert face_mirror.view(ready) == {"type": "ready", "output_rate": 24000, "profile": "condo"}


def test_a_screen_that_joins_mid_call_gets_the_call_and_its_story():
    face_mirror.reset()
    assert json.loads(face_mirror.snapshot()[0]) == {"type": "mirror_hello", "in_call": False}
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "condo"})
    face_mirror.publish_json({"type": "tool_result", "result": {"ok": True, "screen": "story", "topic": "t",
                                                                "story": {"template": "stack"}}})
    kinds = [json.loads(x)["type"] for x in face_mirror.snapshot()]
    assert kinds == ["mirror_hello", "mirror_start", "ready", "tool_result"]
    face_mirror.publish_json({"type": "speech_started"})              # a new question owns the screen
    assert [json.loads(x)["type"] for x in face_mirror.snapshot()][-1] == "ready"
    face_mirror.call_ended()
    assert json.loads(face_mirror.snapshot()[0])["in_call"] is False


def test_a_slow_screen_drops_and_never_holds_the_robot():
    face_mirror.reset()
    queue = asyncio.Queue(maxsize=2)
    face_mirror._clients.add(queue)
    for _ in range(5):
        face_mirror.publish_audio(b"\x00\x00" * 10)                   # put_nowait, never awaits
    assert queue.qsize() == 2 and face_mirror._dropped["count"] == 3
    face_mirror.reset()
    assert not face_mirror.watching()


# ---- the listener: token once, cookie after, read only -------------------------------

@pytest.fixture
def mirror_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app import mirror_server

    monkeypatch.setattr(settings, "mirror_token", "room-secret")
    face_mirror.reset()
    with TestClient(mirror_server.app) as client:
        yield client
    face_mirror.reset()


def test_the_room_screen_needs_the_token_then_a_cookie(mirror_client):
    assert mirror_client.get("/mirror").status_code == 403
    assert mirror_client.get("/mirror?token=wrong").status_code == 403
    assert mirror_client.get("/assets/emma-story.js").status_code == 403
    page = mirror_client.get("/mirror?token=room-secret")
    assert page.status_code == 200 and "location.pathname === '/mirror'" in page.text
    cookie = page.cookies.get("emma_mirror")
    assert cookie and "room-secret" not in cookie                     # derived, never the token itself
    assert mirror_client.get("/assets/emma-story.js").status_code == 200
    assert mirror_client.get("/assets/story-stage.html").status_code == 200
    assert mirror_client.get("/plan-display-protocol.js").status_code == 200
    # only what the page needs: the call server's routes are not here
    for path in ("/preview", "/health", "/voices", "/data-console", "/robot/console"):
        assert mirror_client.get(path).status_code == 404, path


def test_the_room_screens_socket_is_read_only(mirror_client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with mirror_client.websocket_connect("/ws/mirror") as ws:      # no token, no cookie
            ws.receive_text()
    mirror_client.get("/mirror?token=room-secret")
    with mirror_client.websocket_connect("/ws/mirror") as ws:
        assert json.loads(ws.receive_text()) == {"type": "mirror_hello", "in_call": False}
        ws.send_text(json.dumps({"type": "quick_ask", "id": "price"}))  # read and dropped
        face_mirror.call_started()
        assert json.loads(ws.receive_text()) == {"type": "mirror_start"}
        face_mirror.publish_json({"type": "user_transcript", "text": "guest"})
        face_mirror.publish_json({"type": "assistant_transcript", "text": "ค่ะ"})
        assert json.loads(ws.receive_text()) == {"type": "assistant_transcript", "text": "ค่ะ"}
        face_mirror.publish_audio(b"\x01\x00")
        assert ws.receive_bytes() == b"\x01\x00"


def test_no_listener_without_a_port_or_a_token(monkeypatch):
    from app import mirror_server

    monkeypatch.setattr(settings, "mirror_port", 0)
    asyncio.run(mirror_server.serve())                               # returns at once: nothing listens
    monkeypatch.setattr(settings, "mirror_port", 8099)
    monkeypatch.setattr(settings, "mirror_token", "")
    asyncio.run(mirror_server.serve())
    assert mirror_server._server["obj"] is None


def test_the_listener_does_not_take_the_servers_signals():
    import contextlib

    from app import mirror_server

    cls = mirror_server._server_class()
    assert isinstance(cls.capture_signals(object.__new__(cls)), contextlib.nullcontext)


# ---- the session: only the robot face's call, only with the switch on ----------------

def test_only_the_robot_faces_call_is_copied(monkeypatch):
    from app.session import VoiceSession

    session = VoiceSession.__new__(VoiceSession)
    session._robot_face_claim = True
    monkeypatch.setattr(settings, "mirror_port", 0)
    assert not session._mirrored()                                    # the default: nothing changes
    monkeypatch.setattr(settings, "mirror_port", 8002)
    assert session._mirrored()
    session._robot_face_claim = False                                 # the sales room's desk page
    assert not session._mirrored()
    session._robot_face_claim = True
    monkeypatch.setattr(settings, "multi_session", True)
    assert not session._mirrored()


def test_the_session_publishes_what_it_sends_its_page(monkeypatch):
    from app.session import VoiceSession

    sent = []

    class _Ws:
        async def send_text(self, text):
            sent.append(json.loads(text))

    face_mirror.reset()
    queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    monkeypatch.setattr(settings, "mirror_port", 8002)
    session = VoiceSession.__new__(VoiceSession)
    session.ws = _Ws()
    session._robot_face_claim = True
    asyncio.run(session._send_json({"type": "ready", "output_rate": 24000, "quick_asks": {}}))
    asyncio.run(session._send_json({"type": "user_transcript", "text": "guest"}))
    got = [json.loads(queue.get_nowait()) for _ in range(queue.qsize())]
    assert got == [{"type": "mirror_start"}, {"type": "ready", "output_rate": 24000}]
    assert [m["type"] for m in sent] == ["ready", "user_transcript"]    # the face itself as before
    face_mirror.reset()
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module)
    assert "face_mirror.publish_audio(event.audio)" in src
    assert "face_mirror.call_ended()" in src


# ---- the page in /mirror mode -----------------------------------------------------------

def test_the_room_screens_page_never_dials_listens_or_sends():
    html = PAGE.read_text(encoding="utf-8")
    assert "var mirror = location.pathname === '/mirror';" in html
    watch = html.split("function startMirror() {", 1)[1].split("function startMirrorAudio()", 1)[0]
    assert "'/ws/mirror'" in watch and "'/ws?" not in watch
    assert "sock.send = function () {};" in watch                    # every upstream frame: nowhere
    assert "if (t === 'user_transcript') return;" in watch           # belt: the server sends none
    assert "function startCall() {\n      if (mirror) return;" in html
    mic = html.split("async function startMic() {", 1)[1]
    assert mic.lstrip().startswith("// the room screen (/mirror): her voice to play, never a microphone\n"
                                   "      if (mirror) { startMirrorAudio(); return; }")
    audio = html.split("function startMirrorAudio() {", 1)[1].split("function mirrorIdle()", 1)[0]
    assert "getUserMedia" not in audio and "startMic" not in audio
    assert "if (cueOff || !audioCtx || mirror) return;" in html     # no "your turn" chime
    # muted, but never exact zeros: Chrome stops rendering a silent stream and
    # the clock her pictures wait on stalled (live 28 Oct: 16 s behind the robot)
    assert "if (mirror) playbackBaseGain = mirrorSound ? 1 : 0.0001;" in html
    boot = html.split("// ---- boot: mirror index.html's /voices + /health handshake ----", 1)[1]
    assert boot.index("if (mirror) {") < boot.index("fetch('/voices')")
    # the story frame is loaded at boot (live 28 Oct: the first story's opening beat was an empty frame)
    assert "if (mirror) { forwardStoryEngine(); mirrorStage(); mirrorIdle(); startMirror(); return; }" in boot


def test_the_story_frame_is_laid_out_for_the_tv():
    """Owner 29 Oct, of the room TV: "ทำเรื่องเล่าแบบแนวนอน" -- the frame is a
    landscape stage the engine lays the same story out on (play's
    layout: 'land'), the robot's portrait screen only for a template with no
    landscape layout; and "ตอนไม่มีภาพ ก็ให้ตัวหุ่นอยู่กลางจอ"."""
    stage = (ROOT / "client" / "assets" / "story-stage.html").read_text(encoding="utf-8")
    # the frame follows the size the page gives it (landscape or portrait)
    assert "html, body { margin: 0; width: 100%; height: 100%;" in stage
    html = PAGE.read_text(encoding="utf-8")
    import re

    tags = set(re.findall(r"emma-story\.(?:js|css)\?v=([\w-]+)", html))
    assert tags == set(re.findall(r"emma-story\.(?:js|css)\?v=([\w-]+)", stage)) and len(tags) == 1
    # a story with a landscape layout is laid out across the frame
    show = html.split("function showMirrorStory(story) {", 1)[1].split("\n    }\n", 1)[0]
    assert "var land = !!(eng.landscape && eng.landscape(story.template));" in show
    assert "mirrorFrameMode(land);" in show and "layout: land ? 'land' : null," in show
    assert show.index("mirrorFrameMode(land);") < show.index("eng.play(story")
    mode = html.split("function mirrorFrameMode(land) {", 1)[1].split("\n    }\n", 1)[0]
    assert "Math.round(1080 * (a[2] - a[0]) / Math.max(1, a[3] - a[1]))" in mode
    # one scale for both axes (a story is never stretched), in the screen
    # left of Emma's column
    fit = html.split("function fitMirrorStage() {", 1)[1].split("\n    }\n", 1)[0]
    assert "var k = Math.min(aw / cw, ah / ch)" in fit
    assert "scale(' + k.toFixed(4) + ')'" in fit
    area = html.split("function mirrorArea() {", 1)[1].split("\n    }\n", 1)[0]
    assert "--mirror-side" in area
    # the robot's portrait screen (no landscape layout): the band every story
    # draws in -- measured on all 63 catalog stories, every beat and the end
    # state, y 269..1741
    crop = html.split("function mirrorCrop() {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (mirrorLand) return [0, 0, fw, fh];" in crop
    assert "return [0, MIRROR_BAND.top, 1080, MIRROR_BAND.top + MIRROR_BAND.height];" in crop
    top, height = (int(x) for x in re.search(
        r"var MIRROR_BAND = \{ top: (\d+), height: (\d+) \};", html).groups())
    assert 0 <= top <= 269 and 1741 <= top + height <= 1920
    # nothing on this page clips the story: only the frame's own edge and the TV's
    css = html.split("html.mirror .mirror-stage {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in css and "width: 0; height: 0" in css
    # Emma in the middle with no picture up; in the corner while one is --
    # by one transform on her (the 3D canvas keeps its size)
    emma = html.split("html.mirror .mascot-wrap {\n", 1)[1].split("}", 1)[0]
    assert "left: 39vw" in emma and "width: 22vw" in emma and "transition: transform" in emma
    corner = html.split("html.mirror body.mirror-story .mascot-wrap {", 1)[1].split("}", 1)[0]
    assert "transform: translate(" in corner and "scale(.4773)" in corner
    assert "width" not in corner and "left" not in corner
    # the tap chips are the robot guest's buttons -- the room screen only
    # watches; and no box of its own (the story on the room screen's sky)
    assert ".es-taps { display: none !important; }" in stage
    assert "background: transparent" in stage and "color-scheme: dark" in stage
