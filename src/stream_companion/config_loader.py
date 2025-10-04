"""Utilities for loading shortcut configuration from JSON files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import jsonschema

from .models import ActivatorConfig, OverlayConfig, Shortcut

_LOGGER = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised when the configuration file cannot be loaded or validated."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_paths() -> tuple[Path, Path, Path]:
    root = _repository_root()
    config_path = root / "config" / "shortcuts.json"
    schema_path = root / "config" / "schema.json"
    sample_path = root / "config" / "shortcuts.sample.json"
    return config_path, schema_path, sample_path


def load_shortcuts(
    config_path: Optional[Path] = None,
    *,
    schema_path: Optional[Path] = None,
    sample_path: Optional[Path] = None,
) -> List[Shortcut]:
    """Load shortcuts from a JSON configuration file.

    If the configuration file does not exist it is populated from the sample
    template so streamers have a starting point.
    """

    cfg_path, sch_path, samp_path = _resolve_paths(
        config_path, schema_path, sample_path
    )
    _ensure_config_exists(cfg_path, samp_path)

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {cfg_path} is not valid JSON: {exc}") from exc

    _validate_config(data, sch_path)
    _, shortcuts = _hydrate_config(data)
    _LOGGER.info("Loaded %d shortcuts from %s", len(shortcuts), cfg_path)
    return shortcuts


def _resolve_paths(
    config_path: Optional[Path],
    schema_path: Optional[Path],
    sample_path: Optional[Path],
) -> tuple[Path, Path, Path]:
    defaults = _default_paths()
    cfg_path = config_path or defaults[0]
    sch_path = schema_path or defaults[1]
    samp_path = sample_path or defaults[2]
    return cfg_path, sch_path, samp_path


def _ensure_config_exists(config_path: Path, sample_path: Path) -> None:
    if config_path.exists():
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if sample_path.exists():
        config_path.write_text(
            sample_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _LOGGER.info("Created sample configuration at %s", config_path)
    else:
        config_path.write_text(
            '{\n  "version": "1.0.0",\n  "shortcuts": []\n}\n', encoding="utf-8"
        )
        _LOGGER.info(
            "Sample template missing; created empty configuration at %s", config_path
        )


def _validate_config(data: dict, schema_path: Path) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Schema file {schema_path} is missing") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Schema file {schema_path} is not valid JSON: {exc}"
        ) from exc

    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        raise ConfigError(f"Configuration validation error: {exc.message}") from exc


def _hydrate_config(data: dict) -> Tuple[Optional[ActivatorConfig], List[Shortcut]]:
    activator: Optional[ActivatorConfig] = None
    if isinstance(data.get("activator"), dict):
        a = data["activator"]
        try:
            activator = ActivatorConfig(
                hotkey=a["hotkey"],
                mode=a.get("mode", "press"),
                timeout_ms=int(a.get("timeout_ms", 1500)),
            )
        except KeyError as exc:
            raise ConfigError(f"Activator missing required field: {exc.args[0]}") from exc

    shortcuts: List[Shortcut] = []
    for index, raw in enumerate(data.get("shortcuts", [])):
        try:
            overlay = (
                OverlayConfig(
                    file=raw["overlay"]["file"],
                    x=raw["overlay"].get("x", 0),
                    y=raw["overlay"].get("y", 0),
                    duration_ms=raw["overlay"].get("duration", 1500),
                    width=raw["overlay"].get("width"),
                    height=raw["overlay"].get("height"),
                )
                if "overlay" in raw and raw["overlay"] is not None
                else None
            )

            hotkey = raw.get("hotkey")
            suffix_raw = raw.get("suffix")
            if hotkey is None and suffix_raw is None:
                raise ConfigError(
                    f"Shortcut at index {index} must define either 'hotkey' or 'suffix'"
                )

            suffix_tuple = None
            if suffix_raw is not None:
                if isinstance(suffix_raw, str):
                    tokens = [suffix_raw]
                elif isinstance(suffix_raw, list):
                    tokens = [str(t) for t in suffix_raw]
                    if not tokens:
                        raise ConfigError(
                            f"Shortcut at index {index} has empty suffix list"
                        )
                else:
                    raise ConfigError(
                        f"Shortcut at index {index} has invalid 'suffix' type"
                    )
                suffix_tuple = tuple(t.strip().lower() for t in tokens)

            shortcut = Shortcut(
                hotkey=hotkey,
                suffix=suffix_tuple,
                sound_path=raw.get("sound"),
                overlay=overlay,
            )
        except KeyError as exc:
            raise ConfigError(
                f"Shortcut at index {index} is missing required field: {exc.args[0]}"
            ) from exc
        shortcuts.append(shortcut)
    return activator, shortcuts


def load_config(
    config_path: Optional[Path] = None,
    *,
    schema_path: Optional[Path] = None,
    sample_path: Optional[Path] = None,
) -> Tuple[Optional[ActivatorConfig], List[Shortcut]]:
    """Load full configuration including optional activator and shortcuts."""

    cfg_path, sch_path, samp_path = _resolve_paths(
        config_path, schema_path, sample_path
    )
    _ensure_config_exists(cfg_path, samp_path)

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file {cfg_path} is not valid JSON: {exc}") from exc

    _validate_config(data, sch_path)
    activator, shortcuts = _hydrate_config(data)
    _LOGGER.info("Loaded %d shortcuts from %s", len(shortcuts), cfg_path)
    return activator, shortcuts


def save_shortcuts(
    shortcuts: List[Shortcut],
    config_path: Optional[Path] = None,
    *,
    schema_path: Optional[Path] = None,
) -> None:
    """Backward-compatible save: writes only shortcuts (no activator).

    Prefer save_config() to persist the activator as well.
    """

    save_config(None, shortcuts, config_path=config_path, schema_path=schema_path)


def _serialize(activator: Optional[ActivatorConfig], shortcuts: List[Shortcut]) -> dict:
    """Convert config to JSON-serializable dictionary."""

    serialized = []
    for shortcut in shortcuts:
        entry: dict = {}
        if shortcut.hotkey:
            entry["hotkey"] = shortcut.hotkey
        if shortcut.suffix:
            if len(shortcut.suffix) == 1:
                entry["suffix"] = shortcut.suffix[0]
            else:
                entry["suffix"] = list(shortcut.suffix)
        if shortcut.sound_path:
            entry["sound"] = shortcut.sound_path
        if shortcut.overlay:
            entry["overlay"] = {
                "file": shortcut.overlay.file,
                "x": shortcut.overlay.x,
                "y": shortcut.overlay.y,
                "duration": shortcut.overlay.duration_ms,
            }
            if shortcut.overlay.width is not None:
                entry["overlay"]["width"] = shortcut.overlay.width
            if shortcut.overlay.height is not None:
                entry["overlay"]["height"] = shortcut.overlay.height
        serialized.append(entry)

    data: dict = {"version": "1.1.0", "shortcuts": serialized}
    if activator is not None:
        data["activator"] = {
            "hotkey": activator.hotkey,
            "mode": getattr(activator, "mode", "press"),
            "timeout_ms": getattr(activator, "timeout_ms", 1500),
        }
    return data


def save_config(
    activator: Optional[ActivatorConfig],
    shortcuts: List[Shortcut],
    *,
    config_path: Optional[Path] = None,
    schema_path: Optional[Path] = None,
) -> None:
    """Save full configuration including optional activator and shortcuts."""

    cfg_path, sch_path, _ = _resolve_paths(config_path, schema_path, None)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    data = _serialize(activator, shortcuts)
    _validate_config(data, sch_path)

    cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _LOGGER.info("Saved %d shortcuts to %s", len(shortcuts), cfg_path)
