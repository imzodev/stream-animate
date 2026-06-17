"""Main configurator window: composes the per-section widgets.

This module is the orchestrator. It owns the shortcut list, the global
settings tab, the STT section, and the shortcut details section. Each
section widget has a small read/populate/validate API and lives in
:mod:`.sections`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import model_downloader
from ..config_loader import ConfigError, load_full_config, save_config
from ..llm.config import LLMConfig
from ..models import ActivatorConfig, OverlayConfig, Shortcut
from ..overlay import OverlayWindow
from ..sound import SoundPlayer
from .llm_section import LLMSection
from .sections import STTSection, ShortcutSection
from .widgets import HotkeyCapture

_LOGGER = logging.getLogger(__name__)


class ConfiguratorWindow(QMainWindow):
    """Main window for the desktop configurator."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Streaming Companion - Configurator")
        self.resize(900, 600)

        self._shortcuts: List[Shortcut] = []
        self._activator: Optional[ActivatorConfig] = None
        self._stt_config = None
        self._llm_config: Optional[LLMConfig] = None
        self._current_index: Optional[int] = None
        self._sound_player = SoundPlayer()
        self._overlay_window = OverlayWindow()

        self._init_ui()
        self._load_shortcuts()

    def _init_ui(self) -> None:
        """Initialize the UI layout."""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout()

        # Left panel: shortcut list
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("<b>Shortcuts</b>"))

        self._list_widget = QListWidget()
        self._list_widget.currentRowChanged.connect(self._on_selection_changed)
        left_panel.addWidget(self._list_widget)

        list_buttons = QHBoxLayout()
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_shortcut)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._delete_shortcut)
        list_buttons.addWidget(self._add_btn)
        list_buttons.addWidget(self._delete_btn)
        left_panel.addLayout(list_buttons)

        # Right panel uses tabs
        tabs = QTabWidget()

        # Tab 1: Global Settings
        global_panel = self._build_global_panel()
        # Tab 2: Speech-to-Text
        self._stt_section = STTSection()
        # Tab 3: AI Assistant (LLM)
        self._llm_section = LLMSection()
        # Tab 4: Shortcut Details
        shortcut_panel = QWidget()
        shortcut_layout = QVBoxLayout()
        shortcut_panel.setLayout(shortcut_layout)
        self._shortcut_section = ShortcutSection()
        shortcut_layout.addWidget(self._shortcut_section)

        # Action buttons (Preview / Save) for the selected shortcut.
        # These are global actions so they live in the window, not the
        # section widget.
        action_buttons = QHBoxLayout()
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.clicked.connect(self._preview_shortcut)
        self._save_btn = QPushButton("Save Changes")
        self._save_btn.clicked.connect(self._save_changes)
        action_buttons.addWidget(self._preview_btn)
        action_buttons.addWidget(self._save_btn)
        shortcut_layout.addLayout(action_buttons)

        tabs.addTab(global_panel, "Global Settings")
        tabs.addTab(self._stt_section, "Speech-to-Text")
        tabs.addTab(self._llm_section, "AI Assistant")
        tabs.addTab(shortcut_panel, "Shortcuts")

        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(tabs, 2)
        central.setLayout(main_layout)

        self._update_ui_state()

    def _build_global_panel(self) -> QWidget:
        """Construct the Global Settings tab."""
        global_panel = QWidget()
        global_layout = QVBoxLayout()
        global_panel.setLayout(global_layout)
        global_layout.addWidget(QLabel("<b>Global Settings</b>"))

        global_layout.addWidget(QLabel("Activator Hotkey:"))
        self._activator_capture = HotkeyCapture()
        global_layout.addWidget(self._activator_capture)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        self._mode_press = QRadioButton("Press")
        self._mode_hold = QRadioButton("Hold")
        self._mode_press.setChecked(True)
        mode_layout.addWidget(self._mode_press)
        mode_layout.addWidget(self._mode_hold)
        mode_layout.addSpacing(20)
        mode_layout.addWidget(QLabel("Timeout (ms):"))
        self._timeout_input = QSpinBox()
        self._timeout_input.setRange(100, 60000)
        self._timeout_input.setValue(1500)
        mode_layout.addWidget(self._timeout_input)
        global_layout.addLayout(mode_layout)

        global_buttons = QHBoxLayout()
        self._global_save_btn = QPushButton("Save Global Settings")
        self._global_save_btn.clicked.connect(self._save_changes)
        global_buttons.addStretch(1)
        global_buttons.addWidget(self._global_save_btn)
        global_layout.addLayout(global_buttons)
        global_layout.addStretch()
        return global_panel

    def _load_shortcuts(self) -> None:
        """Load shortcuts from configuration file."""
        try:
            (
                self._activator,
                self._shortcuts,
                self._stt_config,
                self._llm_config,
            ) = load_full_config()
            self._refresh_list()
            _LOGGER.info("Loaded %d shortcuts", len(self._shortcuts))
            if self._activator:
                self._activator_capture.set_hotkey(self._activator.hotkey)
                self._timeout_input.setValue(
                    int(getattr(self._activator, "timeout_ms", 1500))
                )
                mode = getattr(self._activator, "mode", "press").lower()
                if mode == "hold":
                    self._mode_hold.setChecked(True)
                else:
                    self._mode_press.setChecked(True)
            else:
                self._activator_capture.set_hotkey("")
                self._timeout_input.setValue(1500)
                self._mode_press.setChecked(True)
            self._stt_section.populate(self._stt_config)
            self._llm_section.populate(self._llm_config)
        except ConfigError as exc:
            QMessageBox.critical(self, "Configuration Error", str(exc))
            _LOGGER.error("Failed to load shortcuts: %s", exc)

    def _refresh_list(self) -> None:
        """Refresh the shortcut list widget."""
        self._list_widget.clear()
        for shortcut in self._shortcuts:
            if shortcut.hotkey:
                display = shortcut.hotkey
            else:
                seq = "+".join(shortcut.suffix) if shortcut.suffix else ""
                display = f"[{seq}]"
            if shortcut.sound_path:
                display += " | 🔊"
            if shortcut.overlay:
                display += " | 🖼️"
            triggers = shortcut.all_trigger_phrases()
            if triggers:
                preview = ", ".join(f"“{t}”" for t in triggers[:2])
                if len(triggers) > 2:
                    preview += f" +{len(triggers) - 2}"
                display += f" | 🗣️{preview}"
            item = QListWidgetItem(display)
            self._list_widget.addItem(item)

    def _on_selection_changed(self, index: int) -> None:
        """Handle shortcut selection change."""
        if index < 0 or index >= len(self._shortcuts):
            self._current_index = None
            self._shortcut_section.clear()
            self._update_ui_state()
            return

        self._current_index = index
        self._shortcut_section.populate(self._shortcuts[index])
        self._update_ui_state()

    def _update_ui_state(self) -> None:
        """Update button enabled states."""
        has_selection = self._current_index is not None
        self._delete_btn.setEnabled(has_selection)
        self._preview_btn.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Shortcut CRUD
    # ------------------------------------------------------------------

    def _add_shortcut(self) -> None:
        """Add a new shortcut."""
        new_shortcut = Shortcut(hotkey="<ctrl>+<alt>+new")
        self._shortcuts.append(new_shortcut)
        self._refresh_list()
        self._list_widget.setCurrentRow(len(self._shortcuts) - 1)

    def _delete_shortcut(self) -> None:
        """Delete the selected shortcut."""
        if self._current_index is None:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Delete",
            "Are you sure you want to delete this shortcut?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.Yes:
            del self._shortcuts[self._current_index]
            self._refresh_list()
            self._current_index = None
            self._shortcut_section.clear()
            self._update_ui_state()

    # ------------------------------------------------------------------
    # Preview / Save
    # ------------------------------------------------------------------

    def _preview_shortcut(self) -> None:
        """Preview the current shortcut's sound and overlay.

        Reads from the editor (not the saved model) so the user can hear
        changes before clicking Save.
        """
        if self._current_index is None:
            return

        sec = self._shortcut_section
        sound_path = sec._sound_input.text().strip()
        overlay_path = sec._overlay_input.text().strip()

        if sound_path:
            if not Path(sound_path).is_file():
                QMessageBox.warning(
                    self, "Preview Error", f"Sound file not found: {sound_path}"
                )
            else:
                sound_id = f"preview_{self._current_index}"
                if self._sound_player.load(sound_id, sound_path):
                    self._sound_player.play(sound_id)
                else:
                    QMessageBox.warning(
                        self, "Preview Error", "Failed to load sound file"
                    )

        if overlay_path:
            if not Path(overlay_path).is_file():
                QMessageBox.warning(
                    self, "Preview Error", f"Overlay file not found: {overlay_path}"
                )
            else:
                size = None
                if sec._custom_size_checkbox.isChecked():
                    size = (sec._width_input.value(), sec._height_input.value())

                success = self._overlay_window.show_asset(
                    overlay_path,
                    duration_ms=sec._duration_input.value(),
                    position=(sec._x_input.value(), sec._y_input.value()),
                    size=size,
                )
                if not success:
                    QMessageBox.warning(
                        self, "Preview Error", "Failed to display overlay"
                    )

    def _save_changes(self) -> None:
        """Save all shortcuts to configuration file."""
        if self._current_index is not None:
            if not self._update_current_shortcut():
                return

        validation_errors = self._validate_shortcuts()
        stt_config = self._stt_section.read()
        validation_errors.extend(self._stt_section.validate(stt_config))
        llm_config = self._llm_section.read()
        validation_errors.extend(self._llm_section.validate(llm_config))
        if validation_errors:
            QMessageBox.warning(
                self,
                "Validation Errors",
                "The following issues were found:\n\n" + "\n".join(validation_errors),
            )
            return

        try:
            activator = None
            act_hotkey = self._activator_capture.get_hotkey().strip()
            if act_hotkey:
                mode = "hold" if self._mode_hold.isChecked() else "press"
                activator = ActivatorConfig(
                    hotkey=act_hotkey,
                    mode=mode,
                    timeout_ms=self._timeout_input.value(),
                )
            self._stt_config = stt_config
            self._llm_config = llm_config
            save_config(activator, self._shortcuts, stt=stt_config, llm=llm_config)
            _LOGGER.info(
                "Saved %d shortcuts (stt=%s, llm=%s)",
                len(self._shortcuts),
                "on" if stt_config else "off",
                llm_config.persona,
            )
            self._maybe_preload_stt_model(stt_config)
            QMessageBox.information(
                self, "Success", f"Saved {len(self._shortcuts)} shortcuts successfully!"
            )
        except ConfigError as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            _LOGGER.error("Failed to save shortcuts: %s", exc)

    def _maybe_preload_stt_model(self, stt_config) -> None:
        """Start a background download of the configured Whisper model."""
        if stt_config is None or not stt_config.enabled:
            return
        model_name = stt_config.model
        if model_name not in model_downloader.available_models():
            _LOGGER.warning(
                "STT model %r is not recognized; skipping preload. "
                "Available models: %s",
                model_name,
                ", ".join(model_downloader.available_models()),
            )
            return
        if model_downloader.is_model_cached(model_name):
            _LOGGER.info(
                "Whisper model '%s' is already cached; no preload needed.",
                model_name,
            )
            return
        _LOGGER.info(
            "Scheduling background download of Whisper model '%s' after config save.",
            model_name,
        )
        model_downloader.start_background_download(
            model_name,
            on_complete=lambda path: _LOGGER.info(
                "Whisper model '%s' preloaded to %s; first dictation will be instant.",
                model_name,
                path,
            ),
        )

    def _update_current_shortcut(self) -> bool:
        """Update the currently selected shortcut from editor fields."""
        if self._current_index is None:
            return True

        suffix_mode = self._shortcut_section._trigger_suffix.isChecked()
        hotkey = None
        suffix = None
        if suffix_mode:
            raw = self._shortcut_section._suffix_capture.get_key().strip().lower()
            if not raw:
                QMessageBox.warning(self, "Validation Error", "Suffix cannot be empty")
                return False
            tokens = [t for t in re.split(r"[\s,]+", raw) if t]
            if not tokens:
                QMessageBox.warning(self, "Validation Error", "Suffix cannot be empty")
                return False
            suffix = tuple(tokens)
        else:
            hk = self._shortcut_section._hotkey_capture.get_hotkey().strip()
            if not hk:
                QMessageBox.warning(self, "Validation Error", "Hotkey cannot be empty")
                return False
            hotkey = hk

        sound_path = self._shortcut_section._sound_input.text().strip() or None
        overlay_path = self._shortcut_section._overlay_input.text().strip()

        overlay = None
        if overlay_path:
            width = None
            height = None
            if self._shortcut_section._custom_size_checkbox.isChecked():
                width = self._shortcut_section._width_input.value()
                height = self._shortcut_section._height_input.value()
                if (width is not None and height is None) or (
                    width is None and height is not None
                ):
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        "Both width and height must be set together for custom size",
                    )
                    return False

            overlay = OverlayConfig(
                file=overlay_path,
                x=self._shortcut_section._x_input.value(),
                y=self._shortcut_section._y_input.value(),
                duration_ms=self._shortcut_section._duration_input.value(),
                width=width,
                height=height,
            )

        trigger_word_raw = self._shortcut_section._trigger_word_input.text().strip()
        trigger_word = trigger_word_raw.lower() or None

        phrases_text = self._shortcut_section._trigger_phrases_input.toPlainText()
        trigger_phrases_list: List[str] = []
        for raw_line in phrases_text.splitlines():
            cleaned = raw_line.strip()
            if cleaned:
                trigger_phrases_list.append(cleaned)
        trigger_phrases = tuple(trigger_phrases_list) if trigger_phrases_list else None

        self._shortcuts[self._current_index] = Shortcut(
            hotkey=hotkey,
            suffix=suffix,
            sound_path=sound_path,
            overlay=overlay,
            trigger_word=trigger_word,
            trigger_phrases=trigger_phrases,
        )
        self._refresh_list()
        return True

    def _validate_shortcuts(self) -> List[str]:
        """Validate all shortcuts and return list of errors."""
        errors = []

        hotkeys = [s.hotkey for s in self._shortcuts if s.hotkey]
        duplicates = {hk for hk in hotkeys if hotkeys.count(hk) > 1}
        if duplicates:
            errors.append(f"Duplicate hotkeys found: {', '.join(duplicates)}")

        suffixes = [s.suffix for s in self._shortcuts if s.suffix]
        dup_suffix: list[tuple[str, ...]] = []
        for sx in suffixes:
            if suffixes.count(sx) > 1 and sx not in dup_suffix:
                dup_suffix.append(sx)
        if dup_suffix:
            formatted = ["+".join(sx) for sx in dup_suffix]
            errors.append(f"Duplicate chord suffixes found: {', '.join(formatted)}")

        seen: set[str] = set()
        duplicate_triggers: list[str] = []
        for s in self._shortcuts:
            for phrase in s.all_trigger_phrases():
                if phrase in seen and phrase not in duplicate_triggers:
                    duplicate_triggers.append(phrase)
                seen.add(phrase)
        for phrase in duplicate_triggers:
            errors.append(
                f"Duplicate voice triggers found: {phrase!r} — only the "
                "first shortcut with that trigger will fire."
            )

        for i, shortcut in enumerate(self._shortcuts):
            if shortcut.sound_path and not Path(shortcut.sound_path).is_file():
                errors.append(
                    f"Shortcut {i + 1}: Sound file not found: {shortcut.sound_path}"
                )
            if shortcut.overlay and not Path(shortcut.overlay.file).is_file():
                errors.append(
                    f"Shortcut {i + 1}: Overlay file not found: {shortcut.overlay.file}"
                )

        return errors

    def closeEvent(self, event) -> None:
        """Handle window close event."""
        self._shortcut_section.cleanup_preview()
        self._sound_player.shutdown()
        super().closeEvent(event)
