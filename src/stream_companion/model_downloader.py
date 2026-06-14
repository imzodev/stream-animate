"""Whisper model downloader for the Streaming Companion Tool.

Pre-downloads the configured Whisper model to the user's local cache so
that the first dictation does not block on a multi-gigabyte download.
The download is driven by ``whisper._download`` under the hood; this
module is responsible for:

* Validating the requested model name against the canonical list.
* Checking whether the model is already on disk (and SHA256-valid).
* Running the download in a background thread.
* Streaming progress back to the Python logger (so it shows up in the
  terminal or log file) via a small ``tqdm`` adapter.

Public entry points:

* :func:`is_model_cached` — fast existence + checksum check
* :func:`download_model` — blocking download with progress callback
* :func:`start_background_download` — fire-and-forget background download
* :func:`wait_for_pending_downloads` — join all in-flight downloads
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import urllib.request
from typing import Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

# Mirror of whisper._MODELS so we can validate names without importing
# the whisper package (and so we keep working even if whisper internals move).
_WHISPER_MODELS: Dict[str, str] = {
    "tiny.en": "https://openaipublic.azureedge.net/main/whisper/models/d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03/tiny.en.pt",
    "tiny": "https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt",
    "base.en": "https://openaipublic.azureedge.net/main/whisper/models/25a8566e1d0c1e2231d1c762132cd20e0f96a85d16145c3a00adf5d1ac670ead/base.en.pt",
    "base": "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt",
    "small.en": "https://openaipublic.azureedge.net/main/whisper/models/f953ad0fd29cacd07d5a9eda5624af0f6bcf2258be67c92b79389873d91e0872/small.en.pt",
    "small": "https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt",
    "medium.en": "https://openaipublic.azureedge.net/main/whisper/models/d7440d1dc186f76616474e0ff0b3b6b879abc9d1a4926b7adfa41db2d497ab4f/medium.en.pt",
    "medium": "https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt",
    "large-v1": "https://openaipublic.azureedge.net/main/whisper/models/e4b87e7e0bf463eb8e6956e646f1e277e901512310def2c24bf0e11bd3c28e9a/large-v1.pt",
    "large-v2": "https://openaipublic.azureedge.net/main/whisper/models/81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524/large-v2.pt",
    "large-v3": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
    "large": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
    "large-v3-turbo": "https://openaipublic.azureedge.net/main/whisper/models/aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a/large-v3-turbo.pt",
    "turbo": "https://openaipublic.azureedge.net/main/whisper/models/aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a/large-v3-turbo.pt",
}


def available_models() -> List[str]:
    """Return the list of supported model names."""

    return list(_WHISPER_MODELS.keys())


def default_cache_dir() -> str:
    """Return the directory Whisper would use for cached checkpoints.

    Mirrors ``whisper._MODELS``-driven behavior: ``$XDG_CACHE_HOME/whisper``
    on Linux, ``$XDG_CACHE_HOME`` defaults to ``~/.cache``.
    """

    return os.path.join(
        os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
        "whisper",
    )


def model_filename(model_name: str) -> str:
    """Return the on-disk filename for a given model."""

    if model_name not in _WHISPER_MODELS:
        raise ValueError(
            f"Unknown Whisper model {model_name!r}. "
            f"Available: {', '.join(_WHISPER_MODELS.keys())}"
        )
    return os.path.basename(_WHISPER_MODELS[model_name])


def model_path(model_name: str, cache_dir: Optional[str] = None) -> str:
    """Return the absolute path where the model checkpoint should live."""

    return os.path.join(cache_dir or default_cache_dir(), model_filename(model_name))


def is_model_cached(model_name: str, cache_dir: Optional[str] = None) -> bool:
    """Return ``True`` iff the model exists on disk and matches its SHA256."""

    try:
        path = model_path(model_name, cache_dir)
    except ValueError:
        return False
    if not os.path.isfile(path):
        return False
    expected = _expected_sha256(model_name)
    if expected is None:
        return False
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == expected
    except OSError as exc:
        _LOGGER.debug("Could not read cached model %s: %s", path, exc)
        return False


def _expected_sha256(model_name: str) -> Optional[str]:
    """Return the SHA256 embedded in the model's URL (the path before the
    filename). Whisper uses the URL as its checksum source of truth."""

    url = _WHISPER_MODELS.get(model_name)
    if not url:
        return None
    parts = url.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else None


def _model_size_hint(model_name: str) -> Optional[int]:
    """Best-effort size hint in bytes, used only for log messages."""

    hints = {
        "tiny": 72 * 1024 * 1024,
        "tiny.en": 72 * 1024 * 1024,
        "base": 138 * 1024 * 1024,
        "base.en": 138 * 1024 * 1024,
        "small": 462 * 1024 * 1024,
        "small.en": 462 * 1024 * 1024,
        "medium": 1.5 * 1024 * 1024 * 1024,
        "medium.en": 1.5 * 1024 * 1024 * 1024,
        "large": 2.9 * 1024 * 1024 * 1024,
        "large-v1": 2.9 * 1024 * 1024 * 1024,
        "large-v2": 2.9 * 1024 * 1024 * 1024,
        "large-v3": 2.9 * 1024 * 1024 * 1024,
        "large-v3-turbo": 1.5 * 1024 * 1024 * 1024,
        "turbo": 1.5 * 1024 * 1024 * 1024,
    }
    return hints.get(model_name)


def _human_bytes(num_bytes: int) -> str:
    """Format a byte count as a short human-readable string."""

    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


# ---------------------------------------------------------------------------
# Download with progress reporting
# ---------------------------------------------------------------------------


class _LoggerTqdm:
    """Minimal tqdm-shaped adapter that writes to a Python logger.

    whisper's ``_download`` only uses ``update`` and ``close``, so a
    tiny shim is enough. We log at INFO on each milestone and at DEBUG
    between milestones.
    """

    def __init__(
        self,
        *,
        total: int,
        model_name: str,
        log_every_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self._total = int(total) if total else 0
        self._model_name = model_name
        self._log_every = max(1, int(log_every_bytes))
        self._next_milestone = self._log_every
        self._current = 0
        self._last_pct_logged = -1

    def update(self, n: int) -> None:
        self._current += int(n)
        if self._total and self._current >= self._next_milestone:
            pct = self._current * 100 / self._total
            _LOGGER.info(
                "Whisper model '%s' download: %s / %s (%.0f%%)",
                self._model_name,
                _human_bytes(self._current),
                _human_bytes(self._total) if self._total else "?",
                pct,
            )
            # Log roughly every ~10%
            next_pct = int(pct // 10) * 10 + 10
            self._next_milestone = max(
                self._next_milestone + self._log_every,
                int(self._total * next_pct / 100) if self._total else self._current + self._log_every,
            )
        else:
            _LOGGER.debug(
                "Whisper model '%s' download: %s / %s",
                self._model_name,
                _human_bytes(self._current),
                _human_bytes(self._total) if self._total else "?",
            )

    def close(self) -> None:
        if self._total:
            _LOGGER.info(
                "Whisper model '%s' download: 100%% (%s)",
                self._model_name,
                _human_bytes(self._total),
            )
        self._current = self._total

    # whisper._download sets a few attributes we don't use; ignore them.
    def set_description(self, *_args, **_kwargs) -> None:  # noqa: D401
        pass

    def __enter__(self) -> "_LoggerTqdm":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def download_model(
    model_name: str,
    *,
    cache_dir: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Download a Whisper model and return its on-disk path.

    Args:
        model_name: One of the names in :func:`available_models`.
        cache_dir: Override the cache directory (defaults to
            ``$XDG_CACHE_HOME/whisper``).
        on_progress: Optional callback ``(bytes_done, total_bytes)``
            fired periodically during the download. Used by tests and
            advanced UIs that want their own progress widget.

    Raises:
        ValueError: If ``model_name`` is not recognized.
        RuntimeError: If the download completes but the SHA256 mismatches
            (propagated from ``whisper._download``).
    """

    if model_name not in _WHISPER_MODELS:
        raise ValueError(
            f"Unknown Whisper model {model_name!r}. "
            f"Available: {', '.join(_WHISPER_MODELS.keys())}"
        )

    url = _WHISPER_MODELS[model_name]
    target_dir = cache_dir or default_cache_dir()
    target = os.path.join(target_dir, os.path.basename(url))
    os.makedirs(target_dir, exist_ok=True)

    if is_model_cached(model_name, target_dir):
        _LOGGER.info(
            "Whisper model '%s' already cached at %s; skipping download.",
            model_name,
            target,
        )
        if on_progress is not None:
            try:
                size = os.path.getsize(target)
            except OSError:
                size = 0
            on_progress(size, size)
        return target

    size_hint = _model_size_hint(model_name)
    if size_hint is not None:
        _LOGGER.info(
            "Whisper model '%s' not cached; downloading (~%s) to %s …",
            model_name,
            _human_bytes(size_hint),
            target,
        )
    else:
        _LOGGER.info(
            "Whisper model '%s' not cached; downloading to %s …",
            model_name,
            target,
        )

    # Defer the heavy import to keep the rest of the app responsive on
    # systems where whisper isn't installed.
    import whisper as _whisper_module  # type: ignore[import-not-found]

    # We drive the download directly (instead of relying on whisper's
    # private tqdm context) so we can:
    #   * call on_progress at every chunk, reliably
    #   * log progress through our own logger at our own cadence
    #   * avoid monkey-patching tqdm module-wide
    # Tests can monkeypatch ``urllib.request.urlopen`` to feed a fake
    # response without touching the network.
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, os.path.basename(url))
    expected_sha = url.split("/")[-2]
    downloader_bar = _LoggerTqdm(total=0, model_name=model_name)

    try:
        with urllib.request.urlopen(url) as source, open(target_path, "wb") as output:
            total_str = source.info().get("Content-Length")
            total = int(total_str) if total_str else 0
            downloader_bar._total = total
            if total:
                _LOGGER.info(
                    "Whisper model '%s' download starting (size=%s)",
                    model_name,
                    _human_bytes(total),
                )
            chunk_size = 8192
            while True:
                buffer = source.read(chunk_size)
                if not buffer:
                    break
                output.write(buffer)
                downloader_bar.update(len(buffer))
                if on_progress is not None:
                    try:
                        on_progress(downloader_bar._current, total)
                    except Exception:  # pragma: no cover - user callback
                        _LOGGER.exception("download on_progress callback raised")
        downloader_bar.close()
    except Exception:
        # Best-effort cleanup of the partial file
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise

    # Validate the SHA256 (mirrors whisper._download's post-write check)
    with open(target_path, "rb") as f:
        actual_sha = hashlib.sha256(f.read()).hexdigest()
    if actual_sha != expected_sha:
        try:
            os.remove(target_path)
        except OSError:
            pass
        raise RuntimeError(
            f"Whisper model '{model_name}' downloaded but SHA256 mismatch "
            f"(expected {expected_sha}, got {actual_sha}). Please retry."
        )

    final_path = target_path
    _LOGGER.info(
        "Whisper model '%s' download complete: %s",
        model_name,
        final_path,
    )
    # Reference whisper module to avoid unused-import warnings; it is
    # imported here so static analyzers can see the dependency.
    del _whisper_module
    return final_path

    final_path = result if isinstance(result, str) else target
    _LOGGER.info(
        "Whisper model '%s' download complete: %s",
        model_name,
        final_path,
    )
    return final_path


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
    The downloader's own thread is tracked in a module-level list so
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
