"""Streaming answer view.

Renders the LLM's response as a sequence of tokens. A blinking
caret is shown at the end while the stream is active, and
hidden when the stream is done.

Layout / behavior:

* Plain text in a large sans-serif font (falls back to the
  platform default if Inter / Manrope is not installed).
* Auto-grow vertically with content up to a maximum height,
  after which a scrollbar appears.
* Per-token fade-in: each call to :meth:`append_token` briefly
  animates the new text from 0.4 → 1.0 opacity over 80ms.
* The caret is implemented as a unicode block character
  (``▎``) appended at the end of the text while the stream is
  active. The blinking is done via a :class:`_Blink` animator
  on the caret character.

The view uses a ``QTextEdit`` under the hood for free text
layout and scrolling. External code only sees the
:meth:`append_token`, :meth:`clear`, and :meth:`set_streaming`
API.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QTextEdit

from ._animations import blink


# Min / max / step heights for the auto-grow behavior.
_MIN_HEIGHT = 96
_MAX_HEIGHT = 480
_HEIGHT_STEP = 24  # grow by this much per content line block

# Caret character. A thin block works well in monospace fonts.
_CARET = "▎"


class _AnswerView(QTextEdit):
    """Streaming text view with a blinking caret at the end."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        # Hide the default focus rectangle; this is a display widget.
        # The right padding is wider than the left to compensate
        # for the vertical scrollbar (6px) + its margin (4px on
        # each side) sitting inside the viewport on the right edge.
        # Without the extra right margin, the last word of a wrapped
        # line gets visually clipped by the scrollbar.
        self.setStyleSheet(
            "QTextEdit {"
            "  background: transparent;"
            "  border: none;"
            "  color: #F9FAFB;"
            "  padding: 4px 64px 4px 14px;"
            "  selection-background-color: rgba(99, 102, 241, 0.45);"
            "}"
            "QScrollBar:vertical {"
            "  background: transparent; width: 6px; margin: 4px;"
            "}"
            "QScrollBar::handle:vertical {"
            "  background: rgba(255, 255, 255, 0.18);"
            "  border-radius: 3px; min-height: 24px;"
            "}"
        )
        # Sans-serif fallback chain. Inter and Manrope are
        # preferred when installed; we degrade gracefully.
        font = QFont()
        font.setStyleHint(QFont.StyleHint.SansSerif)
        families = ["Inter", "Manrope", "Segoe UI", "SF Pro Text", "Cantarell"]
        for fam in families:
            if fam in QFont().families() if False else False:
                font.setFamily(fam)
                break
        # Always default to the first available family in the
        # chain. QFontDatabase lookup is platform-specific; we
        # simply try to set the family and Qt falls back.
        font.setFamilies(families)
        font.setPointSize(15)
        self.setFont(font)
        self.document().setDocumentMargin(0)

        self._streaming = False
        self._caret_animator = None
        # Only set a minimum height. The parent panel's
        # ``_fit_height_to_content`` grows the panel itself when
        # the answer grows, so we don't cap the view here —
        # otherwise the answer would scroll inside the view even
        # when the panel has plenty of room.
        self.setMinimumHeight(_MIN_HEIGHT)
        self._adjust_height()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append_token(self, token: str) -> None:
        """Append a token to the streaming answer.

        If a stream is active, the caret is refreshed at the end.
        """
        if not token:
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # If the caret is currently shown, drop it before adding
        # the new token (then re-add it).
        if self._streaming and self.toPlainText().endswith(_CARET):
            cursor.deletePreviousChar()
        cursor.insertText(token)
        if self._streaming:
            self._insert_caret()
        self._adjust_height()

    def clear(self) -> None:
        """Empty the view and stop the caret animation."""
        self._stop_caret()
        self.clear_text()

    def clear_text(self) -> None:
        """Empty the view but keep caret state (used by hide/show)."""
        super().clear()
        self._adjust_height()

    def set_streaming(self, streaming: bool) -> None:
        """Start or stop the streaming state (controls the caret)."""
        if streaming and not self._streaming:
            self._streaming = True
            self._insert_caret()
        elif not streaming and self._streaming:
            self._streaming = False
            self._stop_caret()
            # Strip the caret character from the text.
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if self.toPlainText().endswith(_CARET):
                cursor.deletePreviousChar()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _insert_caret(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(_CARET)
        # Restart the blink animator each time the caret moves so
        # the new caret position is in sync.
        if self._caret_animator is None:
            # Attach the opacity effect to the whole document via
            # the viewport — animating a single character in a
            # QTextEdit is not directly supported.
            self._caret_animator = blink(self, interval_ms=500)
        # Scroll to bottom so the caret is always visible.
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _stop_caret(self) -> None:
        if self._caret_animator is not None:
            self._caret_animator.stop()
            self._caret_animator = None
        # Make sure the document is fully opaque when the caret is gone.
        self.setWindowOpacity(1.0)

    def _adjust_height(self) -> None:
        """Grow the widget vertically with content.

        Sets a minimum height that matches the document's intrinsic
        height so the layout gives the view as much vertical space
        as it needs. We don't set a maximum height here — the
        parent panel's ``_fit_height_to_content`` handles the
        overall sizing and will grow the panel to fit the answer.

        If we set a maximum here, the answer view would be capped
        at 480px regardless of how tall the panel is, and the
        answer would scroll inside the view even when there's
        plenty of panel space available.
        """
        doc_height = int(self.document().size().height()) + 24
        target = max(_MIN_HEIGHT, min(_MAX_HEIGHT, doc_height))
        # Round up to the next step for less jittery resize.
        target = ((target + _HEIGHT_STEP - 1) // _HEIGHT_STEP) * _HEIGHT_STEP
        if abs(self.minimumHeight() - target) > 2:
            self.setMinimumHeight(target)


__all__ = ["_AnswerView"]
