"""The walk's music on the hall's screen (app/catwalk_music.py, app/face_mirror.py `catwalk`, client/assets/catwalk-music.js;
owner 24 Nov): what the server says and when it stops, what a screen back from a blink is told, the file route, and the page's
engine in a real Chromium with real buffers (a loop that keeps going, the duck, the hit, the fade, a lost robot)."""
import asyncio
import hashlib
import io
import json
import math
import re
import struct
import wave
from pathlib import Path

import pytest

from app import catwalk_music, face_mirror
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "client" / "voice-preview.html"
ENGINE = ROOT / "client" / "assets" / "catwalk-music.js"


@pytest.fixture(autouse=True)
def _clean():
    face_mirror.reset()
    yield
    face_mirror.reset()


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "catwalk_music", True)
    folder = tmp_path / "data" / "events" / "clips"
    folder.mkdir(parents=True)
    for name in ("walk.opus", "guests.opus", "hit.flac"):
        (folder / ("catwalk_" + name)).write_bytes(b"sound " + name.encode())
    monkeypatch.setattr(catwalk_music, "ROOT", tmp_path)
    real = catwalk_music.files
    monkeypatch.setattr(catwalk_music, "files", lambda root=tmp_path: real(root))
    return folder


def _screen():
    q: asyncio.Queue = asyncio.Queue()
    face_mirror._clients.add(q)
    return q


def _sent(q) -> list[dict]:
    out = []
    while not q.empty():
        item = q.get_nowait()
        if isinstance(item, str):
            out.append(json.loads(item))
    return [m for m in out if str(m.get("type")).startswith("catwalk")]


def _call(profile="ceremony"):
    face_mirror.call_started()
    face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": profile})


def test_what_is_said_and_the_level_is_kept_for_a_screen_that_comes_later(on):
    q = _screen()
    _call()
    assert face_mirror.catwalk("walk") is True
    assert face_mirror.catwalk("duck") is True
    join = [json.loads(x) for x in face_mirror.snapshot()]
    assert join[-2]["type"] == "catwalk_files" and set(join[-2]["files"]) == {"walk", "guests", "hit"}
    assert join[-1] == {"type": "catwalk", "op": "walk", "back": True, "track": "walk", "duck": True}
    assert face_mirror.catwalk("unduck") and face_mirror.catwalk("walk", track="guests") and face_mirror.catwalk("hit")
    assert [(m["op"], m.get("track")) for m in _sent(q)] == [("walk", "walk"), ("duck", None), ("unduck", None),
                                                             ("walk", "guests"), ("hit", None)]
    # (over: a screen back stops the music -- and, within the pictures' linger after a hit, keeps them a while)
    assert json.loads(face_mirror.snapshot()[-1]) == {"type": "catwalk", "op": "off", "linger": True}
    assert face_mirror.catwalk("louder") is False
    # (after the hit, her line over the ringing sting: the duck is still said, nothing is kept for a screen that comes)
    assert face_mirror.catwalk("duck") is True and face_mirror._state["catwalk"] is None
    assert _sent(q)[-1] == {"type": "catwalk", "op": "duck"} and json.loads(face_mirror.snapshot()[-1])["op"] == "off"
    assert face_mirror.catwalk("walk", track="../x") and face_mirror._state["catwalk"]["track"] == "walk"


def test_only_the_ceremonys_screen_and_only_with_the_switch(on, monkeypatch):
    q = _screen()
    assert face_mirror.catwalk("walk") is False                                                # (no call)
    _call("event")
    assert face_mirror.catwalk("walk") is False
    _call()
    monkeypatch.setattr(settings, "catwalk_music", False)
    assert face_mirror.catwalk("walk") is False and face_mirror.catwalk("off") is False
    assert not any("catwalk" in x for x in face_mirror.snapshot()) and catwalk_music.addresses() == {}
    assert _sent(q) == []


@pytest.mark.parametrize("stop", ["red", "end", "left"])
def test_it_ends_with_the_red_stop_the_call_and_the_show_mode(on, stop):
    q = _screen()
    _call()
    face_mirror.catwalk("walk")
    _sent(q)
    if stop == "red":
        face_mirror.subtitle_cut()
    elif stop == "end":
        face_mirror.call_ended()
    else:
        face_mirror.publish_json({"type": "ready", "output_rate": 24000, "profile": "event"})
    assert _sent(q) == [{"type": "catwalk", "op": "off"}] and face_mirror._state["catwalk"] is None


def test_a_line_a_slide_and_the_debuts_end_do_not_end_it(on):
    q = _screen()
    _call()
    face_mirror.catwalk("walk")
    _sent(q)
    face_mirror.subtitle({"id": "x", "text": "คำ"})
    face_mirror._stopped("slide")
    face_mirror.subtitle_end("x")
    face_mirror.reveal_stop()
    face_mirror.publish_json({"type": "interrupted"})
    face_mirror.call_started()                                                             # (a call replaced mid-walk)
    assert _sent(q) == [] and face_mirror._state["catwalk"] == {"track": "walk", "duck": False}


