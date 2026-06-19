"""Small reusable animation helpers for the answer panel widgets.

Each helper wraps a ``QTimer`` and a callback so widgets can
say ``pulse(my_widget)`` and get a periodic opacity animation
without each widget re-implementing the boilerplate.

All helpers are designed to be safe to start/stop multiple
times (e.g. when the panel is shown/hidden).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


class _Pulse(QObject):
    """Smoothly oscillate a widget's opacity between ``lo`` and ``hi``.

    Used by the status pill dot for the "listening" state and
    the streaming caret blink. The QTimer fires every
    ``interval_ms``; on each tick we step the opacity toward the
    next target (lo or hi) and apply it via a
    :class:`QGraphicsOpacityEffect`.
    """

    def __init__(
        self,
        widget: QWidget,
        *,
        lo: float = 0.4,
        hi: float = 1.0,
        interval_ms: int = 50,
        step: float = 0.08,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(hi)
        widget.setGraphicsEffect(self._effect)
        self._lo = lo
        self._hi = hi
        self._step = step
        self._direction = -1  # start fading down

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        # Reset to fully opaque so the widget is visible when not pulsing.
        self._effect.setOpacity(self._hi)

    def _tick(self) -> None:
        current = self._effect.opacity()
        if self._direction < 0:
            new = current - self._step
            if new <= self._lo:
                new = self._lo
                self._direction = 1
        else:
            new = current + self._step
            if new >= self._hi:
                new = self._hi
                self._direction = -1
        self._effect.setOpacity(new)


class _Blink(QObject):
    """Toggle a widget's opacity between full and zero on a fixed interval.

    Used by the streaming caret. The QTimer fires every
    ``interval_ms`` and flips between visible and hidden. Less
    smooth than :class:`_Pulse` but more "typewriter-like".
    """

    def __init__(self, widget: QWidget, *, interval_ms: int = 500) -> None:
        super().__init__(widget)
        self._widget = widget
        self._effect = QGraphicsOpacityEffect(widget)
        self._effect.setOpacity(1.0)
        widget.setGraphicsEffect(self._effect)
        self._visible = True

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._visible = True
            self._effect.setOpacity(1.0)
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._effect.setOpacity(1.0)

    def _tick(self) -> None:
        self._visible = not self._visible
        self._effect.setOpacity(1.0 if self._visible else 0.0)


def pulse(widget: QWidget, **kwargs) -> _Pulse:
    """Create a :class:`_Pulse` animator attached to ``widget`` and start it."""
    animator = _Pulse(widget, **kwargs)
    animator.start()
    return animator


def blink(widget: QWidget, **kwargs) -> _Blink:
    """Create a :class:`_Blink` animator attached to ``widget`` and start it."""
    animator = _Blink(widget, **kwargs)
    animator.start()
    return animator


__all__ = ["_Blink", "_Pulse", "blink", "pulse"]
