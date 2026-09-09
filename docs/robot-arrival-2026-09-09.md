# ตรวจรับหุ่น — 9 กันยายน 2026

**ข้อสรุปขณะตรวจ: ซอฟต์แวร์ฝั่ง PC และตัวจำลองทดสอบได้ แต่ยังไม่มีหลักฐานว่าพร้อมติดตั้งแล้วสั่งหุ่นจริงเดินทันที** ยังขาดแอป Android bridge, SDK ที่ตรงรุ่น และการทดสอบกับเครื่องที่ส่งมาจริง

## ผลตรวจบน PC

| รายการ | ผลที่ตรวจได้ | ยังต้องทำ |
|---|---|---|
| Python / dependencies | Python 3.14.6; 103 packages compatible; import ทุก extras ผ่าน | ใช้ `.venv-smoke` ที่ตรวจแล้ว |
| Automated tests หลังแก้ทั้งหมด | **1,202 passed / 1 Starlette deprecation warning ใน 182.38s** | ยังต้องทำ hardware acceptance |
| Client เสียง | Node regression 3 ชุดผ่าน รวมเสียงทักทายแรก/ปลุก/เปลี่ยนสาย | ฟังลำโพงและไมค์หุ่นจริง |
| Gemini จริง | แก้วิธีส่งข้อความสำหรับ 3.1; QA ได้เสียงทักทาย 208,350 bytes และเสียงแจ้งถึงจุดหมายจำลอง 184,830 bytes จบทั้งสอง turn | ยังไม่ได้ตรวจเสียงออกลำโพงหุ่นจริง |
| ตัวจำลอง 3D / Emma | เปิด backend รุ่นแก้บน 8010 แล้ว, health ผ่าน, wake เชื่อมต่อได้ | เป็นแผนที่สมมติ ไม่ใช่แผนที่ SLAM ของหุ่น |
| Production | ตั้ง HTTPS พอร์ต **8001** แต่ connection refused ตอนตรวจ | เปิด `run_server.py` ก่อนทดสอบจากหุ่น |
| TLS | โหลด certificate กับ private key คู่กันได้; certificate หมดอายุ 24 พ.ย. 2028 | Android ต้องเชื่อถือ certificate และใช้ชื่อ/IP ที่อยู่ใน SAN; ยังไม่ผ่านการทดสอบจากหุ่น |
| การสั่งหุ่นจริง | `ROBOT_ENABLED=false`, ยังไม่มี ROBOT_TOKEN และกลุ่มเครื่องมือปัจจุบันไม่มี `robot` | ตั้งค่าหลัง bridge และการหยุดหน้างานพร้อม |
| SDK / Android app | ค้นใน repo และ Downloads ไม่พบ AAR/APK | ขอจากผู้ขาย; ต้องมีแอป bridge เพิ่ม ไม่ใช่เพียงเปิดเว็บ |
| เครื่องมือ Android | เตรียม local ADB **37.0.1** แล้วใน `tools/android/platform-tools`; ยังไม่มี java/gradle ใน PATH | ขอ source demo และเวอร์ชัน build tools จากผู้ขายก่อนจัด JDK/Gradle |
| Smart Home จริง | มี Broadlink package, device config และ IR code file | ยังไม่ได้ส่ง IR หรือยืนยันไฟ/แอร์จริง; IR เป็นการส่งทางเดียว |

ผลเหล่านี้เป็นสถานะเวลาตรวจ ไม่ใช่การรับรองว่า API key, เครือข่าย หรืออุปกรณ์จะพร้อมตลอดวัน ไม่มีการเปิดไมค์หรือสั่งมอเตอร์จริงระหว่างตรวจ

## สิ่งที่แก้ก่อนรับหุ่น

- Live check พบ Gemini เปิดสายแต่ไม่ส่งเสียงทักทาย: เปลี่ยน Gemini 3.1 Flash Live ให้ส่งข้อความสดด้วย `send_realtime_input` ตาม API รุ่นนี้ ทดสอบจาก API จริงแล้วทั้ง greeting และประกาศถึงจุดหมายจำลอง ส่วน Gemini 2.5 ใช้เส้นทางเดิม

