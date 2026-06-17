"""LLM client and configuration for the fact-checker / concept explainer.

The package exposes:

* :data:`PERSONA_PRESETS` — built-in persona system prompts
* :func:`resolve_system_prompt` — persona resolution helper

:class:`LLMConfig` itself lives in :mod:`stream_companion.models` and is
imported from there (``from stream_companion.models import LLMConfig``)
to keep the import graph acyclic: ``models.py`` imports from
:mod:`.personas`, and importing :mod:`.client` would pull in ``httpx``
which is not a model-layer concern.

The streaming client (:class:`FactCheckerClient`) and error type
(:class:`LLMError`) live in :mod:`.client`.
"""

from .personas import PERSONA_PRESETS, resolve_system_prompt

__all__ = [
    "PERSONA_PRESETS",
    "resolve_system_prompt",
]
