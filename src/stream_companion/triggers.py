"""Voice-trigger matching for the Streaming Companion Tool.

The STT engine transcribes short audio chunks and emits the resulting
text. The application can attach one or more ``trigger_word`` values
to a :class:`~stream_companion.models.Shortcut`; when the transcribed
phrase contains the word (case-insensitive, word-boundary match), the
shortcut fires.

This module owns the matching logic — keeping it out of the engine
keeps the engine decoupled from application-level concepts. The
:func:`TriggerMatcher` class:

* Holds a mapping of normalized trigger word → callback.
* Maintains a per-word cooldown timestamp so a single utterance with
  overlapping chunks does not re-fire the same shortcut many times.
* Provides both a pure ``match`` (no side effects, used for logging
  and tests) and a ``dispatch`` that respects the cooldown.

Matching is case-insensitive and uses Unicode-aware word boundaries
so accented characters and CJK punctuation don't break the check.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Word-boundary matching
# ---------------------------------------------------------------------------


# A trigger word is "letters, digits, and underscore" — but the user
# may also type accented characters (e.g. "niño") or short tokens like
# "OK". We treat anything that's a Unicode letter/digit/underscore as
# a word character; the surrounding "non-word" boundary keeps the
# match from firing on substrings (e.g. "fail" should not match
# "failful" or "failsafe").
_WORD_PATTERN = re.compile(
    r"(?<![\w])(\w+)(?![\w])",
    re.UNICODE,
)


def find_trigger_words(
    phrase: str,
    trigger_words: Iterable[str],
) -> List[str]:
    """Return the list of trigger words that appear in ``phrase``.

    Matching is case-insensitive and word-boundary aware. The order of
    the returned list mirrors the order in which the words appear in
    ``phrase`` (so a phrase with two trigger words yields both, in
    spoken order). Unknown trigger words (empty, None) are skipped.

    Args:
        phrase: Transcribed text from the STT engine.
        trigger_words: Iterable of trigger words (case insensitive).
            Empty or whitespace-only strings are ignored.

    Returns:
        List of trigger words that were matched, normalized to the
        same case as the inputs to ``trigger_words``.
    """

    if not phrase:
        return []
    norm_to_original: Dict[str, str] = {}
    for raw in trigger_words:
        if not raw:
            continue
        normalized = raw.strip().lower()
        if not normalized:
            continue
        if normalized not in norm_to_original:
            norm_to_original[normalized] = raw
    if not norm_to_original:
        return []

    # Tokenize the phrase on word boundaries, case-insensitive.
    tokens = [(m.group(1).lower(), m.start()) for m in _WORD_PATTERN.finditer(phrase)]
    tokens.sort(key=lambda t: t[1])

    hits: List[str] = []
    for token, _pos in tokens:
        if token in norm_to_original and norm_to_original[token] not in hits:
            hits.append(norm_to_original[token])
    return hits


# ---------------------------------------------------------------------------
# Dispatcher with cooldown
# ---------------------------------------------------------------------------


Callback = Callable[[str], None]


class TriggerMatcher:
    """Match transcribed phrases against a set of trigger words.

    Example:
        >>> matcher = TriggerMatcher(cooldown_ms=1500, clock=time.monotonic)
        >>> matcher.register("fail", lambda word: print("play fail sound"))
        >>> matcher.dispatch("oh what a fail")
        play fail sound
        ['fail']

    The clock and on_skip hook are injectable for tests; production
    callers can use the defaults.
    """

    def __init__(
        self,
        cooldown_ms: int = 1500,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_skip: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        if cooldown_ms < 0:
            raise ValueError("cooldown_ms must be >= 0")
        self._cooldown_seconds = cooldown_ms / 1000.0
        self._clock = clock
        self._on_skip = on_skip
        self._callbacks: Dict[str, Callback] = {}
        self._last_fired: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._fire_count = 0
        self._skip_count = 0

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @property
    def cooldown_ms(self) -> int:
        return int(self._cooldown_seconds * 1000)

    @cooldown_ms.setter
    def cooldown_ms(self, value: int) -> None:
        if value < 0:
            raise ValueError("cooldown_ms must be >= 0")
        self._cooldown_seconds = value / 1000.0

    def register(self, word: str, callback: Callback) -> None:
        """Register a callback to fire when ``word`` is matched.

        ``word`` is normalized (stripped + lowercased). Re-registering
        the same word replaces the previous callback.
        """

        normalized = self._normalize(word)
        if normalized is None:
            raise ValueError(f"Cannot register empty trigger word: {word!r}")
        with self._lock:
            self._callbacks[normalized] = callback

    def unregister(self, word: str) -> bool:
        normalized = self._normalize(word)
        if normalized is None:
            return False
        with self._lock:
            return self._callbacks.pop(normalized, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._callbacks.clear()
            self._last_fired.clear()

    def registered_words(self) -> List[str]:
        with self._lock:
            return list(self._callbacks.keys())

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @property
    def fire_count(self) -> int:
        return self._fire_count

    @property
    def skip_count(self) -> int:
        return self._skip_count

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match(self, phrase: str) -> List[str]:
        """Return the trigger words matched in ``phrase`` (no callbacks)."""

        with self._lock:
            registered = list(self._callbacks.keys())
        return find_trigger_words(phrase, registered)

    def dispatch(self, phrase: str) -> List[str]:
        """Match and fire callbacks for any trigger words in ``phrase``.

        Returns the list of words that were fired (excluding any that
        were skipped due to cooldown). Callbacks are invoked on the
        caller's thread.
        """

        with self._lock:
            registered = list(self._callbacks.keys())
        if not registered:
            return []
        matched = find_trigger_words(phrase, registered)
        fired: List[str] = []
        now = self._clock()
        # Use -inf as "never fired" sentinel so the very first dispatch
        # is not blocked by an apparent elapsed time of (now - 0.0).
        never_fired = float("-inf")
        for word in matched:
            normalized = self._normalize(word)
            if normalized is None:
                continue
            with self._lock:
                callback = self._callbacks.get(normalized)
                last = self._last_fired.get(normalized, never_fired)
            if callback is None:
                continue
            elapsed = now - last
            if elapsed < self._cooldown_seconds:
                with self._lock:
                    self._skip_count += 1
                remaining_ms = int((self._cooldown_seconds - elapsed) * 1000)
                if self._on_skip is not None:
                    try:
                        self._on_skip(normalized, remaining_ms / 1000.0)
                    except Exception:  # pragma: no cover - user callback
                        _LOGGER.exception("Trigger on_skip callback raised")
                _LOGGER.debug(
                    "Trigger '%s' skipped: cooldown active for another %d ms",
                    word,
                    remaining_ms,
                )
                continue
            try:
                callback(word)
            except Exception:  # pragma: no cover - user callback
                _LOGGER.exception("Trigger callback for '%s' raised", word)
                continue
            with self._lock:
                self._last_fired[normalized] = now
                self._fire_count += 1
            fired.append(word)
        if fired:
            _LOGGER.info(
                "Voice triggers fired: %s (phrase=%r)", ", ".join(fired), phrase
            )
        return fired

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(word: str) -> Optional[str]:
        if not word:
            return None
        normalized = word.strip().lower()
        return normalized or None


def build_matcher_from_shortcuts(
    shortcuts: Iterable,
    *,
    cooldown_ms: int = 1500,
    clock: Callable[[], float] = time.monotonic,
) -> Tuple[TriggerMatcher, List[Tuple[str, str]]]:
    """Build a :class:`TriggerMatcher` from an iterable of Shortcut objects.

    Returns a tuple of ``(matcher, duplicates)`` where ``duplicates`` is
    a list of ``(normalized_word, shortcut_label)`` for every shortcut
    that shares a trigger word with an earlier one. The caller can log
    these so the user is aware of the configuration conflict.
    """

    matcher = TriggerMatcher(cooldown_ms=cooldown_ms, clock=clock)
    seen: Dict[str, str] = {}
    duplicates: List[Tuple[str, str]] = []
    for shortcut in shortcuts:
        word = getattr(shortcut, "normalized_trigger_word", lambda: None)()
        if not word:
            continue
        if word in seen:
            duplicates.append((word, shortcut.label()))
            # Keep the first registration; later duplicates are skipped
            # so the matcher only fires once per utterance.
            continue
        seen[word] = shortcut.label()
        matcher.register(word, lambda w=word, sc=shortcut: _fire_shortcut(sc, w))
    return matcher, duplicates


def _fire_shortcut(shortcut, word: str) -> None:
    """Default callback used by :func:`build_matcher_from_shortcuts`.

    The actual application wiring (sound + overlay) is performed by
    the existing :class:`stream_companion.application.Application`
    pipeline. This module just emits a structured log message so
    external observers (e.g. tests, the tray) can correlate triggers
    with their effects.
    """

    _LOGGER.info("Trigger word '%s' matched shortcut %s", word, shortcut.label())
