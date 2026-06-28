from __future__ import annotations

import os
import threading

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


class _FakeHub:
    """In-memory stand-in for faster-whisper's HuggingFace download.

    Tracks which models are "cached" and records download calls so tests can
    assert behavior without touching the network.
    """

    def __init__(self, *, available=None, cached=None) -> None:
        self.available = list(
            available or ["tiny", "base", "small", "medium", "large-v3"]
        )
        self.cached = set(cached or [])
        self.download_calls: list[str] = []
        self._hook = None  # optional callable invoked at the start of a download

    def available_models(self):
        return list(self.available)

    def download(self, model_name, *, cache_dir=None, local_files_only=False):
        if local_files_only:
            if model_name not in self.cached:
                raise RuntimeError(f"{model_name} not found in local cache")
            return f"/fake/hub/{model_name}"
        if self._hook is not None:
            self._hook(model_name)
        self.download_calls.append(model_name)
        self.cached.add(model_name)
        return f"/fake/hub/{model_name}"

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_FakeHub":
        monkeypatch.setattr(model_downloader, "_fw_available_models", self.available_models)
        monkeypatch.setattr(model_downloader, "_fw_download_model", self.download)
        return self


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> _FakeHub:
    return _FakeHub().install(monkeypatch)


# ---------------------------------------------------------------------------
# Model name resolution
# ---------------------------------------------------------------------------


def test_available_models_lists_known_names(hub: _FakeHub):
    names = model_downloader.available_models()
    for expected in ("tiny", "base", "small", "medium"):
        assert expected in names
    # Friendly aliases are always advertised even if the backend omits them.
    assert "turbo" in names
    assert "large" in names


def test_available_models_falls_back_when_backend_absent(monkeypatch: pytest.MonkeyPatch):
    def boom():
        raise ImportError("no faster_whisper")

    monkeypatch.setattr(model_downloader, "_fw_available_models", boom)
    names = model_downloader.available_models()
    assert "turbo" in names
    assert "small" in names


# ---------------------------------------------------------------------------
# Cache detection
# ---------------------------------------------------------------------------


def test_is_model_cached_true_when_present(monkeypatch: pytest.MonkeyPatch):
    _FakeHub(cached=["small"]).install(monkeypatch)
    assert model_downloader.is_model_cached("small") is True


def test_is_model_cached_false_when_absent(hub: _FakeHub):
    assert model_downloader.is_model_cached("small") is False


def test_is_model_cached_false_for_unknown_model(hub: _FakeHub):
    assert model_downloader.is_model_cached("not-a-real-model") is False


def test_model_path_returns_snapshot_dir(monkeypatch: pytest.MonkeyPatch):
    _FakeHub(cached=["medium"]).install(monkeypatch)
    assert model_downloader.model_path("medium") == "/fake/hub/medium"


# ---------------------------------------------------------------------------
# download_model
# ---------------------------------------------------------------------------


def test_download_model_downloads_when_absent(hub: _FakeHub):
    path = model_downloader.download_model("small")
    assert path == "/fake/hub/small"
    assert hub.download_calls == ["small"]
    assert model_downloader.is_model_cached("small") is True


def test_download_model_skips_when_cached(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    hub = _FakeHub(cached=["tiny"]).install(monkeypatch)
    import logging

    with caplog.at_level(logging.INFO, logger="stream_companion.model_downloader"):
        path = model_downloader.download_model("tiny")
    assert path == "/fake/hub/tiny"
    # A cached model must not trigger a (non-local-only) download call.
    assert hub.download_calls == []
    assert any("already cached" in m for m in caplog.messages)


def test_download_model_unknown_model_raises(hub: _FakeHub):
    with pytest.raises(ValueError):
        model_downloader.download_model("not-a-real-model")


# ---------------------------------------------------------------------------
# start_background_download
# ---------------------------------------------------------------------------


def test_start_background_download_runs_in_thread(hub: _FakeHub):
    called = {"complete": False, "path": None}
    thread = model_downloader.start_background_download(
        "tiny",
        on_complete=lambda p: called.update(complete=True, path=p),
    )
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert called["complete"] is True
    assert called["path"] == "/fake/hub/tiny"
    assert hub.download_calls == ["tiny"]


def test_start_background_download_invokes_on_error(monkeypatch: pytest.MonkeyPatch):
    hub = _FakeHub().install(monkeypatch)

    def boom(model_name):
        raise RuntimeError("network down")

    hub._hook = boom

    errors: list = []
    thread = model_downloader.start_background_download(
        "tiny", on_error=lambda exc: errors.append(exc)
    )
    thread.join(timeout=5.0)
    assert errors and isinstance(errors[0], RuntimeError)
    assert "network down" in str(errors[0])


def test_active_downloads_lists_running_threads(monkeypatch: pytest.MonkeyPatch):
    hub = _FakeHub().install(monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow(model_name):
        started.set()
        release.wait(timeout=5.0)

    hub._hook = slow

    model_downloader.start_background_download("tiny")
    started.wait(timeout=2.0)
    assert any("tiny" in name for name in model_downloader.active_downloads())
    release.set()
    model_downloader.wait_for_pending_downloads(timeout=2.0)
    assert model_downloader.active_downloads() == []


# ---------------------------------------------------------------------------
# Cache size + human-readable byte formatting
# ---------------------------------------------------------------------------


def test_cache_size_bytes_for_file(tmp_path):
    f = tmp_path / "model.bin"
    f.write_bytes(b"x" * 2048)
    assert model_downloader.cache_size_bytes(str(f)) == 2048


def test_cache_size_bytes_for_directory(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 500)
    assert model_downloader.cache_size_bytes(str(tmp_path)) == 1500


def test_cache_size_bytes_missing_path_is_zero(tmp_path):
    assert model_downloader.cache_size_bytes(str(tmp_path / "nope")) == 0


def test_human_bytes():
    assert model_downloader._human_bytes(0) == "0.0 B"
    assert model_downloader._human_bytes(512) == "512.0 B"
    assert model_downloader._human_bytes(2048) == "2.0 KiB"
    assert model_downloader._human_bytes(5 * 1024 * 1024) == "5.0 MiB"
    assert model_downloader._human_bytes(2 * 1024 * 1024 * 1024) == "2.0 GiB"
