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


def test_saved_places_are_drawn_by_name_and_savable_from_the_page(html):
    """Owner 2026-09-30: "เอาแผนที่โชว์รูมแล้วก็มาร์กตำแหน่งหุ่นแล้วก็ให้มัน
    ขยับ เพราะแอพของที่เขาให้มาดูใช้ยาก" — our page is the vendor app's
    replacement, so it must SHOW every saved spot where it is (place_poses
    from /robot/state) and let the owner save the robot's current position
    under a typed name. Undock/home ride along: a robot on its dock cannot
    walk anywhere until undocked."""
    assert "place_poses" in html
    assert "ctx.fillText(poi.name" in html
    for action in ("'save_poi'", "'undock'", "'home'"):
        assert "action: " + action in html, action
    # the save button sends the TYPED name and refuses an empty one
    assert "const name = $('poiName').value.trim();" in html
    assert "if (!name) {" in html


def test_ray_junk_is_not_drawn_as_floor(html):
    """Measured on this showroom's map (2026-09-30): real floor clusters at
    confidence 127 (~69 m²) while a sparse 10..110 tail is laser rays through
    glass — the white spikes that made the map unreadable (owner:
    "ไม่รู้เรื่อง"). Low-confidence free must render as barely-there, and the
    threshold must be the named measured constant, not a magic number."""
    assert "const FLOOR_CONFIDENT = 112;" in html
    norm = html.replace(" ", "")
    assert "v>=FLOOR_CONFIDENT" in norm
    # the legend explains the colours in the owner's language
    for word in ("พื้นที่เดินได้", "กำแพง", "ยังไม่เคยสำรวจ", "จุดที่บันทึกไว้"):
        assert word in html, word


def test_the_owners_plan_rides_on_a_two_point_calibration(html):
    """Owner 2026-09-30: "อัพผังให้ง่ายกว่าใหม่" — the page can upload the
    showroom's own drawing and pin it to the chassis' world with two matched
    points. The contracts: plan pixels are conjugated (py sign-flipped)
    before the complex similarity or the plan renders MIRRORED; the walls
    layer still draws over the plan so moved furniture shows; the wizard is
    cancellable; and goto still sends the same canvasToWorld metres whether
    the plan or the scan is on screen (one click path, no second transform)."""
    norm = html.replace(" ", "")
    assert "im:-pairs[0].py" in norm and "im:-pairs[1].py" in norm
    assert "function computeCalib(pairs)" in html
    assert "ctx.drawImage(wallsCanvas, 0, 0);" in html
    assert "id=\"planFile\"" in html and 'accept="image/png,image/jpeg"' in html
    assert "'/robot/plan/calibration'" in html
    assert "ยกเลิกการจับคู่" in html
    # one click path: the wizard intercepts, everything else is the old goto
    assert "if (calibWizard) { calibClick(cx, cy); return; }" in html
    assert html.count("= canvasToWorld(cx, cy)") == 2  # goto + wizard scan click, nothing else


def test_localization_off_is_said_with_the_fix_one_press_away(html):
    """Design §4 (S10): drive_route leaves board localization OFF, and the
    server then refuses every planner walk with `relocalize`. The page says
    so and offers the existing /robot/command relocalize — never a goto that
    fails with no reason. Unreadable (null) counts as off: the server's rule."""
    assert "ระบบระบุตำแหน่งปิดอยู่ — กดตั้งตำแหน่งใหม่" in html
    assert 'id="relocBtn"' in html
    assert "action: 'relocalize', mode" in html
    assert "s.localization_enabled !== true" in html
    # A refused goto/home with that code raises the same notice.
    assert html.count("if (r.error === 'relocalize') showReloc(true);") == 2
    # Neither relocalize mode moves the robot: `rotate` is never sent from here.
    assert "'rotate'" not in html


def test_a_fenced_refusal_shows_its_thai_reason(html):
    """run_active / routes_own_the_base / relocalize come back with a Thai
    `hint`; the bare code tells the operator nothing."""
    assert "function why(r) { return (r && (r.hint || r.error))" in html
    assert "'สั่งไม่ได้: ' + why(r)" in html
