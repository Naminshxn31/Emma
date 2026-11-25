"""The ceremony on the room screen (owner 17 Nov: her voice on stage is English, "ซับไทย ...
ด้านล่าง", and "ก็ทำเหมือนตัวหุ่น ก็เอาหุ่นออกแล้วเอาพวกนี้ไปใส่แทน"): no Emma on the
screen, the stories across it, and the cue's approved Thai line in a band at the bottom,
in step with her voice. app/face_mirror.py (subtitle), app/event_cues.py (_subtitle), the
page's html.mirror-show."""
import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import event_cues, event_script, face_mirror

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
CEREMONY = ROOT / "data" / "ceremony" / "floor1_ceremony.json"


def _screen() -> asyncio.Queue:
    face_mirror.reset()
    queue: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(queue)
    return queue


def _got(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        item = queue.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item))
    return out


# ---- the server's message -------------------------------------------------------------

def test_the_subtitle_is_the_servers_own_message_and_a_joining_screen_gets_it():
    queue = _screen()
    face_mirror.call_started()
    face_mirror.subtitle({"id": "s03_l03", "text": "  โครงการอยู่ใกล้หาดค่ะ ", "scene": "ทำเลที่ตั้ง", "seconds": 12.34})
    sent = [m for m in _got(queue) if m["type"] == "mirror_subtitle"]
    assert sent == [{"type": "mirror_subtitle", "id": "s03_l03", "text": "โครงการอยู่ใกล้หาดค่ะ",
                     "scene": "ทำเลที่ตั้ง", "seconds": 12.3}]
    assert json.loads(face_mirror.snapshot()[-1]) == sent[0]          # the window opened late
    face_mirror.subtitle_end("s02_l09")                               # the line before's late end
    assert _got(queue) == [] and face_mirror._state["subtitle"] is not None
    face_mirror.subtitle_end("s03_l03", cut=True)
    assert _got(queue) == [{"type": "mirror_subtitle_end", "id": "s03_l03", "cut": True}]
    assert all(json.loads(x)["type"] != "mirror_subtitle" for x in face_mirror.snapshot())


def test_no_words_no_subtitle_and_a_call_that_ended_keeps_none():
    queue = _screen()
    face_mirror.call_started()
    _got(queue)
    for bad in ({"id": "x", "text": ""}, {"id": "x", "text": None}, {"id": "x", "text": ["a"]}, {"id": "x"}):
        face_mirror.subtitle(bad)
    assert _got(queue) == [] and face_mirror._state["subtitle"] is None
    face_mirror.subtitle({"id": "x", "text": "ก" * 5000, "seconds": True})
    one = _got(queue)[0]
    assert len(one["text"]) == face_mirror.SUBTITLE_MAX and "seconds" not in one
    face_mirror.call_ended()
    assert face_mirror._state["subtitle"] is None


def test_a_new_call_and_a_dropped_socket_leave_no_line_of_the_old_one():
    queue = _screen()
    face_mirror.call_started()
    face_mirror.subtitle({"id": "a", "text": "คำ"})
    face_mirror.call_started()                       # the call replaced mid-reading: no call_ended came
    assert face_mirror._state["subtitle"] is None
    source = PAGE.read_text(encoding="utf-8")
    close = source[source.index("      sock.onclose = function () {"):source.index("    // ---- the ceremony's screen")]
    assert "mirrorSubEnd({ cut: true });" in close
    assert "t === 'mirror_end' || t === 'mirror_start' ||" in source


def test_the_faces_own_stream_cannot_put_a_subtitle_on_the_screen():
    """Only the cue code sends one: a tool result or a page message of that type is dropped
    by `view` like anything else the face does not draw."""
    assert face_mirror.view({"type": "mirror_subtitle", "text": "x"}) is None
    assert face_mirror.view({"type": "mirror_subtitle_end"}) is None


# ---- the cue code -----------------------------------------------------------------------

