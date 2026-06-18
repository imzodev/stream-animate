"""Adapter base classes and the normalized StreamChunk value object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import LLMConfig


@dataclass(frozen=True)
class StreamChunk:
    """A single normalized streaming chunk produced by an adapter.

    Attributes:
        content: Visible answer tokens for this chunk. Empty string
            when the chunk carries no answer text.
        reasoning: Chain-of-thought tokens for this chunk (e.g.
            DeepSeek Reasoner, o1-style models). Empty string when
            the provider does not expose reasoning.
        is_final: True when the provider has signalled end-of-stream
            (e.g. ``finish_reason="stop"`` or a ``[DONE]`` sentinel).
            The client uses this to stop iterating.
        finish_reason: The provider's raw finish_reason field, when
            present. Useful for logging / debugging; the engine
            does not act on it.
        role_delta: A role indicator that may appear on the first
            chunk (e.g. ``"assistant"``). Empty string when absent.
    """

    content: str = ""
    reasoning: str = ""
    is_final: bool = False
    finish_reason: str = ""
    role_delta: str = ""


class ProviderAdapter:
    """Base class for a provider-specific chunk parser.

    Subclasses define :meth:`matches` (cheap predicate) and
    :meth:`parse_chunk` (raw JSON -> :class:`StreamChunk`). The
    factory tries adapters in :data:`stream_companion.llm.providers.factory.ADAPTERS`
    order; the first whose ``matches`` returns True wins.
    """

    #: Short human-readable name (used in logs and error messages).
    name: str = "unknown"

    def matches(self, config: "LLMConfig") -> bool:
        """Return True if this adapter handles ``config``.

        Default implementation returns False (i.e. subclasses must
        opt in). Implementations should be cheap — they run on every
        request.
        """

        return False

    def parse_chunk(self, raw: dict) -> StreamChunk:
        """Convert a raw provider chunk into a normalized StreamChunk.

        Must handle the common edge cases: missing keys, empty
        delta, role-only first chunk, finish_reason signals.
        """

        raise NotImplementedError
