# ผลค้น interface สำหรับ Emma จากสำเนาแอป Aobo

> **อัปเดตภายหลัง:** อ่านโค้ดจากโปรเซสได้แล้ว พบ AIDL, schema และเส้นทางแขน/จอ ดู [ผลถอด interface](robot-recovered-interface-2026-09-11.md) ด้านล่างเป็นผลก่อนอ่าน memory ข้อเสนอขอ interface จากผู้ขายไม่ใช่ข้อจำกัดปัจจุบัน และไม่ได้ส่งคำขอนั้น

วันที่ตรวจ: 11 กันยายน 2026 — ค้นข้อมูลเท่านั้นตามคำสั่งเจ้าของ ไม่ติดต่อหุ่น ไม่เรียก ADB, REST, Binder หรือพอร์ต USB/serial และไม่เริ่ม/หยุด service

## ข้อสรุปที่ใช้ตัดสินใจได้

พบช่องทางภายนอกที่ควรขอรายละเอียดจากผู้ขายคือ `com.aobo.aibot.aidl.MyService` ซึ่งประกาศ `exported=true` จริง ส่วน `SerialdataService` ไม่ได้เปิดให้แอป Emma ซึ่งมี UID ต่างกันเรียกโดยตรงตาม manifest ที่สำรองมา การระบุ component ให้ครบไม่ได้ข้ามข้อจำกัดนี้

ยังไม่พบสัญญาการเรียกของ MyService: ชื่อ interface, เมธอด, พารามิเตอร์, callbacks หรือ Parcelable จึงยังสร้างตัวเชื่อมที่ยืนยันว่าใช้งานได้ไม่ได้ และยังไม่ทราบว่า service นี้รองรับแขน/จอหรือเป็นเพียงฟังก์ชันอื่น ชื่อแพ็กเกจที่มี `aidl` เป็นเบาะแส ไม่ใช่หลักฐานว่ามีไฟล์ `.aidl` ที่เรานำมาใช้ได้แล้ว

## Manifest ที่ถอดจาก APK จริง

สำเนาติดตั้ง: `com.aobo.robot.ai3` versionCode **278**, versionName `3.1.230528au3.sl.deliver.4m`, minSdk 23, targetSdk 28; application ใช้ `com.stub.StubApp` และไม่ประกาศ application-level permission

SHA-256 ของ APK: `a4f3f1ec23686ec4a19d393af4d6d42c11ba4c0cff7ad7065e06246804c031f4`

| Component | ค่าที่อ่านได้ | ผลต่อแอป Emma |
| --- | --- | --- |
| `com.aobo.aibot.aidl.MyService` | `exported=true`; action `com.aobo.aidl.test`; category `android.intent.category.DEFAULT`; ไม่มี service-level permission | เปิดให้แอปอื่นเข้าถึงในระดับ manifest แต่ยังไม่ทราบการตรวจสิทธิ์ในโค้ดหรือผลของการ bind |
| `com.aobo.robot.ai3.usbserial.SerialdataService` | ไม่ระบุ exported; ไม่มี intent filter | ค่าเริ่มต้น exported เป็น false จึงใช้ภายในแอป/UID เดียวกัน ไม่ใช่ช่องทางตรงสำหรับ Emma |
| `com.aobo.robot.ai3.services.DoubleScreenService` | ไม่ระบุ exported; มี action `com.aobo.robot.luncher.services.DoubleScreenService` | ตามค่าเริ่มต้นเป็น exported แต่ยังไม่มีสัญญาการสั่งจอ; ชื่อ action ไม่ใช่ชื่อ component ตัวที่สอง |
| `com.aobo.aibot.services.TcpService` | ไม่ระบุ exported; ไม่มี intent filter | เป็น service ภายใน; ชื่อไม่บอกพอร์ตหรือ TCP schema |
| `com.aobo.aibot.ws.WebSocketService` | `enabled=true`, `exported=false` | เรียก component จาก Emma โดยตรงไม่ได้; ไม่ได้พิสูจน์ว่ามี WebSocket endpoint ภายนอก |

