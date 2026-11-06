"""The "Voice Commands Guide" dialog.

Built from the live catalog (:mod:`..voice_commands.catalog`), so it can
never drift from what the app actually understands the way a hand-written
copy could. Opened from a button in :class:`.voice_section.VoiceCommandsSection`.
"""

from __future__ import annotations

from html import escape
from typing import Optional

from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..voice_commands import COMMANDS, WAKE_WORD_PRESETS
from ..voice_commands.dictation_edits import DEFAULT_EDITS

_CODE = 'style="border:1px solid #888; border-radius:4px; padding:1px 6px; white-space:nowrap;"'
_DIM = 'style="color:#777;"'
_SLOT = 'style="font-style:italic; color:#777;"'


def _phrase(text: str) -> str:
    """One phrase as a code chip, with ``{slot}`` italicised."""

    shown = escape(text).replace("{", "<i " + _SLOT + ">{").replace("}", "}</i>")
    return f'<code {_CODE}>{shown}</code>'


def _phrases(phrases) -> str:
    return "&nbsp; ".join(_phrase(p) for p in phrases)


def _command_row(command) -> str:
    return (
        "<tr>"
        f'<td valign="top" style="padding:4px 10px 4px 0;">{_phrases(command.phrases)}</td>'
        f'<td valign="top" style="padding:4px 0;">{escape(command.description)}</td>'
        "</tr>"
    )


def _matches(command, query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(command.phrases) + " " + command.description
    return query in haystack.lower()


def build_guide_html(query: str = "") -> str:
    """The full guide, or just the commands matching ``query`` (case-insensitive)."""

    q = query.strip().lower()
    groups: "dict[str, list]" = {}
    for command in COMMANDS:
        if _matches(command, q):
            groups.setdefault(command.group, []).append(command)

    parts = ['<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif; font-size:13px;">']

    if not q:
        parts.append(
            "<h2>How a command fires</h2>"
            "<p>Say your <b>wake word</b>, then the command, right after it — "
            f'e.g. <code {_CODE}>computer, skip ad</code>. Normal talk never '
            "triggers anything, because the phrase has to start with the wake "
            "word.</p>"
            "<p>The on-screen pill confirms what happened: a green check for "
            "success, a red cross if it failed, or “Thinking…” while the AI "
            "plans a reply.</p>"
        )

    for group, commands in groups.items():
        parts.append(f"<h3>{escape(group)}</h3>")
        parts.append('<table cellspacing="0">')
        parts.extend(_command_row(c) for c in commands)
        parts.append("</table>")

    if not groups:
        parts.append(f"<p {_DIM}>No command matches “{escape(query)}”.</p>")

    if not q:
        edit_rows = "".join(
            "<tr>"
            f'<td valign="top" style="padding:4px 10px 4px 0;">{_phrases(phrases)}</td>'
            f'<td valign="top" style="padding:4px 0;"></td>'
            "</tr>"
            for phrases in DEFAULT_EDITS.values()
        )
        parts.append(
            "<h2>Editing while you dictate</h2>"
            "<p>No wake word needed — but only when the <b>whole</b> phrase is "
            "the command, said while dictation is on. A sentence that merely "
            "contains the words is still typed.</p>"
            f'<table cellspacing="0">{edit_rows}</table>'
        )

        parts.append(
            "<h2>When nothing matches</h2>"
            "<p>If “Ask the AI…” is on, a wake-word phrase that matches no "
            "command above goes to your configured LLM instead. It can only "
            "use the same actions as the built-in commands — open an app or "
            "URL, focus a window, switch a tab, press media keys, set volume, "
            "scroll, type text, or toggle dictation.</p>"
            "<p>It can <b>never</b> click anything, press Enter, type into a "
            "terminal, or run a shell command, and it's capped at 8 steps. "
            "Each unmatched command costs roughly 1.5k tokens, since your "
            "open window titles and tab names are sent along for context.</p>"
        )

        presets = ", ".join(f'<code {_CODE}>{escape(w)}</code>' for w in WAKE_WORD_PRESETS)
        parts.append(
            "<h2>Making it yours</h2>"
            f"<p><b>Wake word:</b> pick a preset or type your own — {presets}. "
            'Two-word words like "hey computer" misfire less, because ordinary '
            "sentences rarely start with them.</p>"
            "<p><b>Turning commands off:</b> uncheck any command in the list "
            "above; there's no custom-phrase editor, but anything you don't "
            "want can be switched off.</p>"
            "<p><b>Language:</b> commands are spoken in English. If "
            "Speech-to-Text's language isn't <code>auto</code> or "
            "<code>en</code>, Whisper may spell the words differently and "
            "they won't match.</p>"
            "<p><b>After a change:</b> restart the app — settings changes "
            "don't take effect in a session that's already running.</p>"
            "<p><b>Platform:</b> Windows only. Clicking, scrolling, tabs and "
            "exact volume all go through Windows UI Automation and Core "
            "Audio.</p>"
        )

    parts.append("</body>")
    return "".join(parts)


class VoiceGuideDialog(QDialog):
    """A searchable guide to every voice command, generated from the catalog."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Voice Commands Guide")
        self.resize(640, 640)

        layout = QVBoxLayout(self)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search a phrase or action, e.g. “scroll”, “tab”, “volume”…")
        self._search.textChanged.connect(self._refresh)
        layout.addWidget(self._search)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        layout.addWidget(self._browser, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._refresh("")

    def _refresh(self, query: str) -> None:
        self._browser.setHtml(build_guide_html(query))
