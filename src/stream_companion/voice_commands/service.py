"""Route each transcribed phrase: dictation edit → wake word → command → AI.

:meth:`VoiceCommandService.handle` is called by the STT engine *before* a
phrase is typed. It returns ``True`` when the phrase was a command, so the
engine doesn't also type "computadora, abre Spotify" into the focused window.

Commands run synchronously on the STT thread: that keeps order intact (the
next dictated sentence can't be typed before "nueva línea" pressed Enter or
before "dicta en notas" focused Notepad). Audio keeps buffering meanwhile.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .actions import ActionRunner
from .ai_agent import AIAgent, PlanRejected
from .config import VoiceCommandsConfig
from .dictation_edits import apply_edit, build_lookup, match_edit
from .parser import CommandMatcher, fill_slots, split_wake_word

_LOGGER = logging.getLogger(__name__)

# Feedback kinds understood by the on-screen indicator.
OK, ERROR, BUSY, INFO = "ok", "error", "busy", "info"

Feedback = Callable[[str, str], None]


class VoiceCommandService:
    def __init__(
        self,
        config: VoiceCommandsConfig,
        *,
        runner: ActionRunner,
        engine_getter: Callable[[], object],
        agent: Optional[AIAgent] = None,
        on_feedback: Optional[Feedback] = None,
    ) -> None:
        self._config = config
        self._runner = runner
        self._engine_getter = engine_getter
        self._agent = agent if config.ai_fallback else None
        self._on_feedback = on_feedback or (lambda _t, _k: None)
        self._matcher = CommandMatcher(config.commands)
        self._edits = build_lookup()

    @property
    def config(self) -> VoiceCommandsConfig:
        return self._config

    def set_feedback(self, on_feedback: Optional[Feedback]) -> None:
        self._on_feedback = on_feedback or (lambda _t, _k: None)

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

        rest = split_wake_word(text, self._config.wake_words)
        if rest is None:
            return False
        if not rest:
            self._feedback("Yes? Say a command", INFO)
            return True

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
