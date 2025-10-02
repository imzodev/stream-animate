"""Tests for the configurator module."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

import pytest

from stream_companion.config_loader import save_shortcuts
from stream_companion.models import OverlayConfig, Shortcut


@pytest.fixture
def temp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def temp_schema(temp_config_dir: Path) -> Path:
    """Create a temporary schema file."""
    schema_path = temp_config_dir / "schema.json"
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Streaming Companion Shortcuts",
        "type": "object",
        "required": ["shortcuts"],
        "properties": {
            "version": {"type": "string", "default": "1.0.0"},
            "shortcuts": {"type": "array", "items": {"$ref": "#/definitions/shortcut"}},
        },
        "definitions": {
            "shortcut": {
                "type": "object",
                "required": ["hotkey"],
                "properties": {
                    "hotkey": {"type": "string"},
                    "sound": {"type": "string"},
                    "overlay": {"$ref": "#/definitions/overlay"},
                },
                "additionalProperties": False,
            },
            "overlay": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {"type": "string"},
                    "x": {"type": "integer", "default": 0},
                    "y": {"type": "integer", "default": 0},
                    "duration": {"type": "integer", "default": 1500, "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    schema_path.write_text(json.dumps(schema, indent=2))
    return schema_path


def test_save_shortcuts_empty_list(temp_config_dir: Path, temp_schema: Path) -> None:
    """Test saving an empty shortcuts list."""
    config_path = temp_config_dir / "shortcuts.json"
    shortcuts: List[Shortcut] = []

    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert data["version"] == "1.0.0"
    assert data["shortcuts"] == []


def test_save_shortcuts_single_shortcut(
    temp_config_dir: Path, temp_schema: Path
) -> None:
    """Test saving a single shortcut."""
    config_path = temp_config_dir / "shortcuts.json"
    shortcuts = [
        Shortcut(
            hotkey="<ctrl>+<alt>+1",
            sound_path="assets/sounds/test.wav",
            overlay=OverlayConfig(
                file="assets/overlays/test.gif",
                x=100,
                y=200,
                duration_ms=2000,
            ),
        )
    ]

    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert len(data["shortcuts"]) == 1
    assert data["shortcuts"][0]["hotkey"] == "<ctrl>+<alt>+1"
    assert data["shortcuts"][0]["sound"] == "assets/sounds/test.wav"
    assert data["shortcuts"][0]["overlay"]["file"] == "assets/overlays/test.gif"
    assert data["shortcuts"][0]["overlay"]["x"] == 100
    assert data["shortcuts"][0]["overlay"]["y"] == 200
    assert data["shortcuts"][0]["overlay"]["duration"] == 2000


def test_save_shortcuts_without_optional_fields(
    temp_config_dir: Path, temp_schema: Path
) -> None:
    """Test saving shortcuts without sound or overlay."""
    config_path = temp_config_dir / "shortcuts.json"
    shortcuts = [
        Shortcut(hotkey="<ctrl>+<alt>+1"),
        Shortcut(hotkey="<ctrl>+<alt>+2", sound_path="assets/sounds/test.wav"),
        Shortcut(
            hotkey="<ctrl>+<alt>+3",
            overlay=OverlayConfig(file="assets/overlays/test.gif"),
        ),
    ]

    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert len(data["shortcuts"]) == 3

    # First shortcut has only hotkey
    assert data["shortcuts"][0]["hotkey"] == "<ctrl>+<alt>+1"
    assert "sound" not in data["shortcuts"][0]
    assert "overlay" not in data["shortcuts"][0]

    # Second shortcut has sound but no overlay
    assert data["shortcuts"][1]["hotkey"] == "<ctrl>+<alt>+2"
    assert data["shortcuts"][1]["sound"] == "assets/sounds/test.wav"
    assert "overlay" not in data["shortcuts"][1]

    # Third shortcut has overlay but no sound
    assert data["shortcuts"][2]["hotkey"] == "<ctrl>+<alt>+3"
    assert "sound" not in data["shortcuts"][2]
    assert data["shortcuts"][2]["overlay"]["file"] == "assets/overlays/test.gif"


def test_save_shortcuts_multiple(temp_config_dir: Path, temp_schema: Path) -> None:
    """Test saving multiple shortcuts."""
    config_path = temp_config_dir / "shortcuts.json"
    shortcuts = [
        Shortcut(
            hotkey="<ctrl>+<alt>+1",
            sound_path="assets/sounds/test1.wav",
        ),
        Shortcut(
            hotkey="<ctrl>+<alt>+2",
            sound_path="assets/sounds/test2.wav",
        ),
        Shortcut(
            hotkey="<ctrl>+<alt>+3",
            sound_path="assets/sounds/test3.wav",
        ),
    ]

    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    assert config_path.exists()
    data = json.loads(config_path.read_text())
    assert len(data["shortcuts"]) == 3
    assert data["shortcuts"][0]["hotkey"] == "<ctrl>+<alt>+1"
    assert data["shortcuts"][1]["hotkey"] == "<ctrl>+<alt>+2"
    assert data["shortcuts"][2]["hotkey"] == "<ctrl>+<alt>+3"


def test_save_shortcuts_creates_parent_directory(
    tmp_path: Path, temp_schema: Path
) -> None:
    """Test that save_shortcuts creates parent directories if needed."""
    config_path = tmp_path / "nested" / "config" / "shortcuts.json"
    shortcuts = [Shortcut(hotkey="<ctrl>+<alt>+1")]

    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    assert config_path.exists()
    assert config_path.parent.exists()


def test_save_shortcuts_overwrites_existing(
    temp_config_dir: Path, temp_schema: Path
) -> None:
    """Test that save_shortcuts overwrites existing configuration."""
    config_path = temp_config_dir / "shortcuts.json"

    # Save initial shortcuts
    initial_shortcuts = [Shortcut(hotkey="<ctrl>+<alt>+1")]
    save_shortcuts(initial_shortcuts, config_path, schema_path=temp_schema)

    # Save new shortcuts
    new_shortcuts = [
        Shortcut(hotkey="<ctrl>+<alt>+2"),
        Shortcut(hotkey="<ctrl>+<alt>+3"),
    ]
    save_shortcuts(new_shortcuts, config_path, schema_path=temp_schema)

    # Verify only new shortcuts exist
    data = json.loads(config_path.read_text())
    assert len(data["shortcuts"]) == 2
    assert data["shortcuts"][0]["hotkey"] == "<ctrl>+<alt>+2"
    assert data["shortcuts"][1]["hotkey"] == "<ctrl>+<alt>+3"


def test_save_shortcuts_validates_against_schema(
    temp_config_dir: Path, temp_schema: Path
) -> None:
    """Test that save_shortcuts validates against schema."""
    from stream_companion.config_loader import ConfigError

    config_path = temp_config_dir / "shortcuts.json"
    shortcuts = [Shortcut(hotkey="<ctrl>+<alt>+1")]

    # This should succeed
    save_shortcuts(shortcuts, config_path, schema_path=temp_schema)

    # Manually create invalid data to test validation
    # (Note: In practice, the Shortcut model prevents creating invalid data,
    # but this tests the validation layer)
    invalid_data = {"version": "1.0.0", "shortcuts": [{"invalid_field": "value"}]}
    config_path.write_text(json.dumps(invalid_data))

    # Trying to load this should fail
    from stream_companion.config_loader import load_shortcuts

    with pytest.raises(ConfigError):
        load_shortcuts(config_path, schema_path=temp_schema)
