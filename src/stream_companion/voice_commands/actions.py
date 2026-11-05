"""Execute validated voice-command actions.

All side effects go through injected backends (keyboard, windows, tabs,
launcher, typer, STT engine) so the logic is testable without touching the
real desktop.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from .config import validate_action
from .parser import normalize
from .windows import find_window

_LOGGER = logging.getLogger(__name__)

_NUMBER_WORDS = {
    "uno": 1, "una": 1, "primera": 1, "primero": 1, "one": 1, "first": 1,
    "dos": 2, "segunda": 2, "segundo": 2, "two": 2, "second": 2,
    "tres": 3, "tercera": 3, "tercero": 3, "three": 3, "third": 3,
    "cuatro": 4, "cuarta": 4, "four": 4,
    "cinco": 5, "quinta": 5, "five": 5,
    "seis": 6, "sexta": 6, "six": 6,
    "siete": 7, "septima": 7, "seven": 7,
    "ocho": 8, "octava": 8, "eight": 8,
    "nueve": 9, "novena": 9, "nine": 9,
}  # fmt: skip


def tab_number(text: str) -> Optional[int]:
    """``"2"``, ``"dos"``, ``"segunda"`` → 2 (1-9), else ``None``."""

    norm = normalize(text)
    if norm.isdigit() and 1 <= int(norm) <= 9:
        return int(norm)
    return _NUMBER_WORDS.get(norm)


@dataclass
class ActionResult:
    ok: bool
    message: str


class ActionRunner:
    def __init__(
        self,
        *,
        keyboard_factory: Optional[Callable[[], Any]] = None,
        windows: Any = None,
        tabs_factory: Optional[Callable[[], Any]] = None,
        launcher: Optional[Callable[[str], None]] = None,
        typer: Any = None,
        engine_getter: Optional[Callable[[], Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._keyboard_factory = keyboard_factory or _default_keyboard
        self._keyboard = None
        self._windows = windows
        self._tabs_factory = tabs_factory
        self._tabs = None
        self._launcher = launcher or _default_launcher
        self._typer = typer
        self._engine_getter = engine_getter or (lambda: None)
        self._sleep = sleep

    # -- public -------------------------------------------------------------

    def run(self, actions: Iterable[Dict[str, Any]]) -> ActionResult:
        """Run actions in order; stop at the first failure."""

        messages: List[str] = []
        for raw in actions:
            action = validate_action(raw)
            try:
                result = getattr(self, f"_do_{action['type']}")(action)
            except Exception as exc:  # noqa: BLE001 - surface any backend failure
                _LOGGER.exception("Voice action %s failed", action)
                return ActionResult(False, f"{action['type']} failed: {exc}")
            if not result.ok:
                return result
            if result.message:
                messages.append(result.message)
        return ActionResult(True, " · ".join(messages) or "Done")

    def press(self, combo: str, repeat: int = 1) -> None:
        """Press a hotkey combo like ``"<ctrl>+s"`` (also used by dictation edits)."""

        from pynput.keyboard import HotKey  # type: ignore[import-not-found]

        from ..hotkeys import HotkeyManager

        keys = HotKey.parse(HotkeyManager.canonicalize(combo))
        kb = self._kb()
        for _ in range(repeat):
            for k in keys:
                kb.press(k)
            for k in reversed(keys):
                kb.release(k)

    # -- executors ----------------------------------------------------------

    def _do_keys(self, a) -> ActionResult:
        self.press(a["keys"], a.get("repeat", 1))
        return ActionResult(True, "")

    def _do_media(self, a) -> ActionResult:
        from pynput.keyboard import Key  # type: ignore[import-not-found]

        key = {
            "play_pause": Key.media_play_pause,
            "next": Key.media_next,
            "previous": Key.media_previous,
            "volume_up": Key.media_volume_up,
            "volume_down": Key.media_volume_down,
            "mute": Key.media_volume_mute,
        }[a["key"]]
        kb = self._kb()
        kb.press(key)
        kb.release(key)
        return ActionResult(True, a["key"].replace("_", " ").capitalize())

    def _do_open(self, a) -> ActionResult:
        self._launcher(a["target"])
        return ActionResult(True, f"Opening {a['target']}")

    def _do_focus_window(self, a) -> ActionResult:
        windows = self._require_windows()
        match = find_window(windows.list_windows(), a["title"])
        if match is None:
            return ActionResult(False, f"No window matches “{a['title']}”")
        if not windows.focus(match.hwnd):
            return ActionResult(False, f"Couldn't focus “{match.title}”")
        return ActionResult(True, f"Focused {match.title[:40]}")

    def _do_switch_tab(self, a) -> ActionResult:
        number = tab_number(a["tab"])
        windows = self._require_windows()
        if number is not None:
            # Tab by position: focus the app's front window, then its shortcut.
            classes = _app_classes(a["app"])
            target = next(
                (w for w in windows.list_windows() if w.class_name in classes), None
            )
            if target is None:
                return ActionResult(False, f"No {a['app']} window is open")
            windows.focus(target.hwnd)
            combo = (
                f"<ctrl>+<alt>+{number}"
                if a["app"] == "terminal"
                else f"<ctrl>+{number}"
            )
            self.press(combo)
            return ActionResult(True, f"Tab {number}")
        match = self._get_tabs().select(a["app"], a["tab"])
        if match is None:
            return ActionResult(False, f"No {a['app']} tab matches “{a['tab']}”")
        windows.focus(match.hwnd)
        return ActionResult(True, f"Tab: {match.tab_name[:40]}")

    def _do_type_text(self, a) -> ActionResult:
        if self._typer is None:
            return ActionResult(False, "Typing is not available")
        self._typer.type_text(a["text"], append_space=False)
        return ActionResult(True, "")

    def _do_dictation(self, a) -> ActionResult:
        engine = self._engine_getter()
        if engine is None:
            return ActionResult(False, "Dictation is not configured")
        state = a["state"]
        active = (not engine.is_active) if state == "toggle" else state == "on"
        engine.set_active(active)
        return ActionResult(True, "Dictation on" if active else "Dictation off")

    def _do_wait(self, a) -> ActionResult:
        self._sleep(a["ms"] / 1000.0)
        return ActionResult(True, "")

    # -- helpers ------------------------------------------------------------

    def _kb(self):
        if self._keyboard is None:
            self._keyboard = self._keyboard_factory()
        return self._keyboard

    def _require_windows(self):
        if self._windows is None:
            from .windows import Win32Windows

            self._windows = Win32Windows()
        return self._windows

    def _get_tabs(self):
        if self._tabs is None:
            if self._tabs_factory is not None:
                self._tabs = self._tabs_factory()
            else:
                from .tabs import UiaTabs

                self._tabs = UiaTabs()
        return self._tabs


def _app_classes(app: str):
    from .windows import BROWSER_CLASSES, TERMINAL_CLASSES

    return TERMINAL_CLASSES if app == "terminal" else BROWSER_CLASSES


def _default_keyboard():
    from pynput.keyboard import Controller  # type: ignore[import-not-found]

    return Controller()


def _default_launcher(target: str) -> None:
    os.startfile(target)  # type: ignore[attr-defined]  # Windows only
