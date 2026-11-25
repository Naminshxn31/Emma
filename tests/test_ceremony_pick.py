"""Script-first slide picker (owner 24 Nov ~12:00, app/ceremony_pick.py + ceremony_follow `_pick_early` / `_refine`).

Scoring is tested on the REAL script / host lines / slides (pure); the wiring (early pick, her answer's refinement,
pointer rules, the hall's story gate, the topic path) with the `live` fixture of test_ceremony_follow_cues and a
scripted `pick`."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_follow as follow, ceremony_motion, ceremony_pick, ceremony_slides, event_order
from app import event_script, face_mirror
from app.config import settings
from tests.test_ceremony_follow import F1, F3, begin, fol, hear, played, run     # noqa: F401 -- fixtures
from tests.test_ceremony_follow_cues import _settle, live                    # noqa: F401 -- fixtures
from tests.test_event_order import REAL_SCRIPT, call, calls, event            # noqa: F401 -- fixtures


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(settings, "ceremony_file", str(REAL_SCRIPT))
    monkeypatch.setattr(settings, "stories_enabled", True)
    event_script.reset()
    ceremony_pick.reset()
    yield
    ceremony_pick.reset()
    event_script.reset()


# ---- the scoring, on the real script -------------------------------------------------------------

def test_a_partial_question_with_a_script_like_answer_picks_that_cue(real):
    ids = list(ceremony_pick.table()["cues"])
    assert "s04_l03" in ids and "s03_l03" in ids, " ".join(sorted(ids))
    near = set(ids[:3])
    en = event_script.cue("s04_l03")["en"]
    res = ceremony_pick.pick("Tell us about the units.", en, near)
    assert res["verdict"] == "pick" and res["cue"] == "s04_l03" and res["stage"] == "refine"
    loc = event_script.cue("s03_l03")["en"]
    assert ceremony_pick.pick("Where is it?", loc, near)["cue"] == "s03_l03"


def test_her_answer_switches_the_host_only_pick(real):
    ids = list(ceremony_pick.table()["cues"])
    near = set(ids[:3])
    ask = "Tell us about the units."
    host_only = ceremony_pick.rank(ask, None, near)[0][0]
    en = event_script.cue("s03_l03")["en"]                   # (her answer is about a different cue: the location)
    both = ceremony_pick.rank(ask, en, near)[0][0]
    assert host_only != "s03_l03" and both == "s03_l03"      # (her answer outweighs his few words)


def test_off_script_never_picks_a_script_slide(real):
    ids = list(ceremony_pick.table()["cues"])
    generic = "Of course, happy to help. I do not have that detail right now, please ask our sales team."
    for q in ("How tall is the building?", "Can you sing something for us?", "Thank you all for coming today.",
              "Is there parking for guests?", "What time does the fitness room open?"):
        assert ceremony_pick.pick(q, None, set(ids[:3]))["verdict"] == "none", q
        assert ceremony_pick.pick(q, generic, set(ids[:3]))["verdict"] == "none", q


def test_price_and_banter_never_pick(real):
    en = event_script.cue("s04_l03")["en"]
    assert ceremony_pick.pick("What is the price of the units?", en, set())["verdict"] == "none"
    assert ceremony_pick.pick("Emma, did you like the units?", en, set())["verdict"] == "none"


def test_jokes_manual_and_quotation_cues_are_not_candidates(real):
    ids = set(ceremony_pick.table()["cues"])
    assert not ids & {"s04_l09", "s04_l11", "s04_l13", "s06_l05", "s06_l07", "s05_l08", "s08_l06", "s08_l14"}


# ---- the wiring ------------------------------------------------------------------------------------

def _fake_pick(monkeypatch, results):
    """results: stage -> dict (cue, score, strong, verdict); records the calls."""
    seen = []

    def fake(host, answer=None, near=()):
        seen.append((host, answer, tuple(sorted(near))))
        base = results["refine" if answer else "early"]
        return {"verdict": "none", "cue": None, "score": 0.0, "second": 0.0, "strong": False,
                "stage": "refine" if answer else "early", **base}

    monkeypatch.setattr(ceremony_pick, "pick", fake)
    return seen


WORDS = "she answers close to the script with several content words here now"


def test_early_none_then_her_answer_shows_the_slide_without_moving_the_pointer(live, monkeypatch):
    _fake_pick(monkeypatch, {"early": {}, "refine": {"verdict": "pick", "cue": "f3", "score": 0.6}})

    async def go():
        await begin()
        await _settle(live)
        hear("tell us about the rooms")
        await follow._decide()
        assert live.sent == [] and follow.STATE["pick"] is not None
        follow.on_answer_words(ceremony.active_session(), WORDS)
        await asyncio.sleep(0.2)

    run(go())
    assert [s[1] for s in live.sent] == ["f3"]
    assert event_order.STATE["last"] in (None, (1, 0))                   # a topic-level pick: the pointer stays


def test_early_pick_then_a_different_refined_cue_switches_the_same_one_is_not_resent(live, monkeypatch):
    res = {"early": {"verdict": "pick", "cue": "f1", "score": 0.55},
           "refine": {"verdict": "pick", "cue": "f3", "score": 0.6}}
    _fake_pick(monkeypatch, res)

    async def go():
        await begin()
        await _settle(live)
        hear("tell us about it")
        await follow._decide()
        assert [s[1] for s in live.sent] == ["f1"]
        follow.on_answer_words(ceremony.active_session(), WORDS)
        await asyncio.sleep(0.2)
        assert [s[1] for s in live.sent] == ["f1", "f3"]
        follow.on_answer_done(ceremony.active_session())                  # the final refinement: same cue, nothing sent
        await asyncio.sleep(0.2)

    run(go())
    assert [s[1] for s in live.sent] == ["f1", "f3"]


def test_off_script_shows_no_script_slide_and_the_topic_slide_still_works(live, monkeypatch):
    _fake_pick(monkeypatch, {"early": {}, "refine": {}})

    async def go():
        await begin()
        await _settle(live)
        hear("Emma, what time does the gym open?")
        await follow._decide()
        follow.on_answer_words(ceremony.active_session(), WORDS)
        follow.on_answer_done(ceremony.active_session())
        await asyncio.sleep(0.2)
        ceremony_motion.STATE.update(slides={"facilities": {"template": "act", "cue": "s06_l03"}}, ready=True,
                                     script_first=False)
        cap = SimpleNamespace(text="Emma, do you have a gym or a sauna?", at=1e9)
        ceremony_motion._decide_now(ceremony.active_session(), cap)

    run(go())
    assert [s[1] for s in live.sent] == ["s06_l03"]


def test_a_strong_pick_among_the_next_cues_marks_it_and_skips_the_ones_before(live, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.8, "strong": True}, "refine": {}})

    async def go():
        await begin()
        await _settle(live)
        hear("so the rooms")
        await follow._decide()

    run(go())
    assert [s[1] for s in live.sent] == ["f3"]
    marks = event_order.STATE["marks"]
    assert marks["2.2"]["state"] == "done" and marks["2.0"]["state"] == "skipped"


def test_a_refused_mark_shows_no_slide(live, monkeypatch):
    async def refuse(n, i):
        return {"ok": False, "error": "running"}

    monkeypatch.setattr(event_order, "mark_shown", refuse)

    async def go():
        await begin()
        await _settle(live)
        hear(F1)
        await follow._decide()                                            # a scripted line: mark first, no slide when refused

    run(go())
    assert live.sent == []


def test_slides_go_on_the_loop_not_a_worker_thread(live, monkeypatch):
    import threading

    where = []
    monkeypatch.setattr(face_mirror, "motion", lambda slide, cue: where.append(threading.current_thread().name))

    async def go():
        await begin()
        await _settle(live)
        where.clear()
        hear(F1)
        await follow._decide()
        return threading.current_thread().name

    loop_thread = run(go())
    assert where == [loop_thread]


def test_cues_on_and_off_switch_leave_the_picker_alone(live, monkeypatch):
    seen = _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.9}, "refine": {}})
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)

    async def go():
        await begin()
        hear("tell us about the rooms")
        await follow._decide()
        follow.on_answer_words(ceremony.active_session(), WORDS)

    run(go())
    assert seen == [] and follow.STATE["pick"] is None and follow.STATE["ans"] == ""
    # the switch off: the session's hook never reaches follow
    monkeypatch.setattr(settings, "ceremony_follow_host", False)
    monkeypatch.setattr(follow, "on_answer_words", lambda *a: pytest.fail("called"))
    from app.session import VoiceSession
    VoiceSession._note_ceremony_answer(SimpleNamespace(), "words words words")


# ---- the hall: no second motion for what the script covers -----------------------------------------------

def _story(topic):
    return {"type": "tool_result", "result": {"ok": True, "screen": "story", "topic": topic, "story": {"x": 1}}}


def test_a_story_the_script_covers_is_not_on_the_hall_an_uncovered_one_is(live, monkeypatch):
    monkeypatch.setattr(follow, "script_story_ids", lambda: {"location_beach"})
    monkeypatch.setitem(face_mirror._state, "profile", "ceremony")

    async def go():
        await begin()
        assert face_mirror.view(_story("location_beach")) is None          # the cue's slide is the answer
        assert face_mirror.view(_story("airport_distance"))["result"]["screen"] == "story"
        monkeypatch.setattr(settings, "ceremony_follow_cues", True)         # cues on: as today
        assert face_mirror.view(_story("location_beach")) is not None
        monkeypatch.setattr(settings, "ceremony_follow_cues", False)
        monkeypatch.setitem(face_mirror._state, "profile", "event")        # another profile: as today
        assert face_mirror.view(_story("location_beach")) is not None
        monkeypatch.setitem(face_mirror._state, "profile", "ceremony")
        monkeypatch.setattr(settings, "ceremony_follow_host", False)       # the switch off: as today
        assert face_mirror.view(_story("location_beach")) is not None

    run(go())


def test_the_real_order_names_the_stories_the_script_covers(real):
    assert {"location_beach", "unit_count"} <= follow.script_story_ids()


def test_a_story_is_covered_only_when_its_cue_has_a_slide_the_picker_can_show(real, monkeypatch):
    """Review 24 Nov: a cue's `show` story was suppressed on the hall even when that cue has no servable slide in
    the picker -- the hall went blank. Covered = the show of a cue that IS in the picker's table."""
    shows = {st["cue"]: str(st["show"]) for sc in event_script.order() for st in sc["steps"]
             if st.get("do") == "cue" and st.get("show")}
    table = set(ceremony_pick.table()["cues"])
    assert follow.script_story_ids() == {s for c, s in shows.items() if c in table}
    # the cue loses its slide: its story is the hall's again
    cue = "s03_l03"
    assert shows[cue] in follow.script_story_ids()
    real_table = ceremony_pick.table()
    monkeypatch.setattr(ceremony_pick, "table",
                        lambda: {**real_table, "cues": {k: v for k, v in real_table["cues"].items() if k != cue}})
    assert shows[cue] not in follow.script_story_ids() and "unit_count" in follow.script_story_ids()


