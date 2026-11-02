"""The offline router (app/offline/router.py): the content gate the real guest
table asked for, the cascade, and what send_text's instructions mean offline.
The story catalog is a small fake built so the unguarded matcher reproduces
the measured false fires -- each gate test first proves the raw matcher
fires, so a green gate test cannot be a matcher that simply missed."""
from __future__ import annotations

import pytest

from app import stories
from app.config import settings
from app.offline import router

FAKE = {
    "nearby_hospitals": {"id": "nearby_hospitals", "questions_th": [
        "ใกล้โรงพยาบาลไหม", "โรงพยาบาลไม่ไกลใช่ไหม", "มีโรงพยาบาลไหม"],
        "questions_en": ["Is there a hospital nearby?"]},
    "nearby_7eleven": {"id": "nearby_7eleven", "questions_th": ["มีเซเว่นไหม", "มันมีร้านสะดวกซื้อไหม"],
                       "questions_en": ["Is there a 7-Eleven nearby?", "Is 7 Eleven close?"]},
    "fitness_where": {"id": "fitness_where", "questions_th": ["ฟิตเนสอยู่ชั้นไหน", "มีฟิตเนสไหม"],
                      "questions_en": ["Where is the gym?"]},
    "parking": {"id": "parking", "questions_th": ["ที่จอดรถมีกี่คัน", "มีที่จอดรถไหม"],
                "questions_en": ["How many parking spaces?"]},
    "location_beach": {"id": "location_beach", "questions_th": ["ห่างจากทะเลเท่าไหร่", "ใกล้หาดไหม"],
                       "questions_en": ["How far is the beach?"]},
}


@pytest.fixture(autouse=True)
def fake_catalog(monkeypatch):
    monkeypatch.setattr(stories, "available", lambda catalog=None: FAKE)
    monkeypatch.setattr(settings, "stories_enabled", True)
    router.reset()


def route(text, lang="th", state=None, **kw):
    return router.route(text, lang, state or router.RouterState(), now=kw.pop("now", 0.0), **kw)


@pytest.mark.parametrize("noise", ["ไม่", "ไม่มีนะ", "มัน", "7 7"])
def test_the_measured_false_fires_fire_raw_and_not_through_the_gate(noise):
    assert stories.story_for_query(noise, FAKE) is not None      # the bug is real here
    assert router.story_for(noise)[0] is None
    assert route(noise).action == "silent"


def test_a_real_question_still_gets_its_story():
    assert route("Where is the gym?", "en") == router.Route("story", topic="fitness_where", why="story")
    assert route("ที่จอดรถมีกี่คันคะ").topic == "parking"


def test_one_bare_word_is_a_story_only_as_an_answer():
    assert stories.story_for_query("เซเว่น", FAKE) == "nearby_7eleven"
    assert route("เซเว่น").action == "silent"
    assert route("เซเว่น", expecting=True).topic == "nearby_7eleven"


def test_no_story_when_the_session_does_not_offer_them():
    assert route("Where is the gym?", "en", stories_on=False).action != "story"
    # and no menu either: it names the stories' topics, a promise without them
    assert route("Where is the gym?", "en", stories_on=False).line == "handoff_other"


def test_prices_and_units_go_to_the_sales_team():
    assert route("ราคาเริ่มต้นเท่าไหร่คะ").line == "handoff_commercial"
    assert route("What is the cheapest one bedroom?", "en").line == "handoff_commercial"
    assert route("ห้อง A-801 ยังว่างไหมคะ").line == "handoff_commercial"
    assert route("Which units are still available?", "en").line == "handoff_commercial"


def test_price_before_story():
    """A price question that names a story topic is still a price question."""
    assert route("ที่จอดรถราคาเท่าไหร่คะ").line == "handoff_commercial"


