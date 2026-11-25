"""The Emma Ceremony app under a mic hold, on the real page in Chromium.

Robot, 23 Nov: a tap at 19:55 held the mic to 06:00 (MIC_HOLD_PERSIST). The ceremony app opened with
no call -- the standing line skips a held page and the page does not dial by itself -- so there was no
ceremony mode and the opening refused "เปิดแอป Emma Ceremony บนหุ่นก่อน"; it came up only after the mic
was tapped on the robot (`mic_hold on:false why:pressed` 22:25:19). That tap had cleared the hold, so
after the hand-back the Event call came up live with the mic open and was re-muted by hand (22:47:52).

The page half: the ceremony app's page (`?app=1&ceremony=1`) answers the standing line's bell under the
server's hold and dials with its mic open, says whose bell it is, and writes nothing of the hold; the
Event app's page under the same hold is what it was. The server half is in tests/test_standing.py.
"""
from __future__ import annotations

import json

import pytest

from tests.test_voice_preview_page import _READY, _chromium_face  # noqa: F401  (`_chromium_face` is a fixture)

pytestmark = pytest.mark.timeout(120)

# Per socket: the bell's text messages, the call's binary frames (cam=1 also opens /ws/camera, whose
# frames are binary too -- the page-wide `__sent` cannot tell them apart).
_TAP_SOCKETS = r"""
(() => {
  const Base = window.WebSocket;
  class Tap extends Base {
    constructor(url) {
      super(url);
      if (/\/ws\/wake/.test(String(url))) { window.__bellWS = this; this.__said = []; }
      if (/\/ws\?/.test(String(url))) { window.__callFrames = 0; this.__call = true; }
    }
    send(data) {
      if (this.__said && typeof data === 'string') this.__said.push(JSON.parse(data));
      if (this.__call && typeof data !== 'string') window.__callFrames++;
      return super.send(data);
    }
  }
  Tap.CONNECTING = 0; Tap.OPEN = 1; Tap.CLOSING = 2; Tap.CLOSED = 3;
  window.WebSocket = Tap;
})();
"""
_LABEL = "() => document.querySelector('.control.mic .control-label').textContent"
_HOLD = {"on": True, "since": 1795000000, "until": 1795040000}     # 19:55 to 06:00, as the server says it


def _open(chromium, which):
    browser, port, fake_socket = chromium
    ctx = browser.new_context(viewport={"width": 1080, "height": 1920}, permissions=["microphone"])
    ctx.add_init_script(fake_socket)
    ctx.add_init_script(_TAP_SOCKETS)
    page = ctx.new_page()
    page.goto("http://127.0.0.1:%d/preview?app=1&cam=1&cue=0&%s=1" % (port, which))
    page.wait_for_function("() => window.EmmaCall && window.__bellWS && window.__bellWS.readyState === 1",
                           timeout=10000)
    page.wait_for_timeout(1200)                                      # /voices + /health
    # the server's first frame on the bell: the stored hold (app/wake.py hello)
    page.evaluate("(h) => window.__bellWS.onmessage({data: JSON.stringify({type: 'standby_only', hold: h})})",
                  _HOLD)
    return ctx, page


def _ring(page, reason):
    page.evaluate("(r) => window.__bellWS.onmessage({data: JSON.stringify({type: 'wake', reason: r})})", reason)
    page.wait_for_timeout(600)


def test_the_ceremony_page_dials_under_the_hold_with_its_mic_open(_chromium_face):  # noqa: F811
    ctx, page = _open(_chromium_face, "ceremony")
    try:
        said = page.evaluate("() => window.__bellWS.__said")
        assert said == [{"type": "hold", "on": False, "press": False, "ceremony": True}], said
        assert page.evaluate("() => window.EmmaCall.held()") is False, "the server's hold is not this app's"
        _ring(page, "standing_line")
        call = page.evaluate("() => window.__callWS && window.__callWS.url")
        assert call and "profile=ceremony" in call and "summoned=1" in call, call
        page.evaluate(_READY.replace("profile: 'condo'", "profile: 'ceremony'"))
        page.wait_for_timeout(1200)                                  # getUserMedia, the worklet, frames
        assert page.evaluate(_LABEL) == "Listening"
        assert page.evaluate("() => window.__callFrames") > 0, "the mic is open: the ceremony keeps it deaf itself"
        sent = [json.loads(m) for m in page.evaluate("() => window.__sent || []") if m != "[binary]"]
        assert not any(m.get("why") in ("hold_waiting", "mute_tap") for m in sent), sent
        # nothing of the hold written here: no press went up, no key in this WebView
        assert page.evaluate("() => window.__bellWS.__said.length") == 1
        assert page.evaluate("() => [sessionStorage.getItem('emma-mute-hold'), "
                             "sessionStorage.getItem('emma-ceremony-mute-hold')]") == [None, None]
        # the call ends (the hand-back closes this app): at rest, not "Muted", and the next ring redials
        page.evaluate("() => window.__callWS.onclose()")
        page.wait_for_timeout(300)
        assert page.evaluate(_LABEL) == "Tap to talk"
    finally:
        ctx.close()


def test_the_event_page_under_the_hold_is_what_it_was(_chromium_face):  # noqa: F811
    ctx, page = _open(_chromium_face, "event")
    try:
        said = page.evaluate("() => window.__bellWS.__said")
        assert said == [{"type": "hold", "on": True, "press": False}], said
        assert page.evaluate(_LABEL) == "Muted"
        _ring(page, "standing_line")
        assert page.evaluate("() => !!window.__callWS") is False, "the standing line does not open a held page"
        _ring(page, "event_walk")                                    # a staff press: a muted line (10 Nov)
        assert page.evaluate("() => !!window.__callWS && window.__callWS.url.indexOf('profile=event') >= 0")
        page.evaluate(_READY.replace("profile: 'condo'", "profile: 'event'"))
        page.wait_for_timeout(1200)
        assert page.evaluate(_LABEL) == "Muted"
        assert page.evaluate("() => window.__callFrames") == 0
        assert page.evaluate("() => sessionStorage.getItem('emma-mute-hold')") == "1"
    finally:
        ctx.close()
