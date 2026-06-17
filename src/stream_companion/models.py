"""Core data models for the Streaming Companion Tool."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# LLMConfig lives in stream_companion.llm.config but is re-exported here
# for backward compatibility with code that does
# ``from stream_companion.models import LLMConfig``.
from .llm.config import LLMConfig

__all__ = [
    "ActivatorConfig",
    "LLMConfig",
    "OverlayConfig",
    "Shortcut",
    "STTConfig",
]


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
    # When the STT engine transcribes a phrase that contains this word
    # (matched on word boundaries, case-insensitive), the shortcut fires
    # in addition to any hotkey/suffix binding. None or empty string
    # disables the single-word trigger. Legacy field; for multi-word
    # phrases use ``trigger_phrases`` instead.
    trigger_word: Optional[str] = None
    # List of multi-word phrases. Each phrase is a string of one or
    # more words; matching is case-insensitive, word-boundary aware,
    # and requires the tokens to appear contiguously in the transcribed
    # phrase. Multiple phrases on the same shortcut are all checked.
    trigger_phrases: Optional[Tuple[str, ...]] = None
    # Reserved for future use: when True, this shortcut can be bound
    # to a fact-check trigger (currently the global toggle_hotkey in
    # LLMConfig is the only activation; the per-shortcut flag is a
    # no-op in v1 but lets us build per-shortcut personas later
    # without a schema bump).
    fact_check: bool = False

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

    def normalized_trigger_word(self) -> Optional[str]:
        """Return the trigger word lowercased and stripped, or None if empty."""

        if not self.trigger_word:
            return None
        normalized = self.trigger_word.strip().lower()
        return normalized or None

    def all_trigger_phrases(self) -> List[str]:
        """Return all voice triggers (single word + phrases) as a flat list.

        Empty/whitespace strings are dropped. Order is preserved:
        the legacy single word comes first, then the multi-word
        phrases in the order they were declared.
        """

        out: List[str] = []
        word = self.normalized_trigger_word()
        if word is not None:
            out.append(word)
        if self.trigger_phrases:
            for raw in self.trigger_phrases:
                if not raw:
                    continue
                normalized = raw.strip().lower()
                if normalized:
                    out.append(normalized)
        return out

    @classmethod
    def all_fact_check_shortcuts(cls, shortcuts: List["Shortcut"]) -> List["Shortcut"]:
        """Return all shortcuts flagged with ``fact_check=True``.

        Reserved for v1.1 (per-shortcut persona binding). v1 has a
        single global toggle hotkey, so the returned list is not
        consulted by the engine.
        """

        return [s for s in shortcuts if s.fact_check]


@dataclass(frozen=True)
class ActivatorConfig:
    """Global activator configuration for chorded shortcuts."""

    hotkey: str  # e.g. "<ctrl>+<alt>+a"
    mode: str = "press"  # "press" | "hold"
    timeout_ms: int = 1500


@dataclass(frozen=True)
class STTConfig:
    """Configuration for the speech-to-text feature.

    The STT pipeline can be used for two independent things:

    1. Typing the dictated text into whichever window is focused
       (``type_into_focused_window=True``).
    2. Firing configured voice-trigger shortcuts when the dictated
       phrase contains a matching trigger word
       (``voice_triggers_enabled=True``).

    Both are enabled by default. The overall STT pipeline is activated
    by ``always_on`` / ``hotkey`` as before; turning off the two
    sub-flags silences the corresponding side-effect without disabling
    STT entirely.
    """

    enabled: bool = False
    always_on: bool = False
    hotkey: Optional[str] = None  # e.g. "<ctrl>+<alt>+space"
    language: str = "auto"  # "auto" or a Whisper language code
    model: str = "turbo"  # one of tiny, base, small, medium, large, turbo
    device: Optional[int] = (
        None  # sounddevice input device index, None = system default
    )
    chunk_seconds: float = 4.0
    sample_rate: int = 16000
    append_space: bool = True
    silence_rms_threshold: float = 0.005  # skip typing when below this RMS
    # Maximum number of characters of recently-typed text kept for dedup tailing.
    dedup_window: int = 64
    # Per-shortcut cooldown for voice-triggered shortcuts (milliseconds).
    # The same trigger word will not re-fire the same shortcut within this
    # window, so a single utterance with overlapping chunks doesn't trigger
    # the sound/overlay many times.
    trigger_cooldown_ms: int = 1500
    # When True, transcribed text is also typed into the focused window.
    # When False, the STT engine still runs (so voice triggers can fire)
    # but does not type. Default True.
    type_into_focused_window: bool = True
    # When True, transcribed phrases are scanned against each shortcut's
    # ``trigger_word`` and matching shortcuts fire (sound/overlay). When
    # False, transcription still happens (if typing is enabled) but
    # voice triggers are silenced. Default True.
    voice_triggers_enabled: bool = True

    def activation_mode(self) -> str:
        """Return how the listener should activate STT: 'always' or 'hotkey'."""

        if self.always_on:
            return "always"
        if self.hotkey:
            return "hotkey"
        return "off"
