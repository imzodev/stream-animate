"""Animated gradient border widget.

Renders a 2-3 px conic gradient that rotates slowly around the
panel perimeter. The gradient is composed of the two
``gradient_top`` / ``gradient_bottom`` stops from the active
:class:`PersonaAccent` so the border color matches the persona.

The painter is a transparent overlay widget that sits on top of
the panel's content area. The content area itself uses
``WA_TranslucentBackground`` so the gradient shows through.

Implementation notes:

* The conic gradient is drawn with ``QPainter`` using a
  ``QConicalGradient`` rotated by an angle property updated
  by a ``QTimer`` (~50ms tick, 8s full rotation = subtle
  shimmer, not a disco).
* The widget is fully transparent to mouse events so it does
  not steal the drag handle from the parent panel.
* ``set_accent`` swaps the gradient stops immediately (the
  next paint uses the new colors) and continues the rotation
  from the current angle.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from ._persona_accent import PersonaAccent, accent_for


# How many milliseconds between gradient angle updates. Smaller
# values look smoother but use more CPU. 50ms = 20fps which is
# plenty for a slow shimmer.
_TICK_MS = 50

# How many milliseconds the gradient takes to complete a full
# 360 degree rotation. 8 seconds is the "AI magic" tempo —
# noticeable but not distracting.
_PERIOD_MS = 8000

# Border thickness in pixels.
_BORDER_PX = 2

# Corner radius of the rounded border. The panel itself uses a
# larger radius; the border is just a thin line on top.
_CORNER_RADIUS = 18


class _AnimatedBorder(QWidget):
    """Transparent overlay that paints the rotating conic gradient."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Pass-through mouse events so the parent's drag handler
        # still receives them.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        # Angle is stored as a property so QPropertyAnimation can
        # tween it smoothly when the accent changes (we don't
        # currently tween — we just snap — but the property is
        # there for future use).
        self._angle = 0.0
        self._accent: PersonaAccent = accent_for("custom")

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_accent(self, accent: PersonaAccent) -> None:
        """Switch the gradient colors. Takes effect on the next paint."""
        self._accent = accent
        self.update()

    def stop(self) -> None:
        """Stop the rotation timer (e.g. when the panel is hidden)."""
        self._timer.stop()

    def start(self) -> None:
        """Restart the rotation timer."""
        if not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_tick(self) -> None:
        self._angle = (self._angle + (_TICK_MS / _PERIOD_MS) * 360.0) % 360.0
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Build a conic gradient centered on the widget's center.
        # QConicalGradient sweeps counter-clockwise from the 3-o'clock
        # position; we rotate the painter to start at 12-o'clock for a
        # more "header bar" feel.
        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0
        gradient = QConicalGradient(cx, cy, -90.0 + self._angle)
        top = QColor(self._accent.gradient_top)
        bottom = QColor(self._accent.gradient_bottom)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(0.5, bottom)
        gradient.setColorAt(1.0, top)

        # Stroke the rounded-rect path with the conic gradient as
        # the brush. QPen cannot wrap a gradient directly, so we
        # build a QBrush from the gradient and assign it to the
        # pen. The pen is set to a fixed width and a cosmetic
        # style so the stroke stays a constant 2px regardless of
        # the widget's transform.
        from PySide6.QtGui import QBrush, QPen
        from PySide6.QtCore import QRectF

        pen = QPen(QBrush(gradient), _BORDER_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = _BORDER_PX / 2.0
        painter.drawRoundedRect(
            QRectF(inset, inset, w - _BORDER_PX, h - _BORDER_PX),
            _CORNER_RADIUS,
            _CORNER_RADIUS,
        )
        painter.end()


__all__ = ["_AnimatedBorder"]
