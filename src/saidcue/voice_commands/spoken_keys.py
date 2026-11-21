"""Turn spoken key names into hotkey combos.

"control shift t" → ``"<ctrl>+<shift>+t"``, "backspace three times" →
(``"<backspace>"``, 3). Whisper writes keys many ways ("Ctrl+Shift+T",
"control-shift-T", "page down"), so everything goes through
:func:`~.parser.normalize` first and multi-word names are matched before
single words.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .parser import normalize

MAX_REPEAT = 50

MODIFIERS: Dict[str, str] = {
    "control": "<ctrl>", "ctrl": "<ctrl>", "ctl": "<ctrl>", "control key": "<ctrl>",
    "shift": "<shift>", "mayus": "<shift>", "mayusculas": "<shift>",
    "alt": "<alt>", "alternate": "<alt>", "option": "<alt>",
    "windows": "<cmd>", "win": "<cmd>", "windows key": "<cmd>", "super": "<cmd>",
    "command": "<cmd>",
}  # fmt: skip

KEYS: Dict[str, str] = {
    "enter": "<enter>", "return": "<enter>", "intro": "<enter>",
    "escape": "<esc>", "esc": "<esc>", "escape key": "<esc>", "escapar": "<esc>",
    "tab": "<tab>", "tabulador": "<tab>",
    "backspace": "<backspace>", "back space": "<backspace>", "borrar": "<backspace>",
    "delete": "<delete>", "del": "<delete>", "suprimir": "<delete>",
    "space": "<space>", "spacebar": "<space>", "space bar": "<space>", "espacio": "<space>",
    "home": "<home>", "inicio": "<home>", "end": "<end>", "fin": "<end>",
    "page up": "<page_up>", "pageup": "<page_up>",
    "page down": "<page_down>", "pagedown": "<page_down>",
    "up": "<up>", "up arrow": "<up>", "arrow up": "<up>", "arriba": "<up>",
    "down": "<down>", "down arrow": "<down>", "arrow down": "<down>", "abajo": "<down>",
    "left": "<left>", "left arrow": "<left>", "arrow left": "<left>", "izquierda": "<left>",
    "right": "<right>", "right arrow": "<right>", "arrow right": "<right>",
    "derecha": "<right>",
    "insert": "<insert>", "print screen": "<print_screen>", "menu": "<menu>",
    "context menu": "<menu>", "caps lock": "<caps_lock>",
    "minus": "-", "dash": "-", "comma": ",", "period": ".", "dot": ".",
    "slash": "/", "backslash": "\\", "semicolon": ";", "equals": "=",
}  # fmt: skip

# Spoken letters ("bee", "see") and the NATO alphabet, for "control see".
_LETTERS: Dict[str, str] = {
    "a": "a", "b": "b", "bee": "b", "c": "c", "see": "c", "sea": "c", "d": "d",
    "e": "e", "f": "f", "g": "g", "h": "h", "i": "i", "j": "j", "jay": "j",
    "k": "k", "kay": "k", "l": "l", "m": "m", "n": "n", "o": "o", "p": "p",
    "pee": "p", "q": "q", "queue": "q", "r": "r", "are": "r", "s": "s",
    "t": "t", "tea": "t", "tee": "t", "u": "u", "you": "u", "v": "v",
    "w": "w", "x": "x", "ex": "x", "y": "y", "why": "y", "z": "z", "zee": "z",
    "alpha": "a", "bravo": "b", "charlie": "c", "delta": "d", "echo": "e",
    "foxtrot": "f", "golf": "g", "hotel": "h", "india": "i", "juliet": "j",
    "kilo": "k", "lima": "l", "mike": "m", "november": "n", "oscar": "o",
    "papa": "p", "quebec": "q", "romeo": "r", "sierra": "s", "tango": "t",
    "uniform": "u", "victor": "v", "whiskey": "w", "xray": "x", "x ray": "x",
    "yankee": "y", "zulu": "z",
}  # fmt: skip

_DIGITS: Dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9",
}  # fmt: skip

_FUNCTION_KEYS: Dict[str, str] = {f"f{n}": f"<f{n}>" for n in range(1, 13)}
_FUNCTION_KEYS.update({f"f {n}": f"<f{n}>" for n in range(1, 13)})

# Names that count as a key only next to a modifier ("control a"), never
# alone: a bare "a", "you" or "one" is ordinary speech, not a key press.
_COMBO_ONLY = {**_LETTERS, **_DIGITS, **{str(d): str(d) for d in range(10)}}

_NUMBER_WORDS = {
    "once": 1, "twice": 2, "thrice": 3,
    "one": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4, "for": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}  # fmt: skip

_TIMES = ("times", "time", "veces", "vez")
_FILLER = frozenset({"the", "key", "button", "la", "el", "tecla"})


def parse_count(text: str) -> Optional[int]:
    """``"3"``, ``"three"``, ``"twice"`` → an int 1-50, else ``None``."""

    words = normalize(text).split()
    if words and words[-1] in _TIMES:
        words.pop()
    if len(words) != 1:
        return None
    word = words[0]
    if word.isdigit():
        n = int(word)
    else:
        n = _NUMBER_WORDS.get(word)
    if n is None or not 1 <= n <= MAX_REPEAT:
        return None
    return n


def split_count(text: str) -> Tuple[str, int]:
    """Peel a trailing repeat count off: "backspace three times" → ("backspace", 3)."""

    words = normalize(text).split()
    if len(words) >= 2 and words[-1] in _TIMES:
        n = parse_count(" ".join(words[-2:]))
        if n is not None:
            return " ".join(words[:-2]), n
    if len(words) >= 2 and words[-1] in ("once", "twice", "thrice"):
        return " ".join(words[:-1]), _NUMBER_WORDS[words[-1]]
    return " ".join(words), 1


def _take(words: List[str], table: Dict[str, str]) -> Optional[Tuple[str, int]]:
    """Longest name in ``table`` at the start of ``words``: (key, words used)."""

    for size in (3, 2, 1):
        if len(words) >= size:
            name = " ".join(words[:size])
            if name in table:
                return table[name], size
    return None


def parse_spoken_combo(text: str) -> Optional[str]:
    """``"control shift t"`` → ``"<ctrl>+<shift>+t"``, else ``None``.

    Accepts any number of modifiers followed by exactly one key. A bare
    letter or digit only counts after a modifier. The Windows key alone
    is a key of its own (it opens the Start menu).
    """

    words = [w for w in normalize(text.replace("+", " ")).split() if w not in _FILLER]
    if not words:
        return None
    mods: List[str] = []
    while words:
        taken = _take(words, MODIFIERS)
        if taken is None:
            break
        mod, used = taken
        if mod not in mods:
            mods.append(mod)
        words = words[used:]
    if not words:
        # Only modifiers: "windows" alone opens the Start menu. A lone
        # "control" or "shift" does nothing useful, so it isn't a key.
        return "<cmd>" if mods == ["<cmd>"] else None
    tables = [KEYS, _FUNCTION_KEYS]
    if mods:
        tables.append(_COMBO_ONLY)
    for table in tables:
        taken = _take(words, table)
        if taken is not None and taken[1] == len(words):
            return "+".join(mods + [taken[0]])
    return None


def parse_key_press(text: str) -> Optional[Tuple[str, int]]:
    """A spoken key with an optional repeat: (combo, count) or ``None``."""

    rest, count = split_count(text)
    combo = parse_spoken_combo(rest)
    if combo is None:
        return None
    return combo, count


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4,
    "for": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ate": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}  # fmt: skip


def parse_number(text: str, limit: int = 999) -> Optional[int]:
    """A spoken whole number 1..``limit``: "14", "fourteen", "forty two",
    "one hundred five", "treinta y dos". ``None`` otherwise."""

    words = [
        w for w in normalize(text.replace("-", " ")).split() if w not in ("and", "y")
    ]
    if not words:
        return None
    if len(words) == 1 and words[0].isdigit():
        n = int(words[0])
        return n if 1 <= n <= limit else None
    total = 0
    current = 0
    for word in words:
        if word in _UNITS:
            if current % 10 or (current and current < 20):
                return None  # "five six" is not a number
            current += _UNITS[word]
        elif word in _TENS:
            if current % 100:
                return None
            current += _TENS[word]
        elif word in ("hundred", "cien", "ciento"):
            current = (current or 1) * 100
            total += current
            current = 0
        elif word.isdigit() and current == 0 and total == 0 and len(words) == 1:
            current = int(word)
        else:
            return None
    n = total + current
    return n if 1 <= n <= limit else None
