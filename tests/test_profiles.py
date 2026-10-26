"""
One engine, two personas: the gallery receptionist and the owner's Emma.

The profile system was chosen over forking the repo, so what these tests
guard is the seam between the two: the condo profile must be byte-for-byte
the behaviour the gallery has always had, and Emma must not inherit the
parts of it that only exist because the other end of the conversation is a
stranger in a sales gallery.
"""
from __future__ import annotations

import json

import pytest

from app.prompts import EMMA_GREETING, GREETING, build_instructions, greeting_for


@pytest.fixture
def sales_context(monkeypatch, tmp_path):
    """Pin the sales-context source to a copy of the real file in a chosen
    approval state, so a test states which state it is about.

    These tests used to read the real file and assumed it was a draft; the
    owner approved it on 2026-10-06 and four of them turned red although
    nothing was wrong — the gate had done exactly its job. A test of the
    gate must not change colour when somebody signs the file."""
    from app import prompts
    from app.config import ACTIVE_PROJECT_ID
    from app.data_console import PERMISSION_DRAFT

    real_path = prompts.source_path

    def use(approved: bool) -> None:
        data = json.loads(real_path("project_sales_context", ACTIVE_PROJECT_ID)
                          .read_text(encoding="utf-8"))
        if approved:
            data.update(approval_status="approved", approved_by="test",
                        approved_at="2026-01-01T00:00:00+07:00",
                        effective_at="2026-01-01T00:00:00+07:00",
                        disclosure_scope="customer", content_state="preliminary_concept")
        else:
            data.update(PERMISSION_DRAFT)
        copy = tmp_path / "sales_context.json"
        copy.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(prompts, "source_path", lambda sid, pid: (
            copy if sid == "project_sales_context" else real_path(sid, pid)))

    return use


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


#: Sales-room text that must never reach the owner's assistant or the joke
#: face: the project name, the receptionist's scope refusal, and the
#: robot's own sales-talk slips of 19 Oct quoted in the language rule.
_SALES_ONLY = ("Embassy World", "only provide information about", "Kondom",
               "Möbelpaket", "Jedes Zimmer")


@pytest.mark.parametrize("face", [False, True])
@pytest.mark.parametrize("profile", ["emma", "joke"])
def test_the_shared_language_rule_carries_no_sales_sentence(profile, face):
    """CLAUDE.md: "กฎภาษาใช้ร่วมทุกโปรไฟล์ = ห้ามชื่อโครงการ/ประโยคขายในกฎนั้น".
    The 19 Oct language fix quoted two condo slips in that shared rule -- the
    refusal "I can only provide information about the project", and an
    accented condo question ("Bei der Kondom ist inklusive Karte?") answered
    with a furniture-package claim ("Jedes Zimmer wird mit einem Möbelpaket
    geliefert") -- and the pre-commit review of 26 Oct found both in these
    two prompts. The old check here looked only for "Embassy World". The
    instruction itself must survive the move, in neutral words."""
    text = build_instructions("Embassy World", languages="auto", robot_name="Emma",
                              profile=profile, robot_face=face)
    for phrase in _SALES_ONLY:
        assert phrase not in text, "%s/face=%s carries %r" % (profile, face, phrase)
    assert "ประโยคเดียวที่ฟังเหมือนภาษาใกล้กันจากสำเนียง" in text
    assert "คุยอังกฤษอยู่แล้วได้ยินประโยคเดียวเป็นเยอรมัน ให้ตอบอังกฤษ" in text
    assert "ผิด: 'I cannot speak Hebrew'; " in text


@pytest.mark.parametrize("layout", ["v1", "v2"])
def test_the_condo_language_rule_keeps_the_measured_quotes(layout, monkeypatch):
    """The other half: the condo prompt keeps the 19 Oct slips word for word,
    at the same places -- that wording is what the live language eval of 19
    Oct measured (22/22 correct), so moving them out of the shared rule
    must not weaken the sales room."""
    from app.config import settings

    monkeypatch.setattr(settings, "prompt_layout", layout)
    for face in (False, True):
        text = build_instructions("Embassy World", languages="auto", robot_name="Emma",
                                  robot_face=face)
        assert "ผิด: 'I can only provide information about the project' 'I cannot speak Hebrew'; " in text
        assert ("และประโยคเดียวที่ฟังเหมือนภาษาใกล้กันจากสำเนียง "
                "คุยอังกฤษอยู่แล้วได้ยิน 'Bei der Kondom ist inklusive Karte?' ให้ตอบอังกฤษ "
                "ผิด: 'Jedes Zimmer wird mit einem Möbelpaket geliefert' ได้ยิน 'ใช่' "
                "ผิด: 'ค่ะ มีส่วนไหน...'; ") in text
        assert "ได้ยินประโยคเดียวเป็นเยอรมัน" not in text


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


