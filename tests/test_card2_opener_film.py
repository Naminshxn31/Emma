"""Card 2's opener on the hall screen (owner 25 Nov 2026: "เอาเสียงเหมือนอันเก่าเลย แล้วก็อย่าลืมโมชั่นด้วยนะ"): the
signed film of the line it stands in for (s02_l03), retitled with the opener's own first sentence
(`ceremony_slides.opener_slide`), carried by `event_cues.play_line(slide=)` to the subtitle the hall screen gets."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony_slides, event_script

ROOT = Path(__file__).resolve().parent.parent
OLD_EN = ("Hello, Khun Miki, and hello, everyone. I'm Emma. I represent Embassy World, and I'm the smart assistant "
          "for our sales team and all of our agents.")
EN = ("Allow me to introduce myself once again. I'm Emma. I represent Embassy World, and I'm the smart assistant for "
      "our sales team and all of our agents.")
TH = "ขออนุญาตแนะนำตัวอีกครั้งนะคะ เอ็มม่าเป็นตัวแทนของ Embassy World และเป็นผู้ช่วยอัจฉริยะสำหรับทีมขายและเอเจนต์ทุกท่านค่ะ"


def _base() -> dict:
    """s02_l03's slide as the file has it, its picture left out (the catalogue on a PC may be a draft: the words and
    beats are what is tested here; the picture rides along untouched)."""
    slide = json.loads((ROOT / "data" / "ceremony" / "slides" / "s02_l03.json").read_text(encoding="utf-8"))
    slide.pop("pictures", None)
    for beat in slide["beats"]:
        if isinstance(beat.get("key"), list):
            beat["key"] = [k for k in beat["key"] if k != "facade"]
    return slide


@pytest.fixture
def films(monkeypatch):
    doc = {"slides": {"s02_l03": _base()}}
    signed = {"ok": True}
    monkeypatch.setattr(ceremony_slides, "_doc", lambda: doc)
    monkeypatch.setattr(ceremony_slides, "approved", lambda d: signed["ok"])
    monkeypatch.setattr(event_script, "cue", lambda cue_id, doc=None: (
        {"id": "s02_l03", "en": OLD_EN, "th": "สวัสดีค่ะคุณมิกิ"} if cue_id == "s02_l03" else None))
    return SimpleNamespace(doc=doc, signed=signed)


def test_the_opener_film_is_the_signed_film_retitled(films):
    base = ceremony_slides.page_payload(films.doc["slides"]["s02_l03"], event_script.cue("s02_l03"))
    got = ceremony_slides.opener_slide("s02_l03", EN, TH)
    assert got is not None
    assert got["texts"]["hello"] == "Allow me to introduce myself once again"
    assert "every" not in got["texts"]                       # ("Hello, everyone" was the greeting it replaces)
    assert {k: v for k, v in got["texts"].items() if k != "hello"} == {
        k: v for k, v in base["texts"].items() if k not in ("hello", "every")}
    # the greeting goes when her name comes: the beat that showed "Hello, everyone" now shows nothing, at her name
    first, name = got["beats"][0], got["beats"][1]
    assert first == {"at": name["at"], "do": "everyone"} and name["do"] == "name" and name["key"] == "giant"
    assert [(b["do"], b.get("key")) for b in got["beats"][1:]] == [(b["do"], b.get("key")) for b in base["beats"][1:]]
    assert got["options"] == base["options"] and got["act"] == "emma_console"
    # the signed slide in the file is not touched
    assert films.doc["slides"]["s02_l03"]["texts"]["hello"] == "Hello, Khun Miki"
    assert "every" in films.doc["slides"]["s02_l03"]["texts"]


def test_a_word_on_the_screen_she_does_not_say_keeps_the_film_off(films):
    # (the cards say "Our sales team" / "All of our agents": an opener that does not say them has no film)
    assert ceremony_slides.opener_slide("s02_l03", "Allow me to introduce myself once again. I'm Emma. "
                                        "I represent Embassy World.", TH) is None
    assert ceremony_slides.opener_slide("s02_l03", "Allow me to introduce myself once again, I'm Emma, and I "
                                        "represent Embassy World, the smart assistant for our sales team and all of "
                                        "our agents.", TH) is None      # (one sentence: no greeting of its own)


def test_no_film_from_an_unsigned_file_another_film_or_a_broken_slide(films):
    films.signed["ok"] = False
    assert ceremony_slides.opener_slide("s02_l03", EN, TH) is None
    assert ceremony_slides.opener_slide("s02_l03", EN, TH, drafts=True) is not None   # (the PC preview only)
    films.signed["ok"] = True
    assert ceremony_slides.opener_slide("s02_l99", EN, TH) is None
    assert ceremony_slides.opener_slide("s02_l03", EN, None) is None                 # (no Thai line: no slide)
    other = copy.deepcopy(_base())
    other["options"]["mode"] = "caps"
    films.doc["slides"]["s02_l03"] = other
    assert ceremony_slides.opener_slide("s02_l03", EN, TH) is None                   # (only the console's intro)
    broken = _base()
    broken["texts"]["rep"] = "I represent Embassy World Pattaya"                     # (not in s02_l03's own line)
    films.doc["slides"]["s02_l03"] = broken
    assert ceremony_slides.opener_slide("s02_l03", EN, TH) is None                   # (the signed slide is not served)


def test_the_real_file_builds_the_film_when_it_is_served(monkeypatch):
    """On this machine's files: when s02_l03's own film is served, the opener's is too (and when it is not, neither)."""
    from app.config import settings

    monkeypatch.setattr(settings, "stories_enabled", True)      # (as on the robot: robot.env; conftest pins it off)
    monkeypatch.setattr(settings, "ceremony_file", "data/ceremony/floor1_ceremony.json")
    own = ceremony_slides.for_cue("s02_l03")
    got = ceremony_slides.opener_slide("s02_l03", EN, TH)
    if own is None:
        assert got is None
        pytest.skip("s02_l03's film is not served on this machine (unsigned file or draft catalogue) -- the opener's "
                    "film from the real files is NOT checked here")
    assert got is not None and got["pictures"] == own["pictures"]
    assert got["texts"]["hello"] == "Allow me to introduce myself once again"


