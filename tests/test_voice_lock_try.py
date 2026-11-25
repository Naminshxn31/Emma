"""scripts/voice_lock_try.py: the PC bench for "listen to one enrolled voice" (owner 19 Nov).

The maths the owner's verdict rests on -- which side of the bar a score lands on, FAR/FRR, the
recommended bar, the one-voice check of an enrolment, short words kept out of the threshold, the
48 kHz -> 16 kHz path of the live mic -- runs on synthetic numbers. One test runs the real pinned
models on committed wavs when this machine has them, and says what goes unchecked when it does not.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from app import one_voice as ov
from scripts import voice_lock_try as vl

ROOT = Path(__file__).resolve().parent.parent
DIM = 16


def _vec(*xs) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[: len(xs)] = xs
    return v / np.linalg.norm(v)


def _row(label, s, speech_s=2.0, rms_db=-25.0, genuine=None):
    return {"label": label, "genuine": (label == "target") if genuine is None else genuine,
            "speech_s": speech_s, "rms_db": rms_db, "clip": 0.0, "score": s}


# ---- the bar -----------------------------------------------------------------------------------

def test_a_score_on_the_bar_is_accepted_and_below_it_is_not():
    assert ov.decide(0.50, 2.0, 0.50) == ("ACCEPT", False)
    assert ov.decide(0.4999, 2.0, 0.50) == ("REJECT", False)
    assert ov.decide(0.9, 0.6, 0.50) == ("ACCEPT", True)          # short is flagged, not hidden
    assert ov.decide(None, 0.2, 0.50) == ("NONE", True)            # nothing to embed: no verdict


def test_far_counts_others_at_or_over_the_bar_frr_the_enrolled_under_it():
    far, frr = ov.far_frr([0.3, 0.5, 0.7, 0.9], [0.1, 0.5, 0.6], 0.5)
    assert far == pytest.approx(2 / 3)          # 0.5 and 0.6 get in
    assert frr == pytest.approx(1 / 4)          # only 0.3 is refused
    assert ov.far_frr([], [0.2], 0.5) == (0.0, None)
    assert ov.far_frr([0.2], [], 0.5) == (None, 1.0)


def test_eer_is_zero_when_the_voices_never_overlap_and_meets_in_the_middle_when_they_do():
    e, t = ov.eer([0.7, 0.8, 0.9], [0.1, 0.2, 0.3])
    assert e == 0.0 and 0.3 < t <= 0.7
    e, _ = ov.eer([0.4, 0.6, 0.8, 0.9], [0.1, 0.2, 0.5, 0.7])
    assert e == pytest.approx(0.25)
    assert ov.eer([], [0.1]) is None


def test_the_recommended_bar_is_the_lowest_that_keeps_others_out():
    genuine = [0.45, 0.55, 0.65, 0.75, 0.85]
    others = [0.10, 0.20, 0.30, 0.40, 0.52] + [0.15] * 15         # 20 others, the top one at 0.52
    rec = ov.recommend(genuine, others, max_far=0.05)
    # FAR <= 5% of 20 = one may get in: 0.41 lets only 0.52 through (5%) -- the lowest such bar.
    assert rec["threshold"] == pytest.approx(0.41)
    assert rec["far"] == pytest.approx(0.05)
    assert rec["frr"] == 0.0
    strict = ov.recommend(genuine, others, max_far=0.0)
    assert strict["threshold"] == pytest.approx(0.53) and strict["frr"] == pytest.approx(0.2)
    assert ov.recommend(genuine, [], 0.05) is None


def test_the_verdict_needs_both_sides_and_reads_the_refusals():
    assert ov.judge({"threshold": 0.5, "far": 0.0, "frr": 0.05}) == "WORKS"
    assert ov.judge({"threshold": 0.5, "far": 0.0, "frr": 0.25}) == "DOES NOT WORK"
    assert ov.judge(None) == "UNKNOWN"
    assert ov.judge({"threshold": 0.5, "far": 0.0, "frr": None}) == "UNKNOWN"


def test_length_buckets():
    assert [ov.bucket(s) for s in (0.3, 0.99, 1.0, 1.99, 2.0, 9)] == ["<1s", "<1s", "1-2s", "1-2s", ">=2s", ">=2s"]


# ---- enrolment ----------------------------------------------------------------------------------

def test_one_voice_enrols_and_two_voices_half_and_half_do_not():
    rng = np.random.default_rng(1)
    a, b = _vec(1), _vec(0, 1)
    one = [a + rng.normal(0, 0.05, DIM) for _ in range(8)]
    built = ov.build_print(one)
    assert built["ok"] and built["kept"] == 8 and built["worst"] > 0.9
    assert float(built["vector"] @ a) > 0.99
    blend = [a + rng.normal(0, 0.05, DIM) for _ in range(5)] + [b + rng.normal(0, 0.05, DIM) for _ in range(5)]
    out = ov.build_print(blend)
    assert not out["ok"] and out["why"] == "mixed_voices"
    assert "vector" not in out


def test_a_remark_from_somebody_else_is_left_out_of_the_print():
    rng = np.random.default_rng(2)
    a, b = _vec(1), _vec(0, 1)
    wins = [a + rng.normal(0, 0.05, DIM) for _ in range(9)] + [b + rng.normal(0, 0.05, DIM)]
    built = ov.build_print(wins)
    assert built["ok"] and built["kept"] == 9 and built["windows"] == 10
    assert float(built["vector"] @ a) > 0.99      # the remark did not drag the print


def test_too_few_windows_is_refused():
    assert ov.build_print([_vec(1)]) == {"ok": False, "why": "too_few_windows", "windows": 1}


def test_speech_windows_are_whole_and_never_overlap():
    x = np.arange(int(7.5 * ov.RATE), dtype=np.float32)
    w = ov.speech_windows(x, 3.0)
    assert len(w) == 2 and all(len(v) == 3 * ov.RATE for v in w)
    assert w[1][0] == 3 * ov.RATE


# ---- the report -----------------------------------------------------------------------------------

def _scene():
    rows = [_row("target", s) for s in (0.62, 0.66, 0.70, 0.74, 0.78, 0.81, 0.85, 0.58, 0.69, 0.72)]
    rows += [_row("other", s) for s in (0.20, 0.25, 0.31, 0.35, 0.28, 0.22, 0.40, 0.33, 0.26, 0.30)]
    rows += [_row("nearby", s, rms_db=-52.0) for s in (0.30, 0.45, 0.50, 0.38)]
    return rows


def test_report_says_works_when_a_bar_keeps_the_other_person_out():
    lines, numbers = ov.report(_scene(), 0.50)
    assert numbers["verdict"] == "WORKS"
    rec = numbers["long"]["recommend"]
    assert 0.40 < rec["threshold"] <= 0.58 and rec["frr"] == 0.0 and rec["far"] <= 0.05
    assert any(line.startswith("VERDICT") and "WORKS" in line for line in lines)


def test_a_verdict_from_ten_lines_says_it_is_provisional():
    _, numbers = ov.report(_scene(), 0.50)
    assert numbers["far_upper95"] == pytest.approx(1 - 0.05 ** (1 / 10))     # 0 of 10 -> up to 26%
    assert numbers["provisional"] is True


def test_upper_bound_of_a_rate_never_seen():
    assert ov.upper95(0, 15) == pytest.approx(0.181, abs=0.002)
    assert ov.upper95(0, 60) == pytest.approx(0.049, abs=0.002)
    assert ov.upper95(3, 3) == 1.0 and ov.upper95(0, 0) == 1.0
    assert 0.05 < ov.upper95(1, 20) < 0.30


def test_every_voice_on_the_mic_sets_the_bar_not_only_a_folder_named_other():
    """Review 19 Nov: a second folder ('bob') scoring 0.70-0.75 was ignored and the report said WORKS."""
    rows = _scene() + [_row("bob", s) for s in np.linspace(0.70, 0.75, 10)]
    _, numbers = ov.report(rows, 0.50)
    assert set(numbers["against"]) == {"bob", "other"}
    assert numbers["long"]["recommend"]["threshold"] > 0.70
    assert numbers["verdict"] == "DOES NOT WORK"


def test_without_a_second_person_on_the_mic_there_is_no_verdict():
    target_and_bystander = [r for r in _scene() if r["label"] != "other"]
    _, numbers = ov.report(target_and_bystander, 0.50)
    assert numbers["against"] == [] and numbers["verdict"] == "UNKNOWN"
    one_line = [r for r in _scene() if r["label"] != "other"] + [_row("other", 0.2)]
    _, numbers = ov.report(one_line, 0.50)
    assert numbers["verdict"] == "UNKNOWN"                      # 1 < MIN_N


def test_a_word_too_short_to_score_counts_as_refused():
    """Live it is never accepted; dropping it made short-word refusals look rarer than they are."""
    rows = _scene() + [_row("target", None, speech_s=0.2) for _ in range(5)] + [_row("target", 0.9, speech_s=0.6)]
    _, numbers = ov.report(rows, 0.50)
    assert numbers["short"]["n_genuine"] == 6
    far, frr = ov.far_frr([ov.NO_SCORE] * 5 + [0.9], [], 0.5)
    assert frr == pytest.approx(5 / 6)


def test_report_says_does_not_work_when_the_voices_overlap():
    rows = [_row("target", s) for s in np.linspace(0.30, 0.70, 10)]
    rows += [_row("other", s) for s in np.linspace(0.35, 0.65, 10)]
    _, numbers = ov.report(rows, 0.50)
    assert numbers["verdict"] == "DOES NOT WORK"


def test_short_words_never_choose_the_bar_for_sentences():
    """5 Nov: the owner's short words were what failed. They are reported on their own; a short word
    of somebody else scoring high must not push the sentences' bar up."""
    base = _scene()
    with_short = base + [_row("other", 0.80, speech_s=0.4), _row("other", 0.79, speech_s=0.5),
                         _row("target", 0.10, speech_s=0.3)]
    _, a = ov.report(base, 0.50)
    _, b = ov.report(with_short, 0.50)
    assert a["long"]["recommend"] == b["long"]["recommend"]
    assert b["short"]["n_hard"] == 2 and b["short"]["n_genuine"] == 1


