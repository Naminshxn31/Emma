# ผลสำรวจหุ่นจริงและการเชื่อม condo-voice — 10 กันยายน 2026

เชื่อมถึง Android ผ่าน ADB และถึงแชสซีผ่าน REST ได้จริง เซิร์ฟเวอร์ HTTPS ของ
condo-voice ที่พอร์ต 8001 รายงาน `chassis.connected=true` แต่ยังไม่มี POI
และยังไม่ผ่านการตรวจปุ่มหยุดฉุกเฉิน การเชื่อมติดไม่ใช่หลักฐานว่าพร้อมเดินอัตโนมัติ

## ฮาร์ดแวร์ที่อ่านจากตัวจริง

| ส่วน | ผลตรวจ | ผลต่อการพัฒนา |
|---|---|---|
| แท็บเล็ต | ZC-3588A, Android 15, arm64-v8a | ใช้ข้อมูลเครื่องจริงเป็นฐาน ไม่ใช้ Android 10 ในคู่มือสินค้า |
| RAM | MemTotal 16,255,012 kB (ประมาณ 15.5 GiB) | ใกล้เคียงรุ่น RAM 16 GB ไม่ใช่ 4 GB ตามสเปกมาตรฐานในคู่มือ |
| พื้นที่ | `/data` ประมาณ 220G ว่าง 217G ขณะตรวจ | เป็นขนาดพาร์ทิชันที่อ่านได้ ไม่ใช่การยืนยันความจุชิปทั้งหมด |
| แชสซี | Slamtec / Slamware SDP | ใช้ REST adapter ใน `app/robot_chassis.py` ได้ |
| Firmware | `5.1.1-deb-for-aobo-hermes+20250226` | บันทึกเพื่อเทียบความเข้ากันได้ของ API |
| แอปผู้ขาย | `com.aobo.robot.ai3`, versionCode 278, `3.1.230528au3.sl.deliver.4m` | เป็นแอปที่ติดตั้งอยู่ ไม่ใช่ AAR สำหรับสร้างแอปเรา |
| USB audio | Bothlent UAC Dongle, ALSA card 0, capture 8 ช่อง, S16_LE, 16000 Hz | หมายเลข card ต่างจากค่าเริ่มต้น 1 ใน SDK ต้องตรวจเส้นเสียงจริงก่อนตั้งค่า |
| Serial | พบ ttyS0/1/3/4/6/8/9; ไม่พบ ttyUSB หรือ ttyACM | ยังระบุพอร์ตแขนไม่ได้ ห้ามเดาว่า ttyS ใดเป็นแขน |
| แบตเตอรี่ | 30%, not_on_dock, ไม่ชาร์จในช่วงที่ตรวจ | ค่านี้เป็น snapshot และเปลี่ยนภายหลังได้ |
| จุดหมาย | POI จากทั้ง single-map และ multi-floor เป็นรายการว่าง | ต้องสำรวจและบันทึกจุดหมายจริงก่อนใช้ `go_to_place` |
| Localization | quality 51 ในการอ่านครั้งหนึ่ง, mapping disabled | ไม่ตีความค่าครั้งเดียวว่าความแม่นยำเพียงพอ ต้องทดสอบกับแผนที่หน้างาน |

USB descriptor ที่ระบุ 8 ช่องไม่พิสูจน์จำนวนไมโครโฟนจริง 8 ตัว ช่องอาจเป็น raw,
processed หรือ reference audio ยังไม่ได้อัดเสียง ฟังเสียง ทดสอบ AEC หรือ barge-in
และไม่ได้เปิดกล้องเพิ่มในการสำรวจนี้

## เส้นทางเชื่อมที่ทำงานจริง

```text
condo-voice บน PC
    -> HTTP 127.0.0.1:11448
    -> ADB forward ที่มีอยู่
    -> relay บน Android ของหุ่น
    -> เครือข่ายภายในของหุ่น
    -> Slamware REST API บนแชสซี

หน้าจอ/ไมค์บน Android
    -> browser client ของ condo-voice ผ่าน HTTPS
    -> ระบบสนทนาบน PC

แขน/ศีรษะ/นิ้ว
    -> บอร์ดและพอร์ตที่ยังไม่ได้ระบุตัวจริง
    -> ต้องมี SDK/โปรโตคอลและขอบเขตการเคลื่อนที่ที่ตรงรุ่น
```

