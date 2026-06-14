"""Desktop configurator UI for managing shortcuts, sounds, and overlays.

The package is organised into:

* :mod:`.constants` — shared UI constants (model lists, dimensions).
* :mod:`.widgets` — small reusable Qt widgets (hotkey capture, position
  picker).
* :mod:`.sections` — per-section QWidget subclasses (STT settings,
  shortcut details).
* :mod:`.window` — the main :class:`ConfiguratorWindow` that composes
  the sections and owns the shortcut list.

Backward-compatible re-exports of :class:`ConfiguratorWindow` and
:func:`run_configurator` are provided here so existing call sites
(``from .configurator import ConfiguratorWindow``) keep working.
"""

from .window import ConfiguratorWindow
from PySide6.QtWidgets import QApplication


def run_configurator() -> None:
    """Launch the configurator window."""
    app = QApplication.instance() or QApplication([])
    window = ConfiguratorWindow()
    window.show()
    app.exec()


__all__ = ["ConfiguratorWindow", "run_configurator"]
