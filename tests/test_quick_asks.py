"""Tap-to-ask topics on the robot face (app/quick_asks.py, owner 10 Oct 2026).

The rule under test is the one `show_unit(room)` set: the page picks an id,
the server owns every word -- and the desk page never sees any of it."""
import asyncio
import json
import re
from pathlib import Path

from app import quick_asks
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "client" / "voice-preview.html"


def test_the_page_gets_labels_in_every_language_and_never_the_words():
    page = quick_asks.for_page("condo", "Embassy World")
    assert page["brand"] == "Embassy World"
    # the language menu (29 Oct) offers Russian too; topics stay th/en/zh
    assert [b["id"] for b in page["langs"]] == ["th", "en", "zh", "ru"]
    assert set(page["hint"]) == {"th", "en", "zh"}
    assert len(page["items"]) == 6
    for item in page["items"]:
        assert set(item) == {"id", "label"}           # no `ask`: the words stay here
        assert set(item["label"]) == {"th", "en", "zh"}
        assert all(item["label"].values())


def test_other_profiles_have_no_topics():
    for profile in ("joke", "emma", "translator", "nope"):
        assert quick_asks.for_page(profile) is None
        assert quick_asks.text_for(profile, "price") is None
        assert quick_asks.text_for(profile, "lang_en") is None


def test_no_topic_carries_a_number():
    """Figures come from the facts and the tools, never from a button."""
    for topic in quick_asks.TOPICS["condo"]:
        for text in list(topic["label"].values()) + list(topic["ask"].values()):
            assert not re.search(r"\d", text), text


def test_a_tap_is_marked_as_a_tap_and_keeps_the_spoken_language():
    text = quick_asks.text_for("condo", "price", "en")
    assert text.startswith('[ลูกค้าแตะปุ่ม "ราคาเริ่มต้น" บนจอ ไม่ได้พูด')
    assert "ตอบเป็นภาษาเดียวกับที่ลูกค้าพูดล่าสุด" in text
    assert "ถ้ายังไม่ได้พูด ตอบเป็นภาษาอังกฤษ" in text
    assert text.endswith("What are the starting prices for each unit type?")
    # an unknown language code is Thai, not an error
    assert quick_asks.text_for("condo", "price", "xx").endswith("ราคาเริ่มต้นของแต่ละแบบห้องเท่าไหร่คะ")
    assert quick_asks.text_for("condo", "price", None).endswith("ราคาเริ่มต้นของแต่ละแบบห้องเท่าไหร่คะ")


def test_language_buttons_switch_and_say_one_sentence():
    # Since 29 Oct a pick holds for the whole call, and a pick other than Thai
    # is written in English (tests/test_language_menu.py has the rest).
    text = quick_asks.text_for("condo", "lang_zh")
    assert "did not speak" in text and "answer only in Chinese" in text and "do not ask anything" in text
    text = quick_asks.text_for("condo", "lang_th")
    assert "ไม่ได้พูด" in text and "ตั้งแต่นี้ตอบเป็นภาษาไทยเท่านั้น" in text and "ห้ามถามต่อ" in text
    assert quick_asks.text_for("condo", "lang_fr") is None


def test_unknown_ids_and_non_strings_go_nowhere():
    for bad in ("nope", "", None, 3, {"id": "price"}, "lang_"):
        assert quick_asks.text_for("condo", bad) is None


# ---- the session ------------------------------------------------------------

class _Provider:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(text)


def _session(robot_face=True, profile="condo"):
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Provider()
    sess.profile = profile
    sess._robot_face_claim = robot_face
    return sess


def test_a_tap_on_the_robot_face_sends_the_servers_words(monkeypatch):
    from app import turnlog

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: rows.append((event, kw)))
    sess = _session()
    sess._ears_closed_until = 10 ** 9          # a summoned call still holding its ears
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "units", "en")]
    assert sess._ears_closed_until is None
    assert ("quick_ask", {"id": "units", "lang": "en"}) in rows
    assert ("ears_open", {"greeted": False, "why": "quick_ask"}) in rows
    # Review 12 Oct: a tap is a guest in conversation, so the camera greeter
    # (greeter._in_conversation) does not announce "สวัสดีค่ะ" over the answer.
    from app import greeter
    assert sess._ever_heard is True
    sess.summoned = True
    assert greeter._in_conversation(sess) is True