def test_the_files_swap_by_name_and_the_route_gives_nothing_else(on, monkeypatch):
    assert {n: p.name for n, p in catwalk_music.files().items()} == {
        "walk": "catwalk_walk.opus", "guests": "catwalk_guests.opus", "hit": "catwalk_hit.flac"}
    before = catwalk_music.addresses()["walk"]
    (on / "catwalk_walk.opus").write_bytes(b"another, longer sound")
    assert catwalk_music.addresses()["walk"] != before                                       # (never the old copy)
    (on / "catwalk_walk.opus").unlink()
    (on / "catwalk_walk.mp3").write_bytes(b"mp3")
    assert catwalk_music.body("walk")[1] == "audio/mpeg"
    assert catwalk_music.body("../../.env") is None and catwalk_music.body("debut") is None

    from fastapi.testclient import TestClient

    from app import mirror_server

    monkeypatch.setattr(settings, "mirror_token", "a-token-for-this-test")
    client = TestClient(mirror_server.app)
    assert client.get("/catwalk/walk").status_code == 403                                     # (behind the screen's cookie)
    client.cookies.set(mirror_server.COOKIE, mirror_server._cookie_value())
    got = client.get("/catwalk/walk?v=1")
    assert got.status_code == 200 and got.content == b"mp3" and got.headers["content-type"] == "audio/mpeg"
    assert client.get("/catwalk/debut_pageant_jessica").status_code == 404
    monkeypatch.setattr(settings, "catwalk_music", False)
    assert client.get("/catwalk/walk?v=1").status_code == 404


# ---- the page ------------------------------------------------------------------------------------------

def _between(text: str, a: str, b: str) -> str:
    i = text.index(a)
    return text[i:text.index(b, i)]


def test_the_page_plays_it_only_where_it_plays_sound_and_in_the_show_mode():
    page = PAGE.read_text(encoding="utf-8")
    fn = _between(page, "    function mirrorCatwalk(m) {", "    function mirrorCatwalkStop() {")
    assert fn.index("if (!mirrorSound) return;") < fn.index("createElement('script')")
    assert "if (m.op === 'walk' && !document.documentElement.classList.contains('mirror-show')) return;" in fn
    sock = _between(page, "      sock.onmessage = function (m) {", "      sock.onclose = function () {")
    assert "if (t === 'catwalk' || t === 'catwalk_files') { mirrorCatwalk(said); return; }" in sock
    assert "if ((t === 'mirror_subtitle_end' && said.cut && !said.id) || t === 'mirror_end' || (t === 'mirror_hello' && !said.in_call)) mirrorCatwalkStop();" in sock
    assert "mirrorCatwalk" not in _between(page, "    function mirrorSubInterrupted() {", "    }\n")
    assert ("if ((t === 'mirror_subtitle' || t === 'mirror_motion' || t === 'ceremony_reveal') && window.CatwalkMusic) "
            "window.CatwalkMusic.pictures_down();") in sock
    assert "eng.files(m.files, m.gallery)" in page
    closing = _between(page, "      sock.onclose = function () {\n        if (ws !== sock) return;\n        ws = null;", "      };")
    assert "mirrorCatwalkLink(false);" in closing and "mirrorCatwalkStop" not in closing
    assert "if (!on) mirrorCatwalkStop();" in _between(page, "    function mirrorShowMode(on) {", "fitMirrorStage();\n    }\n")
    # the face's own handlers never name it
    handler = _between(page, "    function onMessage(", "    // ---- the room screen (mirror): watch the robot's call ----")
    assert "atwalk" not in handler


TAG = ("20261125-l", "27e87db81277d92a")


def test_the_engines_cache_tag_moves_with_the_engine():
    digest = hashlib.sha256(ENGINE.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]
    assert re.findall(r"var CATWALK_V = '([\w-]+)'", PAGE.read_text(encoding="utf-8")) == [TAG[0]]
    assert digest == TAG[1], "catwalk-music.js changed: bump CATWALK_V in voice-preview.html and set TAG = (<new tag>, %r)" % digest


def _tone(seconds: float, hz: float, level: float) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"".join(struct.pack("<h", int(level * 32767 * math.sin(2 * math.pi * hz * i / 48000)))
                               for i in range(int(48000 * seconds))))
    return out.getvalue()


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf000000030101005d0ad8ef0000000049454e44ae426082")
GALLERY = ["/ceremony-asset/" + ("%064x" % k) for k in range(1, 4)]


SHELL = """<!doctype html><meta charset="utf-8"><body><script>
window.__phase = null;
window.CeremonyShow = { state: function () { return window.__phase ? { reveal: { phase: window.__phase } } : { up: false }; } };
</script><script src="/engine.js"></script></body>"""


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed -- NOT checked here: that the loop plays, ducks, ends and is not left on")
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        except Exception as exc:
            pytest.skip("headless Chromium is not installed (%s) -- NOT checked here: the loop in a browser" % str(exc).splitlines()[0])
        yield b
        b.close()


