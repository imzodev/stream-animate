from __future__ import annotations

import json
from pathlib import Path

import pytest

from stream_companion.config_loader import ConfigError, load_shortcuts


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
