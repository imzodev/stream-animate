"""Question card — the user's transcribed question, in a speech-bubble
shape with a colored left stripe.

Layout (left to right):

* 4 px wide vertical stripe (the persona accent color)
* Speech-bubble area with a faint tinted background and the
  question text in italic

The card slides in from the left + fades in when a question is
set. It is hidden when there is no question.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QPropertyAnimation,
    Qt,
)
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ._persona_accent import PersonaAccent, accent_for


# Width of the colored left stripe (px). Doubles as a visual
# anchor: streamers can scan for the colored stripe to find the
# question block in their overlay.
_STRIPE_PX = 4

# Vertical and horizontal padding inside the bubble.
_PADDING_V = 10
_PADDING_H = 14

# Slide-in / fade-in duration (ms).
_ANIM_MS = 300


class _QuestionCard(QWidget):
    """Speech-bubble question display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._accent: PersonaAccent = accent_for("custom")
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(0)

        # Colored left stripe.
        self._stripe = QLabel()
        self._stripe.setFixedWidth(_STRIPE_PX)
        self._stripe.setStyleSheet(self._stripe_stylesheet(self._accent))
        outer.addWidget(self._stripe, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addSpacing(_PADDING_H)

        # Speech-bubble area with the question text.
        bubble = QWidget()
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(_PADDING_H, _PADDING_V, _PADDING_H, _PADDING_V)
        bubble_layout.setSpacing(0)
        bubble.setStyleSheet(self._bubble_stylesheet(self._accent))
        outer.addWidget(bubble, 1)

        # Header label: "YOU ASKED" in small uppercase.
        self._header = QLabel("YOU ASKED")
        header_font = QFont()
        header_font.setPointSize(8)
        header_font.setBold(True)
        header_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        self._header.setFont(header_font)
        header_color = QColor(self._accent.accent)
        self._header.setStyleSheet(
            f"color: {header_color.name()}; background: transparent;"
        )
        bubble_layout.addWidget(self._header)

        # Question text.
        self._text = QLabel("")
        self._text.setWordWrap(True)
        text_font = QFont()
        text_font.setPointSize(13)
        text_font.setItalic(True)
        self._text.setFont(text_font)
        self._text.setStyleSheet("color: #F3F4F6; background: transparent;")
        bubble_layout.addWidget(self._text)

        # Animation state.
        self._opacity_anim: QPropertyAnimation | None = None
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_question(self, question: str) -> None:
        """Show the question with a slide-in + fade-in animation."""
        self._text.setText(question)
        self._apply_accent()
        self._run_appear_animation()

    def clear(self) -> None:
        """Hide the question and reset the animation state.

        We hide the widget immediately (not via the fade-out
        animation) so the parent layout collapses the card's
        space and the answer view takes its place. If we kept the
        fade-out, the card would remain in the layout while
        transparent, pushing the answer down by the card's height
        and clipping the bottom of the answer.
        """
        # Stop any in-flight animation so the new state is
        # deterministic.
        if self._opacity_anim is not None:
            self._opacity_anim.stop()
            self._opacity_anim = None
        # Reset opacity so the next ``set_question`` starts the
        # fade-in from 0 again.
        self._opacity_effect.setOpacity(0.0)
        # Hide the widget — this removes it from the layout so the
        # answer view moves up to where the card was.
        self.hide()
        self._text.setText("")

    def set_persona(self, persona: str) -> None:
        """Update the accent (color, stripe, header)."""
        self._accent = accent_for(persona)
        self._apply_accent()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_accent(self) -> None:
        self._stripe.setStyleSheet(self._stripe_stylesheet(self._accent))
        # Re-apply the bubble style to the next sibling of the
        # stripe in the outer layout (the speech-bubble area).
        for i in range(self.layout().count()):
            item = self.layout().itemAt(i)
            w = item.widget() if item is not None else None
            if w is not None and w is not self._stripe and w.findChild(QLabel):
                w.setStyleSheet(self._bubble_stylesheet(self._accent))
        header_color = QColor(self._accent.accent)
        self._header.setStyleSheet(
            f"color: {header_color.name()}; background: transparent;"
        )

    def _run_appear_animation(self) -> None:
        # Cancel any in-flight disappear animation so the new
        # fade-in starts cleanly.
        if self._opacity_anim is not None:
            self._opacity_anim.stop()
            self._opacity_anim = None

        # Reset the effect to 0 before starting so the fade is
        # visible (the widget is shown immediately, then fades in).
        self._opacity_effect.setOpacity(0.0)
        self.show()

        # Fade in: 0 → 1. The widget's position is managed by
        # the parent's layout — animating ``pos`` on a layout-
        # managed widget fights the layout, so we just fade.
        self._opacity_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._opacity_anim.setDuration(_ANIM_MS)
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._opacity_anim.start()

    @staticmethod
    def _stripe_stylesheet(accent: PersonaAccent) -> str:
        c = QColor(accent.accent)
        return f"background-color: {c.name()}; border-radius: 2px;"

    @staticmethod
    def _bubble_stylesheet(accent: PersonaAccent) -> str:
        bg = QColor(accent.accent)
        bg.setAlpha(28)
        return (
            f"background-color: {bg.name(QColor.NameFormat.HexArgb)};"
            f" border-radius: 10px;"
        )


__all__ = ["_QuestionCard"]
