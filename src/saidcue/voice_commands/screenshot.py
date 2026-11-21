"""Screenshots for the AI's opt-in vision (``voice_commands.ai_vision``).

Captures a physical-pixel rectangle with GDI (works from any thread, no
Qt GUI calls), scales it down and encodes it as a base64 JPEG. Only used
when the user turned vision on: the image is sent to their LLM provider.
"""

from __future__ import annotations

import base64
import ctypes
import logging
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

_LOGGER = logging.getLogger(__name__)

Rect = Tuple[int, int, int, int]  # left, top, width, height (physical px)

MAX_WIDTH = 1280
JPEG_QUALITY = 70


@dataclass(frozen=True)
class Shot:
    jpeg_b64: str
    rect: Rect  # what the image shows, in physical screen pixels

    def to_screen(self, x: int, y: int) -> Tuple[int, int]:
        """A point on the image in 0-1000 units → physical screen pixels."""

        left, top, w, h = self.rect
        return left + round(w * x / 1000), top + round(h * y / 1000)


def _grab_bgra(rect: Rect) -> bytes:
    """Raw top-down BGRA pixels of ``rect`` from the screen."""

    import ctypes.wintypes as wt

    left, top, w, h = rect
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    # 64-bit handles: without explicit types ctypes truncates them to int.
    user32.GetDC.restype = wt.HDC
    user32.GetDC.argtypes = [wt.HWND]
    user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
    gdi32.CreateCompatibleDC.restype = wt.HDC
    gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
    gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wt.HGDIOBJ
    gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
    gdi32.BitBlt.argtypes = (
        [wt.HDC] + [ctypes.c_int] * 4 + [wt.HDC] + [ctypes.c_int] * 2 + [wt.DWORD]
    )
    gdi32.GetDIBits.argtypes = [
        wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT
    ]  # fmt: skip
    gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wt.HDC]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
        gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, left, top, SRCCOPY | CAPTUREBLT)
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = w
        header.biHeight = -h  # top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(header), 0)
        return buf.raw
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def encode_jpeg(bgra: bytes, width: int, height: int) -> str:
    """BGRA pixels → base64 JPEG, scaled down to :data:`MAX_WIDTH`."""

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage

    image = QImage(bgra, width, height, width * 4, QImage.Format.Format_RGB32)
    if width > MAX_WIDTH:
        image = image.scaledToWidth(
            MAX_WIDTH, Qt.TransformationMode.SmoothTransformation
        )
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", JPEG_QUALITY)
    buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii")


def capture(rect: Rect) -> Optional[Shot]:
    """Screenshot ``rect`` (physical pixels), or ``None`` if it fails."""

    if sys.platform != "win32":
        return None
    left, top, w, h = (int(v) for v in rect)
    if w <= 0 or h <= 0:
        return None
    try:
        return Shot(encode_jpeg(_grab_bgra((left, top, w, h)), w, h), (left, top, w, h))
    except Exception:  # noqa: BLE001 - vision is best effort
        _LOGGER.exception("Screenshot failed")
        return None


def clip(rect: Rect, bounds: Optional[Rect]) -> Rect:
    """``rect`` clipped to ``bounds`` (e.g. a maximized window's -8 px border)."""

    if bounds is None:
        return rect
    left, top, w, h = rect
    bl, bt, bw, bh = bounds
    x0, y0 = max(left, bl), max(top, bt)
    x1, y1 = min(left + w, bl + bw), min(top + h, bt + bh)
    return x0, y0, max(x1 - x0, 0), max(y1 - y0, 0)
