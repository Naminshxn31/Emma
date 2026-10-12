"""Review #18: a place named in a sentence that refuses the walk, or only asks
where the place is, must not authorise a walk.

`heard.declines_or_asks_where` is the check; `robot_nav._heard_place` is the
caller (it refuses before `robot_places.match` when this is True). Measured
with the real tokenizer: before it, "อย่าไปห้องประชุมใหญ่", "ไม่ต้องไป
ห้องประชุม" and "ห้องประชุมใหญ่อยู่ไหน" all named big_room and passed.
"""
from __future__ import annotations

import pytest

from app import heard


@pytest.mark.parametrize("text", [
    "อย่าไปห้องประชุมใหญ่",
    "ไม่ต้องไปห้องประชุม",
    "ไม่ไปแล้ว ห้องประชุมใหญ่",
    "ห้องประชุมใหญ่อยู่ไหน",
    "ห้องประชุมใหญ่ไปทางไหน",          # the question, though it contains ไป
    "where is the big meeting room",
    "don't take me to the big meeting room",
    # Integrator's check: questions about the place, and ไม่อยาก.
    "ไม่อยากไปห้องประชุมใหญ่",
    "ห้องประชุมใหญ่ไปยังไง",
    "ห้องประชุมใหญ่ไกลไหม",
    "ห้องประชุมใหญ่มีกี่ที่นั่ง",
    "Is the big meeting room far?",
])
def test_refusals_and_where_questions_are_not_requests(text):
    assert heard.declines_or_asks_where(text)


@pytest.mark.parametrize("text", [
    "พาไปห้องประชุมใหญ่",
    "take me to the big meeting room",
    "ไปห้องประชุมใหญ่ได้ไหม",          # ไหม asks permission, not the way
    "กลับแท่นชาร์จ",
    "พาไปห้องประชุมใหญ่หน่อย อยู่ไหนนะ",   # asked to be taken: the where is secondary
    "",
])
def test_requests_to_go_pass(text):
    assert not heard.declines_or_asks_where(text)


def test_whole_words_only():
    """Bare ไม่ is not a decline word: "ไม่ไกล" (not far) and the particle
    ใช่ไหม are ordinary parts of a request."""
    assert not heard.declines_or_asks_where("พาไปห้องประชุมใหญ่ ไม่ไกลใช่ไหม")


# ---- review 2 #11: stop / cancel / "not that one" / past / yes-no ----

#: Every sentence review 2 probed with the real tokenizer and floor2.json,
#: each of which named a place (or the dock) and passed `_heard_place`.
#: Stop and cancel first: the guard exists for the model that reads them as
#: a go.
NOT_A_REQUEST = [
    "หยุดไปห้องประชุมใหญ่", "ยกเลิกไปห้องประชุมใหญ่", "เลิกไปห้องประชุมใหญ่",
    "cancel the big meeting room", "stop going to the big meeting room",
    "never mind the big meeting room", "ไปห้องประชุมใหญ่ทีหลัง", "เดี๋ยวค่อยกลับแท่นชาร์จ",
    "ไม่ใช่ห้องประชุมใหญ่", "ยังไม่กลับแท่นชาร์จ", "ไม่กลับแท่นชาร์จ",
    "ไม่ต้องการไปห้องประชุมใหญ่",
    # the past: news, not a request -- even with a take-word
    "เคยไปห้องประชุมใหญ่ไหม", "ผมเพิ่งไปห้องประชุมใหญ่มา", "ผมเพิ่งไปห้องประชุมใหญ่มาครับ",
    "เมื่อกี้ไปห้องประชุมมา", "เมื่อกี๊ไปห้องประชุมใหญ่มา", "ไปห้องประชุมใหญ่มาแล้ว",
    "เคยพาลูกค้าไปห้องประชุมใหญ่ไหม",
    "I just came from the big meeting room", "Have you been to the big meeting room?",
    "I went to the big meeting room",
    # yes/no questions about the place or the robot
    "แบตหมดหรือยัง ต้องกลับแท่นไหม", "ห้องประชุมใหญ่สวยไหม", "ถึงห้องประชุมใหญ่หรือยัง",
    "นี่ห้องประชุมใหญ่ใช่ไหม", "Is the big meeting room nice?",
    "did you go to the big meeting room",
]

