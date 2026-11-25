"""The whole ceremony live: the server follows the host (owner 24 Nov 2026, app/ceremony_follow.py).

"ทั้งพิธีคุยสด ไม่ใช้บทกด": card 2 of the order turns the mode on; from then on the host's scripted line plays
the next cue -- the SERVER decides, by words; anything else she answers live. What these hold down:

* off (the default) = the ceremony as it was: card 2 is not in the order, no key on the page's state, no hook;
* the matcher: words not substrings, paraphrase and transcriber habits match, off-script lines and ties do not;
* the gates: a cue behind an unticked tick / a walk / a cue with no host line cannot fire, nothing plays while
  she speaks or the robot moves, the red stop pauses the mode until staff resume it;
* a staff press moves the pointer; a chained joke still chains; a continuation goes by itself, once;
* the window is the "ตอบสด" one with no cap, and re-opens after a line without the logo;
* the staff page shows the EXPECTED line of the script, never the host's words.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, event_cues, event_host, event_order, event_script
from app.config import settings
from scripts import eval_ceremony_follow as evalrun
from tests.test_event_order import REAL_SCRIPT, _signed, call, calls, event, rows  # noqa: F401 -- fixtures
from tests.test_event_order_page import chromium, real  # noqa: F401 -- the staff server, a browser

ROOT = Path(__file__).resolve().parents[1]
HOST_FILE = ROOT / "data" / "ceremony" / "floor1_ceremony.host.json"

F1 = "ที่ตั้งของโครงการอยู่ที่ไหนครับเอ็มม่า"
F3 = "โครงการของเรามีกี่ยูนิตและมีห้องแบบไหนบ้างครับ"
F5 = "ช่วยเปิดวิดีโอเทคโนโลยีของ Embassy World ให้ทุกท่านชมหน่อยครับ"
F6 = "เรามาดูสิ่งอำนวยความสะดวกของโครงการกันครับ"

SCRIPT = {"format": event_script.FORMAT, "host_th": "คุณมิกิ", "replies": "cues", "cues": [
    _signed({"id": cid, "scene": 2, "th": "ข้อความตัวอย่างของบท%sค่ะ" % word, "numbers": []})
    for cid, word in (("f1", "อัลฟา"), ("f2", "เบตา"), ("f3", "แกมมา"), ("f4", "เดลต้า"), ("f5", "เอปไซลอน"),
                      ("f6", "ซีตา"))], "order": [
    {"n": 1, "title_th": "เปิด", "steps": [{"do": "follow"}]},
    {"n": 2, "title_th": "คุย", "steps": [
        {"do": "cue", "cue": "f1"}, {"do": "cue", "cue": "f2"}, {"do": "cue", "cue": "f3"},
        {"do": "manual", "label_th": "ติ๊กก่อนวิดีโอ"},
        {"do": "cue", "cue": "f5"}, {"do": "cue", "cue": "f4"},
        {"do": "walk", "spot": "stage_centre"}, {"do": "cue", "cue": "f6"}]}]}
HOSTS = {"format": "emma-ceremony-host-lines-v1", "lines": {
    "f1": {"kind": "host", "host_th": F1},
    "f2": {"kind": "continues"},
    "f3": {"kind": "host", "host_th": F3},
    "f4": {"kind": "manual", "note_th": "ไม่มีบทคุณมิกิ"},
    "f5": {"kind": "host", "host_th": F5},
    "f6": {"kind": "host", "host_th": F6}}}


@pytest.fixture
def fol(event, calls, call, monkeypatch):                        # noqa: F811
    """The tmp ceremony with SCRIPT / HOSTS, follow on, the keeper idle (a test ticks it by hand), plays recorded."""
    base = Path(settings.ceremony_file)
    base.write_text(json.dumps(SCRIPT, ensure_ascii=False), encoding="utf-8")
    base.with_suffix(".host.json").write_text(json.dumps(HOSTS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(follow, "KEEP_S", 3600.0)
    monkeypatch.setattr(follow, "HOLD_S", 0.6)
    monkeypatch.setattr(follow, "POLL_S", 0.1)
    monkeypatch.setattr(event_cues, "line_audible", lambda now=None: False)
    event_script.reset()
    event_order.reset()
    follow.reset()
    yield calls
    follow.reset()


_AT = [1000.0]


def hear(text: str, *, session=None) -> None:
    """The host's words as the receive loop hands them over -- the timer cancelled: a test decides itself."""
    _AT[0] += 10.0
    cap = SimpleNamespace(text=text, at=time.monotonic() + _AT[0] - 1000.0, utterance=None)
    follow.on_host_words(session or ceremony.STATE["session"], cap)
    timer = follow.STATE["timer"]
    if timer is not None:
        timer.cancel()
        follow.STATE["timer"] = None


async def begin() -> None:
    """Card 2 pressed, the window opened (what the keeper does on its first look)."""
    r = await event_order.press(1, 0)
    assert r["ok"], r
    await follow._tick()


def run(coro):
    return asyncio.run(coro)


def played(calls) -> list:
    return [c[1] for c in calls if c[0] == "cue"]


# ---- off = as it was -------------------------------------------------------------------