# ==================== the sales host (2026-09-11) ====================


def test_the_gallery_prompt_carries_the_sales_host_voice(sales_context):
    """Keep generic host style while draft sales copy stays outside prompt."""
    sales_context(approved=False)
    text = build_instructions("X")
    assert "ตัวตนและน้ำเสียง" in text
    assert "ขายชีวิตที่ดีขึ้นก่อน" not in text
    # Owner 2026-10-10: the concept caveat is banned, quoted so the model
    # matches it against what it is about to say (the old rule "แนวคิดหรือ
    # กำลังพัฒนาห้ามพูดเหมือนเสร็จแล้ว" is what produced it).
    assert 'ห้ามพูดว่า "เป็นแนวคิดเบื้องต้น" หรือ "อาจมีการเปลี่ยนแปลง"' in text
    assert "ห้ามพูดเหมือนเสร็จแล้ว" not in text
    assert "ห้ามนำข้อมูลจากโครงการอื่น" in text


def test_the_sales_host_block_carries_no_numbers():
    """Every figure the host may say is in the facts block or comes back
    from a tool. A persona block that smuggled in "250 metres from the
    beach" would be a fact with no approver riding on the persona's
    authority, and the persona is re-read every turn."""
    import re

    from app.prompts import SALES_HOST_BLOCK

    assert not re.search(r"\d", SALES_HOST_BLOCK), "numbers belong in condo_facts.json"


def test_a_broad_facilities_question_offers_the_floors_instead_of_the_list():
    """Owner 2026-10-10: instead of reciting every facility, offer a choice of
    floor. The floor names come from the approved "จุดเด่น" fact (this block
    carries no numbers), and the follow-up question is written as an
    exception to "ค่าเริ่มต้นคือไม่ถามต่อ" — two rules racing for one turn is
    a failure this prompt has paid for twice."""
    from app.prompts import SALES_HOST_BLOCK

    line = next(ln for ln in SALES_HOST_BLOCK.splitlines() if ln.startswith("- สิ่งอำนวยความสะดวก:"))
    # Owner 2026-10-11: one inviting sentence ("มากมายถึง 3 ชั้น ลูกค้าอยาก
    # เห็นชั้นไหนเป็นพิเศษไหมคะ"), then only what the guest picks — built by
    # facility_hook from the facts, so the block itself stays digit-free.
    assert "ห้ามไล่ชื่อ" in line and '"[ประโยคสิ่งอำนวยความสะดวก]"' in line
    assert "แล้วค่อยเล่าเฉพาะชั้นหรือสิ่งที่ลูกค้าเลือก" in line
    assert "ถามว่ามีอะไรอีกค่อยบอกโซนภายนอกและเทคโนโลยี" in line
    # Facilities only: the re-run turned "แนะนำโครงการหน่อย" into the menu.
    assert "ถามกว้างว่ามีสิ่งอำนวยความสะดวกอะไรบ้าง" in line
    # 203-case run 2026-10-11: "Can you tell me about this project?" got only
    # "Welcome to Embassy World.", and "มีสระไหม ฟิตเนสไหม" got the menu.
    assert 'ให้บอกว่าเป็นคอนโดแบบไหน กี่ชั้น กี่อาคาร ย่านไหน จาก "โครงการ" และ "ทำเล" ก่อนเล่าแนวคิด' in line
    # Re-run: "มีค่ะ ... อยู่ในตัวโครงการนะคะ" — rule 6's walk answer leaking in.
    assert 'ถามว่ามีสิ่งใดไหม เช่น "มีสระไหม" เป็นคำถามเฉพาะ ตอบว่ามีและอยู่ชั้นไหนตาม "จุดเด่น" (ห้ามตอบแค่ว่าอยู่ในตัวโครงการ)' in line
    assert 'ข้อยกเว้นของ "ไม่ถามต่อ"' in line
    # The exception is written into the default rule itself too: the v2
    # layout puts that rule under a header that outranks everything below it.
    default = next(ln for ln in SALES_HOST_BLOCK.splitlines() if ln.startswith("- ค่าเริ่มต้นคือไม่ถามต่อ"))
    assert "ถามกว้างว่ามีสิ่งอำนวยความสะดวกอะไร" in default
    # Owner 12 Oct on the robot: "เยี่ยมมาก" and a remark got a bare "ค่ะ" --
    # the "ถูก: ได้เลยค่ะ" example taught the model that shortest is best.
    assert 'ห้ามตอบคำเดียว เช่น "ค่ะ" "ครับ"' in default
    assert '"ขอบคุณมากค่ะ ดีใจที่ชอบนะคะ"' in default
    # Banned for one evening and undone by the owner (2026-10-11): "ให้ชม"
    # can mean showing a picture, not walking there.
    assert "ให้ชม" not in line and "ให้บริการ" not in line


