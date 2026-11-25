"""The ceremony's story title and closing (`smarthome_brand` in its "brand" mode: s09_l02, s10_l05) --
the owner's notes of 23 Nov 2026 on his preview:

  * s09_l02: "รูปโลโก้ไม่สวยให้เปลี่ยน ไม่ต้องเอาพื้นหลัง เอาโลโก้ออกไปเลย" -- the chrome logo clip stood on
    the black disc of its own frame over the blue stage. No logo; the title carries the name.
  * s10_l05, the courtyard behind "Emma / An assistant to our sales team": "ทำไมไม่เต็มจอ" -- the picture
    stopped a fifth short of the right edge of his 2.2:1 window. Its box was named `hero`, and the
    mirror page's own `html.mirror .hero { position: fixed; inset: 0 }` took it. It also stopped 2cu
    short of the screen's foot on every screen, and the event's TV is ~1.9:1 (bare stage either side
    of the 16:9 box).
  * s10_l05, "Get to know our project even better": "ภาพซ้อนกัน แล้วรูปไม่ต้องเอาโฮโลแกรมมา เอารูปตึกจริงๆมา"
    -- the strip's cards overlapped, and two were holograms.

Each test here was seen RED with the change undone (the act and the slides of 22 Nov, in a copy).
"""
import json
import re
import struct
import zlib
from pathlib import Path

import pytest

from app import ceremony_slides as cs
from app import event_script
from tests import test_ceremony_acts as T
from tests.test_ceremony_acts import chromium, real  # noqa: F401 -- fixtures, used by name

ROOT = T.ROOT
ACTS = T.ACTS
SLIDES_DIR = T.SLIDES_DIR
STORY, CLOSING = "s09_l02", "s10_l05"
PAGE_HTML = ROOT / "client" / "voice-preview.html"


def _slide(cue_id: str) -> dict:
    return json.loads((SLIDES_DIR / ("%s.json" % cue_id)).read_text(encoding="utf-8"))


def _page(browser):
    return T._page(browser, ACTS)


def _entry(asset_id: str) -> dict:
    """What the index (or, for a catalogue picture, the pictures' list) says a picture is."""
    row = dict(cs.asset_registry().get(asset_id) or {})
    row.update({k: v for k, v in (cs.asset_index().get(asset_id) or {}).items() if v is not None})
    return row


# ---- s09: no logo, the title says the name ----------------------------------------------------

def test_the_story_line_has_no_logo_and_its_title_carries_the_name(real, chromium):
    slide = _slide(STORY)
    assets = [row["asset"] for row in (slide.get("pictures") or {}).values()] + [row["asset"] for row in (slide.get("videos") or {}).values()]
    assert not slide.get("videos") and not any("logo" in a for a in assets), assets
    # the act shows no clip at all (the logo was the only one), in either mode
    assert "ctx.video(" not in (ACTS / "smarthome_brand.js").read_text(encoding="utf-8")
    # "Embassy World" -- what the logo said -- is the title's, lit on her own words
    name = [k for k, v in slide["texts"].items() if v == "Embassy World"]
    assert name and any(b.get("at_en") == "Embassy World" and name[0] in ([b["key"]] if isinstance(b.get("key"), str) else b.get("key") or [])
                        for b in slide["beats"]), slide["beats"]
    # ... and in a real page nothing of a clip is ever on the screen, from her first word to the dive
    payload = cs.for_cue(STORY, drafts=True)
    assert payload is not None, cs.problems(slide, event_script.cue(STORY))
    page, said = _page(chromium)
    try:
        got = T._walk(page, payload)
    finally:
        page.close()
    assert T._on_screen_problems(payload, got, said) == []
    assert not [m for look in got["seen"] for m in look["media"] if m["tag"] == "VIDEO"]


# ---- the pictures: real renders, each view once -----------------------------------------------

