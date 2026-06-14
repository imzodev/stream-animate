from __future__ import annotations

import logging
from typing import Callable, List

import pytest

from stream_companion.hotkeys import HotkeyManager


class FakeListener:
    def __init__(self, on_press: Callable, on_release: Callable) -> None:
        self.on_press = on_press
        self.on_release = on_release
        self.running = False
        self._canonical_calls: List = []

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def canonical(self, key):
        self._canonical_calls.append(key)
        return key


class FakeHotKey:
    def __init__(self, combination: str, callback: Callable[[], None]) -> None:
        self.combination = combination
        self.callback = callback
        self.press_calls: List = []
        self.release_calls: List = []

    def press(self, key) -> None:
        self.press_calls.append(key)

    def release(self, key) -> None:
        self.release_calls.append(key)
        self.callback()


def make_manager():
    listener_ref = {}

    def listener_factory(on_press, on_release):
        listener = FakeListener(on_press, on_release)
        listener_ref["instance"] = listener
        return listener

    def hotkey_factory(combination, callback):
        hotkey = FakeHotKey(combination, callback)
        return hotkey

    manager = HotkeyManager(
        listener_factory=listener_factory,
        hotkey_factory=hotkey_factory,
        logger=logging.getLogger("test.hotkeys"),
    )
    return manager, listener_ref


def test_register_and_trigger_executes_callback():
    manager, _ = make_manager()
    calls: List[str] = []

    manager.register_hotkey("<ctrl>+<alt>+c", lambda: calls.append("hit"))
    assert manager.trigger("<CTRL> + <ALT> + C") is True
    assert calls == ["hit"]
    assert "<ctrl>+<alt>+c" in manager.registered_combinations()


def test_duplicate_registration_raises_value_error():
    manager, _ = make_manager()
    manager.register_hotkey("a", lambda: None)
    with pytest.raises(ValueError):
        manager.register_hotkey("A", lambda: None)


def test_unregister_hotkey_removes_binding():
    manager, _ = make_manager()
    manager.register_hotkey("a", lambda: None)
    assert manager.unregister_hotkey("a") is True
    assert manager.unregister_hotkey("a") is False
    assert manager.trigger("a") is False


def test_start_and_stop_control_listener_state():
    manager, listener_ref = make_manager()

    assert manager.start() is True
    assert manager.is_running is True
    assert manager.start() is False

    listener = listener_ref["instance"]
    assert listener.running is True

    assert manager.stop() is True
    assert listener.running is False
    assert manager.stop() is False


def test_dispatch_invokes_hotkey_press_and_release():
    manager, listener_ref = make_manager()
    events: List[str] = []

    manager.register_hotkey("b", lambda: events.append("released"))
    manager.start()
    listener = listener_ref["instance"]
    fake_key = object()

    listener.on_press(fake_key)
    listener.on_release(fake_key)

    binding = next(iter(manager._hotkeys.values()))  # type: ignore[attr-defined]
    assert binding.hotkey.press_calls == [fake_key]
    assert binding.hotkey.release_calls == [fake_key]
    assert events == ["released"]
    manager.stop()


def test_canonicalize_wraps_modifiers_in_angle_brackets():
    from stream_companion.hotkeys import HotkeyManager

    assert HotkeyManager.canonicalize("ctrl+alt+9") == "<ctrl>+<alt>+9"
    assert HotkeyManager.canonicalize("<ctrl>+<alt>+9") == "<ctrl>+<alt>+9"
    assert HotkeyManager.canonicalize("Ctrl + Alt + 9") == "<ctrl>+<alt>+9"
    assert HotkeyManager.canonicalize("shift+a") == "<shift>+a"
    assert HotkeyManager.canonicalize("F5") == "f5"


def test_canonicalize_rejects_invalid_inputs():
    from stream_companion.hotkeys import HotkeyManager

    import pytest

    with pytest.raises(ValueError):
        HotkeyManager.canonicalize("")
    with pytest.raises(ValueError):
        HotkeyManager.canonicalize("+++")
    with pytest.raises(ValueError):
        # Modifier-only combinations are not allowed
        HotkeyManager.canonicalize("ctrl+alt")


def test_register_hotkey_normalizes_bare_form():
    """A bare 'ctrl+alt+9' should be registered the same as '<ctrl>+<alt>+9'."""

    manager, _ = make_manager()
    calls: List[str] = []

    manager.register_hotkey("ctrl+alt+9", lambda: calls.append("hit"))
    # Trigger using the canonical form
    assert manager.trigger("<ctrl>+<alt>+9") is True
    # And via the bare form too (since it normalizes)
    assert manager.trigger("Ctrl + Alt + 9") is True
    assert calls == ["hit", "hit"]
