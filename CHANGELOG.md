# ประวัติการเปลี่ยนแปลง

## 2026-09-09 — ตรวจ staged files ก่อน commit

- ตรวจรายการ staged **95 ไฟล์ ประมาณ 2.66 MB**: ไม่พบ API key/private key/token ที่ตั้งใช้ในเครื่อง, ไม่มี `.env`, debug log, SDK/APK archive หรือ local Android binaries รวมอยู่
- Staged whitespace check พบ blank line ท้าย `AGENTS.md` (ไม่อยู่ใน tracked diff เดิม) จึงตัดบรรทัดว่างโดยไม่เปลี่ยนข้อกำหนด และเอา unused import ใน test package ออก; รอตรวจ staged ซ้ำ
- ตรวจหลังแก้: package tests **10 passed ใน 0.60s**, staged whitespace check ผ่าน; พร้อมทำ local commit บน `dev` โดยไม่ push
- ชุดรับ SDK มีเอกสาร/ตัวตรวจ/ADB ที่ใช้งานได้ แต่ยังไม่มี vendor AAR/demo APK/source; คำขอลิงก์จากผู้ใช้ยังไม่ได้รับคำตอบ จึงไม่อ้างว่าดาวน์โหลดไฟล์ผู้ขายสำเร็จ

## 2026-09-09 — เตรียม SDK และจัดไฟล์ก่อน commit ชุด 2

- ลบเฉพาะผลทดสอบชั่วคราว/root bytecode **53 โฟลเดอร์** และ `.coverage` รวม **41,609,903 bytes**; ตรวจ absolute paths และไม่มี reparse point ในต้นไม้ก่อนลบ ไม่แก้ runtime environments/model/data
- ดาวน์โหลด Android Platform-Tools จาก Google ผ่าน HTTPS เข้า `tools/android/` ที่ถูก ignore; ตรวจ `adb version` ผ่าน **37.0.1-15733141** เก็บ checksum/URL/เวลาใน local receipt; ไม่รันคำสั่งติดตั้งหรือสั่งงานหุ่น
- `scripts/check_robot_readiness.py`, `tools/android/README.md`, `docs/robot-arrival-2026-09-09.md`: ระบุ local ADB ที่เตรียมสำเร็จและลิงก์ชุดรับ SDK; AAR/demo APK/source ของผู้ขายยังไม่ได้รับ
- ตัวตรวจ package **10 tests passed ใน 0.75s**; baseline backend/client ล่าสุดก่อนชุดเตรียมนี้ **1,202 tests passed + Node เสียง 3 ชุดผ่าน** ไม่มีการแก้ runtime voice/navigation ในชุดนี้

## 2026-09-09 — เตรียม SDK และจัดไฟล์ก่อน commit ชุด 1

- `tests/test_robot_package.py`, `scripts/inspect_robot_package.py`: เพิ่ม regression ด้วย ZIP fixture สำหรับ AAR/APK, checksum ผิด, manifest/dex หาย, archive เสีย, SDK คนละตัว และ path ผิดปกติ; fixture ไม่ใช่ไฟล์สำหรับติดตั้งจริง รอรัน

- `vendor/aobo/README.md`, `tools/android/README.md`, `scripts/inspect_robot_package.py`: จัดชุดรับ vendor AAR/demo APK/source พร้อมข้อความขอไฟล์และตัวตรวจโครงสร้าง/ABI/คลาส/SHA-256 แบบไม่รันโค้ด; ไม่มีการสร้าง AAR/APK ปลอมหรืออ้างว่า vendor SDK พร้อมแล้ว รอตรวจ regression ของตัวตรวจไฟล์
- ตรวจคู่มือไทยหน้า 4 และ PDF ต้นฉบับ ไม่พบ download URL ของ AAR/APK; ค้นเว็บ Aobo ทางการพบการเสนอ SDK แต่ยังไม่พบไฟล์ที่ยืนยันตรงรุ่น จึงต้องรับจากผู้ขาย
- `.gitignore`: กัน debug log, incoming vendor binaries และ local Android tool downloads ออกจาก commit; ตรวจ secret patterns ในไฟล์เปลี่ยนแปลงไม่พบ key/private key และ Three.js vendored checksums ตรง manifest
- เตรียมลบเฉพาะผล pytest ชั่วคราว/coverage/root bytecode ที่สร้างใหม่ได้ หลังตรวจ absolute path ให้อยู่ใน workspace; ยังไม่ลบ source, environments, models, ข้อมูลลูกค้า หรือไฟล์ SDK ของผู้ขาย

## 2026-09-09 — ผลตรวจรับฝั่งซอฟต์แวร์รอบสุดท้าย

- Full suite หลังแก้ทั้งหมด **1,202 passed, 1 warning ใน 182.38s**; warning คือ Starlette TestClient/httpx deprecation; รอบสุดท้ายไม่พบข้อความ pending Proactor ที่เคยพบรอบก่อน
- Node เสียง 3 ชุด, syntax Python ไฟล์ที่แก้ และ `git diff --check` ผ่าน; preflight ตรวจซ้ำยังรายงานสิ่งขาดตามจริง ไม่พิมพ์ secret
- เปิด simulator รุ่นแก้บน **8010** สำเร็จ health ผ่านและมี standby wake socket เชื่อม; ปิด QA 8011 แล้ว ไม่เปิด production 8001 หรือเปลี่ยน `.env` ให้เดินจริง ขาด Android bridge/AAR และผลทดสอบฮาร์ดแวร์ตามรายงาน
- `docs/robot-arrival-2026-09-09.md`: ปรับผลทดสอบล่าสุดและสถานะส่งมอบ เก็บรายการขอผู้ขาย/เกณฑ์รับเครื่องครบ; ยังไม่มีการรับรองว่าติดตั้งแล้วเดินหรือควบคุม Smart Home จริงได้ทันที

## 2026-09-09 — ตรวจรับหุ่น ชุด 3: Gemini 3.1 คำทักทายเงียบ

- Live QA รอบเต็ม: greeting **208,350 audio bytes**, simulated navigation → arrival **184,830 audio bytes**, จบทั้งสอง turn ใน 17.70s; ไม่ส่งไมค์ ไม่สั่งอุปกรณ์จริง; Node เสียง 3 ชุดผ่านซ้ำ
- `docs/robot-arrival-2026-09-09.md`: เพิ่มผลรับเสียงจาก provider และอ้างอิง Google; ตรวจ live 8010 idle ไม่มีงานเดิน ก่อนนำ backend รุ่นแก้ขึ้นใหม่ (การ restart ทำให้สถานะจำลองเริ่มใหม่)

- QA server 8011 รุ่นแก้: เชื่อม provider และได้รับ audio chunk แรก **4,830 bytes ใน 3.34 วินาที** โดยไม่ส่งไมค์; เป็นหลักฐานเสียงถึงไคลเอนต์ ไม่ใช่การฟังลำโพงจริง
- `tests/test_voice.py`: parameterize greeting/resumption ทั้ง 3.1 และ 2.5; targeted รอบก่อน **168 passed, 1 failed** เพราะ fake session เดิมมีเฉพาะ API 2.5 และอ่าน model จาก environment; ปรับ fixture ให้ตรงทั้งสอง protocol แล้ว รอตรวจซ้ำ

- Live check เปิด Gemini สำเร็จแต่รอ 35 วินาทีไม่มี audio; diagnostics ยืนยัน turn จบโดย `audio_observed=false` ไม่ใช่การทดสอบลำโพง
- `app/providers/gemini.py`, `tests/test_gemini_live_text.py`: เปลี่ยนข้อความคำทักทาย/ประกาศของ Gemini 3.1 Flash Live เป็น `send_realtime_input(text=...)` ตาม [Google Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities); รุ่นนี้ `send_client_content` ใช้ seed history ไม่ใช่ข้อความสด เก็บเส้นทาง 2.5 เดิมไว้; รอ automated/live check หลังแก้
- Full suite ก่อนแก้ provider รอบนี้ **1,198 passed, 1 warning ใน 168.55s**; มี Windows Proactor accept task ค้างขณะ test teardown 2 ข้อความ (exit 0) จึงไม่อ้างว่า log สะอาดทั้งหมด

## 2026-09-09 — ตรวจรับหุ่น ชุด 2

- `README.md`, `docs/ต่อกับหุ่นยนต์ Astronaut.md`: แก้ข้อความเก่าที่อ้างว่า server เสร็จครบ/ยังไม่มีการยืนยันตัวตน/ยังไม่มีตัวจำลองสำเร็จ ให้ตรงกับของจริง และระบุข้อจำกัด AEC/half-duplex กับ protocol ชัดเจน

- `docs/robot-arrival-2026-09-09.md`, `docs/robot-integration.md`: บันทึกผลตรวจ/สิ่งขาดก่อนรับเครื่อง/รายการขอผู้ขาย/เกณฑ์ทดสอบจริง และแยก wire protocol ที่มีจาก ACK/telemetry ที่ยังไม่มี พร้อมอ้างอิง Android/MDN
- preflight รันจริง: TLS certificate/key โหลดคู่กันได้, wake/IR config พร้อม; production 8001 ไม่ทำงานและยังไม่มี robot connection/SDK/APK/การเปิด tools สำหรับหุ่นจริง; full regression หลังแก้กำลังรัน

- `scripts/check_robot_readiness.py`: เพิ่ม preflight แบบอ่านอย่างเดียว ตรวจ config/TLS certificate-key/ไฟล์ SDK-APK ที่ระบุ/เครื่องมือ Android/wake/IR และ optional local health; ไม่พิมพ์ secret ไม่ส่งคำสั่งฮาร์ดแวร์ และแยก hardware acceptance ว่ายังไม่ทดสอบเสมอ; รอรัน

## 2026-09-09 — ตรวจรับหุ่น ชุด 1

- รอบ targeted แรก **120 passed, 2 failed**: สองกรณีเดิมคาดว่า mock ยืนยัน `moving=false`; ปรับ `tests/test_robot.py` ให้ตรวจ `null` เพราะยังไม่รู้การเคลื่อนที่จริง และให้ `robot_link.py` แสดง unknown เมื่อส่งคำสั่งล้มเหลวด้วย; รอตรวจซ้ำ

- `tests/test_robot_arrival_readiness.py`: เพิ่ม regression ข้อมูล bridge ผิดรูปแบบ, boolean ที่เป็น string/number, จุดหมายกำกวม, timeout/cancel และสถานะ unknown หลังหยุด/ตัดการเชื่อมต่อ; รอรัน

- `app/session.py`, `app/tools/robot_link.py`, `app/tools/robot.py`: ปฏิเสธ JSON/POI ผิดรูปแบบและ arrival ที่ไม่ได้ระบุ boolean จริง; ไม่เลือกจุดหมายเมื่อมีหลายชื่อที่ไม่ใช่ชื่อซ้อนกัน; แยกสถานะจากคำสั่งออกจากข้อมูลยืนยันจริง และแจ้ง timeout ตามผลส่ง cancel จริง
- ตรวจฐานก่อนแก้: pytest **1,165 passed, 1 warning (173.69s)**; smoke import ทุก extras ผ่าน; Node เสียง 3 ชุดผ่าน; `uv --no-cache pip check` ตรวจ 103 packages ผ่าน (ครั้งแรกใช้ cache ปกติติดสิทธิ์ จึงตรวจโดยไม่ใช้ cache)
- พอร์ตจำลอง 8010 health ผ่าน; production HTTPS 8001 connection refused; ยังไม่พบ APK/AAR ใน repo/Downloads และไม่พบ adb/java/gradle ใน PATH; ยังไม่ได้ทดสอบตัวหุ่นจริง
- รอตรวจ regression หลังแก้; การตรวจนี้ยังไม่รับรองความปลอดภัยหรือความพร้อมของฮาร์ดแวร์

## 2026-09-08 — ส่งมอบบ้านขนาดใหญ่ขึ้น

- หน้า 8010 โหลด third person ที่ `layoutScale=1.5` สำเร็จ ไม่มี JavaScript error; ไม่ต้องรีสตาร์ทเซิร์ฟเวอร์หรือสายเสียงเดิม เพราะเปลี่ยนเฉพาะ client/คู่มือ ผู้ใช้กด Ctrl+F5 เพื่อรับฉากล่าสุด
- ตรวจภาพพื้นหลังแยกระดับแล้ว ไม่พบลายซ้อนที่เห็นในรอบก่อน; syntax และ `git diff --check` ผ่าน ปิด QA 8011 แล้ว
- Browser regression ครอบคลุมผังขยาย/การชน/POI→การเดินถึง/กล้อง/อุปกรณ์/มือถือ ตามชุด 3; ไม่รัน Python full suite ซ้ำเพราะไม่ได้เปลี่ยน backend หรือ voice

### ขยายบ้าน ชุด 3: ผ่านการเดินและแก้พื้นซ้อนฐาน

- `client/robot-scene-3d.js`: แยกระดับพื้นหลักออกจากผิวฐาน platform หลัง screenshot ระยะใกล้พบ z-fighting โดยให้พื้นหลักอยู่ระหว่างฐานกับผิวห้อง
- `docs/robot-simulator.md`: ระบุอัตราขยายพื้นที่ กล้อง และความสัมพันธ์ของพิกัด engine/JSON กับฉาก
- Browser ผ่านจุดเริ่ม/ขอบเขตใหม่, เดินชนกำแพง, ไฟ/ม่าน/ทีวี, raycast จุดฟิตเนส→arrived, กล้องตามหุ่น และมือถือไม่มีล้น/ไม่มี page error; QA แรกผิดที่คาดว่าชนแล้วต้องหยุดทั้งสองแกน แก้ harness ให้ยอมรับการไถลตามผนังโดยไม่เปลี่ยน movement
- Syntax และ diff check ผ่าน; รอตรวจพื้นหลังแยกระดับและหน้า 8010 ล่าสุด

### ขยายบ้าน ชุด 2: ตรวจพื้นและภาพรวม

- `client/robot-scene-3d.js`: แก้ฐานพื้นเดิมที่สูงทับผิวห้อง และเอาแถบรอยต่อซ้อนระดับเดียวกับพื้นออก ให้เห็น texture ไม้/กระเบื้องและลดการกระพริบหลังขยาย
- `client/robot-interior.js`, `client/robot-explorer.js`: แสดงอาคารภายนอกเฉพาะโหมดเดิน ภาพรวมจึงเห็นผังบ้านชัด
- Syntax ผ่าน; browser เปิดผัง 1.5 เท่าได้ ไม่มี page error ตรวจภาพเดินและภาพรวมแล้ว รอตรวจ regression การเดิน/อุปกรณ์หลังชุดนี้

