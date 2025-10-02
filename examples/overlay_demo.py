"""Manual demonstration script for the OverlayWindow component."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from stream_companion.overlay import OverlayWindow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", type=Path, help="Path to a PNG or GIF asset")
    parser.add_argument(
        "--duration",
        type=int,
        default=1500,
        help="Auto-hide duration in ms (0 to persist)",
    )
    parser.add_argument("--x", type=int, default=None, help="Overlay X position")
    parser.add_argument("--y", type=int, default=None, help="Overlay Y position")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    window = OverlayWindow(auto_hide_ms=args.duration)
    window.show_asset(
        args.asset.as_posix(),
        duration_ms=args.duration,
        position=(
            (args.x, args.y) if args.x is not None and args.y is not None else None
        ),
    )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
