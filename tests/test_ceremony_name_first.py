"""Her name first (CEREMONY_NAME_FIRST, owner 24 Nov 2026 ~17:40).

"ให้ทั้งหมดที่หุ่นจะตอบก็ต่อเมื่อมีคำว่า emma ไม่ว่าจะถามไปแล้วหรือยังไม่ถาม ... เขาพูดชื่อ emma แล้วยังพูดก็ยังไม่ตอบรอจนกว่าเขาจะพูดเสร็จ ...
ให้หุ่นมองหาคีย์เวิร์ด ... คำไม่ตรงกับสคริป์ แต่ความหมายเหมือนกันก็ได้"

* off (the default) = nothing of it: the ceremony as it was;
* a line plays / she answers only when he CALLS her -- not when he talks about her, not for a question with no name;
* the call holds while he goes on talking, and nothing is decided while his voice is in the room;
* every time, the lights and the curtains and the answers to her own questions too (owner ~18:10);
* what he means plays the line whatever his words; off the script = a live answer, only for a question, once.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from pathlib import Path as _P

ROOT = _P(__file__).resolve().parents[1]

from app import ceremony, ceremony_call as call_, ceremony_follow as follow, event_cues, event_order
from app.config import settings
from tests.test_ceremony_follow import fol, real_lines  # noqa: F401 -- fixtures
from tests.test_ceremony_follow import F1, F3, HOST_FILE, begin, hear, played
from tests.test_event_order import call, calls, event, rows  # noqa: F401 -- fixtures

ORDER = ["s02_l03", "s02_l09", "s03_l03", "s04_l03", "s04_l06", "s04_l09", "s04_l15", "s05_l03", "s05_l06", "s05_l11",
         "s06_l03", "s06_l05", "s07_l02", "s07_l07", "s08_l06", "s08_l10", "s08_l14", "s09_l02"]


# ---- the name -----------------------------------------------------------------------------

@pytest.mark.parametrize("said", [
    "Emma, how can you help us?", "Hello Emma, welcome. I think everyone is curious.", "So Emma what makes our project different",
    "where is our project located, Emma?", "Wow you remember the details very well Emma", "okay emma turn off the lights",
    "And what about the curtains Emma? Please open them", "Emma can you print the quotation", "Where is it Emma?",
    "Emma is there anything you want to show us", "and Emma, where is it", "Emma will you play the video",
    "Ema where is the project", "Imma, how many units", "Welcome Emma. Please explain how you can help.",
    # her name with its head lost (rehearsal 24 Nov)
    ", how can you help us?", "Ma, please play the story video.", "Hey Ma, where is our project located",
    "Emmett, where is the project located", "Emma's, where is our project?", "MR, how many units", "Mama, open the curtains"])
def test_he_calls_her(said):
    assert call_.called(said), said


@pytest.mark.parametrize("said", [
    "As Emma said, we have a thousand units.", "Later Emma will play the technology video for us.",
    "Emma just told us how our project is different.", "That was how Emma can help us.", "Let's ask Emma to play it later",
    "Please welcome Emma.", "Emma and I will show you the facilities", "Emma's answer was perfect",
    "With Emma we can help the sales team", "Emma knows where the project is", "Emma has told you the location",
    "You can ask Emma anything", "Emma's answers are very good", "Mama is here tonight", "how many units do we have", "This is Emma, our smart assistant.", "", ", right?"])
def test_he_talks_about_her_or_not_to_her(said):
    assert not call_.called(said), said


def test_a_sentence_left_in_the_middle_is_not_asked_yet():
    for said in ("I would like to know", "Emma, can you tell us about the", "Emma, please"):
        assert call_.unfinished(said), said
    for said in ("Emma, where is it?", "Emma, turn the lights on.", "Emma, how many units do we have", "Emma, lights on please."):
        assert not call_.unfinished(said), said


def test_asking_is_not_telling():
    for said in ("Where is our project located", "Emma, please print the quotation.", "Emma, I'd love a sea view",
                 "Okay, what about our facilities.", "Emma, let's see the story video"):
        assert call_.is_request(said), said
    for said in ("okay Emma lights on in the lobby please", "Emma, any facility you recommend to everyone"):
        assert call_.is_request(said), said
    for said in ("Please be careful, do not touch Emma", "Emma, today we have many guests with us.", "Thank you, Emma.", "So now you know how many units we have."):
        assert not call_.is_request(said), said


# ---- what he means ------------------------------------------------------------------------

RULES_OF = lambda rows_, nxt=None: {c: {"means": r.get("means") or [], "not": r.get("means_not") or [],     # noqa: E731
                                        "not_all": r.get("means_not_all") or [],
                                        "tell": r.get("tell"),
                                        "next": (r.get("means_next") or []) if c == nxt else [], "alike": r.get("alike")}
                                    for c, r in rows_.items() if r["kind"] == "host"}


@pytest.fixture(scope="module")
def host_rows():
    return json.loads(HOST_FILE.read_text(encoding="utf-8"))["lines"]


@pytest.mark.parametrize("said,cue", [
    # what the recogniser made of it, rehearsal 24 Nov
    ("What's next are project different?", "s02_l09"), ("Emma and all project different.", "s02_l09"),
    ("Watch make or protect different.", "s02_l09"), ("RHR project locate.", "s03_l03"),
    # the same thing in other words
    ("Emma, what are you able to do for our guests?", "s02_l03"), ("Emma, please introduce yourself.", "s02_l03"),
    ("Emma, why should people choose Embassy World?", "s02_l09"), ("Emma, how far is it from the beach?", "s03_l03"),
    ("Emma, how many rooms do we have altogether?", "s04_l03"), ("Emma, tell us about the amenities.", "s04_l06"),
    ("Emma, you have such a good memory!", "s04_l09"), ("No swimming today Emma, you have to help us first.", "s04_l15"),
    ("Emma, I am a buyer with a wife and two kids, which unit should I take?", "s05_l03"),
    ("I prefer an ocean view, Emma.", "s05_l06"), ("Emma, please prepare a quote for me.", "s05_l11"),
    ("Emma, how tall is the building?", "s06_l03"), ("You are so clever, Emma.", "s06_l05"),
    ("Emma, show us the film about the technology.", "s07_l02"), ("Emma, what are we looking at on this screen?", "s07_l07"),
    ("Emma, switch off the lights.", "s08_l06"), ("Emma, lights on please.", "s08_l10"), ("Emma, draw the curtains open.", "s08_l14"),
    ("Emma, we would like to watch the story film.", "s09_l02")])
def test_other_words_same_question(host_rows, said, cue):
    res = call_.by_meaning(said, RULES_OF(host_rows))
    assert res is not None and res["cue"] == cue, res


@pytest.mark.parametrize("said", [
    "Emma, how much is a unit?", "Emma, when will the project be completed?", "Emma, how many people are here tonight?",
    "Emma, close the curtain.", "Emma, where is the toilet?", "Emma, what time is it?", "Emma, tell me a story.",
    "Emma, can you sing a song?", "Emma, how do you do?", "Emma, where is the parking?", "Emma, do you remember my name?",
    "Emma, how many rooms are left?", "Thank you, Smart Emma.", "Emma, what is the weather like today?",
    "Emma, stop the video.", "Emma, how old are you?", "Emma, who is the developer?", "Emma, do you have a swimming pool at home?",
    "Emma, how do I get a quotation?", "Emma, why are you so smart?"])
def test_another_question_is_no_line_of_the_script(host_rows, said):
    assert call_.by_meaning(said, RULES_OF(host_rows)) is None, said


def test_a_customers_question_is_not_question_1(host_rows):
    for said in ("Emma, I am a buyer with a wife and two kids. How would you help me?",
                 "Emma, I am a customer. How can you help?"):
        assert call_.by_meaning(said, RULES_OF(host_rows))["cue"] == "s05_l03", said
    assert call_.by_meaning("We have seen the rooms for families. Emma, how can you help us?", RULES_OF(host_rows))["cue"] == "s02_l03"


@pytest.mark.parametrize("where", ["start", "its_turn"])
def test_a_buyer_with_a_family_then_how_would_you_help_plays_question_8_not_1(named, where):
    """Scene check 25 Nov (a paraphrase set written blind to the patterns): the question-only try on his LAST sentence
    ("How would you help me, Emma?") dropped the words that rule question 1 out, and question 1 played -- from the start
    and at question 8's own turn. What rules a question out is read in all he said."""
    if where == "its_turn":
        for cue in ORDER[:7]:
            named.marks[event_order._key(*named.where[cue])] = {"state": "done", "at": 0}
        event_order.STATE["last"] = named.where["s04_l15"]
    said = "Okay, so pretend I'm a buyer with my wife and kids. How would you help me, Emma?"
    assert named.said(said, gap=40) == ["s05_l03"]


