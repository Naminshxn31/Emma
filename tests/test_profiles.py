"""
One engine, two personas: the gallery receptionist and the owner's Emma.

The profile system was chosen over forking the repo, so what these tests
guard is the seam between the two: the condo profile must be byte-for-byte
the behaviour the gallery has always had, and Emma must not inherit the
parts of it that only exist because the other end of the conversation is a
stranger in a sales gallery.
"""
from __future__ import annotations

from app.prompts import EMMA_GREETING, GREETING, build_instructions, greeting_for


# ==================== the default must be the gallery ====================


def test_no_profile_argument_means_the_receptionist():
    """Every caller and test written before profiles exists calls this with
    no profile. If the default drifted, the gallery machine would change
    persona on a git pull, silently."""
    assert "ห้ามตอบเรื่องอื่นทุกกรณี" in build_instructions("X")


def test_a_typo_in_the_profile_lands_on_the_receptionist():
    """ASSISTANT_PROFILE is hand-typed in .env. The safe failure for this
    project is the persona that has 544 tests behind it, not a half-built
    one and not an exception at session start."""
    text = build_instructions("X", profile="jarvsi")
    assert "ห้ามตอบเรื่องอื่นทุกกรณี" in text


# ==================== what Emma must not inherit ====================


def test_emma_is_not_topic_locked():
    """The topic ban exists because the receptionist once recommended Xiaomi
    phones to a customer. Refusing the *owner* an answer is the mirror-image
    failure, so the ban must not leak across."""
    text = build_instructions("X", robot_name="Emma", profile="emma")
    assert "ห้ามตอบเรื่องอื่นทุกกรณี" not in text
    assert "คุยได้ทุกเรื่อง" in text


def test_emma_gets_no_slide_or_condo_search_rules():
    """An instruction that names a tool is a promise the tool exists. Emma's
    tool set has no slides and no condo search; rules mentioning them would
    have the model calling functions that were never declared."""
    text = build_instructions("X", profile="emma")
    for tool_name in ("show_slide", "start_presentation", "next_slide",
                      "search_condo_info"):
        assert tool_name not in text, tool_name


def test_emma_does_not_recite_the_condo_facts():
    """The facts block is draft sales copy with empty prices, written for a
    receptionist. It must not ride along into the owner's assistant."""
    text = build_instructions("Embassy World", profile="emma")
    assert "Embassy World" not in text
    assert "Preliminary Concept" not in text


def test_emma_knows_who_she_is_talking_to():
    text = build_instructions("X", robot_name="Emma", profile="emma")
    assert "เจ้าของ" in text
    assert "Emma" in text


def test_emma_without_a_name_still_has_one():
    """ROBOT_NAME is blank in a fresh .env; a nameless persona reads as
    broken the moment someone asks 'who are you'."""
    assert "Emma" in build_instructions("X", robot_name="", profile="emma")


# ==================== what Emma must keep ====================


def test_the_paid_for_lessons_survive_the_profile_switch():
    """These rules came from real incidents, none of which were about
    condos: mishearings guessed at, an AC 'switched off' that never heard
    the command, markdown read aloud. A new persona re-learning them the
    hard way is the failure the shared-repo decision was meant to prevent."""
    text = build_instructions("X", profile="emma")
    assert "ห้ามเดา" in text                       # unclear audio: ask, never guess
    assert "ทวนยืนยัน" in text                     # numbers/names read back
    assert "mock" in text and "failed" in text     # tool results read first
    assert "ห้ามบอกว่าสำเร็จ" in text
    assert "markdown" in text                      # nothing read aloud as symbols
    assert "ห้ามแต่งข้อมูล" in text                # no invented facts


def test_emma_has_real_memory_rules_now():
    """Phase 3 is built, so the old "ยังไม่มีความจำข้ามเซสชัน" honesty line
    is retired — but the honesty itself is not: the rule that replaced it
    must still forbid invented memories, which is the same lie one layer
    deeper. tests/test_memory.py owns the storage behaviour."""
    text = build_instructions("X", profile="emma")
    assert "ยังไม่มีความจำข้ามเซสชัน" not in text
    assert "remember" in text
    assert "ห้ามแต่งความทรงจำ" in text


