"""Click-through overlay that draws number labels and the mouse grid.

One transparent, always-on-top window per monitor. It never takes focus
and lets every mouse event through, so the app underneath keeps working
exactly as before — the overlay only *shows* where things are.

Positions arrive in physical pixels (UI Automation / cursor space) and are
converted per monitor with :mod:`.screen_map`, so labels line up on
mixed-DPI setups.

Public API (all thread-safe — the voice thread calls it):

* :meth:`ScreenOverlay.show_labels` — ``[(text, (left, top, width, height))]``
* :meth:`ScreenOverlay.show_grid` — a 3×3 grid over a physical rectangle
* :meth:`ScreenOverlay.hide`
"""

from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .screen_map import Monitor, qt_monitors, rect_to_logical

_LOGGER = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]

_BADGE_BG = QColor(255, 214, 10, 235)
_BADGE_FG = QColor(20, 20, 20)
_BADGE_BORDER = QColor(0, 0, 0, 150)
_GRID_LINE = QColor(255, 159, 10, 210)
_GRID_SHADE = QColor(0, 0, 0, 40)
_GRID_TEXT = QColor(255, 255, 255, 235)


def _font(size: int) -> QFont:
    font = QFont()
    font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Helvetica Neue"])
    font.setPixelSize(size)
    font.setWeight(QFont.Weight.Bold)
    return font


class _Canvas(QWidget):
    """Covers one monitor; paints whatever it is given in local coordinates."""

    def __init__(self, screen) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.screen_name = screen.name()
        self.setGeometry(screen.geometry())
        self.labels: List[Tuple[str, QRect]] = []
        self.grid: Optional[QRect] = None

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.grid is not None:
            self._paint_grid(painter, self.grid)
        if self.labels:
            self._paint_labels(painter)
        painter.end()

    def _paint_labels(self, painter: QPainter) -> None:
        font = _font(12)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        # Big elements first: a container's badge must not cover the badge
        # of a small button sitting in the same corner.
        ordered = sorted(self.labels, key=lambda lr: -(lr[1].width() * lr[1].height()))
        for text, rect in ordered:
            w = max(metrics.horizontalAdvance(text) + 8, 18)
            h = metrics.height() + 2
            # Top-left corner of the element, nudged inside it.
            badge = QRectF(rect.left() + 1, rect.top() + 1, w, h)
            painter.setPen(QPen(_BADGE_BORDER, 1))
            painter.setBrush(_BADGE_BG)
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(_BADGE_FG)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_grid(self, painter: QPainter, rect: QRect) -> None:
        painter.setBrush(_GRID_SHADE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(rect)
        painter.setPen(QPen(_GRID_LINE, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        cw, ch = rect.width() / 3, rect.height() / 3
        for i in (1, 2):
            x = rect.left() + cw * i
            y = rect.top() + ch * i
            painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        size = int(max(10, min(48, min(cw, ch) * 0.45)))
        painter.setFont(_font(size))
        painter.setPen(_GRID_TEXT)
        for n in range(9):
            row, col = divmod(n, 3)
            cell = QRectF(rect.left() + col * cw, rect.top() + row * ch, cw, ch)
            painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(n + 1))


class _Bridge(QObject):
    labels_requested = Signal(object)
    grid_requested = Signal(object)
    hide_requested = Signal()


class ScreenOverlay(QObject):
    """Owns one :class:`_Canvas` per monitor. Create it on the GUI thread."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._canvases: List[_Canvas] = []
        self._monitors: List[Monitor] = []
        self._bridge = _Bridge()
        self._bridge.labels_requested.connect(self._on_labels)
        self._bridge.grid_requested.connect(self._on_grid)
        self._bridge.hide_requested.connect(self._on_hide)

    # -- public API (thread-safe) -------------------------------------------

    def show_labels(self, items: Sequence[Tuple[str, Rect]]) -> None:
        self._bridge.labels_requested.emit(list(items))

    def show_grid(self, rect: Rect) -> None:
        self._bridge.grid_requested.emit(tuple(rect))

    def hide(self) -> None:
        self._bridge.hide_requested.emit()

    def close(self) -> None:
        for canvas in self._canvases:
            canvas.close()
            canvas.deleteLater()
        self._canvases = []

    # -- GUI thread ------------------------------------------------------------

    def _sync_screens(self) -> None:
        """(Re)build the canvases when monitors were added or removed."""

        screens = QApplication.screens()
        names = [s.name() for s in screens]
        if names != [c.screen_name for c in self._canvases]:
            self.close()
            self._canvases = [_Canvas(s) for s in screens]
        else:
            for canvas, screen in zip(self._canvases, screens):
                canvas.setGeometry(screen.geometry())
        self._monitors = qt_monitors(screens)

    def _local(self, canvas: _Canvas, rect: Rect) -> Optional[QRect]:
        """``rect`` (physical) in ``canvas`` coordinates, if it is mostly there.

        A rectangle that pokes a little past the screen edge (a maximized
        window's tab strip sits at y = -1) is clamped inside, so its badge
        stays visible.
        """

        left, top, w, h = rect_to_logical(self._monitors, rect)
        geo = canvas.geometry()
        cx, cy = left + w // 2, top + h // 2
        if not geo.contains(cx, cy):
            return None
        left = min(max(left, geo.left()), geo.right() - 1)
        top = min(max(top, geo.top()), geo.bottom() - 1)
        return QRect(left - geo.left(), top - geo.top(), w, h)

    def _on_labels(self, items) -> None:
        self._sync_screens()
        for canvas in self._canvases:
            canvas.grid = None
            canvas.labels = [
                (text, local)
                for text, rect in items
                if (local := self._local(canvas, rect)) is not None
            ]
        self._present()

    def _on_grid(self, rect) -> None:
        self._sync_screens()
        for canvas in self._canvases:
            canvas.labels = []
            left, top, w, h = rect_to_logical(self._monitors, rect)
            geo = canvas.geometry()
            local = QRect(left - geo.left(), top - geo.top(), w, h)
            canvas.grid = local if geo.intersects(QRect(left, top, w, h)) else None
        self._present()

    def _present(self) -> None:
        for canvas in self._canvases:
            if canvas.labels or canvas.grid is not None:
                canvas.show()
                canvas.raise_()
                canvas.update()
            else:
                canvas.hide()

    def _on_hide(self) -> None:
        for canvas in self._canvases:
            canvas.labels = []
            canvas.grid = None
            canvas.hide()


__all__ = ["ScreenOverlay"]
