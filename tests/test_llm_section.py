"""Tests for the configurator's LLM section."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from stream_companion.configurator.llm_section import LLMSection
from stream_companion.llm.config import LLMConfig


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def section(qapp: QApplication) -> LLMSection:
    return LLMSection()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_with_defaults(section: LLMSection) -> None:
    section.populate(LLMConfig())
    cfg = section.read()
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_key_env == "LLM_API_KEY"
    assert cfg.persona == "fact_checker"
    assert cfg.system_prompt is None
    assert cfg.temperature == 0.3
    assert cfg.max_tokens == 512
    assert cfg.timeout_seconds == 30
    assert cfg.toggle_hotkey is None


def test_round_trip_preserves_all_fields(section: LLMSection) -> None:
    cfg_in = LLMConfig(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key_env="DEEPSEEK_KEY",
        persona="eli5",
        system_prompt=None,
        temperature=0.7,
        max_tokens=1024,
        toggle_hotkey="<ctrl>+<alt>+q",
        timeout_seconds=45,
    )
    section.populate(cfg_in)
    cfg_out = section.read()
    assert cfg_out == cfg_in


def test_round_trip_with_custom_persona(section: LLMSection) -> None:
    section.populate(LLMConfig(persona="custom", system_prompt="be terse"))
    cfg = section.read()
    assert cfg.persona == "custom"
    assert cfg.system_prompt == "be terse"


def test_read_clears_system_prompt_for_non_custom(section: LLMSection) -> None:
    """When persona is not 'custom', the system_prompt field is dropped."""
    section._persona_combo.setCurrentIndex(0)  # fact_checker
    section._system_prompt_input.setPlainText("leaked prompt")
    cfg = section.read()
    assert cfg.persona == "fact_checker"
    assert cfg.system_prompt is None


# ---------------------------------------------------------------------------
# API key status indicator
# ---------------------------------------------------------------------------


def test_api_key_status_loaded(
    section: LLMSection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_LLM_KEY", "sk-test")
    section._api_key_env_input.setText("MY_LLM_KEY")
    section._refresh_api_key_status()
    assert "loaded" in section._api_key_status.text()


def test_api_key_status_missing(
    section: LLMSection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MY_LLM_KEY", raising=False)
    section._api_key_env_input.setText("MY_LLM_KEY")
    section._refresh_api_key_status()
    assert "not set" in section._api_key_status.text()


def test_api_key_status_invalid_name(section: LLMSection) -> None:
    section._api_key_env_input.setText("1bad")
    section._refresh_api_key_status()
    assert "invalid" in section._api_key_status.text()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_rejects_bad_base_url(section: LLMSection) -> None:
    section.populate(LLMConfig(base_url="not-a-url"))
    cfg = section.read()
    errors = section.validate(cfg)
    assert any("base_url" in e for e in errors)


def test_validate_rejects_missing_v1(section: LLMSection) -> None:
    section.populate(LLMConfig(base_url="https://api.example.com"))
    cfg = section.read()
    errors = section.validate(cfg)
    assert any("/v1" in e for e in errors)


def test_validate_rejects_invalid_env_name(section: LLMSection) -> None:
    section.populate(LLMConfig(api_key_env="lowercase"))
    cfg = section.read()
    errors = section.validate(cfg)
    assert any("environment variable name" in e for e in errors)


def test_validate_rejects_out_of_range_temperature(section: LLMSection) -> None:
    # Construct a config that bypasses the QDoubleSpinBox clamp so we
    # can exercise the validation path.
    bad = LLMConfig(temperature=5.0)
    errors = section.validate(bad)
    assert any("temperature" in e for e in errors)


def test_validate_rejects_out_of_range_max_tokens(section: LLMSection) -> None:
    bad = LLMConfig(max_tokens=40000)
    errors = section.validate(bad)
    assert any("max_tokens" in e for e in errors)


def test_validate_rejects_custom_without_prompt(section: LLMSection) -> None:
    section.populate(LLMConfig(persona="custom", system_prompt=None))
    cfg = section.read()
    errors = section.validate(cfg)
    assert any("system_prompt" in e for e in errors)


def test_validate_passes_valid_config(section: LLMSection) -> None:
    section.populate(LLMConfig())
    cfg = section.read()
    assert section.validate(cfg) == []


# ---------------------------------------------------------------------------
# Persona interaction
# ---------------------------------------------------------------------------


def test_system_prompt_enabled_only_for_custom(section: LLMSection) -> None:
    section._persona_combo.setCurrentIndex(0)  # fact_checker
    assert not section._system_prompt_input.isEnabled()
    # Switch to "custom" (last item).
    for i in range(section._persona_combo.count()):
        if section._persona_combo.itemData(i) == "custom":
            section._persona_combo.setCurrentIndex(i)
            break
    assert section._system_prompt_input.isEnabled()


# ---------------------------------------------------------------------------
# Test Connection button
# ---------------------------------------------------------------------------


class _FakeStreamingClient:
    """Stand-in for ``FactCheckerClient`` that yields a fixed sequence."""

    def __init__(self, tokens=("ok",), raise_after=None):
        self.tokens = list(tokens)
        self.raise_after = raise_after
        self.config = None
        self.closed = False

    def stream(self, user_text):
        if self.raise_after is not None:
            raise self.raise_after
        for t in self.tokens:
            yield t

    def close(self):
        self.closed = True


def test_test_connection_reports_validation_errors(qapp, section, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    section.populate(LLMConfig(base_url="bad-url"))
    section._on_test_clicked()
    assert "highlighted issues" in section._test_status.text()


def test_test_connection_reports_missing_api_key(qapp, section, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    section.populate(LLMConfig(api_key_env="MISSING_KEY"))
    section._on_test_clicked()
    assert "API key not found" in section._test_status.text()


def test_test_connection_success(qapp, section, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    section.populate(LLMConfig(model="kimi-k2.7-code"))

    fake = _FakeStreamingClient(tokens=("hello",))
    import stream_companion.configurator.llm_section as ls

    monkeypatch.setattr(ls, "FactCheckerClient", lambda cfg: fake)
    section._on_test_clicked()
    assert "Connected" in section._test_status.text()
    assert "kimi-k2.7-code" in section._test_status.text()
    assert fake.closed is True


def test_test_connection_opencode_model_not_supported(qapp, section, monkeypatch):
    """A 401 with ``ModelError`` body must show the model-specific hint."""
    from stream_companion.llm.client import LLMError

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    section.populate(LLMConfig(model="deepseek-v4-flash"))
    fake = _FakeStreamingClient(
        raise_after=LLMError(
            "http 401",
            status=401,
            body='{"type":"error","error":{"type":"ModelError","message":"Model X is not supported"}}',
        )
    )
    import stream_companion.configurator.llm_section as ls

    monkeypatch.setattr(ls, "FactCheckerClient", lambda cfg: fake)
    section._on_test_clicked()
    text = section._test_status.text().lower()
    assert "not available" in text
    assert "deepseek-v4-flash" in section._test_status.text()
    assert "auth" not in text


def test_test_connection_404(qapp, section, monkeypatch):
    from stream_companion.llm.client import LLMError

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    section.populate(LLMConfig())
    fake = _FakeStreamingClient(
        raise_after=LLMError("http 404", status=404, body="<html>nope</html>")
    )
    import stream_companion.configurator.llm_section as ls

    monkeypatch.setattr(ls, "FactCheckerClient", lambda cfg: fake)
    section._on_test_clicked()
    text = section._test_status.text()
    assert "404" in text
    assert "<html>" not in text