@pytest.mark.parametrize("said", ["Emma, how long is the technology video?", "Emma, how long is the story video?",
                                  "Emma, how many rooms did you see in the show unit earlier?"])
def test_near_a_line_but_asking_something_else_plays_nothing(named, said):
    """Scene check 25 Nov (blind set): these played the video line / the unit count."""
    assert named.said(said, gap=40) == []


def test_the_lines_next_to_those_exclusions_still_play(named):
    assert named.said("Emma, could you play the technology video?", gap=40) == ["s07_l02"]
    assert named.said("Emma, please play the Embassy World story video.", gap=40) == ["s09_l02"]
    assert named.said("Emma, how many units does our project have?", gap=40) == ["s04_l03"]


def test_question_1_alone_is_still_question_1(named):
    assert named.said("Emma, how would you help us?", gap=40) == ["s02_l03"]
    assert named.said("We have seen the rooms for families. Emma, how can you help us?", gap=40) == ["s02_l03"]


def test_the_question_at_the_end_of_a_long_talk_counts_not_what_he_said_before(host_rows):
    long = ("We have already seen the facilities and the lights, and the story video will come later in the evening. "
            "Emma, where is our project located?")
    assert call_.by_meaning(long, RULES_OF(host_rows))["cue"] == "s03_l03"


