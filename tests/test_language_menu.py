"""The language menu on the robot face (owner 29 Oct 2026: "เพิ่มปุ่มภาษา หุ่นจะได้
ตอบแค่ภาษานั้นๆ จะได้ไม่มีการเปลี่ยนภาษาระหว่างคุย" -- "ปุ่ม dropdown ... ไทย อังกฤษ
จีน จะเพิ่มรัสเซีย").

A pick pins the call's language on the server (app/talk_lang.py `pin`): nothing
she hears moves it, every tool result says so, and the page's button names
what the server holds (lang_pinned), never its own tap."""
import asyncio
import re
from pathlib import Path

from app import quick_asks
from app.talk_lang import TalkLanguage

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "client" / "voice-preview.html"


# ---- the language memory ------------------------------------------------------

def _talk(*lines, pin=None):
    t = TalkLanguage()
    if pin:
        assert t.pin(pin)
    for line in lines:
        t.guest(line)
        t.emma("ok")
    return t


def test_a_pick_holds_against_whole_sentences_in_another_script():
    # Unpinned, one whole Thai sentence moves an English talk at once ...
    assert _talk("Where is the swimming pool please?", "ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง").language() == "th"
    # ... pinned to English, five of them do not.
    t = _talk(*["ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง"] * 5, pin="en")
    assert t.language() == "en"


def test_a_plain_request_moves_a_pick_and_holds_like_one():
    """Live, 29 Oct (cat 32): pinned Chinese, "Can you speak English please?"
    got English from her whatever the pin said -- so the server follows a
    plain request, and nothing else."""
    t = _talk("Please speak English with me", pin="zh")
    assert t.language() == "en" and t.pinned == "en"
    # ...and holds against whole sentences in another script after it
    for _ in range(4):
        t.guest("ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง")
        t.emma("ok")
    assert t.language() == "en"


def test_a_question_about_a_language_or_a_request_before_the_pick_moves_nothing():
    # "Can you speak Hebrew?" asks about her (talk_lang.asked_for): no switch
    assert _talk("Can you speak Russian?", pin="th").language() == "th"
    # a request made BEFORE the pick is overruled by the pick
    t = _talk("Please speak English with me")
    assert t.language() == "en"
    t.pin("ru")
    assert t.language() == "ru"
    assert "ปุ่มภาษา" in t.note()


def test_a_pick_is_the_note_from_the_first_word():
    t = TalkLanguage()
    assert t.note() is None                     # nothing said, nothing picked
    t.pin("zh")
    note = t.note()
    assert note.startswith("ลูกค้าเลือกภาษาจีนเอง (เมนูภาษาบนจอหรือขอตรงๆ)")
    assert "ตอบเป็นภาษาจีนเท่านั้น" in note and "แม้ได้ยินภาษาอื่น" in note
    # no quoted sentence in it for her to say aloud (CLAUDE.md, 27 ต.ค.)
    assert "'" not in note and '"' not in note


def test_a_later_pick_replaces_an_earlier_one_and_junk_changes_nothing():
    t = TalkLanguage()
    assert t.pin("en") and t.pin("ru")
    assert t.language() == "ru" and t.pinned == "ru"
    for bad in ("xx", "", None, 3, "RU"):
        assert t.pin(bad) is False
    assert t.pinned == "ru"


def test_without_a_pick_nothing_changed():
    """The rules before the menu: a slip in the same script does not move."""
    t = _talk("How far is the beach from here?", "Bei der Kondom ist inklusive Karte?")
    assert t.language() == "en" and t.pinned is None
    assert t.note().startswith("ภาษาของบทสนทนานี้คือภาษาอังกฤษ")


# ---- the words a pick sends ----------------------------------------------------

def test_the_menu_offers_four_languages_with_their_own_names():
    page = quick_asks.for_page("condo", "Embassy World")
    assert [(l["id"], l["label"]) for l in page["langs"]] == [
        ("th", "ไทย"), ("en", "English"), ("zh", "中文"), ("ru", "Русский")]
    assert quick_asks.PICK_LANGS == ("th", "en", "zh", "ru")


def test_picked_language_reads_only_menu_ids():
    assert quick_asks.picked_language("lang_ru") == "ru"
    for bad in ("lang_fr", "lang_", "ru", "price", None, 3):
        assert quick_asks.picked_language(bad) is None