@pytest.fixture
def page(browser, request):
    look = getattr(request, "param", None)                     # ("gate": the Light Gate's own tests, the look kept switchable)
    pg = browser.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    holes = {"walk": 0}
    pictures = []

    def answer(route):
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "":
            return route.fulfill(body=SHELL, content_type="text/html")
        if path == "engine.js":
            # (the robot's times, in this test's seconds)
            js = ENGINE.read_text(encoding="utf-8").replace("LOST_MS = 45000, LONGEST_MS = 12 * 60000", "LOST_MS = 600, LONGEST_MS = 4000")
            js = js.replace("SHOW_MS = 7600, FADE_MS = 1200, LINGER_MS = 45000", "SHOW_MS = 300, FADE_MS = 50, LINGER_MS = 700")
            if look:
                js, n = re.subn(r"var WALK_LOOK = '[a-z]+';", "var WALK_LOOK = '%s';" % look, js)
                assert n == 1
            return route.fulfill(body=js, content_type="text/javascript")
        name = path.split("/")[-1]
        if name in ("walk", "guests", "hit"):
            return route.fulfill(body=_tone({"walk": 0.8, "guests": 0.9, "hit": 0.2}[name], 220, {"walk": 0.05}.get(name, 0.3)),
                                 content_type="audio/wav")
        if path.startswith("ceremony-asset/"):
            pictures.append(path)
            return route.fulfill(body=PNG, content_type="image/png")
        return route.fulfill(status=404, body="")

    pg.route("**/*", answer)
    pg.goto("http://catwalk.test/")
    pg.holes = holes
    pg.pictures = pictures
    yield pg
    assert errors == []
    pg.close()


def _state(pg) -> dict:
    return pg.evaluate("() => window.CatwalkMusic.state()")


def _ready(pg):
    pg.evaluate("() => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' })")
    pg.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")


def _say(pg, **m):
    pg.evaluate("(m) => window.CatwalkMusic.message(m)", m)


def test_the_loop_goes_on_ducks_changes_and_ends(page):
    _ready(page)
    _say(page, op="walk", track="walk")
    page.wait_for_timeout(1500)                                       # (past the file's own 0.8 s: it loops)
    s = _state(page)
    assert s["playing"] == "walk" and s["context"] == "running" and s["duck_gain"] == 1
    assert 1.9 <= s["gain"] <= 2.0                                    # (a quiet file comes up, at most this far)
    _say(page, op="duck")
    page.wait_for_timeout(800)
    assert _state(page)["duck_gain"] <= 0.01                          # (her voice on: the music OFF, owner 25 Nov)
    _say(page, op="walk", track="walk", duck=True, back=True)         # (back from a blink: plays on, still off)
    page.wait_for_timeout(100)
    assert _state(page)["duck_gain"] <= 0.01 and _state(page)["playing"] == "walk"
    _say(page, op="unduck")
    page.wait_for_timeout(800)
    assert _state(page)["duck_gain"] >= 0.97
    _say(page, op="walk", track="guests")
    assert _state(page)["playing"] == "guests"
    _say(page, op="hit")
    s = _state(page)
    assert s["playing"] is None and s["want"] is None
    _say(page, op="off")
    _say(page, op="duck")                                             # (nothing plays: nothing to lower)
    assert _state(page)["duck"] is False


def test_a_walk_before_the_file_is_here_starts_when_it_comes_and_a_stop_takes_it_back(page):
    _say(page, op="walk", track="guests")
    assert _state(page)["playing"] is None and _state(page)["want"] == "guests"
    _ready(page)
    page.wait_for_function("() => window.CatwalkMusic.state().playing === 'guests'")
    page.evaluate("() => window.CatwalkMusic.stop(0.1)")
    assert _state(page)["playing"] is None
    _say(page, op="hit")                                              # (a hit after a stop: no sound out of nothing)
    assert _state(page)["playing"] is None


def test_it_comes_in_on_the_debuts_hit_and_rises_under_it(page):
    _ready(page)
    page.evaluate("() => { window.__phase = 'dark'; }")
    _say(page, op="walk")
    page.wait_for_timeout(300)
    assert _state(page)["playing"] is None                            # (the screen's own debut is still in its black)
    page.evaluate("() => { window.__phase = 'hit'; }")
    page.wait_for_function("() => window.CatwalkMusic.state().playing === 'walk'")
    page.wait_for_timeout(300)
    assert _state(page)["gain"] < 0.6
    page.wait_for_timeout(2600)
    assert _state(page)["gain"] >= 1.9


