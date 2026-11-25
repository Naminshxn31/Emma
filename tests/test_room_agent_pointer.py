"""The hall's screen shows no mouse pointer (owner 25 Nov: "จอด้านข้างตอนเปิดตัวยังมีเมาท์"): the page says cursor: none, but
Windows asks a window for its cursor only when the mouse moves -- app/room_agent._hide_pointer moves it, over the page, to
its corner, and only when the pointer is on that window. Fake user32: nothing moves a real pointer."""
import ctypes
import sys
import types

import pytest

from app import room_agent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only -- NOT checked here: the pointer parking")


class User32:
    def __init__(self, rect, at):
        self.rect, self.at, self.moves = rect, at, []

    def GetWindowRect(self, hwnd, ref):
        r = ref._obj
        r.left, r.top, r.right, r.bottom = self.rect
        return 1

    def GetCursorPos(self, ref):
        ref._obj.x, ref._obj.y = self.at
        return 1

    def SetCursorPos(self, x, y):
        self.moves.append((x, y))
        return 1

    def SetThreadDpiAwarenessContext(self, ctx):
        value = -4 if ctx.value == ctypes.c_void_p(-4).value else ctx.value      # (a handle: -4 reads back unsigned)
        self.contexts = getattr(self, "contexts", []) + [value]
        return 1 if value == -4 else 0                  # (the thread's previous context, handed back after)


def _with(monkeypatch, user32):
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(user32=user32), raising=False)


def test_the_pointer_on_the_page_goes_to_its_corner_in_two_moves(monkeypatch):
    u = User32((0, 0, 1920, 1080), (900, 500))
    _with(monkeypatch, u)
    assert room_agent._hide_pointer(1, after_s=0, again=()) is True
    assert u.moves == [(1917, 1077), (1918, 1078)]
    assert u.contexts == [-4, 1]                        # (physical pixels while it reads and moves; the old one back)


def test_a_pointer_on_another_screen_is_left_alone(monkeypatch):
    u = User32((1920, 0, 3840, 1080), (900, 500))                       # (the page on the second screen)
    _with(monkeypatch, u)
    assert room_agent._hide_pointer(1, after_s=0, again=()) is False and u.moves == []


def test_it_runs_after_the_window_is_brought_up():
    import inspect

    src = inspect.getsource(room_agent._bring_to_front)
    assert src.index("user32.SetForegroundWindow(hwnd)") < src.index("_hide_pointer(hwnd)")


def test_it_parks_again_later_while_the_pointer_is_still_on_the_window(monkeypatch):
    """Review 25 Nov: at 4 s the page may not have painted yet -- parked again at ~12 s and ~30 s, only while on it."""
    u = User32((0, 0, 1920, 1080), (900, 500))
    _with(monkeypatch, u)
    assert room_agent.POINTER_PARK_AGAIN_S == (8.0, 18.0)
    assert room_agent._hide_pointer(1, after_s=0, again=(0, 0)) is True
    assert len(u.moves) == 6                            # (three parkings: the pointer stays on the window, in its corner)
    u2 = User32((0, 0, 1920, 1080), (2500, 500))        # (somebody moved it to the other screen meanwhile)
    _with(monkeypatch, u2)
    assert room_agent._hide_pointer(1, after_s=0, again=(0, 0)) is False and u2.moves == []
