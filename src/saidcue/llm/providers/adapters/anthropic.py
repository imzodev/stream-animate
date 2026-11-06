"""Anthropic Messages API adapter.

Anthropic's streaming format is structurally different from OpenAI's
chat-completions API. Each event has a ``type`` field; text arrives
via ``content_block_delta`` events with shape::

    {
      "type": "content_block_delta",
      "index": 0,
      "delta": {
        "type": "text_delta",   # or "thinking_delta" / "input_json_delta"
        "text": "Hello"
      }
    }

Reasoning (extended thinking) arrives via the same envelope but with
``delta.type == "thinking_delta"`` and the token in ``delta.thinking``.

A separate ``message_delta`` event carries ``stop_reason`` to signal
end-of-stream; ``message_stop`` is the hard terminator.

This adapter is a separate class (not a subclass of the OpenAI
adapter) because the streaming protocol is genuinely different and
shares no JSON shape with OpenAI.
"""

from __future__ import annotations

from ..base import ProviderAdapter, StreamChunk
from ...config import LLMConfig


# Recognised Anthropic event ``type`` values.
_EVENT_CONTENT_BLOCK_START = "content_block_start"
_EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
_EVENT_MESSAGE_DELTA = "message_delta"
_EVENT_MESSAGE_STOP = "message_stop"

# Recognised ``delta.type`` values inside content_block_delta.
_DELTA_TEXT = "text_delta"
_DELTA_THINKING = "thinking_delta"


class AnthropicAdapter(ProviderAdapter):
    """Adapter for Anthropic Messages streaming endpoints."""

    name = "anthropic"

    def matches(self, config: LLMConfig) -> bool:
        url = (config.base_url or "").lower()
        return "anthropic" in url or "/v1/messages" in url

    def parse_chunk(self, raw: dict) -> StreamChunk:
        event_type = raw.get("type", "") if isinstance(raw, dict) else ""

        if event_type == _EVENT_CONTENT_BLOCK_DELTA:
            delta = raw.get("delta") if isinstance(raw, dict) else None
            if not isinstance(delta, dict):
                return StreamChunk()
            kind = delta.get("type")
            if kind == _DELTA_THINKING:
                thinking = delta.get("thinking") or ""
                return StreamChunk(reasoning=str(thinking))
            if kind == _DELTA_TEXT:
                text = delta.get("text") or ""
                return StreamChunk(content=str(text))
            # input_json_delta and other types carry no visible text.
            return StreamChunk()

        if event_type == _EVENT_MESSAGE_DELTA:
            delta = raw.get("delta") if isinstance(raw, dict) else None
            stop_reason = ""
            if isinstance(delta, dict):
                stop_reason = str(delta.get("stop_reason") or "")
            is_final = bool(stop_reason)
            return StreamChunk(is_final=is_final, finish_reason=stop_reason)

        if event_type == _EVENT_MESSAGE_STOP:
            return StreamChunk(is_final=True, finish_reason="end_turn")

        # content_block_start, ping, other events: nothing to display.
        return StreamChunk()
