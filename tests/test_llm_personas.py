"""Tests for the persona presets and the LLMConfig resolution helpers."""

from __future__ import annotations

import pytest

from stream_companion.llm.personas import (
    PERSONA_PRESETS,
    resolve_system_prompt,
)
from stream_companion.models import LLMConfig, Shortcut


def test_persona_presets_has_expected_keys() -> None:
    assert set(PERSONA_PRESETS) == {
        "fact_checker",
        "eli5",
        "socratic",
        "devils_advocate",
        "custom",
    }


def test_custom_persona_is_empty_sentinel() -> None:
    # The "custom" persona must not carry a system prompt; the user
    # supplies one via LLMConfig.system_prompt.
    assert PERSONA_PRESETS["custom"] == ""


@pytest.mark.parametrize(
    "persona,key_phrase",
    [
        ("fact_checker", "VERDICT"),
        ("eli5", "like they are five"),
        ("socratic", "probing question"),
        ("devils_advocate", "counter-argument"),
    ],
)
def test_resolve_known_persona(persona: str, key_phrase: str) -> None:
    assert resolve_system_prompt(persona, None) == PERSONA_PRESETS[persona]
    assert key_phrase in resolve_system_prompt(persona, None)


def test_custom_prompt_overrides_persona() -> None:
    assert resolve_system_prompt("fact_checker", "be terse") == "be terse"


def test_unknown_persona_falls_back_to_fact_checker() -> None:
    assert (
        resolve_system_prompt("not_a_real_persona", None)
        == PERSONA_PRESETS["fact_checker"]
    )


def test_empty_custom_does_not_override() -> None:
    # An empty custom prompt must NOT clobber the persona; otherwise
    # a blank UI field would silently switch to no-prompt mode.
    assert resolve_system_prompt("eli5", "   ") == PERSONA_PRESETS["eli5"]


def test_llm_config_defaults() -> None:
    cfg = LLMConfig()
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_key_env == "LLM_API_KEY"
    assert cfg.persona == "fact_checker"
    assert cfg.system_prompt is None
    assert cfg.temperature == 0.3
    assert cfg.max_tokens == 512
    assert cfg.toggle_hotkey is None
    assert cfg.timeout_seconds == 30


def test_llm_config_resolved_system_prompt_default() -> None:
    cfg = LLMConfig()
    assert cfg.resolved_system_prompt() == PERSONA_PRESETS["fact_checker"]


def test_llm_config_resolved_system_prompt_custom() -> None:
    cfg = LLMConfig(persona="custom", system_prompt="be brief")
    assert cfg.resolved_system_prompt() == "be brief"


def test_llm_config_api_key_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test-123")
    assert LLMConfig().api_key() == "sk-test-123"


def test_llm_config_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert LLMConfig().api_key() is None


def test_llm_config_api_key_custom_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PROVIDER_KEY", "abc")
    cfg = LLMConfig(api_key_env="MY_PROVIDER_KEY")
    assert cfg.api_key() == "abc"
    assert cfg.is_valid_api_key_env()


@pytest.mark.parametrize(
    "bad_name",
    ["lowercase", "1LEADING_DIGIT", "has-dash", "", "WITH SPACE"],
)
def test_llm_config_invalid_api_key_env_name(bad_name: str) -> None:
    cfg = LLMConfig(api_key_env=bad_name)
    assert not cfg.is_valid_api_key_env()


def test_shortcut_fact_check_default_false() -> None:
    assert Shortcut(hotkey="<ctrl>+1").fact_check is False


def test_shortcut_fact_check_can_be_enabled() -> None:
    s = Shortcut(hotkey="<ctrl>+q", fact_check=True)
    assert s.fact_check is True


def test_all_fact_check_shortcuts_filters() -> None:
    shortcuts = [
        Shortcut(hotkey="<ctrl>+1"),
        Shortcut(hotkey="<ctrl>+2", fact_check=True),
        Shortcut(hotkey="<ctrl>+3"),
        Shortcut(hotkey="<ctrl>+4", fact_check=True),
    ]
    flagged = Shortcut.all_fact_check_shortcuts(shortcuts)
    assert [s.hotkey for s in flagged] == ["<ctrl>+2", "<ctrl>+4"]
