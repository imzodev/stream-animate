"""The "Voice Commands" settings tab.

The wake word is picked from presets or typed freely. Commands are the
built-in English catalog; each one has a checkbox to turn it on or off.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..voice_commands import COMMANDS, WAKE_WORD_PRESETS, VoiceCommandsConfig
from ..voice_commands.dictation_edits import DEFAULT_EDITS
from .voice_guide import VoiceGuideDialog

_ID_ROLE = Qt.ItemDataRole.UserRole


def _say(phrase: str) -> str:
    return f"“{phrase.replace('{name}', '…')}”"


class VoiceCommandsSection(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = VoiceCommandsConfig()
        self._build_ui()
        self._on_enabled_toggled()

    # -- public API ---------------------------------------------------------

    def populate(self, config: Optional[VoiceCommandsConfig]) -> None:
        self._config = config or VoiceCommandsConfig()
        cfg = self._config
        self._enabled.setChecked(cfg.enabled)
        self._wake_word.setCurrentText(cfg.wake_word)
        self._dictation_editing.setChecked(cfg.dictation_editing)
        self._ai_fallback.setChecked(cfg.ai_fallback)
        off = set(cfg.disabled_commands)
        for item in self._command_items():
            checked = item.data(0, _ID_ROLE) not in off
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._on_enabled_toggled()

    def wake_word(self) -> str:
        return " ".join(self._wake_word.currentText().split())

    def read(self) -> dict:
        """The full ``voice_commands`` block."""

        catalog_ids = {c.id for c in COMMANDS}
        unchecked = [
            item.data(0, _ID_ROLE)
            for item in self._command_items()
            if item.checkState(0) != Qt.CheckState.Checked
        ]
        # Keep ids this version doesn't know so saving never re-enables them.
        unknown = [d for d in self._config.disabled_commands if d not in catalog_ids]
        cfg = dataclasses.replace(
            self._config,
            enabled=self._enabled.isChecked(),
            wake_word=self.wake_word() or self._config.wake_word,
            dictation_editing=self._dictation_editing.isChecked(),
            ai_fallback=self._ai_fallback.isChecked(),
            disabled_commands=tuple(unknown + unchecked),
        )
        return cfg.to_dict()

    def validate(self) -> List[str]:
        if self._enabled.isChecked() and not self.wake_word():
            return ["Voice commands: choose or type a wake word."]
        return []

    # -- internals ----------------------------------------------------------

    def _command_items(self):
        root = self._commands.invisibleRootItem()
        for g in range(root.childCount()):
            group = root.child(g)
            for i in range(group.childCount()):
                yield group.child(i)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._enabled = QCheckBox("Enable voice commands")
        self._enabled.setToolTip(
            "Say the wake word, then a command: “computer, next song”."
        )
        self._enabled.toggled.connect(self._on_enabled_toggled)
        layout.addWidget(self._enabled)

        wake_row = QHBoxLayout()
        wake_row.addWidget(QLabel("Wake word:"))
        self._wake_word = QComboBox()
        self._wake_word.setEditable(True)
        self._wake_word.addItems(WAKE_WORD_PRESETS)
        self._wake_word.setToolTip(
            "Pick one or type your own. A phrase must START with the wake word "
            "to run a command, so normal talking on stream never triggers "
            "actions. Two-word wake words like “hey computer” misfire less."
        )
        wake_row.addWidget(self._wake_word, 1)
        layout.addLayout(wake_row)

        edits = ", ".join(_say(p[0]) for p in DEFAULT_EDITS.values())
        self._dictation_editing = QCheckBox(
            "Dictation editing while typing (no wake word needed)"
        )
        self._dictation_editing.setToolTip(
            f"Say on its own while dictating: {edits}. Only when the whole "
            "phrase is the command, so a sentence containing those words is "
            "still typed."
        )
        layout.addWidget(self._dictation_editing)

        self._ai_fallback = QCheckBox(
            "Ask the AI when a command isn't in the list (uses the AI Assistant's LLM)"
        )
        self._ai_fallback.setToolTip(
            "The AI can only use the same safe actions as the built-in "
            "commands. It never presses Enter and never types into a terminal. "
            "It sees your open window titles and tab names to pick the right one."
        )
        layout.addWidget(self._ai_fallback)

        group = QGroupBox("Commands")
        group_layout = QVBoxLayout(group)
        header_row = QHBoxLayout()
        header_row.addStretch(1)
        guide_btn = QPushButton("📖 Commands guide")
        guide_btn.setToolTip("Open a searchable guide to every command, with examples.")
        guide_btn.clicked.connect(self._open_guide)
        header_row.addWidget(guide_btn)
        group_layout.addLayout(header_row)
        self._commands = QTreeWidget()
        self._commands.setColumnCount(2)
        self._commands.setHeaderLabels(["Say (after the wake word)", "Does"])
        self._commands.setRootIsDecorated(False)
        groups = {}
        for command in COMMANDS:
            parent = groups.get(command.group)
            if parent is None:
                parent = QTreeWidgetItem(self._commands, [command.group])
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                parent.setFirstColumnSpanned(True)
                groups[command.group] = parent
            item = QTreeWidgetItem(
                parent,
                [" / ".join(_say(p) for p in command.phrases), command.description],
            )
            item.setData(0, _ID_ROLE, command.id)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
        self._commands.expandAll()
        self._commands.resizeColumnToContents(0)
        group_layout.addWidget(self._commands)
        hint = QLabel(
            "<i>“…” is a name you say, e.g. “computer, terminal two” or "
            "“computer, tab gmail”. Uncheck a command to turn it off.</i>"
        )
        hint.setWordWrap(True)
        group_layout.addWidget(hint)
        layout.addWidget(group, 1)

    def _open_guide(self) -> None:
        VoiceGuideDialog(self).exec()

    def _on_enabled_toggled(self, *_args) -> None:
        on = self._enabled.isChecked()
        for w in (self._wake_word, self._dictation_editing, self._ai_fallback, self._commands):
            w.setEnabled(on)
