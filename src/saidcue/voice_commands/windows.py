"""Win32 window helpers: list, find, focus, and inspect the foreground window.

Focusing uses the "tap Alt first" trick: Windows refuses
``SetForegroundWindow`` from a background process unless it recently
received input, and a synthetic Alt press satisfies that rule.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .parser import normalize

_LOGGER = logging.getLogger(__name__)

TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")
BROWSER_CLASSES = ("Chrome_WidgetWin_1", "MozillaWindowClass")


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    # Executable name without ".exe", lowercase ("spotify", "chrome").
    process: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.class_name in TERMINAL_CLASSES


class Win32Windows:
    """Real implementation (Windows only). Tests inject a fake."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("window control is only supported on Windows")
        import ctypes.wintypes as wt

        self._wt = wt
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._proc_t = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def _info(self, hwnd) -> WindowInfo:
        u = self._user32
        n = u.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, title, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, cls, 256)
        return WindowInfo(int(hwnd or 0), title.value, cls.value, self._process(hwnd))

    def _process(self, hwnd) -> str:
        """Lowercase executable name of the window's process, or ``""``."""

        pid = self._wt.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
        if not handle:
            return ""
        try:
            size = self._wt.DWORD(520)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)
            ):
                return ""
            name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            return name[:-4] if name.endswith(".exe") else name
        finally:
            kernel32.CloseHandle(handle)

    def list_windows(self) -> List[WindowInfo]:
        """Visible, titled, top-level windows in Z-order (front first)."""

        out: List[WindowInfo] = []
        u = self._user32

        def cb(hwnd, _lparam):
            if u.IsWindowVisible(hwnd) and u.GetWindowTextLengthW(hwnd) > 0:
                info = self._info(hwnd)
                if info.class_name not in ("Progman", "WorkerW"):
                    out.append(info)
            return True

        u.EnumWindows(self._proc_t(cb), 0)
        return out

    def foreground(self) -> Optional[WindowInfo]:
        hwnd = self._user32.GetForegroundWindow()
        return self._info(hwnd) if hwnd else None

    def focus(self, hwnd: int) -> bool:
        from pynput.keyboard import Controller, Key  # type: ignore[import-not-found]

        u = self._user32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        for _ in range(3):
            kb = Controller()
            kb.press(Key.alt)
            kb.release(Key.alt)
            u.SetForegroundWindow(hwnd)
            time.sleep(0.05)
            if u.GetForegroundWindow() == hwnd:
                return True
        return u.GetForegroundWindow() == hwnd

    # -- window state ---------------------------------------------------------

    def minimize(self, hwnd: int) -> bool:
        return bool(self._user32.ShowWindow(hwnd, 6)) or True  # SW_MINIMIZE

    def maximize(self, hwnd: int) -> bool:
        return bool(self._user32.ShowWindow(hwnd, 3)) or True  # SW_MAXIMIZE

    def restore(self, hwnd: int) -> bool:
        return bool(self._user32.ShowWindow(hwnd, 9)) or True  # SW_RESTORE

    def close(self, hwnd: int) -> bool:
        """Ask the window to close (WM_CLOSE): apps still prompt to save."""

        return bool(self._user32.PostMessageW(hwnd, 0x0010, 0, 0))

    def rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        """``(left, top, right, bottom)`` in physical pixels."""

        r = self._wt.RECT()
        if not self._user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return r.left, r.top, r.right, r.bottom


def find_window(windows: List[WindowInfo], query: str) -> Optional[WindowInfo]:
    """Best window whose title contains ``query`` (accent/case-insensitive).

    Prefers a title that *starts* with the query, then the front-most match.
    """

    q = normalize(query)
    if not q:
        return None
    matches = [w for w in windows if q in normalize(w.title)]
    if not matches:
        # "go to spotify" while Spotify's title is the song playing.
        procs = [w for w in windows if w.process and normalize(w.process) == q]
        procs = procs or [
            w for w in windows if w.process and normalize(w.process).startswith(q)
        ]
        return procs[0] if procs else None
    for w in matches:
        if normalize(w.title).startswith(q):
            return w
    return matches[0]
