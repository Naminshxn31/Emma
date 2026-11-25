"""Emma's recorded ceremony lines (app/event_voice.py) and the render script
(scripts/render_event_script.py) with a FAKE Live session -- nothing here opens a
socket. Everything is written under tmp_path: CEREMONY_FILE points at a temp ceremony
file (the ceremony app's own, 9 Nov), so its `voice/` folder is temp too."""
from __future__ import annotations

import hashlib
import json
import sys
import wave
from array import array
from pathlib import Path

import pytest

from app import event_script, event_voice
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import render_event_script as res  # noqa: E402
import robot_server as rs  # noqa: E402

MODEL, VOICE = "gemini-test-live", "Despina"
LINE = "ขอขอบคุณทุกคนที่เข้ามาในงานวันนี้นะคะ"
LINE_B = "ฉันทำอะไรได้หลายอย่างเลยล่ะค่ะ คุณมิกิลองถามฉันมาได้เลยค่ะ"


def _voiced(seconds: float, amp: int = 3000) -> bytes:
    n = int(event_voice.RATE * seconds)
    return array("h", [amp if i % 2 else -amp for i in range(n)]).tobytes()


SILENCE = b"\x00\x00" * 2400                       # 0.1 s


@pytest.fixture
def event(tmp_path, monkeypatch):
    """A temp ceremony (as the real one: data/ceremony/floor1_ceremony.json under a root) whose
    script has one approved cue and one draft, and the settings the render uses."""
    root = tmp_path / "root"
    events = root / "data" / "ceremony"
    events.mkdir(parents=True)
    cues = [{"id": "s9_thanks", "scene": 9, "th": LINE, "numbers": []},
            {"id": "s1_can_do", "scene": 1, "th": LINE_B, "numbers": []}]
    signed = event_script._normalise(cues[0])
    cues[0].update(approved_by="tester", approved_at="2026-11-06T22:00:00+0700",
                   approved_sha256=event_script.digest(signed))
    (events / "floor1_ceremony.json").write_text(
        json.dumps({"format": event_script.FORMAT, "cues": cues}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_file", str(events / "floor1_ceremony.json"))
    monkeypatch.setattr(settings, "gemini_model", MODEL)
    monkeypatch.setattr(settings, "gemini_voice", VOICE)
    event_script.reset()
    event_voice.reset()
    yield {"root": root, "voice": events / "voice"}
    event_script.reset()
    event_voice.reset()


def _write_clip(base: Path, line: str, *, transcript: str | None = None, bar: float = 0.92,
                pcm: bytes | None = None, rate: int = 24000, model: str = MODEL, voice: str = VOICE,
                cue: str = "s9_thanks") -> bytes:
    pcm = pcm if pcm is not None else _voiced(0.5)
    key = event_voice.clip_key(model, voice, "th", line)
    base.mkdir(parents=True, exist_ok=True)
    with wave.open(str(base / (key + ".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    index = {"schema": 1, "model": model, "voice": voice, "rate": 24000, "clips": {key: {
        "file": key + ".wav", "sha256": hashlib.sha256((base / (key + ".wav")).read_bytes()).hexdigest(),
        "lang": "th", "cue": cue, "fidelity": 1.0, "min_fidelity": bar,
        "transcript": line if transcript is None else transcript,
        "numbers": {"missing": [], "extra": []}, "seconds": len(pcm) / 48000, "takes": 1,
        "rendered_at": "2026-11-06T22:40:00+0700"}}}
    (base / "index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    event_voice.reset()
    return pcm


def _cue(line: str = LINE, cid: str = "s9_thanks") -> dict:
    return {"id": cid, "th": line}


# ---- playback ------------------------------------------------------------------------

def test_the_key_covers_model_voice_language_and_words():
    k = event_voice.clip_key("m", "v", "th", "สวัสดีค่ะ")
    assert k == event_voice.clip_key("m", "v", "th", "  สวัสดีค่ะ ")        # normalised words
    assert len({k, event_voice.clip_key("m2", "v", "th", "สวัสดีค่ะ"), event_voice.clip_key("m", "v2", "th", "สวัสดีค่ะ"),
                event_voice.clip_key("m", "v", "en", "สวัสดีค่ะ"), event_voice.clip_key("m", "v", "th", "สวัสดีคะ")}) == 5


def test_a_recorded_line_plays_and_status_says_so(event):
    pcm = _write_clip(event["voice"], LINE)
    assert event_voice.pcm(_cue()) == pcm
    st = event_voice.status(_cue())
    assert st["recorded"] is True and st["fidelity"] == 1.0 and st["seconds"] == pytest.approx(0.5, abs=0.01)


def test_edited_words_have_no_clip(event):
    _write_clip(event["voice"], LINE)
    edited = _cue(LINE.replace("วันนี้", "ค่ำนี้"))
    assert event_voice.pcm(edited) is None
    assert event_voice.status(edited) == {"recorded": False, "fidelity": None, "seconds": None, "why": "no_clip"}


def test_a_voice_or_model_changed_since_the_render_plays_nothing(event, monkeypatch):
    """Another voice must not be another woman saying the line: no clip, read live."""
    _write_clip(event["voice"], LINE)
    monkeypatch.setattr(settings, "gemini_voice", "Kore")
    assert event_voice.pcm(_cue()) is None
    assert event_voice.status(_cue())["why"] == "voice_changed"
    monkeypatch.setattr(settings, "gemini_voice", VOICE)
    monkeypatch.setattr(settings, "gemini_model", "gemini-other-live")
    assert event_voice.pcm(_cue()) is None
    assert event_voice.status(_cue())["why"] == "model_changed"
    monkeypatch.setattr(settings, "gemini_model", "models/" + MODEL)        # the prefix is the same model
    assert event_voice.pcm(_cue()) is not None


def test_bytes_that_are_not_the_pinned_ones_are_not_played(event):
    _write_clip(event["voice"], LINE)
    wav = next(event["voice"].glob("*.wav"))
    with wave.open(str(wav), "wb") as w:              # a valid WAV, the right format, other audio
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(_voiced(0.5, amp=900))
    assert event_voice.pcm(_cue()) is None
    assert event_voice.status(_cue())["why"] == "not_pinned"


def test_a_clip_in_another_format_is_not_played(event):
    _write_clip(event["voice"], LINE, rate=16000)     # pinned, but not the Live API's 24 kHz
    assert event_voice.pcm(_cue()) is None
    assert event_voice.status(_cue())["why"] == "bad_format"


def test_the_transcript_decides_again_at_playback(event):
    """The fidelity is recomputed from the stored transcript against the CURRENT
    line: an index whose "fidelity" says 1.0 does not let a bad take through."""
    _write_clip(event["voice"], LINE, transcript="ขอบคุณค่ะ")
    assert json.loads((event["voice"] / "index.json").read_text(encoding="utf-8"))["clips"]
    assert event_voice.pcm(_cue()) is None
    st = event_voice.status(_cue())
    assert st["why"] == "below_bar" and st["recorded"] is False


def test_a_bar_under_the_floor_or_an_extra_number_is_not_played(event):
    _write_clip(event["voice"], LINE, bar=event_voice.FIDELITY_FLOOR - 0.2)
    assert event_voice.pcm(_cue()) is None and event_voice.status(_cue())["why"] == "bar_too_low"
    _write_clip(event["voice"], LINE, transcript=LINE + " 500")
    assert event_voice.pcm(_cue()) is None and event_voice.status(_cue())["why"] == "extra_number"


NUMBER_LINE = "จะขาดก็แค่ลงไปว่ายน้ำเล่นในสระ 155 เมตรด้วยตัวเองเท่านั้นเองค่ะ"


def test_a_number_said_in_thai_words_plays_a_dropped_one_does_not(event):
    """Preview render 6 Nov: a transcript wrote "หนึ่งร้อยห้าสิบห้า" for 155. A take that
    DROPPED the number scores ~0.98 -- over the bar -- so it needs its own check."""
    in_words = NUMBER_LINE.replace("155", "หนึ่งร้อยห้าสิบห้า")
    dropped = NUMBER_LINE.replace("155 ", "")
    from app import speech_match

    assert speech_match.fidelity(dropped, NUMBER_LINE) > 0.95          # why fidelity alone is not enough
    assert speech_match.fidelity(in_words, NUMBER_LINE) < 0.92         # and why a spelled number needs help
    assert event_voice.line_fidelity(in_words, NUMBER_LINE) == 1.0
    cue = {"id": "s3_joke", "th": NUMBER_LINE}
    _write_clip(event["voice"], NUMBER_LINE, transcript=in_words.replace("หนึ่งร้อย", "ร้อย"), cue="s3_joke")
    assert event_voice.pcm(cue) is not None
    _write_clip(event["voice"], NUMBER_LINE, transcript=dropped, cue="s3_joke")
    assert event_voice.pcm(cue) is None and event_voice.status(cue)["why"] == "number_not_said"
    # the renderer judges a take the same way
    take = {"pcm": _voiced(0.3), "rate": 24000, "complete": True}
    assert res.score(dict(take, transcript=in_words), NUMBER_LINE, 0.92)["ok"]
    judged = res.score(dict(take, transcript=dropped), NUMBER_LINE, 0.92)
    assert not judged["ok"] and judged["why"].startswith("number not said")
    assert event_voice.unspoken_numbers("แปดร้อยหนึ่งห้อง และ หนึ่งพันหกสิบหก ยูนิต", "801 ห้อง 1,066 ยูนิต") == []
    assert event_voice.unspoken_numbers("หนึ่งจุดสามเมตร", "1.3 เมตร") == []


def test_no_transcript_is_an_unscored_take(event):
    judged = res.score({"pcm": _voiced(0.3), "rate": 24000, "complete": True, "transcript": ""}, LINE, 0.92)
    assert not judged["ok"] and judged["why"] == "no transcript"


def test_no_ceremony_no_folder_no_clip(event, monkeypatch):
    _write_clip(event["voice"], LINE)
    monkeypatch.setattr(settings, "ceremony_file", "")
    # the event file is not where the lines live any more (9 Nov): set, it still gives no folder
    monkeypatch.setattr(settings, "event_file", str(event["root"] / "data" / "ceremony" / "floor1_ceremony.json"))
    assert event_voice.folder() is None
    assert event_voice.pcm(_cue()) is None
    assert event_voice.status(_cue())["why"] == "no_folder"


def test_trim_keeps_her_voice_and_a_little_room():
    voiced = _voiced(0.3)
    trimmed = event_voice.trim_silence(SILENCE * 5 + voiced + SILENCE * 10)
    assert voiced in trimmed
    assert len(trimmed) == len(voiced) + 2 * (24000 * 60 // 1000) + 2 * (24000 * 250 // 1000)
    assert event_voice.trim_silence(SILENCE * 3) == b""


# ---- the render, with a fake session --------------------------------------------------

def _takes(*transcripts, pcm=None, rate=24000, complete=True):
    """take_fn handing out these transcripts in order; it records each read turn."""
    calls: list[str] = []
    queue = list(transcripts)

    async def take(read_text: str) -> dict:
        calls.append(read_text)
        text = queue.pop(0)
        if isinstance(text, BaseException):
            raise text
        return {"pcm": SILENCE * 3 + (pcm or _voiced(0.4 + 0.01 * len(calls))) + SILENCE * 3,
                "transcript": text, "rate": rate, "complete": complete}

    take.calls = calls
    return take


def _index(event) -> dict:
    return json.loads((event["voice"] / "index.json").read_text(encoding="utf-8"))


def test_the_best_take_at_the_bar_is_kept(event):
    near = LINE.replace("ทุกคน", "ทุกท่าน")            # 0.947: over the bar, the first good one
    best = LINE.replace("วันนี้", "ในวันนี้")            # 0.974: the best
    worse = LINE.replace("ที่เข้ามาในงาน", "")         # 0.767: under the bar
    last = LINE.replace("ขอขอบคุณ", "ขอบคุณ")          # 0.972: good, the last good one
    take = _takes(near, best, worse, last)
    assert res.main(["--ids", "s9_thanks", "--pause", "0", "--takes", "4"], take_fn=take) == 0
    assert len(take.calls) == 4 and take.calls[0] == event_script.read_text(_cue())
    entry = next(iter(_index(event)["clips"].values()))
    assert entry["transcript"] == best and entry["fidelity"] == pytest.approx(0.9737, abs=1e-3)
    assert entry["takes"] == 4 and entry["min_fidelity"] == 0.92 and entry["cue"] == "s9_thanks"
    # the kept take's own audio (take 2: 0.42 s), trimmed -- not another take's
    played = event_voice.pcm(_cue())
    assert played is not None and _voiced(0.42) in played and _voiced(0.43) not in played
    assert len(played) < len(SILENCE * 6 + _voiced(0.42))


def test_a_perfect_take_ends_the_cue_early(event):
    take = _takes(LINE, LINE, LINE)
    assert res.main(["--ids", "s9_thanks", "--pause", "0", "--takes", "3"], take_fn=take) == 0
    assert len(take.calls) == 1


def test_takes_under_the_bar_or_with_a_new_number_are_not_kept(event):
    take = _takes("ขอบคุณค่ะ", LINE + " 500", "", RuntimeError("socket closed"))
    assert res.main(["--ids", "s9_thanks", "--pause", "0", "--takes", "4"], take_fn=take) == 1
    assert len(take.calls) == 4
    assert not (event["voice"] / "index.json").exists() and not list(event["voice"].glob("*.wav"))
    assert event_voice.pcm(_cue()) is None


def test_drafts_are_rendered_only_with_drafts(event):
    take = _takes(LINE, LINE_B)
    assert res.main(["--pause", "0"], take_fn=take) == 0
    assert [e["cue"] for e in _index(event)["clips"].values()] == ["s9_thanks"]
    assert len(take.calls) == 1
    take = _takes(LINE_B)
    assert res.main(["--pause", "0", "--drafts"], take_fn=take) == 0     # the approved one is there already
    assert sorted(e["cue"] for e in _index(event)["clips"].values()) == ["s1_can_do", "s9_thanks"]
    # the draft's clip is a recording of its words -- the approval is the player's (servable)
    assert event_voice.status(_cue(LINE_B, "s1_can_do"))["recorded"] is True
    assert "s1_can_do" not in event_script.servable()


def test_dry_run_sends_nothing_and_writes_nothing(event, monkeypatch, capsys):
    async def boom(*a, **k):
        raise AssertionError("a dry run opened a session")

    monkeypatch.setattr(res, "live_take", boom)
    assert res.main(["--dry-run", "--drafts"], take_fn=boom) == 0
    assert res.main(["--dry-run", "--drafts"]) == 0
    out = capsys.readouterr().out
    assert "s1_can_do" in out and "DRAFT" in out and "ESTIMATED" in out
    assert not event["voice"].exists()


def test_a_quota_answer_stops_the_run_and_keeps_what_was_kept(event):
    take = _takes(LINE, res.QuotaHit("429 RESOURCE_EXHAUSTED"))
    assert res.main(["--pause", "0", "--drafts"], take_fn=take) == 1
    assert [e["cue"] for e in _index(event)["clips"].values()] == ["s9_thanks"]
    assert len(take.calls) == 2


def test_an_index_of_another_voice_is_refused_without_fresh(event):
    _write_clip(event["voice"], LINE, voice="Kore")
    with pytest.raises(SystemExit):
        res.main(["--ids", "s9_thanks", "--pause", "0"], take_fn=_takes(LINE))
    assert res.main(["--ids", "s9_thanks", "--pause", "0", "--fresh"], take_fn=_takes(LINE)) == 0
    index = _index(event)
    assert index["voice"] == VOICE and len(index["clips"]) == 1 and len(list(event["voice"].glob("*.wav"))) == 1


def test_a_new_render_of_edited_words_drops_the_old_clip(event):
    _write_clip(event["voice"], LINE.replace("วันนี้", "ค่ำนี้"))       # the cue's old words
    assert res.main(["--ids", "s9_thanks", "--pause", "0"], take_fn=_takes(LINE)) == 0
    index = _index(event)
    assert len(index["clips"]) == 1 and next(iter(index["clips"].values()))["transcript"] == LINE
    assert len(list(event["voice"].glob("*.wav"))) == 1


def test_min_fidelity_under_the_floor_is_refused(event):
    with pytest.raises(SystemExit):
        res.main(["--min-fidelity", str(event_voice.FIDELITY_FLOOR - 0.05), "--dry-run"])


def test_the_key_never_reaches_the_screen(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "AIzaSECRET123")
    text = res._safe("wss://x/ws?key=AIzaSECRET123&alt=1 failed (AIzaSECRET123)")
    assert "SECRET" not in text


def test_deploy_ships_exactly_the_rendered_files(event):
    """What the render wrote is what deploy carries: the index and the WAVs it
    names, from the one folder."""
    assert res.main(["--pause", "0", "--drafts"], take_fn=_takes(LINE, LINE_B)) == 0
    (event["voice"] / "stray.wav").write_bytes(b"RIFF")
    shipped = rs.event_voice_files(event["root"])
    named = {"data/ceremony/voice/" + e["file"] for e in _index(event)["clips"].values()}
    assert shipped == named | {"data/ceremony/voice/index.json"} and len(named) == 2
