#!/usr/bin/env python3
"""Make three placeholder PDFs so printing can be tested end to end.

These are **not** sales material. They exist so the path from "ลูกค้าพูด" to
"กระดาษออกมา" can be exercised before the real files arrive, and every page
says so in large letters — a placeholder that could be mistaken for the real
brochure is worse than no placeholder, because it will eventually be handed to
a customer by somebody who didn't look closely.

Run on the machine that has the printer, not in a container: it needs a Thai
font, and Windows already has several.

    python scripts/make_test_documents.py
    python scripts/make_test_documents.py --force
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Fonts that ship with Windows and cover Thai. Checked in order.
THAI_FONTS = [
    r"C:\Windows\Fonts\leelawui.ttf",     # Leelawadee UI — the modern default
    r"C:\Windows\Fonts\tahoma.ttf",       # Tahoma — on every Windows install
    r"C:\Windows\Fonts\THSarabunNew.ttf",
    "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
    "/usr/share/fonts/truetype/thai/Garuda.ttf",
]

DOCS = [
    ("brochure.pdf", "โบรชัวร์โครงการ", [
        "เอกสารนี้เป็นไฟล์ทดสอบระบบพิมพ์เท่านั้น",
        "",
        "ใช้สำหรับตรวจว่าหุ่นยนต์สั่งพิมพ์ได้จริง",
        "ก่อนที่ทีมขายจะนำโบรชัวร์ตัวจริงมาวางแทน",
        "",
        "วิธีเปลี่ยนเป็นไฟล์จริง:",
        "1. วางไฟล์ PDF ตัวจริงทับไฟล์นี้",
        "2. ชื่อไฟล์ต้องตรงกับที่ระบุใน catalogue.json",
        "3. รีสตาร์ตเซิร์ฟเวอร์",
    ]),
    ("booking-form.pdf", "ใบจอง (แบบฟอร์มทดสอบ)", [
        "เอกสารนี้เป็นไฟล์ทดสอบระบบพิมพ์เท่านั้น",
        "ห้ามใช้เป็นเอกสารจริงกับลูกค้า",
        "",
        "ชื่อ-นามสกุล  ______________________________",
        "",
        "เบอร์โทร      ______________________________",
        "",
        "ห้องที่สนใจ   ______________________________",
        "",
        "วันที่         ______________________________",
        "",
        "ลายเซ็นเจ้าหน้าที่ __________________________",
        "",
        "แบบฟอร์มตัวจริงต้องมาจากฝ่ายขาย",
        "หุ่นยนต์เป็นผู้พิมพ์ ไม่ใช่ผู้กรอกหรือรับรอง",
    ]),
    ("floor-plans.pdf", "ผังห้อง (ไฟล์ทดสอบ)", [
        "เอกสารนี้เป็นไฟล์ทดสอบระบบพิมพ์เท่านั้น",
        "",
        "ผังห้องตัวจริงยังไม่ได้นำเข้า",
        "",
        "หมายเหตุ: ข้อมูลแบบห้องและขนาดยังว่างอยู่ใน",
        "data/projects/embassy_world/facts/condo_facts.json — หุ่นยนต์จะบอกลูกค้า",
        "ตรงๆ ว่ายังไม่มีข้อมูล ไม่เดาเอง",
    ]),
]


def find_thai_font() -> str | None:
    for candidate in THAI_FONTS:
        if Path(candidate).exists():
            return candidate
    return None


def build(path: Path, title: str, lines: list[str], font: str | None) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    name = "Body"
    if font:
        pdfmetrics.registerFont(TTFont(name, font))
    else:
        name = "Helvetica"

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    # A watermark that cannot be missed, because the failure mode of a
    # placeholder is somebody handing it to a customer.
    c.saveState()
    c.setFillColor(HexColor("#E8E8E8"))
    c.setFont(name, 64)
    c.translate(width / 2, height / 2)
    c.rotate(45)
    c.drawCentredString(0, 0, "TEST" if name == "Helvetica" else "ไฟล์ทดสอบ")
    c.restoreState()

    c.setFillColor(HexColor("#111111"))
    c.setFont(name, 22)
    c.drawString(50, height - 80, title)
    c.setStrokeColor(HexColor("#CCCCCC"))
    c.line(50, height - 92, width - 50, height - 92)

    c.setFont(name, 13)
    y = height - 130
    for line in lines:
        c.drawString(50, y, line)
        y -= 24

    c.setFont(name, 9)
    c.setFillColor(HexColor("#888888"))
    c.drawString(50, 40, "condo-voice — scripts/make_test_documents.py")
    c.showPage()
    c.save()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    try:
        import reportlab  # noqa: F401
    except ImportError:
        print("ยังไม่ได้ติดตั้ง reportlab — ใช้สำหรับสร้าง PDF ทดสอบเท่านั้น")
        print()
        print("  pip install reportlab")
        print()
        print("(เซิร์ฟเวอร์ไม่ต้องใช้ตัวนี้ เอกสารจริงมาจากทีมขายเป็นไฟล์ PDF อยู่แล้ว)")
        return 1

    from app.config import settings

    out = Path(settings.documents_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    font = find_thai_font()
    if font:
        print("ฟอนต์ไทย: %s" % font)
    else:
        print("ไม่พบฟอนต์ไทย — จะสร้างเป็นภาษาอังกฤษแทน")
        print("(รันบนเครื่อง Windows จะเจอ Leelawadee UI หรือ Tahoma เอง)")

    made, skipped = 0, 0
    for filename, title, lines in DOCS:
        path = out / filename
        if path.exists() and not args.force:
            print("  มีอยู่แล้ว ข้าม: %s  (ใช้ --force เพื่อเขียนทับ)" % filename)
            skipped += 1
            continue
        build(path, title, lines, font)
        print("  สร้าง: %s" % path)
        made += 1

    print()
    print("สร้าง %d ไฟล์ / ข้าม %d ไฟล์" % (made, skipped))
    if made:
        print()
        print("ลองสั่งจาก Python:")
        print('  python -c "import asyncio; from app.tools import load_tools, registry; '
              "load_tools(); print(asyncio.run(registry.dispatch('print_document', "
              "{'document':'โบรชัวร์'})))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