def test_the_203_case_review_fixes_are_in_the_condo_prompt():
    """2026-10-11, 203 scenarios judged against the approved sources. Each
    line quotes what went wrong: refusals ending "How can I help you today?"
    (the commonest flag), "ไปสนามบินอู่ตะเภานานไหม" refused as off-topic though
    a slide has it, gibberish called off-topic, "โครงการของเราได้รับรางวัล"
    (the developer's awards), "ตามกฎหมาย" for foreign ownership, "เรียนเชิญ
    พนักงานขายมาดูแล" with no way to call anyone, find_units' rooms never
    named, and "ไม่ใช่ครับ ตึก C" opening floor 1 instead of the room."""
    text = build_instructions("X")
    # robot 20 Oct: nearby restaurants and the developer's other projects ("Empire Tower") too
    assert "ไม่ใช่นอกเรื่อง: ทำเลและการเดินทางจากโครงการ (สนามบิน หาด ห้าง โรงพยาบาล ร้านอาหารและที่เที่ยวใกล้โครงการ) ข้อเท็จจริงของโครงการ (ขนาด จำนวน ความสูง ที่จอดรถ วัสดุ ระบบ) และผู้พัฒนา ผู้บริหาร โครงการอื่นของผู้พัฒนา ให้ค้นด้วย search_condo_info ก่อนเสมอ ห้ามส่งต่อทีมขายโดยไม่ค้น" in text
    assert "ห้ามด้อยค่าหรือจัดอันดับที่อื่น" in text
    # owner 12 Oct: ask again AND offer the project's topics, never a bare "ค่ะ"
    assert "เสียงหรือคำที่ฟังไม่ออก ให้ขอให้พูดอีกครั้งพร้อมเสนอหัวข้อ" in text
    assert "ห้ามตอบว่าเป็นเรื่องนอกขอบเขต" in text
    # filler / unrelated chatter -> acknowledge + ONE project question (owner 12 Oct)
    assert 'ให้ตอบรับสั้นๆ แล้วชวนคุยเรื่องโครงการหนึ่งคำถาม (ข้อยกเว้นของ "ไม่ถามต่อ")' in text
    assert "ห้ามถามซ้ำข้อที่ถามไปแล้ว ห้ามถามว่าซื้อไว้อยู่เองหรือลงทุน ห้ามถามเรื่องงบหรือราคา" in text
    # owner 15 Oct: an unrelated question gets no content at all (robot log: an
    # English word was translated, then "อยู่เองหรือลงทุน"), and the one question
    # invites a recommendation from the project, never "live in or invest"
    assert "คำถามที่ไม่เกี่ยวกับโครงการ (คำศัพท์ แปลภาษา ความรู้ทั่วไป โครงการอื่น) ห้ามตอบเนื้อหาแม้แต่คำเดียว" in text
    assert '"อยากให้ดิฉันแนะนำส่วนไหนของโครงการเป็นพิเศษไหมคะ"' in text
    assert "อยู่เองหรือไว้ลงทุนคะ" not in text
    assert 'ห้ามพูดว่าโครงการนี้ได้รางวัล' in text
    assert "กฎหมาย สิทธิ์ถือครอง หรือโควตาต่างชาติ ไม่มีในข้อมูล ให้ทีมขายยืนยัน" in text
    assert 'ห้ามพูดว่า "เรียนเชิญพนักงานขายมาดูแล"' in text
    assert "find_units ได้ห้องมา ให้บอกแบบห้องและเลขห้องจากผล ไม่ต้องพูดราคา ห้ามเลี่ยงไปทีมขาย" in text
    assert "ลูกค้าแก้ตึกหรือเลขห้อง ให้เรียก show_unit ห้องที่แก้แล้ว" in text
    # condo only: the owner's other profiles keep their own rule 3
    emma = build_instructions("X", profile="emma")
    assert "เสียงหรือคำที่ไม่มีความหมาย" not in emma


