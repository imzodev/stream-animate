"""System tray icon for the Streaming Companion Tool."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .tray_indicators import (
    FactCheckerIndicatorState,
    TrayIndicatorState,
    compose_tray_icon,
)

_LOGGER = logging.getLogger(__name__)


class TrayIcon:
    """System tray icon with context menu for application control.

    The icon itself reflects two independent indicators (see
    :class:`stream_companion.tray_indicators.TrayIndicatorState`):

    * a red dot in the top-right when STT is active (the engine is
      listening);
    * a blue dot in the bottom-right when typing into the focused
      window is active.

    The state is provided by an injected callable (typically
    ``Application._stt_state``) that returns either ``None`` (STT not
    configured) or a :class:`TrayIndicatorState` describing the dots
    to show.
    """

    def __init__(
        self,
        *,
        on_quit: Optional[Callable[[], None]] = None,
        on_open_configurator: Optional[Callable[[], None]] = None,
        on_toggle_stt: Optional[Callable[[], None]] = None,
        on_toggle_fact_check: Optional[Callable[[], None]] = None,
        stt_state_provider: Optional[Callable[[], Optional[TrayIndicatorState]]] = None,
    ) -> None:
        """Initialize the system tray icon.

        Args:
            on_quit: Callback to execute when user selects Quit.
            on_open_configurator: Callback for the Open Configurator item.
            on_toggle_stt: Callback for the Start/Stop STT menu item (and
                also fired when the user left-clicks the tray icon).
            on_toggle_fact_check: Callback for the Toggle Fact-Checker item.
            stt_state_provider: Callable returning a
                :class:`TrayIndicatorState` or ``None``. ``None`` hides
                the STT-related menu entries; otherwise the state drives
                the colored dots on the icon and the menu labels.
        """
        self._on_quit = on_quit
        self._on_open_configurator = on_open_configurator
        self._on_toggle_stt = on_toggle_stt
        self._on_toggle_fact_check = on_toggle_fact_check
        self._stt_state_provider = stt_state_provider
        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None
        self._stt_toggle_action: Optional[QAction] = None
        self._fact_check_action: Optional[QAction] = None
        self._base_icon: Optional[QIcon] = None
        self._last_state_key: Optional[tuple] = None
        # Disable the dot menu entries until state is provided.
        self._stt_menu_hidden = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def show(self) -> bool:
        """Show the system tray icon."""

        if not QSystemTrayIcon.isSystemTrayAvailable():
            _LOGGER.warning("System tray is not available on this platform")
            return False

        app = QApplication.instance()
        if not app:
            _LOGGER.error("QApplication instance not found")
            return False

        self._tray_icon = QSystemTrayIcon(app)
        self._install_initial_icon()
        self._tray_icon.setToolTip("Streaming Companion Tool")

        # Left-click on the icon = toggle STT (standard media-app behavior)
        if self._on_toggle_stt is not None:
            self._tray_icon.activated.connect(self._on_activated)

        # Build the context menu
        self._menu = QMenu()

        if self._on_open_configurator:
            open_config_action = QAction("Open Configurator", self._menu)
            open_config_action.triggered.connect(self._handle_open_configurator)
            self._menu.addAction(open_config_action)

        if self._on_toggle_stt is not None:
            self._stt_toggle_action = QAction("Start STT", self._menu)
            self._stt_toggle_action.triggered.connect(self._handle_toggle_stt)
            self._menu.addAction(self._stt_toggle_action)

        if self._on_toggle_fact_check is not None:
            self._fact_check_action = QAction("Toggle Fact-Checker", self._menu)
            self._fact_check_action.triggered.connect(self._handle_toggle_fact_check)
            self._menu.addAction(self._fact_check_action)

        if self._menu.actions():
            self._menu.addSeparator()

        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self._handle_quit)
        self._menu.addAction(quit_action)

        self._tray_icon.setContextMenu(self._menu)
        self._tray_icon.show()

        # Apply the initial state (e.g. hide the toggle when STT is
        # not configured).
        self.refresh_stt_label()
        _LOGGER.info("System tray icon displayed")
        return True

    def hide(self) -> None:
        """Hide the system tray icon."""
        if self._tray_icon:
            self._tray_icon.hide()
            _LOGGER.info("System tray icon hidden")

    def show_message(
        self, title: str, message: str, icon: QSystemTrayIcon.MessageIcon = None
    ) -> None:
        """Show a notification message from the tray icon."""

        if not self._tray_icon:
            return
        if icon is None:
            icon = QSystemTrayIcon.MessageIcon.Information
        self._tray_icon.showMessage(title, message, icon, 3000)

    # ------------------------------------------------------------------
    # State refresh
    # ------------------------------------------------------------------

    def _install_initial_icon(self) -> None:
        """Install a base icon (no dots) so the tray has something to show."""

        from .tray_indicators import find_base_icon_pixmap, _fallback_base_pixmap

        base = find_base_icon_pixmap(64) or _fallback_base_pixmap(64)
        self._base_icon = QIcon(base)
        if self._tray_icon is not None:
            self._tray_icon.setIcon(self._base_icon)

    def _composed_icon(self, state: TrayIndicatorState) -> QIcon:
        """Compose the indicator-painted icon for the given state."""

        from .tray_indicators import find_base_icon_pixmap

        base = find_base_icon_pixmap(64)
        return compose_tray_icon(state, size=64, base_pixmap=base)

    def refresh_stt_label(self) -> None:
        """Update the STT menu label and the indicator icon to match state.

        This is the single entry point the application should call when
        the engine state changes. It is a no-op if nothing relevant has
        changed (compared against ``_last_state_key``) to avoid
        unnecessary repaints.
        """

        if self._tray_icon is None or self._stt_state_provider is None:
            return

        state = self._stt_state_provider()
        key = self._state_key(state)
        if key == self._last_state_key:
            return
        self._last_state_key = key

        # Update the icon
        if state is not None:
            icon = self._composed_icon(state)
            self._tray_icon.setIcon(icon)
            self._tray_icon.setToolTip(state.tooltip)
        else:
            # No STT configured at all — revert to the base icon and
            # hide the STT menu item.
            self._tray_icon.setIcon(self._base_icon or QIcon())
            self._tray_icon.setToolTip("Streaming Companion Tool")

        # Update the menu
        if self._stt_toggle_action is not None:
            self._update_menu(state)

    def _state_key(self, state: Optional[TrayIndicatorState]) -> Optional[tuple]:
        """Return a hashable key for the state, used to skip no-op refreshes."""

        if state is None:
            return ("none",)
        if state.fact_check is None:
            fact_key = ("none", "idle")
        else:
            fact_key = (
                "on" if state.fact_check.configured else "off",
                state.fact_check.phase,
            )
        return (
            "on" if state.enabled else "off",
            bool(state.stt_active),
            bool(state.typing_active),
            fact_key,
        )

    def _update_menu(self, state: Optional[TrayIndicatorState]) -> None:
        """Refresh the STT menu entry to reflect the current state."""

        if self._stt_toggle_action is None:
            return
        if state is None:
            # STT not configured: hide the toggle entirely
            self._stt_toggle_action.setVisible(False)
            self._stt_menu_hidden = True
            self._update_fact_check_menu(None)
            return
        # STT is configured (the engine is or could be running).
        self._stt_toggle_action.setVisible(True)
        self._stt_menu_hidden = False
        if not state.enabled:
            self._stt_toggle_action.setText("STT (disabled in config)")
            self._stt_toggle_action.setEnabled(False)
        else:
            if state.typing_active:
                self._stt_toggle_action.setText("Stop STT (currently typing)")
            elif state.stt_active:
                self._stt_toggle_action.setText("Stop STT (listening for triggers)")
            else:
                self._stt_toggle_action.setText("Start STT")
            self._stt_toggle_action.setEnabled(True)
        self._update_fact_check_menu(state.fact_check)

    def _update_fact_check_menu(
        self, fact_state: Optional["FactCheckerIndicatorState"]
    ) -> None:
        """Show / label / disable the fact-checker menu entry."""

        if self._fact_check_action is None:
            return
        if fact_state is None or not fact_state.configured:
            self._fact_check_action.setVisible(False)
            return
        self._fact_check_action.setVisible(True)
        phase = fact_state.phase
        if phase == "listening":
            self._fact_check_action.setText("Stop Fact-Checker (listening)")
        elif phase == "thinking":
            self._fact_check_action.setText("Stop Fact-Checker (thinking)")
        elif phase == "streaming":
            self._fact_check_action.setText("Stop Fact-Checker (answering)")
        else:
            self._fact_check_action.setText("Start Fact-Checker")
        self._fact_check_action.setEnabled(True)

    # ------------------------------------------------------------------
    # Click / menu handlers
    # ------------------------------------------------------------------

    def _on_activated(self, reason) -> None:
        """Handle tray icon activation (left-click toggles STT)."""

        # QSystemTrayIcon.ActivationReason.Trigger == left single click
        # on Linux/Windows; DoubleClick is also common. We treat both
        # as a toggle for convenience.
        from PySide6.QtWidgets import QSystemTrayIcon as _Sti

        if reason in (_Sti.ActivationReason.Trigger, _Sti.ActivationReason.DoubleClick):
            self._handle_toggle_stt()

    def _handle_quit(self) -> None:
        """Handle quit action from tray menu."""

        _LOGGER.info("Quit requested from system tray")
        if self._on_quit:
            self._on_quit()
        else:
            app = QApplication.instance()
            if app:
                app.quit()

    def _handle_open_configurator(self) -> None:
        """Handle open configurator action from tray menu."""

        _LOGGER.info("Open configurator requested from system tray")
        if self._on_open_configurator:
            self._on_open_configurator()

    def _handle_toggle_stt(self) -> None:
        """Handle STT toggle from the tray menu or icon click."""

        _LOGGER.info("STT toggle requested from system tray")
        if self._on_toggle_stt:
            self._on_toggle_stt()
        # The application is expected to refresh the state via the
        # observer; we also call refresh directly in case the observer
        # wasn't wired (e.g. when the toggle is invoked from a one-off
        # CLI path).
        self.refresh_stt_label()

    def _handle_toggle_fact_check(self) -> None:
        """Handle fact-checker toggle from the tray menu."""

        _LOGGER.info("Fact-checker toggle requested from system tray")
        if self._on_toggle_fact_check:
            self._on_toggle_fact_check()
        self.refresh_stt_label()
