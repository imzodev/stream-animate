"""Hotkey management utilities for the Streaming Companion Tool."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional

from pynput import keyboard

Callback = Callable[[], None]


@dataclass
class _Binding:
    combination: str
    callback: Callback
    hotkey: keyboard.HotKey


class HotkeyManager:
    """Manage registration and dispatch of global hotkeys."""

    def __init__(
        self,
        listener_factory: Optional[Callable[[Callable, Callable], keyboard.Listener]] = None,
        hotkey_factory: Optional[Callable[[str, Callback], keyboard.HotKey]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._listener_factory = listener_factory or self._default_listener_factory
        self._hotkey_factory = hotkey_factory or self._default_hotkey_factory
        self._logger = logger or logging.getLogger(__name__)
        self._listener: Optional[keyboard.Listener] = None
        self._hotkeys: Dict[str, _Binding] = {}

    @staticmethod
    def _default_listener_factory(
        on_press: Callable[[keyboard.Key | keyboard.KeyCode], None],
        on_release: Callable[[keyboard.Key | keyboard.KeyCode], None],
    ) -> keyboard.Listener:
        return keyboard.Listener(on_press=on_press, on_release=on_release)

    @staticmethod
    def _default_hotkey_factory(combination: str, on_activate: Callback) -> keyboard.HotKey:
        parsed = keyboard.HotKey.parse(combination)
        return keyboard.HotKey(parsed, on_activate)

    @staticmethod
    def _normalize_combination(combination: str) -> str:
        parts = [segment.strip() for segment in combination.split("+")]
        return "+".join(parts).lower()

    @property
    def is_running(self) -> bool:
        with self._lock:
            listener = self._listener
        return bool(listener and listener.running)

    def start(self) -> bool:
        with self._lock:
            if self._listener is not None:
                return False
            listener = self._listener_factory(self._on_press, self._on_release)
            self._listener = listener
        listener.start()
        self._logger.info("Hotkey manager started with %d hotkeys", len(self._hotkeys))
        return True

    def stop(self) -> bool:
        with self._lock:
            listener = self._listener
            if listener is None:
                return False
            self._listener = None
        listener.stop()
        self._logger.info("Hotkey manager stopped")
        return True

    def register_hotkey(self, combination: str, callback: Callback) -> None:
        if not combination:
            raise ValueError("Hotkey combination must be a non-empty string")
        if not callable(callback):
            raise ValueError("Hotkey callback must be callable")

        normalized = self._normalize_combination(combination)
        with self._lock:
            if normalized in self._hotkeys:
                raise ValueError(f"Hotkey '{combination}' already registered")
            hotkey = self._hotkey_factory(
                combination,
                lambda combo=normalized: self._execute_callback(combo),
            )
            self._hotkeys[normalized] = _Binding(combination, callback, hotkey)
        self._logger.info("Registered hotkey %s", combination)

    def unregister_hotkey(self, combination: str) -> bool:
        normalized = self._normalize_combination(combination)
        with self._lock:
            removed = self._hotkeys.pop(normalized, None)
        if removed:
            self._logger.info("Unregistered hotkey %s", combination)
            return True
        self._logger.debug("Attempted to remove unknown hotkey %s", combination)
        return False

    def trigger(self, combination: str) -> bool:
        normalized = self._normalize_combination(combination)
        return self._execute_callback(normalized)

    def registered_combinations(self) -> Iterable[str]:
        with self._lock:
            return tuple(binding.combination for binding in self._hotkeys.values())

    def _execute_callback(self, normalized: str) -> bool:
        with self._lock:
            binding = self._hotkeys.get(normalized)
        if not binding:
            self._logger.debug("Hotkey %s not found", normalized)
            return False
        try:
            binding.callback()
        except Exception:  # pragma: no cover - defensive logging
            self._logger.exception("Hotkey callback for %s raised an exception", binding.combination)
            return False
        return True

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:  # pragma: no cover - integration path
        self._dispatch("press", key)

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:  # pragma: no cover - integration path
        self._dispatch("release", key)

    def _dispatch(self, action: str, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self._lock:
            listener = self._listener
            hotkeys = list(self._hotkeys.values())
        if listener is None:
            return
        canonical_key = listener.canonical(key)
        for binding in hotkeys:
            getattr(binding.hotkey, action)(canonical_key)
