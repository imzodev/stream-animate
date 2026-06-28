"""Application wiring for the Streaming Companion Tool MVP."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QApplication

from .fact_checker import AnswerPanel, FactCheckerEngine, FactCheckerEvent
from .hotkeys import HotkeyManager
from .llm.config import LLMConfig
from .models import OverlayConfig, Shortcut, STTConfig
from .overlay import OverlayWindow
from .sound import SoundPlayer
from .stt import STTEngine
from .tray_indicators import (
    TrayIndicatorState,
    compose_fact_check_state,
    compose_state,
)
from .triggers import TriggerMatcher, build_matcher_from_shortcuts
from . import registry

_LOGGER = logging.getLogger(__name__)


class ShortcutSignals(QObject):
    """Qt signals for thread-safe shortcut triggering."""

    triggered = Signal(Shortcut)
    stt_status = Signal(str)
    stt_phrase = Signal(str)  # emitted whenever a phrase is finalized
    fact_check_event = Signal(object)  # FactCheckerEvent for GUI-thread observers


class Application:
    """Coordinates the MVP services for hotkey-triggered overlays and audio."""

    def __init__(
        self,
        shortcuts: Iterable[Shortcut],
        *,
        sound_player: Optional[SoundPlayer] = None,
        overlay_window: Optional[OverlayWindow] = None,
        hotkey_manager: Optional[HotkeyManager] = None,
        stt_config: Optional[STTConfig] = None,
        stt_engine: Optional[STTEngine] = None,
        trigger_matcher: Optional[TriggerMatcher] = None,
        llm_config: Optional[LLMConfig] = None,
        fact_checker: Optional[FactCheckerEngine] = None,
        answer_panel: Optional[AnswerPanel] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._shortcuts: List[Shortcut] = list(shortcuts)
        self._sound_player = sound_player or SoundPlayer()
        self._overlay_window = overlay_window or OverlayWindow()
        self._hotkey_manager = hotkey_manager or HotkeyManager()
        self._logger = logger or _LOGGER

        self._sound_ids: Dict[Shortcut, str] = {}
        self._registered = False

        # STT (speech-to-text typing)
        self._stt_config = stt_config
        self._stt_engine: Optional[STTEngine] = stt_engine
        if self._stt_engine is None and self._stt_config is not None:
            self._stt_engine = STTEngine(
                self._stt_config,
                hotkey=self._stt_config.hotkey,
                on_phrase=self._on_stt_phrase,
            )

        # Trigger matcher for voice-triggered shortcuts. If the caller did
        # not inject one, build it from the current shortcut list + the
        # cooldown from the STT config (if any).
        if trigger_matcher is not None:
            self._trigger_matcher = trigger_matcher
        else:
            cooldown = (
                self._stt_config.trigger_cooldown_ms if self._stt_config else 1500
            )
            self._trigger_matcher, duplicates = build_matcher_from_shortcuts(
                self._shortcuts, cooldown_ms=cooldown
            )
            for word, label in duplicates:
                self._logger.warning(
                    "Duplicate voice trigger word %r (also bound to %s); only the first "
                    "registration will fire",
                    word,
                    label,
                )

        # LLM fact-checker. The engine is only constructed when both a
        # config and an API key are present; otherwise the feature is
        # silently absent.
        self._llm_config = llm_config
        self._answer_panel: Optional[AnswerPanel] = answer_panel
        if fact_checker is not None:
            self._fact_checker: Optional[FactCheckerEngine] = fact_checker
        elif llm_config is not None and llm_config.api_key():
            # The fact-checker reuses the STT engine's phrase stream
            # (no second mic handle, no second Whisper pass) and
            # inherits the same language hint. When no STT engine
            # is configured, the fact-checker degrades to a no-op
            # (its toggle handler short-circuits and logs a warning).
            fact_checker_language = (
                self._stt_config.language if self._stt_config is not None else "auto"
            )
            self._fact_checker = FactCheckerEngine(
                llm_config,
                stt_engine=self._stt_engine,
                language=fact_checker_language,
            )
        else:
            self._fact_checker = None
        if self._fact_checker is not None:
            self._fact_checker.add_observer(self._on_fact_check_event)

        # Create signals for thread-safe communication
        self._signals = ShortcutSignals()
        self._signals.triggered.connect(
            self._handle_shortcut_in_main_thread, Qt.ConnectionType.QueuedConnection
        )
        self._signals.stt_status.connect(
            self._handle_stt_status_in_main_thread, Qt.ConnectionType.QueuedConnection
        )
        self._signals.stt_phrase.connect(
            self._handle_stt_phrase_in_main_thread, Qt.ConnectionType.QueuedConnection
        )
        self._signals.fact_check_event.connect(
            self._handle_fact_check_event_in_main_thread,
            Qt.ConnectionType.QueuedConnection,
        )

    def start(self) -> None:
        """Preload assets, register shortcuts, and start the listener."""

        if self._registered:
            return

        self._preload_sounds()
        self._register_hotkeys()
        self._start_stt()

        if not self._shortcuts:
            self._logger.info(
                "No shortcuts configured; application will idle until configuration changes"
            )

        started = self._hotkey_manager.start()
        if started:
            self._registered = True
            self._logger.info(
                "Application started with %d shortcuts", len(self._shortcuts)
            )

    def stop(self) -> None:
        """Stop listening and release audio resources."""

        if not self._registered:
            return
        self._hotkey_manager.stop()
        self._sound_player.shutdown()
        self._stop_stt()
        self._stop_fact_checker()
        self._registered = False
        self._logger.info("Application stopped")

    def stt_engine(self) -> Optional[STTEngine]:
        return self._stt_engine

    def trigger_matcher(self) -> TriggerMatcher:
        return self._trigger_matcher

    def fact_checker(self) -> Optional[FactCheckerEngine]:
        return self._fact_checker

    def llm_config(self) -> Optional[LLMConfig]:
        return self._llm_config

    def answer_panel(self) -> Optional[AnswerPanel]:
        return self._answer_panel

    def set_answer_panel(self, panel: Optional[AnswerPanel]) -> None:
        """Attach (or replace) the streaming answer panel.

        The application holds a reference but does not own the panel —
        callers (typically ``run_application``) are responsible for
        showing/hiding and destroying it.
        """

        self._answer_panel = panel

    def set_llm_config(self, config: Optional[LLMConfig]) -> None:
        """Replace the active LLM config/engine at runtime.

        Restarts the engine so new settings (base_url, model, persona)
        take effect without restarting the whole application. The
        toggle hotkey is re-registered on the next ``_register_hotkeys``
        call so the new key takes effect.
        """

        self._stop_fact_checker()
        self._llm_config = config
        if config is not None and config.api_key():
            self._fact_checker = FactCheckerEngine(config)
            self._fact_checker.add_observer(self._on_fact_check_event)
        else:
            self._fact_checker = None
        self._logger.info(
            "LLM config replaced: configured=%s model=%s persona=%s hotkey=%s",
            bool(config),
            getattr(config, "model", None) if config else None,
            getattr(config, "persona", None) if config else None,
            getattr(config, "toggle_hotkey", None) if config else None,
        )
        if self._registered:
            self._register_hotkeys()

    def set_stt_config(self, config: Optional[STTConfig]) -> None:
        """Replace the active STT config/engine at runtime.

        Restarts the engine so new settings (model, language, device) take
        effect without restarting the whole application. Also rebuilds
        the trigger matcher so the new cooldown is honored.
        """

        self._stop_stt()
        self._stt_config = config
        if config is not None:
            self._stt_engine = STTEngine(
                config,
                hotkey=config.hotkey,
                on_phrase=self._on_stt_phrase,
            )
        else:
            self._stt_engine = None
        # Rebuild the matcher to honor the new cooldown value.
        cooldown = config.trigger_cooldown_ms if config is not None else 1500
        self._trigger_matcher, duplicates = build_matcher_from_shortcuts(
            self._shortcuts, cooldown_ms=cooldown
        )
        for word, label in duplicates:
            self._logger.warning(
                "Duplicate voice trigger word %r (also bound to %s); only the first "
                "registration will fire",
                word,
                label,
            )
        self._logger.info(
            "STT config replaced: enabled=%s always_on=%s hotkey=%s model=%s language=%s trigger_cooldown_ms=%s",
            bool(config and config.enabled),
            bool(config and config.always_on),
            getattr(config, "hotkey", None) if config else None,
            getattr(config, "model", None) if config else None,
            getattr(config, "language", None) if config else None,
            cooldown,
        )
        if self._registered:
            self._register_hotkeys()
            self._start_stt()

    def _start_stt(self) -> None:
        if self._stt_engine is None or self._stt_config is None:
            self._logger.info(
                "STT start skipped: engine=%s config=%s",
                self._stt_engine,
                self._stt_config,
            )
            return
        mode = self._stt_config.activation_mode()
        self._logger.info(
            "STT starting: mode=%s enabled=%s always_on=%s hotkey=%s model=%s type_into_window=%s voice_triggers=%s",
            mode,
            self._stt_config.enabled,
            self._stt_config.always_on,
            self._stt_config.hotkey,
            self._stt_config.model,
            self._stt_config.type_into_focused_window,
            self._stt_config.voice_triggers_enabled,
        )
        if mode == "off":
            self._logger.info("STT activation_mode is 'off'; engine not started")
            return

        # Apply the two independent sub-flags:
        # - voice_triggers_enabled controls whether the matcher scans
        #   phrases (the engine still transcribes so typing can work)
        # - type_into_focused_window is implemented as the engine's
        #   active state, but the two should be decoupled: voice
        #   triggers should fire even when typing is paused.
        self._stt_engine.set_triggers_enabled(self._stt_config.voice_triggers_enabled)

        if mode == "always":
            # In always-on mode, typing is also on (the user can still
            # disable it via the sub-checkbox, but the engine is
            # transcribing either way).
            self._stt_engine.set_active(
                self._stt_config.type_into_focused_window
                or self._stt_config.voice_triggers_enabled
            )
        else:
            # Hotkey mode: typing is paused by default. Voice triggers
            # are still on if the sub-checkbox is enabled.
            self._stt_engine.set_active(False)
        self._stt_engine.start()
        if mode == "always":
            self._logger.info(
                "STT always-on (model=%s, language=%s, type_into_window=%s, voice_triggers=%s)",
                self._stt_config.model,
                self._stt_config.language,
                self._stt_config.type_into_focused_window,
                self._stt_config.voice_triggers_enabled,
            )
        else:
            self._logger.info(
                "STT idle; press %s to toggle (model=%s, language=%s, voice_triggers=%s)",
                self._stt_config.hotkey,
                self._stt_config.model,
                self._stt_config.language,
                self._stt_config.voice_triggers_enabled,
            )

    def _stop_stt(self) -> None:
        if self._stt_engine is not None:
            self._logger.info("STT stopping")
            try:
                self._stt_engine.stop()
            except Exception:  # pragma: no cover - defensive
                self._logger.exception("Error stopping STT engine")
        else:
            self._logger.info("STT stop skipped: no engine")

    def _handle_stt_status_in_main_thread(self, status: str) -> None:
        self._logger.info("STT status: %s", status)

    def _on_stt_phrase(self, event) -> None:
        """Engine callback fired on the capture thread for each phrase.

        Emits a Qt signal so the matcher (and any UI observers) run on
        the main thread.
        """

        try:
            self._signals.stt_phrase.emit(event.raw_text or event.text)
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("Failed to emit stt_phrase signal")

    def _handle_stt_phrase_in_main_thread(self, phrase: str) -> None:
        """Match the phrase against registered trigger words and fire shortcuts.

        Note: typing the phrase into the focused window is intentionally
        NOT done here. Voice triggers only fire the configured
        shortcut's sound/overlay. Dictation-to-typing would be a
        separate opt-in feature.
        """

        if not phrase:
            return
        self._logger.debug("STT phrase received for trigger scan: %r", phrase)
        matched = self._trigger_matcher.match(phrase)
        if not matched:
            return
        # Find the actual Shortcut objects that match the phrases and
        # fire them. We deliberately re-resolve from the live shortcut
        # list (rather than caching on the matcher callback) so that
        # set_stt_config / configurator edits are reflected immediately.
        for matched_phrase in matched:
            for shortcut in self._shortcuts:
                if matched_phrase in shortcut.all_trigger_phrases():
                    self._logger.info(
                        "Voice trigger %r firing shortcut %s",
                        matched_phrase,
                        shortcut.label(),
                    )
                    self._signals.triggered.emit(shortcut)
                    break

    # ------------------------------------------------------------------
    # Fact-checker
    # ------------------------------------------------------------------

    def _on_fact_check_event(self, event: FactCheckerEvent) -> None:
        """Observer callback fired on the engine's background thread.

        Emits a Qt signal so the panel and tray observers run on the
        main thread.
        """

        try:
            self._signals.fact_check_event.emit(event)
        except Exception:  # pragma: no cover - defensive
            self._logger.exception("Failed to emit fact_check_event signal")

    def _handle_fact_check_event_in_main_thread(self, event: FactCheckerEvent) -> None:
        """GUI-thread handler for fact-checker events.

        Drives the answer panel (clear on new question, append tokens
        during streaming) and the persona label.
        """

        self._logger.debug("Fact-checker event: phase=%s", event.phase)
        if self._answer_panel is not None:
            if event.phase == "listening":
                self._answer_panel.clear()
                self._answer_panel.set_phase("listening")
                if self._llm_config is not None:
                    self._answer_panel.set_persona_label(self._llm_config.persona)
                    self._answer_panel.set_model(self._llm_config.model)
                self._answer_panel.show()
            elif event.phase == "thinking":
                self._answer_panel.set_phase("thinking")
                # The question text comes through on the thinking
                # event (the engine has finalized the buffered
                # phrases). Show it in the speech-bubble card.
                if event.text:
                    self._answer_panel.set_question(event.text)
                self._answer_panel.notify_stream_started()
            elif event.phase == "streaming":
                if event.kind == "reasoning":
                    # Show a "thinking…" indicator the first time
                    # reasoning tokens arrive.
                    self._answer_panel.set_phase("thinking")
                else:
                    self._answer_panel.set_phase("streaming")
                if event.delta:
                    self._answer_panel.append_token(event.delta, kind=event.kind)
            elif event.phase == "done":
                self._answer_panel.set_phase("done")
                self._answer_panel.notify_stream_finished()
            elif event.phase == "error":
                self._answer_panel.set_phase("error")
                self._answer_panel.notify_stream_finished()
                self._answer_panel.append_token(f"\n[error: {event.text}]")
            elif event.phase == "idle":
                # Hidden by the close button; do not re-show.
                pass

    def _on_fact_check_toggle(self) -> None:
        """Hotkey handler: toggle fact-checker listening."""
        if self._fact_checker is None:
            self._logger.warning(
                "Fact-checker hotkey pressed but no engine is configured"
            )
            return
        self._logger.info("Fact-checker toggle hotkey pressed")
        self._fact_checker.toggle()

    def _on_fact_check_cancel(self) -> None:
        """Hotkey handler: abort the in-flight LLM stream.

        Bound to the dedicated cancel hotkey (ESC by default) so
        the toggle hotkey never accidentally kills the answer the
        user just asked for. No-op if the engine is idle.
        """
        if self._fact_checker is None:
            self._logger.warning(
                "Fact-checker cancel hotkey pressed but no engine is configured"
            )
            return
        self._logger.info("Fact-checker cancel hotkey pressed")
        self._fact_checker.cancel()

    def _stop_fact_checker(self) -> None:
        if self._fact_checker is not None:
            self._logger.info("Fact-checker stopping")
            try:
                self._fact_checker.close()
            except Exception:  # pragma: no cover - defensive
                self._logger.exception("Error closing fact-checker engine")
            self._fact_checker = None

    def _preload_sounds(self) -> None:
        for shortcut in self._shortcuts:
            path = shortcut.sound_path
            if not path:
                continue
            sound_id = self._unique_sound_id(shortcut)
            success = self._sound_player.load(sound_id, path)
            if success:
                self._sound_ids[shortcut] = sound_id
            else:
                self._logger.warning("Failed to preload sound for %s", shortcut.hotkey)

    def _unique_sound_id(self, shortcut: Shortcut) -> str:
        base = shortcut.sound_id() or f"sound_{len(self._sound_ids) + 1}"
        candidate = base
        counter = 1
        existing = set(self._sound_ids.values())
        while candidate in existing:
            counter += 1
            candidate = f"{base}_{counter}"
        return candidate

    def _register_hotkeys(self) -> None:
        # Register direct hotkeys and collect chord suffix sequences
        seq_map: Dict[Tuple[str, ...], callable] = {}
        for shortcut in self._shortcuts:
            callback = lambda sc=shortcut: self._signals.triggered.emit(sc)
            if shortcut.hotkey:
                try:
                    self._hotkey_manager.register_hotkey(
                        shortcut.hotkey,
                        callback,
                    )
                except ValueError as exc:
                    self._logger.warning(
                        "Skipping duplicate or invalid hotkey '%s': %s",
                        shortcut.hotkey,
                        exc,
                    )
            elif shortcut.suffix:
                key = tuple(k.strip().lower() for k in shortcut.suffix)
                if key in seq_map:
                    self._logger.warning(
                        "Duplicate chord suffix sequence '%s' detected; later entry will override",
                        "+".join(key),
                    )
                seq_map[key] = callback

        # Configure chorded activator if present
        activator = registry.get_activator()
        if activator and seq_map:
            try:
                self._hotkey_manager.configure_chord_sequences(
                    activator.hotkey,
                    activator.timeout_ms,
                    seq_map,
                )
                self._logger.info(
                    "Chord activator configured: %s with %d suffix mappings (mode=%s)",
                    activator.hotkey,
                    len(seq_map),
                    getattr(activator, "mode", "press"),
                )
            except ValueError as exc:
                self._logger.warning("Failed to configure activator: %s", exc)

        # STT toggle hotkey (must not collide with the chord activator's own key)
        if (
            self._stt_engine is not None
            and self._stt_config is not None
            and self._stt_config.hotkey
            and not self._stt_config.always_on
        ):
            try:
                self._hotkey_manager.register_hotkey(
                    self._stt_config.hotkey,
                    self._on_stt_toggle,
                )
                self._logger.info(
                    "STT toggle hotkey registered: %s", self._stt_config.hotkey
                )
            except ValueError as exc:
                self._logger.warning("Failed to register STT toggle hotkey: %s", exc)

        # Fact-checker toggle hotkey. Independent of the STT toggle.
        if self._fact_checker is not None and self._llm_config is not None:
            hk = self._llm_config.toggle_hotkey
            if hk:
                try:
                    self._hotkey_manager.register_hotkey(
                        hk,
                        self._on_fact_check_toggle,
                    )
                    self._logger.info("Fact-checker toggle hotkey registered: %s", hk)
                except ValueError as exc:
                    self._logger.warning(
                        "Failed to register fact-checker toggle hotkey: %s", exc
                    )
            # Cancel hotkey. Aborts the in-flight LLM stream without
            # affecting the toggle hotkey. Bound to ESC by default;
            # set ``esc_hotkey`` to None in the config to disable.
            cancel_hk = self._llm_config.esc_hotkey
            if cancel_hk:
                try:
                    self._hotkey_manager.register_hotkey(
                        cancel_hk,
                        self._on_fact_check_cancel,
                    )
                    self._logger.info(
                        "Fact-checker cancel hotkey registered: %s", cancel_hk
                    )
                except ValueError as exc:
                    self._logger.warning(
                        "Failed to register fact-checker cancel hotkey: %s", exc
                    )

    def _on_stt_toggle(self) -> None:
        if self._stt_engine is None:
            self._logger.warning("STT hotkey pressed but no engine is configured")
            return
        if not self._stt_engine.is_running:
            self._logger.warning(
                "STT hotkey pressed but engine is not running (last_error=%s)",
                self._stt_engine.last_error,
            )
            return
        self._logger.info(
            "STT toggle hotkey pressed: was %s",
            "active" if self._stt_engine.is_active else "idle",
        )
        self._stt_engine.trigger()
        self._signals.stt_status.emit(
            "activated" if self._stt_engine.is_active else "deactivated"
        )

    def _handle_shortcut_in_main_thread(self, shortcut: Shortcut) -> None:
        """Handle shortcut trigger in the main Qt thread."""
        self._logger.info("Hotkey triggered: %s", shortcut.label())
        sound_id = self._sound_ids.get(shortcut)
        if sound_id:
            played = self._sound_player.play(sound_id)
            if not played:
                self._logger.warning("Unable to play sound for %s", shortcut.hotkey)
        elif shortcut.sound_path:
            self._logger.warning("Sound for %s was not preloaded", shortcut.hotkey)

        if shortcut.overlay:
            self._show_overlay(shortcut.overlay)

    def _show_overlay(self, config: OverlayConfig) -> None:
        size = None
        if config.width is not None and config.height is not None:
            size = (config.width, config.height)

        success = self._overlay_window.show_asset(
            config.file,
            duration_ms=config.duration_ms,
            position=(config.x, config.y),
            size=size,
        )
        if not success:
            self._logger.warning("Overlay failed to display: %s", config.file)
        else:
            size_str = f" size=({config.width},{config.height})" if size else ""
            self._logger.info(
                "Overlay displayed: file=%s position=(%s,%s) duration_ms=%s%s",
                config.file,
                config.x,
                config.y,
                config.duration_ms,
                size_str,
            )

    def build_tray_state(self) -> Optional[TrayIndicatorState]:
        """Compose the full tray state (STT dots + fact-check dot).

        Returns ``None`` when neither STT nor the fact-checker is
        configured (the tray icon should not be shown at all).
        """

        # STT component
        engine = self._stt_engine
        stt_configured = bool(self._stt_config and self._stt_config.enabled)
        if engine is None:
            if not stt_configured:
                stt_state = None
            else:
                stt_state = compose_state(
                    stt_configured=True,
                    engine_running=False,
                    triggers_enabled=False,
                    typing_active=False,
                )
        else:
            stt_state = compose_state(
                stt_configured=stt_configured,
                engine_running=engine.is_running,
                triggers_enabled=engine.triggers_enabled,
                typing_active=engine.is_active,
            )

        # Fact-checker component
        fc = self._fact_checker
        if self._llm_config is not None and fc is not None:
            fact_state = compose_fact_check_state(configured=True, phase=fc.phase)
        elif self._llm_config is not None:
            # Configured but engine not built (no API key) — show the
            # dot in the "idle" color so the user knows the feature
            # is wired but disabled.
            fact_state = compose_fact_check_state(configured=True, phase="idle")
        else:
            fact_state = compose_fact_check_state(configured=False, phase="idle")

        if stt_state is None:
            # No STT at all, but a configured fact-checker still
            # warrants an icon (just with the fact-check dot).
            if fact_state.configured:
                return TrayIndicatorState(enabled=False, fact_check=fact_state)
            return None
        return TrayIndicatorState(
            stt_active=stt_state.stt_active,
            typing_active=stt_state.typing_active,
            enabled=stt_state.enabled,
            fact_check=fact_state,
        )


def run_application(
    shortcuts: Iterable[Shortcut],
    stt_config: Optional[STTConfig] = None,
    llm_config: Optional[LLMConfig] = None,
) -> None:
    """Bootstrap the Qt application loop and start the MVP workflow."""
    from .tray_icon import TrayIcon

    # Ensure Qt uses software OpenGL before QApplication is constructed
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    except Exception:
        # Best-effort; continue if not supported on platform
        pass

    app = QApplication.instance() or QApplication([])

    # Build the answer panel eagerly so we can attach it to the
    # application before start() so the engine's first events have
    # somewhere to land.
    answer_panel: Optional[AnswerPanel] = None
    if llm_config is not None:
        answer_panel = AnswerPanel()

    application = Application(
        shortcuts,
        stt_config=stt_config,
        llm_config=llm_config,
        answer_panel=answer_panel,
    )
    application.start()

    _LOGGER.info(
        "STT initial state: engine=%s config=%s",
        "configured" if application.stt_engine() else "none",
        (
            (
                f"enabled={stt_config.enabled} always_on={stt_config.always_on} "
                f"hotkey={stt_config.hotkey} model={stt_config.model} language={stt_config.language}"
            )
            if stt_config
            else "no stt config"
        ),
    )
    _LOGGER.info(
        "LLM initial state: engine=%s persona=%s",
        "configured" if application.fact_checker() else "none",
        llm_config.persona if llm_config is not None else "n/a",
    )

    # Create system tray icon with quit callback. The toggle handlers and
    # state provider are bound methods on ``Application`` so the same logic
    # backs both the tray menu and the global hotkeys.
    tray = TrayIcon(
        on_quit=lambda: (application.stop(), app.quit()),
        on_open_configurator=_open_configurator,
        on_toggle_stt=application._on_stt_toggle,
        on_toggle_fact_check=application._on_fact_check_toggle,
        stt_state_provider=application.build_tray_state,
    )

    def _refresh_tray_label(event=None) -> None:
        # The optional ``event`` argument lets the same function back both
        # engines: ``STTEngine.add_observer`` fires observers with no
        # arguments, while ``FactCheckerEngine.add_observer`` fires them
        # with a ``FactCheckerEvent``. We ignore the payload and just
        # repaint the tray icon.
        tray.refresh_stt_label()

    # Wire observers so the tray updates on every state change.
    engine = application.stt_engine()
    if engine is not None:
        engine.add_observer(_refresh_tray_label)
    fc_engine = application.fact_checker()
    if fc_engine is not None:
        fc_engine.add_observer(_refresh_tray_label)

    tray.show()
    tray.refresh_stt_label()

    try:
        app.exec()
    finally:
        if engine is not None:
            engine.remove_observer(_refresh_tray_label)
        if fc_engine is not None:
            fc_engine.remove_observer(_refresh_tray_label)
        application.stop()
        if answer_panel is not None:
            answer_panel.close()
        tray.hide()


def _open_configurator() -> None:
    """Open the configurator window from the tray menu."""
    from .configurator import ConfiguratorWindow

    # Check if configurator is already open
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, ConfiguratorWindow):
            widget.raise_()
            widget.activateWindow()
            return

    # Create new configurator window
    window = ConfiguratorWindow()
    window.show()