def test_off_by_default_the_order_and_the_page_are_as_they_were(monkeypatch):
    assert settings.ceremony_follow_host is False                       # conftest pins the default
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_script.reset()
    raw = [st["do"] for sc in event_script.order() for st in sc["steps"]]
    seen = [st["do"] for sc in event_order._scenes() for st in sc["steps"]]
    assert "follow" in raw and "follow" not in seen
    assert [d for d in raw if d not in ("follow", "device")] == [d for d in seen if d != "device"]
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    assert "follow" in [st["do"] for sc in event_order._scenes() for st in sc["steps"]]
    event_script.reset()


def test_card_2_changes_no_cue_digest():
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    assert [st["do"] for st in doc["order"][0]["steps"]] == ["manual", "opening", "follow"]
    assert "follow" in event_script.ORDER_KINDS
    # the order is not part of an approval (event_script.digest reads the words, the live brief, numbers, the clip)
    import inspect

    assert "order" not in inspect.getsource(event_script.digest)


def test_off_the_session_hook_and_the_routes_say_nothing(monkeypatch):
    from fastapi.testclient import TestClient

    from app import event_control_server

    client = TestClient(event_control_server.app)
    assert client.post("/ceremony/follow", json={"action": "off"}).status_code in (401, 403, 404)


# ---- the matcher: pure --------------------------------------------------------------------

REAL_HOSTS = {c: r for c, r in json.loads(HOST_FILE.read_text(encoding="utf-8"))["lines"].items() if r["kind"] == "host"}


@pytest.fixture
def real_lines(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    event_script.reset()
    follow.reset()
    yield
    follow.reset()
    event_script.reset()


def test_the_host_file_covers_the_order_and_says_who_is_manual(real_lines):
    cov = follow.coverage()
    assert cov["cues"] == 28 and len(cov["host"]) == 18
    assert cov["continues"] == ["s02_l05", "s05_l08", "s05_l13"]
    assert cov["chain_follow"] == ["s04_l11", "s04_l13", "s06_l07"]
    assert cov["manual"] == ["s08_l08", "s08_l12", "s08_l16", "s10_l05"]
    # the lines are the script file's own host column (not retyped): the same words
    cues = {c["id"]: c for c in json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))["cues"]}
    for cue_id, row in REAL_HOSTS.items():
        assert " ".join(row["host_th"].split()) == " ".join(cues[cue_id]["host_th"].split()), cue_id


def test_the_words_are_words_not_substrings(real_lines):
    toks = follow.tokens("สวัสดีเอ็มม่า ยินดีต้อนรับนะครับ")
    assert "เอมมา" not in toks and "ครับ" not in toks                  # her name and the polite end are not content
    assert "สระ" not in follow.tokens("สระบุรี")                      # no piece of a word
    # one cue's words inside another word never count: "ไฟ" is not in "ไฟนอล"
    res = follow.decide_text("ไฟนอล", ["s08_l06", "s08_l10", "s08_l14"])
    assert res["verdict"] in ("nothing", "weak")


def test_every_host_line_matches_itself_and_a_noisy_copy_among_its_neighbours(real_lines):
    ids = list(REAL_HOSTS)
    for k, cue in enumerate(ids):
        near = ids[k:k + 3]
        res = follow.decide_windows([REAL_HOSTS[cue]["host_th"]], near)
        assert res["verdict"] == "match" and res["cue"] == cue, (cue, res)


def test_the_eval_run_fires_the_script_and_never_an_off_script_line(real_lines):
    out = evalrun.run(seed=1)
    assert out["wrong"] == 0 and out["wrong_off"] == []
    host_rows = [r for r in out["rows"] if r["kind"] == "host"]
    assert len(host_rows) == 18
    for name in ("verbatim", "noisy", "chatter", "two_pieces", "off_script_first"):
        assert {r[name] for r in host_rows} == {"fired"}, name
    for seed in (2, 3, 4):
        other = evalrun.run(seed=seed)
        assert other["wrong"] == 0 and other["wrong_off"] == []


def test_his_line_may_be_shorter_longer_reordered_or_misheard_per_cue(real_lines):
    """Owner 24 Nov: "คำที่มิกิพูดอาจจะไม่ตรงกับสคริป อาจจะสั้นกว่าหรือยาวกว่าก็ได้". At ~70% of the line, ~150% of it (banter
    before and after), his phrases reordered, and the 70% misheard: every cue fires. At ~40% (key words only) every cue
    fires but the generic ones -- those are listed, and shown as "กดเอง" on the page."""
    generic = set(follow.generic_cues())
    assert generic == {"s04_l09", "s04_l15", "s05_l06", "s06_l05", "s07_l07", "s08_l10", "s08_l14"}
    for seed in (1, 2, 3):
        out = evalrun.run(seed=seed)
        rows = [r for r in out["rows"] if r["kind"] == "host"]
        for name in ("p70", "p150", "reorder", "p70_noisy"):
            assert {r["cue"] for r in rows if r[name] != "fired"} == set(), (seed, name)
        missed40 = {r["cue"] for r in rows if r["p40"] != "fired"}
        assert missed40 <= generic, (seed, missed40 - generic)
        assert len(missed40) <= 7 and all(r["p40"] in ("fired", "missed") for r in rows)         # never a wrong cue
        assert out["wrong"] == 0 and out["wrong_off"] == []


