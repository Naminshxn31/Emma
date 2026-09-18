# M0.3.1 — Runtime inventory routing

รอบนี้ต่อ policy กับช่องทาง inventory ก่อน ยังไม่ถือว่า M0.3 ทั้งหมดเสร็จ `RuntimeResult` ใน `app/runtime_policy.py` มี `allowed`, `project_id`, `source_id`, `reason`, `payload`; ถ้าไม่ผ่าน `payload` เป็น `{}` เสมอ และผลเครื่องมือส่งกลับ Emma เฉพาะ `policy_trace` กับคำอธิบายให้ตรวจฝ่ายขาย ไม่มีการ์ด/รายการ/ตัวเลขที่ถูกบล็อก

## Live inventory

- เชื่อถือเฉพาะ adapter `live_unit_inventory` ของ server, project ID ของ session และ `projects.slug` ที่มากับ **ทุกแถวที่ตอบกลับ** ต้องตรงกัน ทั้ง query filter และผลลัพธ์จึงต้องผ่าน ไม่ใช้ `project_id` ที่ mapper เติมเองเป็นหลักฐานของแถว
- freshness คือเวลาที่ scoped adapter อ่านฐานขายสำเร็จ (`_fetched_at`) รวมอายุ cache ไม่เกิน `min(INVENTORY_CACHE_S, 60s)`; `updated_at` ของแถวคือเวลาแก้ไขครั้งล่าสุด ไม่ใช่เวลาอ่านข้อมูลสด ห้องที่ไม่มีการเปลี่ยนสถานะนานจึงไม่ถูกบล็อกเพียงเพราะแถวนั้นเก่า
- ส่งเฉพาะฟิลด์การ์ดใน allowlist; `price_thb` และ `base_price_thb` ต้องมี `UNITS_SHOW_PRICE=true` จึงเข้าผลเครื่องมือได้ คำบรรยายอิสระ (`note`, `promo_note`) และราคา/เงื่อนไขโปรโมชั่นไม่เข้าผล Emma แม้แถว active จะมีจริง การมีธงโปรโมชั่นบอกได้ แต่รายละเอียดต้องให้ฝ่ายขายยืนยัน
- รายการและการเปรียบเทียบตรวจ **ทุกแถวก่อน** สร้างผลรวม หากมีแถวผิดโครงการ/ขาด scope/หมด freshness หรือ status ที่ขัดกับ query ให้ปิดทั้งผล ไม่ส่งรายการบางส่วนที่อาจทำให้จำนวนผิด
- ทางเปิด quotation ตรวจการ์ดก่อนสั่งหน้าเว็บ; `price_pair` สำหรับปุ่ม reveal ฝั่งพนักงานตรวจ scope/freshness ด้วย แต่ยังเป็น endpoint แยกที่ต้องมี token และตัวเลขไม่เข้าผลเครื่องมือของ Emma

## Static inventory

ไฟล์ตัวอย่างใช้พัฒนา UI ได้ แต่ `show_unit` ไม่ส่งเนื้อหาให้ Emma อีกต่อไป แม้เปิด `UNITS_SAMPLE`. ไฟล์ขายจริงที่เป็น local export ต้องผ่าน approval/disclosure metadata แบบ curated source ก่อน เพราะไม่มีการอ่านระบบขายสดมายืนยันทุกครั้ง; metadata ที่มีแค่ `approved_by` แบบเดิมไม่พอ. Cache ตรวจ mtime/size ของไฟล์เพื่อไม่ค้างอนุมัติหลังเปลี่ยนไฟล์ และประเมินเวลา effective/expiry ใหม่ทุกครั้ง.

## ขอบเขตถัดไป

`show_plan` แยกไปตาม `docs/runtime-plan-policy.md` แล้ว; slide, document, Canva, print, provider/tool adapters อื่นยังต้องทำ bypass audit ใน M0.3 รอบต่อ ๆ ไป. ห้ามใช้ approval gate ของ facts กับทุก source. ข้อมูล promotion note/เงื่อนไขที่ต้องการเปิดให้ Emma ในอนาคตต้องมี disclosure route ของตน ไม่เปิดโดยอาศัย flag ของยูนิต.