@pytest.mark.filterwarnings("ignore:coroutine '_recorded_line' was never awaited:RuntimeWarning")
def test_the_film_rides_with_the_line_and_a_line_without_one_has_none(monkeypatch):
    from app import event_cues, face_mirror

    provider = SimpleNamespace(plays_recorded=True)
    monkeypatch.setattr(event_cues, "_live_call", lambda: SimpleNamespace(provider=provider))
    monkeypatch.setattr(event_cues, "_quiet", lambda: None)
    monkeypatch.setattr(event_cues, "_offline", lambda p: False)
    monkeypatch.setattr(event_cues, "_running", lambda cue_id: False)
    monkeypatch.setitem(event_cues.STATE, "runs", {})
    looked = []
    monkeypatch.setattr(ceremony_slides, "for_cue", lambda cue_id, **k: looked.append(cue_id) or {"template": "x"})
    runs = []

    def spawn(coro):
        coro.close()
        run = max(event_cues.STATE["runs"].values(), key=lambda r: r["seq"])
        runs.append(run)
        run["started"].set_result({"ok": True})

    monkeypatch.setattr(event_cues, "_spawn", spawn)
    sent = []
    monkeypatch.setattr(face_mirror, "subtitle", lambda line: sent.append(line))
    film = {"template": "act", "act": "emma_console"}
    asyncio.run(event_cues.play_line("card2_open", EN, b"\x00\x00" * 24000, lang="en", sub_th=TH, slide=film))
    event_cues._subtitle(runs[0], 10.0)
    assert sent[0]["slide"] == film and sent[0]["text"] == TH
    # no film given: none goes up -- never the slide some script cue would have (no lookup by its id)
    runs.clear()
    sent.clear()
    asyncio.run(event_cues.play_line("card2_open", EN, b"\x00\x00" * 24000, lang="en", sub_th=TH))
    event_cues._subtitle(runs[0], 10.0)
    assert "slide" not in sent[0] and looked == []
    # a script cue pressed by staff still gets its own slide by its id, as before
    assert event_cues._slide({"id": "s02_l05", "lang": "en"}) == {"template": "x"} and looked == ["s02_l05"]
