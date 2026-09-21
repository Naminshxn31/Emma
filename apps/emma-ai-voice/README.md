# Emma AI Voice

แอป Android สำหรับคุยกับ Emma ด้วยเสียงบนจอกลางของหุ่นหรือ Android Emulator
แพ็กเกจ `com.emma.ai.voice` แยกจาก `Emma Robot` และแอปทดสอบจอยทั้งหมด

แอปนี้เป็น WebView shell ของหน้า `/preview?app=1` ใน `condo-voice` จึงใช้ระบบเสียงจริงชุดเดียวกับเว็บ:

- อ่านรายชื่อเสียงจาก `/voices` และเลือกเสียงเริ่มต้นที่เซิร์ฟเวอร์ตั้งไว้
- อ่านค่าระบบและไมโครโฟนจาก `/health`
- ส่งเสียงไมค์และรับเสียงตอบกลับผ่าน `/ws`
- แสดง transcript, สถานะการฟัง/พูด และขยับปาก Emma ตามระดับเสียงตอบกลับ
- จบสายและคืนไมค์เมื่อแอปถูกพักหรือออกจากหน้าจอ
- ซ่อนแถบนำทาง Android ระหว่างใช้งาน แต่คงแถบสถานะและปุ่มตั้งค่าไว้
- เพิ่มระดับเสียงตอบของ Emma เฉพาะหน้าแอป 1.75 เท่า พร้อม limiter กันยอดเสียงแตก และให้ปุ่มเสียงของเครื่องปรับ Media volume โดยตรง

API key ของ Gemini/OpenAI อยู่ที่เซิร์ฟเวอร์เท่านั้น แอปไม่บรรจุ API key ลงใน APK
แอปเก็บที่อยู่เซิร์ฟเวอร์และ `WS_TOKEN` ใน SharedPreferences ส่วนตัวของแอป

Release build รับเฉพาะ URL แบบ HTTPS และไม่อ่าน `server_url`/`ws_token` จาก
Intent ภายนอก เพื่อไม่ให้แอปอื่นเปลี่ยน origin ที่ได้รับสิทธิ์ไมโครโฟนได้
Debug build อนุญาต HTTP เฉพาะ `localhost`, `127.0.0.1`, `::1` และ
`10.0.2.2` สำหรับพัฒนาในเครื่องหรือ Emulator เท่านั้น

## เปิดบนคอมด้วย Emulator

1. เปิดเซิร์ฟเวอร์จาก root ของ `condo-voice`:

   ```powershell
   .\.venv\Scripts\python.exe run_server.py
   ```

2. ส่งพอร์ต HTTPS ของคอมเข้า Emulator:

   ```powershell
   .\tools\android\platform-tools\adb.exe -s emulator-5554 reverse tcp:8001 tcp:8001
   ```

3. ติดตั้ง APK:

   ```powershell
   .\tools\android\platform-tools\adb.exe -s emulator-5554 install -r "apps\emma-ai-voice\app\build\outputs\apk\debug\app-debug.apk"
   ```

4. เปิด **Emma AI Voice** ใส่เซิร์ฟเวอร์ `https://127.0.0.1:8001` และค่า `WS_TOKEN`
   จาก `.env` แล้วกด **เชื่อมต่อและเปิด Emma**
5. กดปุ่มไมค์และอนุญาตใช้ไมโครโฟนในครั้งแรก

## เปิดบนหุ่นระหว่างพัฒนา

ถ้าเซิร์ฟเวอร์ยังรันบนคอม ให้ต่อ ADB แล้วใช้ `adb reverse tcp:8001 tcp:8001` เหมือน Emulator
แอปจึงใช้ URL เดิม `https://127.0.0.1:8001` ได้โดยไม่ต้องใส่ IP ของคอม
เมื่อย้ายเซิร์ฟเวอร์เข้า Android ของหุ่น URL นี้ยังใช้ได้เช่นกัน

ไอคอนรูปเฟืองมุมขวาบนใช้เปลี่ยน server URL หรือ token แอปขอเฉพาะสิทธิ์อินเทอร์เน็ตและระบบเสียง
และไม่เรียกกล้อง ฐานล้อ แขน หรือแอป Aobo

## สร้าง APK

ต้องมี JDK 17 และ Android SDK 34:

```powershell
cd apps\emma-ai-voice
.\gradlew.bat --no-daemon assembleDebug lintDebug
```

APK อยู่ที่ `app/build/outputs/apk/debug/app-debug.apk`

ก่อนแจกใช้งานจริง ให้สร้าง release build ที่ลงลายเซ็นแล้ว; release ใช้
`network_security_config.xml` ซึ่งปิด cleartext traffic ส่วน debug ใช้
`network_security_config_debug.xml` แยกต่างหาก
