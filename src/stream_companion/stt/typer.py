"""Text typing into the focused window with rolling-window dedup.

Two delivery methods:

* ``"paste"`` (default in the app) — put the text on the clipboard, press
  Ctrl+V once, then restore the previous clipboard text. Atomic and
  layout-independent; immune to apps that drop fast synthetic keystrokes
  (Windows 11 Notepad turned "Una cocina pequeña" into "Una ina pequeéa").
* ``"keys"`` — one synthetic keystroke per character, optionally paced by
  ``key_delay`` seconds. For apps that block paste.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)

METHOD_PASTE = "paste"
METHOD_KEYS = "keys"
_METHODS = (METHOD_PASTE, METHOD_KEYS)


def _default_clipboard() -> Optional[object]:
    try:
        from .clipboard import WindowsClipboard

        return WindowsClipboard()
    except Exception as exc:  # noqa: BLE001 - non-Windows or ctypes failure
        _LOGGER.info("Clipboard paste unavailable (%s); typing keystrokes", exc)
        return None


class TextTyper:
    """Type text into whichever window currently has focus.

    Maintains a rolling window of recently-typed text so that successive
    transcriptions of overlapping audio don't duplicate characters.
    """

    def __init__(
        self,
        *,
        controller_factory: Optional[Callable[[], object]] = None,
        window: int = 64,
        method: str = METHOD_KEYS,
        key_delay: float = 0.0,
        clipboard: Optional[object] = None,
        clipboard_factory: Optional[Callable[[], Optional[object]]] = None,
        paste_restore_delay: float = 0.25,
    ) -> None:
        if window < 0:
            raise ValueError("window must be >= 0")
        if method not in _METHODS:
            raise ValueError(f"method must be one of {_METHODS}, got {method!r}")
        self._controller_factory = controller_factory or self._default_controller
        self._window = window
        self._method = method
        self._key_delay = max(0.0, float(key_delay))
        self._clipboard = clipboard
        self._clipboard_factory = clipboard_factory or _default_clipboard
        self._clipboard_resolved = clipboard is not None
        self._paste_restore_delay = max(0.0, float(paste_restore_delay))
        self._lock = threading.Lock()
        self._typed_tail = ""
        self._controller: Optional[object] = None

    @property
    def method(self) -> str:
        return self._method

    def _default_controller(self) -> object:
        from pynput.keyboard import Controller  # type: ignore[import-not-found]

        return Controller()

    def _get_controller(self) -> object:
        if self._controller is None:
            self._controller = self._controller_factory()
        return self._controller

    def type_text(self, text: str, *, append_space: bool = True) -> str:
        """Type the given text. Returns the substring actually typed.

        Deduplicates against the recently-typed tail so repeated chunks
        (overlapping audio) don't repeat the same words.
        """

        if not text:
            return ""
        payload = text if not append_space else text + " "
        with self._lock:
            overlap = self._find_overlap(self._typed_tail, payload)
            to_type = payload[overlap:]
        if not to_type:
            _LOGGER.debug(
                "STT typer: fully dedup'd against tail (tail_len=%d, payload_len=%d)",
                len(self._typed_tail),
                len(payload),
            )
            return ""
        try:
            used = self._deliver(to_type)
        except Exception as exc:
            _LOGGER.exception("STT typer: delivering %r failed: %s", to_type, exc)
            raise
        with self._lock:
            self._typed_tail = (
                (self._typed_tail + to_type)[-self._window :] if self._window else ""
            )
        _LOGGER.info("STT typer %s %d chars: %r", used, len(to_type), to_type)
        return to_type

    def _deliver(self, text: str) -> str:
        """Send ``text`` to the focused window. Returns the method used."""

        if self._method == METHOD_PASTE:
            clipboard = self._get_clipboard()
            if clipboard is not None:
                try:
                    self._paste(clipboard, text)
                    return "pasted"
                except Exception as exc:  # noqa: BLE001 - fall back to keys
                    _LOGGER.warning(
                        "STT typer: clipboard paste failed (%s); typing keys", exc
                    )
        self._type_keys(text)
        return "typed"

    def _get_clipboard(self) -> Optional[object]:
        if not self._clipboard_resolved:
            self._clipboard = self._clipboard_factory()
            self._clipboard_resolved = True
        return self._clipboard

    def _paste(self, clipboard, text: str) -> None:
        """Ctrl+V ``text`` via the clipboard, then restore the old contents."""

        from pynput.keyboard import Key  # type: ignore[import-not-found]

        try:
            previous = clipboard.get_text()
        except Exception as exc:  # noqa: BLE001 - can't save; still paste
            _LOGGER.debug("STT typer: could not read clipboard: %s", exc)
            previous = None
        clipboard.set_text(text)
        controller = self._get_controller()
        controller.press(Key.ctrl)
        try:
            controller.press("v")
            controller.release("v")
        finally:
            controller.release(Key.ctrl)
        if previous is not None:
            # The target app reads the clipboard asynchronously when it
            # handles Ctrl+V; restoring too early would paste the old text.
            time.sleep(self._paste_restore_delay)
            try:
                clipboard.set_text(previous)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("STT typer: could not restore clipboard: %s", exc)

    def _type_keys(self, text: str) -> None:
        controller = self._get_controller()
        if self._key_delay <= 0:
            controller.type(text)
            return
        for ch in text:
            controller.type(ch)
            time.sleep(self._key_delay)

    def reset(self) -> None:
        with self._lock:
            self._typed_tail = ""

    def tail(self) -> str:
        with self._lock:
            return self._typed_tail

    @staticmethod
    def _find_overlap(existing: str, incoming: str) -> int:
        """Return the length of the longest suffix of ``existing`` that
        matches a prefix of ``incoming`` (capped by the dedup window).
        """

        if not existing or not incoming:
            return 0
        max_check = min(len(existing), len(incoming))
        for length in range(max_check, 0, -1):
            if existing[-length:] == incoming[:length]:
                return length
        return 0
