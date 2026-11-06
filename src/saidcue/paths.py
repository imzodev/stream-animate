"""Filesystem locations that differ between running from source and a
packaged (PyInstaller) install.

Two kinds of location, resolved differently depending on how the app is
running:

* **Bundled read-only resources** (``assets/``, ``config/schema.json``,
  ``config/shortcuts.sample.json``) ship alongside the app. In dev mode
  that's the repo root; in a frozen build it's wherever PyInstaller put
  the data (``sys._MEIPASS`` for a onefile build, or the folder next to
  the executable for onedir).
* **The user's own writable config** (``shortcuts.json``) can't live next
  to a frozen build — an installed app usually sits under
  ``Program Files``, which an ordinary user can't write to. Once frozen,
  it moves to a per-user directory instead (``%APPDATA%\\Saidcue`` on
  Windows, ``~/.config/saidcue`` on Linux).

Dev mode (running ``python main.py`` from a checkout) is unaffected by
any of this: both kinds of location stay relative to the repo root,
exactly as before this module existed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running from a PyInstaller build, not from source."""

    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path:
    """The checkout root when running from source."""

    return Path(__file__).resolve().parents[2]


def bundle_root() -> Path:
    """Where bundled read-only resources (assets/, config/*.json) live."""

    if not is_frozen():
        return repo_root()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(sys.executable).resolve().parent


def user_data_dir() -> Path:
    """Per-user writable directory for Saidcue's own config."""

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "Saidcue"