def test_greeting_once_then_her_project_question():
    """Review 26 Oct: a guest greeting her back after the camera greeting got
    the thank-you line ("My pleasure...") -- now her one project question."""
    state = router.RouterState()
    assert route("สวัสดีค่ะ", state=state).line == "offline_greeting"
    assert route("Hello Emma.", "en", state=state, now=40.0).line == "invite"
    state = router.RouterState(greeted=True)          # the camera's greeting played
    for text, lang in (("สวัสดีค่ะ", "th"), ("Hello", "en"), ("Hi Emma", "en"), ("good morning", "en")):
        assert route(text, lang, state=router.RouterState(greeted=True)).line == "invite", text
    assert route("ขอบคุณค่ะ", state=router.RouterState()).line == "ack"


def test_off_topic_is_redirected_not_answered():
    assert route("อากาศวันนี้เป็นยังไงคะ").line == "off_topic"
    assert route("Can you sing a song for me?", "en").line == "off_topic"


def test_a_project_question_without_a_story_gets_the_menu_then_the_hand_off():
    state = router.RouterState()
    assert route("แบบห้องมีกี่แบบคะ", state=state).line == "menu"
    assert route("ห้องนอนมีหน้าต่างไหมคะ", state=state, now=40.0).line == "handoff_other"
    # the menu once per language
    assert route("What room types do you have?", "en", state=state, now=80.0).line == "menu"


def test_people_talking_to_each_other_get_silence():
    # the same question without the slang is answered: the slang is the reason
    assert route("ห้องนี้มีอะไรบ้าง", stories_on=False).line == "handoff_other"
    for text in ("เฮ้ย ห้องนี้มีอะไรบ้าง", "อันนี้ห้องอะไร", "ตึกนี้สูงกี่ชั้นวะ"):
        assert route(text, stories_on=False) == router.Route("silent", why="human_talk"), text


def test_a_long_monologue_with_a_question_word_is_silence():
    text = "ห้อง" + "ก็คือว่าเราจะต้องทำแบบนี้ไปเรื่อยๆจนกว่าจะเสร็จ" * 4 + "ไหม"
    assert route(text).action == "silent"


def test_the_same_line_twice_in_a_row_is_not_said_twice():
    state = router.RouterState()
    assert route("ราคาเท่าไหร่คะ", state=state, now=0.0).line == "handoff_commercial"
    assert route("ราคาเท่าไหร่คะ", state=state, now=5.0).action == "silent"
    assert route("ราคาเท่าไหร่คะ", state=state, now=60.0).line == "handoff_commercial"


def test_english_spoken_to_her_gets_the_menu_without_a_project_word():
    assert route("Can you show me more pictures?", "en").line == "menu"


def test_the_thai_recognizers_mai_is_a_question():
    """zipformer-thai writes the question particle ไหม as ใหม่ at the end."""
    assert router.asks("มีห้องสมุดใหม่")
    assert not router.asks("ห้องสมุด")


def test_a_language_request_gets_the_menu_in_it_once():
    state = router.RouterState()
    assert router.route_language_request("en", state, now=0.0).line == "menu"
    assert router.route_language_request("en", state, now=40.0).line == "invite"
    # without stories the menu would promise topics: the question instead
    assert router.route_language_request("th", router.RouterState(), now=0.0,
                                         stories_on=False).line == "invite"


#: The review's questions (26 Oct, reproduced): none is about money, every
#: one went to the line that opened "For prices and the units still
#: available..." -- Emma raising price herself (owner 8 Oct: never).
NOT_PRICE = [("ฟิตเนสเปิดกี่โมงคะ", "th"), ("What time does the gym open?", "en"),
             ("ห้องขายเปิดกี่โมงคะ", "th"), ("โครงการสร้างเสร็จเมื่อไหร่คะ", "th"),
             ("มีห้องประชุมให้จองไหมคะ", "th"), ("Can I book the meeting room?", "en"),
             ("ห้องไหนวิวทะเลสวยที่สุดคะ", "th")]