# ==================== greeting ====================


def test_each_profile_greets_its_own_audience():
    """The receptionist introduces the project to a stranger; Emma's owner
    hearing a sales pitch would be the profile system failing out loud on
    the first sentence of every session."""
    assert greeting_for("condo") is GREETING
    assert greeting_for("emma") is EMMA_GREETING
    assert "โครงการ" not in EMMA_GREETING
    # Unknown profile: same rule as the instructions — fall back to gallery.
    assert greeting_for("jarvsi") is GREETING


# ==================== tools ====================


def test_emma_defaults_to_home_tools_only(monkeypatch):
    """A tool the model can see is a tool it will eventually call. Emma with
    `start_presentation` would narrate the condo deck in a living room.
    Smarthome and reminders are hers; everything else is gallery equipment."""
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert settings.enabled_tool_groups() == {
        "smarthome", "reminders", "memory", "mydocs", "websearch", "computer",
    }


def test_an_explicit_tool_list_beats_the_profile_default(monkeypatch):
    """Trying a tool group out on either persona should stay a one-line
    .env change, not a code change."""
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "smarthome,documents")
    assert settings.enabled_tool_groups() == {"smarthome", "documents"}


def test_the_condo_profile_still_gets_everything(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert settings.enabled_tool_groups() is None


def test_emma_refuses_instead_of_pretending():
    """From the first real session, 2026-08-22 14:09: asked to แนะนำโครงการ,
    Emma narrated a sales pitch from world knowledge; told "ปิดสไลด์", she
    answered "ปิดหน้าต่างพรีเซนต์เรียบร้อยค่ะ" — with no slide tool loaded —
    and the misheard follow-ups became four real set_lights calls in nine
    seconds. The turn log has the receipts: zero slide tools in that session.

    A capability she doesn't have must be declined by name, never claimed
    done, and never rerouted onto the nearest tool that does exist."""
    text = build_instructions("X", profile="emma")
    assert "ไม่มีเครื่องมือ" in text
    assert "ห้ามตอบว่าทำแล้ว" in text
    assert "ปิดสไลด์ไม่ใช่ปิดไฟ" in text


def test_speaking_a_language_is_never_a_tool_call():
    """Real session, 2026-08-22: "พูดอะไรก็ได้เป็นภาษาญี่ปุ่นยาวๆ" sent Emma
    to search_web — which happened to be failing — so she announced she
    "couldn't find a Japanese example" and apologised, for a thing she can
    do natively in ~97 languages. The docs-first rule needed the boundary
    stated: language ability is hers, not a lookup."""
    text = build_instructions("X", profile="emma")
    assert "ความสามารถของตัวคุณเอง" in text
    assert "ห้ามไปค้นเว็บหา" in text


# ==================== the translator ====================


def test_the_translator_translates_and_does_nothing_else():
    """The discipline of the profile is what it does NOT do: no answering
    (it translates the question instead), no opinions, no tools, no gallery
    facts, no owner's memory — an interpreter carrying private context into
    a room of strangers is a leak wearing headphones."""
    text = build_instructions("Embassy World", profile="translator")
    assert "ล่าม" in text
    assert "ภาษาไทย ให้พูดคำแปลเป็นภาษาอังกฤษ" in text
    assert "ภาษาอื่นที่ไม่ใช่ไทย ให้พูดคำแปลเป็นภาษาไทย" in text
    assert "ห้ามเรียกใช้เครื่องมือ" in text
    assert "ไม่ใช่ตอบมัน" in text                 # translate the question, don't answer it
    # Each of these quotes a sentence from the first real interpreter
    # session, 2026-08-24 — the house technique, because the generic rule
    # alone let all three happen:
    assert "ห้ามแปลเป็น \"I love you\"" in text   # 我爱你 went to English, twice
    assert "ไม่ใช่ \"ฉันชื่อโชกุนค่ะ\"" in text   # ค่ะ added to a male speaker's words
    assert "ห้ามตอบว่า \"สบายดีค่ะ\"" in text     # answered a greeting instead of translating
    assert "Embassy World" not in text            # no gallery facts
    assert "ความจำ" not in text                   # no owner memory block


def test_the_translator_has_zero_tool_groups(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "translator")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert settings.enabled_tool_groups() == set()


def test_the_translator_session_is_toolless_by_config(monkeypatch):
    """Not by prompt: a declared tool is a callable tool whatever the
    instructions say, and an interpreter calling set_lights mid-sentence is
    not a theoretical failure in this codebase."""
    from app.providers.openai_realtime import build_session_config

    session = build_session_config("marin", "x", use_tools=False)["session"]
    assert "tools" not in session

    from app.providers.gemini import GeminiProvider

    provider = GeminiProvider("Kore", "x", use_tools=False)
    config = provider._build_config()
    assert "tools" not in config


def test_the_translator_greeting_is_bilingual_and_nothing_more():
    g = greeting_for("translator")
    assert "Interpreter" in g and "ล่าม" in g


def test_mic_shaping_defaults_keep_the_gallery_audio_untouched():
    """MIC_BOOST=1.0 skips the compressor chain entirely — the gallery's
    audio path must stay byte-identical to what shipped before this knob
    existed. Asserted on source defaults, not Settings() (which bakes the
    developer's .env at import — the documented trap)."""
    import inspect
    import re

    from app import config

    src = inspect.getsource(config)
    assert re.search(r'os\.getenv\("MIC_BOOST",\s*"1\.0"\)', src)
    assert re.search(r'_get_bool\("MIC_NOISE_SUPPRESSION",\s*True\)', src)


def test_the_translator_target_language_is_a_parameter():
    """Item 7 on the sales boss's list names Spanish, French, German,
    Chinese and Arabic customers. Staff Thai goes out in the customer's
    language; whatever the customer speaks still comes back as Thai."""
    es = build_instructions("X", profile="translator", translator_target="es")
    assert "ภาษาสเปน" in es
    ar = build_instructions("X", profile="translator", translator_target="ar")
    assert "ภาษาอาหรับ" in ar
    # The return direction never moves: customer speech -> Thai, always.
    assert "ภาษาไทยเสมอ" in es
    # Unknown code lands on English, not an exception mid-greeting.
    assert "ภาษาอังกฤษ" in build_instructions("X", profile="translator",
                                              translator_target="xx")


def test_the_gallery_prompt_learns_the_library_only_when_it_is_loaded(monkeypatch):
    """Adding mydocs to TOOL_GROUPS was measured to be not enough: the tool
    registered and the robot never called it, because rule 14 routes every
    unknown to search_condo_info and nothing in the prompt said a library
    existed. A tool the model has no reason to reach for is the same as no
    tool.

    Gated on the group: the gallery default (blank TOOL_GROUPS, mydocs
    opt-in and absent) keeps its prompt byte-identical on a pull."""
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "")
    plain = build_instructions("X", extra_facts="")
    assert "search_my_documents" not in plain, \
        "the default gallery prompt must not mention a tool it does not have"

    monkeypatch.setattr(settings, "tool_groups", "smarthome,slides,knowledge,mydocs")
    withlib = build_instructions("X", extra_facts="")
    assert "search_my_documents" in withlib
    # The half that keeps the numbers honest: articles are marketing copy,
    # and their figures ("yields 7-10%") have no approver.
    assert "ห้ามอ้างตัวเลขการเงินจากบทความ" in withlib
    assert "ฝ่ายขาย" in withlib.split("ห้ามอ้างตัวเลขการเงินจากบทความ", 1)[1]