def test_a_tap_from_anything_but_the_robot_face_sends_nothing():
    sess = _session(robot_face=False)
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "price"}))
    assert sess.provider.sent == []


def test_an_unknown_topic_or_a_text_field_sends_nothing():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "anything you like"}))
    asyncio.run(sess._quick_ask({"type": "quick_ask", "text": "say this"}))
    assert sess.provider.sent == []


def test_two_taps_inside_the_gap_are_one_question():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "price"}))
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units"}))
    assert len(sess.provider.sent) == 1


def test_ready_carries_topics_for_the_robot_face_only(monkeypatch):
    from fastapi.testclient import TestClient

    from tests.test_voice import _fake_gemini, _Content, _Msg

    _fake_gemini(monkeypatch, [_Msg(_Content(turn_complete=True))])
    monkeypatch.setattr(settings, "robot_face_token", "")
    from app.main import app

    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        desk = ws.receive_json()
    assert desk["type"] == "ready" and "quick_asks" not in desk
    with client.websocket_connect("/ws", headers={"user-agent": "Mozilla/5.0 EmmaRobot/0.1 EmmaBridge/1"}) as ws:
        face = ws.receive_json()
    assert face["type"] == "ready"
    assert face["quick_asks"] == quick_asks.for_page(face["profile"], settings.project_name)


# ---- the page ---------------------------------------------------------------

def _quick_code():
    html = PAGE.read_text(encoding="utf-8")
    return html[html.index("// ---- tap-to-ask topics"):html.index("    function toggleMute() {")]


def test_the_page_sends_an_id_and_a_language_never_text():
    code = _quick_code()
    sends = re.findall(r"ws\.send\(JSON\.stringify\(\{ type: 'quick_ask'[^)]*\)\)", code)
    # `cut` (29 Oct): whether the tap stopped her voice -- a flag, never words
    assert sends == ["ws.send(JSON.stringify({ type: 'quick_ask', id: id, lang: uiLang, cut: cut }))"]
    assert "textContent = quickLabel(item.label)" in code      # labels as text, never HTML
    assert "innerHTML" not in code


def test_topics_show_only_in_the_app_during_a_call_and_leave_with_it():
    html = PAGE.read_text(encoding="utf-8")
    code = _quick_code()
    assert "var on = !!(SHOW_TOPICS && appShell && quickAsks && inCall);" in code
    # Owner 12 Oct on the robot: brand, language and topic buttons "เอาออก".
    assert "var SHOW_TOPICS = false;" in code
    close = html[html.index("ws.onclose = function"):html.index("function endCall()")]
    end = html[html.index("function endCall()"):html.index("// ---- tap-to-ask topics")]
    assert "quickAsks = null; setUiLang(FIXED_UI_LANG || 'th'); renderQuick();" in close
    assert "quickAsks = null; setUiLang(FIXED_UI_LANG || 'th'); renderQuick();" in end
    # desktop /preview: the new blocks exist but stay hidden
    assert ".brand, .langs, .quick { display: none; }" in html
    # the chosen language reaches the status pill, the mic and the two buttons
    assert "if (lastStatusKey) status(lastStatusKey);" in code
    assert "if (lastMicKey) setMicLabel(lastMicKey);" in code
    assert "if (!appShell) return key;" in html            # the desktop page keeps English keys


def test_the_guests_words_pick_the_language_but_never_reach_the_screen():
    import shutil
    import subprocess

    node = shutil.which("node")
    assert node, "node is needed to run the page's language detection"
    code = _quick_code()
    fn = code[code.index("function scriptLang(text)"):code.index("    function sendQuickAsk(id)")]
    js = fn + """
const out = [scriptLang('ราคาเท่าไหร่คะ'), scriptLang('How much is it'), scriptLang('多少钱一平方'),
             scriptLang('ok'), scriptLang('')];
console.log(JSON.stringify(out));"""
    result = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["th", "en", "zh", None, None]
    user_branch = PAGE.read_text(encoding="utf-8").split("case 'user_transcript':", 1)[1].split("break;", 1)[0]
    assert "setUiLang(spoken)" in user_branch


# ---- heard_end: "thinking" the moment the guest is heard to finish (10 Oct) ----

