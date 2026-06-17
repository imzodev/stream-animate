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


def test_application_registers_and_triggers_shortcut(
    shortcut: Shortcut, qt_app
) -> None:
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


# ---------------------------------------------------------------------------
# Fact-checker wiring
# ---------------------------------------------------------------------------


class FakeFactCheckerEngine:
    """Drop-in for FactCheckerEngine that records observer callbacks."""

    def __init__(self) -> None:
        self.observers: List[callable] = []
        self.closed = False
        self.toggle_count = 0
        self.phase = "idle"

    def add_observer(self, callback) -> None:
        self.observers.append(callback)

    def remove_observer(self, callback) -> None:
        try:
            self.observers.remove(callback)
        except ValueError:
            pass

    def toggle(self) -> None:
        self.toggle_count += 1
        self.phase = "listening" if self.phase == "idle" else "idle"

    def close(self) -> None:
        self.closed = True


def test_application_constructs_fact_checker_when_api_key_present(
    shortcut: Shortcut, qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        llm_config=None,  # unset: must use the env var
    )
    # When no LLMConfig is passed, no engine is built.
    assert app.fact_checker() is None


def test_application_uses_provided_fact_checker(shortcut: Shortcut, qt_app) -> None:
    fake = FakeFactCheckerEngine()
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        fact_checker=fake,
    )
    assert app.fact_checker() is fake
    # Application should have subscribed to the engine so the tray
    # can be refreshed.
    assert app._on_fact_check_event in fake.observers  # noqa: SLF001


def test_application_fact_checker_toggle_via_hotkey(shortcut: Shortcut, qt_app) -> None:
    fake = FakeFactCheckerEngine()
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        fact_checker=fake,
    )
    app._on_fact_check_toggle()  # noqa: SLF001
    assert fake.toggle_count == 1


def test_application_fact_checker_toggle_is_noop_without_engine(
    shortcut: Shortcut, qt_app
) -> None:
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
    )
    # Should not raise.
    app._on_fact_check_toggle()  # noqa: SLF001


def test_application_fact_check_event_drives_panel(shortcut: Shortcut, qt_app) -> None:
    """A streaming event appends its delta to the answer panel."""
    from stream_companion.fact_checker import FactCheckerEvent

    class FakePanel:
        def __init__(self) -> None:
            self.tokens: List[str] = []
            self.phases: List[str] = []
            self.cleared = False
            self.shown = False

        def append_token(self, token: str) -> None:
            self.tokens.append(token)

        def clear(self) -> None:
            self.cleared = True

        def set_phase(self, phase: str) -> None:
            self.phases.append(phase)

        def set_persona_label(self, name: str) -> None:
            pass

        def show(self) -> None:
            self.shown = True

    panel = FakePanel()
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        fact_checker=FakeFactCheckerEngine(),
        answer_panel=panel,  # type: ignore[arg-type]
    )

    listening = FactCheckerEvent(phase="listening")
    thinking = FactCheckerEvent(phase="thinking", text="the question")
    token1 = FactCheckerEvent(phase="streaming", text="Hello", delta="Hello")
    token2 = FactCheckerEvent(phase="streaming", text="Hello world", delta=" world")
    done = FactCheckerEvent(phase="done", text="the question")

    app._handle_fact_check_event_in_main_thread(listening)  # noqa: SLF001
    app._handle_fact_check_event_in_main_thread(thinking)  # noqa: SLF001
    app._handle_fact_check_event_in_main_thread(token1)  # noqa: SLF001
    app._handle_fact_check_event_in_main_thread(token2)  # noqa: SLF001
    app._handle_fact_check_event_in_main_thread(done)  # noqa: SLF001

    assert panel.cleared is True
    assert panel.shown is True
    assert panel.tokens == ["Hello", " world"]
    assert panel.phases == ["listening", "thinking", "streaming", "streaming", "done"]


def test_application_registers_fact_checker_toggle_hotkey(
    shortcut: Shortcut, qt_app
) -> None:
    from stream_companion.llm.config import LLMConfig

    fake = FakeFactCheckerEngine()
    hotkeys = FakeHotkeyManager()
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=hotkeys,
        llm_config=LLMConfig(toggle_hotkey="<ctrl>+<alt>+q"),
        fact_checker=fake,
    )
    app.start()
    assert "<ctrl>+<alt>+q" in hotkeys.callbacks
    app.stop()


def test_application_set_llm_config_rebuilds_engine(
    shortcut: Shortcut, qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stream_companion.llm.config import LLMConfig

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        fact_checker=FakeFactCheckerEngine(),
    )
    original = app.fact_checker()
    app.set_llm_config(LLMConfig(toggle_hotkey="<ctrl>+<alt>+q", persona="eli5"))
    new = app.fact_checker()
    assert new is not original
    assert new is not None
    assert app.llm_config() is not None
    assert app.llm_config().persona == "eli5"


def test_application_set_llm_config_to_none_disables(
    shortcut: Shortcut, qt_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    app = Application(
        [shortcut],
        sound_player=FakeSoundPlayer(),
        overlay_window=FakeOverlayWindow(),
        hotkey_manager=FakeHotkeyManager(),
        fact_checker=FakeFactCheckerEngine(),
    )
    app.set_llm_config(None)
    assert app.fact_checker() is None
