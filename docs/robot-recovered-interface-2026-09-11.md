# Interface Aobo ที่ถอดได้จากหน่วยความจำ

> **ผลค้นต่อ:** พบ label `6 → 握手 (จับมือ)` แต่ชื่อขัดกับ ActionConstants และไม่พบ caller ใน bytecode ที่ตรวจ จึงยังไม่ยืนยันกลุ่มบนบอร์ด ดู [รายงานเลขท่าและผังข้อต่อ](robot-gesture-mapping-research-2026-09-11.md)

> **การสร้างท่าเองและพฤติกรรมหลังหยุด:** พบขั้นตอน Add/Download ในคู่มือ Torobot และเส้นทาง Aobo ที่อาจส่งกลุ่ม 99 หลังรับ stop ACK ตามเงื่อนไข ดู [วิเคราะห์การสร้างท่าเอง](robot-custom-motion-design-2026-09-11.md) ไม่ได้ทดลองกับบอร์ด

11 กันยายน 2026 — `com.aobo.robot.ai3`, versionCode 278, versionName `3.1.230528au3.sl.deliver.4m`

## ผลและขอบเขต

อ่านโค้ดที่คลายการห่อได้ **DEX 7 ไฟล์, 31,328 class definitions รวมคลาส Aobo 5,616 รายการ** พบ AIDL จริง คำสั่งแขน และช่องทางแสดงภาพ/วิดีโอ ไม่ต้องรอผู้ขายเพื่อทราบ interface เหล่านี้แล้ว แต่ยังไม่ใช่หลักฐานว่าควบคุมข้อต่อทุกตัวหรือทำท่าจับมือได้จริง

ข้อห้ามสั่งการเคลื่อนไหวยังมีผล ใช้ su ที่มีอยู่แล้วอ่าน maps/fd/preferences/databases/memory ไม่ติดตั้ง root ไม่เปลี่ยน SELinux หรือค่าหุ่น หลังแบตหมดและผู้ใช้เปิดหุ่นใหม่ ได้ตรวจอุปกรณ์และ PID ใหม่ก่อนอ่านต่อ **ผู้ใช้อนุญาตให้เปิด Aobo โดยมีคนดู จึงเปิด SplashActivity หนึ่งครั้ง** ไม่มีการเรียก Binder methods, broadcasts ควบคุม, ท่าหรือเขียน USB/serial เอง

**ผลข้างเคียงที่พบภายหลัง:** `SerialToothManagerCH340.usbInit()` มีการเขียน `#99GC1\r\n` หลังเปิดพอร์ตสำเร็จ การเปิดแอปจึงอาจกระตุ้นแขนเอง ไม่ได้ตรวจวัดว่า branch นี้ส่งถึงบอร์ดจริงหรือหุ่นขยับในการเปิดครั้งนี้ ไม่อ้างว่าการเปิดแอปเป็นการอ่านอย่างเดียว

ติดตั้ง Androguard 4.1.4 เฉพาะโฟลเดอร์วิเคราะห์ในคอม ไม่รันโค้ด Android ที่คัดลอกมา ไม่ติดตั้ง APK Emma ไม่แก้ runtime `/arm` และไม่ติดต่อผู้ขาย

## 1. สัญญา AIDL

| รายการ | ค่าจาก Stub/Proxy |
| --- | --- |
| Component | `com.aobo.robot.ai3/com.aobo.aibot.aidl.MyService` |
| Action ใน manifest | `com.aobo.aidl.test` |
| Descriptor | `com.helang.lib.IMyAidlInterface` |
| Transaction 1 | `void sendMessage(String tag, String message)` |
| Transaction 2 | `void registerListener(IMyAidlCallBackInterface listener)` |
| Transaction 3 | `void unregisterListener(IMyAidlCallBackInterface listener)` |
| Callback descriptor | `com.helang.lib.IMyAidlCallBackInterface` |
| Callback transaction 1 | `void callback(String type, String message)` |