def test_heard_end_goes_to_the_robot_face_only():
    """Measured ~1.1 s from the VAD's end to her first audio; the face shows
    "thinking" for it. The desk page never receives the cue."""
    source = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    block = source.split('if event.kind == "speech_stopped":', 1)[1].split("continue", 1)[0]
    assert 'if getattr(self, "_robot_face_claim", False):' in block
    assert '{"type": "heard_end"}' in block
    page = PAGE.read_text(encoding="utf-8")
    branch = page.split("case 'heard_end':", 1)[1].split("break;", 1)[0]
    assert "if (inCall && !playing)" in branch and "show('thinking')" in branch
    assert "THINKING_FALLBACK_MS" in branch          # silence back to listening, never stuck


def test_the_anime_effects_are_decoration_only_and_app_shell_only():
    page = PAGE.read_text(encoding="utf-8")
    fx = page[page.index("The robot face's anime-presenter effects"):page.index("window.EmmaFx = Object.freeze")]
    assert "if (!document.documentElement.classList.contains('app-shell')) return;" in fx
    for forbidden in ("ws.send", "WebSocket", "fetch(", "prompt(", "Math.random"):
        assert forbidden not in fx, forbidden
    assert "prefers-reduced-motion" in fx
    css = page[page.index("/* ==== The robot face's \"anime presenter\" theme"):page.index("  html.app-shell .controls {")]
    assert "@media (prefers-reduced-motion: reduce)" in css
    # Every rule of the theme is scoped to the robot face.
    import re
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S))
    for selector, _body in rules:
        selector = selector.strip()
        if selector.startswith(("@", "from", "to", "0%", "50%", "100%")) or "%" in selector.split()[0]:
            continue
        assert all(part.strip().startswith("html.app-shell") for part in selector.split(",")), selector


def test_the_anime_theme_stays_light_on_the_robot():
    """Owner 2026-10-10, after the first pass: "บางอย่างมันทำให้แอพช้าได้ดูไม่สวย
    พยายามทำให้การใช้งานลื่นที่สุด". Measured at 1080x1920, CPU x4, WebGL off:
    first pass 29.7 -> 13.7 fps listening (78 frames > 50 ms in 6 s vs 4);
    this pass 32.5 fps, p95 33 ms, 0-1 long frames. What bought it back, each
    pinned here: no drifting layer behind the page, no infinite animation
    that is not gated on Emma's state (the six-chip shimmer and the always-
    spinning ring), no backdrop-filter in the app shell (it re-blurs whatever
    moves beneath it every frame), and the voice level written on the halo,
    never on <html> (a custom property on the root restyles the document)."""
    import re
    page = PAGE.read_text(encoding="utf-8")
    css = page[page.index("/* ==== The robot face's \"anime presenter\" theme"):page.index("  html.app-shell .controls {")]
    code = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    fx = page[page.index("The robot face's anime-presenter effects"):page.index("window.EmmaFx = Object.freeze")]
    for gone in ("fx-cloud", "fx-star", "fx-layer", "fx-shimmer", "clip-path"):
        assert gone not in code and gone not in fx, gone
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", code):
        if "infinite" in body:
            assert "data-emma-state=" in selector, selector.strip()
        if "backdrop-filter" in body:
            assert re.search(r"backdrop-filter:\s*none", body), selector.strip()
    # backdrop-filter is switched off by name for everything in the app shell
    # that had one, the overlays that open now and then included.
    unblur = code[code.index("html.app-shell .status,"):]
    unblur = unblur[:unblur.index("}")]
    for name in (".status", ".langs", ".control-icon", ".emma-action-icon", ".emma-actions-toast",
                 ".emma-scan", ".emma-nav"):
        assert "html.app-shell " + name in unblur, name
    assert "backdrop-filter: none" in unblur
    assert "documentElement.style" not in fx and "root.style" not in fx
    # Owner 12 Oct on the robot: the halo/dashed ring/ripples behind her
    # ("ที่เหมือนเรดาห์ด้านหลังโมเดล") are gone, CSS and script both.
    for gone in ("emma-halo", "emma-ripple", "fx-ripple", "LEVEL_STEP"):
        assert gone not in code and gone not in fx, gone
    # The pop must not hold a transform: fill-mode kept scale(1) over the
    # caption band's translateX(-50%) and pushed it half off the screen.
    pop = code[code.index("@keyframes fx-pop"):]
    pop = pop[:pop.index("} }") + 3]
    assert "transform" not in pop and "scale:" in pop
    assert "fx-pop .3s cubic-bezier(.2, 1.4, .4, 1);" in code       # no `both`/`forwards`
    # On the stage the reply is the caption band, not the white card.
    assert "html.app-shell body.emma-staging .reply {" in code
    assert "html.app-shell body.emma-staging .reply::before { content: none; }" in code
    # The hidden SVG puppet's loops used to be paused under the 3D body (the
    # glint repainted for nobody); since 14 Oct there is no puppet to pause.
    assert "mascot-rig" not in page and "@keyframes glint" not in page



