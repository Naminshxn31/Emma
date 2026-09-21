"""
The camera opening the robot's mouth.

`facewatch` decides *whether* somebody is there; this is everything that
happens after — the loop, the switch that keeps it off in the showroom, and
the single road to the model that four earlier event sources each found a
different way to get wrong.
"""
from __future__ import annotations

import asyncio

import numpy as np

from app import facewatch, greeter
from app.config import settings


def _sighting(kind="known", name="ต้า", group="staff", score=0.61):
    return facewatch.Sighting(kind=kind, name=name, score=score,
                              meta={"group": group})


# -- what the model is told -------------------------------------------------

def test_a_known_face_is_greeted_by_name():
    text = greeter.greeting_for(_sighting())
    assert "ต้า" in text


def test_the_robot_is_told_not_to_narrate_the_camera():
    """"ระบบจดจำใบหน้าตรวจพบคุณ..." is a thing to hear from a robot you have
    just walked up to, and the model reaches for it unprompted.

    Quoting the sentence rather than describing the rule, because this
    project has measured the difference: "ห้ามเกริ่นล่วงหน้า" did nothing
    and quoting the exact wrong words worked.
    """
    for kind in ("known", "stranger"):
        text = greeter.greeting_for(_sighting(kind=kind))
        assert "ห้ามพูดถึงกล้อง" in text
    assert "ระบบจดจำใบหน้าตรวจพบ" in greeter.greeting_for(_sighting())


def test_a_stranger_is_greeted_without_being_told_they_are_a_stranger():
    text = greeter.greeting_for(_sighting(kind="stranger", name=None, group=""))
    assert "ห้ามบอกว่าจำไม่ได้" in text.replace(" ", "") or "จำไม่ได้" in text
    assert "ยังไม่รู้ว่าเป็นใคร" in text


# -- the switch -------------------------------------------------------------

def test_nothing_watches_unless_the_machine_was_told_to(monkeypatch):
    """The showroom default must stay byte-identical after a git pull.

    Every face that gets through opens a Gemini session, which is the one
    thing the wake word exists to prevent.
    """
    monkeypatch.setattr(settings, "face_enabled", False)
    monkeypatch.setattr(greeter, "_task", None)

    async def go():
        greeter.start()
        return greeter._task

    assert asyncio.run(go()) is None


def test_the_camera_is_never_opened_without_a_gallery(monkeypatch):
    """A lens held open to feed a decision nobody can make is a light on the
    front of the machine for no reason."""
    opened = []

    from app import camera

    monkeypatch.setattr(facewatch.Watcher, "from_settings",
                        classmethod(lambda cls: None))
    monkeypatch.setattr(camera, "open_camera",
                        lambda *a, **k: opened.append(1))

    asyncio.run(greeter.run())
    assert opened == []


# -- the loop ---------------------------------------------------------------

