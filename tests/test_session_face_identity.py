"""Which conversation is the robot face — decided in `VoiceSession.run` (finding #40).

`robot_face = "EmmaRobot/" in user-agent` is the one line that sets
`registry.robot_face_session` and calls `robot_face_link.claim`, and so decides
whether `move_body` is offered and whether the nav tools route to the face.
Replacing it with `True` (every browser is the robot) or `False` (never) left
the whole suite green, because every other test sets the ContextVar itself.

These tests run the real `run()` with a fake socket and a fake provider and
look from *inside* the provider — the task the model's tool calls inherit
their context from — at what that line decided.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

from app import robot_face_link
from app import session as session_module
from app.config import settings
from app.tools import registry

DESK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
ROBOT_UA = "Mozilla/5.0 (Linux; Android 15) wv EmmaRobot/0.1"


class FakeWS:
    def __init__(self, user_agent: str):
        self.headers = {"user-agent": user_agent}
        self.query_params = {}
        self.sent: list[str] = []
        self.closed = False

    async def accept(self):
        pass

    async def receive(self):
        await asyncio.Event().wait()           # the page says nothing; cancelled at the end

    async def send_text(self, text):
        self.sent.append(text)

    async def send_bytes(self, data):
        pass

    async def close(self, *a, **k):
        self.closed = True


class FakeProvider:
    """Looks at the session from inside its own task, then ends the call.
    `script` is the events it yields; `probe(sess)` runs once before them."""

    input_sample_rate = 16000
    output_sample_rate = 24000
    session_limit_minutes = 15
    auto_resumes = True
    model = "fake"

    def __init__(self, probe=None, script=()):
        self.probe = probe
        self.script = list(script)
        self.session = None
        self.seen: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stand_down_floor(self):
        pass

    async def send_text(self, text):
        pass

    async def send_audio(self, data):
        pass

    async def events(self):
        if self.probe is not None:
            self.seen.update(self.probe(self.session))
        for step in self.script:
            if callable(step):
                self.seen.update(step(self.session))
            else:
                yield step


def run_session(monkeypatch, user_agent: str, provider: FakeProvider):
    """`VoiceSession(ws).run()` end to end with the fakes; returns the session."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "idle_timeout_s", 0)
    monkeypatch.setattr(settings, "canva_url", "")
    monkeypatch.setattr(settings, "turn_log", False)
    monkeypatch.setattr(session_module, "get_provider", lambda *a, **k: provider)
    sess = session_module.VoiceSession(FakeWS(user_agent))
    provider.session = sess

    async def go():
        await asyncio.wait_for(sess.run(), 10)

    asyncio.run(go())
    return sess


def _move_body():
    import app.tools.body as body_tool

    entry = registry.get("move_body")
    if entry is None or entry.handler is not body_tool.move_body:
        registry._REGISTRY.pop("move_body", None)
        importlib.reload(body_tool)
        entry = registry.get("move_body")
    return entry


def _identity(sess):
    return {"face_var": registry.robot_face_session.get(),
            "is_face": robot_face_link.is_robot_face(sess),
            "move_body": registry.allowed(_move_body())}


@pytest.fixture
def body_on(monkeypatch):
    monkeypatch.setattr(settings, "tools_enabled", True)
    monkeypatch.setattr(settings, "tool_groups", "robot,body")
    monkeypatch.setattr(settings, "robot_body_enabled", True)


def test_a_desk_browser_is_not_the_robot_face(monkeypatch, body_on):
    provider = FakeProvider(probe=_identity)
    run_session(monkeypatch, DESK_UA, provider)
    assert provider.seen == {"face_var": None, "is_face": False, "move_body": False}


def test_the_robot_user_agent_is_the_robot_face(monkeypatch, body_on):
    provider = FakeProvider(probe=_identity)
    sess = run_session(monkeypatch, ROBOT_UA, provider)
    assert provider.seen["face_var"] is sess
    assert provider.seen["is_face"] is True
    assert provider.seen["move_body"] is True


def test_with_a_face_token_the_user_agent_is_only_a_claim(monkeypatch, body_on):
    """ROBOT_FACE_TOKEN set: the tools are declared for the claimed face (the
    declaration is fixed before any frame can arrive), but nothing reaches
    the bridge until a face_hello with the token."""
    monkeypatch.setattr(settings, "robot_face_token", "t-face")

    def hello(sess):
        robot_face_link.receive(sess, {"type": "face_hello", "token": "t-face"})
        return {"after_hello": robot_face_link.is_robot_face(sess)}

    provider = FakeProvider(probe=_identity, script=[hello])
    sess = run_session(monkeypatch, ROBOT_UA, provider)
    assert provider.seen["face_var"] is sess
    assert provider.seen["is_face"] is False
    assert provider.seen["after_hello"] is True

