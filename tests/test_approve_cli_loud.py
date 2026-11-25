"""scripts/approve_event_script.py --listened must be loud when it cannot pin the recordings.

24 Nov 2026: on this PC plain `python` has no dotenv, `from app import event_voice` raised
ModuleNotFoundError, `_clip_sha` swallowed it and returned None, and
`--by NAME --listened` signed the WORDS, pinned no recording and printed nothing -- the owner
would believe the takes were approved while on stage Gemini read every line live.

Everything here runs on a COPY of a tiny cue file in tmp_path; nothing touches
data/ceremony/*.json and nothing is signed in a real file.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "approve_event_script.py"

LINE = "ยินดีต้อนรับค่ะ"


def _doc() -> dict:
    return {"cues": [
        {"id": "t_line", "scene": 1, "th": LINE, "numbers": []},
        {"id": "t_live", "scene": 1, "th": None, "live_th": "แนะนำห้อง", "numbers": []},
    ]}


@pytest.fixture
def cue_copy(tmp_path):
    target = tmp_path / "mini.script.json"
    target.write_text(json.dumps(_doc(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _module():
    spec = importlib.util.spec_from_file_location("approve_event_script_loud", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args: list[str], *, pythonpath: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=120)


@pytest.fixture
def no_event_voice(tmp_path):
    """A sitecustomize on PYTHONPATH that makes `app.event_voice` fail to import, as it does
    under a Python without the app's packages."""
    folder = tmp_path / "blocked"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text(
        "import sys\n"
        "class _Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'app.event_voice':\n"
        "            raise ModuleNotFoundError(\"No module named 'dotenv'\", name='dotenv')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Block())\n", encoding="utf-8")
    return folder


def test_listened_aborts_loudly_when_event_voice_cannot_be_imported(cue_copy, no_event_voice):
    before = cue_copy.read_bytes()
    done = _run(["--file", str(cue_copy), "--by", "tester", "--listened"], pythonpath=no_event_voice)
    assert done.returncode != 0, done.stdout + done.stderr
    assert cue_copy.read_bytes() == before, "the words were signed although no recording could be pinned"
    text = done.stderr + done.stdout
    assert "app.event_voice" in text and ".venv/Scripts/python.exe" in text
    assert "ไม่ได้เขียนอะไรลงไฟล์" in text and "nothing was written" in text
    assert "approved by" not in done.stdout


def test_listened_refuses_a_line_whose_recording_is_missing_and_writes_nothing(cue_copy, capsys):
    mod = _module()
    before = cue_copy.read_bytes()
    with pytest.raises(SystemExit) as stop:
        mod.approve(cue_copy, "tester", None, audit=None, voice=True)
    assert "t_line" in str(stop.value) and "nothing was written" in str(stop.value)
    assert cue_copy.read_bytes() == before
    # the live-brief cue has no line and no recording by design: it alone never blocks
    assert mod.approve(cue_copy, "tester", {"t_live"}, audit=None, voice=True) == ["t_live"]


def test_a_draft_that_is_skipped_does_not_trigger_the_abort(cue_copy, capsys):
    """An unfinished line (not polite Thai) is skipped and named, as ever -- it has no recording to want."""
    doc = _doc()
    doc["cues"].insert(0, {"id": "t_draft", "scene": 1, "th": "ยังไม่เสร็จ", "numbers": []})
    doc["cues"] = [c for c in doc["cues"] if c["id"] != "t_line"]
    cue_copy.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    mod = _module()
    assert mod.approve(cue_copy, "tester", None, audit=None, voice=True) == ["t_live"]
    assert "skipped t_draft" in capsys.readouterr().out


def test_the_normal_path_pins_and_prints_the_summary(cue_copy, tmp_path, monkeypatch, capsys):
    mod = _module()
    monkeypatch.setattr(mod, "AUDIT", tmp_path / "audit.jsonl")       # never the real approval audit
    monkeypatch.setattr(mod, "_clip_sha", lambda cue: "a" * 64)
    assert mod.main(["--file", str(cue_copy), "--by", "tester", "--listened"]) == 0
    out = capsys.readouterr().out
    assert "approved by tester: t_line, t_line:voice, t_live" in out
    assert "2 cue(s): words signed, 1 recording(s) pinned" in out
    assert "(:voice = the recorded clip" in out
    raw = {c["id"]: c for c in json.loads(cue_copy.read_text(encoding="utf-8"))["cues"]}
    assert raw["t_line"]["approved_voice_sha256"] == "a" * 64
    assert "approved_voice_sha256" not in raw["t_live"]


def test_without_listened_the_words_are_signed_and_no_summary_line_is_printed(cue_copy, tmp_path, monkeypatch,
                                                                                capsys):
    """Words-only never needed the clips (a Python without event_voice signs them as ever)."""
    mod = _module()
    monkeypatch.setattr(mod, "AUDIT", tmp_path / "audit.jsonl")
    assert mod.main(["--file", str(cue_copy), "--by", "tester"]) == 0
    out = capsys.readouterr().out
    assert "approved by tester: t_line, t_live" in out and "recording(s) pinned" not in out
