# การตรวจทางอ่านและปรับระยะหลบสิ่งกีดขวางของฐานหุ่น

วันที่ 2 ตุลาคม 2026 — ตรวจเอกสารผู้ผลิต โค้ด Aobo ที่สำรองไว้ และคำขออ่านสถานะจากหุ่นจริงเท่านั้น ไม่เปิดแอป Aobo ไม่เปลี่ยนแผนที่หรือค่าฐาน และไม่อัปโหลด CUBE config

## ข้อสรุป

**อัปเดต 18:10 — อ่านค่าจริงจากบอร์ดได้แล้ว และ Side Margin ไม่ใช่ตัวที่ต้องลด:** `motion_plan.side_margin = 0.01` ม. (1 ซม.) อยู่แล้ว ส่วนเส้นผ่านศูนย์กลางหุ่น (`base.robot_size`) คือ 0.55 ม. ดังนั้นประตูกว้าง 85 ซม. เหลือระยะข้างละ 15 ซม. พอดีเมื่อหุ่นอยู่กลางประตู การลด Side Margin ลงอีกจึงไม่ทำให้หุ่นผ่านประตูง่ายขึ้น ส่วนที่ทำให้หุ่นหยุดอยู่ในชั้นอื่น เช่น โซนาร์ planner ท้องถิ่น และกล้องความลึก ดูหัวข้อ "ค่าจริงจากบอร์ด" ข้างล่าง ยังไม่มีการเขียนค่าใดกลับไปที่บอร์ด

ค่าระยะอัลตราโซนิกในแอป Aobo เป็นอีกชั้นหนึ่ง; เมื่อแอป Aobo ปิดอยู่ ไม่ควรถือว่าค่านี้คุ้มครองการเดินที่ Emma สั่งผ่าน SLAMWARE

## ค่าจริงจากบอร์ด (2 ต.ค. 18:10)

- **บอร์ดเป็น Slamware Core (ชนิดฐาน `zeus`) ไม่ใช่ SlamCube** หน้าเว็บพอร์ต 80 ขึ้นชื่อ "Slamware Core Administration" และเส้นทางของ SlamCube Config Tool (`/service/system/cube/config`) ตอบ 404 ทั้งพอร์ต 80 และ 1448 เครื่องมือที่ตรงกับบอร์ดนี้คือ Slamware Config Tool
- RoboStudio 2.1.1 จาก ZIP ทางการต่อบอร์ดได้และเห็นแผนที่ แต่หลังต่อประมาณ 13 วินาที ทุก request ก็ timeout รวมถึง "Get Robot Config" อาการนี้เกิดเหมือนกันทั้งเมื่อต่อผ่าน adb และเมื่อต่อตรงผ่าน LAN จึงไม่ใช่ปัญหาของท่อ adb แต่ตรงกับที่เคยพบว่า RoboStudio บน PC ใช้กับ SDP 6.2 ไม่ได้
- RoboStudio ที่ติดตั้งใน `C:\Slamtec\RoboStudio` ดาวน์โหลดปลั๊กอินจาก store ได้ แต่ตั้งชื่อโฟลเดอร์ด้วยเลขไทยตาม locale ของเครื่อง (`config_tool_๑๗๔๐…`) ทำให้โหลด DLL ไม่ได้ (error 126) จึงต้องใช้ตัวใน ZIP
- **ทางอ่านที่ใช้ได้:** ส่งคำสั่งอ่านของ SDK แบบ JSON จากในตัวหุ่นไปยัง `192.168.11.1:1445` ข้อความคั่นด้วย `\r\n\r\n` เป็นคำสั่งเดียวกับที่ RoboStudio ใช้ ชื่อคำสั่งพบใน `robostudio.exe` โดยเริ่มจาก `getsdpversion` (ได้ `6.2.0-rc1 (Feb 9 2025)`) แล้วจึงส่ง `getrobotconfig` ได้ JSON ขนาด 18 KB ทั้งสองคำสั่งเป็นคำสั่งอ่าน
- สำรองไว้ที่ `Downloads\robot_config_20261002.json` (เก็บไฟล์คำตอบดิบไว้ที่ `robot_config_raw_20261002.bin` ด้วย) ไฟล์อยู่นอก repository และไม่มีรหัสผ่านหรือ token

