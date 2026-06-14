# Streaming Companion Tool

Turn your keyboard into a live-production command center. The Streaming Companion Tool listens for your custom hotkeys, unleashing audio stingers and on-screen overlays that delight your audience without breaking your flow.

## Highlights
- **Always-on control:** Define global shortcuts (e.g., `<ctrl>+<alt>+c`) that work even when your streaming software is focused.
- **Instant reactions:** Fire sound bites (WAV/MP3) and overlays (PNG/GIF/JPG/Video) with a tap.
- **Speech-to-text typing:** Use OpenAI Whisper to dictate into any focused text field. Toggle with a hotkey or run it always-on, in 99+ languages with auto-detect.
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
  - **Start/Stop STT** - Toggle the speech-to-text engine (only shown when STT is enabled in the configurator)
  - **Quit** - Gracefully exit the application
- The tray icon provides a clean way to manage the background process without terminal access

## Speech-to-Text Typing
Stream Companion can transcribe your voice in real time and type the result into whichever text field currently has focus. Powered by [OpenAI Whisper](https://github.com/openai/whisper), it runs entirely on your machine — no cloud calls, no API keys.

### Quick Start
1. Open the configurator (`python main.py --config`) and switch to the **Speech-to-Text** tab.
2. Tick **Enable speech-to-text (STT)**.
3. Choose an activation mode:
   - **Always on** — the engine starts transcribing as soon as the app launches.
   - **Toggle via hotkey** — capture a global hotkey (e.g. `<ctrl>+<alt>+space`); press once to start dictating, again to stop.
4. By default the engine both **types dictated text into the focused window** and **triggers voice shortcuts** (see [Voice Triggers](#voice-triggers) below). You can untick either sub-option to disable that side-effect.
5. Pick a **Whisper model**. `turbo` is recommended for live dictation; use `base` or `small` on lower-end hardware.
6. Select your **Language** (`auto` lets Whisper detect, or pick a specific code).
7. Pick an **Input device** (or leave it on *System default*).
8. Click **Save STT Settings**. The save returns immediately, but if the model is not yet cached the tool will start downloading it in the background. **Watch the terminal for the progress log** — you'll see lines like:
   ```
   [INFO] stream_companion.model_downloader: Whisper model 'large' not cached; downloading (~2.9 GiB) to /home/irving/.cache/whisper/large-v3.pt …
   [INFO] stream_companion.model_downloader: Whisper model 'large' download: 500.0 MiB / 2.9 GiB (17%)
   [INFO] stream_companion.model_downloader: Whisper model 'large' download: 1000.0 MiB / 2.9 GiB (34%)
   …
   [INFO] stream_companion.model_downloader: Whisper model 'large' download complete: /home/irving/.cache/whisper/large-v3.pt
   ```
   You can close the configurator during the download; it continues in the background.
9. Re-launch the listener (`python main.py --log-level INFO`) and click into any text field. Start speaking — each phrase is typed into the focused window followed by a space.

### Voice Triggers
Each shortcut can declare a **trigger word** that, when spoken, fires the shortcut (plays its sound, displays its overlay, or runs the same code path as the hotkey). The triggers and the focused-window typing are **independent** — you can keep voice triggers active while disabling typing, or vice versa.

#### Configuring a trigger word
1. Open the configurator and select a shortcut.
2. In the **Voice Trigger Word (optional)** field, type the word (e.g. `fail`, `niño`, `OK`). Matching is:
   - **Case-insensitive** — `Fail` and `fail` both match.
   - **Word-boundary** — `fail` matches "what a fail" but NOT "failful" or "failsafe".
   - **Whole-word token** — the same word is required even when preceded/followed by punctuation.
3. The same trigger word on two shortcuts will only fire the first one (the second is reported as a warning in the configurator).
4. Click **Save Changes**. The trigger word is now active.

#### Per-shortcut cooldown
To avoid firing the same shortcut many times on overlapping audio chunks of one utterance, each shortcut has a per-shortcut cooldown (default **1500 ms**, configurable via `trigger_cooldown_ms` in the STT block of `config/shortcuts.json`).

#### JSON form
```json
{
  "hotkey": "<ctrl>+<alt>+f",
  "sound": "sounds/celebration.wav",
  "trigger_word": "fail"
}
```

#### Independent control: typing vs triggers
- The hotkey/always-on toggle controls **when the STT engine runs**.
- `type_into_focused_window` (default `true`) decides whether the transcribed text is typed.
- `voice_triggers_enabled` (default `true`) decides whether the engine scans phrases for trigger words.

So with `always_on: false` + a hotkey + `voice_triggers_enabled: true` + `type_into_focused_window: false`, your hotkey turns on listening (which fires voice shortcuts on detected words) but does NOT type into any focused window — useful when you only want reactions on stream.

### Tray / Hotkey Controls
- The tray menu shows **Start STT** / **Stop STT** based on the current state.
- In hotkey mode, the configured hotkey toggles listening on/off at any time.

### Pre-downloading the Whisper model
The first time Whisper transcribes audio for a given model, it has to download a multi-gigabyte checkpoint. To avoid that surprise mid-stream, the configurator checks the cache when you click **Save STT Settings**:

- If the model is already cached, nothing happens — the save just persists.
- If the model is not cached, a background thread starts the download and logs live progress to the terminal at roughly every 10% (`stream_companion.model_downloader: Whisper model 'X' download: …%`).

The download continues even if you close the configurator; you can check on it with `python main.py --stt-status` (which now also reports cache state) or trigger it explicitly with `python main.py --preload-stt [--model NAME]`.

### Tips
- **First run downloads the Whisper model** (a few hundred MB for `turbo`); subsequent starts are instant.
- A short **silence threshold** (RMS) prevents typing of pure background noise — increase it if you see unwanted characters.
- **Dedup window** keeps the last N characters you typed so overlapping audio chunks don't double-type.
- The list of supported languages is the union of Whisper's tokenizer + `auto`.

### Requirements
The listener adds two new system dependencies:
- `openai-whisper` (transcription model)
- `sounddevice` (microphone capture via PortAudio)
- `numpy` (audio buffer math)

On Debian/Ubuntu you may also need:
```bash
sudo apt-get install ffmpeg libportaudio2
```

`ffmpeg` is required by Whisper for first-use model downloads and any future audio-format conversions.

### JSON Configuration
STT settings can also be edited directly in `config/shortcuts.json`:
```json
{
  "version": "1.2.0",
  "shortcuts": [],
  "stt": {
    "enabled": true,
    "always_on": false,
    "hotkey": "<ctrl>+<alt>+space",
    "language": "auto",
    "model": "turbo",
    "device": null,
    "chunk_seconds": 4.0,
    "sample_rate": 16000,
    "append_space": true,
    "silence_rms_threshold": 0.005,
    "dedup_window": 64
  }
}
```
- `enabled` — master switch. When `false`, the engine is not started.
- `always_on` — when `true`, dictation begins on app start. When `false`, the `hotkey` toggles it.
- `hotkey` — pynput-style hotkey, e.g. `<ctrl>+<alt>+space`. Ignored when `always_on` is `true`.
- `language` — a Whisper language code, or `auto`.
- `model` — `tiny`, `base`, `small`, `medium`, `large`, or `turbo` (recommended).
- `device` — `null` for the system default, or the `sounddevice` input device index.
- `chunk_seconds` — how many seconds of audio to transcribe at a time (0.5–30).
- `append_space` — append a space after each phrase so concatenated chunks don't run together.
- `silence_rms_threshold` — skip chunks whose RMS volume is below this value.
- `dedup_window` — number of recent characters used for tail-based dedup.

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
- **STT shows as disabled in tray:** The tray menu reflects the engine state in real time. If the **Start/Stop STT** entry is greyed out, run with `--log-level INFO` and look for messages from `stream_companion.stt` and `stream_companion.application`. Common causes:
  - The hotkey couldn't be registered (bad format). The engine accepts both `<ctrl>+<alt>+9` and bare `ctrl+alt+9` and is logged as the canonical form.
  - The microphone device couldn't be opened (e.g. wrong device index, missing PortAudio, permission denied). Look for `Cannot start STT microphone:` in the log.
  - The Whisper model failed to load (network blocked on first use, or insufficient disk/memory).
- **Debugging STT interactively:**
  - `python main.py --stt-status` prints the parsed STT configuration and exits without starting the listener. Use this to confirm the config file is being read correctly. The output also shows whether the configured Whisper model is already cached locally.
  - `python main.py --preload-stt` downloads the Whisper model declared in the config and exits. Combine with `--model NAME` (e.g. `tiny`, `base`, `small`, `medium`, `large`, `turbo`) to download a specific model. Useful for scripted installs: `python main.py --preload-stt --model turbo` will fetch the model with live progress in the terminal and exit before the GUI ever starts.
  - `python main.py --log-level DEBUG` shows every state transition (engine start/stop, hotkey toggle, mic open/close, transcription calls, typed character counts).
  - The `STTEngine.status()` method returns a JSON-serializable dict with `running`, `active`, `mic_open`, `transcriber_loaded`, `model`, `language`, `device`, `chunk_seconds`, `always_on`, `hotkey`, `typed_chars`, `started_at`, and `last_error` — useful for custom diagnostics.

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