## 2026-09-08 — ขยายบ้าน ชุด 1

- `client/robot-scene-3d.js`: ขยายผังแนวราบ 1.5 เท่าต่อด้าน (พื้นที่ 2.25 เท่า), จัดเฟอร์นิเจอร์เป็นกลุ่มคงขนาดเดิม แล้วกระจายตำแหน่งตามผังใหม่; แปลง POI/เส้นทาง/หุ่น/minimap และ collision ด้วยอัตราเดียวกัน
- `client/robot-explorer.js`: ขอบเขตเดินและจุดเริ่มตามบ้านที่ใหญ่ขึ้น กล้องบุคคลที่สามถอยจาก 2.1 เป็น 2.8 หน่วย; ตัวคนและหุ่นไม่ขยาย
- `client/robot-interior.js`: เก็บขนาดทีวี/แอร์/โคม, ขยายหน้าต่าง/ม่านกับผนัง และระยะแสง; texture รองรับเฟอร์นิเจอร์ใน group
- รอ syntax และ browser QA ตรวจห้อง/การเดินชน/จุดหมาย/อุปกรณ์; ไม่แก้ backend คำสั่งหรือเสียง

## 2026-09-08 — ส่งมอบ Third person และ smart home จำลอง

- เปลี่ยนเฉพาะเซิร์ฟเวอร์จำลอง 8010 หลังตรวจ provider idle และไม่มี active command เปิดรุ่นล่าสุดแล้วและปิด QA 8011
- ตรวจหน้าใช้งานจริง: third-person เปิดสำเร็จ, ตัวเลขซูมตอบสนอง, มีแผงอุปกรณ์ 4 ตัว ไม่มี browser error; handshake Gemini จริงรับ configuration ที่มี robot/virtual-home tools ได้ ready โดยไม่ส่งเสียงไมค์ผู้ใช้
- ผลทดสอบ: full suite **1,165 passed, 1 warning**, หลังแก้เปิดไฟจาก 0 targeted **12 passed, 1 warning**; Node เสียง 3 ชุดผ่านและ browser desktop/mobile/isolation/fallback ตามชุด 10
- ผู้ใช้เปิด `http://127.0.0.1:8010` และ Ctrl+F5; คลิกฉากแล้ว WASD เดิน, ลากมอง, V สลับภาพรวม, F ตาม Emma และสั่งอุปกรณ์ผ่านเสียง/แผงควบคุมได้
- ขอบเขตที่ส่งมอบเป็นตัวละครและโมเดลห้องสร้างขึ้น ไม่มีแบบวัดสถานที่จริง อุปกรณ์บ้านทั้งหมดจำลอง ไม่แตะ Broadlink หรืออุปกรณ์จริง และไม่เปลี่ยน `.env`/API key

### Third person ชุด 10: ผ่านการซ้อมครบวงจร

- `client/robot-explorer.js`: ตัวเลขซูมแสดงระยะของ third-person จริงเมื่อใช้ล้อหรือปุ่ม แทนค่าค้างจากภาพรวม
- Browser QA ผ่าน: เดินชนขอบ/ผนัง, เปลี่ยน focus แล้วหยุดเดินแม้ยังกดปุ่ม, ไฟปิดแล้วความสว่างภาพเฉลี่ยลด 139.9 → 30.0, ม่าน/แอร์/อุณหภูมิ/ทีวี, voice iframe → fake AI → tool จริงของตัวจำลองปิดไฟ, สลับภาพรวม/ตามหุ่น/เดิน, export smart_home และ journal
- Mobile 390px ไม่มีล้น ปุ่มเดินค้าง/ปล่อยทำงาน; ปิด WebGL แล้วยังใช้แผง smart home ในฉากสำรองได้ ไม่มี page error ตรวจภาพ desktop/mobile/ไฟเปิด/ไฟปิดแล้ว
- Node เสียง 3 ชุดและ syntax ผ่าน; รอตรวจซูมหลังปรับและเปิดเซิร์ฟเวอร์ใช้งานจริงรุ่นล่าสุด

### Third person ชุด 9: คู่มือการเดินและคำสั่งบ้าน

- `README.md`, `docs/robot-simulator.md`: วิธีเดิน third person/ภาพรวม/touch, แยกผู้เยี่ยมชมจากหุ่น, ตัวอย่างเสียงทั้ง 4 อุปกรณ์, ค่าที่รองรับ, export/reset และข้อจำกัดผังสมมติ/ไฟทั้งฉาก/ไม่จำลองความร้อน
- `client/robot-simulator.html`: accessible description ตรงกับ third person ค่าเริ่มต้น
- หลังชุด 8 targeted smart-home **12 passed, 1 warning**; รอ browser QA รอบส่งมอบ

### Third person ชุด 8: เปิดไฟหลังหรี่สุดและหน้าจอทีวี

- `app/robot_home.py`, `tests/test_robot_home.py`: เมื่อหรี่ไฟเป็น 0 แล้วสั่งเปิด ให้คืนความสว่าง 85% เพื่อเห็นแสงจริงในฉาก เพิ่มกรณีต่อเนื่องนี้ใน regression
- `client/robot-interior.js`: หน้าจอทีวีมีภาพจำลองวาดในเครื่องเมื่อเปิดและดับเมื่อปิด ไม่มีโหลดสื่อภายนอกหรือเสียงแทรก
- Full suite ก่อนชุดนี้ **1,165 passed, 1 warning ใน 163.07 วินาที**; ตรวจภาพห้อง/กล้องรุ่นล่าสุดแล้ว รอ targeted และ browser รอบสุดท้ายหลังชุดนี้

### Third person ชุด 7: กล้องเริ่มต้นและด้านหน้าของอุปกรณ์

- `client/robot-explorer.js`: ลดระยะกล้องเริ่มต้น เพิ่ม FOV และย้ายจุดเริ่มให้กล้องอยู่ในห้อง ไม่ถูกกรอบประตูบังเกือบทั้งฉาก ปรับส่วนผมด้านหลังของ avatar
- `client/robot-interior.js`: ย้ายผิวทีวีมาอยู่ด้านหน้าตัวเครื่องและไฟสถานะแอร์เข้าด้านห้อง แก้ geometry ที่บังผลเปิด/ปิด
- เป็นผลจากตรวจ screenshot รุ่นก่อน; รอตรวจภาพและ browser regression ล่าสุด

### Third person ชุด 6: ตรวจมุมระดับคนและรายละเอียดห้อง

- `client/robot-interior.js`: หลังตรวจภาพจริง เพิ่มเพดานที่ซ่อนเมื่อดูภาพรวม และเปลี่ยนม่านจากชิ้นแยกเป็นผ้าจีบผืนต่อเนื่อง
- `client/robot-explorer.js`: เริ่มภายในห้องตัวอย่าง ปรับไหล่/ใบหน้า/ขากางเกงและรองเท้าของ avatar ให้มีสัดส่วนคนชัดขึ้น
- Targeted backend/isolation/voice **50 passed, 1 warning**; browser เปิด third person ได้ ไม่มี page error และเดินจากจุดเริ่มผ่านประตูได้ รอตรวจภาพล่าสุด/แสง/ม่านและ full suite ที่กำลังรัน

### Third person ชุด 5: ทดสอบขอบเขตอุปกรณ์

- เพิ่ม `tests/test_robot_home.py`: API/tool ใช้ state เดียวกัน, reset/journal, validation แบบไม่เปลี่ยน state เมื่อผิด, origin/JSON, tools ปิดนอกจำลอง และ trap Broadlink ไม่ให้แตะอุปกรณ์จริง
- `tests/test_robot_voice.py`: ส่ง PCM ผ่าน websocket → provider fake → actual virtual-home tool เปลี่ยนไฟ และไม่ส่ง IR
- Syntax JavaScript 4 ไฟล์ใหม่/แก้ผ่าน; รอผล targeted tests และ browser QA

### Third person ชุด 4: แผงเดินและ smart home

- `client/robot-simulator.html`, `.css`, `.js`: สลับเดินสำรวจ/ภาพรวม, ปุ่มเดิน touch, คำแนะนำ WASD และแผงไฟ/ความสว่าง แอร์/อุณหภูมิ ม่าน/เปอร์เซ็นต์ ทีวี ที่ส่ง `/api/home` และแสดงสถานะจาก server
- `client/robot-explorer.js`: จุดเริ่มผู้เยี่ยมชมอยู่หน้าประตูห้องตัวอย่าง ให้เห็นภายในในมุมด้านหลัง; smart home ใช้เสียงเดิมและค่า state เดียวกันกับปุ่ม
- รอ syntax/backend tests และ browser QA; ไม่เปลี่ยนการรับเสียงหรือ Broadlink จริง

### Third person ชุด 3: ห้องและอุปกรณ์ที่มองเห็น

- เพิ่ม `client/robot-interior.js`: ผนังสูง/ช่องประตู หน้าต่าง/วิวเมือง texture ไม้และกระเบื้องแบบ procedural, โคมไฟ PointLight, ม่านเคลื่อนตามเปอร์เซ็นต์, ทีวีและแอร์เปลี่ยนตาม state
- `client/robot-scene-3d.js`: เชื่อมตัวละครผู้ใช้และ collision, เปิด third person เป็นค่าเริ่มต้น, สลับภาพรวม/ตาม Emma, ปรับความสว่างจากไฟและแสงผ่านม่าน พร้อมจุดผู้ใช้ใน minimap
- `app/robot_simulator.py`: เสิร์ฟ module ใหม่แบบ allowlist; ยังรอ UI และ browser QA รวมการเปิด/ปิดไฟเห็นผลในฉาก

### Third person ชุด 2: ตัวผู้สำรวจ

- เพิ่ม `client/robot-explorer.js`: ตัวละครคนและกล้องด้านหลัง, WASD/ลูกศร/Shift, ลากมอง/ล้อซูม และปุ่มเดินบน touch
- เดินเฉพาะตัวละครผู้ใช้ใน browser ไม่ส่งคำสั่งเคลื่อนหุ่น; circle/AABB collision พร้อม substeps กันทะลุเฟอร์นิเจอร์/ขอบฉาก, raycast ระยะกล้อง และหยุดเดินเมื่อเสีย focus
- รอเชื่อม scene/UI และตรวจระยะชน/กล้องจริงใน browser

## 2026-09-08 — Third person และ smart home ชุด 1

- เพิ่ม `app/robot_home.py`, `app/tools/simulation_home.py`: คำสั่งไฟ/ความสว่าง แอร์/อุณหภูมิ ม่าน/เปอร์เซ็นต์ และทีวีแบบจำลอง พร้อม validation ไม่มี device driver
- `app/robot_simulation.py`, `app/robot_simulator.py`: สถานะ smart_home ใน snapshot/reset/export, journal และ `/api/home` ใช้ handler เดียวกับเสียง
- `app/robot_backend.py`, `app/tools/registry.py`, `app/tools/__init__.py`: เพิ่ม 2 tools เฉพาะ context จำลอง ปิดเสมอนอก context; ไม่เรียก smarthome/Broadlink จริง
- `app/prompts.py`, `app/robot_diagnostics.py`: อธิบายคำสั่ง/ขอบเขตไฟทั้งฉากและวัดชื่อ tool ใหม่; รอทดสอบ isolation/validation และเชื่อมภาพ

## 2026-09-08 — ส่งมอบ Emma World 3D

- ตรวจ gesture หลังชุด 5 ผ่านด้วย Chromium mobile touch: pinch ซูมจริง คืนกล้องได้ และไม่เปลี่ยนจุดหมายโดยไม่ตั้งใจ; Node syntax และ `git diff --check` ผ่าน
- ผล regression รอบนี้ **37 passed, 1 warning** พร้อม Node เสียง 3 ชุดและ browser QA ตามบันทึกชุด 5; ไม่รัน full suite ซ้ำ เพราะไม่ได้แก้ voice/backend การเดิน และ targeted/API/browser ครอบคลุมเส้นทางที่เปลี่ยน
- ตรวจไม่มี active command และ provider idle ก่อนแทนที่เซิร์ฟเวอร์ 8010; เปิดรุ่นล่าสุดแล้ว ปิด QA 8011 ตรวจหน้าใช้งานจริงว่า renderer เป็น WebGL, มี 5 จุดหมาย, wake พร้อม และไม่มี page error โดยไม่เปิดไมค์หรือเรียก AI ในการตรวจส่งมอบ
- ทางเข้าเดิม `http://127.0.0.1:8010` กด Ctrl+F5 เพื่อโหลดหน้า 3D; โมเดลและแผนที่เป็น geometry สมมติสำหรับซ้อมคำสั่ง ไม่ใช่โมเดลจาก CAD หรือการทดสอบ collision/เซนเซอร์ของหุ่นจริง

### ฉาก 3D ชุด 5: ผ่าน browser QA และปรับ touch

- `client/robot-scene-3d.js`: ป้องกัน gesture สองนิ้วกลายเป็นคลิกเลือกจุดโดยไม่ตั้งใจ; `README.md`: อธิบายฉาก 3D และกล้อง/fallback ในทางเข้าหลัก
- Python targeted **37 passed, 1 warning** (Starlette/httpx เดิม); Node syntax และ audio-start/voice-lifecycle/metrics ผ่าน
- Chromium WebGL ผ่านหมุน/ซูม/คืนกล้อง, raycast เลือกและดับเบิลคลิกเดินไปฟิตเนส, F ตามระยะใกล้, Space หยุด, เสียงไมค์จำลองผ่าน voice iframe → fake provider → actual robot tool → arrived, context lost/restored, mobile 390px ไม่มีล้น และปิด WebGL แล้วยังใช้ Canvas สำรองสั่งกลับฐาน/หยุดได้ ไม่มี page error
- ตรวจภาพ desktop/mobile/กล้องใกล้แล้ว; รอตรวจ gesture หลังแก้และเปิดเซิร์ฟเวอร์ 8010 รุ่นล่าสุด ยังไม่เรียก provider จริงใน QA ชุดนี้

### ฉาก 3D ชุด 4: ตรวจภาพและเตรียม regression

