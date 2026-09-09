# ชุดรับ SDK และ demo จากผู้ขาย

**สถานะ 9 ก.ย. 2026: ยังไม่ได้รับ AAR/demo APK ที่ยืนยันว่าตรงกับเครื่อง** โฟลเดอร์นี้ไม่ใช่ SDK หรือแอปหุ่นที่ build เสร็จแล้ว

ใส่ไฟล์ที่ผู้ขายส่งใน `vendor/aobo/incoming/` (ถูกกันออกจาก Git):

| ไฟล์ | สิ่งที่ต้องขอ |
|---|---|
| `aoborobotsdk-v2.0.aar` หรือรุ่นที่ผู้ขายรับรอง | รุ่นหุ่น/firmware, SHA-256, license, package names, dependencies, minSdk, ABI/native libraries |
| demo `.apk` | application ID, version, signing certificate fingerprint, วิธีเข้าโหมด MOCK และรายชื่อ permission |
| source demo `.zip` | Gradle wrapper, plugin/JDK/Android SDK เวอร์ชันที่ใช้ build, local SDK dependencies, ขั้นตอน build |
| คู่มือ callback | connection/arrival/failure/cancel/battery/charging และความหมายของ error codes |

คู่มือไทยหน้า 4 ระบุให้ใช้ AAR ที่มากับโปรเจกต์และติดต่อฝ่ายสนับสนุนเมื่อไม่ชัดเจน ไม่พบลิงก์ดาวน์โหลด AAR/APK ในคู่มือที่ตรวจ
[เว็บไซต์ Aobo](https://www.aoborobot.com/en/index_en.html) ระบุว่ามี SDK สำหรับพัฒนาต่อ แต่การค้นครั้งนี้ยังไม่พบไฟล์ดาวน์โหลดที่ยืนยันตรงรุ่น อย่าใช้ SDK ของ AUBO แขนกลอุตสาหกรรมเพียงเพราะชื่อคล้ายกัน

## เมื่อได้ไฟล์

```powershell
.venv-smoke\Scripts\python.exe -X utf8 scripts/inspect_robot_package.py "vendor/aobo/incoming/aoborobotsdk-v2.0.aar"
.venv-smoke\Scripts\python.exe -X utf8 scripts/inspect_robot_package.py "vendor/aobo/incoming/vendor-demo.apk"
# เมื่อผู้ขายให้ checksum ที่เชื่อถือได้ เพิ่ม --expected-sha256 <64-hex-digits>
```

ตัวตรวจอ่านโครงสร้าง ZIP, SHA-256, ABI และชื่อคลาสที่คู่มืออ้างถึงเท่านั้น ไม่แตกไฟล์ ไม่ติดตั้ง ไม่รันโค้ด และ **ไม่ได้รับรองผู้ผลิต/ลายเซ็น/ความเข้ากันได้/ความปลอดภัย** AAR อาจเปลี่ยนชื่อคลาสได้ ต้องเทียบกับผู้ขาย

APK ต้องตรวจเพิ่มด้วย Android Build Tools: `apksigner verify --verbose --print-certs <demo.apk>` แล้วเทียบ certificate fingerprint กับผู้ขาย; ใช้ `aapt dump badging <demo.apk>` ตรวจ application ID, minSdk และ ABI ก่อนติดตั้ง ดู [apksigner](https://developer.android.com/tools/apksigner) และ [รูปแบบ AAR](https://developer.android.com/studio/projects/android-library)

เครื่องมือ ADB อยู่ใน [tools/android](../../tools/android/README.md) เมื่อได้รับไฟล์ครบ ให้รัน `check_robot_readiness.py --sdk <AAR> --apk <APK> --health` ด้วย **APK ที่ระบุใน preflight ต้องเป็น Emma bridge ที่พัฒนาแล้ว ไม่ใช่ vendor demo ที่ยังไม่เชื่อม Emma**

## ข้อความพร้อมส่งให้ผู้ขาย

> ขอชุดพัฒนาที่ตรงกับหุ่น Astronaut/Aobo เครื่องที่จะส่งวันนี้ ได้แก่ SDK AAR, demo APK และ source demo ที่ build ได้พร้อม Gradle wrapper/dependencies/JDK version ครับ กรุณาระบุรุ่นหุ่น/firmware ที่รองรับ, minSdk/ABI, SHA-256 ของไฟล์, signing certificate ของ APK และไลเซนส์ด้วย ขอคู่มือ callback สำหรับไปจุดหมาย/หยุด/กลับฐาน/แบต/ชาร์จ วิธีปิดระบบสนทนาเดิมเพื่อใช้ไมค์กับแอปเรา และวิธีติดตั้งแอปผ่าน USB debugging ครับ รบกวนเตรียมสาธิต E-Stop, cancel และหยุดเมื่อเครือข่ายหลุดด้วย

> Please provide the SDK AAR, demo APK and buildable demo source for the exact Astronaut/Aobo robot being delivered, including Gradle wrapper, dependencies and JDK version. Please confirm supported firmware/model, minSdk, ABIs, file SHA-256, APK signing certificate and SDK license. We also need navigation/cancel/home/battery/charging callback documentation, access to the processed microphone audio, instructions to disable the built-in voice assistant, USB app installation access, and a demonstration of E-stop and connection-loss stopping.

ข้อความนี้จัดเตรียมไว้ให้ผู้ใช้ส่ง ยังไม่ได้ติดต่อผู้ขายแทนผู้ใช้ ดู [รายการตรวจรับ](../../docs/robot-arrival-2026-09-09.md) ก่อนเปิด REAL
