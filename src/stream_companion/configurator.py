"""Desktop configurator UI for managing shortcuts, sounds, and overlays."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional
import re

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QPen, QColor, QCursor, QPixmap, QMovie
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
    QRadioButton,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from .config_loader import ConfigError, load_config, save_config
from .models import ActivatorConfig, OverlayConfig, Shortcut
from .overlay import OverlayWindow
from .sound import SoundPlayer

_LOGGER = logging.getLogger(__name__)

# UI Constants
PREVIEW_WIDTH = 200
PREVIEW_HEIGHT = 150
MAX_OVERLAY_SIZE = 9999
MIN_OVERLAY_SIZE = 1
MAX_DURATION_MS = 60000


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


class SingleKeyCapture(QWidget):
    """Widget to capture a single key (no modifiers) for chord suffix."""

    key_captured = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._capturing = False
        layout = QHBoxLayout()
        self._display = QLineEdit()
        # Allow manual entry for multi-key sequences (space/comma separated)
        self._display.setReadOnly(False)
        self._display.setPlaceholderText("Enter keys (space/comma separated) or click Capture for one key")
        self._capture_btn = QPushButton("Capture")
        self._capture_btn.clicked.connect(self._toggle_capture)
        layout.addWidget(self._display)
        layout.addWidget(self._capture_btn)
        self.setLayout(layout)

    def set_key(self, key: str) -> None:
        self._display.setText(key)

    def get_key(self) -> str:
        return self._display.text()

    def _toggle_capture(self) -> None:
        if self._capturing:
            self._capturing = False
            self._capture_btn.setText("Capture")
            return
        self._capturing = True
        # Do not overwrite existing content; user may be building a sequence
        if not self._display.text().strip():
            self._display.setText("Press a key...")
        self._capture_btn.setText("Stop")
        self.setFocus()

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        # Disallow modifier-only keys
        mods = event.modifiers()
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier):
            self._display.setText("No modifiers allowed")
            return
        name = HotkeyCapture._qt_key_to_name(self, event.key(), event.modifiers())
        if name:
            existing = self._display.text().strip()
            if existing and existing not in ("Press a key...", "No modifiers allowed"):
                self._display.setText(existing + " " + name)
            else:
                self._display.setText(name)
            self._capturing = False
            self._capture_btn.setText("Capture")
            self.key_captured.emit(name)


class ConfiguratorWindow(QMainWindow):
    """Main window for the desktop configurator."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Streaming Companion - Configurator")
        self.resize(900, 600)

        self._shortcuts: List[Shortcut] = []
        self._activator: Optional[ActivatorConfig] = None
        self._current_index: Optional[int] = None
        self._sound_player = SoundPlayer()
        self._overlay_window = OverlayWindow()
        self._preview_movie: Optional[QMovie] = None

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

        # Right panel uses tabs: Global Settings and Shortcut Details
        tabs = QTabWidget()

        # Tab 1: Global Settings
        global_panel = QWidget()
        global_layout = QVBoxLayout()
        global_panel.setLayout(global_layout)
        global_layout.addWidget(QLabel("<b>Global Settings</b>"))
        # Activator hotkey
        global_layout.addWidget(QLabel("Activator Hotkey:"))
        self._activator_capture = HotkeyCapture()
        global_layout.addWidget(self._activator_capture)
        # Mode and timeout
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
        # Save button for global settings
        global_buttons = QHBoxLayout()
        self._global_save_btn = QPushButton("Save Global Settings")
        self._global_save_btn.clicked.connect(self._save_changes)
        global_buttons.addStretch(1)
        global_buttons.addWidget(self._global_save_btn)
        global_layout.addLayout(global_buttons)
        global_layout.addStretch()

        # Tab 2: Shortcut Details (existing UI)
        shortcut_panel = QWidget()
        right_panel = QVBoxLayout()
        shortcut_panel.setLayout(right_panel)
        right_panel.addWidget(QLabel("<b>Shortcut Details</b>"))

        # Trigger type
        trigger_layout = QHBoxLayout()
        trigger_layout.addWidget(QLabel("Trigger:"))
        self._trigger_direct = QRadioButton("Direct hotkey")
        self._trigger_suffix = QRadioButton("Chord suffix")
        self._trigger_direct.setChecked(True)
        trigger_layout.addWidget(self._trigger_direct)
        trigger_layout.addWidget(self._trigger_suffix)
        right_panel.addLayout(trigger_layout)

        # Hotkey / Suffix capture widgets
        right_panel.addWidget(QLabel("Hotkey or Suffix:"))
        self._hotkey_capture = HotkeyCapture()
        self._suffix_capture = SingleKeyCapture()
        right_panel.addWidget(self._hotkey_capture)
        right_panel.addWidget(self._suffix_capture)
        self._suffix_capture.hide()

        def _on_trigger_changed():
            suffix_mode = self._trigger_suffix.isChecked()
            self._hotkey_capture.setVisible(not suffix_mode)
            self._suffix_capture.setVisible(suffix_mode)
        self._trigger_direct.toggled.connect(_on_trigger_changed)
        self._trigger_suffix.toggled.connect(_on_trigger_changed)

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
        self._overlay_input.setPlaceholderText("Path to overlay image/gif/video (optional)")
        self._overlay_input.textChanged.connect(self._on_overlay_changed)
        self._overlay_browse_btn = QPushButton("Browse...")
        self._overlay_browse_btn.clicked.connect(self._browse_overlay)
        overlay_layout.addWidget(self._overlay_input)
        overlay_layout.addWidget(self._overlay_browse_btn)
        right_panel.addLayout(overlay_layout)

        # Overlay preview
        self._overlay_preview = QLabel()
        self._overlay_preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self._overlay_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay_preview.setStyleSheet("QLabel { border: 1px solid #ccc; background-color: #f0f0f0; }")
        self._overlay_preview.setText("No preview")
        right_panel.addWidget(self._overlay_preview)

        # Overlay size
        right_panel.addWidget(QLabel("Overlay Size (optional):"))
        size_layout = QHBoxLayout()
        self._custom_size_checkbox = QCheckBox("Custom size")
        self._custom_size_checkbox.stateChanged.connect(self._on_custom_size_toggled)
        size_layout.addWidget(self._custom_size_checkbox)
        size_layout.addWidget(QLabel("Width:"))
        self._width_input = QSpinBox()
        self._width_input.setRange(MIN_OVERLAY_SIZE, MAX_OVERLAY_SIZE)
        self._width_input.setValue(100)
        self._width_input.setEnabled(False)
        size_layout.addWidget(self._width_input)
        size_layout.addWidget(QLabel("Height:"))
        self._height_input = QSpinBox()
        self._height_input.setRange(MIN_OVERLAY_SIZE, MAX_OVERLAY_SIZE)
        self._height_input.setValue(100)
        self._height_input.setEnabled(False)
        size_layout.addWidget(self._height_input)
        right_panel.addLayout(size_layout)

        # Overlay position
        right_panel.addWidget(QLabel("Overlay Position:"))
        position_layout = QHBoxLayout()
        position_layout.addWidget(QLabel("X:"))
        self._x_input = QSpinBox()
        self._x_input.setRange(0, MAX_OVERLAY_SIZE)
        position_layout.addWidget(self._x_input)
        position_layout.addWidget(QLabel("Y:"))
        self._y_input = QSpinBox()
        self._y_input.setRange(0, MAX_OVERLAY_SIZE)
        position_layout.addWidget(self._y_input)
        self._pick_position_btn = QPushButton("Pick Position...")
        self._pick_position_btn.clicked.connect(self._pick_position)
        position_layout.addWidget(self._pick_position_btn)
        right_panel.addLayout(position_layout)

        # Duration
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Duration (ms):"))
        self._duration_input = QSpinBox()
        self._duration_input.setRange(0, MAX_DURATION_MS)
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

        # Assemble tabs
        tabs.addTab(global_panel, "Global Settings")
        tabs.addTab(shortcut_panel, "Shortcuts")

        # Combine panels
        main_layout.addLayout(left_panel, 1)
        main_layout.addWidget(tabs, 2)
        central.setLayout(main_layout)

        self._update_ui_state()

    def _load_shortcuts(self) -> None:
        """Load shortcuts from configuration file."""
        try:
            self._activator, self._shortcuts = load_config()
            self._refresh_list()
            _LOGGER.info("Loaded %d shortcuts", len(self._shortcuts))
            # Populate global settings UI from activator
            if self._activator:
                self._activator_capture.set_hotkey(self._activator.hotkey)
                self._timeout_input.setValue(int(getattr(self._activator, "timeout_ms", 1500)))
                mode = getattr(self._activator, "mode", "press").lower()
                if mode == "hold":
                    self._mode_hold.setChecked(True)
                else:
                    self._mode_press.setChecked(True)
            else:
                self._activator_capture.set_hotkey("")
                self._timeout_input.setValue(1500)
                self._mode_press.setChecked(True)
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

        # Trigger type and captures
        if shortcut.hotkey:
            self._trigger_direct.setChecked(True)
            self._hotkey_capture.set_hotkey(shortcut.hotkey)
            self._suffix_capture.set_key("")
        else:
            self._trigger_suffix.setChecked(True)
            self._hotkey_capture.set_hotkey("")
            self._suffix_capture.set_key(" ".join(shortcut.suffix) if shortcut.suffix else "")
        self._sound_input.setText(shortcut.sound_path or "")

        if shortcut.overlay:
            self._overlay_input.setText(shortcut.overlay.file)
            self._x_input.setValue(shortcut.overlay.x)
            self._y_input.setValue(shortcut.overlay.y)
            self._duration_input.setValue(shortcut.overlay.duration_ms)
            if shortcut.overlay.width is not None and shortcut.overlay.height is not None:
                self._custom_size_checkbox.setChecked(True)
                self._width_input.setValue(shortcut.overlay.width)
                self._height_input.setValue(shortcut.overlay.height)
            else:
                self._custom_size_checkbox.setChecked(False)
        else:
            self._overlay_input.setText("")
            self._x_input.setValue(0)
            self._y_input.setValue(0)
            self._duration_input.setValue(1500)
            self._custom_size_checkbox.setChecked(False)

        self._update_ui_state()

    def _clear_editor(self) -> None:
        """Clear the editor panel."""
        self._trigger_direct.setChecked(True)
        self._hotkey_capture.set_hotkey("")
        self._suffix_capture.set_key("")
        self._sound_input.setText("")
        self._overlay_input.setText("")
        self._cleanup_preview_movie()
        self._overlay_preview.clear()
        self._overlay_preview.setText("No preview")
        self._x_input.setValue(0)
        self._y_input.setValue(0)
        self._duration_input.setValue(1500)
        self._custom_size_checkbox.setChecked(False)
        self._width_input.setValue(100)
        self._height_input.setValue(100)

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
            "Overlay Files (*.png *.gif *.jpg *.jpeg *.mp4 *.mov *.avi *.mkv *.webm *.m4v);;All Files (*)",
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

    def _cleanup_preview_movie(self) -> None:
        """Clean up the preview movie to prevent memory leaks."""
        if self._preview_movie is not None:
            self._preview_movie.stop()
            self._preview_movie.deleteLater()
            self._preview_movie = None

    def _on_overlay_changed(self, path: str) -> None:
        """Update overlay preview when path changes."""
        # Clean up previous movie if any
        self._cleanup_preview_movie()
        
        if not path or not Path(path).exists():
            self._overlay_preview.clear()
            self._overlay_preview.setText("No preview")
            return

        # Load and display preview
        path_obj = Path(path)
        try:
            suffix = path_obj.suffix.lower()
            if suffix == ".gif":
                # For GIFs, show first frame
                movie = QMovie(str(path_obj))
                if not movie.isValid():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid GIF")
                    _LOGGER.warning("Failed to load GIF preview: %s", path_obj)
                    return
                
                movie.jumpToFrame(0)
                pixmap = movie.currentPixmap()
                if pixmap.isNull():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid GIF")
                    return
                
                # Store reference to clean up later
                self._preview_movie = movie
                
                scaled = pixmap.scaled(
                    PREVIEW_WIDTH, PREVIEW_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self._overlay_preview.setPixmap(scaled)
                # Set default size if not already set
                if not self._custom_size_checkbox.isChecked():
                    self._width_input.setValue(pixmap.width())
                    self._height_input.setValue(pixmap.height())
            elif suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
                # For videos, we don't render a live preview here
                self._overlay_preview.clear()
                self._overlay_preview.setText("Video file selected (no preview)")
                # If custom size not set, leave size fields as-is
            else:
                # For static images
                pixmap = QPixmap(str(path_obj))
                if pixmap.isNull():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid image")
                    _LOGGER.warning("Failed to load image preview: %s", path_obj)
                    return
                
                scaled = pixmap.scaled(
                    PREVIEW_WIDTH, PREVIEW_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
                self._overlay_preview.setPixmap(scaled)
                # Set default size if not already set
                if not self._custom_size_checkbox.isChecked():
                    self._width_input.setValue(pixmap.width())
                    self._height_input.setValue(pixmap.height())
        except Exception as exc:
            self._overlay_preview.clear()
            self._overlay_preview.setText("Error loading preview")
            _LOGGER.error("Error loading overlay preview for %s: %s", path_obj, exc)

    def _on_custom_size_toggled(self, state: int) -> None:
        """Handle custom size checkbox toggle."""
        enabled = state == Qt.CheckState.Checked.value
        self._width_input.setEnabled(enabled)
        self._height_input.setEnabled(enabled)

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
                size = None
                if self._custom_size_checkbox.isChecked():
                    size = (self._width_input.value(), self._height_input.value())
                
                success = self._overlay_window.show_asset(
                    overlay_path,
                    duration_ms=self._duration_input.value(),
                    position=(self._x_input.value(), self._y_input.value()),
                    size=size,
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

        # Save to file (include activator)
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
            save_config(activator, self._shortcuts)
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

        # Determine trigger
        suffix_mode = self._trigger_suffix.isChecked()
        hotkey = None
        suffix = None
        if suffix_mode:
            raw = self._suffix_capture.get_key().strip().lower()
            if not raw:
                QMessageBox.warning(self, "Validation Error", "Suffix cannot be empty")
                return False
            # Split on spaces or commas; ignore extra separators
            tokens = [t for t in re.split(r"[\s,]+", raw) if t]
            if not tokens:
                QMessageBox.warning(self, "Validation Error", "Suffix cannot be empty")
                return False
            suffix = tuple(tokens)
        else:
            hk = self._hotkey_capture.get_hotkey().strip()
            if not hk:
                QMessageBox.warning(self, "Validation Error", "Hotkey cannot be empty")
                return False
            hotkey = hk

        sound_path = self._sound_input.text().strip() or None
        overlay_path = self._overlay_input.text().strip()

        overlay = None
        if overlay_path:
            width = None
            height = None
            if self._custom_size_checkbox.isChecked():
                width = self._width_input.value()
                height = self._height_input.value()
                # Validate that both width and height are set together
                if (width is not None and height is None) or (width is None and height is not None):
                    QMessageBox.warning(
                        self,
                        "Validation Error",
                        "Both width and height must be set together for custom size",
                    )
                    return False
            
            overlay = OverlayConfig(
                file=overlay_path,
                x=self._x_input.value(),
                y=self._y_input.value(),
                duration_ms=self._duration_input.value(),
                width=width,
                height=height,
            )

        # Create new shortcut (since Shortcut is frozen)
        self._shortcuts[self._current_index] = Shortcut(
            hotkey=hotkey,
            suffix=suffix,
            sound_path=sound_path,
            overlay=overlay,
        )
        self._refresh_list()
        return True

    def _validate_shortcuts(self) -> List[str]:
        """Validate all shortcuts and return list of errors."""
        errors = []

        # Check for duplicate hotkeys (ignore None)
        hotkeys = [s.hotkey for s in self._shortcuts if s.hotkey]
        duplicates = {hk for hk in hotkeys if hotkeys.count(hk) > 1}
        if duplicates:
            errors.append(f"Duplicate hotkeys found: {', '.join(duplicates)}")

        # Check for duplicate suffix sequences (ignore None)
        suffixes = [s.suffix for s in self._shortcuts if s.suffix]
        dup_suffix: list[tuple[str, ...]] = []
        for sx in suffixes:
            if suffixes.count(sx) > 1 and sx not in dup_suffix:
                dup_suffix.append(sx)
        if dup_suffix:
            formatted = ["+".join(sx) for sx in dup_suffix]
            errors.append(f"Duplicate chord suffixes found: {', '.join(formatted)}")

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
        self._cleanup_preview_movie()
        self._sound_player.shutdown()
        super().closeEvent(event)


def run_configurator() -> None:
    """Launch the configurator window."""
    app = QApplication.instance() or QApplication([])
    window = ConfiguratorWindow()
    window.show()
    app.exec()
