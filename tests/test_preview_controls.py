"""The ceremony preview's buttons (owner 18 Nov 2026: "ตอนเล่นมันกดกลับกดหยุดไม่ได้หรอ จะได้เช็ค").

They live in the preview's own process only: the day's room-screen app must never carry
them, and a pause must stop the line's clock (a pause that kept counting would end the line
-- and take its slide down -- under the watcher's eyes)."""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _preview():
    spec = importlib.util.spec_from_file_location("preview_ceremony_show", ROOT / "scripts" / "preview_ceremony_show.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("preview_ceremony_show", mod)
    spec.loader.exec_module(mod)
    return mod


_real_sleep = asyncio.sleep


async def _fast_sleep(seconds, *a, **k):
    await _real_sleep(min(seconds, 0.01))


CUES = [{"id": "a", "en": "one"}, {"id": "b", "en": "two"}, {"id": "c", "en": "three"}]


def test_a_pause_stops_the_waiting_clock():
    pv = _preview()
    ctl = pv.Controls(CUES)

    async def go():
        ctl.set_paused(True)
        waiting = asyncio.create_task(ctl.wait(seconds=0.3))
        await asyncio.sleep(0.6)
        held = waiting.done()               # paused: 0.3 s of unpaused time never came
        ctl.set_paused(False)
        await asyncio.wait_for(waiting, 2)
        return held

    assert asyncio.run(go()) is False
    assert ctl.paused_s() >= 0.5


def test_a_jump_ends_the_wait_and_goto_is_clamped():
    pv = _preview()
    ctl = pv.Controls(CUES)
    ctl.press("goto", 99)
    assert asyncio.run(ctl.wait(seconds=5)) == ("goto", 2)
    ctl.press("goto", "x")                  # not a number: no jump
    assert ctl.jump is None
    ctl.press("prev")
    assert asyncio.run(ctl.wait()) == ("prev", None)
    ctl.press("delete-everything")
    assert ctl.jump is None


def test_the_controls_are_injected_into_the_previews_copy_only():
    pytest.importorskip("httpx", reason="TestClient needs httpx: the injection is NOT checked")
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.testclient import TestClient

    pv = _preview()
    app = FastAPI()

    @app.get("/mirror")
    async def page():
        r = HTMLResponse("<!doctype html><html><head><title>x</title></head><body></body></html>")
        r.set_cookie("emma_mirror", "v")
        return r

    ctl = pv.Controls(CUES)
    pv.add_controls(app, ctl)
    client = TestClient(app)
    r = client.get("/mirror", headers={"If-None-Match": "x"})
    assert r.status_code == 200
    assert r.text.startswith('<!doctype html><html><head><script src="/preview-ctl.js"></script>'
                             '<script src="/preview-host-caption.js"></script><title>')
    assert "__pvHostCaption" in client.get("/preview-host-caption.js").text
    assert r.headers.get("set-cookie", "").startswith("emma_mirror=")    # the gate's cookie survives
    assert int(r.headers["content-length"]) == len(r.content)
    assert "pausedAt" in client.get("/preview-ctl.js").text
    s = client.post("/preview-ctl", json={"cmd": "pause"}).json()
    assert s["paused"] is True and s["id"] == "a" and s["n"] == 3      # (a 422 here once: Request unresolved)
    assert client.post("/preview-ctl", json={"cmd": "play"}).json()["paused"] is False


def test_the_days_room_screen_app_has_no_preview_routes():
    from app import mirror_server

    paths = [getattr(r, "path", "") for r in mirror_server.app.routes]
    assert not [p for p in paths if p.startswith("/preview")]
    assert "preview-ctl" not in (ROOT / "client" / "voice-preview.html").read_text(encoding="utf-8")


def test_khun_mikis_line_is_sound_alone_never_a_subtitle(monkeypatch):
    """Review 18 Nov: sent as a subtitle with no slide, his line made the page clear the stage --
    the preview and the recorded scenes showed a cut between his question and her answer that the
    hall never sees. His voice goes out as audio only; his words go to the preview's caption."""
    from app import face_mirror

    pv = _preview()
    sent, captions = [], []
    monkeypatch.setattr(face_mirror, "subtitle", lambda cue: sent.append(("subtitle", cue)))
    monkeypatch.setattr(face_mirror, "subtitle_end", lambda *a, **k: sent.append(("end", a)))
    monkeypatch.setattr(face_mirror, "publish_json", lambda m: sent.append(("json", m)))
    monkeypatch.setattr(face_mirror, "publish_audio", lambda b: sent.append(("audio", len(b))))
    ctl = pv.Controls(CUES)
    pcm = bytes(2) * (pv.RATE // 2)                     # half a second of his voice
    her = []

    async def her_line(cue, pcm, **kw):                # (her line itself is play()'s own business)
        her.append(cue["id"])

    monkeypatch.setattr(pv, "play", her_line)
    monkeypatch.setattr(pv.asyncio, "sleep", _fast_sleep)
    asyncio.run(pv.exchange({"id": "a", "th": "x", "en": "x"}, b"", {"text": "สวัสดีเอ็มม่า", "pcm": pcm}, ctl,
                            on_caption=captions.append))
    assert her == ["a"]
    kinds = {k for k, _ in sent}
    assert kinds == {"audio"}, kinds
    assert sum(n for k, n in sent if k == "audio") == len(pcm)
    assert captions == ["สวัสดีเอ็มม่า", ""] and ctl.caption == ""