def test_the_364_faq_review_fixes_are_in_the_condo_prompt():
    """2026-10-11, the report's 364 FAQ judged: Thai questions carrying an
    English term ("Freehold หรือ...", "Google Home") answered in English;
    refund, resale and document questions refused as off-topic; corner and
    Pool Access rooms declined without asking the sales system; and "เปิด
    กี่โมง ค่าใช้เท่าไหร่" answered with the floor invitation."""
    text = build_instructions("X")
    assert "ประโยคไทยที่มีคำอังกฤษปน (เช่น Freehold, Studio, Google Home) คือภาษาไทย ตอบไทย" in text
    assert "การซื้อ สัญญา เอกสาร การยกเลิก คืนเงินจอง ขายต่อ และศัพท์หรือข้อมูลของโครงการ ก็ไม่ใช่นอกเรื่อง" in text
    assert "ถามรายละเอียด (เวลาเปิด ค่าใช้ ขนาด จำนวน สิทธิ์ใช้) ไม่ใช่คำถามกว้าง" in text
    # Owner 2026-10-12: Utapao is 30 min as the slide says (now in the facts);
    # captions also print "40-60 นาที" and cannot be edited in the console.
    assert "ห้ามพูดว่า \"ไม่ติดทะเล\"" in text   # owner 12 Oct: lead with the distance
    assert "ข้อมูลโครงการกับคำบรรยายภาพขัดกันให้ใช้ข้อมูลโครงการ" in text
    assert "ห้ามใช้ประโยคชวนเลือกชั้น" in text
    # Re-run: told to ask the system about corner/Pool Access rooms, Emma
    # read 132 AVAILABLE corner rooms as the total (217) and decoded view
    # codes the report says have no official meaning.
    assert "ถามจำนวนห้องว่างตามแบบ ตึก หรือวิว ให้เรียก compare_unit_types หรือ find_units ก่อน" in text
    assert "ตัวเลขที่ได้คือห้องว่าง ณ ตอนนี้ ไม่ใช่จำนวนทั้งหมด" in text
    assert "รหัสวิวหรือคอลเลกชันในระบบห้ามตีความเอง" in text


def test_the_facility_sentence_counts_floors_from_the_approved_facts(monkeypatch):
    """The "3 ชั้น" of the owner's sentence is read from the approved
    "จุดเด่น" fact (its floor sections — the outdoor zone and the technology
    are not floors), never typed into the persona; no fact, no number. "เห็น"
    only where answering can put a picture up."""
    from app.config import settings
    from app.prompts import facility_hook

    facts = "\n".join(["## ข้อมูลโครงการ",
                       "- จุดเด่น: ชั้น G: Lobby, ฟิตเนส · ชั้นใต้ดิน: ซาวน่า · ชั้น 3 อาคาร A: Sky Pool "
                       "· ภายนอก: Water Lagoon · เทคโนโลยี: Smart Home", ""])
    monkeypatch.setattr(settings, "tool_groups", "knowledge,units")
    monkeypatch.setattr(settings, "answer_images", True)
    assert facility_hook(facts) == "ในโครงการของเรามีสิ่งอำนวยความสะดวกมากมายถึง 3 ชั้น ลูกค้าอยากเห็นชั้นไหนเป็นพิเศษไหมคะ"
    monkeypatch.setattr(settings, "answer_images", False)
    assert "ลูกค้าอยากรู้จักชั้นไหนเป็นพิเศษไหมคะ" in facility_hook(facts)
    monkeypatch.setattr(settings, "tool_groups", "")          # gallery default: slides loaded
    assert "อยากเห็น" in facility_hook(facts)
    bare = facility_hook("\n".join(["## ข้อมูลโครงการ", "- ทำเล: จอมเทียน", ""]))
    assert "มากมายหลายชั้น" in bare and not any(ch.isdigit() for ch in bare)
    assert "[ประโยคสิ่งอำนวยความสะดวก]" not in build_instructions("X", extra_facts=facts)
    assert "มากมายถึง 3 ชั้น" in build_instructions("X", extra_facts=facts)


def test_asked_to_walk_to_a_facility_she_says_it_is_in_the_project():
    """Rule 6's right answer ("มีห้องตัวอย่างให้ชมค่ะ แต่ตอนนี้เอ็มม่ายัง
    ไม่สามารถพาเดินไป...") is true of the show unit in the gallery and was
    copied onto the gym: "มีฟิตเนสให้บริการค่ะ แต่ตอนนี้...ไปยังฟิตเนสได้ค่ะ"
    (eval 2026-10-10) — a gym a short walk away, in a building not yet
    built. Rule 6 quotes that line as wrong and says where it is, with no
    concept caveat (owner, same day)."""
    text = build_instructions("X")
    rule6 = next(ln for ln in text.splitlines() if ln.startswith("6. "))
    assert 'ผิด: "มีฟิตเนสให้บริการค่ะ' in rule6
    assert 'ถูก: "ฟิตเนสอยู่ในตัวโครงการค่ะ ไม่ได้อยู่ที่ห้องขายนี้"' in rule6
    assert "แนวคิด" not in rule6


def test_approved_sales_context_reaches_the_prompt(sales_context):
    """The other half of the gate: once signed (pete, 2026-10-06) the story
    and scope rules ride in the prompt — including the line that names
    Embassy Life as a different project, the boundary the 2026-09-14
    mix-up was fixed with."""
    sales_context(approved=True)
    text = build_instructions("Embassy World")
    assert "ขายชีวิตที่ดีขึ้นก่อน" in text
    assert "Embassy Life" in text and "คนละโครงการ" in text