def test_a_bare_answer_counts_only_for_the_line_that_comes_next(host_rows):
    assert call_.by_meaning("Yes please.", RULES_OF(host_rows)) is None
    assert call_.by_meaning("Yes please.", RULES_OF(host_rows, "s05_l11"))["cue"] == "s05_l11"
    assert call_.by_meaning("That is amazing, Emma.", RULES_OF(host_rows)) is None
    assert call_.by_meaning("That is amazing, Emma.", RULES_OF(host_rows, "s04_l09"))["cue"] == "s04_l09"
    # ...and praise is not a question
    assert call_.by_meaning("Wow Emma, how much is that?", RULES_OF(host_rows, "s04_l09")) is None


def test_every_pattern_compiles_and_every_question_has_some(host_rows):
    import re

    for cue in ORDER:
        row = host_rows[cue]
        assert row["means"], cue
        for p in row["means"] + (row.get("means_not") or []) + (row.get("means_next") or []):
            re.compile(p)
        # his own scripted line still means its own cue (or nothing -- the line match has it)
        res = call_.by_meaning(row["host_en"], RULES_OF(host_rows, cue))
        assert res is None or res["cue"] == cue, (cue, res)


# ---- the decision -------------------------------------------------------------------------

@pytest.fixture
def named(real_lines, monkeypatch):                                                  # noqa: F811
    """The real order and lines, the decision alone: `said(...)` in, the lines it pressed out."""
    for key, value in (("ceremony_english_only", True), ("ceremony_follow_host", True), ("ceremony_follow_answer_all", True),
                       ("ceremony_script_show_only", True), ("ceremony_answer_named", True), ("ceremony_name_first", True)):
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setitem(follow.STATE, "on", True)
    monkeypatch.setitem(follow.STATE, "paused", None)
    monkeypatch.setattr(follow, "_recorded", lambda cue: False)
    monkeypatch.setattr(follow, "_gate_why", lambda recorded=False: None)
    monkeypatch.setattr(follow, "OFF_SCRIPT_WAIT_S", 0.0)
    monkeypatch.setattr(follow, "_to_logo", lambda why: out.logos.append(why))
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    monkeypatch.setitem(event_cues.STATE, "said_log", [])
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    monkeypatch.setitem(event_order.STATE, "marks", marks)
    monkeypatch.setitem(event_order.STATE, "last", (1, 2))
    monkeypatch.setitem(event_order.STATE, "chain", None)
    where = {d["cue"]: (d["n"], d["i"]) for d in follow._all_host_cues()}
    out = SimpleNamespace(pressed=[], logos=[], now=1000.0, talking=False)
    monkeypatch.setattr(follow, "_host_talking", lambda: out.talking)
    monkeypatch.setattr(event_order, "_settle", lambda scenes: None)

    async def play(cand, res, by, skipped=None):
        out.pressed.append(cand["cue"])
        follow.STATE["spent"], follow.STATE["utt"] = max(follow.STATE["utt"], default=follow.STATE["spent"]), {}
        marks[event_order._key(*where[cand["cue"]])] = {"state": "done", "at": time.time()}
        for sk in skipped or []:
            marks[event_order._key(sk["n"], sk["i"])] = {"state": "skipped"}
        flat = event_order._flat(event_order._scenes())
        if flat.index(where[cand["cue"]]) > flat.index(tuple(event_order.STATE["last"])):
            event_order.STATE["last"] = where[cand["cue"]]
        event_cues.STATE["said_log"] = [{"id": cand["cue"], "at": time.monotonic()}]
        return {"ok": True}

    monkeypatch.setattr(follow, "_play", play)

    def said(*parts, gap=3.0):
        """He says these, `gap` seconds apart; the decision is taken after each. Returns what was pressed."""
        before = len(out.pressed)
        for text in parts:
            out.now += gap
            follow.STATE["utt"][out.now] = text
            follow.STATE["dropped"] = False
            follow._note_call(out.now, text, out.now + 0.5)
            asyncio.run(follow._decide_inner())
        return out.pressed[before:]

    out.said = said
    out.marks = marks
    out.where = where
    return out