เส้นทางแรกอ่านค่าจริงได้และ production health เห็นแชสซีแล้ว เส้นทางเสียงบน
หน้าจอหุ่นยังไม่ได้รับรองครบวงจร การตรวจ HTTPS จาก Python สำเร็จเมื่อใช้
`certs/rootCA-android.crt` เป็น trust anchor โดยยังตรวจใบรับรองตามปกติ
สิ่งนี้ไม่พิสูจน์ว่า Android Chromium เชื่อถือ CA หรือเปิดไมค์ให้เว็บไซต์แล้ว
หน้าจอหุ่นที่อ่าน metadata ในรอบนี้เปิด RoboStudio อยู่

relay และ ADB forward มีอยู่ก่อนเริ่มงานรอบนี้ ไม่ได้สร้าง persistent service
หรือเปิดพอร์ตใหม่ ต้องตรวจการเชื่อมซ้ำหลังรีบูต และต้องออกแบบ watchdog บนฝั่ง
หุ่นก่อนปล่อยเดินแบบไม่มีคนดู การที่ PC แจ้ง timeout ไม่รับประกันว่าล้อหยุด

## ผลตรวจปุ่มหยุด

### ผลทดสอบกลับแท่นในเวลาต่อมา

ผู้ใช้ยืนยันเพิ่มเติมว่ามีคนดู ทางโล่ง และทดสอบวิธีหยุดได้แล้ว จึงทดลองกลับแท่น
หนึ่งครั้งตามคำขอ ไม่ได้เปิดสวิตช์ให้ AI เริ่มการเดินอัตโนมัติทั่วไป
พบ `homepose` และ `homedocks` จริง (แม้ POI สำหรับนำทางยังว่าง) ระยะจาก pose
ตอนเริ่มถึงพิกัดแท่นประมาณ 23 ซม. ค่านี้เป็นระยะบนแผนที่ ไม่ใช่ระยะวัดหน้างาน

ส่ง `GoHomeAction` พร้อม `flags=dock`, `charging_retry_count=1`,
`back_to_landing=false` ได้ action id 4, สถานะเริ่ม 0 แล้วเป็น 1
ครบเวลาเฝ้าติดตาม 90 วินาทียังไม่ชาร์จ จึงส่งยกเลิก ไม่ส่งเดินซ้ำ
ตรวจหลังยกเลิก: current action เป็น 404, action 4 เป็น
`status=4, result=-2, reason=aborted`; power ยังคง `not_on_dock`,
`isCharging=false`, `isDCConnected=false`, battery 20%

ผู้ใช้เห็นหุ่นเคลื่อนหรือหมุนแต่เข้าแท่นไม่สำเร็จ และยืนยันว่าแท่นเสียบไฟ มีไฟติด
และอยู่ตำแหน่งเดิม สาเหตุของการเข้าจอดยังไม่ทราบ; ไม่ถือว่า firmware รายงาน
failure เอง เพราะรอบนี้จบด้วย timeout ที่ผู้ช่วยกำหนดและสั่งยกเลิก
ไม่ได้ขยับ/เขียนตำแหน่งแท่นใหม่ ไม่ได้ปิด obstacle avoidance
การตรวจต่อควรดูทิศการเข้าจอด สัญญาณหาแท่น และหน้าสัมผัสชาร์จหน้างาน

หลักฐานอยู่ที่ `data/robot-inspection/20260910-docking-attempt.json`
สัญญา REST `/js/spec.js` ของตัวเครื่องยืนยันว่า ActionState ใช้
0 NewBorn, 1 Working, 3 Paused, 4 Done กับ result 0 Success/-1 Failed/-2 Aborted
จึงแก้โค้ดที่เดิมอ้าง bit flags ของ C++ SDK; regression ล่าสุด 96 passed
โหลดโค้ดแก้ไขเข้า `run_server.py` แล้วหลังยืนยันไม่มี current action
ตรวจ HTTPS health ผ่านโดยตรวจ CA จริง: chassis connected และ motion_enabled=false
power หลังโหลดโค้ดยังอยู่ที่ 20%/not_on_dock/not charging

