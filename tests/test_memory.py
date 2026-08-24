"""
Emma's memory: what "จำไว้ด้วย" stores, and what "จำได้ไหม" is allowed to say.

The dangerous failure here isn't losing a fact — it's inventing one. A
missing memory costs an apology; a fabricated memory about a real person
costs the trust the whole assistant runs on. So half these tests are about
what happens when the store is empty, wrong, or asked about the gallery.
"""
from __future__ import annotations

import json

import pytest

from app import memory_store
from app.config import settings
from app.prompts import build_instructions


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_file", str(tmp_path / "memory.json"))
    memory_store.reset()
    yield
    memory_store.reset()


# ==================== storing ====================


def test_a_fact_is_stored_and_survives_a_restart():
    from app.tools.memory import remember

    out = remember("เจ้าของชอบกาแฟลาเต้ไม่ใส่น้ำตาล")
    assert out["ok"] is True

    memory_store.reset()                    # a new process, same file
    assert [f["text"] for f in memory_store.facts()] == [
        "เจ้าของชอบกาแฟลาเต้ไม่ใส่น้ำตาล"
    ]


def test_the_same_sentence_is_not_stored_twice():
    """"จำไว้ว่า..." repeated (people do) must not pad the prompt with
    copies — every duplicated line taxes every future turn's latency."""
    from app.tools.memory import remember

    a = remember("แมวชื่อส้มโอ")
    b = remember("แมวชื่อส้มโอ")
    assert a["id"] == b["id"]
    assert len(memory_store.facts()) == 1


def test_forgetting_removes_from_store_and_prompt():
    from app.tools.memory import forget_memory, remember

    item = remember("ข้อมูลที่จำผิด")
    assert "ข้อมูลที่จำผิด" in build_instructions("X", profile="emma")

    out = forget_memory(item["id"])
    assert out["ok"] is True and out["forgot"] == "ข้อมูลที่จำผิด"
    assert "ข้อมูลที่จำผิด" not in build_instructions("X", profile="emma")


def test_forgetting_a_bad_id_says_so():
    from app.tools.memory import forget_memory

    out = forget_memory("nope")
    assert out["ok"] is False
    assert "list_memories" in out["instruction"]


# ==================== the prompt ====================


def test_stored_facts_ride_in_every_emma_session():
    from app.tools.memory import remember

    remember("ประชุมทีมทุกวันจันทร์เช้า")
    text = build_instructions("X", profile="emma")
    assert "ประชุมทีมทุกวันจันทร์เช้า" in text
    assert "ห้ามแต่งความทรงจำ" in text


def test_an_empty_memory_is_declared_not_omitted():
    """With no section at all, "จำได้ไหมว่า..." invites a guess. The prompt
    must give the model something true to say instead."""
    text = build_instructions("X", profile="emma")
    assert "ยังไม่มีอะไรถูกบันทึกไว้" in text


def test_the_gallery_never_sees_the_owners_memory():
    """The receptionist talks to strangers all day. One leaked line —
    "เจ้าของชอบกาแฟลาเต้" recited to a customer — is a privacy failure the
    profile split exists to prevent."""
    from app.tools.memory import remember

    remember("เรื่องส่วนตัวมากๆ ของเจ้าของ")
    assert "เรื่องส่วนตัวมากๆ" not in build_instructions("Embassy World")


def test_the_prompt_carries_the_newest_facts_when_over_the_cap(monkeypatch):
    monkeypatch.setattr(memory_store, "PROMPT_FACT_LIMIT", 3)
    from app.tools.memory import remember

    for i in range(5):
        remember("เรื่องที่ %d" % i)
    block = memory_store.prompt_block()
    assert "เรื่องที่ 4" in block and "เรื่องที่ 0" not in block
    # Older facts are not deleted — only left out of the prompt.
    assert len(memory_store.facts()) == 5


# ==================== who gets these tools ====================


def test_memory_tools_are_opt_in_not_ambient():
    """Same fence as reminders: the gallery (TOOL_GROUPS blank = everything
    that predates these groups) must not gain a `remember` tool — a guest
    telling the showroom robot "จำไว้ว่า..." writes into the owner's file."""
    from app.tools import _modules_to_load

    assert "app.tools.memory" not in _modules_to_load(None)
    assert "app.tools.memory" in _modules_to_load({"memory"})


def test_emma_gets_memory_by_default(monkeypatch):
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert "memory" in settings.enabled_tool_groups()


def test_the_files_are_kept_apart_from_the_customer_logs():
    """Opposite lifecycles: logs are strangers' speech deleted at 30 days,
    memory is the owner's data kept until told otherwise. One path shared
    and a retention sweep eats someone's memory — or nobody dares sweep."""
    assert "logs" not in settings.memory_file
    assert "logs" not in settings.reminders_file
