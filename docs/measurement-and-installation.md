# วัดผลและติดตั้ง condo-voice ซ้ำ

ชุดพัฒนา 8 กันยายน 2026: metrics, eval และ dependency lock

## ติดตั้งตาม lock

Lock นี้รองรับ **Windows AMD64 / Python 3.14** ตามเครื่องที่ตรวจจริง โดย `pyproject.toml` ระบุ direct dependencies และ `uv.lock` ระบุ transitive dependencies/แหล่งที่มา/hash ส่วน `requirements.txt` เป็นไฟล์ export รวมทุก extras และ dev สำหรับคำสั่ง pip เดิม ห้ามแก้ export ด้วยมือ

บนเครื่องใหม่ที่มี Python 3.14 และ uv 0.12.1:

```powershell
uv sync --locked --all-extras
uv pip check
uv run --locked --all-extras python scripts/smoke_install.py --all-extras
uv run --locked --all-extras python -m pytest -q
node tests/client_metrics.cjs
```

เลือกติดตั้งเฉพาะส่วนที่ใช้ได้ เช่น `uv sync --locked --extra wake --extra local-search --extra browser` ส่วนที่เลือกมี base, local-search, face, wake, browser, hardware, documents และ websearch; dev เป็น dependency group ค่าเริ่มต้น ถ้าติดตั้งทุก extras แล้วเรียก `uv run` ครั้งต่อไปให้ระบุ extras ชุดเดียวกันด้วย

เมื่อมี `.venv` ที่ใช้งานอยู่ ให้ตรวจรุ่นใหม่ใน environment แยกก่อน:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD\.venv-smoke"
uv sync --locked --all-extras
uv pip check --python .venv-smoke/Scripts/python.exe
.\.venv-smoke\Scripts\python.exe -u -X utf8 scripts/smoke_install.py --all-extras
.\.venv-smoke\Scripts\python.exe -X utf8 -m pytest -q
Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

คำสั่งนี้ติดตั้งใน `.venv-smoke` ไม่แก้ `.env`, weights, ฐานข้อมูล หรือรีสตาร์ตแอป กลุ่ม wake ระบุ `sherpa-onnx-core` โดยตรง เพราะพบว่า resolver ตก dependency ที่ Windows wheel ต้องใช้จน VAD ล้ม การ import Python อย่างเดียวตรวจเรื่องนี้ไม่ครบ จึงมีทั้ง dependency check/DLL check และ real VAD test เมื่อมี weights

การเพิ่ม/อัปเดต dependency: แก้ `pyproject.toml`, รัน `uv lock`, export ด้วย `uv export --locked --all-extras --format requirements-txt --output-file requirements.txt --quiet`, แล้วตรวจใน environment แยกพร้อมบันทึกผลใน `CHANGELOG.md` การ rollback ใช้โค้ดและ lock รุ่นเดียวกันที่ผ่านการตรวจแล้ว สร้าง environment ใหม่และสลับเมื่อหยุดบริการตามขั้นตอนของเครื่องนั้น ไม่ใช้ `uv sync` ทับ environment ที่กำลังรับลูกค้า

ไฟล์โมเดลและ Chromium เป็น assets แยกจาก Python lock ต้องใช้สคริปต์ fetch/playwright setup เดิมบนเครื่องใหม่ CI ทดสอบ package imports และ fake providers; tests ที่ต้องมี weights จะแจ้ง skip ถ้าไม่มี ไม่ถือว่าได้ทดสอบไมค์/กล้อง/หุ่นยนต์แล้ว สามารถทดสอบ encoder ที่มีอยู่จริงโดยไม่ดาวน์โหลดได้:

```powershell
.\.venv-smoke\Scripts\python.exe -u -X utf8 scripts/smoke_install.py --local-model C:\models\encoder
```

## Metrics ที่เพิ่ม

