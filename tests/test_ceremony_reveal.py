"""The debut on card 1 (owner 23 Nov 2026: "ซาวน์นับถอยหลัง 10 9 8 ขึ้นจอด้วยโดยเมื่อถึง 0 ก็ให้ทุกอย่างมืด แล้วก็มี
ซาวน์ แล้วหุ่นก็เปิดตัว"; "แนวนอน.mp4 เพิ่มในตอนเปิดตัว"):

  * app/ceremony_reveal.py -- what card 1 calls (ready / start / impact_at / abort / cancelled / running):
    sends the debut to the room screen(s) in the ceremony's show mode (nothing, with a reason, otherwise),
    says when each part is (the walk starts on the hit), and says when it was cancelled (any stop before
    the hit: no walk);
  * data/ceremony/reveal.json names its sound (one pinned mix with its times: scripts/ceremony_reveal_sound.py)
    and its video (the owner's wave, pinned and checked); the sound is the screen's one fetchable sound;
    deploy ships both;
  * client/assets/ceremony-show.js (revealStart / revealStop) -- the countdown, the black, the hit, the
    wave, on the sound's clock, ending on the opening clip's held last frame; run here in Chromium with
    the real files;
  * client/voice-preview.html -- the mirror socket's messages and every stop the page has.

Each test was seen RED with its change undone (in a copy).
"""
import asyncio
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import ceremony_reveal, ceremony_slides as cs, face_mirror
from tests.test_ceremony_acts import real  # noqa: F401 -- fixture, used by name

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"
ENGINE = CLIENT / "assets" / "ceremony-show.js"
STYLE = CLIENT / "assets" / "ceremony-show.css"
PAGE = CLIENT / "voice-preview.html"
TOOL = ROOT / "scripts" / "ceremony_reveal_sound.py"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HALL = {"sound": True, "w": 1920, "h": 1080, "coarse": False}        # (the lobby's screen as its page says it is)


def _screen(kind=HALL) -> asyncio.Queue:
    queue = asyncio.Queue(maxsize=50)
    face_mirror._clients.add(queue)
    if kind is not None:
        face_mirror._kinds[queue] = kind
    return queue


@pytest.fixture
def screens(monkeypatch):
    """face_mirror with one fake hall screen playing the sound (its queue), no call; the owner has
    listened to the mix (its own test says what happens when he has not)."""
    face_mirror.reset()
    ceremony_reveal.reset()
    monkeypatch.setattr(ceremony_reveal, "_listened", lambda: True)
    queue = _screen()
    yield queue
    face_mirror.reset()
    ceremony_reveal.reset()


@pytest.fixture
def clock(monkeypatch):
    """The debut's clock, moved by hand."""
    now = [100.0]
    monkeypatch.setattr(ceremony_reveal, "_clock", lambda: now[0])
    return now


@pytest.fixture(autouse=True)
def lit(monkeypatch):
    """The lit debut (its light show, the logo, the wave) is what this file checks: kept whole in the code.
    reveal.json has said `dark` since 25 Nov (the owner: the opening is dark) -- the DARK tests at the end
    of the file take this away (`black`)."""
    monkeypatch.setattr(ceremony_reveal, "dark", lambda: False)


_REAL_DARK = ceremony_reveal.dark


@pytest.fixture
def black(monkeypatch):
    """reveal.json with `dark` on (the owner's 02:15 choice; at 03:20 he chose the lit debut, `false`, which the
    real file says -- test_the_real_debut_is_the_lit_one_now)."""
    real_cfg = dict(cs.reveal_config())
    monkeypatch.setattr(ceremony_reveal, "dark", _REAL_DARK)
    monkeypatch.setattr(cs, "reveal_config", lambda: {**real_cfg, "dark": True})


def test_the_real_debut_is_black_until_the_hit_then_lit(real):
    """Owner 25 Nov ~03:20: "โลโก้เล่นจนจบ" (the lit debut); ~04:25 after rehearsal 6: "ฉากแรกตอนปิดไฟจอก็ควรดำ ถ้าจะขึ้นก็ต้อง
    ตรงสปอตไลท์นะ" -- black until the spotlight (the hit), the lit debut from there: the light, the logo clip, the wave."""
    assert json.loads((ROOT / "data" / "ceremony" / "reveal.json").read_text(encoding="utf-8"))["dark"] == "build"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ceremony_reveal, "dark", _REAL_DARK)
        spec = ceremony_reveal.timeline()
    assert _REAL_DARK() == "build" and spec["dark"] == "build"
    assert spec["video"]["at_s"] == spec["impact_s"] and spec["video_problem"] is None    # (the wave, as lit)
    assert spec["length_s"] > spec["settle_s"] > spec["impact_s"]                         # (the lit timeline)


def _sent(queue) -> list[dict]:
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item))
    return out


def _row(asset_id: str) -> dict:
    rows = json.loads((cs.index_dir() / "index.json").read_text(encoding="utf-8"))
    return next(r for r in rows if r.get("id") == asset_id)


# ---- the sound and the video: assets of the index, pinned, their times data ---------------------------

def test_the_debut_sound_is_one_pinned_mix_whose_times_are_its_own(real):
    spec = ceremony_reveal.timeline()
    assert spec["sound_problem"] is None, spec
    sound = cs.reveal_sound_id()
    entry = cs.asset_index()[sound]
    assert entry["kind"] == "audio" and spec["src"] == "/ceremony-asset/%s" % entry["sha256"]
    found = cs.asset_file(entry["sha256"])
    assert found is not None and found[1] == "audio/mpeg"
    assert cs.act_asset_problem(sound) == "not_a_picture" and cs.act_asset_problem(sound, video=True) == "not_a_video"
    # the debut the owner asked for after listening ("เอาเวลานับออก"): no countdown -- no numbers -- the
    # music's build of 4-6 s from a bar line, its beats a steady grid from the press (the light pulses on
    # them), then 1.5 s of black and the hit
    assert spec["numbers"] == []
    beats = spec["beats"]
    assert 4.0 <= spec["zero_s"] <= 6.0 and abs(spec["impact_s"] - spec["zero_s"] - 1.5) < 0.02
    assert beats and beats[0] <= 0.1 and beats[-1] < spec["zero_s"]
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert all(abs(g - gaps[0]) < 0.01 for g in gaps) and 0.5 <= gaps[0] <= 0.85
    row = _row(sound)
    assert row["numbers"] == [] and row["beats"] == beats
    assert row["zero_s"] == spec["zero_s"] and row["impact_s"] == spec["impact_s"]
    assert "listened" not in row                                  # (the owner listens to this mix again)
    # the loudness asked for: a little over Emma's -15.5, the true peak at most -1 dBTP
    assert -14.5 <= row["lufs"] <= -12.5 and row["true_peak"] <= -1.0
    # the owner's Pixabay download, credited -- and only what is in it (the counting voice is not)
    licence = row["licence"]
    assert "Pixabay Content License" in licence["note"] and "owner" in licence["note"]
    assert {c["author"] for c in licence["credits"]} == {"AudioAtlant"}
    assert all(c["url"].startswith("https://pixabay.com/sound-effects/") for c in licence["credits"])
    assert "voiceofruthie" not in json.dumps(row) and "countdown-echo" not in row["source"]
    # the clip starts so its flare is lit on the hit; the screen asks for it all when it opens
    assert spec["clip_at_s"] == round(spec["impact_s"] - ceremony_reveal.CLIP_PEAK_S, 3)
    assert cs.acts_manifest()["reveal"] == ceremony_reveal.info()
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed -- NOT checked here: that the hit and the black are where the file has them")
    tool = _module("reveal_sound_tool", TOOL)
    x = tool.load(entry["file"], 1)
    # the hit the index holds is the file's own, measured again here
    assert abs(tool.hit(x, start=spec["impact_s"] - 0.4, within=1.0) - spec["impact_s"]) <= 0.02
    # ... and at 0 the sound breaks to silence until it
    level = tool.levels(x)
    quiet = level[int((spec["zero_s"] + 0.3) / 0.01):int((spec["impact_s"] - 0.05) / 0.01)]
    assert len(quiet) and quiet.max() < -55, quiet.max()
    assert level[int(0.05 / 0.01):int(spec["zero_s"] / 0.01)].mean() > -30      # (the music, from the press)
    lufs, peak = tool.loudness(entry["file"])
    assert abs(lufs - row["lufs"]) < 0.2 and peak <= -1.0


def test_the_owners_wave_is_pinned_checked_and_starts_on_the_hit(real):
    spec = ceremony_reveal.timeline()
    vid = cs.reveal_video_id()
    assert spec["video_problem"] is None and cs.act_asset_problem(vid, video=True) is None
    entry = cs.asset_index()[vid]
    row = _row(vid)
    assert row["people"] == [] and "no people" in row["checked"] and "แนวนอน.mp4" in row["source"]
    assert spec["video"] == {"src": "/ceremony-asset/%s" % entry["sha256"], "at_s": spec["impact_s"],
                             "length_s": row["duration"]}
    # it rises behind the logo from the hit; the dark lifts once it has drained away
    assert spec["settle_s"] == round(spec["impact_s"] + row["duration"], 3)
    assert spec["length_s"] == round(spec["settle_s"] + ceremony_reveal.LIFT_S, 3)
    # cut for the screen on Wi-Fi: small enough, 1080p, no sound of its own
    assert entry["file"].stat().st_size <= 15_000_000 and (row["w"], row["h"]) == (1920, 1080)
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed -- NOT checked here: that the video has no sound track")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(entry["file"])],
                           capture_output=True, check=True).stdout
    assert [s["codec_type"] for s in json.loads(probe)["streams"]] == ["video"]