def test_a_shorter_line_waits_a_moment_for_its_ending_then_stops_asking(real_lines):
    short = evalrun.shortened(REAL_HOSTS["s02_l03"]["host_th"], "s02_l03", 0.4)          # the key words, not its ending
    res = follow.decide_text(short, ["s02_l03"])
    assert res["verdict"] == "no_tail"
    assert follow.decide_text(short, ["s02_l03"], final=True)["verdict"] == "match"
    assert follow.TAIL_GRACE_S > 0.5


def test_the_key_words_of_one_place_are_not_a_line(real_lines):
    """"สิ่งอำนวยความสะดวก" is cut into three words next to each other: an ad-lib that says it is not s04_l06."""
    res = follow.decide_text("ใครมีคำถามเกี่ยวกับสิ่งอำนวยความสะดวกบ้างครับ", ["s04_l06"])
    assert res["verdict"] != "match", res
    # a short line is matched whole or not at all
    res = follow.decide_text("ผมว่าห้องที่มองเห็นทะเลน่าสนใจมากเลยนะครับ", ["s05_l06"])
    assert res["verdict"] != "match", res
    assert follow.decide_text(REAL_HOSTS["s05_l06"]["host_th"], ["s05_l06"])["verdict"] == "match"


def test_words_spread_among_other_talk_are_diluted(real_lines):
    line = REAL_HOSTS["s06_l03"]["host_th"]
    words = [p for p, w in evalrun._pieces(line, "s06_l03") if w is not None]
    spread = " ".join(w + " ขอบคุณครับ ทุกท่านครับ เดี๋ยวนะครับ โอเคครับ" for w in words)
    assert follow.decide_text(spread, ["s06_l03"])["verdict"] in ("diluted", "weak")


def test_weak_tied_and_diluted_are_no_cue(real_lines):
    # half a line is weak
    half = "เอาล่ะครับ ตอนนี้ทุกคนคงเห็นแล้วว่า Embassy World มีอะไรพิเศษบ้าง"
    assert follow.decide_text(half, ["s03_l03"])["verdict"] in ("weak", "nothing", "no_tail")
    # the lobby lights off / on share words: the verb decides
    assert follow.decide_text("เอ็มม่า เปิดไฟล็อบบี้อีกครั้งครับ", ["s08_l06", "s08_l10"])["cue"] == "s08_l10"
    # two cues the host's words cover about equally: none
    a, b = REAL_HOSTS["s02_l03"]["host_th"], REAL_HOSTS["s02_l09"]["host_th"]
    both = follow.decide_text(a + " " + b, ["s02_l03", "s02_l09"])
    assert both["verdict"] != "match" or both["cover"] - both["second"] >= follow.MARGIN
    # his line after a long speech (banter before it is none of its span): it is still his line
    speech = " ".join(["ขอบคุณทุกท่านที่มาร่วมงานในวันนี้ครับ"] * 6) + " " + REAL_HOSTS["s05_l06"]["host_th"]
    assert follow.decide_text(speech, ["s05_l06"])["verdict"] == "match"


def _slice(text: str, lo: int, hi: int) -> str:
    """The raw words of `text` whose content-word number is in [lo, hi) -- what the host said of it, in his own spelling."""
    import unicodedata

    from app import event_script

    out, k = [], 0
    for piece in event_script._segment(follow._NAME_RE.sub(" ", unicodedata.normalize("NFKC", text))):
        t = follow._norm(piece)
        content = bool(t) and t not in follow._STOP and t not in follow._NAMES and bool(
            follow._THAI.search(t) or follow._ALNUM.search(t)) and not (len(t) < 2 and not t.isdigit())
        if lo <= k < hi:
            out.append(piece)
        if content:
            k += 1
    return "".join(out)


def test_the_bars_are_the_measured_ones():
    """A weaker bar plays a cue on half a line: pinned, as the eval's table is run with these."""
    assert (follow.MIN_COVER, follow.MARGIN, follow.MIN_MATCHED, follow.MIN_PRECISION) == (0.6, 0.15, 3, 0.4)
    assert follow.QUIET_S >= 0.8 and follow.LOOKAHEAD == 3


def test_half_a_long_line_never_fires_the_head_missing_or_the_tail_missing(real_lines):
    import math

    fired_early = []
    long_lines = 0
    for cue, row in REAL_HOSTS.items():
        n = len(follow.tokens(row["host_th"]))
        if n < 8:
            continue
        long_lines += 1
        # the line's first part only (its ending was not said yet): the host is still talking
        head_only = follow.decide_text(_slice(row["host_th"], 0, n - max(3, math.ceil(n * 0.3))), [cue])
        if head_only["verdict"] == "match":
            fired_early.append(cue)                       # a tail word said earlier in the line too (s09_l02: "Embassy World")
        assert follow.decide_text(_slice(row["host_th"], 0, n), [cue])["verdict"] == "match", cue
    assert long_lines >= 8 and len(fired_early) <= 2, fired_early


def test_never_backwards_only_the_next_few(real_lines):
    scenes = event_script.order()
    host = follow.lines()
    marks = {}
    last = None
    for sc in scenes[:1]:
        for st in sc["steps"]:
            marks[event_order._key(sc["n"], st["i"])] = {"state": "done"}
            last = (sc["n"], st["i"])
    ptr = follow.pointer_of(scenes, marks, last, host)
    assert ptr["kind"] == "host" and [c["cue"] for c in ptr["cands"]] == ["s02_l03"]       # s02_l05 continues: stops there
    # a line of an earlier cue is no candidate once it is taken
    marks[event_order._key(ptr["cands"][0]["n"], ptr["cands"][0]["i"])] = {"state": "done"}
    ptr = follow.pointer_of(scenes, marks, (2, 0), host)
    assert ptr["kind"] == "continues" and ptr["cue"] == "s02_l05"
    assert follow.decide_windows([REAL_HOSTS["s02_l03"]["host_th"]], [c["cue"] for c in ptr["cands"]])["verdict"] == "nothing"


