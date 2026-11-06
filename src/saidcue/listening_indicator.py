"""On-screen "Listening" pill shown while dictation typing is active.

A small Dynamic-Island-style capsule: an orange "mic in use" dot that
breathes with your voice, a label, and a live waveform in Siri colors. It
springs in when typing is toggled on and switches to bouncing dots while
Whisper is transcribing a phrase.

The window never takes focus and ignores the mouse — dictated text is
pasted into whichever window has focus, so the indicator must never be it.

State is *polled* from the STT engine on the GUI thread (cheap attribute
reads), which avoids cross-thread signalling entirely.
"""

from __future__ import annotations

import math
import time
from typing import Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

POSITIONS = (
    "top-center",
    "top-left",
    "top-right",
    "bottom-center",
    "bottom-left",
    "bottom-right",
)
DEFAULT_POSITION = "top-center"

_PILL_W, _PILL_H = 172, 44
_MAX_PILL_W = 380  # pill grows up to this to fit command feedback text
_SHADOW = 22  # transparent margin around the pill for the soft shadow
_FLASH_SECONDS = 2.4
_BUSY_MAX_SECONDS = 30.0
_SCREEN_MARGIN = 18
_N_BARS = 5
_ACTIVE_FPS_MS = 16
_IDLE_POLL_MS = 120
# Speech RMS from a typical mic sits around 0.01-0.08; map to 0..1.
_LEVEL_FULL_SCALE = 0.06

_ORANGE = QColor(255, 159, 10)
_GREEN = QColor(48, 209, 88)
_RED = QColor(255, 69, 58)
_SIRI = (QColor(100, 210, 255), QColor(94, 92, 230), QColor(191, 90, 242))


def compute_position(
    position: str, screen: QRect, size: Tuple[int, int], margin: int = _SCREEN_MARGIN
) -> Tuple[int, int]:
    """Top-left window coordinate for ``position`` within ``screen``.

    ``size`` is the full window size (pill + shadow margin); the shadow
    margin is subtracted so the *visible pill* sits ``margin`` px from the
    screen edge.
    """

    w, h = size
    if position not in POSITIONS:
        position = DEFAULT_POSITION
    vertical, horizontal = position.split("-")
    edge = margin - _SHADOW
    if horizontal == "left":
        x = screen.left() + edge
    elif horizontal == "right":
        x = screen.right() + 1 - w - edge
    else:
        x = screen.left() + (screen.width() - w) // 2
    if vertical == "top":
        y = screen.top() + edge
    else:
        y = screen.bottom() + 1 - h - edge
    return x, y


