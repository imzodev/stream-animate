"""Tests for the tray icon module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from stream_companion.tray_icon import TrayIcon
from stream_companion.tray_indicators import TrayIndicatorState


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
    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon()
        tray.show()
        tray.hide()

        # Icon should still exist but be hidden
        assert tray._tray_icon is not None


def test_tray_icon_quit_callback(qt_app):
    """Test that quit callback is invoked."""
    on_quit_mock = MagicMock()

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_quit=on_quit_mock)
        tray.show()
        tray._handle_quit()

        on_quit_mock.assert_called_once()


def test_tray_icon_configurator_callback(qt_app):
    """Test that configurator callback is invoked."""
    on_config_mock = MagicMock()

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_open_configurator=on_config_mock)
        tray.show()
        tray._handle_open_configurator()

        on_config_mock.assert_called_once()


# ---------------------------------------------------------------------------
# New state-driven tests
# ---------------------------------------------------------------------------


def test_state_key_handles_none_and_state():
    """The state key is hashable and used to skip no-op refreshes."""

    tray = TrayIcon()
    assert tray._state_key(None) == ("none",)
    state_off = TrayIndicatorState(enabled=False)
    state_on = TrayIndicatorState(enabled=True, stt_active=True, typing_active=False)
    # The 4th element of the key is a (fact_check_configured, fact_phase)
    # tuple; both states have no fact_check configured, so it's
    # ("none", "idle") in both cases.
    assert tray._state_key(state_off) == ("off", False, False, ("none", "idle"))
    assert tray._state_key(state_on) == ("on", True, False, ("none", "idle"))
    # Different states produce different keys
    assert tray._state_key(state_on) != tray._state_key(state_off)


def test_state_key_distinguishes_fact_check_phase():
    """The state key changes when the fact-checker phase changes."""

    from stream_companion.tray_indicators import FactCheckerIndicatorState

    tray = TrayIcon()
    s_idle = TrayIndicatorState(
        enabled=True,
        fact_check=FactCheckerIndicatorState(configured=True, phase="idle"),
    )
    s_listening = TrayIndicatorState(
        enabled=True,
        fact_check=FactCheckerIndicatorState(configured=True, phase="listening"),
    )
    assert tray._state_key(s_idle) != tray._state_key(s_listening)


def test_refresh_skips_when_state_unchanged(qt_app):
    """A refresh with the same state should not call setIcon again."""

    from PySide6.QtGui import QIcon, QPixmap

    state_calls = {"n": 0}

    def state_provider():
        state_calls["n"] += 1
        return TrayIndicatorState(enabled=True, stt_active=True, typing_active=False)

    def fake_compose(state, *, size=64, base_pixmap=None):
        return QIcon(QPixmap(size, size))

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(stt_state_provider=state_provider)
        tray.show()
        # Reset the state-key cache so the next refresh will compare
        # against the previous key.
        tray._last_state_key = None
        with patch.object(tray, "_composed_icon", side_effect=fake_compose) as composed:
            tray.refresh_stt_label()  # first call after reset
            tray.refresh_stt_label()  # same state, should be a no-op
            assert composed.call_count == 1


def test_refresh_updates_icon_on_state_change(qt_app):
    """A state change must call setIcon on the tray."""

    from PySide6.QtGui import QIcon, QPixmap

    state_holder = {"state": TrayIndicatorState(enabled=True)}

    def fake_compose(state, *, size=64, base_pixmap=None):
        return QIcon(QPixmap(size, size))

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(stt_state_provider=lambda: state_holder["state"])
        tray.show()
        tray._last_state_key = None  # force re-eval
        with patch.object(tray, "_composed_icon", side_effect=fake_compose) as composed:
            # First refresh: enabled=True
            tray.refresh_stt_label()
            assert composed.call_count == 1
            # State changes
            state_holder["state"] = TrayIndicatorState(
                enabled=True, stt_active=True, typing_active=True
            )
            tray.refresh_stt_label()
            assert composed.call_count == 2
            # State goes away (None)
            state_holder["state"] = None
            tray.refresh_stt_label()
            # The None branch doesn't compose, so the count is still 2
            assert composed.call_count == 2


def test_menu_hidden_when_state_is_none(qt_app):
    """When STT is not configured, the toggle item is hidden."""

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_toggle_stt=MagicMock(), stt_state_provider=lambda: None)
        tray.show()
        tray.refresh_stt_label()
        assert tray._stt_toggle_action is not None
        assert tray._stt_toggle_action.isVisible() is False


def test_menu_label_reflects_typing_state(qt_app):
    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_toggle_stt=MagicMock())
        tray.show()
        # Typing active
        tray._update_menu(
            TrayIndicatorState(enabled=True, stt_active=True, typing_active=True)
        )
        assert "typing" in tray._stt_toggle_action.text().lower()
        # Listening only (triggers)
        tray._update_menu(
            TrayIndicatorState(enabled=True, stt_active=True, typing_active=False)
        )
        assert "listening" in tray._stt_toggle_action.text().lower()
        # Idle
        tray._update_menu(
            TrayIndicatorState(enabled=True, stt_active=False, typing_active=False)
        )
        assert "start" in tray._stt_toggle_action.text().lower()


def test_menu_disabled_when_stt_disabled_in_config(qt_app):
    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_toggle_stt=MagicMock())
        tray.show()
        tray._update_menu(
            TrayIndicatorState(enabled=False, stt_active=False, typing_active=False)
        )
        assert tray._stt_toggle_action.isEnabled() is False
        assert "disabled" in tray._stt_toggle_action.text().lower()


def test_left_click_toggles_stt(qt_app):
    """Left-clicking the tray icon must invoke the toggle callback."""

    on_toggle = MagicMock()
    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon(on_toggle_stt=on_toggle)
        tray.show()
        # Simulate a left-click (Trigger) and a double-click
        from PySide6.QtWidgets import QSystemTrayIcon as _Sti

        tray._on_activated(_Sti.ActivationReason.Trigger)
        tray._on_activated(_Sti.ActivationReason.DoubleClick)
        assert on_toggle.call_count == 2


def test_left_click_ignored_when_no_toggle_callback(qt_app):
    """When no on_toggle_stt is provided, the click is a no-op."""

    with patch(
        "stream_companion.tray_icon.QSystemTrayIcon.isSystemTrayAvailable",
        return_value=True,
    ):
        tray = TrayIcon()  # no on_toggle_stt
        tray.show()
        # Should not raise even though there's no callback
        from PySide6.QtWidgets import QSystemTrayIcon as _Sti

        tray._on_activated(_Sti.ActivationReason.Trigger)  # no error