def test_a_sound_is_never_served_unless_it_is_the_debuts_and_its_bytes(real, tmp_path, monkeypatch):
    """The screen's one fetchable sound is the one reveal.json names, as its pin says. Not gated on a
    person's `checked` (the mix script used to write that itself -- review 23 Nov): the owner's
    `listened` is a warning on the card, below."""
    folder = tmp_path / "assets"
    folder.mkdir()
    (folder / "a.mp3").write_bytes(b"ID3 not really")
    sha = hashlib.sha256(b"ID3 not really").hexdigest()
    rows = [{"id": "other_sfx", "kind": "audio", "file": "a.mp3", "sha256": sha, "candidate": True, "ok": True,
             "people": [], "about": ["debut"], "shows": "x"}]
    (folder / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp_path / "reveal.json").write_text(json.dumps({"sound": "debut_mix"}), encoding="utf-8")
    monkeypatch.setattr(cs, "index_dir", lambda: folder)
    cs._index_cache.update(key=None, doc={})
    assert cs.reveal_sound_id() == "debut_mix"
    assert cs.asset_file(sha) is None and cs.reveal_sound_problem() == "no_reveal_sound"   # (not the debut's id)
    rows[0].update(id="debut_mix", ok=False)
    (folder / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    assert cs.asset_file(sha) is None and cs.reveal_sound_problem() == "asset_excluded"
    rows[0]["ok"] = True
    (folder / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    assert cs.asset_file(sha) is not None and cs.reveal_sound_problem() is None
    # (the settings name another sound: this one is no longer the screen's)
    (tmp_path / "reveal.json").write_text(json.dumps({"sound": "debut_mix_two"}), encoding="utf-8")
    assert cs.asset_file(sha) is None
    (tmp_path / "reveal.json").write_text(json.dumps({"sound": "debut_mix"}), encoding="utf-8")
    (folder / "a.mp3").write_bytes(b"ID3 swapped")                       # (other bytes than the pin)
    assert cs.asset_file(sha) is None and cs.reveal_sound_problem() == "asset_changed"


def test_the_mix_script_records_what_it_measured_and_never_that_a_person_listened(real):
    """Review 23 Nov: the script wrote the "a person checked it" note itself. Now `measured` is the
    script's, `listened` only the owner's (the `listened` command, pinned to the bytes)."""
    row = _row(cs.reveal_sound_id())
    assert "checked" not in row and row["measured"]["by"] == "scripts/ceremony_reveal_sound.py mix"
    assert row["measured"]["true_peak"] <= -1.0 and "voice_words_s" not in row["measured"]
    assert 85 <= row["measured"]["tempo_bpm"] <= 95                                # (the bed's own beat, measured)
    source = TOOL.read_text(encoding="utf-8")
    mix = source[source.index("def mix(args)"):source.index("def fade_in(")]
    assert '"checked"' not in mix and '"listened"' not in mix
    assert 'row["listened"] = {"by": args.by' in source


def test_deploy_ships_the_debuts_sound_and_video():
    rs = _module("robot_server_for_reveal", ROOT / "scripts" / "robot_server.py")
    ships = rs.ceremony_film_files(ROOT)
    cfg = json.loads((ROOT / "data" / "ceremony" / "reveal.json").read_text(encoding="utf-8"))
    assert "data/ceremony/assets/%s.mp3" % cfg["sound"] in ships
    assert "data/ceremony/assets/%s.mp4" % cfg["video"] in ships


# ---- the entry point card 1 calls -----------------------------------------------------------------

def _ceremony_call():
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "ceremony"})


def _start() -> dict:
    return asyncio.run(ceremony_reveal.start())


def test_ready_and_start_need_a_screen_in_the_ceremonys_show_mode_and_say_when_each_part_is(real, screens, clock):
    face_mirror._clients.discard(screens)
    assert ceremony_reveal.ready() == (False, "no_screen") and _start()["ok"] is False
    face_mirror._clients.add(screens)
    assert ceremony_reveal.ready() == (False, "not_ceremony")                         # (no call)
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "event"})
    got = _start()                                                                     # (the event app's call)
    assert got["ok"] is False and got["why"] == "not_ceremony" and got["run"] == ""
    assert ceremony_reveal.why_th("not_ceremony") and ceremony_reveal.why_th("no_screen") != ceremony_reveal.why_th("not_ceremony")
    assert not any(m["type"] == "ceremony_reveal" for m in _sent(screens))
    assert ceremony_reveal.impact_at() is None and not ceremony_reveal.running() and not ceremony_reveal.cancelled()
    _ceremony_call()
    _sent(screens)
    assert ceremony_reveal.ready() == (True, None)
    got = _start()
    spec = ceremony_reveal.timeline()
    lag = ceremony_reveal.START_LAG_S
    assert got["ok"] and got["why"] is None and got["run"].startswith("debut-")
    # every time from the send, the screen's start lag in it -- the walk goes out on impact_at()
    assert got["impact_in_s"] == round(spec["impact_s"] + lag, 3) and got["zero_in_s"] == round(spec["zero_s"] + lag, 3)
    assert got["numbers_in_s"] == []                                               # (no countdown)
    assert got["length_s"] == round(spec["length_s"] + lag, 3)
    assert ceremony_reveal.impact_at() == pytest.approx(clock[0] + got["impact_in_s"])
    sent = _sent(screens)
    assert sent == [{"type": "ceremony_reveal", "seq": int(got["run"].split("-")[1]), "numbers": spec["numbers"],
                     "beats": spec["beats"], "zero_s": spec["zero_s"], "impact_s": spec["impact_s"], "clip_at_s": spec["clip_at_s"],
                     "settle_s": spec["settle_s"], "length_s": spec["length_s"], "src": spec["src"], "video": spec["video"]}]
    # where it is, from the server's own clock
    assert ceremony_reveal.running() and ceremony_reveal.where()["phase"] == "build"
    clock[0] += got["zero_in_s"] + 0.1
    assert ceremony_reveal.where()["phase"] == "dark"
    clock[0] += 1.5
    assert ceremony_reveal.where()["phase"] == "impact"
    clock[0] += got["length_s"]
    assert not ceremony_reveal.running() and not ceremony_reveal.cancelled()
    # abort: idempotent, and the screens are told even with nothing running
    ceremony_reveal.abort("red_stop")
    ceremony_reveal.abort("red_stop")
    assert _sent(screens) == [{"type": "ceremony_reveal_stop"}] * 2


def test_ready_never_refuses_a_screen_and_warns_on_what_the_staff_should_know(real, screens, clock, monkeypatch):
    """Review 23 Nov: the sound plays only on a screen opened with ?sound=1 -- the staff must know before
    the press if none does, or two do (the mix doubled), or the owner never listened to this mix. Re-review:
    a screen is never refused for what it is (a 1920 TV at 200% scaling read 960 px and was refused, so the
    hall's TV stayed dark while the robot came out): only phone-like screens connected is a warning, and
    the debut goes to every screen."""
    _ceremony_call()
    assert ceremony_reveal.ready() == (True, None) and ceremony_reveal.warnings() == []
    face_mirror._kinds[screens] = {**HALL, "sound": False}                        # (opened without sound=1)
    assert ceremony_reveal.ready() == (True, "no_sound_screen")
    got = _start()
    assert got["ok"] and got["why"] == "no_sound_screen" and got["warnings"] == ["no_sound_screen"]
    assert "เงียบ" in ceremony_reveal.why_th("no_sound_screen")
    face_mirror._kinds[screens] = HALL
    two = _screen()                                                               # (a second screen aloud)
    assert ceremony_reveal.ready() == (True, "two_sound_screens")
    face_mirror._clients.discard(two)
    # the hall's TV at 200% scaling, a zoomed or not-yet-maximised window: still the hall's screen
    for kind in ({"sound": True, "w": 960, "h": 540, "coarse": False},
                 {"sound": False, "w": 800, "h": 600, "coarse": False}):
        face_mirror._kinds[screens] = kind
        ok, why = ceremony_reveal.ready()
        assert ok and why != "small_screen_only", (kind, why)
    # only a staff phone (touch-first, portrait) connected: a warning, and the debut still goes to it
    face_mirror._kinds[screens] = {"sound": False, "w": 1170, "h": 2532, "coarse": True}
    assert ceremony_reveal.ready() == (True, "small_screen_only")
    assert ceremony_reveal.warnings() == ["small_screen_only", "no_sound_screen"]          # (all of them)
    _sent(screens)
    got = _start()
    assert got["ok"] and got["why"] == "small_screen_only" and [m["type"] for m in _sent(screens)] == ["ceremony_reveal"]
    assert ceremony_reveal.why_th("small_screen_only")
    # nothing connected is the one refusal of the kind
    face_mirror._clients.discard(screens)
    assert ceremony_reveal.ready() == (False, "no_screen") and _start()["why"] == "no_screen"
    # the owner's ear: a warning only -- the debut still runs (pinned to the bytes: a new mix asks again)
    face_mirror._clients.add(screens)
    face_mirror._kinds[screens] = HALL
    monkeypatch.setattr(ceremony_reveal, "_listened", lambda: False)
    assert ceremony_reveal.ready() == (True, "not_listened") and _start()["ok"]
    # what a page says it is, from its socket's query
    assert face_mirror.kind_from_query({"sound": "1", "w": "3840", "h": "2160", "coarse": "0"}) == \
        {"sound": True, "w": 3840, "h": 2160, "coarse": False}
    assert face_mirror.kind_from_query({"token": "x"}) is None                   # (a page from before: unknown)
    assert face_mirror.kind_from_query({"sound": "0", "w": "abc"}) == {"sound": False, "w": 0, "h": 0, "coarse": False}
    assert set(ceremony_reveal.WARNINGS) == {"small_screen_only", "no_sound_screen", "two_sound_screens", "not_listened"}
    assert "no_show_screen" not in ceremony_reveal.WHY_TH


