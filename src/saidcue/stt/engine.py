"""STT engine orchestrator: audio capture -> whisper -> text typer.

The :class:`STTEngine` runs the capture/transcribe loop in a background
thread and can be started/stopped from any thread. Voice triggers and
typing are controlled independently.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from ..models import STTConfig
from .audio import AudioCapture, AudioCaptureError
from .segmenter import UtteranceSegmenter
from .transcriber import WhisperTranscriber
from .typer import TextTyper

_LOGGER = logging.getLogger(__name__)

# Mic blocks are small so a pause is noticed quickly; the segmenter groups
# them into whole utterances before anything reaches Whisper.
_CAPTURE_BLOCK_SECONDS = 0.1


@dataclass
class STTEvent:
    """Event emitted by the STT engine after a successful transcription."""

    text: str
    raw_text: str
    rms: float
    language: str


class STTEngine:
    """Top-level orchestrator: audio capture -> whisper -> text typer."""

    def __init__(
        self,
        config: STTConfig,
        *,
        audio_capture: Optional[AudioCapture] = None,
        transcriber: Optional[WhisperTranscriber] = None,
        typer: Optional[TextTyper] = None,
        on_phrase: Optional[Callable[[STTEvent], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        hotkey: Optional[str] = None,
        command_handler: Optional[Callable[[str, bool], bool]] = None,
    ) -> None:
        self._config = config
        # Voice commands: called with (text, typing_active) before a phrase
        # is typed; returning True means "this was a command, don't type it".
        self._command_handler = command_handler
        self._stt_hotkey = hotkey
        self._audio = audio_capture or AudioCapture(
            sample_rate=config.sample_rate,
            chunk_seconds=_CAPTURE_BLOCK_SECONDS,
            device=config.device,
        )
        self._segmenter = UtteranceSegmenter(
            self._audio.sample_rate,
            silence_threshold=config.silence_rms_threshold,
            end_silence_ms=config.end_silence_ms,
            max_utterance_seconds=config.max_utterance_seconds,
        )
        self._transcriber = transcriber or WhisperTranscriber(model_name=config.model)
        self._typer = typer or TextTyper(
            window=config.dedup_window,
            method=(
                config.typing_method
                if config.typing_method in ("paste", "keys")
                else "paste"
            ),
            key_delay=config.key_delay_ms / 1000.0,
        )
        self._on_phrase = on_phrase
        self._on_status = on_status

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active = False
        # Voice triggers can be active independently of typing. When
        # ``_triggers_enabled`` is True, the engine still transcribes
        # audio chunks (and emits phrase events) even if ``_active`` is
        # False. The default is True so the voice trigger feature
        # works out of the box; users who only want typing can call
        # ``set_triggers_enabled(False)`` to suppress trigger scanning
        # while keeping typing under its own activation control.
        self._triggers_enabled = True
        self._typed_total_chars = 0
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._mic_open: bool = False
        self._observers: List[Callable[[], None]] = []
        # Phrase observers receive the STTEvent for every successful
        # transcription. They are independent of the ``on_phrase``
        # constructor callback (which still fires too) so listeners
        # like the fact-checker can subscribe and unsubscribe at
        # runtime without losing the original wired callback.
        self._phrase_observers: List[Callable[["STTEvent"], None]] = []
        # Live UI hints (read from the GUI thread; plain float/bool
        # assignments are atomic in CPython, so no lock is needed).
        self._input_level = 0.0
        self._transcribing = False

    @property
    def config(self) -> STTConfig:
        return self._config

    @property
    def typer(self) -> TextTyper:
        """The typer used for dictation (voice commands reuse it)."""

        return self._typer

    def set_command_handler(
        self, handler: Optional[Callable[[str, bool], bool]]
    ) -> None:
        self._command_handler = handler

    @property
    def input_level(self) -> float:
        """RMS of the most recent mic block (0 when not listening)."""

        return self._input_level

    @property
    def is_transcribing(self) -> bool:
        """``True`` while Whisper is processing an utterance."""

        return self._transcribing

    @property
    def transcriber(self) -> WhisperTranscriber:
        """The shared :class:`WhisperTranscriber` instance.

        The application layer can pass this to other engines (e.g.
        the fact-checker) so the Whisper model is loaded into
        memory only once.
        """

        return self._transcriber

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_running(self) -> bool:
        """``True`` if the background loop thread is alive (regardless of active state)."""

        return self._thread is not None and self._thread.is_alive()

    @property
    def triggers_enabled(self) -> bool:
        """``True`` when voice-trigger scanning is on (independent of typing)."""

        return self._triggers_enabled

    @property
    def typed_total_chars(self) -> int:
        return self._typed_total_chars

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def status(self) -> dict:
        """Return a JSON-serializable snapshot of the engine state.

        Useful for logging, the tray icon, and one-shot CLI debugging.
        """

        with self._lock:
            active = self._active
            thread_alive = self._thread is not None and self._thread.is_alive()
        transcriber_loaded = self._transcriber.is_loaded()
        return {
            "running": thread_alive,
            "active": active,
            "mic_open": self._mic_open,
            "transcriber_loaded": transcriber_loaded,
            "model": self._config.model,
            "language": self._config.language,
            "device": self._config.device,
            "chunk_seconds": self._config.chunk_seconds,
            "always_on": self._config.always_on,
            "hotkey": self._stt_hotkey,
            "typed_chars": self._typed_total_chars,
            "started_at": self._started_at,
            "last_error": self._last_error,
        }

    def start(self) -> None:
        """Start the capture/transcribe loop in a background thread.

        The loop will be *active* (transcribing) only if ``self._active`` was
        already ``True`` before calling ``start()``. Callers who want the
        engine to start in the active state should set it via ``set_active``
        or assign it directly before invoking this method.
        """

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                _LOGGER.info("STT start requested but engine is already running")
                return
            self._stop_event.clear()
            self._last_error = None
            self._started_at = time.time()
            self._thread = threading.Thread(
                target=self._run, name="stt-engine", daemon=True
            )
            self._thread.start()
        _LOGGER.info(
            "STT engine started: model=%s language=%s device=%s always_on=%s",
            self._config.model,
            self._config.language,
            self._config.device,
            self._config.always_on,
        )
        self._started_at = time.time()
        self._emit_status("started")

    def stop(self) -> None:
        """Stop the capture/transcribe loop and release the audio device."""

        with self._lock:
            if self._thread is None:
                _LOGGER.info("STT stop requested but engine is not running")
                return
            self._active = False
            self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
        self._audio.stop()
        with self._lock:
            self._thread = None
            self._mic_open = False
            self._started_at = None
        _LOGGER.info("STT engine stopped (typed_chars=%d)", self._typed_total_chars)
        self._emit_status("stopped")

    def set_active(self, active: bool) -> None:
        """Toggle the engine's active state without tearing down the thread.

        When deactivated mid-stream, the capture and transcription simply
        pause; the audio stream is left open and resumed on the next
        activation. This keeps toggle latency low for hotkey-driven use.
        """

        with self._lock:
            if self._active == active:
                _LOGGER.debug("STT set_active(%s) is a no-op", active)
                return
            self._active = active
            if not active:
                self._typer.reset()
        if active:
            _LOGGER.info(
                "STT activated (model=%s, language=%s)",
                self._config.model,
                self._config.language,
            )
        else:
            _LOGGER.info("STT deactivated")
        self._emit_status("activated" if active else "deactivated")

    def trigger(self) -> None:
        """Convenience: toggle the active state (used by the hotkey mode)."""

        with self._lock:
            current = self._active
        new_state = not current
        _LOGGER.info(
            "STT toggle: %s -> %s (hotkey=%s, model=%s)",
            "active" if current else "idle",
            "active" if new_state else "idle",
            self._stt_hotkey,
            self._config.model,
        )
        self.set_active(new_state)

    def set_triggers_enabled(self, enabled: bool) -> None:
        """Enable or disable voice-trigger scanning independently of typing.

        Triggers are enabled by default. When disabled, the engine still
        transcribes (so typing works in the same activation mode) but
        the engine loop discards transcription results. Useful for
        streamers who only want typing, or who want to temporarily
        silence voice-triggered sound effects.
        """

        with self._lock:
            if self._triggers_enabled == enabled:
                return
            self._triggers_enabled = enabled
        _LOGGER.info("STT voice triggers %s", "enabled" if enabled else "disabled")
        self._notify_observers()

    def _run(self) -> None:
        try:
            self._audio.start()
        except AudioCaptureError as exc:
            self._last_error = str(exc)
            with self._lock:
                self._mic_open = False
            _LOGGER.error("Cannot start STT microphone: %s", exc)
            self._emit_status(f"error:{exc}")
            return
        with self._lock:
            self._mic_open = True
        _LOGGER.info(
            "STT engine loop running (mic=%s, sample_rate=%d, block=%.2fs, silence_threshold=%.4f, end_silence=%dms, max_utterance=%.1fs)",
            (
                self._audio.device
                if hasattr(self._audio, "device")
                else self._config.device
            ),
            self._audio.sample_rate,
            self._audio.chunk_seconds,
            self._config.silence_rms_threshold,
            self._config.end_silence_ms,
            self._config.max_utterance_seconds,
        )
        blocks_received = 0
        utterances = 0
        utterances_transcribed = 0
        utterances_triggered = 0
        last_log = time.time()
        was_active = False
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    active = self._active
                    triggers_enabled = self._triggers_enabled
                if was_active and not active:
                    # Typing was just toggled off mid-phrase: finish and
                    # type what was said while it was on, rather than
                    # losing the tail of the sentence. Audio still queued
                    # was captured while typing was on, so include it.
                    pending = []
                    while (block := self._audio.get_chunk(timeout=0)) is not None:
                        pending.extend(self._segmenter.feed(block))
                    for utterance in pending + self._segmenter.flush():
                        utterances += 1
                        if self._process_chunk(utterance) == "transcribed":
                            utterances_transcribed += 1
                was_active = active
                listening_for_commands = self._command_handler is not None
                if not active and not triggers_enabled and not listening_for_commands:
                    # Nothing to do: drop audio (and any half-heard
                    # utterance) so we don't build up a backlog while paused.
                    self._audio.get_chunk(timeout=0.25)
                    self._segmenter.reset()
                    continue
                block = self._audio.get_chunk(timeout=0.5)
                if block is None:
                    continue
                blocks_received += 1
                self._input_level = (
                    float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
                )
                # Whisper only ever sees whole utterances (speech up to a
                # pause), never arbitrary slices that split words.
                for utterance in self._segmenter.feed(block):
                    utterances += 1
                    # When typing is paused, still transcribe so voice
                    # triggers can fire; `type_into_window=False` skips typing.
                    result = self._process_chunk(utterance, type_into_window=active)
                    if result == "transcribed":
                        utterances_transcribed += 1
                    elif result == "trigger_only":
                        utterances_triggered += 1
                # Periodically log activity even if nothing was transcribed,
                # so users can see that the loop is alive and receiving audio.
                if time.time() - last_log > 5.0:
                    _LOGGER.info(
                        "STT heartbeat: %d audio blocks, %d utterances, %d transcribed, %d trigger-only (in_speech=%s, active=%s, triggers_enabled=%s, model_loaded=%s)",
                        blocks_received,
                        utterances,
                        utterances_transcribed,
                        utterances_triggered,
                        self._segmenter.in_speech,
                        active,
                        triggers_enabled,
                        self._transcriber.is_loaded(),
                    )
                    last_log = time.time()
        finally:
            with self._lock:
                self._mic_open = False
            self._audio.stop()
            self._segmenter.reset()
            _LOGGER.info(
                "STT engine loop stopped (audio_blocks=%d, utterances=%d, transcribed=%d, trigger_only=%d)",
                blocks_received,
                utterances,
                utterances_transcribed,
                utterances_triggered,
            )

    def _process_chunk(
        self, chunk: np.ndarray, *, type_into_window: bool = True
    ) -> str:
        """Transcribe one utterance. Returns a status tag for the loop counters.

        Args:
            chunk: The audio buffer to transcribe — a whole utterance from
                :class:`UtteranceSegmenter` in the engine loop.
            type_into_window: When True (the default), the typed text is
                also injected into the focused window. When False, the
                transcription still happens (so voice triggers can be
                scanned) but typing is skipped. This is used by the
                engine loop to keep voice triggers active even when
                typing is paused.

        Returns one of: ``"silent"``, ``"above_silence"``, ``"transcribed"``,
        ``"trigger_only"``, ``"command"``, ``"error"``. The first two are tracked by the
        engine loop; the last three are surfaced to the user via the
        typed log message.
        """

        rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        if rms < self._config.silence_rms_threshold:
            _LOGGER.debug(
                "STT skipping silent chunk (rms=%.6f < threshold=%.4f, samples=%d)",
                rms,
                self._config.silence_rms_threshold,
                chunk.size,
            )
            return "silent"
        _LOGGER.debug(
            "STT chunk above silence: rms=%.4f samples=%d",
            rms,
            chunk.size,
        )
        if not self._transcriber.is_loaded():
            _LOGGER.info(
                "STT loading Whisper model '%s' (first use may take a while)…",
                self._config.model,
            )
        t0 = time.time()
        self._transcribing = True
        try:
            text = self._transcriber.transcribe(chunk, language=self._config.language)
        except Exception as exc:  # pragma: no cover - model path
            self._last_error = f"transcribe: {exc}"
            _LOGGER.exception("Whisper transcription failed: %s", exc)
            self._emit_status(f"error:transcribe:{exc}")
            return "error"
        finally:
            self._transcribing = False
        dt = time.time() - t0
        if not text:
            _LOGGER.info(
                "STT transcription returned empty string after %.2fs (rms=%.4f, model=%s, lang=%s)",
                dt,
                rms,
                self._config.model,
                self._config.language,
            )
            return "above_silence"
        _LOGGER.info(
            "STT transcribed in %.2fs: %r (rms=%.4f, model=%s)",
            dt,
            text,
            rms,
            self._config.model,
        )
        if self._command_handler is not None:
            try:
                consumed = self._command_handler(text, type_into_window)
            except Exception:  # noqa: BLE001 - a command bug must not stop STT
                _LOGGER.exception("Voice command handler raised for %r", text)
                consumed = False
            if consumed:
                # A command: neither typed nor scanned for voice triggers.
                return "command"
        typed = ""
        if type_into_window:
            try:
                typed = self._typer.type_text(
                    text, append_space=self._config.append_space
                )
            except Exception as exc:
                self._last_error = f"typer: {exc}"
                _LOGGER.exception("STT typer failed: %s", exc)
                self._emit_status(f"error:typer:{exc}")
                return "error"
            with self._lock:
                self._typed_total_chars += len(typed)
            _LOGGER.info(
                "STT typed=%d chars (rms=%.3f, lang=%s) :: %s",
                len(typed),
                rms,
                self._config.language,
                text,
            )
        else:
            _LOGGER.info(
                "STT trigger-only mode (typing paused): %r (rms=%.4f)",
                text,
                rms,
            )
        # Always emit the phrase event so the trigger matcher (or any
        # other observer) can react, even when typing is skipped.
        stt_event = STTEvent(
            text=typed,
            raw_text=text,
            rms=rms,
            language=self._config.language,
        )
        if self._on_phrase is not None:
            try:
                self._on_phrase(stt_event)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT on_phrase callback raised")
        # Fire the dynamic phrase-observers list (used by the
        # fact-checker engine to reuse the STT transcription stream
        # without re-running Whisper on overlapping audio).
        with self._lock:
            phrase_observers = list(self._phrase_observers)
        for cb in phrase_observers:
            try:
                cb(stt_event)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT phrase observer raised")
        return "transcribed" if type_into_window else "trigger_only"

    def _emit_status(self, status: str) -> None:
        if self._on_status is None:
            pass
        else:
            try:
                self._on_status(status)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT on_status callback raised")
        self._notify_observers()

    def add_observer(self, callback: Callable[[], None]) -> None:
        """Register a callback fired on every state transition.

        Use to update UI (tray icon, status indicators) without polling.
        """

        with self._lock:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[[], None]) -> None:
        with self._lock:
            try:
                self._observers.remove(callback)
            except ValueError:
                pass

    def add_phrase_observer(self, callback: Callable[["STTEvent"], None]) -> None:
        """Register a callback fired on every successfully
        transcribed STT chunk.

        Phrase observers receive an :class:`STTEvent` and run on
        the STT engine's background thread. They are independent of
        the ``on_phrase`` constructor callback (which still fires
        in parallel), so a listener like the fact-checker engine
        can subscribe and unsubscribe at runtime.
        """

        with self._lock:
            self._phrase_observers.append(callback)

    def remove_phrase_observer(self, callback: Callable[["STTEvent"], None]) -> None:
        with self._lock:
            try:
                self._phrase_observers.remove(callback)
            except ValueError:
                pass

    def _notify_observers(self) -> None:
        with self._lock:
            observers = list(self._observers)
        for cb in observers:
            try:
                cb()
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT observer raised")
