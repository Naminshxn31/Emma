"""The map viewer + click-to-go — client/robot-map.html.

Source-level, like the joystick and control pages. The two things a map click
can get wrong are silent and dangerous: the y-axis flip (grid row 0 is the
lowest world-y, canvas row 0 is the top) and the cell-centre coordinate
transform. If either is backwards, "go here" lands mirrored or shifted, so
these pin both formulas to the contract agreed with the chassis owner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "client" / "robot-map.html")


@pytest.fixture(scope="module")
def html() -> str:
    return SRC.read_text(encoding="utf-8")


def test_it_uses_the_agreed_endpoints(html):
    assert "/robot/map" in html and "/robot/state" in html and "/robot/command" in html
    assert "cells_b64" in html


def test_the_grid_is_drawn_bottom_up(html):
    # Grid row 0 is the lowest world-y; canvas row 0 is the top. The draw loop
    # must flip: canvasRow = height - 1 - row.
    assert "map.height - 1 - row" in html


def test_the_coordinate_transform_matches_the_contract(html):
    # world = origin + (index + 0.5) * resolution, both axes, and the inverse
    # on a click.
    assert "map.origin_x + (col + 0.5) * map.resolution" in html
    assert "map.origin_y + (row + 0.5) * map.resolution" in html
    # click -> world flips y back the same way it was drawn.
    assert "map.height - 1 - cy" in html


def test_occupied_cells_are_not_drawn_as_open_floor(html):
    """Byte semantics measured on the base (2026-09-12): 0 = unknown,
    1..127 = free (127 the most confident), 128..255 = occupied. A plain
    grayscale (pixel = v) drew walls near-white and floor mid-grey — the
    inversion that makes click-to-go read a wall as open floor. The draw must
    branch on the value at the measured threshold, not copy it straight."""
    norm = html.replace(" ", "")
    assert "img.data[o]=img.data[o+1]=img.data[o+2]=v;" not in norm, \
        "plain grayscale is the inverted-map bug"
    assert "v===0" in norm and "v<=127" in norm


def test_a_click_sends_metres_not_a_distance(html):
    # The goto carries {action:'goto', x, y}; yaw and step distance stay on
    # the server (the contract: place beats x,y, yaw is the robot's own).
    assert "action: 'goto', x: target.x, y: target.y" in html
    assert "yaw" not in html.split("action: 'goto'", 1)[1][:200], \
        "the client must not send a yaw with goto"


def test_stop_is_always_available_and_lock_is_shown(html):
    assert "action: 'stop'" in html
    assert "motion_enabled" in html
    assert "ล็อก" in html and "ปลดล็อก" in html
    # A locked base disables 'go' but never 'stop'.
    assert "$('go').disabled = !motionEnabled" in html or \
           "$('go').disabled = !(motionEnabled)" in html


def test_a_missing_map_or_base_is_said_not_hidden(html):
    # Errors from /robot/map surface to the human instead of a blank canvas.
    assert "โหลดแผนที่จากฐานไม่ได้" in html or "โหลดแผนที่ไม่ได้" in html
