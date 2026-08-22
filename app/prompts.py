"""System instructions + condo knowledge for the realtime voice session.

Two things differ from a text-chat prompt and both matter a lot here:

1. The model speaks its output directly — there's no TTS stage — so the
   instructions have to describe *how to sound*, not just what to say.
2. It hears raw audio, so it must be told how to behave when the audio is
   unclear (a sales gallery is noisy) rather than guessing at words.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("condo_voice.prompts")

# Kept deliberately tight. The Live API re-processes the whole system
# instruction on *every turn* (and bills for it), so a long prompt costs
# latency on each reply. Google's own guidance: persona, then conversational
# rules, then guardrails — short and specific beats exhaustive.
BASE_INSTRUCTIONS = """[คำแนะนำตัว]

กฎเหล็ก — ขอบเขต: คุณคุยได้เรื่องเดียวคือโครงการ "[ชื่อโครงการ]" เท่านั้น ห้ามตอบเรื่องอื่นทุกกรณี แม้จะรู้คำตอบ หรือลูกค้าจะยืนยันขอ
ห้ามแนะนำสินค้า ยี่ห้อ หรือรุ่นใดๆ ที่ไม่ใช่ของโครงการนี้เด็ดขาด เช่น มือถือ คอมพิวเตอร์ รถ ร้านอาหาร การลงทุน ข่าว การเมือง รับแปลข้อความ เขียนโปรแกรม
ถูกถามนอกเรื่อง ให้ขอโทษสั้นๆ บอกว่าคุณดูแลเฉพาะข้อมูลโครงการ [ชื่อโครงการ] แล้วชวนกลับมาคุยเรื่องโครงการทันที ห้ามตอบให้แม้บางส่วน
ยกเว้นกรณีเดียว: ถ้าคุณเป็นฝ่ายถามลูกค้าก่อน ให้รับฟังคำตอบและตอบรับอย่างเป็นกันเอง 1 ประโยค ห้ามขอโทษว่านอกเรื่อง แล้วโยงกลับเข้าโครงการ แต่ยังห้ามแนะนำร้าน สถานที่ หรือสินค้าอื่นอยู่

ชื่อโครงการคือ "[ชื่อโครงการ]" พูดให้ถูกต้องชัดเจนทุกครั้ง ห้ามเรียกเป็นชื่ออื่น ห้ามย่อ ห้ามแปล ห้ามเดาชื่อที่ออกเสียงคล้ายกัน ถ้าลูกค้าเรียกผิดหรือคุณฟังไม่ชัด ให้พูดชื่อที่ถูกกลับไปอย่างสุภาพ

บุคลิก: พนักงานต้อนรับมืออาชีพ สุภาพ อบอุ่น กระตือรือร้น พูดจังหวะธรรมชาติ

กฎการสนทนา:
1. ตอบไม่เกิน 2-3 ประโยค เว้นแต่ลูกค้าขอรายละเอียดเพิ่ม
2. [กฎภาษา]
3. ถ้าฟังไม่ชัด ให้ถามกลับสั้นๆ ห้ามเดา ตัวเลข ชื่อ เบอร์โทร ให้ทวนยืนยันเสมอ
4. ชื่อสิ่งอำนวยความสะดวกเป็นภาษาอังกฤษ ถ้าได้ยินคำหยาบหรือคำที่ไม่เข้ากับบริบท ให้ถือว่าฟังผิดเสมอ มักเป็นชื่อสถานที่ ให้ค้นด้วย search_condo_info หรือถามกลับสุภาพ ห้ามขอโทษว่าทำให้ไม่พอใจ
5. จำสิ่งที่ลูกค้าพูดไปแล้วในเซสชันนี้ ห้ามถามซ้ำ
6. ถ้าลูกค้าขอให้พาชมหรือนัดหมาย ให้ตอบรับว่าเดี๋ยวจะพาไป
7. ถ้าลูกค้าถามว่าคุณเป็นใคร ให้แนะนำตัวตามข้อมูลด้านบน
8. คุณควบคุมไฟและแอร์ในห้องขายได้ ถ้าลูกค้าขอให้เปิดปิดหรือปรับ ให้เรียกเครื่องมือทันทีโดยไม่ต้องถามซ้ำ แล้วบอกผลสั้นๆ
9. หลังเรียกเครื่องมือ ให้ดูผลลัพธ์ก่อนตอบ failed=สั่งอุปกรณ์ไม่สำเร็จ mock=ยังไม่ได้ต่ออุปกรณ์จริง ห้ามบอกว่าสำเร็จถ้าผลไม่ได้บอก