class _Cap:
    """A camera that hands over `frames` and then stops."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.released = False

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        self.released = True


def _run_with(monkeypatch, sightings, frames=3, **conf):
    from app import camera, events

    announced = []

    async def fake_announce(text, *, source, **kw):
        announced.append((source, text, kw.get("summon"),
                          kw.get("arm_greeting")))
        return True

    seen = list(sightings)
    cap = _Cap([np.zeros((8, 8, 3), np.uint8)] * frames)

    class _Watcher:
        threshold, cooldown_s = 0.45, 600.0
        someone_at = None            # the pre-ring reads this

        def see(self, frame, now=None):
            return seen.pop(0) if seen else None

    monkeypatch.setattr(facewatch.Watcher, "from_settings",
                        classmethod(lambda cls: _Watcher()))
    monkeypatch.setattr(camera, "open_camera", lambda *a, **k: cap)
    monkeypatch.setattr(events, "announce", fake_announce)
    monkeypatch.setattr(settings, "face_fps", 100.0)
    for key, value in conf.items():
        monkeypatch.setattr(settings, key, value)

    asyncio.run(greeter.run())
    return announced, cap


def test_seeing_somebody_known_summons_a_session(monkeypatch):
    """There is usually no session at all: the line is asleep.

    `summon=True` is the difference between a greeting and a log line —
    it rings the standby browser and delivers on the fresh connection.
    """
    announced, _ = _run_with(monkeypatch, [_sighting()])

    assert len(announced) == 1
    source, text, summon, arm_greeting = announced[0]
    assert source == "face_known"
    assert summon is True
    assert arm_greeting is True
    assert "ต้า" in text


def test_a_stranger_summons_too_when_allowed(monkeypatch):
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=True)
    assert [a[0] for a in announced] == ["face_stranger"]


def test_a_stranger_can_be_left_alone(monkeypatch):
    """A camera aimed at a corridor with this on is a bill."""
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=False)
    assert announced == []


def test_a_known_face_still_speaks_when_strangers_are_off(monkeypatch):
    announced, _ = _run_with(monkeypatch, [_sighting()], face_greet_strangers=False)
    assert [a[0] for a in announced] == ["face_known"]


def test_the_camera_is_released_when_the_loop_ends(monkeypatch):
    """A held lens keeps the light on and stops the enrolment station
    opening the same device."""
    _, cap = _run_with(monkeypatch, [])
    assert cap.released is True


def test_a_camera_that_stops_delivering_frames_gives_up_out_loud(monkeypatch, caplog):
    """Unplugged mid-day. The loop must not spin on None for ever, and must
    not stop silently either — "the robot stopped greeting people" has to be
    answerable from the log."""
    announced, cap = _run_with(monkeypatch, [], frames=0)

    assert cap.released is True
    assert any("stopped returning frames" in r.message for r in caplog.records)


def test_recognition_never_runs_on_the_event_loop(monkeypatch):
    """100-200ms of ONNX per frame, in a process also carrying live audio.

    Read from the source rather than timed: a test that measured latency
    would pass on a fast machine on a good day.
    """
    from pathlib import Path

    source = Path(greeter.__file__).read_text(encoding="utf-8")
    assert "asyncio.to_thread(watcher.see" in source
    assert "asyncio.to_thread(cap.read)" in source
    assert "asyncio.to_thread(camera.open_camera)" in source


def test_the_greeting_goes_through_the_one_road_to_the_model():
    """Not `provider.send_text`. Everything the four earlier event sources
    learned — wait for the audio queue, re-check afterwards, one at a time,
    never deliver to the guest who replaced the one it was meant for —
    lives in `events.announce` and nowhere else."""
    import ast
    from pathlib import Path

    source = Path(greeter.__file__).read_text(encoding="utf-8")
    assert "events.announce" in source
    # The module docstring explains the rule by naming the thing it forbids,
    # so scanning raw text fails on its own explanation — the same trap the
    # `0.0.0.0` test fell into. Read the code, not the reasoning.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    assert "send_text" not in ast.unparse(tree)


def test_stopping_cancels_the_watch(monkeypatch):
    async def go():
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(greeter, "run", forever)
        monkeypatch.setattr(settings, "face_enabled", True)
        monkeypatch.setattr(greeter, "_task", None)
        greeter.start()
        await started.wait()
        task = greeter._task
        await greeter.stop()
        return task

    task = asyncio.run(go())
    assert task.cancelled() or task.done()
    assert greeter._task is None


# -- the bell the camera rings ---------------------------------------------

def test_the_standby_socket_stays_open_for_a_camera(monkeypatch):
    """With no keyword there was nobody to call.

    `events.announce(summon=True)` rings the standby browsers, and the only
    thing that holds one is `/ws/wake` — which used to hang up whenever the
    wake word was off. A face could be recognised at the door and reach
    nothing at all.
    """
    from fastapi.testclient import TestClient

    from app import wake
    from app.main import app

    monkeypatch.setattr(settings, "wake_enabled", False)
    monkeypatch.setattr(settings, "face_enabled", True)
    client = TestClient(app)
    with client.websocket_connect("/ws/wake") as ws:
        assert ws.receive_json() == {"type": "standby_only"}
        assert len(wake._STANDBY) == 1


def test_no_camera_and_no_keyword_still_hangs_up(monkeypatch):
    """The button path is what is left, and the page has to be told."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "wake_enabled", False)
    monkeypatch.setattr(settings, "face_enabled", False)
    client = TestClient(app)
    with client.websocket_connect("/ws/wake") as ws:
        assert ws.receive_json() == {"type": "wake_unavailable",
                                     "reason": "disabled"}


