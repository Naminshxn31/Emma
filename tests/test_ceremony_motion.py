""""ตอบสด"'s topic slides on the event screen (owner 23 Nov 2026: "ทำเลย ใช้เอเจนท์ตอบสดตัวเดิม").

The host off-script: when his transcribed words match a topic of a motion slide the ceremony already has,
that slide goes up settled on the event screen; otherwise the show's own logo loop (app/ceremony_motion.py).
What these hold down:

* the SERVER decides from the host's words, never the model; Thai as words (event_script's dictionary),
  never substrings; no match beats a weak or tied one; price, lights, curtains, video, lucky-draw words
  never get a slide; measured on two sets written blind (B never tuned on): no wrong-topic slide;
* every cue of the ceremony is either a topic's slide or excluded with its reason -- jokes, quotation,
  lights/curtains, the closing thanks are never shown;
* only inside the ตอบสด window, only on the ceremony call; the logo when it opens; a press of the script
  takes ours down (clear at press); a short "wow" keeps the slide, a new unrecognised question takes it down;
* only slides ceremony_slides would serve: a draft file (today) = the logo, the preview alone shows drafts;
* face_mirror: the server's own message (the face's stream cannot make one), replayed to a joining screen
  until a line's subtitle replaces it, cut by the red stop; the session feeds the host's current caption.
The page's side runs on the real page in Chromium: scripts/preview_ceremony_motion.py --check.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import ceremony, ceremony_motion, ceremony_slides, face_mirror
from app.config import settings
from tests.ceremony_app import ceremony_off, ceremony_on
from tests.test_reply_stall import rows  # noqa: F401 -- the turn log, captured

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "data" / "ceremony" / "floor1_ceremony.json"
SLIDE = {"template": "act", "act": "units_views", "texts": {}, "figures": {}, "pictures": {}, "beats": []}


def _cases(name: str) -> list[dict]:
    return json.loads((ROOT / "data" / "eval" / ("ceremony_motion_cases_%s.json" % name)).read_text(
        encoding="utf-8"))["cases"]


# ---- the matcher ------------------------------------------------------------------------------------

#: The sets' known wrong-topic slides: C's one, scored blind after review round 1 and left as measured
#: ("my son's school is far away from here" -> the map; c30) -- not tuned on, so C stays an honest number.
KNOWN_WRONG = {"a": set(), "b": set(), "c": {"c30"}}


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_no_wrong_topic_slide_on_the_blind_sets(name):
    """Written blind by separate agents from the slides' descriptions; A tuned on, B after that tuning, C after
    review round 1 -- neither tuned on (scripts/eval_ceremony_motion.py prints the whole table)."""
    from scripts.eval_ceremony_motion import score

    s = score(_cases(name))
    assert {m["id"] for m in s["misses"] if m["kind"].startswith("wrong_topic")} == KNOWN_WRONG[name], s["misses"]
    floor = {"a": 17, "b": 16, "c": 21}[name]           # 23 Nov, round 2: A 17/18, B 16/24, C 21/24
    assert s["hit"] >= floor, s


@pytest.mark.parametrize("text,topic", [
    ("Emma, how far is the project from Jomtien Beach?", "location"),
    ("ทำเลอยู่ใกล้ทะเลไหมครับ", "location"),
    ("How many units are there, studios and one bedroom?", "units"),
    ("มีห้องนอนกี่แบบ สตูดิโอมีไหม", "units"),
    ("Tell us about the swimming pool, Emma", "facilities"),
    ("ส่วนกลางมีฟิตเนสกับซาวน่าไหม", "facilities"),
    ("Are there any schools or hospitals nearby?", "location"),     # everyday nouns WITH a second cue
    ("Is it far from the beach?", "location"),
    ("Do you have a gym?", "facilities"),                           # "do you have" = the project, not her
    ("Does the project have smart home technology?", "technology"),
    ("สมาร์ตโฮมใช้แอพอะไรครับ", "technology"),            # the other spellings
])
def test_a_clear_topic_gets_its_slide(text, topic):
    assert ceremony_motion.decide(text) == (topic, "match")


@pytest.mark.parametrize("text,why", [
    ("Emma, what's the price of a studio unit?", "never"),         # a price never looks answered on screen
    ("ราคาห้องนอนเริ่มต้นเท่าไหร่", "never"),
    ("Can you turn the lights off, Emma?", "never"),
    ("ปิดม่านหน่อย", "never"),
    ("Who wins the lucky draw by the pool?", "never"),
    ("Is the pool near the beach?", "tie"),                         # two topics: unsure = none
    ("Emma you're so smart", "weak"),                               # praise, not technology
    ("So happy to be here tonight!", "nothing"),                   # "app" is not inside "happy"
    ("เอ็มม่าเป็นอิสระไหม", "nothing"),                              # สระ is not inside อิสระ
    ("ขอบคุณทุกท่านครับ", "nothing"),
    # review 23 Nov: banter that names a place or a thing -- about her, or an everyday word alone
    ("Emma, did you go to school?", "about_her"),
    ("Are you a robot unit?", "about_her"),
    ("Do you like shopping?", "about_her"),
    ("Have you been to Pattaya before?", "about_her"),
    ("เอ็มม่าเคยไปโรงเรียนไหม", "about_her"),
    ("ชอบไปห้างไหม", "about_her"),
    ("What a beautiful day at the beach yesterday", "weak"),
    ("How many people are in the room tonight?", "weak"),
    ("ห้องวิวทะเลมีไหม", "weak"),                                    # a room AND the sea: neither enough
    ("Tell us about the swimming pool and the gym, and the studio units", "tie"),   # 7 vs 4: still two topics
])
def test_no_match_is_the_logo(text, why):
    assert ceremony_motion.decide(text) == (None, why)


def test_every_cue_is_a_topic_or_excluded_with_its_reason():
    cues = [c["id"] for c in json.loads(SCRIPT.read_text(encoding="utf-8"))["cues"]]
    topics = {t["cue"] for t in ceremony_motion.TOPICS.values() if t["cue"]}
    assert not topics & set(ceremony_motion.EXCLUDED)
    assert set(cues) == topics | set(ceremony_motion.EXCLUDED), set(cues) ^ (topics | set(ceremony_motion.EXCLUDED))
    for cue in ("s04_l11", "s04_l13", "s06_l07", "s05_l08", "s05_backup_quote", "s08_l06", "s08_l14", "s10_l05"):
        assert cue in ceremony_motion.EXCLUDED and ceremony_motion.EXCLUDED[cue]
    assert all(row["reason"] for row in ceremony_motion.table())


# ---- inside the window, the event screen --------------------------------------------------------------

@pytest.fixture
def screen(monkeypatch):
    """A room screen (face_mirror's queue) and a ceremony call; the slides served as if signed."""
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    monkeypatch.setattr(ceremony_slides, "for_cue", lambda cue, lang="en", drafts=False: dict(SLIDE, cue=cue))
    call = ceremony_on(replies="cues", session=SimpleNamespace(_robot_face_claim=True))
    yield queue, call
    face_mirror._clients.discard(queue)
    face_mirror.reset()


def _got(queue) -> list[dict]:
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item))
    return out


