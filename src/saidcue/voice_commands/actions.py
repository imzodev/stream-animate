"""Execute validated voice-command actions.

All side effects go through injected backends (keyboard, windows, tabs,
launcher, typer, STT engine) so the logic is testable without touching the
real desktop.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from .config import validate_action
from .parser import normalize
from .windows import BROWSER_CLASSES, find_window

_LOGGER = logging.getLogger(__name__)

_NUMBER_WORDS = {
    "one": 1, "first": 1, "1st": 1,
    "two": 2, "second": 2, "2nd": 2,
    "three": 3, "third": 3, "3rd": 3,
    "four": 4, "fourth": 4, "4th": 4,
    "five": 5, "fifth": 5, "5th": 5,
    "six": 6, "sixth": 6, "6th": 6,
    "seven": 7, "seventh": 7, "7th": 7,
    "eight": 8, "eighth": 8, "8th": 8,
    "nine": 9, "ninth": 9, "9th": 9,
    # Spanish: Whisper transcribes commands in the language it hears.
    "un": 1, "uno": 1, "una": 1, "primer": 1, "primero": 1, "primera": 1,
    "dos": 2, "segundo": 2, "segunda": 2,
    "tres": 3, "tercero": 3, "tercera": 3,
    "cuatro": 4, "cuarto": 4, "cuarta": 4,
    "cinco": 5, "quinto": 5, "quinta": 5,
    "seis": 6, "sexto": 6, "sexta": 6,
    "siete": 7, "septimo": 7, "septima": 7,
    "ocho": 8, "octavo": 8, "octava": 8,
    "nueve": 9, "noveno": 9, "novena": 9,
}  # fmt: skip


def tab_number(text: str) -> Optional[int]:
    """``"2"``, ``"two"``, ``"second"`` → 2 (1-9), else ``None``."""

    norm = normalize(text)
    if norm.isdigit() and 1 <= int(norm) <= 9:
        return int(norm)
    return _NUMBER_WORDS.get(norm)


@dataclass
class ActionResult:
    ok: bool
    message: str
    # Nothing matched what the user named (e.g. no app called that): the
    # service may hand the phrase to the AI fallback instead.
    unmatched: bool = False


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
        elements_factory: Optional[Callable[[], Any]] = None,
        volume_factory: Optional[Callable[[], Any]] = None,
        assistant_getter: Optional[Callable[[], Any]] = None,
        on_ambiguous: Optional[Callable[[List[Any], str], None]] = None,
        mouse_factory: Optional[Callable[[], Any]] = None,
        apps_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._apps_factory = apps_factory
        self._apps = None
        self._apps_lock = threading.Lock()
        self._mouse_factory = mouse_factory
        self._mouse = None
        self._drag_from: Optional[tuple] = None
        self._assistant_getter = assistant_getter or (lambda: None)
        self._elements_factory = elements_factory
        self._elements = None
        self._volume_factory = volume_factory
        self._volume = None
        self._keyboard_factory = keyboard_factory or _default_keyboard
        self._keyboard = None
        self._windows = windows
        self._tabs_factory = tabs_factory
        self._tabs = None
        self._tabs_lock = threading.Lock()
        self._launcher = launcher or _default_launcher
        self._typer = typer
        self._engine_getter = engine_getter or (lambda: None)
        self._sleep = sleep
        self._on_ambiguous = on_ambiguous or (lambda _candidates, _query: None)
        self._on_choice: Callable[..., None] = lambda *_a: None
        self._on_labels: Optional[Callable[..., None]] = None
        self._on_grid: Optional[Callable[..., None]] = None
        # The elements the AI was shown this round (click_element ids).
        self._ai_targets: tuple = (None, [])

    def set_choice_handler(self, handler: Callable[..., None]) -> None:
        """Called with ``(candidates, query, noun, resolve)`` when a name
        matches several apps or windows and the user has to pick one."""

        self._on_choice = handler or (lambda *_a: None)

    def set_labels_handler(self, handler: Optional[Callable[..., None]]) -> None:
        """Called with ``(hwnd, targets, sticky)`` to show number labels."""

        self._on_labels = handler

    def set_grid_handler(self, handler: Optional[Callable[..., None]]) -> None:
        """Called with a physical ``(left, top, width, height)`` to show the grid."""

        self._on_grid = handler

    def set_ambiguous_handler(self, handler: Callable[[List[Any], str], None]) -> None:
        """Called with the candidates when a query matches several tabs."""

        self._on_ambiguous = handler or (lambda _candidates, _query: None)

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

        try:
            canonical = HotkeyManager.canonicalize(combo)
        except ValueError:
            # A lone modifier such as "<cmd>" (opens the Start menu) is a
            # valid key press even though it is not a valid hotkey.
            canonical = combo.strip().lower()
        keys = HotKey.parse(canonical)
        kb = self._kb()
        for _ in range(repeat):
            for k in keys:
                kb.press(k)
            for k in reversed(keys):
                kb.release(k)

    # -- executors ----------------------------------------------------------

    def _do_keys(self, a) -> ActionResult:
        repeat = _count_of(a.get("repeat", 1))
        if repeat is None:
            return ActionResult(False, f"How many times is “{a['repeat']}”?")
        self.press(a["keys"], repeat)
        return ActionResult(True, "")

    def _do_press(self, a) -> ActionResult:
        """Press a spoken key, or click the thing with that label."""

        from .spoken_keys import parse_key_press

        parsed = parse_key_press(a["target"])
        if parsed is not None:
            combo, count = parsed
            self.press(combo, count)
            label = combo.replace("<", "").replace(">", "").replace("cmd", "win")
            return ActionResult(
                True, f"Pressed {label}" + (f" ×{count}" if count > 1 else "")
            )
        return self._do_click({"type": "click", "target": a["target"], "exact": False})

    def _do_mouse(self, a) -> ActionResult:
        mouse = self._get_mouse()
        op = a["op"]
        x, y = mouse.position()
        if op == "click":
            button = a.get("button", "left")
            mouse.click(x, y, button)
            return ActionResult(True, _CLICK_LABELS[button])
        if op == "nudge":
            repeat = _count_of(a.get("repeat", 1))
            if repeat is None:
                return ActionResult(False, f"How far is “{a['repeat']}”?")
            dx, dy = _NUDGE[a["direction"]]
            mouse.move(x + dx * NUDGE_PIXELS * repeat, y + dy * NUDGE_PIXELS * repeat)
            return ActionResult(True, f"Mouse {a['direction']}")
        if op == "drag_start":
            self._drag_from = (x, y)
            return ActionResult(True, "Marked. Move there and say “drag here”")
        if op == "drag_end":
            if self._drag_from is None:
                return ActionResult(False, "Say “mark” where the drag starts first")
            start, self._drag_from = self._drag_from, None
            mouse.drag(start, (x, y))
            return ActionResult(True, "Dragged")
        return ActionResult(True, "")  # "move": the cursor is already there

    def click_at(self, x: int, y: int, button: str = "left") -> None:
        """Click a physical screen point (number labels, the grid, the AI)."""

        self._get_mouse().click(x, y, button)

    def move_mouse(self, x: int, y: int) -> None:
        self._get_mouse().move(x, y)

    def drag_mouse(self, start, end) -> None:
        self._get_mouse().drag(tuple(start), tuple(end))

    def mouse_position(self):
        return self._get_mouse().position()

    def _do_grid(self, a) -> ActionResult:
        if self._on_grid is None:
            return ActionResult(False, "The mouse grid isn't available")
        windows = self._require_windows()
        fg = windows.foreground()
        rect = None
        if fg is not None:
            if a.get("scope") == "window":
                r = windows.rect(fg.hwnd)
                rect = (r[0], r[1], r[2] - r[0], r[3] - r[1]) if r else None
            else:
                rect = windows.monitor_rect(fg.hwnd)
        if rect is None:
            x, y = self._get_mouse().position()
            rect = (x - 400, y - 300, 800, 600)
        self._on_grid(rect)
        return ActionResult(True, "Say a number to zoom in, then “click”")

    def use_targets(self, hwnd: Optional[int], targets) -> None:
        """Set the element list that ``click_element`` ids refer to."""

        self._ai_targets = (hwnd, list(targets))

    def _do_click_element(self, a) -> ActionResult:
        hwnd, targets = self._ai_targets
        if hwnd is None or not 1 <= a["id"] <= len(targets):
            return ActionResult(False, f"There is no element {a['id']}")
        return self.activate_target(hwnd, targets[a["id"] - 1], a.get("button", "left"))

    def _do_labels(self, a) -> ActionResult:
        if self._on_labels is None:
            return ActionResult(False, "Number labels aren't available")
        hwnd, targets = self.label_targets()
        if hwnd is None:
            return ActionResult(False, "No window is in front")
        if not targets:
            return ActionResult(False, "Nothing to click in this window")
        self._on_labels(hwnd, targets, True)
        return ActionResult(True, f"{len(targets)} labels: say a number")

    def label_targets(self):
        """``(hwnd, targets)`` for the window in front (``(None, [])`` if none)."""

        fg = self._require_windows().foreground()
        if fg is None:
            return None, []
        return fg.hwnd, self._get_elements().list_targets(fg.hwnd)

    def activate_target(
        self, hwnd: int, target: Any, button: str = "left"
    ) -> ActionResult:
        """Click one labelled target (or focus it, for a text box)."""

        how = self._get_elements().activate_target(hwnd, target, button)
        _LOGGER.info("Label click: %r (%s) via %s", target.name, target.kind, how)
        name = f"“{target.name[:40]}”" if target.name else "it"
        if target.kind == "input" and button == "left":
            return ActionResult(True, f"Focused {name}")
        return ActionResult(True, f"{_CLICK_LABELS[button]} {name}")

    def _do_launch_app(self, a) -> ActionResult:
        name = a["name"]
        found = self._get_apps().find(name)
        if len(found) == 1:
            return self.launch_app_entry(found[0])
        if found:
            self._on_choice(found[:9], name, "app", self.launch_app_entry)
            return ActionResult(True, f"{len(found)} apps match “{name}”")
        site = _site_url(name)
        if site is not None:
            self._launcher(site)
            return ActionResult(True, f"Opening {site}")
        return ActionResult(False, f"No app called “{name}”", unmatched=True)

    def launch_app_entry(self, app: Any) -> ActionResult:
        """Launch one app from :class:`~.apps.StartApps` (also the picker's pick)."""

        self._get_apps().launch(app)
        return ActionResult(True, f"Opening {app.name}")

    def _do_close_app(self, a) -> ActionResult:
        windows = self._require_windows()
        match = find_window(windows.list_windows(), a["name"])
        if match is None:
            return ActionResult(
                False, f"No window matches “{a['name']}”", unmatched=True
            )
        windows.close(match.hwnd)
        return ActionResult(True, f"Closing {match.title[:40]}")

    def _do_list_windows(self, a) -> ActionResult:
        from .modes import ListItem

        items = [
            ListItem(w.title, w.process, w.hwnd)
            for w in self._require_windows().list_windows()
            if w.title.strip()
        ][:9]
        if not items:
            return ActionResult(False, "No windows are open")
        self._on_choice(items, "", "window", self.focus_list_item)
        return ActionResult(True, "Say a window's number")

    def focus_list_item(self, item: Any) -> ActionResult:
        if not self._require_windows().focus(item.value):
            return ActionResult(False, f"Couldn't focus “{item.label[:40]}”")
        return ActionResult(True, f"Focused {item.label[:40]}")

    def _do_window(self, a) -> ActionResult:
        op = a["op"]
        if op in _WINDOW_KEYS:
            self.press(_WINDOW_KEYS[op])
            return ActionResult(True, _WINDOW_LABELS[op])
        windows = self._require_windows()
        fg = windows.foreground()
        if fg is None:
            return ActionResult(False, "No window is in front")
        getattr(windows, op)(fg.hwnd)
        return ActionResult(True, f"{_WINDOW_LABELS[op]}: {fg.title[:30]}")

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
        repeat = a.get("repeat", 1)
        for _ in range(repeat):
            kb.press(key)
            kb.release(key)
        label = a["key"].replace("_", " ").capitalize()
        if a["key"].startswith("volume_"):
            label += f" {2 * repeat}%"  # each volume key press is 2% in Windows
        return ActionResult(True, label)

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
            # Tab by position: focus the app's front window, then its
            # shortcut. "any" is ambiguous here, so prefer the browser
            # (the usual place for numbered tabs) and fall back to the
            # terminal.
            app = a["app"]
            if app == "any":
                app = (
                    "browser"
                    if self._front_window(windows, BROWSER_CLASSES)
                    else "terminal"
                )
            classes = _app_classes(app)
            target = self._front_window(windows, classes)
            if target is None:
                return ActionResult(False, f"No {app} window is open")
            windows.focus(target.hwnd)
            combo = (
                f"<ctrl>+<alt>+{number}" if app == "terminal" else f"<ctrl>+{number}"
            )
            self.press(combo)
            return ActionResult(True, f"Tab {number}")
        candidates = self._get_tabs().find(a["app"], a["tab"])
        if not candidates:
            return ActionResult(False, f"No {a['app']} tab matches “{a['tab']}”")
        if len(candidates) == 1:
            match = self._select_candidate(candidates[0])
            if match is None:
                return ActionResult(False, f"No {a['app']} tab matches “{a['tab']}”")
            windows.focus(match.hwnd)
            return ActionResult(True, f"Tab: {match.tab_name[:40]}")
        # Several tabs match: do not guess. Hand the list over so the
        # user can pick the one they meant.
        self._on_ambiguous(candidates, a["tab"])
        return ActionResult(True, f"{len(candidates)} tabs match “{a['tab']}”")

    def _front_window(self, windows, classes):
        """The front-most window whose class is in ``classes``."""

        return next(
            (w for w in windows.list_windows() if w.class_name in classes), None
        )

    def select_tab_candidate(self, candidate: Any) -> ActionResult:
        """Select one tab out of an ambiguous match and focus its window."""

        match = self._select_candidate(candidate)
        if match is None:
            return ActionResult(False, "That tab is gone")
        self._require_windows().focus(match.hwnd)
        return ActionResult(True, f"Tab: {match.tab_name[:40]}")

    def _select_candidate(self, candidate: Any):
        return self._get_tabs().select_candidate(candidate)

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

    def _do_ask(self, a) -> ActionResult:
        assistant = self._assistant_getter()
        if assistant is None:
            return ActionResult(
                False, "The AI assistant isn't set up (see the AI Assistant tab)"
            )
        if not assistant.ask(a["question"]):
            return ActionResult(False, "The assistant is busy, try again in a moment")
        return ActionResult(True, "Asking the assistant…")

    def _do_ask_listen(self, a) -> ActionResult:
        assistant = self._assistant_getter()
        if assistant is None:
            return ActionResult(
                False, "The AI assistant isn't set up (see the AI Assistant tab)"
            )
        if not assistant.start_listening():
            return ActionResult(False, "The assistant is already listening or busy")
        return ActionResult(True, "Listening for your question…")

    def _do_wait(self, a) -> ActionResult:
        self._sleep(a["ms"] / 1000.0)
        return ActionResult(True, "")

    def _do_click(self, a) -> ActionResult:
        fg = self._require_windows().foreground()
        if fg is None:
            return ActionResult(False, "No window is in front")
        queries = a.get("any") or [a["target"]]
        button = a.get("button", "left")
        hit = self._get_elements().click(
            fg.hwnd,
            queries,
            a.get("exact", False),
            button=button,
            # Same-named buttons ("Reply" under every comment) get labels
            # to pick from, unless this is a fixed-label command (skip ad).
            allow_ties=self._on_labels is not None and "any" not in a,
        )
        if hit is None:
            return ActionResult(
                False, a.get("missing") or f"Nothing called “{queries[0]}” here"
            )
        if hit.ties:
            self._on_labels(fg.hwnd, list(hit.ties), False)
            return ActionResult(
                True, f"{len(hit.ties)} × “{hit.name[:30]}”: say a number"
            )
        verb = (
            "Focused"
            if hit.kind == "input" and button == "left"
            else _CLICK_LABELS[button]
        )
        return ActionResult(True, f"{verb} “{hit.name[:40]}”")

    def _do_volume(self, a) -> ActionResult:
        from .audio import parse_percent

        volume = self._get_volume()
        if "mute" in a:
            volume.set_mute(a["mute"])
            return ActionResult(True, "Muted" if a["mute"] else "Unmuted")
        level = a["level"]
        percent = level if isinstance(level, int) else parse_percent(level)
        if percent is None:
            return ActionResult(False, f"Say a volume from 0 to 100, not “{level}”")
        volume.set_level(percent)
        return ActionResult(True, f"Volume {percent}%")

    def _do_scroll(self, a) -> ActionResult:
        fg = self._require_windows().foreground()
        if fg is None:
            return ActionResult(False, "No window is in front")
        direction, amount = a["direction"], a.get("amount", "small")
        elements = self._get_elements()
        if not elements.scroll(fg.hwnd, direction, amount):
            # The window doesn't support UI Automation scrolling (e.g. Firefox).
            if direction in ("top", "bottom"):
                self.press("<ctrl>+<home>" if direction == "top" else "<ctrl>+<end>")
            elif not elements.wheel(fg.hwnd, direction, amount):
                return ActionResult(False, "Couldn't scroll this window")
        if direction in ("top", "bottom"):
            return ActionResult(True, f"Scrolled to {direction}")
        return ActionResult(True, f"Scrolled {direction}")

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
        # The tab backend is used from the voice thread and from the GUI
        # thread (the mouse pick in the tab picker), so the lazy init
        # needs a lock.
        with self._tabs_lock:
            if self._tabs is None:
                if self._tabs_factory is not None:
                    self._tabs = self._tabs_factory()
                else:
                    from .tabs import UiaTabs

                    self._tabs = UiaTabs()
            return self._tabs

    def _get_volume(self):
        if self._volume is None:
            if self._volume_factory is not None:
                self._volume = self._volume_factory()
            else:
                from .audio import SystemVolume

                self._volume = SystemVolume()
        return self._volume

    def _get_apps(self):
        with self._apps_lock:  # the picker's mouse pick runs on the GUI thread
            if self._apps is None:
                if self._apps_factory is not None:
                    self._apps = self._apps_factory()
                else:
                    from .apps import StartApps

                    self._apps = StartApps()
            return self._apps

    def _get_mouse(self):
        if self._mouse is None:
            if self._mouse_factory is not None:
                self._mouse = self._mouse_factory()
            else:
                from .mouse import Win32Mouse

                self._mouse = Win32Mouse()
        return self._mouse

    def _get_elements(self):
        if self._elements is None:
            if self._elements_factory is not None:
                self._elements = self._elements_factory()
            else:
                from .elements import UiaElements

                self._elements = UiaElements()
        return self._elements


def _count_of(value: Any) -> Optional[int]:
    """A repeat count from an int or spoken text; ``None`` if unreadable.

    A placeholder left unfilled (the phrase had no ``{count}`` slot, as
    in "mouse up" versus "mouse up 3") counts as once.
    """

    if isinstance(value, int):
        return value
    if value.startswith("{") and value.endswith("}"):
        return 1
    from .spoken_keys import parse_count

    return parse_count(value)


_SITE_ENDINGS = ("com", "org", "net", "io", "tv", "dev", "app", "ai", "gg", "co", "mx")


def _site_url(name: str) -> Optional[str]:
    """``"github com"`` / ``"github dot com"`` → ``"https://github.com"``."""

    words = [w for w in normalize(name).split() if w != "dot"]
    if len(words) < 2 or words[-1] not in _SITE_ENDINGS:
        return None
    if not all(w.isalnum() for w in words):
        return None
    return "https://" + "".join(words[:-1]) + "." + words[-1]


NUDGE_PIXELS = 40
_NUDGE = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
_CLICK_LABELS = {
    "left": "Clicked",
    "right": "Right-clicked",
    "middle": "Middle-clicked",
    "double": "Double-clicked",
}
_WINDOW_KEYS = {
    "snap_left": "<cmd>+<left>",
    "snap_right": "<cmd>+<right>",
    "next_monitor": "<cmd>+<shift>+<right>",
}
_WINDOW_LABELS = {
    "minimize": "Minimized",
    "maximize": "Maximized",
    "restore": "Restored",
    "close": "Closing",
    "snap_left": "Snapped left",
    "snap_right": "Snapped right",
    "next_monitor": "Moved to the next monitor",
}


def _app_classes(app: str):
    from .windows import BROWSER_CLASSES, TERMINAL_CLASSES

    return TERMINAL_CLASSES if app == "terminal" else BROWSER_CLASSES


def _default_keyboard():
    from pynput.keyboard import Controller  # type: ignore[import-not-found]

    return Controller()


def _default_launcher(target: str) -> None:
    os.startfile(target)  # type: ignore[attr-defined]  # Windows only
