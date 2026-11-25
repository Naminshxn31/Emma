"""What the hall says about her, over her and around her must not press anything; what is said to her must (the owner, 24 Nov
23:20: "เรื่องเสียงเรื่องคำถาม เสียงคนพูดในงานทดสอบให้หมดเลย"). The cases of that night's audit (924 sentences by two readers + 357
real captions of the day's rehearsals on the clip mic), through the real decision."""
import json

import pytest

from app import ceremony_call as call_, ceremony_extra as extra, ceremony_follow as follow
from tests.test_ceremony_extra import HOST, _rules
from tests.test_ceremony_follow import real_lines                                      # noqa: F401
from tests.test_ceremony_name_first import named                                       # noqa: F401


@pytest.fixture
def hall(named, monkeypatch):                                                          # noqa: F811
    """The decision with the extras on, as if signed: hall.said(...) -> the cues pressed."""
    host = json.loads(HOST.read_text(encoding="utf-8"))
    monkeypatch.setattr(extra, "rules", lambda: {k: {**v, "next": [], "alike": None, "tell": False} for k, v in _rules(host).items()})
    pressed = []

    async def press_found(cand, res, stamp, skipped):
        pressed.append(cand["cue"])
        follow.STATE["spent"], follow.STATE["utt"] = stamp, {}

    monkeypatch.setattr(follow, "_press", press_found)
    monkeypatch.setattr(follow, "TAIL_GRACE_S", 0.0)

    def said(*parts, gap=100.0):
        before = len(pressed)
        named.said(*parts, gap=gap)
        return pressed[before:]

    named.hall = said
    return said


@pytest.mark.parametrize("said", [
    "Everyone look at Emma, and now lights off please, thank you.",
    "I told Emma, turn off the lights. And she did.",
    "My friend said hey Emma turn off the lights, funny.",
    "When I say Emma turn off the lights, the lights go off. But wait for it.",
    "Later I will say, Emma, please turn off the lights. Not yet.",
    "You can say Emma, open the curtains, and she will do it, but not now.",
    "Somebody said Emma, how many units do we have, and she answered.",
    "Can Emma open the curtains?", "Will Emma turn off the lights?", "Does Emma speak Thai?", "Isn't Emma great?",
    "Doesn't Emma look beautiful today?", "Who is Emma?", "Where is Emma?", "Can Emma hear us?",
    "Wow, Emma really knows the details.", "Thank you Emma. Now everyone please sit down.",
    "Emma is our new colleague.", "Please give Emma a hand.", "Look at Emma.", "Open the curtains.", "Lights off please.",
    "How many units do we have?", "Thank you for watching.", "[Music]", "Emma", "เปิดม่านหน่อย", "ปิดไฟหน่อยครับ",
])
def test_the_hall_talking_presses_nothing_and_is_not_answered(hall, said):
    assert hall(said) == [] and follow.STATE["may_answer"] is False, (said, follow.STATE.get("last_eval"))


def test_a_mention_of_her_then_a_command_to_somebody_else(hall):
    assert hall("Look at Emma.") == []
    assert hall("Lights off please.", gap=2.0) == []
    assert hall("I told Emma about it.", gap=60) == []
    assert hall("Open the curtains.", gap=2.0) == []


@pytest.mark.parametrize("said,cue", [
    ("I must say, Emma, you really know the details very well.", "s06_l05"),
    ("I must say Emma, you know the details very well.", "s06_l05"),
    ("As you can see, the lights are bright. Emma, please turn off the lobby lights.", "s08_l06"),
    ("Thank you for coming. Emma, open the curtains please.", "s08_l14"),
    ("Okay everybody, watch this. Emma, can you open the lobby curtains?", "s08_l14"),
    ("Where is our project, Emma?", "s03_l03"), ("Okay Emma do we have a steam room", "x_saunas_where"),
    ("Emma sorry can you say that again where is the sky pool", "x_sky_pool_where"),
    ("Emma lets go back and talk about the location", "s03_l03"),
    ("Okay Emma what facilities are on the basement floor", "x_facilities_basement"),
    ("Emma, what facilities are on level 3?", "x_facilities_level3"),
    ("So Emma, what facilities do you have for kids?", "x_for_families"),
    ("Emma, is there a glass pool?", "x_sky_pool_where"), ("Emma where can I work in the project", "x_coworking_where"),
    ("And what facilities do we have in the project, Emma?", "s04_l06"), ("Emma, what about all facility?", "s04_l06"),
    # the clip mic's own captions of 24 Nov
    ("Close Emma. Customer looking for a room for my family. How was you help?", "s05_l03"),
    ("Emma, please spin the quotation.", "s05_l11"), ("Emma and all project different.", "s02_l09"),
    ("Emma, what room of type?", "s04_l03"), ("free style Emma, what is the height of the building?", "s06_l03"),
    (", how many unit for Embassy World?", "s04_l03"), ("Ma, please play the story video.", "s09_l02"),
    ("Emma, what's my project different?", "s02_l09"), ("Wow, Emma, you remember the detail very well.", "s04_l09"),
    # (no full stop from the recogniser: a command ending on its verb is still whole -- review 24 Nov night)
    ("Emma, please turn the lobby lights off", "s08_l06"), ("Emma please turn the lobby lights back on", "s08_l10"),
])
def test_what_is_said_to_her_still_reaches_her(hall, said, cue):
    assert hall(said) == [cue], (said, follow.STATE.get("last_eval"))