def test_the_hard_case_is_the_other_person_not_the_bystander():
    """'nearby' (no mic) is answered by level; the bar is set against 'other' on the same mic."""
    rows = _scene() + [_row("nearby", 0.95, rms_db=-55.0)]
    _, numbers = ov.report(rows, 0.50)
    assert numbers["long"]["recommend"]["threshold"] < 0.58       # the 0.95 bystander did not set it
    assert numbers["level"]["nearby_let_in"] == 0.0 and numbers["level"]["enrolled_kept"] == 1.0


def test_short_word_rules_are_estimated_worst_case_and_level_alone_lets_a_same_mic_voice_in():
    rows = _scene() + [_row("target", 0.62, speech_s=0.5), _row("target", 0.20, speech_s=0.4),
                       _row("other", 0.30, speech_s=0.5, rms_db=-26.0),
                       _row("nearby", 0.70, speech_s=0.5, rms_db=-50.0)]
    pol = ov.short_policy(rows, 0.55)
    assert pol["level"]["other"] == (1, 1)            # same mic, same loudness: level cannot tell
    assert pol["level"]["nearby"] == (0, 1)           # 25 dB down: level can
    assert pol["level"]["target"] == (2, 2)
    assert pol["level+score"]["target"] == (1, 2)     # 0.20 < 0.55 - 0.10
    assert pol["level+score"]["other"] == (0, 1)
    assert ov.short_policy([_row("other", 0.9)], 0.5) is None      # no accepted sentence: no reference