- JSON ที่ไม่ใช่ object หรือ type ไม่ใช่ข้อความจะถูกข้าม ไม่ทำให้สายเสียงล้ม
- `robot_ready.places` ต้องเป็น list ของชื่อที่ไม่ว่าง; ปฏิเสธชนิดผิดและชื่อยาวผิดปกติ ไม่แทนที่แผนที่ด้วยข้อมูลเสีย
- `robot_arrived` ต้องมีชื่อจุดหมายตรงกับงานที่รอ และ `ok` เป็น boolean จริง; `"false"`, `1`, ค่าว่าง หรือไม่ส่ง `ok` ไม่กลายเป็นข้อความถึงแล้ว
- คำขอที่ตรงหลายจุดหมายซึ่งไม่ใช่ชื่อซ้อนกันจะถูกปฏิเสธเพื่อถามให้ชัดก่อนส่งคำสั่ง
- เมื่อ timeout แยกได้ว่าส่ง cancel สำเร็จหรือไม่ และไม่ยืนยันว่าหุ่นหยุดจริง; สถานะสาธารณะใช้ `moving: null` เมื่อไม่ทราบ รวมตอนตัดสายและส่ง stop
- `status_source` แสดงที่มาของสถานะ: `command_sent` เป็นคำสั่งที่ส่ง ไม่ใช่ telemetry; `arrival_report` เป็น callback ของแอป

## ขอผู้ขายก่อนช่างกลับ

มี [ชุดรับ AAR/demo APK พร้อมข้อความขอผู้ขาย](../vendor/aobo/README.md) และ [วิธีใช้ local ADB](../tools/android/README.md) แล้ว ยังไม่มีไฟล์ AAR/demo ที่ได้รับการยืนยันจากผู้ขาย

1. ยืนยันรุ่นจริง, Android/firmware, ชื่อและเวอร์ชัน AAR, CPU ABI ที่รองรับ และไลเซนส์ SDK ให้ตรงกับเครื่อง พร้อม AAR, demo APK **และ source demo/วิธี build**
2. วิธีเข้า Android settings/ติดตั้งแอปของเรา, USB debugging, สาย USB ที่ส่งข้อมูลได้, สิทธิ์ kiosk/autostart และวิธีกลับไปแอปเดิม
3. เปิด demo ให้ดูการอ่านแผนที่/POI, เดินไปจุด, cancel, กลับฐานและชาร์จ พร้อม payload callback สำเร็จ/ผิดพลาดจริง
4. ให้สาธิตปุ่มหยุดฉุกเฉินและการคืนสภาพ, หุ่นทำอะไรเมื่อ Wi-Fi/SDK หลุด, ตรวจสิ่งกีดขวาง/ขอบพื้น และวิธีจำกัดความเร็วสำหรับพื้นที่ทดสอบ
5. ปิดระบบสนทนาเดิมเพื่อไม่แย่งไมค์; ระบุช่องเสียงที่ผ่าน AEC, sample rate/mono PCM, วิธีใช้ลำโพง และสิทธิ์ไมค์ใน Android/WebView
6. สร้าง/สำรองแผนที่หน้างานและแท่นชาร์จจริง เก็บรายชื่อ POI จากเครื่อง ห้ามคัดลอกพิกัดจากฉาก 3D ไปใช้เป็นพิกัดหุ่น

## ลำดับตรวจรับช่วงบ่าย

| ลำดับ | วิธีตรวจ | เกณฑ์ผ่าน |
|---|---|---|
| 1. เครื่องเดิม | ช่างเปิด demo ตรวจแบต/เซนเซอร์/แผนที่/ฐานชาร์จ และสาธิต E-Stop ในพื้นที่กั้น | หยุดได้จริงและกู้คืนได้ตามคู่มือรุ่นที่ส่ง |
| 2. เครือข่าย | PC กับ Android ใช้เครือข่ายถึงกัน; เปิด production HTTPS 8001; ทดสอบ health จากหุ่น | ไม่มี cert error, firewall ไม่ขวาง; ไม่ใช้ `127.0.0.1` ของ PC เป็นที่อยู่บนหุ่น |
| 3. เสียงอย่างเดียว | เปิด Emma บนหุ่น อนุญาตไมค์/เสียง ลองเรียกชื่อและคุย 10 รอบที่ 1–2 เมตร | ได้ยินคำทักทายตั้งแต่ต้น, ไม่มี echo วน, reconnect แล้วใช้ได้; ยังปิด movement |
| 4. Bridge แบบ mock | ใช้ vendor SDK MOCK และเชื่อม contract ด้านล่าง | รับ POI/คำสั่ง/callback ได้, token ผิดถูกปฏิเสธ, ไม่ขยับจริง |
| 5. การเคลื่อนที่จริง | หลังผู้ดูแลพร้อมปุ่มหยุด เปิด REAL ชั่วคราว ทดสอบจุดใกล้ 1 จุด → หยุด → ถึง → กลับฐาน | callback ตรงเหตุการณ์จริง; สั่งถึง/ชาร์จก่อนยืนยันไม่ได้ |
| 6. ความผิดพลาด | ช่างทดสอบเส้นทางถูกกั้น/ตัด Wi-Fi/แอปหลุดภายใต้การควบคุม | หุ่นหยุดในเครื่องเองและ Emma ไม่อ้างว่าถึง; หากไม่ผ่านให้หยุดทดสอบ REAL |
| 7. Smart Home | ตรวจไฟล์ IR ตรงอุปกรณ์ แล้วลองไฟ/แอร์ทีละตัวโดยผู้ดูแลเห็นของจริง | อุปกรณ์ตอบจริง; ฉากสมมติ lights/curtains/TV/AC ไม่ถือว่าทดสอบอุปกรณ์จริงผ่าน |