def test_the_sales_host_block_keeps_embassy_world_apart_from_other_projects(sales_context):
    """The mydocs corpus mixes Empire projects: Embassy Life (Pattaya) sits
    next to Embassy World. Measured 2026-09-14 the model answered an Embassy
    World facilities question from Embassy Life chunks, and injected facility
    names and a lagoon size ("155 metres", "Thermal Galaxy") from its own
    training memory of the project — not from any document. Naming the
    boundary fixed the project mix-up (the model then said "that is Embassy
    Life"); forbidding invented details is the guardrail for the rest, even
    though prompt text alone did not fully stop a strong training prior on
    the proxy model. Both rules must stay in the persona, re-read every turn."""
    from app.prompts import SALES_HOST_BLOCK

    sales_context(approved=False)
    text = build_instructions("Embassy World")
    assert "Embassy Life" not in text and "Embassy World" in text
    assert "Biogenesis" not in SALES_HOST_BLOCK, "project facts belong in project data"
    assert "ความรู้เดิม" in SALES_HOST_BLOCK, "must forbid inventing from the model's own memory"


def test_sales_host_answers_customer_intent_before_the_story():
    from app.prompts import SALES_HOST_BLOCK

    assert "ตอบสิ่งที่ลูกค้าพูดก่อนเสมอ" in SALES_HOST_BLOCK
    assert "ห้ามดึงบทสนทนากลับไป" in SALES_HOST_BLOCK
    assert "ตอบเฉพาะสิ่งที่จำเป็นต่อ intent นั้นก่อน" in SALES_HOST_BLOCK


def test_sales_host_keeps_a_no_intent_opening_brief():
    from app.prompts import SALES_HOST_BLOCK

    assert 'ลูกค้าบอกว่า "ยังไม่รู้อะไรเลย"' in SALES_HOST_BLOCK
    assert '"ยินดีต้อนรับค่ะ ที่นี่คือ Embassy World ค่ะ" เท่านั้นแล้วหยุด' in build_instructions("Embassy World")
    assert "ห้ามถามเรื่องแบบห้อง" in SALES_HOST_BLOCK
    assert "ห้าม" in SALES_HOST_BLOCK and "ทำเล แนวคิด facility" in SALES_HOST_BLOCK


def test_sales_host_uses_a_personal_clue_once_then_stops(sales_context):
    sales_context(approved=False)
    text = build_instructions("Embassy World")
    assert "เหมาะกับการใช้เวลาพักผ่อนร่วมกันทั้งครอบครัว" not in text
    assert "ตอบสิ่งที่ลูกค้าพูดก่อนเสมอ" in text


def test_sales_host_does_not_require_a_follow_up_question():
    from app.prompts import SALES_HOST_BLOCK

    assert "ค่าเริ่มต้นคือไม่ถามต่อ" in SALES_HOST_BLOCK
    assert "ให้จบคำตอบทันที" in SALES_HOST_BLOCK
    assert "ถามต่อได้เฉพาะเมื่อขาดข้อมูลที่จำเป็น" in SALES_HOST_BLOCK
    for tail in (
        "มีอะไรให้ช่วยเพิ่มเติมไหมคะ",
        "สนใจดู...ไหมคะ",
        "อยากให้เปิด...ไหมคะ",
    ):
        assert tail in SALES_HOST_BLOCK


def test_sales_host_does_not_offer_after_a_complete_answer():
    from app.prompts import SALES_HOST_BLOCK

    assert "ห้ามเติมคำถามหรือข้อเสนอเพื่อกันความเงียบ" in SALES_HOST_BLOCK
    assert "ขาดข้อมูลที่จำเป็นต่อการทำสิ่งที่ลูกค้าขออยู่ตอนนี้" in SALES_HOST_BLOCK


def test_sales_host_stops_when_the_customer_closes_the_conversation():
    from app.prompts import SALES_HOST_BLOCK

    assert "ลูกค้าปิดเรื่อง ปฏิเสธ หรือชะลอการตัดสินใจ" in SALES_HOST_BLOCK
    assert "ให้หยุดทันที" in SALES_HOST_BLOCK
    assert 'ผิด: "ได้ค่ะ เดี๋ยวเปิดผังหรือห้องอื่นให้ดูเพิ่มเติมได้ค่ะ"' in SALES_HOST_BLOCK
    assert 'ถูก: "ได้เลยค่ะ"' in SALES_HOST_BLOCK


def test_sales_host_speaks_unconfirmed_status_out_loud():
    from app.prompts import SALES_HOST_BLOCK

    assert "draft" in SALES_HOST_BLOCK and "expired" in SALES_HOST_BLOCK
    assert "ใช้ตอบข้อเท็จจริงไม่ได้" in SALES_HOST_BLOCK
    from app.knowledge_policy import state_instruction
    assert "กำลังพัฒนา" in state_instruction("developing")