# ---- signal ---------------------------------------------------------------------------------------

def test_level_stats_and_liveness():
    x = np.array([0.0, 0.0, 0.5, -1.0], dtype=np.float32)
    st = ov.level_stats(x)
    assert st["zero"] == 0.5 and st["clip"] == 0.25 and st["peak"] == 1.0
    assert st["rms_db"] == pytest.approx(20 * np.log10(np.sqrt((0.25 + 1) / 4)), abs=0.1)
    assert ov.level_stats(np.zeros(10))["rms_db"] is None
    assert ov.liveness([0.0, 0.0, 0.0]) == "NO SOUND"
    assert ov.liveness([]) == "NO SOUND"
    assert ov.liveness([0.0, 0.0003]) == "DITHER ONLY"
    assert ov.liveness([0.0, 0.3]) == "ALIVE"


def test_the_live_down_sampler_hears_what_an_offline_read_hears():
    rng = np.random.default_rng(4)
    t = np.arange(48000 * 2) / 48000
    x = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.05 * rng.normal(size=len(t))).astype(np.float32)
    whole = vl.Decimator(48000)(x)
    d = vl.Decimator(48000)
    parts, i = [], 0
    for n in rng.integers(100, 6000, 200):                         # odd block sizes, like a device
        parts.append(d(x[i: i + n]))
        i += n
        if i >= len(x):
            break
    assert len(whole) == 32000
    assert np.allclose(np.concatenate(parts), whole, atol=1e-6)