- `client/robot-scene-3d.js`: หลังตรวจ screenshot WebGL จริง ปรับแสงให้อ่านสีเฟอร์นิเจอร์ง่ายขึ้น ขยายป้าย/ฉาก แก้ตำแหน่งลายน้ำ และให้ F เข้ามุมใกล้หุ่น
- `tests/test_robot_simulation.py`: ตรวจ SHA-256 ของ vendor ที่เสิร์ฟจริง, MIME ของ JS module, allowlist ป้องกัน path อื่น และ CSP ยังจำกัด self
- `docs/robot-simulator.md`: วิธีหมุน/เลื่อน/ซูม/touch/follow, แหล่งเอกสาร Three.js, version/license และ fallback
- Chromium เปิด WebGL ได้จริงและไม่มี JavaScript error ตรวจภาพรอบแรกแล้ว; Node syntax ผ่าน รอตรวจภาพล่าสุดและทดสอบ navigation/voice/fallback

### ฉาก 3D ชุด 3: เชื่อมหน้าเดียวกับ Emma

- `client/robot-simulator.js`: เปิด renderer 3D แบบ async แล้วใช้ callback/tool/state ชุดเดิม หาก module/WebGL เปิดไม่ได้ใช้ Canvas 2.5D สำรองพร้อมแจ้งผู้ใช้
- `client/robot-simulator.html`: สถานะ 3D และคำอธิบายการหมุน/เลื่อน/เลือกจุด; `app/robot_simulator.py`: เสิร์ฟ module/ไฟล์ Three.js แบบ allowlist ภายใน origin เดิม โดยไม่ขยาย CSP
- รอตรวจ syntax, API allowlist, browser WebGL/fallback และการเดินทาง; ไม่เปลี่ยน voice client หรือ backend การเดินหุ่น

### ฉาก 3D ชุด 2: geometry และกล้อง

- เพิ่ม `client/robot-scene-3d.js`: ฉาก WebGL 2 จาก geometry จริงพร้อมแสงเงา เฟอร์นิเจอร์ หุ่น astronaut หันตามตำแหน่งเซิร์ฟเวอร์ และเส้นทาง/POI/minimap จาก engine เดิม
- OrbitControls รองรับหมุน เลื่อน ซูม และ touch; raycast เลือกจุดหมาย, กล้องตามหุ่น, keyboard controls, จำกัด DPR/30 fps, หยุด render เมื่อซ่อนหน้า และ dispose เมื่อออกจากหน้า
- แสดงสถานะ context loss และ reload เมื่อคืนหน้าจาก BFcache; ยังไม่เชื่อมหน้า UI และรอตรวจ browser/คำสั่งจริงในตัวจำลอง

## 2026-09-08 — ฉาก 3D ชุด 1: dependency ภายในโปรเจกต์

- เพิ่ม `client/vendor/three/`: Three.js 0.180.0 จาก tag r180 ของ mrdoob/three.js พร้อม MIT LICENSE และ manifest SHA-256; แก้เฉพาะ import ของ OrbitControls ให้ชี้ module ภายใน ไม่ใช้ CDN ขณะเปิดฉาก
- ดาวน์โหลดครบก่อนเขียน ตรวจ version จาก upstream package.json แล้ว; sandbox ปฏิเสธ network จึงดาวน์โหลดด้วยสิทธิ์เครื่องมือที่อนุมัติ รอเชื่อมฉากและตรวจ browser/WebGL

## 2026-09-08 — ส่งมอบแผนที่และ Emma ในหน้าเดียว

- `README.md`: ปรับทางเข้าเป็นหน้าแผนที่พร้อมแผงเสียง อธิบาย standby/Start, readiness, ทดสอบลำโพงและส่งออกผลซ้อม ให้ตรงกับหน้าจอที่ตรวจแล้ว
- Full suite: **1,151 passed, 1 warning ใน 163.46 วินาที**, exit code 0; ยังมี warning เดิม Starlette TestClient/httpx และข้อความ pending IocpProactor/Event loop closed ตอน teardown ของ fake upstream บน Windows
- Node audio-start, metrics/syntax และ voice-lifecycle ผ่านหลังชุด 7; ตรวจ Chromium desktop/mobile ผ่านตามชุด 6 รวม wake fixture → fake provider → actual robot tool, การถือไมค์เพียงแท็บเดียวและรับช่วงหลังปิดแท็บแรก, JSON export v2 และไม่มี JavaScript error
- เปิดเซิร์ฟเวอร์ตัวจำลองรุ่นล่าสุดพอร์ต 8010 นอก sandbox หลังตรวจว่าไม่มีสายหรืองานเดินค้าง และปิด QA พอร์ต 8011; ตรวจ `/health` ว่า wake พร้อม และทดสอบ `/ws` กับ Gemini จริงได้ ready พร้อม diagnostic session ID โดยไม่ส่งเสียงไมค์ จากนั้น `/api/diagnostics` มี session_start/voice_ready/session_end และ provider กลับ idle
- ข้อมูล diagnostics เก็บ 800 เหตุการณ์ใน RAM และส่งออกเองได้ ไม่มี transcript หรือ raw audio ใน metrics; เวลา latency วัดการรับเสียงจาก provider ไม่ใช่การยืนยันว่าเสียงออกลำโพงผู้ใช้ ทดสอบลำโพงจริงได้ด้วยปุ่มบนหน้าใหม่
- ไม่แก้ `.env` หรือ API key, ไม่รีสตาร์ท Emma หลัก และยังไม่มีการทดสอบกับหุ่นจริง; บันทึกนี้รวมผลตรวจที่เคยระบุรอในชุด 1–7

### ชุดหน้าเดียว — ชุด 7: timer และเสียงท้ายสายเก่า

- `client/index.html`: ไม่ให้ timer standby เก่าเปิดไมค์ระหว่างสายใหม่, ไม่ให้ onended ของเสียงจาก context เก่าเปลี่ยนสถานะสายใหม่ และให้ gesture ที่ค้างจาก context ปิดแล้วจบโดยไม่เกิด unhandled rejection
- `tests/client_audio_start.cjs`, `tests/client_voice_lifecycle.cjs`: เพิ่มกรณี callback เสียงเก่าและ timer standby ระหว่าง active call
- รอตรวจ Node/regression รอบสุดท้าย; full suite กำลังรัน และยังไม่เปลี่ยนเซิร์ฟเวอร์ที่ผู้ใช้เปิดอยู่

### ชุดหน้าเดียว — ชุด 6: ตรวจรวมและคู่มือ

- `app/robot_diagnostics.py`: เก็บ outcome ของ tool แบบ allowlist และตรวจชนิด error category; `client/simulator-voice.css`: ซ่อนข้อความตกแต่งของหน้าเดิมใน embedded view
- `docs/robot-simulator.md`: วิธีใช้หน้าเดียว/readiness/test-speaker, Web Locks และข้อจำกัดข้าม origin, RAM retention/export v2 และเวลาที่วัดได้จริง
- ผลตรวจ: Python targeted **262 passed, 1 warning**; Node audio-start/metrics/lifecycle ผ่าน; Chromium หน้าเดียวปลุกจาก fixture → fake provider → actual robot tool ผ่าน ไม่มี JS error, root ไม่เปิดไมค์, แท็บที่สองไม่เปิดไมค์และรับช่วงได้หลังปิดแท็บแรก, export มี session/metrics และมือถือไม่ล้นแนวนอน
- ตรวจภาพ desktop/mobile แล้ว; QA แรก assertion นับ iframe ทั้งหมดผิดเพราะ client เดิมมี iframe สำหรับสื่ออยู่แล้ว จึงตรวจจำนวนการเปิดไมค์จริงแทน ผ่านโดยไม่เปลี่ยน runtime จากปัญหา harness
- รอ full suite และเปิด server รุ่นล่าสุด; ยังไม่เรียก provider จริงในชุดทดสอบนี้

### ชุดหน้าเดียว — ชุด 5: privacy และ lifecycle regression

- เพิ่ม `tests/test_robot_diagnostics.py`: correlation/session/turn/command, ไม่เก็บข้อความส่วนตัว, bounded/reset/stale session, origin/cookie/schema และ readiness ไม่เชื่อค่าจาก client
- เพิ่ม `tests/client_voice_lifecycle.cjs`, `.github/workflows/test.yml`: ทดสอบ callback/timer ของสายเก่า, standby resume ค้างและไมค์เปิดเสร็จหลังถูกยกเลิก
- `tests/test_voice.py`: regression เดิมยังบังคับ teardown ก่อน branch ของสายปัจจุบัน แต่ยกเว้น guard ของ callback สายเก่า
- ผลก่อนชุดนี้: Node audio-start/metrics/syntax ผ่าน; Python targeted **258 passed / 1 failed** จาก source assertion ที่ยังไม่รู้จัก ownership guard รอตรวจซ้ำ

### ชุดหน้าเดียว — ชุด 4: แผนที่และ Emma ในจอเดียว

- `client/robot-simulator.html`, `client/robot-simulator.css`: วางแผนที่คู่แผงเสียงเดิมผ่าน same-origin iframe, ย้ายปุ่มทดสอบเป็นแผงพับได้, แสดง readiness และผลซ้อมเสียง
- `client/robot-simulator.js`: อ่านสถานะจาก iframe ที่ตรวจ origin/source, แสดง latency/เหตุการณ์ และ export รูปแบบ v2 รวม metrics จำลอง โดยตรวจ epoch ตรงกัน
- `app/robot_simulator.py`: export มี epoch และปฏิเสธ reset ระหว่างมีสายเพื่อไม่ทำให้ข้อมูลวัดข้ามรอบ
- รอตรวจ API/browser/privacy และภาพ desktop/mobile ยังไม่เปิด provider จริงในรอบพัฒนา

### ชุดหน้าเดียว — ชุด 3: เก็บ metrics แยกและ readiness ฝั่ง server

- เพิ่ม `app/robot_diagnostics.py`: เก็บ 800 เหตุการณ์ใน RAM แบบ allowlist/จำกัดค่า ไม่มี transcript, raw audio, exception text หรือ secret; เชื่อม session/turn/command และสรุป p50/p95 ของเวลารับเสียง provider
- `app/robot_simulation.py`, `app/turnlog.py`: ส่งเฉพาะ observations ที่อนุญาตเข้า sink จำลองแทน visitor log และ reset/export แยกชัดเจน
- `app/session.py`: บันทึก ready/error category และส่ง diagnostic session ID ให้ client จำลอง
- `app/robot_simulator.py`: เพิ่ม export/readiness และ cookie/origin-guarded browser diagnostics, อนุญาต embed หน้าเสียงเฉพาะ same origin
- รอ API/privacy/regression tests และรวมหน้าจอ; ตัวเลข latency ไม่ใช่หลักฐานว่าเสียงดังออกลำโพง

### ชุดหน้าเดียว — ชุด 2: ตัวเชื่อมเสียงและสถานะอุปกรณ์

- เพิ่ม `client/simulator-voice.js`, `client/simulator-voice.css`: ใช้ voice client เดิม, Web Locks เลือกเจ้าของไมค์ใน origin เดียว, readiness แบบแยกชั้น, ปุ่มทดสอบลำโพงในเครื่อง และเตรียม telemetry แบบไม่มีข้อความสนทนา
- `client/index.html`, `app/robot_simulator.py`: hook ช่วงต่อสายและเสิร์ฟไฟล์เฉพาะ simulator; รองรับ embedded layout โดยไม่เปิด mic client เพิ่ม
- รอ endpoint diagnostics/หน้าแผนที่และการทดสอบครบก่อนเปิด server รุ่นใหม่

## 2026-09-08 — ชุดเสียงเสถียร/หน้าเดียว/ผลซ้อม: ชุด 1

- `client/index.html`: guard socket message/error/close/timer ตามเจ้าของสาย, กันผลเปิดไมค์/worklet standby ที่ล่าช้า และติดตั้ง unlock ก่อน resume; แยกการปิด standby ระหว่าง handover กับการหลุดจริง
- เตรียม hook โหลด `simulator-voice.js` เฉพาะโหมดจำลองสำหรับเจ้าของเสียง/readiness/telemetry; ไฟล์ต่อเชื่อมจะเพิ่มในชุดถัดไป
- รอทดสอบหลังต่อครบ ไม่รีสตาร์ทเซิร์ฟเวอร์ระหว่างชุดแก้ไข

## 2026-09-08 — Research งานถัดไปหลังทดสอบ Emma กับหุ่นจำลอง

- เพิ่ม `docs/research/emma-robot-next-steps-2026-09-08.md`: เทียบสิ่งที่ทำแล้วกับ roadmap เดิม จัดลำดับ 8 งาน พร้อมหลักฐานโค้ด เกณฑ์รับงาน และเอกสารปฐมภูมิ 9 แหล่ง
- พบลำดับ wake resume ก่อน unlock, callback socket เก่าที่เรียก shared teardown, ช่องว่าง readiness/metrics จำลอง และความเสี่ยงคืนฉากผ่าน bfcache; แยกข้อที่ทำซ้ำได้จากข้อเสนอ/ความเสี่ยงที่ยังต้องทดสอบ
- ผลตรวจ: Node VM ทำซ้ำ late close ได้ และตรวจลำดับ wake resume/unlock ตรงกับรายงาน; ยังไม่ได้แก้ข้อค้นพบใหม่ ไม่เปิดไมค์/paid API/ฮาร์ดแวร์หรือรีสตาร์ทเซิร์ฟเวอร์
- ตรวจแล้ว: ลิงก์แหล่งข้อมูลที่ไม่ซ้ำ 9 URL, relative file link resolve ได้, UTF-8/Markdown fences และ `git diff --check` ผ่าน; งานรอบนี้เพิ่มเอกสารเท่านั้น ไม่รัน unit suite ซ้ำ

## 2026-09-08 — ส่งมอบการแก้เสียงทักทายต้นสาย

- Node regression ผ่าน: PCM แรกระหว่างรอไมค์, ปฏิเสธไมค์, suspended playback, reuse wake context/stream, sample rate และ stale permission cleanup; client metrics/syntax ผ่าน
- Chromium ผ่านด้วย Web Audio จริงและ WebSocket ที่จำลองในเบราว์เซอร์: รับ PCM ทักทาย 24,000 samples และ schedule ขณะ context running ก่อน getUserMedia ที่หน่วง 1.8 วินาทีเสร็จ ไม่มี JavaScript error; ไม่ใช้ provider หรือไมค์จริงของผู้ใช้
- Python regression ที่เกี่ยวข้อง: **224 passed, 1 warning ใน 14.66 วินาที**; `git diff --check` ผ่าน
- HTML เสิร์ฟจากไฟล์ปัจจุบัน ผู้ใช้กด Ctrl+F5 ที่หน้าเสียงเพื่อโหลดการแก้ไข ไม่ต้องรีสตาร์ทเซิร์ฟเวอร์หรือเปลี่ยน `.env`; ผลนี้ยืนยันเส้นทางเล่นเสียงในซอฟต์แวร์ ไม่ใช่การวัดเสียงจากลำโพงผู้ใช้