def test_health_separates_listening_from_being_reachable(monkeypatch):
    """Two flags because the page does two different things with them, and
    the second one must not open a microphone."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(settings, "wake_enabled", False)
    monkeypatch.setattr(settings, "face_enabled", True)
    payload = TestClient(app).get("/health").json()["wake"]

    assert payload["ready"] is False
    assert payload["standby"] is True


def test_the_page_holds_the_socket_without_opening_a_microphone():
    """A reassuring screen over an ear that was never opened is the bug
    WAKE_DEBUG exists to find. Not worth rebuilding somewhere new."""
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    assert "standby_only" in page
    assert "if (!wakeListens) {" in page
    # And the mic is opened after that gate, not before it.
    body = page.split("async function startWakeMode")[1]
    assert body.index("if (!wakeListens) {") < body.index("openPreferredMic(")


def test_somebody_enrolled_but_not_built_is_named_at_boot(monkeypatch, caplog, tmp_path):
    """Standing at the camera unrecognised looks exactly like a threshold
    that is too high, and the answer is usually that nobody rebuilt the
    gallery. The two steps stay separate; the gap gets a name in the log.
    """
    import logging

    from app import enrollment, faces

    class _Gallery:
        names = ["ต้า"]

    class _Watcher:
        gallery = _Gallery()

    monkeypatch.setattr(enrollment, "enrolled",
                        lambda: [("ต้า", 5), ("โชกุน", 5)])
    with caplog.at_level(logging.WARNING):
        greeter._say_who_is_missing(_Watcher())

    said = " ".join(r.message for r in caplog.records)
    assert "โชกุน" in said
    assert "ต้า" not in said          # already in the gallery, nothing to say
    assert "build-face-gallery" in said


def test_the_launcher_turns_the_camera_on_without_editing_the_env_file():
    """`load_dotenv()` does not override what is already in the environment,
    which is what lets a double-click mean "this run" and not "from now on".
    The showroom default has to survive a git pull unchanged."""
    from pathlib import Path

    cmd = Path("start-emma-camera.cmd").read_text(encoding="utf-8")
    assert "set FACE_ENABLED=true" in cmd
    assert "run_server.py --open" in cmd


def test_the_launcher_opens_the_origin_the_microphone_was_granted_to():
    """Two facts, both measured, both invisible from the page.

    The printed LAN link is not a secure origin and can never hold a mic
    grant. And `127.0.0.1` is a *different origin* from `localhost`, so it
    carries none of the permissions given to it — Chrome's own settings on
    the showroom machine list `http://localhost:8001` as allowed and do not
    mention `http://127.0.0.1:8001` at all. Opening the second one cost the
    first real camera greeting.
    """
    from pathlib import Path

    source = Path("run_server.py").read_text(encoding="utf-8")
    assert '_open_when_up(f"{scheme}://localhost:{settings.port}/{q}")' in source
    assert '_open_when_up(f"{scheme}://127.0.0.1' not in source


def test_the_server_never_prints_or_access_logs_url_tokens():
    from pathlib import Path

    source = Path("run_server.py").read_text(encoding="utf-8")
    assert "access_log=False" in source
    assert 'print(f"  robot kiosk' not in source
    assert 'print(f"  robot screen' not in source
    assert 'print(f"  talk to Emma : {base}/{q}")' not in source


def test_websocket_logging_redacts_a_query_token():
    import logging
    import run_server

    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
        '%s - "WebSocket %s"', ("client", "/ws/wake?token=secret-value&x=1"), None)
    assert run_server._TokenRedactionFilter().filter(record) is True
    assert "secret-value" not in record.getMessage()
    assert "token=***&x=1" in record.getMessage()


# -- why nothing was heard --------------------------------------------------

def test_an_empty_room_of_browsers_says_so(monkeypatch, caplog):
    """Two different failures wore one line.

    The first real camera greeting recognised somebody at 0.617 and was
    dropped, and the log said only "no live session" — which is equally true
    when a page is open and cannot dial. Naming the missing piece is the
    difference between a five-second fix and an afternoon.

    Since the pre-ring, zero rings still gets the dial-in poll (a browser
    answering an *earlier* ring has already dropped its standby socket), so
    the wait is shortened here rather than sat through.
    """
    import logging

    from app import events, wake
    from app import session as session_module

    async def go():
        return await events.announce("x", source="face_known", summon=True)

    monkeypatch.setattr(session_module, "_active", None)
    monkeypatch.setattr(events, "SUMMON_WAIT_S", 0.3)

    async def nobody(reason="server"):
        return 0

    monkeypatch.setattr(wake, "summon", nobody)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(go()) is False

    said = " ".join(r.message for r in caplog.records)
    assert "no standby browser rang" in said


def test_a_ring_answered_before_this_one_still_gets_the_greeting(monkeypatch):
    """The pre-ring means the browser may already be dialling when the
    announcement rings — its standby socket is gone, so the ring lands on
    nobody, and the old code dropped the greeting on that evidence alone.
    The session that then appears seconds later is the answer to the first
    ring, and it must be spoken into."""
    from app import display, events, wake
    from app import session as session_module

    class _Provider:
        def __init__(self):
            self.texts = []

        async def send_text(self, text):
            self.texts.append(text)

    class _Session:
        provider = _Provider()

    async def go():
        monkeypatch.setattr(session_module, "_active", None)

        async def nobody(reason="server"):
            return 0

        async def heard(**kw):
            return 0.0

        monkeypatch.setattr(wake, "summon", nobody)
        monkeypatch.setattr(display, "wait_until_heard", heard)

        async def dial_in():
            await asyncio.sleep(0.1)
            session_module._active = _Session()

        asyncio.get_running_loop().create_task(dial_in())
        return await events.announce("ทัก", source="face_known", summon=True)

    try:
        assert asyncio.run(go()) is True
    finally:
        session_module._active = None
    assert _Session.provider.texts == ["ทัก"]


def test_a_refused_microphone_does_not_take_the_camera_down_with_it():
    """The door camera rings the same socket and needs no microphone.

    Collapsing the two meant a machine where the mic permission was not
    granted could recognise a face and have nowhere to send the greeting.
    """
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    catch = page.split("// No permission for the *microphone*")[1].split("}")[0]
    assert "wakeCanBeRung" in catch
    # And it must not close the socket on that path.
    assert "stopWakeMode()" not in catch.split("return;")[0]


def test_a_refused_microphone_still_lets_emma_be_heard():
    """Two directions, one of them denied.

    The first camera greeting that worked end to end arrived as text on a
    silent page: `startMic` returned on the permission error *before* the
    AudioContext existed, so there was nowhere to play. A robot that can
    greet you by name but cannot hear you is the entire point of a door
    camera; a robot that does neither is not.
    """
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    body = page.split("async function startMic()")[1].split("\nfunction ")[0]
    catch = body.split("} catch (e) {")[1].split("}")[0]

    # The permission failure records the fact and falls through to the
    # audio graph; only a call that ended during permission may return.
    assert "micStream = null" in catch
    assert "return" not in catch.replace("if (audioCtx !== callCtx) return;", "")
    assert body.index("audioCtx = new") < body.index("if (!micStream) {")
    # And the banner names the browser's actual error, because "denied"
    # covered five different failures while every permission read Allow.
    assert "mic failed:" in body
    assert "NotReadableError" in body


def test_the_one_permanent_button_keeps_its_own_colours():
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    assert "#endBtn { background: #0d0d0d; color: #fff; }" in page
    assert "#endBtn { background: #1c1c22; color: #fff; }" in page


# -- the page has to parse ---------------------------------------------------

def test_every_page_script_actually_parses():
    """A syntax error in the page is invisible from Python and total.

    Measured, on this feature: one line of Python-style string concatenation
    ('a' 'b' with no +) went into the client, every one of the 942 tests
    stayed green, and the showroom machine served a page whose JavaScript
    never ran at all — no /health, no /voices, no goToSleep. What the owner
    saw was a half-drawn setup screen with a dead button, which looks like a
    server problem and is not one. Nothing in this suite could tell.
    """
    import shutil
    import subprocess
    import re
    import tempfile
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        import pytest

        pytest.skip("node not available — the pages' JavaScript is UNPARSED "
                    "here, so a syntax error in client/*.html would ship")

    for name in ("index.html", "display.html"):
        page = Path("client") / name
        if not page.is_file():
            continue
        html = page.read_text(encoding="utf-8")
        for i, block in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8",
                                             delete=False) as handle:
                handle.write(block)
                path = handle.name
            try:
                done = subprocess.run([node, "--check", path],
                                      capture_output=True, text=True,
                                      encoding="utf-8", timeout=30)
            finally:
                Path(path).unlink(missing_ok=True)
            assert done.returncode == 0, f"{name} script #{i}:\n{done.stderr}"


def test_a_dead_browser_socket_is_not_reported_as_a_crash():
    """WinError 10054 on teardown, on every page reload, with a traceback.

    It has been read as the robot failing more than once in this project's
    own logs. Only ConnectionResetError is swallowed; anything else still
    reaches the default handler.
    """
    import asyncio

    from app.main import _quiet_connection_resets

    async def go():
        loop = asyncio.get_running_loop()
        _quiet_connection_resets(loop)
        handler = loop.get_exception_handler()

        passed_on = []
        loop.default_exception_handler = lambda ctx: passed_on.append(ctx)

        handler(loop, {"exception": ConnectionResetError(10054, "reset")})
        assert passed_on == []          # the noise is dropped

        handler(loop, {"exception": RuntimeError("real")})
        assert len(passed_on) == 1      # everything else still surfaces

    asyncio.run(go())


def test_a_camera_wake_flushes_no_standby_tail():
    """Nobody spoke, so there is nothing to preserve — only noise to send.

    The keyword path buffers the audio right after the name ("เอ็มม่า เปิด
    ไฟหน่อย" must not lose its second half) and flushes it into the new
    session. A server-rung wake reused that path wholesale, so a camera
    summon flushed seconds of empty-room audio through the VAD's grace
    window, and the transcriber answered with a stock sentence: a guest
    turn reading "I'm going to go to the bathroom." that no one said,
    which Emma then politely answered. The server's ring carries `reason`;
    a real keyword hit does not — that field is the difference between
    words and noise.
    """
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    handler = page.split("if (evt.type === 'wake')")[1].split("} else if")[0]
    assert "if (!evt.reason && wakeWorklet)" in handler


def test_the_server_ring_still_carries_its_reason():
    """The page's whole defence keys off this field existing."""
    import inspect

    from app import wake

    assert '"reason": reason' in inspect.getsource(wake.summon)


def test_somebody_close_rings_before_anybody_is_confirmed(monkeypatch):
    """The dial overlaps the confirmation instead of following it.

    Confirming takes ~1.2s and the Gemini dial 2-3s; run in sequence the
    robot greeted people's backs (measured 2026-08-31: ~5-7s from doorway
    to first word). The ring is cheap, fires only when no session is up,
    and at most once per 15s.
    """
    import time as _t

    from app import camera, events, wake
    from app import session as session_module

    rings = []

    async def ring(reason="server"):
        rings.append(reason)
        return 1

    cap = _Cap([np.zeros((8, 8, 3), np.uint8)] * 4)

    class _Watcher:
        threshold, cooldown_s = 0.45, 600.0
        someone_at = None

        def see(self, frame, now=None):
            self.someone_at = _t.monotonic()   # a face, not yet confirmed
            return None

    async def no_announce(*a, **k):
        raise AssertionError("nothing was confirmed, nothing may announce")

    monkeypatch.setattr(facewatch.Watcher, "from_settings",
                        classmethod(lambda cls: _Watcher()))
    monkeypatch.setattr(camera, "open_camera", lambda *a, **k: cap)
    monkeypatch.setattr(wake, "summon", ring)
    monkeypatch.setattr(events, "announce", no_announce)
    monkeypatch.setattr(session_module, "_active", None)
    monkeypatch.setattr(settings, "face_fps", 100.0)

    asyncio.run(greeter.run())

    assert rings == ["face_approach"]      # once, not once per frame


# -- one greeting per arrival, not two ---------------------------------------

def test_a_summoned_session_has_no_opening_line_of_its_own():
    """The event that rang the page delivers the greeting; the provider's
    own opening line on top of it had the second cutting the first off
    mid-word — "หวัดดีค่ะ" faded, then "สวัสดีค่ะ มีอะไรให้ช่วยไหมคะ". The
    robot interrupting itself, with HALF_DUPLEX on and nobody else talking.
    """
    from app import session as session_module

    class _WS:
        pass

    plain = session_module.VoiceSession(_WS(), provider_name="gemini")
    rung = session_module.VoiceSession(_WS(), provider_name="gemini", summoned=True)
    assert plain.summoned is False
    assert rung.summoned is True

    # And the flag is what decides the greeting, read from the source so
    # the decision cannot drift back into "always".
    import inspect

    src = inspect.getsource(session_module.VoiceSession.run)
    assert "greeting=None if self.summoned else greeting_for(self.profile)" in src
    assert "if not self.summoned:" in src
    assert "await robot_arm.greet()" in src


def test_the_page_tells_the_server_when_a_dial_was_rung():
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    assert "summonedDial = !!evt.reason;" in page
    assert "summonedDial ? '&summoned=1' : ''" in page
    # Consumed by the dial, so a later button press does not inherit it.
    dial = page.split("summonedDial ? '&summoned=1'")[1][:200]
    assert "summonedDial = false" in dial


def test_the_socket_passes_the_flag_through(monkeypatch):
    from app import main as main_module

    seen = {}

    async def fake_handle(ws, **kw):
        seen.update(kw)

    monkeypatch.setattr(main_module, "handle_connection", fake_handle)

    class _WS:
        pass

    async def go():
        await main_module.ws_endpoint(_WS(), voice="Kore", summoned="1")

    monkeypatch.setattr(main_module, "_reject_unauthorized",
                        lambda ws, token: _false())
    asyncio.run(go())
    assert seen["summoned"] is True


async def _false():
    return False


# -- speak first, then listen -------------------------------------------------

class _MicWS:
    """A browser that sends some mic chunks and then hangs up."""

    def __init__(self, chunks):
        self._queue = [{"bytes": c} for c in chunks] + [{"type": "websocket.disconnect"}]

    async def receive(self):
        return self._queue.pop(0)


class _Ears:
    """A provider that only records what audio reached it."""

    def __init__(self):
        self.heard = []
        self.floor_down = False

    async def send_audio(self, data):
        self.heard.append(data)

    def stand_down_floor(self):
        self.floor_down = True


def _pump(session, chunks):
    import asyncio

    session.ws = _MicWS(chunks)
    asyncio.run(session._browser_to_provider())
    return session.provider.heard


def test_a_summoned_session_is_deaf_until_its_greeting_has_been_heard(monkeypatch):
    """Rung by the camera, the session had its mic live 2-3s before the
    greeting was even generated, so whatever the room said in that window
    became the first turn — ahead of the greeting Emma was rung to give.
    The owner: "ตื่นแล้วทักทาย ให้ emma พูดก่อนค่อยฟังคนพูด". Held until the
    browser's audio queue for the greeting runs dry, the same clock every
    screen waits on."""
    from app import display
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess.provider = _Ears()
    assert _pump(sess, [b"\x01" * 320, b"\x02" * 320]) == [], \
        "mic audio reached the model before the greeting"

    # The greeting turn completes with 0s of audio still queued -> open.
    monkeypatch.setattr(display, "remaining_lead", lambda: 0.0)
    sess._greeting_turn_done()
    assert _pump(sess, [b"\x03" * 320]) == [b"\x03" * 320]


def test_the_ears_wait_for_the_greeting_to_be_heard_not_generated(monkeypatch):
    """turn_complete means the model stopped *generating*; the browser still
    has seconds of greeting queued. Opening on turn_complete would be the
    audio-lead bug wearing a microphone."""
    from app import display
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess.provider = _Ears()
    monkeypatch.setattr(display, "remaining_lead", lambda: 30.0)
    sess._greeting_turn_done()
    assert _pump(sess, [b"\x04" * 320]) == [], \
        "opened while the greeting was still playing"


def test_a_summoned_session_that_never_gets_its_greeting_listens_anyway():
    """The announcement can be dropped (`still_relevant` false, the person
    walked off and back). A session that stays deaf forever is the same bug
    with the sign flipped, so the hold has a ceiling — and the log says
    which of the two ways it opened."""
    import time

    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess.provider = _Ears()
    assert sess._ears_closed_until is not None
    assert sess._ears_closed_until - time.monotonic() <= session_module.VoiceSession.SUMMONED_HOLD_MAX_S
    sess._ears_closed_until = time.monotonic() - 1     # ceiling passed
    assert _pump(sess, [b"\x05" * 320]) == [b"\x05" * 320]


def test_a_plain_session_hears_from_the_first_byte():
    """Wake word and button sessions are opened *by* the person talking;
    holding their first words back would behead the utterance that opened
    the session — the wake-tail bug all over again."""
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Ears()
    assert sess._ears_closed_until is None
    assert _pump(sess, [b"\x06" * 320]) == [b"\x06" * 320]


def test_turn_complete_is_what_ends_the_hold():
    """Read from the source: the hook must sit in the turn_complete branch,
    before the browser is told the turn is over."""
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession._provider_to_browser)
    branch = src.split('event.kind == "turn_complete"', 1)[1]
    assert "self._greeting_turn_done()" in branch.split('await self._send_json({"type": "turn_complete"})', 1)[0]


