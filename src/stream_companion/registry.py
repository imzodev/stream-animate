"""Shortcut registry for the Streaming Companion Tool MVP."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import OverlayConfig, Shortcut

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"


def default_shortcuts() -> List[Shortcut]:
    """Return the built-in shortcut list used for the Phase 1 MVP.

    The registry references asset paths relative to the repository ``assets/``
    directory. If an asset is missing at runtime the individual services log a
    warning but continue operating, allowing streamers to customise the files
    without modifying code.
    """

    return [
        Shortcut(
            hotkey="<ctrl>+<alt>+1",
            sound_path=str(_ASSETS_DIR / "sample.wav"),
            overlay=OverlayConfig(
                file=str(_ASSETS_DIR / "sample.gif"),
                x=960,
                y=540,
                duration_ms=1500,
            ),
        )
    ]


def iter_shortcuts() -> Iterable[Shortcut]:
    """Convenience iterator over the default shortcuts."""

    yield from default_shortcuts()
