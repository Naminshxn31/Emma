"""The hall's sound over a bad Wi-Fi (24 Nov 2026): a recorded line's voice goes to the room screens WHOLE
at its first word (app/face_mirror.py `ahead`), the page plays it on its own clock, a screen back from a
blink asks for the rest of it, and the recorded lines get -2 dB of headroom on the room screen only.
The robot face's own road is untouched (face_mirror is mirror-only; the page's `mirror` branch)."""
import asyncio
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from app import event_cues, face_mirror

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
VOICE_DIR = ROOT / "data" / "ceremony" / "voice"
RATE = 24000


@pytest.fixture(autouse=True)
def _clean():
    face_mirror.reset()
    yield
    face_mirror.reset()


def _pcm(seconds: float, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    return (rng.integers(-3000, 3000, int(seconds * RATE))).astype("<i2").tobytes()


def _screen():
    q: asyncio.Queue = asyncio.Queue(maxsize=face_mirror.QUEUE_MAX)
    face_mirror._clients.add(q)
    return q


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def _robot_pushes(pcm: bytes, step_s: float = 0.1):
    step = int(RATE * step_s) * 2
    for i in range(0, len(pcm), step):
        face_mirror.publish_audio(pcm[i:i + step])


def _started():
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "ceremony"})


# ---- the whole line goes once, at its first word ------------------------------------------------

def test_the_line_goes_whole_at_its_first_chunk_and_the_robots_chunks_are_not_sent_again():
    _started()
    q = _screen()
    pcm = _pcm(3.0)
    face_mirror.ahead("s02_l03", pcm)
    assert _drain(q) == []                       # nothing before her first word
    _robot_pushes(pcm)
    items = _drain(q)
    head = json.loads(items[0])
    assert head == {"type": "mirror_voice", "id": "s02_l03", "tok": face_mirror._state["ahead"]["tok"],
                    "from": 0, "bytes": len(pcm)}
    assert b"".join(items[1:]) == pcm            # all of it, once, in order
    assert all(len(x) <= int(RATE * face_mirror.AHEAD_PIECE_S) * 2 for x in items[1:])


def test_a_chunk_that_is_not_the_lines_goes_as_it_always_did():
    _started()
    q = _screen()
    face_mirror.ahead("s02_l03", _pcm(1.0, seed=1))
    face_mirror.publish_audio(_pcm(0.1, seed=2))          # something else (a live answer)
    items = _drain(q)
    assert len(items) == 1 and isinstance(items[0], bytes)