def test_a_summoned_session_only_bypasses_the_floor_when_configured():
    """The floor exists to ignore people not talking to us. A session the
    camera opened just greeted, by name, somebody standing at the door —
    exactly where the floor says nobody worth hearing stands. 2026-08-31:
    อาซู่ answered from the doorway and the session logged no `heard` at
    all; VAD_MIN_RMS=0.01 is already at the top of the owner's own desk
    readings (0.004-0.012)."""
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession.run)
    after = src.split("self.provider = provider", 1)[1]
    assert "if self.summoned and settings.vad_summoned_bypass:" in after[:500]
    assert "provider.stand_down_floor()" in after[:600]


def test_the_gemini_provider_forwards_stand_down_to_its_gate():
    from app.providers import gemini

    class _Gate:
        down = False

        def stand_down(self):
            self.down = True

    prov = gemini.GeminiProvider.__new__(gemini.GeminiProvider)
    prov._vad_gate = _Gate()
    prov.stand_down_floor()
    assert prov._vad_gate.down
    prov._vad_gate = None
    prov.stand_down_floor()             # no gate: nothing to stand down, no error


# -- the call's microphone leaves evidence, like the standby's does ------------

def _session_with_ears(monkeypatch, segments):
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Ears()
    sess.provider._vad_gate = type("G", (), {"segments": segments})()
    monkeypatch.setattr(session_module.VoiceSession, "MIC_REPORT_S", 0.0)
    return sess


