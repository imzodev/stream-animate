"""Unit tests for the :mod:`stream_companion.llm.thinking` module.

The extractor is stateful and must handle the awkward cases that
come up in real streaming:

* Plain text (no tags) — must still emit everything, just in
  smaller pieces because of the carry-over buffer.
* Complete ``<thinking>...</thinking>`` regions in a single
  chunk — must split into (reasoning, answer) cleanly.
* Tags that span chunk boundaries — the extractor must buffer
  the partial tail and reassemble on the next call.
* Multiple tags in one chunk — must handle in order.
* End-of-stream flush — must emit any remaining buffered text.
* All three strategies (SEPARATE / STRIP / KEEP).
"""

from __future__ import annotations


from stream_companion.llm.thinking import (
    DEFAULT_TAG_PATTERNS,
    ThinkingExtractor,
    ThinkingSplit,
    ThinkingStrategy,
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_strategies() -> None:
    """The strategy enum exposes exactly three members."""
    assert {s.value for s in ThinkingStrategy} == {"separate", "strip", "keep"}


def test_default_tag_patterns_cover_common_conventions() -> None:
    """The default tag set includes the most common inline
    thinking-tag conventions used by open-source models."""
    tags = [open_tag for open_tag, _ in DEFAULT_TAG_PATTERNS]
    assert "<thinking>" in tags
    assert "<reasoning>" in tags
    assert "<thought>" in tags


def test_default_tag_patterns_pairs_match() -> None:
    """Every open tag has a matching close tag of the same form."""
    for open_tag, close_tag in DEFAULT_TAG_PATTERNS:
        assert open_tag.startswith("<") == close_tag.startswith("</")
        assert open_tag.endswith(">") == close_tag.endswith(">")


# ---------------------------------------------------------------------------
# Plain text (no tags)
# ---------------------------------------------------------------------------


def test_plain_text_eventually_emits_full_content() -> None:
    """A short plain-text chunk is held back initially (the
    extractor doesn't know yet whether a tag is starting), then
    flushed at end-of-stream. The cumulative content equals the
    input."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    parts: list[str] = []
    parts += e.process("Hello world!").answer
    parts += e.flush().answer
    assert "".join(parts) == "Hello world!"


def test_plain_text_across_multiple_chunks() -> None:
    """Long content spread across many small chunks must reassemble
    in order, with no chars lost or duplicated."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    parts: list[str] = []
    for c in ["abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx", "yz!"]:
        parts += e.process(c).answer
    parts += e.flush().answer
    assert "".join(parts) == "abcdefghijklmnopqrstuvwxyz!"


# ---------------------------------------------------------------------------
# Complete tag
# ---------------------------------------------------------------------------


def test_complete_tag_in_one_chunk_separate() -> None:
    """A complete <thinking>foo</thinking>bar chunk must split
    into reasoning='foo' and answer='bar' across the process()
    and flush() calls."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    r1 = e.process("<thinking>thought</thinking>answer")
    flush = e.flush()
    total_reasoning = r1.reasoning + flush.reasoning
    total_answer = r1.answer + flush.answer
    assert total_reasoning == "thought"
    assert total_answer == "answer"


def test_complete_tag_in_one_chunk_strip() -> None:
    """STRIP drops the reasoning entirely."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.STRIP)
    parts: list[str] = []
    parts += e.process("<thinking>thought</thinking>answer").answer
    parts += e.flush().answer
    assert "".join(parts) == "answer"


def test_complete_tag_in_one_chunk_keep() -> None:
    """KEEP leaves the tags visible in the answer."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.KEEP)
    r = e.process("<thinking>thought</thinking>answer")
    assert r.reasoning == ""
    assert r.answer == "<thinking>thought</thinking>answer"
    # flush should emit empty (nothing held back)
    assert e.flush().answer == ""


# ---------------------------------------------------------------------------
# Tag split across chunks
# ---------------------------------------------------------------------------


def test_open_tag_split_across_chunks() -> None:
    """The open tag <thinking> arrives split: '<thin' then 'king>'.
    The extractor must hold the partial tag, then on a later
    call recognise the full open tag and start reasoning. The
    close tag may also be split across chunks; reasoning content
    is only emitted once the close tag is fully visible."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    r1 = e.process("<thin")
    assert r1.reasoning == ""
    assert r1.answer == ""
    # Second chunk completes the open tag and adds the content
    # and a partial close tag. The reasoning is held back
    # because the close tag is still partial.
    r2 = e.process("king>foo</think")
    assert r2.reasoning == ""
    assert r2.answer == ""
    # Third chunk completes the close tag. Now the reasoning
    # 'foo' is emitted, and 'bar' becomes answer.
    r3 = e.process("ing>bar")
    flush = e.flush()
    total_reasoning = r2.reasoning + r3.reasoning + flush.reasoning
    total_answer = r2.answer + r3.answer + flush.answer
    assert total_reasoning == "foo"
    assert total_answer == "bar"


def test_close_tag_split_across_chunks() -> None:
    """The close tag </thinking> arrives split: '</thin' then
    'king>'. The reasoning must include everything between the
    open and close tags, and the close tag itself is consumed
    cleanly."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    e.process("<thinking>")
    r1 = e.process("foo</thin")
    # Close tag is partial — we should NOT have emitted 'foo</thin'
    # as reasoning yet because the close tag might continue in
    # the next chunk.
    assert r1.reasoning == ""
    r2 = e.process("king>after")
    flush = e.flush()
    total_reasoning = r1.reasoning + r2.reasoning + flush.reasoning
    total_answer = r1.answer + r2.answer + flush.answer
    assert total_reasoning == "foo"
    assert total_answer == "after"


# ---------------------------------------------------------------------------
# Multiple tags
# ---------------------------------------------------------------------------


def test_multiple_tags_in_one_chunk() -> None:
    """Two complete <thinking>...</thinking> regions in a single
    chunk must be split correctly."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    r = e.process("a<thinking>t1</thinking>b<thinking>t2</thinking>c")
    flush = e.flush()
    total_reasoning = r.reasoning + flush.reasoning
    total_answer = r.answer + flush.answer
    assert total_reasoning == "t1t2"
    assert total_answer == "abc"


# ---------------------------------------------------------------------------
# flush()
# ---------------------------------------------------------------------------


def test_flush_emits_buffered_answer_when_outside_thinking() -> None:
    """If we end the stream OUTSIDE a thinking block, the buffered
    tail is plain answer text."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    e.process("some content")
    flush = e.flush()
    assert flush.answer != "" or flush.reasoning != ""


def test_flush_emits_buffered_reasoning_when_inside_thinking() -> None:
    """If we end the stream INSIDE a thinking block (unclosed tag),
    the buffered tail is reasoning. The user sees the full chain
    of thought rather than losing the last few chars. Earlier
    content was already emitted by process() to keep the buffer
    bounded; flush() emits the final tail."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    r = e.process("<thinking>unfinished thought")
    flush = e.flush()
    total = r.reasoning + flush.reasoning
    assert total == "unfinished thought"
    assert r.answer + flush.answer == ""


def test_flush_with_no_data_emits_empty() -> None:
    """flush() before any process() call should be a no-op."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    assert e.flush() == ThinkingSplit()


def test_reset_clears_state() -> None:
    """After reset(), the extractor forgets any partial tag state
    so a new stream can start fresh without bleeding from a
    previous one."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    e.process("<thinking>unfinished")
    e.reset()
    # Now feed plain text — should be classified as answer, not
    # as reasoning continuation.
    r = e.process("hello")
    flush = e.flush()
    total = r.answer + flush.answer
    assert total == "hello"
    assert r.reasoning + flush.reasoning == ""


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


def test_keep_strategy_passes_content_through_untouched() -> None:
    """KEEP is a debugging mode: tags remain visible in answer."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.KEEP)
    r = e.process("hello <thinking>thought</thinking> world")
    assert r.answer == "hello <thinking>thought</thinking> world"
    assert r.reasoning == ""


def test_keep_strategy_skips_extraction_even_for_partial_tags() -> None:
    """KEEP doesn't care about tag boundaries — it just passes
    the content through. The buffer stays empty."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.KEEP)
    r = e.process("<thin")
    assert r.answer == "<thin"
    assert r.reasoning == ""


def test_strip_strategy_drops_reasoning_from_chunks_and_flush() -> None:
    """STRIP discards reasoning both during process() and at
    flush() time."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.STRIP)
    parts: list[str] = []
    parts += e.process("a<thinking>thought1</thinking>b").answer
    parts += e.process("<thinking>thought2</thinking>c").answer
    parts += e.flush().answer
    assert "".join(parts) == "abc"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_input_does_not_yield() -> None:
    """Empty content is a no-op."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    assert e.process("") == ThinkingSplit()


def test_partial_tag_at_chunk_boundary_does_not_emit_as_text() -> None:
    """A chunk that ends with a partial <thin must not emit those
    chars as plain answer; they must wait for the next chunk."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE)
    r = e.process("preface <thin")
    assert "thin" not in r.answer
    assert "<" not in r.answer


def test_extractor_with_empty_tag_patterns_is_pure_pass_through() -> None:
    """If we configure no tag patterns, the extractor should
    behave as if no tag is possible (always hold back the last
    N chars where N is the max open tag length, which is 0 in
    this case)."""
    e = ThinkingExtractor(strategy=ThinkingStrategy.SEPARATE, tag_patterns=())
    parts: list[str] = []
    parts += e.process("hello").answer
    parts += e.flush().answer
    # hold = max(0, 0 - 1) = 0, so everything flushes immediately
    # on the second call (or via flush).
    assert "".join(parts) == "hello"


def test_strategies_are_string_enum() -> None:
    """Strategies are JSON-serialisable strings (so the config
    loader can round-trip them through the JSON file)."""
    assert ThinkingStrategy.SEPARATE.value == "separate"
    assert ThinkingStrategy.STRIP.value == "strip"
    assert ThinkingStrategy.KEEP.value == "keep"