String arguments ส่งตามลำดับข้างบน, listener ใช้ strong Binder, transact flags เป็น 0 และอ่าน reply exception จึงเป็น synchronous AIDL ไม่ใช่ `oneway` ไม่มี Parcelable ที่ต้องเพิ่มในสองสัญญานี้

สร้างกลับเป็น [IMyAidlInterface.aidl](robot-interface-reference/aidl/com/helang/lib/IMyAidlInterface.aidl) และ [IMyAidlCallBackInterface.aidl](robot-interface-reference/aidl/com/helang/lib/IMyAidlCallBackInterface.aidl) ชื่อพารามิเตอร์ตั้งให้อ่านง่าย ไม่ใช่ source ต้นฉบับ ยังไม่ได้ compile ด้วย Android SDK หรือ bind จริง

MyService ประกาศ `exported=true`, ไม่พบ permission บังคับใน service/application manifest ที่ตรวจ และ `onBind()` คืน Binder นี้ ส่วน `SerialdataService` กับ `DoubleScreenService` คืน null การเปิดให้ bind ใน manifest ไม่รับรองว่าทุก feature เปิดใช้หรือทำงานสำเร็จ

### tag ที่ MyService.dealVoice รับ

| tag | message / การส่งต่อ | ข้อจำกัด |
| --- | --- | --- |
| `startrecord` | `ROBOT_SERVICE_START_RECORD` | เริ่มรับเสียง มีตัวนับ/ข้อจำกัดการใช้งาน |
| `stoprecord` | `ROBOT_SERVICE_STOP_RECORD` | หยุดรับเสียง |
| `starttts` | ข้อความ → `ROBOT_SERVICE_TTS_SPEAK` | helper ตั้ง `isPlayAction=true` อาจเล่นท่าประกอบ ไม่ใช้เป็น probe ที่รับรองว่าไม่ขยับ |
| `stoptts` | `ROBOT_SERVICE_TTS_STOP` | branch ตรวจ message ไม่เป็น null |
| `queryaiui` | ข้อความ → `ROBOT_SERVICE_QUERYAIUI` | เข้า engine สนทนา ไม่ใช่ health check |
| `openpage` | ชื่อหน้า → `ROBOT_SERVICE_STARTACTIVITY`, String extra `robotPageName` | ยังไม่มี allowlist หน้าที่เปิดได้จริง |
| `wakeup` | ค่า → `ROBOT_SERVICE_THIRDAPP_WAKEUP`, String extra `direct` | ตัวรับมีเส้นทางส่งพอร์ต |
| `robotaction` | JSON ตามด้านล่าง | เกี่ยวกับมอเตอร์/เซนเซอร์ ไม่ใช้เป็นการอ่านสถานะ |

ทุกครั้งที่เข้า `dealVoice` มีการตั้ง `StaticClass.isEnterThirdApp=true`; ไม่ควรอ้างว่าเป็นการอ่านอย่างเดียวจากชื่อ tag

`robotaction` อ่าน `robotRunType` และ `runTime` ด้วย `JSONObject.getString()` แล้วส่ง `ROBOT_AIDL_SERVICE_ROBOTRUN` พร้อม String extras `robotRunType`, `runTime`, `speed` โดย **speed ถูกตรึงเป็น `"1"`** ไม่อ่าน speed จากผู้เรียก

`FloatRecordService$RobotServiceReceiver` ตรวจค่า `forward`, `backward`, `turn_left`, `turn_right`, `turn_around`, `robot_stop` จริง สี่ท่าแรกแปลง runTime เป็น integer แล้วหาร 500; `turn_around` เลือกทิศแบบสุ่มและคูณค่าอื่น จึงไม่สรุปว่าทุกค่าใช้หน่วยเดียวกัน เส้นทางเลือก packet ตาม `motorType` ยังไม่พิสูจน์ว่าตรงกับฐาน Slamware ที่ติดตั้งอยู่