def test_sales_host_offers_only_actions_available_in_the_session():
    from app.prompts import SALES_HOST_BLOCK

    assert "การกระทำต้องเกิดจริงก่อนพูดว่าเกิดแล้ว" in SALES_HOST_BLOCK
    assert "รอผลสำเร็จในเทิร์นนั้น" in SALES_HOST_BLOCK
    assert "ถ้าไม่มีเครื่องมือสำหรับการกระทำนั้น ห้ามเสนอเหมือนว่าทำได้" in SALES_HOST_BLOCK
    for fabricated_action in ("เปิดแล้ว", "กำลังเปิด", "เดี๋ยวเปิด", "พาไป", "พาชม"):
        assert fabricated_action in SALES_HOST_BLOCK


def test_gallery_cannot_claim_physical_tour_without_navigation():
    text = build_instructions("X")

    assert "navigation" in text
    for physical_action in ("พาชม", "พาไปดู", "เดินไป", "นำไป", "ตามฉันมา", "เดี๋ยวพาไป"):
        assert physical_action in text
    assert "ข้อความเชิง sales ในเอกสารไม่ได้แปลว่ามีความสามารถนี้" in text
    assert "ต้องบอกตรงๆ ว่าตอนนี้ไม่สามารถพาเดินไปได้" in text
    assert 'ห้ามหลบคำถามด้วยการตอบเพียงว่าสถานที่นั้น "มีให้ชม"' in text
    assert 'ผิดเมื่อไม่มีผล navigation: "เดี๋ยวเอ็มม่าพาไปชมห้องตัวอย่างนะคะ"' in text
    assert 'ถูก: "มีห้องตัวอย่างให้ชมค่ะ แต่ตอนนี้เอ็มม่ายังไม่สามารถพาเดินไปยังห้องตัวอย่างได้ค่ะ"' in text
    assert "ตอบรับว่าเดี๋ยวจะพาไป" not in text


def test_sales_host_does_not_narrate_every_tool_result():
    from app.prompts import SALES_HOST_BLOCK

    assert "ผลเครื่องมืออาจมีข้อมูลมากกว่าที่ถาม" in SALES_HOST_BLOCK
    assert "ใช้เฉพาะข้อมูลที่จำเป็นต่อคำถามตรงหน้า" in SALES_HOST_BLOCK
    assert "ห้ามนำข้อมูลข้างเคียงมาต่อเติม" in SALES_HOST_BLOCK


def test_sales_host_compares_room_use_before_numbers(sales_context):
    sales_context(approved=False)
    text = build_instructions("Embassy World")
    assert "อธิบายความต่างด้านพื้นที่และการใช้งานก่อน" not in text
    # Owner 12 Oct ("ตอบให้ครบถ้วนพอ"): live, "มีแบบห้องอะไรบ้าง" got "each type has a different
    # size" with the sizes sitting in the approved facts -- name them.
    assert 'ถามแบบห้องหรือความต่าง ให้บอกแบบพร้อมขนาดตาม "แบบห้องและขนาด" ในข้อมูลโครงการ' in text
    assert "โซน/ชั้นของห้องให้ค้นแล้วตอบตามผลจริง" in text
    # live: "0.6 กม." came out as "สองจุดหก กิโลเมตร" -- say metres
    assert "ระยะที่ไม่ถึงหนึ่งกิโลเมตรให้พูดเป็นเมตร ห้ามอ่านทศนิยม" in text


def test_sales_host_has_explicit_price_and_pool_stop_examples():
    from app.prompts import SALES_HOST_BLOCK

    assert 'ลูกค้าถาม "ห้องนี้ราคาเท่าไหร่"' in SALES_HOST_BLOCK
    assert '"ราคาขอให้ทีมขายยืนยันนะคะ ดิฉันไม่อยากให้ข้อมูลที่คลาดเคลื่อน" แล้วหยุด' in SALES_HOST_BLOCK
    assert 'ลูกค้าถาม "สระอยู่ตรงไหน"' not in SALES_HOST_BLOCK
    assert "ผลที่ระบุ draft" in SALES_HOST_BLOCK


def test_the_sales_host_voice_stays_out_of_the_other_profiles():
    """Emma the personal assistant does not sell condos to her owner, and an
    interpreter does not sell anything to anyone."""
    for profile in ("emma", "translator"):
        text = build_instructions("X", profile=profile)
        assert "ขายชีวิตที่ดีขึ้นก่อน" not in text, profile


def test_the_gallery_greeting_leads_with_the_project_not_price():
    """The opening is the welcome line plus one warm, natural question to
    start the conversation (owner 2026-09-14 final: "just the welcome, then
    ask along too — it feels more natural, the rest gets told through the
    conversation"). The project overview is deferred to the conversation,
    and the opening still never leads with a price."""
    assert "ยังไม่พูดเรื่องราคา" in GREETING
    assert "ภาพรวมโครงการ" in GREETING


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
    assert "search_my_documents" not in withlib, "unapproved library must not be suggested"


