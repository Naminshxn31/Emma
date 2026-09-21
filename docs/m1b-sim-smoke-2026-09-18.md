# M1b-SIM smoke evidence — 2026-09-18

สถานะ: **browser simulator subset ผ่านตามผลด้านล่าง; M1b-SIM/Android และ M1 ยังไม่ผ่าน**

## ขอบเขตและวิธีทดสอบ

- เปิด FastAPI ใน thread ที่มี event loop แยกบน `127.0.0.1:8001` แล้วใช้ Playwright Chromium โหลด `client/index.html` จริง เชื่อม `/ws` จริง เปิด Gemini Live จริง และใช้ไมโครโฟนสังเคราะห์ของ browser; ปิด call debug, turn log, wake, Canva และ robot/chassis สำหรับรอบนี้
- ส่งคำขอ `show_plan` ชั้น 1 ให้ live provider ผ่าน text turn เพื่อวัดลำดับ provider → tool → WebSocket → fetch → SHA-256 → render → ACK → tool result → เสียงตอบ; text turn นี้ **ไม่ใช่** speech recognition จากไมค์จริง
- จำลอง 404/hash corruption ด้วย network interception และ drop/delay ACK เฉพาะใน browser test; กรณี protocol failure 5 รายการเรียก `tools.dispatch` ตรงผ่าน session/socket เดียวกัน จึงพิสูจน์ browser/server contract แต่ **ไม่พิสูจน์คำพูดของ live provider ทุก failure case**
- ค่าเวลาในตารางเป็น `time.monotonic()` ภายใน process ทดสอบ (วินาที) ไม่ใช่เวลานาฬิกา/เวลาเครื่องจริง; ไม่มี token, API key, transcript ลูกค้า หรือข้อมูลส่วนบุคคลในบันทึกนี้
- ใช้ชั้น 1/2 แทนชั้น 8/9 เพราะ metadata ผังชั้น 8 ยังเป็น `review-required`; ไม่เปลี่ยนสถานะ approval เพื่อให้ smoke test ผ่าน

## หลักฐาน timeline

| กรณี | `command_id` | เหตุการณ์ที่สังเกตได้ | ผล |
| --- | --- | --- | --- |
| Live happy path ชั้น 1 | `59945384ae3efc2ee352217b1ef6631a` | tool call 227792.537 → command 227793.090 → rendered ACK 227793.271 → `tool_result ok:true` 227793.271 → transcript เริ่ม 227794.281 → audio bytes ชุดแรก 227794.627 | ผ่านบน browser: audio 97 chunks; ไม่มี audio ของ turn นี้ก่อน ACK; ข้อความเริ่ม “I've displayed the floor plan...” |
| Live asset 404 | `4ff18224db53293fdb00b8db71f3c6f4` | tool call 227836.619 → command 227837.117 → `load_failed` ACK 227837.123 → `tool_result ok:false` 227837.125 → audio เริ่ม 227839.756 | ไม่ render; transcript ที่จับได้เริ่ม “I'm sorry, the display render…” ไม่ใช่คำยืนยันสำเร็จ; เก็บเพียงช่วงต้นของคำตอบ |
| Asset 404, direct dispatch | `91643f9663b18cb89ef8caed13e4ed7f` | command 227716.051 → `load_failed` ACK 227716.079 | `display render failed`; ภาพบน card = 0 |
| SHA mismatch, direct dispatch | `14197ce60d6e096210f51c25e24fab94` | command 227717.584 → `hash_mismatch` ACK 227717.588 | `display hash mismatch`; ภาพบน card = 0 |
| Drop ACK, direct dispatch | `1a8214a681275079dfe3b81c7ed1a0fd` | command 227717.901 → `display_cancel` 227729.908 | `display timeout`; ภาพบน card = 0 หลัง cancel |
| Late ACK, direct dispatch | `858a3013b336b97e2e2720fc66902ea7` | command 227879.767 → cancel 227891.772 → ACK `rendered` มาช้า 227893.912 | `display timeout`; pending = 0; ภาพบน card = 0 หลัง ACK ช้า |
| สองคำสั่งซ้อน ชั้น 1 → 2, direct dispatch | `1ee945e964808fb78132ee6b43974a08` สำหรับคำสั่งแรก | command ชั้น 1 เวลา 227744.826 → ACK ชั้น 1 เวลา 227746.497; คำสั่งชั้น 2 ได้ `display busy` | ไม่เอา ACK ชั้น 1 ไปยืนยันชั้น 2 แต่ยัง **ไม่** ทำ correction ชั้น 2 ให้สำเร็จ; เป็นข้อจำกัดของ flow ปัจจุบัน |

กรณี live happy path ใช้ model ที่ session ต่อสำเร็จจริงคือ `gemini-2.5-flash-native-audio-preview-12-2025` หลัง fallback จาก model ที่ตั้งไว้; รอบนี้ไม่ได้แก้ provider/model. เวลา ACK และ tool result เท่ากันเมื่อปัดสามตำแหน่งทศนิยม จึงยืนยันได้เพียงลำดับ event ที่รับใน browser และการที่ audio bytes ตามหลังทั้งคู่ ไม่อ้างความละเอียดระดับต่ำกว่ามิลลิวินาที

## สิ่งที่ยังต้องพิสูจน์ก่อนปิด M1b-SIM / M1

1. Android app `apps/emma-ai-voice` โหลด `/preview?app=1` (`client/voice-preview.html`) ไม่ใช่ `client/index.html`; หน้า preview ยังไม่มี handler `display_command`/`display_cancel`/`display.render_ack` และยังเรียก `showPlan(r)` จาก `tool_result` ที่ M1a ไม่คืน image แล้ว จึงยังไม่มีหลักฐานว่า Android WebView แสดงและ ACK ได้
2. มี AVD configuration `EmmaRobotPreview` แต่ไม่พบ emulator executable ใน Android SDK path ที่ตรวจ และ `adb devices` ไม่พบอุปกรณ์; ยังไม่ได้รัน Android Emulator หรือ kiosk จริง
3. Failure speech จาก live provider ตรวจจริงเฉพาะ asset 404; hash mismatch, no ACK, late ACK และสองคำสั่งซ้อนยังไม่มี live audio timeline ทุกกรณี ส่วนสองคำสั่งซ้อนยังไม่ตอบโจทย์การเปลี่ยนใจไปชั้นใหม่ให้สำเร็จ
4. ยังไม่ได้ทดสอบ live microphone/STT, Android audio playback, WebView lifecycle, showroom network หรือจอและลำโพงของหุ่นจริง; M1b-HW จึงยัง pending

ไม่เปลี่ยน source approval, model, prompt, Android app หรือ production feature ในรอบเก็บหลักฐานนี้
