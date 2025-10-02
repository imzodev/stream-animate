"""Tests for the tray icon module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from stream_companion.tray_icon import TrayIcon


@pytest.fixture
def qt_app():
    """Provide a QApplication instance for tests."""
    app = QApplication.instance() or QApplication([])
    yield app


def test_tray_icon_initialization(qt_app):
    """Test that TrayIcon can be initialized."""
    on_quit_mock = MagicMock()
    on_config_mock = MagicMock()

    tray = TrayIcon(on_quit=on_quit_mock, on_open_configurator=on_config_mock)

    assert tray._on_quit == on_quit_mock
    assert tray._on_open_configurator == on_config_mock


@patch("stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable")
def test_tray_icon_show_when_available(mock_available, qt_app):
    """Test showing tray icon when system tray is available."""
    mock_available.return_value = True

    tray = TrayIcon()
    result = tray.show()

    assert result is True
    assert tray._tray_icon is not None
    assert tray._menu is not None


@patch("stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable")
def test_tray_icon_show_when_unavailable(mock_available, qt_app):
    """Test showing tray icon when system tray is not available."""
    mock_available.return_value = False

    tray = TrayIcon()
    result = tray.show()

    assert result is False


def test_tray_icon_hide(qt_app):
    """Test hiding the tray icon."""
    with patch("stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
        tray = TrayIcon()
        tray.show()
        tray.hide()

        # Icon should still exist but be hidden
        assert tray._tray_icon is not None


def test_tray_icon_quit_callback(qt_app):
    """Test that quit callback is invoked."""
    on_quit_mock = MagicMock()

    with patch("stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
        tray = TrayIcon(on_quit=on_quit_mock)
        tray.show()
        tray._handle_quit()

        on_quit_mock.assert_called_once()


def test_tray_icon_configurator_callback(qt_app):
    """Test that configurator callback is invoked."""
    on_config_mock = MagicMock()

    with patch("stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable", return_value=True):
        tray = TrayIcon(on_open_configurator=on_config_mock)
        tray.show()
        tray._handle_open_configurator()

        on_config_mock.assert_called_once()