def test_listened_is_the_owners_and_pinned_to_the_mixs_bytes(real, tmp_path, monkeypatch):
    rows = json.loads((cs.index_dir() / "index.json").read_text(encoding="utf-8"))
    sound = next(r for r in rows if r.get("id") == cs.reveal_sound_id())
    monkeypatch.setattr(ceremony_reveal, "_row", lambda asset_id: sound if asset_id == sound["id"] else {})
    assert ceremony_reveal._listened() is False
    sound["listened"] = {"by": "pete", "on": "2026-11-23", "sha256": sound["sha256"]}
    assert ceremony_reveal._listened() is True
    sound["listened"]["sha256"] = "0" * 64                                         # (another mix's)
    assert ceremony_reveal._listened() is False


def test_the_debut_stops_on_exactly_what_the_page_stops_it_on(real, screens, clock):
    """Review 23 Nov (and its harness server_asym_rv.py): the page and the server's run take the debut down
    on the same things -- otherwise the robot walks out over the held logo. Down: card 1's abort, a second
    start, the red stop, the call over, show mode over (another app's ready), a slide coming up. Not: an
    interrupted (the counting voice in the hall), a line's own end even cut short, a new call's start (its
    `ready` decides), and not after the hit for the walk that has gone out."""
    _ceremony_call()

    async def waiting(run):
        task = asyncio.ensure_future(ceremony_reveal.until_impact(run))
        await asyncio.sleep(0.02)
        early = task.done()
        clock[0] += 60
        return early, await asyncio.wait_for(task, 2)

    got = _start()
    assert asyncio.run(waiting(got["run"])) == (False, True) and not ceremony_reveal.cancelled()
    downs = {
        "abort": lambda: ceremony_reveal.abort("red_stop"),
        "second start": _start,
        "red stop": face_mirror.subtitle_cut,
        "call over": face_mirror.call_ended,
        "another app": lambda: face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "event"}),
        "a slide": lambda: face_mirror.subtitle({"id": "s02_l03", "text": "x", "slide": {"template": "act"}}),
    }
    for name, stopping in downs.items():
        _ceremony_call()
        got = _start()
        clock[0] += 5                                                     # (in the countdown)
        assert ceremony_reveal.impact_at() is not None, name
        stopping()
        assert ceremony_reveal.cancelled_before_impact(got["run"]) is True, name
        assert asyncio.run(asyncio.wait_for(ceremony_reveal.until_impact(got["run"]), 2)) is False, name
        if name != "second start":
            assert ceremony_reveal.cancelled() and not ceremony_reveal.running() and ceremony_reveal.impact_at() is None, name
    assert ceremony_reveal.cancelled_before_impact("debut-9999") is True   # (not known: no walk either)
    keeps = {
        "interrupted": lambda: face_mirror.publish_json({"type": "interrupted"}),
        "a line's own cut end": lambda: (face_mirror.subtitle({"id": "s02_l03", "text": "x"}),
                                         face_mirror.subtitle_end("s02_l03", cut=True)),
        "a new call": face_mirror.call_started,
        "the ceremony's ready again": lambda: face_mirror.publish_json({"type": "ready", "profile": "ceremony"}),
    }
    for name, keeping in keeps.items():
        _ceremony_call()
        got = _start()
        clock[0] += 5
        keeping()
        assert ceremony_reveal.running() and not ceremony_reveal.cancelled() and ceremony_reveal.impact_at(), name
    # after the hit the walk has gone out: the red stop takes the screen's debut down, the walk stays out
    _ceremony_call()
    got = _start()
    clock[0] += got["impact_in_s"] + 1
    face_mirror.subtitle_cut()
    assert not ceremony_reveal.running() and ceremony_reveal.cancelled()
    assert not ceremony_reveal.cancelled_before_impact(got["run"])


def test_a_screen_back_from_a_blink_is_told_when_the_debut_is_over(real, screens, clock, monkeypatch):
    """The page plays the debut on through its socket closing (the sound and the wave are already there);
    when it is back, the snapshot stops it only if the server's debut is over or was taken down."""
    _ceremony_call()
    stop = json.dumps({"type": "ceremony_reveal_stop"})
    assert stop not in face_mirror.snapshot()                                     # (no debut yet: nothing said)
    _start()
    assert stop not in face_mirror.snapshot()                                     # (live: it plays on)
    ceremony_reveal.abort("red_stop")
    assert stop in face_mirror.snapshot()
    _start()
    real_monotonic = face_mirror.time.monotonic
    monkeypatch.setattr(face_mirror.time, "monotonic", lambda: real_monotonic() + 120)
    assert stop in face_mirror.snapshot()                                         # (over by now)


def test_a_debut_without_its_sound_or_video_still_runs_and_says_so(real, screens, clock, monkeypatch):
    _ceremony_call()
    sound_problem = cs.reveal_sound_problem
    monkeypatch.setattr(cs, "reveal_sound_problem", lambda: "asset_file_missing")
    assert ceremony_reveal.ready() == (False, "sound_unusable")
    got = _start()
    assert got["ok"] and got["why"] == "sound_unusable" and ceremony_reveal.why_th("sound_unusable")
    sent = [m for m in _sent(screens) if m["type"] == "ceremony_reveal"]
    cfg = cs.reveal_config()
    assert sent and "src" not in sent[0]                                  # (the build silent, on the settings' times)
    assert cfg["count_from"] == 0 and sent[0]["numbers"] == []
    assert sent[0]["zero_s"] == cfg["build_s"] and sent[0]["impact_s"] == round(cfg["build_s"] + cfg["blackout_s"], 3)
    assert sent[0]["beats"] and abs(sent[0]["beats"][1] - sent[0]["beats"][0] - cfg["beat_s"]) < 0.01
    monkeypatch.setattr(cs, "reveal_sound_problem", sound_problem)
    monkeypatch.setattr(cs, "act_asset_problem", lambda *a, **k: "asset_changed")
    assert ceremony_reveal.ready() == (False, "video_unusable")
    got = _start()
    assert got["ok"] and got["why"] == "video_unusable"
    sent = [m for m in _sent(screens) if m["type"] == "ceremony_reveal"]
    assert sent and "video" not in sent[0] and "src" in sent[0]


@pytest.mark.parametrize("change", [
    {"numbers": [[10, 1.0], [8, 2.0]]},                         # (not counting down by one)
    {"numbers": [[2, 1.0], [1, 0.5]]},                          # (out of order)
    {"zero_s": 0.8},                                            # (0 before the last number)
    {"impact_s": 30.0},                                         # (the hit after the file's end)
    {"numbers": "10 9 8"},
    {"beats": [0.5, 0.4]},                                      # (beats out of order)
    {"beats": [0.5, 3.5]},                                      # (a beat in the black)
])
def test_a_mix_whose_times_do_not_hold_together_is_not_trusted(change):
    good = {"numbers": [[2, 1.0], [1, 2.0]], "beats": [0.0, 0.7, 1.4], "zero_s": 3.0, "impact_s": 4.5, "duration": 9.0}
    assert ceremony_reveal._sound_times(good) is not None
    assert ceremony_reveal._sound_times({**good, "numbers": []}) is not None     # (no countdown: the owner's)
    assert ceremony_reveal._sound_times({**good, **change}) is None


# ---- the page: the socket's messages, every stop -----------------------------------------------------

def _between(text: str, start: str, end: str) -> str:
    a = text.index(start)
    return text[a:text.index(end, a)]


