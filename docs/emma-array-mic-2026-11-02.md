# ไมค์อาร์เรย์เป็นของแอปคนเดียว — เส้นเสียงใหม่ (ARRAY_MIC)

เจ้าของ 2 พ.ย. 2026: *"ต้องแก้แอปให้ถือไมค์เองและทำเส้นเสียงใหม่ ลองทำดูได้ไหม ให้ดูผลการหันหัวกับกล้องก่อน ทำแล้วรีวิวมา"*

**สถานะ:** ทำแล้วทั้งสามส่วน (แอป หน้าหุ่น เซิร์ฟเวอร์) **ปิดเป็นค่าเริ่มต้นทุกสวิตช์ ยังไม่เคยรันบนหุ่น** · `robot.env` ไม่ได้แตะ ·
APK สร้างแล้วยังไม่ได้ลง: `.tmp/apk/emma-robot-arraymic-callhold-20261102.apk` (sha256 c868e71a…66fe9c; รุ่นแรก `emma-robot-arraymic-20261102.apk` ล้าสมัย — ห้ามลง) · ลำดับ: **ทดสอบหันหัว+กล้อง (`docs/robot-servo-limits-2026-10-31.md`) ก่อน**
แล้วค่อยแผนในข้อ 6

---

## 1. ปัญหาที่วัดแล้ว และสิ่งที่ขาด

- **10 ต.ค. (วัดบนหุ่น):** อาร์เรย์ Bothlent ให้ Android **สตรีมเดียว** — แอปเปิดก่อน = ไมค์ของหน้าเว็บ (getUserMedia) อ่านได้ศูนย์ล้วนทั้งสาย
  (Emma หูดับ 37 รายงาน) · หน้าเว็บเปิดก่อน = แอปได้ 2 จาก 8 ช่อง → `DOA_ENABLED=false` ใน robot.env ตั้งแต่นั้น
- ของที่มีอยู่แล้ว: `ArrayListener.java` (เปิด 8 ช่อง, DOA, beam), `ArrayBeam.java`, `app/doa.py` (ทิศ + calibration จากหน้า),
  `app/array_audio.py` (สตรีม beam + `Tap` ที่ "หน้าตัดสินว่าเมื่อไหร่ อาร์เรย์ตัดสินว่าอะไร") — แต่ทั้งหมดออกแบบบนสมมติฐานว่าสองทางเปิดพร้อมกันได้
- ที่ขาด (`docs/emma-multi-person-2026-11-02.md` §3): หน้าเว็บต้องไม่เปิดไมค์เลย · echo ต้องจัดที่เซิร์ฟเวอร์ · สตรีมตาย = สายหูดับ ต้องมีตัวตรวจ+คืนไมค์

## 2. การออกแบบ

### 2.1 เจ้าของเดียว

- เซิร์ฟเวอร์เปิด `ARRAY_MIC` → `doa_ready` ใส่ `array_mic` **เฉพาะแอปที่ user agent ของลิงก์มี `EmmaArrayMic/1`** → แอปเปิดอาร์เรย์ 8 ช่อง
  ตอนหน้า Emma อยู่ด้านหน้า (เหมือน DOA เดิม) และเป็นเจ้าของคนเดียว ส่ง beam (DS ค่าเริ่ม) ขึ้นลิงก์ `/ws/robot-doa` ทุก 16 ms
- **หน้าหุ่นไม่เรียก getUserMedia เสียงเลย** ในโหมดนี้ (กล้องเหมือนเดิม — วิดีโออย่างเดียว ไม่ชนกับเสียง)
- หน้าหุ่นเข้าโหมดเมื่อ **ครบทุกข้อ** ตอน `ready` ของแต่ละสาย:
  1. สวิตช์เซิร์ฟเวอร์ (`ARRAY_MIC` + `DOA_ENABLED` + `ARRAY_AUDIO`, ไม่ใช่ `MULTI_SESSION`)
  2. user agent ของ WebView มี `EmmaArrayMic/1` (และ `EmmaBridge/1`) — **บทเรียน EmmaBridge/1:** APK เก่าต้องไม่ได้หน้าที่รอสะพานที่มันไม่มี ·
     และ URL ของสายมี `array_mic=1` (JS ของหน้ารู้จักโหมดนี้ — หน้าที่เปิดค้างจากก่อนเซิร์ฟเวอร์รีโหลดยังเป็น JS เก่า: บทเรียน echo_barge
     → ได้ `old_page` = เก็บไมค์ไว้ แอปถูกสั่งปล่อย)
  3. สตรีมอาร์เรย์ live (มี chunk ใน 120 ms, ไม่ใช่ศูนย์ล้วน, สถานะแอป capturing, ต่อเนื่อง ≥ 0.5 วิ) — รอได้ถึง `ARRAY_MIC_START_WAIT_S` (1.5 วิ)
     เพราะแอปที่เพิ่ง resume ยังเปิดอาร์เรย์อยู่
  4. ไม่ใช่ barge-in / hold-speech / input rate อื่น (อาร์เรย์ไม่มี AEC)
