# ตรวจข้อมูลภายในหุ่นเพื่อถอด interface ของ Aobo

> **สถานะล่าสุดหลังเจ้าของอนุญาตให้เปิด Aobo:** อ่าน DEX ที่คลายการห่อได้ 7 ไฟล์ พบ AIDL และเส้นทางแขน/จอแล้ว ดู [ผลถอด interface](robot-recovered-interface-2026-09-11.md) เนื้อหาด้านล่างเป็นบันทึกก่อนการเปิดแอป ไม่ใช่ข้อจำกัดปัจจุบัน

## ขอบเขตล่าสุด

เจ้าของอนุญาตให้ตรวจข้อมูลภายในเพิ่มเติมและให้ค้นเองโดยไม่ติดต่อผู้ขาย ข้อห้ามควบคุมหรือส่งคำสั่งให้หุ่นทำงานยังมีผล รอบนี้จึงอ่านข้อมูลด้วย ADB/su ที่มีอยู่แล้ว แต่ไม่เริ่ม/หยุดแอป ไม่เปิด USB/serial เพื่อส่งข้อมูล ไม่เรียก Binder methods และไม่แก้หน่วยความจำหรือค่าบนหุ่น

## สิ่งที่อ่านได้เพิ่มจริง

- **su ใช้งานได้จริง**: อ่าน `id` ใน ADB shell ได้ UID 2000 และ `/system/xbin/su 0 id` ได้ UID 0, SELinux context `u:r:su:s0` ไม่ได้ติดตั้ง root หรือเปลี่ยน SELinux
- อ่าน memory maps, รายการ file descriptors และรายชื่อไฟล์ส่วนตัวของโปรเซส `com.aobo.robot.ai3` ที่รันอยู่แล้วได้สำเร็จ
- พบ **`shared_prefs/armSetting.xml`** และ **`shared_prefs/USB_HUB_LINK.xml`** ในข้อมูลส่วนตัวแอป ซึ่งการสำรองด้วย shell ธรรมดารอบก่อนไม่สามารถอ่านได้
- พบ `aobosetting.xml`, `OtherSetting.xml`, `dsconfig.xml`, `ws_service_config.xml` รวมถึง `files/abRealm.realm` และฐานข้อมูลของแอป เป็นแหล่งข้อมูลที่จะใช้ค้นค่าพอร์ต/แขน/จอและชุดท่าต่อ ยังไม่อ้างว่าไฟล์เหล่านี้มี schema ที่ต้องการจนกว่าจะอ่านเนื้อหา
- โปรเซส Aobo เปิด `/dev/bus/usb/003/004` และ `/dev/bus/usb/003/006` อยู่จริงจากรายการ fd นี่เป็นหลักฐานการเปิดอุปกรณ์ของโปรเซสที่เฉพาะเจาะจงกว่าการเห็นไดรเวอร์ `usbfs` เพียงอย่างเดียว แต่ยังไม่พิสูจน์ว่าแต่ละ node คือ CP2102/CH340 ตัวใด หรือใครถือสิทธิ์ claim ของแต่ละ interface
- พบช่วง anonymous executable memory ขนาด 26,861,568 และ 15,474,688 bytes เป็นจุดที่ควรตรวจ header เพื่อหาโค้ดที่คลายการห่อแล้ว **ยังไม่ยืนยันว่าช่วงเหล่านี้เป็น DEX**

## สิ่งที่วิเคราะห์จาก APK ในคอม

