"""Running a multi-step AI request without blocking speech recognition.

The agent's rounds (an LLM call, then actions, then another look at the
screen) can take many seconds, so they run on a worker thread. Meanwhile
an :class:`AgentMode` is open in the service: "stop" (no wake word
needed) cancels between steps, and a risky step waits for a spoken "yes"
or "no". Any new wake-word command also interrupts the agent.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from .modes import Mode, is_cancel
from .parser import normalize

_LOGGER = logging.getLogger(__name__)

CONFIRM_TIMEOUT_SECONDS = 20.0

_STOP = frozenset(
    {"stop", "stop it", "stop that", "abort", "halt", "para", "detente", "alto"}
)
_YES = frozenset(
    {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "do it", "go ahead",
     "confirm", "yes please", "si", "dale", "hazlo"}
)  # fmt: skip
_NO = frozenset({"no", "nope", "don t", "do not", "skip", "no thanks", "nah"})


class AgentMode(Mode):
    """Open while the AI works: listens for "stop" and for yes/no answers."""

    kind = "agent"

    def __init__(self) -> None:
        super().__init__()
        self.cancel = threading.Event()
        self._answered = threading.Event()
        self._answer: Optional[bool] = None
        self._lock = threading.Lock()
        self.question: Optional[str] = None

    # -- voice side (speech thread) -----------------------------------------

    def parse(self, text: str):
        norm = normalize(text)
        with self._lock:
            asking = self.question is not None
        if asking and norm in _YES:
            return "yes"
        if asking and norm in _NO:
            return "no"
        if norm in _STOP or is_cancel(norm):
            return "stop"
        return None

    def apply(self, parsed):
        from .actions import ActionResult

        if parsed == "stop":
            self.stop()
            return ActionResult(True, "Stopping…")
        with self._lock:
            self._answer = parsed == "yes"
        self._answered.set()
        return ActionResult(True, "OK" if parsed == "yes" else "Skipped")

    def stop(self) -> None:
        self.cancel.set()
        self._answered.set()
        self.finished = True

    def close(self) -> None:
        # Replaced by another command: the user moved on.
        self.stop()

    # -- worker side -------------------------------------------------------------

    def ask(self, question: str, timeout: float = CONFIRM_TIMEOUT_SECONDS) -> bool:
        """Wait for a spoken yes/no. ``False`` on "no", timeout or stop."""

        self._answered.clear()
        with self._lock:
            self.question = question
            self._answer = None
        if self.cancel.is_set():
            return False
        try:
            self._answered.wait(timeout)
        finally:
            with self._lock:
                self.question = None
        with self._lock:
            return bool(self._answer) and not self.cancel.is_set()
