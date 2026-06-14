"""Speech-to-text typing engine for the Streaming Companion Tool.

This module wires together three independent components:

* :class:`AudioCapture` — a small `sounddevice` stream that buffers mono
  16 kHz float32 audio from the system microphone (or a specific device)
  and yields fixed-size chunks on a background thread.
* :class:`WhisperTranscriber` — a thin wrapper around ``whisper`` that
  loads a model lazily and exposes a synchronous ``transcribe`` method
  returning plain text.
* :class:`TextTyper` — types text into whichever window is focused using
  ``pynput.keyboard.Controller``. A small rolling window of the recently
  typed text is kept so that overlapping chunks don't produce duplicate
  characters.

The :class:`STTEngine` is the orchestrator. It accepts a callback that is
fired when a new phrase is finalized, runs the capture/transcribe loop in a
background thread, and can be started/stopped from any thread.

The module is intentionally dependency-injection friendly so the audio
backend, the whisper model, and the keyboard controller can all be
swapped in tests.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np

from .models import STTConfig

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audio capture
# ---------------------------------------------------------------------------


class AudioCaptureError(RuntimeError):
    """Raised when microphone capture cannot start."""


class AudioCapture:
    """Capture mono float32 audio from a sounddevice input device.

    Audio is buffered into fixed-size chunks and exposed via a thread-safe
    ``get_chunk(timeout)`` call. ``start`` and ``stop`` are idempotent and
    safe to call from any thread.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_seconds: float = 4.0,
        device: Optional[int] = None,
        *,
        sounddevice_module=None,
    ) -> None:
        if chunk_seconds <= 0:
            raise ValueError("chunk_seconds must be positive")
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self._sample_rate = int(sample_rate)
        self._chunk_seconds = float(chunk_seconds)
        self._frames_per_chunk = max(
            1, int(round(self._sample_rate * self._chunk_seconds))
        )
        self._device = device

        self._sd = sounddevice_module
        if self._sd is None:
            import sounddevice as sd  # type: ignore[import-not-found]

            self._sd = sd

        self._stream = None
        self._buffer: List[np.ndarray] = []
        self._lock = threading.Lock()
        self._chunks: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._overflow_warned = False
        self._running = False
        self._error: Optional[Exception] = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def chunk_seconds(self) -> float:
        return self._chunk_seconds

    @property
    def frames_per_chunk(self) -> int:
        return self._frames_per_chunk

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        try:
            self._stream = self._sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                device=self._device,
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._error = exc
            raise AudioCaptureError(
                f"Failed to open microphone input device {self._device!r}: {exc}"
            ) from exc
        self._running = True
        _LOGGER.info(
            "Audio capture started: device=%s rate=%d chunk=%.2fs",
            self._device,
            self._sample_rate,
            self._chunk_seconds,
        )

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover - defensive cleanup
                _LOGGER.exception("Error closing audio stream")
            self._stream = None
        with self._lock:
            self._buffer.clear()
        # Drop any queued chunks
        while not self._chunks.empty():
            try:
                self._chunks.get_nowait()
            except queue.Empty:  # pragma: no cover
                break
        _LOGGER.info("Audio capture stopped")

    def get_chunk(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        """Return the next buffered chunk or ``None`` on timeout."""

        try:
            return self._chunks.get(timeout=timeout)
        except queue.Empty:
            return None

    def last_error(self) -> Optional[Exception]:
        return self._error

    def _on_audio(
        self, indata, frames, time_info, status
    ) -> None:  # pragma: no cover - hardware path
        if status:
            _LOGGER.debug("sounddevice status: %s", status)
        if not self._running:
            return
        chunk = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
        with self._lock:
            self._buffer.append(chunk)
            total = sum(c.shape[0] for c in self._buffer)
            if total >= self._frames_per_chunk:
                merged = np.concatenate(self._buffer)
                self._buffer.clear()
                pieces = [
                    merged[i : i + self._frames_per_chunk]
                    for i in range(0, merged.shape[0], self._frames_per_chunk)
                ]
                # Keep any leftover for the next callback
                if pieces:
                    last = pieces[-1]
                    if last.shape[0] < self._frames_per_chunk:
                        self._buffer.append(last)
                        pieces = pieces[:-1]
                for piece in pieces:
                    self._enqueue(piece)

    def _enqueue(self, chunk: np.ndarray) -> None:
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:
            if not self._overflow_warned:
                _LOGGER.warning("Audio chunk queue is full; dropping chunks")
                self._overflow_warned = True


# ---------------------------------------------------------------------------
# Whisper transcription
# ---------------------------------------------------------------------------


class WhisperTranscriber:
    """Lazy-loading wrapper around the ``whisper`` Python package."""

    def __init__(
        self,
        model_name: str = "turbo",
        *,
        model_loader: Optional[Callable[[str], object]] = None,
    ) -> None:
        self._model_name = model_name
        self._model_loader = model_loader
        self._model = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def load(self) -> None:
        """Load the model if it isn't already in memory."""

        with self._lock:
            if self._model is not None:
                return
            loader = self._model_loader or self._default_loader
            _LOGGER.info(
                "Loading Whisper model '%s' (first use may download weights)…",
                self._model_name,
            )
            self._model = loader(self._model_name)
            _LOGGER.info("Whisper model '%s' loaded", self._model_name)

    @staticmethod
    def _default_loader(name: str) -> object:
        import whisper  # type: ignore[import-not-found]

        return whisper.load_model(name)

    def transcribe(
        self,
        audio: np.ndarray,
        language: str = "auto",
    ) -> str:
        """Transcribe a single audio chunk. Loads the model lazily."""

        self.load()
        kwargs = {}
        if language and language != "auto":
            kwargs["language"] = language
        with self._lock:
            result = self._model.transcribe(audio, **kwargs)  # type: ignore[union-attr]
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        # Some model implementations return objects with a .text attribute
        return str(getattr(result, "text", "")).strip()

    def is_loaded(self) -> bool:
        return self._model is not None


# ---------------------------------------------------------------------------
# Text typing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Engine orchestrator
# ---------------------------------------------------------------------------


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
    ) -> None:
        self._config = config
        self._stt_hotkey = hotkey
        self._audio = audio_capture or AudioCapture(
            sample_rate=config.sample_rate,
            chunk_seconds=config.chunk_seconds,
            device=config.device,
        )
        self._transcriber = transcriber or WhisperTranscriber(model_name=config.model)
        self._typer = typer or TextTyper(window=config.dedup_window)
        self._on_phrase = on_phrase
        self._on_status = on_status

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active = False
        self._typed_total_chars = 0
        self._last_error: Optional[str] = None
        self._started_at: Optional[float] = None
        self._mic_open: bool = False
        self._observers: List[Callable[[], None]] = []

    @property
    def config(self) -> STTConfig:
        return self._config

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_running(self) -> bool:
        """``True`` if the background loop thread is alive (regardless of active state)."""

        return self._thread is not None and self._thread.is_alive()

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
            "STT engine loop running (mic=%s, sample_rate=%d, chunk=%.2fs, silence_threshold=%.4f)",
            (
                self._audio.device
                if hasattr(self._audio, "device")
                else self._config.device
            ),
            self._audio.sample_rate,
            self._audio.chunk_seconds,
            self._config.silence_rms_threshold,
        )
        chunks_received = 0
        chunks_above_silence = 0
        chunks_transcribed = 0
        last_log = time.time()
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    active = self._active
                if not active:
                    # Drop audio so we don't build up a backlog while paused
                    chunk = self._audio.get_chunk(timeout=0.25)
                    if chunk is not None:
                        # discard
                        pass
                    continue
                chunk = self._audio.get_chunk(timeout=0.5)
                if chunk is None:
                    continue
                chunks_received += 1
                result = self._process_chunk(chunk)
                if result == "above_silence":
                    chunks_above_silence += 1
                elif result == "transcribed":
                    chunks_above_silence += 1
                    chunks_transcribed += 1
                # Periodically log activity even if nothing was transcribed,
                # so users can see that the loop is alive and receiving audio.
                if time.time() - last_log > 5.0:
                    _LOGGER.info(
                        "STT heartbeat: %d chunks received, %d above silence, %d transcribed (rms_threshold=%.4f, model_loaded=%s)",
                        chunks_received,
                        chunks_above_silence,
                        chunks_transcribed,
                        self._config.silence_rms_threshold,
                        self._transcriber.is_loaded(),
                    )
                    last_log = time.time()
        finally:
            with self._lock:
                self._mic_open = False
            self._audio.stop()
            _LOGGER.info(
                "STT engine loop stopped (chunks_received=%d, above_silence=%d, transcribed=%d)",
                chunks_received,
                chunks_above_silence,
                chunks_transcribed,
            )

    def _process_chunk(self, chunk: np.ndarray) -> str:
        """Transcribe a single chunk. Returns a status tag for the loop counters.

        Returns one of: ``"silent"``, ``"above_silence"``, ``"transcribed"``,
        ``"error"``. The first two are tracked by the engine loop; the last
        two are surfaced to the user via the typed log message.
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
        try:
            text = self._transcriber.transcribe(chunk, language=self._config.language)
        except Exception as exc:  # pragma: no cover - model path
            self._last_error = f"transcribe: {exc}"
            _LOGGER.exception("Whisper transcription failed: %s", exc)
            self._emit_status(f"error:transcribe:{exc}")
            return "error"
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
        try:
            typed = self._typer.type_text(text, append_space=self._config.append_space)
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
        if self._on_phrase is not None:
            try:
                self._on_phrase(
                    STTEvent(
                        text=typed,
                        raw_text=text,
                        rms=rms,
                        language=self._config.language,
                    )
                )
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT on_phrase callback raised")
        return "transcribed"

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

    def _notify_observers(self) -> None:
        with self._lock:
            observers = list(self._observers)
        for cb in observers:
            try:
                cb()
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("STT observer raised")