#: One render in two crops: two of a group on one line are the same picture twice -- 22 Nov the
#: closing's w1 and w3 were the courtyard.
SAME_RENDER = [{"fin_pool_v4b", "fin_pool_v4_1610"}, {"fin_pool_v1", "fin_pool_v1_s"}]
#: One view of the project in another render (the owner's front by day and at dusk): never side by side.
SAME_VIEW = [{"fin_front_1120", "fin_entrance"}, {"fin_pool_v4b", "fin_pool_v4_1610", "liv_trees", "prj_life_lagoon"}]


def _twins(row: list, ring: bool) -> list:
    """The pairs in `row` that are one render twice, or one view side by side (`ring`: the last is
    beside the first -- the reel runs round)."""
    bad = [sorted(g & set(row)) for g in SAME_RENDER if len(g & set(row)) > 1]
    pairs = list(zip(row, row[1:])) + ([(row[-1], row[0])] if ring and len(row) > 2 else [])
    return bad + [[a, b] for a, b in pairs for g in SAME_VIEW if a != b and {a, b} <= g]


def test_the_story_and_the_closing_show_real_renders_never_a_hologram(real):
    """Owner 23 Nov on the closing's strip: "รูปไม่ต้องเอาโฮโลแกรมมา เอารูปตึกจริงๆมา" -- and the story's
    reel had six holograms. Every picture of both lines is one of the owner's renders."""
    for cue_id in (STORY, CLOSING):
        for key, row in _slide(cue_id)["pictures"].items():
            entry = _entry(row["asset"])
            shows = str(entry.get("shows") or "").lower()
            assert entry.get("class") == "render" and "holo" not in row["asset"] and "hologram" not in shows, (cue_id, key, row["asset"])


#: The building's OLD design (white wavy balconies): the owner had rm_signature replaced on s06_l03 for
#: that ("เปลี่ยนเป็นภาพใหม่", 23 Nov) -- c_entrance_aerial and liv_garden are the same old look.
OLD_DESIGN = {"rm_signature", "c_entrance_aerial", "liv_garden", "liv_trees", "prj_life_lagoon", "c_lagoon_path"}


def test_the_story_and_the_closing_show_only_the_owners_final_design(real):
    """Coordinator 23 Nov, after the owner's note on s06_l03: no picture of the old building design on
    these two lines -- only the owner's final renders (the fin_* family)."""
    for cue_id in (STORY, CLOSING):
        used = [row["asset"] for row in _slide(cue_id)["pictures"].values()]
        assert all(a.startswith("fin_") and a not in OLD_DESIGN for a in used), (cue_id, used)


