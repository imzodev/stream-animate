"""Factory: pick the right :class:`ProviderAdapter` for a config.

:func:`AdapterFactory.create` tries each adapter in :data:`ADAPTERS`
order; the first whose ``matches(config)`` returns True wins. The
last entry is always :class:`OpenAIGenericAdapter` (with a
``matches()`` that returns False for everything else), so any
standard ``/v1/chat/completions`` endpoint works out of the box
even when the model name isn't recognized by a specific adapter.

To add a new provider, append the new adapter class to
:data:`ADAPTERS` (BEFORE the OpenAI generic). The factory requires
no other change.
"""

from __future__ import annotations

import logging
from typing import List, Type

from .base import ProviderAdapter, StreamChunk
from .adapters.anthropic import AnthropicAdapter
from .adapters.deepseek import DeepSeekAdapter
from .adapters.openai_generic import OpenAIGenericAdapter
from ..config import LLMConfig

_LOGGER = logging.getLogger(__name__)


# Order matters: specific adapters first, generic OpenAI last.
ADAPTERS: List[Type[ProviderAdapter]] = [
    AnthropicAdapter,
    DeepSeekAdapter,
    OpenAIGenericAdapter,
]


class AdapterFactory:
    """Picks the right provider adapter for an LLMConfig."""

    @staticmethod
    def create(config: LLMConfig) -> ProviderAdapter:
        """Return the first adapter whose ``matches(config)`` is True.

        Always returns an adapter instance — the generic OpenAI
        adapter is the safety net for any standard chat-completions
        endpoint that no specific adapter claims.
        """

        for adapter_cls in ADAPTERS:
            try:
                if adapter_cls().matches(config):
                    _LOGGER.debug(
                        "LLM adapter selected: %s for model=%r",
                        adapter_cls.name,
                        config.model,
                    )
                    return adapter_cls()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception(
                    "Adapter %s.matches() raised; skipping", adapter_cls.__name__
                )
        # Should be unreachable because the generic OpenAI adapter
        # never matches, and we don't have a True-by-default fallback.
        # Defensive: return the generic adapter anyway.
        _LOGGER.warning(
            "No LLM adapter matched config (model=%r); falling back to OpenAI generic",
            config.model,
        )
        return OpenAIGenericAdapter()


__all__ = ["AdapterFactory", "ADAPTERS", "ProviderAdapter", "StreamChunk"]
