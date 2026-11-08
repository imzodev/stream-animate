"""Floating picker for choosing between several matching tabs.

When a voice command matches more than one tab ("computer, tab youtube"
with four YouTube tabs open), Saidcue does not guess: it shows this
numbered list and waits. Say the number to pick by voice, or click the
row with the mouse.

The window is shown without activating and does not accept keyboard
focus, so the app you were working in stays the foreground window —
clicking a row picks a tab without stealing your place.

Public API:

* :meth:`show_candidates` — show a list of candidates (thread-safe)
* :meth:`dismiss` — hide the list (thread-safe)
* :attr:`chosen` — emitted with the 1-based index of the picked row
* :attr:`dismissed` — emitted when the list closes without a pick
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .tabs import clean_tab_name

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 20_000
MAX_ROWS = 9
_TITLE_CHARS = 56
_WINDOW_CHARS = 30

# Same type stack as the "Listening" pill, so the two widgets on screen
# at the same time look like one app.
_FONT_FAMILIES = [
    "Segoe UI Variable Display",
    "Segoe UI",
    "SF Pro Text",
    "Helvetica Neue",
]

_STYLESHEET = """
#TabPickerCard {
    background-color: rgba(16, 18, 23, 238);
    border: 1px solid rgba(255, 255, 255, 22);
    border-radius: 14px;
}
#TabPickerHeader { color: rgba(255, 255, 255, 238); }
#TabPickerHint { color: rgba(255, 255, 255, 110); }
#TabPickerBadge {
    color: rgb(255, 159, 10);
    background-color: rgba(255, 159, 10, 42);
    border-radius: 10px;
}
#TabPickerName { color: rgba(255, 255, 255, 232); }
#TabPickerWindow { color: rgba(255, 255, 255, 105); }
#TabPickerRow { border-radius: 9px; }
#TabPickerRow:hover { background-color: rgba(255, 255, 255, 20); }
"""  # fmt: skip


def _font(size: int, *, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(_FONT_FAMILIES)
    font.setPixelSize(size)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


def _elide(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _candidate_title(candidate: Any) -> str:
    """The tab's title, without the browser's trailing app name."""

    name = getattr(candidate, "tab_name", "") or ""
    return clean_tab_name(name) or "(untitled tab)"


def _header_text(count: int, query: str) -> str:
    label = "tab" if count == 1 else "tabs"
    if query:
        return f"{count} {label} match “{query}”"
    return f"{count} {label}"


class _Row(QFrame):
    """One candidate: pick number, tab title, and the window it lives in."""

    clicked = Signal(int)

    def __init__(
        self,
        index: int,
        title: str,
        window_title: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TabPickerRow")
        self._index = index
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(10)

        badge = QLabel(str(index))
        badge.setObjectName("TabPickerBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(22, 22)
        badge.setFont(_font(11, bold=True))
        layout.addWidget(badge)
        self._badge = badge

        name = QLabel(_elide(title, _TITLE_CHARS))
        name.setObjectName("TabPickerName")
        name.setToolTip(title)
        name.setFont(_font(13))
        layout.addWidget(name, 1)
        self._title_label = name

        window = QLabel(_elide(window_title, _WINDOW_CHARS))
        window.setObjectName("TabPickerWindow")
        window.setToolTip(window_title)
        window.setFont(_font(11))
        layout.addWidget(window)
        self._window_label = window

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._index)
            event.accept()
            return
        super().mousePressEvent(event)


class _PickerBridge(QObject):
    """Marshals calls from the voice thread onto the GUI thread."""

    show_requested = Signal(object, str)
    dismiss_requested = Signal()


class TabPicker(QWidget):
    """Numbered list of matching tabs; pick one by voice or by mouse."""

    chosen = Signal(int)
    dismissed = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        # Frameless, on top, no taskbar entry. WindowDoesNotAcceptFocus
        # keeps the foreground window where it was, so clicking a row
        # never pulls you out of the browser or terminal.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_STYLESHEET)

        self._bridge = _PickerBridge()
        self._bridge.show_requested.connect(self._on_show)
        self._bridge.dismiss_requested.connect(self._on_dismiss)

        self._card = QFrame(self)
        self._card.setObjectName("TabPickerCard")
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(14, 11, 14, 12)
        card_layout.setSpacing(4)

        self._header = QLabel()
        self._header.setObjectName("TabPickerHeader")
        self._header.setFont(_font(13, bold=True))
        card_layout.addWidget(self._header)

        self._hint = QLabel("Say the number, or click a tab")
        self._hint.setObjectName("TabPickerHint")
        self._hint.setFont(_font(11))
        card_layout.addWidget(self._hint)

        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 4, 0, 0)
        self._rows_box.setSpacing(2)
        card_layout.addLayout(self._rows_box)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)

        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        self._card.setGraphicsEffect(shadow)

        self._timeout_ms = timeout_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        self._rows: List[_Row] = []

    # -- public API (thread-safe) -------------------------------------------

    def show_candidates(self, candidates: Sequence[Any], *, query: str = "") -> None:
        """Show the list of candidates. Safe to call from the voice thread."""

        self._bridge.show_requested.emit(list(candidates), query)

    def dismiss(self) -> None:
        """Hide the list. Safe to call from the voice thread."""

        self._bridge.dismiss_requested.emit()

    @property
    def is_open(self) -> bool:
        return self.isVisible()

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def set_timeout_ms(self, timeout_ms: int) -> None:
        self._timeout_ms = max(0, int(timeout_ms))

    # -- GUI-thread slots ----------------------------------------------------

    def _on_show(self, candidates: Sequence[Any], query: str) -> None:
        self._timer.stop()
        self._clear_rows()

        shown = list(candidates)[:MAX_ROWS]
        titles = [_candidate_title(c) for c in shown]
        # Several tabs can share a name (four "Windows PowerShell" tabs,
        # four "YouTube" tabs). When they do, the position is the only
        # thing that tells them apart — so show it.
        ambiguous = len(set(titles)) != len(titles)
        # The window title only helps when the tabs live in more than
        # one window; otherwise it is just noise.
        multi_window = len({getattr(c, "hwnd", 0) for c in shown}) > 1

        for i, candidate in enumerate(shown, start=1):
            title = titles[i - 1]
            if ambiguous:
                index = getattr(candidate, "tab_index", 0) + 1
                count = getattr(candidate, "tab_count", 0)
                title = f"{title}  (tab {index}/{count})" if count else title
            window_title = (
                getattr(candidate, "window_title", "") if multi_window else ""
            )
            row = _Row(i, title, window_title, self._card)
            row.clicked.connect(self._on_row_clicked)
            self._rows_box.addWidget(row)
            self._rows.append(row)

        self._header.setText(_header_text(len(shown), query))
        self._card.adjustSize()
        self.adjustSize()
        self._reposition()
        self.show()
        self._timer.start(self._timeout_ms)

    def _on_row_clicked(self, index: int) -> None:
        self._hide()
        self.chosen.emit(index)

    def _on_timeout(self) -> None:
        self._hide()
        self.dismissed.emit()

    def _on_dismiss(self) -> None:
        self._hide()
        self.dismissed.emit()

    # -- helpers -------------------------------------------------------------

    def _hide(self) -> None:
        self._timer.stop()
        self.hide()
        self._clear_rows()

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._rows_box.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.top() + 26
        self.move(x, y)


__all__ = ["TabPicker"]