# ---- choices under her own question (owner 12 Oct 2026) ----------------------
#
# "ลูกค้ากำลังมองหาห้องแบบสตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ คำพวกนี้อยากให้ขึ้นเป็น
# ตัวเลือกบนจอด้วยให้ลูกค้ากดเข้าไปดูได้ ... พอกดไปเราก็อาจจะขึ้นผังหรือรูปห้องนั้น"

def test_her_room_type_question_is_read_as_a_choice():
    asked = [
        "ยินดีต้อนรับค่ะ ลูกค้ากำลังมองหาห้องแบบสตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ",   # live 12 Oct
        "สนใจห้องแบบไหนเป็นพิเศษไหมคะ สตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ",          # the prompt's example
        "สนใจสตูดิโอ หรือ 1 ห้องนอนคะ",
        "Are you looking for a studio, one-bedroom or two-bedroom unit?",
        "Would you prefer a 2-bedroom or a studio?",
        "您想看开间、一室还是两室呢？",
        # the types listed, then "which kind?"
        "เรามีห้องสตูดิโอ 25 ตร.ม. หนึ่งห้องนอน 34 ตร.ม. และสองห้องนอน 52 ตร.ม. ค่ะ ลูกค้าสนใจห้องแบบไหนคะ",
        "We have studios, 1-bedroom and 2-bedroom units. Which one would you like to see?",
    ]
    for line in asked:
        assert quick_asks.choices_asked(line) == "room_types", line
    not_asked = [
        "ลูกค้ากำลังมองหาห้องไว้อยู่เองหรือไว้ลงทุนคะ",                    # a choice with nothing to show
        "มี Pilates Studio และ Bungee Gym ลูกค้าอยากเห็นชั้นไหนคะ",        # a facility, not a room type
        "We have studios and 1-bedroom units. Which floor interests you?",  # a different choice
        "ฟิตเนสอยู่ชั้น G ค่ะ ลูกค้าสนใจห้องแบบไหนคะ",                     # "which kind" with none listed
        "สตูดิโอ",
        "",
    ]
    for line in not_asked:
        assert quick_asks.choices_asked(line) is None, line


def test_a_choice_tap_is_the_servers_words_and_names_the_tool():
    page = quick_asks.choices_for_page("room_types")
    assert page["type"] == "choices" and page["set"] == "room_types"
    assert [i["id"] for i in page["items"]] == ["type_studio", "type_1br", "type_2br"]
    for item in page["items"]:
        assert set(item) == {"id", "label"}                     # no words for the model
        assert set(item["label"]) == {"th", "en", "zh"} and all(item["label"].values())
    assert set(page["hint"]) == {"th", "en", "zh"}
    text = quick_asks.text_for("condo", "type_2br", "en")
    assert text.startswith('[ลูกค้าแตะปุ่ม "2 ห้องนอน" ใต้คำถามของคุณบนจอ ไม่ได้พูด')
    assert "ถ้ายังไม่ได้พูด ตอบเป็นภาษาอังกฤษ" in text
    assert 'show_room_type(type="2br")' in text
    assert quick_asks.choice_set_of("type_studio") == "room_types"
    assert quick_asks.choice_set_of("price") is None and quick_asks.choice_set_of("lang_en") is None
    for profile in ("joke", "emma", "translator"):
        assert quick_asks.text_for(profile, "type_studio") is None
    # every id a button can send leads to the tool its set names
    from app.tools import registry, units  # noqa: F401  (registers show_room_type)
    for name, spec in quick_asks.CHOICES.items():
        assert registry.get(spec["tool"]) is not None, name
        for item in spec["items"]:
            assert spec["tool"] in quick_asks.text_for("condo", item["id"]), item["id"]


