"""Application wiring for the Streaming Companion Tool MVP."""

from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional

from PySide6.QtCore import QMetaObject, Qt, QObject, Signal
from PySide6.QtWidgets import QApplication

from .hotkeys import HotkeyManager
from .models import OverlayConfig, Shortcut
from .overlay import OverlayWindow
from .sound import SoundPlayer
from . import registry

_LOGGER = logging.getLogger(__name__)


class ShortcutSignals(QObject):
    """Qt signals for thread-safe shortcut triggering."""

    triggered = Signal(Shortcut)


class Application:
    """Coordinates the MVP services for hotkey-triggered overlays and audio."""

    def __init__(
        self,
        shortcuts: Iterable[Shortcut],
        *,
        sound_player: Optional[SoundPlayer] = None,
        overlay_window: Optional[OverlayWindow] = None,
        hotkey_manager: Optional[HotkeyManager] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._shortcuts: List[Shortcut] = list(shortcuts)
        self._sound_player = sound_player or SoundPlayer()
        self._overlay_window = overlay_window or OverlayWindow()
        self._hotkey_manager = hotkey_manager or HotkeyManager()
        self._logger = logger or _LOGGER

        self._sound_ids: Dict[Shortcut, str] = {}
        self._registered = False

        # Create signals for thread-safe communication
        self._signals = ShortcutSignals()
        self._signals.triggered.connect(
            self._handle_shortcut_in_main_thread, Qt.ConnectionType.QueuedConnection
        )

    def start(self) -> None:
        """Preload assets, register shortcuts, and start the listener."""

        if self._registered:
            return

        self._preload_sounds()
        self._register_hotkeys()

        if not self._shortcuts:
            self._logger.info(
                "No shortcuts configured; application will idle until configuration changes"
            )

        started = self._hotkey_manager.start()
        if started:
            self._registered = True
            self._logger.info(
                "Application started with %d shortcuts", len(self._shortcuts)
            )

    def stop(self) -> None:
        """Stop listening and release audio resources."""

        if not self._registered:
            return
        self._hotkey_manager.stop()
        self._sound_player.shutdown()
        self._registered = False
        self._logger.info("Application stopped")

    def _preload_sounds(self) -> None:
        for shortcut in self._shortcuts:
            path = shortcut.sound_path
            if not path:
                continue
            sound_id = self._unique_sound_id(shortcut)
            success = self._sound_player.load(sound_id, path)
            if success:
                self._sound_ids[shortcut] = sound_id
            else:
                self._logger.warning("Failed to preload sound for %s", shortcut.hotkey)

    def _unique_sound_id(self, shortcut: Shortcut) -> str:
        base = shortcut.sound_id() or f"sound_{len(self._sound_ids) + 1}"
        candidate = base
        counter = 1
        existing = set(self._sound_ids.values())
        while candidate in existing:
            counter += 1
            candidate = f"{base}_{counter}"
        return candidate

    def _register_hotkeys(self) -> None:
        # Register direct hotkeys and collect chord suffix sequences
        from typing import Tuple
        seq_map: Dict[Tuple[str, ...], callable] = {}
        for shortcut in self._shortcuts:
            callback = lambda sc=shortcut: self._signals.triggered.emit(sc)
            if shortcut.hotkey:
                try:
                    self._hotkey_manager.register_hotkey(
                        shortcut.hotkey,
                        callback,
                    )
                except ValueError as exc:
                    self._logger.warning(
                        "Skipping duplicate or invalid hotkey '%s': %s",
                        shortcut.hotkey,
                        exc,
                    )
            elif shortcut.suffix:
                key = tuple(k.strip().lower() for k in shortcut.suffix)
                if key in seq_map:
                    self._logger.warning(
                        "Duplicate chord suffix sequence '%s' detected; later entry will override",
                        "+".join(key),
                    )
                seq_map[key] = callback

        # Configure chorded activator if present
        activator = registry.get_activator()
        if activator and seq_map:
            try:
                self._hotkey_manager.configure_chord_sequences(
                    activator.hotkey,
                    activator.timeout_ms,
                    seq_map,
                )
                self._logger.info(
                    "Chord activator configured: %s with %d suffix mappings (mode=%s)",
                    activator.hotkey,
                    len(seq_map),
                    getattr(activator, "mode", "press"),
                )
            except ValueError as exc:
                self._logger.warning("Failed to configure activator: %s", exc)

    def _handle_shortcut_in_main_thread(self, shortcut: Shortcut) -> None:
        """Handle shortcut trigger in the main Qt thread."""
        self._logger.info("Hotkey triggered: %s", shortcut.label())
        sound_id = self._sound_ids.get(shortcut)
        if sound_id:
            played = self._sound_player.play(sound_id)
            if not played:
                self._logger.warning("Unable to play sound for %s", shortcut.hotkey)
        elif shortcut.sound_path:
            self._logger.warning("Sound for %s was not preloaded", shortcut.hotkey)

        if shortcut.overlay:
            self._show_overlay(shortcut.overlay)

    def _show_overlay(self, config: OverlayConfig) -> None:
        size = None
        if config.width is not None and config.height is not None:
            size = (config.width, config.height)

        success = self._overlay_window.show_asset(
            config.file,
            duration_ms=config.duration_ms,
            position=(config.x, config.y),
            size=size,
        )
        if not success:
            self._logger.warning("Overlay failed to display: %s", config.file)
        else:
            size_str = f" size=({config.width},{config.height})" if size else ""
            self._logger.info(
                "Overlay displayed: file=%s position=(%s,%s) duration_ms=%s%s",
                config.file,
                config.x,
                config.y,
                config.duration_ms,
                size_str,
            )


def run_application(shortcuts: Iterable[Shortcut]) -> None:
    """Bootstrap the Qt application loop and start the MVP workflow."""
    from .tray_icon import TrayIcon

    # Ensure Qt uses software OpenGL before QApplication is constructed
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    except Exception:
        # Best-effort; continue if not supported on platform
        pass

    app = QApplication.instance() or QApplication([])
    application = Application(shortcuts)
    application.start()

    # Create system tray icon with quit callback
    tray = TrayIcon(
        on_quit=lambda: (application.stop(), app.quit()),
        on_open_configurator=_open_configurator,
    )
    tray.show()

    try:
        app.exec()
    finally:
        application.stop()
        tray.hide()


def _open_configurator() -> None:
    """Open the configurator window from the tray menu."""
    from .configurator import ConfiguratorWindow

    # Check if configurator is already open
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, ConfiguratorWindow):
            widget.raise_()
            widget.activateWindow()
            return

    # Create new configurator window
    window = ConfiguratorWindow()
    window.show()