def test_a_thai_pick_is_thai_and_any_other_names_its_language_in_english():
    th = quick_asks.text_for("condo", "lang_th")
    assert th.startswith("[ลูกค้าแตะปุ่มเลือกภาษาบนจอ ไม่ได้พูด] ลูกค้าเลือกภาษาไทย")
    assert "ตอบเป็นภาษาไทยเท่านั้น แม้ได้ยินภาษาอื่น" in th and "ห้ามถามต่อ" in th
    for code, name in (("en", "English"), ("zh", "Chinese"), ("ru", "Russian")):
        text = quick_asks.text_for("condo", "lang_" + code)
        assert text.startswith("[The guest chose %s on the screen's language menu and did not speak.]" % name)
        assert "answer only in %s, even when you hear another language" % name in text
        assert "do not ask anything" in text
        # a stage direction in English, no Thai turn for the answer to follow
        assert not re.search(r"[฀-๿]", text)
    assert quick_asks.text_for("condo", "lang_fr") is None
    assert quick_asks.text_for("joke", "lang_ru") is None


def test_offline_reads_every_pick():
    from app.offline import router

    for code in quick_asks.PICK_LANGS:
        got = router.classify_injected(quick_asks.text_for("condo", "lang_" + code))
        assert (got.kind, got.lang) == ("language", code)


# ---- the session ---------------------------------------------------------------

class _Provider:
    def __init__(self):
        self.sent = []
        self._talk = TalkLanguage()

    async def send_text(self, text):
        self.sent.append(text)


def _session():
    from app import session as session_module

    sess = session_module.VoiceSession(None, provider_name="gemini")
    sess.provider = _Provider()
    sess.profile = "condo"
    sess._robot_face_claim = True
    sess.page = []

    async def send_json(obj):
        sess.page.append(obj)

    sess._send_json = send_json
    return sess


def test_a_pick_pins_the_call_and_tells_the_page():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "lang_ru", "lang": "en"}))
    assert sess.provider._talk.pinned == "ru"
    assert {"type": "lang_pinned", "lang": "ru"} in sess.page
    assert sess.provider.sent == [quick_asks.text_for("condo", "lang_ru")]
    # every later tap in the call speaks the pinned language
    sess._quick_ask_at = -1e9
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "units", "lang": "en"}))
    assert sess.provider.sent[-1] == quick_asks.text_for("condo", "units", "en", "ru")
    assert "This conversation is in Russian" in sess.provider.sent[-1]


def test_a_spoken_request_after_a_pick_moves_the_button_once():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "lang_zh"}))
    talk = sess.provider._talk
    talk.guest("Can you speak English?")             # a question about her: stays Chinese
    asyncio.run(sess._show_held_language())
    assert [m["lang"] for m in sess.page if m.get("type") == "lang_pinned"] == ["zh"]
    talk.emma("可以")
    talk.guest("Can you speak English please?")      # the live case: a request
    asyncio.run(sess._show_held_language())
    asyncio.run(sess._show_held_language())          # nothing new: nothing sent
    shown = [m["lang"] for m in sess.page if m.get("type") == "lang_pinned"]
    assert shown == ["zh", "en"]


def test_before_a_pick_the_button_hears_nothing():
    sess = _session()
    sess.provider._talk.guest("Please speak English")
    asyncio.run(sess._show_held_language())
    assert sess.page == []


def test_a_dropped_pick_pins_nothing_and_the_page_hears_nothing():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "price"}))
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "lang_zh"}))   # inside the tap gap
    assert sess.provider._talk.pinned is None
    assert not [m for m in sess.page if m.get("type") == "lang_pinned"]
    assert len(sess.provider.sent) == 1


def test_a_topic_tap_pins_nothing():
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "location", "lang": "zh"}))
    assert sess.provider._talk.pinned is None and sess.page == []


def test_the_pin_rides_on_every_tool_result(monkeypatch):
    from app.config import settings
    from app.providers.gemini import GeminiProvider

    monkeypatch.setattr(settings, "talk_language_note", True)
    p = GeminiProvider.__new__(GeminiProvider)
    p._talk = TalkLanguage()
    p._talk.pin("en")
    p._talk.guest("ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง")
    view = p._with_talk_language({"found": True})
    assert view["reply_language"].startswith("ลูกค้าเลือกภาษาอังกฤษเอง")


def test_the_mirror_never_sees_a_pick():
    from app import face_mirror

    assert face_mirror.view({"type": "lang_pinned", "lang": "ru"}) is None


