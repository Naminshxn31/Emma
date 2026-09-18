"""
Searching the owner's documents: the thin index over retrieval.py.

The Thai lessons are inherited, not retested in depth — `test_retrieval.py`
owns the tokenizer. What this file owns is the folder contract (drop a file
in, it's searchable; empty folder says so), the found-gate (rank is not an
answer), and the fence (gallery never gets the tool).
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.tools import mydocs


@pytest.fixture(autouse=True)
def _own_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "personal_docs_dir", str(tmp_path))
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    mydocs.reset()
    yield tmp_path
    mydocs.reset()


def _write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def test_a_thai_question_finds_the_right_file(_own_folder):
    _write(_own_folder, "รถ.txt",
           "ประกันรถยนต์ต่ออายุทุกเดือนตุลาคม กับบริษัทวิริยะ เบี้ยปีละสองหมื่น")
    _write(_own_folder, "บ้าน.txt",
           "ค่าส่วนกลางหมู่บ้านจ่ายทุกหกเดือน งวดละหนึ่งหมื่นสองพันบาท")

    out = mydocs.search_my_documents("ประกันรถต่อเมื่อไหร่")
    assert out["found"] is True
    assert out["results"][0]["file"] == "รถ.txt"
    assert "ตุลาคม" in out["results"][0]["text"]


def test_the_library_can_be_scoped_to_one_project(_own_folder, monkeypatch):
    """MYDOCS_INCLUDE fences the sales host to one project's files, so an
    Embassy Life brochure sitting in the same corpus cannot answer a question
    about Embassy World (measured 2026-09-14: it did, and the model invented
    specifics on top). The filter is on the path relative to the folder;
    empty means every file, which conftest pins for the rest of the suite."""
    (_own_folder / "embassy-world").mkdir()
    (_own_folder / "embassy-life").mkdir()
    _write(_own_folder / "embassy-world", "world.md",
           "ที่นี่มีลากูนและสวนสำหรับครอบครัว")
    _write(_own_folder / "embassy-life", "life.md",
           "ที่นี่มีจากุซซี่ Crystal Maze สุดหรูริมทะเล")

    monkeypatch.setattr(settings, "mydocs_include", "embassy-world")
    monkeypatch.setattr(settings, "assistant_profile", "condo")
    mydocs.reset()
    scoped = mydocs.search_my_documents("จากุซซี่ Crystal Maze")
    assert not any("life.md" in r["file"] for r in scoped.get("results", [])), \
        "Embassy Life doc leaked past the scope"

    monkeypatch.setattr(settings, "mydocs_include", "")
    mydocs.reset()
    still_scoped = mydocs.search_my_documents("จากุซซี่ Crystal Maze")
    assert not any("life.md" in r["file"] for r in still_scoped.get("results", [])), \
        "empty include cannot widen a gallery session"


def test_nothing_relevant_is_a_refusal_not_the_closest_chunk(_own_folder):
    """Rank only says "closest there is"; for a question about nothing in
    the corpus, something is always closest. The coverage gate is what
    keeps a pension question from being answered with a car policy."""
    _write(_own_folder, "รถ.txt", "ประกันรถยนต์ต่ออายุทุกเดือนตุลาคม")
    out = mydocs.search_my_documents("สูตรทำขนมเค้กช็อกโกแลต")
    assert out["found"] is False
    assert "ห้ามเดา" in out["instruction"]


def test_an_empty_folder_explains_itself(_own_folder):
    """The condo_facts lesson: a feature whose data nobody filled must say
    so and say where — not fail vaguely until someone reads the code."""
    out = mydocs.search_my_documents("อะไรก็ได้")
    assert out["found"] is False
    assert str(settings.personal_docs_dir) in out["instruction"]


def test_a_new_file_is_searchable_without_a_restart(_own_folder):
    """The fingerprint is names+mtimes+sizes: drop a file in, ask again,
    it's there. An index that needs a restart teaches the owner the
    feature is broken the first time they use it."""
    assert mydocs.search_my_documents("รหัสไวไฟ")["found"] is False
    _write(_own_folder, "บ้าน.txt", "รหัสไวไฟที่บ้านคือ moobaan1234")
    out = mydocs.search_my_documents("รหัสไวไฟ")
    assert out["found"] is True
    assert "moobaan1234" in out["results"][0]["text"]


def test_long_documents_are_chunked_but_paragraphs_stay_whole(_own_folder):
    para = "เรื่องที่หนึ่งยาวมาก " * 30           # ~600 chars
    other = "นัดหมอฟันวันที่สิบห้า มกราคม ที่คลินิกใกล้บ้าน"
    _write(_own_folder, "โน้ต.txt", para + "\n\n" + other)
    out = mydocs.search_my_documents("นัดหมอฟันวันไหน")
    assert out["found"] is True
    assert "มกราคม" in out["results"][0]["text"]


def test_the_gallery_never_gets_this_tool():
    """The corpus is the owner's private files. A customer in the showroom
    asking the receptionist to read them is the leak the opt-in fence
    exists to stop."""
    from app.tools import _modules_to_load

    assert "app.tools.mydocs" not in _modules_to_load(None)
    assert "app.tools.mydocs" in _modules_to_load({"mydocs"})


def test_emma_gets_it_by_default(monkeypatch):
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert "mydocs" in settings.enabled_tool_groups()


def test_the_ratcha_akan_bug_cannot_return_through_the_new_gate(_own_folder):
    """ราคา matched อาคาร twice in this project's history, both times via
    character runs against raw text (they share าคา). The word-in-token
    rule must not reopen that door: the full query word has to sit whole
    inside one tokenized word, and ราคา does not sit inside อาคาร."""
    from app.tools.mydocs import _coverage

    assert _coverage(["ราคา"], {"อาคาร", "สวยงาม"}) == 0.0
    assert _coverage(["รถ"], {"รถยนต์"}) == 1.0     # the case the rule is for

    _write(_own_folder, "ตึก.txt", "อาคารจอดรถสร้างเสร็จปีหน้า มีสวนบนดาดฟ้า")
    out = mydocs.search_my_documents("ราคาทองวันนี้")
    assert out["found"] is False, "ราคา found its way into อาคาร again"


def test_emma_is_told_to_search_our_docs_before_answering(_own_folder):
    """The real session, 2026-08-22: asked about Empire Group with 25 pages
    of the company's own site sitting in the corpus, Emma answered from
    world knowledge and invented projects that do not exist ("Embassy
    Gallery สาทร"). Told "ค้นหาสิ", she searched and answered correctly —
    the tool worked; the reaching-for-it didn't. The fix is the documented
    house rule: the gate lives in the tool description and the prompt, and
    it cites the real failure."""
    from app.prompts import build_instructions

    _write(_own_folder, "web-empire-group.txt", "Empire Group คือผู้พัฒนา")
    text = build_instructions("X", profile="emma")
    assert "search_my_documents ก่อนตอบเสมอ" in text
    assert "empire-group" in text          # the topic list is real, not prose
    assert "ค่ะ/คะ" in text                # and she stops flip-flopping ครับ/ค่ะ

    from app.tools.mydocs import search_my_documents
    desc = search_my_documents._tool_description if hasattr(
        search_my_documents, "_tool_description") else None
    # The registry owns the description; read it from there.
    from app.tools import registry
    entry = registry.get("search_my_documents")
    assert "ห้ามตอบจากความรู้ทั่วไปก่อนค้น" in entry.description
    assert "ไม่มีจริง" in entry.description   # the quoted real incident


# ============ the pricing gate reaches the web library ============


def test_a_money_question_never_gets_a_marketing_number(_own_folder):
    """The library is marketing copy, and marketing copy carries numbers
    nobody signed — "yields 7-10%" sits in the pre-sale articles today. The
    guard was on search_condo_info only; opened to the gallery as-is, a
    guest asking about returns would have been quoted an unsigned figure
    with the robot's confidence. Same gate, same module, so the two search
    surfaces cannot drift apart."""
    _write(_own_folder, "presale.txt",
           "Condo pre-sale gives higher long-term ROI, typical yields 7-10% "
           "และการลงทุนคอนโดพัทยาให้ผลตอบแทนดี ราคาเริ่มต้นน่าสนใจ")

    for q in ("ผลตอบแทนเท่าไหร่", "ราคาเท่าไหร่", "ผ่อนเดือนละเท่าไร",
              "how much does it cost", "价格是多少"):
        out = mydocs.search_my_documents(q)
        assert out["found"] is False, q
        assert out.get("commercial_question") is True, q
        assert "ฝ่ายขาย" in out["instruction"], q
        assert not out["results"], "no article text may ride along"

    # And the questions the library exists for still work.
    ok = mydocs.search_my_documents("ทำไมควรซื้อช่วง pre-sale")
    assert ok["found"] is True


def test_ordinary_questions_are_not_caught_by_the_gate(_own_folder):
    """The guard's own history: the substring version refused "เป็นยังไงบ้าง"
    because of งบ. Moving it must not resurrect that."""
    _write(_own_folder, "gym.txt", "ฟิตเนสของโครงการเปิดตลอดและผ่อนคลายด้วยซาวน่า")
    out = mydocs.search_my_documents("ฟิตเนสเป็นยังไงบ้าง")
    assert out.get("commercial_question") is not True


# ============ subfolders: the rendered corpus lives one level down ============


def test_a_file_in_a_subfolder_is_searchable_without_a_restart(_own_folder):
    """corpus_to_docs.py renders 54 documents into personal-docs/corpus/ —
    and iterdir() never saw them. Search still said found=True from the
    top-level web pages, so nothing looked broken; the biggest part of the
    library was simply absent. Both halves must recurse: the index (or the
    file is never read) and the fingerprint (or dropping a file into the
    subfolder never triggers a rebuild — this test writes it *after* the
    first search so a stale cache fails here)."""
    _write(_own_folder, "web-note.txt", "เรื่องอื่นที่ไม่เกี่ยวข้องกันเลย")
    assert mydocs.search_my_documents("สระว่ายน้ำถ้ำอยู่ชั้นไหน")["found"] is False

    sub = _own_folder / "corpus"
    sub.mkdir()
    _write(sub, "embassy-life.md",
           "สระว่ายน้ำถ้ำ Cave Pool ของ Embassy Life อยู่ชั้นสาม เปิดทุกวัน")
    out = mydocs.search_my_documents("สระว่ายน้ำถ้ำอยู่ชั้นไหน")
    assert out["found"] is True
    assert out["results"][0]["file"] == "corpus/embassy-life.md", \
        "the file id must say which collection it came from"


def test_the_prompt_names_the_collection_not_its_54_filenames(_own_folder):
    """The doc list in the prompt is capped at 15 names, sorted — 54 slug
    filenames would either flood the budget or hide behind the cap. A
    subfolder appears as one summary entry, first, and its long filenames
    stay out of the prompt."""
    from app.prompts import build_instructions

    sub = _own_folder / "corpus"
    sub.mkdir()
    _write(sub, "EMBASSY-LIFE-Brochure-E-Brochure-EMBASSY-LIFE-pdf.md", "ข้อความ")
    _write(sub, "Empire-Information-Company-Profile-pdf.md", "ข้อความ")

    text = build_instructions("X", profile="emma")
    assert "corpus/ (2 ไฟล์)" in text
    assert "EMBASSY-LIFE-Brochure" not in text
