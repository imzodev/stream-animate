from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from stream_companion.application import Application
from stream_companion.models import OverlayConfig, Shortcut


@pytest.fixture(scope="module")
def qt_app():
    """Provide a QApplication instance for tests."""
    app = QApplication.instance() or QApplication([])
    yield app


class FakeSoundPlayer:
    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.loaded: Dict[str, str] = {}
        self.played: List[str] = []
        self.shutdown_called = False

    def load(self, sound_id: str, path: str) -> bool:
        if self.succeed:
            self.loaded[sound_id] = path
        return self.succeed

    def play(
        self, sound_id: str, *, loops: int = 0
    ) -> bool:  # noqa: ARG002 - test double interface
        self.played.append(sound_id)
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeOverlayWindow:
    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.calls: List[OverlayConfig] = []

    def show_asset(
        self,
        file: str,
        *,
        duration_ms: int,
        position: Optional[tuple[int, int]],
        size: Optional[tuple[int, int]] = None,
    ) -> bool:
        self.calls.append(
            OverlayConfig(
                file=file,
                x=position[0] if position else 0,
                y=position[1] if position else 0,
                duration_ms=duration_ms,
                width=size[0] if size else None,
                height=size[1] if size else None,
            )
        )
        return self.succeed


class FakeHotkeyManager:
    def __init__(self) -> None:
        self.callbacks: Dict[str, callable] = {}
        self.started = False
        self.stopped = False

    def register_hotkey(self, combination: str, callback) -> None:
        if combination in self.callbacks:
            raise ValueError("duplicate")
        self.callbacks[combination] = callback

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> bool:
        self.stopped = True
        return True


@pytest.fixture()
def shortcut() -> Shortcut:
    return Shortcut(
        hotkey="<ctrl>+<alt>+k",
        sound_path="/tmp/sound.wav",
        overlay=OverlayConfig(file="/tmp/overlay.png", x=10, y=20, duration_ms=500),
    )


def test_application_registers_and_triggers_shortcut(shortcut: Shortcut, qt_app) -> None:
    sound = FakeSoundPlayer()
    overlay = FakeOverlayWindow()
    hotkeys = FakeHotkeyManager()

    app = Application(
        [shortcut], sound_player=sound, overlay_window=overlay, hotkey_manager=hotkeys
    )
    app.start()

    assert sound.loaded  # sound preloaded
    assert hotkeys.started is True
    assert shortcut.hotkey in hotkeys.callbacks

    hotkeys.callbacks[shortcut.hotkey]()
    # Process Qt events to handle the signal
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents()

    assert sound.played == list(sound.loaded.keys())
    assert len(overlay.calls) == 1
    assert overlay.calls[0].file == shortcut.overlay.file  # type: ignore[union-attr]

    app.stop()
    assert sound.shutdown_called is True
    assert hotkeys.stopped is True


def test_application_handles_missing_sound_gracefully(
    shortcut: Shortcut, qt_app, caplog: pytest.LogCaptureFixture
) -> None:
    sound = FakeSoundPlayer(succeed=False)
    overlay = FakeOverlayWindow()
    hotkeys = FakeHotkeyManager()

    app = Application(
        [shortcut], sound_player=sound, overlay_window=overlay, hotkey_manager=hotkeys
    )
    with caplog.at_level(logging.WARNING):
        app.start()

    assert not sound.loaded  # load failed
    hotkeys.callbacks[shortcut.hotkey]()
    # Process Qt events to handle the signal
    QCoreApplication.processEvents()
    QCoreApplication.sendPostedEvents()

    assert overlay.calls  # overlay still displayed
    assert "Failed to preload sound" in caplog.text
    assert "Unable to play sound" in caplog.text or "was not preloaded" in caplog.text

    app.stop()
