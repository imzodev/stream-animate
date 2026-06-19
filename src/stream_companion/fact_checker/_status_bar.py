"""Top status bar of the answer panel.

Layout (left to right):

* Persona icon (emoji glyph in a colored circle)
* Persona name (uppercase, letter-spaced)
* Stretch
* State pill: small colored dot + state label
  (``LISTENING`` / ``THINKING`` / ``STREAMING`` / ``DONE`` /
  ``ERROR`` / ``IDLE``)
* Lock toggle
* Close button

The state pill color and the dot animation change based on the
phase passed to :meth:`set_phase`. The persona name and color
change based on the persona string passed to
:meth:`set_persona`.

The widget has a fixed height (44px) and is rendered above the
content area of the panel. The persona icon's circle is filled
with the persona's accent color at low alpha for a subtle
branded look.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ._animations import pulse
from ._persona_accent import PersonaAccent, accent_for


# Height of the status bar in pixels.
_HEIGHT = 44

# State pill colors (hex). Each phase has its own pill color.
# The dot is filled with this color; the label uses it too.
_STATE_COLORS: dict[str, str] = {
    "idle": "#6B7280",  # grey
    "listening": "#22D3EE",  # cyan
    "thinking": "#A855F7",  # purple
    "streaming": "#10B981",  # green
    "done": "#10B981",  # green
    "error": "#EF4444",  # red
}

# Phases that should pulse the dot (to draw the eye).
_PULSING_PHASES = frozenset({"listening", "streaming"})

# Phases that should show the spinner instead of a static dot.
# (The spinner is a static visual cue here; the real "thinking"
# animation lives in the border painter's continuous rotation.)
_SPINNING_PHASES = frozenset({"thinking"})


class _StatusBar(QWidget):
    """Top status bar widget. Owned by :class:`AnswerPanel`."""

    # Emitted when the user toggles the lock button.
    lock_toggled = Signal(bool)
    # Emitted when the user clicks the close button.
    close_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._locked = False
        self._accent: PersonaAccent = accent_for("custom")
        self._phase = "idle"
        self._pulse_animator = None  # type: ignore[var-annotated]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)

        # Persona icon: emoji in a circular tinted background.
        self._icon = QLabel()
        self._icon.setFixedSize(28, 28)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Slightly larger emoji for visual weight.
        icon_font = QFont()
        icon_font.setPointSize(14)
        self._icon.setFont(icon_font)
        self._icon.setStyleSheet(self._icon_stylesheet(self._accent))
        self._icon.setText(self._accent.glyph)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        # Persona name (uppercase, letter-spaced).
        self._name_label = QLabel(self._accent.display_name)
        name_font = QFont()
        name_font.setPointSize(10)
        name_font.setBold(True)
        # Tight letter-spacing for a confident streamer look.
        name_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        self._name_label.setFont(name_font)
        self._name_label.setStyleSheet("color: #E5E7EB; background: transparent;")
        layout.addWidget(self._name_label, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addStretch(1)

        # State pill: dot + label, colored by phase.
        self._state_dot = QLabel("●")
        dot_font = QFont()
        dot_font.setPointSize(14)
        self._state_dot.setFont(dot_font)
        self._state_dot.setStyleSheet(
            f"color: {_STATE_COLORS['idle']}; background: transparent;"
        )
        self._state_dot.setFixedWidth(14)
        layout.addWidget(self._state_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        self._state_label = QLabel("IDLE")
        state_font = QFont()
        state_font.setPointSize(9)
        state_font.setBold(True)
        state_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self._state_label.setFont(state_font)
        self._state_label.setStyleSheet(
            f"color: {_STATE_COLORS['idle']}; background: transparent;"
        )
        layout.addWidget(self._state_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Lock toggle.
        self._lock_btn = QPushButton("🔓")
        self._lock_btn.setFixedSize(28, 28)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.setToolTip("Lock position")
        self._lock_btn.setStyleSheet(self._icon_button_stylesheet())
        self._lock_btn.clicked.connect(self._on_lock_clicked)
        layout.addWidget(self._lock_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Close button.
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("Hide")
        self._close_btn.setStyleSheet(self._icon_button_stylesheet())
        self._close_btn.clicked.connect(self.close_clicked)
        layout.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        # Apply the initial accent.
        self._apply_accent()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_persona(self, persona: str) -> None:
        """Switch the persona icon, name, and accent color."""
        self._accent = accent_for(persona)
        self._icon.setText(self._accent.glyph)
        self._name_label.setText(self._accent.display_name)
        self._icon.setStyleSheet(self._icon_stylesheet(self._accent))
        self._apply_accent()

    def set_phase(self, phase: str) -> None:
        """Switch the state pill color, label, and dot animation."""
        self._phase = phase
        color = _STATE_COLORS.get(phase, _STATE_COLORS["idle"])
        self._state_dot.setStyleSheet(f"color: {color}; background: transparent;")
        self._state_label.setText(phase.upper())
        self._state_label.setStyleSheet(f"color: {color}; background: transparent;")
        # Animate the dot based on phase.
        if self._pulse_animator is not None:
            self._pulse_animator.stop()
            self._pulse_animator = None
        if phase in _PULSING_PHASES:
            self._pulse_animator = pulse(
                self._state_dot, lo=0.35, hi=1.0, interval_ms=60
            )

    def is_locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        """Programmatic lock state (no signal)."""
        self._locked = locked
        self._lock_btn.setText("🔒" if locked else "🔓")
        self._lock_btn.setToolTip(
            "Locked — click to unlock" if locked else "Lock position"
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_lock_clicked(self) -> None:
        self.set_locked(not self._locked)
        self.lock_toggled.emit(self._locked)

    def _apply_accent(self) -> None:
        # The icon circle background uses the persona accent at low
        # alpha so the brand color shows through even on dark UIs.
        accent = QColor(self._accent.accent)
        accent.setAlpha(60)
        self._icon.setStyleSheet(self._icon_stylesheet(self._accent))

    @staticmethod
    def _icon_stylesheet(accent: PersonaAccent) -> str:
        bg = QColor(accent.accent)
        bg.setAlpha(60)
        return (
            f"color: #FFFFFF; background-color: {bg.name(QColor.NameFormat.HexArgb)};"
            f" border-radius: 14px; padding: 0px;"
        )

    @staticmethod
    def _icon_button_stylesheet() -> str:
        return (
            "QPushButton {"
            "  color: #9CA3AF; background-color: transparent;"
            "  border: none; border-radius: 14px;"
            "  font-size: 12pt;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(255, 255, 255, 24);"
            "  color: #FFFFFF;"
            "}"
            "QPushButton:pressed {"
            "  background-color: rgba(255, 255, 255, 40);"
            "}"
        )


__all__ = ["_StatusBar"]