**คำว่า “หยุด” ผ่าน Gemini มีความหน่วงและอาจไม่รับระหว่าง Emma พูดเมื่อเปิด half-duplex อยู่ จึงต้องมีเส้นทางหยุดในตัวหุ่นและปุ่มกายภาพที่ช่างทดสอบแล้ว** แอป bridge ยังไม่ได้ทำ local stop/watchdog ให้ใน repo นี้

## Contract ที่มีจริงตอนนี้

ใช้ `/ws?token=<WS_TOKEN>` บน production ผ่าน TLS; bridge ต้องเป็นไคลเอนต์เสียงบน socket เดียวกัน ส่ง `robot_ready` หลัง SDK พร้อมพร้อม `token=<ROBOT_TOKEN>` ซึ่งเป็นคนละ secret กับ WS_TOKEN

```json
{"type":"robot_ready","token":"<ROBOT_TOKEN>","places":["Lobby"]}
{"type":"robot","action":"move_to_point","args":{"place":"Lobby"}}
{"type":"robot","action":"cancel_navigation","args":{}}
{"type":"robot","action":"go_home","args":{}}
{"type":"robot_arrived","place":"Lobby","ok":true}
{"type":"robot_arrived","place":"Lobby","ok":false}
```

เมื่อ `go_home` จบ callback ใช้ `place:"base"`; ถึงฐานไม่ได้ยืนยันกำลังชาร์จ ต้องตรวจจาก SDK จริง การเปิดเว็บธรรมดาไม่ทำให้เว็บเรียก AAR หรือส่ง `robot_ready` เองได้

**ข้อจำกัดที่ต้องแก้ร่วมกับ bridge ก่อนใช้งานเดินอิสระ:** production ยังไม่มี command ID/ACK/heartbeat/handler สำหรับ battery หรือ charging telemetry การส่ง socket สำเร็จหมายถึงส่งออกได้เท่านั้น และ callback เก่าของจุดหมายเดิมยังแยกจากงานใหม่ที่จุดเดียวกันไม่ได้ ตัวจำลองมี lifecycle ของตัวเองแต่ไม่ได้ทำให้ protocol จริงมีสิ่งเหล่านี้ การสลับ/จบสายเสียงยังทำให้ช่องสั่งหุ่นหลุด ต้องทดสอบ local cancel/watchdog ด้วย

## ตรวจซ้ำได้โดยไม่สั่งอุปกรณ์

รันในโฟลเดอร์โปรเจกต์:

```powershell
.venv-smoke\Scripts\python.exe -X utf8 scripts/check_robot_readiness.py --health
# เมื่อได้รับไฟล์แล้ว เพิ่ม --sdk "path\vendor.aar" --apk "path\bridge.apk"
```

Exit 2 = ยังขาด prerequisite, exit 0 = prerequisite ที่สคริปต์ตรวจผ่านเท่านั้น ไม่ใช่รับรองฮาร์ดแวร์ ไฟล์ APK มีอยู่ไม่ได้แปลว่าเข้ากันกับหุ่น ห้ามใส่ secret จริงลง CHANGELOG หรือเอกสาร

Production ใช้ `.venv-smoke\Scripts\python.exe run_server.py` ซึ่งอ่าน HOST/PORT/TLS จาก `.env`; อาจเปิด background features ตาม config จึงให้ผู้ดูแลเปิดขณะเตรียมทดสอบจริง หากพอร์ตใช้อยู่ให้ตรวจ process เดิมก่อนเปิดซ้ำ ดู [คู่มือจำลอง](robot-simulator.md) สำหรับหน้า 8010

## เอกสารอ้างอิงที่ตรวจประกอบ

- [Android adb](https://developer.android.com/tools/adb): USB debugging และยอมรับสิทธิ์เครื่อง PC; Android 10 ใช้ USB ก่อนเมื่อจะเชื่อม adb ผ่าน Wi-Fi
- [Android WebView PermissionRequest](https://developer.android.com/reference/android/webkit/PermissionRequest): ต้องจัดการคำขอสิทธิ์ capture ตาม origin/resource ที่อนุญาต
- [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia): ไมค์บนเว็บต้องใช้ secure context และสิทธิ์ผู้ใช้
- [Google Live API capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities): Gemini 3.1 ใช้ realtime text สำหรับข้อความสด; client content ใช้เริ่มประวัติ
- [ข้อค้นพบจากคู่มือหุ่น](robot-integration.md) และ [สัญญาเชื่อมต่อภาษาไทย](ต่อกับหุ่นยนต์%20Astronaut.md)
