# Streaming Companion Tool – Specification Document

## Project Overview
A cross-platform desktop application (Linux first, later Windows and macOS) that runs in the background and allows streamers to trigger sounds, images, or GIF overlays on the screen using custom keyboard shortcuts. This is similar to a software-only StreamDeck.

---

## Goals
1. Capture global hotkeys.
2. Play assigned sound effects.
3. Display image or GIF overlays on screen at configurable positions.
4. Provide a GUI to configure shortcuts, assets, and overlay settings.
5. Package into executables for Linux, Windows, and macOS.

---

## Technology Stack
- **Language:** Python 3.10+
- **GUI & overlay:** PyQt5 or PySide6
- **Hotkeys:** pynput (cross-platform global hotkeys)
- **Sound playback:** pygame.mixer
- **Configuration storage:** JSON
- **Packaging:** PyInstaller

---

## Features

### Phase 1 – Core MVP
- Register global hotkeys (e.g., `<ctrl>+<alt>+c`).
- On hotkey press:
  - Play a sound file (WAV/MP3).
  - Display a transparent overlay (PNG/GIF) at a fixed position.
  - Auto-hide overlay after a configurable duration.

### Phase 2 – Config System
- Load a JSON file at startup containing shortcut definitions:
  ```json
  {
    "shortcuts": [
      {
        "hotkey": "<ctrl>+<alt>+c",
        "sound": "sounds/celebration.wav",
        "overlay": {
          "file": "images/celebration.gif",
          "x": 100,
          "y": 200,
          "duration": 3000
        }
      }
    ]
  }
  ```
- Allow multiple shortcuts.
- If no config exists, create a default one.

### Phase 3 – GUI Configurator
- Interface for managing shortcuts.
- Features:
  - View a list of shortcuts.
  - Add/Edit/Delete shortcuts.
  - File picker for sound/image/gif.
  - Hotkey selector.
  - Input fields for overlay position and duration.
  - Save changes to JSON file.

### Phase 4 – Cross-Platform Packaging
- **Linux:** Single binary (`.AppImage` or CLI + `.deb`).
- **Windows:** `.exe` using PyInstaller.
- **macOS:** `.app` bundle with proper permissions.
- Ensure hotkey capture requests required accessibility permissions on macOS.

### Phase 5 – Enhancements (Future)
- Overlay animations (fade in/out).
- OBS WebSocket integration (trigger OBS scenes/sources).
- Multi-monitor support.
- Profiles/scenes (switch between shortcut sets).
- Cloud config sync.

---

## Non-Functional Requirements
- **Cross-platform:** Linux (primary), Windows, macOS.
- **Low resource usage:** Should run unobtrusively in background during streams.
- **Customizability:** Users can freely assign hotkeys, assets, and positions.
- **Simplicity:** Streamers should be able to configure without editing code.

---

## Deliverables
1. **Phase 1 Prototype:** Hotkey triggers sound + overlay.
2. **Phase 2 JSON Loader:** Load and trigger based on config.
3. **Phase 3 GUI Configurator:** User-facing tool to manage shortcuts.
4. **Phase 4 Builds:** Executables for Linux, Windows, macOS.
5. **Phase 5 Features:** (optional enhancements).
