"""Utilities for loading shortcut configuration from JSON files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import jsonschema

from .llm.config import LLMConfig
from .models import ActivatorConfig, OverlayConfig, Shortcut, STTConfig

_LOGGER = logging.getLogger(__name__)

# Bump the schema version when adding new top-level blocks or fields.
# Keep this in sync with config/schema.json.
SCHEMA_VERSION = "1.5.0"


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
            raise ConfigError(
                f"Activator missing required field: {exc.args[0]}"
            ) from exc

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

            # Voice triggers: ``trigger_word`` (legacy) and
            # ``trigger_phrases`` (new). Both can be present on the
            # same shortcut and are matched independently.
            trigger_phrases_raw = raw.get("trigger_phrases")
            trigger_phrases_tuple: Optional[Tuple[str, ...]] = None
            if trigger_phrases_raw is not None:
                if isinstance(trigger_phrases_raw, str):
                    phrases_list = [trigger_phrases_raw]
                elif isinstance(trigger_phrases_raw, list):
                    phrases_list = [str(p) for p in trigger_phrases_raw]
                else:
                    raise ConfigError(
                        f"Shortcut at index {index} has invalid 'trigger_phrases' type"
                    )
                cleaned = tuple(p.strip() for p in phrases_list if p.strip())
                trigger_phrases_tuple = cleaned or None

            shortcut = Shortcut(
                hotkey=hotkey,
                suffix=suffix_tuple,
                sound_path=raw.get("sound"),
                overlay=overlay,
                trigger_word=raw.get("trigger_word"),
                trigger_phrases=trigger_phrases_tuple,
                fact_check=bool(raw.get("fact_check", False)),
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
    """Load full configuration including optional activator and shortcuts.

    Backward-compatible signature; STT config is available via load_full_config().
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
    activator, shortcuts = _hydrate_config(data)
    _LOGGER.info("Loaded %d shortcuts from %s", len(shortcuts), cfg_path)
    return activator, shortcuts


