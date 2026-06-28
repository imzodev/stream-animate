"""Whisper model downloader for the Streaming Companion Tool.

Pre-downloads the configured Whisper model to the user's local cache so
that the first dictation does not block on a multi-gigabyte download.

Models are CTranslate2 conversions hosted on the Hugging Face Hub and are
fetched via :func:`faster_whisper.download_model`, which caches them under
``~/.cache/huggingface/hub`` (or ``$HF_HOME``). A model "path" is therefore a
*directory* (the snapshot), not a single ``.pt`` file.

Public entry points:

* :func:`available_models` — the list of supported model names
* :func:`is_model_cached` — fast local-only existence check
* :func:`model_path` — local snapshot directory for a cached model
* :func:`download_model` — blocking download, returns the snapshot directory
* :func:`start_background_download` — fire-and-forget background download
* :func:`wait_for_pending_downloads` — join all in-flight downloads
* :func:`active_downloads` — names of models still downloading
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, List, Optional

_LOGGER = logging.getLogger(__name__)

# Fallback list used only when faster-whisper isn't importable (e.g. during
# unit tests that don't have it installed). Mirrors the size names
# faster-whisper accepts; the canonical list comes from the package itself
# via :func:`available_models` when available.
_STATIC_MODELS: List[str] = [
    "tiny.en",
    "tiny",
    "base.en",
    "base",
    "small.en",
    "small",
    "medium.en",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large",
    "large-v3-turbo",
    "turbo",
    "distil-small.en",
    "distil-medium.en",
    "distil-large-v2",
    "distil-large-v3",
]


# ---------------------------------------------------------------------------
# faster-whisper seams (lazy imports; monkeypatchable in tests)
# ---------------------------------------------------------------------------


def _fw_available_models() -> List[str]:
    """Return faster-whisper's supported model names (lazy import)."""

    from faster_whisper import available_models  # type: ignore[import-not-found]

    return list(available_models())


def _fw_download_model(
    model_name: str,
    *,
    cache_dir: Optional[str] = None,
    local_files_only: bool = False,
) -> str:
    """Delegate to ``faster_whisper.download_model`` (lazy import).

    Returns the local snapshot directory. With ``local_files_only=True`` it
    raises if the model isn't already cached (used for cache detection).
    """

    from faster_whisper import download_model  # type: ignore[import-not-found]

    return download_model(
        model_name,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def available_models() -> List[str]:
    """Return the list of supported model names."""

    try:
        names = _fw_available_models()
    except Exception as exc:  # noqa: BLE001 - faster-whisper may be absent
        _LOGGER.debug("faster-whisper not available for model list (%s); using static list", exc)
        return list(_STATIC_MODELS)
    # Guarantee the friendly aliases the rest of the app advertises.
    for alias in ("turbo", "large"):
        if alias not in names:
            names.append(alias)
    return names


def is_model_cached(model_name: str, cache_dir: Optional[str] = None) -> bool:
    """Return ``True`` iff the model snapshot is present in the local cache."""

    if model_name not in available_models():
        return False
    try:
        _fw_download_model(model_name, cache_dir=cache_dir, local_files_only=True)
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means "not cached"
        _LOGGER.debug("Model %r not cached locally: %s", model_name, exc)
        return False


def model_path(model_name: str, cache_dir: Optional[str] = None) -> str:
    """Return the local snapshot directory for a cached model.

    Raises if the model isn't cached (propagated from faster-whisper's
    ``local_files_only`` lookup).
    """

    return _fw_download_model(model_name, cache_dir=cache_dir, local_files_only=True)


def download_model(model_name: str, *, cache_dir: Optional[str] = None) -> str:
    """Download a model and return its local snapshot directory.

    Raises:
        ValueError: If ``model_name`` is not recognized.
    """

    if model_name not in available_models():
        raise ValueError(
            f"Unknown Whisper model {model_name!r}. "
            f"Available: {', '.join(available_models())}"
        )

    if is_model_cached(model_name, cache_dir):
        path = model_path(model_name, cache_dir)
        _LOGGER.info(
            "Whisper model '%s' already cached at %s; skipping download.",
            model_name,
            path,
        )
        return path

    _LOGGER.info(
        "Whisper model '%s' not cached; downloading from Hugging Face "
        "(watch this log for progress) …",
        model_name,
    )
    path = _fw_download_model(model_name, cache_dir=cache_dir)
    _LOGGER.info("Whisper model '%s' download complete: %s", model_name, path)
    return path


def cache_size_bytes(path: str) -> int:
    """Return the total size in bytes of a cached model (file or directory)."""

    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _human_bytes(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""

    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


# ---------------------------------------------------------------------------
# Background download
# ---------------------------------------------------------------------------


_BACKGROUND_THREADS: List[threading.Thread] = []
_BACKGROUND_LOCK = threading.Lock()


def start_background_download(
    model_name: str,
    *,
    on_complete: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[BaseException], None]] = None,
) -> threading.Thread:
    """Start downloading a model in a background thread.

    Returns the ``threading.Thread`` so callers can ``join()`` if needed.
    The thread is tracked in a module-level list so
    :func:`wait_for_pending_downloads` can join them all at shutdown.
    """

    def _runner() -> None:
        try:
            path = download_model(model_name)
        except BaseException as exc:  # noqa: BLE001
            _LOGGER.exception("Whisper model '%s' download failed: %s", model_name, exc)
            if on_error is not None:
                try:
                    on_error(exc)
                except Exception:  # pragma: no cover - user callback
                    _LOGGER.exception("download on_error callback raised")
            return
        if on_complete is not None:
            try:
                on_complete(path)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("download on_complete callback raised")

    thread = threading.Thread(
        target=_runner,
        name=f"whisper-download-{model_name}",
        daemon=True,
    )
    with _BACKGROUND_LOCK:
        _BACKGROUND_THREADS.append(thread)
    thread.start()
    return thread


def wait_for_pending_downloads(timeout: Optional[float] = None) -> None:
    """Block until all background download threads have finished.

    ``timeout`` is per-thread, mirroring :meth:`threading.Thread.join`.
    """

    with _BACKGROUND_LOCK:
        threads = list(_BACKGROUND_THREADS)
    for thread in threads:
        thread.join(timeout=timeout)


def active_downloads() -> List[str]:
    """Return the model names currently being downloaded in the background."""

    with _BACKGROUND_LOCK:
        return [t.name for t in _BACKGROUND_THREADS if t.is_alive()]
