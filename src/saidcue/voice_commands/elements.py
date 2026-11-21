"""Click buttons, focus inputs and scroll inside the foreground window.

Uses Windows UI Automation, which exposes both native controls and web page
content (Chrome, Edge and Firefox publish the page's buttons, links and
inputs with their labels). Nothing here moves the mouse except the last-resort
fallbacks, which put the cursor back where it was.

Must run on a thread with COM initialized; every public method wraps itself
in ``UIAutomationInitializerInThread``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .parser import normalize

_LOGGER = logging.getLogger(__name__)

CLICKABLE_TYPES = frozenset({
    "ButtonControl", "HyperlinkControl", "MenuItemControl", "CheckBoxControl",
    "RadioButtonControl", "SplitButtonControl", "TabItemControl",
    "ListItemControl", "TreeItemControl",
})  # fmt: skip
INPUT_TYPES = frozenset({"EditControl", "ComboBoxControl"})
# Browser/window UI outside the page that is never clicked by voice: "click
# close" must not close the window, and per-tab buttons exist once per tab,
# so the first match would hit the wrong tab.
_FRAME_BUTTONS = frozenset({
    "close", "minimize", "maximize", "restore", "close tab", "mute tab", "unmute tab",
})  # fmt: skip

_TREE_SUBTREE = 7  # TreeScope_Subtree (element + descendants)
_TREE_DESCENDANTS = 4  # TreeScope_Descendants

# How far each amount scrolls with UI Automation increments / wheel notches.
SCROLL_STEPS = {"small": 5, "page": 1, "large": 3}
WHEEL_NOTCHES = {"small": 3, "page": 8, "large": 20}


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str  # "button" or "input"
    in_page: bool  # inside a document (web page / editor)
    offscreen: bool = False


@dataclass(frozen=True)
class Target:
    """An element on screen that the number labels can point at.

    ``rect`` is ``(left, top, width, height)`` in physical pixels.
    ``runtime_id`` finds the same element again in a later scan (COM
    elements are not kept across scans).
    """

    name: str
    kind: str  # "button" or "input"
    rect: Tuple[int, int, int, int]
    runtime_id: Tuple[int, ...] = ()
    in_page: bool = False

    @property
    def center(self) -> Tuple[int, int]:
        left, top, w, h = self.rect
        return left + w // 2, top + h // 2


@dataclass(frozen=True)
class ElementHit:
    name: str
    kind: str  # "button" or "input"
    # Several on-screen elements matched equally well ("click reply" with
    # a Reply button under every comment): nothing was clicked, and these
    # are the ones to choose from.
    ties: Tuple[Target, ...] = ()


# Labels further than this outside the window are dropped (stale rects).
MAX_TARGETS = 300


def best_element(
    candidates: Sequence[Candidate], queries: Sequence[str], exact: bool = False
) -> Optional[int]:
    """Index of the candidate that best matches any of ``queries``.

    Whole-word matching on normalized names: exact name, then name starting
    with the query ("pause" → "Pause (k)"), then name containing the query
    ("regida" → "Subscribe to FUERZA REGIDA."), then — the other way round —
    the query containing the whole name ("the search box" → "Search"), so
    saying more than the label still finds it. With ``exact`` only exact
    names count. Ties prefer on-screen elements, then elements inside the
    page over browser UI, then document order.
    """

    scored = _score(candidates, queries, exact)
    return min(scored)[3] if scored else None


def tied_elements(
    candidates: Sequence[Candidate], queries: Sequence[str], exact: bool = False
) -> List[int]:
    """Indexes of every candidate tied with the best match, in document order.

    A tie means the same match rank, on screen, and the same place (page
    or browser UI) — e.g. the "Reply" button under every comment.
    """

    scored = _score(candidates, queries, exact)
    if not scored:
        return []
    best = min(scored)[:3]
    return [i for *key, i in sorted(scored, key=lambda e: e[3]) if tuple(key) == best]


def _score(
    candidates: Sequence[Candidate], queries: Sequence[str], exact: bool
) -> List[Tuple[int, bool, bool, int]]:
    qs = [q for q in (normalize(q) for q in queries) if q]
    scored: List[Tuple[int, bool, bool, int]] = []
    for i, c in enumerate(candidates):
        n = normalize(c.name)
        if not n or (not c.in_page and n in _FRAME_BUTTONS):
            continue
        rank = None
        for q in qs:
            if n == q:
                r = 0
            elif exact:
                continue
            elif n.startswith(q + " "):
                r = 1
            elif f" {q} " in f" {n} ":
                r = 2
            elif f" {n} " in f" {q} ":
                r = 3
            else:
                continue
            rank = r if rank is None else min(rank, r)
        if rank is not None:
            scored.append((rank, c.offscreen, not c.in_page, i))
    return scored


class UiaElements:
    """Real UI Automation backend. Tests inject a fake with the same API."""

    def __init__(self) -> None:
        import uiautomation as auto  # type: ignore[import-not-found]

        self._auto = auto

    # -- clicking -------------------------------------------------------------

    def click(
        self,
        hwnd: int,
        queries: Sequence[str],
        exact: bool = False,
        button: str = "left",
        allow_ties: bool = False,
    ) -> Optional[ElementHit]:
        """Press the best-matching button/link (or focus the input) in ``hwnd``.

        ``button`` "right", "middle" or "double" does a real mouse click on
        the element's centre: UI Automation has no pattern for those.

        With ``allow_ties``, several equally good on-screen matches are not
        clicked: the hit carries them in ``ties`` so the user can pick.
        """

        with self._auto.UIAutomationInitializerInThread():
            found = self._scan(hwnd)
            cands = [c for c, _ in found]
            if allow_ties:
                tied = tied_elements(cands, queries, exact)
                if len(tied) > 1 and not cands[tied[0]].offscreen:
                    targets = tuple(
                        t
                        for t in (self._target(cands[i], found[i][1]) for i in tied)
                        if t is not None
                    )
                    if len(targets) > 1:
                        return ElementHit(
                            cands[tied[0]].name, cands[tied[0]].kind, targets
                        )
            idx = best_element(cands, queries, exact)
            if idx is None:
                _LOGGER.info(
                    "No element matches %s (%d candidates, e.g. %s)",
                    list(queries),
                    len(found),
                    [c.name[:30] for c, _ in found[:15]],
                )
                return None
            cand, element = found[idx]
            control = self._auto.Control.CreateControlFromElement(element)
            how = self._activate(control, cand.kind, button)
            _LOGGER.info("Voice click: %r (%s) via %s", cand.name, cand.kind, how)
            return ElementHit(cand.name, cand.kind)

    def _uia(self):
        from uiautomation import uiautomation as core  # type: ignore[import-not-found]

        return core._AutomationClient.instance().IUIAutomation

    def _type_condition(self, uia, type_names):
        auto = self._auto
        cond = None
        for name in sorted(type_names):
            c = uia.CreatePropertyCondition(
                auto.PropertyId.ControlTypeProperty, getattr(auto.ControlType, name)
            )
            cond = c if cond is None else uia.CreateOrCondition(cond, c)
        return cond

    # -- number labels ----------------------------------------------------------

    def list_targets(self, hwnd: int) -> List[Target]:
        """Every visible clickable and input in ``hwnd`` (and any open popup
        menu), named or not, for the number labels. Document order."""

        with self._auto.UIAutomationInitializerInThread():
            found = self._scan(hwnd, named_only=False)
            out: List[Target] = []
            seen = set()
            for cand, element in found:
                if cand.offscreen:
                    continue
                target = self._target(cand, element)
                if target is None or target.rect in seen:
                    continue
                seen.add(target.rect)  # nested wrappers share one rectangle
                out.append(target)
                if len(out) >= MAX_TARGETS:
                    break
            return out

    def activate_target(self, hwnd: int, target: Target, button: str = "left") -> str:
        """Click (or focus) a target from :meth:`list_targets`.

        The element is found again by its runtime id, so a page that
        changed since the labels were drawn doesn't get a stale object.
        If it is gone, the click lands on the centre of where it was.
        """

        auto = self._auto
        with auto.UIAutomationInitializerInThread():
            for cand, element in self._scan(hwnd, named_only=False):
                if target.runtime_id and self._runtime_id(element) == target.runtime_id:
                    control = auto.Control.CreateControlFromElement(element)
                    return self._activate(control, cand.kind, button)
            x, y = target.center
            click = {
                "left": auto.Click,
                "right": auto.RightClick,
                "middle": auto.MiddleClick,
            }.get(button)
            if click is None:  # double
                auto.Click(x, y, waitTime=0.02)
                auto.Click(x, y, waitTime=0.05)
            else:
                click(x, y, waitTime=0.05)
            return "mouse click at its last position"

    def _runtime_id(self, element) -> Tuple[int, ...]:
        try:
            return tuple(element.GetRuntimeId())
        except Exception:  # noqa: BLE001 - element went away
            return ()

    def _target(self, cand: Candidate, element) -> Optional[Target]:
        try:
            r = element.CachedBoundingRectangle
            rect = (
                int(r.left),
                int(r.top),
                int(r.right - r.left),
                int(r.bottom - r.top),
            )
        except Exception:  # noqa: BLE001 - no cached rectangle
            return None
        if rect[2] <= 1 or rect[3] <= 1:
            return None
        return Target(
            cand.name, cand.kind, rect, self._runtime_id(element), cand.in_page
        )

    def _popup_roots(self, uia):
        """Open popup menus (context menus live in their own top-level window)."""

        auto = self._auto
        desktop = uia.GetRootElement()
        cond = uia.CreateOrCondition(
            self._type_condition(uia, {"MenuControl"}),
            uia.CreatePropertyCondition(auto.PropertyId.ClassNameProperty, "#32768"),
        )
        found = desktop.FindAll(2, cond)  # TreeScope_Children
        return [found.GetElement(i) for i in range(found.Length if found else 0)]

    def _scan(
        self, hwnd: int, named_only: bool = True
    ) -> List[Tuple[Candidate, object]]:
        """Enabled clickables and inputs in document order (named ones
        only, unless ``named_only`` is off), plus any open popup menu.

        One batched ``FindAll`` with cached properties: ~0.1 s for a YouTube
        page, versus seconds when walking the tree one call per element.
        """

        auto = self._auto
        uia = self._uia()
        root = uia.ElementFromHandle(hwnd)
        if not root:
            return []
        ids = {getattr(auto.ControlType, n): n for n in CLICKABLE_TYPES | INPUT_TYPES}
        cond = self._type_condition(uia, CLICKABLE_TYPES | INPUT_TYPES)
        cache = uia.CreateCacheRequest()
        for prop in (
            auto.PropertyId.NameProperty,
            auto.PropertyId.ControlTypeProperty,
            auto.PropertyId.IsEnabledProperty,
            auto.PropertyId.IsOffscreenProperty,
            auto.PropertyId.BoundingRectangleProperty,
        ):
            cache.AddProperty(prop)

        # Which candidates sit inside a document (the web page / editor area)?
        in_page = set()
        docs = root.FindAll(
            _TREE_DESCENDANTS, self._type_condition(uia, {"DocumentControl"})
        )
        for i in range(docs.Length if docs else 0):
            sub = docs.GetElement(i).FindAll(_TREE_DESCENDANTS, cond)
            for j in range(sub.Length if sub else 0):
                in_page.add(tuple(sub.GetElement(j).GetRuntimeId()))

        out: List[Tuple[Candidate, object]] = []
        roots = [root]
        try:
            roots += self._popup_roots(uia)
        except Exception:  # noqa: BLE001 - popups are a bonus
            _LOGGER.debug("Could not list popup menus", exc_info=True)
        for scan_root in roots:
            found = scan_root.FindAllBuildCache(_TREE_DESCENDANTS, cond, cache)
            for i in range(found.Length if found else 0):
                el = found.GetElement(i)
                name = (el.CachedName or "").strip()
                if (named_only and not name) or not el.CachedIsEnabled:
                    continue
                kind = (
                    "input"
                    if ids.get(el.CachedControlType) in INPUT_TYPES
                    else "button"
                )
                page = bool(in_page) and self._runtime_id(el) in in_page
                out.append(
                    (Candidate(name, kind, page, bool(el.CachedIsOffscreen)), el)
                )
        return out

    def _activate(self, control, kind: str, button: str = "left") -> str:
        auto = self._auto
        if button != "left":
            # The cursor stays there: a context menu opens next to it.
            if button == "right":
                control.RightClick(simulateMove=False, waitTime=0.05)
            elif button == "middle":
                control.MiddleClick(simulateMove=False, waitTime=0.05)
            else:
                control.DoubleClick(simulateMove=False, waitTime=0.05)
            return f"{button} click"
        if kind == "input":
            control.SetFocus()
            return "focus"
        attempts = (
            (auto.PatternId.InvokePattern, lambda p: p.Invoke(), "invoke"),
            (auto.PatternId.TogglePattern, lambda p: p.Toggle(), "toggle"),
            (auto.PatternId.SelectionItemPattern, lambda p: p.Select(), "select"),
            (
                auto.PatternId.LegacyIAccessiblePattern,
                lambda p: p.DoDefaultAction(),
                "default action",
            ),
        )
        for pattern_id, act, how in attempts:
            pattern = control.GetPattern(pattern_id)
            if pattern is None:
                continue
            try:
                act(pattern)
                return how
            except Exception as exc:  # noqa: BLE001 - try the next way
                _LOGGER.debug("%s failed on %r: %s", how, control.Name, exc)
        # Last resort: a real click on its centre, then put the cursor back.
        x, y = auto.GetCursorPos()
        try:
            control.Click(simulateMove=False, waitTime=0.05)
        finally:
            auto.SetCursorPos(x, y)
        return "mouse click"

    # -- scrolling ------------------------------------------------------------

    def scroll(self, hwnd: int, direction: str, amount: str = "small") -> bool:
        """Scroll the main scrollable area of ``hwnd``.

        Returns ``False`` when nothing in the window supports UI Automation
        scrolling (e.g. Firefox pages); the caller then falls back to
        :meth:`wheel` or keys.
        """

        auto = self._auto
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return False
            pattern = self._main_scroll_pattern(root)
            if pattern is None:
                return False
            before = pattern.VerticalScrollPercent
            # Already at the edge (some apps, e.g. Notepad, report >100%).
            at_edge = before <= 0.01 if direction in ("up", "top") else before >= 99.99
            if at_edge:
                return True
            self._scroll_with(pattern, direction, amount)
            # Some apps (e.g. Qt) accept the call but don't move. Check, and
            # let the caller fall back to the mouse wheel if nothing changed.
            for _ in range(4):
                if abs(pattern.VerticalScrollPercent - before) > 0.001:
                    return True
                time.sleep(0.05)
            _LOGGER.info("UI Automation scroll had no effect; falling back")
            return False

    def _scroll_with(self, pattern, direction: str, amount: str) -> None:
        auto = self._auto
        if direction in ("top", "bottom"):
            pattern.SetScrollPercent(-1, 0 if direction == "top" else 100)
            return
        size = "Small" if amount == "small" else "Large"
        way = "Increment" if direction == "down" else "Decrement"
        step = getattr(auto.ScrollAmount, size + way)
        for _ in range(SCROLL_STEPS[amount]):
            pattern.Scroll(auto.ScrollAmount.NoAmount, step)

    def _main_scroll_pattern(self, root):
        """The largest visible vertically-scrollable element's ScrollPattern."""

        auto = self._auto
        uia = self._uia()
        element = uia.ElementFromHandle(root.NativeWindowHandle)
        cond = uia.CreatePropertyCondition(
            auto.PropertyId.IsScrollPatternAvailableProperty, True
        )
        found = element.FindAll(_TREE_SUBTREE, cond)
        best, best_area = None, 0
        for i in range(found.Length if found else 0):
            control = auto.Control.CreateControlFromElement(found.GetElement(i))
            pattern = control.GetPattern(auto.PatternId.ScrollPattern)
            if (
                pattern is None
                or not pattern.VerticallyScrollable
                or control.IsOffscreen
            ):
                continue
            rect = control.BoundingRectangle
            area = max(rect.width(), 0) * max(rect.height(), 0)
            if area > best_area:
                best, best_area = pattern, area
        return best

    def wheel(self, hwnd: int, direction: str, amount: str = "small") -> bool:
        """Mouse-wheel over the window's centre, then restore the cursor."""

        auto = self._auto
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(hwnd)
            if root is None:
                return False
            rect = root.BoundingRectangle
            if rect.width() <= 0 or rect.height() <= 0:
                return False
            x, y = auto.GetCursorPos()
            try:
                auto.SetCursorPos(
                    rect.left + rect.width() // 2, rect.top + rect.height() // 2
                )
                notches = WHEEL_NOTCHES[amount]
                if direction == "down":
                    auto.WheelDown(notches, interval=0.01, waitTime=0.05)
                else:
                    auto.WheelUp(notches, interval=0.01, waitTime=0.05)
            finally:
                auto.SetCursorPos(x, y)
            return True