def test_the_down_sampler_passes_speech_and_stops_what_would_alias():
    t = np.arange(48000) / 48000
    low = vl.Decimator(48000)(np.sin(2 * np.pi * 1000 * t))[2000:]
    high = vl.Decimator(48000)(np.sin(2 * np.pi * 12000 * t))[2000:]       # would alias to 4 kHz
    assert np.sqrt(np.mean(low ** 2)) > 0.68
    assert np.sqrt(np.mean(high ** 2)) < 0.01
    assert np.array_equal(vl.Decimator(16000)(np.ones(5)), np.ones(5, dtype=np.float32))
    with pytest.raises(ValueError):
        vl.Decimator(44100)


def test_an_utterance_is_judged_on_its_speech_not_its_padding():
    """The VAD hands over a word with its hangover around it: 0.4 s of "ค่ะ" arrives as ~1.4 s.
    Untrimmed it would count as a sentence and set the sentences' bar."""
    seen = []

    def fake_embed(x):
        seen.append(len(x))
        return _vec(1)

    r = ov.RATE
    t = np.arange(int(0.4 * r)) / r
    word = np.concatenate([np.full(r // 2, 1e-4), 0.3 * np.sin(2 * np.pi * 300 * t), np.full(r // 2, 1e-4)])
    row = ov.judge_utterance(word.astype(np.float32), fake_embed, _vec(1), 0.5)
    assert row["short"] and 0.35 <= row["speech_s"] <= 0.5
    assert seen and seen[0] < 0.5 * r                                  # the model saw the word only
    assert row["decision"] == "ACCEPT" and row["score"] == pytest.approx(1.0)
    hiss = ov.judge_utterance(np.full(2 * r, 5e-4, dtype=np.float32), fake_embed, _vec(1), 0.5)
    assert hiss["score"] is None and hiss["decision"] == "NONE"      # dither is not a voice


def test_pair_far_finds_the_one_close_voice_the_average_hides():
    # items x prints: speaker 0's items, speaker 1's, speaker 2's. Speaker 2 sounds like 0.
    S = np.array([[0.8, 0.1, 0.4], [0.7, 0.2, 0.6],
                  [0.1, 0.9, 0.1], [0.2, 0.8, 0.0],
                  [0.6, 0.1, 0.9], [0.55, 0.0, 0.8]])
    own = np.array([0, 0, 1, 1, 2, 2])
    pf = vl.pair_far(S, own, 0.5)
    assert np.isnan(pf[0, 0]) and np.isnan(pf[2, 2])
    assert pf[0, 2] == 1.0          # print 0 lets speaker 2 in every time
    assert pf[2, 0] == 0.5
    assert pf[1, 0] == 0.0
    assert vl.pair_mean(S, own)[0, 0] == pytest.approx(0.75)
    everyone = np.full((3, 3), 1.0)
    np.fill_diagonal(everyone, np.nan)
    assert vl.majority_share(everyone) == 1.0                  # not 6/9: the diagonal is no pair


# ---- files: personal data -------------------------------------------------------------------------

def test_a_print_round_trips_without_pickle_and_its_meta_carries_no_vector(tmp_path, monkeypatch):
    monkeypatch.setattr(ov, "_model_pin", lambda: "pin-a")
    built = {"ok": True, "vector": _vec(1, 2), "windows": 5, "kept": 5, "worst": 0.9, "self_mean": 0.95,
             "halves": 0.9, "smaller": 0.0}
    p = ov.save_print("owner", built, {"speech_s": 20.0}, folder=tmp_path)
    with np.load(p, allow_pickle=False) as z:
        assert set(z.files) == {"vector", "model", "meta"}
        assert z["meta"].dtype.kind == "U"
        meta = json.loads(str(z["meta"]))
    assert "vector" not in meta and meta["speech_s"] == 20.0
    v, m = ov.load_print("owner", folder=tmp_path)
    assert np.allclose(v, _vec(1, 2)) and m["kept"] == 5
    monkeypatch.setattr(ov, "_model_pin", lambda: "pin-b")
    with pytest.raises(SystemExit):                       # other weights = another space
        ov.load_print("owner", folder=tmp_path)


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 41, "ชื่อ", "a b", "CON", "nul", "com1"])
def test_a_print_name_is_a_label_not_a_path(bad):
    with pytest.raises(SystemExit):
        ov.safe_name(bad)


def test_recordings_and_prints_are_kept_out_of_git():
    lines = [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()]
    assert "data/voice-lock/" in lines


def test_the_server_never_imports_the_bench_and_the_robot_is_not_switched_on():
    """The CLI uses the shared core (app/one_voice.py), never the other way round; and the
    door is not switched on for the robot this round (owner 19 Nov: try it on the PC first)."""
    import re as _re

    for p in (ROOT / "app").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        assert not _re.search(r"^\s*(from|import)\s+scripts", text, _re.M), p
    robot_env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "ONE_VOICE" not in robot_env


@pytest.mark.parametrize("module", ["scripts.voice_lock_try", "app.one_voice"])
def test_importing_the_bench_loads_no_audio_or_model_library(module):
    code = (f"import sys, importlib; importlib.import_module('{module}'); "
            "print(sorted(m for m in ('sounddevice', 'sherpa_onnx', 'soundfile', 'scipy') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ---- the real models ------------------------------------------------------------------------------

def test_real_models_live_path_matches_the_offline_read():
    from app import voiceprint as vp

    model = ROOT / "data" / "wake" / "silero_vad.onnx"
    if not vp.DEFAULT_MODEL.is_file() or not model.is_file():
        pytest.skip("speaker model or Silero VAD missing (scripts/fetch_voiceprint_model.py, "
                    "scripts/fetch_vad_model.py): UNCHECKED -- that a 48 kHz device stream cut by the "
                    "VAD scores like the file it came from. The maths above is still checked.")
    try:
        import sherpa_onnx  # noqa: F401
        from scipy.signal import resample_poly
    except ImportError:
        pytest.skip("sherpa-onnx or scipy missing: the real models were NOT checked")
    embed = ov.load_embedder()
    same = ov.read_audio(ROOT / "tests" / "data" / "wake" / "other.wav")
    thai = ov.read_audio(ROOT / "tests" / "data" / "wake" / "other_imm_trap.wav")
    voice = embed(ov.trim(same))
    # The GCK path: 48 kHz, exact zeros either side (its noise gate), device-sized blocks.
    pad = np.zeros(48000, dtype=np.float32)
    x48 = np.concatenate([pad, resample_poly(same, 3, 1).astype(np.float32), pad])
    dec, seg, got = vl.Decimator(48000), ov.Segmenter(), []
    for i in range(0, len(x48), 4800):
        got += seg.feed(dec(x48[i: i + 4800]))
    got += seg.flush()
    assert len(got) >= 1
    start, longest = max(got, key=lambda s: len(s[1]))
    row = ov.judge_utterance(longest, embed, voice, 0.5)
    assert row["speech_s"] > 1.5 and row["decision"] == "ACCEPT" and row["score"] > 0.85
    other = ov.judge_utterance(thai, embed, voice, 0.5)
    assert other["score"] < row["score"] - 0.2


# --- 20 Nov: the GCK is "Microphone (GCK)", not LCS_USB_AUDIO -------------------------------------
# 19 Nov the receiver was guessed as "Microphone (LCS_USB_AUDIO)" (USB 0a67:8524); every check on it
# read exact zero and said "transmitter off". 20 Nov, owner speaking into the GCK: "Microphone (GCK)"
# peak 0.43-0.80 while LCS_USB_AUDIO stayed zero. Both the script and the page must pick the GCK.

_TWO = [
    {"index": 2, "name": "Microphone (LCS_USB_AUDIO)", "api": "MME", "channels": 1, "rate": 44100, "default": False},
    {"index": 14, "name": "Microphone (GCK)", "api": "Windows WASAPI", "channels": 2, "rate": 48000, "default": False},
    {"index": 15, "name": "Microphone (LCS_USB_AUDIO)", "api": "Windows WASAPI", "channels": 1, "rate": 48000, "default": True},
]


def test_the_script_picks_the_gck_not_the_silent_lcs_input(monkeypatch):
    devs = [dict(d, gck=any(g in d["name"] for g in ov.GCK_NAMES)) for d in _TWO]
    monkeypatch.setattr(vl, "input_devices", lambda: devs)
    assert vl.pick_device(None)["name"] == "Microphone (GCK)"
    assert [d["name"] for d in devs if d["gck"]] == ["Microphone (GCK)"]


def test_the_page_marks_the_gck_and_not_the_lcs_input():
    import re
    src = (Path(__file__).resolve().parents[1] / "client" / "voice-lock.html").read_text(encoding="utf-8")
    m = re.search(r"const GCK = \[([^\]]*)\];", src)
    assert m, "the page's GCK name list moved"
    needles = re.findall(r"'([^']*)'", m.group(1))
    hit = lambda label: any(n in label.lower() for n in needles)   # the page's own rule: label.toLowerCase().includes
    assert hit("Microphone (GCK)") and hit("Microphone (GCK) (1a2b:3c4d)")
    assert not hit("Microphone (LCS_USB_AUDIO) (0a67:8524)") and not hit("Microphone (Realtek HD Audio Mic input)")
