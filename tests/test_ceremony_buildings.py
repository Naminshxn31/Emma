"""The owner, 24 Nov: "check the building topics of the two sets do not collide" -- the script's total units (s04_l03) and its
heights (s06_l03) against the extras' units per building, and the places in building A. Each sentence goes through the real
decision (name-first, the script's own lines live, the extras' rules as if signed) and must reach its own line or nobody's."""
import json

import pytest

from app import ceremony_extra as extra, ceremony_follow as follow
from tests.test_ceremony_extra import HOST, _rules
from tests.test_ceremony_follow import real_lines                                      # noqa: F401
from tests.test_ceremony_name_first import named                                       # noqa: F401

TOTAL, EACH, HEIGHT = "s04_l03", "x_units_by_building", "s06_l03"
SAID = [
    ("Let's start with the rooms. How many units does our project have in total, and what types of rooms are there, Emma?", TOTAL),
    ("Emma, how many units are there?", TOTAL), ("Emma, how many apartments are in the project altogether?", TOTAL),
    ("Emma, what room types do you have?", TOTAL),
    ("Emma, how many units are in building A?", EACH), ("Emma, how many rooms does building C have?", EACH),
    ("Emma, how many units in each building?", EACH), ("Emma, how many buildings do we have?", EACH),
    ("Emma, which building has the most units?", EACH),
    ("Emma, how tall is the building?", HEIGHT), ("Emma, how many storeys is the building?", HEIGHT),
    ("Emma, how many floors does the building have?", HEIGHT),
    ("Alright, Emma. I'd like to ask some details. How deep is the swimming pool, how high is the building and how much indoor facility area do we have?", HEIGHT),
    ("Emma, where is the gym in building A?", "x_fitness_where"), ("Emma, what is on the third floor of building A?", "x_facilities_level3"),
    ("Emma, what is in the basement of building A?", "x_facilities_basement"),
    ("Emma, how many one bedroom units are there?", None), ("Emma, how many units are left?", None),
    ("Emma, how much is a unit in building A?", None), ("Emma, is building C sold out?", None),
    ("Emma, how many parking floors are there in the building?", None),
]


@pytest.mark.parametrize("said,cue", SAID)
def test_the_building_topics_do_not_collide(named, monkeypatch, said, cue):             # noqa: F811
    host = json.loads(HOST.read_text(encoding="utf-8"))
    monkeypatch.setattr(extra, "rules", lambda: {k: {**v, "next": [], "alike": None, "tell": False} for k, v in _rules(host).items()})
    pressed = []

    async def press_found(cand, res, stamp, skipped):
        pressed.append(cand["cue"])
        follow.STATE["spent"], follow.STATE["utt"] = stamp, {}

    monkeypatch.setattr(follow, "_press", press_found)
    monkeypatch.setattr(follow, "TAIL_GRACE_S", 0.0)
    named.said(said, gap=100.0)
    assert pressed == ([cue] if cue else []), said
