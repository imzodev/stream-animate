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
            "version": {"type": "string"},
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
                        "trigger_word": {
                            "type": ["string", "null"],
                            "description": "Voice trigger word",
                        },
                        "trigger_phrases": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            ],
                            "description": "Multi-word voice trigger phrases",
                        },
                    },
                    "additionalProperties": False,
                },
            },
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
                    "trigger_cooldown_ms": {"type": "integer"},
                    "type_into_focused_window": {"type": "boolean"},
                    "voice_triggers_enabled": {"type": "boolean"},
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
                    "trigger_cooldown_ms": 2000,
                    "type_into_focused_window": False,
                    "voice_triggers_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )

    _, shortcuts, stt, _ = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert len(shortcuts) == 1
    assert stt is not None
    assert stt.enabled is True
    assert stt.always_on is False
    assert stt.hotkey == "<ctrl>+<alt>+space"
    assert stt.model == "turbo"
    assert stt.chunk_seconds == 3.0
    assert stt.type_into_focused_window is False
    assert stt.voice_triggers_enabled is True

    # Save and reload
    save_config(
        None, shortcuts, config_path=config_path, schema_path=schema_path, stt=stt
    )
    _, _, stt2, _ = load_full_config(
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

    _, _, stt, _ = load_full_config(
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
    _, _, stt, _ = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert stt is not None
    assert stt.always_on is True
    assert stt.model == "base"


def test_trigger_word_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trigger_word declared on a shortcut should survive a save/load cycle."""

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "shortcuts": [
                    {
                        "hotkey": "<ctrl>+<alt>+c",
                        "sound": "sounds/celebration.wav",
                        "trigger_word": "Fail",
                    }
                ],
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

    shortcuts = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert len(shortcuts) == 1
    assert shortcuts[0].trigger_word == "Fail"
    assert shortcuts[0].normalized_trigger_word() == "fail"

    # Save and reload — trigger_word must be preserved
    config_loader.save_config(
        None, shortcuts, config_path=config_path, schema_path=schema_path
    )
    shortcuts2 = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert shortcuts2[0].trigger_word == "fail"  # normalized on save
    assert shortcuts2[0].normalized_trigger_word() == "fail"


def test_trigger_word_omitted_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shortcut without trigger_word should have None, not a default empty string."""

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {"version": "1.0.0", "shortcuts": [{"hotkey": "a", "sound": "x.wav"}]}
        ),
        encoding="utf-8",
    )

    from stream_companion import config_loader

    monkeypatch.setattr(
        config_loader,
        "_default_paths",
        lambda: (config_path, schema_path, config_path),
    )

    shortcuts = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert shortcuts[0].trigger_word is None
    assert shortcuts[0].normalized_trigger_word() is None


def test_trigger_phrases_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-word trigger phrases should survive a save/load cycle."""

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "shortcuts": [
                    {
                        "hotkey": "<ctrl>+<alt>+c",
                        "sound": "sounds/celebration.wav",
                        "trigger_phrases": [
                            "Play Fail",
                            "react with fire",
                            "",  # empty entries should be dropped
                        ],
                    }
                ],
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

    shortcuts = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert len(shortcuts) == 1
    # Empty entries are dropped; remaining phrases are stripped
    assert shortcuts[0].trigger_phrases == ("Play Fail", "react with fire")
    # all_trigger_phrases() lowercases
    assert "play fail" in shortcuts[0].all_trigger_phrases()

    # Save and reload — phrases must be preserved
    config_loader.save_config(
        None, shortcuts, config_path=config_path, schema_path=schema_path
    )
    shortcuts2 = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert shortcuts2[0].trigger_phrases == ("Play Fail", "react with fire")


def test_trigger_phrases_omitted_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shortcut without trigger_phrases should have None, not []."""

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {"version": "1.0.0", "shortcuts": [{"hotkey": "a", "sound": "x.wav"}]}
        ),
        encoding="utf-8",
    )

    from stream_companion import config_loader

    monkeypatch.setattr(
        config_loader,
        "_default_paths",
        lambda: (config_path, schema_path, config_path),
    )

    shortcuts = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert shortcuts[0].trigger_phrases is None
    assert shortcuts[0].all_trigger_phrases() == []