# ---- the mode on a call --------------------------------------------------------------------

def test_card_2_turns_it_on_and_holds_the_window_open_without_the_cap(fol, rows):         # noqa: F811
    async def go():
        before = ceremony.live_state()
        assert before is None
        await begin()
        return ceremony.live_state()

    live = run(go())
    assert follow.STATE["on"] and live is not None and live["cue"] == ceremony.ADHOC_ID
    assert live["until"] - live["opened_at"] > 3600                          # not CEREMONY_ADHOC_MAX_S (120)
    assert ceremony.FOLLOW_BRIEF_TH in ceremony.STATE["live"]["brief"]
    assert event_order.STATE["marks"]["1.0"]["state"] == "done"
    assert {"event": "ceremony_follow", "action": "start", "by": "order", "cues": 6} in rows


def test_the_hosts_line_plays_the_next_cue_like_a_staff_press(fol, rows):               # noqa: F811
    async def go():
        await begin()
        hear(F1)
        await follow._decide()

    run(go())
    assert played(fol) == ["f1"] and ("cue", "f1", "staff", None) in fol
    assert ceremony.live_state() is None                                     # the press closed her window (script_press)
    assert follow.STATE["last_fire"]["cue"] == "f1" and follow.STATE["last_fire"]["ok"] is True
    assert follow.STATE["last_fire"]["by"] == "host"
    fire = [r for r in rows if r["event"] == "ceremony_follow" and r.get("action") == "fire"]
    assert fire and fire[0]["cue"] == "f1" and "text" not in fire[0] and "heard" not in fire[0]
    assert event_order.STATE["last"] == (2, 0)


def test_off_script_words_play_nothing_and_her_window_stays(fol):                      # noqa: F811
    async def go():
        await begin()
        for text in ("เอ็มม่า ห้องสตูดิโอราคาเท่าไหร่ครับ", "Emma, what time does the gym open?", "ขอบคุณทุกท่านครับ"):
            hear(text)
            await follow._decide()
        return ceremony.live_state()

    assert run(go()) is not None and played(fol) == []
    assert follow.STATE["last_eval"]["verdict"] in ("nothing", "weak")


def test_no_name_needed_for_the_script_line_but_the_live_answer_stays_named_only(fol):    # noqa: F811
    async def go():
        await begin()
        hear(F3.replace("เอ็มม่า", ""))
        await follow._decide()                                               # f3 is behind f1/f2: not a candidate yet
        hear(F1.replace("เอ็มม่า", ""))                                      # f1 without her name
        await follow._decide()

    run(go())
    assert played(fol) == ["f1"]
    from app.providers import gemini

    assert gemini._ceremony_named_only() is True                             # the door of her live answers: her name


def test_he_spoke_and_nothing_matched_the_next_step_stands_out_until_a_cue_plays(fol):       # noqa: F811
    async def go():
        await begin()
        assert "nudge" not in follow.snapshot()
        hear("ขอบคุณทุกท่านที่มาร่วมงานครับ")
        await follow._decide()
        snap = follow.snapshot()
        assert snap["nudge"] == {"n": 2, "i": 0, "at": snap["nudge"]["at"]}            # f1: the step to press by hand
        assert snap["next"]["expect"][0]["generic"] is True                           # (f1's line is short: "กดเอง")
        hear(F1)
        await follow._decide()
        assert "nudge" not in follow.snapshot()                                        # a cue played since
        await follow._tick()                                                           # her window is back
        hear("ขอบคุณอีกครั้งครับ")
        await follow._decide()
        again = follow.snapshot()
        assert again["nudge"]["i"] != 0
        follow.pause("stop")
        assert "nudge" not in follow.snapshot()
        follow.resume()
        follow.STATE["last_eval"]["at"] -= follow.NUDGE_S + 1
        assert "nudge" not in follow.snapshot()

    run(go())


def test_a_shorter_line_is_decided_after_the_grace_and_not_before(fol, monkeypatch):    # noqa: F811
    monkeypatch.setattr(follow, "TAIL_GRACE_S", 0.05)

    async def go():
        await begin()
        for text in (F1, F3):
            hear(text)
            await follow._decide()
            follow.STATE["quiet_since"] = time.monotonic() - 5
            follow.STATE["heard_mono"] = None
            await follow._maybe_continue(time.monotonic())
            await follow._tick()
        await event_order.press(2, 3)
        await follow._tick()
        hear("ช่วยเปิดวิดีโอเทคโนโลยี Embassy World")                  # f5, without its ending
        await follow._decide()

    run(go())
    assert played(fol)[-1] == "f5", played(fol)