def test_the_gallery_prompt_drops_the_slide_rules_with_the_slides_group(monkeypatch):
    """The owner took the presentation out on 2026-09-11 ("เอาพรีเซ้นออกก่อน
    ยังไม่ได้ใช้"). Removing `slides` from TOOL_GROUPS unregisters the tools;
    the prompt must stop naming them in the same move — in the session that
    prompted this, the host offered "แนะนำสไลด์" it could not open. The
    gallery default (blank TOOL_GROUPS = every group) keeps the rules."""
    from app.config import settings
    from app.prompts import BASE_INSTRUCTIONS, _SLIDE_RULES

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "")
    plain = build_instructions("X", extra_facts="")
    for name in ("show_slide", "start_presentation", "next_slide", "จอแสดงสไลด์"):
        assert name in plain, "the default gallery prompt keeps its slide rules"
    # The removal is a slice of the real block; if someone rewrites rules
    # 10-13 the slice must still be what gets removed, not a stale copy.
    assert _SLIDE_RULES in BASE_INSTRUCTIONS
    assert "show_slide" in _SLIDE_RULES and "next_slide" in _SLIDE_RULES

    monkeypatch.setattr(settings, "tool_groups", "knowledge,units,smarthome,mydocs")
    bare = build_instructions("X", extra_facts="")
    for name in ("show_slide", "start_presentation", "next_slide", "จอแสดงสไลด์"):
        assert name not in bare, f"{name} named with no slides group loaded"
    assert "ไม่มีสไลด์หรือพรีเซนต์" in bare, "say what there is instead, not just what is missing"
    assert "10. ถามรายละเอียด" in bare and "14. ถามรายละเอียด" not in bare
    from app.prompts import SALES_HOST_BLOCK
    assert "สไลด์" not in SALES_HOST_BLOCK, "the host block must not offer a slide it cannot open"
    assert "search_condo_info" in bare, "the surviving lookup rule still routes unknowns"


def test_answer_images_let_a_picture_request_through_without_the_deck(monkeypatch):
    """Owner 2026-10-11: pictures with answers, not the presentation. With
    ANSWER_IMAGES on, "ถูกขอดูสไลด์ให้บอกว่าวันนี้ยังไม่มี" would turn away "ขอดู
    รูปสระ" — so the rule sends the request to search (which puts the picture
    up) and lets "มีภาพให้ชม" be said only after the result says it is on
    screen. Off, the prompt is the sales room's, byte for byte."""
    from app.config import settings
    from app.prompts import _NO_SLIDES_RULE

    monkeypatch.setattr(settings, "assistant_profile", "condo")
    monkeypatch.setattr(settings, "tool_groups", "knowledge,units,mydocs")
    monkeypatch.setattr(settings, "answer_images", False)
    off = build_instructions("X", extra_facts="")
    assert _NO_SLIDES_RULE.strip() in off
    monkeypatch.setattr(settings, "answer_images", True)
    on = build_instructions("X", extra_facts="")
    assert _NO_SLIDES_RULE.strip() not in on
    assert "ถูกขอดูรูป หรือถาม/พูดถึงสิ่งอำนวยความสะดวกหรือสถานที่เฉพาะอย่าง ให้เรียก search_condo_info" in on
    assert "ด้วยแม้รู้คำตอบแล้ว" in on   # facts answered "มีสระไหม" with no search, no picture
    assert "พูดว่ามีภาพให้ชมบนจอได้เฉพาะเมื่อผลค้นบอกว่าแสดงภาพบนจอแล้ว" in on
    assert "ห้ามพูดว่าเปิดสไลด์หรือพรีเซนต์ให้" in on
    for name in ("show_slide", "start_presentation", "next_slide"):
        assert name not in on
    # Two differences and only two: that rule, and the facility sentence's
    # "เห็น" (a picture can come up) for "รู้จัก".
    assert "ลูกค้าอยากเห็นชั้นไหน" in on and "ลูกค้าอยากรู้จักชั้นไหน" in off
    assert off.replace(_NO_SLIDES_RULE.strip(), "") == on.replace(
        on[on.index("ไม่มีพรีเซนต์ทั้งชุด"):on.index("บนจอแล้ว", on.index("ไม่มีพรีเซนต์ทั้งชุด")) + len("บนจอแล้ว")], ""
    ).replace("ลูกค้าอยากเห็นชั้นไหน", "ลูกค้าอยากรู้จักชั้นไหน")


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


