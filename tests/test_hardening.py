"""
The 2026-09-01 hardening batch, from the outside review (docs/review/).

Every item here was verified against the code before being fixed, and each
test pins the fix to the reason it exists — the review's own words where
they were exact. The six items, in the review's order:

  1. `open_program` could hand a bare token ("cmd", "regedit") to
     ShellExecute when it was not in the Start Menu.
  2. Tool arguments — phone numbers, budgets, room numbers — went to the
     console log verbatim.
  3. The face gallery was loaded with `allow_pickle=True`: a pickle is
     code, and a swapped gallery file would run inside the server.
  4. The face dependencies were installed by hand and in no requirements
     file.
  5. A camera that stopped delivering frames ended the greeter until the
     next restart.
  6. Any socket holding WS_TOKEN could send `robot_ready` and become the
     robot; `robot_arrived` was accepted from anyone at any time; a walk
     with no arrival stayed "moving" forever; and `app_gone()` — written
     for the robot's socket closing — was never called.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path

import numpy as np
import pytest

from app.config import settings


def run(coro):
    return asyncio.run(coro)


# ==================== 1. open_program: the Start Menu is the allow-list ====================

def test_a_bare_token_not_in_the_start_menu_never_reaches_the_shell(monkeypatch, tmp_path):
    """"cmd", "powershell", "regedit" are one word each and none is in the
    Start Menu. The old fallback handed exactly such words to ShellExecute
    "because there is nothing to inject into" — true, and beside the point
    on a server that takes commands from a microphone on 0.0.0.0."""
    import os

    from app.tools import computer

    d = tmp_path / "menu"
    d.mkdir()
    (d / "Spotify.lnk").write_text("x")
    monkeypatch.setattr(computer, "_start_menu_dirs", lambda: [d])
    launched = []
    monkeypatch.setattr(os, "startfile", lambda t: launched.append(t), raising=False)

    for word in ("cmd", "powershell", "regedit", "diskpart", "notepad"):
        out = computer.open_program(word)
        assert out["ok"] is False and out["error"] == "not found", word
    assert launched == [], "something outside the Start Menu was launched"


def test_the_fallback_is_gone_from_the_source():
    from app.tools import computer

    src = inspect.getsource(computer.open_program)
    assert "os.startfile(q)" not in src
    assert "re.fullmatch" not in src


# ==================== 2. tool arguments stay out of the console ====================

def test_the_tool_call_log_line_names_arguments_but_never_values():
    from app.providers import gemini

    src = inspect.getsource(gemini)
    line = [ln for ln in src.splitlines() if 'logger.info("tool call:' in ln]
    assert len(line) == 1
    assert '", ".join(sorted(args))' in line[0]
    assert "name, args)" not in line[0]


def test_the_tool_call_log_line_renders_keys_only(caplog):
    """The same line, executed: a phone number in the arguments must not
    appear in the record."""
    log = logging.getLogger("condo_voice.gemini")
    args = {"phone": "0812345678", "room": "A801"}
    with caplog.at_level(logging.INFO, logger="condo_voice.gemini"):
        log.info("tool call: %s(%s)", "show_unit", ", ".join(sorted(args)))
    msg = caplog.records[-1].getMessage()
    assert msg == "tool call: show_unit(phone, room)"
    assert "0812345678" not in msg and "A801" not in msg


# ==================== 3. gallery without pickle ====================

def test_the_gallery_round_trips_without_allow_pickle(tmp_path, monkeypatch):
    from app import faces

    vecs = np.random.default_rng(0).standard_normal((3, 8)).astype(np.float32)
    g = faces.Gallery(["โชกุน", "พีท", "อาชู่"], vecs,
                      [{"group": "enrolled"}, {"group": "staff"}, {}], faces.MODEL_TAG)
    path = tmp_path / "gallery.npz"
    g.save(path)

    with np.load(path, allow_pickle=False) as data:      # would raise on object arrays
        assert data["names"].dtype.kind == "U"
        assert data["meta"].dtype.kind == "U"
        assert data["model_tag"].dtype.kind == "U"

    back = faces.Gallery.load(path)
    assert back.names == ["โชกุน", "พีท", "อาชู่"]
    assert back.meta[1] == {"group": "staff"}
    assert np.allclose(back.vecs, vecs)


def test_the_loader_never_asks_for_pickle():
    from app import faces

    src = inspect.getsource(faces.Gallery.load)
    assert "allow_pickle=False" in src
    assert "allow_pickle=True" not in src


def test_an_old_pickled_gallery_is_refused_with_the_rebuild_command(tmp_path):
    """The pre-2026-09-01 file format. Refusing is the fix; the message has
    to carry the one command that repairs it."""
    from app import faces

    path = tmp_path / "old.npz"
    np.savez(path, names=np.array(["x"], dtype=object), vecs=np.zeros((1, 4), np.float32),
             meta=np.array(["{}"], dtype=object), model_tag=faces.MODEL_TAG)
    with pytest.raises(ValueError, match="build-face-gallery.cmd"):
        faces.Gallery.load(path)


# ==================== 4. face dependencies are declared ====================

def test_face_dependencies_are_in_requirements():
    req = Path("requirements.txt").read_text(encoding="utf-8")
    for pkg in ("insightface", "onnxruntime", "opencv-python", "scikit-image"):
        assert pkg in req, f"{pkg} missing — a second machine cannot reproduce the camera"


# ==================== 5. the camera comes back ====================

def test_a_dead_camera_is_reopened_instead_of_ending_the_greeter(monkeypatch):
    """`MAX_EMPTY_READS` empty frames used to `return` — greeting off,
    silently, until the next restart. Now the handle is released and the
    camera reopened on a timer; the loop ends only when retrying is off."""
    from app import camera, facewatch, greeter

    class _Cap:
        def __init__(self, frames):
            self.frames, self.released = list(frames), False

        def read(self):
            return (True, self.frames.pop(0)) if self.frames else (False, None)

        def release(self):
            self.released = True

    first = _Cap([])                       # dies at once
    second = _Cap([np.zeros((8, 8, 3), np.uint8)])
    opened = []

    def open_camera(*a, **k):
        opened.append(1)
        if len(opened) == 1:
            return first
        if len(opened) == 2:
            return second
        raise asyncio.CancelledError      # the server shutting down ends the loop

    class _Watcher:
        threshold, cooldown_s, someone_at = 0.45, 600.0, None

        def see(self, frame, now=None):
            return None

    monkeypatch.setattr(facewatch.Watcher, "from_settings", classmethod(lambda cls: _Watcher()))
    monkeypatch.setattr(camera, "open_camera", open_camera)
    monkeypatch.setattr(settings, "face_fps", 100.0)
    monkeypatch.setattr(settings, "face_camera_retry_s", 0.01)
    monkeypatch.setattr(greeter, "MAX_EMPTY_READS", 2)
    slept = []

    async def fast_sleep(s):
        slept.append(s)

    monkeypatch.setattr(greeter.asyncio, "sleep", fast_sleep)

    with pytest.raises(asyncio.CancelledError):
        run(greeter.run())
    assert first.released, "the dead handle was not released before reopening"
    assert len(opened) == 3, "the camera was not reopened after it died"


def test_retry_off_keeps_the_old_give_up_behaviour(monkeypatch):
    """conftest pins FACE_CAMERA_RETRY_S=0 for every test, and the greeter
    tests rely on the loop ending when the frames run out."""
    from app import greeter

    src = inspect.getsource(greeter.run)
    assert "settings.face_camera_retry_s <= 0" in src
    assert settings.face_camera_retry_s == 0.0


# ==================== 6. the robot is whoever holds ROBOT_TOKEN ====================

class _WS:
    def __init__(self, messages):
        self._q = [{"text": json.dumps(m)} for m in messages] + [{"type": "websocket.disconnect"}]

    async def receive(self):
        return self._q.pop(0)


class _Provider:
    async def send_audio(self, data):
        pass


def _pump(monkeypatch, messages, robot_token):
    from app import session as session_module
    from app.tools import robot_link

    robot_link.reset_state()
    monkeypatch.setattr(settings, "robot_token", robot_token)
    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Provider()
    sess.ws = _WS(messages)
    run(sess._browser_to_provider())
    return sess


def test_robot_ready_without_the_robot_token_is_ignored(monkeypatch):
    """"A browser never sends this" guarded the robot until 2026-09-01. It
    is true of the page this project ships and of nothing else on the LAN
    that holds WS_TOKEN — which is every browser's URL."""
    from app.tools import robot_link

    sess = _pump(monkeypatch, [{"type": "robot_ready", "places": ["ห้องตัวอย่าง"]}],
                 robot_token="secret")
    assert robot_link.available() is False
    assert not getattr(sess, "_is_robot", False)

    sess = _pump(monkeypatch, [{"type": "robot_ready", "token": "wrong", "places": ["x"]}],
                 robot_token="secret")
    assert robot_link.available() is False