class ListeningIndicator(QWidget):
    """Frameless, click-through, never-focused status pill."""

    def __init__(self, position: str = DEFAULT_POSITION) -> None:
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
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(_MAX_PILL_W + 2 * _SHADOW, _PILL_H + 2 * _SHADOW)

        self._pill_w = float(_PILL_W)  # animated toward the content width
        # Command feedback ("✓ Tab: servidor", "Thinking…").
        self._flash_text = ""
        self._flash_kind = ""
        self._flash_until = 0.0

        self._engine = None
        self._enabled = True
        self._position = position if position in POSITIONS else DEFAULT_POSITION
        self._want_visible = False

        self._level = 0.0  # smoothed 0..1
        self._bars = [0.0] * _N_BARS
        self._transcribing = False
        self._t0 = time.monotonic()

        # 0 = hidden, 1 = fully shown. Drives scale + opacity.
        self._appear = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_appear_value)
        self._anim.finished.connect(self._on_appear_finished)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(_IDLE_POLL_MS)

        self._label_font = QFont()
        self._label_font.setFamilies(
            ["Segoe UI Variable Display", "Segoe UI", "SF Pro Text", "Helvetica Neue"]
        )
        self._label_font.setPixelSize(13)
        self._label_font.setWeight(QFont.Weight.DemiBold)

    # -- public API ---------------------------------------------------------

    @property
    def position(self) -> str:
        return self._position

    @property
    def is_shown(self) -> bool:
        """``True`` when the indicator wants to be visible (typing active)."""

        return self._want_visible

    def attach(self, engine, *, enabled: bool = True, position: Optional[str] = None):
        """Follow ``engine``'s typing state. ``enabled=False`` keeps it hidden."""

        self._engine = engine
        self._enabled = bool(enabled)
        if position is not None:
            self.set_position(position)
        self._tick()

    def detach(self) -> None:
        self._engine = None
        self._set_visible(False)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._tick()

    def set_position(self, position: str) -> None:
        self._position = position if position in POSITIONS else DEFAULT_POSITION
        self._reposition()

    def flash(self, text: str, kind: str = "ok") -> None:
        """Briefly show command feedback. ``kind``: ok / error / busy / info.

        ``busy`` stays up until the next flash (e.g. "Thinking…" while the AI
        works). Respects the show/hide setting, so a hidden indicator stays
        hidden on stream.
        """

        self._flash_text = text
        self._flash_kind = kind
        duration = _BUSY_MAX_SECONDS if kind == "busy" else _FLASH_SECONDS
        self._flash_until = time.monotonic() + duration
        self._tick()

    @property
    def flash_text(self) -> str:
        return self._flash_text if self._flashing() else ""

    def _flashing(self) -> bool:
        return bool(self._flash_text) and time.monotonic() < self._flash_until

    # -- state --------------------------------------------------------------

    def _tick(self) -> None:
        engine = self._engine
        typing = bool(engine is not None and engine.is_running and engine.is_active)
        flashing = self._flashing()
        self._set_visible(self._enabled and (typing or flashing))
        if not self.isVisible():
            return
        active = typing

        goal_w = self._content_width() if flashing else _PILL_W
        self._pill_w += (goal_w - self._pill_w) * 0.25

        raw = engine.input_level if (engine is not None and active) else 0.0
        target = min(1.0, math.sqrt(max(0.0, raw) / _LEVEL_FULL_SCALE))
        # Fast attack, slow release — feels like a VU meter.
        k = 0.45 if target > self._level else 0.12
        self._level += (target - self._level) * k
        self._transcribing = bool(engine is not None and engine.is_transcribing)

        t = time.monotonic() - self._t0
        for i in range(_N_BARS):
            # Each bar gets its own wobble; the middle bars reach highest.
            envelope = 1.0 - abs(i - (_N_BARS - 1) / 2) / _N_BARS
            wobble = 0.55 + 0.45 * math.sin(t * (7.0 + i * 1.7) + i * 1.3)
            goal = 0.12 + 0.88 * self._level * envelope * wobble
            self._bars[i] += (goal - self._bars[i]) * 0.35
        self.update()

    def _set_visible(self, visible: bool) -> None:
        if visible == self._want_visible:
            return
        self._want_visible = visible
        self._anim.stop()
        if visible:
            self._reposition()
            self.show()
            self._timer.setInterval(_ACTIVE_FPS_MS)
            self._anim.setStartValue(self._appear)
            self._anim.setEndValue(1.0)
            self._anim.setDuration(420)
            self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        else:
            self._anim.setStartValue(self._appear)
            self._anim.setEndValue(0.0)
            self._anim.setDuration(200)
            self._anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._anim.start()

    def _on_appear_value(self, value) -> None:
        self._appear = float(value)
        self.update()

    def _on_appear_finished(self) -> None:
        if not self._want_visible:
            self.hide()
            self._timer.setInterval(_IDLE_POLL_MS)
            self._level = 0.0
            self._bars = [0.0] * _N_BARS
            self._pill_w = float(_PILL_W)

    def _content_width(self) -> float:
        text_w = QFontMetrics(self._label_font).horizontalAdvance(self._flash_text)
        extra = 46 + 22 + (34 if self._flash_kind == "busy" else 0)
        return float(min(_MAX_PILL_W, max(_PILL_W, text_w + extra)))

    def _pill_rect(self) -> QRectF:
        """The pill, anchored to the configured side so it grows inward."""

        w = self._pill_w
        horizontal = self._position.split("-")[1]
        if horizontal == "left":
            x = _SHADOW
        elif horizontal == "right":
            x = self.width() - _SHADOW - w
        else:
            x = (self.width() - w) / 2
        return QRectF(x, _SHADOW, w, _PILL_H)

    def _reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        x, y = compute_position(
            self._position, screen.availableGeometry(), (self.width(), self.height())
        )
        self.move(x, y)

    # -- painting -----------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        a = max(0.0, self._appear)
        if a <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(min(1.0, a))

        pill = self._pill_rect()
        # Spring scale from the pill's centre (OutBack overshoots past 1).
        scale = 0.82 + 0.18 * a
        cx, cy = pill.center().x(), pill.center().y()
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        radius = _PILL_H / 2
        self._paint_shadow(p, pill, radius)
        self._paint_body(p, pill, radius)
        if self._flashing():
            kind = self._flash_kind
            if kind == "ok":
                self._paint_badge(p, pill, _GREEN, check=True)
            elif kind == "error":
                self._paint_badge(p, pill, _RED, check=False)
            else:
                self._paint_mic(p, pill)
            right = 22 + (34 if kind == "busy" else 0)
            self._paint_text(p, pill, self._flash_text, right)
            if kind == "busy":
                self._paint_dots(p, pill)
        else:
            self._paint_mic(p, pill)
            self._paint_label(p, pill)
            if self._transcribing:
                self._paint_dots(p, pill)
            else:
                self._paint_wave(p, pill)
        p.end()

    def _paint_badge(self, p: QPainter, pill: QRectF, color: QColor, check: bool):
        c = QPointF(pill.left() + 24, pill.center().y())
        glow = QRadialGradient(c, 20)
        g = QColor(color)
        g.setAlpha(90)
        glow.setColorAt(0.0, g)
        g0 = QColor(color)
        g0.setAlpha(0)
        glow.setColorAt(1.0, g0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, 20, 20)
        p.setBrush(color)
        p.drawEllipse(c, 12, 12)
        pen = QPen(QColor(255, 255, 255), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if check:
            path = QPainterPath(QPointF(c.x() - 5, c.y() + 0.5))
            path.lineTo(c.x() - 1.5, c.y() + 4)
            path.lineTo(c.x() + 5.5, c.y() - 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        else:
            p.drawLine(QPointF(c.x() - 4, c.y() - 4), QPointF(c.x() + 4, c.y() + 4))
            p.drawLine(QPointF(c.x() + 4, c.y() - 4), QPointF(c.x() - 4, c.y() + 4))

    def _paint_text(self, p: QPainter, pill: QRectF, text: str, right: float):
        p.setFont(self._label_font)
        p.setPen(QColor(255, 255, 255, 235))
        rect = QRectF(
            pill.left() + 46, pill.top(), pill.width() - 46 - right, pill.height()
        )
        elided = QFontMetrics(self._label_font).elidedText(
            text, Qt.TextElideMode.ElideRight, int(rect.width())
        )
        p.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )

    def _paint_shadow(self, p: QPainter, pill: QRectF, radius: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        steps = 12
        for i in range(steps, 0, -1):
            spread = i * 1.6
            alpha = int(26 * (1 - i / (steps + 1)) ** 2)
            p.setBrush(QColor(0, 0, 0, alpha))
            r = pill.adjusted(-spread, -spread + 5, spread, spread + 5)
            p.drawRoundedRect(r, radius + spread, radius + spread)

    def _paint_body(self, p: QPainter, pill: QRectF, radius: float) -> None:
        body = QPainterPath()
        body.addRoundedRect(pill, radius, radius)
        grad = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        grad.setColorAt(0.0, QColor(34, 34, 40, 245))
        grad.setColorAt(1.0, QColor(12, 12, 16, 245))
        p.fillPath(body, grad)
        # Hairline rim: brighter on top, like light catching glass.
        rim = QLinearGradient(pill.topLeft(), pill.bottomLeft())
        rim.setColorAt(0.0, QColor(255, 255, 255, 46))
        rim.setColorAt(1.0, QColor(255, 255, 255, 10))
        p.setPen(QPen(rim, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def _paint_mic(self, p: QPainter, pill: QRectF) -> None:
        c = QPointF(pill.left() + 24, pill.center().y())
        # Glow that breathes with the voice level.
        glow_r = 15 + 7 * self._level
        glow = QRadialGradient(c, glow_r)
        g = QColor(_ORANGE)
        g.setAlpha(int(70 + 90 * self._level))
        glow.setColorAt(0.0, g)
        glow.setColorAt(1.0, QColor(255, 159, 10, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, glow_r, glow_r)

        dot = QRadialGradient(QPointF(c.x() - 3, c.y() - 4), 16)
        dot.setColorAt(0.0, QColor(255, 196, 82))
        dot.setColorAt(1.0, _ORANGE)
        p.setBrush(dot)
        p.drawEllipse(c, 12, 12)

        # Microphone glyph.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(c.x() - 2.6, c.y() - 7, 5.2, 9), 2.6, 2.6)
        pen = QPen(QColor(255, 255, 255), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(c.x() - 5, c.y() - 4.5, 10, 9), 200 * 16, 140 * 16)
        p.drawLine(QPointF(c.x(), c.y() + 4.6), QPointF(c.x(), c.y() + 7))

    def _paint_label(self, p: QPainter, pill: QRectF) -> None:
        p.setFont(self._label_font)
        p.setPen(QColor(255, 255, 255, 235))
        text = "Transcribing" if self._transcribing else "Listening"
        rect = QRectF(pill.left() + 46, pill.top(), 90, pill.height())
        p.drawText(
            rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )

    def _paint_wave(self, p: QPainter, pill: QRectF) -> None:
        bar_w, gap, max_h = 3.0, 3.2, 22.0
        total = _N_BARS * bar_w + (_N_BARS - 1) * gap
        x0 = pill.right() - 18 - total
        cy = pill.center().y()
        grad = QLinearGradient(
            QPointF(x0, cy - max_h / 2), QPointF(x0 + total, cy + max_h / 2)
        )
        grad.setColorAt(0.0, _SIRI[0])
        grad.setColorAt(0.5, _SIRI[1])
        grad.setColorAt(1.0, _SIRI[2])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        for i, v in enumerate(self._bars):
            h = max(bar_w, v * max_h)
            x = x0 + i * (bar_w + gap)
            p.drawRoundedRect(QRectF(x, cy - h / 2, bar_w, h), bar_w / 2, bar_w / 2)

    def _paint_dots(self, p: QPainter, pill: QRectF) -> None:
        t = time.monotonic() - self._t0
        r, gap = 2.6, 5.0
        x0 = pill.right() - 18 - (3 * 2 * r + 2 * gap)
        cy = pill.center().y()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            phase = (t * 2.4 - i * 0.18) % 1.0
            lift = math.sin(phase * math.pi) if phase < 0.5 else 0.0
            p.setBrush(QColor(255, 255, 255, int(130 + 125 * lift)))
            x = x0 + r + i * (2 * r + gap)
            p.drawEllipse(QPointF(x, cy - 4 * lift), r, r)