def test_a_question_without_her_name_plays_nothing_and_is_not_answered(named):
    for text in ("How can you help us?", "Where is our project located?", "How many units do we have?", "How much is a unit?"):
        assert named.said(text, gap=40.0) == [], text
        assert follow.STATE["may_answer"] is False and follow.STATE["last_eval"]["verdict"] == "not_called"
    assert named.logos == []                                         # the screen stays as it is while he talks to the guests


def test_with_her_name_the_line_plays_first_time_out_of_order_and_again(named):
    assert named.said("Emma, how can you help us?") == ["s02_l03"]
    assert named.said("Emma, how many rooms do we have altogether?", gap=40) == ["s04_l03"]       # out of order, other words
    assert named.said("Emma, what's make our project difference?", gap=40) == ["s02_l09"]          # back
    assert named.said("Emma, how can you help us?", gap=40) == ["s02_l03"]                          # again
    assert named.said("Emma just told us how she can help us.", gap=40) == []                       # about her
    assert named.said("Emma, please play the story film.", gap=40) == ["s09_l02"]                   # far ahead


def test_her_name_then_a_long_talk_then_the_question(named):
    assert named.said("Emma, good evening.", "Today we have many guests with us from many places.",
                      "They all came a long way to be here.", "Where is our project located?", gap=8) == ["s03_l03"]
    # the call is used up: the next question is asked with her name again
    assert named.said("How many units do we have?", gap=20) == []


def test_the_call_ends_with_a_long_silence_and_after_a_minute(named):
    assert named.said("Emma, good evening.") == []
    assert named.said("Where is our project located?", gap=follow.CALL_GAP_S + 5) == []
    assert named.said("Emma, good evening.", *["We are very happy tonight."] * 8, "Where is our project located?", gap=8) == []


def test_telling_is_listened_to_and_a_question_off_the_script_is_answered_once(named):
    assert named.said("Emma, today we have many guests with us.") == []
    assert follow.STATE["may_answer"] is False and named.logos == []          # he is only telling: she listens
    assert named.said("What time is it now?") == []
    assert follow.STATE["may_answer"] is True and named.logos == ["off_script"]
    assert named.said("And how much is a unit?", gap=6) == []                 # no name: the call was used up
    assert follow.STATE["may_answer"] is False
    assert named.said("Emma, how much is a unit?", gap=20) == [] and follow.STATE["may_answer"] is True


def test_what_he_told_the_guests_before_her_name_is_not_his_question(named):
    assert named.said("Where is our project located? We will see it in a moment.") == []
    assert named.said("Emma, good evening, we are happy to have you.", gap=6) == []
    assert named.said("How many units do we have?", gap=90) == []
    assert named.said("Emma?", gap=4) == ["s04_l03"]                 # asked, then her name alone: that is the call for it
    assert named.said("Where is our project located?", gap=90) == []
    assert named.said("Emma?", gap=20) == []                          # long after it


def test_a_question_said_in_three_pieces(named):
    assert named.said("Emma, one more thing", "I would like to know", "how many units in total", gap=4) == ["s04_l03"]
    assert named.said("Emma, where", "is our lovely", "project", gap=60 / 12) == ["s03_l03"]
    named.talking = True
    assert named.said("Emma", "turn the lobby lights", gap=4) == []
    named.talking = False
    assert named.said("back on please", gap=4) == ["s08_l10"]
    # what "them" is was said just before her name
    assert named.said("So first, the lobby lights.", "Emma, please turn them off.", gap=5) == ["s08_l06"]
    assert named.said("Now Emma, pretend I am a customer, I have a big family, how can you help me choose", gap=60) == ["s05_l03"]
    assert named.said("Emma, tell us what are your abilities and how you can support our team", gap=60) == ["s02_l03"]