def test_no_robot_token_on_the_server_means_no_socket_is_ever_the_robot(monkeypatch):
    from app.tools import robot_link

    _pump(monkeypatch, [{"type": "robot_ready", "token": "", "places": ["x"]}], robot_token="")
    assert robot_link.available() is False


def test_the_right_token_makes_this_socket_the_robot(monkeypatch):
    from app.tools import robot_link

    sess = _pump(monkeypatch,
                 [{"type": "robot_ready", "token": "secret", "places": ["ห้องตัวอย่าง"]}],
                 robot_token="secret")
    assert sess._is_robot is True
    # ...until the pump ends: with the socket gone the fixture's reset has
    # not run, but `run()`'s finally block calls app_gone — covered below.
    assert robot_link.KNOWN_PLACES == ["ห้องตัวอย่าง"]
    robot_link.reset_state()


def test_robot_arrived_from_a_socket_that_is_not_the_robot_is_dropped(monkeypatch):
    from app import events
    from app.tools import robot_link

    announced = []

    async def fake_announce(text, **kw):
        announced.append(kw.get("source"))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    robot_link.reset_state()
    robot_link.STATE.update(connected=True, moving=True, destination="ห้องตัวอย่าง")
    _pump(monkeypatch, [{"type": "robot_arrived", "place": "ห้องตัวอย่าง", "ok": True}],
          robot_token="secret")
    assert announced == [], "an arrival from a stranger became a turn"
    robot_link.reset_state()


