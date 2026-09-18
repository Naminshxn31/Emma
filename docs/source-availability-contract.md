# M0.1.1 — Source Availability & CI Contract

`data/registry/source_registry.json` ระบุ `availability` และ `required_for` ของแต่ละ source โดยยังคง `authority`/`customer_facing` เดิมไว้ การมีไฟล์หรือ checksum **ไม่ใช่การอนุมัติเนื้อหา**

| `availability` | ความหมาย | ตรวจใน CI | ตรวจบนเครื่องใช้งาน |
| --- | --- | --- | --- |
| `tracked` | ต้องมาพร้อม Git | ต้องมีไฟล์/JSON ที่อ่านได้ | ตรวจเมื่อ feature นั้นใช้งาน |
| `external_required` | ต้องรับจากระบบ/เจ้าของภายนอก Git | ตรวจวิธี materialize และ manifest ที่ตรึงชื่อไฟล์/แฮช หากเป็น asset คงที่ | ตรวจไฟล์และ SHA-256 ของ feature ที่ใช้งาน |
| `generated` | เกิดขณะรันหรือสร้างใหม่ได้ | ไม่บังคับให้มีไฟล์ผลลัพธ์ | ไม่ใช้เป็นข้อมูลโครงการที่อนุมัติแล้ว |
| `review_only` | เก็บตรวจย้อนหลัง ไม่เข้า Emma production ปกติ | ไม่ต้องมีไฟล์ local | `full` แจ้งเตือนเมื่อหาย |

คำสั่งตรวจ:

```powershell
python scripts/audit_data_sources.py --mode ci
python scripts/audit_data_sources.py --mode runtime
python scripts/audit_data_sources.py --mode runtime --feature printing
python scripts/audit_data_sources.py --mode full
python scripts/build_external_asset_manifest.py --check
```

`ci` ออกแบบให้รันใน **clean checkout**: ต้องมี source ที่ระบุ `tracked` และ `data/registry/external_asset_manifest.json` ซึ่งรายชื่อ asset ต้องตรงกับ slide index / print catalogue; ไม่ต้องมีภาพหรือ PDF จริง `runtime` บังคับภาพสไลด์ Embassy World เพราะหน้าจอนำเสนอใช้งานจริง และตรวจ PDF เมื่อเปิด feature `printing` `full` ตรวจของ local ทั้งหมดแต่ไม่ยกระดับข้อมูล `review_only` ที่หายเป็น error

ภาพสไลด์ 135 ไฟล์อยู่ภายนอก Git ใต้ `data/projects/embassy_world/presentations/slides/`; PDF 3 ไฟล์อยู่ใต้ `data/documents/` ให้ผู้ดูแลนำ bundle จากเครื่องที่มี export ชุดปัจจุบันมาวางใน path เหล่านี้ **โดยไม่แก้ชื่อไฟล์** แล้วรัน `--mode runtime` และ `--mode runtime --feature printing` เทียบแฮช หาก asset เปลี่ยน ให้ตรวจ export/เนื้อหาแยกก่อน จากนั้นจึงรัน `python scripts/build_external_asset_manifest.py --write` และทบทวน diff ของ manifest ก่อน commit ห้ามใช้การสร้าง manifest ใหม่เพื่อกลบ checksum mismatch โดยไม่ตรวจไฟล์

ตอนนี้ยัง **ไม่มี URL หรือ artifact store ถาวร** สำหรับ bundle ภายนอก Git ดังนั้น clean checkout ตรวจสัญญาได้ แต่การ deploy ใหม่ยังต้องได้รับ bundle จากผู้ดูแลก่อน จะเรียกว่า deploy แบบอัตโนมัติครบวงจรไม่ได้ ข้อมูล unit สดเป็น service ที่เปลี่ยนตลอด, ไฟล์ unit และ private mydocs เป็นแหล่งภายนอกที่ไม่ตรึง checksum; registry ต้องระบุ `integrity_exemption` ของสามกรณีนี้อย่างชัดเจน ไม่ให้ asset คงที่หลุดจากการตรวจแฮช และต้องยืนยันความถูกต้องของเนื้อหาต่างหาก

`data/showroom/`, `docs/robot-interface-reference/` และภาพเก่าใน `data/review/unindexed_slides/` ถูกจัดเป็น `review_only` เพราะไม่ใช่ input บังคับของ Emma production; ไม่ได้ลบหรืออนุมัติข้อมูลเหล่านั้น รอบนี้ยังไม่เปลี่ยน `.env` เป็น `DATA_ROOT` และยังไม่ทำ approval/disclosure gate

แหล่งที่อาจออกสู่ลูกค้าแต่ยังติด `project_id` ไม่ได้อย่างซื่อตรง (private library ที่คละข้อมูล, print catalogue/PDF ที่ยังไม่ยืนยันเจ้าของโครงการ และ telemetry ของอุปกรณ์) ต้องมี `scope_exception` ชัดเจนใน registry; exception นี้ **ไม่ใช่ใบอนุญาตให้เปิดเผย** และ source ที่เข้าทางหลักของ Emma (`runtime`/`presentation`) ห้ามใช้ exception แทน `project_id` การตัดสินเจ้าของเอกสาร/สิทธิ์เปิดเผยอยู่ใน M0.2