def test_all_zero_mic_audio_is_called_digital_silence_not_quiet(monkeypatch, caplog):
    """2026-08-31: camera greeted, the browser's bytes reached the pump,
    and then nothing — no floor line, no `heard`. A real microphone never
    reads an exact zero; all-zero PCM is a second stream on a device
    another stream still holds (the standby ears, in a handover), and the
    fix is a reload, not a louder voice. The log has to say which."""
    import logging

    sess = _session_with_ears(monkeypatch, segments=0)
    with caplog.at_level(logging.INFO, logger="condo_voice.session"):
        _pump(sess, [b"\x00" * 640, b"\x00" * 640])
    lines = [r.getMessage() for r in caplog.records if "call audio" in r.getMessage()]
    assert lines and "DIGITAL SILENCE" in lines[-1]


def test_loud_audio_with_no_vad_segment_points_at_the_detector(monkeypatch, caplog):
    import logging

    loud = (int(0.3 * 32767)).to_bytes(2, "little", signed=True) * 320
    sess = _session_with_ears(monkeypatch, segments=0)
    with caplog.at_level(logging.INFO, logger="condo_voice.session"):
        _pump(sess, [loud, loud])
    lines = [r.getMessage() for r in caplog.records if "call audio" in r.getMessage()]
    assert lines and "Silero opened no segment" in lines[-1]
    assert "vad segments=0" in lines[-1]