def test_a_cut_stops_the_lines_rest_and_a_later_chunk_goes_as_it_comes():
    _started()
    q = _screen()
    pcm = _pcm(2.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    _drain(q)
    face_mirror.publish_json({"type": "interrupted"})
    assert _drain(q) == [json.dumps({"type": "interrupted"})]
    face_mirror.publish_audio(pcm[4800:9600])             # no longer the line's: not swallowed
    assert [type(x) for x in _drain(q)] == [bytes]


def test_a_line_that_never_started_sends_nothing_and_a_stale_one_is_dropped():
    _started()
    q = _screen()
    face_mirror.ahead("l1", _pcm(1.0))
    face_mirror.ahead_gone("l1")
    face_mirror.publish_audio(_pcm(0.1, seed=3))
    assert [type(x) for x in _drain(q)] == [bytes]


def test_a_line_that_ended_on_the_robot_while_pushing_tells_the_screens_to_stop():
    _started()
    q = _screen()
    pcm = _pcm(2.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    _drain(q)
    face_mirror.ahead_gone("l1")                          # the robot's page stopped taking it
    assert _drain(q) == [json.dumps({"type": "interrupted"})]
    face_mirror.ahead_gone("l1")                          # once
    assert _drain(q) == []


def test_a_new_call_stops_a_line_still_sounding_and_forgets_it():
    _started()
    q = _screen()
    pcm = _pcm(2.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    _drain(q)
    face_mirror.call_started()
    sent = [json.loads(x)["type"] for x in _drain(q)]
    assert sent[:2] == ["interrupted", "mirror_start"]
    assert face_mirror._state["ahead"] is None and face_mirror._state["ahead_prev"] is None


# ---- a screen that joins or comes back -----------------------------------------------------------

def test_a_screen_joining_mid_line_gets_the_voice_from_where_the_robot_is():
    _started()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])                 # first word now
    a = face_mirror._state["ahead"]
    a["at"] -= 4.0                                        # four seconds ago
    texts, voice = face_mirror._join(a["at"] + 4.0, None)
    hello = json.loads(texts[0])
    assert hello["voice"] == {"id": "l1", "tok": a["tok"], "state": "live"}
    head = json.loads(voice[0])
    assert head["type"] == "mirror_voice" and head["id"] == "l1"
    assert head["from"] == 4 * RATE * 2 and head["bytes"] == len(pcm) - head["from"]
    assert b"".join(voice[1:]) == pcm[head["from"]:]


def test_a_screen_back_from_a_blink_gets_the_rest_from_what_it_holds_and_no_new_subtitle():
    _started()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    a = face_mirror._state["ahead"]
    face_mirror._state["subtitle"] = {"type": "ceremony_subtitle", "id": "l1", "text": "x"}
    face_mirror._state["subtitle_voice_at"] = a["at"]
    have = 3 * RATE * 2
    texts, voice = face_mirror._join(a["at"] + 3.5, ("l1", have, a["tok"]))
    assert all(json.loads(t)["type"] != "ceremony_subtitle" for t in texts)       # its own clock runs on
    head = json.loads(voice[0])
    assert head["from"] == have and b"".join(voice[1:]) == pcm[have:]


def test_a_screen_back_after_a_cut_is_not_resumed():
    _started()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    face_mirror.publish_json({"type": "interrupted"})
    _texts, voice = face_mirror._join(face_mirror._state["ahead"]["at"] + 1.0,
                                       ("l1", 1000, face_mirror._state["ahead"]["tok"]))
    assert voice == []


def test_the_resume_question_is_validated():
    ok = face_mirror.resume_asked({"line": "s02_l03", "have": "96000", "tok": "7"})
    assert ok == ("s02_l03", 96000, 7)
    for bad in ({}, {"line": "", "have": "1", "tok": "1"}, {"line": "x" * 65, "have": "1", "tok": "1"},
                {"line": "a", "have": "-1", "tok": "1"}, {"line": "a", "have": "abc", "tok": "1"},
                {"line": "a", "have": str(face_mirror.AHEAD_MAX_BYTES + 1), "tok": "1"},
                {"line": "a", "have": "5"}, {"line": "a", "have": "5", "tok": "0"}, None):
        assert face_mirror.resume_asked(bad) is None


def test_a_plain_join_is_the_snapshot_it_always_was():
    face_mirror.reset()
    assert json.loads(face_mirror.snapshot()[0]) == {"type": "mirror_hello", "in_call": False}
    _started()
    kinds = [json.loads(x)["type"] for x in face_mirror.snapshot()]
    assert kinds == ["mirror_hello", "mirror_start", "ready"]


# ---- event_cues hands the line over ----------------------------------------------------------------

def test_event_cues_registers_the_pinned_pcm_and_a_refused_line_is_taken_back(monkeypatch):
    seen = []
    monkeypatch.setattr(face_mirror, "ahead", lambda i, p: seen.append(("ahead", i, len(p))))
    monkeypatch.setattr(face_mirror, "ahead_gone", lambda i: seen.append(("gone", i)))
    event_cues._voice_ahead({"id": "s1"}, _pcm(1.0))
    event_cues._voice_ahead({"id": "s1"}, None)           # no recording = nothing to send ahead
    event_cues._voice_gone({"id": "s1"})
    assert seen == [("ahead", "s1", RATE * 2), ("gone", "s1")]


def test_a_failure_in_the_screens_copy_never_stops_the_line(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(face_mirror, "ahead", boom)
    monkeypatch.setattr(face_mirror, "ahead_gone", boom)
    event_cues._voice_ahead({"id": "s1"}, _pcm(1.0))
    event_cues._voice_gone({"id": "s1"})


# ---- review 24 Nov: the cut goes first, the right play resumes --------------------------------------------

def _types(items):
    return [("bytes" if isinstance(x, bytes) else json.loads(x)["type"]) for x in items]


def test_a_red_stop_is_not_queued_behind_the_lines_unsent_voice():
    _started()
    q = _screen()
    pcm = _pcm(30.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])                 # first word: the whole line is queued
    q.put_nowait(json.dumps({"type": "tool_result", "result": {"screen": "clear"}}))   # something else, queued
    assert len(_drain_copy(q)) > 60
    face_mirror.publish_json({"type": "interrupted"})
    items = _drain(q)
    assert _types(items) == ["tool_result", "interrupted"]        # no voice ahead of the stop, order kept


def test_subtitle_cut_and_ahead_gone_and_a_new_call_also_take_the_unsent_voice_out():
    for how in ("subtitle_cut", "ahead_gone", "call_started"):
        face_mirror.reset()
        _started()
        q = _screen()
        pcm = _pcm(10.0)
        face_mirror.ahead("l1", pcm)
        face_mirror.publish_audio(pcm[:4800])
        if how == "subtitle_cut":
            face_mirror.subtitle_cut()
        elif how == "ahead_gone":
            face_mirror.ahead_gone("l1")
        else:
            face_mirror.call_started()
        assert "bytes" not in _types(_drain(q)), how


def test_the_robots_own_chunks_and_other_messages_survive_a_purge():
    _started()
    q = _screen()
    pcm = _pcm(5.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    face_mirror.publish_json({"type": "interrupted"})
    live = _pcm(0.1, seed=9)
    face_mirror.publish_audio(live)                       # the robot's chunk after the cut: goes as it comes
    assert _drain(q) == [json.dumps({"type": "interrupted"}), live]


def test_speech_started_with_the_robot_still_pushing_cuts_the_line_and_the_unsent_voice():
    _started()
    q = _screen()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    face_mirror.publish_json({"type": "speech_started"})
    assert _types(_drain(q)) == ["speech_started"]        # the page stops its queue on it (see the page test)
    face_mirror.publish_audio(pcm[4800:9600])             # the robot goes on saying it: heard live, once
    assert [type(x) for x in _drain(q)] == [bytes]
    assert face_mirror._state["ahead"]["state"] == "cut"


def test_a_cut_while_the_screen_still_plays_a_line_the_server_clock_calls_over():
    # blink + red stop + a screen behind the server's clock: the line is "over" by the clock (20 s ago)
    _started()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    face_mirror.publish_audio(pcm[4800:])                 # all pushed -> "said"
    a = face_mirror._state["ahead"]
    a["at"] -= 20.0                                       # 10 s line began 20 s ago: over by the server clock
    assert face_mirror._voice_state(a)["state"] == "over"
    face_mirror.subtitle_cut()
    assert face_mirror._voice_state(a)["state"] == "cut"
    _texts, voice = face_mirror._join(a["at"] + 21.0, ("l1", 1000, a["tok"]))
    assert voice == []                                    # a screen back from the blink is not resumed
    # a line long over for the screen too (beyond the lag margin) is left alone
    a["state"] = "said"
    a["at"] -= face_mirror.AHEAD_LAG_S + 5
    face_mirror._state["ahead_prev"] = None
    face_mirror.publish_json({"type": "interrupted"})
    assert a["state"] == "said"


def test_a_resume_of_another_play_of_the_same_line_is_not_matched():
    _started()
    pcm = _pcm(10.0)
    face_mirror.ahead("l1", pcm)
    face_mirror.publish_audio(pcm[:4800])
    first = face_mirror._state["ahead"]["tok"]
    face_mirror.ahead("l1", pcm)                          # the same line played again while the screen was away
    face_mirror.publish_audio(pcm[:4800])
    a = face_mirror._state["ahead"]
    assert a["tok"] != first
    now = a["at"] + 1.0
    _t, voice = face_mirror._join(now, ("l1", 96000, first))
    assert json.loads(voice[0])["from"] != 96000          # not resumed from the old play's byte count
    _t, voice = face_mirror._join(now, ("l1", 96000, a["tok"]))
    assert json.loads(voice[0])["from"] == 96000


def test_a_pending_line_that_never_started_is_dropped_after_the_pending_window():
    _started()
    q = _screen()
    pcm = _pcm(2.0)
    face_mirror.ahead("l1", pcm)
    face_mirror._state["ahead"]["set"] -= face_mirror.AHEAD_PENDING_S + 1
    face_mirror.publish_audio(pcm[:4800])                 # a late chunk, equal to the line's first bytes
    assert face_mirror._state["ahead"] is None
    assert [type(x) for x in _drain(q)] == [bytes] and len(_drain_copy(q)) == 0


def _drain_copy(q):
    return list(q._queue)


# ---- CEREMONY_VOICE_ON_ROBOT (owner 24 Nov): her voice on the robot's speaker only -----------------------

def _ready(profile):
    return face_mirror.view({"type": "ready", "output_rate": RATE, "profile": profile})


def test_off_nothing_about_the_voice_is_said_to_any_page(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ceremony_voice_on_robot", False)
    assert "voice_on_robot" not in _ready("ceremony")
    _started()
    assert all("voice_on_robot" not in json.loads(t) for t in face_mirror.snapshot())
    assert face_mirror.voice_on_robot() is False


def test_on_only_the_ceremonys_ready_carries_it_and_a_joining_screen_gets_it(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ceremony_voice_on_robot", True)
    assert _ready("ceremony")["voice_on_robot"] is True
    assert "voice_on_robot" not in _ready("event") and "voice_on_robot" not in _ready("condo")
    _started()
    ready = [json.loads(t) for t in face_mirror.snapshot() if json.loads(t)["type"] == "ready"][0]
    assert ready["voice_on_robot"] is True
    face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": "event"})
    assert face_mirror.voice_on_robot() is False


@pytest.mark.parametrize("flag,profile,expect", [(False, "ceremony", 0.8), (True, "ceremony", 0.0),
                                                 (True, "event", 0.8), (True, "condo", 0.8)])
def test_the_echo_tail_applies_only_where_the_screen_really_plays_her_voice(monkeypatch, flag, profile, expect):
    from app import room_screens
    from app.config import settings
    monkeypatch.setattr(settings, "room_screens_enabled", True)
    monkeypatch.setattr(settings, "room_screen_sound", True)
    monkeypatch.setattr(settings, "room_screen_echo_tail_s", 0.8)
    monkeypatch.setattr(settings, "ceremony_voice_on_robot", flag)
    monkeypatch.setitem(room_screens.STATE, "open", True)
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": RATE, "profile": profile})
    assert room_screens.echo_tail_s() == expect
    face_mirror.call_ended()
    assert room_screens.echo_tail_s() == 0.8                    # (no ceremony call any more: as ever)


def test_the_debuts_sound_warnings_do_not_change_with_her_voice_on_the_robot(monkeypatch):
    from app import ceremony_reveal
    from app.config import settings
    out = {}
    for flag in (False, True):
        monkeypatch.setattr(settings, "ceremony_voice_on_robot", flag)
        face_mirror.reset()
        for sound, w in ((True, 1920), (False, 1920)):
            face_mirror._kinds.clear()
            face_mirror._clients.clear()
            screen = _screen()
            face_mirror._kinds[screen] = {"sound": sound, "w": w, "h": 1080, "coarse": False}
            out[(flag, sound)] = [x for x in ceremony_reveal.warnings() if x in ("no_sound_screen", "two_sound_screens")]
    assert out[(True, True)] == out[(False, True)] == []                     # the music still has its screen
    assert out[(True, False)] == out[(False, False)] == ["no_sound_screen"]


def test_the_page_names_for_her_voice_stay_out_of_the_shared_handlers_and_the_robot_face():
    html = PAGE.read_text(encoding="utf-8")
    on_message = html.split("function onMessage(", 1)[1].split(chr(10) + "    function ", 1)[0]
    assert "mirrorHerVoice" not in on_message
    assert html.count("mirrorHerVoice(") == 2                                  # its definition, its one caller
    assert "if (mirror && mirrorHerVoiceSilent) playbackBaseGain = 0.0001;" in html     # never 0: the clock would stop
    # a resume asks for the play it holds (review 24 Nov)
    assert "'&tok=' + mirrorBlink.tok" in html and "v.tok === k.tok" in html


# ---- the page: mirror only ------------------------------------------------------------------------

def test_the_page_plays_the_whole_line_on_its_own_clock_and_only_on_the_mirror():
    html = PAGE.read_text(encoding="utf-8")
    assert "if (mirror) src.connect(mirrorOut(audioCtx));\n      else src.connect(playbackOutput(audioCtx));" in html
    assert "MIRROR_GAIN_DB = { line: -2, live: 0 }" in html
    # the voice frames after mirror_voice are the line's; a blink keeps the line only on the ceremony screen
    assert "if (t === 'mirror_voice') { mirrorVoiceAt(said); return; }" in html
    assert "document.documentElement.classList.contains('mirror-show')" in html
    # no new names in the shared handlers (CLAUDE.md): the new code is inside sock.onmessage / its helpers
    on_message = html.split("function onMessage(", 1)[1].split("\n    function ", 1)[0]
    assert "mirrorVoice" not in on_message and "mirrorBlink" not in on_message


# ---- the -2 dB headroom holds every pinned take under -0.4 dBTP ---------------------------------------

def _true_peak_db(path: Path, gain_db: float) -> float:
    from scipy.signal import resample_poly
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768.0
    x = x * 10 ** (gain_db / 20)
    y = resample_poly(x, 4, 1)
    return 20 * np.log10(max(np.abs(y).max(), 1e-9))


def test_minus_two_db_keeps_every_pinned_take_under_minus_point_four_dbtp():
    files = sorted(VOICE_DIR.glob("*.wav")) if VOICE_DIR.is_dir() else []
    if not files:
        pytest.skip("data/ceremony/voice has no recordings here -- NOT checked: the headroom of the pinned takes")
    worst = max(_true_peak_db(f, -2.0) for f in files)
    assert worst < -0.4, worst
