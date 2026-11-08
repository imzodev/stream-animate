"""Route each transcribed phrase: dictation edit → wake word → command → AI.

:meth:`VoiceCommandService.handle` is called by the STT engine *before* a
phrase is typed. It returns ``True`` when the phrase was a command, so the
engine doesn't also type "computer, open YouTube" into the focused window.

Commands run synchronously on the STT thread: that keeps order intact (the
next dictated sentence can't be typed before "new line" pressed Enter or
before "dictate in notes" focused Notepad). Audio keeps buffering meanwhile.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, List, Optional, Sequence

from .actions import ActionRunner, tab_number
from .ai_agent import AIAgent, PlanRejected
from .config import VoiceCommandsConfig
from .dictation_edits import apply_edit, build_lookup, match_edit
from .parser import CommandMatcher, fill_slots, normalize, split_wake_word

_LOGGER = logging.getLogger(__name__)

# Feedback kinds understood by the on-screen indicator.
OK, ERROR, BUSY, INFO = "ok", "error", "busy", "info"

Feedback = Callable[[str, str], None]

# Words that can sit in front of a spoken choice: "pick two", "the
# second one", "tab 2", "el dos".
_CHOICE_PREFIXES = frozenset(
    {
        "tab", "tabs", "pick", "picks", "number", "option", "choose",
        "select", "go", "the", "a", "el", "la", "los", "las", "por",
        "con", "usa", "use", "elige", "escoge",
    }
)  # fmt: skip
# …and after it: "the second one", "pick three please".
_CHOICE_SUFFIXES = frozenset({"one", "uno", "una", "please", "por", "favor"})


def choice_index(text: str) -> Optional[int]:
    """The 1-based option the user picked, or ``None``.

    Understands "two", "2", "second", "the second one", "pick three",
    "tab 2" and the Spanish equivalents ("el dos", "la segunda").
    """

    words = normalize(text).split()
    while words and words[0] in _CHOICE_PREFIXES:
        words.pop(0)
    while len(words) > 1 and words[-1] in _CHOICE_SUFFIXES:
        words.pop()
    if not words:
        return None
    number = tab_number(" ".join(words))
    if number is None:
        return None
    return number


class _PendingTabs:
    """Thread-safe holder for the candidates of an open tab picker.

    The list is written by the voice thread (when a command turns out
    to be ambiguous) and read from both the voice thread (a spoken
    choice) and the GUI thread (a click on the picker).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._candidates: List[Any] = []

    def set(self, candidates: Sequence[Any]) -> None:
        with self._lock:
            self._candidates = list(candidates)

    def peek(self) -> List[Any]:
        with self._lock:
            return list(self._candidates)

    def take(self) -> Optional[List[Any]]:
        """Return the candidates and clear them, or ``None`` if already gone.

        Atomic: the spoken choice and the mouse click race for the same
        list, and only one of them may win.
        """

        with self._lock:
            if not self._candidates:
                return None
            candidates = self._candidates
            self._candidates = []
            return candidates

    def clear(self) -> None:
        with self._lock:
            self._candidates = []