def test_quiet_audio_is_reported_as_too_quiet(monkeypatch, caplog):
    import logging

    quiet = b"\x30\x00" * 320
    sess = _session_with_ears(monkeypatch, segments=0)
    with caplog.at_level(logging.INFO, logger="condo_voice.session"):
        _pump(sess, [quiet, quiet])
    lines = [r.getMessage() for r in caplog.records if "call audio" in r.getMessage()]
    assert lines and "too quiet" in lines[-1]


def test_the_report_sits_on_the_forwarding_path_not_before_the_hold():
    """Bytes dropped by the greeting hold are not "the microphone": what is
    reported is exactly what the model is offered."""
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession._browser_to_provider)
    block = src.split('message.get("bytes")', 1)[1].split("continue\n", 2)
    assert "self._mic_report(data)" in src
    assert src.index("self._mic_report(data)") > src.index('turnlog.record("ears_open"')
    assert src.index("self._mic_report(data)") < src.index("await self.provider.send_audio(data)")


def test_a_handover_hands_the_standby_stream_to_the_call():
    """Measured 2026-09-01: a camera-rung call whose fresh getUserMedia
    stream delivered exact zeros for the whole session (`call audio:
    peak=0.0000`, 251/251 frames per report), and releasing the standby's
    tracks *before* asking again changed nothing. The standby already
    holds a live stream from this device; the call takes that one and
    never opens the device a second time."""
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    start_mic = page.split("async function startMic()", 1)[1].split("function onMessage", 1)[0]
    handed = start_mic.split("const handed =", 1)[1]
    assert "micStream = wakeMicStream;" in handed
    assert "wakeMicStream = null;" in handed
    # The fresh open is the fallback, taken only when there is nothing to hand over.
    assert handed.index("micStream = wakeMicStream;") < handed.index("openPreferredMic(")
    # And the earlier attempt is gone: no stopping standby tracks at the ring.
    ring = page.split("summonedDial = !!evt.reason;", 1)[1].split("startCall();", 1)[0]
    assert "getTracks().forEach((t) => t.stop())" not in ring


