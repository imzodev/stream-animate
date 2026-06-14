from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Optional

import pytest

from stream_companion import model_downloader


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_background_threads(monkeypatch: pytest.MonkeyPatch):
    """Prevent real network downloads and clean up the thread registry."""

    monkeypatch.setattr(model_downloader, "_BACKGROUND_THREADS", [])
    yield
    model_downloader.wait_for_pending_downloads(timeout=1.0)
    monkeypatch.setattr(model_downloader, "_BACKGROUND_THREADS", [])


def _populate_cache(cache_dir: str, model_name: str) -> str:
    """Write a placeholder file to the cache dir and return its path.

    Note: real Whisper validates SHA256 on disk. For tests we patch
    ``is_model_cached`` to return ``True`` instead of forging a SHA256
    preimage, which is computationally infeasible.
    """

    os.makedirs(cache_dir, exist_ok=True)
    target = os.path.join(cache_dir, model_downloader.model_filename(model_name))
    with open(target, "wb") as f:
        f.write(b"cached test fixture")
    return target


def test_is_model_cached_true_when_checksum_matches(tmp_path, monkeypatch):
    target = _populate_cache(str(tmp_path), "tiny")
    # Patch the SHA256 check to always pass so the test doesn't need a
    # real hash collision. We still verify the file exists.
    monkeypatch.setattr(
        model_downloader,
        "_expected_sha256",
        lambda name: hashlib.sha256(b"cached test fixture").hexdigest(),
    )
    with open(target, "rb") as f:
        content = f.read()
    # Confirm the file isn't empty and that the helper resolves it
    assert content
    assert model_downloader.is_model_cached("tiny", cache_dir=str(tmp_path)) is True