def test_the_page_plays_the_debut_from_the_socket_and_stops_it_on_what_the_server_stops_it_on():
    """The page's half of test_the_debut_stops_on_exactly_what_the_page_stops_it_on (review 23 Nov)."""
    page = PAGE.read_text(encoding="utf-8")
    sock = _between(page, "      sock.onmessage = function (m) {", "      sock.onclose = function () {")
    assert "if (t === 'ceremony_reveal') { mirrorReveal(said); return; }" in sock
    assert "if (t === 'ceremony_reveal_stop') { mirrorRevealStop(); return; }" in sock
    # down: the red stop's end (cut, no line's id), the call over, show mode over (mirrorShowMode)
    # (24 Nov: her live Thai line's end is taken first -- mirrorLiveEndHeld -- then the script line's rule as before)
    assert ("if (t === 'mirror_subtitle_end') { if (mirrorLiveEndHeld(said)) return; "
            "if (said.cut && !said.id) mirrorRevealStop(); mirrorSubEnd(said); return; }") in sock
    assert "if (t === 'mirror_end' || (t === 'mirror_hello' && !said.in_call)) mirrorRevealStop();" in sock
    # (the end marker is the function's own last line: "    }\n" alone also matches the "      }\n" of the face-film
    # postMessage block added inside it on 24 Nov, and cut the function short)
    assert "else { mirrorRevealStop(); mirrorSubDown(); }" in _between(page, "    function mirrorShowMode(on) {",
                                                                      "fitMirrorStage();\n    }\n")
    # not: an interrupted, a new call's start, this screen's socket closing
    assert sock.count("mirrorRevealStop()") == 3
    assert "mirrorRevealStop" not in _between(page, "    function mirrorSubInterrupted() {", "    }\n")
    assert "mirrorRevealStop" not in _between(page, "      sock.onclose = function () {", "      };")
    # the page says what it is: sound aloud, its size (app/ceremony_reveal.ready counts the hall's screens)
    opening = _between(page, "    function startMirror() {", "      sock.binaryType")
    assert "'?sound=' + (mirrorSound ? '1' : '0') + '&w=' + Math.round(window.innerWidth * dpr)" in opening
    assert "'&coarse=' + (coarse ? '1' : '0')" in opening and "matchMedia('(pointer: coarse)')" in opening
    assert "(window.innerWidth > window.innerHeight) !== mirrorLandscape) ws.close();" in opening   # (says it again)
    # ... and the functions in the subtitle's block (the one place the page names the engine)
    block = _between(page, "    var MIRROR_SUB_CHARS = ", "    // ---- a film over the face")
    assert "function mirrorReveal(m) {" in block and "function mirrorRevealStop() {" in block
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed -- NOT checked here: that leaving show mode takes the debut down, that it "
                    "plays aloud only where her voice does and that the screen gets every time of the message")
    # (the block goes in on stdin, not argv: 24 Nov the live-subtitle code grew it past Windows' 32k command line --
    # "WinError 206: The filename or extension is too long")
    script = r"""
const block = require('fs').readFileSync(0, 'utf8');
function el() { const c = new Set(); return { className: '', children: [], textContent: '', appendChild: function (x) { this.children.push(x); },
  classList: { add: x => c.add(x), remove: x => c.delete(x), contains: x => c.has(x), toggle: (x, on) => on ? c.add(x) : c.delete(x) } }; }
function run(sound, steps, walking) {
  const calls = [];
  const document = { documentElement: el(), body: el(), createElement: el };
  const window = { CeremonyShow: { reveal: m => { calls.push(['reveal', m.sound, m.impact_s, m.numbers.length, m.zero_s, m.settle_s, m.video.at_s, m.dark]); return true; },
    revealStop: o => calls.push(['stop', o === undefined]), clear: () => calls.push(['clear']), show: () => true, progress: () => {}, settle: () => {} } };
  if (walking) window.CatwalkMusic = { pictures_down: n => calls.push(['pictures_down', n]), stop: s => calls.push(['music_stop', s]) };
  const fn = new Function('document', 'window', 'mirrorSound', 'audioCtx', 'Date', 'setInterval', 'clearInterval',
    'mirrorFrame', 'fitMirrorStage', 'playHead', 'steps', block + `
    steps.forEach(function (s) {
      if (s.mode !== undefined) mirrorShowMode(s.mode);
      if (s.reveal) mirrorReveal({ numbers: [[2, 1], [1, 2]], zero_s: 3, impact_s: 4.5, clip_at_s: 1.9, settle_s: 9, length_s: 11,
                                   seq: 1, src: '/x', video: { src: '/v', at_s: 4.5, length_s: 4.5 }, dark: s.dark });
      if (s.stop) mirrorRevealStop();
      if (s.interrupted) mirrorSubInterrupted();
    });`);
  fn(document, window, sound, { currentTime: 0, state: 'running' }, { now: () => 0 }, () => 1, () => {}, null, () => {}, 0, steps);
  return calls;
}
process.stdout.write(JSON.stringify({
  outside: run(true, [{ reveal: 1 }]),
  aloud: run(true, [{ mode: true }, { reveal: 1 }, { interrupted: 1 }, { mode: false }]),
  muted: run(false, [{ mode: true }, { reveal: 1 }, { stop: 1 }]),
  dark: run(true, [{ mode: true }, { reveal: 1, dark: true }]),
  walking: run(true, [{ mode: true }, { reveal: 1 }], true),
  walking_dark: run(true, [{ mode: true }, { reveal: 1, dark: 'build' }], true),
}));
"""
    out = subprocess.run([node, "-e", script], input=block, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["outside"] == []                                                 # (not the ceremony's screen: nothing)
    # (an interrupted: nothing; show mode over: down)
    assert got["aloud"] == [["reveal", True, 4.5, 2, 3, 9, 4.5, False], ["stop", True]]
    assert got["muted"] == [["reveal", False, 4.5, 2, 3, 9, 4.5, False], ["stop", True]]
    # the dark opening reaches the engine (25 Nov: it was dropped here, and the press lit the screen)
    assert got["dark"] == [["reveal", True, 4.5, 2, 3, 9, 4.5, True]]
    # the debut takes the stage from a walk's lingering pictures and its music (review 25 Nov: they stayed over the press)
    assert got["walking"] == [["pictures_down", False], ["music_stop", 0.4], ["reveal", True, 4.5, 2, 3, 9, 4.5, False]]
    # (a black debut: the pictures go in this frame, no fade over the dark hall)
    assert got["walking_dark"] == [["pictures_down", True], ["music_stop", 0.4], ["reveal", True, 4.5, 2, 3, 9, 4.5, "build"]]


# ---- the engine, in Chromium, with the real sound, the real clip and the real wave -----------------------

SHELL = """<!doctype html><html class="mirror mirror-show"><head><meta charset="utf-8">
<style>html, body { margin: 0; height: 100%; background: #000; }</style>
<link rel="stylesheet" href="/assets/ceremony-show.css"></head><body><script src="/assets/ceremony-show.js"></script></body></html>"""

#: Every media element the page plays, kept where the test can look at it (the engine's sound is not
#: in the document).
WATCH = """(() => { window.__media = []; const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { if (window.__media.indexOf(this) < 0) window.__media.push(this); return play.apply(this, arguments); }; })()"""


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: that the debut plays its countdown, its sound, "
                    "its black and its wave and stops at once")
    pw = sync_playwright().start()
    try:
        # (the event screen's Chrome is started so by app/room_agent.py: a sound plays without a click)
        b = pw.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required", "--disable-webgl",
                                                    "--disable-3d-apis"])
    except Exception as exc:
        pw.stop()
        pytest.skip("headless Chromium is not installed (%s) -- NOT checked here: the debut on the screen" % str(exc).splitlines()[0])
    yield b
    b.close()
    pw.stop()


def _short(spec: dict, digits: bool = False) -> dict:
    """The real files on a short timeline (the real one is ~22 s): the build with its beats (no numbers --
    the owner's debut; `digits`: three numbers, the code a counting mix would use), the black, the hit,
    the wave for 2.5 s. These are times on the sound's clock."""
    return {"numbers": [[3, 0.6], [2, 1.2], [1, 1.8]] if digits else [], "beats": [] if digits else [0.0, 0.6, 1.2, 1.8],
            "zero_s": 2.4, "impact_s": 3.2, "clip_at_s": 0.6,
            "settle_s": 5.7, "length_s": 7.5, "src": spec["src"], "video": {**spec["video"], "at_s": 3.2}}


def _page(browser, spec: dict, opened: str = "!!document.querySelector('.cs-dbt-wave video')"):
    clip = cs.asset_index()[cs.IDLE_CLIP]
    sound = cs.asset_index()[cs.reveal_sound_id()]
    wave = cs.asset_index()[cs.reveal_video_id()]
    manifest = {"acts": {}, "idle": {"src": "/ceremony-asset/%s" % clip["sha256"], "w": 1920, "h": 1080, "type": "video/mp4"},
                "reveal": spec}
    files = {clip["sha256"]: (clip["file"], "video/mp4"), sound["sha256"]: (sound["file"], "audio/mpeg"),
             wave["sha256"]: (wave["file"], "video/mp4")}
    page = browser.new_page(viewport={"width": 1280, "height": 720})
    said: list[str] = []
    page.on("pageerror", lambda e: said.append(str(e)))
    page.asked = []                                         # (every path the page asked for, in order)

    def answer(route):
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        page.asked.append(path)
        if path == "":
            return route.fulfill(body=SHELL, content_type="text/html")
        if path in ("assets/ceremony-show.js", "assets/ceremony-show.css"):
            return route.fulfill(body=(CLIENT / path).read_bytes(),
                                 content_type="text/css" if path.endswith(".css") else "text/javascript")
        if path == "ceremony-acts.json":
            return route.fulfill(body=json.dumps(manifest), content_type="application/json")
        m = re.fullmatch(r"ceremony-asset/([0-9a-f]{64})", path)
        if m and m.group(1) in files:
            file, kind = files[m.group(1)]
            body = Path(file).read_bytes()
            # (byte ranges, as the screen's listener answers them: a clip can only be sought in a file
            # served so -- the held last frame is a seek)
            asked = re.fullmatch(r"bytes=(\d+)-(\d*)", route.request.headers.get("range", ""))
            if asked:
                a, b = int(asked.group(1)), int(asked.group(2) or len(body) - 1)
                return route.fulfill(status=206, body=body[a:b + 1], content_type=kind, headers={
                    "Content-Range": "bytes %d-%d/%d" % (a, b, len(body)), "Accept-Ranges": "bytes"})
            return route.fulfill(body=body, content_type=kind, headers={"Accept-Ranges": "bytes"})
        return route.fulfill(status=404, body="")

    page.add_init_script(WATCH)
    page.route("**/*", answer)
    page.goto("http://ceremony.test/")
    page.wait_for_function("() => !!window.CeremonyShow && !!document.querySelector('.cs-idle video') && " + opened)
    return page, said


