# งานถัดไปของ Emma และหุ่นจำลอง

ค้นคว้าและตรวจ working tree: 8 กันยายน 2026 หลังแก้เสียงทักทายต้นสาย

ข้อเสนอหลัก: ทำวงจรเสียงให้เสถียรและเห็นสาเหตุเมื่อใช้งานไม่ได้ แล้วพัฒนาตัวจำลองให้ทดสอบเหตุการณ์ที่หุ่นจริงต้องเจอได้ รายงานนี้เป็นแผนงาน ยังไม่ได้ลงมือแก้โค้ดตามข้อค้นพบใหม่ ตัวเลขเกณฑ์ผ่านด้านล่างเป็นเป้าหมายที่เสนอ ไม่ใช่ผลวัดของระบบ

## สิ่งที่มีแล้วและไม่ควรเริ่มทำซ้ำ

- ฉาก Canvas 2.5D, กล้อง, ห้อง/เฟอร์นิเจอร์, route/progress และการสั่งผ่าน Emma ใช้ engine เดียวกัน
- ตัวจำลองมี command ID, ACK, arrival, timeout, fault injection และ JSON export แล้ว แต่สถานะอยู่ใน RAM
- Wake word ใช้ detector ในเครื่อง; provider เชื่อมเมื่อเรียกชื่อหรือกดเริ่ม และทดสอบ handshake Gemini จริงผ่านหลังแก้สิทธิ์โปรเซส
- การแก้ล่าสุดสร้าง playback ก่อนรอไมค์ รับ AudioContext ต่อจาก wake และมี Node regression เสียงต้นสาย
- มี `app/metrics.py`, usage, eval train/test split, `uv.lock` และ Windows CI แล้ว ข้อเสนอ metrics/lock ใน roadmap ฉบับแรกจึงทำไปบางส่วนแล้ว
- ผลทดสอบที่บันทึกไว้ล่าสุด: full suite 1,148 passed ก่อนแก้เสียงต้นสาย; หลังแก้เสียงต้นสาย targeted 224 passed พร้อม Node และ Chromium QA ไม่ควรเรียกว่า full suite ล่าสุดผ่าน 1,148 หลังทุกการแก้

## ข้อค้นพบใหม่จากโค้ด

| ข้อ | หลักฐาน | ระดับความมั่นใจ / ผลกระทบ |
|---|---|---|
| ช่วงรอฟังชื่อยังรอ resume ก่อนติดตั้งตัวปลดล็อกเสียง | `client/index.html`, `startWakeMode`: `await wakeCtx.resume()` มาก่อน `armAudioUnlock(wakeCtx)` | ยืนยันลำดับจากโค้ด; ถ้า browser รอ gesture อาจค้างก่อนติดตั้ง handler ต้องทดสอบใน browser ที่บล็อก autoplay จริง |
| callback ปิด socket เก่าอาจปล่อยอุปกรณ์ของสายใหม่ | `client/index.html`, `startCall` / `ws.onclose`: เรียก `releaseCallHardware()` โดยไม่ตรวจว่าเป็น socket ปัจจุบัน | ทำซ้ำใน Node VM แยกจากระบบจริงแล้ว: เปลี่ยน `ws` เป็น socket ใหม่และเรียก close callback เก่า ยังเกิด shared hardware release ไม่ได้ยืนยันว่านี่เป็นสาเหตุของภาพที่ผู้ใช้ส่งทุกครั้ง |
| ready ของเว็บยังไม่ใช่พร้อมคุยทั้งหมด | `app/robot_simulator.py`, `/health`: ตรวจ key presence และ wake model; สถานะเชื่อมต่อบนแผนที่คือ engine จำลอง | ยืนยันจากโค้ดและเหตุการณ์ WinError 5 ก่อนหน้า จึงควรแยกความพร้อมเว็บ/ไมค์/เสียงออก/Gemini/หุ่น |
| metrics จำลองไม่ได้ถูกเก็บผ่าน turnlog | `app/metrics.py`, `SessionMetrics.record` ส่งเข้า `turnlog.record`; `app/turnlog.py` คืนทันทีเมื่ออยู่ใน simulation context | เป็นการแยกข้อมูลที่ตั้งใจไว้ แต่ต้องมีที่เก็บ metrics เฉพาะตัวจำลองจึงจะวิเคราะห์เสียงที่ซ้อมได้ |
| กลับมาที่หน้าเกมอาจไม่มีวงรอบวาดภาพ | `client/robot-scene.js` ยกเลิก animation และ ResizeObserver เมื่อ pagehide ไม่มี pageshow สำหรับเริ่มใหม่ | ความเสี่ยงเมื่อ browser คืนหน้าจาก bfcache; ยังไม่ได้ทำซ้ำกับ bfcache จริงในรอบนี้ |

