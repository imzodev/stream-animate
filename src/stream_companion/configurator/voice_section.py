"""The "Voice Commands" settings tab.

Edits the switches and wake words. The command list itself is shown
read-only and edited in ``shortcuts.json`` (an "Open config file" button is
provided) — a full command editor is out of scope for v1.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..voice_commands import VoiceCommandsConfig


def describe_action(action: dict) -> str:
    """One short human-readable line for an action dict."""

    kind = action.get("type")
    if kind == "keys":
        return f"press {action.get('keys')}"
    if kind == "media":
        return f"media {action.get('key')}"
    if kind == "open":
        return f"open {action.get('target')}"
    if kind == "focus_window":
        return f"focus “{action.get('title')}”"
    if kind == "switch_tab":
        return f"{action.get('app')} tab “{action.get('tab')}”"
    if kind == "type_text":
        return f"type “{action.get('text')}”"
    if kind == "dictation":
        return f"dictation {action.get('state')}"
    if kind == "wait":
        return f"wait {action.get('ms')} ms"
    return str(kind)


class VoiceCommandsSection(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = VoiceCommandsConfig()
        self._config_path: Optional[Path] = None
        self._build_ui()
        self._on_enabled_toggled()

    # -- public API ---------------------------------------------------------

    def populate(
        self, config: Optional[VoiceCommandsConfig], config_path: Optional[Path] = None
    ) -> None:
        self._config = config or VoiceCommandsConfig()
        self._config_path = config_path
        cfg = self._config
        self._enabled.setChecked(cfg.enabled)
        self._wake_words.setText(", ".join(cfg.wake_words))
        self._dictation_editing.setChecked(cfg.dictation_editing)
        self._ai_fallback.setChecked(cfg.ai_fallback)
        lines = []
        for command in cfg.commands:
            phrases = " / ".join(command.phrases)
            steps = " → ".join(describe_action(a) for a in command.actions)
            lines.append(f"• {phrases}\n      {steps}")
        self._commands_view.setPlainText(
            "\n".join(lines)
            if lines
            else "No commands yet — add them in the config file."
        )
        self._on_enabled_toggled()

    def wake_words(self) -> List[str]:
        return [w.strip() for w in self._wake_words.text().split(",") if w.strip()]

    def read(self) -> dict:
        """The full ``voice_commands`` block (commands kept as loaded)."""

        cfg = dataclasses.replace(
            self._config,
            enabled=self._enabled.isChecked(),
            wake_words=tuple(self.wake_words()) or self._config.wake_words,
            dictation_editing=self._dictation_editing.isChecked(),
            ai_fallback=self._ai_fallback.isChecked(),
        )
        return cfg.to_dict()

    def validate(self) -> List[str]:
        if self._enabled.isChecked() and not self.wake_words():
            return ["Voice commands: enter at least one wake word."]
        return []

    # -- internals ----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._enabled = QCheckBox("Enable voice commands")
        self._enabled.setToolTip(
            "Say the wake word, then a command: “computadora, terminal servidor”."
        )
        self._enabled.toggled.connect(self._on_enabled_toggled)
        layout.addWidget(self._enabled)

        wake_row = QHBoxLayout()
        wake_row.addWidget(QLabel("Wake words:"))
        self._wake_words = QLineEdit()
        self._wake_words.setPlaceholderText("computadora, compu")
        self._wake_words.setToolTip(
            "Comma-separated. A phrase must START with one of these to run a "
            "command, so normal talking on stream never triggers actions. "
            "Add variants if Whisper spells your wake word differently."
        )
        wake_row.addWidget(self._wake_words, 1)
        layout.addLayout(wake_row)

        self._dictation_editing = QCheckBox(
            "Dictation editing while typing (“nueva línea”, “borra eso”, "
            "“deja de escribir”…)"
        )
        self._dictation_editing.setToolTip(
            "Only when the whole phrase is the command, so dictating a sentence "
            "that contains those words still types it."
        )
        layout.addWidget(self._dictation_editing)

        self._ai_fallback = QCheckBox(
            "Ask the AI when a command isn't in the list (uses the AI Assistant's LLM)"
        )
        self._ai_fallback.setToolTip(
            "The AI can only use the same safe actions as your commands. It "
            "never presses Enter and never types into a terminal. It sees your "
            "open window titles and tab names to pick the right one."
        )
        layout.addWidget(self._ai_fallback)

        group = QGroupBox("Commands")
        group_layout = QVBoxLayout(group)
        self._commands_view = QPlainTextEdit()
        self._commands_view.setReadOnly(True)
        group_layout.addWidget(self._commands_view)
        hint = QLabel(
            "<i>Commands are edited in <b>shortcuts.json</b> under "
            "<code>voice_commands.commands</code>. A phrase can end with "
            "<code>{name}</code> to capture the rest of what you say.</i>"
        )
        hint.setWordWrap(True)
        group_layout.addWidget(hint)
        btn_row = QHBoxLayout()
        self._open_btn = QPushButton("Open config file")
        self._open_btn.clicked.connect(self._open_config_file)
        btn_row.addWidget(self._open_btn)
        btn_row.addStretch(1)
        group_layout.addLayout(btn_row)
        layout.addWidget(group, 1)

    def _on_enabled_toggled(self, *_args) -> None:
        on = self._enabled.isChecked()
        for w in (self._wake_words, self._dictation_editing, self._ai_fallback):
            w.setEnabled(on)

    def _open_config_file(self) -> None:
        if self._config_path is not None and self._config_path.exists():
            os.startfile(str(self._config_path))  # type: ignore[attr-defined]
