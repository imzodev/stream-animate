"""Fact-checker / concept-explainer engine.

The engine subscribes to the existing :class:`stream_companion.stt.STTEngine`
phrase stream rather than opening its own microphone and running
its own Whisper transcription pass. This avoids:

* a second ``sounddevice.InputStream`` holding the same device,
* a second set of Whisper calls on overlapping audio,
* the well-known accuracy drop of Whisper on very short clips.

When the user presses the fact-checker hotkey, the engine starts
buffering phrases emitted by STT. The question ends when the user
presses the hotkey again or when no new phrase has arrived for
``_SILENT_PHRASE_TIMEOUT`` seconds (1.5 s by default — generous enough
to span the longest natural pause within a question).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..llm.client import FactCheckerClient, LLMError
from ..llm.config import LLMConfig
from ..stt.engine import STTEvent, STTEngine

_LOGGER = logging.getLogger(__name__)


# How long to wait without a new STT phrase before assuming the user
# has stopped talking. Tuned for natural speech: longer than typical
# intra-sentence pauses, shorter than the gap between sentences.
_SILENT_PHRASE_TIMEOUT = 1.5

# Hard safety cap on how long a single question can run.
_MAX_QUESTION_SECONDS = 30.0

# Hard cap on how many phrases we'll buffer before flushing. A
# pathological STT loop cannot starve the LLM call forever.
_MAX_BUFFERED_PHRASES = 64


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
        kind: For ``"streaming"`` events, ``"answer"`` (default) or
            ``"reasoning"`` (chain-of-thought tokens from a thinking
            model). Lets the GUI render the two streams differently.
    """

    phase: str
    text: str = ""
    delta: str = ""
    kind: str = "answer"


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
    """Orchestrator: STT phrase stream → LLM client."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        stt_engine: Optional[STTEngine] = None,
        client: Optional[FactCheckerClient] = None,
        language: str = "auto",
        silence_timeout: Optional[float] = None,
    ) -> None:
        self._config = config
        self._language = language or "auto"
        # Resolve the silence timeout from (in order):
        #   1. explicit constructor arg (tests)
        #   2. config.silence_timeout (user-tunable via .json)
        #   3. module-level default (production default)
        # We read the module constant at runtime (not at function
        # definition time) so tests can monkeypatch it.
        if silence_timeout is not None:
            self._silence_timeout = silence_timeout
        elif config.silence_timeout > 0:
            self._silence_timeout = config.silence_timeout
        else:
            self._silence_timeout = _SILENT_PHRASE_TIMEOUT
        self._owns_client = client is None
        self._client = client or FactCheckerClient(config)
        # STT engine is optional. When present, the fact-checker
        # subscribes to its phrase stream. When absent (e.g. the user
        # configured the LLM but not the STT engine), the
        # fact-checker degrades gracefully: ``toggle()`` becomes a
        # no-op and ``is_running`` stays False.
        self._stt_engine: Optional[STTEngine] = stt_engine

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Separate cancellation flag for the in-flight LLM stream.
        # The toggle hotkey never sets this — pressing the toggle
        # again while the LLM is streaming is ignored (the stream
        # runs to completion). To abort an in-flight stream the user
        # presses the dedicated cancel hotkey (ESC by default), which
        # routes through ``cancel()`` below.
        self._cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._phase = "idle"
        self._listening = False
        self._last_question = ""
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        # Buffer of STT phrases accumulated since the toggle. The
        # engine concatenates them with single spaces when sending
        # to the LLM.
        self._phrase_buffer: List[str] = []
        # Wall-clock time when the last STT phrase arrived. Used by
        # the silence detector to decide when to stop listening.
        self._last_phrase_at: float = 0.0
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

    @property
    def language(self) -> str:
        return self._language

    @property
    def using_stt_stream(self) -> bool:
        """True when the engine is reusing an existing STT engine's
        phrase stream (the normal path). False means the engine was
        constructed without an STT engine; ``toggle`` is a no-op.
        """
        return self._stt_engine is not None

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
        if self._stt_engine is None:
            _LOGGER.warning(
                "Fact-checker toggle ignored: no STT engine wired. "
                "Configure the Speech-to-Text tab to enable the "
                "fact-checker."
            )
            return
        with self._lock:
            if self._listening:
                # User pressed again — stop listening; the worker
                # thread will finalize the question and stream the
                # answer.
                _LOGGER.info("Fact-checker toggle: stop listening")
                self._stop_event.set()
                return
            if self.is_running:
                # Already processing (thinking/streaming). The toggle
                # hotkey is reserved for "start" and "send" — it does
                # NOT abort the in-flight LLM stream (that would be
                # easy to do by accident). To cancel mid-stream the
                # user presses the dedicated cancel hotkey (ESC).
                _LOGGER.info(
                    "Fact-checker toggle: ignored (already processing, "
                    "phase=%s — press the cancel hotkey to abort)",
                    self._phase,
                )
                return
            self._listening = True
            self._stop_event.clear()
            # Reset the cancel flag too so a previous run's ESC
            # press doesn't bleed into this run.
            self._cancel_event.clear()
            self._last_error = None
            self._last_question = ""
            self._started_at = time.time()
            self._phrase_buffer = []
            # 0.0 means "no phrase has arrived yet" — the silence
            # detector in _run() ignores the silence timeout until at
            # least one phrase is buffered, so a slow STT engine (which
            # can take several seconds to produce its first phrase) does
            # NOT cause us to give up with an empty question.
            self._last_phrase_at = 0.0
            self._thread = threading.Thread(
                target=self._run,
                name="fact-checker",
                daemon=True,
            )
            self._thread.start()
            self._phase = "listening"
            # Subscribe to STT phrases. We register the observer only
            # while listening so we don't accumulate callbacks when
            # idle.
            self._stt_engine.add_phrase_observer(self._on_stt_phrase_for_fact_check)
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(FactCheckerEvent(phase="listening"))
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("Fact-checker observer raised")
        _LOGGER.info(
            "Fact-checker toggle: start (model=%s, persona=%s, "
            "subscribed to STT phrase stream)",
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

    def cancel(self) -> None:
        """Abort an in-flight LLM stream.

        Bound to the dedicated cancel hotkey (ESC by default). The
        toggle hotkey does NOT call this — pressing the toggle
        again during streaming is a no-op so the user does not
        accidentally abort the answer they asked for. To explicitly
        cancel, they press the cancel hotkey.

        Idempotent: calling this multiple times is harmless.
        """
        self._cancel_event.set()
        _LOGGER.info("Fact-checker: cancel requested via hotkey")

    def close(self) -> None:
        """Stop any running thread and close owned resources."""
        self._stop_event.set()
        self._cancel_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        if self._owns_client:
            self._client.close()

    # ------------------------------------------------------------------
    # STT phrase subscription
    # ------------------------------------------------------------------

    def _on_stt_phrase_for_fact_check(self, event: STTEvent) -> None:
        """Called by the STT engine for every successfully transcribed chunk.

        We append non-empty phrases to the buffer and note the
        wall-clock time so the silence detector in ``_run`` knows
        when to stop listening.

        Note: ``event.text`` is the *typed* text (what got injected
        into the focused window) and is empty when the STT engine
        is in trigger-only mode (typing paused). We use
        ``event.raw_text`` — the actual Whisper output — because
        that is what the user said, regardless of whether the
        typer is active.
        """
        with self._lock:
            if not self._listening:
                return
            text = (event.raw_text or "").strip()
            if not text:
                return
            self._phrase_buffer.append(text)
            # Cap the buffer so a pathological STT loop can't starve
            # the LLM call indefinitely.
            if len(self._phrase_buffer) > _MAX_BUFFERED_PHRASES:
                self._phrase_buffer = self._phrase_buffer[-_MAX_BUFFERED_PHRASES:]
            self._last_phrase_at = time.time()

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        # Wait for one of:
        # (a) the user presses the toggle again (set _stop_event), or
        # (b) the user has spoken and then been silent for
        #     self._silence_timeout seconds, or
        # (c) the hard cap _MAX_QUESTION_SECONDS has elapsed.
        #
        # Note: we do NOT time out if no phrase has arrived yet. The
        # STT engine is chunked and can take several seconds to
        # produce its first phrase; we wait for it (up to the hard
        # cap). The user can always re-press the toggle to cancel.
        deadline = time.time() + _MAX_QUESTION_SECONDS
        try:
            while True:
                # Stop immediately when the user re-presses.
                if self._stop_event.is_set():
                    break
                with self._lock:
                    last_at = self._last_phrase_at
                # Silence timeout only fires AFTER the first phrase.
                # last_at == 0.0 means "no phrase buffered yet — keep
                # waiting".
                if last_at > 0 and time.time() - last_at >= self._silence_timeout:
                    break
                if time.time() >= deadline:
                    _LOGGER.info(
                        "Fact-checker: max question time reached, " "finalising"
                    )
                    break
                # Cooperative short sleep so we don't burn CPU.
                time.sleep(0.05)
        finally:
            # Unsubscribe from STT phrases BEFORE we emit the
            # "thinking" event so observers polling is_listening see
            # the transition cleanly.
            if self._stt_engine is not None:
                try:
                    self._stt_engine.remove_phrase_observer(
                        self._on_stt_phrase_for_fact_check
                    )
                except Exception:  # pragma: no cover - defensive
                    pass
            with self._lock:
                self._listening = False
                question_parts = list(self._phrase_buffer)
                self._phrase_buffer = []

        question = " ".join(question_parts).strip()
        _LOGGER.info(
            "Fact-checker buffered STT phrases (%d): %r",
            len(question_parts),
            question_parts,
        )
        if not question:
            _LOGGER.info("Fact-checker: empty question, returning to idle")
            self._set_phase("idle")
            self._emit(FactCheckerEvent(phase="idle"))
            with self._lock:
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
            # _stream_answer already called _fail (so the error event
            # is delivered to the panel and the phase is "error"). Log
            # the full exception here for diagnostics, then return.
            _LOGGER.error("LLM call failed: %s", exc)
            with self._lock:
                self._thread = None
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._fail(f"unexpected: {exc}")
            return
        self._set_phase("done")
        self._emit(FactCheckerEvent(phase="done", text=question))
        with self._lock:
            self._thread = None
        _LOGGER.info("Fact-checker done")

    def _stream_answer(self, question: str) -> None:
        """Stream the LLM answer, emitting one event per token chunk.

        Reasoning tokens (from thinking models like DeepSeek Reasoner)
        are emitted as ``kind="reasoning"`` events; final-answer
        tokens are emitted as ``kind="answer"``. Both kinds carry the
        same delta-accumulation semantics — the panel can render
        them in different colors or styles.

        On LLM error, the user-friendly message is surfaced via
        ``_fail`` (which sets phase to "error" and emits the error
        event) and the exception is re-raised so the caller in
        ``_run`` can short-circuit and skip the "done" phase. The full
        error is logged at ERROR level by the caller.
        """
        self._set_phase("streaming")
        streamed_answer: List[str] = []
        streamed_reasoning: List[str] = []
        try:
            for stream_chunk in self._client.stream(question):
                # The cancel hotkey (ESC) sets _cancel_event. The
                # toggle hotkey never touches this flag — pressing
                # it again during streaming is a no-op so the user
                # does not accidentally abort the answer they
                # asked for.
                if self._cancel_event.is_set():
                    _LOGGER.info("Fact-checker: user cancelled mid-stream")
                    break
                if stream_chunk.reasoning:
                    streamed_reasoning.append(stream_chunk.reasoning)
                    _LOGGER.info(
                        "Fact-checker reasoning chunk: %r (total=%d chars)",
                        stream_chunk.reasoning,
                        len("".join(streamed_reasoning)),
                    )
                    self._emit(
                        FactCheckerEvent(
                            phase="streaming",
                            text="".join(streamed_reasoning),
                            delta=stream_chunk.reasoning,
                            kind="reasoning",
                        )
                    )
                if stream_chunk.content:
                    streamed_answer.append(stream_chunk.content)
                    _LOGGER.info(
                        "Fact-checker answer chunk: %r (total=%d chars)",
                        stream_chunk.content,
                        len("".join(streamed_answer)),
                    )
                    self._emit(
                        FactCheckerEvent(
                            phase="streaming",
                            text="".join(streamed_answer),
                            delta=stream_chunk.content,
                            kind="answer",
                        )
                    )
        except LLMError as exc:
            self._fail(self._summarize_llm_error(exc))
            raise

        # Log the full cumulative streams for debugging — useful when
        # the panel shows unexpected content and we need to see what
        # actually came back from the model.
        _LOGGER.info(
            "Fact-checker final answer (%d chars): %r",
            len("".join(streamed_answer)),
            "".join(streamed_answer),
        )
        _LOGGER.info(
            "Fact-checker final reasoning (%d chars): %r",
            len("".join(streamed_reasoning)),
            "".join(streamed_reasoning),
        )

    def _summarize_llm_error(self, exc: "LLMError") -> str:
        """Return a short, user-facing error string for the panel.

        Keeps the panel readable when the server returns an HTML 404
        page or a multi-line JSON dump. The full body is still
        available in the application log.
        """
        status = exc.status
        body = exc.body or ""
        # Some providers (notably opencode's proxy) return 401 with a
        # JSON ``ModelError`` body when the key is fine but the model
        # name is unknown. Detect that before falling back to the
        # generic auth message.
        if "ModelError" in body or "not supported" in body.lower():
            return (
                f"Model {self._config.model!r} is not available on the "
                f"configured base URL. Check the provider's model "
                f"list or pick a different model."
            )
        if status == 401 or status == 403:
            return (
                f"Auth failed ({status}). Check that the API key in env "
                f"var '{self._config.api_key_env}' is valid for the "
                f"configured base URL."
            )
        if status == 404:
            return (
                f"Endpoint not found (404). Check that the model name "
                f"{self._config.model!r} is valid for the configured "
                f"base URL ({self._config.base_url})."
            )
        if status == 429:
            return "Rate limited (429). Try again in a moment."
        if status is not None and status >= 500:
            return f"LLM service error ({status}). Try again."
        if status is None:
            return f"Network error: {body or 'unreachable'}"
        return f"LLM error ({status}): {body[:120]}"

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
