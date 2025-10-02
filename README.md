# Streaming Companion Tool

Turn your keyboard into a live-production command center. The Streaming Companion Tool listens for your custom hotkeys, unleashing audio stingers and on-screen overlays that delight your audience without breaking your flow.

## Highlights
- **Always-on control:** Define global shortcuts (e.g., `<ctrl>+<alt>+c`) that work even when your streaming software is focused.
- **Instant reactions:** Fire sound bites (WAV/MP3) and animated overlays (PNG/GIF) with a tap.
- **Configurable visuals:** Choose overlay positions, screen duration, and transparency to match your brand.
- **Configurator in development:** A streamlined UI with file-browser selection for sounds, media, and shortcuts is on the roadmap to make setup even easier.
- **Low footprint:** Runs quietly in the background so you can focus on the show.

## Quick Start
1. **Install requirements**
   ```bash
   pip install -r requirements.txt
   ```
2. **Prepare assets**
   - Drop audio clips (WAV/MP3) and overlays (PNG/GIF) inside the `assets/` directory.
3. **Define shortcuts**
   - Copy the template: `cp config/shortcuts.sample.json config/shortcuts.json`.
   - Edit `config/shortcuts.json` to add your hotkeys, sound paths, and overlay settings.
4. **Run automated checks (optional but recommended)**
   ```bash
   python run_checks.py
   ```
   This formats the codebase, runs linting, and executes the test suite.
5. **Launch the companion**
   ```bash
   python main.py --log-level INFO
   ```
6. **Trigger a shortcut**
   - Press one of the hotkeys you configured and watch the overlay/sound fire instantly.

## Configure Your Hotkeys
Shortcuts are authored in Python (Phases 2+ will move them to JSON). The minimal structure lives in `src/stream_companion/models.py`.

```python
from stream_companion.models import OverlayConfig, Shortcut

SHORTCUTS = [
    Shortcut(
        hotkey="<ctrl>+<alt>+1",
        sound_path="assets/sounds/celebration.wav",
        overlay=OverlayConfig(
            file="assets/overlays/celebration.gif",
            x=960,
            y=540,
            duration_ms=1500,
        ),
    )
]
```

- **`hotkey`** accepts `pynput`-style strings. Combine modifiers (`<ctrl>`, `<alt>`, `<shift>`, `<cmd>`) with letters or function keys.
- **`sound_path`** points to your audio file. WAV provides the snappiest playback.
- **`OverlayConfig`** lets you position overlays via `x`/`y` pixels from the top-left corner of the display and control visibility duration in milliseconds.

Phase 2 will introduce a JSON loader so you can manage hotkeys outside of Python; stay tuned.

## Logging & Troubleshooting
- **Structured logs:** All components share the standard Python logger. Use `--log-level DEBUG` for verbose output. Events include application start/stop, hotkey registration, trigger execution, and overlay/sound warnings.
- **Missing assets:** The app logs a warning if referenced files are absent. Confirm paths in `registry.py` and that assets exist under `assets/`.
- **Qt platform plugin:** If you see `Could not load the Qt platform plugin "xcb"`, install the missing dependencies (Ubuntu: `sudo apt-get install libxcb-cursor0`).
- **Global hotkeys on macOS:** Approve the accessibility prompt so the listener can capture shortcuts while other apps are focused.

## Power Tips
- **Layer multiple shortcuts:** Build themed reactions (victory, defeat, raid) with unique audio/visual combos.
- **Stay organized:** Keep assets in `assets/sounds/` and `assets/overlays/` to simplify sharing setups.
- **OBS pairing:** Pin the overlay window to your stream layout or capture the transparent window directly for picture-perfect reactions.

## Contributing & Support
- Open issues or suggestions in this repo to share new ideas or report glitches.
- Refer to `spec_document.md` for deeper design context and upcoming milestones.
- Pull requests that enhance usability, add new overlay effects, or expand shortcut options are welcome.

## License
This project is licensed under the [MIT License](./LICENSE), giving you freedom to use, modify, and redistribute the tool in open-source or commercial streams.