ROOM_TYPE_QUESTION = "ยินดีต้อนรับค่ะ ลูกค้ากำลังมองหาห้องแบบสตูดิโอ หนึ่งห้องนอน หรือสองห้องนอนคะ"


def _choice_session(monkeypatch, robot_face=True, profile="condo", visible=True):
    from app import prompts

    monkeypatch.setattr(prompts, "_tool_visible", lambda name: visible)
    sess = _session(robot_face=robot_face, profile=profile)
    sess.sent_json = []

    async def send_json(payload):
        sess.sent_json.append(payload)

    sess._send_json = send_json
    return sess


def _turn(sess, said):
    sess._said_this_turn = said
    asyncio.run(sess._offer_choices())


def test_her_question_puts_the_buttons_up_and_her_next_line_takes_them_down(monkeypatch):
    sess = _choice_session(monkeypatch)
    _turn(sess, ROOM_TYPE_QUESTION)
    assert sess.sent_json == [quick_asks.choices_for_page("room_types")]
    assert sess._choices_offered == "room_types" and sess._said_this_turn == ""
    _turn(sess, "")                          # gemini-3.8's wordless turn_complete: no change
    _turn(sess, ROOM_TYPE_QUESTION)          # asked again: already up, nothing resent
    assert len(sess.sent_json) == 1
    _turn(sess, "ขอบคุณที่แวะมานะคะ")         # her next line, no question in it
    assert sess.sent_json[-1] == {"type": "choices", "set": None, "items": []}
    assert sess._choices_offered is None


def test_no_buttons_off_the_robot_face_off_condo_without_the_tool_or_a_question(monkeypatch):
    for kw in ({"robot_face": False}, {"profile": "joke"}, {"visible": False}):
        sess = _choice_session(monkeypatch, **kw)
        _turn(sess, ROOM_TYPE_QUESTION)
        assert sess.sent_json == [], kw
    sess = _choice_session(monkeypatch)
    _turn(sess, "เรามีห้องแบบสตูดิโอ หนึ่งห้องนอน และสองห้องนอนค่ะ")    # told, not asked
    assert sess.sent_json == []


def test_a_choice_counts_only_while_its_buttons_are_up(monkeypatch):
    sess = _choice_session(monkeypatch)
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "type_studio", "lang": "th"}))
    assert sess.provider.sent == [], "no question asked: a stale or crafted tap goes nowhere"
    _turn(sess, ROOM_TYPE_QUESTION)
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "type_studio", "lang": "th"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "type_studio", "th")]
    assert sess._choices_offered is None, "answered: the buttons are gone on both sides"


def test_the_guest_speaking_takes_the_buttons_down(monkeypatch):
    sess = _choice_session(monkeypatch)
    _turn(sess, ROOM_TYPE_QUESTION)
    sess._guest_started_turn()
    assert sess._choices_offered is None and sess._said_this_turn == ""


def test_the_robot_face_gets_the_buttons_after_her_question(monkeypatch):
    """End to end: her transcript, then turn_complete, then the buttons --
    on the robot face's socket, never the desk page's."""
    from fastapi.testclient import TestClient

    from tests.test_voice import _Content, _fake_gemini, _Msg, _Text

    # Her question, a line without one, and a third turn as the end marker:
    # reading up to its turn_complete never blocks on a frame that is not
    # coming (a missing `choices` fails the assert, it does not hang).
    script = [[_Msg(_Content(output_transcription=_Text(ROOM_TYPE_QUESTION))),
               _Msg(_Content(turn_complete=True))],
              [_Msg(_Content(output_transcription=_Text("ยินดีค่ะ"))),
               _Msg(_Content(turn_complete=True))],
              [_Msg(_Content(output_transcription=_Text("ตกลงค่ะ"))),
               _Msg(_Content(turn_complete=True))]]
    monkeypatch.setattr(settings, "robot_face_token", "")
    from app.main import app

    def frames(headers):
        _fake_gemini(monkeypatch, [list(turn) for turn in script])
        seen, done = [], 0
        with TestClient(app).websocket_connect("/ws", headers=headers) as ws:
            while done < 3:
                msg = ws.receive()
                if not msg.get("text"):
                    continue
                evt = json.loads(msg["text"])
                if evt["type"] in ("turn_complete", "choices"):
                    seen.append(evt)
                done += evt["type"] == "turn_complete"
        return seen

    face = frames({"user-agent": "Mozilla/5.0 EmmaRobot/0.1 EmmaBridge/1"})
    tc = {"type": "turn_complete"}
    assert face == [tc, quick_asks.choices_for_page("room_types"),
                    tc, {"type": "choices", "set": None, "items": []}, tc]
    desk = frames({})
    assert desk == [tc, tc, tc]


