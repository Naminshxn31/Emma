"""The conversation's language, kept by the server (app/talk_lang.py), and the
note every tool result carries (TALK_LANGUAGE_NOTE, providers/gemini.py).

The sequences come from a robot conversation of 19 Oct (an English talk that
went German and Thai on its own) and 15 Oct (a Thai team and an English tester
taking turns): the misheard pieces as the recogniser heard them, the one
personal remark of 19 Oct replaced by a neutral question of the same kind.
"""
import pytest

from app import talk_lang as tl
from app.config import settings


def _talk(*turns):
    """Guest lines; Emma answers after each (one utterance per entry)."""
    t = tl.TalkLanguage()
    seen = []
    for line in turns:
        t.guest(line)
        seen.append(t.language())
        t.emma("(reply)")
    return t, seen


def test_the_english_talk_of_19_oct_stays_english():
    _, seen = _talk("Did you sleep good?",
                    "de",                                   # a garbled sound: says nothing
                    "Okay, English. I want to ask you, do you have a good recipe for a cheesecake?",
                    "Bei der Kondom ist inklusive Karte?",  # an accent, heard as German
                    "Okay, is it include cartons?",
                    "ใช่",                                  # a "yeah" heard as Thai
                    "Okay, which room type would you recommend for a family to buy, and why that one?")
    assert seen == ["en"] * 7


def test_the_first_utterance_sets_it_and_the_greeting_is_not_the_guests():
    t = tl.TalkLanguage()
    t.emma("Hello, I'm Emma. Welcome to Embassy World.")     # ours: English
    assert t.language() is None and t.note() is None
    t.guest("สวัสดีค่ะ มีสระว่ายน้ำไหมคะ")
    assert t.language() == "th"
    assert "ภาษาไทย" in t.note()


def test_a_whole_sentence_in_another_script_moves_it_at_once_a_knock_does_not():
    _, seen = _talk("Where is the project?", "ตึ้ง ตึง  ตึง", "อาคารสูงเท่าไหร่")
    assert seen == ["en", "en", "th"]
    # 15 Oct: a Thai talk, then somebody asking in English
    _, seen = _talk("มีแบบห้องอะไรบ้างคะ", "What?", "What is there for children?")
    assert seen == ["th", "th", "en"]


def test_the_same_script_needs_two_in_a_row():
    _, seen = _talk("How big is the studio?", "Wie groß ist die Wohnung?", "Gibt es einen Pool im Haus?")
    assert seen == ["en", "en", "de"]
    _, seen = _talk("How big is the studio?", "Wie groß ist die Wohnung?", "And the beach, how far is it?")
    assert seen == ["en", "en", "en"]


def test_asking_for_a_language_sets_it_and_holds_until_three_say_otherwise():
    _, seen = _talk("What is the size of the studio?", "Please answer in German from now on.",
                    "How far is the beach?", "Is there a gym?", "And a sauna in the building?")
    assert seen == ["en", "de", "de", "de", "en"]
    _, seen = _talk("ต่อไปนี้ตอบเป็นภาษาอังกฤษเท่านั้นนะ", "ค่าส่วนกลางเท่าไหร่")     # S21-switch-en
    assert seen == ["en", "en"]
    _, seen = _talk("Why you switch to Thai? Change back to English.")
    assert seen == ["en"]
    _, seen = _talk("อาคารสูงเท่าไหร่", "พูดภาษาอังกฤษได้ไหม")                       # Thai asks with ได้ไหม
    assert seen == ["th", "en"]


def test_a_question_about_her_or_a_word_in_a_language_is_not_a_choice():
    _, seen = _talk("Where is the project?", "Can you speak Chinese?")
    assert seen == ["en", "en"]
    # replaying 15 Oct, this read as a request and would have held English
    # against the Thai team's next 15 questions
    _, seen = _talk("ชื่อเป็นภาษาอังกฤษอะไรอย่างเงี้ย", "ที่ดินที่นี่ขนาดเท่าไหร่ครับ")
    assert seen == ["th", "th"]
    assert tl.asked_for("ภาษาอังกฤษใช้คำว่าอะไร") == (None, False)


@pytest.mark.parametrize("line", [
    "Sorry, I don't speak Thai", "I don't speak Thai, sorry", "Sorry, I can't speak Thai",
    "I do not speak Thai", "I cannot speak Thai", "I never speak German", "I can't really speak Thai",
    "Please don't answer in Thai", "Can you not speak Thai?", "I don't want you to speak Thai",
    "ผมพูดภาษาอังกฤษไม่ได้นะครับ", "ไม่ต้องพูดภาษาอังกฤษนะ", "อย่าพูดภาษาไทย",
    "พูดภาษาอังกฤษไม่เป็นค่ะ", "ฉันพูดภาษาอังกฤษไม่เก่ง", "พูดภาษาอังกฤษได้ไม่ค่อยดี",
])
def test_a_negation_next_to_the_verb_is_no_request(line):
    # pre-commit review 26 Oct: every one of these locked the language it refuses
    assert tl.asked_for(line) == (None, False)


