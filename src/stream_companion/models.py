"""Core data models for the Streaming Companion Tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class OverlayConfig:
    """Configuration describing an overlay asset and its placement."""

    file: str
    x: int = 0
    y: int = 0
    duration_ms: int = 1500
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(frozen=True)
class Shortcut:
    """Representation of a triggerable shortcut."""

    # One of these must be provided:
    hotkey: Optional[str] = None  # direct hotkey, e.g. "<ctrl>+<alt>+k"
    suffix: Optional[Tuple[str, ...]] = None  # sequential chord suffix keys
    sound_path: Optional[str] = None
    overlay: Optional[OverlayConfig] = None

    def sound_id(self) -> Optional[str]:
        """Derive a reusable sound identifier from the configured path."""

        if not self.sound_path:
            return None
        return Path(self.sound_path).stem

    def label(self) -> str:
        if self.hotkey:
            return self.hotkey
        if self.suffix:
            return "activator+" + "+".join(self.suffix)
        return "<unbound>"


@dataclass(frozen=True)
class ActivatorConfig:
    """Global activator configuration for chorded shortcuts."""

    hotkey: str  # e.g. "<ctrl>+<alt>+a"
    mode: str = "press"  # "press" | "hold"
    timeout_ms: int = 1500
