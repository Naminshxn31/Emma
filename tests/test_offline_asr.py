"""Offline ASR (app/offline/asr.py): which decoder hears a segment, the pins,
and failure without a crash. Unit tests stub the recognizers; one test runs
the real models when this machine has them (and says what it skips when not)."""
from __future__ import annotations

import wave
from pathlib import Path

import pytest

from app.config import settings
from app.offline import asr

REAL_BUILD = asr._build                     # captured before conftest stubs it
WAKE = Path(__file__).resolve().parent / "data" / "wake"
SECOND = b"\x10\x00" * 16000                # one second at 16 kHz


class Stream:
    def __init__(self, owner):
        self.owner = owner
        self.result = type("R", (), {"text": owner.text})()

    def accept_waveform(self, rate, samples):
        self.owner.got.append((rate, len(samples)))


class Rec:
    def __init__(self, text="", fail=False):
        self.text, self.fail, self.got, self.decoded = text, fail, [], 0

    def create_stream(self):
        return Stream(self)

    def decode_stream(self, stream):
        if self.fail:
            raise RuntimeError("decoder broke")
        self.decoded += 1


class Lid:
    def __init__(self, answer):
        self.answer, self.got, self.text = answer, [], ""

    def create_stream(self):
        return Stream(self)

    def compute(self, stream):
        return self.answer


def _engine(monkeypatch, models):
    monkeypatch.setattr(asr, "_build", lambda base, threads, usable: models)
    return asr.Engine()


def test_the_conversation_language_skips_language_id(monkeypatch):
    th, en, lid = Rec("ฟิตเนสอยู่ไหน"), Rec("Where is the gym?"), Lid("en")
    e = _engine(monkeypatch, {"th": th, "en": en, "lid": lid})
    got = e.transcribe(SECOND, "th")
    assert (got.text, got.lang, got.lid_ms) == ("ฟิตเนสอยู่ไหน", "th", 0)
    assert lid.got == []
    assert e.transcribe(SECOND, "en").lang == "en"
    # any other known language: the English decoder (no third set of lines)
    assert e.transcribe(SECOND, "de").lang == "en"


@pytest.mark.parametrize("label,decoder", [("en", "en"), ("th", "th"), ("my", "th"), ("zh", "th"), ("", "th")])
def test_unknown_language_uses_the_binary_rule(monkeypatch, label, decoder):
    """Measured: "en or not" was right on 170/170 clips; Thai was labelled
    my/vi/zh/km sometimes, never en."""
    e = _engine(monkeypatch, {"th": Rec("ไทย"), "en": Rec("English"), "lid": Lid(label)})
    assert e.transcribe(SECOND, None).lang == decoder


def test_a_click_is_not_decoded(monkeypatch):
    th = Rec("x")
    e = _engine(monkeypatch, {"th": th})
    assert e.transcribe(b"\x10\x00" * 1000, "th").text == ""
    assert th.decoded == 0


def test_a_decoder_error_is_no_transcript_not_a_crash(monkeypatch):
    e = _engine(monkeypatch, {"th": Rec(fail=True)})
    assert e.transcribe(SECOND, "th") is None


def test_no_models_loads_nothing_and_says_so(monkeypatch, caplog):
    e = asr.Engine()                        # conftest's stub: nothing loads
    assert e.load() is False and not e.ready
    assert e.transcribe(SECOND, "th") is None
    assert "fetch_offline_models.py" in caplog.text


def test_only_one_language_present_still_decodes(monkeypatch):
    e = _engine(monkeypatch, {"th": Rec("ไทย")})
    assert e.transcribe(SECOND, None).lang == "th"
    assert e.transcribe(SECOND, "en").lang == "th"      # the one decoder there is


def test_problems_names_missing_and_changed_files(tmp_path):
    folder = asr.model_dir("whisper", tmp_path)
    folder.mkdir(parents=True)
    (folder / "base-tokens.txt").write_bytes(b"not the pinned tokens")
    bad = asr.problems("whisper", tmp_path)
    assert "changed base-tokens.txt" in bad and "missing base-encoder.int8.onnx" in bad


def test_the_pins_are_the_archives_and_the_files_loaded():
    pins = asr.fetch_map()
    assert set(pins) == {"thai", "whisper"}
    for pin in pins.values():
        assert pin["url"].startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/")
        assert len(pin["sha256"]) == 64 and pin["size"] > 0
        assert all(len(h) == 64 for h in pin["files"].values())
    assert "encoder-epoch-12-avg-5.int8.onnx" in pins["thai"]["files"]
    assert "base-decoder.int8.onnx" in pins["whisper"]["files"]


def _real_models_present() -> bool:
    base = Path(asr.ROOT) / "data" / "offline" / "asr"
    return all(not asr.problems(name, base) for name in asr.MODELS)


def _wav(name):
    with wave.open(str(WAKE / name)) as w:
        return w.readframes(w.getnframes())


def test_the_real_models_hear_thai_and_english(monkeypatch):
    if not _real_models_present():
        pytest.skip("NOT CHECKED: the real offline recognizers (zipformer-thai + whisper-base) "
                    "decode Thai and English -- data/offline/asr is missing or not the pinned files "
                    "on this machine; python scripts/fetch_offline_models.py restores it")
    monkeypatch.setattr(asr, "_build", REAL_BUILD)
    monkeypatch.setattr(settings, "offline_asr_dir", str(Path(asr.ROOT) / "data" / "offline" / "asr"))
    e = asr.Engine()
    assert e.load() and e.languages() == {"th", "en"}
    english = e.transcribe(_wav("emma_sentence.wav"), None)       # TTS, committed for the wake tests
    assert english.lang == "en" and "light" in english.text.lower()
    thai = e.transcribe(_wav("other_imm_trap.wav"), None)
    assert thai.lang == "th" and "กินข้าว" in thai.text
