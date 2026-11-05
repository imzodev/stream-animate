"""Switch to a tab by name in Windows Terminal or a browser.

Uses Windows UI Automation: Windows Terminal, Chrome, Edge and Firefox all
expose their tab strips as ``TabItem`` elements that support
``SelectionItemPattern.Select()``. Must run on a thread that has COM
initialized (see :class:`~stream_companion.voice_commands.service.VoiceCommandService`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .parser import normalize
from .windows import BROWSER_CLASSES, TERMINAL_CLASSES

_LOGGER = logging.getLogger(__name__)

_APP_CLASSES = {"terminal": TERMINAL_CLASSES, "browser": BROWSER_CLASSES}
_MAX_DEPTH = 12


@dataclass(frozen=True)
class TabMatch:
    hwnd: int
    window_title: str
    tab_name: str


def best_tab(names: List[str], query: str) -> Optional[int]:
    """Index of the tab whose name best matches ``query``, else ``None``.

    Exact (normalized) name beats prefix beats substring; ties go to the
    first tab. All comparisons ignore case, accents and punctuation.
    """

    q = normalize(query)
    if not q:
        return None
    ranked: List[Tuple[int, int]] = []
    for i, name in enumerate(names):
        n = normalize(name)
        if n == q:
            ranked.append((0, i))
        elif n.startswith(q):
            ranked.append((1, i))
        elif q in n:
            ranked.append((2, i))
    return min(ranked)[1] if ranked else None


class UiaTabs:
    """Real UI Automation backend. Tests inject a fake with the same API."""

    def __init__(self) -> None:
        import uiautomation as auto  # type: ignore[import-not-found]

        self._auto = auto

    def _tab_items(self, window) -> list:
        found = []

        def walk(control, depth):
            if depth > _MAX_DEPTH:
                return
            for child in control.GetChildren():
                if child.ControlTypeName == "TabItemControl":
                    found.append(child)
                else:
                    walk(child, depth + 1)

        walk(window, 0)
        return found

    def list_tabs(self, app: str) -> List[str]:
        """Names of all tabs across every window of ``app``."""

        with self._auto.UIAutomationInitializerInThread():
            names: List[str] = []
            for window in self._auto.GetRootControl().GetChildren():
                if window.ClassName in _APP_CLASSES[app]:
                    names.extend(i.Name or "" for i in self._tab_items(window))
            return names

    def select(self, app: str, query: str) -> Optional[TabMatch]:
        """Select the best-matching tab across all windows of ``app``.

        Returns the match (so the caller can focus its window) or ``None``.
        """

        # UI Automation is COM: initialize it on whatever thread calls us.
        with self._auto.UIAutomationInitializerInThread():
            return self._select(app, query)

    def _select(self, app: str, query: str) -> Optional[TabMatch]:
        classes = _APP_CLASSES[app]
        candidates = []  # (window, tab_item, name)
        for window in self._auto.GetRootControl().GetChildren():
            if window.ClassName not in classes:
                continue
            for item in self._tab_items(window):
                candidates.append((window, item, item.Name or ""))
        idx = best_tab([c[2] for c in candidates], query)
        if idx is None:
            _LOGGER.info(
                "No %s tab matches %r (tabs: %s)",
                app,
                query,
                [c[2] for c in candidates],
            )
            return None
        window, item, name = candidates[idx]
        item.GetSelectionItemPattern().Select()
        return TabMatch(int(window.NativeWindowHandle), window.Name, name)
