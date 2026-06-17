"""Fact-checker / concept-explainer engine.

Mirrors :class:`stream_companion.stt.STTEngine`'s threading and observer
patterns. The engine:

1. Listens to the microphone (its own ``AudioCapture`` instance — does
   not share the STT engine's mic handle).
2. Transcribes each non-silent chunk with its own
   :class:`WhisperTranscriber` and concatenates the partial transcripts
   into a running "what the user is saying" buffer.
3. When 1.5 seconds of silence follow a non-silent chunk, treats the
   accumulated text as a complete question and stops listening.
4. Sends the question to an OpenAI-compatible chat-completions endpoint
   via :class:`FactCheckerClient` and emits a streaming event for each
   token delta so the UI can render a typewriter-style answer.
5. Emits phase transitions (``listening`` / ``thinking`` / ``streaming``
   / ``done`` / ``error``) to all registered observers.

Cancellation: calling :meth:`toggle` while the engine is listening or
streaming will set a stop flag; the listening loop checks the flag
between chunks, the streaming loop checks it between tokens. The
caller is responsible for closing the client (the engine does not own
the default httpx transport when one is injected).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np

from ..llm.client import FactCheckerClient, LLMError
from ..llm.config import LLMConfig
from ..stt.audio import AudioCapture, AudioCaptureError
from ..stt.transcriber import WhisperTranscriber

_LOGGER = logging.getLogger(__name__)


# Number of consecutive sub-threshold chunks that end a question.
# With 0.5s chunks, 3 chunks = 1.5s of silence.
_SILENCE_CHUNKS_TO_END = 3
# RMS below this is "silent" for the fact-checker (same scale as STT).
_SILENCE_RMS = 0.005


@dataclass
class FactCheckerEvent:
    """Observer event emitted by :class:`FactCheckerEngine`.

    Attributes:
        phase: One of ``"listening"``, ``"thinking"``, ``"streaming"``,
            ``"done"``, ``"error"``, ``"idle"``.
        text: For ``"thinking"`` and ``"done"`` this is the user's
            question. For ``"streaming"`` it is the full text streamed
            so far. For ``"error"`` it is a short human-readable
            message. Empty for ``"listening"`` and ``"idle"``.
        delta: For ``"streaming"`` this is the latest token delta.
            Empty for all other phases.
    """

    phase: str
    text: str = ""
    delta: str = ""


@dataclass
class FactCheckerStatus:
    """JSON-serializable snapshot of the engine state."""

    running: bool
    listening: bool
    phase: str
    model: str
    persona: str
    last_question: str = ""
    last_error: Optional[str] = None
    started_at: Optional[float] = field(default=None)


class FactCheckerEngine:
    """Top-level orchestrator: mic → whisper → LLM client."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        audio_capture: Optional[AudioCapture] = None,
        transcriber: Optional[WhisperTranscriber] = None,
        client: Optional[FactCheckerClient] = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or FactCheckerClient(config)

        # Audio: 0.5s chunks, 16kHz mono float32 (matches STT defaults
        # except chunk size — shorter chunks = faster end-of-speech
        # detection).
        self._audio = audio_capture or AudioCapture(
            sample_rate=16000,
            chunk_seconds=0.5,
            device=None,
        )
        self._transcriber = transcriber or WhisperTranscriber(model_name="tiny")
        # The transcribe lock is per-instance; we keep ours lightweight
        # (we use the small "tiny" model by default; the user can
        # override via a custom transcriber).

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._phase = "idle"
        self._listening = False
        self._last_question = ""
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._observers: List[Callable[[FactCheckerEvent], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_listening(self) -> bool:
        return self._listening

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def status(self) -> dict:
        with self._lock:
            phase = self._phase
            listening = self._listening
            last_q = self._last_question
        return {
            "running": self.is_running,
            "listening": listening,
            "phase": phase,
            "model": self._config.model,
            "persona": self._config.persona,
            "last_question": last_q,
            "last_error": self._last_error,
            "started_at": self._started_at,
        }

    def toggle(self) -> None:
        """Press-to-toggle: start listening, or stop and send."""
        with self._lock:
            if self._listening:
                # User pressed again — stop listening, the loop will
                # finalize the question and stream the answer.
                _LOGGER.info("Fact-checker toggle: stop listening")
                self._stop_event.set()
                return
            if self.is_running:
                # Already processing (thinking/streaming) — ignore.
                _LOGGER.info(
                    "Fact-checker toggle: ignored (already processing, phase=%s)",
                    self._phase,
                )
                return
            self._listening = True
            self._stop_event.clear()
            self._last_error = None
            self._last_question = ""
            self._started_at = time.time()
            self._thread = threading.Thread(
                target=self._run,
                name="fact-checker",
                daemon=True,
            )
            self._thread.start()
            # Emit the "listening" event under the same lock so the
            # observer list cannot be mutated between
            # ``start()`` and ``_emit()`` by a concurrent add/remove.
            self._phase = "listening"
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(FactCheckerEvent(phase="listening"))
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("Fact-checker observer raised")
        _LOGGER.info(
            "Fact-checker toggle: start (model=%s, persona=%s)",
            self._config.model,
            self._config.persona,
        )

    def add_observer(self, callback: Callable[[FactCheckerEvent], None]) -> None:
        with self._lock:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[[FactCheckerEvent], None]) -> None:
        with self._lock:
            try:
                self._observers.remove(callback)
            except ValueError:
                pass

    def close(self) -> None:
        """Stop any running thread and close owned resources."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._audio.start()
        except AudioCaptureError as exc:
            self._fail(f"microphone: {exc}")
            return
        try:
            accumulated = self._listen_loop()
        finally:
            self._audio.stop()
        if self._stop_event.is_set():
            # User cancelled before the question completed; drop it.
            self._set_phase("idle")
            self._emit(FactCheckerEvent(phase="idle"))
            with self._lock:
                self._listening = False
                self._thread = None
            return
        question = accumulated.strip()
        if not question:
            _LOGGER.info("Fact-checker: empty question, returning to idle")
            self._set_phase("idle")
            self._emit(FactCheckerEvent(phase="idle"))
            with self._lock:
                self._listening = False
                self._thread = None
            return
        with self._lock:
            self._last_question = question
        _LOGGER.info("Fact-checker question: %r", question)
        self._set_phase("thinking")
        self._emit(FactCheckerEvent(phase="thinking", text=question))
        try:
            self._stream_answer(question)
        except LLMError as exc:
            self._fail(f"llm: {exc}")
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(f"unexpected: {exc}")
            return
        self._set_phase("done")
        self._emit(FactCheckerEvent(phase="done", text=question))
        with self._lock:
            self._listening = False
            self._thread = None
        _LOGGER.info("Fact-checker done")

    def _listen_loop(self) -> str:
        """Listen until silence or stop, return the accumulated text."""
        accumulated_parts: List[str] = []
        silent_streak = 0
        chunks_above = 0
        # Safety cap: if we never see silence, end after this many
        # chunks (~30s of audio) so a runaway session can't loop forever.
        max_chunks = 60
        chunks_seen = 0
        try:
            while not self._stop_event.is_set():
                chunk = self._audio.get_chunk(timeout=0.25)
                if chunk is None:
                    continue
                chunks_seen += 1
                rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
                if rms < _SILENCE_RMS:
                    silent_streak += 1
                    if silent_streak >= _SILENCE_CHUNKS_TO_END and chunks_above > 0:
                        # Question complete.
                        break
                    # If we have never heard anything and the user has
                    # been silent for a long time, give up so the
                    # engine can return to idle. 6 chunks of pure
                    # silence is a 3-second "no one is here" signal.
                    if silent_streak >= 6 and chunks_above == 0:
                        break
                    continue
                chunks_above += 1
                silent_streak = 0
                try:
                    text = self._transcriber.transcribe(chunk, language="auto").strip()
                except Exception as exc:  # pragma: no cover - model path
                    _LOGGER.exception("Fact-checker transcribe failed: %s", exc)
                    continue
                if text:
                    accumulated_parts.append(text)
                if chunks_seen >= max_chunks:
                    _LOGGER.info("Fact-checker: max listen chunks reached, finalising")
                    break
        finally:
            pass
        return " ".join(accumulated_parts)

    def _stream_answer(self, question: str) -> None:
        """Stream the LLM answer, emitting one event per token delta."""
        self._set_phase("streaming")
        streamed: List[str] = []
        for token in self._client.stream(question):
            if self._stop_event.is_set():
                _LOGGER.info("Fact-checker: user cancelled mid-stream")
                break
            streamed.append(token)
            self._emit(
                FactCheckerEvent(
                    phase="streaming",
                    text="".join(streamed),
                    delta=token,
                )
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def _fail(self, message: str) -> None:
        _LOGGER.error("Fact-checker error: %s", message)
        self._set_phase("error")
        # Emit the error event BEFORE clearing ``_thread`` so a caller
        # polling ``is_running`` cannot observe the thread as gone
        # before the event has been delivered to observers.
        self._emit(FactCheckerEvent(phase="error", text=message))
        with self._lock:
            self._last_error = message
            self._listening = False
            self._thread = None

    def _emit(self, event: FactCheckerEvent) -> None:
        with self._lock:
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(event)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("Fact-checker observer raised")