def load_full_config(
    config_path: Optional[Path] = None,
    *,
    schema_path: Optional[Path] = None,
    sample_path: Optional[Path] = None,
) -> Tuple[
    Optional[ActivatorConfig],
    List[Shortcut],
    Optional[STTConfig],
    Optional[LLMConfig],
]:
    """Load full configuration: activator, shortcuts, STT, and LLM config.

    Returns a 4-tuple. The LLM config defaults to ``LLMConfig()`` when
    the file is older than schema 1.5.0 and has no ``llm`` block, so
    existing 1.4.0 files keep working without a migration step.
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
    activator, shortcuts = _hydrate_config(data)
    stt = _hydrate_stt_config(data.get("stt"))
    llm = _hydrate_llm_config(data.get("llm"))
    _LOGGER.info(
        "Loaded %d shortcuts from %s (stt=%s, llm=%s)",
        len(shortcuts),
        cfg_path,
        "on" if stt and stt.enabled else "off",
        "on" if llm else "off",
    )
    return activator, shortcuts, stt, llm


def _hydrate_stt_config(raw: Optional[dict]) -> Optional[STTConfig]:
    """Build an STTConfig from the raw 'stt' section, or None if missing."""

    if not isinstance(raw, dict):
        return None
    try:
        return STTConfig(
            enabled=bool(raw.get("enabled", False)),
            always_on=bool(raw.get("always_on", False)),
            hotkey=raw.get("hotkey"),
            language=str(raw.get("language", "auto")),
            model=str(raw.get("model", "turbo")),
            device=raw.get("device"),
            chunk_seconds=float(raw.get("chunk_seconds", 4.0)),
            sample_rate=int(raw.get("sample_rate", 16000)),
            append_space=bool(raw.get("append_space", True)),
            silence_rms_threshold=float(raw.get("silence_rms_threshold", 0.005)),
            dedup_window=int(raw.get("dedup_window", 64)),
            trigger_cooldown_ms=int(raw.get("trigger_cooldown_ms", 1500)),
            # Both new flags default to True; legacy config files that
            # don't carry them are interpreted as "use defaults".
            type_into_focused_window=bool(raw.get("type_into_focused_window", True)),
            voice_triggers_enabled=bool(raw.get("voice_triggers_enabled", True)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid STT configuration: {exc}") from exc


def _hydrate_llm_config(raw: Optional[dict]) -> Optional[LLMConfig]:
    """Build an LLMConfig from the raw 'llm' section, or None if missing.

    Returning None (rather than ``LLMConfig()`` defaults) when the
    block is absent lets callers distinguish "user has not configured
    the LLM" from "user wants the defaults". ``load_full_config``
    currently converts None to a default; the loader returns None to
    preserve the option for future callers.
    """

    if not isinstance(raw, dict):
        return None
    try:
        return LLMConfig(
            base_url=str(raw.get("base_url", LLMConfig().base_url)),
            model=str(raw.get("model", LLMConfig().model)),
            api_key_env=str(raw.get("api_key_env", LLMConfig().api_key_env)),
            persona=str(raw.get("persona", LLMConfig().persona)),
            system_prompt=(
                str(raw["system_prompt"])
                if raw.get("system_prompt") is not None
                else None
            ),
            temperature=float(raw.get("temperature", LLMConfig().temperature)),
            max_tokens=int(raw.get("max_tokens", LLMConfig().max_tokens)),
            toggle_hotkey=(
                str(raw["toggle_hotkey"])
                if raw.get("toggle_hotkey") is not None
                else None
            ),
            timeout_seconds=int(
                raw.get("timeout_seconds", LLMConfig().timeout_seconds)
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid LLM configuration: {exc}") from exc


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


def _serialize(
    activator: Optional[ActivatorConfig],
    shortcuts: List[Shortcut],
    stt: Optional[STTConfig] = None,
    llm: Optional[LLMConfig] = None,
) -> dict:
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
        if shortcut.normalized_trigger_word() is not None:
            entry["trigger_word"] = shortcut.normalized_trigger_word()
        if shortcut.trigger_phrases:
            # Strip + drop empty entries for clean JSON
            entry["trigger_phrases"] = [
                p for p in (raw.strip() for raw in shortcut.trigger_phrases) if p
            ]
        if shortcut.fact_check:
            # Only serialize the fact_check flag when True so the
            # default-False case keeps old configs byte-identical.
            entry["fact_check"] = True
        serialized.append(entry)

    data: dict = {"version": SCHEMA_VERSION, "shortcuts": serialized}
    if activator is not None:
        data["activator"] = {
            "hotkey": activator.hotkey,
            "mode": getattr(activator, "mode", "press"),
            "timeout_ms": getattr(activator, "timeout_ms", 1500),
        }
    if stt is not None:
        data["stt"] = {
            "enabled": stt.enabled,
            "always_on": stt.always_on,
            "hotkey": stt.hotkey,
            "language": stt.language,
            "model": stt.model,
            "device": stt.device,
            "chunk_seconds": stt.chunk_seconds,
            "sample_rate": stt.sample_rate,
            "append_space": stt.append_space,
            "silence_rms_threshold": stt.silence_rms_threshold,
            "dedup_window": stt.dedup_window,
            "trigger_cooldown_ms": stt.trigger_cooldown_ms,
            "type_into_focused_window": stt.type_into_focused_window,
            "voice_triggers_enabled": stt.voice_triggers_enabled,
        }
    if llm is not None:
        data["llm"] = {
            "base_url": llm.base_url,
            "model": llm.model,
            "api_key_env": llm.api_key_env,
            "persona": llm.persona,
            "system_prompt": llm.system_prompt,
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "toggle_hotkey": llm.toggle_hotkey,
            "timeout_seconds": llm.timeout_seconds,
        }
    return data


def save_config(
    activator: Optional[ActivatorConfig],
    shortcuts: List[Shortcut],
    *,
    config_path: Optional[Path] = None,
    schema_path: Optional[Path] = None,
    stt: Optional[STTConfig] = None,
    llm: Optional[LLMConfig] = None,
) -> None:
    """Save full configuration including optional activator and shortcuts.

    Args:
        stt: Optional STTConfig to persist. ``None`` means "do not touch the
            'stt' key" (preserves whatever is already on disk during a partial
            save). Pass an ``STTConfig`` to write it explicitly.
        llm: Same partial-save semantics for the ``llm`` block.
    """

    cfg_path, sch_path, _ = _resolve_paths(config_path, schema_path, None)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    existing_stt: Optional[STTConfig] = None
    existing_llm: Optional[LLMConfig] = None
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = None
        if isinstance(raw, dict):
            existing_stt = _hydrate_stt_config(raw.get("stt"))
            existing_llm = _hydrate_llm_config(raw.get("llm"))

    data = _serialize(
        activator,
        shortcuts,
        stt if stt is not None else existing_stt,
        llm if llm is not None else existing_llm,
    )
    _validate_config(data, sch_path)

    cfg_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _LOGGER.info("Saved %d shortcuts to %s", len(shortcuts), cfg_path)
