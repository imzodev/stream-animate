"""Per-persona visual identity for the streaming answer panel.

Each persona gets a distinct color palette (gradient stops, accent
color, glow color) and a glyph + display label. The widgets in
``answer_panel`` and its sub-modules look up the current persona
via :func:`accent_for` and paint the border / state pill / glow
accordingly.

This module is pure data — no Qt imports — so it can be tested
without a QApplication and reused in non-Qt contexts (e.g. an
OBS browser-source bridge that consumes the same accent
information).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PersonaAccent:
    """Visual identity for a single persona.

    Attributes:
        persona: The persona name (matches ``LLMConfig.persona``).
        display_name: Human-readable label shown in the status
            bar (e.g. ``"FACT-CHECKER"``).
        glyph: Single character / emoji shown next to the name.
        gradient_top: Top stop of the border gradient (hex).
        gradient_bottom: Bottom stop of the border gradient
            (hex). The two stops animate via a slow rotation
            driven by the border painter.
        accent: Primary accent color (hex) used for the glow,
            the question card's left stripe, and the state
            pill.
        glow: Slightly desaturated accent for the drop shadow
            (hex, with alpha applied at paint time).
    """

    persona: str
    display_name: str
    glyph: str
    gradient_top: str
    gradient_bottom: str
    accent: str
    glow: str


# A custom persona uses a neutral mint→blue gradient so streamers
# can configure their own system_prompt without inheriting one of
# the built-in palettes.
CUSTOM = PersonaAccent(
    persona="custom",
    display_name="CUSTOM",
    glyph="✨",
    gradient_top="#06FFA5",
    gradient_bottom="#3A86FF",
    accent="#06FFA5",
    glow="#3A86FF",
)


# Ordered by the LLMConfig.persona enum. Keep the keys in sync with
# ``stream_companion/llm/personas.py``.
ACCENTS: dict[str, PersonaAccent] = {
    "fact_checker": PersonaAccent(
        persona="fact_checker",
        display_name="FACT-CHECKER",
        glyph="🔍",
        gradient_top="#FF6B35",
        gradient_bottom="#F7931E",
        accent="#FF8A3D",
        glow="#FF6B35",
    ),
    "eli5": PersonaAccent(
        persona="eli5",
        display_name="ELI5",
        glyph="💡",
        gradient_top="#00D4FF",
        gradient_bottom="#7B61FF",
        accent="#22D3EE",
        glow="#7B61FF",
    ),
    "socratic": PersonaAccent(
        persona="socratic",
        display_name="SOCRATIC",
        glyph="🏛️",
        gradient_top="#9D4EDD",
        gradient_bottom="#5A189A",
        accent="#A855F7",
        glow="#7C3AED",
    ),
    "devils_advocate": PersonaAccent(
        persona="devils_advocate",
        display_name="DEVIL'S ADVOCATE",
        glyph="⚔️",
        gradient_top="#FF006E",
        gradient_bottom="#8338EC",
        accent="#EC4899",
        glow="#FF006E",
    ),
    "custom": CUSTOM,
}


def accent_for(persona: str) -> PersonaAccent:
    """Return the :class:`PersonaAccent` for ``persona``.

    Falls back to the ``custom`` accent (mint→blue) for any
    unknown persona name. This keeps the panel rendering
    gracefully if a user types a custom persona string in
    their config.
    """

    return ACCENTS.get(persona, CUSTOM)


__all__ = ["ACCENTS", "CUSTOM", "PersonaAccent", "accent_for"]
