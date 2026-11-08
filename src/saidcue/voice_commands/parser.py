"""Wake-word detection and command-phrase matching.

Everything is compared after :func:`normalize`: lowercase, accents and
punctuation removed, whitespace collapsed. Whisper writes the same speech
many ways ("Computer, open YouTube." / "computer open youtube").
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SLOT = re.compile(r"\{(\w+)\}")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(
        ch if (ch.isalnum() or ch.isspace() or ch in "{}_") else " "
        for ch in no_accents
    )
    return " ".join(cleaned.split())


def split_wake_word(text: str, wake_words: Iterable[str]) -> Optional[str]:
    """Return the command after a leading wake word, else ``None``.

    The wake word must be the first word(s) of the phrase. Returns ``""``
    when the phrase is *only* the wake word.
    """

    norm = normalize(text)
    words = norm.split()
    # Longest wake word first so "hey computer" beats "hey".
    for wake in sorted((normalize(w) for w in wake_words), key=len, reverse=True):
        if not wake:
            continue
        wake_tokens = wake.split()
        if words[: len(wake_tokens)] == wake_tokens:
            rest = words[len(wake_tokens) :]
            return " ".join(_collapse_repeat(rest, wake_tokens))
    return None


def _collapse_repeat(words: List[str], wake_tokens: List[str]) -> List[str]:
    """Turn ``X <wake> X`` into ``X``.

    People (and Whisper) often say a command twice in one breath, e.g.
    "computer skip ad computer skip ad". The repeat would otherwise make
    the whole phrase unmatchable. Only an exact repeat is collapsed, so a
    command that legitimately contains the wake word is left alone.
    """

    n = len(wake_tokens)
    for i in range(1, len(words) - n):
        if words[i : i + n] == wake_tokens and words[:i] == words[i + n :]:
            return words[:i]
    return words


class CommandMatcher:
    """Match a command string against phrase templates.

    A template is plain words, optionally with one ``{slot}`` that
    captures the words in between: ``"terminal {name}"`` matches
    "terminal web server" with ``{"name": "web server"}``, and
    ``"{name} tab"`` matches "youtube tab" with ``{"name": "youtube"}``.
    The slot may sit anywhere, so a template can have literal words both
    before and after it.

    The most specific template (most literal words) wins. Commands are
    any objects with ``phrases`` and ``actions``.
    """

    def __init__(self, commands: Sequence[Any]) -> None:
        entries: List[
            Tuple[int, Tuple[str, ...], Tuple[str, ...], Optional[str], Any]
        ] = []
        for command in commands:
            for phrase in command.phrases:
                norm = normalize(phrase)
                slot = None
                prefix: Tuple[str, ...] = ()
                suffix: Tuple[str, ...] = ()
                m = _SLOT.search(norm)
                if m:
                    slot = m.group(1)
                    prefix = tuple(norm[: m.start()].split())
                    suffix = tuple(norm[m.end() :].split())
                    if not prefix and not suffix:
                        raise ValueError(f"slot needs words around it: {phrase!r}")
                else:
                    prefix = tuple(norm.split())
                literal = len(prefix) + len(suffix)
                entries.append((literal, prefix, suffix, slot, command))
        # Most literal words first, then exact templates, then templates
        # anchored at the front. The last key matters for phrases like
        # "ask what is a browser tab": "ask {question}" has to win over
        # "{name} tab", or the question would switch tabs instead.
        entries.sort(
            key=lambda e: (e[0], e[3] is None, bool(e[1])),
            reverse=True,
        )
        self._entries = entries

    def match(self, command_text: str) -> Optional[Tuple[Any, Dict[str, str]]]:
        words = normalize(command_text).split()
        for _n, prefix, suffix, slot, command in self._entries:
            if prefix and tuple(words[: len(prefix)]) != prefix:
                continue
            if suffix and tuple(words[len(words) - len(suffix) :]) != suffix:
                continue
            if slot is None:
                if len(words) == len(prefix):
                    return command, {}
                continue
            start = len(prefix)
            end = len(words) - len(suffix)
            if end <= start:
                continue
            return command, {slot: " ".join(words[start:end])}
        return None


def fill_slots(value, slots: Dict[str, str]):
    """Substitute ``{slot}`` placeholders in an action value (recursively)."""

    if isinstance(value, str):
        return _SLOT.sub(lambda m: slots.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: fill_slots(v, slots) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_slots(v, slots) for v in value]
    return value