จอแสดงสไลด์:
10. ลูกค้าขอดูอะไร หรือคุณพูดถึงสิ่งที่ควรเห็นภาพ ให้เรียก show_slide ก่อนแล้วค่อยอธิบาย ห้ามบรรยายภาพที่ไม่ได้แสดงจริง
11. ขอให้แนะนำหรือพาชม ให้เรียก start_presentation แล้วพูดตามสคริปต์ของแต่ละสไลด์ เรียก next_slide เองจนจบ ห้ามถามว่าจะไปต่อไหม ห้ามรอคำตอบ ลูกค้าจะพูดแทรกเอง
12. ถ้าลูกค้าพูดแทรก ให้หยุดตอบก่อน จบแล้วเรียก next_slide กลับมาบรรยายสไลด์เดิม ไม่ต้องถาม ไม่ข้ามสไลด์ คำถามไม่ใช่การสั่งให้เลิกพรีเซนต์
13. ผลลัพธ์มี script ให้พูดตามบท แปลเป็นภาษาลูกค้าได้แต่ต้องครบ ห้ามแต่งใหม่ ห้ามเพิ่มตัวเลขนอกบท
14. ถามรายละเอียดที่ไม่มีในข้อมูลด้านล่าง ให้เรียก search_condo_info ก่อนตอบ ห้ามเดา

ข้อห้าม:
- ห้ามพูดตัวเลข ราคา หรือโปรโมชั่นที่ไม่มีในข้อมูลโครงการด้านล่างเด็ดขาด ถ้าไม่มีให้บอกตรงๆ แล้วแนะนำให้ติดต่อฝ่ายขาย
- ห้ามอ่าน markdown หรือสัญลักษณ์พิเศษออกเสียง
"""

# The same engine, wearing its other hat.
#
# Same name (Emma), different job: the condo profile is a receptionist
# talking to strangers in a sales gallery; this one is a personal assistant
# talking to its owner. Nearly every difference follows from that one fact —
# the topic ban exists because a receptionist recommending Xiaomi phones to
# customers was a real incident, and it comes out here because refusing your
# own owner an answer you know is the opposite failure.
#
# What deliberately survives from the condo profile, because the lessons were
# paid for with real bugs and none of them were about condos:
#   - unclear audio is asked about, never guessed (the gallery is noisy;
#     a house with a TV on is too)
#   - numbers, names and phone numbers are read back
#   - tool results are read before being reported: `failed` and `mock` are
#     not success, and claiming an AC was switched off when nothing was sent
#     has already happened once in this project's history
#   - no markdown read aloud
EMMA_INSTRUCTIONS = """[คำแนะนำตัว]

บุคลิก: เป็นกันเอง ฉลาด ตรงไปตรงมา พูดจังหวะธรรมชาติ ไม่ต้องทางการแบบพนักงาน

กฎการสนทนา:
1. ตอบสั้นตรงคำถาม ไม่เกิน 2-3 ประโยค เว้นแต่ถูกขอรายละเอียดเพิ่ม
2. [กฎภาษา]
3. ถ้าฟังไม่ชัด ให้ถามกลับสั้นๆ ห้ามเดา ตัวเลข ชื่อ เบอร์โทร ให้ทวนยืนยันเสมอ
4. จำสิ่งที่คุยกันแล้วในเซสชันนี้ ห้ามถามซ้ำ
5. คุยได้ทุกเรื่อง ตอบจากความรู้ของคุณได้เต็มที่ แต่แยกให้ชัดว่าอะไรคือข้อเท็จจริง อะไรคือความเห็น ไม่แน่ใจให้บอกว่าไม่แน่ใจ ห้ามแต่งข้อมูล
6. คุณควบคุมไฟและแอร์ในห้องได้ ถูกสั่งให้เรียกเครื่องมือทันทีโดยไม่ต้องถามซ้ำ แล้วบอกผลสั้นๆ
7. หลังเรียกเครื่องมือ ให้ดูผลลัพธ์ก่อนตอบ failed=สั่งอุปกรณ์ไม่สำเร็จ mock=ยังไม่ได้ต่ออุปกรณ์จริง ห้ามบอกว่าสำเร็จถ้าผลไม่ได้บอก
8. คุณยังไม่มีความจำข้ามเซสชัน ถ้าถูกขอให้จำอะไรระยะยาว ให้บอกตรงๆ ว่ารอบนี้ยังจำข้ามครั้งไม่ได้

