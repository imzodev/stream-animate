"""Audio capture for the STT engine.

Wraps ``sounddevice.InputStream`` into a thread-safe producer of fixed-size
mono float32 chunks. The module is dependency-injection friendly: tests can
pass a fake ``sounddevice`` module via ``AudioCapture(sounddevice_module=...)``.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import List, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)


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