- ไม่ครบ → `ready.array_mic: false` + เหตุผล + `array_mic_scope` → แอปหยุดจับก่อน แล้วหน้าเปิดไมค์ของตัวเอง
  **หลังจากแอปบอกว่าการจับเสียงจบแล้วเท่านั้น** · ขอบเขตแยกตามเหตุผล (รีวิวของ lead 2 พ.ย. — รุ่นแรกล็อกทุกเหตุผลทั้งรอบแอป:
  หุ่นบูต/เซิร์ฟเวอร์รีสตาร์ตทุกครั้งที่ deploy → ลิงก์แอปยัง back-off 2/4/8/16/32 วิ ตอนสาย standing line แรก → `no_link` → ปิดอาร์เรย์ทั้งวันเงียบๆ):
  - **ทั้งรอบแอป (`scope=run`)** เฉพาะเหตุผลที่ไม่เปลี่ยนในรอบนี้ — `old_page`, `old_app`, `released`, `input_rate`, `barge_in`, `hold_speech`
    (`array_audio.FINAL_REASONS`): สะพาน `emma-face:mic:release` จากหน้า **และ** `array_release` ลงลิงก์ (หน้า JS เก่าไม่เคยถามสะพาน)
  - **เฉพาะสายนี้ (`scope=call`)** ทุกเหตุผลอื่น — `no_link`, `not_capturing`, `stale`, `zeros`, `recovering` (คือ "ตอนนี้" ไม่ใช่คำตัดสินทั้งวัน):
    หน้าขอผ่านสะพาน `emma-face:mic:hold` เท่านั้น (**ไม่พึ่งลิงก์** — `no_link` แปลว่าลิงก์ยังไม่ขึ้น) แล้ว `emma-face:mic:free` เมื่อสายจบ
- ไม่มี `array_mic` ใน `ready` (สวิตช์ปิด, หน้าอื่น, APK เก่า) = เส้นทางวันนี้ทุกไบต์
- **APK ใหม่จับอาร์เรย์เฉพาะในฐานะเจ้าของคนเดียว:** `ARRAY_MIC` ปิดแต่ `DOA_ENABLED` เปิด → ลิงก์ของ APK ใหม่ได้ `doa_off` ·
  APK เก่า (ไม่มี marker) ขณะ `ARRAY_MIC` เปิด → `doa_off` เหมือนกัน → **ไม่มีการตั้งค่าไหนที่ทำให้สองทางจับพร้อมกัน** และย้อนกลับได้ขั้นเดียว

**ไม่มีทางจับพร้อมกัน — กลไกจริง:** กติกาอยู่ใน `MicOwner.java` (Java ล้วน รันบน JVM ในเทสต์) · แอปเรียก `mayStart()` ใต้
`synchronized (micOwner)` ตรงที่สร้างเธรดจับเสียง และทุก hold/release ใช้ล็อกเดียวกัน · สะพานตอบ `capturing` = "ยังต้องการ หรือเธรดที่ถือ
AudioRecord ยังไม่จบ" (เธรดจบหลัง `r.release()`) · หน้าเว็บ poll ทุก 100 ms และเปิดไมค์**เฉพาะเมื่อได้ `capturing:false` ชัดๆ**
(คำตอบแปลก/bad_request ไม่นับ) หรือครบ 3 วิ (แล้วบันทึก `timeout`) — เหมือนเดิมทุกข้อ ทั้ง hold และ release

**hold ของสาย — ปล่อยเมื่อไหร่ และไม่ค้างข้ามหน้า:**
- หน้าทำเครื่องหมาย hold **ก่อน**ถามสะพาน (สายที่ถูกรื้อระหว่างรอก็ยังปล่อย) และปล่อย (`emma-face:mic:free`) ใน `releaseCallHardware`
  **หลังบรรทัดที่หยุด track ไมค์ของสาย** — ทุกทางออกของสายผ่านฟังก์ชันนี้ (socket ปิด, ไมค์พัง, สายใหม่เริ่ม) (เทสต์รันฟังก์ชันจริงใน node ดูลำดับ)
- แอปได้ free แล้ว**รอให้ recording ของ WebView ที่ยังเปิดตอนนั้นจบก่อน** (≤ `FREE_WAIT_MS` 3 วิ) จึงเปิดอาร์เรย์ใหม่เมื่อลิงก์พร้อม — track stop
  ใน renderer เป็นแบบ async; เปิดทับ = ปัญหา 10 ต.ค. และ `checkConcurrency` จะ latch "conflict" จนกว่าจะ resume
- **hold ไม่ค้างข้ามหน้า = รีเซ็ตตอนโหลดหน้า:** สิ่งแรกที่บล็อกสะพานของหน้าใหม่ทำ (ก่อนสายใดๆ ของหน้านี้จะ hold ได้) คือ `emma-face:mic:free`
  — hold ของ document ก่อน (โหลดใหม่ตอน resume, renderer ตาย, เปลี่ยนหน้า) ไม่มีสายอยู่ข้างหลังแล้ว (เทสต์ Chromium จริง: คำถามแรกของหน้าคือ free)
- resume ของแอปไม่ล้างทั้ง hold และ release (hold หน้าเป็นคนปล่อย, release ต้องเปิดแอปใหม่)

### 2.2 หน้าเว็บยังเป็นนาฬิกา — เสียงเงียบแทนไมค์

แทน getUserMedia หน้าเว็บสร้าง `ConstantSource(0)` → worklet `cap-silent` ที่ปล่อยเฟรม 20 ms ของศูนย์ล้วนตามเวลา (ไม่ขึ้นกับ input) →
**`handleMicAudio` ตัวเดิม** → ด่านทุกตัวของวันนี้ทำงานตามเดิมทุกไบต์: half-duplex (ไม่ส่งระหว่างเธอพูด), echo guard 350 ms, mute, hold-speech (ปิดอยู่บนหุ่น),
summoned hold (อยู่ที่เซิร์ฟเวอร์ก่อน tap) — เซิร์ฟเวอร์เอาเสียงอาร์เรย์ใส่แทนทุกเฟรม (`Tap(sole=True)`)

**ทำไมไม่ทำแบบอื่น:**
- *ให้หน้าส่งข้อความ "เปิด/ปิด" แล้วเซิร์ฟเวอร์ดึงอาร์เรย์เอง* — ต้องลอก half-duplex/echo guard/mute/hold มาไว้ฝั่งเซิร์ฟเวอร์ = สองนาฬิกาที่เพี้ยนออกจากกัน
  (บทเรียน "นาฬิกาสองเรือน" ใน CLAUDE.md)
