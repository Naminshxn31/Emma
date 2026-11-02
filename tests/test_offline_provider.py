"""The offline provider end to end with a scripted VAD and a stubbed
recognizer (no model, no network): what it yields for a story, a fixed line,
a draft, a tap, an instruction it cannot honour; and that the switch-off
registry is unchanged."""
from __future__ import annotations

import asyncio
import hashlib
import json
import types
import wave

import pytest

from app import stories
from app.config import settings
from app.offline import asr, lines, router, voice
from app.providers import ProviderError, default_voice_for, get_provider
from app.vad_gate import VadGate

CHUNK = b"\x01\x02" * 160          # 20 ms at 16 kHz, above the dead-mic floor


class ScriptedDetector:
    def __init__(self, answers):
        self.answers = list(answers)

    def accept_waveform(self, samples):
        pass

    def is_speech_detected(self):
        return self.answers.pop(0) if self.answers else False


class FakeEngine:
    def __init__(self, text="", lang="th", ready=True):
        self.text, self.lang, self._ready = text, lang, ready
        self.calls = []

    @property
    def ready(self):
        return self._ready

    def languages(self):
        return {"th", "en"} if self._ready else set()

    def load(self):
        return self._ready

    def transcribe(self, pcm16, lang=None, rate=16000):
        self.calls.append((len(pcm16), lang))
        return asr.Transcript(self.text, lang or self.lang, 3) if self._ready else None


STORY = {"ok": True, "screen": "story", "topic": "parking", "story": {"template": "stack", "taps": []},
         "say": [{"th": "ที่จอดรถของเราอยู่ชั้นใต้ดินค่ะ", "en": "Our parking is in the basement."},
                 {"th": "เชื่อมทุกอาคารค่ะ", "en": "It links every building."}],
         "instruction": "for a model"}