ถ้า JSON มี `robotSensorType` จะส่ง `ROBOT_AIDL_SERVICE_ROBOTSENSOR` พร้อม String extras `robotSensorType`, `sensorAction` โดยอ่าน `sensorAction` ก่อน ถ้าไม่มีอ่าน `ultrasound_distance` ถ้าไม่มีทั้งคู่ใช้ `"noaction"` ทั้งสอง branch อาจทำงานจาก JSON เดียวกัน ยังไม่ยืนยันรายการ sensor type ครบ

Callback ภายในใช้ broadcast `ROBOT_SERVICE_THIRDAPP_CALLBACK`, String extras `type`, `message` แล้วส่งต่อ listener พบ type `0`–`3` ในเส้นทางที่ตรวจ ยังไม่ตั้งชื่อความหมายครบโดยเดา และไม่ได้รับ callback จากการ bind จริง

## 2. แขนและ USB

เส้นทางที่พบ: `ArmTestActivity / BaseActivity` → `FucUtil.getArmRobotPacket(String)` → `SerialdataService.sendCmdtowhichUart(byte[], 29987)` → `sendCmdTwo(byte[])` → broadcast → `SerialToothManagerCH340.startAction(byte[])` → USB write

| รายการ | ค่าที่พบ |
| --- | --- |
| Dynamic broadcast action | `send.usbserial.cmd.ch340` |
| Extra | `cmd` ชนิด **byte[]** ไม่ใช่ String |
| Receiver | `SerialdataService$1`, ลงทะเบียนใน `onCreate()` |
| Encoding | GBK, `getArmRobotPacket` ไม่เพิ่ม envelope/checksum |
| CH340 settings | 115200 baud, 8 data bits, 1 stop bit, parity 0 |
| Driver API | `com.hoho.android.usbserial.driver.UsbSerialPort` |

SerialdataService ไม่ exported เป็นข้อจำกัดของ component; dynamic receiver เป็นอีกช่องทาง พบการลงทะเบียน receiver แบบสองพารามิเตอร์ ยังไม่ทดสอบว่า Android ยอมรับ broadcast จาก UID ของ Emma หรือไม่ ไม่ได้แย่งสิทธิ์ USB จาก Aobo

### Frames ที่ยืนยันจากโค้ด

เอกสารอ้างอิงเท่านั้น ไม่ได้ส่งไปทดสอบ `\r\n` หมายถึง bytes `0D 0A` หนึ่งคู่

| Frame | ความหมายในแอป | สิ่งที่ยังไม่พิสูจน์ |
| --- | --- | --- |
| `#STOP\r\n` | stop helper; hex `2353544f500d0a` | หยุดมอเตอร์ทั้งหมดทันทีหรือได้ทุกสถานะหรือไม่ |
| `#STOP+OK` | receive parser เปลี่ยน `isArmStartAction=false` | เป็นโค้ดรองรับ ACK ไม่ใช่ ACK ที่รับจริงในงานนี้ |
| `#99GC1\r\n` | helper กลับท่าตั้งต้น และ `usbInit()` | เรียกกลุ่ม 99 ไม่ใช่หยุด; เปิดพอร์ตอาจส่งเอง |
| `#<group>GC5\r\n` | `ArmTestActivity.sendArmToRobot` | แอปทดสอบใช้ 5 รอบ ไม่ใช่ข้อกำหนดหนึ่งรอบของ `/arm` |
| `#<servo>P1500T3000\r\n` | `sendSingleServoPowerOn` | มีเป้าหมาย 1500 อาจขยับ ไม่ใช่แค่เปิดไฟ |
| `#<servo>P0T1000\r\n` | `sendSingleServoReleaseForce` | คลายแรงยึด ไม่ใช่การหยุดที่รับรองว่าคงท่า |

`ArmTestActivity.SERVO_NUMBERS` = `[1, 11, 7, 8]`; All วนสี่ช่องนี้ ไม่ได้แปลว่ามีมอเตอร์แค่สี่ตัว ยังไม่มี wiring map ไหล่/ศอก/นิ้ว บาง helper ส่งซ้ำแบบหน่วงเวลา ต้องตรวจทั้งเส้นทางก่อนนำไปใช้