def test_emmajoke_can_talk_about_anything_except_the_project():
    """Owner, 2026-09-24: "EmmaJoke ... ทำได้ทุกอย่างที่ไม่เกี่ยวกับโครงการ
    แล้วก็อย่าไปยุ่งข้อมูลโครงการหรือเอกสาร" (the gold Emma stays the one that
    "ช่วยขาย ค่อยให้ข้อมูลโครงการ").

    The prompt says it; the tool gate below is what makes it true.
    """
    text = build_instructions("Embassy World", profile="joke", robot_name="Emma")

    # None of the sales material comes with it.
    assert "SALES" not in text
    assert "ราคาเริ่มต้น" not in text
    assert "search_condo_info" not in text
    assert "search_my_documents" not in text
    assert "[เอกสาร]" not in text and "[ความจำ]" not in text

    # It knows what to SAY when the question comes — a missing tool cannot
    # answer "ห้องนี้ราคาเท่าไหร่" politely on its own.
    assert "ไม่ได้ดูข้อมูลโครงการ" in text
    assert "ไฟที่หน้าอก" in text            # how the guest switches to Emma

    # The lessons this repo paid for in real bugs survive the profile switch.
    assert "ห้ามเดา" in text
    assert "ทวนยืนยัน" in text
    assert "mock=" in text                   # read the hardware result first
    assert "ห้ามอ่าน markdown" in text


def test_emmajoke_cannot_reach_a_single_project_tool(monkeypatch):
    """The wall is `registry.allowed()`, not the wording above it.

    Measured with the tools loaded from this machine's own TOOL_GROUPS:
    condo and emma see the unit/knowledge tools, EmmaJoke sees only the
    smart-home and robot ones. Per CONNECTION, not per machine — one server
    answers a sales session and an EmmaJoke session at the same time.
    """
    from app import tools as tools_module
    from app.tools import registry

    tools_module.load_tools()

    def names_for(profile):
        token = registry.session_profile.set(profile)
        try:
            return {t.name for t in registry.all_tools()}
        finally:
            registry.session_profile.reset(token)

    joke = names_for("joke")
    condo = names_for("condo")

    assert joke                                   # it is not simply muted
    for tool in ("search_condo_info", "search_my_documents", "show_unit",
                 "find_units", "show_plan", "calc_roi", "list_promotions"):
        assert tool not in joke, tool
    assert "search_condo_info" in condo            # the gate is per profile
    for tool in joke:
        assert registry.get(tool).group in {"smarthome", "robot", "reminders"}, tool


def test_approved_starting_prices_may_be_said_unit_prices_still_go_to_sales():
    """Owner, 2026-10-08: Emma may give the approved starting prices ("พูด
    เป็นช่วงได้ หรือเริ่มต้น"). Before this the block said only "refuse",
    and the live eval measured 0 of 30 price questions answered while
    condo_facts.json and qa_script q04 (both approved) carried the prices.
    A specific unit's price still goes to the sales team, a range never
    invents a top price, and the block itself still carries no numbers."""
    from app.prompts import SALES_HOST_BLOCK

    line = [l for l in SALES_HOST_BLOCK.splitlines() if l.startswith("- ราคา:")][0]
    assert 'ให้ตอบตาม "ราคาเริ่มต้น" ในข้อมูลโครงการ' in line
    assert "ห้ามเดาราคาสูงสุด" in line
    # Re-test: "one-bedroom units start at 3.19 million" — 2.79 + the
    # "generally ~400,000 higher" note, added up into a figure nobody approved.
    assert "ห้ามบวกลบเป็นตัวเลขใหม่" in line
    # Re-test: asked the garden/pool-view price, answered with the type
    # starting prices as if they were the view prices.
    # (owner 12 Oct: never "ไม่มีข้อมูล" -- hand it to the sales team instead)
    assert "ราคาตามวิวหรือชั้นไม่มีในข้อมูล ห้ามเดา ให้ทีมขายยืนยันอย่างเป็นธรรมชาติ" in line
    # The first wording said "starting price questions" only, and the live
    # re-test (2026-10-08) still refused "ราคาเท่าไหร่" / "สองห้องนอนราคา
    # เท่าไหร่" / "How much is a one-bedroom?" — read as the unit example.
    # The questions it must answer are quoted, like the one it must not.
    for asked in ('"ราคาเท่าไหร่"', '"สองห้องนอนราคาเท่าไหร่"', '"How much is a one-bedroom?"'):
        assert asked in line, asked
    assert "ราคาของห้องเจาะจง (มีเลขห้อง)" in line
    assert "ห้ามถามหรือเปิดเรื่องราคาเอง" in line
    assert '"ราคาขอให้ทีมขายยืนยันนะคะ ดิฉันไม่อยากให้ข้อมูลที่คลาดเคลื่อน" แล้วหยุด' in line
    assert not any(ch.isdigit() for ch in line)