# ---- the page ------------------------------------------------------------------

def _menu_code():
    html = PAGE.read_text(encoding="utf-8")
    return html[html.index("    var SHOW_LANG_MENU = true;"):html.index("    function setUiLang(code) {")]


def test_the_menu_shows_in_a_call_on_the_robot_face_only():
    code = _menu_code()
    assert ("var on = !!(SHOW_LANG_MENU && appShell && !mirror && quickAsks && inCall "
            "&& Object.keys(offered).length);") in code
    html = PAGE.read_text(encoding="utf-8")
    assert "html.mirror .langs { display: none !important; }" in html
    assert ".brand, .langs, .quick { display: none; }" in html      # desktop /preview: hidden


def test_the_button_names_what_the_server_holds_not_the_tap():
    code = _menu_code()
    click = code[code.index("b.addEventListener('click'"):code.index("document.addEventListener('pointerdown'")]
    assert "sendQuickAsk('lang_' + b.dataset.lang)" in click
    assert "pickedLang =" not in click                  # the tap itself sets nothing
    html = PAGE.read_text(encoding="utf-8")
    case = html.split("case 'lang_pinned':", 1)[1].split("break;", 1)[0]
    assert "langPinned(evt.lang);" in case
    assert "innerHTML" not in code                      # labels as text


def test_a_call_that_ends_forgets_the_pick():
    html = PAGE.read_text(encoding="utf-8")
    assert html.count("choiceSet = null; pickedLang = null; quickAsks = null;") == 3


def test_the_menu_stays_light():
    """The face's own rules (10 Oct): no backdrop-filter, nothing infinite,
    the menu opens on opacity/transform."""
    html = PAGE.read_text(encoding="utf-8")
    css = html[html.index("/* The language menu (owner 29 Oct"):html.index("html.mirror .langs { display: none !important; }")]
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    # review 29 Oct: a blurred box-shadow (CLAUDE.md 21 ต.ค.) and repainting
    # background/border transitions got past a check of one rule
    for banned in ("backdrop-filter", "infinite", "blur(", "will-change", "box-shadow", "filter:"):
        assert banned not in css, banned
    transitions = re.findall(r"transition\s*:\s*([^;]+);", css)
    assert len(transitions) >= 5
    for decl in transitions:
        for part in decl.split(","):
            assert part.split()[0] in ("opacity", "transform", "visibility"), decl


def test_the_static_menu_matches_the_server_list():
    html = PAGE.read_text(encoding="utf-8")
    ids = re.findall(r'class="lang-option" type="button" role="option" data-lang="(\w+)"', html)
    assert ids == [l["id"] for l in quick_asks.LANGUAGE_BUTTONS]
    assert "var PICK_LANGS = ['th', 'en', 'zh', 'ru'];" in _menu_code()


# ---- lines the server writes itself (review 29 Oct) ------------------------------

def test_a_held_language_rides_on_every_announcement():
    """FOLLOW_UP_TEXT and the others say "the guest's language": with a pick,
    the guest's latest words may be a colleague's Thai in an English talk."""
    from app import events, session as session_module
    from app.session import FOLLOW_UP_TEXT, VoiceSession

    sess = VoiceSession(ws=None)
    sess.provider = _Provider()
    session_module._active = sess          # announce drops lines with no live session
    asyncio.run(events.announce(FOLLOW_UP_TEXT, source="follow_up", max_wait=0, then_pause=0))
    assert sess.provider.sent == [FOLLOW_UP_TEXT]            # nothing picked: the text as it was
    sess.provider._talk.pin("en")
    sess.provider._talk.guest("ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง")
    sess.turn_idle.set()                   # her reply to the first line is over
    asyncio.run(events.announce(FOLLOW_UP_TEXT, source="follow_up", max_wait=0, then_pause=0))
    sent = sess.provider.sent[-1]
    assert sent.startswith(FOLLOW_UP_TEXT) and "ตอบเป็นภาษาอังกฤษเท่านั้น" in sent


def test_a_stall_ask_in_a_held_talk_names_the_held_language():
    from app.providers.gemini import STALL_ASK, STALL_ASK_LANG, GeminiProvider

    p = GeminiProvider.__new__(GeminiProvider)
    p._talk = TalkLanguage()
    assert p._ask_text("ที่จอดรถมีกี่คัน") == STALL_ASK % "ที่จอดรถมีกี่คัน"   # unheld: unchanged
    assert STALL_ASK_LANG in STALL_ASK
    p._talk.pin("en")
    text = p._ask_text("ที่จอดรถมีกี่คัน")
    assert STALL_ASK_LANG not in text and "ตอบเป็นภาษาอังกฤษเท่านั้น" in text
    assert text.startswith('ลูกค้าพูดว่า "ที่จอดรถมีกี่คัน"')