`classes.dex` มีขนาด 13,074,112 bytes ขณะที่จุดสิ้นสุดส่วน data ตาม header อยู่ที่ 33,388 bytes จึงมีข้อมูลต่อท้าย 13,040,724 bytes ที่ไม่ถูกครอบคลุมด้วยการค้น string IDs ของ DEX ปกติ ตาม [รูปแบบ DEX ของ Android](https://source.android.com/docs/core/runtime/dex-format)

ค้น magic `dex\n03` ในส่วนต่อท้ายทั้งแบบปกติและ XOR ค่าคงที่ครบ 256 ค่าแล้วไม่พบ จึงยังไม่ใช่ DEX ที่ดึงออกได้ด้วยวิธีง่ายนี้ การทดลอง XOR บางช่วงเห็นโครงสร้างคล้าย metadata แต่ยังไม่มีตัวถอดหรือสัญญา service ที่ตรวจสอบได้ ไม่ได้นำผลเดามาใช้สร้างคำสั่ง

เครื่องมือ unpack ที่ใช้ hook การโหลด DEX เช่น [fridroid-unpacker](https://github.com/enovella/fridroid-unpacker) ต้องแทรกโค้ดในโปรเซสและตัวอย่างใช้การเริ่มแอปใหม่ ไม่ได้นำมารันกับหุ่น ทางที่เตรียมไว้ตอนนี้ใช้การอ่าน header จาก `/proc/<pid>/mem` ผ่าน `dd` ที่มีอยู่ โดยไม่หยุดโปรเซสหรือเขียนกลับ แต่ยังไม่พิสูจน์ว่า kernel อนุญาตให้อ่าน memory ดังกล่าว

## จุดที่หยุดเพราะการเชื่อมต่อ

หลังเก็บรายชื่อไฟล์และ maps สำเร็จ ADB เปลี่ยนเป็น **device offline** ก่อนอ่านเนื้อหา preferences และก่อนอ่าน memory header สคริปต์ตรวจ PID ล้มเหลวและหยุดโดยยังไม่ได้เรียก `cat` ของ preferences หรือ `dd` อ่าน memory การตรวจซ้ำยืนยันว่าเป็น offline ไม่ใช่หลักฐานว่า PID เปลี่ยน

ลองเชื่อมกลับ endpoint เดิมหนึ่งครั้งแล้ว timeout (10060) ไม่ได้รีบูตหุ่น รีสตาร์ตแอป เปลี่ยน Wi-Fi หรือเปิดพอร์ตใหม่ ยังไม่ทราบว่าหุ่นปิดเครื่อง เปลี่ยนเครือข่าย หรือ wireless debugging หลุดด้วยสาเหตุใด

สถานะช่วงนี้เป็นเหตุการณ์ระหว่างงาน ต่อมาผู้ใช้ยืนยันว่าแบตหมดและเปิดเครื่องใหม่ ผลที่อ่านต่อได้อยู่ด้านล่าง

## หลังผู้ใช้เปิดหุ่นใหม่

เชื่อมกลับผ่าน endpoint ที่ mDNS ประกาศและตรวจ serial/model แล้วว่าเป็นหุ่นเดิม สำรอง `shared_prefs`, `databases`, `files/abRealm.realm` และ `files/imi.ini` ผ่าน tar แบบส่งข้อมูลออกมายังคอม ไม่มีไฟล์ tar ถูกเขียนบนหุ่น

ได้ 48 ไฟล์ ขนาด archive 235,520 bytes; SHA-256 `307648fbea328a6b1484b368f7b7a2a701c6843e27f39d3bcdf224b87ba9a82d` ฐานข้อมูล SQLite 5 ไฟล์ผ่าน `PRAGMA quick_check` ทุกไฟล์ สำเนานี้เพิ่มส่วนที่ shell ธรรมดาอ่านไม่ได้ในการสำรองครั้งก่อน ไม่ใช่ backup ทั้งเครื่อง และไม่ได้ทดลอง restore

ค่าที่อ่านได้จริง:

| ไฟล์ / key | ค่า | ความหมายที่ยืนยันได้ |
| --- | --- | --- |
| `armSetting.xml` / `key_voicearmisuse` | false | ปิดตัวเลือกที่ resource แอปเรียกว่า Voice control action group ไม่ได้พิสูจน์ว่าแขนไม่ทำงานผ่านช่องทางอื่น |
| `FacereSetting.xml` / `faceregroupaction` | `6` | มีเลขกลุ่มที่ถูกบันทึกในส่วนตั้งค่าการรู้จำใบหน้า ยังไม่มีชื่อท่าหรือหลักฐานว่าอยู่จริงในบอร์ด |
| `FacereSetting.xml` / `navirungroupaction` | `17` | มีเลขกลุ่มที่บันทึกสำหรับส่วนการนำทาง ยังไม่รู้ท่าที่เล่น |
| `FacereSetting.xml` / `voiceaction`, `naviaction` | false ทั้งคู่ | ตัวเลือกประกอบท่าทั้งสองปิดอยู่ในไฟล์ที่อ่าน |
| `aobosetting.xml` / `estopstoparmmovementkey` | true | แอปมีการตั้งค่าที่เกี่ยวกับการหยุดแขนเมื่อ E-stop; ยังไม่ใช่การพิสูจน์ว่าโปรโตคอล/ฮาร์ดแวร์หยุดได้ |
| `aobosetting.xml` / `estopstopallmovementkey` | false | เป็นค่าที่บันทึกไว้ ไม่ได้เปลี่ยนหรือทดสอบผล |

`USB_HUB_LINK.xml` มีเพียง `lastloraid` ไม่ใช่ตารางจับคู่ UART ที่ตามหา ส่วน `dsconfig.xml` มีค่าการตั้งค่าโมเดลสนทนา ไม่ควรอนุมานจากชื่อไฟล์ว่าเป็น double screen ข้อมูลที่อาจเป็น credential เก็บเฉพาะในสำเนา ignored ไม่ใส่ในรายงานหรือ changelog

ไม่พบตารางที่ระบุข้อต่อ/servo mapping จากชื่อและ schema ของ SQLite ที่อ่าน ส่วน Realm ขนาด 12,288 bytes พบชื่อคลาส Chat/Document/Feedback/Food/QuestionAndAnswer/User ในการค้น strings ยังไม่ได้ถอดโครงสร้าง Realm ครบ จึงไม่อ้างว่าภายในไม่มีข้อมูลอื่น

ขณะตรวจหลังบูต `pidof com.aobo.robot.ai3` ยังไม่พบโปรเซส และ `topResumedActivity` เป็น `com.android.launcher3/.uioverrides.QuickstepLauncher` จึงไม่เปิดแอปหรืออ่าน address ก่อนแบตหมดซ้ำ การอ่านโค้ดจาก memory ต้องอาศัยโปรเซสที่รันอยู่จริง และการเริ่มแอปอาจสั่งฮาร์ดแวร์อัตโนมัติ

**ยังไม่ได้ DEX ที่คลายการห่อ รายการเมธอด MyService หรือ joint map ครบ** แต่ได้สำรองและอ่านค่าภายในเพิ่มแล้ว งานนี้ไม่ได้ติดต่อผู้ขาย

## หลักฐานและงานที่เตรียมไว้

ข้อมูลอ่านจริงเก็บใน `data/robot-inspection/service-interface-research-20260911/live-code/` (ignored): `maps.txt` 295,791 bytes, `fds.txt` 28,872 bytes, `private-file-names.txt` 9,304 bytes และ `inventory-summary.json` ซึ่งทั้งสามคำสั่งคืน exit code 0

สคริปต์ `read_live_code_inventory.py` ใช้อ่าน inventory ของแอปที่รันอยู่ ส่วน `read_target_code.py` เตรียมอ่าน preferences เฉพาะรายการและ header ของ anonymous executable regions โดยต้องระบุ transport ที่ออนไลน์ ตรวจ PID และอ่าน maps ใหม่ก่อน ไม่เริ่มแอปให้เอง

งานที่เหลือเมื่อมีโปรเซสแอป Aobo รันอยู่: อ่าน PID/maps ใหม่ → ตรวจ header ของช่วงโค้ด → ถ้าเป็น DEX จึงคัดลอกเฉพาะ DEX มาวิเคราะห์ในคอม → ถอดชื่อ interface, transaction mapping, พารามิเตอร์และ callbacks จากโค้ด โดยยังไม่เรียกคำสั่งใดบนหุ่น