def test_the_old_topic_slides_the_script_covers_are_dropped_in_live_first(live, monkeypatch):
    monkeypatch.setattr(ceremony_pick, "table", lambda: {"cues": {"s03_l03": {}, "s04_l03": {}, "s06_l03": {}}})

    async def go():
        await begin()
        return ceremony_motion._prepare()

    slides = run(go())
    assert slides["location"] is None and slides["units"] is None and slides["facilities"] is None
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)            # cues on: the topics are as they were

    async def again():
        return ceremony_motion._prepare()

    assert run(again())["facilities"] is not None


# ---- the nudge: his lead-in matched, no question, she does not start -----------------------------------------

@pytest.fixture
def nudges(live, monkeypatch):
    from app import events

    sent = []

    async def fake(text, **kw):
        sent.append((text, kw.get("source"), kw["still_relevant"]() if kw.get("still_relevant") else True))
        return True

    monkeypatch.setattr(events, "announce", fake)
    monkeypatch.setattr(follow, "NUDGE_AFTER_S", 0.15)
    return sent


def test_a_matched_lead_in_with_no_reply_gets_exactly_one_nudge_for_that_cue(nudges):
    async def go():
        await begin()
        await _settle(live=None)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert len(nudges) == 1
    text, source, relevant = nudges[0]
    assert source == "follow_nudge" and "f1" in text and relevant is True
    assert F1 not in text and "ที่ตั้ง" not in text                       # never his words


