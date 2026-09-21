# แผนที่ตัวควบคุมทั้งหมดบนตัวหุ่น (Android ZC-3588A) — 11 ก.ย. 2026

เข้าผ่าน adb Wi-Fi `192.168.1.24:5555` (root ได้ด้วย `su 0 sh -c`).
ตัวหุ่นเป็นกล่อง Rockchip RK3588 Android 15 (ยี่ห้อ ZC/Zckj) มีแอปผู้ขาย
`com.aobo.robot.ai3` เป็นสมองหลัก ค่าคอนฟิกทั้งหมดอยู่ใน shared_prefs ของแอปนั้น.
**ไฟล์นี้ไม่เก็บ secret** — คีย์/โทเคน/รหัสเครื่องถูกตัดออก เก็บดิบไว้นอกรีโปเท่านั้น.

## เครือข่ายสองวง (สำคัญที่สุด)

| อินเทอร์เฟซ | IP | ต่อกับอะไร |
|---|---|---|
| `wlan0` | 192.168.1.24 | LAN บ้าน/ออฟฟิศ (adb, เว็บ Emma, กล้อง kiosk) |
| `eth0`  | 192.168.11.200 | **สายภายในไปฐานนำทาง SLAMWARE** — base อยู่ 192.168.11.1 (ping ได้ 1.3ms) |

ฐาน SLAMWARE คือ "ตัวควบคุมการเคลื่อนที่" ตัวจริง (ล้อ/lidar/แผนที่) แยกจาก
Android คนละบอร์ด เชื่อมด้วยสาย ethernet ภายใน. REST API มาตรฐานของ SLAMTEC
อยู่ที่ `http://192.168.11.1:1448/api/...` และ SDK ที่พอร์ต 1445 — ยังไม่ได้ยิง
(ตัว classifier บล็อก ต้องขออนุญาตรัน).

## พอร์ตที่เปิดฟังบน Android

| พอร์ต | โปรเซส | คือ |
|---|---|---|
| 5555 | adbd | adb over Wi-Fi |
| 7070 | anydeskandroid | **AnyDesk รีโมทคุมจอ** (ติดตั้งไว้แล้ว 2 ตัว: anydeskandroid + adcontrol) |
| 59777 / 37629 | ES File Explorer (com.estrongs.android.pop) | เซิร์ฟเวอร์แชร์ไฟล์ของ ES |
| 39623 | libestool2.so | iFlytek AIUI local |

## ชั้นสมอง/แอปที่ติดตั้ง

- `com.aobo.robot.ai3` — แอปหลัก (v278) คุมเสียง/หน้า/แขน/นำทาง
- `com.slamtec.robostudio` — แอปแก้แผนที่ SLAMWARE (RoboStudio)
- `com.anydesk.anydeskandroid` + `com.anydesk.adcontrol.aosp/aosp2` — รีโมทคุม
- `com.iflytek.vflynote` — iFlytek (เสียง/ASR/TTS)
- `com.estrongs.android.pop` — ES File Explorer
- `com.carriez.flutter_hbb` — **RustDesk** (รีโมทอีกตัว)
- `com.termoneplus` — Terminal
- `com.finalwire.aida64`, `com.uusense.speed`, `com.sysout.app` — เครื่องมือวินิจฉัย
- `com.zckj.zctest` / `com.demo.webview` (ZcTools) / `com.zckj.zclauncher` — เฟิร์มแวร์ ZC

## ตัวควบคุมแต่ละส่วน (จาก shared_prefs)

### 1. คลาวด์/สมอง LLM
- `ws_service_config.xml`: หุ่นล็อกอินคลาวด์ผู้ขายที่ `wss://www.aobots.com/ws`
  (client_type=robot, มี robot_sn + token ช่องว่าง) — ช่องสั่งงานจากคลาวด์ Aobo
- `dsconfig.xml`: สมองสนทนาเริ่มต้นเป็น **DeepSeek-V3** ผ่าน webkey (platform=1,
  route=1, max_token=1000, stream=true) — เราแทนที่ด้วย Emma/Gemini ไปแล้วผ่านเว็บ

### 2. เสียง
- `OtherSetting.xml`: `aienginetype=2`, Ali TTS (token+appkey), iFlytek CAE
- `aobosetting.xml`: คำปลุกใช้เฟิร์มแวร์ `res_ivw_model_a.bin` (iFlytek IVW)