- *ให้แอปส่งเสียงเข้า `/ws` ของสายตรงๆ* — ต้องย้าย state machine ของสายทั้งตัวไปไว้ในแอป
- ราคาของแบบที่เลือก: ศูนย์ 32 kB/วิ บน loopback ตอนหน้าไม่ได้ปิดไมค์ — ไม่มีนัยสำคัญ
- **ตัวกันที่ต้องมีคู่กัน:** ถ้า WebView ไม่รัน worklet บน input เงียบ จะไม่มีเฟรมถึงเซิร์ฟเวอร์เลย — แม้แต่ศูนย์ที่ตัวตรวจหูดับนับ
  (= สายหูดับแบบเงียบ) → หน้าเว็บนับเฟรมของตัวเอง ไม่มีเลยใน 2 วิ (`ARRAY_CLOCK_CHECK_MS`) = คืนไมค์เอง (`mic_state array_no_clock`) ·
  วัดแล้วบน Chromium ของ PC (playwright): ≥ 40 เฟรมศูนย์ใน 1.5 วิ ไม่มี getUserMedia — **WebView 124 ของหุ่นยังไม่ได้วัด**

### 2.3 Standby / standing line

บนหุ่น "standby" คือ **standing line** (`STANDING_LINE=true`): สาย Gemini ที่เปิดค้าง = เป็นสายปกติ ผ่าน `ready` → ได้อาร์เรย์ทางเดียวกัน ·
`/ws/wake` บนหุ่นเป็นกระดิ่งอย่างเดียว (`WAKE_ENABLED=false` — ไม่มีไมค์) · ระหว่างสายหนึ่งจบกับสายใหม่ (~3 วิ) และตอน mute hold
หน้าเว็บไม่ส่งเฟรม = ไม่มีเสียงใครถูกใช้ · **แต่แอปยังจับและส่ง beam ขึ้นเซิร์ฟเวอร์ในเครื่องตลอดที่หน้า Emma อยู่ด้านหน้า** (เหมือน ARRAY_AUDIO เดิม)
เซิร์ฟเวอร์เก็บไว้ไม่เกิน 1 วิแล้วทิ้ง ไม่มีอะไรไป Gemini ถ้าไม่มีเฟรมของสายมาขอ — "mute" จึงแปลว่าไม่ส่ง ไม่ใช่ไมค์ปิดทางกายภาพ (ไมค์ของหน้าเว็บวันนี้ก็เหมือนกัน)

### 2.4 หูดับ → คืนไมค์อย่างเป็นระเบียบ

| ที่ไหน | เงื่อนไข | แถว log |
|---|---|---|
| ที่ไหน | เงื่อนไข | ขอบเขต | แถว log |
|---|---|---|---|
| ตอน `ready` | final: หน้า JS เก่า / แอปเก่า / ปล่อยไปแล้ว / barge-in / hold-speech / input rate | ทั้งรอบแอป | `array_mic_handback phase=start scope=run why=…` |
| ตอน `ready` | อื่นๆ: ไม่ live ภายใน 1.5 วิ (no_link, not_capturing, stale, zeros, recovering) | สายนี้ | `… phase=start scope=call why=…` |
| กลางสาย (เซิร์ฟเวอร์) | ไม่มี chunk ที่มีเสียงนาน `ARRAY_MIC_DEAF_S` (2 วิ) นับจาก chunk ดีล่าสุด · **ไม่ตัดสินระหว่าง/หลัง loop ค้าง** (ด้านล่าง) | สายนี้ (deaf hold) | `… phase=call scope=call why=… dead_ms` |
| กลางสาย (หน้า) | นาฬิกาเงียบไม่เดินใน 2 วิ | สายนี้ (deaf hold) | `mic_state why=array_no_clock` |
| ครั้งที่ 3 ของ deaf hold ในรอบแอปเดียว | นับในแอป (`MicOwner.DEAF_HOLDS_PER_RUN` = 3) | ทั้งรอบแอป | สถานะแอป `released` (`deaf_x3`) |
| หน้าที่ยืนยันตัวไม่ได้ (ROBOT_FACE_TOKEN) | ไม่ได้เสียงอาร์เรย์ → หูดับ → คืนไมค์ให้หน้านั้น · เซิร์ฟเวอร์ไม่ส่งอะไรถึงแอป | สายนี้ | `why=not_robot_face` |

ลำดับกลางสาย: เซิร์ฟเวอร์ส่ง `array_mic_lost` ให้หน้า (**ไม่ส่งอะไรถึงแอปแล้ว** — hold ของสายเป็นคำขอของหน้าเองผ่านสะพาน) → หน้าเรียก
`emma-face:mic:hold:deaf` → poll จน `capturing:false` → getUserMedia → ต่อ mic chain + worklet `cap` เดิมเข้า AudioContext เดิม แทน silent source
(`mic_state array_lost held` แล้ว `page`) · สายจบ → free → สายถัดไปกลับมาใช้อาร์เรย์ · หลังคืนไมค์ tap ส่งเฟรมของหน้าตรงๆ ตลอดสาย ไม่กลับไปอาร์เรย์อีก
แม้มันฟื้น · ช่วงระหว่างคืน (≤ 2 วิหูดับ + ≤ 3 วิรอแอป + getUserMedia) คือเสียงที่หาย

**ทำไมเพดาน 3 ต่อรอบแอป:** แต่ละครั้งที่หูดับกลางสาย ลูกค้าเสียเสียงพูด 2–5 วิ · ครั้งสองครั้งต่อวันคือ USB สะดุดหรือ loop ค้าง · ครั้งที่สามในรอบเดียวกัน
บอกว่าเส้นอาร์เรย์ของรอบนี้ไม่น่าไว้ใจ — ใช้ไมค์หน้าเว็บจนเปิดแอปใหม่ไม่เสียอะไรที่ได้ยิน · hold ตอน `ready` (บูต/รีสตาร์ต) **ไม่นับ**

