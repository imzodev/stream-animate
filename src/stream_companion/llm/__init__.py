"""LLM client and configuration for the fact-checker / concept explainer.

Public surface:

* :class:`LLMConfig` — frozen dataclass for the LLM block
* :data:`PERSONA_PRESETS` — built-in persona system prompts
* :func:`resolve_system_prompt` — persona resolution helper
* :class:`FactCheckerClient` — OpenAI-compatible streaming client
* :class:`LLMError` — client error type
"""

from .client import FactCheckerClient, LLMError
from .config import LLMConfig
from .personas import PERSONA_PRESETS, resolve_system_prompt

__all__ = [
    "FactCheckerClient",
    "LLMConfig",
    "LLMError",
    "PERSONA_PRESETS",
    "resolve_system_prompt",
]
