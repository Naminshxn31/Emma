# M0.2 — Knowledge Approval & Disclosure

`project_id` และ `source_id` เป็น identity ที่ server ตรวจ ไม่ใช่สิทธิ์ในการพูดกับลูกค้า ภาพที่แสดงได้ไม่ใช่ข้อเท็จจริงที่ได้รับอนุมัติโดยอัตโนมัติ

## Metadata ของ curated source/claim

ฟิลด์ที่ต้องมี: `source_id`, `project_id`, `approval_status`, `approved_by`, `approved_at`, `effective_at`, `disclosure_scope`, `content_state`; `expires_at` ใส่ `null` ได้ถ้าไม่กำหนดวันหมดอายุ เวลาต้องเป็น ISO 8601 พร้อม timezone. `approval_status="approved"` และ `disclosure_scope="customer"` เท่านั้นจึงใช้กับลูกค้าได้ พร้อมผู้อนุมัติและเวลาที่ครบถ้วน โดยยังต้องอยู่ในช่วงมีผลและตรงโครงการของ session. ถ้าขาด metadata, เป็น draft, หมดอายุ, หรือผิดโครงการ ให้ปฏิเสธก่อนส่งข้อความให้โมเดล. Claim ภายในไฟล์อาจกำหนด metadata ที่เข้มกว่า source ได้ แต่ยกระดับ source ที่เป็น draft เองไม่ได้.

`content_state` แยกจาก approval: `existing`, `completed`, `developing`, `preliminary_concept`, `proposed`. เนื้อหาที่อนุมัติและเป็น `developing` ต้องพูดว่ากำลังพัฒนา ไม่ใช่สร้างเสร็จ; concept/proposed ก็ต้องรักษาสถานะ. ตัวอย่าง metadata `approved` ใน unit tests เป็นข้อมูลสมมติเพื่อพิสูจน์ policy ไม่ใช่การอนุมัติข้อมูลจริง.

## เส้นทางที่ปิดแล้ว

- `condo_facts.json` และ `sales_context.json` ยังเป็น draft/internal; prompt ใช้ fallback ที่ไม่มี claim โครงการ และไม่ inject เนื้อหาจากไฟล์เหล่านี้. ข้อมูลเอกลักษณ์ `project_name` ที่ server ตั้งยังใช้ทักทายได้ แต่ไม่อนุญาตให้ขยายเป็นข้อเท็จจริงอื่น.
- `search_condo_info` ปฏิเสธ claim จาก catalog ที่ไม่มี metadata; ผลมี `policy_trace` ซึ่งระบุ policy version, source ID, project ID, decision/reason และ content state โดยไม่มีข้อความที่ปฏิเสธ. ค้นเพื่อเลือกภาพภายในได้ แต่ไม่ใช้ผลค้น draft ตอบลูกค้า.
- เครื่องมือสไลด์แสดงภาพเดี่ยวได้โดยส่งเพียง URL/ID และป้ายกลาง ๆ; ไม่ส่งชื่อเรื่อง/summary/script/ask จาก draft ให้โมเดล. Script ต้องผ่านทั้ง source metadata และ flag `script_approved` พร้อม `script_approved_by`. Tour ที่มีสไลด์ไม่ผ่าน gate จะไม่เริ่มบรรยาย.
- `search_my_documents` ในโหมด `condo` ปฏิเสธคลังไฟล์ที่ไม่มี approval metadata; โหมด personal Emma ยังใช้งานเอกสารเจ้าของตามเดิม. Prompt ห้องขายไม่ชี้ไปใช้คลัง draft นี้.

`python scripts/audit_data_sources.py --mode ci` รายงาน policy/source ID และจำนวน slide claims ที่ถูกปิด โดยเป็น warning ไม่ใช่การ auto-approve หรือ error ของ CI; runtime tool result และ logger ก็ส่ง `policy_trace` เฉพาะ metadata/decision ไม่ส่งข้อความ draft ใน trace.

## Inventory และงานต่อ

Live inventory เป็น operational source: ตรวจ trusted adapter, project scope, เวลา fetch/TTL และ disclosure ของแต่ละฟิลด์; ไม่ต้องให้คนเซ็นห้องว่างทุกแถว. `evaluate_live_inventory` นิยามและทดสอบสัญญานี้แล้ว แต่ **ยังไม่ได้ต่อเข้าทุกผลลัพธ์ของ units/plan ในรอบ M0.2**. การบังคับครบทุก adapter, static sample, document/catalogue, Canva และเครื่องมืออื่นเป็น M0.3; อย่าอ้างว่ารอบนี้ครอบคลุมทุก customer-facing route. ราคา live ยังถูกกรองด้วยนโยบาย `units_show_price` เดิม; ห้ามใช้ `updated_at` ของแถวแทนเวลา fetch (แถวที่ไม่เปลี่ยนหลายวันยังอาจเป็นข้อมูลสดจาก DB).

การอนุมัติจริงต้องมาจากเจ้าของข้อมูล ไม่แก้ `approval_status` ของ source production ผ่าน test หรือสคริปต์อัตโนมัติ; ทดสอบการแสดงผลด้วย metadata สมมติใน fixture แยกจาก catalog จริง.
