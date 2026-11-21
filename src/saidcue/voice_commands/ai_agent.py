"""AI fallback: turn a free-form spoken command into validated actions.

When a wake-word phrase matches no configured command, the configured LLM
gets the request plus desktop context (foreground window, open windows,
tab names and — when clicks are allowed — the clickable elements of the
window in front) and must answer with a JSON plan. The plan is validated
with the same :func:`~.config.validate_action` as configured commands,
then checked against safety rules before anything runs.

A request can take several *rounds* ("open settings and turn on dark
mode"): after each round's actions run, the model is asked again with a
fresh view of the screen and what happened so far, until it says
``"done": true`` (see :mod:`.service` for the loop).

Safety rules (enforced here, not trusted to the model):

* Only the actions in :data:`~.config.ACTION_TYPES` exist — there is no way
  to run a shell command or a program with arguments.
* No Enter / Return key presses at all (the AI can type into a field but
  never submit it with the keyboard).
* No ``type_text`` or ``keys`` when the input would land in a terminal
  window (Windows Terminal / console).
* Clicks only when ``allow_clicks`` is on, only on elements it was shown
  or named, and never blind mouse clicks. A click on something that
  sounds destructive (delete, buy, send, …) or closing a window needs the
  user's spoken "yes" first (:attr:`Plan.risky`).
* No pickers, labels or grid: those wait for the user.
* At most :data:`MAX_STEPS` actions per round.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .config import MEDIA_KEYS, WINDOW_OPS, ActionError, validate_action
from .parser import normalize
from .spoken_keys import parse_key_press
from .windows import TERMINAL_CLASSES, WindowInfo, find_window

_LOGGER = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_ELEMENTS = 150

# A click on an element whose label contains one of these needs a spoken
# "yes" first. Whole words, after normalize() (accents removed).
RISKY_WORDS = (
    "delete", "remove", "erase", "trash", "discard", "clear", "reset", "wipe",
    "buy", "purchase", "pay", "checkout", "check out", "place order", "order now",
    "subscribe", "donate",
    "send", "submit", "post", "publish", "tweet", "reply", "share", "upload",
    "confirm", "accept", "agree", "allow", "install", "uninstall", "format disk",
    "format drive",
    "sign out", "log out", "logout", "unsubscribe", "block", "report", "ban",
    "close account", "deactivate", "transfer", "end stream", "stop stream",
    "go live", "start streaming", "restart", "shut down", "shutdown",
    "eliminar", "borrar", "comprar", "pagar", "enviar", "publicar", "aceptar",
    "confirmar", "cerrar sesion", "desinstalar",
)  # fmt: skip

# Actions that open something waiting for the user: never for the AI.
_INTERACTIVE = ("labels", "grid", "list_windows", "ask_listen")


def is_risky(label: str) -> bool:
    text = f" {normalize(label)} "
    return any(f" {w} " in text for w in RISKY_WORDS)


def system_prompt(allow_clicks: bool = False) -> str:
    click_actions = ""
    click_rules = "- You cannot click. Use keys, focus_window, open and scroll.\n"
    if allow_clicks:
        click_actions = (
            '- {"type": "click_element", "id": <id from "elements">, '
            '"button": "left"|"right"|"double"} (clicks or focuses that element)\n'
            '- {"type": "click", "target": "<label of a button, link, menu item or '
            'text box>"} (when it isn\'t in "elements" yet)\n'
        )
        click_rules = (
            '- "elements" lists what can be clicked in the window in front, with ids.\n'
            "  Click with click_element by id. To type into a text box, click it first,\n"
            "  then type_text.\n"
            "- Clicks on anything that deletes, buys, sends or closes are confirmed by\n"
            "  the user before they run; still only do them when clearly asked.\n"
        )
    return f"""You control a Windows PC for a live streamer by voice.
Turn the user's spoken request (it comes from speech recognition, so expect
typos) into actions. You work in rounds: after your actions run you are asked
again with the new screen state and what happened ("history"), until done.

Reply with ONLY a JSON object, no prose, no code fences:
{{"actions": [ ... ], "say": "<very short message in English>", "done": true|false}}

"done": true when this reply finishes the request (most simple requests finish
in one round). "done": false when you must see the result first — e.g. after
opening an app or a menu, to click something in it next round.

