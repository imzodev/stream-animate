"""Provider adapter abstractions for the LLM streaming client.

Each provider (OpenAI, DeepSeek, Anthropic, Ollama, ...) delivers
chat-completions chunks in a slightly different shape. Some put the
model's answer in ``delta.content``; others use a top-level
``message.content`` or a separate ``content_block_delta`` event. Some
expose chain-of-thought as ``delta.reasoning_content``; others as a
sibling ``thinking`` block; others don't expose it at all.

Instead of growing a wall of ``if`` branches inside ``client.py``,
this package defines a single normalized ``StreamChunk`` value object
and a ``ProviderAdapter`` interface that maps provider-specific
chunks to it. ``AdapterFactory`` picks the right adapter based on the
configured ``base_url`` and ``model`` (matching by URL hostname or
model prefix).

To add support for a new provider:

1. Subclass :class:`ProviderAdapter`.
2. Implement ``matches(config) -> bool`` (cheap predicate on
   ``base_url`` and/or ``model``).
3. Implement ``parse_chunk(raw) -> StreamChunk``.
4. Register the adapter in :data:`ADAPTERS` (or let
   :func:`AdapterFactory.create` fall through to the generic
   OpenAI adapter if the URL is a standard ``/v1`` chat-completions
   endpoint).
"""

from __future__ import annotations

from .adapters.anthropic import AnthropicAdapter
from .adapters.deepseek import DeepSeekAdapter
from .adapters.openai_generic import OpenAIGenericAdapter
from .base import ProviderAdapter, StreamChunk
from .factory import AdapterFactory, ADAPTERS

__all__ = [
    "AdapterFactory",
    "ADAPTERS",
    "ProviderAdapter",
    "StreamChunk",
    "AnthropicAdapter",
    "DeepSeekAdapter",
    "OpenAIGenericAdapter",
]