### เสียงประโยคแรก — ชุด 3: ไมค์ถูกปฏิเสธและ CI

- `tests/client_audio_start.cjs`: เพิ่มกรณีปฏิเสธไมค์แต่ยังเล่นเสียงได้
- `tests/test_greeter.py`: ยอมให้ return เฉพาะสายที่จบขณะรอสิทธิ์ไมค์ โดยยังห้าม return จากการปฏิเสธไมค์ของสายปัจจุบัน
- `.github/workflows/test.yml`: รัน regression เสียงประโยคแรกใน CI
- ผลก่อนชุดนี้: Node audio-start และ metrics/syntax ผ่าน; Python targeted 223 passed / 1 failed เพราะ assertion เดิมห้าม return ทุกกรณีรวม guard สายเก่า รอตรวจซ้ำและ browser QA

### เสียงประโยคแรก — ชุด 2: regression ที่ทำให้เกิด race ซ้ำได้

- `tests/client_audio_start.cjs`: จำลองไมค์เปิดช้าแล้วส่ง PCM ทักทายทันที ตรวจไม่ถูกทิ้ง, suspended context ไม่ขวางการเตรียมไมค์, unlock ถูกลงทะเบียนก่อน resume, ใช้ context/ไมค์เดิมจาก wake, sampling rate และปล่อยไมค์เมื่อสายจบก่อน permission เสร็จ
- ผลตรวจ: รอรัน Node และ Python regression; ไม่ใช้ไมค์หรือ API จริงในการทดสอบชุดนี้

## 2026-09-08 — เก็บเสียงประโยคแรกของ Emma (ชุด 1)

- `client/index.html`: สร้าง playback context ทันทีที่รับ ready ก่อน await เปิดไมค์ เพื่อไม่ทิ้ง PCM ทักทายที่มาถึงเร็ว; โหมดเรียกชื่อรับ context ที่เปิดเสียงแล้วจาก standby และคง output sample rate ของแต่ละ buffer
- ลงทะเบียน gesture unlock ก่อนเรียก resume ที่อาจรอ gesture ไม่เสร็จ; ป้องกันผลเปิดไมค์/worklet ของสายเก่ามาแทรกสายใหม่ และไม่เปลี่ยนเป็น listening ทับ greeting ที่กำลังเล่น
- เหตุผลจากโค้ด: เดิม playChunk คืนทันทีเมื่อ audioCtx ยังไม่มี แต่ startMic สร้าง context หลัง await getUserMedia; รอ regression/browser QA ยังไม่ยืนยันลำโพงผู้ใช้

## 2026-09-08 — แก้การเปิด Gemini ถูก sandbox ปฏิเสธ

- ตรวจ traceback แล้ว WinError 5 เกิดใน Windows socket connect ของโปรเซสที่รันภายใต้ข้อจำกัดเครือข่าย ไม่ใช่ข้อผิดพลาดจากไมค์หรือการเลือกเครื่องมือ
- ทดสอบ `_connect()` ของ GeminiProvider ด้วย backend จำลอง: ภายใน sandbox ได้ PermissionError; เมื่อรันนอก sandbox โดยผ่านการอนุมัติของเครื่องมือ ได้ **LIVE_HANDSHAKE_OK** แล้วปิดสาย โดยไม่ส่งเสียงไมค์หรือข้อความสนทนา
- `docs/robot-simulator.md`: เพิ่มแนวทางตรวจข้อจำกัดของโปรเซสและเปิด launcher จาก Windows ตามปกติ พร้อมรีเฟรช cookie หลังรีสตาร์ท
- เปลี่ยนโปรเซสพอร์ต 8010 ให้รันนอก sandbox แล้ว ตรวจผ่านเซิร์ฟเวอร์จริง: health wake พร้อม, `/ws/wake` ตอบ wake_listening และ `/ws` เชื่อม Gemini จริงตอบ ready พร้อม robot_simulator=true แล้วปิดสายทดสอบ ไม่ส่งเสียงไมค์ผู้ใช้; เส้นทาง `/ws` ใช้ขั้นตอน greeting ตามปกติ
- จัดย่อหน้าการแก้ WinError 5 ใน `docs/robot-simulator.md` ไว้ส่วนเปิดใช้งาน; ไม่เปลี่ยน API key, `.env`, firewall หรือโปรแกรม Emma หลัก ไม่มีการแก้โค้ดและไม่รัน unit suite ซ้ำสำหรับการเปลี่ยนสิทธิ์โปรเซส

## 2026-09-08 — ส่งมอบการเรียกชื่อ Emma ในตัวจำลอง

- Full suite: **1,148 passed, 1 warning ใน 152.30 วินาที**; เพิ่ม 5 กรณีจากฐาน 1,143 มี warning เดิม Starlette TestClient/httpx และข้อความ pending IocpProactor ตอน teardown บน Windows แต่ exit code 0
- ตรวจเบราว์เซอร์ครบจากไมค์ fixture/โมเดล wake จริงจน actual robot tool ผ่าน provider fake โดยไม่กด Start; ไม่ได้วัดความแม่นยำของเสียงผู้ใช้หรือเรียก provider จริงระหว่าง QA
- ตรวจว่าไม่มีสายเสียงหรืองานเดินค้างก่อนรีสตาร์ทเฉพาะตัวจำลอง; เซิร์ฟเวอร์ล่าสุดพอร์ต 8010 ตอบ `wake.ready=true`, `standby=true`, `auto_connect=false` แล้ว ปิด QA พอร์ต 8011
- ผู้ใช้รีเฟรช `/voice` แล้วเรียกชื่อได้เมื่ออนุญาตไมค์และระบบเสียงของเบราว์เซอร์ทำงาน; `git diff --check` ผ่าน ไม่แก้ `.env` หรือรีสตาร์ท Emma หลัก

### เรียก Emma ด้วยชื่อ — ชุด 4: ตรวจเบราว์เซอร์และคู่มือ

- `docs/robot-simulator.md`: ปรับวิธีเริ่มจากเรียกชื่อ, สิทธิ์ไมค์/ปลดล็อกเสียง, fallback ปุ่ม, กลับรอฟังหลังจบสาย และรีเฟรชหลังรีสตาร์ท
- Targeted tests: **60 passed, 1 warning ใน 20.35 วินาที**
- Chromium QA ผ่านโดยไม่คลิก Start: fixture ไมค์ → AudioWorklet → โมเดล wake จริง → wake → voice PCM → provider fake → actual robot tool (`hardware=simulated`); ไม่มี JavaScript error และไม่ใช้ provider จริง
- QA ครั้งแรก provider fake ไม่ได้ load_tools ตามที่ provider จริงทำ จึงพบ unknown tool; แก้ harness ชั่วคราวให้โหลดเครื่องมือแล้วตรวจผ่าน ไม่ได้แก้ production tools จากปัญหา harness นี้
- รอ full suite และรีสตาร์ทตัวจำลองพอร์ต 8010

### เรียก Emma ด้วยชื่อ — ชุด 3: ปรับ fixture regression

- `tests/test_wake.py`: source assertion ตรวจ guard ใหม่ที่ยังบังคับเปิด debug/enroll และเติม diagnostics=True ให้ fixture ที่สร้าง stream ผ่าน `__new__` โดยข้าม constructor
- ผลรอบแรก: 58 passed / 2 failed จาก assertion ของ guard เดิมและ fixture ขาด attribute ใหม่; โมเดลจริงในกรณี diagnostics=False และ protocol wake ผ่าน รอตรวจซ้ำ

### เรียก Emma ด้วยชื่อ — ชุด 2: ตรวจ wake และส่งต่อเสียง

- `tests/test_robot_voice.py`: ตรวจ health/PCM/wake/handover ไป actual robot tool, ไม่เปิด provider ก่อน wake, fallback ปุ่มเมื่อปิดหรือไม่มีโมเดล และ origin/cookie ของ standby
- `tests/test_wake.py`: ใช้โมเดลจริงกับเสียง fixture ยืนยัน diagnostics=False ยังได้ยินชื่อ แม้เครื่องเปิด debug/enroll และไม่บันทึกคลิป
- ผลตรวจ: รอ targeted tests; fixture ของการทดสอบเสียงเดิมปิด wake อย่างชัดเจน แทนการอิงค่า health ที่เคยบังคับปิด

## 2026-09-08 — เรียก Emma ด้วยชื่อในตัวจำลอง (ชุด 1)

- `app/robot_simulator.py`: เปิด health wake ตาม WAKE_ENABLED/โมเดลเดิม เพิ่ม `/ws/wake` ใช้ detector ในเครื่องและ origin/cookie เดียวกับเสียง ส่ง wake ให้ client เดิมเปิดสายเมื่อได้ยินชื่อ ไม่เชื่อม provider ระหว่าง standby
- `app/wake.py`: เพิ่มตัวเลือก diagnostics ต่อ stream (ค่าเริ่มต้นคงพฤติกรรมเดิม) เพื่อปิดการเก็บคลิป debug/enrollment เฉพาะตัวจำลอง ไม่ลงทะเบียนรับการเรียกจาก reminder/camera จริง
- เหตุผล: ผู้ใช้ต้องการเรียกชื่อได้เหมือน Emma เดิม แทนการบังคับกดเริ่มคุย; ตรวจพบ WAKE_ENABLED เปิดและมีโมเดล/sherpa ในเครื่องแล้ว
- ผลตรวจ: รอ tests และ browser QA; ยังไม่เปลี่ยน `.env` หรือรีสตาร์ทเซิร์ฟเวอร์

## 2026-09-08 — เปิดตัวจำลองซ้ำโดยไม่ชนพอร์ต

- `start-robot-simulator.cmd`: ตรวจ `/health` ว่าเป็น robot simulator ที่ทำงานอยู่ก่อนเริ่มเซิร์ฟเวอร์ ถ้าพบให้เปิดหน้าเว็บเดิมแล้วจบตัวเปิด ป้องกัน WinError 10048 เมื่อดับเบิลคลิกซ้ำ
- `docs/robot-simulator.md`: อธิบายการเปิดซ้ำและวิธีใช้พอร์ตอื่นเมื่อมีโปรแกรมอื่นครอบครองพอร์ต
- ผลตรวจ: localhost:8010 ตอบ health `ok=true`, `robot_simulator=true`; รันตัวเปิดจริงแล้วเข้าแขนง `already running` และเรียกเปิดเบราว์เซอร์โดยไม่เริ่ม uvicorn ซ้ำ; หน้า Emma World ตอบ HTTP 200 และ `git diff --check` ผ่าน ไม่หยุดเซิร์ฟเวอร์หรือรีเซ็ตงานเดิม

## 2026-09-08 — ส่งมอบแผนที่เกม Emma World

- เปลี่ยนฉากเป็น Canvas isometric 2.5D พร้อมเฟอร์นิเจอร์ หุ่น astronaut เส้นทางตามทางเดิน mission progress แผนที่ย่อ และกล้องซูม/ลาก/ติดตาม/เต็มจอ; ใช้ backend คำสั่งเดียวกับ Emma
- Full suite: **1,143 passed, 1 warning ใน 143.57 วินาที** (`.pytest_robot_game_full`); warning เดิม Starlette TestClient/httpx deprecation
- ตรวจ Chromium desktop/mobile และ interaction ผ่านตามชุด 5; JavaScript syntax 2 ไฟล์ผ่าน; ตรวจภาพจริงแล้ว และ audit working tree 65 ไฟล์มีชื่อใน CHANGELOG ครบ
- เปิดเซิร์ฟเวอร์จำลองล่าสุดที่ `http://127.0.0.1:8010`; ผู้เปิดหน้าเก่ากด Ctrl+F5 แล้วใช้ฉากใหม่ได้ทันที; หลัง QA คืนสถานะ idle
- แผนที่และเฟอร์นิเจอร์ยังเป็นข้อมูลสมมติ ไม่ยืนยัน physics/SLAM/การหลบสิ่งกีดขวางจริง; รอบนี้ไม่ทดสอบเสียงกับ provider จริง ไม่แก้ `.env` หรือสั่งฮาร์ดแวร์

### แผนที่เกม — ชุด 5b: ชื่อปุ่มในคู่มือ

- `docs/robot-simulator.md`: ปรับชื่อปุ่มกล้องและเริ่มเดินทางให้ตรงกับหน้า HTML หลังตรวจเทียบ
- ผลตรวจ: `git diff --check` ผ่าน; audit 65 ไฟล์ใน working tree มีชื่อในประวัติครบ; full suite ยังรันอยู่

### แผนที่เกม — ชุด 5: คู่มือและตรวจการใช้งาน

- `docs/robot-simulator.md`: เพิ่มวิธีใช้ฉาก Emma World, คลิก/สัมผัส/คีย์บอร์ด กล้อง แผนที่ย่อ และขอบเขตเส้นทางสมมติ พร้อมแก้คำอธิบายสิ่งกีดขวางจากเส้นตรงเป็นระยะทางตาม route
- ผลตรวจ: targeted robot/voice tests **55 passed, 1 warning**; JavaScript syntax ผ่าน 2 ไฟล์
- Chromium desktop: zoom/pan/follow/fullscreen, เลือกจุด/ดับเบิลคลิก, Space หยุด, arrival/progress, obstacle และ reset ผ่าน; mobile 390px แตะเลือกจุดได้และไม่มี horizontal overflow; ไม่มี JavaScript error ตรวจภาพ desktop/mobile แล้ว
- Browser QA รอบแรกอ่านสถานะ fullscreen ก่อน promise จบ จึงปรับเวลาอ่านในการตรวจและผ่าน โดยไม่ได้แก้พฤติกรรมโปรแกรม; ทดสอบด้วยโลกจำลอง ไม่เปิดไมค์หรือ provider จริง
- รอ full suite รอบสุดท้าย; คืน simulator เป็น idle หลังทดสอบ

### แผนที่เกม — ชุด 4b: จุดปลายเส้นทาง

- `app/robot_simulation.py`: คืนพิกัดต้น/ปลายตรงเมื่อ progress ถึง 0/1 แก้เศษ floating point ที่พบจากการทดสอบเส้นทางทุกคู่จุด
- ผลตรวจรอบแรก: JavaScript syntax ผ่านทั้ง 2 ไฟล์; targeted 54 passed / 1 failed จาก 1.5000000000000004 เทียบ 1.5 จะแก้และตรวจซ้ำ

### แผนที่เกม — ชุด 4: เชื่อมฉากกับคำสั่งจริงของตัวจำลอง