ผู้ใช้แจ้งว่าพบปุ่มแต่ยังไม่เคยทดสอบ แล้วแจ้งว่าได้กดปุ่มแล้ว ขณะอ่านก่อนและ
หลังการกด `hasSystemEmergencyStop` ยังคงเป็น `false` และ health ไม่มี error
จึงยังสรุปไม่ได้ว่าปุ่มไม่ทำงาน หรือควบคุมส่วนใด อาจไม่เชื่อมกับสถานะนี้ก็ได้
ไม่ได้ทดสอบการหยุดระหว่างหุ่นเคลื่อนที่ และไม่มีการส่งคำสั่งเริ่มเดินจากผู้ช่วย

ระหว่างสำรวจเคยเห็น current action มีสถานะ active และภายหลังกลับเป็น HTTP 404
ยังไม่ได้ระบุแหล่งคำสั่ง/ชื่อ action ในจังหวะนั้น ไม่ควรตีความว่าเป็นหลักฐาน
ว่าล้อเคลื่อนที่จริง ต้องแยกสถานะงานออกจาก wheel-speed feedback

ก่อนทดสอบเคลื่อนที่ ให้ระบุปุ่มตามคู่มือ/ผู้ขาย ตรวจทั้งฐานล้อและแขนว่าอยู่ในวงจร
หยุดใด แล้วทดสอบร่วมกับคนประจำตัวหุ่นในพื้นที่ปิด ไม่มีขอบบันไดหรือคนในระยะเคลื่อน
ไม่ใช้คำสั่ง `go_home` เพื่อทดสอบปุ่ม เพราะสามารถเริ่มเคลื่อนที่ได้จริง

## ไฟล์ SDK และข้อมูลของผู้ขาย

### ตรวจเพิ่มเติมตามคำขอให้ยกมือ

ตรวจ UI ต่อ: เปิด ArmTestActivity ตรงผ่าน ADB ไม่สำเร็จ เพราะ activity
ไม่ได้ exported จากแอป ไม่ได้แก้ manifest หรือใช้ root เพื่อข้ามข้อจำกัด
เปิด SplashActivity ซึ่งเป็นทางเข้าปกติสำเร็จ แต่หน้าหลักแสดง voice lead,
voice explain, RoboStudio และ AnyDesk ยังไม่พบเมนู Arm Test ที่เปิดได้
กำลังรอวิธีเข้าหน้า Settings/后台 จากผู้ใช้หรือคู่มือผู้ดูแลของผู้ขาย
ผู้ใช้แจ้งว่ายังไม่ทราบวิธีเข้า ทดลองเมนูมาตรฐานและการแตะโลโก้ตาม resource
แล้วยังไม่พบหน้าตั้งค่าที่เปิดได้ จึงยังไม่ยืนยันพอร์ตหรือท่ายกมือ
ไม่มีการลองรหัสผู้ดูแล ปรับสิทธิ์แอป หรือส่งคำสั่งแขน

ผู้ใช้ยืนยันว่ายังไม่เคยลองยกมือจากแอป/รีโมต ตรวจ APK ของผู้ขายที่มีอยู่บนหุ่น
แบบอ่านอย่างเดียว พบ activity `com.aobo.aibot.ui.test.ArmTestActivity`
และ `ArmBindMusicActivity` พร้อม layout `activity_arm_test.xml`
ใน resource มีข้อความ “手臂动作测试” (ทดสอบท่าทางแขน), “手臂动作组”
(กลุ่มท่าทางแขน), “停止手臂动作” (หยุดท่าทางแขน), “舵机号码”
(หมายเลขเซอร์โว) และ “是否使用手臂” (เปิดใช้แขนหรือไม่)

นี่เป็นหลักฐานว่าแอปมีหน้าตั้งค่า/ทดสอบแขน แต่ยังไม่พิสูจน์ว่าตัวหุ่นติดตั้ง
และเชื่อมต่อบอร์ดแขนแล้ว หรือ action group หมายเลขใดแปลว่ายกมือ
มี service `com.aobo.aibot.aidl.MyService` / action `com.aobo.aidl.test`
แต่ยังไม่มีสัญญา AIDL หรือ API แขนที่ระบุพารามิเตอร์ได้ จึงยังไม่เรียก service
ไม่เปิดหน้าทดสอบ ไม่ส่ง serial bytes/PWM และไม่สั่งยกมือจริง

