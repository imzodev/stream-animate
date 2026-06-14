from __future__ import annotations

import json
from pathlib import Path

import pytest

from stream_companion import registry


def _write_schema(path: Path) -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["shortcuts"],
        "properties": {
            "shortcuts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["hotkey"],
                    "properties": {"hotkey": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            "stt": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "always_on": {"type": "boolean"},
                    "hotkey": {"type": ["string", "null"]},
                    "language": {"type": "string"},
                    "model": {"type": "string"},
                    "device": {"type": ["integer", "null"]},
                    "chunk_seconds": {"type": "number"},
                    "sample_rate": {"type": "integer"},
                    "append_space": {"type": "boolean"},
                    "silence_rms_threshold": {"type": "number"},
                    "dedup_window": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    """Reset the module-level cache between tests."""

    registry.reload_config()
    yield
    registry.reload_config()


def test_get_stt_config_works_when_iter_shortcuts_called_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: when iter_shortcuts/get_activator populate the shortcut
    cache before get_stt_config is called, the STT config must still load.
    """

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "stt": {
                    "enabled": True,
                    "always_on": False,
                    "hotkey": "<ctrl>+<alt>+9",
                    "language": "auto",
                    "model": "turbo",
                    "device": None,
                },
            }
        ),
        encoding="utf-8",
    )

    # Force the loader to read from our tmp config by stubbing the defaults
    from stream_companion import config_loader

    monkeypatch.setattr(
        config_loader,
        "_default_paths",
        lambda: (config_path, schema_path, config_path),
    )

    # Prime the shortcut cache first (this is what main.py + Application do).
    list(registry.iter_shortcuts())
    # Now ask for the STT config; it must be populated, not None.
    stt = registry.get_stt_config()
    assert stt is not None
    assert stt.enabled is True
    assert stt.hotkey == "<ctrl>+<alt>+9"


def test_get_stt_config_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps({"shortcuts": [{"hotkey": "a"}]}), encoding="utf-8"
    )

    from stream_companion import config_loader

    monkeypatch.setattr(
        config_loader,
        "_default_paths",
        lambda: (config_path, schema_path, config_path),
    )

    assert registry.get_stt_config() is None
    # Should also work after priming iter_shortcuts first
    list(registry.iter_shortcuts())
    assert registry.get_stt_config() is None


def test_reload_config_clears_all_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "stt": {"enabled": True, "always_on": True, "model": "base"},
            }
        ),
        encoding="utf-8",
    )

    from stream_companion import config_loader

    monkeypatch.setattr(
        config_loader,
        "_default_paths",
        lambda: (config_path, schema_path, config_path),
    )

    stt = registry.get_stt_config()
    assert stt is not None and stt.model == "base"
    registry.reload_config()
    # After reload, the cache should be empty
    assert registry._CACHED_SHORTCUTS is None
    assert registry._CACHED_STT is None
    assert registry._FULL_CONFIG_LOADED is False
    # Re-fetching reloads from disk
    stt2 = registry.get_stt_config()
    assert stt2 is not None and stt2.model == "base"
