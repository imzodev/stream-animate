"""AI Assistant / LLM section for the configurator.

Mirrors the structure of :class:`STTSection`: a self-contained
:class:`QWidget` with ``populate``, ``read``, and ``validate`` methods.
The window composes the section like any other tab.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..llm.config import LLMConfig
from ..llm.personas import PERSONA_PRESETS
from .widgets import HotkeyCapture

_LOGGER = logging.getLogger(__name__)


class LLMSection(QWidget):
    """The AI Assistant settings tab.

    Public API:
        ``populate(config)`` — fill widgets from an :class:`LLMConfig`.
        ``read()`` — build an :class:`LLMConfig`.
        ``validate(config)`` — return user-facing validation errors.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build_ui()
        # Initial state: refresh the API-key indicator with the current env.
        self._refresh_api_key_status()
        self._on_persona_changed(self._persona_combo.currentText())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def populate(self, config: Optional[LLMConfig]) -> None:
        """Populate widgets from the given config (or defaults)."""
        cfg = config or LLMConfig()
        self._base_url_input.setText(cfg.base_url)
        self._model_input.setText(cfg.model)
        self._api_key_env_input.setText(cfg.api_key_env)
        idx = self._persona_combo.findData(cfg.persona)
        if idx >= 0:
            self._persona_combo.setCurrentIndex(idx)
        else:
            self._persona_combo.setCurrentIndex(0)
        self._system_prompt_input.setPlainText(cfg.system_prompt or "")
        self._temperature_input.setValue(float(cfg.temperature))
        self._max_tokens_input.setValue(int(cfg.max_tokens))
        self._timeout_input.setValue(int(cfg.timeout_seconds))
        self._toggle_hotkey_capture.set_hotkey(cfg.toggle_hotkey or "")
        self._refresh_api_key_status()
        self._on_persona_changed(self._persona_combo.currentText())

    def read(self) -> LLMConfig:
        """Build an LLMConfig from the current widget values."""
        persona = str(self._persona_combo.currentData() or "fact_checker")
        custom = self._system_prompt_input.toPlainText().strip()
        # The system_prompt field is only meaningful when persona is
        # "custom"; otherwise we leave it None so the persona preset
        # wins.
        system_prompt = custom if persona == "custom" else None
        return LLMConfig(
            base_url=self._base_url_input.text().strip() or "https://api.openai.com/v1",
            model=self._model_input.text().strip() or "gpt-4o-mini",
            api_key_env=self._api_key_env_input.text().strip() or "LLM_API_KEY",
            persona=persona,
            system_prompt=system_prompt,
            temperature=float(self._temperature_input.value()),
            max_tokens=int(self._max_tokens_input.value()),
            toggle_hotkey=self._toggle_hotkey_capture.get_hotkey().strip() or None,
            timeout_seconds=int(self._timeout_input.value()),
        )

    def validate(self, config: LLMConfig) -> List[str]:
        """Return user-facing validation errors for the LLM config."""
        errors: List[str] = []
        if not config.base_url.startswith(("http://", "https://")):
            errors.append("LLM: base_url must start with http:// or https://")
        if "/v1" not in config.base_url:
            errors.append(
                "LLM: base_url must include '/v1' (e.g. https://api.openai.com/v1)"
            )
        if not config.model:
            errors.append("LLM: model is required")
        if not config.is_valid_api_key_env():
            errors.append(
                "LLM: api_key_env must be a valid environment variable name "
                "(uppercase letters, digits, and underscores; must start with a letter)"
            )
        if config.temperature < 0.0 or config.temperature > 2.0:
            errors.append("LLM: temperature must be between 0.0 and 2.0")
        if config.max_tokens < 1 or config.max_tokens > 4096:
            errors.append("LLM: max_tokens must be between 1 and 4096")
        if config.timeout_seconds < 5 or config.timeout_seconds > 300:
            errors.append("LLM: timeout_seconds must be between 5 and 300")
        if config.persona == "custom" and not (
            config.system_prompt and config.system_prompt.strip()
        ):
            errors.append(
                "LLM: persona is 'custom' but system_prompt is empty. "
                "Provide a system prompt or pick a preset."
            )
        return errors

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        self.setLayout(layout)

        # ---- Connection ------------------------------------------------
        conn = QGroupBox("Connection")
        conn_layout = QVBoxLayout()
        conn.setLayout(conn_layout)
        row = QHBoxLayout()
        row.addWidget(QLabel("Base URL:"))
        self._base_url_input = QLineEdit()
        self._base_url_input.setPlaceholderText("https://api.openai.com/v1")
        row.addWidget(self._base_url_input, 1)
        conn_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Model:"))
        self._model_input = QLineEdit()
        self._model_input.setPlaceholderText("gpt-4o-mini")
        row.addWidget(self._model_input, 1)
        conn_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("API key env var:"))
        self._api_key_env_input = QLineEdit()
        self._api_key_env_input.setPlaceholderText("LLM_API_KEY")
        self._api_key_env_input.setMaxLength(64)
        self._api_key_env_input.textChanged.connect(self._refresh_api_key_status)
        row.addWidget(self._api_key_env_input, 1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_api_key_status)
        row.addWidget(self._refresh_btn)
        conn_layout.addLayout(row)
        self._api_key_status = QLabel("")
        conn_layout.addWidget(self._api_key_status)
        note = QLabel(
            "<i>The API key is read from the named environment variable at "
            "runtime. It is never stored in the config file. Set the "
            "variable in your shell (e.g. <code>export LLM_API_KEY=sk-…</code>) "
            "before launching the app.</i>"
        )
        note.setWordWrap(True)
        conn_layout.addWidget(note)
        layout.addWidget(conn)

        # ---- Persona ---------------------------------------------------
        persona_group = QGroupBox("Persona")
        persona_layout = QVBoxLayout()
        persona_group.setLayout(persona_layout)
        row = QHBoxLayout()
        row.addWidget(QLabel("Persona:"))
        self._persona_combo = QComboBox()
        for name in PERSONA_PRESETS:
            label = {
                "fact_checker": "Fact-checker (default)",
                "eli5": "Explain like I'm 5",
                "socratic": "Socratic tutor",
                "devils_advocate": "Devil's advocate",
                "custom": "Custom…",
            }.get(name, name)
            self._persona_combo.addItem(label, userData=name)
        self._persona_combo.currentTextChanged.connect(self._on_persona_changed)
        row.addWidget(self._persona_combo, 1)
        persona_layout.addLayout(row)
        persona_layout.addWidget(
            QLabel("System prompt (only used when persona is Custom):")
        )
        self._system_prompt_input = QPlainTextEdit()
        self._system_prompt_input.setPlaceholderText(
            "You are a …\n\nOnly used when persona is 'Custom'."
        )
        self._system_prompt_input.setMaximumHeight(100)
        self._system_prompt_input.setTabChangesFocus(True)
        persona_layout.addWidget(self._system_prompt_input)
        layout.addWidget(persona_group)

        # ---- Behaviour -------------------------------------------------
        behavior = QGroupBox("Behaviour")
        behavior_layout = QVBoxLayout()
        behavior.setLayout(behavior_layout)
        row = QHBoxLayout()
        row.addWidget(QLabel("Temperature:"))
        self._temperature_input = QDoubleSpinBox()
        self._temperature_input.setRange(0.0, 2.0)
        self._temperature_input.setSingleStep(0.05)
        self._temperature_input.setDecimals(2)
        self._temperature_input.setValue(0.3)
        row.addWidget(self._temperature_input)
        row.addSpacing(20)
        row.addWidget(QLabel("Max tokens:"))
        self._max_tokens_input = QSpinBox()
        self._max_tokens_input.setRange(1, 4096)
        self._max_tokens_input.setValue(512)
        row.addWidget(self._max_tokens_input)
        row.addSpacing(20)
        row.addWidget(QLabel("Timeout (s):"))
        self._timeout_input = QSpinBox()
        self._timeout_input.setRange(5, 300)
        self._timeout_input.setValue(30)
        row.addWidget(self._timeout_input)
        behavior_layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Toggle hotkey:"))
        self._toggle_hotkey_capture = HotkeyCapture()
        row.addWidget(self._toggle_hotkey_capture, 1)
        behavior_layout.addLayout(row)
        layout.addWidget(behavior)

        layout.addStretch(1)

    def _refresh_api_key_status(self) -> None:
        name = self._api_key_env_input.text().strip() or "LLM_API_KEY"
        if (
            not name
            or not name[0].isalpha()
            or not name[0].isupper()
            or not all(c.isalnum() or c == "_" for c in name)
        ):
            self._api_key_status.setText("API key env var: <i>invalid name</i>")
            self._api_key_status.setStyleSheet("color: #c0392b;")
            return
        if os.environ.get(name):
            self._api_key_status.setText(
                f"API key: ✓ loaded from environment variable {name!r}"
            )
            self._api_key_status.setStyleSheet("color: #27ae60;")
        else:
            self._api_key_status.setText(
                f"API key: ✗ environment variable {name!r} is not set. "
                "The fact-checker will refuse to start until it is."
            )
            self._api_key_status.setStyleSheet("color: #c0392b;")

    def _on_persona_changed(self, _label: str) -> None:
        persona = str(self._persona_combo.currentData() or "fact_checker")
        is_custom = persona == "custom"
        self._system_prompt_input.setEnabled(is_custom)
        # When switching away from "custom", leave the text in place so
        # the user can switch back without losing their work.