LOOK = """() => {
  const st = document.querySelector('.cs-stage'), v = document.querySelector('.cs-idle video'), w = document.querySelector('.cs-dbt-wave video');
  const sound = (window.__media || []).filter(m => m.tagName === 'AUDIO');
  const vis = s => { const e = document.querySelector(s); if (!e) return null; const c = getComputedStyle(e);
    return c.visibility === 'hidden' ? 0 : +c.opacity; };
  const digit = document.querySelector('.cs-dbt-num'), beams = document.querySelector('.cs-dbt-beams');
  return { cls: Array.from(st.classList).filter(c => /^cs-(dbt|idle|up)/.test(c)), state: window.CeremonyShow.state(),
           clip: v ? { t: +v.currentTime.toFixed(2), d: +(v.duration || 0).toFixed(2), paused: v.paused, opacity: +getComputedStyle(v).opacity } : null,
           wave: w ? { t: +w.currentTime.toFixed(2), paused: w.paused, muted: w.muted, shown: vis('.cs-dbt-wave') } : null,
           sound: sound.map(a => ({ paused: a.paused, muted: a.muted, volume: a.volume, t: +a.currentTime.toFixed(2) })),
           digit: digit ? { text: digit.textContent, h: digit.getBoundingClientRect().height, vis: getComputedStyle(document.querySelector('.cs-dbt-count')).visibility } : null,
           light: vis('.cs-dbt'), build: vis('.cs-dbt-build'), flash: vis('.cs-dbt-flash'),
           pulse: beams ? (/cs-dbt-pulse-(a|b)/.exec(beams.className) || [null, null])[1] : null };
}"""


def _dark(page) -> float:
    """The screen's mean brightness (0..255), from a screenshot."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(page.screenshot())).convert("L").resize((64, 36))
    return sum(im.getdata()) / (64 * 36)


def test_the_debut_builds_goes_black_hits_and_ends_on_the_held_logo(real, browser):
    """The owner's debut since 23 Nov ("เอาเวลานับออก"): the music's build -- no digit anywhere, the light
    pulsing on its beats and rising -- black at 0, the hit, the wave, the held logo."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    seen, black = [], []
    try:
        page.wait_for_timeout(600)
        assert page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 7, sound: true})", spec) is True
        for _ in range(int((spec["length_s"] + 1.0) / 0.15)):
            page.wait_for_timeout(150)
            look = page.evaluate(LOOK)
            seen.append(look)
            if look["state"].get("reveal") and look["state"].get("reveal")["phase"] == "dark" and not black:
                black.append(_dark(page))
    finally:
        page.close()
    assert said == []
    during = [s for s in seen if s["state"].get("reveal")]
    # the sound plays aloud, and is the clock
    assert during and all(s["sound"] and not s["sound"][0]["muted"] and s["sound"][0]["volume"] == 1 for s in during)
    assert any(not s["sound"][0]["paused"] for s in during)
    phase = lambda s: s["state"].get("reveal")["phase"]                                            # noqa: E731
    building = [s for s in during if phase(s) == "build"]
    # no countdown: no digit, ever; the beams pulse on the beats (each beat restarts it: a, b, a, ...)
    assert building and all(s["digit"] is None and s["state"]["reveal"]["number"] is None for s in seen if s["state"].get("reveal"))
    assert {"a", "b"} <= {s["pulse"] for s in building}
    assert building[-1]["build"] > building[0]["build"]                          # (the light rising to 0)
    assert all(s["clip"]["opacity"] == 0 for s in building)                     # (the logo held back)
    # 0: everything dark -- the light and the number hidden, the screen black
    dark = [s for s in during if phase(s) == "dark"]
    assert dark and all(s["build"] == 0 and s["digit"] is None and s["pulse"] is None for s in dark)
    assert all(s["sound"][0]["t"] >= spec["zero_s"] - 0.05 for s in dark)
    assert black and black[0] < 3, black
    # the hit on the sound's time: the logo bursts in, the owner's wave plays (muted) behind it
    hits = [s for s in during if phase(s) in ("hit", "settle")]
    assert hits and "cs-dbt-hit" in hits[0]["cls"] and abs(hits[0]["sound"][0]["t"] - spec["impact_s"]) < 0.3, hits[0]
    assert max(s["clip"]["opacity"] for s in hits) == 1
    assert any(s["wave"]["shown"] > 0.5 and not s["wave"]["paused"] and s["wave"]["t"] > 0.3 for s in hits)
    assert all(s["wave"]["muted"] for s in hits)
    # over: no light, the sound and the wave stopped, the clip on its held last frame -- the idle stage
    end = seen[-1]
    assert end["state"].get("reveal") is None and "cs-dbt-on" not in end["cls"] and end["light"] == 0
    assert "cs-idle-on" in end["cls"] and "cs-dbt-done" in end["cls"]
    assert end["clip"]["paused"] and abs(end["clip"]["t"] - (end["clip"]["d"] - 0.05)) < 0.06 and end["clip"]["opacity"] == 1
    assert all(a["paused"] for a in end["sound"]) and end["wave"]["paused"]


def _sound_t(page) -> float:
    return page.evaluate("() => window.__media.filter(m => m.tagName === 'AUDIO')[0].currentTime")


def test_a_small_drift_is_the_sounds_and_a_big_one_is_pulled_back_to_the_pages_clock(real, browser):
    """While the sound plays and moves, the light follows it (it is what the room hears) within 0.5 s of
    the page's clock; a sound more than that off is sent back to where the page is -- the page's clock is
    what the server counted the hit from (review 23 Nov: the light ran 0.82 s ahead of a late sound)."""
    spec = _short(ceremony_reveal.info(), digits=True)
    first = spec["numbers"][0][1]
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(500)
        page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 3, sound: true})", spec)
        page.wait_for_function("() => (window.__media || []).some(m => m.tagName === 'AUDIO' && !m.paused && m.currentTime > 0.05)")
        # a small step ahead, to just before "three": the light takes it -- "three" comes before the page's
        # own clock gets there (where the page was when it came up: number_page)
        page.evaluate("(t) => window.__media.filter(m => m.tagName === 'AUDIO')[0].currentTime = t", first - 0.05)
        page.wait_for_function("() => { const r = window.CeremonyShow.state().reveal; return !!r && r.number === 3; }",
                               timeout=2000)
        small = page.evaluate(LOOK)["state"]["reveal"]
        # a big one (to "one"): pulled back -- the sound is sent to the page's clock, the light stays on it
        page.evaluate("(t) => window.__media.filter(m => m.tagName === 'AUDIO')[0].currentTime = t", spec["numbers"][-1][1] + 0.15)
        page.wait_for_timeout(300)
        big = page.evaluate(LOOK)["state"]["reveal"]
        back = _sound_t(page)
    finally:
        page.close()
    assert said == []
    assert small["number"] == 3 and small["number_page"] < first - 0.05, small
    assert big["number"] != 1 and abs(big["t"] - big["page"]) < 0.3, big
    assert abs(back - big["page"]) < 0.35, (back, big)


