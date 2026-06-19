"""Tests for the OpenAI-compatible streaming client."""

from __future__ import annotations

import json
from typing import Callable, List

import httpx
import pytest

from stream_companion.llm import FactCheckerClient, LLMError
from stream_companion.models import LLMConfig

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_sse(chunks: List[dict]) -> bytes:
    """Encode a list of chunk dicts as an SSE byte stream."""
    parts: List[bytes] = []
    for c in chunks:
        parts.append(b"data: " + json.dumps(c).encode("utf-8") + b"\n\n")
    parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def _make_handler(
    response_factory: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(response_factory)


def _ok_response(chunks: List[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=_make_sse(chunks),
    )


def _chunks_with_tokens(*tokens: str) -> List[dict]:
    return [
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": t}}],
        }
        for t in tokens
    ]


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_rejects_missing_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    cfg = LLMConfig(base_url="https://api.example.com")
    with pytest.raises(LLMError) as exc:
        FactCheckerClient(cfg)
    assert "config" == exc.value.message
    assert "/v1" in exc.value.body


def test_construction_rejects_non_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    cfg = LLMConfig(base_url="ftp://nope")
    with pytest.raises(LLMError) as exc:
        FactCheckerClient(cfg)
    assert "config" == exc.value.message


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/",
        "https://api.deepseek.com/v1",
        "https://api.minimax.com/v1",
        "http://localhost:11434/v1",  # Ollama default
    ],
)
def test_construction_accepts_known_providers(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    client = FactCheckerClient(LLMConfig(base_url=base_url))
    client.close()


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        ("https://api.openai.com/v1/", "https://api.openai.com/v1/chat/completions"),
        (
            "https://opencode.ai/zen/go/v1/chat/completions",
            "https://opencode.ai/zen/go/v1/chat/completions",
        ),
        (
            "https://opencode.ai/zen/go/v1/chat/completions/",
            "https://opencode.ai/zen/go/v1/chat/completions",
        ),
        (
            "https://example.com/v1/chat/completions",
            "https://example.com/v1/chat/completions",
        ),
    ],
)
def test_url_is_not_double_chat_completions(
    base_url: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """base_url may end with /chat/completions; we must not append it again."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    # Replace the http_client's transport with a MockTransport so we
    # can observe the request URL without doing a real network call.
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n",
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        client = FactCheckerClient(
            LLMConfig(base_url=base_url), http_client=http_client
        )
        try:
            for _ in client.stream("ping"):
                break
        finally:
            client.close()
        assert captured, "no request was made"
        assert captured[0] == expected
    finally:
        http_client.close()


# ---------------------------------------------------------------------------
# Streaming — happy path
# ---------------------------------------------------------------------------


def test_stream_yields_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    captured: List[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return _ok_response(_chunks_with_tokens("Hello", " world", "!"))

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        chunks = list(client.stream("Why is the sky blue?"))
    finally:
        http_client.close()

    # The thinking extractor buffers up to ``hold`` chars per call
    # so tags that span chunk boundaries are recognised. For
    # tag-free streams the cumulative content still equals the
    # raw input; only the per-chunk boundaries change.
    assert "".join(c.content for c in chunks) == "Hello world!"
    assert all(c.reasoning == "" for c in chunks)
    assert all(c.is_final is False for c in chunks)
    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url).endswith("/chat/completions")
    body = json.loads(req.content.decode("utf-8"))
    assert body["model"] == "gpt-4o-mini"
    assert body["stream"] is True
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1] == {"role": "user", "content": "Why is the sky blue?"}
    assert "Bearer sk-test" in req.headers["authorization"]


def test_stream_surfaces_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek-style models emit reasoning_content before content.

    The client must surface both as separate fields on the StreamChunk
    so the engine can render them differently (or at least not lose
    them silently).
    """
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    def handler(req: httpx.Request) -> httpx.Response:
        chunks = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "reasoning_content": "Let me think",
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": " about this."},
                    }
                ]
            },
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "The answer is 4."},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]
        return _ok_response(chunks)

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(
            LLMConfig(model="deepseek-v4-flash"), http_client=http_client
        )
        chunks = list(client.stream("What is 2+2?"))
    finally:
        http_client.close()

    reasoning = [c.reasoning for c in chunks if c.reasoning]
    content = [c.content for c in chunks if c.content]
    assert reasoning == ["Let me think", " about this."]
    assert content == ["The answer is 4."]
    # The final chunk must be marked is_final so the engine stops.
    assert chunks[-1].is_final is True


