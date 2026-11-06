"""Main configurator window: composes the per-section widgets.

This module is the orchestrator. It owns the shortcut list, the global
settings tab, the STT section, and the shortcut details section. Each
section widget has a small read/populate/validate API and lives in
:mod:`.sections`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import model_downloader
from ..config_loader import (
    ConfigError,
    load_full_config,
    load_voice_commands_config,
    save_config,
)
from .voice_section import VoiceCommandsSection
from ..llm.config import LLMConfig
from ..models import ActivatorConfig, Shortcut
from ..overlay import OverlayWindow
from ..sound import SoundPlayer
from .llm_section import LLMSection
from .sections import STTSection, ShortcutSection
from .theme import STYLESHEET, logo_pixmap, window_icon
from .widgets import HotkeyCapture

_NAV_PAGES = (
    "⚙  General",
    "🎙  Speech-to-Text",
    "🤖  AI Assistant",
    "🗣  Voice Commands",
    "⌨  Shortcuts",
)

_LOGGER = logging.getLogger(__name__)


class ConfiguratorWindow(QMainWindow):
    """Main window for the desktop configurator."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stream Companion - Settings")
        self.setWindowIcon(window_icon())
        self.resize(1040, 680)
        self.setStyleSheet(STYLESHEET)

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
        """Initialize the UI layout: header, sidebar nav, pages, footer."""
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._nav_list = QListWidget()
        self._nav_list.setObjectName("navList")
        self._nav_list.setFixedWidth(200)
        self._nav_list.setFrameShape(QFrame.Shape.NoFrame)
        for label in _NAV_PAGES:
            QListWidgetItem(label, self._nav_list)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._scrollable(self._build_global_panel()))
        self._stt_section = STTSection()
        self._stack.addWidget(self._scrollable(self._stt_section))
        self._llm_section = LLMSection()
        self._stack.addWidget(self._scrollable(self._llm_section))
        self._voice_section = VoiceCommandsSection()
        self._stack.addWidget(self._voice_section)
        self._stack.addWidget(self._build_shortcuts_page())

        self._nav_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav_list.setCurrentRow(0)

        body_layout.addWidget(self._nav_list)
        body_layout.addWidget(self._stack, 1)
        outer.addWidget(body, 1)
        outer.addWidget(self._build_footer())

        self._update_ui_state()

    def _build_header(self) -> QWidget:
        """App brand mark + title, shown above the sidebar and pages."""
        header = QWidget()
        header.setObjectName("headerBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(logo_pixmap(36))
        logo.setFixedSize(36, 36)
        layout.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("Stream Companion")
        title.setObjectName("headerTitle")
        subtitle = QLabel("Settings")
        subtitle.setObjectName("headerSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)
        return header

    def _build_footer(self) -> QWidget:
        """Single "Save All Settings" action, visible from every page."""
        footer = QWidget()
        footer.setObjectName("footerBar")
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.addStretch(1)
        self._save_btn = QPushButton("Save All Settings")
        self._save_btn.setObjectName("primaryButton")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save_changes)
        layout.addWidget(self._save_btn)
        return footer

    @staticmethod
    def _scrollable(widget: QWidget) -> QScrollArea:
        """Wrap a settings-form page so long forms scroll instead of clipping."""
        area = QScrollArea()
        area.setObjectName("pageScroll")
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidgetResizable(True)
        area.setWidget(widget)
        return area

    def _build_shortcuts_page(self) -> QWidget:
        """The "Shortcuts" page: a master list beside the detail editor."""
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        list_card = QWidget()
        list_card.setObjectName("card")
        list_layout = QVBoxLayout(list_card)
        list_layout.addWidget(QLabel("<b>Shortcuts</b>"))

        self._list_widget = QListWidget()
        self._list_widget.currentRowChanged.connect(self._on_selection_changed)
        list_layout.addWidget(self._list_widget)

        list_buttons = QHBoxLayout()
        self._add_btn = QPushButton("Add")
        self._add_btn.clicked.connect(self._add_shortcut)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._delete_shortcut)
        list_buttons.addWidget(self._add_btn)
        list_buttons.addWidget(self._delete_btn)
        list_layout.addLayout(list_buttons)
        list_card.setMinimumWidth(240)
        list_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        detail_card = QWidget()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        self._shortcut_section = ShortcutSection()
        detail_layout.addWidget(self._shortcut_section)

        # Preview the selected shortcut's sound/overlay. Saving is
        # handled by the global "Save All Settings" button in the
        # footer, which is visible from every page.
        action_buttons = QHBoxLayout()
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.clicked.connect(self._preview_shortcut)
        action_buttons.addWidget(self._preview_btn)
        action_buttons.addStretch(1)
        detail_layout.addLayout(action_buttons)

        detail_scroll = self._scrollable(detail_card)

        splitter.addWidget(list_card)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 700])

        layout.addWidget(splitter)
        return page

    def _build_global_panel(self) -> QWidget:
        """Construct the General settings page."""
        global_panel = QWidget()
        global_layout = QVBoxLayout()
        global_panel.setLayout(global_layout)

        group = QWidget()
        group.setObjectName("card")
        group_layout = QVBoxLayout(group)
        group_layout.addWidget(QLabel("<b>Activator</b>"))

        group_layout.addWidget(QLabel("Activator Hotkey:"))
        self._activator_capture = HotkeyCapture()
        group_layout.addWidget(self._activator_capture)

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
        mode_layout.addStretch(1)
        group_layout.addLayout(mode_layout)

        global_layout.addWidget(group)
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
            self._load_voice_commands()
        except ConfigError as exc:
            QMessageBox.critical(self, "Configuration Error", str(exc))
            _LOGGER.error("Failed to load shortcuts: %s", exc)

    def _load_voice_commands(self) -> None:
        """Load the voice-commands block; a bad block disables saving it."""

        self._voice_load_error: Optional[str] = None
        try:
            voice = load_voice_commands_config()
        except ConfigError as exc:
            # Don't overwrite a block we couldn't parse (the user may be
            # mid-edit in the JSON file); show why instead.
            self._voice_load_error = str(exc)
            voice = None
            QMessageBox.warning(self, "Voice Commands", str(exc))
        self._voice_section.populate(voice)

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
        validation_errors.extend(self._voice_section.validate())
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
            # None keeps the block on disk untouched (e.g. it failed to load).
            voice = (
                None
                if getattr(self, "_voice_load_error", None)
                else self._voice_section.read()
            )
            save_config(
                activator,
                self._shortcuts,
                stt=stt_config,
                llm=llm_config,
                voice_commands=voice,
            )
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

        errors = self._shortcut_section.validate_trigger()
        if errors:
            QMessageBox.warning(self, "Validation Error", errors[0])
            return False

        self._shortcuts[self._current_index] = self._shortcut_section.read()
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
