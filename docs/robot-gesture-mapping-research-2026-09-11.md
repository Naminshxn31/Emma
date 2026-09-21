# ผลค้นเลขท่าจับมือและผังข้อต่อเพิ่มเติม

วันที่ 11 กันยายน 2026 — ตรวจ APK v278 และ DEX ที่กู้จากโปรเซส Aobo ไว้แล้ว อ่านไฟล์ตั้งค่าบนหุ่นเพิ่มเติมเฉพาะจุด ไม่มีการเปิดแอป เรียกท่า เรียก Binder/broadcast ควบคุม หรือเขียน USB/serial ในรอบนี้

## สิ่งที่พบเพิ่ม

**พบชื่อ “จับมือ” ผูกกับเลข 6 ใน `IdleActionService.getActionName(int)` จริง** ไม่ใช่ข้อความ handshake ของการสื่อสาร แต่ยังไม่ยืนยันว่าเลข 6 บนบอร์ดของเครื่องนี้เป็นท่าจับมือ เพราะชื่อในอีกคลาสขัดกัน และยังไม่พบจุดเรียกใช้เมธอดแปลงชื่อนี้ใน bytecode ที่ตรวจ

### ตารางชื่อจาก switch จริง

| เลขที่เข้า getActionName | ข้อความที่คืน | แปลความหมาย | ชื่อ constant เลขเดียวกันใน ActionConstants |
| --- | --- | --- | --- |
| 5 | 敬礼 | ทำความเคารพ | `ACTION_TYPE_WAVE` |
| **6** | **握手** | **จับมือ** | **`ACTION_TYPE_NOD`** |
| 7 | 摆臂 | แกว่งแขน | `ACTION_TYPE_SHAKE` |
| 8 | 倒水 | เทน้ำ | `ACTION_TYPE_DANCE1` |
| 9 | 撕拉 | ฉีก/ดึง ตามชื่อจีน | `ACTION_TYPE_DANCE2` |
| 10 | 全部动作 | ท่าทั้งหมด | `ACTION_TYPE_BOW` |
| 11 | 摆臂行走 | แกว่งแขนขณะเดิน | `ACTION_TYPE_TURN` |
| 12 | 打招呼行走 | ทักทายขณะเดิน | `ACTION_TYPE_SALUTE` |
| 13 | 敬礼行走 | ทำความเคารพขณะเดิน | `ACTION_TYPE_POSE1` |

ชื่อที่กล่าวถึงการเดินเป็นข้อความในแอป ไม่ใช่หลักฐานว่าแพ็กเก็ตแขนสั่งฐานล้อร่วมด้วย ห้ามใช้ตารางนี้เป็นรายการท่าที่ทดสอบแล้วหรือ wiring map

หลักฐาน: `recovered-93886000.dex`, SHA-256 `1587455af53aa343415fe9ddbe2b24159b3130995bfc6e6ea7ae47357ed3cf00`; เมธอด `getActionName` code offset **3820232**, packed-switch ที่ byte offset 0, payload ที่ 124; key 6 ไปที่ byte offset **106** ซึ่งเป็น `const-string/jumbo 握手` และคืน String นั้น สร้างตารางด้วยการ decode switch targets ไม่ใช่เรียงข้อความแล้วเดาหมายเลข

### Target จริงที่พิมพ์เพิ่มเพื่อให้ตรวจซ้ำได้

เลข offset ข้างต้นมีฐานต่างกัน: `code_item` อยู่ที่ **`0x3a4ac8`**, header ยาว 16 bytes จึงเริ่มคำสั่งที่ **`0x3a4ad8`**; payload อยู่ที่ **`0x3a4b54`** ในไฟล์ DEX ที่กู้แยกแล้ว ไม่ใช่ offset ใน APK หรือ memory dump ก้อนใหญ่

คำสั่ง switch = `2b 03 3e 00 00 00`; payload header = `00 01 09 00 05 00 00 00` หมายถึง packed-switch, 9 entries, first key 5 ตามลำดับ little-endian

| Key | Target delta (หน่วย 16-bit) | Byte offset จากคำสั่ง switch | File offset | String ที่คืน |
| --- | --- | --- | --- | --- |
| 5 | 57 (`0x39`) | `0x72` | `0x3a4b4a` | 敬礼 |
| **6** | **53 (`0x35`)** | **`0x6a`** | **`0x3a4b42`** | **握手** |
| 7 | 49 (`0x31`) | `0x62` | `0x3a4b3a` | 摆臂 |
| 8 | 46 (`0x2e`) | `0x5c` | `0x3a4b34` | 倒水 |
| 9 | 42 (`0x2a`) | `0x54` | `0x3a4b2c` | 撕拉 |
| 10 | 39 (`0x27`) | `0x4e` | `0x3a4b26` | 全部动作 |
| 11 | 35 (`0x23`) | `0x46` | `0x3a4b1e` | 摆臂行走 |
| **12** | **31 (`0x1f`)** | **`0x3e`** | **`0x3a4b16`** | **打招呼行走** |
| 13 | 27 (`0x1b`) | `0x36` | `0x3a4b0e` | 敬礼行走 |

