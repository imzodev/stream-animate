"""Voice-command configuration and action validation.

The ``voice_commands`` block of ``shortcuts.json``::

    "voice_commands": {
      "enabled": true,
      "wake_words": ["computadora", "compu"],
      "dictation_editing": true,
      "ai_fallback": true,
      "commands": [
        {"phrases": ["terminal {name}"],
         "actions": [{"type": "switch_tab", "app": "terminal", "tab": "{name}"}]}
      ]
    }

Every action — from the config *or* proposed by the AI agent — goes through
:func:`validate_action`, so there is exactly one definition of what the app
is allowed to do. There is deliberately no "run a shell command" action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

ACTION_TYPES = (
    "keys",
    "media",
    "open",
    "focus_window",
    "switch_tab",
    "type_text",
    "dictation",
    "wait",
)
MEDIA_KEYS = ("play_pause", "next", "previous", "volume_up", "volume_down", "mute")
TAB_APPS = ("terminal", "browser")
DICTATION_STATES = ("on", "off", "toggle")
MAX_WAIT_MS = 5000

DEFAULT_WAKE_WORDS: Tuple[str, ...] = ("computadora", "compu")


class ActionError(ValueError):
    """An action dict is malformed or not allowed."""


def _require_str(action: Mapping[str, Any], key: str) -> str:
    value = action.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"{action.get('type')!r} action needs a non-empty {key!r}")
    return value.strip()


def validate_action(action: Any) -> Dict[str, Any]:
    """Return a cleaned copy of ``action`` or raise :class:`ActionError`."""

    if not isinstance(action, Mapping):
        raise ActionError(f"action must be an object, got {type(action).__name__}")
    kind = action.get("type")
    if kind not in ACTION_TYPES:
        raise ActionError(f"unknown action type {kind!r}")
    out: Dict[str, Any] = {"type": kind}
    if kind == "keys":
        out["keys"] = _require_str(action, "keys")
        repeat = action.get("repeat", 1)
        if not isinstance(repeat, int) or not 1 <= repeat <= 50:
            raise ActionError("keys 'repeat' must be an integer 1-50")
        out["repeat"] = repeat
    elif kind == "media":
        key = _require_str(action, "key")
        if key not in MEDIA_KEYS:
            raise ActionError(f"media key must be one of {MEDIA_KEYS}")
        out["key"] = key
    elif kind == "open":
        out["target"] = _require_str(action, "target")
    elif kind == "focus_window":
        out["title"] = _require_str(action, "title")
    elif kind == "switch_tab":
        app = _require_str(action, "app")
        if app not in TAB_APPS:
            raise ActionError(f"switch_tab 'app' must be one of {TAB_APPS}")
        out["app"] = app
        out["tab"] = _require_str(action, "tab")
    elif kind == "type_text":
        text = action.get("text")
        if not isinstance(text, str) or not text:
            raise ActionError("type_text needs a non-empty 'text'")
        out["text"] = text
    elif kind == "dictation":
        state = _require_str(action, "state")
        if state not in DICTATION_STATES:
            raise ActionError(f"dictation 'state' must be one of {DICTATION_STATES}")
        out["state"] = state
    elif kind == "wait":
        ms = action.get("ms")
        if not isinstance(ms, int) or not 0 <= ms <= MAX_WAIT_MS:
            raise ActionError(f"wait 'ms' must be an integer 0-{MAX_WAIT_MS}")
        out["ms"] = ms
    return out


@dataclass(frozen=True)
class VoiceCommand:
    """One configured command: phrase templates and the actions they run."""

    phrases: Tuple[str, ...]
    actions: Tuple[Dict[str, Any], ...]

    def __hash__(self) -> int:  # actions are dicts; hash by phrases
        return hash(self.phrases)


@dataclass(frozen=True)
class VoiceCommandsConfig:
    enabled: bool = False
    wake_words: Tuple[str, ...] = DEFAULT_WAKE_WORDS
    dictation_editing: bool = True
    ai_fallback: bool = False
    commands: Tuple[VoiceCommand, ...] = field(default_factory=tuple)

    def __hash__(self) -> int:
        return hash((self.enabled, self.wake_words, self.commands))

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "VoiceCommandsConfig":
        """Build from the JSON block. Raises :class:`ActionError` on bad input."""

        if not raw:
            return cls()
        wake = tuple(
            w.strip() for w in raw.get("wake_words", DEFAULT_WAKE_WORDS) if w.strip()
        )
        if not wake:
            raise ActionError("voice_commands needs at least one wake word")
        commands: List[VoiceCommand] = []
        for i, entry in enumerate(raw.get("commands", [])):
            phrases = tuple(p.strip() for p in entry.get("phrases", []) if p.strip())
            if not phrases:
                raise ActionError(f"command #{i + 1} has no phrases")
            try:
                actions = tuple(validate_action(a) for a in entry.get("actions", []))
            except ActionError as exc:
                raise ActionError(f"command {phrases[0]!r}: {exc}") from exc
            if not actions:
                raise ActionError(f"command {phrases[0]!r} has no actions")
            commands.append(VoiceCommand(phrases=phrases, actions=actions))
        return cls(
            enabled=bool(raw.get("enabled", False)),
            wake_words=wake,
            dictation_editing=bool(raw.get("dictation_editing", True)),
            ai_fallback=bool(raw.get("ai_fallback", False)),
            commands=tuple(commands),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "wake_words": list(self.wake_words),
            "dictation_editing": self.dictation_editing,
            "ai_fallback": self.ai_fallback,
            "commands": [
                {"phrases": list(c.phrases), "actions": [dict(a) for a in c.actions]}
                for c in self.commands
            ],
        }
