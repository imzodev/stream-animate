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

## Phase 2 – JSON Configuration Loader
Goal: Externalize shortcut definitions and assets.
- Design configuration schema version 1.0 (`config/schema.json`).
- Implement `ConfigLoader` that reads JSON, validates fields, and hydrates runtime models.
- Add startup default config creation when file is missing.
- Extend Phase 1 registry to load shortcuts dynamically from config file.
- Add error messaging for malformed or missing assets.
- Document configuration workflow and sample files.

## Phase 3 – Desktop Configurator UI
Goal: Allow streamers to manage shortcuts without editing JSON manually.
- Design PySide6 UI layout (list view + detail editor) for shortcuts.
- Implement forms for sound/image selection using native file dialogs.
- Add hotkey capture widget to record new combinations safely.
- Support CRUD operations (add/edit/delete) with validation and live preview.
- Persist updates back to JSON with transactional writes.
- Provide onboarding walkthrough or tooltip hints within the UI.

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

## Ongoing Engineering Practices
- Maintain automated formatting/linting/testing via `run_checks.py`.
- Add unit/integration tests as features land; expand coverage per phase.
- Track issues in GitHub by referencing the relevant phase/task from this plan.

## Issue Mapping
- Phase 1 initial issues: `#1` Hotkey manager, `#2` Sound player service, `#3` Overlay window, `#4` Wire MVP workflow, `#5` Logging & docs refresh.
- Future phases will receive additional issues as design details firm up.
