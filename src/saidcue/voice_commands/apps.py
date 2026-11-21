"""Find and launch installed apps by name ("open spotify", "launch obs").

The list comes from ``Get-StartApps``: every Start-menu entry, both
classic desktop programs and Store apps, with the AppUserModelID that
``shell:AppsFolder`` launches. Reading it takes about a second, so it is
cached, loaded in the background at start-up and refreshed now and then.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from .parser import normalize

_LOGGER = logging.getLogger(__name__)

REFRESH_SECONDS = 600.0

# Start-menu entries nobody means when they say "open X".
_JUNK_WORDS = (
    "uninstall", "readme", "read me", "release notes", "license", "documentation",
    "help", "manual", "website", "on the web", "changelog", "what s new",
)  # fmt: skip

_POWERSHELL = (
    "powershell",
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
)


@dataclass(frozen=True)
class AppEntry:
    name: str
    app_id: str

    @property
    def label(self) -> str:  # shown in the picker
        return self.name

    @property
    def detail(self) -> str:
        return ""


def rank_apps(apps: Sequence[AppEntry], query: str) -> List[Tuple[int, int, int]]:
    """``(rank, extra_words, index)`` for every app matching ``query``, best first.

    Ranks: exact name, then the query as whole words inside the name
    ("chrome" → "Google Chrome"), then a name starting with the query,
    then any substring. Within a rank, names with fewer extra words come
    first, so "chrome" prefers "Google Chrome" over "Chrome Remote Desktop".
    """

    q = normalize(query)
    if not q:
        return []
    q_words = q.split()
    out: List[Tuple[int, int, int]] = []
    for i, app in enumerate(apps):
        n = normalize(app.name)
        if not n:
            continue
        if n == q:
            rank = 0
        elif f" {q} " in f" {n} ":
            rank = 1
        elif n.startswith(q):
            rank = 2
        elif q in n:
            rank = 3
        else:
            continue
        extra = max(len(n.split()) - len(q_words), 0)
        out.append((rank, extra, i))
    out.sort()
    return out


def best_apps(apps: Sequence[AppEntry], query: str) -> List[AppEntry]:
    """The app to launch (one item), the ones to pick from, or ``[]``.

    One item when a single app is clearly the best match; several when
    the top matches are equally good.
    """

    ranked = rank_apps(apps, query)
    if not ranked:
        return []
    top_rank, top_extra, _ = ranked[0]
    tied = [apps[i] for r, e, i in ranked if r == top_rank and e == top_extra]
    if len(tied) == 1:
        return tied
    # Same name twice (a per-user and a machine-wide shortcut): one app.
    names = {normalize(a.name) for a in tied}
    if len(names) == 1:
        return tied[:1]
    return tied


def _is_junk(name: str) -> bool:
    n = f" {normalize(name)} "
    return any(f" {w} " in n for w in _JUNK_WORDS)


def parse_start_apps(raw: str) -> List[AppEntry]:
    """Parse ``Get-StartApps | ConvertTo-Json`` output."""

    raw = (raw or "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):  # a single app is not wrapped in a list
        data = [data]
    out: List[AppEntry] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        app_id = str(item.get("AppID") or "").strip()
        if not name or not app_id or _is_junk(name):
            continue
        key = (normalize(name), app_id.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(AppEntry(name, app_id))
    return out


def _read_start_apps() -> str:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    done = subprocess.run(
        _POWERSHELL,
        capture_output=True,
        timeout=30,
        creationflags=flags,
    )
    return done.stdout.decode("utf-8", errors="replace")


def _default_launch(app_id: str) -> None:
    os.startfile("shell:AppsFolder\\" + app_id)  # type: ignore[attr-defined]


class StartApps:
    """Cached Start-menu app list. Tests inject ``reader`` and ``launcher``."""

    def __init__(
        self,
        *,
        reader: Callable[[], str] = _read_start_apps,
        launcher: Callable[[str], None] = _default_launch,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader = reader
        self._launcher = launcher
        self._clock = clock
        self._lock = threading.Lock()
        self._apps: Optional[List[AppEntry]] = None
        self._loaded_at = 0.0
        self._loading: Optional[threading.Thread] = None

    def preload(self) -> None:
        """Start loading the list in the background (non-blocking)."""

        with self._lock:
            if self._loading is not None and self._loading.is_alive():
                return
            thread = threading.Thread(
                target=self.refresh, name="start-apps", daemon=True
            )
            self._loading = thread
        thread.start()

    def refresh(self) -> List[AppEntry]:
        try:
            apps = parse_start_apps(self._reader())
        except Exception:  # noqa: BLE001 - keep the old list on failure
            _LOGGER.exception("Could not read the Start-menu app list")
            with self._lock:
                return list(self._apps or [])
        with self._lock:
            self._apps = apps
            self._loaded_at = self._clock()
        _LOGGER.info("Loaded %d Start-menu apps", len(apps))
        return apps

    def apps(self) -> List[AppEntry]:
        with self._lock:
            apps, age = self._apps, self._clock() - self._loaded_at
        if apps is None:
            return self.refresh()  # first use before the preload finished
        if age > REFRESH_SECONDS:
            self.preload()
        return list(apps)

    def find(self, query: str) -> List[AppEntry]:
        """See :func:`best_apps`. Refreshes once when nothing matches —
        the app may have been installed since the list was read."""

        found = best_apps(self.apps(), query)
        if not found:
            with self._lock:
                stale = self._clock() - self._loaded_at > 5.0
            if stale:
                found = best_apps(self.refresh(), query)
        return found

    def launch(self, app: AppEntry) -> None:
        self._launcher(app.app_id)
