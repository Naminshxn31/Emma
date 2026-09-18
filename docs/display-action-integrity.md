# M1a — ความถูกต้องของคำสั่งแสดงผังชั้น

`show_plan` แยกเป็นสองช่วง: ตัว handler ตรวจ project, manifest, SHA-256 ของภาพต้นทาง, live inventory และ overlay แล้วคืน `pending_display` (ยังไม่ใช่ความสำเร็จ). `app.tools.registry` จึงส่งคำสั่ง `display_command` ไปยัง socket ของ session ที่เรียก tool และรอ `display.render_ack` ก่อนคืน `ok: true` ให้ Gemini/OpenAI. Provider จึงไม่มีผลสำเร็จให้พูดว่า “เปิดแล้ว” ระหว่างจอยังโหลดอยู่

ภาพที่ตรวจแล้วเก็บใน cache อายุไม่เกิน 5 นาที และเสิร์ฟ byte ชุดเดียวกันจาก `/verified-plan/{sha256}` โดยตรวจ `WS_TOKEN` เช่นเดียวกับ endpoint ข้อมูลขาย. Browser โหลดจาก endpoint นี้, คำนวณ SHA-256 เอง (Web Crypto เมื่อใช้ได้ และ JavaScript fallback สำหรับ HTTP LAN), decode ภาพ, ใส่ภาพกับ overlay บนหน้าจอและรอสอง animation frames ก่อน ACK. `tool_result` ไม่สั่งโหลด URL ภาพซ้ำ

ACK ต้องตรง `command_id`, session socket, `project_id`, `asset_id`, SHA-256 และ `status=rendered`; timeout, socket หลุด, hash ผิด, โหลด/ถอดรหัส/แสดงไม่สำเร็จ คืน `ok: false` โดยไม่ส่งภาพหรือ overlay เข้า provider. Timeout ส่ง `display_cancel` ให้ browser ยกเลิกการโหลดและล้างภาพของ command นั้นถ้ามาถึงช้า. `REQUESTED → DISPATCHED → ACKNOWLEDGED_BY_DISPLAY → RENDERED` เป็นเหตุการณ์ที่ server บันทึกได้จริง; ผล `RENDERED` อนุญาตให้ Emma ยืนยันกับลูกค้า แต่ **ไม่ได้** อ้างว่าได้ยินเสียงตอบรับหรือว่าลูกค้าเห็นจอแล้ว. Browser ACK เป็นหลักฐานระดับ software ของแท็บที่เปิดและภาพที่ decode/paint; ยังไม่ใช่หลักฐานจากเซ็นเซอร์ว่าจอภาพจริงเปิดหรือคนมองเห็น

สัญญานี้คุมผลสำเร็จของ `show_plan` ก่อนส่งกลับ provider ไม่ใช่ตัวกรองคำพูดทุกประโยคที่โมเดลอาจสร้างเองก่อนเรียก tool. การทดสอบกับ provider live ต้องตรวจลำดับเสียงจริงและยืนยันว่าไม่มีคำว่า “เปิดแล้ว” ก่อน ACK. Cache byte ที่ตรวจเป็น in-memory ของ process เดียว; deployment หลาย worker ต้องใช้ store ร่วมกันหรือ sticky routing มิฉะนั้น endpoint จะ fail closed เป็น 404

ขอบเขตนี้ใช้กับผังใน `client/index.html` ซึ่งเป็นจอที่แสดง plan จริง ไม่ใช่ `client/display.html` ที่ใช้ mirror สไลด์. ยังต้องทดสอบ end-to-end บน kiosk/WebView และ Supabase asset จริงก่อน production; การทดสอบจำลองไม่พิสูจน์การแสดงบนจอฮาร์ดแวร์. เส้นทาง Canva, เครื่องพิมพ์ และ provider live session อยู่ใน production validation checklist แยกต่างหาก