def _say(call, text, at=None):
    ceremony_motion.on_host_words(call, SimpleNamespace(text=text, at=time.monotonic() if at is None else at))


def test_the_window_opens_on_the_logo_and_the_hosts_topic_goes_up(screen, rows):
    queue, call = screen
    _say(call, "Emma, where is the project? Near Jomtien Beach?")
    assert _got(queue) == [], "no ตอบสด window: nothing"
    assert ceremony.adhoc_open()["ok"]
    assert _got(queue) == [{"type": "mirror_motion"}]                         # the logo loop
    _say(call, "Emma, where is the project? Near Jomtien Beach?", at=1.0)
    sent = _got(queue)
    assert sent == [{"type": "mirror_motion", "id": "s03_l03", "slide": dict(SLIDE, cue="s03_l03")}]
    _say(call, "Emma, where is the project? Near Jomtien Beach? And the schools?", at=1.0)
    assert _got(queue) == [], "the same topic is not sent again"
    _say(call, "Wow, great.", at=2.0)
    assert _got(queue) == [], "a short reaction keeps it"
    _say(call, "What time does the party start tonight?", at=2.5)
    assert _got(queue) == [{"type": "mirror_motion"}], "a new question nobody recognised: the logo"
    _say(call, "And how many units are there?", at=2.8)
    assert _got(queue)[0]["id"] == "s04_l03"
    _say(call, "Emma, are you nervous standing up here today?", at=3.0)
    assert _got(queue) == [{"type": "mirror_motion"}], "a question about her: the logo"
    _say(call, "And how many units are there?", at=4.0)
    assert _got(queue)[0]["id"] == "s04_l03"
    _say(call, "Does the project have smart home technology?", at=5.0)
    assert _got(queue) == [{"type": "mirror_motion"}], "technology: its line settles on the logo -- no slide"
    _say(call, "Does the project have smart home technology? Which app?", at=5.0)
    assert _got(queue) == []
    rows_ = [r for r in rows if r["event"] == "ceremony_motion"]
    assert [r["shown"] for r in rows_] == ["logo", "s03_l03", "logo", "s04_l03", "logo", "s04_l03", "logo"]
    assert [r["why"] for r in rows_] == ["open", "match", "nothing", "match", "about_her", "match",
                                         "topic_without_slide"]
    assert all(set(r) == {"event", "shown", "why"} for r in rows_), "no words of anybody's in the log"