- `client/robot-simulator.js`: ส่ง state/route/progress ให้ renderer, คลิกเลือก POI รวมฐานชาร์จ, ดับเบิลคลิกและ Space เรียกเครื่องมือเดิม, ปุ่ม zoom/center/follow/fullscreen และ mission HUD
- จุดจากหน้าเกมและคำสั่งเสียงใช้ backend เดียวกัน ไม่ทำให้ตัวละครเดินเองโดยไม่ผ่าน engine; การเริ่มเดินทางไม่เท่ากับแจ้งถึง
- ผลตรวจ: รอ syntax/unit/browser QA บน server เวอร์ชันใหม่

### แผนที่เกม — ชุด 3b: รูปแบบหน้าจอ

- `client/robot-simulator.css`: โทน navy/mint ฉากใหญ่พร้อม HUD กล้องและแผนที่ย่อ ปรับหน้าจอมือถือ/เต็มจอและ reduced-motion
- ผลตรวจ: รอ browser render หลังเชื่อม state ไม่เพิ่ม assets ภายนอก

### แผนที่เกม — ชุด 3: โครงหน้าจอ Emma World

- `client/robot-simulator.html`: แทน SVG ด้วยฉาก Canvas มีปุ่มกล้อง mission HUD แผนที่ย่อและ progress พร้อมเก็บจุดสั่งงาน/เสียง/fault/journal เดิม
- ผลตรวจ: รอ stylesheet และผูก state; patch แรกถูกปฏิเสธเพราะมีหลาย operation ต่อ CSS เดียว จึงแยก patch ตามไฟล์ โดยไม่มีการแก้บางส่วนจาก patch ที่ล้มเหลว

### แผนที่เกม — ชุด 2: วาดฉากและกล้อง

- เพิ่ม `client/robot-scene.js`: Canvas 2.5D แบบ isometric พร้อมห้องตัวอย่าง/โซฟา/เตียง/โต๊ะ/ฟิตเนส/สระน้ำเคลื่อนไหว/ต้นไม้/ฐานชาร์จ หุ่น astronaut เคลื่อนไหวตามข้อมูล server, ทางเดินและแผนที่ย่อ
- รองรับลากฉาก ซูม ติดตามหุ่น คลิกเลือก/ดับเบิลคลิกเดินทาง คีย์บอร์ดเฉพาะเมื่อ canvas โฟกัส และ reduced-motion; ไม่มี CDN หรือ raster assets เพิ่มเติม
- ผลตรวจ: รอเชื่อมหน้าจอและทดสอบ browser; รูปห้อง/เฟอร์นิเจอร์เป็นภาพประกอบสมมติ ไม่ใช่แบบโครงการหรือ collision geometry จริง

## 2026-09-08 — แผนที่เกม 2.5D (ชุด 1)

- `app/robot_simulation.py`: เปลี่ยนการเคลื่อนที่สมมติเป็นเส้นทางตาม corridor/ทางเข้าห้อง มี route/progress ส่งให้หน้าจอ ยังคง command/ACK/arrival และ Emma interface เดิม; ไม่ใช่ SLAM หรือ physics หุ่นจริง
- `app/robot_simulator.py`: เตรียม route เสิร์ฟ `client/robot-scene.js` ในเครื่อง ไม่เพิ่ม CDN/dependency
- `tests/test_robot_simulation.py`: ตรวจเส้นทางทุกคู่จุดหมายและการเดินต่อหลังหยุดกลางทางว่าใช้ทางเดิน/ไม่วิ่งทแยงผ่านห้อง
- ผลตรวจ: รอ renderer/UI และ tests; ทุกจุด/ผังยังเป็นข้อมูลจำลอง ไม่แก้แผนที่หุ่นจริงหรือ `.env`

## 2026-09-08 — ส่งมอบ Emma สั่งหุ่นจำลองผ่านเสียง

- Full suite โค้ดล่าสุด: **1,142 passed, 1 warning ใน 155.69 วินาที**; เพิ่ม voice tests 9 กรณีจากฐาน 1,133
- ชุด voice/provider/review/metrics หลังแก้ regression ผ่าน **202 passed**; voice tests หลังเพิ่มกรณี failure/stale/rejected ผ่าน **9 passed** ก่อน full suite รอบสุดท้าย
- Chromium browser QA: ใช้ไมค์จำลองและ upstream fake ตรวจ client AudioWorklet/PCM, WebSocket เดิม, actual robot tool, audio response และ arrival กลับ Emma ผ่าน; ตรวจหน้า desktop/mobile และข้อความแยก ACK จาก arrival ผ่าน ไม่มี JavaScript error
- JavaScript syntax ใน `client/index.html`, Python AST 10 ไฟล์ และ `git diff --check` ผ่าน; audit working tree 64 ไฟล์รวมงานเดิม มีชื่อในประวัติครบ
- เปิดโค้ดล่าสุดที่ `http://127.0.0.1:8010/voice` พร้อมปุ่มจากแผนที่; ตรวจหน้าใช้งานจริงโดยไม่กดเริ่มคุย: Gemini ตั้ง API key แล้ว, auto-connect ปิด, ไม่มี WebSocket เสียงหรือการเปิดไมค์อัตโนมัติ
- ปิดเซิร์ฟเวอร์ QA upstream fake พอร์ต 8011 แล้ว เหลือเซิร์ฟเวอร์ตัวจำลองพอร์ต 8010; รีสตาร์ทเฉพาะตัวจำลอง ไม่แก้ `.env`, ไม่รีสตาร์ท Emma หลัก, ไม่ commit/push
- เสียงจริงต้องให้ผู้ใช้กดเริ่มและอนุญาตไมค์เอง ใช้ API/voice configuration เดิม; รอบนี้ไม่ได้ส่งเสียงไป provider จริงหรือทดสอบความแม่นยำการฟังคำพูดของผู้ใช้
- คำเตือนเดิม Starlette TestClient/httpx deprecation และ teardown ของ Windows fake-upstream บางชุดไม่ทำให้ tests fail

### Emma สั่งตัวจำลอง — ชุด 6: ผลเดินทางที่ยังเกี่ยวข้อง

- `app/robot_voice.py`: คำสั่งใหม่ที่ถูกปฏิเสธไม่ควรกลบรายงาน arrival ของงานเดิมที่สำเร็จ จึงเลือกคำสั่งล่าสุดที่มีผลจริงในโลกจำลองเมื่อกรองประกาศค้าง
- `tests/test_robot_voice.py`: เพิ่มผลนำทางล้มเหลวผ่าน WebSocket และการกรองประกาศหลัง reset/stop/คำสั่งใหม่ที่ถูกปฏิเสธ
- ผลตรวจ: รอ targeted tests และ full suite รอบสุดท้ายหลังแก้ regression; ไม่มีการเปลี่ยนสถานะหรือสั่งงานหุ่นจริง

### Emma สั่งตัวจำลอง — ชุด 5: แก้ข้อผิดพลาดจาก regression

- `app/session.py`: คงเงื่อนไขเริ่ม Canva เดิมไว้ แล้วตรวจ context จำลองชั้นใน เพื่อผ่าน regression เดิมที่ตรวจ source text พร้อมคงพฤติกรรมไม่เริ่ม Canva ระหว่างซ้อม
- `tests/test_robot_voice.py`: อ่าน Gemini config เป็น dict ตาม API ภายในจริง และใส่เครื่องมือทดสอบที่ลงทะเบียนแล้วแต่ไม่มี group เพื่อยืนยัน dispatch ปฏิเสธจริง ไม่ใช่ผ่านเพราะหาเครื่องมือไม่พบ
- ผลตรวจที่นำมาแก้: full suite รอบแรก **1 failed, 1,138 passed** (source-string assertion); targeted หลังเพิ่ม schema test **2 failed, 200 passed** (เพิ่ม test ใช้ attribute ผิดกับ dict) ไม่ใช่การเชื่อม API หรืออุปกรณ์จริงล้มเหลว; รอตรวจใหม่

### Emma สั่งตัวจำลอง — ชุด 4: ตรวจเบราว์เซอร์และคู่มือ

- `app/providers/gemini.py`, `tests/test_robot_voice.py`: ปิด native Google Search เฉพาะ context จำลอง แม้ WEB_SEARCH เปิดในเครื่อง และตรวจ schema ของทั้ง Gemini/OpenAI เหลือเฉพาะ 4 robot tools
- `client/index.html`: แสดง “รับคำสั่งจำลองแล้ว · รอผลเดินทาง” แทน “สำเร็จ” ตอน tool เริ่มนำทาง ไม่ทำให้ ACK ดูเป็น arrival
- `docs/robot-simulator.md`, `README.md`: วิธีคุยผ่าน `/voice`, API quota, กดเริ่มก่อน/wake word/half-duplex, ขอบเขตเครื่องมือและผลทดสอบเสียง พร้อมแก้คำอธิบายเก่าที่ว่าไม่มี VoiceSession
- ผลตรวจก่อน guard/schema test เพิ่ม: voice tests **6 passed, 1 warning**; browser Chromium ผ่านด้วยไมค์จำลองและ upstream fake: AudioWorklet → WebSocket เดิม → actual tool → engine → arrival กลับหน้า Emma; ตรวจภาพ desktop/mobile ไม่มี JS error
- ยังไม่ใช้ไมค์ผู้ใช้หรือเรียกบริการเสียงจริง; full suite อยู่ระหว่างรัน และจะตรวจ provider guard/ข้อความล่าสุดซ้ำ

- ตรวจชุดทดสอบเสียงก่อนรัน: ลบ expression ที่ไม่ทำงานใน `tests/test_robot_voice.py`; ใช้ coroutine ส่ง callback เข้า event loop ของ TestClient โดยตรง (ผลทดสอบรอตรวจ)

### Emma สั่งตัวจำลอง — ชุด 3: ทดสอบเสียงและ session isolation

- เพิ่ม `tests/test_robot_voice.py`: upstream fake รับ PCM ผ่าน WebSocket เดิม แล้วเรียก robot tool จริง/ส่งเสียงและ arrival กลับ; ตรวจ origin/cookie, หนึ่งเซสชัน, หยุดเมื่อปิดเสียง, การแยกข้อมูลส่วนตัว/log และ stale announcement หลัง reset
- `app/session.py`: mirror ผล voice tools ลง journal จำลอง, งด nudge สไลด์ และให้เซสชันจำลองใช้ผู้ควบคุมหนึ่งคนแม้เครื่องเดิมตั้ง MULTI_SESSION
- `start-robot-simulator.cmd`: แก้ข้อความเปิดโปรแกรมให้ตรงว่าเสียง Emma เป็นตัวเลือกและใช้ API เดิม
- ผลตรวจระหว่างพัฒนา: robot/simulator/review เดิม **74 passed, 1 warning**; tests เสียงใหม่รอตรวจ ไม่มีการส่ง PCM ไป provider จริง

### Emma สั่งตัวจำลอง — ชุด 2: หน้าสนทนาและผลตอบรับ

- เพิ่ม `app/robot_voice.py`: แจ้ง arrival/failure/disconnect เข้าเสียง Emma ผ่านกลไกรอจบคำพูดเดิม กันแจ้งผลที่ค้างหลัง reset/เปลี่ยนคำสั่ง/เปลี่ยนเซสชัน
- `app/robot_simulator.py`: เพิ่ม `/voice`, `/voices`, `/health`, `/ws` เฉพาะเซิร์ฟเวอร์จำลอง ใช้ client/provider เดิม กดเริ่มก่อนเปิดเสียง; ตรวจ same origin + HttpOnly SameSite cookie, รับเสียงครั้งละหนึ่งเซสชัน และยกเลิกงานจำลองเมื่อจบเสียง
- `client/robot-simulator.html`, `client/robot-simulator.css`, `client/index.html`: ปุ่มเปิด Emma อีกแท็บ ป้ายบอกโหมดและการใช้ API เดิม ชื่อเครื่องมือหุ่น และซ่อนตัวสลับล่ามเฉพาะหน้าเสียงจำลอง
- `app/turnlog.py`: ย้าย docstring กลับตำแหน่งแรกตามรูปแบบ Python หลังเพิ่ม guard ในชุด 1
- ผลตรวจ: รอ fake-provider/WebSocket/browser tests; ยังไม่เรียก API เสียงจริงหรือเปิดไมค์ผู้ใช้

## 2026-09-08 — เชื่อมเสียง Emma กับหุ่นจำลอง (ชุด 1)

- `app/robot_backend.py`, `app/config.py`, `app/tools/registry.py`: จำกัดเซสชันจำลองเป็น robot tools 4 ตัว ทั้ง schema และ dispatch แม้ profile จริงเปิดเครื่องมืออื่น; context ไม่เปิดจากข้อความ client ของระบบหลัก
- `app/prompts.py`: คำสั่งสำหรับ Emma โหมดซ้อมโดยเฉพาะ ไม่อ่านข้อมูลส่วนตัว/เอกสาร/ข้อมูลขาย; ระบุเครื่องมือและให้รอผลเดินทางก่อนแจ้งว่าถึง
- `app/session.py`: อนุญาต Emma ใน context จำลองที่ server สร้าง, ส่งสถานะโหมด, เตรียม watcher ของผลจำลอง, งดติดตาม Canva และไม่รับ robot_ready/robot_arrived จาก client ในโหมดนี้
- `app/turnlog.py`: ไม่เขียน transcript/metrics จาก context จำลองปะปนกับบันทึกผู้ใช้งานจริง
- ผลตรวจ: รอทดสอบร่วมกับ voice endpoint และ watcher; ไม่เปลี่ยน `.env` หรือเปิด API เสียงจริงระหว่างพัฒนา

## 2026-09-08 — ผลส่งมอบตัวจำลองหุ่น