@pytest.mark.parametrize("line", [
    "My wife can speak Thai", "She can speak English", "Can they speak English here?",
    "ภรรยาผมพูดภาษาไทยได้", "เพื่อนผมพูดภาษาจีน",
])
def test_somebody_else_as_the_subject_is_no_request(line):
    assert tl.asked_for(line) == (None, False)


def test_a_refusal_does_not_lock_the_language_it_refuses():
    # the review's scenario: an English guest objecting to Thai was then told
    # "answer in Thai" for two more English questions (LOCKED_RUN)
    _, seen = _talk("Where is the project?", "Sorry, I don't speak Thai", "Where is the pool?",
                    "Is there a gym?")
    assert seen == ["en", "en", "en", "en"]
    _, seen = _talk("อาคารสูงเท่าไหร่", "ผมพูดภาษาอังกฤษไม่ได้นะครับ", "ฟิตเนสอยู่ชั้นไหน")
    assert seen == ["th", "th", "th"]
    _, seen = _talk("มีที่จอดรถไหมคะ", "ไม่ต้องพูดภาษาอังกฤษนะ", "สระว่ายน้ำอยู่ชั้นไหน")
    assert seen == ["th", "th", "th"]
    _, seen = _talk("How big is the studio?", "My wife can speak Thai", "And the beach, how far is it?")
    assert seen == ["en", "en", "en"]


def test_why_not_and_a_refusal_with_a_request_are_still_requests():
    for line in ("Why don't you speak English?", "Why not speak English?", "Why you don't speak English?",
                 "ทำไมไม่พูดภาษาอังกฤษ"):
        assert tl.asked_for(line) == ("en", False), line
    # one mention refused, the other asked for
    assert tl.asked_for("I don't speak English, please speak Thai") == ("th", False)
    assert tl.asked_for("I can speak Thai") == ("th", False)          # the guest's own: unchanged
    assert tl.asked_for("Can you speak Chinese?") == ("zh", True)     # still a question about her
    # the fix round's review (26 Oct): "she" in the question form is her, not
    # a third person, and "ให้เขา..." asks for it through somebody
    assert tl.asked_for("Can she speak Chinese?") == ("zh", True)
    for line in ("ให้เขาพูดภาษาอังกฤษ", "บอกให้เขาพูดภาษาอังกฤษหน่อย", "อยากให้เขาตอบเป็นภาษาอังกฤษ"):
        assert tl.asked_for(line) == ("en", False), line
    assert tl.asked_for("เขาพูดภาษาอังกฤษได้") == (None, False)


def test_pieces_of_one_sentence_are_one_utterance():
    # the recogniser sends a sentence in pieces; two German-looking pieces
    # before Emma speaks are still one line, not "two in a row"
    t = tl.TalkLanguage()
    t.guest("How many bathrooms are in the two bedroom?")
    t.emma("Two.")
    t.guest("Bei der Kondom")
    t.guest("ist inklusive Karte? Gibt es das?")
    assert t.language() == "en"


def test_detect_reads_scripts_and_function_words():
    assert tl.detect("มี sky pool ไหมคะ อยู่ floor ไหน") == "th"      # Thai with English names in it
    assert tl.detect("项目在哪里？离海滩远吗？") == "zh"
    assert tl.detect("駅から近いですか？") == "ja"
    assert tl.detect("Where is the project, is it near the beach?") == "en"
    assert tl.detect("Wo ist das Projekt und wie weit ist der Strand?") == "de"
    assert tl.detect("ใช่") is None and tl.detect("OK") is None and tl.detect("de") is None


@pytest.fixture
def provider(monkeypatch):
    from app.providers.gemini import GeminiProvider
    p = GeminiProvider.__new__(GeminiProvider)
    p._talk = tl.TalkLanguage()
    return p


def test_the_note_rides_on_tool_results_only_when_switched_on(provider, monkeypatch):
    result = {"found": True, "lines": ["Jedes Zimmer ..."]}
    monkeypatch.setattr(settings, "talk_language_note", False)
    provider.note_guest("How many bathrooms are in the two bedroom?")
    assert provider._with_talk_language(result) is result          # off: byte-identical
    monkeypatch.setattr(settings, "talk_language_note", True)
    out = provider._with_talk_language(result)
    assert "ภาษาอังกฤษ" in out["reply_language"] and out["found"] is True
    assert "reply_language" not in result                          # the session's copy untouched
    assert provider._with_talk_language(["not", "a", "dict"]) == ["not", "a", "dict"]


def test_no_note_before_the_guest_has_said_anything_that_tells(provider, monkeypatch):
    monkeypatch.setattr(settings, "talk_language_note", True)
    result = {"found": True}
    assert provider._with_talk_language(result) is result
    provider.note_guest("OK")
    assert provider._with_talk_language(result) is result


def test_the_recognisers_words_feed_it(provider, monkeypatch):
    # the provider's own transcript path, not only the eval's note_guest
    import inspect
    from app.providers import gemini
    src = inspect.getsource(gemini.GeminiProvider._input_event)
    assert "self._talk.guest(text)" in src
    assert "self._talk.emma(shown)" in inspect.getsource(gemini.GeminiProvider)   # her words, markup stripped (27 Oct)
    assert "_with_talk_language(" in inspect.getsource(gemini.GeminiProvider)