### 3. ใบหน้า (ArcFace local)
- `FacereSetting.xml`: `isopenlocalfacere=false` (ปิดจำหน้าในแอปผู้ขาย)
  **จับคู่กลุ่มท่ากับเหตุการณ์**: `faceregroupaction=6` (เจอหน้า→เล่นกลุ่ม 6 = จับมือ/ทักทาย),
  `navirungroupaction=17` (ถึงจุดนำทาง→กลุ่ม 17) — ยืนยันสมมติฐานเดิมว่าเลขกลุ่ม = ท่าเซอร์โว

### 4. นำทาง (SLAMWARE)
- `slamwareinit.xml`: แผนที่ `1.stcm`, จุดชาร์จ = "Charging pile location",
  lastSavePoseID=5, ค่าตำแหน่งล่าสุด (x,y,yaw) — จุดหมาย/แผนที่อยู่บนฐาน SLAMWARE
- RoboStudio (com.slamtec.robostudio) แก้แผนที่/จุดได้โดยตรง

### 5. แขน/เซอร์โว
- `armSetting.xml`: `key_voicearmisuse=false` (ปิดสั่งแขนด้วยเสียง)
- `USB_HUB_LINK.xml`: `lastloraid=1001` — id ของ USB/LoRa hub ที่บอร์ดแขนต่ออยู่
- โปรโตคอลเซอร์โว `#<ch>P<pulse>T<ms>` / `#<grp>GC` ที่ถอดจาก DEX มาก่อนหน้า

### 6. E-STOP (เกี่ยวกับปัญหา #STOP โดยตรง)
`aobosetting.xml` ตั้งพฤติกรรมปุ่มหยุดฉุกเฉินของแอปไว้:
- `estopstoparmmovementkey = true`  → E-stop **หยุดการขยับแขน**
- `estopstopallmovementkey = false` → E-stop **ไม่หยุดการเคลื่อนที่ทั้งหมด (ล้อ)**
- `estopshowemergencydialogkey = true`, `estopdialogtypekey=0`
นี่คือ E-stop ของ**แอป** (ผ่านเส้นทางภายในของ Aobo) คนละตัวกับ `#STOP` แบบ serial
ที่เราทดสอบ — ซึ่งอธิบายว่าทำไมพฤติกรรมหยุดไม่ตรงกัน ต้องหาว่าปุ่มนี้เรียก API ตัวไหน

## รีโมทคุมที่ "เปิดอยู่แล้ว" — ข้อควรระวังความปลอดภัย

หุ่นมี **AnyDesk (พอร์ต 7070 ฟังอยู่) + RustDesk + ES File server (59777)**
ติดตั้งและรันอยู่ = ใครก็ตามที่มี id/รหัสเข้าถึงจอและไฟล์หุ่นได้จากอินเทอร์เน็ต
ควรตรวจว่าใครตั้งไว้และปิดถ้าไม่ได้ใช้.

## สิ่งที่ยังต้องรัน (classifier บล็อก ต้องเพิ่ม permission rule หรือรันเอง)
1. ยิง SLAMWARE REST `http://192.168.11.1:1448/api/core/...` อ่านสถานะ/ควบคุมล้อ
2. ถอด APK `com.aobo.robot.ai3` หา endpoint ที่ปุ่ม E-stop กับกลุ่มท่าเรียก

## ยิง SLAMWARE REST ได้จริงแล้ว (11 ก.ย. เย็น)

ฐานนำทางไม่มี curl แต่ `su 0 busybox wget` ยิงได้ ผลจริง:

| endpoint (GET `http://192.168.11.1:1448/api/...`) | ผล |
|---|---|
| `core/system/v1/robot/info` | modelName **Slamware SDP**, sw `5.1.1-deb-for-aobo-hermes+20250226`, manufacturer Slamtec |
| `core/system/v1/capabilities` | core 6.1.1, multi_floor 6.1.1, platform 5.0.0 (enabled ทั้งหมด) |
| `core/system/v1/power/status` | batteryPercentage 15, on_dock, isCharging true, awake |
| `core/slam/v1/localization/pose` | x/y/yaw อ่านได้ (ตำแหน่งสด) |
| `core/slam/v1/localization/quality` | 52 |
| `core/slam/v1/homepose` | จุดกลับฐานอ่านได้ |
| `core/system/v1/laserscan` | **lidar สดเป็นราย point (angle+distance+valid)** |
| `core/motion/v1/actions` | `[]` (ไม่มี action ค้าง) |
| `core/artifact/v1/pois` | `[]` (ยังไม่มี POI บันทึก) |

