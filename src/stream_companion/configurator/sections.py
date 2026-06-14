"""Per-section widgets for the configurator.

Each section is a :class:`QWidget` that owns its UI and exposes a small
read/populate/validate API for the main window to drive.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import STTConfig, Shortcut
from .constants import (
    MAX_DURATION_MS,
    MAX_OVERLAY_SIZE,
    MIN_OVERLAY_SIZE,
    PREVIEW_HEIGHT,
    PREVIEW_WIDTH,
    WHISPER_LANGUAGES,
    WHISPER_MODELS,
)
from .widgets import HotkeyCapture, PositionPicker, SingleKeyCapture

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# STT section
# ---------------------------------------------------------------------------


class STTSection(QWidget):
    """The Speech-to-Text settings tab.

    Public API:
        ``populate(config)`` — fill widgets from an :class:`STTConfig`.
        ``read()`` — build an :class:`STTConfig` (or ``None`` if disabled).
        ``validate(config)`` — return user-facing validation errors.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        # Apply default-disabled UI state
        self._on_enabled_toggled(False)
        self._on_activation_toggled()

    # -- public API ---------------------------------------------------------

    def populate(self, config: Optional[STTConfig]) -> None:
        """Populate widgets from the given config (or defaults)."""
        cfg = config or STTConfig()
        self._enabled_checkbox.setChecked(cfg.enabled)
        if cfg.always_on:
            self._always_on.setChecked(True)
        else:
            self._use_hotkey.setChecked(True)
        self._hotkey_capture.set_hotkey(cfg.hotkey or "")
        # Sub-options default to True on a fresh STTConfig but the
        # legacy config files don't carry them. When the field is
        # missing, fall back to True (the desired default).
        self._type_into_window_checkbox.setChecked(
            getattr(cfg, "type_into_focused_window", True)
        )
        self._voice_triggers_checkbox.setChecked(
            getattr(cfg, "voice_triggers_enabled", True)
        )

        model_idx = self._model_combo.findData(cfg.model)
        if model_idx >= 0:
            self._model_combo.setCurrentIndex(model_idx)
        else:
            self._model_combo.setCurrentIndex(self._model_combo.findData("turbo"))

        lang_idx = self._language_combo.findData(cfg.language)
        if lang_idx >= 0:
            self._language_combo.setCurrentIndex(lang_idx)
        else:
            self._language_combo.setCurrentIndex(self._language_combo.findData("auto"))

        device_idx = self._device_combo.findData(cfg.device)
        if device_idx >= 0:
            self._device_combo.setCurrentIndex(device_idx)
        else:
            self._device_combo.setCurrentIndex(0)

        self._chunk_input.setValue(float(cfg.chunk_seconds))
        sr_idx = self._sample_rate_combo.findData(cfg.sample_rate)
        if sr_idx >= 0:
            self._sample_rate_combo.setCurrentIndex(sr_idx)
        self._silence_input.setValue(float(cfg.silence_rms_threshold))
        self._append_space.setChecked(bool(cfg.append_space))
        self._dedup_input.setValue(int(cfg.dedup_window))

        self._on_enabled_toggled(cfg.enabled)

    def read(self) -> Optional[STTConfig]:
        """Build an STTConfig from the current widget values, or None if disabled."""
        if not self._enabled_checkbox.isChecked():
            return None

        always_on = self._always_on.isChecked()
        hotkey = None
        if not always_on:
            raw = self._hotkey_capture.get_hotkey().strip()
            if raw:
                hotkey = raw

        model = self._model_combo.currentData() or "turbo"
        language = self._language_combo.currentData() or "auto"
        device = self._device_combo.currentData()  # may be None
        return STTConfig(
            enabled=True,
            always_on=always_on,
            hotkey=hotkey,
            language=str(language),
            model=str(model),
            device=device,
            chunk_seconds=float(self._chunk_input.value()),
            sample_rate=int(self._sample_rate_combo.currentData() or 16000),
            append_space=self._append_space.isChecked(),
            silence_rms_threshold=float(self._silence_input.value()),
            dedup_window=int(self._dedup_input.value()),
            type_into_focused_window=self._type_into_window_checkbox.isChecked(),
            voice_triggers_enabled=self._voice_triggers_checkbox.isChecked(),
        )

    def validate(self, stt: Optional[STTConfig]) -> List[str]:
        """Return user-facing validation messages for the STT config."""
        errors: List[str] = []
        if stt is None:
            return errors
        if not stt.always_on and not stt.hotkey:
            errors.append("STT: enable 'Always on' or set a toggle hotkey.")
        if stt.chunk_seconds < 0.5 or stt.chunk_seconds > 30.0:
            errors.append("STT: chunk size must be between 0.5 and 30 seconds.")
        if stt.sample_rate < 8000 or stt.sample_rate > 48000:
            errors.append("STT: sample rate must be between 8000 and 48000 Hz.")
        if stt.silence_rms_threshold < 0:
            errors.append("STT: silence threshold cannot be negative.")
        return errors

    # -- internals ----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        self.setLayout(layout)

        self._enabled_checkbox = QCheckBox("Enable speech-to-text (STT)")
        self._enabled_checkbox.setToolTip(
            "When enabled, the app will listen to your microphone and transcribe "
            "spoken words with Whisper. Use the sub-options below to choose what "
            "happens with the transcribed text."
        )
        self._enabled_checkbox.toggled.connect(self._on_enabled_toggled)
        layout.addWidget(self._enabled_checkbox)

        sub_options = QVBoxLayout()
        sub_options.setContentsMargins(20, 0, 0, 0)
        self._type_into_window_checkbox = QCheckBox(
            "Type dictated text into the focused window"
        )
        self._type_into_window_checkbox.setChecked(True)
        self._type_into_window_checkbox.toggled.connect(self._on_enabled_toggled)
        sub_options.addWidget(self._type_into_window_checkbox)
        self._voice_triggers_checkbox = QCheckBox(
            "Trigger voice shortcuts (sounds, images, videos)"
        )
        self._voice_triggers_checkbox.setChecked(True)
        self._voice_triggers_checkbox.setToolTip(
            "When a transcribed phrase contains a shortcut's 'Trigger Word', "
            "that shortcut fires (its sound and/or overlay play). Independent "
            "from typing — you can keep voice triggers active while disabling "
            "the focused-window typing if you only want reactions on stream."
        )
        self._voice_triggers_checkbox.toggled.connect(self._on_enabled_toggled)
        sub_options.addWidget(self._voice_triggers_checkbox)
        layout.addLayout(sub_options)

        activation_group = QGroupBox("Activation")
        activation_layout = QVBoxLayout()
        activation_group.setLayout(activation_layout)
        self._always_on = QRadioButton(
            "Always on (transcribe whenever the app is running)"
        )
        self._use_hotkey = QRadioButton(
            "Toggle via hotkey (press to start, press again to stop)"
        )
        self._always_on.setChecked(True)
        for rb in (self._always_on, self._use_hotkey):
            rb.toggled.connect(self._on_activation_toggled)
            activation_layout.addWidget(rb)

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("Toggle hotkey:"))
        self._hotkey_capture = HotkeyCapture()
        hotkey_row.addWidget(self._hotkey_capture, 1)
        activation_layout.addLayout(hotkey_row)
        layout.addWidget(activation_group)

        model_group = QGroupBox("Model & Language")
        model_layout = QVBoxLayout()
        model_group.setLayout(model_layout)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Whisper model:"))
        self._model_combo = QComboBox()
        for code, label in WHISPER_MODELS:
            self._model_combo.addItem(label, userData=code)
        model_row.addWidget(self._model_combo, 1)
        model_layout.addLayout(model_row)

        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self._language_combo = QComboBox()
        for code, label in WHISPER_LANGUAGES:
            self._language_combo.addItem(label, userData=code)
        lang_row.addWidget(self._language_combo, 1)
        model_layout.addLayout(lang_row)

        note = QLabel(
            "<i>Larger models are more accurate but slower and use more memory. "
            "The 'turbo' model is recommended for live dictation.</i>"
        )
        note.setWordWrap(True)
        model_layout.addWidget(note)
        layout.addWidget(model_group)

        audio_group = QGroupBox("Audio Capture")
        audio_layout = QVBoxLayout()
        audio_group.setLayout(audio_layout)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("Input device:"))
        self._device_combo = QComboBox()
        self._populate_audio_devices()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._populate_audio_devices)
        device_row.addWidget(self._device_combo, 1)
        device_row.addWidget(self._refresh_btn)
        audio_layout.addLayout(device_row)

        chunk_row = QHBoxLayout()
        chunk_row.addWidget(QLabel("Chunk size (seconds):"))
        self._chunk_input = QDoubleSpinBox()
        self._chunk_input.setRange(0.5, 30.0)
        self._chunk_input.setSingleStep(0.5)
        self._chunk_input.setValue(4.0)
        chunk_row.addWidget(self._chunk_input)
        chunk_row.addSpacing(20)
        chunk_row.addWidget(QLabel("Sample rate (Hz):"))
        self._sample_rate_combo = QComboBox()
        for rate in (8000, 16000, 22050, 32000, 44100, 48000):
            self._sample_rate_combo.addItem(str(rate), userData=rate)
        idx = self._sample_rate_combo.findData(16000)
        if idx >= 0:
            self._sample_rate_combo.setCurrentIndex(idx)
        chunk_row.addWidget(self._sample_rate_combo)
        audio_layout.addLayout(chunk_row)

        silence_row = QHBoxLayout()
        silence_row.addWidget(QLabel("Silence threshold (RMS):"))
        self._silence_input = QDoubleSpinBox()
        self._silence_input.setRange(0.0, 0.5)
        self._silence_input.setSingleStep(0.001)
        self._silence_input.setDecimals(3)
        self._silence_input.setValue(0.005)
        silence_row.addWidget(self._silence_input)
        silence_row.addStretch(1)
        audio_layout.addLayout(silence_row)
        layout.addWidget(audio_group)

        typing_group = QGroupBox("Typing Behavior")
        typing_layout = QVBoxLayout()
        typing_group.setLayout(typing_layout)
        self._append_space = QCheckBox("Append a space after each transcribed phrase")
        self._append_space.setChecked(True)
        typing_layout.addWidget(self._append_space)
        dedup_row = QHBoxLayout()
        dedup_row.addWidget(QLabel("Dedup window (chars):"))
        self._dedup_input = QSpinBox()
        self._dedup_input.setRange(0, 4096)
        self._dedup_input.setValue(64)
        dedup_row.addWidget(self._dedup_input)
        dedup_row.addStretch(1)
        typing_layout.addLayout(dedup_row)
        layout.addWidget(typing_group)
        layout.addStretch(1)

    def _populate_audio_devices(self) -> None:
        """Enumerate input devices via sounddevice and fill the combo box."""
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("System default", userData=None)
        try:
            import sounddevice as sd  # type: ignore[import-not-found]

            devices = sd.query_devices()
            for index, info in enumerate(devices):
                if int(info.get("max_input_channels", 0)) > 0:
                    name = info.get("name", f"Device {index}")
                    self._device_combo.addItem(f"{index}: {name}", userData=index)
        except Exception as exc:  # pragma: no cover - hardware-specific
            _LOGGER.warning("Could not enumerate audio devices: %s", exc)
        self._device_combo.blockSignals(False)

    def _on_enabled_toggled(self, _checked: bool) -> None:
        """Enable/disable the rest of the STT panel based on the master switch."""
        enabled = self._enabled_checkbox.isChecked()
        for w in (
            self._always_on,
            self._use_hotkey,
            self._hotkey_capture,
            self._type_into_window_checkbox,
            self._voice_triggers_checkbox,
            self._model_combo,
            self._language_combo,
            self._device_combo,
            self._refresh_btn,
            self._chunk_input,
            self._sample_rate_combo,
            self._silence_input,
            self._append_space,
            self._dedup_input,
        ):
            w.setEnabled(enabled)
        self._on_activation_toggled()

    def _on_activation_toggled(self) -> None:
        """Enable hotkey capture only when toggle mode is selected."""
        if not hasattr(self, "_use_hotkey"):
            return
        hotkey_enabled = (
            self._enabled_checkbox.isChecked() and self._use_hotkey.isChecked()
        )
        self._hotkey_capture.setEnabled(hotkey_enabled)


