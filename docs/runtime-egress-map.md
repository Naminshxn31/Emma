# M0.3c — egress map และขอบเขตการตรวจ

เส้นทาง customer-facing ที่ตรวจจากโค้ด (ไม่ใช่การรับรองอุปกรณ์จริง):

| ปลายทาง | ทางเข้า | gate ล่าสุดก่อนออก | กรณี fail closed |
| --- | --- | --- | --- |
| Gemini / OpenAI tool response | `registry.dispatch_all` → provider serializer | source-specific adapter และ `registry._dispatch` ตรวจ foreign project ทุก tool; exception คืนรหัสทั่วไป | ไม่ส่ง raw exception, foreign payload หรือ printer diagnostic |
| prompt / retrieval / reranker | `load_facts`, `search_condo_info`, `SlideSearch` | `project_facts` ต้องมี source/project/approval; approved slide text กรองก่อน index; routing hint ไม่ป้อน customer-text ranker | source ผิด, draft, source หาย หรือ parse fail ได้ fallback ที่ไม่มี fact |
| conversation UI / display / Canva | session tool event, `display._reveal`, `display.broadcast`, direct `canva_display.goto` | `slides.current_slide` และ `slides.display_payload` resolve catalog+manifest ปัจจุบันซ้ำหลัง audio delay หรือ direct adapter call; upstream error ส่งข้อความกลาง | foreign/asset ถูกถอน ไม่ broadcast หรือสั่ง Canva; raw dict และ provider error body ไม่ออก UI |
| HTTP image | `SlideAssets.get_response` | token (ถ้าตั้งไว้), image ต้องอยู่ใน catalog และ manifest ของ project | URL เดาหรือไฟล์รูปเก่าที่ไม่ได้ลงทะเบียนได้ 404 |
| unit card / floor overlay | `units` adapters | inventory project/freshness/disclosure และ plan manifest; local/live caches ผูก project | ไม่คืน field/overlay ที่ไม่ผ่าน และไม่ reuse cache คนละ project |
| print | `documents._print_document` | entire-document approval ก่อนค้นชื่อและส่งงาน | draft ไม่เข้า queue; printer failure ไม่คืน stderr/path ใน provider หรือ turn log |
| optional web/personal/computer | `registry.allowed` | condo profile ปิด `websearch`, `computer`, `memory`, `mydocs` แม้ `TOOL_GROUPS` ระบุ; direct `search_web` ปิดด้วย | ไม่ใช้ผลค้นหรือไฟล์ส่วนตัวเป็น project fact ผ่าน env flag |

Test กลาง `tests/test_runtime_egress.py` ตรวจ serialized Gemini/OpenAI output, UI event, Canva command, upstream error body, project switch ของ cache และ config opt-in. Test อื่นตรวจ HTTP route, print diagnostics และ source-specific gate. `logs` ระดับ operator เป็นอีกระบบที่ต้องจำกัดสิทธิ์เข้าถึง; tool exception ที่ sweep นี้พบถูกบันทึกเพียงชนิด exception ไม่ใช่ข้อความที่อาจมี source text/path.

## Production validation matrix (ยังไม่ได้ทำใน M0.3c)

| ระบบจริง | หลักฐานที่ต้องเก็บก่อน deploy | รอบ |
| --- | --- | --- |
| Canva remote | design/page ตรง measured map หลังแก้ deck, ไม่มี metadata draft หลุด และ remote timeout ไม่เกิด action ผิด | live smoke |
| printer | PDF bundle/approval ถูกต้อง, queue รับงานจริง, paper/error ไม่ทำให้ Emma อ้างว่าสำเร็จ | live smoke |
| Gemini/OpenAI live | capture request tool response และ browser event แบบ redacted; blocked sentinel ไม่ปรากฏ | live smoke |
| display | byte ที่ render ตรง asset ที่ server ตั้งใจ และ ACK ผูก command/sha256 | M1 |

ข้อจำกัด: `/slides` ตรวจ membership/catalog+manifest ก่อน serve แต่ยังไม่มีหลักฐาน end-to-end ว่าจอ render byte เดียวกับ asset ที่ตรวจ; นั่นเป็น M1. การเปิด approved source ใหม่, optional external tool หรือ project ใหม่ต้องมี policy และ regression test เพิ่มก่อนอนุญาต ไม่ถือว่าการมีไฟล์หรือการเปิด flag คือ approval.
