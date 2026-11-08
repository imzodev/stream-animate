"""Switch to a tab by name in Windows Terminal or a browser.

Uses Windows UI Automation: Windows Terminal, Chrome, Edge and Firefox all
expose their tab strips as ``TabItem`` elements that support
``SelectionItemPattern.Select()``. Must run on a thread that has COM
initialized (see :class:`~saidcue.voice_commands.service.VoiceCommandService`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from .parser import normalize
from .windows import BROWSER_CLASSES, TERMINAL_CLASSES

_LOGGER = logging.getLogger(__name__)

_APP_CLASSES = {"terminal": TERMINAL_CLASSES, "browser": BROWSER_CLASSES}
_MAX_DEPTH = 12


def app_kinds(app: str) -> Tuple[str, ...]:
    """The specific apps to search for ``app``.

    ``"any"`` means every app that has tabs: the browsers and Windows
    Terminal. The results keep the kind they came from, so a candidate
    can be re-resolved later.
    """

    if app == "any":
        return ("browser", "terminal")
    return (app,)


# Match ranks, best first.
_EXACT, _PREFIX, _SUBSTRING = 0, 1, 2

# Browsers append their own name to the page title. Chrome's tab is
# named "Some video - YouTube - Google Chrome"; the trailing app name
# is noise when you are picking between tabs.
_BROWSER_SUFFIXES = (
    " - google chrome",
    " - microsoft edge",
    " - mozilla firefox",
    " - firefox",
    " - chrome",
    " - edge",
)


@dataclass(frozen=True)
class TabMatch:
    hwnd: int
    window_title: str
    tab_name: str


@dataclass(frozen=True)
class TabCandidate:
    """A tab that matched a query, with enough context to select it later.

    ``tab_index`` is the tab's position inside its own window, so the
    candidate can be re-resolved even when several tabs share a name
    (four "YouTube" tabs, four "Windows PowerShell" tabs).
    """

    app: str
    hwnd: int
    window_title: str
    tab_name: str
    tab_index: int
    tab_count: int
    rank: int


def clean_tab_name(name: str) -> str:
    """Strip the browser's trailing app name from a tab title."""

    lowered = name.lower()
    for suffix in _BROWSER_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)].strip()
    return name


def rank_tabs(names: Sequence[str], query: str) -> List[Tuple[int, int]]:
    """``(rank, index)`` for every tab matching ``query``, best match first.

    Exact (normalized) name beats prefix beats substring; ties keep the
    original order. All comparisons ignore case, accents and punctuation.
    """

    q = normalize(query)
    if not q:
        return []
    ranked: List[Tuple[int, int]] = []
    for i, name in enumerate(names):
        n = normalize(name)
        if n == q:
            ranked.append((_EXACT, i))
        elif n.startswith(q):
            ranked.append((_PREFIX, i))
        elif q in n:
            ranked.append((_SUBSTRING, i))
    ranked.sort()
    return ranked


def best_tab(names: List[str], query: str) -> Optional[int]:
    """Index of the tab whose name best matches ``query``, else ``None``."""

    ranked = rank_tabs(names, query)
    return ranked[0][1] if ranked else None


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

    def _windows(self, app: str) -> List[Tuple[str, Any]]:
        """``(kind, window)`` for every window of ``app``, front-most first."""

        pairs: List[Tuple[str, Any]] = []
        for kind in app_kinds(app):
            classes = _APP_CLASSES[kind]
            for window in self._auto.GetRootControl().GetChildren():
                if window.ClassName in classes:
                    pairs.append((kind, window))
        return pairs

    def list_tabs(self, app: str) -> List[str]:
        """Names of all tabs across every window of ``app``."""

        with self._auto.UIAutomationInitializerInThread():
            return self._list_tabs(app)

    def _list_tabs(self, app: str) -> List[str]:
        names: List[str] = []
        for _kind, window in self._windows(app):
            names.extend(i.Name or "" for i in self._tab_items(window))
        return names

    def find(self, app: str, query: str) -> List[TabCandidate]:
        """Every tab of ``app`` whose name matches ``query``, best first.

        Unlike :meth:`select` this does not pick one: the caller decides
        what to do when the match is ambiguous.
        """

        with self._auto.UIAutomationInitializerInThread():
            return self._find(app, query)

    def _find(self, app: str, query: str) -> List[TabCandidate]:
        found: List[Tuple[int, int, int, TabCandidate]] = []
        for window_order, (kind, window) in enumerate(self._windows(app)):
            items = self._tab_items(window)
            names = [i.Name or "" for i in items]
            hwnd = int(window.NativeWindowHandle)
            title = window.Name or ""
            for rank, idx in rank_tabs(names, query):
                found.append(
                    (
                        rank,
                        window_order,
                        idx,
                        TabCandidate(
                            app=kind,
                            hwnd=hwnd,
                            window_title=title,
                            tab_name=names[idx],
                            tab_index=idx,
                            tab_count=len(items),
                            rank=rank,
                        ),
                    )
                )
        found.sort(key=lambda entry: entry[:3])
        return [entry[3] for entry in found]

    def select(self, app: str, query: str) -> Optional[TabMatch]:
        """Select the best-matching tab across all windows of ``app``.

        Returns the match (so the caller can focus its window) or ``None``.
        """

        # UI Automation is COM: initialize it on whatever thread calls us.
        with self._auto.UIAutomationInitializerInThread():
            return self._select(app, query)

    def _select(self, app: str, query: str) -> Optional[TabMatch]:
        candidates = self._find(app, query)
        if not candidates:
            _LOGGER.info("No %s tab matches %r", app, query)
            return None
        return self._select_candidate(candidates[0])

    def select_candidate(self, candidate: TabCandidate) -> Optional[TabMatch]:
        """Select one specific tab, re-resolving it first.

        Tabs move: the user may have closed one since the list was
        built. The recorded index is verified against the tab's name and
        the name is used as a fallback.
        """

        with self._auto.UIAutomationInitializerInThread():
            return self._select_candidate(candidate)

    def _select_candidate(self, candidate: TabCandidate) -> Optional[TabMatch]:
        classes = _APP_CLASSES[candidate.app]
        for window in self._auto.GetRootControl().GetChildren():
            if window.ClassName not in classes:
                continue
            if int(window.NativeWindowHandle) != candidate.hwnd:
                continue
            items = self._tab_items(window)
            names = [i.Name or "" for i in items]
            idx = candidate.tab_index
            if idx >= len(items) or names[idx] != candidate.tab_name:
                same = [i for i, n in enumerate(names) if n == candidate.tab_name]
                if not same:
                    _LOGGER.info("Tab %r is gone", candidate.tab_name)
                    return None
                idx = same[0]
            items[idx].GetSelectionItemPattern().Select()
            return TabMatch(candidate.hwnd, window.Name or "", names[idx])
        return None