def test_a_robot_that_does_not_come_back_does_not_leave_a_loop_in_the_hall(page):
    _ready(page)
    _say(page, op="walk")
    page.evaluate("() => window.CatwalkMusic.lost()")
    page.wait_for_timeout(250)
    page.evaluate("() => window.CatwalkMusic.found()")                # (a blink)
    page.wait_for_timeout(700)
    assert _state(page)["playing"] == "walk"
    page.evaluate("() => window.CatwalkMusic.lost()")
    page.wait_for_timeout(900)
    assert _state(page)["playing"] is None
    _say(page, op="walk")
    page.wait_for_timeout(4300)                                       # (its longest)
    assert _state(page)["playing"] is None


def test_the_pictures_come_with_the_walk_stay_through_a_handshake_and_go_with_it(page):
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)",
                  GALLERY + ["javascript:alert(1)", "/elsewhere/x.png"])
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    g = lambda: page.evaluate("() => window.CatwalkMusic.gallery()")
    assert g() == {"on": False, "urls": 3, "k": 0, "lingering": False, "waiting": False}   # (only the film's own addresses)
    _say(page, op="walk")
    page.wait_for_timeout(1000)
    on = g()
    assert on["on"] and on["k"] >= 3                                                # (one after another)
    assert page.evaluate("() => getComputedStyle(document.querySelector('.cw-gallery')).zIndex") == "6"
    _say(page, op="hit")                                                            # (Khun Miki's hand: the music stops)
    assert g()["on"] and g()["lingering"] and _state(page)["playing"] is None
    _say(page, op="walk", track="guests")                                           # (on to the guests)
    assert g()["on"] and not g()["lingering"]
    _say(page, op="hit")
    page.wait_for_timeout(1000)
    assert not g()["on"]                                                            # (the last hit: they go after a while)
    _say(page, op="walk")
    assert g()["on"]
    _say(page, op="off")
    assert not g()["on"]
    _say(page, op="walk")
    page.evaluate("() => window.CatwalkMusic.pictures_down()")                      # (a line of the script came up)
    assert not g()["on"] and _state(page)["playing"] == "walk"                     # (the music goes on under it)
    _say(page, op="walk")
    page.evaluate("() => window.CatwalkMusic.stop(0.1)")                            # (the red stop, the call over)
    assert not g()["on"] and all(p.startswith("ceremony-asset/") for p in page.pictures)


def test_the_gallery_is_the_films_own_pictures_with_nobody_in_them(on, monkeypatch):
    from app import ceremony_slides

    entries = {"a": {"id": "fin_entrance", "kind": "picture", "sha256": "a" * 64, "people": []},
               "b": {"id": "fin_lobby_01", "kind": "picture", "sha256": "b" * 64, "people": [[1, 2, 3, 4]]},
               "c": {"id": "fin_pool_v1", "kind": "picture", "sha256": "c" * 64, "people": []},
               "d": {"id": "not_listed", "kind": "picture", "sha256": "d" * 64, "people": []}}
    monkeypatch.setattr(ceremony_slides, "asset_index", lambda: entries)
    monkeypatch.setattr(ceremony_slides, "_servable", lambda e: e["id"] != "fin_pool_v1")
    assert catwalk_music.gallery() == [{"src": "/ceremony-asset/" + "a" * 64, "label": "Entrance"}]
    monkeypatch.setattr(settings, "catwalk_music", False)
    assert catwalk_music.gallery() == []


def test_every_picture_has_one_short_fixed_place_word():
    assert set(catwalk_music.GALLERY_LABELS) == set(catwalk_music.GALLERY_IDS)
    for word in catwalk_music.GALLERY_LABELS.values():
        assert re.fullmatch(r"[A-Z][A-Za-z ]{2,18}", word), word                     # (English, short, no digits)


@pytest.mark.parametrize("page", ["gate"], indirect=True)
def test_the_pictures_move_slowly_with_a_word_and_no_will_change_left_on(page):
    objs = [{"src": g, "label": w} for g, w in zip(GALLERY, ["Lobby", "Water Lagoon", "Studio"])]
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", objs)
    assert page.evaluate("() => window.CatwalkMusic.gallery().urls") == 3
    _say(page, op="walk")
    page.wait_for_timeout(900)
    info = page.evaluate("""() => {
      const pics = [...document.querySelectorAll('.cw-gallery .cw-pic')];
      const anims = pics.flatMap(p => p.getAnimations()).map(a => a.effect.getKeyframes().map(k => k.transform));
      return { anims, labels: [...document.querySelectorAll('.cw-label')].map(l => l.textContent),
               prop: pics.map(p => getComputedStyle(p).willChange) };
    }""")
    assert info["anims"] and all(len(k) == 2 and all(t.startswith("scale(") for t in k) for k in info["anims"])
    assert set(info["labels"]) <= {"Lobby", "Water Lagoon", "Studio"} and any(info["labels"]) and all(w == "auto" for w in info["prop"])
    _say(page, op="off")


# ---- the beat (owner 25 Nov: "โมชั่นก็ให้เข้ากับเพลง") ----------------------------------------------------