| ค่า | บนบอร์ด | ความหมาย |
|---|---|---|
| `motion_plan.side_margin` | 0.01 ม. | ระยะเผื่อรอบตัว ต่ำอยู่แล้ว |
| `base.robot_size` | 0.55 ม. | เส้นผ่านศูนย์กลางที่ planner ใช้ (ตรงกับตัวจริง ห้ามแก้) |
| `base.wheel_span` | 0.32 ม. | ระยะล้อ |
| `motion_plan.local_costmap.narrow_way_robot_size` | 0.5 ม. (เปิดใช้) | ขนาดที่ใช้ในทางแคบ |
| `motion_plan.global_path_finder.max_cost_distance` | 0.2 ม. | แถบ cost นอกตัวหุ่น — น่าจะเป็นเหตุที่เส้นทางเลี่ยงผนังประมาณ 0.40 ม. |
| `motion_plan.dwa_local_planner.inflate_radius` | 0.3 ม. | การขยายสิ่งกีดขวางของ planner ท้องถิ่น |
| `mapping.observation_map.ultra_obstacle_hold_in_front_dist` | 0.5 ม. | สิ่งที่โซนาร์เห็นด้านหน้าภายในระยะนี้ถูกค้างไว้บนแผนที่ |
| `mapping.observation_map.ultra_obstacle_hold_directly_dist` | 0.2 ม. | ระยะค้างโดยตรงของโซนาร์ |

เซนเซอร์มีโซนาร์ 7 ตัว ได้แก่ ตัวหน้า (x 0.20), ตัวข้างที่ y ±0.25 ซึ่งหันออกด้านข้าง ±66° และตัวหน้าที่สูง z 0.52 และ 0.70 นอกจากนี้มีกล้องความลึก (x 0.10, z 0.44, มุมมอง ±40°) และ bumper

ข้อที่ยังเป็นสมมติฐาน: อาการที่หุ่นหยุดข้างวงกบ (ประมาณ 0.35 ม.) และหยุดหน้าผนัง (ประมาณ 0.6 ม.) น่าจะมาจากโซนาร์ข้าง/หน้ากับค่า hold และ planner ท้องถิ่น ไม่ใช่ Side Margin ยังไม่ได้ทดลองแยกว่าค่าใดเป็นสาเหตุ บอร์ดมีคำสั่งเขียน (`updaterobotconfig`, `updatebinaryconfig`) แต่ยังไม่รู้รูปแบบข้อมูลที่ต้องส่ง และเคยพบว่าคำสั่งที่ PC รายงานว่า timeout ถูกบอร์ดทำไปแล้ว ดังนั้นห้ามเขียนค่าก่อนเจ้าของอนุมัติและผู้ขายยืนยัน

## หลักฐานที่ตรวจ

