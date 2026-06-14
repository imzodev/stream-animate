"""Text typing into the focused window with rolling-window dedup."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

_LOGGER = logging.getLogger(__name__)


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
    ) -> None:
        if window < 0:
            raise ValueError("window must be >= 0")
        self._controller_factory = controller_factory or self._default_controller
        self._window = window
        self._lock = threading.Lock()
        self._typed_tail = ""
        self._controller: Optional[object] = None

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
            controller = self._get_controller()
            controller.type(to_type)
        except Exception as exc:
            _LOGGER.exception(
                "STT typer: pynput controller.type(%r) failed: %s", to_type, exc
            )
            raise
        with self._lock:
            self._typed_tail = (
                (self._typed_tail + to_type)[-self._window :] if self._window else ""
            )
        _LOGGER.info("STT typer typed %d chars: %r", len(to_type), to_type)
        return to_type

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
