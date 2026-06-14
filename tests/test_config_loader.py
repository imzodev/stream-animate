from __future__ import annotations

import json
from pathlib import Path

import pytest

from stream_companion.config_loader import (
    ConfigError,
    load_full_config,
    load_shortcuts,
    save_config,
)


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
                    "properties": {
                        "hotkey": {"type": "string"},
                        "sound": {"type": "string"},
                        "overlay": {
                            "type": "object",
                            "required": ["file"],
                            "properties": {
                                "file": {"type": "string"},
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "duration": {"type": "integer"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
            }
        },
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema), encoding="utf-8")


def test_load_shortcuts_reads_config(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema(schema_path)
    config = {
        "shortcuts": [
            {
                "hotkey": "<ctrl>+<alt>+1",
                "sound": "assets/sounds/sample.wav",
                "overlay": {
                    "file": "assets/overlays/sample.gif",
                    "x": 10,
                    "y": 20,
                    "duration": 500,
                },
            }
        ]
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    shortcuts = load_shortcuts(
        config_path, schema_path=schema_path, sample_path=sample_path
    )

    assert len(shortcuts) == 1
    shortcut = shortcuts[0]
    assert shortcut.hotkey == "<ctrl>+<alt>+1"
    assert shortcut.sound_path == "assets/sounds/sample.wav"
    assert shortcut.overlay is not None
    assert shortcut.overlay.file == "assets/overlays/sample.gif"
    assert shortcut.overlay.duration_ms == 500


def test_load_shortcuts_creates_file_from_sample(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema(schema_path)
    sample_path.write_text(
        json.dumps({"shortcuts": [{"hotkey": "a", "sound": "sound.wav"}]}),
        encoding="utf-8",
    )

    shortcuts = load_shortcuts(
        config_path, schema_path=schema_path, sample_path=sample_path
    )

    assert config_path.exists()
    assert len(shortcuts) == 1
    assert shortcuts[0].hotkey == "a"


def test_load_shortcuts_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema(schema_path)
    config_path.write_text("{not valid json}", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_shortcuts(config_path, schema_path=schema_path, sample_path=sample_path)


def test_load_shortcuts_validation_failure(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema(schema_path)
    config_path.write_text(
        json.dumps({"shortcuts": [{"sound": "missing"}]}), encoding="utf-8"
    )

    with pytest.raises(ConfigError):
        load_shortcuts(config_path, schema_path=schema_path, sample_path=sample_path)


# ---------------------------------------------------------------------------
# STT configuration
# ---------------------------------------------------------------------------


def _write_full_schema(path: Path) -> None:
    """Schema that also accepts the 'stt' section used by the loader."""

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["shortcuts"],
        "properties": {
            "version": {"type": "string"},
            "shortcuts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["hotkey"],
                    "properties": {
                        "hotkey": {"type": "string"},
                    },
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


def test_stt_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_full_schema(schema_path)
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "stt": {
                    "enabled": True,
                    "always_on": False,
                    "hotkey": "<ctrl>+<alt>+space",
                    "language": "auto",
                    "model": "turbo",
                    "device": None,
                    "chunk_seconds": 3.0,
                    "sample_rate": 16000,
                    "append_space": True,
                    "silence_rms_threshold": 0.01,
                    "dedup_window": 32,
                },
            }
        ),
        encoding="utf-8",
    )

    _, shortcuts, stt = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert len(shortcuts) == 1
    assert stt is not None
    assert stt.enabled is True
    assert stt.always_on is False
    assert stt.hotkey == "<ctrl>+<alt>+space"
    assert stt.model == "turbo"
    assert stt.chunk_seconds == 3.0

    # Save and reload
    save_config(
        None, shortcuts, config_path=config_path, schema_path=schema_path, stt=stt
    )
    _, _, stt2 = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert stt2 == stt


def test_stt_omitted_returns_none(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_full_schema(schema_path)
    config_path.write_text(
        json.dumps({"shortcuts": [{"hotkey": "a"}]}), encoding="utf-8"
    )

    _, _, stt = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert stt is None


def test_stt_save_without_stt_preserves_existing(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_full_schema(schema_path)
    # Pre-seed config with an stt block
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "stt": {
                    "enabled": True,
                    "always_on": True,
                    "language": "en",
                    "model": "base",
                },
            }
        ),
        encoding="utf-8",
    )

    # Save without stt -> existing stt block should be preserved
    save_config(None, [], config_path=config_path, schema_path=schema_path)
    _, _, stt = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert stt is not None
    assert stt.always_on is True
    assert stt.model == "base"
