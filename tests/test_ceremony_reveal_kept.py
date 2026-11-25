"""The room screen's debut after its hit is not cut by the robot's own trouble (owner 23 Nov on the robot).

22:26:03 the ceremony's opening was pressed, the hit came at 22:26:10, the walk out stopped on a pose jump at
22:26:16 (0.24 of 1.00 m) and card 1's end took the screen's debut down mid-wave ("debut_down", before_hit false):
the wave vanished and the held logo came up -- "มีซีนตอนเปลี่ยนอยู่ก็หายไปแล้วขึ้นโลโก้ มันดูไม่สมูทเลย ปรับเลย".
After the hit only a person's / staff's end takes it down (event_tour.SCREEN_DOWN_AFTER_HIT); before the hit every
end still does (no walk out without the screen's hit).
"""
from __future__ import annotations

import pytest

from app import ceremony_reveal, event_tour


@pytest.fixture
def opening(monkeypatch):
    """Card 1's ceremony opening with a screen run, and what the reveal module is asked."""
    event_tour.reset()
    event_tour.STATE.update(owner="ceremony", reveal="debut-1", phase="walk_out")
    aborted = []
    where = {"now": None}
    monkeypatch.setattr(ceremony_reveal, "abort", lambda why="aborted": aborted.append(why))
    monkeypatch.setattr(ceremony_reveal, "where", lambda: where["now"])
    monkeypatch.setattr(ceremony_reveal, "running", lambda: where["now"] is not None)
    yield aborted, where
    event_tour.reset()


@pytest.mark.parametrize("why", ["debut_cut", "debut_short", "not_still", "leg_cut", "leg_refused", "wheels_still",
                                 "handshake_failed", "hand_down", "error"])
def test_after_the_hit_the_robots_own_trouble_leaves_the_screen_playing(opening, why):
    aborted, where = opening
    where["now"] = {"run": "debut-1", "phase": "impact", "impact_in": -6.0, "ends_in": 9.0}
    event_tour._abort_reveal(why)
    assert aborted == []


@pytest.mark.parametrize("why", ["staff", "voice", "desk", "app_stop", "replaced", "order_reset", "ceremony_app_off"])
def test_after_the_hit_a_persons_stop_still_takes_it_down(opening, why):
    aborted, where = opening
    where["now"] = {"run": "debut-1", "phase": "impact", "impact_in": -6.0, "ends_in": 9.0}
    event_tour._abort_reveal(why)
    assert aborted == [why]


@pytest.mark.parametrize("phase", ["build", "dark"])
def test_before_the_hit_every_end_takes_it_down(opening, phase):
    aborted, where = opening
    where["now"] = {"run": "debut-1", "phase": phase, "impact_in": 2.0, "ends_in": 17.0}
    event_tour._abort_reveal("debut_cut")
    assert aborted == ["debut_cut"]


def test_the_end_of_card_1_after_the_hit_keeps_the_screen(opening):
    """Through `_end` itself (what ran on the robot): a cut walk out after the hit."""
    aborted, where = opening
    where["now"] = {"run": "debut-1", "phase": "impact", "impact_in": -6.0, "ends_in": 9.0}
    event_tour.STATE.update(seq=3, ended=None, plan=[], until=1e18)
    event_tour._end(3, "debut_cut", detail_th="หุ่นไม่แน่ใจตำแหน่งตัวเอง")
    assert event_tour.STATE["ended"] == "debut_cut" and aborted == []
