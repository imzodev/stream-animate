"""Shortcut registry for the Streaming Companion Tool MVP."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List

from .config_loader import ConfigError, load_config
from .models import ActivatorConfig, Shortcut


_LOGGER = logging.getLogger(__name__)
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"

_CACHED_ACTIVATOR: ActivatorConfig | None = None
_CACHED_SHORTCUTS: list[Shortcut] | None = None


def default_shortcuts() -> List[Shortcut]:
    """Return the built-in shortcut list used for the Phase 1 MVP.

    The default registry is intentionally empty so that streamers can supply
    their own configuration without shipping placeholder assets. Future phases
    will populate this list from a JSON configuration file.
    """

    return []


def _load_config_cached() -> tuple[ActivatorConfig | None, list[Shortcut]]:
    global _CACHED_ACTIVATOR, _CACHED_SHORTCUTS
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


def get_activator() -> ActivatorConfig | None:
    """Return the optional global activator configuration, if present."""
    activator, _ = _load_config_cached()
    return activator


def iter_shortcuts() -> Iterable[Shortcut]:
    """Convenience iterator over the configured shortcuts."""
    _, shortcuts = _load_config_cached()
    yield from shortcuts


def assets_dir() -> Path:
    """Return the canonical location for user-provided assets."""

    return _ASSETS_DIR
