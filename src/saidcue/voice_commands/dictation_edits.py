"""Editing commands spoken while dictation typing is on.

These need no wake word, but only fire when the **whole utterance** is the
command. Utterances are split on pauses, so saying "new line" on its own
is distinct from dictating a sentence that merely contains those words.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional

from .parser import normalize

# edit name -> spoken phrases (matching ignores case and punctuation)
DEFAULT_EDITS: Dict[str, tuple] = {
    "newline": ("new line", "next line"),
    "submit": (
        "press enter", "hit enter", "enter", "press return", "hit return",
        "submit", "submit it", "send it", "send message", "send that",
        "presiona enter", "oprime enter", "presiona intro", "enviar",
    ),
    "paragraph": ("new paragraph",),
    "undo_phrase": ("scratch that", "delete that", "undo that"),
    "delete_word": ("delete word", "delete last word"),
    "select_all": ("select all",),
    "stop": ("stop typing", "stop dictation", "stop dictating"),
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
    if name == "submit":
        runner.press("<enter>")
        return "Enter"
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
