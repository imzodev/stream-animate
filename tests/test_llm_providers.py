"""Tests for the provider adapter abstraction."""

from __future__ import annotations

import pytest

from stream_companion.llm.config import LLMConfig
from stream_companion.llm.providers import (
    AdapterFactory,
    ADAPTERS,
    ProviderAdapter,
    StreamChunk,
)
from stream_companion.llm.providers.adapters.anthropic import AnthropicAdapter
from stream_companion.llm.providers.adapters.deepseek import DeepSeekAdapter
from stream_companion.llm.providers.adapters.openai_generic import (
    OpenAIGenericAdapter,
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected_cls",
    [
        ("deepseek-v4-flash", DeepSeekAdapter),
        ("deepseek-chat", DeepSeekAdapter),
        ("DEEPSEEK-reasoner", DeepSeekAdapter),
        ("gpt-4o", OpenAIGenericAdapter),
        ("llama-3.1-70b", OpenAIGenericAdapter),
        ("qwen2.5-72b", OpenAIGenericAdapter),
    ],
)
def test_factory_picks_adapter_by_model_name(model: str, expected_cls: type) -> None:
    cfg = LLMConfig(model=model)
    adapter = AdapterFactory.create(cfg)
    assert isinstance(adapter, expected_cls)


@pytest.mark.parametrize(
    "base_url,expected_cls",
    [
        ("https://api.anthropic.com/v1/messages", AnthropicAdapter),
        ("https://api.anthropic.com", AnthropicAdapter),
        ("https://api.openai.com/v1", OpenAIGenericAdapter),
        ("http://localhost:11434/v1", OpenAIGenericAdapter),
        (
            "https://opencode.ai/zen/go/v1/chat/completions",
            DeepSeekAdapter,  # model name matches
        ),
    ],
)
def test_factory_picks_adapter_by_url(base_url: str, expected_cls: type) -> None:
    cfg = LLMConfig(base_url=base_url, model="x")
    # Adjust model to avoid a DeepSeek collision on the opencode URL.
    if "opencode" in base_url:
        cfg = LLMConfig(base_url=base_url, model="deepseek-v4-flash")
    elif "anthropic" in base_url:
        cfg = LLMConfig(base_url=base_url, model="claude-3-5-sonnet")
    else:
        cfg = LLMConfig(base_url=base_url, model="gpt-4o")
    adapter = AdapterFactory.create(cfg)
    assert isinstance(adapter, expected_cls)


def test_factory_adapters_list_ends_with_generic() -> None:
    """The OpenAI generic adapter must be last so specific adapters win."""
    assert ADAPTERS[-1] is OpenAIGenericAdapter


# ---------------------------------------------------------------------------
# OpenAI generic
# ---------------------------------------------------------------------------


def test_openai_generic_content_delta() -> None:
    adapter = OpenAIGenericAdapter()
    raw = {"choices": [{"index": 0, "delta": {"content": "hello"}}]}
    chunk = adapter.parse_chunk(raw)
    assert chunk.content == "hello"
    assert chunk.reasoning == ""
    assert chunk.is_final is False


def test_openai_generic_reasoning_delta() -> None:
    adapter = OpenAIGenericAdapter()
    raw = {"choices": [{"index": 0, "delta": {"reasoning_content": "thinking"}}]}
    chunk = adapter.parse_chunk(raw)
    assert chunk.reasoning == "thinking"
    assert chunk.content == ""


def test_openai_generic_finish_reason_marks_final() -> None:
    adapter = OpenAIGenericAdapter()
    raw = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    chunk = adapter.parse_chunk(raw)
    assert chunk.is_final is True
    assert chunk.finish_reason == "stop"


def test_openai_generic_legacy_ollama_message_chunk() -> None:
    adapter = OpenAIGenericAdapter()
    raw = {"choices": [{"index": 0, "delta": {}, "message": {"content": "ollama"}}]}
    chunk = adapter.parse_chunk(raw)
    assert chunk.content == "ollama"


def test_openai_generic_role_delta_captured() -> None:
    adapter = OpenAIGenericAdapter()
    raw = {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
    chunk = adapter.parse_chunk(raw)
    assert chunk.role_delta == "assistant"


def test_openai_generic_empty_chunk() -> None:
    adapter = OpenAIGenericAdapter()
    chunk = adapter.parse_chunk({})
    assert chunk == StreamChunk()


def test_openai_generic_skips_anthropic_url() -> None:
    """The generic must defer to AnthropicAdapter when the URL screams
    Anthropic, even though it would match many OpenAI-shaped responses."""
    assert not OpenAIGenericAdapter().matches(
        LLMConfig(base_url="https://api.anthropic.com/v1/messages")
    )


# ---------------------------------------------------------------------------
# DeepSeek
# ---------------------------------------------------------------------------


def test_deepseek_adapter_inherits_openai_parsing() -> None:
    adapter = DeepSeekAdapter()
    raw = {"choices": [{"index": 0, "delta": {"content": "x"}}]}
    assert adapter.parse_chunk(raw).content == "x"
    assert adapter.parse_chunk(raw).reasoning == ""


def test_deepseek_matches_model_name_case_insensitive() -> None:
    cfg = LLMConfig(model="DeepSeek-V4-Flash")
    assert DeepSeekAdapter().matches(cfg)


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_content,expected_reasoning,expected_final",
    [
        # Text delta.
        (
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "hello"},
            },
            "hello",
            "",
            False,
        ),
        # Thinking delta.
        (
            {
                "type": "content_block_delta",
                "delta": {"type": "thinking_delta", "thinking": "let me think"},
            },
            "",
            "let me think",
            False,
        ),
        # Message delta with stop_reason = end-of-stream.
        (
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            "",
            "",
            True,
        ),
        # Message stop = hard terminator.
        (
            {"type": "message_stop"},
            "",
            "",
            True,
        ),
        # Content block start = nothing to show.
        (
            {"type": "content_block_start", "index": 0},
            "",
            "",
            False,
        ),
    ],
)
def test_anthropic_adapter_parses_event_types(
    raw: dict,
    expected_content: str,
    expected_reasoning: str,
    expected_final: bool,
) -> None:
    adapter = AnthropicAdapter()
    chunk = adapter.parse_chunk(raw)
    assert chunk.content == expected_content
    assert chunk.reasoning == expected_reasoning
    assert chunk.is_final is expected_final


def test_anthropic_matches_anthropic_url() -> None:
    assert AnthropicAdapter().matches(
        LLMConfig(base_url="https://api.anthropic.com/v1/messages")
    )
    assert AnthropicAdapter().matches(
        LLMConfig(base_url="https://anthropic.example.com/v1/messages")
    )


def test_anthropic_does_not_match_openai_url() -> None:
    assert not AnthropicAdapter().matches(
        LLMConfig(base_url="https://api.openai.com/v1")
    )


# ---------------------------------------------------------------------------
# Abstract base contract
# ---------------------------------------------------------------------------


def test_provider_adapter_base_matches_returns_false_by_default() -> None:
    assert ProviderAdapter().matches(LLMConfig()) is False


def test_provider_adapter_base_parse_chunk_raises() -> None:
    with pytest.raises(NotImplementedError):
        ProviderAdapter().parse_chunk({})