def test_stream_sends_resolved_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    captured_body: List[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_body.append(json.loads(req.content.decode("utf-8")))
        return _ok_response(_chunks_with_tokens("ok"))

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(
            LLMConfig(persona="eli5"),
            http_client=http_client,
        )
        list(client.stream("q"))
    finally:
        http_client.close()

    assert "like they are five" in captured_body[0]["messages"][0]["content"]


def test_stream_handles_role_only_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    chunks = [
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        *_chunks_with_tokens("Hi"),
    ]
    transport = _make_handler(lambda _r: _ok_response(chunks))
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        emitted = list(client.stream("q"))
    finally:
        http_client.close()
    # The role-only chunk is filtered out (no content / reasoning).
    # Only the token-bearing chunk contributes content; the
    # extractor may split it across multiple yield points but the
    # cumulative content equals the raw input.
    assert "".join(c.content for c in emitted) == "Hi"
    # The first emitted chunk had a role_delta even though it
    # carried no text (if it was yielded at all).
    if emitted and emitted[0].role_delta:
        assert emitted[0].role_delta == "assistant"


def test_stream_handles_ollama_style_message_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    chunks = [
        {
            "choices": [
                {"index": 0, "delta": {}, "message": {"content": "ollama-token"}}
            ]
        },
    ]
    transport = _make_handler(lambda _r: _ok_response(chunks))
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        emitted = list(client.stream("q"))
    finally:
        http_client.close()
    assert "".join(c.content for c in emitted) == "ollama-token"


def test_stream_fact_checker_uses_correct_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a model name from a registered provider picks the
    matching adapter. We can detect which adapter was used by
    observing the chunk shape it accepts."""
    from stream_companion.llm.providers import AdapterFactory
    from stream_companion.llm.providers.adapters.deepseek import DeepSeekAdapter

    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    adapter = AdapterFactory.create(LLMConfig(model="deepseek-v4-flash"))
    assert isinstance(adapter, DeepSeekAdapter)

    adapter = AdapterFactory.create(LLMConfig(model="gpt-4o"))
    # OpenAI generic matches anything that isn't Anthropic.
    from stream_companion.llm.providers.adapters.openai_generic import (
        OpenAIGenericAdapter,
    )

    assert isinstance(adapter, OpenAIGenericAdapter)

    adapter = AdapterFactory.create(
        LLMConfig(
            base_url="https://api.anthropic.com/v1/messages",
            model="claude-3-5-sonnet",
        )
    )
    from stream_companion.llm.providers.adapters.anthropic import AnthropicAdapter

    assert isinstance(adapter, AnthropicAdapter)


# ---------------------------------------------------------------------------
# Streaming — error paths
# ---------------------------------------------------------------------------


def test_stream_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    # A handler that should never be reached.
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP should not be called without an API key")

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        with pytest.raises(LLMError) as exc:
            list(client.stream("q"))
        assert exc.value.message == "auth"
    finally:
        http_client.close()


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503])
def test_stream_raises_on_http_error(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="upstream error")

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        with pytest.raises(LLMError) as exc:
            list(client.stream("q"))
        assert exc.value.status == status
        assert "upstream error" in exc.value.body
    finally:
        http_client.close()


def test_stream_raises_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns blew up")

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        with pytest.raises(LLMError) as exc:
            list(client.stream("q"))
        assert exc.value.message == "network"
        assert "dns blew up" in exc.value.body
    finally:
        http_client.close()


def test_stream_skips_malformed_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    body = (
        b"data: not-json-at-all\n\n"
        b"data: " + json.dumps(_chunks_with_tokens("A")[0]).encode() + b"\n\n"
        b"data: {also-bad}\n\n"
        b"data: " + json.dumps(_chunks_with_tokens("B")[0]).encode() + b"\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        emitted = list(client.stream("q"))
    finally:
        http_client.close()
    assert "".join(c.content for c in emitted) == "AB"


def test_stream_skips_sse_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    body = (
        b": this is a comment line\n\n"
        b"data: " + json.dumps(_chunks_with_tokens("ok")[0]).encode() + b"\n\n"
        b"data: [DONE]\n\n"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body,
        )

    transport = _make_handler(handler)
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        emitted = list(client.stream("q"))
    finally:
        http_client.close()
    assert [c.content for c in emitted] == ["ok"]


def test_stream_early_break_closes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    # Many chunks; the caller will stop after the first.
    chunks = _chunks_with_tokens("a", "b", "c", "d", "e")
    transport = _make_handler(lambda _r: _ok_response(chunks))
    http_client = httpx.Client(transport=transport)
    try:
        client = FactCheckerClient(LLMConfig(), http_client=http_client)
        gen = client.stream("q")
        first = next(gen)
        gen.close()
        # The extractor buffers up to ``hold`` chars, so the first
        # yielded chunk may not be the full "a" token — but it
        # must start with "a".
        assert first.content.startswith("a")
    finally:
        http_client.close()


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_redact_body_strips_api_keys() -> None:
    # This indirectly exercises _redact_body through the HTTP error path.
    from stream_companion.llm.client import _redact_body

    redacted = _redact_body("Authorization: Bearer sk-1234567890abcdef more text")
    assert "sk-1234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_error_body_truncated() -> None:
    from stream_companion.llm.client import _MAX_ERROR_BODY, _redact_body

    huge = "x" * (_MAX_ERROR_BODY * 4)
    redacted = _redact_body(huge)
    assert redacted.endswith("...[truncated]")


# ---------------------------------------------------------------------------
# context manager
# ---------------------------------------------------------------------------


def test_context_manager_closes_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    with FactCheckerClient(LLMConfig()) as c:
        assert c.config.model == "gpt-4o-mini"
    # No assertion needed — close() on the default client is safe to call.