def test_an_english_reading_sends_its_thai_line_and_a_thai_one_sends_nothing():
    queue = _screen()
    face_mirror.call_started()
    _got(queue)
    thai = {"id": "a", "th": "สวัสดีค่ะ", "scene_th": "ฉาก"}
    run = {"id": "a", "cue": thai}
    event_cues._subtitle(run)
    assert _got(queue) == [] and not run.get("subtitled")

    english = {"id": "b", "th": "Hello, everyone. I am Emma.", "sub_th": "สวัสดีทุกท่านค่ะ", "lang": "en",
               "scene_th": "ฉาก"}
    run = {"id": "b", "cue": english}
    event_cues._subtitle(run)
    sent = _got(queue)
    assert sent == [{"type": "mirror_subtitle", "id": "b", "text": "สวัสดีทุกท่านค่ะ", "scene": "ฉาก",
                     "seconds": round(len(english["th"]) / event_cues.EN_CHARS_PER_S, 1)}]
    assert run["subtitled"] is True
    # a recording's true length wins over the guess
    event_cues._subtitle(run, 7.5)
    assert _got(queue)[0]["seconds"] == 7.5


def test_the_press_ending_takes_the_line_down_and_a_stop_takes_it_down_at_once(monkeypatch):
    monkeypatch.setattr(event_cues.turnlog, "record", lambda *a, **k: None)
    for stopped, ok, cut in ((False, True, False), (True, False, True), (False, False, True)):
        queue = _screen()
        face_mirror.call_started()
        face_mirror.subtitle({"id": "c1", "text": "คำ"})
        _got(queue)
        run = {"id": "c1", "by": "staff", "mode": "read", "subtitled": True, "stopped": stopped}
        event_cues._end(run, ok=ok, error=None if ok else "stopped")
        assert _got(queue) == [{"type": "mirror_subtitle_end", "id": "c1", "cut": cut}]
    # a press that never put a line up sends nothing
    queue = _screen()
    event_cues._end({"id": "c2", "by": "staff", "mode": "read"}, ok=False, error="no_call")
    assert _got(queue) == []


def test_both_roads_of_a_line_send_the_subtitle():
    """The recorded line and the live reading are two functions: one of them forgetting the
    subtitle is a silent hole on the day's screen."""
    source = (ROOT / "app" / "event_cues.py").read_text(encoding="utf-8")
    recorded = source[source.index("async def _recorded_line"):source.index("def _heard_recorded")]
    read = source[source.index("async def _read_line"):source.index("async def _clip(")]
    assert "_subtitle(run" in recorded and "_subtitle(run" in read


def test_the_ceremony_file_gives_every_spoken_cue_a_thai_line():
    doc = json.loads(CEREMONY.read_text(encoding="utf-8"))
    assert event_script.speak(doc) == "en"
    for c in doc["cues"]:
        if not c.get("en"):
            continue
        said = event_script.spoken(c, doc)
        assert said["lang"] == "en" and said["sub_th"] == c["th"] and said["sub_th"].strip(), c["id"]


# ---- the page ---------------------------------------------------------------------------