- เจ้าของตรวจ RoboStudio บนหุ่นครบทุกหน้าแล้ว: มี Speed, Navigation, Sensors ที่ว่าง, และหน้า Configurations and logs ที่แสดง CUBE กับปุ่ม UPLOAD แต่ไม่มี Motion Plan หรือ Side Margin เจ้าของไม่ได้แก้ค่าใดในหุ่น
- [Slamtec REST OpenAPI](https://docs-en.slamtec.com/opt/swagger-conf.json) ระบุ `PUT /api/core/system/v1/cube/config` สำหรับอัปโหลดไฟล์ทั้งชุด โดยไม่มี `GET` ของเส้นทางนี้; `GET /api/core/system/v1/parameter` ระบุเฉพาะ `base.max_moving_speed`, `base.max_angular_speed` และ `docking.docked_register_strategy` ไม่ระบุ Side Margin หรือขนาดหุ่น
- อ่าน API หุ่นจริงผ่าน Android แบบ read-only: `GET .../parameter?param=base.max_moving_speed` สำเร็จ แต่ `motion_plan.side_margin` และ `base.robot_size` ไม่สำเร็จ จึงไม่มีหลักฐานว่าเฟิร์มแวร์ Aobo เปิดสองค่านี้ผ่าน REST
- [คู่มือ RoboStudio ของ Slamtec](https://wiki.slamtec.com/download/attachments/83066883/SA008_SLAMTEC_slamware_robot_studio_appnote_v1.5_en.pdf?api=v2&modificationDate=1726643956979&version=1) หน้า 39–40 อธิบาย SlamCube Config Tool บน Windows: `Custom Configuration → Connect Device and Load` เพื่ออ่านค่าจากหุ่น และ `Export` เพื่อบันทึกไฟล์ ทางนี้ *อาจ* ช่วยสำรองค่าจริง แต่ยังไม่ได้พิสูจน์ว่าใช้กับฐาน Aobo รุ่นนี้ได้ ห้ามกด `Upload` ก่อนมีสำเนาที่ตรวจย้อนกลับได้
- [คู่มือ Slamware Console ของ Slamtec](https://wiki.slamtec.com/pages/viewpage.action?pageId=1015836) มีคำสั่ง `base -c serial -p COM3 config` ที่อ่าน `robot_size`, ตำแหน่งเซนเซอร์ และ `motion_plan.side_margin` จาก Control Bus โดยตรง ตัวอย่างเป็นฐานอ้างอิงรุ่นเก่าและต้องเข้าถึงพอร์ตภายใน จึงเป็นคำถามสำหรับ Aobo/Slamtec ไม่ใช่ขั้นตอนให้ลองกับหุ่นนี้ทันที
- จากสำเนาโค้ด `FloatRecordService$RobotServiceReceiver`: คำสั่ง `ultrasound` ของ Aobo รับระยะ 10–150 ซม. แล้วเก็บใน SharedPreferences; `getconfiguration` คืนสถานะที่แอปถือไว้ (`front_ultrasound`, `back_ultrasound`, `ultrasound_distance`) และ `getcurrentdistance` ใช้ขอข้อมูลเซนเซอร์ ค่านี้ไม่ใช่ Side Margin ของ SLAMWARE สำเนา SharedPreferences และไฟล์ปัจจุบันที่อ่านแบบ read-only ไม่พบค่า `ultdistance` ที่ตั้งค้างไว้ และแอป Aobo ไม่ได้รันในรอบตรวจ
- `aoborobotsdk-v2.1.2.aar` ที่ใช้กับ Emma ไม่มีคลาส Aobo สำหรับตั้งอัลตราโซนิกโดยตรง; เส้นทางคำสั่งที่พบอยู่ใน service ของแอป Aobo ที่ติดตั้งแยกต่างหาก

## ตรวจไฟล์ในแอปบนหุ่นเพิ่มเติม

- ยืนยันเครื่องผ่าน serial และขนาดจอแล้วว่า ADB ต่อกับหุ่นตัวจริง ตรวจแบบอ่านอย่างเดียวในพื้นที่ส่วนตัวของ `com.slamtec.robostudio`: `files/datastore` มีเพียง `app_map_configuration.preferences_pb` (28 ไบต์; ค่าการรีเฟรชแผนที่) และ `devices.preferences_pb` (155 ไบต์; ประวัติการเชื่อมต่อ) ไม่มีไฟล์ CUBE หรือฐานข้อมูลค่าฐาน พื้นที่ `shared_prefs` และแคช WebView ที่ไล่รายชื่อก็ไม่พบไฟล์ดังกล่าว
- ใน APK RoboStudio ที่สำรองไว้พบชื่อเมธอด `uploadCubeConfig` และ `setCubeConfig` กับ `cubeConfigPath` แต่ไม่พบชื่อเมธอดอ่าน CUBE กลับ การค้นชื่อไฟล์ในพื้นที่เก็บร่วมของ Android ไม่พบไฟล์ CUBE การมีชื่อ `CUBE` บนหน้าจอจึงไม่พิสูจน์ว่า Android เก็บไฟล์ต้นฉบับไว้
- พื้นที่ส่วนตัวของ Aobo มี `slamwareinit.xml` ที่อ้างไฟล์แผนที่ `.stcm` แต่ไม่พบคีย์ Side Margin หรือไฟล์ CUBE จากรายชื่อไฟล์ที่ตรวจ แผนที่ `.stcm` ไม่ใช่ชุดค่าขนาดหุ่นและตำแหน่งเซนเซอร์
- ลอง `GET /api/core/system/v1/cube/config` จาก Android ไปยังบอร์ดแบบอ่านอย่างเดียว ได้ `HTTP 404 Not Found` ตรงกับ OpenAPI ที่ประกาศเฉพาะ `PUT` สำหรับเส้นทางนี้ จึงยังไม่มีทางดาวน์โหลด CUBE ผ่าน REST ที่ตรวจพบ

## ตรวจทางอ่านจากบอร์ดโดยตรง

- ยืนยันว่าบอร์ดเข้าถึงได้จาก Android ผ่านพอร์ต SDK 1445 แล้วสร้างสะพาน TCP ชั่วคราวจากคอมผ่าน ADB ไปยังพอร์ตนี้ ทดสอบเปิดการเชื่อมต่อ TCP สำเร็จ แต่ยังไม่ได้ส่งคำสั่งอ่าน CUBE ผ่าน SDK
- เว็บจัดการของบอร์ดที่พอร์ต 80 เปิดหน้าสถานะและเมนูดูแลระบบ ไม่พบเมนูส่งออก CUBE จากรายการเมนูที่อ่าน ส่วน REST `GET` สำหรับ CUBE ตอบ 404 ตามผลข้างต้น
- RoboStudio ที่ติดตั้งบนคอมมีชุดแพ็กเกจ 1.1.0 และโฟลเดอร์ปลั๊กอินว่าง จึงไม่มี SlamCube Config Tool ในเมนูที่เปิดตรวจ
- ดาวน์โหลด [RoboStudio 2.1.1 จากหน้า Slamtec](https://wiki.slamtec.com/display/SD/Overseas+Help+Center) ไว้นอก repository ตรวจ ZIP ทั้งชุดผ่าน และพบปลั๊กอิน `robostudio.plugin.cube_cfg_tool` กับ `robostudio.plugin.config_tool` จริง ยังไม่ได้เปิดโปรแกรมที่ดาวน์โหลดมาเพื่ออ่านค่าหุ่น เพราะทักษะ computer-use กำหนดให้ขออนุมัติทันที ก่อนรันซอฟต์แวร์ใหม่บน Windows
- ปิดสะพาน TCP ชั่วคราวและ ADB forward หลังการตรวจ ไม่เปลี่ยนค่าบอร์ดหรือแผนที่ ผลนี้ยืนยันว่ามีเส้นทางเชื่อมต่อ แต่ยังไม่ยืนยันว่า CUBE config ของหุ่น Aobo อ่านกลับได้หรือมีค่า Side Margin ใด

## ทางดำเนินการที่ยังต้องยืนยัน

1. ให้ Aobo ส่ง CUBE config **ของหุ่นตัวนี้** พร้อมรุ่นเฟิร์มแวร์ วิธีอ่านกลับ/สำรอง และวิธีกู้คืน หรือให้ช่าง Aobo อ่านค่าจริงและปรับด้วยเครื่องมือที่รองรับรุ่นนี้
2. ~~ลอง RoboStudio for Windows รุ่นใหม่~~ ลองแล้วเมื่อ 2 ต.ค.: RoboStudio 2.1.1 timeout กับบอร์ดนี้ แต่ใช้คำสั่ง SDK `getrobotconfig` อ่านค่าครบแล้ว (ดูหัวข้อ "ค่าจริงจากบอร์ด") ถ้าจะเปลี่ยนค่า ให้ถามผู้ขายก่อนว่า `updaterobotconfig` รับข้อมูลรูปแบบใด และกู้คืนจากไฟล์ที่สำรองไว้ได้อย่างไร
3. ถาม Aobo โดยตรงว่าการหยุดด้วยอัลตราโซนิกทำงานจากฐานเองหรือเฉพาะเมื่อแอป Aobo รันอยู่ และมี API ที่อ่านค่า/ตั้งค่าได้โดยไม่ให้ Aobo แย่งการควบคุมจาก Emma หรือไม่

การขยาย virtual wall/พื้นที่ห้ามผ่านบนแผนที่ช่วยเฉพาะสิ่งกีดขวางคงที่ ไม่เท่ากับเพิ่มระยะปลอดภัยรอบคนที่เคลื่อนที่ และการลดความเร็วไม่เปลี่ยนขนาดตัวหุ่นที่ planner ใช้
