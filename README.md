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
   pip install PySide6 pynput pygame
   ```
2. **Launch the companion**
   ```bash
   python main.py
   ```
3. **Trigger a shortcut**
   - Use one of the preloaded hotkeys from the configuration file (details below).
   - Watch the overlay pop in and the audio play instantly on your stream.

## Configure Your Hotkeys
Hotkeys live in a JSON file (default: `config/shortcuts.json`). Each entry maps a key combo to both audio and overlay actions.

```json
{
  "shortcuts": [
    {
      "hotkey": "<ctrl>+<alt>+c",
      "sound": "assets/sounds/celebration.wav",
      "overlay": {
        "file": "assets/overlays/celebration.gif",
        "x": 100,
        "y": 200,
        "duration": 3000
      }
    }
  ]
}
```

- **`hotkey`** accepts `pynput`-style strings. Combine modifiers (`<ctrl>`, `<alt>`, `<shift>`, `<cmd>`) with letters or function keys.
- **`sound`** points to your audio clip (WAV/MP3 recommended for near-zero latency).
- **`overlay.file`** supports PNG or GIF (including transparency). Position via `x`/`y` pixels from the top-left corner of your primary display.
- **`overlay.duration`** controls how long the graphic stays on screen (milliseconds).

On startup, the tool auto-creates a sample config if none exists so you can start tweaking immediately.

## Power Tips
- **Layer multiple shortcuts:** Build themed reactions (victory, defeat, raid) with unique audio/visual combos.
- **Stay organized:** Keep assets in `assets/sounds/` and `assets/overlays/` to simplify sharing setups.
- **Mac users:** Approve the accessibility prompt so global hotkeys can fire while other apps are active.
- **OBS pairing:** Pin the overlay window to your stream layout or capture the transparent window directly for picture-perfect reactions.

## Contributing & Support
- Open issues or suggestions in this repo to share new ideas or report glitches.
- Refer to `spec_document.md` for deeper design context and upcoming milestones.
- Pull requests that enhance usability, add new overlay effects, or expand shortcut options are welcome.

## License
This project is licensed under the [MIT License](./LICENSE), giving you freedom to use, modify, and redistribute the tool in open-source or commercial streams.