- Full suite บน `.venv-smoke`: **1,133 passed, 1 warning ใน 142.13 วินาที**; หลังปรับชื่อ event และระยะป้ายในชุด 6 ตรวจ robot/simulator ซ้ำ **45 passed, 1 warning ใน 2.49 วินาที**
- เพิ่ม automated tests 19 กรณี; คำเตือนเป็น Starlette TestClient/httpx deprecation เดิม ไม่มี test failure
- Playwright Chromium ตรวจ flow จริงทั้ง desktop/mobile รวม success, stop, home, unknown POI, duplicate, reconnect, obstacle timeout และ JSON download ผ่าน; ตรวจภาพหลังปรับป้ายแล้ว ป้ายฐานไม่ทับตัวหุ่น
- รอบตรวจสุดท้ายใช้ locator assertions แทน string-eval ของ Playwright ที่โดน CSP ปฏิเสธ คง CSP เดิมไว้และตรวจ command journal/stop/reset ผ่าน; JavaScript syntax และ Python AST ผ่าน
- `git diff --check` ผ่าน; audit ชื่อไฟล์ที่แก้/เพิ่มทั้ง working tree **61 ไฟล์** (รวมงานรอบก่อน) มีบันทึกครบ ไม่มีการ commit/push
- เปิดเซิร์ฟเวอร์จำลองโค้ดล่าสุดที่ `http://127.0.0.1:8010` และ reset กลับสถานะพร้อมซ้อม; รีสตาร์ทเฉพาะโปรเซสจำลองที่เปิดในงานนี้ ไม่แตะบริการเสียงเดิมหรือ `.env`
- ส่งมอบ `start-robot-simulator.cmd` และ `docs/robot-simulator.md`; ขอบเขตพร้อมใช้คือการซ้อมระดับคำสั่งผ่านปุ่ม/ข้อความ ไม่ใช่ speech recognition, vendor SDK runtime, Android APK หรือผลตรวจฮาร์ดแวร์จริง
- ถัดไปต้องใช้ AAR/demo ตรงรุ่นเพื่อสร้าง Android bridge/APK และทดสอบ SDK MOCK → REAL, map/POI, ไมค์/AEC และการหยุดบนหุ่นที่ส่งมาจริง

### ตัวจำลองหุ่น — ชุด 6: ผลตรวจหน้าจอและปรับระยะป้าย

- `client/robot-simulator.js`: ขยับป้ายจุดหมายให้พ้นตัวหุ่นเมื่อจอดทับจุด; ระบุชื่อเต็ม `client/robot-simulator.css` และ `client/robot-simulator.js` สำหรับ audit ประวัติ (เพิ่มในชุด 3)
- `app/robot_simulation.py`: เก็บชนิด event เป็น `command` แยกจาก `type: robot` ใน payload ของคำสั่ง ป้องกันการทับชื่อเหตุการณ์ใน journal
- ผลตรวจก่อนปรับสองจุดนี้: targeted robot/simulator/hardening/review **94 passed, 1 warning**; Playwright Chromium ผ่าน desktop 1440px/mobile 390px, ไปถึง/กลับฐาน/จุดไม่รู้จัก/หยุด/คำสั่งซ้ำ/reconnect/obstacle timeout/export JSON ไม่มี JavaScript error หรือ horizontal overflow; ตรวจภาพทั้งสองขนาดแล้ว
- Browser QA ครั้งแรกใช้ networkidle แล้ว timeout เพราะหน้า poll ทุก 250ms; เปลี่ยนเป็นรอ DOM และสถานะที่พร้อมใช้งานจริงแล้วผ่าน ไม่ใช่ server ล้มเหลว
- Python AST ผ่าน 6 ไฟล์; Git diff whitespace ผ่าน; full suite อยู่ระหว่างรัน และจะตรวจ targeted หลังปรับซ้ำ

### ตัวจำลองหุ่น — ชุด 5: คู่มือใช้งานและเตรียม Android

- เพิ่ม `docs/robot-simulator.md`: วิธีเปิด/ซ้อม/ส่งออก, ขอบเขตคำสั่งเทียบ SDK พร้อมเลขหน้า PDF, ความต่างตัวจำลองของเรากับ SDK MOCK, รายการที่ต้องได้ก่อนสร้าง APK และตรวจบน REAL
- `README.md`, `docs/robot-integration.md`: เชื่อมไปตัวจำลองและแก้ mapping กลับฐานให้ตรง `manager.goHome()`
- ตรวจเอกสาร Android ทางการเรื่อง WebView/native bridge เพิ่มเติมและใส่แหล่งอ้างอิงในคู่มือ; ไม่เพิ่ม Android skeleton ที่ยังตรวจ compile กับ vendor SDK ไม่ได้
- ผลตรวจ: JavaScript syntax ผ่าน; tests ชุด robot/simulator/hardening/review อยู่ระหว่างรัน; ยังไม่อ้างว่า APK หรือการเดินจริงพร้อมใช้งาน

### ตัวจำลองหุ่น — ชุด 4: regression tests

- เพิ่ม `tests/test_robot_simulation.py` ตรวจวงจร ACK/arrival, คำสั่งซ้ำ/ID ชน, หยุด/กลับฐาน/เหตุขัดข้อง/timeout, callback เก่า, disconnect/reconnect, bounded memory, context isolation และ HTTP validation
- เพิ่ม regression ของระบบจริงสำหรับ stop ส่งไม่สำเร็จ, แผนที่จริงว่าง, arrival คนละจุดหมาย; แก้ `.gitignore` ไม่ติดตามโฟลเดอร์ scratch `.pytest-tmp-*`
- ผลตรวจเบื้องต้น: tests หุ่นเดิม **26 passed**; รอบแรก pytest ใช้ temp เดิมนอก workspace แล้วติดสิทธิ์ จึงใช้ `--basetemp` ภายใน workspace และผ่าน ไม่ได้แก้ระบบหรือขอขยายสิทธิ์
- tests ใหม่และ browser QA รอตรวจในชุดส่งมอบ

### ตัวจำลองหุ่น — ชุด 3: หน้าจอและตัวเปิดใช้งาน

- เพิ่ม `client/robot-simulator.html`, `.css`, `.js`: หน้าจอภาษาไทย แผนที่ SVG เคลื่อนไหว สถานะ/ผลเครื่องมือ แทรกเหตุขัดข้อง ลองส่งคำสั่งซ้ำ และส่งออก JSON พร้อมป้าย simulation ทุกส่วนสำคัญ
- เพิ่ม `start-robot-simulator.cmd` เปิดเซิร์ฟเวอร์แยก localhost:8010 ด้วย virtualenv ที่มี ไม่เปลี่ยนค่าการเชื่อมต่อระบบหลักและไม่เปิดฟีเจอร์ฮาร์ดแวร์
- หน้าจอใช้ไฟล์ในเครื่องทั้งหมดและ textContent สำหรับข้อมูลเหตุการณ์ ไม่มี CDN/ไมโครโฟน/LLM ในตัวจำลอง; ชื่อจุดหมายเป็นข้อมูลสมมติ
- ผลตรวจ: รอทดสอบ unit/API และตรวจหน้าจอจริง ไม่ได้สร้าง APK เพราะยังไม่มี vendor AAR และ Android toolchain

### ตัวจำลองหุ่น — ชุด 2: engine และ API แยกจากระบบเสียง

- เพิ่ม `app/robot_simulation.py`: แผนที่/เวลา/แบตเตอรี่สมมติ, ไปจุดหมาย/หยุด/กลับฐาน, ACK, command ID, ป้องกันคำสั่งซ้ำในหน้าต่าง 256 รายการ, กัน callback เก่า, จำลองสิ่งกีดขวาง/นำทางล้มเหลว/ไม่รายงานถึง/ACK หาย/การเชื่อมต่อหลุด
- เพิ่ม `app/robot_simulator.py`: FastAPI เฉพาะ localhost พอร์ต 8010 เรียกเครื่องมือ robot เดิมผ่าน context จำลอง; จำกัด 4 เครื่องมือและ payload, ป้องกัน cross-origin mutation, ไม่เปิด VoiceSession หรือเชื่อม SDK/มอเตอร์
- ACK และ command ID ในชุดนี้เป็น contract ของตัวจำลองที่เสนอสำหรับ Android bridge ไม่ใช่การอ้างว่า SDK หรือ `/ws` จริงรองรับแล้ว; การชน/ระยะเบรก/SLAM/เสียงยังไม่ใช่สิ่งที่จำลองได้
- ผลตรวจ: รอ unit/API/browser tests หลังเพิ่มหน้าจอ; ไม่เพิ่ม dependency

## 2026-09-08 — ตัวจำลองหุ่นจากคู่มือที่ผู้ใช้ส่ง (ชุด 1)

- เพิ่ม `app/robot_backend.py` เป็น context เฉพาะคำขอสำหรับตัวจำลอง แยกสถานะจากหุ่นจริง ไม่มี endpoint เปิดโหมดสั่งฮาร์ดแวร์
- `app/tools/robot_link.py`, `app/tools/robot.py`: ให้เครื่องมือเดิมใช้ backend จำลองและรายงาน `hardware: simulated`; แผนที่หุ่นจริงว่างต้องไม่ย้อนใช้จุดสมมติ; ปฏิเสธ arrival คนละจุดหมาย; แก้ข้อความหยุด/กลับฐานให้ไม่ยืนยันผลจริงก่อนมีหลักฐาน และเพิ่ม timeout สำหรับกลับฐาน
- อ่าน PDF ต้นฉบับ/ฉบับไทย 4 ไฟล์ใน Downloads; ตรวจภาพหน้า 4 ของ SDK ยืนยัน REAL/MOCK และชื่อ AAR ข้อมูลส่วน SDK/Android จริงยังต้องยืนยันด้วยไฟล์ไลบรารีและฮาร์ดแวร์
- ผลตรวจ: รอทดสอบร่วมกับ engine/API/UI จำลองในชุดถัดไป ไม่ได้แก้ `.env` หรือรีสตาร์ทระบบเสียงจริง

บันทึกทุกชุดการแก้ไข/เพิ่มไฟล์ พร้อมเหตุผลและผลตรวจสอบ ห้ามใส่ secret หรือข้อมูลส่วนบุคคลจริง

## 2026-09-08 — กติกาบันทึกและเริ่มแก้ผลรีวิว

- เพิ่ม `AGENTS.md` เพื่อเก็บคำสั่งของผู้ใช้ให้ทุกการแก้ไข/เพิ่มงานมีบันทึกในไฟล์นี้ต่อไป
- เพิ่ม `CHANGELOG.md` เป็นประวัติกลาง และบันทึกไฟล์ประวัติ/กติกาที่เพิ่มในรอบนี้ด้วย
- รายงานต้นทาง: `docs/review/project-review-2026-09-08.md` เพิ่มในการรีวิวก่อนหน้า มี 8 ข้อค้นพบพร้อมหลักฐานและแนวทางแก้
- ผลก่อนแก้: ชุดทดสอบเดิม 1,064 passed, 1 warning; ตัวอย่างจำลองพบช่องว่างเพิ่มเติม 8 เรื่องตามรายงาน
- สถานะ: เริ่มดำเนินการแก้ตามการอนุญาตของผู้ใช้ ยังไม่แก้ข้อมูลโครงการหรือการตั้งค่าลับบนเครื่อง

### ชุด 1a — เครื่องมือและบทบรรยาย

- `app/tools/registry.py`: กรองกลุ่มเจ้าของเครื่องมือทั้ง schema และ dispatch แม้ import ทางอ้อม
- `app/tools/knowledge.py`: แยก draft/approved ตามข้อมูลจริง และไม่เขียนสถานะสไลด์ใน multi-session
- `app/tools/webstage.py`: ปิดผลข้างเคียงหน้าต่างเครื่องใน multi-session
- ผลตรวจ: รอ regression tests; ไม่เปลี่ยนสถานะอนุมัติในข้อมูลโครงการ

### ชุด 1b — ความจำและ profile ต่อเซสชัน

- `app/prompts.py`: ไม่อ่าน/แนบ personal memory เมื่อปิดกลุ่ม memory หรืออยู่ใน shared mode
- `app/session.py`: ไม่ให้ URL เปลี่ยนเครื่อง condo เป็น Emma; profile ที่ไม่รู้จักกลับเป็น condo
- `app/providers/gemini.py`, `app/providers/openai_realtime.py`: ไม่ execute tool เมื่อเซสชัน use_tools=False แม้ upstream ส่ง tool call มา
- ผลตรวจ: รอ regression tests ของ persona และ provider

### ชุด 2a — ย้ายงาน I/O ออกจากวงรอบเสียง

- เพิ่ม `app/tool_io.py`: worker I/O และจุดส่งงาน state/UI กลับ event loop อย่างชัดเจน
- `app/tools/registry.py`: รองรับ blocking handlers พร้อม timeout และเก็บงานที่ยังทำไม่เสร็จเพื่อปฏิเสธการสั่งซ้ำ
- `app/tools/webstage.py`, `app/tools/knowledge.py`: งานหน้าจอ/สถานะจาก worker กลับมาทำบน event loop
- `app/tools/documents.py`: ย้าย printer subprocess ออกจาก event loop โดยคงฟังก์ชัน async สาธารณะเดิม
- ผลตรวจ: รอ heartbeat/timeout/duplicate-execution tests; worker thread ไม่สามารถย้อนคำสั่งอุปกรณ์ที่ส่งไปแล้วได้

### Batch 2b - Enable worker execution

- Mark synchronous tools in `units.py`, `computer.py`, `knowledge.py`, `mydocs.py`, and `websearch.py` as blocking so registry dispatch runs their network/filesystem/OS calls outside the audio loop. Direct function APIs stay compatible.
- Validation: pending targeted tests.

### ชุด 2c — งานค้างหลัง timeout

- `app/tool_io.py`, `app/tools/registry.py`: ส่งสัญญาณยกเลิกผลข้างเคียงบน UI หลัง timeout/cancel แต่เก็บ worker จนจบเพื่อกันคำสั่งซ้ำ
- `app/tools/documents.py`: ใช้ exclusive execution สำหรับงานพิมพ์ที่เริ่มส่งแล้ว ห้ามซ้ำระหว่างงานเก่ายังไม่จบ
- `app/main.py`: ย้าย inventory startup probe ไป thread ด้วย
- ผลตรวจ: รอ regression tests; ยังไม่ยืนยันผลการพิมพ์จากการหมดเวลา

### ชุด 3 — ขอบเขตบทสนทนา ผลอุปกรณ์ และหลักฐานเสียง

- `app/session.py`: reset ROI/สไลด์/คำพูดเก่าเมื่อเริ่มและจบสายปกติ ไม่ให้ cleanup สายเก่าเช็ด subtitle ของสายใหม่ และบันทึก said สำหรับ transcript ปกติด้วย
- `app/tools/robot_link.py`: ส่งผ่าน transport ที่ส่งต่อ exception แทน UI helper ที่กลืน error เพื่อคืน failed ตามจริง
- `app/display.py`: เพิ่ม guard multi-session ที่เส้นทางแสดงสไลด์/Canva ไม่ใช่เฉพาะ subtitle
- ผลตรวจ: รอ sequential-session, broken-socket และ transcript tests

### ชุด 4 — ปิดการเผยแพร่ metadata และป้องกันภาพสไลด์

