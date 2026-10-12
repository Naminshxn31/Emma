# Emma Panel Tools

แอปจิ๋วสำหรับ **จอ Android interactive panel ในห้องขาย** (เช่น TB1100 Pro, Android 11) ที่
**ซ่อน Developer options** ออกจาก Settings จนเปิด ADB แบบปกติไม่ได้

แอปนี้ **ไม่คุมจอ และไม่ขอสิทธิ์อันตรายอะไรเลย** — หน้าที่เดียวคือ **พยายามเปิดหน้า Developer
options ที่ซ่อนอยู่โดยตรง**:
1. ผ่าน action สาธารณะ `ACTION_APPLICATION_DEVELOPMENT_SETTINGS` (ได้ผลกับหลาย ROM แม้เมนูถูกซ่อน)
2. ถ้าไม่ได้ → เปิด activity ของ Settings ตรงๆ (ชื่อของ Android 11 และของเก่า)

ถ้าเปิดได้ → คนไปกด **เปิด Wireless debugging** — Android 11 ต้องจับคู่ก่อน จะมี **IP:PORT + รหัส pair
6 หลัก** ให้จด/บอกกลับมา จากนั้น `adb pair` + `adb connect` คุมจอได้เลย · ถ้าต่อสาย USB เข้าคอมได้
ใช้ **USB debugging** แล้ว `adb tcpip 5555` ก็พอ (แค่ IP อย่างเดียวต่อ adb ไม่ได้บน Android 11)

แอปยังโชว์ให้เห็นด้วยว่า **Developer mode เปิดหรือยัง** และ **ADB ถูกล็อกโดยนโยบาย (MDM/Device Owner)
หรือไม่** — ถ้าถูกล็อก แปลว่าเปิดหน้า Developer options ไปก็เปิด debugging ไม่ได้ (ปุ่มเทา) ต้องไปทาง native เลย
(ไม่ต้องเหลือแอปนี้ค้างไว้ ใช้เสร็จถอนได้)

ถ้าลองทุกปุ่มแล้วยังเปิดไม่ได้ = ROM นี้ถอดหน้านี้ออกจริง → ต้องไปทาง **แอป control แบบ native**
(Accessibility + MediaProjection) แทน แอปนี้จึงเป็นแค่ตัว "ลองปลดล็อก" ที่ใช้แล้วถอนได้

## สร้าง APK

ต้องมี JDK 17 + Android SDK 34 (ชุดเดียวกับที่ใช้บิลด์ `emma-ai-voice`/`emma-robot-android`):

```powershell
cd apps\emma-panel-tools
.\gradlew.bat --no-daemon assembleDebug
```

APK อยู่ที่ `app\build\outputs\apk\debug\app-debug.apk` — ก๊อปไป USB แล้วลงบนจอ

## ใช้บนจอ

1. เสียบ USB ที่มี `app-debug.apk` → เปิด **File Manager ของจอ** → แตะไฟล์ → อนุญาตติดตั้ง
   (ถ้าเด้งเรื่อง "แหล่งที่ไม่รู้จัก" ให้อนุญาตให้ File Manager)
2. เปิดแอป **Emma Panel Tools**
3. กดปุ่ม **① เปิด Developer Options**
   - เปิดได้ → เปิด **Wireless debugging** ในนั้น แล้วจด **IP:PORT + รหัส pair** ส่งกลับมา
     (หรือถ้าต่อสาย USB เข้าคอมได้ ใช้ **USB debugging**)
   - เด้งกลับ/ว่างเปล่า/ปุ่มเทา → ROM หรือ MDM บล็อก: ลองปุ่ม ③ (แตะ Build number 7 ครั้ง) ก่อน แล้วลอง ① ใหม่
     ถ้ายังไม่ได้ แจ้งผลกลับมา (ไปทางแอป native)

## ทำไมแยกเป็นแอปนี้

ตั้งใจให้เล็กและทิ้งได้ ไม่ปนกับ `com.emma.ai.voice` (จอเสียง), `com.emma.robot.control`
(แอปหุ่น) — คนละแพ็กเกจ คนละหน้าที่ ลง/ถอนแยกกันได้
