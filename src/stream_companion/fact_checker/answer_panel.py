"""Streaming answer panel: a small always-on-top Qt widget that renders
the LLM's response as it is generated.

The panel is deliberately minimal: a single ``QTextEdit`` with a
typewriter-style monospace font, a thin border, and a close button.
It is draggable and remembers its last position via :class:`QSettings`.

Thread safety: ``append_token`` may be called from the engine's
background thread. We marshal to the GUI thread with
``QMetaObject.invokeMethod`` using ``Qt.QueuedConnection``.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import QMetaObject, QObject, Qt, Q_ARG, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_LOGGER = logging.getLogger(__name__)


class _PanelBridge(QObject):
    """Helper QObject that emits signals on the GUI thread.

    The engine thread calls ``bridge.append_token(token)``; the bridge
    re-emits a :class:`Signal` which is delivered to the widget on the
    GUI thread. This avoids busy-spinning the GUI thread from the
    engine.
    """

    token_appended = Signal(str, str)
    cleared = Signal()
    phase_changed = Signal(str)
    persona_changed = Signal(str)


class AnswerPanel(QWidget):
    """A small draggable, always-on-top widget for the LLM's response."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Frameless, on top, tool window (no taskbar entry).
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self._bridge = _PanelBridge()
        self._bridge.token_appended.connect(self._on_token)
        self._bridge.cleared.connect(self._on_clear)
        self._bridge.phase_changed.connect(self._on_phase)
        self._bridge.persona_changed.connect(self._on_persona)

        # ------------------------------------------------------------------
        # UI
        # ------------------------------------------------------------------
        outer = QVBoxLayout()
        outer.setContentsMargins(8, 8, 8, 8)
        self.setLayout(outer)

        title_row = QHBoxLayout()
        self._persona_label = QLabel("fact-checker")
        self._persona_label.setStyleSheet("color: #888;")
        title_row.addWidget(self._persona_label)
        title_row.addStretch(1)
        self._phase_label = QLabel("idle")
        self._phase_label.setStyleSheet("color: #888;")
        title_row.addWidget(self._phase_label)
        self._close_btn = QPushButton("×")
        self._close_btn.setFixedSize(24, 24)
        self._close_btn.clicked.connect(self.hide)
        title_row.addWidget(self._close_btn)
        outer.addLayout(title_row)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(13)
        self._text.setFont(font)
        self._text.setMinimumSize(360, 180)
        outer.addWidget(self._text)

        # Drag tracking: remember the offset between the mouse-down
        # point and the top-left of the window so ``mouseMoveEvent``
        # can drag smoothly.
        self._drag_offset = None

        # Default position: bottom-right of the primary screen.
        self.resize(420, 240)
        self._move_to_default_position()

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def append_token(self, token: str, *, kind: str = "answer") -> None:
        """Append a single token to the answer text.

        Safe to call from any thread. The actual mutation runs on the
        GUI thread. ``kind="reasoning"`` styles the token as italic
        grey (chain-of-thought from a thinking model); ``kind="answer"``
        is plain text.
        """

        if not token:
            return
        QMetaObject.invokeMethod(
            self._bridge,
            "token_appended",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, token),
            Q_ARG(str, kind),
        )

    def clear(self) -> None:
        """Empty the answer text. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "cleared",
            Qt.ConnectionType.QueuedConnection,
        )

    def set_phase(self, phase: str) -> None:
        """Update the small status label. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "phase_changed",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, phase),
        )

    def set_persona_label(self, name: str) -> None:
        """Update the persona label. Thread-safe."""
        QMetaObject.invokeMethod(
            self._bridge,
            "persona_changed",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, name),
        )

    # ------------------------------------------------------------------
    # GUI-thread slots
    # ------------------------------------------------------------------

    def _on_token(self, token: str, kind: str = "answer") -> None:
        # Reasoning tokens are dropped from the panel — the chain-
        # of-thought is logged for debugging but never shown to the
        # user. The final answer is the only thing that matters.
        if kind == "reasoning":
            return
        cursor = self._text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._text.setTextCursor(cursor)
        self._text.insertPlainText(token)

    def _on_clear(self) -> None:
        self._text.clear()

    def _on_phase(self, phase: str) -> None:
        self._phase_label.setText(phase)

    def _on_persona(self, name: str) -> None:
        self._persona_label.setText(name)

    # ------------------------------------------------------------------
    # Drag + position
    # ------------------------------------------------------------------

    def _move_to_default_position(self) -> None:
        from PySide6.QtWidgets import QApplication

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 24
        self.move(
            geo.right() - self.width() - margin,
            geo.bottom() - self.height() - margin,
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()

    def showEvent(self, event) -> None:
        # Reposition on show in case the screen layout changed.
        if self.pos().x() < 0 or self.pos().y() < 0:
            self._move_to_default_position()
        super().showEvent(event)
