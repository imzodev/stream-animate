"""Telemetry footer bar — small status line at the bottom of the
answer panel.

Shows three pieces of information, separated by middle dots:

* Character count of the streamed answer (e.g. ``147 chars``)
* Model name (e.g. ``deepseek-v4-flash``)
* Elapsed time in seconds since the question was sent
  (e.g. ``2.3s``)

The values are populated by the parent :class:`AnswerPanel` as
events arrive. The footer is purely informational — clicking
on it has no effect.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget


# How often the elapsed-time label refreshes.
_TICK_MS = 100

# Footer height (px). Compact on purpose so the answer area
# dominates the panel.
_HEIGHT = 26


class _FooterBar(QWidget):
    """Bottom telemetry strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._char_count = 0
        self._model = ""
        self._started_at: float | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(8)

        font = QFont()
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFamilies(["JetBrains Mono", "Consolas", "Menlo", "Monaco", "monospace"])
        font.setPointSize(9)

        self._chars_label = QLabel("0 chars")
        self._model_label = QLabel("")
        self._elapsed_label = QLabel("0.0s")

        for label in (self._chars_label, self._model_label, self._elapsed_label):
            label.setFont(font)
            label.setStyleSheet("color: #6B7280; background: transparent;")
            layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._refresh_elapsed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear all stats and stop the elapsed-time timer."""
        self._char_count = 0
        self._model = ""
        self._started_at = None
        self._timer.stop()
        self._chars_label.setText("0 chars")
        self._model_label.setText("")
        self._elapsed_label.setText("0.0s")

    def set_model(self, model: str) -> None:
        self._model = model
        self._model_label.setText(model)

    def add_chars(self, n: int) -> None:
        self._char_count += n
        self._chars_label.setText(f"{self._char_count} chars")

    def start_timer(self) -> None:
        """Start the elapsed-time counter (called when the LLM begins)."""
        self._started_at = time.time()
        if not self._timer.isActive():
            self._timer.start()
        self._refresh_elapsed()

    def stop_timer(self) -> None:
        self._timer.stop()
        self._refresh_elapsed()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_elapsed(self) -> None:
        if self._started_at is None:
            self._elapsed_label.setText("0.0s")
            return
        elapsed = time.time() - self._started_at
        self._elapsed_label.setText(f"{elapsed:.1f}s")


__all__ = ["_FooterBar"]