def test_one_utterance_that_grows_keeps_its_slide(screen):
    queue, call = screen
    assert ceremony.adhoc_open()["ok"]
    _got(queue)
    _say(call, "Tell us about the swimming pool", at=5.0)
    assert _got(queue)[0]["id"] == "s06_l03"
    _say(call, "Tell us about the swimming pool, Emma, and what else you can tell our lovely guests", at=5.0)
    assert _got(queue) == [], "the same question's words: it stays"


def test_a_press_of_the_script_takes_ours_down_and_nothing_outside_the_window(screen, monkeypatch):
    queue, call = screen
    monkeypatch.setattr(ceremony_motion, "PRESS_LINE_WAIT_S", 0.05)
    assert ceremony.adhoc_open()["ok"]
    _say(call, "How many bedrooms do the units have?", at=1.0)
    assert _got(queue)[-1]["id"] == "s04_l03"

    async def press(why="script", line=False):
        async with ceremony.script_press(why):
            if line:
                face_mirror.subtitle({"id": "s05_l03", "text": "คำ"})          # the pressed line goes out
        await asyncio.sleep(0.15)

    asyncio.run(press())
    assert _got(queue) == [{"type": "mirror_motion"}], "a press with no line: the logo"
    asyncio.run(press())
    assert _got(queue) == [], "nothing of ours up: nothing sent"
    _say(call, "Tell us about the swimming pool", at=2.0)
    assert _got(queue) == [], "the window is shut: the host's words drive nothing"
    other = SimpleNamespace(_robot_face_claim=True)
    assert ceremony.adhoc_open()["ok"]
    _got(queue)
    _say(other, "Tell us about the swimming pool", at=3.0)
    assert _got(queue) == [], "not the ceremony's call"
    _say(call, "Tell us about the swimming pool", at=4.0)
    assert _got(queue)[-1]["id"] == "s06_l03"
    asyncio.run(press(line=True))
    assert [m["type"] for m in _got(queue)] == ["mirror_subtitle"], "the line's own replaces it: no logo between"
    assert ceremony.adhoc_open()["ok"]
    _got(queue)
    _say(call, "Tell us about the swimming pool", at=5.0)
    assert _got(queue)[-1]["id"] == "s06_l03"
    asyncio.run(press("reset"))
    assert _got(queue) == [{"type": "mirror_motion"}], "a reset of the order takes it down too"


