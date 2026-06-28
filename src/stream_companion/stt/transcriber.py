"""Whisper transcription wrapper.

Lazy-loads the model on first use, then exposes a synchronous ``transcribe``
method. Tests can inject a fake ``model_loader`` callable.

Backends
--------
By default this prefers `faster-whisper <https://github.com/SYSTRAN/faster-whisper>`_
(CTranslate2), which is several times faster than ``openai-whisper`` on both
CPU (int8) and GPU (float16) and ships a built-in voice-activity-detection
(VAD) filter that trims silence before inference. If ``faster-whisper`` is not
installed — or fails to initialize (e.g. missing CUDA/cuDNN runtime) — the
loader transparently falls back to ``openai-whisper``.

The active backend is detected from the loaded model object's module, so an
injected ``model_loader`` (used by tests) keeps the original ``openai-whisper``
dict-shaped contract without any special-casing.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable, Optional

import numpy as np

_LOGGER = logging.getLogger(__name__)

_FASTER = "faster"
_OPENAI = "openai"


def _ensure_cuda_dll_path() -> None:
    """Make the pip-installed NVIDIA CUDA DLLs loadable on Windows.

    The ``nvidia-*-cu12`` wheels drop ``cublas64_12.dll`` / ``cudnn64_9.dll``
    under ``site-packages/nvidia/*/bin``. Windows does **not** search those
    directories by default, so CTranslate2 fails with "Library cublas64_12.dll
    is not found" even though the file is installed. ``os.add_dll_directory``
    registers them with the loader. No-op on non-Windows (Linux/macOS resolve
    these via RPATH / ``LD_LIBRARY_PATH``) and harmless if the dirs are absent.
    """

    if sys.platform != "win32":
        return
    try:
        import importlib.util

        spec = importlib.util.find_spec("nvidia")
        roots = list(spec.submodule_search_locations) if spec is not None else []
    except Exception as exc:  # noqa: BLE001 - best effort
        _LOGGER.debug("Could not locate the 'nvidia' package for CUDA DLLs: %s", exc)
        return

    import glob

    for nvidia_root in roots:
        for bin_dir in glob.glob(os.path.join(nvidia_root, "*", "bin")):
            if not os.path.isdir(bin_dir):
                continue
            # add_dll_directory helps DLLs that Python itself loads, but
            # CTranslate2 loads cublas/cudnn by *name* from its own C++ code,
            # which uses the standard Windows search order — that consults
            # PATH, not the add_dll_directory list. Do both so the libraries
            # are found regardless of which loader path is taken.
            try:
                os.add_dll_directory(bin_dir)
            except OSError as exc:
                _LOGGER.debug("Could not add DLL directory %s: %s", bin_dir, exc)
            if bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            _LOGGER.debug("Registered CUDA DLL directory: %s", bin_dir)


class WhisperTranscriber:
    """Lazy-loading wrapper around a Whisper backend.

    The public surface is backend-agnostic: ``transcribe(audio, language)``
    returns a plain string regardless of whether faster-whisper or
    openai-whisper is in use.
    """

    def __init__(
        self,
        model_name: str = "turbo",
        *,
        model_loader: Optional[Callable[[str], object]] = None,
        device: str = "auto",
        compute_type: str = "auto",
        vad_filter: bool = True,
    ) -> None:
        self._model_name = model_name
        self._model_loader = model_loader
        self._device = device
        self._compute_type = compute_type
        self._vad_filter = vad_filter
        self._model = None
        self._backend: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def backend(self) -> Optional[str]:
        """The active backend tag (``"faster"`` / ``"openai"``) once loaded."""

        return self._backend

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
            self._backend = self._detect_backend(self._model)
            _LOGGER.info(
                "Whisper model '%s' loaded (backend=%s)",
                self._model_name,
                self._backend,
            )

    def _default_loader(self, name: str) -> object:
        """Load faster-whisper when available, else fall back to openai-whisper."""

        try:
            _ensure_cuda_dll_path()
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            device, compute_type = self._resolve_device_compute()
            _LOGGER.info(
                "Initializing faster-whisper model %r (device=%s, compute_type=%s)",
                name,
                device,
                compute_type,
            )
            return WhisperModel(name, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001 - fall back on any init failure
            _LOGGER.warning(
                "faster-whisper unavailable (%s); falling back to openai-whisper. "
                "Install faster-whisper (and CUDA/cuDNN for GPU) for lower latency.",
                exc,
            )
            import whisper  # type: ignore[import-not-found]

            return whisper.load_model(name)

    def _resolve_device_compute(self) -> tuple[str, str]:
        """Pick a CTranslate2 device + compute_type.

        ``device="auto"`` selects CUDA when CTranslate2 reports a GPU, else
        CPU. ``compute_type="auto"`` then maps to ``float16`` on GPU and
        ``int8`` on CPU — the fast defaults for each. Explicit values passed
        to the constructor are honored verbatim.
        """

        device = self._device
        if device == "auto":
            device = "cpu"
            try:
                import ctranslate2  # type: ignore[import-not-found]

                if ctranslate2.get_cuda_device_count() > 0:
                    device = "cuda"
            except Exception as exc:  # noqa: BLE001 - default to CPU on probe failure
                _LOGGER.debug("CUDA probe failed (%s); using CPU", exc)

        compute_type = self._compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        return device, compute_type

    @staticmethod
    def _detect_backend(model: object) -> str:
        """Classify the loaded model as the faster-whisper or openai backend."""

        module = (type(model).__module__ or "").lower()
        if module.startswith("faster_whisper"):
            return _FASTER
        return _OPENAI

    def transcribe(
        self,
        audio: np.ndarray,
        language: str = "auto",
    ) -> str:
        """Transcribe a single audio chunk. Loads the model lazily."""

        self.load()
        if self._backend == _FASTER:
            return self._transcribe_faster(audio, language)
        return self._transcribe_openai(audio, language)

    def _transcribe_faster(self, audio: np.ndarray, language: str) -> str:
        """faster-whisper path: iterate the segment generator into text."""

        kwargs: dict = {"vad_filter": self._vad_filter}
        if language and language != "auto":
            kwargs["language"] = language
        with self._lock:
            try:
                segments, _info = self._model.transcribe(audio, **kwargs)  # type: ignore[union-attr]
            except TypeError:
                # Older faster-whisper without vad_filter support — retry plain.
                kwargs.pop("vad_filter", None)
                segments, _info = self._model.transcribe(audio, **kwargs)  # type: ignore[union-attr]
            # The segment generator is lazy; materialize it inside the lock
            # so the (single, shared) model isn't re-entered concurrently.
            text = "".join(segment.text for segment in segments)
        return text.strip()

    def _transcribe_openai(self, audio: np.ndarray, language: str) -> str:
        """openai-whisper path: a single dict/object result."""

        kwargs: dict = {}
        if language and language != "auto":
            kwargs["language"] = language
        with self._lock:
            result = self._model.transcribe(audio, **kwargs)  # type: ignore[union-attr]
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        # Some implementations return an object exposing ``.text``.
        return str(getattr(result, "text", "")).strip()

    def is_loaded(self) -> bool:
        return self._model is not None
