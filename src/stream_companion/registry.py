"""Shortcut registry for the Streaming Companion Tool MVP."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from .config_loader import ConfigError, load_config, load_full_config
from .llm.config import LLMConfig
from .models import ActivatorConfig, Shortcut, STTConfig

_LOGGER = logging.getLogger(__name__)
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

_CACHED_ACTIVATOR: Optional[ActivatorConfig] = None
_CACHED_SHORTCUTS: Optional[list[Shortcut]] = None
_CACHED_STT: Optional[STTConfig] = None
_CACHED_LLM: Optional[LLMConfig] = None
_FULL_CONFIG_LOADED: bool = False


def default_shortcuts() -> List[Shortcut]:
    """Return the built-in shortcut list used for the Phase 1 MVP.

    The default registry is intentionally empty so that streamers can supply
    their own configuration without shipping placeholder assets. Future phases
    will populate this list from a JSON configuration file.
    """

    return []


def _load_config_cached() -> tuple[Optional[ActivatorConfig], list[Shortcut]]:
    global _CACHED_ACTIVATOR
    global _CACHED_SHORTCUTS
    if _CACHED_SHORTCUTS is not None:
        return _CACHED_ACTIVATOR, _CACHED_SHORTCUTS
    try:
        activator, shortcuts = load_config()
        _CACHED_ACTIVATOR = activator
        _CACHED_SHORTCUTS = list(shortcuts)
    except ConfigError as exc:
        _LOGGER.warning("Falling back to built-in shortcuts: %s", exc)
        _CACHED_ACTIVATOR = None
        _CACHED_SHORTCUTS = list(default_shortcuts())
    return _CACHED_ACTIVATOR, _CACHED_SHORTCUTS


def _load_full_config_cached() -> tuple[
    Optional[ActivatorConfig],
    list[Shortcut],
    Optional[STTConfig],
    Optional[LLMConfig],
]:
    global _CACHED_ACTIVATOR
    global _CACHED_SHORTCUTS
    global _CACHED_STT
    global _CACHED_LLM
    global _FULL_CONFIG_LOADED
    if _FULL_CONFIG_LOADED:
        return _CACHED_ACTIVATOR, _CACHED_SHORTCUTS, _CACHED_STT, _CACHED_LLM
    try:
        activator, shortcuts, stt, llm = load_full_config()
        _CACHED_ACTIVATOR = activator
        _CACHED_SHORTCUTS = list(shortcuts)
        _CACHED_STT = stt
        _CACHED_LLM = llm
    except ConfigError as exc:
        _LOGGER.warning("Falling back to built-in shortcuts: %s", exc)
        _CACHED_ACTIVATOR = None
        _CACHED_SHORTCUTS = list(default_shortcuts())
        _CACHED_STT = None
        _CACHED_LLM = None
    _FULL_CONFIG_LOADED = True
    return _CACHED_ACTIVATOR, _CACHED_SHORTCUTS, _CACHED_STT, _CACHED_LLM


def get_activator() -> Optional[ActivatorConfig]:
    """Return the optional global activator configuration, if present."""
    activator, _ = _load_config_cached()
    return activator


def iter_shortcuts() -> Iterable[Shortcut]:
    """Convenience iterator over the configured shortcuts."""
    _, shortcuts = _load_config_cached()
    yield from shortcuts


def get_stt_config() -> Optional[STTConfig]:
    """Return the speech-to-text configuration, if present."""

    _, _, stt, _ = _load_full_config_cached()
    return stt


def get_llm_config() -> Optional[LLMConfig]:
    """Return the LLM fact-checker configuration, if present.

    Returns ``None`` when the LLM block is absent from the config file
    (i.e. schema < 1.5.0 or the user has not configured it).
    """

    _, _, _, llm = _load_full_config_cached()
    return llm


def reload_config() -> None:
    """Clear the config cache so the next call reloads from disk."""

    global _CACHED_ACTIVATOR
    global _CACHED_SHORTCUTS
    global _CACHED_STT
    global _CACHED_LLM
    global _FULL_CONFIG_LOADED
    _CACHED_ACTIVATOR = None
    _CACHED_SHORTCUTS = None
    _CACHED_STT = None
    _CACHED_LLM = None
    _FULL_CONFIG_LOADED = False


def assets_dir() -> Path:
    """Return the canonical location for user-provided assets."""

    return _ASSETS_DIR