def test_a_cue_behind_a_tick_a_walk_or_a_hostless_cue_cannot_fire(fol):                # noqa: F811
    async def go():
        await begin()
        hear(F1)
        await follow._decide()
        follow.STATE["quiet_since"] = time.monotonic() - 5                   # f2 continues by itself
        follow.STATE["heard_mono"] = None
        await follow._maybe_continue(time.monotonic())
        await follow._tick()                                                 # (the keeper: her window is back)
        hear(F3)
        await follow._decide()
        await follow._tick()
        # f3 taken; the unticked manual step now stands before f5
        ptr = follow.pointer()
        assert ptr["kind"] == "gate" and "ติ๊กก่อนวิดีโอ" in ptr["label"], ptr
        hear(F5)
        await follow._decide()
        assert follow.STATE["last_eval"]["verdict"] == "blocked"
        # ticked: f5 may go; f4 (no host line) then blocks f6
        await event_order.press(2, 3)
        await follow._tick()
        hear(F5)
        await follow._decide()
        await follow._tick()
        hear(F6)
        await follow._decide()
        return follow.pointer()

    ptr = run(go())
    assert played(fol) == ["f1", "f2", "f3", "f5"], played(fol)
    assert "f6" not in played(fol) and ptr["kind"] == "manual" and ptr["cue"] == "f4"
    assert "walk" not in [c[0] for c in fol]                                # voice never starts a walk


def test_a_continuation_goes_by_itself_once_and_a_refusal_is_not_retried(fol, monkeypatch):    # noqa: F811
    async def go():
        await begin()
        hear(F1)
        await follow._decide()
        await follow._tick()                                                 # the line is "over" but only for 0 s
        assert "f2" not in played(fol)
        follow.STATE["quiet_since"] = time.monotonic() - 5
        follow.STATE["heard_mono"] = None
        assert await follow._maybe_continue(time.monotonic()) is True
        assert played(fol) == ["f1", "f2"]
        assert follow.STATE["last_fire"]["by"] == "continue"
        return follow.pointer()

    ptr = run(go())
    assert ptr["kind"] == "host" and ptr["cands"][0]["cue"] == "f3"

    async def refused():
        follow.reset()
        event_order.reset()
        await begin()
        hear(F1)
        await follow._decide()

        async def no(cue_id, *, by="staff", show=None):
            fol.append(("cue", cue_id, by, show))
            return {"ok": False, "error": "not_ready", "error_th": "ยังไม่พร้อม"}

        monkeypatch.setattr(event_cues, "play", no)
        follow.STATE["quiet_since"] = time.monotonic() - 5
        follow.STATE["heard_mono"] = None
        before = len(fol)
        await follow._maybe_continue(time.monotonic())
        n = len(fol)
        await follow._maybe_continue(time.monotonic())
        return before, n, len(fol), follow.pointer()

    n0, n1, n2, ptr = run(refused())
    assert n1 == n0 + 1 and n2 == n1                                          # asked once, then staff's
    assert ptr["kind"] == "manual" and "เล่นต่อเองไม่ได้" in ptr["why_th"] and "ข้ามขั้นนี้" in ptr["why_th"]


def test_she_speaks_the_robot_moves_a_stop_a_decided_line_waits_then_is_dropped(fol, monkeypatch):   # noqa: F811
    async def go():
        await begin()
        monkeypatch.setattr(event_cues, "line_audible", lambda now=None: True)
        hear(F1)
        await follow._decide()                                               # held HOLD_S then dropped
        assert played(fol) == [] and follow.STATE["last_fire"]["error"] == "held"
        monkeypatch.setattr(event_cues, "line_audible", lambda now=None: False)
        monkeypatch.setattr(event_host, "robot_moving", lambda: True)
        hear(F1)
        await follow._decide()
        assert played(fol) == []
        monkeypatch.setattr(event_host, "robot_moving", lambda: False)
        hear(F1)
        await follow._decide()
        assert played(fol) == ["f1"]

    run(go())


def test_the_red_stop_pauses_until_staff_resume_and_off_never_comes_back(fol, rows):          # noqa: F811
    async def go():
        await begin()
        assert follow.pause("stop") is True
        assert follow.wants_words(ceremony.STATE["session"]) is False
        hear(F1)
        await follow._decide()
        assert played(fol) == []
        await follow._tick()
        assert ceremony._adhoc_live(time.time()) is not None                 # (the stop's route closes it; a pause alone does not reopen)
        assert follow.snapshot()["paused"] == "stop"
        assert follow.resume("page") is True
        hear(F1)
        await follow._decide()
        assert played(fol) == ["f1"]
        assert follow.stop("staff") is True
        await follow._tick() if follow.STATE["on"] else None
        assert follow.wants_words(ceremony.STATE["session"]) is False

    run(go())
    assert [r["action"] for r in rows if r["event"] == "ceremony_follow" and r["action"] in ("pause", "resume", "stop")] == [
        "pause", "resume", "stop"]


def test_a_staff_press_moves_the_pointer(fol):                                           # noqa: F811
    async def go():
        await begin()
        assert follow.pointer()["cands"][0]["cue"] == "f1"
        r = await event_order.press(2, 2)                                     # staff press f3 out of turn
        assert r["ok"]
        ptr = follow.pointer()
        assert [c["cue"] for c in ptr["cands"]] == [] and ptr["kind"] == "gate"      # the tick is next
        hear(F1)
        await follow._decide()                                                # f1 is behind the pointer now
        assert played(fol) == ["f3"]

    run(go())


