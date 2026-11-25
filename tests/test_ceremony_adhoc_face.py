"""The ceremony page's "ตอบสด" on the REAL robot face page (owner 23 Nov: "the picture stays until the next
line is pressed"). Its own module: it runs tests/test_stories.py's headless Chromium with the face's fake
socket, and one sync Playwright per module (tests/test_ceremony_adhoc.py has the staff page's).
The server side -- the mark, the press, the clear through session._story_picture, the event screen's copy
-- is tests/test_ceremony_adhoc.py.
"""
from __future__ import annotations

import pytest

from tests.test_stories import _chromium, face  # noqa: F401 -- the REAL robot face page in Chromium


@pytest.mark.timeout(120)
def test_on_the_real_face_her_picture_stays_under_a_line_until_the_clear(face):
    """The REAL robot face page (tests/test_stories.py `face`): why the server must send the clear. The
    host speaks, her live answer puts a story up; a recorded line with no `show` of its own then plays --
    her voice, no guest before it -- and the story stays (the page clears an old picture at a new answer's
    first sound only after a guest spoke). The press's clear takes it down."""
    from tests.test_stories import STACK, _story

    page = face
    page.evaluate("() => window.__emit({type: 'speech_started'})")          # the host, inside the window
    _story(page, STACK, settle=False)
    page.evaluate("() => { window.__emit({type: 'assistant_transcript', text: 'We have three room types.'});"
                  " window.__audio(600, 0.09, 0); }")
    page.wait_for_timeout(900)
    page.evaluate("() => window.__emit({type: 'turn_complete'})")
    page.wait_for_timeout(300)
    up = "() => !!document.querySelector('.es-root') && document.body.classList.contains('emma-story')"
    assert page.evaluate(up)
    # the next scripted line, recorded, no picture of its own: an interrupted, then her voice
    page.evaluate("() => { window.__emit({type: 'interrupted'});"
                  " window.__emit({type: 'assistant_transcript', text: 'Now let me show you the lobby.'});"
                  " window.__audio(800, 0.09, 1); }")
    page.wait_for_timeout(1200)
    page.evaluate("() => window.__emit({type: 'turn_complete'})")
    page.wait_for_timeout(300)
    assert page.evaluate(up), "the line alone does not take her picture down"
    # the press's clear (ceremony._clear_adhoc_picture -> session._story_picture {"clear": True})
    page.evaluate("() => window.__emit({type: 'tool_result', name: 'event_cue', result: {screen: 'clear'}})")
    page.wait_for_timeout(600)
    assert not page.evaluate("() => document.body.classList.contains('emma-story')")
    assert not page.evaluate("() => document.body.classList.contains('emma-staging')")