**loop ค้างไม่ใช่อาร์เรย์ตาย (`array_audio._stalled`):** `last_good` คือเวลาที่ chunk ที่มีเสียงถูก*ประมวลผล* — หลัง event loop ค้าง เฟรมของหน้า
อาจถูกตัดสินก่อน chunk ที่ค้างคิวในอีก socket = "ตาย" ปลอม · ตัวเฝ้า loop (beat ทุก 50 ms, ค้าง = beat ช้ากว่า 250 ms) — ระหว่างที่ตัวเฝ้ายัง
ไม่ได้ beat หลังการค้าง และอีก `STALL_GRACE_S` 0.5 วิหลังจากนั้น **ไม่ตัดสินคืนไมค์** (นาฬิกาตายยังเดินต่อ: อาร์เรย์ที่ตายจริงถูกคืนทันทีหลังพ้น grace) ·
`array_report` มี `stall_held` และ `loop_stall_ms`
· หน้าเว็บคืนไมค์เอง (`no_clock`) โดยเซิร์ฟเวอร์ยังไม่รู้: tap ยังโหมดอาร์เรย์ แต่สตรีมตายทันทีที่แอปปล่อย และกฎ "ไม่ live = เฟรมของหน้าขึ้นไป"
ส่งเฟรมของหน้า (ตอนนี้เป็นไมค์จริงแล้ว) ขึ้นไปตรงๆ จนครบ 2 วิแล้วคืนไมค์อย่างเป็นทางการ — ไม่มีเสียงหายเพิ่ม

### 2.5 Echo

อาร์เรย์ไม่มี echo canceller (ไมค์หน้าเว็บได้ AEC ของ WebView) — ใช้กฎของ `array_audio` เดิม: ไม่ใช้อะไรจากอาร์เรย์ระหว่างที่นาฬิกาเล่นเสียงของเซิร์ฟเวอร์
บอกว่าเธออาจยังได้ยิน + `ARRAY_AUDIO_ECHO_TAIL_S` (0.25 วิ) นับใหม่จากตอนหน้าเว็บกลับมาส่ง (half-duplex: หน้าหยุดส่งระหว่างเธอพูด และกลับมาหลัง
playback ของตัวเอง + 350 ms) — เฟรมช่วงนั้นขึ้นไปเป็นศูนย์ และเสียงอาร์เรย์ช่วงนั้นถูกทิ้ง ไม่ส่งย้อนหลัง

**ราคา:** ไม่มี barge-in (พูดแทรกเธอไม่ได้) และ hold-speech เปิดไม่ได้ในโหมดนี้ — **บนหุ่นวันนี้ไม่ได้เสียอะไรเพิ่ม** (`HALF_DUPLEX=true`,
`ECHO_BARGE=false`, hold-speech ปิดเองบนโปรไฟล์ฟังไกล) แต่ถ้าวันหน้าอยากให้พูดแทรกได้บนหุ่น ต้องมี AEC ในแอป (ยังไม่มี) ·
ความเสี่ยง: เสียงก้องในห้องที่ยาวกว่า tail → เธอได้ยินหางเสียงตัวเอง (วัดตามข้อ 6)

### 2.6 ระดับเสียง

เกณฑ์ทุกตัวจูนจากไมค์หน้าเว็บ: `VAD_MIN_RMS_FAR` 0.02, `FOCUS_NEAR_RMS` 0.06, `NOISE_REPLY_NEAR_RMS` 0.06, `FOCUS_SCREEN_RMS` 0.03,
`HEAD_SEARCH_RMS` 0.06, `SILENCE_RMS` 0.0005, `CALL_MIC_BOOST` (หน้าเว็บเท่านั้น — ไม่มีผลกับอาร์เรย์) · สเกลของ beam ต่างออกไป
(ไม่ผ่าน chain ของ Chrome, mic0/DS เฉลี่ย) **ห้ามเดาตัวแปลง**

- ปุ่มปรับ: **`ARRAY_AUDIO_GAIN` (มีอยู่แล้ว ค่าเริ่ม 1.0, เพดาน 16)** คูณเสียงอาร์เรย์ก่อนถึง VAD — ไม่เพิ่มปุ่มที่สองเพราะเป็นการแปลงเดียวกัน
  ณ จุดเดียวกัน (สองปุ่มของสเกลเดียวเพี้ยนออกจากกัน) ทุกเกณฑ์ข้างบนเห็นค่าหลัง gain
- log: `array_report` ของสายโหมดนี้มี `sole=true`, `array_rms` (ก่อน gain), `sent_rms` (หลัง gain), `gain`, `max_gap_ms` · `mic_report` มี
  `source=array`/`gain` (ไม่ใส่เมื่อสวิตช์ปิด) · `analyze_log.py` หัวข้อ "Array mic" แยกคอลัมน์ array/page ของ `focus_open rms`, `vad_floor rms`,
  `mic_report avg` (p10/p50/p90)
- ค่าจริงตั้งจากการวัด A/B บนหุ่น (ข้อ 6 ขั้น 1a)

### 2.7 ทิศเสียง

- **DOA → focus (กฎข้อ 4)** มีอยู่แล้ว: เมื่อแอปเป็นเจ้าของ เฟรม DOA ไหลตามเดิม (ลูปจับเสียงเดียวกัน) · เพิ่ม **`DOA_FOCUS`** (ค่าเริ่ม true = พฤติกรรม
  DOA_ENABLED เดิม) เพื่อทดสอบเส้นเสียงก่อนโดยยังไม่ให้ทิศตัดสินอะไร (ข้อ 6 ขั้น 1 ตั้ง false)