Allowed actions (nothing else exists):
- {{"type": "open", "target": "<URL, protocol like ms-settings:, or program like notepad / calc / mspaint>"}}
- {{"type": "launch_app", "name": "<installed app name, e.g. OBS Studio, Spotify>"}}
- {{"type": "focus_window", "title": "<part of an open window's title>"}}
- {{"type": "switch_tab", "app": "terminal"|"browser"|"any", "tab": "<part of tab name, or a number 1-9>"}} ("any" searches every app that has tabs)
- {{"type": "media", "key": one of {list(MEDIA_KEYS)}, "repeat": 1}} (each volume press is 2%)
- {{"type": "keys", "keys": "<combo like <ctrl>+t, <alt>+f, <tab>, <down>, <esc>>", "repeat": 1}}
- {{"type": "type_text", "text": "<text to paste into the focused box>"}}
- {{"type": "dictation", "state": "on"|"off"|"toggle"}}
- {{"type": "wait", "ms": <0-5000>}}
- {{"type": "volume", "level": <0-100>}} or {{"type": "volume", "mute": true|false}}
- {{"type": "scroll", "direction": "up"|"down"|"top"|"bottom", "amount": "small"|"page"|"large"}}
- {{"type": "window", "op": one of {list(WINDOW_OPS)}}} (acts on the window in front)
{click_actions}
Rules:
- At most {MAX_STEPS} actions per round. Prefer the fewest.
- Never press Enter/Return. Never type into a terminal.
{click_rules}- To search the web, open a search URL (e.g. https://www.google.com/search?q=...).
- Use window titles / tab names from the context when they match.
- If the request names a tab (or asks for "the X tab"), use switch_tab with
  the matching tab name. Never use "open" for a tab that is already open.
- If a step failed (see "history"), try another way or stop and explain.
- If the request is impossible or unsafe, reply {{"actions": [], "say": "<why, briefly>", "done": true}}.
"""


SYSTEM_PROMPT = system_prompt(False)


class PlanRejected(Exception):
    """The model's plan failed validation or a safety rule."""


@dataclass
class Plan:
    actions: List[Dict[str, Any]]
    say: str
    # The request is finished after these actions (no further rounds).
    done: bool = True
    # What needs the user's spoken "yes" before the actions run.
    risky: List[str] = field(default_factory=list)


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
    allow_clicks: bool = False,
    targets: Optional[Sequence[Any]] = None,
    allow_points: bool = False,
) -> Plan:
    """Validate a parsed plan and enforce the safety rules.

    ``targets`` are the elements the model was shown (``click_element``
    ids are 1-based positions in it).
    """

    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, list):
        raise PlanRejected("'actions' must be a list")
    if len(actions_raw) > MAX_STEPS:
        raise PlanRejected(f"plan has {len(actions_raw)} steps (max {MAX_STEPS})")
    try:
        actions = [validate_action(a) for a in actions_raw]
    except ActionError as exc:
        raise PlanRejected(str(exc)) from exc

    risky: List[str] = []
    # Track which window would receive keyboard input at each step.
    target_is_terminal = bool(foreground and foreground.class_name in TERMINAL_CLASSES)
    for a in actions:
        kind = a["type"]
        if kind in _INTERACTIVE:
            raise PlanRejected(f"{kind} is not allowed for the AI")
        if kind == "switch_tab":
            target_is_terminal = a["app"] == "terminal"
        elif kind == "focus_window":
            match = find_window(list(windows), a["title"])
            target_is_terminal = bool(match and match.class_name in TERMINAL_CLASSES)
        elif kind in ("open", "launch_app"):
            target_is_terminal = False
        elif kind in ("keys", "press"):
            if kind == "press":
                parsed = parse_key_press(a["target"])
                if parsed is None:
                    # Not a key: "press" would click a button by its label.
                    raise PlanRejected("use click, not press, to click")
                combo = parsed[0]
            else:
                combo = a["keys"].lower()
            if "enter" in combo or "return" in combo:
                raise PlanRejected("pressing Enter is not allowed")
            if target_is_terminal:
                raise PlanRejected("sending keys to a terminal is not allowed")
            if "<f4>" in combo and "<alt>" in combo:
                risky.append("close the window")
        elif kind == "mouse" and a["op"] in ("click", "drag_end"):
            raise PlanRejected("clicking blind is not allowed for the AI")
        elif kind == "click":
            # Clicking needs the user's permission (ai_clicks): the AI may
            # misread the page, and a click can submit, buy or delete.
            if not allow_clicks:
                raise PlanRejected("clicking is not allowed for the AI")
            label = a.get("target") or " / ".join(a.get("any", []))
            if is_risky(label):
                risky.append(f"click “{label[:40]}”")
            target_is_terminal = False
        elif kind == "click_element":
            if not allow_clicks:
                raise PlanRejected("clicking is not allowed for the AI")
            if targets is None or not 1 <= a["id"] <= len(targets):
                raise PlanRejected(f"there is no element {a['id']}")
            name = getattr(targets[a["id"] - 1], "name", "") or f"element {a['id']}"
            if is_risky(name):
                risky.append(f"click “{name[:40]}”")
            target_is_terminal = False
        elif kind == "click_point":
            if not (allow_clicks and allow_points):
                raise PlanRejected("clicking a point needs AI vision")
            label = a.get("target", "")
            if not label:
                raise PlanRejected("click_point needs a 'target' describing it")
            if is_risky(label):
                risky.append(f"click “{label[:40]}”")
        elif kind == "close_app" or (kind == "window" and a["op"] == "close"):
            risky.append(f"close {a.get('name', 'the window')}")
        elif kind == "type_text":
            if "\n" in a["text"] or "\r" in a["text"]:
                raise PlanRejected("typing line breaks is not allowed")
            if target_is_terminal:
                raise PlanRejected("typing into a terminal is not allowed")
    say = raw.get("say")
    done = raw.get("done", True)
    return Plan(
        actions=actions,
        say=say if isinstance(say, str) else "",
        done=done if isinstance(done, bool) else True,
        risky=risky,
    )