def test_the_window_closed_by_staff_pauses_by_the_red_stop_ends_by_a_reset(fol):         # noqa: F811
    async def go():
        await begin()
        ceremony.adhoc_close("staff_stopped")
        await follow._tick()
        assert follow.STATE["paused"] == "stop"
        follow.resume()
        await follow._tick()                                                  # reopened, quiet (no logo)
        assert ceremony._adhoc_live(time.time()) is not None
        ceremony.adhoc_close("reset")
        await follow._tick()
        assert follow.STATE["on"] is False

    run(go())


def test_the_snapshot_shows_the_scripts_line_never_what_he_said(fol):                  # noqa: F811
    async def go():
        await begin()
        secret = "คำที่คุณมิกิพูดจริงๆ ไม่ควรขึ้นหน้าจอ"
        hear(secret)
        await follow._decide()
        return follow.snapshot(), secret

    snap, secret = run(go())
    blob = json.dumps(snap, ensure_ascii=False)
    assert secret not in blob and F1 in blob
    assert snap["on"] and snap["next"]["expect"][0]["cue"] == "f1" and snap["window_open"] is True
    assert snap["last_eval"]["verdict"] in ("nothing", "weak")
    assert snap["manual_cues"] == ["f4"] and snap["hostless"] == 2


def test_the_mic_muted_is_said(fol, monkeypatch):                                     # noqa: F811
    async def go():
        await begin()
        monkeypatch.setattr(event_host, "_mic_closed", lambda: True)
        return follow.snapshot()

    snap = run(go())
    assert snap["mic_muted"] is True and "ไมค์ปิด" in snap["mic_th"]


def test_the_ears_are_up_in_the_window_and_the_stop_puts_them_back(fol):                # noqa: F811
    async def go():
        await begin()
        assert event_host.mic_deaf() is False                               # cue-only, but her window is open
        follow.pause("stop")
        ceremony.adhoc_close("staff_stopped")
        assert event_host.mic_deaf() is True                                # silence again
        follow.stop("staff")

    run(go())


def test_the_route_walks_on_off_resume_and_the_red_stop_pauses(fol):                  # noqa: F811
    from fastapi.testclient import TestClient

    from app import event_control_server

    async def start():
        await begin()

    run(start())
    key = getattr(settings, "event_control_key", "")
    client = TestClient(event_control_server.app)
    if key:
        client.cookies.set("emma_event_key", key)
    snap = follow.snapshot()
    assert snap["on"]
    r = client.post("/ceremony/follow", json={"action": "bad"})
    assert r.status_code in (400, 401, 403)
    follow.pause("stop")
    assert follow.snapshot()["paused"] == "stop"
    assert follow.resume("test")
    assert follow.stop("staff")
    assert follow.snapshot()["on"] is False and follow.snapshot()["can_start"] is True


def test_a_second_card_2_press_while_on_is_nothing_and_after_a_pause_it_resumes(fol):      # noqa: F811
    async def go():
        await begin()
        again = await follow.start(by="order")
        assert again == {"ok": True, "follow": "already"}
        follow.pause("stop")
        back = await follow.start(by="page")
        assert back == {"ok": True, "follow": "resumed"} and follow.STATE["paused"] is None

    run(go())


def test_it_is_refused_where_she_already_answers_by_name(fol):                         # noqa: F811
    ceremony.STATE["replies"] = "named"
    assert "ตอบเมื่อถูกเรียกชื่อ" in follow.start_refusal()
    ceremony.STATE["replies"] = "cues"
    settings.ceremony_follow_host = False
    assert follow.start_refusal() is not None


def test_the_brief_and_the_prompt_rule_hold_the_live_answer_to_what_was_asked():
    for text in (ceremony.FOLLOW_BRIEF_TH, ceremony.FOLLOW_PROMPT_TH):
        assert not any(ch.isdigit() for ch in text)
        for mark in ("«", "»", '"', "“", "”"):
            assert mark not in text
        assert "เกินกว่าที่ถูกถาม" in text and "เรียกชื่อ" in text
    assert len(ceremony.ADHOC_BRIEF_TH + ceremony.FOLLOW_BRIEF_TH) < 1200


def test_the_prompt_has_the_follow_rule_only_when_the_switch_is_on(monkeypatch):
    block_off = event_host.ceremony_block()
    assert ceremony.FOLLOW_PROMPT_TH not in block_off
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    assert ceremony.FOLLOW_PROMPT_TH in event_host.ceremony_block()
    monkeypatch.setattr(settings, "ceremony_follow_host", False)
    assert event_host.ceremony_block() == block_off