- **กรอบอ้างอิง:** อาร์เรย์อยู่บนตัว ไม่หมุนตามหัว กล้องอยู่บนหัวและหมุน → `doa.py` เรียนคู่ calibration ใน**กรอบของหุ่น** (ทิศหน้าในกล้อง + มุมหัว,
  `head_follow.offset_deg`) และ**ไม่เรียน**ภายใน 1.5 วิหลังหัวถูกสั่งขยับ (`head_follow.moved_at`) · ทิศที่ส่งให้ focus = ทิศหุ่น − มุมหัว
  (focus เทียบกับหน้าในกล้อง) · HEAD_FOLLOW ปิด = มุม 0 ทุกตัวเลขเหมือนเดิม · calibration เก่า (หัวตรงตลอด) ใช้ต่อได้
- **`HEAD_FOLLOW_DOA`** (ค่าเริ่ม false): การค้นหา "เสียงดังที่หุ่นแต่ไม่เห็นหน้า" ของ head_follow หันไปทางทิศ DOA ก่อน (ทั้งมุม ครั้งเดียว + ก้ม)
  ก่อนไลดาร์และก่อนก้มเฉยๆ — ทิศ DOA ที่ focus ได้คือ error ในกรอบกล้องอยู่แล้ว · ไม่ใช้ DOA ขับลูปหน้า (ไม่มีหน้าให้วัดซ้ำ)
- **calibration เกิดอย่างไรในโหมดนี้:** `doa.py` เรียนจาก "หน้าเดียวที่ engaged + ปากขยับ" — ระหว่างคุยกล้องหัวเห็นหน้าแค่ ~11% และ ASD บนหุ่น
  ยังได้ 0 หน้า (ขั้น 0 ของเอกสาร multi-person) → **จะไม่เรียนเองระหว่างใช้งานจริง** · ทางที่ใช้ได้: เจ้าหน้าที่หนึ่งคนยืน 1–1.5 ม. ในภาพ พูดต่อเนื่อง
  เดินช้าๆ ซ้าย-ขวาทั่วภาพ (±25°) 2–3 นาที (ต้องแก้ ASD ก่อน — ไม่มีปากขยับ = ไม่มีคู่) · ไฟล์ `data/doa_calibration.json` อยู่ข้ามรีสตาร์ต ·
  ตรวจด้วย `scripts/doa_calibrate.py` (k-fold) · ทางถัดไปที่ไม่ต้องพึ่งปาก: ใช้คนในไลดาร์ (เมื่อเส้นเทียบกล้องผ่าน) เป็นความจริงของคู่ — ยังไม่ทำ
- ข้อจำกัดฟิสิกส์ (doa.py): หน้า/หลังแยกไม่ได้ · อาร์เรย์ ~6 ซม. beam DS แทบไม่แยกคน (+0.1..+0.4 dB) — ของที่ได้จริงคือ "ทิศ" ไม่ใช่ "เสียงสะอาด"

## 3. สวิตช์

| สวิตช์ | ค่าเริ่ม | หน้าที่ |
|---|---|---|
| `ARRAY_MIC` | false | แอปเป็นเจ้าของอาร์เรย์คนเดียว เสียงสายมาจากอาร์เรย์ (ต้องมี `DOA_ENABLED` + `ARRAY_AUDIO`) |
| `ARRAY_MIC_DEAF_S` | 2.0 | ไม่มีเสียงจากอาร์เรย์นานเท่านี้กลางสาย = คืนไมค์ให้หน้าเว็บสำหรับสายนี้ (ครั้งที่ 3 ในรอบแอป = ทั้งรอบ) |
| `ARRAY_MIC_START_WAIT_S` | 1.5 | ตอน `ready` รออาร์เรย์ live นานสุดเท่านี้ |
| `DOA_FOCUS` | true | ทิศ DOA ถึง focus (กฎ 4, ตา, HEAD_FOLLOW_DOA) — true = พฤติกรรมเดิมของ DOA_ENABLED |
| `HEAD_FOLLOW_DOA` | false | การค้นหาเสียงไม่มีหน้าของหัวจริงหันตามทิศ DOA |
| `ARRAY_AUDIO_GAIN` | 1.0 | (มีอยู่แล้ว) สเกลเสียงอาร์เรย์ — ตั้งจาก A/B |
| `ARRAY_AUDIO_ECHO_TAIL_S` | 0.25 | (มีอยู่แล้ว) หางเสียงหลังเธอพูด |
| หน้าเว็บ `ARRAY_RELEASE_WAIT_MS` / `ARRAY_CLOCK_CHECK_MS` | 3000 / 2000 | รอแอปปล่อย / นาฬิกาเงียบต้องเดินภายใน |
| แอป `MicOwner.DEAF_HOLDS_PER_RUN` / `ArrayListener.FREE_WAIT_MS` | 3 / 3000 | deaf hold ครั้งที่ 3 ในรอบแอป = ทั้งรอบ / หลัง free รอ recording ของ WebView จบ |
| เซิร์ฟเวอร์ `LOOP_BEAT_S` / `STALL_S` / `STALL_GRACE_S` | 0.05 / 0.25 / 0.5 | ตัวเฝ้า event loop ของการตัดสินหูดับ |

## 4. ไฟล์ที่แก้

- `app/config.py` — 5 สวิตช์ข้างบน (ใหม่)
- `app/array_audio.py` — `MARKER`, `sole_enabled`, `ready_fields(agent)` (+`array_mic`), `_Stream.last_good`, `sole_state`/`wait_sole`/
  `sole_offer`/`request_release`, `Tap(sole=True)` → `_decide_sole`/`_hand_back`, แถว report `sole`/`handed_back`, docstring ส่วน ARRAY_MIC
- `app/doa.py` — สถานะ `released`, `ready_frame(agent)`, ลิงก์ APK เก่าภายใต้ ARRAY_MIC = `doa_off`, APK ใหม่ไม่ใช่โหมดเจ้าของเดียว = `doa_off`,
  `link.sole/released/send_text`, `_head_pan` (เรียนในกรอบหุ่น, ส่ง focus ในกรอบกล้อง), `DOA_FOCUS`
