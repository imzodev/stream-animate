"""Configuration for the LLM fact-checker / concept explainer.

The :class:`LLMConfig` dataclass is kept in its own module so the model
layer (``stream_companion.models``) does not need to know about the
LLM client, and the LLM client does not need to know about the model
layer — both depend on this module.

Importing this module pulls in :mod:`.personas` but no I/O or network
libraries, so it is safe to import at any time.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from .personas import resolve_system_prompt

# Environment-variable name pattern (uppercase + underscores, must start
# with a letter, no leading digits).
_ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for the LLM fact-checker / concept explainer.

    The engine talks to any OpenAI-compatible ``/v1/chat/completions``
    endpoint (OpenAI, DeepSeek, MiniMax, Together, Groq, Ollama,
    LM Studio, vLLM, etc.). API keys are read from an environment
    variable named by ``api_key_env`` — they are never stored in the
    config file.
    """

    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "LLM_API_KEY"
    persona: str = "fact_checker"
    system_prompt: Optional[str] = None
    temperature: float = 0.3
    max_tokens: int = 512
    # Global toggle hotkey (press once to start listening, again to stop).
    # When None, the engine cannot be toggled via keyboard; the user can
    # still trigger it programmatically (e.g. from a tray menu).
    toggle_hotkey: Optional[str] = None
    timeout_seconds: int = 30
    # Seconds of silence (no new STT phrase) after which the
    # fact-checker finalises the buffered question and sends it to
    # the LLM. The silence window is measured from the LAST phrase,
    # not from the toggle press — the engine waits indefinitely for
    # the first phrase (up to ``timeout_seconds``) so a slow STT
    # engine does not cause premature "empty question" timeouts.
    #
    # The default (5.0s) is tuned for the bundled Whisper ``turbo``
    # model which can take 2-4 seconds between transcriptions of
    # consecutive audio chunks. If the user prefers explicit control
    # (must press toggle twice), set this >= ``timeout_seconds``.
    silence_timeout: float = 5.0
    # Hotkey that aborts an in-flight LLM stream. Bound to the
    # dedicated cancel path (``FactCheckerEngine.cancel``) so the
    # toggle hotkey never accidentally kills the answer the user
    # asked for. Set to ``None`` to disable.
    esc_hotkey: Optional[str] = "<esc>"

    def resolved_system_prompt(self) -> str:
        """Return the active system prompt (custom → preset → default)."""
        return resolve_system_prompt(self.persona, self.system_prompt)

    def api_key(self) -> Optional[str]:
        """Read the API key from the environment, or return None if unset."""
        return os.environ.get(self.api_key_env)

    def is_valid_api_key_env(self) -> bool:
        """Return True when ``api_key_env`` is a syntactically valid env name."""
        return bool(self.api_key_env and _ENV_VAR_NAME.match(self.api_key_env))


__all__ = ["LLMConfig"]