CLICKS = """(a) => {
  const sr = 48000, bpm = a.bpm, first = a.first, secs = a.secs;
  const b = new AudioBuffer({ length: sr * secs, sampleRate: sr, numberOfChannels: 1 }), d = b.getChannelData(0);
  const beat = 60 / bpm;
  for (let t = first, n = 0; t < secs - 0.05; t += beat, n++) {
    const s = Math.floor(t * sr);
    for (let i = 0; i < 480; i++) d[s + i] += (n % 4 === 0 ? 0.9 : 0.6) * Math.sin(2 * Math.PI * 1500 * i / sr) * Math.exp(-i / 90);
  }
  return window.CatwalkMusic.tempo(b);
}"""


@pytest.mark.parametrize("bpm", [126, 100])
def test_the_tempo_and_first_downbeat_are_read_from_a_click_track(page, bpm):
    got = page.evaluate(CLICKS, {"bpm": bpm, "first": 0.2, "secs": 20})
    assert abs(got["bpm"] - bpm) <= 2 and got["sure"], got
    assert abs(got["off"] - 0.2) < 0.04, got                                        # (the first strong click = the downbeat)


def test_a_loop_with_no_beat_falls_back_to_126(page):
    got = page.evaluate("""() => {
      const b = new AudioBuffer({ length: 48000 * 20, sampleRate: 48000, numberOfChannels: 1 }), d = b.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = 0.3 * Math.sin(2 * Math.PI * 220 * i / 48000);
      return window.CatwalkMusic.tempo(b);
    }""")
    assert got["bpm"] == 126 and not got["sure"]


def test_a_cut_is_always_on_a_bar_line_with_time_to_load_the_picture(page):
    res = page.evaluate("""() => {
      const E = window.CatwalkMusic, beat = 60 / 126, bar = beat * 4, off = 0.2, dur = bar * 16 + off * 0, bad = [];
      for (let pos = 0; pos < dur; pos += 0.173) {
        const g = { pos, dur, bpm: 126, off, beat }, w = E.nextLine(g, 0.8);
        let at = pos + w;                                                // (where in its lap or the next)
        if (at >= dur) at -= dur;                                        // (a lap is its own: the lines start again at `off`)
        const k = (at - off) / bar;
        if (w < 0.8 - 1e-6 || w > 0.8 + bar + 1e-6 || Math.abs(k - Math.round(k)) > 1e-6) bad.push([pos, w, k]);
      }
      return { bad: bad.slice(0, 5), bars: [E.barsFor({ beat: 60 / 126 }), E.barsFor({ beat: 60 / 200 }), E.barsFor({ beat: 60 / 70 })] };
    }""")
    assert res["bad"] == []
    assert res["bars"] == [4, 8, 2]                                         # (4 bars is ~7.6 s at 126; slower/faster tracks keep 6-11 s)


def _on_line(t, step, off=0.2):
    k = (t - off) / step
    return abs(k - round(k)) < 1e-3


ANIMS = """(sel) => [...document.querySelectorAll(sel)].flatMap(e => e.getAnimations()).map(a => {
  const t = a.effect.getTiming();
  return { delay: t.delay, duration: t.duration, iterations: t.iterations, fill: t.fill,
           kf: a.effect.getKeyframes().map(k => ({ offset: k.computedOffset, transform: k.transform, opacity: k.opacity })) };
})"""


@pytest.mark.parametrize("page", ["gate"], indirect=True)
def test_with_the_music_on_a_fake_clock_the_first_picture_is_up_at_once_and_its_gate_opens_on_a_beat(page):
    """25 Nov: the walk's first picture waited for the next bar line (up to ~2 s over black as the robot set off) -- now
    it is up at once behind the shut gate, which opens on the music's next beat; it stays to a bar line (so every gate
    after it is on one) and the beams pulse from a beat. Owner ~03:25: "แสงก่อน ... เพิ่มโมชั่น อนิเมชั่น"."""
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    page.evaluate("""() => { const t0 = performance.now();
      window.CatwalkMusic._grid(() => ({ pos: 3.1 + (performance.now() - t0) / 1000, dur: 30.476, bpm: 126, off: 0.2, beat: 60 / 126 })); }""")
    _say(page, op="walk")
    page.wait_for_timeout(300)
    log = page.evaluate("() => window.CatwalkMusic.beat().log")
    beams = page.evaluate(ANIMS, ".cw-gallery .cw-beam")
    door = page.evaluate(ANIMS, ".cw-gallery .cw-dl")
    beat = 60 / 126
    bar = 4 * beat
    first = log[0]
    assert first["first"] is True and first["bars"] == 4
    assert 3.1 <= first["cut"] < 3.1 + 0.4                                          # (up now, not on the next line)
    # the gate opens on a beat, the stage up by then (at least 0.35 s on, at most a beat and that)
    assert _on_line(first["gate"], beat) and 0.35 - 1e-6 <= first["gate"] - first["planned"] <= beat + 0.35 + 1e-6
    # its stay ends on a bar line, about four bars on (half a bar either way)
    assert _on_line(first["until"], bar)
    assert abs((first["until"] - first["cut"]) - 4 * bar) <= bar / 2 + 1e-6
    # the beams pulse from a beat, to the next gate and a little past it
    pulse = [a for a in beams if a["iterations"] and a["iterations"] > 1]
    assert len(pulse) == 8 and all(abs(a["duration"] - beat * 1000) < 1 for a in pulse)
    assert all(_on_line(first["planned"] + a["delay"] / 1000, beat) and a["iterations"] >= 12 for a in pulse)
    # the beams' poses: turned in on the gate, a pose on each bar line after it, turned in again on the next gate
    pose = [a for a in beams if a["iterations"] == 1]
    assert len(pose) == 8
    for a in pose:
        start = first["planned"] + a["delay"] / 1000
        assert abs(start - first["gate"]) < 2e-3 and abs(start + a["duration"] / 1000 - first["until"]) < 2e-3
        inner = [start + k["offset"] * a["duration"] / 1000 for k in a["kf"][1:-1]]
        assert inner and all(_on_line(t, bar) for t in inner)
        assert a["kf"][0]["transform"] == a["kf"][-1]["transform"]
    # the doors: shut until just after the gate, then open outward
    (op,) = door
    assert abs(first["planned"] + op["delay"] / 1000 - (first["gate"] + 0.09)) < 2e-3
    assert "-100%" not in op["kf"][0]["transform"] and "-100%" in op["kf"][-1]["transform"]
    _say(page, op="off")