def test_is_model_cached_false_when_checksum_wrong(tmp_path, monkeypatch):
    target = model_downloader.model_path("tiny", cache_dir=str(tmp_path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(b"definitely not the right hash")
    # The real check sees the hash mismatch and returns False
    assert model_downloader.is_model_cached("tiny", cache_dir=str(tmp_path)) is False


def test_is_model_cached_false_for_unknown_model(tmp_path):
    assert (
        model_downloader.is_model_cached("not-a-real-model", cache_dir=str(tmp_path))
        is False
    )


# ---------------------------------------------------------------------------
# Model name resolution
# ---------------------------------------------------------------------------


def test_available_models_lists_known_names():
    names = model_downloader.available_models()
    assert "tiny" in names
    assert "base" in names
    assert "small" in names
    assert "medium" in names
    assert "large" in names
    assert "turbo" in names


def test_model_filename_returns_basename():
    assert model_downloader.model_filename("tiny") == "tiny.pt"
    assert model_downloader.model_filename("large") == "large-v3.pt"
    assert model_downloader.model_filename("turbo") == "large-v3-turbo.pt"


def test_unknown_model_raises():
    with pytest.raises(ValueError):
        model_downloader.model_filename("not-a-real-model")


def test_default_cache_dir_uses_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert model_downloader.default_cache_dir() == str(tmp_path / "whisper")


# ---------------------------------------------------------------------------
# Cache detection
# ---------------------------------------------------------------------------


def test_is_model_cached_false_when_no_file(tmp_path):
    assert model_downloader.is_model_cached("tiny", cache_dir=str(tmp_path)) is False


def test_is_model_cached_true_when_checksum_matches(tmp_path, monkeypatch):
    target = _populate_cache(str(tmp_path), "tiny")
    assert os.path.isfile(target)
    # Patch the SHA256 check to match the placeholder file
    monkeypatch.setattr(
        model_downloader,
        "_expected_sha256",
        lambda name: hashlib.sha256(b"cached test fixture").hexdigest(),
    )
    assert model_downloader.is_model_cached("tiny", cache_dir=str(tmp_path)) is True


def test_is_model_cached_false_when_checksum_wrong(tmp_path):
    target = model_downloader.model_path("tiny", cache_dir=str(tmp_path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(b"definitely not the right hash")
    assert model_downloader.is_model_cached("tiny", cache_dir=str(tmp_path)) is False


def test_is_model_cached_false_for_unknown_model(tmp_path):
    assert (
        model_downloader.is_model_cached("not-a-real-model", cache_dir=str(tmp_path))
        is False
    )


# ---------------------------------------------------------------------------
# download_model: mocked network
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes, chunk_size: int = 8192) -> None:
        self._payload = payload
        self._chunk_size = chunk_size
        self._offset = 0
        self.info_calls = 0

    def info(self) -> dict:
        self.info_calls += 1
        return {"Content-Length": str(len(self._payload))}

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self._chunk_size
        if self._offset >= len(self._payload):
            return b""
        chunk = self._payload[self._offset : self._offset + n]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_download_model_writes_file_and_invokes_callback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    target_dir = tmp_path / "cache"

    # Use a payload whose SHA256 we know; then patch _expected_sha256 so
    # the post-write validation passes.
    payload = b"x" * 16384
    expected_sha = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        model_downloader, "_expected_sha256", lambda name: expected_sha
    )
    # Skip the pre-check so the download actually runs.
    monkeypatch.setattr(
        model_downloader,
        "is_model_cached",
        lambda name, cache_dir=None: False,
    )

    def fake_urlopen(url, *args, **kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(model_downloader.urllib.request, "urlopen", fake_urlopen)

    progress_calls: list = []
    path = model_downloader.download_model(
        "tiny",
        cache_dir=str(target_dir),
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )
    assert os.path.isfile(path)
    assert progress_calls, "progress callback was never called"
    # The last call should report full progress
    final_done, final_total = progress_calls[-1]
    assert final_total == len(payload)
    assert final_done == len(payload)


def test_download_model_skips_when_cached(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    target_dir = tmp_path / "cache"
    _populate_cache(str(target_dir), "tiny")
    # Make the SHA256 check match the placeholder file
    monkeypatch.setattr(
        model_downloader,
        "_expected_sha256",
        lambda name: hashlib.sha256(b"cached test fixture").hexdigest(),
    )

    called = {"urlopen": 0}

    def fake_urlopen(url, *args, **kwargs):
        called["urlopen"] += 1
        return _FakeResponse(b"never used")

    monkeypatch.setattr(model_downloader.urllib.request, "urlopen", fake_urlopen)

    with caplog.at_level(logging.INFO, logger="stream_companion.model_downloader"):
        path = model_downloader.download_model("tiny", cache_dir=str(target_dir))
    assert path
    assert called["urlopen"] == 0
    assert any("already cached" in m for m in caplog.messages)


def test_download_model_unknown_model_raises(tmp_path):
    with pytest.raises(ValueError):
        model_downloader.download_model("not-a-real-model", cache_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# start_background_download
# ---------------------------------------------------------------------------


def test_start_background_download_runs_in_thread(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    target_dir = tmp_path / "cache"
    payload = b"x" * 1024
    expected_sha = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        model_downloader, "_expected_sha256", lambda name: expected_sha
    )
    monkeypatch.setattr(
        model_downloader, "is_model_cached", lambda name, cache_dir=None: False
    )

    called = {"urlopen": 0, "complete": False}

    def fake_urlopen(url, *args, **kwargs):
        called["urlopen"] += 1
        return _FakeResponse(payload)

    monkeypatch.setattr(model_downloader.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_downloader, "default_cache_dir", lambda: str(target_dir))

    thread = model_downloader.start_background_download(
        "tiny", on_complete=lambda p: called.update({"complete": True, "path": p})
    )
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert called["urlopen"] == 1
    assert called["complete"] is True


def test_start_background_download_invokes_on_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    def fake_urlopen(url, *args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(model_downloader.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_downloader, "default_cache_dir", lambda: str(tmp_path))

    errors = []
    thread = model_downloader.start_background_download(
        "tiny", on_error=lambda exc: errors.append(exc)
    )
    thread.join(timeout=5.0)
    assert errors and isinstance(errors[0], RuntimeError)
    assert "network down" in str(errors[0])


def test_active_downloads_lists_running_threads(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    started = threading.Event()
    release = threading.Event()

    def slow_urlopen(url, *args, **kwargs):
        started.set()
        release.wait(timeout=5.0)
        return _FakeResponse(b"x" * 16)

    monkeypatch.setattr(model_downloader.urllib.request, "urlopen", slow_urlopen)
    monkeypatch.setattr(model_downloader, "default_cache_dir", lambda: str(tmp_path))

    model_downloader.start_background_download("tiny")
    started.wait(timeout=2.0)
    assert any("tiny" in name for name in model_downloader.active_downloads())
    release.set()
    model_downloader.wait_for_pending_downloads(timeout=2.0)
    assert model_downloader.active_downloads() == []


# ---------------------------------------------------------------------------
# Human-readable byte formatting
# ---------------------------------------------------------------------------


def test_human_bytes():
    assert model_downloader._human_bytes(0) == "0.0 B"
    assert model_downloader._human_bytes(512) == "512.0 B"
    assert model_downloader._human_bytes(2048) == "2.0 KiB"
    assert model_downloader._human_bytes(5 * 1024 * 1024) == "5.0 MiB"
    assert model_downloader._human_bytes(2 * 1024 * 1024 * 1024) == "2.0 GiB"
