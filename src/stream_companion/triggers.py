"""Voice-trigger matching for the Streaming Companion Tool.

The STT engine transcribes short audio chunks and emits the resulting
text. The application can attach one or more trigger words or
phrases to a :class:`~stream_companion.models.Shortcut`; when the
transcribed phrase contains the trigger (case-insensitive,
word-boundary match), the shortcut fires.

Two trigger sources are supported on each shortcut:

* ``trigger_word`` — a single word (legacy field).
* ``trigger_phrases`` — a list of one or more phrases, each of which
  is a string of one or more words. Matching requires the tokens of
  the phrase to appear contiguously in the transcribed phrase
  (case-insensitive, word-boundary aware).

This module owns the matching logic — keeping it out of the engine
keeps the engine decoupled from application-level concepts. The
:func:`TriggerMatcher` class:

* Holds a mapping of normalized trigger phrase → callback.
* Maintains a per-phrase cooldown timestamp so a single utterance
  with overlapping chunks does not re-fire the same shortcut many
  times.
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


def _tokenize(phrase: str) -> List[Tuple[str, int]]:
    """Return the phrase's word tokens (lowercased) and their char offsets.

    Words are split on the Unicode-aware word boundary, and ordering
    matches the original string.
    """

    if not phrase:
        return []
    return [(m.group(1).lower(), m.start()) for m in _WORD_PATTERN.finditer(phrase)]


def _normalize_candidate(candidate: str) -> Tuple[str, ...]:
    """Normalize a candidate phrase into a tuple of lowercase tokens.

    Whitespace-separated words, in order. Empty/whitespace candidates
    return an empty tuple (caller should treat as "no trigger").
    """

    if not candidate:
        return ()
    parts = _WORD_PATTERN.findall(candidate.lower())
    return tuple(p for p in parts if p)


def find_trigger_phrases(
    phrase: str,
    candidates: Iterable[str],
) -> List[str]:
    """Return the list of trigger phrases that appear in ``phrase``.

    Matching rules:

    * Case-insensitive.
    * Word-boundary aware (so "fail" doesn't match "failful").
    * **Contiguous** — the candidate's tokens must appear as a
      contiguous subsequence of the phrase's tokens, in order.
    * The order of the returned list mirrors the order in which the
      candidate phrases appear in ``phrase``. Multiple matches of the
      same candidate are deduplicated.

    Args:
        phrase: Transcribed text from the STT engine.
        candidates: Iterable of trigger words or phrases (case
            insensitive). A single-word candidate matches just like
            a single-token phrase. Empty/whitespace candidates are
            ignored.

    Returns:
        List of candidate strings that matched, in the same casing as
        they were passed in (after stripping).
    """

    if not phrase:
        return []
    tokens = _tokenize(phrase)
    if not tokens:
        return []

    # Pre-normalize candidates: keep the original string (for logging
    # and as the matcher key) and a tuple of lowercased tokens for
    # the actual matching.
    norm_candidates: List[Tuple[str, Tuple[str, ...]]] = []
    for raw in candidates:
        if not raw:
            continue
        stripped = raw.strip()
        if not stripped:
            continue
        token_tuple = _normalize_candidate(stripped)
        if not token_tuple:
            continue
        norm_candidates.append((stripped.lower(), token_tuple))

    if not norm_candidates:
        return []

    # Sliding window: for each starting position, look for any
    # candidate whose tokens match the contiguous slice.
    hits: List[str] = []
    seen: set = set()
    token_strings = [t for t, _ in tokens]
    n_tokens = len(token_strings)
    for i in range(n_tokens):
        for original, candidate_tokens in norm_candidates:
            cand_len = len(candidate_tokens)
            if cand_len == 0 or i + cand_len > n_tokens:
                continue
            window = token_strings[i : i + cand_len]
            if window == list(candidate_tokens):
                if original not in seen:
                    seen.add(original)
                    hits.append(original)
                # Don't break — the same candidate can be found at
                # multiple positions in the phrase; we still want to
                # record the first occurrence and skip the rest.
                break
    return hits


# Backward-compat alias for the old name; the behavior is the same
# when candidates are single words.
def find_trigger_words(
    phrase: str,
    trigger_words: Iterable[str],
) -> List[str]:
    """Deprecated alias for :func:`find_trigger_phrases`."""

    return find_trigger_phrases(phrase, trigger_words)


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

    Pulls all voice triggers from each shortcut — both the legacy
    ``trigger_word`` and the new ``trigger_phrases`` list — and
    registers one callback per phrase. Each phrase is keyed in the
    matcher's cooldown map by its normalized text, so a phrase fires
    once per cooldown window.

    Returns a tuple of ``(matcher, duplicates)`` where ``duplicates``
    is a list of ``(normalized_phrase, shortcut_label)`` for every
    shortcut that shares a trigger phrase with an earlier one. The
    caller can log these so the user is aware of the configuration
    conflict.
    """

    matcher = TriggerMatcher(cooldown_ms=cooldown_ms, clock=clock)
    seen: Dict[str, str] = {}
    duplicates: List[Tuple[str, str]] = []
    for shortcut in shortcuts:
        all_phrases = getattr(shortcut, "all_trigger_phrases", lambda: [])()
        for phrase in all_phrases:
            if phrase in seen:
                duplicates.append((phrase, shortcut.label()))
                continue
            seen[phrase] = shortcut.label()
            matcher.register(
                phrase,
                lambda p=phrase, sc=shortcut: _fire_shortcut(sc, p),
            )
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