สูตรคือ `target = switch_address + signed_delta * 2` ไม่ได้บวกจาก payload และไม่ใช้ลำดับการวาง string blocks ในกรณีนี้ key 6: `0x3a4ad8 + 0x35 * 2 = 0x3a4b42`; ไบต์ที่เป้าหมาย `1b 03 80 d5 00 00 11 03` โหลด String ID `0xd580` (握手) ลง v3 แล้ว `return-object v3`

เครื่องมือตรวจซ้ำ: [inspect_aobo_gesture_switch.py](../scripts/inspect_aobo_gesture_switch.py) ใช้ Python standard library ค้น class/method/signature ผ่านตาราง DEX เพื่อหา code_item ก่อน ไม่ใช้ offset ที่เดาไว้ ตรวจด้วยว่าแต่ละ target โหลด String แล้ว return register เดียวกัน ผลเต็มพร้อม SHA-256, payload bytes, target bytes และ String IDs อยู่ใน [get-action-name-switch-targets.json](robot-interface-reference/get-action-name-switch-targets.json)

```powershell
python scripts/inspect_aobo_gesture_switch.py data/robot-inspection/service-interface-research-20260911/live-code/recovered-93886000.dex
```

คำสั่งนี้อ่านสำเนาในคอมเท่านั้น ไม่เรียกหุ่น ผลยืนยันเลขในเมธอดนี้ แต่ยังไม่ยืนยันว่า `#6GC1` เป็นท่าจับมือบนบอร์ด หรือว่าความขัดแย้งกับ ActionConstants เกิดจากรุ่นเก่า/รุ่นอื่น ชื่อ `NOD`/`SHAKE` เพียงอย่างเดียวก็ยังไม่พิสูจน์อวัยวะที่ขยับ

`ActionConstants` มีหมวด DEFAULT/GREETING ใช้ key 1 ซ้ำกัน การใส่ Map ครั้งหลังทับครั้งแรก; ตัวเลือกหมวดเหล่านี้เป็นคนละระดับกับเลขท่า ไม่ควรใช้ key หมวดเป็น servo channel หรือกลุ่มบนบอร์ด เมธอด `getCurrentActionGroup()` ที่อ่าน Map นี้ก็ยังไม่พบ caller ใน bytecode ที่ตรวจ

สำรวจ 30,433 method declarations ใน namespace Aobo/Bole รวม 29,829 เมธอดที่มี code body ไม่พบคำสั่งอ้างถึง `IdleActionService.getActionName` หรือ `getCurrentActionGroup` จาก body เหล่านี้ ข้อนี้ไม่พิสูจน์ว่าไม่มีการเรียกผ่าน native/reflection หรือส่วนที่ยังไม่กู้ได้ จึงให้สถานะเลข 6 เป็น **เบาะแสจากโค้ดที่ต้องยืนยัน**

## เส้นทางที่ใช้เลขกลุ่มท่าจริง

ผลทดสอบจริงต่อมาเวลาไทยประมาณ 14:55: กลุ่ม `3` ผ่าน API เดิมทำให้แขนด้านซ้ายของภาพยกและงอศอก เห็นหัวหันด้วย เจ้าของยืนยันว่าหุ่นยกเองไม่ได้ช่วยยก ได้ภาพ 156 เฟรมและ stop ACK จบรอบ locked ยังไม่ทราบช่องในกลุ่มหรือการขยับนิ้วรายนิ้ว และไม่ตั้งชื่อว่าจับมือ ดู [รายงานกลุ่ม 3 พร้อมภาพ](robot-group3-live-test-2026-09-11.md) ผลนี้เพิ่มเติมจากรายงานเจ้าของ/ภาพตัวเลือกด้านล่าง