การตรวจ Node VM อ่าน callback จากไฟล์จริงแล้วรันกับ object จำลอง ไม่เปิดไมค์ ไม่เรียก API และไม่เปลี่ยนสายที่ผู้ใช้กำลังคุย

## ลำดับที่แนะนำ

P0 = ควรทำในชุดถัดไป, P1 = ต่อทันทีหลังฐานเสียงนิ่ง, P2 = ทำเมื่อข้อมูล/SDK ที่ต้องใช้พร้อม

| ลำดับ | งาน | ผลที่ผู้ใช้เห็น | เกณฑ์รับงานที่เสนอ |
|---|---|---|---|
| 1 — P0 | วงจรเสียงและเจ้าของสาย | เรียกครั้งแรกได้ เสียงไม่หายเมื่อเปิด/ปิดเร็วหรือเปิดหลายแท็บ | cold start, denied mic, suspended context, late close และสลับสาย 100 รอบ ไม่ทิ้งเสียงต้นสาย/ไม่ปิดไมค์ของสายใหม่ |
| 2 — P0 | รวมแผนที่และคุย Emma พร้อมหน้าความพร้อม | เปิดหน้าเดียว เห็นหุ่นและคุยได้ รู้ว่าติดที่ไมค์ ลำโพง หรือ provider | มีตัวทดสอบเสียงในเครื่องและสถานะแยกแต่ละชั้น; network/permission/key/quota failure แสดงวิธีแก้ตรงสาเหตุ |
| 3 — P1 | เก็บผลซ้อมเสียงแยกจากข้อมูลจริง | กดส่งออกครั้งเดียวแล้วเห็นว่าเรียกชื่อ/ต่อสาย/เสียงตอบช้าตรงไหน | journal เชื่อม session/turn/command; ไม่มี transcript/raw audio โดยค่าเริ่มต้น; จำกัดขนาดและส่งออกได้ |
| 4 — P1 | Browser CI และชุดทดสอบเสียงไทย/คุยนาน | ปัญหาเดิมถูกจับก่อนส่งให้ลอง | browser automation ไม่ใช้ API จริง; ชุดเสียงมีทั้งเรียกชื่อ/ไม่เรียกชื่อ/คำสั่ง; ทดสอบ reconnect ระหว่างเดินและระหว่างพูด |
| 5 — P1 | ผังแก้ไขได้และสิ่งกีดขวางจริงในโลกจำลอง | วางห้อง ประตู เขตห้ามเข้า และคนเดินตัดหน้าได้ | renderer และ planner ใช้ map schema เดียว; route ไม่ผ่าน blocked cell/ประตูปิด; replay เหตุการณ์เดิมให้ผลเดิม |
| 6 — P1 ก่อนฮาร์ดแวร์ | เชื่อม contract คำสั่งจากตัวจำลองไป adapter | รับคำสั่ง/กำลังเดิน/กำลังหยุด/หยุดแล้วแยกชัด | คำสั่งซ้ำ, callback เก่า, cancel ช้า, หลุดแล้วต่อใหม่ ไม่ถูกนับเป็นสำเร็จผิดงาน |
| 7 — P2 | ภารกิจพาชมหลายจุด | “พาชมห้องแล้วไปสระ” กลายเป็นลำดับงานที่ดู/หยุด/ข้ามได้ | หยุดแล้วไม่มีขั้นต่อไปแอบเริ่ม; บรรยายตามจุดที่ถึงจริงใน engine และใช้เนื้อหาที่เจ้าของอนุมัติ |
| 8 — P2 / รอ SDK | Android integration spike | รู้แน่ชัดว่าอะไรลงหุ่น และอะไรยังรันบนคอม | build demo ด้วย AAR ที่ตรงรุ่น; ยืนยัน POI/callback, WebView/audio, reconnect และการเปิดหลังบูตบน target จริง |