#: ...and what a guest who wants to go actually says, which must still walk:
#: polite "ได้ไหม", "shall we" กันไหม, "go and come back" (กลับมา is one word).
STILL_A_REQUEST = [
    "พาไปห้องประชุมใหญ่หน่อย", "ไปห้องประชุมใหญ่ได้ไหมครับ", "ไปห้องประชุมใหญ่กันไหม",
    "ไปห้องประชุมใหญ่กันมั้ยคะ", "ไปห้องประชุมใหญ่เลย", "อยากไปห้องประชุมใหญ่",
    "กลับแท่นชาร์จได้แล้ว", "กลับแท่นชาร์จได้ไหม", "ไปห้องประชุมใหญ่แล้วกลับมา",
    "ช่วยพาไปห้องประชุมใหญ่หน่อยค่ะ", "Can you take me to the big meeting room?",
    "Could you go back to the charging dock?", "go to the big meeting room", "ห้องประชุมใหญ่",
]


@pytest.mark.parametrize("text", NOT_A_REQUEST)
def test_stop_cancel_past_and_yes_no_are_not_requests(text):
    assert heard.declines_or_asks_where(text)


@pytest.mark.parametrize("text", STILL_A_REQUEST)
def test_the_requests_people_say_still_pass(text):
    assert not heard.declines_or_asks_where(text)


@pytest.fixture
def places(tmp_path):
    """floor2's two places, with the aliases test_robot_nav_voice uses
    (ห้องประชุม alone names big_room there — the stricter table). Written
    per test: conftest keeps the tracked floor plan out of the suite."""
    import json

    from app import robot_places

    data = {
        "format": 1, "route": "data/robot/routes/floor2_bigroom_roundtrip.json",
        "route_sha256": "ab" * 32,
        "places": [
            {"id": "dock", "name_th": "แท่นชาร์จ", "name_en": "charging dock",
             "aliases_th": ["ที่ชาร์จ", "แท่น", "จุดจอด"], "aliases_en": ["dock", "charger"],
             "kind": "dock"},
            {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
             "aliases_th": ["ห้องประชุม"], "aliases_en": ["meeting room"], "kind": "room"},
        ],
        "legs": [
            {"from": "dock", "to": "big_room", "from_step": 1, "to_step": 10,
             "min_battery": 30, "verified": True, "est_s": None, "doors": [], "note": ""},
            {"from": "big_room", "to": "dock", "from_step": 10, "to_step": 20,
             "min_battery": 10, "verified": True, "est_s": None, "doors": [], "note": ""},
        ],
    }
    path = tmp_path / "floor2.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return robot_places.load(path)


def _guard(places, text: str, place_id: str, *, dock: bool) -> bool:
    """robot_nav._heard_place itself, on a session holding one fresh caption
    -- the layer the guest meets, dock branch included."""
    from app import robot_nav

    session = object()
    heard.record(text, session)
    try:
        verdict = robot_nav._heard_place(session, places.place(place_id), places, dock=dock)
    finally:
        heard.forget(session)
    return bool(verdict[0] if isinstance(verdict, tuple) else verdict)


@pytest.mark.parametrize("text", NOT_A_REQUEST)
def test_the_place_guard_walks_nowhere_on_them(places, text):
    """Both places, both branches: the dock branch ("กลับ" + "แท่น") is where
    "ยังไม่กลับแท่นชาร์จ" used to pass, since only ไม่ไป was listed."""
    for place_id, dock in (("big_room", False), ("dock", True), ("dock", False)):
        assert not _guard(places, text, place_id, dock=dock), (place_id, dock)


@pytest.mark.parametrize("text,place_id,dock", [
    ("พาไปห้องประชุมใหญ่หน่อย", "big_room", False),
    ("ไปห้องประชุมใหญ่กันไหม", "big_room", False),
    ("Take me to the big meeting room", "big_room", False),
    ("กลับแท่นชาร์จได้แล้ว", "dock", True),
    ("กลับแท่นได้ไหม", "dock", True),
])
def test_the_place_guard_still_walks_on_requests(places, text, place_id, dock):
    assert _guard(places, text, place_id, dock=dock)
