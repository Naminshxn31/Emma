"""The hall's voice, the page's side, in a real Chromium: the cut path of /mirror (review 24 Nov).
The real mirror listener (app/mirror_server.py) runs on a local port; face_mirror is driven in the same
process on that server's loop; the page is the real client/voice-preview.html. What is checked is what the
screen DOES with a recorded line's voice: it queues all of it on its own clock, and a stop empties it."""
import asyncio
import json
import threading
import time

import numpy as np
import pytest

from app import config, face_mirror, mirror_server

RATE = 24000


def _pcm(seconds: float) -> bytes:
    rng = np.random.default_rng(3)
    return rng.integers(-3000, 3000, int(seconds * RATE)).astype("<i2").tobytes()


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: that the room screen's page stops a "
                    "recorded line's queued voice on a cut")
    pw = sync_playwright().start()
    try:
        b = pw.chromium.launch(headless=True, args=["--disable-webgl", "--disable-3d-apis",
                                                    "--autoplay-policy=no-user-gesture-required"])
    except Exception as exc:
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- NOT checked here: that the room screen's page "
                    "stops a recorded line's queued voice on a cut" % str(exc).splitlines()[0])
    yield b
    b.close()
    pw.stop()


@pytest.fixture()
def hall(monkeypatch):
    """The mirror listener on a free port, on its own loop in a thread."""
    import socket
    import uvicorn
    monkeypatch.setattr(config.settings, "mirror_token", "t0k", raising=False)
    face_mirror.reset()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    cfg = uvicorn.Config(mirror_server.app, host="127.0.0.1", port=port, lifespan="off", log_config=None)
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None
    box: dict = {}

    def run():
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started

    def on_loop(fn, *a):
        done = threading.Event()
        out: list = []

        def go():
            out.append(fn(*a))
            done.set()
        box["loop"].call_soon_threadsafe(go)
        assert done.wait(5)
        return out[0]

    yield port, on_loop
    server.should_exit = True
    t.join(5)
    face_mirror.reset()


def _open(browser, port, query=""):
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:%d/mirror?token=t0k%s" % (port, query))
    page.wait_for_function("() => !!window.EmmaMirror", timeout=15000)
    return page, errors


def _state(page):
    return page.evaluate("() => window.EmmaMirror.state()")


def _line_playing(browser, hall, cut):
    port, on_loop = hall
    page, errors = _open(browser, port)
    on_loop(face_mirror.call_started)
    on_loop(face_mirror.publish_json, {"type": "ready", "output_rate": RATE, "profile": "ceremony"})
    pcm = _pcm(30.0)
    on_loop(face_mirror.ahead, "l1", pcm)
    step = int(RATE * 0.1) * 2
    for i in range(0, 4):                          # the robot's first chunks: the whole line goes to the screen
        on_loop(face_mirror.publish_audio, pcm[i * step:(i + 1) * step])
    page.wait_for_function("() => window.EmmaMirror.state().queued > 25", timeout=15000)
    assert _state(page)["playing"] is True
    on_loop(cut)
    page.wait_for_function("() => window.EmmaMirror.state().queued < 0.5", timeout=5000)
    assert _state(page)["playing"] is False
    assert [e for e in errors if "WebGL" not in e] == []      # (no GPU here: the mascot cannot draw)
    page.close()


def test_the_page_empties_a_30_second_line_on_interrupted(browser, hall):
    _line_playing(browser, hall, lambda: face_mirror.publish_json({"type": "interrupted"}))


def test_the_page_empties_the_line_on_the_red_stop(browser, hall):
    def red_stop():                                # event_cues.cut: the subtitle's cut, and the provider's interrupted
        face_mirror.subtitle_cut()
        face_mirror.publish_json({"type": "interrupted"})
    _line_playing(browser, hall, red_stop)


def test_the_page_empties_the_line_on_speech_started_with_the_robot_still_pushing(browser, hall):
    port, on_loop = hall

    def cut():
        face_mirror.publish_json({"type": "speech_started"})
    _line_playing(browser, hall, cut)


def _first_word(on_loop, pcm):
    on_loop(face_mirror.call_started)
    on_loop(face_mirror.publish_json, {"type": "ready", "output_rate": RATE, "profile": "ceremony"})
    on_loop(face_mirror.ahead, "l1", pcm)
    step = int(RATE * 0.1) * 2
    for i in range(4):
        on_loop(face_mirror.publish_audio, pcm[i * step:(i + 1) * step])


@pytest.mark.parametrize("flag,sound,gain", [(True, "1", 0.0001), (False, "1", 1.0), (True, "0", 0.0001)])
def test_the_ceremony_screen_plays_her_voice_at_the_silent_gain_only_when_the_robot_speaks(browser, hall, monkeypatch,
                                                                                         flag, sound, gain):
    port, on_loop = hall
    monkeypatch.setattr(config.settings, "ceremony_voice_on_robot", flag)
    page, _errors = _open(browser, port, "&sound=" + sound)
    page.wait_for_function("() => window.EmmaMirror.state().socket === 1", timeout=5000)
    _first_word(on_loop, _pcm(10.0))
    page.wait_for_function("() => window.EmmaMirror.state().queued > 5", timeout=15000)
    got = _state(page)["voiceGain"]
    assert got == pytest.approx(gain, rel=1e-3), got
    page.close()


def test_a_ready_that_comes_after_her_voice_began_turns_it_silent_and_back(browser, hall, monkeypatch):
    port, on_loop = hall
    monkeypatch.setattr(config.settings, "ceremony_voice_on_robot", False)
    page, _errors = _open(browser, port, "&sound=1")
    page.wait_for_function("() => window.EmmaMirror.state().socket === 1", timeout=5000)
    _first_word(on_loop, _pcm(10.0))
    page.wait_for_function("() => window.EmmaMirror.state().voiceGain === 1", timeout=15000)
    monkeypatch.setattr(config.settings, "ceremony_voice_on_robot", True)
    on_loop(face_mirror.publish_json, {"type": "ready", "output_rate": RATE, "profile": "ceremony"})
    page.wait_for_function("() => window.EmmaMirror.state().voiceGain < 0.001", timeout=5000)
    on_loop(face_mirror.publish_json, {"type": "ready", "output_rate": RATE, "profile": "condo"})
    page.wait_for_function("() => window.EmmaMirror.state().voiceGain === 1", timeout=5000)
    page.close()