ตรวจซ้ำแล้วยังไม่พบ `/dev/ttyUSB*` หรือ `/dev/ttyACM*`
เก็บ APK อ้างอิงไว้ใน `data/robot-inspection/aobo-installed-reference.apk`
(ไม่เข้า Git), ขนาด 147,068,269 bytes, SHA-256:
`a4f3f1ec23686ec4a19d393af4d6d42c11ba4c0cff7ad7065e06246804c031f4`
ไม่ได้ติดตั้ง ถอนแอป หรือแก้ APK นี้ การตรวจถัดไปควรระบุพอร์ตที่หน้า Arm Test
ใช้จริงและรายชื่อท่าที่ผู้ขายตั้งไว้ พร้อมยืนยันวิธีตัดกำลังแขนก่อนทดลอง

- `/sdcard/SysQS/sdk` ที่เห็นในภาพมี `persistence` และไฟล์สำรองข้อมูลสองไฟล์
  ไม่พบ AAR/APK/source ภายในต้นไม้ที่ตรวจ จึงยังไม่ใช่ชุดพัฒนาที่นำไป build ได้
- `Downloads/aobo.zip`: 93 ไฟล์ เป็นสื่อ/การตั้งค่าของแอป ไม่พบไฟล์นามสกุล
  `.aar`, `.jar`, `.apk`, `.so`, `.java`, `.kt` ภายใน archive
- SHA-256 ของ aobo.zip:
  `63441140519cfc3ceb68e0398715ba1c72f91e981486cb4c22ca7e3e329badb8`
- APK ของผู้ขายมีอยู่ใน Download บนหุ่น แต่ยังไม่ได้ถอนแอป ติดตั้งแทน หรือดัดแปลง
- SDK จีนหน้า 21 ระบุค่าพอร์ตแขนเริ่มต้น `/dev/ttyUSB1` / 115200 แต่พอร์ตนี้
  ไม่ปรากฏบนตัวจริงขณะตรวจ จึงไม่ส่งข้อมูลทดลองเข้า serial port ใด
- คู่มือสินค้าไทยหน้า 7 ระบุแหล่งจ่ายไฟบอร์ดและเซอร์โวแยกกัน และช่องควบคุม
  20 ช่อง ขณะที่ตารางระบุข้อต่อส่วนบน 22 ข้อต่อ ต้องขอ wiring/channel map
  ของเครื่องที่ส่งมาจริง ไม่ใช้ตารางทั่วไปเป็นผังสาย

ไฟล์ที่ยังต้องได้สำหรับแขนและเสียงระดับ SDK: `aoborobotsdk-v2.0.aar` ที่รองรับ
firmware นี้, demo/source, dependency/ABI, wiring/channel map, joint limits,
คำอธิบาย stop/failsafe และเอกสารการอนุญาตใช้โมดูลเสียง ไม่ส่งข้อมูลหรือข้อความ
ไปหาผู้ขายโดยอัตโนมัติ

## สิ่งที่แก้ในโปรเจกต์

- อ่าน action และ pose แม้ไม่มีคำสั่งจาก condo-voice เพื่อไม่พลาดงานจาก RoboStudio
- motion ที่ยังไม่รู้/เพิ่งรับคำสั่งหยุด/ขาดการเชื่อมต่อ แสดงเป็น unknown
- แชสซีหลุดแล้วไม่ใช้สถานะ app ที่ค้างอยู่มาอ้างว่าเชื่อมได้
- snapshot เพิ่ม pose/docked และสถานะล็อกการเคลื่อนที่
- `ROBOT_CHASSIS_MOTION_ENABLED=false` เป็นค่าเริ่มต้น: ปิดการเริ่มนำทางและ
  กลับฐานจาก `robot_chassis.send()` แต่ยังส่งยกเลิกได้ ตัวเลือกนี้ไม่ใช่
  emergency stop ของฮาร์ดแวร์และไม่ควบคุมคำสั่งจาก RoboStudio/แอปผู้ขาย