@pytest.mark.parametrize("page", ["gate"], indirect=True)
def test_every_gate_after_the_first_is_on_a_downbeat_and_the_doors_shut_on_its_last_beat(page):
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    # (160 a minute: the shortest phrase barsFor keeps, ~6 s)
    page.evaluate("""() => { const t0 = performance.now();
      window.CatwalkMusic._grid(() => ({ pos: 3.1 + (performance.now() - t0) / 1000, dur: 60, bpm: 160, off: 0.2, beat: 60 / 160 })); }""")
    for _ in range(4):                                  # (the robot's word again, as on a screen back from a blink: this
        _say(page, op="walk", back=True)                # test's LONGEST_MS is 4 s, a first phrase here ~6 s)
        page.wait_for_timeout(2000)
    page.wait_for_function("() => window.CatwalkMusic.beat().log.length >= 2", timeout=3000)
    log = page.evaluate("() => window.CatwalkMusic.beat().log")
    doors = page.evaluate(ANIMS, ".cw-gallery .cw-dl")
    glow = page.evaluate(ANIMS, ".cw-gallery .cw-sglow")
    beat = 60 / 160
    bar = 4 * beat
    second = log[1]
    assert second["first"] is False and abs(second["gate"] - log[0]["until"]) < 1e-6 and _on_line(second["gate"], bar)
    assert 0.8 - 1e-6 <= second["gate"] - second["planned"] <= 0.8 + bar
    assert abs(second["until"] - second["gate"] - second["bars"] * bar) < 1e-6
    close = doors[-1]                                                               # (the newest: this gate's)
    t = [second["planned"] + (close["delay"] + k["offset"] * close["duration"]) / 1000 for k in close["kf"]]
    shut = [k["transform"] for k in close["kf"]]
    assert "-100%" in shut[0] and "-100%" in shut[-1] and "-100%" not in shut[1] and "-100%" not in shut[2]
    assert abs(t[0] - (second["gate"] - 1.5 * beat)) < 2e-3                         # (they close over the last beat and a half)
    assert t[1] < second["gate"] < t[2] and t[2] - t[1] < 0.2                        # (shut a moment round the downbeat)
    peak = glow[-1]                                                                 # (the seam blooms on it)
    tops = [second["planned"] + (peak["delay"] + k["offset"] * peak["duration"]) / 1000 for k in peak["kf"]
            if k["opacity"] is not None and float(k["opacity"]) > 0.5]
    assert tops and all(abs(x - second["gate"]) < 0.05 for x in tops)
    _say(page, op="off")


FORBIDDEN_CSS = ("filter", "blur", "backdrop", "mix-blend", "box-shadow", "will-change", "mask")


