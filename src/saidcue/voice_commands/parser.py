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
            return " ".join(words[len(wake_tokens) :])
    return None


class CommandMatcher:
    """Match a command string against phrase templates.

    A template is plain words, optionally ending with one ``{slot}`` that
    captures the rest of the phrase: ``"terminal {name}"`` matches
    "terminal web server" with ``{"name": "web server"}``. The most
    specific template (most literal words) wins. Commands are any objects
    with ``phrases`` and ``actions``.
    """

    def __init__(self, commands: Sequence[Any]) -> None:
        entries: List[Tuple[int, Tuple[str, ...], Optional[str], Any]] = []
        for command in commands:
            for phrase in command.phrases:
                norm = normalize(phrase)
                slot = None
                m = _SLOT.search(norm)
                if m:
                    if not norm.endswith(m.group(0)):
                        raise ValueError(
                            f"slot must be at the end of the phrase: {phrase!r}"
                        )
                    slot = m.group(1)
                    norm = norm[: m.start()].strip()
                literal = tuple(norm.split())
                entries.append((len(literal), literal, slot, command))
        # Most literal words first; exact templates before slot templates.
        entries.sort(key=lambda e: (e[0], e[2] is None), reverse=True)
        self._entries = entries

    def match(self, command_text: str) -> Optional[Tuple[Any, Dict[str, str]]]:
        words = normalize(command_text).split()
        for _n, literal, slot, command in self._entries:
            if words[: len(literal)] != list(literal):
                continue
            rest = words[len(literal) :]
            if slot is None:
                if not rest:
                    return command, {}
            elif rest:
                return command, {slot: " ".join(rest)}
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