def test_the_screen_asks_for_the_sound_and_the_wave_when_it_opens(real, browser):
    """(the reviewer's harness_rv3.py) The press must not wait for a file: both are asked for when the screen
    opens, the wave preloaded whole."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(1500)
        asked = list(page.asked)
        wave = page.evaluate("() => { const w = document.querySelector('.cs-dbt-wave video'); return { preload: w.preload, ready: w.readyState, muted: w.muted }; }")
    finally:
        page.close()
    assert said == []
    for src in (spec["src"], spec["video"]["src"]):
        assert src.lstrip("/") in asked, (src, asked)
    assert wave["preload"] == "auto" and wave["ready"] >= 1 and wave["muted"] is True


# ---- a late and a stalled sound (the reviewer's harness_rv2.py, async: the file's answer is held back) ----

async def _held_page(pw_browser, spec, *, sound_delay_s=0.0, sound_cut_at=None, prepare=True):
    clip = cs.asset_index()[cs.IDLE_CLIP]
    sound = cs.asset_index()[cs.reveal_sound_id()]
    wave = cs.asset_index()[cs.reveal_video_id()]
    manifest = {"acts": {}, "idle": {"src": "/ceremony-asset/%s" % clip["sha256"], "w": 1920, "h": 1080, "type": "video/mp4"}}
    if prepare:
        manifest["reveal"] = spec
    files = {clip["sha256"]: (clip["file"], "video/mp4"), sound["sha256"]: (sound["file"], "audio/mpeg"),
             wave["sha256"]: (wave["file"], "video/mp4")}
    page = await pw_browser.new_page(viewport={"width": 1280, "height": 720})
    errs: list[str] = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    async def answer(route):
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "":
            return await route.fulfill(body=SHELL, content_type="text/html")
        if path in ("assets/ceremony-show.js", "assets/ceremony-show.css"):
            return await route.fulfill(body=(CLIENT / path).read_bytes(),
                                       content_type="text/css" if path.endswith(".css") else "text/javascript")
        if path == "ceremony-acts.json":
            return await route.fulfill(body=json.dumps(manifest), content_type="application/json")
        m = re.fullmatch(r"ceremony-asset/([0-9a-f]{64})", path)
        if m and m.group(1) in files:
            file, kind = files[m.group(1)]
            body = Path(file).read_bytes()
            is_sound = m.group(1) == sound["sha256"]
            if is_sound and sound_delay_s:
                await asyncio.sleep(sound_delay_s)
            asked = re.fullmatch(r"bytes=(\d+)-(\d*)", route.request.headers.get("range", ""))
            total = len(body)
            a = int(asked.group(1)) if asked else 0
            b = int(asked.group(2) or total - 1) if asked else total - 1
            if is_sound and sound_cut_at is not None:
                if a >= sound_cut_at:
                    await asyncio.sleep(3600)          # never answered: the link is gone
                    return
                b = min(b, sound_cut_at - 1)
            return await route.fulfill(status=206, body=body[a:b + 1], content_type=kind, headers={
                "Content-Range": "bytes %d-%d/%d" % (a, b, total), "Accept-Ranges": "bytes"})
        return await route.fulfill(status=404, body="")

    await page.add_init_script(WATCH)
    await page.route("**/*", answer)
    await page.goto("http://ceremony.test/")
    await page.wait_for_function("() => !!window.CeremonyShow && !!document.querySelector('.cs-idle video')")
    return page, errs


def _held(run):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: a late or a stalled sound")

    async def main():
        async with async_playwright() as pw:
            try:
                b = await pw.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required",
                                                                    "--disable-webgl", "--disable-3d-apis"])
            except Exception as exc:
                pytest.skip("headless Chromium is not installed (%s) -- NOT checked here: a late or a stalled sound"
                            % str(exc).splitlines()[0])
            try:
                return await run(b)
            finally:
                await b.close()
    # (on a thread of its own: the module's sync Playwright keeps an event loop running on this one)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, main()).result(timeout=120)


SOUNDS = "() => (window.__media || []).filter(m => m.tagName === 'AUDIO').map(a => ({ paused: a.paused, t: a.currentTime }))"
STATE = "() => { const s = window.CeremonyShow.state(); return s.reveal || null; }"


def test_a_sound_that_comes_late_is_put_in_place_and_a_stop_stops_it_even_before_it_starts(real):
    """Review 23 Nov (harness A): the sound not preloaded and 1.5 s slow. The countdown does not wait for it,
    and when it comes it is sent on to where the page is (the hit where the server counted it); and a stop
    while its play() is still pending must leave it silent -- the element was dropped, so it played on
    into the dark hall after a red stop."""
    spec = _short(ceremony_reveal.info(), digits=True)

    async def run(b):
        page, errs = await _held_page(b, spec, sound_delay_s=1.5, prepare=False)
        await page.wait_for_timeout(300)
        await page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 1, sound: true})", spec)
        seen = []
        for _ in range(14):
            await page.wait_for_timeout(200)
            seen.append((await page.evaluate(STATE), await page.evaluate(SOUNDS)))
        await page.close()
        page, errs2 = await _held_page(b, spec, sound_delay_s=1.5, prepare=False)
        await page.wait_for_timeout(300)
        await page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 2, sound: true})", spec)
        await page.wait_for_timeout(400)
        await page.evaluate("() => window.CeremonyShow.revealStop()")
        await page.wait_for_timeout(2200)                              # (its file comes, its play() resolves)
        after = await page.evaluate(SOUNDS)
        await page.close()
        return seen, after, errs + errs2

    seen, after, errs = _held(run)
    assert errs == []
    states = [s for s, _ in seen if s]
    assert states and (states[0]["phase"] == "build" or states[0]["t"] > 2.3), states[0]
    # the numbers came on time though no sound had (the page's clock), and never went back when it came
    # (a PC so loaded that its first sample lands after the 3 has gone by -- three parallel runs did it,
    # 24 Nov -- has no 3 to see: that is the machine's clock, not the page's. Judge the samples that came
    # in time; the order check below still covers all of them)
    early = [s for s, _ in seen if s and s["t"] < 1.1 and s["number"] is not None]
    if early:
        assert early[0]["number"] == 3, early[0]
    shown = [s["number"] for s, _ in seen if s and s["number"] is not None]
    assert shown == sorted(shown, reverse=True), shown
    assert states[-1]["t"] >= 14 * 0.2 - 0.3, states[-1]              # (not held back by the late sound)
    # once it plays it is where the page is -- not 1.5 s behind it
    playing = [(s, a[0]) for s, a in seen if s and a and not a[0]["paused"] and a[0]["t"] > 0]
    assert playing, seen
    s, a = playing[-1]
    assert abs(a["t"] - s["t"]) < 0.6, (s, a)
    # stopped before it ever played: it never does
    assert after and all(x["paused"] for x in after), after


def test_a_sound_that_comes_after_0_but_before_the_hit_is_heard_on_the_hit(real):
    """Re-review 23 Nov: a sound that came after 0 was given up, and the hit was silent. Before the hit it
    is let in where the page is."""
    spec = _short(ceremony_reveal.info())

    async def run(b):
        page, errs = await _held_page(b, spec, sound_delay_s=spec["zero_s"] + 0.2, prepare=False)
        await page.wait_for_timeout(300)
        await page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 6, sound: true})", spec)
        seen = []
        for _ in range(int((spec["impact_s"] + 1.5) / 0.15)):
            await page.wait_for_timeout(150)
            seen.append((await page.evaluate(STATE), await page.evaluate(SOUNDS)))
        await page.close()
        return seen, errs

    seen, errs = _held(run)
    assert errs == []
    hits = [(s, a[0]) for s, a in seen if s and s["phase"] == "hit" and a]
    assert hits and any(not a["paused"] and abs(a["t"] - s["page"]) < 0.5 for s, a in hits), hits


@pytest.mark.parametrize("cut_s", [1.4, 3.0])
def test_a_sound_that_stalls_does_not_freeze_the_countdown_and_the_debut_ends_by_itself(cut_s, real):
    """Review 23 Nov (harness B): the sound's file stops arriving -- here after 1.4 s of it (too little to
    start: it never does) or 3.0 s (it plays, then stalls). The countdown sat on one number for 16 s while
    the server's hit passed. Now the light goes on by the page's clock, the element is given up and
    paused (never to start into the dark hall), and it all ends by itself on time."""
    spec = _short(ceremony_reveal.info(), digits=True)
    sound = cs.asset_index()[cs.reveal_sound_id()]
    size = sound["file"].stat().st_size
    duration = _row(cs.reveal_sound_id())["duration"]

    async def run(b):
        page, errs = await _held_page(b, spec, sound_cut_at=int(size * cut_s / duration), prepare=True)
        await page.wait_for_timeout(1500)
        await page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 3, sound: true})", spec)
        seen = []
        for _ in range(int((spec["length_s"] + 1.5) / 0.25)):
            await page.wait_for_timeout(250)
            seen.append((await page.evaluate(STATE), await page.evaluate(SOUNDS)))
        await page.close()
        return seen, errs

    seen, errs = _held(run)
    assert errs == []
    played = max((x["t"] for _, a in seen for x in a), default=0)
    assert (played > 1.5) if cut_s > 2 else (played == 0), played      # (3.0: it played, then stalled)
    # given up while the debut goes on: the element paused, not left "playing" a sound that is gone
    later = [a for s, a in seen if s and s["t"] >= max(cut_s, spec["impact_s"]) + 0.5]
    assert later and all(x["paused"] for a in later for x in a), later[:3]
    numbers = [s["number"] for s, _ in seen if s and s["number"] is not None]
    assert numbers and numbers[-1] == 1, numbers                  # (past the stall: to "one")
    phases = [s["phase"] for s, _ in seen if s]
    assert "dark" in phases and "hit" in phases
    assert seen[-1][0] is None                                     # (over, by itself)
    assert all(x["paused"] for x in seen[-1][1])


def test_a_slide_after_the_hit_dissolves_the_debut_under_it(real, browser):
    """Re-review 23 Nov: a slide coming up during the wave cut the debut in one frame. After the hit it
    dissolves (~0.65 s: the light, the wave, the ringing hit going down), then is over like any end:
    the sound paused and rewound, the wave paused."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(500)
        page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 5, sound: true})", spec)
        page.wait_for_function("() => { const r = window.CeremonyShow.state().reveal; return !!r && r.phase === 'hit'; }",
                               timeout=8000)
        page.wait_for_timeout(500)
        page.evaluate("() => window.CeremonyShow.show({ template: 'hero', title: 'Hello', beats: [] })")
        # (sampled through the dissolve, not one look at 200 ms: on a loaded PC -- 25 Nov, the walk simulator running beside
        # the suite -- the fade's first frame came later and the single look still read 1)
        samples = []
        for _ in range(12):
            page.wait_for_timeout(50)
            samples.append((page.evaluate(LOOK), page.evaluate("() => window.__media.filter(m => m.tagName === 'AUDIO')[0].volume")))
        going = [(s, v) for s, v in samples if 0 < s["light"] < 1]
        mid, mid_vol = going[0] if going else samples[3]
        page.wait_for_timeout(800)
        end = page.evaluate(LOOK)
    finally:
        page.close()
    assert said == []
    assert mid["state"].get("reveal") is None and "cs-dbt-fade" in mid["cls"]
    assert 0 < mid["light"] < 1, mid["light"]                                  # (going, not gone)
    assert not mid["wave"]["paused"] and 0 < mid_vol < 1
    assert "cs-dbt-fade" not in end["cls"] and "cs-dbt-hit" not in end["cls"] and end["light"] == 0
    assert all(a["paused"] and a["t"] == 0 for a in end["sound"]) and end["wave"]["paused"]