# ---------------------------------------------------------------------------
# Shortcut details section
# ---------------------------------------------------------------------------


class ShortcutSection(QWidget):
    """The shortcut details panel (hotkey, voice triggers, sound, overlay, preview).

    Public API:
        ``populate(shortcut)`` — fill widgets from a :class:`Shortcut` (or
            clear them with ``clear()``).
        ``clear()`` — reset all fields to empty/defaults.
        ``read()`` — return a tuple ``(hotkey, suffix, sound_path, overlay,
            trigger_word, trigger_phrases)``. ``hotkey`` and ``suffix`` are
            mutually exclusive based on the trigger radio. Returns ``None``
            for the trigger values if not set.
        ``validate_trigger()`` — return a list of user-facing validation
            errors (e.g. empty hotkey, empty suffix).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._preview_movie: Optional[QMovie] = None
        self._build_ui()
        self.clear()

    # -- public API ---------------------------------------------------------

    def populate(self, shortcut: Shortcut) -> None:
        """Fill widgets from a :class:`Shortcut`."""
        if shortcut.hotkey:
            self._trigger_direct.setChecked(True)
            self._hotkey_capture.set_hotkey(shortcut.hotkey)
            self._suffix_capture.set_key("")
        else:
            self._trigger_suffix.setChecked(True)
            self._hotkey_capture.set_hotkey("")
            self._suffix_capture.set_key(
                " ".join(shortcut.suffix) if shortcut.suffix else ""
            )
        self._trigger_word_input.setText(shortcut.trigger_word or "")
        self._trigger_phrases_input.setPlainText(
            "\n".join(shortcut.trigger_phrases or ())
        )
        self._sound_input.setText(shortcut.sound_path or "")

        if shortcut.overlay:
            self._overlay_input.setText(shortcut.overlay.file)
            self._x_input.setValue(shortcut.overlay.x)
            self._y_input.setValue(shortcut.overlay.y)
            self._duration_input.setValue(shortcut.overlay.duration_ms)
            if (
                shortcut.overlay.width is not None
                and shortcut.overlay.height is not None
            ):
                self._custom_size_checkbox.setChecked(True)
                self._width_input.setValue(shortcut.overlay.width)
                self._height_input.setValue(shortcut.overlay.height)
            else:
                self._custom_size_checkbox.setChecked(False)
        else:
            self._overlay_input.setText("")
            self._x_input.setValue(0)
            self._y_input.setValue(0)
            self._duration_input.setValue(1500)
            self._custom_size_checkbox.setChecked(False)

    def clear(self) -> None:
        """Reset all fields to empty/defaults."""
        self._trigger_direct.setChecked(True)
        self._hotkey_capture.set_hotkey("")
        self._suffix_capture.set_key("")
        self._trigger_word_input.setText("")
        self._trigger_phrases_input.setPlainText("")
        self._sound_input.setText("")
        self._overlay_input.setText("")
        self._cleanup_preview_movie()
        self._overlay_preview.clear()
        self._overlay_preview.setText("No preview")
        self._x_input.setValue(0)
        self._y_input.setValue(0)
        self._duration_input.setValue(1500)
        self._custom_size_checkbox.setChecked(False)
        self._width_input.setValue(100)
        self._height_input.setValue(100)

    def cleanup_preview(self) -> None:
        """Release preview-movie resources. Call from window closeEvent."""
        self._cleanup_preview_movie()

    # -- internals ----------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        self.setLayout(layout)
        layout.addWidget(QLabel("<b>Shortcut Details</b>"))

        # Trigger type
        trigger_layout = QHBoxLayout()
        trigger_layout.addWidget(QLabel("Trigger:"))
        self._trigger_direct = QRadioButton("Direct hotkey")
        self._trigger_suffix = QRadioButton("Chord suffix")
        self._trigger_direct.setChecked(True)
        trigger_layout.addWidget(self._trigger_direct)
        trigger_layout.addWidget(self._trigger_suffix)
        layout.addLayout(trigger_layout)

        # Hotkey / Suffix capture widgets
        layout.addWidget(QLabel("Hotkey or Suffix:"))
        self._hotkey_capture = HotkeyCapture()
        self._suffix_capture = SingleKeyCapture()
        layout.addWidget(self._hotkey_capture)
        layout.addWidget(self._suffix_capture)
        self._suffix_capture.hide()

        # Voice triggers: a single word (legacy) and an optional list
        # of multi-word phrases. Both can be configured for the same
        # shortcut. We keep the single-word input as a QLineEdit for
        # the simplest case, and add a multi-line text box for
        # phrases (one per line).
        layout.addWidget(QLabel("Voice Trigger Word (optional):"))
        self._trigger_word_input = QLineEdit()
        self._trigger_word_input.setPlaceholderText(
            "Single word that, when spoken, fires this shortcut (e.g. 'fail')"
        )
        self._trigger_word_input.setMaxLength(64)
        layout.addWidget(self._trigger_word_input)

        layout.addWidget(QLabel("Voice Trigger Phrases (optional, one per line):"))
        self._trigger_phrases_input = QPlainTextEdit()
        self._trigger_phrases_input.setPlaceholderText(
            "Multi-word phrases. Each line is one phrase.\n"
            "e.g. 'play fail' or 'react with fire'.\n"
            "Matching requires the words to appear in order, contiguously, "
            "in the transcribed text."
        )
        self._trigger_phrases_input.setMaximumHeight(80)
        self._trigger_phrases_input.setTabChangesFocus(True)
        layout.addWidget(self._trigger_phrases_input)

        self._trigger_word_note = QLabel(
            "<i>Leave both empty to disable voice triggering. Match is "
            "case-insensitive and word-boundary (so 'fail' does not match "
            "'failful'). Phrases must be contiguous in the spoken text. "
            "The same trigger on two shortcuts will only fire the first.</i>"
        )
        self._trigger_word_note.setWordWrap(True)
        layout.addWidget(self._trigger_word_note)

        def _on_trigger_changed():
            suffix_mode = self._trigger_suffix.isChecked()
            self._hotkey_capture.setVisible(not suffix_mode)
            self._suffix_capture.setVisible(suffix_mode)

        self._trigger_direct.toggled.connect(_on_trigger_changed)
        self._trigger_suffix.toggled.connect(_on_trigger_changed)

        # Sound
        layout.addWidget(QLabel("Sound File:"))
        sound_layout = QHBoxLayout()
        self._sound_input = QLineEdit()
        self._sound_input.setPlaceholderText("Path to audio file (optional)")
        self._sound_browse_btn = QPushButton("Browse...")
        self._sound_browse_btn.clicked.connect(self._browse_sound)
        sound_layout.addWidget(self._sound_input)
        sound_layout.addWidget(self._sound_browse_btn)
        layout.addLayout(sound_layout)

        # Overlay
        layout.addWidget(QLabel("Overlay File:"))
        overlay_layout = QHBoxLayout()
        self._overlay_input = QLineEdit()
        self._overlay_input.setPlaceholderText(
            "Path to overlay image/gif/video (optional)"
        )
        self._overlay_input.textChanged.connect(self._on_overlay_changed)
        self._overlay_browse_btn = QPushButton("Browse...")
        self._overlay_browse_btn.clicked.connect(self._browse_overlay)
        overlay_layout.addWidget(self._overlay_input)
        overlay_layout.addWidget(self._overlay_browse_btn)
        layout.addLayout(overlay_layout)

        # Overlay preview
        self._overlay_preview = QLabel()
        self._overlay_preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self._overlay_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._overlay_preview.setStyleSheet(
            "QLabel { border: 1px solid #ccc; background-color: #f0f0f0; }"
        )
        self._overlay_preview.setText("No preview")
        layout.addWidget(self._overlay_preview)

        # Overlay size
        layout.addWidget(QLabel("Overlay Size (optional):"))
        size_layout = QHBoxLayout()
        self._custom_size_checkbox = QCheckBox("Custom size")
        self._custom_size_checkbox.stateChanged.connect(self._on_custom_size_toggled)
        size_layout.addWidget(self._custom_size_checkbox)
        size_layout.addWidget(QLabel("Width:"))
        self._width_input = QSpinBox()
        self._width_input.setRange(MIN_OVERLAY_SIZE, MAX_OVERLAY_SIZE)
        self._width_input.setValue(100)
        self._width_input.setEnabled(False)
        size_layout.addWidget(self._width_input)
        size_layout.addWidget(QLabel("Height:"))
        self._height_input = QSpinBox()
        self._height_input.setRange(MIN_OVERLAY_SIZE, MAX_OVERLAY_SIZE)
        self._height_input.setValue(100)
        self._height_input.setEnabled(False)
        size_layout.addWidget(self._height_input)
        layout.addLayout(size_layout)

        # Overlay position
        layout.addWidget(QLabel("Overlay Position:"))
        position_layout = QHBoxLayout()
        position_layout.addWidget(QLabel("X:"))
        self._x_input = QSpinBox()
        self._x_input.setRange(0, MAX_OVERLAY_SIZE)
        position_layout.addWidget(self._x_input)
        position_layout.addWidget(QLabel("Y:"))
        self._y_input = QSpinBox()
        self._y_input.setRange(0, MAX_OVERLAY_SIZE)
        position_layout.addWidget(self._y_input)
        self._pick_position_btn = QPushButton("Pick Position...")
        self._pick_position_btn.clicked.connect(self._pick_position)
        position_layout.addWidget(self._pick_position_btn)
        layout.addLayout(position_layout)

        # Duration
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("Duration (ms):"))
        self._duration_input = QSpinBox()
        self._duration_input.setRange(0, MAX_DURATION_MS)
        self._duration_input.setValue(1500)
        duration_layout.addWidget(self._duration_input)
        layout.addLayout(duration_layout)

        layout.addStretch()

    def _browse_sound(self) -> None:
        """Open file dialog to select sound file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Sound File",
            "",
            "Audio Files (*.wav *.mp3);;All Files (*)",
        )
        if file_path:
            self._sound_input.setText(file_path)

    def _browse_overlay(self) -> None:
        """Open file dialog to select overlay file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Overlay File",
            "",
            "Overlay Files (*.png *.gif *.jpg *.jpeg *.mp4 *.mov *.avi *.mkv *.webm *.m4v);;All Files (*)",
        )
        if file_path:
            self._overlay_input.setText(file_path)

    def _pick_position(self) -> None:
        """Open position picker to select overlay position with mouse."""
        picker = PositionPicker(self)
        picker.position_picked.connect(self._on_position_picked)
        picker.show()

    def _on_position_picked(self, x: int, y: int) -> None:
        """Handle position picked from the position picker."""
        self._x_input.setValue(x)
        self._y_input.setValue(y)

    def _cleanup_preview_movie(self) -> None:
        """Clean up the preview movie to prevent memory leaks."""
        if self._preview_movie is not None:
            self._preview_movie.stop()
            self._preview_movie.deleteLater()
            self._preview_movie = None

    def _on_overlay_changed(self, path: str) -> None:
        """Update overlay preview when path changes."""
        self._cleanup_preview_movie()

        if not path or not Path(path).exists():
            self._overlay_preview.clear()
            self._overlay_preview.setText("No preview")
            return

        path_obj = Path(path)
        try:
            suffix = path_obj.suffix.lower()
            if suffix == ".gif":
                movie = QMovie(str(path_obj))
                if not movie.isValid():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid GIF")
                    _LOGGER.warning("Failed to load GIF preview: %s", path_obj)
                    return

                movie.jumpToFrame(0)
                pixmap = movie.currentPixmap()
                if pixmap.isNull():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid GIF")
                    return

                self._preview_movie = movie

                scaled = pixmap.scaled(
                    PREVIEW_WIDTH,
                    PREVIEW_HEIGHT,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._overlay_preview.setPixmap(scaled)
                if not self._custom_size_checkbox.isChecked():
                    self._width_input.setValue(pixmap.width())
                    self._height_input.setValue(pixmap.height())
            elif suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}:
                self._overlay_preview.clear()
                self._overlay_preview.setText("Video file selected (no preview)")
            else:
                pixmap = QPixmap(str(path_obj))
                if pixmap.isNull():
                    self._overlay_preview.clear()
                    self._overlay_preview.setText("Invalid image")
                    _LOGGER.warning("Failed to load image preview: %s", path_obj)
                    return

                scaled = pixmap.scaled(
                    PREVIEW_WIDTH,
                    PREVIEW_HEIGHT,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._overlay_preview.setPixmap(scaled)
                if not self._custom_size_checkbox.isChecked():
                    self._width_input.setValue(pixmap.width())
                    self._height_input.setValue(pixmap.height())
        except Exception as exc:
            self._overlay_preview.clear()
            self._overlay_preview.setText("Error loading preview")
            _LOGGER.error("Error loading overlay preview for %s: %s", path_obj, exc)

    def _on_custom_size_toggled(self, state: int) -> None:
        """Handle custom size checkbox toggle."""
        enabled = state == Qt.CheckState.Checked.value
        self._width_input.setEnabled(enabled)
        self._height_input.setEnabled(enabled)