def test_she_started_replying_so_no_nudge(nudges):
    async def go():
        await begin()
        await _settle(live=None)
        hear(F1)
        await follow._decide()
        follow.on_answer_words(ceremony.active_session(), "Of course")      # her first words, inside the delay
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_no_match_no_nudge(nudges, monkeypatch):
    _fake_pick(monkeypatch, {"early": {}, "refine": {}})

    async def go():
        await begin()
        await _settle(live=None)
        hear("thank you everyone for coming today")
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_a_strong_pick_nudges_once_a_weak_one_never(nudges, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.8, "strong": True}, "refine": {}})

    async def go():
        await begin()
        await _settle(live=None)
        hear("so the rooms")
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert [n[0].count("f3") for n in nudges] == [1]


def test_a_weak_pick_never_nudges(nudges, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.55, "strong": False}, "refine": {}})

    async def go():
        await begin()
        await _settle(live=None)
        hear("tell us about the rooms")
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_cues_on_sends_no_nudge(nudges, monkeypatch):
    monkeypatch.setattr(settings, "ceremony_follow_cues", True)

    async def go():
        await begin()
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def _host_gate(in_speech):
    """The ceremony call's provider with a VAD gate (what `_host_speaking` reads)."""
    gate = SimpleNamespace(in_speech=in_speech)
    follow.STATE["session"].provider = SimpleNamespace(_vad_gate=gate)
    return gate