- เพิ่ม `app/slide_assets.py` และเปลี่ยน mount ใน `app/main.py`: เปิดเฉพาะภาพ raster, ไม่เปิด index/embeddings/backup แม้มี token และตรวจ WS_TOKEN ก่อนส่งภาพ
- `client/index.html`, `client/display.html`: แนบ token เฉพาะ URL ภาพ /slides/ ของ origin เดียวกัน ไม่ส่ง credential ไปเว็บภายนอก
- ผลตรวจ: รอ HTTP auth/path tests และ client checks; เครื่องที่ไม่ได้ตั้ง WS_TOKEN ยังใช้ภาพได้ตามเดิม

### ชุด 5a — ผล targeted tests และปรับ fixtures ให้ตรงสิทธิ์จริง

- ตรวจ 176 tests แรก: ผ่าน 162, ไม่ผ่าน 14; ตรวจสาเหตุแล้วพบ fixtures memory ไม่ได้เปิดสิทธิ์, robot fake ข้าม transport และ approved-copy fixture ไม่เคยอนุมัติข้อมูล
- `tests/test_memory.py`, `tests/test_robot.py`, `tests/test_knowledge.py`: ระบุสิทธิ์/สถานะข้อมูลและจำลอง transport ตามสัญญาใหม่ โดยคง assertions ผลลัพธ์เดิม
- `app/tools/__init__.py`: ประเมินโมดูลที่ต้องโหลดทุกครั้ง (Python cache import อยู่แล้ว) เพื่อไม่ค้างกลุ่มจากการตั้งค่าครั้งก่อน
- `app/tools/registry.py`: คงคำสั่งห้ามเงียบหลัง timeout พร้อมเพิ่มข้อห้ามสั่งซ้ำ
- ผลตรวจ: รอรันซ้ำร่วมกับ regression tests ใหม่

### ชุด 5b — เพิ่ม regression tests ตามหลักฐานรีวิว

- เพิ่ม `tests/test_review_fixes.py`: cold import/schema/dispatch, memory/profile, draft approval, shared-screen isolation, worker heartbeat/timeout/duplicate prevention, thread-to-loop handoff, printer exclusivity, visitor state, robot transport, said logs และ HTTP asset authorization
- ใช้ข้อมูลสังเคราะห์และ stub อุปกรณ์/เครือข่ายเท่านั้น ไม่มีการส่งคำสั่งจริงหรือใช้ API key
- ผลตรวจ: รอรัน targeted suite และ full suite

### ชุด 6a — ผล full suite รอบแรกและรอยต่อ I/O/lifecycle

- Targeted suite ผ่าน 199 tests; full suite รอบแรกผ่าน 1,080 และไม่ผ่าน 7 (robot fake อีกจุดไม่มี transport และ computer/websearch tests ไม่ได้เปิดกลุ่มที่กำลังตรวจ)
- `tests/test_hardening.py`, `tests/test_websearch_computer.py`: แก้ fixtures สองชนิดข้างต้น โดยไม่ลด assertions ของพฤติกรรมเดิม
- `app/tools/slides.py`: ย้าย semantic search ใน show_slide ไป thread ด้วย
- `app/tools/computer.py`: ส่งการเปิดโปรแกรม/เว็บกลับผ่านจุดตรวจ cancellation บน event loop เพื่อไม่เปิดหน้าต่างล่าช้าหลัง tool timeout
- `app/main.py`: ไม่ warm/open Canva ตอนเริ่มเซิร์ฟเวอร์ multi-session
- `app/display.py`, `app/session.py`: ยกเลิกภาพที่รอเสียงและงานพูดเบื้องหลังเมื่อสายจบ เพื่อไม่หลุดไปยังผู้ใช้คนถัดไป
- Client checks ผ่าน: JavaScript ทั้งสองหน้าผ่าน syntax และตรวจพฤติกรรม token เฉพาะ same-origin /slides/ แล้ว
- ผลตรวจชุดนี้: รอ targeted และ full suite รอบถัดไป

### ชุด 6b — งานพร้อมกันและการติดตาม session

- `app/turnlog.py`, `app/session.py`: ใส่ session ID แบบสุ่มลง log และส่งต่อผ่าน context ไป worker โดยไม่ใช้ข้อมูลส่วนบุคคล; ล็อกการเปิด/หมุน/เขียน log ให้ปลอดภัยเมื่อหลาย worker จบพร้อมกัน
- `app/tools/registry.py`: กันงานซ้ำแยกต่อ session สำหรับงานอ่าน เพื่อให้ผู้ใช้คนละคนค้นพร้อมกันได้; งานพิมพ์ยัง exclusive ทั้งเครื่อง และบันทึกผลเมื่อ worker จบหลังหมดเวลารอ
- `app/providers/openai_realtime.py`: บันทึกเฉพาะชื่อ argument เช่นเดียวกับ Gemini ไม่พิมพ์ค่าข้อมูลลูกค้าลง console
- ผลตรวจ: รอ concurrency/context propagation และ regression suite

### ชุด 6c — แก้ผล compile check ก่อนรันทดสอบต่อ

- Compile check พบว่า show_slide เป็น synchronous API เดิม จึงใช้ await ใน body ไม่ได้
- `app/tools/slides.py`: ใช้ blocking registration เช่นเดียวกับ knowledge และส่ง state mutation/resume_hint กลับผ่าน on_loop โดยคง synchronous API ให้ผู้เรียกเดิม
- `app/session.py`: ย่อและจัด comment cleanup ให้ตรงเงื่อนไขเจ้าของเซสชันใหม่
- ผลตรวจ: รอ compile และ targeted suite ซ้ำ; ไม่ได้ปล่อยโค้ดที่ compile ไม่ผ่านไปรันเซิร์ฟเวอร์

### ชุด 6d — เพิ่ม tests ของรอยต่อที่พบระหว่างแก้

- `tests/test_review_fixes.py`: เพิ่ม provider tool refusal ทั้งสองเจ้า, การค้นชนิดเดียวกันจากผู้ใช้สองคนพร้อมกัน, ยกเลิกภาพค้าง, cleanup สายเก่าไม่ล้างตัวเลขสายใหม่ และ session identity ของ log ที่เขียนจาก worker
- ผล compile หลังแก้ชุด 6c: ผ่าน; targeted suite ชุดล่าสุดอยู่ระหว่างรัน

### ชุด 6e — ผล tests ของรอยต่อ

- Targeted suite ชุด 6c ผ่าน 228 tests; regression file หลังเพิ่มเคสผ่าน 28/29 โดย fake upstream ของ OpenAI ขาดเมธอด send (ใช้คนละ API กับ FastAPI WebSocket)
- `tests/test_review_fixes.py`: เพิ่ม send ให้ fake upstream ตาม transport จริง ไม่เปลี่ยน assertions การปฏิเสธเครื่องมือ
- ผลตรวจ: รอ full suite รอบสุดท้าย

### ชุด 7 — เอกสารใช้งานและประวัติถาวร

- `README.md`: อธิบาย asset authentication, tool enforcement, private-memory/profile restrictions, timeout/print semantics, session reset/log IDs และแก้ข้อความเก่าที่บอกว่ายังไม่มี wake/auth
- `CLAUDE.md`: เชื่อมกติกาบันทึกทุกการแก้ไขไปยัง AGENTS/CHANGELOG และเลิกระบุจำนวน tests ที่ล้าสมัย
- `docs/review/project-review-2026-09-08.md`: ระบุชัดว่าเป็นหลักฐานก่อนแก้ พร้อมลิงก์ติดตามผลแก้ในประวัติ โดยคงรายละเอียดรีวิวเดิม
- ผลตรวจ: ตรวจลิงก์/ความครบถ้วนท้ายงาน; full suite กำลังรันจากโค้ดล่าสุด

## 2026-09-08 — ผลส่งมอบหลังแก้รีวิว

บันทึกผลตรวจสุดท้ายใน `CHANGELOG.md` หลังจบชุดการแก้ไขข้างต้น:

| ข้อรีวิว | สิ่งที่แก้แล้ว |
|---|---|
| 1. เครื่องมือหลุดผ่าน import | กรองทั้ง schema/dispatch และป้องกันหน้าจอ/Canva ใน shared mode |
| 2. ความจำส่วนตัวใน shared prompt | ไม่อ่าน memory เมื่อไม่มีสิทธิ์/อยู่ใน shared mode และไม่ให้ URL ยกระดับ condo เป็น Emma |
| 3. Blocking I/O | ย้ายงานไป worker, ส่ง state/UI กลับ event loop, จำกัดเวลารอ/ป้องกันคำสั่งซ้ำ และแยก worker scope ต่อเซสชัน |
| 4. Draft กลายเป็น approved | ส่งสถานะตาม script_approved จริง โดยไม่แก้เนื้อหาหรือสถานะอนุมัติในไฟล์ข้อมูล |
| 5. ROI/สไลด์ข้ามผู้ใช้ | reset ขอบเขตเริ่ม/จบสาย ยกเลิกภาพค้างและงานพูดของสายเก่า พร้อมป้องกัน cleanup ทับสายใหม่ |
| 6. Robot ส่งไม่ได้แต่บอก ok | transport error คืน failed และมี regression ผ่าน VoiceSession จริงกับ socket จำลองที่เสีย |
| 7. Metadata ผ่าน static route | ไม่เสิร์ฟ metadata และตรวจ token สำหรับภาพ; ทั้งสองหน้าแนบ token เฉพาะ same-origin |
| 8. คำพูดผู้ช่วยไม่ลง log | บันทึก normal/silent transcript ครั้งเดียว พร้อม session ID และการเขียนจาก worker ที่มี lock |

### ผลตรวจสุดท้าย

- `.\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp=.pytest_fix_final --tb=short`: **1,093 passed, 1 warning ใน 143.71 วินาที** (เพิ่ม regression tests 29 กรณีจากฐานเดิม 1,064)
- Python compile/parse: ผ่าน
- JavaScript ทั้ง `client/index.html` และ `client/display.html`: ผ่าน syntax และพฤติกรรมแนบ credential เฉพาะภาพภายใน origin
- `git diff --check`: ผ่าน
- ตรวจไฟล์ที่แก้/เพิ่มทั้งหมด 33 ไฟล์: มีรายการในประวัติครบ ไม่มีไฟล์ตกหล่น; ลิงก์ใน AGENTS/CHANGELOG/รายงานรีวิวตรวจแล้วเปิดถึงไฟล์จริง
- ข้อสังเกตจากเครื่องทดสอบ: มี Starlette TestClient deprecation warning หนึ่งรายการ และข้อความ pending IocpProactor accept task ระหว่าง teardown ของ fake upstream บน Windows; ไม่มี test failure ไม่ได้ปิดซ่อนข้อความเหล่านี้

### ขอบเขตที่ยังต้องยืนยันบนเครื่องจริง

- ยังไม่ได้เรียก Gemini/OpenAI/Supabase จริง เปิดไมค์/กล้อง หรือส่งคำสั่งเครื่องพิมพ์/หุ่นยนต์จริง
- ไม่เปลี่ยน `.env`, secret, ข้อมูลโครงการ หรืออนุมัติบทบรรยายแทนเจ้าของ
- ไม่ได้รีสตาร์ตบริการที่กำลังใช้งาน; ให้รีสตาร์ตเซิร์ฟเวอร์และรีเฟรชหน้าคุย/หน้าจอสไลด์เพื่อโหลดโค้ดและ asset authentication ใหม่
- การหมดเวลาของงานพิมพ์ไม่ได้ยกเลิกสิ่งที่ส่งถึงอุปกรณ์แล้ว ระบบจึงรายงานว่ายังยืนยันผลไม่ได้และกันคำสั่งซ้ำจนงานเก่าจบ ส่วน ACK/การหยุดจริงเมื่อหุ่นยนต์หลุดการเชื่อมต่อยังต้องทดสอบกับแอป Android
- ความเสี่ยงเพิ่มเติมในรายงาน เช่น dependency constraints/การวัดคุณภาพเสียงและใบหน้าบนฮาร์ดแวร์ ไม่ได้อ้างว่าถูกแก้ด้วย unit tests รอบนี้

## 2026-09-08 — Research แนวทางพัฒนาต่อ

- เพิ่ม `docs/research/improvement-roadmap-2026-09-08.md`: เทียบโค้ดหลังแก้รีวิวกับเอกสารทางการของ Google, OpenAI, LiveKit, Anthropic, Sentence Transformers, AWS, uv, OWASP และงานวิจัย Thai end-of-turn; จัดลำดับงาน 10 ข้อ พร้อมช่องว่างจริง ขอบเขต เกณฑ์รับงาน และประมาณการ โดยแยกการทดลอง WebRTC/Thai EOT ออกจากงานที่พร้อมเริ่ม
- บันทึกสิ่งที่มีอยู่แล้วเพื่อไม่เสนอซ้ำ เช่น hybrid retrieval/reranker, local VAD, compression/resumption, inventory/promotions/compare/quotation และ arrival timeout
- ตรวจ metadata แบบอ่านอย่างเดียว: 150 ภาพ, 64 scripts, ไม่มี `script_approved` ที่เป็นจริง; eval file มี 126 กรณี ไม่เผยแพร่เนื้อหาส่วนตัวหรือเปลี่ยนสถานะอนุมัติ
- ระบุข้อสังเกตจากการอ่าน `scripts/eval_search.py`: กรณี `not hits` ที่ควรค้นเจอไม่เพิ่ม `wrong` และ commercial cases ไม่อยู่ใน denominator เดียวกัน เป็นงานเสนอถัดไป ยังไม่ได้แก้ runtime/สคริปต์หรืออ้างว่ารันจำลองแล้ว
- อัปเดต `CHANGELOG.md` ในชุดเดียวกับเอกสารตามคำสั่งให้บันทึกทุกการแก้/เพิ่ม; รอบนี้ไม่มีการเปลี่ยนโค้ด การตั้งค่า dependencies ข้อมูลโครงการ หรือบริการที่กำลังใช้งาน
- ผลตรวจและการอัปเดตผลในประวัติรอบนี้: UTF-8 ผ่าน, ลิงก์ไฟล์ภายในรายงาน 16 จุดเข้าถึงได้ครบ, แหล่งอ้างอิงภายนอก 13 URL, whitespace/final newline ของเอกสารและประวัติผ่าน และ `git diff --check` ผ่าน; ไม่รัน full suite ซ้ำสำหรับเอกสารล้วน และไม่เรียก API/ฮาร์ดแวร์จริง ผล 1,093 passed ที่อ้างถึงเป็นผลรอบก่อนหน้า

## 2026-09-08 — ลงมือพัฒนาชุดแรกตาม research

### ชุด 1 — Metrics ฝั่งเซิร์ฟเวอร์และ provider

