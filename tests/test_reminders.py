"""
Timers and reminders: promises about later.

The three ways "later" breaks are the three sections here — later is
mid-sentence (delivery goes through events.announce), later is after the
line parked (summon), later is after a restart (the file). Plus the boring
CRUD, which is boring only until a cancelled alarm rings anyway.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.config import settings
from app.tools import registry, reminders


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _own_store(tmp_path, monkeypatch):
    from app.tools import load_tools

    load_tools()
    monkeypatch.setattr(settings, "reminders_file", str(tmp_path / "reminders.json"))
    reminders.reset()
    yield
    reminders.reset()


# ==================== CRUD ====================


def test_a_timer_is_recorded_and_persisted():
    out = reminders.set_timer(10, "ต้มไข่")
    assert out["ok"] is True
    on_disk = json.loads(
        (settings.reminders_file and open(settings.reminders_file, encoding="utf-8").read())
    )["items"]
    assert len(on_disk) == 1
    assert on_disk[0]["label"] == "ต้มไข่"
    assert on_disk[0]["status"] == "pending"


def test_a_reminder_takes_an_absolute_time():
    at = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 3600))
    out = reminders.set_reminder("ประชุมทีมขาย", at)
    assert out["ok"] is True and out["at"] == at


def test_a_time_already_gone_is_refused_with_a_question():
    """The model computed a past time — it mis-read "now", or the owner was
    ambiguous. Accepting silently means an alarm that rings instantly,
    which reads as a haunted robot."""
    out = reminders.set_reminder("สาย", "2020-01-01 09:00")
    assert out["ok"] is False
    assert "ถามเจ้าของ" in out["instruction"]


def test_a_misheard_duration_is_bounced_back():
    """"อะไรนะ 50" once became a real print job. Here it becomes a 3am
    alarm, so out-of-range numbers go back for confirmation."""
    out = reminders.set_timer(0)
    assert out["ok"] is False
    for bad in (-5, 100000):
        assert reminders.set_timer(bad)["ok"] is False


def test_cancel_needs_the_right_id():
    item = reminders.set_timer(30, "ซักผ้า")
    assert reminders.cancel_reminder("nope")["ok"] is False
    assert reminders.cancel_reminder(item["id"])["ok"] is True
    assert reminders.list_reminders()["count"] == 0, "cancelled items are not listed"


# ==================== firing ====================


def _due_now(label="ทดสอบ"):
    item = reminders.set_timer(1, label)
    for stored in reminders._load():
        if stored["id"] == item["id"]:
            stored["due"] = time.time() - 1
    reminders._save()
    return item


def test_a_due_timer_goes_through_the_announcement_pipe(monkeypatch):
    """Never send_text — a timer ringing mid-sentence is the barge-in bug
    with a clock attached. The pipe waits for the ear; this test pins the
    route, events' own tests pin the waiting."""
    from app import events

    said = []

    async def fake_announce(text, *, source, summon=False, **kw):
        said.append((text, source, summon))
        return True

    monkeypatch.setattr(events, "announce", fake_announce)
    _due_now("ต้มบะหมี่")
    run(reminders.deliver_due())

    assert len(said) == 1
    text, source, summon = said[0]
    assert "ต้มบะหมี่" in text
    assert source == "reminder"
    assert summon is True, "an alarm must be able to ring a parked line"
    assert reminders._load()[0]["status"] == "done"


def test_an_undeliverable_alarm_retries_then_records_the_miss(monkeypatch):
    """No browser, no session, for ten straight minutes: give up *on the
    record*. The item flips to missed — list_reminders can answer 'ทำไม
    ไม่ปลุก' — instead of pretending it never existed."""
    from app import events

    async def nobody(text, **kw):
        return False

    monkeypatch.setattr(events, "announce", nobody)
    item = _due_now("หายไปเฉยๆ")

    run(reminders.deliver_due())
    assert reminders._load()[0]["status"] == "pending", "one failure is a retry, not a miss"

    for stored in reminders._load():
        if stored["id"] == item["id"]:
            stored["due"] = time.time() - reminders.OVERDUE_GIVE_UP_S - 5
    reminders._save()
    run(reminders.deliver_due())
    assert reminders._load()[0]["status"] == "missed"
    listed = reminders.list_reminders()["items"]
    assert listed and listed[0]["status"] == "missed"


def test_pending_items_survive_a_restart():
    """The file is the memory. A fresh process (reset simulates one) must
    see yesterday's alarm, or a reboot silently eats the 7am wake-up."""
    reminders.set_timer(60, "ข้ามรีสตาร์ต")
    reminders.reset()                      # new process, same file
    assert [i["label"] for i in reminders.pending()] == ["ข้ามรีสตาร์ต"]


# ==================== who gets these tools ====================


def test_reminders_are_opt_in_not_ambient():
    """TOOL_GROUPS blank on the condo profile means "everything" — but
    everything *predating* these groups. The gallery must not sprout alarm
    tools on a git pull; a guest setting the showroom a 6am alarm is
    nobody's feature."""
    from app.tools import _modules_to_load

    assert "app.tools.reminders" not in _modules_to_load(None)
    assert "app.tools.reminders" in _modules_to_load({"smarthome", "reminders"})


def test_emma_gets_reminders_by_default(monkeypatch):
    monkeypatch.setattr(settings, "assistant_profile", "emma")
    monkeypatch.setattr(settings, "tool_groups", "")
    assert "reminders" in settings.enabled_tool_groups()


def test_emma_knows_what_time_it_is():
    """"เตือนพรุ่งนี้เจ็ดโมง" is uncomputable without today's date, and a
    Live model has no clock. The prompt carries one."""
    import time as _t

    from app.prompts import build_instructions

    text = build_instructions("X", profile="emma")
    assert _t.strftime("%Y-%m-%d") in text