## 1. วงจรเสียง: งานแก้ที่ควรเริ่มก่อน

ให้แต่ละรอบคุยมี session generation ของตัวเอง callback/async result ต้องตรวจเจ้าของก่อนแก้สถานะหรือปล่อยอุปกรณ์ ครอบคลุมทั้ง socket และ `startWakeMode` ที่มี await หลายจุด ส่วนการเตรียม playback ก่อนไมค์ใน `startMic` ที่เพิ่งแก้ให้คงไว้

จัดการ unlock ก่อนรอ resume ทุกเส้นทาง และแสดงคำแนะนำเมื่อ browser ยังระงับเสียง นี่เป็นข้อจำกัดของ browser ที่ต้องรองรับ: MDN อธิบายการเริ่ม/resume Web Audio จาก user gesture จึงรับรองไม่ได้ว่าทุกเครื่องจะเปิดเสียงจากหน้าใหม่โดยไม่มีการแตะเลย [Web Audio best practices](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API/Best_practices)

สำหรับหลายแท็บ ใช้ Web Locks ช่วยเลือกแท็บที่ถือไมค์ พร้อม guard ฝั่ง server ที่มีอยู่แล้ว Web Locks ใช้ประสานงานได้เฉพาะ same origin จึงไม่ครอบคลุม `localhost` เทียบ `127.0.0.1`, คนละพอร์ต หรือคนละ browser; ควรให้ launcher ใช้ URL มาตรฐานเดียว และระบุข้อจำกัดนี้ในทดสอบ [Web Locks API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Locks_API)

## 2. หน้าเดียวและสถานะที่บอกความพร้อมจริง

รวมแผนที่ บทสนทนา ปุ่มเริ่ม/จบ และตัวควบคุมเสียงโดยมี audio owner เดียว ไม่สร้างไคลเอนต์ไมค์อีกตัวในหน้าเกม เพิ่มสถานะ: เว็บพร้อม → ไมค์ได้รับสิทธิ์ → ได้รับ PCM → playback running → wake ready → provider connected → หุ่นจำลอง/จริง

เพิ่มปุ่ม “ทดสอบลำโพง” ที่เล่นเสียงสั้นในเครื่องและให้ผู้ใช้บอกว่าได้ยินหรือไม่ กับปุ่ม “ทดสอบการเชื่อมต่อเสียง” ที่แจ้งก่อนว่าต้องใช้ API จริง การเปิดหน้าแผนที่ไม่ควรสร้างสายตรวจ provider อัตโนมัติทุกครั้ง สำหรับการรันผิดสิทธิ์ให้แสดงว่า local server เปิดได้แต่ outbound connect ถูกปฏิเสธ ไม่แนะนำให้เปลี่ยน key โดยไม่มีหลักฐาน

## 3. การวัดผลที่ใช้หาสาเหตุได้

เพิ่ม metrics sink เฉพาะ simulation แทนการเปิด turnlog ข้อมูลจริงกลับมา เก็บเหตุการณ์ wake detected, voice connecting/ready, PCM received, playback scheduled, context state, disconnect reason และ command lifecycle ไม่ต้องเก็บคำพูดเพื่อหาว่าเสียงหายช่วงไหน

ใช้ `getOutputTimestamp()` ประกอบเวลาที่ฝั่ง output device กำลัง render แล้วแยกจาก metric “schedule” เดิม ค่านี้ยังไม่ยืนยันว่าลำโพงเปิดเสียงหรือคนได้ยิน ต้องตรวจ acoustic loopback/ฟังจริงอีกชั้น [AudioContext.getOutputTimestamp](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/getOutputTimestamp)