def test_leave_to_answer_runs_out(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_name_first", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    follow.STATE.update(may_answer=True, may_at=time.monotonic())
    assert follow.may_answer("x") is True
    follow.STATE["may_at"] = time.monotonic() - follow.MAY_ANSWER_S - 1
    assert follow.may_answer("x") is False
    follow.reset()


def test_a_decided_line_waits_for_his_voice_and_is_not_lost_to_a_noise(named, monkeypatch):
    monkeypatch.setattr(follow, "POLL_S", 0.02)
    monkeypatch.setattr(follow, "HOST_TALK_MAX_S", 0.2)
    named.talking = True                                             # a segment noise holds open
    began = time.monotonic()
    assert named.said("Emma, how can you help us?") == ["s02_l03"]
    assert 0.15 <= time.monotonic() - began < 5

    async def go():
        # he goes on while it waits: his new words are decided, this line is not pressed over them
        named.talking = True
        follow.STATE.update(hist={}, utt={}, spent=0.0, call_spent=0.0)
        follow.STATE["utt"][5000.0] = "Emma, how can you help us?"
        follow._note_call(5000.0, "Emma, how can you help us?", 5000.5)
        before = len(named.pressed)
        task = asyncio.create_task(follow._decide_inner())
        await asyncio.sleep(0.08)
        follow.STATE["utt"][5002.0] = "I mean the sales team"
        await task
        assert len(named.pressed) == before

    asyncio.run(go())


def test_a_new_caption_starts_the_wait_for_his_voice_anew(fol, monkeypatch):               # noqa: F811
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    monkeypatch.setattr(settings, "ceremony_name_first", True)

    async def go():
        await begin()
        follow.STATE["talk_since"] = time.monotonic() - 2.9
        hear("Emma, and one more thing")
        assert follow.STATE["talk_since"] is None

    asyncio.run(go())


def test_new_words_of_his_take_back_the_leave_to_answer_the_old_ones():
    follow.STATE["may_answer"] = True
    follow._note_call(10.0, "And another thing", 10.5)
    assert follow.STATE["may_answer"] is False
    follow.reset()


def test_two_lines_meant_as_much_is_no_line():
    rules = {"a": {"means": ["sea view"]}, "b": {"means": ["view sea", "sea view"]}}
    assert call_.by_meaning("Emma, a sea view", rules) is None
    rules["b"]["means"] = ["a sea view"]
    assert call_.by_meaning("Emma, a sea view", rules)["cue"] == "b"          # the longer of the two


def test_he_goes_on_talking_while_she_would_answer_nothing_is_let_out(named):
    named.talking = True
    assert named.said("Emma, what time is it?") == [] and follow.STATE["may_answer"] is False
    named.talking = False
    assert named.said("I mean, where is our project located?") == ["s03_l03"]


def test_his_voice_is_the_open_segment_of_the_mic(monkeypatch):
    for open_, want in ((True, True), (False, False), (None, False)):
        gate = SimpleNamespace(in_speech=open_)
        monkeypatch.setitem(follow.STATE, "session", SimpleNamespace(provider=SimpleNamespace(_vad_gate=gate)))
        assert follow._host_talking() is want
    monkeypatch.setitem(follow.STATE, "session", None)
    assert follow._host_talking() is False


def test_nothing_is_decided_while_his_voice_is_in_the_room(fol, monkeypatch):              # noqa: F811
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    monkeypatch.setattr(settings, "ceremony_name_first", True)
    talking = [True]
    monkeypatch.setattr(follow, "_host_talking", lambda: talking[0])
    decided = []

    async def decide():
        decided.append(1)

    monkeypatch.setattr(follow, "_decide", decide)
    monkeypatch.setattr(follow, "POLL_S", 0.02)

    async def go():
        follow._quiet()
        await asyncio.sleep(0.1)
        assert not decided and follow.STATE["timer"] is not None
        talking[0] = False
        await asyncio.sleep(0.1)
        assert decided == [1]
        # a segment that never closes (room noise) is not waited for for ever
        monkeypatch.setattr(follow, "HOST_TALK_MAX_S", 0.05)
        talking[0] = True
        follow._quiet()
        await asyncio.sleep(0.2)
        assert decided == [1, 1]
        follow._quiet if follow.STATE["timer"] is None else follow.STATE["timer"].cancel()

    asyncio.run(go())


def test_her_name_every_time_the_lights_the_curtains_and_her_own_questions_too(named):
    assert named.said("Emma, I am a customer looking for a room for my family. How would you help?") == ["s05_l03"]
    assert named.said("I would like a room with a sea view.", gap=6) == []
    assert named.said("Emma, I would like a room with a sea view.", gap=6) == ["s05_l06"]
    assert named.said("Emma, yes please.", gap=20) == ["s05_l11"]
    for said, cue in (("turn off the lobby lights", "s08_l06"), ("turn the lobby lights back on", "s08_l10"),
                      ("open the lobby curtains", "s08_l14")):
        assert named.said("Please " + said + ".", gap=90) == [], said
        assert named.said("Emma, please " + said + ".", gap=90) == [cue], said


def test_an_answer_window_after_her_question_if_it_is_ever_opened_is_that_one_line_only(named, monkeypatch):
    monkeypatch.setattr(follow, "REPLY_TO_HER_S", 30.0)
    assert named.said("Emma, I am a customer looking for a room for my family. How would you help?") == ["s05_l03"]
    # ("what kind of view do you like? ...") -- his script line has no name in it
    assert named.said("I would like a room with a sea view.", gap=6) == ["s05_l06"]
    event_cues.STATE["said_log"] = [{"id": "s05_l08", "at": time.monotonic()}]      # "...prepare a quotation for this unit?"
    assert named.said("Yes please.", gap=6) == ["s05_l11"]
    event_cues.STATE["said_log"] = [{"id": "s05_l08", "at": time.monotonic()}]
    assert named.said("Where is our project located?", gap=6) == []                   # not an answer to her
    event_cues.STATE["said_log"] = [{"id": "s05_l06", "at": time.monotonic()}]      # (a line that asks him nothing)
    assert named.said("Yes please.", gap=40) == [] and named.said("Please print the quotation.", gap=40) == []
    event_cues.STATE["said_log"] = [{"id": "s05_l03", "at": time.monotonic() - follow.REPLY_TO_HER_S - 1}]
    assert named.said("I would like a room with a sea view.", gap=40) == []           # too long ago
    monkeypatch.setattr(follow, "REPLY_TO_HER_S", 0.0)                                # the strict setting: her name, always
    event_cues.STATE["said_log"] = [{"id": "s05_l03", "at": time.monotonic()}]
    assert named.said("I would like a room with a sea view.", gap=40) == []


def test_the_praise_before_each_joke_is_the_next_jokes(named):
    for cue in ORDER[:5]:
        named.marks[event_order._key(*named.where[cue])] = {"state": "done", "at": 0}
    event_order.STATE["last"] = named.where["s04_l06"]
    assert named.said("Wow Emma, you really know the details.") == ["s04_l09"]         # the second joke's words, the first's turn
    for cue in ORDER[6:11]:
        named.marks[event_order._key(*named.where[cue])] = {"state": "done", "at": 0}
    event_order.STATE["last"] = named.where["s06_l03"]
    assert named.said("That's right Emma. You remember everything very well.", gap=40) == ["s06_l05"]


def test_the_whole_script_in_his_own_lines_with_her_name(named):
    rows_ = follow.lines()
    for cue in ORDER:
        text = rows_[cue]["host_en"]
        text = text if call_.called(text) else "Emma, " + text[0].lower() + text[1:]
        assert named.said(text, gap=40) == [cue], (cue, text)


def test_a_question_cut_in_two_by_a_pause(named):
    named.talking = True                                             # (he only took a breath)
    assert named.said("Emma, can you tell us where", gap=4) == []
    named.talking = False
    assert named.said("our project is located", gap=4) == ["s03_l03"]


def test_telling_the_guests_to_her_is_no_command_and_getting_her_ear_is_no_question(named):
    assert named.said("Emma, I think everyone here wants to see the technology.") == []
    assert named.said("Could you play the technology video?", gap=5) == ["s07_l02"]
    assert named.said("Emma, please listen.", gap=60) == [] and follow.STATE["may_answer"] is False
    assert named.said("What is the height of the building, and how deep is the swimming pool?", gap=5) == ["s06_l03"]
    # a command already done, said short: again; a recap of it, never
    assert named.said("Emma, lights off.", gap=60) == ["s08_l06"] and named.said("Emma, the lights off please.", gap=60) == ["s08_l06"]
    assert named.said("Emma, thank you very much for telling us about the height of the building.", gap=60) == []


def test_off_is_as_it_was(real_lines, monkeypatch):                                   # noqa: F811
    assert settings.ceremony_name_first is False and follow.name_first() is False
    assert follow.may_answer("Emma, hi") is True and follow.may_answer("hi") is False
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert follow.name_first() is False                              # the script shown, the switch off: as it was
    monkeypatch.setattr(settings, "ceremony_script_show_only", False)
    monkeypatch.setattr(settings, "ceremony_name_first", True)
    assert follow.name_first() is False                              # (only with the script shown)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert follow.name_first() is True
    follow.STATE["may_answer"] = False
    assert follow.may_answer("Emma, hi") is False                    # the decision says, not the word


def test_her_live_reply_goes_out_only_when_the_decision_said_so(monkeypatch):
    from app.providers import gemini, transcript_filter as tf

    monkeypatch.setattr(settings, "ceremony_name_first", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    monkeypatch.setattr(settings, "ceremony_answer_named", True)
    monkeypatch.setattr(gemini, "_follow_answers_all", lambda: True)
    silent = gemini.GeminiProvider._ceremony_silence_is_right
    call_stub = SimpleNamespace(_stall_text_turn=False)
    follow.STATE["may_answer"] = False
    assert silent(call_stub, "Emma, today we have many guests.") is True      # told, not asked: silence is right
    follow.STATE.update(may_answer=True, may_at=time.monotonic())
    assert silent(call_stub, "Emma, what time is it now?") is False           # owed an answer: chased
    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: True)
    for ok, want in ((False, "drop"), (True, "pass")):
        follow.STATE.update(may_answer=ok, may_at=time.monotonic())
        gate = tf.NoiseReplyGate(0.06)
        gate.follow_hold = lambda: None
        gate._heard = {"rms": 0.03, "text": "Emma, what time is it now?", "tool": False, "server": False}
        assert gate._verdict(True) == want
    follow.reset()


def test_the_recogniser_of_the_english_ceremony_expects_his_words_not_the_sales_rooms(monkeypatch):
    from app.providers import gemini

    monkeypatch.setattr(settings, "ceremony_english_only", True)
    monkeypatch.setattr(gemini, "_session_profile", lambda: "ceremony")
    words = gemini.adaptation_phrases()
    assert words[0] == "Emma" and "print the quotation" in words and len(words) <= 60
    assert not any("฀" <= c <= "๿" for w in words for c in w), words
    monkeypatch.setattr(gemini, "_session_profile", lambda: "condo")
    assert "เปิดไฟ" in gemini.adaptation_phrases()                    # everything else as it was


def test_robot_env_turns_it_on_and_a_new_call_starts_clean():
    env = (Path(__file__).resolve().parents[1] / "data" / "robot-server" / "robot.env").read_text(encoding="utf-8")
    assert "\nCEREMONY_NAME_FIRST=true" in env.replace("\r\n", "\n")
    follow.STATE["hist"][1.0] = {"t": 1.0, "called": True}
    follow.STATE.update(may_answer=True, call_spent=5.0)
    follow.reset()
    assert follow.STATE["hist"] == {} and follow.STATE["may_answer"] is False and follow.STATE["call_spent"] == 0.0


def test_the_ceremony_is_not_opened_to_a_name_alone(named):
    assert named.said("Emma.") == [] and follow.STATE["may_answer"] is False
    assert named.said("Emma?", gap=40) == []
    assert json and ceremony                                         # (imports used by the fixtures above)
    assert F1 and F3 and begin and hear and played


# ---- the hole of 18:56 (owner: "ทำไมหุ่นถึงตอบไทยได้") ---------------------------------------------------

def _live_gate(monkeypatch, *, name_first=False, may=False):
    from app.providers import transcript_filter as tf

    monkeypatch.setattr(settings, "ceremony_english_only", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    monkeypatch.setattr(settings, "ceremony_answer_named", True)
    monkeypatch.setattr(settings, "ceremony_name_first", name_first)
    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: True)
    follow.STATE.update(may_answer=may, may_at=time.monotonic())
    gate = tf.NoiseReplyGate(0.06)
    gate.follow_hold = lambda: None
    return tf, gate


def test_somebody_speaking_under_a_dropped_reply_is_judged_too(monkeypatch):
    from app.providers.base import ProviderEvent

    tf, gate = _live_gate(monkeypatch)
    audio = ProviderEvent(kind="audio", audio=b"\0" * 480)
    gate.segment(0.05)
    gate.heard("อยู่ล่าง ใช่ ไหม")
    assert gate.push(audio) == []                                    # Thai: dropped...
    gate.segment(0.02)                                               # ...and a second voice opens while it streams
    assert gate.close()[1]["why"] == "thai_ignored"
    gate.heard("ขอ เพลง ที่ เกี่ยว กับ แม่")                        # (its words come after the first reply's turn_complete)
    assert gate.push(audio) == []                                    # robot 24 Nov 18:56:46: this one was played
    assert gate.close()[1]["why"] == "thai_ignored"
    # the same hole, English and no name
    gate.segment(0.05)
    gate.heard("somebody talks to the staff")
    gate.push(audio)
    gate.segment(0.05)
    gate.close()
    gate.heard("and goes on talking to them")
    gate.push(audio)
    assert gate.close()[1]["why"] == "not_named"
    # an ordinary reply still uses up its own words
    gate.segment(0.05)
    gate.heard("Emma, what time is it now?")
    assert gate.push(audio) == [audio] and gate.close() == ([], None) and gate._heard is None
    follow.reset()


def test_a_reply_in_thai_is_never_played_in_the_english_ceremony_and_one_to_nobody_is_not_either(monkeypatch):
    from app.providers.base import ProviderEvent

    tf, gate = _live_gate(monkeypatch, name_first=True, may=True)
    audio = ProviderEvent(kind="audio", audio=b"\0" * 480)
    thai = ProviderEvent(kind="assistant_transcript", text="ขออภัยค่ะ ")
    gate.segment(0.05)
    gate.heard("Emma, what time is it now?")
    assert gate.push(thai) == [] and gate.push(audio) == []
    assert gate.close()[1]["why"] == "thai_reply"
    follow.STATE.update(may_answer=False)
    assert gate._heard is None
    assert gate.push(audio) == []                                    # nobody is known to have said anything
    assert gate.close()[1]["why"] == "not_named"
    follow.STATE.update(may_answer=True, may_at=time.monotonic())
    assert gate.push(audio) == [audio]                               # (the rest of an answer she may give)
    gate.close()
    follow.STATE.update(may_answer=False)
    gate.text_turn()                                                 # a line the server has her read
    assert gate.push(audio) == [audio]
    gate.close()
    assert gate.push(audio) == [audio]                               # ...and what is left of it
    gate.close()
    # any other call is as it was
    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: False)
    gate._server_at = float("-inf")
    assert gate.push(thai) == [thai]
    follow.reset()