def test_the_page_draws_her_choices_and_drops_them_when_answered():
    html = PAGE.read_text(encoding="utf-8")
    code = _quick_code()
    assert "var pick = !!(appShell && quickAsks && inCall && choiceSet);" in code
    assert "var src = pick ? choiceSet : quickAsks;" in code
    assert "if (pick) { choiceSet = null; renderQuick(); return; }" in code
    branch = html.split("case 'choices':", 1)[1].split("break;", 1)[0]
    assert "choiceSet = appShell && evt.items && evt.items.length ? evt : null;" in branch
    spoke = html.split("case 'speech_started':", 1)[1].split("break;", 1)[0]
    assert "if (choiceSet) { choiceSet = null; renderQuick(); }" in spoke
    # (the language menu's pick goes with them since 29 Oct)
    assert html.count("choiceSet = null; pickedLang = null; quickAsks = null; setUiLang(FIXED_UI_LANG || 'th'); "
                      "renderQuick();") == 3
    ready = html.split("case 'ready':", 1)[1].split("break;", 1)[0]
    assert "choiceSet = null;" in ready
    # a tap is a new question: its answer owns the screen, as a spoken one's does
    send = code[code.index("function sendQuickAsk(id)"):]
    assert "answerOwed = true;" in send and "answerStaged = false;" in send
    # the example room says what it is
    assert "if (u.example_of)" in html


# ---- the conversation's language for a tap (owner, 15 Oct 2026) ----------------

def test_script_lang_reads_the_talk_not_the_names_in_it():
    # a Thai reply is full of English names; an English one may end on "ค่ะ"
    assert quick_asks.script_lang("ชั้น 3 อาคาร A มี Social Lounge, Co-working Lounge "
                                  "ไว้นั่งทำงาน และ Sky Pool ค่ะ") == "th"
    assert quick_asks.script_lang("Our project is developed by Empire Group ค่ะ") == "en"
    assert quick_asks.script_lang("我可以为您介绍项目") == "zh"
    assert quick_asks.script_lang("ok") is None and quick_asks.script_lang(None) is None


def test_a_tap_in_an_english_talk_is_english_and_says_so():
    text = quick_asks.text_for("condo", "units", "th", "en")
    assert text.startswith('[The guest tapped "Unit types" on the screen and did not speak. '
                           "This conversation is in English: answer in English, do not switch language.]")
    assert text.endswith("What unit types are there, and how big are they?")
    assert not re.search("[฀-๿]", text), "no Thai in an English talk's tap"
    choice = quick_asks.text_for("condo", "type_1br", "th", "en")
    assert "show_room_type(type=\"1br\")" in choice and not re.search("[฀-๿]", choice)
    thai = quick_asks.text_for("condo", "units", "en", "th")
    assert "ตอนนี้คุยกันเป็นภาษาไทย" in thai and thai.endswith("มีแบบห้องอะไรบ้าง ขนาดเท่าไหร่คะ")
    # no talk known yet: the old text, byte for byte
    assert quick_asks.text_for("condo", "units", "en", None) == quick_asks.text_for("condo", "units", "en")


def test_a_story_tap_in_an_english_talk_is_english():
    from app import stories

    tap = {"id": "story_x_level3", "label_en": "Level 3", "label_th": "ชั้น 3", "next_story": "facilities_level3"}
    text = stories.tap_text(tap, "en", "en")
    assert text == ('[The guest tapped "Level 3" on the screen and did not speak. This conversation is in '
                    'English: answer in English, do not switch language.] '
                    'Call present_story(topic="facilities_level3") and tell it from the result.')
    assert "ภาษาไทย" in stories.tap_text(tap, "en", "th")
    assert stories.tap_text(tap, "en") == stories.tap_text(tap, "en", None)


def test_the_session_taps_in_the_language_she_is_speaking(monkeypatch):
    """Robot 15 Oct: an English answer, then a tap -- the reply came in Thai."""
    sess = _session()
    sess._said_since_guest = "Our facilities are spread over three floors."
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "th"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "units", "th", "en")]
    sess = _session()
    sess._guest_text = "ห้องมีกี่แบบครับ อยากรู้รายละเอียด"
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "units", "en", "th")]