def _approved_lines(tmp_path, monkeypatch, ids=lines.IDS):
    from pathlib import Path

    shipped = json.loads((Path(__file__).resolve().parents[1] / "data" / "projects" / "embassy_world"
                          / "offline" / "lines.json").read_text(encoding="utf-8"))
    for line in shipped["lines"]:
        if line["id"] in ids:
            line.update(approved_by="test", approved_at="2026-10-26T10:00:00+07:00")
            line["approved_sha256"] = lines.digest(line)
    file = tmp_path / "lines.json"
    file.write_text(json.dumps(shipped, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(lines, "path", lambda project_id=None: file)
    lines.reset()
    return {ln["id"]: ln for ln in shipped["lines"]}


def _clip(text, lang, pcm=b"\x20\x00" * 2400):
    folder = voice.folder()
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / voice.INDEX
    index = (json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file()
             else {"schema": 1, "model": "m", "voice": settings.gemini_voice, "rate": 24000, "clips": {}})
    key = voice.clip_key("m", settings.gemini_voice, lang, text)
    with wave.open(str(folder / (key + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    index["clips"][key] = {"file": key + ".wav", "lang": lang,
                           "sha256": hashlib.sha256((folder / (key + ".wav")).read_bytes()).hexdigest()}
    index_path.write_text(json.dumps(index), encoding="utf-8")
    voice.reset()
    return pcm


def _warm_tokenizer():
    """What router.prepare does in production, off the event loop: the
    tokenizer's first call loads pythainlp (~0.3 s here). Stubbing prepare
    moved that load onto the loop, inside the settle window of whichever
    test tokenized first -- red or green by test order."""
    from app.tools.retrieval import tokenize

    tokenize("ที่จอดรถมีกี่คันคะ")


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(settings, "offline_fallback", True)
    monkeypatch.setattr(router, "prepare", _warm_tokenizer)
    from app.providers import offline as module

    gates = []

    def gate(vad_floor):
        g = VadGate(ScriptedDetector([True] * 5 + [False] * 3), prefix_padding_ms=0)
        gates.append(g)
        return g

    monkeypatch.setattr(module, "_own_gate", gate)
    return module


def _run(provider, *, audio_frames=0, texts=(), settle=0.3):
    """Open, feed, collect every event for `settle` seconds after the input."""
    async def main():
        out = []
        async with provider:
            async def pump():
                async for event in provider.events():
                    out.append(event)

            task = asyncio.create_task(pump())
            await asyncio.sleep(0.05)
            for _ in range(audio_frames):
                await provider.send_audio(CHUNK)
            for text in texts:
                await provider.send_text(text)
            await asyncio.sleep(settle)
        await asyncio.wait_for(task, 2)
        return out

    return asyncio.run(main())


def _kinds(events):
    return [e.kind for e in events]


def test_switch_off_the_name_does_not_exist():
    with pytest.raises(ProviderError, match="Unknown VOICE_PROVIDER 'offline'"):
        get_provider("offline", "Despina", "x")
    assert default_voice_for("offline") == settings.openai_voice


def test_switch_on_it_is_a_provider_with_the_live_rates(offline):
    p = get_provider("offline", "Despina", "x")
    assert (p.input_sample_rate, p.output_sample_rate, p.auto_resumes) == (16000, 24000, False)
    assert default_voice_for("offline") == settings.gemini_voice
    # what the session reads by getattr
    assert p._vad_gate is not None and p._vad_gate.keep_audio
    assert p._speech_ended_at is None and p._replied_since_end is False


def test_a_heard_story_question_yields_what_present_story_yields(offline, monkeypatch, tmp_path):
    engine = FakeEngine("ที่จอดรถมีกี่คันคะ", "th")
    monkeypatch.setattr(asr, "engine", lambda: engine)
    monkeypatch.setattr(router, "route", lambda *a, **k: router.Route("story", topic="parking", why="story"))
    monkeypatch.setattr(stories, "tool_result", lambda topic: dict(STORY, topic=topic))
    pcm = _clip(STORY["say"][0]["th"], "th")
    p = offline.OfflineProvider("Despina", "x")
    p._stories_visible = True
    events = _run(p, audio_frames=8)
    kinds = _kinds(events)
    assert kinds[0] == "speech_started" and "user_transcript" in kinds
    assert kinds.index("tool_call") < kinds.index("tool_result") < kinds.index("audio") < kinds.index("turn_complete")
    result = next(e for e in events if e.kind == "tool_result")
    assert result.text == "present_story" and result.data["topic"] == "parking"
    said = "".join(e.text for e in events if e.kind == "assistant_transcript")
    assert said == STORY["say"][0]["th"] + " " + STORY["say"][1]["th"]
    # the first line had a clip, the second did not: words without a voice
    assert b"".join(e.audio for e in events if e.kind == "audio") == pcm
    assert engine.calls and engine.calls[0][1] is None            # language unknown -> ID
    assert p._replied_since_end is True


def test_an_approved_line_is_said_with_its_clip(offline, monkeypatch, tmp_path):
    shipped = _approved_lines(tmp_path, monkeypatch)
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine("ราคาเริ่มต้นเท่าไหร่คะ", "th"))
    text = lines.norm(shipped["handoff_commercial"]["th"])
    pcm = _clip(text, "th")
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, audio_frames=8)
    said = "".join(e.text for e in events if e.kind == "assistant_transcript")
    assert said == text
    assert b"".join(e.audio for e in events if e.kind == "audio") == pcm
    assert _kinds(events)[-1] == "turn_complete"


def test_a_draft_is_never_said_or_shown(offline, monkeypatch):
    """The shipped file is all drafts: nothing reaches the bubble or the speaker."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine("ราคาเริ่มต้นเท่าไหร่คะ", "th"))
    p = offline.OfflineProvider("Despina", "x", greeting="greet them")
    events = _run(p, audio_frames=8)
    assert not [e for e in events if e.kind in ("assistant_transcript", "audio")]


def test_the_opening_greeting_is_the_offline_greeting_in_english(offline, monkeypatch, tmp_path):
    shipped = _approved_lines(tmp_path, monkeypatch, ids=("offline_greeting",))
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    p = offline.OfflineProvider("Despina", "x", greeting="greet them")
    events = _run(p)
    said = "".join(e.text for e in events if e.kind == "assistant_transcript")
    assert said == lines.norm(shipped["offline_greeting"]["en"])


def test_a_camera_greeting_by_send_text_is_said_once(offline, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import greeter

    _approved_lines(tmp_path, monkeypatch, ids=("offline_greeting",))
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    text = greeter.greeting_for(SimpleNamespace(kind="stranger", name=None, group=None))
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, texts=(text, text))
    assert _kinds(events).count("turn_complete") == 1


def test_instructions_for_a_model_are_dropped(offline, monkeypatch, tmp_path):
    from app import session

    _approved_lines(tmp_path, monkeypatch)
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, texts=(session.FOLLOW_UP_TEXT, session.GOODBYE_TEXT))
    assert events == []


def test_a_story_tap_presents_the_next_story(offline, monkeypatch):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    monkeypatch.setattr(stories, "tool_result", lambda topic: dict(STORY, topic=topic))
    tap = {"id": "story_parking_x", "label_th": "ที่จอดรถ", "label_en": "Parking", "next_story": "parking"}
    p = offline.OfflineProvider("Despina", "x")
    p._stories_visible = True
    events = _run(p, texts=(stories.tap_text(tap, "en", "en"),))
    assert [e.data["topic"] for e in events if e.kind == "tool_result"] == ["parking"]


def test_no_recognizer_no_reply_and_no_crash(offline, monkeypatch):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine(ready=False))
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, audio_frames=8)
    assert _kinds(events) == ["speech_started"]
    assert p._replied_since_end is True        # nothing owed: the session must not wait for one


def test_background_talk_gets_silence(offline, monkeypatch):
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine("พี่ อันนี้ตึกเอใช่ไหม", "th"))
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, audio_frames=8)
    assert "assistant_transcript" not in _kinds(events) and "turn_complete" not in _kinds(events)


def _stop_seen(monkeypatch, offline, *, offered=True):
    from app.tools import registry

    calls = []

    async def dispatch(name, args):
        calls.append((name, args))
        return {"ok": True, "hardware": "requested", "instruction": "for a model"}

    monkeypatch.setattr(registry, "dispatch", dispatch)
    monkeypatch.setattr(offline.OfflineProvider, "_stop_tool",
                        staticmethod(lambda: ("stop_moving", {}) if offered else None))
    return calls


@pytest.mark.parametrize("heard_text", ["หยุด", "Emma stop", "stop walking"])
def test_a_heard_stop_calls_the_stop_tool_and_says_nothing(offline, monkeypatch, tmp_path, heard_text):
    """Review 26 Oct: offline had no voice stop -- "หยุด" was silence and
    "Emma stop" after her question started the menu while the robot moved."""
    _approved_lines(tmp_path, monkeypatch)          # every line approved: none is said anyway
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine(heard_text, "en"))
    monkeypatch.setattr(offline.OfflineProvider, "_robot_moving", staticmethod(lambda: True))
    calls = _stop_seen(monkeypatch, offline)
    p = offline.OfflineProvider("Despina", "x")
    p._asked_until = 1e12                           # she has just asked something
    events = _run(p, audio_frames=8)
    assert calls == [("stop_moving", {})]
    assert [e.text for e in events if e.kind == "tool_call"] == ["stop_moving"]
    assert not [e for e in events if e.kind in ("assistant_transcript", "audio")], "a stop is never answered"


def test_a_stop_with_no_stop_tool_offered_is_silence(offline, monkeypatch, tmp_path):
    _approved_lines(tmp_path, monkeypatch)
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine("หยุด", "th"))
    calls = _stop_seen(monkeypatch, offline, offered=False)
    p = offline.OfflineProvider("Despina", "x")
    events = _run(p, audio_frames=8)
    assert calls == [] and not [e for e in events if e.kind in ("tool_call", "assistant_transcript")]


def test_the_stop_tool_is_the_registrys_visible_one(monkeypatch):
    from app.providers import offline as module
    from app.tools import registry

    visible = {"move_body"}
    monkeypatch.setattr(registry, "allowed", lambda entry: entry.name in visible)
    monkeypatch.setattr(registry, "get", lambda name: types.SimpleNamespace(name=name))
    assert module.OfflineProvider._stop_tool() == ("move_body", {"action": "stop"})
    visible.add("stop_moving")
    assert module.OfflineProvider._stop_tool() == ("stop_moving", {})
    visible.clear()
    assert module.OfflineProvider._stop_tool() is None


def test_the_segment_hook_still_sees_every_segment(offline, monkeypatch):
    """Listening focus attributes segments through watch_segments; offline
    keeps the audio whatever the session asks (the recognizer needs it)."""
    monkeypatch.setattr(asr, "engine", lambda: FakeEngine())
    seen = []
    p = offline.OfflineProvider("Despina", "x")
    assert p.watch_segments(lambda *closed: seen.append(closed) or "a note", keep_audio=False)
    assert p._vad_gate.keep_audio
    _run(p, audio_frames=8)
    assert len(seen) == 1 and len(seen[0][3]) > 0