def test_offline_ignores_the_held_language_note_on_an_instruction():
    from app.offline import router
    from app.session import FOLLOW_UP_TEXT, GOODBYE_TEXT

    t = TalkLanguage()
    t.pin("ru")
    for text in (FOLLOW_UP_TEXT, GOODBYE_TEXT):
        assert router.classify_injected(text + " — " + t.held_note()).kind == "ignore"


def test_offline_keeps_a_pick_it_cannot_speak():
    """Offline answers in Thai or English only; a Chinese pick used to be
    handed a made-up "speak English" after the pin, which moved the held
    language (and the screen's button) to English."""
    from app.offline import router
    from app.providers import offline

    p = offline.OfflineProvider("Despina", "x")

    async def perform(route, lang):
        p.performed = lang

    p._perform = perform
    p._talk.pin("zh")                      # session._quick_ask pins before it sends
    asyncio.run(p._injected(router.classify_injected(quick_asks.text_for("condo", "lang_zh"))))
    assert p._talk.pinned == "zh" and p.performed == "en"
    assert p._reply_lang(None) == "en"     # what offline can say, the pick still held
    # unpinned (an older page): the made-up request still locks, as before
    q = offline.OfflineProvider("Despina", "x")
    q._perform = perform
    asyncio.run(q._injected(router.classify_injected(quick_asks.text_for("condo", "lang_th"))))
    assert q._talk.language() == "th" and q._talk.pinned is None


# ---- review 29 Oct: a held language follows what SHE does about a language question ----

def _held(pin, *steps):
    t = TalkLanguage()
    t.pin(pin)
    for role, text in steps:
        (t.guest if role == "guest" else t.emma)(text)
    return t.language()


def test_her_offer_taken_moves_the_held_language():
    offer = ("emma", "พูดได้ค่ะ ต้องการให้ดิฉันใช้ภาษารัสเซียเลยไหมคะ")
    ask = ("guest", "Can you speak Russian?")
    assert _held("th", ask, offer, ("guest", "Да, пожалуйста. Где находится спортзал?")) == "ru"
    assert _held("th", ask, offer, ("guest", "yes")) == "ru"
    # ...and declined, or not taken up, it stays
    assert _held("th", ask, offer, ("guest", "ไม่เป็นไรค่ะ ภาษาไทยดีแล้ว")) == "th"
    assert _held("th", ask, offer, ("guest", "ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง"),
                 ("emma", "ที่จอดรถมีประมาณสองร้อยกว่าคันค่ะ"), ("guest", "yes")) == "th"


def test_she_answering_in_the_language_asked_about_moves_it():
    assert _held("zh", ("guest", "你会说英语吗"),
                 ("emma", "Yes, of course. How can I help you with Embassy World?")) == "en"
    assert _held("en", ("guest", "ช่วยพูดภาษาไทยได้ไหมคะ"),
                 ("emma", "ได้ค่ะ ยินดีให้บริการเป็นภาษาไทยค่ะ")) == "th"


def test_a_polite_question_alone_does_not_move_a_held_language():
    """"พูดภาษาจีนได้ไหมคะ" is a request by the unheld rules (Thai asks with
    ได้ไหม); held, it held Chinese over five Thai questions (review 29 Oct)."""
    steps = [("guest", "พูดภาษาจีนได้ไหมคะ"), ("emma", "ได้ค่ะ ต้องการให้ใช้ภาษาจีนเลยไหมคะ")]
    steps += [("guest", "ที่จอดรถมีกี่คันคะ อยู่ชั้นไหนบ้าง"), ("emma", "มีที่จอดรถใต้ดินค่ะ")] * 5
    assert _held("th", *steps) == "th"
    assert _held("zh", ("guest", "你会说英语吗"), ("emma", "会的，您想用英语交流吗？")) == "zh"
    # unheld, the old rule stands: ได้ไหม asks for it
    t = TalkLanguage()
    t.guest("พูดภาษาจีนได้ไหมคะ")
    assert t.language() == "zh"


