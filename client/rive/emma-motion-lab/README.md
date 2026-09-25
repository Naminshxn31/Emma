# Emma Motion Lab — ต้นแบบแยก

Experiment 05 ทดลองการเคลื่อนไหวต่อเนื่องของ Emma ด้วย Rive โดยคงรูปแบบโครเมียมเดิม
อ้างอิงจังหวะท่าค้าง การเอียงหัว การกลับ idle และเมนูเลือกท่าจาก
[Rive AI Robot Mascot Animation — Karina](https://contra.com/p/N3A3q6Lv-rive-ai-robot-mascot-animation)
ไม่ได้ใช้ไฟล์ตัวละครหรือ animation ของศิลปิน

## เปิดดู

รันจากรากโปรเจกต์ใน PowerShell:

```powershell
.\scripts\preview-emma-motion-lab.ps1
```

จะเปิด **หน้าต่าง Rive แยก** ไม่ใช่ `/preview` เดิม ไม่ต้องเปิดเซิร์ฟเวอร์เสียง
เลือกปุ่มด้านขวาเพื่อดูท่าทักทาย ค้นหา ส่ายหน้า/ยักไหล่ สแกน หรือเขิน
แต่ละท่าจบแล้วกลับ idle; เมื่อจบท่ากดซ้ำได้ และเปลี่ยนไปท่าอื่นระหว่างเล่นได้
`Play all motions` เล่นครบ 5 ท่าวนรอบ 35 วินาที; `Rest` หยุดทัวร์และคืนท่าพัก
ตอนพัก เลื่อนเมาส์ซ้าย/ขวาและขึ้น/ลงเพื่อเปลี่ยนมุมผิวใบหน้า ตา และปากพร้อมกันแบบ 2.5D
`gaze.luau` หน่วงการมองด้วย exponential damping และคืนหน้าตรงเมื่อเมาส์ออกจากหน้าต่าง
ระหว่างเล่นท่า ทิศมองจะตาม choreography และค่อยกลับไปรับเมาส์เมื่อจบท่า

ปิดหน้าต่างเพื่อหยุดพรีวิว เปิดใหม่ด้วยคำสั่งเดิม
หากหน้าต่างยังเปิดอยู่ แก้ source และ generate ใหม่ได้ โดยพรีวิว reload อัตโนมัติ

## โครงสร้าง

- `scene.rml`: scene ที่ generate แล้ว รวม UI ทดลองและ state machine `EmmaMotionLab`
- `../../../scripts/build-emma-motion-lab.py`: source ของ rig, UV ของฝ่ามือ และ keyframes (รันจากรากด้วย `python scripts/build-emma-motion-lab.py`)
- `../../../scripts/emma_lab_geometry.py`: การฉายผิวโดมในมุมจำกัดและการงอนิ้วจากจุดข้อต่อของนิ้วแต่ละนิ้ว
- `build/emma-motion-lab.riv`: ไฟล์ทดลองที่สร้างโดย CLI ไม่มีการคัดลอกทับไฟล์ production
- `build/emma-motion-lab.png`: ภาพเริ่มต้นจาก renderer จริง
- Font Inter คัดลอกจาก sample ที่มากับ Rive CLI ลง `assets/Inter.ttf` ในเครื่องขณะ generate
- ภาพหัว/ตัว/ตา/วงฐานอ่านจาก `../emma/assets` ไม่แก้ raster ต้นฉบับ; mesh เลือกบริเวณ artwork และเปลี่ยนพิกัด vertex ใน Rive
- `gaze.luau`: รับตำแหน่งเมาส์และส่ง `lookX`/`lookY` ที่หน่วงแล้วให้ joystick ฉายผิวหัว
- `assets/palm-open-v4.png`: แหล่ง texture ของฝ่ามือ/ข้อมือและนิ้วทั้งห้า ใช้ไฟล์เดียวในทุกท่าทั้งสองมือ; สร้างในรอบก่อนด้วย built-in imagegen รายละเอียดใน [assets/PROMPTS-v4.md](assets/PROMPTS-v4.md)
- `assets/head-turnaround-v4.png`, `assets/grip-magnifier-v1.png`, `assets/hand-relaxed-v2.png`: เก็บงานทดลองรุ่นก่อน แต่รุ่นนี้ไม่โหลด ไม่สลับภาพเหล่านี้ระหว่างการเคลื่อนไหว

หัวมี bone ที่จุดคอ แขนแต่ละข้างมี shoulder/elbow bones ความยาวคงที่ และ wrist node
มือทั้งสองใช้ mesh ฝ่ามือ/ข้อมือหนึ่งชิ้นและนิ้วห้าชิ้นต่อเนื่องกัน ไม่มีการสลับ asset หรือ opacity ของมือ
แต่ละนิ้วฉายการงอสองช่วงจากโคนของตัวเองเป็น vertex keyframes ใน Rive; ฝ่ามือไม่ถูกย่นตามช่องระหว่างนิ้ว
นิ้วโป้งมีการหุบเข้าหานิ้วอื่น ข้อมือเริ่มและหยุดช้ากว่าไหล่ ทุกท่ากลับรูปมือพักชุดเดียวกัน
แว่นเป็น vector prop แยกใต้ผิวนิ้ว ติดตามข้อมือเดียวกันและเผยขณะเตรียมจับ ไม่ได้ติดอยู่ในภาพกำมือ
หัวใช้ภาพเดิมภาพเดียว ฉายเป็นโดมตื้นแบบต่อเนื่อง จำกัดมุมหันประมาณ ±11° และก้มเงยเล็กน้อย
ไม่มีการ morph ระหว่างภาพหัวที่รูปทรง/แสงคนละแบบ จึงไม่เกิดก้อนขอบหัวจากช่วงเชื่อมภาพ
blink แยก layer จาก expression ของตา
ท่าต่าง ๆ ใช้ cubic easing และ keyframe เตรียมท่า/หยุดค้าง/คืนท่า
มี view model triggers: `idle`, `wave`, `search`, `deny`, `scan`, `shy`, `showcase`

## ขอบเขตรุ่นทดลอง

เป็น Rive 2D ใช้ texture กับ geometry แบบ 2.5D วัสดุและเงายังติดอยู่ในภาพ จึงไม่ใช่โมเดล 3D
การหันหัวตั้งใจจำกัดมุม ไม่รองรับการหันเห็นด้านข้างเต็มหน้า/ด้านหลัง
นิ้วแยก mesh แล้ว แต่การงอยังเป็นการฉาย texture และไม่ได้คำนวณการบังกันของนิ้วหรือแสงใหม่แบบ 3D
รูปมือพักและท่าจับแว่นยังไม่ตรง artwork อ้างอิงทั้งหมด แว่นเผยระหว่างเตรียมท่า ไม่มีการหยิบวัตถุจากฉากจริง; ยังไม่มี lip-sync
การแสดงใบหน้ายังคงรอยยิ้มโครเมียมเดิม ไม่มีการอ้างว่าเหมือนวิดีโอทุกเฟรม

โฟลเดอร์นี้ไม่มีการเชื่อมไมค์, WebSocket, โมเดลเสียง หรือฮาร์ดแวร์
ไม่ได้เปลี่ยน `client/rive/emma/scene.rml`, `client/assets/emma/emma.riv` หรือ `client/voice-preview.html`

## ตรวจ build

```powershell
.\scripts\preview-emma-motion-lab.ps1 -VerifyOnly
```

ใช้ Rive CLI 1.1.0 หรือรุ่นที่รองรับ RML skin/image mesh และ view model triggers
คำสั่งตรวจ verify, inspect และ render; ไม่ publish หรือ upload เข้า Rive account