def test_a_price_two_topics_or_banter_take_a_slide_down_even_short_or_in_the_same_breath(screen):
    """Review 23 Nov: `never`/`tie` were treated like "wow" -- the slide stayed under "How much?", and under a
    question that grew into two topics."""
    queue, call = screen
    assert ceremony.adhoc_open()["ok"]
    _got(queue)
    for at, (first, then) in enumerate([
            ("Tell us about the swimming pool", "How much?"),                            # short, a new one
            ("Tell us about the swimming pool", "ราคาเท่าไหร่"),
            ("Tell us about the swimming pool", "Units or pool?"),                       # short tie
            ("Tell us about the studio units", "Emma, did you like it?")], start=1):     # about her
        _say(call, first, at=at * 10.0)
        assert _got(queue)[-1].get("slide"), first
        _say(call, then, at=at * 10.0 + 1)
        assert _got(queue) == [{"type": "mirror_motion"}], then
    # the same utterance growing into a price, a second topic, or out of a half-said word
    for at, (first, grown) in enumerate([
            ("The studio units", "The studio units, how much do they cost?"),
            ("Which is better, the gym", "Which is better, the gym or the sea view from the units?"),
            ("เอ็มม่าไปสระ", "เอ็มม่าไปสระบุรี")], start=10):                           # สระ|บุรี
        _say(call, first, at=at * 10.0)
        assert _got(queue)[-1].get("slide"), first
        _say(call, grown, at=at * 10.0)
        assert _got(queue) == [{"type": "mirror_motion"}], grown


def test_his_words_are_decided_once_they_stop_growing(screen, monkeypatch, rows):
    """Review 23 Nov: decided on every partial caption, "Which is better, the gym" put the facilities slide up
    before the rest made it a tie. On the voice loop the decision waits QUIET_S after the last piece."""
    queue, call = screen
    monkeypatch.setattr(ceremony_motion, "QUIET_S", 0.2)

    async def run():
        assert ceremony.adhoc_open()["ok"]                                   # (the slides in a thread)
        await asyncio.sleep(0.1)
        said = ""
        for piece in ["Which is better, the gym", " or the sea view", " from the units?"]:
            said += piece
            ceremony_motion.on_host_words(call, SimpleNamespace(text=said, at=1.0))
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.35)
        tie = _got(queue)
        said = ""
        for piece in ["Tell us", " about the swimming", " pool, Emma"]:
            said += piece
            ceremony_motion.on_host_words(call, SimpleNamespace(text=said, at=2.0))
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.35)
        pool = _got(queue)
        ceremony_motion.on_host_words(call, SimpleNamespace(text="And how many units are there?", at=3.0))
        async with ceremony.script_press():                                  # pressed before the quiet ran out
            pass
        await asyncio.sleep(0.35)
        return tie, pool

    tie, pool = asyncio.run(run())
    assert tie == [{"type": "mirror_motion"}], "the open's logo, and nothing for a question that is two topics"
    assert [m.get("id") for m in pool] == ["s06_l03"], "one decision, once he stopped"
    assert [r["shown"] for r in rows if r["event"] == "ceremony_motion"][-1] == "s06_l03", \
        "a decision still waiting when the line is pressed is dropped"


def test_a_slide_coming_up_ends_a_debut_on_the_server_too(monkeypatch):
    stopped = []
    monkeypatch.setattr(face_mirror, "_stopped", lambda why: stopped.append(why))
    face_mirror.motion(dict(SLIDE), "s04_l03")
    face_mirror.motion(None, None)
    assert stopped == ["slide"]
    face_mirror.reset()