รายงาน p50/p95 และจำนวนครั้งที่ล้มเหลวพร้อมตัวหาร เช่น เริ่มสาย 100 ครั้งล้มเหลว 2 ครั้ง ไม่แสดงเฉพาะค่าเฉลี่ยของครั้งที่สำเร็จ วัดบน clock เดียวต่อช่วงเวลา ไม่ลบ server timestamp จาก browser timestamp ตรงๆ

## 4. ชุดทดสอบที่ใกล้การใช้งาน

นำ Chromium QA ที่รันแบบชั่วคราวเข้า repo/CI ต่อจาก Node checks โดยดัก WebSocket และจำลองเสียงตอบทันที/ช้า/ขาดช่วง รวม mic permission delay, autopause, late callback และสองแท็บ Playwright รองรับ WebSocket mocking โดยไม่ต้องต่อ upstream จริง [Playwright WebSocketRoute](https://playwright.dev/docs/api/class-websocketroute)

ชุดเสียงที่เสนอ: เรียก “เอ็มม่า”, พูดชื่อพร้อมคำสั่ง, พูดคล้ายชื่อแต่ไม่ได้เรียก, เปลี่ยนจุดหมาย, หยุด, ไปจุดที่ไม่มี และถามสถานะ แยกคะแนน wake detection, เข้าใจคำสั่ง, เลือก tool และผล navigation ใช้เสียง fixture ก่อน; เสียงคนจริงเก็บเมื่อมีสิทธิ์และกำหนด retention แล้ว

เพิ่ม soak test ข้ามหลายรอบ reconnect และทดสอบ resume ระหว่าง tool/เสียง ระบบมี compression/resumption/GoAway แล้วจึงควรตรวจการทำงานร่วมกัน Google แยกอายุ session กับอายุ connection ชัดเจน; compression อย่างเดียวไม่ได้แทนการ reconnect [Gemini Live session management](https://ai.google.dev/gemini-api/docs/live-api/session-management)

## 5. พัฒนาฉากให้ซ้อมเหตุการณ์ได้

ย้าย POINTS/geometry จาก Python และ JavaScript ไป map schema ที่มี version, units, POI ID, ประตูและเขตห้ามเข้า ในช่วงแรกใช้ grid + A* กับขนาดตัวหุ่นจำลองก่อน เพิ่มคนเดินตัดหน้า, stop/slow zones และ seeded replay ให้ตรวจผลซ้ำได้ ค่าอัตราเร็วและระยะต่างๆ ต้องติดป้ายว่าเป็นสมมติจนวัดจากหุ่นจริง

Nav2 มีแนวคิดแยก collision monitor จาก planner และแบ่ง stop/slowdown/limit/approach zones ซึ่งใช้เป็นแบบออกแบบได้ แต่เอกสารระบุว่าไม่ใช่ระบบ hard real-time safety ที่ผ่านการรับรอง และยังไม่มีหลักฐานว่าหุ่น Aobo รุ่นนี้ใช้ ROS/Nav2 ได้ จึงไม่ควรผูก SDK ที่ยังไม่มีเข้ากับ ROS โดยสมมติ [Nav2 Collision Monitor](https://docs.nav2.org/rolling/configuration_and_development/configuration_guide/core_servers/collision_monitor/configuring_collision_monitor_node/)

เพิ่มทดสอบ pageshow หลังคืนหน้าจาก bfcache และจัดการหยุด/เริ่ม animation กับ polling ให้ครบ ปัจจุบัน pagehide หยุด renderer แต่ไม่มีทาง rearm สำหรับการคืนหน้าแบบนี้ [MDN pageshow](https://developer.mozilla.org/en-US/docs/Web/API/Window/pageshow_event)

## 6–8. จากคำสั่งในจอไปสู่หุ่นจริง

ใช้ protocol version, command UUID, issued/expires time และแยก accepted/executing/canceling/canceled/succeeded/failed/unknown โดย adapter ตรวจคำสั่งซ้ำและ callback ของงานเก่า เพิ่ม heartbeat และการซิงก์สถานะหลัง reconnect อย่า replay คำสั่งเดินโดยอัตโนมัติ การเก็บผลจำลองไว้ RAM ไม่เท่ากับ durable idempotency ของฮาร์ดแวร์

ROS 2 Actions เป็นตัวอย่างที่แยกตอบรับเป้าหมาย ผลสุดท้าย feedback และการร้องขอยกเลิกออกจากการยกเลิกเสร็จแล้ว ใช้แนวคิดได้โดยไม่ต้องนำ ROS มาเป็น dependency [ROS 2 Actions design](https://design.ros2.org/articles/actions.html)

ตอนนี้ `app/tools/robot_link.py` และ handler หุ่นจริงใน `app/session.py` ยังไม่ได้ใช้ contract command ID/ACK ของตัวจำลองครบ จึงควรทำ conformance suite ชุดเดียวให้ simulator และ adapter ผ่านก่อนเริ่มเดินจริง รายละเอียด vendor API ให้ยึด [คู่มือ integration ในโปรเจกต์](../robot-simulator.md) และตรวจ AAR จริง ไม่อ้างว่า ROS protocol คือ Aobo protocol

หลัง contract นิ่งจึงเพิ่มภารกิจพาชมหลายจุด เช่น ห้องตัวอย่าง → บรรยาย → สระ → กลับฐาน ให้ mission runner จัดลำดับ/หยุด/ข้ามอย่างชัดเจน ส่วน LLM แปลเจตนาและพูดกับผู้ใช้ ไม่ให้สถานะภารกิจอยู่ใน prompt อย่างเดียว

Android ควรเริ่มจาก demo ที่ผู้ขายให้จริง แล้วตัดสินใจว่าจะใช้หุ่นเป็น audio/UI/native bridge และให้ Python อยู่บนคอม หรือจะย้าย runtime ส่วนใด เอกสาร Android แสดงข้อกำหนด permission/foreground service สำหรับไมค์และข้อจำกัด background; ต้องเลือกกฎตาม Android/targetSdk/firmware จริง ไม่เอากฎ Android รุ่นใหม่ทั้งหมดไปเหมารวมกับ Android 10 ในคู่มือ [Android foreground service types](https://developer.android.com/develop/background-work/services/fgs/service-types)

## ชุดงานที่ควรเริ่ม

1. **ชุด A — เรียกติดและเสียงไม่หาย:** แก้สอง race ที่พบใหม่, ป้องกันหลายแท็บถือไมค์, เพิ่ม readiness/test-speaker และ browser regressions
2. **ชุด B — ซ้อมแล้ววัดผลได้:** รวมหน้าคุยกับแผนที่, metrics จำลองแยก, scenario runner, ชุดเสียงไทยและ reconnect soak
3. **ชุด C — พร้อมต่อ SDK:** map schema/obstacles, command contract/conformance, ภารกิจพาชม และ Android demo หลังได้ SDK

ยังไม่จัดการเปลี่ยนโมเดลเสียง ย้ายทั้งระบบไป ROS หรือเพิ่มกราฟิก 3D เต็มรูปแบบเป็นงานแรก เพราะเหตุการณ์ล่าสุดและโค้ดชี้ว่าจุดที่คุ้มแก้ก่อนคือ lifecycle/observability ส่วนภาพ 2.5D ที่ทำแล้วใช้เป็นฐานสำหรับเพิ่มฉากและเครื่องมือแก้ผังได้

## ขอบเขตการตรวจรอบนี้

- อ่าน working tree และเทียบ roadmap เดิมกับ CHANGELOG; ไม่อ่าน transcript/คลิปส่วนตัวหรือเปิดไมค์
- เปิดเอกสารปฐมภูมิที่อ้างในเนื้อหา 9 แหล่ง: MDN 4 หน้า, Playwright, Google, Nav2, ROS 2 และ Android
- ทำซ้ำ late-close callback ด้วย Node VM และตรวจลำดับ wake resume/unlock จากโค้ด; bfcache/autoplay บนเครื่องผู้ใช้ยังเป็นกรณีที่ต้องทดสอบเพิ่ม
- ไม่เรียก paid voice provider ไม่สั่งหุ่น ไม่รีสตาร์ท server และไม่ได้แก้ runtime ในงาน research นี้