def test_the_browser_reports_which_microphone_the_call_is_on():
    """The browser half of the `call audio:` line — all-zero PCM has
    several causes and only the browser can say whether the OS muted the
    track. Sent on start and on every mute/unmute."""
    from pathlib import Path

    page = Path("client/index.html").read_text(encoding="utf-8")
    assert "function reportMicState(handed)" in page
    assert "type: 'mic_state'" in page
    assert "t.onmute = () =>" in page
    start_mic = page.split("async function startMic()", 1)[1].split("function onMessage", 1)[0]
    assert "reportMicState(handed);" in start_mic


def test_the_server_logs_the_mic_state_into_the_turn_log(monkeypatch):
    import asyncio
    import json

    from app import session as session_module
    from app import turnlog

    seen = []
    monkeypatch.setattr(turnlog, "record", lambda ev, **f: seen.append((ev, f)))

    class _WS:
        def __init__(self):
            self._q = [{"text": json.dumps({"type": "mic_state", "why": "start",
                                            "label": "USB mic", "muted": True,
                                            "state": "live", "handed": True})},
                       {"type": "websocket.disconnect"}]

        async def receive(self):
            return self._q.pop(0)

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Ears()
    sess.ws = _WS()
    asyncio.run(sess._browser_to_provider())
    assert ("mic_state", {"why": "start", "muted": True, "state": "live", "handed": True}) in seen


# -- the microphone check below the browser ------------------------------------

def test_check_mic_verdicts_separate_dead_from_silent_from_alive():
    """2026-09-01: two browser-side fixes changed nothing because the USB
    microphone was delivering no frames to any program. The verdict that
    would have said so in one line is this one."""
    import numpy as np

    from scripts import check_mic

    assert check_mic.classify(None, 96000).startswith("NO FRAMES")
    assert check_mic.classify(np.zeros(0, dtype="float32"), 96000).startswith("NO FRAMES")
    assert check_mic.classify(np.zeros(96000, dtype="float32"), 96000).startswith("SILENT")
    noise = np.full(96000, 0.002, dtype="float32")
    assert check_mic.classify(noise, 96000) == "alive"
    assert check_mic.classify(noise[:1000], 96000).startswith("stalling")


def test_the_launcher_runs_the_mic_check():
    from pathlib import Path

    cmd = Path("check-mic.cmd").read_text(encoding="utf-8")
    assert "scripts\\check_mic.py" in cmd
    assert "pause" in cmd


# -- a stranger wakes the line, a stranger does not interrupt it --------------

def test_a_stranger_does_not_cut_into_a_live_conversation(monkeypatch):
    """2026-09-01, one session: three "strangers" at 0.355/0.469/0.478 were
    the two people already talking, caught turning their heads. Each one
    became a user turn and Emma broke off mid-sentence to greet nobody."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", object())
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=True)
    assert announced == []


def test_a_known_face_still_gets_a_name_mid_conversation(monkeypatch):
    """The other half: "หวัดดี พีท" while โชกุน is talking carries a name
    somebody may want to hear — measured working the same morning."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", object())
    announced, _ = _run_with(monkeypatch, [_sighting()])
    assert [a[0] for a in announced] == ["face_known"]