def test_her_words_count_only_from_the_pick():
    t = TalkLanguage()
    t.guest("Can you speak Russian?")
    t.emma("Конечно, я могу говорить по-русски. Чем могу помочь?")
    t.pin("th")                     # picked after all that: Thai
    assert t.language() == "th"


def test_a_pick_mid_answer_asks_for_the_answer_again():
    for code, name in (("en", "English"), ("ru", "Russian")):
        cut = quick_asks.text_for("condo", "lang_" + code, cut=True)
        assert "give your last answer again, whole, in %s" % name in cut and "one short sentence" not in cut
    th = quick_asks.text_for("condo", "lang_th", cut=True)
    assert "ตอบคำตอบล่าสุดนั้นใหม่ทั้งหมดเป็นภาษาไทย" in th and "ตอบรับสั้นๆ" not in th
    from app.offline import router
    assert router.classify_injected(th).lang == "th"
    assert router.classify_injected(quick_asks.text_for("condo", "lang_ru", cut=True)).lang == "ru"


def test_the_session_passes_the_cut_and_logs_the_pick(monkeypatch):
    from app import turnlog

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: rows.append((event, kw)))
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "lang_ru", "lang": "en", "cut": True}))
    assert sess.provider.sent == [quick_asks.text_for("condo", "lang_ru", cut=True)]
    assert ("lang_pinned", {"lang": "ru", "by": "menu"}) in rows
    # a crafted non-boolean `cut` is no cut
    sess2 = _session()
    asyncio.run(sess2._quick_ask({"type": "quick_ask", "id": "lang_ru", "cut": "yes"}))
    assert sess2.provider.sent == [quick_asks.text_for("condo", "lang_ru")]


def test_a_spoken_switch_is_logged(monkeypatch):
    from app import turnlog

    rows = []
    monkeypatch.setattr(turnlog, "record", lambda event, **kw: rows.append((event, kw)))
    sess = _session()
    asyncio.run(sess._quick_ask({"type": "quick_ask", "id": "lang_zh"}))
    sess.provider._talk.guest("Please speak English")
    asyncio.run(sess._show_held_language())
    assert ("lang_pinned", {"lang": "en", "by": "talk"}) in rows


def test_the_loop_shows_the_held_language_after_her_turn_and_the_guests_words():
    src = (ROOT / "app" / "session.py").read_text(encoding="utf-8")
    tc = src.split('elif event.kind == "turn_complete":', 1)[1].split("elif event.kind ==", 1)[0]
    ut = src.split('elif event.kind == "user_transcript":', 1)[1].split("elif event.kind ==", 1)[0]
    assert "await self._show_held_language()" in tc and "await self._show_held_language()" in ut


def test_a_fresh_session_is_told_the_held_language_and_a_resume_is_not():
    from app.providers.gemini import GeminiProvider

    p = GeminiProvider("Kore", "SYSTEM", use_tools=False)
    base = p._build_config()["system_instruction"]
    assert base == "SYSTEM"                          # nothing picked: as it always was
    p._talk.pin("ru")
    assert p._build_config()["system_instruction"].startswith("SYSTEM\n\nลูกค้าเลือกภาษารัสเซียเอง")
    p._resume_handle = "h1"                          # a resume keeps the instructions it had
    assert p._build_config()["system_instruction"] == "SYSTEM"


def test_russian_room_type_questions_get_the_buttons():
    assert quick_asks.choices_asked(
        "У нас есть студии, квартиры с одной спальней и с двумя спальнями. Какой тип квартиры вас интересует?"
    ) == "room_types"
    assert quick_asks.choices_asked("Бассейн Sky Pool находится на 3 этаже. Хотите узнать больше?") is None


def test_a_pick_keeps_the_screen_and_says_whether_it_cut_her_off():
    html = PAGE.read_text(encoding="utf-8")
    send = html[html.index("    function sendQuickAsk(id) {"):html.index("    function toggleMute() {")]
    assert "var langPick = id.indexOf('lang_') === 0, cut = !!playing;" in send
    owed = send[send.index("if (!langPick) {"):]
    assert owed.index("answerOwed = true;") < owed.index("}")      # only a question owes an answer
    # the server's confirmation is not her answer: the Thinking fallback still runs
    # (head_look joined 31 Oct: the head's look is not her answer either)
    assert ("if (evt.type !== 'attention' && evt.type !== 'lang_pinned' && evt.type !== 'head_look') "
            "serverSeq++;") in html