- `app/session.py` — `_array_mic_offer` (ก่อนส่ง `ready`), `_open_array_and_enhancer(mic_offer=)`, `_array_frame(verified)`, `_array_handback`,
  `mic_report` มี `source`/`gain` + คำวินิจฉัยที่ถูกสำหรับโหมดนี้
- `app/head_follow.py` — `_HEAD["moved_at"]`/`moved_at()`, `_doa_look`, การค้นหาใช้ DOA เมื่อ `HEAD_FOLLOW_DOA`
- `client/voice-preview.html` — worklet `cap-silent`, `ARRAY_MIC_APP`, `pageMicConstraints` (ชุด constraint เดียว), `reportArrayMic`,
  `releaseArrayMic`, `startArrayMic` (+นาฬิกาต้องเดิน), `arrayMicLost`, case `array_mic_lost`, `ready` จำ `array_mic` · บล็อกสะพาน: `ARRAY_MIC`,
  `emma-face:mic`/`emma-face:mic:release` ใน FIXED_MESSAGES, `micAnswer` (whitelist), `EmmaRobotBridge.arrayMic`
- `apps/emma-robot-android/.../EmmaFace.java` — UA `EmmaArrayMic/1`, Bridge `micState`/`micRelease`, คำตอบสองข้อความ
- `.../MainActivity.java` — implement สองเมธอด
- `.../ArrayListener.java` — UA ลิงก์ `EmmaArrayMic/1`, `soleMode` จาก `array_mic`, `handedBack` + `ownerLock`, `handBack`/`captureRunning`/
  `micState`/`micRelease`, `array_release` จากเซิร์ฟเวอร์, สถานะ `released` บนหน้าคุม
- `.../DoaMath.java` — `codeOrEmpty` (เหตุผลจากเซิร์ฟเวอร์ผ่านการตรวจรูปก่อนถึง log/หน้าคุม)
- `.../MicOwner.java` (ใหม่, รีวิว lead) — กติกา hold ของสาย / release ทั้งรอบ / เพดาน deaf 3 / free · `ArrayListener` ใช้แทน latch เดิม
  (`micTake`/`micHold`/`micFree`, สถานะ `held`) · `EmmaFace`/`MainActivity` สามข้อความใหม่ `emma-face:mic:hold`, `:hold:deaf`, `:free`
- `scripts/analyze_log.py` — หัวข้อ "Array mic"
- เทสต์: `tests/test_array_mic.py` (ใหม่), `tests/conftest.py` (pin), `tests/test_doa.py` (UA ใหม่), `tests/test_face_body_nav_page.py` (สองข้อความใหม่)
- เอกสาร: ไฟล์นี้, `docs/emma-multi-person-2026-11-02.md` §3/ขั้น 5

## 5. ความเสี่ยง (เรียงจากมากไปน้อย)

1. **ทุกเทิร์นของทุกสายเปลี่ยนเส้นทางเสียง** — ความเสี่ยงถดถอยสูงสุด · ทุกการเปลี่ยนอยู่หลังสวิตช์และพิสูจน์ด้วยเทสต์ว่าปิด = เดิมทุกไบต์ ·
   ย้อนกลับขั้นเดียว (ข้อ 6)
2. **ระดับเสียงคนละสเกล** — จนกว่าจะ A/B: พื้น 0.02/โฟกัส 0.06/ตัดเสียงรบกวน 0.06 อาจตัดคนพูดเบา หรือปล่อยเสียงห้อง · ห้ามใช้งานกับลูกค้าก่อนขั้น 1a
3. **นาฬิกาเงียบบน WebView 124** — วัดบน Chromium PC เท่านั้น · ตัวกันคือ `no_clock` (คืนไมค์ใน 2 วิ) แต่ถ้าเกิดจริง ฟีเจอร์ใช้ไม่ได้ทั้งหมด
4. **Echo ไม่มี AEC** — หางเสียงก้องยาวกว่า 0.25 วิ (หลัง 350 ms ของหน้า) = เธอได้ยินตัวเอง · วัดขั้น 1b
5. **HAL ปล่อยอาร์เรย์ช้า/ไม่ปล่อย** หลังแอปหยุด — getUserMedia ของหน้าอาจได้ศูนย์หรือ 2 ช่อง (ผลเดียวกับ 10 ต.ค.) · ดู `mic_report zero_frames`
   หลังคืนไมค์ (ขั้น 1d)
6. **สายที่เริ่มโดยไม่มีอาร์เรย์ใช้ไมค์หน้าจนสายจบ** — standing line ที่ยังไม่มีใครใช้ถูกถือเปิดค้างได้นาน (หลังบูต/รีสตาร์ต สายแรกอาจอยู่ทั้งชั่วโมง
   บนไมค์หน้า) · ยังไม่มีการสลับหน้า→อาร์เรย์กลางสาย (ขั้นถัดไปถ้าวัดแล้วเจอบ่อย: `array_mic_handback phase=start scope=call` ต่อวัน)
7. **free → แอปรอ recording ของ WebView จบ ≤ 3 วิ** — ถ้า WebView ปล่อยช้ากว่านั้น แอปเปิดทับ = ได้ 2/8 ช่อง หรือ latch "conflict" จน resume
   (ดูบรรทัด `recording configs` ใน logcat EmmaArray และสถานะ conflict บนหน้าคุม) · สายถัดไปหลัง free เริ่มใน ~3 วิ: ถ้าแอปเปิดอาร์เรย์ไม่ทัน
   1.5 วิของ `ready` = สายนั้นใช้ไมค์หน้า (scope call) แล้วสายต่อไปค่อยได้