def test_a_draft_slides_file_shows_the_logo_and_only_the_preview_shows_drafts(rows, monkeypatch):
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    try:
        calls = []
        monkeypatch.setattr(ceremony_slides, "for_cue",
                            lambda cue, lang="en", drafts=False: calls.append(drafts) or (SLIDE if drafts else None))
        call = ceremony_on(replies="cues", session=SimpleNamespace(_robot_face_claim=True))
        assert ceremony.adhoc_open()["ok"]
        _say(call, "Tell us about the swimming pool", at=1.0)
        assert calls == [False, False, False], "the window's slides, once each at the open; signed only"
        assert _got(queue) == [], "no slide servable: nothing at all on the event screen, not even the logo"
        assert [(r["shown"], r["why"]) for r in rows if r["event"] == "ceremony_motion"] == [("none", "no_slides")]
        assert ceremony_motion.wants_words(call) is False, "the session does not even read his captions"

        async def press():
            async with ceremony.script_press():
                pass
            await asyncio.sleep(0.05)

        asyncio.run(press())
        assert _got(queue) == [] and len(calls) == 3, "a press: nothing sent, nothing read"
        ceremony_motion.DRAFTS = True                                          # the preview on this PC
        assert ceremony.adhoc_open()["ok"]
        assert _got(queue) == [{"type": "mirror_motion"}] and calls[-1] is True    # slides now: the logo
        _say(call, "Tell us about the swimming pool", at=2.0)
        assert _got(queue)[-1]["id"] == "s06_l03"
    finally:
        ceremony_motion.DRAFTS = False
        face_mirror._clients.discard(queue)
        face_mirror.reset()


def test_the_real_slides_file_is_still_a_draft_so_the_robot_shows_the_logo():
    """Today's file (23 Nov): unsigned -- what the robot does now. Signing it is the owner's."""
    doc = ceremony_slides.load()
    if doc is not None and ceremony_slides.approved(doc):
        pytest.skip("the slides file is signed now: the topic slides go up on the robot -- this test is not checking the draft case")
    assert all(ceremony_slides.for_cue(t["cue"]) is None for t in ceremony_motion.TOPICS.values() if t["cue"])


# ---- face_mirror and the session ---------------------------------------------------------------------

def test_the_message_is_the_servers_own_and_a_joining_screen_gets_it_until_a_line_replaces_it():
    face_mirror.reset()
    face_mirror.call_started()
    face_mirror.motion(dict(SLIDE), "s04_l03")
    snap = [json.loads(x) for x in face_mirror.snapshot()]
    assert snap[-1] == {"type": "mirror_motion", "id": "s04_l03", "slide": SLIDE}
    face_mirror.subtitle({"id": "s05_l03", "text": "คำ"})                      # the next line of the script
    assert all(x.get("type") != "mirror_motion" for x in map(json.loads, face_mirror.snapshot()))
    face_mirror.subtitle_end("s05_l03")                                       # ...and once it has ended
    assert all(x.get("type") != "mirror_motion" for x in map(json.loads, face_mirror.snapshot()))
    face_mirror.motion(dict(SLIDE), "s04_l03")
    face_mirror.subtitle_cut()                                                # the red stop
    assert all(json.loads(x).get("type") != "mirror_motion" for x in face_mirror.snapshot())
    face_mirror.motion({"template": 5}, "s04_l03")                            # not a slide: the logo
    assert face_mirror._state["motion"] is None
    assert face_mirror.view({"type": "mirror_motion", "id": "s04_l03", "slide": SLIDE}) is None   # the face's stream
    face_mirror.reset()