อัปเดตจากเจ้าของหลังตรวจวิดีโอภายใน: เจ้าของแจ้งว่าแขนและนิ้วเคยขยับได้ปกติตามคู่มือ และส่งภาพหน้า console ตัวเลือกกลุ่มท่า `3` ตรวจโค้ด `client/robot-console.html` → `app/main.py` → `app/robot_arm.py:run_group` แล้ว ปุ่มนี้ใช้เลขกลุ่มบนบอร์ดจริง เมื่อเลือก 3 จะประกอบเฟรม `#3GC1` ไม่ผ่านตาราง ACTION_TYPE ด้านบน ข้อมูลนี้เป็นผลที่เจ้าของรายงานพร้อมภาพค่าที่เลือก ยังไม่ใช่ log หรือวิดีโอการเล่นกลุ่ม 3 ที่เราตรวจวัดเอง และยังไม่ยืนยันชื่อ “จับมือ” ช่องที่ขยับ หรือลำดับภายในกลุ่ม ดู [รายละเอียดหลักฐาน](robot-interior-video-analysis-2026-09-11.md)

`VoiceArmKeyWordBean` มี fields `keyWord`, `bindvalue`, `actionName`, `actionType`, `saveMusicPath`

- `VoiceCommandMatcher$8.getMatchResult()` อ่าน `getBindvalue()` ลง `capturedValue` และเก็บชนิด/ไฟล์สื่อแยกกัน
- เส้นทางเสียงใช้ `SiriAction.voiceArmActionGroup`; `FloatRecordService.executeVoiceArmAction()` สร้าง `#<group>GC1` ตามด้วย CRLF แล้วส่งผ่าน CH340 มีการส่งซ้ำหลังหน่วง 60 ms
- หน้า Arm Test โหลดรายการจาก SharedPreferences **`armkeywordsharepreferences`**, key **`armkeywordJson`** ผ่าน Gson เป็นรายการ `VoiceArmKeyWordBean`
- ดังนั้นชื่อท่าที่ผู้ใช้ตั้งกับเลขที่จะส่งต้องมาจากข้อมูล binding จริง ไม่ได้อ่านชื่อ constant `ACTION_TYPE_NOD` แล้วแปลงเป็นท่าจับมือโดยอัตโนมัติ

อีกจุดที่ต้องระวัง: `IdleActionService.sendArmToRobot(String)` อ่านเลขจาก `FacereSetting.voicegroupaction` (default `11`) หรือสุ่มตาม mode; String argument ใช้ประกอบ log ไม่ใช่เลขกลุ่มที่เมธอดรับไปส่งตรง ๆ ห้ามเรียกเมธอดนี้โดยคิดว่าการส่ง String `6` จะทำให้บอร์ดเล่นกลุ่ม 6 แน่นอน

## ตรวจไฟล์บนหุ่นเพิ่มเติม

ยืนยันเป็นอุปกรณ์เดิมด้วย serial ก่อนอ่าน ใช้เฉพาะ getprop/cat/ls ผ่าน su ที่มีอยู่แล้ว

| ตำแหน่ง | ผลอ่านจริง |
| --- | --- |
| `shared_prefs/armkeywordsharepreferences.xml` | ไม่พบไฟล์ (`No such file or directory`) จึงไม่ได้รายการชื่อท่า–เลขกลุ่ม |
| `shared_prefs/RobotSettings.xml` | ไม่พบไฟล์ จึงไม่มี custom_actions จากแหล่งนี้ให้ตรวจ |
| `shared_prefs/armSetting.xml` | อ่าน XML ได้ ตัวเลือก voice arm ปิดอยู่ |
| `shared_prefs/FacereSetting.xml` | อ่าน XML ได้ `faceregroupaction=6`, `navirungroupaction=17`; นี่คือค่าที่บันทึก ไม่พิสูจน์ท่าจับมือ |
| `.../aobocenter/armaction` | ls path ตรงคืน `Invalid argument`; ตรวจ parent `/storage/emulated/0/aobo/apps/aobocenter` ต่อแล้วไม่มีรายการโฟลเดอร์ armaction |
| `.../aobocenter/touch` | มี `start_success.mp3` ไม่พบไฟล์ผังท่าใน listing นี้ |

su ของเครื่องนี้คืน exit code 0 ได้แม้ cat/ls แจ้ง error จึงตรวจเนื้อหาตอบกลับและ parse XML เพิ่ม แก้ summary ให้ไฟล์ที่ไม่พบเป็น `read_error` ไม่อ้างว่าสำรองสำเร็จจาก exit code อย่างเดียว สองไฟล์ XML ที่อ่านได้เก็บเฉพาะในโฟลเดอร์ ignored ไม่คัดค่าที่ไม่เกี่ยวข้องลงรายงาน

## ผังข้อต่อ: สิ่งที่ตรวจแล้วและสิ่งที่ยังขาด