พบ marker `#CC` ใน parser แต่ยังไม่พิสูจน์ว่าให้ตำแหน่งข้อต่อจริง คำว่า `握手`/handshake ใน driver ที่ตรวจเป็นการสื่อสารระหว่างบอร์ด ไม่ใช่ท่าจับมือคน

**แก้ข้อสรุปเดิม:** ไม่ถูกต้องที่จะกล่าวว่าไม่มีคำสั่งหยุดแขน แต่ยังเปลี่ยนปุ่ม “หยุดส่งคำสั่ง” ให้รับรองว่าหยุดแขนจริงไม่ได้จนกว่าจะทดสอบ งานนี้ไม่ได้แก้ runtime หรือเปิดใช้การควบคุม

## 3. จอและสีหน้า

### DoubleScreenService

- Dynamic action `android.set`, extra `paramInt` int ค่าเริ่มต้น 0
- `paramInt == 2`: release MediaPlayer และ dismiss/cancel Presentation
- ค่าอื่นอ่าน SharedPreferences `rk`, key **`rk_video_path`** แล้วเรียก `updateContents` ไม่ได้อ่าน path จาก Intent extra
- `updateContents` ใช้ `DisplayManager.getDisplays()[1]` ไม่ใช่หลักฐานว่าจอนี้คือจอบนหัว
- Guard ตรวจจำนวน display อย่างน้อย 1 แต่ใช้ index 1 ซึ่งต้องมีอย่างน้อย 2 เป็นข้อบกพร่องจากการอ่านโค้ด ไม่ได้ทดลองให้ crash
- `onBind()` คืน null ไม่ใช่ AIDL ส่งภาพ

### FaceExpressionNewActivity

- อ่านภาพจาก external storage `/aobo/apps/aobocenter/faceexpression` โดยทั่วไปอยู่ใต้ `/sdcard`
- รองรับ `.png`, `.jpg`, `.jpeg`, `.webp`, เรียงชื่อภาพแบบตัวเลข มีค่าเริ่มต้น `normal`
- อ่าน `delivery_animation_testmode` int; ถ้าเป็น 1 อ่าน `delivery_animation_preview` String; มิฉะนั้นอ่าน **`delivery_animation_funtion`** int (สะกดตามโค้ด)
- ใช้ preferences `animation_settings`; Activity เป็นภายใน การรู้ extras ไม่ทำให้แอป UID อื่นเปิดตรงได้
- เป็นภาพบนจอ ไม่ใช่คำสั่งหันคอ ยังไม่ยืนยันการจับคู่จอทางกายภาพ

## 4. สำรองและหลักฐาน

สำรอง preferences/databases/Realm/imi.ini เพิ่ม 48 ไฟล์, tar 235,520 bytes SHA-256 `307648fbea328a6b1484b368f7b7a2a701c6843e27f39d3bcdf224b87ba9a82d`; ไม่ใช่ backup ทั้งเครื่องและไม่ได้ restore รายละเอียดใน [รายงานข้อมูลภายใน](robot-internal-code-research-2026-09-11.md)

อ่าน `key_voicearmisuse=false`, กลุ่มท่าที่ตั้งไว้สำหรับใบหน้า/นำทาง `6`/`17`, `voiceaction=false`, `naviaction=false`, `estopstoparmmovementkey=true`, `estopstopallmovementkey=false` ทั้งหมดเป็นค่าตั้ง ไม่ใช่หลักฐานผลฮาร์ดแวร์ `USB_HUB_LINK.xml` มีเพียง `lastloraid`; `dsconfig.xml` เป็นโมเดลสนทนา ไม่ใช่จอ

SQLite 5 ไฟล์ผ่าน `PRAGMA quick_check` ไม่พบ joint map จากชื่อ/schema ที่ตรวจ ส่วน Realm ยังไม่ได้ถอดครบ ข้อมูลที่อาจเป็น credential เก็บเฉพาะสำเนา ignored ไม่คัดลงเอกสารหรือ changelog

โฟลเดอร์หลักในคอม: `data/robot-inspection/service-interface-research-20260911/` อยู่ใน Git ignore

