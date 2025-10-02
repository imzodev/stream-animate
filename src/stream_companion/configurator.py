"""Desktop configurator UI for managing shortcuts, sounds, and overlays."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QCursor
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .config_loader import ConfigError, load_shortcuts, save_shortcuts
from .models import OverlayConfig, Shortcut
from .overlay import OverlayWindow
from .sound import SoundPlayer

_LOGGER = logging.getLogger(__name__)


class PositionPicker(QWidget):
    """Full-screen overlay for picking a position with the mouse."""

    position_picked = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._picked_position: Optional[tuple[int, int]] = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def showEvent(self, event) -> None:
        """Make the widget full screen when shown."""
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        super().showEvent(event)

    def paintEvent(self, event) -> None:
        """Draw semi-transparent overlay with instructions."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Semi-transparent dark overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 180))

        # Draw instructions
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        font = painter.font()
        font.setPointSize(16)
        painter.setFont(font)

        text = "Click anywhere to set overlay position\nPress ESC to cancel"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)

        # Draw crosshair at cursor position
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        painter.setPen(QPen(QColor(255, 0, 0), 2))
        # Horizontal line
        painter.drawLine(0, cursor_pos.y(), self.width(), cursor_pos.y())
        # Vertical line
        painter.drawLine(cursor_pos.x(), 0, cursor_pos.x(), self.height())

        # Draw coordinates
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        font.setPointSize(12)
        painter.setFont(font)
        coord_text = f"X: {cursor_pos.x()}, Y: {cursor_pos.y()}"
        painter.drawText(
            cursor_pos.x() + 10, cursor_pos.y() - 10, coord_text
        )

    def mouseMoveEvent(self, event) -> None:
        """Update display as mouse moves."""
        self.update()

    def mousePressEvent(self, event) -> None:
        """Capture the clicked position."""
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.pos()
            self.position_picked.emit(pos.x(), pos.y())
            self.close()

    def keyPressEvent(self, event) -> None:
        """Handle ESC key to cancel."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()


class HotkeyCapture(QWidget):
    """Widget for capturing keyboard shortcuts."""

    hotkey_captured = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._capturing = False
        self._keys: List[str] = []

        layout = QHBoxLayout()
        self._display = QLineEdit()
        self._display.setReadOnly(True)
        self._display.setPlaceholderText("Click 'Capture' to record a hotkey")

        self._capture_btn = QPushButton("Capture")
        self._capture_btn.clicked.connect(self._toggle_capture)

        layout.addWidget(self._display)
        layout.addWidget(self._capture_btn)
        self.setLayout(layout)

    def set_hotkey(self, hotkey: str) -> None:
        """Set the displayed hotkey value."""
        self._display.setText(hotkey)

    def get_hotkey(self) -> str:
        """Get the current hotkey value."""
        return self._display.text()

    def _toggle_capture(self) -> None:
        if self._capturing:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self) -> None:
        self._capturing = True
        self._keys = []
        self._display.setText("Press keys...")
        self._capture_btn.setText("Stop")
        self.setFocus()

    def _stop_capture(self) -> None:
        self._capturing = False
        self._capture_btn.setText("Capture")
        if self._keys:
            hotkey = self._format_hotkey(self._keys)
            self._display.setText(hotkey)
            self.hotkey_captured.emit(hotkey)

    def _format_hotkey(self, keys: List[str]) -> str:
        """Format captured keys into pynput-style hotkey string."""
        modifiers = []
        regular = []

        for key in keys:
            if key in ("ctrl", "alt", "shift", "cmd", "meta"):
                modifiers.append(f"<{key}>")
            else:
                regular.append(key)

        return "+".join(modifiers + regular)

    def keyPressEvent(self, event) -> None:
        """Capture key press events."""
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key_name = self._qt_key_to_name(event.key(), event.modifiers())
        if key_name and key_name not in self._keys:
            self._keys.append(key_name)
            self._display.setText(" + ".join(self._keys))

    def _qt_key_to_name(self, key: int, modifiers) -> Optional[str]:
        """Convert Qt key code to readable name."""
        # First check if the key itself is a modifier key
        modifier_keys = {
            Qt.Key_Control: "ctrl",
            Qt.Key_Alt: "alt",
            Qt.Key_Shift: "shift",
            Qt.Key_Meta: "cmd",
            Qt.Key_Super_L: "cmd",
            Qt.Key_Super_R: "cmd",
        }

        if key in modifier_keys:
            return modifier_keys[key]

        # Map regular keys
        key_map = {
            Qt.Key_A: "a",
            Qt.Key_B: "b",
            Qt.Key_C: "c",
            Qt.Key_D: "d",
            Qt.Key_E: "e",
            Qt.Key_F: "f",
            Qt.Key_G: "g",
            Qt.Key_H: "h",
            Qt.Key_I: "i",
            Qt.Key_J: "j",
            Qt.Key_K: "k",
            Qt.Key_L: "l",
            Qt.Key_M: "m",
            Qt.Key_N: "n",
            Qt.Key_O: "o",
            Qt.Key_P: "p",
            Qt.Key_Q: "q",
            Qt.Key_R: "r",
            Qt.Key_S: "s",
            Qt.Key_T: "t",
            Qt.Key_U: "u",
            Qt.Key_V: "v",
            Qt.Key_W: "w",
            Qt.Key_X: "x",
            Qt.Key_Y: "y",
            Qt.Key_Z: "z",
            Qt.Key_0: "0",
            Qt.Key_1: "1",
            Qt.Key_2: "2",
            Qt.Key_3: "3",
            Qt.Key_4: "4",
            Qt.Key_5: "5",
            Qt.Key_6: "6",
            Qt.Key_7: "7",
            Qt.Key_8: "8",
            Qt.Key_9: "9",
            Qt.Key_F1: "f1",
            Qt.Key_F2: "f2",
            Qt.Key_F3: "f3",
            Qt.Key_F4: "f4",
            Qt.Key_F5: "f5",
            Qt.Key_F6: "f6",
            Qt.Key_F7: "f7",
            Qt.Key_F8: "f8",
            Qt.Key_F9: "f9",
            Qt.Key_F10: "f10",
            Qt.Key_F11: "f11",
            Qt.Key_F12: "f12",
        }
        return key_map.get(key)


class ConfiguratorWindow(QMainWindow):
    """Main window for the desktop configurator."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Streaming Companion - Configurator")
        self.resize(900, 600)

        self._shortcuts: List[Shortcut] = []
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

        # Right panel: detail editor
        right_panel = QVBoxLayout()
        right_panel.addWidget(QLabel("<b>Shortcut Details</b>"))

        # Hotkey
        right_panel.addWidget(QLabel("Hotkey:"))
        self._hotkey_capture = HotkeyCapture()
        right_panel.addWidget(self._hotkey_capture)

        # Sound
        right_panel.addWidget(QLabel("Sound File:"))
        sound_layout = QHBoxLayout()
        self._sound_input = QLineEdit()
        self._sound_input.setPlaceholderText("Path to audio file (optional)")
        self._sound_browse_btn = QPushButton("Browse...")
        self._sound_browse_btn.clicked.connect(self._browse_sound)
        sound_layout.addWidget(self._sound_input)
        sound_layout.addWidget(self._sound_browse_btn)
        right_panel.addLayout(sound_layout)

        # Overlay
        right_panel.addWidget(QLabel("Overlay File:"))
        overlay_layout = QHBoxLayout()
        self._overlay_input = QLineEdit()
        self._overlay_input.setPlaceholderText("Path to overlay image/gif (optional)")
        self._overlay_browse_btn = QPushButton("Browse...")
        self._overlay_browse_btn.clicked.connect(self._browse_overlay)
        overlay_layout.addWidget(self._overlay_input)
        overlay_layout.addWidget(self._overlay_browse_btn)
        right_panel.addLayout(overlay_layout)

        # Overlay position
        right_panel.addWidget(QLabel("Overlay Position:"))
        position_layout = QHBoxLayout()
        position_layout.addWidget(QLabel("X:"))
        self._x_input = QSpinBox()
        self._x_input.setRange(0, 9999)
        position_layout.addWidget(self._x_input)
        position_layout.addWidget(QLabel("Y:"))
        self._y_input = QSpinBox()
        self._y_input.setRange(0, 9999)
        position_layout.addWidget(self._y_input)
        self._pick_position_btn = QPushButton("Pick Position...")
        self._pick_position_btn.clicked.connect(self._pick_position)
        position_layout.addWidget(self._pick_position_btn)
        right_panel.addLayout(position_layout)

        # Duration
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Duration (ms):"))
        self._duration_input = QSpinBox()
        self._duration_input.setRange(0, 60000)
        self._duration_input.setValue(1500)
        duration_layout.addWidget(self._duration_input)
        right_panel.addLayout(duration_layout)

        # Action buttons
        action_buttons = QHBoxLayout()
        self._preview_btn = QPushButton("Preview")
        self._preview_btn.clicked.connect(self._preview_shortcut)
        self._save_btn = QPushButton("Save Changes")
        self._save_btn.clicked.connect(self._save_changes)
        action_buttons.addWidget(self._preview_btn)
        action_buttons.addWidget(self._save_btn)
        right_panel.addLayout(action_buttons)

        right_panel.addStretch()

        # Combine panels
        main_layout.addLayout(left_panel, 1)
        main_layout.addLayout(right_panel, 2)
        central.setLayout(main_layout)

        self._update_ui_state()

    def _load_shortcuts(self) -> None:
        """Load shortcuts from configuration file."""
        try:
            self._shortcuts = load_shortcuts()
            self._refresh_list()
            _LOGGER.info("Loaded %d shortcuts", len(self._shortcuts))
        except ConfigError as exc:
            QMessageBox.critical(self, "Configuration Error", str(exc))
            _LOGGER.error("Failed to load shortcuts: %s", exc)

    def _refresh_list(self) -> None:
        """Refresh the shortcut list widget."""
        self._list_widget.clear()
        for shortcut in self._shortcuts:
            display = f"{shortcut.hotkey}"
            if shortcut.sound_path:
                display += " | 🔊"
            if shortcut.overlay:
                display += " | 🖼️"
            item = QListWidgetItem(display)
            self._list_widget.addItem(item)

    def _on_selection_changed(self, index: int) -> None:
        """Handle shortcut selection change."""
        if index < 0 or index >= len(self._shortcuts):
            self._current_index = None
            self._clear_editor()
            self._update_ui_state()
            return

        self._current_index = index
        shortcut = self._shortcuts[index]

        self._hotkey_capture.set_hotkey(shortcut.hotkey)
        self._sound_input.setText(shortcut.sound_path or "")

        if shortcut.overlay:
            self._overlay_input.setText(shortcut.overlay.file)
            self._x_input.setValue(shortcut.overlay.x)
            self._y_input.setValue(shortcut.overlay.y)
            self._duration_input.setValue(shortcut.overlay.duration_ms)
        else:
            self._overlay_input.setText("")
            self._x_input.setValue(0)
            self._y_input.setValue(0)
            self._duration_input.setValue(1500)

        self._update_ui_state()

    def _clear_editor(self) -> None:
        """Clear the editor panel."""
        self._hotkey_capture.set_hotkey("")
        self._sound_input.setText("")
        self._overlay_input.setText("")
        self._x_input.setValue(0)
        self._y_input.setValue(0)
        self._duration_input.setValue(1500)

    def _update_ui_state(self) -> None:
        """Update button enabled states."""
        has_selection = self._current_index is not None
        self._delete_btn.setEnabled(has_selection)
        self._preview_btn.setEnabled(has_selection)

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
            self._clear_editor()
            self._update_ui_state()

    def _browse_sound(self) -> None:
        """Open file dialog to select sound file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Sound File",
            "",
            "Audio Files (*.wav *.mp3);;All Files (*)",
        )
        if file_path:
            self._sound_input.setText(file_path)

    def _browse_overlay(self) -> None:
        """Open file dialog to select overlay file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Overlay File",
            "",
            "Image Files (*.png *.gif *.jpg);;All Files (*)",
        )
        if file_path:
            self._overlay_input.setText(file_path)

    def _pick_position(self) -> None:
        """Open position picker to select overlay position with mouse."""
        picker = PositionPicker(self)
        picker.position_picked.connect(self._on_position_picked)
        picker.show()

    def _on_position_picked(self, x: int, y: int) -> None:
        """Handle position picked from the position picker."""
        self._x_input.setValue(x)
        self._y_input.setValue(y)

    def _preview_shortcut(self) -> None:
        """Preview the current shortcut's sound and overlay."""
        if self._current_index is None:
            return

        sound_path = self._sound_input.text().strip()
        overlay_path = self._overlay_input.text().strip()

        # Preview sound
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

        # Preview overlay
        if overlay_path:
            if not Path(overlay_path).is_file():
                QMessageBox.warning(
                    self, "Preview Error", f"Overlay file not found: {overlay_path}"
                )
            else:
                success = self._overlay_window.show_asset(
                    overlay_path,
                    duration_ms=self._duration_input.value(),
                    position=(self._x_input.value(), self._y_input.value()),
                )
                if not success:
                    QMessageBox.warning(
                        self, "Preview Error", "Failed to display overlay"
                    )

    def _save_changes(self) -> None:
        """Save all shortcuts to configuration file."""
        # Update current shortcut if one is selected
        if self._current_index is not None:
            if not self._update_current_shortcut():
                return

        # Validate shortcuts
        validation_errors = self._validate_shortcuts()
        if validation_errors:
            QMessageBox.warning(
                self,
                "Validation Errors",
                "The following issues were found:\n\n" + "\n".join(validation_errors),
            )
            return

        # Save to file
        try:
            save_shortcuts(self._shortcuts)
            QMessageBox.information(
                self, "Success", f"Saved {len(self._shortcuts)} shortcuts successfully!"
            )
            _LOGGER.info("Saved %d shortcuts", len(self._shortcuts))
        except ConfigError as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            _LOGGER.error("Failed to save shortcuts: %s", exc)

    def _update_current_shortcut(self) -> bool:
        """Update the currently selected shortcut from editor fields."""
        if self._current_index is None:
            return True

        hotkey = self._hotkey_capture.get_hotkey().strip()
        if not hotkey:
            QMessageBox.warning(self, "Validation Error", "Hotkey cannot be empty")
            return False

        sound_path = self._sound_input.text().strip() or None
        overlay_path = self._overlay_input.text().strip()

        overlay = None
        if overlay_path:
            overlay = OverlayConfig(
                file=overlay_path,
                x=self._x_input.value(),
                y=self._y_input.value(),
                duration_ms=self._duration_input.value(),
            )

        # Create new shortcut (since Shortcut is frozen)
        self._shortcuts[self._current_index] = Shortcut(
            hotkey=hotkey,
            sound_path=sound_path,
            overlay=overlay,
        )
        self._refresh_list()
        return True

    def _validate_shortcuts(self) -> List[str]:
        """Validate all shortcuts and return list of errors."""
        errors = []

        # Check for duplicate hotkeys
        hotkeys = [s.hotkey for s in self._shortcuts]
        duplicates = {hk for hk in hotkeys if hotkeys.count(hk) > 1}
        if duplicates:
            errors.append(f"Duplicate hotkeys found: {', '.join(duplicates)}")

        # Check for missing files
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
        self._sound_player.shutdown()
        super().closeEvent(event)


def run_configurator() -> None:
    """Launch the configurator window."""
    app = QApplication.instance() or QApplication([])
    window = ConfiguratorWindow()
    window.show()
    app.exec()
