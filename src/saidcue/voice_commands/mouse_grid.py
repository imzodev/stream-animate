"""The mouse grid: point anywhere on screen by voice.

A 3×3 numbered grid covers the screen (or a window). Saying a number
zooms into that cell, and the mouse follows its centre; a few numbers
reach any pixel. Then "click", "right click", "double click", "move", or
"mark" + "drag here" act at that point. Works on anything — games,
canvases, remote desktops — because it never asks UI Automation what is
there.

:class:`GridState` is the pure geometry; :class:`GridMode` reads the
user's answers (see :mod:`.modes`).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from .modes import Mode, is_cancel
from .parser import normalize

Rect = Tuple[int, int, int, int]  # left, top, width, height (physical px)

MIN_CELL = 6  # stop zooming once a cell is this small


class GridState:
    """The current grid rectangle and how we got there (for "back")."""

    def __init__(self, rect: Rect) -> None:
        self.rect: Rect = tuple(int(v) for v in rect)  # type: ignore[assignment]
        self._history: List[Rect] = []

    def cell(self, n: int) -> Rect:
        """Rectangle of cell ``n`` (1-9, reading order)."""

        if not 1 <= n <= 9:
            raise ValueError("grid cells are 1-9")
        left, top, w, h = self.rect
        row, col = divmod(n - 1, 3)
        x0 = left + w * col // 3
        x1 = left + w * (col + 1) // 3
        y0 = top + h * row // 3
        y1 = top + h * (row + 1) // 3
        return x0, y0, x1 - x0, y1 - y0

    def can_zoom(self) -> bool:
        return self.rect[2] // 3 >= MIN_CELL and self.rect[3] // 3 >= MIN_CELL

    def zoom(self, n: int) -> Rect:
        self._history.append(self.rect)
        self.rect = self.cell(n)
        return self.rect

    def back(self) -> bool:
        if not self._history:
            return False
        self.rect = self._history.pop()
        return True

    def reset(self, rect: Rect) -> None:
        self.rect = tuple(int(v) for v in rect)  # type: ignore[assignment]
        self._history = []

    def center(self) -> Tuple[int, int]:
        left, top, w, h = self.rect
        return left + w // 2, top + h // 2


_GRID_DIGITS = {
    "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4,
    "for": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ate": 8, "nine": 9,
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9,
}  # fmt: skip
_ACTIONS = {
    "click": "left", "left click": "left", "click there": "left", "click here": "left",
    "right click": "right", "double click": "double", "middle click": "middle",
    "move": "move", "move there": "move", "move here": "move", "go there": "move",
    "mouse here": "move", "done": "move",
    "mark": "mark", "mark here": "mark", "mark there": "mark", "start drag": "mark",
    "drag here": "drop", "drag there": "drop", "drop here": "drop", "drop there": "drop",
    "drag to here": "drop",
    "back": "back", "go back": "back", "undo": "back", "zoom out": "back",
}  # fmt: skip


def _digits(words: List[str]) -> Optional[List[int]]:
    """ "5", "5 3", "53", "five three" → [5, 3]; ``None`` if not all digits 1-9."""

    out: List[int] = []
    for word in words:
        if word.isdigit():
            if "0" in word:
                return None
            out.extend(int(ch) for ch in word)
        elif word in _GRID_DIGITS:
            out.append(_GRID_DIGITS[word])
        else:
            return None
    return out or None


def parse_grid(text: str):
    """``("zoom", [5, 3])``, ``("act", "right", [5])``, ``("cancel",)`` or ``None``.

    An action can end with cells to zoom into first: "click 5".
    """

    norm = normalize(text)
    if is_cancel(norm):
        return ("cancel",)
    words = norm.split()
    for size in (3, 2, 1):
        head = " ".join(words[:size])
        if len(words) >= size and head in _ACTIONS:
            rest = words[size:]
            cells = _digits(rest) if rest else []
            if cells is None:
                return None
            return ("act", _ACTIONS[head], cells)
    cells = _digits(words)
    return ("zoom", cells) if cells else None


class GridMode(Mode):
    """Keeps the grid on screen and acts on the user's answers."""

    kind = "grid"

    def __init__(self, runner: Any, overlay: Any, rect: Rect) -> None:
        super().__init__()
        self._runner = runner
        self._overlay = overlay
        self._home: Rect = rect
        self.state = GridState(rect)
        self._marked: Optional[Tuple[int, int]] = None

    def open(self) -> None:
        self._draw()

    def close(self) -> None:
        if self._overlay is not None:
            self._overlay.hide()

    def _draw(self) -> None:
        if self._overlay is not None:
            self._overlay.show_grid(self.state.rect)
        self._runner.move_mouse(*self.state.center())

    def parse(self, text: str):
        return parse_grid(text)

    def _zoom(self, cells: List[int]) -> None:
        for n in cells:
            if not self.state.can_zoom():
                break
            self.state.zoom(n)

    def apply(self, parsed):
        from .actions import ActionResult

        kind = parsed[0]
        if kind == "cancel":
            self._finish()
            return ActionResult(True, "Grid closed")
        if kind == "zoom":
            self._zoom(parsed[1])
            self._draw()
            return ActionResult(True, "Say a number, or “click”")
        _act, what, cells = parsed
        self._zoom(cells)
        if what == "back":
            self.state.back()
            self._draw()
            return ActionResult(True, "Back")
        x, y = self.state.center()
        if what == "mark":
            self._marked = (x, y)
            self.state.reset(self._home)
            self._draw()
            return ActionResult(
                True, "Marked: now pick where to drop, then “drag here”"
            )
        self._finish()  # hide the grid before acting on what is under it
        if what == "move":
            self._runner.move_mouse(x, y)
            return ActionResult(True, "Mouse moved")
        if what == "drop":
            if self._marked is None:
                return ActionResult(False, "Say “mark” where the drag starts first")
            self._runner.drag_mouse(self._marked, (x, y))
            return ActionResult(True, "Dragged")
        self._runner.click_at(x, y, what)
        from .actions import _CLICK_LABELS

        return ActionResult(True, _CLICK_LABELS[what])

    def _finish(self) -> None:
        self.finished = True
        self.close()
