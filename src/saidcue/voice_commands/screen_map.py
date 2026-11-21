"""Map between physical screen pixels and Qt's logical pixels.

UI Automation rectangles, ``GetCursorPos`` and mouse input use *physical*
pixels. Qt places windows in *logical* (device-independent) pixels, and
on a mixed-DPI setup every monitor has its own scale. Overlays that draw
on top of UI elements (number labels, the mouse grid) have to convert.

The maths is pure (:class:`Monitor`, :func:`to_logical`,
:func:`to_physical`) so it is tested without a display; :func:`qt_monitors`
builds the table from the live screens.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

_LOGGER = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]  # left, top, width, height
Point = Tuple[int, int]


@dataclass(frozen=True)
class Monitor:
    name: str
    physical: Rect
    logical: Rect

    @property
    def scale(self) -> float:
        lw = self.logical[2] or 1
        return self.physical[2] / lw


def _contains(rect: Rect, x: float, y: float) -> bool:
    left, top, w, h = rect
    return left <= x < left + w and top <= y < top + h


def _nearest(monitors: Sequence[Monitor], x: float, y: float, which: str) -> Monitor:
    def distance(m: Monitor) -> float:
        left, top, w, h = getattr(m, which)
        dx = max(left - x, 0, x - (left + w - 1))
        dy = max(top - y, 0, y - (top + h - 1))
        return dx * dx + dy * dy

    return min(monitors, key=distance)


def monitor_at_physical(monitors: Sequence[Monitor], x: float, y: float) -> Monitor:
    for m in monitors:
        if _contains(m.physical, x, y):
            return m
    return _nearest(monitors, x, y, "physical")


def monitor_at_logical(monitors: Sequence[Monitor], x: float, y: float) -> Monitor:
    for m in monitors:
        if _contains(m.logical, x, y):
            return m
    return _nearest(monitors, x, y, "logical")


def to_logical(monitors: Sequence[Monitor], x: float, y: float) -> Point:
    """Physical point → logical point, using the monitor it lies on."""

    if not monitors:
        return int(x), int(y)
    m = monitor_at_physical(monitors, x, y)
    px, py, pw, ph = m.physical
    lx, ly, lw, lh = m.logical
    return (
        round(lx + (x - px) * lw / (pw or 1)),
        round(ly + (y - py) * lh / (ph or 1)),
    )


def to_physical(monitors: Sequence[Monitor], x: float, y: float) -> Point:
    """Logical point → physical point (the inverse of :func:`to_logical`)."""

    if not monitors:
        return int(x), int(y)
    m = monitor_at_logical(monitors, x, y)
    px, py, pw, ph = m.physical
    lx, ly, lw, lh = m.logical
    return (
        round(px + (x - lx) * pw / (lw or 1)),
        round(py + (y - ly) * ph / (lh or 1)),
    )


def rect_to_logical(monitors: Sequence[Monitor], rect: Rect) -> Rect:
    left, top, w, h = rect
    x0, y0 = to_logical(monitors, left, top)
    x1, y1 = to_logical(monitors, left + w, top + h)
    return x0, y0, max(x1 - x0, 0), max(y1 - y0, 0)


# -- live screens -------------------------------------------------------------


def _win32_monitors() -> Dict[str, Rect]:
    """``{device name: physical rect}`` from ``EnumDisplayMonitors``."""

    if sys.platform != "win32":
        return {}
    import ctypes.wintypes as wt

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("rcMonitor", wt.RECT),
            ("rcWork", wt.RECT),
            ("dwFlags", wt.DWORD),
            ("szDevice", wt.WCHAR * 32),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    out: Dict[str, Rect] = {}
    proc_t = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
    )

    def cb(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            out[info.szDevice] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        return True

    user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    return out


def qt_monitors(screens: Optional[Sequence[object]] = None) -> List[Monitor]:
    """Build the monitor table from Qt's screens.

    The physical rectangle comes from Win32 when the device names line
    up. Otherwise it is derived from Qt itself: on Windows Qt keeps each
    screen's top-left at its native position and scales only the size.
    """

    if screens is None:
        from PySide6.QtGui import QGuiApplication

        screens = QGuiApplication.screens()
    try:
        native = _win32_monitors()
    except Exception:  # noqa: BLE001 - fall back to Qt's own numbers
        _LOGGER.debug("EnumDisplayMonitors failed", exc_info=True)
        native = {}
    out: List[Monitor] = []
    for screen in screens:
        g = screen.geometry()
        logical = (g.x(), g.y(), g.width(), g.height())
        physical = native.get(screen.name())
        if physical is None:
            dpr = screen.devicePixelRatio() or 1.0
            physical = (g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr))
        out.append(Monitor(screen.name(), physical, logical))
    return out