def test_trigger_phrases_accepts_single_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For convenience, a single string is coerced to a 1-element list."""

    schema_path = tmp_path / "schema.json"
    _write_schema(schema_path)
    config_path = tmp_path / "shortcuts.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "shortcuts": [
                    {
                        "hotkey": "a",
                        "trigger_phrases": "just one",
                    }
                ],
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

    shortcuts = config_loader.load_shortcuts(config_path, schema_path=schema_path)
    assert shortcuts[0].trigger_phrases == ("just one",)


# ---------------------------------------------------------------------------
# LLM config (schema 1.5.0)
# ---------------------------------------------------------------------------


def _write_schema_with_llm(path: Path) -> None:
    """Schema that also accepts the 'llm' and 'stt' sections."""

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
                        "fact_check": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            },
            "stt": {"type": "object", "additionalProperties": False},
            "llm": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "persona": {"type": "string"},
                    "system_prompt": {"type": ["string", "null"]},
                    "temperature": {"type": "number"},
                    "max_tokens": {"type": "integer"},
                    "toggle_hotkey": {"type": ["string", "null"]},
                    "timeout_seconds": {"type": "integer"},
                    "silence_timeout": {"type": "number"},
                    "esc_hotkey": {"type": ["string", "null"]},
                    "thinking": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema), encoding="utf-8")


def test_llm_config_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema_with_llm(schema_path)
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "llm": {
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key_env": "DEEPSEEK_KEY",
                    "persona": "eli5",
                    "system_prompt": None,
                    "temperature": 0.7,
                    "max_tokens": 1024,
                    "toggle_hotkey": "<ctrl>+<alt>+q",
                    "timeout_seconds": 45,
                    "silence_timeout": 7.0,
                    "esc_hotkey": "<esc>",
                    "thinking": "strip",
                },
            }
        ),
        encoding="utf-8",
    )

    from stream_companion.llm.config import LLMConfig

    _, _, _, llm = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert llm is not None
    assert llm == LLMConfig(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_KEY",
        persona="eli5",
        system_prompt=None,
        temperature=0.7,
        max_tokens=1024,
        toggle_hotkey="<ctrl>+<alt>+q",
        timeout_seconds=45,
        silence_timeout=7.0,
        esc_hotkey="<esc>",
        thinking="strip",
    )

    # Round-trip via save_config
    save_config(
        None,
        [],
        config_path=config_path,
        schema_path=schema_path,
        llm=llm,
    )
    _, _, _, llm2 = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert llm2 == llm


def test_llm_omitted_returns_none(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema_with_llm(schema_path)
    config_path.write_text(
        json.dumps({"shortcuts": [{"hotkey": "a"}]}), encoding="utf-8"
    )

    _, _, _, llm = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert llm is None


def test_llm_save_without_llm_preserves_existing(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema_with_llm(schema_path)
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [{"hotkey": "a"}],
                "llm": {
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key_env": "DEEPSEEK_KEY",
                    "persona": "eli5",
                },
            }
        ),
        encoding="utf-8",
    )

    # Save without llm -> existing llm block must be preserved.
    save_config(None, [], config_path=config_path, schema_path=schema_path)
    _, _, _, llm = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert llm is not None
    assert llm.base_url == "https://api.deepseek.com/v1"
    assert llm.model == "deepseek-chat"


def test_fact_check_shortcut_round_trip(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"
    sample_path = tmp_path / "shortcuts.sample.json"

    _write_schema_with_llm(schema_path)
    config_path.write_text(
        json.dumps(
            {
                "shortcuts": [
                    {"hotkey": "a"},
                    {"hotkey": "b", "fact_check": True},
                ]
            }
        ),
        encoding="utf-8",
    )

    _, shortcuts, _, _ = load_full_config(
        config_path, schema_path=schema_path, sample_path=sample_path
    )
    assert shortcuts[0].fact_check is False
    assert shortcuts[1].fact_check is True


def test_save_config_writes_version_1_5_0(tmp_path: Path) -> None:
    config_path = tmp_path / "shortcuts.json"
    schema_path = tmp_path / "schema.json"

    _write_schema_with_llm(schema_path)
    save_config(None, [], config_path=config_path, schema_path=schema_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["version"] == "1.5.0"