def test_the_light_moves_by_transform_and_opacity_only_and_nothing_runs_when_it_is_down(page):
    """The hall's screen is a notebook's integrated GPU, in a dark hall: transform and opacity only, no blur, no blend, no
    mask or clip-path on anything that moves, no will-change left on, no digit drawn; and when it goes down -- off, a line
    (pictures_down), the red stop -- not one animation is left running under it."""
    objs = [{"src": g, "label": w} for g, w in zip(GALLERY, ["Lobby", "Water Lagoon", "Studio"])]
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", objs)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    running = """() => document.getAnimations().filter(a => { const t = a.effect && a.effect.target;
                    return t && t.closest && t.closest('.cw-gallery') && a.playState !== 'finished'; }).length"""
    for down in ("off", "line", "stop"):
        _say(page, op="walk")
        page.wait_for_timeout(700)
        info = page.evaluate("""() => {
          const box = document.querySelector('.cw-gallery'), anims = document.getAnimations().filter(a => a.effect && a.effect.target && box.contains(a.effect.target));
          const props = new Set(), moving = new Set();
          anims.forEach(a => { moving.add(a.effect.target); a.effect.getKeyframes().forEach(k => Object.keys(k).forEach(p => props.add(p))); });
          const clipped = [...moving].filter(e => { const c = getComputedStyle(e); return c.clipPath !== 'none' || (c.maskImage && c.maskImage !== 'none'); }).length;
          const willChange = [...box.querySelectorAll('*')].filter(e => getComputedStyle(e).willChange !== 'auto').length;
          const words = [...box.querySelectorAll('.cw-label')].map(l => l.textContent).join(' ');
          return { n: anims.length, props: [...props], clipped, willChange, words, css: box.querySelector('style').textContent,
                   infinite: anims.filter(a => a.effect.getTiming().iterations === Infinity).length };
        }""")
        assert info["n"] > 20
        assert set(info["props"]) <= {"transform", "opacity", "offset", "computedOffset", "easing", "composite"}, info["props"]
        assert info["clipped"] == 0 and info["willChange"] == 0 and info["infinite"] == 0
        assert not any(w in info["css"] for w in FORBIDDEN_CSS)
        assert info["words"].strip() and not re.search(r"\d", info["words"])
        if down == "off":
            _say(page, op="off")
        elif down == "line":
            page.evaluate("() => window.CatwalkMusic.pictures_down()")
        else:
            page.evaluate("() => window.CatwalkMusic.stop(0.1)")
        page.wait_for_timeout(250)                                                    # (FADE_MS here is 50)
        assert not page.evaluate("() => window.CatwalkMusic.gallery().on")
        assert page.evaluate(running) == 0, down
        assert page.evaluate("() => getComputedStyle(document.querySelector('.cw-gallery')).opacity") == "0"
        _say(page, op="off")


def test_the_pictures_wait_for_the_debut_on_the_same_screen_to_end(page):
    """Owner 25 Nov ~03:10, rehearsal 4: "คลิปโลโก้ยังไม่ทันจบเลย โดนตัดไปก่อน" -- the walk's music starts on the
    debut's hit, and the pictures came over the logo bursting in. Now: while CeremonyShow says a debut is up the walk's
    pictures wait; when it is over they come; the hit, off or a line during the wait drop it."""
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.evaluate("() => { window.__rv = { phase: 'hit' }; window.CeremonyShow = { state: () => ({ reveal: window.__rv }) }; }")
    g = lambda: page.evaluate("() => window.CatwalkMusic.gallery()")              # noqa: E731
    _say(page, op="walk")
    page.wait_for_timeout(900)
    assert not g()["on"] and g()["waiting"]                                        # (the logo plays on)
    page.evaluate("() => { window.__rv = null; }")                                # (the debut over: the logo held)
    page.wait_for_timeout(500)
    assert g()["on"] and not g()["waiting"]
    _say(page, op="off")
    # a hit (the handshake) while still waiting: no pictures after it
    page.evaluate("() => { window.__rv = { phase: 'settle' }; }")
    _say(page, op="walk")
    page.wait_for_timeout(400)
    assert g()["waiting"]
    _say(page, op="hit")
    page.evaluate("() => { window.__rv = null; }")
    page.wait_for_timeout(700)
    assert not g()["on"] and not g()["waiting"]
    # a line of the script during the wait: dropped the same way
    page.evaluate("() => { window.__rv = { phase: 'build' }; }")
    _say(page, op="walk")
    page.evaluate("() => window.CatwalkMusic.pictures_down()")
    page.evaluate("() => { window.__rv = null; }")
    page.wait_for_timeout(700)
    assert not g()["on"] and not g()["waiting"]
    page.evaluate("() => { delete window.CeremonyShow; }")
    _say(page, op="off")


def test_her_line_right_after_the_handshake_silences_the_hits_sting(page):
    """Rehearsal 5 (25 Nov ~03:18): the handshake's `hit` rings its 2.6 s sting at full level and her hello to Khun Miki
    starts a second later -- now a duck after the hit takes the sting down too, and the next walk brings the level back."""
    _ready(page)
    _say(page, op="walk", track="walk")
    page.wait_for_timeout(600)
    _say(page, op="hit")
    assert _state(page)["duck_gain"] >= 0.97                          # (the sting rings: this page's is 0.2 s, the real 2.6 s)
    _say(page, op="duck")
    page.wait_for_timeout(700)
    assert _state(page)["duck_gain"] <= 0.01                          # (her line: silent)
    _say(page, op="walk", track="guests")
    page.wait_for_timeout(700)
    s = _state(page)
    assert s["playing"] == "guests" and s["duck_gain"] >= 0.97
    _say(page, op="off")



def test_a_screen_back_after_a_hit_keeps_the_pictures_lingering_but_not_after_a_stop(on, monkeypatch):
    """Review 25 Nov: a blink during Khun Miki's handshake took the pictures the hit lets linger down for the rest of it.
    Within CATWALK_LINGER_S of a hit the rejoining screen is told `off` + `linger`; after a red stop, or past it, plain off."""
    import time as _time

    _call()
    face_mirror.catwalk("walk")
    face_mirror.catwalk("hit")
    assert json.loads(face_mirror.snapshot()[-1]) == {"type": "catwalk", "op": "off", "linger": True}
    real = _time.monotonic
    monkeypatch.setattr(face_mirror.time, "monotonic", lambda: real() + face_mirror.CATWALK_LINGER_S + 1)
    assert json.loads(face_mirror.snapshot()[-1]) == {"type": "catwalk", "op": "off"}
    monkeypatch.setattr(face_mirror.time, "monotonic", real)        # (not undo(): that also takes the switch off)
    _call()
    face_mirror.catwalk("walk")
    face_mirror.catwalk("hit")
    face_mirror.subtitle_cut()                                         # (the red stop)
    assert json.loads(face_mirror.snapshot()[-1]) == {"type": "catwalk", "op": "off"}


def test_the_page_keeps_lingering_pictures_on_an_off_with_linger(page):
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    g = lambda: page.evaluate("() => window.CatwalkMusic.gallery()")              # noqa: E731
    _say(page, op="walk")
    page.wait_for_timeout(300)
    _say(page, op="hit")
    _say(page, op="off", linger=True)                                  # (the screen came back during the handshake)
    assert g()["on"] and g()["lingering"]
    _say(page, op="off")
    assert not g()["on"]


def test_failed_reconnects_do_not_keep_restarting_the_lost_clock(page):
    """Review 25 Nov: every failed reconnect (2-30 s apart) restarted the 45 s clock -- the loop played to its 12-minute
    cap while the robot's server was away. The clock runs from the first close."""
    src = page.evaluate("() => window.CatwalkMusic.lost.toString()")
    assert "if (lostT) return;" in src and "lostT = null;" in src