8. **กฎ 4 เมื่อ calibration พร้อม** — ทิศเพี้ยน = ปฏิเสธคนจริง "elsewhere" (fail-open เมื่อไม่รู้ แต่ไม่ใช่เมื่อรู้ผิด) · `DOA_FOCUS=false` จนกว่าวัด
9. **ช่วงคืนไมค์หูดับ** ≤ 2 วิ + ≤ 3 วิ + เวลา getUserMedia — คำที่พูดช่วงนี้หาย
10. **CPU** — beam DS ~1.5% ของคอร์ใหญ่ RK3588 (ประเมิน) + DOA + ส่ง 62 chunk/วิ + tap ฝั่งเซิร์ฟเวอร์ · ดู `asd:`/load ตอนทดสอบ
11. **ความเป็นส่วนตัว** — beam ไหลจากแอปเข้าเซิร์ฟเวอร์ในเครื่องตลอดที่หน้า Emma อยู่ด้านหน้า (รวมตอน mute) เก็บ ≤ 1 วิ ในหน่วยความจำแล้วทิ้ง
12. **การทดสอบ concurrency แบบเดิม** (`do restart` ของ adb, "ทั้งสองลำดับ") ใช้กับ APK ใหม่ไม่ได้แล้วโดยตั้งใจ — APK ใหม่จับอาร์เรย์เฉพาะในโหมดเจ้าของเดียว ·
    การทดสอบช่อง 4-7 (`ARRAY_CAPTURE8`) ต้องเปิด `ARRAY_MIC` ด้วย

## 6. แผนทดสอบบนหุ่น — หลังทดสอบหันหัว+กล้องเสร็จ

**ก่อนเริ่ม:** เจ้าของลง APK `.tmp/apk/emma-robot-arraymic-callhold-20261102.apk` · `robot_server.py deploy` (โค้ดเซิร์ฟเวอร์ชุดนี้) · ไม่มีลูกค้าในห้อง

**ย้อนกลับขั้นเดียว (ทุกขั้น):** ลบบรรทัดที่เพิ่มใน robot.env (หรือแค่ `ARRAY_MIC=false` หรือ `DOA_ENABLED=false` — อย่างใดอย่างหนึ่งพอ เพราะ APK ใหม่
ได้ `doa_off` ทันทีที่ ARRAY_MIC ไม่ทำงาน) → `robot_server.py secrets` → `restart` → เปิดหน้าคุมแล้วกลับหน้า Emma (แอปต่อลิงก์ใหม่ทันที แทนรอ 5 นาที)

**ขั้น 1 — เส้นเสียงอย่างเดียว:** เพิ่มใน robot.env `DOA_ENABLED=true` `ARRAY_AUDIO=true` `ARRAY_MIC=true` `DOA_FOCUS=false` → `secrets` → `restart` →
เปิดหน้าคุมแล้วกลับหน้า Emma · ตรวจ: หน้าคุมขึ้น "เสียงคุยจากไมค์ 8 ช่อง (ds, ถือไมค์คนเดียว)" · server log `call audio source: array (sole)` ·
`analyze_log.py --days 1` หัวข้อ Array mic "calls on the array alone ≥ 1"

- **1a ระดับเสียง (A/B):** เปิดคลิปพูดเดียวกันจากลำโพงมือถือ ระยะ 1 ม. หน้าหุ่น ระดับเดิม 3 รอบ + 3 ม. 3 รอบ · รอบ A = ARRAY_MIC เปิด ·
  รอบ B = ย้อนกลับ (ไมค์หน้าเว็บ วันนี้) · `analyze_log.py` แถว array/page: p50 ของ `focus_open rms` และ `mic_report avg` →
  `ARRAY_AUDIO_GAIN` = p50(page)/p50(array) ถ้าต่างเกิน ~1.5 dB · ทำรอบ A ซ้ำ ต้องอยู่ใน ±1 dB และเสียง 3 ม. ยังต่ำกว่า `FOCUS_NEAR_RMS`
- **1b echo:** ถามคำถามที่คำตอบยาว ไม่มีใครพูด 10 รอบ → ไม่มีเทิร์นเปิดภายใน 1 วิหลังเสียงเธอจบ (`vad_floor`/`focus_open` เวลาเทียบ `turn_complete`)
  และไม่มี `heard` ที่เป็นคำของเธอเอง · เจอ = เพิ่ม `ARRAY_AUDIO_ECHO_TAIL_S` ทีละ 0.1
- **1c ด่านเดิม:** ปุ่ม mute (ไม่มี `heard`), กล้องทัก (หูเปิดหลังคำทัก — `ears_open`), standing line วนใหม่หลัง idle (สายใหม่ `array (sole)` อีก)
- **1d คืนไมค์ (ของสาย):** กลางสาย `adb shell am broadcast -a com.emma.robot.control.DOA --es do off` → ภายใน ~2 วิ log
  `array mic: the array delivered nothing`, แถว `array_mic_handback phase=call scope=call`, `mic_state array_lost held` แล้ว `page` · คุยต่อได้ด้วย
  ไมค์หน้าเว็บ (`mic_report` ไม่ใช่ศูนย์ล้วน) · หน้าคุมขึ้น "พักให้หน้า Emma ใช้ไมค์…" · `--es do on` → สายนี้ยังไมค์หน้า · ปล่อยให้สายจบ (idle)
  → สายถัดไป `array (sole)` กลับมาเอง (ดูบรรทัด EmmaArray ว่าเปิดหลัง recording ของ WebView จบ ไม่มี conflict)
- **1d' เพดาน:** ทำ 1d ซ้ำจนครบ 3 ครั้งในรอบแอปเดียว → ครั้งที่ 3 สถานะแอป `released (deaf_x3)` และสายต่อๆ ไป `scope=run why=released` ·
  ปิดแอปแล้วเปิดใหม่ → `array (sole)` กลับมา