# ==================== the translator switch on the page ====================


def _client_src():
    from pathlib import Path

    return (Path(__file__).parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")


def test_the_switch_languages_are_real_server_languages():
    """The client's TRANSLATOR_LANGS list feeds ?lang= straight into
    build_instructions; a code the server does not know falls back to
    English with only a log warning. Cross-check the two maps so the client
    cannot drift into offering a language the prompt cannot name."""
    import re

    from app.prompts import _LANG_NAMES

    src = _client_src()
    block = src[src.index("const TRANSLATOR_LANGS"):]
    block = block[:block.index("};")]
    codes = re.findall(r"(\w{2}):\s*'", block)
    assert codes, "could not parse TRANSLATOR_LANGS from the client"
    for code in codes:
        assert code in _LANG_NAMES, f"client offers {code!r}, server cannot name it"
    assert "th" not in codes, "ไทย ↔ ไทย is not a translation"


def test_flipping_the_switch_mid_call_goes_through_the_close_path():
    """The old socket's onclose releases the microphone unconditionally —
    that line exists because every exit used to leak the mic. A new call
    dialed before it fires gets its mic pulled from under it (digital
    silence, peak=0.0000). So setTranslator must never call startCall()
    itself: it closes the socket and lets onclose consume modeRestart,
    after the standDown branch so a superseded tab stays lost."""
    src = _client_src()
    body = src[src.index("function setTranslator"):]
    body = body[:body.index("\n$('translatorBtn')")]
    assert "startCall(" not in body, \
        "setTranslator dials over a live socket — mic race reintroduced"
    assert "modeRestart = true" in body and "ws.close()" in body
    # The switch edits the URL — the one source of truth startCall reads.
    assert "history.replaceState" in body

    onclose = src[src.index("ws.onclose = "):][:12000]
    restart = onclose.index("if (modeRestart)")
    assert onclose.index("standDown = false") < restart, \
        "a superseded tab must stand down before any mode-switch redial"
    assert restart < onclose.index("autoConnect && !manualEnd"), \
        "modeRestart must win before the generic redial backoff"


def test_emma_is_told_formatted_verse_goes_silent():
    """2026-08-26, measured three times (turnlog 14:50, 14:53, 17:11): Emma
    delivered a rap verse as a quoted multi-line block — the text hit the
    transcript as one atomic chunk and the speakers stayed silent, while
    every flowing-speech delivery (5 probe sessions: same words, same
    profanity, one with Emma's full prompt) produced complete audio. The
    model then told the owner "ได้แต่พิมพ์" — a capability it invented to
    explain its own silence, and then argued when the owner said he heard
    nothing. The rule pins both halves: verse must be flowing speech, and
    the text-only excuse is forbidden by name."""
    from app.prompts import build_instructions

    text = build_instructions("X", profile="emma")
    assert "ประโยคพูดต่อเนื่อง" in text
    assert "ได้แต่พิมพ์" in text, "the observed excuse must be quoted verbatim"


def test_no_websearch_group_means_no_websearch_in_the_prompt(monkeypatch):
    """Live, 2026-08-27: "ทำไมเลือกบริษัทไทย" was answered from
    siamconsultancy.com with a "ถูกกว่า 75%" figure nobody signed — spoken
    with the robot's confidence to a would-be buyer. The owner's call:
    Emma is becoming the information assistant, the information must be
    the company's own prepared data, web search off. With the group off,
    the prompt must stop naming search_web (an instruction naming an
    undeclared tool is the refusing-model bug) and must not promise a
    web fallback that no longer exists."""
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups",
                        "smarthome,reminders,memory,mydocs,computer")
    text = build_instructions("X", profile="emma")
    assert "search_web" not in text
    assert "ค้นเอกสาร เปิดเว็บบนจอ" in text          # rule 6, websearch dropped
    assert "ห้ามอ้างข้อมูลจากเว็บภายนอก" in text

    # And the machines that keep the group keep the fallback.
    monkeypatch.setattr(settings, "tool_groups", "")
    assert "ไม่พบค่อยใช้ search_web" in build_instructions("X", profile="emma")

    # The tool's own description must work on both kinds of machine, so it
    # names no fallback tool at all.
    from app.tools import load_tools, registry

    load_tools()
    assert "search_web" not in registry.get("search_my_documents").description


def test_this_project_means_our_own_projects(monkeypatch):
    """Same session: "สิ่งอำนวยความสะดวกของโครงการนี้" got "ไม่ทราบว่าเป็น
    โครงการอะไร" — with the facilities sitting in the sales kit in her own
    library. "โครงการนี้" from a guest in this company's building means this
    company's projects, and recommendations stay in-group."""
    text = build_instructions("X", profile="emma")
    assert "ในเครือ Empire" in text
    assert "ไม่ทราบว่าเป็นโครงการอะไร" in text       # the quoted live failure
    assert "ห้ามยกโครงการนอกเครือ" in text