@pytest.mark.parametrize("how", ["stop", "slide"])
def test_the_debut_stops_at_once(how, real, browser):
    """Its own stop (the red stop, the call over, show mode over all end in revealStop on the page) and a
    line's slide coming up. At once: the sound and the wave paused, the light gone, the logo on its held
    frame."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(500)
        page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 1, sound: true})", spec)
        page.wait_for_timeout(1000)
        before = page.evaluate(LOOK)
        if how == "stop":
            page.evaluate("() => window.CeremonyShow.revealStop()")
        else:
            page.evaluate("() => window.CeremonyShow.show({ template: 'hero', title: 'Hello', beats: [] })")
        now = page.evaluate(LOOK)
    finally:
        page.close()
    assert said == []
    assert before["state"].get("reveal") and before["sound"] and not before["sound"][0]["paused"]
    assert before["state"].get("reveal")["phase"] == "build" and before["pulse"] in ("a", "b")
    assert now["state"].get("reveal") is None and all(a["paused"] for a in now["sound"]) and now["wave"]["paused"]
    assert "cs-dbt-on" not in now["cls"] and now["light"] == 0 and now["flash"] == 0
    assert now["clip"]["paused"] and abs(now["clip"]["t"] - (now["clip"]["d"] - 0.05)) < 0.06


CLIP_LOOK = """() => {
  const w = document.querySelector('.cs-idle-m'), v = document.querySelector('.cs-idle video');
  const c = getComputedStyle(w), cv = getComputedStyle(v), b = getComputedStyle(document.body, '::before');
  const mask = s => s.maskImage || s.webkitMaskImage || 'none';
  return { wrap: c.visibility, blend: c.mixBlendMode, mask: mask(c) !== 'none', vblend: cv.mixBlendMode, vmask: mask(cv),
           op: +cv.opacity, cls: Array.from(document.querySelector('.cs-stage').classList), backdrop: b.visibility };
}"""


def test_the_clips_blend_is_out_of_the_drawing_while_a_line_is_up_and_back_for_the_debut(real, browser):
    """23 Nov, the event notebook (integrated GPU): the clip's `screen` blend made the whole stage an
    offscreen pass every frame, under every line, though the clip was clear; and its mask, on the video
    that breathes and bursts, was drawn again at every scale (~470 tiles a second on the held logo). The
    blend and the mask are on a still wrapper now, out of the drawing once the clip's own fade-out is
    over, and back the moment the clip shows -- the held logo, the debut."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(700)
        idle = page.evaluate(CLIP_LOOK)
        page.evaluate("() => window.CeremonyShow.show({ template: 'hero', title: 'Hello', beats: [] })")
        page.wait_for_function("() => document.querySelector('.cs-stage').classList.contains('cs-up')", polling=10)
        fading = page.evaluate(CLIP_LOOK)                    # (the clip's own fade-out has just begun)
        page.wait_for_timeout(900)
        line = page.evaluate(CLIP_LOOK)
        page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 3, sound: false})", spec)
        debut = page.evaluate(CLIP_LOOK)
        page.evaluate("() => window.CeremonyShow.revealStop()")
        page.wait_for_timeout(600)
        held = page.evaluate(CLIP_LOOK)
    finally:
        page.close()
    assert said == []
    assert "cs-idle-on" in idle["cls"] and idle["wrap"] == "visible" and idle["blend"] == "screen" and idle["mask"], idle
    assert idle["vblend"] == "normal" and idle["vmask"] == "none", idle          # (the video moves: no look of its own)
    assert idle["backdrop"] == "hidden", idle                 # (the page's grain under the covering stage: not drawn)
    assert "cs-idle-on" not in fading["cls"] and fading["wrap"] == "visible", fading
    assert line["wrap"] == "hidden" and line["op"] == 0 and line["backdrop"] == "hidden", line
    assert "cs-dbt-on" in debut["cls"] and debut["wrap"] == "visible", debut
    assert "cs-idle-on" in held["cls"] and held["wrap"] == "visible" and held["op"] == 1, held


def test_after_the_debut_the_next_slide_takes_its_held_logo_look_off(real, browser):
    """Review 23 Nov: show() took off `cs-rv-done` (a name that never was) -- `cs-dbt-done` stayed for the
    evening, and its held-logo rule outranked the idle stage's own."""
    spec = _short(ceremony_reveal.info())
    page, said = _page(browser, spec)
    try:
        page.wait_for_timeout(500)
        page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 4, sound: false})", spec)
        page.wait_for_timeout(300)
        page.evaluate("() => window.CeremonyShow.revealStop()")
        held = page.evaluate(LOOK)["cls"]
        page.evaluate("() => window.CeremonyShow.show({ template: 'hero', title: 'Hello', beats: [] })")
        after = page.evaluate(LOOK)["cls"]
    finally:
        page.close()
    assert said == []
    assert "cs-dbt-done" in held and "cs-dbt-done" not in after


def test_the_debut_moves_by_transform_and_opacity_only():
    """The film's motion rules for the core's own stylesheet (tests/test_ceremony_acts.py css_problems,
    unprefixed) hold for the debut's rules too, and the debut has no blend or mask of its own."""
    from tests.test_ceremony_acts import css_problems
    css = STYLE.read_text(encoding="utf-8")
    look = css[css.index("/* ==== the debut"):css.index("/* ==== the living stage")]
    assert ".cs-dbt-num" in look and ".cs-dbt-wave" in look and css_problems("core", look, prefixed=False) == []
    code = re.sub(r"/\*.*?\*/", "", look, flags=re.S)
    assert "mix-blend-mode" not in code and "mask" not in code and "blur(" not in code
    assert "cs-dbt" in ENGINE.read_text(encoding="utf-8")


# ---- the real /mirror page on the real listener: what it says it is (the reviewer's page_r2.py size cases) ----

@pytest.fixture
def listener(monkeypatch):
    """app/mirror_server on a port of its own (a thread), the room screen's token."""
    import secrets
    import socket
    import threading

    import uvicorn

    from app import mirror_server
    from app.config import settings

    face_mirror.reset()
    monkeypatch.setattr(settings, "mirror_token", secrets.token_urlsafe(12))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(mirror_server.app, host="127.0.0.1", port=port, lifespan="off",
                                           log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        __import__("time").sleep(0.05)
    yield "http://127.0.0.1:%d/mirror?token=%s" % (port, settings.mirror_token)
    server.should_exit = True
    thread.join(10)
    face_mirror.reset()


def _kinds_soon(n: int, timeout: float = 15.0) -> list:
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        kinds = [k for k in face_mirror.screen_kinds() if k is not None]
        if len(kinds) >= n:
            return kinds
        time.sleep(0.1)
    return [k for k in face_mirror.screen_kinds() if k is not None]


@pytest.mark.parametrize("view", ["1920x1080@1", "960x540@2", "640x360@3", "390x844@3,touch"])
def test_the_real_page_says_what_it_is_in_device_pixels_and_is_never_refused(view, real, browser, listener):
    """Re-review 23 Nov: the hall's TV at 200% scaling (960x540 CSS px) said 960 and was refused. The page
    now says its size in device pixels and whether it is touch-first; a hall screen is one that plays the
    sound or is landscape and not touch-first -- and nothing is ever refused for it, only warned."""
    size, _, rest = view.partition("@")
    dpr, _, touch = rest.partition(",")
    w, h = (int(v) for v in size.split("x"))
    context = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=float(dpr),
                                  has_touch=bool(touch), is_mobile=bool(touch))
    page = context.new_page()
    try:
        page.goto(listener + ("" if touch else "&sound=1"))
        kinds = _kinds_soon(1)
    finally:
        context.close()
    assert kinds, "the page never connected"
    kind = kinds[0]
    assert (kind["w"], kind["h"]) == (round(w * float(dpr)), round(h * float(dpr)))
    assert kind["coarse"] is bool(touch) and kind["sound"] is not bool(touch)
    assert ceremony_reveal._hall(kind) is not bool(touch)


