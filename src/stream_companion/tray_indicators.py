"""Tray-icon state and painting for the Streaming Companion Tool.

The tray icon has two independent status indicators:

* **STT active** (red dot, top-right corner) — the STT engine is
  running, the mic is open, and the engine is processing audio. Always
  required for anything to happen.
* **Typing active** (blue dot, bottom-right corner) — the engine is
  also typing the dictated text into whichever window is focused.
  This is independent of the STT indicator: the user can have STT
  on (red) without typing (no blue) and vice versa, although in
  practice the engine only transcribes when one or the other is on.

The :class:`TrayIndicatorState` dataclass captures the current
state, and :func:`compose_tray_icon` renders the icon by overlaying
the colored dots on a base icon. Both functions are pure (no Qt
state changes that persist), which keeps them trivial to unit-test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)

# Indicator color palette. Bright, high-contrast colors that read
# well over the dark app icon.
COLOR_STT_ACTIVE = QColor(220, 38, 38)  # red
COLOR_TYPING_ACTIVE = QColor(37, 99, 235)  # blue
COLOR_LISTENING = QColor(245, 158, 11)  # amber — STT running, no triggers
COLOR_DISABLED = QColor(120, 120, 120)  # gray
COLOR_BORDER = QColor(255, 255, 255)  # white ring around each dot

# Where on the icon each dot is anchored (top-right vs bottom-right).
# Values are (x_anchor, y_anchor) where (0,0) is top-left and (1,1) is
# bottom-right of the rendered icon.
DOT_TOP_RIGHT = (0.78, 0.12)
DOT_BOTTOM_RIGHT = (0.78, 0.72)


@dataclass(frozen=True)
class TrayIndicatorState:
    """Current visual state of the tray icon.

    Attributes:
        stt_active: True when the STT engine is running and processing
            audio (regardless of whether it's typing or just scanning
            for triggers). This drives the red dot in the top-right.
        typing_active: True when the engine is currently typing into
            the focused window. Drives the blue dot in the bottom-right.
        enabled: True when STT is configured at all. When False, the
            icon shows no dots (idle / grayed out).
    """

    stt_active: bool = False
    typing_active: bool = False
    enabled: bool = True

    @property
    def any_active(self) -> bool:
        return self.enabled and (self.stt_active or self.typing_active)

    @property
    def tooltip(self) -> str:
        if not self.enabled:
            return "Streaming Companion Tool (STT disabled)"
        parts = ["Streaming Companion Tool"]
        flags = []
        if self.stt_active:
            flags.append("listening")
        if self.typing_active:
            flags.append("typing into focused window")
        if flags:
            parts.append("STT: " + " + ".join(flags))
        else:
            parts.append("STT: idle")
        return " — ".join(parts)


# ---------------------------------------------------------------------------
# Base icon discovery
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the project root (parent of ``src/``)."""

    return Path(__file__).resolve().parents[2]


def _candidate_icon_paths() -> list[Path]:
    """Return the list of paths checked for a base tray icon (in order)."""

    assets = _project_root() / "assets"
    return [
        assets / "icon.png",
        assets / "tray_icon.png",
        assets / "icon.ico",
    ]


def find_base_icon_pixmap(size: int = 64) -> Optional[QPixmap]:
    """Return the first existing icon as a square ``QPixmap`` of ``size``.

    Returns ``None`` when no asset is found. Callers are expected to
    fall back to a generated default (a colored square with a letter).
    """

    for path in _candidate_icon_paths():
        if path.is_file():
            pix = QPixmap(str(path))
            if not pix.isNull():
                return pix.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return None


def _fallback_base_pixmap(size: int) -> QPixmap:
    """Return a synthetic default icon when no asset file is present.

    A solid dark-blue square with the white letters "SC" centered
    in it. Looks reasonable in any platform's tray.
    """

    pix = QPixmap(size, size)
    pix.fill(QColor(31, 41, 55))  # slate-800
    painter = QPainter(pix)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setPointSizeF(size * 0.36)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "SC")
    finally:
        painter.end()
    return pix


# ---------------------------------------------------------------------------
# Painting
# ---------------------------------------------------------------------------


def _draw_dot(painter: QPainter, *, rect: QRect, fill: QColor) -> None:
    """Draw a single indicator dot with a thin white ring around it."""

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # White ring for contrast against the base icon
    painter.setPen(QPen(COLOR_BORDER, max(1, rect.width() * 0.10)))
    painter.setBrush(QBrush(fill))
    painter.drawEllipse(rect)


def compose_tray_icon(
    state: TrayIndicatorState,
    *,
    size: int = 64,
    base_pixmap: Optional[QPixmap] = None,
) -> QIcon:
    """Render the tray icon for the given indicator state.

    Args:
        state: What to draw. ``enabled=False`` produces an icon with no
            dots; ``stt_active=True`` adds a red dot in the top-right;
            ``typing_active=True`` adds a blue dot in the bottom-right.
        size: Edge length of the square icon in pixels. The default of
            64 looks right on Linux/Windows/macOS tray bars.
        base_pixmap: Optional override for the base icon. When ``None``,
            the project assets are searched; if none exist, a synthetic
            "SC" icon is generated.

    Returns:
        A ``QIcon`` ready to be installed on a ``QSystemTrayIcon``.
    """

    if base_pixmap is None:
        base_pixmap = find_base_icon_pixmap(size) or _fallback_base_pixmap(size)
    else:
        base_pixmap = base_pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    image: QImage = base_pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        dot_diameter = max(8, int(size * 0.32))
        if state.enabled and state.stt_active:
            x, y = DOT_TOP_RIGHT
            rect = QRect(
                int(size * x) - dot_diameter // 2,
                int(size * y) - dot_diameter // 2,
                dot_diameter,
                dot_diameter,
            )
            _draw_dot(painter, rect=rect, fill=COLOR_STT_ACTIVE)
        if state.enabled and state.typing_active:
            x, y = DOT_BOTTOM_RIGHT
            rect = QRect(
                int(size * x) - dot_diameter // 2,
                int(size * y) - dot_diameter // 2,
                dot_diameter,
                dot_diameter,
            )
            _draw_dot(painter, rect=rect, fill=COLOR_TYPING_ACTIVE)
    finally:
        painter.end()

    pix = QPixmap.fromImage(image)
    icon = QIcon(pix)
    # Some tray hosts only honor the "active" pixmap; provide the same
    # image for every state so the dot is always visible.
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Disabled):
        icon.addPixmap(pix, mode)
    return icon


def compose_state(
    *,
    stt_configured: bool,
    engine_running: bool,
    triggers_enabled: bool,
    typing_active: bool,
) -> TrayIndicatorState:
    """Build a :class:`TrayIndicatorState` from raw engine flags.

    Centralizes the "what does the icon look like" logic so the
    application layer doesn't have to re-derive the same conditions.

    - ``stt_configured``: STT is enabled in the config (or by user choice).
    - ``engine_running``: the engine's background loop is alive.
    - ``triggers_enabled``: voice triggers are turned on (master switch).
    - ``typing_active``: the engine's typing active flag is set.
    """

    if not stt_configured:
        return TrayIndicatorState(enabled=False)
    # The red dot means "the engine is listening". Listening is true
    # when the engine is running AND either typing or triggers is on
    # (otherwise the loop discards audio).
    listening = engine_running and (typing_active or triggers_enabled)
    return TrayIndicatorState(
        stt_active=listening,
        typing_active=typing_active,
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def indicator_size_for(size: int) -> Tuple[int, int]:
    """Return the (width, height) of the rendered icon."""

    return (size, size)