- เพิ่มตัวตรวจอ่านอย่างเดียวและสำรองแผนที่ใน `scripts/inspect_robot_live.py`

ผลทดสอบโค้ดที่เกี่ยวข้องล่าสุด: 90 passed (chassis, robot, arrival readiness)
รอบแรกติดสิทธิ์ temporary directory ใน sandbox ก่อนเข้า test; รันซ้ำโดยตั้ง
`--basetemp` ไว้ใน workspace แล้วผ่าน ไม่มีการส่งคำสั่งเข้าหุ่นจากชุดทดสอบ

ผู้ใช้ยืนยันว่าหุ่นอยู่นิ่งโดยไม่ได้สั่งงาน จึงเพิ่มชื่อ/สถานะ action และให้ชนิดงาน
ที่ยังไม่ระบุเป็น unknown แทนการตีความทุก active action ว่ากำลังเดิน

โหลดโค้ดใหม่เข้าตัวเซิร์ฟเวอร์แล้ว โดยรีสตาร์ตเฉพาะ process `run_server.py`
ที่ยืนยัน path/command line ตรงกับโปรเจกต์ และใช้ `.venv-smoke` เดิม
ตรวจ HTTPS โดยเชื่อถือ CA ของโปรเจกต์หลังรีสตาร์ต: `ok=true`,
`chassis.connected=true`, `motion_enabled=false`, `places_known=0`,
แบตเตอรี่ล่าสุด 25% และยังไม่ชาร์จ เก็บผลไว้ที่
`data/robot-inspection/20260910-server-verified.json`
การตรวจนี้ไม่ได้เริ่มบทสนทนาหรือยืนยันการใช้ไมค์บนหน้าจอหุ่น

## หลักฐานและการตรวจซ้ำ

รายงานดิบอยู่ที่ `data/robot-inspection/20260910-live.json` ซึ่งถูกกันออกจาก Git
พร้อมแผนที่เดิม `20260910-live.stcm` ขนาด 366,512 bytes, SHA-256:
`3b96b507b45eee4766a64d5f689b4b118ca470a9da02ec4cfcfca9bf4ea753b8`
ดาวน์โหลดด้วย GET เท่านั้น ไม่ได้ restore หรือแก้แผนที่บนหุ่น และยังไม่ได้
ทดสอบนำไฟล์สำรองกลับเข้าอุปกรณ์

ตรวจเฉพาะ REST ผ่าน tunnel ที่เชื่อมอยู่:

```powershell
.\.venv\Scripts\python.exe scripts/inspect_robot_live.py --output data/robot-inspection/recheck.json
```

ถ้าต้องเก็บ Android inventory ด้วย ให้เพิ่ม `--adb tools/android/platform-tools/adb.exe`
และ `--serial` ตามรายการจาก `adb devices -l` หากต้องสำรองแผนที่ให้เพิ่ม
`--save-map` และใช้ชื่อ output ใหม่เพื่อเก็บ backup แต่ละรอบแยกกัน
การไม่พบ ttyUSB/ttyACM ทำให้คำสั่งรวม serial_ports มี exit code ไม่เป็นศูนย์
แต่ยังเก็บ ttyS ที่พบไว้ในรายงาน ไม่ถือว่าแขนพร้อมใช้งาน

