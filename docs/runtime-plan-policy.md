# M0.3.2 — ผังชั้นและแผนที่ใน runtime

`show_plan` ใช้ policy คนละชนิดกับข้อความขาย: `floor_plan_assets` ต้องเป็น source ของโครงการใน session, ชั้นต้องอยู่ใน manifest, path ต้องเป็นไฟล์ `.webp` ที่ปลอดภัย, สถานะภาพต้อง `review-ready` และ SHA-256 ของภาพที่ HTTP adapter อ่านจริงต้องตรงกับ manifest พร้อม MIME `image/webp`. ผลปฏิเสธไม่มี path หรือ overlay ให้ Emma; ภาพที่ตรวจผ่าน **ไม่ใช่การอนุมัติให้พูดข้อความบนภาพ**. Overlay ห้องทุกแถวต้องผ่าน project/freshness gate ของ live inventory, ชั้นและสถานะต้องตรงกับคำขอ, พิกัดต้องเป็นตัวเลขในช่วง 0–100. มีแถวใดผิดให้บล็อกทั้งผัง ไม่สรุปจำนวนบางส่วน.

การ์ด inventory ไม่ส่งฟิลด์ `photo` หรือ `floorplan` ที่ไม่มี asset verification แม้ adapter หรือไฟล์ static จะใส่ URL มา การแสดงภาพผังใช้ route ที่ตรวจ asset ด้านบนเท่านั้น.

Verifier เก็บผลผ่านไว้ได้ 5 นาทีเพื่อลดการดาวน์โหลดซ้ำ โดยไม่ตาม HTTP redirect และจำกัดภาพ 20 MiB. เมื่อเครื่องไม่มี asset endpoint หรือ hash ไม่ตรง ระบบปิดการแสดงผังและให้ฝ่ายขายช่วยเปิดภาพที่ยืนยันแล้ว. ยังไม่ได้ทดสอบกับ endpoint production หรือจอจริง. ปัจจุบันจอยังรับ URL ของระบบขายและดาวน์โหลดภาพซ้ำเอง จึงยังมีช่องว่างแบบ time-of-check/time-of-use หากไฟล์ต้นทางเปลี่ยนหลัง verifier ตรวจ; ก่อน production ที่ต้องการ integrity แบบ end-to-end ควรให้จออ่าน byte ที่เซิร์ฟเวอร์ตรวจแล้วจาก proxy/cache ที่ผูกกับ digest.

`show_map` ใช้ข้อมูลจาก `project_facts` จึงต้องผ่าน approval/disclosure แบบ claim ก่อนเปิด URL บนจอ ไม่ใช้กฎภาพผัง. URL จำกัด HTTPS และ hostname Google Maps จริง ไม่รับเพียง prefix ที่ปลอมโดเมนได้. ข้อมูล map ใน source ปัจจุบันยังเป็น draft/ไม่มีสิทธิ์ customer-facing จึงจะถูกบล็อกจนเจ้าของข้อมูลอนุมัติ โดยไม่ได้เปลี่ยน draft เป็น approved ในงานนี้.

ยังไม่ถือว่า M0.3 จบ: route ของ slide/presentation, document/print, provider และเครื่องมืออื่นต้อง audit และทดสอบ bypass แยก. `test_system_instruction_stays_short` เป็น known failure เดิม ไม่แก้ในรอบนี้.