def test_a_chained_joke_still_chains_when_the_server_presses_its_set_up(monkeypatch, tmp_path, calls, call, event):   # noqa: F811
    """The real order and host lines (signed copies): s04_l09 is `chain` -- the set-up pressed by the host's
    words starts the punchline's chain exactly as a staff press does."""
    doc = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    doc["cues"] = [_signed({k: v for k, v in c.items() if not k.startswith("approved")}) for c in doc["cues"]]
    target = Path(settings.ceremony_file)
    target.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    target.with_suffix(".host.json").write_text(HOST_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(follow, "KEEP_S", 3600.0)
    monkeypatch.setattr(event_cues, "line_audible", lambda now=None: False)
    event_script.reset()
    event_order.reset()
    follow.reset()
    scenes = event_order._scenes()
    for sc in scenes:
        for st in sc["steps"]:
            if (sc["n"], st["i"]) < (4, 2):
                event_order.STATE["marks"][event_order._key(sc["n"], st["i"])] = {"state": "done", "at": time.time(), "since": time.monotonic(), "at_step": (sc["n"], st["i"]), "error_th": None}
    event_order.STATE["last"] = (4, 1)
    follow.STATE.update(on=True, session=ceremony.STATE["session"], since=time.time())

    async def go():
        ceremony.adhoc_open(follow=True)
        hear(REAL_HOSTS["s04_l09"]["host_th"])
        await follow._decide()

    run(go())
    assert "s04_l09" in played(calls)
    chain = event_order.STATE.get("chain")
    assert chain is not None and tuple(chain["lead"]) == (4, 2)
    follow.reset()


# ---- the staff server, over HTTP ----------------------------------------------------------------

def test_the_staff_routes_state_on_stop_resume_off(real, monkeypatch, tmp_path):             # noqa: F811
    """The real staff server: /ceremony/state carries `follow` only with the switch on; the page's own controls, and the
    red stop that PAUSES the mode (never ends it)."""
    from tests.ceremony_app import ceremony_on

    ceremony_on(replies="cues")
    script = Path(settings.ceremony_file)
    script.with_suffix(".host.json").write_text(json.dumps(HOSTS, ensure_ascii=False), encoding="utf-8")
    assert "follow" not in real.get("/ceremony/state").json()                            # off: no key at all
    assert real.post("/ceremony/follow", json={"action": "on"}).status_code == 404
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(follow, "KEEP_S", 3600.0)
    doc = real.get("/ceremony/state").json()["follow"]
    assert doc["enabled"] is True and doc["on"] is False
    r = real.post("/ceremony/follow", json={"action": "on"}).json()
    assert r["ok"] and r["follow"]["on"] is True
    assert real.post("/ceremony/follow", json={"action": "toggle"}).status_code == 400
    assert real.post("/event/stop").json()["ok"] is not None
    paused = real.get("/ceremony/state").json()["follow"]
    assert paused["paused"] == "stop" and "พัก" in paused["paused_th"]
    assert real.post("/ceremony/follow", json={"action": "resume"}).json()["follow"]["paused"] is None
    off = real.post("/ceremony/follow", json={"action": "off"}).json()
    assert off["ok"] and off["follow"]["on"] is False
    # off never needs the mode to be on, and says so quietly
    assert real.post("/ceremony/follow", json={"action": "off"}).json()["stopped"] is False


# ---- the staff page, in Chromium ------------------------------------------------------------------

def _follow_doc(**over) -> dict:
    now = time.time()
    doc = {"enabled": True, "on": True, "paused": None, "paused_th": None, "since": now - 60, "server_time": now,
           "window_open": True, "mic_muted": False, "mic_th": None, "cues": 29, "with_host": 18, "hostless": 11,
           "manual_cues": ["s04_l13"], "generic_cues": ["s05_l06"], "can_start": False, "gate_th": None,
           "next": {"kind": "host", "at": [2, 1], "label": None, "why_th": None,
                    "expect": [{"n": 2, "i": 1, "cue": "s02_l05", "host_th": "บรรทัดตามบทของคุณมิกิ", "generic": True}]},
           "last_fire": None, "last_eval": {"at": now - 3, "verdict": "weak", "cue": "s02_l05", "cover": 0.4, "why_th": None}}
    doc.update(over)
    return doc


def test_the_page_card_nudges_the_next_step_and_stops_the_mode(chromium):
    from tests.test_event_order_page import Page, _order, _scenes, _state

    state = _state(_order(_scenes(), last=[2, 0], next=[2, 1]))
    state["follow"] = _follow_doc(nudge={"n": 2, "i": 1, "at": time.time() - 3})
    page = Page(chromium, state)
    try:
        assert page.visible("#followBox") and "เปิด" in page.text("#followNow")
        assert "บรรทัดตามบทของคุณมิกิ" in page.text("#followExpect") and "กดเอง" in page.text("#followExpect")
        assert page.visible("#followNudge") and page.visible("#followOff")
        assert "nudged" in page.page.locator("#step-2-1").get_attribute("class")
        assert "nudged" not in page.page.locator("#step-2-2").get_attribute("class")
        assert "ฟังล่าสุด" in page.text("#followEval") and "ตรงบางส่วน" in page.text("#followEval")
        page.posts["/ceremony/follow"] = {"ok": True, "stopped": True, "follow": _follow_doc(on=False, can_start=True)}
        page.page.click("#followOff")
        page.page.wait_for_timeout(300)
        assert page.sent[-1] == ("/ceremony/follow", {"action": "off"})
    finally:
        page.close()


def test_the_page_card_paused_muted_and_without_a_nudge(chromium):
    from tests.test_event_order_page import Page, _order, _scenes, _state

    state = _state(_order(_scenes(), last=[2, 0], next=[2, 1]))
    state["follow"] = _follow_doc(paused="stop", paused_th="กดหยุดแดง — พักโหมดสด", mic_muted=True,
                                  mic_th="ไมค์ปิดอยู่ที่หน้าหุ่น")
    page = Page(chromium, state)
    try:
        assert "พักอยู่" in page.text("#followNow") and page.visible("#followResume")
        assert page.visible("#followMic") and "ไมค์ปิด" in page.text("#followMic")
        assert not page.visible("#followNudge")
        assert "nudged" not in page.page.locator("#step-2-1").get_attribute("class")
        # off = no card at all, the page as it was
        page.state["follow"] = {"enabled": False}
        page.page.wait_for_timeout(1300)
        assert not page.visible("#followBox")
    finally:
        page.close()


# ---- English (25 Nov: Khun Miki speaks English; host_en = OUR translation, not his words) -------------------------

def test_every_host_line_has_an_english_line_and_it_matches_itself_among_its_neighbours(real_lines):
    ids = list(REAL_HOSTS)
    assert all(REAL_HOSTS[c].get("host_en") for c in ids)
    for k, cue in enumerate(ids):
        res = follow.decide_windows([REAL_HOSTS[cue]["host_en"]], ids[k:k + 3])
        assert res["verdict"] == "match" and res["cue"] == cue and res["lang"] == "en", (cue, res)


def test_english_paraphrases_asr_errors_and_banter_fire_the_right_cue_and_off_script_never(real_lines):
    for seed in (1, 2, 3):
        out = evalrun.run(seed=seed, lang="en")
        assert out["wrong"] == 0 and out["wrong_off"] == [], (seed, out["wrong_off"][:3])
        rows = [r for r in out["rows"] if r["kind"] == "host"]
        assert len(rows) == 18
        generic = set(follow.generic_cues("en"))              # a short line with much banter around it is "กดเอง"
        for name in ("verbatim", "chatter", "two_pieces", "off_script_first", "p150", "reorder", "asr"):
            assert {r["cue"] for r in rows if r[name] != "fired"} <= (generic if name in ("p150", "asr") else set()), (seed, name)
        assert out["fired"] >= 200, out["fired"]
    # the Thai run is as it was
    thai = evalrun.run(seed=1)
    assert thai["wrong"] == 0 and thai["wrong_off"] == [] and thai["fired"] >= 170


def test_a_short_english_line_is_every_word_of_it_and_an_ad_lib_about_it_is_not(real_lines):
    assert follow.decide_text("Sea view rooms are very popular with families.", ["s05_l06"])["verdict"] != "match"
    assert follow.decide_text("I would like a room with a sea view.", ["s05_l06"])["verdict"] == "match"
    # the lobby lights off / on: the verb decides
    res = follow.decide_text("Emma, please turn the lobby lights back on.", ["s08_l06", "s08_l10"])
    assert res["cue"] == "s08_l10" and res["verdict"] == "match"


def test_the_transcribers_english_habits_are_one_word(real_lines):
    assert follow._stem("units") == follow._stem("unit")
    assert follow._stem("units") == follow._stem("unit") and follow._stem("located") == follow._stem("location")
    assert follow._stem("facilities") == follow._stem("facility")
    assert follow._stem("ดี") == "ดี" and follow._stem("ฟิตเนส1") == "ฟิตเนส1"


def test_generic_lines_are_listed_per_language_and_the_page_marks_either(real_lines):
    assert set(follow.generic_cues("en")) == {"s04_l09", "s05_l06", "s06_l05", "s08_l10", "s08_l14"}
    assert set(follow.generic_cues("th")) <= set(follow.generic_cues("any"))
    assert set(follow.generic_cues("en")) <= set(follow.generic_cues("any"))


def test_a_line_with_no_english_is_matched_in_thai_only(real_lines):
    follow._stock["compiled"] = None
    follow._stock.pop("compiled")
    rows = follow.lines()
    assert rows["s02_l03"]["host_en"]
    rows["s02_l03"]["host_en"] = None
    follow._stock.pop("compiled", None)
    try:
        assert "s02_l03|en" not in follow._compiled() and "s02_l03" in follow._compiled()
        assert follow.decide_text(REAL_HOSTS["s02_l03"]["host_th"], ["s02_l03"])["verdict"] == "match"
    finally:
        follow.reset()


def test_the_semantic_net_never_fires_below_its_thresholds_and_never_without_a_model():
    """The net (app/ceremony_follow_sem.py) is NOT wired into the server: the sweep could not separate the lines from
    off-script talk on the same topic (docs/emma-ceremony.md). Its rule is still pinned: all three bars, no model = no fire."""
    import numpy as np

    from app import ceremony_follow_sem as sem

    def unit(*v):
        a = np.array(v, dtype="float32")
        return a / np.linalg.norm(a)

    table = {"line a": unit(1, 0, 0), "line b": unit(0, 1, 0), "close": unit(1, 0.2, 0), "tie": unit(1, 0.95, 0),
             "bar": unit(1, 0.0, 0.6), "far": unit(0, 0, 1)}
    anchor_near = {t: unit(1, 0.1, 0.8) for t in sem.ANCHORS}

    def encode(texts):
        return np.stack([table.get(t, anchor_near.get(t, unit(0, 0, 1))) for t in texts])

    sem.reset()
    lines = {"a": ["line a"], "b": ["line b"]}
    assert sem.decide("close", lines, encode=encode)["fire"] is True
    assert sem.decide("tie", lines, encode=encode)["why"] in ("tie", "weak")
    assert sem.decide("far", lines, encode=encode)["fire"] is False
    # closer to an off-script anchor than the margin allows: no fire
    res = sem.decide("bar", lines, encode=encode, sim=0.0, margin=0.0)
    assert res["fire"] is False and res["why"] == "off_script"
    assert sem.decide("close", lines, encode=lambda t: None)["why"] == "no_model"
    assert sem.decide("", lines, encode=encode)["fire"] is False
    sem.reset()
