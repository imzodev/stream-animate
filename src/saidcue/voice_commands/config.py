"""Voice-command configuration and action validation.

The ``voice_commands`` block of ``shortcuts.json``::

    "voice_commands": {
      "enabled": true,
      "wake_word": "computer",
      "dictation_editing": true,
      "ai_fallback": false,
      "disabled_commands": ["open_twitch"]
    }

The commands themselves are the built-in English catalog in
:mod:`.catalog`; the config only switches individual ones off.

Every action — built in *or* proposed by the AI agent — goes through
:func:`validate_action`, so there is exactly one definition of what the app
is allowed to do. There is deliberately no "run a shell command" action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

ACTION_TYPES = (
    "keys",
    "media",
    "open",
    "focus_window",
    "switch_tab",
    "type_text",
    "dictation",
    "wait",
    "click",
    "scroll",
    "volume",
    "ask",
    "ask_listen",
    "press",
    "mouse",
    "window",
)
MEDIA_KEYS = ("play_pause", "next", "previous", "volume_up", "volume_down", "mute")
TAB_APPS = ("terminal", "browser", "any")
DICTATION_STATES = ("on", "off", "toggle")
SCROLL_DIRECTIONS = ("up", "down", "top", "bottom")
SCROLL_AMOUNTS = ("small", "page", "large")
MAX_WAIT_MS = 5000
CLICK_BUTTONS = ("left", "right", "middle", "double")
MOUSE_OPS = ("click", "move", "nudge", "drag_start", "drag_end")
MOUSE_DIRECTIONS = ("up", "down", "left", "right")
WINDOW_OPS = (
    "minimize", "maximize", "restore", "close",
    "snap_left", "snap_right", "next_monitor",
)  # fmt: skip
MAX_NUDGE = 50

DEFAULT_WAKE_WORD = "computer"  # also the first preset in .catalog


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
        out["repeat"] = _repeat(action, "keys")
    elif kind == "media":
        key = _require_str(action, "key")
        if key not in MEDIA_KEYS:
            raise ActionError(f"media key must be one of {MEDIA_KEYS}")
        out["key"] = key
        repeat = action.get("repeat", 1)
        if not isinstance(repeat, int) or not 1 <= repeat <= 50:
            raise ActionError("media 'repeat' must be an integer 1-50")
        out["repeat"] = repeat
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
    elif kind == "click":
        # ``target``: what the user said; or ``any``: a list of exact labels
        # to look for (e.g. the skip-ad button in several languages).
        names = action.get("any")
        if names is not None:
            if (
                not isinstance(names, list)
                or not names
                or not all(isinstance(n, str) and n.strip() for n in names)
            ):
                raise ActionError("click 'any' must be a non-empty list of labels")
            out["any"] = [n.strip() for n in names]
        else:
            out["target"] = _require_str(action, "target")
        exact = action.get("exact", False)
        if not isinstance(exact, bool):
            raise ActionError("click 'exact' must be true or false")
        out["exact"] = exact
        out["button"] = _button(action, "click")
        if action.get("missing") is not None:  # feedback when nothing matches
            out["missing"] = _require_str(action, "missing")
    elif kind == "scroll":
        direction = _require_str(action, "direction")
        if direction not in SCROLL_DIRECTIONS:
            raise ActionError(f"scroll 'direction' must be one of {SCROLL_DIRECTIONS}")
        out["direction"] = direction
        amount = action.get("amount", "small")
        if amount not in SCROLL_AMOUNTS:
            raise ActionError(f"scroll 'amount' must be one of {SCROLL_AMOUNTS}")
        out["amount"] = amount
    elif kind == "ask":
        # A question for the AI assistant's answer panel.
        out["question"] = _require_str(action, "question")
    elif kind == "ask_listen":
        pass  # start listening for a spoken question; takes no arguments
    elif kind == "press":
        # A spoken key ("escape", "control shift t", "backspace 3 times")
        # or, when it isn't one, the label of something to click.
        out["target"] = _require_str(action, "target")
    elif kind == "mouse":
        op = _require_str(action, "op")
        if op not in MOUSE_OPS:
            raise ActionError(f"mouse 'op' must be one of {MOUSE_OPS}")
        out["op"] = op
        if op == "click":
            out["button"] = _button(action, "mouse")
        if op == "nudge":
            direction = _require_str(action, "direction")
            if direction not in MOUSE_DIRECTIONS:
                raise ActionError(
                    f"mouse 'direction' must be one of {MOUSE_DIRECTIONS}"
                )
            out["direction"] = direction
            out["repeat"] = _repeat(action, "mouse", MAX_NUDGE)
    elif kind == "window":
        op = _require_str(action, "op")
        if op not in WINDOW_OPS:
            raise ActionError(f"window 'op' must be one of {WINDOW_OPS}")
        out["op"] = op
    elif kind == "volume":
        # Exactly one of: ``level`` (0-100, or spoken text like "fifty" /
        # "{level}", parsed when it runs) or ``mute`` (true/false).
        has_level, has_mute = "level" in action, "mute" in action
        if has_level == has_mute:
            raise ActionError("volume needs exactly one of 'level' or 'mute'")
        if has_mute:
            if not isinstance(action["mute"], bool):
                raise ActionError("volume 'mute' must be true or false")
            out["mute"] = action["mute"]
        else:
            level = action["level"]
            if isinstance(level, bool) or not isinstance(level, (int, str)):
                raise ActionError("volume 'level' must be 0-100")
            if isinstance(level, int) and not 0 <= level <= 100:
                raise ActionError("volume 'level' must be 0-100")
            if isinstance(level, str) and not level.strip():
                raise ActionError("volume 'level' must be 0-100")
            out["level"] = level
    return out


def _repeat(action: Mapping[str, Any], kind: str, limit: int = 50) -> Any:
    """A repeat count: an int, or spoken text (``"{count}"``) parsed at run time."""

    repeat = action.get("repeat", 1)
    if isinstance(repeat, str) and repeat.strip():
        return repeat.strip()
    if (
        isinstance(repeat, bool)
        or not isinstance(repeat, int)
        or not 1 <= repeat <= limit
    ):
        raise ActionError(f"{kind} 'repeat' must be an integer 1-{limit}")
    return repeat


def _button(action: Mapping[str, Any], kind: str) -> str:
    button = action.get("button", "left")
    if button not in CLICK_BUTTONS:
        raise ActionError(f"{kind} 'button' must be one of {CLICK_BUTTONS}")
    return button


@dataclass(frozen=True)
class VoiceCommandsConfig:
    enabled: bool = False
    wake_word: str = DEFAULT_WAKE_WORD
    dictation_editing: bool = True
    ai_fallback: bool = False
    disabled_commands: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def commands(self) -> Tuple[Any, ...]:
        """The enabled built-in commands (see :mod:`.catalog`)."""

        from .catalog import COMMANDS

        off = set(self.disabled_commands)
        return tuple(c for c in COMMANDS if c.id not in off)

    @classmethod
    def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "VoiceCommandsConfig":
        """Build from the JSON block. Raises :class:`ActionError` on bad input."""

        if not raw:
            return cls()
        wake = raw.get("wake_word", DEFAULT_WAKE_WORD)
        if not isinstance(wake, str) or not wake.strip():
            raise ActionError("voice_commands needs a wake word")
        disabled = raw.get("disabled_commands", [])
        if not isinstance(disabled, list) or not all(
            isinstance(d, str) for d in disabled
        ):
            raise ActionError("disabled_commands must be a list of command ids")
        return cls(
            enabled=bool(raw.get("enabled", False)),
            wake_word=" ".join(wake.split()),
            dictation_editing=bool(raw.get("dictation_editing", True)),
            ai_fallback=bool(raw.get("ai_fallback", False)),
            # Unknown ids (e.g. a command removed in a later version) are kept
            # harmlessly so saving doesn't silently re-enable anything.
            disabled_commands=tuple(dict.fromkeys(disabled)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "wake_word": self.wake_word,
            "dictation_editing": self.dictation_editing,
            "ai_fallback": self.ai_fallback,
            "disabled_commands": list(self.disabled_commands),
        }