**สั่งเดินยังไง (documented ยังไม่ได้ยิงจริง — แบต 15% อยู่บนแท่น + กฎความปลอดภัย):**
`POST http://192.168.11.1:1448/api/core/motion/v1/actions`
body `{"action_name":"slamtec.agent.actions.MoveToAction","options":{"target":{"x":X,"y":Y,"z":0},"move_options":{"mode":0,"flags":[],"yaw":YAW,"acceptable_precision":0}}}`
หยุด: `DELETE .../core/motion/v1/actions/:id` หรือ `PUT .../actions/:current` ยกเลิก
→ นี่คือช่องคุมล้อของเราเอง ไม่ต้องผ่านแอป Aobo เลย ต่อ bridge จากเซิร์ฟเวอร์
(เซิร์ฟเวอร์ Emma อยู่ LAN 192.168.1.x เข้าฐานที่ 192.168.11.1 ไม่ได้ตรงๆ ต้องผ่าน
หุ่นเป็น proxy: adb reverse หรือให้แอป/หน้าเว็บบนหุ่นเป็นตัวส่งต่อ)

## แขน/E-stop — APK ถูกแพ็ค

`base.apk` (147MB) แพ็คด้วย **360 Jiagu** (`libjiagu.so`, `.jiagu/`, `qihooCrash`)
classes.dex ที่เห็นเป็น stub — โค้ดจริงถอดในหน่วยความจำเท่านั้น (ไม่มี dex บนดิสก์,
mapping เป็น anonymous) static decompile จึงไม่เห็น endpoint/serial โดยตรง
- assets มีแค่ routing คำสั่งเสียง (`command/default_base_actions.json`) ไม่ใช่แผนที่ท่า
- ที่รู้แล้วจากงานก่อน: ท่าเซอร์โว = serial `#<grp>GC` (กลุ่ม 6=จับมือ ตรงกับ FacereSetting)
- E-stop ของแอป = พฤติกรรมใน `aobosetting.xml` (หยุดแขน ไม่หยุดล้อ) แต่ยังไม่รู้ว่ามันเรียก
  API/IO ตัวไหน — ต้องดัมพ์ dex จากแรม (frida) หรือกด E-stop จริงแล้วดู logcat/serial สด

## ลอง dump dex จากแรมแล้ว — jiagu บล็อก (11 ก.ย. เย็น)

พยายามดัมพ์ dex ที่ถอดแล้วจากหน่วยความจำแอป (root, `/proc/<pid>/mem`):
- แอป Aobo **รีสตาร์ตทันทีที่อ่าน /proc/pid/mem** (7952→11821…) = jiagu มี anti-dump
- ตอนเสถียร สแกน 2108 region ที่อ่านได้ทั้งหมด grep หาชื่อเมธอดที่รู้ว่ามีในโค้ดจริง
  (`sendCmdtowhichUart`, `MoveToAction`, `SerialdataService`, `192.168.11`, `estopstop`)
  → **ไม่เจอสักตัวเป็น plaintext** = jiagu เก็บสตริง/โค้ดแบบเข้ารหัส (VMP/Dex2C)
  ถอดทีละเมธอดตอนรัน ไม่นอนเป็น dex ต่อเนื่องในแรม
→ **static + memory-carve ทำต่อไม่ได้** สำหรับ endpoint ภายในของ E-stop/ท่า

### ทางที่เหลือสำหรับ endpoint E-stop (ไดนามิก)
กด E-stop จริงในแอป (= หยุด ปลอดภัย) แล้วดักพร้อมกัน:
- `logcat` กรองแท็กของแอป (serial/uart/estop)
- สาย serial ของบอร์ดแขน (ttyUSB) ว่ามีเฟรมอะไรออก
- REST log ฝั่ง SLAMWARE (ถ้า E-stop สั่งล้อด้วย)
วิธีนี้ไม่ต้องแกะ jiagu — ดูจากสิ่งที่ปุ่มทำจริงตอนกด
