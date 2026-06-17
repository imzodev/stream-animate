"""Built-in persona presets for the LLM fact-checker / concept explainer.

Each persona maps to a system prompt that is sent with every user message.
The ``"custom"`` persona is a sentinel: when selected, the user-provided
``LLMConfig.system_prompt`` is used instead.
"""

from __future__ import annotations

from typing import Dict

PERSONA_PRESETS: Dict[str, str] = {
    "fact_checker": (
        "You are a rigorous fact-checker. Verify the claim in the user's "
        "spoken question. Reply with: VERDICT (true / false / mixed / "
        "unverified), one-sentence reasoning, and one source to consult. "
        "No hedging."
    ),
    "eli5": (
        "Explain the concept in the user's question like they are five. "
        "Use one analogy. Three sentences max."
    ),
    "socratic": (
        "Answer the user's question with a single probing question that "
        "leads them to the answer themselves. Never give the answer."
    ),
    "devils_advocate": (
        "Steel-man the strongest possible counter-argument to the user's "
        "claim. Be persuasive but fair. Three paragraphs max."
    ),
    "custom": "",
}


def resolve_system_prompt(persona: str, custom: str | None) -> str:
    """Return the active system prompt.

    Order of precedence:
    1. ``custom`` if non-empty.
    2. ``PERSONA_PRESETS[persona]`` if the persona is known.
    3. ``PERSONA_PRESETS["fact_checker"]`` as a safe fallback.
    """

    if custom and custom.strip():
        return custom
    if persona in PERSONA_PRESETS and persona != "custom":
        return PERSONA_PRESETS[persona]
    return PERSONA_PRESETS["fact_checker"]


__all__ = ["PERSONA_PRESETS", "resolve_system_prompt"]
