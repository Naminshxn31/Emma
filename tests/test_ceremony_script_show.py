"""The ceremony follows the SCRIPT again, and shows only the script (owner 24 Nov ~13:45-13:50, rehearsal).

Owner: "ปรับให้สคริป์ของเราเหลือคำถาม ถ้าหุ่นได้ยินประโยคที่คล้ายคำถามก็เปลี่ยนภาพจอแล้วพูดตามบท" and "ถ้าคำถามที่เขาคล้ายกับเรา ก็ให้ตอบ
ตามสคริปที่เราทำ แล้วก็โชว์ไปเลย ส่วนโหมดเก่าก็เอาออกไปก่อน ... อะไรที่ไม่มีในบท ก็ให้หุ่นตอบแบบสดไปได้ แต่ไม่ต้องโชว์อะไร แล้วก็พยายามให้เขา
มาตามบทของเรา". What these hold down:

* `ask_en` (floor1_ceremony.host.json): his QUESTION alone matches its cue -- at the real order's lookahead, never the wrong
  cue, never an off-script sentence; lights off is never lights back on; a one-word question is never a variant;
* CEREMONY_SCRIPT_SHOW_ONLY: no motion story offered to the ceremony call (the Event app keeps it), no topic slide from
  the host's words; off = as before;
* the script in her context: in free talk too (it used to be None there), his English question beside her line, a
  continuation joined to the line before it, no staff-tick line, no auto joke; off the script she answers and invites him
  back to the next topic.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, ceremony_motion, event_host
from app.config import settings
from app.tools import registry
from tests.test_ceremony_follow import fol, real_lines  # noqa: F401 -- fixtures
from tests.test_event_order import call, calls, event, rows  # noqa: F401 -- fixtures

ORDER = ["s02_l03", "s02_l09", "s03_l03", "s04_l03", "s04_l06", "s04_l09", "s04_l15", "s05_l03", "s05_l06",
         "s05_l11", "s06_l03", "s06_l05", "s07_l02", "s07_l07", "s08_l06", "s08_l10", "s08_l14", "s09_l02"]
# How he might really ask -- none of these is a variant's own wording word for word
SAID = {
    "s02_l03": ["Emma, how can you help us today?", "So Emma, what can you help us with?"],
    "s02_l09": ["What makes our project different from the others?", "Emma, how is our project different?"],
    "s03_l03": ["Where is our project, Emma?", "So where exactly is Embassy World located?"],
    "s04_l03": ["How many units do we have?", "And what types of rooms are there?"],
    "s04_l06": ["What about our facilities, Emma?", "Tell us about the facilities."],
    "s06_l03": ["What about the depth of the swimming pool?"],
    "s07_l02": ["Emma, please play the technology video."],
    "s07_l07": ["What does this page show?"],
    "s08_l06": ["Emma, please turn off the lobby lights.", "Turn the lights off please."],
    "s08_l10": ["Emma, please turn the lights back on.", "Turn the lights back on please."],
    "s08_l14": ["Please open the curtains.", "And what about the curtains, Emma?"],
    "s09_l02": ["Emma, please play the story video."],
}
OFF = ["What's the weather like today?", "Emma, what can you do?", "Thank you everyone for coming today.",
       "Can you sing a song?", "How are you today, Emma?", "Let's give Emma a big hand!",
       "The project is very beautiful, isn't it?", "Do you have a pool?", "Is there parking?"]


def _ahead(cue):
    i = ORDER.index(cue)
    return ORDER[i:i + follow.LOOKAHEAD]


def _matched(said, cue):
    r = follow.decide_text(said, _ahead(cue), final=True)
    return r["verdict"] == "match" and r["cue"] == cue, r


def test_his_question_alone_plays_its_cue_and_off_script_never_does(real_lines):
    for cue, sayings in SAID.items():
        for said in sayings:
            ok, r = _matched(said, cue)
            assert ok, (cue, said, r)
    for k in range(len(ORDER)):
        for said in OFF:
            r = follow.decide_text(said, ORDER[k:k + follow.LOOKAHEAD], final=True)
            assert r["verdict"] != "match", (ORDER[k], said, r)


def test_without_the_questions_most_of_these_were_missed(real_lines, monkeypatch):
    # (why ask_en exists: the whole host lines alone matched 9 of these 29 at the rehearsal's wording)
    real = follow._compiled

    def lines_only():
        return {k: v for k, v in real().items() if follow.ASK not in k}

    monkeypatch.setattr(follow, "_compiled", lines_only)
    missed = sum(not _matched(said, cue)[0] for cue, sayings in SAID.items() for said in sayings)
    assert missed >= 10


def test_lights_off_is_never_lights_back_on(real_lines):
    r = follow.decide_text("Emma, please turn off the lobby lights.", ["s08_l06", "s08_l10", "s08_l14"], final=True)
    assert r["verdict"] == "match" and r["cue"] == "s08_l06"
    r = follow.decide_text("Turn the lights back on, Emma.", ["s08_l10", "s08_l14", "s09_l02"], final=True)
    assert r["verdict"] == "match" and r["cue"] == "s08_l10"
    # back on, said while lights-off is still the next cue: back on, never off
    r = follow.decide_text("Turn the lights back on, Emma.", ["s08_l06", "s08_l10", "s08_l14"], final=True)
    assert r["cue"] == "s08_l10" or r["verdict"] != "match"


def test_a_question_of_one_word_is_never_a_variant_and_not_listed_generic(real_lines):
    asks = {k: v for k, v in follow._compiled().items() if follow.ASK in k}
    assert asks and all(len(v) >= 2 for v in asks.values())
    assert all(follow.ASK not in c for c in follow.generic_cues("any"))
    assert set(follow.generic_cues()) == {"s04_l09", "s04_l15", "s05_l06", "s06_l05", "s07_l07", "s08_l10", "s08_l14"}


def _story_tool_allowed(profile):
    entry = registry._REGISTRY.get("present_story")
    assert entry is not None
    p = registry.session_profile.set(profile)
    f = registry.robot_face_session.set(object())
    try:
        return registry.allowed(entry)
    finally:
        registry.robot_face_session.reset(f)
        registry.session_profile.reset(p)


def test_no_motion_story_in_the_ceremony_call_with_the_switch_the_event_keeps_it(monkeypatch):
    import app.tools.stories  # noqa: F401 -- registers present_story
    from app import stories

    monkeypatch.setattr(settings, "stories_enabled", True)
    monkeypatch.setattr(settings, "enabled_tool_groups", lambda: None)
    monkeypatch.setattr(registry, "_OPT_IN", set(), raising=False)
    import app.tools as tools_pkg
    monkeypatch.setattr(tools_pkg, "_OPT_IN", set())
    monkeypatch.setattr(stories, "offered", lambda: True)
    assert _story_tool_allowed("ceremony") is True                     # off: as before
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert _story_tool_allowed("ceremony") is False
    assert _story_tool_allowed("event") is True                        # after the ceremony: the Event app's stories


def test_no_topic_slide_from_the_hosts_words_with_the_switch(monkeypatch):
    session = object()
    decided = []
    monkeypatch.setattr(ceremony, "active_session", lambda: session)
    monkeypatch.setattr(ceremony, "_adhoc_live", lambda now: {"cue": "adhoc"})
    monkeypatch.setattr(ceremony_motion, "_decide_now", lambda s, c: decided.append(c))
    caption = SimpleNamespace(text="tell us about the swimming pool", at=1.0)
    ceremony_motion.on_host_words(session, caption)                    # (no loop: decided at once)
    assert decided == [caption]                                        # off: as before
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    ceremony_motion.on_host_words(session, caption)
    assert decided == [caption]


@pytest.fixture
def script_on(real_lines, monkeypatch):                                # noqa: F811
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", True)
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_jokes_auto", True)


def test_free_talk_has_the_script_with_his_english_question_and_the_continuation(script_on, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)
    monkeypatch.setattr(settings, "ceremony_free_talk", True)
    block = event_host.script_block()
    assert block is not None and event_host.SCRIPT_HEAD in block
    first = next(line for line in block.splitlines() if "how you can help us" in line)    # his English line (host_en)
    assert "I can share project information" in first                  # S02-L05/L06 joined to S02-L03's answer
    assert "Wow" not in block and "remember the details" not in block  # the auto jokes' set-ups: the server's
    assert event_host.SCRIPT_FREE_TAIL_EN.split("%")[0][:30] in block or "invite him back" in block
    monkeypatch.setattr(settings, "ceremony_follow_script_prompt", False)
    assert event_host.script_block() is None                           # the switch still decides


def test_script_mode_tail_answers_off_script_and_steers_back(script_on, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    block = event_host.script_block()
    assert block is not None
    assert "invite him back" in block and "is not a question to you: do not say a script line" not in block
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", False)  # named-only: the old tail
    assert "invite him back" not in event_host.script_block()


def _skipped_scene_1():
    from app import event_order

    return event_order._scenes(), {event_order._key(1, i): {"state": "skipped"} for i in range(3)}


def test_a_missed_first_question_does_not_strand_the_pointer_in_script_mode(real_lines):
    # rehearsal 24 Nov 14:36: s02_l03 was missed (she said it live), the pointer stayed on it with s02_l05 (a
    # continuation) behind it, and "Where is our project located?" was matched against s02_l03 alone -- no slide, ever
    from app import event_order

    scenes, marks = _skipped_scene_1()
    old = follow.pointer_of(scenes, marks, (1, 2), follow.lines())
    assert [c["cue"] for c in old["cands"]] == ["s02_l03"]                     # (as it was, off)
    ptr = follow.pointer_of(scenes, marks, (1, 2), follow.lines(), read_past=True)
    ids = [c["cue"] for c in ptr["cands"]]
    assert ids[:3] == ["s02_l03", "s02_l09", "s03_l03"] and len(ids) == follow.SCRIPT_LOOKAHEAD
    r = follow.decide_windows(["Where is our project located?"], ids, final=True)
    assert r["verdict"] == "match" and r["cue"] == "s03_l03"
    # a continuation that IS the next step still plays by itself after its line
    marks[event_order._key(2, 0)] = {"state": "done"}
    after = follow.pointer_of(scenes, marks, (2, 0), follow.lines(), read_past=True)
    assert after["kind"] == "continues" and after["cue"] == "s02_l05"
    assert [c["cue"] for c in after["cands"]][:2] == ["s02_l09", "s03_l03"]   # (his next questions behind it)


def test_the_pointer_reads_past_only_in_script_mode(real_lines, monkeypatch):
    monkeypatch.setattr(follow, "pointer_of", lambda *a, **k: {"kind": "end", "cands": [], "at": None, **k})
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    assert follow.pointer()["read_past"] is False
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert follow.pointer()["read_past"] is True
    monkeypatch.setattr(settings, "ceremony_follow_cues", False)                 # live first reads past anyway
    assert follow.pointer()["read_past"] is False


def test_one_dropped_question_word_is_still_the_question(real_lines):
    # rehearsal 24 Nov 14:36: "What makes our project different?" was transcribed "Make project different."
    ids = ["s02_l03", "s02_l09", "s03_l03"]
    r = follow.decide_windows(["Make project different."], ids, final=True)
    assert r["verdict"] == "match" and r["cue"] == "s02_l09"
    # ... but a question left with fewer than three words is not: "our project" alone is any sentence about it
    r = follow.decide_windows(["Is our project located near here?"], ["s02_l03", "s02_l09"], final=True)
    assert r["verdict"] != "match"


def test_the_question_is_tried_on_the_first_decision_not_only_after_the_tail_grace(real_lines):
    # rehearsal 24 Nov 14:44: _decide judges once his words stop (QUIET_S); only a "no_tail" verdict ever reaches the
    # final pass -- "What makes our project different?" got "nothing" and was never matched as a question
    r = follow.decide_windows(["What makes our project different?"], ["s02_l03", "s02_l09", "s03_l03"])
    assert r["verdict"] == "match" and r["cue"] == "s02_l09" and r["by"] == "ask"


def test_script_mode_asks_for_a_short_live_reply_so_the_press_does_not_wait_on_a_whole_line(script_on, monkeypatch):
    # rehearsal 24 Nov ~14:55 "ทำไมพอถามไปมันเหมือนตอบช้าจัง": the read press waits for her held reply to finish
    # generating; she recited the whole line (6-9 s). In script mode the note asks for 1-2 short sentences instead.
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    assert event_host.SCRIPT_SHOW_NOTE_EN.split("%")[0] not in event_host.script_block()   # off: the old follow note
    assert "never recite a whole line" not in event_host.script_block()
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    block = event_host.script_block()
    assert "never recite a whole line" in block and "15-25 words" in block
    assert "The system itself plays the recorded line" not in block


LONG_TALK = [
    ("s03_l03", ["s02_l09", "s03_l03", "s04_l03"],
     "Ladies and gentlemen, our project is in a very good area, close to the beach and the city, and many of you "
     "already know Jomtien very well. It is a lovely place to live. So Emma, where is our project located?"),
    ("s04_l06", ["s04_l03", "s04_l06", "s04_l09"],
     "We have many kinds of rooms here, and I think everyone will love the swimming pool and the gym and all the places "
     "to relax with your family. Our team worked very hard on this. Emma, what about our facilities?"),
    ("s04_l03", ["s04_l03", "s04_l06", "s04_l09"],
     "Thank you Emma. Now everyone, this project was designed for families and for investors, the rooms are bright and "
     "modern and the views are wonderful. Emma, how many units do we have, and what types of rooms?"),
]


def test_a_long_talk_to_the_guests_then_his_question_plays_the_questions_cue(real_lines):
    # owner 24 Nov ~14:58: "มิกิพูดยาวๆ แบบอธิบายให้แขกฟัง แล้วกลับมาถามหุ่นจะตอบได้ไหม หุ่นจะตอบตามคำถามไหม"
    for cue, ahead, talk in LONG_TALK:
        r = follow.decide_windows([talk], ahead, final=True)
        assert r["verdict"] == "match" and r["cue"] == cue, (cue, r)
    # a talk with no question of the script is no cue
    r = follow.decide_windows(["Everyone, please enjoy the drinks and the food, our team is here to help you with "
                               "anything you need, and after the ceremony you can visit the sales gallery to see the "
                               "rooms and the facilities."], ["s04_l03", "s04_l06", "s04_l09"], final=True)
    assert r["verdict"] != "match"


def test_his_pauses_do_not_close_his_turn_in_script_mode_too(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_live_end_silence_ms", 1500)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)
    assert settings.call_vad_silence_ms("ceremony") is None                    # pressed mode, as it was
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert settings.call_vad_silence_ms("ceremony") == 1500
    assert settings.call_vad_silence_ms("event") is None and settings.call_vad_silence_ms("condo") is None


def test_english_only_the_thai_lines_are_not_matched_and_print_heard_as_spin_is(real_lines, monkeypatch):
    # owner 24 Nov ~15:06 "วันจริงเองห้ามพูดไทย ไม่ต้องฟังไทยด้วย"; rehearsal 15:03 / 15:05: "print" came out "spin" twice
    from tests.test_ceremony_follow import REAL_HOSTS

    thai = REAL_HOSTS["s03_l03"]["host_th"]
    assert follow.decide_windows([thai], ["s03_l03"], final=True)["verdict"] == "match"      # off: as before
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    assert follow.decide_windows([thai], ["s03_l03"], final=True)["verdict"] != "match"
    r = follow.decide_windows(["No, please spin the quotation."], ["s05_l11"], final=True)
    assert r["verdict"] == "match" and r["cue"] == "s05_l11"
    r = follow.decide_windows(["Where is our project located?"], ["s02_l09", "s03_l03"], final=True)
    assert r["verdict"] == "match" and r["cue"] == "s03_l03"                                 # English still is


def test_a_thai_only_caption_gets_no_reply_in_the_english_only_window(monkeypatch):
    from app.providers import transcript_filter as tf
    from app.providers.base import ProviderEvent
    from app.providers.transcript_filter import NoiseReplyGate

    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: True)

    def gate():
        return NoiseReplyGate(0.06, follow_hold=lambda: None)

    def reply(g, heard):
        g.segment(0.03)
        g.heard(heard)
        return g.push(ProviderEvent(kind="assistant_transcript", text="Sure."))

    monkeypatch.setattr(settings, "ceremony_english_only", False)
    assert reply(gate(), "ใช่ ๆ จะ ได้ ไม่ ต้อง เบลอ") != []                                # off: answered, as before
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    g = gate()
    assert reply(g, "ใช่ ๆ จะ ได้ ไม่ ต้อง เบลอ") == []
    assert g.close()[1]["why"] == "thai_ignored"
    assert reply(gate(), "Emma, where is our project located?") != []                     # English: hers
    assert reply(gate(), "QR Code อยู่ไหน") != []                                           # any Latin: his, as before


def test_an_off_script_answer_takes_the_last_lines_slide_down_to_the_logo(monkeypatch):
    # rehearsal 24 Nov 15:08: "Prepare a quotation for this unit?" stood under her live answers about other things
    from app import event_cues, face_mirror

    sent = []
    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: sent.append((slide, cue)))
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    monkeypatch.setattr(follow, "_gate_why", lambda: None)
    follow._off_script_logo(["What about all facility?"])
    assert sent == [(None, None)]
    sent.clear()
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    follow._off_script_logo(["ใช่ ๆ จะ ได้ ไม่ ต้อง เบลอ"])                    # the staff's Thai: nothing
    assert sent == []
    monkeypatch.setattr(event_cues, "line_audible", lambda: True)            # a line still being said: it stays
    follow._off_script_logo(["Thank you, Emma."])
    assert sent == []
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    monkeypatch.setattr(follow, "_gate_why", lambda: "chain")                # a punchline coming: it stays
    follow._off_script_logo(["Thank you, Emma."])
    assert sent == []


SHEET = {   # the questions on Khun Miki's page, in order (artifact "Khun Miki's Questions")
    "s02_l03": "Emma, how can you help us?", "s02_l09": "What makes our project different?",
    "s03_l03": "Where is our project located?", "s04_l03": "How many units do we have, and what types of rooms?",
    "s04_l06": "What about our facilities?", "s04_l09": "Wow, you remember the details very well!",
    "s04_l15": "Please stay and help the sales team with me first.",
    "s05_l03": "Suppose I'm a customer looking for a room for my family. How would you help?",
    "s05_l06": "I would like a sea view.", "s05_l11": "Please print the quotation.",
    "s06_l03": "What if a customer asks for more detail, like the height of the building?",
    "s06_l05": "You really do know the details very well.", "s07_l02": "Emma, please play the technology video.",
    "s07_l07": "What does this page show?", "s08_l06": "Emma, please turn off the lobby lights.",
    "s08_l10": "Emma, please turn the lights back on.", "s08_l14": "Please open the curtains.",
    "s09_l02": "Emma, please play the story video.",
}


def test_the_whole_ceremony_by_his_questions_alone_never_sticks_and_nobody_ticks(real_lines, monkeypatch):
    # owner 24 Nov ~15:12 "ของจริงต้องไม่ค้างนะ": every question of his page, in order, with NO staff tick ever made --
    # each plays its own cue; a continuation plays by itself; a joke's punchline follows its set-up
    from app import event_order

    monkeypatch.setattr(settings, "ceremony_english_only", True)
    scenes = event_order._scenes()
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    last, fired = (1, 2), []
    for cue, question in SHEET.items():
        for _ in range(6):                                   # what the server plays by itself first
            ptr = follow.pointer_of(scenes, marks, last, follow.lines(), read_past=True)
            if ptr["kind"] not in ("continues",):
                break
            marks[event_order._key(*ptr["at"])] = {"state": "done"}
            last = tuple(ptr["at"])
        assert ptr["kind"] == "host", (cue, ptr)
        ids = [c["cue"] for c in ptr["cands"]]
        r = follow.decide_windows([question], ids)
        assert r["verdict"] == "match" and r["cue"] == cue, (cue, ids, r)
        cand = next(c for c in ptr["cands"] if c["cue"] == cue)
        marks[event_order._key(cand["n"], cand["i"])] = {"state": "done"}
        last = (cand["n"], cand["i"])
        fired.append(cue)
        if cue in ("s04_l09", "s06_l05"):                    # the chain plays the punchline
            nxt = follow.pointer_of(scenes, marks, last, follow.lines(), True, read_past=True)
            assert nxt["kind"] == "chain"
            n, i = last
            marks[event_order._key(n, i + 1)] = {"state": "done"}
            last = (n, i + 1)
    assert fired == list(SHEET)


def test_a_lost_punchline_and_an_unticked_step_do_not_hold_him(real_lines):
    from app import event_order

    scenes = event_order._scenes()
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    for k in ("2.0", "2.1", "2.2", "3.0", "4.0", "4.1", "4.2"):
        marks[k] = {"state": "done"}
    # the set-up said, its punchline lost (a restart mid-joke): his next question still plays
    ptr = follow.pointer_of(scenes, marks, (4, 2), follow.lines(), False, read_past=True)
    assert "s04_l15" in [c["cue"] for c in ptr["cands"]]
    # ... while a punchline that IS coming holds (the chain waiting)
    held = follow.pointer_of(scenes, marks, (4, 2), follow.lines(), True, read_past=True)
    assert held["kind"] == "chain"
    # the quotation's tick never ticked: the details question still plays
    for k in ("4.3", "4.4", "4.5", "5.0", "5.1", "5.2", "5.3", "5.4"):
        marks[k] = {"state": "done"}
    ptr = follow.pointer_of(scenes, marks, (5, 4), follow.lines(), read_past=True)
    assert [c["cue"] for c in ptr["cands"]][:2] == ["s06_l03", "s06_l05"]
    old = follow.pointer_of(scenes, marks, (5, 4), follow.lines())
    assert old["kind"] == "gate"                                                  # (off: staff's tick, as before)


def test_a_matched_cue_waiting_for_its_press_keeps_her_reply_held_past_the_usual_6_s(monkeypatch):
    # rehearsal 24 Nov 15:21: the 6 s reply hold ran out while the press waited for her held reply's turn to end; her
    # reply came out of the speaker and the press gave up at 8 s ("held") = no line, no slide
    import time as _time

    from app import ceremony

    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    monkeypatch.setattr(follow, "active", lambda: True)
    monkeypatch.setattr(ceremony, "_adhoc_live", lambda now: {"cue": "adhoc"})
    monkeypatch.setitem(follow.STATE, "on", True)
    monkeypatch.setitem(follow.STATE, "paused", None)
    monkeypatch.setitem(follow.STATE, "dropped", False)
    monkeypatch.setitem(follow.STATE, "pending", True)
    monkeypatch.setitem(follow.STATE, "heard_mono", _time.monotonic() - (follow.REPLY_HOLD_MAX_S + 1))
    monkeypatch.setitem(follow.STATE, "press_wait", None)
    assert follow.reply_hold() is None                                  # past the usual hold: released, as before
    monkeypatch.setitem(follow.STATE, "press_wait", _time.monotonic() - 10)
    assert follow.reply_hold() == "wait"                                # a press waiting: still held
    monkeypatch.setitem(follow.STATE, "press_wait", _time.monotonic() - (follow.SCRIPT_HOLD_S + 3))
    assert follow.reply_hold() is None                                  # ... bounded
    assert follow.SCRIPT_HOLD_S >= 15 and follow.HOLD_S == 8.0          # (the pressed mode's own wait unchanged)


def test_a_continuation_that_never_plays_does_not_hold_his_next_questions(real_lines):
    # review 24 Nov: staff skipped the empty s05_l07 (or s05_l06 is 90 s old, or the press was refused): the pointer
    # stood on s05_l08 with no questions behind it -- print, details, video, lights, curtains, story all dead
    from app import event_order

    scenes = event_order._scenes()
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    for k in ("2.0", "2.1", "2.2", "3.0", "4.0", "4.1", "4.2", "4.3", "4.4", "4.5", "5.0", "5.1"):
        marks[k] = {"state": "done"}
    ptr = follow.pointer_of(scenes, marks, (5, 1), follow.lines(), read_past=True)
    assert ptr["kind"] == "continues" and ptr["cue"] == "s05_l08"            # still played by itself...
    ids = [c["cue"] for c in ptr["cands"]]
    assert ids[:2] == ["s05_l11", "s06_l03"]                                   # ...with his questions behind it
    r = follow.decide_windows(["Please print the quotation."], ids)
    assert r["verdict"] == "match" and r["cue"] == "s05_l11"
    old = follow.pointer_of(scenes, marks, (5, 1), follow.lines())
    assert old["kind"] == "continues" and old["cands"] == []                   # (off: as before)
    # two continuations in a row (the demo room's empty line was still between them, rehearsal 24 Nov 17:33: the second
    # became the pointer with nothing behind it -- "would you like me to prepare a quotation" never came): the first is
    # the pointer, and his questions are still behind it
    rows = dict(follow.lines(), s05_l06={"kind": "continues"})
    marks.pop("5.1")
    two = follow.pointer_of(scenes, marks, (5, 0), rows, read_past=True)
    assert (two["kind"], two["cue"]) == ("continues", "s05_l06") and [c["cue"] for c in two["cands"]][:1] == ["s05_l11"]


def test_thai_captions_do_not_enter_his_words_in_the_english_only_ceremony(monkeypatch):
    from types import SimpleNamespace as NS

    monkeypatch.setattr(follow, "wants_words", lambda s: True)
    monkeypatch.setitem(follow.STATE, "utt", {})
    monkeypatch.setitem(follow.STATE, "spent", 0.0)
    monkeypatch.setitem(follow.STATE, "timer", None)
    monkeypatch.setattr(follow, "_decide_sync", lambda: None)
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    import time as _time

    t0 = _time.monotonic()
    follow.on_host_words(object(), NS(at=t0, text="ใส่ ชุด พร้อม ให้ มัน รี แล้ว นะ"))
    assert follow.STATE["utt"] == {}
    follow.on_host_words(object(), NS(at=t0 + 0.5, text="Where is our project located?"))
    assert list(follow.STATE["utt"].values()) == ["Where is our project located?"]


def test_a_played_question_asked_again_puts_its_slide_up_again(real_lines, monkeypatch):
    # rehearsal 24 Nov 15:26 "ถามไปแล้วโมชั่นไม่เห็นเปลี่ยนเลย": the facilities / location again went to the logo
    import asyncio

    from app import ceremony_motion, event_cues, event_order

    sent = []
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    monkeypatch.setattr(follow, "_gate_why", lambda: None)
    monkeypatch.setattr(ceremony_motion, "prepare_cue", lambda cue: {"cue": cue})
    monkeypatch.setattr(ceremony_motion, "send_cue", lambda cue, slide, at=None: sent.append(cue) or True)
    monkeypatch.setitem(follow.STATE, "utt", {})
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    for k in ("2.0", "2.1", "2.2", "3.0", "4.0", "4.1"):
        marks[k] = {"state": "done"}
    monkeypatch.setitem(event_order.STATE, "marks", marks)
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    # (owner 15:37 "มันขึ้นมาแบบพรวดพราด": the line is played again -- voice, motion, subtitle -- not the slide alone)
    monkeypatch.setitem(event_order.STATE, "last", (4, 1))
    asked = lambda said, near=(): follow._elsewhere_match([said], list(near))          # noqa: E731
    again = asked("Emma, what about all facilities?")
    assert again is not None and (again[0]["n"], again[0]["i"], again[0]["cue"]) == (4, 1, "s04_l06")
    # asked again needs no name, only a question or a request (rehearsal 16:46-16:47, owner "พอกลับมาถามซ้ำโมชั่นก็ไม่เปลี่ยนนะ")
    for said in ("Where is our project located?", "What about our facilities?", "Where is our project located"):
        assert asked(said) is not None, said
    marks["3.0"] = {"state": "skipped"}          # (its press had failed and it was skipped: question 2, rehearsal 15:37)
    assert asked("Emma, where is our project located?")[0]["cue"] == "s03_l03"
    assert asked("Emma, what's the weather like today?") is None                             # off-script: logo
    assert asked("What about our facilities?", near=["s04_l06"]) is None                     # among the next few: theirs
    # a recap of what was just said is no question to her: never the whole old line over him (measured 24 Nov 16:40)
    for recap in ("So now you know how many units we have and what types of rooms.",
                  "Emma just told us how our project is different.", "That was how Emma can help us.",
                  "How can we help you tonight, everyone?"):
        assert asked(recap) is None, recap
    assert sent == [] and asyncio is not None


def test_the_press_waits_for_a_turn_of_hers_in_flight_in_script_mode(monkeypatch):
    # rehearsal 24 Nov 15:30:08: matched, pressed 1 s later and refused "still_speaking" -- her turn had begun with
    # nothing out yet; the gate only looked at streaming output
    from types import SimpleNamespace as NS

    from app import event_cues

    monkeypatch.setitem(follow.STATE, "session", NS(provider=object()))
    monkeypatch.setattr(event_cues, "_asks", lambda provider, name: False)
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    busy = {"v": "turn"}
    monkeypatch.setattr(event_cues, "_model_busy", lambda s, p, now: busy["v"])
    assert follow._gate_why() is None                              # off: as before
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    assert follow._gate_why() == "answering"
    busy["v"] = "reply_owed"                                       # (the read lets this one through itself)
    assert follow._gate_why() is None
    busy["v"] = None
    assert follow._gate_why() is None


def test_off_the_script_she_answers_only_when_he_names_her(monkeypatch):
    # owner 24 Nov ~15:38 "ต่อให้มิกิจะพูดนานแค่ไหน แต่ถ้าบอกชื่อ emma ... พร้อมคำถาม ถึงหุ่นจะตอบ"
    from app.providers import transcript_filter as tf
    from app.providers.base import ProviderEvent
    from app.providers.transcript_filter import NoiseReplyGate

    monkeypatch.setattr(tf, "_ceremony_answers_all", lambda: True)

    def reply(heard):
        g = NoiseReplyGate(0.06, follow_hold=lambda: None)
        g.segment(0.03)
        g.heard(heard)
        out = g.push(ProviderEvent(kind="assistant_transcript", text="Sure."))
        return out, g.close()

    long_talk = "Ladies and gentlemen, thank you so much for coming tonight, the food is wonderful."
    assert reply(long_talk)[0] != []                                          # off: answered, as before
    monkeypatch.setattr(settings, "ceremony_answer_named", True)
    out, (owed, dropped) = reply(long_talk)
    assert out == [] and owed == [] and dropped["why"] == "not_named"
    assert reply("Emma, can you sing a song for us?")[0] != []                 # named: hers
    assert reply("Thank you so much, Emma.")[0] != []


def test_a_jokes_punchline_is_read_sooner_so_the_joke_has_no_hole(monkeypatch):
    # owner 24 Nov ~15:45 "พยายามให้มันเป็นบทเดียวกันเลย ไม่งั้นมันจะเปิดให้พูด": the punchline's read waited the set-up's last
    # 1.5 s of voice and then ~1 s more for her first word. A chained punchline counts the tail CHAIN_READ_LEAD_S sooner.
    import time as _time

    from app import event_cues

    prov = object()
    now = _time.monotonic()
    monkeypatch.setitem(event_cues.STATE, "audible", (prov, now + 0.6 - event_cues.AUDIBLE_SLACK_S, True))
    assert event_cues._tail_on(prov, now) is True                                  # an ordinary line waits it out
    assert event_cues._tail_on(prov, now + event_cues.CHAIN_READ_LEAD_S) is False  # the punchline goes now
    assert 0.5 <= event_cues.CHAIN_READ_LEAD_S <= 1.2
    src = open("app/event_cues.py", encoding="utf-8").read()
    assert src.count("CHAIN_READ_LEAD_S if run.get(\"chained\") else 0.0") == 2    # both looks: the wait and the send


def test_a_listened_recording_does_not_wait_for_her_held_reply(monkeypatch):
    # owner 24 Nov ~15:55 "ที่ตอบช้าแก้แล้วใช่ไหม": the read must wait her held turn out (6-9 s); a recording need not --
    # play_recorded purges/holds the model's output itself
    from types import SimpleNamespace as NS

    from app import event_cues

    monkeypatch.setitem(follow.STATE, "session", NS(provider=object()))
    monkeypatch.setattr(event_cues, "_asks", lambda provider, name: name == "model_streaming")
    monkeypatch.setattr(event_cues, "line_audible", lambda: False)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    hold = {"v": "wait"}
    monkeypatch.setattr(follow, "reply_hold", lambda: hold["v"])
    assert follow._gate_why() == "answering"                     # a live read: waits, as before
    assert follow._gate_why(recorded=True) is None               # a recording: goes now
    hold["v"] = None                                             # her reply already out loud: still waited for
    assert follow._gate_why(recorded=True) == "answering"
    monkeypatch.setattr(settings, "ceremony_script_show_only", False)
    hold["v"] = "wait"
    assert follow._gate_why(recorded=True) == "answering"        # (only in script mode)
    assert follow._recorded(None) is False


def test_the_lookahead_of_the_script_mode_is_five_and_three_missed_questions_go_on(real_lines):
    # scenario review 24 Nov: 3 questions missed in a row and nothing later could ever match (1/18 fired)
    from app import event_order

    scenes = event_order._scenes()
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    ids = [c["cue"] for c in follow.pointer_of(scenes, marks, (1, 2), follow.lines(), read_past=True)["cands"]]
    assert ids == ["s02_l03", "s02_l09", "s03_l03", "s04_l03", "s04_l06"]
    r = follow.decide_windows(["What about our facilities?"], ids)              # four missed before it
    assert r["verdict"] == "match" and r["cue"] == "s04_l06"


def test_of_two_equal_questions_the_one_said_closest_together_counts(monkeypatch):
    # "play the technology video" inside "play the Embassy World technology video": the first ask (span 5) was kept, 0.6
    heard = follow.tokens("play the Embassy World technology video")
    pos = follow._positions(heard)
    loose = [(t, 1.0) for t in follow.tokens("play the technology video")]
    tight = [(t, 1.0) for t in follow.tokens("play the Embassy World technology video")]
    monkeypatch.setattr(follow, "_compiled", lambda: {"x|q0|en": loose, "x|q1|en": tight})
    monkeypatch.setattr(follow, "_distinct", lambda: {})
    best = follow._score_ask("x", heard, pos)
    assert best["matched"] == best["span"] == len(tight)


def test_the_watchdog_leaves_his_unnamed_talk_alone(monkeypatch):
    from app.providers import gemini

    monkeypatch.setattr(gemini, "_follow_answers_all", lambda: True)
    silent = gemini.GeminiProvider._ceremony_silence_is_right
    talk = "Thank you all so much for coming tonight."
    call_ = SimpleNamespace(_stall_text_turn=False)
    assert silent(call_, talk) is False                    # off: a sentence unanswered is chased
    monkeypatch.setattr(settings, "ceremony_answer_named", True)
    assert silent(call_, talk) is True                     # unnamed: silence is right
    assert silent(call_, "Emma, can you sing for us?") is False


def test_a_press_refused_still_speaking_is_pressed_once_more(fol, monkeypatch):        # noqa: F811
    # rehearsal 24 Nov 15:30:08: refused 1 s after the match and the question was lost
    import asyncio

    from tests.test_ceremony_follow import F1, begin, hear

    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)
    answers, tries = [{"ok": False, "error": "still_speaking"}, {"ok": True}], []

    async def play(cand, res, by, skipped=None):
        tries.append(cand["cue"])
        return answers[len(tries) - 1]

    monkeypatch.setattr(follow, "_play", play)
    monkeypatch.setattr(follow, "_gate_why", lambda recorded=False: None)
    monkeypatch.setattr(follow, "_recorded", lambda cue: False)

    async def go():
        await begin()
        hear(F1)
        await follow._decide()

    asyncio.run(go())
    assert tries == ["f1", "f1"] and follow.STATE["dropped"] is True and follow.STATE["press_wait"] is None


def test_a_question_played_before_is_pressed_again_and_the_order_never_goes_back(fol, monkeypatch):   # noqa: F811
    import asyncio

    from app import event_order
    from tests.test_ceremony_follow import F1, begin, hear, played

    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)

    async def go():
        await begin()
        hear(F1)
        await follow._decide()
        assert played(fol) == ["f1"]
        event_order._settle(event_order._scenes())
        last = event_order.STATE["last"]
        ceremony.adhoc_open(follow=True)                             # (what the keeper does once her line is over)
        hear(F1.replace("เอ็มม่า", ""))                             # told, not asked: not again
        await follow._decide()
        assert played(fol) == ["f1"]
        hear(F1.replace("เอ็มม่า", "") + "?")
        await follow._decide()
        return last, event_order.STATE["last"]

    before, after = asyncio.run(go())
    assert played(fol) == ["f1", "f1"] and before == after


def test_no_window_to_talk_opens_between_two_lines_said_as_one(fol, monkeypatch):      # noqa: F811
    # owner 24 Nov ~15:45 "พยายามให้มันเป็นบทเดียวกันเลย ไม่งั้นมันจะเปิดให้พูด"
    import asyncio
    import time as _time

    from app import event_order
    from tests.test_ceremony_follow import begin

    async def go():
        await begin()
        ceremony.adhoc_close("script")
        monkeypatch.setitem(event_order.STATE, "chain", {"state": "waiting", "lead": (2, 0), "follow": (2, 1)})
        await follow._tick()
        during = ceremony._adhoc_live(_time.time())
        monkeypatch.setitem(event_order.STATE, "chain", {"state": "pressed", "lead": (2, 0), "follow": (2, 1)})
        await follow._tick()
        return during, ceremony._adhoc_live(_time.time())

    during, after = asyncio.run(go())
    assert during is None and after is not None


def test_question_8_is_not_question_1_when_both_are_behind_the_order(real_lines, monkeypatch):
    # rehearsal 24 Nov 16:41: the order stood at scene 7; "...looking for a room for my family. How would you help?" had
    # both questions said whole, the tie fell to the last sentence alone ("How would you help?") and question 1 was played
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    done = ORDER[:12]
    said = "Support, I'm a customer looking for a room for my family. How would you help?"
    r = follow.decide_windows([said], done)
    assert r["verdict"] == "match" and r["cue"] == "s05_l03", r
    r = follow.decide_windows(["Emma, how can you help us?"], done)
    assert r["verdict"] == "match" and r["cue"] == "s02_l03"


def test_question_1_needs_him_to_ask_what_SHE_can_do(real_lines, monkeypatch):
    # scenario review 24 Nov: "how ... help" is all tokens() leaves of question 1 -- "How can I help you tonight?" played it
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    ids = ORDER[:5]
    for said in ("How can I help you tonight?", "How can we help our customers every day?", "That was how Emma can help us."):
        assert follow.decide_windows([said], ids)["verdict"] != "match", said
    for said in ("Emma, how can you help us?", "So Emma, what can you help us with?", "What can you do to help?"):
        r = follow.decide_windows([said], ids)
        assert r["verdict"] == "match" and r["cue"] == "s02_l03", said


def _script_mode(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_follow_answer_all", True)
    monkeypatch.setattr(settings, "ceremony_script_show_only", True)


async def _asked(said):
    from app import event_order
    from tests.test_ceremony_follow import hear

    event_order._settle(event_order._scenes())
    ceremony.adhoc_open(follow=True)                                 # (what the keeper does once her line is over)
    hear(said)
    await follow._decide()


def test_questions_out_of_order_and_then_back_again(fol, monkeypatch):                 # noqa: F811
    # owner 24 Nov 16:50: "เราต้องเผื่อกรณีที่เขาถามข้ามไปมานะ เพราะบางอาจจะถามอันนี้ก่อนแล้วกลับมาถามอันนี้ก็ได้"
    import asyncio

    from app import event_order
    from tests.test_ceremony_follow import F1, F3, F5, begin, played

    _script_mode(monkeypatch)

    async def go():
        await begin()
        await _asked(F3)                                             # the third first
        ahead = event_order.STATE["last"]
        await _asked(F1.replace("เอ็มม่า", ""))                     # back to the one he went past: no name, no "?"
        assert event_order.STATE["last"] == ahead                    # the order never goes back
        await _asked(F5)                                             # and on
        await _asked(F3 + "?")                                       # and one already said, asked again

    asyncio.run(go())
    assert played(fol) == ["f3", "f1", "f5", "f3"]


def test_a_walk_or_the_end_of_the_order_does_not_stop_a_question_asked_again(fol, monkeypatch):      # noqa: F811
    # rehearsal 24 Nov 16:46-16:48: the order stood at the walk to the globe -- every repeat was refused unheard, the logo
    import asyncio

    from app import event_order
    from tests.test_ceremony_follow import F1, F3, begin, played

    _script_mode(monkeypatch)
    done = lambda k: {"state": "done", "at": 0.0, "at_step": [int(x) for x in k.split(".")]}      # noqa: E731

    async def go():
        await begin()
        for k in ("2.0", "2.1", "2.2", "2.3", "2.4", "2.5"):
            event_order.STATE["marks"][k] = done(k)
        event_order.STATE["last"] = (2, 5)
        assert follow.pointer()["kind"] == "gate"
        await _asked(F3 + "?")
        event_order.STATE["marks"].update({k: done(k) for k in ("2.6", "2.7")})
        event_order.STATE["last"] = (2, 7)
        assert follow.pointer()["kind"] == "end"
        await _asked(F1 + "?")
        await _asked(F1.replace("เอ็มม่า", ""))                     # (and told, not asked, still nothing)

    asyncio.run(go())
    assert played(fol) == ["f3", "f1"]


def test_a_question_far_ahead_needs_to_be_asked_and_a_joke_her_name(real_lines, monkeypatch):
    from app import event_order

    monkeypatch.setattr(settings, "ceremony_english_only", True)
    marks = {event_order._key(1, i): {"state": "skipped"} for i in range(3)}
    monkeypatch.setitem(event_order.STATE, "marks", marks)
    monkeypatch.setitem(event_order.STATE, "last", (1, 2))
    near = [c["cue"] for c in follow.pointer_of(event_order._scenes(), marks, (1, 2), follow.lines(), read_past=True)["cands"]]
    go = lambda said: follow._elsewhere_match([said], near)                            # noqa: E731
    assert go("Please play the Embassy World story video.")[0]["cue"] == "s09_l02"
    assert go("Emma, please turn off the lobby lights.")[0]["cue"] == "s08_l06"
    assert go("Later we will all see how Emma can turn off the lobby lights.") is None  # told, not asked
    assert go("You remember the details very well.") is None                            # the joke: her name
    assert go("Wow, you remember the details very well, Emma.")[0]["cue"] == "s04_l09"
    # gone past and never said: his plain words are enough; being said right now: nothing
    marks["3.0"] = {"state": "skipped"}
    monkeypatch.setitem(event_order.STATE, "last", (7, 3))
    assert follow._elsewhere_match(["where our project is located"], [])[0]["cue"] == "s03_l03"
    marks["3.0"] = {"state": "running"}
    assert follow._elsewhere_match(["where is our project located?"], []) is None


def test_the_request_form_tells_asking_from_telling():
    for said in ("Where is our project located", "Emma, please print the quotation.", "Okay, what about our facilities.",
                 "Make our project different.", "I would like a room with a sea view.", "Can you tell us how many units there are",
                 "So many units, right? Emma", "And what about our facilities.", "So now please print the quotation."):
        assert follow._is_request(said), said
    for said in ("So now you know how many units we have and what types of rooms.", "That was how Emma can help us.",
                 "Emma just told us how our project is different.", "You really know the details, Emma.", ""):
        assert not follow._is_request(said), said


def test_one_word_of_a_question_counts_for_the_next_two_only(real_lines, monkeypatch):
    # rehearsal 24 Nov: "in the quotation", "Please pin the quotation", "Dashboard show." -- heard three times as no match
    monkeypatch.setattr(settings, "ceremony_english_only", True)
    i = ORDER.index("s05_l11")
    for said in ("is in the quotation", "Please pin the quotation.", "In the quotation"):
        r = follow.decide_windows([said], ORDER[i:i + 5])
        assert r["verdict"] == "match" and r["cue"] == "s05_l11", said
    assert follow.decide_windows(["is in the quotation"], ORDER[i - 2:i + 3])["verdict"] != "match"
    assert follow.decide_windows(["We have the quotation for our customers and the whole family ready"], ORDER[i:i + 5])["verdict"] != "match"
    j = ORDER.index("s07_l07")
    assert follow.decide_windows(["Dashboard show."], ORDER[j:j + 5])["cue"] == "s07_l07"


def test_question_one_is_said_whole_again_with_both_its_lines(real_lines, monkeypatch):
    from app import event_order

    flat = event_order._flat(event_order._scenes())
    # asked again later: the press of s02_l03 chains s02_l05 as it does the first time (the chain is the step's, not the mark's)
    step = event_order._find(event_order._scenes(), 2, 0)
    assert step["cue"] == "s02_l03" and step.get("chain") and (2, 1) in flat


def test_the_longer_question_wins_over_the_next_one_wherever_the_order_stands(real_lines, monkeypatch):
    # at question 1, question 8 asked first: the next five alone said "question 1" (its two words are inside question 8)
    import asyncio

    monkeypatch.setattr(settings, "ceremony_english_only", True)
    monkeypatch.setattr(settings, "ceremony_follow_host", True)
    _script_mode(monkeypatch)
    monkeypatch.setitem(follow.STATE, "on", True)
    monkeypatch.setitem(follow.STATE, "paused", False)
    monkeypatch.setattr(follow, "_recorded", lambda cue: False)
    cues = [{"n": 2, "i": k, "cue": c, "state": None, "behind": False} for k, c in enumerate(ORDER)]
    monkeypatch.setattr(follow, "_all_host_cues", lambda: cues)
    monkeypatch.setattr(follow, "pointer", lambda: {"kind": "host", "cands": [dict(c, host_th="") for c in cues[:5]],
                                                    "at": (2, 0), "label": None, "why_th": None})
    tried = []

    async def play(cand, res, by, skipped=None):
        tried.append((cand["cue"], [c["cue"] for c in skipped or []]))
        return {"ok": True}

    monkeypatch.setattr(follow, "_play", play)
    monkeypatch.setattr(follow, "_gate_why", lambda recorded=False: None)

    async def go():
        for k, said in enumerate(("I am a customer looking for a room for my family. How would you help?",
                                  "Emma, how can you help us?", "And what about our facilities, Emma?")):
            follow.STATE["utt"] = {k + 1.0: said}
            await follow._decide_inner()

    asyncio.run(go())
    assert tried == [("s05_l03", []), ("s02_l03", []), ("s04_l06", ["s02_l03", "s02_l09", "s03_l03", "s04_l03"])], tried


def test_a_continuation_does_not_follow_another_line_asked_again_since(fol, monkeypatch):                # noqa: F811
    # review 24 Nov: s05_l06 said, an earlier question asked again over its pause, and s05_l08 ("a quotation for this
    # unit?") came by itself after the answer about the location
    import asyncio
    import time

    from app import event_order
    from tests.test_ceremony_follow import begin

    _script_mode(monkeypatch)
    pressed = []

    async def play(cand, res, by, skipped=None):
        pressed.append((cand["cue"], by))
        return {"ok": True}

    monkeypatch.setattr(follow, "_play", play)
    monkeypatch.setattr(follow, "_gate_why", lambda recorded=False: None)
    mark = lambda k, ago: {"state": "done", "at": time.time() - ago, "at_step": k}       # noqa: E731

    async def go():
        await begin()
        event_order.STATE["marks"]["2.0"] = mark((2, 0), 20)
        event_order.STATE["last"] = (2, 0)
        follow.STATE.update(quiet_since=time.monotonic() - 5, heard_mono=None)
        event_order.STATE["marks"]["2.4"] = mark((2, 4), 3)                # another line, said since
        assert follow.pointer()["kind"] == "continues"
        assert await follow._maybe_continue(time.monotonic()) is False and not pressed
        event_order.STATE["marks"]["2.4"] = mark((2, 4), 60)               # said before it: the continuation goes
        assert await follow._maybe_continue(time.monotonic()) is True

    asyncio.run(go())
    assert pressed == [("f2", "continue")]


def test_question_9_is_one_answer_and_no_joke(real_lines):
    # owner 24 Nov 17:30 "จะอยู่ในคำถามที่ 9 ต่อเลย": the room, then "would you like me to prepare a quotation" with no pause
    from app import event_order

    scenes = event_order._scenes()
    assert event_order._chain_follower(scenes, 5, 1) == (5, 2)
    assert event_order._find(scenes, 5, 2)["cue"] == "s05_l08"
    setups = {cue for _p, _s, cue in follow._joke_pairs()}
    assert setups == {"s04_l09", "s06_l05"}                          # asked again it needs no name: it is no joke
