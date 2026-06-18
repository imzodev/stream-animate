"""DeepSeek-specific adapter.

DeepSeek's ``deepseek-reasoner`` (and the proxy's ``deepseek-v4-flash``
which appears to share the same model class) emits chain-of-thought
in ``delta.reasoning_content`` BEFORE the final answer appears in
``delta.content``. The reasoning stream can be many kilobytes; the
answer is often short.

The base OpenAI shape works, but this adapter adds an explicit
identity so logs / debugging make it clear which adapter is in
play, and so we can recognize new DeepSeek-specific fields in the
future (e.g. tool calls, function names) without touching the
generic parser.
"""

from __future__ import annotations

from .openai_generic import OpenAIGenericAdapter
from ...config import LLMConfig


class DeepSeekAdapter(OpenAIGenericAdapter):
    """Adapter for DeepSeek models (deepseek-chat, deepseek-reasoner, etc.)."""

    name = "deepseek"

    def matches(self, config: LLMConfig) -> bool:
        model = (config.model or "").lower()
        return model.startswith("deepseek") or "deepseek" in model
