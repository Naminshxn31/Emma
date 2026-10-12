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
    assert [b["id"] for b in page["langs"]] == ["th", "en", "zh"]
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
    text = quick_asks.text_for("condo", "lang_zh")
    assert "ไม่ได้พูด" in text and "ตั้งแต่นี้ตอบเป็นภาษาจีน" in text and "ห้ามถามต่อ" in text
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
    assert sends == ["ws.send(JSON.stringify({ type: 'quick_ask', id: id, lang: uiLang }))"]
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
    # A ready 3D body pauses the hidden puppet's loops (the glint repainted).
    assert ".mascot-wrap.emma-3d-ready .mascot-rig * { animation-play-state: paused !important; }" in page