# ---- the coverage audit of 24 Nov 21:4x: 397 sentences without her name played nothing; these played wrongly WITH it

@pytest.mark.parametrize("said", [
    "I told Emma to open the curtains yesterday.", "We told Emma to turn off the lights.", "I'll get Emma to turn off the lights in a moment.",
    "We want Emma to open the curtains later.", "Emma here is our robot and she can open the curtains.",
    "Emma, as you can see, is a very smart robot.", "Emma, our new robot, opens the curtains by herself."])
def test_words_about_her_never_call_her_audit(named, said):
    assert call_.called(said) is False
    assert named.said(said, gap=60) == []


@pytest.mark.parametrize("said", [
    "Emma, please turn off the lobby lights.", "Emma, our assistant, can you open the curtains?", "Hey Emma open the curtains please",
    "Emma, as you can see the hall is bright, please turn off the lights."])
def test_words_to_her_still_call_her(said):
    assert call_.called(said) is True


def test_the_hall_is_not_her():
    assert call_.is_request("Thank you, Emma. How many of you came by car?") is False
    assert call_.is_request("Wow, Emma, the lights are off. How many of you like that?") is False
    assert call_.is_request("Emma, how many units do we have?") is True


@pytest.mark.parametrize("said,cue", [
    ("Emma, how can you help the sales team?", "s02_l03"), ("Hello Emma, welcome. What can you do for our sales team?", "s02_l03"),
    ("But today please stay and help the sales team with me first, Emma.", "s04_l15")])
def test_what_she_does_for_the_sales_team_is_question_one(said, cue):
    import json as _json
    raw = _json.loads((ROOT / "data" / "ceremony" / "floor1_ceremony.host.json").read_text(encoding="utf-8"))["lines"]
    rules = {k: {"means": r["means"], "not": r.get("means_not") or [], "not_all": r.get("means_not_all") or [], "tell": r.get("tell")}
             for k, r in raw.items() if r["kind"] == "host"}
    got = call_.by_meaning(said, rules)
    assert got and got["cue"] == cue, got


def test_dim_is_not_off_and_a_buildings_units_are_not_the_total(named):
    assert named.said("Emma, dim the lights a little.", gap=60) == []
    for said in ("Emma, how many units are there in building B?", "Emma, how many one bedroom units do we have?", "Emma, how many units are left?"):
        assert "s04_l03" not in [c["cue"] if isinstance(c, dict) else c for c in named.said(said, gap=60)], said

