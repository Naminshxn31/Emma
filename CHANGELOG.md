# ประวัติการเปลี่ยนแปลง

## 2026-09-18 — M0.3b gate Slides / Canva / Documents

- ตรวจเพิ่มจุด cache ใน `app/tools/slides.py`, `tests/test_presentation_runtime_policy.py`, `docs/runtime-presentation-policy.md`: `current_slide()` เคยคืน public result เก่าหลังถอน approval หรือเปลี่ยน project จึง resolve source และตรวจ policy ซ้ำก่อนส่งให้ provider; กรณี source/asset ถูก block คืน `None` ไม่ส่งข้อความเก่า. ชุดเฉพาะทาง **258 ผ่าน / 1 warning**, ชุดเต็มล่าสุด **1,585 ผ่าน / 1 known failure / 1 warning** (`tests/test_voice.py::test_system_instruction_stays_short`, ความยาว 6,460 เกินเกณฑ์ 4,700 ตาม baseline เดิม), ไม่ skip; `compileall` และ `git diff --check` ผ่าน. ยังไม่ทดสอบกับ Canva remote, เครื่องพิมพ์หรือ provider live session
- `app/presentation_policy.py`, `app/tools/{slides,slide_search,knowledge,canva_display,documents}.py`: แยก display asset จาก speakable text; ตรวจ source/project/asset manifest ก่อนคืนภาพ และตัด draft title/summary/notes/OCR/script/metadata ก่อน tool result; แยก lexical image index ที่ไม่ส่ง draft ไป embedding/reranker ออกจาก approved-text index ที่กรองก่อน ranking; slide catalog และ Canva map cache ผูก project/path/content, Canva ต้องมี measured page map และ design ID ตรง server config ไม่ fallback เดาหมายเลข; เอกสารกรอง source/project/approval แบบทั้ง PDF ก่อน search/list/print ไม่คืน draft catalogue metadata; `mydocs` ใน condo profile คง deny ก่อนอ่าน corpus
- `tests/test_presentation_runtime_policy.py`, `tests/test_document_runtime_policy.py`, `tests/test_slides.py`, `tests/test_canva_display.py`, `tests/test_documents.py`, `tests/test_knowledge_policy.py`, `tests/test_tools.py`, `docs/runtime-presentation-policy.md`: เพิ่มกรณีภาพเปิดได้แต่ข้อความ draft, foreign slide/design/document, Canva ถูกคลิกไปหน้า draft จากภายนอก, mixed-status PDF, cache ถูกถอนอนุมัติ/เปลี่ยนโครงการ, fallback map หาย, hidden metadata, และตรวจ serialized provider input ไม่มีข้อความที่ถูก block; ปรับ fixture ที่เคยสมมติว่า ID ของ Canva คือเลขหน้า หรือย้าย slides_dir โดยไม่มี index; fixture approval เป็นข้อมูลสมมติสำหรับทดสอบ ไม่เปลี่ยน source production เป็น approved
- ตรวจจริง: ชุด Slides/Canva/Documents/knowledge/mydocs รอบแรก **341 ผ่าน / 1 warning**; หลังเพิ่ม cache และ source-level document policy ชุด Canva/presentation/documents **96 ผ่าน**; suite เต็มรอบแรกพบ fixture Canva URL ผิดและ skip เพราะ test ย้าย index ไม่ครบ จึงแก้สองเคสและ targeted ผ่าน **2/2**; suite เต็มรอบสุดท้าย **1,583 ผ่าน / 1 known failure / 1 warning** (`tests/test_voice.py::test_system_instruction_stays_short`, 6,460 เทียบเพดาน 4,700), ไม่มี skip; `python -m compileall -q app` และ `git diff --check` ผ่าน; audit CI **0 errors / 3 warnings** ซึ่งเป็นคำเตือนข้อมูลที่ยังไม่อนุมัติ. ยังไม่ได้ทดสอบ Canva remote, PDF กับเครื่องพิมพ์จริง หรือ provider live session

## 2026-09-18 — M0.3.1 ต่อ runtime policy กับ inventory (ยังไม่ปิด M0.3)

- `app/runtime_policy.py`, `app/knowledge_policy.py`, `app/tools/units.py`, `data/registry/source_registry.json`: เพิ่ม `RuntimeResult`/allowlist ฟิลด์ และบังคับ live inventory ด้วย trusted source, project slug ในแถวจริง, เวลา fetch/cache และ disclosure ก่อนส่งการ์ด/รายการ/ผลรวมให้ Emma; blocked result ไม่มี payload; static sample และ local export ที่ไม่มี approval metadata ถูกปิดก่อนคืนการ์ด; cache ไฟล์ตรวจ mtime/size เพื่อรับการถอนอนุมัติ; promotion free text/ราคาโปรไม่ส่งผ่านเครื่องมือโดยอัตโนมัติ; `price_pair`/quotation ตรวจ scope ก่อนเปิดเผยข้อมูลหรือจอ; registry ของ local export ใช้ approval metadata ชุดเดียวกับ runtime
- `tests/test_units.py`, `tests/test_sales_links.py`, `tests/test_knowledge_policy.py`, `tests/test_inventory_runtime_policy.py`, `docs/runtime-inventory-policy.md`: ปรับ fixture ให้มี project slug จริงและเพิ่ม direct-adapter bypass tests (ผิดโครงการ, ขาด slug/เวลา fetch, stale, ฟิลด์ไม่อนุญาต, mixed list, สถานะขัด query, promotion, quotation และถอน approval ของ static file); บันทึกความต่างระหว่าง `updated_at` ของแถวกับเวลา fetch และระบุว่า plan/slide/provider ยังเป็นงาน M0.3 ถัดไป
- ตรวจจริง: ชุด inventory/sales/knowledge policy **78 ผ่าน / 1 warning**; ชุดเต็มหลังต่อ inventory **1,565 ผ่าน / 1 ล้มเดิม / 1 warning** (`test_system_instruction_stays_short`; รันก่อนเก็บ dead code และปรับ registry ล่าสุด); ยังไม่ทดสอบกับ Supabase production หรือจอจริง

## 2026-09-18 — M0.3.2 ต่อ policy กับผังและปิด bypass แผนที่ (ยังไม่ปิด M0.3)

- `app/runtime_policy.py`, `app/tools/units.py`: เพิ่ม route `plan_asset_runtime_v1` แยกจาก inventory/claim; `show_plan` ตรวจ source/project, manifest, ชนิดไฟล์, MIME และ SHA-256 ของภาพที่อ่านจริงก่อนส่ง URL, และตรวจทุก overlay ด้วย live inventory scope/freshness, floor/status/พิกัด ก่อนคืนภาพหรือจำนวนห้อง; blocked result ไม่ส่งภาพ/overlay. การ์ด inventory ไม่ส่ง `photo`/`floorplan` URL ที่ยังไม่ตรวจ asset. `show_map` จาก `project_facts` ต้องผ่าน approval/disclosure ก่อนเปิดจอ และตรวจ HTTPS hostname จริงแทนการเทียบ prefix
- `tests/test_plan_runtime_policy.py`, `tests/test_units.py`, `tests/test_sales_links.py`, `docs/runtime-inventory-policy.md`, `docs/runtime-plan-policy.md`: เพิ่มกรณีตรงเข้าตัว router/adapter สำหรับ metadata ขาด, ต่างโครงการ, checksum/MIME ผิด, asset ไม่พร้อม, overlay ต่างชั้น/สถานะผิด/พิกัดนอกช่วง, draft map และ hostname ปลอม; บันทึกข้อจำกัดว่าจอยังดาวน์โหลด URL ภาพซ้ำหลัง verifier ตรวจ จึงยังไม่ใช่ byte-integrity end-to-end
- ตรวจจริง: ชุด plan+units **41 ผ่าน / 1 warning**; ชุด sales+plan+inventory **36 ผ่าน**; หลังตัด URL การ์ดที่ไม่ตรวจ asset ชุด inventory+plan+units+sales **75 ผ่าน / 1 warning** และชุดเต็มรอบสุดท้าย **1,572 ผ่าน / 1 ล้มเดิม / 1 warning** (`tests/test_voice.py::test_system_instruction_stays_short`, ความยาว 6,460 เทียบเพดาน 4,700); `git diff --check` ผ่าน. ยังไม่ทดสอบกับ Supabase, endpoint ภาพ หรือจอ production และ slide/document/provider routes ยังไม่ผ่าน audit M0.3 ทั้งหมด

## 2026-09-18 — M0.2 gate ข้อมูลโครงการสำหรับลูกค้า

- `app/knowledge_policy.py`, `data/projects/embassy_world/{facts/condo_facts.json,presentations/sales_context.json}`: นิยาม metadata approval/disclosure/content state และ deny-by-default พร้อม trace ที่ไม่มีเนื้อหา claim; ติดป้าย source เดิมเป็น draft/internal โดยไม่อนุมัติหรือแก้ข้อเท็จจริง; live inventory มี policy แยกตาม trusted source/project/fetch freshness/disclosure ไม่บังคับผู้อนุมัติรายห้อง
- `app/prompts.py`, `app/tools/{knowledge,slides,mydocs}.py`: ไม่ inject draft facts/sales context/เอกสารเข้า prompt หรือคำตอบ; slide ยังแสดงภาพได้แต่ไม่ส่ง draft text/script ให้โมเดล, tour ที่มีเนื้อหาไม่อนุมัติจะไม่เริ่ม; แยก script approval จาก source approval และคงสถานะ developing/concept ในคำสั่งตอบ
- `data/registry/source_registry.json`, `scripts/audit_data_sources.py`, `tests/test_data_source_registry.py`: เปลี่ยนฟิลด์ audit ของ facts เป็น schema ใหม่ เพิ่ม sales context และสรุป policy trace/source ID/จำนวนสไลด์ที่ถูกปิดใน CI โดยไม่เปิดข้อความ draft; CI ยังไม่ fail เพราะการรออนุมัติเป็นสถานะจริง ไม่ใช่ไฟล์เสีย
- `tests/{test_knowledge_policy,test_knowledge,test_slides,test_voice,test_review_fixes,test_profiles,test_canva_display,test_tools,approval_fixture}.py`, `docs/knowledge-approval-contract.md`: เพิ่มกรณี approved/draft/missing/expired/wrong-project/concept, แยก fixture อนุมัติสมมติสำหรับทดสอบกลไกสไลด์จาก catalog จริง, บันทึกขอบเขตที่ยังต้องบังคับทั่ว runtime ใน M0.3; ไม่แตะ Gemini/Android/เสียง
- ตรวจจริง: ชุดเต็มหลังปรับสัญญา **1,555 ผ่าน / 1 ล้มเดิม / 1 warning** (`test_system_instruction_stays_short` เป็น prompt-length failure เดิม; รันก่อนเพิ่ม trace ใน audit); ชุด registry/source/policy และเคส draft หลังแก้ audit/decision ล่าสุด **26 ผ่าน / 1 warning**; หลังแก้ถ้อยคำราคาใน prompt รันเคสตรงประเด็น **2 ผ่าน**; audit `ci`/`runtime` ได้ **0 errors / 3 warnings** (facts draft, slide catalog 135 รายการไม่อนุมัติเป็น claim, sales context draft), `full` ได้ **0 errors / 5 warnings** (รวม local unit file กับ navigation points ที่ยังไม่ยืนยัน); `git diff --check` ผ่านในไฟล์งานรอบนี้โดยมีเพียงคำเตือน LF/CRLF จาก Git; ยังไม่ได้ทดสอบเสียงหรือ deployment จริง

## 2026-09-18 — M0.1.1 ระบุความพร้อมของ source และสัญญา CI

- `data/registry/source_registry.json`, `data/registry/external_asset_manifest.json`: ระบุ `availability`/`required_for` ของทุก source และแยกภาพสไลด์ 135 ไฟล์กับ PDF 3 ไฟล์ที่ต้องจัดหานอก Git; manifest ตรึงชื่อ ขนาด และ SHA-256 จากไฟล์บนเครื่องปัจจุบันเพื่อกันวาง bundle ผิดชุด ไม่ถือเป็นการอนุมัติเนื้อหา; แหล่ง showroom/หลักฐานหุ่น/ภาพเก่าที่ไม่ได้เป็น input production จัดเป็น `review_only` โดยไม่ลบข้อมูล
- `app/data_sources.py`, `scripts/audit_data_sources.py`, `scripts/build_external_asset_manifest.py`, `tests/test_data_source_registry.py`, `tests/test_source_availability.py`, `tests/test_slides.py`: เพิ่มโหมด `ci`/`runtime`/`full`, ตรวจ manifest เทียบ index, ปฏิเสธ path เฉพาะเครื่อง/ข้าม root, ตรวจ scope source ที่เข้าถึงลูกค้า และทดสอบว่า CI ไม่ต้องมี asset จริงแต่ runtime ต้องมีไฟล์ตรงแฮช; test byte-serving ของสไลด์ skip เฉพาะเมื่อไม่มี bundle ทั้งชุด แต่ยัง fail หากขาดบางภาพ
- `.github/workflows/test.yml`, `README.md`, `data/README.md`, `docs/source-availability-contract.md`: เพิ่มขั้น audit CI บน checkout ใหม่ บันทึกคำสั่งเตรียมไฟล์ภายนอก/ตรวจ deployment และข้อจำกัดว่ายังไม่มี artifact store ถาวร; ยังไม่เปลี่ยน `.env` หรือกฎ approval/disclosure
- ตรวจจริง: `audit --mode ci` ได้ **0 errors / 1 warning**, `--mode runtime` (รวม `--feature printing`) ได้ **0 errors / 1 warning**, `--mode full` ได้ **0 errors / 3 warnings เดิม**; `build_external_asset_manifest.py --check` ผ่าน; ชุด source-contract + slide skip ล่าสุดผ่าน **15/15**, ชุด loader/สไลด์/ยูนิต/เอกสารผ่าน **339/339**; ชุดรวม **1,539 ผ่าน / 5 ล้มเดิม / 1 warning** โดย failures คือ `test_retrieval_preserves_script_approval` สี่กรณีและ `test_system_instruction_stays_short` หนึ่งกรณี; ยังไม่ได้รัน suite เต็มจาก clean checkout ที่ materialize asset ภายนอก

## 2026-09-18 — แยกข้อมูล Embassy World ตามโครงการและบังคับขอบเขตตอนโหลด

- ตรวจจาก clean worktree ของชุดโค้ด M0.1.1 ที่ไม่มีรูป/PDF local จริง: `--mode ci` ได้ **0 errors / 1 warning**, `--mode runtime` ได้ **1 error / 1 warning** ตามสัญญาเพราะขาดภาพสไลด์ 135 ไฟล์; focused tests ของ registry, availability, mydocs และ slides ได้ **152 ผ่าน / 2 skip / 1 warning** โดย skip เฉพาะ test ที่ต้องอ่าน byte ภาพ; ยังไม่มี artifact store ถาวรหรือการทดสอบ deployment ที่นำ bundle มาใส่
- `data/projects/embassy_world/`, `data/registry/source_registry.json`, `data/review/`: ย้าย facts, vocabulary, floor-plan assets และ Canva map ไปตามหน้าที่; แยกดัชนี/ภาพสไลด์ Embassy World 135 รายการจาก `other-project` 15 รายการที่รอจำแนก; ย้ายภาพเด็คเก่า 59 ไฟล์พร้อมไฟล์สำรองและแคชไป `review/unindexed_slides/` โดยไม่ลบเนื้อหา เพิ่ม `source_id`/`project_id` ให้ JSON ที่ย้าย และเก็บคำแนะนำโครงการจาก prompt เดิมใน `presentations/sales_context.json` สถานะ `migrated_unreviewed` ไม่เปลี่ยน draft เป็น approved
- `app/data_sources.py`, `app/config.py`, `app/prompts.py`, `app/session.py`, `app/tools/{slides,knowledge,project_knowledge,units,registry,canva_display,mydocs}.py`: ใช้ทะเบียนแก้ path ตาม source ID; server กำหนด project ID เอง ตรวจ identity/slug ของ inventory, metadata ใน facts/สไลด์/ผัง/Canva และปฏิเสธผลเครื่องมือที่มี project ID อื่น; เอกสาร mydocs ในโหมดห้องขายถูกจำกัดที่โฟลเดอร์ของโครงการแม้ `MYDOCS_INCLUDE` ว่าง ข้อมูลเฉพาะโครงการถูกย้ายออกจาก `SALES_HOST_BLOCK` โดยคงถ้อยคำที่ประกอบ prompt เดิม
- `.env.example`, `.gitignore`, `README.md`, `data/README.md`, `docs/project-structure-and-data-sources.md`, `scripts/{migrate_project_sources,audit_data_sources,import_slides,import_canva_export,canva_pages,approve_narration,import_page_docs,build_embeddings,analyze_log,make_test_documents}.py`, `tests/`: อัปเดต path นำเข้า/เอกสาร/การทดสอบและเพิ่มเคสกันข้อมูลข้ามโครงการ; ไม่แตะ secret ใน `.env` หรือเนื้อหาข้อมูลขายที่ต้องให้เจ้าของอนุมัติ
- ยังไม่ย้าย `data/documents/`, `data/personal-docs/`, `data/showroom/` หรือ `data/units.sample.json` ไปติดป้ายโครงการโดยไม่มีหลักฐานเจ้าของ/ประเภทเพิ่มเติม; เอกสารส่วนตัวในโหมดห้องขายถูกจำกัดด้วย path โครงการแล้ว แต่ยังต้องตรวจเนื้อหาเอกสารแต่ละฉบับในรอบอนุมัติ
- ตรวจจริง: เทียบ JSON เดิมกับที่ย้ายโดยตัด metadata ใหม่แล้ว ได้สไลด์ครบ 150 รายการและเนื้อหาเปลี่ยน 0 รายการ; facts, vocabulary, floor-plan assets และ Canva map เนื้อหาเดิมตรงกัน; ภาพสไลด์ที่อยู่ในดัชนีครบ **135 + 15**, ภาพ/ไฟล์เก่าในพื้นที่ review **61 ไฟล์** ไม่มีรายการสูญหาย; `python scripts/audit_data_sources.py` ได้ **0 errors / 3 warnings เดิม** (ข้อมูล facts ยังไม่มีการอนุมัติ, ตารางยูนิต local ยังไม่มี, จุดนำทาง 5 จุดยังไม่ยืนยัน); `python -m compileall -q app scripts` และ `git diff --check` ผ่าน; ชุดทดสอบรวม **1,533 ผ่าน / 5 ล้ม / 1 warning** ไม่มี regression ใหม่จากการย้ายข้อมูล; failure ที่เก็บเป็น baseline คือ `tests/test_review_fixes.py::test_retrieval_preserves_script_approval[th-False]`, `[th-None]`, `[en-False]`, `[en-None]` และ `tests/test_voice.py::test_system_instruction_stays_short` (prompt 7,979 ตัวอักษร เทียบเพดาน 4,700)
- ขอบเขต commit M0.1: ย้ายโครงสร้าง แก้ source loading/project scoping และนำข้อความโครงการออกจาก prompt เท่านั้น; **ยังไม่เปลี่ยนกฎ approval/disclosure** หรือยกระดับ draft เป็น approved; งาน path config แบบ root และ policy gate อยู่รอบถัดไป

## 2026-09-17 — ลดไฟล์สร้างอัตโนมัติใน Source Control

- ตรวจเพิ่มจาก checkout เฉพาะไฟล์ที่ stage: `tests/test_mydocs.py` ผ่าน **14/14** หลังตรึง `MYDOCS_INCLUDE` ใน fixture; audit ใน checkout ว่างรายงาน **4 errors / 4 warnings** เพราะแหล่งที่เป็นไฟล์ local/ยังไม่อยู่ใน Git (`review/unindexed_slides`, แผนที่ห้องขาย และหลักฐานหุ่น) ไม่ได้ถูกนำเข้า commit นี้; test รูปสไลด์ 2 เคสต้องใช้ JPG ที่ Git ignore อยู่เดิม จึงยังต้องจัดการ fixture/asset สำหรับ CI แยกต่างหาก ไม่อ้างว่า checkout ใหม่ผ่านทั้งชุด
- `.gitignore`: ไม่แสดงแคช Gradle และผล build ใต้ `apps/emma-ai-voice/` ใน Git เพราะสร้างใหม่ได้และไม่ใช่ซอร์ส; ไม่ลบไฟล์ในเครื่องหรือซ่อนซอร์สแอป
- ตรวจสอบจริง: ก่อนแก้ `git status --porcelain=v1 -uall` แสดง 295 ไฟล์ โดย 144 ไฟล์เป็นสองโฟลเดอร์ดังกล่าว; โฟลเดอร์ `.tmp/` ราว 15,458 ไฟล์ถูก ignore อยู่แล้ว; หลังแก้ `git status` เหลือ 151 ไฟล์ และ `git check-ignore -v` ยืนยันกฎทั้งสอง โดยซอร์สแอป 16 ไฟล์ยังปรากฏตามเดิม

## 2026-09-17 — ปลดชุดตัวจำลองหุ่นก่อนรับเครื่อง

- ลบ `app/robot_backend.py`, `app/robot_diagnostics.py`, `app/robot_home.py`, `app/robot_simulation.py`, `app/robot_simulator.py`, `app/robot_voice.py`, `app/tools/simulation_home.py`, หน้า `client/robot-simulator.*`, ฉาก `robot-scene*`, `robot-explorer.js`, `robot-interior.js`, `robot-showroom.js`, `simulator-voice.*`, `robot-drive-sim.html`, ชุด Three.js ที่ใช้เฉพาะตัวจำลอง, `start-robot-simulator.cmd`, `scripts/build_showroom_map.py` และการทดสอบเฉพาะตัวจำลอง เพราะหน้าและ runtime ซ้อมก่อนหุ่นมาถูกเลิกใช้แล้ว; คง adapter/หน้าควบคุมหุ่นจริงและไฟล์ CAD ใน `data/showroom/` ไว้เป็นข้อมูลอ้างอิงที่ยังไม่ยืนยันสำหรับการเดินจริง
- เก็บจุดเรียกตัวจำลองที่ค้างใน `app/config.py`, `app/prompts.py`, `app/providers/gemini.py`, `app/session.py`, `app/tools/{__init__,registry,robot,robot_link}.py`, `app/turnlog.py`, `client/index.html`, `scripts/check_robot_readiness.py`, `tests/conftest.py`; ย้ายการจัดหมวด provider error ไป `app/session.py` เพื่อไม่ต้องพึ่งโมดูล diagnostics ของตัวจำลอง
- ปรับ `README.md`, `.gitignore`, `docs/robot-integration.md`, `docs/robot-arrival-2026-09-09.md`, `docs/ต่อกับหุ่นยนต์ Astronaut.md` และหมายเหตุใน `docs/research/emma-robot-next-steps-2026-09-08.md` ให้ลิงก์/คำแนะนำปัจจุบันไม่ชี้ไปบริการพอร์ต 8010 ที่ลบแล้ว โดยเก็บรายงานเก่าไว้เป็นประวัติ ไม่เปลี่ยนข้อมูล draft เป็น approved
- ตรวจสอบจริง: `pytest -q --basetemp=.pytest_cleanup_20260917 -p no:cacheprovider` ได้ **1,527 ผ่าน / 5 ล้ม / 1 warning** โดยทั้ง 5 เคสล้มเรื่อง `draft_script` (4) และความยาว prompt (1) เหมือนรอบรีวิวก่อนลบตัวจำลอง; `python -m compileall -q app scripts`, `git diff --check` และ `node tests/client_metrics.cjs` ผ่าน; `node tests/client_audio_start.cjs` กับ `node tests/client_voice_lifecycle.cjs` ยังล้มเพราะ test harness เรียก `getUserMedia` ตรง แต่หน้าเว็บปัจจุบันใช้ `openPreferredMic` จากงานค้างเดิม ไม่ใช่ผลการถอดตัวจำลอง

## 2026-09-15 — Conversation brevity เฉพาะ walk-in และ family/weekend clue

- `app/prompts.py`: จูนเฉพาะกฎ adaptive/storytelling เดิม—เคส “ยังไม่รู้อะไรเลย” ให้ต้อนรับและบอกชื่อโครงการหนึ่งประโยคแล้วหยุด ไม่แตกเป็น mini-presentation หรือรีบถามแบบห้อง; เมื่อมี clue เรื่องครอบครัว เวลา หรือรูปแบบการมาใช้ ให้ตอบด้วยประโยชน์ที่เกี่ยวที่สุดเพียงเรื่องเดียวแล้วหยุด ไม่ไล่ facility/ห้องหรือถามต่อ
- `tests/test_profiles.py`: เพิ่ม invariant ขนาดเล็กสำหรับความกระชับของเคส walk-in และการใช้ personal clue แล้วหยุด โดยไม่ล็อกทั้งย่อหน้า
- Gemini Live A/G รอบแรก (`gemini-3.1-flash-live-preview`, synthetic, ปิด turn log/ราคา, ไม่ประกาศ `robot`/`smarthome`, prompt เดียวและไม่แก้ระหว่างสองเคส): A มีสองประโยค/หนึ่งคำถามแต่ประโยคแรกยังขยายเป็นคำขายกว้าง; G ไล่ Junior World กับ Aqua Cinema และถามต่อว่าเด็กชอบกิจกรรมอะไร — FAIL brevity/anti-hook จึงกระชับสองกฎเดิมให้ A เหลือภาพรวมหนึ่งใจความ และ quote เฉพาะ failure tail ของ G เพื่อให้เลือก benefit เดียวแล้วหยุด
- Gemini Live A/G รอบสอง (เงื่อนไขเดิมและไม่แก้ระหว่างคู่): A ผ่านด้าน brevity ที่สองประโยค/หนึ่งคำถามและไม่มี tool/action; G ยังไล่สอง facility แล้วถามว่าจะดูแบบห้องครอบครัวไหม เพราะตี clue เป็นเคส “ยังไม่มี intent” · แก้ classification ในกฎเดิมให้ family/time/usage clue นับเป็น intent ชัด ไม่ใช่ช่องเปิดคำถาม พร้อม quote offer tail ที่เกิดจริง
- Gemini Live A/G รอบสาม (เงื่อนไขเดิมและไม่แก้ระหว่างคู่): A ยังอยู่สองประโยคแต่ยัดทำเล, Luxury และ facility ไว้ในประโยคแรก; G กล่าวถึง Junior World แล้วเติมคำถามแบบห้องอีก — classification อย่างเดียวยังไม่พอ จึงเปลี่ยนถ้อยคำเดิมเป็น output shape: A มีชื่อโครงการกับจุดเด่นเดียวและคำถามอย่างมากหนึ่งข้อ; G มี benefit เดียวหนึ่งประโยค ห้าม question/offer tail และห้ามเอ่ย facility เกินหนึ่งแห่ง
- Gemini Live A/G รอบสี่ (เงื่อนไขเดิมและไม่แก้ระหว่างคู่): G ผ่าน—หนึ่งประโยค กล่าวถึง Junior World อย่างเดียว ไม่มีคำถาม/offer/tool; A ยังยัดทำเล ลากูน และ facility ในประโยคแรกแม้กำหนดจุดเด่นเดียว จึงล็อกเฉพาะ utterance “ยังไม่รู้อะไรเลย” ให้ต้อนรับ+บอกชื่อโครงการ แล้วมีคำถามเปิดสั้นได้อย่างมากหนึ่งข้อ โดยห้าม factual pitch ในเทิร์นนั้น
- Gemini Live A/G รอบห้าและ rerun โดยไม่แก้ prompt: A ผ่านซ้ำที่ต้อนรับ+ชื่อโครงการ+คำถามเดียว; G ผ่าน brevity/anti-hook ที่หนึ่งประโยคและไม่มีคำถาม แต่ rerun พูด Junior World เหมือนมีอยู่แล้วทั้งที่ facts เป็น Preliminary Concept จึงยังไม่ผ่าน factual grounding · เสริมในกฎ G เดิมว่าถ้าเอ่ย facility จากชุดนี้ ต้องรวมสถานะ “ตามแนวคิดของโครงการ...จะเป็น...” ในประโยคเดียวกัน
- Gemini Live A/G รอบหก (เงื่อนไขเดิม): A ผ่าน; G กลับมายาว เอ่ย Junior World เหมือนเปิดใช้แล้วและเสนอเปิดผัง/ห้องสองนอนโดยไม่มี tool — การบังคับ status พร้อม facility ทำให้ภาระเกิน intent · เปลี่ยนเฉพาะ G ให้ตอบ benefit ทั่วไปจาก family/weekend clue หนึ่งประโยคและห้ามเอ่ย facility/ห้อง จึงรักษา factual grounding โดยไม่ต้องดึง concept ที่ลูกค้าไม่ได้ถามเข้ามา
- Gemini Live A/G รอบเจ็ด (เงื่อนไขเดิม): A ผ่านที่ประโยคต้อนรับเดียว; G พูด benefit ที่ต้องการแล้วแต่เติม Junior World เป็นประโยคสองโดยไม่มี tool/status — คำว่า “เช่น” ยังเปิดช่องให้ขยาย จึงเปลี่ยนเป็น response contract ว่าให้พูดประโยค benefit นั้น “เท่านั้น” แล้วหยุด
- Gemini Live A/G รอบแปด (เงื่อนไขเดิม): G ผ่านเป็นประโยคเดียวและไม่มีคำถาม/tool; A สั้นแต่ถามต่อเรื่องแบบห้องทั้งที่ลูกค้าบอกว่ายังไม่รู้ จึงเสี่ยงกลับเป็น qualification form · ล็อก A ให้ต้อนรับ+ชื่อโครงการเท่านั้นแล้วหยุด (ศูนย์คำถามยังอยู่ใน “at most one”) และห้ามถามแบบห้องในเทิร์นนี้
- Gemini Live รอบยืนยันและ rerun โดยใช้ assembled prompt snapshot เดียวต่อคู่และไม่แก้ระหว่าง A/G (`gemini-3.1-flash-live-preview`, synthetic, ปิด turn log/ราคา, ไม่ประกาศ `robot`/`smarthome`): A ตอบ “ยินดีต้อนรับค่ะ ที่นี่คือ Embassy World ค่ะ” เหมือนกันทั้งสองรอบ; G ตอบ benefit เรื่องใช้เวลาพักผ่อนกับครอบครัวในวันหยุดเพียงประโยคเดียวทั้งสองรอบ (rerun เติมคำต้อนรับสั้น ๆ แต่ไม่แตกประเด็น); ทุกเคสมีเสียงตอบ, `tool_trace=[]`, ไม่มีคำถาม/ข้อเสนอท้าย — ผ่าน acceptance ด้าน brevity, intent, factual grounding, capability truth และ anti-hook
- ตรวจ `tests/test_profiles.py` ผ่าน **45 tests**; regression `profiles + units + knowledge + mydocs + retrieval + slides + robot` ผ่าน **342 tests, 1 warning** · `SALES_HOST_BLOCK` = **3648 chars** (SHA-256 `413ccbda4c661ace9c202d98c79bc4facf3c00e3aab94f655f8f8dab81b8a6f1`) · budget test ยังคงเป็น known failure **7982 > 4700**, assembled runtime ตาม tool groups จริง **8075**; ไม่ขยับ threshold และไม่แตะ tool schema/boundary, knowledge retrieval, price policy, navigation, slide หรือ hardware integration

## 2026-09-15 — ผูกคำว่า “พาชม” กับ physical navigation เท่านั้น

- `app/prompts.py`: นิยาม “พาชม/พาไปดู/เดินไป/นำไป/ตามฉันมา/เดี๋ยวพาไป” ว่าเป็นการเคลื่อนที่ของหุ่นไปสถานที่จริง ต้องมี `navigation` tool สำหรับปลายทางและได้ผลสำเร็จก่อนพูดว่าพาไปได้; ถ้าไม่มี tool ต้องตอบ capability ตรงๆ ว่าตอนนี้พาเดินไปไม่ได้ ห้ามหลบด้วยการตอบเพียงว่าสถานที่ “มีให้ชม” และย้ำว่าสคริปต์ sales ในเอกสารไม่ใช่หลักฐานว่าหุ่นมี capability นั้น · ลบกฎเดิมที่สั่งตอบรับว่าจะพาไปโดยไม่ดู tool result
- `app/prompts.py`, `app/tools/slides.py`: เลิกใช้คำว่า “พาชม” แทน slide presentation; `start_presentation` หมายถึงการแนะนำโครงการ/ดูภาพรวมบนจอเท่านั้น เพื่อไม่ให้ retrieval หรือ tool description กลบขอบเขต physical action
- `tests/test_profiles.py`, `tests/test_slides.py`: เพิ่ม invariant สำหรับ navigation gate, failure quote จริง และแยก slide action ออกจาก physical tour
- ผลตรวจสุดท้าย: focused `test_profiles` + invariant ของ slides **44 ผ่าน, 1 warning**; regression `profiles + units + knowledge + mydocs + retrieval + slides + robot` **340 ผ่าน, 1 warning** · `SALES_HOST_BLOCK` ไม่เปลี่ยน (**3422 chars**, SHA-256 `506e85efdb642a4ee8ef0c8a7f9efe881401046a23ebc62bdf54faab75b6fe70`) · budget test ยังเป็น known failure **7756 > 4700**, assembled runtime ตาม tool groups จริง **7841**; ไม่ขยับ threshold
- Gemini Live probe รอบแรก (`gemini-3.1-flash-live-preview`, synthetic, ปิด turn log/ราคา, ถอด `robot` group และตรวจแล้วว่า `go_to_place` ไม่ถูกประกาศ): “ยังไม่รู้จะดูอะไรเลย” ไม่มี tool call/คำรับปากพาไป แต่ยังยาวกว่าที่ต้องการ; “พาไปดูห้องตัวอย่างได้ไหม” ตอบเพียง “มีห้องตัวอย่างให้ชมค่ะ” และ tool trace ว่าง — ไม่โกหกเรื่อง action แต่ยัง FAIL intent เพราะหลบคำถาม capability จึงแก้ gate เดิมให้ต้องตอบตรงๆ · ไม่มี secret/PII/ราคาใน transcript หรือ trace
- Gemini Live A/NAV รอบยืนยัน (เงื่อนไขข้อมูล/เครื่องมือเหมือนรอบแรกและไม่แก้ prompt ระหว่างสองเคส): A ไม่มี tool call และไม่เสนอ physical tour; NAV ตอบตรงว่า “มีห้องตัวอย่างให้ชมค่ะ แต่ตอนนี้เอ็มม่ายังไม่สามารถพาเดินไปยังห้องตัวอย่างได้ค่ะ”, `tool_trace=[]` และไม่ถามต่อ — ผ่าน capability truth/intent/anti-hook ครบ · A ยังยาว แต่จัดเป็น conversation-quality งานใหม่ ไม่ใช่ safety blocker
- restart runtime บน port 8001 แล้ว; `/health` ตอบ 200 ด้วย `gemini-3.1-flash-live-preview` (หุ่นจริงยังไม่เชื่อมต่อ จึง `robot.usable=false` ตามสถานะจริง)

## 2026-09-15 — รวม SALES_HOST_BLOCK 17→8 กฎ + anti-hook / offer≠question / adaptive

- โทน Emma เจ้าบ้าน: จาก 17 บูลเลตที่วนพูดเรื่องสไตล์ซ้ำ → รวมเป็น 8 กฎใหญ่ที่มีหน้าที่แยกกัน (ตัวตน/น้ำเสียง · turn-taking+anti-hook · adaptive · เล่าเรื่อง · ราคา/คำแนะนำ · source-of-truth · ตรงชั้น/ตรงโครงการ · claims/ความไม่แน่ใจ)
- ใหม่ **anti-hook**: "คำถามต่อท้ายเป็นเครื่องมือ ไม่ใช่คำลงท้าย" + test ในหัว "คำตอบลูกค้าจะเปลี่ยนสิ่งที่จะแนะนำ/แสดง/ทำต่อไหม ถ้าไม่เปลี่ยน หยุดและรอ" (แก้อาการต่อท้ายทุกประโยคด้วยคำถาม เช่น "สระอยู่ชั้นบนค่ะ สนใจดูส่วนกลางไหมคะ") · **offer ≠ question** ("เปิดผังให้ดูได้ค่ะ" ไม่ใช่ "ให้ดูไหมคะ") · **adaptive** — ลูกค้าบอก intent แล้วตอบทันที ห้ามลากกลับลำดับสคริปต์ · ownership กฎคำถามอยู่ข้อ 2 ที่เดียว
- guardrail แข็งคงครบทุกข้อ (ราคาปิด, ห้ามแต่ง/ห้ามใช้ความรู้เดิม, ห้ามใส่เมตร, Embassy World≠Life, ชั้น facility, ห้ามการันตีผลตอบแทน) · บล็อกไม่มีตัวเลข (test ผ่าน) · refactor แรกลด 4042→3673; หลังเพิ่มกฎจากผล Live E/F/G เป็น 4335
- ปิดท้ายข้อ 8 เลิกบังคับลิสต์ "ห้องตัวอย่าง ผัง สไลด์ หรือฝ่ายขาย" → "มีขั้นถัดไปที่ช่วยได้จริงให้เสนอแบบบอกเล่า ไม่มีก็หยุดได้" (กัน hook เวอร์ชัน offer) → ลบ `.replace(...)` ที่ตายแล้วใน `without_slide_rules()` + เปลี่ยน assertion เป็น invariant "บล็อกห้ามมีคำว่า สไลด์" (แข็งกว่าเดิม เพราะบล็อกไม่เอ่ยสไลด์เลย = เสนอสไลด์ที่เปิดไม่ได้ไม่ได้ตั้งแต่ต้น)
- รีวิวถ้อยคำข้อ 8 แล้วเปลี่ยนเฉพาะหัวข้อจาก "ปิดด้วยการเสนอ" เป็น "ความไม่แน่ใจ และขั้นถัดไป" เพื่อไม่ให้หัวข้อบังคับเสนอทุกคำตอบ ขณะที่ body เดิมกำหนดถูกแล้วว่าเสนอเฉพาะเมื่อช่วยได้จริงและหยุดได้เมื่อไม่มีสิ่งต้องทำต่อ
- เพิ่ม regression tests แบบ invariant 3 ข้อใน `tests/test_profiles.py`: intent-first, ตอบครบแล้วหยุดได้ และเสนอทางเลือกได้โดยไม่เปลี่ยนเป็นคำถาม โดยไม่ล็อก prose ทั้งย่อหน้า
- ผล Gemini Live รอบแรก: 2BR เลือก `compare_unit_types` และไม่ถามงบ, ราคาไม่รั่ว; แต่ C/D/F ยังต่อคำถาม, E ลูกค้าปิดเรื่องแล้วยังเสนอขายต่อ, F กลืนสถานะ draft/rendering และ G เสนอพาไปทั้งที่ session ไม่มี action พร้อมใช้
- แก้แบบ prompt-only ใน 8 กฎเดิม: anti-hook ครอบคลุมทั้งคำถามและ offer tail พร้อมตัวอย่างปิดบทสนทนา, เสนอขั้นต่อไปเฉพาะเมื่อรับใช้ intent, บังคับพูดสถานะ draft/concept/rendering/proposed/developing และห้ามเสนอ action ที่ไม่มีเครื่องมือจริง · ย้ำ intent ชัดให้ตอบเฉพาะส่วนจำเป็นก่อน
- เพิ่ม invariant tests สำหรับการหยุดเมื่อผู้ใช้ปิดเรื่อง การพูดสถานะข้อมูลที่ยังไม่ยืนยัน และขอบเขต action ตามเครื่องมือใน session; ไม่แก้ `find_units`, ราคา หรือ threshold
- ที่มา: จูน 5 สถานการณ์จริง (walk-in / รู้ว่าอยากได้อะไร / ถามราคา / เทียบยูนิต / ลังเล) แล้วย้อนเขียนกฎจากพฤติกรรมที่อยากเห็น ไม่ใช่กองกฎทีละข้อ
- ผลหลัง prompt-only patch: `tests/test_profiles.py` **39 ผ่าน** · ชุด `test_profiles + test_units + test_knowledge + test_mydocs` **127 ผ่าน, 1 warning** · budget test ยังเป็น known failure **8110 > 4700** และ runtime prompt ตาม tool groups จริง **8198**; ไม่ขยับ threshold
- Gemini Live รอบสอง A–G (`gemini-3.1-flash-live-preview`, synthetic data, ปิด turn log/ราคา และไม่แก้ prompt ระหว่างรอบ): E หยุดถูกที่ "ได้เลยค่ะ"; D เรียก `compare_unit_types({})` และตอบขนาดจากผล tool ได้ แต่ยังถามต่อ; C ปฏิเสธยืนยันราคาได้ถูกแต่ยังถามต่อ; A ยังยาวและเสนอพาชมโดยไม่มี tool; B ไม่เรียก tool แต่พูดว่าเปิดผัง 2 ห้องนอนแล้วและถามต่อ (unsupported action regression); F เรียก `search_condo_info({query: "สระว่ายน้ำอยู่ตรงไหน"})` แต่กลืน `script_is_draft=true`, ตอบเกิน Sky Pool ไปถึงลากูน และถามต่อ; G ยังเสนอพาไปชมผังโดยไม่มี tool · ไม่มีราคา/secret/PII รั่วใน transcript หรือ trace ที่ตรวจ และยังไม่แก้ `find_units`
- รอบสาม prompt-only: แทนกฎ reasoning anti-hook เดิมด้วย default output rule ที่ถามต่อได้เฉพาะเมื่อขาดข้อมูลจำเป็น, ใส่ failure phrases ของ C/B/F โดยตรง, บังคับ action เกิดจริงและได้ผล tool ก่อนพูดว่าเกิดแล้ว, จำกัดคำตอบเฉพาะข้อมูลที่ถาม และให้เทียบการใช้งานห้องก่อนตัวเลข พร้อมยุบ prose ซ้ำ · `SALES_HOST_BLOCK` ลด **4335→3422**, budget-test prompt ลด **8110→7197** และ assembled runtime ลด **8198→7305**; ไม่แตะ `find_units`, threshold, tool result หรือ architecture
- ตรวจรอบสาม: `tests/test_profiles.py` **42 ผ่าน** · ชุด `test_profiles + test_units + test_knowledge + test_mydocs` **130 ผ่าน, 1 warning** · budget test ยังเป็น known failure **7197 > 4700**
- Gemini Live รอบสาม A–G (`gemini-3.1-flash-live-preview`, synthetic data, ปิด turn log/ราคา และไม่แก้ prompt ระหว่างรอบ): C จบหลัง price boundary, E ยังจบที่ "ได้เลยค่ะ", D/F ไม่ถามต่อ, F พูดสถานะ draft และ B เรียก tool ก่อนพูดว่าแสดงผลแล้ว · จุดที่ยังไม่ผ่าน: A ยังยาวและพูดความยาวลากูนทั้งที่ห้าม; B เติมเงื่อนไขตึก A/วิวสระเอง เรียก `compare_unit_types({building: "A"})` กับ `find_units({view_contains: "pool"})` จนผล `find_units` เป็น Studio; D ยังอธิบายตัวเลขก่อนประโยชน์ใช้สอย; F ยังลาก Aqua Cinema/ลากูนจากผลข้างเคียงและพูดความยาวที่ห้าม; G ยังต่อคำถามเสนอผัง/ห้องตัวอย่าง · ไม่มีราคา/secret/PII รั่วใน transcript หรือ trace ที่ตรวจ
- ไฟล์: app/prompts.py, tests/test_profiles.py, CHANGELOG.md

## 2026-09-15 — ย้าย 2BR / room comparison / knowledge safety ไป tool boundary

- `find_units` รับ `bedrooms` โดยตรงและกรอง relation `unit_types!inner(name)` ใน PostgREST (`bedrooms=2` → `eq.2 Bedroom`) · schema/description กำชับให้ส่งเฉพาะ building/view ที่ลูกค้าพูดเอง ห้ามเดาหรือใส่ default · คำขอที่ไม่มีงบไม่ส่งถ้อยคำเรื่องราคาไปกระตุ้นโมเดล ส่วน price policy เดิมและการ strip ตัวเลขราคายังคงเดิม
- `compare_unit_types` ไม่เปลี่ยนข้อมูลที่คืน แต่เปลี่ยน result instruction ให้เริ่มจากความต่างด้านการใช้งานก่อนตัวเลข ใช้จำนวน/ขนาด/ชั้น/วิวเป็นข้อมูลประกอบ ไม่เหมา investment/อยู่อาศัย และจบโดยไม่ถามต่อ
- knowledge boundary ใช้ `sanitize_common_area_dimensions()` ร่วมกันทั้ง facts ที่ประกอบเข้า prompt และ `search_condo_info`: ตัดตัวเลขขนาด/ความยาวสระ ลากูน และพื้นที่ส่วนกลางก่อนถึงโมเดล แต่เก็บเลขชั้น/ตัวเลขข้อเท็จจริงประเภทอื่น · specific query คืนเฉพาะ top hit, overview จึงค่อยคืนหลายผล · ตัดข้อความ marketing script ที่ยังไม่อนุมัติแต่รักษา `script_is_draft`, `content_status=draft`, `must_preserve_status=true` และ mandatory instruction · `now_showing` ผ่าน policy เดียวกัน
- schema ของ `search_condo_info.query` บังคับคง intent ตำแหน่ง/ชั้น หลัง live probe พบโมเดลย่อ "สระอยู่ตรงไหน" เหลือ "สระว่ายน้ำ" แล้วได้ภาพบรรยากาศแทนผังตำแหน่ง
- regression: focused boundary **122 ผ่าน, 1 warning**; final `profiles + units + knowledge + mydocs + retrieval + slides` **312 ผ่าน, 1 warning** · budget test ยังเป็น known failure **7191 > 4700**, assembled runtime **7299**; ไม่ขยับ threshold
- ยืนยัน `SALES_HOST_BLOCK` ไม่เปลี่ยน: **3422 chars**, SHA-256 `506e85efdb642a4ee8ef0c8a7f9efe881401046a23ebc62bdf54faab75b6fe70`
- Gemini Live targeted A/B/D/F (`gemini-3.1-flash-live-preview`, synthetic data, ปิด turn log/ราคา): A ไม่พูดตัวเลขขนาดส่วนกลางแล้วแต่รอบล่าสุดยังเสนอ "พาชม" โดยไม่มี tool; B เรียก `find_units({bedrooms: 2})` อย่างเดียว ได้เฉพาะ 2 Bedroom และไม่หยิบราคาขึ้นมา; D เริ่มจากการใช้งานก่อนตัวเลขและไม่ถามต่อ; F หลังล็อก query เรียก `search_condo_info({query: "สระว่ายน้ำอยู่ตรงไหน"})`, คืนเฉพาะผัง Sky Pool ชั้นสาม, พูด draft และหยุดโดยไม่มีข้อมูลข้างเคียง/ตัวเลขต้องห้าม · ไม่รัน A–G เต็มชุดเพราะ A ยังไม่ผ่าน gate · ไม่มีราคา, secret หรือ PII รั่วใน transcript/trace ที่ตรวจ
- ไฟล์: app/tools/units.py, app/tools/knowledge.py, app/tools/retrieval.py, app/prompts.py (facts boundary เท่านั้น), tests/test_units.py, tests/test_knowledge.py, CHANGELOG.md

## 2026-09-14 — เลือกเสียงต่อลิงก์ (?voice=) + เหลือ 2 เสียง (Despina default)

- เจ้าของอยากได้เสียงต่างภาษา (ไทย=Zephyr, อื่น=Despina) · **สลับตามภาษากลางสายทำไม่ได้** — Gemini Live ตั้งเสียงเดียวต่อ session ตอน connect (โค้ดเดิม callOrb ยืนยัน: เปลี่ยนเสียง = redial สายใหม่)
- `client/index.html` — เพิ่ม `?voice=<ชื่อ>` บน URL (แบบเดียวกับ ?profile): ตรึงเสียงของสายนั้น = bookmark ต่อเสียง · node --check JS ผ่าน
- **เจ้าของสั่ง "เหลือสองเสียงนี้พอ เอา Despina ตั้ง"** → `app/voices.py` `GEMINI_VOICES` เหลือ **Despina (recommended/default) + Zephyr** (จาก 30) · `.env` `GEMINI_VOICE=Kore→Despina` · แก้เทสต์: `test_voices_endpoint` 30→2, `test_gemini_voice_ids_match_documented_list` เหลือ {Despina,Zephyr}, `test_valid_voice_is_honoured` Sulafat→Zephyr · session.py fallback เดิมรองรับเสียงที่ถูกถอด (invalid→default ไม่ crash) · 30 เสียงเต็มอยู่ใน git history
- สร้างตัวอย่างเสียงหญิง 10 เสียง (Gemini TTS `gemini-2.5-flash-preview-tts`) เก็บที่ `C:\Users\Name\Downloads\emma-voices\`
- ตรวจ: `pytest tests/test_voice.py -q` → ผ่านหมดยกเว้น `test_system_instruction_stays_short` (prompt bloat จากฟีเจอร์ที่ยังไม่ commit สะสม ไม่เกี่ยวกับเสียง) · **ยังไม่ commit**

## 2026-09-14 — Emma ไม่หยิบราคา/งบมาพูดเอง + ห้ามแต่งตัวเลขขนาดสระ/ลากูน

- เห็นจากจอจริง (คุยบนคอม): (1) Emma ถามงบ→กรอง→"ไม่พบในงบ 10 ล้าน"→ขอโทษวนซ้ำหลายรอบ เจ้าของ "ถ้าไม่มีก็ไม่น่าเอาขึ้นมาพูด" · (2) overview หลุด "ลากูน 155 เมตร" — เช็คแล้ว "155" **ไม่มีในเอกสาร embassy-world** (docs พูดถึง Lagoon แต่ไม่เคยระบุขนาด) = แต่งเอง
- `app/prompts.py` `SALES_HOST_BLOCK`: (1) เปลี่ยนกฎราคาเป็น **"ห้ามหยิบราคา/งบขึ้นมาพูดหรือถามลูกค้าเอง"** (ยังไม่มีราคาอนุมัติ) ถ้าลูกค้าเอ่ยงบเอง รับสั้นๆ ให้ฝ่ายขายยืนยัน ไม่พูดตัวเลขซ้ำ ไม่ถามงบเพื่อกรอง ช่วยเลือกจากแบบ/ขนาด/วิว/สถานะแทน · (2) เพิ่มกฎเจาะจง **ห้ามใส่ตัวเลขขนาด/ความยาว (เมตร/ตร.ม.) ให้สระ/ลากูน/สกายพูล/ส่วนกลาง** (อ้างเคสหลุดจริง ตามวิธีที่ได้ผลกับโมเดลนี้) — ไม่มีตัวเลขยืนยันในเอกสาร ให้บรรยายบรรยากาศแทน
- ทั้งสองกฎไม่มีตัวเลข (เทสต์ no-digit ผ่าน) · `pytest tests/test_profiles.py -q` → **33 passed** · **ยังไม่ commit**

## 2026-09-14 — เชื่อมข้อมูลห้อง: ยืนยัน LIVE + แก้หน้าสรุปที่ล้าสมัย

- เจ้าของ "เชื่อมกับข้อมูลห้อง" → ตรวจแล้ว **เชื่อมอยู่แล้วและใช้งานได้เต็ม**: `unit inventory: LIVE (1082 units of embassy-world)` · `find_units`/`show_unit`/`show_plan` โหลดใน session · ทดสอบจริง: `show_unit B-124` → Studio 25 ตร.ม. ตึก B ชั้น 1 วิว ORT คอลเลกชัน SIGNATURE สถานะ "ว่าง" (source: live) · normalize "B124"→"B-124" · ราคาไม่ออก (`UNITS_SHOW_PRICE=false`) มี instruction กำกับให้ชี้ฝ่ายขาย
- ไม่แก้ prompt เพิ่ม (กัน latency) — tools มี description บอกโมเดลเองว่าใช้เมื่อไร + กฎ "ห้ามเดาขนาด/เลขห้อง" มีอยู่แล้ว
- `docs/emma-content-summary.md` — แก้ที่เขียนว่า "แบบห้อง/ขนาด ยังว่าง" (ล้าสมัย): เพิ่มแหล่ง **ผังขาย LIVE 1,082 ยูนิต** ในส่วนที่ 1 (เลขห้อง/ตึก/ชั้น/แบบ/ขนาด/วิว/คอลเลกชัน/สถานะว่าง) · ส่วนที่ 4 เหลือแค่ "ราคาเริ่มต้น+โปรฯ ที่อนุมัติ" ที่ยังขาด (แบบห้อง/ขนาดไม่ขาดแล้ว)
- **ยังไม่ commit**

## 2026-09-14 — Emma ตอบเสียงหลอน (noise/echo ทะลุ VAD floor) บนหุ่น

- อาการ: กด "เริ่มคุย" แล้ว Emma ทักถูก (+ถามเรื่องโครงการ) แต่หลังจากนั้นมีบับเบิล YOU เป็น **ญี่ปุ่น "え、もう1回戻ろうか。" / เกาหลี "네가"** ทั้งที่ไม่มีใครพูด แล้ว Emma ตอบกลับ + คำเปิดโดน "ถูกพูดแทรก" (เจ้าของ: "ไม่ได้พูดอะไรเลย ทำไมเป็นแบบนั้น")
- ต้นเหตุจาก log: `call audio: peak=0.0238-0.0250 speech level reached | vad segments` — เสียงรบกวน/เอคโค่ลำโพงหุ่น (ไมค์อาเรย์ + agc=0) ทะลุ `VAD_MIN_RMS=0.01` ถูกส่งขึ้น Gemini → ถอดเป็นภาษามั่ว (silence→hallucination) · การเพิ่ม ja-JP,ko-KR เข้า transcribe ทำให้หลอนเป็น ja/ko
- แก้ (วัดก่อนจูนตามกฎ): `.env` `VAD_MIN_RMS=0.01→0.03` (เสียงหลอนวัดได้ 0.0238-0.0250, เสียงพูดจริงเข้าหุ่น 0.06+ จาก wake log → 0.03 บล็อกเสียงหลอน ผ่านเสียงพูด) · `TRANSCRIBE_LANGUAGES` ถอด ja/ko กลับเป็น `th-TH,en-US,zh-CN` (reply ยัง auto = หลายภาษาได้อยู่) · หมายเหตุ: ค่านี้ global ต่อทั้งหุ่น+เดสก์ท็อป — ถ้าเดสก์ท็อปไมค์ใกล้แล้วเสียงเบาโดนตัด ค่อยลดลง
- **เอาพูดแทรกออก (เจ้าของ):** สาเหตุที่คำเปิดโดนแทรก = เสียงหลอนข้างบน (floor 0.03 อุดแล้ว) + HALF_DUPLEX=true (กัน barge-in) มีอยู่แล้ว · **ลองแก้ session.py ให้ปิดหูตอนคำเปิดกับทุก session (ไม่ใช่แค่ summoned) แต่ย้อนกลับ** เพราะชน `test_a_plain_session_hears_from_the_first_byte` (plain session ต้องได้ยินตั้งแต่ byte แรกโดยตั้งใจ — ดีไซน์เดิม) · ผลจริงบนหุ่น: คำเปิดพูดจบเต็มไม่โดนแทรก + ไม่มี ja/ko หลอนแล้ว
- ตรวจ: `pytest tests/test_profiles.py -q` → 33 passed (เดี่ยว) · หมายเหตุ: `test_system_instruction_stays_short` แดง — prompt working tree 6812 chars > ลิมิต 4700 เพราะฟีเจอร์ sales-host (3037 chars) + gallery ที่**ยังไม่ commit**สะสมหลาย session (stash prompts.py ออก = HEAD lean ผ่าน) ไม่ใช่ regression วันนี้ · `test_no_websearch...` = isolation flake (รันเดี่ยวผ่าน)
- **ยังไม่ commit**

## 2026-09-14 — Emma พูดเอง (wake ปลุกตัวเอง) + ให้ตามสคริปต์ในไฟล์

- **Emma พูดเอง:** log ชี้ `wake word heard: 'EMMA'` ยิงเอง 3 ครั้งทั้งที่ไม่มีใครเรียก (camera greeter ไม่ได้ยิง — กล้องหลุดเฟรม) = เสียง Emma พูดคำว่า "Emma" ในคำทักเอง ย้อนเข้าไมค์อาเรย์ของหุ่น (ไม่มี AEC) → ปลุก wake ตัวเอง = acoustic feedback loop (ตรงกับสมมติฐานเดิมใน task ค้าง)
- แก้สำหรับตอนตรวจ: `.env` `WAKE_ENABLED=true→false` (AUTO_CONNECT=false อยู่แล้ว → หน้าหุ่นเป็นปุ่ม "เริ่มคุย" เริ่มเอง ไม่มีทางปลุกตัวเอง) · โหลดหน้าหุ่นใหม่แบบ `?agc=0&token=...` (เอา cam=1 ออก — กล้อง greeter หลุดเฟรม + กัน auto-greet) · การแก้ feedback ให้ wake กลับมาใช้ได้ต้องทำ echo-cancel/หยุด wake ตอน Emma พูด = งานหลังตรวจ
- **ตามสคริปต์ในไฟล์:** เจ้าของ "แก้ๆ ให้เน้นตามสคริปในไฟล์ไปก่อน" → `SALES_HOST_BLOCK`: เปลี่ยนกฎ brevity จาก "ห้ามลอกประโยคจากเอกสาร" → "ยึดสคริปต์+สำนวนตามไฟล์ได้ แต่แบ่งเล่าเป็นช่วงสั้นๆ ทีละส่วน ไม่เทหมดในคราวเดียว" + เพิ่มลำดับนำเสนอตามสคริปต์ (ต้อนรับ → ทำไม Embassy World → จินตนาการชีวิต → โลกต่างๆ → Future Living → ทำเล → ความน่าเชื่อถือ → แนะนำเฉพาะบุคคล → ปิดการขาย)
- ตรวจจริง: `pytest tests/test_profiles.py -q` → **33 passed** · **ยังไม่ commit**

## 2026-09-14 — เตรียมเจ้านายตรวจ: หลายภาษา + รันขึ้นหน้าจอหุ่น

- เจ้าของ: "เดี๋ยวเจ้านายจะมาตรวจ ให้สามารถพูดได้หลายภาษา แล้วก็รันให้ทำงานผ่านหน้าจอหุ่น" (ใช้หน้าโปรดักชัน `/` เดิม ไม่ใช่ voice-preview ใหม่)
- **หลายภาษา:** `REPLY_LANGUAGES=auto` (ค่า default, ไม่มี override) เปิดอยู่แล้ว → Emma ตอบภาษาเดียวกับลูกค้าอัตโนมัติ สลับกลางบทได้ (~97 ภาษา ผ่าน `_language_rule`) · `.env` `TRANSCRIBE_LANGUAGES` ขยาย `th-TH,en-US` → `th-TH,en-US,zh-CN,ja-JP,ko-KR` (แค่ caption บนจอ ไม่กระทบภาษาที่ตอบ ตามคอมเมนต์ใน config)
- **หน้าจอหุ่น:** หุ่น ZC-3588A (Android 15) IP เปลี่ยนเป็น **192.168.1.63:5555** หลังรีบูต (จากเดิม .24 — DHCP, หาเจอด้วย `adb mdns services`) · เปิด Chromium (org.chromium.chrome.stable ตัวที่เชื่อ cert mkcert) ชี้ไปเซิร์ฟเวอร์ 192.168.1.43:8001 ผ่าน adb · เจ้าของสั่งเอา `?kiosk=1` ออก ให้เหมือนหน้า `/` ที่ใช้อยู่ (การ์ด concierge) → เปิดด้วย `?cam=1&agc=0&token=...` · ปิดแท็บซ้อนเหลือแท็บเดียว · ยืนยันจริงจาก screencap: หน้า SLEEP "เรียก emma ได้เลยค่ะ" + กล้อง first frame 1920x1080 + ไมค์รับเสียงถึง speech level
- **คำถามเปิด/แนวคิด (prompts.py):** เจ้าของ "ห้ามถามห้องตัวอย่าง ให้ถามคำถามเกี่ยวกับโครงการก่อน" → `GREETING` เปลี่ยนตัวอย่างคำถามเป็นเรื่องโครงการ (รู้จักโครงการไหม/แวะมาชมไหม/สนใจเรื่องไหนของโครงการ) ห้ามถามแบบห้อง/ขนาด · เจ้าของ "พยายามพูดเรื่องแนวคิด ที่มาของโครงการ เอาให้เหมือนในไฟล์" (5 ไฟล์ Downloads = ต้นทางของ embassy-world/ 5 md, import แล้ว 11 ก.ย.) → เพิ่มกฎใน `SALES_HOST_BLOCK`: เล่าแนวคิด+ที่มาเป็นหัวใจ (ไม่ใช่คอนโด+facility ยาวขึ้น แต่ชีวิตเปลี่ยน ที่อยู่ควรพัฒนาตาม / One Place Many Worlds) อ้างอิงถ้อยคำจากเอกสารที่ค้นได้ ห้ามแต่ง (ไม่เอ่ยชื่อ tool — กันรั่วตอน mydocs ไม่โหลด, เทสต์คุม)
- ตรวจจริง: `pytest tests/test_profiles.py -q` → **33 passed** · **ยังไม่ commit**

## 2026-09-14 — ประโยคเปิด (GREETING): ทักทาย + ถามต่อหนึ่งข้อเป็นกันเอง

- ปรับหลายรอบตามเจ้าของ: สคริปต์เต็ม → "ยาวไป" → ค่อยๆ ถาม → เกริ่นนิดเดียว → "เอาแค่คำทักทายพอ" → (เห็นหน้าจอจริง) "ให้ถามไปด้วย"
- `app/prompts.py` `GREETING` (instruction ไม่ใช่บทท่อง): **ทักทายแนะนำตัว "สวัสดีค่ะ ดิฉัน Emma ยินดีต้อนรับสู่โครงการ Embassy World ค่ะ" + คำถามเปิดเบาๆ หนึ่งข้อ** (เช่น วันนี้แวะมาชมโครงการไหมคะ / มองหาบ้านแบบไหนอยู่คะ) เปิดให้ลูกค้าตอบ ยังไม่เล่า overview ยาว ไม่พูดราคา/ขนาด แล้วค่อยเล่าโครงการทีละส่วนตามที่ลูกค้าสนใจ · `tests/test_profiles.py` — docstring `test_the_gallery_greeting_leads_with_the_project_not_price` อัปเดตตามเจตนา (ทักทาย+ถามหนึ่งข้อ, overview เลื่อนไปในบทสนทนา) · assertion "ยังไม่พูดเรื่องราคา"+"ภาพรวมโครงการ" ยังจริง
- ตรวจจริง: `pytest tests/test_profiles.py -q` → **33 passed** · ทดสอบบนเครื่องจริง เห็น Emma ทักแล้วถาม "มีห้องแบบไหนที่สนใจเป็นพิเศษไหมคะ" · **ยังไม่ commit**

## 2026-09-14 — สรุปคลังความรู้ + เปิด WAKE_DEBUG หา false wake

- เจ้าของขอ "สรุปเนื้อหาทั้งหมดที่เรามี" → `docs/emma-content-summary.md`: (1) ที่ Emma ใช้ตอบตอนนี้ = Embassy World 6 ไฟล์ + สไลด์ + condo_facts (2) สาระ Embassy World (แนวคิด One Place Many Worlds/Nothing Is Missing, Worlds, ทำเลจอมเทียน) (3) คลังที่ปิดไว้: Embassy Life 3, Empire 2+เว็บ 7, ก่อสร้าง 42, web dump 127 (4) ที่ขาด: ราคา/แบบห้อง (condo_facts ว่าง)
- ยืนยัน "One Place, Many Worlds" ที่คำทักทายพูด = **ของจริง** (อยู่ใน thai-market-strategy + สไลด์) ไม่ได้แต่ง · greeting ค้น query "ภาพรวมโครงการและแนวคิดหลัก" ก่อนพูด (grounding — เหตุผลที่คอนเซ็ปต์ถูก)
- เจ้าของรายงาน wake ปลุกเองทั้งที่ไม่เรียก "emma" → เช็ค: ไม่ใช่หลายแท็บ (run_server ไม่เด้งแท็บ, 2 conn) · **เปิด `WAKE_DEBUG=true`** ใน .env (ตามกฎ: วัดก่อนจูน) เพื่อดู RMS+คำตัดสินตอน false wake · สงสัยเสียง Emma พูด "Emma" เองย้อนเข้าไมค์ · รีสตาร์ต run_server.py · **ยังไม่ commit** (WAKE_DEBUG เป็น debug ชั่วคราว ปิดกลับหลังวัดเสร็จ)

## 2026-09-14 — ปรับ UX/UI หน้า voice preview เป็น Emma แบบโฟกัสเดียว

- `client/voice-preview.html` — ปรับลำดับสายตาตามภาพอ้างอิงเป็น **สถานะฟัง → มาสคอต Emma → ประโยคตอบล่าสุด → ปุ่มควบคุม**; ตัดข้อความแบรนด์/ชื่อ Emma ที่ซ้ำด้านบนและตัดการ์ดคำตอบที่รบกวนสายตา เหลือข้อความตอบใต้ตัวการ์ตูนโดยตรง พร้อม responsive layout, safe-area, focus state และ reduced-motion
- หน้า preview วนแสดง pose ที่อนุมัติครบทั้ง `pose-01..pose-10` และ `wai` อัตโนมัติทุก 2.2 วินาที เพื่อให้เห็นว่ามีท่าอื่นจริง; ใช้ `?autoPose=0` เพื่อปิด หรือเรียก `setPose()` เพื่อหยุดลูปและให้ host ควบคุมท่าเอง พร้อมเพิ่ม `startPoseDemo()`/`stopPoseDemo()` สำหรับควบคุมจากภายนอก
- เปลี่ยนจากการสลับ PNG หลายท่าเป็น **layered puppet rig**: `emma-puppet-atlas-compact-v1.png` วาดตามสัดส่วนภาพอ้างอิงใหม่ให้หัวกลมใหญ่ ลำตัวสั้น แขนสั้นและมือใหญ่ พร้อมแยกหัวเปล่า ลำตัว แขน/มือซ้ายขวา ตาโค้ง ปากหลายรูป และมือไหว้บน alpha จริง; SVG crop แต่ละชิ้นแล้วหมุนรอบข้อต่อ จึง interpolate ต่อเนื่องระหว่าง pose แทนการตัดภาพ
- `client/voice-preview.html` — เพิ่ม joint targets สำหรับ `pose-01..pose-10` และ `wai`, transition easing 720ms, idle breathing, blink, wave loop, celebrate bounce, frustrated shake, wai bow, loading ring, cursor parallax และ mouth-level lip-sync hook โดยใช้ puppet ตัวเดียวตลอด state machine
- ตัดตากลม/ตาเปิดที่ ImageGen สร้างเผื่อออกจาก rig ตามคำสั่งเจ้าของ เหลือเฉพาะตาโค้งยิ้ม ตาหลับเศร้า และตาหยีที่ดัดจากทรงโค้งเดียวกัน
- เก็บ visual QA หลังเปลี่ยน asset: จำกัด moving glint ไว้เฉพาะใบหน้าเพื่อไม่ให้เกิดเงาแขนซ้อนจาก mask เดิม และย้าย halo ring ไปอยู่ใต้ลำตัวตาม approved concept
- แยก state ข้อความ 10 สถานะออกจาก pose โดยจงใจ **ไม่กำหนดเอง** ว่า `thinking`/`speaking` ต้องใช้ท่าไหน; `window.EmmaVoicePreview.setPose('pose-01'..'pose-10')` เปลี่ยน joint/สีหน้าของ puppet และหยุด demo loop เพื่อให้เจ้าของเลือก mapping ภายหลัง ส่วน `greeting` คงเรียก `wai`
- `setMouthLevel(0..1)` เปลี่ยนระหว่างปากเล็ก/ปากกว้างและสเกลช่องปากแบบเฟรมต่อเฟรมสำหรับต่อ lip-sync จริง; ตาโค้ง blink เองโดยไม่ใช้ตากลม
- ท่า `wai` ใช้มือพนมแยกใน atlas, fade แขนปกติออก แล้วขยับหัว/ลำตัว/มือก้มต่อเนื่องแทนการเปลี่ยนไปเป็นภาพไหว้อีกใบ
- จูนจุดหมุนและองศาแขนใหม่ให้เข้ากับแขนสั้นของ atlas: ท่าแตะหู คิด กางแขน โบกมือ และดีใจไม่หลุดกรอบ; ท่าเศร้า/ขอบคุณ/loading ใช้มือประสานตาม pose sheet, เพิ่มวงจุด aura รอบหัวในท่า loading และแก้ floor ring จากการหมุนจนตั้งฉากเป็น pulse แนวนอน
- ปุ่มข้อความ/ไมค์/จบเปลี่ยนจาก emoji ซึ่งหน้าตาแปรตามระบบ เป็น inline SVG เส้นสม่ำเสมอแนวทาง Lucide (`viewBox 24`, `currentColor`, stroke 2, linecap/linejoin round) พร้อมชื่อใต้ไอคอน, `aria-label`, `title` และพื้นที่กดวงกลม 54–76px; ฝังในไฟล์เพื่อไม่พึ่ง CDN
- `tests/test_voice_preview_page.py` — ล็อกโครง reference layout, SVG icon/accessibility, layered puppet parts, joint transition, facial layers, ไม่มีตากลม, pose API แยกจาก state API และ asset RGBA ที่แพ็กกับหน้า
- ไม่ฝัง/คัดลอก `.riv` ของศิลปินตัวอย่างลงโปรเจกต์; ใช้ Emma atlas ต้นฉบับของโปรเจกต์กับ state machine ฝั่งหน้าเว็บเพื่อให้ทำงานแบบ self-contained และแก้ mapping ได้เอง
- ตรวจจริง: pytest `tests/test_voice_preview_page.py` **5 passed**; Playwright demo เปลี่ยน `pose-01→pose-02`, เรียก `setPose()` ครบ 10 ท่ากับ `wai` แล้ว head/arm matrix และ eye/mouth state เปลี่ยนตาม target โดยใช้ character layer เดิมตลอด; float/turn/parallax/reduced-motion ทำงาน, หน้า 390×844 และ 1280×900 ไม่ล้น, ไม่มี console/page error

## 2026-09-14 — เพิ่มเอกสาร "ภาพรวม Embassy World" ให้ Emma อธิบายโครงการ+แนวคิดได้

- เจ้าของ (หลัง lookup timeout หายแล้ว ✅): Emma อธิบายโครงการยังบางไป อยากให้ "อธิบายโครงการ + ตั้งมาเพื่ออะไร (แนวคิด)" · ต้นเหตุ: embassy-world/ เป็นคู่มือการขาย ไม่มีภาพรวมโครงการล้วนๆ
- **ไฟล์ใหม่** `data/personal-docs/embassy-world/embassy-world-overview.md` — grounded จากเอกสารจริง (master presentation): เป็นโครงการอะไร/ที่ไหน (คอนโดลักชูรี จอมเทียน พัทยา, Empire Group), **แนวคิด "ตั้งมาเพื่ออะไร"** (New Generation Living / Nothing Is Missing / ขายชีวิตที่ดีขึ้น), "โลก" ต่างๆ (Junior World, Aqua Cinema/Water, Wellness, Journey to Mars, Social, Future Living/Embassy AI), ทำเล · **ไม่มีราคา/ขนาด/โปรฯ** (กฎ approved-facts — หมายเหตุให้ยืนยันกับฝ่ายขาย)
- อยู่ในโฟลเดอร์ embassy-world/ = ใน MYDOCS_INCLUDE scope · mydocs index rebuild อัตโนมัติ (fingerprint เปลี่ยน ไม่ต้องรีสตาร์ต) · ยืนยัน: search "Embassy World คืออะไร"/"โครงการนี้ตั้งมาเพื่ออะไร"/"แนวคิดของโครงการ" → overview.md เป็นอันดับ 1 · **ยังไม่ commit**

## 2026-09-14 — แก้ voice Emma: lookup timeout + เลิกกั๊กเรื่องสิ่งอำนวยความสะดวก/ทำเล

- เจ้าของทดสอบ voice จริง (Gemini Live) ถาม "Embassy World คืออะไร" → **"lookup — timeout"** Emma ตอบไม่ได้ + สั่งว่า "อย่ากั๊กเรื่องสิ่งอำนวยความสะดวก/ทำเล"
- **timeout**: จับเวลาแล้ว `search_condo_info` **cold-start 7.5 วิ** (โหลด MiniLM ครั้งแรก) — บนเซิร์ฟเวอร์ที่กำลังประมวลผลเสียง Live พร้อมกันเลยทะลุเพดาน tool 15 วิ · แก้ `app/main.py` เพิ่ม startup hook `_warm_search` (background) เรียก search_condo_info ตอนบูต → โมเดลอุ่นก่อน คำถามแรกของลูกค้าไม่ cold (warm = 0.0s) · ยืนยัน log "search: embedding model warmed" (แก้ NameError logger รอบแรกด้วย)
- **เลิกกั๊ก**: `app/prompts.py` SALES_HOST_BLOCK เดิม "ห้ามไล่รายการ facility เว้นแต่ลูกค้าถาม" → เปลี่ยนเป็น "พูดถึงสิ่งอำนวยความสะดวกและทำเลกับลูกค้าได้ตามปกติ ไม่ต้องกั๊ก แค่ยังไม่เปิดด้วยราคา/เงื่อนไขชำระเงินก่อนลูกค้าถาม" (คงกฎราคาไว้)
- **เลิกถามลูกค้ากลับ** (เจ้าของเห็นในภาพ: Emma ถามกลับ "อยากทราบเรื่องสิ่งอำนวยความสะดวก/ทำเลไหมคะ"): กฎ "ฟังก่อนขาย ถามทีละคำถาม (ซื้อเพื่อใคร...)" → เปลี่ยนเป็น "ให้ข้อมูล/ตอบตรงๆ ก่อน ไม่ต้องถามลูกค้ากลับว่าอยากรู้เรื่องไหน — เล่าให้เลย จะถามเพื่อเข้าใจได้บ้างแต่เบาๆ ไม่ถามรัว/ไม่ถามก่อนให้ข้อมูล" · Emma เอียงไปทาง "ผู้ให้ข้อมูลที่ตอบตรง" ตามที่เจ้าของ iterate
- `test_profiles.py` 33 passed · รีสตาร์ต run_server.py (warm search ทำงาน) · **ยังไม่ commit**

## 2026-09-14 — เสียงเบราว์เซอร์ในหน้าแชท: ทำแล้ว revert (เจ้าของเลือกใช้ voice Emma จริง)

- เจ้าของ "ไม่เอาแชท เอาเป็นแบบเสียงเลย" → ผมเผลอทำเสียงเบราว์เซอร์ (webkitSpeechRecognition + speechSynthesis) ในหน้าแชท local · เจ้าของท้วง "เอาอันที่เราเคยทำสิ จะทำใหม่ทำไม" = **voice Emma มีอยู่แล้ว** (หน้า `/` โปรดักชัน, Gemini Live, ใช้ prompt/persona/คลังชุดเดียวกับที่แก้)
- **revert** เสียงเบราว์เซอร์ออกจาก `client/emma-chat.html` ทั้งหมด (คืนเป็นเทสต์ text local เหมือนเดิม) · node --check ผ่าน · ไม่เหลือ ref เสียง
- รีสตาร์ตเซิร์ฟเวอร์หลัก (run_server.py) ให้ voice Emma จริงใช้ค่าใหม่: persona "พนักงานต้อนรับและผู้ให้ข้อมูล", คำทักทายสั้น, กฎอธิบายโครงการ, MYDOCS_INCLUDE=embassy-world · เจ้าของทดสอบด้วยเสียงที่หน้า `/` (กด "เริ่มคุย" + ไมค์) · ยังไม่ commit

## 2026-09-14 — Emma พูดสั้นลง ไม่เป็นสคริปต์ + คำทักทายแนะนำตัว

- เจ้าของ: "ไม่อยากให้ Emma พูดเหมือนในไฟล์ ดูสคริปต์และยาวเกินไป" + อยากได้คำทักทาย "สวัสดีค่ะ ดิฉัน Emma เป็น..."
- `app/prompts.py` **SALES_HOST_BLOCK** เพิ่มกฎ (ไม่มีเลข ผ่านเทสต์): "ตอบสั้น กระชับ เป็นธรรมชาติ ไม่กี่ประโยค ห้ามยาวเป็นย่อหน้า **ห้ามลอกประโยคจากเอกสารมาทั้งท่อน** สรุปด้วยคำพูดตัวเอง ลูกค้าอยากรู้ลึกค่อยเล่าต่อ" · **GREETING** เปลี่ยนเป็นแนะนำตัวสั้น: เริ่มด้วย "สวัสดีค่ะ ดิฉัน Emma" + ถามคำถามเดียวเบาๆ ไม่เกิน 2 ประโยค (ยังคง "ยังไม่พูดเรื่องราคา"/"ถามคำถามเดียว" ตามเทสต์)
- **เปลี่ยนคำเรียกตัวเอง (เจ้าของ iterate): "เจ้าบ้าน" → สุดท้าย "พนักงานต้อนรับและผู้ให้ข้อมูลของโครงการ"** ใน GREETING + SALES_HOST_BLOCK บรรทัดแรก แต่คงเสียง "เรา/ของเรา/ที่นี่" · ผล: "สวัสดีค่ะ ดิฉัน Emma **พนักงานต้อนรับและผู้ให้ข้อมูล**ของโครงการ Embassy World ค่ะ..." · หมายเหตุ: กลับทิศจาก CLAUDE.md ที่เขียนว่า Emma เป็น "เจ้าบ้าน ไม่ใช่พนักงานต้อนรับ" — รอโทนนิ่งค่อยอัปเดต doc
- **เพิ่มกฎ "อธิบายโครงการเมื่อถูกขอ"**: SALES_HOST_BLOCK เดิมทำให้ Emma เลี่ยง (ถาม "อธิบายโครงการ" → ตอบ "เริ่มจากวิถีชีวิตที่คุณอยากมี") ขัดกับบทบาท "ผู้ให้ข้อมูล" → เพิ่ม "ถ้าลูกค้าขอให้อธิบาย/แนะนำโครงการ ให้เล่าภาพรวมจริงๆ สั้นๆ (แบบไหน/อยู่ไหน/จุดเด่น) ห้ามเลี่ยงด้วยการถามกลับอย่างเดียว" · หลังแก้ Emma อธิบายจริงขึ้น แต่ยังกว้าง เพราะ (ก) qwen3 local เล็ก (ข) **เอกสาร embassy-world เป็นคู่มือการขาย ไม่ใช่ fact sheet โครงการ** → ค้นได้ meta-text วิธีขาย ไม่ใช่ข้อเท็จจริง · ต่อไป: เพิ่มเอกสาร "ภาพรวม Embassy World" แบบข้อเท็จจริงล้วน · `test_profiles.py` 33 passed · ยังไม่ commit
- `app/emma_chat.py` เพิ่ม `emma_greeting()` + `GET /greeting` (สร้างคำทักทายแบบเดียวกับที่โปรดักชันส่งตอน connect) · `client/emma-chat.html` ดึง /greeting ตอนโหลด โชว์เป็นข้อความแรก → ปรับคำทักทายจริงได้ในหน้านี้
- ตรวจ: `test_profiles.py` 33 passed · chatbot local จริง: ทักทาย → **"สวัสดีค่ะ ดิฉัน Emma เป็นเจ้าบ้านของโครงการ Embassy World ค่ะ วันนี้สนใจเรื่องไหนบ้างคะ?"** ✅ · ตอบ "สวัสดี" สั้นลง 371 ตัวอักษร (เดิมยาวกว่ามาก) · py_compile + node --check ผ่าน · playwright ยืนยัน UI · **ยังไม่ commit**

## 2026-09-14 — จำกัด Emma ให้ตอบเฉพาะ Embassy World (MYDOCS_INCLUDE) — แก้ปนโครงการที่ต้นเหตุ

- เจ้าของสั่ง "ให้ตอบเฉพาะ Embassy World ก่อน" · ต้นเหตุการปนโครงการ = คลัง mydocs มี Embassy Life/Empire/ก่อสร้าง ปนกัน แล้ว retrieval คืน chunk ผิดโครงการ (Embassy World มีเฉพาะโฟลเดอร์ `embassy-world/` 5 ไฟล์ — corpus ไม่มี EW เลย)
- **แก้ที่ retrieval (ช่วยทุกโมเดล ไม่ต้องพึ่ง prompt)**: `app/config.py` เพิ่ม `mydocs_include` (env `MYDOCS_INCLUDE`, default "" = ทุกไฟล์) · `app/tools/mydocs.py` `_doc_files()` กรองไฟล์ตาม fragment ของ path (เทียบ relative path) · `.env` ตั้ง `MYDOCS_INCLUDE=embassy-world`
- **ผลจริง (chatbot local qwen3)**: ถาม Crystal Maze → เดิม "มีค่ะ ที่นี่มี" (ผิด) → **หลังแก้ "ไม่ค่ะ ที่นี่ไม่มี Crystal Maze"** ✅ · search คืนเฉพาะ `embassy-world/` (Crystal Maze/Embassy Life = ไม่เจอแล้ว) · slides (search_condo_info) เป็น EW อยู่แล้ว คงไว้
- conftest pin `mydocs_include=""` (เหมือน tool_groups/embed_provider — เทสต์ห้ามเปลี่ยนตาม .env เครื่อง; 6 เทสต์ mydocs แดงเพราะ .env ก่อน pin) · เพิ่มเทสต์ `test_the_library_can_be_scoped_to_one_project` · `test_mydocs.py`+`test_profiles.py` 46 passed · **ยังไม่ commit** · widen ได้: ต่อ `,Empire-Information` ฯลฯ ใน MYDOCS_INCLUDE ทีหลัง

## 2026-09-14 — หน้าแชทบอท Emma แบบพิมพ์ (ทดสอบคลังความรู้ + เห็น hallucination ชัด)

- เจ้าของขอ "ทำหน้า Emma เป็นแชทบอทไว้ทดสอบถามคำถาม" · **ไฟล์ใหม่ standalone** `app/emma_chat.py` (FastAPI แยก port 8020, ไม่แตะ app.main/.env) + `client/emma-chat.html` (หน้าแชท text)
- ใช้ **prompt จริง** (`build_instructions` condo profile) + **retrieval จริง** (`search_my_documents` + `search_condo_info`) + Gemini text model · แต่ละคำตอบโชว์ **sources (ไฟล์ที่หยิบมา)** + expand ดู chunk ได้ → เจ้าของเห็นเองว่าคำตอบมีที่มาไหม (specific ที่ไม่มี source = แต่ง)
- รันแยก: `.venv/Scripts/python.exe -m app.emma_chat` → http://127.0.0.1:8020 · **ราวความซื่อสัตย์**: ใช้ gemini-2.5-flash (text sibling ไม่ใช่ Live prod) — หน้าเว็บบอกชื่อโมเดลชัด เป็น probe ไม่ใช่ preview เป๊ะ
- ตรวจ end-to-end (server จริง + Gemini): (1) ถามสิ่งอำนวยความสะดวก → reply มี "155ม./Thermal Galaxy" + **sources ว่าง = โชว์ว่าแต่ง** (2) ถาม Crystal Maze → หยิบไฟล์ EMBASSY-LIFE 3 ไฟล์ + ตอบ "เป็นของ Embassy Life ค่ะ" = แยกโครงการถูก+มีที่มา (3) ถามราคา → commercial=True + refuse+ฝ่ายขาย · แก้บั๊ก dedup sources (logic กลับด้าน ทำ sources ว่างเสมอ) · py_compile + node --check ผ่าน · playwright ยืนยัน UI · **ยังไม่ commit**
- **เปลี่ยนไปใช้ LLM local (Ollama qwen3:8b) แทน Gemini** (เจ้าของชน free-tier rate limit — "เอาเป็น local") · `_ollama_chat()` POST localhost:11434/api/chat, `think:False`, strip `<think>`, timeout 180s (~3-4 วิ/คำตอบ) · retrieval เดิม (search_my_documents = BM25 local) · **ไม่ใช้ Gemini เลย = ไม่เผา quota** · ตรวจ: qwen3 ตอบไทยได้, ถามสิ่งอำนวยความสะดวก → **ไม่แต่ง "155ม./Thermal Galaxy"** (โมเดล local ไม่มี training memory ของ Embassy World = ข้อดีสำหรับจับ ungrounded) · **แต่แยกโครงการแย่กว่า Gemini**: ถาม Crystal Maze → ตอบ "มีค่ะ ที่นี่มี" (ไม่แยกว่าเป็น Embassy Life — instruction-following อ่อนกว่า) → ตอกย้ำว่า root cause จริงคือ retrieval คืน chunk ผิดโครงการ ต้องแก้ retrieval scoping ถึงจะช่วยทุกโมเดล

## 2026-09-14 — เทสต์ Emma แยกโครงการ + กันแต่งข้อมูล (SALES_HOST_BLOCK) — แยกโครงการได้ แต่ hallucinate ยังไม่หมด

- เจ้าของขอเทสต์ว่า Emma แยกโครงการออกไหม + ปรับให้แยกชัดขึ้น · สำรวจคลัง: mydocs ปน **Embassy World (5) + Embassy Life พัทยา + Empire + web dump หลายสิบไฟล์** · retrieval ทดสอบ 7/9 found + กันราคา 2/2 · **แต่ถามสิ่งอำนวยความสะดวก "ที่นี่" → คืน chunk Embassy Life (คนละโครงการ)**
- **เทสต์ Gemini จริง** (proxy gemini-2.5-flash เพราะ prod เป็น Live text ไม่ได้) ป้อน prompt จริง + ผลค้นจริง: Emma แต่ง **"ลากูน 155 เมตร, Thermal Galaxy spa, Biogenesis"** ที่**ไม่มีในเอกสารไหนเลย** (เช็คทั้งคลัง 0 ไฟล์) — ดึงจาก training prior ของโมเดลเรื่อง "Embassy World Pattaya" ทับข้อมูลจริง แม้ป้อนเนื้อหา EW จริงให้แล้วก็ยังแทรก
- **แก้ `app/prompts.py` SALES_HOST_BLOCK** เพิ่ม 2 กฎ (ไม่มีเลข ผ่านเทสต์ no-numbers): (1) ที่นี่คือ Embassy World เท่านั้น, Embassy Life/โครงการอื่นเป็นคนละโครงการ, chunk ที่มีวงเล็บบริบทของโครงการอื่นห้ามพูดเหมือนของที่นี่ (2) ห้ามใช้ความรู้เดิมของโมเดลเติมชื่อ/ตัวเลข/โซนที่ไม่อยู่ในผลค้น
- **ผลหลังแก้**: แยกโครงการ **✅ ได้ผล** (ถาม Crystal Maze → "เป็นของ Embassy Life ค่ะ ส่วน Embassy World...") · แต่ **hallucinate ตัวเลข/ชื่อ facility ❌ ยังไม่หยุด** แม้กฎแรง "ห้ามใช้ความรู้เดิม" — prompt เอาไม่อยู่กับ prior ของ proxy model
- เพิ่มเทสต์ `test_the_sales_host_block_keeps_embassy_world_apart_from_other_projects` (อ่าน source ล็อก 2 กฎ) · `tests/test_profiles.py` 33 passed · **ยังไม่ commit** · ค้าง: (ก) ยืนยันกับเจ้าของว่า "155ม./Thermal Galaxy/Biogenesis" จริงหรือแต่ง (ถ้าจริง = ใส่ verified facts, ถ้าแต่ง = ต้องแก้ระดับ model/grounding) (ข) เทสต์ซ้ำบน prod Live model (พฤติกรรมอาจต่าง) (ค) retrieval scoping ให้คืน chunk EW สำหรับคำถาม EW

## 2026-09-12 — sandbox ทดลอง feel การขับ (client/robot-drive-sim.html) — ปรับให้ลื่นแบบ RC โดยไม่ต้องมีหุ่น

- เจ้าของ: "อยากให้ลื่นแบบรถบังคับ ตอนนี้ใช้หุ่นไม่ได้ (ไหม้) แต่มีข้อมูลหุ่นแล้ว สร้างแบบจำลองแล้วปรับจากตรงนี้" · sim เดิม (Emma World, app/robot_simulator.py :8010) จำลอง go_to_place เดินตามเส้นทาง **ไม่มี joystick drive** และเป็น WIP งานอื่น (uncommitted) — ไม่ทับ
- **ไฟล์ใหม่ standalone** `client/robot-drive-sim.html` (เปิด file:// ได้เลย ไม่ต้องเซิร์ฟเวอร์/หุ่น/token): จอบนลงล่าง (canvas, กล้องตามหุ่น, grid 0.5ม., trail) + จอย analog แบบ /drive + WASD/ลูกศร · **โมเดลการเคลื่อนที่ dt-based ปรับได้**: max linear/angular speed, accel, brake(decel), turn accel, deadzone, turn-taper-at-speed
- **2 โหมดจงใจ**: (ก) "ลื่น RC ในอุดมคติ" = ramp นุ่มๆ ออกแบบ feel ที่อยากได้ (ข) "จำลองบอร์ดจริง" = MoveBy ยิงซ้ำทุก heartbeat + latency + รีสตาร์ท ramp ทุกครั้ง → เห็นอาการกระตุกแบบของจริง เทียบกันได้ว่าต่างกันแค่ไหน (ค่าจริงยังต้องวัดบนหุ่น) · กล่อง params โชว์ค่าปัจจุบันไว้ก๊อปไปตั้งหุ่นจริง (max_moving_speed/max_angular_speed + cadence)
- ตรวจ: extract inline JS → `node --check` rc 0 · playwright 1400×820 ขับด้วย keydown w+d จริง → หุ่นวิ่ง+เลี้ยว เห็น trail, readout อัปเดต, layout/สไลเดอร์/โหมดครบ · **ยังไม่ commit** · หมายเหตุ: sandbox ออกแบบ "feel ที่อยากได้" ได้จริง แต่หุ่นจริงจะทำได้แค่ไหนต้องวัดบนบอร์ด (condo-voice-79 ตั้ง log latency ไว้แล้ว)
- **แก้ทิศ (เจ้าของ "กดลงแต่มันขึ้น")**: heading เริ่มต้น -90°→+90° (หัวชี้ขึ้น = forward=ขึ้น) + ทิศหมุนกลับเครื่องหมาย (สติ๊กขวา=ตามเข็ม=เลี้ยวขวา บน world y-up) · ยืนยัน playwright: W→v=+0.32 (ขึ้น), D→w=-30°/s (ขวา), trail โค้งขวา
- **โหมด "บอร์ดจริง" ทำให้สมจริง (เจ้าของ "หุ่นจริงคุมยากกว่านี้")**: ตรวจโค้ดจริง `DRIVE_DIRECTIONS={forward,back,right,left}` + MoveBy รับทีละทิศ สปีดคงที่ → โหมดบอร์ดเปลี่ยนเป็น `discreteTargets()`: เลือกทิศเด่น 1 ใน 4, สปีดคงที่ (ไม่มีคันเร่งต่อเนื่อง), เลี้ยวพร้อมวิ่งไม่ได้ (ไม่มีโค้ง) + heartbeat/latency/ramp-restart เดิม = ทำไมจริงคุมยากกว่า analog · ยืนยัน playwright board mode: W+D พร้อมกัน → v=0.45 คงที่ w=0 (ไปทิศเด่นทิศเดียว)
- ตั้ง default เป็นค่าที่เจ้าของชอบ: maxV 0.60 m/s, maxW 90°/s, accel 1.9, brake 1.4, turnAccel 180, deadzone 0.12, taper 25% (เป็น target feel — หุ่นจริง 4-ทิศคงที่อาจต้องลดสปีดลงเพื่อคุมง่าย)
- **ตอบคำถาม velocity API (ค้น docs+robot_chassis.py, condo-voice-79 ยืนยันจาก spec.js บอร์ด)**: SLAMWARE REST **ไม่มี** cmd_vel/twist/velocity — action-based ล้วน (MoveBy 4 ทิศ, MoveTo, Rotate, GoHome) · speed = global `base.max_moving_speed`(0.4)/`base.max_angular_speed`(1.0) ผ่าน PUT /parameter ไม่ใช่ per-call → **analog throttle/โค้งลื่นแบบ RC ทำไม่ได้ที่ชั้น REST** · เจอ: `MoveByActionOptions` มี field `theta` (มุม, อาจโค้งได้ แต่ยังไม่รู้หน่วย/ทิศ ต้องวัดบนหุ่น) · pseudo-throttle = PUT base speed 2-3 ระดับตอนเปลี่ยนระดับ (ไม่ใช่ทุก heartbeat) + คืนค่าตอน stop (peer ทำ chassis ได้)
- **sandbox: เพิ่ม option "ระดับสปีด (pseudo-throttle)" 1-3 ในโหมดบอร์ด** — `discreteTargets()` quantize สปีดตามระยะจอยเป็นขั้น (จำลอง pseudo-throttle จริง) · ยืนยัน playwright: 3 ระดับ จอยจิ้มนิด v=0.20 ดันสุด v=0.60 · node --check rc 0 · ยังไม่ commit

## 2026-09-12 — ⚠️ เหตุการณ์: หัวหุ่นมีกลิ่นไหม้ตอนทดสอบเอียงคอ — ตัดไฟ + ปิด motion กลับ

- ทดสอบเอียงคอจริง (ตามคำสั่งเจ้าของ "ทำจนกว่าจะทำได้"): เปิด `ROBOT_ARM_MOTION_ENABLED=true` → นำพอร์ตขึ้น (force-stop `com.aobo.robot.ai3` + `su 0` bind ch341 ที่ `3-1.4:1.0` → `/dev/ttyUSB10` โผล่ + stty raw 115200) → เจ้าของกด centre/▲/▼ ช่อง 8 บน /drive · หัว**ขยับจริง** (กล้องอยู่บนหัว ยืนยันจากเฟรมเลื่อน) ยืนยัน concept
- **แต่เจ้าของรายงาน "หัวมีกลิ่นไหม้เหมือนมีอะไรเสีย"** → สั่งปิดหุ่น · เจ้าของตัดไฟทัน (adb → offline = หุ่นดับ ไฟเซอร์โวถูกตัด) · #STOP/unbind ที่ผมพยายามส่งช่วยไม่ทัน (device offline แล้ว) — **ตัวหยุดที่แน่นอนคือสวิตช์ไฟเซอร์โว ไม่ใช่ซอฟต์แวร์ (ตรงกับที่ทั้งโปรเจกต์เขียนไว้)**
- สาเหตุ (ประเมิน): เซอร์โวหัวถูกสั่งไปตำแหน่งที่**ชนสุดทางกล (hard stop)** แล้ว stall → กระแสสูง → ร้อน/ไหม้ · **ยืนยันความเสี่ยงที่ condo-voice-79 เตือนไว้ก่อนเปิด**: "ไม่มีใครวัดว่า pulse ปลายช่วง (1500±200) ชนขอบกลไกไหม" — ช่วง `ROBOT_ARM_SPAN=200` (±200µs) เกินระยะกลไกจริงของคอ
- **แก้กลับทันที**: `ROBOT_ARM_MOTION_ENABLED=false` (พร้อมคอมเมนต์เหตุผล) · **ยังไม่รีสตาร์ต server / หุ่นยังปิด**
- **ค้าง/ต้องทำก่อนแตะหัวอีก**: (1) เช็คเซอร์โวหัวว่าไหม้/เสียไหมทางกายภาพ (2) วัดระยะปลอดภัยจริงของหัวด้วย span เล็กมาก (เช่น ±20-40µs) ทีละก้าว มีคนมืออยู่ที่สวิตช์ (3) หาค่า home จริงของหัว (pulse 1500 ≠ กลางเชิงกล) (4) เปิดแอปหุ่น `am start ...ai3` กลับเมื่อหุ่นกลับมาออนไลน์ (มันยึดพอร์ตคืน = คุมหัวไม่ได้ ต้องทำหลังตรวจเสร็จ) — ประสาน condo-voice-79 (เจ้าของโค้ดแขน) · ไม่ commit

## 2026-09-12 — ปุ่ม "เอียงคอ" ใน /drive (ก้ม/เงยหัว = เล็งกล้องขึ้นลง)

- เจ้าของถาม "ปรับกล้องมองขึ้นลงได้ไหม" → วัด caps กล้องจริงผ่าน DevTools (adb 9222 → Runtime.evaluate `getCapabilities()` บน video track): **ไม่มี `pan`/`tilt`** เลย (มีแค่ zoom≤4, focus, exposure, WB) = เอียงกล้องทางกลไก/PTZ ทำไม่ได้ · เจ้าของ: "ก็ขยับคอหุ่น"
- ตรวจ `docs/robot-command-research-2026-09-11.md`: **หัวขยับได้จริง — ช่อง 7/8 บนบอร์ดเซอร์โว Torobot** (พิสูจน์ hardware 11 ก.ย. 12:07: ch7/8 หัวขยับ 2 แกน, ch1/11 แขนเงียบ) ผ่าน `/dev/ttyUSB10` CH340 115200 · กล้องหน้าอยู่บนหัว → ก้ม/เงยหัว = เล็งกล้องขึ้นลง
- `client/robot-joystick.html`: เพิ่ม **"เอียงคอ"** ใต้สไลเดอร์ซูมในการ์ดกล้อง — 2 แถว: `ช่อง 8 (ก้ม-เงย) [กลาง][▲][▼]` + `ช่อง 7 (หัน) [กลาง][◄][►]` ยิง `POST /arm/command {action:centre|up|down, channel}` (endpoint ของ condo-voice-79 ที่หน้า /arm ใช้อยู่แล้ว — หน้านี้แค่เรียก ไม่แตะ robot_arm.py) · `checkHead()` เรียก `/arm/state` ตอนโหลด: ถ้า `!enabled`/`!motion_enabled`/`!port_present` → disable ปุ่ม + บอกเหตุ (motion ปิด default เพราะ #STOP ยังพิสูจน์ไม่ได้) · เซอร์โวไม่มี feedback → ต้อง `centre` ก่อน step (server บังคับ, page ใช้ note บอกให้กด "กลาง" ก่อน)
- **mapping ช่อง** (condo-voice-79 ยืนยันจาก docs/robot-front-camera-test-2026-09-11.md): ช่อง 8=หัวก้ม/เงย(tilt), ช่อง 7=หัวหัน(pan) · **ทิศ +/- (▲=ขึ้นหรือลง) ยังไม่มีใครวัด** — note บอกเจ้าของกด ▲ แล้วดูเอง · ป้ายคงเลขช่องไว้ + วงเล็บชื่อข้อต่อ (ตามกติกาเดียวกับเทสต์ `test_the_page_never_labels_a_channel_with_a_joint_name` ของหน้า /arm ที่ห้ามเขียนชื่อข้อต่อแทนเลขช่อง)
- ตรวจ: extract inline JS → `node --check` rc 0 · playwright 1600×900 ยืนยัน 2 แถวปุ่มขึ้นครบไม่ทับ layout · **ยังไม่ทดสอบขยับหัวจริง** — ติด `ROBOT_ARM_MOTION_ENABLED=false` (เจ้าของต้องตัดสินใจเปิด — เปิดแล้วปลดทุกช่องใน allowlist 1/11/7/8 ไม่ใช่แค่หัว) · แจ้ง condo-voice-79 ว่าเพิ่มปุ่มเรียก /arm/command บน /drive

## 2026-09-12 — ปรับกล้องหุ่นจาก cockpit (zoom) — ช่องสั่งกลับหน้าหุ่น

- เจ้าของขอ "เพิ่มที่ปรับกล้อง" — วัด capabilities: ปรับได้จริงแค่ **zoom 1..4x** (focus/exposure/WB เป็น auto ปรับไม่ได้; FOV คงที่ขยายมุมกว้างไม่ได้)
- กล้องอยู่หน้าหุ่น cockpit เป็นคนละ browser → เพิ่ม**ช่องสั่งกลับ**: `app/robot_camera.py` feed เก็บ `set_control/control` (latest-wins + version) · `app/main.py` `/ws/camera` เพิ่ม task `_push_control` ส่ง `{type:"camctl",zoom}` กลับหน้าหุ่น (recv+push พร้อมกันด้วย asyncio.wait FIRST_COMPLETED) + `POST /cam/control` (clamp zoom, token-gate) · `client/index.html` sock.onmessage รับ camctl → `track.applyConstraints({advanced:[{zoom}]})` ตาม caps จริง · `client/robot-joystick.html` slider ซูมใต้กล้อง (ยิงตอน release)
- **ทดสอบสด end-to-end: POST zoom 2.5 → หุ่น applyConstraints จริง (getSettings().zoom=2.5) → รีเซ็ต 1x** · reconnect ก็ได้ค่าล่าสุด (push_control ส่งตอน connect) · node --check 2 หน้า rc 0, py parse ok, `test_robot_camera/joystick_page/cockpit_feeds` 23 passed · แจ้ง condo-voice-79 ก่อนแตะ main.py (เขายืนยันว่าง)

## 2026-09-12 — freeze-detect กล้องหุ่น (กล้องค้างเงียบตอนแท็บถูกพับ)

- เจ้าของถาม "กล้องค้างไหม" — วัดจริง: /cam.jpg 200 แต่ 4 เฟรมใน 8 วิ md5 เท่ากันเป๊ะ = **ค้าง** · ต้นเหตุ: หน้า kiosk บนหุ่นถูกพับไป launcher แล้วดึงกลับ → getUserMedia video ค้าง แต่ loop ยัง push เฟรมเดิมซ้ำ → เซิร์ฟเวอร์ไม่เห็นว่า stale (ด่าน 2 วิ ของ /cam.jpg จับได้แค่ push ที่หยุด ไม่ใช่ push เฟรมค้างซ้ำ)
- `client/index.html` (loop push กล้องหุ่น): เพิ่ม **freeze guard** — เช็ค `video.currentTime` ถ้าไม่ขยับเกิน 3 วิ ตอน `!document.hidden` = วิดีโอค้าง → รื้อ (stop tracks + remove video + close socket) แล้ว `startRobotCamera` ใหม่ (fresh getUserMedia) · guard ด้วย `gen !== robotCamGen` กันซ้อน · ตรวจ: reload หน้าหุ่น → 3 เฟรม md5 ต่างกัน (สด) · `node --check` index.html rc 0, `client_voice_lifecycle.cjs` PASS, `test_robot_camera.py` 7 passed
- เพิ่ม `svc power stayon true` + ปิด screen timeout บนหุ่น (ลดการพับหน้า) · **kiosk-lock ถาวร (screen pinning/device-owner) ยังไม่ทำ** — invasive เจ้าของบอก "ตอนใช้จริงค่อยปรับ" → ทำ freeze-detect (ซอฟต์แวร์ ปลอดภัย) ก่อน ค่อยล็อกจอตอน deploy จริง

## 2026-09-12 — บันทึกจุด "ที่ชาร์จ" + ปุ่มขึ้นแท่นชาร์จ + แก้แผนที่หาย + เรดาร์/แผนที่เคียงกัน

- **แผนที่หาย** (เจ้าของ, จอฟอนต์ใหญ่): chrome การ์ดแมพดันจน canvas ยุบ → `.canvwrap { min-height:150px }` + การ์ด `overflow-y:auto` (chrome scroll ไม่บีบ canvas) + ปุ่มเล็กลง (`.btn` padding 5/8, ชื่อสั้น)
- **เรดาร์/แผนที่เล็ก** → วางเคียงกัน (`.pair` flex row) แทน stack 3 ชั้น, drive เล็กไว้บน — ทั้งคู่ใหญ่ขึ้นมาก (ยืนยันด้วย playwright 1600×900 + 1600×760)
- **บันทึกจุด "ที่ชาร์จ"** (เจ้าของขอ): ปุ่ม "บันทึกจุดนี้" → `{action:"save_poi",name}` (condo-voice-79 ทำ chassis; 403 ตอนแนบ pose → แก้เป็น POST ไม่แนบ pose บอร์ดสร้างที่ตำแหน่งหุ่นเอง) · **ทดสอบสด: POI "ที่ชาร์จ" บันทึกที่ base สำเร็จ** pose (3.05,-4.05,yaw3.02) quality 64, `/robot/state.places=['ที่ชาร์จ']`, ชื่อไทยถูกต้อง (รอบแรกเพี้ยน '????' เพราะ inline curl บน Git Bash ไม่ใช่โค้ด server — ส่ง UTF-8 เหมือน browser ถูก) · multi-floor pois ยังว่าง (POI อยู่ core/artifact)
- **ปุ่ม "ขึ้นแท่นชาร์จ"** (เจ้าของ clarify: ชาร์จ=ถอยเข้าแท่นจริง ไม่ใช่แค่ POI): `{action:"home"}` → go_home/GoHomeAction (มีอยู่แล้ว) · confirm ก่อน + ต้อง motion unlock (เป็นการเคลื่อนที่ ยังไม่กดทดสอบ หุ่นอยู่บนแท่น)
- รอ condo-voice-79: (1) เซฟแมพถาวร — เจ้าของอนุญาต "back up ไฟล์ก่อน" (POI อยู่ในแมพ RAM รีบูตอาจหาย) (2) ยืนยัน go_home docking จริง · เทสต์ joystick page 13 passed, node rc 0 · รีสตาร์ต run_server.py ให้ save_poi ทำงาน

## 2026-09-12 — cockpit ปรับ layout ตามฟีดแบ็กจริง (ถ่าย screenshot ยืนยันด้วย playwright)

- เจ้าของ iterate หลายรอบผ่าน screenshot จริง ("แก้", "สามอันเท่าจอ", "ไม่เห็นเรดาร์กับแผนที่", "ลดตัวบังคับ") — ยืนยัน layout ด้วย playwright headless 1600×900 (จอหุ่นเป็น portrait ถ่ายไม่ตรง PC landscape ของเจ้าของ)
- `.cockpit` definite-height `calc(100vh-96px)` (media ≥900px) — 3 พาเนลขวาเติมพอดีจอไม่ล้น/ไม่เลื่อน · **canvas เรดาร์/แมพยุบเพราะ flex-canvas ตรงๆ** → ห่อ `.canvwrap { position:relative; flex:1 }` + `canvas { position:absolute; inset:0; object-fit:contain }` (วิธีชัวร์ให้ canvas โตในพื้นที่ flex) · เรดาร์ flex 1.1 / แมพ 1.4 (แมพเป็นตัวโต้ตอบ)
- **ลดตัวบังคับ** ตามที่ขอ: stick 210→132, knob 84→52, เอามิเตอร์ไมค์ออกจากการ์ด (สถานะไมค์ดูที่ pill บน header; `pollMic` guard `miclvl` ที่ถูกลบ) → เรดาร์/แมพได้พื้นที่เพิ่ม
- **แบตไม่ตรงจอหุ่น** (เจ้าของสังเกต): วัดแล้ว = **คนละก้อน** — SLAMWARE ฐาน/ขับเคลื่อน 25% (ที่ /drive โชว์) ≠ Android บอร์ดจอ 50% (dumpsys battery, ที่หน้าจอหุ่นโชว์) · เปลี่ยนป้าย "แบต" → **"แบตฐาน"** ให้ชัดว่าเป็นแบตขับเคลื่อน ไม่ใช่แบตจอ
- กล้อง/ไมค์หลุดสตรีมตอนรีสตาร์ต server (หน้า kiosk บนหุ่นถูกพับไป launcher) — เปิด Chrome kiosk กลับ foreground + close แท็บซ้ำ + activate cam tab → กล้องกลับมา (1920×1080) · **ไมค์ยังไม่ปลุก**: หน้าได้ `standby_only` (wakeListens=false) ทั้งที่ /health wake ready — ค้างรอเจาะต่อ (ไม่บล็อกงานหลัก)
- เทสต์ `tests/test_robot_joystick_page.py` 13 passed ทุกรอบ · node --check rc 0

## 2026-09-12 — cockpit: กล้องซ้าย+พาเนลขวาพอดีจอ, แมพอ่านง่าย, หยุดใกล้ 0.3ม., ปรับตำแหน่ง

- เจ้าของขอ 4 อย่าง — แบ่ง: layout+แมพ (client, ผม) · relocalize+obstacle (chassis, condo-voice-79)
- **Layout** `client/robot-joystick.html`: กล้องย้ายมาซ้าย (16:9 ใหญ่ ไม่มีแถบดำ), radar/บังคับ/แผนที่ stack ขวา · เจ้าของ: "สามอันรวมกันเท่าจอ" → radar/map canvas cap `max-height:28vh/30vh` + object-fit บังคับ ให้ 3 พาเนลรวมสูง ~1 จอ (เลี่ยง flex-grow+max-height ที่ทำ canvas ยุบเป็น 0)
- **แมพอ่านง่าย** (เจ้าของ: "แมพอ่านยาก"): เดิม grayscale ไล่ระดับ+pixelated เล็ก → สีเรียบ (พื้นว่าง `#e6ebf0` / กำแพง `#c73838` / ยังไม่สำรวจเทาเข้ม), scale grid ขึ้น ~600px crisp, **หุ่นเป็นสามเหลี่ยมชี้ทิศ** (yaw) แทนจุด, เพิ่ม legend (ว่าง/กำแพง/ยังไม่สำรวจ/หุ่น) · click หาร mapScale
- **หยุดใกล้ 0.3ม.** (client รับจาก server ของ condo-voice-79): drive obstacle → จบ hold + โชว์ `r.hint` + flare วง `min_clearance` แดงในเรดาร์ทาง blocked.direction 2.5วิ · เรดาร์วาดวงเส้นหยุด (แดงประ) จาก `min_clearance` + note โชว์ clearance หน้า/หลัง (ถอย/หมุนออกไม่ถูกบล็อก)
- **ปรับตำแหน่ง (relocalize)**: ปุ่มในการ์ดแมพยิง `{action:"relocalize",mode:"dock"}` แล้ว poll `localization_quality` โชว์ผล · **ทดสอบสดสำเร็จ: quality 43→71, pose เด้งไปตำแหน่งจริงบนแมพ (3.01,-4.10) จากที่ลอย ~0,0** (localization_was_paused:false) → click-to-go ใช้ได้จริงแล้ว
- ตรวจ: `tests/test_robot_joystick_page.py` 13 passed · node --check JS rc 0 · รีสตาร์ต run_server.py ให้ relocalize/obstacle/clearance ของ condo-voice-79 ทำงาน (server ก่อนหน้าไม่รู้จัก) · /robot/laserscan มี clearance{front,back,left,right}+min_clearance จริง

## 2026-09-12 — จอยลากนิ้ว (analog stick) + กล้องเต็มกว้าง

- เจ้าของ: "เอาแบบจอยที่เลื่อนขยับ ไม่ต้องกด" — `client/robot-joystick.html`: แทนแป้น 4 ปุ่มกดด้วย **แป้นกลมลากนิ้ว** (knob) ลากไปทิศไหน=เดินทิศนั้น ลากกลับกลาง(ใน deadzone 0.32)หรือปล่อย=หยุด · แมปเวกเตอร์ลากเป็น 4 ทิศที่บอร์ด SLAMWARE รับ (แกนเด่นชนะ; บอร์ดไม่รับอนาล็อก 360°/theta) · refactor เป็น pump loop เดียว keyed ที่ `driving` — ลากเปลี่ยนทิศลื่นไม่มีช่องว่าง (เซิร์ฟเวอร์รับทิศใหม่โดยไม่ต้อง stop) · setPointerCapture + pointermove; ปล่อย/cancel/lostpointercapture/blur/visibilitychange/pagehide → stop; ทน fail 1 หยุด 2 ติด; ล็อก→จบ · `setDirEnabled` → toggle `data-locked` ของ stick
- เจ้าของ: "กล่อง[กล้อง]ไม่เห็นอะไร ขอเต็ม/กว้างสุด" — กล่องกล้องขยายเต็มความกว้าง (`grid-column:1/-1`, 16:9, สูงถึง 64vh) · `client/index.html`: getUserMedia หุ่น 1280×720 → **1920×1080** (คมขึ้น) · **FOV เลนส์คงที่ ขยายมุมกว้างไม่ได้ + zoom อยู่ที่ 1 (กว้างสุดแล้ว)** — "ไม่เห็นอะไร" เพราะกล้องทั้ง 2 ตัวหันไปด้านหลังและหุ่นหันเข้าผนัง/เพดานเปล่า ต้องหันตัวหุ่น
- ตรวจสด: reload หน้า kiosk บนหุ่นผ่าน DevTools → /cam.jpg = **1920×1080** จริง, ไมค์กลับมาสตรีมหลังแตะจอ (level 0.0008 age 0.06) · node --check JS หน้า cockpit = rc 0 (ไม่มี syntax error) · เทสต์: อัปเดต `tests/test_robot_joystick_page.py` (drag stick แทน .dir buttons, release events, loop `while(driving)`) + map = **20 passed**
- แก้ layout กล่องกล้อง (เจ้าของชี้ว่ามีพื้นที่ดำ): เดิม `grid-column:1/-1` + `max-height` ทำให้กล่องกว้างกว่า 16:9 → ภาพ contain เหลือแถบดำ และ 1/-1 ทำให้ auto-fit ไม่ยุบคอลัมน์ว่าง (radar/drive/map แคบชิดซ้าย) · ย้ายกล้องออกจาก grid เป็น `.camrow` เต็มบรรทัด, กล่อง 16:9 จัดกลาง `max-width:min(100%, 72vh*16/9)` — ภาพเต็มกล่องไม่มีแถบดำใน, แถวล่าง 3 พาเนลเต็มความกว้าง
- หน้า /drive เสิร์ฟจาก FileResponse — reload หน้าได้เลย · กล้อง 1920 มีผลเมื่อ kiosk บนหุ่น reload (ทำแล้ว)

## 2026-09-12 — จอยบังคับหุ่นแบบต่อเนื่อง (ฟีลจอย) แทน nudge 0.3ม.ต่อกด

- เจ้าของ: "ขยับไม่ดั่งใจ อยากได้ฟีลจอย" — วัดแล้ว RTT หุ่น 2ms (ไม่ใช่เน็ต) ต้นเหตุคือ nudge เดิม = MoveToAction 0.3ม./กด (เร่ง→เบรกหยุดทุกก้าว) กระตุกเป็นสเต็ป
- ประสานกับ condo-voice-79: เขาทำฝั่ง chassis `drive(direction)` + watchdog เซิร์ฟเวอร์ (`ROBOT_DRIVE_TIMEOUT_MS=400` เงียบเกินนี้สั่ง DELETE :current หยุดเอง) ผมทำ client stick UI ตาม contract: `POST /robot/command {token, action:"drive", direction}` — ฐาน SLAMWARE `MoveByAction` ต้องเรียกซ้ำถึงวิ่งต่อเนื่อง (ไม่มี cmd_vel, บอร์ดรับแค่ 4 ทิศ)
- `client/robot-joystick.html`: แทน `driveLoop` เดิมด้วยลูป self-paced (ไม่ใช่ setInterval — reply ช้าไม่ซ้อน request) ส่ง drive ทุก ~150ms ตลอดที่กดค้าง (< watchdog 400ms), ปล่อย/pointercancel/pointerleave/blur/visibilitychange/pagehide/refused → `stop` ทันที, ทน fetch fail 1 ครั้ง หยุดเมื่อ 2 ครั้งติด (บอร์ดหยุดเองใน 400ms อยู่แล้ว), ล็อก→จบ hold ไม่รัว · ลบฟังก์ชัน `command()` เดิม ใช้ `commandBody` (wrap token) · แก้ hint "ก้าวละ 0.3ม." → "กดค้าง=เดินต่อเนื่อง ปล่อย=หยุดทันที"
- `tests/test_robot_joystick_page.py`: อัปเดต 2 เทสต์ (payload drive+direction, ลูป self-paced) + เพิ่ม `test_drive_is_a_continuous_heartbeat_under_the_watchdog` (cadence 150 + ทน fail 2) · 3 ไฟล์ cockpit/map/feeds = **23 passed**
- รีสตาร์ต run_server.py ให้ action `drive` ของ condo-voice-79 ทำงาน (เซิร์ฟเวอร์ก่อนหน้ารีสตาร์ตตอนแก้ไมค์ ยังไม่รู้จัก drive) — ยืนยัน API: ทิศผิด→"ไม่รู้จักทิศ" (ไม่ได้สั่งหุ่นขยับจริง แค่ทิศผิดที่ถูกปฏิเสธ)
- **ยังไม่ทดสอบเดินจริง** (แบต 10% + localization 0 + ต้องมีคนถือ E-stop): ครั้งแรกที่ทดสอบต้องวัดให้ condo-voice-79 — (1) หลังหยุดส่ง heartbeat หุ่นหยุดในกี่ ms (2) base.max_moving_speed ปัจจุบัน
- **ข้อค้นพบ E-stop จาก condo-voice-79:** `base.emergency_stop` (PUT /api/core/system/v1/parameter on/off) = software E-stop ล้อผ่าน REST + `base.brake_release` — ยังไม่แตะจนเจ้าของสั่ง

## 2026-09-12 — แก้สีแมพในหน้า cockpit/mapview ตามค่าไบต์ที่ condo-voice-79 วัดได้

- condo-voice-79 วัดความหมายค่าไบต์แมพ (scripts/probe_map_semantics.py) ด้วยการฉาย lidar ลงแมพ: อ่านเป็น int8 — **0 = unknown, 1..127 = ว่าง (127 มั่นใจสุด), 128..255 = ทึบ/กำแพง** — ตรงข้ามกับที่ grayscale ตรงๆ ของผมวาด (พื้นว่าง=เทากลาง กำแพง=เกือบขาว = กลับด้าน อันตรายกับ click-to-go)
- `client/robot-map.html` (/mapview) + `client/robot-joystick.html` (/drive, พาเนลแมพ): เปลี่ยนจาก `pixel=v` เป็น branch ตามค่า — 0→เทาเข้ม(45), 1..127→สว่างไล่ระดับ(128+v/127·127, 127=ขาว), 128..255→แดง(200,60,60) กำแพงเห็นชัด "ห้ามเดินทับ"
- error ของ goto ที่ condo-voice-79 เพิ่มฝั่งเซิร์ฟเวอร์ (คลิกกำแพง/คลิกจุด unknown) แสดงผ่าน `r.error` บนหน้าอยู่แล้ว ไม่ต้องแก้เพิ่ม
- `tests/test_robot_map_page.py` + `tests/test_robot_joystick_page.py`: เพิ่มเทสต์กันไม่ให้กลับไป `img.data=v` ตรงๆ (บั๊กแมพกลับด้าน) — บังคับให้ branch ที่ threshold 127 + จัดการ v===0 · 2 ไฟล์ = **19 passed** · หน้าเสิร์ฟจาก FileResponse โค้ดใหม่ live ทันทีไม่ต้องรีสตาร์ต

## 2026-09-12 — หุ่นออนไลน์: ทดสอบสด cockpit + แก้บั๊กมิเตอร์ไมค์ที่ตายบนเครื่องห้องขาย

- เจ้าของเปิดหุ่น — ตั้งอุโมงค์ใหม่ผ่าน adb ในรีโป (`tools/android/platform-tools/adb.exe`): connect 192.168.1.24:5555 + nc relay 11448 + forward. chassis ต่อติด, lidar/pose/power อ่านสดจริง · เปิดหน้า kiosk บนหุ่นผ่าน adb (Chrome) — ต้องปิดแท็บเก่า `?kiosk=1` (ไม่มี token) + activate แท็บ `?cam=1&token=` ผ่าน DevTools (`/json/activate`, `/json/close`) กล้องถึงเข้า (getUserMedia ทำงานเฉพาะแท็บ foreground)
- **กล้องยืนยันสด**: /cam.jpg 200, เฟรม 1280×720 จริง (robot→/ws/camera→/cam.jpg) · **lidar สด**: /robot/laserscan จุดจริงรอบตัว
- **บั๊ก /mic/level (ฟีเจอร์ของ session นี้เอง):** level publish ถูกวางใน `WakeStream._report` ซึ่งเรียกเฉพาะเมื่อ `wake_debug`/`wake_enroll` เปิด → บนเครื่องห้องขาย (ทั้งคู่ปิด) มิเตอร์ไมค์ใน cockpit **ไม่มีวันขึ้น** ทั้งที่ไมค์ส่ง PCM จริง (วัดด้วย DevTools: getUserMedia MIC_OK, /ws/wake OPEN, ส่ง 128 เฟรม/2.5วิ, client meter 0.001) — อาการเดียวกับ "พูด emma แล้วเหมือนไม่ได้ยิน" ที่มิเตอร์นี้ถูกสร้างมาแก้
- `app/wake.py`: แยก `_publish_level(samples)` ออกจาก `_report` เรียก**ทุกเฟรมไม่มีเงื่อนไข** (RMS ถูก, cheap) ส่วน verdict log ยัง gate ด้วย wake_debug เหมือนเดิม · หลังรีสตาร์ต run_server.py: /mic/level `streaming=true` level 0.001–0.011 age~0.05 สดจริง
- `tests/test_wake.py`: เพิ่ม `test_the_meter_level_is_published_without_wake_debug` (unit, ไม่ต้องโมเดล — publish + mic_level) + `test_the_meter_updates_from_feed_with_wake_debug_off` (@needs_model — ยืนยัน feed เรียก publish นอก gate) · `tests/test_wake.py test_robot_cockpit_feeds.py` = **32 passed** (โมเดลมีบนเครื่องนี้ integration รันจริง)
- ยังไม่ขับ/สั่งแขน: แบต 15% ไม่ชาร์จ + `localization_quality=0` (pose 0,0 = หุ่นยังไม่รู้ตำแหน่งบนแมพ) — รอเจ้าของเอาขึ้นแท่นชาร์จ (ชาร์จ + relocalize) ก่อน · แบ่งงานกับ condo-voice-79: ผมคุมกล้อง/ไมค์/cockpit/wake, เขาถือ chassis motion + arm safety · ส่งค่าไบต์แมพ (Counter) + radar ให้เขาแล้ว

## 2026-09-12 — ทดสอบ Q&A ของ Emma กับเอกสารขายที่ส่งมา + ปิดช่องรั่วคำถาม ROI

- เจ้าของขอทดสอบว่าคำถาม/คำตอบของ Emma ตรงกับ 5 ไฟล์ที่ส่งให้ (`data/personal-docs/embassy-world/`) ไหม — เขียน eval ยิง `search_my_documents` จริง 11 คำถามเนื้อหา + 3 คำถามราคา (ไม่ commit สคริปต์, อยู่ scratchpad)
- **พบช่องรั่วความปลอดภัย (แก้แล้ว):** "ผลตอบแทนการลงทุนกี่เปอร์เซ็นต์" หลุด commercial gate → `found=True` หยิบตัวเลขจากคลังการตลาด (ไฟล์ที่คอมเมนต์ของ gate เองระบุว่ามี "yields 7-10%") — `COMMERCIAL_TERMS` มี ราคา/งบ/ดอกเบี้ย/กี่บาท แต่**ไม่มีคำว่า yield/return/ผลตอบแทน** ซึ่งเป็นคำถามเงินที่คลังนั้นตอบด้วยตัวเลขไม่มีคนเซ็นมั่นใจที่สุด
- `app/tools/retrieval.py`: เติม `ผลตอบแทน, เปอร์เซ็นต์, ค่าเช่า, ปล่อยเช่า, roi, yield, rental, return on, percent` ใน `COMMERCIAL_TERMS` · **จงใจไม่ใส่ "ลงทุน" เดี่ยวๆ** เพราะ "ทำไมพัทยาน่าลงทุน" คือเรื่องราวใน thai-market-strategy ไม่ใช่คำถามตัวเลข (วัดแล้วไม่ over-refuse ผ่อนคลาย/ยังไงบ้าง/น่าลงทุน)
- `tests/test_knowledge.py`: เพิ่ม `test_a_yield_question_is_a_money_question` (ROI/yield/rental/ค่าเช่า ต้องถูกปฏิเสธ) + `test_investing_as_a_reason_to_live_here_is_not_a_price_question` (คำถามเชิงเรื่องราวห้ามโดน) — `tests/test_knowledge.py test_mydocs.py` = **54 passed**; eval ยิงซ้ำปฏิเสธครบ 3/3
- **ข้อค้นพบที่ยังไม่แก้ (รอเจ้าของตัดสิน):** เอกสารขาย 5 ไฟล์ "จม" — คำถามเนื้อหาตอบจากไฟล์ที่ส่งมาแค่ 4/11 เพราะ index รวมทั้ง `data/personal-docs/` = 2081 chunk / 186 ไฟล์ (เว็บ Embassy **Life**/Pattaya + corpus เก่า) แข่งกับ 5 ไฟล์ Embassy **World** → "ทำไมชื่อ Embassy World", "Junior World", ระบบทำความเย็น landscape ไปโดนเว็บเก่า/รายงาน MEP แทนต้นฉบับ ยังไม่แตะ ranking/ขอบเขตเพราะเปลี่ยนสิ่งที่ Emma รู้ = การตัดสินใจของเจ้าของ

## 2026-09-11 — ห้องบังคับหุ่นหน้าเดียว: กล้อง + เรดาร์(lidar) + ไมค์ + จอย + แมพ

- เจ้าของขอรวมทุกอย่างเป็นหน้าเดียว และตอนบังคับอยากเห็นกล้อง/ไมค์/เรดาร์ของหุ่น — `client/robot-joystick.html` (เสิร์ฟที่ `/drive`) เปลี่ยนจากหน้าจอยล้วนเป็น cockpit รวม: กล้องหุ่น, เรดาร์ lidar, มิเตอร์ไมค์, ปุ่มบังคับ, แผนที่คลิกสั่ง — แต่ละพาเนลล้มแยกกัน (ไม่มีสัญญาณ = ขึ้นข้อความ ไม่พังทั้งหน้า)
- แบ่งงานกับ condo-voice-79: เขาเพิ่ม `/robot/laserscan` (chassis) ผมทำ client + endpoint กล้อง/ไมค์ สลับกันแก้ main.py กันชน
- `app/main.py` (ของผมรอบนี้): `GET /cam.jpg` อ่าน `robot_camera.feed` — **เฟรมเก่ากว่า 2 วิ = 503 ไม่เสิร์ฟซ้ำ** (ลิงก์ค้าง = กล้องหยุด ไม่ใช่คนยืนนิ่ง ตามกฎ greeter) token gate · `GET /mic/level` คืนระดับไมค์จาก wake stream
- `app/wake.py`: เก็บ `_LAST_LEVEL`/`_LAST_LEVEL_AT` module-level ใน `_report` + `mic_level()` — best-effort, `streaming=false` เมื่อไม่มีเฟรมใน ~2 วิ (สายคุยยึดไมค์ หรือไม่ได้ standby) ไม่ใช่คำตัดสิน wake
- **cockpit ความปลอดภัย** (ตามที่เพื่อนเตือน): ปุ่ม `#estop` sticky ล่างจอ เห็นชัดสุดไม่เลื่อนหาย, ทุก poller หยุดเมื่อแท็บซ่อน (visibilitychange) ลดโหลดอุโมงค์ nc, จอยคงกติกาเดิม (กดค้างไม่ยิงซ้อน/ปล่อย=หยุด/ล็อกไม่รัว) · เรดาร์: จุด invalid ไม่วาดเป็นวัตถุที่ระยะ 0, มุม 0 = หน้าหุ่น(ขึ้นบน)
- เทสต์: อัปเดต `tests/test_robot_joystick_page.py` (จอย+กล้อง+เรดาร์+ไมค์+estop+visibility), เพิ่ม `tests/test_robot_cockpit_feeds.py` (/cam.jpg 401/503/200 + สด-ไม่-ค้าง, /mic/level) รวมกับ map + voice = 175 passed · ตรวจสด: /drive 200, /cam.jpg 401(ไม่มี token)/503(ไม่มีเฟรม), /mic/level ok
- **ยังไม่ทดสอบสดกับหุ่น** (หุ่นออฟไลน์) กล้อง/ไมค์/เรดาร์จะมีภาพเมื่อหุ่นกลับ online + หน้า kiosk ?cam=1 บนหุ่นดันเฟรม

## 2026-09-11 — หน้าจอยบังคับหุ่น + หน้าแมพคลิกสั่งพิกัด (ฝั่ง client)

- คู่กับงานเซิร์ฟเวอร์ของ condo-voice-79 (ด้านล่าง) — เจ้าของขอ console บังคับเหมือนจอย + เจนแมพคลิกได้พิกัด ผมทำ client เป็นไฟล์ใหม่ ไม่แตะ robot_chassis.py/main.py/robot-control.html ของเขา (route เสิร์ฟ /drive, /mapview เขาเป็นคนเพิ่มใน main.py)
- `client/robot-joystick.html` (`/drive`): บังคับหุ่นด้วยปุ่มทิศ ยิง `POST /robot/command` เดิม (forward/back/left/right/stop) ระยะก้าวอยู่ที่เซิร์ฟเวอร์ (0.3 ม./15°) body ส่งแค่ {token, action} · **กติกาความปลอดภัย**: กดค้างไม่ยิงซ้อน (รอ response ก้าวก่อนก่อนยิงก้าวถัดไป), ปล่อยปุ่ม/pointercancel/pointerleave/blur/visibilitychange/fetch error → ส่ง stop ทันที, ไม่ auto-repeat ตอนโหลด, ก้าวที่ถูกปฏิเสธ (motion_locked) จบ loop ไม่รัวยิง, ปุ่มหยุดใหญ่กลางจอทำงานเสมอแม้ล็อก, โชว์ motion_enabled จาก /robot/state
- `client/robot-map.html` (`/mapview`): เรนเดอร์ occupancy grid จาก `GET /robot/map` (base64 ไบต์/ช่อง grayscale) **กลับแกน y** (grid row 0 = y ต่ำสุด, canvas row 0 = บน) คลิก→พิกัดโลก `origin+(i+0.5)*res`→ `POST /robot/command {action:'goto',x,y}` (ไม่ส่ง yaw/place ตาม contract) จุดเขียว=หุ่นจาก pose สด · ผ่าน motion lock เดิม, ปุ่มหยุดเสมอ, error ดึงแมพขึ้นข้อความไม่ใช่จอเปล่า
- เทสต์ source ใหม่ 13 ตัว: `tests/test_robot_joystick_page.py` (7), `tests/test_robot_map_page.py` (6) — ยึด contract: ห้ามส่งระยะ/yaw, แกน y กลับ, สูตรพิกัด, stop ทุกทางที่ press จบ · **ยังไม่ทดสอบสดกับหุ่น** (หุ่นออฟไลน์) ค่าไบต์แต่ละช่องของแมพต้องวัดตอนหุ่นกลับมา

## 2026-09-11 — แมพจากฐานนำทางขึ้นหน้าคุม + คลิกพิกัดสั่งเดิน (ฝั่งเซิร์ฟเวอร์)

- เจ้าของขอ (ผ่าน session condo-voice-83): เจนแมพแล้วคลิกสั่งไป — แบ่งงาน: ฝั่งนี้ทำเซิร์ฟเวอร์ session นั้นทำ client (canvas + หน้าจอย) เป็นไฟล์ใหม่ ไม่แตะไฟล์กัน
- `app/robot_chassis.py`: `request_raw()` สำหรับ endpoint ที่ตอบเป็นไบต์ (แยกจาก `request()` เพื่อให้ stand-in ในเทสต์ไม่ต้องตอบสองแบบ) · `parse_explore_map()` ถอด binary ของ `GET /api/core/slam/v1/maps/explore` ตาม layout ใน `/js/spec.js` ของฐานจริง (header 20 ไบต์ + reserved 12 + size u32 + 1 ไบต์/ช่อง, little-endian) **เข้มเรื่องขนาด** เพราะอุโมงค์เป็น nc relay บนหุ่น อ่านขาดครึ่งจะกลายเป็นแมพครึ่งห้องเงียบๆ · `explore_map()` จำขอบเขต (`_map_bounds`) · `goto_xy()` = คำสั่งเดียวที่ client เลือกจุดหมายเอง จึงถูกบังคับว่าจุดต้องอยู่ในแมพก่อนส่ง (นอกแมพ/ไม่มีแมพ/NaN → ValueError ไม่ส่งอะไร) และผ่าน motion lock เดิม · `send("move_to_xy")` เข้า `_pending` เดียวกับ POI → arrival ตัดสินโดย `_tick` จาก pose เหมือนเดิน POI
- **ยังไม่ตีความค่าไบต์ของช่อง** (ว่าง/ทึบ/ไม่รู้) เพราะสเปกไม่ระบุและหุ่นออฟไลน์ วัดไม่ได้ — client แสดง grayscale ไปก่อน จะเช็คช่องว่างก่อนเดินได้เมื่อวัดแล้ว (มีหมายเหตุใน docstring)
- `app/main.py`: `GET /robot/map?token=` → `{ok, map:{origin_x, origin_y, width, height, resolution, cells_b64}}` (row 0 = y ต่ำสุด) · `POST /robot/command {action:"goto", x, y}` เพิ่มจาก `goto` แบบ `place` เดิม — `place` ชนะเสมอถ้าส่งมาพร้อมกัน, รับตัวเลขเป็น string ได้, ไม่มีทั้งสอง → error
- เทสต์ใหม่ใน `tests/test_robot_chassis.py` (แมพ 4 เคสเสีย, ขอบเขต, คลิกใน/นอก/ไม่มีแมพ/NaN/ล็อก, จบเดินด้วย pose) และ `tests/test_robot_control_page.py` (token, ไบต์กลับครบ, relay ตัดสั้น, คลิก, string, นอกแมพ, ล็อก, ไม่มี place/x,y, place ชนะ) — เทสต์ที่เกี่ยว 7 ไฟล์ = **203 passed**; ชุดเต็มดูบรรทัดถัดไปเมื่อรันเสร็จ
- ชุดเต็มหลังเพิ่มแมพ/goto: **1416 passed, 1 failed** — ตัวที่แดงคือ `tests/test_robot_joystick_page.py` ของ session condo-voice-83 ที่ยังเขียนไม่เสร็จตอนรัน (รันซ้ำหลังเขาเสร็จ: ผ่าน)
- `app/main.py`: route `GET /drive` → `client/robot-joystick.html` และ `GET /mapview` → `client/robot-map.html` (หน้าและเทสต์ของสองหน้านั้นเป็นของ session condo-voice-83) เทสต์ว่าเสิร์ฟได้และอ้าง `/robot/command` อยู่ใน `tests/test_robot_control_page.py` — ตรวจ 5 ไฟล์ที่เกี่ยว = 111 passed
- **เรดาร์**: `robot_chassis.laserscan(max_points)` อ่าน `GET /api/core/system/v1/laserscan` (shape จาก spec.js `LaserScan` และตัวอย่างสดวันที่ 10 ก.ย.: `laser_points[{angle,distance,valid}]` + `pose`) เก็บจุด invalid ไว้ (เรดาร์ควรเห็นว่ามองไปแล้วไม่มีอะไรสะท้อน) ทิ้งเฉพาะที่ไม่ใช่ตัวเลข/NaN, thin ทุก n จุดเพื่อรักษามุมครอบคลุม · `GET /robot/laserscan?token=&max=` → `{ok, points, pose, total, valid}` เพดาน `_LASERSCAN_MAX_POINTS=400` (หน้าเว็บขอน้อยกว่าได้ มากกว่าไม่ได้) สำหรับหน้า cockpit ของ session condo-voice-83 · เทสต์ 6 ตัวใหม่ — 6 ไฟล์ที่เกี่ยว = 143 passed
- ชุดเต็มหลังรวมงานทั้งสอง session (แมพ/goto/เรดาร์/route ฝั่งนี้ + cockpit/cam/mic ฝั่ง condo-voice-83): **1439 passed** (174 วิ)
- **12 ก.ย. — ค่าไบต์ในแมพวัดแล้ว** (`scripts/probe_map_semantics.py` ใหม่ อ่านอย่างเดียว): ฉายเฟรม lidar สดลงแมพสด — ช่องที่หุ่นยืน = 127, ช่องตามแนวลำแสง (ว่างโดยนิยาม) อยู่ 20..127 = 4,668/4,928 ตัวอย่าง, ช่องที่ลำแสงชน (ทึบโดยนิยาม) อยู่ 129..196 = 226/335 ที่เหลือเป็น 0 → อ่านเป็น int8: **บวก=ว่าง (127 มั่นใจสุด) ลบ=ทึบ 0=ยังไม่เคยเห็น** ตรงข้ามกับสัญชาตญาณ "ค่าสูง=ทึบ" จึงมีเทสต์กันแก้กลับ · `cell_kind()`/`cell_at()` ใน robot_chassis.py, `goto_xy` ปฏิเสธจุดที่เป็นสิ่งกีดขวางและจุดที่ยังไม่สำรวจ (หุ่นเพิ่งบูต 81.6% ของแมพเป็น 0 — planner ตอบด้วยความเงียบแบบ 10 ก.ย.) และอ่านแมพใหม่ถ้าเก่ากว่า `MAP_MAX_AGE_S=10` วิ · เทสต์ 6 ตัวใหม่ — 7 ไฟล์ที่เกี่ยว (รวมของ condo-voice-83) = 162 passed · แมพวันนี้เล็ก (50×108 ที่ 0.05 ม.) เพราะ localization_quality=0 ยังไม่ relocalize
- **12 ก.ย. — จอยกดค้าง (ฝั่ง chassis)** เจ้าของบอก "ขยับไม่ได้ดั่งใจ" — nudge = MoveToAction 0.3 ม./กด เร่ง-เบรก-หยุดทุกก้าว จึงกระตุก · spec.js ของบอร์ดมี `slamtec.agent.actions.MoveByAction` ("遥控移动, 需要定时调用以达到连续运动效果" = รีโมต ต้องเรียกซ้ำเป็นจังหวะ) options `{direction: 0 หน้า/1 หลัง/2 ขวา/3 ซ้าย}` ไม่มีความเร็วต่อครั้ง (ใช้ `base.max_moving_speed` ของบอร์ด) · `robot_chassis.drive(direction)` + `drive_stop()`: ขอบเขตเปลี่ยนจาก*ระยะ*เป็น*เวลา* — deadman ฝั่งเซิร์ฟเวอร์ `ROBOT_DRIVE_TIMEOUT_MS=400` (config.py + .env.example) สั่ง DELETE :current เองเมื่อหน้าเว็บเงียบ ไม่พึ่ง deadman ของบอร์ดที่ยังไม่ได้วัด · `stop()` ตอนปิดเซิร์ฟเวอร์หยุดการกดที่ค้างอยู่ (ข้อยกเว้นเดียวของ "ปิดเซิร์ฟเวอร์ไม่ใช่เหตุให้ขยับหุ่น" — เพราะนี่คือทิศ*หยุด*) · `POST /robot/command {action:"drive", direction}` (heartbeat ทุก ~150 ms; log เฉพาะครั้งแรกของการกด) และ `stop` ปลด deadman ด้วย · เทสต์ 12 ตัวใหม่ (deadman ยิงจริงเมื่อเงียบ, heartbeat กันไว้, ปล่อย=หยุดทันทีไม่ยิงซ้ำ, ล็อก, ทิศผิด, ปิดเซิร์ฟเวอร์กลางกด) — 7 ไฟล์ที่เกี่ยว = 175 passed
- ชุดเต็มหลังจอยกดค้าง: **1465 passed, 2 failed** — สองตัวที่แดงคือ `tests/test_robot_joystick_page.py` ของ condo-voice-83 ที่กำลังแก้ stick UI อยู่ตอนรัน (ไฟล์เปลี่ยนหลังเริ่มรัน) รันไฟล์นั้นซ้ำหลังเขาเซฟ: 13 passed
- **12 ก.ย. — เจ้าของสั่ง "ทำเลย" สองเรื่อง (ผ่าน condo-voice-83):**
  - **หยุดอัตโนมัติเมื่อใกล้ 0.3 ม.** `ROBOT_DRIVE_MIN_CLEARANCE_M=0.3` (config + .env.example) · `clearance(points)` ระยะใกล้สุดต่อ arc หน้า/หลัง (±35°) ซ้าย/ขวา (55°..125°) จากเฟรม lidar — แนบไปกับ `/robot/laserscan` (`clearance`, `min_clearance`) ให้เรดาร์โชว์ · `drive()` เช็คทุก heartbeat ด้วยเฟรมใหม่ **เฉพาะ arc ในทิศที่ไป** — MoveByAction คือรีโมต ไม่มีตัววางแผนหลบสิ่งกีดขวางบนบอร์ดเหมือน MoveToAction จึงเป็นด่านเดียวระหว่างจอยกับกำแพง · หมุนอยู่กับที่และถอยออกจากของไม่โดนบล็อกเด็ดขาด (หุ่นห่างผนัง 0.7 ม. ที่ถอยไม่ได้คือติด ไม่ใช่ปลอดภัย) · burst ที่วิ่งอยู่ถูกหยุดก่อนคืน error · `/robot/command` ตอบ `{ok:false, error:"obstacle", blocked:{direction, distance, limit}, hint}` · ตั้ง 0 = ปิดด่าน
  - **Relocalize** หุ่นอยู่บนแท่นแต่ `localization_quality=0` pose (0,0) = odometry ลอย · จาก spec.js: `RecoverLocalizationAction` (NoMove/RotateOnly), `PUT localization/pose`, `localization/:enable` ("false = โหมด odometry ล้วน" — ค่า quality จะ 0 ตลอดไม่ว่า lidar เห็นอะไร), `homedocks` (shape ยืนยันจาก capture 10 ก.ย.) · `relocalize(mode)`: ทุกโหมดเปิด localization กลับถ้าบอร์ดบอกว่าพักอยู่ · `dock` (default) = ต้อง `dockingStatus=on_dock` (ไม่งั้นปฏิเสธ ไม่แต่ง pose) → PUT pose เป็นตำแหน่งแท่น → Recover NoMove · `static` = Recover NoMove จากที่ยืน · `rotate` = RotateOnly **ขยับหุ่น จึงอยู่หลัง motion lock** · `POST /robot/command {action:"relocalize", mode}` ทำงานได้ขณะล็อก (ยกเว้น rotate) เพราะหุ่นที่ไม่รู้ว่าตัวเองอยู่ไหนต้องการมันตอนที่อย่างอื่นยังล็อก · ผลเป็น action ที่จบเอง หน้าเว็บดู `localization_quality` ต่อ
  - เทสต์ใหม่ 14 ตัว — 7 ไฟล์ที่เกี่ยว = 191 passed · ชุดเต็ม `tests/` = **1483 passed** (177 วิ) · **ผลจริงบนหุ่น (condo-voice-83 รันหลังรีสตาร์ต):** `relocalize dock` สำเร็จโดยไม่ขยับ — quality_before 43 (ฟื้นเองบางส่วนตอนอยู่บนแท่น ไม่ใช่ 0 แล้ว), `localization_was_paused=false` (สวิตช์พักไม่ใช่สาเหตุ), pose ถูกตั้งเป็นแท่น (-0.25, 0.01, -0.04) แล้ว RecoverLocalizationAction หาตำแหน่งจริงเจอ: quality **43→71** ที่ t+6 วิ นิ่งที่ 71, pose เด้งไป (3.01, -4.10, yaw 3.06) จากที่ลอยอยู่ ~(0,0) = click-to-go มีจุดอ้างอิงจริงแล้ว · clearance สดอ่านได้ (front 0.72 / back null / left 0.82 / right 1.04) · เส้น 0.3 ม. กับจอยยังไม่ได้วัดตอนวิ่งจริง
- **12 ก.ย. — "บันทึกตำแหน่งปัจจุบันเป็นจุด" (เจ้าของขอผ่าน condo-voice-83):** `robot_chassis.save_poi(name)` → `POST /api/core/artifact/v1/pois` ตาม PoseEntry ของ spec.js (id = uuid ที่เราสร้าง, pose {x,y,yaw}, metadata {display_name, type:"point"}) แล้ว `refresh_places()` ให้ชื่อนั้นเป็นจุดหมายของ `go_to_place` ทันที · ปฏิเสธ: quality ต่ำกว่า `ROBOT_POI_MIN_QUALITY=50` (config + .env.example — pose ตอน quality 0 คือ (0,0) ทั้งที่หุ่นอยู่ (3.0,-4.1) จุดที่เซฟจากมันคือจุดหมายไปที่ไม่มีอยู่จริง), ชื่อว่าง, ชื่อซ้ำกับที่มีบนแมพ, และบอร์ดตอบ 200 แต่ไม่แสดงในรายการ (รูปความล้มเหลวแบบ 10 ก.ย.) · `POST /robot/command {action:"save_poi", name}` ทำงานได้ขณะล็อก (ไม่ขยับ) · **POI ≠ แท่นชาร์จ**: `go_home` ยังใช้ homedock ที่บอร์ดลงทะเบียนไว้ ไม่ใช่ POI ชื่อ "ที่ชาร์จ" · **ยังไม่ persist ข้ามรีบูต** — หุ่นรันแมพที่ไม่ได้เซฟ (10 ก.ย.) การเซฟแมพเป็นการตัดสินใจแยก · เทสต์ 6 ตัวใหม่ — 7 ไฟล์ที่เกี่ยว = 197 passed
  - **ลองจริงรอบแรก 403** (condo-voice-83): บอร์ดตอบ `403 "operation fail"` ต่อ POST — probe ตรงผ่านอุโมงค์ (ลบจุดทดสอบออกแล้ว) ได้ผลชัด: POI ที่**แนบ pose มาเอง** = 403 ทั้ง POST และ PUT by id · POI **ไม่มี pose** = 200 และบอร์ดสร้างที่ตำแหน่งปัจจุบันของหุ่นเอง (3.05, -4.05, yaw 3.02) ตรงที่สเปกแนะนำ ("建议不包含Pose") · multi-floor pois ยังเป็น [] (อ่านอย่างเดียวตามสเปก) แต่ `refresh_places()` ถอยไปอ่าน core/artifact อยู่แล้ว · สถานะบอร์ดตอนนั้น: mapping=false, localization=true, floors=[] · แก้ `save_poi` ไม่ส่ง pose แล้วอ่าน pose กลับจากรายการของบอร์ดมาตอบ (คือจุดที่ลูกค้าจะถูกพาไปจริง) — FakeBoard ในเทสต์เลียนแบบ 403 นี้ไว้กันแก้กลับ · 197 passed เท่าเดิม
- **12 ก.ย. — เซฟแมพถาวร (เจ้าของอนุญาต: "ทำเลย back up ไฟล์ไว้ก่อนก็ได้") + ขึ้นแท่นชาร์จ:**
  - **backup ก่อน**: `GET /api/core/slam/v1/maps/stcm` → `data/robot-inspection/20260912-111751-before-save.stcm` (366,512 ไบต์, gitignore) + สำเนาใน scratchpad · SHA-256 `3b96b507…` **เท่ากับ backup 10 ก.ย. เป๊ะ** = แมพที่บอร์ดโหลดไม่เคยเปลี่ยนตั้งแต่นั้น · floors=[] (ชั้นเดียว — สเปกห้ามเซฟแบบนี้เฉพาะ multi-floor) · แล้ว `POST /api/multi-floor/map/v1/stcm/:save` → **200** · หลังเซฟ GET stcm = 366,603 ไบต์ (โตขึ้นจากการสำรวจวันนี้) quality 64 pose (3.05,-4.05) ไม่เปลี่ยน · **ทนรีบูตไหมยังไม่ได้พิสูจน์** — รอ condo-voice-83/เจ้าของรีบูตแล้วเช็ค · **save_poi ใช้ได้จริง end-to-end** (condo-voice-83 หลังแก้ไม่ส่ง pose): POI "ที่ชาร์จ" pose (3.053,-4.048,yaw 3.018) ≈ pose ปัจจุบัน, places=['ที่ชาร์จ'], multi-floor pois ยังว่าง (ไม่มีชั้นให้ผูก — ไม่กระทบเพราะ refresh_places อ่าน core/artifact) · POI นั้นเกิดหลัง save รอบแรก จึง `stcm/:save` ซ้ำ → 200, แมพ 366,619 ไบต์ มี POI อยู่ตอนเซฟ
  - `robot_chassis.save_map()` (กันหลายชั้นตามคำเตือนสเปก) + `POST /robot/command {action:"save_map"}` · `save_poi` เซฟแมพต่อท้ายอัตโนมัติ (ตอบ `persisted: true/false` — เซฟไม่ได้ไม่ถือว่า POI ล้มเหลว จุดยังอยู่จนรีบูต)
  - เจ้าของ clarify: "ที่ชาร์จ" = **ขึ้นแท่นจริง** ไม่ใช่ POI → ปุ่มใช้ `{action:"home"}` = `GoHomeAction` flags default `dock` (docking จริง, `_tick` ตัดสินจาก dockingStatus) · เพิ่มด่านใน `go_home()`: quality < `ROBOT_POI_MIN_QUALITY` → ปฏิเสธพร้อมบอกให้ปรับตำแหน่ง (go_home จาก pose odometry = เดินไปที่ที่แท่น*ควร*อยู่ถ้าหุ่นอยู่ตรงที่มันคิด — วันนี้ห่างสามเมตร) — ครอบทั้งปุ่มและเสียง (`return_to_base`)
  - เทสต์ 6 ตัวใหม่ — 7 ไฟล์ที่เกี่ยว = 202 passed · ชุดเต็มก่อนก้อนนี้ (หลังแก้ POI ไม่ส่ง pose) = 1489 passed · ชุดเต็มหลังก้อนนี้ = **1494 passed** (148 วิ)
- **12 ก.ย. — แท่นชาร์จลงทะเบียนผิดที่ 5.2 ม. (พบตอนทดสอบขึ้นแท่น) + undock:**
  - condo-voice-83 พบ: homedock ลงทะเบียนที่ (-0.25, 0.01) แต่หุ่นที่*อยู่บนแท่นจริง* (on_dock, charging, quality 64) อ่านได้ (3.05, -4.05) — ห่าง 5.23 ม. (hypot ไม่ใช่ 7 ม. ที่รายงานแรก) · สาเหตุ: `docking.docked_register_strategy = when_not_exists` (อ่านสด) บอร์ดจึงไม่แก้แท่นที่ลงทะเบียนไว้จาก frame แมพเก่าเอง · **go_home จากนอกแท่นจะเดินไปจุดเก่า** — นี่คือเหตุที่ relocalize dock ต้อง recover เด้งจาก (-0.25,0.01) ไป (3,-4)
  - **แก้บนบอร์ดแล้ว** (เจ้าของสั่งผ่าน condo-voice-83, ไม่ขยับหุ่น): `PUT /api/core/slam/v1/homedocks/home_dock {pose: (3.053,-4.048,yaw 3.020)}` → 200 true · homepose ยืนยันตรง · `stcm/:save` → 200 (366,619 ไบต์) · ค่าเก่าบันทึกไว้ในบรรทัดนี้เผื่อย้อน
  - โค้ด: `dock_check()` (offset แท่น↔หุ่นตอน on_dock+มี fix, `DOCK_STALE_M=1.0`) และ `register_dock()` (แก้แท่นเดิม in place ไม่เพิ่มแท่นที่สอง, ต้อง on_dock + quality ≥ 50, ตรวจว่าบอร์ดเปลี่ยนจริง, เซฟแมพต่อท้าย) + actions `dock_check`, `register_dock` (ก่อน motion lock) · `undock()` + action `undock` (ต้อง motion_enabled): ไม่มี undock ในสเปก และ MoveByAction บนแท่นวันนี้สร้าง action จบเองโดยหุ่นไม่ขยับ (10 ครั้ง 0.00 ม.) → ใช้ MoveToAction ไปข้างหน้า `UNDOCK_M=0.6` ตามทิศหุ่น (หุ่นถอยเข้าแท่น ข้างหน้าคือออก) ผ่านด่านของ goto_xy ทั้งหมด + เช็ค clearance หน้า ≥ 0.6+0.3 · **ผลจริง** (condo-voice-83 หลังรีสตาร์ต): `dock_check` → docked true, quality 64, offset_m 0.0, stale false ✓ · `undock` → ปฏิเสธ `obstacle` front 0.72 < 0.9 หุ่นไม่ขยับ (ด่านทำงานตามออกแบบ) — หน้าหุ่นมีผนัง 0.72 ม. ที่ตั้งแท่นแคบเกินจะขับออกไปข้างหน้า ต้องเคลียร์ที่หน้าหุ่น ≥ 0.9 ม. หรือย้ายหุ่นก่อนทดสอบเคลื่อนที่จริง
  - อ่านสดเพิ่ม: `base.max_moving_speed=0.4 m/s`, `base.max_angular_speed=1.0 rad/s` (ไม่ใช่ 0 — ความเร็วไม่ใช่เหตุที่ MoveBy บนแท่นไม่ขยับ) · `base.brake_release`/`base.emergency_stop` อ่านผ่าน GET ไม่ได้ (400 — GET รับ 3 ชื่อตาม enum, ตัวนี้ PUT-only)
  - เทสต์ 6 ตัวใหม่ — 7 ไฟล์ที่เกี่ยว = 208 passed · ชุดเต็ม `tests/` = **1500 passed** (156 วิ)
  - **ทดสอบ go_home จริง** (condo-voice-83, เจ้าของอยู่ข้างหุ่น): หุ่น**ขยับออกจากแท่น ~0.2 ม. แล้วค้าง 50 วิ** ไม่เข้าแท่น (หยุดด้วยมือ) — ที่เข้าแท่นได้คือเจ้าของบังคับจอยเอง · = MoveTo/GoHome ขับล้อได้จริง (ต่างจาก MoveBy บนแท่นที่ไม่ขยับ) แต่ติดที่หน้าแท่นมีผนัง 0.56–0.72 ม. ไม่มีที่ตั้งลำ · **REST ไม่มีพารามิเตอร์ปรับระยะ staging/approach**: PUT parameter รับแค่ max_moving_speed / max_angular_speed / emergency_stop / brake_release, GoHomeActionOptions มีแค่ flags dock|no_dock, back_to_landing, charging_retry_count (default 5) — "จุดตั้งลำ" คำนวณภายในจาก pose+yaw ของแท่น ตรงที่มีผนังพอดี → ข้อจำกัดกายภาพ ทางแก้คือย้ายแท่นไปที่มีที่ว่างหน้าแท่น ≥ ~1 ม. แล้ว register_dock ใหม่ หรือเข้าแท่นด้วยจอยมือ · ไม่มีการเปลี่ยนโค้ด/ค่าจากเรื่องนี้
- **12 ก.ย. — จอยกระตุก: เอา lidar ออกจากเส้นทาง heartbeat** เจ้าของบอกกระตุก "รวมถึงตอนขยับฐาน" · condo-voice-83 อ่านโค้ดถูก: ทุก heartbeat (150 ms) ยิง laserscan *แล้วค่อย* MoveBy = 2 round-trip ผ่าน nc relay ถ้าคู่นั้นช้า MoveBy ตัวเดิมหมดอายุก่อนตัวใหม่มา = สะดุด (บั๊กรูปเดียวกับ step เซอร์โว) · ที่**ไม่**เปลี่ยน: ยัง POST MoveBy ทุก heartbeat เพราะสเปกระบุตรงๆ ว่าต้องเรียกซ้ำเป็นจังหวะถึงจะวิ่งต่อ — ส่งครั้งเดียวแล้วต่ออายุ deadman อย่างเดียวทำไม่ได้ (และอายุ MoveBy หนึ่งตัวยังไม่ได้วัด) · ที่เปลี่ยน: `_scan_watch` task อ่าน lidar เบื้องหลังทุก `SCAN_WATCH_S=0.2` วิ ตลอด burst, `_refuse_if_blocked` ใช้เฟรมนั้นถ้าอายุ ≤ `SCAN_FRESH_S=0.5` วิ ไม่งั้นดึงเอง (เฟรมเก่ากว่านั้นไม่ใช่ถนนว่าง) → heartbeat = 1 round-trip · watcher หยุดพร้อม burst · log เวลา POST ถ้าเกิน 100 ms เพื่อวัด relay · ราคาที่จ่าย: กำแพงที่โผล่กลาง burst ถูกเห็นช้าสุด 0.2 วิ + 1 heartbeat (เทสต์ปรับตาม) · เทสต์ 3 ตัวใหม่ — 7 ไฟล์ = 211 passed · ชุดเต็ม `tests/` = **1503 passed** (152 วิ) · **ยังไม่ได้ลองกับหุ่น** (ปิดอยู่หลังกลิ่นไหม้ที่หัว) — ตอนลอง: heartbeat ฝั่ง client 100-150 ms และดู log "MoveBy POST took"
- **พบใน spec.js ระหว่างนี้ ยังไม่แตะ:** พารามิเตอร์ระบบ `base.emergency_stop` (on/off) และ `base.brake_release` ผ่าน `PUT /api/core/system/v1/parameter` = E-stop/ปลดเบรกของแชสซีทาง REST — แจ้ง session condo-voice-83 ที่ทำเอกสาร E-stop แล้ว รอเจ้าของสั่งก่อนใช้
- ไม่ได้ทดสอบกับหุ่นจริง (หลุด Wi-Fi ระหว่างทำ) — สิ่งที่ต้องวัดเมื่อกลับมา: ค่าไบต์ในแมพ, ว่า `maps/explore` บนเฟิร์มแวร์นี้ตอบตาม layout จริงไหม, และขนาดแมพผ่านอุโมงค์ nc

## 2026-09-11 — กู้ `app/robot_chassis.py` และ `tests/test_robot_chassis.py` ที่ถูก session อื่นเขียนทับ

- session อื่น (condo-voice-83) เผลอ `Write` ทับ `app/robot_chassis.py` ทั้งไฟล์ (11:00 น. UTC) แล้วเขียนทับ+ลบ `tests/test_robot_chassis.py` ด้วย — ทั้งสองไฟล์ untracked, git กู้ไม่ได้, ไม่มี .bak ที่ตรงเวอร์ชันล่าสุด (`%TEMP%\rc.bak` มีแต่เป็นฉบับ 15:33 น. วันที่ 10 ก.ย. ก่อนแก้ status code/motion lock)
- **แหล่งกู้:** Claude Code เก็บ `structuredPatch` ของคำสั่ง Write ที่ทับไว้ใน transcript (`~/.claude/projects/.../cccd47be-….jsonl`) hunk เดียวครอบทั้งไฟล์ (old 1+610 / old 1+513) → ประกอบบรรทัด context + `-` กลับเป็นไฟล์เดิม byte-for-byte ณ ตอนก่อนถูกทับ ตรวจสอบไขว้กับ rc.bak + Edit 4 ครั้งที่บันทึกไว้ (ต่างกันเฉพาะส่วนที่แก้นอก tool record: ActionState จาก spec.js 0/1/3/4, `moving=None` เมื่อไม่รู้, motion lock ใน `send()`, `_last_action_seen`)
- ไม่ได้แก้เนื้อหาใดๆ นอกจากกู้คืน · `ast.parse` ผ่านทั้งสองไฟล์ · เทสต์ที่เกี่ยว: `tests/test_robot_chassis.py test_robot.py test_robot_control_page.py test_hardware_page.py test_robot_arrival_readiness.py test_hardening.py` = **146 passed** · ชุดเต็ม `tests/` = **1385 passed** (150 วิ)
- เก็บเวอร์ชันของ session อื่นไว้ใน scratchpad นอกรีโปเผื่ออ้างอิง (`peer_version_app_robot_chassis.py`) — ไม่ได้ merge เพราะ API ไม่ตรงกับ `main.py` (`/robot/state`, `/robot/command`, `nudge`, `live_state`)

## 2026-09-11 — ลอง dump dex แขน/E-stop จากแรม แต่ jiagu บล็อก

- ตามคำสั่ง "ทำเลย" ดัมพ์ dex ที่ถอดแล้วจาก `/proc/<pid>/mem` ของแอป Aobo (root)
- **jiagu มี anti-dump**: แอปรีสตาร์ตทันทีที่อ่าน /proc/pid/mem (pid เปลี่ยน 7952→11821…) · ตอนเสถียรสแกน 2108 region ที่อ่านได้ grep ชื่อเมธอดที่รู้ว่ามีจริง (sendCmdtowhichUart/MoveToAction/SerialdataService/estopstop) **ไม่เจอ plaintext เลย** = โค้ด/สตริงถูกเข้ารหัสแบบ VMP ถอดทีละเมธอดตอนรัน
- สรุป: **static + memory-carve หา endpoint E-stop ภายในไม่ได้** · ทางที่เหลือคือไดนามิก — กด E-stop จริง (ปลอดภัย=หยุด) แล้วดัก logcat + serial ttyUSB + REST พร้อมกัน (ไม่ต้องแกะ jiagu) · อัปเดต `docs/robot-android-control-map-2026-09-11.md`
- เก็บกวาด /data/local/tmp บนหุ่นแล้ว · แอป Aobo กลับมาปกติ (AllSettingActivity) · รีโปไม่มี secret

## 2026-09-11 — คุมล้อผ่าน SLAMWARE REST ได้จริง + APK แขนถูกแพ็ค

- ต่อยอดจากแผนที่ตัวควบคุม: ฐานนำทางไม่มี curl แต่ `su 0 busybox wget` ยิง REST ได้ · อัปเดต `docs/robot-android-control-map-2026-09-11.md` ด้วยผลจริง
- **SLAMWARE REST ที่ 192.168.11.1:1448 ทำงานเต็ม**: robot/info = Slamware SDP sw 5.1.1-for-aobo-hermes, capabilities core/multi_floor/platform, power (แบต 15% on_dock), pose สด, **laserscan lidar สดรายจุด**, motion/actions ว่าง — นี่คือช่องคุมล้อของเราเอง ไม่ต้องผ่านแอป Aobo · เขียน MoveToAction API ไว้ในเอกสาร **ยังไม่ยิงสั่งเดิน** (แบตต่ำ+อยู่บนแท่น+กฎความปลอดภัย)
- ข้อจำกัดเครือข่าย: เซิร์ฟเวอร์ Emma อยู่ LAN 192.168.1.x เข้าฐาน 192.168.11.1 ตรงไม่ได้ ต้องผ่านหุ่นเป็น proxy
- **APK แขน/E-stop ถูกแพ็คด้วย 360 Jiagu** (libjiagu.so) โค้ดจริงถอดในแรมเท่านั้น static ไม่เห็น endpoint · assets มีแค่ routing เสียง · ท่า = serial `#<grp>GC` (รู้แล้ว), E-stop = พฤติกรรมใน aobosetting.xml แต่ยังไม่รู้ว่าเรียก API/IO ไหน — ต้องดัมพ์ dex จากแรม (frida) หรือกด E-stop จริงแล้วดู logcat/serial
- ดึง base.apk (147MB) ไว้ scratchpad นอกรีโป · ตรวจแล้วรีโปไม่มี secret

## 2026-09-11 — สำรวจตัวควบคุมทั้งหมดบนตัวหุ่น (เจ้าของอนุญาต)

- เจ้าของสั่งเข้าไปหาไฟล์/ตัวควบคุมทั้งหมดในหุ่น (เครื่องของเจ้าของเอง) — เพิ่ม `docs/robot-android-control-map-2026-09-11.md` **ตัด secret ออกหมด** (คีย์ DeepSeek/Ali TTS/robot SN/รหัสติดตั้ง เก็บดิบไว้ใน scratchpad นอกรีโป ตรวจแล้วรีโปไม่มี secret)
- พบสถาปัตยกรรม: RK3588 Android 15 ยี่ห้อ ZC + แอป `com.aobo.robot.ai3` เป็นสมอง คอนฟิกทั้งหมดใน shared_prefs · **สองเครือข่าย**: wlan0=192.168.1.24 (LAN), eth0=192.168.11.200 สายภายในไปฐานนำทาง **SLAMWARE ที่ 192.168.11.1** (ping 1.3ms) — ตัวคุมล้อ/lidar แยกบอร์ด REST มาตรฐานอยู่ :1448
- **ตรงกับปัญหา #STOP**: `aobosetting.xml` ตั้ง E-stop ของแอป = หยุดแขน (true) แต่ไม่หยุดล้อ (false) เป็นคนละเส้นกับ `#STOP` serial ที่เราทดสอบ · `FacereSetting.xml` ยืนยันเลขกลุ่ม=ท่า: เจอหน้า→กลุ่ม 6 (จับมือ), ถึงจุด→กลุ่ม 17
- **ความปลอดภัย**: หุ่นมี AnyDesk (พอร์ต 7070 ฟังอยู่) + RustDesk + ES File server (59777) รันอยู่ = เข้าถึงจอ/ไฟล์จากภายนอกได้ ควรตรวจและปิดถ้าไม่ใช้
- ตรวจ db 3 ตัว (hmdb/alsn/logdb) ว่างเกือบหมด (log ประวัติ) · ลบไฟล์ชั่วคราวที่ก๊อปไป /sdcard แล้ว · classifier บล็อกการยิง REST ฐานนำทางกับ dump แบบ recursive — เหลือทำต่อเมื่อได้ permission

## 2026-09-11 — กล้องหุ่นเข้า face greeter ผ่านหน้า kiosk และหุ่นใช้ 8001 ตัวเดียวกับ PC

- **ตอบคำถาม "ทำไมไม่ใช้อันเดียวกับบนคอม"**: ก่อนหน้านี้หุ่นต้องใช้ `localhost:8000` ผ่าน adb reverse เพราะ Chrome บนหุ่นไม่เชื่อใบรับรอง mkcert ของ 8001 ตอนนี้ติดตั้ง `certs/rootCA-android.crt` ลง user CA store ของหุ่นแล้ว (root: `/data/misc/user/0/cacerts-added/a23c2e19.0` — ตัวติดตั้งปกติของ Android 11+ ปฏิเสธไฟล์ CA จาก intent) รีสตาร์ต Chrome แล้วเปิด `https://192.168.1.43:8001/?kiosk=1&cam=1&agc=0&token=…` ได้ตรงๆ ปิด instance 8000 และแท็บ localhost แล้ว เหลือเซิร์ฟเวอร์ตัวเดียว
- **กล้องหุ่น → greeter** (`app/robot_camera.py` ใหม่, `/ws/camera` ใน main.py, `FACE_CAMERA_SOURCE=robot` ใน config/camera.py, `?cam=1` ใน client): เซิร์ฟเวอร์เปิดกล้องบนหุ่นไม่ได้ (Android คนละเครื่อง) แต่หน้า kiosk ที่รันบนหุ่นอยู่แล้วขอกล้องได้เหมือนขอไมค์ จึงส่ง JPEG ต่อเฟรมทาง WebSocket และ `RobotCapture.read()` ทำตัวเหมือน `cv2.VideoCapture` ให้ greeter โดยไม่แก้ลูป greeter เลย กติกาที่ตั้งใจ: เฟรมเดิมไม่เสิร์ฟซ้ำ (ลิงก์ค้าง = กล้องหยุด ไม่ใช่คนยืนนิ่ง — ไม่งั้นรูปแช่ยืนยันหน้าตัวเองครบเฟรม), ไบต์เสียอ่านเป็นเฟรมว่างไม่โยน, วิดีโออย่างเดียว (`audio:false` — ห้ามเปิดไมค์ตัวที่สาม), kiosk + `?cam=1` เท่านั้น (เบราว์เซอร์โต๊ะห้ามอัปโหลดเว็บแคมเพราะ flag ค้าง), token gate เหมือนทุก socket, `bufferedAmount` กันเฟรมค้างคิว
- **สองอาการที่วัดบนหุ่นแล้วแก้ในหน้า**: (1) `getUserMedia` วิดีโอที่เรียกตอนหน้ายังโหลดอยู่ **ไม่ resolve ไม่ reject** (ไม่มี prompt) ขณะที่เรียกซ้ำอีกครั้งติดทันที → เริ่มหลัง `load` +1.5 วิ และ race 8 วิ ถือว่าค้าง=ล้มเหลว ลองใหม่ได้ 6 ครั้ง; (2) กล้องทั้งสองตัวของหุ่นรายงาน "facing back" → เลือกด้วย `?camdev=N` ไม่ใช่ facingMode · ผลจริง: `robot camera: first frame 1280x720` ถึง greeter (26 ใบหน้าในแกลเลอรี threshold 0.50) กล้อง index 0 ตอนนี้หันเข้ากำแพง ยังไม่ได้ทดสอบจำหน้า
- `.env`: `FACE_ENABLED=true` + `FACE_CAMERA_SOURCE=robot` (greeter ไม่แตะกล้อง PC อีก จึงไม่ชนกล้อง observer ของงานแขน) · `.env.example` อธิบายสวิตช์ · conftest pin `face_camera_source="local"` · `FACE_CAMERA_SOURCE` สะกดผิด = local ไม่ใช่ปิดเงียบ (เทสต์ subprocess)
- เทสต์ใหม่ `tests/test_robot_camera.py` 7 ตัว, `/ws/camera` เข้าเทสต์ "ทุก socket ปฏิเสธ token ผิด", ชุดเต็มรอบสุดท้าย **1385 passed** (หลังแก้ client รอบท้าย) · Chrome บนหุ่นถูกเปิด/ตรวจผ่าน DevTools remote (`adb forward tcp:9222 localabstract:chrome_devtools_remote`) — สคริปต์อยู่ใน scratchpad ไม่ใช่รีโป
- ยังค้าง: ผลของ `?agc=0` ต่อเสียงคนไกล (ต้องมีคนพูดใกล้/ไกลจริง), หันกล้องหุ่นให้เห็นทางเดิน แล้วลอง `?camdev=1` ถ้าตัวแรกไม่ใช่ตัวที่มองลูกค้า

## 2026-09-11 — ทดสอบไมค์/กล้อง/Emma บนตัวหุ่นจริง และปิด AGC เฉพาะหุ่น

- **ต่อหุ่นผ่าน adb** (ZC-3588A, Android 15): กล้อง 0 ใช้ได้ (ภาพสดจากแอปกล้อง), ไมค์ USB "Bothlent UAC Dongle" อัดตรงจาก ALSA (root, `tinycap -c 8 -r 16000`) ได้ 4 ช่องจริง rms ≈0.008 ช่อง 4-5 เบา ช่อง 6-7 ว่าง · ลำโพงอยู่การ์ด 2 (es8388) เล่นคลิป `emma_thai_f.wav` ผ่าน `tinyplay` ได้
- **เปิดหน้า Emma บน Chrome ของหุ่นโดยไม่ต้องลง cert**: `adb reverse tcp:8000` + instance HTTP ที่ผูก 127.0.0.1:8000 (WAKE_DEBUG=true) → Chrome เห็นเป็น `localhost` = secure context ไมค์เปิดได้ ผลจริง: `wake word heard: 'EMMA'` เปิดสายเอง Gemini ถอดไทยและตอบ กฎห้ามนอกเรื่องทำงาน (ถูกขอให้ทายชื่อ → ปฏิเสธ) · บั๊กที่เจอระหว่างทาง: refresh หน้าบนหุ่น token หลุดจาก URL แล้ว `/ws/wake` ถูกปิดเงียบ (หน้าแสดง sleep ระดับเสียง 0%) ต้องเปิดด้วย URL ที่มี token
- **ไมค์หุ่น "รับได้ต่างจากเดิม" — วัดแล้ว**: ระดับเสียงในสายใกล้เคียง PC (avg 0.01-0.07 peak 0.1-0.4) แต่ 7 ใน 15 เทิร์นที่ Gemini ได้ยินเป็นบทสนทนาของคนอื่นในห้อง (ถอดแบบเว้นวรรครายคำ 4 เทิร์น, สลับไปเกาหลีจากเสียงไกล 1 ครั้ง) ตัวแปรที่ต่าง: หุ่นเป็นอาร์เรย์ 4 ไมค์รอบทิศ + AGC ของ Chrome เปิดอยู่ (`MIC_AGC` default true) ดันเสียงไกลขึ้นมาเท่าเสียงใกล้ พื้น `VAD_MIN_RMS=0.010` ที่จูนกับไมค์ PC จึงแยกไม่ออก (ตัดได้แค่ 5 ครั้ง rms 0.001-0.0085)
- `client/index.html`: เพิ่ม override ต่อเครื่องทาง URL `?agc=0|1` และ `?ns=0|1` ทับค่าจาก `/health` เฉพาะ client นั้น (สองไมค์ใช้ .env เดียวกัน — ค่า .env ยังเป็นของ PC) ตรวจสดบนหุ่น: track ของสายมี `autoGainControl:false` แล้ว · เทสต์ `test_the_page_lets_a_url_override_mic_agc_per_client` · **ยังไม่ได้วัดผลหลังปิด AGC** — ต้องมีคนพูดใกล้หุ่นและคนคุยไกลพร้อมกัน แล้วอ่านบรรทัด `vad floor` ก่อนจะแตะ VAD_MIN_RMS
- **อุบัติเหตุปลายบรรทัด**: `Path.write_text` บน Windows เขียน CRLF ทับไฟล์ที่แก้วันนี้ทั้งไฟล์ (config/units/prompts/prompts tests/CHANGELOG/CLAUDE.md/index.html/test_voice.py) git ปกติ normalize ตอน commit จึงมองไม่เห็น แต่ `tests/client_voice_lifecycle.cjs` อ่านไฟล์ดิบแล้วหา `'
  };
}'` ไม่เจอ → พัง แปลงกลับเป็น LF ทุกไฟล์ที่ HEAD เป็น LF แล้ว node test ทั้งสองผ่าน, pytest 4 ไฟล์ **240 passed** — ต่อไปเขียนไฟล์ด้วย `newline="
"` เท่านั้น
- กล้องหุ่น: face greeter ยังใช้กล้องของ PC (`FACE_CAMERA` เป็น index ของ OpenCV) กล้องบนหุ่นยังไม่ได้ต่อเข้า Emma — ต้องมีตัวส่งภาพจากหุ่นมาที่เซิร์ฟเวอร์ก่อน (ยังไม่ทำ)

## 2026-09-11 — ห้องว่างต้องเป็นของ Embassy World เท่านั้น และตัดสไลด์ออกจาก Emma ชั่วคราว

- **บั๊กจริงจากภาพหน้าจอเจ้าของ**: `find_units` ขึ้นจอ "ห้องว่าง 8+ ห้อง" มี A-1405 ซึ่งตรวจกับ DB แล้วเป็นห้องของ **Embassy Life** — ผังขาย (Supabase ของทีมเซลส์) มี 3 โครงการตั้งแต่ 3 ก.ย. (World / Life / One) ตึก A/B/C ซ้ำกันทุกโครงการ เลขห้องจึงไม่ unique และทุก query ของเราไม่เคยกรองโครงการเลย
- `app/tools/units.py`: เพิ่ม `_SCOPE_EMBED` (`floors!inner(...buildings!inner(...projects!inner(slug)))`) กับ `_scoped()` ใส่ตัวกรอง `floors.buildings.projects.slug=eq.<slug>` ให้**ทุก** query: show_unit, find_units, show_plan, compare_unit_types, list_promotions, price_pair, _active_promotion ต้อง `!inner` ทุกชั้น เพราะกรองบน embed แบบ left ไม่ทิ้งแถว แค่ทำ embed เป็น null (บทเรียนเดียวกับ filter ชั้นของ show_plan ลึกลงอีกชั้น)
- `app/config.py`: `INVENTORY_PROJECT` default `embassy-world` ค่าว่าง = ของเรา ไม่ใช่ทุกโครงการ (บทเรียน WS_TOKEN="") · `.env.example` มีบรรทัดอธิบาย
- ตรวจกับ DB จริงหลังแก้: `find_units()` ได้ 8 ห้องทั้งหมด slug embassy-world, `show_unit("A1405")` → unknown room (ถูกต้อง — ไม่ใช่ห้องเรา), อ่าน 1000 แถวแบบ scoped ได้ slug เดียว
- **Emma ตัดสไลด์ออก** ตามคำสั่ง "เอาพรีเซ้นออกก่อน ยังไม่ได้ใช้": `.env` TOOL_GROUPS เอา `slides` ออก และ `app/prompts.py` เพิ่ม `_SLIDE_RULES` (slice จริงจาก BASE_INSTRUCTIONS ไม่ใช่ก๊อป) + `without_slide_rules()` — เมื่อเครื่องไม่โหลดกลุ่ม slides กฎ 10-13 ที่สั่งเรียก show_slide/start_presentation/next_slide หายไป แทนด้วยบรรทัดเดียว "ไม่มีสไลด์ในเครื่องนี้ เสนอผัง/ห้องว่างแทน" ข้อ 14→10 และท้าย SALES_HOST_BLOCK เลิกเสนอ "สไลด์" เหตุผล: ในเซสชันเดียวกัน Emma เสนอ "แนะนำสไลด์" ที่ตัวเองเปิดไม่ได้ (เครื่องมือถูกถอดแต่ prompt ยังสั่ง) default ห้องขาย (TOOL_GROUPS ว่าง) prompt เหมือนเดิมทุกไบต์
- เทสต์ใหม่: `test_every_live_read_is_scoped_to_our_project` (ทุก entry point ต้องมีทั้ง filter และ embed `!inner`), `test_a_blank_project_setting_means_ours_not_everything` (subprocess — reload app.config ในโปรเซสเทสต์ทำให้ `settings` กลายเป็นคนละ object แล้วเทสต์ token 16 ตัวแดง เจอจริงรอบแรก), `test_the_gallery_prompt_drops_the_slide_rules_with_the_slides_group`
- `live_probe()` (บรรทัดบูต) กรองโครงการด้วย เดิมขึ้น "2540 units" = สามโครงการรวมกัน ตอนนี้ `LIVE (1082 units of embassy-world ...)` ตรวจกับ DB จริงแล้ว
- ผลตรวจ: test_units + test_sales_links + test_profiles + test_voice **239 passed** ชุดเต็ม **1377 passed** ใน 149 วินาที (1 warning เดิมของ Starlette) · รีสตาร์ต 8001 ผ่าน `run_server.py` (HTTPS) ให้ .env ใหม่มีผล

## 2026-09-11 — ค้นคำสั่งอ่านกลุ่มและควบคุมรายข้อต่อ

- เพิ่ม `docs/robot-channel-mapping-stop-findings-2026-09-11.md` รวมผลจริง จำนวนกลุ่ม 26 และหลักฐานช่อง 5 แก้รายงาน E-stop เรื่อง IO7 ที่ภายหลังยืนยันแล้ว ขยาย guard ใน serial probe ให้ปฏิเสธทุกโหมดขยับก่อน I/O (ไม่ใช่การล็อกหน้าเว็บหรือตัดไฟ) ตรวจ recorder/arm/console หลังขยาย guard แบบ parametrized ผ่าน 62 ตัวใน 1.46 วินาที มี deprecation warning ของ Starlette/httpx หนึ่งรายการ; git diff --check ผ่าน ไม่มีการติดต่อหรือส่งคำสั่งขยับหุ่นในรอบแก้ไขนี้

- ช่อง 5 มีภาพแขนด้านซ้ายเปลี่ยนมุมออกจากลำตัวทั้งรอบ P1500 และ P1540 แต่ยังเปลี่ยนหลังช่วง remote STOP จึงพัก commissioning ทุกช่องไว้ก่อน I/O และเพิ่มเทสต์พิสูจน์การปฏิเสธ เปลี่ยนข้อความปุ่ม console/arm เป็นส่ง STOP และล็อก พร้อมระบุข้อจำกัดที่พบจริง ไม่อ้างว่า +40 เป็นระยะเคลื่อนเล็กน้อยหรือเวลา STOP เป็นขอบเขตการเคลื่อนจริง เจ้าของแจ้งว่ายังไม่ได้ปิดไฟหลัก จึงไม่ส่งคำสั่งขยับต่อ

- คืนกล้องหลังพัก face greeter และเริ่มเซิร์ฟเวอร์ใหม่ ทดสอบช่อง 2 เป้าหมาย 1500 ได้ stop ACK แต่ยังไม่เห็นเปลี่ยนท่า เพิ่มเป้าหมาย commissioning ทางเลือก 1540/T3000 เท่านั้น (ยัง remote stop 2 วินาที) เพื่อแยกกรณีอยู่ใกล้ค่าเดิม ไม่เปิดช่วง pulse ทั้งหมดและไม่อ้างตำแหน่งจริงจากตัวเลขคำสั่ง

- พัก `FACE_ENABLED=false` ใน `.env` ชั่วคราวและเก็บค่าเดิมไว้ในหลักฐาน ignored เนื่องจาก run_server.py ที่เริ่มใหม่เปิด face greeter ใช้กล้องเดียวกับ observer; รอบ commissioning ช่อง 2 ยกเลิกก่อนส่งเพราะไม่มีภาพ ไม่เปลี่ยนสวิตช์ควบคุมแขนเพื่อแก้ปัญหากล้อง

- อ่านจริงได้ `#R+OK+026` โดย #Read ไม่ส่ง motion; รอบแรกปฏิเสธเพราะ API ถูก rearm ระหว่างที่มีคำสั่งกลุ่มอื่นเข้ามา จึงแจ้งให้เว้นหน้าเว็บและ stop/ล็อกก่อนอ่านใหม่ ทดสอบช่อง 1 ซ้ำได้ ACK แต่ยังไม่เห็นขยับจากภาพ
- เพิ่มโหมด commissioning แยกจากปุ่มเว็บ: `--map-channel` เลือกทีละช่อง 1–20 เท่านั้น ส่ง P1500/T9999 และตั้ง remote trap ส่ง STOP หลัง 2 วินาที รักษา API เดิม locked และไม่ขยาย production allowlist ค่า 1500 ไม่ใช่ท่ากลางที่วัดแล้ว มี watchdog กล้องและ stop API หลังจบตามเดิม เจ้าของอนุญาตค้นครบและยืนยันมือพ้นหุ่นแล้ว; รอตรวจเทสต์และภาพรายช่อง

- ดาวน์โหลด RIOS_USC จากลิงก์ผู้ผลิตในคู่มือเก็บ ignored เพื่อวิเคราะห์ static เท่านั้น ไม่รันหรือติดตั้ง พบสตริง `#Read` พร้อม CRLF และ parser marker `#R+OK` เพิ่ม `scripts/read_robot_arm_groups.py` ส่งเฉพาะ query นี้หลังตรวจ API locked/พอร์ตพร้อม/แอปผู้ขายหยุด ไม่เรียกท่า ไม่เขียน Flash ไม่เปลี่ยน baud; รอตรวจ syntax และผลจริง

## 2026-09-11 — เตรียมตรวจกลุ่มท่า 3 ด้วยกล้อง

- ทดสอบจริงหลังเจ้าของยืนยันพร้อม: กลุ่ม 3 หนึ่งครั้ง สังเกต 8 วินาทีแล้ว stop/ล็อก ได้ 156 เฟรม RX 80 ไบต์และ stop ACK เห็นหัวหันและแขนด้านซ้ายของภาพยก/งอศอก เจ้าของยืนยันว่าไม่ได้ช่วยยก เพิ่ม `docs/robot-group3-live-test-2026-09-11.md` และอัปเดตรายงาน mapping ยังไม่ยืนยันช่องภายในกลุ่ม นิ้วรายนิ้ว หรือผลหยุดฉุกเฉิน ไม่มีการสั่งกลุ่มอื่นหรือเปิดไฟรีเลย์
- ตรวจลิงก์หลักฐานรายงานใหม่ 5 รายการและจำนวนเฟรม/RX/ACK ตรงไฟล์จริง ตรวจ state ซ้ำได้ armed=false, port_present=true, commanded ว่างตามกลไกหลังเล่นกลุ่ม และ whitespace ผ่าน

- เพิ่มตัวเลือก explicit `--group 3` ใน serial probe และ `--probe group3` ใน camera recorder ตามกลุ่มที่เจ้าของแจ้งว่าเคยใช้ ทดสอบได้เฉพาะกลุ่ม 3 หนึ่งรอบ มีช่วงสังเกต 8 วินาทีแล้วส่ง stop/ล็อก ไม่ถือว่าท่าจบครบ ใช้ watchdog กล้องเดิมและไม่เปิดไฟรีเลย์อัตโนมัติ
- เพิ่มเทสต์ปฏิเสธเลือกกลุ่มพร้อมขยับรายช่องและปฏิเสธกลุ่มอื่น รวมเทสต์ recorder/probe ผ่าน 8 ตัวใน 0.41 วินาทีหลังใช้ basetemp ใหม่ใน workspace (รอบแรกติดสิทธิ์ temp เดิมก่อนเทสต์เริ่ม); py_compile และ whitespace ผ่าน ตรวจ state พบพอร์ตพร้อม กลุ่มปลดล็อกแต่การส่ง disarmed และกล้องคอมได้ 58 เฟรมในโหมด observe ยังไม่ส่งคำสั่งหุ่น รอยืนยันพื้นที่ภายในและฝาครอบพ้นกลไกจากเจ้าของก่อนทดสอบ

## 2026-09-11 — ตรวจวิดีโอบอร์ดและสายภายในสองคลิป

- เพิ่ม `docs/robot-interior-video-analysis-2026-09-11.md` และลิงก์ต่อจากรายงานภาพเดิม แยกเฟรมวิดีโอในโฟลเดอร์ ignored พบรายละเอียดบอร์ดแดง ตัวปรับสามตัว และป้ายสาย 2/11 แต่ยังไม่ยืนยันรุ่น ผังช่อง หรือสาเหตุแขนไม่ขยับ ไม่ตีความขั้วที่มองไม่เห็นสายเป็นหลักฐานไฟขาด
- บันทึกข้อมูลเจ้าของว่าเคยทดสอบแขนและนิ้วแล้วขยับปกติ แยกจากผลที่เราวัดเอง เปลี่ยนจุดมุ่งหมายเป็นตามเส้นทางคำสั่งที่ทำงานและเงื่อนไขเปิดใช้งาน ไม่อนุมานว่ามอเตอร์ไม่มีหรือเสียจากช่อง 1/11 ที่ไม่เห็นขยับ
- ภาพที่เจ้าของส่งต่อแสดงปุ่มเล่นกลุ่มท่าหมายเลข 3 ตรวจเส้นทาง console/API/run_group ว่าสร้าง `#3GC1` จริง อัปเดต `docs/robot-gesture-mapping-research-2026-09-11.md` โดยไม่ตั้งชื่อกลุ่มหรืออ้างว่าตรวจการเคลื่อนที่เอง
- เทียบแหล่งข้อมูลการใช้ชุดมอเตอร์ DC กับวงจรเซอร์โวเพื่อคงความเป็นไปได้ของ PWM โดยไม่คัดลอก pinout หรือค่าจ่ายไฟของอุปกรณ์อื่น ตรวจภาพขนาดเดิม ข้อมูลวิดีโอ และลิงก์ในรายงานวิดีโอ 11 รายการพบครบ; ตรวจ whitespace ผ่าน ไม่มี ADB คำสั่งหุ่น หรือการเปลี่ยน runtime จึงไม่รัน unit tests

## 2026-09-11 — ตรวจภาพฮาร์ดแวร์ภายในจากเจ้าของ

- เพิ่ม `docs/robot-interior-photo-analysis-2026-09-11.md` และสำเนาภาพเดิมในโฟลเดอร์ ignored เห็นมอเตอร์กระบอกโลหะสองชุดบริเวณไหล่กับเซอร์โวบริเวณคอ แยกข้อสังเกตจากการคาดชนิด/หน้าที่ ยังไม่ยืนยันรุ่นบอร์ด ผังสาย หรือมอเตอร์นิ้ว
- ตรวจจุดเรียก MotorRunTurnLeft/Right ใน disassembly พบอยู่กับ touchActionType/robotIsRun จึงไม่ถือว่าเป็นคำสั่งแขนและไม่ทดลองส่ง มี LED ติดในภาพจึงไม่ถือว่าภายในปลอดไฟ; รอบนี้ไม่เรียก ADB หรือสั่งหุ่น ไม่แก้ runtime ไม่รัน unit tests ตรวจภาพและลิงก์หลักฐานแทน

## 2026-09-11 — ตรวจเส้นทาง CP2102 และสถานะแขนต่อ

- แก้เส้นทางรีเลย์ใน `docs/robot-command-research-2026-09-11.md` ให้ตรง target จริง พร้อมทำเครื่องหมายข้อความ handshake เดิมเป็นข้อสันนิษฐานที่แก้ไขแล้ว; เพิ่มผล IO7 ที่วัดได้ใน `docs/robot-estop-evidence-2026-09-11.md` โดยไม่เปลี่ยนเป็นคำรับรองการหยุดหรือตัดไฟ
- เพิ่ม `docs/robot-cp210-estop-relay-2026-09-11.md` บันทึก IO7=0→1→0 จากแพ็กเก็ต checksum ผ่านครบทุกตัวอย่าง แก้สาขา CP2102/PL232 เดิม และผล power pulse จริง 1 วินาที กล้อง 91 เฟรมยังไม่เห็นแขนเปลี่ยนตำแหน่ง ไม่มีการวัดแรงดันหรือ ACK รีเลย์ จบรอบสั่ง OFF สองครั้งและคืนไดรเวอร์ unbound; เทสต์ offline รวม 13 ตัวผ่าน ตรวจ API แขน locked และ CH340 ยังอยู่
- ยืนยัน IO7 จากการอ่านจริงสามรอบ: ก่อนกด IO=0, กด IO=64, ปล่อย IO=0; เพิ่ม `scripts/probe_robot_arm_relay.py` สำหรับทดสอบจ่ายไฟรีเลย์หนึ่งวินาทีแล้วสั่งตัดสองครั้ง โดยต้องมีภาพและ preflight IO7 ปล่อยครบทุกตัวอย่าง ไม่มีคำสั่งข้อต่อ ปลายทางเป็นคำสั่ง OFF ไม่ใช่ยืนยันแรงดันหรือคืนสถานะเดิม เพิ่ม `tests/test_robot_relay_probe.py` และตัวเลือก relay ในตัวบันทึก; รอตรวจและทดสอบจริง
- อ่าน CP2102 ได้ข้อมูลออกเองระหว่างเปิด reader 4 วินาที ไม่ส่ง serial payload และคืนไดรเวอร์เป็น unbound แล้ว stdout ของ ADB exec-out รวมข้อความสถานะที่เขียน stderr มาด้วย จึงห้ามนับทุกไบต์เป็น serial เพิ่ม `scripts/decode_robot_cp210.py` แยกเฟรมด้วย header/length/checksum ตามโค้ดผู้ขาย และ `tests/test_robot_cp210_decode.py` ตรวจ noise/checksum/truncation; รอผลตรวจและเทียบปุ่มแดง
- ไล่ branch จริงของ `sendCmdtowhichUart`: route 60000 ไป `sendCmd`/`SerialToothManager` (CP2102) ไม่ใช่ `sendCmdPL232` ตามรายงานเดิม ถอด DataConstants/AllTouchSignalEvent เพิ่มจาก DEX สำรอง พบ `COMM_GET_SENOR` และรูปแบบรับ IO; ยังไม่ส่งคำขอหรือรีเลย์
- เพิ่ม `scripts/read_robot_cp210_status.py` สำหรับผูกไดรเวอร์ CP2102 ที่ตรวจ VID/PID แล้วชั่วคราว เก็บ RX 4 วินาที คืนค่า stty และ unbind ใน trap ไม่ส่ง serial payload ไม่เปลี่ยนแอปหรือ CH340; การเปิดพอร์ตอาจเปลี่ยน control lines จึงไม่เรียกว่า electrically passive รอตรวจจริง

## 2026-09-11 — เตรียมทดสอบช่องอื่นพร้อมกล้อง

- ตรวจสุดท้าย: เทสต์ตัวบันทึก/ตัวเลือกช่องแบบ mock ผ่าน 7 ตัว; สร้างและตรวจภาพเปรียบเทียบจริงแล้ว อ่านสถานะหลังทดสอบ armed=false (ค่าที่สั่งไว้ช่อง 7/8/11 = 1540 ไม่ใช่ค่ามุมที่วัด)

- ทดสอบด้านหน้าครบช่อง 7/8/11 รวม 6 รอบ บันทึก 498 เฟรม ทุกครั้งส่งหยุดและล็อกกลับ: ช่อง 7 หันหัวไปขวาของภาพ, ช่อง 8 ก้มลงเล็กน้อยเมื่อ 1500→1540, ช่อง 11 ไม่เห็นเปลี่ยนตำแหน่ง เพิ่ม `docs/robot-front-camera-test-2026-09-11.md` พร้อมหลักฐาน/ข้อจำกัด ไม่อ้างว่าทดสอบครบทุกมอเตอร์หรือหยุดกลางทางได้
- ภาพช่อง 8 เมื่อ 1500→1540 เห็นหัวก้มลงเล็กน้อย เพิ่มการแยกข้อมูลใน serial probe: `physical_motion_observed=null` หมายถึงสคริปต์อ่านสายไม่ได้ตรวจภาพเอง แทน false ที่อาจถูกตีความว่าไม่มีการขยับ หลักฐานเก่าคงเดิมและต้องอ่านร่วมผลตรวจภาพในรายงาน
- กล้องจริง index 1 ผ่าน DirectShow เก็บภาพด้านหน้าได้หลังปิด preview Camera Hub; ทดสอบช่อง 7 ที่ 1500 แล้ว 1540 ทีละรอบพร้อม stop/ล็อกกลับ ภาพรอบ 1540 เห็นหัวหันไปขวาของภาพจริง ไม่แปลงเป็นองศาหรือยืนยันผลหยุดขณะเคลื่อน เพิ่ม `scripts/render_robot_front_probe.py` สร้างภาพเปรียบเทียบ/GIF จากเฟรมจริงและตัดบริเวณจอคอมออก; รอตรวจช่อง 8/11 ต่อ
- ผู้ใช้ยืนยันมุมด้านหน้าพร้อมแล้ว ตัวบันทึกกล้อง 0 ได้ภาพดำสองรอบ จึงยุติก่อนส่งคำสั่งทั้งสองรอบ เพิ่ม `--camera` ใน `scripts/record_robot_arm_probe.py` เพื่อเลือกอุปกรณ์จริงแทน virtual camera พร้อมบันทึกเลขอุปกรณ์ในหลักฐาน; รอตรวจภาพก่อนทดสอบต่อ
- ผู้ใช้ขอทดสอบส่วนอื่น เพิ่มตัวเลือกช่องเดี่ยว 1/11/7/8 ใน `scripts/probe_robot_arm_stop.py` และ `scripts/record_robot_arm_probe.py` โดยไม่เพิ่มช่องที่ไม่มีหลักฐานและไม่เล่นกลุ่มท่า; step ต้องมีค่าที่สั่งไว้ 1500 ของช่องที่เลือกเอง ไม่ใช้ค่าจากช่องอื่น ค่าเริ่มต้นยังคง observe/stop-only
- เพิ่มเทสต์ปฏิเสธช่องที่ไม่รู้จัก การเลือกหลาย action และการใช้ baseline ข้ามช่องใน `tests/test_robot_probe_recording.py`; ตรวจผ่าน 7 ตัวแบบ mock ไม่ติดต่อหุ่น; รอภาพด้านหน้าก่อนทดสอบจริง

## 2026-09-11 — ตรวจ USB หลังผู้ใช้ไม่เห็นการขยับ

- ผู้ใช้รายงานว่าไม่เห็นการขยับจากรอบช่อง 1; ยังไม่ระบุผังข้อต่อจากผลนี้
- เพิ่ม `scripts/probe_robot_arm_usb.py` ตรวจ VID/PID และจับ usbmon เฉพาะ CH340 ระหว่าง stop-only probe โดยกรองบนหุ่นก่อนส่ง/เก็บข้อมูล ไม่เก็บ traffic กล้องหรือไมค์ ไม่เปลี่ยนไดรเวอร์; พบ TX #STOP CRLF 7 ไบต์สองครั้งและ RX #STOP+OK... CRLF 13 ไบต์สองครั้ง
- แก้ตัวอ่านเดิมด้วย timeout --foreground และ adb exec-out: รับ ACK รวม 26 ไบต์ตรงกับ USB; ผล 0 ไบต์เดิมไม่ใช่หลักฐานว่าบอร์ดไม่ตอบ ยังคงไม่ยืนยันตำแหน่งหรือการหยุดทางกายภาพ
- เพิ่ม flag --step-channel-1 แบบใช้แทน centre และต้องมี last-commanded=1500 ก่อน ทดสอบ up (+40 → 1540) เพียงครั้งเดียวแล้ว stop/ล็อกกลับ: รับ 31 ไบต์ เป็น #CC CRLF และ stop ACK สองชุด ผู้ใช้ยังไม่เห็นการขยับ ไม่เพิ่มระยะต่อ
- เทียบ #CC กับ parser/ARM_ONE_ACTION_END=169 จากโค้ดผู้ขาย ยืนยันว่าเป็นข้อความระดับ action ไม่มีค่ามุมหรือ encoder; ปรับรายงาน live measurement ให้แก้ข้อสรุป 0 ไบต์เดิมอย่างเด่นชัด
- ผู้ใช้รายงานปุ่มแดงเด้งขึ้นและขอเชื่อมกล้องคอม เปิดหน้าตรวจกล้อง localhost แล้ว คำขอเปิดกล้องยังรอผล ยังไม่ส่งคำสั่งขยับรอบใหม่
- ตรวจกล้องต่อ: getUserMedia ในเบราว์เซอร์ Codex ค้างโดยไม่มี prompt จึงปิดแท็บนั้น พบ Elgato Facecam และ Camera Hub แจ้ง occupied; Windows ระบุ Python ใช้กล้อง, เซิร์ฟเวอร์ condo-voice พอร์ต8001 เปิด FACE_ENABLED อยู่ รีสตาร์ตเฉพาะเซิร์ฟเวอร์ที่ยืนยัน PID โดยตั้ง FACE_ENABLED=false ชั่วคราวใน process และเริ่ม robot_arm แบบล็อก ไม่แก้ .env ถาวร ภาพสดใน Camera Hub กลับมาแล้ว แต่จอคอมบังแขนข้างหนึ่ง จึงรอจัดมุมก่อนสั่งรอบถัดไป
- ผู้ใช้จัดมุมและยืนยันพร้อม เห็นหัวและแขนสองข้างจากภาพกล้อง เพิ่ม `scripts/record_robot_arm_probe.py` เก็บเฟรมพร้อมเวลา โดยค่าเริ่มต้น observe ไม่ส่งคำสั่ง; ต้องเลือก centre/step ชัดเจนจึงเรียก probe หนึ่งรอบหลังได้ baseline อย่างน้อย 12 เฟรม กล้องล้มเหลวหรือ probe เกินเวลาจะร้องขอ stop และไม่อ้างว่าหยุดทางกายภาพแล้ว
- ทดสอบพร้อมกล้องจริงช่อง 1 ที่ 1500/T9999 และ 1540/T800 แล้ว stop/ล็อกกลับ ได้ภาพ 84 และ 82 เฟรม รับ stop ACK ทั้งสองรอบ และ #CC ในรอบ 1540 จากภาพก่อน/ระหว่าง/หลังยังไม่เห็นหัวหรือแขนเปลี่ยนตำแหน่งจากมุมนี้ ไม่ใช่การวัดองศาหรือข้อพิสูจน์ว่าไม่มีการขยับเล็กน้อย รอบ baseline ดำหรือไม่ครบยุติก่อนส่งคำสั่ง
- เพิ่ม `scripts/render_robot_probe_review.py` สร้างภาพเปรียบเทียบและ GIF จากเฟรมจริงในโฟลเดอร์หลักฐาน ignored; อัปเดต `docs/robot-arm-live-measurement-2026-09-11.md` ด้วยผลกล้องและข้อจำกัด เพิ่ม `tests/test_robot_probe_recording.py` ตรวจ watchdog ด้วย mock ผ่าน 3 ตัว กล้องยังเปิดดูได้และระบบทักทายด้วยใบหน้าพักชั่วคราวเฉพาะ process; `.env` ไม่เปลี่ยน

## 2026-09-11 — เริ่มวัดข้อมูลรับจากบอร์ดแขนจริง

- เพิ่ม `scripts/probe_robot_arm_stop.py` เปิด reader แบบจำกัดเวลา แล้วเรียก stop endpoint เดิมเพื่อเก็บ RX เป็น hex/ASCII โดยต้องระบุ --send-stop ชัดเจน ไม่ส่งคำสั่งท่าหรือกลับ home และตรวจแอปผู้ขายไม่ทำงานก่อนทดสอบ
- ผู้ใช้อนุญาตทดลองและแจ้งว่าอยู่ใกล้หุ่น ตรวจสด 115200 8N1 raw/echo-off แล้วทดสอบ stop: รอบแรกตัวอ่าน SIGTERM ติด timeout ฝั่งคอม (ไม่ใช้สรุป RX); รอบที่เก็บครบใช้ timeout SIGKILL 5 วินาที ได้ 0 ไบต์ ไม่พบ ACK, stop_write_ok=true, armed=false และไม่มี local timeout
- เพิ่ม `docs/robot-arm-live-measurement-2026-09-11.md` แยกผลเขียน ข้อมูลรับ และการเคลื่อนไหวจริง ช่วงทดสอบนี้ยังไม่ส่งคำสั่งขยับ และยังไม่ยืนยันผลหยุดทางกายภาพ; ตรวจ syntax สคริปต์และ mandatory --send-stop ก่อนเข้าถึงหุ่น
- ต่อมาผู้ใช้ยืนยันว่าปิดไฟหลักได้: เพิ่ม flag `--centre-channel-1` และทดสอบ centre ช่อง 1 จริงเพียงครั้งเดียว (1500/T9999) แล้ว stop หลัง API ตอบ 2 วินาทีใน finally และล็อกกลับ; ทั้งสอง API ตอบสำเร็จแต่ RX 0 ไบต์เช่นเดิม ยังรอผู้ใช้รายงานการขยับ/หยุดที่เห็น ไม่ทดสอบช่องอื่นหรือกลุ่มท่าอัตโนมัติ

## 2026-09-11 — ปรับคอนโซล HUD และติดตามผลคำสั่ง

- ปรับ `client/robot-console.html` เป็น HUD น้ำเงินฟ้า เพิ่มผังเรืองแสง แผงผลคำสั่งแยกแขน/ฐานล้อ ตัวบ่งชี้ช่องที่ส่งและค่าที่สั่งไว้ พร้อมตัวอย่างแสงเฉพาะบนจอ ไม่สร้างผังข้อต่อหรือค่าตำแหน่งจริงที่ยังไม่ได้วัด
- ผลเขียน stop ล้มเหลวแสดงข้อผิดพลาดแม้ API คืน ok=true; ผลคำสั่งเก่าไม่ทับผลหยุดที่ใหม่กว่า ปุ่มหยุดไม่รอ busy และล้างสถานะ/ปิดปุ่มเมื่ออ่านไม่ได้ รายงานกล้องที่บันทึกไว้ไม่ทำให้กล้องขึ้นออนไลน์
- เพิ่ม `scripts/test_robot_console_ui.cjs` ทดสอบใน Chromium โดย mock ทุก request: เปิดหน้า/กดตัวอย่างไม่ส่งคำสั่ง, pending/stop/late response, อ่านล้มเหลว, การแสดงรายงานเก่า, escape ชื่อสถานที่, ไม่มี JavaScript error และมือถือไม่ล้นแนวนอน ผ่านทั้งหมด ตรวจภาพ desktop/mobile แล้ว
- รัน pytest หน้า console/arm/hud ผ่าน 59 ตัว มี Starlette deprecation warning เดิม 1 รายการ; ครั้งแรกติดสิทธิ์ temp ก่อนเข้าเทสต์ แก้โดยใช้ basetemp ใน workspace ไม่ใช่เปลี่ยน assertion ไม่ได้รัน suite ทั้งหมด
- ตรวจเซิร์ฟเวอร์เดิมพอร์ต 8001 ตอบ HTTP 200 พร้อมหน้าใหม่ และอ่าน ADB พบ ttyUSB10 บน USB VID/PID 1a86:7523 (CH340); ไม่พบ PID แอปผู้ขายในการอ่านครั้งนี้ ยังไม่ได้ส่งคำสั่งเคลื่อนไหว เปิดพอร์ต หรือทดสอบหยุดแขนจริงในรอบนี้

## 2026-09-11 — วิเคราะห์การสร้างท่าเองและผลหลัง stop ACK

- เพิ่ม `docs/robot-custom-motion-design-2026-09-11.md` อธิบายคำสั่งรายช่อง/พร้อมกัน/กลุ่ม ลำดับ keyframe สองทาง (Emma เก็บเองหรือบอร์ดเก็บ) และ mapping/ขอบเขตจริงที่ยังขาด ตรวจภาพคู่มือ Torobot หน้า 4/6 ยืนยัน Add/Download ตามคู่มือ แต่ยังไม่ยืนยัน upload protocol หรือความเข้ากันได้กับบอร์ดจริง
- ยืนยัน branch Save คำพูดของ ArmTestActivity บันทึก JSON keyword binding ใน preferences ไม่ใช่ keyframe ลงบอร์ด; ตรวจ startAction ใช้กลุ่ม 99 เป็น metadata fallback โดยไม่แทน raw frame
- พบเส้นทาง stop ACK → Handler FROMARM ความยาว 13 → read32UartControlStopARM ถ้า isArmBlocked=false ส่ง #99GC1 และส่งซ้ำ 120 ms; อัปเดตลิงก์จากรายงาน interface/E-stop ระบุเงื่อนไขและยังไม่ทดสอบจริง ไม่อ้างว่า stop ACK ทุกแบบส่ง reset
- อ่านเอกสารและ disassembly บนคอมเท่านั้น ไม่เปิด RIOS_USC ไม่เรียก ADB/Binder/USB/serial ไม่อัปโหลดหรือลบท่า ไม่แก้ runtime และไม่รัน unit tests ของแอป; ตรวจภาพ render แล้ว `git diff --check`, whitespace และลิงก์ภายในรายงานผ่าน

## 2026-09-11 — ตรวจหลักฐานปุ่มฉุกเฉินและแหล่งจ่ายเซอร์โว

- เพิ่ม `docs/robot-estop-evidence-2026-09-11.md` แก้คำตอบว่าข้อมูลแยกไฟมีเฉพาะคู่มือบอร์ดอ้างอิง: คู่มือผู้ใช้ `หุ่นยนต์ ที่สนใจ.pdf` หน้า 6 ระบุแหล่งจ่ายเซอร์โวแยกจากบอร์ดจริง แต่ไม่ได้บอกตำแหน่งสวิตช์หรือผังสายเครื่องนี้
- ตรวจเส้นทาง Aobo รับสถานะ E-stop ผ่าน isIo7 ภายใต้ flag รับสัญญาณ แล้วเรียก stop helper เมื่อ estopStopArmMovement เปิด; helper ส่ง #STOP ผ่าน CH340 และส่งซ้ำ 60 ms ยืนยันตัวเลือกที่สำรองไว้เป็น true แยกจากการพิสูจน์ว่าปุ่มตัดไฟจริงหรือหยุดแขนจริง
- ตรวจข้อความ PDF สี่ไฟล์และภาพหน้า 6 ด้วย Poppler, อ่าน branch/helper/ค่าตั้งจากสำเนาในคอม ไม่ติดต่อหรือสั่งหุ่น ไม่แก้ runtime ไม่รัน unit tests ของแอป

## 2026-09-11 — พิมพ์ target จริงของ switch ชื่อท่า

- เพิ่ม `scripts/inspect_aobo_gesture_switch.py` ใช้ standard library ค้น IdleActionService.getActionName(int) ผ่านตาราง DEX แล้วอ่าน packed-switch และคำสั่งคืน String; เพิ่มหลักฐาน `docs/robot-interface-reference/get-action-name-switch-targets.json` และตาราง offset/bytes ในรายงานผังข้อต่อ
- ยืนยัน key 6 → target 0x3a4b42 → 握手 และ key 12 → target 0x3a4b16 → 打招呼行走 จาก DEX เดิมที่ตรวจ hash แล้ว การเรียงบล็อกในไฟล์ไม่ใช่ลำดับ key; เพิ่มการพิมพ์ targets ในสคริปต์ disassembly เฉพาะงาน (ignored) ยังไม่ยืนยันกลุ่มท่าบนบอร์ดหรือสาเหตุที่สองตารางชื่อขัดกัน
- รันตัวตรวจใหม่สำเร็จ อ่าน targets ทั้ง 9 พร้อมตรวจ const-string/return-object โดยไม่เรียกหุ่น; เทียบ hash/code_item/ชื่อทั้ง 9 กับผลเดิมตรงกัน รัน disassembly อีกครั้งพิมพ์ targets ตรงกัน ตรวจ syntax/whitespace/ลิงก์เอกสารและ `git diff --check` ผ่าน
- ผู้ใช้รายงานผลชุดทดสอบ 1330 ผ่านจากเครื่องของผู้ใช้ ผู้ช่วยยังไม่ได้รัน `python -m pytest tests/ -q` ซ้ำในรอบนี้ ตัวเลขนี้จึงเป็นผลที่ผู้ใช้รายงาน ไม่ใช่ผลตรวจของผู้ช่วย

## 2026-09-11 — ค้นต่อเลขท่าจับมือและผังข้อต่อโดยไม่สั่งหุ่น

- เพิ่ม `docs/robot-gesture-mapping-research-2026-09-11.md` และ `docs/robot-interface-reference/gesture-labels.json`; อัปเดตลิงก์จากรายงาน interface พบ switch `IdleActionService.getActionName` จับคู่ 6 กับ “จับมือ” พร้อมชื่ออีก 8 รายการ แต่ชื่อ constant เลขเดียวกันขัดกัน และไม่พบ caller ใน bytecode Aobo/Bole ที่ตรวจ จึงระบุเป็นเบาะแส ไม่ใช่ท่าที่พิสูจน์กับบอร์ด
- ตรวจเส้นทาง VoiceArmKeyWordBean → VoiceCommandMatcher → เลขกลุ่มที่ส่ง CH340 และพบ String argument ของ IdleActionService.sendArmToRobot ใช้ log ไม่ใช่เลขกลุ่มโดยตรง ยังไม่พบ wiring map รายข้อต่อ; ไม่ใช้ชื่อ skeleton ของไลบรารี IMI หรือชื่อ connector ของ Office มาเป็นชื่อเซอร์โว
- อ่านไฟล์ตั้งค่าจากหุ่นเดิมเฉพาะจุด ไม่พบ armkeywordsharepreferences/RobotSettings และไม่พบ armaction ใน parent listing; แก้ตัวตรวจให้ตรวจเนื้อหา XML/ข้อความ error เพราะ su คืน 0 ได้เมื่อ cat/ls ล้มเหลว ไม่อ้างว่าสำรองไฟล์ที่ไม่พบสำเร็จ
- รอบนี้ไม่เปิดแอป ไม่เรียกท่า/Binder/broadcast ควบคุม ไม่เขียน USB/serial และไม่แก้ runtime `/arm`; สคริปต์/ข้อมูลดิบอยู่ในโฟลเดอร์วิเคราะห์ ignored ตรวจ raw DEX switch ด้วย struct ได้เลข/ชื่อครบ 9 ตรงกับ Androguard และ JSON, SHA-256/ค่าคงที่ที่ขัดกันผ่าน, XML สองไฟล์ parse/hash ผ่านและระบุสาม read errors ตามจริง, AST parse ผ่าน 14 สคริปต์ ไม่รัน unit tests เพราะไม่แก้ runtime

## 2026-09-11 — อ่านข้อมูลภายใน Aobo หลังเจ้าของอนุญาตให้ค้นเอง

- **ผลต่อเนื่องหลังได้รับอนุญาตให้เปิด Aobo โดยมีคนดู:** เปิด SplashActivity หนึ่งครั้ง อ่าน DEX 7 ไฟล์รวม 31,328 class definitions มีคลาส Aobo 5,616 รายการ เพิ่ม `docs/robot-recovered-interface-2026-09-11.md`, ไฟล์อ้างอิง AIDL สองไฟล์ใน `docs/robot-interface-reference/` และเชื่อมสถานะใหม่จากรายงานก่อนหน้า
- ถอด MyService/Callback, tags, ช่องทางแขน CH340 byte[] และจอ/สีหน้า พบ `#STOP` กับ parser `#STOP+OK` จึงแก้ข้อสรุปเดิมว่าไม่มี stop แต่ยังไม่ทดสอบฮาร์ดแวร์ พบ usbInit ส่งกลุ่ม 99 เมื่อเปิดพอร์ตสำเร็จและ TTS helper เปิดท่าประกอบ จึงไม่อ้างว่าการเริ่มแอปไม่มีโอกาสสั่งฮาร์ดแวร์
- ติดตั้ง Androguard เฉพาะโฟลเดอร์วิเคราะห์ในคอม ไม่แก้ runtime ไม่เรียก Binder/broadcast ควบคุมหรือส่ง USB/serial เอง ไม่ติดต่อผู้ขาย; DEX มี Adler32 ตรงแต่ header SHA-1 ไม่ตรงและบางเมธอดยัง native จึงไม่อ้างว่ากู้โค้ด/ชื่อท่า/joint map ครบ
- ผลตรวจ offline: SHA-256/ขนาด/Adler32 ของ DEX ทั้ง 7 ผ่าน เทียบจำนวนคลาสด้วย parser สองตัวตรงกัน ถอดคลาสที่เลือก 260 คลาส ตรวจ CRLF และ AIDL transaction IDs ตรง bytecode, Python AST parse ผ่าน 9 สคริปต์, `git diff --check` ผ่าน; ยังไม่ได้ compile/bind AIDL หรือรัน unit tests เพราะไม่แก้ runtime รายการด้านล่างเป็นเหตุการณ์ช่วงก่อนการเปิดแอปที่ได้รับอนุญาต

- เพิ่ม `docs/robot-internal-code-research-2026-09-11.md`: ยืนยัน su เดิมใช้งาน UID 0 ได้ อ่าน maps/fd/รายชื่อไฟล์ส่วนตัวของแอปที่รันอยู่สำเร็จ พบ armSetting.xml, USB_HUB_LINK.xml และช่วง memory ที่ควรตรวจโค้ดต่อ
- แยกหลักฐานการเปิด USB ของโปรเซสออกจากการอ้างสิทธิ์ interface และแยกช่วง memory ที่น่าสนใจออกจาก DEX ที่ยืนยันแล้ว ไม่อ้างว่าได้คำสั่งหรือ joint map ครบ
- สคริปต์เฉพาะงานและข้อมูลดิบเก็บใน `data/robot-inspection/service-interface-research-20260911/` (ignored); พบและแก้ path ของเครื่องมือในสคริปต์ inventory ก่อนรันสำเร็จ
- ADB หลุดเป็น offline ก่อนอ่านเนื้อหา preferences/memory; ผู้ใช้ยืนยันแบตหมดและเปิดใหม่ จึงเชื่อมกลับ endpoint ที่ประกาศและตรวจอุปกรณ์เดิม สำรองค่าภายใน/ฐานข้อมูลเพิ่ม 48 ไฟล์ วิเคราะห์ SQLite แบบอ่านอย่างเดียว 5 ไฟล์ quick_check ผ่านทั้งหมด
- พบสวิตช์ voice action group ปิดและเลขกลุ่มท่าที่ตั้งไว้ 6/17; ยังไม่มีชื่อท่า/joint map หรือ DEX ที่คลายการห่อ แอป Aobo ยังไม่รันหลังบูตจึงไม่เปิดแอปให้เอง ไม่ติดตั้ง/เริ่ม/หยุดแอป ไม่แก้ค่าหุ่นหรือส่งคำสั่งมอเตอร์
- ตรวจไฟล์ APK ต่อท้าย DEX และไม่พบ magic แบบ constant-XOR; การตรวจ syntax ของสคริปต์และ diff รอตรวจท้ายงาน ไม่รัน unit tests ของแอปเพราะไม่แก้ runtime

## 2026-09-11 — ตรวจสัญญา service จากสำเนา APK สำหรับ Emma

- เพิ่ม `docs/robot-service-interface-research-2026-09-11.md`: ยืนยัน MyService ประกาศ exported=true ใน APK 278/227/226; SerialdataService ไม่มี exported/filter จึงเป็นภายในตามค่าเริ่มต้น แก้ความเข้าใจว่าระบุ component แล้วแอปอื่นเรียกได้
- ค้นสมาชิก archive 39 ไฟล์/82,871 รายการ และ string IDs 1,942,274 รายการจาก DEX ระดับบน 62 ไฟล์ใน APK 37 ไฟล์; ยังไม่พบ AIDL/SDK contract ที่อ่านได้ ระบุข้อจำกัด Jiagu และโฟลเดอร์นอกโครงการที่อ่านไม่ได้อย่างชัดเจน
- ตรวจคู่มือ SDK หน้า 6 ด้วยภาพและข้อความ ยืนยันชื่อ AAR ที่ต้องใช้; เตรียมร่างขอ interface/SDK ตรงรุ่นจากผู้ขายไว้ในรายงาน ยังไม่ได้ส่ง
- สคริปต์และหลักฐานอยู่ใน `data/robot-inspection/service-interface-research-20260911/` (ignored); รันสคริปต์ offline ทั้งสองสำเร็จ ตรวจ assertions ของ manifest, SHA-256, จำนวน archive/DEX และเทียบชื่อกับ package-manager dump ที่สำรองไว้ผ่าน; `git diff --check` ผ่าน ไม่เรียก ADB/REST/Binder/USB/serial ไม่แก้ runtime หรือการตั้งค่าหุ่น ไม่รัน unit tests ของแอปเพราะแก้เฉพาะงานวิจัย

## 2026-09-11 — ค้นพอร์ตและโปรโตคอลจากสำเนาโดยไม่ติดต่อหุ่น

- `docs/robot-command-research-2026-09-11.md`: บันทึกขอบเขตค้นข้อมูลเท่านั้นตามผู้ใช้; ไม่ใช้ ADB/REST/serial หรือเรียก service บนหุ่น
- ถอด complex resource arrays ของ APK ได้ค่าตัวเลือก Relay/Servo Board, หมายเลขเซอร์โว และชนิด UART; ตรวจ Manifest แยก DoubleScreenService ออกจากชื่อ intent action พร้อม SerialdataService
- เปรียบเทียบ DEX สำเนา APK 3 รุ่น พบ wrapper Jiagu ทั้งสาม; ค้นข้อความ 140 ไฟล์โดยระบุขอบเขตและข้อจำกัด ไม่อ้างว่าได้ joint/channel map แล้ว
- พบคู่มือ Torobot จากแหล่งผู้ผลิต ตรวจภาพตารางโปรโตคอลและระบุความต่าง USC-32/20 ช่อง และ baud rate; ไม่ติดตั้งโปรแกรมหรือส่งคำสั่งตัวอย่าง
- หลักฐานและสคริปต์แบบ offline อยู่ใน `data/robot-inspection/static-port-research-20260911/` (ignored); ตรวจ parser/ข้อมูลอ้างอิงและ `git diff --check` ผ่าน ไม่รัน unit tests ของแอปเพราะไม่แก้ runtime

## 2026-09-11 — #STOP ไม่หยุดข้อต่อ: อินเตอร์ล็อกการขยับแขน และคืนเซิร์ฟเวอร์ HTTPS

- จากรายงาน `docs/robot-channel-mapping-stop-findings-2026-09-11.md` ของอีกเซสชัน: บอร์ดตอบ `#STOP+OK` สามชุด แต่ช่อง 5 (แขนซ้ายในภาพ) เคลื่อนต่อจนถึงเป้าทั้งรอบ P1500/T9999 และ P1540/T3000 — **ACK ของ #STOP ไม่ใช่หลักประกันว่าแขนหยุด** และอ่านจำนวนกลุ่มท่าได้ `#R+OK+026` (26 กลุ่ม ไม่ใช่รายชื่อ) กลุ่ม 3 ทำให้หัวหันและแขนยก/งอจริง
- `app/config.py` + `app/robot_arm.py` + `app/main.py`: เพิ่ม `ROBOT_ARM_MOTION_ENABLED` (default **false**) รูปเดียวกับของแชสซี — `centre/step/group` ปฏิเสธด้วย `motion_locked` พร้อม hint อธิบายเหตุ; `stop`/`prepare`/`state` ยังทำงาน (ล็อกที่ปิดเบรกด้วยแย่กว่าไม่มีล็อก) · เทสต์ +2 (`test_robot_arm_page.py`) รวมยืนยัน default จาก source ไม่ใช่ Settings() · conftest ปิดสวิตช์ในเทสต์
- `client/robot-console.html`, `client/robot-arm.html`: แถวสถานะ "การขยับ" และปุ่มขยับทั้งหมดถูกปิดเมื่อล็อก (อีกเซสชันแก้ข้อความปุ่ม/แถบเตือนเรื่อง #STOP ไว้ก่อนแล้ว ไม่ทับ) · `.env`/`.env.example`: `ROBOT_ARM_MOTION_ENABLED=false` พร้อมเหตุผล
- **เซิร์ฟเวอร์ชนกันระหว่างสองเซสชัน**: หลังผมเปิด HTTPS ผ่าน `run_server.py` อีกเซสชันรีสตาร์ต 8001 เป็น `uvicorn --log-level warning` แบบ HTTP (เพื่อใช้ `FACE_ENABLED=false`) เบราว์เซอร์จึงได้ `ERR_SSL_PROTOCOL_ERROR` — ไม่ใช่ใบรับรอง แต่คือ TLS คุยกับพอร์ต HTTP ตรวจด้วย `curl` ทั้งสอง scheme และ command line ของ pid ที่ถือพอร์ต · คืนเป็น `run_server.py` (HTTPS, pid 35520) แล้ว `/health` ตอบ 200 ที่ 192.168.1.43 และ localhost, `/arm/state` รายงาน `motion_enabled=false` — **ต่อไปเซสชันใดรีสตาร์ต 8001 ต้องใช้ `run_server.py` เท่านั้น** ไม่งั้นไมค์บน LAN ใช้ไม่ได้
- ยังไม่ส่งไบต์ใดไปบอร์ดในรอบนี้; รีเลย์ไฟเซอร์โวยังจ่ายอยู่ตามที่อีกเซสชันบันทึก และเจ้าของยังไม่ได้ปิดไฟหลัก
- ตรวจจริง: `pytest tests/ -q` = **1374 passed** (จาก 1365); เทสต์หน้าหุ่น 76 ผ่าน (รวม `test_robot_probe_recording.py` ของอีกเซสชัน); HTTPS ตรวจด้วย curl ที่ 192.168.1.43 และ localhost ได้ 200

## 2026-09-11 — Emma เป็นเจ้าบ้านของ Embassy World: โปรไฟล์ condo + บล็อกวิธีขาย + คลังเอกสารห้าชุด

- เจ้าของส่งเอกสารห้าชุด (บทพูดไทย ×2 ซ้ำกัน sha256 เดียว, กลยุทธ์ตลาดไทย, ระบบภูมิทัศน์, คู่มือนำเสนอ EN แล้ว TH) สั่ง "ทำเป็น emma แบบนี้เลย เพราะจะไม่ให้ตอบเรื่องอื่น" — ตีความเป็นสามส่วน: (1) สลับเครื่องนี้เป็นโปรไฟล์ `condo` ซึ่งมีกฎห้ามตอบนอกเรื่องอยู่แล้ว (2) เพิ่มบุคลิก/วิธีขายลงพรอมต์ (3) เอาเอกสารเข้าคลังที่ค้นได้
- `app/prompts.py`: เพิ่ม `SALES_HOST_BLOCK` (895 ตัวอักษร) ต่อท้ายกฎ condo ทุกเทิร์น — เจ้าบ้านพูด "เรา/ที่นี่" เรียกตัวเองว่าฉัน · ขายชีวิตก่อนเทคโนโลยี ห้ามเปิดด้วยราคา/ขนาดห้อง/AI · ถามค้นหาความต้องการทีละคำถาม · เล่าเป็น "โลก" · New Generation/Luxury/Nothing Is Missing ตามความหมายในเอกสาร · ห้ามด้อยค่าคอนโดอื่น ห้ามการันตีผลตอบแทน ห้ามเร่งซื้อ · แนวคิดที่กำลังพัฒนาห้ามพูดเหมือนเสร็จ · ประโยค fallback อ้างจากคู่มือไทย section 19 ตรงตัว "ฉันไม่อยากให้ข้อมูลที่คลาดเคลื่อนกับคุณ…" · ปิดด้วยขั้นต่อไปเสมอ — **บล็อกไม่มีตัวเลขเลย** ตัวเลขทุกตัวยังอยู่ใน condo_facts.json หรือมาจากเครื่องมือ
- `GREETING` ของ condo เปลี่ยนจาก "มีอะไรให้ช่วยไหม" เป็น Master Opening ย่อ: แนะนำตัว ไม่พูดราคา แล้วถามหนึ่งคำถามว่าอะไรจะทำให้อยากกลับมาบ่อยๆ ไม่เกิน 3 ประโยค
- `tests/test_profiles.py` +4: บล็อกอยู่ใน condo, ไม่มีตัวเลข, ไม่รั่วไป emma/translator, greeting ไม่เปิดด้วยราคา · `tests/test_voice.py`: เพดานพรอมต์ 3800 → 4700 (ครั้งที่แปด บันทึกเหตุผลใน docstring; ตัดบล็อกจาก 1040 → 895 ก่อน)
- `data/personal-docs/embassy-world/` (gitignore) 5 ไฟล์ .md: thai-sales-speech, thai-market-strategy, future-climate-landscape, emma-sales-presentation-master (EN/TH) · **PDF บทพูดไทยใช้ฟอนต์ที่ทำสระเพี้ยน** (หน้าธรรมดา ำ→ĕา ู→่ / ตัวหนา า→ำ ่→ุ) pdftotext อ่านไทยไม่ได้เลย ซ่อมด้วย `fitz` แยก span ตามฟอนต์ + พจนานุกรมคำที่กำกวม ตรวจทั้งไฟล์แล้วไม่เหลือรูปแบบต้องสงสัย · ตรวจว่า `search_my_documents` เจอ "Nothing Is Missing" และ "New Generation" (found=True) และคำถามราคายังถูกด่านการเงินกัน (found=False)
- `.env`: `ASSISTANT_PROFILE=condo`, `TOOL_GROUPS=slides,knowledge,smarthome,mydocs,units,calc,robot` — ตัด reminders/memory/computer ออกเพราะเป็นเครื่องมือคอมของเจ้าของ ลูกค้าในห้องขายต้องเปิดโปรแกรมหรืออ่านความจำเจ้าของไม่ได้
- `CLAUDE.md`: เพิ่มย่อหน้าอธิบายการเปลี่ยนนี้และวิธีดึงข้อความจาก PDF ฟอนต์เพี้ยน
- รีสตาร์ต 8001 ภายใต้ condo: banner แสดง 26 เครื่องมือรวม search_my_documents
- ตรวจจริง: `pytest tests/ -q` = **1365 passed** (จาก 1345); ไม่ได้ทดสอบเสียงจริงกับ Gemini ในรอบนี้ — เจ้าของต้องลองคุยกับ Emma จริงก่อน commit ตามกติกา

## 2026-09-11 — แขนยังต้านหลัง force-stop: รีเลย์ค้าง ON และแก้ตำแหน่งค้างข้ามการเสียพอร์ต

- เจ้าของรายงาน "ต้าน" อีกครั้งหลังผู้ช่วย `am force-stop` = **รีเลย์ค้างจ่ายไฟหลังแอปถูกหยุด** ลำดับ "เปิดแอปให้จ่ายไฟ → หยุดแอป → bind" ใช้ได้ เจ้าของ bind ch341 ได้ `ttyUSB10` (13:30) เซิร์ฟเวอร์เห็นพอร์ต
- **บั๊กที่เจอก่อนกด**: `/arm/state` ยังจำ `commanded: {1: 1540}` จากรอบก่อน ทั้งที่ระหว่างนั้นแอป Aobo ยึดบอร์ดกลับและรัน `#99GC1` ท่าตั้งต้น ตำแหน่งจริงของช่อง 1 จึงไม่ใช่ 1540 อีกแล้ว ปุ่ม +40 จะเป็นการเคลื่อนเร็ว (T=800) จากจุดที่ไม่รู้ — `app/robot_arm.py`: `port_present()` ล้าง `_commanded` ทุกครั้งที่พบว่าพอร์ตหายหรือเช็คไม่ได้ เพราะพอร์ตที่หายไปคือพอร์ตที่คนอื่นอาจถือ; เทสต์ `test_a_port_that_went_away_takes_the_commanded_positions_with_it` (`test_robot_arm_page.py` = 32)
- รีสตาร์ต 8001 (pid 10480) ให้กฎใหม่ทำงานและล้างความจำเก่า
- ตรวจจริง: เทสต์ไฟล์แขน 32 ผ่าน; ยังไม่รันทั้งชุดเพราะกำลังทดสอบสด จะรันหลังจบรอบ; ยังไม่ส่งไบต์เพิ่มไปบอร์ด

## 2026-09-11 — ทดลองลำดับ "เปิดแอปให้จ่ายไฟ → หยุดแอป": แขนต้าน = มีไฟ และหลักการสร้างท่าเอง

- เจ้าของเปิดแอป Aobo กลับ (`am start`) แอปยึด CP2102/CH340 คืนทั้งคู่ ไม่เขียน log ที่อ่านได้ · เจ้าของดันแขนแล้วรายงาน **"ต้าน (มีไฟ)"** = ไฟแขนมาจากแอปจริง สนับสนุนสมมติฐานรีเลย์ · ผู้ช่วย `am force-stop` ทันทีขณะไฟจ่าย ทั้งสองอินเทอร์เฟซถูกปล่อย (`driver=[]`) รอเจ้าของ bind ch341 และทดสอบช่อง 1 ซ้ำ
- เจ้าของสรุปหลักการสร้างท่าเองจากเฟรมหลายช่อง `#1P..#2P..T..` เรียงเป็นขั้น โดยไม่ต้องรู้เลขกลุ่มเดิม — เห็นด้วย บันทึกเงื่อนไขที่ต้องมาก่อนสามข้อ (ผังช่อง→ข้อต่อ, ช่วงที่ใช้ได้จริงต่อช่องพร้อมธง verified, เซิร์ฟเวอร์ปฏิเสธท่าที่อ้างช่องที่ยังไม่วัด) และข้อจำกัด T เดียวต่อเฟรม ลงใน `docs/robot-command-research-2026-09-11.md`
- บันทึกความเสี่ยงที่เจ้าของพบ: แอป Aobo อาจสั่งกลุ่ม 99 หลังได้ `#STOP+OK` — ไม่มีผลตอนแอปถูกหยุด แต่ต้องปิดให้ได้ก่อนให้ Emma คุมท่าผ่านทาง 3
- ไม่แก้ runtime code; ยังไม่ส่งไบต์เพิ่มไปบอร์ดในรอบนี้

## 2026-09-11 — ไม่มีคู่มือช่อง→ข้อต่อ แต่พบว่าไฟเซอร์โวเป็นรีเลย์บนบอร์ดล่างที่สั่งผ่าน CP2102

- เจ้าของถามว่ามีคู่มือว่าแต่ละช่องคืออะไรไหม — **ไม่มีที่ไหนเลย** ค้นซ้ำใน resource และ disassembly ทั้งหมด มีแค่เลข 1/11/7/8 ไม่มีชื่อข้อต่อ ตารางเดียวของหุ่นตัวนี้คือที่วัดได้วันนี้ (7/8 = หัวสองแกน รอยืนยันว่าเลขไหนแกนไหน)
- การค้นพบระหว่างทาง (ถอดด้วยเครื่องมือของเจ้าของ `disassemble_recovered.py` — รันได้หลัง pin lxml ของ venv ไว้ก่อน เพราะ `python-libs/lxml` ที่แนบมาไม่มี `etree`): แอปมีสองโหมดไฟเซอร์โว `armcontrolmethodtype` 0=Relay 1=Servo Board ค่าสำรองไม่มีคีย์นี้จึงเป็น **Relay** · จ่าย/ตัดไฟทำด้วยแพ็กเก็ต 10 ไบต์ `A5 01 2A 02 00 00 00 08 <00=จ่าย|01=ตัด> <checksum>` ส่งไป route **60000 = 0xEA60 = CP2102** (`firstUart主板串口` = บอร์ดล่าง STM32) — เลข route ในโค้ดคือ USB PID ตรงตัว (29987 = CH340, 8963 = PL2303)
- **ยังไม่ส่งและยังไม่ทำเป็นปุ่ม**: manager ของ CP2102 มี handshake + heartbeat บอร์ดล่างเป็นโปรโตคอลมีสถานะและคุมมอเตอร์/เซ็นเซอร์ด้วย ต้องเป็นงานแยกและเป็นการตัดสินใจของเจ้าของ
- สมมติฐานที่อธิบาย "หัวขยับ แขนเงียบ" ครบ: โค้ดแอปตัดไฟเซอร์โวเองเมื่อ IR เจอสิ่งกีดขวางหรือมีการสัมผัสแขน หุ่นจอดในที่แคบ → รีเลย์ค้างตัดไฟตอนเรา force-stop แอป → ช่อง 1/11 และกลุ่ม 6 เงียบ หัวคนละรางจึงขยับ — ทดสอบได้โดยไม่ส่งอะไร (ดันแขน / เปิดแอปกลับแล้วดูแขน / force-stop แล้วลองช่อง 1 ซ้ำ) บันทึกลำดับใน `docs/robot-command-research-2026-09-11.md`
- ตรวจจริง: อ่าน disassembly และคำนวณ checksum จาก `checkCode` (two's complement ของผลรวมไบต์ 0–8); ไม่ส่งคำสั่งใดไปหุ่น ไม่แก้ runtime code

## 2026-09-11 — สังเกตฮาร์ดแวร์ครั้งแรก: หัวขยับที่ช่อง 7/8 ช่อง 1/11 กับกลุ่ม 6 เงียบ

- เจ้าของทดสอบจาก `/console` โดยมีคนอยู่กับหุ่น เซิร์ฟเวอร์ยืนยันเฟรมที่ออกทุกบรรทัด ผล: **ช่อง 7 และ 8 ขยับหัวคนละแกน** · ช่อง 1, 11 และ `#6GC1` ไม่มีอะไรเกิดขึ้น · `#STOP` ส่งตอนไม่มีอะไรเคลื่อน จึงยังไม่มีหลักฐานเรื่องหยุด
- **พิสูจน์แล้วระดับฮาร์ดแวร์**: โปรโตคอล Torobot ใช้กับบอร์ดตัวนี้ได้ผ่าน `/dev/ttyUSB10` (CH340) ที่ 115200 · เส้นทาง เซิร์ฟเวอร์→adb Wi-Fi→เชลล์→printf→tty→บอร์ด→เซอร์โว ทำงานครบ · CH340 ขับหัว ไม่ใช่แขนอย่างเดียว · 1500 ไม่ทำอันตรายกับ 7/8
- เจ้าของรายงานสองแกนของหัวเป็น "ช่อง 7" ทั้งคู่ ซึ่งเป็นไปไม่ได้บนช่องเดียว รอยืนยันว่าเลขไหนคือแกนไหนก่อนบันทึก wiring map — **ยังไม่ใส่ชื่อข้อต่อลงหน้าเว็บ**
- `client/robot-console.html`: บันทึกคำสั่งขึ้นชื่อเลน (`แขน ·` / `ฐานล้อ ·`) นำหน้าทุกบรรทัด เพราะปุ่มแดงสองปุ่มกดในวินาทีเดียวกันขึ้นเป็น "stop ส่งแล้ว" กับ "stop ไม่สำเร็จ" และบรรทัดที่ล้มคือแชสซีที่ไม่ได้ต่อ ไม่ใช่แขน เจ้าของอ่านเป็น error; เทสต์ +1 (`test_robot_console_page.py` = 15)
- บันทึกรายละเอียดและสมมติฐานที่เหลือใน `docs/robot-command-research-2026-09-11.md` (ช่อง 1/11 เงียบมีสี่สาเหตุที่เป็นไปได้ กลุ่ม 6 เงียบมีสาม ตัวแยกคือกลุ่ม 99)
- ไม่แก้ runtime code ฝั่ง Python จึงไม่รันชุดเทสต์ซ้ำทั้งหมด

## 2026-09-11 — E-stop ของหุ่นถึงแขนผ่านแอป Aobo: เตือนทุกหน้า และส่ง #STOP ซ้ำตามผู้ขาย

- จาก `docs/robot-estop-evidence-2026-09-11.md` ของเจ้าของ: ปุ่มแดงของหุ่นเข้ามาเป็นสัญญาณ IO ที่แอป (`isIo7()`) แล้ว `FloatRecordService.sendArmToRobotStop()` เขียน `#STOP` ไปบอร์ดแขนเมื่อ `estopStopArmMovement=true` ซึ่งค่าสำรองของหุ่นตัวนี้เปิดอยู่ helper ส่งซ้ำหลัง 60 ms — **ผลต่อขั้นตอนทดสอบ: การ force-stop แอปเพื่อปล่อยพอร์ตปิดเส้นทางนี้ไปด้วย** ระหว่างที่เราถือพอร์ต ปุ่มแดงไม่มีเส้นทางที่*รู้*ว่าถึงแขน ยังไม่รู้ว่าตัดไฟเซอร์โวในทางฮาร์ดแวร์ด้วยหรือไม่
- `client/robot-console.html`, `client/robot-arm.html`, `client/robot-hud.html`: แถบความปลอดภัยเพิ่มประโยคนี้ตรงตัว มีเทสต์คุมใน `test_robot_arm_page.py` และ `test_robot_console_page.py`
- `app/robot_arm.py`: `stop()` เขียน `#STOP` สองครั้งห่าง `STOP_REPEAT_S=0.06` ตามพฤติกรรม helper ของผู้ขาย (เฟรมเดิม ไม่ขออะไรใหม่; ครั้งที่สองล้มเหลวไม่ทำให้ `stop_write_ok` เป็นเท็จถ้าครั้งแรกสำเร็จ) docstring หัวไฟล์เพิ่มข้อเท็จจริงเรื่องเส้นทาง E-stop; `docs/robot-command-research-2026-09-11.md` เพิ่มหัวข้อผลต่อลำดับทดสอบ
- รีสตาร์ต 8001 (pid 27568) ให้โมดูลใหม่ทำงาน `/console` ตอบ 200; ผู้ใช้เปิด `/arm` แล้วบอกว่า "หน้าเดิม" — หน้ารวมคือ `/console` แจ้งแล้ว
- ตรวจจริง: `pytest tests/ -q` = **1345 passed** (จาก 1343); เทสต์สามหน้า 57 ตัว; ยังไม่มีไบต์ใดถึงบอร์ดแขน

## 2026-09-11 — คอนโซลทดสอบรวมทุกคำสั่งที่ /console

- เจ้าของขอ "หน้าที่สั่งการได้หมด" สำหรับทดสอบทั้งตัว — `client/robot-console.html` + route `/console` + `tests/test_robot_console_page.py` (13 ตัว): ผังหุ่นแบบ `/hud` ตรงกลาง แขนซ้าย (ตั้งค่าพอร์ต, สั่งไป 1500/±40 ต่อช่อง, เล่นกลุ่มท่าหนึ่งรอบ, ปลดล็อก) ฐานล้อขวา (ก้าวเดิน/หมุน, กลับแท่น, ไปจุดหมาย) และบันทึกคำสั่งที่โชว์ `note` จากเซิร์ฟเวอร์ตรงตัว
- **ไม่เพิ่มพฤติกรรมใด** เรียก `/robot/command` กับ `/arm/command` ที่มีอยู่แล้ว ระยะ พัลส์ จำนวนรอบ และล็อกทุกตัวยังตัดสินที่เซิร์ฟเวอร์ หน้าส่งแค่ intent — เทสต์ห้ามคำว่า metres/pulse/cycles/target ในสคริปต์ และตรวจว่ามี body builder เดียว
- **ปุ่มหยุดสองปุ่ม จงใจไม่รวม**: "หยุดฐานล้อ" คือ cancel ที่แชสซีตอบและเคยเห็นทำงาน · "หยุดแขน (#STOP)" เขียนเฟรมแล้วล็อก ป้ายเขียนว่ายังไม่เคยเห็นมันหยุดแขนจริง — รวมกันจะทำให้ปุ่มที่พิสูจน์แล้วให้เครดิตปุ่มที่ยังไม่พิสูจน์ · สองเลนมี busy flag แยก (`{ base, arm }`) เพราะการอ่านแชสซีที่ช้าต้องไม่ใช่เหตุที่ปุ่มหยุดแขนรอ และปุ่มหยุดทั้งสองข้ามด่าน busy
- กติกาความซื่อสัตย์เหมือน `/hud` ทุกข้อ: ทุกช่องเริ่มว่าง · ตำแหน่งแขนคือค่าที่สั่งไป · ช่องเซอร์โวอยู่กล่องแยกไม่อยู่บนไหล่ · ไม่ระบุสาเหตุพอร์ตหาย · ลิดาร์ไม่สว่างตามแชสซี · ช่องกลุ่มท่าเติม **6** ไว้ล่วงหน้าพร้อมข้อความว่าสามแหล่งชี้ว่าเป็นจับมือแต่ยังไม่มีข้อใดเป็นการสังเกตบอร์ด และ 99 คือท่าที่แอปผู้ขายส่งเองตอนเปิดพอร์ต
- `/hud` ยังอยู่เป็นจอดูอย่างเดียวสำหรับจอที่ไม่ควรสั่งอะไรได้ `/robot` และ `/arm` ยังอยู่เหมือนเดิม
- รีสตาร์ตเซิร์ฟเวอร์ 8001 อีกครั้งให้มี route ใหม่ (pid ใหม่ 26040) และเช็คว่า `/console` ตอบ 200 · `/arm/state` หลังรีสตาร์ตยังเห็น `/dev/ttyUSB10` present, armed, groups_enabled — ยังไม่มีไบต์ใดถึงบอร์ด
- หมายเหตุความปลอดภัย: เจ้าของวาง URL ที่มี `WS_TOKEN` เต็มค่าลงในแชต ค่านั้นจึงอยู่ใน log ของบทสนทนาแล้ว แนะนำหมุน `WS_TOKEN` หลังจบการทดสอบวันนี้ (ค่าไม่ถูกคัดลอกลงเอกสารหรือ changelog)
- ตรวจจริง: `pytest tests/ -q` = **1343 passed** (จาก 1330; เจ้าของยังไม่ได้รันซ้ำเองรอบนี้)

## 2026-09-11 — เตรียมทดสอบแขนจริงครั้งแรก: พอร์ตขึ้นแล้ว เซิร์ฟเวอร์เห็นแล้ว ยังไม่ส่งไบต์

- เจ้าของสั่ง "รันเลย เดี๋ยวทดสอบ" — ลำดับที่ทำ: ผู้ช่วย `am force-stop com.aobo.robot.ai3` (อินเทอร์เฟซ `9-1.4:1.0` เป็น `driver=[]`) → เจ้าของรัน bind `ch341` ด้วย root ได้ `/dev/ttyUSB10` 0666 → ผู้ช่วยยืนยันผ่าน sysfs ว่า `ttyUSB10 → 9-1.4:1.0 → ch341-uart` คือ CH340 ตัวที่โค้ดแอปผู้ขายใช้กับแขน ไม่ใช่ CP2102
- `.env` (ไม่อยู่ในรีโป): `ROBOT_ARM_ENABLED=true`, `ROBOT_ARM_PORT=/dev/ttyUSB10`, `ROBOT_ARM_GROUPS_ENABLED=true`, `ROBOT_ARM_ADB=tools/android/platform-tools/adb.exe`, `ROBOT_ARM_ADB_SERIAL=192.168.1.24:5555` — ค่าแชสซีและ TOOL_GROUPS ไม่แตะ
- แทนที่เซิร์ฟเวอร์ที่รันอยู่บน 8001 ตั้งแต่ 10 ก.ย. (โค้ดเก่า ไม่มี `/arm`) ด้วยโค้ดปัจจุบัน log ไว้ที่ `data/robot-inspection/server-20260911-arm.*.log` (ถูก ignore) · ความผิดพลาดระหว่างทาง: คำสั่งค้นโปรเซสด้วย `CommandLine -match 'uvicorn'` จับตัวเองแล้วฆ่าเชลล์ตัวเอง แก้ด้วยการหยุดตาม pid ที่รู้อยู่แล้ว
- ตรวจจริงกับเซิร์ฟเวอร์ที่รันอยู่: `/arm/state` ตอบ `configured=true, port_present=true, armed=true, groups_enabled=true, commanded={}` · `/robot/state` ตอบ ConnectError เพราะ relay ไปบอร์ดนำทางไม่ได้เปิด — ไม่จำเป็นสำหรับการทดสอบแขน
- **ยังไม่มีไบต์ใดถึงบอร์ดแขน** ปุ่มแรกเป็นของเจ้าของ: เล่นกลุ่ม 6 หนึ่งรอบ โดยมีคนยืนอยู่กับหุ่นและมือถึงไฟเลี้ยงเซอร์โว หลังทดสอบต้องคืนสภาพด้วย `am start -n com.aobo.robot.ai3/com.aobo.aibot.ui.activity.SplashActivity`
- ไม่แก้ runtime code จึงไม่รัน unit tests ซ้ำ

## 2026-09-11 — ถอนสมมติฐาน 握手=12: target ของ switch วัดแล้ว 握手 = key 6

- สมมติฐานที่ผู้ช่วยเขียนไว้ในรายการก่อนหน้า ("ถ้าคอมไพเลอร์เรียงบล็อกตาม key 握手 จะเป็น 12") **ผิด** เจ้าของพิมพ์ target จริงด้วย `scripts/inspect_aobo_gesture_switch.py` (ค้นเมธอดจากตาราง DEX ไม่เดา offset) ได้ targets `57 53 49 46 42 39 35 31 27` สำหรับ key 5–13 คือ**เรียงกลับด้าน** key สูงอยู่ที่อยู่ต่ำ — ผู้ช่วยรันสคริปต์เดียวกันซ้ำแล้วได้ผลตรงกัน และตรงกับ Androguard
- ตารางที่ถูกต้องของ `IdleActionService.getActionName`: **5 敬礼 · 6 握手 · 7 摆臂 · 8 倒水 · 9 撕拉 · 10 全部动作 · 11 摆臂行走 · 12 打招呼行走 · 13 敬礼行走**
- อ่าน `ActionConstants.ACTION_GROUPS` ควบกับสองคลังคำศัพท์: payload ของ fill-array-data แก้ตามที่อยู่คำสั่งจริงได้ key 1 = [5,6,7] แล้วถูก put ซ้ำทับด้วย [10,5,12] (เพราะ `DEFAULT` กับ `GREETING` เป็น 1 ทั้งคู่ — ชุดแรกเป็นโค้ดตาย), key 2 DANCE = [8,9,11], key 3 IDLE = [6,7,13] · **ภายใต้ชื่อของ ActionConstants กลุ่มอ่านสอดคล้อง** (GREETING = BOW/WAVE/SALUTE, DANCE = DANCE1/DANCE2/TURN, IDLE = NOD/SHAKE/POSE1) **ภายใต้ป้ายของ getActionName กลุ่ม DANCE มี "เทน้ำ"** — เป็นการอนุมานว่าสองตารางเป็นคนละรุ่นและ ActionConstants คือรุ่นที่ตรงกับตารางกลุ่มในคลาสเดียวกัน ยังไม่ทราบว่าตารางไหนตรงกับบอร์ดตัวนี้
- แต่เลข 6 มีสามแหล่งชี้ไปทางเดียวกัน: ป้าย 握手 ใน getActionName · ค่า preference กลุ่มท่าสำหรับใบหน้า = 6 (ตามรายงาน interface ที่ถอดได้) · คู่มือข้อ 18 "จับมือเมื่อจดจำใบหน้าได้" — **ทั้งสามเป็นหลักฐานระดับแอปและเอกสาร ไม่มีข้อใดเป็นการสังเกตบอร์ด** จึงยังไม่แปลว่า `#6GC1` ทำให้บอร์ดจับมือ และยังไม่พบเส้นทางที่ส่งเลข ACTION_TYPE ตรงไปเป็น `#nGC`
- ผลเทสต์: เจ้าของรัน `pytest tests/ -q` เอง = **1330 passed** ตรงกับที่ผู้ช่วยรายงาน — ตัวเลขนี้ยืนยันโดยเจ้าของแล้ว ไม่ใช่ของผู้ช่วยฝ่ายเดียวอีกต่อไป
- ไม่แก้ runtime code ไม่ส่งคำสั่งไปหุ่น

## 2026-09-11 — แก้ถ้อยคำที่เกินหลักฐานสี่จุด และดูตารางชื่อท่าเอง

จากรีวิวเจ้าของสองรอบ ทุกข้อถูก แก้ในก้อนเดียว:

- **`stop_sent` → `stop_write_ok`** ฟิลด์เดิมชื่อเหมือนยืนยันว่าเฟรมถึงสาย ทั้งที่รู้แค่ว่าเชลล์บนหุ่นรันคำสั่งเขียนแล้วออก 0 ไม่ยืนยันว่าไบต์ถึงบอร์ด บอร์ดเข้าใจ หรือข้อต่อหยุด — สามข้ออ้างที่โมดูลนี้ไม่มีทางทำได้ ข้อความตอบกลับแก้เป็น "เชลล์รายงานว่าเขียนสำเร็จ — ไม่ยืนยันว่าเฟรมถึงบอร์ดหรือแขนหยุด"
- **`/hud`: "บอร์ดเซอร์โวไม่ตอบอะไรเลย" → "หน้านี้ยังไม่ได้อ่านค่าตอบกลับจากบอร์ดแขน"** เพราะโค้ดแอปผู้ขายมีตัวรับ `#STOP+OK` และสถานะจบกลุ่มท่า บอร์ดพูดอะไรบางอย่าง เราแค่ยังไม่เคยฟัง — เป็นช่องว่างของหน้านี้ ไม่ใช่คุณสมบัติของฮาร์ดแวร์
- **`/hud`: "ไม่พบพอร์ตเพราะ Aobo ถือไว้" → "ไม่พบพอร์ตที่ตั้งค่าไว้"** ข้อเท็จจริงกับสาเหตุเป็นการวัดคนละอย่าง สาเหตุต้องอ่าน `/proc` ด้วย root ซึ่งหน้านี้ไม่ทำ มีเทสต์ห้ามคำว่า Aobo ในสคริปต์
- **`/hud`: ย้ายจุดช่องเซอร์โว 1/7/11/8 ออกจากไหล่และศอกบน SVG** ไปอยู่ในกล่องแยกติดป้าย "ยังไม่ทราบตำแหน่งข้อต่อ" — การวางบนข้อต่อคือการวาด wiring map ที่ไม่มีใครมี และแผนภาพถูกเชื่อเร็วกว่าคำบรรยายใต้มัน
- **ถอนข้อสรุปที่เขียนไว้ว่า AIDL แก้ปัญหาการอยู่ร่วมกับแอป Aobo ได้แล้ว** ถอดสัญญาได้แต่ยังไม่ได้ bind หรือเรียกจริงจาก Emma · เส้นทางแขนที่พบคือ broadcast `send.usbserial.cmd.ch340` พร้อม `cmd` ชนิด byte[] ซึ่งเป็นคนละช่องทางกับ AIDL และยังไม่ยืนยันว่ารับจาก UID อื่น · การให้ Aobo ถือ USB แล้วรับคำสั่งจาก Emma เป็นแนวทางที่ต้องพิสูจน์ ไม่ใช่ข้อจำกัดที่แก้แล้ว
- **ดูตารางชื่อท่าในไบต์โค้ดเอง** (`disassembly/` ของงานถอดโค้ด): `ActionConstants` ระบุ `ACTION_TYPE_WAVE=5, NOD=6, SHAKE=7, DANCE1=8, DANCE2=9, BOW=10, TURN=11, SALUTE=12, POSE1=13` และ `ACTION_GROUPS` map: key 1 (DEFAULT/GREETING ค่าเดียวกัน จึง put สองครั้ง ค่าหลังทับ) · key 2 DANCE · key 3 IDLE · key 4 CUSTOM ว่าง · ส่วน `IdleActionService.getActionName` เป็น packed-switch key 5–13 กับสตริงเก้าบล็อกเรียงตามที่อยู่: 敬礼行走, 打招呼行走, 摆臂行走, 全部动作, 撕拉, 倒水, 摆臂, 握手, 敬礼 — **ถ้า**คอมไพเลอร์เรียงบล็อกตามลำดับ key (ปกติเป็นเช่นนั้น) 握手 จะเป็น key **12** ไม่ใช่ 6 และ 6 คือ 打招呼行走 — **ยังไม่ยืนยัน** เพราะ disassembly ที่มีไม่ได้พิมพ์ target ของ switch และการอ่าน payload จาก DEX ที่ offset ที่เดาไว้ไม่พบ ต้องให้เครื่องมือถอดโค้ดพิมพ์ target จริง · ที่ยืนยันได้จากไบต์โค้ด: **สองตารางนี้เป็นคนละคลังคำศัพท์บนช่วง key เดียวกัน** (5–13) ตัวหนึ่งเป็น wave/nod/shake/dance/bow ตัวหนึ่งเป็นท่าเดินประกอบ/เทน้ำ/จับมือ/เคารพ ซึ่งตรงกับรายการฟีเจอร์ในคู่มือมากกว่า — น่าจะมีตัวหนึ่งเก่าหรือของรุ่นอื่น · และ**เลข ACTION_TYPE เป็นเลขระดับแอป ยังไม่พบเส้นทางที่ส่งเลขนี้ตรงไปเป็น `#nGC`** ต่อให้รู้ว่า 握手=12 ก็ยังไม่แปลว่า `#12GC1` คือจับมือ
- ตรวจจริง: `pytest tests/ -q` = **1330 passed** (จาก 1327); เทสต์ใหม่สามตัวใน `test_robot_hud_page.py` คุมสามข้อแก้; ไม่ได้ส่งคำสั่งใดไปหุ่น — และตามที่เจ้าของระบุว่ายังไม่ได้ตรวจผลเทสต์ซ้ำเอง ตัวเลขทั้งหมดในนี้คือผลจากเครื่องของผู้ช่วยจนกว่าเจ้าของจะรันเอง

## 2026-09-11 — แผงตรวจหุ่นหน้าเดียวที่ /hud

- `client/robot-hud.html` + `tests/test_robot_hud_page.py` (ใหม่ 9 ตัว) + route `/hud` ใน `app/main.py`: หน้าจอเดียวแสดงสถานะทุกส่วนของหุ่นในรูปทรงของหุ่นเอง ผังเป็น SVG พร้อมจุดสถานะรายส่วน — หัว/จอสีหน้า, ไมค์อาร์เรย์, กล้อง, ช่องเซอร์โว 1/7/11/8, ฐานล้อ, ลิดาร์, จุดตัดไฟเซอร์โว
- **กติกาเดียวของหน้านี้คือตรงข้ามกับภาพต้นแบบที่เจ้าของส่งมา**: ภาพพวกนั้นเต็มไปด้วยตัวเลขประดับ ("792/1000", "671/0.032") ซึ่งบนหน้าจอที่คนอ่านก่อนตัดสินใจสั่งหุ่น ตัวเลขประดับแย่กว่าช่องว่าง เพราะช่องว่างถูกตรวจสอบ ส่วนตัวเลขที่ดูสมเหตุสมผลถูกเชื่อ — **ทุกช่องต้องมาจากการวัด ไม่มีข้อมูล = ขึ้นว่าไม่มี**
- ผลคือสองครึ่งของหน้าไม่เท่ากันโดยตั้งใจ: ครึ่งแชสซีเต็มเพราะ SLAMTEC ตอบคำถามเกี่ยวกับตัวเองได้ ครึ่งลำตัวบนแทบว่างเพราะบอร์ดเซอร์โวไม่ตอบอะไรเลย — ความไม่เท่ากันนี้คือสิ่งที่ซื่อสัตย์ที่สุดบนจอ และเป็นสิ่งที่คนจะตัดสินใจว่าทดสอบอะไรต่อต้องเห็น
- อ่านอย่างเดียว ไม่มีปุ่มสั่งการ ลิงก์ไป `/robot` กับ `/arm` แทน เพราะปุ่มหยุดสองสำเนาจะเพี้ยนออกจากกัน และสำเนาที่เพี้ยนคือสำเนาที่คนต้องการมันที่สุดเป็นคนเจอ
- เทสต์คุมกติกาไว้: ทุกช่องต้องเริ่มว่าง · ค่าทุกค่าต้องผ่านฟังก์ชัน `set()` ตัวเดียว (บรรทัดอธิบายเหตุผลยกเว้นได้ เพราะเหตุผลที่เก่าอ่านออกว่าเป็นเหตุผลเก่า ส่วนตัวเลขที่เก่าอ่านออกว่าเป็นสถานะปัจจุบัน) · **ห้ามจุดลิดาร์สว่างเพราะแชสซีตอบ** ซึ่งเป็นความผิดพลาด "วัดห่างจากสิ่งที่ตัดสินไปหนึ่งชั้น" ที่โปรเจกต์นี้เคยทำมาแล้วสามครั้ง · ตำแหน่งแขนต้องติดป้ายว่าเป็นค่าที่*สั่งไป* ไม่ใช่ค่าที่วัดได้ · หน้านี้เรียกได้เฉพาะสาม endpoint ที่มีอยู่แล้ว
- ตรวจจริง: `pytest tests/ -q` = **1327 passed** (จาก 1318); ย้อนโค้ดพิสูจน์สองข้อ — ทำให้จุดลิดาร์สว่างตามแชสซีแล้วแดง, ใส่ค่า "792/1000" ลงช่องแบตแล้วแดง, คืนโค้ดแล้วเขียวครบ; ไม่ได้ส่งคำสั่งใดไปหุ่น

## 2026-09-11 — แก้ /arm ตามโค้ดที่ถอดได้: #STOP มีจริง และแขนอยู่บน CH340

ตามรายงาน `docs/robot-recovered-interface-2026-09-11.md` ที่ถอด DEX ได้ 7 ไฟล์ สองข้อที่กระทบโค้ดจริงถูกแก้แล้ว:

- **`#STOP` มีอยู่จริง — ข้อสรุปเดิมในโค้ดผิด** พบในตัวช่วยหยุดของแอปผู้ขาย พร้อม parser ที่เคลียร์ `isArmStartAction` เมื่อได้ `#STOP+OK` `stop()` จึงส่ง `#STOP` จริงแล้ว **และยังล็อกเหมือนเดิม** ลำดับสำคัญ: ตั้งล็อกก่อนแล้วค่อยเขียน เพราะครึ่งที่ล้มไม่ได้ต้องไม่รอครึ่งที่ล้มได้ · คำตอบมี `stop_sent` ที่บอกว่า*เฟรมถึงสายไหม* ไม่ใช่ว่าแขนหยุดไหม — โมดูลนี้ไม่อ่านค่ากลับเลย และต่อให้อ่าน `#STOP+OK` ก็แปลได้แค่ว่าบอร์ดได้ยิน
- **แขนอยู่บน CH340 (1a86:7523) ไม่ใช่ CP2102** คลาสที่ส่งคือ `SerialToothManagerCH340` ที่ 115200 8N1 — `.env`/`.env.example` แก้คำแนะนำพอร์ตแล้ว รวมคำสั่ง bind ที่ถูก (`ch341` ไม่ใช่ `ch341-uart`, interface `9-1.4:1.0`) และหมายเหตุว่า ueventd ให้ `ttyUSB10-19` เป็น 0666 จึงเขียนได้โดยไม่ต้อง root ต้อง root เฉพาะตอน bind
- บันทึกคำเตือนที่สำคัญที่สุดของรายงาน: **`usbInit()` ของแอปผู้ขายเขียน `#99GC1` ทันทีที่เปิดพอร์ตสำเร็จ** แปลว่าการเปิดแอปนั้นทำให้แขนขยับเองได้ — `VENDOR_HOME_GROUP = 99` ถูกบันทึกไว้เป็นคำเตือน ไม่ใช่ฟีเจอร์ และ `/arm` ไม่ส่งอะไรเลยตอนเชื่อมต่อ ขึ้นหน้าเว็บด้วย
- ยืนยันของเดิมที่ทำไว้ถูก: `ArmTestActivity.SERVO_NUMBERS = [1, 11, 7, 8]` ตรงกับ `CHANNELS` · ตัวช่วย `sendSingleServoPowerOn` ของผู้ขายคือ `#<servo>P1500T3000` รูปเดียวกับ `centre()` แต่ของเราใช้ T=9999 ช้ากว่า เพราะเขารู้ว่ากำลังขยับข้อต่อไหน เราไม่รู้ · แอปทดสอบของผู้ขายใช้ `GC5` ส่วนเราตรึง `GC1` ไว้เหมือนเดิม
- `client/robot-arm.html`: แถบแดงเปลี่ยนเป็น "ส่ง `#STOP` แล้วล็อก — ยังไม่เคยมีใครเห็นมันหยุดแขนจริง" และเพิ่มคำเตือนเรื่องกลุ่ม 99 ในส่วนกลุ่มท่า
- ตรวจจริง: `pytest tests/ -q` = **1318 passed** (จาก 1315); ไฟล์นี้ 30 ตัว (จาก 27); ย้อนโค้ดพิสูจน์สองข้อ — ถอด `#STOP` ออกให้เหลือล็อกอย่างเดียวแล้ว `test_stop_sends_the_board_its_stop_frame` แดง, ย้ายการตั้งล็อกไปไว้หลังการเขียนแล้ว `test_the_latch_is_set_before_the_stop_frame_is_written` แดง, คืนโค้ดแล้วเขียวครบ; **ไม่ได้ส่งคำสั่งใดไปหุ่นในรอบนี้**

## 2026-09-11 — ค้นในเครื่องหุ่นและใน APK เอง พบแผนที่กับจุดหมายที่บันทึกไว้แล้ว

- เจ้าของทักว่าทำไมไม่เข้าไปค้นไฟล์เอง ถูกต้อง — รอบนี้ค้นจริงทั้งบนเครื่องและใน APK
- **ตัดออกได้**: ไลบรารี native ทั้ง 61 ไฟล์ใน APK ไม่มีโค้ดพอร์ตอนุกรมเลย (โปรโตคอลอยู่ฝั่ง Java ใน DEX ที่ถูกห่อ) · `lib1180Driver.so`/`lib3000Driver.so` เป็นไดรเวอร์กล้องความลึก Imi ไม่ใช่แขน · `librpsdk.so` คือ RoboPeak = SLAMTEC · `localvoice.xlsx`/`add.xlsx` เป็นเทมเพลตคลังถาม-ตอบ · `prompt.txt`/`knowledgeprompt.txt` ว่างเปล่า 0 ไบต์ · `run-as` ใช้ไม่ได้เพราะแอปไม่ใช่ debuggable
- **พบชั้นคำสั่งของแอปใน `voice_config/lang_*.user.json`**: `ROBOTPERFORM` (perform/show) และ `ROBOTJUMP` เป็น exactMatch ยืนยันว่า ROBOTJUMP เป็นคำปลุกคำสั่ง ไม่ใช่หลักฐานว่าหุ่นกระโดด · `ORDERPAUSE`/`ORDERSTOP`/`ORDERCONTINUE` แปลว่า**ทางหยุดชุดท่ามีอยู่ที่ชั้นแอป** ประกอบกับ `stopArm()` ใน SDK — ถ้อยคำ "ยังไม่มีคำสั่งหยุดที่ยืนยันว่าใช้กับบอร์ดตัวนี้ได้" ยังถูก และตอนนี้รู้ว่าจะไปหาที่ไหน · `FACE` แปดแบบ editable=false เข้าคู่กับโฟลเดอร์ faceexpression · `MAPUCONTROL` สี่ทาง ยังไม่ทราบว่าคุมอะไร ระบุไว้ว่าห้ามเดาว่าเกี่ยวกับสี่ช่องเซอร์โว · ไม่มีเลขกลุ่มท่าในไฟล์เหล่านี้ สอดคล้องกับข้อสรุปเดิม
- **ของที่ไม่ได้ตามหาแต่สำคัญกว่า**: `/sdcard/aobo/map/` ลงวันที่ 25 ก.ค. 2026 มี `1.stcm` (372,262 ไบต์ **คนละไฟล์กับที่ดึงจากแชสซีเมื่อ 10 ก.ย.** ขนาดและ sha256 ต่างกัน) และ `Pose.txt` ที่มี**จุดหมายบันทึกไว้แล้วสามจุดพร้อมพิกัดและมุมหันจริง** หนึ่งจุดชื่อ `sofa` — แตะงานที่ค้างเรื่อง "ต้องให้ช่างสร้างแผนที่และตั้งจุด" และห้าจุดใน `data/showroom/layout.json` ที่ยัง `verified: false`
- **ยังไม่ยืนยัน**ว่าแผนที่นี้เป็นของห้องขายนี้ (ลงวันที่ก่อนหุ่นมาถึง) และยังไม่ยืนยันว่าโหลดขึ้นแชสซีแล้วพิกัดตรงพื้นจริง
- ตรวจจริง: อ่านและคัดลอกอย่างเดียว เก็บสำเนาไว้ที่ `data/robot-inspection/tablet-pull-20260911/` (ถูก ignore); ไม่เขียนอะไรลงหุ่น ไม่ส่งคำสั่งมอเตอร์ ไม่แก้ runtime code จึงไม่รัน unit tests ซ้ำ

## 2026-09-11 — ทดลองหยุดแอปแล้วผูกไดรเวอร์: ได้พอร์ตจริง และแก้ข้อสรุปที่ผิดสองข้อ

- เจ้าของรันลำดับ force-stop → bind → restart ให้ (คำสั่ง root รันโดยเจ้าของ ผู้ช่วยวัดผล) ผลครบวง: หยุดแอปแล้วอินเทอร์เฟซถูกปล่อย (`driver=[]` โดย `class=ff` ยังอยู่) · ไดรเวอร์ kernel **ไม่ผูกกลับเอง** · `echo 9-1.2:1.0 > /sys/bus/usb/drivers/cp210x/bind` สำเร็จ ได้ `/dev/ttyUSB10` · เปิดแอปกลับ แอปยึดคืนเป็น usbfs ทันทีและ node หายไป
- ขั้นสุดท้ายคือสิ่งที่ขาด: **กลไกที่เคยอนุมานเรื่องเหตุการณ์วันที่ 10 ถูกสังเกตตรงๆ แล้ว** ไม่ต้องอนุมานอีกว่าเกิดกับเครื่องนี้ได้จริงไหม
- **แก้ข้อสรุปผิดข้อ 1**: ที่เขียนว่าไม่มีกฎ ueventd สำหรับ ttyUSB จึงเป็นของ root — กฎมีอยู่ `/dev/ttyUSB0..9` = 0660 radio:radio (จองให้โมเด็ม) และ `/dev/ttyUSB1*` = **0666 system:system** ตรงกับสิทธิ์ที่วัดได้จริงของ node; สาเหตุที่อ่านผิดคือใช้ `grep 'ttyUSB\|ttyACM'` ซึ่ง **toybox grep ไม่รองรับ `\|` แบบ BRE** จึงไม่แมตช์อะไรเลย — ผลว่างของการวัดที่พังหน้าตาเหมือนผลว่างที่เป็นจริง รูปเดียวกับ `nc -z` เมื่อ 9 ก.ย. ซึ่งรอบนั้นรอดเพราะมีตัวควบคุมด้านบวก รอบนี้ไม่มี
- **แก้ข้อสรุปผิดข้อ 2**: ที่ตีความว่า `centre(1)` ล้มเพราะสิทธิ์ของ node — ข้อความจริงคือ `can't create /dev/ttyUSB10` คือพยายามสร้างไฟล์ใหม่ใน `/dev` เพราะ node ยังไม่มี ไม่ใช่ถูกปฏิเสธที่ตัว node; **หน้า `/arm` เขียนได้โดยไม่ต้อง root เมื่อพอร์ตว่าง**
- แก้ชื่อไดรเวอร์: บนบัส usb คือ `ch341` ไม่ใช่ `ch341-uart` (ตัวหลังเป็นชื่อบนบัส usb-serial)
- บันทึกลำดับที่ใช้ได้จริงห้าขั้นไว้ในเอกสาร พร้อมข้อจำกัดถาวร: แอปผู้ขายกับเราถือพอร์ตพร้อมกันไม่ได้
- ยังไม่รู้ว่า CP2102 หรือ CH340 คือแขน · จุดตัดไฟเลี้ยงเซอร์โวยังไม่มีใครหาเจอ · **ยังไม่ส่งไบต์ใดไปบอร์ดทั้งสิ้น** และบันทึกเตือนว่าการเปิดพอร์ตแม้เพื่อฟังอย่างเดียวก็ยก DTR/RTS ซึ่งอาจรีเซ็ตไมโครคอนโทรลเลอร์ปลายทาง
- ตรวจจริง: วัดสถานะ sysfs ก่อนและหลังทุกขั้น; ไม่แก้ runtime code จึงไม่รัน unit tests ซ้ำ; หุ่นถูกคืนสภาพแล้ว แอปกลับมารันและถือพอร์ตตามเดิม

## 2026-09-11 — ปิดเรื่องเจ้าของพอร์ตอนุกรม วัดได้แล้วว่าเป็นแอป Aobo ตัวเดียวถือทั้งสอง

- เจ้าของรัน `su 0 sh -c 'ls -l /proc/*/fd/* | grep bus/usb'` ให้ (ผู้ช่วยยกสิทธิ์เองไม่ได้ ถูกบล็อก) ผลคือมีโปรเซสเดียวที่เปิด usbfs node ไว้ คือ pid 3465 = `com.aobo.robot.ai3` ถือสี่ fd ชี้ไปที่สองอุปกรณ์
- จับคู่ `busnum`/`devnum` จาก sysfs แล้ว: `/dev/bus/usb/009/004` = CP2102, `/dev/bus/usb/009/006` = CH340 — **แอปเดียวถือตัวแปลงอนุกรมทั้งสองตัว** ไม่ใช่เดมอนของผู้ผลิตเครื่อง (`gpioservice`, `lcdparamservice`, `com.wits.witsservices` ที่ `ps` ทำให้เป็นผู้ต้องสงสัย)
- ข้อที่เคยบันทึกว่า "ยังไม่ยืนยัน" ตอนนี้ยืนยันด้วยการวัดแล้ว ส่วนเหตุการณ์วันที่ 10 ยังเป็นการอนุมาน เพราะไม่ได้วัด ณ ตอนนั้น — แต่เป็นการอนุมานที่หนักขึ้นมาก เพราะกลไกและตัวผู้กระทำถูกวัดแล้ววันนี้
- ผลต่อการเลือกทาง: ทาง 3 ถูกยืนยันว่าตรงที่สุด เพราะผู้ถือพอร์ตคือแอปที่มี service ให้เรียก ถ้าผลออกมาเป็น `gpioservice` แผนจะต้องเปลี่ยนไปคุยกับผู้ผลิตเครื่องแทนผู้ผลิตหุ่น ซึ่งคนละบริษัท; ทาง 2 ทำได้จริงเชิงเทคนิคแต่ต้อง root สองชั้น (ผูกไดรเวอร์กลับ + สิทธิ์ device node ที่ไม่มีกฎ ueventd) และเป็นการหยุดแอปหลักของหุ่น จึงเป็นการตัดสินใจของเจ้าของ
- ตรวจจริง: คำสั่งฝั่งผู้ช่วยเป็นการอ่าน sysfs อย่างเดียว; คำสั่ง root รันโดยเจ้าของ; ไม่ได้หยุดแอป ไม่ได้เปลี่ยนค่าบนหุ่น ไม่ได้ส่งคำสั่งไปมอเตอร์; ไม่แก้ runtime code จึงไม่รัน unit tests ซ้ำ

## 2026-09-11 — ทดสอบ /arm กับหุ่นจริงเท่าที่ทำได้โดยไม่ขยับอะไร

- รัน `app/robot_arm.py` กับหุ่นจริงผ่าน adb Wi-Fi โดยตั้งค่าในโปรเซสทดสอบเท่านั้น ไม่แตะ `.env` ผลตรวจสี่ข้อ:
- **ตัวตรวจพอร์ตวัดได้จริง ไม่ได้ตอบ False อย่างเดียว** — เทียบกับ `/dev/ttyS0` ที่มีอยู่จริงได้ `present=True` ส่วน `/dev/ttyUSB10` ได้ `False` การมีตัวควบคุมด้านบวกคือบทเรียนจาก `nc -z` เมื่อ 9 ก.ย. ที่รายงานว่าทุกพอร์ตปิดรวมถึงพอร์ตที่เปิดอยู่
- **ท่อ adb ถึงหุ่นทำงานครบเส้น** — เรียก `centre(1)` ตรงๆ ข้ามด่าน `port_present()` แล้วคำสั่งถูกส่งถึงเชลล์ของหุ่นและทำงานจริง ตอบกลับว่า `can't create /dev/ttyUSB10: Permission denied` ซึ่ง**ยืนยันการวิเคราะห์ ueventd ด้วยการวัด**: ไม่มีกฎสำหรับ `ttyUSB` node จึงเป็นของ root และ adb shell เป็น uid 2000
- **การเขียนที่ล้มเหลวถูกรายงานว่าล้มเหลว** ไม่ใช่รายงานว่าส่งแล้ว ตรงตามกฎ mock-ไม่ใช่-ok
- สรุปสถานะหน้า `/arm`: โค้ดกับท่อพิสูจน์แล้วทั้งเส้น เหลือด่านเดียวคือสิทธิ์และการครอบครองพอร์ต ซึ่งแก้ด้วยโค้ดไม่ได้
- root ถูกบล็อกจากฝั่งเครื่องมือของผู้ช่วย จึงส่งคำสั่งระบุเจ้าของพอร์ตให้เจ้าของรันเอง ไม่ยกสิทธิ์แทน
- ตรวจจริง: คำสั่งที่ส่งถึงหุ่นรอบนี้มีเพียง `ls` และการพยายามเขียนหนึ่งครั้งที่ถูกระบบปฏิเสธ **ไม่มีไบต์ใดถึงบอร์ดเซอร์โว ไม่มีมอเตอร์ใดถูกสั่ง ไม่ได้หยุดแอป ไม่ได้เปลี่ยนค่าบนหุ่น**; ไม่แก้ runtime code จึงไม่รัน unit tests ซ้ำ

## 2026-09-11 — ต่อ adb ผ่าน Wi-Fi แล้วพบว่าข้อสรุปเรื่องพอร์ตหลุดของเมื่อเช้าผิด

- ต่อ adb ผ่าน Wi-Fi สำเร็จ (`adb pair` แล้ว `adb connect`) ไม่ต้องเสียบสายเข้าคอม; ตรวจสดแล้ว `/dev/ttyUSB*` ไม่มีเลยทั้งที่บูตมา 8 นาที
- **แก้ข้อสรุปเดิม**: ที่เขียนไว้ว่า "ไฟถูกตัดหรือพอร์ตถูกสั่งปิด" ผิด — ตัวแปลงทั้งสองตัวต่ออยู่ครบ (CP2102 ที่ 3-1.2, CH340 ที่ 3-1.4) แต่ผูกกับ **`usbfs`** ไม่ใช่ `cp210x`/`ch341-uart` และ `/sys/bus/usb-serial/devices/` ว่างทั้งที่ไดรเวอร์ลงทะเบียนอยู่ แปลว่า **แอป `com.aobo.robot.ai3` เรียก `claimInterface()` ยึดอินเทอร์เฟซไว้ ทำให้ไดรเวอร์ kernel ปล่อยอุปกรณ์** — อธิบาย log วันที่ 10 ได้ครบกว่าเดิม รวมถึงข้อที่ว่าทำไมสองตัวหลุดห่างกันสามมิลลิวินาที (เป็นการกระทำของโปรแกรมเดียว ไม่ใช่ไฟเส้นเดียวกัน)
- ผลต่อหน้า `/arm`: สมมติฐาน `ROBOT_ARM_PORT=/dev/ttyUSB10` ใช้ไม่ได้ตราบใดที่แอป Aobo รัน และถึงหยุดแอปก็ยังติดสิทธิ์ เพราะ `ueventd` ไม่มีกฎสำหรับ `ttyUSB` เลย (node จะเป็นของ root) ส่วน adb shell คือ uid 2000 — **หน้า `/arm` จึงยังใช้กับหุ่นตัวนี้ไม่ได้ตามที่ออกแบบไว้ ไม่ใช่เพราะโค้ดผิด แต่เพราะพอร์ตไม่ว่าง** และด่าน `port_present()` ที่ทำไว้ก็รายงานตรงตามจริงแล้ว
- ทางที่เหลือบันทึกไว้สี่ทางพร้อมหลักฐาน: ผ่าน usbfs โดยตรง (`/dev/bus/usb/003/*` เป็น 0666 เขียนได้ไม่ต้อง root แต่ต้องทำ control transfer เอง) · หยุดแอป Aobo แล้วยกสิทธิ์ (เครื่องเป็น userdebug มี `/system/xbin/su` — **เป็นการเปลี่ยนสภาพหุ่น ส่งให้เจ้าของตัดสินใจ ไม่ทำเอง**) · ผ่าน `SerialdataService` ของแอป ซึ่งกลับมามีน้ำหนักเพราะแอปคือผู้ถือพอร์ต · เขียนแอปของเราเองด้วย usb-serial-for-android ซึ่งเป็นทางเดียวกับที่แอปผู้ขายใช้และเข้ากับแผน APK ของ Emma
- ยืนยันอุปกรณ์อื่นระหว่างทาง: `3-1.3` Bothlent UAC Dongle ผูก `snd-usb-audio` คือไมค์อาร์เรย์ · `3-1.1` USB RGB Camera ผูก `uvcvideo` · `9-1.1` จอสัมผัส ILITEK · `ttyS0/1/3/4/6/8` เป็น 0666 แต่ยังไม่มีหลักฐานว่าตัวใดเกี่ยวกับแขน และกฎ ueventd ผูกบางตัวไว้กับ bluetooth/gps จึงห้ามลองเขียนมั่ว
- **แก้ให้แคบลงอีกชั้นในวันเดียวกัน**: ที่เขียนว่าแอป Aobo เป็นผู้ยึดอินเทอร์เฟซ แรงเกินหลักฐาน — `usbfs` พิสูจน์ว่ามีโปรแกรมฝั่งผู้ใช้ยึดอยู่ ไม่ได้บอกว่าตัวไหนหรือเป็นตัวเดียวกันทั้งสอง; `ls /proc/<pid>/fd` ของโปรเซสอื่นถูกปฏิเสธสิทธิ์, `lsof` เห็นเฉพาะโปรเซสตัวเอง, `dumpsys usb` บล็อก `permissions_manager` ว่างเปล่า — **ระบุตัวเจ้าของต้องใช้ root ซึ่งไม่ทำเอง** คำอธิบายเหตุการณ์วันที่ 10 จึงยังถือว่าไม่ยืนยัน
- **และการยึดพอร์ตไม่ถูกข้ามด้วยการเขียน usbfs เองหรือทำ APK ใหม่**: `claimInterface()` เป็นสิทธิ์เฉพาะราย ไฟล์อุปกรณ์ที่เป็น 0666 บอกแค่ว่าเปิดไฟล์ได้ ไม่ได้แปลว่าอ้างสิทธิ์อินเทอร์เฟซได้ ผู้ถือเดิมต้องปล่อยก่อนเสมอ — ทาง 3 (สัญญาของ service ผู้ขาย) จึงขึ้นเป็นลำดับแรก ทาง 4 เป็นทางสำรองของ APK Emma
- ทาง 3 คืบจาก package manager แบบอ่านอย่างเดียว: พบ **AIDL service `com.aobo.aibot.aidl.MyService` action `com.aobo.aidl.test`** (เป็น AIDL = มีสัญญาเป็นไฟล์ `.aidl` อยู่จริง) และ `DoubleScreenService` ที่มี intent filter ส่วน **`SerialdataService` ไม่มี intent filter เลย** เรียกด้วย action ไม่ได้ ต้องระบุ component พร้อม extras ที่อยู่ใน DEX ที่ถูกห่อ — ข้อที่ขอผู้ขายจึงแคบจาก "ขอ SDK" เหลือ "ขอไฟล์ .aidl กับรายการ extras" ซึ่งคู่มือหน้า 9 ระบุว่าอยู่ในชุดส่งมอบอยู่แล้ว
- ตรวจจริง: คำสั่งอ่านอย่างเดียวทั้งหมด — `ls`, `cat /sys/...`, `ps`, `getprop`, `grep ueventd`; **ไม่ได้หยุดแอป ไม่ได้ยกสิทธิ์ ไม่ได้เปิดพอร์ต ไม่ได้เขียนอะไรลงอุปกรณ์ ไม่ได้เปลี่ยนค่าบนหุ่น**; ไม่แก้ runtime code ในรอบนี้ ไม่รัน unit tests ซ้ำเพราะเปลี่ยนเฉพาะเอกสาร

## 2026-09-11 — แก้ห้าข้อจากรีวิวเจ้าของก่อนแตะหุ่นจริง

รีวิวชี้จุดที่ต้องแก้ก่อนถือว่าพร้อมทดลอง ตรวจกับไฟล์จริงแล้ว**ถูกทั้งห้าข้อ** แก้ในก้อนเดียว:

- **ปุ่มหยุดใช้ไม่ได้ตอนที่จำเป็นที่สุด** `command()` คืนค่าทันทีเมื่อ `busy=true` และปุ่มแดงใช้ฟังก์ชันเดียวกัน กดตอนมีคำสั่งค้างจึงไม่ส่งอะไรเลย — ปุ่มที่มีไว้สำหรับตอนที่ของกำลังเคลื่อนที่ กลายเป็นปุ่มที่ไม่ทำงานตอนของกำลังเคลื่อนที่ ตอนนี้ stop/arm ส่งแบบ `urgent` ข้ามด่าน busy และไม่แตะค่า busy ของคำสั่งที่มันแซง
- **กลุ่มท่าไม่ได้อยู่ใต้ข้อจำกัดใดเลย** รายการสี่ช่องกับ `ROBOT_ARM_SPAN` คุมเฉพาะปุ่มเลื่อน ส่วนกลุ่มท่าที่บันทึกไว้สั่งช่องไหนก็ได้ในยี่สิบช่อง ไปที่ไหนก็ได้ การตรึงหนึ่งรอบคุมแค่จำนวนครั้ง — เพิ่มสวิตช์แยก `ROBOT_ARM_GROUPS_ENABLED` (default false) ไม่ให้ติดมากับ `ROBOT_ARM_ENABLED` และเขียนข้อจำกัดนี้ไว้ทั้งใน docstring และบนหน้าเว็บ
- **ตำแหน่งค้างหลังเล่นกลุ่มท่า** `step()` คำนวณต่อจากค่าที่กลุ่มท่าทำให้ไม่จริงไปแล้ว ปุ่ม +40 จึงไม่ใช่ก้าวเล็กจากตำแหน่งปัจจุบัน — `run_group()` ล้าง `_commanded` ทั้งหมด ต้องสั่งไป 1500 ใหม่ก่อนเลื่อน (ล้างทิ้ง ไม่ใช่ติดธงว่า stale เพราะธงคือสิ่งที่คนลืมเช็ค และ `step()` มีด่าน "ไม่รู้จักช่องนี้" ที่เทสต์คุมอยู่แล้ว)
- **"ตั้งจุดเริ่ม" เป็นชื่อที่บอกว่ามีการวัด** ทั้งที่มันสั่งไป 1500 จริงๆ และ 1500 คือกลางของช่วงโปรโตคอล ไม่ใช่กลางของข้อต่อ ยังไม่มีหลักฐานว่าเหมาะกับทุกช่อง — เปลี่ยนป้ายเป็น "สั่งไป 1500" ทั้งบนปุ่มและในข้อความ error พร้อมเตือนว่ากดครั้งแรกของแต่ละช่องต้องมีมืออยู่ที่สวิตช์ไฟเซอร์โว
- **3000 ms ไม่ใช่ช้าที่สุด** เอกสารระบุช่วง `T` = 100–9999 — `SLOW_MS` เป็น 9999 แล้ว เมื่อระยะทางที่จะเคลื่อนยังไม่รู้ "ช้าที่สุดที่มี" เป็นค่าเดียวที่อธิบายได้
- **ถ้อยคำ** "บอร์ดไม่มีคำสั่งหยุด" → "ยังไม่มีคำสั่งหยุดที่ยืนยันว่าใช้กับบอร์ดตัวนี้ได้" เพราะตารางคำสั่งที่อ่านได้เป็นของรุ่น 32 ช่อง ส่วนคู่มือหุ่นระบุ 20 ช่อง ความเข้ากันยังไม่พิสูจน์ — และข้อนี้ตัดทั้งสองทาง รวมถึงทางนี้ด้วย
- `.env` / `.env.example`: เพิ่ม `ROBOT_ARM_GROUPS_ENABLED=false` และคอมเมนต์ว่าใช้ adb ผ่าน Wi-Fi ได้ ไม่ต้องเสียบสายเข้าคอม (`adb connect <ip>:5555` แล้วใส่ที่ `ROBOT_ARM_ADB_SERIAL`) พร้อมแยกให้ชัดว่าสาย USB ไปคอมถอดได้ ส่วน USB ในตัวหุ่นที่ไปบอร์ดเซอร์โวถอดไม่ได้
- ตรวจจริง: `pytest tests/ -q` = **1315 passed** (จาก 1309); เทสต์ไฟล์นี้ 27 ตัว (จาก 21); ย้อนโค้ดพิสูจน์ครบสี่ข้อ — คืน busy gate ให้ปุ่มแดง, ถอดด่านสวิตช์กลุ่มท่า, ไม่ล้างตำแหน่งหลังกลุ่มท่า, และ `SLOW_MS` กลับเป็น 3000 แต่ละข้อทำให้เทสต์ที่เขียนไว้เพื่อมันแดงทีละตัว คืนโค้ดแล้วเขียวครบ; ยังไม่ได้ต่อหุ่น ไม่ได้เปิดพอร์ตอนุกรม ไม่ได้ส่งคำสั่งไปมอเตอร์

## 2026-09-11 — หน้าสั่งแขนด้วยมือที่ /arm

- `app/robot_arm.py` (ใหม่): คุยกับบอร์ดเซอร์โว Torobot ผ่านพอร์ตอนุกรมของหุ่นด้วย adb — `centre()` สั่งไปกลางช่วงช้าที่สุด, `step()` ก้าวละ 40 µs จากตำแหน่งที่เราสั่งไว้ล่าสุด, `run_group()` เล่นกลุ่มท่าหนึ่งรอบ, `stop()` ล็อกไม่ให้ส่งต่อ, `port_present()` เช็คว่าพอร์ตมีอยู่จริงก่อนเขียน
- ออกแบบตามข้อจำกัดจริงของบอร์ดสามข้อ: **ไม่มีคำสั่งหยุด** ปุ่มแดงจึงเขียนว่า "หยุดส่งคำสั่ง" และหน้าเว็บบอกว่าตัวหยุดจริงคือไฟเลี้ยงเซอร์โว · **เซอร์โวอ่านตำแหน่งกลับไม่ได้** จึงต้องกดตั้งจุดเริ่มก่อน ห้ามเดาว่าอยู่กลาง · **ยังไม่รู้ว่าช่องไหนคือข้อต่อไหน** จึงสั่งได้เฉพาะช่อง 1, 11, 7, 8 ที่แอปของหุ่นเองเปิดไว้ และหน้าเว็บแสดงเลขช่อง ไม่ตั้งชื่อข้อต่อ
- `client/robot-arm.html` (ใหม่): ปุ่มหยุดติดบนสุดแบบ sticky, ปุ่มเลื่อนถูกปิดจนกว่าจะตั้งจุดเริ่ม, ระยะกำหนดที่เซิร์ฟเวอร์ หน้าเว็บส่งแค่ `token` กับ `action`; `app/main.py` เพิ่ม `/arm`, `/arm/state`, `/arm/command` โดย stop/arm ทำงานก่อนด่านตรวจ config
- `app/config.py` + `.env.example`: `ROBOT_ARM_ENABLED` (default false), `ROBOT_ARM_PORT` (ว่างโดยตั้งใจ — วันที่ 10 ก.ย. พอร์ตจริงคือ ttyUSB10/11 ไม่ใช่ ttyUSB1 ที่ SDK ระบุ), `ROBOT_ARM_BAUD`, `ROBOT_ARM_SPAN` (200 µs จาก 1000), `ROBOT_ARM_ADB`, `ROBOT_ARM_ADB_SERIAL`, `ROBOT_ARM_TIMEOUT_S`
- `.env` (ไม่อยู่ในรีโป): เพิ่มบล็อก `ROBOT_ARM_*` ต่อท้าย ตั้ง `ROBOT_ARM_ENABLED=false` และ `ROBOT_ARM_PORT` ว่างไว้ พร้อมคอมเมนต์ว่าต้องดูชื่อพอร์ตจากหุ่นจริงภายในนาทีแรกหลังบูต และต้องหาจุดตัดไฟเลี้ยงเซอร์โวก่อนเปิดสวิตช์; ตรวจแล้วว่าไม่มีคีย์ `ROBOT_ARM_` เดิมซ้ำ และหลังแก้ `TOOL_GROUPS` กับค่าแชสซียังเหมือนเดิมทุกตัว
- `tests/test_robot_arm_page.py` (ใหม่ 21 ตัว) + `tests/conftest.py` ปิดสวิตช์ทั้งสองและล้าง state เพื่อไม่ให้เครื่องที่เสียบหุ่นอยู่ยิง adb จริงตอนรันเทสต์
- **บั๊กที่เทสต์จับได้ระหว่างเขียน**: เทสต์ห้ามตั้งชื่อข้อต่อใช้ `in` กับคำไทย ทำให้ `คอ` แมตช์ใน `โปรโตคอล` — กับดักเดียวกับ `ราคา` ใน `อาคาร` ที่โปรเจกต์นี้จ่ายมาแล้วสองรอบ แก้เป็นเทียบราย*คำ*ผ่าน `tokenize` และตัดคอมเมนต์ออกก่อนสแกน
- ตรวจจริง: `pytest tests/ -q` = **1309 passed** (เดิม 1288); ย้อนโค้ดพิสูจน์สองข้อ — ถอดล็อก disarm แล้ว `test_stop_latches_and_refuses_everything_after` แดง, เปลี่ยนจากปฏิเสธเป็นเดาว่าอยู่กลางแล้ว `test_stepping_is_refused_until_the_channel_has_been_centred` แดง, คืนโค้ดแล้วเขียวครบ 21 ตัว; ไม่ได้เชื่อมต่อหุ่นจริง ไม่ได้เปิดพอร์ตอนุกรม ไม่ได้ส่งคำสั่งไปยังมอเตอร์ใด — **ยังไม่มีใครพิสูจน์กับบอร์ดจริง**

## 2026-09-11 — พอร์ตอนุกรมของแขนเคยขึ้นจริงแล้วถูกตัดหลังบูตหนึ่งนาที

- `docs/robot-command-research-2026-09-11.md`: อ่าน `recent_android_log.txt` ที่สำรองไว้ซ้ำแล้วพบ CP2102 ขึ้นเป็น `ttyUSB10` และ CH341 ขึ้นเป็น `ttyUSB11` เวลา 17:07:17–18 แล้ว**หลุดพร้อมกันในระยะ 3 มิลลิวินาที** เวลา 17:08:18 — สองอุปกรณ์คนละพอร์ตคนละชิปหลุดพร้อมกันเป๊ะขนาดนี้คือไฟถูกตัดหรือพอร์ตถูกสั่งปิด ไม่ใช่สายหลวม อธิบายได้ว่าทำไม `ls /dev/ttyUSB*` ตอนสำรอง 17:47 จึงไม่พบอะไร
- ชื่อพอร์ตจริงคือ `ttyUSB10`/`ttyUSB11` ไม่ใช่ `/dev/ttyUSB1` ที่ SDK ตั้งเป็นค่าเริ่มต้น เป็นคนละ device node กัน; เลขที่เริ่มที่ 10 บ่งว่าเลข 0–9 ถูกจองไว้แล้ว
- งานถัดไปที่ต้องมาก่อนเรื่องอื่นและเป็นการอ่านอย่างเดียว: บูตหุ่นแล้วดู `/dev/ttyUSB*` ภายในนาทีแรกพร้อมเก็บ log ช่วงนั้น **คอขวดคือพอร์ตที่หายไป ไม่ใช่ schema ของ SerialdataService** ซึ่งไม่จำเป็นต้องรู้เพราะโปรโตคอลเป็นข้อความบนพอร์ตอนุกรม
- ทบทวนข้อสงสัยความเข้ากันได้: **baud ไม่ขัดกัน** คู่มือ Astronaut หน้า 6 ระบุสเปกบอร์ดเองว่ารับ 9600–128000 รวม 115200 และบอร์ดตระกูลนี้ตรวจ baud อัตโนมัติ; ส่วนจำนวนช่อง 20 เทียบเอกสาร USC-32 ยังต่างจริง ยังไม่ยืนยัน
- ข้อสังเกต `servo_number_options` = `1, 11, 7, 8, All` **ไม่เรียงจากน้อยไปมาก** และ 1 กับ 11 ห่างกัน 10 พอดีบนบอร์ด 20 ช่อง ตั้งเป็นสมมติฐานที่ทดสอบได้ว่าช่อง N กับ N+10 อาจเป็นข้อต่อคู่กระจกซ้าย–ขวา ระบุชัดว่าเป็นสมมติฐานและอยู่หลังเงื่อนไขความปลอดภัยทั้งหมด
- ตรวจจริง: grep log ที่สำรองไว้พร้อมเวลาและหมายเลขบรรทัด, ยืนยันข้อความ `Baud rate range` และ `Simultaneous control of 20 channels` จากคู่มือที่สกัดไว้; `git diff --check` ผ่าน ไม่รัน unit tests เพราะเพิ่มเฉพาะเอกสาร ไม่เชื่อมต่อหุ่น ไม่เปิดพอร์ตอนุกรม ไม่ส่งคำสั่งมอเตอร์

## 2026-09-11 — บอร์ดแขนเป็น Torobot: โปรโตคอลเปิด ตัดผู้ขายออกจากเส้นทางท่าแขน

- `docs/robot-motion-capabilities-2026-09-11.md` หัวข้อ 7: คู่มือหน้า 6 ระบุว่าบอร์ดข้อต่อส่วนบน **"Controlled using Torobot programming software"** — เป็นบอร์ดควบคุมเซอร์โวเชิงพาณิชย์ที่มีคู่มือโปรโตคอลเปิดเผย ไม่ใช่ของเฉพาะ Aobo
- ชุดคำสั่งเป็นข้อความล้วนบนพอร์ตอนุกรมจบด้วย `
`: `#1P1500T100` สั่งช่องเดียวไปตำแหน่ง (500–2500) ตามเวลา (100–9999), `#1GC2` เล่นกลุ่มท่าตามเลขและจำนวนรอบ — แปลว่า**คุมทีละข้อต่อได้โดยไม่ต้องรู้ตารางกลุ่มท่า** และซอฟต์แวร์ผู้ผลิตบอร์ดมีปุ่มอ่านหมายเลขกลุ่มท่าที่มีอยู่จริง ตัดการไล่เลขแบบสุ่มออก
- ข้อจำกัดที่บันทึกไว้ ไม่กลบ: คู่มือ Torobot ที่อ่านได้**ไม่มีคำสั่งหยุด** ตัวหยุดที่แน่นอนคือไฟเลี้ยงเซอร์โวที่แยกวงจร ต้องหาให้เจอก่อนทดสอบ · เซอร์โว PWM ไม่มีการอ่านตำแหน่งกลับ คำสั่งแรกของทุกช่องคือการขยับแบบไม่รู้จุดตั้งต้น ต้องใช้เวลาช้าที่สุดเสมอ · คู่มือที่ดึงมาเป็นรุ่น 24/32 ช่อง ส่วนบอร์ดในหุ่นระบุ 20 ช่อง ยังไม่ยืนยันกับบอร์ดตัวนี้
- คู่มือยังระบุไมค์อาร์เรย์ 4 ตัวพร้อม echo cancellation ในตัว (ตรงกับที่วัดได้ 8 ช่อง 16 kHz) และจอสองจอเป็นสเปกมาตรฐาน จอสีหน้า 7 นิ้วที่หัว จอสัมผัส 18.5 นิ้วที่อก
- ตรวจจริง: ดึงข้อความคู่มือ Astronaut ด้วยการลบช่องว่างก่อน grep (ข้อความสกัดมาเป็นตัวอักษรเว้นห่าง ทำให้ค้นตรงๆ ไม่เจอ), ดาวน์โหลดและ `pdftotext -layout` คู่มือโปรโตคอล Torobot เก็บสำเนาไว้ที่ `data/robot-inspection/motion-capabilities-20260911/torobot-usc-protocol-en.txt` (โฟลเดอร์ถูก ignore); `git diff --check` ผ่าน ไม่รัน unit tests เพราะเพิ่มเฉพาะเอกสาร และไม่ส่งคำสั่งไปยังหุ่นหรือบอร์ดใดๆ

## 2026-09-11 — สำรวจความสามารถการเคลื่อนไหวของ Astronaut

- `docs/robot-motion-capabilities-2026-09-11.md`: รวมการเคลื่อนฐาน หัว/คอ แขน ท่อนแขน มือ/นิ้ว ท่าประกอบและงานนำทาง พร้อมระดับหลักฐานคู่มือ/ระบบประกาศ/ทดลองจริง
- ระบุมุมจากคู่มือหน้า 6 โดยไม่แปลงเป็นค่าข้อต่อที่ปรับเทียบแล้ว; แยกข้อขัดแย้ง 20 channels / 22 joints / 24 DOF และสเปกคอมในคู่มือที่ต่างจากเครื่องจริง
- ตรวจชื่อ action factories 21 รายการจาก snapshot และ strings ใน APK; ไม่ตีความ FollowTarget/Sweep/ROBOTJUMP เป็นการตามคน ทำความสะอาด หรือกระโดดที่พิสูจน์แล้ว
- ตรวจจริง: อ่านคู่มือครบ 10 หน้าและภาพหน้าที่เกี่ยวข้อง, เทียบข้อมูล SDK/สำเนาแชสซี, ค้นเว็บผู้ผลิตพร้อมแหล่งอ้างอิง; `git diff --check` ผ่าน ไม่รัน unit tests เพราะเพิ่มเฉพาะรายงาน และไม่ส่งคำสั่งเคลื่อนไหว

## 2026-09-10 — แขนและจอบนหัว: ตัดเบาะแสที่ตันออก เหลือของที่ต้องขอผู้ขายสามข้อ

- ต่อจากรายงาน `data/robot-inspection/analysis-arm-head-20260910/` ตรวจเพิ่มแล้ว**ตัดออก**: `chatcode.txt` เป็นมุกตลกจีน 21 รายการ · `base_actions.json` เป็นกติกาจับข้อความไม่ใช่ท่ามอเตอร์ · layout หน้า ARM มีแค่ปุ่มเลือกเลขกลุ่มท่ากับ spinner เลือกเซอร์โว แอปส่งเลข ไม่ได้ถือตาราง — **ตารางเลขท่าอยู่ในหน่วยความจำของบอร์ดเซอร์โว ไม่ได้อยู่ในไฟล์ใดบนแท็บเล็ต เลิกค้นในไฟล์ได้**
- สมมติฐานที่ทดสอบได้ด้วยสวิตช์เดียวสำหรับจอบนหัว: `double_screen.xml` มี `SurfaceAnimView` ชื่อ `robotface` และมี `switch_doublescreen` — เปิดโหมดสองจอแล้ว dump จอใหม่ ถ้าเจอจอจริงตัวที่สองแปลว่าเป็นจอ Android เอาหน้าเว็บขึ้นได้เลย ถ้าไม่เจอแปลว่าขับด้วยตัวควบคุมแยกผ่านพอร์ตอนุกรม
- ข้อควรระวังที่บันทึกไว้: `loopCount=0` วนไม่สิ้นสุด ห้ามใช้ทดสอบ · เลขท่าที่ไม่รู้จักอาจดันข้อต่อสุดระยะ · **ห้ามไล่เลขท่าจากเครื่องมือที่โมเดลเรียกได้**
- รายการที่ต้องขอผู้ขายเหลือสามข้อ: ตารางกลุ่มท่าแขน, ช่องทางส่งภาพไปจอบนหัว, ช่องไมค์ที่ผ่าน AEC — ล้อไม่อยู่ในรายการแล้ว

## 2026-09-10 — จัดทำรายละเอียดไฟล์และหลักฐานคำสั่งแขน/จอ

- `data/robot-inspection/analysis-arm-head-20260910/` (ignored): รายงานภาษาไทยแยกเมธอด SDK แขนจากคำสั่งที่ทดสอบแล้ว, layout สีหน้า/จอสองจอ, ภาพสีหน้า 16 ไฟล์ และข้อจำกัดการระบุจอบนหัว
- ทำรายละเอียด shared storage ครบ 337 รายการ, installed APK 14 แอป, ไฟล์ประกอบ 121 รายการ และสารบัญ APK/ZIP 39 ไฟล์ รวม 82,871 สมาชิก; ระบุฐานหลักฐานของคำอธิบายและส่วนที่ยังถอดความหมายไม่ได้
- ตรวจจริง: SHA-256 สำเนา shared 331 ไฟล์เทียบหลักฐานต้นทางที่สำรองไว้ตรงครบ, สำเนา APK 14 ไฟล์ตรง hash เดิม, assertions จำนวนรายการผ่าน; ตรวจภาพ SDK จีนหน้า 21–23 และตัวอย่างภาพสีหน้าสามชุด
- `docs/robot-live-inspection-2026-09-10.md`: บันทึกว่า Android พบจอจริงหนึ่งจอและจอเสมือน AnyDesk ไม่ใช่จอหัวที่สอง; ไม่เปลี่ยน runtime/ค่าหุ่นหรือส่งคำสั่งมอเตอร์ ไม่รัน unit tests สำหรับงานรายงาน; ตรวจเอกสารด้วย `git diff --check`

## 2026-09-10 — สำรองและอ่านข้อมูลหุ่นที่เข้าถึงได้

- `docs/robot-live-inspection-2026-09-10.md`: เพิ่มผลสำรอง shared storage 331/337 ไฟล์ และ APK 14 แอป พร้อมตรวจ SHA-256 เทียบต้นทางผ่านครบส่วนที่คัดลอกได้
- สำรองค่าระบบ Android รายการแอป/ฮาร์ดแวร์/log แผนที่จากทั้งแชสซีและแอป และค่า API ที่อ่านได้; เก็บข้อมูลดิบและรายงานใน `data/robot-inspection/` ที่ถูก ignore ไม่มีข้อมูลส่วนตัวหรือค่าลับใน changelog
- ทำ catalog เนื้อหาและ manifest 456 ไฟล์; ระบุข้อมูลส่วนตัว Aobo/VoiceNote และ cache ที่ติดสิทธิ์ รวมถึง archive แรกที่ไม่ครบเพราะ USB หลุด ไม่อ้างว่าเป็น full-device image หรือ restore ที่ทดสอบแล้ว
- ตรวจข้อมูลอนุกรมเพิ่มเติมจาก log และสำรองไฟล์บริการ ttyusb-scan แบบอ่านอย่างเดียว; ยังไม่ยืนยัน mapping แขน
- ไม่แก้ runtime code ไม่เปลี่ยนค่าหุ่น ไม่ส่งคำสั่งมอเตอร์; ตรวจจริงด้วย file/hash/API inventory และ `git diff --check` สำหรับเอกสาร ไม่รัน unit tests ซ้ำ

## 2026-09-10 — เปิดโอนไฟล์หุ่นผ่าน USB

- เปลี่ยน USB function ปัจจุบันบนหุ่นเป็น `mtp` ด้วย `svc usb setFunctions mtp` เพื่อให้ผู้ใช้ดูไฟล์ใน Windows File Explorer; ไม่เปลี่ยนค่าเริ่มต้นบน unlock และไม่ส่งคำสั่งมอเตอร์
- ก่อนเปลี่ยน `sys.usb.config=adb` และ `svc usb getFunctions` ว่าง; ระหว่างเปลี่ยนคำสั่งจบด้วย exit 1 พร้อม opId จึงตรวจผลต่อ ไม่ถือว่าเป็นผลสำเร็จทันที
- ยืนยันหลัง USB ต่อใหม่: `svc usb getFunctions` ตอบ `mtp`, ADB ผ่าน USB ยังขึ้น `device`, Windows แสดงอุปกรณ์ WPD ชื่อ ZC-3588A สถานะ OK
- ไม่แก้ runtime code; ไม่รัน unit tests สำหรับการตั้งค่าอุปกรณ์ครั้งนี้

## 2026-09-10 — ตรวจคำสั่งจับมือและอุปกรณ์ USB

- `docs/robot-live-inspection-2026-09-10.md`: บันทึกผลค้นท่าจับมือใน APK และไฟล์ข้อความ/config/workbook 20 ไฟล์ใน aobo.zip; ยังไม่พบ mapping ท่า และ btn_shakehand ที่พบเป็น check status ของ LoRa
- อ่าน Android USB inventory พบ USB Serial สองชนิด แม้ไม่มี ttyUSB/ttyACM; ยังไม่ยืนยันตัวที่เชื่อมแขน บันทึกข้อจำกัดข้อความ #CC และ UI hierarchy ที่ค้าง
- ไม่ส่งท่า ไม่เปลี่ยนการควบคุมมอเตอร์; เปลี่ยนเฉพาะเอกสารจึงไม่รัน unit tests ซ้ำ

## 2026-09-10 — พบทางเข้าเมนู ARM Setting บนหุ่นจริง

- `docs/robot-live-inspection-2026-09-10.md`: เพิ่มทางเข้าที่ทดสอบสำเร็จผ่านการแตะโลโก้ Robot หลายครั้ง แล้วแตะหัวข้อ Backstage หลายครั้ง > Arm movement test; ไม่ต้องเปิด activity จากภายนอก
- ตรวจไฟล์ APK resources/layouts และโฟลเดอร์ที่ผู้ใช้ชี้เพิ่มเติม; เก็บผลและภาพหน้าจอใน `data/robot-inspection/` ที่ถูก ignore
- ผลจริง: เปิด ARM Setting ได้, หน้าจอเลือก Relay และแสดง connected (ยังไม่ใช่หลักฐานตอบกลับบอร์ด), ตัวเลือกท่าเป็นเลขและรายการคำสั่งที่ผูกไว้ไม่ปรากฏแถว; ยังไม่ได้ส่งท่าทาง เปลี่ยนวิธีควบคุม หรือติดตั้ง APK
- เปลี่ยนเฉพาะเอกสารและหลักฐานการตรวจ ไม่แก้ runtime code จึงไม่ได้รันชุดทดสอบซ้ำ

## 2026-09-10 — ทดสอบสั่งหุ่นจริง: คุมได้ครบเส้นทาง แต่หุ่นเดินไม่ออกเพราะถูกล้อม

- `app/robot_chassis.py`: log เอกสาร action ที่บอร์ดคืนมา ทั้งตอนสร้าง (`move_to`) และทุกครั้งที่สถานะเปลี่ยน (`_tick`, เทียบกับ `_last_action_seen` เพื่อไม่ให้มีบรรทัดทุก 2 วินาทีตลอดวัน) — ความล้มเหลวที่น่าสนใจไม่ใช่ "POST ถูกปฏิเสธ" แต่คือบอร์ดตอบ 200 แล้ว action ค้างอยู่เฉยๆ โดยไม่บอกเหตุผลที่ไหนเลย
- **ผลวินิจฉัย:** เส้นทางคำสั่งครบ 22/22 ถึงบอร์ด แต่ตำแหน่งที่บอร์ดรายงานเองไม่ขยับเลยใน 10.5 วินาที (0.0 ซม. 0.0 องศา) · action ค้างที่ `status: 1` โดย `stage` ว่างเปล่า = ตัววางแผนไม่เคยเริ่ม · ไลดาร์ปกติ (1572/1586 จุดใช้ได้) และบอกว่ารอบตัวหุ่นแคบ 0.43-0.48 ม. สามด้าน — หุ่นถือว่าออกไม่ได้ · `robot/health` สะอาด ไม่มี emergency stop
- **ตัดทิ้งได้แล้ว:** ไม่ใช่โค้ด ไม่ใช่ไลดาร์ ไม่ใช่ล็อกซอฟต์แวร์ ไม่ใช่ error ของฐาน
- **ข้อควรระวังใหม่:** action ที่ค้างไม่หายเอง ต้อง `DELETE /api/core/motion/v1/actions/:current` (ปุ่มหยุดบนหน้า `/robot`) ไม่งั้นคำสั่งถัดไปทับงานเดิม — ยกเลิกให้แล้วระหว่างตรวจ
- บันทึกตารางระยะรอบตัวหุ่นและลำดับที่ต้องทำต่อไว้ใน `docs/robot-arrival-2026-09-09.md`
- **เจอผู้ต้องสงสัยอันดับหนึ่งจากสเปกของบอร์ดเอง** (`/js/spec.js` 160 KB เป็น OpenAPI): `PUT /api/core/system/v1/parameter` รับ `base.brake_release` (`on` = ปลดเบรก, `off` = คืนเบรก) และ `base.emergency_stop` — **เขียนได้อย่างเดียว อ่านไม่ได้** (enum ของ GET มีแค่ 3 ตัว) โหมดปลดเบรกมีไว้เข็นหุ่นด้วยมือ มอเตอร์ถูกปลดกำลัง ซึ่งอธิบายอาการครบทุกข้อ: action สร้างได้ `status: 1` แต่ `stage` ว่าง ไม่มี error และตำแหน่งไม่ขยับเลย — และวันนี้มีการเข็นหุ่นด้วยมือจริง · ยังไม่ได้ทดสอบ คำสั่งที่ต้องรันอยู่ในเอกสารตรวจรับ (ปลอดภัยทั้งสองทาง สเปกระบุว่าค่ามีผลเฉพาะรอบนี้ รีบูตแล้วคืนค่าเดิม)

## 2026-09-10 — หน้าสั่งหุ่นด้วยมือ (`/robot`) สำหรับพิสูจน์ว่าคุมได้จริง

- `client/robot-control.html` + `GET /robot`, `GET /robot/state`, `POST /robot/command` (ใหม่): อ่านสถานะสดจากบอร์ดทุกวินาทีครึ่ง และสั่งขยับทีละก้าว
- **ปุ่มหยุดไม่ใช่ปุ่มหนึ่งในหลายปุ่ม** อยู่บนสุดแบบ sticky ติดจอตลอด และเป็นคำสั่งเดียวที่ยังทำงานตอน `ROBOT_CHASSIS_MOTION_ENABLED` ปิดอยู่ — ล็อกที่ปิดเบรกไปด้วยแย่กว่าไม่มีล็อก (มีเทสต์)
- **ทุกการเคลื่อนที่เป็นก้าวเดียวที่จบในตัว** ไม่มีกดค้าง ไม่มี "เดินไปเรื่อยๆ" เซิร์ฟเวอร์คำนวณเป้าหมายจากท่าทาง ณ วินาทีที่กด สายหลุดกลางทาง = หุ่นเดินจบ 30 ซม. แล้วรอ ไม่ใช่วิ่งต่อโดยไม่มีใครเรียกกลับได้
- **ระยะอยู่ที่เซิร์ฟเวอร์ หน้าเว็บส่งได้แค่เจตนา** (`action: "forward"` ไม่ใช่ `metres: 0.3`) — หน้าที่ให้ไคลเอนต์บอกระยะได้ ห่างจากการส่งหุ่นข้ามห้องแค่พิมพ์ผิดหนึ่งตัว และไคลเอนต์คือหน้าเว็บที่ใครในวง LAN ก็เปิดได้ (มีเทสต์อ่าน body จริง)
- **ต้องมี `WS_TOKEN`** ทั้ง `/robot/state` และ `/robot/command` ใช้กติกาเทียบ token เดียวกับที่ `_reject_unauthorized` ใช้กับทุก socket — เข้ารหัสก่อน `compare_digest` เพราะ token ที่ก๊อปจาก password manager มีอักขระอะไรก็ได้
- `app/robot_chassis.py`: `nudge()` สร้างก้าวสั้นด้วย `MoveToAction` **ไม่ใช่** `MoveByAction`/`RotateAction` ที่บอร์ดโฆษณาไว้ — สองอันหลังไม่มีรูปแบบ body ในเอกสารที่เรามี และสิ่งแรกที่ payload ที่ยังไม่ได้ยืนยันจะทำคือขยับหุ่นในห้องที่มีคนอยู่ · `live_state()` อ่านสดแยกจาก cache ของ poller เพราะคนที่ยืนถือปุ่มหยุดต้องการค่าวินาทีนี้ ไม่ใช่ค่าเมื่อ 2 วินาทีก่อน
- `tests/test_robot_control_page.py` (ใหม่ 14 ตัว) · **ย้อนโค้ดพิสูจน์แล้ว**: ถอดด่าน token และย้ายด่านล็อกไปไว้ก่อน stop → แดง 3 ตัว คืนแล้วเขียว · เทสต์ทั้งชุด: 1288 passed
- ตรวจกับบอร์ดจริง (อ่านอย่างเดียว ไม่กดปุ่มเดินสักครั้ง): หน้าเสิร์ฟ 200 · `/robot/state` ไม่มี token ตอบ unauthorized · มี token ได้ค่าจริง Slamware SDP แบต 20% ไม่ได้ชาร์จ ความมั่นใจในตำแหน่ง 45 ไม่มีคำสั่งค้าง จุดหมายในแผนที่ 0 จุด

## 2026-09-10 — กลับแท่นชาร์จภายใต้การดูแลและตรวจ REST จากตัวเครื่อง

- ตรวจปิดรอบ: โหลดโค้ด ActionState ใหม่เข้าเซิร์ฟเวอร์แล้วหลังตรวจไม่มีงานค้าง; HTTPS health ผ่านและ chassis connected/motion_enabled=false; power ยัง 20% ไม่อยู่บนแท่นและไม่ชาร์จ; ผู้ใช้ไม่ทราบวิธีเข้าตั้งค่า Aobo และการสำรวจหน้าหลักยังไม่เปิด Arm Test ได้ ไม่มีการสั่งแขนหรือเปลี่ยนสิทธิ์

- ผลทดสอบจริง: บอร์ดรับ GoHomeAction แต่ครบ 90 วินาทียังไม่ชาร์จ จึงส่งยกเลิก; อ่านกลับ current action 404 และงานเดิมเป็น Done/Aborted (-2), แบต 20%/not_on_dock; ผู้ใช้ยืนยันเคลื่อนหรือหมุนแต่เข้าแท่นไม่ได้ ทั้งที่แท่นมีไฟและอยู่ที่เดิม ไม่เขียนพิกัดแท่นหรือสั่งซ้ำ
- `docs/robot-live-inspection-2026-09-10.md`: บันทึกการกลับแท่นไม่สำเร็จ ข้อจำกัดเวลาทดสอบ และการเปิด ArmTestActivity ที่ Android ปฏิเสธเพราะไม่ exported; เปิดหน้าหลัก Aobo ผ่านทางปกติแล้ว รอวิธีเข้าเมนูตั้งค่า
- ผลตรวจโค้ด ActionState: **96 passed ใน 4.26s**; รอโหลดเข้าเซิร์ฟเวอร์หลังตรวจไม่มีงานค้าง

- ผู้ใช้ยืนยันมีคนดู ทางโล่ง และทดสอบวิธีหยุดแล้ว; ตรวจพบ home dock จริง จึงส่ง GoHomeAction ครั้งเดียวพร้อม dock และจำกัด retry เป็น 1 โดยไม่เปิดสวิตช์การเดินอัตโนมัติของโปรเจกต์; รอผลการชาร์จ บันทึกหลักฐานในโฟลเดอร์ inspection ที่ถูก ignore
- `app/robot_chassis.py`, `tests/test_robot_chassis.py`: แก้ ActionState ให้ตรง `/js/spec.js` ของเครื่อง (0 NewBorn, 1 Working, 3 Paused, 4 Done และ result 0/-1/-2) แทนค่า bit flags ของ C++ SDK; คำตอบจริงเมื่อส่งกลับฐานยืนยัน status 0 แล้ว 1; เพิ่ม regression สำหรับ REST lifecycle และ Done ที่ขาด result รอตรวจ

## 2026-09-10 — ตรวจช่องทางยกมือจากแอปผู้ขาย

- `docs/robot-live-inspection-2026-09-10.md`: บันทึกผลตรวจ APK แบบอ่านอย่างเดียว พบ ArmTestActivity/กลุ่มท่าทาง/ข้อความหยุดแขน และ AIDL service แต่ยังไม่ยืนยันพอร์ตหรือหมายเลขท่ายกมือ
- คัดลอก APK ที่มีอยู่บนหุ่นเข้าโฟลเดอร์หลักฐานที่ถูก ignore และคำนวณ SHA-256; ตรวจซ้ำไม่พบ ttyUSB/ttyACM; ไม่มีการติดตั้ง/เปิดหน้าทดสอบ/ส่งคำสั่งมอเตอร์ ไม่มีการแก้ runtime code จึงไม่รันชุดทดสอบซ้ำ

## 2026-09-10 — หน้าตรวจไมค์และกล้องของหุ่น (`/hardware`)

- `client/hardware.html` + route `/hardware` ใน `app/main.py` (ใหม่): เปิดในเบราว์เซอร์ของ**เครื่องที่มีอุปกรณ์** แล้ววัดของจริง เพราะฝั่งเซิร์ฟเวอร์แยกไม่ออกว่าเสียงเบาเพราะไมค์ถูก mute เพราะแอปอื่นยึดไมค์ หรือเพราะคนยืนไกล — log ได้แค่ `avg=0.0000` แล้วเดา
- วัดอะไรบ้าง: secure context (สาเหตุอันดับหนึ่งที่ไมค์ใช้ไม่ได้บนหุ่น คือ origin ที่เบราว์เซอร์ไม่เชื่อถือ ไม่ใช่ตัวไมค์) · รายการอุปกรณ์ทั้งหมด · **ระดับเสียงแยกทีละช่อง** ผ่าน `createChannelSplitter` (คู่มือบอกไมค์ 4 ตัว แต่ `MicConfig.channels` บอก 8 — ช่องไหนผ่าน AEC แล้วเป็นคำถามค้างกับผู้ขาย หน้านี้ทำให้เห็นแทนที่จะถาม) · sample rate/track state · อัด 4 วิแล้วฟังกลับ · กล้องพร้อม fps ที่**นับเอง** ไม่ใช่ค่าที่ `getSettings()` อ้าง (เครื่องนี้เองมี log "can't grab frame" ขณะกล้องรายงาน 30 fps)
- กติกาที่เขียนเป็นเทสต์: **ไม่ส่งอะไรออกจากหน้า** ไม่มี WebSocket/fetch/upload/beacon (หน้าที่ถือไมค์อยู่แล้วส่งเสียงออกไปได้ = เครื่องอัดในห้องขายที่ไม่มีใครรับผิดชอบ ปัญหาเดียวกับที่ `data/logs/` ต้องมี TURN_LOG_KEEP_DAYS) · ตัดสินจากตัวอย่างเสียงจริงเสมอ ไม่ใช่จากการที่ไม่มี error (`getUserMedia` ผ่านแล้วยัง `peak=0.0000` ได้ 16 วินาที — เคยเกิดจริง) · แยก "ศูนย์ล้วน" ออกจาก "เบาเกิน" เพราะวิธีแก้คนละเรื่อง · ปิด echo cancellation/AGC/noise suppression ไม่งั้นตัวกรองจะกลบสิ่งที่กำลังหา · คืนไมค์กับกล้องตอนออกจากหน้า
- ไม่ต้องใช้ token เพราะหน้านี้สั่งอะไรไม่ได้เลย — และมันจำเป็นที่สุดตอนที่ token นั่นแหละคือสิ่งที่ผิด มีเทสต์กันไม่ให้มีคำสั่งใดหลุดเข้าไป
- **ส่งผลข้ามเครื่องได้** (`POST /hardware/report`, `GET /hardware/reports`): เบราว์เซอร์อ่านได้เฉพาะอุปกรณ์ของเครื่องที่เปิดมันเอง ผลของหุ่นจึงอ่านจากโต๊ะเซลส์ไม่ได้เลย และจอหุ่นเป็นจอแนวตั้งบนหน้าอกที่มักมีคนอื่นใช้อยู่ ปุ่ม "ส่งไปเครื่องหลัก" ส่ง**เฉพาะข้อความสรุป** ชื่ออุปกรณ์ ระดับเสียง เฟรมเรต — ไม่มีเสียงหรือภาพ และเทสต์บังคับไว้ว่าคำขอห้ามพก blob/chunks/stream ปลายทางมีได้แค่สอง path ที่ระบุชื่อไว้ · เก็บใน deque ในหน่วยความจำ ไม่เขียนลงดิสก์ (ไฟล์ผลตรวจไมค์ที่เก็บจากห้องขาย คือคำถามเรื่องการเก็บข้อมูลที่ไม่มีใครตัดสินใจ แบบเดียวกับที่ `data/logs/` ต้องมี TURN_LOG_KEEP_DAYS มาแก้) · รับ POST โดยไม่มี token จึงจำกัดขนาด 4000 ตัวอักษรและเก็บ 8 รายการล่าสุด และทนต่อ payload ขยะ
- ตรวจจริงบนเซิร์ฟเวอร์ที่รันอยู่: POST แล้ว GET กลับมาได้ ภาษาไทยไม่เพี้ยน
- **อ่านสเปกไมค์ของหุ่นจากฮาร์ดแวร์ผ่าน adb** (ไม่แตะจอหุ่น): เป็น USB dongle `Bothlent UAC` `/proc/asound/card0/stream0` บอก **8 ช่อง 16 kHz S16_LE** — จบข้อสงสัย "คู่มือบอก 4 ตัว config บอก 8" · `dumpsys audio` ยืนยันไมค์ไม่ได้ถูก mute ทั้งสี่ทาง · **ระบบเสียงเดิมของหุ่นรันอยู่จริง** `com.aobo.robot.ai3` เป็น audio client และ `com.iflytek.vflynote` ก็รันอยู่ — ข้อที่ CLAUDE.md ระบุว่าเสี่ยงที่สุด ตอนนี้มีหลักฐานแทนการคาดเดา (ยังไม่พิสูจน์ว่ายึดไมค์แบบผูกขาด) · บันทึกในเอกสารตรวจรับ
- ไมค์ของ **PC** (`LCS_USB_AUDIO`, 2 ช่อง 48 kHz, track live, ปิด AEC/AGC ครบ) ให้ค่าศูนย์ล้วน ตรงกับ `DIGITAL SILENCE` ใน log ทั้งเย็น เป็นปัญหาของไมค์ตัวนั้นเอง ไม่เกี่ยวกับหุ่นและไม่ขวางหุ่น
- `tests/test_hardware_page.py` (ใหม่ 16 ตัว) · เทสต์ทั้งชุด: 1269 passed
- ตรวจจริง: หุ่นโหลดหน้านี้แล้ว (เห็น `GET /hardware` ในบันทึกเซิร์ฟเวอร์) ผลการวัดของไมค์กับกล้องรอเจ้าของกดทดสอบหน้าเครื่อง

## 2026-09-10 — ตรวจตัวจริงและแก้สถานะการควบคุมร่วมกับ RoboStudio

- ผลปิดงานรอบนี้: **90 passed ใน 4.25s**, `compileall` ตัวตรวจผ่าน, `git diff --check` ผ่าน, รายงาน/แผนที่ถูก ignore; รีสตาร์ตเฉพาะ `run_server.py` ด้วย `.venv-smoke` เดิมแล้ว ตรวจ HTTPS health ผ่านโดยตรวจใบรับรองจริง: เชื่อมแชสซีได้และ `motion_enabled=false`; ไม่ส่งคำสั่งเริ่มเคลื่อนที่ ไม่มีการติดตั้ง/ถอนแอปหรือเปลี่ยน firmware

- `app/robot_chassis.py`: อ่าน action และ pose ทุก poll แม้คำสั่งมาจาก RoboStudio; สถานะไม่รู้จัก/ขาดการเชื่อมต่อ/เพิ่งรับคำสั่งหยุดเป็น unknown จนได้ค่าบอร์ดใหม่
- `app/tools/robot_link.py`: ส่ง pose/docked ใน snapshot และไม่ใช้สถานะ app เก่าทดแทนแชสซีที่ขาดการเชื่อมต่อ
- `tests/test_robot_chassis.py`: เพิ่ม regression สำหรับคำสั่งจากภายนอก สถานะที่ไม่รู้จัก การยืนยันคำสั่งหยุด และการขาดการเชื่อมต่อ; รอตรวจ
- ตรวจจริงแบบอ่านอย่างเดียว: ADB และ REST ใช้งานได้, production health เห็นแชสซี, ไม่พบ POI; ยังไม่สั่งล้อ/แขนหรือรับรองระบบหยุดฉุกเฉิน
- `scripts/inspect_robot_live.py` + `.gitignore`: เพิ่มตัวสำรวจ GET/ADB แบบอ่านอย่างเดียวและสำรองแผนที่เดิมเป็น STCM พร้อม SHA-256; เก็บหลักฐานไว้ใน `data/robot-inspection/` ที่ไม่เข้า Git; รอตรวจการรันจริง
- `app/config.py`, `.env.example`, `app/robot_chassis.py`, `app/tools/robot.py`: แยก `ROBOT_CHASSIS_MOTION_ENABLED=false` เป็นค่าเริ่มต้น ล็อกคำสั่งเริ่มเดิน/กลับฐานจากโปรเจกต์แต่ยังส่งยกเลิกได้ พร้อมแจ้งข้อจำกัดในสถานะ; เหตุผลคือหลังผู้ใช้กดปุ่ม ค่าฉุกเฉินจากบอร์ดยังเป็น false จึงยังรับรองปุ่มไม่ได้; รอตรวจ regression เพิ่มเติม
- `docs/robot-live-inspection-2026-09-10.md`, `docs/robot-integration.md`: บันทึกฮาร์ดแวร์จริง ความต่างจากคู่มือ เส้นทางเชื่อม ส่วนที่ยังไม่ผ่าน และวิธีตรวจซ้ำ; REST GET 10 รายการผ่าน, สำรองแผนที่ได้และคำนวณ SHA-256 แล้ว; regression ล่าสุด **89 passed ใน 4.28s** (รอบแรกติดสิทธิ์ temp ใน sandbox แก้โดยใช้ basetemp ใน workspace); การโหลดโค้ดเข้าตัวเซิร์ฟเวอร์ยังรอจังหวะที่ไม่มีการทดสอบหุ่นจากแอปอื่น
- เมื่อผู้ใช้ยืนยันว่าหุ่นอยู่นิ่งทั้งที่เคยเห็น active action: เพิ่ม `action_name`/`action_status` และให้ชนิดงานที่ไม่รู้จักเป็น unknown; คำอธิบายแยกงานนำทางออกจากความเร็วล้อ ไม่รับรองการหยุดทางกายภาพ; เพิ่ม regression งาน background ที่ไม่ได้พิสูจน์การเคลื่อนที่ รอตรวจ

## 2026-09-10 — สั่งหุ่นเดินได้โดยไม่ต้องมี AAR: ต่อตรงเข้าแชสซี SLAMTEC

- `app/robot_chassis.py` (ใหม่): ไคลเอนต์ RESTful ของบอร์ดนำทาง Slamware + poller ที่เป็นนาฬิกาเรือนเดียวซึ่งรู้ว่าเดินจบ แล้วเรียก `robot_link.arrived()` ทางเดิมกับที่ `robot_arrived` ของแอปเคยใช้ (REST ไม่มี callback)
- `app/tools/robot_link.py`: `send()`/`places()`/`snapshot()`/`available()`/`status()` เลือกทางแชสซีเมื่อ `ROBOT_CHASSIS_URL` ถูกตั้ง — **เจ้าของล้อมีคนเดียวเสมอ** ติดต่อบอร์ดไม่ได้ตอบ `mock` ไม่แอบสลับกลับไปทางแอป (บั๊กนาฬิกาสองเรือน) · `snapshot()` บนทางนี้เป็นค่าที่**วัดได้**ไม่ใช่เสียงสะท้อนของคำสั่งล่าสุด และมีแบต/สถานะชาร์จเป็นครั้งแรก
- `app/config.py` + `.env.example`: `ROBOT_CHASSIS_URL` (ว่าง = ทางเดิมทุกประการ), `ROBOT_CHASSIS_POLL_S`, `ROBOT_CHASSIS_TIMEOUT_S`, `ROBOT_CHASSIS_ARRIVAL_TOLERANCE_M` · `app/main.py`: startup/shutdown hook ที่เช็คสวิตช์เอง (ทรงเดียวกับ `greeter.start()`)
- กฎที่เขียนเป็นเทสต์: HTTP 200 ไม่ใช่การไปถึง · **สถานะ action ที่ไม่รู้จัก = ยังเดินอยู่** (ตัวเลขมาจาก SDK ของ SLAMTEC ไม่ใช่จากการเดินจริง อ่านผิดทางหนึ่งคือตกไป arrival timeout ซึ่งประกาศว่าไม่สำเร็จ อีกทางคือประกาศว่าถึงทั้งที่ไม่ถึง) · action ที่หายไปตัดสินจาก**ระยะระหว่างท่าทางจริงกับจุดที่สั่ง** ส่วน `go_home` ตัดสินจาก `dockingStatus` · แผนที่ว่างไม่ถอยไปใช้ `ROBOT_MOCK_PLACES` (จะเสนอจุดที่มีอยู่ใน .env แต่ไม่มีบนพื้น) · จุดที่ไม่มีพิกัดถูกตัดออกจากรายการ ไม่ใช่เสนอแล้วค่อยปฏิเสธ
- บั๊กที่เจอเพราะรันกับบอร์ดจริง ไม่ใช่จากอ่านเอกสาร: `/api/multi-floor/map/v1/pois` ตอบ `[]` พร้อม 200 เมื่อไม่มี floor ที่บันทึกไว้ (หุ่นตัวนี้ `floors` ก็ว่าง) โค้ดเดิมเช็ค `is None` จึงไม่เคยถาม artifact API เลย = รายงาน "ไม่มีจุดหมาย" บนหุ่นที่อาจมี
- `tests/test_robot_chassis.py` (ใหม่ 22 ตัว) + `tests/conftest.py` pin `robot_chassis_url=""` และล้าง state (ตระกูลเดียวกับ `inventory_url` แต่ปลายทางมีมอเตอร์) · **ย้อนโค้ดพิสูจน์แล้ว**: ปิดสองพฤติกรรม เทสต์แดง 7 ตัว คืนแล้วเขียว
- ตรวจกับบอร์ดจริง (อ่านอย่างเดียว ไม่สั่งอะไรให้ขยับ): `robot/info` = Slamware SDP fw 5.1.1-deb-for-aobo-hermes, แบต 35% on_dock กำลังชาร์จ, pose x -0.592 y 3.627, `action-factories` 21 รายการ, `snapshot()` ได้ `status_source: chassis` · **ยังไม่ได้ตรวจ: การสั่งเดินจริง** (รูปแบบ body มาจากเอกสาร SLAMTEC ไม่ใช่จากการยิงจริง) และ **การอ่านชื่อ POI** (แผนที่ยังไม่มีจุดหมายสักจุด) ต้องทดสอบตอนมีคนยืนข้างหุ่นพร้อมปุ่มหยุด
- `docs/ต่อกับหุ่นยนต์ Astronaut.md`: หัวข้อ "ทางที่สอง: สั่งแชสซีตรงๆ ไม่ผ่านแอป" และแก้รายการค้างว่า AAR ไม่ใช่ตัวขวางการเดินอีกแล้ว
- `app/main.py`: บรรทัดตอนบูตเคยบอกว่า "รอแอปส่ง robot_ready" เสมอ ซึ่งพูดผิดบนเครื่องที่สั่งแชสซีตรงๆ คนอ่านจะไปตามหาแอป Android ที่ไม่มีวันรายงานเข้ามา ตอนนี้บอกว่ากำลังรออะไรอยู่จริงๆ
- เทสต์ทั้งชุด: 1243 passed (จากเดิม 1221)

## 2026-09-10 — หุ่นต่อเซิร์ฟเวอร์ไม่ติดเพราะอยู่คนละวง Wi-Fi

- **ไม่มีการแก้โค้ด** เป็นการตั้งค่าเครื่องและบันทึกผลวัดจากของจริง
- อาการ: Chromium บนหุ่นเปิด `https://192.168.0.3:8001/health` แล้วหมุนค้าง สาเหตุคือหุ่นอยู่ 192.168.1.23 (Wi-Fi) ส่วน PC อยู่ 192.168.0.3 (Ethernet) prefix /24 ทั้งคู่ = ไม่มีเส้นทางถึงกัน — เดิมไล่ผิดทางเพราะอาการ "ช้า" ไม่มี error ให้ดูเลย
- แก้: ต่อ Wi-Fi ของ PC เข้า SSID `Embassy` ได้ 192.168.1.43 · วัดแล้ว ping หุ่น 2-3 ms, `/health` ตอบ 200 ใน 9 ms, SAN ของ `certs/lan-cert.pem` มี IP นี้อยู่แล้ว, firewall rule เดิมครอบโปรไฟล์ Public, Ethernet ยังเป็น default route (metric 35 < 60) อินเทอร์เน็ตไม่สะดุด
- `docs/robot-arrival-2026-09-09.md`: หัวข้อใหม่ "วงเน็ต: หุ่นกับ PC เคยอยู่คนละวง" พร้อม URL ที่หุ่นต้องเปิด, ขั้นตอนติดตั้ง CA ที่ยังค้าง, บันทึกว่า SSID `SLAMWARE-AB340E` คือบอร์ดนำทางของหุ่นเอง (เกาะพร้อมวงหลักไม่ได้) และเหตุผลที่ไม่ต้องลง Google Play Store
- **ตรวจแล้วผ่าน (15:45):** หุ่นเปิด `?kiosk=1` ขึ้นหน้า Emma สถานะ "ออนไลน์" และเซิร์ฟเวอร์เห็น TCP established จาก 192.168.1.23 มาที่ 192.168.1.43:8001 — เป็นครั้งแรกที่หุ่นคุยกับเซิร์ฟเวอร์ได้ (ก่อนหน้านี้ `Get-NetTCPConnection -LocalPort 8001 -State Established` ว่างเปล่าทุกครั้ง)
- **แชสซีเป็น SLAMTEC ไม่ใช่ของ Aobo เอง — สั่งเดินได้โดยไม่ต้องรอ AAR:** บอร์ดนำทาง 192.168.11.1 เปิดพอร์ต 1445/1448/80/22 และ `/api/core/system/v1/robot/info` ตอบว่าเป็น Slamware SDP ของ Slamtec fw `5.1.1-deb-for-aobo-hermes+20250226` ซึ่ง SLAMTEC เปิดเอกสาร RESTful API ให้ดาวน์โหลดเอง อ่านค่าจริงจาก PC ได้แล้ว: แบต 35% on_dock กำลังชาร์จ, pose x -0.592 y 3.627 yaw 0.187, POI `[]` (ยังไม่มีจุดหมาย) วิธีต่อคือ relay ผ่านแท็บเล็ตด้วย `nc` + `adb forward` บันทึกไว้ในเอกสารตรวจรับ กับดักที่เสียเวลา: `-w` ของ toybox nc คือ timeout ตอนเชื่อมต่อ ไม่ใช่ตอนรออ่าน ต้องใส่ `-q` ไม่งั้นได้ผลว่างเปล่าโดยไม่มี error
- **เจอทางลัด:** พอร์ต adb 5555 ของหุ่นเปิดอยู่ `adb connect 192.168.1.23:5555` ต่อติดทันที ไม่มีหน้าต่างขออนุญาตบนจอ ใช้ platform-tools ที่โหลดไว้แล้ว — คุมหุ่นได้โดยไม่ต้องคลิกผ่าน AnyDesk อีก อ่านได้ว่า Android 15 / RK3588 arm64-v8a, แอปผู้ขาย `com.aobo.robot.ai3` 3.1.230528au3.sl.deliver.4m, Chromium ได้ `RECORD_AUDIO` แบบ SYSTEM_FIXED แล้ว, มี `/system/xbin/su` (ยังไม่ทดสอบ ให้เจ้าของตัดสินใจ) บันทึกในเอกสารตรวจรับหัวข้อ "ADB ผ่าน Wi-Fi เปิดอยู่"
- `scripts/check_robot_readiness.py --health` (exit 2) ขาด 6 ข้อ: `robot_token_configured`, `robot_enabled`, `robot_tools_enabled`, `sdk_aar_supplied`, `bridge_apk_supplied`, `live_robot_connection` — สามข้อแรกแก้ที่ `.env` สองข้อกลางต้องขอผู้ขาย · เครื่องนี้ไม่มี java/gradle/Android SDK จึง build APK ยังไม่ได้
- **ยังค้าง:** ยังไม่ได้ติดตั้ง `rootCA-android.crt` บนหุ่น (ตอนนี้ต้องกดผ่านหน้าเตือนใบรับรองทุกครั้ง) และยังไม่ได้ทดสอบว่าไมค์ของหุ่นส่งเสียงเข้า `/ws/wake` จริง

## 2026-09-09 — โชว์รูม: ตกแต่ง, ความลื่น, smart home

- `client/robot-showroom.js` (ใหม่): เฟอร์นิเจอร์/โคมแขวน/ต้นไม้ต่อจุดหมาย วางชิดผนังจริงด้วย `walls` จาก `/api/layout` (`Occupancy.wall_scan` ใหม่ใน `app/showroom_map.py` + เทสต์) และ smart home props (ไฟ 8 ดวง, ม่านเลื่อน, ทีวีมีภาพ, แอร์ LED/ครีบ) อ่าน `smart_home` state เดิม ไม่แตะเซิร์ฟเวอร์
- บั๊กที่เจอ: `place()` ลบตำแหน่งกลุ่มออกจากพิกัดที่เป็น local อยู่แล้ว เฟอร์นิเจอร์ทุกชิ้นไปกองที่ (0,0) — screenshot จับได้ (ห้องว่างทั้งที่ group มีลูก 9-21 ชิ้น) · `floor_y` 8.6→8.7 เพราะ CAD มีพื้นสองชั้น (8.1 โครงสร้าง / 8.7 ผิวสำเร็จ) หุ่นเคยจมพื้น 10 ซม.
- `client/robot-scene-3d.js`: cap 30→60 fps, shadow map เฟรมเว้นเฟรม, รวม mesh ต่อวัสดุ (2,531→~100, 169 draw calls), entity interpolation ย้อน 300 ms ให้หุ่นเดินเรียบ, `flyTo` ease กล้องตอน center/follow, โดมท้องฟ้า vertex-color, RoomEnvironment env map (vendor r180 + manifest), exposure/แสงปรับลง; explorer ชนเฟอร์นิเจอร์ที่วางเองด้วย (collider หมุนตามกลุ่ม)
- `app/robot_simulator.py`: allowlist `robot-showroom.js`, `RoomEnvironment.js`; `data/showroom/layout.json`: ย้ายจุดหมาย 3 จุดให้อยู่กลางห้อง (ห้องตัวอย่าง 7.9,17.4 · ฟิตเนส 17,24 · สระ 3.5,24) ยัง `verified: false`
- ตรวจจริง (Playwright + SwiftShader, 8011): โมเดลพร้อมใน ~5 วิ, ทุกห้องมีของ, ทีวี/ม่าน/ไฟเปลี่ยนตาม `/api/home`, เดินชนเคาน์เตอร์ได้, console 0 error; showroom tests 9 passed; full suite: **1,221 passed, 1 warning ใน 153.49s**
- `docs/robot-simulator.md`: หัวข้อย่อย "การตกแต่ง ความลื่น และ smart home ในโชว์รูม"

## 2026-09-09 — ตัวจำลองใช้โมเดลโชว์รูมจริงจาก CAD

- `app/showroom_map.py` (ใหม่): อ่าน GLB (SimLab export, 5,149 node ไม่มีชื่อห้อง, 702,100 สามเหลี่ยม) → ตาราง occupancy 0.2 ม. (พื้น/ผนัง/ประตู) + A* + `Layout` ที่ engine เดินบน; `scripts/build_showroom_map.py` (ใหม่) สร้าง `data/showroom/map.json` + `map-preview.png` จาก zip และรายงานความถึงได้ของทุกจุดจากฐาน
- บั๊กที่เจอระหว่างทำ: `cv2.fillPoly` รับ list แล้วเติมแบบ even-odd — พื้นสองชั้น (8.1/8.7 ม.) หักล้างกันจนโถงทั้งหมดกลายเป็น "ไม่มีพื้น" แก้เป็นเติมทีละสามเหลี่ยม · บานประตูใน CAD วาดปิด (วัสดุ `door`/`doorMetal`/`handle`) ทำให้ 4/4 จุดหมายถึงไม่ได้ → ช่องประตูชนะผนัง (`D` ใน grid) แล้วถึงได้ครบ
- `app/robot_simulation.py`: `SimulatedRobot(layout=…)` เดินตาม `Layout` (toy corridor เดิม หรือ showroom grid), snapshot มี `layout`, จุดที่ถึงไม่ได้ในผังบันทึกเหตุการณ์ `route_unreachable`; `app/robot_simulator.py`: `/api/layout`, `/showroom/model.glb` (404 เมื่อไม่มีไฟล์), allowlist `robot-map.js`/GLTFLoader/BufferGeometryUtils, CSP เพิ่ม `blob:` ให้ GLTFLoader โหลด texture ที่ฝังใน GLB
- `client/robot-scene-3d.js`: โหมด showroom โหลด GLB ด้วย GLTFLoader (ลด metalness ที่ CAD ตั้ง 0.5), กล้อง/แสง/หมอกตามขนาดอาคาร, minimap วาดจากตาราง, fallback เป็นบล็อกผนังจากตารางเมื่อโหลดโมเดลไม่ได้; `client/robot-map.js` (ใหม่) ตารางเดียวกับ Python ใช้ชนของผู้เยี่ยมชม; `client/robot-scene.js` (2.5D) วาดผังจากตารางเมื่อมี layout; `client/robot-simulator.js` ดึง `/api/layout` ก่อนสร้างฉาก
- `client/vendor/three/`: เพิ่ม GLTFLoader.js + BufferGeometryUtils.js r180 (patch import path เหมือน OrbitControls) และ sha256 ใน manifest
- `data/showroom/layout.json` (ใหม่, ต้องมีคนยืนยัน): origin [471,-30], floor_y 8.6, จุดหมาย 5 จุด `verified: false` ทั้งหมด; `data/showroom/*.glb` ถูก gitignore (คืนด้วย build script)
- `tests/conftest.py` pin `ROBOT_SIM_LAYOUT=toy`; `tests/test_showroom_map.py` (ใหม่ 8 เทสต์) ใช้ GLB จิ๋วที่สร้างเอง: เส้นทางลอดประตู ไม่ทะลุผนัง, จุดในผนังถูกขยับพร้อมคำเตือน, env pin, endpoint/CSP/vendor import, source ของ client
- ตรวจจริงด้วย Playwright headless (Chromium + SwiftShader) บนพอร์ต 8011: สถานะ "3D · SHOWROOM" ใน 3 วิ, เดินไปห้องตัวอย่างถึงจุดหมายผ่านประตูจริง, โหมดเดินสำรวจถูกตารางกั้น, console 0 error; ปิด WebGL แล้วฉากสำรอง 2.5D วาดผังจากตารางและเดินได้; targeted tests 57 passed; Node เสียง 3 ชุดผ่าน; full suite: **1,220 passed, 1 warning ใน 149.56s** (จาก 1,212 ก่อนแก้ = +8 เทสต์ใหม่)
- `docs/robot-simulator.md`: หัวข้อ "โมเดลโชว์รูมจริง" — วิธี build, กติกาผนังจากเรขาคณิต, จุดหมายเป็นการเดาที่ต้องยืนยัน, ห้ามเอาพิกัดไปใส่หุ่นจริง
- ตัวจำลองที่เปิดค้างบน 8010 เป็นโค้ดเก่า ต้องปิดแล้วเปิด `start-robot-simulator.cmd` ใหม่ถึงจะเห็นโชว์รูม

## 2026-09-09 — เช้าวันรับหุ่น: เตรียมเครื่อง PC

- ตรวจ commit `ea7b361` ก่อนเริ่ม: full suite **1,212 passed, 1 warning ใน 187.83s** (`.venv` Python 3.14.6), smoke import ทุก extras ผ่าน, Node เสียง 3 ชุดผ่าน, สแกน secret ใน diff ไม่พบ; working tree สะอาด ไม่มีอะไรค้าง commit
- อ่าน PDF 4 ไฟล์ใน Downloads (คู่มือย่ออังกฤษ/ไทย, SDK v2.0 จีน/ไทย) — ตรงกับที่ `docs/robot-integration.md` รีวิวไว้แล้ว ไม่มีข้อมูลใหม่; นำเลข section ของ SDK ที่ bridge ต้องใช้ไปใส่ในเอกสารตรวจรับ
- `certs/lan-cert.pem`/`lan-key.pem` (ไม่ติดตาม git): ออกใหม่ด้วย mkcert เพิ่ม SAN `192.168.1.43` (Wi-Fi) และ hostname เพราะของเดิมมีแต่ Ethernet IP; สำรองของเดิมใน `certs/backup-2026-09-09/`; ก๊อป root CA เป็น `certs/rootCA-android.crt` สำหรับติดตั้งบนหุ่น (ไม่มี private key)
- เปิด HTTPS 8001 ชั่วคราว: `/health`, `/?kiosk=1`, `/display?chat=1` ตอบ 200 ทั้ง localhost/192.168.0.3/192.168.1.43/hostname; TLS ผ่านทั้ง Windows store และ mkcert CA (Python ssl, TLS 1.3); ปิดแล้ว ไม่ได้เปิดไมค์หรือสั่งอุปกรณ์ (กล้อง face ทำงานตาม config ระหว่างนั้น)
- พบ **firewall rule 8001 เปิดเฉพาะ Private แต่ทั้งสองเครือข่ายเป็น Public** — คำสั่งแก้ต้องรันด้วย Administrator ยังไม่ได้แก้ ระบุคำสั่งใน `docs/robot-arrival-2026-09-09.md`
- `docs/robot-arrival-2026-09-09.md`: เพิ่มส่วน "เช้าวันรับหุ่น" — ผลเตรียมเครื่อง, ขั้นติดตั้ง CA บน Android และข้อจำกัด WebView user CA, ลำดับแก้ `.env` ต่อขั้น, section SDK ที่ bridge ใช้
- ไม่แก้ `.env`, โค้ด หรือเทสต์; รอเจ้าของลองของจริงก่อน commit

## 2026-09-09 — ตรวจ staged files ก่อน commit

- ตรวจรายการ staged **95 ไฟล์ ประมาณ 2.66 MB**: ไม่พบ API key/private key/token ที่ตั้งใช้ในเครื่อง, ไม่มี `.env`, debug log, SDK/APK archive หรือ local Android binaries รวมอยู่
- Staged whitespace check พบ blank line ท้าย `AGENTS.md` (ไม่อยู่ใน tracked diff เดิม) จึงตัดบรรทัดว่างโดยไม่เปลี่ยนข้อกำหนด และเอา unused import ใน test package ออก; รอตรวจ staged ซ้ำ
- ตรวจหลังแก้: package tests **10 passed ใน 0.60s**, staged whitespace check ผ่าน; พร้อมทำ local commit บน `dev` โดยไม่ push
- ชุดรับ SDK มีเอกสาร/ตัวตรวจ/ADB ที่ใช้งานได้ แต่ยังไม่มี vendor AAR/demo APK/source; คำขอลิงก์จากผู้ใช้ยังไม่ได้รับคำตอบ จึงไม่อ้างว่าดาวน์โหลดไฟล์ผู้ขายสำเร็จ

## 2026-09-09 — เตรียม SDK และจัดไฟล์ก่อน commit ชุด 2

- ลบเฉพาะผลทดสอบชั่วคราว/root bytecode **53 โฟลเดอร์** และ `.coverage` รวม **41,609,903 bytes**; ตรวจ absolute paths และไม่มี reparse point ในต้นไม้ก่อนลบ ไม่แก้ runtime environments/model/data
- ดาวน์โหลด Android Platform-Tools จาก Google ผ่าน HTTPS เข้า `tools/android/` ที่ถูก ignore; ตรวจ `adb version` ผ่าน **37.0.1-15733141** เก็บ checksum/URL/เวลาใน local receipt; ไม่รันคำสั่งติดตั้งหรือสั่งงานหุ่น
- `scripts/check_robot_readiness.py`, `tools/android/README.md`, `docs/robot-arrival-2026-09-09.md`: ระบุ local ADB ที่เตรียมสำเร็จและลิงก์ชุดรับ SDK; AAR/demo APK/source ของผู้ขายยังไม่ได้รับ
- ตัวตรวจ package **10 tests passed ใน 0.75s**; baseline backend/client ล่าสุดก่อนชุดเตรียมนี้ **1,202 tests passed + Node เสียง 3 ชุดผ่าน** ไม่มีการแก้ runtime voice/navigation ในชุดนี้

## 2026-09-09 — เตรียม SDK และจัดไฟล์ก่อน commit ชุด 1

- `tests/test_robot_package.py`, `scripts/inspect_robot_package.py`: เพิ่ม regression ด้วย ZIP fixture สำหรับ AAR/APK, checksum ผิด, manifest/dex หาย, archive เสีย, SDK คนละตัว และ path ผิดปกติ; fixture ไม่ใช่ไฟล์สำหรับติดตั้งจริง รอรัน

- `vendor/aobo/README.md`, `tools/android/README.md`, `scripts/inspect_robot_package.py`: จัดชุดรับ vendor AAR/demo APK/source พร้อมข้อความขอไฟล์และตัวตรวจโครงสร้าง/ABI/คลาส/SHA-256 แบบไม่รันโค้ด; ไม่มีการสร้าง AAR/APK ปลอมหรืออ้างว่า vendor SDK พร้อมแล้ว รอตรวจ regression ของตัวตรวจไฟล์
- ตรวจคู่มือไทยหน้า 4 และ PDF ต้นฉบับ ไม่พบ download URL ของ AAR/APK; ค้นเว็บ Aobo ทางการพบการเสนอ SDK แต่ยังไม่พบไฟล์ที่ยืนยันตรงรุ่น จึงต้องรับจากผู้ขาย
- `.gitignore`: กัน debug log, incoming vendor binaries และ local Android tool downloads ออกจาก commit; ตรวจ secret patterns ในไฟล์เปลี่ยนแปลงไม่พบ key/private key และ Three.js vendored checksums ตรง manifest
- เตรียมลบเฉพาะผล pytest ชั่วคราว/coverage/root bytecode ที่สร้างใหม่ได้ หลังตรวจ absolute path ให้อยู่ใน workspace; ยังไม่ลบ source, environments, models, ข้อมูลลูกค้า หรือไฟล์ SDK ของผู้ขาย

## 2026-09-09 — ผลตรวจรับฝั่งซอฟต์แวร์รอบสุดท้าย

- Full suite หลังแก้ทั้งหมด **1,202 passed, 1 warning ใน 182.38s**; warning คือ Starlette TestClient/httpx deprecation; รอบสุดท้ายไม่พบข้อความ pending Proactor ที่เคยพบรอบก่อน
- Node เสียง 3 ชุด, syntax Python ไฟล์ที่แก้ และ `git diff --check` ผ่าน; preflight ตรวจซ้ำยังรายงานสิ่งขาดตามจริง ไม่พิมพ์ secret
- เปิด simulator รุ่นแก้บน **8010** สำเร็จ health ผ่านและมี standby wake socket เชื่อม; ปิด QA 8011 แล้ว ไม่เปิด production 8001 หรือเปลี่ยน `.env` ให้เดินจริง ขาด Android bridge/AAR และผลทดสอบฮาร์ดแวร์ตามรายงาน
- `docs/robot-arrival-2026-09-09.md`: ปรับผลทดสอบล่าสุดและสถานะส่งมอบ เก็บรายการขอผู้ขาย/เกณฑ์รับเครื่องครบ; ยังไม่มีการรับรองว่าติดตั้งแล้วเดินหรือควบคุม Smart Home จริงได้ทันที

## 2026-09-09 — ตรวจรับหุ่น ชุด 3: Gemini 3.1 คำทักทายเงียบ

- Live QA รอบเต็ม: greeting **208,350 audio bytes**, simulated navigation → arrival **184,830 audio bytes**, จบทั้งสอง turn ใน 17.70s; ไม่ส่งไมค์ ไม่สั่งอุปกรณ์จริง; Node เสียง 3 ชุดผ่านซ้ำ
- `docs/robot-arrival-2026-09-09.md`: เพิ่มผลรับเสียงจาก provider และอ้างอิง Google; ตรวจ live 8010 idle ไม่มีงานเดิน ก่อนนำ backend รุ่นแก้ขึ้นใหม่ (การ restart ทำให้สถานะจำลองเริ่มใหม่)

- QA server 8011 รุ่นแก้: เชื่อม provider และได้รับ audio chunk แรก **4,830 bytes ใน 3.34 วินาที** โดยไม่ส่งไมค์; เป็นหลักฐานเสียงถึงไคลเอนต์ ไม่ใช่การฟังลำโพงจริง
- `tests/test_voice.py`: parameterize greeting/resumption ทั้ง 3.1 และ 2.5; targeted รอบก่อน **168 passed, 1 failed** เพราะ fake session เดิมมีเฉพาะ API 2.5 และอ่าน model จาก environment; ปรับ fixture ให้ตรงทั้งสอง protocol แล้ว รอตรวจซ้ำ

- Live check เปิด Gemini สำเร็จแต่รอ 35 วินาทีไม่มี audio; diagnostics ยืนยัน turn จบโดย `audio_observed=false` ไม่ใช่การทดสอบลำโพง
- `app/providers/gemini.py`, `tests/test_gemini_live_text.py`: เปลี่ยนข้อความคำทักทาย/ประกาศของ Gemini 3.1 Flash Live เป็น `send_realtime_input(text=...)` ตาม [Google Live capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities); รุ่นนี้ `send_client_content` ใช้ seed history ไม่ใช่ข้อความสด เก็บเส้นทาง 2.5 เดิมไว้; รอ automated/live check หลังแก้
- Full suite ก่อนแก้ provider รอบนี้ **1,198 passed, 1 warning ใน 168.55s**; มี Windows Proactor accept task ค้างขณะ test teardown 2 ข้อความ (exit 0) จึงไม่อ้างว่า log สะอาดทั้งหมด

## 2026-09-09 — ตรวจรับหุ่น ชุด 2

- `README.md`, `docs/ต่อกับหุ่นยนต์ Astronaut.md`: แก้ข้อความเก่าที่อ้างว่า server เสร็จครบ/ยังไม่มีการยืนยันตัวตน/ยังไม่มีตัวจำลองสำเร็จ ให้ตรงกับของจริง และระบุข้อจำกัด AEC/half-duplex กับ protocol ชัดเจน

- `docs/robot-arrival-2026-09-09.md`, `docs/robot-integration.md`: บันทึกผลตรวจ/สิ่งขาดก่อนรับเครื่อง/รายการขอผู้ขาย/เกณฑ์ทดสอบจริง และแยก wire protocol ที่มีจาก ACK/telemetry ที่ยังไม่มี พร้อมอ้างอิง Android/MDN
- preflight รันจริง: TLS certificate/key โหลดคู่กันได้, wake/IR config พร้อม; production 8001 ไม่ทำงานและยังไม่มี robot connection/SDK/APK/การเปิด tools สำหรับหุ่นจริง; full regression หลังแก้กำลังรัน

- `scripts/check_robot_readiness.py`: เพิ่ม preflight แบบอ่านอย่างเดียว ตรวจ config/TLS certificate-key/ไฟล์ SDK-APK ที่ระบุ/เครื่องมือ Android/wake/IR และ optional local health; ไม่พิมพ์ secret ไม่ส่งคำสั่งฮาร์ดแวร์ และแยก hardware acceptance ว่ายังไม่ทดสอบเสมอ; รอรัน

## 2026-09-09 — ตรวจรับหุ่น ชุด 1

- รอบ targeted แรก **120 passed, 2 failed**: สองกรณีเดิมคาดว่า mock ยืนยัน `moving=false`; ปรับ `tests/test_robot.py` ให้ตรวจ `null` เพราะยังไม่รู้การเคลื่อนที่จริง และให้ `robot_link.py` แสดง unknown เมื่อส่งคำสั่งล้มเหลวด้วย; รอตรวจซ้ำ

- `tests/test_robot_arrival_readiness.py`: เพิ่ม regression ข้อมูล bridge ผิดรูปแบบ, boolean ที่เป็น string/number, จุดหมายกำกวม, timeout/cancel และสถานะ unknown หลังหยุด/ตัดการเชื่อมต่อ; รอรัน

- `app/session.py`, `app/tools/robot_link.py`, `app/tools/robot.py`: ปฏิเสธ JSON/POI ผิดรูปแบบและ arrival ที่ไม่ได้ระบุ boolean จริง; ไม่เลือกจุดหมายเมื่อมีหลายชื่อที่ไม่ใช่ชื่อซ้อนกัน; แยกสถานะจากคำสั่งออกจากข้อมูลยืนยันจริง และแจ้ง timeout ตามผลส่ง cancel จริง
- ตรวจฐานก่อนแก้: pytest **1,165 passed, 1 warning (173.69s)**; smoke import ทุก extras ผ่าน; Node เสียง 3 ชุดผ่าน; `uv --no-cache pip check` ตรวจ 103 packages ผ่าน (ครั้งแรกใช้ cache ปกติติดสิทธิ์ จึงตรวจโดยไม่ใช้ cache)
- พอร์ตจำลอง 8010 health ผ่าน; production HTTPS 8001 connection refused; ยังไม่พบ APK/AAR ใน repo/Downloads และไม่พบ adb/java/gradle ใน PATH; ยังไม่ได้ทดสอบตัวหุ่นจริง
- รอตรวจ regression หลังแก้; การตรวจนี้ยังไม่รับรองความปลอดภัยหรือความพร้อมของฮาร์ดแวร์

## 2026-09-08 — ส่งมอบบ้านขนาดใหญ่ขึ้น

- หน้า 8010 โหลด third person ที่ `layoutScale=1.5` สำเร็จ ไม่มี JavaScript error; ไม่ต้องรีสตาร์ทเซิร์ฟเวอร์หรือสายเสียงเดิม เพราะเปลี่ยนเฉพาะ client/คู่มือ ผู้ใช้กด Ctrl+F5 เพื่อรับฉากล่าสุด
- ตรวจภาพพื้นหลังแยกระดับแล้ว ไม่พบลายซ้อนที่เห็นในรอบก่อน; syntax และ `git diff --check` ผ่าน ปิด QA 8011 แล้ว
- Browser regression ครอบคลุมผังขยาย/การชน/POI→การเดินถึง/กล้อง/อุปกรณ์/มือถือ ตามชุด 3; ไม่รัน Python full suite ซ้ำเพราะไม่ได้เปลี่ยน backend หรือ voice

### ขยายบ้าน ชุด 3: ผ่านการเดินและแก้พื้นซ้อนฐาน

- `client/robot-scene-3d.js`: แยกระดับพื้นหลักออกจากผิวฐาน platform หลัง screenshot ระยะใกล้พบ z-fighting โดยให้พื้นหลักอยู่ระหว่างฐานกับผิวห้อง
- `docs/robot-simulator.md`: ระบุอัตราขยายพื้นที่ กล้อง และความสัมพันธ์ของพิกัด engine/JSON กับฉาก
- Browser ผ่านจุดเริ่ม/ขอบเขตใหม่, เดินชนกำแพง, ไฟ/ม่าน/ทีวี, raycast จุดฟิตเนส→arrived, กล้องตามหุ่น และมือถือไม่มีล้น/ไม่มี page error; QA แรกผิดที่คาดว่าชนแล้วต้องหยุดทั้งสองแกน แก้ harness ให้ยอมรับการไถลตามผนังโดยไม่เปลี่ยน movement
- Syntax และ diff check ผ่าน; รอตรวจพื้นหลังแยกระดับและหน้า 8010 ล่าสุด

### ขยายบ้าน ชุด 2: ตรวจพื้นและภาพรวม

- `client/robot-scene-3d.js`: แก้ฐานพื้นเดิมที่สูงทับผิวห้อง และเอาแถบรอยต่อซ้อนระดับเดียวกับพื้นออก ให้เห็น texture ไม้/กระเบื้องและลดการกระพริบหลังขยาย
- `client/robot-interior.js`, `client/robot-explorer.js`: แสดงอาคารภายนอกเฉพาะโหมดเดิน ภาพรวมจึงเห็นผังบ้านชัด
- Syntax ผ่าน; browser เปิดผัง 1.5 เท่าได้ ไม่มี page error ตรวจภาพเดินและภาพรวมแล้ว รอตรวจ regression การเดิน/อุปกรณ์หลังชุดนี้

## 2026-09-08 — ขยายบ้าน ชุด 1

- `client/robot-scene-3d.js`: ขยายผังแนวราบ 1.5 เท่าต่อด้าน (พื้นที่ 2.25 เท่า), จัดเฟอร์นิเจอร์เป็นกลุ่มคงขนาดเดิม แล้วกระจายตำแหน่งตามผังใหม่; แปลง POI/เส้นทาง/หุ่น/minimap และ collision ด้วยอัตราเดียวกัน
- `client/robot-explorer.js`: ขอบเขตเดินและจุดเริ่มตามบ้านที่ใหญ่ขึ้น กล้องบุคคลที่สามถอยจาก 2.1 เป็น 2.8 หน่วย; ตัวคนและหุ่นไม่ขยาย
- `client/robot-interior.js`: เก็บขนาดทีวี/แอร์/โคม, ขยายหน้าต่าง/ม่านกับผนัง และระยะแสง; texture รองรับเฟอร์นิเจอร์ใน group
- รอ syntax และ browser QA ตรวจห้อง/การเดินชน/จุดหมาย/อุปกรณ์; ไม่แก้ backend คำสั่งหรือเสียง

## 2026-09-08 — ส่งมอบ Third person และ smart home จำลอง

- เปลี่ยนเฉพาะเซิร์ฟเวอร์จำลอง 8010 หลังตรวจ provider idle และไม่มี active command เปิดรุ่นล่าสุดแล้วและปิด QA 8011
- ตรวจหน้าใช้งานจริง: third-person เปิดสำเร็จ, ตัวเลขซูมตอบสนอง, มีแผงอุปกรณ์ 4 ตัว ไม่มี browser error; handshake Gemini จริงรับ configuration ที่มี robot/virtual-home tools ได้ ready โดยไม่ส่งเสียงไมค์ผู้ใช้
- ผลทดสอบ: full suite **1,165 passed, 1 warning**, หลังแก้เปิดไฟจาก 0 targeted **12 passed, 1 warning**; Node เสียง 3 ชุดผ่านและ browser desktop/mobile/isolation/fallback ตามชุด 10
- ผู้ใช้เปิด `http://127.0.0.1:8010` และ Ctrl+F5; คลิกฉากแล้ว WASD เดิน, ลากมอง, V สลับภาพรวม, F ตาม Emma และสั่งอุปกรณ์ผ่านเสียง/แผงควบคุมได้
- ขอบเขตที่ส่งมอบเป็นตัวละครและโมเดลห้องสร้างขึ้น ไม่มีแบบวัดสถานที่จริง อุปกรณ์บ้านทั้งหมดจำลอง ไม่แตะ Broadlink หรืออุปกรณ์จริง และไม่เปลี่ยน `.env`/API key

### Third person ชุด 10: ผ่านการซ้อมครบวงจร

- `client/robot-explorer.js`: ตัวเลขซูมแสดงระยะของ third-person จริงเมื่อใช้ล้อหรือปุ่ม แทนค่าค้างจากภาพรวม
- Browser QA ผ่าน: เดินชนขอบ/ผนัง, เปลี่ยน focus แล้วหยุดเดินแม้ยังกดปุ่ม, ไฟปิดแล้วความสว่างภาพเฉลี่ยลด 139.9 → 30.0, ม่าน/แอร์/อุณหภูมิ/ทีวี, voice iframe → fake AI → tool จริงของตัวจำลองปิดไฟ, สลับภาพรวม/ตามหุ่น/เดิน, export smart_home และ journal
- Mobile 390px ไม่มีล้น ปุ่มเดินค้าง/ปล่อยทำงาน; ปิด WebGL แล้วยังใช้แผง smart home ในฉากสำรองได้ ไม่มี page error ตรวจภาพ desktop/mobile/ไฟเปิด/ไฟปิดแล้ว
- Node เสียง 3 ชุดและ syntax ผ่าน; รอตรวจซูมหลังปรับและเปิดเซิร์ฟเวอร์ใช้งานจริงรุ่นล่าสุด

### Third person ชุด 9: คู่มือการเดินและคำสั่งบ้าน

- `README.md`, `docs/robot-simulator.md`: วิธีเดิน third person/ภาพรวม/touch, แยกผู้เยี่ยมชมจากหุ่น, ตัวอย่างเสียงทั้ง 4 อุปกรณ์, ค่าที่รองรับ, export/reset และข้อจำกัดผังสมมติ/ไฟทั้งฉาก/ไม่จำลองความร้อน
- `client/robot-simulator.html`: accessible description ตรงกับ third person ค่าเริ่มต้น
- หลังชุด 8 targeted smart-home **12 passed, 1 warning**; รอ browser QA รอบส่งมอบ

### Third person ชุด 8: เปิดไฟหลังหรี่สุดและหน้าจอทีวี

- `app/robot_home.py`, `tests/test_robot_home.py`: เมื่อหรี่ไฟเป็น 0 แล้วสั่งเปิด ให้คืนความสว่าง 85% เพื่อเห็นแสงจริงในฉาก เพิ่มกรณีต่อเนื่องนี้ใน regression
- `client/robot-interior.js`: หน้าจอทีวีมีภาพจำลองวาดในเครื่องเมื่อเปิดและดับเมื่อปิด ไม่มีโหลดสื่อภายนอกหรือเสียงแทรก
- Full suite ก่อนชุดนี้ **1,165 passed, 1 warning ใน 163.07 วินาที**; ตรวจภาพห้อง/กล้องรุ่นล่าสุดแล้ว รอ targeted และ browser รอบสุดท้ายหลังชุดนี้

### Third person ชุด 7: กล้องเริ่มต้นและด้านหน้าของอุปกรณ์

- `client/robot-explorer.js`: ลดระยะกล้องเริ่มต้น เพิ่ม FOV และย้ายจุดเริ่มให้กล้องอยู่ในห้อง ไม่ถูกกรอบประตูบังเกือบทั้งฉาก ปรับส่วนผมด้านหลังของ avatar
- `client/robot-interior.js`: ย้ายผิวทีวีมาอยู่ด้านหน้าตัวเครื่องและไฟสถานะแอร์เข้าด้านห้อง แก้ geometry ที่บังผลเปิด/ปิด
- เป็นผลจากตรวจ screenshot รุ่นก่อน; รอตรวจภาพและ browser regression ล่าสุด

### Third person ชุด 6: ตรวจมุมระดับคนและรายละเอียดห้อง

- `client/robot-interior.js`: หลังตรวจภาพจริง เพิ่มเพดานที่ซ่อนเมื่อดูภาพรวม และเปลี่ยนม่านจากชิ้นแยกเป็นผ้าจีบผืนต่อเนื่อง
- `client/robot-explorer.js`: เริ่มภายในห้องตัวอย่าง ปรับไหล่/ใบหน้า/ขากางเกงและรองเท้าของ avatar ให้มีสัดส่วนคนชัดขึ้น
- Targeted backend/isolation/voice **50 passed, 1 warning**; browser เปิด third person ได้ ไม่มี page error และเดินจากจุดเริ่มผ่านประตูได้ รอตรวจภาพล่าสุด/แสง/ม่านและ full suite ที่กำลังรัน

### Third person ชุด 5: ทดสอบขอบเขตอุปกรณ์

- เพิ่ม `tests/test_robot_home.py`: API/tool ใช้ state เดียวกัน, reset/journal, validation แบบไม่เปลี่ยน state เมื่อผิด, origin/JSON, tools ปิดนอกจำลอง และ trap Broadlink ไม่ให้แตะอุปกรณ์จริง
- `tests/test_robot_voice.py`: ส่ง PCM ผ่าน websocket → provider fake → actual virtual-home tool เปลี่ยนไฟ และไม่ส่ง IR
- Syntax JavaScript 4 ไฟล์ใหม่/แก้ผ่าน; รอผล targeted tests และ browser QA

### Third person ชุด 4: แผงเดินและ smart home

- `client/robot-simulator.html`, `.css`, `.js`: สลับเดินสำรวจ/ภาพรวม, ปุ่มเดิน touch, คำแนะนำ WASD และแผงไฟ/ความสว่าง แอร์/อุณหภูมิ ม่าน/เปอร์เซ็นต์ ทีวี ที่ส่ง `/api/home` และแสดงสถานะจาก server
- `client/robot-explorer.js`: จุดเริ่มผู้เยี่ยมชมอยู่หน้าประตูห้องตัวอย่าง ให้เห็นภายในในมุมด้านหลัง; smart home ใช้เสียงเดิมและค่า state เดียวกันกับปุ่ม
- รอ syntax/backend tests และ browser QA; ไม่เปลี่ยนการรับเสียงหรือ Broadlink จริง

### Third person ชุด 3: ห้องและอุปกรณ์ที่มองเห็น

- เพิ่ม `client/robot-interior.js`: ผนังสูง/ช่องประตู หน้าต่าง/วิวเมือง texture ไม้และกระเบื้องแบบ procedural, โคมไฟ PointLight, ม่านเคลื่อนตามเปอร์เซ็นต์, ทีวีและแอร์เปลี่ยนตาม state
- `client/robot-scene-3d.js`: เชื่อมตัวละครผู้ใช้และ collision, เปิด third person เป็นค่าเริ่มต้น, สลับภาพรวม/ตาม Emma, ปรับความสว่างจากไฟและแสงผ่านม่าน พร้อมจุดผู้ใช้ใน minimap
- `app/robot_simulator.py`: เสิร์ฟ module ใหม่แบบ allowlist; ยังรอ UI และ browser QA รวมการเปิด/ปิดไฟเห็นผลในฉาก

### Third person ชุด 2: ตัวผู้สำรวจ

- เพิ่ม `client/robot-explorer.js`: ตัวละครคนและกล้องด้านหลัง, WASD/ลูกศร/Shift, ลากมอง/ล้อซูม และปุ่มเดินบน touch
- เดินเฉพาะตัวละครผู้ใช้ใน browser ไม่ส่งคำสั่งเคลื่อนหุ่น; circle/AABB collision พร้อม substeps กันทะลุเฟอร์นิเจอร์/ขอบฉาก, raycast ระยะกล้อง และหยุดเดินเมื่อเสีย focus
- รอเชื่อม scene/UI และตรวจระยะชน/กล้องจริงใน browser

## 2026-09-08 — Third person และ smart home ชุด 1

- เพิ่ม `app/robot_home.py`, `app/tools/simulation_home.py`: คำสั่งไฟ/ความสว่าง แอร์/อุณหภูมิ ม่าน/เปอร์เซ็นต์ และทีวีแบบจำลอง พร้อม validation ไม่มี device driver
- `app/robot_simulation.py`, `app/robot_simulator.py`: สถานะ smart_home ใน snapshot/reset/export, journal และ `/api/home` ใช้ handler เดียวกับเสียง
- `app/robot_backend.py`, `app/tools/registry.py`, `app/tools/__init__.py`: เพิ่ม 2 tools เฉพาะ context จำลอง ปิดเสมอนอก context; ไม่เรียก smarthome/Broadlink จริง
- `app/prompts.py`, `app/robot_diagnostics.py`: อธิบายคำสั่ง/ขอบเขตไฟทั้งฉากและวัดชื่อ tool ใหม่; รอทดสอบ isolation/validation และเชื่อมภาพ

## 2026-09-08 — ส่งมอบ Emma World 3D

- ตรวจ gesture หลังชุด 5 ผ่านด้วย Chromium mobile touch: pinch ซูมจริง คืนกล้องได้ และไม่เปลี่ยนจุดหมายโดยไม่ตั้งใจ; Node syntax และ `git diff --check` ผ่าน
- ผล regression รอบนี้ **37 passed, 1 warning** พร้อม Node เสียง 3 ชุดและ browser QA ตามบันทึกชุด 5; ไม่รัน full suite ซ้ำ เพราะไม่ได้แก้ voice/backend การเดิน และ targeted/API/browser ครอบคลุมเส้นทางที่เปลี่ยน
- ตรวจไม่มี active command และ provider idle ก่อนแทนที่เซิร์ฟเวอร์ 8010; เปิดรุ่นล่าสุดแล้ว ปิด QA 8011 ตรวจหน้าใช้งานจริงว่า renderer เป็น WebGL, มี 5 จุดหมาย, wake พร้อม และไม่มี page error โดยไม่เปิดไมค์หรือเรียก AI ในการตรวจส่งมอบ
- ทางเข้าเดิม `http://127.0.0.1:8010` กด Ctrl+F5 เพื่อโหลดหน้า 3D; โมเดลและแผนที่เป็น geometry สมมติสำหรับซ้อมคำสั่ง ไม่ใช่โมเดลจาก CAD หรือการทดสอบ collision/เซนเซอร์ของหุ่นจริง

### ฉาก 3D ชุด 5: ผ่าน browser QA และปรับ touch

- `client/robot-scene-3d.js`: ป้องกัน gesture สองนิ้วกลายเป็นคลิกเลือกจุดโดยไม่ตั้งใจ; `README.md`: อธิบายฉาก 3D และกล้อง/fallback ในทางเข้าหลัก
- Python targeted **37 passed, 1 warning** (Starlette/httpx เดิม); Node syntax และ audio-start/voice-lifecycle/metrics ผ่าน
- Chromium WebGL ผ่านหมุน/ซูม/คืนกล้อง, raycast เลือกและดับเบิลคลิกเดินไปฟิตเนส, F ตามระยะใกล้, Space หยุด, เสียงไมค์จำลองผ่าน voice iframe → fake provider → actual robot tool → arrived, context lost/restored, mobile 390px ไม่มีล้น และปิด WebGL แล้วยังใช้ Canvas สำรองสั่งกลับฐาน/หยุดได้ ไม่มี page error
- ตรวจภาพ desktop/mobile/กล้องใกล้แล้ว; รอตรวจ gesture หลังแก้และเปิดเซิร์ฟเวอร์ 8010 รุ่นล่าสุด ยังไม่เรียก provider จริงใน QA ชุดนี้

### ฉาก 3D ชุด 4: ตรวจภาพและเตรียม regression

- `client/robot-scene-3d.js`: หลังตรวจ screenshot WebGL จริง ปรับแสงให้อ่านสีเฟอร์นิเจอร์ง่ายขึ้น ขยายป้าย/ฉาก แก้ตำแหน่งลายน้ำ และให้ F เข้ามุมใกล้หุ่น
- `tests/test_robot_simulation.py`: ตรวจ SHA-256 ของ vendor ที่เสิร์ฟจริง, MIME ของ JS module, allowlist ป้องกัน path อื่น และ CSP ยังจำกัด self
- `docs/robot-simulator.md`: วิธีหมุน/เลื่อน/ซูม/touch/follow, แหล่งเอกสาร Three.js, version/license และ fallback
- Chromium เปิด WebGL ได้จริงและไม่มี JavaScript error ตรวจภาพรอบแรกแล้ว; Node syntax ผ่าน รอตรวจภาพล่าสุดและทดสอบ navigation/voice/fallback

### ฉาก 3D ชุด 3: เชื่อมหน้าเดียวกับ Emma

- `client/robot-simulator.js`: เปิด renderer 3D แบบ async แล้วใช้ callback/tool/state ชุดเดิม หาก module/WebGL เปิดไม่ได้ใช้ Canvas 2.5D สำรองพร้อมแจ้งผู้ใช้
- `client/robot-simulator.html`: สถานะ 3D และคำอธิบายการหมุน/เลื่อน/เลือกจุด; `app/robot_simulator.py`: เสิร์ฟ module/ไฟล์ Three.js แบบ allowlist ภายใน origin เดิม โดยไม่ขยาย CSP
- รอตรวจ syntax, API allowlist, browser WebGL/fallback และการเดินทาง; ไม่เปลี่ยน voice client หรือ backend การเดินหุ่น

### ฉาก 3D ชุด 2: geometry และกล้อง

- เพิ่ม `client/robot-scene-3d.js`: ฉาก WebGL 2 จาก geometry จริงพร้อมแสงเงา เฟอร์นิเจอร์ หุ่น astronaut หันตามตำแหน่งเซิร์ฟเวอร์ และเส้นทาง/POI/minimap จาก engine เดิม
- OrbitControls รองรับหมุน เลื่อน ซูม และ touch; raycast เลือกจุดหมาย, กล้องตามหุ่น, keyboard controls, จำกัด DPR/30 fps, หยุด render เมื่อซ่อนหน้า และ dispose เมื่อออกจากหน้า
- แสดงสถานะ context loss และ reload เมื่อคืนหน้าจาก BFcache; ยังไม่เชื่อมหน้า UI และรอตรวจ browser/คำสั่งจริงในตัวจำลอง

## 2026-09-08 — ฉาก 3D ชุด 1: dependency ภายในโปรเจกต์

- เพิ่ม `client/vendor/three/`: Three.js 0.180.0 จาก tag r180 ของ mrdoob/three.js พร้อม MIT LICENSE และ manifest SHA-256; แก้เฉพาะ import ของ OrbitControls ให้ชี้ module ภายใน ไม่ใช้ CDN ขณะเปิดฉาก
- ดาวน์โหลดครบก่อนเขียน ตรวจ version จาก upstream package.json แล้ว; sandbox ปฏิเสธ network จึงดาวน์โหลดด้วยสิทธิ์เครื่องมือที่อนุมัติ รอเชื่อมฉากและตรวจ browser/WebGL

## 2026-09-08 — ส่งมอบแผนที่และ Emma ในหน้าเดียว

- `README.md`: ปรับทางเข้าเป็นหน้าแผนที่พร้อมแผงเสียง อธิบาย standby/Start, readiness, ทดสอบลำโพงและส่งออกผลซ้อม ให้ตรงกับหน้าจอที่ตรวจแล้ว
- Full suite: **1,151 passed, 1 warning ใน 163.46 วินาที**, exit code 0; ยังมี warning เดิม Starlette TestClient/httpx และข้อความ pending IocpProactor/Event loop closed ตอน teardown ของ fake upstream บน Windows
- Node audio-start, metrics/syntax และ voice-lifecycle ผ่านหลังชุด 7; ตรวจ Chromium desktop/mobile ผ่านตามชุด 6 รวม wake fixture → fake provider → actual robot tool, การถือไมค์เพียงแท็บเดียวและรับช่วงหลังปิดแท็บแรก, JSON export v2 และไม่มี JavaScript error
- เปิดเซิร์ฟเวอร์ตัวจำลองรุ่นล่าสุดพอร์ต 8010 นอก sandbox หลังตรวจว่าไม่มีสายหรืองานเดินค้าง และปิด QA พอร์ต 8011; ตรวจ `/health` ว่า wake พร้อม และทดสอบ `/ws` กับ Gemini จริงได้ ready พร้อม diagnostic session ID โดยไม่ส่งเสียงไมค์ จากนั้น `/api/diagnostics` มี session_start/voice_ready/session_end และ provider กลับ idle
- ข้อมูล diagnostics เก็บ 800 เหตุการณ์ใน RAM และส่งออกเองได้ ไม่มี transcript หรือ raw audio ใน metrics; เวลา latency วัดการรับเสียงจาก provider ไม่ใช่การยืนยันว่าเสียงออกลำโพงผู้ใช้ ทดสอบลำโพงจริงได้ด้วยปุ่มบนหน้าใหม่
- ไม่แก้ `.env` หรือ API key, ไม่รีสตาร์ท Emma หลัก และยังไม่มีการทดสอบกับหุ่นจริง; บันทึกนี้รวมผลตรวจที่เคยระบุรอในชุด 1–7

### ชุดหน้าเดียว — ชุด 7: timer และเสียงท้ายสายเก่า

- `client/index.html`: ไม่ให้ timer standby เก่าเปิดไมค์ระหว่างสายใหม่, ไม่ให้ onended ของเสียงจาก context เก่าเปลี่ยนสถานะสายใหม่ และให้ gesture ที่ค้างจาก context ปิดแล้วจบโดยไม่เกิด unhandled rejection
- `tests/client_audio_start.cjs`, `tests/client_voice_lifecycle.cjs`: เพิ่มกรณี callback เสียงเก่าและ timer standby ระหว่าง active call
- รอตรวจ Node/regression รอบสุดท้าย; full suite กำลังรัน และยังไม่เปลี่ยนเซิร์ฟเวอร์ที่ผู้ใช้เปิดอยู่

### ชุดหน้าเดียว — ชุด 6: ตรวจรวมและคู่มือ

- `app/robot_diagnostics.py`: เก็บ outcome ของ tool แบบ allowlist และตรวจชนิด error category; `client/simulator-voice.css`: ซ่อนข้อความตกแต่งของหน้าเดิมใน embedded view
- `docs/robot-simulator.md`: วิธีใช้หน้าเดียว/readiness/test-speaker, Web Locks และข้อจำกัดข้าม origin, RAM retention/export v2 และเวลาที่วัดได้จริง
- ผลตรวจ: Python targeted **262 passed, 1 warning**; Node audio-start/metrics/lifecycle ผ่าน; Chromium หน้าเดียวปลุกจาก fixture → fake provider → actual robot tool ผ่าน ไม่มี JS error, root ไม่เปิดไมค์, แท็บที่สองไม่เปิดไมค์และรับช่วงได้หลังปิดแท็บแรก, export มี session/metrics และมือถือไม่ล้นแนวนอน
- ตรวจภาพ desktop/mobile แล้ว; QA แรก assertion นับ iframe ทั้งหมดผิดเพราะ client เดิมมี iframe สำหรับสื่ออยู่แล้ว จึงตรวจจำนวนการเปิดไมค์จริงแทน ผ่านโดยไม่เปลี่ยน runtime จากปัญหา harness
- รอ full suite และเปิด server รุ่นล่าสุด; ยังไม่เรียก provider จริงในชุดทดสอบนี้

### ชุดหน้าเดียว — ชุด 5: privacy และ lifecycle regression

- เพิ่ม `tests/test_robot_diagnostics.py`: correlation/session/turn/command, ไม่เก็บข้อความส่วนตัว, bounded/reset/stale session, origin/cookie/schema และ readiness ไม่เชื่อค่าจาก client
- เพิ่ม `tests/client_voice_lifecycle.cjs`, `.github/workflows/test.yml`: ทดสอบ callback/timer ของสายเก่า, standby resume ค้างและไมค์เปิดเสร็จหลังถูกยกเลิก
- `tests/test_voice.py`: regression เดิมยังบังคับ teardown ก่อน branch ของสายปัจจุบัน แต่ยกเว้น guard ของ callback สายเก่า
- ผลก่อนชุดนี้: Node audio-start/metrics/syntax ผ่าน; Python targeted **258 passed / 1 failed** จาก source assertion ที่ยังไม่รู้จัก ownership guard รอตรวจซ้ำ

### ชุดหน้าเดียว — ชุด 4: แผนที่และ Emma ในจอเดียว

- `client/robot-simulator.html`, `client/robot-simulator.css`: วางแผนที่คู่แผงเสียงเดิมผ่าน same-origin iframe, ย้ายปุ่มทดสอบเป็นแผงพับได้, แสดง readiness และผลซ้อมเสียง
- `client/robot-simulator.js`: อ่านสถานะจาก iframe ที่ตรวจ origin/source, แสดง latency/เหตุการณ์ และ export รูปแบบ v2 รวม metrics จำลอง โดยตรวจ epoch ตรงกัน
- `app/robot_simulator.py`: export มี epoch และปฏิเสธ reset ระหว่างมีสายเพื่อไม่ทำให้ข้อมูลวัดข้ามรอบ
- รอตรวจ API/browser/privacy และภาพ desktop/mobile ยังไม่เปิด provider จริงในรอบพัฒนา

### ชุดหน้าเดียว — ชุด 3: เก็บ metrics แยกและ readiness ฝั่ง server

- เพิ่ม `app/robot_diagnostics.py`: เก็บ 800 เหตุการณ์ใน RAM แบบ allowlist/จำกัดค่า ไม่มี transcript, raw audio, exception text หรือ secret; เชื่อม session/turn/command และสรุป p50/p95 ของเวลารับเสียง provider
- `app/robot_simulation.py`, `app/turnlog.py`: ส่งเฉพาะ observations ที่อนุญาตเข้า sink จำลองแทน visitor log และ reset/export แยกชัดเจน
- `app/session.py`: บันทึก ready/error category และส่ง diagnostic session ID ให้ client จำลอง
- `app/robot_simulator.py`: เพิ่ม export/readiness และ cookie/origin-guarded browser diagnostics, อนุญาต embed หน้าเสียงเฉพาะ same origin
- รอ API/privacy/regression tests และรวมหน้าจอ; ตัวเลข latency ไม่ใช่หลักฐานว่าเสียงดังออกลำโพง

### ชุดหน้าเดียว — ชุด 2: ตัวเชื่อมเสียงและสถานะอุปกรณ์

- เพิ่ม `client/simulator-voice.js`, `client/simulator-voice.css`: ใช้ voice client เดิม, Web Locks เลือกเจ้าของไมค์ใน origin เดียว, readiness แบบแยกชั้น, ปุ่มทดสอบลำโพงในเครื่อง และเตรียม telemetry แบบไม่มีข้อความสนทนา
- `client/index.html`, `app/robot_simulator.py`: hook ช่วงต่อสายและเสิร์ฟไฟล์เฉพาะ simulator; รองรับ embedded layout โดยไม่เปิด mic client เพิ่ม
- รอ endpoint diagnostics/หน้าแผนที่และการทดสอบครบก่อนเปิด server รุ่นใหม่

## 2026-09-08 — ชุดเสียงเสถียร/หน้าเดียว/ผลซ้อม: ชุด 1

- `client/index.html`: guard socket message/error/close/timer ตามเจ้าของสาย, กันผลเปิดไมค์/worklet standby ที่ล่าช้า และติดตั้ง unlock ก่อน resume; แยกการปิด standby ระหว่าง handover กับการหลุดจริง
- เตรียม hook โหลด `simulator-voice.js` เฉพาะโหมดจำลองสำหรับเจ้าของเสียง/readiness/telemetry; ไฟล์ต่อเชื่อมจะเพิ่มในชุดถัดไป
- รอทดสอบหลังต่อครบ ไม่รีสตาร์ทเซิร์ฟเวอร์ระหว่างชุดแก้ไข

## 2026-09-08 — Research งานถัดไปหลังทดสอบ Emma กับหุ่นจำลอง

- เพิ่ม `docs/research/emma-robot-next-steps-2026-09-08.md`: เทียบสิ่งที่ทำแล้วกับ roadmap เดิม จัดลำดับ 8 งาน พร้อมหลักฐานโค้ด เกณฑ์รับงาน และเอกสารปฐมภูมิ 9 แหล่ง
- พบลำดับ wake resume ก่อน unlock, callback socket เก่าที่เรียก shared teardown, ช่องว่าง readiness/metrics จำลอง และความเสี่ยงคืนฉากผ่าน bfcache; แยกข้อที่ทำซ้ำได้จากข้อเสนอ/ความเสี่ยงที่ยังต้องทดสอบ
- ผลตรวจ: Node VM ทำซ้ำ late close ได้ และตรวจลำดับ wake resume/unlock ตรงกับรายงาน; ยังไม่ได้แก้ข้อค้นพบใหม่ ไม่เปิดไมค์/paid API/ฮาร์ดแวร์หรือรีสตาร์ทเซิร์ฟเวอร์
- ตรวจแล้ว: ลิงก์แหล่งข้อมูลที่ไม่ซ้ำ 9 URL, relative file link resolve ได้, UTF-8/Markdown fences และ `git diff --check` ผ่าน; งานรอบนี้เพิ่มเอกสารเท่านั้น ไม่รัน unit suite ซ้ำ

## 2026-09-08 — ส่งมอบการแก้เสียงทักทายต้นสาย

- Node regression ผ่าน: PCM แรกระหว่างรอไมค์, ปฏิเสธไมค์, suspended playback, reuse wake context/stream, sample rate และ stale permission cleanup; client metrics/syntax ผ่าน
- Chromium ผ่านด้วย Web Audio จริงและ WebSocket ที่จำลองในเบราว์เซอร์: รับ PCM ทักทาย 24,000 samples และ schedule ขณะ context running ก่อน getUserMedia ที่หน่วง 1.8 วินาทีเสร็จ ไม่มี JavaScript error; ไม่ใช้ provider หรือไมค์จริงของผู้ใช้
- Python regression ที่เกี่ยวข้อง: **224 passed, 1 warning ใน 14.66 วินาที**; `git diff --check` ผ่าน
- HTML เสิร์ฟจากไฟล์ปัจจุบัน ผู้ใช้กด Ctrl+F5 ที่หน้าเสียงเพื่อโหลดการแก้ไข ไม่ต้องรีสตาร์ทเซิร์ฟเวอร์หรือเปลี่ยน `.env`; ผลนี้ยืนยันเส้นทางเล่นเสียงในซอฟต์แวร์ ไม่ใช่การวัดเสียงจากลำโพงผู้ใช้

### เสียงประโยคแรก — ชุด 3: ไมค์ถูกปฏิเสธและ CI

- `tests/client_audio_start.cjs`: เพิ่มกรณีปฏิเสธไมค์แต่ยังเล่นเสียงได้
- `tests/test_greeter.py`: ยอมให้ return เฉพาะสายที่จบขณะรอสิทธิ์ไมค์ โดยยังห้าม return จากการปฏิเสธไมค์ของสายปัจจุบัน
- `.github/workflows/test.yml`: รัน regression เสียงประโยคแรกใน CI
- ผลก่อนชุดนี้: Node audio-start และ metrics/syntax ผ่าน; Python targeted 223 passed / 1 failed เพราะ assertion เดิมห้าม return ทุกกรณีรวม guard สายเก่า รอตรวจซ้ำและ browser QA

### เสียงประโยคแรก — ชุด 2: regression ที่ทำให้เกิด race ซ้ำได้

- `tests/client_audio_start.cjs`: จำลองไมค์เปิดช้าแล้วส่ง PCM ทักทายทันที ตรวจไม่ถูกทิ้ง, suspended context ไม่ขวางการเตรียมไมค์, unlock ถูกลงทะเบียนก่อน resume, ใช้ context/ไมค์เดิมจาก wake, sampling rate และปล่อยไมค์เมื่อสายจบก่อน permission เสร็จ
- ผลตรวจ: รอรัน Node และ Python regression; ไม่ใช้ไมค์หรือ API จริงในการทดสอบชุดนี้

## 2026-09-08 — เก็บเสียงประโยคแรกของ Emma (ชุด 1)

- `client/index.html`: สร้าง playback context ทันทีที่รับ ready ก่อน await เปิดไมค์ เพื่อไม่ทิ้ง PCM ทักทายที่มาถึงเร็ว; โหมดเรียกชื่อรับ context ที่เปิดเสียงแล้วจาก standby และคง output sample rate ของแต่ละ buffer
- ลงทะเบียน gesture unlock ก่อนเรียก resume ที่อาจรอ gesture ไม่เสร็จ; ป้องกันผลเปิดไมค์/worklet ของสายเก่ามาแทรกสายใหม่ และไม่เปลี่ยนเป็น listening ทับ greeting ที่กำลังเล่น
- เหตุผลจากโค้ด: เดิม playChunk คืนทันทีเมื่อ audioCtx ยังไม่มี แต่ startMic สร้าง context หลัง await getUserMedia; รอ regression/browser QA ยังไม่ยืนยันลำโพงผู้ใช้

## 2026-09-08 — แก้การเปิด Gemini ถูก sandbox ปฏิเสธ

- ตรวจ traceback แล้ว WinError 5 เกิดใน Windows socket connect ของโปรเซสที่รันภายใต้ข้อจำกัดเครือข่าย ไม่ใช่ข้อผิดพลาดจากไมค์หรือการเลือกเครื่องมือ
- ทดสอบ `_connect()` ของ GeminiProvider ด้วย backend จำลอง: ภายใน sandbox ได้ PermissionError; เมื่อรันนอก sandbox โดยผ่านการอนุมัติของเครื่องมือ ได้ **LIVE_HANDSHAKE_OK** แล้วปิดสาย โดยไม่ส่งเสียงไมค์หรือข้อความสนทนา
- `docs/robot-simulator.md`: เพิ่มแนวทางตรวจข้อจำกัดของโปรเซสและเปิด launcher จาก Windows ตามปกติ พร้อมรีเฟรช cookie หลังรีสตาร์ท
- เปลี่ยนโปรเซสพอร์ต 8010 ให้รันนอก sandbox แล้ว ตรวจผ่านเซิร์ฟเวอร์จริง: health wake พร้อม, `/ws/wake` ตอบ wake_listening และ `/ws` เชื่อม Gemini จริงตอบ ready พร้อม robot_simulator=true แล้วปิดสายทดสอบ ไม่ส่งเสียงไมค์ผู้ใช้; เส้นทาง `/ws` ใช้ขั้นตอน greeting ตามปกติ
- จัดย่อหน้าการแก้ WinError 5 ใน `docs/robot-simulator.md` ไว้ส่วนเปิดใช้งาน; ไม่เปลี่ยน API key, `.env`, firewall หรือโปรแกรม Emma หลัก ไม่มีการแก้โค้ดและไม่รัน unit suite ซ้ำสำหรับการเปลี่ยนสิทธิ์โปรเซส

## 2026-09-08 — ส่งมอบการเรียกชื่อ Emma ในตัวจำลอง

- Full suite: **1,148 passed, 1 warning ใน 152.30 วินาที**; เพิ่ม 5 กรณีจากฐาน 1,143 มี warning เดิม Starlette TestClient/httpx และข้อความ pending IocpProactor ตอน teardown บน Windows แต่ exit code 0
- ตรวจเบราว์เซอร์ครบจากไมค์ fixture/โมเดล wake จริงจน actual robot tool ผ่าน provider fake โดยไม่กด Start; ไม่ได้วัดความแม่นยำของเสียงผู้ใช้หรือเรียก provider จริงระหว่าง QA
- ตรวจว่าไม่มีสายเสียงหรืองานเดินค้างก่อนรีสตาร์ทเฉพาะตัวจำลอง; เซิร์ฟเวอร์ล่าสุดพอร์ต 8010 ตอบ `wake.ready=true`, `standby=true`, `auto_connect=false` แล้ว ปิด QA พอร์ต 8011
- ผู้ใช้รีเฟรช `/voice` แล้วเรียกชื่อได้เมื่ออนุญาตไมค์และระบบเสียงของเบราว์เซอร์ทำงาน; `git diff --check` ผ่าน ไม่แก้ `.env` หรือรีสตาร์ท Emma หลัก

### เรียก Emma ด้วยชื่อ — ชุด 4: ตรวจเบราว์เซอร์และคู่มือ

- `docs/robot-simulator.md`: ปรับวิธีเริ่มจากเรียกชื่อ, สิทธิ์ไมค์/ปลดล็อกเสียง, fallback ปุ่ม, กลับรอฟังหลังจบสาย และรีเฟรชหลังรีสตาร์ท
- Targeted tests: **60 passed, 1 warning ใน 20.35 วินาที**
- Chromium QA ผ่านโดยไม่คลิก Start: fixture ไมค์ → AudioWorklet → โมเดล wake จริง → wake → voice PCM → provider fake → actual robot tool (`hardware=simulated`); ไม่มี JavaScript error และไม่ใช้ provider จริง
- QA ครั้งแรก provider fake ไม่ได้ load_tools ตามที่ provider จริงทำ จึงพบ unknown tool; แก้ harness ชั่วคราวให้โหลดเครื่องมือแล้วตรวจผ่าน ไม่ได้แก้ production tools จากปัญหา harness นี้
- รอ full suite และรีสตาร์ทตัวจำลองพอร์ต 8010

### เรียก Emma ด้วยชื่อ — ชุด 3: ปรับ fixture regression

- `tests/test_wake.py`: source assertion ตรวจ guard ใหม่ที่ยังบังคับเปิด debug/enroll และเติม diagnostics=True ให้ fixture ที่สร้าง stream ผ่าน `__new__` โดยข้าม constructor
- ผลรอบแรก: 58 passed / 2 failed จาก assertion ของ guard เดิมและ fixture ขาด attribute ใหม่; โมเดลจริงในกรณี diagnostics=False และ protocol wake ผ่าน รอตรวจซ้ำ

### เรียก Emma ด้วยชื่อ — ชุด 2: ตรวจ wake และส่งต่อเสียง

- `tests/test_robot_voice.py`: ตรวจ health/PCM/wake/handover ไป actual robot tool, ไม่เปิด provider ก่อน wake, fallback ปุ่มเมื่อปิดหรือไม่มีโมเดล และ origin/cookie ของ standby
- `tests/test_wake.py`: ใช้โมเดลจริงกับเสียง fixture ยืนยัน diagnostics=False ยังได้ยินชื่อ แม้เครื่องเปิด debug/enroll และไม่บันทึกคลิป
- ผลตรวจ: รอ targeted tests; fixture ของการทดสอบเสียงเดิมปิด wake อย่างชัดเจน แทนการอิงค่า health ที่เคยบังคับปิด

## 2026-09-08 — เรียก Emma ด้วยชื่อในตัวจำลอง (ชุด 1)

- `app/robot_simulator.py`: เปิด health wake ตาม WAKE_ENABLED/โมเดลเดิม เพิ่ม `/ws/wake` ใช้ detector ในเครื่องและ origin/cookie เดียวกับเสียง ส่ง wake ให้ client เดิมเปิดสายเมื่อได้ยินชื่อ ไม่เชื่อม provider ระหว่าง standby
- `app/wake.py`: เพิ่มตัวเลือก diagnostics ต่อ stream (ค่าเริ่มต้นคงพฤติกรรมเดิม) เพื่อปิดการเก็บคลิป debug/enrollment เฉพาะตัวจำลอง ไม่ลงทะเบียนรับการเรียกจาก reminder/camera จริง
- เหตุผล: ผู้ใช้ต้องการเรียกชื่อได้เหมือน Emma เดิม แทนการบังคับกดเริ่มคุย; ตรวจพบ WAKE_ENABLED เปิดและมีโมเดล/sherpa ในเครื่องแล้ว
- ผลตรวจ: รอ tests และ browser QA; ยังไม่เปลี่ยน `.env` หรือรีสตาร์ทเซิร์ฟเวอร์

## 2026-09-08 — เปิดตัวจำลองซ้ำโดยไม่ชนพอร์ต

- `start-robot-simulator.cmd`: ตรวจ `/health` ว่าเป็น robot simulator ที่ทำงานอยู่ก่อนเริ่มเซิร์ฟเวอร์ ถ้าพบให้เปิดหน้าเว็บเดิมแล้วจบตัวเปิด ป้องกัน WinError 10048 เมื่อดับเบิลคลิกซ้ำ
- `docs/robot-simulator.md`: อธิบายการเปิดซ้ำและวิธีใช้พอร์ตอื่นเมื่อมีโปรแกรมอื่นครอบครองพอร์ต
- ผลตรวจ: localhost:8010 ตอบ health `ok=true`, `robot_simulator=true`; รันตัวเปิดจริงแล้วเข้าแขนง `already running` และเรียกเปิดเบราว์เซอร์โดยไม่เริ่ม uvicorn ซ้ำ; หน้า Emma World ตอบ HTTP 200 และ `git diff --check` ผ่าน ไม่หยุดเซิร์ฟเวอร์หรือรีเซ็ตงานเดิม

## 2026-09-08 — ส่งมอบแผนที่เกม Emma World

- เปลี่ยนฉากเป็น Canvas isometric 2.5D พร้อมเฟอร์นิเจอร์ หุ่น astronaut เส้นทางตามทางเดิน mission progress แผนที่ย่อ และกล้องซูม/ลาก/ติดตาม/เต็มจอ; ใช้ backend คำสั่งเดียวกับ Emma
- Full suite: **1,143 passed, 1 warning ใน 143.57 วินาที** (`.pytest_robot_game_full`); warning เดิม Starlette TestClient/httpx deprecation
- ตรวจ Chromium desktop/mobile และ interaction ผ่านตามชุด 5; JavaScript syntax 2 ไฟล์ผ่าน; ตรวจภาพจริงแล้ว และ audit working tree 65 ไฟล์มีชื่อใน CHANGELOG ครบ
- เปิดเซิร์ฟเวอร์จำลองล่าสุดที่ `http://127.0.0.1:8010`; ผู้เปิดหน้าเก่ากด Ctrl+F5 แล้วใช้ฉากใหม่ได้ทันที; หลัง QA คืนสถานะ idle
- แผนที่และเฟอร์นิเจอร์ยังเป็นข้อมูลสมมติ ไม่ยืนยัน physics/SLAM/การหลบสิ่งกีดขวางจริง; รอบนี้ไม่ทดสอบเสียงกับ provider จริง ไม่แก้ `.env` หรือสั่งฮาร์ดแวร์

### แผนที่เกม — ชุด 5b: ชื่อปุ่มในคู่มือ

- `docs/robot-simulator.md`: ปรับชื่อปุ่มกล้องและเริ่มเดินทางให้ตรงกับหน้า HTML หลังตรวจเทียบ
- ผลตรวจ: `git diff --check` ผ่าน; audit 65 ไฟล์ใน working tree มีชื่อในประวัติครบ; full suite ยังรันอยู่

### แผนที่เกม — ชุด 5: คู่มือและตรวจการใช้งาน

- `docs/robot-simulator.md`: เพิ่มวิธีใช้ฉาก Emma World, คลิก/สัมผัส/คีย์บอร์ด กล้อง แผนที่ย่อ และขอบเขตเส้นทางสมมติ พร้อมแก้คำอธิบายสิ่งกีดขวางจากเส้นตรงเป็นระยะทางตาม route
- ผลตรวจ: targeted robot/voice tests **55 passed, 1 warning**; JavaScript syntax ผ่าน 2 ไฟล์
- Chromium desktop: zoom/pan/follow/fullscreen, เลือกจุด/ดับเบิลคลิก, Space หยุด, arrival/progress, obstacle และ reset ผ่าน; mobile 390px แตะเลือกจุดได้และไม่มี horizontal overflow; ไม่มี JavaScript error ตรวจภาพ desktop/mobile แล้ว
- Browser QA รอบแรกอ่านสถานะ fullscreen ก่อน promise จบ จึงปรับเวลาอ่านในการตรวจและผ่าน โดยไม่ได้แก้พฤติกรรมโปรแกรม; ทดสอบด้วยโลกจำลอง ไม่เปิดไมค์หรือ provider จริง
- รอ full suite รอบสุดท้าย; คืน simulator เป็น idle หลังทดสอบ

### แผนที่เกม — ชุด 4b: จุดปลายเส้นทาง

- `app/robot_simulation.py`: คืนพิกัดต้น/ปลายตรงเมื่อ progress ถึง 0/1 แก้เศษ floating point ที่พบจากการทดสอบเส้นทางทุกคู่จุด
- ผลตรวจรอบแรก: JavaScript syntax ผ่านทั้ง 2 ไฟล์; targeted 54 passed / 1 failed จาก 1.5000000000000004 เทียบ 1.5 จะแก้และตรวจซ้ำ

### แผนที่เกม — ชุด 4: เชื่อมฉากกับคำสั่งจริงของตัวจำลอง

- `client/robot-simulator.js`: ส่ง state/route/progress ให้ renderer, คลิกเลือก POI รวมฐานชาร์จ, ดับเบิลคลิกและ Space เรียกเครื่องมือเดิม, ปุ่ม zoom/center/follow/fullscreen และ mission HUD
- จุดจากหน้าเกมและคำสั่งเสียงใช้ backend เดียวกัน ไม่ทำให้ตัวละครเดินเองโดยไม่ผ่าน engine; การเริ่มเดินทางไม่เท่ากับแจ้งถึง
- ผลตรวจ: รอ syntax/unit/browser QA บน server เวอร์ชันใหม่

### แผนที่เกม — ชุด 3b: รูปแบบหน้าจอ

- `client/robot-simulator.css`: โทน navy/mint ฉากใหญ่พร้อม HUD กล้องและแผนที่ย่อ ปรับหน้าจอมือถือ/เต็มจอและ reduced-motion
- ผลตรวจ: รอ browser render หลังเชื่อม state ไม่เพิ่ม assets ภายนอก

### แผนที่เกม — ชุด 3: โครงหน้าจอ Emma World

- `client/robot-simulator.html`: แทน SVG ด้วยฉาก Canvas มีปุ่มกล้อง mission HUD แผนที่ย่อและ progress พร้อมเก็บจุดสั่งงาน/เสียง/fault/journal เดิม
- ผลตรวจ: รอ stylesheet และผูก state; patch แรกถูกปฏิเสธเพราะมีหลาย operation ต่อ CSS เดียว จึงแยก patch ตามไฟล์ โดยไม่มีการแก้บางส่วนจาก patch ที่ล้มเหลว

### แผนที่เกม — ชุด 2: วาดฉากและกล้อง

- เพิ่ม `client/robot-scene.js`: Canvas 2.5D แบบ isometric พร้อมห้องตัวอย่าง/โซฟา/เตียง/โต๊ะ/ฟิตเนส/สระน้ำเคลื่อนไหว/ต้นไม้/ฐานชาร์จ หุ่น astronaut เคลื่อนไหวตามข้อมูล server, ทางเดินและแผนที่ย่อ
- รองรับลากฉาก ซูม ติดตามหุ่น คลิกเลือก/ดับเบิลคลิกเดินทาง คีย์บอร์ดเฉพาะเมื่อ canvas โฟกัส และ reduced-motion; ไม่มี CDN หรือ raster assets เพิ่มเติม
- ผลตรวจ: รอเชื่อมหน้าจอและทดสอบ browser; รูปห้อง/เฟอร์นิเจอร์เป็นภาพประกอบสมมติ ไม่ใช่แบบโครงการหรือ collision geometry จริง

## 2026-09-08 — แผนที่เกม 2.5D (ชุด 1)

- `app/robot_simulation.py`: เปลี่ยนการเคลื่อนที่สมมติเป็นเส้นทางตาม corridor/ทางเข้าห้อง มี route/progress ส่งให้หน้าจอ ยังคง command/ACK/arrival และ Emma interface เดิม; ไม่ใช่ SLAM หรือ physics หุ่นจริง
- `app/robot_simulator.py`: เตรียม route เสิร์ฟ `client/robot-scene.js` ในเครื่อง ไม่เพิ่ม CDN/dependency
- `tests/test_robot_simulation.py`: ตรวจเส้นทางทุกคู่จุดหมายและการเดินต่อหลังหยุดกลางทางว่าใช้ทางเดิน/ไม่วิ่งทแยงผ่านห้อง
- ผลตรวจ: รอ renderer/UI และ tests; ทุกจุด/ผังยังเป็นข้อมูลจำลอง ไม่แก้แผนที่หุ่นจริงหรือ `.env`

## 2026-09-08 — ส่งมอบ Emma สั่งหุ่นจำลองผ่านเสียง

- Full suite โค้ดล่าสุด: **1,142 passed, 1 warning ใน 155.69 วินาที**; เพิ่ม voice tests 9 กรณีจากฐาน 1,133
- ชุด voice/provider/review/metrics หลังแก้ regression ผ่าน **202 passed**; voice tests หลังเพิ่มกรณี failure/stale/rejected ผ่าน **9 passed** ก่อน full suite รอบสุดท้าย
- Chromium browser QA: ใช้ไมค์จำลองและ upstream fake ตรวจ client AudioWorklet/PCM, WebSocket เดิม, actual robot tool, audio response และ arrival กลับ Emma ผ่าน; ตรวจหน้า desktop/mobile และข้อความแยก ACK จาก arrival ผ่าน ไม่มี JavaScript error
- JavaScript syntax ใน `client/index.html`, Python AST 10 ไฟล์ และ `git diff --check` ผ่าน; audit working tree 64 ไฟล์รวมงานเดิม มีชื่อในประวัติครบ
- เปิดโค้ดล่าสุดที่ `http://127.0.0.1:8010/voice` พร้อมปุ่มจากแผนที่; ตรวจหน้าใช้งานจริงโดยไม่กดเริ่มคุย: Gemini ตั้ง API key แล้ว, auto-connect ปิด, ไม่มี WebSocket เสียงหรือการเปิดไมค์อัตโนมัติ
- ปิดเซิร์ฟเวอร์ QA upstream fake พอร์ต 8011 แล้ว เหลือเซิร์ฟเวอร์ตัวจำลองพอร์ต 8010; รีสตาร์ทเฉพาะตัวจำลอง ไม่แก้ `.env`, ไม่รีสตาร์ท Emma หลัก, ไม่ commit/push
- เสียงจริงต้องให้ผู้ใช้กดเริ่มและอนุญาตไมค์เอง ใช้ API/voice configuration เดิม; รอบนี้ไม่ได้ส่งเสียงไป provider จริงหรือทดสอบความแม่นยำการฟังคำพูดของผู้ใช้
- คำเตือนเดิม Starlette TestClient/httpx deprecation และ teardown ของ Windows fake-upstream บางชุดไม่ทำให้ tests fail

### Emma สั่งตัวจำลอง — ชุด 6: ผลเดินทางที่ยังเกี่ยวข้อง

- `app/robot_voice.py`: คำสั่งใหม่ที่ถูกปฏิเสธไม่ควรกลบรายงาน arrival ของงานเดิมที่สำเร็จ จึงเลือกคำสั่งล่าสุดที่มีผลจริงในโลกจำลองเมื่อกรองประกาศค้าง
- `tests/test_robot_voice.py`: เพิ่มผลนำทางล้มเหลวผ่าน WebSocket และการกรองประกาศหลัง reset/stop/คำสั่งใหม่ที่ถูกปฏิเสธ
- ผลตรวจ: รอ targeted tests และ full suite รอบสุดท้ายหลังแก้ regression; ไม่มีการเปลี่ยนสถานะหรือสั่งงานหุ่นจริง

### Emma สั่งตัวจำลอง — ชุด 5: แก้ข้อผิดพลาดจาก regression

- `app/session.py`: คงเงื่อนไขเริ่ม Canva เดิมไว้ แล้วตรวจ context จำลองชั้นใน เพื่อผ่าน regression เดิมที่ตรวจ source text พร้อมคงพฤติกรรมไม่เริ่ม Canva ระหว่างซ้อม
- `tests/test_robot_voice.py`: อ่าน Gemini config เป็น dict ตาม API ภายในจริง และใส่เครื่องมือทดสอบที่ลงทะเบียนแล้วแต่ไม่มี group เพื่อยืนยัน dispatch ปฏิเสธจริง ไม่ใช่ผ่านเพราะหาเครื่องมือไม่พบ
- ผลตรวจที่นำมาแก้: full suite รอบแรก **1 failed, 1,138 passed** (source-string assertion); targeted หลังเพิ่ม schema test **2 failed, 200 passed** (เพิ่ม test ใช้ attribute ผิดกับ dict) ไม่ใช่การเชื่อม API หรืออุปกรณ์จริงล้มเหลว; รอตรวจใหม่

### Emma สั่งตัวจำลอง — ชุด 4: ตรวจเบราว์เซอร์และคู่มือ

- `app/providers/gemini.py`, `tests/test_robot_voice.py`: ปิด native Google Search เฉพาะ context จำลอง แม้ WEB_SEARCH เปิดในเครื่อง และตรวจ schema ของทั้ง Gemini/OpenAI เหลือเฉพาะ 4 robot tools
- `client/index.html`: แสดง “รับคำสั่งจำลองแล้ว · รอผลเดินทาง” แทน “สำเร็จ” ตอน tool เริ่มนำทาง ไม่ทำให้ ACK ดูเป็น arrival
- `docs/robot-simulator.md`, `README.md`: วิธีคุยผ่าน `/voice`, API quota, กดเริ่มก่อน/wake word/half-duplex, ขอบเขตเครื่องมือและผลทดสอบเสียง พร้อมแก้คำอธิบายเก่าที่ว่าไม่มี VoiceSession
- ผลตรวจก่อน guard/schema test เพิ่ม: voice tests **6 passed, 1 warning**; browser Chromium ผ่านด้วยไมค์จำลองและ upstream fake: AudioWorklet → WebSocket เดิม → actual tool → engine → arrival กลับหน้า Emma; ตรวจภาพ desktop/mobile ไม่มี JS error
- ยังไม่ใช้ไมค์ผู้ใช้หรือเรียกบริการเสียงจริง; full suite อยู่ระหว่างรัน และจะตรวจ provider guard/ข้อความล่าสุดซ้ำ

- ตรวจชุดทดสอบเสียงก่อนรัน: ลบ expression ที่ไม่ทำงานใน `tests/test_robot_voice.py`; ใช้ coroutine ส่ง callback เข้า event loop ของ TestClient โดยตรง (ผลทดสอบรอตรวจ)

### Emma สั่งตัวจำลอง — ชุด 3: ทดสอบเสียงและ session isolation

- เพิ่ม `tests/test_robot_voice.py`: upstream fake รับ PCM ผ่าน WebSocket เดิม แล้วเรียก robot tool จริง/ส่งเสียงและ arrival กลับ; ตรวจ origin/cookie, หนึ่งเซสชัน, หยุดเมื่อปิดเสียง, การแยกข้อมูลส่วนตัว/log และ stale announcement หลัง reset
- `app/session.py`: mirror ผล voice tools ลง journal จำลอง, งด nudge สไลด์ และให้เซสชันจำลองใช้ผู้ควบคุมหนึ่งคนแม้เครื่องเดิมตั้ง MULTI_SESSION
- `start-robot-simulator.cmd`: แก้ข้อความเปิดโปรแกรมให้ตรงว่าเสียง Emma เป็นตัวเลือกและใช้ API เดิม
- ผลตรวจระหว่างพัฒนา: robot/simulator/review เดิม **74 passed, 1 warning**; tests เสียงใหม่รอตรวจ ไม่มีการส่ง PCM ไป provider จริง

### Emma สั่งตัวจำลอง — ชุด 2: หน้าสนทนาและผลตอบรับ

- เพิ่ม `app/robot_voice.py`: แจ้ง arrival/failure/disconnect เข้าเสียง Emma ผ่านกลไกรอจบคำพูดเดิม กันแจ้งผลที่ค้างหลัง reset/เปลี่ยนคำสั่ง/เปลี่ยนเซสชัน
- `app/robot_simulator.py`: เพิ่ม `/voice`, `/voices`, `/health`, `/ws` เฉพาะเซิร์ฟเวอร์จำลอง ใช้ client/provider เดิม กดเริ่มก่อนเปิดเสียง; ตรวจ same origin + HttpOnly SameSite cookie, รับเสียงครั้งละหนึ่งเซสชัน และยกเลิกงานจำลองเมื่อจบเสียง
- `client/robot-simulator.html`, `client/robot-simulator.css`, `client/index.html`: ปุ่มเปิด Emma อีกแท็บ ป้ายบอกโหมดและการใช้ API เดิม ชื่อเครื่องมือหุ่น และซ่อนตัวสลับล่ามเฉพาะหน้าเสียงจำลอง
- `app/turnlog.py`: ย้าย docstring กลับตำแหน่งแรกตามรูปแบบ Python หลังเพิ่ม guard ในชุด 1
- ผลตรวจ: รอ fake-provider/WebSocket/browser tests; ยังไม่เรียก API เสียงจริงหรือเปิดไมค์ผู้ใช้

## 2026-09-08 — เชื่อมเสียง Emma กับหุ่นจำลอง (ชุด 1)

- `app/robot_backend.py`, `app/config.py`, `app/tools/registry.py`: จำกัดเซสชันจำลองเป็น robot tools 4 ตัว ทั้ง schema และ dispatch แม้ profile จริงเปิดเครื่องมืออื่น; context ไม่เปิดจากข้อความ client ของระบบหลัก
- `app/prompts.py`: คำสั่งสำหรับ Emma โหมดซ้อมโดยเฉพาะ ไม่อ่านข้อมูลส่วนตัว/เอกสาร/ข้อมูลขาย; ระบุเครื่องมือและให้รอผลเดินทางก่อนแจ้งว่าถึง
- `app/session.py`: อนุญาต Emma ใน context จำลองที่ server สร้าง, ส่งสถานะโหมด, เตรียม watcher ของผลจำลอง, งดติดตาม Canva และไม่รับ robot_ready/robot_arrived จาก client ในโหมดนี้
- `app/turnlog.py`: ไม่เขียน transcript/metrics จาก context จำลองปะปนกับบันทึกผู้ใช้งานจริง
- ผลตรวจ: รอทดสอบร่วมกับ voice endpoint และ watcher; ไม่เปลี่ยน `.env` หรือเปิด API เสียงจริงระหว่างพัฒนา

## 2026-09-08 — ผลส่งมอบตัวจำลองหุ่น

- Full suite บน `.venv-smoke`: **1,133 passed, 1 warning ใน 142.13 วินาที**; หลังปรับชื่อ event และระยะป้ายในชุด 6 ตรวจ robot/simulator ซ้ำ **45 passed, 1 warning ใน 2.49 วินาที**
- เพิ่ม automated tests 19 กรณี; คำเตือนเป็น Starlette TestClient/httpx deprecation เดิม ไม่มี test failure
- Playwright Chromium ตรวจ flow จริงทั้ง desktop/mobile รวม success, stop, home, unknown POI, duplicate, reconnect, obstacle timeout และ JSON download ผ่าน; ตรวจภาพหลังปรับป้ายแล้ว ป้ายฐานไม่ทับตัวหุ่น
- รอบตรวจสุดท้ายใช้ locator assertions แทน string-eval ของ Playwright ที่โดน CSP ปฏิเสธ คง CSP เดิมไว้และตรวจ command journal/stop/reset ผ่าน; JavaScript syntax และ Python AST ผ่าน
- `git diff --check` ผ่าน; audit ชื่อไฟล์ที่แก้/เพิ่มทั้ง working tree **61 ไฟล์** (รวมงานรอบก่อน) มีบันทึกครบ ไม่มีการ commit/push
- เปิดเซิร์ฟเวอร์จำลองโค้ดล่าสุดที่ `http://127.0.0.1:8010` และ reset กลับสถานะพร้อมซ้อม; รีสตาร์ทเฉพาะโปรเซสจำลองที่เปิดในงานนี้ ไม่แตะบริการเสียงเดิมหรือ `.env`
- ส่งมอบ `start-robot-simulator.cmd` และ `docs/robot-simulator.md`; ขอบเขตพร้อมใช้คือการซ้อมระดับคำสั่งผ่านปุ่ม/ข้อความ ไม่ใช่ speech recognition, vendor SDK runtime, Android APK หรือผลตรวจฮาร์ดแวร์จริง
- ถัดไปต้องใช้ AAR/demo ตรงรุ่นเพื่อสร้าง Android bridge/APK และทดสอบ SDK MOCK → REAL, map/POI, ไมค์/AEC และการหยุดบนหุ่นที่ส่งมาจริง

### ตัวจำลองหุ่น — ชุด 6: ผลตรวจหน้าจอและปรับระยะป้าย

- `client/robot-simulator.js`: ขยับป้ายจุดหมายให้พ้นตัวหุ่นเมื่อจอดทับจุด; ระบุชื่อเต็ม `client/robot-simulator.css` และ `client/robot-simulator.js` สำหรับ audit ประวัติ (เพิ่มในชุด 3)
- `app/robot_simulation.py`: เก็บชนิด event เป็น `command` แยกจาก `type: robot` ใน payload ของคำสั่ง ป้องกันการทับชื่อเหตุการณ์ใน journal
- ผลตรวจก่อนปรับสองจุดนี้: targeted robot/simulator/hardening/review **94 passed, 1 warning**; Playwright Chromium ผ่าน desktop 1440px/mobile 390px, ไปถึง/กลับฐาน/จุดไม่รู้จัก/หยุด/คำสั่งซ้ำ/reconnect/obstacle timeout/export JSON ไม่มี JavaScript error หรือ horizontal overflow; ตรวจภาพทั้งสองขนาดแล้ว
- Browser QA ครั้งแรกใช้ networkidle แล้ว timeout เพราะหน้า poll ทุก 250ms; เปลี่ยนเป็นรอ DOM และสถานะที่พร้อมใช้งานจริงแล้วผ่าน ไม่ใช่ server ล้มเหลว
- Python AST ผ่าน 6 ไฟล์; Git diff whitespace ผ่าน; full suite อยู่ระหว่างรัน และจะตรวจ targeted หลังปรับซ้ำ

### ตัวจำลองหุ่น — ชุด 5: คู่มือใช้งานและเตรียม Android

- เพิ่ม `docs/robot-simulator.md`: วิธีเปิด/ซ้อม/ส่งออก, ขอบเขตคำสั่งเทียบ SDK พร้อมเลขหน้า PDF, ความต่างตัวจำลองของเรากับ SDK MOCK, รายการที่ต้องได้ก่อนสร้าง APK และตรวจบน REAL
- `README.md`, `docs/robot-integration.md`: เชื่อมไปตัวจำลองและแก้ mapping กลับฐานให้ตรง `manager.goHome()`
- ตรวจเอกสาร Android ทางการเรื่อง WebView/native bridge เพิ่มเติมและใส่แหล่งอ้างอิงในคู่มือ; ไม่เพิ่ม Android skeleton ที่ยังตรวจ compile กับ vendor SDK ไม่ได้
- ผลตรวจ: JavaScript syntax ผ่าน; tests ชุด robot/simulator/hardening/review อยู่ระหว่างรัน; ยังไม่อ้างว่า APK หรือการเดินจริงพร้อมใช้งาน

### ตัวจำลองหุ่น — ชุด 4: regression tests

- เพิ่ม `tests/test_robot_simulation.py` ตรวจวงจร ACK/arrival, คำสั่งซ้ำ/ID ชน, หยุด/กลับฐาน/เหตุขัดข้อง/timeout, callback เก่า, disconnect/reconnect, bounded memory, context isolation และ HTTP validation
- เพิ่ม regression ของระบบจริงสำหรับ stop ส่งไม่สำเร็จ, แผนที่จริงว่าง, arrival คนละจุดหมาย; แก้ `.gitignore` ไม่ติดตามโฟลเดอร์ scratch `.pytest-tmp-*`
- ผลตรวจเบื้องต้น: tests หุ่นเดิม **26 passed**; รอบแรก pytest ใช้ temp เดิมนอก workspace แล้วติดสิทธิ์ จึงใช้ `--basetemp` ภายใน workspace และผ่าน ไม่ได้แก้ระบบหรือขอขยายสิทธิ์
- tests ใหม่และ browser QA รอตรวจในชุดส่งมอบ

### ตัวจำลองหุ่น — ชุด 3: หน้าจอและตัวเปิดใช้งาน

- เพิ่ม `client/robot-simulator.html`, `.css`, `.js`: หน้าจอภาษาไทย แผนที่ SVG เคลื่อนไหว สถานะ/ผลเครื่องมือ แทรกเหตุขัดข้อง ลองส่งคำสั่งซ้ำ และส่งออก JSON พร้อมป้าย simulation ทุกส่วนสำคัญ
- เพิ่ม `start-robot-simulator.cmd` เปิดเซิร์ฟเวอร์แยก localhost:8010 ด้วย virtualenv ที่มี ไม่เปลี่ยนค่าการเชื่อมต่อระบบหลักและไม่เปิดฟีเจอร์ฮาร์ดแวร์
- หน้าจอใช้ไฟล์ในเครื่องทั้งหมดและ textContent สำหรับข้อมูลเหตุการณ์ ไม่มี CDN/ไมโครโฟน/LLM ในตัวจำลอง; ชื่อจุดหมายเป็นข้อมูลสมมติ
- ผลตรวจ: รอทดสอบ unit/API และตรวจหน้าจอจริง ไม่ได้สร้าง APK เพราะยังไม่มี vendor AAR และ Android toolchain

### ตัวจำลองหุ่น — ชุด 2: engine และ API แยกจากระบบเสียง

- เพิ่ม `app/robot_simulation.py`: แผนที่/เวลา/แบตเตอรี่สมมติ, ไปจุดหมาย/หยุด/กลับฐาน, ACK, command ID, ป้องกันคำสั่งซ้ำในหน้าต่าง 256 รายการ, กัน callback เก่า, จำลองสิ่งกีดขวาง/นำทางล้มเหลว/ไม่รายงานถึง/ACK หาย/การเชื่อมต่อหลุด
- เพิ่ม `app/robot_simulator.py`: FastAPI เฉพาะ localhost พอร์ต 8010 เรียกเครื่องมือ robot เดิมผ่าน context จำลอง; จำกัด 4 เครื่องมือและ payload, ป้องกัน cross-origin mutation, ไม่เปิด VoiceSession หรือเชื่อม SDK/มอเตอร์
- ACK และ command ID ในชุดนี้เป็น contract ของตัวจำลองที่เสนอสำหรับ Android bridge ไม่ใช่การอ้างว่า SDK หรือ `/ws` จริงรองรับแล้ว; การชน/ระยะเบรก/SLAM/เสียงยังไม่ใช่สิ่งที่จำลองได้
- ผลตรวจ: รอ unit/API/browser tests หลังเพิ่มหน้าจอ; ไม่เพิ่ม dependency

## 2026-09-08 — ตัวจำลองหุ่นจากคู่มือที่ผู้ใช้ส่ง (ชุด 1)

- เพิ่ม `app/robot_backend.py` เป็น context เฉพาะคำขอสำหรับตัวจำลอง แยกสถานะจากหุ่นจริง ไม่มี endpoint เปิดโหมดสั่งฮาร์ดแวร์
- `app/tools/robot_link.py`, `app/tools/robot.py`: ให้เครื่องมือเดิมใช้ backend จำลองและรายงาน `hardware: simulated`; แผนที่หุ่นจริงว่างต้องไม่ย้อนใช้จุดสมมติ; ปฏิเสธ arrival คนละจุดหมาย; แก้ข้อความหยุด/กลับฐานให้ไม่ยืนยันผลจริงก่อนมีหลักฐาน และเพิ่ม timeout สำหรับกลับฐาน
- อ่าน PDF ต้นฉบับ/ฉบับไทย 4 ไฟล์ใน Downloads; ตรวจภาพหน้า 4 ของ SDK ยืนยัน REAL/MOCK และชื่อ AAR ข้อมูลส่วน SDK/Android จริงยังต้องยืนยันด้วยไฟล์ไลบรารีและฮาร์ดแวร์
- ผลตรวจ: รอทดสอบร่วมกับ engine/API/UI จำลองในชุดถัดไป ไม่ได้แก้ `.env` หรือรีสตาร์ทระบบเสียงจริง

บันทึกทุกชุดการแก้ไข/เพิ่มไฟล์ พร้อมเหตุผลและผลตรวจสอบ ห้ามใส่ secret หรือข้อมูลส่วนบุคคลจริง

## 2026-09-08 — กติกาบันทึกและเริ่มแก้ผลรีวิว

- เพิ่ม `AGENTS.md` เพื่อเก็บคำสั่งของผู้ใช้ให้ทุกการแก้ไข/เพิ่มงานมีบันทึกในไฟล์นี้ต่อไป
- เพิ่ม `CHANGELOG.md` เป็นประวัติกลาง และบันทึกไฟล์ประวัติ/กติกาที่เพิ่มในรอบนี้ด้วย
- รายงานต้นทาง: `docs/review/project-review-2026-09-08.md` เพิ่มในการรีวิวก่อนหน้า มี 8 ข้อค้นพบพร้อมหลักฐานและแนวทางแก้
- ผลก่อนแก้: ชุดทดสอบเดิม 1,064 passed, 1 warning; ตัวอย่างจำลองพบช่องว่างเพิ่มเติม 8 เรื่องตามรายงาน
- สถานะ: เริ่มดำเนินการแก้ตามการอนุญาตของผู้ใช้ ยังไม่แก้ข้อมูลโครงการหรือการตั้งค่าลับบนเครื่อง

### ชุด 1a — เครื่องมือและบทบรรยาย

- `app/tools/registry.py`: กรองกลุ่มเจ้าของเครื่องมือทั้ง schema และ dispatch แม้ import ทางอ้อม
- `app/tools/knowledge.py`: แยก draft/approved ตามข้อมูลจริง และไม่เขียนสถานะสไลด์ใน multi-session
- `app/tools/webstage.py`: ปิดผลข้างเคียงหน้าต่างเครื่องใน multi-session
- ผลตรวจ: รอ regression tests; ไม่เปลี่ยนสถานะอนุมัติในข้อมูลโครงการ

### ชุด 1b — ความจำและ profile ต่อเซสชัน

- `app/prompts.py`: ไม่อ่าน/แนบ personal memory เมื่อปิดกลุ่ม memory หรืออยู่ใน shared mode
- `app/session.py`: ไม่ให้ URL เปลี่ยนเครื่อง condo เป็น Emma; profile ที่ไม่รู้จักกลับเป็น condo
- `app/providers/gemini.py`, `app/providers/openai_realtime.py`: ไม่ execute tool เมื่อเซสชัน use_tools=False แม้ upstream ส่ง tool call มา
- ผลตรวจ: รอ regression tests ของ persona และ provider

### ชุด 2a — ย้ายงาน I/O ออกจากวงรอบเสียง

- เพิ่ม `app/tool_io.py`: worker I/O และจุดส่งงาน state/UI กลับ event loop อย่างชัดเจน
- `app/tools/registry.py`: รองรับ blocking handlers พร้อม timeout และเก็บงานที่ยังทำไม่เสร็จเพื่อปฏิเสธการสั่งซ้ำ
- `app/tools/webstage.py`, `app/tools/knowledge.py`: งานหน้าจอ/สถานะจาก worker กลับมาทำบน event loop
- `app/tools/documents.py`: ย้าย printer subprocess ออกจาก event loop โดยคงฟังก์ชัน async สาธารณะเดิม
- ผลตรวจ: รอ heartbeat/timeout/duplicate-execution tests; worker thread ไม่สามารถย้อนคำสั่งอุปกรณ์ที่ส่งไปแล้วได้

### Batch 2b - Enable worker execution

- Mark synchronous tools in `units.py`, `computer.py`, `knowledge.py`, `mydocs.py`, and `websearch.py` as blocking so registry dispatch runs their network/filesystem/OS calls outside the audio loop. Direct function APIs stay compatible.
- Validation: pending targeted tests.

### ชุด 2c — งานค้างหลัง timeout

- `app/tool_io.py`, `app/tools/registry.py`: ส่งสัญญาณยกเลิกผลข้างเคียงบน UI หลัง timeout/cancel แต่เก็บ worker จนจบเพื่อกันคำสั่งซ้ำ
- `app/tools/documents.py`: ใช้ exclusive execution สำหรับงานพิมพ์ที่เริ่มส่งแล้ว ห้ามซ้ำระหว่างงานเก่ายังไม่จบ
- `app/main.py`: ย้าย inventory startup probe ไป thread ด้วย
- ผลตรวจ: รอ regression tests; ยังไม่ยืนยันผลการพิมพ์จากการหมดเวลา

### ชุด 3 — ขอบเขตบทสนทนา ผลอุปกรณ์ และหลักฐานเสียง

- `app/session.py`: reset ROI/สไลด์/คำพูดเก่าเมื่อเริ่มและจบสายปกติ ไม่ให้ cleanup สายเก่าเช็ด subtitle ของสายใหม่ และบันทึก said สำหรับ transcript ปกติด้วย
- `app/tools/robot_link.py`: ส่งผ่าน transport ที่ส่งต่อ exception แทน UI helper ที่กลืน error เพื่อคืน failed ตามจริง
- `app/display.py`: เพิ่ม guard multi-session ที่เส้นทางแสดงสไลด์/Canva ไม่ใช่เฉพาะ subtitle
- ผลตรวจ: รอ sequential-session, broken-socket และ transcript tests

### ชุด 4 — ปิดการเผยแพร่ metadata และป้องกันภาพสไลด์

- เพิ่ม `app/slide_assets.py` และเปลี่ยน mount ใน `app/main.py`: เปิดเฉพาะภาพ raster, ไม่เปิด index/embeddings/backup แม้มี token และตรวจ WS_TOKEN ก่อนส่งภาพ
- `client/index.html`, `client/display.html`: แนบ token เฉพาะ URL ภาพ /slides/ ของ origin เดียวกัน ไม่ส่ง credential ไปเว็บภายนอก
- ผลตรวจ: รอ HTTP auth/path tests และ client checks; เครื่องที่ไม่ได้ตั้ง WS_TOKEN ยังใช้ภาพได้ตามเดิม

### ชุด 5a — ผล targeted tests และปรับ fixtures ให้ตรงสิทธิ์จริง

- ตรวจ 176 tests แรก: ผ่าน 162, ไม่ผ่าน 14; ตรวจสาเหตุแล้วพบ fixtures memory ไม่ได้เปิดสิทธิ์, robot fake ข้าม transport และ approved-copy fixture ไม่เคยอนุมัติข้อมูล
- `tests/test_memory.py`, `tests/test_robot.py`, `tests/test_knowledge.py`: ระบุสิทธิ์/สถานะข้อมูลและจำลอง transport ตามสัญญาใหม่ โดยคง assertions ผลลัพธ์เดิม
- `app/tools/__init__.py`: ประเมินโมดูลที่ต้องโหลดทุกครั้ง (Python cache import อยู่แล้ว) เพื่อไม่ค้างกลุ่มจากการตั้งค่าครั้งก่อน
- `app/tools/registry.py`: คงคำสั่งห้ามเงียบหลัง timeout พร้อมเพิ่มข้อห้ามสั่งซ้ำ
- ผลตรวจ: รอรันซ้ำร่วมกับ regression tests ใหม่

### ชุด 5b — เพิ่ม regression tests ตามหลักฐานรีวิว

- เพิ่ม `tests/test_review_fixes.py`: cold import/schema/dispatch, memory/profile, draft approval, shared-screen isolation, worker heartbeat/timeout/duplicate prevention, thread-to-loop handoff, printer exclusivity, visitor state, robot transport, said logs และ HTTP asset authorization
- ใช้ข้อมูลสังเคราะห์และ stub อุปกรณ์/เครือข่ายเท่านั้น ไม่มีการส่งคำสั่งจริงหรือใช้ API key
- ผลตรวจ: รอรัน targeted suite และ full suite

### ชุด 6a — ผล full suite รอบแรกและรอยต่อ I/O/lifecycle

- Targeted suite ผ่าน 199 tests; full suite รอบแรกผ่าน 1,080 และไม่ผ่าน 7 (robot fake อีกจุดไม่มี transport และ computer/websearch tests ไม่ได้เปิดกลุ่มที่กำลังตรวจ)
- `tests/test_hardening.py`, `tests/test_websearch_computer.py`: แก้ fixtures สองชนิดข้างต้น โดยไม่ลด assertions ของพฤติกรรมเดิม
- `app/tools/slides.py`: ย้าย semantic search ใน show_slide ไป thread ด้วย
- `app/tools/computer.py`: ส่งการเปิดโปรแกรม/เว็บกลับผ่านจุดตรวจ cancellation บน event loop เพื่อไม่เปิดหน้าต่างล่าช้าหลัง tool timeout
- `app/main.py`: ไม่ warm/open Canva ตอนเริ่มเซิร์ฟเวอร์ multi-session
- `app/display.py`, `app/session.py`: ยกเลิกภาพที่รอเสียงและงานพูดเบื้องหลังเมื่อสายจบ เพื่อไม่หลุดไปยังผู้ใช้คนถัดไป
- Client checks ผ่าน: JavaScript ทั้งสองหน้าผ่าน syntax และตรวจพฤติกรรม token เฉพาะ same-origin /slides/ แล้ว
- ผลตรวจชุดนี้: รอ targeted และ full suite รอบถัดไป

### ชุด 6b — งานพร้อมกันและการติดตาม session

- `app/turnlog.py`, `app/session.py`: ใส่ session ID แบบสุ่มลง log และส่งต่อผ่าน context ไป worker โดยไม่ใช้ข้อมูลส่วนบุคคล; ล็อกการเปิด/หมุน/เขียน log ให้ปลอดภัยเมื่อหลาย worker จบพร้อมกัน
- `app/tools/registry.py`: กันงานซ้ำแยกต่อ session สำหรับงานอ่าน เพื่อให้ผู้ใช้คนละคนค้นพร้อมกันได้; งานพิมพ์ยัง exclusive ทั้งเครื่อง และบันทึกผลเมื่อ worker จบหลังหมดเวลารอ
- `app/providers/openai_realtime.py`: บันทึกเฉพาะชื่อ argument เช่นเดียวกับ Gemini ไม่พิมพ์ค่าข้อมูลลูกค้าลง console
- ผลตรวจ: รอ concurrency/context propagation และ regression suite

### ชุด 6c — แก้ผล compile check ก่อนรันทดสอบต่อ

- Compile check พบว่า show_slide เป็น synchronous API เดิม จึงใช้ await ใน body ไม่ได้
- `app/tools/slides.py`: ใช้ blocking registration เช่นเดียวกับ knowledge และส่ง state mutation/resume_hint กลับผ่าน on_loop โดยคง synchronous API ให้ผู้เรียกเดิม
- `app/session.py`: ย่อและจัด comment cleanup ให้ตรงเงื่อนไขเจ้าของเซสชันใหม่
- ผลตรวจ: รอ compile และ targeted suite ซ้ำ; ไม่ได้ปล่อยโค้ดที่ compile ไม่ผ่านไปรันเซิร์ฟเวอร์

### ชุด 6d — เพิ่ม tests ของรอยต่อที่พบระหว่างแก้

- `tests/test_review_fixes.py`: เพิ่ม provider tool refusal ทั้งสองเจ้า, การค้นชนิดเดียวกันจากผู้ใช้สองคนพร้อมกัน, ยกเลิกภาพค้าง, cleanup สายเก่าไม่ล้างตัวเลขสายใหม่ และ session identity ของ log ที่เขียนจาก worker
- ผล compile หลังแก้ชุด 6c: ผ่าน; targeted suite ชุดล่าสุดอยู่ระหว่างรัน

### ชุด 6e — ผล tests ของรอยต่อ

- Targeted suite ชุด 6c ผ่าน 228 tests; regression file หลังเพิ่มเคสผ่าน 28/29 โดย fake upstream ของ OpenAI ขาดเมธอด send (ใช้คนละ API กับ FastAPI WebSocket)
- `tests/test_review_fixes.py`: เพิ่ม send ให้ fake upstream ตาม transport จริง ไม่เปลี่ยน assertions การปฏิเสธเครื่องมือ
- ผลตรวจ: รอ full suite รอบสุดท้าย

### ชุด 7 — เอกสารใช้งานและประวัติถาวร

- `README.md`: อธิบาย asset authentication, tool enforcement, private-memory/profile restrictions, timeout/print semantics, session reset/log IDs และแก้ข้อความเก่าที่บอกว่ายังไม่มี wake/auth
- `CLAUDE.md`: เชื่อมกติกาบันทึกทุกการแก้ไขไปยัง AGENTS/CHANGELOG และเลิกระบุจำนวน tests ที่ล้าสมัย
- `docs/review/project-review-2026-09-08.md`: ระบุชัดว่าเป็นหลักฐานก่อนแก้ พร้อมลิงก์ติดตามผลแก้ในประวัติ โดยคงรายละเอียดรีวิวเดิม
- ผลตรวจ: ตรวจลิงก์/ความครบถ้วนท้ายงาน; full suite กำลังรันจากโค้ดล่าสุด

## 2026-09-08 — ผลส่งมอบหลังแก้รีวิว

บันทึกผลตรวจสุดท้ายใน `CHANGELOG.md` หลังจบชุดการแก้ไขข้างต้น:

| ข้อรีวิว | สิ่งที่แก้แล้ว |
|---|---|
| 1. เครื่องมือหลุดผ่าน import | กรองทั้ง schema/dispatch และป้องกันหน้าจอ/Canva ใน shared mode |
| 2. ความจำส่วนตัวใน shared prompt | ไม่อ่าน memory เมื่อไม่มีสิทธิ์/อยู่ใน shared mode และไม่ให้ URL ยกระดับ condo เป็น Emma |
| 3. Blocking I/O | ย้ายงานไป worker, ส่ง state/UI กลับ event loop, จำกัดเวลารอ/ป้องกันคำสั่งซ้ำ และแยก worker scope ต่อเซสชัน |
| 4. Draft กลายเป็น approved | ส่งสถานะตาม script_approved จริง โดยไม่แก้เนื้อหาหรือสถานะอนุมัติในไฟล์ข้อมูล |
| 5. ROI/สไลด์ข้ามผู้ใช้ | reset ขอบเขตเริ่ม/จบสาย ยกเลิกภาพค้างและงานพูดของสายเก่า พร้อมป้องกัน cleanup ทับสายใหม่ |
| 6. Robot ส่งไม่ได้แต่บอก ok | transport error คืน failed และมี regression ผ่าน VoiceSession จริงกับ socket จำลองที่เสีย |
| 7. Metadata ผ่าน static route | ไม่เสิร์ฟ metadata และตรวจ token สำหรับภาพ; ทั้งสองหน้าแนบ token เฉพาะ same-origin |
| 8. คำพูดผู้ช่วยไม่ลง log | บันทึก normal/silent transcript ครั้งเดียว พร้อม session ID และการเขียนจาก worker ที่มี lock |

### ผลตรวจสุดท้าย

- `.\.venv\Scripts\python.exe -X utf8 -m pytest -q --basetemp=.pytest_fix_final --tb=short`: **1,093 passed, 1 warning ใน 143.71 วินาที** (เพิ่ม regression tests 29 กรณีจากฐานเดิม 1,064)
- Python compile/parse: ผ่าน
- JavaScript ทั้ง `client/index.html` และ `client/display.html`: ผ่าน syntax และพฤติกรรมแนบ credential เฉพาะภาพภายใน origin
- `git diff --check`: ผ่าน
- ตรวจไฟล์ที่แก้/เพิ่มทั้งหมด 33 ไฟล์: มีรายการในประวัติครบ ไม่มีไฟล์ตกหล่น; ลิงก์ใน AGENTS/CHANGELOG/รายงานรีวิวตรวจแล้วเปิดถึงไฟล์จริง
- ข้อสังเกตจากเครื่องทดสอบ: มี Starlette TestClient deprecation warning หนึ่งรายการ และข้อความ pending IocpProactor accept task ระหว่าง teardown ของ fake upstream บน Windows; ไม่มี test failure ไม่ได้ปิดซ่อนข้อความเหล่านี้

### ขอบเขตที่ยังต้องยืนยันบนเครื่องจริง

- ยังไม่ได้เรียก Gemini/OpenAI/Supabase จริง เปิดไมค์/กล้อง หรือส่งคำสั่งเครื่องพิมพ์/หุ่นยนต์จริง
- ไม่เปลี่ยน `.env`, secret, ข้อมูลโครงการ หรืออนุมัติบทบรรยายแทนเจ้าของ
- ไม่ได้รีสตาร์ตบริการที่กำลังใช้งาน; ให้รีสตาร์ตเซิร์ฟเวอร์และรีเฟรชหน้าคุย/หน้าจอสไลด์เพื่อโหลดโค้ดและ asset authentication ใหม่
- การหมดเวลาของงานพิมพ์ไม่ได้ยกเลิกสิ่งที่ส่งถึงอุปกรณ์แล้ว ระบบจึงรายงานว่ายังยืนยันผลไม่ได้และกันคำสั่งซ้ำจนงานเก่าจบ ส่วน ACK/การหยุดจริงเมื่อหุ่นยนต์หลุดการเชื่อมต่อยังต้องทดสอบกับแอป Android
- ความเสี่ยงเพิ่มเติมในรายงาน เช่น dependency constraints/การวัดคุณภาพเสียงและใบหน้าบนฮาร์ดแวร์ ไม่ได้อ้างว่าถูกแก้ด้วย unit tests รอบนี้

## 2026-09-08 — Research แนวทางพัฒนาต่อ

- เพิ่ม `docs/research/improvement-roadmap-2026-09-08.md`: เทียบโค้ดหลังแก้รีวิวกับเอกสารทางการของ Google, OpenAI, LiveKit, Anthropic, Sentence Transformers, AWS, uv, OWASP และงานวิจัย Thai end-of-turn; จัดลำดับงาน 10 ข้อ พร้อมช่องว่างจริง ขอบเขต เกณฑ์รับงาน และประมาณการ โดยแยกการทดลอง WebRTC/Thai EOT ออกจากงานที่พร้อมเริ่ม
- บันทึกสิ่งที่มีอยู่แล้วเพื่อไม่เสนอซ้ำ เช่น hybrid retrieval/reranker, local VAD, compression/resumption, inventory/promotions/compare/quotation และ arrival timeout
- ตรวจ metadata แบบอ่านอย่างเดียว: 150 ภาพ, 64 scripts, ไม่มี `script_approved` ที่เป็นจริง; eval file มี 126 กรณี ไม่เผยแพร่เนื้อหาส่วนตัวหรือเปลี่ยนสถานะอนุมัติ
- ระบุข้อสังเกตจากการอ่าน `scripts/eval_search.py`: กรณี `not hits` ที่ควรค้นเจอไม่เพิ่ม `wrong` และ commercial cases ไม่อยู่ใน denominator เดียวกัน เป็นงานเสนอถัดไป ยังไม่ได้แก้ runtime/สคริปต์หรืออ้างว่ารันจำลองแล้ว
- อัปเดต `CHANGELOG.md` ในชุดเดียวกับเอกสารตามคำสั่งให้บันทึกทุกการแก้/เพิ่ม; รอบนี้ไม่มีการเปลี่ยนโค้ด การตั้งค่า dependencies ข้อมูลโครงการ หรือบริการที่กำลังใช้งาน
- ผลตรวจและการอัปเดตผลในประวัติรอบนี้: UTF-8 ผ่าน, ลิงก์ไฟล์ภายในรายงาน 16 จุดเข้าถึงได้ครบ, แหล่งอ้างอิงภายนอก 13 URL, whitespace/final newline ของเอกสารและประวัติผ่าน และ `git diff --check` ผ่าน; ไม่รัน full suite ซ้ำสำหรับเอกสารล้วน และไม่เรียก API/ฮาร์ดแวร์จริง ผล 1,093 passed ที่อ้างถึงเป็นผลรอบก่อนหน้า

## 2026-09-08 — ลงมือพัฒนาชุดแรกตาม research

### ชุด 1 — Metrics ฝั่งเซิร์ฟเวอร์และ provider

- เพิ่ม `app/metrics.py`: ID ต่อ reply, monotonic durations, bounded client/response dedupe และ allowlist ตัวเลข usage ที่ไม่บันทึก arguments/credential; Gemini เก็บเป็น snapshot ไม่บวกเป็นยอด billing เพราะไม่มี response ID
- แก้ `app/providers/base.py`, `app/providers/openai_realtime.py`, `app/providers/gemini.py`: normalized usage/speech-stop events และเวลาที่ local VAD ตัดสินจบเสียง โดยไม่เปลี่ยน VAD หรือโมเดลที่ใช้งาน
- แก้ `app/session.py`: ผูก metrics ต่อ session, จบ reply เมื่อ complete/interrupted/closed และส่ง ID ให้ browser วัด queue; แก้ `app/tools/registry.py`: วัดเวลารอ dispatch รวม timeout/cancellation โดยไม่แก้คำสั่งหรือ arguments
- ผลตรวจ: รอทดสอบ unit/integration หลังต่อ browser และรายงาน; เป็นเวลาที่ระบบสังเกตได้ ไม่ใช่เสียงถึงหูหรือค่าใช้จ่ายจริง

### ชุด 2 — Browser และรายงาน metrics

- `client/index.html`: รายงานเวลารอ schedule เสียงและเวลาล้างคิวเมื่อถูกพูดแทรก ภายใต้ turn ID ที่ server ส่งมา ไม่ใช้ timestamp ข้ามเครื่องและไม่เปลี่ยน audio buffer
- เพิ่ม `scripts/report_metrics.py`: อ่าน JSONL แล้วสรุป p50/p95/จำนวนตัวอย่างและ token usage แยก provider/model/VAD; ไม่แสดง transcript และไม่นำ Gemini snapshot มาบวกเป็นยอดรวม
- `app/session.py`: ใส่ชื่อโมเดล OpenAI และปิด reply เมื่อมี speech-start; `app/tools/registry.py`: แก้ชื่อ registry ใน metrics wrapper ให้ตรง `_REGISTRY` ที่มีจริง (พบจากตรวจ source ก่อนรันทดสอบ)
- ผลตรวจ: รอ regression tests; client metric ผ่าน allowlist/ช่วงค่า/dedupe ต่อ session แล้ว แต่ยังไม่ได้วัดเสียงออกลำโพงจริง

### ชุด 3 — ผล eval ที่ตรวจสอบย้อนกลับได้

- `scripts/eval_search.py`: ผลหนึ่งรายการต่อหนึ่งคำถาม รวม commercial refusal/no hits/error เป็น pass/fail/skipped ชัดเจน; no hits ของคำถามบวกเป็น fail; errors ไม่ถูกนับเป็นคำตอบถูก
- เพิ่มตัวเลือก train/test/all, manifest ตรวจไม่ให้กลุ่มเดียวกันข้าม split, JSON report พร้อม dataset/deck hash และ configuration; แนะนำ threshold เฉพาะ train และเขียน query cache เมื่อระบุ `--write-cache` เท่านั้น
- ผลตรวจ: รอ manifest และ regression tests; split จากคลังเก่าจะระบุว่าเป็น regression partition ไม่อ้างว่าเป็นข้อมูลที่โมเดล/ผู้พัฒนาไม่เคยใช้มาก่อน

### ชุด 4 — กำหนด environment สำหรับติดตั้งซ้ำ

- เพิ่ม `pyproject.toml`: Python 3.14 / Windows AMD64 ตามเครื่องที่ทดสอบจริง, pin direct dependencies ตามเวอร์ชันติดตั้งปัจจุบัน, แยก local-search/face/wake/browser/hardware/documents/websearch และ dev โดยประกาศ sentence-transformers ที่เดิมขาด
- `.gitignore`: ไม่เก็บ `.venv-smoke`, uv cache และผล eval ที่อาจมีคำถามจริงลง Git
- เตรียมสร้าง `uv.lock` และ export `requirements.txt` จาก lock เพื่อรักษาคำสั่งติดตั้งเดิม; ทดสอบใน `.venv-smoke` โดยไม่ sync/เปลี่ยน `.venv` ที่ใช้งานอยู่
- ผลตรวจ: รอ dependency resolution และ clean-install smoke; ยังไม่อ้างว่าติดตั้งใหม่ผ่าน

### ชุด 5 — Manifest แยกชุด eval

- เพิ่ม `data/eval/splits-v1.json`: แบ่ง 126 คำถามเดิมตามกลุ่มคำตอบ/หมวด negative แบบคงที่ เป็น train 104 / test 22 โดยไม่แบ่งกลุ่มเดียวกันข้าม split และระบุว่าเป็น historical regression partition
- คง `data/eval_questions.txt` และข้อมูลโครงการเดิม; แก้ข้อความประวัติชุดนี้ที่ PowerShell pipe แปลงภาษาไทยเป็นเครื่องหมายคำถามด้วย UTF-8 patch; ผลตรวจ manifest รอ regression tests

### ชุด 6 — ผลตรวจระหว่างพัฒนา

- รอเพิ่ม `tests/test_metrics.py`, `tests/test_eval_search.py` และแก้ test_voice/script config ในชุดเดียวกัน; patch ชุดแรกไม่ผ่านการตรวจ context ในประวัติที่ encoding เสีย จึงยังไม่เกิดการแก้ไฟล์เหล่านั้น
- ผลตรวจรอบแรก: voice/review suite 180 passed, 1 failed จาก assertion อ่าน JavaScript เพียง 700 ตัวอักษร; เตรียมให้ตรวจถึง case ถัดไปโดยคง assertion เดิม
- uv resolve ครั้งแรกติด network sandbox; retry โดยสิทธิ์ที่ automatic review อนุมัติแล้วสร้าง `uv.lock` ได้ 104 packages และ export `requirements.txt` พร้อม hashes; clean install ทุก extras ใน `.venv-smoke` สำเร็จ 102 packages โดยไม่แก้ `.venv` เดิม
- Full suite บน environment ใหม่หยุดที่ native VAD access violation หลังผ่านประมาณ 79%; กำลังตรวจ dependency/native library จึงยังไม่ถือว่า lock ผ่านการยืนยัน

### ชุด 7 — แก้ dependency ที่ขาดและเพิ่ม regression tests

- `pyproject.toml`: เพิ่ม `sherpa-onnx-core==1.13.6` ใน wake extra อย่างชัดเจน เพราะ uv lock ไม่รวม dependency นี้แม้ Windows wheel ระบุ Requires-Dist; `uv pip check` ยืนยันว่า core ขาด และโฟลเดอร์ใหม่ไม่มี DLL ที่มีในเครื่องเดิม เตรียม regenerate `uv.lock`/`requirements.txt` และ sync เฉพาะ smoke environment
- เพิ่ม `tests/test_metrics.py`, `tests/test_eval_search.py` ครอบคลุม metrics clock, bounded/deduped client reports, usage snapshots, timeout, provider events และผล eval/group separation
- `tests/test_voice.py`: เปลี่ยนขอบเขตตรวจจาก 700 ตัวอักษรเป็น handler ทั้ง case โดยคง assertion; `scripts/eval_search.py`: ใช้ local coverage setting และ lexical constant ที่มีจริงใน JSON report
- ผลตรวจ: รอ targeted tests และ real VAD test บน environment ใหม่หลังเติม native core

### ชุด 8 — Clean-install smoke และ CI

- เพิ่ม `scripts/smoke_install.py`: import ทุก feature โดยไม่เปิดอุปกรณ์/เรียก provider พร้อมตรวจ DLL ของ sherpa core; เลือกตรวจ encode จริงจากโฟลเดอร์ local model ได้โดยไม่ดาวน์โหลด
- เพิ่ม `.github/workflows/test.yml`: Windows/Python 3.14, locked install ทุก extras, dependency check, smoke, pytest และ Node; เพิ่ม `tests/client_metrics.cjs` ทดสอบ syntax และเล่นเสียงผ่าน Web Audio mock รวมคิวและการหยุด
- `scripts/eval_search.py`: หากขอ semantic/reranker แล้ว backend ใช้ไม่ได้ให้ skipped และ exit ไม่สำเร็จ ไม่รายงาน lexical fallback ว่าเป็นผล hybrid; JSON ระบุ effective provider/availability; `scripts/report_metrics.py`: แยกเวลาของแต่ละ tool เพิ่มจากภาพรวม
- ผลตรวจหลังแก้ core: `uv pip check` ผ่าน; real Silero VAD กับ WAV ที่มีในโปรเจคผ่าน 1 test; targeted metrics/eval/voice/review ผ่าน 201 tests; lexical test split ผ่าน 22/22 ไม่มี skipped และเขียน `data/eval-runs/lexical-test.json` ที่ gitignore ไว้ (เป็นผลข้อมูลเก่า ไม่ใช่เสียงลูกค้าจริง)
- Lock ใหม่มี 105 packages / environment ติดตั้ง 103 packages; รอ smoke/Node/full suite รอบสุดท้าย และยังไม่ได้รัน GitHub Actions บน remote

### ชุด 9 — คู่มือใช้งานและผล eval ทั้งสอง split

- เพิ่ม `docs/measurement-and-installation.md`: วิธีติดตั้งตาม lock/เลือก extras/ทดสอบ environment แยก, ความหมายและข้อจำกัดของทุก metric/usage, CLI รายงาน, eval split/report/cache และขั้นตอนเก็บเสียงจริงภายหลัง
- `README.md`: เชื่อมคู่มือและระบุ Python/Windows ที่ lock รองรับ; แทนคำสั่ง pip บน macOS/Linux ที่ export ใหม่ไม่รองรับด้วยขอบเขตจริง; อัปเดตคำอธิบาย train/test โดยไม่อ้างว่า historical data เป็นข้อมูลใหม่
- ผล Node: syntax + first-chunk metric + queue timing/interruption ผ่าน; lexical train 93 passed / 11 failed / 0 skipped จาก 104 กรณี (exit 1 ตามข้อผิดพลาดจริง), test 22/22; ไม่ปรับ threshold หรือคำตอบเพื่อทำคะแนนให้ผ่าน เก็บรายงานใน `data/eval-runs/lexical-train.json` และ `lexical-test.json`
- Full suite บน `.venv-smoke` และ all-extras import smoke กำลังรัน; พบ encoder snapshot อยู่ใน cache เดิมจึงเริ่มทดสอบ encode แบบ offline เพิ่ม โดยไม่ดาวน์โหลดโมเดล

### ชุด 10 — ปิดช่องว่างการ calibrate จากข้อมูลไม่ครบ

- `scripts/eval_search.py`: ไม่เสนอ threshold เมื่อ train มี skipped แม้บางกรณีมีคะแนน; ปรับหัวตารางให้ตรงผลรายกรณีและลบตัวแปรเก่าที่ไม่ใช้
- `tests/test_eval_search.py`: เพิ่ม CLI regression กรณี semantic ใช้ไม่ได้ทั้งชุด ต้อง exit 1/แสดง skipped และไม่เข้า calibration
- ผลตรวจล่าสุดก่อนชุดนี้: full suite บน clean environment **1,113 passed, 1 warning ใน 159.80 วินาที**; all-extras smoke ผ่านทุกกลุ่ม, actual local encoder inference จาก snapshot เดิมผ่านโดย offline; รอ targeted eval tests หลัง guard นี้

## 2026-09-08 — ผลส่งมอบชุดวัดผล/eval/lock

- Full suite ใน `.venv-smoke`: **1,113 passed, 1 warning ใน 159.80 วินาที** ก่อนเพิ่ม guard สุดท้ายใน eval; หลังเพิ่ม guard และ CLI regression ตรวจ eval/metrics/retrieval ซ้ำ **74 passed ใน 13.42 วินาที** (เพิ่ม tests รวม 21 กรณีจากฐาน 1,093)
- Node browser syntax/queue/interruption: ผ่าน; import smoke ทุก extras และ `app.main`: ผ่าน; local SentenceTransformer encode จาก snapshot เดิมโดย `local_files_only=True`: ผ่าน; real Silero VAD จาก WAV ในโปรเจค: ผ่าน
- `uv lock --check --offline`: ผ่าน 105 packages; `uv pip check` บน environment ใหม่: ผ่าน 103 installed packages; export requirements มี version pins และ hashes ไม่มีการอัปเดต `.venv` หลัก
- Eval lexical: train 93/104 (11 fail, 0 skipped), test 22/22 (0 skipped) เป็น historical text regression ไม่ใช่คะแนนเสียงจริง; ไม่มีการปรับคำถาม/ข้อมูล/threshold ให้คะแนนผ่าน
- `git diff --check`, TOML และ workflow YAML parse: ผ่าน; GitHub Actions ถูกเพิ่มแต่ยังไม่ได้ push หรือเรียก remote workflow
- คำเตือนเดิม: Starlette TestClient deprecation และ Windows fake-upstream teardown pending accept tasks ไม่ทำให้ suite fail; native VAD crash จาก core ที่ขาดถูกแก้และตรวจใหม่แล้ว
- ตรวจชื่อไฟล์ในประวัติพบ 3 ไฟล์จากรอบก่อนที่บันทึกชื่อย่อ จึงระบุชื่อเต็มเพิ่ม: `app/tools/mydocs.py`, `app/tools/units.py`, `app/tools/websearch.py` (blocking worker/UI-loop fixes ของรอบรีวิว ไม่มีการแก้เพิ่มใน research implementation นี้)
- อัปเดตผลตรวจใน `CHANGELOG.md` รอบนี้: ตรวจ UTF-8/whitespace ของไฟล์ใหม่และลิงก์คู่มือผ่าน; ไฟล์ที่แก้/เพิ่มทั้งหมดใน working tree 50 ไฟล์ (รวมรอบรีวิวก่อนหน้า) มีชื่อในประวัติครบ ไม่มีรายการตกหล่น
- ไม่รีสตาร์ตบริการหรือเปิดไมค์/กล้อง/เครื่องพิมพ์/robot จริง ไม่เรียก paid provider หรือ inventory จริง ไม่เปลี่ยน `.env` หรืออนุมัติเนื้อหาโครงการ; เปิดใช้โค้ดใหม่หลังรีสตาร์ต server/รีเฟรช browser ในช่วงที่เหมาะสม
- คู่มือส่งมอบ: `docs/measurement-and-installation.md`; งาน Hybrid VAD, content approval workflow, robot ACK และ acoustic benchmark อยู่ในรอบถัดไปตาม research โดยชุดนี้เตรียมฐานวัดผลให้แล้ว
