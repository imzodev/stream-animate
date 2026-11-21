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
import time
from typing import Any, Callable, Optional, Sequence

from .actions import ActionRunner
from .agent_run import AgentMode
from .ai_agent import AIAgent, PlanRejected
from .config import VoiceCommandsConfig
from .dictation_edits import apply_edit, build_lookup, match_edit
from .modes import LabelsMode, ListMode, Mode, ModeSlot, choice_index
from .parser import CommandMatcher, fill_slots, split_wake_word

__all__ = ["VoiceCommandService", "choice_index"]

_LOGGER = logging.getLogger(__name__)

# Feedback kinds understood by the on-screen indicator.
OK, ERROR, BUSY, INFO = "ok", "error", "busy", "info"

Feedback = Callable[[str, str], None]

# How long a bare wake word waits for the rest of the command. Whisper
# splits "computer, youtube tab" into two utterances when there is a
# pause after the wake word.
WAKE_MERGE_SECONDS = 3.0

# Pause between AI rounds so the screen can settle (a menu opening, a
# page loading) before the next look.
AGENT_SETTLE_SECONDS = 0.6


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
        background: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # ``background``: multi-step AI requests run on a worker thread so
        # speech keeps flowing ("stop", "yes"). Off in tests: they run inline.
        self._background = background
        self._sleep = sleep
        self._config = config
        self._runner = runner
        self._engine_getter = engine_getter
        self._agent = agent if config.ai_fallback else None
        self._on_feedback = on_feedback or (lambda _t, _k: None)
        self._matcher = CommandMatcher(config.commands)
        self._edits = build_lookup()
        self._picker = picker
        self._modes = ModeSlot()
        self._merge_at = 0.0
        self._runner.set_ambiguous_handler(self._on_ambiguous)
        self._runner.set_choice_handler(self._on_choice)
        self._runner.set_labels_handler(self._on_labels)
        self._runner.set_grid_handler(self._on_grid)
        self._overlay: Any = None

    @property
    def config(self) -> VoiceCommandsConfig:
        return self._config

    def set_feedback(self, on_feedback: Optional[Feedback]) -> None:
        self._on_feedback = on_feedback or (lambda _t, _k: None)

    def set_screen_overlay(self, overlay: Any) -> None:
        """Attach the click-through overlay that draws labels and the grid."""

        self._overlay = overlay

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
        if rest is None and self._config.overlay_without_wake_word:
            # While a picker / labels / the grid is open, its answers
            # ("two", "click 14") need no wake word. Anything else is
            # ordinary speech and is typed as usual.
            if self._answer_mode(text):
                return True
        if rest is None:
            # No wake word here. If the wake word was spoken a moment
            # ago on its own ("computer" … "youtube tab"), join them.
            merged = self._take_merged(text)
            if merged is not None and self._is_actionable(merged):
                _LOGGER.info("Joined the pending wake word with %r", text)
                return self._run_command(merged)
            return False
        if not rest:
            # A bare wake word: the command is probably coming next.
            self._arm_merge()
            self._feedback("Yes? Say a command", INFO)
            return True
        self._clear_merge()
        return self._run_command(rest)

    def _run_command(self, rest: str) -> bool:
        """Match and run a command phrase. Returns ``True`` if consumed."""

        # An open mode wins over the command table: while the picker is
        # open, "computer, two" means "the second candidate", not "the
        # second tab of the focused window".
        if self._answer_mode(rest):
            return True
        # Not an answer — the user wants something else. Close whatever
        # is open and carry on with the normal command table.
        self.close_mode()

        matched = self._matcher.match(rest)
        if matched is not None:
            command, slots = matched
            actions = [fill_slots(a, slots) for a in command.actions]
            _LOGGER.info("Voice command %r → %s", rest, actions)
            result = self._runner.run(actions)
            if result.unmatched and self._agent is not None:
                # e.g. "open the weather" with no app by that name: the
                # AI may still know what to do.
                self._run_ai(rest)
                return True
            self._feedback(result.message, OK if result.ok else ERROR)
            return True

        if self._agent is None:
            _LOGGER.info("Unknown voice command: %r", rest)
            self._feedback(f"Unknown command: {rest}", ERROR)
            return True
        self._run_ai(rest)
        return True

    def _is_actionable(self, rest: str) -> bool:
        """Would ``rest`` do something on its own (a command or a choice)?

        Used to decide whether a phrase that followed a bare wake word
        belongs to that command or is just ordinary speech.
        """

        mode = self._modes.get()
        if mode is not None and mode.parse(rest) is not None:
            return True
        return self._matcher.match(rest) is not None

    # -- wake word merge -----------------------------------------------------

    def _arm_merge(self) -> None:
        """Remember a bare wake word: the command may be coming next."""

        self._merge_at = time.monotonic()

    def _clear_merge(self) -> None:
        self._merge_at = 0.0

    def _take_merged(self, text: str) -> Optional[str]:
        """The phrase to run as the command, if a wake word is pending.

        Whisper splits "computer, youtube tab" into two utterances when
        there is a pause after the wake word. The bare wake word waits
        a moment for the rest of the phrase.
        """

        if not self._merge_at:
            return None
        if time.monotonic() - self._merge_at > WAKE_MERGE_SECONDS:
            self._clear_merge()
            return None
        self._clear_merge()
        return text

    # -- modes: pickers, labels, grid ------------------------------------------

    @property
    def mode(self) -> Optional[Mode]:
        """The open mode, if any (for tests and the UI)."""

        return self._modes.get()

    def open_mode(self, mode: Mode) -> None:
        """Close whatever is open and show ``mode``."""

        old = self._modes.replace(mode)
        if old is not None and old is not mode:
            old.close()
        mode.open()

    def close_mode(self) -> None:
        mode = self._modes.replace(None)
        if mode is not None:
            mode.close()

    def _answer_mode(self, text: str) -> bool:
        """Offer ``text`` to the open mode. ``True`` if it was an answer."""

        mode = self._modes.get()
        if mode is None:
            return False
        parsed = mode.parse(text)
        if parsed is None:
            return False
        result = mode.apply(parsed)
        if mode.finished:
            self._modes.clear_if(mode)
        if result is not None:
            self._feedback(result.message, OK if result.ok else ERROR)
        return True

    def resolve_choice(self, index: int) -> None:
        """Pick a candidate by mouse. Safe to call from the GUI thread."""

        mode = self._modes.get()
        if mode is None:
            return
        result = mode.pick(index)
        if mode.finished:
            self._modes.clear_if(mode)
        if result is not None:
            self._feedback(result.message, OK if result.ok else ERROR)

    def clear_pending(self) -> None:
        """The picker closed on its own (timeout): forget its candidates."""

        mode = self._modes.get()
        if isinstance(mode, ListMode):
            mode.finished = True
            self._modes.clear_if(mode)

    def _on_ambiguous(self, candidates: Sequence[Any], query: str) -> None:
        """Several tabs matched: open the picker instead of guessing."""

        app = getattr(candidates[0], "app", "") if candidates else ""
        _LOGGER.info(
            "%d %s tabs match %r — showing the picker", len(candidates), app, query
        )
        self.open_mode(
            ListMode(
                candidates,
                self._runner.select_tab_candidate,
                self._picker,
                noun="tab",
                query=query,
                app=app,
            )
        )

    def _on_labels(self, hwnd: int, targets: Sequence[Any], sticky: bool) -> None:
        _LOGGER.info("Showing %d number labels", len(targets))
        self.open_mode(
            LabelsMode(self._runner, self._overlay, hwnd, targets, sticky=sticky)
        )

    def _on_grid(self, rect) -> None:
        from .mouse_grid import GridMode

        self.open_mode(GridMode(self._runner, self._overlay, rect))

    def _on_choice(
        self,
        candidates: Sequence[Any],
        query: str,
        noun: str,
        resolve: Callable[[Any], Any],
    ) -> None:
        """Several apps / windows matched: let the user pick one."""

        _LOGGER.info(
            "%d %ss match %r — showing the picker", len(candidates), noun, query
        )
        self.open_mode(
            ListMode(candidates, resolve, self._picker, noun=noun, query=query)
        )

    def stop_agent(self) -> None:
        """Stop a running AI request (the stop hotkey). Any thread."""

        mode = self._modes.get()
        if isinstance(mode, AgentMode):
            mode.stop()
            self._modes.clear_if(mode)
            self._feedback("Stopping…", INFO)

    def _run_ai(self, request: str) -> None:
        if hasattr(self._agent, "step"):
            self._start_agent(request)
            return
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

    # -- multi-step AI -----------------------------------------------------------

    def _start_agent(self, request: str) -> None:
        mode = AgentMode()
        self.open_mode(mode)
        self._feedback("Thinking…", BUSY)
        if self._background:
            threading.Thread(
                target=self._agent_loop,
                args=(request, mode),
                name="voice-ai",
                daemon=True,
            ).start()
        else:
            self._agent_loop(request, mode)

    def _agent_loop(self, request: str, mode: AgentMode) -> None:
        names = [c.phrases[0] for c in self._config.commands]
        history: list = []
        rounds = getattr(self._agent, "max_rounds", 1)
        try:
            for round_no in range(1, rounds + 1):
                if mode.cancel.is_set():
                    self._feedback("Stopped", INFO)
                    return
                plan, targets = self._agent.step(request, names, history, round_no)
                if mode.cancel.is_set():
                    self._feedback("Stopped", INFO)
                    return
                if not plan.actions:
                    ok = bool(history) and history[-1]["ok"]
                    self._feedback(
                        plan.say or ("Done" if ok else "I can't do that"),
                        OK if ok else ERROR,
                    )
                    return
                if plan.risky:
                    question = (
                        f"{plan.risky[0][:1].upper()}{plan.risky[0][1:]}? Say yes or no"
                    )
                    self._feedback(question, INFO)
                    if not mode.ask(question):
                        self._feedback("Cancelled: nothing was clicked", INFO)
                        return
                self._runner.use_targets(*targets)
                result = self._runner.run(plan.actions)
                history.append(
                    {"actions": plan.actions, "ok": result.ok, "result": result.message}
                )
                if plan.done and result.ok:
                    self._feedback(plan.say or result.message, OK)
                    return
                if mode.cancel.is_set():
                    self._feedback("Stopped", INFO)
                    return
                if round_no == rounds:
                    break
                step = result.message if result.ok else f"Retrying: {result.message}"
                self._feedback(f"Step {round_no}: {step}", BUSY)
                self._sleep(AGENT_SETTLE_SECONDS)
            last = history[-1]["result"] if history else ""
            self._feedback(f"Stopped after {rounds} steps. {last}".strip(), ERROR)
        except PlanRejected as exc:
            _LOGGER.warning("Voice AI plan rejected for %r: %s", request, exc)
            self._feedback(f"Blocked: {exc}", ERROR)
        except Exception as exc:  # noqa: BLE001 - network/LLM failures
            _LOGGER.exception("Voice AI request failed for %r", request)
            self._feedback(f"AI error: {exc}", ERROR)
        finally:
            self._runner.use_targets(None, [])
            mode.finished = True
            self._modes.clear_if(mode)

    def _feedback(self, text: str, kind: str) -> None:
        try:
            self._on_feedback(text, kind)
        except Exception:  # noqa: BLE001 - UI must never break commands
            _LOGGER.exception("Voice feedback callback raised")