@pytest.mark.parametrize("parts,gap,cue", [
    (("Emma,", "please turn off the lobby lights."), 1.5, "s08_l06"),
    (("Emma", "where is the project located?"), 2.0, "s03_l03"),
    (("Emma, turn off", "the lobby lights."), 1.5, "s08_l06"),
    (("Emma please open", "the lobby curtains."), 2.5, "s08_l14"),
])
def test_a_call_in_two_pieces(hall, parts, gap, cue):
    assert hall(*parts, gap=gap) == [cue], follow.STATE.get("last_eval")


def test_the_chatter_after_an_answer_stays_chatter(hall):
    assert hall("Emma, how many units do we have?") == ["s04_l03"]
    for text in ("So as you heard, one thousand units.", "We have three types of rooms.", "Please enjoy your drinks.", "Open the door please.",
                 "Is everybody happy?", "How many of you came by car?", "Turn it up a little.", "Thank you."):
        assert hall(text, gap=5.0) == [], text


def test_a_finished_command_is_not_waited_for_and_a_bare_verb_is():
    for said in ("Emma, please turn the lights off", "Emma please turn it off", "Emma, lights off", "Emma, start the video"):
        assert not call_.unfinished(said), said
    for said in ("Emma, turn off", "Emma please open", "Emma, please close", "Emma, start"):
        assert call_.unfinished(said), said
    assert call_.called("What should we do Emma?") and call_.called("What do I do Emma?")
    assert not call_.called("Does Emma speak Thai?") and not call_.called("What is Emma?")


@pytest.mark.parametrize("parts,cue", [
    (("Emma, before I ask you, let me tell everyone something.", "Now I want to tell you about the location.",
      "Where we are located is one of the best places in the whole area.", "The airport is also easy to reach.",
      "Where is the sky pool?"), "x_sky_pool_where"),
    (("Emma, before I ask you, let me tell everyone something.", "Our executives are here today with us as well.",
      "I would like to thank all of them.", "And now we will begin the real presentation.", "Is there a gym in the project?"),
     "x_fitness_where"),
    (("Emma, before I ask you, let me tell everyone something.", "Our lights, our curtains and the air conditioning are all smart.",
      "You will see the lobby lights turn off and the curtains open later today.", "Please open the lobby curtains."), "s08_l14"),
    (("Let me tell you a little about our facilities.", "So Emma,", "what facilities are", "on the third floor?"),
     "x_facilities_level3"),
    (("We have a big pool on the third floor.", "Emma, how many", "units do we have", "in total?"), "s04_l03"),
])
def test_a_long_talk_then_the_question(hall, parts, cue):
    got = []
    for k, part in enumerate(parts):
        got += [(k, c) for c in hall(part, gap=2.0)]
    assert [c for _, c in got] == [cue] and got[0][0] >= len(parts) - 2, got      # (never mid-talk)


def test_an_opener_alone_waits_for_the_rest(hall):
    for said in ("Emma, do we have", "Emma, can you tell me", "So Emma, what facilities are"):
        assert call_.unfinished(said) or call_.dangling(said), said
    assert hall("Emma, do we have", gap=60) == [] and follow.STATE["may_answer"] is False
    assert hall("a steam room?", gap=2.0) == ["x_saunas_where"]
    assert not call_.dangling("Emma, how many units do we have") and not call_.unfinished("Emma, how many units do we have")
    assert not call_.dangling("Emma how can you help us")


def test_names_like_hers_that_are_not_hers():
    for name in ("Emily", "Anna", "Amber", "Alma", "Elmer", "Them", "Dilemma"):
        assert not call_.called("%s, how many units are there?" % name), name
    for name in ("Emma", "Ema", "Emmer", "Emmar", "Emmi", "Amma", "Em ma"):
        assert call_.called("%s, how many units are there?" % name), name