def test_a_tap_in_another_language_names_it_not_english():
    """Audit 27 Oct: any language TalkLanguage can tell, outside th/en/zh, gets
    one bracketed preamble naming it -- a German talk's tap used to say "This
    conversation is in English" (the tap itself switched her)."""
    text = quick_asks.text_for("condo", "units", "th", "de")
    assert text.startswith('[The guest tapped "Unit types" on the screen and did not speak. '
                           "This conversation is in German: answer in German, do not switch language.]")
    assert text.endswith("What unit types are there, and how big are they?")
    assert "English" not in text and not re.search("[฀-๿]", text)
    choice = quick_asks.text_for("condo", "type_1br", "th", "ja")
    assert "This conversation is in Japanese: answer in Japanese" in choice
    assert 'show_room_type(type="1br")' in choice
    assert quick_asks.tap_call("present_story()", "de") == "Call present_story() and tell it from the result."
    # the helpers story taps can call (stories.tap_text / the picture preamble)
    assert quick_asks.tap_talk("de") == "de" and quick_asks.talk_name_en("de") == "German"
    assert quick_asks.tap_talk("xx") is None and quick_asks.talk_name_en("xx") is None
    assert quick_asks.tap_talk(None) is None
    # th/en/zh read exactly as before
    assert quick_asks.text_for("condo", "units", "th", "en").startswith(
        '[The guest tapped "Unit types" on the screen and did not speak. This conversation is in English')
    # a code nobody can name is the old wording, as for no talk at all
    assert quick_asks.text_for("condo", "units", "en", "xx") == quick_asks.text_for("condo", "units", "en")


def test_script_lang_does_not_read_japanese_as_chinese():
    """Kanji with kana is Japanese, which script_lang cannot name: None, not
    "zh" (a Japanese talk's tap was told "This conversation is in Chinese")."""
    assert quick_asks.script_lang("日本語で話してください") is None
    assert quick_asks.script_lang("スタジオの広さはどのくらいですか") is None
    assert quick_asks.script_lang("我可以为您介绍项目") == "zh"


class _TalkingProvider(_Provider):
    """A provider that knows the conversation's language, as GeminiProvider
    does (`_talk`, app/talk_lang.py -- what the guest said)."""

    def __init__(self, *guest):
        super().__init__()
        from app.talk_lang import TalkLanguage

        self._talk = TalkLanguage()
        for line in guest:
            self._talk.guest(line)
            self._talk.emma("ok")


def test_the_session_taps_in_the_language_the_guest_speaks_not_the_script():
    """Audit 27 Oct (verified on the robot's own wording): German talk, a tap
    -> "This conversation is in English"; Japanese talk -> "in Chinese".
    `script_lang` reads any Latin script as English and kanji as Chinese; the
    provider's TalkLanguage -- the language every tool result already
    carries -- is asked first."""
    sess = _session()
    sess.provider = _TalkingProvider("Wo ist das Fitnessstudio und wie gross ist es bitte?")
    sess._said_since_guest = "Das Fitnessstudio Biogenesis ist im Erdgeschoss von Gebaeude A."
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "units", "en", "de")]
    assert "in German" in sess.provider.sent[0] and "English" not in sess.provider.sent[0]
    sess = _session()
    sess.provider = _TalkingProvider("この部屋の広さはどのくらいですか")
    sess._said_since_guest = "このお部屋は約三十平方メートルです"
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert "in Japanese" in sess.provider.sent[0] and "Chinese" not in sess.provider.sent[0]
    # the guest said nothing that tells: her own words' script, as before
    sess = _session()
    sess.provider = _TalkingProvider()
    sess._said_since_guest = "Our facilities are spread over three floors."
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "th"}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "units", "th", "en")]



def test_a_tap_is_watched_like_a_spoken_question():
    """Replay of the 20 Oct robot log (27 Oct): a tapped topic whose answer
    never came was never chased. The session hands the tap's own text to the
    provider's reply watchdog right after sending it."""
    sess = _session()
    watched = []
    sess.provider.watch_text_turn = watched.append
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert watched == sess.provider.sent == [quick_asks.text_for("condo", "units", "en")]
