"""The driving cockpit — client/robot-joystick.html (served at /drive).

One page: camera, lidar radar, mic meter, a drive pad and the click-to-go map.
The danger in a joystick is what happens when a press does not end cleanly, so
these hold the drive pad to the safety contract agreed with the chassis owner —
no overlapping requests, a stop on every way a press or the page can end, no
auto-repeat on load, a refused step ends the hold — and check that each sensor
panel and the always-visible stop are present. Source-level, so it stays honest
on a machine with no browser and no robot.
"""
from __future__ import annotations

from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parent.parent / "client" / "robot-joystick.html")


@pytest.fixture(scope="module")
def html() -> str:
    return SRC.read_text(encoding="utf-8")


# ---- the drive pad's safety contract ----

def test_it_posts_only_intents_to_the_existing_command_endpoint(html):
    assert "/robot/command" in html and "/robot/state" in html
    for act in ("forward", "back", "left", "right", "stop"):
        assert act in html
    # Continuous drive: the body carries intent (a direction), never a
    # distance or speed — the step size and speed live on the server.
    assert "metres" not in html and "forward_m" not in html
    assert "action: 'drive', direction" in html
    assert "Object.assign({ token }" in html  # token wrapped in by commandBody


def test_holding_never_overlaps_requests(html):
    # One self-paced pump keyed on `driving` (not setInterval): each drive
    # awaits the previous, so a slow reply never stacks a second heartbeat, and
    # dragging to a new direction just changes what the single loop sends.
    assert "while (driving)" in html
    assert "await commandBody({ action: 'drive', direction: driving })" in html


def test_drive_is_a_continuous_heartbeat_under_the_watchdog(html):
    """The joystick feel: while a direction is held the page re-sends drive on
    a fixed cadence that sits under the server's 400ms watchdog, so the base
    keeps moving — and the moment the page stops (release, disconnect, hidden)
    the server stops the robot on its own. One dropped send is tolerated; two
    in a row ends the hold."""
    assert "DRIVE_INTERVAL_MS = 150" in html
    assert "++fails >= 2" in html


def test_every_way_a_drag_ends_sends_stop(html):
    # The stick captures the pointer, so a release arrives as pointerup,
    # pointercancel, or lostpointercapture — each must stop the base.
    for ev in ("pointerup", "pointercancel", "lostpointercapture"):
        assert ev in html, f"a released drag via {ev} must stop"
    assert "'blur'" in html and "visibilitychange" in html
    assert "stopNow" in html
    assert "catch (e)" in html and "stopNow(" in html


def test_a_refused_step_ends_the_hold_instead_of_hammering(html):
    assert "motion_locked" in html
    assert "driving = null" in html


def test_the_control_is_a_drag_stick_not_press_buttons(html):
    # The owner asked for "a joystick you slide, not press". The control is a
    # drag stick: pointerdown captures the pointer and pointermove steers by
    # position — never a click, which can neither hold nor release a direction.
    # The old 4-button press pad is gone.
    assert "let driving = null" in html
    assert 'id="stick"' in html and 'id="knob"' in html
    assert "stick.setPointerCapture" in html
    assert "stick.addEventListener('pointerdown'" in html
    assert "stick.addEventListener('pointermove'" in html
    assert 'class="dir"' not in html            # the press-buttons are gone
    assert "setDirEnabled(false)" in html


def test_the_lock_state_is_shown_not_hidden(html):
    assert "motion_enabled" in html
    assert "ล็อก" in html and "ปลดล็อก" in html


def test_there_is_an_always_visible_chassis_stop(html):
    # Beyond the pad's stop, a sticky stop that never scrolls away.
    assert 'id="estop"' in html
    assert "position:sticky" in html
    assert "$('estop').addEventListener('pointerdown'" in html


# ---- the sensor panels, each failing on its own ----

def test_the_camera_panel_polls_and_falls_back_on_a_stale_feed(html):
    assert "/cam.jpg" in html
    # A dropped frame shows the placeholder, not a frozen last image.
    assert "camph" in html and "img.onerror" in html


def test_the_radar_reads_lidar_points_by_angle_and_distance(html):
    assert "/robot/laserscan" in html
    assert "p.distance" in html and "p.angle" in html
    # invalid returns are not drawn as an object at range 0.
    assert "p.valid" in html


def test_the_mic_meter_reads_the_level_endpoint(html):
    assert "/mic/level" in html
    assert "speech_floor" in html and "streaming" in html


def test_the_map_click_goes_by_coordinate_not_place(html):
    assert "/robot/map" in html
    assert "action: 'goto', x: target.x, y: target.y" in html
    assert "map.height - 1 - row" in html          # y-axis flip
    norm = html.replace(" ", "")
    assert "map.origin_x+(col+0.5)*map.resolution" in norm
    assert "map.origin_y+(row+0.5)*map.resolution" in norm
    # Measured byte semantics (2026-09-12): 0 unknown, 1..127 free, 128..255
    # occupied. A plain grayscale drew walls near-white — inverted, and unsafe
    # for a map you click to drive onto. Render must branch, not copy v.
    assert "img.data[o]=img.data[o+1]=img.data[o+2]=v;" not in norm
    assert "v===0" in norm and "v<=127" in norm


def test_the_pollers_pause_when_the_tab_is_hidden(html):
    # Every poller checks document.hidden so a backgrounded tab stops taxing
    # the nc tunnel (the chassis owner's rate note).
    assert html.count("if (document.hidden) return;") >= 4
