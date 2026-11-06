"""Stateful extractor that separates chain-of-thought from the
visible answer in a token stream.

Different providers/models expose reasoning in two shapes:

1. **Structured** — a dedicated ``reasoning_content`` / ``thinking``
   field separate from ``content`` (DeepSeek Reasoner, OpenAI o1/o3,
   Anthropic extended thinking). The adapters already handle this
   by populating :attr:`StreamChunk.reasoning` directly.

2. **Inline** — the reasoning is wrapped in XML-like tags inside the
   visible ``content`` field. Common conventions:

   - ``<thinking>...</thinking>``  (Qwen, many local models)
   - ``<reasoning>...</reasoning>``
   - ``<thought>...</thought>``
   - ``[THINKING]...[/THINKING]`` (some custom proxies)

   Tags may span multiple streaming chunks (the open ``<think`` may
   arrive in chunk N and the closing ``>`` in chunk N+1), so a
   naive per-chunk regex misses them. This module implements a
   tiny state machine that buffers the partial tail of each chunk
   and re-emits clean answer / reasoning deltas.

Strategies (configurable via :class:`LLMConfig.thinking`):

* ``SEPARATE`` (default) — split inline reasoning out of
  ``content`` and merge it into ``reasoning`` so the GUI can
  render it as italic grey. Original behavior preserved.
* ``STRIP`` — drop inline reasoning entirely. The panel shows
  only the final answer. Use when the chain-of-thought is noisy.
* ``KEEP`` — pass ``content`` through untouched. The tags remain
  visible in the answer. Useful for debugging.

Adding a new tag convention is a one-line change in
:data:`DEFAULT_TAG_PATTERNS` — no adapter needs to know about it.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import List, Tuple

_LOGGER = logging.getLogger(__name__)


class ThinkingStrategy(str, enum.Enum):
    """How to handle inline ``<thinking>...</thinking>`` content."""

    SEPARATE = "separate"
    STRIP = "strip"
    KEEP = "keep"


# Default tag conventions. Order matters: longer / more specific
# patterns first so we don't accidentally match a prefix of one tag
# as another. Each pair is (open_tag, close_tag).
DEFAULT_TAG_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("<thinking>", "</thinking>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("[THINKING]", "[/THINKING]"),
)


@dataclass
class ThinkingSplit:
    """Result of splitting a single chunk's content.

    Attributes:
        reasoning: Reasoning tokens discovered in this chunk
            (empty when nothing matched). Emitted with
            ``kind="reasoning"``.
        answer: Visible answer tokens for this chunk (empty when
            the entire chunk was reasoning). Emitted with
            ``kind="answer"``.
    """

    reasoning: str = ""
    answer: str = ""


@dataclass
class ThinkingExtractor:
    """Stateful inline-thinking extractor.

    One instance per stream. The caller feeds each chunk's
    ``content`` (in order) to :meth:`process` and receives the
    split between answer and reasoning. The extractor buffers the
    partial tail of each chunk so tags that span chunk boundaries
    are still recognised.

    The extractor is intentionally tiny and dependency-free so it
    is easy to unit-test with hundreds of chunk permutations.
    """

    strategy: ThinkingStrategy = ThinkingStrategy.SEPARATE
    tag_patterns: Tuple[Tuple[str, str], ...] = DEFAULT_TAG_PATTERNS
    # Internal state.
    _inside_thinking: bool = field(default=False, init=False, repr=False)
    _buffer: str = field(default="", init=False, repr=False)
    # Cached maximum open-tag length so the partial-tag buffer is
    # exactly large enough — never smaller (we'd miss a tag) and
    # never larger (we'd withhold chunks forever).
    _max_open_tag_len: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._max_open_tag_len = max(
            (len(open_tag) for open_tag, _ in self.tag_patterns),
            default=0,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Clear internal state. Call before processing a new stream."""
        self._inside_thinking = False
        self._buffer = ""

    def flush(self) -> ThinkingSplit:
        """Emit any remaining buffered text and reset state.

        The caller must call this at end-of-stream (e.g. after the
        provider sends ``[DONE]`` or the HTTP connection closes).
        Otherwise up to ``max_open_tag_len - 1`` characters of the
        final answer can be stuck in the carry-over buffer forever.

        Reasoning vs answer classification depends on the
        ``_inside_thinking`` flag at flush time:

        * If we were inside a thinking block, everything buffered
          is treated as reasoning.
        * Otherwise, everything buffered is treated as answer.
        """

        tail = self._buffer
        self._buffer = ""
        if self._inside_thinking:
            if self.strategy == ThinkingStrategy.STRIP:
                return ThinkingSplit()
            return ThinkingSplit(reasoning=tail)
        return ThinkingSplit(answer=tail)

    def process(self, content: str) -> ThinkingSplit:
        """Split one chunk's ``content`` into (reasoning, answer).

        Handles two cases:

        * ``content`` may contain one or more complete
          ``<thinking>...</thinking>`` regions.
        * The open or close tag may be split across the boundary
          with the previous / next chunk. The extractor buffers up
          to ``max_open_tag_len - 1`` characters of the tail in
          case a tag is just starting there.

        When :attr:`strategy` is ``STRIP``, reasoning deltas are
        discarded. When ``KEEP``, the original ``content`` is
        returned as ``answer`` unchanged.
        """

        if self.strategy == ThinkingStrategy.KEEP or not content:
            return ThinkingSplit(answer=content if content else "")

        # We hold back the last ``hold`` characters of the combined
        # input in case an open tag is just starting there. As
        # soon as we have more than ``hold`` chars, the leftover
        # cannot be a partial tag (the longest open tag fits in
        # ``hold + 1`` chars), so it is safe to flush.
        hold = max(0, self._max_open_tag_len - 1)
        combined = self._buffer + content

        if len(combined) <= hold:
            # Too short to decide; wait for more chunks.
            self._buffer = combined
            return ThinkingSplit()

        n = len(combined)
        reasoning_parts: List[str] = []
        answer_parts: List[str] = []

        cursor = 0
        while cursor < n:
            if self._inside_thinking:
                # Close tag may lie anywhere from cursor to the
                # end of combined (since the open tag may have
                # been near the end of a previous chunk, leaving
                # its close tag in the buffer tail).
                close_idx, close_len = self._find_close_anywhere(combined, cursor, n)
                if close_idx < 0:
                    # No close tag in combined. Emit everything
                    # we've decided about (cursor to n-hold) as
                    # reasoning and hold the last ``hold`` chars
                    # in the buffer in case the close tag
                    # continues in the next chunk.
                    if cursor <= n - hold:
                        reasoning_parts.append(combined[cursor : n - hold])
                        self._buffer = combined[n - hold :]
                    else:
                        reasoning_parts.append(combined[cursor:])
                        self._buffer = ""
                    cursor = n
                    break
                reasoning_parts.append(combined[cursor:close_idx])
                cursor = close_idx + close_len
                self._inside_thinking = False
            else:
                # Open tag: must fully fit in combined. If the
                # tag would extend past combined (because it
                # straddles into the next chunk), we hold back
                # from its start.
                open_idx, open_len = self._find_open_with_len(combined, cursor, n)
                if open_idx < 0:
                    # No open tag fits in the remainder.
                    if cursor <= n - hold:
                        # Emit everything we've decided about,
                        # buffer the last ``hold`` chars in case
                        # an open tag starts in the next chunk.
                        answer_parts.append(combined[cursor : n - hold])
                        self._buffer = combined[n - hold :]
                    else:
                        # We already processed past the hold
                        # region (e.g. a close tag ended past
                        # it). Emit everything as answer; nothing
                        # more to hold.
                        answer_parts.append(combined[cursor:])
                        self._buffer = ""
                    cursor = n
                    break
                if open_idx + open_len > n - hold:
                    # Open tag starts but extends into the
                    # buffer tail — hold it all back from the
                    # tag's start.
                    answer_parts.append(combined[cursor:open_idx])
                    self._buffer = combined[open_idx:]
                    cursor = n
                    break
                # Open tag fully inside the safe region.
                answer_parts.append(combined[cursor:open_idx])
                cursor = open_idx + open_len
                self._inside_thinking = True

        answer_delta = "".join(answer_parts)
        reasoning_delta = "".join(reasoning_parts)

        if self.strategy == ThinkingStrategy.STRIP:
            return ThinkingSplit(answer=answer_delta)

        return ThinkingSplit(reasoning=reasoning_delta, answer=answer_delta)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find_open(self, text: str, start: int) -> Tuple[int, int]:
        """Return (index, tag_length) of the earliest matching open
        tag in ``text[start:]``, or (-1, 0) if none. Iterates
        through the configured tag patterns in order so the
        longest / most specific tag wins ties.
        """

        best_idx = -1
        best_len = 0
        for open_tag, _close in self.tag_patterns:
            idx = text.find(open_tag, start)
            if idx < 0:
                continue
            if best_idx < 0 or idx < best_idx:
                best_idx = idx
                best_len = len(open_tag)
        return best_idx, best_len

    def _find_open_with_len(self, text: str, start: int, end: int) -> Tuple[int, int]:
        """Like :meth:`_find_open` but the tag must be fully
        contained in ``text[:end]``. Tags that would extend past
        ``end`` are not returned — the caller is expected to hold
        them back.
        """

        best_idx = -1
        best_len = 0
        for open_tag, _close in self.tag_patterns:
            idx = text.find(open_tag, start)
            if idx < 0:
                continue
            if idx + len(open_tag) > end:
                # Tag would extend past the end — don't emit.
                continue
            if best_idx < 0 or idx < best_idx:
                best_idx = idx
                best_len = len(open_tag)
        return best_idx, best_len

    def _find_close_anywhere(self, text: str, start: int, end: int) -> Tuple[int, int]:
        """Return (index, tag_length) of the earliest matching close
        tag in ``text[start:end]``, or (-1, 0) if none. Accepts ANY
        configured close tag (not just the one matching the
        currently-open tag) so the extractor is robust to nested
        / mismatched tags from models that emit
        ``<thinking>...</reasoning>``.
        """

        best_idx = -1
        best_len = 0
        for _open, close_tag in self.tag_patterns:
            idx = text.find(close_tag, start, end)
            if idx < 0:
                continue
            if best_idx < 0 or idx < best_idx:
                best_idx = idx
                best_len = len(close_tag)
        return best_idx, best_len


__all__ = [
    "DEFAULT_TAG_PATTERNS",
    "ThinkingExtractor",
    "ThinkingSplit",
    "ThinkingStrategy",
]
