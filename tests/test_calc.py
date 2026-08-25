"""
The ROI sheet — money, spoken numbers, and arithmetic, kept apart on purpose.

The model may carry numbers from the guest's mouth to the server and read
the computed results back. It may not do the math, may not supply a missing
number, and may not present the guest's assumptions as the project's.
"""
from __future__ import annotations

import pytest

from app.tools import calc


@pytest.fixture(autouse=True)
def _blank_sheet():
    calc.reset()
    yield
    calc.reset()


def test_the_server_does_the_arithmetic_exactly():
    """Compound growth is exactly the arithmetic a language model gets
    confidently wrong, and a wrong number about money in a warm voice is
    worse than no answer. Pinned to the closed-form values."""
    out = calc.compute({"price_thb": 3_290_000, "monthly_rent_thb": 15_000,
                        "years": 5, "appreciation_pct": 4})
    assert out["rental_yield_pct"] == round(15_000 * 12 / 3_290_000 * 100, 2)
    assert out["future_value_thb"] == round(3_290_000 * 1.04 ** 5, 2)
    assert out["roi_pct"] == round(
        (out["rent_total_thb"] + out["capital_gain_thb"]) / 3_290_000 * 100, 2)


def test_changing_one_number_keeps_the_rest_of_the_sheet():
    """"เปลี่ยนเป็นสิบปี" is one field, not the whole sheet again — the
    screen the owner asked for: numbers changeable at any time."""
    calc.calc_roi(price_thb=3_000_000, monthly_rent_thb=12_000,
                  years=5, appreciation_pct=3)
    out = calc.calc_roi(years=10)
    assert out["inputs"]["price_thb"] == 3_000_000
    assert out["inputs"]["years"] == 10
    assert out["results"]["rent_total_thb"] == 12_000 * 12 * 10


def test_no_default_is_invented_for_a_missing_number():
    """A sheet with no holding period gets a yield and nothing else. A
    default period would be this module quietly inventing the one kind of
    number it exists to refuse."""
    out = calc.calc_roi(price_thb=2_000_000, monthly_rent_thb=10_000)
    assert "rental_yield_pct" in out["results"]
    assert "roi_pct" not in out["results"], "no years, no projection"

    calc.reset()
    missing = calc.calc_roi(years=5)
    assert missing["ok"] is False and missing["error"] == "no price"
    assert "ห้ามเดาราคาเอง" in missing["instruction"]


def test_the_result_says_whose_numbers_these_are_every_time():
    """Every call, not once at the start: an instruction that must be
    remembered across turns is one that gets dropped, and what would be
    dropped here is "these are the guest's own assumptions"."""
    out = calc.calc_roi(price_thb=1_000_000, monthly_rent_thb=5_000, years=3,
                        appreciation_pct=2)
    assert "ตัวเลขสมมติของลูกค้า" in out["instruction"]
    assert "ห้ามคำนวณหรือปัดตัวเลขเอง" in out["instruction"]


def test_the_model_is_told_to_confirm_heard_numbers_first():
    """"อะไรนะ 50" is in a real transcript. The description names it, and
    quotes what to do — the only phrasing this project has found to work."""
    from app.tools import load_tools

    entry = next(t for t in load_tools() if t.name == "calc_roi")
    assert "ทวนตัวเลขที่ได้ยิน" in entry.description
    assert "อะไรนะ 50" in entry.description
    assert "ห้ามเดา yield" in entry.description


def test_a_new_session_starts_with_a_blank_sheet():
    """The previous guest's budget is not the next guest's business — the
    transcript rule, applied to numbers, which are more personal still."""
    import inspect

    from app import session

    src = inspect.getsource(session.handle_connection)
    block = src.split("slides.reset_state()", 1)[1].split("VoiceSession(", 1)[0]
    assert "_calc.reset()" in block


def test_the_screen_shows_the_inputs_as_prominently_as_the_results():
    """The card is the confirmation: the guest's own spoken numbers are on
    it, so "50" appearing when they said 15 is caught by the one person who
    can correct it. And the banner marks whose numbers they are."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "client" / "index.html").read_text(
        encoding="utf-8")
    body = js[js.index("function showCalc(r)"):]
    body = body[:body.index("\n//: Put a framed page")]
    assert "input_labels" in body, "the heard numbers must be on screen"
    assert "คำนวณจากตัวเลขสมมติของลูกค้า" in body

    res = js[js.index("case 'tool_result': {"):]
    res = res[:res.index(chr(10) + "    }")]
    assert "showCalc(r)" in res