def test_he_speaks_again_inside_the_delay_so_no_nudge(nudges):
    """Review 24 Nov (A): a second utterance inside the delay is a new caption -- the old lead-in's nudge is void."""
    async def go():
        await begin()
        await _settle(live=None)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.05)
        hear("and one more thing everybody")
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


@pytest.mark.parametrize("what", ["paused", "window"])
def test_follow_paused_or_the_window_shut_inside_the_delay_so_no_nudge(nudges, monkeypatch, what):
    async def go():
        await begin()
        await _settle(live=None)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.05)
        if what == "paused":
            follow.STATE["paused"] = "staff"
        else:
            monkeypatch.setattr(ceremony, "_adhoc_live", lambda now: None)
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_the_host_is_speaking_now_so_no_nudge_and_the_announce_recheck_sees_it(nudges):
    async def go():
        await begin()
        await _settle(live=None)
        gate = _host_gate(False)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.05)
        gate.in_speech = True                                              # a new utterance has begun: no caption yet
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_the_announce_asks_again_when_it_is_about_to_send(live, monkeypatch):
    """The relevance callback handed to events.announce is the live check: he starts talking while announce waits."""
    from app import events

    seen = []
    gate = {}

    async def fake(text, **kw):
        gate["g"].in_speech = True                                         # (during announce's own waits)
        seen.append(kw["still_relevant"]())
        return True

    monkeypatch.setattr(events, "announce", fake)
    monkeypatch.setattr(follow, "NUDGE_AFTER_S", 0.1)

    async def go():
        await begin()
        await _settle(live=None)
        gate["g"] = _host_gate(False)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.5)

    run(go())
    assert seen == [False]


def test_a_press_gate_up_inside_the_delay_so_no_nudge(nudges, monkeypatch):
    from app import event_walk

    async def go():
        await begin()
        await _settle(live=None)
        hear(F1)
        await follow._decide()
        await asyncio.sleep(0.05)
        monkeypatch.setattr(event_walk, "walking", lambda: True)           # the robot is moving: gate "moving"
        await asyncio.sleep(0.5)

    run(go())
    assert nudges == []