def test_a_stranger_still_wakes_an_idle_line(monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", None)
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=True)
    assert [a[0] for a in announced] == ["face_stranger"]


# -- a line held open for the greeting is not a conversation ------------------

class _Summoned:
    summoned = True
    _greeting_turn_seen = False


class _Greeted(_Summoned):
    _greeting_turn_seen = True


def test_a_stranger_is_still_greeted_on_the_session_the_pre_ring_opened_for_them(monkeypatch):
    """The pre-ring opens the session before the face is confirmed, so a
    confirmed stranger always finds a live session — theirs. 2026-09-01
    14:5x: every stranger greeting was dropped as "interrupting", and the
    summoned session timed out with no greeting at all."""
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", _Summoned())
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=True)
    assert [a[0] for a in announced] == ["face_stranger"]


def test_once_greeted_a_summoned_session_is_a_conversation(monkeypatch):
    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", _Greeted())
    announced, _ = _run_with(monkeypatch, [_sighting(kind="stranger", name=None)],
                             face_greet_strangers=True)
    assert announced == []


def test_an_ungreeted_summoned_session_gives_up_in_thirty_seconds(monkeypatch):
    """Rung, never greeted, nobody talking: not worth two minutes of an
    open Gemini line. Measured 2026-09-01 15:xx."""
    import asyncio
    import pytest
    import time

    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess._last_heard_at = time.monotonic() - 31
    sent = []

    async def send(msg):
        sent.append(msg)

    sess._send_json = send
    monkeypatch.setattr(session_module.settings, "idle_timeout_s", 0)      # off — still ends

    async def no_sleep(s):
        pass

    monkeypatch.setattr(session_module.asyncio, "sleep", no_sleep)
    from app.tools import slides

    async def shut():
        pass

    monkeypatch.setattr(slides, "shutdown_display", shut)
    asyncio.run(sess._close_when_nobody_is_there())
    assert sent and sent[0]["type"] == "idle_timeout"

    # ...but once greeted it follows IDLE_TIMEOUT_S like any session (0 = never).
    sess2 = session_module.VoiceSession(None, provider_name="gemini", summoned=True)
    sess2._greeting_turn_seen = True
    sess2._last_heard_at = time.monotonic() - 1000
    calls = []

    async def counted_sleep(s):
        calls.append(s)
        if len(calls) > 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(session_module.asyncio, "sleep", counted_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(sess2._close_when_nobody_is_there())


# -- one announcement finishes before the next one starts ------------------------

def test_the_next_announcement_waits_for_the_previous_turn_to_complete(monkeypatch):
    """Between "text sent" and "first audio chunk back" the browser's queue
    is empty, so waiting on the queue alone let a second greeting land
    while the model was still generating the first — a barge-in by the
    robot's own hand (on screen 2026-09-01: ถูกพูดแทรก mid-greeting)."""
    import asyncio

    from app import display, events
    from app import session as session_module

    async def go():
        sess = session_module.VoiceSession(None, provider_name="gemini")
        sent = []

        class _P:
            async def send_text(self, text):
                sent.append(text)

        sess.provider = _P()
        monkeypatch.setattr(session_module, "_active", sess)

        async def heard(max_wait=25.0, then_pause=0.0):
            return 0.0

        monkeypatch.setattr(display, "wait_until_heard", heard)

        assert await events.announce("หนึ่ง", source="t1") is True
        assert not sess.turn_idle.is_set(), "the model owes a turn for the first text"

        second = asyncio.create_task(events.announce("สอง", source="t2"))
        await asyncio.sleep(0.05)
        assert sent == ["หนึ่ง"], "the second announcement went in mid-turn"
        sess.turn_idle.set()                       # turn_complete for the first
        assert await second is True
        assert sent == ["หนึ่ง", "สอง"]

    asyncio.run(go())


def test_a_turn_that_never_completes_does_not_park_announcements_forever(monkeypatch):
    import asyncio

    from app import display, events
    from app import session as session_module

    monkeypatch.setattr(events, "TURN_WAIT_S", 0.05)

    async def go():
        sess = session_module.VoiceSession(None, provider_name="gemini")
        sent = []

        class _P:
            async def send_text(self, text):
                sent.append(text)

        sess.provider = _P()
        monkeypatch.setattr(session_module, "_active", sess)

        async def heard(max_wait=25.0, then_pause=0.0):
            return 0.0

        monkeypatch.setattr(display, "wait_until_heard", heard)
        sess.turn_idle.clear()                     # a turn that never ends
        assert await events.announce("สอง", source="t2") is True
        assert sent == ["สอง"]

    asyncio.run(go())


def test_turn_complete_marks_the_session_idle_for_announcements():
    import inspect

    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession._provider_to_browser)
    branch = src.split('event.kind == "turn_complete"', 1)[1]
    assert "idle.set()" in branch.split('await self._send_json({"type": "turn_complete"})', 1)[0]
