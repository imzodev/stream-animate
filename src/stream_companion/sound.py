"""Sound playback utilities for the Streaming Companion Tool."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import pygame.mixer

Logger = logging.Logger


class SoundPlayer:
    """Load and play short audio clips using ``pygame.mixer``.

    The player preloads requested sound files and keeps them in memory for
    near-zero latency playback. It uses dependency injection friendly
    factories to aid unit testing.
    """

    def __init__(
        self,
        mixer: Optional[pygame.mixer] = None,
        logger: Optional[Logger] = None,
    ) -> None:
        self._mixer = mixer or pygame.mixer
        self._logger = logger or logging.getLogger(__name__)
        self._sounds: Dict[str, pygame.mixer.Sound] = {}
        self._initialized = False

    def initialize(
        self,
        *,
        frequency: int = 44_100,
        size: int = -16,
        channels: int = 2,
        buffer: int = 512,
    ) -> None:
        """Initialize the mixer subsystem if it is not already running."""

        if self._initialized:
            return
        self._mixer.init(
            frequency=frequency,
            size=size,
            channels=channels,
            buffer=buffer,
        )
        self._initialized = True
        self._logger.info(
            "Initialized audio mixer (freq=%s, size=%s, channels=%s, buffer=%s)",
            frequency,
            size,
            channels,
            buffer,
        )

    def shutdown(self) -> None:
        """Stop playback and release all loaded sounds."""

        if not self._initialized:
            return
        self.stop_all()
        self._mixer.quit()
        self._sounds.clear()
        self._initialized = False
        self._logger.info("Audio mixer shut down")

    def load(self, sound_id: str, file_path: str) -> bool:
        """Load an audio file and associate it with ``sound_id``.

        Returns ``True`` when the file was loaded successfully, ``False``
        otherwise. Errors are logged but never raised to the caller.
        """

        if not sound_id:
            raise ValueError("sound_id must be provided")

        path = Path(file_path)
        if not path.is_file():
            self._logger.warning("Sound file missing: %s", path)
            return False

        self._ensure_initialized()

        try:
            sound = self._mixer.Sound(path.as_posix())
        except Exception:  # pragma: no cover - defensive logging
            self._logger.exception("Failed to load sound: %s", path)
            return False

        self._sounds[sound_id] = sound
        self._logger.info("Loaded sound '%s' from %s", sound_id, path)
        return True

    def unload(self, sound_id: str) -> bool:
        """Remove a previously loaded sound from memory."""

        removed = self._sounds.pop(sound_id, None)
        if removed is None:
            self._logger.debug("Attempted to unload missing sound '%s'", sound_id)
            return False
        self._logger.info("Unloaded sound '%s'", sound_id)
        return True

    def play(self, sound_id: str, *, loops: int = 0) -> bool:
        """Play a loaded sound. Returns ``True`` if playback began."""

        sound = self._sounds.get(sound_id)
        if sound is None:
            self._logger.warning("Sound '%s' not loaded", sound_id)
            return False
        try:
            sound.play(loops=loops)
        except Exception:  # pragma: no cover - defensive logging
            self._logger.exception("Failed to play sound '%s'", sound_id)
            return False
        return True

    def stop_all(self) -> None:
        """Stop playback for all channels."""

        if not self._initialized:
            return
        self._mixer.stop()

    def loaded_sounds(self) -> Dict[str, pygame.mixer.Sound]:
        """Return a shallow copy of the loaded sound mapping."""

        return dict(self._sounds)

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self.initialize()
