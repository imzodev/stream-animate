"""Command-line entry point for the Streaming Companion Tool MVP."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
import os

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Default to disabling XCB GLX integration to avoid GLX requirements for video rendering
os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")

from stream_companion import application, model_downloader, registry  # noqa: E402

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
    parser.add_argument(
        "--stt-status",
        action="store_true",
        help="Print the current STT configuration and exit (no GUI, no listener)",
    )
    parser.add_argument(
        "--preload-stt",
        action="store_true",
        help=(
            "Download the Whisper model used by the STT config and exit. "
            "Use --model NAME to download a specific model without "
            "reading the config."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Whisper model name to use with --preload-stt "
            "(e.g. tiny, base, small, medium, large, turbo). "
            "Defaults to the model declared in the STT config."
        ),
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


def _format_cache_status(model_name: str) -> str:
    """Return a human-readable cache status for a model name."""

    if model_name not in model_downloader.available_models():
        return f"unknown model: {model_name!r}"
    if model_downloader.is_model_cached(model_name):
        path = model_downloader.model_path(model_name)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return f"cached at {path} ({_human_bytes(size)})"
    return "not cached"


def _human_bytes(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def print_stt_status() -> None:
    """Print the parsed STT configuration and exit."""

    stt_config = registry.get_stt_config()
    payload = {
        "config_file": str(ROOT_DIR / "config" / "shortcuts.json"),
        "stt_config": stt_config.__dict__ if stt_config is not None else None,
    }
    if stt_config is None:
        print("STT: no 'stt' block found in config/shortcuts.json")
    else:
        cache_status = _format_cache_status(stt_config.model)
        print(
            f"STT: enabled={stt_config.enabled} "
            f"always_on={stt_config.always_on} "
            f"hotkey={stt_config.hotkey!r} "
            f"model={stt_config.model!r} "
            f"language={stt_config.language!r} "
            f"activation_mode={stt_config.activation_mode()!r} "
            f"device={stt_config.device!r} "
            f"chunk_seconds={stt_config.chunk_seconds} "
            f"sample_rate={stt_config.sample_rate}"
        )
        print(f"Whisper model {stt_config.model!r}: {cache_status}")
    print(json.dumps(payload, indent=2, default=str))


def preload_stt_model(model_name: str) -> int:
    """Download the requested model and return a process exit code."""

    if model_name not in model_downloader.available_models():
        print(
            f"error: unknown model {model_name!r}. "
            f"Available: {', '.join(model_downloader.available_models())}",
            file=sys.stderr,
        )
        return 2
    if model_downloader.is_model_cached(model_name):
        path = model_downloader.model_path(model_name)
        _LOGGER.info("Whisper model '%s' is already cached at %s", model_name, path)
        return 0
    _LOGGER.info("Preloading Whisper model '%s'…", model_name)
    try:
        model_downloader.download_model(model_name)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to preload Whisper model '%s': %s", model_name, exc)
        return 1
    return 0


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)

    if args.stt_status:
        print_stt_status()
        return

    if args.preload_stt:
        model_name = args.model
        if model_name is None:
            stt_config = registry.get_stt_config()
            if stt_config is None or not stt_config.enabled:
                print(
                    "error: --preload-stt requires an STT config or --model NAME",
                    file=sys.stderr,
                )
                sys.exit(2)
            model_name = stt_config.model
        sys.exit(preload_stt_model(model_name))

    if args.config:
        from stream_companion import configurator

        configurator.run_configurator()
    else:
        ensure_assets_exist()
        stt_config = registry.get_stt_config()
        if stt_config is not None:
            _LOGGER.info(
                "Loaded STT config: enabled=%s always_on=%s hotkey=%r model=%r language=%r",
                stt_config.enabled,
                stt_config.always_on,
                stt_config.hotkey,
                stt_config.model,
                stt_config.language,
            )
        else:
            _LOGGER.info("No STT config in shortcuts.json (STT disabled)")
        application.run_application(registry.iter_shortcuts(), stt_config=stt_config)


if __name__ == "__main__":
    main()
