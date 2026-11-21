"""Real mouse input on Windows: move, click, double/right click, drag.

All coordinates are *physical* screen pixels — the same space UI
Automation's ``BoundingRectangle`` and ``GetCursorPos`` use (Qt overlays
convert through :mod:`.screen_map`). Tests inject a fake with this API.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Tuple

_LEFTDOWN, _LEFTUP = 0x0002, 0x0004
_RIGHTDOWN, _RIGHTUP = 0x0008, 0x0010
_MIDDLEDOWN, _MIDDLEUP = 0x0020, 0x0040

_BUTTONS = {
    "left": (_LEFTDOWN, _LEFTUP),
    "right": (_RIGHTDOWN, _RIGHTUP),
    "middle": (_MIDDLEDOWN, _MIDDLEUP),
}
BUTTONS = ("left", "right", "middle", "double")


class Win32Mouse:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("mouse control is only supported on Windows")
        import ctypes.wintypes as wt

        self._wt = wt
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def position(self) -> Tuple[int, int]:
        pt = self._wt.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move(self, x: int, y: int) -> None:
        self._user32.SetCursorPos(int(x), int(y))

    def _event(self, flag: int) -> None:
        self._user32.mouse_event(flag, 0, 0, 0, 0)

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click at (x, y). ``button`` is left, right, middle or double."""

        self.move(x, y)
        time.sleep(0.02)
        if button == "double":
            down, up = _BUTTONS["left"]
            for _ in range(2):
                self._event(down)
                self._event(up)
                time.sleep(0.03)
            return
        down, up = _BUTTONS[button]
        self._event(down)
        time.sleep(0.02)
        self._event(up)

    def drag(self, start: Tuple[int, int], end: Tuple[int, int]) -> None:
        """Press at ``start``, glide to ``end`` and release."""

        down, up = _BUTTONS["left"]
        self.move(*start)
        time.sleep(0.05)
        self._event(down)
        steps = 12
        (x0, y0), (x1, y1) = start, end
        for i in range(1, steps + 1):
            time.sleep(0.015)
            self.move(x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
        time.sleep(0.05)
        self._event(up)