- **1d'' รีสตาร์ตเซิร์ฟเวอร์กลางวัน (รีวิว lead):** `robot_server.py restart` ระหว่างที่ไม่มีลูกค้า → สาย standing line แรกหลังรีสตาร์ตน่าจะได้
  `array_mic_handback phase=start scope=call why=no_link` (ลิงก์แอปยัง back-off) — **ต้องไม่มี `scope=run` และไม่มี `array_release`** · เมื่อสายนั้นจบ
  (ทักแล้วปล่อย idle) สายถัดไปต้องเป็น `array (sole)` · ทำซ้ำด้วยการรีบูตหุ่นหนึ่งครั้ง (หลังบูตแอป Emma ไม่เปิดเอง — เปิดเอง)
- **1e แอปเพิ่ง resume:** เปิดหน้าคุมแล้วกลับ แล้วให้กล้องทักทันที → `ready` รอได้ถึง 1.5 วิ · นับ `array_mic_handback phase=start` ต่อวัน
- **1f หนึ่งวันใช้งาน:** `analyze_log.py` — hand-back ต่อวัน, `max_gap_ms` p95 (ต้องต่ำกว่า `ARRAY_MIC_DEAF_S` มาก), `focus_deny` เทียบวันก่อน

**ขั้น 2 — ทิศเสียง:** `DOA_FOCUS=true` · calibration ตามข้อ 2.7 (หลังแก้ ASD) จนได้แถว `doa_calibration change=ready` · `scripts/doa_calibrate.py` ·
สองคนคุยข้างหุ่น → `focus_deny why=elsewhere` เพิ่ม และคนหน้าหุ่นยังได้ยินครบ · ทิศเพี้ยน = ลบ `data/doa_calibration.json` (กลับเป็น "ไม่รู้" = fail-open)

**ขั้น 3 — หันหัวตามเสียง:** (HEAD_FOLLOW ผ่านแล้ว + calibration พร้อม) `HEAD_FOLLOW_DOA=true` · คนพูดดังจากข้างหุ่นนอกภาพ →
`head_look why=doa` ไปทางเสียง แล้วลูปหน้ารับต่อ · ผิดข้าง = ปิดสวิตช์ (ทิศ DOA ไม่เคยถูกเช็คกับหัวจริง)

## 7. เทสต์

`tests/test_array_mic.py` (60; รอบแรก 41 + รีวิว lead 19): ปิด = doa_ready/`ready`/tap เดิมทุกไบต์ · ต้องครบทุกสวิตช์และ marker ทั้งสองฝั่ง · marker สี่ที่เป็นสตริงเดียวกัน ·
ลิงก์จริง (`/ws/robot-doa`): APK เก่าได้ doa_off, ARRAY_MIC ปิด APK ใหม่ได้ doa_off, สถานะ released · tap จำลองเวลา: ทุกเฟรมเงียบเป็นเสียงอาร์เรย์,
echo hold, tail นับจากหน้ากลับมา, ตายแล้วเป็นรู → คืนไมค์ครั้งเดียว → ไม่กลับอาร์เรย์, สองรูสั้นไม่ใช่รูยาว, ศูนย์ล้วน = หูดับ, ตายตอนเธอพูดนับตรงเวลา,
หน้าไม่ยืนยัน · session: ready true/รอแล้ว false/ไม่รอ released, ปั๊มจริง, หน้าไม่ยืนยันสั่งแอปไม่ได้, mic_report · DOA กรอบหุ่น/กล้อง, `DOA_FOCUS` ·
head_follow `_doa_look` · หน้าเว็บ (node + ของปลอม): ไม่มีไมค์บนอาร์เรย์, เปิดไมค์หลังแอปจบเท่านั้น, หมดเวลา, คำตอบไม่ชัดไม่นับ, APK เก่า/ไม่มีคีย์ = วันนี้,
คืนไมค์กลางสาย, นาฬิกาไม่เดิน, whitelist คำตอบ · **Chromium จริง (playwright)**: นาฬิกาเงียบเดิน ≥ 40 เฟรมศูนย์/1.5 วิ ไม่มี getUserMedia,
คืนไมค์เปิดไมค์หลังแอปบอกจบ, APK เก่าได้ไมค์วันนี้ · แอป (ข้อความซอร์ส + JVM): latch ใต้ล็อก, finally, resume ไม่ล้าง, `codeOrEmpty` ·
**mutation 23 จุด (เจ้าของเดียว ×6, APK เก่า ×2, หน้า JS เก่า ×2, คืนไมค์ ×4, echo ×2, ready รอ, สวิตช์ปิด, กรอบ DOA ×2, HEAD_FOLLOW_DOA, DOA_FOCUS,
เหตุผลจากเซิร์ฟเวอร์) เทสต์แดงทุกจุด** · **รีวิว lead: อีก 19 จุด แดง 18** (transient ไม่ล็อก, final ล็อก, old_page เป็น final, กลางสายไม่ส่งถึงแอป,
หน้า: แปล scope / call=hold run=release / กลางสาย=deaf / ทำเครื่องหมายก่อนถาม / free หลัง track (ย้ายไปก่อน = แดง) / teardown ปล่อย /
รีเซ็ตตอนโหลด, แอป: เพดาน 3 / hold ตอนเริ่มไม่นับ / free รอ WebView / แปลชนิด hold, ตัวเฝ้า loop ×3) — รอด 1 = `MicOwner.free` ตัด `&& !released`
ซึ่งเป็น mutant ที่เทียบเท่า (release() ล้าง held อยู่แล้ว เงื่อนไขนี้คือกันซ้ำ) · เทสต์ใหม่: เหตุผล transient 5 ตัวไม่ส่ง array_release / final 6 ตัวส่ง,
`MicOwner` บน JVM, `releaseCallHardware` จริงใน node (track ก่อน free), ทุกทางออกของสายผ่าน teardown, การตัดสินรอหลัง loop ค้าง (จำลอง +
ตัวเฝ้าจริงกับการค้าง 0.4 วิจริง), Chromium: free เป็นคำถามแรกของหน้า และ free เมื่อสายจบ