def test_the_real_page_says_it_again_once_it_is_turned_landscape(real, browser, listener):
    """A window that connected portrait (a narrow window, a side-docked one) and is then maximised says
    what it is again -- a new socket; the debut would play on through it."""
    context = browser.new_context(viewport={"width": 700, "height": 900})
    page = context.new_page()
    try:
        page.goto(listener)
        first = _kinds_soon(1)
        page.set_viewport_size({"width": 1400, "height": 800})
        import time
        end = time.monotonic() + 12
        later = first
        while time.monotonic() < end:
            later = [k for k in face_mirror.screen_kinds() if k is not None]
            if later and later[0]["w"] > later[0]["h"]:
                break
            time.sleep(0.2)
    finally:
        context.close()
    assert first and first[0]["w"] < first[0]["h"] and not ceremony_reveal._hall(first[0])
    assert later and later[0]["w"] > later[0]["h"] and ceremony_reveal._hall(later[0]), later


# ---- DARK: the opening heard on a black screen (owner 25 Nov ~02:15, relayed by the tour's session:
# "ตอนเปิดทุกอย่างจะดับ โลโก้ไม่ต้องขึ้นก็ได้ ถ้าจะขึ้นก็คือสปอร์ตไลท์หลังไฟดับ ซึ่งยังไม่ได้ต่อสมาทโฮม") ------------

def test_the_dark_debut_keeps_its_hit_where_the_walk_goes_out(real, black, screens, clock):
    """reveal.json saying `dark`: the screen is told so, there is no wave (none to be missing either: ready
    says nothing of it), and the times the robot's walk goes by are the lit debut's own."""
    assert ceremony_reveal.dark() is True
    spec = ceremony_reveal.timeline()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ceremony_reveal, "dark", lambda: False)
        lit_spec = ceremony_reveal.timeline()
    assert spec["dark"] is True and "dark" not in lit_spec
    assert "video" not in spec and spec["video_problem"] is None and spec["sound_problem"] is None
    for key in ("numbers", "beats", "zero_s", "impact_s", "src"):             # (the music and its hit: the same)
        assert spec[key] == lit_spec[key], key
    # (over when the music is -- no logo clip to wait for, no lift)
    assert spec["length_s"] == _row(cs.reveal_sound_id())["duration"] and spec["settle_s"] == spec["impact_s"]
    assert ceremony_reveal.info()["dark"] is True                              # (what the screen asks for on opening)
    _ceremony_call()
    _sent(screens)
    assert ceremony_reveal.ready() == (True, None)
    # (on a thread of its own: this test runs after the Chromium ones, and the module's sync Playwright holds this
    # thread's event loop -- asyncio.run here raises "cannot be called from a running event loop")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(1) as pool:
        got = pool.submit(_start).result()
    sent = [m for m in _sent(screens) if m["type"] == "ceremony_reveal"]
    assert len(sent) == 1 and sent[0]["dark"] is True and "video" not in sent[0]
    assert got["impact_in_s"] == round(spec["impact_s"] + ceremony_reveal.START_LAG_S, 3)
    assert ceremony_reveal.impact_at() == pytest.approx(clock[0] + got["impact_in_s"])


def test_the_dark_debut_keeps_the_screen_black_and_a_line_brings_the_stage_back(real, black, browser):
    """On the screen: black from the moment it opens (no logo opening), through the build, the black and
    the hit (no light, no logo, no wave) with the music playing aloud, and after it; the first line's slide
    brings the stage back."""
    spec = {**_short({**ceremony_reveal.info(), "video": {}}), "dark": True}
    spec.pop("video")
    page, said = _page(browser, spec, "document.querySelector('.cs-stage').classList.contains('cs-black')")
    seen, bright = [], []
    try:
        page.wait_for_timeout(900)
        opened = page.evaluate(LOOK)
        bright.append(("opened", _dark(page)))
        assert page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 9, sound: true})", spec) is True
        for _ in range(int((spec["length_s"] + 1.0) / 0.3)):
            page.wait_for_timeout(300)
            look = page.evaluate(LOOK)
            seen.append(look)
            r = look["state"].get("reveal")
            bright.append((r["phase"] if r else "over", _dark(page)))
        page.evaluate("() => window.CeremonyShow.show({ template: 'hero', title: 'Hello', beats: [] })")
        page.wait_for_timeout(1500)
        slide = (_dark(page), page.evaluate("() => [getComputedStyle(document.querySelector('.cs-bg')).visibility, "
                                            "getComputedStyle(document.querySelector('.cs-stage')).backgroundImage]"))
    finally:
        page.close()
    assert said == []
    # the logo never opened, never played
    assert opened["clip"]["paused"] and opened["clip"]["t"] < 0.1
    assert all(s["clip"]["paused"] for s in seen)
    assert all(s["clip"]["t"] < 0.1 for s in seen if s["state"].get("reveal"))
    # the music played aloud, the phases ran on it (the hit, then over)
    during = [s for s in seen if s["state"].get("reveal")]
    assert during and any(s["sound"] and not s["sound"][0]["paused"] and not s["sound"][0]["muted"] for s in during)
    assert {"build", "dark", "hit"} <= {s["state"]["reveal"]["phase"] for s in during}
    assert seen[-1]["state"].get("reveal") is None
    # nothing lit at any moment: no light show, no flash, the screen black
    assert all(s["light"] == 0 and s["flash"] == 0 for s in seen)
    assert all(b < 2 for _, b in bright), [x for x in bright if x[1] >= 2]
    # a line's slide: the stage is back
    assert slide[1][0] == "visible" and "gradient" in slide[1][1] and slide[0] > 2, slide



def test_the_debut_ends_a_walks_music_and_replays_no_held_line_to_a_screen_that_joins(real, screens, clock):
    """Review 25 Nov: a walk's music left on (a press within the pictures' linger, or a walk whose hit/off never came)
    played on under the build, and a ตอบสด slide still held was replayed to a screen that blinked mid-build -- it ended
    the debut on that screen while the robot still walked out on the hit."""
    from app.config import settings

    _ceremony_call()
    _sent(screens)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "catwalk_music", True)
        assert face_mirror.catwalk("walk") is True
        face_mirror._state["motion"] = {"type": "mirror_motion", "slide": {"x": 1}}
        face_mirror._state["subtitle"] = {"type": "mirror_subtitle", "th": "x"}
        _sent(screens)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(1) as pool:                  # (after the Chromium tests: their loop holds this thread's)
            pool.submit(_start).result()
        sent = _sent(screens)
        assert {"type": "catwalk", "op": "off"} in sent
        assert sent.index({"type": "catwalk", "op": "off"}) < [m["type"] for m in sent].index("ceremony_reveal")
        assert face_mirror._state["catwalk"] is None
        assert face_mirror._state["motion"] is None and face_mirror._state["subtitle"] is None
        joined = [json.loads(x) for x in face_mirror.snapshot()]
        assert not any(m.get("type") in ("mirror_motion", "mirror_subtitle") for m in joined)
        assert not any(m.get("type") == "catwalk" and m.get("op") == "walk" for m in joined)



def test_until_the_hit_the_screen_is_black_then_the_spotlight_brings_the_lit_debut(real, browser, monkeypatch):
    """reveal.json "build" on the screen: black when it opens (no logo opening), through the build and its black with
    the music aloud; on the hit the lit debut -- the light up, the logo clip playing, the wave."""
    real_cfg = dict(cs.reveal_config())
    monkeypatch.setattr(ceremony_reveal, "dark", _REAL_DARK)
    monkeypatch.setattr(cs, "reveal_config", lambda: {**real_cfg, "dark": "build"})
    spec = _short(ceremony_reveal.info())
    spec["dark"] = "build"
    page, said = _page(browser, spec, "document.querySelector('.cs-stage').classList.contains('cs-black')")
    before, after = [], []
    try:
        page.wait_for_timeout(700)
        opened = _dark(page)
        assert page.evaluate("(m) => window.CeremonyShow.reveal({...m, seq: 11, sound: true})", spec) is True
        for _ in range(int((spec["length_s"] + 0.5) / 0.3)):
            page.wait_for_timeout(300)
            look = page.evaluate(LOOK)
            r = look["state"].get("reveal")
            phase = r["phase"] if r else "over"
            (before if phase in ("build", "dark") else after).append((phase, _dark(page), look))
    finally:
        page.close()
    assert said == []
    assert opened < 2, opened
    assert before and all(b < 2 for _, b, _l in before), [(p_, b) for p_, b, _l in before]
    hit = [x for x in after if x[0] in ("hit", "settle")]
    assert hit and max(b for _, b, _l in hit) > 20, [(p_, b) for p_, b, _l in hit]          # (the spotlight's light)
    assert any(l["clip"]["opacity"] == 1 and not l["clip"]["paused"] for _, _b, l in hit)   # (the logo plays)
    assert any(l["wave"]["shown"] > 0.5 for _, _b, l in hit)                                # (the wave)
    assert any(not s["paused"] for _, _b, l in before for s in l["sound"])                   # (the music in the dark)
