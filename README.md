# Streaming Companion Tool

Turn your keyboard into a live-production command center. The Streaming Companion Tool listens for your custom hotkeys, unleashing audio stingers and on-screen overlays that delight your audience without breaking your flow.

## Highlights
- **Always-on control:** Define global shortcuts (e.g., `<ctrl>+<alt>+c`) that work even when your streaming software is focused.
- **Instant reactions:** Fire sound bites (WAV/MP3) and overlays (PNG/GIF/JPG/Video) with a tap.
- **Configurable visuals:** Choose overlay positions, screen duration, and transparency to match your brand.
- **Desktop configurator:** A streamlined UI with file-browser selection, live preview, and hotkey capture makes setup effortless—no manual JSON editing required.
- **Low footprint:** Runs quietly in the background so you can focus on the show.

## Quick Start
1. **Install requirements**
   ```bash
   pip install -r requirements.txt
   ```
2. **Prepare assets**
   - Drop audio clips (WAV/MP3) and overlays (PNG/GIF/JPG/MP4/WEBM/MOV/M4V/AVI/MKV) inside the `assets/` directory.
3. **Configure shortcuts (GUI method - recommended)**
   ```bash
   python main.py --config
   ```
   - Use the desktop configurator to add, edit, and preview shortcuts with a user-friendly interface.
   - Click "Add" to create new shortcuts, use "Browse..." buttons to select files, and "Preview" to test them.
   - Click "Save Changes" when done.
   - To use activator + chorded suffixes, see "Activator + Chorded Suffixes" below.
4. **Alternative: Manual JSON configuration**
   - Copy the template: `cp config/shortcuts.sample.json config/shortcuts.json`.
   - Edit `config/shortcuts.json` to add your hotkeys, sound paths, and overlay settings.
5. **Run automated checks (optional but recommended)**
   ```bash
   python run_checks.py
   ```
   This formats the codebase, runs linting, and executes the test suite.
6. **Launch the companion**
   ```bash
   python main.py --log-level INFO
   ```
   - The application runs in the background with a system tray icon.
   - Right-click the tray icon to access the menu:
     - **Open Configurator** - launch the GUI to edit shortcuts
     - **Quit** - gracefully exit the application
7. **Trigger a shortcut**
   - Press one of the hotkeys you configured and watch the overlay/sound fire instantly.

## Configure Your Hotkeys

### Desktop Configurator (Recommended)
Launch the visual configurator to manage shortcuts without editing files:
```bash
python main.py --config
```

**Features:**
- **Add/Edit/Delete shortcuts** with a user-friendly interface
- **Hotkey capture widget** - click "Capture" and press your desired key combination
- **File browsers** for selecting sounds and overlays
- **Visual position picker** - click "Pick Position..." to select overlay location with your mouse
- **Live preview** - test sounds and overlays before saving
- **Validation** - automatic checks for duplicate hotkeys and missing files
- **Position controls** - set X/Y coordinates and duration with spin boxes or visual picker

### Manual JSON Configuration
Shortcuts are stored in `config/shortcuts.json`. You can also edit this file directly:

```json
{
  "version": "1.1.0",
  "shortcuts": [
    {"hotkey": "<ctrl>+<alt>+1", "sound": "assets/sounds/celebration.wav"},

    {"suffix": "b", "overlay": {"file": "assets/overlays/sample.gif", "x": 960, "y": 540, "duration": 1500}},
    {"suffix": ["g", "h"], "overlay": {"file": "assets/overlays/sample.mp4", "x": 800, "y": 450, "duration": 0}}
  ],
  "activator": {"hotkey": "<ctrl>+<alt>+a", "mode": "press", "timeout_ms": 1500}
}
```

- **`hotkey`**: direct global hotkey (works without activator).
- **`suffix`**: one key or a sequence of keys pressed after the activator within `timeout_ms`.
- **`activator`**: the global key combo that arms the chord mode. Supported modes: `press` (implemented), `hold` (reserved).
- **`sound`**: path to audio file. WAV/MP3 supported.
- **`overlay`**: visual asset path with optional `x`, `y`, `duration` (ms), `width`, `height`.

### Activator + Chorded Suffixes

You can trigger shortcuts by first pressing a global activator and then one or more keys in sequence.

- **Enable**: set `activator.hotkey` and add shortcuts with `suffix` (string or array of strings).
- **Behavior** (press mode):
  - Press and release the activator to arm for `timeout_ms` (default 1500 ms).
  - Press suffix key(s) in order. Matching is prefix-aware; it waits for more keys if your input matches a sequence prefix.
  - Press `Esc` to cancel arming.
- **Notes**:
  - Avoid defining a direct `hotkey` that equals the activator, as it will fire immediately and can be confusing during testing.
  - Numeric suffixes use top-row digits; Numpad digits are not mapped by default.
  - Sequences of 2–3 keys are recommended to fit within the timeout.

## System Tray Control
When running in listener mode, the application displays a system tray icon for easy control:
- **Right-click the tray icon** to access:
  - **Open Configurator** - Edit shortcuts without restarting
  - **Quit** - Gracefully exit the application
- The tray icon provides a clean way to manage the background process without terminal access

## Logging & Troubleshooting
- **Structured logs:** All components share the standard Python logger. Use `--log-level DEBUG` for verbose output. Events include application start/stop, hotkey registration, trigger execution, and overlay/sound warnings.
- **Missing assets:** The app logs a warning if referenced files are absent. The configurator also validates files when saving and shows warnings for missing assets.
- **Configurator preview issues:** If sound or overlay previews fail, check that:
  - File paths are correct and files exist
  - Audio files are in supported formats (WAV/MP3)
  - Overlay files are in supported formats (PNG/GIF/JPG/MP4/WEBM/MOV/M4V/AVI/MKV)
- **System tray not showing:** If the tray icon doesn't appear, your desktop environment may not support system trays. You can still quit the application with `Ctrl+C` in the terminal.
- **Qt platform plugin:** If you see `Could not load the Qt platform plugin "xcb"`, install the missing dependencies (Ubuntu: `sudo apt-get install libxcb-cursor0`).
- **Global hotkeys on macOS:** Approve the accessibility prompt so the listener can capture shortcuts while other apps are focused.
- **Activator disarms immediately**: Ensure you’re on the latest code (we ignore the activator’s last press as a suffix), your suffix is defined (string or array), and there’s no direct hotkey equal to the activator.

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