def test_the_red_stop_cuts_the_hits_sting(page):
    """Review 25 Nov: the sting is not the loop, and stop() did not touch it -- 0.27 loud 200 ms after the red stop."""
    _ready(page)
    _say(page, op="walk")
    page.wait_for_timeout(300)
    _say(page, op="hit")
    assert _state(page)["sting"] is True
    page.evaluate("() => window.CatwalkMusic.stop(0.4)")
    assert _state(page)["sting"] is False



def test_a_black_debuts_press_takes_the_pictures_down_in_the_same_frame(page):
    """Review 25 Nov: the walk's pictures lingering at a black debut's press faded over the dark hall for ~1.1 s."""
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    _say(page, op="walk")
    page.wait_for_timeout(1500)
    _say(page, op="hit")                                               # (lingering)
    got = page.evaluate("""() => { window.CatwalkMusic.pictures_down(true);
      const b = document.querySelector('.cw-gallery'), c = getComputedStyle(b);
      return { on: window.CatwalkMusic.gallery().on, opacity: +c.opacity, transition: c.transitionDuration }; }""")
    assert got["on"] is False and got["opacity"] == 0 and got["transition"] in ("0s", "")
    page.wait_for_timeout(200)
    assert page.evaluate("() => getComputedStyle(document.querySelector('.cw-gallery')).transitionDuration") != "0s"   # (a later walk fades in)



def test_the_owners_pick_is_the_film_look(page):
    """Owner 25 Nov ~05:00, after the three previews (Downloads/walk-looks): "เอา 2" -- the film: the pictures edge to edge,
    no frame, wall or doors; the light on the music."""
    assert "var WALK_LOOK = 'film';" in ENGINE.read_text(encoding="utf-8")
    page.evaluate("(g) => window.CatwalkMusic.files({ walk: '/catwalk/walk?v=1', guests: '/catwalk/guests?v=1', hit: '/catwalk/hit?v=1' }, g)", GALLERY)
    page.wait_for_function("() => window.CatwalkMusic.state().ready.length === 3")
    _say(page, op="walk")
    page.wait_for_timeout(1200)
    got = page.evaluate("""() => { const b = document.querySelector('.cw-gallery');
      return { cls: b.className, doors: document.querySelectorAll('.cw-door, .cw-wall').length, on: window.CatwalkMusic.gallery().on,
               pics: document.querySelectorAll('.cw-gallery img').length, opacity: +getComputedStyle(b).opacity }; }""")
    assert got["on"] and "cw-film" in got["cls"] and got["doors"] == 0 and got["pics"] >= 1 and got["opacity"] > 0.5
    _say(page, op="off")
    page.wait_for_timeout(200)
    assert page.evaluate("() => document.getAnimations().filter(a => a.playState === 'running').length") == 0