def _page(cases: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required: NOT checked here -- which piece of the Thai subtitle is on the room "
                    "screen at which second of her voice, and that it comes down when she stops")
    result = subprocess.run([node, "tests/client_mirror_subtitle.cjs", json.dumps(cases)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_every_line_of_the_ceremony_fits_the_band_in_pieces_cut_at_its_own_spaces():
    doc = json.loads(CEREMONY.read_text(encoding="utf-8"))
    cases = {c["id"]: {"chunks": c["th"]} for c in doc["cues"] if c.get("th")}
    got = _page(cases)
    most = int(re.search(r"var MIRROR_SUB_CHARS = (\d+);", PAGE.read_text(encoding="utf-8")).group(1))
    for c in doc["cues"]:
        if not c.get("th"):
            continue
        chunks = got[c["id"]]["chunks"]
        assert " ".join(chunks).replace("\u00a0", " ") == " ".join(c["th"].split()), c["id"]       # nothing lost, nothing added
        assert not any(x.endswith("Embassy") for x in chunks), c["id"]       # the name is never cut
        assert all(len(x) <= most for x in chunks), (c["id"], [len(x) for x in chunks])


def test_the_pieces_follow_her_voice_and_never_a_clock_started_at_the_press():
    text = "aaaa bbbb cccc"            # with a 4-character piece limit this would be 3 pieces; here: one
    long = " ".join(["ก" * 60, "ข" * 60, "ค" * 60])       # three pieces of 60
    got = _page({
        "timed": {"steps": [
            {"mode": True},
            {"show": {"id": "x", "text": long, "seconds": 18, "scene": "ทำเลที่ตั้ง"}},
            {"at": 0}, {"at": 5},                       # no voice yet: the first piece, however long it takes
            {"head": 23, "at": 5.2},                    # her voice starts at 5.2
            {"at": 10}, {"at": 11.4}, {"at": 17.5}, {"at": 23.0},
            {"end": {"id": "x"}}, {"at": 23.1}, {"at": 24.0}, {"at": 25.5}]},
        "short": {"steps": [{"mode": True}, {"show": {"id": "y", "text": text, "seconds": 3}}, {"at": 0}]},
    })
    seen = dict((t, up) for t, up in got["timed"]["seen"])
    a, b, c = "ก" * 60, "ข" * 60, "ค" * 60
    assert seen[0] == a and seen[5] == a
    assert seen[10] == a                               # 4.8 s into 18: still the first third
    assert seen[11.4] == b                             # 6.2 s: the second
    assert seen[17.5] == c and seen[23.0] == c
    assert seen[23.1] == c and seen[24.0] == c         # she stopped: the last words a moment more
    assert seen[25.5] is None
    assert got["timed"]["scene"] == "ทำเลที่ตั้ง" and got["timed"]["show"] is True
    assert got["short"]["seen"] == [[0, text]]


def test_a_piece_ends_with_its_sentence_when_it_can_stand_alone():
    """Seen in Chromium, 17 Nov: "...ได้สะดวกขึ้นค่ะ Embassy World" -- the next sentence's first
    words on the old one's piece. A short sentence still shares a piece."""
    first = "เราให้ความสำคัญกับทั้งประสบการณ์การอยู่อาศัยค่ะ"
    got = _page({"a": {"chunks": first + " Embassy World จึงเป็นโครงการสำหรับคนที่เปิดรับนวัตกรรมค่ะ"},
                 "b": {"chunks": "ได้ค่ะ คุณมิกิเริ่มถามได้เลยค่ะ"},
                 "c": {"chunks": "It is about 600 metres away. The beach is close."}})
    assert got["a"]["chunks"] == [first, "Embassy World จึงเป็นโครงการสำหรับคนที่เปิดรับนวัตกรรมค่ะ"]   # (the name: one word)
    assert got["b"]["chunks"] == ["ได้ค่ะ คุณมิกิเริ่มถามได้เลยค่ะ"]
    assert got["c"]["chunks"] == ["It is about 600 metres away. The beach is close."]


def test_a_stop_another_lines_end_and_leaving_the_ceremony():
    line = {"id": "x", "text": "คำแรก คำสอง", "seconds": 5}
    got = _page({
        "cut": {"steps": [{"mode": True}, {"show": line}, {"head": 5, "at": 1}, {"end": {"id": "x", "cut": True}},
                          {"at": 1.1}]},
        # (frames still coming -- the robot talking to this screen, her voice not: HER silence, the 6 s;
        # with nothing coming at all it is the link's, test_a_gap_in_the_link_holds_the_line_...)
        "other": {"steps": [{"mode": True}, {"show": line}, {"head": 5, "at": 1}, {"end": {"id": "w"}},
                            {"link": 9, "at": 9}, {"link": 12, "at": 12}, {"link": 16, "at": 16}]},
        # an end that never came, and a line whose voice never came: down by themselves
        "no_voice": {"steps": [{"mode": True}, {"show": line}, {"at": 1}, {"at": 14}, {"at": 16.5}]},
        # the line is over and an answer starts right after: the words go with THEIR voice
        "next_voice": {"steps": [{"mode": True}, {"show": line}, {"head": 5, "at": 1}, {"at": 5},
                                 {"end": {"id": "x"}}, {"head": 30, "at": 5.5}, {"at": 6.5}, {"at": 7}]},
        # the server's guess was far too long (30 s for a 9 s reading): once the line is over
        # there, all of her voice is queued here and its true length places the pieces
        "true_length": {"steps": [{"mode": True},
                                  {"show": {"id": "z", "text": " ".join(["ก" * 60, "ข" * 60, "ค" * 60]), "seconds": 30}},
                                  {"head": 10, "at": 1}, {"end": {"id": "z"}}, {"at": 5}, {"at": 8.5}]},
        "left": {"steps": [{"mode": True}, {"show": line}, {"at": 1}, {"mode": False}, {"at": 1.1}]},
        "blocked": {"blocked": True, "steps": [{"mode": True}, {"show": line}, {"head": 5, "at": 1},
                                                {"end": {"id": "x"}}, {"at": 2}, {"at": 4}]},
        "fit": {"frame": True, "steps": [{"mode": True}, {"mode": True}, {"mode": False}]},
    })
    assert got["cut"]["seen"][-1][1] is None
    other = dict((t, up) for t, up in got["other"]["seen"])
    assert other[12] == "คำแรก คำสอง"                              # the line before's late end changes nothing
    assert other[16] is None                                       # ... and silent 6 s with no end: over
    assert [up for _, up in got["no_voice"]["seen"]] == ["คำแรก คำสอง", "คำแรก คำสอง", None]
    assert [up for _, up in got["next_voice"]["seen"]] == ["คำแรก คำสอง"] * 4 + [None]
    assert [up for _, up in got["true_length"]["seen"]] == ["ก" * 60, "ข" * 60, "ค" * 60]
    assert got["left"]["seen"] == [[1, "คำแรก คำสอง"], [1.1, None]] and got["left"]["show"] is False
    # a browser that has not let her voice start: the line is up, and comes down when it is over
    assert got["blocked"]["seen"] == [[1, "คำแรก คำสอง"], [2, "คำแรก คำสอง"], [4, None]]
    assert got["fit"]["fits"] == 2                                   # on and off: a story up is fitted again



GAP_PIECES = ["ก" * 60, "ข" * 60, "ค" * 60]
GAP_LINE = {"id": "g", "text": " ".join(GAP_PIECES), "seconds": 10}
# A recorded line as it reaches the screen: her voice from 1 s, sent 2 s ahead of her
# (RECORDED_LEAD_S), a frame a second -- until 3 s, when the link goes quiet. Her voice
# queued here runs out at 5 s, 4 s into her 10.
GAP_START = [{"mode": True}, {"link": 0}, {"show": GAP_LINE}, {"at": 0},
             {"head": 3, "link": 1, "at": 1}, {"head": 4, "link": 2, "at": 2}, {"head": 5, "link": 3, "at": 3}]


def test_a_gap_in_the_link_holds_the_line_where_her_voice_ran_out():
    """The open risk of 21 Nov: robot -> screen is Wi-Fi, and her voice queued here runs dry
    while she is still speaking (a recorded line is only 2 s ahead). The 6 s took the words
    down under a line still being said, after running the pieces on through the gap. Now
    nothing arriving = the link's silence: the line stands where her voice ran out, goes on
    with the rest of it, and only 15 s of nothing at all after her voice ran out takes it
    down (the robot is gone)."""
    a, b, c = GAP_PIECES
    got = _page({
        # 9.5 s of nothing, then the rest of her voice (6 s) and the line's end at once
        "gap": {"steps": GAP_START + [{"at": 4}, {"at": 5.0}, {"at": 7}, {"at": 9}, {"at": 11}, {"at": 12},
                                     {"head": 18.5, "link": 12.5, "at": 12.6}, {"end": {"id": "g"}},
                                     {"at": 15}, {"at": 18.5}, {"at": 20.0}, {"at": 20.5}]},
        # the robot gone at 3 s: nothing ever again
        "gone": {"steps": GAP_START + [{"at": 5.0}, {"at": 10}, {"at": 19.9}, {"at": 20.1}, {"at": 30}]},
        # back too late: the rest of her voice at 25 s brings the words back no more
        "too_long": {"steps": GAP_START + [{"at": 5.0}, {"at": 19.9}, {"at": 20.1},
                                          {"head": 31, "link": 25, "at": 25.1}, {"end": {"id": "g"}}, {"at": 26}]},
    })
    gap = dict((t, up) for t, up in got["gap"]["seen"])
    assert gap[4] == a and gap[5.0] == b                          # 4 s of her 10: the second piece
    assert all(gap[t] == b for t in (7, 9, 11, 12, 12.6, 15))     # held through the gap, on from there after it
    assert gap[18.5] == c and gap[20.0] == c and gap[20.5] is None    # her line's end, then down
    gone = dict((t, up) for t, up in got["gone"]["seen"])
    assert gone[10] == b and gone[19.9] == b                      # held, never past where she stopped
    assert gone[20.1] is None and gone[30] is None                # 15 s after her voice ran out: down
    late = dict((t, up) for t, up in got["too_long"]["seen"])
    assert late[19.9] == b and late[20.1] is None and late[25.1] is None and late[26] is None


SETUP = {"id": "s", "text": "ก่อน", "seconds": 4, "slide": {"template": "hero"}}
PUNCH = {"id": "p", "text": "หลัง", "seconds": 2, "slide": {"template": "number"}}
# The set-up as it reaches the screen: its voice 0..4 s here, its end from the server at 2 s (a
# recording's run ends 2 s ahead of her voice), then the punchline's line at 2 s -- the robot's
# clock said the set-up was over; this screen still has 2 s of it to play.
JOKE_START = [{"mode": True}, {"show": SETUP}, {"head": 4, "at": 0.0}, {"at": 2}, {"end": {"id": "s"}},
              {"show": PUNCH}]


def test_a_line_waits_for_the_voice_before_it_to_play_out_here():
    """Review 23 Nov (the jokes said straight through): a line that reaches the room screen while the
    line before's voice is still queued there waits for it -- its words AND its slide go up when its
    own voice starts on this screen, and the line before plays out whole (its typed panel, its length
    read up to its own voice). Every way down still takes it down: a stop or the call over before it
    went up = it never does; leaving the ceremony likewise. Her voice cut short (`interrupted`) = up at
    once. An audio clock that stopped: up by the wall clock. A joining screen (`late`) never waits."""
    got = _page({
        "joke": {"engine": True, "steps": JOKE_START + [
            {"at": 2.5}, {"head": 6, "at": 3}, {"at": 3.9}, {"at": 4.0}, {"at": 5},
            {"end": {"id": "p"}}, {"at": 6.1}, {"at": 8}]},
        "stopped": {"engine": True, "steps": JOKE_START + [
            {"end": {"cut": True}}, {"interrupt": True}, {"at": 2.5}, {"head": 6, "at": 4.5}, {"at": 5}]},
        "left": {"steps": JOKE_START + [{"mode": False}, {"at": 2.5}, {"head": 6, "at": 4.5}]},
        # its own press stopped or failed after its line went out (event_cues' end with its id, cut)
        "own_cut": {"steps": JOKE_START + [{"end": {"id": "p", "cut": True}}, {"at": 3}, {"head": 6, "at": 4.5}]},
        # on an animation frame: up on the frame its voice starts, not on the next 150 ms tick
        "frame": {"engine": True, "frames": True, "steps": JOKE_START + [{"head": 6}, {"frame": 3.9}, {"frame": 4.0}]},
        "own_end_first": {"steps": JOKE_START + [{"end": {"id": "p"}}, {"head": 5, "at": 3}, {"at": 4.0},
                                                 {"at": 4.9}, {"at": 5.5}, {"at": 7.5}]},
        "interrupted": {"steps": JOKE_START + [{"interrupt": True}, {"at": 2.1}, {"head": 4, "at": 2.5}]},
        "stuck_clock": {"steps": JOKE_START + [{"wall": 3}, {"wall": 4.9}, {"wall": 5.1}]},
        "late": {"steps": [{"mode": True}, {"show": SETUP}, {"head": 4, "at": 0.0},
                           {"show": dict(PUNCH, late=1.5)}, {"at": 0.5}]},
        "nothing_queued": {"steps": [{"mode": True}, {"show": SETUP}, {"head": 4, "at": 0.0}, {"at": 4.5},
                                     {"show": PUNCH}, {"at": 4.6}]},
    })
    joke = dict((t, up) for t, up in got["joke"]["seen"])
    assert [joke[t] for t in (2, 2.5, 3, 3.9)] == ["ก่อน"] * 4        # the set-up's words to the end of its voice
    assert joke[4.0] == "หลัง" and joke[5] == "หลัง" and joke[8] is None
    slides = got["joke"]["slides"]
    up = slides.index(["show", "number", False])
    # the set-up's slide ran on its own voice to the end (3 of 4 s at 3 s -- not 3 of the 6 s queued)
    assert [s[1] for s in slides[:up] if s[0] == "progress"][-3:] == [0.625, 0.75, 0.975]
    # the punchline's slide on ITS voice: its clock from 4.0 s, half its 2 s at 5 s
    assert [s[1] for s in slides[up:] if s[0] == "progress"][:2] == [0.0, 0.5]
    for case in ("stopped", "left", "own_cut"):
        assert "หลัง" not in [u for _t, u in got[case]["seen"]], case             # never up
    assert dict((t, up) for t, up in got["own_cut"]["seen"])[3] == "ก่อน"     # the set-up plays on
    framed = got["frame"]["slides"]
    assert ["show", "number", False] in framed                                # no tick since 2 s: the frame did it
    assert framed.index(["show", "number", False]) > framed.index(["progress", 0.975])
    assert ["show", "number", False] not in got["stopped"]["slides"]
    assert [u for _t, u in got["stopped"]["seen"]][-1] is None and not got["stopped"]["ticking"]
    own = dict((t, up) for t, up in got["own_end_first"]["seen"])
    # its end, kept while it waited: down 1.8 s after its voice, not 15 s later
    assert own[3] == "ก่อน" and own[4.0] == "หลัง" and own[4.9] == "หลัง" and own[7.5] is None
    assert dict((t, up) for t, up in got["interrupted"]["seen"])[2.1] == "หลัง"
    stuck = dict((t, up) for t, up in got["stuck_clock"]["seen"])
    assert stuck["wall 3"] == "ก่อน" and stuck["wall 4.9"] == "ก่อน" and stuck["wall 5.1"] == "หลัง"
    assert got["late"]["seen"] == [[0.0, "ก่อน"], [0.5, "หลัง"]]
    assert dict((t, up) for t, up in got["nothing_queued"]["seen"])[4.6] == "หลัง"


def test_every_frame_on_the_mirror_socket_says_the_link_is_alive():
    """The hold reads when the socket last brought anything: the call is in the mirror socket's
    own handler (the harness runs the subtitle's block alone), first after its stale-socket
    check -- every frame, text or her voice, before anything in it can return early."""
    source = PAGE.read_text(encoding="utf-8")
    sock = source[source.index("      sock.onmessage = function (m) {"):source.index("      sock.onclose = function () {")]
    assert sock.startswith("      sock.onmessage = function (m) {\n        if (ws !== sock) return;\n"
                           "        mirrorSubFrame();")
    assert source.count("mirrorSubFrame()") == 2                        # its definition and this call
    handler = source[source.index("    function onMessage("):source.index("    // ---- the room screen (mirror): watch the robot's call ----")]
    assert "mirrorSubFrame" not in handler


def test_only_the_ceremonys_call_turns_the_screen_into_the_show():
    source = PAGE.read_text(encoding="utf-8")
    assert source.count("mirrorShowMode(") == 2                      # its definition and one caller
    assert "if (t === 'ready') mirrorShowMode(said.profile === 'ceremony');" in source
    # no Emma, no bubble, no status pill -- and only under the mode's class
    rule = re.search(r"html\.mirror\.mirror-show \.mascot-wrap, html\.mirror\.mirror-show \.reply,\s*"
                     r"html\.mirror\.mirror-show \.topbar \{([^}]*)\}", source)
    assert rule and "visibility: hidden" in rule.group(1)
    assert ".mirror-sub, .mirror-back { display: none; }" in source
    # the page's shared message handler has no name of this in it (its test harnesses run
    # slices of it alone, CLAUDE.md): the subtitle lives in the mirror socket's own handler
    handler = source[source.index("    function onMessage("):source.index("    // ---- the room screen (mirror): watch the robot's call ----")]
    assert "mirrorSub" not in handler and "mirrorShow" not in handler


def test_the_pictures_leave_room_for_the_band():
    source = PAGE.read_text(encoding="utf-8")
    area = source[source.index("    function mirrorArea() {"):source.index("    function mirrorFrameMode(")]
    assert "--mirror-foot" in area and "h * 0.985 - foot" in area
    assert re.search(r"html\.mirror\.mirror-show \{ --mirror-side: 0vw; --mirror-foot: \d+vh; \}", source)


def test_a_piece_wraps_only_at_the_lines_own_spaces():
    """17 Nov evening: the band broke "ผู้|ช่วย" and "รับ|ชม" -- the browser's Thai dictionary
    breaks inside a phrase, and text-wrap: balance puts the break in the middle of a piece.
    Each phrase is one unbreakable run; the line's spaces are the only places to wrap; a
    phrase no line can hold keeps the browser's breaking (it must not run off the band)."""
    long = "ก" * 71
    text = "ระบบนี้ช่วยทีมขาย ดูแลลูกค้า " + long
    got = _page({"x": {"steps": [{"mode": True}, {"show": {"id": "x", "text": text, "seconds": 3}}, {"at": 0}]}})["x"]
    assert got["runs"] == [["mirror-sub-p", "ระบบนี้ช่วยทีมขาย"], ["", " "], ["mirror-sub-p", "ดูแลลูกค้า"], ["", " "],
                           ["", long]]
    assert got["seen"] == [[0, text]]
    html = PAGE.read_text(encoding="utf-8")
    assert ".mirror-sub-p { white-space: nowrap; }" in html and "var MIRROR_SUB_PHRASE = 70;" in html
    # the project's name: one run, never cut at its space
    named = _page({"x": {"steps": [{"mode": True}, {"show": {"id": "x", "text": "ขอบคุณ Embassy World ค่ะ", "seconds": 3}}, {"at": 0}]}})["x"]
    assert ["mirror-sub-p", "Embassy\u00a0World"] in named["runs"]


def test_a_number_keeps_the_word_after_it_on_its_piece():
    """17 Nov night, s03_l03: "...เป็นเส้นตรงประมาณ 600" on one piece and "เมตรค่ะ" on the next."""
    doc = json.loads(CEREMONY.read_text(encoding="utf-8"))
    th = next(c["th"] for c in doc["cues"] if c.get("id") == "s03_l03")
    got = _page({"x": {"chunks": th}})["x"]["chunks"]
    assert " ".join(got).replace("\u00a0", " ") == " ".join(th.split())
    assert not any(re.search(r"(?:^|\s)[\d,.]+$", piece) for piece in got[:-1]), got
    assert any("600\u00a0เมตร" in piece for piece in got)                 # (24 Nov: the number and its word are one run)


QUALIFIER = re.compile(r"(?:ประมาณ|ราว|ราวๆ|กว่า|ถึง|เกือบ|about|around|approximately|nearly|almost|over)$", re.I)


def test_a_number_keeps_the_word_that_qualifies_it_on_its_piece():
    """Review 18 Nov, s03_l03: "...เป็นเส้นตรงประมาณ" then "600 เมตรค่ะ" -- a bare 600 on the
    screen. The qualifier goes with its number; still only at the line's own spaces."""
    doc = json.loads(CEREMONY.read_text(encoding="utf-8"))
    th = next(c["th"] for c in doc["cues"] if c.get("id") == "s03_l03")
    tail = "ห่างจากหาดเป็นเส้นตรงประมาณ"
    filler = "ก" * (98 - len(tail) - 5)          # "<filler> <tail> 600" = 98: the unit does not fit
    near = "ข" * (98 - len("ห่างประมาณ") - 1)    # "<near> ห่างประมาณ" = 98: the number does not fit
    eng = "x" * (98 - len(" it is about"))
    got = _page({"real": {"chunks": th},
                 # the break after the number ("...ประมาณ 600" too long): both move on
                 "number": {"chunks": filler + " " + tail + " 600 เมตรค่ะ"},
                 # the break right after the qualifier, before the number
                 "qualifier": {"chunks": near + " ห่างประมาณ 600 เมตรค่ะ"},
                 "english": {"chunks": eng + " it is about 600 metres from the beach."},
                 "sentence": {"chunks": "ค" * 40 + "ค่ะ ราว 600 เมตร"}})
    for v in got.values():
        v["chunks"] = [p.replace("\u00a0", " ") for p in v["chunks"]]
    for k, v in got.items():
        pieces = v["chunks"]
        for a, b in zip(pieces, pieces[1:]):
            last = a.split()[-1] if a.split() else ""
            assert not (QUALIFIER.search(last) and re.match(r"[\d,.]", b)), (k, pieces)
            assert not re.search(r"(?:^|\s)[\d,.]+$", a), (k, pieces)
    assert got["number"]["chunks"] == [filler, tail + " 600 เมตรค่ะ"]
    assert got["qualifier"]["chunks"] == [near, "ห่างประมาณ 600 เมตรค่ะ"]
    assert got["english"]["chunks"] == [eng + " it is", "about 600 metres from the beach."]
    # a sentence that ended keeps its end: the next starts with "ราว 600"
    assert got["sentence"]["chunks"] == ["ค" * 40 + "ค่ะ", "ราว 600 เมตร"]
    assert any("ประมาณ 600 เมตร" in piece for piece in got["real"]["chunks"])


def test_a_name_of_several_words_and_a_number_with_its_unit_are_never_split():
    """24 Nov, the 43 extra lines on the hall screen: "... Common Sphere และ Sky" then "Pool ค่ะ", "General" | "Manager",
    "Embassy" | "Life", and wraps inside "30 | นาที", "176 | ห้อง". A run longer than a line keeps its own spaces."""
    nb = chr(0xa0)
    got = _page({
        "pool": {"chunks": "ชั้น 3 อาคาร A มี Social Lounge Co-working Lounge ไว้นั่งทำงาน โถงส่วนกลาง Common Sphere และ Sky Pool ค่ะ"},
        "unit": {"chunks": "สนามบินอู่ตะเภาขับรถประมาณ 30 นาที และสุวรรณภูมิประมาณชั่วโมงครึ่งค่ะ"},
        "long": {"chunks": "A Very Long Name Of Many Capital Words Here"},
        "spa": {"chunks": "ผลงานเช่น Empire Tower, The Orient Resort & Spa และ The Vision"}})
    pool = got["pool"]["chunks"]
    assert len(pool) == 2 and pool[1] == "Sky" + nb + "Pool ค่ะ" and "Common" + nb + "Sphere" in pool[0]
    assert "30" + nb + "นาที" in got["unit"]["chunks"][0]
    assert nb not in got["long"]["chunks"][0]
    spa = got["spa"]["chunks"][0]
    assert "Empire" + nb + "Tower, The" + nb + "Orient" + nb + "Resort" + nb + "&" + nb + "Spa" in spa
