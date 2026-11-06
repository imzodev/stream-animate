"""Generic OpenAI-compatible chat-completions adapter.

This is the fallback for any endpoint that exposes the standard
``POST /v1/chat/completions`` streaming shape. Most OpenAI-compatible
providers (Together, Groq, Ollama >=0.4, LM Studio, vLLM) emit
chunks of the form::

    {
      "choices": [{
        "index": 0,
        "delta": {
          "role": "assistant",        # first chunk only
          "content": "Hello",          # subsequent chunks
          "reasoning_content": "..."   # thinking models (optional)
        },
        "finish_reason": "stop"        # last chunk
      }]
    }

If the chunk doesn't fit that shape, falls back to a top-level
``message.content`` field (older Ollama).
"""

from __future__ import annotations

from typing import Any

from ..base import ProviderAdapter, StreamChunk
from ...config import LLMConfig


class OpenAIGenericAdapter(ProviderAdapter):
    """Fallback adapter for any standard OpenAI chat-completions endpoint."""

    name = "openai-generic"

    def matches(self, config: LLMConfig) -> bool:
        # The generic adapter is the universal fallback. It matches
        # anything that talks ``/v1/chat/completions``. We avoid
        # matching obvious non-OpenAI protocols (Anthropic) so the
        # more specific adapter still wins when both are registered.
        url = (config.base_url or "").lower()
        if "anthropic" in url or "/v1/messages" in url:
            return False
        return True

    def parse_chunk(self, raw: dict) -> StreamChunk:
        choices = raw.get("choices") or []
        if not choices:
            return StreamChunk()

        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first, dict) else None
        content = ""
        reasoning = ""
        role_delta = ""
        if isinstance(delta, dict):
            c = delta.get("content")
            if isinstance(c, str):
                content = c
            r = delta.get("reasoning_content")
            if isinstance(r, str):
                reasoning = r
            role = delta.get("role")
            if isinstance(role, str):
                role_delta = role
        elif isinstance(delta, str):
            content = delta

        # Some older providers send a top-level ``message.content``
        # instead of ``delta.content``.
        if not content and not reasoning:
            message = first.get("message") if isinstance(first, dict) else None
            if isinstance(message, dict):
                c = message.get("content")
                if isinstance(c, str):
                    content = c

        finish_reason = (
            first.get("finish_reason", "") if isinstance(first, dict) else ""
        )
        is_final = bool(finish_reason) or _has_done_sentinel(raw)

        return StreamChunk(
            content=content,
            reasoning=reasoning,
            is_final=is_final,
            finish_reason=str(finish_reason or ""),
            role_delta=role_delta,
        )


def _has_done_sentinel(raw: dict) -> bool:
    """Some providers send a literal ``[DONE]`` chunk; detect it.

    The client strips the ``data:`` prefix and checks the body; this
    helper is here in case a non-SSE adapter ever needs it.
    """
    if not isinstance(raw, dict):
        return False
    for key in ("done", "is_done", "finished"):
        value: Any = raw.get(key)
        if isinstance(value, bool) and value:
            return True
    return False