def test_the_closing_strip_holds_each_view_once_and_its_middle_card_is_the_pulled_back_picture(real):
    slide = _slide(CLOSING)
    strip = [slide["pictures"][k]["asset"] for k in sorted(slide["pictures"]) if re.fullmatch(r"w\d", k)]
    assert len(set(strip)) == len(strip) and _twins(strip, False) == [], strip
    # (the full-screen picture is pulled back INTO the middle card and that card's copy takes over
    # under it: the middle card is the same picture, the hand-over is invisible -- it is not a twin)
    assert strip[len(strip) // 2] == slide["pictures"]["full"]["asset"]
    story = _slide(STORY)["pictures"]
    reel = [story[k]["asset"] for k in sorted(story) if k.startswith("reel")]
    assert len(set(reel)) == len(reel) and _twins(reel, True) == [], reel
    # (the window in the middle of the reel is the courtyard: the reel has none of it)
    assert _twins(reel + [story["into"]["asset"]], False) == [] and story["into"]["asset"] not in reel, reel


# ---- the strip: no card over another --------------------------------------------------------

STRIP_JS = """async (slide) => {
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 100 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  await wait(400);
  for (const f of slide.__steps) { eng.progress(f); await wait(60); }
  await wait(1900);
  const stage = document.querySelector('.cs-act-stage'), cu = stage.getBoundingClientRect().width / 100;
  const cards = Array.from(document.querySelectorAll('.cs-act-stage .cs-wheel-card')).map(e => {
    const r = e.getBoundingClientRect(), card = e.querySelector('.wcard');
    return { l: r.left, r: r.right, t: r.top, b: r.bottom, o: card ? +getComputedStyle(card).opacity : 0 };
  });
  eng.clear(); await wait(60);
  return { cu: cu, cards: cards, state: eng.state() };
}"""


def test_the_closing_strip_tiles_never_overlap(real, chromium):
    """Owner 23 Nov, "ภาพซ้อนกัน": 26cu cards on a 58cu cylinder 24 degrees apart met 24.7cu apart --
    every two overlapped, and the outer two of seven faced away. Measured in the page, the row whole
    (after "our project"): every card drawn, and a gap of light between every two."""
    payload = cs.for_cue(CLOSING, drafts=True)
    whole = next(b["at"] for b in payload["beats"] if b["do"] == "pair2")
    back = next(b["at"] for b in payload["beats"] if b["do"] == "back")
    steps = sorted({round(b["at"] + 0.004, 4) for b in payload["beats"] if b["at"] <= whole} | {round((whole + back) / 2, 4)})
    page, said = _page(chromium)
    try:
        got = page.evaluate(STRIP_JS, {**payload, "__steps": steps})
    finally:
        page.close()
    cards = sorted(got["cards"], key=lambda c: c["l"])
    assert len(cards) == 5 and all(c["o"] > 0.9 and c["r"] - c["l"] > 2 * got["cu"] for c in cards), (cards, said)
    gaps = [round((b["l"] - a["r"]) / got["cu"], 2) for a, b in zip(cards, cards[1:])]
    assert min(gaps) >= 0.5, gaps
    # ... and the row stays on the stage
    assert cards[0]["l"] >= 0 and cards[-1]["r"] <= 100 * got["cu"] + 0.5, cards


# ---- the project's frame fills the screen, 16:9 and wider ------------------------------------

def _png(w: int, h: int, rgb: tuple) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


MAGENTA = _png(16, 10, (255, 0, 255))

#: The mirror page's own stylesheet -- where `html.mirror .hero` lives -- on the test page, and what is
#: drawn OVER the frame (words, glass, the grades) hidden: what is left must be the frame's picture.
HIDE = """.cs-act-smarthome_brand .cs-text, .cs-act-smarthome_brand .cs-glass, .cs-act-smarthome_brand .grade,
.cs-act-smarthome_brand .pgrade, .cs-act-smarthome_brand .sbwgrade, .cs-act-smarthome_brand .sbwtitle,
.cs-act-smarthome_brand .titlebox, .cs-act-smarthome_brand .chiplane, .cs-act-smarthome_brand .reel,
.cs-act-smarthome_brand .landbg { visibility: hidden !important; }
.cs-act-smarthome_brand *::before, .cs-act-smarthome_brand *::after { display: none !important; }"""

FRAME_JS = """async (args) => {
  const [slide, steps, settle, holdMs] = args;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 100 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  await wait(400);
  for (const f of steps) { eng.progress(f); await wait(60); }
  if (settle) eng.settle();
  await wait(holdMs);
  const stage = document.querySelector('.cs-act-stage');
  return { state: eng.state(), cu: stage ? stage.getBoundingClientRect().width / 100 : 0 };
}"""


def _misses(shot: bytes, width: int, height: int, cu: float) -> tuple[float, list]:
    """The share of the screen's stage area (its whole width; 56.25cu from the top) that is not the
    frame's picture, and where."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow is not installed -- NOT checked here: that the "
                                "project's frame fills the event screen edge to edge")
    import io
    img = Image.open(io.BytesIO(shot)).convert("RGB")
    rows = min(height, int(56.25 * cu))
    px = img.load()
    bad, where = 0, []
    for y in range(0, rows, 3):
        for x in range(0, width, 3):
            r, g, b = px[x, y]
            if not (r > 170 and g < 90 and b > 170):
                bad += 1
                if len(where) < 6:
                    where.append((x, y, (r, g, b)))
    return bad / (((rows + 2) // 3) * ((width + 2) // 3)), where


def _frame_page(chromium, size: tuple, hide: str = HIDE):
    page, said = _page(chromium)
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.route("**/ceremony-asset/*", lambda route: route.fulfill(body=MAGENTA, content_type="image/png"))
    css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", PAGE_HTML.read_text(encoding="utf-8"), flags=re.S))
    page.add_style_tag(content=css)
    if hide:
        page.add_style_tag(content=hide)
    return page, said


@pytest.mark.parametrize("size", [(1920, 1080), (2048, 1080), (1910, 870)])
def test_the_closing_opens_on_the_project_edge_to_edge_on_a_169_and_a_wider_screen(size, real, chromium):
    """Owner 23 Nov, "ทำไมไม่เต็มจอ" (his preview 1910 x 870; the event's TV is 4096 x 2160): from the
    first frame to the pull back into the strip, the courtyard is the whole screen -- no bare stage at
    the sides, none under the subtitle's box. On the real mirror page's stylesheet."""
    payload = cs.for_cue(CLOSING, drafts=True)
    pull = next(b["at"] for b in payload["beats"] if b["do"] == "pull")
    early = [round(b["at"] + 0.004, 4) for b in payload["beats"] if b["at"] < 0.12] + [0.12]
    late = [round(b["at"] + 0.004, 4) for b in payload["beats"] if 0.12 < b["at"] < pull] + [round(pull - 0.01, 4)]
    page, said = _frame_page(chromium, size)
    try:
        first = page.evaluate(FRAME_JS, [payload, early, False, 3300])
        shot1 = page.screenshot()
        second = page.evaluate("async (steps) => { const e = window.CeremonyShow;"
                               " for (const f of steps) { e.progress(f); await new Promise(r => setTimeout(r, 60)); }"
                               " await new Promise(r => setTimeout(r, 900)); return e.state(); }", late)
        shot2 = page.screenshot()
    finally:
        page.close()
    assert first["state"]["started"] and not first["state"]["failed"] and not second["failed"], said
    for shot in (shot1, shot2):
        share, where = _misses(shot, size[0], size[1], first["cu"])
        assert share < 0.001, (size, round(share, 4), where)


@pytest.mark.parametrize("size", [(1920, 1080), (2048, 1080)])
def test_the_story_line_dives_into_the_project_until_it_is_the_whole_screen(size, real, chromium):
    """s09's last frame -- held while the hall plays the story video, and the frame s10 opens on (a
    join) -- is the courtyard, edge to edge, on a 16:9 and on the event's ~1.9:1 screen."""
    payload = cs.for_cue(STORY, drafts=True)
    steps = [round(b["at"] + 0.004, 4) for b in payload["beats"]] + [1.0]
    page, said = _frame_page(chromium, size)
    try:
        got = page.evaluate(FRAME_JS, [payload, steps, True, 6200])
        shot = page.screenshot()
    finally:
        page.close()
    assert got["state"]["settled"] and not got["state"]["failed"], said
    share, where = _misses(shot, size[0], size[1], got["cu"])
    assert share < 0.001, (size, round(share, 4), where)


# ---- s09: no reel card is seen cut by the window or tucked behind it -----------------------------

REEL_JS = """async (args) => {
  const [slide, steps] = args;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const eng = window.CeremonyShow;
  eng.show(slide);
  for (let i = 0; i < 100 && !(eng.state().started || eng.state().failed); i++) await wait(50);
  await wait(400);
  for (const f of steps) { eng.progress(f); await wait(60); }
  await wait(1600);
  const box = s => { const e = document.querySelector('.cs-act-stage ' + s); if (!e) return null;
    const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  const stage = document.querySelector('.cs-act-stage');
  return { state: eng.state(), cu: stage.getBoundingClientRect().width / 100, win: box('.sbwin'), reel: box('.reel') };
}"""

#: What stands over the reel and is not a reel card: hidden for the look (the window itself, its
#: glow, the words, the chips) -- what is left in the window's place can only be the reel.
HIDE_WINDOW = """.cs-act-smarthome_brand .sbwinwrap, .cs-act-smarthome_brand .cs-text, .cs-act-smarthome_brand .chiplane,
.cs-act-smarthome_brand .titlebox { visibility: hidden !important; }"""


@pytest.mark.parametrize("size", [(1920, 1080), (2048, 1080)])
def test_no_reel_card_is_seen_cut_by_the_story_window(size, real, chromium):
    """Review 23 Nov: s09's window stood over the reel's middle cards -- a card's edge peeked out beside
    it, another was half under it; the owner had just said "ภาพซ้อนกัน" of s10's strip. With the window
    up (and the reel running), nothing of a reel card shows in the window's place or within 1.5cu of it:
    looked at twice, the reel's pictures magenta, the window hidden for the look."""
    pytest.importorskip("PIL.Image", reason="Pillow is not installed -- NOT checked here: that no reel card "
                        "shows cut by the story line's window")
    import io

    from PIL import Image

    payload = cs.for_cue(STORY, drafts=True)
    steps = [round(b["at"] + 0.004, 4) for b in payload["beats"]]
    page, said = _frame_page(chromium, size, hide="")
    try:
        got = page.evaluate(REEL_JS, [payload, steps])
        assert got["win"] and got["reel"] and not got["state"]["failed"], (got, said)
        page.add_style_tag(content=HIDE_WINDOW)
        shots = [page.screenshot()]
        page.wait_for_timeout(700)
        shots.append(page.screenshot())
    finally:
        page.close()
    cu, win, reel = got["cu"], got["win"], got["reel"]
    assert win["r"] - win["l"] > 10 * cu and win["t"] < reel["b"] and win["b"] > reel["t"]     # (the window is over the reel)
    x0, x1 = int(win["l"] - 1.5 * cu), int(win["r"] + 1.5 * cu)
    y0, y1 = int(max(win["t"], reel["t"])) + 2, int(min(win["b"], reel["b"])) - 2
    for shot in shots:
        px = Image.open(io.BytesIO(shot)).convert("RGB").load()
        seen = [(x, y, px[x, y]) for y in range(y0, y1, 2) for x in range(x0, x1, 2)
                if px[x, y][0] - px[x, y][1] > 40]                      # (magenta, even faint, over the blue stage)
        assert seen == [], (size, len(seen), seen[:5])


# ---- the mirror page's own stylesheet never reaches into an act ---------------------------------

def _subjects(css: str) -> set:
    """(classes, selector) of every rule whose subject -- the last compound -- is classes only."""
    out = set()
    for prelude in re.findall(r"([^{}]+)\{", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        prelude = prelude.strip()
        if not prelude or prelude.startswith("@"):
            continue
        for sel in prelude.split(","):
            last = re.sub(r"::?[\w-]+(\([^)]*\))?", "", re.split(r"\s+|>|\+|~", sel.strip())[-1])
            if re.fullmatch(r"(\.[\w-]+)+", last):
                out.add((frozenset(re.findall(r"\.([\w-]+)", last)), sel.strip()))
    return out


def test_no_act_names_its_own_parts_with_a_class_the_mirror_page_styles():
    """23 Nov: smarthome_brand's full-screen picture was a `.hero`; the mirror page -- the event
    screen's page -- has its own `html.mirror .hero { position: fixed; inset: 0 }`, and it took the
    act's box (the picture hung off the screen's left edge, past the stage's clip, and stopped short
    of its right). An act's stylesheet is scoped under .cs-act-<name>; the page's is not."""
    html = PAGE_HTML.read_text(encoding="utf-8")
    page_css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.S))
    for name in re.findall(r'<link rel="stylesheet" href="/assets/([^"?]+)', html):
        page_css += "\n" + (ROOT / "client" / "assets" / name).read_text(encoding="utf-8")
    page = _subjects(page_css)
    assert any("hero" in classes for classes, _ in page)                      # (the rule is there: the check reads it)
    bad = []
    for sheet in sorted(ACTS.glob("*.css")):
        mine = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"/\*.*?\*/", "", sheet.read_text(encoding="utf-8"), flags=re.S)))
        bad += ["%s: %s" % (sheet.name, sel) for classes, sel in page if classes <= mine and not any(c.startswith("cs-") for c in classes)]
    assert bad == []
