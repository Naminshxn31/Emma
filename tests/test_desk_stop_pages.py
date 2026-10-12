"""The desk STOP buttons say "sent", not "stopped", for a route run (finding #0).

During a leg run `/robot/command stop` goes to the robot face as a nav stop
and a body stop and answers `did: "stop_requested"` — the app queued it, no
more (tests/test_robot_chassis_guards.py covers the server). These pages are
where staff read that answer; "หยุดแล้ว" there would be the same lie the
voice path is forbidden to tell. Source checks, the way the other page tests
in this repo read their pages.
"""
from __future__ import annotations

import re
from pathlib import Path

CLIENT = Path(__file__).resolve().parent.parent / "client"


def _stop_handler(html: str) -> str:
    start = html.index("$('stop').addEventListener('click'")
    return html[start:html.index("\n});", start)]


def test_the_map_page_says_a_route_stop_was_sent_not_done():
    handler = _stop_handler((CLIENT / "robot-map.html").read_text(encoding="utf-8"))
    assert "r.did === 'stop_requested'" in handler
    sent = re.search(r"'stop_requested' \? '([^']*)'", handler).group(1)
    assert "ส่งคำสั่งหยุดแล้ว" in sent and "ยังไม่ยืนยัน" in sent
    assert "หยุดแล้ว'" not in sent.replace("ส่งคำสั่งหยุดแล้ว", "")
    # A refusal shows the server's Thai hint (press the red button), not the code.
    assert "why(r)" in handler


def test_the_console_labels_a_route_stop_as_sent():
    html = (CLIENT / "robot-console.html").read_text(encoding="utf-8")
    line = next(l for l in html.splitlines() if l.strip().startswith("const status = failed"))
    assert "out.did === 'stop_requested' ? 'ส่งคำสั่งหยุดแล้ว'" in line
    # The server's note (sent, not confirmed) is what the detail shows.
    assert "out.note ||" in html


def test_the_joystick_stop_reads_the_reply():
    """robot-joystick.html sends the same stop on every stick release and on
    its red button. It dropped the reply: a route stop read "หยุด", and the
    face-gone answer (press the robot's own stop) was never shown."""
    html = (CLIENT / "robot-joystick.html").read_text(encoding="utf-8")
    start = html.index("async function stopNow(why)")
    body = html[start:html.index("\n}", start)]
    assert "r = await commandBody({ action: 'stop' })" in body
    assert body.index("r.ok === false") < body.index("if (was || why)")
    assert "r.hint" in body
    sent = re.search(r"r\.did === 'stop_requested'\) \{ driveMsg\('([^']*)'", body).group(1)
    assert "ส่งคำสั่งหยุดแล้ว" in sent and "ยังไม่ยืนยัน" in sent


def test_an_unconfirmed_stop_is_never_shown_as_done():
    """Review 2, findings #0-#2: with a walk the server cannot see (face not
    on a call) or a face that is gone, /robot/command stop answers
    did:'stop_unconfirmed' — only the board's pause went out. /mapview must
    not say 'สั่งหยุดแล้ว' and the console must not say 'เซิร์ฟเวอร์ตอบรับ'
    for it, whatever `ok` says; both show the server's hint."""
    handler = _stop_handler((CLIENT / "robot-map.html").read_text(encoding="utf-8"))
    branch = re.search(r"r\.did === 'stop_unconfirmed' \? '([^']*)' \+ why\(r\)", handler)
    assert branch, "the map page has no stop_unconfirmed branch"
    assert "ยังยืนยันไม่ได้" in branch.group(1) and "หยุดแล้ว" not in branch.group(1)
    # The unconfirmed branch is decided before `r.ok` is looked at.
    assert handler.index("'stop_unconfirmed'") < handler.index("r.ok ?")

    html = (CLIENT / "robot-console.html").read_text(encoding="utf-8")
    failed = next(l for l in html.splitlines() if l.strip().startswith("const failed ="))
    assert "out.did === 'stop_unconfirmed'" in failed
    status = next(l for l in html.splitlines() if l.strip().startswith("const status = failed"))
    unconfirmed = re.search(r"out\.did === 'stop_unconfirmed' \? '([^']*)'", status).group(1)
    assert "ยืนยัน" in unconfirmed and unconfirmed != "เซิร์ฟเวอร์ตอบรับ"


def test_the_joystick_shows_the_hint_of_an_unconfirmed_stop():
    """The server answers stop_unconfirmed with ok:false, so the joystick's
    `r.ok === false` branch is what shows the hint."""
    html = (CLIENT / "robot-joystick.html").read_text(encoding="utf-8")
    body = html[html.index("async function stopNow(why)"):]
    body = body[:body.index("\n}")]
    assert "if (r && r.ok === false) { driveMsg(r.hint" in body
