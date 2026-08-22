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


def test_emma_admits_she_has_no_long_term_memory_yet():
    """Phase 3 is not built. Until it is, 'จำไว้หน่อย' answered with a
    confident 'จำแล้วค่ะ' would be the assistant inventing a capability —
    the exact class of lie the hardware rules exist to prevent."""
    assert "ยังไม่มีความจำข้ามเซสชัน" in build_instructions("X", profile="emma")


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


def test_emma_defaults_to_smarthome_tools_only(monkeypatch):
    """A tool the model can see is a tool it will eventually call. Emma with
    `start_presentation` would narrate the condo deck in a living room."""
    from app.config import settings

    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert settings.enabled_tool_groups() == {"smarthome"}


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