| ไฟล์/โฟลเดอร์ | เนื้อหา |
| --- | --- |
| `live-code/private-settings-and-databases.tar` | สำรองข้อมูลภายใน |
| `live-code/private-analysis.json` | key/schema/จำนวนแถวและผล quick_check |
| `live-code/maps.txt`, `fds.txt`, `inventory-summary.json` | หลักฐานหลังเปิดแอป |
| `live-code/before-battery-shutdown/` | หลักฐานก่อนแบตหมด |
| `live-code/recovered-dex-manifest.json` | DEX ทั้ง 7 ขนาด SHA-256 และผล header |
| `live-code/recovered-*.interface.json` | รายการคลาส/เมธอด Aobo, signature, access flags, code offsets |
| `disassembly/index.json`, `*.asm.txt` | คลาสที่เลือกถอดและตำแหน่งคำสั่ง |
| `verification-summary.json` | ผลตรวจ offline ท้ายงาน |

DEX interface/driver หลัก: `recovered-9278c000.dex`, SHA-256 `2ff27afc63d61d262cd74f0c41fd342eccbec67df2f3142eef0c09521e6576d5`; UI/MyService: `recovered-93886000.dex`, SHA-256 `1587455af53aa343415fe9ddbe2b24159b3130995bfc6e6ea7ae47357ed3cf00`

ตำแหน่งอ้างอิง: `IMyAidlInterface$Stub$Proxy.sendMessage` code offset 6989132; `SerialToothManagerCH340.usbInit` 4225536; `ArmTestActivity.sendSingleServoPowerOn` 5012736; `sendSingleServoReleaseForce` 5012904; `sendArmToRobot` 5017924; `sendArmToRobotStop` 5018092

ทั้ง 7 ไฟล์มี Adler32 ตรง header แต่ SHA-1 ใน header ไม่ตรงเนื้อหาปัจจุบัน เก็บ bytes เดิม ไม่อ้างว่าเป็น DEX ต้นฉบับที่ลายเซ็นครบ โครงสร้างอ่านได้ด้วย parser และ Androguard ตาม [รูปแบบ DEX](https://source.android.com/docs/core/runtime/dex-format) บางเมธอดยังเป็น native เช่น `ArmTestActivity.onCreate` code offset 0 จึงไม่อ้างว่ากู้ executable logic ครบทั้งหมด

ผลตรวจ offline: ขนาดรวม 42,321,712 bytes; SHA-256 ทั้ง 7 ตรง manifest; parser ที่เขียนอ่านโครงสร้างและ Androguard นับคลาสตรงกันทุกไฟล์; ถอดคลาสที่เลือก 260 คลาส; ตรวจ raw strings ยืนยัน CRLF หนึ่งคู่; ลำดับเมธอด AIDL ตรง transaction IDs ของ Stub; Python AST parse ผ่าน 9 สคริปต์ ไม่มีการเรียกหุ่นในการตรวจนี้ ไม่ได้ compile/bind AIDL หรือรัน unit tests ของแอปเพราะไม่แก้ runtime

## งานที่ยังต้องพิสูจน์และแนวทาง Emma

ยังไม่ยืนยัน wiring map ทุกข้อต่อ, ชื่อ/เนื้อหากลุ่มท่าในบอร์ด, feedback ตำแหน่ง/แรง, ผล stop จริง, จอใดอยู่บนหัว, การเรียกข้าม UID และ logic ใน native code ไม่เติมด้วยการเดาหรือทดลองส่งคำสั่ง

แนวทางที่มีหลักฐานรองรับคือให้ APK Emma เชื่อม MyService ผ่าน AIDL และแยก adapter แขน/จอจากเสียงเพราะ TTS อาจเล่นท่า ให้ Aobo ถือ USB ต่อไปได้หากช่องทางที่พบเรียกจาก Emma สำเร็จ ซึ่งยังเป็นเงื่อนไขต้องตรวจภายหลัง ไฟล์ AIDL รอบนี้เป็นข้อมูลอ้างอิง ไม่มีตัวส่งคำสั่งหรือ APK ติดตั้งใหม่