Targets = Tuple[Optional[int], List[Any]]


class AIAgent:
    """Ask the LLM for a plan. ``complete(system, user) -> str`` is injected.

    ``elements`` returns ``(hwnd, targets)`` for the window in front (the
    runner's :meth:`~.actions.ActionRunner.label_targets`); it is only
    used when ``allow_clicks`` is on.
    """

    def __init__(
        self,
        complete: Callable[..., str],
        *,
        windows=None,
        tabs=None,
        elements: Optional[Callable[[], Targets]] = None,
        allow_clicks: bool = False,
        max_rounds: int = 6,
    ) -> None:
        self._complete = complete
        self._windows = windows
        self._tabs = tabs
        self._elements = elements
        self.allow_clicks = allow_clicks and elements is not None
        self.max_rounds = max(1, int(max_rounds))

    def plan(self, request: str, command_names: Sequence[str] = ()) -> Plan:
        """A single round with no history (kept for simple callers)."""

        plan, _targets = self.step(request, command_names, [], 1)
        return plan

    def step(
        self,
        request: str,
        command_names: Sequence[str],
        history: Sequence[Dict[str, Any]],
        round_no: int,
    ) -> Tuple[Plan, Targets]:
        """One round: look at the screen, ask the model, check its plan.

        Returns the plan and the ``(hwnd, targets)`` its ``click_element``
        ids refer to.
        """

        foreground = self._windows.foreground() if self._windows else None
        windows = self._windows.list_windows() if self._windows else []
        context: Dict[str, Any] = {
            "request": request,
            "foreground_window": foreground.title if foreground else None,
            "open_windows": [w.title for w in windows[:20]],
        }
        if round_no == 1:
            # Tabs are slow to read and rarely change mid-request.
            context["terminal_tabs"] = self._safe_tabs("terminal")
            context["browser_tabs"] = self._safe_tabs("browser")
            context["user_commands"] = list(command_names)[:30]
        else:
            context["round"] = round_no
            context["history"] = list(history)[-6:]
        targets: Targets = (None, [])
        if self.allow_clicks:
            targets = self._safe_elements()
            context["elements"] = _describe(targets[1])
        reply = self._complete(
            system_prompt(self.allow_clicks), json.dumps(context, ensure_ascii=False)
        )
        _LOGGER.info("Voice AI round %d for %r: %s", round_no, request, reply[:500])
        plan = check_plan(
            extract_json(reply),
            foreground=foreground,
            windows=windows,
            allow_clicks=self.allow_clicks,
            targets=targets[1],
        )
        return plan, targets

    def _safe_elements(self) -> Targets:
        try:
            hwnd, targets = self._elements()
            return hwnd, list(targets)
        except Exception as exc:  # noqa: BLE001 - context is best effort
            _LOGGER.debug("Could not list elements: %s", exc)
            return None, []

    def _safe_tabs(self, app: str) -> List[str]:
        if self._tabs is None:
            return []
        try:
            return [n[:60] for n in self._tabs.list_tabs(app)][:30]
        except Exception as exc:  # noqa: BLE001 - context is best effort
            _LOGGER.debug("Could not list %s tabs: %s", app, exc)
            return []


def _describe(targets: Sequence[Any]) -> List[Dict[str, Any]]:
    """Named elements as ``{"id", "name", "type"}``; ids are 1-based positions."""

    out: List[Dict[str, Any]] = []
    for i, t in enumerate(targets, start=1):
        name = (getattr(t, "name", "") or "").strip()
        if not name:
            continue  # the model can't tell unnamed icons apart
        out.append(
            {
                "id": i,
                "name": name[:60],
                "type": "text box" if getattr(t, "kind", "") == "input" else "button",
            }
        )
        if len(out) >= MAX_ELEMENTS:
            break
    return out