ยังไม่พบ mapping ช่องเซอร์โวกับไหล่/ศอก/ข้อมือ/นิ้วของเครื่องนี้ หลักฐานที่มีเป็น `[1, 11, 7, 8]` ในตัวเลือก Servo Board และคำสั่งตำแหน่งที่ประกอบจากเลขช่อง ไม่ได้ตั้งชื่อข้อต่อ

ค้น strings ของ DEX ทั้ง 7 รวมถึงไหล่/ศอก/ข้อมือ/ซ้าย/ขวาและคำจีน พบชื่อ `IMI_SKELETON_POSITION_*` ใน `com.hjimi.api.iminect.ImiSkeletonPositionIndex` รวมตำแหน่งข้อเท้า สะโพก ไหล่ ฯลฯ และสถานะ tracked/inferred เป็นข้อมูลโครงร่างของไลบรารี IMI ไม่พบหลักฐานผูก enum เหล่านี้เข้ากับ servo channel จึงไม่นำมาเป็น wiring map อีกคำ `INT_ELBOW` อยู่ใน schema ของ Microsoft Office connector จึงไม่เกี่ยวกับศอกหุ่น

ค้นชื่อสมาชิก archive 39 ไฟล์ รวม 82,871 รายการเพิ่มเติม ไม่พบชื่อไฟล์ที่ยืนยันว่าเป็นผังข้อต่อหรือไฟล์ท่าจับมือของเครื่องนี้ การค้นชื่อ archive ไม่ใช่การถอด binary ทุกไฟล์ และการไม่พบตารางฝั่ง Android ไม่พิสูจน์ว่าไม่มีข้อมูลใน firmware ของบอร์ดแขน

`SensorUtil$4` รับ `send.32servoboard.urtevent` และอ่าน `endcmd` เพื่อบันทึกว่า action group จบ ไม่ได้ส่งกลับชื่อข้อต่อหรือค่าตำแหน่งรายช่องในเมธอดที่ตรวจนี้

## หลักฐานและผลตรวจ

ข้อมูลดิบ/สคริปต์อยู่ใต้ `data/robot-inspection/service-interface-research-20260911/` (ignored):

- `gesture-method-hits.json`: 277 เมธอดที่มีชื่อ/คำสั่งเข้าข่ายจากการค้นรอบแรก
- `gesture-label-evidence.json`: switch labels, resources และรายการเมธอดที่ติดตาม
- `gesture-xrefs.json`: references, strings และ archive candidates จากการตรวจต่อ
- `gesture-live-files/`: ผลอ่านเฉพาะจุดและ summary ที่ตรวจเนื้อหาตอบกลับแล้ว
- `disassembly/`: เพิ่มคลาส IdleAction, ActionConstants, VoiceCommandMatcher, VoiceArmKeyWordBean และ SensorUtil ที่ใช้อ้างอิง
- `gesture-verification.json`: ผลตรวจ switch จาก raw bytes เทียบตาราง, hashes และ syntax ของสคริปต์ท้ายงาน

ไฟล์ที่นำไปอ้างอิงต่อได้: [gesture-labels.json](robot-interface-reference/gesture-labels.json) ระบุ `hardware_verified=false` และความขัดแย้งของชื่อชัดเจน ไม่มีรายการช่องข้อต่อที่เดาเพิ่ม ไม่มีการเปลี่ยน `/arm` ให้ส่งเลข 6 และยังไม่ได้อ่าน firmware หรือเรียกบอร์ดเพื่อดึงกลุ่มท่า

ผลตรวจท้ายงาน: อ่าน packed-switch จาก raw DEX ด้วย `struct` โดยไม่ใช้ Androguard ได้ชื่อและเลขทั้ง 9 ตรงกับผลถอดเดิมและ JSON อ้างอิง; SHA-256 ของ DEX ตรง manifest; ตรวจค่าคงที่ที่ขัดกันครบ; XML สองไฟล์ผ่าน parse/hash และสามผลอ่านที่เป็น error ถูกระบุตามจริง; AST parse ผ่าน 14 สคริปต์ ไม่รัน unit tests ของแอปเพราะแก้เฉพาะงานวิจัยและเอกสาร

**ข้อสรุปปัจจุบัน:** เลข 6 เป็นเบาะแสท่าจับมือที่มีข้อความรองรับในโค้ด แต่ยังไม่ใช่ท่าจับมือที่ยืนยันกับบอร์ด ผังรายข้อต่อยังไม่มีหลักฐานเพียงพอ การยืนยันถัดไปต้องได้ตาราง/สำเนาท่าจากบอร์ดหรือหลักฐานการต่อสายของเครื่องจริง ซึ่งยังไม่ได้ทำในรอบค้นข้อมูลนี้
