"""Whisper transcription wrapper.

Lazy-loads the model on first use, then exposes a synchronous ``transcribe``
method. Tests can inject a fake ``model_loader`` callable.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)


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
