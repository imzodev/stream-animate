"""Editing commands spoken while dictation typing is on.

These need no wake word, but only fire when the **whole utterance** is the
command. Utterances are split on pauses, so saying "nueva línea" on its own
is distinct from dictating a sentence that merely contains those words.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional

from .parser import normalize

# edit name -> spoken phrases (written without accents; matching ignores them)
DEFAULT_EDITS: Dict[str, tuple] = {
    "newline": ("nueva linea", "salto de linea", "enter", "intro"),
    "paragraph": ("nuevo parrafo", "punto y aparte"),
    "undo_phrase": ("borra eso", "borrar eso", "deshacer", "deshaz eso"),
    "delete_word": ("borra la palabra", "borrar palabra", "borra palabra"),
    "select_all": ("selecciona todo", "seleccionar todo"),
    "stop": ("deja de escribir", "deja de dictar", "para de dictar", "para de escribir"),
}  # fmt: skip


def build_lookup(edits: Mapping[str, tuple] = DEFAULT_EDITS) -> Dict[str, str]:
    return {normalize(p): name for name, phrases in edits.items() for p in phrases}


def match_edit(text: str, lookup: Mapping[str, str]) -> Optional[str]:
    """Edit name if the whole utterance is an edit command, else ``None``."""

    return lookup.get(normalize(text))


def apply_edit(name: str, runner, typer, engine) -> str:
    """Perform edit ``name``. Returns a short feedback message."""

    if name == "newline":
        runner.press("<enter>")
        return "New line"
    if name == "paragraph":
        runner.press("<enter>", repeat=2)
        return "New paragraph"
    if name == "undo_phrase":
        removed = typer.forget_last() if typer is not None else ""
        if not removed:
            return "Nothing to erase"
        runner.press("<backspace>", repeat=len(removed))
        return "Erased last phrase"
    if name == "delete_word":
        runner.press("<ctrl>+<backspace>")
        return "Deleted word"
    if name == "select_all":
        runner.press("<ctrl>+a")
        return "Selected all"
    if name == "stop":
        if engine is not None:
            engine.set_active(False)
        return "Dictation off"
    raise ValueError(f"unknown edit {name!r}")
