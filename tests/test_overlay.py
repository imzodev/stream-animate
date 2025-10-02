from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from stream_companion.overlay import OverlayWindow


@pytest.fixture(scope="session", autouse=True)
def ensure_offscreen_platform() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def overlay(qapp: QApplication) -> OverlayWindow:
    window = OverlayWindow(auto_hide_ms=100)
    yield window
    window.close()


@pytest.fixture()
def png_asset(tmp_path: Path) -> Path:
    path = tmp_path / "overlay.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.red)
    assert pixmap.save(str(path), "PNG")
    return path


@pytest.fixture()
def gif_asset(tmp_path: Path) -> Path:
    # Minimal 1x1 pixel GIF image
    gif_bytes = (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\x00\x00\x00\x00\x00!\xf9\x04"
        b"\x01\n\x00\x01\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )
    path = tmp_path / "overlay.gif"
    path.write_bytes(gif_bytes)
    return path


def test_show_static_image(overlay: OverlayWindow, png_asset: Path) -> None:
    assert overlay.show_asset(png_asset.as_posix(), duration_ms=150) is True
    assert overlay.isVisible() is True
    assert overlay.is_auto_hide_active() is True

    QTest.qWait(200)
    assert overlay.isVisible() is False


def test_show_gif_animation(overlay: OverlayWindow, gif_asset: Path) -> None:
    assert overlay.show_asset(gif_asset.as_posix(), duration_ms=0) is True
    assert overlay.is_animating() is True
    assert overlay.is_auto_hide_active() is False

    overlay.hide()
    QTest.qWait(50)
    assert overlay.is_animating() is False


def test_missing_asset_returns_false(overlay: OverlayWindow) -> None:
    assert overlay.show_asset("missing.png") is False
    assert overlay.isVisible() is False
