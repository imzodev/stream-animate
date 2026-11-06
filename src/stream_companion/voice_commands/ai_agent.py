"""AI fallback: turn a free-form spoken command into validated actions.

When a wake-word phrase matches no configured command, the configured LLM
gets the request plus desktop context (foreground window, open windows,
tab names) and must answer with a JSON plan. The plan is validated with the
same :func:`~.config.validate_action` as configured commands, then checked
against safety rules before anything runs.

Safety rules (enforced here, not trusted to the model):

* Only the actions in :data:`~.config.ACTION_TYPES` exist — there is no way
  to run a shell command or a program with arguments.
* No Enter / Return key presses at all (the AI can type into a field but
  never submit it).
* No ``type_text`` or ``keys`` when the input would land in a terminal
  window (Windows Terminal / console).
* No ``click`` actions (a click can submit, buy or delete).
* At most :data:`MAX_STEPS` actions.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .config import MEDIA_KEYS, ActionError, validate_action
from .windows import TERMINAL_CLASSES, WindowInfo, find_window

_LOGGER = logging.getLogger(__name__)

MAX_STEPS = 8

SYSTEM_PROMPT = f"""You control a Windows PC for a live streamer by voice.
Turn the user's spoken request (it comes from speech recognition, so expect
typos) into a short plan of actions.

Reply with ONLY a JSON object, no prose, no code fences:
{{"actions": [ ... ], "say": "<very short confirmation in English>"}}

Allowed actions (nothing else exists):
- {{"type": "open", "target": "<URL, protocol like spotify:, or program like notepad / calc / mspaint>"}}
- {{"type": "focus_window", "title": "<part of an open window's title>"}}
- {{"type": "switch_tab", "app": "terminal"|"browser", "tab": "<part of tab name, or a number 1-9>"}}
- {{"type": "media", "key": one of {list(MEDIA_KEYS)}, "repeat": 1}} (each volume press is 2%)
- {{"type": "keys", "keys": "<combo like <ctrl>+t or <alt>+<f4>>", "repeat": 1}}
- {{"type": "type_text", "text": "<text to paste>"}}
- {{"type": "dictation", "state": "on"|"off"|"toggle"}}
- {{"type": "wait", "ms": <0-5000>}}
- {{"type": "volume", "level": <0-100>}} or {{"type": "volume", "mute": true|false}}
- {{"type": "scroll", "direction": "up"|"down"|"top"|"bottom", "amount": "small"|"page"|"large"}}

Rules:
- At most {MAX_STEPS} actions. Prefer the fewest.
- Never press Enter/Return. Never type into a terminal.
- To search the web, open a search URL (e.g. https://www.google.com/search?q=...).
- Use window titles / tab names from the context when they match.
- If the request is impossible or unsafe, reply {{"actions": [], "say": "<why, briefly>"}}.
"""


class PlanRejected(Exception):
    """The model's plan failed validation or a safety rule."""


@dataclass
class Plan:
    actions: List[Dict[str, Any]]
    say: str


def extract_json(text: str) -> Dict[str, Any]:
    """Parse the first JSON object in ``text`` (tolerates code fences/prose)."""

    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\s*|\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise PlanRejected("no JSON object in reply")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as exc:
                    raise PlanRejected(f"invalid JSON: {exc}") from exc
    raise PlanRejected("unterminated JSON object")


def check_plan(
    raw: Dict[str, Any],
    *,
    foreground: Optional[WindowInfo],
    windows: Sequence[WindowInfo],
) -> Plan:
    """Validate a parsed plan and enforce the safety rules."""

    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, list):
        raise PlanRejected("'actions' must be a list")
    if len(actions_raw) > MAX_STEPS:
        raise PlanRejected(f"plan has {len(actions_raw)} steps (max {MAX_STEPS})")
    try:
        actions = [validate_action(a) for a in actions_raw]
    except ActionError as exc:
        raise PlanRejected(str(exc)) from exc

    # Track which window would receive keyboard input at each step.
    target_is_terminal = bool(foreground and foreground.class_name in TERMINAL_CLASSES)
    for a in actions:
        kind = a["type"]
        if kind == "switch_tab":
            target_is_terminal = a["app"] == "terminal"
        elif kind == "focus_window":
            match = find_window(list(windows), a["title"])
            target_is_terminal = bool(match and match.class_name in TERMINAL_CLASSES)
        elif kind == "open":
            target_is_terminal = False
        elif kind == "keys":
            combo = a["keys"].lower()
            if "enter" in combo or "return" in combo:
                raise PlanRejected("pressing Enter is not allowed")
            if target_is_terminal:
                raise PlanRejected("sending keys to a terminal is not allowed")
        elif kind == "click":
            # Clicking is for the user's own "click …" command only: the AI
            # can't see the page, and a click can submit, buy or delete.
            raise PlanRejected("clicking is not allowed for the AI")
        elif kind == "type_text":
            if "\n" in a["text"] or "\r" in a["text"]:
                raise PlanRejected("typing line breaks is not allowed")
            if target_is_terminal:
                raise PlanRejected("typing into a terminal is not allowed")
    say = raw.get("say")
    return Plan(actions=actions, say=say if isinstance(say, str) else "")


class AIAgent:
    """Ask the LLM for a plan. ``complete(system, user) -> str`` is injected."""

    def __init__(
        self,
        complete: Callable[[str, str], str],
        *,
        windows=None,
        tabs=None,
    ) -> None:
        self._complete = complete
        self._windows = windows
        self._tabs = tabs

    def plan(self, request: str, command_names: Sequence[str] = ()) -> Plan:
        foreground = self._windows.foreground() if self._windows else None
        windows = self._windows.list_windows() if self._windows else []
        context = {
            "request": request,
            "foreground_window": foreground.title if foreground else None,
            "open_windows": [w.title for w in windows[:20]],
            "terminal_tabs": self._safe_tabs("terminal"),
            "browser_tabs": self._safe_tabs("browser"),
            "user_commands": list(command_names)[:30],
        }
        reply = self._complete(SYSTEM_PROMPT, json.dumps(context, ensure_ascii=False))
        _LOGGER.info("Voice AI plan for %r: %s", request, reply[:500])
        return check_plan(extract_json(reply), foreground=foreground, windows=windows)

    def _safe_tabs(self, app: str) -> List[str]:
        if self._tabs is None:
            return []
        try:
            return [n[:60] for n in self._tabs.list_tabs(app)][:30]
        except Exception as exc:  # noqa: BLE001 - context is best effort
            _LOGGER.debug("Could not list %s tabs: %s", app, exc)
            return []
