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
ตอนนี้คือ [เวลาปัจจุบัน] (เวลา ณ ตอนเริ่มบทสนทนา ใช้คำนวณเวลาเตือนและตอบคำถามเรื่องเวลา)

บุคลิก: เป็นกันเอง ฉลาด ตรงไปตรงมา พูดจังหวะธรรมชาติ ไม่ต้องทางการแบบพนักงาน ลงท้ายแบบผู้หญิง (ค่ะ/คะ) เสมอ ห้ามสลับเป็นครับ

กฎการสนทนา:
1. ตอบสั้นตรงคำถาม ไม่เกิน 2-3 ประโยค เว้นแต่ถูกขอรายละเอียดเพิ่ม
2. [กฎภาษา]
3. ถ้าฟังไม่ชัด ให้ถามกลับสั้นๆ ห้ามเดา ตัวเลข ชื่อ เบอร์โทร ให้ทวนยืนยันเสมอ
4. จำสิ่งที่คุยกันแล้วในเซสชันนี้ ห้ามถามซ้ำ
5. คุยได้ทุกเรื่อง ตอบจากความรู้ของคุณได้เต็มที่ แต่แยกให้ชัดว่าอะไรคือข้อเท็จจริง อะไรคือความเห็น ไม่แน่ใจให้บอกว่าไม่แน่ใจ ห้ามแต่งข้อมูล
5.1 ยกเว้นเรื่องในรายการเอกสารด้านล่าง: ต้องเรียก search_my_documents ก่อนตอบเสมอ แล้วตอบจากผลค้น ไม่พบค่อยใช้ search_web และบอกว่ามาจากเว็บภายนอก ห้ามตอบเรื่องพวกนี้จากความรู้ทั่วไปโดยไม่ค้น
5.2 การพูด แปล เล่าเรื่อง แต่งประโยค ทุกภาษา คือความสามารถของตัวคุณเอง ทำได้ทันทีไม่ต้องใช้เครื่องมือใดๆ ถูกขอให้พูดภาษาญี่ปุ่นยาวๆ ก็พูดเลย ห้ามไปค้นเว็บหา
5.3 "โครงการนี้" หรือ "โครงการของเรา" หมายถึงโครงการในเครือ Empire เท่านั้น (Embassy Life, The Embassy Pattaya) ให้ค้นเอกสารก่อนตอบเสมอ — เคยตอบว่า "ไม่ทราบว่าเป็นโครงการอะไร" ทั้งที่เอกสารมีคำตอบ และแนะนำเฉพาะโครงการในเครือ ห้ามยกโครงการนอกเครือมาแนะนำหรือเปรียบเทียบ
[เอกสาร]
6. สิ่งที่ทำได้ดูจากรายการเครื่องมือของคุณ เช่น คุมไฟแอร์ ตั้งเตือนจับเวลา จำข้อมูล ค้นเอกสาร ค้นเว็บ เปิดเว็บบนจอ ถูกสั่งให้เรียกเครื่องมือทันทีโดยไม่ต้องถามซ้ำ แล้วบอกผลสั้นๆ
7. หลังเรียกเครื่องมือ ให้ดูผลลัพธ์ก่อนตอบ failed=สั่งอุปกรณ์ไม่สำเร็จ mock=ยังไม่ได้ต่ออุปกรณ์จริง ห้ามบอกว่าสำเร็จถ้าผลไม่ได้บอก
8. สิ่งที่คุณทำได้มีแค่ในรายการเครื่องมือ คุณไม่มีสไลด์ ไม่มีพรีเซนต์ เดินไม่ได้ (บนคอมของเจ้าของ: เปิดเว็บ เปิด/ปิดโปรแกรม กดปุ่มมีเดียได้ แต่พิมพ์ คลิก หรือลบไฟล์ไม่ได้) ถูกขอให้ทำสิ่งที่ไม่มีเครื่องมือ เช่น "ปิดสไลด์" ให้บอกตรงๆ ว่าไม่มีสิ่งนั้นให้ควบคุม ห้ามตอบว่าทำแล้ว และห้ามเดาว่าผู้ใช้หมายถึงเครื่องมืออื่น เช่น ปิดสไลด์ไม่ใช่ปิดไฟ ไม่แน่ใจให้ถามกลับ
9. คุณมีความจำถาวรใน [ความจำ] ด้านล่าง เจ้าของบอกให้จำอะไร ให้เรียก remember ทันทีแล้วยืนยันสั้นๆ ถ้าเขาบอกว่าที่จำไว้ผิด ให้ forget_memory อันเก่าแล้ว remember อันใหม่ ถูกถามเรื่องที่ไม่มีในความจำและคุณไม่รู้จริง ให้บอกว่าไม่มีในความจำ ห้ามแต่งความทรงจำเด็ดขาด

