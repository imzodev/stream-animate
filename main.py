"""Command-line entry point for the Streaming Companion Tool MVP."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from stream_companion import application, registry  # noqa: E402

_LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (default: INFO)",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Launch the desktop configurator UI instead of the runtime listener",
    )
    return parser.parse_args()


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def ensure_assets_exist() -> None:
    for shortcut in registry.iter_shortcuts():
        if shortcut.sound_path and not Path(shortcut.sound_path).is_file():
            _LOGGER.warning("Sound asset missing: %s", shortcut.sound_path)
        if shortcut.overlay and not Path(shortcut.overlay.file).is_file():
            _LOGGER.warning("Overlay asset missing: %s", shortcut.overlay.file)


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)

    if args.config:
        from stream_companion import configurator

        configurator.run_configurator()
    else:
        ensure_assets_exist()
        application.run_application(registry.iter_shortcuts())


if __name__ == "__main__":
    main()