@pytest.mark.parametrize("text,lang", NOT_PRICE)
def test_a_question_that_is_not_about_money_never_hears_about_price(text, lang):
    import json
    from pathlib import Path

    got = route(text, lang)
    assert got.action == "line" and got.line in ("handoff_commercial", "handoff_other"), (text, got)
    shipped = json.loads((Path(__file__).resolve().parents[1] / "data" / "projects" / "embassy_world"
                          / "offline" / "lines.json").read_text(encoding="utf-8"))
    said = next(ln for ln in shipped["lines"] if ln["id"] == got.line)
    for words in (said["th"], said["en"]):
        assert "ราคา" not in words and "price" not in words.lower(), (text, words)


# ---- stop --------------------------------------------------------------------

@pytest.mark.parametrize("text", ["หยุด", "หยุดก่อน", "stop", "Emma stop", "พอแล้ว", "เอ็มม่า หยุด"])
def test_a_stop_on_its_own_is_a_stop(text):
    """Review 26 Oct: offline had no voice stop -- these were silence, and
    "Emma stop" after her question was the 15-second menu."""
    assert router.stop_route(text, moving=False) == router.Route("stop", why="stop")
    assert router.stop_route(text, moving=True) == router.Route("stop", why="stop_moving")


def test_while_moving_any_stop_word_stops_and_standing_still_a_question_stays_one():
    assert router.stop_route("หยุดเดิน", moving=True).action == "stop"
    assert router.stop_route("stop walking please", moving=True).action == "stop"
    assert router.stop_route("wait, what about the gym?", moving=False) is None
    assert router.stop_route("ฟิตเนสอยู่ชั้นไหนคะ", moving=True) is None
    # whole words: "stopwatch" is not a stop, "ยกเลิก" is
    assert router.stop_route("stopwatch", moving=True) is None
    assert router.stop_route("ยกเลิก", moving=False).action == "stop"


# ---- send_text -> what it means offline -----------------------------------------

def test_a_camera_greeting_is_recognised():
    from types import SimpleNamespace

    from app import greeter

    stranger = SimpleNamespace(kind="stranger", name=None, group=None)
    assert router.classify_injected(greeter.greeting_for(stranger)).kind == "greeting"
    known = SimpleNamespace(kind="known", name="แก้ว", group="staff")
    assert router.classify_injected(greeter.greeting_for(known)).kind == "greeting"


def test_story_taps_in_every_language_are_recognised():
    tap = {"id": "story_x_more", "label_th": "ที่จอดรถ", "label_en": "Parking", "next_story": "parking"}
    for talk in (None, "th", "en", "zh"):
        got = router.classify_injected(stories.tap_text(tap, "th", talk))
        assert (got.kind, got.topic) == ("story", "parking"), talk


def test_inventory_taps_and_choice_buttons():
    tool_tap = {"id": "story_y", "label_th": "ดูห้องจริง", "label_en": "See a real room",
                "tool": {"name": "show_room_type", "args": {"type": "studio"}}}
    assert router.classify_injected(stories.tap_text(tool_tap, "th", "en")).kind == "tool_tap"
    from app import quick_asks

    for talk in (None, "th", "en"):
        got = router.classify_injected(quick_asks.text_for("condo", "type_2br", "th", talk))
        assert (got.kind, got.topic) == ("choice", "unit_two_bedroom"), talk


def test_a_tapped_picture_and_a_topic_and_a_language_button():
    assert router.classify_injected(
        "[The guest tapped the picture \"Lobby\" on the screen to see it large and did not speak; "
        "it is enlarged on the screen now. This conversation is in English: answer in English.] x").kind == "more_tap"
    from app import quick_asks

    got = router.classify_injected(quick_asks.text_for("condo", "location", "en", "en"))
    assert got.kind == "topic" and got.text == quick_asks.TOPICS["condo"][4]["ask"]["en"]
    got = router.classify_injected(quick_asks.text_for("condo", "lang_en"))
    assert (got.kind, got.lang) == ("language", "en")


def test_instructions_with_no_offline_meaning_are_ignored():
    from app import session

    for text in (session.FOLLOW_UP_TEXT, session.GOODBYE_TEXT, "หุ่นเดินถึงห้องประชุมแล้ว"):
        assert router.classify_injected(text).kind == "ignore"
