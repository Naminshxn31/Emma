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


def _node() -> str:
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — robot-joystick.html's JavaScript is UNPARSED and the walkable "
                    "fan's colours / left-right mapping are NOT being checked on this machine")
    return node


def _run_js(code: str) -> str:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
        handle.write(code)
        path = handle.name
    try:
        # encoding, not text=True: Thai comes back on stdout and cp874 cannot decode it
        done = subprocess.run([_node(), path], capture_output=True, encoding="utf-8", timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def _walk_region(html: str) -> str:
    # from the line after the opening marker (its own line is a comment)
    return html.split("// <walkable>", 1)[1].split("\n", 1)[1].split("// </walkable>", 1)[0]


def test_every_plain_script_block_parses(html):
    import re
    import subprocess
    import tempfile

    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks
    for i, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(block)
            path = handle.name
        try:
            done = subprocess.run([_node(), "--check", path], capture_output=True,
                                  encoding="utf-8", timeout=30)
        finally:
            Path(path).unlink(missing_ok=True)
        assert done.returncode == 0, f"robot-joystick.html script #{i}:\n{done.stderr}"


# ---- the walkable fan (owner 9 ต.ค.: "มุมไหนเดินได้หรือไม่ได้") ----

def test_the_fan_reads_only_the_servers_walkable_field(html):
    """One place decides what is in the way (app/main.py _walkable, the lanes'
    own free_space). The overlay draws that field; it never works the answer out
    again from the points, which would let the screen and the robot disagree."""
    import re

    code = re.sub(r"//[^\n]*", "", _walk_region(html))
    assert "scan.walkable" in code
    for other in (".points", ".distance", ".clearance", ".angle", "laserscan", "fetch("):
        assert other not in code, other
    # drawn under the stop ring and the points, from the same mapping as the points
    radar = html.split("function drawRadar(", 1)[1].split("async function pollRadar", 1)[0]
    assert radar.index("drawWalk(") < radar.index("min_clearance") < radar.index("p.valid) continue")
    assert "radarXY(cx, cy, scale, p.angle, p.distance)" in radar


def test_the_fan_colours_and_the_left_is_drawn_on_the_left(html):
    import json

    out = json.loads(_run_js(_walk_region(html) + r"""
const set = { step: 5, max: 3, length: 0.6, width: 0.7,
              dirs: [{ a: 0, free: 3.0, turn: true }, { a: 90, free: 0.62, turn: true }],
              arms: { width: 0.78, dirs: [{ a: 0, free: 0.62, turn: true }] } };
const left = radarXY(100, 100, 10, Math.PI / 2, 1), ahead = radarXY(100, 100, 10, 0, 1);
console.log(JSON.stringify({
  cls: [[null, null], [1.0, true], [0.99, true], [0.4, true], [0.39, true], [3.0, false], [2.0, null]]
        .map((x) => walkClass(x[0], x[1])),
  left, ahead,
  base: walkSet({ walkable: set }, false).width, arms: walkSet({ walkable: set }, true).width,
  none: [walkSet({}, false), walkSet(null, true), walkSet({ walkable: { dirs: 'x' } }, false)],
  noArms: walkSet({ walkable: { step: 5, max: 3, length: 0.6, width: 0.7, dirs: [] } }, true).width,
  textBase: walkAheadText(walkSet({ walkable: set }, false)),
  textArms: walkAheadText(walkSet({ walkable: set }, true)),
  textNone: walkAheadText(null),
}));
"""))
    assert out["cls"] == ["unk", "ok", "tight", "tight", "stop", "stop", "ok"]
    # + angle is the robot's LEFT (board convention); forward is up on the plot
    assert out["left"][0] < 100 and abs(out["left"][1] - 100) < 1e-9
    assert out["ahead"][1] < 100 and abs(out["ahead"][0] - 100) < 1e-9
    assert (out["base"], out["arms"], out["noArms"]) == (0.7, 0.78, 0.7)
    assert out["none"] == [None, None, None]
    assert out["textBase"] == "ตรงหน้า: เดินได้ เกิน 3 ม. (ตัวกว้าง 0.70 ม.)"
    assert out["textArms"] == "ตรงหน้า: แคบ 0.62 ม. (ตัวกว้าง 0.78 ม.)"
    assert out["textNone"] == "มุมเดินได้: เฟรมนี้ไม่มีข้อมูล"


def test_the_fan_has_a_thai_legend_and_asks_for_the_arms_only_when_ticked(html):
    for label in ("เดินได้", "แคบ", "ติด", "มองไม่เห็น"):
        assert f"</span>{label}</span>" in html, label
    assert 'id="walknote"' in html and 'id="walkarms"' in html
    poll = html.split("async function pollRadar", 1)[1].split("// ---------- map", 1)[0]
    assert "(arms ? (q ? '&' : '?') + 'arms=1' : '')" in poll
    # a lost frame clears the fan: a green left over from the last good frame
    # is a picture of a room that may have changed
    assert "drawRadar({ points: [] }, null);" in poll


def test_the_pollers_pause_when_the_tab_is_hidden(html):
    # Every poller checks document.hidden so a backgrounded tab stops taxing
    # the nc tunnel (the chassis owner's rate note).
    assert html.count("if (document.hidden) return;") >= 4
