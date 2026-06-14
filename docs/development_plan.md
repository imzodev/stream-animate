# Streaming Companion Tool — Development Plan

This roadmap translates the high-level specification in `spec_document.md` into actionable phases and tasks. Each phase concludes with a working milestone that can be demonstrated to end users or stakeholders.

## Phase 0 – Foundations (Complete)
- Project README with usage guidance (`README.md`).
- MIT licensing (`LICENSE`).
- Virtual environment and dependency management (`requirements.txt`, `requirements-dev.txt`).
- Developer tooling workflow via `run_checks.py`.
- GitHub repository initialized and baseline configuration in place.

## Phase 1 – Core Hotkey Trigger MVP
Goal: Pressing a global shortcut plays a sound and shows an overlay.
- Implement `HotkeyManager` using `pynput` to capture global shortcuts.
- Build `SoundPlayer` service around `pygame.mixer` with preloading for low-latency playback.
- Create `OverlayWindow` using PySide6 to show PNG/GIF with transparency and auto-hide timer.
- Define a minimal in-memory shortcut registry (hard-coded for now) that connects the manager, player, and overlay.
- Provide a simple telemetry/logging layer to trace trigger events and errors.
- Update `README.md` with MVP usage notes and troubleshooting.
    - ✅ Logging wired across MVP services and README refreshed with run/troubleshooting guidance (Issue #5).

## Phase 2 – JSON Configuration Loader
Goal: Externalize shortcut definitions and assets.
- ✅ `config/schema.json` and `config/shortcuts.sample.json` define the JSON structure.
- ✅ `config_loader` module validates JSON, hydrates runtime models, and auto-creates configs.
- ✅ Registry loads shortcuts dynamically with fallback logging.
- Document configuration workflow and sample files.

## Phase 3 – Desktop Configurator UI (Complete)
Goal: Allow streamers to manage shortcuts without editing JSON manually.
- ✅ Design PySide6 UI layout (list view + detail editor) for shortcuts.
- ✅ Implement forms for sound/image selection using native file dialogs.
- ✅ Add hotkey capture widget to record new combinations safely.
- ✅ Support CRUD operations (add/edit/delete) with validation and live preview.
- ✅ Persist updates back to JSON with transactional writes via `save_shortcuts()`.
- ✅ Added `--config` CLI flag to launch configurator mode.
- ✅ Implemented validation for duplicate hotkeys and missing files.
- ✅ Added unit tests for config save/load functionality.
- ✅ Updated README with configurator usage instructions (Issue #13).

## Phase 4 – Cross-Platform Packaging
Goal: Deliver installable builds for Linux, Windows, and macOS.
- Create PyInstaller spec files per platform and automate build scripts.
- Add platform-specific post-processing (AppImage/DEB, .exe, .app bundle).
- Document platform prerequisites and accessibility permissions (especially macOS hotkeys).
- Set up CI/CD workflow to produce release artifacts.

## Phase 5 – Strategic Enhancements (Backlog)
Future-facing improvements that can be scheduled after the core experience ships.
- Overlay effects (fade in/out, animated transitions).
- OBS WebSocket integration for scene/source triggers.
- Multi-monitor positioning and per-display previews.
- Profile management (switch shortcut sets on the fly).
- Optional cloud sync for configurations.

## Phase 6 – Speech-to-Text Typing (Complete)
Goal: Add a configurable voice-to-text pipeline that types into whichever window is focused, with hotkey or always-on activation.

- ✅ `STTConfig` dataclass with `enabled`, `always_on`, `hotkey`, `language`, `model`, `device`, `chunk_seconds`, `sample_rate`, `append_space`, `silence_rms_threshold`, `dedup_window`.
- ✅ `whisper` Python API wrapped by `WhisperTranscriber` with lazy model loading.
- ✅ `sounddevice` capture with rolling buffer, RMS silence gating, and pluggable module for tests.
- ✅ `pynput.keyboard.Controller` based `TextTyper` with rolling-window dedup to avoid double-typing overlapping chunks.
- ✅ Background `STTEngine` orchestrator: capture → transcribe → type on a daemon thread; safe to start/stop from any thread; toggleable active state.
- ✅ Application wiring: `Application.start()` boots the engine; `always_on` activates immediately, `hotkey` registers a toggle in `HotkeyManager`.
- ✅ System tray: live "Start/Stop STT" entry plus tooltip reflecting engine state.
- ✅ Configurator: dedicated **Speech-to-Text** tab with model/language/device selectors, capture and typing options, and per-field validation.
- ✅ JSON schema extended (`config/schema.json`) and `save_config()` preserves the `stt` block across partial saves.
- ✅ Unit tests: capture lifecycle, chunk framing, transcriber lazy-load + language handling, typer dedup, engine hotkey vs always-on modes, silence gating, config round-trip.

## Phase 7 – Whisper model pre-download (Complete)
Goal: Avoid blocking the first dictation on a multi-GB download by fetching the model the moment the user saves the STT config, with live progress in the terminal.

- ✅ New `src/stream_companion/model_downloader.py`:
  - `is_model_cached(model_name, cache_dir=None)` — file existence + SHA256 check
  - `download_model(model_name, cache_dir=None, on_progress=...)` — drives `urllib.request.urlopen` directly, logs progress through a custom `_LoggerTqdm` that prints every ~10%, and validates the SHA256 on completion
  - `start_background_download(model_name, on_complete, on_error)` — fire-and-forget thread; tracks the thread in a module-level registry
  - `wait_for_pending_downloads` / `active_downloads` for shutdown
  - Mirrors the model list and URL→SHA convention from `whisper._MODELS`
- ✅ Configurator: on save, checks the cache and starts a background download if the configured model is missing. The save dialog still appears immediately; the download continues even if the user closes the configurator.
- ✅ `python main.py --preload-stt [--model NAME]` — one-shot CLI for scripted installs; live progress in the terminal.
- ✅ `python main.py --stt-status` — now also reports whether the configured model is cached and at what path/size.
- ✅ 15 new unit tests in `tests/test_model_downloader.py` covering cache detection, model name validation, background thread management, SHA validation, error callbacks, and human-readable byte formatting.

## Phase 8 – Voice Triggers (Complete)
Goal: Let the user attach a **trigger word** to any shortcut so that speaking the word fires the shortcut (sound + overlay), independently of the focused-window typing flow.

- ✅ `Shortcut` model gains `trigger_word: Optional[str]` and `normalized_trigger_word()` helper.
- ✅ `STTConfig` gains `trigger_cooldown_ms: int = 1500`, `type_into_focused_window: bool = True`, and `voice_triggers_enabled: bool = True`.
- ✅ New `src/stream_companion/triggers.py`:
  - `find_trigger_words(phrase, words)` — case-insensitive, word-boundary match using a Unicode-aware regex; preserves the order in which the words appear in the phrase.
  - `TriggerMatcher` class with `register` / `unregister` / `dispatch` / `match` / `clear`, per-word cooldown via injectable clock, `on_skip` hook, and fire/skip counters for diagnostics.
  - `build_matcher_from_shortcuts(shortcuts, cooldown_ms=...)` factory that normalizes trigger words and reports duplicates.
- ✅ `STTEngine` runs the engine loop independently of the typing active flag when triggers are enabled. `_process_chunk` accepts a `type_into_window` parameter so transcription still happens for trigger scanning even when typing is paused. New `set_triggers_enabled` method.
- ✅ `Application._on_stt_phrase` emits a Qt signal; `_handle_stt_phrase_in_main_thread` matches the phrase against the live shortcut list and fires matching shortcuts through the existing sound+overlay pipeline. The matcher is rebuilt on `set_stt_config` so the new cooldown takes effect immediately.
- ✅ Configurator: the Shortcut Details panel now has a **Voice Trigger Word** input. The Speech-to-Text tab gains two sub-checkboxes: "Type dictated text into the focused window" and "Trigger voice shortcuts". Both default to on.
- ✅ Schema: `config/schema.json` extended with `trigger_word` on shortcuts, and the new `trigger_cooldown_ms` / `type_into_focused_window` / `voice_triggers_enabled` on the STT block. Config version bumped to 1.3.0.
- ✅ Tests: 27 new tests in `tests/test_triggers.py` plus 2 round-trip tests in `tests/test_config_loader.py` and 2 in `tests/test_stt.py` for the new engine behavior. Total: 111 tests passing.

## Phase 9 – Tray status indicators (Complete)
Goal: Show two independent visual indicators in the system tray so the user can see at a glance whether STT is listening and whether typing is active, even with the menu closed.

- ✅ New `src/stream_companion/tray_indicators.py`:
  - `TrayIndicatorState` dataclass with `enabled`, `stt_active`, `typing_active` and a friendly `tooltip`.
  - `compose_state(...)` factory that derives the indicator state from raw engine flags (`stt_configured`, `engine_running`, `triggers_enabled`, `typing_active`).
  - `compose_tray_icon(state, size=64, base_pixmap=None)` paints two corner dots on the base icon: a red dot in the top-right (STT active) and a blue dot in the bottom-right (typing active). Falls back to a synthesized "SC" badge when no asset is present.
  - `find_base_icon_pixmap()` discovers the project's `assets/icon.png` / `tray_icon.png` / `icon.ico`.
- ✅ `STTEngine` exposes a public `triggers_enabled` property so the application can read the flag from any thread.
- ✅ `TrayIcon` rewritten:
  - The state provider now returns a `TrayIndicatorState` (or `None` for "STT not configured").
  - `refresh_stt_label()` recomposes the icon on every state change. A hashed state key skips no-op refreshes.
  - The menu item label reflects the current sub-state: "Start STT", "Stop STT (currently typing)", "Stop STT (listening for triggers)", or "STT (disabled in config)".
  - **Left-click** on the tray icon now toggles STT (single + double click both work), matching media-app conventions.
- ✅ `Application._stt_state` returns the new `TrayIndicatorState`; the existing observer continues to refresh the tray on every state change.
- ✅ Tests: 17 new tests in `tests/test_tray_indicators.py` covering state composition, color presence, fallback paths, and the no-dots cases; 8 new tests in `tests/test_tray_icon.py` covering state-key deduping, menu hiding, left-click toggling, and label transitions. Total: 136 tests passing.

## Ongoing Engineering Practices
- Maintain automated formatting/linting/testing via `run_checks.py`.
- Add unit/integration tests as features land; expand coverage per phase.
- Track issues in GitHub by referencing the relevant phase/task from this plan.

## Issue Mapping
- Phase 1 initial issues: `#1` Hotkey manager, `#2` Sound player service, `#3` Overlay window, `#4` Wire MVP workflow, `#5` Logging & docs refresh.
- Phase 3 issues: `#13` Desktop configurator UI (complete).
- Future phases will receive additional issues as design details firm up.