class VoiceCommandService:
    def __init__(
        self,
        config: VoiceCommandsConfig,
        *,
        runner: ActionRunner,
        engine_getter: Callable[[], object],
        agent: Optional[AIAgent] = None,
        on_feedback: Optional[Feedback] = None,
        picker: Any = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._engine_getter = engine_getter
        self._agent = agent if config.ai_fallback else None
        self._on_feedback = on_feedback or (lambda _t, _k: None)
        self._matcher = CommandMatcher(config.commands)
        self._edits = build_lookup()
        self._picker = picker
        self._pending = _PendingTabs()
        self._runner.set_ambiguous_handler(self._on_ambiguous)

    @property
    def config(self) -> VoiceCommandsConfig:
        return self._config

    def set_feedback(self, on_feedback: Optional[Feedback]) -> None:
        self._on_feedback = on_feedback or (lambda _t, _k: None)

    def set_picker(self, picker: Any) -> None:
        """Attach the tab picker (any object with the picker's API)."""

        self._picker = picker

    def handle(self, text: str, typing_active: bool) -> bool:
        """Handle ``text`` if it is a command. Returns ``True`` if consumed."""

        if not self._config.enabled or not text:
            return False
        engine = self._engine_getter()

        if self._config.dictation_editing and typing_active:
            edit = match_edit(text, self._edits)
            if edit is not None:
                typer = getattr(engine, "typer", None)
                _LOGGER.info("Dictation edit %r from %r", edit, text)
                self._feedback(apply_edit(edit, self._runner, typer, engine), OK)
                return True

        rest = split_wake_word(text, [self._config.wake_word])
        if rest is None:
            return False
        if not rest:
            self._feedback("Yes? Say a command", INFO)
            return True
        return self._run_command(rest)

    def _run_command(self, rest: str) -> bool:
        """Match and run a command phrase. Returns ``True`` if consumed."""

        # A pending tab choice wins over the command table: while the
        # picker is open, "computer, two" means "the second candidate",
        # not "the second tab of the focused window".
        pending = self._pending.peek()
        if pending:
            index = choice_index(rest)
            if index is not None:
                self._resolve_choice(index)
                return True
            # Not a choice — the user wants something else. Drop the
            # picker and carry on with the normal command table.
            self._dismiss_pending()

        matched = self._matcher.match(rest)
        if matched is not None:
            command, slots = matched
            actions = [fill_slots(a, slots) for a in command.actions]
            _LOGGER.info("Voice command %r → %s", rest, actions)
            result = self._runner.run(actions)
            self._feedback(result.message, OK if result.ok else ERROR)
            return True

        if self._agent is None:
            _LOGGER.info("Unknown voice command: %r", rest)
            self._feedback(f"Unknown command: {rest}", ERROR)
            return True
        self._run_ai(rest)
        return True

    # -- tab picking ---------------------------------------------------------

    def resolve_choice(self, index: int) -> None:
        """Pick a candidate by mouse. Safe to call from the GUI thread."""

        self._resolve_choice(index)

    def clear_pending(self) -> None:
        """Forget the pending candidates (the picker closed on its own)."""

        self._pending.clear()

    def _on_ambiguous(self, candidates: Sequence[Any], query: str) -> None:
        """Several tabs matched: open the picker instead of guessing."""

        self._pending.set(candidates)
        app = getattr(candidates[0], "app", "") if candidates else ""
        _LOGGER.info(
            "%d %s tabs match %r — showing the picker",
            len(candidates),
            app,
            query,
        )
        if self._picker is not None:
            self._picker.show_candidates(candidates, query=query)

    def _resolve_choice(self, index: int) -> None:
        candidates = self._pending.peek()
        if not candidates:
            return
        if not 1 <= index <= len(candidates):
            self._feedback(f"There are only {len(candidates)} tabs", ERROR)
            return
        # Take the list atomically: the spoken choice and the mouse click
        # race for it, and only one of them may win.
        candidates = self._pending.take()
        if candidates is None:
            return
        self._dismiss_picker()
        result = self._runner.select_tab_candidate(candidates[index - 1])
        self._feedback(result.message, OK if result.ok else ERROR)

    def _dismiss_pending(self) -> None:
        self._pending.clear()
        self._dismiss_picker()

    def _dismiss_picker(self) -> None:
        if self._picker is not None:
            self._picker.dismiss()

    def _run_ai(self, request: str) -> None:
        self._feedback("Thinking…", BUSY)
        names = [c.phrases[0] for c in self._config.commands]
        try:
            plan = self._agent.plan(request, names)
        except PlanRejected as exc:
            _LOGGER.warning("Voice AI plan rejected for %r: %s", request, exc)
            self._feedback(f"Blocked: {exc}", ERROR)
            return
        except Exception as exc:  # noqa: BLE001 - network/LLM failures
            _LOGGER.exception("Voice AI request failed for %r", request)
            self._feedback(f"AI error: {exc}", ERROR)
            return
        if not plan.actions:
            self._feedback(plan.say or "I can't do that", ERROR)
            return
        result = self._runner.run(plan.actions)
        if result.ok:
            self._feedback(plan.say or result.message, OK)
        else:
            self._feedback(result.message, ERROR)

    def _feedback(self, text: str, kind: str) -> None:
        try:
            self._on_feedback(text, kind)
        except Exception:  # noqa: BLE001 - UI must never break commands
            _LOGGER.exception("Voice feedback callback raised")