def test_the_sales_robots_captions_go_nowhere_and_the_ceremonys_only_inside_the_window(monkeypatch, rows):
    """session._note_ceremony_words runs on EVERY caption of every robot-face call -- the sales robot's
    too (coordinator 24 Nov). The gate is asked first: not the ceremony app's call, or no ตอบสด window =
    the caption is not even read, nothing decided, nothing sent, no row."""
    import inspect
    import types

    from app import heard
    from app.session import VoiceSession

    seen = []
    real_caption = heard.latest_caption
    monkeypatch.setattr(heard, "latest_caption", lambda *a, **k: seen.append("read") or real_caption(*a, **k))
    monkeypatch.setattr(ceremony_motion, "on_host_words", lambda s, c: seen.append(("decide", c.text)))
    monkeypatch.setattr(ceremony_slides, "for_cue", lambda cue, lang="en", drafts=False: dict(SLIDE, cue=cue))
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    try:
        utterances = iter(range(100))

        def hears(session, text):
            heard.record(text, session=session, utterance_id="u%d" % next(utterances))     # each a new one
            VoiceSession._note_ceremony_words(session)

        sales = types.SimpleNamespace(_robot_face_claim=True, profile="condo")
        hears(sales, "Tell us about the swimming pool")                          # the sales robot, no ceremony
        cer = ceremony_on(replies="cues", session=types.SimpleNamespace(_robot_face_claim=True, profile="ceremony"))
        hears(sales, "Tell us about the swimming pool")                          # a ceremony on, not this call
        hears(cer, "Tell us about the swimming pool")                            # the ceremony's call, no window
        assert seen == [] and _got(queue) == []
        assert not [r for r in rows if r["event"] == "ceremony_motion"]
        assert ceremony.adhoc_open()["ok"]
        hears(sales, "Tell us about the swimming pool")                          # the window is the ceremony's
        assert seen == []
        hears(cer, "Tell us about the swimming pool")
        assert seen == ["read", ("decide", "Tell us about the swimming pool")]
    finally:
        face_mirror._clients.discard(queue)
        face_mirror.reset()
    loop = inspect.getsource(VoiceSession)
    at = loop.index("heard.record(event.text or \"\", session=self,")
    assert loop.index("self._note_ceremony_words()", at) < loop.index("await self._send_json(payload)", at)
    body = inspect.getsource(VoiceSession._note_ceremony_words)
    assert body.index("wants_words(self)") < body.index("latest_caption"), "the gate before the caption is read"


def test_the_hosts_words_are_never_logged_or_sent(screen, rows, caplog):
    """Only a cue id and a slide leave this module, and turnlog rows carry shown/why -- never his words."""
    queue, call = screen
    caplog.set_level("DEBUG")
    assert ceremony.adhoc_open()["ok"]
    for k, text in enumerate(["Tell us about the swimming pool, Khun Zanzibar", "Zanzibar asks: how much?",
                              "Zanzibar wonders about nothing in particular tonight", "เอ็มม่า แซนซิบาร์ ไปสระ"]):
        _say(call, text, at=float(k))
    sent = json.dumps(_got(queue), ensure_ascii=False)
    logged = json.dumps(rows, ensure_ascii=False) + caplog.text
    assert "s06_l03" in sent
    for word in ("Zanzibar", "แซนซิบาร์", "swimming", "how much"):
        assert word not in sent and word not in logged, word


# ---- the page, for real ------------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.timeout(240)
def test_the_real_event_screen_shows_the_topic_settled_and_the_logo_otherwise():
    """scripts/preview_ceremony_motion.py --check: the REAL mirror listener and page in headless Chromium,
    the real ceremony call path (claim, adhoc_open, on_host_words, script_press), the slides as drafts
    (the preview's only difference from the day): after every demo line the engine's own state --
    a topic's slide up and settled (`instant`), the logo (nothing up) otherwise, down after the press."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        pytest.skip("playwright is not installed -- the event screen's topic slides are NOT being checked in a page")
    run = subprocess.run([sys.executable, "scripts/preview_ceremony_motion.py", "--check", "--port", str(_free_port())],
                         cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=220)
    if "Executable doesn't exist" in run.stdout + run.stderr:
        pytest.skip("headless Chromium is not installed -- the event screen's topic slides are NOT being checked")
    assert run.returncode == 0, run.stdout[-3000:] + run.stderr[-3000:]
    assert "checked: 0 wrong" in run.stdout
