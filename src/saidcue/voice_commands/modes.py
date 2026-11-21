"""Choice modes: short-lived states that read the user's next phrase.

While a picker, the number labels, the mouse grid or a yes/no question
is on screen, the next phrase is usually an answer to it ("two",
"click 14", "seven", "yes") rather than a new command. A *mode* owns that
state. :class:`~.service.VoiceCommandService` keeps at most one open and
offers it every phrase first.

Each mode splits handling into :meth:`Mode.parse` (no side effects: is
this phrase meant for me?) and :meth:`Mode.apply`, so the service can ask
before it commits — e.g. to decide whether a phrase spoken without the
wake word is an answer or ordinary dictation.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from .parser import normalize

_LOGGER = logging.getLogger(__name__)

# Phrases that close whatever is open without doing anything.
CANCEL_PHRASES = frozenset(
    {
        "cancel", "never mind", "nevermind", "close", "dismiss", "stop", "escape",
        "hide", "hide numbers", "hide the numbers", "hide labels", "close numbers",
        "close grid", "hide grid", "cancel grid", "no thanks", "cancelar", "cerrar",
    }
)  # fmt: skip

# Words that can sit in front of a spoken choice: "pick two", "the
# second one", "tab 2", "el dos".
_CHOICE_PREFIXES = frozenset(
    {
        "tab", "tabs", "pick", "picks", "number", "option", "choose",
        "select", "go", "the", "a", "el", "la", "los", "las", "por",
        "con", "usa", "use", "elige", "escoge", "app", "window",
    }
)  # fmt: skip
# …and after it: "the second one", "pick three please".
_CHOICE_SUFFIXES = frozenset({"one", "uno", "una", "please", "por", "favor"})


def is_cancel(text: str) -> bool:
    return normalize(text) in CANCEL_PHRASES


def choice_index(text: str, app: str = "") -> Optional[int]:
    """The 1-based option the user picked (1-9), or ``None``.

    Understands "two", "2", "second", "the second one", "pick three",
    "tab 2" and the Spanish equivalents ("el dos", "la segunda").
    ``app`` is the app the pending candidates belong to, so "terminal
    two" counts as a choice when the terminal picker is the open one.
    """

    from .actions import tab_number

    words = normalize(text).split()
    if app == "terminal" and words and words[0] == "terminal":
        words.pop(0)
    elif app == "browser" and words and words[0] == "browser":
        words.pop(0)
    while words and words[0] in _CHOICE_PREFIXES:
        words.pop(0)
    while len(words) > 1 and words[-1] in _CHOICE_SUFFIXES:
        words.pop()
    if not words:
        return None
    return tab_number(" ".join(words))


@dataclass(frozen=True)
class ListItem:
    """A generic picker row: what to show, and the value behind it."""

    label: str
    detail: str = ""
    value: Any = field(default=None, compare=False)


class Mode:
    """Base class. Subclasses implement :meth:`parse` and :meth:`apply`."""

    kind = "mode"

    def __init__(self) -> None:
        self.finished = False

    def open(self) -> None:
        """Show the mode's UI (called once, by the service)."""

    def close(self) -> None:
        """Hide the mode's UI. Must be safe to call more than once."""

    def parse(self, text: str) -> Optional[Any]:
        """The parsed answer if ``text`` is meant for this mode, else ``None``."""

        raise NotImplementedError

    def apply(self, parsed: Any):
        """Act on a parsed answer; returns an ``ActionResult``."""

        raise NotImplementedError

    def handle(self, text: str):
        parsed = self.parse(text)
        if parsed is None:
            return None
        return self.apply(parsed)

    def pick(self, index: int):
        """A mouse pick (only list pickers have one)."""

        return None


class ListMode(Mode):
    """A numbered list (tabs, apps, windows): say the number or click a row.

    The spoken choice and the mouse click race for the same list; only
    the first one wins.
    """

    kind = "list"

    def __init__(
        self,
        candidates: Sequence[Any],
        resolve: Callable[[Any], Any],
        picker: Any,
        *,
        noun: str = "tab",
        query: str = "",
        app: str = "",
    ) -> None:
        super().__init__()
        self.candidates: List[Any] = list(candidates)
        self._resolve = resolve
        self._picker = picker
        self.noun = noun
        self.query = query
        self.app = app
        self._lock = threading.Lock()

    def open(self) -> None:
        if self._picker is None:
            return
        if self.noun == "tab":
            self._picker.show_candidates(self.candidates, query=self.query)
        else:
            self._picker.show_candidates(
                self.candidates, query=self.query, noun=self.noun
            )

    def close(self) -> None:
        if self._picker is not None:
            self._picker.dismiss()

    def parse(self, text: str):
        if is_cancel(text):
            return "cancel"
        return choice_index(text, self.app)

    def apply(self, parsed):
        from .actions import ActionResult

        if parsed == "cancel":
            self._finish()
            return ActionResult(True, "Cancelled")
        if not 1 <= parsed <= len(self.candidates):
            return ActionResult(
                False, f"There are only {len(self.candidates)} {self.noun}s"
            )
        candidate = self._take(parsed)
        if candidate is None:
            return None
        self.close()
        return self._resolve(candidate)

    def pick(self, index: int):
        """Mouse pick from the GUI thread. The picker has already closed."""

        if not 1 <= index <= len(self.candidates):
            return None
        candidate = self._take(index)
        if candidate is None:
            return None
        return self._resolve(candidate)

    def _take(self, index: int) -> Optional[Any]:
        with self._lock:
            if self.finished:
                return None
            self.finished = True
            return self.candidates[index - 1]

    def _finish(self) -> None:
        with self._lock:
            self.finished = True
        self.close()


class ModeSlot:
    """Thread-safe holder for the one open mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: Optional[Mode] = None

    def get(self) -> Optional[Mode]:
        with self._lock:
            mode = self._mode
            if mode is not None and mode.finished:
                self._mode = None
                return None
            return mode

    def replace(self, mode: Optional[Mode]) -> Optional[Mode]:
        """Install ``mode``; returns the previous one (still open)."""

        with self._lock:
            old, self._mode = self._mode, mode
        return old

    def clear_if(self, mode: Mode) -> None:
        with self._lock:
            if self._mode is mode:
                self._mode = None