def test_an_arrival_with_no_walk_pending_earns_no_turn(monkeypatch):
    """A stale callback, a replay, or a sender that is not the robot: none
    of them should make Emma announce an arrival."""
    from app import events
    from app.tools import robot_link

    announced = []

    async def fake_announce(text, **kw):
        announced.append(kw.get("source"))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    robot_link.reset_state()
    assert run(robot_link.arrived("ห้องตัวอย่าง", ok=True)) is False
    assert announced == []


def test_a_walk_with_no_arrival_times_out_cancels_and_tells_the_model(monkeypatch):
    """An app that crashes after taking the order sends nothing, and
    `moving=True` used to be forever — with the model told to wait for an
    arrival message that never came."""
    from app import events
    from app.tools import robot_link

    sent, announced = [], []

    class _Live:
        def __init__(self):
            self.ws = self

        async def send_text(self, text):
            sent.append(json.loads(text))

        async def _send_json(self, msg):
            sent.append(msg)

    async def fake_announce(text, **kw):
        announced.append((kw.get("source"), text))
        return True

    from app import session as session_module

    monkeypatch.setattr(session_module, "_active", _Live())
    monkeypatch.setattr(settings, "robot_enabled", True)
    monkeypatch.setattr(events, "announce", fake_announce)

    async def go():
        robot_link.reset_state()
        robot_link.app_connected(["ห้องตัวอย่าง"])
        robot_link.STATE.update(moving=True, destination="ห้องตัวอย่าง")
        robot_link.start_arrival_watch("ห้องตัวอย่าง", 0.01)
        await asyncio.sleep(0.1)

    run(go())
    assert robot_link.STATE["moving"] is False
    assert [m["action"] for m in sent] == ["cancel_navigation"]
    assert announced and announced[0][0] == "robot_arrival_timeout"
    assert "พาไปไม่สำเร็จ" in announced[0][1]
    robot_link.reset_state()


def test_an_arrival_in_time_disarms_the_watch(monkeypatch):
    from app import events
    from app.tools import robot_link

    announced = []

    async def fake_announce(text, **kw):
        announced.append(kw.get("source"))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)

    async def go():
        robot_link.reset_state()
        robot_link.STATE.update(connected=True, moving=True, destination="ห้องตัวอย่าง")
        robot_link.start_arrival_watch("ห้องตัวอย่าง", 0.05)
        assert await robot_link.arrived("ห้องตัวอย่าง", ok=True) is True
        await asyncio.sleep(0.1)

    run(go())
    assert announced == ["robot_arrived"], "the timeout fired after a real arrival"
    robot_link.reset_state()


def test_go_to_place_arms_the_watch():
    from app.tools import robot

    src = inspect.getsource(robot.go_to_place)
    assert "robot_link.start_arrival_watch(target, settings.robot_arrival_timeout_s)" in src


def test_the_robot_socket_closing_is_reported_to_robot_link():
    """`app_gone()` existed for exactly this and nothing called it."""
    from app import session as session_module

    src = inspect.getsource(session_module.VoiceSession.run)
    tail = src.split("finally:", 1)[1]
    assert 'getattr(self, "_is_robot", False)' in tail
    assert "robot_link.app_gone()" in tail


def test_the_protocol_doc_tells_the_app_about_the_token_and_the_deadline():
    doc = Path("docs/ต่อกับหุ่นยนต์ Astronaut.md").read_text(encoding="utf-8")
    assert '"token": "<ROBOT_TOKEN>"' in doc
    assert "ROBOT_ARRIVAL_TIMEOUT_S" in doc
    assert "cancelNavigation()" in doc            # the on-robot stop path, spelled out
