"""Overlay window utilities for the Streaming Companion Tool.

This module provides the `OverlayWindow` class for displaying PNG and GIF
assets on-screen using PySide6. The window is borderless, transparent, and
auto-hides after a configurable duration.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

_LOGGER = logging.getLogger(__name__)


class OverlayWindow(QWidget):
    """Display PNG/GIF overlays in a frameless, transparent window.

    The overlay window keeps a `QLabel` as its sole child. Static images are
    rendered via `QPixmap`, while GIF animations are played through `QMovie`.
    A single-shot `QTimer` hides the window after the configured duration.

    Args:
        auto_hide_ms: Default duration before the window auto-hides. Set to
            ``0`` to keep the overlay visible until hidden manually.
        parent: Optional parent widget.
    """

    def __init__(
        self, *, auto_hide_ms: int = 1500, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self._auto_hide_ms = auto_hide_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._movie: Optional[QMovie] = None

        self._configure_window_flags()

    def show_asset(
        self,
        asset_path: str,
        *,
        duration_ms: Optional[int] = None,
        position: Optional[Tuple[int, int]] = None,
        size: Optional[Tuple[int, int]] = None,
    ) -> bool:
        """Display an asset from disk.

        Args:
            asset_path: Absolute or relative path to the PNG/GIF file.
            duration_ms: Optional override for the auto-hide timer. ``None``
                uses the instance default. ``0`` disables auto-hiding.
            position: Optional ``(x, y)`` coordinates for the overlay. When
                omitted, the window is left at its current position.
            size: Optional ``(width, height)`` to resize the overlay. When
                omitted, uses the original asset size.

        Returns:
            ``True`` when the asset was successfully displayed, otherwise
            ``False``.
        """

        path = Path(asset_path)
        if not path.exists() or not path.is_file():
            _LOGGER.warning("Overlay asset missing or not a file: %s", path)
            return False

        is_gif = path.suffix.lower() == ".gif"
        if is_gif:
            if not self._prepare_movie(path, size):
                return False
        else:
            if not self._prepare_pixmap(path, size):
                return False

        self._start_timer(duration_ms)

        if position is not None:
            self.move(QPoint(position[0], position[1]))

        self.show()
        self.raise_()
        return True

    def is_animating(self) -> bool:
        """Return ``True`` when a GIF is currently playing."""

        return bool(self._movie and self._movie.state() == QMovie.Running)

    def is_auto_hide_active(self) -> bool:
        """Return ``True`` when the auto-hide timer is active."""

        return self._timer.isActive()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._stop_animation()
        self._label.clear()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_animation()
        super().closeEvent(event)

    def _configure_window_flags(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

    def _prepare_pixmap(self, path: Path, size: Optional[Tuple[int, int]] = None) -> bool:
        pixmap = QPixmap(path.as_posix())
        if pixmap.isNull():
            _LOGGER.warning("Overlay image failed to load: %s", path)
            return False

        # Resize if size is specified
        if size is not None:
            pixmap = pixmap.scaled(
                size[0], size[1], Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
            )

        self._stop_animation()
        self._label.setPixmap(pixmap)
        self._resize_to_pixmap(pixmap)
        return True

    def _prepare_movie(self, path: Path, size: Optional[Tuple[int, int]] = None) -> bool:
        movie = QMovie(path.as_posix())
        if not movie.isValid():
            _LOGGER.warning("Overlay animation invalid: %s", path)
            return False

        # Set scaled size if specified
        if size is not None:
            from PySide6.QtCore import QSize
            movie.setScaledSize(QSize(size[0], size[1]))

        self._stop_animation()
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        movie.start()
        if movie.currentPixmap().isNull():
            movie.jumpToFrame(0)

        self._label.setMovie(movie)
        self._resize_to_pixmap(movie.currentPixmap())
        self._movie = movie
        return True

    def _resize_to_pixmap(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        size = pixmap.size()
        self._label.resize(size)
        self.resize(size)

    def _start_timer(self, duration_ms: Optional[int]) -> None:
        effective = self._auto_hide_ms if duration_ms is None else duration_ms
        if effective and effective > 0:
            self._timer.start(effective)
        else:
            self._timer.stop()

    def _stop_animation(self) -> None:
        if self._movie:
            self._movie.stop()
            self._label.setMovie(None)
            self._movie = None