metrics เขียนเป็น events ใน turn log เดิม จึงใช้ `TURN_LOG` และนโยบายเก็บ log เดิม แต่ metrics เองไม่มี transcript, tool arguments, credential หรือข้อมูลเสียง ไม่เพิ่มการบันทึกเสียง การปิด `TURN_LOG` ปิดการเขียน metrics ด้วย

| Event | ความหมาย |
|---|---|
| `metric_turn_start` | เริ่ม reply ที่สังเกตจาก text/tool/audio/usage; มี turn ID เฉพาะ reply |
| `metric_first_audio` | เสียงก้อนแรกมาถึง session; `reply_start_to_audio_ms` เริ่มนับจาก event แรกที่สังเกตใน reply |
| `end_signal_to_audio_ms` ใน first audio | เวลาหลังได้รับ speech-stop ของ OpenAI หรือหลัง local Gemini VAD ตัดสินจบเสียง ไม่ใช่เวลาหลังพยางค์สุดท้ายจริง |
| `metric_tool` | เวลารอ dispatch รวม timeout/การส่งผลไปจอ พร้อม outcome และชื่อ tool ที่รู้จัก; timeout ไม่ใช่การหยุด physical job |
| `metric_turn_end` | reply จบ/ถูกพูดแทรก/สายปิด; ระบุว่าเคยได้รับ audio หรือไม่ |
| `metric_client` | browser แจ้งเวลาถึงจุด schedule เสียง/คิวที่รอ หรือเวลาประมวลผลล้างคิวเมื่อได้รับ interruption |
| `metric_usage` | token counts ที่ provider ส่ง ไม่ใช่ราคาเงินจริง |

เวลาฝั่ง server คำนวณด้วย monotonic clock ฝั่ง browser คำนวณระยะเวลาใน browser แล้วส่งค่า duration มา ไม่เอา timestamp ต่างเครื่องมาลบกัน `playback_schedule_ms` เป็นกำหนดเล่นของ Web Audio ไม่ยืนยันว่าเสียงดังออกลำโพงหรือผู้ใช้ได้ยินแล้ว ส่วน `interruption_clear_ms` ไม่รวมเวลาจับเสียงพูดแทรกและเวลาเครือข่าย

Gemini automatic VAD ไม่ได้ให้จุด speech-stop ผ่าน adapter นี้ จึงรายงาน end signal เป็น unavailable แทนเดาเวลา การทักทาย/ประกาศที่ไม่มี speech-stop ก็เป็น unavailable เช่นกัน ตัวเลข reply-start เริ่มที่ event ที่เราสังเกต จึงอาจเริ่มช้ากว่า provider เริ่มคิด ห้ามเรียกค่านี้ว่า end-to-end latency

OpenAI usage เก็บต่อ response ID และสรุปแบบ dedupe ตาม session/response ส่วน Gemini รายงานเป็น snapshots ที่ไม่มี response ID จึงแสดง snapshot ล่าสุดพร้อมป้ายว่าไม่ใช่ยอดรวม ไม่บวก snapshots ไม่คิดค่าใช้จ่ายจากจำนวน input tokens อย่างเดียว และไม่ถือว่าเป็นยอด billing ที่ครบทุกผลิตภัณฑ์ เช่น transcription แยกต่างหาก

อ่านรายงานโดยไม่แสดงบทสนทนา:

```powershell
python scripts/report_metrics.py data/logs
python scripts/report_metrics.py data/logs/2026-09-08.jsonl
```

ผล JSON แยก provider/model/VAD มีจำนวนตัวอย่าง p50/p95 ของแต่ละ metric และเวลาของแต่ละ tool พร้อมจำนวน first-audio ที่ไม่มี end signal รองรับ log เก่า/บรรทัดท้ายที่เขียนไม่ครบ Client reports จำกัด field/ช่วงตัวเลข/dedupe และรับเฉพาะ turn ID ใน 32 replies ล่าสุดของ session; การวัดจาก client ใช้สำหรับวินิจฉัย ไม่ใช่หลักฐานควบคุมสิทธิ์หรือ billing

