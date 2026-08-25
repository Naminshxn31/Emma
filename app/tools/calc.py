"""
The ROI sheet: numbers heard from the guest, arithmetic done by the server.

Three of this project's most expensive failure modes meet in one feature —
money, spoken numbers, and mental arithmetic — so the design is mostly a
list of things the model is *not* allowed to do:

- **It does not do the math.** Compound growth over years is exactly the
  kind of arithmetic a language model gets confidently wrong, and a wrong
  number about money, spoken with a warm voice, is worse than no answer.
  The server computes; the model reads the result out.

- **It does not supply the numbers.** Every figure comes from the guest —
  their budget, their expected rent, their guess at appreciation. That is
  what keeps this on the right side of "ห้ามแต่งข้อมูลโครงการเองเด็ดขาด":
  arithmetic on the *guest's own assumptions*, labelled as such on screen,
  is a service; the project's numbers stay absent until the sales manager
  signs them (item 19 on the sales list — the formula was always the easy
  half, the assumptions need an owner).

- **The screen is the confirmation.** "อะไรนะ 50" is in a real transcript
  of this project: heard numbers go wrong. Every input is shown on the
  card the moment it is used, so a mishearing is visible to the person who
  said the number — the one party who can actually correct it.

State merges per call, so "เปลี่ยนปีเป็น 10" updates one field instead of
requiring the whole sheet again — the screen the owner asked for: "เปลี่ยน
ตัวเลขได้ตลอดเวลา". Cleared when a new session takes the robot over, for
the same reason the transcript is: the last guest's budget is not the next
guest's business.
"""
from __future__ import annotations

import logging

from app import turnlog
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.calc")

#: The sheet as it currently stands. Merged, not replaced, on every call.
STATE: dict[str, float] = {}

_FIELDS = ("price_thb", "monthly_rent_thb", "years",
           "appreciation_pct", "selling_fee_pct")

#: Thai labels for the card, in display order.
FIELD_TH = {
    "price_thb": "ราคาห้อง (บาท)",
    "monthly_rent_thb": "ค่าเช่าที่คาดหวัง/เดือน (บาท)",
    "years": "ระยะเวลาถือครอง (ปี)",
    "appreciation_pct": "ราคาขึ้นต่อปี (%)",
    "selling_fee_pct": "ค่าธรรมเนียมตอนขาย (%)",
}


def reset() -> None:
    """New session, blank sheet. Also used by tests."""
    STATE.clear()


def _round(x: float) -> float:
    return round(x, 2)


def compute(inputs: dict[str, float]) -> dict[str, float]:
    """Exact arithmetic on whatever inputs exist. Pure, so tests can pin it.

    Only computes what its inputs allow: a sheet with price and rent but no
    holding period gets a yield and nothing else, rather than projections
    built on a default nobody chose. A default holding period would be this
    module quietly inventing the one kind of number it exists to refuse.
    """
    out: dict[str, float] = {}
    price = inputs.get("price_thb") or 0
    rent = inputs.get("monthly_rent_thb") or 0
    years = inputs.get("years") or 0
    appreciation = inputs.get("appreciation_pct")
    fee = inputs.get("selling_fee_pct") or 0

    if price <= 0:
        return out
    if rent > 0:
        out["rental_yield_pct"] = _round(rent * 12 / price * 100)
    if years > 0:
        if rent > 0:
            out["rent_total_thb"] = _round(rent * 12 * years)
        if appreciation is not None:
            future = price * (1 + appreciation / 100) ** years
            out["future_value_thb"] = _round(future)
            out["capital_gain_thb"] = _round(future - price)
            if fee > 0:
                out["selling_fee_thb"] = _round(future * fee / 100)
        net = (out.get("rent_total_thb", 0)
               + out.get("capital_gain_thb", 0)
               - out.get("selling_fee_thb", 0))
        if rent > 0 or appreciation is not None:
            out["net_gain_thb"] = _round(net)
            out["roi_pct"] = _round(net / price * 100)
            if net / price > -1:
                out["annualized_pct"] = _round(
                    ((1 + net / price) ** (1 / years) - 1) * 100)
    return out


