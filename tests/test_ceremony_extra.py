"""The ceremony's extra lines (CEREMONY_EXTRA_LINES, owner 24 Nov 2026 ~19:20: "ก็ให้มันทำเป็นบททั้งหมดเลย")."""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_call as call_, ceremony_extra as extra, ceremony_follow as follow, ceremony_stories
from app import event_cues, event_order, event_script, stories
from app.config import settings
from tests.test_ceremony_follow import real_lines  # noqa: F401 -- fixture
from tests.test_ceremony_name_first import ORDER, named  # noqa: F401 -- fixture
from tests.test_event_order import REAL_SCRIPT, call, calls, event, rows  # noqa: F401 -- fixtures

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "data" / "ceremony" / "floor1_ceremony.extra.json"
HOST = ROOT / "data" / "ceremony" / "floor1_ceremony.extra.host.json"


@pytest.fixture(scope="module")
def doc():
    return json.loads(FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def host():
    return json.loads(HOST.read_text(encoding="utf-8"))


def _rules(host):
    return {k: {"means": v["means"], "not": v["not"], "not_all": host["never"]} for k, v in host["lines"].items()}


def _script_rules():
    raw = json.loads((ROOT / "data" / "ceremony" / "floor1_ceremony.host.json").read_text(encoding="utf-8"))["lines"]
    return raw, {k: {"means": r["means"], "not": r.get("means_not") or [], "not_all": r.get("means_not_all") or [],
                     "tell": r.get("tell")} for k, r in raw.items() if r["kind"] == "host"}


def test_the_lines_are_the_approved_stories_own_words_and_none_is_of_the_order(doc, host, monkeypatch):
    monkeypatch.setattr(settings, "stories_enabled", True)
    ids = [c["id"] for c in doc["cues"]]
    assert ids == sorted("x_" + t for t in ceremony_stories.listed()) and set(host["lines"]) == set(ids)
    script = json.loads(REAL_SCRIPT.read_text(encoding="utf-8"))
    assert not set(ids) & {c["id"] for c in script["cues"]}
    assert not any(str(st.get("cue")).startswith("x_") for sc in script["order"] for st in sc["steps"])
    have = stories.available()
    if not have:
        pytest.skip("the story catalog is a draft in this tree")
    for c in doc["cues"]:
        story = have[c["id"][2:]]
        assert c["en"] == " ".join(" ".join(line["en"].split()) for line in story["lines"]), c["id"]   # word for word
        assert event_script._problems_in(event_script._normalise(c), "en") == [], c["id"]
        assert c["approved_sha256"] in (None, event_script.digest(event_script._normalise(c))), c["id"]


def test_every_pattern_compiles_and_a_line_has_some(host):
    for cue, row in host["lines"].items():
        assert row["means"], cue
        for p in row["means"] + row["not"] + host["never"]:
            re.compile(p)


def test_the_stories_own_questions_find_their_line(host, monkeypatch):
    monkeypatch.setattr(settings, "stories_enabled", True)
    have = stories.available()
    if not have:
        pytest.skip("the story catalog is a draft in this tree")
    _raw, script = _script_rules()
    every = {**script, **_rules(host)}
    right = total = 0
    for cue in host["lines"]:
        for q in have[cue[2:]]["questions_en"]:
            got = call_.by_meaning("Emma, " + q, every)
            total += 1
            right += bool(got) and got["cue"] == cue
            assert got is None or got["cue"] == cue or got["cue"] in script, (q, got)       # never ANOTHER extra
    assert total > 150 and right / total >= 0.95, (right, total)


def test_no_question_of_the_script_goes_to_an_extra(host):
    raw, script = _script_rules()
    every = {**script, **_rules(host)}
    for cue in script:
        for said in (raw[cue].get("ask_en") or []) + [raw[cue]["host_en"]]:
            got = call_.by_meaning("Emma, " + said, every)
            assert got is None or not got["cue"].startswith("x_"), (cue, said, got)


@pytest.mark.parametrize("said", [
    "Emma, how much is the studio?", "Emma, what time does the gym open?", "Emma, when will the lobby be ready?",
    "Emma, can I rent a one bedroom?", "Emma, is there free Wi-Fi in the lobby?", "Emma, do you like restaurants?",
    "Emma, where is the nearest swimming school?", "Emma, how many people can the meeting room hold?",
    "Emma, where can I park my car?", "Emma, is there a promotion for the two bedroom?"])
def test_what_no_line_may_answer_is_left_to_her(host, said):
    _raw, script = _script_rules()
    got = call_.by_meaning(said, {**script, **_rules(host)})
    assert got is None or not got["cue"].startswith("x_"), got


@pytest.mark.parametrize("said,cue", [
    ("Emma, where is the gym?", "x_fitness_where"), ("Emma, tell us about the sauna.", "x_saunas_where"),
    ("Emma, what is there for the kids?", "x_for_families"), ("Emma, who is the CEO?", "x_executives"),
    ("Emma, are there hospitals nearby?", "x_nearby_hospitals"), ("Emma, how far is the airport?", "x_travel_airports"),
    ("Emma, what is on the third floor?", "x_facilities_level3"), ("Emma, show us the sky pool.", "x_sky_pool_where"),
    ("Emma, what is Journey to Mars?", "x_journey_mars_where"), ("Emma, where is the kids' room?", "x_kids_room_where"),
    ("Emma, how is the one bedroom different from the two bedroom?", "x_unit_compare_1br_2br")])
def test_other_words_find_the_line(host, said, cue):
    _raw, script = _script_rules()
    got = call_.by_meaning(said, {**script, **_rules(host)})
    assert got is not None and got["cue"] == cue, got


# ---- served only when signed, words and voice --------------------------------------------------------------

@pytest.fixture
def signed(real_lines, monkeypatch, tmp_path, doc):                                 # noqa: F811
    """A copy of the extras beside a copy of the script, one line signed."""
    script = tmp_path / "floor1_ceremony.json"
    script.write_text(REAL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    copy = json.loads(json.dumps(doc))
    for c in copy["cues"]:
        c.update(approved_by=None, approved_at=None, approved_sha256=None, approved_voice_sha256=None)
    gym = next(c for c in copy["cues"] if c["id"] == "x_fitness_where")
    gym.update(approved_by="test", approved_at="2026-11-24", approved_sha256=event_script.digest(event_script._normalise(gym)))
    (tmp_path / "floor1_ceremony.extra.json").write_text(json.dumps(copy, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "floor1_ceremony.extra.host.json").write_text(HOST.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "floor1_ceremony.host.json").write_text(
        (ROOT / "data" / "ceremony" / "floor1_ceremony.host.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(settings, "ceremony_file", str(script))
    for key in ("ceremony_extra_lines", "ceremony_name_first", "ceremony_script_show_only", "stories_enabled"):
        monkeypatch.setattr(settings, key, True)
    monkeypatch.setattr(event_script, "number_problems", lambda c, lookup=None: [])
    monkeypatch.setattr(stories, "available", lambda catalog=None: {c["id"][2:]: {} for c in copy["cues"]})
    voice = {"approved": True}
    monkeypatch.setattr(event_cues, "_voice_status", lambda c: dict(voice))
    event_script.reset()
    extra.reset()
    yield voice
    extra.reset()
    event_script.reset()


def test_a_line_is_offered_only_with_both_signatures(signed, monkeypatch):
    assert set(extra.servable()) == {"x_fitness_where"} and set(extra.rules()) == {"x_fitness_where"}
    gym = extra.rules()["x_fitness_where"]
    assert gym["not_all"] and call_.by_meaning("Emma, where is the gym?", {"x_fitness_where": gym})
    assert call_.by_meaning("Emma, what time does the gym open?", {"x_fitness_where": gym}) is None
    line, why = extra.pick("x_fitness_where")
    assert why is None and line["lang"] == "en" and line["sub_th"] and line["th"].startswith("Our fitness centre")
    assert event_cues._pick("x_fitness_where")[1] is None and event_cues._pick("x_security") == (None, "no_cue")
    assert follow._recorded("x_fitness_where") is True and follow._recorded("x_security") is False
    extra.reset()
    signed["approved"] = False                                       # the recording is not signed: nothing is read live
    assert extra.servable() == {} and extra.pick("x_fitness_where") == (None, "no_cue")
    signed["approved"] = True
    extra.reset()
    monkeypatch.setattr(settings, "ceremony_extra_lines", False)
    assert extra.on() is False and extra.rules() == {} and extra.pick("x_fitness_where") == (None, "no_cue")


def test_the_story_tool_is_only_the_fallback(signed, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_offscript_stories", True)
    assert ceremony_stories.on() is False                            # a line is served: the model has no story to call
    signed["approved"] = False
    extra.reset()
    assert ceremony_stories.on() is True


def test_her_prompt_and_the_order_know_nothing_of_them(signed):
    assert not any(str(c).startswith("x_") for c in event_script.servable())
    assert not any(str(d["cue"]).startswith("x_") for d in follow._all_host_cues())


# ---- the decision ------------------------------------------------------------------------------------------

def test_his_question_plays_the_line_with_its_story_and_the_order_stands(named, host, monkeypatch):      # noqa: F811
    monkeypatch.setattr(extra, "rules", lambda: {k: {**v, "next": [], "alike": None, "tell": False}
                                                 for k, v in _rules(host).items()})
    last = event_order.STATE["last"]
    press = []

    async def press_found(cand, res, stamp, skipped):
        press.append((cand["cue"], cand.get("extra"), skipped))
        follow.STATE["spent"], follow.STATE["utt"] = stamp, {}

    monkeypatch.setattr(follow, "_press", press_found)
    assert named.said("Where is the gym?", gap=60) == [] and not press                      # no name
    named.said("Emma, where is the gym?", gap=60)
    named.said("Emma, how much is a studio?", gap=60)                                        # no line may answer that
    named.said("Emma, and what is there for the children?", gap=60)
    named.said("Emma, where is our project located?", gap=60)                                # the script's own first
    assert [p[0] for p in press] == ["x_fitness_where", "x_for_families", "s03_l03"]
    assert press[0][1:] == (True, []) and event_order.STATE["last"] == last


def test_the_press_is_the_cues_own_with_the_storys_picture(monkeypatch):
    played = []

    async def play(cue, by=None, show=None):
        played.append((cue, by, show))
        return {"ok": True}

    async def press(n, i):
        raise AssertionError("an extra is no step of the order")

    @contextlib.asynccontextmanager
    async def script_press(why="script"):
        yield True

    monkeypatch.setattr(event_cues, "play", play)
    monkeypatch.setattr(event_order, "press", press)
    monkeypatch.setattr(ceremony, "script_press", script_press)
    follow.STATE["utt"] = {5.0: "x"}
    res = asyncio.run(follow._play({"n": None, "i": None, "cue": "x_fitness_where", "extra": True}, {"cover": 1.0}, "host"))
    assert res["ok"] and played == [("x_fitness_where", "staff", "fitness_where")]
    assert follow.STATE["last_fire"]["cue"] == "x_fitness_where" and follow.STATE["utt"] == {}
    follow.reset()
    assert SimpleNamespace and ORDER


def test_robot_env_turns_it_on():
    env = (ROOT / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "\nCEREMONY_EXTRA_LINES=true" in env
