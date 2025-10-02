"""System tray icon for the Streaming Companion Tool."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

_LOGGER = logging.getLogger(__name__)


class TrayIcon:
    """System tray icon with context menu for application control."""

    def __init__(
        self,
        *,
        on_quit: Optional[Callable[[], None]] = None,
        on_open_configurator: Optional[Callable[[], None]] = None,
    ) -> None:
        """Initialize the system tray icon.

        Args:
            on_quit: Callback to execute when user selects Quit
            on_open_configurator: Callback to execute when user selects Open Configurator
        """
        self._on_quit = on_quit
        self._on_open_configurator = on_open_configurator
        self._tray_icon: Optional[QSystemTrayIcon] = None
        self._menu: Optional[QMenu] = None

    def show(self) -> bool:
        """Show the system tray icon.

        Returns:
            True if the tray icon was shown successfully, False otherwise.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            _LOGGER.warning("System tray is not available on this platform")
            return False

        app = QApplication.instance()
        if not app:
            _LOGGER.error("QApplication instance not found")
            return False

        # Create tray icon
        self._tray_icon = QSystemTrayIcon(app)

        # Try to set an icon (fallback to default if no custom icon exists)
        icon = self._load_icon()
        if icon and not icon.isNull():
            self._tray_icon.setIcon(icon)
        else:
            # Use a default Qt icon as fallback
            from PySide6.QtWidgets import QStyle

            self._tray_icon.setIcon(
                app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            )

        self._tray_icon.setToolTip("Streaming Companion Tool")

        # Create context menu
        self._menu = QMenu()

        if self._on_open_configurator:
            open_config_action = QAction("Open Configurator", self._menu)
            open_config_action.triggered.connect(self._handle_open_configurator)
            self._menu.addAction(open_config_action)
            self._menu.addSeparator()

        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self._handle_quit)
        self._menu.addAction(quit_action)

        self._tray_icon.setContextMenu(self._menu)
        self._tray_icon.show()

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
        """Show a notification message from the tray icon.

        Args:
            title: Notification title
            message: Notification message
            icon: Icon type (defaults to Information)
        """
        if not self._tray_icon:
            return

        if icon is None:
            icon = QSystemTrayIcon.MessageIcon.Information

        self._tray_icon.showMessage(title, message, icon, 3000)

    def _load_icon(self) -> Optional[QIcon]:
        """Load the tray icon from assets.

        Returns:
            QIcon if found, None otherwise.
        """
        # Try to load custom icon from assets
        from pathlib import Path

        icon_paths = [
            Path(__file__).parents[2] / "assets" / "icon.png",
            Path(__file__).parents[2] / "assets" / "tray_icon.png",
        ]

        for path in icon_paths:
            if path.exists():
                return QIcon(str(path))

        return None

    def _handle_quit(self) -> None:
        """Handle quit action from tray menu."""
        _LOGGER.info("Quit requested from system tray")
        if self._on_quit:
            self._on_quit()
        else:
            # Default behavior: quit the application
            app = QApplication.instance()
            if app:
                app.quit()

    def _handle_open_configurator(self) -> None:
        """Handle open configurator action from tray menu."""
        _LOGGER.info("Open configurator requested from system tray")
        if self._on_open_configurator:
            self._on_open_configurator()
