"""Visual theme for the configurator window.

A single QSS stylesheet applied to :class:`ConfiguratorWindow`. Qt's
default (native) widget style on Windows looks dated — flat gray
buttons, square corners, no spacing — so every control here is
restyled: rounded cards, a soft dark palette, an accent color for the
primary action, and a slim scrollbar. The stylesheet is scoped to the
configurator window (set via ``setStyleSheet`` on the window itself,
not the whole ``QApplication``), so it never reaches the transparent
overlay or the on-screen "Listening" indicator elsewhere in the app.
"""

from __future__ import annotations

from PySide6.QtGui import QIcon, QPixmap

# -- palette ------------------------------------------------------------

BG_WINDOW = "#191a1f"
BG_SIDEBAR = "#14151a"
BG_PANEL = "#1e2025"
BG_CARD = "#24262d"
BG_INPUT = "#1b1c21"
BORDER = "#34363f"
BORDER_SOFT = "#2a2c34"
TEXT_PRIMARY = "#eceef2"
TEXT_SECONDARY = "#9a9da8"
TEXT_DIM = "#6c6f79"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7aa2ff"
ACCENT_PRESSED = "#4472e6"
ACCENT_SOFT = "rgba(91, 140, 255, 40)"
ACCENT_SOFT_STRONG = "rgba(91, 140, 255, 70)"

STYLESHEET = f"""
* {{
    font-family: "Segoe UI", "Segoe UI Variable", sans-serif;
    font-size: 10pt;
    color: {TEXT_PRIMARY};
}}

QMainWindow, #centralWidget {{
    background: {BG_WINDOW};
}}

/* ---- header ---------------------------------------------------- */

#headerBar {{
    background: {BG_WINDOW};
    border-bottom: 1px solid {BORDER_SOFT};
}}

#headerTitle {{
    font-size: 15pt;
    font-weight: 600;
    color: {TEXT_PRIMARY};
}}

#headerSubtitle {{
    font-size: 9pt;
    color: {TEXT_SECONDARY};
}}

/* ---- sidebar navigation ----------------------------------------- */

#navList {{
    background: {BG_SIDEBAR};
    border: none;
    border-right: 1px solid {BORDER_SOFT};
    padding: 10px 8px;
    outline: none;
}}

#navList::item {{
    padding: 9px 12px;
    margin: 1px 0px;
    border-radius: 8px;
    border-left: 3px solid transparent;
    color: {TEXT_SECONDARY};
}}

#navList::item:hover {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
}}

#navList::item:selected {{
    background: {ACCENT_SOFT};
    border-left: 3px solid {ACCENT};
    color: {TEXT_PRIMARY};
    font-weight: 600;
}}

/* ---- pages / cards ------------------------------------------------ */

QStackedWidget, #pageScroll, #pageScroll > QWidget > QWidget {{
    background: {BG_PANEL};
    border: none;
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QGroupBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    top: 2px;
    padding: 0 6px;
    color: {ACCENT_HOVER};
    background: transparent;
}}

#card {{
    background: {BG_CARD};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}

QLabel {{
    background: transparent;
}}

QLabel[hint="true"] {{
    color: {TEXT_SECONDARY};
}}

/* ---- buttons -------------------------------------------------------- */

QPushButton {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 16px;
    color: {TEXT_PRIMARY};
}}

QPushButton:hover {{
    background: #2c2e36;
    border-color: #3d3f49;
}}

QPushButton:pressed {{
    background: #1c1d22;
}}

QPushButton:disabled {{
    color: {TEXT_DIM};
    border-color: {BORDER_SOFT};
    background: {BG_PANEL};
}}

QPushButton#primaryButton {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #0b1020;
    font-weight: 600;
    padding: 9px 22px;
}}

QPushButton#primaryButton:hover {{
    background: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

QPushButton#primaryButton:pressed {{
    background: {ACCENT_PRESSED};
}}

/* ---- text inputs ------------------------------------------------- */

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 5px 9px;
    selection-background-color: {ACCENT};
}}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {ACCENT};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QPlainTextEdit:disabled {{
    color: {TEXT_DIM};
    background: {BG_PANEL};
}}

QLineEdit[readOnly="true"] {{
    color: {TEXT_SECONDARY};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: 6px;
    selection-background-color: {ACCENT_SOFT_STRONG};
    padding: 4px;
}}

QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    border: none;
}}

/* ---- checkboxes / radios ------------------------------------------ */

QCheckBox, QRadioButton {{
    spacing: 8px;
    background: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    background: {BG_INPUT};
}}

QCheckBox::indicator {{
    border-radius: 4px;
}}

QRadioButton::indicator {{
    border-radius: 9px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT_HOVER};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {BORDER_SOFT};
    background: {BG_PANEL};
}}

/* ---- lists / trees -------------------------------------------------- */

QListWidget, QTreeWidget {{
    background: {BG_INPUT};
    border: 1px solid {BORDER_SOFT};
    border-radius: 8px;
    outline: none;
}}

QListWidget::item, QTreeWidget::item {{
    padding: 5px 4px;
    border-radius: 5px;
}}

QListWidget::item:hover, QTreeWidget::item:hover {{
    background: {BG_CARD};
}}

QListWidget::item:selected, QTreeWidget::item:selected {{
    background: {ACCENT_SOFT_STRONG};
    color: {TEXT_PRIMARY};
}}

QTreeWidget::branch {{
    background: transparent;
}}

QHeaderView::section {{
    background: {BG_CARD};
    color: {TEXT_SECONDARY};
    border: none;
    border-bottom: 1px solid {BORDER_SOFT};
    padding: 5px;
}}

/* ---- footer --------------------------------------------------------- */

#footerBar {{
    background: {BG_WINDOW};
    border-top: 1px solid {BORDER_SOFT};
}}

/* ---- splitter --------------------------------------------------------- */

QSplitter::handle {{
    background: {BORDER_SOFT};
    width: 1px;
}}

QSplitter::handle:hover {{
    background: {ACCENT};
}}

/* ---- scrollbars ------------------------------------------------------- */

QScrollBar:vertical {{
    background: transparent;
    width: 11px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 11px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 24px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {ACCENT};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QToolTip {{
    background: {BG_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    border-radius: 6px;
}}

QMessageBox {{
    background: {BG_PANEL};
}}
"""


def logo_pixmap(size: int = 32) -> QPixmap:
    """The same brand mark used on the tray icon, for the settings header."""

    from ..tray_indicators import _fallback_base_pixmap, find_base_icon_pixmap

    return find_base_icon_pixmap(size) or _fallback_base_pixmap(size)


def window_icon() -> QIcon:
    """Window icon matching the tray icon's brand mark."""

    return QIcon(logo_pixmap(64))
