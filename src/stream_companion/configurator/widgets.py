"""Reusable widgets for the configurator UI: hotkey capture, position picker.

These widgets have no dependency on the rest of the configurator and can be
embedded in any Qt application.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

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
        painter.drawText(cursor_pos.x() + 10, cursor_pos.y() - 10, coord_text)

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
        self._display.setPlaceholderText(
            "Enter keys (space/comma separated) or click Capture for one key"
        )
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
        if mods & (
            Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier
        ):
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
