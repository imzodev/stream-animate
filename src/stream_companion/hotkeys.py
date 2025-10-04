"""Hotkey management utilities for the Streaming Companion Tool."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Tuple, List

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
        listener_factory: Optional[
            Callable[[Callable, Callable], keyboard.Listener]
        ] = None,
        hotkey_factory: Optional[Callable[[str, Callback], keyboard.HotKey]] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._listener_factory = listener_factory or self._default_listener_factory
        self._hotkey_factory = hotkey_factory or self._default_hotkey_factory
        self._logger = logger or logging.getLogger(__name__)
        self._listener: Optional[keyboard.Listener] = None
        self._hotkeys: Dict[str, _Binding] = {}
        # Chorded hotkey support (press-mode MVP)
        self._activator_combo: Optional[str] = None
        self._activator_binding: Optional[_Binding] = None
        self._armed: bool = False
        self._arm_timer: Optional[threading.Timer] = None
        self._arm_timeout_ms: int = 1500
        # For sequential chords
        self._suffix_seq_map: Dict[Tuple[str, ...], Callback] = {}
        self._buffer: List[str] = []
        # When activator is triggered by a key press, ignore that press as a suffix token
        self._ignore_next: bool = False

    @staticmethod
    def _default_listener_factory(
        on_press: Callable[[keyboard.Key | keyboard.KeyCode], None],
        on_release: Callable[[keyboard.Key | keyboard.KeyCode], None],
    ) -> keyboard.Listener:
        return keyboard.Listener(on_press=on_press, on_release=on_release)

    @staticmethod
    def _default_hotkey_factory(
        combination: str, on_activate: Callback
    ) -> keyboard.HotKey:
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

    def configure_chord(self, activator: str, timeout_ms: int, suffix_map: Dict[str, Callback]) -> None:
        """Enable chorded shortcuts with a global activator and a map of suffix keys.

        MVP supports press-mode only: activator press arms for timeout_ms; next key triggers suffix.
        """
        if not activator:
            raise ValueError("Activator hotkey must be a non-empty string")
        if not suffix_map:
            raise ValueError("Suffix map must not be empty")

        # Register activator as a normal hotkey whose callback arms the manager
        self._activator_combo = self._normalize_combination(activator)
        def _arm_cb() -> None:
            self._arm(timeout_ms)

        # Use register_hotkey to reuse parsing/logging
        self.register_hotkey(activator, _arm_cb)
        with self._lock:
            # Back-compat: wrap single-key map into sequence map
            self._suffix_seq_map = { (k,): cb for k, cb in suffix_map.items() }
            self._arm_timeout_ms = max(100, int(timeout_ms))

    def configure_chord_sequences(self, activator: str, timeout_ms: int, seq_map: Dict[Tuple[str, ...], Callback]) -> None:
        """Enable chorded shortcuts using sequential suffix sequences.

        seq_map keys are tuples of tokens, e.g. ("g","h").
        """
        if not activator:
            raise ValueError("Activator hotkey must be a non-empty string")
        if not seq_map:
            raise ValueError("Sequence map must not be empty")

        self._activator_combo = self._normalize_combination(activator)
        def _arm_cb() -> None:
            self._arm(timeout_ms)
        self.register_hotkey(activator, _arm_cb)
        with self._lock:
            self._suffix_seq_map = dict(seq_map)
            self._arm_timeout_ms = max(100, int(timeout_ms))

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
            self._logger.exception(
                "Hotkey callback for %s raised an exception", binding.combination
            )
            return False
        return True

    def _on_press(
        self, key: keyboard.Key | keyboard.KeyCode
    ) -> None:  # pragma: no cover - integration path
        self._dispatch("press", key)

    def _on_release(
        self, key: keyboard.Key | keyboard.KeyCode
    ) -> None:  # pragma: no cover - integration path
        self._dispatch("release", key)

    def _dispatch(self, action: str, key: keyboard.Key | keyboard.KeyCode) -> None:
        with self._lock:
            listener = self._listener
            hotkeys = list(self._hotkeys.values())
        if listener is None:
            return
        canonical_key = listener.canonical(key)
        # Feed existing bindings first (activator will arm here on press)
        for binding in hotkeys:
            getattr(binding.hotkey, action)(canonical_key)

        # Handle chord suffix when ARMED on key press (sequential)
        if action == "press" and self._armed:
            # Ignore the very first key press after arming if it was the activator's own final key
            if self._ignore_next:
                self._ignore_next = False
                return
            token = self._key_to_token(canonical_key)
            if token is None:
                return
            # Esc cancels arming
            if token == "esc":
                self._disarm()
                return
            self._buffer.append(token)
            seq = tuple(self._buffer)
            # Exact match?
            callback = self._suffix_seq_map.get(seq)
            if callback is not None:
                self._disarm()
                try:
                    callback()
                except Exception:
                    self._logger.exception("Chord sequence callback for %s raised", "+".join(seq))
                return
            # Prefix of any sequence?
            if any(s[:len(seq)] == seq for s in self._suffix_seq_map.keys()):
                # Keep waiting for more keys within the same arming window
                return
            # No match and not a prefix -> disarm and clear buffer
            self._disarm()

    def _arm(self, timeout_ms: int) -> None:
        with self._lock:
            # Cancel previous timer
            if self._arm_timer is not None:
                self._arm_timer.cancel()
            self._armed = True
            self._buffer.clear()
            self._ignore_next = True
            self._arm_timer = threading.Timer(max(0.1, timeout_ms / 1000.0), self._disarm)
            self._arm_timer.daemon = True
            self._arm_timer.start()
            self._logger.debug("Activator armed for %d ms", timeout_ms)

    def _disarm(self) -> None:
        with self._lock:
            if self._arm_timer is not None:
                try:
                    self._arm_timer.cancel()
                except Exception:
                    pass
                self._arm_timer = None
            if self._armed:
                self._logger.debug("Activator disarmed")
            self._armed = False
            self._buffer.clear()
            self._ignore_next = False

    def _key_to_token(self, key: keyboard.Key | keyboard.KeyCode) -> Optional[str]:
        # Map special keys first
        specials = {
            keyboard.Key.esc: "esc",
            keyboard.Key.space: "space",
            keyboard.Key.enter: "enter",
            keyboard.Key.tab: "tab",
            keyboard.Key.backspace: "backspace",
            keyboard.Key.delete: "delete",
            keyboard.Key.up: "up",
            keyboard.Key.down: "down",
            keyboard.Key.left: "left",
            keyboard.Key.right: "right",
            keyboard.Key.home: "home",
            keyboard.Key.end: "end",
            keyboard.Key.page_up: "pageup",
            keyboard.Key.page_down: "pagedown",
            keyboard.Key.f1: "f1", keyboard.Key.f2: "f2", keyboard.Key.f3: "f3", keyboard.Key.f4: "f4",
            keyboard.Key.f5: "f5", keyboard.Key.f6: "f6", keyboard.Key.f7: "f7", keyboard.Key.f8: "f8",
            keyboard.Key.f9: "f9", keyboard.Key.f10: "f10", keyboard.Key.f11: "f11", keyboard.Key.f12: "f12",
        }
        if key in specials:
            return specials[key]
        if isinstance(key, keyboard.KeyCode) and key.char is not None:
            c = key.char.lower()
            if len(c) == 1 and (c.isalnum() or c in "-=`[];,'./\\"):
                return c
        return None