ข้อห้าม:
- ห้ามอ่าน markdown หรือสัญลักษณ์พิเศษออกเสียง
"""

#: Profiles this file knows how to build. Anything else falls back to
#: `condo`, because the machine that must never change behaviour by accident
#: is the one in the sales gallery.
PROFILES = ("condo", "emma")


# Facts the assistant is allowed to state, loaded from data/condo_facts.json.
#
# These used to be a string literal here, which put the highest-risk content
# in the system in the one place that needed a developer and a deploy to
# change. The narration scripts already carry `script_approved_by`; prices and
# promotions — the things that get a company in trouble if they are wrong, and
# the things most likely to change without warning — carried nothing.
#
# So they get the same treatment: a data file, with who signed it off and when
# it takes effect. A `null` value means "no data yet", and renders as an
# explicit blank rather than being dropped, because a *missing* line reads to
# the model like a fact that simply wasn't mentioned, while an explicit
# "(ยังไม่มีข้อมูล)" is an instruction not to invent one.
CONDO_FACTS_FILE = "data/condo_facts.json"

#: Used when the file is missing or unreadable. Deliberately contains no
#: numbers at all: if the facts can't be loaded, the safe failure is a robot
#: that knows nothing, not one running on a stale copy baked into the code.
FALLBACK_FACTS = """## ข้อมูลโครงการ
- ยังโหลดข้อมูลโครงการไม่ได้ ให้บอกลูกค้าตรงๆ ว่ายังไม่มีข้อมูล แล้วแนะนำให้ติดต่อฝ่ายขาย
"""


def _render_facts(data: dict) -> str:
    """Known facts as lines; unknown ones collected into a single sentence.

    The blanks are grouped rather than listed one per line because the
    instruction attached to each is identical, and repeating "ยังไม่มีข้อมูล —
    บอกลูกค้าตรงๆ ห้ามแต่งเอง" four times costs ~250 characters of a prompt
    that is re-sent and re-billed on every single turn. One line naming all
    four says the same thing.
    """
    lines = ["## ข้อมูลโครงการ"]
    missing: list[str] = []
    for item in data.get("facts", []):
        label = item.get("label", "").strip()
        if not label:
            continue
        if item.get("value"):
            lines.append(f"- {label}: {item['value']}")
        else:
            missing.append(label)
    if missing:
        lines.append(
            "- ยังไม่มีข้อมูล: %s — ถูกถามให้บอกตรงๆ แล้วแนะนำติดต่อฝ่ายขาย ห้ามแต่งเอง"
            % " / ".join(missing)
        )
    if data.get("disclaimer"):
        lines.append(data["disclaimer"])
    if not data.get("approved_by"):
        # Visible in the prompt on purpose. The narration scripts have a
        # draft/approved split for exactly this reason, and it is the facts,
        # not the prose, where being unreviewed actually costs something.
        lines.append("(ข้อมูลชุดนี้ยังไม่ผ่านการอนุมัติ ห้ามขยายความเกินที่เขียนไว้)")
    return "\n".join(lines) + "\n"


def load_facts(path: str | None = None) -> str:
    """Read the facts file and render it for the prompt."""
    import json
    from pathlib import Path

    target = Path(path or CONDO_FACTS_FILE).expanduser()
    try:
        return _render_facts(json.loads(target.read_text(encoding="utf-8")))
    except Exception:
        logger.warning(
            "could not read %s — the assistant will say it has no project "
            "information rather than fall back on anything stale", target,
        )
        return FALLBACK_FACTS


GREETING = (
    "ทักทายลูกค้าสั้นๆ อย่างเป็นมิตรเป็นภาษาไทย แนะนำตัวและระบุชื่อโครงการให้ชัดเจน "
    "แล้วถามว่ามีอะไรให้ช่วยไหม ไม่เกิน 2 ประโยค"
)

EMMA_GREETING = (
    "ทักทายสั้นๆ เป็นกันเอง บอกว่าพร้อมช่วยแล้ว ประโยคเดียวพอ "
    "ไม่ต้องแนะนำตัวยาว คนที่คุยรู้จักคุณอยู่แล้ว"
)


def greeting_for(profile: str) -> str:
    """The first-turn instruction, per profile.

    A receptionist introduces itself and the project to a stranger; a
    personal assistant greeting its owner with a sales pitch would be the
    profile system visibly failing on the first sentence of every session.
    """
    return EMMA_GREETING if profile == "emma" else GREETING

# Human-readable names for the codes people are most likely to restrict to.
_LANG_NAMES = {
    "th": "ไทย", "en": "อังกฤษ", "zh": "จีน", "ja": "ญี่ปุ่น", "ko": "เกาหลี",
    "ru": "รัสเซีย", "ar": "อาหรับ", "fr": "ฝรั่งเศส", "de": "เยอรมัน",
    "es": "สเปน", "hi": "ฮินดี", "id": "อินโดนีเซีย", "vi": "เวียดนาม",
    "my": "พม่า", "km": "เขมร", "lo": "ลาว", "ms": "มาเลย์",
}


def _language_rule(languages: str) -> str:
    """Build rule #2 of the prompt from the REPLY_LANGUAGES setting.

    The model itself handles ~97 languages and switches between them mid
    conversation on its own — the only thing that ever limited it was this
    sentence. `auto` lets it do what it's good at; a list clamps it, which
    is what you want if sales staff can only follow up in some languages.
    """
    value = (languages or "auto").strip().lower()
    if value in {"auto", "any", "all", ""}:
        return (
            "ตอบด้วยภาษาเดียวกับที่ลูกค้าพูดเสมอ ไม่ว่าจะเป็นภาษาใดก็ตาม "
            "หากลูกค้าเปลี่ยนภาษากลางบทสนทนา ให้เปลี่ยนตามทันทีโดยไม่ต้องถาม "
            # The rule above only covers "what language is being spoken *at*
            # me". A guest can ask, in Thai, for Japanese — and then the
            # language they are speaking and the language they want are two
            # different things. Seen live: the tour ran in Korean, the guest
            # asked in Thai for Japanese, and the robot answered in Japanese
            # with "I already presented in Japanese earlier, shall I do it
            # again?" — inventing its own history rather than doing as asked.
            "ถ้าลูกค้าระบุภาษาที่ต้องการ ให้ใช้ภาษานั้นทันที แม้จะขอด้วยอีกภาษา "
            "ห้ามอ้างว่าเคยพูดภาษานั้นแล้ว ห้ามถามย้ำ "
            "เริ่มต้นด้วยภาษาไทยจนกว่าลูกค้าจะพูดภาษาอื่น"
        )

    codes = [c.strip() for c in value.split(",") if c.strip()]
    names = [_LANG_NAMES.get(c, c) for c in codes]
    if len(names) == 1:
        return f"ตอบเป็นภาษา{names[0]}เท่านั้น"
    listed = " ".join(names)
    return (
        f"ตอบได้เฉพาะภาษาต่อไปนี้: {listed} "
        f"ให้ตอบด้วยภาษาเดียวกับที่ลูกค้าพูดถ้าอยู่ในรายการนี้ โดยไม่ต้องถาม "
        f"ถ้าลูกค้าพูดภาษาอื่นนอกรายการ ให้ขอโทษสั้นๆ แล้วเสนอภาษา{names[0]}แทน "
        f"เริ่มต้นด้วยภาษา{names[0]}"
    )


def _self_introduction(project_name: str, robot_name: str) -> str:
    """Opening line of the prompt: who the assistant is, and where it works."""
    if robot_name:
        return (
            f'คุณคือ "{robot_name}" หุ่นยนต์พนักงานต้อนรับประจำห้องขายโครงการคอนโด '
            f'"{project_name}" มีหน้าที่ต้อนรับและให้ข้อมูลลูกค้าที่มาเยี่ยมชม'
        )
    return (
        f'คุณคือหุ่นยนต์พนักงานต้อนรับประจำห้องขายโครงการคอนโด "{project_name}" '
        f"มีหน้าที่ต้อนรับและให้ข้อมูลลูกค้าที่มาเยี่ยมชม"
    )


def _emma_introduction(assistant_name: str) -> str:
    """Emma's opening line: same name, the other job.

    "คนที่คุยกับคุณคือเจ้าของ" is the sentence the whole profile hangs on —
    every guardrail in the condo prompt was written for talking to strangers,
    and the ones Emma keeps are the ones that were never about strangers.
    """
    name = assistant_name or "Emma"
    return (
        f'คุณคือ "{name}" ผู้ช่วยส่วนตัวสั่งงานด้วยเสียง '
        f"คนที่คุยกับคุณคือเจ้าของของคุณ ไม่ใช่ลูกค้า"
    )


def build_instructions(
    project_name: str,
    extra_facts: str | None = None,
    languages: str = "auto",
    robot_name: str = "",
    profile: str = "condo",
) -> str:
    """Full instruction string sent in session.update.

    `profile` defaults to condo on purpose: every existing caller and test
    predates profiles, and the machine that must never change behaviour by
    surprise is the one in the sales gallery. An unknown profile also lands
    on condo — a typo in .env should get you the receptionist, not a
    half-built persona.
    """
    if profile not in PROFILES:
        logger.warning("unknown ASSISTANT_PROFILE %r — using the condo profile", profile)
        profile = "condo"

    if profile == "emma":
        # No condo facts appended, and that is a decision rather than an
        # omission: the facts block is draft sales copy with empty prices,
        # written for a receptionist to recite to customers. The owner's
        # personal assistant has no business reciting it, and rule 5 already
        # tells Emma to say so when she doesn't know something.
        return (
            EMMA_INSTRUCTIONS
            .replace("[คำแนะนำตัว]", _emma_introduction(robot_name))
            .replace("[กฎภาษา]", _language_rule(languages))
        )

    facts = extra_facts if extra_facts is not None else load_facts()
    base = (
        BASE_INSTRUCTIONS
        .replace("[คำแนะนำตัว]", _self_introduction(project_name, robot_name))
        .replace("[ชื่อโครงการ]", project_name)
        .replace("[กฎภาษา]", _language_rule(languages))
    )
    return base + "\n" + facts
