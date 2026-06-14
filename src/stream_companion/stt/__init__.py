"""Speech-to-text typing engine for the Streaming Companion Tool.

This package wires together three independent components:

* :mod:`.audio` — :class:`AudioCapture`, a small ``sounddevice`` stream that
  buffers mono 16 kHz float32 audio and yields fixed-size chunks on a
  background thread.
* :mod:`.transcriber` — :class:`WhisperTranscriber`, a thin wrapper around
  ``whisper`` that loads a model lazily and exposes a synchronous
  ``transcribe`` method returning plain text.
* :mod:`.typer` — :class:`TextTyper`, which types text into whichever window
  is focused using ``pynput.keyboard.Controller``. A small rolling window
  of the recently typed text is kept so that overlapping chunks don't
  produce duplicate characters.

The :class:`STTEngine` in :mod:`.engine` is the orchestrator. It accepts a
callback that is fired when a new phrase is finalized, runs the
capture/transcribe loop in a background thread, and can be started/stopped
from any thread.

The package is intentionally dependency-injection friendly so the audio
backend, the whisper model, and the keyboard controller can all be swapped
in tests.
"""

from .audio import AudioCapture, AudioCaptureError
from .engine import STTEngine, STTEvent
from .transcriber import WhisperTranscriber
from .typer import TextTyper

__all__ = [
    "AudioCapture",
    "AudioCaptureError",
    "STTEngine",
    "STTEvent",
    "TextTyper",
    "WhisperTranscriber",
]
