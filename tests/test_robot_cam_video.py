"""scripts/robot_cam_video.py: the head camera's video, recorded on the PC from
the face page's own stream (owner 4 Nov 2026: "บันทึกวิดีโอ")."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("robot_cam_video", ROOT / "scripts" / "robot_cam_video.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def test_only_the_face_pages_websocket_is_taken_never_its_url():
    """The page URL carries the WS token: the script keeps the DevTools
    websocket address only, and never prints a page URL."""
    pages = [{"type": "service_worker", "url": "https://127.0.0.1:8001/sw.js", "webSocketDebuggerUrl": "ws://x/sw"},
             {"type": "page", "url": "https://127.0.0.1:8001/preview?app=1&cam=1&token=SECRET",
              "webSocketDebuggerUrl": "ws://127.0.0.1:9333/devtools/page/AB"}]
    assert cv.page_socket(pages) == "ws://127.0.0.1:9333/devtools/page/AB"
    with pytest.raises(SystemExit):
        cv.page_socket(pages[:1])
    source = (ROOT / "scripts" / "robot_cam_video.py").read_text(encoding="utf-8")
    assert '["url"]' not in source and "p['url']" not in source, "nothing reads a page URL out to print"


def test_the_recorder_uses_the_pages_own_stream_and_caps_what_it_holds():
    """No second camera (the page's stream feeds /ws/camera and the greeter),
    and a stalled PC drops chunks instead of growing the WebView's memory."""
    assert "window.__emmaCamStream" in cv.START_JS and "getUserMedia" not in cv.START_JS
    assert "st.chunks.length >= %d" % cv.MAX_PAGE_CHUNKS in cv.START_JS
    assert 0 < cv.MAX_PAGE_CHUNKS * cv.CHUNK_S <= 180, "at most a few minutes held in the page"
    assert "rec.start(%d)" % (cv.CHUNK_S * 1000) in cv.START_JS
    assert "st.rec.stop()" in cv.STOP_JS


def test_the_video_stays_in_the_gitignored_folder(tmp_path, monkeypatch):
    """The pictures have people in them: .tmp/walk-video only, and the name
    cannot walk out of it."""
    assert cv.OUT_DIR == ROOT / ".tmp" / "walk-video"
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".tmp/" in gitignore
    for bad in ("../x", "a/b", "a\\b", "x.webm"):
        with pytest.raises(SystemExit):
            cv.main(["stop", bad])
    monkeypatch.setattr(cv, "OUT_DIR", tmp_path)
    assert cv.main(["stop", "lobby_1104"]) == 0
    assert (tmp_path / "lobby_1104.webm.stop").exists()


def test_without_ffmpeg_the_webm_is_left_as_it_is(tmp_path, monkeypatch):
    webm = tmp_path / "a.webm"
    webm.write_bytes(b"\x1a\x45\xdf\xa3")
    monkeypatch.setattr(cv, "ffmpeg_exe", lambda: None)
    assert cv.to_mp4(webm) == []
    assert webm.exists() and not (tmp_path / "a.mp4").exists()
