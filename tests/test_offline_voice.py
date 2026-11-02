"""Pre-rendered offline clips (app/offline/voice.py) and the render script's
dry run -- which must never call the TTS API."""
from __future__ import annotations

import hashlib
import json
import sys
import wave
from pathlib import Path

import pytest

from app.config import settings
from app.offline import voice

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _clip(folder: Path, text: str, lang: str, *, model="m-tts", voice_name=None, rate=24000, pcm=None):
    voice_name = voice_name or settings.gemini_voice
    pcm = pcm if pcm is not None else (b"\x10\x00" * 4800)
    key = voice.clip_key(model, voice_name, lang, text)
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / (key + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    index_path = folder / voice.INDEX
    index = (json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file()
             else {"schema": voice.SCHEMA, "model": model, "voice": voice_name, "rate": 24000, "clips": {}})
    index["clips"][key] = {"file": key + ".wav", "lang": lang,
                           "sha256": hashlib.sha256((folder / (key + ".wav")).read_bytes()).hexdigest()}
    index_path.write_text(json.dumps(index), encoding="utf-8")
    voice.reset()
    return key, pcm


def test_a_pinned_clip_plays(tmp_path):
    _key, pcm = _clip(voice.folder(), "สวัสดีค่ะ", "th")
    assert voice.pcm_for("สวัสดีค่ะ", "th") == pcm
    # the key is over normalised words: spacing does not lose the clip
    assert voice.pcm_for("  สวัสดีค่ะ ", "th") == pcm


def test_language_model_and_voice_are_part_of_the_key():
    _clip(voice.folder(), "Hello.", "en")
    assert voice.pcm_for("Hello.", "th") is None
    assert voice.clip_key("a", "v", "en", "x") != voice.clip_key("b", "v", "en", "x")
    assert voice.clip_key("a", "v", "en", "x") != voice.clip_key("a", "w", "en", "x")


def test_a_clip_in_another_voice_is_not_played(monkeypatch):
    _clip(voice.folder(), "Hello.", "en", voice_name="SomeoneElse")
    assert voice.pcm_for("Hello.", "en") is None


def test_a_changed_clip_is_not_played():
    """A valid WAV in the right format but not the pinned bytes -- only the
    hash can tell (a swapped file is a different sentence in her voice)."""
    key, _ = _clip(voice.folder(), "Hello.", "en")
    with wave.open(str(voice.folder() / (key + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x7f\x00" * 4800)
    assert voice.pcm_for("Hello.", "en") is None


def test_a_clip_at_another_rate_is_not_played():
    _clip(voice.folder(), "Hello.", "en", rate=16000)
    assert voice.pcm_for("Hello.", "en") is None


def test_an_index_entry_cannot_point_outside_the_folder():
    key, _ = _clip(voice.folder(), "Hello.", "en")
    index = json.loads((voice.folder() / voice.INDEX).read_text(encoding="utf-8"))
    index["clips"][key]["file"] = "../elsewhere.wav"
    (voice.folder() / voice.INDEX).write_text(json.dumps(index), encoding="utf-8")
    voice.reset()
    assert voice.pcm_for("Hello.", "en") is None


def test_no_index_no_audio():
    assert voice.pcm_for("Hello.", "en") is None


def test_chunks_are_whole_samples_and_cover_the_clip():
    pcm = bytes(range(256)) * 100
    parts = voice.chunks(pcm, ms=100)
    assert b"".join(parts) == pcm
    assert all(len(p) % 2 == 0 for p in parts[:-1])


def test_render_dry_run_calls_no_api(monkeypatch, capsys):
    """--dry-run is the only way this repo's agents may run the renderer:
    the TTS renderer is armed to fail the test if it is ever reached."""
    import render_offline_voice
    from app import voice as tts

    def boom(*a, **k):
        raise AssertionError("the TTS API was called in a dry run")

    monkeypatch.setattr(tts, "_render", boom)
    monkeypatch.setattr(render_offline_voice, "render", boom)
    assert render_offline_voice.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "ESTIMATED cost" in out and "drafts are never rendered" in out


def test_render_wants_only_approved_lines(monkeypatch, tmp_path):
    import render_offline_voice
    from app.offline import lines

    data = {"source_id": lines.SOURCE_ID, "project_id": settings.project_id, "schema": 1, "lines": [
        {"id": "ack", "kind": "ack", "th": "ยินดีค่ะ", "en": "My pleasure.", "approved_by": None,
         "approved_at": None, "approved_sha256": None},
        {"id": "menu", "kind": "menu", "th": "เมนูค่ะ", "en": "Menu.", "approved_by": "pete",
         "approved_at": "2026-10-26T10:00:00+07:00", "approved_sha256": None},
    ]}
    data["lines"][1]["approved_sha256"] = lines.digest(data["lines"][1])
    file = tmp_path / "lines.json"
    file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(lines, "path", lambda project_id=None: file)
    lines.reset()
    got = render_offline_voice.wanted("lines")
    assert {(lang, text) for _s, lang, text in got} == {("th", "เมนูค่ะ"), ("en", "Menu.")}


def test_render_refuses_to_mix_two_voices(monkeypatch):
    import render_offline_voice

    _clip(voice.folder(), "Hello.", "en", model="old-model")
    monkeypatch.setattr(settings, "gemini_api_key", "test-not-a-key")
    with pytest.raises(SystemExit):
        render_offline_voice.render([("line:x", "en", "Hi.", "k")], "new-model", voice.folder())
