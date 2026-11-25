"""The hall's screen shows no mouse pointer (owner 24 Nov night: "จอโน๊ตบุคตอนเปิดไม่อยากให้โชว์เมาท์ขึ้นมา"): the room
screen page and the story frame in it, in a real Chromium -- and the robot's own face page keeps its pointer."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
STAGE = ROOT / "client" / "assets" / "story-stage.html"


def test_the_rules():
    page, stage = PAGE.read_text(encoding="utf-8"), STAGE.read_text(encoding="utf-8")
    assert "  html.mirror, html.mirror * { cursor: none !important; }" in page
    assert "  html, body, body * { cursor: none !important; }" in stage
    assert "f.src = '/assets/story-stage.html?v=20261124-d';" in page                # (a new address: not the cached frame)


def test_in_chromium():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: the pointer in a browser")
    css = PAGE.read_text(encoding="utf-8")
    css = css[css.index("<style>") + 7:css.index("</style>")]
    doc = "<!doctype html><html class='{cls}'><style>" + css + "</style><body><div class='controls'><button id='b'>x</button></div><p id='p'>t</p></body></html>"
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s) -- NOT checked here" % str(exc).splitlines()[0])
        pg = b.new_page()
        got = {}
        for cls in ("mirror mirror-show", ""):
            pg.set_content(doc.replace('{cls}', cls))
            got[cls] = pg.evaluate("() => [getComputedStyle(document.getElementById('p')).cursor, getComputedStyle(document.body).cursor]")
        pg.set_content(STAGE.read_text(encoding="utf-8").replace("<script", "<!--").replace("</script>", "-->"))
        stage = pg.evaluate("() => getComputedStyle(document.body).cursor")
        b.close()
    assert got["mirror mirror-show"] == ["none", "none"] and "none" not in got[""] and stage == "none"
