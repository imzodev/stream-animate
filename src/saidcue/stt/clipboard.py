"""Minimal Windows clipboard access (Unicode text) via ctypes.

Used by :class:`~saidcue.stt.typer.TextTyper` to paste dictated
text in one Ctrl+V instead of synthesizing one keystroke per character —
apps like the Windows 11 Notepad drop fast synthetic keystrokes, which
garbled dictation ("Una cocina" -> "Una ina").
"""

from __future__ import annotations

import sys
import time
from typing import Optional

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


class ClipboardError(RuntimeError):
    """Raised when the clipboard cannot be opened or written."""


class WindowsClipboard:
    """Read/write Unicode text on the Windows clipboard."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ClipboardError("WindowsClipboard is only available on Windows")
        import ctypes
        import ctypes.wintypes as wt

        self._ctypes = ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.OpenClipboard.argtypes = [wt.HWND]
        user32.OpenClipboard.restype = wt.BOOL
        user32.CloseClipboard.restype = wt.BOOL
        user32.EmptyClipboard.restype = wt.BOOL
        user32.GetClipboardData.argtypes = [wt.UINT]
        user32.GetClipboardData.restype = wt.HANDLE
        user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
        user32.SetClipboardData.restype = wt.HANDLE
        user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
        user32.IsClipboardFormatAvailable.restype = wt.BOOL
        kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wt.HGLOBAL
        kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
        kernel32.GlobalUnlock.restype = wt.BOOL
        kernel32.GlobalFree.argtypes = [wt.HGLOBAL]
        kernel32.GlobalFree.restype = wt.HGLOBAL

        self._user32 = user32
        self._kernel32 = kernel32

    def _open(self) -> None:
        # Another app may hold the clipboard briefly; retry for ~0.5 s.
        for _ in range(50):
            if self._user32.OpenClipboard(None):
                return
            time.sleep(0.01)
        raise ClipboardError("Could not open the clipboard (held by another app)")

    def get_text(self) -> Optional[str]:
        """Return the clipboard's Unicode text, or ``None`` if it has none."""

        self._open()
        try:
            if not self._user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                return None
            handle = self._user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            ptr = self._kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return self._ctypes.wstring_at(ptr)
            finally:
                self._kernel32.GlobalUnlock(handle)
        finally:
            self._user32.CloseClipboard()

    def set_text(self, text: str) -> None:
        """Replace the clipboard contents with ``text``."""

        data = (text + "\0").encode("utf-16-le")
        handle = self._kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardError("GlobalAlloc failed")
        ptr = self._kernel32.GlobalLock(handle)
        if not ptr:
            self._kernel32.GlobalFree(handle)
            raise ClipboardError("GlobalLock failed")
        self._ctypes.memmove(ptr, data, len(data))
        self._kernel32.GlobalUnlock(handle)

        self._open()
        try:
            self._user32.EmptyClipboard()
            if not self._user32.SetClipboardData(_CF_UNICODETEXT, handle):
                self._kernel32.GlobalFree(handle)
                raise ClipboardError("SetClipboardData failed")
            # On success the system owns ``handle``; don't free it.
        finally:
            self._user32.CloseClipboard()
