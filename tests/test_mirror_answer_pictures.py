"""The room screen shows the answer pictures the robot shows, not only stories.

Robot log 28 Oct (staff testing with the robot's screen on the media room's
TV, "บางคำถามมีรูป แต่ทำไมอยู่ๆก็ไม่มี"): an answer that put a slide on the
robot's screen (ANSWER_IMAGES, search_condo_info -> `slide` on the tool_result)
put nothing on the TV -- app/face_mirror.view passed `screen: story` and
`screen: clear` only, and the mirror listener had no route to the deck's
images anyway (they live behind WS_TOKEN on the call server).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import face_mirror
from app.config import settings

PAGE = Path(__file__).resolve().parent.parent / "client" / "voice-preview.html"
SLIDE = {"id": "intro-002", "project_id": "embassy_world", "source_id": "slide_catalog",
         "url": "/slides/intro_002.jpg", "title_th": "ประวัติผู้พัฒนา", "title_en": "Developer",
         "summary_th": "approved words that stay home", "policy_trace": {"x": 1},
         "asset_policy_trace": {"y": 2}, "capabilities": {"display": True}}


def test_an_answer_picture_reaches_the_room_screen_rebuilt():
    payload = {"type": "tool_result", "name": "search_condo_info",
               "result": {"found": True, "heard": "the guest's words", "note": "x"},
               "slide": SLIDE, "displays": 0}
    out = face_mirror.view(payload)
    assert out == {"type": "tool_result", "result": {"screen": "slide"},
                   "slide": {"url": "/slides/intro_002.jpg", "title_th": "ประวัติผู้พัฒนา",
                             "title_en": "Developer"}}
    assert "heard" not in json.dumps(out) and "summary" not in json.dumps(out)


@pytest.mark.parametrize("url", ["/slides/../.env", "http://evil.example/x.jpg", "/story-asset/abc",
                                 "/slides/index.json", "/slides/a/b.jpg", "", None, 3])
def test_only_a_deck_image_address_passes(url):
    out = face_mirror.view({"type": "tool_result", "result": {"found": True},
                            "slide": {**SLIDE, "url": url}})
    assert out is None


def test_a_story_still_wins_its_answer():
    story = {"type": "tool_result", "result": {"ok": True, "screen": "story", "topic": "t",
                                               "story": {"template": "stack"}}, "slide": SLIDE}
    assert "slide" not in face_mirror.view(story)


def test_a_screen_that_joins_mid_answer_gets_the_picture():
    face_mirror.reset()
    face_mirror.call_started()
    face_mirror.publish_json({"type": "tool_result", "result": {"found": True}, "slide": SLIDE})
    last = json.loads(face_mirror.snapshot()[-1])
    assert last["slide"]["url"] == "/slides/intro_002.jpg"
    face_mirror.publish_json({"type": "speech_started"})       # a new question owns the screen
    assert "tool_result" not in [json.loads(x)["type"] for x in face_mirror.snapshot()]
    face_mirror.reset()


@pytest.fixture
def mirror_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import mirror_server
    from app.tools import slides

    (tmp_path / "intro_002.jpg").write_bytes(b"\xff\xd8\xffJPEG")
    (tmp_path / "intro_003.jpg").write_bytes(b"\xff\xd8\xffJPEG")
    (tmp_path / "index.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "mirror_token", "room-secret")
    monkeypatch.setattr(settings, "slides_dir", str(tmp_path))
    monkeypatch.setattr(slides, "load_slides", lambda: [{"id": "intro-002", "file": "intro_002.jpg"},
                                                        {"id": "intro-003", "file": "intro_003.jpg"}])
    # the display policy: intro-003 is not cleared for the screen
    monkeypatch.setattr(slides, "display_payload",
                        lambda c: {"url": "/slides/x"} if c.get("id") == "intro-002" else None)
    face_mirror.reset()
    with TestClient(mirror_server.app) as client:
        yield client
    face_mirror.reset()


def test_the_room_screen_serves_the_picture_behind_its_cookie(mirror_client):
    assert mirror_client.get("/slides/intro_002.jpg").status_code == 403      # no cookie yet
    mirror_client.get("/mirror?token=room-secret")
    got = mirror_client.get("/slides/intro_002.jpg")
    assert got.status_code == 200 and got.content == b"\xff\xd8\xffJPEG"
    assert "no-store" in got.headers.get("cache-control", "")


def test_only_pictures_the_display_policy_allows(mirror_client):
    mirror_client.get("/mirror?token=room-secret")
    assert mirror_client.get("/slides/intro_003.jpg").status_code == 404     # policy says no
    assert mirror_client.get("/slides/index.json").status_code == 404        # not an image
    assert mirror_client.get("/slides/missing.jpg").status_code == 404       # not in the deck
    assert mirror_client.get("/slides/..%2F.env").status_code == 404


def test_the_page_draws_it_where_the_stories_draw():
    """In the robot's own screen frame, cleared like a story -- not the
    face's stage (openStage), whose app-shell rules move Emma and her words."""
    html = PAGE.read_text(encoding="utf-8")
    show = html.split("    function showSlide(slide) {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (mirror) { showMirrorSlide(slide); return; }" in show
    assert show.index("showMirrorSlide") < show.index("openStage()")
    body = html.split("    function showMirrorSlide(slide) {", 1)[1].split("\n    }\n", 1)[0]
    assert "mirrorStage()" in body and ".vp-stage-inner" in body
    assert "classList.add('mirror-story')" in body and "answerStaged = true" in body
    assert "token" not in body, "the room screen's pictures ride its cookie, never a token"