RESULT_TH = {
    "rental_yield_pct": "ผลตอบแทนค่าเช่า (%/ปี)",
    "rent_total_thb": "รายได้ค่าเช่ารวม (บาท)",
    "future_value_thb": "มูลค่าเมื่อครบกำหนด (บาท)",
    "capital_gain_thb": "กำไรส่วนต่างราคา (บาท)",
    "selling_fee_thb": "หักค่าธรรมเนียมขาย (บาท)",
    "net_gain_thb": "ผลตอบแทนสุทธิ (บาท)",
    "roi_pct": "ROI รวม (%)",
    "annualized_pct": "เฉลี่ยต่อปี (%)",
}


@tool(
    name="calc_roi",
    description=(
        "คำนวณผลตอบแทนการลงทุนจากตัวเลขที่ลูกค้าบอกเอง และแสดงตารางบนจอ "
        "ใส่เฉพาะฟิลด์ที่ลูกค้าเพิ่งบอกหรือขอเปลี่ยน ฟิลด์เดิมระบบจำไว้ให้ "
        "เช่นลูกค้าบอก 'เปลี่ยนเป็นสิบปี' ให้ส่ง years=10 อย่างเดียว "
        "ก่อนเรียกครั้งแรกให้ทวนตัวเลขที่ได้ยินกลับให้ลูกค้ายืนยันก่อนเสมอ "
        "(เลขจากการฟังผิดได้ — เคยมี 'อะไรนะ 50' มาแล้ว) "
        "ตัวเลขทุกตัวต้องมาจากปากลูกค้าเท่านั้น ห้ามเดา yield หรือ % ราคาขึ้นแทนเขา "
        "และห้ามคำนวณเลขเองเด็ดขาด ให้อ่านผลจากฟิลด์ results ที่ได้กลับมาเท่านั้น"
    ),
    parameters={
        "type": "object",
        "properties": {
            "price_thb": {"type": "number", "description": "ราคาห้อง (บาท)"},
            "monthly_rent_thb": {"type": "number",
                                 "description": "ค่าเช่าที่ลูกค้าคาดหวังต่อเดือน (บาท)"},
            "years": {"type": "number", "description": "ระยะเวลาถือครอง (ปี)"},
            "appreciation_pct": {"type": "number",
                                 "description": "สมมติฐานราคาขึ้นต่อปี (%) ของลูกค้า"},
            "selling_fee_pct": {"type": "number",
                                "description": "ค่าธรรมเนียมตอนขาย (%) ถ้าลูกค้าระบุ"},
        },
    },
    tags=["calc"],
)
def calc_roi(**kwargs) -> dict:
    changed = {k: float(v) for k, v in kwargs.items()
               if k in _FIELDS and v is not None}
    STATE.update(changed)

    if not STATE.get("price_thb"):
        return {"ok": False, "error": "no price",
                "instruction": ("ยังไม่มีราคาห้อง ให้ถามลูกค้าก่อนว่าห้องราคาเท่าไหร่ "
                                "ห้ามเดาราคาเอง")}

    results = compute(STATE)
    turnlog.record("calc_roi", changed=sorted(changed), fields=sorted(STATE))
    return {
        "ok": True, "screen": "calc",
        "inputs": dict(STATE), "results": results,
        "input_labels": {k: FIELD_TH[k] for k in STATE},
        "result_labels": {k: RESULT_TH[k] for k in results},
        # Said every call, not once: an instruction that must be remembered
        # across turns is one that gets dropped, and what would be dropped
        # here is "whose numbers these are".
        "instruction": (
            "อ่านผลจาก results เท่านั้น ห้ามคำนวณหรือปัดตัวเลขเอง "
            "และต้องพูดกำกับว่านี่คำนวณจากตัวเลขสมมติของลูกค้าเอง "
            "ไม่ใช่ตัวเลขหรือการรับประกันของโครงการ ราคาและผลตอบแทนจริง "
            "ต้องสอบถามฝ่ายขาย"
        ),
    }