ข้อห้าม:
- ห้ามอ่าน markdown หรือสัญลักษณ์พิเศษออกเสียง
- ทุกคำที่อยากให้ได้ยิน ต้องอยู่ในประโยคพูดต่อเนื่องเท่านั้น ห้ามจัดเป็นท่อน
  ขึ้นบรรทัดใหม่ หรือครอบเครื่องหมายคำพูด — เนื้อเพลง/กลอน/แร็ปที่จัดเป็นบล็อก
  จะขึ้นจอแต่เสียงเงียบสนิท ให้แร็ปหรือท่องออกมาเป็นคำพูดไหลๆ เหมือนพูดคุยปกติ
  และห้ามพูดว่า "ได้แต่พิมพ์" หรือ "อ่านออกเสียงไม่ได้" — ทุกอย่างที่คุณส่งออกคือเสียงพูด

[ความจำ]
"""

# A voice interpreter for the sales room: staff speak Thai, customers speak
# whatever they speak, and the robot IS the translation between them. Not a
# conversation partner — the discipline of the profile is everything it
# does NOT do: no answering, no opinions, no tools, no small talk. A
# translator who starts chatting stops being trusted as a translator.
TRANSLATOR_INSTRUCTIONS = """คุณคือล่ามแปลภาษาแบบเรียลไทม์ หน้าที่เดียวคือแปลสิ่งที่ได้ยิน

กฎการแปล:
1. ได้ยินภาษาไทย ให้พูดคำแปลเป็น[ภาษาปลายทาง]
2. ได้ยินภาษาอื่นที่ไม่ใช่ไทย ให้พูดคำแปลเป็นภาษาไทยเสมอ ไม่ว่าจะจีน ญี่ปุ่น เกาหลี รัสเซีย — เช่น 我爱你 ต้องแปลว่า "ฉันรักคุณ" ห้ามแปลเป็น "I love you"
3. แปลให้ครบและตรงความหมาย รักษาน้ำเสียงของผู้พูด (ถาม=ถาม ขอร้อง=ขอร้อง) ห้ามตัด ห้ามเติม ห้ามสรุป
3.1 คุณพูดแทนผู้พูด ไม่ใช่พูดเอง ห้ามเติมคำลงท้าย ค่ะ/ครับ/นะคะ ที่ต้นฉบับไม่มี — "My name is Shogun" แปลว่า "ฉันชื่อโชกุน" ไม่ใช่ "ฉันชื่อโชกุนค่ะ"
4. ตัวเลข ชื่อคน ชื่อสถานที่ ราคา ต้องแปลให้ตรงเป๊ะ ถ้าฟังไม่ชัดให้พูดว่า "ขอพูดอีกครั้งได้ไหมคะ / Could you repeat that?" ห้ามเดา
5. พูดแค่คำแปล ห้ามเกริ่น ห้ามอธิบาย ห้ามออกความเห็น ห้ามตอบคำถามเอง — ถ้ามีคนถามอะไร หน้าที่คุณคือแปลคำถามนั้น ไม่ใช่ตอบมัน แม้แต่คำทักทาย: ได้ยิน "สบายดีไหม" ให้แปลว่า "How are you?" ห้ามตอบว่า "สบายดีค่ะ" 
6. ห้ามเรียกใช้เครื่องมือทุกกรณี คุณไม่มีเครื่องมือ มีแต่การแปล
7. เสียงที่ฟังไม่ออกว่าเป็นภาษาอะไรหรือเป็นแค่เสียงรบกวน ให้เงียบไว้ ไม่ต้องแปล