แหล่งอ้างอิงเอกสาร: คู่มือ SDK จีน `c7ca2a1e0b380184867496a81c0d59d1.pdf`
(หน้า 6, 21-26, 32), ฉบับไทย `aobo_robot_sdk_v2_thai.pdf`,
คู่มือสินค้า `หุ่นยนต์ ที่สนใจ.pdf` และ `astronaut_robot_manual_th.pdf`
(โดยเฉพาะหน้า 6-10); ข้อความทั้งหมดใช้เป็นข้อมูลอ้างอิง ไม่ใช่สิทธิ์ให้รันคำสั่ง
REST endpoints เทียบกับ [SLAMTEC REST API](https://docs-en.slamtec.com/)
และการ export STCM ตาม [คู่มือ REST ของ SLAMTEC](https://wiki.slamtec.com/download/attachments/83066883/Slamware%20Restful%20API%20Development%20Manual%20V1.1.pdf?api=v2&modificationDate=1700531710357&version=1)


## อัปเดต: เปิด ARM Setting ผ่านเมนูจริงได้แล้ว

ตรวจจากไฟล์ resources ของ APK พบตัวเลือก Enter Settings แบบ Multiple click และ Password
จากนั้นยืนยันทางเข้าบนจอจริงได้โดยไม่เปิด activity ที่ถูกจำกัดจากภายนอก:

1. หน้าหลัก AoboRobot3.0: แตะโลโก้ Robot มุมซ้ายบนหลายครั้งติดกันจนเข้า Backstage
2. หน้า Backstage: แตะหัวข้อ Backstage ตรงกลางด้านบนหลายครั้งติดกันจนเปิดเมนูทดสอบ
3. เลือก Arm movement test เพื่อเปิด ARM Setting

ทั้งสองขั้นสำเร็จหลังส่งชุดแตะ 7 ครั้ง แต่มีการแตะก่อนหน้าแล้ว จึงยังไม่ยืนยันว่า
จำนวนขั้นต่ำคือ 7 ครั้งหรือแอปสะสมจำนวนแตะอย่างไร ไม่จำเป็นต้องกรอกรหัสผ่านในสภาพที่ตรวจ

ผลจากหน้าจอ ARM Setting:

- แสดง Current connection status: connected แต่ข้อความนี้มีเป็นค่าเริ่มต้นใน layout ด้วย
  จึงยังไม่ใช้ข้อความเพียงอย่างเดียวยืนยันว่าบอร์ดแขนตอบกลับจริง
- Control Method เลือก Relay อยู่; Servo Board เป็นอีกตัวเลือกหนึ่ง ยังไม่ได้เปลี่ยน
- Voice control action group และ Arm movement with background music แสดงสวิตช์ปิด
- มี Send, stop, Release Force และ Power On; ยังไม่ได้กดปุ่มเหล่านี้
- Selective action เปิดตัวเลือกหมายเลข Action group (เห็น 20/21/22) ไม่มีชื่อท่า
  ปิดด้วย Back โดยไม่กด Confirm; ช่อง Send the test ยังคงค่า 1
- Show all action instructions เปลี่ยนเป็น hide แต่ไม่มีแถวคำสั่งปรากฏ
- แตะ Action บนหัวหน้าแล้วไม่พบหน้าใหม่หรือข้อความตอบกลับใหม่
- หน้า Setting > Navi Settings แสดงเลขชุดท่า idle=11, speaking=21,
  navigation speaking=21 และ walking=17; ไม่ถือว่าเลขเหล่านี้เป็นท่ายกมือ

ตรวจโฟลเดอร์เพิ่มเติมตามภาพ Files: /sdcard/1 มี APK เครื่องมือ serial/terminal และ
Aobo รุ่นเก่า; backups/apps, One_Click_Installer และโฟลเดอร์สื่อใน zblibrary.demo
ไม่พบไฟล์ในการตรวจครั้งนี้ ส่วน song*.txt ใน aobo/apps/aobocenter/music เป็นเนื้อเพลง
ภาษาอังกฤษ/จีนใน voice_config มีคำสั่งเดิน/นำทาง แต่ไม่มีการผูกชื่อท่ายกมือที่ตรวจพบ
ไม่ได้ติดตั้ง APK รุ่นเก่าหรือส่งข้อมูลเข้าพอร์ต serial

หลักฐานที่ไม่เข้า Git: data/robot-inspection/apk-resource-map.json,
apk-layouts.json, arm-setting-found.png และ arm-setting-found.xml
ภาพและ UI hierarchy ยืนยันว่าเข้าหน้าทดสอบแขนได้จริง แต่ยังไม่ทดสอบการขยับแขน
ขั้นต่อไปคือยืนยันความหมายเลขท่าและเส้นทาง Relay/Servo Board ของเครื่องนี้ก่อนส่งท่ายกมือ


## ตรวจคำขอท่าจับมือเพิ่มเติม

- ค้น APK resources และไฟล์ข้อความ/config/workbook ใน aobo.zip 20 ไฟล์ด้วยคำ
  จับมือ/握手/handshake/shake hand/抬手/举手 ไม่พบ mapping หมายเลขท่าจับมือ
- พบ id/btn_shakehand แต่ layout activity_test_lora.xml ระบุข้อความ check status
  จึงเป็นปุ่มตรวจสถานะ LoRa ไม่ใช่ท่าทางแขน
- Android dumpsys usb พบ USB Serial VID:PID 1A86:7523 และ Silicon Labs
  CP2102 USB to UART Bridge Controller 10C4:EA60 ขณะตรวจ การไม่พบ ttyUSB/ttyACM
  จึงไม่เพียงพอที่จะสรุปว่าไม่มีอุปกรณ์ serial; ยังไม่ระบุว่าตัวใดเชื่อมบอร์ดแขน
- ภาพ ARM Setting ครั้งล่าสุดพบข้อความ #CC พร้อมเวลาต่อเนื่องใน Action message prompt
  ยังไม่ทราบความหมายหรือยืนยันว่าเป็นคำตอบจากบอร์ดแขน ไม่ถือเป็น ACK ของท่าจับมือ
- พบ UI hierarchy ค้างจากการ dump ไม่สำเร็จ จึงตรวจภาพ screenshot และ resumed activity
  ประกอบ; หน้าจอเปลี่ยนระหว่างตรวจ จึงพักการส่ง input เพื่อประสานกับผู้ใช้
- ไม่มีการกด Send/Power On/Release Force เปลี่ยน Relay/Servo Board หรือส่งท่าจับมือ


## อัปเดต: สำรองข้อมูลที่เข้าถึงได้และจัดทำรายการระบบ

สำรองไว้ใน `data/robot-inspection/backup-20260910-174700/` ซึ่งถูก ignore โดย Git
รายงานอ่านง่ายอยู่ใน `README-ข้อมูลหุ่นและผลสำรอง.md` พร้อม `backup-summary.json`

- พบ shared storage 337 ไฟล์ สำรองได้ 331 ไฟล์ รวม 1,033,462,702 bytes
- เทียบ SHA-256 กับต้นทางผ่านครบ 331 ไฟล์; log หนึ่งไฟล์เปลี่ยนขณะสำรอง จึงคัดลอกใหม่และตรวจผ่าน
- สำรอง APK แอปที่ติดตั้งเพิ่ม 14 แอป รวม 470,643,361 bytes และเทียบ hash ต้นทางผ่านทั้ง 14
- อ่านรายการแพ็กเกจทั้งหมด 150 แพ็กเกจ; ค่าระบบ system/global/secure จำนวน 50/133/101 รายการ
- ทำ content catalog สำหรับไฟล์ที่คัดลอกได้ทั้งหมด: text 136, binary 161, zip-container 27,
  binary/large 3 และ SQLite 4; อ่านข้อความ/JSON, รายการ archive และ schema ฐานข้อมูลที่อ่านได้
- เก็บแผนที่สดจาก chassis 366,512 bytes และไฟล์แผนที่ในแอป 372,262 bytes แยกกัน
  แผนที่ chassis มี SHA-256 ตรงฉบับที่สำรองไว้ก่อนหน้า; แผนที่แอปมี hash ต่างกัน ยังไม่สรุปความต่างเชิงพื้นที่
- เก็บค่าแท่น ความเร็วสูงสุด กลยุทธ์นำทาง เส้น/พื้นที่เสมือน เซนเซอร์ เครือข่าย สถิติ และ API spec
- เก็บข้อมูล Android USB/audio/camera/sensors/input/services และ log ที่มีอยู่ ไม่เปิดบันทึกเสียงหรือวิดีโอใหม่
- Log ยืนยันเคยมี ttyUSB10/11 แล้วรายงาน disconnected; USB inventory ยังเห็น CP2102/USB Serial
  ไม่สรุปว่าคือสายหลุดหรืออุปกรณ์ใดควบคุมแขน สำรองโปรแกรม ttyusb-scan และ init config ไว้โดยไม่รันหรือแก้
- แบตเตอรี่ chassis ตอนจบ 5%, isCharging=false, isDCConnected=false; แจ้งผู้ใช้ให้ต่อเครื่องชาร์จแบบสายแล้ว

ข้อจำกัด: ข้อมูลส่วนตัว Aobo อ่านไม่ได้และ run-as แจ้งไม่ debuggable; ไฟล์ VoiceNote 6 ไฟล์
กับไดเรกทอรี cache 2 แห่งถูกจำกัดสิทธิ์; ไม่ใช่อิมเมจระบบทั้งเครื่องหรือ snapshot เวลาเดียวกัน
และยังไม่ทดสอบ restore บนเครื่องจริง ไม่ได้ root หรือเปลี่ยนสิทธิ์/ข้อมูลต้นทาง
การสำรองครั้งแรกผ่าน USB ขาดกลางทาง เก็บไว้ในชื่อ INCOMPLETE-DO-NOT-RESTORE
และสำรองชุดที่ตรวจผ่านใหม่ผ่านเครือข่ายเดิม ไม่ใช้ archive แรกอ้างว่าสำรองครบ

หลักฐานตรวจสอบ: `shared-integrity.json`, `apk-integrity.json`, `MANIFEST.sha256.json`
ซึ่งรวมรายการไฟล์สำรองบนคอม 456 ไฟล์ (ไม่รวม manifest เองและ archive ที่ไม่ครบ)
ไม่มีการแก้ runtime code หรือส่งคำสั่งมอเตอร์ในรอบนี้ ไม่รัน unit tests ซ้ำสำหรับงานสำรอง

## ตรวจคำสั่งแขน/สีหน้าและจัดทำรายละเอียดไฟล์

รายงานอ่านง่ายและรายการไฟล์อยู่ใน `data/robot-inspection/analysis-arm-head-20260910/`
แยกจาก backup เดิมเพื่อไม่เปลี่ยนชุดหลักฐานต้นทาง

- ตรวจภาพ SDK จีนหน้า 21–23: มี `connectArm`, `listArmSerialPorts`, `isArmConnected`,
  `setArmPosition`, `executeArmActionGroup`, `stopArm`, `stopArmAll`, `verifyArm` และ `disconnectArm`;
  เป็นอินเทอร์เฟซที่ระบุในคู่มือ ยังไม่มี library/runtime และ mapping ท่ายกมือหรือจับมือที่ยืนยันกับบอร์ดจริง
- `verifyArm()` มีการส่งคำสั่ง ไม่ใช่ query อย่างเดียว; `loopCount=0` ในคู่มือคือวนไม่จำกัด
- APK มี `activity_face_expression_new.xml`, `activity_faceexpression.xml`, `double_screen.xml`
  พร้อม ImageFrameCustomView/GifImageView/SurfaceAnimView และตัวเลือก doublescreen/faceexpress
- shared storage มีภาพใน faceexpression/normal, smile, custom รวม 16 ไฟล์ (10 SHA-256 ที่ไม่ซ้ำ)
  เปิดตัวอย่างชุดละหนึ่งภาพพบภาพดวงตา; รายการ FACE ใน voice JSON เป็นคำที่รู้จัก ยังไม่ใช่ API จอที่ทดสอบแล้ว
- Android display dump มีจอ Built-in Screen 1080×1920 และ screencap แบบ VIRTUAL ของ AnyDesk;
  ไม่ใช่หลักฐานว่าจอบนหัวเป็นจอจริงที่สองที่ Android เรียกใช้ได้ และยังไม่ระบุตำแหน่งจอจริงจาก dump อย่างเดียว
- สร้างคำอธิบายทุกไฟล์ shared storage ที่สำรวจพบ 337 รายการ (331 อ่านได้, 6 ติดสิทธิ์),
  ตาราง APK 14 แอป, รายการไฟล์ประกอบ 121 ไฟล์ และสารบัญ APK/ZIP 39 ไฟล์ รวม 82,871 สมาชิก
  พร้อมระบุว่าส่วนใดอ่านเนื้อหาแล้วและส่วนใดอนุมานจากชื่อ/เส้นทาง ไม่อ้างว่าถอดโค้ดไบนารีครบ
- ตรวจ hash สำเนา shared 331 ไฟล์กับหลักฐานต้นทางที่บันทึกไว้ตรงครบ และ APK 14 ไฟล์ตรง hash เดิม;
  ไม่อ่าน hash สดจากหุ่นในรอบนี้ ไม่แก้ค่าหุ่นหรือส่งคำสั่งเคลื่อนไหว
