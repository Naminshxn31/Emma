# Android tools สำหรับรับหุ่น

ใช้ Android SDK Platform-Tools จาก [Google ทางการ](https://developer.android.com/tools/releases/platform-tools) แบบ local ใน `platform-tools/` ไม่แก้ PATH ของเครื่อง และไม่ commit binaries หรือไฟล์จากผู้ขาย

บนเครื่องที่เตรียมวันที่ 9 ก.ย. 2026 ดาวน์โหลดสำเร็จและรัน `adb version` ได้ **37.0.1-15733141** (ADB 1.0.41) การ clone repo ใหม่ต้องดาวน์โหลดเครื่องมือนี้ใหม่

```powershell
tools\android\platform-tools\adb.exe version
# เมื่อหุ่นมาถึง ต่อ USB และอนุญาต USB debugging บนจอหุ่นก่อน:
tools\android\platform-tools\adb.exe devices -l
# หากมีหลายเครื่องให้ระบุ -s <serial> ทุกครั้ง:
tools\android\platform-tools\adb.exe -s <serial> shell getprop ro.build.version.release
tools\android\platform-tools\adb.exe -s <serial> shell getprop ro.product.cpu.abilist
```

ยังไม่ติดตั้ง APK/เปลี่ยน firmware/ปลด bootloader จากชุดเตรียมนี้ ADB ไม่ใช่ Aobo SDK และไม่สามารถใช้ build APK ได้เอง ต้องขอ JDK/Gradle/Android SDK ที่ตรง source demo ของผู้ขายก่อน

ไฟล์ `download-receipt.json` เก็บ URL/เวลา/SHA-256 ของ archive ที่ดาวน์โหลดในเครื่อง เป็น checksum ที่คำนวณหลังดาวน์โหลด ไม่ใช่ checksum ที่ Google ยืนยันให้แยกต่างหาก หากต้องติดตั้งใหม่ใช้ลิงก์ Windows บนหน้าทางการ; ไฟล์ latest เปลี่ยนรุ่นได้

ตรวจ APK ตาม [ชุดรับ Aobo](../../vendor/aobo/README.md) ก่อนใช้ `adb -s <serial> install <demo.apk>` และใช้ package ID ที่ตรวจได้จริง การเปิด demo ของผู้ขายไม่ได้แปลว่า Emma bridge ทำงานแล้ว