def test_the_nudge_belongs_to_the_utterance_the_pick_answered_not_a_later_read(live, monkeypatch):
    """Review 24 Nov (4): `_show_pick` hands the nudge the heard_at it started with; the pointer's awaits may pass a
    newer caption's time, and the race re-check keeps a stale slide off the hall."""
    got = []

    async def spy(cue, stamp, heard_at):
        got.append(heard_at)

    monkeypatch.setattr(follow, "_nudge", spy)
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.8, "strong": True}, "refine": {}})
    real_mark = event_order.mark_shown

    async def slow_mark(n, i):
        follow.STATE["heard_mono"] = 123.0                                 # a later caption's time, pick untouched
        return await real_mark(n, i)

    monkeypatch.setattr(event_order, "mark_shown", slow_mark)

    async def go():
        await begin()
        await _settle(live)
        hear("so the rooms")
        first = follow.STATE["heard_mono"]
        await follow._decide()
        await asyncio.sleep(0.1)
        return first

    first = run(go())
    assert got == [first]


def test_he_speaks_during_the_marks_awaits_so_no_stale_slide(live, monkeypatch):
    _fake_pick(monkeypatch, {"early": {"verdict": "pick", "cue": "f3", "score": 0.8, "strong": True}, "refine": {}})
    real_mark = event_order.mark_shown

    async def slow_mark(n, i):
        result = await real_mark(n, i)
        hear("wait, one more thing")                                       # new caption: pick cleared
        return result

    monkeypatch.setattr(event_order, "mark_shown", slow_mark)

    async def go():
        await begin()
        await _settle(live)
        hear("so the rooms")
        await follow._decide()

    run(go())
    assert live.sent == []


# ---- timing: the slide is on the hall screen by her first audio ---------------------------------------------------

class _Mirror(asyncio.Queue):
    def __init__(self):
        super().__init__()
        self.at = []

    def put_nowait(self, item):
        import time
        if isinstance(item, str) and "mirror_motion" in item and '"slide"' in item:
            self.at.append(time.monotonic())
        super().put_nowait(item)


def _timed(live, monkeypatch, text, *, picked=None):
    """Host utterance end = his last caption; her first audio is taken at +1.2 s (the typical 1-2 s, the early end)."""
    import time

    if picked:
        _fake_pick(monkeypatch, {"early": picked, "refine": {}})
    import json

    def queued(slide, cue):                    # (what face_mirror.motion queues to a connected screen)
        face_mirror._put(json.dumps({"type": "mirror_motion", "id": cue, "slide": slide}))

    monkeypatch.setattr(face_mirror, "motion", queued)
    out = {}

    async def go():
        await begin()
        await _settle(live=None)
        q = _Mirror()
        face_mirror._clients.add(q)
        try:
            end = time.monotonic()
            cap = SimpleNamespace(text=text, at=end, utterance=None)
            follow.on_host_words(ceremony.active_session(), cap)           # the real QUIET_S timer decides
            for _ in range(60):
                await asyncio.sleep(0.05)
                if q.at:
                    break
            out["queued_after_end_s"] = (q.at[0] - end) if q.at else None
        finally:
            face_mirror._clients.discard(q)

    run(go())
    return out


def test_a_scripted_line_is_on_the_hall_screen_before_her_first_audio(live, monkeypatch):
    out = _timed(live, monkeypatch, F1)
    print("scripted line: host end -> slide queued %.2f s (her first audio at ~1.0-2.0 s)" % out["queued_after_end_s"])
    assert out["queued_after_end_s"] is not None and out["queued_after_end_s"] <= 1.2


def test_the_early_pick_does_not_wait_for_the_tail_grace(live, monkeypatch):
    part = "โครงการของเรามีกี่ยูนิตและมีห้องแบบ"                    # a lead-in cut short: 'no_tail' waits 1.3 s more
    assert follow.decide_windows([part], ["f1", "f3", "f6"])["verdict"] == "no_tail"   # (it really is the grace path)
    out = _timed(live, monkeypatch, part, picked={"verdict": "pick", "cue": "f3", "score": 0.55})
    print("early pick: host end -> slide queued %.2f s (the tail grace would be %.1f s later)" %
          (out["queued_after_end_s"], follow.TAIL_GRACE_S))
    assert out["queued_after_end_s"] is not None and out["queued_after_end_s"] <= 1.2