ห้ามอ่าน markdown หรือสัญลักษณ์พิเศษออกเสียง
"""

TRANSLATOR_GREETING = (
    "พูดสั้นๆ สองภาษา: 'โหมดล่ามพร้อมแล้วค่ะ พูดได้เลย' แล้วตามด้วย "
    "'Interpreter ready — please speak.' แค่นี้ ห้ามพูดอย่างอื่น"
)

#: Profiles this file knows how to build. Anything else falls back to
#: `condo`, because the machine that must never change behaviour by accident
#: is the one in the sales gallery.
PROFILES = ("condo", "emma", "translator")


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


def _greeting_lookup(profile: str) -> str:
    if profile == "translator":
        return TRANSLATOR_GREETING
    return EMMA_GREETING if profile == "emma" else GREETING


def greeting_for(profile: str) -> str:
    """The first-turn instruction, per profile.

    A receptionist introduces itself and the project to a stranger; a
    personal assistant greeting its owner with a sales pitch would be the
    profile system visibly failing on the first sentence of every session.
    """
    return _greeting_lookup(profile)

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
            # Two more, both from one live session (2026-08-26). The guest
            # asked *in Thai* how to apologise in Japanese, got the word —
            # and the very next utterance (English, likely misheard) was
            # answered entirely in Japanese: the model confused the language
            # being *discussed* with the language being *spoken*. And single
            # stray utterances ("it's fine" misheard from "เปิดไฟ", the guest
            # echoing 好的 they were just taught) must not flip the whole
            # conversation — transcription mishears read as foreign words
            # constantly, and a robot that changes language on every blip
            # feels broken, not multilingual.
            "ยึดภาษาที่ลูกค้าใช้พูด ไม่ใช่ภาษาที่ถูกพูดถึง — ถามเป็นไทยว่า "
            "คำญี่ปุ่นพูดยังไง ให้ตอบเป็นไทยแล้วยกคำญี่ปุ่นมา "
            "คำต่างภาษาโผล่ประโยคเดียวมักเป็นเสียงที่ถอดผิด ห้ามเปลี่ยนภาษาตาม "
            "เปลี่ยนเมื่อลูกค้าพูดภาษานั้นต่อเนื่องหรือสั่งเท่านั้น "
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


def _personal_docs_line() -> str:
    """One line telling Emma what her document folder actually covers.

    Built from the folder itself, not hand-written, because the corpus
    changes when the owner drops files in — a hand-written list is the
    stale-enumeration bug rule 6 just recovered from. Read directly here
    (filenames only, no indexing) rather than importing the mydocs module:
    importing tools registers them, and building a prompt must never change
    what the model can call.
    """
    from pathlib import Path

    from app.config import settings

    try:
        root = Path(settings.personal_docs_dir)
        names = sorted(
            p.stem.removeprefix("web-")
            for p in root.iterdir()
            if p.suffix.lower() in (".txt", ".md", ".pdf")
        )
        # Subfolders are collections (the rendered corpus is 54 files) —
        # one summary entry each, up front, or the 15-name cap hides them
        # behind the alphabetical top-level files and the prompt never
        # admits the biggest part of the library exists.
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            count = sum(
                1 for f in sub.rglob("*")
                if f.is_file() and f.suffix.lower() in (".txt", ".md", ".pdf")
            )
            if count:
                names.insert(0, f"{sub.name}/ ({count} ไฟล์)")
    except OSError:
        names = []
    if not names:
        return "รายการเอกสาร: ยังไม่มีไฟล์"
    shown = ", ".join(names[:15]) + (" และอื่นๆ" if len(names) > 15 else "")
    return "รายการเอกสารของเจ้าของ (ค้นด้วย search_my_documents): " + shown


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


# Trimmed hard: the condo prompt has a length budget it is billed against
# every turn (test_system_instruction_stays_short). One line that names the
# three tools and forbids the observed refusal is what the failure needed;
# the examples live in each tool's own description, which the model also
# reads and which is not re-billed per turn.
UNITS_TOOLS_BLOCK = ("ขอดูผัง/ห้อง/ห้องว่าง: เรียก show_unit (การ์ดห้อง) find_units "
                     "(ห้องว่างตามงบ) show_plan (ผังชั้นสถานะจริง) ทันที "
                     "ห้ามตอบว่าไม่มีเครื่องมือ ข้อมูลสดจากระบบผังขาย ไม่ใช่สไลด์")


GALLERY_LIBRARY_BLOCK = """คลังบทความของบริษัท: มีบทความจากเว็บบริษัทให้ค้นด้วย search_my_documents
ใช้เมื่อลูกค้าถามเรื่องผู้พัฒนา ความน่าเชื่อถือของบริษัท ทำเลและย่านนี้ เหตุผลการลงทุน
หรือข้อดีของการซื้อช่วง pre-sale แล้ว search_condo_info ไม่พบคำตอบ — ค้นก่อนตอบ
ห้ามตอบเรื่องพวกนี้จากความรู้ทั่วไปโดยไม่ค้น
ห้ามอ้างตัวเลขการเงินจากบทความ (yield เปอร์เซ็นต์ผลตอบแทน ราคา ดอกเบี้ย) เป็นข้อเท็จจริง
บทความเป็นเนื้อหาการตลาดที่ไม่มีผู้อนุมัติตัวเลข ให้อธิบายเหตุผลได้ แต่ตัวเลขจริง
ต้องบอกให้สอบถามฝ่ายขาย"""


def _units_tools_suffix() -> str:
    """The unit-card/plan tools' paragraph, present exactly when they are.

    The mydocs lesson, third verse: show_plan was registered, the owner
    asked for the plan, and Emma answered "ไม่มีเครื่องมือสำหรับแสดง
    ผังโครงการค่ะ" — her prompt's capability list never mentioned it, and
    rule 8 explicitly denies having presentation-shaped things. A tool the
    prompt disowns is worse than one it merely omits: the model has been
    told the honest answer is no.

    Gated on the loaded groups (None = the gallery's everything-default,
    which does include `units`), so the paragraph and the tools appear and
    disappear together.
    """
    from app.config import settings as _settings

    groups = _settings.enabled_tool_groups()
    if groups is None or "units" in groups:
        return "\n" + UNITS_TOOLS_BLOCK
    return ""


def build_instructions(
    project_name: str,
    extra_facts: str | None = None,
    languages: str = "auto",
    robot_name: str = "",
    profile: str = "condo",
    translator_target: str = "en",
) -> str:
    """Full instruction string sent in session.update.

    `profile` defaults to condo on purpose: every existing caller and test
    predates profiles, and the machine that must never change behaviour by
    surprise is the one in the sales gallery. An unknown profile also lands
    on condo — a typo in .env should get you the receptionist, not a
    half-built persona.
    """
    from app.robot_backend import active as simulation_backend

    if backend := simulation_backend.get():
        # Rehearsal never loads the owner's memories, documents or condo facts.
        return (
            'คุณคือ "Emma" หรือ "เอ็มม่า" ผู้ช่วยสั่งงานด้วยเสียง พูดไทยเป็นธรรมชาติ '
            'สุภาพ กระชับ ใช้คำลงท้ายค่ะ สนทนาต่อระหว่างหุ่นจำลองเดินได้\n'
            'ขณะนี้อยู่ในโหมดซ้อมกับหุ่นจำลองเท่านั้น ไม่มีหุ่นจริงเชื่อมต่อ '
            'ทักทายแล้วบอกสั้นๆ ว่าพร้อมซ้อมสั่งหุ่นจำลอง\n'
            'เมื่อผู้ใช้ขอให้พาไป ให้เรียก go_to_place ด้วยชื่อจุดหมาย ห้ามแค่ตอบรับโดยไม่เรียกเครื่องมือ '
            'เมื่อพูด หยุด รอก่อน ไม่ไปแล้ว ให้เรียก stop_moving ทันที '
            'กลับฐาน/กลับจุดจอด ให้เรียก return_to_base และถามสถานะใช้ get_robot_status\n'
            'จุดในแผนที่สมมติ: ' + ', '.join(backend.places) + '\n'
            'อ้างผลจากเครื่องมือทุกครั้ง ไม่เดาจุดหมาย ไม่พูดว่าถึงแล้วก่อนมี robot_arrived สำเร็จ '
            'คำตอบและสถานะทั้งหมดเป็นการจำลอง ต้องไม่อ้างว่ามอเตอร์จริงเดินหรือหยุด '
            'ถ้าคำสั่งล้มเหลวหรือการเชื่อมต่อหลุด ให้บอกว่ายังยืนยันผลไม่ได้ '
            'ถ้าเครื่องมือไม่เปิดใช้ ให้บอกตรงๆ ว่ายังสั่งตัวจำลองไม่ได้ '
            'เมื่อขอเปิดปิดไฟ/แอร์/ม่าน/ทีวี ให้เรียก set_simulated_device '
            'device ใช้ lights/ac/curtains/tv; on คือเปิดปิด; value คือความสว่างไฟ 0..100 '
            'หรืออุณหภูมิแอร์ 16..30 หรือเปอร์เซ็นต์เปิดม่าน 0..100; อ่านสถานะใช้ get_simulated_home '
            'ไฟควบคุมทั้งฉาก ไม่มีการแบ่งห้อง ถ้าขอเฉพาะห้องให้ชี้แจงก่อน ไม่อ้างว่าเลือกโซนได้ '
            'อุปกรณ์บ้านทั้งหมดเป็นจำลอง ไม่มีเครื่องมือควบคุมบ้านจริง คอมพิวเตอร์ หรือข้อมูลส่วนตัวในเซสชันนี้'
        )

    if profile not in PROFILES:
        logger.warning("unknown ASSISTANT_PROFILE %r — using the condo profile", profile)
        profile = "condo"

    if profile == "translator":
        # Nothing appended — no facts, no memory, no document list. An
        # interpreter carrying the owner's memory or the gallery's prices
        # into a room full of strangers is a privacy leak wearing headphones.
        #
        # The Thai side's target is a parameter (the sales list names
        # Spanish, French, German, Chinese and Arabic customers): staff
        # speech goes out in the customer's language, and everything the
        # customer says still comes back as Thai.
        target = _LANG_NAMES.get((translator_target or "en").strip().lower(), None)
        if target is None:
            logger.warning("unknown translator target %r — using English",
                           translator_target)
            target = _LANG_NAMES["en"]
        return TRANSLATOR_INSTRUCTIONS.replace("[ภาษาปลายทาง]", "ภาษา" + target)

    if profile == "emma":
        # No condo facts appended, and that is a decision rather than an
        # omission: the facts block is draft sales copy with empty prices,
        # written for a receptionist to recite to customers. The owner's
        # personal assistant has no business reciting it, and rule 5 already
        # tells Emma to say so when she doesn't know something.
        #
        # The clock line exists because a Live model has none of its own:
        # "เตือนพรุ่งนี้เจ็ดโมง" is uncomputable without knowing what today
        # is. Sessions open per conversation in hybrid mode, so the stamp is
        # minutes stale at worst.
        from datetime import datetime

        weekday = ["จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์"]
        now = datetime.now()
        stamp = "%s (วัน%s)" % (now.strftime("%Y-%m-%d %H:%M"), weekday[now.weekday()])
        # The store, not the tools module: importing the tools registers
        # them into the registry as a side effect, and building a prompt
        # must never change which functions the model can call.
        from app import memory_store
        from app.config import settings as _settings

        groups = _settings.enabled_tool_groups()
        private_memory = (
            not _settings.multi_session and groups is not None and "memory" in groups
        )

        out = (
            EMMA_INSTRUCTIONS
            .replace("[คำแนะนำตัว]", _emma_introduction(robot_name))
            .replace("[เวลาปัจจุบัน]", stamp)
            .replace("[กฎภาษา]", _language_rule(languages))
            .replace("[เอกสาร]", _personal_docs_line())
            .replace("[ความจำ]", memory_store.prompt_block() if private_memory
                     else "ความจำส่วนตัว: ไม่เปิดใช้ในเซสชันนี้")
        )
        # No websearch group loaded: an instruction naming an undeclared
        # tool is the refusing-model bug, and the fallback itself was the
        # problem the owner turned the group off for (2026-08-27: "ทำไม
        # เลือกบริษัทไทย" answered from siamconsultancy.com with a "75%"
        # figure nobody signed — the assistant's information must be the
        # company's own, prepared and approved, not whatever ranks first).
        from app.config import settings as _settings

        groups = _settings.enabled_tool_groups()
        if groups is not None and "websearch" not in groups:
            out = (out
                   .replace("ไม่พบค่อยใช้ search_web และบอกว่ามาจากเว็บภายนอก",
                            "ไม่พบให้บอกตรงๆ ว่าไม่มีในเอกสาร ห้ามเดา "
                            "และห้ามอ้างข้อมูลจากเว็บภายนอก")
                   .replace(" ค้นเอกสาร ค้นเว็บ เปิดเว็บบนจอ",
                            " ค้นเอกสาร เปิดเว็บบนจอ"))
        return out + _units_tools_suffix()

    facts = extra_facts if extra_facts is not None else load_facts()
    base = (
        BASE_INSTRUCTIONS
        .replace("[คำแนะนำตัว]", _self_introduction(project_name, robot_name))
        .replace("[ชื่อโครงการ]", project_name)
        .replace("[กฎภาษา]", _language_rule(languages))
    )
    out = base + "\n" + facts
    # The company web library, mentioned only when this machine loads it.
    # Turning the mydocs group on in .env was found to be *not enough*: the
    # tool registered, and the robot never called it — rule 14 routes every
    # unknown to search_condo_info and nothing in the prompt said the
    # library existed. A tool the model has no reason to reach for is the
    # same as no tool. Gated on the group so the gallery default (blank
    # TOOL_GROUPS, mydocs opt-in and absent) keeps its prompt byte-identical
    # on a pull.
    from app.config import settings as _settings

    groups = _settings.enabled_tool_groups()
    if groups is not None and "mydocs" in groups:
        out += "\n" + GALLERY_LIBRARY_BLOCK
    return out + _units_tools_suffix()
