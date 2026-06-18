"""OpenAI-compatible streaming client for the LLM fact-checker.

Talks to any endpoint that implements ``POST /v1/chat/completions`` with
SSE streaming. Tested against OpenAI, DeepSeek, MiniMax, Together,
Groq, Ollama, LM Studio, and vLLM (which all expose the same shape).

The client is intentionally minimal:

* :class:`LLMError` carries an HTTP status (when applicable) and a
  redacted body for diagnostics.
* :func:`stream` yields token deltas. It is an iterator, so the caller
  can stop early (e.g. when the user toggles the engine off).
* Malformed SSE lines are skipped, not fatal — partial network damage
  shouldn't kill the stream.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterator, Optional

import httpx

from .config import LLMConfig

_LOGGER = logging.getLogger(__name__)


# Maximum bytes of an HTTP response body we'll log on error. Anything
# larger is truncated to keep logs readable.
_MAX_ERROR_BODY = 512


@dataclass
class LLMError(Exception):
    """Raised when the LLM request fails.

    Attributes:
        status: HTTP status code, or ``None`` for transport-level errors.
        body: A short, redacted error body. Never includes the API key.
    """

    message: str
    status: Optional[int] = None
    body: str = ""

    def __str__(self) -> str:
        if self.status is not None:
            return f"{self.message} (status={self.status}): {self.body}"
        return f"{self.message}: {self.body}"


def _validate_base_url(base_url: str) -> None:
    """Ensure the base URL points at a ``/v1`` chat-completions endpoint."""

    if not base_url or not base_url.startswith(("http://", "https://")):
        raise LLMError(
            "config",
            body=f"base_url must be http(s); got {base_url!r}",
        )
    # Accept both "https://api.x.com/v1" and "https://api.x.com/v1/" and
    # ".../v1/chat/completions" forms.
    if "/v1" not in base_url:
        raise LLMError(
            "config",
            body=(
                f"base_url must include '/v1' (got {base_url!r}). "
                "Most OpenAI-compatible endpoints expose the chat "
                "completions API at /v1/chat/completions."
            ),
        )


def _redact_body(body: str) -> str:
    """Strip any ``api_key=...`` or ``sk-...`` patterns from a log body."""

    if not body:
        return ""
    redacted = body
    for token in (
        "sk-",
        "sk_",
        "Bearer ",
    ):
        if token in redacted:
            # Drop everything from the token to the next whitespace or
            # end-of-string. Best-effort redaction.
            head, sep, tail = redacted.partition(token)
            cut = tail.find(" ")
            if cut == -1:
                redacted = head + sep + "[REDACTED]"
            else:
                redacted = head + sep + "[REDACTED]" + tail[cut:]
    if len(redacted) > _MAX_ERROR_BODY:
        redacted = redacted[:_MAX_ERROR_BODY] + "...[truncated]"
    return redacted


class FactCheckerClient:
    """Stateless OpenAI-compatible streaming client.

    Construct with an :class:`LLMConfig`. Pass an optional
    :class:`httpx.Client` to inject a transport in tests; otherwise a
    default client is created and closed when the client is closed.
    """

    def __init__(
        self,
        config: LLMConfig,
        *,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        _validate_base_url(config.base_url)
        self._config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(config.timeout_seconds),
                write=float(config.timeout_seconds),
                pool=10.0,
            )
        )

    @property
    def config(self) -> LLMConfig:
        return self._config

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "FactCheckerClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def stream(self, user_text: str) -> Iterator[str]:
        """Yield token deltas from a chat-completions streaming request.

        The iterator stops when the server sends ``data: [DONE]``, when
        the connection closes, or when a fatal HTTP / network error
        occurs (in which case :class:`LLMError` is raised).

        The caller may stop iterating early (e.g. on user cancel); the
        underlying connection is closed when the response context
        manager exits.
        """

        api_key = self._config.api_key()
        if not api_key:
            raise LLMError(
                "auth",
                body=(
                    f"Environment variable {self._config.api_key_env!r} is "
                    "not set. Set it before running the fact-checker."
                ),
            )

        payload = {
            "model": self._config.model,
            "stream": True,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": self._config.resolved_system_prompt(),
                },
                {"role": "user", "content": user_text},
            ],
        }
        url = self._chat_completions_url()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            request = self._client.build_request(
                "POST", url, json=payload, headers=headers
            )
            response = self._client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise LLMError("network", body=str(exc)) from exc

        try:
            if response.status_code >= 400:
                # Drain (and discard) the body so the connection can be
                # returned to the pool, but keep a short preview for logs.
                try:
                    raw = response.read()
                except Exception:  # pragma: no cover - defensive
                    raw = b""
                body = _redact_body(raw.decode("utf-8", errors="replace"))
                response.close()
                raise LLMError(
                    f"http {response.status_code}",
                    status=response.status_code,
                    body=body,
                )

            for line in response.iter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    # SSE comment (heartbeat). Ignore.
                    continue
                if not line.startswith("data:"):
                    # Some providers send non-SSE trailers; log + skip.
                    _LOGGER.debug("LLM stream: non-SSE line: %r", line[:120])
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "LLM stream: skipping malformed JSON line (%d chars)",
                        len(data),
                    )
                    continue
                token = _extract_delta(chunk)
                if token:
                    yield token
        finally:
            response.close()

    def _chat_completions_url(self) -> str:
        """Return the full chat-completions URL.

        If ``base_url`` already ends in ``/chat/completions`` (some
        providers hard-code the full path in their docs), use it as
        is. Otherwise append ``/chat/completions`` to the base.
        """
        base = self._config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"


def _extract_delta(chunk: dict) -> str:
    """Pull ``choices[0].delta.content`` from a streaming chunk.

    Tolerates the common alternative shapes (string ``delta``,
    ``message`` key, missing ``choices``). Returns "" when no
    content is present in this chunk.
    """

    try:
        choices = chunk.get("choices") or []
    except AttributeError:
        return ""
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        if isinstance(content, str):
            return content
        # Empty delta (e.g. a role-only chunk or an Ollama-style
        # chunk that carries the content in ``message``) — fall through
        # to the message-key check below.
    elif isinstance(delta, str):
        return delta
    # Some non-OpenAI providers (e.g. older Ollama) return a top-level
    # ``message.content`` even when streaming.
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""
