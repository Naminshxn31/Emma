"""talk_lang hears a request for Vietnamese (and the languages it already
named but could not be asked for).

Robot log 28 Oct (staff testing in the media room): a Thai talk, a guest heard
as "ภาษาเวียดนาม", and Emma answered three turns in Vietnamese ("Ở tầng hầm
có ...") -- while app/talk_lang.py, which knew no Vietnamese, handed every
tool result "ภาษาของบทสนทนานี้คือภาษาไทย: ตอบเป็นภาษาไทย". When the guest
really asks, the server's note must say the same thing she was asked, not
the opposite. A bare language name with no verb of speaking is still not a
request here (the 19 Oct rule: "ชื่อเป็นภาษาอังกฤษอะไร" locked English) --
what Emma should do with one is the owner's call, not this module's.
"""
from __future__ import annotations

import pytest

from app import talk_lang


def _talk(*guest):
    tl = talk_lang.TalkLanguage()
    for line in guest:
        tl.guest(line)
        tl.emma("ค่ะ")
    return tl


@pytest.mark.parametrize("line", ["พูดภาษาเวียดนามหน่อยครับ", "พูดภาษาเวียดนามได้ไหม",
                                  "Please answer in Vietnamese", "speak Vietnamese please",
                                  "请用越南语", "nói tiếng việt đi"])
def test_asking_for_vietnamese_sets_it_and_the_note_says_so(line):
    tl = _talk("นี่แหละครับ อยากรู้เรื่องโครงการ", line)
    assert tl.language() == "vi", line
    assert "ภาษาเวียดนาม" in tl.note()


def test_a_vietnamese_sentence_is_vietnamese_not_english():
    """Its tone-marked vowels sit in U+1E00-1EFF, which the Latin count
    missed: "Ở tầng hầm có" read as too few letters, or as English."""
    assert talk_lang.detect("Ở tầng hầm có hồ bơi trong nhà") == "vi"
    assert talk_lang.detect("Xin chào, dự án ở đâu?") == "vi"
    assert talk_lang.detect("Où est l'hôtel? Je veux être prêt") == "fr"
    assert talk_lang.detect("Where is the gym on the ground floor?") == "en"


def test_a_question_about_her_or_a_bare_name_moves_nothing():
    assert _talk("ภาษาไทยนะครับ ขอดูสระว่ายน้ำ", "Can you speak Vietnamese?").language() == "th"
    # 28 Oct's line exactly: no verb of speaking -- unchanged on purpose
    assert _talk("นี่แหละครับ อยากรู้เรื่องโครงการ", "ภาษาเวียดนาม").language() == "th"


@pytest.mark.parametrize("line,code", [("please speak Italian", "it"), ("พูดภาษาฮินดีได้ไหม", "hi"),
                                       ("answer in Arabic please", "ar"), ("พูดภาษาฮีบรูหน่อย", "he")])
def test_the_names_it_already_had_can_be_asked_for(line, code):
    assert _talk("Where is the pool on this project?", line).language() == code


def test_a_negated_ask_for_vietnamese_is_no_request():
    assert _talk("Where is the pool on this project?", "Sorry, I don't speak Vietnamese").language() == "en"


def test_a_tap_in_a_vietnamese_talk_names_vietnamese():
    """The tap texts read the same codes (session._quick_ask -> quick_asks /
    stories.tap_text): a code talk_lang can name that quick_asks cannot is a
    tap that falls back to the Thai "answer in the language spoken last" --
    which a Thai tap text beats (owner 15 Oct). Review 29 Oct: "vi" was added
    here and not there."""
    from app import quick_asks

    assert set(talk_lang.NAMES) <= set(quick_asks._LANG_NAMES_EN)
    topic = quick_asks.TOPICS["condo"][0]
    text = quick_asks.text_for("condo", topic["id"], "th", "vi")
    assert "This conversation is in Vietnamese" in text, text
