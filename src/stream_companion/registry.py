"""Shortcut registry for the Streaming Companion Tool MVP."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import Shortcut


_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"


def default_shortcuts() -> List[Shortcut]:
    """Return the built-in shortcut list used for the Phase 1 MVP.

    The default registry is intentionally empty so that streamers can supply
    their own configuration without shipping placeholder assets. Future phases
    will populate this list from a JSON configuration file.
    """

    return []


def iter_shortcuts() -> Iterable[Shortcut]:
    """Convenience iterator over the default shortcuts."""

    yield from default_shortcuts()


def assets_dir() -> Path:
    """Return the canonical location for user-provided assets."""

    return _ASSETS_DIR