ตรวจเทียบ APK รุ่น 226 และ 227 แล้วพบข้อประกาศของ MyService, SerialdataService และ DoubleScreenService แบบเดียวกัน การคงชื่อข้ามรุ่นไม่ได้รับรองว่าสัญญาเมธอดเหมือนกัน

การแปลค่า exported และ permission อ้างอิง [Android service manifest](https://developer.android.com/guide/topics/manifest/service-element) ส่วนการเรียก AIDL ต้องมีชนิด interface และรูปแบบข้อมูลที่ตรงกันตาม [เอกสาร AIDL ของ Android](https://developer.android.com/develop/background-work/services/aidl) การเปิดให้เข้าถึงจึงยังไม่เท่ากับเรียกคำสั่งได้แล้ว

## ค้นไฟล์สัญญาและ SDK ไปถึงไหน

| ขอบเขต | ผลจริง |
| --- | --- |
| รายชื่อสมาชิกใน archive สำรอง 39 ไฟล์ รวม 82,871 รายการ | ไม่พบ `.aidl`, `.aar` หรือ SDK/demo Aobo ตามชื่อที่ค้น; พบ JAR 7 รายการในแอปอื่น เช่นโฆษณา/ไลบรารีทั่วไป ซึ่งไม่ใช่หลักฐาน SDK Aobo |
| DEX ระดับบนใน APK สำรอง 37 ไฟล์ รวม 62 DEX / 1,942,274 string IDs | ค้นชื่อแพ็กเกจ Aobo, AoboRobotManager, SDK และ service เป้าหมาย พบเพียง package/library path ของ wrapper ใน APK Aobo 4 สำเนา; ไม่พบ contract ใน DEX ที่อ่านได้ |
| Downloads และไฟล์ใน workspace ที่อ่านได้ | พบ AAR จำลองจาก pytest ในโครงการ แต่ไม่ใช่ SDK ผู้ขาย; การค้นทั่ว workspace มีบางโฟลเดอร์ output ของโครงการอื่นถูกปฏิเสธสิทธิ์ จึงไม่อ้างว่าค้นทุกไฟล์ในคอมครบ |
| โฟลเดอร์ Downloads/aobo และ aobo.zip ในชุดสำรอง | เป็นสื่อ/การตั้งค่า ไม่พบไลบรารี Aobo SDK ที่ต้องการจากรายการไฟล์ |
| คู่มือ SDK จีน 35 หน้าและฉบับแปลไทย | ระบุ `aoborobotsdk-v2.0.aar` ชัดเจน แต่ PDF ไม่ใช่ตัวไลบรารี และไม่พบคำจำกัดความ MyService/AIDL |
| ค้นเว็บด้วยชื่อ component/action/SDK แบบตรงตัว | ไม่พบผลที่ให้ interface หรือไฟล์ SDK ตรงรุ่น; ผลค้นว่างไม่พิสูจน์ว่าไม่มีอยู่ที่อื่น |

ข้อจำกัด: APK Aobo มี wrapper Jiagu และ DEX ที่เปิดอ่านได้มีเพียง 4 class definitions ต่อสำเนา การค้นครั้งนี้ไม่ครอบคลุมโค้ดที่ถูกห่อ, โค้ดที่โหลดขณะรัน, DEX ใน JAR ซ้อน หรือข้อมูลส่วนตัวของแอปที่สำรองไม่ได้ ไม่ได้ถอดรหัส/รัน payload หรือดึงหน่วยความจำบนหุ่น

หลักฐานและสคริปต์ offline อยู่ที่ `data/robot-inspection/service-interface-research-20260911/` (ignored): `service-evidence.json`, `dex-contract-search.json`, `inspect_services.py`, `scan_dex_contracts.py` และข้อความที่สกัดจาก PDF

## สิ่งที่คู่มือและผู้ผลิตบอกจริง

คู่มือจีน `c7ca2a1e0b380184867496a81c0d59d1.pdf` หน้า PDF 6 ระบุชื่อ `aoborobotsdk-v2.0.aar`, การเพิ่ม dependency ใน Gradle และโหมด REAL/MOCK ตรวจทั้งข้อความและภาพเต็มหน้าแล้ว ฉบับไทยอยู่หน้า PDF 4 ยังไม่มีหลักฐานว่า AAR นี้ใช้ MyService หรือ SerialdataService ภายในอย่างไร และไม่ควรนำเมธอด AoboRobotManager ไปเดาเป็น Binder methods

คู่มือ `หุ่นยนต์ ที่สนใจ.pdf` หน้า PDF 9 ระบุชุดพัฒนา Android SDK/demo/documentation และโปรโตคอลควบคุมแอป แต่ **ไม่ได้ระบุชื่อไฟล์ `.aidl` ของ MyService โดยตรง** จึงแก้ข้อสรุปเดิมที่ว่าเอกสารรับรองไฟล์ AIDL แล้ว

[หน้า SDK support ของผู้ผลิต](https://www.aoborobot.com/subwebsite/merchants.html) ระบุว่ามี SDK และช่วยใช้เอกสารพัฒนา แต่ไม่ให้ contract ตรงรุ่นในผลที่อ่านได้ ส่วน [หน้าบริการและเทคนิค](https://www.aoborobot.com/subwebsite/service.html) ชี้ไปยังบัญชี WeChat ของ Aobo เพื่อรับบทเรียน/บริการเทคนิค ไม่พบลิงก์ดาวน์โหลด AAR/AIDL ในเนื้อหาที่เครื่องมืออ่านได้ บางหน้าบทความเปิดไม่สำเร็จ จึงไม่อ้างว่าตรวจแหล่งดาวน์โหลดบนเว็บครบทุกหน้า

## ชุดข้อมูลที่ขาดและข้อความพร้อมขอผู้ขาย

ร่างนี้ยังไม่ได้ส่ง:

> We are integrating our Emma Android application with our Aobo robot. The backed-up controller app is `com.aobo.robot.ai3`, versionCode 278, versionName `3.1.230528au3.sl.deliver.4m`; the robot runs Android 15 on RK3588.
>
> Please provide the supported integration package for this exact app and robot configuration:
>
> 1. `aoborobotsdk-v2.0.aar` (or its compatible replacement), dependencies, package/import names, API documentation, release notes and a complete Android demo project.
> 2. The public contract for exported service `com.aobo.aibot.aidl.MyService`, action `com.aobo.aidl.test`: all AIDL/interface files, callbacks, Parcelable implementations, binding Intent requirements, permission/signature requirements and supported functions. If it is not the supported integration entry point, please identify the correct public API.
> 3. The supported API for `com.aobo.robot.ai3.services.DoubleScreenService`: required Intent extras or Binder schema, display selection, supported media/expression values and completion/error callbacks.
> 4. Whether the SDK shares the existing controller's USB connection through IPC or opens the USB interface itself, and the supported coexistence/ownership procedure with the stock app.
> 5. The exact arm controller model, joint-to-channel mapping, calibrated limits, action-group IDs, position/status feedback and documented stop/power-cut behavior for this robot, plus separate head-motion and face-display interfaces if available.
>
> Please include an offline/mock example with no hardware connection or startup movement. We are currently reviewing documentation only and will not execute robot commands as part of this inquiry.

## ผลต่อแผน Emma

ทางที่มีหลักฐานรองรับให้ศึกษาต่อคือ public MyService หรือ SDK ที่ผู้ขายรับรอง หลังได้ contract จึงตรวจโค้ดและสร้าง adapter แบบ offline ได้ ส่วนการใช้ SerialdataService โดยตรงต้องมีช่องทางที่ผู้ขายเปิดไว้ ไม่ใช่เพียงเดา extras แล้วระบุ component

ยังไม่ได้สร้าง/ติดตั้ง APK เชื่อมหุ่น เปลี่ยน `.env` หรือปรับ `/arm` รอบนี้ ไม่ได้ทดสอบ bind แม้เมธอดจะดูเป็นการอ่าน เพราะการสร้าง service อาจมีผลข้างเคียงได้ การตรวจทั้งหมดเป็นไฟล์ในคอมและเว็บเอกสาร