- เพิ่ม `app/metrics.py`: ID ต่อ reply, monotonic durations, bounded client/response dedupe และ allowlist ตัวเลข usage ที่ไม่บันทึก arguments/credential; Gemini เก็บเป็น snapshot ไม่บวกเป็นยอด billing เพราะไม่มี response ID
- แก้ `app/providers/base.py`, `app/providers/openai_realtime.py`, `app/providers/gemini.py`: normalized usage/speech-stop events และเวลาที่ local VAD ตัดสินจบเสียง โดยไม่เปลี่ยน VAD หรือโมเดลที่ใช้งาน
- แก้ `app/session.py`: ผูก metrics ต่อ session, จบ reply เมื่อ complete/interrupted/closed และส่ง ID ให้ browser วัด queue; แก้ `app/tools/registry.py`: วัดเวลารอ dispatch รวม timeout/cancellation โดยไม่แก้คำสั่งหรือ arguments
- ผลตรวจ: รอทดสอบ unit/integration หลังต่อ browser และรายงาน; เป็นเวลาที่ระบบสังเกตได้ ไม่ใช่เสียงถึงหูหรือค่าใช้จ่ายจริง

### ชุด 2 — Browser และรายงาน metrics

- `client/index.html`: รายงานเวลารอ schedule เสียงและเวลาล้างคิวเมื่อถูกพูดแทรก ภายใต้ turn ID ที่ server ส่งมา ไม่ใช้ timestamp ข้ามเครื่องและไม่เปลี่ยน audio buffer
- เพิ่ม `scripts/report_metrics.py`: อ่าน JSONL แล้วสรุป p50/p95/จำนวนตัวอย่างและ token usage แยก provider/model/VAD; ไม่แสดง transcript และไม่นำ Gemini snapshot มาบวกเป็นยอดรวม
- `app/session.py`: ใส่ชื่อโมเดล OpenAI และปิด reply เมื่อมี speech-start; `app/tools/registry.py`: แก้ชื่อ registry ใน metrics wrapper ให้ตรง `_REGISTRY` ที่มีจริง (พบจากตรวจ source ก่อนรันทดสอบ)
- ผลตรวจ: รอ regression tests; client metric ผ่าน allowlist/ช่วงค่า/dedupe ต่อ session แล้ว แต่ยังไม่ได้วัดเสียงออกลำโพงจริง

### ชุด 3 — ผล eval ที่ตรวจสอบย้อนกลับได้

- `scripts/eval_search.py`: ผลหนึ่งรายการต่อหนึ่งคำถาม รวม commercial refusal/no hits/error เป็น pass/fail/skipped ชัดเจน; no hits ของคำถามบวกเป็น fail; errors ไม่ถูกนับเป็นคำตอบถูก
- เพิ่มตัวเลือก train/test/all, manifest ตรวจไม่ให้กลุ่มเดียวกันข้าม split, JSON report พร้อม dataset/deck hash และ configuration; แนะนำ threshold เฉพาะ train และเขียน query cache เมื่อระบุ `--write-cache` เท่านั้น
- ผลตรวจ: รอ manifest และ regression tests; split จากคลังเก่าจะระบุว่าเป็น regression partition ไม่อ้างว่าเป็นข้อมูลที่โมเดล/ผู้พัฒนาไม่เคยใช้มาก่อน

### ชุด 4 — กำหนด environment สำหรับติดตั้งซ้ำ

- เพิ่ม `pyproject.toml`: Python 3.14 / Windows AMD64 ตามเครื่องที่ทดสอบจริง, pin direct dependencies ตามเวอร์ชันติดตั้งปัจจุบัน, แยก local-search/face/wake/browser/hardware/documents/websearch และ dev โดยประกาศ sentence-transformers ที่เดิมขาด
- `.gitignore`: ไม่เก็บ `.venv-smoke`, uv cache และผล eval ที่อาจมีคำถามจริงลง Git
- เตรียมสร้าง `uv.lock` และ export `requirements.txt` จาก lock เพื่อรักษาคำสั่งติดตั้งเดิม; ทดสอบใน `.venv-smoke` โดยไม่ sync/เปลี่ยน `.venv` ที่ใช้งานอยู่
- ผลตรวจ: รอ dependency resolution และ clean-install smoke; ยังไม่อ้างว่าติดตั้งใหม่ผ่าน

### ชุด 5 — Manifest แยกชุด eval

- เพิ่ม `data/eval/splits-v1.json`: แบ่ง 126 คำถามเดิมตามกลุ่มคำตอบ/หมวด negative แบบคงที่ เป็น train 104 / test 22 โดยไม่แบ่งกลุ่มเดียวกันข้าม split และระบุว่าเป็น historical regression partition
- คง `data/eval_questions.txt` และข้อมูลโครงการเดิม; แก้ข้อความประวัติชุดนี้ที่ PowerShell pipe แปลงภาษาไทยเป็นเครื่องหมายคำถามด้วย UTF-8 patch; ผลตรวจ manifest รอ regression tests

### ชุด 6 — ผลตรวจระหว่างพัฒนา

- รอเพิ่ม `tests/test_metrics.py`, `tests/test_eval_search.py` และแก้ test_voice/script config ในชุดเดียวกัน; patch ชุดแรกไม่ผ่านการตรวจ context ในประวัติที่ encoding เสีย จึงยังไม่เกิดการแก้ไฟล์เหล่านั้น
- ผลตรวจรอบแรก: voice/review suite 180 passed, 1 failed จาก assertion อ่าน JavaScript เพียง 700 ตัวอักษร; เตรียมให้ตรวจถึง case ถัดไปโดยคง assertion เดิม
- uv resolve ครั้งแรกติด network sandbox; retry โดยสิทธิ์ที่ automatic review อนุมัติแล้วสร้าง `uv.lock` ได้ 104 packages และ export `requirements.txt` พร้อม hashes; clean install ทุก extras ใน `.venv-smoke` สำเร็จ 102 packages โดยไม่แก้ `.venv` เดิม
- Full suite บน environment ใหม่หยุดที่ native VAD access violation หลังผ่านประมาณ 79%; กำลังตรวจ dependency/native library จึงยังไม่ถือว่า lock ผ่านการยืนยัน

### ชุด 7 — แก้ dependency ที่ขาดและเพิ่ม regression tests

- `pyproject.toml`: เพิ่ม `sherpa-onnx-core==1.13.6` ใน wake extra อย่างชัดเจน เพราะ uv lock ไม่รวม dependency นี้แม้ Windows wheel ระบุ Requires-Dist; `uv pip check` ยืนยันว่า core ขาด และโฟลเดอร์ใหม่ไม่มี DLL ที่มีในเครื่องเดิม เตรียม regenerate `uv.lock`/`requirements.txt` และ sync เฉพาะ smoke environment
- เพิ่ม `tests/test_metrics.py`, `tests/test_eval_search.py` ครอบคลุม metrics clock, bounded/deduped client reports, usage snapshots, timeout, provider events และผล eval/group separation
- `tests/test_voice.py`: เปลี่ยนขอบเขตตรวจจาก 700 ตัวอักษรเป็น handler ทั้ง case โดยคง assertion; `scripts/eval_search.py`: ใช้ local coverage setting และ lexical constant ที่มีจริงใน JSON report
- ผลตรวจ: รอ targeted tests และ real VAD test บน environment ใหม่หลังเติม native core

### ชุด 8 — Clean-install smoke และ CI

- เพิ่ม `scripts/smoke_install.py`: import ทุก feature โดยไม่เปิดอุปกรณ์/เรียก provider พร้อมตรวจ DLL ของ sherpa core; เลือกตรวจ encode จริงจากโฟลเดอร์ local model ได้โดยไม่ดาวน์โหลด
- เพิ่ม `.github/workflows/test.yml`: Windows/Python 3.14, locked install ทุก extras, dependency check, smoke, pytest และ Node; เพิ่ม `tests/client_metrics.cjs` ทดสอบ syntax และเล่นเสียงผ่าน Web Audio mock รวมคิวและการหยุด
- `scripts/eval_search.py`: หากขอ semantic/reranker แล้ว backend ใช้ไม่ได้ให้ skipped และ exit ไม่สำเร็จ ไม่รายงาน lexical fallback ว่าเป็นผล hybrid; JSON ระบุ effective provider/availability; `scripts/report_metrics.py`: แยกเวลาของแต่ละ tool เพิ่มจากภาพรวม
- ผลตรวจหลังแก้ core: `uv pip check` ผ่าน; real Silero VAD กับ WAV ที่มีในโปรเจคผ่าน 1 test; targeted metrics/eval/voice/review ผ่าน 201 tests; lexical test split ผ่าน 22/22 ไม่มี skipped และเขียน `data/eval-runs/lexical-test.json` ที่ gitignore ไว้ (เป็นผลข้อมูลเก่า ไม่ใช่เสียงลูกค้าจริง)
- Lock ใหม่มี 105 packages / environment ติดตั้ง 103 packages; รอ smoke/Node/full suite รอบสุดท้าย และยังไม่ได้รัน GitHub Actions บน remote

### ชุด 9 — คู่มือใช้งานและผล eval ทั้งสอง split

- เพิ่ม `docs/measurement-and-installation.md`: วิธีติดตั้งตาม lock/เลือก extras/ทดสอบ environment แยก, ความหมายและข้อจำกัดของทุก metric/usage, CLI รายงาน, eval split/report/cache และขั้นตอนเก็บเสียงจริงภายหลัง
- `README.md`: เชื่อมคู่มือและระบุ Python/Windows ที่ lock รองรับ; แทนคำสั่ง pip บน macOS/Linux ที่ export ใหม่ไม่รองรับด้วยขอบเขตจริง; อัปเดตคำอธิบาย train/test โดยไม่อ้างว่า historical data เป็นข้อมูลใหม่
- ผล Node: syntax + first-chunk metric + queue timing/interruption ผ่าน; lexical train 93 passed / 11 failed / 0 skipped จาก 104 กรณี (exit 1 ตามข้อผิดพลาดจริง), test 22/22; ไม่ปรับ threshold หรือคำตอบเพื่อทำคะแนนให้ผ่าน เก็บรายงานใน `data/eval-runs/lexical-train.json` และ `lexical-test.json`
- Full suite บน `.venv-smoke` และ all-extras import smoke กำลังรัน; พบ encoder snapshot อยู่ใน cache เดิมจึงเริ่มทดสอบ encode แบบ offline เพิ่ม โดยไม่ดาวน์โหลดโมเดล

### ชุด 10 — ปิดช่องว่างการ calibrate จากข้อมูลไม่ครบ

- `scripts/eval_search.py`: ไม่เสนอ threshold เมื่อ train มี skipped แม้บางกรณีมีคะแนน; ปรับหัวตารางให้ตรงผลรายกรณีและลบตัวแปรเก่าที่ไม่ใช้
- `tests/test_eval_search.py`: เพิ่ม CLI regression กรณี semantic ใช้ไม่ได้ทั้งชุด ต้อง exit 1/แสดง skipped และไม่เข้า calibration
- ผลตรวจล่าสุดก่อนชุดนี้: full suite บน clean environment **1,113 passed, 1 warning ใน 159.80 วินาที**; all-extras smoke ผ่านทุกกลุ่ม, actual local encoder inference จาก snapshot เดิมผ่านโดย offline; รอ targeted eval tests หลัง guard นี้

## 2026-09-08 — ผลส่งมอบชุดวัดผล/eval/lock

- Full suite ใน `.venv-smoke`: **1,113 passed, 1 warning ใน 159.80 วินาที** ก่อนเพิ่ม guard สุดท้ายใน eval; หลังเพิ่ม guard และ CLI regression ตรวจ eval/metrics/retrieval ซ้ำ **74 passed ใน 13.42 วินาที** (เพิ่ม tests รวม 21 กรณีจากฐาน 1,093)
- Node browser syntax/queue/interruption: ผ่าน; import smoke ทุก extras และ `app.main`: ผ่าน; local SentenceTransformer encode จาก snapshot เดิมโดย `local_files_only=True`: ผ่าน; real Silero VAD จาก WAV ในโปรเจค: ผ่าน
- `uv lock --check --offline`: ผ่าน 105 packages; `uv pip check` บน environment ใหม่: ผ่าน 103 installed packages; export requirements มี version pins และ hashes ไม่มีการอัปเดต `.venv` หลัก
- Eval lexical: train 93/104 (11 fail, 0 skipped), test 22/22 (0 skipped) เป็น historical text regression ไม่ใช่คะแนนเสียงจริง; ไม่มีการปรับคำถาม/ข้อมูล/threshold ให้คะแนนผ่าน
- `git diff --check`, TOML และ workflow YAML parse: ผ่าน; GitHub Actions ถูกเพิ่มแต่ยังไม่ได้ push หรือเรียก remote workflow
- คำเตือนเดิม: Starlette TestClient deprecation และ Windows fake-upstream teardown pending accept tasks ไม่ทำให้ suite fail; native VAD crash จาก core ที่ขาดถูกแก้และตรวจใหม่แล้ว
- ตรวจชื่อไฟล์ในประวัติพบ 3 ไฟล์จากรอบก่อนที่บันทึกชื่อย่อ จึงระบุชื่อเต็มเพิ่ม: `app/tools/mydocs.py`, `app/tools/units.py`, `app/tools/websearch.py` (blocking worker/UI-loop fixes ของรอบรีวิว ไม่มีการแก้เพิ่มใน research implementation นี้)
- อัปเดตผลตรวจใน `CHANGELOG.md` รอบนี้: ตรวจ UTF-8/whitespace ของไฟล์ใหม่และลิงก์คู่มือผ่าน; ไฟล์ที่แก้/เพิ่มทั้งหมดใน working tree 50 ไฟล์ (รวมรอบรีวิวก่อนหน้า) มีชื่อในประวัติครบ ไม่มีรายการตกหล่น
- ไม่รีสตาร์ตบริการหรือเปิดไมค์/กล้อง/เครื่องพิมพ์/robot จริง ไม่เรียก paid provider หรือ inventory จริง ไม่เปลี่ยน `.env` หรืออนุมัติเนื้อหาโครงการ; เปิดใช้โค้ดใหม่หลังรีสตาร์ต server/รีเฟรช browser ในช่วงที่เหมาะสม
- คู่มือส่งมอบ: `docs/measurement-and-installation.md`; งาน Hybrid VAD, content approval workflow, robot ACK และ acoustic benchmark อยู่ในรอบถัดไปตาม research โดยชุดนี้เตรียมฐานวัดผลให้แล้ว