## Eval ที่แยกการปรับค่าออกจากการสอบ

ไฟล์คำถามเดิม `data/eval_questions.txt` มี 126 กรณี ส่วน `data/eval/splits-v1.json` แบ่งตามกลุ่มคำตอบและหมวดคำถามที่ต้องปฏิเสธ เป็น train 104 / test 22 โดยกลุ่มเดียวกันไม่ข้าม split ข้อมูลชุดนี้เคยใช้ปรับระบบแล้ว จึงเป็น **historical regression partition** ไม่ใช่ชุดเสียงใหม่ที่ไม่เคยใช้ปรับค่า

```powershell
python scripts/eval_search.py --lexical --split train --json-output data/eval-runs/train.json
python scripts/eval_search.py --lexical --split test --json-output data/eval-runs/test.json
python scripts/eval_search.py --lexical --split all --json-output data/eval-runs/all.json
```

ทุกกรณีมี pass/fail/skipped: คำถามที่ควรค้นเจอแต่ไม่มี hits เป็น fail; commercial refusal ถูกนับใน total; ระบบค้นหาขัดข้องเป็น skipped ไม่ถือว่าปฏิเสธได้ถูกต้อง Accuracy คิดจาก pass/(pass+fail) และแสดง skipped/total ข้างกัน Exit code เป็น 1 เมื่อมี fail หรือ skipped เพื่อไม่ให้ CI ผ่านผลที่วัดไม่ครบ

CLI เริ่มที่ train ตามค่าเริ่มต้น เฉพาะ train จึงพิมพ์คำแนะนำ threshold; test/all ให้คะแนนอย่างเดียว Custom file ต้องใช้ `--split all` หรือ manifest ของตนเอง เมื่อเพิ่มคำถามต้องทบทวน manifest ให้ครอบคลุมก่อนใช้ train/test

JSON report ระบุ dataset version/hash, deck hash, split, requested configuration, effective embedding provider และผลรายกรณี การไม่ใส่ `--lexical` หมายถึงขอทดสอบ semantic หาก backend ใช้ไม่ได้จะ skipped/exit 1 ไม่รายงาน fallback เป็น hybrid ที่สำเร็จ เช่นเดียวกับ reranker ที่เปิดไว้แต่โหลดไม่ได้

การทดสอบ semantic อาจดาวน์โหลดโมเดล/สร้าง embeddings หรือใช้ API ตาม config เดิม ควรตรวจต้นทุนและ cache ก่อนรัน ส่วน query cache จะเขียนเมื่อระบุ `--write-cache` เท่านั้น ผล JSON ใน `data/eval-runs` ถูก gitignore เพราะ custom questions อาจมีข้อมูลจากลูกค้า

## ขั้นตอนเก็บ baseline เสียงจริงถัดไป

ให้ทีมที่ยินยอมบันทึกเสียงจัดชุดคำถามไทย/ภาษาอื่นตามผู้ใช้งานจริง แยกผู้พูดและกลุ่มความหมายของ train/test บันทึก device, ระยะห่าง, เสียงรบกวน, provider/model/VAD และรุ่นข้อมูล ตรวจเลขห้อง/ราคา/ทศนิยมกับแหล่งจริง เก็บเสียงไว้ในพื้นที่ที่กำหนดสิทธิ์ ไม่เพิ่มเข้า Git อัตโนมัติ

เครื่องมือชุดนี้เตรียมระบบวัดและประเมินข้อความแล้ว ยังไม่ได้เก็บเสียงลูกค้าใหม่หรือทดสอบ acoustic latency การเปรียบเทียบ Hybrid VAD, WebRTC และการปรับ retrieval เป็นงานถัดไปตาม [รายงาน research](research/improvement-roadmap-2026-09-08.md)

อ้างอิง semantics: [OpenAI Realtime costs](https://developers.openai.com/api/docs/guides/realtime-costs), [Google Live API reference](https://ai.google.dev/api/live), [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
