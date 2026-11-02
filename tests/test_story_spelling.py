"""A loanword the transcriber spells another way still reaches its story.

Robot 27 Oct: "ใกล้ซุปเปอร์มาร์เก็ตไหม" was asked three times and handed to
sales three times -- the story lists "ใกล้ซูเปอร์มาร์เก็ตไหม", and to the
tokenizer "ซุป" and "ซู" are different words (`stories._respell`)."""
from __future__ import annotations

import json
from pathlib import Path

from app import stories

ROOT = Path(__file__).resolve().parents[1]


def _offered():
    catalog = json.loads((ROOT / "data/projects/embassy_world/stories/stories.json").read_text(encoding="utf-8"))
    return {s["id"]: s for s in catalog["stories"]}


def test_the_transcribers_spelling_of_a_loanword_reaches_its_story():
    offered = _offered()
    for query in ("ใกล้ซุปเปอร์มาร์เก็ตไหม", "ซุปเปอร์มาร์เก็ตไหม", "ใกล้ซูเปอร์มาเก็ตไหม",
                  "ใกล้ซุบเปอร์มาร์เก็ตไหม"):
        assert stories.story_for_query(query, offered) == "nearby_places", query
    # the search the model sent is the guest's own words: the redirect too
    assert stories.story_for_guest("ใกล้ซุปเปอร์มาร์เก็ตไหม", "ใกล้ซุปเปอร์มาร์เก็ตไหม", offered) == "nearby_places"


def test_one_spelling_on_both_sides():
    assert stories._respell("ซุปเปอร์มาร์เก็ต") == "ซูเปอร์มาร์เก็ต"
    assert stories._respell("ซูเปอร์มาร์เก็ต") == "ซูเปอร์มาร์เก็ต"
    assert stories._respell("ซุปเปอมาเก็ท") == "ซูเปอร์มาร์เก็ต"
    assert stories._respell("ซุปเปอรมาร์เก็ต") == "ซูเปอร์มาร์เก็ต", "no leftover ร without its ์"
    assert stories._respell("ซุปไก่") == "ซุปไก่", "a soup is not a supermarket"
